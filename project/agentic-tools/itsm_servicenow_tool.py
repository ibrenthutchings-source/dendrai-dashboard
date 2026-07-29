#!/usr/bin/env python3
"""
ServiceNow poll-connector adapter (observability.poll_connectors,
connector_type='itsm_servicenow'). Mirrors itsm_jira_tool.py exactly — see
its module docstring for why this reconciles status instead of pulling a
log of new events.

Required per-connector config (set via the app UI, not env vars):
  base_url:     ServiceNow instance URL, e.g. "https://mycompany.service-now.com"
  credentials:  {"username": "...", "password": "..."}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import db
import itsm_connectors

logger = logging.getLogger(__name__)


def _reconcile_once(base_url: str, credentials: dict) -> list[dict]:
    username = (credentials or {}).get("username")
    password = (credentials or {}).get("password")
    if not base_url:
        raise ValueError("ServiceNow base_url is required")
    if not username or not password:
        raise ValueError("credentials.username and credentials.password are required")

    open_tickets = [t for t in db.list_itsm_tickets(external_system="servicenow", limit=500)
                    if not itsm_connectors.is_terminal_status(t["status"])]

    events = []
    for ticket in open_tickets:
        # sys_id is stored in extra state at creation time — external_ticket_key
        # here is the human-facing number (e.g. 'INC0012345'); the connector
        # needs the sys_id for the Table API path, resolved via a quick lookup.
        try:
            sys_id = _resolve_sys_id(base_url, username, password, ticket["external_ticket_key"])
            if not sys_id:
                continue
            new_status = itsm_connectors.servicenow_get_incident_status(base_url, username, password, sys_id)
        except Exception as exc:
            logger.warning("itsm_servicenow_tool: status check failed for %s: %s",
                            ticket["external_ticket_key"], exc)
            continue
        if new_status == ticket["status"]:
            continue
        db.update_itsm_ticket_status(ticket["id"], new_status)
        events.append({
            "event_id":    f"itsm-snow-status:{ticket['id']}:{new_status}:{datetime.now(timezone.utc).date().isoformat()}",
            "event_type":  "itsm_ticket_status_change",
            "actor":       "itsm_servicenow_tool",
            "action":      "itsm_ticket_status_change",
            "resource":    ticket["external_ticket_key"],
            "severity":    "INFO",
            "raw_payload": {
                "ticket_id": ticket["id"], "finding_hash": ticket["finding_hash"],
                "external_system": "servicenow", "external_ticket_key": ticket["external_ticket_key"],
                "previous_status": ticket["status"], "new_status": new_status,
            },
        })
    return events


def _resolve_sys_id(base_url: str, username: str, password: str, number: str, timeout: int = 15) -> Optional[str]:
    import requests
    resp = requests.get(
        f"{base_url.rstrip('/')}/api/now/table/incident",
        auth=(username, password), headers={"Accept": "application/json"},
        params={"sysparm_query": f"number={number}", "sysparm_fields": "sys_id", "sysparm_limit": 1},
        timeout=timeout,
    )
    resp.raise_for_status()
    results = resp.json().get("result", [])
    return results[0]["sys_id"] if results else None


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    return _reconcile_once(base_url, credentials or {})


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity via a minimal, harmless Table API read."""
    try:
        import requests
        username = (credentials or {}).get("username")
        password = (credentials or {}).get("password")
        if not base_url:
            return False, "ServiceNow base_url is required"
        if not username or not password:
            return False, "credentials.username and credentials.password are required"
        resp = requests.get(
            f"{base_url.rstrip('/')}/api/now/table/incident",
            auth=(username, password), headers={"Accept": "application/json"},
            params={"sysparm_limit": 1, "sysparm_fields": "sys_id"},
            timeout=15,
        )
        resp.raise_for_status()
        return True, "ServiceNow Table API authentication succeeded"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return bool(base_url)
