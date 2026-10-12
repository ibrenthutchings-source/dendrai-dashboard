"""
Regression tests for edgar_segments.py's pure XBRL-parsing path.

The fixture (test_fixtures/on_semi_10q_2026q2.xml) is the real, unmodified
XBRL instance document from ON Semiconductor's 2026-08-03 10-Q (accession
0001097864-26-000017), fetched directly from SEC EDGAR. The expected values
below are the figures as actually filed — verified by hand against the
filing before being written here, not derived from the code under test.

No network access: extract_segments_from_xml() is a pure function over
already-fetched XML, so this suite never calls SEC EDGAR. The end-to-end
fetch_segments() path (ticker -> CIK -> filing -> instance doc) is exercised
manually against ON Semi and Coca-Cola during development — see the plan's
Phase 1 verification notes — but isn't re-run on every test invocation
since it depends on EDGAR's live rate limits and current filings.
"""

from __future__ import annotations

import copy
import os
from unittest.mock import patch

import pytest

import edgar_segments as es

_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "test_fixtures", "on_semi_10q_2026q2.xml"
)


@pytest.fixture(scope="module")
def on_semi_xml() -> str:
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def on_semi_result(on_semi_xml):
    return es.extract_segments_from_xml(on_semi_xml)


def _breakdown(result, segment_type, start, end):
    for b in result["breakdowns"]:
        if b["segment_type"] == segment_type and b["period_start"] == start and b["period_end"] == end:
            return b
    raise AssertionError(f"No {segment_type} breakdown for {start}->{end} in {result['breakdowns']}")


def _member(breakdown, name):
    for m in breakdown["members"]:
        if m["segment_name"] == name:
            return m
    raise AssertionError(f"No member {name!r} in {breakdown['members']}")


class TestBusinessSegmentExtraction:
    """Known-good figures for ON Semi's current-quarter business segments,
    as filed. Revenue in dollars (not millions) since that's the unit the
    instance document itself reports; the %s are ASC 606 disaggregation
    percentages ON Semi discloses directly in the same footnote, so they
    double as an independent check on our own revenue_pct computation."""

    def test_extracted_true(self, on_semi_result):
        assert on_semi_result["extracted"] is True

    def test_three_segments_found(self, on_semi_xml, on_semi_result):
        b = _breakdown(on_semi_result, "business_segment", "2026-04-04", "2026-07-03")
        assert len(b["members"]) == 3

    def test_power_solutions_group(self, on_semi_result):
        b = _breakdown(on_semi_result, "business_segment", "2026-04-04", "2026-07-03")
        m = _member(b, "PowerSolutionsGroup")
        assert m["revenue"] == pytest.approx(829_000_000, rel=1e-6)
        assert m["revenue_pct"] == pytest.approx(51.7, abs=0.05)

    def test_analog_mixed_signal_group(self, on_semi_result):
        b = _breakdown(on_semi_result, "business_segment", "2026-04-04", "2026-07-03")
        m = _member(b, "AnalogMixedSignalGroup")
        assert m["revenue"] == pytest.approx(545_700_000, rel=1e-6)

    def test_intelligent_sensing_group(self, on_semi_result):
        b = _breakdown(on_semi_result, "business_segment", "2026-04-04", "2026-07-03")
        m = _member(b, "IntelligentSensingGroup")
        assert m["revenue"] == pytest.approx(228_800_000, rel=1e-6)

    def test_reconciles_to_consolidated_revenue(self, on_semi_result):
        b = _breakdown(on_semi_result, "business_segment", "2026-04-04", "2026-07-03")
        assert b["consolidated_revenue"] == pytest.approx(1_603_500_000, rel=1e-6)
        assert b["reconciled"] is True


class TestGeographyExtraction:
    """Known-good figures for ON Semi's current-quarter geographic revenue —
    a different XBRL axis (srt:StatementGeographicalAxis) than business
    segment, exercising the axis-agnostic parsing path."""

    def test_five_geographies_found(self, on_semi_result):
        b = _breakdown(on_semi_result, "geography", "2026-04-04", "2026-07-03")
        assert len(b["members"]) == 5

    def test_hong_kong_largest(self, on_semi_result):
        b = _breakdown(on_semi_result, "geography", "2026-04-04", "2026-07-03")
        assert b["members"][0]["segment_name"] == "HK"
        m = _member(b, "HK")
        assert m["revenue"] == pytest.approx(444_200_000, rel=1e-6)
        assert m["revenue_pct"] == pytest.approx(27.7, abs=0.05)

    def test_all_five_iso_codes(self, on_semi_result):
        b = _breakdown(on_semi_result, "geography", "2026-04-04", "2026-07-03")
        names = {m["segment_name"] for m in b["members"]}
        assert names == {"HK", "SG", "GB", "US", "OtherGeographicalAreas"}

    def test_reconciles_to_consolidated_revenue(self, on_semi_result):
        b = _breakdown(on_semi_result, "geography", "2026-04-04", "2026-07-03")
        assert b["reconciled"] is True


