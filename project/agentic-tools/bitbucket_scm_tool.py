#!/usr/bin/env python3
"""
Bitbucket SCM Integrity poll-connector adapter (observability.poll_connectors,
connector_type='bitbucket_scm'). Bitbucket counterpart of github_scm_tool.py/
gitlab_scm_tool.py — see github_scm_tool.py's docstring for the shared design
(point-in-time audit, no `since` delta, branch_protection_violation flag).

Required per-connector config (set via the app UI, not env vars):
  base_url:     Bitbucket API host, e.g. "https://api.bitbucket.org/2.0" (or self-managed)
  credentials:  {"token": "<repository/workspace/API access token>"}
  extra_config: {"repo_full_name": "workspace/repo_slug", "branch": "main"}
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
        raise ValueError("extra_config.repo_full_name is required (e.g. 'my-workspace/my-repo')")
    if not token:
        raise ValueError("credentials.token is required (a Bitbucket access token)")

    api_base = base_url or "https://api.bitbucket.org/2.0"
    restrictions = scm_connectors.fetch_bitbucket_branch_restrictions(repo_full_name, branch, token, api_base)
    codeowners = scm_connectors.fetch_bitbucket_codeowners(repo_full_name, branch, token, api_base)
    compliance = scm_connectors.normalize_bitbucket_compliance(restrictions, codeowners)

    violated = (
        not compliance["enforce_admins"]
        or compliance["required_approving_review_count"] < 1
        or not compliance["dismiss_stale_reviews"]
        or not compliance["codeowners_present"]
        or (compliance["codeowners_present"] and not compliance["codeowners_covers_workflows"])
    )
    severity = "CRITICAL" if not compliance["enforce_admins"] else ("HIGH" if violated else "INFO")

    drift_events = db.record_scm_audit_snapshot(f"{repo_full_name}@{branch}", compliance) if db.is_available() else []

    return {
        "repo_full_name": repo_full_name,
        "branch": branch,
        "compliance": compliance,
        "violated": violated,
        "severity": severity,
        "raw_restrictions": restrictions,
        "drift_events": drift_events,
    }


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    result = _audit_once(base_url, credentials, extra_config)
    today = datetime.now(timezone.utc).date().isoformat()
    return [{
        "event_id":    f"scm-audit:{result['repo_full_name']}:{result['branch']}:{today}",
        "event_type":  "branch_restriction_audit",
        "actor":       "bitbucket_scm_tool",
        "action":      "branch_restriction_audit",
        "resource":    f"{result['repo_full_name']}@{result['branch']}",
        "severity":    result["severity"],
        "raw_payload": {
            "branch_protection_violation": result["violated"] or bool(result["drift_events"]),
            "compliance": result["compliance"],
            "raw_restrictions": result["raw_restrictions"],
            "drift_events": result["drift_events"],
        },
    }]


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    try:
        result = _audit_once(base_url, credentials, extra_config)
        return True, f"Fetched branch restrictions for {result['repo_full_name']}@{result['branch']}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return True
