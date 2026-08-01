#!/usr/bin/env python3
"""
Predictive Analytics Tool — Dendrai Intelligenza

Implements:
  1.  Financial Ratio Analysis        (EDGAR XBRL)
  2.  Beneish M-Score                 (earnings manipulation detection)
  3.  Industry-Templated Risk Scoring (8 verticals × 8 risks each)
  4.  Scenario Analysis               (Bear / Base / Bull)
  5.  Grey Swan Model                 (4-stage escalation cascade T+0→T+90)
  6.  FRED Macro Leading Indicators   (cross-correlation at lags 1–4)
  7.  Time-Series Forecasting         (ARIMA, Prophet-like, Random Forest, Ensemble)
  8.  Walk-Forward Backtesting        (MAPE, RMSE, R², directional F1)
  9.  RSS Signal Grading              (relevance × severity NLP-lite pipeline)
  10. QoQ Revenue Momentum / Sentiment
"""

import json
import math
import os
import random
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import feedparser
    _HAS_FEEDPARSER = True
except ImportError:
    _HAS_FEEDPARSER = False

sys.path.insert(0, os.path.dirname(__file__))

try:
    from edgar_tool import get_company_info, fetch_xbrl_facts
    _HAS_EDGAR = True
except ImportError:
    _HAS_EDGAR = False

try:
    from fred_tool import (
        run_analysis as _fred_run_analysis,
        FRED_SERIES,
        _add_quarters as _fred_add_quarters,
        _date_to_quarter_end as _fred_date_to_qend,
    )
    _HAS_FRED = True
except ImportError:
    _HAS_FRED = False

HEADERS = {"User-Agent": "PredictiveAnalyticsTool/1.0 (research@example.com)"}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — FINANCIAL RATIO ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════


# "MANUAL-A"/"MANUAL-Q" are written by manual_financials_tool.py's
# commit_line_items for user-uploaded annual/quarterly data (private-company
# financials, or a supplement/correction to a public company's EDGAR data —
# see build_company_xbrl). Distinct tags per granularity, not one shared
# "MANUAL" tag, so an annual entry can never accidentally satisfy the
# quarterly-only filter below purely because form-string membership is the
# primary gate here.
_ANNUAL_FORMS    = {"10-K", "20-F", "10-K/A", "MANUAL-A"}
_QUARTERLY_FORMS = {"10-Q", "10-Q/A", "MANUAL-Q"}


def _annual_pts(metric_data: dict) -> list:
    return [
        p for p in metric_data.get("data_points", [])
        if p.get("form") in _ANNUAL_FORMS and p.get("val") is not None
    ]

def _quarterly_pts(metric_data: dict) -> list:
    """Return only standalone quarterly entries (Q1/Q2/Q3/Q4), not YTD cumulative (H1, 9M, etc.).

    EDGAR companyfacts entries do not include a 'start' field, so period-length filtering
    is not reliable. Use the 'fp' field as the primary discriminator: only Q1/Q2/Q3/Q4
    are standalone quarters. When 'start' IS present (some concept-level APIs), also
    apply a 60-110 day period-length guard as a secondary check.
    """
    result = []
    for p in metric_data.get("data_points", []):
        if p.get("form") not in _QUARTERLY_FORMS:
            continue
        if p.get("val") is None:
            continue
        # Primary filter: fp must be a standalone quarter designation
        fp = p.get("fp", "")
        if fp and fp not in {"Q1", "Q2", "Q3", "Q4"}:
            continue
        # Secondary filter: period-length check when start date is available
        start, end = p.get("start"), p.get("end")
        if start and end:
            try:
                from datetime import date as _date
                days = (_date.fromisoformat(end) - _date.fromisoformat(start)).days
                if not (60 <= days <= 110):   # reject H1 (~180d) and 9M (~270d)
                    continue
            except Exception:
                pass
        result.append(p)
    return result

def _latest(pts: list) -> Optional[float]:
    if not pts:
        return None
    return max(pts, key=lambda p: p.get("end", ""))["val"]

def _prev(pts: list) -> Optional[float]:
    if len(pts) < 2:
        return None
    srt = sorted(pts, key=lambda p: p.get("end", ""), reverse=True)
    return srt[1]["val"]

def _safe_div(n, d):
    if n is None or d is None or d == 0:
        return None
    return n / d

def compute_financial_ratios(xbrl: dict) -> dict:
    """
    Compute all Dendrai Intelligenza financial ratios from EDGAR XBRL data.
    Returns dict of ratio names → float | None.
    """
    def _a(m): return _annual_pts(xbrl.get(m, {}))
    def _q(m): return _quarterly_pts(xbrl.get(m, {}))

    rev_a    = _a("Revenue")
    gp_a     = _a("GrossProfit")
    op_a     = _a("OperatingIncome")
    ni_a     = _a("NetIncome")
    rd_a     = _a("ResearchAndDevelopment")
    cfo_a    = _a("OperatingCashFlow")
    capex_a  = _a("CapEx")
    assets_a = _a("TotalAssets")
    cash_a   = _a("Cash")
    ar_a     = _a("AccountsReceivable")
    cur_assets_a = _a("CurrentAssets")
    cur_liab_a   = _a("CurrentLiabilities")
    tot_liab_a   = _a("TotalLiabilities")
    equity_a     = _a("StockholdersEquity")
    retained_a   = _a("RetainedEarnings")

    rev_now   = _latest(rev_a)
    rev_prev  = _prev(rev_a)
    gp_now    = _latest(gp_a)
    gp_prev   = _prev(gp_a)
    op_now    = _latest(op_a)
    ni_now    = _latest(ni_a)
    rd_now    = _latest(rd_a)
    cfo_now   = _latest(cfo_a)
    capex_now = _latest(capex_a)
    assets_now  = _latest(assets_a)
    assets_prev = _prev(assets_a)
    cash_now  = _latest(cash_a)
    ar_now    = _latest(ar_a)
    ar_prev   = _prev(ar_a)
    cur_assets_now = _latest(cur_assets_a)
    cur_liab_now   = _latest(cur_liab_a)
    tot_liab_now   = _latest(tot_liab_a)
    equity_now     = _latest(equity_a)
    retained_now   = _latest(retained_a)

    # SGA ≈ GrossProfit - OperatingIncome
    sga_now = (gp_now - op_now) if (gp_now is not None and op_now is not None) else None

    # FCF = CFO - CapEx  (CapEx often negative in EDGAR, so use abs)
    fcf_now = None
    if cfo_now is not None and capex_now is not None:
        fcf_now = cfo_now - abs(capex_now)

    # ── Derived ratios ───────────────────────────────────────────────────────
    revenue_growth  = _safe_div(rev_now - rev_prev, rev_prev) if (rev_now and rev_prev) else None
    gross_margin    = _safe_div(gp_now, rev_now)
    gross_margin_p  = _safe_div(gp_prev, rev_prev)
    rd_intensity    = _safe_div(rd_now, rev_now)
    sga_intensity   = _safe_div(sga_now, rev_now)
    net_margin      = _safe_div(ni_now, rev_now)
    fcf_margin      = _safe_div(fcf_now, rev_now)
    asset_growth    = _safe_div(assets_now - assets_prev, assets_prev) if (assets_now and assets_prev) else None
    cash_ratio      = _safe_div(cash_now, assets_now)

    # TATA = (NetIncome - CFO) / TotalAssets
    tata = None
    if ni_now is not None and cfo_now is not None and assets_now:
        tata = (ni_now - cfo_now) / assets_now

    # DSRI = (AR/Rev)_now / (AR/Rev)_prev
    dsr_now  = _safe_div(ar_now, rev_now)
    dsr_prev = _safe_div(ar_prev, rev_prev)
    dsri = _safe_div(dsr_now, dsr_prev)

    # SGI = Rev_now / Rev_prev
    sgi = _safe_div(rev_now, rev_prev)

    # GMI = GrossMargin_prev / GrossMargin_now  (rising GMI → deteriorating margin)
    gmi = _safe_div(gross_margin_p, gross_margin)

    # Altman Z''-Score inputs (general/non-manufacturer variant, book equity —
    # no market-cap dependency)
    working_capital = (cur_assets_now - cur_liab_now) if (cur_assets_now is not None and cur_liab_now is not None) else None
    zscore_x1 = _safe_div(working_capital, assets_now)
    zscore_x2 = _safe_div(retained_now, assets_now)
    zscore_x3 = _safe_div(op_now, assets_now)
    zscore_x4 = _safe_div(equity_now, tot_liab_now)

    return {
        "revenue_now":        rev_now,
        "revenue_prev":       rev_prev,
        "revenue_growth":     revenue_growth,
        "gross_margin":       gross_margin,
        "gross_margin_prev":  gross_margin_p,
        "gross_margin_index": gmi,
        "rd_intensity":       rd_intensity,
        "sga_intensity":      sga_intensity,
        "net_margin":         net_margin,
        "fcf_margin":         fcf_margin,
        "asset_growth":       asset_growth,
        "cash_ratio":         cash_ratio,
        "tata":               tata,
        "dsri":               dsri,
        "sgi":                sgi,
        "assets_now":         assets_now,
        "cash_now":           cash_now,
        "net_income_now":     ni_now,
        "operating_cashflow": cfo_now,
        "zscore_x1":          zscore_x1,
        "zscore_x2":          zscore_x2,
        "zscore_x3":          zscore_x3,
        "zscore_x4":          zscore_x4,
    }


def extract_quarterly_series(xbrl: dict, metric: str) -> list[dict]:
    """Return [{quarter_end, value}, ...] sorted oldest-first, deduplicated by period end."""
    pts = _quarterly_pts(xbrl.get(metric, {}))
    # Keep the most recently filed entry per period end date (amended filings supersede originals)
    by_end: dict = {}
    for p in pts:
        end = p.get("end")
        if not end:
            continue
        if end not in by_end or p.get("filed", "") > by_end[end].get("filed", ""):
            by_end[end] = p
    pts_sorted = sorted(by_end.values(), key=lambda p: p.get("end", ""))
    return [{"quarter_end": p["end"], "value": p["val"], "start": p.get("start")} for p in pts_sorted]


def extract_monthly_series(company_id: Optional[int], metric: str) -> list[dict]:
    """Return [{month_end, value}, ...] sorted oldest-first, from manually
    uploaded monthly data points only. SEC XBRL has no monthly granularity, so
    unlike extract_quarterly_series this never touches live EDGAR data — it
    feeds only the forecast/backtest/QoQ-momentum stage (finer time
    resolution than quarterly where a user has entered it); the ratio/
    Beneish/Altman models stay annual/quarterly as before, see
    build_company_xbrl. Returns [] when there's no DB, no company_id, or no
    monthly data uploaded for this metric — same "degrade to nothing" pattern
    every other sparse-data path in this file already follows."""
    if not company_id:
        return []
    try:
        import db
    except ImportError:
        return []
    if not db.is_available():
        return []
    monthly = db.get_manual_financials(company_id, granularity=["monthly"])
    pts = monthly.get(metric, {}).get("data_points", [])
    by_end: dict = {}
    for p in pts:
        end = p.get("end")
        if not end or p.get("val") is None:
            continue
        if end not in by_end or (p.get("filed") or "") > (by_end[end].get("filed") or ""):
            by_end[end] = p
    pts_sorted = sorted(by_end.values(), key=lambda p: p.get("end", ""))
    return [{"month_end": p["end"], "value": p["val"], "start": p.get("start")} for p in pts_sorted]


def _merge_xbrl_manual(xbrl: dict, manual: dict) -> dict:
    """Overlay manually-uploaded annual/quarterly points onto live EDGAR data.
    Manual wins on a period_end collision for the same metric — a user
    entering a restated or more precise figure supersedes the as-filed EDGAR
    value rather than the two silently coexisting as duplicate history
    points feeding the same ratio calculation."""
    if not manual:
        return xbrl
    merged = {k: {**v, "data_points": list(v.get("data_points", []))} for k, v in xbrl.items()}
    for metric, m_entry in manual.items():
        base_entry = merged.setdefault(metric, {
            "tag": m_entry.get("tag") or metric, "label": metric,
            "unit": m_entry.get("unit", "USD"), "data_points": [],
        })
        manual_ends = {p["end"] for p in m_entry.get("data_points", []) if p.get("end")}
        base_entry["data_points"] = [
            p for p in base_entry["data_points"] if p.get("end") not in manual_ends
        ] + m_entry.get("data_points", [])
    return merged


def build_company_xbrl(ticker: str) -> tuple[dict, dict]:
    """Resolve a ticker to XBRL-shaped financial data plus company metadata,
    branching on whether it's a real SEC filer or a private company created
    via db.upsert_private_company (synthetic PVT-<SLUG> ticker, no CIK).
    Private companies skip the EDGAR round-trip entirely and run purely on
    manually-uploaded data; public companies get live EDGAR XBRL overlaid
    with any manual annual/quarterly corrections/supplements a user has
    uploaded (see _merge_xbrl_manual). Returns (xbrl, meta) where meta has
    company_name/cik/sic/sic_description/is_private/company_id — cik and
    company_id are None for a private company not yet persisted, or for a
    public company never previously looked up (DB unavailable/not yet upserted)."""
    try:
        import db
        has_db = db.is_available()
    except ImportError:
        db = None
        has_db = False

    if has_db and db.is_private_ticker(ticker):
        company_meta = db.get_company_meta(ticker)
        if not company_meta:
            raise ValueError(
                f"Unknown private company ticker '{ticker}' — create it via POST /company/private first"
            )
        xbrl = db.get_manual_financials(company_meta["id"], granularity=["annual", "quarterly"])
        meta = {
            "company_name": company_meta["company_name"], "cik": None,
            "sic": company_meta.get("sic") or "", "sic_description": company_meta.get("sic_description") or "",
            "is_private": True, "company_id": company_meta["id"],
        }
        return xbrl, meta

    meta_edgar, _ = get_company_info(ticker)
    xbrl = fetch_xbrl_facts(meta_edgar["cik"])
    company_id = None
    if has_db:
        company_id = db.get_company_id(ticker)
        if company_id:
            manual = db.get_manual_financials(company_id, granularity=["annual", "quarterly"])
            xbrl = _merge_xbrl_manual(xbrl, manual)
    meta = {
        "company_name": meta_edgar["company_name"], "cik": meta_edgar["cik"],
        "sic": meta_edgar.get("sic", ""), "sic_description": meta_edgar.get("sic_description", ""),
        "is_private": False, "company_id": company_id,
    }
    return xbrl, meta


def _add_quarters_to_qend(qend: str, n: int) -> str:
    """Add n quarters to a 'YYYY-MM-DD' quarter-end date string. Self-
    contained (doesn't reuse fred_tool's _add_quarters) so forecast-accuracy
    recording works regardless of whether FRED is configured/importable."""
    y, m, d = (int(x) for x in qend.split("-"))
    total_months = (y * 12 + (m - 1)) + n * 3
    new_y, new_m0 = divmod(total_months, 12)
    new_m = new_m0 + 1
    next_month = datetime(new_y + 1, 1, 1) if new_m == 12 else datetime(new_y, new_m + 1, 1)
    return (next_month - timedelta(days=1)).date().isoformat()