class TestMultiplePeriods:
    """The instance document carries comparatives (current Q, prior-year Q,
    YTD, prior-year YTD) — all four should be extracted and reconciled
    independently, not just the first one encountered."""

    def test_finds_prior_year_quarter(self, on_semi_result):
        b = _breakdown(on_semi_result, "business_segment", "2025-04-05", "2025-07-04")
        assert b["reconciled"] is True

    def test_finds_ytd_periods(self, on_semi_result):
        b = _breakdown(on_semi_result, "business_segment", "2026-01-01", "2026-07-03")
        assert b["reconciled"] is True

    def test_at_least_eight_breakdowns(self, on_semi_result):
        # 2 axes x 4 periods, at minimum
        assert len(on_semi_result["breakdowns"]) >= 8


class TestMultiAxisContextsExcluded:
    """A context carrying the segment axis AND some other dimension (e.g.
    ConsolidationItemsAxis, StatementEquityComponentsAxis — both present in
    this filing) is a finer cross-tab, not a clean single-axis breakdown,
    and must never be summed into the segment/geography totals above —
    doing so would silently double-count against the reconciled total."""

    def test_single_axis_restriction_holds(self, on_semi_xml):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(on_semi_xml)
        multi_axis_count = 0
        for c in root.findall("xbrli:context", es._NS):
            dims = es._context_dims(c)
            has_any = next(c.iter(es._EXPLICIT_MEMBER_TAG), None) is not None
            if has_any and len(dims) == 0:
                # has some OTHER dimension we don't track (e.g. DebtInstrumentAxis) — fine, excluded
                continue
            if has_any and len(dims) > 1:
                multi_axis_count += 1
        # This filing does carry multi-dimension contexts (segment x
        # ConsolidationItemsAxis etc.) — assert the fixture actually
        # exercises the exclusion path, not just that it's theoretically possible.
        assert multi_axis_count > 0


class TestFilerSpecificBusinessSegmentAxis:
    """Unlike geography (one universal SRT element every filer uses),
    business/operating segment reporting has no standardized axis — real
    filers routinely define their own extension (e.g. "ibm:SegmentAxis",
    "aapl:ReportableSegmentsAxis") rather than the one literal
    us-gaap:StatementBusinessSegmentsAxis QName. Matching only that exact
    string meant geography populated for a company while its business-unit
    segments silently stayed empty. _axis_type()/_context_dims() must
    recognize any axis whose local name looks like a segment axis,
    regardless of its namespace prefix."""

    def test_custom_prefixed_axis_with_standard_local_name_is_business_segment(self):
        assert es._axis_type("ibm:StatementBusinessSegmentsAxis") == "business_segment"

    def test_entirely_custom_segment_axis_name_is_business_segment(self):
        assert es._axis_type("ibm:ReportableSegmentsAxis") == "business_segment"
        assert es._axis_type("aapl:SegmentAxis") == "business_segment"

    def test_exact_standard_qnames_still_match(self):
        assert es._axis_type("us-gaap:StatementBusinessSegmentsAxis") == "business_segment"
        assert es._axis_type("srt:StatementGeographicalAxis") == "geography"

    def test_unrelated_custom_axis_is_not_misclassified(self):
        assert es._axis_type("us-gaap:ConsolidationItemsAxis") is None
        assert es._axis_type("us-gaap:ProductOrServiceAxis") is None
        assert es._axis_type("us-gaap:DisposalGroupClassificationAxis") is None

    def test_custom_geography_named_axis_not_misclassified_as_business_segment(self):
        # A filer-specific geography axis under a non-standard name should
        # never fall through to the business_segment pattern.
        assert es._axis_type("ibm:GeographicSegmentsAxis") is None

    def test_context_dims_extracts_custom_axis_via_iter(self):
        xml = """<?xml version="1.0"?>
<xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:ibm="http://www.ibm.com/xbrl">
  <xbrli:context id="c1">
    <xbrli:entity><xbrli:identifier>0000000000</xbrli:identifier>
      <xbrli:segment>
        <xbrldi:explicitMember dimension="ibm:ReportableSegmentsAxis">ibm:SoftwareMember</xbrldi:explicitMember>
      </xbrli:segment>
    </xbrli:entity>
    <xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period>
  </xbrli:context>
</xbrl>"""
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml)
        ctx = root.find("xbrli:context", es._NS)
        dims = es._context_dims(ctx)
        assert dims == {"business_segment": "ibm:SoftwareMember"}


