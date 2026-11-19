#!/usr/bin/env python3
"""
Journal Entry Testing sweep — periodic pull of real GL journal entries from
every active financial connector, scored via je_testing_tool.run_je_tests.

Mirrors identity_graph_sync.py's shape: infinite loop, per-connector
isolation (one connector's failure can't block the others or crash the
loop), errors caught and logged, never exits on its own except cancellation.
Started as an asyncio task in api_server.py's lifespan alongside the other
background loops.

Findings are persisted via db.insert_exception_event, reusing Exception
Management's exception_control_events/exception_model_inferences schema
(already generic enough: control_id, system_source, process, raw_payload) —
but deliberately NOT gated by deploy_env.IS_DEVELOPMENT the way
connector_poller.py's own exception-scoring hook is. JE Testing is a real,
always-on control, not the dev-only ML-uncertainty demo exceptions.jsx is;
je_testing_endpoints.py reads the same tables filtered to JE-* control ids,
in every environment. Rules are deterministic (je_testing_tool.py), not a
trained model, so there's no uncertainty_score ambiguity band to model —
severity maps directly to anomaly_score/requires_human_review below.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import db
import dynamics365_tool
import je_testing_tool
import netsuite_tool
import oracle_fusion_tool
import sap_hana_tool
import synthetic_transaction_tool

logger = logging.getLogger(__name__)

_TICK_S = float(os.environ.get("JE_TESTING_SWEEP_TICK_S", "1800"))
_LOOKBACK_DAYS = int(os.environ.get("JE_TESTING_LOOKBACK_DAYS", "7"))

_CONNECTOR_TYPES = ("oracle_fusion", "netsuite", "sap_hana", "dynamics365", "synthetic_transaction")

# A deterministic rule engine, not a trained model — the exception_model_inferences
# schema's model_version column is still the natural place to record "which
# rule set produced this finding" for traceability.
_MODEL_VERSION = "je-rules-v1"

# CRITICAL/HIGH findings queue for human triage (requires_human_review=True);
# MEDIUM/LOW are recorded (visible in GET /je-testing/findings) but don't.
_SEVERITY_REQUIRES_REVIEW = {"CRITICAL": True, "HIGH": True, "MEDIUM": False, "LOW": False}
_SEVERITY_ANOMALY_SCORE = {"CRITICAL": 0.95, "HIGH": 0.75, "MEDIUM": 0.5, "LOW": 0.25}


def _pull_journal_entries(connector: dict) -> list[dict]:
    """Pull journal entries for one active connector. Never raises — a
    connector-specific failure means "nothing to test this tick" for that
    connector, same per-connector isolation identity_graph_sync._sync_one
    gives Oracle Fusion connectors."""
    since = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
    ctype = connector["connector_type"]
    creds = connector.get("credentials") or {}
    base_url = connector.get("base_url")
    extra_config = connector.get("extra_config") or {}

    try:
        if ctype == "oracle_fusion":
            client = oracle_fusion_tool.OracleFusionClient(
                host=base_url, username=creds.get("username"), password=creds.get("password"),
                client_id=creds.get("client_id"), client_secret=creds.get("client_secret"),
            )
            result = oracle_fusion_tool.get_journal_entries(date_from=since.date().isoformat(), client=client)
        elif ctype == "netsuite":
            result = netsuite_tool.get_journal_entries(base_url, creds, extra_config, since=since)
        elif ctype == "sap_hana":
            result = sap_hana_tool.get_journal_entries(base_url, creds, extra_config, since=since)
        elif ctype == "dynamics365":
            result = dynamics365_tool.get_journal_entries(base_url, creds, extra_config, since=since)
        elif ctype == "synthetic_transaction":
            result = synthetic_transaction_tool.get_journal_entries(base_url, creds, extra_config, since=since)
        else:
            return []
    except Exception as exc:
        logger.warning("je_testing_sweep: connector %s (%s) raised: %s", connector["id"], ctype, exc)
        return []

    if result.get("error"):
        logger.warning("je_testing_sweep: connector %s (%s) get_journal_entries failed: %s",
                        connector["id"], ctype, result["error"])
        return []
    return result.get("journal_entries") or []


def _persist_finding(finding: dict, connector: dict) -> None:
    severity = finding["severity"]
    db.insert_exception_event(
        control_id=finding["rule_id"],
        system_source=finding.get("source_system") or connector["connector_type"],
        process="record_to_report",
        event_timestamp=finding.get("posted_at") or datetime.now(timezone.utc),
        features={"amount": finding.get("amount"), "account": finding.get("account"),
                  "severity": severity, "rule_label": finding.get("rule_label")},
        model_version=_MODEL_VERSION,
        anomaly_score=_SEVERITY_ANOMALY_SCORE.get(severity, 0.5),
        uncertainty_score=0.0,  # deterministic rule — no model ambiguity to express
        requires_human_review=_SEVERITY_REQUIRES_REVIEW.get(severity, False),
        actor=finding.get("preparer"),
        action="Journal Entry Posted",
        event_type="JOURNAL_ENTRY",
        raw_payload=finding,
    )


async def _sweep_one(connector: dict) -> dict:
    """Pull + test one connector's journal entries. Never raises."""
    connector_id = connector["id"]
    try:
        full = await asyncio.to_thread(db.get_poll_connector, connector_id, True)
    except db.EncryptionKeyMissing as exc:
        logger.warning("je_testing_sweep: connector %s skipped — %s", connector_id, exc)
        return {"connector_id": connector_id, "error": str(exc)}
    if not full:
        return {"connector_id": connector_id, "error": "connector not found"}

    jes = await asyncio.to_thread(_pull_journal_entries, full)
    if not jes:
        return {"connector_id": connector_id, "journal_entries": 0, "findings": 0}

    findings = await asyncio.to_thread(je_testing_tool.run_je_tests, jes)
    for finding in findings:
        await asyncio.to_thread(_persist_finding, finding, full)

    logger.info("je_testing_sweep: connector %s (%s) — %d journal entr(y/ies), %d finding(s)",
                connector_id, full["display_name"], len(jes), len(findings))
    return {"connector_id": connector_id, "journal_entries": len(jes), "findings": len(findings)}


async def sweep_once() -> dict:
    """Run one sweep pass across every active financial connector. Returns
    {connector_id: result} — exposed for tests and an on-demand admin
    trigger, not just the periodic loop."""
    connectors = await asyncio.to_thread(db.list_poll_connectors)
    targets = [c for c in connectors if c["connector_type"] in _CONNECTOR_TYPES and c["active"]]
    results: dict = {}
    for c in targets:
        results[c["id"]] = await _sweep_one(c)
    return results


async def start_sweep() -> None:
    logger.info("JE Testing sweep started (tick=%.0fs, lookback=%dd)", _TICK_S, _LOOKBACK_DAYS)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("JE Testing sweep stopped")
            break
        except Exception as exc:
            logger.warning("je_testing_sweep tick error: %s", exc)
