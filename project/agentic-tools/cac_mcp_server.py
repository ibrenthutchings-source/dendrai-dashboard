#!/usr/bin/env python3
"""
Controls-as-Code MCP Server

Exposes Rego controls generation, evaluation, and lifecycle management for
Oracle Fusion ERP as MCP tools usable by Claude Code and Claude Desktop.

── Setup ─────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "controls-as-code": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/cac_mcp_server.py"]
        }
      }
    }

Claude Code — add to .claude/settings.json in your project:

    {
      "mcpServers": {
        "controls-as-code": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/cac_mcp_server.py"]
        }
      }
    }

── Available tools ───────────────────────────────────────────────────────────

    cac_generate         Generate CaC Rego from a JSON controls list and persist
    cac_get_latest       Retrieve the most recent CaC artifact (by ticker)
    cac_list_artifacts   Paginated list of saved CaC artifacts (metadata only)
    cac_from_pac         Synthesise a test-harness CaC from PAC deny rules
    cac_validate         Validate CaC Rego structure and control_active rules
    cac_evaluate_event   Simulate policy evaluation against a sample input event
    cac_export           Export a CaC artifact in Rego, JSON, or YAML format
    cac_map_to_risks     Map CaC controls to risk register items — coverage matrix

── Environment variables ─────────────────────────────────────────────────────

    DATABASE_URL        PostgreSQL connection string (required for persistence)
    MCP_READ_ONLY       Set to "true" to block all write operations
    MCP_RATE_LIMIT_PER_MIN  Override per-tool rate limit (default 30)
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

import yaml
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from mcp_guards import (
    audit_log,
    cap_output,
    check_rate_limit,
    check_read_only,
    validate_enum,
    validate_ticker,
)
import db
from pac_endpoints import (
    VALID_PROCESSES,
    _REGO_DEFAULTS,
    _controls_to_rego,
)

mcp = FastMCP("controls-as-code")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _list_cac_artifacts(ticker: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Return metadata rows from controls_as_code_artifacts (no rego content)."""
    def _q():
        with db._conn() as conn:
            with conn.cursor() as cur:
                if ticker:
                    cur.execute(
                        "SELECT id, ticker, run_id, generated_at "
                        "FROM controls_as_code_artifacts WHERE ticker = %s "
                        "ORDER BY generated_at DESC LIMIT %s",
                        (ticker.upper(), limit),
                    )
                else:
                    cur.execute(
                        "SELECT id, ticker, run_id, generated_at "
                        "FROM controls_as_code_artifacts "
                        "ORDER BY generated_at DESC LIMIT %s",
                        (limit,),
                    )
                return [
                    {
                        "id": r[0], "ticker": r[1], "run_id": r[2],
                        "generated_at": r[3].isoformat() if r[3] else None,
                    }
                    for r in cur.fetchall()
                ]
    return db._run(_q) or []


def _get_cac_artifact_by_id(artifact_id: int) -> Optional[dict]:
    """Fetch a CaC artifact row by primary key."""
    def _q():
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, ticker, run_id, content_rego, generated_at "
                    "FROM controls_as_code_artifacts WHERE id = %s",
                    (artifact_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0], "ticker": row[1], "run_id": row[2],
                    "content_rego": row[3],
                    "generated_at": row[4].isoformat() if row[4] else None,
                }
    return db._run(_q)


def _parse_control_active_rules(rego_content: str) -> list[dict]:
    """
    Extract control_active[ref] := { ... } blocks from CaC Rego.
    Returns a list of parsed control metadata dicts.
    """
    controls: list[dict] = []
    pattern = re.compile(
        r'control_active\["([^"]+)"\]\s*:=\s*\{([^}]+)\}',
        re.MULTILINE,
    )
    for m in pattern.finditer(rego_content):
        ref  = m.group(1)
        body = m.group(2)
        ctrl: dict = {"ref": ref}
        for field in ("name", "framework", "category", "domain", "description", "frequency", "owner"):
            fm = re.search(rf'"{field}"\s*:\s*"([^"]*)"', body)
            if fm:
                ctrl[field] = fm.group(1)
        controls.append(ctrl)
    return controls