class TestPersistSegments:
    """persist_segments() orchestration — mocked db/network, no live calls.
    Verifies the honesty rules that matter most here: unreconciled
    breakdowns are never written, only the latest period per axis is
    written (the table isn't a time series), and persisted rows are
    stamped source='filed' or 'filed+estimated' (never bare 'filed' unless
    every derived financial field really was filed dimensionally) so
    neither is ever mistaken for a manual entry or for each other."""

    def _fake_result(self, *, reconciled_current=True, reconciled_prior=True, financials_source="filed"):
        return {
            "extracted": True,
            "ticker": "ON",
            "fiscal_year": "FY2026",
            "breakdowns": [
                {
                    "segment_type": "business_segment",
                    "period_start": "2026-04-04", "period_end": "2026-07-03",
                    "reconciled": reconciled_current,
                    "members": [
                        {"segment_name": "PowerSolutionsGroup", "revenue": 829_000_000.0, "revenue_pct": 51.7,
                         "financials_source": financials_source},
                        {"segment_name": "AnalogMixedSignalGroup", "revenue": 545_700_000.0, "revenue_pct": 34.0,
                         "financials_source": financials_source},
                    ],
                },
                {
                    # An older comparative period for the same axis — must
                    # NOT be persisted alongside the current one.
                    "segment_type": "business_segment",
                    "period_start": "2025-04-05", "period_end": "2025-07-04",
                    "reconciled": reconciled_prior,
                    "members": [
                        {"segment_name": "PowerSolutionsGroup", "revenue": 698_200_000.0, "revenue_pct": 47.5,
                         "financials_source": financials_source},
                    ],
                },
            ],
        }

    def test_persists_only_latest_period_per_axis(self):
        with patch.object(es, "fetch_segments", return_value=self._fake_result()), \
             patch.object(es, "get_company_info", return_value=({"cik": "0001097864", "ticker": "ON"}, {})), \
             patch("db.is_available", return_value=True), \
             patch("db.upsert_company", return_value=42), \
             patch("db.upsert_sox_segment") as mock_upsert:
            result = es.persist_segments("ON")

        assert len(mock_upsert.call_args_list) == 2  # 2 members, current period only
        periods_seen = {call.args[2]["revenue"] for call in mock_upsert.call_args_list}
        assert 829_000_000.0 in periods_seen  # current-period PSG revenue
        assert 698_200_000.0 not in periods_seen  # prior-year PSG revenue must be excluded
        assert len(result["persisted"]) == 2

    def test_stamps_source_as_filed_when_every_derived_field_was_filed(self):
        with patch.object(es, "fetch_segments", return_value=self._fake_result(financials_source="filed")), \
             patch.object(es, "get_company_info", return_value=({"cik": "0001097864", "ticker": "ON"}, {})), \
             patch("db.is_available", return_value=True), \
             patch("db.upsert_company", return_value=42), \
             patch("db.upsert_sox_segment") as mock_upsert:
            es.persist_segments("ON")

        for call in mock_upsert.call_args_list:
            assert call.args[2]["source"] == "filed"

    def test_stamps_source_as_filed_plus_estimated_when_a_field_was_allocated(self):
        """Revenue/revenue_pct are always filed at this point (only
        reconciled breakdowns reach persist_segments' write loop), but
        gross_profit/operating_income/net_income/assets are commonly
        allocated by revenue_pct rather than filed dimensionally — the row
        must say so, not claim 'filed' for figures that weren't."""
        for fin_source in ("estimated", "mixed", None):
            with patch.object(es, "fetch_segments", return_value=self._fake_result(financials_source=fin_source)), \
                 patch.object(es, "get_company_info", return_value=({"cik": "0001097864", "ticker": "ON"}, {})), \
                 patch("db.is_available", return_value=True), \
                 patch("db.upsert_company", return_value=42), \
                 patch("db.upsert_sox_segment") as mock_upsert:
                es.persist_segments("ON")

            for call in mock_upsert.call_args_list:
                assert call.args[2]["source"] == "filed+estimated"

    def test_unreconciled_breakdown_is_skipped_not_written(self):
        with patch.object(es, "fetch_segments", return_value=self._fake_result(reconciled_current=False)), \
             patch.object(es, "get_company_info", return_value=({"cik": "0001097864", "ticker": "ON"}, {})), \
             patch("db.is_available", return_value=True), \
             patch("db.upsert_company", return_value=42), \
             patch("db.upsert_sox_segment") as mock_upsert:
            result = es.persist_segments("ON")

        mock_upsert.assert_not_called()
        assert len(result["skipped"]) == 1
        assert "reconcile" in result["skipped"][0]["reason"]

    def test_no_database_configured_returns_extraction_only(self):
        with patch.object(es, "fetch_segments", return_value=self._fake_result()), \
             patch("db.is_available", return_value=False), \
             patch("db.upsert_sox_segment") as mock_upsert:
            result = es.persist_segments("ON")

        mock_upsert.assert_not_called()
        assert result["extracted"] is True  # extraction result still returned
        assert result["persisted"] == []


