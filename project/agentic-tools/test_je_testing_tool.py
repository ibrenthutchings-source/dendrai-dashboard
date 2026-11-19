#!/usr/bin/env python3
"""
Unit tests for je_testing_tool.py — classic JE anomaly rules over normalized
GL data. Entirely pure: no DB needed, same testability reasoning as
test_process_mining_tool.py's process_mining_tool.py tests.

    pytest test_je_testing_tool.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import je_testing_tool as jt

_BASE = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)  # a Monday


def _je(je_id="JE-1", amount=1234.56, account="6100-OPEX", description="Vendor invoice payment",
        preparer="alice", approver="bob", posted_at=None, source_system="ORACLE_FUSION"):
    return {
        "je_id": je_id,
        "amount": amount,
        "currency": "USD",
        "account": account,
        "gl_account_desc": "Operating Expense",
        "description": description,
        "preparer": preparer,
        "approver": approver,
        "posted_at": posted_at or _BASE,
        "period_close_date": None,
        "source_system": source_system,
    }


def _rule_ids(findings):
    return {f["rule_id"] for f in findings}


# ── round_dollar ───────────────────────────────────────────────────────────────

def test_round_dollar_flags_exact_multiple():
    je = _je(amount=5000.0)
    assert jt.round_dollar(je)["rule_id"] == "JE-ROUND-DOLLAR"


def test_round_dollar_ignores_non_round_amount():
    je = _je(amount=5001.37)
    assert jt.round_dollar(je) is None


# ── after_hours_or_weekend ───────────────────────────────────────────────────────

def test_weekend_posting_flagged():
    saturday = _BASE + timedelta(days=5)  # Monday base + 5 = Saturday
    je = _je(posted_at=saturday)
    finding = jt.after_hours_or_weekend(je)
    assert finding["rule_id"] == "JE-WEEKEND-POSTING"


def test_after_hours_weekday_posting_flagged():
    late_night = _BASE.replace(hour=23)
    je = _je(posted_at=late_night)
    finding = jt.after_hours_or_weekend(je)
    assert finding["rule_id"] == "JE-AFTER-HOURS"


def test_business_hours_weekday_posting_not_flagged():
    je = _je(posted_at=_BASE.replace(hour=10))
    assert jt.after_hours_or_weekend(je) is None


def test_unparseable_timestamp_does_not_raise():
    je = _je(posted_at="not-a-date")
    assert jt.after_hours_or_weekend(je) is None


# ── preparer_equals_approver ──────────────────────────────────────────────────

def test_sod_conflict_flagged_when_preparer_is_approver():
    je = _je(preparer="alice", approver="alice")
    finding = jt.preparer_equals_approver(je)
    assert finding["rule_id"] == "JE-SOD-PREPARER-APPROVER"


def test_sod_conflict_not_flagged_for_different_people():
    je = _je(preparer="alice", approver="bob")
    assert jt.preparer_equals_approver(je) is None


def test_sod_conflict_not_flagged_when_approver_missing():
    je = _je(preparer="alice", approver=None)
    assert jt.preparer_equals_approver(je) is None


# ── manual_je_over_threshold_unapproved ───────────────────────────────────────

def test_threshold_breach_unapproved_flagged():
    je = _je(amount=15000.0, approver=None)
    finding = jt.manual_je_over_threshold_unapproved(je)
    assert finding["rule_id"] == "JE-THRESHOLD-UNAPPROVED"


def test_threshold_breach_with_approver_not_flagged():
    je = _je(amount=15000.0, approver="bob")
    assert jt.manual_je_over_threshold_unapproved(je) is None


def test_below_threshold_unapproved_not_flagged():
    je = _je(amount=500.0, approver=None)
    assert jt.manual_je_over_threshold_unapproved(je) is None


# ── top_side_unapproved ───────────────────────────────────────────────────────

def test_top_side_proxy_flags_large_unapproved_entry():
    je = _je(amount=750_000.0, approver=None)
    finding = jt.top_side_unapproved(je)
    assert finding["rule_id"] == "JE-TOPSIDE-UNAPPROVED"


def test_top_side_proxy_not_flagged_when_approved():
    je = _je(amount=750_000.0, approver="cfo")
    assert jt.top_side_unapproved(je) is None


# ── rare_account_combination ──────────────────────────────────────────────────

def test_rare_account_flagged_in_large_population():
    common = [_je(je_id=f"JE-{i}", account="6100-OPEX") for i in range(19)]
    rare = _je(je_id="JE-RARE", account="9999-UNUSUAL")
    findings = jt.rare_account_combination(common + [rare])
    assert any(f["je_id"] == "JE-RARE" and f["rule_id"] == "JE-RARE-ACCOUNT" for f in findings)
    assert not any(f["je_id"] == "JE-0" for f in findings)


def test_rare_account_not_evaluated_below_minimum_population():
    jes = [_je(je_id=f"JE-{i}", account=f"ACCT-{i}") for i in range(5)]
    assert jt.rare_account_combination(jes) == []


# ── unusual_description ───────────────────────────────────────────────────────

def test_unusual_description_flagged_above_materiality_floor():
    common = [_je(je_id=f"JE-{i}", description="Standard monthly accrual", amount=100)
              for i in range(19)]
    unusual = _je(je_id="JE-UNIQUE", description="One-off write-off adjustment", amount=9000)
    findings = jt.unusual_description(common + [unusual])
    assert any(f["je_id"] == "JE-UNIQUE" for f in findings)


def test_unusual_description_not_flagged_below_materiality_floor():
    common = [_je(je_id=f"JE-{i}", description="Standard monthly accrual", amount=100)
              for i in range(19)]
    unusual = _je(je_id="JE-SMALL", description="One-off tiny note", amount=10)
    findings = jt.unusual_description(common + [unusual])
    assert not any(f["je_id"] == "JE-SMALL" for f in findings)


# ── je_velocity_spike ─────────────────────────────────────────────────────────

def test_velocity_spike_flags_outlier_day():
    jes = []
    for day in range(5):
        jes.append(_je(je_id=f"JE-baseline-{day}", preparer="alice",
                        posted_at=_BASE + timedelta(days=day)))
    spike_day = _BASE + timedelta(days=5)
    for i in range(15):
        jes.append(_je(je_id=f"JE-spike-{i}", preparer="alice", posted_at=spike_day))
    findings = jt.je_velocity_spike(jes)
    assert any(f["rule_id"] == "JE-VELOCITY-SPIKE" for f in findings)


def test_velocity_spike_not_flagged_with_insufficient_history():
    jes = [_je(je_id="JE-1", preparer="alice", posted_at=_BASE),
           _je(je_id="JE-2", preparer="alice", posted_at=_BASE + timedelta(days=1))]
    assert jt.je_velocity_spike(jes) == []


def test_velocity_spike_not_flagged_for_uniform_daily_volume():
    jes = [_je(je_id=f"JE-{d}", preparer="alice", posted_at=_BASE + timedelta(days=d))
           for d in range(5)]
    assert jt.je_velocity_spike(jes) == []


# ── run_je_tests ───────────────────────────────────────────────────────────────

def test_run_je_tests_aggregates_all_rules():
    je = _je(je_id="JE-BAD", amount=5000.0, preparer="alice", approver="alice",
              posted_at=_BASE + timedelta(days=5))  # round dollar + SoD + weekend
    findings = jt.run_je_tests([je])
    ids = _rule_ids(findings)
    assert "JE-ROUND-DOLLAR" in ids
    assert "JE-SOD-PREPARER-APPROVER" in ids
    assert "JE-WEEKEND-POSTING" in ids


def test_run_je_tests_clean_entry_produces_no_findings():
    je = _je(je_id="JE-CLEAN", amount=1234.56, preparer="alice", approver="bob",
              posted_at=_BASE.replace(hour=10))
    assert jt.run_je_tests([je]) == []


def test_run_je_tests_empty_input():
    assert jt.run_je_tests([]) == []
