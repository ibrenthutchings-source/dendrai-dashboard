#!/usr/bin/env python3
"""
EDGAR MCP Server

Exposes EDGAR data as tools usable by Claude Code and Claude Desktop.

── Setup ────────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "edgar": {
          "command": "python",
          "args": ["/absolute/path/to/edgar_tool/edgar_mcp_server.py"]
        }
      }
    }

Claude Code — add to .claude/settings.json in your project:

    {
      "mcpServers": {
        "edgar": {
          "command": "python",
          "args": ["/absolute/path/to/edgar_tool/edgar_mcp_server.py"]
        }
      }
    }

── Available tools ───────────────────────────────────────────────────────────────
    edgar_company_info      CIK, SIC, entity type, address, exchanges
    edgar_financial_metrics XBRL time-series (revenue, income, assets, cash, ...)
    edgar_risk_factors      Item 1A from 10-K filings
    edgar_proxy_data        Exec comp, board, say-on-pay, shareholder proposals
    edgar_filings_index     Full list of 10-K/10-Q/8-K/DEF 14A filings (5 years)
    edgar_sic_peers         Companies sharing the target's SIC industry code
    edgar_peer_financials   Latest annual financials for SIC-matched peers
"""

import json
import os
import sys

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from mcp_guards import audit_log, check_rate_limit, sanitize_external, validate_int_range, validate_ticker
from edgar_tool import (
    annotate_8k,
    extract_proxy_sections,
    extract_risk_factors,
    fetch_filing_text,
    fetch_sic_peers,
    fetch_xbrl_facts,
    get_company_info,
    parse_filings,
    summarize_xbrl_annual,
)

mcp = FastMCP("edgar")


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def edgar_company_info(ticker: str) -> str:
    """
    Return CIK, SIC code and description, entity type, state of incorporation,
    fiscal year end, exchanges, phone, and business address for a public company.
    """
    try:
        check_rate_limit("edgar_company_info")
        ticker = validate_ticker(ticker)
        audit_log("edgar_company_info", ticker=ticker)
        meta, _ = get_company_info(ticker)
        return json.dumps(meta, indent=2)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching company info: {e}"


@mcp.tool()
def edgar_financial_metrics(ticker: str) -> str:
    """
    Return structured XBRL financial time-series for the past 5 years.
    Covers: Revenue, GrossProfit, OperatingIncome, NetIncome, EPS (basic/diluted),
    TotalAssets, CurrentAssets/Liabilities, StockholdersEquity, Cash, LongTermDebt,
    OperatingCashFlow, CapEx, Depreciation, R&D, IncomeTax, InterestExpense,
    Inventory, AccountsReceivable, SharesOutstanding, Dividends.
    Each metric includes quarterly and annual data points with period dates.
    """
    try:
        check_rate_limit("edgar_financial_metrics")
        ticker = validate_ticker(ticker)
        audit_log("edgar_financial_metrics", ticker=ticker)
        meta, _ = get_company_info(ticker)
        xbrl = fetch_xbrl_facts(meta["cik"])
        return json.dumps(xbrl, indent=2)
    except Exception as e:
        return f"Error fetching financial metrics: {e}"


@mcp.tool()
def edgar_risk_factors(ticker: str, max_filings: int = 2) -> str:
    """
    Return the Item 1A Risk Factors section from the most recent 10-K annual
    reports. max_filings sets how many years to fetch (default 2, max 5).
    """
    try:
        check_rate_limit("edgar_risk_factors", max_per_minute=10)
        ticker = validate_ticker(ticker)
        max_filings = validate_int_range(max_filings, 1, 5, "max_filings")
        audit_log("edgar_risk_factors", ticker=ticker, max_filings=max_filings)
        meta, sub = get_company_info(ticker)
        filings = parse_filings(sub, {"10-K"})["10-K"][:max_filings]
        results = []
        for f in filings:
            text = fetch_filing_text(meta["cik"], f)
            risks = extract_risk_factors(text) if text else ""
            results.append({
                "filing_date": f["date"],
                "accession_number": f["accession_number"],
                "risk_factors": sanitize_external(risks, max_len=30_000, source=f"SEC 10-K {f['date']}"),
                "word_count": len(risks.split()) if risks else 0,
            })
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error fetching risk factors: {e}"


@mcp.tool()
def edgar_proxy_data(ticker: str, max_filings: int = 2) -> str:
    """
    Return DEF 14A proxy statement data: executive compensation (CD&A),
    board of directors composition, say-on-pay advisory votes, and shareholder
    proposals. max_filings sets how many years to fetch (default 2, max 5).
    """
    try:
        check_rate_limit("edgar_proxy_data", max_per_minute=10)
        ticker = validate_ticker(ticker)
        max_filings = validate_int_range(max_filings, 1, 5, "max_filings")
        audit_log("edgar_proxy_data", ticker=ticker, max_filings=max_filings)
        meta, sub = get_company_info(ticker)
        filings = parse_filings(sub, {"DEF 14A"})["DEF 14A"][:max_filings]
        results = []
        for f in filings:
            text = fetch_filing_text(meta["cik"], f)
            sections = extract_proxy_sections(text) if text else {}
            results.append({
                "filing_date": f["date"],
                "accession_number": f["accession_number"],
                "sections": {
                    k: sanitize_external(v, max_len=15_000, source=f"SEC DEF14A {f['date']}")
                    for k, v in sections.items()
                },
            })
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error fetching proxy data: {e}"