def _record_and_reconcile_forecasts(ticker: Optional[str], company_id: Optional[int],
                                     metric_name: str, q_series: list, fc: dict) -> None:
    """Best-effort forecast-accuracy bookkeeping: reconcile past forecasts
    for this metric against the quarterly actuals just extracted (so a
    forecast made N quarters ago gets its real outcome filled in once it's
    available), then record this run's new point forecasts — the ensemble
    blend and each component leg — for future reconciliation. See
    forecast_accuracy_history's DDL comment in db.py for why this exists:
    walk_forward_backtest's own accuracy numbers are recomputed over the
    same fixed history every time, capped at a handful of out-of-sample
    steps for this platform's typical ~13-quarter series; this table is what
    lets genuinely new evidence accumulate quarter over quarter instead.
    Never raises — this bookkeeping must not break the forecast itself."""
    if not ticker:
        return
    try:
        import db
        if not db.is_available():
            return
    except ImportError:
        return
    try:
        db.reconcile_forecast_actuals(ticker, metric_name, q_series)
    except Exception:
        pass
    try:
        last_qend = q_series[-1]["quarter_end"] if q_series else None
        if not last_qend:
            return
        for entry in fc.get("forecasts", []):
            h = entry.get("horizon")
            if h is None:
                continue
            target_qend = _add_quarters_to_qend(last_qend, h)
            db.record_forecast(ticker, metric_name, target_qend, h, "Ensemble", entry["point"], company_id)
            for model_name, comp in (fc.get("components") or {}).items():
                pts = comp.get("forecasts", [])
                if h - 1 < len(pts):
                    db.record_forecast(ticker, metric_name, target_qend, h, model_name, pts[h - 1]["point"], company_id)
    except Exception:
        pass


def compute_analyst_series(xbrl: dict, rev_q_series: list, macro_info: Optional[dict] = None,
                            forecast_horizon: int = 4, ticker: Optional[str] = None,
                            company_id: Optional[int] = None) -> dict:
    """
    Compute analyst KPI quarterly time series from XBRL:
      eps, op_income, op_margin, net_income, fcf, ebitda
    Each entry: [{quarter_end, value}, ...]
    Forecast keys (eps_forecast, eps_backtest, etc.) added when ≥8 quarters
    available. `macro_info` is the live FRED result dict (correlation_results
    + fred_macro_series) from get_macro_leading_indicators, when available —
    used to feed lag-aligned FRED features into the Random Forest leg for
    metrics fred_tool.py has correlation hits for (EPS_Diluted/EPS_Basic,
    NetIncome, EBITDA).
    """
    result: dict = {}
    rev_map = {p["quarter_end"]: p["value"] for p in (rev_q_series or [])}

    # EPS (diluted preferred, fall back to basic)
    for eps_key in ("EPS_Diluted", "EPS_Basic"):
        eps_q = extract_quarterly_series(xbrl, eps_key)
        if eps_q:
            result["eps"] = eps_q
            vals = [p["value"] for p in eps_q]
            if len(vals) >= 8:
                try:
                    fred_matrix = fred_meta = None
                    if macro_info:
                        fred_matrix, fred_meta = _build_fred_feature_matrix(
                            eps_q, macro_info, eps_key, forecast_horizon)
                    bt  = walk_forward_backtest(vals, fred_matrix=fred_matrix)
                    fc  = compute_ensemble_forecast(vals, horizon=4, weights=bt.get("calibrated_weights"),
                                                     fred_matrix=fred_matrix, backtest_sigmas=backtest_sigmas_from(bt))
                    if fred_meta:
                        fc["fred_features_used"] = fred_meta
                    result["eps_forecast"] = fc
                    result["eps_backtest"] = bt
                    _record_and_reconcile_forecasts(ticker, company_id, "eps", eps_q, fc)
                except Exception:
                    pass
            break

    # Operating Income → Operating Margin %
    oi_q = extract_quarterly_series(xbrl, "OperatingIncome")
    if oi_q:
        result["op_income"] = oi_q
        op_margin = []
        for p in oi_q:
            rv = rev_map.get(p["quarter_end"])
            if rv and rv > 0:
                op_margin.append({"quarter_end": p["quarter_end"], "value": round(p["value"] / rv * 100, 2)})
        if op_margin:
            result["op_margin"] = op_margin
            om_vals = [p["value"] for p in op_margin]
            if len(om_vals) >= 8:
                try:
                    # Op Margin = OperatingIncome / Revenue has no correlation
                    # entry of its own (fred_tool.py only correlates raw XBRL
                    # metrics) — borrow OperatingIncome's correlated indicators
                    # and lags, aligned to op_margin's own quarter positions.
                    fred_matrix = fred_meta = None
                    if macro_info:
                        fred_matrix, fred_meta = _build_fred_feature_matrix(
                            op_margin, macro_info, "OperatingIncome", forecast_horizon)
                    om_bt = walk_forward_backtest(om_vals, fred_matrix=fred_matrix)
                    om_fc = compute_ensemble_forecast(
                        om_vals, horizon=4,
                        weights=om_bt.get("calibrated_weights"), fred_matrix=fred_matrix,
                        backtest_sigmas=backtest_sigmas_from(om_bt),
                    )
                    if fred_meta:
                        om_fc["fred_features_used"] = fred_meta
                    result["op_margin_forecast"] = om_fc
                    result["op_margin_backtest"] = om_bt
                    _record_and_reconcile_forecasts(ticker, company_id, "op_margin", op_margin, om_fc)
                except Exception:
                    pass

    # Net Income
    ni_q = extract_quarterly_series(xbrl, "NetIncome")
    if ni_q:
        result["net_income"] = ni_q
        ni_vals = [p["value"] for p in ni_q]
        if len(ni_vals) >= 8:
            try:
                fred_matrix = fred_meta = None
                if macro_info:
                    fred_matrix, fred_meta = _build_fred_feature_matrix(
                        ni_q, macro_info, "NetIncome", forecast_horizon)
                ni_bt = walk_forward_backtest(ni_vals, fred_matrix=fred_matrix)
                ni_fc = compute_ensemble_forecast(ni_vals, horizon=4, weights=ni_bt.get("calibrated_weights"),
                                                   fred_matrix=fred_matrix, backtest_sigmas=backtest_sigmas_from(ni_bt))
                if fred_meta:
                    ni_fc["fred_features_used"] = fred_meta
                result["net_income_forecast"] = ni_fc
                result["net_income_backtest"] = ni_bt
                _record_and_reconcile_forecasts(ticker, company_id, "net_income", ni_q, ni_fc)
            except Exception:
                pass

    # FCF = CFO − |CapEx|  (match by quarter_end)
    cfo_q   = extract_quarterly_series(xbrl, "OperatingCashFlow")
    capex_q = extract_quarterly_series(xbrl, "CapEx")
    if cfo_q and capex_q:
        cfo_map = {p["quarter_end"]: p["value"] for p in cfo_q}
        fcf = [
            {"quarter_end": p["quarter_end"], "value": cfo_map[p["quarter_end"]] - abs(p["value"])}
            for p in capex_q if p["quarter_end"] in cfo_map
        ]
        if len(fcf) >= 4:
            result["fcf"] = sorted(fcf, key=lambda x: x["quarter_end"])

    # EBITDA = Operating Income + D&A
    dep_q = extract_quarterly_series(xbrl, "Depreciation")
    if oi_q and dep_q:
        dep_map = {p["quarter_end"]: p["value"] for p in dep_q}
        ebitda = [
            {"quarter_end": p["quarter_end"], "value": p["value"] + dep_map[p["quarter_end"]]}
            for p in oi_q if p["quarter_end"] in dep_map
        ]
        if len(ebitda) >= 4:
            ebitda_sorted = sorted(ebitda, key=lambda x: x["quarter_end"])
            result["ebitda"] = ebitda_sorted
            eb_vals = [p["value"] for p in ebitda_sorted]
            if len(eb_vals) >= 8:
                try:
                    fred_matrix = fred_meta = None
                    if macro_info:
                        fred_matrix, fred_meta = _build_fred_feature_matrix(
                            ebitda_sorted, macro_info, "EBITDA", forecast_horizon)
                    eb_bt = walk_forward_backtest(eb_vals, fred_matrix=fred_matrix)
                    eb_fc = compute_ensemble_forecast(eb_vals, horizon=4, weights=eb_bt.get("calibrated_weights"),
                                                       fred_matrix=fred_matrix, backtest_sigmas=backtest_sigmas_from(eb_bt))
                    if fred_meta:
                        eb_fc["fred_features_used"] = fred_meta
                    result["ebitda_forecast"] = eb_fc
                    result["ebitda_backtest"] = eb_bt
                    _record_and_reconcile_forecasts(ticker, company_id, "ebitda", ebitda_sorted, eb_fc)
                except Exception:
                    pass

    return result


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — BENEISH M-SCORE
# ══════════════════════════════════════════════════════════════════════════════

def compute_beneish_mscore(ratios: dict) -> dict:
    """
    Simplified 5-variable Beneish M-Score (Beneish 1999).

    M = -4.84 + 0.920·DSRI + 0.528·GMI + 0.892·SGI + 4.679·TATA

    Thresholds:
      M > -1.78  → likely manipulator   (Red)
      M > -2.22  → gray zone            (Amber)
      M ≤ -2.22  → likely non-manipulator (Green)
    """
    dsri = ratios.get("dsri")
    gmi  = ratios.get("gross_margin_index")
    sgi  = ratios.get("sgi")
    tata = ratios.get("tata")

    missing = [k for k, v in {"DSRI": dsri, "GMI": gmi, "SGI": sgi, "TATA": tata}.items() if v is None]

    if len(missing) > 2:
        return {
            "m_score": None,
            "interpretation": "insufficient_data",
            "missing_inputs": missing,
        }

    # Conservative defaults for single missing inputs
    dsri = dsri if dsri is not None else 1.0
    gmi  = gmi  if gmi  is not None else 1.0
    sgi  = sgi  if sgi  is not None else 1.0
    tata = tata if tata is not None else 0.0

    m = -4.84 + 0.920 * dsri + 0.528 * gmi + 0.892 * sgi + 4.679 * tata

    if m > -1.78:
        interp, rag = "likely_manipulator", "Red"
    elif m > -2.22:
        interp, rag = "gray_zone", "Amber"
    else:
        interp, rag = "likely_non_manipulator", "Green"

    return {
        "m_score":        round(m, 3),
        "interpretation": interp,
        "rag_status":     rag,
        "thresholds":     {"red_above": -1.78, "amber_above": -2.22},
        "inputs":         {"dsri": round(dsri, 4), "gmi": round(gmi, 4),
                           "sgi":  round(sgi, 4),  "tata": round(tata, 4)},
        "missing_inputs": missing,
        "formula":        "M = -4.84 + 0.920·DSRI + 0.528·GMI + 0.892·SGI + 4.679·TATA",
    }


