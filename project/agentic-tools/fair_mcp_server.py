#!/usr/bin/env python3
"""
Risk Quantification (FAIR) MCP Server

Exposes fair_tool.py's Monte Carlo loss quantification — Threat Event
Frequency x Loss Magnitude, FAIR-style — as MCP tools, so Claude Code /
Claude Desktop can price an adjudicated control failure, a SOX process, or a
risk register entry in dollar terms instead of only the platform's ordinal
P1/P2/P3 severity or 5x5 RAG score. Same engine as POST /fair/quantify
(fair_endpoints.py) — this server calls db.py + fair_tool.py directly rather
than the REST layer, same pattern every other *_mcp_server.py in this
directory follows (see infrastructure_monitoring_mcp_server.py).

── Setup ─────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "fair-risk-quantification": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/fair_mcp_server.py"]
        }
      }
    }

── Available tools ───────────────────────────────────────────────────────────

    fair_quantify          Run a Monte Carlo quantification, optionally persisting it (write-guarded)
    fair_ale_summary       Highest-ALE resources right now
    fair_control_roi       Risk-adjusted ROI of a control (two ALE figures + annual cost)
    fair_severity_bands    The CEM-severity PERT default bands the engine falls back to

── Environment variables ─────────────────────────────────────────────────────

    DATABASE_URL          PostgreSQL connection string (required for persistence/history)
    MCP_READ_ONLY         Set to "true" to block fair_quantify(persist=True)
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
from mcp_guards import audit_log, cap_output, check_rate_limit, check_read_only
import db
import fair_tool

mcp = FastMCP("fair-risk-quantification")

_VALID_RESOURCE_TYPES = {"cem_event", "cem_event_template", "sox_process", "risk", "control"}


@mcp.tool()
def fair_severity_bands() -> str:
    """List the default (min, most-likely, max) $M PERT bands per CEM
    severity (P1/P2/P3) that fair_quantify falls back to when no SOX
    exposure, risk dollar exposure, or manual estimate is supplied."""
    try:
        check_rate_limit("fair_severity_bands")
        return cap_output(json.dumps({"bands": fair_tool.CEM_SEVERITY_BANDS}, indent=2))
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def fair_quantify(
    resource_type: str,
    resource_ref: str,
    control_id: str = None,
    process: str = None,
    company_id: int = None,
    run_id: int = None,
    window_days: int = 90,
    manual_tef: float = None,
    manual_loss_min: float = None,
    manual_loss_likely: float = None,
    manual_loss_max: float = None,
    sox_estimated_exposure: float = None,
    risk_dollar_exposure_m: float = None,
    cem_severity: str = None,
    simulations: int = 5000,
    persist: bool = True,
) -> str:
    """
    Run a FAIR Monte Carlo loss quantification and return ALE (annualized
    loss expectancy), percentiles, and a loss-exceedance curve.

    Frequency: if control_id is given and manual_tef is not, the real
    trailing-window fire count for that control (observability.
    adjudicated_tool_calls) is annualized as Threat Event Frequency.
    Otherwise manual_tef is used, defaulting to 0 events/year.

    Magnitude: first available of manual_loss_min/likely/max, then
    sox_estimated_exposure (or a company_id lookup for resource_type=
    sox_process), then risk_dollar_exposure_m, then a CEM-severity default
    band (cem_severity or "P2"). See fair_severity_bands for those bands.

    Blocked when MCP_READ_ONLY=true and persist=True (this writes a
    fair_quantifications row and, for a cem_event/cem_event_template
    resource, updates its exposure_amount_m).

    Args:
        resource_type: cem_event | cem_event_template | sox_process | risk | control
        resource_ref: id/slug of the resource within its own table (stringified)
        control_id: control whose fire history feeds TEF, if different from resource_ref
        process: PaC process id, for reference in the persisted row
        window_days: lookback window for empirical control-fire frequency (default 90)
        simulations: Monte Carlo years to simulate (500-20000, default 5000)
        persist: write the run to history + back onto the source CEM row (default True)
    """
    try:
        check_rate_limit("fair_quantify")
        if resource_type not in _VALID_RESOURCE_TYPES:
            return f"Error: resource_type must be one of {sorted(_VALID_RESOURCE_TYPES)}"
        if persist:
            check_read_only("fair_quantify")
            audit_log("fair_quantify", resource_type=resource_type, resource_ref=resource_ref)

        fire_count_window = 0
        if control_id and manual_tef is None and db.is_available():
            stats = db.get_control_fire_stats(control_id, window_days)
            fire_count_window = stats["fire_count_window"]

        sox_exposure = sox_estimated_exposure
        if resource_type == "sox_process" and sox_exposure is None and company_id and db.is_available():
            details = db.get_sox_process_details(company_id)
            sox_exposure = (details.get(resource_ref) or {}).get("estimated_exposure")

        manual_magnitude = None
        if None not in (manual_loss_min, manual_loss_likely, manual_loss_max):
            manual_magnitude = (manual_loss_min, manual_loss_likely, manual_loss_max)

        result = fair_tool.quantify(
            fire_count_window=fire_count_window, window_days=window_days, manual_tef=manual_tef,
            manual_magnitude=manual_magnitude, sox_estimated_exposure=sox_exposure,
            risk_dollar_exposure_m=risk_dollar_exposure_m, cem_severity=cem_severity,
            simulations=simulations,
        )

        quant_id = None
        if persist and db.is_available():
            quant_id = db.save_fair_quantification({
                "resource_type": resource_type, "resource_ref": resource_ref,
                "company_id": company_id, "run_id": run_id, "control_id": control_id, "process": process,
                **result, "created_by": "mcp",
            })
            try:
                if resource_type == "cem_event":
                    db.update_cem_event_exposure(int(resource_ref), result["ale"], "fair")
                elif resource_type == "cem_event_template":
                    db.update_cem_event_template_exposure(int(resource_ref), result["ale"], "fair")
            except (TypeError, ValueError):
                pass

        return cap_output(json.dumps({"id": quant_id, "resource_type": resource_type, "resource_ref": resource_ref,
                                       "fire_count_window": fire_count_window, **result}, indent=2, default=str))
    except Exception as exc:
        return f"Error quantifying: {exc}"


@mcp.tool()
def fair_ale_summary(days: int = 365) -> str:
    """List every resource with a FAIR run in the trailing window, highest
    ALE first — the fastest way to answer 'what's the most expensive open
    risk right now.'"""
    try:
        check_rate_limit("fair_ale_summary")
        if not db.is_available():
            return json.dumps({"resources": [], "total_ale": 0, "note": "Database not configured"}, indent=2)
        rows = db.get_fair_ale_summary(days)
        total_ale = round(sum(r["ale"] or 0 for r in rows), 4)
        return cap_output(json.dumps({"resources": rows, "total_ale": total_ale, "window_days": days},
                                      indent=2, default=str))
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def fair_control_roi(ale_before: float, ale_after: float, annual_control_cost: float, control_id: str = None) -> str:
    """
    Risk-adjusted ROI of a control: annualized loss it removes
    (ale_before - ale_after) versus its annual cost. Feed this two
    fair_quantify results — one for "control absent/failing" (higher TEF or
    magnitude), one for "control holding" — to size whether a proposed MAP
    remediation is worth its cost.

    Args:
        ale_before: annualized loss expectancy without the control (or with it failing)
        ale_after: annualized loss expectancy with the control holding
        annual_control_cost: fully-loaded annual cost to run the control
        control_id: optional, for reference in the response only
    """
    try:
        check_rate_limit("fair_control_roi")
        result = fair_tool.control_roi(ale_before, ale_after, annual_control_cost)
        return cap_output(json.dumps({"control_id": control_id, **result}, indent=2))
    except Exception as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
