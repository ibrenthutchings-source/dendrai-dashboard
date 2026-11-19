#!/usr/bin/env python3
"""
Policy-as-Code auto-sync sweep.

The UBO Configuration screen's PaC repo registry (observability.pac_repositories)
could previously only be synced by a human clicking "Sync Now"
(mcp_governance.sync_pac_repo). This sweep polls every repo with
auto_sync_enabled=TRUE and, when the branch's HEAD commit has actually moved
since the last sync, triggers the exact same sync path a manual click uses —
this module adds a trigger, not a second import/conversion pipeline.

Mirrors risk_waiver_sweep.py's shape exactly: infinite loop, errors caught
and logged per-repo (one repo's failure never stops the rest of the sweep),
never exits on its own except cancellation. Started as an asyncio task in
api_server.py's lifespan alongside the other background loops.
"""

from __future__ import annotations

import asyncio
import logging

import mcp_governance
import pac_endpoints

logger = logging.getLogger(__name__)

# Hourly — a policy repo doesn't change often enough to need finer-grained
# polling, and this is the same cadence risk_waiver_sweep.py uses for a
# similarly low-frequency check.
_TICK_S = 3600


async def sweep_once() -> int:
    """One pass over every auto_sync_enabled repo. Returns the number of
    repos actually synced (i.e. whose HEAD commit had moved) — exposed for
    tests and any future on-demand admin trigger, not just the periodic loop."""
    repos = await asyncio.to_thread(mcp_governance._fetch_auto_sync_candidates)
    synced = 0
    for repo in repos:
        try:
            head_sha = await pac_endpoints.get_branch_head_sha(
                repo["owner"], repo["repo_name"], repo["branch"], repo["token"],
            )
            if not head_sha or head_sha == repo["last_synced_sha"]:
                continue
            await mcp_governance.sync_pac_repo(repo["id"])
            await asyncio.to_thread(mcp_governance._update_last_synced_sha, repo["id"], head_sha)
            synced += 1
        except Exception as exc:
            logger.warning("pac_auto_sync_sweep: repo %s failed: %s", repo.get("id"), exc)
    if synced:
        logger.info("pac_auto_sync_sweep: auto-synced %d repo(s)", synced)
    return synced


async def start_sweep() -> None:
    logger.info("PaC auto-sync sweep started (tick=%.0fs)", _TICK_S)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("PaC auto-sync sweep stopped")
            break
        except Exception as exc:
            logger.warning("pac_auto_sync_sweep tick error: %s", exc)