def _parse_pac_deny_rules(rego_content: str) -> list[dict]:
    """
    Extract deny_*[msg] rule definitions from a PAC Rego module.
    Returns a list of {rule_name, msg_pattern} dicts.
    """
    rules: list[dict] = []
    pattern = re.compile(
        r'^(deny_\w+)\[msg\]\s+if\s*\{([^}]+)\}',
        re.MULTILINE,
    )
    for m in pattern.finditer(rego_content):
        rule_name = m.group(1)
        body      = m.group(2)
        # Extract msg := sprintf or msg := "..." pattern
        msg_match = re.search(r'msg\s*:=\s*sprintf\("([^"]+)"', body)
        if not msg_match:
            msg_match = re.search(r'msg\s*:=\s*"([^"]+)"', body)
        msg_pattern = msg_match.group(1) if msg_match else ""
        rules.append({"rule_name": rule_name, "msg_pattern": msg_pattern})
    return rules


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def cac_generate(
    controls_json: str,
    ticker: str = "",
    run_id: int = 0,
    persist: bool = True,
) -> str:
    """
    Generate a Controls-as-Code Rego artifact from a JSON list of controls.

    Each control in the list is rendered as an OPA `control_active[ref]` rule
    with name, framework, category, domain, description, frequency, owner, and
    test_criteria fields. Groups controls by category for readability.

    The generated Rego is compatible with OPA's `rego.v1` and FastMCP evaluation.

    Args:
        controls_json: JSON array of control objects. Each object should contain:
                       ref (or control_ref), name, category, framework, domain,
                       description (or desc). Example:
                       '[{"ref":"AC-01","name":"Access Provisioning","category":"ITGC","framework":"SOX"}]'
        ticker:        Stock ticker to tag this artifact (optional, e.g. 'AAPL')
        run_id:        Risk pipeline run_id to link this artifact to (0 = none)
        persist:       Save artifact to database (default true). Set false to
                       preview the generated Rego without writing to DB.
    """
    try:
        check_rate_limit("cac_generate")
        audit_log("cac_generate", ticker=ticker or "(none)", persist=persist)

        try:
            controls = json.loads(controls_json)
        except json.JSONDecodeError as exc:
            return f"Error: controls_json is not valid JSON — {exc}"

        if not isinstance(controls, list) or not controls:
            return "Error: controls_json must be a non-empty JSON array"

        tok = validate_ticker(ticker) if ticker.strip() else None
        rid = int(run_id) if run_id else None

        content_rego = _controls_to_rego(controls, tok)
        artifact_id: Optional[int] = None

        if persist:
            check_read_only("cac_generate (persist)")
            if db.is_available():
                artifact_id = db.save_controls_as_code_artifact(content_rego, tok, rid)
                if artifact_id:
                    try:
                        db.save_embedding(
                            source_table="controls_as_code_artifacts",
                            source_id=artifact_id,
                            content_type=db.EMBT_CAC,
                            text=content_rego[:8000],
                        )
                    except Exception as emb_exc:
                        pass  # embedding is non-fatal

        return cap_output(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ticker": tok,
            "run_id": rid,
            "control_count": len(controls),
            "artifact_id": artifact_id,
            "persisted": artifact_id is not None,
            "content_rego": content_rego,
        }, indent=2))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error generating CaC: {exc}"


@mcp.tool()
def cac_get_latest(ticker: str = "") -> str:
    """
    Retrieve the most recent Controls-as-Code artifact from the database.

    Returns the full Rego content plus metadata (id, ticker, run_id, generated_at).
    Use cac_list_artifacts to browse historical entries.

    Args:
        ticker: Filter by company ticker symbol, e.g. 'AAPL' (empty = global latest)
    """
    try:
        check_rate_limit("cac_get_latest")
        tok = validate_ticker(ticker) if ticker.strip() else None
        audit_log("cac_get_latest", ticker=tok or "(any)")

        if not db.is_available():
            return json.dumps({"artifact": None, "note": "Database not configured"}, indent=2)

        artifact = db.get_latest_cac_artifact(tok)
        return cap_output(json.dumps({"artifact": artifact}, indent=2))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error fetching latest CaC artifact: {exc}"


