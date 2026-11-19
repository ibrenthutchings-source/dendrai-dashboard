#!/usr/bin/env python3
"""
PII / telemetry retention sweep — SOC 2 Privacy (P4/P5) purge job.

exception_control_events.raw_payload/actor carries unredacted source-system
event data pulled from ERP/HR/finance connectors, and previously had no TTL
at all (see db.purge_expired_exception_events's docstring). This sweep turns
that "never deleted" gap into a documented, configurable retention window —
EXCEPTION_EVENT_RETENTION_DAYS (default db.DEFAULT_EXCEPTION_EVENT_RETENTION_DAYS)
env var, same override pattern the rest of this codebase uses for tunables.

Mirrors vendor_risk_sweep.py's shape exactly: infinite loop, errors caught
and logged, never exits on its own except cancellation. Started as an asyncio
task in api_server.py's lifespan alongside the other background sweeps. Every
purge run is itself recorded to the tamper-evident audit trail
(db.insert_audit_log_entry) so "how many rows were purged, and when" stays
answerable even though the purged rows themselves are gone.
"""

from __future__ import annotations

import asyncio
import logging
import os

import db

logger = logging.getLogger(__name__)

# Daily is plenty of resolution for a retention window measured in months —
# same reasoning as vendor_risk_sweep.py's daily tick.
_TICK_S = 86400

_RETENTION_DAYS = int(os.environ.get(
    "EXCEPTION_EVENT_RETENTION_DAYS", str(db.DEFAULT_EXCEPTION_EVENT_RETENTION_DAYS)
))


async def sweep_once() -> int:
    """Run one purge pass. Returns the number of exception_control_events
    rows deleted — exposed for tests and an on-demand admin trigger, not just
    the periodic loop."""
    deleted = await asyncio.to_thread(db.purge_expired_exception_events, _RETENTION_DAYS)
    if deleted:
        logger.info("pii_retention_sweep: purged %d exception_control_events row(s) past %dd retention",
                    deleted, _RETENTION_DAYS)
        await asyncio.to_thread(
            db.insert_audit_log_entry, "retention", "exception_events_purged",
            actor="system", detail={"rows_deleted": deleted, "retention_days": _RETENTION_DAYS},
        )
    return deleted


async def start_sweep() -> None:
    logger.info("PII retention sweep started (tick=%.0fs, retention=%dd)", _TICK_S, _RETENTION_DAYS)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("PII retention sweep stopped")
            break
        except Exception as exc:
            logger.warning("pii_retention_sweep tick error: %s", exc)
