#!/usr/bin/env python3
"""
Unit tests for pac_negative_sweep.py — the periodic full-evaluation sweep
(P1a). db.is_available() is False in this environment, so these exercise the
no-DB degrade path (built-in defaults only, no regression detection since
there's no persisted history to compare against) — the same precondition
test_pac_assurance.py documents.

    pytest test_pac_negative_sweep.py -v
"""

from __future__ import annotations

import asyncio

import db
import pac_endpoints
import pac_negative_sweep as pns


def test_sweep_once_covers_every_builtin_process():
    results = asyncio.run(pns.sweep_once())
    assert set(results.keys()) == set(pac_endpoints._REGO_DEFAULTS.keys())


def test_sweep_once_infrastructure_monitoring_passes():
    results = asyncio.run(pns.sweep_once())
    assert results["infrastructure_monitoring"]["ok"] is True


def test_sweep_once_original_builtins_fail_by_construction():
    """Documents the same finding test_pac_contracts.py locks in, from the
    sweep's perspective: itgc/receive_to_ship/record_to_report still fail
    (no real producer wires their input fields yet). procure_to_pay also
    still fails here — but for a documented, narrower reason (deny_vendor_
    event's "new_vendor_activation" clause, pre-existing deferred debt) — not
    the broad "every field is unproducible" failure this process used to have
    before generate_o2c_p2p_synthetic_log.py's wiring; see test_pac_contracts.
    py's test_procure_to_pay_is_no_longer_dead_by_root_except_one_documented_
    gap for the precise remaining gap. order_to_cash is fully fixed — see
    test_sweep_once_order_to_cash_now_passes below. This is a tripwire that
    will fail (as a welcome surprise) the day someone wires a real producer
    for one of the three remaining processes and forgets to update this test."""
    results = asyncio.run(pns.sweep_once())
    for process in ("itgc", "receive_to_ship", "record_to_report"):
        assert results[process]["ok"] is False, f"{process} unexpectedly passed"
    assert results["procure_to_pay"]["ok"] is False
    assert results["procure_to_pay"]["contract"]["unproducible_roots"] == []


def test_sweep_once_order_to_cash_now_passes():
    """order_to_cash now has a real producer (generate_o2c_p2p_synthetic_log.py,
    routed via mcp_governance._SOURCE_EVENT_TO_PAC_PROCESS) — the sweep must
    report it as passing, matching infrastructure_monitoring."""
    results = asyncio.run(pns.sweep_once())
    assert results["order_to_cash"]["ok"] is True


def test_rego_for_process_falls_back_to_builtin_default_without_db():
    assert not db.is_available()  # documents the precondition
    rego, module_id = pns._rego_for_process("infrastructure_monitoring")
    assert rego == pac_endpoints._REGO_DEFAULTS["infrastructure_monitoring"]
    assert module_id is None


def test_rego_for_process_unknown_process_returns_none():
    rego, module_id = pns._rego_for_process("not_a_real_process")
    assert rego is None
    assert module_id is None


def test_sweep_once_does_not_raise_without_database():
    """The whole point of the try/except shape in start_sweep is that a
    single tick's failure never kills the loop — sweep_once itself must
    also never raise just because persistence isn't available."""
    result = asyncio.run(pns.sweep_once())
    assert isinstance(result, dict) and result
