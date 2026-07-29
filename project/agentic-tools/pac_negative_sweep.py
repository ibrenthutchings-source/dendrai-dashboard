#!/usr/bin/env python3
"""
Policy-as-Code negative testing — periodic full evaluation sweep.

Runs pac_assurance.evaluate_and_record for every registered process on a
schedule, not just when a human happens to edit or approve a module. Two
things this catches that edit-time testing alone can't:

  1. A control's Rego doesn't change, but what it's tested against does —
     the moment a real producer starts feeding a previously-template-only
     process (see pac_contracts.py's module docstring), this sweep is what
     notices the contract now passes / a corpus now applies, without anyone
     having to remember to re-test.
  2. REGRESSION: a module that passed its corpus last sweep and fails this
     one. The Rego text may not even have changed — a conformer edit
     elsewhere in the codebase (Silver layer risk_indicators, a routing map)
     can silently break a policy's contract just as easily as editing the
     Rego itself, and nothing about editing silver.py would trigger the
     approval-gate test. Comparing consecutive observability.pac_test_runs
     rows is what catches that.
  3. APPROVAL DRIFT (pac_approval_drift.py): the module actually being
     evaluated (db.get_latest_pac_module — the latest SAVE, not the latest
     APPROVAL) silently diverges from the latest version a human actually
     signed off on. Since there's no approval gate on evaluation itself
     (see pac_approval_drift.py's module docstring), this is the only thing
     that surfaces it.

Mirrors risk_waiver_sweep.py/itsm_sla_sweep.py's shape exactly: infinite
loop, errors caught and logged, never exits on its own except cancellation.
Started as an asyncio task in api_server.py's lifespan alongside those.
"""

from __future__ import annotations

import asyncio
import logging

import db
import pac_approval_drift
import pac_assurance
import pac_endpoints

logger = logging.getLogger(__name__)

# Hourly matches risk_waiver_sweep.py/itsm_sla_sweep.py's cadence — negative
# testing is pure Rego evaluation (no external calls), so this is cheap to
# run this often; there's no "2am override"-style urgency requiring finer
# resolution either.
_TICK_S = 3600


def _rego_for_process(process: str) -> tuple[str | None, int | None]:
    """The Rego currently "live" for a process: the latest saved module if
    one exists, falling back to the built-in default — the same fallback
    chain _evaluate_pac_policy itself uses, so this sweep tests exactly what
    real adjudication would evaluate against, not some other version."""
    saved = db.get_latest_pac_module(process) if db.is_available() else None
    if saved:
        return saved["rego_content"], saved["id"]
    return pac_endpoints._REGO_DEFAULTS.get(process), None


async def sweep_once() -> dict:
    """Run one full-evaluation pass across every process. Returns
    {process: evaluate_and_record() result} — exposed for tests and for an
    on-demand admin/MCP trigger, not just the periodic loop."""
    processes = sorted(pac_endpoints._valid_processes()) if db.is_available() else sorted(pac_endpoints._REGO_DEFAULTS)
    results: dict = {}
    for process in processes:
        rego_content, module_id = await asyncio.to_thread(_rego_for_process, process)
        if not rego_content:
            continue

        # Regression check: compare against the previous run BEFORE this
        # sweep's own result gets persisted, so "previous" really means
        # "before this tick," not "the row we're about to write."
        previous_ok = None
        if db.is_available():
            history = await asyncio.to_thread(db.list_pac_test_runs, process, 1)
            if history:
                prev = history[0]
                previous_ok = bool(prev.get("contract_ok")) and prev.get("passed") == prev.get("total")

        result = await asyncio.to_thread(
            pac_assurance.evaluate_and_record, process, rego_content,
            module_id=module_id, triggered_by="scheduled_sweep",
        )
        results[process] = result

        drift = await asyncio.to_thread(pac_approval_drift.check_process_drift, process)
        result["approval_drift"] = drift
        if drift["drifted"]:
            logger.warning(
                "pac_negative_sweep: APPROVAL DRIFT on process '%s' — %s",
                process, drift["reason"],
            )

        if previous_ok is True and result["ok"] is False:
            logger.warning(
                "pac_negative_sweep: REGRESSION on process '%s' — passed last sweep, "
                "fails now (contract_ok=%s, corpus=%s/%s)",
                process, result["contract"]["ok"],
                result["corpus"].get("passed"), result["corpus"].get("total"),
            )

    return results


async def start_sweep() -> None:
    logger.info("PaC negative-testing sweep started (tick=%.0fs)", _TICK_S)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("PaC negative-testing sweep stopped")
            break
        except Exception as exc:
            logger.warning("pac_negative_sweep tick error: %s", exc)
