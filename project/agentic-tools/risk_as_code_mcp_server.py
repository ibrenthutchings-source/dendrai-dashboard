#!/usr/bin/env python3
"""
Risk-as-Code MCP Server

Converts risk lists from three sources into OSCAL (NIST SP 800-53) and
COSO ERM 2017 / ISO 31000:2018 YAML artifacts.

Sources
───────
  • Loop output  — JSON array from output.s2.risks (frontend pipeline)
  • PostgreSQL   — risk_scores table, fetched by ticker / run_id
  • Excel upload — .xlsx / .xls / .csv file path (written by HTTP bridge)

── Setup ────────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "risk-as-code": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/risk_as_code_mcp_server.py"]
        }
      }
    }

Claude Code — add to .claude/settings.json:

    {
      "mcpServers": {
        "risk-as-code": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/risk_as_code_mcp_server.py"]
        }
      }
    }

── Available tools ───────────────────────────────────────────────────────────────
    rac_from_loop_output   Convert loop JSON risks → OSCAL / COSO ERM YAML
    rac_from_database      Fetch risks from PostgreSQL → OSCAL / COSO ERM YAML
    rac_from_excel         Parse Excel/CSV risk register → OSCAL / COSO ERM YAML
    rac_from_review        Convert a Risk Register Review session → YAML
    rac_persist_artifacts  Explicitly persist YAML artifacts to DB (write-gated)
    rac_validate           Validate structural integrity of a Risk-as-Code YAML
    rac_list_runs          List DB runs with existing artifacts for a ticker
    rac_list_reviews       List Risk Register Review sessions saved from the UI

── Excel column names (case-insensitive, flexible) ──────────────────────────────
    ID / Risk ID / Ref
    Name / Risk Name / Title
    Category / Type
    Score / Risk Score / Total Score
    Base Score / Inherent Score
    RAG / Status / Rating / Colour / Color
    Velocity / Trend
    CE / Control Effectiveness
    Peer / Benchmark / Peer Benchmark
    Narrative / Description / Notes / Details
    Impact / Impact Score
    Likelihood / Probability
"""

import json
import os
import sys
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from mcp_guards import (
    audit_log, cap_output, check_rate_limit, check_read_only,
    confine_path, validate_ticker, validate_enum, yaml_escape,
)

from risks_as_code import to_oscal, to_coso_erm
import db
import embedding_util

mcp = FastMCP("risk-as-code")


# ─────────────────────────────────────────────────────────────────────────────
# Excel column normalizer
# ─────────────────────────────────────────────────────────────────────────────

_COLUMN_ALIASES: dict[str, list[str]] = {
    "id":        ["id", "risk id", "risk_id", "ref", "risk ref", "risk_ref", "risk no", "no"],
    "name":      ["name", "risk name", "risk_name", "title", "risk title", "risk description"],
    "category":  ["category", "cat", "risk category", "type", "risk type", "domain"],
    "score":     ["score", "risk score", "risk_score", "total score", "final score", "residual score"],
    "base":      ["base", "base score", "base_score", "inherent score", "inherent_score", "inherent"],
    "rag":       ["rag", "status", "rating", "rag status", "colour", "color", "traffic light"],
    "velocity":  ["velocity", "trend", "vel", "direction", "score trend", "score_trend"],
    "ce":        ["ce", "control effectiveness", "control_effectiveness", "controls", "ce rating"],
    "peer":      ["peer", "peer benchmark", "benchmark", "peer_benchmark", "vs peers"],
    "narrative": ["narrative", "description", "notes", "details", "risk narrative", "risk detail"],
    "impact":    ["impact", "impact score", "impact_score", "consequence"],
    "likelihood": ["likelihood", "probability", "prob", "like", "likelihood_score"],
}


def _normalize_excel_cols(df) -> dict[str, str]:
    """Map actual DataFrame column names → canonical field names."""
    lower_cols = {c.lower().strip(): c for c in df.columns}
    mapping: dict[str, str] = {}
    for field, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_cols:
                mapping[field] = lower_cols[alias]
                break
    return mapping


def _parse_rag(val) -> str:
    if val is None:
        return "A"
    v = str(val).strip().upper()
    if v in ("R", "RED", "HIGH"):
        return "R"
    if v in ("G", "GREEN", "LOW"):
        return "G"
    return "A"


def _parse_velocity(val) -> int:
    try:
        return max(-1, min(3, int(float(str(val)))))
    except Exception:
        return 0


