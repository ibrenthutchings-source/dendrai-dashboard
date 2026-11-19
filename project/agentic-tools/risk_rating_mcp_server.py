#!/usr/bin/env python3
"""
Canonical Risk Rating MCP Server

Exposes risk_rating_engine.py — the ONE risk-scoring methodology now shared
by the Enterprise Risk Loop (risk-engine.js), segment/geography risks
(segment_risk_tool.py), predictive analytics (predictive_analytics_tool.py),
and operational control exceptions (exception_tool.py) — as MCP tools, so
Claude Code / Claude Desktop (or any other MCP client) can score a risk or
pull the current, already-persisted register through the exact same
thresholds and vocabulary every screen in the platform uses: 0-25 scale
(impact 0-5 x likelihood 0-5), bands R >= 15 / A >= 9 / G below, letters not
words. This server calls db.py + risk_rating_engine.py +
predictive_analytics_tool.py directly rather than the REST layer, same
pattern every other *_mcp_server.py in this directory follows (see
fair_mcp_server.py).

Before this existed, "risk scoring" on the MCP surface meant
/mcp/predictive (predictive_analytics_mcp_server.py) — which is still
mounted and still useful for its own full-analysis workflow, but computed
scores on an different scale (0-10, Red/Amber/Green) than everything else in
the platform. predictive_analytics_tool.compute_risk_scores has since been
migrated to emit the canonical scale directly (see risk_rating_engine.
score_from_raw10), so that inconsistency is closed at the source — this
server does not patch around it, it exposes the same canonical numbers.

── Setup ─────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "risk-rating": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/risk_rating_mcp_server.py"]
        }
      }
    }

── Available tools ───────────────────────────────────────────────────────────

    risk_rating_thresholds   The 0-25 RAG bands and impact-by-category table this server scores against
    risk_score_exception     Score one operational control exception (severity/process/connector tier)
    risk_score_register      Score a full industry-templated register from live ratios (predictive analytics path)
    risk_register_for_run    The already-persisted, canonical risk register for a run — "pass all risks through"

── Environment variables ─────────────────────────────────────────────────────

    DATABASE_URL            PostgreSQL connection string (required for risk_register_for_run)
    MCP_RATE_LIMIT_PER_MIN  Override per-tool rate limit (default 30)
"""

from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from mcp_guards import cap_output, check_rate_limit
import db
import risk_rating_engine as rre
from predictive_analytics_tool import compute_risk_scores

mcp = FastMCP("risk-rating")


@mcp.tool()
def risk_rating_thresholds() -> str:
    """The canonical 0-25 RAG bands (R >= 15, A >= 9, G below), the
    impact-by-category table, and the category aliases/process mappings this
    server scores against — for audit and for any caller that wants to band
    a score itself instead of calling risk_score_exception/risk_score_register."""
    try:
        check_rate_limit("risk_rating_thresholds")
        return cap_output(json.dumps({
            "scale": {"min": rre.SCORE_MIN, "max": rre.SCORE_MAX,
                      "likelihood_min": rre.LIKELIHOOD_MIN, "likelihood_max": rre.LIKELIHOOD_MAX},
            "rag_bands": {"R": f">= {rre.RAG_RED_THRESHOLD}",
                          "A": f">= {rre.RAG_AMBER_THRESHOLD}",
                          "G": f"< {rre.RAG_AMBER_THRESHOLD}"},
            "category_impact": rre.CATEGORY_IMPACT,
            "default_impact": rre.DEFAULT_IMPACT,
            "category_aliases": rre.CATEGORY_ALIASES,
            "process_category": rre.PROCESS_CATEGORY,
        }, indent=2))
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def risk_score_exception(severity: str, process: str = None, connector_risk_tier: str = None) -> str:
    """
    Score one operational control exception onto the canonical 0-25 / R-A-G
    scale — the same methodology exception_tool.py's own connector-polling
    pipeline now calls for every exception it scores.

    Args:
        severity: CRITICAL | HIGH | MEDIUM | WARN | LOW | INFO (case-insensitive)
        process: the connector's configured business process (e.g. record_to_report,
                 procure_to_pay) — drives impact via risk_rating_thresholds' process_category
                 map. Unmapped or omitted falls back to the default impact (3).
        connector_risk_tier: the connector's classified risk tier (critical/high/medium/low)
                 — a genuinely independent modifier on top of severity, not a duplicate signal.
    """
    try:
        check_rate_limit("risk_score_exception")
        result = rre.score_exception(severity, process=process, connector_risk_tier=connector_risk_tier)
        return cap_output(json.dumps({
            "severity": severity, "process": process, "connector_risk_tier": connector_risk_tier,
            **result,
        }, indent=2))
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def risk_score_register(ratios: str, industry: str = "Generic") -> str:
    """
    Score a full industry-templated risk register from live financial ratios
    — predictive_analytics_tool.compute_risk_scores, now emitting the same
    canonical 0-25 / R-A-G scale as everything else (previously 0-10 /
    Red-Amber-Green — see this server's module docstring).

    Args:
        ratios: JSON object of financial ratios (revenue_growth, gross_margin,
                fcf_margin, rd_intensity, etc. — whatever the industry's
                template rules reference; missing keys default to neutral).
        industry: one of predictive_analytics_tool.INDUSTRY_TEMPLATES' keys
                  (falls back to "Generic" if unrecognized).
    """
    try:
        check_rate_limit("risk_score_register")
        parsed_ratios = json.loads(ratios) if isinstance(ratios, str) else (ratios or {})
        result = compute_risk_scores(parsed_ratios, industry)
        return cap_output(json.dumps(result, indent=2, default=str))
    except json.JSONDecodeError as exc:
        return f"Error: ratios must be a JSON object — {exc}"
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def risk_register_for_run(run_id: int) -> str:
    """
    The already-persisted, canonical risk register for a completed run —
    wraps db.get_risk_scores_for_run. This is "pass all risks through
    whenever needed": whatever wrote risk_scores for this run (the
    Enterprise Risk Loop, a segment risk assessment, or a predictive-analytics
    run) already used the shared 0-25 / R-A-G scale, so every caller gets
    one consistent register regardless of which feature produced it.
    """
    try:
        check_rate_limit("risk_register_for_run")
        if not db.is_available():
            return json.dumps({"risks": [], "note": "Database not configured"}, indent=2)
        risks = db.get_risk_scores_for_run(run_id)
        rag_counts = {"R": 0, "A": 0, "G": 0}
        for r in risks:
            rag_counts[r.get("rag_status") if r.get("rag_status") in rag_counts else "G"] += 1
        return cap_output(json.dumps({"run_id": run_id, "risks": risks, "rag_summary": rag_counts},
                                      indent=2, default=str))
    except Exception as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
