#!/usr/bin/env python3
"""
Risk Waiver & Exception Hub — automated expiry sweep.

Periodically flips ACTIVE observability.risk_waivers rows to EXPIRED once
past their expires_at, and re-escalates the underlying finding: "automated
expiry" means the control goes back to failing and reappears for review, not
that the waiver silently lapses with no one told (db.expire_overdue_waivers's
docstring makes the same point).

Mirrors mcp_governance.start_polling()'s shape exactly: infinite loop, errors
caught and logged, never exits on its own except cancellation. Started as an
asyncio task in api_server.py's lifespan alongside the other background loops.
"""

from __future__ import annotations

import asyncio
import logging

import db
import mcp_governance

logger = logging.getLogger(__name__)

# Hourly is plenty of resolution for day-granularity waiver expirations —
# there's no "2am override"-style urgency here, unlike SCM drift polling.
_TICK_S = 3600


async def _reescalate(waiver: dict) -> None:
    """Re-ingest the original finding as a fresh system_telemetry event tagged
    sast_finding, so it flows through the normal adjudication pipeline again
    and reappears in Continuous Monitoring / the HITL inbox exactly as if it
    were newly discovered — the waiver's grace period is over."""
    flags = await asyncio.to_thread(mcp_governance._detect_system_flags, {
        "action": "sast_finding", "resource": waiver["vulnerability_hash"],
        "severity": "HIGH", "event_type": "sast_finding",
        "payload": {"sast_finding": True},
    })
    await asyncio.to_thread(
        mcp_governance._ingest_system_event,
        "risk-waiver-sweep", "sast", "sast_finding",
        f"waiver-expired:{waiver['vulnerability_hash']}:{waiver['expires_at']}",
        None, "waiver_expired", waiver["vulnerability_hash"],
        "HIGH", flags,
        {
            "waiver_id": waiver["id"], "reason": waiver["reason"],
            "compensating_control": waiver["compensating_control"],
            "approved_by": waiver["approved_by"], "expired_at": waiver["expires_at"],
            "note": "Risk waiver expired — control re-opened as failing.",
        },
        None,
    )


async def sweep_once() -> int:
    """Run one expiry pass. Returns the number of waivers expired — exposed
    for tests and for an on-demand admin trigger, not just the periodic loop."""
    expired = await asyncio.to_thread(db.expire_overdue_waivers)
    for waiver in expired:
        try:
            await _reescalate(waiver)
        except Exception as exc:
            logger.warning("risk_waiver_sweep: failed to re-escalate waiver %s: %s", waiver.get("id"), exc)
    if expired:
        logger.info("risk_waiver_sweep: expired %d waiver(s), re-escalated", len(expired))
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
