#!/usr/bin/env python3
"""
Identity/role graph sync — periodic pull of real user<->role assignments
and open Segregation-of-Duties violations from every active Oracle Fusion
connector.

Feeds observability.identity_role_edges / .sod_violations (db.py), which
mcp_governance.py's _process_one() reads to populate role_count/entitlements
on a URO's risk_indicators before it reaches The Graph Architect
(UBO/agents/graph_architect.py) — those fields were previously always
zero/empty for every real production event (no Silver conformer ever set
them from real data), so the agent's blast-radius/SPoF checks, though
correctly implemented, were structurally dead. This sync makes their input
real; it changes no threshold, no scoring logic, and no agent code.

oracle_fusion_tool.get_user_roles()/get_sod_violations() already exist and
are correct — they were just never called by anything scheduled (only
reachable via an on-demand REST endpoint). This is a full-refresh sync, not
an incremental "since" pull: identity/role state is a snapshot to diff, not
a stream of discrete events, so it doesn't fit connector_poller.py's
pull_events() event-shaped abstraction — see db.upsert_identity_role_edges/
upsert_sod_violations for the delete-then-insert semantics that makes a
revoked role actually disappear here.

SoD violations are persisted but NOT (yet) re-raised as adjudicated events —
that's a separate follow-up that would engage graph_architect.py's already-
coded but currently-unreachable EventType.SOD_VIOLATION amplifier; out of
scope here, this sweep only keeps the two tables current.

Mirrors risk_waiver_sweep.py's shape exactly: infinite loop, errors caught
and logged, never exits on its own except cancellation. Started as an
asyncio task in api_server.py's lifespan alongside the other background loops.
"""

from __future__ import annotations

import asyncio
import logging

import db
import oracle_fusion_tool

logger = logging.getLogger(__name__)

# Identity/role assignments are slow-moving (people don't change roles by
# the minute) — hourly matches risk_waiver_sweep.py/itsm_sla_sweep.py's
# cadence for similarly slow-moving state.
_TICK_S = 3600


def _client_for(full_connector: dict) -> "oracle_fusion_tool.OracleFusionClient":
    creds = full_connector.get("credentials") or {}
    return oracle_fusion_tool.OracleFusionClient(
        host=full_connector.get("base_url"),
        username=creds.get("username"),
        password=creds.get("password"),
        client_id=creds.get("client_id"),
        client_secret=creds.get("client_secret"),
    )


async def _sync_one(connector: dict) -> dict:
    """Sync one Oracle Fusion connector's role assignments + open SoD
    violations. Never raises — a failing connector is logged and skipped so
    it can't block the others in the same tick."""
    connector_id = connector["id"]
    try:
        full = await asyncio.to_thread(db.get_poll_connector, connector_id, True)
    except db.EncryptionKeyMissing as exc:
        logger.warning("identity_graph_sync: connector %s skipped — %s", connector_id, exc)
        return {"connector_id": connector_id, "error": str(exc)}
    if not full:
        return {"connector_id": connector_id, "error": "connector not found"}

    try:
        client = _client_for(full)
        roles = await asyncio.to_thread(oracle_fusion_tool.get_user_roles, "", "", 2000, client)
        if roles.get("error"):
            raise RuntimeError(roles["error"])
        sod = await asyncio.to_thread(oracle_fusion_tool.get_sod_violations, "Open", "", 500, client)
        if sod.get("error"):
            raise RuntimeError(sod["error"])

        n_roles = await asyncio.to_thread(db.upsert_identity_role_edges, connector_id, roles["assignments"])
        n_sod = await asyncio.to_thread(db.upsert_sod_violations, connector_id, sod["violations"])
        logger.info(
            "identity_graph_sync: connector %s (%s) — %d role edge(s), %d open SoD violation(s)",
            connector_id, full["display_name"], n_roles, n_sod,
        )
        return {"connector_id": connector_id, "roles": n_roles, "sod_violations": n_sod}
    except Exception as exc:
        logger.warning("identity_graph_sync: connector %s failed: %s", connector_id, exc)
        return {"connector_id": connector_id, "error": str(exc)}


async def sweep_once() -> dict:
    """Run one sync pass across every active Oracle Fusion connector.
    Returns {connector_id: result} — exposed for tests and an on-demand
    admin trigger, not just the periodic loop."""
    connectors = await asyncio.to_thread(db.list_poll_connectors)
    oracle_connectors = [c for c in connectors if c["connector_type"] == "oracle_fusion" and c["active"]]
    results: dict = {}
    for c in oracle_connectors:
        results[c["id"]] = await _sync_one(c)
    return results


async def start_sweep() -> None:
    logger.info("Identity graph sync started (tick=%.0fs)", _TICK_S)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("Identity graph sync stopped")
            break
        except Exception as exc:
            logger.warning("identity_graph_sync tick error: %s", exc)