@mcp.tool()
def cac_list_artifacts(ticker: str = "", limit: int = 20) -> str:
    """
    List saved Controls-as-Code artifacts, newest first.

    Returns metadata only (id, ticker, run_id, generated_at) — no Rego content.
    Use cac_export with a specific artifact_id to retrieve the full content.

    Args:
        ticker: Filter by company ticker (empty = all tickers)
        limit:  Maximum number of results (1–100, default 20)
    """
    try:
        check_rate_limit("cac_list_artifacts")
        tok = validate_ticker(ticker) if ticker.strip() else None
        lim = max(1, min(100, int(limit)))
        audit_log("cac_list_artifacts", ticker=tok or "(any)", limit=lim)

        if not db.is_available():
            return json.dumps({"artifacts": [], "note": "Database not configured"}, indent=2)

        rows = _list_cac_artifacts(tok, lim)
        return json.dumps({"artifacts": rows, "total": len(rows)}, indent=2)
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error listing CaC artifacts: {exc}"


@mcp.tool()
def cac_from_pac(process: str = "", ticker: str = "") -> str:
    """
    Synthesise a Controls-as-Code test harness from Policy-as-Code deny rules.

    Reads the latest PAC Rego module for the specified process (or all five
    processes if process is empty), extracts every deny_*[msg] rule, and generates
    a CaC test harness with one control_active entry per unique deny rule.

    The resulting Rego can be fed to an OPA test runner to verify that each
    deny rule fires correctly against representative input events.

    Persists the synthesised CaC to the database (blocked by MCP_READ_ONLY).

    Args:
        process: ERP process to synthesise from — itgc | order_to_cash |
                 procure_to_pay | receive_to_ship | record_to_report.
                 Empty = synthesise from all five processes.
        ticker:  Optional ticker to tag the generated artifact (e.g. 'MSFT')
    """
    try:
        check_read_only("cac_from_pac")
        check_rate_limit("cac_from_pac")
        tok = validate_ticker(ticker) if ticker.strip() else None
        audit_log("cac_from_pac", process=process or "(all)", ticker=tok or "(none)")

        procs: list[str]
        if process.strip():
            p = process.strip().lower().replace("-", "_").replace(" ", "_")
            if p not in VALID_PROCESSES:
                return f"Error: Unknown process '{process}'. Valid: {', '.join(sorted(VALID_PROCESSES))}"
            procs = [p]
        else:
            procs = sorted(VALID_PROCESSES)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines: list[str] = [
            "# Controls-as-Code Test Harness — synthesised from Policy-as-Code deny rules",
            f"# Entity: {tok or 'Global'}",
            f"# Generated: {now}",
            f"# Processes: {', '.join(procs)}",
            "#",
            "# Each control_active entry corresponds to one PAC deny_*[msg] rule.",
            "# Use with: opa test -v <this_file> <pac_module_file>",
            "",
            "package controls.test_harness",
            "",
            "import future.keywords.in",
            "",
        ]

        all_rules: list[dict] = []
        for proc in procs:
            # Prefer saved module; fall back to built-in default
            rego_content: str = ""
            if db.is_available():
                mod = db.get_latest_pac_module(proc)
                if mod:
                    rego_content = mod["rego_content"]
            if not rego_content:
                rego_content = _REGO_DEFAULTS.get(proc, "")

            deny_rules = _parse_pac_deny_rules(rego_content)
            for r in deny_rules:
                r["process"] = proc
            all_rules.extend(deny_rules)

            if deny_rules:
                divider = "─" * max(0, 74 - len(proc))
                lines.append(f"# ── {proc} {divider}")
                lines.append("")
                seen: set[str] = set()
                for r in deny_rules:
                    ref = f"{proc.upper()}:{r['rule_name']}"
                    if ref in seen:
                        continue
                    seen.add(ref)
                    # Normalise rule name → control ref
                    ctrl_ref = r["rule_name"].replace("deny_", "").replace("_event", "").replace("_", "-").upper()
                    msg_snippet = r["msg_pattern"][:80].replace('"', '\\"') if r["msg_pattern"] else r["rule_name"]
                    lines.append(f'control_active["{proc.upper()}-{ctrl_ref}"] := {{')
                    lines.append(f'    "name":        "{r["rule_name"]}",')
                    lines.append(f'    "framework":   "Oracle Fusion PaC",')
                    lines.append(f'    "process":     "{proc}",')
                    lines.append(f'    "pac_rule":    "{r["rule_name"]}",')
                    lines.append(f'    "description": "{msg_snippet}",')
                    lines.append(f'    "frequency":   "Continuous",')
                    lines.append(f'    "owner":       "Control Owner",')
                    lines.append(f'    "test_criteria": [')
                    lines.append(f'        "Verify deny fires on non-compliant input",')
                    lines.append(f'        "Verify deny does NOT fire on compliant input",')
                    lines.append(f'        "Verify msg format matches policy specification"')
                    lines.append(f'    ]')
                    lines.append("}")
                    lines.append("")

        content_rego = "\n".join(lines).rstrip() + "\n"

        artifact_id: Optional[int] = None
        if db.is_available():
            artifact_id = db.save_controls_as_code_artifact(content_rego, tok, None)
            if artifact_id:
                try:
                    db.save_embedding(
                        source_table="controls_as_code_artifacts",
                        source_id=artifact_id,
                        content_type=db.EMBT_CAC,
                        text=content_rego[:8000],
                    )
                except Exception:
                    pass

        return cap_output(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "pac_deny_rules",
            "processes": procs,
            "ticker": tok,
            "rule_count": len(all_rules),
            "artifact_id": artifact_id,
            "content_rego": content_rego,
        }, indent=2))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error synthesising CaC from PaC: {exc}"


