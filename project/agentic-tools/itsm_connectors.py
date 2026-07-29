#!/usr/bin/env python3
"""
ITSM/Jira-ServiceNow SLA Bridge — pure REST calls and normalization.

No DB, no FastAPI — mirrors scm_connectors.py's shape exactly. Two systems,
one normalized status vocabulary (open | in_progress | resolved | closed |
cancelled) so itsm_endpoints.py and the sweep never branch on which system a
ticket lives in once they have its normalized status.

Auth scope (v1): Jira API token (basic auth) and ServiceNow basic auth or
OAuth2 client-credentials — the same "credential blob, no persisted refresh
token" shape every other poll connector in this codebase uses (see
dynamics365_tool.py's Azure AD client-credentials fetch for the closest
precedent). Full 3-legged/user-delegated OAuth (Jira Cloud 3LO) would need a
persisted refresh_token + a background refresh task with no existing
precedent anywhere in this codebase — out of scope for v1.
"""

from __future__ import annotations

import base64
from typing import Optional

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# Simple severity -> remediation-SLA-hours mapping. Matches the spec
# thresholds already encoded in the devops_monitoring Rego module's
# DEVOPS-007/008 comments (7-day CRITICAL / 30-day HIGH for SARIF findings) —
# expressed here in hours since sla_due_at math needs a duration, not a rule.
_SLA_HOURS_BY_SEVERITY = {
    "CRITICAL": 48,     # ITSM SLA bridge is stricter than the 7-day SARIF
    "HIGH":     168,    # remediation window — a *ticket* not moving in 48h/7d
    "MEDIUM":   240,    # is itself a signal, independent of the finding's own
    "LOW":      720,    # remediation clock.
}

_TERMINAL_STATUSES = {"closed", "cancelled"}


def sla_hours_for_severity(severity: str) -> int:
    return _SLA_HOURS_BY_SEVERITY.get(str(severity or "").upper(), _SLA_HOURS_BY_SEVERITY["MEDIUM"])


def _require_requests():
    if not _HAS_REQUESTS:
        raise ImportError("requests library required: pip install requests")


# ── Jira Cloud/Server REST v3 ─────────────────────────────────────────────────

def _jira_auth_header(email: str, api_token: str) -> dict:
    token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json", "Accept": "application/json"}


def jira_create_issue(base_url: str, email: str, api_token: str, project_key: str,
                       summary: str, description: str, issue_type: str = "Bug",
                       timeout: int = 15) -> dict:
    """Returns {key, id, status}."""
    _require_requests()
    resp = requests.post(
        f"{base_url.rstrip('/')}/rest/api/3/issue",
        headers=_jira_auth_header(email, api_token),
        json={"fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": description or summary}]},
            ]},
            "issuetype": {"name": issue_type},
        }},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    return {"key": body["key"], "id": body["id"], "status": "open"}


def jira_get_issue_status(base_url: str, email: str, api_token: str, issue_key: str,
                           timeout: int = 15) -> str:
    _require_requests()
    resp = requests.get(
        f"{base_url.rstrip('/')}/rest/api/3/issue/{issue_key}",
        headers=_jira_auth_header(email, api_token),
        params={"fields": "status"},
        timeout=timeout,
    )
    resp.raise_for_status()
    raw_status = ((resp.json().get("fields") or {}).get("status") or {}).get("name", "")
    return normalize_status("jira", raw_status)


# ── ServiceNow Table API ──────────────────────────────────────────────────────

def _servicenow_auth(username: Optional[str], password: Optional[str]) -> tuple:
    return (username or "", password or "")


def servicenow_create_incident(base_url: str, username: str, password: str,
                                short_description: str, description: str,
                                urgency: str = "2", timeout: int = 15) -> dict:
    """Returns {key (number), id (sys_id), status}."""
    _require_requests()
    resp = requests.post(
        f"{base_url.rstrip('/')}/api/now/table/incident",
        auth=_servicenow_auth(username, password),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        json={"short_description": short_description, "description": description or short_description,
              "urgency": urgency},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json().get("result", {})
    return {"key": body.get("number"), "id": body.get("sys_id"), "status": "open"}


def servicenow_get_incident_status(base_url: str, username: str, password: str, sys_id: str,
                                    timeout: int = 15) -> str:
    _require_requests()
    resp = requests.get(
        f"{base_url.rstrip('/')}/api/now/table/incident/{sys_id}",
        auth=_servicenow_auth(username, password),
        headers={"Accept": "application/json"},
        params={"sysparm_fields": "state"},
        timeout=timeout,
    )
    resp.raise_for_status()
    raw_state = (resp.json().get("result", {}) or {}).get("state", "")
    return normalize_status("servicenow", raw_state)


# ── Status normalization ──────────────────────────────────────────────────────
# Jira workflow names and ServiceNow numeric `state` codes both vary by
# instance customization — this covers the common defaults; anything
# unrecognized falls back to "in_progress" (safer than silently marking an
# unfamiliar status as resolved and stopping the SLA clock early).

_JIRA_STATUS_MAP = {
    "to do": "open", "open": "open", "backlog": "open",
    "in progress": "in_progress", "in review": "in_progress",
    "done": "resolved", "resolved": "resolved",
    "closed": "closed", "cancelled": "cancelled", "canceled": "cancelled",
}

# ServiceNow incident.state defaults: 1=New 2=In Progress 3=On Hold 6=Resolved 7=Closed 8=Canceled
_SERVICENOW_STATE_MAP = {
    "1": "open", "2": "in_progress", "3": "in_progress",
    "6": "resolved", "7": "closed", "8": "cancelled",
}


def normalize_status(external_system: str, raw_status: str) -> str:
    raw = str(raw_status or "").strip().lower()
    if external_system == "jira":
        return _JIRA_STATUS_MAP.get(raw, "in_progress")
    if external_system == "servicenow":
        return _SERVICENOW_STATE_MAP.get(raw, "in_progress")
    return "in_progress"


def is_terminal_status(status: str) -> bool:
    return str(status or "").lower() in _TERMINAL_STATUSES
