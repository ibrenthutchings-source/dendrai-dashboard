#!/usr/bin/env python3
"""
GitHub SCM Integrity poll-connector adapter (observability.poll_connectors,
connector_type='github_scm').

Scheduled by connector_poller.py alongside the Oracle Fusion/SAP HANA/
SailPoint/Dynamics365/NetSuite adapters — same pull_events()/test_connection()
contract (see connector_poller.py's module docstring). Each poll re-audits the
registered repo/branch's *current* branch-protection + CODEOWNERS state — `since`
is unused, since this is a point-in-time configuration check, not an append-only
log of new events. The one event per poll is tagged with the
branch_protection_violation flag (mcp_governance._detect_system_flags) whenever
the audit found anything the devops_monitoring PaC policy would deny, so it
flows through the normal adjudication pipeline exactly like every other
poll-connector event.

Required per-connector config (set via the app UI, not env vars):
  base_url:     GitHub API host, e.g. "https://api.github.com" (or a GHE host)
  credentials:  {"token": "<personal access token>"}
  extra_config: {"repo_full_name": "owner/repo", "branch": "main"}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import db
import scm_connectors

logger = logging.getLogger(__name__)


def _audit_once(base_url: Optional[str], credentials: dict, extra_config: dict) -> dict:
    extra_config = extra_config or {}
    repo_full_name = extra_config.get("repo_full_name")
    branch = extra_config.get("branch") or "main"
    token = (credentials or {}).get("token")
    if not repo_full_name:
        raise ValueError("extra_config.repo_full_name is required (e.g. 'my-org/my-repo')")
    if not token:
        raise ValueError("credentials.token is required (a GitHub personal access token)")

    api_base = base_url or "https://api.github.com"
    protection = scm_connectors.fetch_github_branch_protection(repo_full_name, branch, token, api_base)
    codeowners = scm_connectors.fetch_github_codeowners(repo_full_name, token, api_base)
    compliance = scm_connectors.normalize_github_compliance(protection, codeowners)

    violated = (
        not compliance["enforce_admins"]
        or compliance["required_approving_review_count"] < 1
        or not compliance["dismiss_stale_reviews"]
        or not compliance["has_required_sast_check"]
        or not compliance["has_required_test_check"]
        or not compliance["codeowners_present"]
        or (compliance["codeowners_present"] and not compliance["codeowners_covers_workflows"])
    )
    severity = "CRITICAL" if not compliance["enforce_admins"] else ("HIGH" if violated else "INFO")

    # Drift & Time-Series: this scheduled path (default every 30 min, per
    # connector_poller._TICK_S / poll_interval_s) is exactly where a short-lived
    # "2am override" gets caught — diff against the last snapshot before
    # anything else runs.
    drift_events = db.record_scm_audit_snapshot(f"{repo_full_name}@{branch}", compliance) if db.is_available() else []

    return {
        "repo_full_name": repo_full_name,
        "branch": branch,
        "compliance": compliance,
        "violated": violated,
        "severity": severity,
        "raw_protection": protection,
        "drift_events": drift_events,
    }


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    """One audit event per poll tick, normalized to the uniform connector
    event shape (event_id, event_type, actor, action, resource, severity,
    raw_payload) per connector_poller.py's documented contract."""
    result = _audit_once(base_url, credentials, extra_config)
    today = datetime.now(timezone.utc).date().isoformat()
    return [{
        "event_id":    f"scm-audit:{result['repo_full_name']}:{result['branch']}:{today}",
        "event_type":  "branch_protection_audit",
        "actor":       "github_scm_tool",
        "action":      "branch_protection_audit",
        "resource":    f"{result['repo_full_name']}@{result['branch']}",
        "severity":    result["severity"],
        "raw_payload": {
            "branch_protection_violation": result["violated"] or bool(result["drift_events"]),
            "compliance": result["compliance"],
            "raw_protection": result["raw_protection"],
            "drift_events": result["drift_events"],
        },
    }]


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity by running one real audit — the same call
    pull_events() needs."""
    try:
        result = _audit_once(base_url, credentials, extra_config)
        return True, f"Fetched branch protection for {result['repo_full_name']}@{result['branch']}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return True
