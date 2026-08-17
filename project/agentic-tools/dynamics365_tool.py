#!/usr/bin/env python3
"""
Microsoft Dynamics 365 / Dataverse Connector

Pulls audit records from Dataverse's built-in `audit` entity via the
OData v4 Web API, authenticated with Azure AD OAuth2 client-credentials.

Required per-connector config (set via the app UI — Dendrai UBO Configuration
screen — not env vars):
  base_url       org URL, e.g. "https://myorg.crm.dynamics.com"
  extra_config:  {"tenant_id": "<azure-ad-tenant-guid>"}
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


def _get_token(base_url: str, tenant_id: str, credentials: dict, timeout: int = 15) -> str:
    if not _HAS_REQUESTS:
        raise ImportError("requests library required: pip install requests")
    if not tenant_id:
        raise ValueError("Azure AD tenant_id (extra_config.tenant_id) is required")
    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": credentials.get("client_id"),
            "client_secret": credentials.get("client_secret"),
            "scope": f"{base_url.rstrip('/')}/.default",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    """Pull audit records since `since` via Dataverse's `audit` entity,
    normalized to the uniform connector event shape (event_id, event_type,
    actor, action, resource, severity, raw_payload)."""
    if not base_url:
        raise ValueError("Dynamics 365 org base_url is required")
    tenant_id = (extra_config or {}).get("tenant_id", "")
    token = _get_token(base_url, tenant_id, credentials)
    since_iso = (since or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = requests.get(
        f"{base_url.rstrip('/')}/api/data/v9.2/audits",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        },
        params={"$filter": f"createdon gt {since_iso}", "$top": 250},
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json().get("value", [])

    events = []
    for e in items:
        action = str(e.get("action") or e.get("operation") or "dataverse_audit")
        events.append({
            "event_id":    str(e.get("auditid") or ""),
            "event_type":  action,
            "actor":       str(e.get("_userid_value") or ""),
            "action":      action,
            "resource":    str(e.get("objecttypecode") or ""),
            "severity":    "INFO",
            "raw_payload": e,
        })
    return events


def get_journal_entries(base_url: Optional[str], credentials: dict, extra_config: dict,
                         since: Optional[datetime] = None, max_items: int = 500) -> dict:
    """Fetch posted general-journal lines via the Dynamics 365 Finance &
    Operations OData service (a different root — `/data/...` — than the
    Dataverse `audit` entity pull_events() above queries; F&O's GL entities
    live on the Finance & Operations environment, not the CDS/CRM one),
    normalized to the shared journal-entry shape je_testing_tool.py's rule
    engine consumes: je_id, amount, currency, account, gl_account_desc,
    description, preparer, approver, posted_at, period_close_date,
    source_system.

    Reuses the same Azure AD client-credentials token as pull_events() —
    F&O and Dataverse share the same Azure AD tenant, just a different
    resource scope (the F&O environment's own base_url)."""
    if not base_url:
        raise ValueError("Dynamics 365 org base_url is required")
    tenant_id = (extra_config or {}).get("tenant_id", "")
    try:
        token = _get_token(base_url, tenant_id, credentials)
        since_iso = (since or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = requests.get(
            f"{base_url.rstrip('/')}/data/GeneralJournalAccountEntries",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "OData-MaxVersion": "4.0",
                "OData-Version": "4.0",
            },
            params={"$filter": f"AccountingDate gt {since_iso}", "$top": min(max_items, 250)},
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("value", [])
    except Exception as exc:
        return {"error": str(exc), "journal_entries": [], "count": 0}

    normalized = []
    for e in items[:max_items]:
        normalized.append({
            "je_id":             str(e.get("JournalBatchNumber") or e.get("Voucher") or ""),
            "amount":            float(e.get("AmountInAccountingCurrency") or e.get("Amount") or 0),
            "currency":          str(e.get("CurrencyCode") or "USD"),
            "account":           str(e.get("MainAccountId") or e.get("LedgerAccount") or ""),
            "gl_account_desc":   str(e.get("MainAccountName") or ""),
            "description":       str(e.get("Text") or e.get("Description") or ""),
            "preparer":          str(e.get("CreatedBy") or e.get("EnteredBy") or ""),
            "approver":          (str(e.get("ApprovedBy")) if e.get("ApprovedBy") else None),
            "posted_at":         str(e.get("AccountingDate") or e.get("TransactionDate") or ""),
            "period_close_date": str(e.get("PeriodName") or "") or None,
            "source_system":     "DYNAMICS365",
        })

    return {
        "source":          "Dynamics 365 Finance & Operations — General Journal",
        "fetched_at":      datetime.now(timezone.utc).isoformat(),
        "count":           len(normalized),
        "journal_entries": normalized,
    }


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity by fetching an OAuth2 token for the org's scope."""
    try:
        if not base_url:
            return False, "Dynamics 365 org base_url is required"
        tenant_id = (extra_config or {}).get("tenant_id", "")
        _get_token(base_url, tenant_id, credentials)
        return True, "Azure AD client-credentials token fetch succeeded"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return _HAS_REQUESTS and bool(base_url)