def _parse_ce(val) -> str:
    if val is None:
        return "ADEQUATE"
    v = str(val).strip().upper()
    if "STRONG" in v or v == "S":
        return "STRONG"
    if "WEAK" in v or v == "W":
        return "WEAK"
    return "ADEQUATE"


def _is_blank(val) -> bool:
    if val is None:
        return True
    s = str(val).strip().lower()
    return s in ("", "nan", "none", "n/a", "na", "-")


def _excel_row_to_risk(row: dict, col_map: dict[str, str], idx: int) -> dict:
    def get(field, default=None):
        col = col_map.get(field)
        if col is None:
            return default
        v = row.get(col, default)
        return default if _is_blank(v) else v

    raw_score = get("score", 0)
    score = round(float(raw_score or 0), 2)
    rag   = _parse_rag(get("rag") or ("R" if score >= 15 else "A" if score >= 9 else "G"))

    return {
        "id":        str(get("id") or f"R-{str(idx + 1).zfill(2)}").strip(),
        "name":      str(get("name") or f"Risk {idx + 1}").strip(),
        "category":  str(get("category") or "General").strip(),
        "score":     score,
        "base":      round(float(get("base") or score), 2),
        "rag":       rag,
        "velocity":  _parse_velocity(get("velocity") or 0),
        "ce":        _parse_ce(get("ce")),
        "peer":      str(get("peer") or "in-line").strip().lower(),
        "narrative": str(get("narrative") or "").strip(),
        "impact":    round(float(get("impact") or 0), 2),
        "likelihood": round(float(get("likelihood") or 0), 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Shared output builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_artifacts(
    framework: str,
    ticker: str,
    risks: list,
    period: str,
    industry: str,
    ratios: dict,
    objectives: list,
    maps: list,
    signals: list,
    run_id: Optional[int],
) -> dict:
    """Return dict of {framework: yaml_str} based on requested framework."""
    result: dict[str, str] = {}
    kw = dict(
        ticker=ticker, risks=risks, objectives=objectives,
        maps=maps, ratios=ratios, signals=signals,
        industry=industry, period=period, run_id=run_id,
    )
    if framework in ("oscal", "both"):
        result["oscal"] = to_oscal(**kw)
    if framework in ("coso_erm", "both"):
        result["coso_erm"] = to_coso_erm(**kw)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# MCP Tools
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def rac_from_loop_output(
    risks_json: str,
    ticker: str,
    period: str = "",
    framework: str = "both",
    industry: str = "",
    ratios_json: str = "",
    objectives_json: str = "",
    maps_json: str = "",
    signals_json: str = "",
    run_id: Optional[int] = None,
) -> str:
    """Convert a JSON risk array from the Dendrai risk loop into Risk-as-Code YAML.

    To persist the generated artifacts to the database, call rac_persist_artifacts
    after this tool returns the YAML content.

    Args:
        risks_json:      JSON string — array of risk objects from output.s2.risks
        ticker:          Company ticker symbol, e.g. "ON"
        period:          Audit period label, e.g. "Q4 2025"
        framework:       Output format: "oscal" | "coso_erm" | "both"
        industry:        Industry label, e.g. "Semiconductors"
        ratios_json:     Optional JSON object of financial ratios (m_score, revenue_growth_pct, …)
        objectives_json: Optional JSON array of audit objectives (output.s3.objectives)
        maps_json:       Optional JSON array of MAPs (output.s4.maps)
        signals_json:    Optional JSON array of signals (output.s1.signals)
        run_id:          Optional DB run_id to attach to the output metadata

    Returns:
        JSON: {"oscal": "...", "coso_erm": "...", "summary": {...}}
    """
    try:
        check_rate_limit("rac_from_loop_output", max_per_minute=20)
        audit_log("rac_from_loop_output", ticker=ticker, framework=framework, run_id=run_id)
        ticker = validate_ticker(ticker)
        framework = validate_enum(framework, {"oscal", "coso_erm", "both"}, "framework", default="both")
    except ValueError as e:
        return json.dumps({"error": str(e)})

    try:
        risks = json.loads(risks_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid risks_json — {e}"})

    if not isinstance(risks, list) or not risks:
        return json.dumps({"error": "risks_json must be a non-empty JSON array"})

    ratios     = json.loads(ratios_json)     if ratios_json     else {}
    objectives = json.loads(objectives_json) if objectives_json else []
    maps       = json.loads(maps_json)       if maps_json       else []
    signals    = json.loads(signals_json)    if signals_json    else []

    artifacts = _build_artifacts(
        framework, ticker, risks, period, industry, ratios,
        objectives, maps, signals, run_id,
    )

    artifacts["summary"] = {
        "ticker":     ticker,
        "period":     period,
        "industry":   industry,
        "risk_count": len(risks),
        "red":        sum(1 for r in risks if r.get("rag") == "R"),
        "amber":      sum(1 for r in risks if r.get("rag") == "A"),
        "green":      sum(1 for r in risks if r.get("rag") == "G"),
        "source":     "loop_output",
        "framework":  framework,
        "run_id":     run_id,
        "note":       "Call rac_persist_artifacts to save YAML to database.",
    }
    return cap_output(json.dumps(artifacts, default=str))


@mcp.tool()
def rac_from_database(
    ticker: str,
    run_id: Optional[int] = None,
    framework: str = "both",
) -> str:
    """Fetch risks from the PostgreSQL risk_scores table and convert to Risk-as-Code YAML.

    If run_id is omitted the most recent completed run for the ticker is used.

    Args:
        ticker:    Company ticker symbol
        run_id:    Specific run_id; omit to use the most recent run
        framework: Output format: "oscal" | "coso_erm" | "both"

    Returns:
        JSON: {"oscal": "...", "coso_erm": "...", "summary": {...}, "run_id": N}
    """
    try:
        check_rate_limit("rac_from_database")
        audit_log("rac_from_database", ticker=ticker, run_id=run_id)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    if not db.is_available():
        return json.dumps({"error": "Database not configured — set DATABASE_URL environment variable"})

    ticker_u = ticker.upper()

    # Resolve run_id
    if run_id is None:
        history = db.get_run_history(ticker_u, limit=5)
        completed = [r for r in history if r.get("completed")]
        if not completed:
            # Fall back to the most recent run, completed or not
            if not history:
                return json.dumps({"error": f"No runs found for ticker {ticker_u}"})
            run_id = history[0]["run_id"]
        else:
            run_id = completed[0]["run_id"]

    run = db.get_run_detail(run_id)
    if not run:
        return json.dumps({"error": f"Run {run_id} not found in database"})

    # get_run_detail returns slim risk_scores; rebuild from full schema
    raw_scores = run.get("risk_scores", [])
    if not raw_scores:
        return json.dumps({"error": f"No risk scores persisted for run {run_id}"})

    risks = [
        {
            "id":        r.get("risk_ref", ""),
            "name":      r.get("risk_name", ""),
            "category":  r.get("category", "Operational"),
            "score":     float(r.get("score") or 0),
            "base":      float(r.get("base_score") or r.get("score") or 0),
            "rag":       r.get("rag_status") or r.get("rag", "A"),
            "velocity":  int(r.get("velocity") or 0),
            "ce":        r.get("control_env") or r.get("ce", "ADEQUATE"),
            "peer":      r.get("peer_benchmark", "in-line"),
            "narrative": r.get("risk_name", ""),
        }
        for r in raw_scores
    ]

    bm = run.get("beneish_mscore") or {}
    ratios = {
        "m_score":             bm.get("m_score"),
        "m_score_interpretation": bm.get("interpretation"),
    }

    period_val   = run.get("period_end") or ""
    industry_val = run.get("industry") or ""

    artifacts = _build_artifacts(
        framework, ticker_u, risks, period_val, industry_val, ratios, [], [], [], run_id,
    )

    artifacts["summary"] = {
        "ticker":     ticker_u,
        "run_id":     run_id,
        "period":     period_val,
        "industry":   industry_val,
        "risk_count": len(risks),
        "red":        sum(1 for r in risks if r.get("rag") == "R"),
        "amber":      sum(1 for r in risks if r.get("rag") == "A"),
        "green":      sum(1 for r in risks if r.get("rag") == "G"),
        "source":     "database",
        "framework":  framework,
    }
    return cap_output(json.dumps(artifacts, default=str))


@mcp.tool()
def rac_from_excel(
    file_path: str,
    ticker: str,
    period: str = "",
    industry: str = "",
    framework: str = "both",
    sheet_name: str = "0",
) -> str:
    """Parse a risk register Excel or CSV file and convert to Risk-as-Code YAML.

    The file should contain columns with names matching (case-insensitive):
      ID / Risk ID · Name / Risk Name · Category · Score · Base Score ·
      RAG / Status · Velocity / Trend · CE / Control Effectiveness ·
      Peer / Benchmark · Narrative / Description · Impact · Likelihood

    When called via the HTTP bridge the frontend uploads the file first;
    the bridge saves it to a temp path and passes that path here.

    To persist the generated artifacts to the database, call rac_persist_artifacts
    with the returned YAML content.

    Args:
        file_path:  Absolute path to .xlsx, .xls, or .csv file on the server
        ticker:     Company ticker symbol
        period:     Audit period label, e.g. "Q4 2025"
        industry:   Industry label, e.g. "Semiconductors"
        framework:  Output format: "oscal" | "coso_erm" | "both"
        sheet_name: Sheet name or zero-based index (default "0" = first sheet)

    Returns:
        JSON: {"oscal": "...", "coso_erm": "...", "summary": {...},
               "rows_parsed": N, "warnings": [...]}
    """
    try:
        check_rate_limit("rac_from_excel", max_per_minute=10)
        audit_log("rac_from_excel", ticker=ticker, file_path=file_path, framework=framework)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    try:
        import pandas as pd
    except ImportError:
        return json.dumps({"error": "pandas is not installed — run: pip install pandas openpyxl"})

    from pathlib import Path

    try:
        fp = confine_path(file_path)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    if not fp.exists():
        return json.dumps({"error": f"File not found: {file_path}"})
    if fp.suffix.lower() not in (".xlsx", ".xls", ".csv"):
        return json.dumps({"error": f"Unsupported file type '{fp.suffix}' — use .xlsx, .xls, or .csv"})

    warnings: list[str] = []

    # Load the spreadsheet
    try:
        if fp.suffix.lower() == ".csv":
            df = pd.read_csv(fp)
        else:
            sheet: int | str = int(sheet_name) if sheet_name.isdigit() else sheet_name
            df = pd.read_excel(fp, sheet_name=sheet, engine="openpyxl")
    except Exception as e:
        return json.dumps({"error": f"Could not read file: {e}"})

    if df.empty:
        return json.dumps({"error": "Spreadsheet is empty"})

    col_map = _normalize_excel_cols(df)

    if "name" not in col_map:
        warnings.append("No 'Name' / 'Risk Name' column detected — rows will use generic labels (R-01, R-02, …)")
    if "score" not in col_map:
        warnings.append("No 'Score' column detected — all risk scores default to 0")
    if "rag" not in col_map:
        warnings.append("No 'RAG' / 'Status' column detected — RAG will be derived from score (R≥15 / A≥9 / G<9)")

    # Parse each row into a risk dict
    risks: list[dict] = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        try:
            risk = _excel_row_to_risk(row_dict, col_map, len(risks))
            if _is_blank(risk["name"]):
                continue
            risks.append(risk)
        except Exception as e:
            warnings.append(f"Row {idx + 2} skipped — {e}")

    if not risks:
        return json.dumps({"error": "No valid risk rows found in file", "warnings": warnings})

    ticker_u = ticker.upper()
    artifacts = _build_artifacts(
        framework, ticker_u, risks, period, industry, {}, [], [], [], None,
    )

    artifacts["rows_parsed"] = len(risks)
    artifacts["warnings"]    = warnings
    artifacts["summary"] = {
        "ticker":         ticker_u,
        "period":         period,
        "industry":       industry,
        "risk_count":     len(risks),
        "red":            sum(1 for r in risks if r.get("rag") == "R"),
        "amber":          sum(1 for r in risks if r.get("rag") == "A"),
        "green":          sum(1 for r in risks if r.get("rag") == "G"),
        "source":         "excel",
        "file":           fp.name,
        "framework":      framework,
        "columns_mapped": list(col_map.keys()),
        "note":           "Call rac_persist_artifacts to save YAML to database.",
    }
    return cap_output(json.dumps(artifacts, default=str))


@mcp.tool()
def rac_validate(yaml_content: str, framework: str = "oscal") -> str:
    """Validate the structural integrity of a Risk-as-Code YAML document.

    Checks required top-level keys, UUID presence, risk vs. findings alignment,
    and ISO / COSO required fields.

    Args:
        yaml_content: Raw YAML string to validate
        framework:    "oscal" | "coso_erm"

    Returns:
        JSON: {"valid": bool, "errors": [...], "warnings": [...], "framework": "..."}
    """
    try:
        check_rate_limit("rac_validate")
        audit_log("rac_validate", framework=framework)
        framework = validate_enum(framework, {"oscal", "coso_erm"}, "framework", default=None)
    except ValueError as e:
        return json.dumps({"valid": False, "errors": [str(e)], "warnings": [], "framework": framework})

    try:
        import yaml as _yaml
        doc = _yaml.safe_load(yaml_content)
    except Exception as e:
        return json.dumps({"valid": False, "errors": [f"YAML parse error: {e}"], "warnings": [], "framework": framework})

    errors: list[str]   = []
    warnings: list[str] = []

    if framework == "oscal":
        if not isinstance(doc, dict) or "assessment-results" not in doc:
            errors.append("Missing top-level 'assessment-results' key")
        else:
            ar = doc["assessment-results"]
            if "uuid" not in ar:
                errors.append("assessment-results.uuid is missing")
            if "metadata" not in ar:
                errors.append("assessment-results.metadata is missing")
            results = ar.get("results", [])
            if not results:
                errors.append("assessment-results.results[] is empty or missing")
            else:
                r0       = results[0]
                n_risks  = len(r0.get("risks", []))
                n_find   = len(r0.get("findings", []))
                if n_risks == 0:
                    errors.append("No risks found in results[0].risks[]")
                if n_risks != n_find:
                    warnings.append(
                        f"risks count ({n_risks}) does not match findings count ({n_find})"
                    )
                for risk in r0.get("risks", []):
                    if "uuid" not in risk:
                        warnings.append(f"Risk '{risk.get('title', '?')}' is missing a uuid")

    elif framework == "coso_erm":
        for key in ("framework", "entity", "risk_universe"):
            if key not in doc:
                errors.append(f"Missing required key: '{key}'")
        if "risk_universe" in doc:
            universe = doc["risk_universe"]
            if not universe:
                errors.append("risk_universe[] is empty")
            else:
                for entry in universe:
                    if "risk_id" not in entry:
                        warnings.append(f"Entry '{entry.get('name', '?')}' missing 'risk_id'")
                    if "coso_component" not in entry:
                        warnings.append(f"Entry '{entry.get('risk_id', '?')}' missing 'coso_component'")
    else:
        errors.append(f"Unknown framework '{framework}' — use 'oscal' or 'coso_erm'")

    return json.dumps({
        "valid":     len(errors) == 0,
        "errors":    errors,
        "warnings":  warnings,
        "framework": framework,
    })


@mcp.tool()
def rac_list_runs(ticker: str, limit: int = 10) -> str:
    """List recent risk loop runs for a ticker, showing which have Risk-as-Code artifacts.

    Args:
        ticker: Company ticker symbol
        limit:  Maximum number of runs to return (default 10)

    Returns:
        JSON: {"ticker": "...", "runs": [{run_id, run_at, industry, risk_count,
               has_artifacts, frameworks, completed}, ...]}
    """
    try:
        check_rate_limit("rac_list_runs")
        audit_log("rac_list_runs", ticker=ticker)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    if not db.is_available():
        return json.dumps({"error": "Database not configured — set DATABASE_URL environment variable"})

    ticker_u = ticker.upper()
    history  = db.get_run_history(ticker_u, limit=limit)
    if not history:
        return json.dumps({"ticker": ticker_u, "runs": [], "note": f"No runs found for {ticker_u}"})

    # Existing artifacts for this ticker (all runs)
    all_artifacts = db.get_latest_risks_as_code_artifacts(ticker_u)
    artifact_run_ids: set[int] = {a.get("run_id") for a in all_artifacts if a.get("run_id")}
    framework_by_run: dict[int, list[str]] = {}
    for a in all_artifacts:
        rid = a.get("run_id")
        if rid:
            framework_by_run.setdefault(rid, []).append(a.get("framework", ""))

    runs = [
        {
            "run_id":       r["run_id"],
            "run_at":       r.get("run_at", ""),
            "company_name": r.get("company_name", ""),
            "industry":     r.get("industry", ""),
            "risk_count":   r.get("risk_count", 0),
            "completed":    r.get("completed", False),
            "has_artifacts": r["run_id"] in artifact_run_ids,
            "frameworks":   framework_by_run.get(r["run_id"], []),
        }
        for r in history
    ]
    return json.dumps({"ticker": ticker_u, "runs": runs})


# ─────────────────────────────────────────────────────────────────────────────
# Review-screen tools
# ─────────────────────────────────────────────────────────────────────────────

_CTRL_MAP_LOCAL = {c["ref"]: c for c in [
    {"ref": "FC-01", "name": "Revenue Recognition Controls"},
    {"ref": "FC-02", "name": "Financial Close Reconciliation"},
    {"ref": "FC-03", "name": "Segregation of Financial Duties"},
    {"ref": "FC-04", "name": "Fraud Risk Assessment"},
    {"ref": "AC-01", "name": "Access Control Policy"},
    {"ref": "AC-02", "name": "Account Management"},
    {"ref": "AC-03", "name": "Access Enforcement"},
    {"ref": "AC-04", "name": "Privileged Access Management"},
    {"ref": "AC-05", "name": "Logical Access Review"},
    {"ref": "SC-01", "name": "Information Security Policy"},
    {"ref": "SC-02", "name": "Data Protection & Encryption"},
    {"ref": "SC-03", "name": "Incident Response Plan"},
    {"ref": "SC-04", "name": "Vulnerability Management"},
    {"ref": "SC-05", "name": "Change Management Controls"},
    {"ref": "RM-01", "name": "Risk Assessment Process"},
    {"ref": "RM-02", "name": "Risk Treatment Plan"},
    {"ref": "RM-03", "name": "Risk Appetite Framework"},
    {"ref": "RM-04", "name": "Emerging Risk Monitoring"},
    {"ref": "OP-01", "name": "Business Continuity Plan"},
    {"ref": "OP-02", "name": "Supplier Risk Management"},
    {"ref": "OP-03", "name": "Key Person Dependencies"},
    {"ref": "CM-01", "name": "Compliance Monitoring Program"},
    {"ref": "CM-02", "name": "Regulatory Change Management"},
    {"ref": "CM-03", "name": "Privacy Controls"},
    {"ref": "VM-01", "name": "Vendor Security Assessment"},
    {"ref": "VM-02", "name": "Supply Chain Resilience"},
    {"ref": "HR-01", "name": "Security Awareness Training"},
    {"ref": "HR-02", "name": "Background Screening"},
]}


@mcp.tool()
def rac_list_reviews(run_id: Optional[int] = None, limit: int = 10) -> str:
    """List Risk Register Review sessions saved from the Dendrai Review screen.

    Each session captures a curator's decisions: which risks were included or
    excluded, wording changes, and controls assigned. Use the returned `id`
    with rac_from_review to generate Risk-as-Code YAML from a session.

    Args:
        run_id: Filter to reviews linked to a specific pipeline run (optional)
        limit:  Maximum number of sessions to return (default 10)

    Returns:
        JSON: {"reviews": [{id, run_id, review_type, framework, status,
               created_at, completed_at}, ...], "count": N}
    """
    try:
        check_rate_limit("rac_list_reviews")
        audit_log("rac_list_reviews", run_id=run_id)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    if not db.is_available():
        return json.dumps({"error": "Database not configured — set DATABASE_URL environment variable"})

    reviews = db.list_risk_register_reviews(run_id=run_id, limit=limit)
    return json.dumps({"reviews": reviews, "count": len(reviews)}, default=str)


@mcp.tool()
def rac_from_review(
    review_id: int,
    ticker: str = "",
    period: str = "",
    industry: str = "",
    framework: str = "review",
) -> str:
    """Generate Risk-as-Code YAML from a completed Risk Register Review session.

    Reads the curated risk states saved by the Review screen — wording edits,
    include/exclude decisions, and control assignments — and converts them to YAML.

    To persist the generated artifacts to the database, call rac_persist_artifacts
    with the returned YAML content.

    Args:
        review_id: ID of the review session (use rac_list_reviews to find it)
        ticker:    Company ticker symbol; required when framework is oscal/coso_erm/both/all
        period:    Audit period label, e.g. "Q4 2025" (optional)
        industry:  Industry label, e.g. "Technology" (optional)
        framework: Output format:
                     "review"   – flat YAML matching the Review screen output (default)
                     "oscal"    – NIST OSCAL assessment-results document
                     "coso_erm" – COSO ERM 2017 / ISO 31000:2018 YAML
                     "both"     – oscal + coso_erm
                     "all"      – review + oscal + coso_erm

    Returns:
        JSON: {"review": "...", "oscal": "...", "coso_erm": "...", "summary": {...}}
        Keys present depend on the requested framework.
    """
    import datetime

    try:
        check_rate_limit("rac_from_review", max_per_minute=20)
        audit_log("rac_from_review", review_id=review_id, ticker=ticker, framework=framework)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    if not db.is_available():
        return json.dumps({"error": "Database not configured — set DATABASE_URL environment variable"})

    # 1. Review metadata
    review = db.get_risk_register_review(review_id)
    if not review:
        return json.dumps({"error": f"Review session {review_id} not found — use rac_list_reviews to see available sessions"})

    # 2. Risk states saved by the Review screen
    states = db.get_review_risk_states(review_id)
    if not states:
        return json.dumps({
            "error": f"No risk states for review {review_id}. The review may be empty or was created without saving states.",
            "review": review,
        })

    # 3. Enrich with pipeline risk_scores when the review is linked to a run
    scores_by_ref: dict = {}
    if review.get("run_id"):
        raw_scores = db.get_risk_scores_for_run(review["run_id"])
        scores_by_ref = {r["risk_ref"]: r for r in raw_scores}

    today = datetime.date.today().isoformat()
    fw_label = review.get("framework") or "Internal Risk Register"
    review_type = review.get("review_type") or "internal"

    included = [s for s in states if s.get("included", True)]
    excluded = [s for s in states if not s.get("included", True)]

    def _ctrl_name(ref: str) -> str:
        return _CTRL_MAP_LOCAL.get(ref, {}).get("name", "")

    # ── "review" flat YAML format ────────────────────────────────────────────

    def _build_review_yaml() -> str:
        lines = [
            "# Risk Register Review — Risk-as-Code Output",
            f"# Generated: {today}  ·  review_id: {review_id}  ·  status: {review.get('status', 'draft')}",
            f"# Source: {fw_label}  ·  type: {review_type}",
            f"# Risks included: {len(included)}  ·  excluded: {len(excluded)}",
            "",
            "thresholds:",
            "  red:   15.0",
            "  amber:  9.0",
            "",
            "scoring:",
            "  scale: 25    # impact (0-5) × likelihood (0-5)",
            "",
        ]

        if included:
            lines.append("risks:")
            for s in included:
                ref = s["risk_ref"]
                base = scores_by_ref.get(ref, {})
                wording = yaml_escape(s.get("current_wording") or s.get("original_wording") or "")
                category = yaml_escape(base.get("category") or "—")
                score = base.get("score")
                rag = yaml_escape(base.get("rag") or base.get("rag_status") or "—")
                source_fw = yaml_escape(base.get("source_framework") or fw_label)
                reason = yaml_escape(s.get("reason_for_change") or "")
                controls = s.get("controls_assigned") or []

                lines.append(f"  - id:               {ref}")
                lines.append(f'    name:             "{wording}"')
                lines.append(f'    category:         "{category}"')
                lines.append(f'    source_framework: "{source_fw}"')
                lines.append(f'    rag:              "{rag}"')
                if score is not None:
                    lines.append(f"    score:            {float(score):.1f}   # out of 25")
                if reason:
                    lines.append(f'    change_reason:    "{reason}"')
                if controls:
                    lines.append("    controls:")
                    for ctrl in controls:
                        ctrl_ref = ctrl.get("ref", "") if isinstance(ctrl, dict) else str(ctrl)
                        cname = _ctrl_name(ctrl_ref)
                        lines.append(f"      - ref: {ctrl_ref}")
                        if cname:
                            lines.append(f'        name: "{yaml_escape(cname)}"')
                        if isinstance(ctrl, dict) and ctrl.get("generate_code"):
                            lines.append("        generate_control_as_code: true")
                lines.append("")

        if excluded:
            lines.append("excluded_risks:")
            for s in excluded:
                ref = s["risk_ref"]
                wording = yaml_escape(s.get("current_wording") or s.get("original_wording") or "")
                reason = yaml_escape(s.get("reason_for_change") or "No reason provided")
                lines.append(f"  - id:     {ref}")
                lines.append(f'    name:   "{wording}"')
                lines.append(f'    reason: "{reason}"')
                lines.append("")

        return "\n".join(lines).rstrip()

    # ── Assemble result ──────────────────────────────────────────────────────

    result: dict = {}

    if framework in ("review", "all"):
        result["review"] = _build_review_yaml()

    if framework in ("oscal", "coso_erm", "both", "all"):
        if not ticker:
            result["warning"] = (
                "ticker is required for oscal / coso_erm output. "
                "Pass ticker=<SYMBOL> and re-run, or use framework='review' for the flat format."
            )
        else:
            risks_for_fw = [
                {
                    "id":        s["risk_ref"],
                    "name":      s.get("current_wording") or s.get("original_wording") or "",
                    "category":  scores_by_ref.get(s["risk_ref"], {}).get("category") or "General",
                    "score":     float(scores_by_ref.get(s["risk_ref"], {}).get("score") or 0),
                    "base":      float(scores_by_ref.get(s["risk_ref"], {}).get("score") or 0),
                    "rag":       scores_by_ref.get(s["risk_ref"], {}).get("rag") or
                                 scores_by_ref.get(s["risk_ref"], {}).get("rag_status") or "A",
                    "velocity":  int(scores_by_ref.get(s["risk_ref"], {}).get("velocity") or 0),
                    "ce":        scores_by_ref.get(s["risk_ref"], {}).get("control_env") or "ADEQUATE",
                    "peer":      scores_by_ref.get(s["risk_ref"], {}).get("peer_benchmark") or "in-line",
                    "narrative": s.get("current_wording") or "",
                    "controls":  s.get("controls_assigned") or [],
                }
                for s in included
            ]
            fw_param = "both" if framework == "all" else framework
            artifacts = _build_artifacts(
                fw_param, ticker.upper(), risks_for_fw,
                period, industry, {}, [], [], [],
                review.get("run_id"),
            )
            result.update(artifacts)

    changed = [
        s for s in states
        if s.get("current_wording") and s.get("original_wording")
        and s["current_wording"] != s["original_wording"]
    ]
    controls_total = sum(len(s.get("controls_assigned") or []) for s in states)

    result["summary"] = {
        "review_id":        review_id,
        "run_id":           review.get("run_id"),
        "review_type":      review_type,
        "framework_label":  fw_label,
        "status":           review.get("status"),
        "created_at":       review.get("created_at"),
        "completed_at":     review.get("completed_at"),
        "ticker":           ticker.upper() if ticker else None,
        "period":           period,
        "industry":         industry,
        "risks_total":      len(states),
        "risks_included":   len(included),
        "risks_excluded":   len(excluded),
        "wording_changed":  len(changed),
        "controls_assigned": controls_total,
        "output_framework": framework,
        "note":             "Call rac_persist_artifacts to save YAML to database.",
    }

    return cap_output(json.dumps(result, default=str))


@mcp.tool()
def rac_persist_artifacts(
    run_id: int,
    ticker: str,
    framework: str,
    yaml_content: str,
) -> str:
    """Persist a Risk-as-Code YAML artifact to the database.

    This is the ONLY tool that writes artifacts to the database.
    It is blocked when MCP_READ_ONLY=true is set in .env.

    Call this after rac_from_loop_output, rac_from_excel, or rac_from_review
    to explicitly save the generated YAML to risks_as_code_artifacts.

    Args:
        run_id:       Pipeline run_id to link this artifact to
        ticker:       Company ticker symbol (e.g. "AAPL")
        framework:    "oscal" | "coso_erm"
        yaml_content: The YAML string returned by one of the rac_from_* tools

    Returns:
        JSON: {"saved": true, "run_id": N, "ticker": "...", "framework": "..."}
    """
    try:
        check_rate_limit("rac_persist_artifacts", max_per_minute=10)
        check_read_only("DB artifact save")
        ticker = validate_ticker(ticker)
        framework = validate_enum(framework, {"oscal", "coso_erm"}, "framework", default=None)
        audit_log("rac_persist_artifacts", run_id=run_id, ticker=ticker, framework=framework)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    if not db.is_available():
        return json.dumps({"error": "Database not configured — set DATABASE_URL environment variable"})

    if not yaml_content or not yaml_content.strip():
        return json.dumps({"error": "yaml_content is empty"})

    try:
        artifact_id = db.save_risks_as_code_artifact(run_id, ticker, framework, yaml_content)
        if artifact_id:
            try:
                company_id = db.get_company_id(ticker)
                vec = embedding_util.embed_text(yaml_content[:8000])
                if vec:
                    db.save_embedding(
                        source_table="risks_as_code_artifacts", source_id=artifact_id,
                        content_type=db.EMBT_RAC, embedding=vec,
                        company_id=company_id, text_snippet=f"{framework}: {yaml_content[:600]}",
                    )
            except Exception:
                pass  # embedding is non-fatal
        return json.dumps({
            "saved":     True,
            "run_id":    run_id,
            "ticker":    ticker,
            "framework": framework,
            "bytes":     len(yaml_content),
        })
    except Exception as e:
        return json.dumps({"error": f"DB save failed: {e}"})


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
