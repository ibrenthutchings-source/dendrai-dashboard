#!/usr/bin/env python3
"""
Process Mining MCP Server

Exposes process_mining_tool.py's variant analysis, conformance checking,
cycle-time/bottleneck stats, and rework detection over case-tracked
adjudications (observability.adjudicated_tool_calls.case_id/process_step) —
so Claude Code / Claude Desktop can ask "which cases deviated from
Procure-to-Pay this month" or "where's the bottleneck in Order-to-Cash"
directly, the same computation GET /process-mining/* (process_mining_
endpoints.py) and Continuous Monitoring's Process Mining tabs use.

Read-only — this server never writes anything, so it carries no
MCP_READ_ONLY gate; process mining only ever reads adjudication history that
another producer (an ERP connector, generate_o2c_p2p_synthetic_log.py)
already wrote via the ordinary adjudication pipeline.

── Setup ─────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "process-mining": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/process_mining_mcp_server.py"]
        }
      }
    }

── Available tools ───────────────────────────────────────────────────────────

    pm_list_processes    The known process templates (id, label, canonical steps)
    pm_summary           Headline tiles: case counts, conformance/rework rate, bottleneck per process
    pm_variants          Distinct step sequences observed, most frequent first
    pm_conformance        Conformance rate + deviating cases against the matched template
    pm_cycle_times        Per-edge duration stats + overall case duration + bottleneck
    pm_rework              Cases that revisited an already-completed step
    pm_list_cases          Per-case detail (steps, duration, variant, conformance)

── Environment variables ─────────────────────────────────────────────────────

    DATABASE_URL          PostgreSQL connection string (required — no DB, no case data)
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
import process_mining_tool as pm

mcp = FastMCP("process-mining")


def _load_cases(days: int) -> dict:
    if not db.is_available():
        return {}
    events = db.get_recent_adjudications_for_domain_summary(days=days, limit=5000)
    return pm.build_cases(events)


@mcp.tool()
def pm_list_processes() -> str:
    """List the known process templates (Procure to Pay, Order to Cash,
    Receive to Ship) — id, label, and canonical step order."""
    try:
        check_rate_limit("pm_list_processes")
        out = [{"id": pid, "label": t["label"], "steps": t["steps"]} for pid, t in pm.PROCESS_TEMPLATES.items()]
        return cap_output(json.dumps({"processes": out}, indent=2))
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def pm_summary(days: int = 30) -> str:
    """Headline process-mining tiles over the trailing `days`: total cases,
    an untemplated-case count, and per-process case count / conformance
    rate / rework rate / bottleneck edge / average case duration."""
    try:
        check_rate_limit("pm_summary")
        if not db.is_available():
            return json.dumps({"total_cases": 0, "untemplated_cases": 0, "processes": {},
                                "note": "Database not configured"}, indent=2)
        events = db.get_recent_adjudications_for_domain_summary(days=days, limit=5000)
        return cap_output(json.dumps({**pm.summary(events), "window_days": days}, indent=2, default=str))
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def pm_variants(days: int = 30, process: str = None) -> str:
    """Every distinct step sequence observed for `process` (or all cases if
    omitted) over the trailing `days`, most frequent first — the happy path
    plus every deviation and how often it actually happens.

    Args:
        days: lookback window (default 30)
        process: filter to one PROCESS_TEMPLATES id (procure_to_pay, order_to_cash, receive_to_ship)
    """
    try:
        check_rate_limit("pm_variants")
        cases = _load_cases(days)
        return cap_output(json.dumps({"variants": pm.variant_analysis(cases, process), "process": process,
                                       "window_days": days}, indent=2, default=str))
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def pm_conformance(days: int = 30, process: str = None) -> str:
    """Conformance rate against the matched process template, a breakdown
    of deviation types (missing/extra/repeated step, out-of-order), and the
    specific non-conforming cases."""
    try:
        check_rate_limit("pm_conformance")
        cases = _load_cases(days)
        return cap_output(json.dumps({**pm.conformance_summary(cases, process), "window_days": days},
                                      indent=2, default=str))
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def pm_cycle_times(days: int = 30, process: str = None) -> str:
    """Per-step-transition duration stats (mean/median/p90 hours) and
    overall case duration, with the slowest transition flagged as the
    bottleneck — where time actually accumulates, as opposed to a
    directly-follows graph's edge width, which shows volume, not speed."""
    try:
        check_rate_limit("pm_cycle_times")
        cases = _load_cases(days)
        return cap_output(json.dumps({**pm.cycle_time_stats(cases, process), "window_days": days},
                                      indent=2, default=str))
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def pm_rework(days: int = 30, process: str = None) -> str:
    """Cases that revisited a step they'd already completed — often the
    fingerprint of a control catching something and sending the case
    backward rather than forward."""
    try:
        check_rate_limit("pm_rework")
        cases = _load_cases(days)
        return cap_output(json.dumps({**pm.rework_summary(cases, process), "window_days": days},
                                      indent=2, default=str))
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def pm_list_cases(days: int = 30, process: str = None, limit: int = 50) -> str:
    """Per-case detail: steps taken, matched process, variant signature,
    duration, rework flag, and conformance verdict — newest-started first.

    Args:
        days: lookback window (default 30)
        process: filter to one PROCESS_TEMPLATES id
        limit: max cases to return (default 50)
    """
    try:
        check_rate_limit("pm_list_cases")
        cases = _load_cases(days)
        out = pm.list_case_summaries(cases, process)
        return cap_output(json.dumps({"cases": out[:limit], "total": len(out), "window_days": days},
                                      indent=2, default=str))
    except Exception as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
