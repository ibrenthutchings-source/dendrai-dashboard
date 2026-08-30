"""
Tests for synthetic_transaction_tool.get_journal_entries's per-tick pacing.

Confirmed against real production data: this function's only real caller
(je_testing_sweep.py's recurring 30-minute poll) was getting a flat 200
fabricated journal entries every single tick, with no incremental/since
logic at all — one rule (JE-WEEKEND-POSTING) alone accumulated 19,000+
fired exceptions in a single 30-day window, producing a FAIR-estimated
impact in the tens of billions. Fixed to pace like every other synthetic
feed in this module: _CASES_PER_TICK (1-3) new entries per call.

    pytest test_synthetic_transaction_tool.py -v
"""

from __future__ import annotations

import datetime as dt
import random
from unittest.mock import patch

import synthetic_transaction_tool as stt


def _r2r_config():
    return {"process": "record_to_report"}


class TestGetJournalEntriesPacing:
    def test_generates_at_most_a_handful_per_call_not_a_flat_200(self):
        result = stt.get_journal_entries(None, {}, _r2r_config())
        assert result["count"] <= stt._CASES_PER_TICK[1]
        assert result["count"] >= 0

    def test_never_exceeds_cases_per_tick_across_many_calls(self):
        """The old bug was a hardcoded 200 every call; run enough calls that
        a regression back to a large flat count would be caught reliably."""
        counts = [stt.get_journal_entries(None, {}, _r2r_config())["count"] for _ in range(30)]
        assert all(c <= stt._CASES_PER_TICK[1] for c in counts)

    def test_max_items_still_caps_the_per_tick_count(self):
        """A future caller passing something below _CASES_PER_TICK's usual
        range must still be respected — max_items is a real cap, not
        decorative now that the baseline count is already small. Only
        _CASES_PER_TICK is patched (to a fixed, larger-than-max_items range)
        — the rng itself must stay real, since _build_own_case's fake-data
        generation depends on genuine random.Random behavior throughout."""
        with patch.object(stt, "_CASES_PER_TICK", (5, 5)):
            result = stt.get_journal_entries(None, {}, _r2r_config(), max_items=1)
        assert result["count"] <= 1

    def test_non_record_to_report_process_returns_empty_regardless(self):
        result = stt.get_journal_entries(None, {}, {"process": "order_to_cash"})
        assert result["count"] == 0
        assert result["journal_entries"] == []

    def test_returned_entries_have_the_shared_journal_entry_shape(self):
        result = stt.get_journal_entries(None, {}, _r2r_config())
        for je in result["journal_entries"]:
            assert je["source_system"] == "SYNTHETIC"
            assert je["currency"] == "USD"
            assert isinstance(je["amount"], float)
            assert je["posted_at"]  # ISO string, not a bare datetime


# ── violating cases actually reach mcp_governance._detect_system_flags() ────
# Confirmed against real production data: HIGH-severity synthetic violations
# (vendor offboarding, HCM termination, payment runs, ...) all had
# risk_flags=[] in system_telemetry, because the payload used this module's
# own vocabulary (e.g. "access_revoked": false) while the detector checks
# for a completely different, specific key (e.g.
# "terminated_employee_access_retained"). _VIOLATION_RATE is forced to 1.0
# throughout so every generated case is deterministically a violation.