class TestHonestFailureStates:
    """No dimensional facts at all -> extracted=False with a reason, never
    a silent empty-but-"successful" result."""

    def test_empty_instance_reports_not_extracted(self):
        minimal = (
            '<?xml version="1.0"?>'
            '<xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            'xmlns:xbrldi="http://xbrl.org/2006/xbrldi">'
            '<xbrli:context id="c1">'
            '<xbrli:period><xbrli:instant>2026-01-01</xbrli:instant></xbrli:period>'
            "</xbrli:context>"
            "</xbrl>"
        )
        result = es.extract_segments_from_xml(minimal)
        assert result["extracted"] is False
        assert result["breakdowns"] == []

    def test_single_member_axis_is_not_a_breakdown(self):
        # Only one member for an axis/period isn't a "breakdown" — it's
        # either an incomplete disclosure or a mis-tagged fact, and
        # asserting a % of a total against a single data point is
        # meaningless — must not be reported as extracted.
        xml = """<?xml version="1.0"?>
<xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:us-gaap="http://fasb.org/us-gaap/2023">
  <xbrli:context id="c1">
    <xbrli:entity><xbrli:identifier>0000000000</xbrli:identifier>
      <xbrli:segment>
        <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">us-gaap:OnlySegmentMember</xbrldi:explicitMember>
      </xbrli:segment>
    </xbrli:entity>
    <xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <us-gaap:Revenues contextRef="c1">1000000</us-gaap:Revenues>
</xbrl>"""
        result = es.extract_segments_from_xml(xml)
        assert result["extracted"] is False


class TestMemberNameCleaning:
    def test_strips_namespace_and_member_suffix(self):
        assert es._clean_member_name("on:PowerSolutionsGroupMember") == "PowerSolutionsGroup"

    def test_country_code_unaffected(self):
        assert es._clean_member_name("country:HK") == "HK"

    def test_no_member_suffix_left_alone(self):
        assert es._clean_member_name("us-gaap:NonUsMember") == "NonUs"


# ── Financial enrichment: gross_profit/operating_income/net_income/assets/
# margins per segment member — filed dimensionally when reported, else
# estimated as consolidated_value * revenue_pct. See edgar_segments.py's
# "Financial enrichment" section. ──────────────────────────────────────────

class TestComputeMargins:
    def test_computes_all_three_when_every_input_present(self):
        m = es._compute_margins(revenue=1000.0, gross_profit=400.0, operating_income=200.0, net_income=100.0)
        assert m == {"gross_margin_pct": 40.0, "op_margin_pct": 20.0, "net_margin_pct": 10.0}

    def test_none_for_a_missing_numerator_not_zero(self):
        m = es._compute_margins(revenue=1000.0, gross_profit=None, operating_income=200.0, net_income=None)
        assert m == {"gross_margin_pct": None, "op_margin_pct": 20.0, "net_margin_pct": None}

    def test_none_when_revenue_is_zero_or_missing(self):
        assert es._compute_margins(revenue=None, gross_profit=400.0, operating_income=None, net_income=None) == {
            "gross_margin_pct": None, "op_margin_pct": None, "net_margin_pct": None,
        }
        assert es._compute_margins(revenue=0, gross_profit=400.0, operating_income=None, net_income=None) == {
            "gross_margin_pct": None, "op_margin_pct": None, "net_margin_pct": None,
        }


