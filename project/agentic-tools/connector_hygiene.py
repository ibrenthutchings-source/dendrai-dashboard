#!/usr/bin/env python3
"""
Connector credential rotation hygiene — Infrastructure Monitoring dogfooded
on Intelligenza's own credential store, not just external audit targets.

The other Infrastructure Monitoring producers (postgres_cis_tool.py,
railway_iaas_tool.py) audit systems Intelligenza connects TO. This module
audits a property of Intelligenza's OWN observability.poll_connectors table:
how long each stored credential (Oracle Fusion, SAP HANA, GitHub/GitLab PAT,
Postgres DSN, Railway token, ...) has gone unrotated. An old, never-rotated
credential is a real control gap (SOC 2 CC6.1/CC6.3-style access management)
even when nothing else about the connector looks wrong — this is the one
Infrastructure Monitoring check with no external system to poll at all; the
"system" being checked is Intelligenza itself.

Reuses the existing INFRASTRUCTURE_FINDING EventType end-to-end (no new
EventType, no new PaC process, no new Silver rule needed — POL-INFRA-001
already escalates any INFRASTRUCTURE_FINDING at CRITICAL/HIGH severity
regardless of which specific check produced it).
"""

from __future__ import annotations

import db

DEFAULT_STALE_DAYS = 90


def normalize_connector_hygiene(stale_connectors: list[dict]) -> dict:
    """Same normalize_*_compliance idiom as iaas_connectors.py — the shape
    the infrastructure_monitoring Rego module's INFRA-008 rule and Silver's
    (reused, unmodified) POL-INFRA-001 rule read as input.event.*."""
    return {
        "stale_connector_count": len(stale_connectors),
        "oldest_credential_age_days": max(
            (c.get("credential_age_days") or 0) for c in stale_connectors
        ) if stale_connectors else 0,
        "stale_connectors": [
            {
                "id": c.get("id"),
                "display_name": c.get("display_name"),
                "connector_type": c.get("connector_type"),
                "credential_age_days": c.get("credential_age_days"),
            }
            for c in stale_connectors
        ],
    }


def evaluate_connector_hygiene_severity(compliance: dict) -> str:
    """HIGH (not CRITICAL) — an unrotated-but-otherwise-unexploited
    credential is a hardening gap, same tier as INFRA-002/003's weak
    password hashing / superuser sprawl, not an active live exposure like
    SSL-disabled (INFRA-001, CRITICAL)."""
    return "HIGH" if compliance.get("stale_connector_count", 0) > 0 else "INFO"


def check_connector_credential_rotation(stale_days: int = DEFAULT_STALE_DAYS) -> dict:
    """Runs the real query against Intelligenza's own connector store.
    Returns {"compliance", "severity", "violated"} — same result shape
    every other Infrastructure Monitoring producer returns before its
    caller decides whether to adjudicate."""
    stale = db.list_connectors_with_stale_credentials(stale_days=stale_days) if db.is_available() else []
    compliance = normalize_connector_hygiene(stale)
    severity = evaluate_connector_hygiene_severity(compliance)
    return {"compliance": compliance, "severity": severity, "violated": severity != "INFO"}
