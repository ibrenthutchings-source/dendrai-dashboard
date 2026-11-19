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


def _segments():
    return [
        {"segment_name": "US", "segment_type": "geography", "revenue": 6000.0},
        {"segment_name": "EMEA", "segment_type": "geography", "revenue": 4000.0},
        {"segment_name": "Hardware", "segment_type": "business_segment", "revenue": 5000.0},
        {"segment_name": "Software", "segment_type": "product_line", "revenue": 5000.0},
    ]


class TestDefaultGeographyAndSegmentTagging:
    """A significant account should come pre-tagged with the entity's known
    geography/business-segment breakdown rather than sitting blank until an
    auditor hand-types it per account — there's no per-account mapping data
    source, so every in-scope account defaults to the full reported spread."""

    def test_always_in_scope_account_gets_full_geo_and_segment_defaults(self):
        result = sst.scope_accounts(_materiality(), _projections(), _ratios(), _risk_scores(),
                                     segments=_segments())
        rev = next(a for a in result if a["account_id"] == "revenue")
        assert rev["in_scope"] is True
        assert rev["geography"] == ["EMEA", "US"]
        # business_segment + product_line both count as "segments" (only
        # segment_type == "geography" is split out separately).
        assert rev["segments"] == ["Hardware", "Software"]

    def test_out_of_scope_account_gets_no_default_tags(self):
        # Performance materiality far above every account's balance, and
        # trivial low enough that nothing hits the "clearly inconsequential"
        # branch either — everything not always_scope falls to the plain
        # "below performance materiality; no elevated risk linkage" branch.
        result = sst.scope_accounts(_materiality(performance=9_999_999.0, trivial=1.0),
                                     _projections(), _ratios(), _risk_scores(),
                                     segments=_segments())
        goodwill = next(a for a in result if a["account_id"] == "goodwill")
        assert goodwill["in_scope"] is False
        assert goodwill["geography"] == []
        assert goodwill["segments"] == []

    def test_no_segment_data_yields_empty_defaults(self):
        result = sst.scope_accounts(_materiality(), _projections(), _ratios(), _risk_scores(),
                                     segments=None)
        rev = next(a for a in result if a["account_id"] == "revenue")
        assert rev["geography"] == []
        assert rev["segments"] == []

    def test_saved_detail_record_wins_over_default_even_when_cleared(self):
        # account_overrides carries an actual saved record (not None) for
        # "revenue" with both fields cleared out — that's a deliberate
        # auditor choice and must not be silently re-filled by the default.
        result = sst.scope_accounts(_materiality(), _projections(), _ratios(), _risk_scores(),
                                     segments=_segments(),
                                     account_overrides={"revenue": {"geography": [], "segments": []}})
        rev = next(a for a in result if a["account_id"] == "revenue")
        assert rev["geography"] == []
        assert rev["segments"] == []

    def test_saved_detail_record_with_values_still_wins_over_default(self):
        result = sst.scope_accounts(_materiality(), _projections(), _ratios(), _risk_scores(),
                                     segments=_segments(),
                                     account_overrides={"revenue": {"geography": ["APAC"], "segments": ["Services"]}})
        rev = next(a for a in result if a["account_id"] == "revenue")
        assert rev["geography"] == ["APAC"]
        assert rev["segments"] == ["Services"]


class TestScopeSegmentsOverrides:
    """Gate S1's per-segment HITL approval (sox-hitl.jsx) adjusts a segment's
    computed in-scope decision via segment_overrides, keyed by segment_key —
    mirroring scope_accounts' account_overrides pattern exactly."""

    def test_segment_key_is_type_colon_name(self):
        result = sst.scope_segments(_segments(), _materiality())
        us = next(s for s in result if s["segment_name"] == "US")
        assert us["segment_key"] == "geography:US"
        hw = next(s for s in result if s["segment_name"] == "Hardware")
        assert hw["segment_key"] == "business_segment:Hardware"

    def test_no_overrides_leaves_computed_decision_alone(self):
        result = sst.scope_segments(_segments(), _materiality(performance=9_999_999.0))
        us = next(s for s in result if s["segment_name"] == "US")
        assert us["manual_override"] is False

    def test_override_forces_out_of_scope(self):
        # US is a large enough segment to compute in-scope by default.
        result = sst.scope_segments(_segments(), _materiality(),
                                     segment_overrides={"geography:US": {"manual_in_scope": False, "notes": "n/a"}})
        us = next(s for s in result if s["segment_name"] == "US")
        assert us["in_scope"] is False
        assert us["manual_override"] is True
        assert "Manually overridden by user" in us["rationale"]
        assert us["notes"] == "n/a"

    def test_override_forces_in_scope(self):
        # Performance materiality set so high nothing computes in-scope on its own.
        result = sst.scope_segments(_segments(), _materiality(performance=9_999_999.0),
                                     segment_overrides={"business_segment:Hardware": {"manual_in_scope": True}})
        hw = next(s for s in result if s["segment_name"] == "Hardware")
        assert hw["in_scope"] is True
        assert hw["manual_override"] is True

    def test_unaffected_segments_keep_computed_decision(self):
        result = sst.scope_segments(_segments(), _materiality(),
                                     segment_overrides={"geography:US": {"manual_in_scope": False}})
        emea = next(s for s in result if s["segment_name"] == "EMEA")
        assert emea["manual_override"] is False

    def test_run_sox_scoping_threads_segment_overrides(self):
        out = sst.run_sox_scoping(
            run_id=None,
            forecast={"forecasts": []},
            risk_scores=_risk_scores(),
            ratios=_ratios(),
            segments=_segments(),
            segment_overrides={"geography:US": {"manual_in_scope": False}},
        )
        us = next(s for s in out["segments_coverage"] if s["segment_name"] == "US")
        assert us["in_scope"] is False
        assert us["manual_override"] is True
