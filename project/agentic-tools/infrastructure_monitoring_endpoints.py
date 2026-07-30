#!/usr/bin/env python3
"""
Infrastructure Monitoring API — Postgres CIS hardening + Railway platform/
deployment drift + Intelligenza's own connector-credential rotation hygiene.

Router prefix: /infra-monitoring

    GET  /infra-monitoring/connectors              List registered postgres_cis/railway_iaas connectors
    POST /infra-monitoring/connectors/{id}/run      Run an audit now, synchronously
    GET  /infra-monitoring/results                  Latest audit per (connector, resource) — status matrix feed
    GET  /infra-monitoring/results/history          Audit history for one resource
    GET  /infra-monitoring/connector-hygiene        Live stale-credential check (no history needed — see below)

Postgres/Railway connectors are registered as observability.poll_connectors rows
(connector_type 'postgres_cis'/'railway_iaas') exactly like the SCM connectors
scm_audit_endpoints.py manages — registration happens on the Dendrai UBO
Configuration screen, this router only surfaces what's unique to this category.

Unlike scm_audit_endpoints.py, postgres_cis_tool.py/railway_iaas_tool.py were
built as plain observability.poll_connectors adapters (pull_events/test_connection),
not UBO-adjudicated — findings land in observability.system_telemetry via
mcp_governance._detect_system_flags/_ingest_system_event, the same path
connector_poller.py's scheduled ticks use. "Run now" here calls the identical
adapter + ingestion path on demand rather than re-implementing a parallel one.

Connector Hygiene (connector_hygiene.py) has no external system to poll — it's
a query against Intelligenza's own poll_connectors table — so its endpoint
just computes and returns the current answer live rather than reading history;
connector_hygiene_sweep.py separately writes a system_telemetry row once a day
only when something is actually stale, for the audit trail / drift alerting
(see model_health_drift_watch-style background loop in api_server.py).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

import aws_iaas_tool
import connector_hygiene
import db
import mcp_governance
import ot_heartbeat_tool
import postgres_cis_tool
import railway_iaas_tool
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.infrastructure_monitoring")
# Router-level: backs the "Infrastructure Monitoring" screen (nav id
# "infrastructuremonitoring") — see require_screen_permission's docstring.
router = APIRouter(prefix="/infra-monitoring", tags=["Infrastructure Monitoring"],
                    dependencies=[Depends(require_screen_permission("infrastructuremonitoring"))])

_CONNECTOR_TYPES = ("postgres_cis", "railway_iaas", "aws_iaas", "ot_heartbeat")
_ADAPTERS = {
    "postgres_cis": postgres_cis_tool, "railway_iaas": railway_iaas_tool,
    "aws_iaas": aws_iaas_tool, "ot_heartbeat": ot_heartbeat_tool,
}


@router.get("/connectors")
async def list_connectors():
    if not db.is_available():
        return {"connectors": []}
    rows = [c for c in db.list_poll_connectors() if c["connector_type"] in _CONNECTOR_TYPES]
    out = []
    for c in rows:
        out.append({
            "id":               c["id"],
            "connector_type":   c["connector_type"],
            "display_name":     c["display_name"],
            "active":           c["active"],
            "last_poll_at":     c["last_poll_at"],
            "last_poll_status": c["last_poll_status"],
            "last_poll_error":  c["last_poll_error"],
            "risk_tier":        c.get("risk_tier"),
            "data_sensitivity": c.get("data_sensitivity"),
            "system_owner":     c.get("system_owner"),
        })
    return {"connectors": out}


@router.post("/connectors/{connector_id}/run")
async def run_connector_audit(connector_id: int):
    """Run postgres_cis_tool/railway_iaas_tool's adapter against this
    connector's live credentials right now, ingest through the same
    system_telemetry path the scheduled poller uses, and return the
    freshly-computed result directly (not dependent on whether the insert
    landed — pull_events' event_id is day-scoped for dedup against repeated
    poll ticks, so a second manual run the same day is a no-op for
    persistence but the live compliance data returned here is always current)."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    connector = db.get_poll_connector(connector_id, include_credentials=True)
    if not connector or connector["connector_type"] not in _CONNECTOR_TYPES:
        raise HTTPException(status_code=404, detail="Connector not found")

    adapter = _ADAPTERS[connector["connector_type"]]
    try:
        events = await asyncio.to_thread(
            adapter.pull_events, connector["base_url"], connector["credentials"],
            connector.get("extra_config") or {}, None)
    except Exception as exc:
        await asyncio.to_thread(db.record_poll_result, connector_id, "error", f"{type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}")

    server_name = f"{connector['connector_type']}:{connector['display_name']}"[:128]
    results = []
    for event in events:
        flags = await asyncio.to_thread(mcp_governance._detect_system_flags, {
            "action": event.get("action") or "",
            "resource": event.get("resource") or "",
            "severity": event.get("severity") or "INFO",
            "event_type": event.get("event_type") or "",
            "payload": event.get("raw_payload") or {},
        })
        await asyncio.to_thread(
            mcp_governance._ingest_system_event,
            server_name, connector["connector_type"], event.get("event_type") or "infrastructure_finding",
            event.get("event_id"), event.get("actor"), event.get("action"), event.get("resource"),
            event.get("severity") or "INFO", flags, event.get("raw_payload"), None,
        )
        results.append({
            "resource": event.get("resource"),
            "severity": event.get("severity"),
            "raw_payload": event.get("raw_payload"),
        })
    await asyncio.to_thread(db.record_poll_result, connector_id, "ok")
    return {"results": results}


@router.get("/results")
async def list_results(limit: int = 50):
    """Latest audit per (connector, resource) — the Infrastructure Posture
    matrix feed. Includes both passing and failing checks (unlike
    secret-scan/pipeline-security, event_type='infrastructure_finding' is
    written on every poll tick regardless of outcome)."""
    if not db.is_available():
        return {"results": []}
    return {"results": db.fetch_infra_monitoring_results(limit=limit)}


@router.get("/results/history")
async def result_history(resource: str, limit: int = 50):
    if not db.is_available():
        return {"history": []}
    return {"history": db.fetch_infra_monitoring_results(resource=resource, limit=limit)}


@router.get("/connector-hygiene")
async def connector_hygiene_status(stale_days: int = 90):
    """Live stale-credential check against Intelligenza's own poll_connectors
    table — always current, no history to page through (see module docstring)."""
    result = await asyncio.to_thread(connector_hygiene.check_connector_credential_rotation, stale_days)
    return result
