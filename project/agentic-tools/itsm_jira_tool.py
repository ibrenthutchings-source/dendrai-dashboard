#!/usr/bin/env python3
"""
Jira Cloud/Server poll-connector adapter (observability.poll_connectors,
connector_type='itsm_jira').

Scheduled by connector_poller.py alongside the other adapters — same
pull_events()/test_connection() contract. Unlike github_scm_tool.py (one
audit of one repo per poll), this adapter reconciles the status of every
still-open observability.itsm_tickets row tied to Jira: a human closing a
ticket in Jira's own UI needs to be reflected here without waiting for
itsm_sla_sweep.py's hourly tick, since that sweep's only job is SLA-breach
detection, not status sync (see its module docstring).

Required per-connector config (set via the app UI, not env vars):
  base_url:     Jira base URL, e.g. "https://mycompany.atlassian.net"
  credentials:  {"email": "...", "api_token": "..."}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import db
import itsm_connectors

logger = logging.getLogger(__name__)


def _reconcile_once(base_url: str, credentials: dict) -> list[dict]:
    email = (credentials or {}).get("email")
    api_token = (credentials or {}).get("api_token")
    if not base_url:
        raise ValueError("Jira base_url is required")
    if not email or not api_token:
        raise ValueError("credentials.email and credentials.api_token are required")

    open_tickets = [t for t in db.list_itsm_tickets(external_system="jira", limit=500)
                    if not itsm_connectors.is_terminal_status(t["status"])]

    events = []
    for ticket in open_tickets:
        try:
            new_status = itsm_connectors.jira_get_issue_status(
                base_url, email, api_token, ticket["external_ticket_key"])
        except Exception as exc:
            logger.warning("itsm_jira_tool: status check failed for %s: %s",
                            ticket["external_ticket_key"], exc)
            continue
        if new_status == ticket["status"]:
            continue
        db.update_itsm_ticket_status(ticket["id"], new_status)
        events.append({
            "event_id":    f"itsm-jira-status:{ticket['id']}:{new_status}:{datetime.now(timezone.utc).date().isoformat()}",
            "event_type":  "itsm_ticket_status_change",
            "actor":       "itsm_jira_tool",
            "action":      "itsm_ticket_status_change",
            "resource":    ticket["external_ticket_key"],
            "severity":    "INFO",
            "raw_payload": {
                "ticket_id": ticket["id"], "finding_hash": ticket["finding_hash"],
                "external_system": "jira", "external_ticket_key": ticket["external_ticket_key"],
                "previous_status": ticket["status"], "new_status": new_status,
            },
        })
    return events


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    return _reconcile_once(base_url, credentials or {})


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity via Jira's /myself endpoint — cheapest authenticated call."""
    try:
        import requests
        email = (credentials or {}).get("email")
        api_token = (credentials or {}).get("api_token")
        if not base_url:
            return False, "Jira base_url is required"
        if not email or not api_token:
            return False, "credentials.email and credentials.api_token are required"
        resp = requests.get(f"{base_url.rstrip('/')}/rest/api/3/myself",
                             headers=itsm_connectors._jira_auth_header(email, api_token), timeout=15)
        resp.raise_for_status()
        who = resp.json().get("displayName", "unknown")
        return True, f"Authenticated to Jira as {who}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return bool(base_url)
