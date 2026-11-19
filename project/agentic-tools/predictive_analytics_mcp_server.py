#!/usr/bin/env python3
"""
Predictive Analytics MCP Server — Dendrai Intelligenza

Exposes 10 predictive analytics models as MCP tools for Claude Code / Claude Desktop.

── Models ──────────────────────────────────────────────────────────────────────
  1.  predictive_financial_ratios   Revenue growth, margins, DSRI, TATA, etc.
  2.  predictive_beneish_mscore     Earnings manipulation detection (M-Score)
  3.  predictive_industry_risks     8-vertical × 8-risk RAG scoring
  4.  predictive_scenario_analysis  Bear / Base / Bull deterministic scenarios
  5.  predictive_grey_swan          4-stage T+0→T+90 escalation cascade
  6.  predictive_macro_indicators   FRED leading indicator correlations
  7.  predictive_forecast           ARIMA / Prophet / RF / Ensemble with CI
  8.  predictive_backtest           Walk-forward MAPE, RMSE, R², directional F1
  9.  predictive_rss_signals        Relevance × severity RSS signal grading
  10. predictive_qoq_momentum       8-quarter QoQ revenue momentum trend
  11. predictive_full_analysis      All 10 models in one call

── Setup ────────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:
    {
      "mcpServers": {
        "predictive-analytics": {
          "command": "python",
          "args": ["/absolute/path/to/predictive_analytics_mcp_server.py"]
        }
      }
    }

Claude Code — add to .claude/settings.json:
    {
      "mcpServers": {
        "predictive-analytics": {
          "command": "python",
          "args": ["/absolute/path/to/predictive_analytics_mcp_server.py"]
        }
      }
    }

── Prerequisites ────────────────────────────────────────────────────────────────
  pip install -r requirements.txt
  (Optional) FRED_API_KEY env var for live macro correlations
"""

import json
import os
import sys

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from mcp_guards import audit_log, cap_output, check_rate_limit, validate_enum, validate_int_range, validate_ticker
from predictive_analytics_tool import (
    compute_financial_ratios,
    compute_beneish_mscore,
    compute_risk_scores,
    compute_scenario_analysis,
    compute_grey_swan,
    get_macro_leading_indicators,
    compute_ensemble_forecast,
    walk_forward_backtest,
    compute_rss_signals,
    compute_qoq_momentum,
    extract_quarterly_series,
    detect_industry,
    run_full_analysis,
    INDUSTRY_TEMPLATES,
)

try:
    from edgar_tool import get_company_info, fetch_xbrl_facts
    _HAS_EDGAR = True
except ImportError:
    _HAS_EDGAR = False

mcp = FastMCP("predictive-analytics")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _edgar_check() -> str:
    if not _HAS_EDGAR:
        return "Error: edgar_tool.py not found — place it in the same directory as this server."
    return ""

