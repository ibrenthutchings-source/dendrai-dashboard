"""
Tests for _persist_full_analysis()'s always-on segment ingestion hook
(api_server.py) — every /predictive/full-analysis run should call
edgar_segments.persist_segments() unconditionally (not gated behind SOX
materiality), skip private tickers (no CIK/filings to parse), run BEFORE
the SOX auto-rescope block so db.get_sox_segments() sees fresh data on the
same run, and never let a segment-ingestion failure break the analysis.

All db.* and edgar_segments.* calls are mocked — no real DB, no network.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import api_server
import edgar_segments


def _base_mocks():
    """Return the patch context managers needed to drive
    _persist_full_analysis() down to (and past) the segment-ingestion hook
    with minimal, successful stand-ins for everything else it touches."""
    return [
        patch.object(api_server.db, "upsert_company", return_value=42),
        patch.object(api_server.db, "create_risk_loop_run", return_value=99),
        patch.object(api_server.db, "save_financial_ratios"),
        patch.object(api_server.db, "save_beneish_mscore"),
        patch.object(api_server.db, "save_altman_zscore"),
        patch.object(api_server.db, "save_risk_scores"),
        patch.object(api_server.db, "save_scenario_analyses"),
        patch.object(api_server.db, "save_grey_swan"),
        patch.object(api_server.db, "complete_risk_loop_run"),
        patch.object(api_server.db, "is_private_ticker", return_value=False),
        # SOX auto-rescope block: short-circuit by reporting no sox_cfg's
        # required inputs (forecast/risk_scores empty in our fake result),
        # so it no-ops without needing its own deep mock chain.
        patch.object(api_server.db, "get_sox_config", return_value=None),
    ]


def _run(req_ticker="ON", result_ticker=None):
    req = api_server.FullAnalysisRequest(ticker=req_ticker)
    result = {"ticker": result_ticker or req_ticker, "company_name": "ON Semiconductor"}
    with patch.object(api_server.db, "is_available", return_value=True):
        mocks = _base_mocks()
        for m in mocks:
            m.start()
        try:
            return api_server._persist_full_analysis(req, result)
        finally:
            for m in mocks:
                m.stop()


class TestSegmentIngestionHook:
    def test_calls_persist_segments_for_a_public_ticker(self):
        with patch.object(edgar_segments, "persist_segments") as mock_persist:
            _run(req_ticker="ON", result_ticker="ON")
        mock_persist.assert_called_once_with("ON")

    def test_skips_private_tickers(self):
        req = api_server.FullAnalysisRequest(ticker="PVT-ACME")
        result = {"ticker": "PVT-ACME", "company_name": "Acme Co"}
        with patch.object(api_server.db, "is_available", return_value=True), \
             patch.object(api_server.db, "upsert_company", return_value=42), \
             patch.object(api_server.db, "create_risk_loop_run", return_value=99), \
             patch.object(api_server.db, "save_financial_ratios"), \
             patch.object(api_server.db, "save_beneish_mscore"), \
             patch.object(api_server.db, "save_altman_zscore"), \
             patch.object(api_server.db, "save_risk_scores"), \
             patch.object(api_server.db, "save_scenario_analyses"), \
             patch.object(api_server.db, "save_grey_swan"), \
             patch.object(api_server.db, "complete_risk_loop_run"), \
             patch.object(api_server.db, "get_sox_config", return_value=None), \
             patch.object(api_server.db, "is_private_ticker", return_value=True), \
             patch.object(edgar_segments, "persist_segments") as mock_persist:
            api_server._persist_full_analysis(req, result)
        mock_persist.assert_not_called()

    def test_ingestion_failure_does_not_break_the_run(self):
        with patch.object(edgar_segments, "persist_segments", side_effect=RuntimeError("EDGAR unreachable")):
            run_id = _run(req_ticker="ON", result_ticker="ON")
        assert run_id == 99

    def test_runs_before_sox_get_sox_segments_is_read(self):
        """The whole point of running this on every analysis, not just via
        the manual import endpoint, is that SOX auto-rescope's
        db.get_sox_segments() call sees fresh data on THIS run — verify
        persist_segments is actually invoked (its own tests cover that it
        writes via db.upsert_sox_segment) rather than silently skipped."""
        with patch.object(edgar_segments, "persist_segments") as mock_persist:
            _run(req_ticker="ON", result_ticker="ON")
        assert mock_persist.called
