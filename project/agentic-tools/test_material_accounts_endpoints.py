"""
Tests for GET /material-accounts/{ticker} and POST /material-accounts/{ticker}/forecast
(api_server.py). Same direct-call, mocked-boundary style as
test_edgar_peers_saved_self_heal.py — no real DB, no network.

    pytest test_material_accounts_endpoints.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import api_server


def _meta(sic="3674"):
    return {"cik": "0000123456", "cik_plain": "123456", "company_name": "Test Manufacturing Co", "sic": sic,
            "sic_description": "Semiconductors"}


def _xbrl_entry(value):
    return {"label": "Test Metric", "data_points": [{"val": value, "end": "2026-06-30"}]}


def _manufacturer_xbrl():
    return {
        "Revenue": _xbrl_entry(1000.0),
        "TotalAssets": _xbrl_entry(1000.0),
        "Inventory": _xbrl_entry(80.0),   # 8% of assets -> material
        "COGS": _xbrl_entry(600.0),       # 60% of revenue -> material
        "PPEGross": _xbrl_entry(10.0),    # 1% -> not material
    }


class TestMaterialAccountsEndpoint:
    def test_returns_detected_accounts_with_db_unavailable(self):
        with patch.object(api_server, "get_company_info", return_value=(_meta(), {})), \
             patch.object(api_server, "fetch_xbrl_facts", return_value=_manufacturer_xbrl()), \
             patch.object(api_server.db, "is_available", return_value=False):
            result = api_server.material_accounts("ON")

        assert result["ticker"] == "ON"
        metrics = {a["metric"]: a for a in result["accounts"]}
        assert metrics["Inventory"]["is_material"] is True
        assert metrics["COGS"]["is_material"] is True
        assert metrics["PPEGross"]["is_material"] is False

    def test_uploaded_data_overrides_filed_when_db_available(self):
        uploaded = {"Inventory": _xbrl_entry(900.0)}  # 90% of assets once uploaded
        with patch.object(api_server, "get_company_info", return_value=(_meta(), {})), \
             patch.object(api_server, "fetch_xbrl_facts", return_value=_manufacturer_xbrl()), \
             patch.object(api_server.db, "is_available", return_value=True), \
             patch.object(api_server.db, "upsert_company", return_value=42), \
             patch.object(api_server.db, "get_manual_financials", return_value=uploaded):
            result = api_server.material_accounts("ON")

        inv = next(a for a in result["accounts"] if a["metric"] == "Inventory")
        assert inv["value"] == 900.0
        assert inv["source"] == "uploaded"

    def test_404_for_unknown_ticker(self):
        with patch.object(api_server, "get_company_info", side_effect=ValueError("not found")):
            try:
                api_server.material_accounts("NOPE")
                assert False, "expected HTTPException"
            except Exception as e:
                assert getattr(e, "status_code", None) == 404


class TestMaterialAccountsForecastEndpoint:
    def test_forecast_only_covers_material_accounts(self):
        calls = []

        def _fake_forecast_backtest(xbrl, macro_info, metric, horizon, company_id=None):
            calls.append(metric)
            return {"forecast": {"note": "stub"}}

        with patch.object(api_server, "get_company_info", return_value=(_meta(), {})), \
             patch.object(api_server, "fetch_xbrl_facts", return_value=_manufacturer_xbrl()), \
             patch.object(api_server.db, "is_available", return_value=False), \
             patch("predictive_analytics_tool.run_forecast_backtest", side_effect=_fake_forecast_backtest):
            req = api_server.MaterialAccountsForecastRequest()
            result = api_server.material_accounts_forecast("ON", req)

        assert "Inventory" in calls
        assert "COGS" in calls
        assert "PPEGross" not in calls  # not material, never forecast
        assert set(result["forecasts"].keys()) == set(calls)

    def test_404_for_unknown_ticker(self):
        with patch.object(api_server, "get_company_info", side_effect=ValueError("not found")):
            req = api_server.MaterialAccountsForecastRequest()
            try:
                api_server.material_accounts_forecast("NOPE", req)
                assert False, "expected HTTPException"
            except Exception as e:
                assert getattr(e, "status_code", None) == 404
