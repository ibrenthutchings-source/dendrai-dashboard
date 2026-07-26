#!/usr/bin/env python3
"""
Unit tests for pac_negative_tests.py — the must-fire/must-not-fire corpus
runner (P0b). Uses hand-built minimal Rego fixtures (not pac_endpoints'
shipped defaults) so these tests independently verify the runner's pass/fail
logic itself, separate from test_pac_contracts.py's checks on the real
devops_monitoring module.

    pytest test_pac_negative_tests.py -v
"""

from __future__ import annotations

import pac_negative_tests as pnt

_MINI_REGO = '''
package mini

import future.keywords.if

deny_x[msg] if {
    input.event.flag == true
    msg := sprintf("TEST-001: flag was set on '%v'", [input.event.flag])
}
'''
# The heuristic fallback's control_id extraction only recognizes
# `msg := sprintf("...")` (matching every real Rego module in this repo) —
# a plain `msg := "..."` string literal isn't parsed for a control_id under
# the heuristic path, though real OPA doesn't care either way. This fixture
# uses sprintf so tests exercise both engines identically.


def test_run_fixture_must_fire_passes_when_expected_control_fires():
    fixture = pnt.Fixture(
        name="flag_set", input_event={"event": {"flag": True}},
        expect="fire", expected_control_id="TEST-001",
    )
    result = pnt.run_fixture(_MINI_REGO, fixture)
    assert result["passed"] is True
    assert result["fired_control_ids"] == ["TEST-001"]


def test_run_fixture_must_fire_fails_when_nothing_fires():
    fixture = pnt.Fixture(
        name="flag_unset", input_event={"event": {"flag": False}},
        expect="fire", expected_control_id="TEST-001",
    )
    result = pnt.run_fixture(_MINI_REGO, fixture)
    assert result["passed"] is False
    assert result["fired_control_ids"] == []


def test_run_fixture_must_fire_fails_when_wrong_control_fires():
    """A rule firing isn't enough — it has to be the EXPECTED control. Catches
    the case where an edit breaks rule A but rule B's unrelated firing masks it."""
    fixture = pnt.Fixture(
        name="flag_set", input_event={"event": {"flag": True}},
        expect="fire", expected_control_id="TEST-999-DOES-NOT-EXIST",
    )
    result = pnt.run_fixture(_MINI_REGO, fixture)
    assert result["passed"] is False


def test_run_fixture_must_not_fire_passes_when_silent():
    fixture = pnt.Fixture(name="flag_unset", input_event={"event": {"flag": False}}, expect="silent")
    result = pnt.run_fixture(_MINI_REGO, fixture)
    assert result["passed"] is True


def test_run_fixture_must_not_fire_fails_when_it_fires():
    fixture = pnt.Fixture(name="flag_set", input_event={"event": {"flag": True}}, expect="silent")
    result = pnt.run_fixture(_MINI_REGO, fixture)
    assert result["passed"] is False


def test_run_fixture_rejects_invalid_expect_value():
    fixture = pnt.Fixture(name="bad", input_event={"event": {}}, expect="not_a_real_value")
    try:
        pnt.run_fixture(_MINI_REGO, fixture)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_run_corpus_reports_no_corpus_for_unregistered_process():
    result = pnt.run_corpus("some_process_with_no_corpus", _MINI_REGO)
    assert result["ok"] is None
    assert result["total"] == 0
    assert "note" in result


def test_run_corpus_aggregates_pass_fail_counts():
    result = pnt.run_corpus("devops_monitoring", _MINI_REGO)
    # _MINI_REGO doesn't implement any of the devops_monitoring corpus's
    # expected controls, so every must-fire fixture should fail and every
    # must-not-fire fixture should pass (nothing in _MINI_REGO ever fires
    # for their inputs either).
    assert result["ok"] is False
    assert result["total"] == len(pnt.DEVOPS_MONITORING_FIXTURES)
    assert result["passed"] + result["failed"] == result["total"]


def test_run_all_corpora_covers_every_requested_process():
    result = pnt.run_all_corpora({"devops_monitoring": _MINI_REGO, "itgc": _MINI_REGO})
    assert set(result.keys()) == {"devops_monitoring", "itgc"}
    assert result["itgc"]["ok"] is None  # no corpus registered for itgc yet
