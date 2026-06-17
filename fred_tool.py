#!/usr/bin/env python3
"""
FRED Macro-Economic Correlation Tool

Fetches 30 macro-economic indicator series from the Federal Reserve Economic
Data (FRED) API and computes Pearson correlation with a target company's
quarterly financial metrics (from SEC EDGAR XBRL). Identifies indicators
with |r| >= threshold at 1-3 quarter leading lags and saves results to
fred_macro_indicators.json.

Requires a free FRED API key: https://fred.stlouisfed.org/docs/api/api_key.html
  Add to .env in this directory:  FRED_API_KEY=your_key_here

Usage:
    python fred_tool.py AAPL
    python fred_tool.py MSFT --min-r 0.80
    python fred_tool.py NVDA --output fred_macro_indicators.json
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, os.path.dirname(__file__))
from edgar_tool import XBRL_METRICS, get_company_info

# ── Optional scipy for p-values ───────────────────────────────────────────────

try:
    from scipy.stats import pearsonr as _scipy_pearsonr
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

# ── Constants ─────────────────────────────────────────────────────────────────

EDGAR_DATA_BASE = "https://data.sec.gov"
FRED_BASE = "https://api.stlouisfed.org/fred"

FRED_HEADERS = {"User-Agent": "FREDMacroTool/1.0 (research@example.com)"}
EDGAR_HEADERS = {
    "User-Agent": "FREDMacroTool/1.0 (research@example.com)",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json",
}

_NOW = datetime.now(timezone.utc)

# 5-year window for company financial data
ANALYSIS_START = (_NOW - timedelta(days=5 * 365 + 10)).strftime("%Y-%m-%d")
# 6-year window for FRED data (provides 1-3 quarter lead buffer)
FRED_START = (_NOW - timedelta(days=6 * 365 + 100)).strftime("%Y-%m-%d")

# Minimum quarter pairs required for a valid correlation
MIN_PAIRS = 6

# ── FRED Series Catalog ───────────────────────────────────────────────────────
#
# 30 series spanning output, employment, demand, inflation, interest rates,
# credit, money supply, trade, housing, manufacturing, and leading indicators.
# agg_method: FRED aggregation when converting to quarterly (avg / eop / sum).

FRED_SERIES: dict[str, dict] = {
    # Output / Activity
    "GDPC1": {
        "name": "Real GDP",
        "category": "Output",
        "agg_method": "avg",
        "units": "Billions of Chained 2017 Dollars",
        "description": "Inflation-adjusted total U.S. economic output (quarterly native)",
    },
    "INDPRO": {
        "name": "Industrial Production Index",
        "category": "Output",
        "agg_method": "avg",
        "units": "Index 2017=100",
        "description": "Output of manufacturing, mining, and utilities sectors",
    },
    "CP": {
        "name": "Corporate Profits After Tax",
        "category": "Corporate",
        "agg_method": "avg",
        "units": "Billions of Dollars",
        "description": "Economy-wide after-tax corporate profits (quarterly native)",
    },
    "CFNAI": {
        "name": "Chicago Fed National Activity Index",
        "category": "Leading Indicators",
        "agg_method": "avg",
        "units": "Index (0 = trend growth)",
        "description": "Composite of 85 economic indicators; values above 0 indicate above-trend growth",
    },
    # Employment
    "PAYEMS": {
        "name": "Total Nonfarm Payrolls",
        "category": "Employment",
        "agg_method": "avg",
        "units": "Thousands of Persons",
        "description": "Total number of U.S. nonfarm employees",
    },
    "UNRATE": {
        "name": "Unemployment Rate",
        "category": "Employment",
        "agg_method": "avg",
        "units": "Percent",
        "description": "Civilian unemployment rate",
    },
    "ICSA": {
        "name": "Initial Jobless Claims",
        "category": "Employment",
        "agg_method": "avg",
        "units": "Number",
        "description": "Weekly initial unemployment insurance claims (leading labor indicator)",
    },
    # Consumer Demand
    "PCE": {
        "name": "Personal Consumption Expenditures",
        "category": "Demand",
        "agg_method": "avg",
        "units": "Billions of Dollars",
        "description": "Total U.S. consumer spending on goods and services",
    },
    "RSXFS": {
        "name": "Retail Sales excl. Food Services",
        "category": "Demand",
        "agg_method": "avg",
        "units": "Millions of Dollars",
        "description": "Advance retail and food services sales, excluding food services",
    },
    "UMCSENT": {
        "name": "Consumer Sentiment (U. of Michigan)",
        "category": "Sentiment",
        "agg_method": "avg",
        "units": "Index 1966:Q1=100",
        "description": "Consumer confidence and near-term spending expectations",
    },
    # Inflation / Prices
    "CPIAUCSL": {
        "name": "CPI: All Urban Consumers",
        "category": "Inflation",
        "agg_method": "avg",
        "units": "Index 1982-1984=100",
        "description": "Broad consumer price inflation",
    },
    "PCEPI": {
        "name": "PCE Price Index",
        "category": "Inflation",
        "agg_method": "avg",
        "units": "Index 2017=100",
        "description": "Fed's preferred inflation measure",
    },
    "PPIACO": {
        "name": "PPI: All Commodities",
        "category": "Inflation",
        "agg_method": "avg",
        "units": "Index 1982=100",
        "description": "Producer-level input price pressures (leads CPI)",
    },
    "DCOILWTICO": {
        "name": "Crude Oil Price (WTI)",
        "category": "Commodities",
        "agg_method": "avg",
        "units": "Dollars per Barrel",
        "description": "West Texas Intermediate crude oil spot price",
    },
    # Interest Rates
    "FEDFUNDS": {
        "name": "Federal Funds Rate",
        "category": "Interest Rates",
        "agg_method": "avg",
        "units": "Percent",
        "description": "Overnight lending rate set by the Federal Reserve",
    },
    "DGS10": {
        "name": "10-Year Treasury Rate",
        "category": "Interest Rates",
        "agg_method": "avg",
        "units": "Percent",
        "description": "Benchmark long-term risk-free discount rate",
    },
    "DGS2": {
        "name": "2-Year Treasury Rate",
        "category": "Interest Rates",
        "agg_method": "avg",
        "units": "Percent",
        "description": "Short-to-medium term risk-free rate",
    },
    "T10Y2Y": {
        "name": "10Y-2Y Treasury Yield Spread",
        "category": "Interest Rates",
        "agg_method": "avg",
        "units": "Percent",
        "description": "Yield curve steepness; negative = inverted (recession signal)",
    },
    # Credit / Risk
    "BAA10Y": {
        "name": "Moody's Baa Corporate Bond Spread",
        "category": "Credit",
        "agg_method": "avg",
        "units": "Percent",
        "description": "Investment-grade credit risk premium above 10-year Treasuries",
    },
    "VIXCLS": {
        "name": "CBOE VIX Volatility Index",
        "category": "Market Volatility",
        "agg_method": "avg",
        "units": "Index",
        "description": "Equity market implied volatility / fear gauge",
    },
    "DRTSCILM": {
        "name": "C&I Loan Tightening Standards (Large Firms)",
        "category": "Credit Conditions",
        "agg_method": "avg",
        "units": "Percent (net)",
        "description": "Net pct of banks tightening commercial & industrial loan standards",
    },
    # Money Supply / Credit
    "M2SL": {
        "name": "M2 Money Supply",
        "category": "Money Supply",
        "agg_method": "avg",
        "units": "Billions of Dollars",
        "description": "Broad money supply including savings and small time deposits",
    },
    "TOTALSL": {
        "name": "Total Consumer Credit",
        "category": "Credit",
        "agg_method": "avg",
        "units": "Millions of Dollars",
        "description": "Total outstanding consumer installment debt",
    },
    # Trade / FX
    "BOPGSTB": {
        "name": "Trade Balance: Goods & Services",
        "category": "Trade",
        "agg_method": "avg",
        "units": "Millions of Dollars",
        "description": "U.S. goods and services trade balance (positive = surplus)",
    },
    "DTWEXBGS": {
        "name": "Trade-Weighted US Dollar Index (Broad)",
        "category": "Currency",
        "agg_method": "avg",
        "units": "Index Jan 2006=100",
        "description": "Broad USD strength against major trading partner currencies",
    },
    # Housing / Construction
    "HOUST": {
        "name": "Housing Starts: Total",
        "category": "Housing",
        "agg_method": "avg",
        "units": "Thousands of Units",
        "description": "New residential construction begins",
    },
    "PERMIT": {
        "name": "Building Permits",
        "category": "Housing",
        "agg_method": "avg",
        "units": "Thousands of Units",
        "description": "New residential building permits (1-2 months ahead of starts)",
    },
    # Business / Manufacturing
    "ISRATIO": {
        "name": "Business Inventories/Sales Ratio",
        "category": "Business",
        "agg_method": "avg",
        "units": "Ratio",
        "description": "Inventory-to-sales ratio; high = demand slowdown or supply glut",
    },
    "MANEMP": {
        "name": "Manufacturing Employees",
        "category": "Manufacturing",
        "agg_method": "avg",
        "units": "Thousands of Persons",
        "description": "Total manufacturing sector employment",
    },
    # Composite Leading Indicators
    "USALOLITONOSTSAM": {
        "name": "OECD Leading Indicators: USA",
        "category": "Leading Indicators",
        "agg_method": "avg",
        "units": "Ratio",
        "description": "OECD composite designed to anticipate turning points 6-9 months ahead",
    },
}

# Financial metrics sourced from EDGAR XBRL (must match XBRL_METRICS keys or EBITDA)
TARGET_METRICS = [
    "Revenue",
    "GrossProfit",
    "OperatingIncome",
    "NetIncome",
    "EPS_Diluted",
    "EPS_Basic",
    "OperatingCashFlow",
    "TotalAssets",
    "StockholdersEquity",
    "EBITDA",  # computed: OperatingIncome + Depreciation
]

# Metrics that are point-in-time (balance sheet) vs. period (income statement)
_BALANCE_SHEET = {
    "TotalAssets", "CurrentAssets", "CurrentLiabilities", "TotalLiabilities",
    "StockholdersEquity", "Cash", "LongTermDebt", "Inventory",
    "AccountsReceivable", "SharesOutstanding",
}

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url: str, headers: dict, params: dict = None) -> Optional[dict]:
    time.sleep(0.1)
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"    Request failed [{url[:60]}…]: {e}", file=sys.stderr)
        return None


# ── Date / quarter utilities ──────────────────────────────────────────────────

_QTR_ENDS = ["03-31", "06-30", "09-30", "12-31"]


def _date_to_quarter_end(date_str: str) -> str:
    """Snap any YYYY-MM-DD to its calendar quarter-end date."""
    d = datetime.strptime(date_str[:10], "%Y-%m-%d")
    q = (d.month - 1) // 3
    return f"{d.year}-{_QTR_ENDS[q]}"


def _add_quarters(quarter_end: str, n: int) -> str:
    """Shift a quarter-end date by n calendar quarters (negative shifts back)."""
    d = datetime.strptime(quarter_end[:10], "%Y-%m-%d")
    total = d.year * 4 + (d.month - 1) // 3 + n
    year, q = divmod(total, 4)
    return f"{year}-{_QTR_ENDS[q]}"


def _period_days(start: str, end: str) -> Optional[int]:
    try:
        s = datetime.strptime(start[:10], "%Y-%m-%d")
        e = datetime.strptime(end[:10], "%Y-%m-%d")
        return (e - s).days
    except ValueError:
        return None


# ── EDGAR XBRL (unlimited data points) ───────────────────────────────────────

def _fetch_raw_xbrl(cik: str) -> dict:
    """Fetch company XBRL facts without edgar_tool's 24-point cap."""
    url = f"{EDGAR_DATA_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
    data = _get(url, EDGAR_HEADERS)
    return data.get("facts", {}).get("us-gaap", {}) if data else {}