@mcp.tool()
def cac_validate(rego_content: str) -> str:
    """
    Validate Controls-as-Code Rego structure and control_active rules.

    Checks performed (no OPA binary required):
      - Package declaration present
      - At least one control_active[ref] rule defined
      - Each control_active block contains required fields (name, framework,
        category, description, test_criteria)
      - No duplicate control references
      - Brace balance
      - test_criteria is a non-empty list

    Returns a structured report with per-control issues and a summary.

    Args:
        rego_content: CaC Rego text to validate
    """
    try:
        check_rate_limit("cac_validate")
        audit_log("cac_validate")

        content = rego_content.strip()
        errors: list[str]   = []
        warnings: list[str] = []
        required_fields = {"name", "framework", "category", "description"}

        # Package check
        pkg_match = re.search(r'^package\s+([\w.]+)', content, re.MULTILINE)
        package_name = pkg_match.group(1) if pkg_match else None
        if not package_name:
            errors.append("Missing 'package' declaration")

        # Brace balance
        if content.count("{") != content.count("}"):
            errors.append(f"Unbalanced braces: {content.count('{')} '{{' vs {content.count('}')} '}}'")

        # Parse control_active rules
        controls = _parse_control_active_rules(content)
        if not controls:
            errors.append("No 'control_active[ref]' rules found — CaC module appears empty")

        refs_seen: set[str] = set()
        per_control_issues: list[dict] = []
        for ctrl in controls:
            ref      = ctrl["ref"]
            issues   = []
            if ref in refs_seen:
                issues.append(f"Duplicate control reference '{ref}'")
                errors.append(f"Duplicate control reference '{ref}'")
            refs_seen.add(ref)
            for f in required_fields:
                if not ctrl.get(f):
                    issues.append(f"Missing required field '{f}'")
            # test_criteria — check it exists as a list in the raw content
            tc_match = re.search(
                rf'control_active\["{re.escape(ref)}"\][^\{{]*\{{([^}}]+)\}}',
                content,
            )
            if tc_match and "test_criteria" not in tc_match.group(1):
                issues.append("Missing 'test_criteria' list")

            if issues:
                per_control_issues.append({"ref": ref, "issues": issues})

        valid = len(errors) == 0
        return json.dumps({
            "valid": valid,
            "package": package_name,
            "control_count": len(controls),
            "errors": errors,
            "warnings": warnings,
            "per_control_issues": per_control_issues,
            "refs": [c["ref"] for c in controls],
        }, indent=2)
    except Exception as exc:
        return f"Error validating CaC: {exc}"


