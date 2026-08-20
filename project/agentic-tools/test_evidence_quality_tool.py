#!/usr/bin/env python3
"""
Unit tests for evidence_quality_tool.py — deterministic PBC evidence
quality checks (period mismatch, staleness, missing signature).

    pytest test_evidence_quality_tool.py -v
"""
from __future__ import annotations

from datetime import date

import evidence_quality_tool as eqt


# ── check_period_mismatch ─────────────────────────────────────────────────────

def test_period_mismatch_flags_missing_collected_date():
    flag = eqt.check_period_mismatch(None, "2026-01-01", "2026-03-31")
    assert flag["code"] == "MISSING_COLLECTED_DATE"
    assert flag["severity"] == "HIGH"


def test_period_mismatch_flags_before_period_start():
    flag = eqt.check_period_mismatch("2025-12-15", "2026-01-01", "2026-03-31")
    assert flag["code"] == "PERIOD_MISMATCH"
    assert "before" in flag["message"]


def test_period_mismatch_flags_after_period_end():
    flag = eqt.check_period_mismatch("2026-04-15", "2026-01-01", "2026-03-31")
    assert flag["code"] == "PERIOD_MISMATCH"
    assert "after" in flag["message"]


def test_period_mismatch_clean_when_inside_period():
    assert eqt.check_period_mismatch("2026-02-15", "2026-01-01", "2026-03-31") is None


def test_period_mismatch_clean_when_no_period_bounds_given():
    """No period_start/period_end recorded — can't check alignment, so
    don't fabricate a mismatch finding; only a missing date is flagged."""
    assert eqt.check_period_mismatch("2026-02-15", None, None) is None


# ── check_staleness ────────────────────────────────────────────────────────────

def test_staleness_flags_evidence_past_threshold():
    flag = eqt.check_staleness("2025-01-01", max_age_days=90, today=date(2026, 1, 1))
    assert flag["code"] == "STALE"
    assert flag["severity"] == "MEDIUM"


def test_staleness_clean_within_threshold():
    assert eqt.check_staleness("2025-12-15", max_age_days=90, today=date(2026, 1, 1)) is None


def test_staleness_none_when_no_collected_date():
    """Missing date is check_period_mismatch's job to flag, not staleness'."""
    assert eqt.check_staleness(None, max_age_days=90) is None


def test_staleness_exact_boundary_is_not_stale():
    flag = eqt.check_staleness("2025-10-03", max_age_days=90, today=date(2026, 1, 1))
    assert flag is None  # exactly 90 days — threshold is "greater than", not "at least"


# ── check_signature ────────────────────────────────────────────────────────────

def test_signature_flags_missing_when_required():
    flag = eqt.check_signature(has_signature=False, requires_signature=True)
    assert flag["code"] == "UNSIGNED"
    assert flag["severity"] == "HIGH"


def test_signature_clean_when_present():
    assert eqt.check_signature(has_signature=True, requires_signature=True) is None


def test_signature_clean_when_not_required():
    assert eqt.check_signature(has_signature=False, requires_signature=False) is None


# ── run_quality_checks ────────────────────────────────────────────────────────

def test_run_quality_checks_returns_empty_for_clean_evidence():
    evidence = {
        "collected_date": "2026-02-01", "period_start": "2026-01-01", "period_end": "2026-03-31",
        "has_signature": True, "requires_signature": True, "max_age_days": 365,
    }
    assert eqt.run_quality_checks(evidence, today=date(2026, 2, 5)) == []


def test_run_quality_checks_returns_all_applicable_flags():
    evidence = {
        "collected_date": "2025-01-01", "period_start": "2026-01-01", "period_end": "2026-03-31",
        "has_signature": False, "requires_signature": True, "max_age_days": 90,
    }
    flags = eqt.run_quality_checks(evidence, today=date(2026, 2, 5))
    codes = {f["code"] for f in flags}
    assert "PERIOD_MISMATCH" in codes
    assert "STALE" in codes
    assert "UNSIGNED" in codes


def test_run_quality_checks_orders_high_severity_first():
    evidence = {
        "collected_date": "2025-06-01", "has_signature": False, "requires_signature": True, "max_age_days": 30,
    }
    flags = eqt.run_quality_checks(evidence, today=date(2026, 1, 1))
    severities = [f["severity"] for f in flags]
    assert severities == sorted(severities, key=lambda s: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[s])
