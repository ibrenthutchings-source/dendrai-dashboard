#!/usr/bin/env python3
"""
Unit tests for pac_approval_drift.py — the detector for the gap where
db.get_latest_pac_module (what's actually evaluated in production) can
silently diverge from the latest module version a human actually approved,
since pac_policy_modules has no status/approved column and nothing gates
evaluation on approval existing at all (see the module's own docstring).

db.is_available() is False in this environment, so check_process_drift
degrades to "no saved module — evaluating the built-in default", the same
precondition other db-function tests in this suite document.

    pytest test_pac_approval_drift.py -v
"""

from __future__ import annotations

import db
import pac_approval_drift as pad
import pac_endpoints


# ── check_process_drift (no-DB degrade path) ────────────────────────────────

def test_check_process_drift_no_db_reports_default_as_live_and_not_drifted():
    assert not db.is_available()  # documents the precondition this test relies on
    result = pad.check_process_drift("devops_monitoring")
    assert result["live_module_id"] is None
    assert result["drifted"] is False
    assert "built-in default" in result["reason"]


def test_check_process_drift_no_db_live_hash_matches_the_actual_default():
    result = pad.check_process_drift("devops_monitoring")
    expected_hash = pad._content_hash(pac_endpoints._REGO_DEFAULTS["devops_monitoring"])
    assert result["live_hash"] == expected_hash


def test_check_process_drift_unknown_process_still_returns_a_result():
    """No KeyError for a process with no built-in default either — just an
    empty-string hash, which is still a defined, honest answer."""
    result = pad.check_process_drift("not_a_real_process")
    assert result["process"] == "not_a_real_process"
    assert result["drifted"] is False


# ── check_all_processes ──────────────────────────────────────────────────────

def test_check_all_processes_covers_every_builtin_default():
    result = pad.check_all_processes()
    assert set(pac_endpoints._REGO_DEFAULTS.keys()) <= set(result["processes"].keys())


def test_check_all_processes_any_drifted_false_when_nothing_saved():
    result = pad.check_all_processes()
    assert result["any_drifted"] is False


# ── _content_hash (pure) ─────────────────────────────────────────────────────

def test_content_hash_is_deterministic():
    assert pad._content_hash("package foo") == pad._content_hash("package foo")


def test_content_hash_differs_for_different_content():
    assert pad._content_hash("package foo") != pad._content_hash("package bar")


def test_content_hash_handles_empty_and_none_the_same():
    assert pad._content_hash("") == pad._content_hash(None)
