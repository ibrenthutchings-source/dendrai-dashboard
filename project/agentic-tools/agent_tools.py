#!/usr/bin/env python3
"""
Tool surface for the Dendrai investigation agent (recommendation #1).

The existing MCP layer is a fixed pipeline: run_full_analysis() always runs all
ten models in the same order. This module exposes the same underlying data
fetchers as discrete Claude tools so the model can investigate like a human
auditor — pull the financials, notice a dip, fetch the 8-Ks, compare peers —
instead of always running everything.

Each tool returns trimmed, model-friendly JSON. The deterministic analytics
(Beneish M-score, ARIMA, correlations) remain the ground truth the agent cites,
never invents.
"""

from __future__ import annotations

import logging
from typing import Any

from edgar_tool import (
    get_company_info,
    fetch_xbrl_facts,
    summarize_xbrl_annual,
    extract_risk_factors,
    fetch_sic_peers,
    parse_filings,
    fetch_filing_text,
    annotate_8k,
)
from rss_tool import run_rss_analysis
from predictive_analytics_tool import run_full_analysis

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Tool implementations — each takes the model's tool input dict, returns JSON
# ─────────────────────────────────────────────────────────────────────────────

def _financials(inp: dict) -> Any:
    ticker = (inp.get("ticker") or "").upper()
    meta, _ = get_company_info(ticker)
    xbrl = fetch_xbrl_facts(meta["cik"])
    annual = summarize_xbrl_annual(xbrl) if xbrl else {}
    return {
        "ticker": ticker,
        "company_name": meta.get("company_name"),
        "sic": meta.get("sic"),
        "sic_description": meta.get("sic_description"),
        "annual_summary": annual,
    }


def _risk_factors(inp: dict) -> Any:
    ticker = (inp.get("ticker") or "").upper()
    meta, sub = get_company_info(ticker)
    filings = parse_filings(sub, {"10-K"})["10-K"][: int(inp.get("max_filings", 1))]
    out = []
    for f in filings:
        text = fetch_filing_text(meta["cik"], f)
        risks = extract_risk_factors(text) if text else ""
        out.append({
            "filing_date": f["date"],
            "accession_number": f["accession_number"],
            # Cap per-filing text so a single tool result stays in budget.
            "risk_factors_excerpt": (risks or "")[:12_000],
        })
    return {"ticker": ticker, "filings": out}


def _eightk_events(inp: dict) -> Any:
    ticker = (inp.get("ticker") or "").upper()
    meta, sub = get_company_info(ticker)
    filings = parse_filings(sub, {"8-K"})["8-K"][: int(inp.get("limit", 15))]
    return {
        "ticker": ticker,
        "events": [annotate_8k(dict(f)) for f in filings],
    }


def _peers(inp: dict) -> Any:
    ticker = (inp.get("ticker") or "").upper()
    meta, sub = get_company_info(ticker)
    sic = meta.get("sic", "")

    # Primary: 10-K-named competitors (Claude NLP + EDGAR resolution)
    peer_source = "10-K named competitors"
    named: list = []
    peers: list = []
    try:
        import peer_intel
        named = peer_intel.extract_competitor_names(ticker, meta, sub)
        if named:
            peers = peer_intel.resolve_names_to_edgar(named, exclude_cik=meta.get("cik_plain", ""))
    except Exception as exc:
        logger.debug("peer_intel extraction failed: %s", exc)

    # Fallback: SIC peers when fewer than 3 resolved
    if len(peers) < 3:
        peer_source = "SIC peers" if not peers else "mixed (10-K + SIC)"
        sic_peers = fetch_sic_peers(sic, max_peers=int(inp.get("max_peers", 10)))
        existing_ciks = {p.get("cik_plain") for p in peers}
        for p in sic_peers:
            if p.get("cik_plain") not in existing_ciks:
                peers.append(p)
            if len(peers) >= int(inp.get("max_peers", 10)):
                break

    return {
        "ticker": ticker,
        "sic": sic,
        "sic_description": meta.get("sic_description"),
        "peer_source": peer_source,
        "named_competitors": named,
        "peers": peers[:int(inp.get("max_peers", 10))],
    }


