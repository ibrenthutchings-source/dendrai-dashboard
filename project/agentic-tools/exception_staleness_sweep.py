#!/usr/bin/env python3
"""
Exception Management: staleness escalation sweep. Development environment
only (see deploy_env.py) — task-creation gated the same way
infra_asset_sweep.py/vulnerability_sweep.py are, since a background loop has
no HTTP request to 404.

Closes the "unbounded growth" gap in the Triage Queue: a pending exception
otherwise ages silently toward the 400-day hard-delete
(purge_expired_exception_events) with nothing surfacing that it's been
sitting untouched. Daily, flips any pending, not-yet-R-rated exception older
than EXCEPTION_STALE_DAYS (default 14) to risk_rating='R' — this does NOT
auto-resolve anything (a stale exception isn't necessarily benign, silently
closing it would be exactly the kind of fabricated "all clear" this
platform's other sweeps avoid); it only escalates visibility so an aging
item surfaces at the top of the risk-sorted queue instead of getting buried
under fresher, lower-priority items.

Mirrors risk_waiver_sweep.py's shape exactly: infinite loop, errors caught
and logged, never exits on its own except cancellation.
"""

from __future__ import annotations

import asyncio
import logging
import os

import db

logger = logging.getLogger(__name__)

_TICK_S = 86400  # daily
_STALE_DAYS = int(os.environ.get("EXCEPTION_STALE_DAYS", "14"))


async def sweep_once() -> int:
    """Run one escalation pass. Returns the number of exceptions escalated —
    exposed for tests and an on-demand admin trigger, not just the periodic
    loop."""
    if not db.is_available():
        return 0
    escalated = await asyncio.to_thread(db.escalate_stale_exceptions, _STALE_DAYS)
    if escalated:
        logger.info("exception_staleness_sweep: escalated %d stale pending exception(s) to risk_rating=R", escalated)
    return escalated


async def start_sweep() -> None:
    logger.info("Exception staleness sweep started (tick=%.0fs, stale_days=%d)", _TICK_S, _STALE_DAYS)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("Exception staleness sweep stopped")
            break
        except Exception as exc:
            logger.warning("exception_staleness_sweep tick error: %s", exc)
