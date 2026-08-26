"""
Tests for edgar_segments.py's multi-filing history + forecasting path
(fetch_segment_history / forecast_segments) — Phase 2 of the Risk Coverage
Cube plan. A single filing's own comparatives (current Q, prior-year Q, YTD,
prior YTD) top out around 4 points, short of fit_arima's 8-observation
floor; these walk several 10-Qs to build a real quarterly run per segment.

All network calls (get_company_info, parse_filings, _find_instance_doc,
_fetch_instance_xml) and the XML parse step (extract_segments_from_xml) are
mocked — no live EDGAR access, matching test_edgar_segments.py's own
"exercised manually during development" convention for the end-to-end path.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import edgar_segments as es


def _breakdown(period_start, period_end, us_rev, emea_rev, reconciled=True):
    total = us_rev + emea_rev
    return {
        "segment_type": "geography", "period_start": period_start, "period_end": period_end,
        "consolidated_revenue": total, "reconciled": reconciled,
        "members": [
            {"segment_name": "US", "raw_member": "country:US", "revenue": us_rev,
             "revenue_pct": round(us_rev / total * 100, 2)},
            {"segment_name": "EMEA", "raw_member": "country:EMEA", "revenue": emea_rev,
             "revenue_pct": round(emea_rev / total * 100, 2)},
        ],
    }


class TestIsQuarterlyPeriod:
    def test_ninety_day_period_is_quarterly(self):
        assert es._is_quarterly_period("2026-04-04", "2026-07-03") is True

    def test_ytd_nine_month_period_is_not_quarterly(self):
        assert es._is_quarterly_period("2025-12-28", "2026-07-03") is False

    def test_annual_period_is_not_quarterly(self):
        assert es._is_quarterly_period("2025-01-01", "2025-12-31") is False

    def test_malformed_dates_return_false_not_error(self):
        assert es._is_quarterly_period("", "") is False


class TestFetchSegmentHistory:
    """Drives fetch_segment_history() through a synthetic multi-filing walk
    with every network/parse boundary mocked."""

    def _fake_filings(self, n):
        # Newest first, ~91 days apart, matching parse_filings' own shape.
        filings = []
        base_year, base_month = 2026, 7
        for i in range(n):
            month = base_month - 3 * i
            year = base_year + (month - 1) // 12 if month <= 0 else base_year
            month = ((month - 1) % 12) + 1
            filings.append({"date": f"{year}-{month:02d}-15", "accession_number": f"000-{i:03d}"})
        return {"10-Q": filings}

    def test_dedups_by_period_end_newest_filing_wins(self):
        """Filing N's 'current quarter' and filing N+4's 'prior-year
        comparative' can describe the same real quarter — the newest
        filing's own current-period figure must be the one kept."""
        filings = self._fake_filings(2)
        xml_by_accession = {"000-000": "xml-0", "000-001": "xml-1"}
        extracted_by_xml = {
            # Filing 0 (newest): current Q + prior-year Q comparative
            "xml-0": {"extracted": True, "breakdowns": [
                _breakdown("2026-04-04", "2026-07-03", 700.0, 300.0),
                _breakdown("2025-04-05", "2025-07-04", 600.0, 250.0),  # same real quarter as filing 1's "current"
            ]},
            # Filing 1 (older): its own current Q restated slightly differently
            "xml-1": {"extracted": True, "breakdowns": [
                _breakdown("2025-04-05", "2025-07-04", 590.0, 245.0),
            ]},
        }
        with patch.object(es, "get_company_info", return_value=({"cik_plain": "1"}, {})), \
             patch.object(es, "parse_filings", return_value=filings), \
             patch.object(es, "_find_instance_doc", side_effect=lambda cik, acc: xml_by_accession[acc]), \
             patch.object(es, "_fetch_instance_xml", side_effect=lambda cik, acc, doc: doc), \
             patch.object(es, "extract_segments_from_xml", side_effect=lambda xml: extracted_by_xml[xml]):
            result = es.fetch_segment_history("ON", max_filings=2)

        assert result["extracted"] is True
        us_points = result["series"][("geography", "US")]
        assert len(us_points) == 2
        # 2025-07-04 point must come from filing 0 (600.0), not filing 1 (590.0)
        p_2025 = next(p for p in us_points if p["period_end"] == "2025-07-04")
        assert p_2025["revenue"] == 600.0

    def test_ytd_breakdowns_excluded_from_history(self):
        filings = self._fake_filings(1)
        with patch.object(es, "get_company_info", return_value=({"cik_plain": "1"}, {})), \
             patch.object(es, "parse_filings", return_value=filings), \
             patch.object(es, "_find_instance_doc", return_value="doc"), \
             patch.object(es, "_fetch_instance_xml", return_value="xml"), \
             patch.object(es, "extract_segments_from_xml", return_value={"extracted": True, "breakdowns": [
                 _breakdown("2026-04-04", "2026-07-03", 700.0, 300.0),          # quarterly — kept
                 _breakdown("2025-12-28", "2026-07-03", 1400.0, 600.0),         # YTD — excluded
             ]}):
            result = es.fetch_segment_history("ON", max_filings=1)

        us_points = result["series"][("geography", "US")]
        assert len(us_points) == 1
        assert us_points[0]["revenue"] == 700.0

    def test_unreconciled_breakdowns_excluded(self):
        filings = self._fake_filings(1)
        with patch.object(es, "get_company_info", return_value=({"cik_plain": "1"}, {})), \
             patch.object(es, "parse_filings", return_value=filings), \
             patch.object(es, "_find_instance_doc", return_value="doc"), \
             patch.object(es, "_fetch_instance_xml", return_value="xml"), \
             patch.object(es, "extract_segments_from_xml", return_value={"extracted": True, "breakdowns": [
                 _breakdown("2026-04-04", "2026-07-03", 700.0, 300.0, reconciled=False),
             ]}):
            result = es.fetch_segment_history("ON", max_filings=1)

        assert result["extracted"] is False
        assert result["series"] == {}

    def test_no_filings_found_is_honest_not_an_error(self):
        with patch.object(es, "get_company_info", return_value=({"cik_plain": "1"}, {})), \
             patch.object(es, "parse_filings", return_value={}):
            result = es.fetch_segment_history("NOFILE", max_filings=5)
        assert result["extracted"] is False
        assert "No" in result["reason"]