def _extract_flow_metric(
    us_gaap: dict,
    tags: list[str],
) -> dict[str, float]:
    """
    Extract single-quarter (3-month period) values for an income-statement metric.
    Tries each tag in order; returns {quarter_end: value} for the analysis window.
    Prefers most-recently-filed value when multiple filings cover the same quarter.
    """
    for tag in tags:
        if tag not in us_gaap:
            continue
        unit_vals = (
            us_gaap[tag].get("units", {}).get("USD")
            or us_gaap[tag].get("units", {}).get("shares")
            or us_gaap[tag].get("units", {}).get("USD/shares")
            or []
        )
        best: dict[str, tuple[float, str]] = {}  # quarter_end → (val, filed)
        for dp in unit_vals:
            end = dp.get("end", "")
            start = dp.get("start", "")
            val = dp.get("val")
            filed = dp.get("filed", "")
            fp = dp.get("fp", "")
            form = dp.get("form", "")

            if val is None or not end or end < ANALYSIS_START:
                continue
            if form not in {"10-K", "10-Q", "20-F", "10-K/A", "10-Q/A"}:
                continue

            # Accept explicitly labelled quarterly fiscal periods
            is_quarterly = fp in {"Q1", "Q2", "Q3", "Q4"}
            # Fall back to period-length heuristic (60-105 days ≈ one quarter)
            if not is_quarterly:
                days = _period_days(start, end)
                if days is None or not (60 <= days <= 105):
                    continue

            prev = best.get(end)
            if prev is None or filed > prev[1]:
                best[end] = (float(val), filed)

        if best:
            return {q: v[0] for q, v in best.items()}

    return {}


