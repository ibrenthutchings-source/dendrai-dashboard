"""
Tests for GET /edgar/peers/{ticker} (api_server.edgar_peers_saved)'s self-heal
of a starved saved peer set.

fetch_sic_peers() used to return an alphabetically-first, largely-defunct
slice of EDGAR's SIC roster (fixed in edgar_tool.py — see
test_edgar_sic_peers.py). That fix only helps a FRESH /edgar/peers run: a
peer set already persisted to sic_peers from before the fix stayed starved
forever, because this endpoint only re-enriches the identities already
saved — it never re-derives them. edgar_peers_saved now re-runs discovery
once when the saved, re-enriched set is thin, and keeps the result only if
it's actually better.

All db.*, fetch_sic_peers, and _enrich_peer_financials calls are mocked —
no real DB, no network.

    pytest test_edgar_peers_saved_self_heal.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import api_server


def _saved(sic="3674", n_peers=1):
    return {
        "ticker": "ON", "company_name": "ON Semiconductor", "sic": sic,
        "sic_description": "Semiconductors", "cik": "0001097864",
        "peers": [{"ticker": f"P{i}", "cik": f"000000000{i}", "company_name": f"Peer {i}"} for i in range(n_peers)],
    }


def _peer_with_data(ticker):
    return {"ticker": ticker, "cik": "1", "company_name": ticker, "gross_margin": 0.4}


def _peer_without_data(ticker):
    return {"ticker": ticker, "cik": "1", "company_name": ticker}


def _base_patches(saved, enrich_side_effect, fetch_sic_peers_return=None):
    """Common patch set; each test overrides what it needs to assert on."""
    # _enrich_peer_financials now takes an optional subject_sic second
    # arg (material-account detection scored against the SUBJECT's
    # industry) — every test here supplies a single-arg side_effect, so
    # swallow that second positional arg here rather than changing every
    # test's lambda.
    def _enrich_wrapper(peer, subject_sic=""):
        return enrich_side_effect(peer)

    return [
        patch.object(api_server.db, "is_available", return_value=True),
        patch.object(api_server.db, "get_sic_peers", return_value=saved),
        patch.object(api_server, "_enrich_peer_financials", side_effect=_enrich_wrapper),
        patch.object(api_server, "fetch_sic_peers", return_value=fetch_sic_peers_return or []),
        patch.object(api_server, "fetch_xbrl_facts", return_value=None),
        patch.object(api_server.db, "upsert_company", return_value=42),
        patch.object(api_server.db, "save_sic_peers"),
    ]


def _apply(patches):
    started = [p.start() for p in patches]
    return patches, started


def _stop(patches):
    for p in patches:
        p.stop()


class TestEdgarPeersSavedSelfHeal:
    def test_starved_saved_set_triggers_a_fresh_lookup_and_swaps_in_when_better(self):
        saved = _saved(n_peers=1)  # only 1 saved identity — enriches to 1 usable peer, below the floor
        fresh_identities = [{"ticker": f"F{i}", "cik": f"2{i}", "company_name": f"Fresh {i}"} for i in range(5)]

        def enrich(p):
            # Saved identity has no data; every fresh identity does.
            return _peer_without_data(p["ticker"]) if p["ticker"].startswith("P") else _peer_with_data(p["ticker"])

        patches = _base_patches(saved, enrich, fetch_sic_peers_return=fresh_identities)
        _, started = _apply(patches)
        try:
            result = api_server.edgar_peers_saved("ON")
        finally:
            _stop(patches)

        assert len(result["peers"]) == 5
        assert {p["ticker"] for p in result["peers"]} == {"F0", "F1", "F2", "F3", "F4"}
        started[3].assert_called_once_with("3674", max_peers=15)  # fetch_sic_peers
        started[5].assert_called_once()   # upsert_company
        started[6].assert_called_once()   # save_sic_peers persists the fresh identities

    def test_starved_saved_set_keeps_saved_result_if_fresh_lookup_is_not_better(self):
        saved = _saved(n_peers=1)
        fresh_identities = [{"ticker": "F0", "cik": "20", "company_name": "Fresh 0"}]

        def enrich(p):
            # Neither the saved nor the fresh identity has data.
            return _peer_without_data(p["ticker"])

        patches = _base_patches(saved, enrich, fetch_sic_peers_return=fresh_identities)
        _, started = _apply(patches)
        try:
            result = api_server.edgar_peers_saved("ON")
        finally:
            _stop(patches)

        assert result["peers"] == []  # saved set (also empty after enrichment) is kept, not swapped
        started[6].assert_not_called()  # nothing new persisted — no improvement to save

    def test_healthy_saved_set_skips_the_self_heal_lookup_entirely(self):
        saved = _saved(n_peers=5)
        patches = _base_patches(saved, lambda p: _peer_with_data(p["ticker"]))
        _, started = _apply(patches)
        try:
            result = api_server.edgar_peers_saved("ON")
        finally:
            _stop(patches)

        assert len(result["peers"]) == 5
        started[3].assert_not_called()  # fetch_sic_peers never invoked — already healthy

    def test_no_sic_code_skips_self_heal_without_crashing(self):
        saved = _saved(sic="", n_peers=1)
        patches = _base_patches(saved, lambda p: _peer_without_data(p["ticker"]))
        _, started = _apply(patches)
        try:
            result = api_server.edgar_peers_saved("ON")
        finally:
            _stop(patches)

        assert result["peers"] == []
        started[3].assert_not_called()

    def test_404_when_nothing_saved(self):
        with patch.object(api_server.db, "is_available", return_value=True), \
             patch.object(api_server.db, "get_sic_peers", return_value=None):
            try:
                api_server.edgar_peers_saved("NOPE")
                assert False, "expected HTTPException"
            except Exception as e:
                assert getattr(e, "status_code", None) == 404

    def test_503_when_db_unavailable(self):
        with patch.object(api_server.db, "is_available", return_value=False):
            try:
                api_server.edgar_peers_saved("ON")
                assert False, "expected HTTPException"
            except Exception as e:
                assert getattr(e, "status_code", None) == 503


class TestPeerEnrichmentConcurrencyIsBounded:
    """Regression coverage for the 2026-08-30 development OOM incident:
    fetch_sic_peers now ranks real, active (often large-cap) companies first
    (see test_edgar_sic_peers.py), so a peer's XBRL companyfacts document is
    typically several MB raw / 15-20MB parsed — confirmed by direct
    measurement against real SEC data, not estimated. At the old
    max_workers=8, one /edgar/peers call could hold 8 of these in memory
    concurrently; the exact same page load can also trigger
    edgar_peers_saved's self-heal path, doubling that. _PEER_ENRICH_MAX_WORKERS
    is the single shared bound for every peer-enrichment thread pool in this
    file — these tests guard against a future edit silently widening it back
    out (a hardcoded max_workers=8 creeping back into just one call site, or
    the constant itself being bumped without re-deriving the memory budget)."""

    def test_the_shared_constant_is_small(self):
        # Not a specific number, deliberately — just "small enough that this
        # was clearly sized against a memory budget, not defaulted." Anyone
        # changing it should update the comment above it, not just the value.
        assert 1 <= api_server._PEER_ENRICH_MAX_WORKERS <= 4

    def _spy_on_thread_pool(self, monkeypatch):
        """Wrap the real ThreadPoolExecutor so pool.map still works, but
        record every max_workers it was constructed with."""
        real_executor = api_server.concurrent.futures.ThreadPoolExecutor
        calls = []

        def _spy(*args, **kwargs):
            calls.append(kwargs.get("max_workers", args[0] if args else None))
            return real_executor(*args, **kwargs)

        monkeypatch.setattr(api_server.concurrent.futures, "ThreadPoolExecutor", _spy)
        return calls

    def test_edgar_peers_saved_self_heal_uses_the_bounded_pool(self, monkeypatch):
        calls = self._spy_on_thread_pool(monkeypatch)
        saved = _saved(n_peers=1)  # thin -> triggers both enrichment passes
        patches = _base_patches(
            saved,
            lambda p: _peer_without_data(p["ticker"]),
            fetch_sic_peers_return=[{"ticker": "F0", "cik": "20", "company_name": "Fresh 0"}],
        )
        _, started = _apply(patches)
        try:
            api_server.edgar_peers_saved("ON")
        finally:
            _stop(patches)

        assert calls, "ThreadPoolExecutor was never constructed"
        assert all(c == api_server._PEER_ENRICH_MAX_WORKERS for c in calls)

    def test_edgar_peers_live_endpoint_uses_the_bounded_pool(self, monkeypatch):
        calls = self._spy_on_thread_pool(monkeypatch)
        with patch.object(api_server, "get_company_info", return_value=({"cik": "1", "cik_plain": "1", "company_name": "ON Semi", "sic": "3674"}, {})), \
             patch.object(api_server.peer_intel, "extract_competitor_names", return_value=[]), \
             patch.object(api_server, "fetch_sic_peers", return_value=[{"ticker": "F0", "cik": "20", "company_name": "Fresh 0"}]), \
             patch.object(api_server, "_enrich_peer_financials", side_effect=lambda p, subject_sic="": {**p, "gross_margin": 0.4}), \
             patch.object(api_server, "fetch_xbrl_facts", return_value=None), \
             patch.object(api_server.db, "is_available", return_value=False):
            api_server.edgar_peers(api_server.TickerRequest(ticker="ON"))

        assert calls, "ThreadPoolExecutor was never constructed"
        assert all(c == api_server._PEER_ENRICH_MAX_WORKERS for c in calls)
