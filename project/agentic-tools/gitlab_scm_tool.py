#!/usr/bin/env python3
"""
GitLab SCM Integrity poll-connector adapter (observability.poll_connectors,
connector_type='gitlab_scm'). GitLab counterpart of github_scm_tool.py — see
that module's docstring for the shared design (point-in-time audit, no
`since` delta, branch_protection_violation flag).

Required per-connector config (set via the app UI, not env vars):
  base_url:     GitLab API host, e.g. "https://gitlab.com/api/v4" (or self-managed)
  credentials:  {"token": "<personal/project access token>"}
  extra_config: {"project_ref": "namespace/project", "branch": "main"}
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
    project_ref = extra_config.get("project_ref")
    branch = extra_config.get("branch") or "main"
    token = (credentials or {}).get("token")
    if not project_ref:
        raise ValueError("extra_config.project_ref is required (e.g. 'my-group/my-project' or a numeric project id)")
    if not token:
        raise ValueError("credentials.token is required (a GitLab personal/project access token)")

    api_base = base_url or "https://gitlab.com/api/v4"
    protected_branch = scm_connectors.fetch_gitlab_protected_branches(project_ref, branch, token, api_base)
    approval_rules = scm_connectors.fetch_gitlab_approval_rules(project_ref, token, api_base)
    codeowners = scm_connectors.fetch_gitlab_codeowners(project_ref, branch, token, api_base)
    compliance = scm_connectors.normalize_gitlab_compliance(protected_branch, approval_rules, codeowners)

    violated = (
        not compliance["enforce_admins"]
        or compliance["required_approving_review_count"] < 1
        or not compliance["dismiss_stale_reviews"]
        or not compliance["codeowners_present"]
        or (compliance["codeowners_present"] and not compliance["codeowners_covers_workflows"])
    )
    severity = "CRITICAL" if not compliance["enforce_admins"] else ("HIGH" if violated else "INFO")

    drift_events = db.record_scm_audit_snapshot(f"{project_ref}@{branch}", compliance) if db.is_available() else []

    return {
        "project_ref": project_ref,
        "branch": branch,
        "compliance": compliance,
        "violated": violated,
        "severity": severity,
        "raw_protected_branch": protected_branch,
        "drift_events": drift_events,
    }


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    result = _audit_once(base_url, credentials, extra_config)
    today = datetime.now(timezone.utc).date().isoformat()
    return [{
        "event_id":    f"scm-audit:{result['project_ref']}:{result['branch']}:{today}",
        "event_type":  "protected_branch_audit",
        "actor":       "gitlab_scm_tool",
        "action":      "protected_branch_audit",
        "resource":    f"{result['project_ref']}@{result['branch']}",
        "severity":    result["severity"],
        "raw_payload": {
            "branch_protection_violation": result["violated"] or bool(result["drift_events"]),
            "compliance": result["compliance"],
            "raw_protected_branch": result["raw_protected_branch"],
            "drift_events": result["drift_events"],
        },
    }]


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    try:
        result = _audit_once(base_url, credentials, extra_config)
        return True, f"Fetched protected branch config for {result['project_ref']}@{result['branch']}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return True
