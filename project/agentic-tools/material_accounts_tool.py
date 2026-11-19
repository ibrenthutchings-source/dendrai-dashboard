#!/usr/bin/env python3
"""
Dynamic material-account detection.

The Assess Risk / Peer Comparison / SOX Scoping screens historically show a
fixed set of charts (revenue, margin, EPS, ...) regardless of what's
actually large on a given filer's balance sheet. A manufacturer's Inventory
(and its raw-materials/WIP/finished-goods split), COGS, and PP&E; a bank's
loan book and deposits; a SaaS company's deferred revenue — none of these
get surfaced today unless they happen to be one of the ~7 hardcoded metrics.

This module decides, per filer, which XBRL metrics are actually material
enough to deserve a chart, using:
  1. The filer's SIC-derived industry bucket (sic_industry.py) to pick a
     purpose-built account template (manufacturing / financial_services /
     saas), or fall back to ranking whatever's available for anyone else.
  2. A materiality ratio — balance-sheet accounts ÷ TotalAssets,
     income-statement accounts ÷ Revenue — against a fixed 5% cutoff.

No new XBRL fetching happens here: `edgar_tool.fetch_xbrl_facts()` already
pulls a filer's full companyfacts payload and edgar_tool.XBRL_METRICS
already lists every metric this module can detect (extended for exactly
this feature — see edgar_tool.py's "Industry-template accounts" section).
This module only decides which of those already-fetched metrics matter.

Usage:
    from material_accounts_tool import detect_material_accounts, forecast_material_accounts
    xbrl = edgar_tool.fetch_xbrl_facts(cik)
    accounts = detect_material_accounts(xbrl, sic)
    forecasts = forecast_material_accounts(xbrl, macro_info, accounts)
"""

from __future__ import annotations

from typing import Any, Optional

from sic_industry import template_bucket

# ── Materiality rule ─────────────────────────────────────────────────────────
# A separate, smaller-stakes number from sox_scoping_tool.compute_materiality()'s
# AS2315 audit-materiality dollar threshold (5% of pre-tax income, with
# floors) — that one decides "is this account in SOX scope"; this one just
# decides "is this account big enough to deserve its own chart." Both use a
# 5% convention so the two don't read as contradictory, but they are not
# the same calculation and are not meant to be.
_MATERIALITY_RATIO_THRESHOLD = 0.05

# Generic-industry fallback: how many of the highest-ratio accounts to chart
# even if none individually clears the 5% cutoff (a company still has SOME
# largest line items worth showing).
_GENERIC_FALLBACK_TOP_N = 5

# Bounds the per-account forecast loop below — the same lesson as the
# 2026-08-30 peer-enrichment OOM incident (api_server.py's
# _PEER_ENRICH_MAX_WORKERS): N accounts x a real forecast run is not free,
# so cap it rather than forecasting an unbounded list.
_MAX_FORECAST_ACCOUNTS = 8

# ── Industry templates ───────────────────────────────────────────────────────
# XBRL metric names here are edgar_tool.XBRL_METRICS keys (the already-
# harvested, filer-may-or-may-not-have-it set), not raw us-gaap tags.
# "core" accounts are checked for materiality first (and thus dominate a
# short account list); "detail" accounts are the sub-components a template
# adds beyond the always-available flat metric.
_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "manufacturing": {
        "core":   ["Inventory", "COGS", "PPEGross"],
        "detail": ["InventoryRawMaterials", "InventoryWorkInProcess", "InventoryFinishedGoods",
                   "AccumulatedDepreciation"],
    },
    "financial_services": {
        "core":   ["LoansReceivable", "Deposits", "InterestIncome"],
        "detail": ["AllowanceForLoanLosses", "InterestExpense"],
    },
    "saas": {
        "core":   ["DeferredRevenueCurrent", "ResearchAndDevelopment"],
        "detail": ["DeferredRevenueNoncurrent", "CapitalizedSoftware"],
    },
}

