"""
Tests for edgar_tool.fetch_xbrl_facts's CIK zero-padding.

get_company_info() already returns a zero-padded 10-digit CIK for a fresh
live lookup, but a CIK read back from companies.cik after a DB round trip
is not guaranteed to still have its leading zeros — this silently broke
Board Intelligence's peer-benchmarking subject-history line (confirmed
against real data: db.get_sic_peers() returned cik="1097864" for onsemi,
7 digits not 10, and SEC's companyfacts API 404s on that, which
fetch_xbrl_facts's own `if r is None: return {}` makes indistinguishable
from "this company just has no XBRL data"). Every caller must be protected
by fetch_xbrl_facts itself, not by each caller remembering to pad first.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import edgar_tool


def _fake_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


class TestFetchXbrlFactsCikPadding:
    def test_unpadded_cik_is_zero_padded_before_the_request(self):
        with patch.object(edgar_tool, "_get_safe", return_value=_fake_response({"facts": {}})) as mock_get:
            edgar_tool.fetch_xbrl_facts("1097864")
        requested_url = mock_get.call_args[0][0]
        assert "CIK0001097864.json" in requested_url

    def test_already_padded_cik_is_left_unchanged(self):
        with patch.object(edgar_tool, "_get_safe", return_value=_fake_response({"facts": {}})) as mock_get:
            edgar_tool.fetch_xbrl_facts("0001097864")
        requested_url = mock_get.call_args[0][0]
        assert "CIK0001097864.json" in requested_url

    def test_int_cik_is_accepted_and_padded(self):
        """cik is typed str, but a caller passing the int form (e.g. a value
        that round-tripped through a DB int column) must not crash str.zfill."""
        with patch.object(edgar_tool, "_get_safe", return_value=_fake_response({"facts": {}})) as mock_get:
            edgar_tool.fetch_xbrl_facts(1097864)
        requested_url = mock_get.call_args[0][0]
        assert "CIK0001097864.json" in requested_url

    def test_no_response_returns_empty_dict_not_none(self):
        with patch.object(edgar_tool, "_get_safe", return_value=None):
            result = edgar_tool.fetch_xbrl_facts("1097864")
        assert result == {}
