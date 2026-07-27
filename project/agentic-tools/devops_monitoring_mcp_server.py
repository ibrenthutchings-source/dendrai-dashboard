#!/usr/bin/env python3
"""
DevOps Monitoring MCP Server

Exposes SCM branch-protection auditing and SARIF evidence inspection as MCP
tools usable by Claude Code and Claude Desktop — the same pattern as
pac_mcp_server.py/cac_mcp_server.py, applied to scm_audit_endpoints.py and
evidence_endpoints.py's underlying logic (not the FastAPI routes themselves).

── Setup ─────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "devops-monitoring": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/devops_monitoring_mcp_server.py"]
        }
      }
    }

── Available tools ───────────────────────────────────────────────────────────

    scm_list_repositories   Registered GitHub/GitLab repos under audit (no tokens)
    scm_run_audit           Run a branch-protection/CODEOWNERS audit now
    scm_run_pipeline_security_audit  Run a GitHub Actions workflow-security audit now
    scm_list_drift          Drift/time-series log (control flips, either direction)
    evidence_list_records   Filtered SARIF evidence records
    evidence_verify_record  Recompute the HMAC signature for one evidence record
    waiver_list             List Risk Waivers (ACTIVE/EXPIRED/REVOKED)
    waiver_sweep_now        Run the automated-expiry sweep immediately (write-guarded)
    attestation_list        List pipeline provenance/attestation records
    itsm_list_tickets       List ITSM (Jira/ServiceNow) tickets tracking findings
    itsm_sla_summary        Open/breached/at-risk-24h counts for the ITSM SLA Bridge
    itsm_sweep_now          Run the SLA breach-detection sweep immediately (write-guarded)

── Environment variables ─────────────────────────────────────────────────────

    DATABASE_URL          PostgreSQL connection string (required for persistence)
    EVIDENCE_SIGNING_KEY  HMAC key evidence_verify_record checks records against
    MCP_READ_ONLY         Set to "true" to block scm_run_audit (a write operation)
    MCP_RATE_LIMIT_PER_MIN  Override per-tool rate limit (default 30)
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import sys

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from mcp_guards import audit_log, cap_output, check_rate_limit, check_read_only
import db
import evidence_endpoints
import itsm_sla_sweep
import risk_waiver_sweep
import scm_audit_endpoints

mcp = FastMCP("devops-monitoring")


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def scm_list_repositories() -> str:
    """
    List every GitHub/GitLab repository registered for branch-protection
    auditing. Tokens are never included — only display metadata (provider,
    repo_ref, branch, last_poll_at, risk_tier, etc.).
    """
    try:
        check_rate_limit("scm_list_repositories")
        if not db.is_available():
            return json.dumps({"repositories": [], "note": "Database not configured"}, indent=2)
        rows = [c for c in db.list_poll_connectors() if c["connector_type"] in ("github_scm", "gitlab_scm")]
        out = [{
            "id": c["id"],
            "provider": "github" if c["connector_type"] == "github_scm" else "gitlab",
            "display_name": c["display_name"],
            "repo_ref": scm_audit_endpoints._repo_ref_of(c),
            "branch": (c.get("extra_config") or {}).get("branch"),
            "active": c["active"],
            "last_poll_at": c["last_poll_at"],
            "last_poll_status": c["last_poll_status"],
            "risk_tier": c.get("risk_tier"),
        } for c in rows]
        return cap_output(json.dumps({"repositories": out}, indent=2, default=str))
    except Exception as exc:
        return f"Error listing repositories: {exc}"


@mcp.tool()
def scm_run_audit(repository_id: int) -> str:
    """
    Run a branch-protection + CODEOWNERS audit now for one registered
    repository, adjudicated through the full Bronze->Silver->Gold->Council
    pipeline and the devops_monitoring PaC policy. Blocked when MCP_READ_ONLY=true
    (this writes an adjudication row).

    Args:
        repository_id: The registry id from scm_list_repositories.
    """
    try:
        check_read_only("scm_run_audit")
        check_rate_limit("scm_run_audit")
        if not db.is_available():
            return json.dumps({"note": "Database not configured"}, indent=2)

        audit_log("scm_run_audit", repository_id=repository_id)

        connector = db.get_poll_connector(repository_id, include_credentials=True)
        if not connector or connector["connector_type"] not in ("github_scm", "gitlab_scm"):
            return f"Error: repository {repository_id} not found"

        runner = scm_audit_endpoints._run_github if connector["connector_type"] == "github_scm" \
            else scm_audit_endpoints._run_gitlab
        result = asyncio.run(runner(connector))
        db.record_poll_result(repository_id, "error" if result.get("adjudication_error") else "ok",
                               result.get("adjudication_error"))
        return cap_output(json.dumps(result, indent=2, default=str))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error running audit: {exc}"


@mcp.tool()
def scm_run_pipeline_security_audit(repository_id: int) -> str:
    """
    Run a GitHub Actions workflow-as-code security audit now for one
    registered GitHub repository (permissions least-privilege, unpinned
    third-party actions, risky pull_request_target triggers), adjudicated
    through the full Bronze->Silver->Gold->Council pipeline and the
    devops_monitoring PaC policy. GitHub-only. Blocked when MCP_READ_ONLY=true.

    Args:
        repository_id: The registry id from scm_list_repositories (must be a
            github_scm repository, not gitlab_scm).
    """
    try:
        check_read_only("scm_run_pipeline_security_audit")
        check_rate_limit("scm_run_pipeline_security_audit")
        if not db.is_available():
            return json.dumps({"note": "Database not configured"}, indent=2)

        audit_log("scm_run_pipeline_security_audit", repository_id=repository_id)

        connector = db.get_poll_connector(repository_id, include_credentials=True)
        if not connector or connector["connector_type"] != "github_scm":
            return f"Error: repository {repository_id} not found or not a GitHub repository"

        result = asyncio.run(scm_audit_endpoints._run_github_pipeline_security(connector))
        db.record_poll_result(repository_id, "error" if result.get("adjudication_error") else "ok",
                               result.get("adjudication_error"))
        return cap_output(json.dumps(result, indent=2, default=str))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error running pipeline security audit: {exc}"


@mcp.tool()
def scm_list_drift(resource: str = "", open_only: bool = False, limit: int = 100) -> str:
    """
    Drift & Time-Series log — every branch-protection control that flipped
    between two consecutive audits of the same repo, in either direction.
    A row with resolved_at set shortly after detected_at is the "2am
    override" pattern: briefly non-compliant, then restored before a single
    point-in-time check would have caught it.

    Args:
        resource:  Optional exact filter, e.g. 'my-org/my-repo@main'
        open_only: If true, only unresolved (still-regressed) events
        limit:     Max rows to return (capped at 500)
    """
    try:
        check_rate_limit("scm_list_drift")
        if not db.is_available():
            return json.dumps({"events": [], "note": "Database not configured"}, indent=2)
        rows = db.list_scm_drift_events(resource=resource or None, open_only=open_only, limit=limit)
        return cap_output(json.dumps({"events": rows}, indent=2, default=str))
    except Exception as exc:
        return f"Error listing drift events: {exc}"


@mcp.tool()
def evidence_list_records(repository: str = "", severity: str = "", limit: int = 50) -> str:
    """
    List ingested SARIF evidence records, newest first.

    Args:
        repository: Optional exact repository filter (e.g. 'my-org/my-repo')
        severity:   Optional severity filter — CRITICAL | HIGH | MEDIUM | LOW | INFO
        limit:      Max rows to return (capped at 500)
    """
    try:
        check_rate_limit("evidence_list_records")
        if not db.is_available():
            return json.dumps({"records": [], "note": "Database not configured"}, indent=2)
        rows = db.list_evidence_records(
            repository=repository or None, severity=severity or None, limit=limit)
        return cap_output(json.dumps({"records": rows}, indent=2, default=str))
    except Exception as exc:
        return f"Error listing evidence records: {exc}"


@mcp.tool()
def evidence_verify_record(record_id: int) -> str:
    """
    Recompute the HMAC-SHA256 signature over one evidence record's canonical
    payload and compare it to the stored signature — proves the record hasn't
    been tampered with since ingestion.

    Args:
        record_id: The evidence_records row id (see evidence_list_records).
    """
    try:
        check_rate_limit("evidence_verify_record")
        if not db.is_available():
            return json.dumps({"note": "Database not configured"}, indent=2)
        record = db.get_evidence_record(record_id)
        if not record:
            return f"Error: evidence record {record_id} not found"
        recomputed = evidence_endpoints.sign_record(record["record_json"])
        valid = hmac.compare_digest(recomputed, record["signature"])
        return json.dumps({"id": record_id, "valid": valid, "fingerprint": record["fingerprint"]}, indent=2)
    except Exception as exc:
        return f"Error verifying record: {exc}"


@mcp.tool()
def waiver_list(status: str = "", limit: int = 100) -> str:
    """
    List Risk Waivers (observability.risk_waivers) — documented, time-boxed
    exceptions for findings that can't be fixed within SLA.

    Args:
        status: Optional filter — ACTIVE | EXPIRED | REVOKED
        limit:  Max rows to return (capped at 500)
    """
    try:
        check_rate_limit("waiver_list")
        if not db.is_available():
            return json.dumps({"waivers": [], "note": "Database not configured"}, indent=2)
        rows = db.list_risk_waivers(status=status or None, limit=limit)
        return cap_output(json.dumps({"waivers": rows}, indent=2, default=str))
    except Exception as exc:
        return f"Error listing waivers: {exc}"


@mcp.tool()
def waiver_sweep_now() -> str:
    """
    Run the automated Risk Waiver expiry sweep immediately, instead of
    waiting for the hourly background loop — flips overdue ACTIVE waivers to
    EXPIRED and re-opens their underlying findings as failing again. Blocked
    when MCP_READ_ONLY=true (this writes).
    """
    try:
        check_read_only("waiver_sweep_now")
        check_rate_limit("waiver_sweep_now")
        if not db.is_available():
            return json.dumps({"note": "Database not configured"}, indent=2)
        audit_log("waiver_sweep_now")
        expired_count = asyncio.run(risk_waiver_sweep.sweep_once())
        return json.dumps({"expired_count": expired_count}, indent=2)
    except Exception as exc:
        return f"Error running waiver sweep: {exc}"


@mcp.tool()
def attestation_list(commit_sha: str = "", limit: int = 50) -> str:
    """
    List pipeline provenance/attestation records (OIDC identity, SLSA level
    estimate, environment-variable hash, runner metadata, Cosign verification
    status, SBOM license-risk flag) — one row per ingested CI run.

    Args:
        commit_sha: Optional exact filter
        limit:      Max rows to return (capped at 500)
    """
    try:
        check_rate_limit("attestation_list")
        if not db.is_available():
            return json.dumps({"attestations": [], "note": "Database not configured"}, indent=2)
        rows = db.list_pipeline_attestations(commit_sha=commit_sha or None, limit=limit)
        return cap_output(json.dumps({"attestations": rows}, indent=2, default=str))
    except Exception as exc:
        return f"Error listing attestations: {exc}"


@mcp.tool()
def itsm_list_tickets(status: str = "", external_system: str = "", breached_only: bool = False,
                       limit: int = 100) -> str:
    """
    List ITSM (Jira/ServiceNow) tickets tracking DevOps Monitoring findings,
    with their remediation SLA status.

    Args:
        status: Optional filter — open | in_progress | resolved | closed | cancelled
        external_system: Optional filter — jira | servicenow
        breached_only: If true, only tickets past their SLA due date
        limit: Max rows to return (capped at 500)
    """
    try:
        check_rate_limit("itsm_list_tickets")
        if not db.is_available():
            return json.dumps({"tickets": [], "note": "Database not configured"}, indent=2)
        rows = db.list_itsm_tickets(status=status or None, external_system=external_system or None,
                                     breached_only=breached_only, limit=limit)
        return cap_output(json.dumps({"tickets": rows}, indent=2, default=str))
    except Exception as exc:
        return f"Error listing ITSM tickets: {exc}"


@mcp.tool()
def itsm_sla_summary() -> str:
    """Open/breached/at-risk-within-24h counts for the ITSM SLA Bridge, for a
    quick posture check without listing every ticket."""
    try:
        check_rate_limit("itsm_sla_summary")
        if not db.is_available():
            return json.dumps({"open": 0, "breached": 0, "at_risk_24h": 0, "note": "Database not configured"}, indent=2)
        import itsm_endpoints
        import asyncio
        return json.dumps(asyncio.run(itsm_endpoints.sla_summary()), indent=2)
    except Exception as exc:
        return f"Error computing SLA summary: {exc}"


@mcp.tool()
def itsm_sweep_now() -> str:
    """
    Run the ITSM SLA breach-detection sweep immediately instead of waiting
    for the hourly background loop — flags overdue open tickets as breached
    and re-escalates their underlying findings as failing again. Blocked
    when MCP_READ_ONLY=true (this writes).
    """
    try:
        check_read_only("itsm_sweep_now")
        check_rate_limit("itsm_sweep_now")
        if not db.is_available():
            return json.dumps({"note": "Database not configured"}, indent=2)
        audit_log("itsm_sweep_now")
        breached_count = asyncio.run(itsm_sla_sweep.sweep_once())
        return json.dumps({"breached_count": breached_count}, indent=2)
    except Exception as exc:
        return f"Error running ITSM SLA sweep: {exc}"


if __name__ == "__main__":
    mcp.run()
