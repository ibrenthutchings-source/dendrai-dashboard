"""
Tests for manual_financials_tool.py's Phase-2 segment support: parsing an
optional operating-unit dimension (geography / business_segment) out of an
uploaded spreadsheet, and routing those rows to sox_financial_segments
(source='uploaded') instead of the consolidated xbrl_metric_series path.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pandas as pd
import pytest

import manual_financials_tool as mft


def _csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


class TestTemplateLayoutSegments:
    def test_segment_type_and_name_columns_parsed(self):
        df = pd.DataFrame([
            {"metric": "Revenue", "period_end": "2025-12-31", "value": 100.0,
             "segment_type": "geography", "segment_name": "United States"},
            {"metric": "Revenue", "period_end": "2025-12-31", "value": 50.0,
             "segment_type": "geography", "segment_name": "EMEA"},
        ])
        result = mft.parse_spreadsheet(_csv_bytes(df), "segments.csv")
        items = result["line_items"]
        assert len(items) == 2
        assert {li["segment_name"] for li in items} == {"United States", "EMEA"}
        assert all(li["segment_type"] == "geography" for li in items)
        assert all(li["metric"] == "Revenue" for li in items)

    def test_no_segment_columns_leaves_dimension_none(self):
        df = pd.DataFrame([{"metric": "Revenue", "period_end": "2025-12-31", "value": 100.0}])
        items = mft.parse_spreadsheet(_csv_bytes(df), "plain.csv")["line_items"]
        assert items[0]["segment_type"] is None
        assert items[0]["segment_name"] is None

    def test_segment_name_only_column_defaults_to_business_segment(self):
        df = pd.DataFrame([
            {"metric": "Revenue", "period_end": "2025-12-31", "value": 10.0, "segment_name": "Widgets"},
        ])
        items = mft.parse_spreadsheet(_csv_bytes(df), "seg.csv")["line_items"]
        assert items[0]["segment_type"] == "business_segment"
        assert items[0]["segment_name"] == "Widgets"

    def test_geography_named_column_implies_segment_type(self):
        df = pd.DataFrame([
            {"metric": "Revenue", "period_end": "2025-12-31", "value": 10.0, "geography": "APAC"},
        ])
        items = mft.parse_spreadsheet(_csv_bytes(df), "geo.csv")["line_items"]
        assert items[0]["segment_type"] == "geography"
        assert items[0]["segment_name"] == "APAC"

    def test_blank_segment_name_treated_as_no_dimension(self):
        df = pd.DataFrame([
            {"metric": "Revenue", "period_end": "2025-12-31", "value": 10.0, "segment_name": ""},
        ])
        items = mft.parse_spreadsheet(_csv_bytes(df), "blank.csv")["line_items"]
        assert items[0]["segment_type"] is None
        assert items[0]["segment_name"] is None


class TestTrialBalanceLayoutSegments:
    def test_segment_column_excluded_from_date_columns_and_applied_per_row(self):
        df = pd.DataFrame([
            {"metric": "Revenue", "segment_name": "United States", "Jan-2025": 10.0, "Feb-2025": 12.0},
            {"metric": "Revenue", "segment_name": "EMEA", "Jan-2025": 5.0, "Feb-2025": 6.0},
        ])
        result = mft.parse_spreadsheet(_csv_bytes(df), "trial_balance.csv")
        assert result["format_detected"] == "trial_balance"
        items = result["line_items"]
        assert len(items) == 4
        us_rows = [li for li in items if li["segment_name"] == "United States"]
        assert len(us_rows) == 2
        assert all(li["segment_type"] == "business_segment" for li in items)


class TestCommitSegmentRows:
    def test_segment_rows_routed_to_upsert_sox_segment_not_xbrl_series(self):
        line_items = [
            {"metric": "Revenue", "period_end": "2025-12-31", "value": 700.0,
             "segment_type": "geography", "segment_name": "United States"},
            {"metric": "Revenue", "period_end": "2025-12-31", "value": 300.0,
             "segment_type": "geography", "segment_name": "EMEA"},
        ]
        with patch("db.upsert_xbrl_series") as mock_series, \
             patch("db.upsert_manual_data_points") as mock_points, \
             patch("db.upsert_sox_segment") as mock_seg:
            result = mft.commit_line_items(company_id=42, line_items=line_items)

        mock_series.assert_not_called()
        mock_points.assert_not_called()
        assert mock_seg.call_count == 2
        assert result["segments_saved"] == 2
        assert result["metrics_saved"] == 0

    def test_revenue_pct_computed_as_share_within_upload(self):
        line_items = [
            {"metric": "Revenue", "period_end": "2025-12-31", "value": 700.0,
             "segment_type": "geography", "segment_name": "United States"},
            {"metric": "Revenue", "period_end": "2025-12-31", "value": 300.0,
             "segment_type": "geography", "segment_name": "EMEA"},
        ]
        with patch("db.upsert_sox_segment") as mock_seg:
            mft.commit_line_items(company_id=42, line_items=line_items)

        saved = {c.args[2]["segment_name"]: c.args[2] for c in mock_seg.call_args_list}
        assert saved["United States"]["revenue_pct"] == 70.0
        assert saved["EMEA"]["revenue_pct"] == 30.0
        assert saved["United States"]["source"] == "uploaded"
        assert saved["United States"]["fiscal_year"] == "2025"

    def test_multiple_metrics_for_same_segment_fold_into_one_row(self):
        line_items = [
            {"metric": "Revenue", "period_end": "2025-12-31", "value": 700.0,
             "segment_type": "geography", "segment_name": "United States"},
            {"metric": "OperatingIncome", "period_end": "2025-12-31", "value": 120.0,
             "segment_type": "geography", "segment_name": "United States"},
        ]
        with patch("db.upsert_sox_segment") as mock_seg:
            mft.commit_line_items(company_id=42, line_items=line_items)

        assert mock_seg.call_count == 1
        seg = mock_seg.call_args.args[2]
        assert seg["revenue"] == 700.0
        assert seg["operating_income"] == 120.0

    def test_non_segment_rows_still_use_xbrl_path(self):
        line_items = [
            {"metric": "Revenue", "period_end": "2025-12-31", "period_start": "2025-01-01",
             "value": 1000.0, "granularity": "annual", "segment_type": None, "segment_name": None},
        ]
        with patch("db.upsert_xbrl_series", return_value=99) as mock_series, \
             patch("db.upsert_manual_data_points") as mock_points, \
             patch("db.upsert_sox_segment") as mock_seg:
            result = mft.commit_line_items(company_id=42, line_items=line_items)

        mock_series.assert_called_once()
        mock_points.assert_called_once()
        mock_seg.assert_not_called()
        assert result["metrics_saved"] == 1
        assert result["segments_saved"] == 0

    def test_unmapped_metric_skipped_even_with_segment_dimension(self):
        line_items = [
            {"metric": None, "period_end": "2025-12-31", "value": 5.0,
             "segment_type": "geography", "segment_name": "United States"},
        ]
        with patch("db.upsert_sox_segment") as mock_seg:
            result = mft.commit_line_items(company_id=42, line_items=line_items)

        mock_seg.assert_not_called()
        assert result["skipped_unmapped"] == 1
        assert result["segments_saved"] == 0