def compute_altman_zscore(ratios: dict) -> dict:
    """
    Altman Z''-Score (Altman 1995) — general/non-manufacturer variant, using
    book value of equity in place of market value of equity (this app has no
    stock-price data source, so the classic Z-Score's market-cap term is not
    computable).

    Z'' = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4
      X1 = Working Capital / Total Assets
      X2 = Retained Earnings / Total Assets
      X3 = EBIT / Total Assets
      X4 = Book Value of Equity / Total Liabilities

    Zones:
      Z'' > 2.6   → safe               (Green)
      Z'' > 1.1   → gray zone          (Amber)
      Z'' ≤ 1.1   → distress           (Red)
    """
    x1 = ratios.get("zscore_x1")
    x2 = ratios.get("zscore_x2")
    x3 = ratios.get("zscore_x3")
    x4 = ratios.get("zscore_x4")

    missing = [k for k, v in {"X1": x1, "X2": x2, "X3": x3, "X4": x4}.items() if v is None]

    if len(missing) > 2:
        return {
            "z_score": None,
            "interpretation": "insufficient_data",
            "missing_inputs": missing,
        }

    # Neutral defaults for single missing inputs
    x1 = x1 if x1 is not None else 0.0
    x2 = x2 if x2 is not None else 0.0
    x3 = x3 if x3 is not None else 0.0
    x4 = x4 if x4 is not None else 0.0

    z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4

    if z <= 1.1:
        interp, rag = "distress", "Red"
    elif z <= 2.6:
        interp, rag = "gray_zone", "Amber"
    else:
        interp, rag = "safe", "Green"

    return {
        "z_score":        round(z, 3),
        "interpretation": interp,
        "rag_status":     rag,
        "thresholds":     {"distress_at_or_below": 1.1, "gray_zone_at_or_below": 2.6},
        "inputs":         {"x1": round(x1, 4), "x2": round(x2, 4),
                           "x3": round(x3, 4), "x4": round(x4, 4)},
        "missing_inputs": missing,
        "formula":        "Z'' = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4",
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2b — FINANCIAL RISK PIPELINE (Multi-Domain Continuous Risk Pipeline)
# ══════════════════════════════════════════════════════════════════════════════
# Three pure calculations on top of data the platform already fetches — XBRL
# via build_company_xbrl/extract_quarterly_series (same as compute_altman_zscore
# above), and journal-entry timestamps from whichever ERP source is wired up
# (oracle_fusion_tool.get_audit_events for public/connected companies,
# manual_financials_tool's uploaded entries for private ones). No new external
# connector — see the Multi-Domain Continuous Risk Pipeline plan.

def compute_je_velocity_anomaly(entry_dates: list, window_days: int = 30) -> dict:
    """
    Manual journal-entry velocity — daily entry rate in the most recent
    `window_days` vs. a trailing baseline of the same length immediately
    before it, flagged when the recent rate is >3σ above the baseline mean
    (the same statistical idiom the Operational Risk Pipeline spec uses for
    process-mining variant-speed deviation, reused here for consistency).

    entry_dates: list of ISO-8601 date/datetime strings, one per manual
    journal entry (any source — Oracle Fusion audit events, NetSuite,
    manually-uploaded financials). Unparseable entries are skipped, not fatal.
    """
    parsed: list[datetime] = []
    for d in entry_dates or []:
        try:
            dt = d if isinstance(d, datetime) else datetime.fromisoformat(str(d).replace("Z", "+00:00"))
            parsed.append(dt.replace(tzinfo=None) if dt.tzinfo else dt)
        except (ValueError, TypeError):
            continue

    if len(parsed) < 4:
        return {"anomaly": False, "interpretation": "insufficient_data", "entry_count": len(parsed)}

    now = max(parsed)
    baseline_start = now - timedelta(days=window_days * 2)
    recent_start = now - timedelta(days=window_days)

    baseline_entries = [d for d in parsed if baseline_start <= d < recent_start]
    recent_entries = [d for d in parsed if d >= recent_start]

    def _daily_counts(dates: list, start: datetime, end: datetime) -> list:
        by_day: dict = {}
        for d in dates:
            by_day[d.date()] = by_day.get(d.date(), 0) + 1
        total_days = max((end.date() - start.date()).days, 1)
        return [by_day.get(start.date() + timedelta(days=i), 0) for i in range(total_days)]

    baseline_daily = _daily_counts(baseline_entries, baseline_start, recent_start)
    recent_daily = _daily_counts(recent_entries, recent_start, now)

    if len(baseline_daily) < 7 or statistics.pstdev(baseline_daily) == 0:
        return {"anomaly": False, "interpretation": "insufficient_baseline",
                "entry_count": len(parsed), "recent_daily_rate": statistics.mean(recent_daily) if recent_daily else 0.0}

    baseline_mean = statistics.mean(baseline_daily)
    baseline_stdev = statistics.pstdev(baseline_daily)
    recent_rate = statistics.mean(recent_daily) if recent_daily else 0.0
    z = (recent_rate - baseline_mean) / baseline_stdev

    return {
        "anomaly": z > 3.0,
        "interpretation": "velocity_spike" if z > 3.0 else "normal",
        "rag_status": "Red" if z > 3.0 else ("Amber" if z > 2.0 else "Green"),
        "z_score": round(z, 3),
        "recent_daily_rate": round(recent_rate, 3),
        "baseline_daily_mean": round(baseline_mean, 3),
        "baseline_daily_stdev": round(baseline_stdev, 3),
        "window_days": window_days,
        "entry_count": len(parsed),
        "formula": "z = (recent_daily_rate - baseline_mean) / baseline_stdev, flagged at z > 3",
    }


def _quarterly_ratio_series(xbrl: dict, numerator_metric: str, denominator_metric: str,
                             numerator_adjust=None) -> list:
    """[{quarter_end, value}] of numerator/denominator per quarter, matched by
    quarter_end. `numerator_adjust(num_val, denom_val) -> float` optionally
    transforms the numerator before dividing (e.g. current_assets - inventory
    for a quick ratio) — see compute_liquidity_shift."""
    num_q = extract_quarterly_series(xbrl, numerator_metric)
    denom_q = extract_quarterly_series(xbrl, denominator_metric)
    denom_map = {p["quarter_end"]: p["value"] for p in denom_q}
    series = []
    for p in num_q:
        denom_val = denom_map.get(p["quarter_end"])
        if denom_val in (None, 0):
            continue
        num_val = p["value"]
        if numerator_adjust:
            num_val = numerator_adjust(num_val, p["quarter_end"])
            if num_val is None:
                continue
        series.append({"quarter_end": p["quarter_end"], "value": num_val / denom_val})
    return sorted(series, key=lambda r: r["quarter_end"])


def _latest_qoq_zscore(ratio_series: list) -> Optional[dict]:
    """QoQ deltas across a ratio time series, z-score of the LATEST delta
    against the historical distribution of deltas — shared by
    compute_liquidity_shift and compute_inventory_sales_divergence."""
    if len(ratio_series) < 5:
        return None
    values = [r["value"] for r in ratio_series]
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    history, latest = deltas[:-1], deltas[-1]
    if len(history) < 3 or statistics.pstdev(history) == 0:
        return None
    mean, stdev = statistics.mean(history), statistics.pstdev(history)
    return {
        "latest_delta": round(latest, 4),
        "historical_delta_mean": round(mean, 4),
        "historical_delta_stdev": round(stdev, 4),
        "z_score": round((latest - mean) / stdev, 3),
        "latest_value": round(values[-1], 4),
        "latest_quarter_end": ratio_series[-1]["quarter_end"],
    }


def compute_liquidity_shift(xbrl: dict) -> dict:
    """
    Quarter-over-quarter current-ratio/quick-ratio break — a sudden negative
    swing (z < -3) flags a liquidity shift the point-in-time ratio snapshot
    (compute_financial_ratios' cash_ratio) wouldn't catch on its own, since
    that only ever compares the two most recent quarters, not the shift's
    size relative to the company's own historical quarter-to-quarter noise.
    """
    current_ratio_series = _quarterly_ratio_series(xbrl, "CurrentAssets", "CurrentLiabilities")
    quick_ratio_series = _quarterly_ratio_series(
        xbrl, "CurrentAssets", "CurrentLiabilities",
        numerator_adjust=lambda num, qend: (
            num - next((p["value"] for p in extract_quarterly_series(xbrl, "Inventory") if p["quarter_end"] == qend), 0)
        ),
    )

    current_shift = _latest_qoq_zscore(current_ratio_series)
    quick_shift = _latest_qoq_zscore(quick_ratio_series)
    if current_shift is None and quick_shift is None:
        return {"shift_detected": False, "interpretation": "insufficient_data"}

    worst_z = min(
        (s["z_score"] for s in (current_shift, quick_shift) if s is not None),
        default=0.0,
    )
    shift_detected = worst_z < -3.0

    return {
        "shift_detected": shift_detected,
        "interpretation": "liquidity_shift" if shift_detected else "normal",
        "rag_status": "Red" if shift_detected else ("Amber" if worst_z < -2.0 else "Green"),
        "worst_z_score": round(worst_z, 3),
        "current_ratio": current_shift,
        "quick_ratio": quick_shift,
        "formula": "z-score of the latest QoQ ratio delta vs. the company's own historical QoQ delta distribution, flagged at z < -3",
    }


def compute_inventory_sales_divergence(xbrl: dict) -> dict:
    """
    Company-specific inventory/sales ratio divergence — distinct from the
    generic FRED ISRATIO macro series (fred_tool.py's economy-wide
    inventory-to-sales indicator); this computes the same concept against
    THIS company's own XBRL inventory and revenue. A sharp positive z (ratio
    rising faster than its own historical QoQ noise) is "toxic bloat" —
    inventory building up faster than sales can absorb it.
    """
    ratio_series = _quarterly_ratio_series(xbrl, "Inventory", "Revenue")
    shift = _latest_qoq_zscore(ratio_series)
    if shift is None:
        return {"divergence_detected": False, "interpretation": "insufficient_data"}

    divergence_detected = shift["z_score"] > 3.0
    return {
        "divergence_detected": divergence_detected,
        "interpretation": "toxic_bloat" if divergence_detected else "normal",
        "rag_status": "Red" if divergence_detected else ("Amber" if shift["z_score"] > 2.0 else "Green"),
        **shift,
        "formula": "z-score of the latest QoQ inventory/revenue ratio delta vs. the company's own historical QoQ delta distribution, flagged at z > 3",
    }


def check_financial_risk_pipeline(ticker: str, xbrl: dict, je_entry_dates: Optional[list] = None) -> dict:
    """
    Runs the three Financial Risk Pipeline checks for a ticker/run and, for
    any that breach threshold, ingests a system_telemetry event through the
    real UBO Bronze->Silver->Gold->Council pipeline (POL-FIN-001..003 /
    P-FIN-001..003) — same mechanism Infrastructure Monitoring's
    connector-driven findings use. mcp_governance is imported lazily so this
    module doesn't acquire a hard dependency on the governance stack (it's
    also usable standalone/offline, same as compute_altman_zscore above);
    ingestion failures are swallowed — never fail the analysis run over it.

    je_entry_dates is optional — without a wired manual-JE source (Oracle
    Fusion audit events, manually-uploaded financials), the velocity check
    is skipped, not treated as an error.
    """
    results: dict = {}
    if je_entry_dates:
        results["je_velocity"] = compute_je_velocity_anomaly(je_entry_dates)
    results["liquidity_shift"] = compute_liquidity_shift(xbrl)
    results["inventory_divergence"] = compute_inventory_sales_divergence(xbrl)

    try:
        import mcp_governance
    except ImportError:
        return results

    _CHECKS = (
        ("je_velocity_anomaly", "je_velocity", "anomaly", "HIGH"),
        ("liquidity_shift", "liquidity_shift", "shift_detected", "HIGH"),
        ("inventory_divergence", "inventory_divergence", "divergence_detected", "MEDIUM"),
    )
    today = datetime.now(timezone.utc).date().isoformat()
    for flag_name, result_key, flagged_key, severity in _CHECKS:
        r = results.get(result_key)
        if not r or not r.get(flagged_key):
            continue
        try:
            flags = mcp_governance._detect_system_flags({
                "action": flag_name, "resource": ticker, "severity": severity,
                "event_type": flag_name, "payload": {flag_name: True},
            })
            mcp_governance._ingest_system_event(
                f"financial-risk:{ticker}", "financial_risk", flag_name,
                f"{flag_name}:{ticker}:{today}", "predictive_analytics_tool", flag_name, ticker,
                severity, flags, {flag_name: True, "financial_compliance": r}, None,
            )
        except Exception:
            pass
    return results


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — INDUSTRY-TEMPLATED RISK SCORING
# ══════════════════════════════════════════════════════════════════════════════

def _tier(value: float, thresholds: list) -> float:
    """
    Step-function mapping ordered highest→lowest with None catch-all last.
    Returns the delta for the first threshold where value >= threshold.
    """
    for thresh, delta in thresholds:
        if thresh is None or value >= thresh:
            return delta
    return 0.0


def _risk_delta(ratios: dict, delta_rules: list) -> float:
    """
    Sum deltas from a list of (ratio_key, thresholds) pairs.
    Missing ratios contribute 0.
    """
    total = 0.0
    for key, thresholds in delta_rules:
        val = ratios.get(key)
        if val is not None:
            total += _tier(val, thresholds)
    return total


# Each risk template: name, category, base_score, delta_rules, control_env, peer_benchmark
# delta_rules: list of (ratio_key, [(thresh_hi_to_lo, delta), ..., (None, delta)])

INDUSTRY_TEMPLATES: dict[str, list[dict]] = {

    "Semiconductors": [
        {"name": "Supply Chain Concentration", "category": "Operational", "base": 5.5,
         "rules": [
             ("revenue_growth", [(0.15, -0.5), (0.05, -0.2), (0.0, 0.0), (-0.05, +0.5), (None, +1.2)]),
             ("asset_growth",   [(0.20, +0.3), (0.10, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Export Control Compliance", "category": "Regulatory", "base": 5.0,
         "rules": [
             ("rd_intensity", [(0.20, +0.5), (0.12, +0.2), (0.05, 0.0), (None, -0.3)]),
             ("revenue_growth", [(0.20, +0.3), (0.0, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Capex Overrun", "category": "Financial", "base": 5.5,
         "rules": [
             ("fcf_margin", [(0.15, -0.6), (0.05, -0.2), (0.0, 0.0), (None, +0.8)]),
             ("asset_growth", [(0.20, +0.5), (0.10, +0.2), (None, 0.0)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Technology Obsolescence", "category": "Strategic", "base": 5.0,
         "rules": [
             ("rd_intensity", [(0.20, -0.6), (0.12, -0.2), (0.07, 0.0), (None, +0.8)]),
             ("gross_margin", [(0.60, -0.3), (0.40, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Customer Concentration", "category": "Strategic", "base": 5.5,
         "rules": [
             ("revenue_growth", [(0.20, -0.4), (0.05, 0.0), (None, +0.6)]),
             ("net_margin",     [(0.20, -0.3), (0.10, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "IP / Patent Risk", "category": "Legal", "base": 4.5,
         "rules": [
             ("rd_intensity", [(0.20, +0.3), (0.10, 0.0), (None, -0.3)]),
             ("sga_intensity", [(0.25, +0.2), (0.15, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Financial Reporting Quality", "category": "Financial", "base": 4.5,
         "rules": [
             ("tata",          [(0.10, +1.5), (0.05, +0.8), (0.0, 0.0), (None, -0.3)]),
             ("dsri",          [(1.20, +0.8), (1.05, +0.3), (0.95, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Macro Demand Cyclicality", "category": "Macro", "base": 5.5,
         "rules": [
             ("revenue_growth", [(0.15, -0.5), (0.0, 0.0), (-0.10, +0.7), (None, +1.5)]),
             ("cash_ratio",     [(0.30, -0.3), (0.10, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
    ],

    "Automotive OEM": [
        {"name": "EV Transition Risk", "category": "Strategic", "base": 5.5,
         "rules": [
             ("rd_intensity",  [(0.08, -0.5), (0.04, 0.0), (None, +0.8)]),
             ("fcf_margin",    [(0.08, -0.3), (0.02, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Supply Chain Disruption", "category": "Operational", "base": 5.5,
         "rules": [
             ("gross_margin",  [(0.20, -0.4), (0.10, 0.0), (None, +0.7)]),
             ("asset_growth",  [(0.15, +0.3), (0.0, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Regulatory / Emissions Compliance", "category": "Regulatory", "base": 5.0,
         "rules": [
             ("sga_intensity", [(0.15, +0.3), (0.08, 0.0), (None, -0.2)]),
             ("rd_intensity",  [(0.05, -0.3), (None, +0.3)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Raw Material Cost", "category": "Operational", "base": 5.5,
         "rules": [
             ("gross_margin",  [(0.15, -0.5), (0.08, 0.0), (None, +0.8)]),
             ("net_margin",    [(0.05, -0.3), (0.0, 0.0), (None, +0.6)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Union Labour Risk", "category": "Operational", "base": 5.0,
         "rules": [
             ("sga_intensity", [(0.20, +0.5), (0.12, +0.2), (None, -0.2)]),
             ("net_margin",    [(0.10, -0.3), (0.0, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Warranty / Recall Risk", "category": "Legal", "base": 5.0,
         "rules": [
             ("revenue_growth",[(0.05, -0.3), (0.0, 0.0), (None, +0.5)]),
             ("net_margin",    [(0.08, -0.3), (0.0, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Financial Reporting Quality", "category": "Financial", "base": 4.5,
         "rules": [
             ("tata", [(0.10, +1.5), (0.05, +0.8), (0.0, 0.0), (None, -0.3)]),
             ("dsri", [(1.20, +0.8), (1.05, +0.3), (None, 0.0)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Macro Demand Sensitivity", "category": "Macro", "base": 5.5,
         "rules": [
             ("revenue_growth", [(0.10, -0.5), (0.0, 0.0), (-0.08, +0.8), (None, +1.5)]),
             ("cash_ratio",     [(0.20, -0.3), (0.08, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
    ],

    "Software & Cloud": [
        {"name": "Cybersecurity / Data Breach", "category": "Operational", "base": 5.5,
         "rules": [
             ("rd_intensity",  [(0.20, -0.4), (0.12, 0.0), (None, +0.6)]),
             ("net_margin",    [(0.15, -0.3), (0.0, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Customer Churn / NRR Erosion", "category": "Strategic", "base": 5.0,
         "rules": [
             ("revenue_growth", [(0.20, -0.6), (0.10, -0.2), (0.0, 0.0), (None, +0.9)]),
             ("gross_margin",   [(0.70, -0.3), (0.55, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "AI Disruption / Substitution", "category": "Strategic", "base": 5.5,
         "rules": [
             ("rd_intensity",  [(0.25, -0.6), (0.15, -0.2), (0.08, 0.0), (None, +0.8)]),
             ("revenue_growth", [(0.20, -0.3), (0.05, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Regulatory / GDPR Compliance", "category": "Regulatory", "base": 4.5,
         "rules": [
             ("sga_intensity", [(0.25, +0.3), (0.15, 0.0), (None, -0.2)]),
             ("net_margin",    [(0.20, -0.2), (0.0, 0.0), (None, +0.3)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Talent Retention", "category": "Operational", "base": 4.5,
         "rules": [
             ("sga_intensity", [(0.35, +0.4), (0.20, +0.1), (None, -0.2)]),
             ("rd_intensity",  [(0.25, +0.2), (0.10, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Cloud Infrastructure Dependency", "category": "Operational", "base": 4.5,
         "rules": [
             ("gross_margin", [(0.70, -0.4), (0.55, 0.0), (None, +0.6)]),
             ("fcf_margin",   [(0.15, -0.3), (0.0, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Financial Reporting Quality", "category": "Financial", "base": 4.5,
         "rules": [
             ("tata", [(0.10, +1.5), (0.05, +0.8), (0.0, 0.0), (None, -0.3)]),
             ("dsri", [(1.20, +0.8), (1.05, +0.3), (None, 0.0)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Revenue Concentration", "category": "Strategic", "base": 5.0,
         "rules": [
             ("revenue_growth", [(0.25, -0.5), (0.10, 0.0), (None, +0.6)]),
             ("net_margin",     [(0.20, -0.3), (0.05, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
    ],

    "Financial Services": [
        {"name": "Credit Quality / NPL", "category": "Financial", "base": 5.5,
         "rules": [
             ("net_margin",    [(0.20, -0.5), (0.10, 0.0), (None, +0.8)]),
             ("asset_growth",  [(0.15, +0.3), (0.0, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Regulatory Capital", "category": "Regulatory", "base": 5.5,
         "rules": [
             ("cash_ratio",    [(0.20, -0.5), (0.10, 0.0), (None, +0.8)]),
             ("asset_growth",  [(0.20, +0.4), (0.0, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Interest Rate Sensitivity", "category": "Macro", "base": 5.5,
         "rules": [
             ("net_margin",    [(0.15, -0.4), (0.05, 0.0), (None, +0.7)]),
             ("revenue_growth",[(0.10, -0.3), (0.0, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Cybersecurity", "category": "Operational", "base": 5.5,
         "rules": [
             ("sga_intensity", [(0.30, -0.3), (0.20, 0.0), (None, +0.5)]),
             ("net_margin",    [(0.15, -0.2), (0.0, 0.0), (None, +0.3)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "AML / Compliance Risk", "category": "Regulatory", "base": 5.0,
         "rules": [
             ("sga_intensity", [(0.25, +0.3), (0.15, 0.0), (None, -0.2)]),
             ("revenue_growth",[(0.15, -0.2), (0.0, 0.0), (None, +0.3)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Liquidity Risk", "category": "Financial", "base": 5.5,
         "rules": [
             ("cash_ratio",    [(0.25, -0.6), (0.10, -0.2), (0.05, 0.0), (None, +0.9)]),
             ("fcf_margin",    [(0.10, -0.3), (0.0, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Financial Reporting Quality", "category": "Financial", "base": 5.0,
         "rules": [
             ("tata", [(0.10, +1.5), (0.05, +0.8), (0.0, 0.0), (None, -0.3)]),
             ("dsri", [(1.20, +0.8), (1.05, +0.3), (None, 0.0)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Macro / Systemic Risk", "category": "Macro", "base": 5.5,
         "rules": [
             ("revenue_growth", [(0.15, -0.5), (0.0, 0.0), (-0.05, +0.7), (None, +1.3)]),
             ("cash_ratio",     [(0.20, -0.3), (0.08, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
    ],

    "Healthcare & Pharma": [
        {"name": "Clinical Trial Failure", "category": "Strategic", "base": 5.5,
         "rules": [
             ("rd_intensity", [(0.20, -0.5), (0.12, 0.0), (None, +0.8)]),
             ("net_margin",   [(0.15, -0.3), (0.0, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Regulatory Approval Risk", "category": "Regulatory", "base": 5.5,
         "rules": [
             ("rd_intensity", [(0.25, +0.3), (0.12, 0.0), (None, -0.3)]),
             ("sga_intensity",[(0.30, +0.2), (0.15, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Pricing / Reimbursement Risk", "category": "Regulatory", "base": 5.5,
         "rules": [
             ("gross_margin",  [(0.70, -0.4), (0.50, 0.0), (None, +0.7)]),
             ("revenue_growth",[(0.10, -0.3), (0.0, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Patent Cliff", "category": "Strategic", "base": 5.5,
         "rules": [
             ("revenue_growth", [(0.10, -0.5), (0.0, 0.0), (-0.05, +0.7), (None, +1.3)]),
             ("rd_intensity",   [(0.20, -0.3), (0.10, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Supply Chain / Manufacturing", "category": "Operational", "base": 5.0,
         "rules": [
             ("gross_margin",  [(0.60, -0.3), (0.40, 0.0), (None, +0.6)]),
             ("asset_growth",  [(0.15, +0.3), (0.0, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Litigation / Liability", "category": "Legal", "base": 5.0,
         "rules": [
             ("sga_intensity", [(0.30, +0.4), (0.20, +0.1), (None, -0.2)]),
             ("net_margin",    [(0.15, -0.3), (0.0, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Financial Reporting Quality", "category": "Financial", "base": 4.5,
         "rules": [
             ("tata", [(0.10, +1.5), (0.05, +0.8), (0.0, 0.0), (None, -0.3)]),
             ("dsri", [(1.20, +0.8), (1.05, +0.3), (None, 0.0)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "ESG / Pricing Scrutiny", "category": "Regulatory", "base": 5.0,
         "rules": [
             ("gross_margin",  [(0.70, +0.4), (0.50, +0.1), (None, -0.2)]),
             ("net_margin",    [(0.20, +0.3), (0.10, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
    ],

    "Energy & Utilities": [
        {"name": "Commodity Price Risk", "category": "Market", "base": 5.5,
         "rules": [
             ("gross_margin",  [(0.40, -0.5), (0.25, 0.0), (None, +0.8)]),
             ("revenue_growth",[(0.10, -0.3), (0.0, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Regulatory / Environmental", "category": "Regulatory", "base": 5.5,
         "rules": [
             ("sga_intensity", [(0.15, +0.3), (0.08, 0.0), (None, -0.2)]),
             ("net_margin",    [(0.10, -0.3), (0.0, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Energy Transition Risk", "category": "Strategic", "base": 5.5,
         "rules": [
             ("rd_intensity",  [(0.05, -0.4), (0.02, 0.0), (None, +0.7)]),
             ("asset_growth",  [(0.10, +0.3), (0.0, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Capex / Project Execution", "category": "Financial", "base": 5.0,
         "rules": [
             ("fcf_margin",   [(0.10, -0.5), (0.02, 0.0), (None, +0.8)]),
             ("asset_growth", [(0.20, +0.4), (0.05, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Geopolitical Risk", "category": "Strategic", "base": 5.0,
         "rules": [
             ("revenue_growth",[(0.10, -0.3), (0.0, 0.0), (None, +0.5)]),
             ("cash_ratio",    [(0.20, -0.3), (0.08, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Supply Chain", "category": "Operational", "base": 4.5,
         "rules": [
             ("gross_margin",  [(0.35, -0.3), (0.20, 0.0), (None, +0.5)]),
             ("asset_growth",  [(0.10, +0.2), (0.0, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Financial Reporting Quality", "category": "Financial", "base": 4.5,
         "rules": [
             ("tata", [(0.10, +1.5), (0.05, +0.8), (0.0, 0.0), (None, -0.3)]),
             ("dsri", [(1.20, +0.8), (1.05, +0.3), (None, 0.0)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Climate / Physical Risk", "category": "Operational", "base": 5.0,
         "rules": [
             ("asset_growth",  [(0.15, +0.3), (0.0, 0.0), (None, -0.2)]),
             ("sga_intensity", [(0.10, +0.2), (0.05, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
    ],

    "Retail & Consumer": [
        {"name": "Margin Compression", "category": "Financial", "base": 5.5,
         "rules": [
             ("gross_margin",  [(0.40, -0.5), (0.25, 0.0), (None, +0.8)]),
             ("net_margin",    [(0.08, -0.3), (0.0, 0.0), (None, +0.6)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Consumer Demand / Discretionary", "category": "Macro", "base": 5.5,
         "rules": [
             ("revenue_growth",[(0.10, -0.5), (0.0, 0.0), (-0.05, +0.6), (None, +1.3)]),
             ("fcf_margin",    [(0.08, -0.3), (0.0, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "E-commerce Disruption", "category": "Strategic", "base": 5.0,
         "rules": [
             ("revenue_growth",[(0.15, -0.4), (0.05, 0.0), (None, +0.6)]),
             ("sga_intensity", [(0.30, +0.3), (0.20, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Supply Chain / Inventory", "category": "Operational", "base": 5.0,
         "rules": [
             ("asset_growth",  [(0.20, +0.4), (0.05, 0.0), (None, -0.2)]),
             ("gross_margin",  [(0.35, -0.3), (0.20, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Brand / Reputational Risk", "category": "Strategic", "base": 5.0,
         "rules": [
             ("revenue_growth",[(0.10, -0.3), (0.0, 0.0), (None, +0.5)]),
             ("sga_intensity", [(0.25, -0.2), (0.15, 0.0), (None, +0.3)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Labour Cost Inflation", "category": "Operational", "base": 4.5,
         "rules": [
             ("sga_intensity", [(0.35, +0.5), (0.22, +0.2), (None, -0.2)]),
             ("net_margin",    [(0.08, -0.3), (0.0, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Financial Reporting Quality", "category": "Financial", "base": 4.5,
         "rules": [
             ("tata", [(0.10, +1.5), (0.05, +0.8), (0.0, 0.0), (None, -0.3)]),
             ("dsri", [(1.20, +0.8), (1.05, +0.3), (None, 0.0)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Geographic Concentration", "category": "Strategic", "base": 4.5,
         "rules": [
             ("revenue_growth",[(0.10, -0.3), (0.0, 0.0), (None, +0.4)]),
             ("asset_growth",  [(0.15, +0.2), (0.0, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
    ],

    "Generic": [
        {"name": "Competitive Pressure", "category": "Strategic", "base": 5.0,
         "rules": [
             ("revenue_growth",[(0.15, -0.5), (0.05, 0.0), (None, +0.7)]),
             ("gross_margin",  [(0.40, -0.3), (0.20, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Supply Chain Risk", "category": "Operational", "base": 5.0,
         "rules": [
             ("asset_growth",  [(0.15, +0.3), (0.0, 0.0), (None, -0.2)]),
             ("gross_margin",  [(0.35, -0.3), (0.15, 0.0), (None, +0.5)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Regulatory Compliance", "category": "Regulatory", "base": 5.0,
         "rules": [
             ("sga_intensity", [(0.20, +0.3), (0.12, 0.0), (None, -0.2)]),
             ("net_margin",    [(0.10, -0.2), (0.0, 0.0), (None, +0.3)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Cybersecurity", "category": "Operational", "base": 5.0,
         "rules": [
             ("rd_intensity",  [(0.10, -0.3), (0.05, 0.0), (None, +0.4)]),
             ("sga_intensity", [(0.25, -0.2), (0.10, 0.0), (None, +0.3)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Talent Risk", "category": "Operational", "base": 4.5,
         "rules": [
             ("sga_intensity", [(0.30, +0.4), (0.15, 0.0), (None, -0.2)]),
             ("revenue_growth",[(0.10, -0.2), (0.0, 0.0), (None, +0.3)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Financial Reporting Quality", "category": "Financial", "base": 4.5,
         "rules": [
             ("tata", [(0.10, +1.5), (0.05, +0.8), (0.0, 0.0), (None, -0.3)]),
             ("dsri", [(1.20, +0.8), (1.05, +0.3), (None, 0.0)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "Macro Sensitivity", "category": "Macro", "base": 5.0,
         "rules": [
             ("revenue_growth",[(0.10, -0.4), (0.0, 0.0), (-0.05, +0.6), (None, +1.2)]),
             ("cash_ratio",    [(0.20, -0.3), (0.08, 0.0), (None, +0.4)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
        {"name": "ESG / Sustainability", "category": "Regulatory", "base": 4.5,
         "rules": [
             ("rd_intensity",  [(0.08, -0.3), (0.03, 0.0), (None, +0.3)]),
             ("sga_intensity", [(0.20, +0.2), (0.10, 0.0), (None, -0.2)]),
         ], "ce": "ADEQUATE", "peer": "in-line"},
    ],
}

# SIC code prefix → industry template
SIC_TO_INDUSTRY: list[tuple[str, str]] = [
    ("367",  "Semiconductors"),
    ("366",  "Semiconductors"),
    ("3674", "Semiconductors"),
    ("3711", "Automotive OEM"),
    ("3714", "Automotive OEM"),
    ("3716", "Automotive OEM"),
    ("737",  "Software & Cloud"),
    ("7372", "Software & Cloud"),
    ("7371", "Software & Cloud"),
    ("602",  "Financial Services"),
    ("603",  "Financial Services"),
    ("612",  "Financial Services"),
    ("628",  "Financial Services"),
    ("631",  "Financial Services"),
    ("636",  "Financial Services"),
    ("283",  "Healthcare & Pharma"),
    ("284",  "Healthcare & Pharma"),
    ("800",  "Healthcare & Pharma"),
    ("801",  "Healthcare & Pharma"),
    ("806",  "Healthcare & Pharma"),
    ("131",  "Energy & Utilities"),
    ("291",  "Energy & Utilities"),
    ("491",  "Energy & Utilities"),
    ("492",  "Energy & Utilities"),
    ("493",  "Energy & Utilities"),
    ("52",   "Retail & Consumer"),
    ("53",   "Retail & Consumer"),
    ("54",   "Retail & Consumer"),
    ("55",   "Retail & Consumer"),
    ("56",   "Retail & Consumer"),
    ("57",   "Retail & Consumer"),
    ("58",   "Retail & Consumer"),
    ("59",   "Retail & Consumer"),
]


def detect_industry(sic: str) -> str:
    """Map a SIC code string to an industry template name."""
    for prefix, industry in SIC_TO_INDUSTRY:
        if sic.startswith(prefix):
            return industry
    return "Generic"


def compute_risk_scores(ratios: dict, industry: str = "Generic") -> dict:
    """
    Compute industry-templated risk scores.

    Returns dict with overall summary and per-risk details including score,
    RAG status (Red ≥7.0, Amber ≥5.0, Green <5.0), velocity, and control env.
    """
    template = INDUSTRY_TEMPLATES.get(industry, INDUSTRY_TEMPLATES["Generic"])
    risks = []

    for i, t in enumerate(template):
        base  = t["base"]
        delta = _risk_delta(ratios, t["rules"])
        score = max(1.0, min(10.0, base + delta))

        if score >= 7.0:
            rag = "Red"
        elif score >= 5.0:
            rag = "Amber"
        else:
            rag = "Green"

        velocity = round(delta)          # integer −1 to +3 (clamped)
        velocity = max(-1, min(3, velocity))

        # Stable per-industry ref — without this, save_risk_scores() writes
        # risk_ref=NULL for every row, and every risk collapses onto the same
        # React key downstream (RiskFrameworkMatrix keys rows by id/risk_ref).
        risks.append({
            "id":            f"R-{i+1:02d}",
            "risk_ref":      f"R-{i+1:02d}",
            "name":          t["name"],
            "category":      t["category"],
            "base_score":    round(base, 1),
            "delta":         round(delta, 2),
            "score":         round(score, 2),
            "rag_status":    rag,
            "velocity":      velocity,
            "control_env":   t["ce"],
            "peer_benchmark": t["peer"],
        })

    rag_counts = {"Red": 0, "Amber": 0, "Green": 0}
    for r in risks:
        rag_counts[r["rag_status"]] += 1

    return {
        "industry":   industry,
        "rag_summary": rag_counts,
        "risks":      risks,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — SCENARIO ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def compute_scenario_analysis(ratios: dict, risk_result: dict) -> dict:
    """
    Build Bear / Base / Bull deterministic scenarios from live ratios and risk RAG.

    Bear: dual red-risk materialisation + macro stress (−18% revenue, −380 bps gross margin)
    Base: managed risk profile (revenue at current trend, −50 bps margin)
    Bull: MAP execution ahead of schedule, risk step-down (+5–8% upside)
    """
    rev  = ratios.get("revenue_now") or 0
    gm   = ratios.get("gross_margin") or 0
    trend = ratios.get("revenue_growth") or 0.0

    rag = risk_result.get("rag_summary", {})
    red_count = rag.get("Red", 0)

    bear_rev_chg  = -0.18
    base_rev_chg  = trend
    bull_rev_chg  = max(trend + 0.07, 0.05)

    bear_gm_delta = -0.038
    base_gm_delta = -0.005
    bull_gm_delta = +0.020

    def _s(rev_chg, gm_delta, label, narrative):
        new_rev = rev * (1 + rev_chg) if rev else None
        new_gm  = gm + gm_delta if gm else None
        new_ni  = (new_rev * new_gm * 0.6) if (new_rev and new_gm) else None
        return {
            "scenario":       label,
            "revenue_change": f"{rev_chg:+.1%}",
            "projected_revenue": round(new_rev) if new_rev else None,
            "gross_margin_impact_bps": round(gm_delta * 10000),
            "projected_gross_margin":  round(new_gm, 4) if new_gm else None,
            "indicative_net_income":   round(new_ni) if new_ni else None,
            "narrative": narrative,
        }

    return {
        "Bear": _s(
            bear_rev_chg, bear_gm_delta, "Bear",
            f"Dual red-risk materialisation ({red_count} Red risks) + macro stress. "
            "-18% revenue shock, -380bps gross margin compression.",
        ),
        "Base": _s(
            base_rev_chg, base_gm_delta, "Base",
            "Managed risk profile, existing controls hold. "
            f"Revenue at current growth trend ({trend:+.1%}), -50bps margin drift.",
        ),
        "Bull": _s(
            bull_rev_chg, bull_gm_delta, "Bull",
            "Risk MAP executing ahead of schedule, Red risks step-down to Amber. "
            f"+{bull_rev_chg:.0%} revenue upside, +200bps gross margin improvement.",
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — GREY SWAN MODEL
# ══════════════════════════════════════════════════════════════════════════════

def compute_grey_swan(risk_result: dict, quarterly_revenue: Optional[float] = None) -> dict:
    """
    Plausible-but-underweighted escalation cascade.

    Selects highest-velocity Amber risk as triggering event.
    Projects 4-stage timeline: T+0 → T+30 → T+60 → T+90 days.
    Score trajectory: base → +0.8 → +1.5 → +2.2 above starting score.
    Impact ladder scales off quarterly revenue (or $500M proxy).
    """
    risks = risk_result.get("risks", [])
    amber_risks = [r for r in risks if r["rag_status"] == "Amber"]

    trigger = None
    if amber_risks:
        trigger = max(amber_risks, key=lambda r: r["velocity"])

    if trigger is None:
        all_risks = sorted(risks, key=lambda r: r["score"], reverse=True)
        trigger = all_risks[0] if all_risks else None

    if trigger is None:
        return {"error": "no risks available for grey swan model"}

    base_score = trigger["score"]
    rev_proxy  = quarterly_revenue if quarterly_revenue else 500_000_000

    def _impact(delta_score: float) -> str:
        severity = delta_score / 2.2
        impact   = rev_proxy * severity * 0.15
        if impact > rev_proxy * 0.10:
            tier = "Severe"
        elif impact > rev_proxy * 0.05:
            tier = "Material"
        elif impact > rev_proxy * 0.02:
            tier = "Moderate"
        else:
            tier = "Low"
        return f"{tier} — indicative impact ~${impact/1e6:.0f}M"

    stages = [
        {"day": 0,  "label": "T+0  Trigger",    "score": round(base_score, 2),       "description": f"{trigger['name']} event confirmed — initial disclosure"},
        {"day": 30, "label": "T+30 Escalation", "score": round(base_score + 0.8, 2), "description": "Regulatory inquiry / customer notifications commence"},
        {"day": 60, "label": "T+60 Contagion",  "score": round(base_score + 1.5, 2), "description": "Secondary risk activation — supply chain / credit spread widening"},
        {"day": 90, "label": "T+90 Resolution", "score": round(base_score + 2.2, 2), "description": "Full impact crystallised; remediation plan required"},
    ]

    for s in stages:
        delta = s["score"] - base_score
        s["impact_estimate"] = _impact(delta) if delta > 0 else "Baseline — monitoring phase"
        s["rag_status"] = "Red" if s["score"] >= 7.0 else ("Amber" if s["score"] >= 5.0 else "Green")

    return {
        "trigger_risk":      trigger["name"],
        "trigger_category":  trigger["category"],
        "trigger_base_score": trigger["score"],
        "trigger_velocity":  trigger["velocity"],
        "quarterly_revenue_proxy": rev_proxy,
        "timeline":          stages,
        "peak_score":        round(base_score + 2.2, 2),
        "peak_rag":          "Red" if (base_score + 2.2) >= 7.0 else "Amber",
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — FRED MACRO LEADING INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

# Industry-specific macro series with pre-computed typical Pearson correlations
INDUSTRY_MACRO_SERIES: dict[str, list[dict]] = {
    "Semiconductors": [
        {"series_id": "PPIITM",    "name": "PPI: Industrial Commodities",     "r": 0.72, "lead_quarters": 1},
        {"series_id": "INDPRO",    "name": "Industrial Production Index",      "r": 0.76, "lead_quarters": 1},
        {"series_id": "MANEMP",    "name": "Manufacturing Employment",         "r": 0.68, "lead_quarters": 2},
        {"series_id": "FEDFUNDS",  "name": "Fed Funds Rate",                   "r": -0.65, "lead_quarters": 3},
        {"series_id": "T10Y2Y",    "name": "10Y-2Y Yield Curve",              "r": 0.61, "lead_quarters": 2},
    ],
    "Automotive OEM": [
        {"series_id": "TOTALSA",   "name": "Light Vehicle SAAR",               "r": 0.91, "lead_quarters": 1},
        {"series_id": "UMCSENT",   "name": "Consumer Sentiment",               "r": 0.78, "lead_quarters": 1},
        {"series_id": "UNRATE",    "name": "Unemployment Rate",                "r": -0.82, "lead_quarters": 2},
        {"series_id": "FEDFUNDS",  "name": "Fed Funds Rate",                   "r": -0.71, "lead_quarters": 3},
        {"series_id": "WTISPLC",   "name": "WTI Crude Oil Price",              "r": -0.55, "lead_quarters": 1},
    ],
    "Software & Cloud": [
        {"series_id": "GDP",       "name": "Real GDP Growth",                  "r": 0.74, "lead_quarters": 1},
        {"series_id": "FEDFUNDS",  "name": "Fed Funds Rate",                   "r": -0.68, "lead_quarters": 3},
        {"series_id": "VIXCLS",    "name": "CBOE VIX",                         "r": -0.72, "lead_quarters": 1},
        {"series_id": "UMCSENT",   "name": "Consumer Sentiment",               "r": 0.65, "lead_quarters": 2},
        {"series_id": "T10Y2Y",    "name": "10Y-2Y Yield Curve",              "r": 0.58, "lead_quarters": 2},
    ],
    "Financial Services": [
        {"series_id": "FEDFUNDS",  "name": "Fed Funds Rate",                   "r": 0.76, "lead_quarters": 1},
        {"series_id": "T10Y2Y",    "name": "10Y-2Y Yield Curve",              "r": 0.85, "lead_quarters": 1},
        {"series_id": "DRCCLACBS", "name": "CC Delinquency Rate",             "r": -0.81, "lead_quarters": 2},
        {"series_id": "BAMLH0A0HYM2", "name": "HY Credit Spread",            "r": -0.79, "lead_quarters": 1},
        {"series_id": "UNRATE",    "name": "Unemployment Rate",               "r": -0.71, "lead_quarters": 3},
    ],
    "Healthcare & Pharma": [
        {"series_id": "CPIMEDSL",  "name": "Medical CPI",                     "r": 0.68, "lead_quarters": 2},
        {"series_id": "UNRATE",    "name": "Unemployment Rate",               "r": -0.55, "lead_quarters": 2},
        {"series_id": "GDP",       "name": "Real GDP Growth",                 "r": 0.62, "lead_quarters": 1},
        {"series_id": "FEDFUNDS",  "name": "Fed Funds Rate",                  "r": -0.48, "lead_quarters": 3},
        {"series_id": "UMCSENT",   "name": "Consumer Sentiment",              "r": 0.52, "lead_quarters": 1},
    ],
    "Generic": [
        {"series_id": "NAPM",      "name": "ISM PMI",                         "r": 0.76, "lead_quarters": 1},
        {"series_id": "GDP",       "name": "Real GDP Growth",                 "r": 0.71, "lead_quarters": 1},
        {"series_id": "FEDFUNDS",  "name": "Fed Funds Rate",                  "r": -0.62, "lead_quarters": 3},
        {"series_id": "UNRATE",    "name": "Unemployment Rate",               "r": -0.69, "lead_quarters": 2},
        {"series_id": "VIXCLS",    "name": "CBOE VIX",                        "r": -0.65, "lead_quarters": 1},
    ],
}


def get_macro_leading_indicators(
    ticker: str,
    industry: str = "Generic",
    api_key: str = "",
    lags: str = "1,2,3,4",
    min_r: float = 0.60,
    output_file: str = "",
) -> dict:
    """
    Fetch FRED macro correlations for the ticker, return structured results.
    Falls back to pre-computed industry benchmarks if FRED key unavailable.
    """
    if not _HAS_FRED:
        return {
            "source": "pre_computed_industry_benchmarks",
            "note": "Install fred_tool.py and set FRED_API_KEY for live correlations.",
            "indicators": INDUSTRY_MACRO_SERIES.get(industry, INDUSTRY_MACRO_SERIES["Generic"]),
        }

    fred_key = api_key.strip() or os.environ.get("FRED_API_KEY", "").strip()
    if not fred_key:
        return {
            "source": "pre_computed_industry_benchmarks",
            "note": "FRED_API_KEY not set — showing industry benchmarks.",
            "indicators": INDUSTRY_MACRO_SERIES.get(industry, INDUSTRY_MACRO_SERIES["Generic"]),
        }

    try:
        lag_list = tuple(int(x) for x in lags.split(","))
        from pathlib import Path
        out_path = Path(output_file) if output_file else Path(f"{ticker.upper()}_fred_macro.json")
        result = _fred_run_analysis(ticker=ticker, api_key=fred_key, min_r=min_r,
                                    lags=lag_list, output_path=out_path, industry=industry)
        return {"source": "live_fred_analysis", "result": result}
    except Exception as e:
        return {
            "source": "pre_computed_industry_benchmarks",
            "note": f"FRED analysis failed ({e}) — showing industry benchmarks.",
            "indicators": INDUSTRY_MACRO_SERIES.get(industry, INDUSTRY_MACRO_SERIES["Generic"]),
        }


def _build_fred_feature_matrix(
    q_series: list[dict],
    macro_info: dict,
    metric: str,
    horizon: int,
    top_n: int = 3,
) -> tuple[Optional[dict], Optional[dict]]:
    """
    Build lag-aligned FRED feature arrays for the Random Forest forecasting leg.

    `macro_info` is the live `result` dict from fred_tool.run_analysis()
    (correlation_results + fred_macro_series). For each of the top `top_n`
    indicators correlated with `metric` (by |pearson_r|), returns an array of
    length len(q_series) + horizon where position i holds the macro reading
    from that indicator's own optimal_lag_quarters before position i's
    quarter — i.e. data that was genuinely already published by the time it
    would be used to predict that quarter. Missing/future readings fall back
    to the nearest earlier known value.

    KNOWN LIMITATION (not fixed here, documented instead): `hits` is a
    correlation screen computed once upstream (fred_tool.run_analysis) over
    this company's FULL available history — including quarters that later
    become backtest targets in walk_forward_backtest. Selecting "best
    correlated of many candidates" and then backtesting against the same
    history that selection saw is a multiple-comparisons leak: some
    indicators will correlate by chance over a ~13-quarter history, and nothing
    in the backtest can catch this because the selection never changes fold
    to fold. A fully correct fix means recomputing the correlation screen
    per expanding-window fold using only that fold's training data — a
    change to fred_tool.py's correlation engine (used by several other
    features), not just this function, so it's out of scope here. top_n was
    cut from 5 to 3 as a partial, cheap mitigation (fewer candidates
    screened = less multiple-comparisons exposure), and the caveat below is
    surfaced in the returned meta so FRED-augmented accuracy claims can be
    read with appropriate skepticism rather than silently.

    Returns (fred_matrix, meta) — fred_matrix is None if nothing usable was
    found (no key, no hits, or no series data); meta is a dict
    {"indicators": [...], "leakage_caveat": "..."} or None.
    """
    if not _HAS_FRED or not q_series:
        return None, None

    hits = (macro_info.get("correlation_results") or {}).get(metric, [])
    if not hits:
        return None, None

    selected = sorted(hits, key=lambda h: abs(h.get("pearson_r", 0)), reverse=True)[:top_n]
    macro_series_map = macro_info.get("fred_macro_series") or {}

    # Canonical quarter-end for every position: historical quarters, then
    # `horizon` more extrapolated forward.
    positions_q: list[str] = [_fred_date_to_qend(p["quarter_end"]) for p in q_series]
    last_q = positions_q[-1] if positions_q else None
    for h in range(1, horizon + 1):
        positions_q.append(_fred_add_quarters(last_q, h) if last_q else None)

    matrix: dict = {}
    indicators: list = []

    for hit in selected:
        sid = hit.get("series_id")
        obs = (macro_series_map.get(sid) or {}).get("quarterly_observations", [])
        if not obs:
            continue
        obs_map = {o["quarter_end"]: o["value"] for o in obs}
        sorted_known_q = sorted(obs_map.keys())
        if not sorted_known_q:
            continue
        lag = int(hit.get("optimal_lag_quarters", 0))

        arr: list[float] = []
        last_known = obs_map[sorted_known_q[0]]
        for q in positions_q:
            if q is None:
                arr.append(last_known)
                continue
            shifted_q = _fred_add_quarters(q, -lag)
            if shifted_q in obs_map:
                last_known = obs_map[shifted_q]
                arr.append(last_known)
            else:
                earlier = [k for k in sorted_known_q if k <= shifted_q]
                if earlier:
                    last_known = obs_map[earlier[-1]]
                arr.append(last_known)

        matrix[sid] = arr
        indicators.append({
            "series_id":    sid,
            "name":         hit.get("name", sid),
            "lag_quarters": lag,
            "pearson_r":    hit.get("pearson_r"),
        })

    if not matrix:
        return None, None
    meta = {
        "indicators": indicators,
        "top_n": top_n,
        "leakage_caveat": (
            "These indicators were selected by correlation over this company's full "
            "available history, not re-selected per backtest fold — some may be "
            "selected by chance rather than real signal. Read the Random Forest leg's "
            "backtested accuracy with that in mind, especially over short histories."
        ),
    }
    return matrix, meta


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — TIME-SERIES FORECASTING
# ══════════════════════════════════════════════════════════════════════════════

# Mild L2 shrinkage for the regression-based forecast legs. Chosen with
# features standardized to unit variance first (see _ols below), so this
# value is comparable across callers regardless of each feature's raw scale
# (a raw/unstandardized penalty would under-shrink large-scale regressors
# like Prophet-like's time index relative to its unit-scale Fourier terms).
# _RIDGE_LAMBDA=2.0 is deliberately mild — Prophet-like fits 6 parameters on
# as few as 8 observations (2 spare degrees of freedom), which is where this
# matters most; ARIMA's 3-4 parameters are already parsimonious enough that
# this mostly just adds a small safety margin.
_RIDGE_LAMBDA = 2.0


def _ols(X, y, ridge_lambda: float = 0.0, intercept_col: Optional[int] = None):
    """
    OLS via numpy least squares, with optional ridge (L2) shrinkage.
    Returns (coefficients, residuals, sigma).

    When ridge_lambda > 0: non-intercept columns are standardized to unit
    variance before the penalty is applied (so the penalty is comparable
    across differently-scaled regressors), then coefficients are rescaled
    back to the original feature space — `X @ coefs` still works normally
    on the caller's original, unstandardized X. `intercept_col`, if given,
    is excluded from standardization and from the penalty (standard ridge
    practice — shrinking the intercept toward zero has no justification).
    """
    if ridge_lambda > 0:
        n_features = X.shape[1]
        X_work = X.astype(float).copy()
        scales = np.ones(n_features)
        for j in range(n_features):
            if j == intercept_col:
                continue
            s = float(np.std(X_work[:, j]))
            if s > 1e-8:
                scales[j] = s
                X_work[:, j] = X_work[:, j] / s
        penalty = np.eye(n_features) * ridge_lambda
        if intercept_col is not None:
            penalty[intercept_col, intercept_col] = 0.0
        coefs_scaled = np.linalg.solve(X_work.T @ X_work + penalty, X_work.T @ y)
        coefs = coefs_scaled / scales
    else:
        coefs, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    fitted    = X @ coefs
    residuals = y - fitted
    sigma     = float(np.std(residuals))
    return coefs, residuals, sigma


def fit_arima(series: list[float], p: int = 2, d: int = 1, q: int = 1,
              horizon: int = 4) -> dict:
    """
    ARIMA(p,d,q) via OLS.

    AR coefficients estimated on d-differenced series.
    MA via two-step residual OLS.
    Expanding CI: ±1.96σ√h
    """
    if not _HAS_NUMPY:
        return {"error": "numpy required", "model": "ARIMA", "forecasts": []}

    y = np.array(series, dtype=float)
    n = len(y)

    if n < max(p + d + q + 5, 8):
        return {"error": "insufficient data (need ≥8 observations)", "model": "ARIMA", "forecasts": []}

    # ── 1. Difference d times ─────────────────────────────────────────────────
    y_diff = y.copy()
    for _ in range(d):
        y_diff = np.diff(y_diff)

    m = len(y_diff)
    if m < p + q + 3:
        return {"error": "series too short after differencing", "model": "ARIMA", "forecasts": []}

    # ── 2. Fit AR(p) ──────────────────────────────────────────────────────────
    # y_diff[t] = c + φ₁y_diff[t-1] + ... + φₚy_diff[t-p]
    y_ar = y_diff[p:]
    cols = [y_diff[p - k - 1: m - k - 1] for k in range(p)] + [np.ones(m - p)]
    X_ar = np.column_stack(cols)
    ar_coefs, ar_resid, _ = _ols(X_ar, y_ar, ridge_lambda=_RIDGE_LAMBDA, intercept_col=p)
    ar_phi   = ar_coefs[:p]
    ar_const = float(ar_coefs[p])

    # ── 3. Fit MA(q) on AR residuals ──────────────────────────────────────────
    ma_theta = np.zeros(q)
    if q > 0 and len(ar_resid) > q + 2:
        y_ma = ar_resid[q:]
        X_ma = np.column_stack([ar_resid[q - k - 1: len(ar_resid) - k - 1] for k in range(q)])
        ma_theta, _, _ = _ols(X_ma, y_ma, ridge_lambda=_RIDGE_LAMBDA)

    # ── 4. Estimate σ ─────────────────────────────────────────────────────────
    final_resid = ar_resid.copy()
    if q > 0 and len(ar_resid) > q:
        ma_fit = np.zeros(len(ar_resid))
        for k in range(q):
            ma_fit[k + 1:] += ma_theta[k] * ar_resid[:len(ar_resid) - k - 1]
        final_resid = ar_resid - ma_fit
    sigma = max(float(np.std(final_resid)), 1e-8)

    # ── 5. Multi-step forecast ────────────────────────────────────────────────
    y_diff_buf  = list(y_diff[-p:]) if p > 0 else []
    ma_resid_buf = list(ar_resid[-q:]) if q > 0 else []

    forecasts_diff = []
    for _ in range(horizon):
        ar_part = ar_const + sum(ar_phi[k] * y_diff_buf[-(k + 1)] for k in range(min(p, len(y_diff_buf))))
        ma_part = sum(ma_theta[k] * ma_resid_buf[-(k + 1)]
                      for k in range(min(q, len(ma_resid_buf)))) if q > 0 else 0.0
        pred = float(ar_part + ma_part)
        forecasts_diff.append(pred)
        y_diff_buf.append(pred)
        if q > 0:
            ma_resid_buf.append(0.0)

    # ── 6. Integrate back ─────────────────────────────────────────────────────
    if d == 0:
        point_forecasts = forecasts_diff
    elif d == 1:
        base   = float(y[-1])
        cumsum = 0.0
        point_forecasts = []
        for fd in forecasts_diff:
            cumsum += fd
            point_forecasts.append(base + cumsum)
    else:  # d == 2
        y1_last = float(y[-1] - y[-2])
        base_y  = float(y[-1])
        point_forecasts = []
        for fd in forecasts_diff:
            y1_last += fd
            base_y  += y1_last
            point_forecasts.append(base_y)

    return {
        "model":  "ARIMA",
        "params": {"p": p, "d": d, "q": q},
        "sigma":  round(sigma, 4),
        "forecasts": [
            {
                "horizon":   h + 1,
                "point":     round(point_forecasts[h], 4),
                "ci_lower":  round(point_forecasts[h] - 1.96 * sigma * math.sqrt(h + 1), 4),
                "ci_upper":  round(point_forecasts[h] + 1.96 * sigma * math.sqrt(h + 1), 4),
            }
            for h in range(horizon)
        ],
    }


def fit_prophet_like(series: list[float], horizon: int = 4) -> dict:
    """
    Linear trend + 2 Fourier seasonal terms (period=4 quarters).

    y(t) = β₀ + β₁t + Σₖ[aₖ·sin(2πkt/4) + bₖ·cos(2πkt/4)], k=1,2
    Fitted via OLS. CI: ±1.96σ√h.
    """
    if not _HAS_NUMPY:
        return {"error": "numpy required", "model": "Prophet-like", "forecasts": []}

    y = np.array(series, dtype=float)
    n = len(y)
    if n < 8:
        return {"error": "need ≥8 observations", "model": "Prophet-like", "forecasts": []}

    t = np.arange(n, dtype=float)
    P = 4.0
    X = np.column_stack([
        np.ones(n), t,
        np.sin(2 * math.pi * t / P),
        np.cos(2 * math.pi * t / P),
        np.sin(4 * math.pi * t / P),
        np.cos(4 * math.pi * t / P),
    ])
    coefs, resid, sigma = _ols(X, y, ridge_lambda=_RIDGE_LAMBDA, intercept_col=0)
    sigma = max(sigma, 1e-8)

    t_fc = np.arange(n, n + horizon, dtype=float)
    X_fc = np.column_stack([
        np.ones(horizon), t_fc,
        np.sin(2 * math.pi * t_fc / P),
        np.cos(2 * math.pi * t_fc / P),
        np.sin(4 * math.pi * t_fc / P),
        np.cos(4 * math.pi * t_fc / P),
    ])
    preds = X_fc @ coefs

    return {
        "model":  "Prophet-like",
        "params": {"period": 4, "fourier_terms": 2},
        "sigma":  round(sigma, 4),
        "forecasts": [
            {
                "horizon":  h + 1,
                "point":    round(float(preds[h]), 4),
                "ci_lower": round(float(preds[h]) - 1.96 * sigma * math.sqrt(h + 1), 4),
                "ci_upper": round(float(preds[h]) + 1.96 * sigma * math.sqrt(h + 1), 4),
            }
            for h in range(horizon)
        ],
    }


class _TreeNode:
    __slots__ = ("feature", "threshold", "left", "right", "value")

    def __init__(self):
        self.feature = self.threshold = self.left = self.right = self.value = None

    def predict(self, x):
        if self.value is not None:
            return self.value
        if x[self.feature] <= self.threshold:
            return self.left.predict(x)
        return self.right.predict(x)


def _best_split(X, y, feature_subset=None):
    """`feature_subset`, when given, restricts candidate split features to
    that subset — this is what makes the ensemble a real Random Forest
    rather than plain bagging: without per-split feature subsampling, every
    tree sees every feature at every split, so bootstrap resampling alone
    does far less to decorrelate the trees (they tend to pick the same
    dominant feature near the root every time)."""
    best_score, best_feat, best_thresh = float("inf"), None, None
    n, n_feat = X.shape
    feat_indices = feature_subset if feature_subset is not None else range(n_feat)
    for f in feat_indices:
        vals = np.unique(X[:, f])
        for i in range(len(vals) - 1):
            thresh = (vals[i] + vals[i + 1]) / 2.0
            left = y[X[:, f] <= thresh]
            right = y[X[:, f] > thresh]
            if len(left) < 2 or len(right) < 2:
                continue
            score = len(left) * float(np.var(left)) + len(right) * float(np.var(right))
            if score < best_score:
                best_score, best_feat, best_thresh = score, f, thresh
    return best_feat, best_thresh


def _build_tree(X, y, rng, depth=0, max_depth=4, max_features=None):
    node = _TreeNode()
    if depth >= max_depth or len(y) < 4:
        node.value = float(np.mean(y))
        return node
    n_feat = X.shape[1]
    feature_subset = None
    if max_features is not None and max_features < n_feat:
        feature_subset = rng.sample(range(n_feat), max_features)
    feat, thresh = _best_split(X, y, feature_subset=feature_subset)
    if feat is None:
        node.value = float(np.mean(y))
        return node
    mask = X[:, feat] <= thresh
    node.feature, node.threshold = feat, thresh
    node.left  = _build_tree(X[mask],  y[mask],  rng, depth + 1, max_depth, max_features)
    node.right = _build_tree(X[~mask], y[~mask], rng, depth + 1, max_depth, max_features)
    return node


def _fit_linear_trend(y: np.ndarray) -> np.ndarray:
    """Simple intercept+slope OLS trend line (no ridge needed — only 2
    parameters). Used to detrend the series before it reaches Random Forest,
    see fit_random_forest's docstring for why."""
    n = len(y)
    t = np.arange(n, dtype=float)
    X = np.column_stack([np.ones(n), t])
    coefs, _, _ = _ols(X, y)
    return coefs


def _make_rf_features(y_hist: np.ndarray, fred_matrix: Optional[dict] = None) -> np.ndarray:
    """
    Build feature vector for the next forecast from a (detrended) history
    array. `len(y_hist)` doubles as the absolute position index into
    `fred_matrix` (both the training loop and the forecast loop grow
    `y_hist` by exactly one element per step, so this always lines up).

    Deliberately no raw time-index feature: handing a tree a monotonically
    increasing index is a leakage-prone shortcut (with only a handful of
    training rows, a tree can just split on "index == this recent value" and
    call it learning). The series is detrended by the caller instead, so
    this only has to describe recent level/seasonal structure — genuinely
    learnable from a handful of lags — not re-derive "time is passing".
    """
    n = len(y_hist)
    lags  = [y_hist[n - k - 1] if k < n else 0.0 for k in range(4)]
    win   = y_hist[-4:] if n >= 4 else y_hist
    rmean = float(np.mean(win))
    rstd  = float(np.std(win)) if len(win) > 1 else 0.0
    qtr   = float((n % 4) + 1)
    feats = lags + [rmean, rstd, qtr]
    if fred_matrix:
        for arr in fred_matrix.values():
            feats.append(arr[n] if n < len(arr) else (arr[-1] if arr else 0.0))
    return np.array(feats, dtype=float)


def fit_random_forest(series: list[float], horizon: int = 4, fred_matrix: Optional[dict] = None,
                      n_trees: int = 25, max_depth: int = 4, seed: int = 42) -> dict:
    """
    Random Forest: 25 bootstrap trees, depth 4, with per-split feature
    subsampling (max_features ~= sqrt(n_features), the textbook RF
    decorrelation step — see _best_split's docstring).

    Fit on a DETRENDED copy of the series (linear trend removed via
    _fit_linear_trend, added back at forecast time) rather than the raw
    series with a time-index feature — with only ~9-13 training rows this
    matters: a raw index feature is trivial for a tree to overfit to
    directly, whereas the detrended target only contains level/seasonal
    residual structure for the model to actually learn from.

    Features: lags 1–4, rolling mean/std (window 4), quarter, plus (when
    `fred_matrix` is given) one lag-aligned FRED reading per selected
    indicator — see _build_fred_feature_matrix. Seeded for reproducibility.
    CI from tree prediction variance (does not include trend-extrapolation
    uncertainty, which this simplified linear trend does not model).
    """
    if not _HAS_NUMPY:
        return {"error": "numpy required", "model": "Random Forest", "forecasts": []}

    y = np.array(series, dtype=float)
    n = len(y)
    if n < 8:
        return {"error": "need ≥8 observations", "model": "Random Forest", "forecasts": []}

    trend_coefs = _fit_linear_trend(y)
    y_detrended = y - (trend_coefs[0] + trend_coefs[1] * np.arange(n, dtype=float))

    # Build supervised dataset: (features_at_t, y_detrended[t]) for t in [4, n-1]
    rows_X, rows_y = [], []
    for t in range(4, n):
        rows_X.append(_make_rf_features(y_detrended[:t], fred_matrix))
        rows_y.append(y_detrended[t])
    X_all = np.array(rows_X)
    y_all = np.array(rows_y)

    n_features_total = X_all.shape[1]
    max_features = max(1, round(math.sqrt(n_features_total)))

    rng = random.Random(seed)

    trees = []
    for _ in range(n_trees):
        # Bootstrap sample
        idx = [rng.randint(0, len(y_all) - 1) for _ in range(len(y_all))]
        X_b = X_all[idx]
        y_b = y_all[idx]
        trees.append(_build_tree(X_b, y_b, rng, max_depth=max_depth, max_features=max_features))

    # Forecast — walk forward in DETRENDED space, add the trend back per step
    hist = list(y_detrended)
    forecasts = []
    for h in range(horizon):
        feat  = _make_rf_features(np.array(hist), fred_matrix)
        preds = [t.predict(feat) for t in trees]
        resid_point = float(np.mean(preds))
        sigma = float(np.std(preds)) if len(preds) > 1 else 1e-8
        sigma = max(sigma, 1e-8)
        abs_t = n + h
        trend_value = trend_coefs[0] + trend_coefs[1] * abs_t
        point = resid_point + trend_value
        forecasts.append({
            "horizon":  h + 1,
            "point":    round(point, 4),
            "ci_lower": round(point - 1.96 * sigma * math.sqrt(h + 1), 4),
            "ci_upper": round(point + 1.96 * sigma * math.sqrt(h + 1), 4),
        })
        hist.append(resid_point)

    return {
        "model":   "Random Forest",
        "params":  {"n_trees": n_trees, "max_depth": max_depth, "seed": seed, "max_features": max_features},
        "forecasts": forecasts,
    }


def compute_ensemble_forecast(series: list[float], horizon: int = 4,
                              weights: Optional[dict] = None,
                              fred_matrix: Optional[dict] = None,
                              backtest_sigmas: Optional[dict] = None) -> dict:
    """
    Equal-weight (1/3) blend of ARIMA, Prophet-like, Random Forest.
    After backtesting, weights are recalibrated by inverse-MAPE.
    `fred_matrix`, when given, feeds lag-aligned FRED indicators into the
    Random Forest leg only — ARIMA/Prophet-like remain univariate.

    `backtest_sigmas`, when given ({model_name: resid_std}, e.g. from
    walk_forward_backtest()'s model_metrics[name]["resid_std"]), replaces the
    blended-CI approach below with one derived from each leg's REAL
    out-of-sample backtest error instead of blending each leg's own in-sample
    fit sigma. In-sample sigma is measured on the same data that already
    minimized it — always optimistic, and severely so for Prophet-like
    (6 parameters fit on as few as 8 points leaves almost no spare degrees of
    freedom). The two legs' widths are combined as a weighted-variance sum
    (assumes the legs' errors are roughly independent — a simplification, but
    a far more honest starting point than reusing in-sample sigma).
    """
    arima  = fit_arima(series, horizon=horizon)
    prophet = fit_prophet_like(series, horizon=horizon)
    rf     = fit_random_forest(series, horizon=horizon, fred_matrix=fred_matrix)

    models = [arima, prophet, rf]
    names  = ["ARIMA", "Prophet-like", "Random Forest"]

    if weights is None:
        w = {name: 1.0 / 3.0 for name in names}
    else:
        w = weights

    combined_sigma = None
    if backtest_sigmas:
        combined_var = sum(
            (w.get(name, 0.0) ** 2) * (backtest_sigmas[name] ** 2)
            for name in names
            if backtest_sigmas.get(name) is not None
        )
        if combined_var > 0:
            combined_sigma = math.sqrt(combined_var)

    forecasts = []
    for h in range(horizon):
        blend_pt = 0.0
        blend_lo = 0.0
        blend_hi = 0.0
        total_w  = 0.0
        per_model = {}
        for name, m in zip(names, models):
            pts = m.get("forecasts", [])
            if h < len(pts):
                wt = w.get(name, 1.0 / 3.0)
                blend_pt += wt * pts[h]["point"]
                blend_lo += wt * pts[h]["ci_lower"]
                blend_hi += wt * pts[h]["ci_upper"]
                total_w  += wt
                per_model[name] = pts[h]["point"]
        if total_w > 0:
            blend_pt /= total_w
            blend_lo /= total_w
            blend_hi /= total_w
        if combined_sigma is not None:
            blend_lo = blend_pt - 1.96 * combined_sigma * math.sqrt(h + 1)
            blend_hi = blend_pt + 1.96 * combined_sigma * math.sqrt(h + 1)
        forecasts.append({
            "horizon":   h + 1,
            "point":     round(blend_pt, 4),
            "ci_lower":  round(blend_lo, 4),
            "ci_upper":  round(blend_hi, 4),
            "per_model": per_model,
        })

    return {
        "model":      "Ensemble",
        "weights":    w,
        "components": {n: m for n, m in zip(names, models)},
        "forecasts":  forecasts,
        "ci_source": "out_of_sample_backtest" if combined_sigma is not None else "in_sample_fit",
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — WALK-FORWARD BACKTESTING
# ══════════════════════════════════════════════════════════════════════════════

def _mape(actuals, preds):
    pairs = [(a, p) for a, p in zip(actuals, preds) if a != 0]
    if not pairs:
        return None
    return float(np.mean([abs((a - p) / a) for a, p in pairs]) * 100)

def _rmse(actuals, preds):
    if not actuals:
        return None
    return float(np.sqrt(np.mean([(a - p) ** 2 for a, p in zip(actuals, preds)])))

def _r_squared(actuals, preds):
    if not actuals:
        return None
    a = np.array(actuals)
    p = np.array(preds)
    ss_res = float(np.sum((a - p) ** 2))
    ss_tot = float(np.sum((a - np.mean(a)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else None

def _directional_metrics(actuals, preds):
    if len(actuals) < 2:
        return {"precision": None, "recall": None, "f1": None}
    tp = fp = fn = tn = 0
    for i in range(1, len(actuals)):
        actual_up = actuals[i] > actuals[i - 1]
        pred_up   = preds[i]  > preds[i - 1]
        if actual_up and pred_up:   tp += 1
        elif not actual_up and pred_up: fp += 1
        elif actual_up and not pred_up: fn += 1
        else: tn += 1
    prec   = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    f1     = (2 * prec * recall / (prec + recall)) if (prec and recall) else None
    return {"precision": prec, "recall": recall, "f1": f1}


def _backtest_model(series: list[float], model_fn, min_train: int = 8,
                     fred_matrix: Optional[dict] = None) -> dict:
    n       = len(series)
    actuals = []
    preds   = []
    for t in range(min_train, n):
        train = series[:t]
        result = model_fn(train, horizon=1, fred_matrix=fred_matrix)
        fcs = result.get("forecasts", [])
        if fcs:
            preds.append(fcs[0]["point"])
            actuals.append(series[t])

    dm = _directional_metrics(actuals, preds)
    # Real out-of-sample residual spread — computed from actual expanding-
    # window errors, not each model's own in-sample fit sigma (which is
    # optimistic: it's measured on the same data that already minimized it).
    # Needs >=3 points for a std worth reporting; None below that rather
    # than a number built on 1-2 residuals.
    resid_std = (
        float(np.std(np.array(actuals) - np.array(preds)))
        if len(actuals) >= 3 else None
    )
    return {
        "n_observations":  n,
        "n_backtest_steps": len(actuals),
        "mape":     _mape(actuals, preds),
        "rmse":     _rmse(actuals, preds),
        "r_squared": _r_squared(actuals, preds),
        "precision": dm["precision"],
        "recall":    dm["recall"],
        "f1":        dm["f1"],
        "resid_std": resid_std,
    }


def walk_forward_backtest(series: list[float], fred_matrix: Optional[dict] = None) -> dict:
    """
    Expanding-window 1-step-ahead validation across ARIMA, Prophet-like, and RF.
    Returns per-model metrics + ensemble weights calibrated by inverse-MAPE.
    `fred_matrix`, when given, is fed to the Random Forest leg only, so
    calibrated weights reflect the FRED-augmented RF's real backtested MAPE.
    """
    if not _HAS_NUMPY:
        return {"error": "numpy required"}

    if len(series) < 10:
        return {"error": "need ≥10 observations for backtesting"}

    results = {}
    for name, fn in [
        ("ARIMA",        lambda s, horizon, fred_matrix=None: fit_arima(s, horizon=horizon)),
        ("Prophet-like", lambda s, horizon, fred_matrix=None: fit_prophet_like(s, horizon=horizon)),
        ("Random Forest", lambda s, horizon, fred_matrix=None: fit_random_forest(s, horizon=horizon, fred_matrix=fred_matrix)),
    ]:
        results[name] = _backtest_model(series, fn, fred_matrix=fred_matrix)

    # Calibrate ensemble weights by inverse-MAPE
    mapes = {name: r["mape"] for name, r in results.items() if r.get("mape") is not None}
    perfect = [name for name, m in mapes.items() if m == 0.0]
    if perfect:
        # Any model with 0 MAPE gets full weight (split equally among perfect models)
        w = 1.0 / len(perfect)
        weights = {name: round(w if name in perfect else 0.0, 4) for name in results}
    elif len(mapes) >= 2:
        inv = {name: 1.0 / m for name, m in mapes.items() if m > 0}
        total = sum(inv.values())
        weights = {name: round(v / total, 4) for name, v in inv.items()}
    else:
        weights = {"ARIMA": 1/3, "Prophet-like": 1/3, "Random Forest": 1/3}

    # Shrink toward equal-weight in proportion to how few out-of-sample
    # backtest steps actually back the calibration up. With min_train=8 and
    # this platform's typical ~13-quarter series, a "calibrated" weight can
    # rest on as few as 4-5 real out-of-sample points — treating that as
    # fully trustworthy risks locking the ensemble onto whichever model got
    # lucky over a handful of steps. alpha = k/(k+n_steps) is the standard
    # empirical-Bayes-style shrinkage form: few steps -> alpha near 1 (mostly
    # equal-weight); many steps -> alpha near 0 (mostly trust the
    # calibration). k=8 means ~5 steps (this platform's common case) still
    # gets pulled more than halfway to equal-weight, while ~20+ steps is
    # treated as trustworthy enough to mostly keep as-is.
    _SHRINKAGE_K = 8.0
    n_steps = min((results[name]["n_backtest_steps"] for name in weights if name in results), default=0)
    alpha = _SHRINKAGE_K / (_SHRINKAGE_K + n_steps)
    equal_w = 1.0 / len(weights) if weights else 0.0
    shrunk_weights = {
        name: round(alpha * equal_w + (1 - alpha) * w, 4)
        for name, w in weights.items()
    }

    return {
        "model_metrics":       results,
        "raw_calibrated_weights": weights,
        "calibrated_weights":  shrunk_weights,
        "weight_shrinkage": {"alpha": round(alpha, 4), "n_backtest_steps": n_steps, "k": _SHRINKAGE_K},
        "note": (
            "calibrated_weights are inverse-MAPE weights shrunk toward equal-weight "
            "based on how many out-of-sample backtest steps were available (see "
            "weight_shrinkage); raw_calibrated_weights is the unshrunk inverse-MAPE "
            "value for reference. Pass calibrated_weights to compute_ensemble_forecast(weights=...)."
        ),
    }


def backtest_sigmas_from(bt: dict) -> Optional[dict]:
    """Extract {model_name: resid_std} from a walk_forward_backtest() result
    for compute_ensemble_forecast(backtest_sigmas=...) — the out-of-sample
    residual spread each model actually produced during backtesting, used to
    build honest ensemble CI width instead of blending each leg's in-sample
    fit sigma. Returns None if bt has no usable model_metrics (e.g. the
    {"error": ...} shape walk_forward_backtest returns for too-short series)."""
    metrics = bt.get("model_metrics") if bt else None
    if not metrics:
        return None
    return {name: r.get("resid_std") for name, r in metrics.items()}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — RSS SIGNAL GRADING
# ══════════════════════════════════════════════════════════════════════════════

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "Trade Compliance":     ["tariff", "export control", "sanction", "trade restriction", "customs",
                             "import duty", "itar", "ear", "embargo", "trade war"],
    "Cybersecurity":        ["breach", "cyber", "ransomware", "vulnerability", "exploit",
                             "malware", "data leak", "cisa", "incident", "hack", "phishing"],
    "Financial Reporting":  ["restatement", "material weakness", "sec investigation", "audit failure",
                             "gaap", "earnings manipulation", "fraud", "whistleblower", "icfr"],
    "Macro":                ["recession", "inflation", "fed funds", "interest rate", "gdp",
                             "unemployment", "credit tightening", "monetary policy", "stagflation"],
    "Supply Chain":         ["supply chain", "shortage", "logistics disruption", "procurement",
                             "vendor", "component", "inventory glut", "lead time", "single source"],
    "Regulatory":           ["regulation", "compliance failure", "enforcement action", "penalty",
                             "fine", "investigation", "subpoena", "litigation", "class action"],
    "Environmental":        ["climate", "emissions", "epa", "environmental violation",
                             "carbon", "sustainability failure", "spill", "esg"],
    "Competitive":          ["acquisition", "merger", "hostile takeover", "market share loss",
                             "competitor", "patent lawsuit", "price war", "disruption"],
}

SEVERITY_WEIGHTS: dict[str, float] = {
    "critical":      3.0,
    "urgent":        2.5,
    "immediate":     2.5,
    "violation":     2.5,
    "breach":        2.0,
    "failure":       2.0,
    "warning":       1.5,
    "alert":         1.5,
    "enforcement":   1.5,
    "concern":       1.0,
    "risk":          1.0,
    "investigation": 1.0,
    "proposed":      0.6,
    "review":        0.5,
    "update":        0.3,
    "announcement":  0.2,
}

RSS_SIGNAL_FEEDS: list[dict] = [
    {"name": "SEC EDGAR 8-K Current",
     "url":  "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&dateb=&owner=include&count=40&output=atom",
     "weight": 1.0, "domains": ["Financial Reporting", "Regulatory"]},
    {"name": "Federal Reserve Press Releases",
     "url":  "https://www.federalreserve.gov/feeds/press_all.xml",
     "weight": 0.9, "domains": ["Macro"]},
    {"name": "CISA Alerts",
     "url":  "https://www.cisa.gov/news.xml",
     "weight": 0.8, "domains": ["Cybersecurity"]},
    {"name": "BIS News",
     "url":  "https://www.bis.org/rss/index.htm",
     "weight": 0.7, "domains": ["Macro", "Financial Reporting"]},
    {"name": "EPA News Releases",
     "url":  "https://www.epa.gov/newsreleases/search/rss",
     "weight": 0.7, "domains": ["Environmental", "Regulatory"]},
]


def _grade_article(text: str, feed_weight: float) -> dict:
    """Score a single article for relevance, severity, velocity, and RAG."""
    words = text.lower().split()
    total_words = max(len(words), 1)

    domain_scores: dict[str, float] = {}
    for domain, kws in DOMAIN_KEYWORDS.items():
        hits = sum(1 for w in words if any(kw in w or w in kw for kw in kws))
        domain_scores[domain] = hits / total_words * 100  # per-100-words density

    relevance = max(domain_scores.values()) if domain_scores else 0.0
    top_domain = max(domain_scores, key=domain_scores.get) if domain_scores else "Unknown"

    severity = 0.0
    for kw, wt in SEVERITY_WEIGHTS.items():
        if kw in text.lower():
            severity += wt

    raw_velocity = relevance * severity * 5 * feed_weight
    velocity = min(5, max(0, round(raw_velocity)))

    if velocity >= 3:
        rag = "Red"
    elif velocity >= 2:
        rag = "Amber"
    else:
        rag = "Green"

    return {
        "relevance_score":  round(relevance, 3),
        "severity_score":   round(severity, 3),
        "velocity":         velocity,
        "rag_status":       rag,
        "top_domain":       top_domain,
        "domain_scores":    {k: round(v, 3) for k, v in domain_scores.items()},
    }


def compute_rss_signals(
    ticker: str = "",
    company_name: str = "",
    max_articles: int = 20,
) -> dict:
    """
    Fetch configured RSS feeds and grade each article.
    Returns graded signal summary with per-feed breakdown.
    """
    if not _HAS_FEEDPARSER:
        return {"error": "feedparser not installed (pip install feedparser)"}

    keyword_filter = [w.lower() for w in (ticker + " " + company_name).split() if len(w) > 2]
    all_signals = []

    for feed_cfg in RSS_SIGNAL_FEEDS:
        try:
            parsed = feedparser.parse(feed_cfg["url"])
            entries = parsed.get("entries", [])[:max_articles]
        except Exception as e:
            all_signals.append({"feed": feed_cfg["name"], "error": str(e)})
            continue

        feed_signals = []
        for entry in entries:
            title   = entry.get("title", "")
            summary = entry.get("summary", "")
            text    = (title + " " + summary)

            # Optional: filter to company-relevant articles
            if keyword_filter:
                if not any(kw in text.lower() for kw in keyword_filter):
                    continue

            grade = _grade_article(text, feed_cfg["weight"])
            feed_signals.append({
                "title":    title[:120],
                "date":     entry.get("published", ""),
                **grade,
            })

        if not feed_signals and not keyword_filter:
            for entry in entries:
                text = (entry.get("title", "") + " " + entry.get("summary", ""))
                grade = _grade_article(text, feed_cfg["weight"])
                feed_signals.append({
                    "title": entry.get("title", "")[:120],
                    "date":  entry.get("published", ""),
                    **grade,
                })

        rag_counts = {"Red": 0, "Amber": 0, "Green": 0}
        for s in feed_signals:
            rag_counts[s.get("rag_status", "Green")] += 1

        all_signals.append({
            "feed":       feed_cfg["name"],
            "url":        feed_cfg["url"],
            "domains":    feed_cfg["domains"],
            "articles_graded": len(feed_signals),
            "rag_summary": rag_counts,
            "signals":    feed_signals[:10],
        })

    # Aggregate
    all_rag = {"Red": 0, "Amber": 0, "Green": 0}
    for f in all_signals:
        if "rag_summary" in f:
            for k, v in f["rag_summary"].items():
                all_rag[k] += v

    return {
        "ticker":       ticker.upper() if ticker else "",
        "company_name": company_name,
        "feeds_checked": len(RSS_SIGNAL_FEEDS),
        "aggregate_rag": all_rag,
        "feed_results": all_signals,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — QoQ REVENUE MOMENTUM / SENTIMENT
# ══════════════════════════════════════════════════════════════════════════════

def compute_qoq_momentum(revenue_series: list[dict], window: int = 8) -> dict:
    """
    Rolling 8-quarter QoQ revenue momentum.

    score = (curr_Q - prev_Q) / prev_Q × 100, clamped [−25, +25]
    Trend: IMPROVING (>+5), DETERIORATING (<−5), STABLE
    """
    if len(revenue_series) < 2:
        return {"error": "need ≥2 quarters of revenue data"}

    recent = revenue_series[-window:] if len(revenue_series) >= window else revenue_series
    momentum_pts = []
    for i in range(1, len(recent)):
        prev = recent[i - 1]["value"]
        curr = recent[i]["value"]
        if prev and prev != 0:
            score = max(-25.0, min(25.0, (curr - prev) / prev * 100))
        else:
            score = 0.0
        momentum_pts.append({
            "quarter_end": recent[i]["quarter_end"],
            "score":       round(score, 2),
        })

    if not momentum_pts:
        return {"error": "insufficient data for momentum"}

    latest_score = momentum_pts[-1]["score"]
    avg_score    = sum(p["score"] for p in momentum_pts) / len(momentum_pts)

    if latest_score > 5:
        trend = "IMPROVING"
    elif latest_score < -5:
        trend = "DETERIORATING"
    else:
        trend = "STABLE"

    # Hedge ratio trend direction
    scores = [p["score"] for p in momentum_pts]
    if len(scores) >= 3:
        if scores[-1] > scores[-2] > scores[-3]:
            hedge_trend = "INCREASING_RISK_REDUCTION"
        elif scores[-1] < scores[-2] < scores[-3]:
            hedge_trend = "DECREASING_RISK_REDUCTION"
        else:
            hedge_trend = "STABLE"
    else:
        hedge_trend = "INSUFFICIENT_DATA"

    return {
        "quarters_analyzed": len(momentum_pts),
        "latest_score":      latest_score,
        "average_score":     round(avg_score, 2),
        "trend":             trend,
        "hedge_ratio_trend": hedge_trend,
        "momentum_series":   momentum_pts,
        "interpretation": {
            "IMPROVING":     "Revenue accelerating QoQ (>+5%)",
            "DETERIORATING": "Revenue contracting QoQ (<-5%)",
            "STABLE":        "Revenue flat or modest drift (-5% to +5%)",
        }[trend],
    }


def run_forecast_backtest(xbrl: dict, macro_info: Optional[dict], forecast_metric: str,
                           forecast_horizon: int, company_id: Optional[int] = None) -> dict:
    """Time-series forecasting + walk-forward backtesting for `forecast_metric`
    (re-derives FRED-correlated features via macro_info and re-optimizes
    ensemble weights via inverse-MAPE, see walk_forward_backtest), plus a
    parallel gross-margin forecast and any manually-uploaded monthly detail.

    Extracted out of run_full_analysis so reoptimization_tool.reoptimize_ticker
    can re-run just this layer — re-deriving FRED correlations and backtest
    metrics on drift — without the full ratio/Beneish/Altman/risk-score/RSS
    pipeline. Pure function of its inputs; no DB/network side effects beyond
    what macro_info/xbrl already carry. Returns {forecast, backtest?, monthly_series?}.
    """
    out: dict = {}
    q_series = extract_quarterly_series(xbrl, forecast_metric)
    if q_series:
        vals = [p["value"] for p in q_series]
        if len(vals) >= 8:
            fred_matrix = fred_meta = None
            if macro_info:
                fred_matrix, fred_meta = _build_fred_feature_matrix(
                    q_series, macro_info, forecast_metric, forecast_horizon)
            bt = walk_forward_backtest(vals, fred_matrix=fred_matrix)
            out["backtest"] = bt
            calibrated_w = bt.get("calibrated_weights")
            out["forecast"] = compute_ensemble_forecast(
                vals, horizon=forecast_horizon, weights=calibrated_w, fred_matrix=fred_matrix,
                backtest_sigmas=backtest_sigmas_from(bt),
            )
            out["forecast"]["metric"]  = forecast_metric
            out["forecast"]["quarters"] = [p["quarter_end"] for p in q_series]
            out["forecast"]["history"]  = q_series  # raw quarterly values for JS chart
            if fred_meta:
                out["forecast"]["fred_features_used"] = fred_meta
        else:
            out["forecast"] = {
                "note": f"Only {len(vals)} quarters available for {forecast_metric}; need ≥8",
                "history": q_series,
            }
    else:
        out["forecast"] = {"note": f"No quarterly {forecast_metric} data in XBRL"}

    # ── Monthly detail (manual uploads only — supplements the quarterly
    # forecast history above with finer resolution; never feeds ratios/
    # Beneish/Altman, see extract_monthly_series) ────────────────────────────
    monthly_series = extract_monthly_series(company_id, forecast_metric)
    if monthly_series:
        out["monthly_series"] = {"metric": forecast_metric, "history": monthly_series}

    # Gross margin quarterly history + ensemble forecast — needed by the JS forecast chart
    try:
        gp_series = extract_quarterly_series(xbrl, "GrossProfit")
        rev_map   = {p["quarter_end"]: p["value"] for p in q_series} if q_series else {}
        gm_history = []
        for gp in gp_series:
            rv = rev_map.get(gp["quarter_end"])
            if rv and rv > 0:
                gm = gp["value"] / rv * 100
                if 0 < gm < 100:
                    gm_history.append({"quarter_end": gp["quarter_end"], "value": round(gm, 2)})
        if gm_history:
            out["forecast"]["margin_history"] = gm_history
            # Ensemble forecast for gross margin (same models as revenue)
            gm_vals = [p["value"] for p in gm_history]
            if len(gm_vals) >= 8:
                try:
                    # Gross Margin = GrossProfit / Revenue has no correlation
                    # entry of its own — borrow GrossProfit's correlated
                    # indicators/lags, aligned to gm_history's own quarters.
                    gm_fred_matrix = gm_fred_meta = None
                    if macro_info:
                        gm_fred_matrix, gm_fred_meta = _build_fred_feature_matrix(
                            gm_history, macro_info, "GrossProfit", forecast_horizon)
                    gm_bt = walk_forward_backtest(gm_vals, fred_matrix=gm_fred_matrix)
                    gm_fc = compute_ensemble_forecast(
                        gm_vals, horizon=forecast_horizon,
                        weights=gm_bt.get("calibrated_weights"), fred_matrix=gm_fred_matrix,
                        backtest_sigmas=backtest_sigmas_from(gm_bt),
                    )
                    if gm_fred_meta:
                        gm_fc["fred_features_used"] = gm_fred_meta
                    out["forecast"]["margin_forecast"] = gm_fc
                    out["forecast"]["margin_backtest"] = gm_bt
                except Exception:
                    pass
    except Exception:
        pass

    return out


# ══════════════════════════════════════════════════════════════════════════════
# FULL ANALYSIS ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

def run_full_analysis(
    ticker: str,
    industry: str = "",
    fred_api_key: str = "",
    forecast_horizon: int = 4,
    forecast_metric: str = "Revenue",
    include_rss: bool = True,
    include_fred: bool = True,
) -> dict:
    """
    Run all 10 Dendrai Intelligenza models for a given ticker.

    Returns a structured dict with all model outputs ready for JSON serialization.
    """
    if not _HAS_EDGAR:
        return {"error": "edgar_tool.py not found in the same directory"}

    result: dict = {"ticker": ticker.upper(), "generated_at": datetime.now(timezone.utc).isoformat()}

    # ── Fetch financial data ──────────────────────────────────────────────────
    # build_company_xbrl branches on public (live EDGAR + manual overlay) vs.
    # private (manual data only, no CIK) — see its docstring.
    try:
        xbrl, company_meta = build_company_xbrl(ticker)
        cik        = company_meta.get("cik")
        sic        = company_meta.get("sic", "")
        company_id = company_meta.get("company_id")
        result["company_name"]    = company_meta["company_name"]
        result["cik"]             = cik
        result["sic"]             = sic
        result["sic_description"] = company_meta.get("sic_description", "")
        result["is_private"]      = company_meta.get("is_private", False)
    except Exception as e:
        return {"error": f"Company lookup failed: {e}"}

    # Detect industry if not supplied
    if not industry:
        industry = detect_industry(sic)
    result["industry"] = industry

    # ── 1. Financial Ratios ───────────────────────────────────────────────────
    ratios = compute_financial_ratios(xbrl)
    result["financial_ratios"] = {k: (round(v, 6) if v is not None else None) for k, v in ratios.items()}

    # ── 2. Beneish M-Score ────────────────────────────────────────────────────
    result["beneish_mscore"] = compute_beneish_mscore(ratios)

    # ── 2b. Altman Z''-Score ──────────────────────────────────────────────────
    result["altman_zscore"] = compute_altman_zscore(ratios)

    # ── 2c. Financial Risk Pipeline (liquidity shift, inventory/sales divergence;
    #        manual-JE velocity skipped here — no JE source wired into this
    #        entry point yet, see check_financial_risk_pipeline's docstring) ──
    try:
        result["financial_risk_pipeline"] = check_financial_risk_pipeline(ticker, xbrl)
    except Exception:
        pass

    # ── 3. Risk Scores ────────────────────────────────────────────────────────
    risk_result = compute_risk_scores(ratios, industry)
    result["risk_scores"] = risk_result

    # ── 4. Scenario Analysis ──────────────────────────────────────────────────
    result["scenario_analysis"] = compute_scenario_analysis(ratios, risk_result)

    # ── 5. Grey Swan Model ────────────────────────────────────────────────────
    q_rev = (ratios.get("revenue_now") or 0) / 4
    result["grey_swan"] = compute_grey_swan(risk_result, q_rev if q_rev > 0 else None)

    # ── 6. FRED Macro Indicators ──────────────────────────────────────────────
    macro_info = None
    if include_fred:
        result["macro_leading_indicators"] = get_macro_leading_indicators(
            ticker, industry, api_key=fred_api_key,
        )
        if result["macro_leading_indicators"].get("source") == "live_fred_analysis":
            macro_info = result["macro_leading_indicators"].get("result")

    # ── 7, 7b & 8. Time-Series Forecasting + Backtesting + monthly detail +
    # gross-margin forecast (see run_forecast_backtest — extracted so
    # reoptimization_tool can re-run just this layer on drift) ───────────────
    fb = run_forecast_backtest(xbrl, macro_info, forecast_metric, forecast_horizon, company_id)
    result["forecast"] = fb["forecast"]
    if "backtest" in fb:
        result["backtest"] = fb["backtest"]
    if "monthly_series" in fb:
        result["monthly_series"] = fb["monthly_series"]

    # ── 9. RSS Signal Grading ─────────────────────────────────────────────────
    if include_rss:
        result["rss_signals"] = compute_rss_signals(
            ticker=ticker, company_name=result.get("company_name", ""),
        )

    # ── 10. QoQ Momentum ─────────────────────────────────────────────────────
    rev_q = extract_quarterly_series(xbrl, "Revenue")
    if rev_q:
        result["qoq_momentum"] = compute_qoq_momentum(rev_q)
    else:
        result["qoq_momentum"] = {"note": "No quarterly Revenue data available"}

    # ── 11. Analyst KPI Series (EPS, OpMargin, NetIncome, FCF, EBITDA) ───────
    q_series = extract_quarterly_series(xbrl, forecast_metric)
    result["analyst_series"] = compute_analyst_series(xbrl, q_series or [], macro_info, forecast_horizon,
                                                        ticker=ticker, company_id=company_id)

    return result
