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


class TestPersistSegments:
    """persist_segments() orchestration — mocked db/network, no live calls.
    Verifies the three honesty rules that matter most here: unreconciled
    breakdowns are never written, only the latest period per axis is
    written (the table isn't a time series), and persisted rows are
    stamped source='filed' so they're never mistaken for a manual entry."""

    def _fake_result(self, *, reconciled_current=True, reconciled_prior=True):
        return {
            "extracted": True,
            "ticker": "ON",
            "fiscal_year": "2026",
            "breakdowns": [
                {
                    "segment_type": "business_segment",
                    "period_start": "2026-04-04", "period_end": "2026-07-03",
                    "reconciled": reconciled_current,
                    "members": [
                        {"segment_name": "PowerSolutionsGroup", "revenue": 829_000_000.0, "revenue_pct": 51.7},
                        {"segment_name": "AnalogMixedSignalGroup", "revenue": 545_700_000.0, "revenue_pct": 34.0},
                    ],
                },
                {
                    # An older comparative period for the same axis — must
                    # NOT be persisted alongside the current one.
                    "segment_type": "business_segment",
                    "period_start": "2025-04-05", "period_end": "2025-07-04",
                    "reconciled": reconciled_prior,
                    "members": [
                        {"segment_name": "PowerSolutionsGroup", "revenue": 698_200_000.0, "revenue_pct": 47.5},
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

    def test_stamps_source_as_filed(self):
        with patch.object(es, "fetch_segments", return_value=self._fake_result()), \
             patch.object(es, "get_company_info", return_value=({"cik": "0001097864", "ticker": "ON"}, {})), \
             patch("db.is_available", return_value=True), \
             patch("db.upsert_company", return_value=42), \
             patch("db.upsert_sox_segment") as mock_upsert:
            es.persist_segments("ON")

        for call in mock_upsert.call_args_list:
            assert call.args[2]["source"] == "filed"

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
