#!/usr/bin/env python3
"""
DevOps Monitoring: Risk Waiver & Exception Hub — expiry sweep.

observability.risk_waivers and its full CRUD (db.py: create_risk_waiver,
list_risk_waivers, get_active_waiver, revoke_risk_waiver,
expire_overdue_waivers) already existed with no sweep ever calling
expire_overdue_waivers() — every other sweep in this codebase
(vendor_risk_sweep.py, connector_hygiene_sweep.py, identity_graph_sync.py,
pac_negative_sweep.py) cites "risk_waiver_sweep.py" in its own docstring as
the shape it copied, but that file itself was never actually built. This is
that file.

Periodically flips ACTIVE observability.risk_waivers rows to EXPIRED once
past their expires_at, and re-raises a fresh finding for each — turns a
time-boxed compensating-control exception (approved via the
devops_scm_exception HITL gate, see approvals_endpoints._create_waiver_from_task)
from "silently lapses, stays green forever" into "goes back to failing the
moment the clock runs out," same reasoning vendor_risk_sweep.py applies to
an expired vendor SOC 2 report.

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

# Hourly — matches every other sweep's docstring-cited cadence for this file,
# and a waiver's expires_at is set to day/hour granularity by the approving
# manager, not something that needs finer resolution than this.
_TICK_S = 3600


async def _raise_expired(waiver: dict) -> None:
    """Re-ingest the expired waiver's underlying finding as a fresh
    system_telemetry event so it flows through the normal adjudication
    pipeline and reappears in Continuous Monitoring / the HITL inbox — the
    compensating control's time-boxed basis has lapsed, same "goes back to
    failing" semantics as vendor_risk_sweep._raise_expired."""
    flags = await asyncio.to_thread(mcp_governance._detect_system_flags, {
        "action": "risk_waiver_expired", "resource": waiver["vulnerability_hash"],
        "severity": "HIGH", "event_type": "risk_waiver_expired",
        "payload": {"risk_waiver_expired": True},
    })
    await asyncio.to_thread(
        mcp_governance._ingest_system_event,
        "risk-waiver-sweep", "risk_waiver", "risk_waiver_expired",
        f"waiver-expired:{waiver['id']}:{waiver['expires_at']}",
        waiver.get("approved_by"), "waiver_expiry_check", waiver["vulnerability_hash"],
        "HIGH", flags,
        {
            "risk_waiver_expired": True,
            "risk_waiver_detail": {
                "waiver_id": waiver["id"],
                "vulnerability_hash": waiver["vulnerability_hash"],
                "reason": waiver.get("reason"),
                "compensating_control": waiver.get("compensating_control"),
                "approved_by": waiver.get("approved_by"),
                "expires_at": waiver["expires_at"],
            },
        },
        None,
    )


async def sweep_once() -> int:
    """Run one expiry pass. Returns the number of waivers expired — exposed
    for tests and an on-demand admin trigger, not just the periodic loop."""
    expired = await asyncio.to_thread(db.expire_overdue_waivers)
    for waiver in expired:
        try:
            await _raise_expired(waiver)
        except Exception as exc:
            logger.warning("risk_waiver_sweep: failed to raise finding for waiver %s: %s", waiver.get("id"), exc)
    if expired:
        logger.info("risk_waiver_sweep: expired %d risk waiver(s)", len(expired))
    return len(expired)


async def start_sweep() -> None:
    logger.info("Risk waiver expiry sweep started (tick=%.0fs)", _TICK_S)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("Risk waiver expiry sweep stopped")
            break
        except Exception as exc:
            logger.warning("risk_waiver_sweep tick error: %s", exc)