def _get_xbrl(ticker: str):
    err = _edgar_check()
    if err:
        raise RuntimeError(err)
    ticker = validate_ticker(ticker)
    meta, _ = get_company_info(ticker)
    xbrl    = fetch_xbrl_facts(meta["cik"])
    return meta, xbrl


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def predictive_financial_ratios(ticker: str) -> str:
    """
    Compute Dendrai Intelligenza financial ratios from EDGAR XBRL filings.

    Returns:
      Revenue growth (YoY), gross margin, R&D intensity, SGA intensity,
      net income margin, FCF margin, asset growth, cash ratio,
      TATA (Total Accruals to Total Assets), DSRI (Days Sales Receivable Index),
      SGI (Sales Growth Index), and GMI (Gross Margin Index).

    Args:
        ticker: NYSE/NASDAQ ticker symbol (e.g. NVDA, MSFT, AAPL)
    """
    try:
        check_rate_limit("predictive_financial_ratios", max_per_minute=15)
        audit_log("predictive_financial_ratios", ticker=ticker)
        meta, xbrl = _get_xbrl(ticker)
        ratios = compute_financial_ratios(xbrl)
        out = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in ratios.items()}
        return json.dumps({
            "ticker":       meta["ticker"],
            "company_name": meta["company_name"],
            "sic":          meta.get("sic", ""),
            "ratios":       out,
        }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def predictive_beneish_mscore(ticker: str) -> str:
    """
    Compute the Beneish M-Score for earnings manipulation detection.

    Uses the simplified 5-variable model (Beneish 1999):
      M = -4.84 + 0.920·DSRI + 0.528·GMI + 0.892·SGI + 4.679·TATA

    Interpretation:
      M > -1.78  → likely manipulator   (Red)
      M > -2.22  → gray zone            (Amber)
      M ≤ -2.22  → likely non-manipulator (Green)

    Args:
        ticker: NYSE/NASDAQ ticker symbol
    """
    try:
        check_rate_limit("predictive_beneish_mscore", max_per_minute=15)
        audit_log("predictive_beneish_mscore", ticker=ticker)
        meta, xbrl = _get_xbrl(ticker)
        ratios = compute_financial_ratios(xbrl)
        result = compute_beneish_mscore(ratios)
        return json.dumps({
            "ticker":       meta["ticker"],
            "company_name": meta["company_name"],
            **result,
        }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def predictive_industry_risks(
    ticker: str,
    industry: str = "",
) -> str:
    """
    Score 8 industry-specific risks using financial ratios from EDGAR.

    Each risk returns: base score, live score (after ratio-driven deltas),
    RAG status (Red ≥7.0 / Amber ≥5.0 / Green <5.0), velocity (−1 to +3),
    category, control environment, and peer benchmark.

    Supported industries (auto-detected from SIC if not specified):
      Semiconductors, Automotive OEM, Software & Cloud, Financial Services,
      Healthcare & Pharma, Energy & Utilities, Retail & Consumer, Generic

    Args:
        ticker:   NYSE/NASDAQ ticker symbol
        industry: Override auto-detected industry (optional)
    """
    try:
        check_rate_limit("predictive_industry_risks", max_per_minute=15)
        audit_log("predictive_industry_risks", ticker=ticker, industry=industry)
        meta, xbrl = _get_xbrl(ticker)
        sic = meta.get("sic", "")
        ind = industry.strip() if industry.strip() else detect_industry(sic)
        if ind not in INDUSTRY_TEMPLATES:
            ind = "Generic"
        ratios = compute_financial_ratios(xbrl)
        result = compute_risk_scores(ratios, ind)
        return json.dumps({
            "ticker":       meta["ticker"],
            "company_name": meta["company_name"],
            "sic":          sic,
            "detected_industry": ind,
            **result,
        }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def predictive_scenario_analysis(
    ticker: str,
    industry: str = "",
) -> str:
    """
    Build Bear / Base / Bull scenarios from live financial ratios and risk profile.

    Bear: dual red-risk materialisation + macro stress (-18% revenue, -380bps gross margin)
    Base: managed controls, revenue at current YoY trend, -50bps margin drift
    Bull: MAP ahead of schedule, risk step-down, +5-8% revenue upside

    Each scenario returns: revenue change %, projected revenue, gross margin impact (bps),
    projected gross margin, indicative net income, and a narrative description.

    Args:
        ticker:   NYSE/NASDAQ ticker symbol
        industry: Override auto-detected industry template (optional)
    """
    try:
        check_rate_limit("predictive_scenario_analysis", max_per_minute=15)
        audit_log("predictive_scenario_analysis", ticker=ticker, industry=industry)
        meta, xbrl = _get_xbrl(ticker)
        sic = meta.get("sic", "")
        ind = industry.strip() if industry.strip() else detect_industry(sic)
        if ind not in INDUSTRY_TEMPLATES:
            ind = "Generic"
        ratios = compute_financial_ratios(xbrl)
        risks  = compute_risk_scores(ratios, ind)
        result = compute_scenario_analysis(ratios, risks)
        return json.dumps({
            "ticker":       meta["ticker"],
            "company_name": meta["company_name"],
            "industry":     ind,
            "scenarios":    result,
        }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def predictive_grey_swan(
    ticker: str,
    industry: str = "",
) -> str:
    """
    Model a plausible-but-underweighted escalation cascade (Grey Swan).

    Identifies the highest-velocity Amber risk as the triggering event and
    projects a 4-stage timeline (T+0, T+30, T+60, T+90 days) with:
      - Score trajectory: base → +0.8 → +1.5 → +2.2
      - Impact estimates scaled from quarterly revenue
      - RAG status at each stage

    Args:
        ticker:   NYSE/NASDAQ ticker symbol
        industry: Override auto-detected industry template (optional)
    """
    try:
        check_rate_limit("predictive_grey_swan", max_per_minute=15)
        audit_log("predictive_grey_swan", ticker=ticker, industry=industry)
        meta, xbrl = _get_xbrl(ticker)
        sic = meta.get("sic", "")
        ind = industry.strip() if industry.strip() else detect_industry(sic)
        if ind not in INDUSTRY_TEMPLATES:
            ind = "Generic"
        ratios = compute_financial_ratios(xbrl)
        risks  = compute_risk_scores(ratios, ind)
        q_rev  = (ratios.get("revenue_now") or 0) / 4
        result = compute_grey_swan(risks, q_rev if q_rev > 0 else None)
        return json.dumps({
            "ticker":       meta["ticker"],
            "company_name": meta["company_name"],
            "industry":     ind,
            **result,
        }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def predictive_macro_indicators(
    ticker: str,
    industry: str = "",
    min_correlation: float = 0.60,
    lags: str = "1,2,3,4",
) -> str:
    """
    Identify leading FRED macro indicators correlated with the company's financials.

    Without a FRED API key set in .env, returns pre-computed industry-specific
    Pearson correlations and lead times for canonical macro series.

    With FRED_API_KEY set in .env, runs live correlation analysis across 30 FRED
    series (GDP, unemployment, CPI, VIX, yield curve, credit spreads, and more).

    Get a free FRED API key at: https://fred.stlouisfed.org/docs/api/api_key.html
    Then add FRED_API_KEY=<your_key> to the .env file in this directory.

    Args:
        ticker:          NYSE/NASDAQ ticker symbol
        industry:        Override auto-detected industry (optional)
        min_correlation: Minimum |Pearson r| to include (default 0.60)
        lags:            Comma-separated lags in quarters (default "1,2,3,4")
    """
    try:
        check_rate_limit("predictive_macro_indicators", max_per_minute=10)
        audit_log("predictive_macro_indicators", ticker=ticker, industry=industry)
        meta, _ = _get_xbrl(ticker) if _HAS_EDGAR else ({}, {})
        sic = meta.get("sic", "") if meta else ""
        ind = industry.strip() if industry.strip() else detect_industry(sic)
        if ind not in INDUSTRY_TEMPLATES:
            ind = "Generic"
        fred_api_key = os.environ.get("FRED_API_KEY", "").strip()
        result = get_macro_leading_indicators(
            ticker=ticker, industry=ind, api_key=fred_api_key,
            lags=lags, min_r=min_correlation,
        )
        return json.dumps({
            "ticker":   meta.get("ticker", ticker.upper()) if meta else ticker.upper(),
            "industry": ind,
            **result,
        }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def predictive_forecast(
    ticker: str,
    metric: str = "Revenue",
    horizon: int = 4,
    model: str = "ensemble",
) -> str:
    """
    Forecast a financial metric using time-series models with 95% confidence intervals.

    Models available:
      arima     — ARIMA(2,1,1) via OLS; expanding CI ±1.96σ√h
      prophet   — Linear trend + 2 Fourier seasonal terms (period=4 quarters)
      rf        — Random Forest: 25 bootstrap trees, depth 4, seeded PRNG
      ensemble  — Inverse-MAPE weighted blend (default, recalibrated by backtest)

    Metrics: Revenue, GrossProfit, OperatingIncome, NetIncome, OperatingCashFlow,
             TotalAssets, Cash, ResearchAndDevelopment, and any XBRL metric.

    Args:
        ticker:  NYSE/NASDAQ ticker symbol
        metric:  Financial metric to forecast (default "Revenue")
        horizon: Number of quarters to forecast (default 4)
        model:   One of arima | prophet | rf | ensemble (default "ensemble")
    """
    try:
        check_rate_limit("predictive_forecast", max_per_minute=10)
        audit_log("predictive_forecast", ticker=ticker, metric=metric, model=model)
        meta, xbrl = _get_xbrl(ticker)
        q_series = extract_quarterly_series(xbrl, metric)
        if not q_series:
            return f"Error: no quarterly {metric} data found for {ticker.upper()}"
        vals = [p["value"] for p in q_series]
        if len(vals) < 8:
            return f"Error: only {len(vals)} quarters of {metric} data; need ≥8"

        horizon = validate_int_range(horizon, 1, 12, "horizon")
        model_lc = validate_enum(model, {"arima", "prophet", "rf", "ensemble"}, "model", default="ensemble").lower()
        if model_lc == "arima":
            from predictive_analytics_tool import fit_arima
            result = fit_arima(vals, horizon=horizon)
        elif model_lc in ("prophet", "prophet-like"):
            from predictive_analytics_tool import fit_prophet_like
            result = fit_prophet_like(vals, horizon=horizon)
        elif model_lc in ("rf", "random_forest", "random forest"):
            from predictive_analytics_tool import fit_random_forest
            result = fit_random_forest(vals, horizon=horizon)
        else:
            bt = walk_forward_backtest(vals)
            result = compute_ensemble_forecast(
                vals, horizon=horizon, weights=bt.get("calibrated_weights"),
            )
            result["backtest_metrics"] = bt.get("model_metrics", {})

        return json.dumps({
            "ticker":       ticker.upper(),
            "company_name": meta["company_name"],
            "metric":       metric,
            "history_quarters": [p["quarter_end"] for p in q_series],
            "history_values":   [p["value"] for p in q_series],
            **result,
        }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def predictive_backtest(
    ticker: str,
    metric: str = "Revenue",
) -> str:
    """
    Run walk-forward expanding-window backtesting across all 3 base models.

    Each model is evaluated on 1-step-ahead forecasts using all available
    quarterly data for the specified metric. Returns:
      MAPE, RMSE, R² (coefficient of determination)
      Directional precision, recall, F1 (predicted up/down correctly?)
      Calibrated ensemble weights (inverse-MAPE normalised)

    Args:
        ticker: NYSE/NASDAQ ticker symbol
        metric: Financial metric to backtest (default "Revenue")
    """
    try:
        check_rate_limit("predictive_backtest", max_per_minute=10)
        audit_log("predictive_backtest", ticker=ticker, metric=metric)
        meta, xbrl = _get_xbrl(ticker)
        q_series = extract_quarterly_series(xbrl, metric)
        if not q_series:
            return f"Error: no quarterly {metric} data found for {ticker.upper()}"
        vals = [p["value"] for p in q_series]
        if len(vals) < 10:
            return f"Error: only {len(vals)} quarters; need ≥10 for backtesting"
        result = walk_forward_backtest(vals)
        return json.dumps({
            "ticker":       meta["ticker"],
            "company_name": meta["company_name"],
            "metric":       metric,
            "n_quarters":   len(vals),
            "period":       f"{q_series[0]['quarter_end']} → {q_series[-1]['quarter_end']}",
            **result,
        }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def predictive_rss_signals(
    ticker: str,
    company_name: str = "",
    max_articles: int = 20,
) -> str:
    """
    Grade live RSS signals using the Dendrai relevance × severity pipeline.

    Fetches articles from: SEC EDGAR 8-K, Federal Reserve, CISA, BIS, EPA.
    For each article computes:
      Relevance — keyword density against 8 domain vocabularies
      Severity  — urgency-word weighted sum (e.g. "critical" +3.0, "violation" +2.5)
      Velocity  = relevance × severity × 5 × feedWeight, rounded to [0, 5]
      RAG       = Red (≥3), Amber (≥2), Green (<2)

    Domain vocabularies: Trade Compliance, Cybersecurity, Financial Reporting,
      Macro, Supply Chain, Regulatory, Environmental, Competitive.

    Args:
        ticker:       NYSE/NASDAQ ticker for keyword filtering (optional)
        company_name: Company name for additional keyword filtering (optional)
        max_articles: Max articles to fetch per feed (default 20)
    """
    try:
        check_rate_limit("predictive_rss_signals", max_per_minute=10)
        audit_log("predictive_rss_signals", ticker=ticker or "(none)")
        ticker = validate_ticker(ticker) if ticker else ticker
        max_articles = validate_int_range(max_articles, 1, 50, "max_articles")
        result = compute_rss_signals(
            ticker=ticker,
            company_name=company_name,
            max_articles=max_articles,
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def predictive_qoq_momentum(
    ticker: str,
    window: int = 8,
) -> str:
    """
    Compute rolling QoQ revenue momentum over the past N quarters.

    score = (curr_Q − prev_Q) / prev_Q × 100, clamped to [−25, +25]
    Trend classification:
      IMPROVING    → score > +5%
      DETERIORATING → score < −5%
      STABLE        → between −5% and +5%

    Also derives hedge ratio trend from 3-quarter momentum trajectory.

    Args:
        ticker: NYSE/NASDAQ ticker symbol
        window: Number of quarters to analyse (default 8)
    """
    try:
        check_rate_limit("predictive_qoq_momentum", max_per_minute=15)
        audit_log("predictive_qoq_momentum", ticker=ticker, window=window)
        window = validate_int_range(window, 2, 40, "window")
        meta, xbrl = _get_xbrl(ticker)
        rev_q = extract_quarterly_series(xbrl, "Revenue")
        if not rev_q:
            return f"Error: no quarterly Revenue data found for {ticker.upper()}"
        result = compute_qoq_momentum(rev_q, window=window)
        return json.dumps({
            "ticker":       meta["ticker"],
            "company_name": meta["company_name"],
            **result,
        }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def predictive_full_analysis(
    ticker: str,
    industry: str = "",
    forecast_horizon: int = 4,
    forecast_metric: str = "Revenue",
    include_rss: bool = True,
    include_fred: bool = True,
) -> str:
    """
    Run all 10 Dendrai Intelligenza predictive analytics models in a single call.

    Returns a comprehensive JSON report including:
      1.  Financial ratios (revenue growth, margins, DSRI, TATA, SGI, GMI)
      2.  Beneish M-Score (earnings manipulation, RAG classification)
      3.  Industry risk scores (8 risks, RAG per risk, velocity)
      4.  Scenario analysis (Bear / Base / Bull)
      5.  Grey Swan model (T+0→T+90 cascade, impact ladder)
      6.  FRED macro leading indicators (FRED_API_KEY from .env, or industry benchmarks)
      7.  Ensemble time-series forecast (ARIMA + Prophet + RF blend)
      8.  Walk-forward backtest (MAPE, RMSE, R², directional F1)
      9.  RSS signal grading (relevance × severity, per-feed RAG)
      10. QoQ revenue momentum (8-quarter trend, hedge ratio direction)

    Note: Full analysis can take 30-90 seconds due to EDGAR rate limits.

    Args:
        ticker:           NYSE/NASDAQ ticker symbol (e.g. NVDA, AAPL, JPM)
        industry:         Override auto-detected industry (optional)
        forecast_horizon: Quarters ahead to forecast (default 4)
        forecast_metric:  Metric to forecast: Revenue | GrossProfit | NetIncome | etc.
        include_rss:      Fetch and grade live RSS feeds (default True)
        include_fred:     Include macro indicator analysis (default True)
    """
    try:
        check_rate_limit("predictive_full_analysis", max_per_minute=5)
        audit_log("predictive_full_analysis", ticker=ticker, industry=industry,
                  include_rss=include_rss, include_fred=include_fred)
        fred_api_key = os.environ.get("FRED_API_KEY", "").strip()
        result = run_full_analysis(
            ticker=ticker,
            industry=industry,
            fred_api_key=fred_api_key,
            forecast_horizon=forecast_horizon,
            forecast_metric=forecast_metric,
            include_rss=include_rss,
            include_fred=include_fred,
        )
        return cap_output(json.dumps(result, indent=2))
    except Exception as e:
        return f"Error running full analysis: {e}"


@mcp.tool()
def predictive_list_industries() -> str:
    """
    List all supported industry templates and the risks scored within each.

    Returns industry names, risk names, categories, and base scores.
    Use these names as the `industry` parameter in other predictive_ tools.
    """
    try:
        check_rate_limit("predictive_list_industries")
        audit_log("predictive_list_industries")
    except ValueError as e:
        return f"Error: {e}"
    out = {}
    for ind, risks in INDUSTRY_TEMPLATES.items():
        out[ind] = [
            {"name": r["name"], "category": r["category"], "base_score": r["base"]}
            for r in risks
        ]
    return json.dumps({
        "industries": list(INDUSTRY_TEMPLATES.keys()),
        "risk_templates": out,
        "rag_thresholds": {"Red": "score ≥ 7.0", "Amber": "score ≥ 5.0", "Green": "score < 5.0"},
    }, indent=2)


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