def _extract_balance_metric(us_gaap: dict, tags: list[str]) -> dict[str, float]:
    """
    Extract point-in-time (balance sheet) values, snapped to quarter ends.
    Returns {quarter_end: value} for the analysis window.
    """
    for tag in tags:
        if tag not in us_gaap:
            continue
        unit_vals = (
            us_gaap[tag].get("units", {}).get("USD")
            or us_gaap[tag].get("units", {}).get("shares")
            or []
        )
        best: dict[str, tuple[float, str]] = {}
        for dp in unit_vals:
            end = dp.get("end", "")
            val = dp.get("val")
            filed = dp.get("filed", "")
            form = dp.get("form", "")

            if val is None or not end or end < ANALYSIS_START:
                continue
            if form not in {"10-K", "10-Q", "20-F", "10-K/A", "10-Q/A"}:
                continue

            q_end = _date_to_quarter_end(end)
            prev = best.get(q_end)
            if prev is None or filed > prev[1]:
                best[q_end] = (float(val), filed)

        if best:
            return {q: v[0] for q, v in best.items()}

    return {}


def extract_all_quarterly_financials(cik: str) -> dict[str, dict[str, float]]:
    """
    Return quarterly time-series for all target financial metrics.
    Keys are metric names; values are {quarter_end_date: value}.
    Skips metrics with fewer than MIN_PAIRS quarters of data.
    Also computes EBITDA = OperatingIncome + Depreciation.
    """
    us_gaap = _fetch_raw_xbrl(cik)
    if not us_gaap:
        return {}

    result: dict[str, dict[str, float]] = {}

    for metric, tags in XBRL_METRICS.items():
        if metric in _BALANCE_SHEET:
            series = _extract_balance_metric(us_gaap, tags)
        else:
            series = _extract_flow_metric(us_gaap, tags)

        if len(series) >= MIN_PAIRS:
            result[metric] = series

    # Compute EBITDA = OperatingIncome + Depreciation
    op = result.get("OperatingIncome", {})
    dep = result.get("Depreciation", {})
    if op and dep:
        ebitda = {q: op[q] + dep[q] for q in op if q in dep}
        if len(ebitda) >= MIN_PAIRS:
            result["EBITDA"] = ebitda

    return result


