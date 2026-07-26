#!/usr/bin/env python3
"""
Unit tests for pac_assurance.py — the orchestration layer combining
pac_contracts (layer 1) + pac_negative_tests (layer 2) into one
evaluate_and_record() call, and the assurance_summary() rollup. Pure-function
tests only — db.is_available() is False in this environment (no DATABASE_URL),
so these exercise the no-DB-configured degrade path, which is itself worth
locking in: evaluate_and_record must never raise just because persistence
isn't available.

    pytest test_pac_assurance.py -v
"""

from __future__ import annotations

import db
import pac_assurance as pa
import pac_endpoints as pe


def test_evaluate_and_record_devops_monitoring_is_fully_ok():
    result = pa.evaluate_and_record("devops_monitoring", pe._REGO_DEFAULTS["devops_monitoring"])
    assert result["ok"] is True
    assert result["contract"]["ok"] is True
    assert result["corpus"]["ok"] is True
    assert result["corpus"]["total"] == len(pa.pac_negative_tests.DEVOPS_MONITORING_FIXTURES)


def test_evaluate_and_record_flags_dead_by_construction_module():
    """A module whose event.type literal is wrong (the exact original bug)
    must report ok=False even though nothing crashes."""
    broken_rego = pe._REGO_DEFAULTS["devops_monitoring"].replace(
        'input.event.type == "BRANCH_PROTECTION_BYPASSED"',
        'input.event.type == "branch_protection_rule"',
    )
    result = pa.evaluate_and_record("devops_monitoring", broken_rego)
    assert result["ok"] is False
    assert result["contract"]["ok"] is False
    assert "branch_protection_rule" in result["contract"]["invalid_event_types"]


def test_evaluate_and_record_does_not_raise_without_database():
    """db.is_available() is False in this test environment — the whole point
    of the guard in evaluate_and_record is that testing a policy must never
    depend on persistence succeeding."""
    assert not db.is_available()  # documents the precondition this test relies on
    result = pa.evaluate_and_record("devops_monitoring", pe._REGO_DEFAULTS["devops_monitoring"],
                                     triggered_by="scheduled_sweep")
    assert result["ok"] is True


def test_evaluate_and_record_process_with_no_corpus_is_not_falsely_ok():
    """itgc has no registered negative-control corpus AND fails its contract
    check (unproducible roots) — 'ok' must reflect the contract failure, not
    get masked by the corpus reporting ok=None (no corpus == not evaluated,
    not passed)."""
    result = pa.evaluate_and_record("itgc", pe._REGO_DEFAULTS["itgc"])
    assert result["contract"]["ok"] is False
    assert result["corpus"]["ok"] is None
    assert result["ok"] is False


def test_assurance_summary_degrades_cleanly_without_database():
    summary = pa.assurance_summary()
    assert summary == {"controls": [], "unverified": [], "total": 0, "unverified_count": 0}
