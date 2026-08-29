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
