#!/usr/bin/env python3
"""
Tests for sox_scoping_tool.scope_accounts()'s real_balances param — a real
detected balance (material_accounts_tool.py) should beat the heuristic
estimate, and dynamically-detected sub-accounts should only be scored when
real data actually exists for them (never estimated).

    pytest test_sox_scoping_tool.py -v
"""
from __future__ import annotations

import sox_scoping_tool as sst


def _materiality(planning=1000.0, performance=750.0, trivial=50.0):
    return {"planning_materiality": planning, "performance_materiality": performance,
            "trivial_threshold": trivial}


def _projections(revenue_fy=10000.0, assets_now=8000.0, gross_profit_fy=4000.0,
                  pretax_income_est=2000.0, cash_now=1000.0):
    return {"revenue_fy": revenue_fy, "assets_now": assets_now, "gross_profit_fy": gross_profit_fy,
            "pretax_income_est": pretax_income_est, "cash_now": cash_now}


def _ratios():
    return {"sga_intensity": 0.18}


def _risk_scores():
    return {"risks": []}


class TestRealBalanceOverridesHeuristic:
    def test_real_balance_used_instead_of_heuristic_estimate(self):
        projections = _projections(revenue_fy=10000.0)
        heuristic = sst._estimate_account_balance("inventory", projections, _ratios())  # 15% of revenue = 1500
        assert heuristic == 1500.0

        result = sst.scope_accounts(_materiality(), projections, _ratios(), _risk_scores(),
                                     real_balances={"inventory": 9999.0})
        inv = next(a for a in result if a["account_id"] == "inventory")
        assert inv["balance_estimate"] == 9999
        assert inv["balance_source"] == "real"

    def test_falls_back_to_heuristic_when_no_real_balance_present(self):
        projections = _projections(revenue_fy=10000.0)
        result = sst.scope_accounts(_materiality(), projections, _ratios(), _risk_scores(),
                                     real_balances={})
        inv = next(a for a in result if a["account_id"] == "inventory")
        assert inv["balance_estimate"] == 1500  # 15% of revenue, the heuristic
        assert inv["balance_source"] == "estimated"

    def test_real_balances_none_behaves_like_empty(self):
        projections = _projections(revenue_fy=10000.0)
        result = sst.scope_accounts(_materiality(), projections, _ratios(), _risk_scores(),
                                     real_balances=None)
        inv = next(a for a in result if a["account_id"] == "inventory")
        assert inv["balance_source"] == "estimated"


class TestDynamicSubAccounts:
    def test_subaccount_with_real_data_is_added_and_scored(self):
        projections = _projections()
        result = sst.scope_accounts(_materiality(), projections, _ratios(), _risk_scores(),
                                     real_balances={"inventory_raw_materials": 200.0})
        sub = next((a for a in result if a["account_id"] == "inventory_raw_materials"), None)
        assert sub is not None
        assert sub["balance_estimate"] == 200
        assert sub["balance_source"] == "real"

    def test_subaccount_with_no_real_data_is_absent_not_estimated(self):
        result = sst.scope_accounts(_materiality(), _projections(), _ratios(), _risk_scores(),
                                     real_balances={})
        ids = {a["account_id"] for a in result}
        assert "inventory_raw_materials" not in ids
        assert "inventory_work_in_process" not in ids
        assert "inventory_finished_goods" not in ids

    def test_subaccount_inherits_parents_risk_categories(self):
        result = sst.scope_accounts(_materiality(), _projections(), _ratios(), _risk_scores(),
                                     real_balances={"inventory_raw_materials": 200.0})
        sub = next(a for a in result if a["account_id"] == "inventory_raw_materials")
        parent = next(a for a in result if a["account_id"] == "inventory")
        assert sub["rag_linkage"] is not None  # scoring ran without error using parent's risk_categories
        # Both should react the same way to the same (empty) risk set here.
        assert sub["rag_linkage"] == parent["rag_linkage"]

    def test_fixed_accounts_unaffected_by_dynamic_extension(self):
        result = sst.scope_accounts(_materiality(), _projections(), _ratios(), _risk_scores(),
                                     real_balances={"inventory_raw_materials": 200.0})
        assert sum(1 for a in result if a["account_id"] == "inventory") == 1
        # every fixed SOX_ACCOUNTS id is still present exactly once
        fixed_ids = {a["id"] for a in sst.SOX_ACCOUNTS}
        result_ids = [a["account_id"] for a in result]
        for fid in fixed_ids:
            assert result_ids.count(fid) == 1


class TestRunSoxScopingThreadsRealBalances:
    def test_run_sox_scoping_passes_real_balances_through(self):
        out = sst.run_sox_scoping(
            run_id=None,
            forecast={"forecasts": []},
            risk_scores=_risk_scores(),
            ratios=_ratios(),
            real_balances={"inventory": 12345.0},
        )
        inv = next(a for a in out["accounts_in_scope"] if a["account_id"] == "inventory")
        assert inv["balance_estimate"] == 12345
        assert inv["balance_source"] == "real"
