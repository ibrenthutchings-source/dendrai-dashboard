#!/usr/bin/env python3
"""
DevOps Monitoring: ITSM/Jira-ServiceNow SLA breach sweep.

observability.itsm_tickets' SLA-breach half (db.expire_overdue_sla()) already
existed — db.update_itsm_ticket_status's own docstring says sla_breached_at
is "itsm_sla_sweep.py's job, run independently of ticket status," and
several other sweeps' docstrings cite this file's cadence as their
template — but the file itself was never built. This is that file, the
periodic half of itsm_endpoints.py's real-time webhook half.

Flags every open observability.itsm_tickets row past its sla_due_at with
sla_breached_at (once — expire_overdue_sla() only touches rows where
sla_breached_at IS NULL, so a re-tick never re-flags the same breach), and
re-raises a fresh finding for each so a ticket that's open but nobody's
touched reappears in Continuous Monitoring instead of silently sitting
there overdue with no one told.

Mirrors vendor_risk_sweep.py's shape exactly: infinite loop, errors caught
and logged, never exits on its own except cancellation. Started as an
asyncio task in api_server.py's lifespan alongside the other background loops.
"""

from __future__ import annotations

import asyncio
import logging

import db
import mcp_governance

logger = logging.getLogger(__name__)

_TICK_S = 3600  # hourly


async def _raise_breach(ticket: dict) -> None:
    """Re-ingest the SLA-breached ticket's underlying finding as a fresh
    system_telemetry event — same "goes back to failing" semantics as
    risk_waiver_sweep._raise_expired, just triggered by a missed SLA instead
    of an expired compensating control."""
    flags = await asyncio.to_thread(mcp_governance._detect_system_flags, {
        "action": "itsm_sla_breached", "resource": ticket["external_ticket_key"],
        "severity": ticket.get("severity") or "MEDIUM", "event_type": "itsm_sla_breached",
        "payload": {"itsm_sla_breached": True},
    })
    await asyncio.to_thread(
        mcp_governance._ingest_system_event,
        "itsm-sla-sweep", ticket["external_system"], "itsm_sla_breached",
        f"sla-breach:{ticket['id']}:{ticket['sla_due_at']}",
        None, "sla_breach_check", ticket["external_ticket_key"],
        ticket.get("severity") or "MEDIUM", flags,
        {
            "itsm_sla_breached": True,
            "itsm_ticket_detail": {
                "ticket_id": ticket["id"], "finding_hash": ticket.get("finding_hash"),
                "external_system": ticket["external_system"], "external_ticket_key": ticket["external_ticket_key"],
                "sla_due_at": ticket["sla_due_at"],
            },
        },
        None,
    )


async def sweep_once() -> int:
    """Run one breach-detection pass. Returns the number of tickets newly
    flagged as SLA-breached — exposed for tests and an on-demand admin
    trigger, not just the periodic loop."""
    breached = await asyncio.to_thread(db.expire_overdue_sla)
    for ticket in breached:
        try:
            await _raise_breach(ticket)
        except Exception as exc:
            logger.warning("itsm_sla_sweep: failed to raise finding for ticket %s: %s", ticket.get("id"), exc)
    if breached:
        logger.info("itsm_sla_sweep: flagged %d ITSM ticket(s) as SLA-breached", len(breached))
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