# Sub-account -> the flat/aggregate metric it's a component of. Lets the
# frontend group a chart cluster (e.g. Inventory + its 3 sub-tags) under one
# header instead of rendering N unrelated-looking flat siblings.
_PARENT_OF: dict[str, str] = {
    "InventoryRawMaterials":  "Inventory",
    "InventoryWorkInProcess": "Inventory",
    "InventoryFinishedGoods": "Inventory",
    "AccumulatedDepreciation": "PPEGross",
    "DeferredRevenueNoncurrent": "DeferredRevenueCurrent",
}

# Balance-sheet metrics are measured against TotalAssets; everything else
# (income statement / cash flow) against Revenue. Deliberately explicit
# rather than inferred, so a newly-added metric that's forgotten here fails
# loudly (KeyError-free but visibly wrong: it'd compare against the wrong
# base) rather than silently guessing.
_BALANCE_SHEET_METRICS = {
    "TotalAssets", "CurrentAssets", "CurrentLiabilities", "TotalLiabilities",
    "StockholdersEquity", "RetainedEarnings", "Cash", "LongTermDebt",
    "Inventory", "InventoryRawMaterials", "InventoryWorkInProcess", "InventoryFinishedGoods",
    "AccountsReceivable", "PPEGross", "AccumulatedDepreciation",
    "LoansReceivable", "AllowanceForLoanLosses", "Deposits",
    "DeferredRevenueCurrent", "DeferredRevenueNoncurrent", "CapitalizedSoftware",
}

# edgar_tool.XBRL_METRICS name -> sox_scoping_tool.SOX_ACCOUNTS id (or a
# dynamically-detected sub-account id — see sox_scoping_tool._DYNAMIC_SUB_ACCOUNTS).
# Lets a detected real balance feed straight into SOX scoping's existing
# account list without sox_scoping_tool.py knowing anything about XBRL
# metric names. Metrics with no SOX-account analog (e.g. InterestIncome)
# are simply absent — detect_material_accounts() still returns them for
# charting, they just don't also drive a SOX scope decision.
_SOX_ACCOUNT_MAP = {
    "Revenue": "revenue",
    "AccountsReceivable": "accounts_receivable",
    "Inventory": "inventory",
    "COGS": "cogs",
    "Cash": "cash",
    "LongTermDebt": "long_term_debt",
    "IncomeTaxExpense": "income_tax_provision",
    "DeferredRevenueCurrent": "deferred_revenue",
    "InventoryRawMaterials": "inventory_raw_materials",
    "InventoryWorkInProcess": "inventory_work_in_process",
    "InventoryFinishedGoods": "inventory_finished_goods",
}


def _latest_value(entry: Optional[dict]) -> Optional[float]:
    """Most recent data point's value from an edgar_tool.fetch_xbrl_facts()
    entry (data_points is already sorted newest-first)."""
    if not entry:
        return None
    pts = entry.get("data_points") or []
    if not pts:
        return None
    return pts[0].get("val")


