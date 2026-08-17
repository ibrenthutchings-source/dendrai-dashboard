#!/usr/bin/env python3
"""
Generic poll-connector dispatch loop.

Replaces the idea of one hardcoded poller per external system with a single
loop that reads observability.poll_connectors (configured entirely from the
app UI — Dendrai UBO Configuration screen, not env vars) and dispatches each
due connector to its adapter module. Adding a 6th connector type later means
adding one adapter module and one _ADAPTERS entry — no new scheduler code.

Each adapter (oracle_fusion_tool, sap_hana_tool, sailpoint_tool,
dynamics365_tool, netsuite_tool) exposes:
    pull_events(base_url, credentials, extra_config, since) -> list[dict]
    test_connection(base_url, credentials, extra_config) -> tuple[bool, str]

pull_events() must return the uniform connector event shape:
    {event_id, event_type, actor, action, resource, severity, raw_payload}

This loop does NOT touch the adjudication pipeline — it only produces
system_telemetry rows via mcp_governance._detect_system_flags() +
_ingest_system_event(), the same insert path GitHub-sourced and internal
telemetry-sourced events already go through. mcp_governance's own
start_polling() loop (already running) picks up any row with a non-empty
risk_flags array and adjudicates it — see mcp_governance.py's
_fetch_unprocessed_system() for that filter.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import db
import denied_party_screening_tool
import deploy_env
import dynamics365_tool
import aws_iaas_tool
import exception_tool
import mcp_governance
import netsuite_tool
import oracle_fusion_tool
import oracle_hcm_tool
import ot_heartbeat_tool
import postgres_cis_tool
import railway_iaas_tool
import sailpoint_tool
import sap_hana_tool
import synthetic_transaction_tool

logger = logging.getLogger(__name__)

# Short tick — each connector's OWN poll_interval_s (configured per-connector
# in the UI, default 1800s/30min) governs how often it actually fires; this
# is just how often we check "is anything due yet".
_TICK_S = float(os.environ.get("CONNECTOR_POLLER_TICK_S", "60"))

_ADAPTERS = {
    "oracle_fusion": oracle_fusion_tool,
    "oracle_hcm":    oracle_hcm_tool,
    "denied_party_screening": denied_party_screening_tool,
    "sap_hana":      sap_hana_tool,
    "sailpoint":      sailpoint_tool,
    "dynamics365":    dynamics365_tool,
    "netsuite":       netsuite_tool,
    "postgres_cis":    postgres_cis_tool,
    "railway_iaas":    railway_iaas_tool,
    "aws_iaas":        aws_iaas_tool,
    "ot_heartbeat":    ot_heartbeat_tool,
    "synthetic_transaction": synthetic_transaction_tool,
}


def _score_exception_event(connector: dict, event: dict, system_telemetry_id: "int | None") -> None:
    """Best-effort: scores one connector event for Exception Management
    (exception_tool.score_event) and persists it (db.insert_exception_event).
    Development environment only — see deploy_env.py; every other
    environment's ingestion path never calls this. Runs inside the same
    per-event try/except as the primary mcp_governance ingestion, so a
    failure here can't affect that ingestion or take down the poll loop.

    system_telemetry_id is the row _ingest_system_event just wrote (or None
    if that insert was skipped as a duplicate) — captured here, not looked
    up later, so a reviewer triaging this exception can jump straight to the
    exact source telemetry row instead of just knowing which system it came
    from. actor/action/event_type/raw_payload are carried the same way, for
    "what actually happened" in the triage card itself."""
    extra_config = connector.get("extra_config") or {}
    system_source = extra_config.get("system_label") or connector["connector_type"]
    process = extra_config.get("process")
    control_id = event.get("resource") or event.get("event_id") or "unknown"
    scored = exception_tool.score_event(
        event.get("event_type") or "", event.get("severity") or "INFO", event.get("raw_payload") or {},
    )
    event_ts = event.get("created_at") or datetime.now(timezone.utc)
    db.insert_exception_event(
        control_id, system_source, process, event_ts, scored["features"], scored["model_version"],
        scored["anomaly_score"], scored["uncertainty_score"], scored["requires_human_review"],
        actor=event.get("actor"), action=event.get("action"), event_type=event.get("event_type"),
        raw_payload=event.get("raw_payload"), system_telemetry_id=system_telemetry_id,
    )


def _is_due(connector: dict) -> bool:
    if not connector.get("active"):
        return False
    last = connector.get("last_poll_at")
    if not last:
        return True
    last_dt = datetime.fromisoformat(last)
    interval = timedelta(seconds=int(connector.get("poll_interval_s") or 1800))
    return datetime.now(timezone.utc) - last_dt >= interval


async def _poll_one(connector_id: int) -> None:
    """Poll a single connector. Never raises — errors are recorded via
    db.record_poll_result so one failing connector can't take down the loop
    or block the others in the same tick."""
    try:
        full = await asyncio.to_thread(db.get_poll_connector, connector_id, True)
    except db.EncryptionKeyMissing as exc:
        logger.warning("Connector %s skipped — %s", connector_id, exc)
        return
    if not full:
        return

    adapter = _ADAPTERS.get(full["connector_type"])
    if adapter is None:
        await asyncio.to_thread(db.record_poll_result, connector_id, "error",
                                 f"Unknown connector_type '{full['connector_type']}'")
        return

    since = None
    if full.get("last_poll_at"):
        since = datetime.fromisoformat(full["last_poll_at"])

    try:
        events = await asyncio.to_thread(
            adapter.pull_events, full["base_url"], full["credentials"], full.get("extra_config") or {}, since
        )
    except Exception as exc:
        logger.warning("Connector %s (%s) pull_events failed: %s", connector_id, full["connector_type"], exc)
        await asyncio.to_thread(db.record_poll_result, connector_id, "error", f"{type(exc).__name__}: {exc}")
        return

    server_name = f"{full['connector_type']}:{full['display_name']}"[:128]
    # Same system_label-over-connector_type preference _score_exception_event
    # already applies below — without it, system_telemetry.system_type (the
    # Source System dimension's only signal) collapses every synthetic
    # connector into one literal "synthetic_transaction" bucket instead of
    # SAP HANA/SailPoint/Oracle Fusion/Dynamics 365, regardless of which of
    # the 11 (system, process) pairs actually produced the event.
    extra_config = full.get("extra_config") or {}
    system_type = extra_config.get("system_label") or full["connector_type"]
    ingested = 0
    for event in events:
        try:
            flags = await asyncio.to_thread(mcp_governance._detect_system_flags, {
                "action": event.get("action") or "",
                "resource": event.get("resource") or "",
                "severity": event.get("severity") or "INFO",
                "event_type": event.get("event_type") or "",
                "payload": event.get("raw_payload") or {},
            })
            row_id = await asyncio.to_thread(
                mcp_governance._ingest_system_event,
                server_name, system_type, event.get("event_type") or "poll_event",
                event.get("event_id"), event.get("actor"), event.get("action"), event.get("resource"),
                event.get("severity") or "INFO", flags, event.get("raw_payload"), None,
                # Optional: lets an adapter preserve the event's real occurred-at
                # time (e.g. a backdated synthetic case step, or a real system's
                # own event timestamp from `since`-windowed history) instead of
                # every polled event landing at ingestion time regardless of
                # when it actually happened. None (the default) is unchanged
                # behavior for every adapter that doesn't set this key.
                created_at=event.get("created_at"),
            )
            if row_id is not None:
                ingested += 1
            if deploy_env.IS_DEVELOPMENT:
                await asyncio.to_thread(_score_exception_event, full, event, row_id)
        except Exception as exc:
            logger.warning("Connector %s: failed to ingest one event: %s", connector_id, exc)

    await asyncio.to_thread(db.record_poll_result, connector_id, "ok", None)
    if ingested:
        logger.info("Connector %s (%s): ingested %d/%d event(s)",
                     connector_id, full["connector_type"], ingested, len(events))


async def _poll_due_connectors() -> int:
    connectors = await asyncio.to_thread(db.list_poll_connectors)
    due = [c for c in connectors if _is_due(c)]
    for c in due:
        await _poll_one(c["id"])
    return len(due)


async def start_polling() -> None:
    """Infinite dispatch loop. Started as an asyncio task in api_server.py's
    lifespan; cancelled gracefully on shutdown. Mirrors
    mcp_governance.start_polling()'s shape — errors are caught and logged,
    the loop never exits on its own unless cancelled."""
    logger.info("Connector poller started (tick=%.0fs, types=%s)", _TICK_S, sorted(_ADAPTERS))
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            n = await _poll_due_connectors()
            if n:
                logger.info("Connector poller: polled %d due connector(s)", n)
        except asyncio.CancelledError:
            logger.info("Connector poller stopped")
            break
        except Exception as exc:
            logger.warning("Connector poller tick error: %s", exc)