@mcp.tool()
def cac_evaluate_event(rego_content: str, input_event_json: str) -> str:
    """
    Simulate CaC/PaC policy evaluation against a sample OPA input event.

    This is a Python-based simulation — it does NOT require the OPA binary.
    It pattern-matches deny rule conditions heuristically by checking whether
    the input event fields referenced in each deny rule are present and
    satisfy the comparison conditions (==, !=, >, <, >=, <=).

    Returns a list of rules that would likely FIRE (deny) and rules that would
    PASS (allow) based on the input event, along with confidence scores.

    Note: This is an approximation. For authoritative evaluation, run:
        opa eval -d <module.rego> -i <input.json> 'data.controls.oracle_fusion.<pkg>.deny_*'

    Args:
        rego_content:    Rego module content (PAC or CaC)
        input_event_json: JSON object representing the OPA input document,
                          e.g. '{"event": {"type": "user_provisioning", "approved_by": null}}'
    """
    try:
        check_rate_limit("cac_evaluate_event")
        audit_log("cac_evaluate_event")

        try:
            input_event = json.loads(input_event_json)
        except json.JSONDecodeError as exc:
            return f"Error: input_event_json is not valid JSON — {exc}"

        def _flatten(d: dict, prefix: str = "") -> dict[str, object]:
            """Flatten nested dict to dot-path keys."""
            flat: dict[str, object] = {}
            for k, v in d.items():
                key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    flat.update(_flatten(v, key))
                else:
                    flat[key] = v
            return flat

        flat_input = _flatten(input_event)

        # Extract deny rule bodies
        rule_pattern = re.compile(
            r'^(deny_\w+)\[msg\]\s+if\s*\{([^}]+?)\}',
            re.MULTILINE | re.DOTALL,
        )

        fired: list[dict]   = []
        passed: list[dict]  = []
        skipped: list[dict] = []

        for m in rule_pattern.finditer(rego_content):
            rule_name = m.group(1)
            body      = m.group(2)

            # Extract conditions: lines of form "input.path.field == value"
            conditions = re.findall(
                r'input\.([\w.]+)\s*(==|!=|>|<|>=|<=|!=)\s*([^\n]+)',
                body,
            )
            not_conditions = re.findall(r'not\s+input\.([\w.]+)', body)

            if not conditions and not not_conditions:
                skipped.append({"rule": rule_name, "reason": "No evaluable conditions found"})
                continue

            score = 0
            total = 0
            detail: list[str] = []

            for path, op, val_str in conditions:
                total += 1
                val_str = val_str.strip().strip('"')
                actual  = flat_input.get(f"input.{path}", flat_input.get(path))
                if actual is None:
                    detail.append(f"input.{path} not in event (condition unknown)")
                    continue
                # Best-effort type coercion
                try:
                    val_cmp: object = json.loads(val_str)
                except (json.JSONDecodeError, ValueError):
                    val_cmp = val_str
                try:
                    result = (
                        (op == "==" and actual == val_cmp) or
                        (op == "!=" and actual != val_cmp) or
                        (op == ">"  and float(actual) > float(val_cmp)) or  # type: ignore[arg-type]
                        (op == "<"  and float(actual) < float(val_cmp)) or  # type: ignore[arg-type]
                        (op == ">=" and float(actual) >= float(val_cmp)) or  # type: ignore[arg-type]
                        (op == "<=" and float(actual) <= float(val_cmp))      # type: ignore[arg-type]
                    )
                    if result:
                        score += 1
                    detail.append(f"input.{path} {op} {val_cmp!r}: {'✓' if result else '✗'} (actual={actual!r})")
                except (TypeError, ValueError):
                    detail.append(f"input.{path} {op} {val_cmp!r}: ? (type mismatch)")

            for path in not_conditions:
                total += 1
                actual = flat_input.get(f"input.{path}", flat_input.get(path))
                satisfied = actual is None or actual is False or actual == ""
                if satisfied:
                    score += 1
                detail.append(f"not input.{path}: {'✓' if satisfied else '✗'} (actual={actual!r})")

            confidence = round(score / total, 2) if total else 0.0
            entry = {"rule": rule_name, "confidence": confidence, "conditions_checked": detail}
            if confidence >= 0.7:
                fired.append(entry)
            else:
                passed.append(entry)

        return json.dumps({
            "evaluation": "simulation (Python heuristic — not authoritative OPA)",
            "rules_fired":   fired,
            "rules_passed":  passed,
            "rules_skipped": skipped,
            "summary": {
                "fired_count":   len(fired),
                "passed_count":  len(passed),
                "skipped_count": len(skipped),
            },
        }, indent=2)
    except Exception as exc:
        return f"Error evaluating event: {exc}"


