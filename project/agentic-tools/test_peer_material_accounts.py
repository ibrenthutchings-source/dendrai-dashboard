"""
Tests for peer-benchmarking material-account enrichment (api_server.py's
_enrich_peer_financials/_attach_peer_material_accounts) — a peer's material
accounts are scored against the SUBJECT company's industry template, not
the peer's own, so every peer in a comparison is measured on the same line
items. No real DB, no network.

    pytest test_peer_material_accounts.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import api_server


@pytest.fixture(autouse=True)
def _clear_peer_enrich_cache():
    """_peer_enrich_cache is module-level and keyed only by CIK — without
    clearing it, an earlier test's cache entry for the same fake CIK would
    silently serve stale _flat_metrics/fields to a later test."""
    api_server._peer_enrich_cache.clear()
    yield
    api_server._peer_enrich_cache.clear()


def _xbrl_entry(value):
    return {"label": "Test Metric", "data_points": [
        {"val": value, "end": "2026-06-30", "form": "10-K"},
        {"val": value * 0.9, "end": "2025-06-30", "form": "10-K"},
    ]}


def _manufacturer_xbrl():
    return {
        "Revenue": _xbrl_entry(1000.0),
        "TotalAssets": _xbrl_entry(1000.0),
        "Inventory": _xbrl_entry(80.0),   # 8% of assets -> material
        "COGS": _xbrl_entry(600.0),       # 60% of revenue -> material
        "PPEGross": _xbrl_entry(10.0),    # 1% -> not material
    }


class TestPeerMaterialAccountsUseSubjectTemplate:
    def test_peer_gets_material_accounts_when_subject_sic_given(self):
        peer = {"ticker": "PEER1", "cik": "0000000099"}
        with patch.object(api_server, "fetch_xbrl_facts", return_value=_manufacturer_xbrl()):
            result = api_server._enrich_peer_financials(peer, subject_sic="3674")  # manufacturing
        metrics = {a["metric"] for a in result.get("material_accounts", [])}
        assert "Inventory" in metrics
        assert "COGS" in metrics
        assert "PPEGross" not in metrics  # below the 5% cutoff

    def test_no_subject_sic_means_no_material_accounts_key(self):
        peer = {"ticker": "PEER1", "cik": "0000000099"}
        with patch.object(api_server, "fetch_xbrl_facts", return_value=_manufacturer_xbrl()):
            result = api_server._enrich_peer_financials(peer, subject_sic="")
        assert "material_accounts" not in result

    def test_flat_metrics_never_leaks_into_the_returned_peer(self):
        peer = {"ticker": "PEER1", "cik": "0000000099"}
        with patch.object(api_server, "fetch_xbrl_facts", return_value=_manufacturer_xbrl()):
            result = api_server._enrich_peer_financials(peer, subject_sic="3674")
        assert "_flat_metrics" not in result

    def test_financial_services_subject_template_excludes_inventory_for_same_peer(self):
        # Same peer's raw financials, but the SUBJECT is a bank this time —
        # a manufacturing-only account (Inventory) must not be templated in.
        peer = {"ticker": "PEER1", "cik": "0000000099"}
        xbrl = {**_manufacturer_xbrl(), "LoansReceivable": _xbrl_entry(700.0), "Deposits": _xbrl_entry(800.0)}
        with patch.object(api_server, "fetch_xbrl_facts", return_value=xbrl):
            result = api_server._enrich_peer_financials(peer, subject_sic="6022")  # bank
        metrics = {a["metric"] for a in result.get("material_accounts", [])}
        assert "Inventory" not in metrics
        assert "LoansReceivable" in metrics


class TestCachedPeerStillGetsFreshMaterialAccountsPerSubject:
    def test_second_call_for_a_different_subject_recomputes_material_accounts(self):
        peer1 = {"ticker": "PEER1", "cik": "0000000099"}
        with patch.object(api_server, "fetch_xbrl_facts", return_value=_manufacturer_xbrl()) as mock_fetch:
            first = api_server._enrich_peer_financials(dict(peer1), subject_sic="3674")  # manufacturing
            # Second call for a DIFFERENT subject's industry, same cached peer —
            # fetch_xbrl_facts must NOT be called again (cache hit on base
            # fields), yet material_accounts must reflect the new template.
            second = api_server._enrich_peer_financials(dict(peer1), subject_sic="6022")  # bank

        assert mock_fetch.call_count == 1  # cache hit avoided a second fetch
        first_metrics = {a["metric"] for a in first.get("material_accounts", [])}
        second_metrics = {a["metric"] for a in second.get("material_accounts", [])}
        assert "Inventory" in first_metrics
        assert "Inventory" not in second_metrics
        assert "_flat_metrics" not in first and "_flat_metrics" not in second
