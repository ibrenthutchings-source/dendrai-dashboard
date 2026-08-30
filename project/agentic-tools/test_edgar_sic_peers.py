"""
Tests for edgar_tool.fetch_sic_peers.

Confirmed live against SIC=3674 (semiconductors): EDGAR's browse-edgar
company list for a SIC code is alphabetical across EVERY company that has
EVER filed a matching form, not just current filers — a plain first-N cut
off that list was dominated by decades-old delisted names (3DLABS, ACTEL
CORP, AEROFLEX, AGERE SYSTEMS, ...), 13 of the first 15 alphabetical entries
had zero enrichable financial data, and the peer-benchmarking chart ended up
with 1-2 lines (or none). Separately, every peer's ticker lookup was
silently broken: company_tickers.json keys CIKs unpadded ("2488") while the
browse page's cik_plain is zero-padded ("0000002488"), so the dict lookup
never hit — not even for a still-actively-traded company like AMD.

    pytest test_edgar_sic_peers.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import edgar_tool


def _browse_edgar_html(rows):
    """rows: list of (cik_padded_10, name, state)."""
    trs = "".join(
        f'<tr><td><a href="?CIK={cik}">{cik}</a></td><td>{name}</td><td>{state}</td></tr>'
        for cik, name, state in rows
    )
    return f'<table class="tableFile2"><tr><th>CIK</th><th>Company</th><th>State</th></tr>{trs}</table>'


def _fake_response(*, text=None, json_payload=None):
    resp = MagicMock()
    if text is not None:
        resp.text = text
    if json_payload is not None:
        resp.json.return_value = json_payload
    return resp


def _mock_get_safe(browse_rows, ticker_json):
    browse_html = _browse_edgar_html(browse_rows)

    def _side_effect(url, *a, **kw):
        if "company_tickers.json" in url:
            return _fake_response(json_payload=ticker_json)
        return _fake_response(text=browse_html)

    return _side_effect


class TestFetchSicPeersActiveFilerPreference:
    def test_ticker_resolves_despite_the_padded_vs_unpadded_cik_mismatch(self):
        rows = [("0000002488", "ADVANCED MICRO DEVICES INC", "CA")]
        ticker_json = {"0": {"cik_str": 2488, "ticker": "AMD", "title": "Advanced Micro Devices"}}
        with patch.object(edgar_tool, "_get_safe", side_effect=_mock_get_safe(rows, ticker_json)):
            peers = edgar_tool.fetch_sic_peers("3674", max_peers=5)
        assert peers[0]["ticker"] == "AMD"

    def test_live_ticker_peers_are_ranked_before_defunct_ones(self):
        # Alphabetically, the defunct company comes first — the fix must
        # still put the live-ticker one ahead of it.
        rows = [
            ("0000907687", "ACTEL CORP", "CA"),          # defunct, no ticker
            ("0000002488", "ADVANCED MICRO DEVICES INC", "CA"),  # live
        ]
        ticker_json = {"0": {"cik_str": 2488, "ticker": "AMD", "title": "Advanced Micro Devices"}}
        with patch.object(edgar_tool, "_get_safe", side_effect=_mock_get_safe(rows, ticker_json)):
            peers = edgar_tool.fetch_sic_peers("3674", max_peers=5)
        assert [p["company_name"] for p in peers] == ["ADVANCED MICRO DEVICES INC", "ACTEL CORP"]

    def test_ordering_within_each_group_stays_alphabetical(self):
        rows = [
            ("0000000001", "AAA DEFUNCT CO", "CA"),
            ("0000000002", "ZZZ DEFUNCT CO", "CA"),
            ("0000000003", "AAA LIVE CO", "CA"),
            ("0000000004", "ZZZ LIVE CO", "CA"),
        ]
        ticker_json = {
            "0": {"cik_str": 3, "ticker": "AAAL", "title": "AAA Live"},
            "1": {"cik_str": 4, "ticker": "ZZZL", "title": "ZZZ Live"},
        }
        with patch.object(edgar_tool, "_get_safe", side_effect=_mock_get_safe(rows, ticker_json)):
            peers = edgar_tool.fetch_sic_peers("3674", max_peers=10)
        assert [p["company_name"] for p in peers] == [
            "AAA LIVE CO", "ZZZ LIVE CO", "AAA DEFUNCT CO", "ZZZ DEFUNCT CO",
        ]

    def test_result_is_capped_at_max_peers_after_ranking(self):
        rows = [(f"{i:010d}", f"DEFUNCT {i}", "CA") for i in range(1, 20)]
        rows.append(("0000000099", "LIVE CO", "CA"))
        ticker_json = {"0": {"cik_str": 99, "ticker": "LIVE", "title": "Live Co"}}
        with patch.object(edgar_tool, "_get_safe", side_effect=_mock_get_safe(rows, ticker_json)):
            peers = edgar_tool.fetch_sic_peers("3674", max_peers=3)
        assert len(peers) == 3
        assert peers[0]["company_name"] == "LIVE CO"  # ranked to the front, survives the cap

    def test_requests_a_full_page_regardless_of_max_peers(self):
        """The raw candidate pool must be large (EDGAR's max page size) even
        when max_peers is small — a small requested count is what caused the
        alphabetical-defunct-names bug in the first place (too few raw
        candidates for the active-filer ranking to have anything to promote)."""
        rows = [("0000000001", "SOLO CO", "CA")]
        with patch.object(edgar_tool, "_get_safe", side_effect=_mock_get_safe(rows, {})) as mock_get:
            edgar_tool.fetch_sic_peers("3674", max_peers=5)
        browse_call = next(c for c in mock_get.call_args_list if "browse-edgar" in c[0][0])
        assert "count=100" in browse_call[0][0]

    def test_no_response_returns_empty_list(self):
        with patch.object(edgar_tool, "_get_safe", return_value=None):
            assert edgar_tool.fetch_sic_peers("3674", max_peers=5) == []
