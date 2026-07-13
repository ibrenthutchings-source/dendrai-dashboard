#!/usr/bin/env python3
"""
NetSuite SuiteQL Connector

Pulls system audit records (SystemNote) from NetSuite via SuiteQL over
REST, authenticated with NetSuite's Token-Based Authentication (TBA) — an
OAuth 1.0a variant using HMAC-SHA256 request signing with a consumer
key/secret + token id/secret. NetSuite does not support OAuth2
client-credentials for this style of machine-to-machine access, so the
signing is hand-rolled here against the standard OAuth 1.0a algorithm
(stdlib hmac/hashlib only — no extra dependency).

Required per-connector config (set via the app UI — Dendrai UBO Configuration
screen — not env vars):
  base_url       e.g. "https://ACCOUNTID.suitetalk.api.netsuite.com"
  extra_config:  {"account_id": "1234567"}  (or "1234567_SB1" for a sandbox)
  credentials:   {"consumer_key": ..., "consumer_secret": ...,
                   "token_id": ..., "token_secret": ...}

Connector adapter interface: pull_events(), test_connection().
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


def _oauth1_header(method: str, url: str, account_id: str, credentials: dict) -> str:
    """Build a NetSuite TBA (OAuth 1.0a HMAC-SHA256) Authorization header.
    Standard OAuth 1.0a signing, plus NetSuite's realm=<account_id> addition."""
    oauth_params = {
        "oauth_consumer_key":     credentials.get("consumer_key", ""),
        "oauth_token":            credentials.get("token_id", ""),
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp":        str(int(time.time())),
        "oauth_nonce":            uuid.uuid4().hex,
        "oauth_version":          "1.0",
    }
    base_url = url.split("?")[0]
    sorted_params = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(oauth_params.items()))
    base_string = "&".join([method.upper(), quote(base_url, safe=""), quote(sorted_params, safe="")])
    signing_key = (
        f"{quote(credentials.get('consumer_secret', ''), safe='')}&"
        f"{quote(credentials.get('token_secret', ''), safe='')}"
    )
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha256).digest()
    ).decode()
    oauth_params["oauth_signature"] = signature
    header_params = ", ".join(f'{k}="{quote(v, safe="")}"' for k, v in oauth_params.items())
    return f'OAuth realm="{account_id}", {header_params}'


def _suiteql(base_url: str, account_id: str, credentials: dict, query: str, timeout: int = 30) -> dict:
    if not _HAS_REQUESTS:
        raise ImportError("requests library required: pip install requests")
    if not base_url:
        raise ValueError("NetSuite base_url is required")
    url = f"{base_url.rstrip('/')}/services/rest/query/v1/suiteql"
    auth_header = _oauth1_header("POST", url, account_id, credentials)
    resp = requests.post(
        url,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json",
            "Prefer": "transient",
        },
        json={"q": query},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    """Query SystemNote for records since `since` via SuiteQL, normalized to
    the uniform connector event shape (event_id, event_type, actor, action,
    resource, severity, raw_payload)."""
    account_id = (extra_config or {}).get("account_id", "")
    since_str = (since or datetime.now(timezone.utc)).strftime("%m/%d/%Y")
    query = (
        "SELECT id, date, name, type, field, context, recordtypename "
        "FROM SystemNote "
        f"WHERE date >= TO_DATE('{since_str}', 'MM/DD/YYYY') "
        "ORDER BY date"
    )
    data = _suiteql(base_url, account_id, credentials, query)
    items = data.get("items", [])

    events = []
    for r in items:
        note_type = str(r.get("type") or "netsuite_system_note")
        events.append({
            "event_id":    str(r.get("id") or ""),
            "event_type":  note_type,
            "actor":       str(r.get("name") or ""),
            "action":      note_type,
            "resource":    str(r.get("recordtypename") or r.get("field") or ""),
            "severity":    "INFO",
            "raw_payload": r,
        })
    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity/signing with a trivial SuiteQL query."""
    try:
        account_id = (extra_config or {}).get("account_id", "")
        data = _suiteql(base_url, account_id, credentials, "SELECT 1 AS ok FROM DUAL")
        return True, f"Connected — SuiteQL test query returned {len(data.get('items', []))} row(s)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return _HAS_REQUESTS and bool(base_url)
