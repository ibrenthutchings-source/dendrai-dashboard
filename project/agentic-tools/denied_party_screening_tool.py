#!/usr/bin/env python3
"""
Denied-Party Screening Tool — Export Control / Trade Compliance

Screens vendor (supplier) and customer master records against the U.S.
government's Consolidated Screening List (CSL) — a single API that already
merges OFAC's Specially Designated Nationals (SDN) list, the Commerce
Department's BIS Entity List/Denied Persons List, and several other
restricted-party lists. Using the CSL API instead of separately downloading
and parsing each list's raw XML/CSV is both the realistic production choice
(this is what the API exists for) and avoids re-implementing list-merge/
dedup logic that api.trade.gov already handles.

Reference: https://developer.trade.gov/consolidated-screening-list.html
Request an API key at https://developer.trade.gov/ (free).

This replaces the previous "export control" signal (RSS-feed keyword
tagging on news articles) with an actual screening control against the
company's own vendor/customer master data — the RSS-based signal stays as
a separate, lower-confidence early-warning source; this is the real control.

Required environment variables (standalone/env-var mode only — UI-configured
connectors pass credentials/extra_config explicitly, see pull_events() below):
  TRADE_GOV_API_KEY       api.trade.gov API key (screening)
  ORACLE_FUSION_HOST      same Oracle Fusion tenant as oracle_fusion_tool.py
  ORACLE_FUSION_USERNAME / ORACLE_FUSION_PASSWORD (or CLIENT_ID/SECRET)

Optional:
  TRADE_COMPLIANCE_MATCH_THRESHOLD   CSL fuzzy match score 0-100, defaults to 85
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from oracle_fusion_tool import OracleFusionClient

logger = logging.getLogger(__name__)

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

_CSL_API_BASE = "https://data.trade.gov/consolidated_screening_list/v1/search"
_MATCH_SCORE_THRESHOLD = float(os.environ.get("TRADE_COMPLIANCE_MATCH_THRESHOLD", "85"))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────────────────────────────────────────────────────────────────
# Consolidated Screening List (api.trade.gov)
# ─────────────────────────────────────────────────────────────────────────────

def screen_name(name: str, api_key: str, timeout: int = 15) -> dict:
    """Query the CSL for one party name with fuzzy matching enabled.
    Returns {"matches": [{"name", "source", "score", "entity_number", "remarks"}, ...]}."""
    if not _HAS_REQUESTS:
        raise ImportError("requests library required: pip install requests")
    if not name or not name.strip():
        return {"matches": []}
    try:
        resp = requests.get(
            _CSL_API_BASE,
            params={"name": name, "fuzzy_name": "true", "size": 5},
            headers={"subscription-key": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"error": str(exc), "matches": []}

    matches = [
        {
            "name":          r.get("name", ""),
            "source":        r.get("source", ""),
            "score":         r.get("_score") or r.get("score") or 0,
            "entity_number": r.get("entity_number") or r.get("id") or "",
            "remarks":       r.get("remarks", ""),
        }
        for r in data.get("results", [])
    ]
    return {"query": name, "matches": matches}


# ─────────────────────────────────────────────────────────────────────────────
# Vendor / customer master data (Oracle Fusion FSCM)
# ─────────────────────────────────────────────────────────────────────────────

def get_supplier_master(max_items: int = 500, client: Optional[OracleFusionClient] = None) -> list[dict]:
    c = client or OracleFusionClient()
    items = c._get_all(c._fscm_url("suppliers"), params={"q": "Status='Active'"}, max_items=max_items)
    return [
        {
            "party_id":   s.get("SupplierId", s.get("supplierId", "")),
            "party_name": s.get("SupplierName", s.get("supplierName", "")),
            "party_type": "vendor",
            "country":    s.get("Country", s.get("country", "")),
        }
        for s in items if s.get("SupplierName") or s.get("supplierName")
    ]


def get_customer_master(max_items: int = 500, client: Optional[OracleFusionClient] = None) -> list[dict]:
    c = client or OracleFusionClient()
    items = c._get_all(c._fscm_url("customers"), params={"q": "Status='A'"}, max_items=max_items)
    return [
        {
            "party_id":   cust.get("CustomerId", cust.get("customerId", "")),
            "party_name": cust.get("CustomerName", cust.get("customerName", "")),
            "party_type": "customer",
            "country":    cust.get("Country", cust.get("country", "")),
        }
        for cust in items if cust.get("CustomerName") or cust.get("customerName")
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Screening pass
# ─────────────────────────────────────────────────────────────────────────────

def check_export_control_matches(
    api_key: str,
    threshold: float = _MATCH_SCORE_THRESHOLD,
    max_parties: int = 500,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """Screen the full active supplier + customer master against the CSL and
    return findings for any party scoring at or above threshold."""
    c = client or OracleFusionClient()

    try:
        parties = get_supplier_master(max_items=max_parties, client=c) + \
                  get_customer_master(max_items=max_parties, client=c)
    except Exception as exc:
        return {"error": str(exc), "findings": [], "count": 0}

    findings = []
    for p in parties:
        result = screen_name(p["party_name"], api_key)
        if result.get("error"):
            # A single lookup failure shouldn't abort the whole screening pass —
            # surfaced via logger, not raised, so one bad name doesn't hide
            # findings for every other party already screened this run.
            logger.warning("CSL screen failed for '%s': %s", p["party_name"], result["error"])
            continue
        best = max((m["score"] for m in result["matches"]), default=0)
        if best >= threshold:
            top = max(result["matches"], key=lambda m: m["score"])
            findings.append({
                "party_id":       p["party_id"],
                "party_name":     p["party_name"],
                "party_type":     p["party_type"],
                "matched_name":   top["name"],
                "match_score":    top["score"],
                "list_source":    top["source"],
                "entity_number":  top["entity_number"],
            })

    return {"source": "Consolidated Screening List — Vendor/Customer Master Screen",
            "fetched_at": _now(), "threshold": threshold,
            "parties_screened": len(parties), "count": len(findings), "findings": findings}


# ─────────────────────────────────────────────────────────────────────────────
# connector_poller adapter interface
# ─────────────────────────────────────────────────────────────────────────────

def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    """Full master-data re-screen every poll tick — a party can newly appear
    on the CSL at any time even if its master record hasn't changed, so
    `since` doesn't narrow this the way it does an audit-trail pull."""
    api_key = credentials.get("api_key") or os.environ.get("TRADE_GOV_API_KEY", "")
    if not api_key:
        raise ValueError("Trade.gov API key not configured (credentials.api_key or TRADE_GOV_API_KEY)")

    client = OracleFusionClient(
        host=base_url,
        username=credentials.get("username"),
        password=credentials.get("password"),
        client_id=credentials.get("client_id"),
        client_secret=credentials.get("client_secret"),
    )
    threshold = float((extra_config or {}).get("match_threshold") or _MATCH_SCORE_THRESHOLD)
    result = check_export_control_matches(api_key, threshold=threshold, client=client)
    if result.get("error"):
        raise RuntimeError(result["error"])

    today = datetime.now(timezone.utc).date().isoformat()
    return [
        {
            "event_id":   f"export-control:{f['party_id']}:{today}",
            "event_type": "export_control_match",
            "actor":      "denied_party_screening_tool",
            "action":     "csl_screening",
            "resource":   f"{f['party_type']}/{f['party_id']}",
            "severity":   "CRITICAL",
            "raw_payload": {
                "export_control_match": True,
                "trade_compliance_detail": {
                    "party_name":    f["party_name"],
                    "party_type":    f["party_type"],
                    "matched_name":  f["matched_name"],
                    "match_score":   f["match_score"],
                    "list_source":   f["list_source"],
                    "entity_number": f["entity_number"],
                },
            },
        }
        for f in result["findings"]
    ]


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify both the Oracle Fusion connection and the trade.gov API key
    with minimal real calls."""
    api_key = credentials.get("api_key") or os.environ.get("TRADE_GOV_API_KEY", "")
    if not api_key:
        return False, "Trade.gov API key not configured"
    try:
        result = screen_name("Test Screening Connectivity Check", api_key)
        if result.get("error"):
            return False, f"CSL API error: {result['error']}"
    except Exception as exc:
        return False, f"CSL API {type(exc).__name__}: {exc}"

    try:
        client = OracleFusionClient(
            host=base_url,
            username=credentials.get("username"),
            password=credentials.get("password"),
            client_id=credentials.get("client_id"),
            client_secret=credentials.get("client_secret"),
        )
        suppliers = get_supplier_master(max_items=1, client=client)
        return True, f"Connected — CSL API reachable, {len(suppliers)} supplier record(s) visible in test window"
    except Exception as exc:
        return False, f"Oracle Fusion {type(exc).__name__}: {exc}"