@mcp.tool()
def cac_export(artifact_id: int = 0, format: str = "rego") -> str:
    """
    Export a saved CaC artifact in the specified format.

    Formats:
      rego  — Raw Rego source as stored (default)
      json  — Parsed control metadata array (ref, name, framework, category …)
      yaml  — Same as json but serialised as YAML for use in CI pipelines

    If artifact_id is 0 (default), exports the most recent artifact overall.

    Args:
        artifact_id: Database ID of the artifact (0 = latest)
        format:      Output format — rego | json | yaml (default rego)
    """
    try:
        check_rate_limit("cac_export")
        fmt = validate_enum(format, {"rego", "json", "yaml"}, "format", default="rego")
        audit_log("cac_export", artifact_id=artifact_id, format=fmt)

        if not db.is_available():
            return json.dumps({"error": "Database not configured"}, indent=2)

        if artifact_id:
            artifact = _get_cac_artifact_by_id(int(artifact_id))
        else:
            artifact = db.get_latest_cac_artifact(None)

        if not artifact:
            return json.dumps({"error": f"Artifact{'id=' + str(artifact_id) + ' ' if artifact_id else ' '}not found"}, indent=2)

        rego_content = artifact["content_rego"] or ""

        if fmt == "rego":
            return cap_output(json.dumps({
                "artifact_id": artifact["id"],
                "ticker": artifact.get("ticker"),
                "generated_at": artifact.get("generated_at"),
                "format": "rego",
                "content": rego_content,
            }, indent=2))

        # Parse control_active rules for structured output
        controls = _parse_control_active_rules(rego_content)

        if fmt == "json":
            return cap_output(json.dumps({
                "artifact_id": artifact["id"],
                "ticker": artifact.get("ticker"),
                "generated_at": artifact.get("generated_at"),
                "format": "json",
                "controls": controls,
                "control_count": len(controls),
            }, indent=2))

        # yaml
        doc = {
            "artifact_id":   artifact["id"],
            "ticker":        artifact.get("ticker"),
            "generated_at":  artifact.get("generated_at"),
            "control_count": len(controls),
            "controls":      controls,
        }
        return cap_output(yaml.dump(doc, allow_unicode=True, sort_keys=False))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error exporting CaC artifact: {exc}"


