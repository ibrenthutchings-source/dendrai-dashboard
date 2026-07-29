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


def test_sweep_once_devops_monitoring_passes():
    results = asyncio.run(pns.sweep_once())
    assert results["devops_monitoring"]["ok"] is True


def test_sweep_once_original_builtins_fail_by_construction():
    """Documents the same finding test_pac_contracts.py locks in, from the
    sweep's perspective: every original built-in process except
    devops_monitoring currently fails (no real producer wires their input
    fields yet) — a tripwire that will fail (as a welcome surprise) the day
    someone wires a real producer and forgets to update this test."""
    results = asyncio.run(pns.sweep_once())
    for process in ("itgc", "order_to_cash", "procure_to_pay", "receive_to_ship", "record_to_report"):
        assert results[process]["ok"] is False, f"{process} unexpectedly passed"


def test_rego_for_process_falls_back_to_builtin_default_without_db():
    assert not db.is_available()  # documents the precondition
    rego, module_id = pns._rego_for_process("devops_monitoring")
    assert rego == pac_endpoints._REGO_DEFAULTS["devops_monitoring"]
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