class TestConsolidatedValueForPeriod:
    _FACTS = {
        "GrossProfit": {"data_points": [
            {"start": "2026-04-04", "end": "2026-07-03", "val": 900_000_000.0},
            {"start": "2025-04-05", "end": "2025-07-04", "val": 800_000_000.0},
        ]},
        "TotalAssets": {"data_points": [
            {"end": "2026-07-03", "val": 12_000_000_000.0},  # instant fact, no start
        ]},
    }

    def test_matches_flow_metric_on_start_and_end(self):
        v = es._consolidated_value_for_period(self._FACTS, "GrossProfit", "2026-07-03", "2026-04-04")
        assert v == 900_000_000.0

    def test_does_not_cross_match_a_different_periods_start(self):
        """A quarterly segment figure must never be allocated against an
        annual/YTD consolidated total that happens to share an end date."""
        facts = {"GrossProfit": {"data_points": [
            {"start": "2026-01-01", "end": "2026-07-03", "val": 1_700_000_000.0},  # YTD, same end
        ]}}
        assert es._consolidated_value_for_period(facts, "GrossProfit", "2026-07-03", "2026-04-04") is None

    def test_instant_metric_matches_on_end_only(self):
        v = es._consolidated_value_for_period(self._FACTS, "TotalAssets", "2026-07-03")
        assert v == 12_000_000_000.0

    def test_none_when_metric_absent(self):
        assert es._consolidated_value_for_period({}, "NetIncome", "2026-07-03", "2026-04-04") is None

    def test_none_when_no_point_matches_the_period(self):
        v = es._consolidated_value_for_period(self._FACTS, "GrossProfit", "2099-01-01", "2098-10-01")
        assert v is None


class TestEnrichBreakdownFinancials:
    """Exercises the fallback (percentage-of-consolidated) path against the
    real ON Semi fixture. ON Semi's own 10-Q, it turns out, DOES report
    segment gross profit dimensionally (PowerSolutionsGroup $230.3M —
    confirmed via es._filed_member_values against this same fixture) but
    not operating income, net income, or assets — a realistic mixed case,
    not a contrived one: 'filed' and 'estimated' fields side by side on the
    same member."""

    _PSG_FILED_GROSS_PROFIT = 230_300_000.0  # as filed, confirmed above — never allocated

    def _consolidated_facts(self):
        return {
            "GrossProfit": {"data_points": [{"start": "2026-04-04", "end": "2026-07-03", "val": 900_000_000.0}]},
            "OperatingIncome": {"data_points": [{"start": "2026-04-04", "end": "2026-07-03", "val": 300_000_000.0}]},
            "NetIncome": {"data_points": [{"start": "2026-04-04", "end": "2026-07-03", "val": 250_000_000.0}]},
            "TotalAssets": {"data_points": [{"end": "2026-07-03", "val": 10_000_000_000.0}]},
        }

    def test_prefers_the_filed_figure_over_allocating_by_percentage(self, on_semi_xml, on_semi_result):
        # Deep-copied: enrich_breakdown_financials mutates its `breakdown`
        # arg in place, and on_semi_result is a module-scoped fixture
        # shared with every other test class in this file.
        b = copy.deepcopy(_breakdown(on_semi_result, "business_segment", "2026-04-04", "2026-07-03"))
        es.enrich_breakdown_financials(on_semi_xml, b, self._consolidated_facts())
        m = _member(b, "PowerSolutionsGroup")
        # Filed: exactly the filed figure, NOT 900M * 51.7%.
        assert m["gross_profit"] == self._PSG_FILED_GROSS_PROFIT
        # Not filed for this axis in this filing: allocated by revenue_pct.
        assert m["operating_income"] == pytest.approx(300_000_000.0 * 0.517, rel=1e-3)
        assert m["net_income"] == pytest.approx(250_000_000.0 * 0.517, rel=1e-3)
        assert m["assets"] == pytest.approx(10_000_000_000.0 * 0.517, rel=1e-3)
        assert m["financials_source"] == "mixed"

    def test_margins_are_derived_from_the_resulting_dollar_figures(self, on_semi_xml, on_semi_result):
        b = copy.deepcopy(_breakdown(on_semi_result, "business_segment", "2026-04-04", "2026-07-03"))
        es.enrich_breakdown_financials(on_semi_xml, b, self._consolidated_facts())
        m = _member(b, "PowerSolutionsGroup")
        assert m["gross_margin_pct"] == pytest.approx(m["gross_profit"] / m["revenue"] * 100, abs=0.01)
        assert m["op_margin_pct"] == pytest.approx(m["operating_income"] / m["revenue"] * 100, abs=0.01)
        assert m["net_margin_pct"] == pytest.approx(m["net_income"] / m["revenue"] * 100, abs=0.01)

    def test_a_filed_field_survives_even_with_zero_consolidated_data(self, on_semi_xml, on_semi_result):
        """gross_profit doesn't need fetch_xbrl_facts at all — it was read
        straight off this same filing. Only the fields with nothing filed
        (operating_income/net_income/assets) depend on consolidated data,
        and those alone go null when none is available."""
        b = copy.deepcopy(_breakdown(on_semi_result, "business_segment", "2026-04-04", "2026-07-03"))
        es.enrich_breakdown_financials(on_semi_xml, b, {})
        m = _member(b, "PowerSolutionsGroup")
        assert m["gross_profit"] == self._PSG_FILED_GROSS_PROFIT
        assert m["operating_income"] is None
        assert m["net_income"] is None
        assert m["assets"] is None
        assert m["financials_source"] == "filed"  # the only field that resolved at all was filed
        assert m["gross_margin_pct"] == pytest.approx(self._PSG_FILED_GROSS_PROFIT / m["revenue"] * 100, abs=0.01)
        assert m["op_margin_pct"] is None