@mcp.tool()
def cac_map_to_risks(ticker: str = "", run_id: int = 0, limit: int = 50) -> str:
    """
    Map Controls-as-Code controls to risk register items — coverage matrix.

    Joins the latest CaC artifact with risk_scores rows for the same ticker
    and run to produce a coverage matrix showing which risks each control
    addresses and which risks have no CaC control mapped to them.

    Matching heuristic: control category or domain tokens are compared against
    the risk category, domain, and name using case-insensitive substring match.

    Args:
        ticker: Company ticker (required for risk join, e.g. 'AAPL')
        run_id: Specific pipeline run_id to pull risks from (0 = latest run)
        limit:  Maximum risks to include in the matrix (default 50, max 200)
    """
    try:
        check_rate_limit("cac_map_to_risks")
        tok = validate_ticker(ticker) if ticker.strip() else None
        rid = int(run_id) if run_id else None
        lim = max(1, min(200, int(limit)))
        audit_log("cac_map_to_risks", ticker=tok or "(none)", run_id=rid, limit=lim)

        if not db.is_available():
            return json.dumps({"error": "Database not configured"}, indent=2)

        # Fetch latest CaC artifact
        artifact = db.get_latest_cac_artifact(tok)
        if not artifact or not artifact.get("content_rego"):
            return json.dumps({
                "error": f"No CaC artifact found{' for ' + tok if tok else ''}. "
                         "Run cac_generate or cac_from_pac first."
            }, indent=2)

        controls = _parse_control_active_rules(artifact["content_rego"])
        if not controls:
            return json.dumps({"error": "CaC artifact contains no parseable control_active rules"}, indent=2)

        # Fetch risk scores
        def _fetch_risks() -> list[dict]:
            with db._conn() as conn:
                with conn.cursor() as cur:
                    if tok and rid:
                        cur.execute(
                            "SELECT id, risk_name, category, domain, risk_score, rag_status "
                            "FROM risk_scores WHERE ticker = %s AND run_id = %s "
                            "ORDER BY risk_score DESC LIMIT %s",
                            (tok, rid, lim),
                        )
                    elif tok:
                        cur.execute(
                            "SELECT id, risk_name, category, domain, risk_score, rag_status "
                            "FROM risk_scores WHERE ticker = %s "
                            "ORDER BY risk_score DESC LIMIT %s",
                            (tok, lim),
                        )
                    else:
                        cur.execute(
                            "SELECT id, risk_name, category, domain, risk_score, rag_status "
                            "FROM risk_scores ORDER BY risk_score DESC LIMIT %s",
                            (lim,),
                        )
                    return [
                        {"id": r[0], "risk_name": r[1], "category": r[2] or "",
                         "domain": r[3] or "", "risk_score": r[4], "rag_status": r[5]}
                        for r in cur.fetchall()
                    ]

        risks = db._run(_fetch_risks) or []
        if not risks:
            return json.dumps({
                "artifact_id": artifact["id"],
                "controls": len(controls),
                "risks": 0,
                "matrix": [],
                "note": f"No risk_scores rows found for {tok or 'any ticker'}",
            }, indent=2)

        # Coverage match: control → risks
        def _tokens(s: str) -> set[str]:
            return {t.lower() for t in re.split(r'[\s_\-/]+', s or "") if len(t) > 2}

        matrix: list[dict] = []
        uncovered_risks: list[str] = []

        for risk in risks:
            risk_tokens = (
                _tokens(risk["risk_name"]) |
                _tokens(risk["category"]) |
                _tokens(risk["domain"])
            )
            matched_controls: list[str] = []
            for ctrl in controls:
                ctrl_tokens = (
                    _tokens(ctrl.get("category", "")) |
                    _tokens(ctrl.get("domain", "")) |
                    _tokens(ctrl.get("name", ""))
                )
                if risk_tokens & ctrl_tokens:
                    matched_controls.append(ctrl["ref"])

            entry = {
                "risk_id":          risk["id"],
                "risk_name":        risk["risk_name"],
                "risk_score":       risk["risk_score"],
                "rag_status":       risk["rag_status"],
                "mapped_controls":  matched_controls,
                "control_coverage": len(matched_controls),
                "covered":          len(matched_controls) > 0,
            }
            matrix.append(entry)
            if not matched_controls:
                uncovered_risks.append(risk["risk_name"])

        covered_count   = sum(1 for r in matrix if r["covered"])
        coverage_pct    = round(covered_count / len(risks) * 100, 1) if risks else 0.0

        return cap_output(json.dumps({
            "artifact_id":       artifact["id"],
            "ticker":            tok,
            "run_id":            rid,
            "cac_controls":      len(controls),
            "risks_evaluated":   len(risks),
            "risks_covered":     covered_count,
            "coverage_pct":      coverage_pct,
            "uncovered_risks":   uncovered_risks,
            "matrix":            matrix,
        }, indent=2))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error mapping CaC to risks: {exc}"


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