def _rss(inp: dict) -> Any:
    import tempfile
    from pathlib import Path
    out_path = Path(tempfile.mktemp(suffix=".json"))
    try:
        result = run_rss_analysis(ticker=(inp.get("ticker") or "").upper(), output_path=out_path)
        # Trim to feed/title/severity so the agent sees signals, not full bodies.
        feeds = []
        for feed in result.get("feeds", result.get("feed_results", []))[:6]:
            arts = feed.get("articles", feed.get("signals", []))[:6]
            feeds.append({
                "feed": feed.get("feed") or feed.get("name"),
                "industry": feed.get("industry") or feed.get("category"),
                "articles": [{"title": a.get("title"), "published": a.get("published") or a.get("date")} for a in arts],
            })
        return {"ticker": result.get("ticker"), "company_name": result.get("company_name"), "feeds": feeds}
    finally:
        if out_path.exists():
            out_path.unlink(missing_ok=True)


def _full_analysis(inp: dict) -> Any:
    result = run_full_analysis(
        ticker=(inp.get("ticker") or "").upper(),
        industry=inp.get("industry", ""),
        include_rss=False,
        include_fred=bool(inp.get("include_fred", False)),
    )
    # Return the quantitative cores the agent should cite, not the whole payload.
    return {
        "ticker": result.get("ticker"),
        "financial_ratios": result.get("financial_ratios"),
        "beneish_mscore": result.get("beneish_mscore"),
        "risk_scores": result.get("risk_scores"),
        "qoq_momentum": result.get("qoq_momentum"),
        "scenario_analysis": result.get("scenario_analysis"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tool schemas (Claude tool definitions). Descriptions are prescriptive about
# WHEN to call — recent Opus models reach for tools conservatively.
# ─────────────────────────────────────────────────────────────────────────────

def _ticker_schema(extra: dict | None = None) -> dict:
    props = {"ticker": {"type": "string", "description": "US-listed ticker symbol, e.g. ON, NVDA, F."}}
    if extra:
        props.update(extra)
    return {"type": "object", "properties": props, "required": ["ticker"]}


TOOLS: list[dict] = [
    {
        "name": "get_financials",
        "description": "Fetch SEC EDGAR XBRL annual financial summary (revenue, margins, R&D, cash flow). "
                       "Call this first to ground any quantitative claim about the company.",
        "input_schema": _ticker_schema(),
    },
    {
        "name": "get_risk_factors",
        "description": "Fetch Item 1A Risk Factors text from recent 10-K filings. Call when you need the "
                       "company's own narrative risk disclosures or to detect new/changed risk language.",
        "input_schema": _ticker_schema({
            "max_filings": {"type": "integer", "description": "Number of recent 10-Ks (default 1, max 2)."},
        }),
    },
    {
        "name": "get_8k_events",
        "description": "Fetch recent 8-K material events (restatements, impairments, exec departures, cyber "
                       "incidents). Call when financials look anomalous or you need recent material events.",
        "input_schema": _ticker_schema({
            "limit": {"type": "integer", "description": "Max events to return (default 15)."},
        }),
    },
    {
        "name": "get_peers",
        "description": "Fetch SIC industry peers with gross margin / R&D intensity / revenue growth benchmarks. "
                       "Call when assessing whether a metric is normal for the industry.",
        "input_schema": _ticker_schema({
            "max_peers": {"type": "integer", "description": "Max peers (default 10)."},
        }),
    },
    {
        "name": "get_industry_news",
        "description": "Fetch graded industry RSS headlines relevant to the company's sector. Call when you "
                       "need recent external signals not yet in filings.",
        "input_schema": _ticker_schema(),
    },
    {
        "name": "run_quant_models",
        "description": "Run the deterministic analytics suite (financial ratios, Beneish M-score, templated risk "
                       "scoring, QoQ momentum, scenarios). These are ground-truth numbers — cite them, don't "
                       "recompute by hand. Call once you have the company in view and want the quant baseline.",
        "input_schema": _ticker_schema({
            "include_fred": {"type": "boolean", "description": "Also run FRED macro correlations (needs FRED_API_KEY)."},
        }),
    },
]

IMPLS = {
    "get_financials": _financials,
    "get_risk_factors": _risk_factors,
    "get_8k_events": _eightk_events,
    "get_peers": _peers,
    "get_industry_news": _rss,
    "run_quant_models": _full_analysis,
}
