#!/usr/bin/env python3
"""
ITSM/Jira-ServiceNow SLA Bridge — automated breach-detection sweep.

Periodically flags observability.itsm_tickets rows past their sla_due_at
with sla_breached_at, then re-ingests the underlying finding as a fresh
system_telemetry event tagged sla_breach: a ticket sitting untouched past
its remediation SLA means the finding itself is failing again, not a
detail that only matters to whoever is watching Jira/ServiceNow.

Mirrors risk_waiver_sweep.py's shape exactly (itself mirroring
mcp_governance.start_polling()) — infinite loop, errors caught and logged,
never exits on its own except cancellation. Started as an asyncio task in
api_server.py's lifespan alongside the other background loops. Deliberately
independent of itsm_jira_tool.py/itsm_servicenow_tool.py's status-reconciliation
poll: a ticket that's never synced still gets its SLA breach detected and
escalated on schedule.
"""

from __future__ import annotations

import asyncio
import logging

import db
import mcp_governance

logger = logging.getLogger(__name__)

# Hourly matches risk_waiver_sweep.py's cadence — day-granularity SLA windows
# (48h/168h/240h/720h) don't need finer resolution than that.
_TICK_S = 3600


async def _reescalate(ticket: dict) -> None:
    """Re-ingest the finding tagged sla_breach so it flows through the normal
    adjudication pipeline again — same reasoning as risk_waiver_sweep._reescalate."""
    flags = await asyncio.to_thread(mcp_governance._detect_system_flags, {
        "action": "sla_breach", "resource": ticket["finding_hash"],
        "severity": ticket.get("severity") or "HIGH", "event_type": "sla_breach",
        "payload": {"sla_breach": True},
    })
    await asyncio.to_thread(
        mcp_governance._ingest_system_event,
        "itsm-sla-sweep", "itsm", "sla_breach",
        f"sla-breach:{ticket['id']}:{ticket['external_ticket_key']}",
        None, "sla_breach", ticket["finding_hash"],
        ticket.get("severity") or "HIGH", flags,
        {
            "ticket_id": ticket["id"], "external_system": ticket["external_system"],
            "external_ticket_key": ticket["external_ticket_key"],
            "finding_hash": ticket["finding_hash"], "sla_due_at": ticket["sla_due_at"],
            "note": "ITSM ticket breached its remediation SLA — finding re-opened as failing.",
        },
        None,
    )


async def sweep_once() -> int:
    """Run one breach-detection pass. Returns the number of tickets flagged —
    exposed for tests and for an on-demand admin/MCP trigger."""
    breached = await asyncio.to_thread(db.expire_overdue_sla)
    for ticket in breached:
        try:
            await _reescalate(ticket)
        except Exception as exc:
            logger.warning("itsm_sla_sweep: failed to re-escalate ticket %s: %s", ticket.get("id"), exc)
    if breached:
        logger.info("itsm_sla_sweep: flagged %d ticket(s) as SLA-breached, re-escalated", len(breached))
    return len(breached)


async def start_sweep() -> None:
    logger.info("ITSM SLA breach sweep started (tick=%.0fs)", _TICK_S)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("ITSM SLA breach sweep stopped")
            break
        except Exception as exc:
            logger.warning("itsm_sla_sweep tick error: %s", exc)