# ── FRED API ──────────────────────────────────────────────────────────────────

def fetch_fred_series(series_id: str, api_key: str, agg_method: str = "avg") -> dict[str, float]:
    """
    Fetch a FRED series aggregated to quarterly frequency.
    Returns {quarter_end_date: value}.
    """
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": FRED_START,
        "frequency": "q",
        "aggregation_method": agg_method,
        "output_type": 1,
    }
    data = _get(f"{FRED_BASE}/series/observations", FRED_HEADERS, params)
    if not data:
        return {}

    result: dict[str, float] = {}
    for obs in data.get("observations", []):
        date_str = obs.get("date", "")
        val_str = obs.get("value", ".")
        if not date_str or val_str in (".", "", "NA"):
            continue
        try:
            val = float(val_str)
        except ValueError:
            continue
        result[_date_to_quarter_end(date_str)] = val

    return result


# ── Pearson correlation ───────────────────────────────────────────────────────

def _pearson(x: list[float], y: list[float]) -> tuple[float, Optional[float]]:
    """Return (r, p_value). Uses scipy if available, otherwise pure Python (no p)."""
    n = len(x)
    if n < MIN_PAIRS:
        return 0.0, None

    if _HAS_SCIPY:
        r, p = _scipy_pearsonr(x, y)
        return float(r), float(p)

    # Pure-Python fallback: r only
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx * sy == 0:
        return 0.0, None
    r = max(-1.0, min(1.0, cov / (sx * sy)))
    return r, None


