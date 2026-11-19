#!/usr/bin/env python3
"""
Tests for material_accounts_tool.py — dynamic material-account detection.

    pytest test_material_accounts_tool.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import material_accounts_tool as mat


def _xbrl_entry(value, label="Test Metric"):
    return {"label": label, "data_points": [{"val": value, "end": "2026-06-30"}]}


def _manufacturer_xbrl(inventory=100.0, revenue=1000.0, total_assets=1000.0, cogs=None,
                        raw=None, wip=None, fg=None):
    x = {
        "Revenue": _xbrl_entry(revenue),
        "TotalAssets": _xbrl_entry(total_assets),
        "Inventory": _xbrl_entry(inventory),
    }
    if cogs is not None:
        x["COGS"] = _xbrl_entry(cogs)
    if raw is not None:
        x["InventoryRawMaterials"] = _xbrl_entry(raw)
    if wip is not None:
        x["InventoryWorkInProcess"] = _xbrl_entry(wip)
    if fg is not None:
        x["InventoryFinishedGoods"] = _xbrl_entry(fg)
    return x


class TestMaterialityRatioBoundary:
    def test_exactly_5_percent_is_material(self):
        # Inventory 50 / TotalAssets 1000 = exactly 5%
        xbrl = _manufacturer_xbrl(inventory=50.0, total_assets=1000.0)
        accounts = mat.detect_material_accounts(xbrl, sic="3674")
        inv = next(a for a in accounts if a["metric"] == "Inventory")
        assert inv["ratio"] == 0.05
        assert inv["is_material"] is True

    def test_just_below_5_percent_is_not_material(self):
        xbrl = _manufacturer_xbrl(inventory=49.0, total_assets=1000.0)
        accounts = mat.detect_material_accounts(xbrl, sic="3674")
        inv = next(a for a in accounts if a["metric"] == "Inventory")
        assert inv["ratio"] == 0.049
        assert inv["is_material"] is False

    def test_missing_base_metric_yields_no_ratio_and_not_material(self):
        xbrl = {"Inventory": _xbrl_entry(50.0)}  # no TotalAssets at all
        accounts = mat.detect_material_accounts(xbrl, sic="3674")
        inv = next(a for a in accounts if a["metric"] == "Inventory")
        assert inv["ratio"] is None
        assert inv["is_material"] is False


class TestUploadedOverridesWinOverFiledXBRL:
    def test_uploaded_value_replaces_filed_value(self):
        xbrl = _manufacturer_xbrl(inventory=50.0, total_assets=1000.0)
        uploaded = {"Inventory": _xbrl_entry(200.0)}
        accounts = mat.detect_material_accounts(xbrl, sic="3674", uploaded_xbrl=uploaded)
        inv = next(a for a in accounts if a["metric"] == "Inventory")
        assert inv["value"] == 200.0
        assert inv["source"] == "uploaded"
        assert inv["ratio"] == 0.2

    def test_filed_value_used_when_no_override_present(self):
        xbrl = _manufacturer_xbrl(inventory=50.0, total_assets=1000.0)
        accounts = mat.detect_material_accounts(xbrl, sic="3674", uploaded_xbrl={})
        inv = next(a for a in accounts if a["metric"] == "Inventory")
        assert inv["value"] == 50.0
        assert inv["source"] == "filed"

    def test_uploaded_override_can_introduce_a_metric_absent_from_xbrl_generic_bucket(self):
        xbrl = {"Revenue": _xbrl_entry(1000.0)}
        uploaded = {"Inventory": _xbrl_entry(300.0)}
        accounts = mat.detect_material_accounts(xbrl, sic="9999", uploaded_xbrl=uploaded)
        names = {a["metric"] for a in accounts}
        assert "Inventory" in names


class TestIndustryBucketSelection:
    def test_manufacturing_sic_gets_inventory_and_cogs_candidates(self):
        xbrl = _manufacturer_xbrl(cogs=600.0)
        accounts = mat.detect_material_accounts(xbrl, sic="3674")  # semiconductors -> manufacturing
        metrics = {a["metric"] for a in accounts}
        assert "Inventory" in metrics and "COGS" in metrics
        assert all(a["industry_group"] == "manufacturing" for a in accounts)

    def test_financial_services_sic_gets_loans_and_deposits_not_inventory(self):
        xbrl = {
            "Revenue": _xbrl_entry(1000.0), "TotalAssets": _xbrl_entry(5000.0),
            "LoansReceivable": _xbrl_entry(3000.0), "Deposits": _xbrl_entry(4000.0),
            "Inventory": _xbrl_entry(999.0),  # present in xbrl but NOT in this template's candidate list
        }
        accounts = mat.detect_material_accounts(xbrl, sic="6022")  # national commercial banks
        metrics = {a["metric"] for a in accounts}
        assert "LoansReceivable" in metrics and "Deposits" in metrics
        assert "Inventory" not in metrics
        assert all(a["industry_group"] == "financial_services" for a in accounts)

    def test_saas_sic_gets_deferred_revenue_not_inventory(self):
        xbrl = {
            "Revenue": _xbrl_entry(1000.0), "TotalAssets": _xbrl_entry(2000.0),
            "DeferredRevenueCurrent": _xbrl_entry(400.0),
            "Inventory": _xbrl_entry(999.0),
        }
        accounts = mat.detect_material_accounts(xbrl, sic="7372")  # prepackaged software
        metrics = {a["metric"] for a in accounts}
        assert "DeferredRevenueCurrent" in metrics
        assert "Inventory" not in metrics

    def test_unrecognized_industry_falls_back_to_generic(self):
        xbrl = _manufacturer_xbrl(cogs=600.0)
        accounts = mat.detect_material_accounts(xbrl, sic="9999")
        assert all(a["industry_group"] == "generic" for a in accounts)


class TestSubAccountParentLinkage:
    def test_inventory_subtags_link_to_inventory_as_parent(self):
        xbrl = _manufacturer_xbrl(raw=20.0, wip=15.0, fg=15.0)
        accounts = mat.detect_material_accounts(xbrl, sic="3674")
        by_metric = {a["metric"]: a for a in accounts}
        assert by_metric["InventoryRawMaterials"]["parent"] == "Inventory"
        assert by_metric["InventoryWorkInProcess"]["parent"] == "Inventory"
        assert by_metric["InventoryFinishedGoods"]["parent"] == "Inventory"
        assert by_metric["Inventory"]["parent"] is None

    def test_subtags_absent_from_filing_are_simply_not_included(self):
        # No raw/wip/fg passed — filer doesn't break out inventory components.
        xbrl = _manufacturer_xbrl()
        accounts = mat.detect_material_accounts(xbrl, sic="3674")
        metrics = {a["metric"] for a in accounts}
        assert "InventoryRawMaterials" not in metrics
        assert "Inventory" in metrics  # flat fallback still present


class TestGenericFallback:
    def test_generic_bucket_flags_top_n_material_regardless_of_threshold(self):
        # Every ratio well below 5%, but generic bucket still charts the top N.
        xbrl = {
            "Revenue": _xbrl_entry(1_000_000.0),
            "TotalAssets": _xbrl_entry(1_000_000.0),
            "Cash": _xbrl_entry(100.0),
            "AccountsReceivable": _xbrl_entry(90.0),
            "LongTermDebt": _xbrl_entry(80.0),
        }
        accounts = mat.detect_material_accounts(xbrl, sic="9999")
        assert all(a["ratio"] < 0.05 for a in accounts if a["ratio"] is not None)
        assert sum(1 for a in accounts if a["is_material"]) == len(accounts)  # all 3 < top-5 cap

    def test_generic_bucket_does_not_flag_accounts_beyond_top_n(self):
        xbrl = {"Revenue": _xbrl_entry(1000.0), "TotalAssets": _xbrl_entry(1000.0)}
        for i, m in enumerate(["Cash", "AccountsReceivable", "LongTermDebt",
                                "StockholdersEquity", "RetainedEarnings", "CurrentAssets", "CurrentLiabilities"]):
            xbrl[m] = _xbrl_entry(float(10 - i))  # strictly decreasing, all well under the 5% cutoff
        accounts = mat.detect_material_accounts(xbrl, sic="9999")
        assert sum(1 for a in accounts if a["is_material"]) == 5
        # the lowest-ratio ones (last two) should not be flagged
        ranked = sorted(accounts, key=lambda a: a["ratio"], reverse=True)
        assert ranked[-1]["is_material"] is False
        assert ranked[-2]["is_material"] is False


class TestRealBalancesForSox:
    def test_maps_material_accounts_onto_sox_account_ids(self):
        xbrl = _manufacturer_xbrl(inventory=500.0, total_assets=1000.0, cogs=600.0)
        accounts = mat.detect_material_accounts(xbrl, sic="3674")
        real = mat.real_balances_for_sox(accounts)
        assert real.get("inventory") == 500.0
        assert real.get("cogs") == 600.0

    def test_accounts_with_no_sox_mapping_are_excluded(self):
        xbrl = {"Revenue": _xbrl_entry(1000.0), "TotalAssets": _xbrl_entry(1000.0),
                "InterestIncome": _xbrl_entry(50.0)}
        accounts = mat.detect_material_accounts(xbrl, sic="6022")
        real = mat.real_balances_for_sox(accounts)
        assert "InterestIncome" not in real  # no sox_account_id for this metric


class TestForecastMaterialAccountsCap:
    def test_forecast_loop_is_capped_and_only_covers_material_accounts(self):
        accounts = [
            {"metric": f"M{i}", "is_material": True} for i in range(12)
        ] + [{"metric": "NotMaterial", "is_material": False}]

        calls = []

        def _fake_run_forecast_backtest(xbrl, macro_info, metric, horizon, company_id=None):
            calls.append(metric)
            return {"forecast": {"note": "stub"}}

        with patch("predictive_analytics_tool.run_forecast_backtest", side_effect=_fake_run_forecast_backtest):
            result = mat.forecast_material_accounts({}, None, accounts)

        assert len(calls) == mat._MAX_FORECAST_ACCOUNTS
        assert "NotMaterial" not in calls
        assert len(result) == mat._MAX_FORECAST_ACCOUNTS

    def test_forecast_errors_are_captured_per_account_not_raised(self):
        accounts = [{"metric": "Boom", "is_material": True}]
        with patch("predictive_analytics_tool.run_forecast_backtest", side_effect=ValueError("bad data")):
            result = mat.forecast_material_accounts({}, None, accounts)
        assert result["Boom"] == {"error": "bad data"}
