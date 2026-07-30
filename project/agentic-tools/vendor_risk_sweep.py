#!/usr/bin/env python3
"""
Continuous Third-Party/Vendor Risk — SOC 2 expiry sweep.

Periodically flips CURRENT observability.vendor_risk_profiles rows to
EXPIRED once past their soc2_expires_at, and raises a fresh finding for
each — turns VM-01 (Vendor Security Assessment) from a point-in-time
checklist item into a continuously monitored one, the same way
risk_waiver_sweep.py turns waiver expiry from "silently lapses" into
"control re-opens as failing."

Vendor spend-concentration checking (VENDOR_CONCENTRATION_BREACH) is NOT
in this sweep — that check needs live ERP payment data, so it rides
oracle_fusion_tool.py's poll-connector pull_events() instead (same
per-connector-credential access this sweep doesn't have). This file only
covers the DB-only half of Continuous Third-Party/Vendor Risk.

Mirrors risk_waiver_sweep.py's shape exactly: infinite loop, errors caught
and logged, never exits on its own except cancellation. Started as an
asyncio task in api_server.py's lifespan alongside the other background loops.
"""

from __future__ import annotations

import asyncio
import logging

import db
import mcp_governance

logger = logging.getLogger(__name__)

# Daily is plenty of resolution for a SOC 2 report's day-granularity expiry
# date — same reasoning as risk_waiver_sweep.py's hourly tick, just an even
# slower-moving control.
_TICK_S = 86400


async def _raise_expired(vendor: dict) -> None:
    """Re-ingest the expired SOC 2 as a fresh system_telemetry event so it
    flows through the normal adjudication pipeline and reappears in
    Continuous Monitoring / the HITL inbox — the vendor's control-reliance
    basis has lapsed, same "goes back to failing" semantics as a waiver
    expiring."""
    flags = await asyncio.to_thread(mcp_governance._detect_system_flags, {
        "action": "vendor_soc2_expired", "resource": vendor["vendor_name"],
        "severity": "HIGH", "event_type": "vendor_soc2_expired",
        "payload": {"vendor_soc2_expired": True},
    })
    await asyncio.to_thread(
        mcp_governance._ingest_system_event,
        "vendor-risk-sweep", "vendor_risk", "vendor_soc2_expired",
        f"soc2-expired:{vendor['id']}:{vendor['soc2_expires_at']}",
        None, "soc2_expiry_check", vendor["vendor_name"],
        "HIGH", flags,
        {
            "vendor_soc2_expired": True,
            "vendor_risk_detail": {
                "vendor_name": vendor["vendor_name"],
                "vendor_id": vendor.get("vendor_id"),
                "critical": vendor.get("critical", False),
                "soc2_expires_at": vendor["soc2_expires_at"],
            },
        },
        None,
    )


async def sweep_once() -> int:
    """Run one expiry pass. Returns the number of vendor SOC 2 profiles
    expired — exposed for tests and an on-demand admin trigger, not just
    the periodic loop."""
    expired = await asyncio.to_thread(db.expire_overdue_vendor_soc2)
    for vendor in expired:
        try:
            await _raise_expired(vendor)
        except Exception as exc:
            logger.warning("vendor_risk_sweep: failed to raise finding for vendor %s: %s", vendor.get("id"), exc)
    if expired:
        logger.info("vendor_risk_sweep: expired %d vendor SOC 2 profile(s)", len(expired))
    return len(expired)


async def start_sweep() -> None:
    logger.info("Vendor risk SOC 2 expiry sweep started (tick=%.0fs)", _TICK_S)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("Vendor risk SOC 2 expiry sweep stopped")
            break
        except Exception as exc:
            logger.warning("vendor_risk_sweep tick error: %s", exc)
