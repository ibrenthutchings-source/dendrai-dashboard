"""
Tests for segment_risk_tool.py's assess_segment_risks() — Risk Coverage
Cube Phase 3. Pure function over edgar_segments' own persist_segments()/
forecast_segments() return shapes; no network, no DB.
"""
from __future__ import annotations

import segment_risk_tool as srt


def _persist_result(members, segment_type="geography", period_end="2026-07-03", reconciled=True):
    return {
        "extracted": True, "ticker": "ON",
        "breakdowns": [{
            "segment_type": segment_type, "period_start": "2026-04-04", "period_end": period_end,
            "reconciled": reconciled, "members": members,
        }],
    }


def _member(name, revenue, revenue_pct):
    return {"segment_name": name, "raw_member": f"x:{name}", "revenue": revenue, "revenue_pct": revenue_pct}


def _forecast_result(entries):
    return {"extracted": True, "forecasts": entries, "skipped": []}


def _fc_entry(segment_type, segment_name, rev_growth_yoy, quarters_used=8):
    return {
        "segment_type": segment_type, "segment_name": segment_name,
        "revenue_m": 100.0, "rev_growth_yoy": rev_growth_yoy,
        "quarters_used": quarters_used, "forecast": [], "source": "filed",
    }


class TestNoInput:
    def test_unextracted_persist_result_yields_no_risks(self):
        assert srt.assess_segment_risks({"extracted": False, "breakdowns": []}) == []

    def test_empty_persist_result_yields_no_risks(self):
        assert srt.assess_segment_risks({}) == []

    def test_no_forecast_result_only_assesses_concentration(self):
        result = _persist_result([_member("US", 700.0, 70.0)])
        risks = srt.assess_segment_risks(result, forecast_result=None)
        assert len(risks) == 1
        assert risks[0]["category"] == "Segment Concentration"


class TestConcentrationRisk:
    def test_below_threshold_not_flagged(self):
        result = _persist_result([_member("US", 200.0, 20.0)])
        risks = srt.assess_segment_risks(result)
        assert not [r for r in risks if r["category"] == "Segment Concentration"]

    def test_at_or_above_threshold_flagged(self):
        result = _persist_result([_member("US", 700.0, 70.0)])
        risks = srt.assess_segment_risks(result)
        conc = [r for r in risks if r["category"] == "Segment Concentration"]
        assert len(conc) == 1
        assert conc[0]["segment_name"] == "US"
        assert conc[0]["segment_type"] == "geography"
        assert conc[0]["source_framework"] == "segment_risk"
        assert "70.0%" in conc[0]["narrative"]

    def test_score_scales_with_concentration_and_is_capped(self):
        low = srt.assess_segment_risks(_persist_result([_member("US", 260.0, 26.0)]))
        high = srt.assess_segment_risks(_persist_result([_member("US", 950.0, 95.0)]))
        assert low[0]["score"] < high[0]["score"]
        assert high[0]["score"] <= 25.0

    def test_risk_ref_is_within_varchar16(self):
        result = _persist_result([_member("US", 700.0, 70.0)])
        risks = srt.assess_segment_risks(result)
        assert len(risks[0]["risk_ref"]) <= 16


class TestDeclineRisk:
    def test_flagged_when_below_threshold(self):
        result = _persist_result([_member("APAC", 100.0, 15.0)])
        forecast = _forecast_result([_fc_entry("geography", "APAC", -18.0)])
        risks = srt.assess_segment_risks(result, forecast)
        decl = [r for r in risks if r["category"] == "Segment Decline"]
        assert len(decl) == 1
        assert "18.0%" in decl[0]["narrative"]

    def test_not_flagged_for_mild_decline(self):
        result = _persist_result([_member("APAC", 100.0, 15.0)])
        forecast = _forecast_result([_fc_entry("geography", "APAC", -3.0)])
        risks = srt.assess_segment_risks(result, forecast)
        assert not [r for r in risks if r["category"] == "Segment Decline"]

    def test_positive_growth_never_flagged(self):
        result = _persist_result([_member("APAC", 100.0, 15.0)])
        forecast = _forecast_result([_fc_entry("geography", "APAC", 12.0)])
        risks = srt.assess_segment_risks(result, forecast)
        assert not [r for r in risks if r["category"] == "Segment Decline"]


class TestDivergenceRisk:
    def test_flagged_when_segment_lags_consolidated_by_wide_margin(self):
        result = _persist_result([_member("EU", 100.0, 15.0)])
        forecast = _forecast_result([_fc_entry("geography", "EU", -5.0)])
        risks = srt.assess_segment_risks(result, forecast, consolidated_revenue_growth_pct=15.0)
        div = [r for r in risks if r["category"] == "Segment Divergence"]
        assert len(div) == 1
        assert "20.0-point gap" in div[0]["narrative"]

    def test_not_flagged_when_segment_tracks_consolidated(self):
        result = _persist_result([_member("EU", 100.0, 15.0)])
        forecast = _forecast_result([_fc_entry("geography", "EU", 8.0)])
        risks = srt.assess_segment_risks(result, forecast, consolidated_revenue_growth_pct=10.0)
        assert not [r for r in risks if r["category"] == "Segment Divergence"]

    def test_not_flagged_without_consolidated_growth_figure(self):
        result = _persist_result([_member("EU", 100.0, 15.0)])
        forecast = _forecast_result([_fc_entry("geography", "EU", -20.0)])
        risks = srt.assess_segment_risks(result, forecast, consolidated_revenue_growth_pct=None)
        assert not [r for r in risks if r["category"] == "Segment Divergence"]

    def test_segment_outperforming_consolidated_never_flagged(self):
        result = _persist_result([_member("EU", 100.0, 15.0)])
        forecast = _forecast_result([_fc_entry("geography", "EU", 25.0)])
        risks = srt.assess_segment_risks(result, forecast, consolidated_revenue_growth_pct=5.0)
        assert not [r for r in risks if r["category"] == "Segment Divergence"]


class TestUnreconciledAndMultipleMembers:
    def test_unreconciled_breakdown_yields_no_risks(self):
        result = _persist_result([_member("US", 700.0, 70.0)], reconciled=False)
        assert srt.assess_segment_risks(result) == []

    def test_multiple_members_each_assessed_independently(self):
        result = _persist_result([
            _member("US", 700.0, 70.0),   # concentration
            _member("EMEA", 300.0, 30.0),  # concentration too
        ])
        risks = srt.assess_segment_risks(result)
        names = {r["segment_name"] for r in risks if r["category"] == "Segment Concentration"}
        assert names == {"US", "EMEA"}
        refs = [r["risk_ref"] for r in risks]
        assert len(refs) == len(set(refs))  # distinct risk_refs per member


class TestRagThresholds:
    def test_high_score_is_red(self):
        result = _persist_result([_member("US", 990.0, 99.0)])
        risks = srt.assess_segment_risks(result)
        assert risks[0]["rag_status"] == "R"
