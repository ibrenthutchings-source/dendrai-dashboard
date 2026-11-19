#!/usr/bin/env python3
"""
SailPoint Identity Security Cloud (IdentityNow) Connector

Pulls audit/access events from SailPoint's REST API v3 using OAuth2
client-credentials authentication.

Required per-connector config (set via the app UI — Dendrai UBO Configuration
screen — not env vars):
  base_url       tenant API host, e.g. "https://mycompany.api.identitynow.com"
  credentials:   {"client_id": ..., "client_secret": ...}

Connector adapter interface: pull_events(), test_connection().
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


def _get_token(base_url: str, credentials: dict, timeout: int = 15) -> str:
    if not _HAS_REQUESTS:
        raise ImportError("requests library required: pip install requests")
    resp = requests.post(
        f"{base_url.rstrip('/')}/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": credentials.get("client_id"),
            "client_secret": credentials.get("client_secret"),
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    """Pull audit events created since `since` via SailPoint's Events API,
    normalized to the uniform connector event shape (event_id, event_type,
    actor, action, resource, severity, raw_payload)."""
    if not base_url:
        raise ValueError("SailPoint tenant base_url is required")
    token = _get_token(base_url, credentials)
    since_iso = (since or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = requests.get(
        f"{base_url.rstrip('/')}/beta/events",
        headers={"Authorization": f"Bearer {token}"},
        params={"filters": f'created gt "{since_iso}"', "limit": 250},
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list):
        items = items.get("items", [])

    events = []
    for e in items:
        event_type = e.get("type") or e.get("action") or "sailpoint_event"
        events.append({
            "event_id":    str(e.get("id") or e.get("stackId") or ""),
            "event_type":  event_type,
            "actor":       (e.get("actor") or {}).get("name") or e.get("actorName") or "",
            "action":      e.get("action") or event_type,
            "resource":    (e.get("target") or {}).get("name") or e.get("objectName") or "",
            "severity":    "CRITICAL" if "SOD" in str(event_type).upper() else "INFO",
            "raw_payload": e,
        })
    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity by fetching an OAuth2 token — the same call
    pull_events() needs, without pulling any real events."""
    try:
        if not base_url:
            return False, "SailPoint tenant base_url is required"
        _get_token(base_url, credentials)
        return True, "OAuth2 client-credentials token fetch succeeded"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return _HAS_REQUESTS and bool(base_url)
