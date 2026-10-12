"""
Tests for POST /sox/scope (sox_endpoints.py) preferring real detected
account balances (material_accounts_tool.py) over sox_scoping_tool's
heuristic estimates. No real DB, no network.

    pytest test_sox_scope_real_balances.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_endpoints
import sox_endpoints as se


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(se.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "display_name": "Test Auditor", "role": "admin", "id": 1,
    }
    return TestClient(app)


def _req(**overrides):
    body = {
        "ticker": "ON",
        "forecast": {"forecasts": [{"point": 250_000_000, "ci_lower": 200_000_000, "ci_upper": 300_000_000}] * 4},
        "risk_scores": {"risks": []},
        "ratios": {},
    }
    body.update(overrides)
    return body


def _xbrl_entry(value):
    return {"label": "Test Metric", "data_points": [{"val": value, "end": "2026-06-30", "form": "10-K"}]}


class TestSoxScopeUsesRealBalances:
    def test_real_inventory_balance_overrides_heuristic_estimate(self, client):
        xbrl = {
            "Revenue": _xbrl_entry(1_000_000_000.0),
            "TotalAssets": _xbrl_entry(1_000_000_000.0),
            "Inventory": _xbrl_entry(777_000_000.0),  # far from the 15%-of-revenue heuristic
        }
        with patch.object(se, "_resolve_company_id", return_value=42), \
             patch.object(se.db, "is_available", return_value=True), \
             patch.object(se.db, "list_sox_systems", return_value=[]), \
             patch.object(se.db, "get_sox_segments", return_value=[]), \
             patch.object(se.db, "get_sox_config", return_value=None), \
             patch.object(se.db, "get_sox_account_details", return_value={}), \
             patch.object(se.db, "get_sox_process_details", return_value={}), \
             patch.object(se.db, "get_manual_financials", return_value={}), \
             patch.object(se, "get_company_info", return_value=({"cik": "1", "sic": "3674"}, {})), \
             patch.object(se, "fetch_xbrl_facts", return_value=xbrl):
            r = client.post("/sox/scope", json=_req())

        assert r.status_code == 200
        accounts = r.json()["accounts_in_scope"]
        inv = next(a for a in accounts if a["account_id"] == "inventory")
        assert inv["balance_estimate"] == 777_000_000
        assert inv["balance_source"] == "real"

    def test_falls_back_to_heuristic_when_xbrl_fetch_fails(self, client):
        with patch.object(se, "_resolve_company_id", return_value=42), \
             patch.object(se.db, "is_available", return_value=True), \
             patch.object(se.db, "list_sox_systems", return_value=[]), \
             patch.object(se.db, "get_sox_segments", return_value=[]), \
             patch.object(se.db, "get_sox_config", return_value=None), \
             patch.object(se.db, "get_sox_account_details", return_value={}), \
             patch.object(se.db, "get_sox_process_details", return_value={}), \
             patch.object(se, "get_company_info", side_effect=ValueError("unknown ticker")):
            r = client.post("/sox/scope", json=_req())

        assert r.status_code == 200  # never fails the whole scope over this
        accounts = r.json()["accounts_in_scope"]
        inv = next(a for a in accounts if a["account_id"] == "inventory")
        assert inv["balance_source"] == "estimated"

    def test_uploaded_data_overrides_filed_xbrl_in_sox_scope(self, client):
        xbrl = {"Revenue": _xbrl_entry(1_000_000_000.0), "TotalAssets": _xbrl_entry(1_000_000_000.0),
                "Inventory": _xbrl_entry(50_000_000.0)}
        uploaded = {"Inventory": _xbrl_entry(999_000_000.0)}
        with patch.object(se, "_resolve_company_id", return_value=42), \
             patch.object(se.db, "is_available", return_value=True), \
             patch.object(se.db, "list_sox_systems", return_value=[]), \
             patch.object(se.db, "get_sox_segments", return_value=[]), \
             patch.object(se.db, "get_sox_config", return_value=None), \
             patch.object(se.db, "get_sox_account_details", return_value={}), \
             patch.object(se.db, "get_sox_process_details", return_value={}), \
             patch.object(se.db, "get_manual_financials", return_value=uploaded), \
             patch.object(se, "get_company_info", return_value=({"cik": "1", "sic": "3674"}, {})), \
             patch.object(se, "fetch_xbrl_facts", return_value=xbrl):
            r = client.post("/sox/scope", json=_req())

        inv = next(a for a in r.json()["accounts_in_scope"] if a["account_id"] == "inventory")
        assert inv["balance_estimate"] == 999_000_000