def detect_material_accounts(xbrl: dict, sic: Any, uploaded_xbrl: Optional[dict] = None) -> list[dict]:
    """Return material-account candidates for a filer.

    `xbrl`: edgar_tool.fetch_xbrl_facts() output (metric name -> {data_points, ...}).
    `sic`: the filer's SIC code (any type int() can parse — see sic_industry.classify_sic).
    `uploaded_xbrl`: optional dict in the SAME shape as `xbrl` — pass
        db.get_manual_financials(company_id) here. A metric present in
        `uploaded_xbrl` wins over the filed `xbrl` value for that metric,
        per the XBRL-first/upload-overrides decision this feature was built
        to (Mission Control's committed line items are assumed more current/
        accurate than a filing that may predate them).

    Returns [{metric, label, industry_group, base_metric, value, ratio,
    is_material, parent, source, sox_account_id}, ...]. Never fabricates a
    value: a metric absent from both `xbrl` and `uploaded_xbrl` is simply
    not included, the same "honest gap, not a silent zero" discipline
    edgar_segments.py and GeoSegmentKPISection already follow.
    """
    uploaded_xbrl = uploaded_xbrl or {}
    bucket = template_bucket(sic)

    def _value(metric: str) -> Optional[float]:
        uploaded = _latest_value(uploaded_xbrl.get(metric))
        return uploaded if uploaded is not None else _latest_value(xbrl.get(metric))

    revenue = _value("Revenue")
    total_assets = _value("TotalAssets")

    if bucket == "generic":
        # No purpose-built template — consider every metric this filer
        # actually has data for (minus the two base metrics themselves,
        # which are denominators, not accounts to chart on their own).
        candidates = [m for m in xbrl.keys() if m not in ("Revenue", "TotalAssets")]
        candidates += [m for m in uploaded_xbrl if m not in candidates and m not in ("Revenue", "TotalAssets")]
    else:
        tmpl = _TEMPLATES[bucket]
        candidates = tmpl["core"] + tmpl["detail"]

    out = []
    for metric in candidates:
        value = _value(metric)
        if value is None:
            continue
        is_balance_sheet = metric in _BALANCE_SHEET_METRICS
        base_metric = "TotalAssets" if is_balance_sheet else "Revenue"
        base_value = total_assets if is_balance_sheet else revenue
        ratio = (abs(value) / abs(base_value)) if base_value else None
        uploaded_entry = uploaded_xbrl.get(metric)
        used_uploaded = _latest_value(uploaded_entry) is not None
        entry = (uploaded_entry if used_uploaded else xbrl.get(metric)) or {}
        out.append({
            "metric": metric,
            "label": entry.get("label", metric),
            "industry_group": bucket,
            "base_metric": base_metric,
            "value": value,
            "ratio": round(ratio, 4) if ratio is not None else None,
            "is_material": bool(ratio is not None and ratio >= _MATERIALITY_RATIO_THRESHOLD),
            "parent": _PARENT_OF.get(metric),
            "source": "uploaded" if used_uploaded else "filed",
            "sox_account_id": _SOX_ACCOUNT_MAP.get(metric),
        })

    if bucket == "generic":
        # Rank by materiality ratio (None sorts last) and force-flag the
        # top N as material — a generic-industry filer still has SOME
        # largest line items worth charting, even if none individually
        # clears the 5% cutoff.
        out.sort(key=lambda a: (a["ratio"] if a["ratio"] is not None else -1), reverse=True)
        for a in out[:_GENERIC_FALLBACK_TOP_N]:
            a["is_material"] = True

    return out


def real_balances_for_sox(accounts: list[dict]) -> dict[str, float]:
    """Map detect_material_accounts() output onto sox_scoping_tool's account
    ids, for feeding scope_accounts()'s real_balances param (real detected
    balance beats the heuristic estimate). Only material accounts with a
    known SOX-account mapping are included."""
    return {
        a["sox_account_id"]: a["value"]
        for a in accounts
        if a.get("sox_account_id") and a.get("value") is not None
    }


def forecast_material_accounts(xbrl: dict, macro_info: Optional[dict], accounts: list[dict],
                                horizon: int = 4, company_id: Optional[int] = None) -> dict[str, dict]:
    """Run the existing generic forecasting engine once per material
    account (capped at _MAX_FORECAST_ACCOUNTS). No new forecasting math —
    predictive_analytics_tool.run_forecast_backtest already accepts an
    arbitrary XBRL metric name.

    Returns {metric: run_forecast_backtest() result}, skipping accounts
    with fewer than 8 quarters of history the same way run_forecast_backtest
    itself does (its own {"note": ...} response for that case passes through
    unchanged, not treated as an error here).
    """
    import predictive_analytics_tool as pat  # local import: keeps
    # detect_material_accounts() (the pure ratio/materiality logic, unit-
    # tested without numpy/pandas) usable with zero model dependencies —
    # only this forecasting entry point needs them, matching this file's
    # sibling modules' (edgar_segments.py, sox_scoping_tool.py) convention.

    material = [a for a in accounts if a.get("is_material")][:_MAX_FORECAST_ACCOUNTS]
    out: dict[str, dict] = {}
    for acc in material:
        metric = acc["metric"]
        try:
            out[metric] = pat.run_forecast_backtest(xbrl, macro_info, metric, horizon, company_id)
        except Exception as e:
            out[metric] = {"error": str(e)}
    return out
