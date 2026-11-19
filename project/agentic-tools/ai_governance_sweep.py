#!/usr/bin/env python3
"""
AI Governance — assessment expiry sweep.

Periodically flips CURRENT observability.ai_system_registry rows to EXPIRED
once past their assessment_expires_at, and raises a fresh finding for each —
same "control reliance basis has lapsed" semantics as vendor_risk_sweep.py's
SOC 2 expiry sweep, applied to AI-05 (Third-Party AI Tool Assessment).

The other AI Governance check, AI-06 human-oversight-missing, is NOT in this
sweep — it's a static configuration gap (a system either has a defined human
review point or it doesn't), not something that decays with time, so it's
raised inline when the register is saved (ai_governance_endpoints.py) rather
than swept on a schedule.

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

# Daily is plenty of resolution for an assessment's day-granularity expiry
# date — same reasoning as vendor_risk_sweep.py's tick.
_TICK_S = 86400


async def _raise_expired(ai_system: dict) -> None:
    """Re-ingest the expired assessment as a fresh system_telemetry event so
    it flows through the normal adjudication pipeline and reappears in
    Continuous Monitoring / the HITL inbox."""
    flags = await asyncio.to_thread(mcp_governance._detect_system_flags, {
        "action": "ai_assessment_overdue", "resource": ai_system["system_name"],
        "severity": "HIGH", "event_type": "ai_assessment_overdue",
        "payload": {"ai_assessment_overdue": True},
    })
    await asyncio.to_thread(
        mcp_governance._ingest_system_event,
        "ai-governance-sweep", "ai_governance", "ai_assessment_overdue",
        f"assessment-expired:{ai_system['id']}:{ai_system['assessment_expires_at']}",
        None, "assessment_expiry_check", ai_system["system_name"],
        "HIGH", flags,
        {
            "ai_assessment_overdue": True,
            "ai_governance_detail": {
                "system_name": ai_system["system_name"],
                "vendor": ai_system.get("vendor"),
                "risk_tier": ai_system.get("risk_tier"),
                "assessment_expires_at": ai_system["assessment_expires_at"],
            },
        },
        None,
    )


async def sweep_once() -> int:
    """Run one expiry pass. Returns the number of AI system assessments
    expired — exposed for tests and an on-demand admin trigger."""
    expired = await asyncio.to_thread(db.expire_overdue_ai_assessments)
    for ai_system in expired:
        try:
            await _raise_expired(ai_system)
        except Exception as exc:
            logger.warning("ai_governance_sweep: failed to raise finding for AI system %s: %s", ai_system.get("id"), exc)
    if expired:
        logger.info("ai_governance_sweep: expired %d AI system assessment(s)", len(expired))
    return len(expired)


async def start_sweep() -> None:
    logger.info("AI Governance assessment expiry sweep started (tick=%.0fs)", _TICK_S)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("AI Governance assessment expiry sweep stopped")
            break
        except Exception as exc:
            logger.warning("ai_governance_sweep tick error: %s", exc)
