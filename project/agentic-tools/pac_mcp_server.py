#!/usr/bin/env python3
"""
Policy-as-Code MCP Server

Exposes Rego policy module management for Oracle Fusion ERP processes as
MCP tools usable by Claude Code and Claude Desktop.

── Setup ─────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "policy-as-code": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/pac_mcp_server.py"]
        }
      }
    }

Claude Code — add to .claude/settings.json in your project:

    {
      "mcpServers": {
        "policy-as-code": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/pac_mcp_server.py"]
        }
      }
    }

── Available tools ───────────────────────────────────────────────────────────

    pac_list_modules    All process modules — latest versions with defaults fallback
    pac_get_module      Full Rego content + version + approvals for a process
    pac_save_module     Save / version-bump a Rego module (write-guarded)
    pac_module_history  Version history for a process, newest first
    pac_approve_module  Add an approver sign-off for a module version (write-guarded)
    pac_get_hooks       External hook configs — GitHub and/or Confluence
    pac_save_hook       Save / update an external hook config (write-guarded)
    pac_get_default     Built-in Rego default for a process — no DB required
    pac_validate_rego   Validate Rego syntax and package structure
    pac_diff_modules    Side-by-side diff of the two most recent module versions
    pac_run_negative_tests   Schema-contract check + must-fire/must-not-fire corpus (write-guarded)
    pac_negative_test_history  Past negative-control test runs for a process
    pac_assurance_summary     Which policy-enforced controls are proven working vs. unverified
    pac_run_negative_sweep_now  Run the periodic full-evaluation sweep for every process now (write-guarded)

── Environment variables ─────────────────────────────────────────────────────

    DATABASE_URL        PostgreSQL connection string (required for persistence)
    MCP_READ_ONLY       Set to "true" to block all write operations
    MCP_RATE_LIMIT_PER_MIN  Override per-tool rate limit (default 30)
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import sys

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from mcp_guards import audit_log, cap_output, check_rate_limit, check_read_only, validate_enum
import db
import pac_assurance
import pac_negative_sweep
from pac_endpoints import (
    _PROCESS_LABELS,
    _REGO_DEFAULTS,
    _valid_processes,
)

mcp = FastMCP("policy-as-code")


def _require_process(process: str) -> str:
    """Normalise and validate a process name; raises ValueError on unknown values."""
    p = process.strip().lower().replace("-", "_").replace(" ", "_")
    valid = _valid_processes()
    if p not in valid:
        raise ValueError(
            f"Unknown process '{process}'. Valid values: {', '.join(sorted(valid))}"
        )
    return p


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def pac_list_modules(process: str = "") -> str:
    """
    List the latest Rego policy modules for all Oracle Fusion ERP processes.

    Returns metadata (id, process, module_name, version, approvals, last_revised_at)
    for every process. Processes not yet saved to the database fall back to the
    built-in defaults with is_default=true.

    Args:
        process: Optional filter — return only this process (e.g. 'itgc', 'order_to_cash').
                 Empty string returns all five processes.
    """
    try:
        check_rate_limit("pac_list_modules")
        audit_log("pac_list_modules", process=process or "(all)")

        filter_proc = None
        if process.strip():
            filter_proc = _require_process(process)

        saved: dict = {}
        if db.is_available():
            for m in db.list_pac_modules():
                saved[m["process"]] = m

        procs = [filter_proc] if filter_proc else sorted(_valid_processes())
        result = []
        for proc in procs:
            if proc in saved:
                result.append(saved[proc])
            else:
                result.append({
                    "id": None,
                    "process": proc,
                    "label": _PROCESS_LABELS.get(proc, proc),
                    "module_name": f"controls.oracle_fusion.{proc}",
                    "version": "1.0",
                    "last_revised_at": None,
                    "approvals": [],
                    "is_default": True,
                })

        return cap_output(json.dumps({"modules": result, "total": len(result)}, indent=2))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error listing modules: {exc}"


@mcp.tool()
def pac_get_module(process: str) -> str:
    """
    Return the full Rego content, version, and approvers for a policy module.

    Retrieves the most recently saved version from the database. If the process
    has never been saved, returns the built-in Dendrai default Rego with
    is_default=true — useful for bootstrapping or reading before any edits.

    Args:
        process: ERP process — itgc | order_to_cash | procure_to_pay |
                 receive_to_ship | record_to_report
    """
    try:
        check_rate_limit("pac_get_module")
        proc = _require_process(process)
        audit_log("pac_get_module", process=proc)

        if db.is_available():
            mod = db.get_latest_pac_module(proc)
            if mod:
                return cap_output(json.dumps(mod, indent=2))

        return cap_output(json.dumps({
            "id": None,
            "process": proc,
            "label": _PROCESS_LABELS.get(proc, proc),
            "module_name": f"controls.oracle_fusion.{proc}",
            "rego_content": _REGO_DEFAULTS.get(proc, f"package controls.oracle_fusion.{proc}\n"),
            "version": "1.0",
            "last_revised_at": None,
            "created_at": None,
            "approvals": [],
            "is_default": True,
        }, indent=2))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error fetching module for '{process}': {exc}"


@mcp.tool()
def pac_save_module(
    process: str,
    rego_content: str,
    version: str = "",
    module_name: str = "",
) -> str:
    """
    Save a new versioned Rego policy module for an ERP process.

    Each call inserts a new row (immutable version history). The saved module
    becomes the new "latest" for that process. Approvals accumulate per module_id
    and are not carried over when a new version is saved.

    Blocked when MCP_READ_ONLY=true.

    Args:
        process:      ERP process — itgc | order_to_cash | procure_to_pay |
                      receive_to_ship | record_to_report
        rego_content: The complete Rego module text (must include a package declaration)
        version:      Version label, e.g. '1.1' or '2026-07-04' (auto-increments if empty)
        module_name:  OPA package path override, e.g. 'controls.oracle_fusion.itgc'
                      (defaults to the canonical package for the process)
    """
    try:
        check_read_only("pac_save_module")
        check_rate_limit("pac_save_module")
        proc = _require_process(process)

        content = rego_content.strip()
        if not content:
            return "Error: rego_content must not be empty"
        if "package " not in content:
            return "Error: rego_content must contain a 'package' declaration"

        audit_log("pac_save_module", process=proc, version=version or "auto")

        mod_name = module_name.strip() or f"controls.oracle_fusion.{proc}"

        # Auto-increment version if not supplied
        ver = version.strip()
        if not ver:
            history = db.get_pac_module_history(proc, limit=1) if db.is_available() else []
            if history:
                last = history[0].get("version", "1.0")
                try:
                    major, minor = last.rsplit(".", 1)
                    ver = f"{major}.{int(minor) + 1}"
                except (ValueError, AttributeError):
                    ver = "1.1"
            else:
                ver = "1.0"

        if not db.is_available():
            return json.dumps({
                "saved": False,
                "note": "Database not configured — content accepted but not persisted",
                "process": proc,
                "version": ver,
            }, indent=2)

        module_id = db.save_pac_module(proc, mod_name, content, ver)
        if not module_id:
            return "Error: Database insert failed"

        return json.dumps({
            "saved": True,
            "module_id": module_id,
            "process": proc,
            "module_name": mod_name,
            "version": ver,
        }, indent=2)
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error saving module: {exc}"


@mcp.tool()
def pac_module_history(process: str, limit: int = 10) -> str:
    """
    Return the version history for a policy module, newest version first.

    Each entry includes the module_id (required for pac_approve_module), version
    label, and timestamps. Rego content is not included — use pac_get_module for
    the latest content.

    Args:
        process: ERP process name
        limit:   Maximum number of versions to return (1–50, default 10)
    """
    try:
        check_rate_limit("pac_module_history")
        proc = _require_process(process)
        lim = max(1, min(50, int(limit)))
        audit_log("pac_module_history", process=proc, limit=lim)

        if not db.is_available():
            return json.dumps({"process": proc, "history": [], "note": "Database not configured"}, indent=2)

        history = db.get_pac_module_history(proc, limit=lim)
        return json.dumps({"process": proc, "history": history, "total": len(history)}, indent=2)
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error fetching history for '{process}': {exc}"


@mcp.tool()
def pac_approve_module(module_id: int, approver: str, role: str = "") -> str:
    """
    Add an approver sign-off to a specific policy module version.

    Multiple approvers can sign off on the same module_id. Use pac_module_history
    to find the module_id for the version you want to approve.

    Blocked when MCP_READ_ONLY=true.

    Args:
        module_id: Integer database ID from pac_module_history or pac_save_module
        approver:  Full name of the approver (e.g. 'Jane Smith')
        role:      Approver's role, e.g. 'CISO', 'CFO', 'VP Engineering' (optional)
    """
    try:
        check_read_only("pac_approve_module")
        check_rate_limit("pac_approve_module")

        if not approver.strip():
            return "Error: approver name is required"
        if not re.match(r"^[\w\s'.,\-]{1,128}$", approver):
            return "Error: approver contains invalid characters"

        audit_log("pac_approve_module", module_id=module_id, approver=approver, role=role or "(none)")

        if not db.is_available():
            return json.dumps({"saved": False, "note": "Database not configured"}, indent=2)

        approval_id = db.save_pac_approval(int(module_id), approver.strip(), role.strip() or None)
        if not approval_id:
            return "Error: Database insert failed — check that module_id exists"

        return json.dumps({
            "saved": True,
            "approval_id": approval_id,
            "module_id": module_id,
            "approver": approver.strip(),
            "role": role.strip() or None,
        }, indent=2)
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error saving approval: {exc}"


@mcp.tool()
def pac_get_hooks(hook_type: str = "") -> str:
    """
    Return external integration hook configurations for Policy-as-Code.

    Supported hooks:
      github     — repo URL, branch, and PAT token for pushing Rego to GitHub
      confluence — Confluence space key and URL for syncing policy narratives

    Args:
        hook_type: 'github' | 'confluence' | '' (empty returns both)
    """
    try:
        check_rate_limit("pac_get_hooks")
        audit_log("pac_get_hooks", hook_type=hook_type or "(all)")

        if not db.is_available():
            return json.dumps({"hooks": {}, "note": "Database not configured"}, indent=2)

        if hook_type.strip():
            ht = validate_enum(hook_type, {"github", "confluence"}, "hook_type")
            if not ht:
                return f"Error: hook_type must be 'github' or 'confluence', got '{hook_type}'"
            hook = db.get_pac_hook(ht)
            return json.dumps({"hooks": {ht: hook["config"] if hook else None}}, indent=2)

        all_hooks = db.get_all_pac_hooks()
        return json.dumps({"hooks": {ht: h["config"] for ht, h in all_hooks.items()}}, indent=2)
    except Exception as exc:
        return f"Error fetching hooks: {exc}"


@mcp.tool()
def pac_save_hook(
    hook_type: str,
    repo_url: str = "",
    branch: str = "main",
    token: str = "",
    confluence_url: str = "",
    space_key: str = "",
    page_id: str = "",
) -> str:
    """
    Save or update an external integration hook for Policy-as-Code.

    GitHub hook — push Rego modules to a repository branch after each save.
      Required: hook_type='github', repo_url
      Optional: branch (default 'main'), token (PAT)

    Confluence hook — sync policy narratives to a Confluence space/page.
      Required: hook_type='confluence', confluence_url, space_key
      Optional: page_id (target page for Rego narrative exports)

    Blocked when MCP_READ_ONLY=true.

    Args:
        hook_type:      'github' | 'confluence'
        repo_url:       GitHub repository HTTPS URL (github hook)
        branch:         Target branch for Rego pushes (github hook, default 'main')
        token:          GitHub Personal Access Token (github hook)
        confluence_url: Base URL of the Confluence instance (confluence hook)
        space_key:      Confluence space key, e.g. 'RISK' (confluence hook)
        page_id:        Confluence page ID to update (confluence hook, optional)
    """
    try:
        check_read_only("pac_save_hook")
        check_rate_limit("pac_save_hook")

        ht = validate_enum(hook_type, {"github", "confluence"}, "hook_type")
        if not ht:
            return f"Error: hook_type must be 'github' or 'confluence', got '{hook_type}'"

        if ht == "github":
            if not repo_url.strip():
                return "Error: repo_url is required for the github hook"
            config = {
                "repo_url": repo_url.strip(),
                "branch": branch.strip() or "main",
                "token": token.strip(),
            }
        else:
            if not confluence_url.strip() or not space_key.strip():
                return "Error: confluence_url and space_key are required for the confluence hook"
            config = {
                "url": confluence_url.strip(),
                "space_key": space_key.strip().upper(),
                "page_id": page_id.strip() or None,
            }

        audit_log("pac_save_hook", hook_type=ht)

        if not db.is_available():
            return json.dumps({"saved": False, "note": "Database not configured"}, indent=2)

        ok = db.upsert_pac_hook(ht, config)
        return json.dumps({"saved": ok, "hook_type": ht}, indent=2)
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error saving hook: {exc}"


@mcp.tool()
def pac_get_default(process: str) -> str:
    """
    Return the built-in Dendrai default Rego module for an ERP process.

    Does not require a database connection. Useful for bootstrapping a new
    environment, previewing canonical policy before editing, or resetting a
    module to its baseline.

    The defaults include production-grade Oracle Fusion Rego for all five
    processes: ITGC, O2C, P2P, R2S, and R2R.

    Args:
        process: ERP process — itgc | order_to_cash | procure_to_pay |
                 receive_to_ship | record_to_report
    """
    try:
        check_rate_limit("pac_get_default")
        proc = _require_process(process)
        audit_log("pac_get_default", process=proc)

        rego = _REGO_DEFAULTS.get(proc, f"package controls.oracle_fusion.{proc}\n")
        return cap_output(json.dumps({
            "process": proc,
            "label": _PROCESS_LABELS.get(proc, proc),
            "module_name": f"controls.oracle_fusion.{proc}",
            "rego_content": rego,
            "is_default": True,
        }, indent=2))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error fetching default for '{process}': {exc}"


@mcp.tool()
def pac_validate_rego(rego_content: str) -> str:
    """
    Validate a Rego module's syntax and structural integrity.

    Performs static analysis without requiring an OPA binary:
      - Package declaration present and well-formed
      - At least one rule defined
      - No unclosed braces or brackets
      - deny_* rules follow the Dendrai naming convention
      - String interpolation uses sprintf correctly (balanced % markers)

    Returns a structured report with errors, warnings, and a rule inventory.

    Args:
        rego_content: Full Rego module text to validate
    """
    try:
        check_rate_limit("pac_validate_rego")
        audit_log("pac_validate_rego")

        content = rego_content.strip()
        errors: list[str] = []
        warnings: list[str] = []

        # Package declaration
        pkg_match = re.search(r'^package\s+([\w.]+)', content, re.MULTILINE)
        if not pkg_match:
            errors.append("Missing 'package' declaration — every Rego module must declare a package")
            package_name = None
        else:
            package_name = pkg_match.group(1)

        # Brace balance
        open_b  = content.count("{")
        close_b = content.count("}")
        if open_b != close_b:
            errors.append(f"Unbalanced braces: {open_b} '{{' vs {close_b} '}}'")

        open_br  = content.count("[")
        close_br = content.count("]")
        if open_br != close_br:
            warnings.append(f"Unbalanced brackets: {open_br} '[' vs {close_br} ']'")

        # Rule inventory
        deny_rules    = re.findall(r'^(deny_\w+)\[msg\]', content, re.MULTILINE)
        allow_rules   = re.findall(r'^(allow)\b',         content, re.MULTILINE)
        generic_rules = re.findall(r'^(\w+)\s*:?=\s*\{',  content, re.MULTILINE)

        rule_names = sorted(set(deny_rules))
        if not deny_rules and not allow_rules and not generic_rules:
            warnings.append("No rules detected — module may be incomplete")

        # Import checks
        has_future_kw  = "import future.keywords" in content
        uses_if        = bool(re.search(r'\bif\b', content))
        uses_in        = bool(re.search(r'\bin\b', content))
        if uses_if and not has_future_kw:
            warnings.append("Uses 'if' keyword but missing 'import future.keywords.if'")
        if uses_in and not has_future_kw:
            warnings.append("Uses 'in' keyword but missing 'import future.keywords.in'")

        # sprintf format string sanity: count %v markers vs sprintf args
        for m in re.finditer(r'sprintf\("([^"]+)",\s*\[([^\]]*)\]', content):
            fmt, args_str = m.group(1), m.group(2)
            pct_count  = fmt.count("%v")
            arg_count  = len([a for a in args_str.split(",") if a.strip()])
            if pct_count != arg_count:
                warnings.append(
                    f"sprintf format/arg mismatch: '{fmt[:60]}' has {pct_count} %v but {arg_count} arg(s)"
                )

        valid = len(errors) == 0
        return json.dumps({
            "valid": valid,
            "package": package_name,
            "errors": errors,
            "warnings": warnings,
            "rule_inventory": {
                "deny_rules":  rule_names,
                "deny_count":  len(deny_rules),
                "total_rules": len(deny_rules) + len(allow_rules) + len(set(generic_rules)),
            },
        }, indent=2)
    except Exception as exc:
        return f"Error validating Rego: {exc}"


@mcp.tool()
def pac_diff_modules(process: str, context_lines: int = 5) -> str:
    """
    Show a unified diff between the two most recent saved versions of a module.

    Useful for reviewing what changed before approving a new version, or for
    documenting change justification in audit trail notes.

    Returns a unified diff string plus metadata (version labels, line counts).
    If fewer than two versions exist in the database, returns a message explaining
    that there is nothing to diff.

    Args:
        process:       ERP process name
        context_lines: Lines of context around each change (default 5, max 20)
    """
    try:
        check_rate_limit("pac_diff_modules")
        proc = _require_process(process)
        ctx = max(0, min(20, int(context_lines)))
        audit_log("pac_diff_modules", process=proc, context_lines=ctx)

        if not db.is_available():
            return json.dumps({"error": "Database not configured"}, indent=2)

        history = db.get_pac_module_history(proc, limit=2)
        if len(history) < 2:
            return json.dumps({
                "process": proc,
                "diff": None,
                "note": f"Only {len(history)} version(s) saved for '{proc}' — need at least 2 to diff",
            }, indent=2)

        newer_id = history[0]["id"]
        older_id = history[1]["id"]

        def _fetch(mid: int) -> dict | None:
            import db as _db
            def _q():
                with _db._conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT version, rego_content FROM pac_policy_modules WHERE id = %s",
                            (mid,),
                        )
                        row = cur.fetchone()
                        return {"version": row[0], "rego_content": row[1]} if row else None
            return _db._run(_q)

        newer = _fetch(newer_id)
        older = _fetch(older_id)
        if not newer or not older:
            return json.dumps({"error": "Could not retrieve module versions from database"}, indent=2)

        old_lines  = older["rego_content"].splitlines(keepends=True)
        new_lines  = newer["rego_content"].splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"v{older['version']} (id={older_id})",
            tofile=f"v{newer['version']} (id={newer_id})",
            n=ctx,
        ))

        return cap_output(json.dumps({
            "process": proc,
            "older": {"id": older_id, "version": older["version"]},
            "newer": {"id": newer_id, "version": newer["version"]},
            "changed_lines": len(diff_lines),
            "diff": "".join(diff_lines) or "(no differences)",
        }, indent=2))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error diffing modules for '{process}': {exc}"


@mcp.tool()
def pac_run_negative_tests(process: str, rego_content: str = "") -> str:
    """
    Run negative testing against a process's Rego: a schema-contract check
    (does every input.event.<field>/event-type literal it references
    correspond to something the real adjudication pipeline actually
    produces — see pac_contracts.py) plus a must-fire/must-not-fire fixture
    corpus (does it actually catch the known-bad cases it claims to —
    pac_negative_tests.py). Persists the result as audit evidence
    (observability.pac_test_runs) and updates each exercised control's
    last_verified_at/last_test_passed.

    Only devops_monitoring has a registered fixture corpus today — every
    other built-in process currently fails the contract check (no real
    producer wires their input fields yet), which is itself the finding.

    Blocked when MCP_READ_ONLY=true (writes a test-run row).

    Args:
        process:      Process to test — e.g. 'devops_monitoring', 'itgc'
        rego_content: Optional — test this Rego instead of whatever is
                      currently saved (or the built-in default) for the process
    """
    try:
        check_read_only("pac_run_negative_tests")
        check_rate_limit("pac_run_negative_tests")
        proc = _require_process(process)
        audit_log("pac_run_negative_tests", process=proc)

        content = rego_content.strip()
        module_id = None
        if not content:
            saved = db.get_latest_pac_module(proc) if db.is_available() else None
            if saved:
                content = saved["rego_content"]
                module_id = saved.get("id")
            else:
                content = _REGO_DEFAULTS.get(proc, "")
        if not content:
            return f"Error: no Rego content available for process '{proc}'"

        result = pac_assurance.evaluate_and_record(
            proc, content, module_id=module_id, triggered_by="manual", triggered_by_user="mcp"
        )
        return cap_output(json.dumps(result, indent=2, default=str))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error running negative tests for '{process}': {exc}"


@mcp.tool()
def pac_negative_test_history(process: str, limit: int = 20) -> str:
    """
    Past negative-control test runs for a process, newest first — audit
    evidence that a control was actually tested, and when, not just a claim
    that it was.

    Args:
        process: Process name
        limit:   Max rows to return (capped at 500)
    """
    try:
        check_rate_limit("pac_negative_test_history")
        proc = _require_process(process)
        audit_log("pac_negative_test_history", process=proc)

        if not db.is_available():
            return json.dumps({"process": proc, "runs": [], "note": "Database not configured"}, indent=2)

        runs = db.list_pac_test_runs(process=proc, limit=limit)
        return cap_output(json.dumps({"process": proc, "runs": runs}, indent=2, default=str))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error fetching negative-test history for '{process}': {exc}"


@mcp.tool()
def pac_assurance_summary(process: str = "", stale_days: int = 30) -> str:
    """
    Which policy-enforced controls are currently proven working (a recent
    real production fire and/or a passing negative-control test within
    stale_days) vs. unverified (neither) — the silent-rule-detection view.
    An unverified control isn't necessarily broken, but nothing currently
    proves it works.

    Args:
        process:    Optional filter — restrict to one process
        stale_days: How many days without evidence counts as unverified (default 30)
    """
    try:
        check_rate_limit("pac_assurance_summary")
        proc = _require_process(process) if process.strip() else None
        audit_log("pac_assurance_summary", process=proc or "(all)")

        summary = pac_assurance.assurance_summary(process=proc, stale_days=stale_days)
        return cap_output(json.dumps(summary, indent=2, default=str))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error computing assurance summary: {exc}"


@mcp.tool()
def pac_run_negative_sweep_now() -> str:
    """
    Run the periodic full-evaluation negative-testing sweep immediately,
    instead of waiting for the hourly background loop — tests every
    registered process's currently-live Rego (latest saved module, or the
    built-in default) and persists the result as audit evidence. Also
    detects regressions: a process that passed its previous sweep and fails
    this one gets logged as a warning even if its Rego text didn't change
    (a Silver-layer conformer edit elsewhere can break a contract just as
    easily as editing the policy itself).

    Blocked when MCP_READ_ONLY=true (this writes test-run rows).
    """
    try:
        check_read_only("pac_run_negative_sweep_now")
        check_rate_limit("pac_run_negative_sweep_now")
        audit_log("pac_run_negative_sweep_now")

        results = asyncio.run(pac_negative_sweep.sweep_once())
        summary = {
            proc: {"ok": r["ok"], "contract_ok": r["contract"]["ok"], "corpus": r["corpus"].get("ok")}
            for proc, r in results.items()
        }
        return json.dumps({"processes_tested": len(results), "results": summary}, indent=2)
    except Exception as exc:
        return f"Error running negative-testing sweep: {exc}"


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
