#!/usr/bin/env python3
"""
Infrastructure Monitoring: connector credential rotation hygiene — periodic
sweep.

Checks Intelligenza's own observability.poll_connectors credential ages
(connector_hygiene.py) once a day and, if anything is stale, ingests it as a
system_telemetry event tagged infrastructure_finding — the same
INFRASTRUCTURE_FINDING pipeline path postgres_cis_tool.py/railway_iaas_tool.py
use, via mcp_governance._detect_system_flags/_ingest_system_event.

Daily resolution is enough here — a credential going stale is a slow-moving
signal (days-to-months), unlike SCM drift's "2am override" urgency.

Mirrors risk_waiver_sweep.py's shape exactly: infinite loop, errors caught
and logged, never exits on its own except cancellation. Started as an
asyncio task in api_server.py's lifespan alongside the other background loops.
"""

from __future__ import annotations

import asyncio
import logging

import connector_hygiene
import db
import mcp_governance

logger = logging.getLogger(__name__)

_TICK_S = 24 * 3600


async def sweep_once() -> dict:
    """Run one credential-hygiene check. Returns the check result — exposed
    for tests and for an on-demand admin trigger, not just the periodic loop."""
    result = connector_hygiene.check_connector_credential_rotation()
    if result["violated"]:
        flags = await asyncio.to_thread(mcp_governance._detect_system_flags, {
            "action": "credential_rotation_check", "resource": "observability.poll_connectors",
            "severity": result["severity"],
            "payload": {"infrastructure_finding": True, "infra_compliance": result["compliance"]},
        })
        await asyncio.to_thread(
            mcp_governance._ingest_system_event,
            "intelligenza-connector-hygiene", "internal", "credential_rotation_check",
            f"connector-hygiene:{result['compliance']['stale_connector_count']}",
            "connector_hygiene_sweep", "credential_rotation_check", "observability.poll_connectors",
            result["severity"], flags,
            {
                "infrastructure_finding": True,
                "infra_compliance": result["compliance"],
                "note": "Stored connector credential(s) exceed the rotation-age threshold.",
            },
            None,
        )
        logger.info("connector_hygiene_sweep: %d stale connector credential(s) found",
                    result["compliance"]["stale_connector_count"])
    return result


async def start_sweep() -> None:
    logger.info("Connector credential hygiene sweep started (tick=%.0fs)", _TICK_S)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("Connector credential hygiene sweep stopped")
            break
        except Exception as exc:
            logger.warning("connector_hygiene_sweep tick error: %s", exc)
