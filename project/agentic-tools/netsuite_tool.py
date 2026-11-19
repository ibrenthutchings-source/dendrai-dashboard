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


def get_journal_entries(base_url: Optional[str], credentials: dict, extra_config: dict,
                         since: Optional[datetime] = None, max_items: int = 500) -> dict:
    """Fetch posted journal entries (transaction type 'Journal') via SuiteQL,
    joined to their debit line for an amount/account, normalized to the
    shared journal-entry shape je_testing_tool.py's rule engine consumes:
    je_id, amount, currency, account, gl_account_desc, description, preparer,
    approver, posted_at, period_close_date, source_system.

    One row per JE header (the debit line, arbitrarily — a full multi-line
    JE reconciliation is out of scope for anomaly testing, which only needs
    a representative amount/account per entry) rather than one row per
    transactionline, since je_testing_tool.py's rules operate at JE-header
    granularity same as pac_endpoints.py's record_to_report Rego does."""
    account_id = (extra_config or {}).get("account_id", "")
    since_str = (since or datetime.now(timezone.utc)).strftime("%m/%d/%Y")
    query = (
        "SELECT t.id AS je_id, t.trandate, t.memo, "
        "BUILTIN.DF(t.createdby) AS preparer, BUILTIN.DF(t.approvedby) AS approver, "
        "t.postingperiod, tl.account, BUILTIN.DF(tl.account) AS account_desc, tl.debit "
        "FROM transaction t JOIN transactionline tl ON tl.transaction = t.id "
        "WHERE t.type = 'Journal' AND tl.debit > 0 "
        f"AND t.trandate >= TO_DATE('{since_str}', 'MM/DD/YYYY') "
        f"ORDER BY t.trandate FETCH FIRST {max_items} ROWS ONLY"
    )
    try:
        data = _suiteql(base_url, account_id, credentials, query)
    except Exception as exc:
        return {"error": str(exc), "journal_entries": [], "count": 0}
    items = data.get("items", [])

    normalized = []
    for r in items:
        normalized.append({
            "je_id":             str(r.get("je_id") or ""),
            "amount":            float(r.get("debit") or 0),
            "currency":          "USD",
            "account":           str(r.get("account") or ""),
            "gl_account_desc":   str(r.get("account_desc") or ""),
            "description":       str(r.get("memo") or ""),
            "preparer":          str(r.get("preparer") or ""),
            "approver":          (str(r.get("approver")) if r.get("approver") else None),
            "posted_at":         str(r.get("trandate") or ""),
            "period_close_date": str(r.get("postingperiod") or "") or None,
            "source_system":     "NETSUITE",
        })

    return {
        "source":          "NetSuite — Journal Entries",
        "fetched_at":      datetime.now(timezone.utc).isoformat(),
        "count":           len(normalized),
        "journal_entries": normalized,
    }


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