def _correlate_at_lag(
    financial: dict[str, float],
    macro: dict[str, float],
    lag: int,
) -> tuple[float, Optional[float], int]:
    """
    Pearson r between macro[Q] and financial[Q+lag].
    Shifts macro data forward by `lag` quarters to test its leading power.
    Returns (r, p_value, n_pairs).
    """
    lagged = {_add_quarters(q, lag): v for q, v in macro.items()}
    common = sorted(set(financial) & set(lagged))
    n = len(common)
    if n < MIN_PAIRS:
        return 0.0, None, n

    x = [lagged[q] for q in common]
    y = [financial[q] for q in common]
    r, p = _pearson(x, y)
    return r, p, n


# ── Main analysis ─────────────────────────────────────────────────────────────

def run_analysis(
    ticker: str,
    api_key: str,
    min_r: float = 0.85,
    lags: tuple[int, ...] = (1, 2, 3),
    output_path: Optional[Path] = None,
) -> dict:
    """
    Full pipeline:
      1. Fetch company quarterly financials from EDGAR XBRL
      2. Fetch 30 FRED macro series (6 years, aggregated to quarterly)
      3. Compute Pearson correlation at each lag for every combination
      4. Keep only indicators with |r| >= min_r at any tested lag
      5. Save full results + raw data to fred_macro_indicators.json

    Returns the result dict.
    """
    now_str = _NOW.isoformat()
    print(f"\n── FRED Macro Correlation: {ticker.upper()} ─────────────────────")

    # 1. Company financials
    print("  [1/4] Fetching EDGAR XBRL quarterly financials …")
    meta, _ = get_company_info(ticker)
    financial_series = extract_all_quarterly_financials(meta["cik"])
    metric_names = sorted(financial_series.keys())
    print(f"         {len(financial_series)} metrics: {', '.join(metric_names)}")
    if not financial_series:
        raise ValueError(f"No quarterly XBRL data found for {ticker.upper()}")

    # 2. FRED macro data
    print(f"  [2/4] Fetching {len(FRED_SERIES)} FRED macro series …")
    macro_data: dict[str, dict[str, float]] = {}
    for i, (sid, info) in enumerate(FRED_SERIES.items(), 1):
        print(f"         [{i:02d}/{len(FRED_SERIES)}] {sid}: {info['name']} …", end="", flush=True)
        series = fetch_fred_series(sid, api_key, info["agg_method"])
        if series:
            macro_data[sid] = series
            print(f" {len(series)}q")
        else:
            print(" FAILED")
    print(f"         {len(macro_data)}/{len(FRED_SERIES)} series fetched successfully")

    # 3. Lagged correlation analysis
    print(f"  [3/4] Pearson correlations (lags={list(lags)}, threshold |r|≥{min_r}) …")
    correlation_results: dict[str, list[dict]] = {}

    for fin_metric, fin_series in sorted(financial_series.items()):
        hits: list[dict] = []

        for sid, macro_series in macro_data.items():
            # Find the best lag for this (financial, macro) pair
            best = {"r": 0.0, "p": None, "lag": 0, "n": 0}
            for lag in lags:
                r, p, n = _correlate_at_lag(fin_series, macro_series, lag)
                if abs(r) > abs(best["r"]):
                    best = {"r": r, "p": p, "lag": lag, "n": n}

            if abs(best["r"]) >= min_r:
                info = FRED_SERIES[sid]
                entry: dict = {
                    "series_id": sid,
                    "name": info["name"],
                    "category": info["category"],
                    "units": info["units"],
                    "description": info["description"],
                    "optimal_lag_quarters": best["lag"],
                    "pearson_r": round(best["r"], 4),
                    "direction": "positive" if best["r"] >= 0 else "negative",
                    "n_quarter_pairs": best["n"],
                    "interpretation": (
                        f"{info['name']} {best['lag']}Q prior "
                        f"{'positively' if best['r'] >= 0 else 'negatively'} "
                        f"leads {fin_metric} (r={best['r']:+.3f}, n={best['n']})"
                    ),
                }
                if best["p"] is not None:
                    entry["p_value"] = round(best["p"], 6)
                    entry["significant_p05"] = best["p"] < 0.05
                hits.append(entry)

        hits.sort(key=lambda h: abs(h["pearson_r"]), reverse=True)
        correlation_results[fin_metric] = hits
        n_q = len(fin_series)
        print(f"         {fin_metric} ({n_q}q): {len(hits)} indicator(s) at |r|≥{min_r}")

    # 4. Assemble output document
    print("  [4/4] Writing output …")

    company_financials_out = {
        m: sorted(
            [{"quarter_end": q, "value": v} for q, v in s.items()],
            key=lambda x: x["quarter_end"],
        )
        for m, s in financial_series.items()
    }

    macro_series_out = {
        sid: {
            "name": FRED_SERIES[sid]["name"],
            "category": FRED_SERIES[sid]["category"],
            "units": FRED_SERIES[sid]["units"],
            "description": FRED_SERIES[sid]["description"],
            "aggregation_method": FRED_SERIES[sid]["agg_method"],
            "quarterly_observations": sorted(
                [{"quarter_end": q, "value": v} for q, v in series.items()],
                key=lambda x: x["quarter_end"],
            ),
        }
        for sid, series in macro_data.items()
    }

    result = {
        "generated_at": now_str,
        "ticker": ticker.upper(),
        "company_name": meta["company_name"],
        "cik": meta["cik_plain"],
        "sic": meta["sic"],
        "sic_description": meta["sic_description"],
        "analysis_period": {
            "company_financials_start": ANALYSIS_START,
            "fred_data_start": FRED_START,
            "end": _NOW.strftime("%Y-%m-%d"),
        },
        "parameters": {
            "min_correlation_threshold": min_r,
            "lags_tested_quarters": list(lags),
            "fred_series_attempted": len(FRED_SERIES),
            "fred_series_fetched": len(macro_data),
            "financial_metrics_analyzed": metric_names,
            "min_quarter_pairs_required": MIN_PAIRS,
            "scipy_available_for_pvalue": _HAS_SCIPY,
        },
        "correlation_results": correlation_results,
        "company_quarterly_financials": company_financials_out,
        "fred_macro_series": macro_series_out,
    }

    if output_path is None:
        output_path = Path("fred_macro_indicators.json")

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False, default=str)

    size_kb = output_path.stat().st_size / 1024
    total_hits = sum(len(v) for v in correlation_results.values())
    n_metrics_with_hits = sum(1 for v in correlation_results.values() if v)

    print(f"\n  ✓ {output_path}  ({size_kb:.1f} KB)")
    print(f"  Correlated pairs: {total_hits} across {n_metrics_with_hits} financial metrics")

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Correlate FRED macro indicators with company quarterly financials.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("ticker", help="NYSE/NASDAQ ticker, e.g. AAPL")
    parser.add_argument(
        "--min-r", type=float, default=0.85,
        help="Minimum |Pearson r| threshold (default: 0.85)",
    )
    parser.add_argument(
        "--lags", default="1,2,3",
        help="Comma-separated leading lags in quarters to test (default: 1,2,3)",
    )
    parser.add_argument(
        "--output", "-o", default="fred_macro_indicators.json",
        help="Output JSON file (default: fred_macro_indicators.json)",
    )
    parser.add_argument(
        "--api-key", default=None,
        help="FRED API key (default: $FRED_API_KEY env var)",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("FRED_API_KEY", "")
    if not api_key:
        print(
            "Error: FRED API key required.\n"
            "  Get a free key: https://fred.stlouisfed.org/docs/api/api_key.html\n"
            "  Set env:        export FRED_API_KEY=your_key\n"
            "  Or pass:        --api-key your_key",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        lags = tuple(int(x.strip()) for x in args.lags.split(","))
    except ValueError:
        print("Error: --lags must be comma-separated integers, e.g. '1,2,3'", file=sys.stderr)
        sys.exit(1)

    try:
        run_analysis(
            ticker=args.ticker,
            api_key=api_key,
            min_r=args.min_r,
            lags=lags,
            output_path=Path(args.output),
        )
        print("\nDone.")
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