@mcp.tool()
def edgar_filings_index(ticker: str) -> str:
    """
    Return the complete list of 10-K, 10-Q, 8-K, and DEF 14A filings from the
    past 5 years. 8-K filings include human-readable item descriptions
    (e.g. "Results of Operations", "Departure of Director").
    """
    try:
        check_rate_limit("edgar_filings_index")
        ticker = validate_ticker(ticker)
        audit_log("edgar_filings_index", ticker=ticker)
        meta, sub = get_company_info(ticker)
        filing_map = parse_filings(sub, {"10-K", "10-Q", "8-K", "DEF 14A"})
        filing_map["8-K"] = [annotate_8k(dict(f)) for f in filing_map["8-K"]]
        summary = {
            "company": meta["company_name"],
            "ticker": ticker.upper(),
            "cik": meta["cik_plain"],
            "sic": meta["sic"],
            "sic_description": meta["sic_description"],
            "counts": {ft: len(lst) for ft, lst in filing_map.items()},
            "filings": filing_map,
        }
        return json.dumps(summary, indent=2)
    except Exception as e:
        return f"Error fetching filings index: {e}"


@mcp.tool()
def edgar_sic_peers(ticker: str, max_peers: int = 20) -> str:
    """
    Return a list of public companies that share the same SIC industry code as
    the target company. Each entry includes CIK, ticker (when available),
    company name, state of incorporation, and SIC. Useful for identifying a
    peer group before pulling comparative financials.
    max_peers controls how many peers to return (default 20, max ~100).
    """
    try:
        check_rate_limit("edgar_sic_peers")
        ticker = validate_ticker(ticker)
        max_peers = validate_int_range(max_peers, 1, 100, "max_peers")
        audit_log("edgar_sic_peers", ticker=ticker, max_peers=max_peers)
        meta, _ = get_company_info(ticker)
        sic = meta.get("sic", "")
        if not sic:
            return "Error: no SIC code found for this company"
        peers = fetch_sic_peers(sic, max_peers + 1)
        ticker_up = ticker.upper()
        peers = [p for p in peers if p.get("ticker", "").upper() != ticker_up][:max_peers]
        return json.dumps({
            "target": {
                "ticker": ticker_up,
                "sic": sic,
                "sic_description": meta.get("sic_description", ""),
            },
            "peer_count": len(peers),
            "peers": peers,
        }, indent=2)
    except Exception as e:
        return f"Error fetching SIC peers: {e}"


@mcp.tool()
def edgar_peer_financials(ticker: str, max_peers: int = 10) -> str:
    """
    Return the most recent annual XBRL financial figures for SIC-peer companies
    of the target ticker. Covers the same 20+ metrics as edgar_financial_metrics
    (Revenue, NetIncome, TotalAssets, Cash, etc.) but returns only the latest
    annual value per metric — not the full time-series — so responses stay
    manageable. Peers without a known ticker are skipped.
    max_peers sets how many peers to process (default 10).
    """
    try:
        check_rate_limit("edgar_peer_financials", max_per_minute=5)
        ticker = validate_ticker(ticker)
        max_peers = validate_int_range(max_peers, 1, 20, "max_peers")
        audit_log("edgar_peer_financials", ticker=ticker, max_peers=max_peers)
        meta, _ = get_company_info(ticker)
        sic = meta.get("sic", "")
        if not sic:
            return "Error: no SIC code found for this company"

        ticker_up = ticker.upper()
        candidates = fetch_sic_peers(sic, max_peers + 10)
        peers = [
            p for p in candidates
            if p.get("ticker", "").upper() != ticker_up and p.get("ticker")
        ][:max_peers]

        results = {}
        for peer in peers:
            peer_ticker = peer["ticker"]
            try:
                xbrl = fetch_xbrl_facts(peer["cik"])
                results[peer_ticker] = {
                    "company_name": peer["company_name"],
                    "cik": peer["cik_plain"],
                    "state": peer.get("state", ""),
                    "financials": summarize_xbrl_annual(xbrl),
                }
            except Exception:
                results[peer_ticker] = {
                    "company_name": peer["company_name"],
                    "cik": peer["cik_plain"],
                    "error": "failed to fetch financials",
                }

        return json.dumps({
            "target_ticker": ticker_up,
            "sic": sic,
            "sic_description": meta.get("sic_description", ""),
            "peer_count": len(results),
            "peers": results,
        }, indent=2)
    except Exception as e:
        return f"Error fetching peer financials: {e}"


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()