class TestViolatingCasesAreFlagged:
    def test_hire_to_retire_pay_rate_violation_sets_its_specific_flag(self, monkeypatch):
        """_VIOLATION_RATE=1.0 guarantees exactly one step per case violates,
        but _build_own_case still returns an event for every step in the
        lifecycle — only the HIGH-severity one is the violating draw for
        that case; the other 4 are clean and correctly carry no flag."""
        monkeypatch.setattr(stt, "_VIOLATION_RATE", 1.0)
        rng = random.Random(1)
        found = False
        for _ in range(50):
            events = stt._build_own_case(stt._HIRE_TO_RETIRE, rng, dt.datetime.now(dt.timezone.utc), "hire_to_retire")
            for e in events:
                if e["raw_payload"].get("process_step") == "Pay Rate Change" and e["severity"] == "HIGH":
                    assert e["raw_payload"]["unauthorized_pay_rate_change"] is True
                    found = True
        assert found, "no Pay Rate Change violation drawn across 50 cases — widen the loop or fix the seed"

    def test_hire_to_retire_termination_violation_sets_its_specific_flag(self, monkeypatch):
        monkeypatch.setattr(stt, "_VIOLATION_RATE", 1.0)
        rng = random.Random(2)
        found = False
        for _ in range(50):
            events = stt._build_own_case(stt._HIRE_TO_RETIRE, rng, dt.datetime.now(dt.timezone.utc), "hire_to_retire")
            for e in events:
                if e["raw_payload"].get("process_step") == "Termination Processed" and e["severity"] == "HIGH":
                    assert e["raw_payload"]["terminated_employee_access_retained"] is True
                    found = True
        assert found, "no Termination Processed violation drawn across 50 cases — widen the loop or fix the seed"

    def test_process_with_no_dedicated_flag_falls_back_to_generic_policy_violation(self, monkeypatch):
        """fixed_assets has no process-specific flag defined anywhere in
        mcp_governance._detect_system_flags() — the generic fallback must
        still make every one of its violations Bronze-eligible."""
        monkeypatch.setattr(stt, "_VIOLATION_RATE", 1.0)
        rng = random.Random(3)
        events = stt._build_own_case(stt._FIXED_ASSETS, rng, dt.datetime.now(dt.timezone.utc), "fixed_assets")
        violating = [e for e in events if e["severity"] == "HIGH"]
        assert violating, "no violating step produced — _VIOLATION_RATE patch didn't take"
        for e in violating:
            assert e["raw_payload"]["policy_violation"] is True

    def test_clean_events_never_get_a_violation_flag(self, monkeypatch):
        """The fix must not inflate Bronze intake for benign activity —
        only actual violations should carry a flag key."""
        monkeypatch.setattr(stt, "_VIOLATION_RATE", 0.0)
        rng = random.Random(4)
        events = stt._build_own_case(stt._HIRE_TO_RETIRE, rng, dt.datetime.now(dt.timezone.utc), "hire_to_retire")
        assert all(e["severity"] == "INFO" for e in events)
        flag_keys = {"policy_violation", "unauthorized_pay_rate_change", "terminated_employee_access_retained", "sod_violation"}
        for e in events:
            assert not (flag_keys & e["raw_payload"].keys())

    def test_reused_txn_kind_violation_sets_its_declared_flag(self, monkeypatch):
        """Every one of the 13 O2C/P2P/Inventory TxnKinds declares a `flag`
        matching a real mcp_governance._detect_system_flags() key, but it
        was never threaded into the payload before this fix."""
        import generate_o2c_p2p_synthetic_log as gen
        monkeypatch.setattr(stt, "_VIOLATION_RATE", 1.0)
        steps = [(gen.REVENUE_KIND, "Revenue Recognized", (0, 5))]
        rng = random.Random(5)
        events = stt._build_reused_case(steps, rng, dt.datetime.now(dt.timezone.utc), "order_to_cash")
        assert events[0]["raw_payload"]["revenue_recognition_event"] is True

    def test_reused_txn_kind_clean_case_has_no_flag(self, monkeypatch):
        import generate_o2c_p2p_synthetic_log as gen
        monkeypatch.setattr(stt, "_VIOLATION_RATE", 0.0)
        steps = [(gen.REVENUE_KIND, "Revenue Recognized", (0, 5))]
        rng = random.Random(6)
        events = stt._build_reused_case(steps, rng, dt.datetime.now(dt.timezone.utc), "order_to_cash")
        assert "revenue_recognition_event" not in events[0]["raw_payload"]
