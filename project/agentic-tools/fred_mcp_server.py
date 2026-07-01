#!/usr/bin/env python3
"""
FRED Macro MCP Server

Exposes FRED macro-economic correlation tools for Claude Code and Claude Desktop.
Finds the leading macroeconomic indicators most correlated with a company's
quarterly financials (Revenue, GrossProfit, OperatingIncome, NetIncome, EBITDA,
EPS, OperatingCashFlow, TotalAssets, StockholdersEquity) and saves data to
fred_macro_indicators.json.

── Prerequisites ──────────────────────────────────────────────────────────────

1. Get a free FRED API key at: https://fred.stlouisfed.org/docs/api/api_key.html
2. Add it to a .env file in the same directory as this script:
       FRED_API_KEY=your_key_here
3. Install dependencies:  pip install -r requirements.txt

The server loads .env automatically via python-dotenv on startup.

── Claude Desktop ─ ~/.claude/claude_desktop_config.json ──────────────────────

    {
      "mcpServers": {
        "fred-macro": {
          "command": "python",
          "args": ["/absolute/path/to/fred_mcp_server.py"]
        }
      }
    }

── Claude Code ─ .claude/settings.json ────────────────────────────────────────

    {
      "mcpServers": {
        "fred-macro": {
          "command": "python",
          "args": ["/absolute/path/to/fred_mcp_server.py"]
        }
      }
    }

── Available tools ─────────────────────────────────────────────────────────────
    fred_macro_correlations   Find leading macro indicators for a company's financials
    fred_list_series          List all 30 FRED macro series in the catalog
    fred_load_analysis        Load and summarize a saved fred_macro_indicators.json
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from mcp_guards import audit_log, check_rate_limit, check_read_only, confine_path, validate_ticker
from fred_tool import FRED_SERIES, TARGET_METRICS, run_analysis

mcp = FastMCP("fred-macro")


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def fred_macro_correlations(
    ticker: str,
    min_correlation: float = 0.85,
    lags: str = "1,2,3",
    output_file: str = "fred_macro_indicators.json",
) -> str:
    """
    Identify leading macro-economic indicators from FRED that are most correlated
    with the target company's quarterly financial metrics over the past 5 years.

    Fetches 30 FRED macro series (GDP, unemployment, CPI, Fed funds rate, yield
    curve, VIX, consumer sentiment, credit spreads, trade balance, housing starts,
    corporate profits, and more) and computes Pearson correlation against:
      Revenue, GrossProfit, OperatingIncome, NetIncome, EBITDA, EPS (basic/diluted),
      OperatingCashFlow, TotalAssets, StockholdersEquity.

    Tests 1-, 2-, and 3-quarter leading lags to find indicators where the macro
    data from prior quarters predicts the company's future financial performance.
    Only indicators with |Pearson r| >= min_correlation are returned.

    Results (correlations + 5-6 years of raw quarterly data) are saved to
    fred_macro_indicators.json for further analysis.

    Requires FRED_API_KEY to be set in .env (free key at
    https://fred.stlouisfed.org/docs/api/api_key.html).

    Args:
        ticker:           NYSE/NASDAQ ticker symbol (e.g. AAPL, MSFT, NVDA)
        min_correlation:  Minimum absolute Pearson r to include (default 0.85)
        lags:             Comma-separated leading lags in quarters to test (default "1,2,3")
        output_file:      Path for the output JSON file (default "fred_macro_indicators.json")
    """
    try:
        check_rate_limit("fred_macro_correlations", max_per_minute=10)
        check_read_only("FRED analysis file save")
        ticker = validate_ticker(ticker)
        safe_out = confine_path(output_file)
        audit_log("fred_macro_correlations", ticker=ticker, output_file=safe_out.name)
    except ValueError as e:
        return f"Error: {e}"

    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        return (
            "Error: FRED API key is required.\n\n"
            "  1. Get a free key at: https://fred.stlouisfed.org/docs/api/api_key.html\n"
            "  2. Set environment variable: export FRED_API_KEY=your_key\n"
            "     Or pass directly:         fred_api_key='your_key'\n\n"
            "The FRED API is free and requires only a quick registration."
        )

    try:
        lag_list = tuple(int(x.strip()) for x in lags.split(","))
    except ValueError:
        return "Error: 'lags' must be comma-separated integers, e.g. '1,2,3'"

    if not (0 < min_correlation <= 1.0):
        return "Error: min_correlation must be between 0 and 1 (e.g. 0.85)"

    try:
        result = run_analysis(
            ticker=ticker,
            api_key=api_key,
            min_r=min_correlation,
            lags=lag_list,
            output_path=safe_out,
        )
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error running analysis: {type(e).__name__}: {e}"

    # ── Build a concise human-readable summary ─────────────────────────────────
    params = result["parameters"]
    corr = result.get("correlation_results", {})

    lines = [
        f"Macro-Economic Correlation Analysis",
        f"  Company:   {result['company_name']} ({result['ticker']})",
        f"  SIC:       {result['sic']} — {result['sic_description']}",
        f"  Period:    {result['analysis_period']['company_financials_start']} "
        f"→ {result['analysis_period']['end']}",
        f"  Threshold: |r| ≥ {params['min_correlation_threshold']} "
        f"| Lags: {params['lags_tested_quarters']}Q",
        f"  FRED data: {params['fred_series_fetched']}/{params['fred_series_attempted']} "
        f"series fetched",
        f"  P-values:  {'included (scipy)' if params['scipy_available_for_pvalue'] else 'unavailable (install scipy)'}",
        "",
        "── Correlated Leading Indicators ─────────────────────────────────────────",
    ]

    total_hits = 0
    for metric, hits in sorted(corr.items()):
        if not hits:
            continue
        total_hits += len(hits)
        n_q = len(result["company_quarterly_financials"].get(metric, []))
        lines.append(f"\n{metric}  ({n_q} quarters of data):")
        for h in hits:
            p_str = ""
            if "p_value" in h:
                sig = "✓" if h.get("significant_p05") else "~"
                p_str = f", p={h['p_value']:.4f}{sig}"
            lines.append(
                f"  [{h['optimal_lag_quarters']}Q lead]  "
                f"{h['series_id']:<22} {h['name']:<38} "
                f"r={h['pearson_r']:+.3f}{p_str}"
            )
            lines.append(f"              Category: {h['category']} | {h['description']}")

    if total_hits == 0:
        lines.append(
            f"\n  No indicators met the |r| ≥ {min_correlation} threshold.\n"
            f"  Consider lowering min_correlation (try 0.70 or 0.75).\n"
            f"  This can occur with companies that have high earnings volatility,\n"
            f"  sector-specific drivers not captured in broad macro series,\n"
            f"  or insufficient overlapping quarters (< {params['min_quarter_pairs_required']})."
        )
    else:
        lines.append(f"\n  Total correlated pairs found: {total_hits}")

    lines.append(f"\n✓ Full data saved to: {output_file}")
    lines.append(
        "  The JSON includes raw quarterly macro data and company financials\n"
        "  for use in further statistical analysis or charting."
    )

    return "\n".join(lines)


@mcp.tool()
def fred_list_series() -> str:
    """
    List all 30 FRED macro-economic series in the catalog, grouped by category.
    Shows the series ID, name, units, and a description of each indicator.

    Use series IDs directly at https://fred.stlouisfed.org/series/{ID} to view
    interactive charts and download data.
    """
    try:
        check_rate_limit("fred_list_series")
        audit_log("fred_list_series")
    except ValueError as e:
        return f"Error: {e}"
    by_cat: dict[str, list] = {}
    for sid, info in FRED_SERIES.items():
        by_cat.setdefault(info["category"], []).append((sid, info))

    lines = [
        f"FRED Macro Series Catalog  ({len(FRED_SERIES)} series across {len(by_cat)} categories)",
        "=" * 72,
    ]

    for cat in sorted(by_cat):
        lines.append(f"\n── {cat} ──")
        for sid, info in by_cat[cat]:
            lines.append(f"  {sid:<24}  {info['name']}")
            lines.append(f"  {'':24}  Units: {info['units']}")
            lines.append(f"  {'':24}  {info['description']}")

    lines.append(
        f"\n\nFinancial metrics analyzed from EDGAR XBRL:\n"
        + "  " + ", ".join(TARGET_METRICS)
    )

    return "\n".join(lines)


@mcp.tool()
def fred_load_analysis(file_path: str = "fred_macro_indicators.json") -> str:
    """
    Load and summarize a previously saved fred_macro_indicators.json analysis file.
    Returns the correlation results and metadata without re-running the full analysis.
    Useful for reviewing results across sessions or sharing findings.

    Args:
        file_path: Path to the saved JSON file (default "fred_macro_indicators.json")
    """
    try:
        check_rate_limit("fred_load_analysis")
        p = confine_path(file_path)
        audit_log("fred_load_analysis", file=p.name)
    except ValueError as e:
        return f"Error: {e}"
    if not p.exists():
        return (
            f"File not found: {file_path}\n\n"
            "Run fred_macro_correlations first to generate the analysis."
        )

    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as e:
        return f"Error: {file_path} is not valid JSON — {e}"
    except Exception as e:
        return f"Error reading {file_path}: {e}"

    params = data.get("parameters", {})
    corr = data.get("correlation_results", {})
    period = data.get("analysis_period", {})

    lines = [
        f"Saved Analysis: {data.get('company_name', '?')} ({data.get('ticker', '?')})",
        f"  Generated:    {data.get('generated_at', 'unknown')}",
        f"  CIK:          {data.get('cik', '?')}  |  SIC: {data.get('sic', '?')} {data.get('sic_description', '')}",
        f"  Period:       {period.get('company_financials_start', '?')} → {period.get('end', '?')}",
        f"  Threshold:    |r| ≥ {params.get('min_correlation_threshold', '?')} "
        f"| Lags tested: {params.get('lags_tested_quarters', '?')}Q",
        f"  FRED series:  {params.get('fred_series_fetched', '?')}/{params.get('fred_series_attempted', '?')} fetched",
        "",
        "── Correlated Leading Indicators ─────────────────────────────────────────",
    ]

    total_hits = 0
    for metric, hits in sorted(corr.items()):
        if not hits:
            continue
        total_hits += len(hits)
        n_q = len(data.get("company_quarterly_financials", {}).get(metric, []))
        lines.append(f"\n{metric}  ({n_q} quarters):")
        for h in hits:
            p_str = ""
            if "p_value" in h:
                sig = "✓" if h.get("significant_p05") else "~"
                p_str = f", p={h['p_value']:.4f}{sig}"
            lines.append(
                f"  [{h['optimal_lag_quarters']}Q lead]  "
                f"{h['series_id']:<22} {h['name']:<38} "
                f"r={h['pearson_r']:+.3f}{p_str}"
            )

    if total_hits == 0:
        lines.append(
            f"\n  No correlated indicators found at the stored threshold "
            f"({params.get('min_correlation_threshold', '?')})."
        )
    else:
        lines.append(f"\n  Total correlated pairs: {total_hits}")

    # Quick summary of available quarterly data
    fin_data = data.get("company_quarterly_financials", {})
    if fin_data:
        lines.append(f"\n── Company Quarterly Data Available ──────────────────────────────────────")
        for m, obs in sorted(fin_data.items()):
            if obs:
                lines.append(
                    f"  {m:<28} {len(obs)} quarters  "
                    f"({obs[0]['quarter_end']} → {obs[-1]['quarter_end']})"
                )

    macro_data = data.get("fred_macro_series", {})
    lines.append(
        f"\n── FRED Macro Data Available ─────────────────────────────────────────────\n"
        f"  {len(macro_data)} series with quarterly observations stored in {file_path}"
    )

    return "\n".join(lines)


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