class TestForecastSegments:
    def _history_with_n_quarters(self, n, base_revenue=700.0, step=10.0):
        points = [{"period_end": f"2020-{(i % 12) + 1:02d}-01", "revenue": base_revenue + step * i} for i in range(n)]
        return {"extracted": True, "ticker": "ON", "series": {("geography", "US"): points}, "filings_used": n}

    def test_segment_with_enough_history_gets_forecast_and_persisted(self):
        fake_ensemble = {"forecasts": [{"horizon": 1, "point": 900.0, "ci_lower": 800.0, "ci_upper": 1000.0, "per_model": {}}]}
        with patch.object(es, "fetch_segment_history", return_value=self._history_with_n_quarters(8)), \
             patch("predictive_analytics_tool.compute_ensemble_forecast", return_value=fake_ensemble) as mock_ens, \
             patch("db.save_segment_forecasts") as mock_save:
            result = es.forecast_segments("ON", run_id=123)

        assert result["extracted"] is True
        assert len(result["forecasts"]) == 1
        assert result["forecasts"][0]["segment_name"] == "US"
        assert result["forecasts"][0]["forecast"] == fake_ensemble["forecasts"]
        assert not result["skipped"]
        mock_ens.assert_called_once()
        mock_save.assert_called_once()
        saved_rows = mock_save.call_args.args[1]
        assert saved_rows[0]["segment_name"] == "US"
        assert saved_rows[0]["source"] == "filed"

    def test_segment_with_insufficient_history_is_skipped_not_forced(self):
        with patch.object(es, "fetch_segment_history", return_value=self._history_with_n_quarters(3)), \
             patch("predictive_analytics_tool.compute_ensemble_forecast") as mock_ens, \
             patch("db.save_segment_forecasts") as mock_save:
            result = es.forecast_segments("ON", run_id=123)

        assert result["forecasts"] == []
        assert len(result["skipped"]) == 1
        assert "3" in result["skipped"][0]["reason"]
        mock_ens.assert_not_called()
        mock_save.assert_not_called()

    def test_no_extracted_history_returns_honest_reason(self):
        with patch.object(es, "fetch_segment_history", return_value={
            "extracted": False, "series": {}, "filings_used": 0, "reason": "No 10-Q filings found",
        }):
            result = es.forecast_segments("ON")
        assert result["extracted"] is False
        assert result["reason"] == "No 10-Q filings found"

    def test_rev_growth_yoy_uses_same_quarter_year_ago_not_prior_point(self):
        # 8 points, quarterly step of +10 each; point[-5] is 4 quarters back from point[-1].
        history = self._history_with_n_quarters(8, base_revenue=700.0, step=10.0)
        fake_ensemble = {"forecasts": []}
        with patch.object(es, "fetch_segment_history", return_value=history), \
             patch("predictive_analytics_tool.compute_ensemble_forecast", return_value=fake_ensemble), \
             patch("db.save_segment_forecasts"):
            result = es.forecast_segments("ON", run_id=1)

        points = history["series"][("geography", "US")]
        expected = round((points[-1]["revenue"] - points[-5]["revenue"]) / points[-5]["revenue"] * 100, 2)
        assert result["forecasts"][0]["rev_growth_yoy"] == expected

    def test_persistence_failure_does_not_break_the_forecast_result(self):
        with patch.object(es, "fetch_segment_history", return_value=self._history_with_n_quarters(8)), \
             patch("predictive_analytics_tool.compute_ensemble_forecast", return_value={"forecasts": []}), \
             patch("db.save_segment_forecasts", side_effect=RuntimeError("db down")):
            result = es.forecast_segments("ON", run_id=1)
        assert len(result["forecasts"]) == 1