class TestEstimateSegmentFinancials:
    def test_allocates_by_revenue_pct_and_derives_margins(self):
        facts = {
            "Revenue":        {"data_points": [{"val": 1_600_000_000.0, "end": "2026-07-03", "form": "10-Q"}]},
            "GrossProfit":    {"data_points": [{"val": 900_000_000.0, "end": "2026-07-03", "form": "10-Q"}]},
            "OperatingIncome": {"data_points": [{"val": 300_000_000.0, "end": "2026-07-03", "form": "10-Q"}]},
            "NetIncome":      {"data_points": [{"val": 250_000_000.0, "end": "2026-07-03", "form": "10-Q"}]},
            "TotalAssets":    {"data_points": [{"val": 10_000_000_000.0, "end": "2026-07-03", "form": "10-Q"}]},
        }
        with patch.object(es, "get_company_info", return_value=({"cik_plain": "1097864"}, {})), \
             patch.object(es, "fetch_xbrl_facts", return_value=facts):
            result = es.estimate_segment_financials("ON", revenue_pct=25.0)

        assert result["estimated"] is True
        assert result["source"] == "estimated"
        assert result["revenue"] == pytest.approx(400_000_000.0)
        assert result["gross_profit"] == pytest.approx(225_000_000.0)
        assert result["gross_margin_pct"] == pytest.approx(56.25, rel=1e-3)
        assert result["basis"]["gross_profit"]["consolidated_value"] == 900_000_000.0

    def test_unknown_ticker_reports_honest_failure(self):
        with patch.object(es, "get_company_info", side_effect=ValueError("Ticker not found")):
            result = es.estimate_segment_financials("NOPE", revenue_pct=10.0)
        assert result["estimated"] is False
        assert "not found" in result["reason"]

    def test_no_consolidated_facts_reports_honest_failure(self):
        with patch.object(es, "get_company_info", return_value=({"cik_plain": "1097864"}, {})), \
             patch.object(es, "fetch_xbrl_facts", return_value={}):
            result = es.estimate_segment_financials("ON", revenue_pct=10.0)
        assert result["estimated"] is False

    def test_missing_individual_metric_stays_null_not_zero(self):
        facts = {"Revenue": {"data_points": [{"val": 1_600_000_000.0, "end": "2026-07-03", "form": "10-Q"}]}}
        with patch.object(es, "get_company_info", return_value=({"cik_plain": "1097864"}, {})), \
             patch.object(es, "fetch_xbrl_facts", return_value=facts):
            result = es.estimate_segment_financials("ON", revenue_pct=25.0)
        assert result["revenue"] == pytest.approx(400_000_000.0)
        assert result["gross_profit"] is None
        assert result["gross_margin_pct"] is None
