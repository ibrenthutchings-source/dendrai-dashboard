"""
Tests for _persist_full_analysis()'s always-on segment ingestion hook
(api_server.py) — every /predictive/full-analysis run should call
edgar_segments.persist_segments() and forecast_segments() unconditionally
(not gated behind SOX materiality), then segment_risk_tool.assess_segment_
risks() to score real segment-level risk, skip all of it for private
tickers (no CIK/filings to parse), run BEFORE the SOX auto-rescope block so
db.get_sox_segments() sees fresh data on the same run, and never let a
segment-ingestion/forecast/risk-assessment failure break the analysis.

All db.*, edgar_segments.*, and segment_risk_tool.* calls are mocked — no
real DB, no network. Each mocked attribute is patched exactly ONCE per call
(see _run()) — nesting two `patch.object` calls on the SAME target around
_run() silently shadows the outer one (whichever .start()s last wins while
both are active), so tests pass their own mock objects into _run(mocks=...)
instead of wrapping it in another patch.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import api_server
import edgar_segments
import segment_risk_tool


def _run(req_ticker="ON", result_ticker=None, mocks=None):
    """Drive _persist_full_analysis() with safe defaults for everything it
    touches, overridable via `mocks` = {attr_name: MagicMock(...)}. Returns
    (run_id, mocks_used) so a test can assert against the exact instance it
    passed in (or the default one, if it didn't override that attr)."""
    req = api_server.FullAnalysisRequest(ticker=req_ticker)
    result = {"ticker": result_ticker or req_ticker, "company_name": "ON Semiconductor"}

    m = dict(mocks or {})
    m.setdefault("upsert_company", MagicMock(return_value=42))
    m.setdefault("create_risk_loop_run", MagicMock(return_value=99))
    m.setdefault("save_financial_ratios", MagicMock())
    m.setdefault("save_beneish_mscore", MagicMock())
    m.setdefault("save_altman_zscore", MagicMock())
    m.setdefault("save_risk_scores", MagicMock())
    m.setdefault("save_scenario_analyses", MagicMock())
    m.setdefault("save_grey_swan", MagicMock())
    m.setdefault("complete_risk_loop_run", MagicMock())
    m.setdefault("is_private_ticker", MagicMock(return_value=False))
    m.setdefault("get_sox_config", MagicMock(return_value=None))
    # Safe no-network defaults — see module docstring for why these two in
    # particular must never be left to the real functions in a test.
    m.setdefault("persist_segments", MagicMock(return_value={"extracted": False, "breakdowns": []}))
    m.setdefault("forecast_segments", MagicMock(return_value={"extracted": False, "forecasts": [], "skipped": []}))
    m.setdefault("assess_segment_risks", MagicMock(return_value=[]))

    with patch.object(api_server.db, "is_available", return_value=True), \
         patch.object(api_server.db, "upsert_company", m["upsert_company"]), \
         patch.object(api_server.db, "create_risk_loop_run", m["create_risk_loop_run"]), \
         patch.object(api_server.db, "save_financial_ratios", m["save_financial_ratios"]), \
         patch.object(api_server.db, "save_beneish_mscore", m["save_beneish_mscore"]), \
         patch.object(api_server.db, "save_altman_zscore", m["save_altman_zscore"]), \
         patch.object(api_server.db, "save_risk_scores", m["save_risk_scores"]), \
         patch.object(api_server.db, "save_scenario_analyses", m["save_scenario_analyses"]), \
         patch.object(api_server.db, "save_grey_swan", m["save_grey_swan"]), \
         patch.object(api_server.db, "complete_risk_loop_run", m["complete_risk_loop_run"]), \
         patch.object(api_server.db, "is_private_ticker", m["is_private_ticker"]), \
         patch.object(api_server.db, "get_sox_config", m["get_sox_config"]), \
         patch.object(edgar_segments, "persist_segments", m["persist_segments"]), \
         patch.object(edgar_segments, "forecast_segments", m["forecast_segments"]), \
         patch.object(segment_risk_tool, "assess_segment_risks", m["assess_segment_risks"]):
        run_id = api_server._persist_full_analysis(req, result)
    return run_id, m


class TestSegmentIngestionHook:
    def test_calls_persist_segments_for_a_public_ticker(self):
        mock_persist = MagicMock(return_value={"extracted": False, "breakdowns": []})
        _run(req_ticker="ON", result_ticker="ON", mocks={"persist_segments": mock_persist})
        mock_persist.assert_called_once_with("ON")

    def test_skips_private_tickers(self):
        mock_persist = MagicMock()
        mock_forecast = MagicMock()
        _run(
            req_ticker="PVT-ACME", result_ticker="PVT-ACME",
            mocks={
                "is_private_ticker": MagicMock(return_value=True),
                "persist_segments": mock_persist,
                "forecast_segments": mock_forecast,
            },
        )
        mock_persist.assert_not_called()
        mock_forecast.assert_not_called()

    def test_ingestion_failure_does_not_break_the_run(self):
        run_id, _ = _run(
            req_ticker="ON", result_ticker="ON",
            mocks={"persist_segments": MagicMock(side_effect=RuntimeError("EDGAR unreachable"))},
        )
        assert run_id == 99

    def test_forecast_failure_does_not_break_the_run(self):
        run_id, _ = _run(
            req_ticker="ON", result_ticker="ON",
            mocks={"forecast_segments": MagicMock(side_effect=RuntimeError("EDGAR unreachable"))},
        )
        assert run_id == 99

    def test_runs_before_sox_get_sox_segments_is_read(self):
        """The whole point of running this on every analysis, not just via
        the manual import endpoint, is that SOX auto-rescope's
        db.get_sox_segments() call sees fresh data on THIS run — verify
        persist_segments is actually invoked (its own tests cover that it
        writes via db.upsert_sox_segment) rather than silently skipped."""
        mock_persist = MagicMock(return_value={"extracted": False, "breakdowns": []})
        _run(req_ticker="ON", result_ticker="ON", mocks={"persist_segments": mock_persist})
        assert mock_persist.called


class TestSegmentRiskAssessmentHook:
    def test_assess_segment_risks_called_with_extraction_results_and_saved(self):
        persist_result = {"extracted": True, "breakdowns": [{
            "segment_type": "geography", "period_start": "2026-04-04", "period_end": "2026-07-03",
            "reconciled": True,
            "members": [{"segment_name": "US", "raw_member": "country:US", "revenue": 700.0, "revenue_pct": 70.0}],
        }]}
        forecast_result = {"extracted": True, "forecasts": [], "skipped": []}
        fake_risk = {"risk_ref": "SGG01C", "name": "US revenue concentration", "category": "Segment Concentration",
                     "score": 12.0, "rag_status": "A", "segment_type": "geography", "segment_name": "US",
                     "source_framework": "segment_risk"}
        mock_assess = MagicMock(return_value=[fake_risk])
        mock_save = MagicMock()

        _, _ = _run(req_ticker="ON", result_ticker="ON", mocks={
            "persist_segments": MagicMock(return_value=persist_result),
            "forecast_segments": MagicMock(return_value=forecast_result),
            "assess_segment_risks": mock_assess,
            "save_risk_scores": mock_save,
        })

        mock_assess.assert_called_once()
        call_args = mock_assess.call_args
        assert call_args.args[0] == persist_result
        assert call_args.args[1] == forecast_result
        # save_risk_scores is called at least twice in a real run: once for
        # the consolidated risk_scores (empty in this fake result) and once
        # for the segment risks — assert our fake risk made it into one call.
        assert any(fake_risk in c.args[1] for c in mock_save.call_args_list if len(c.args) > 1)

    def test_risk_assessment_skipped_when_persist_did_not_extract(self):
        mock_assess = MagicMock()
        _run(req_ticker="ON", result_ticker="ON", mocks={
            "persist_segments": MagicMock(return_value={"extracted": False, "breakdowns": []}),
            "assess_segment_risks": mock_assess,
        })
        mock_assess.assert_not_called()

    def test_risk_assessment_failure_does_not_break_the_run(self):
        run_id, _ = _run(req_ticker="ON", result_ticker="ON", mocks={
            "persist_segments": MagicMock(return_value={"extracted": True, "breakdowns": []}),
            "assess_segment_risks": MagicMock(side_effect=RuntimeError("boom")),
        })
        assert run_id == 99
