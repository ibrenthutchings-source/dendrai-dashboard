#!/usr/bin/env python3
"""
Postgres CIS-style hardening audit poll-connector adapter
(observability.poll_connectors, connector_type='postgres_cis').

Scheduled by connector_poller.py alongside the other adapters — same
pull_events()/test_connection() contract. Like github_scm_tool.py, each poll
re-audits the target instance's *current* configuration state (SSL
enforcement, password encryption, superuser count, live unencrypted
connections, connection logging) — a point-in-time check, not a log of new
events, so `since` is unused.

Required per-connector config (set via the app UI, not env vars):
  credentials:  {"dsn": "postgresql://readonly_user:...@host:port/dbname?sslmode=require"}
  extra_config: {"resource_label": "primary-db"}  (optional, defaults to "postgres")

The DSN's user should be a read-only role (pg_read_all_settings/pg_monitor,
NOT superuser) — this adapter only ever runs SELECT/SHOW statements
(iaas_connectors.fetch_postgres_config), but a least-privilege credential is
still the right default to hand out for an audit connector.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import iaas_connectors

logger = logging.getLogger(__name__)


def _audit_once(credentials: dict, extra_config: dict) -> dict:
    extra_config = extra_config or {}
    dsn = (credentials or {}).get("dsn")
    resource_label = extra_config.get("resource_label") or "postgres"
    if not dsn:
        raise ValueError("credentials.dsn is required (a Postgres connection string)")

    raw = iaas_connectors.fetch_postgres_config(dsn)
    compliance = iaas_connectors.normalize_postgres_compliance(raw)
    severity = iaas_connectors.evaluate_severity(compliance)
    # MEDIUM now covers the version-currency check (evaluate_severity) —
    # included here so an outdated-version finding still sets
    # infrastructure_finding=True and reaches _detect_system_flags, not just
    # the CRITICAL/HIGH config gaps that predate that check.
    violated = severity in ("CRITICAL", "HIGH", "MEDIUM")

    return {
        "resource_label": resource_label,
        "compliance": compliance,
        "raw": raw,
        "violated": violated,
        "severity": severity,
    }


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    """One audit event per poll tick, normalized to the uniform connector
    event shape (event_id, event_type, actor, action, resource, severity,
    raw_payload) per connector_poller.py's documented contract."""
    result = _audit_once(credentials, extra_config)
    today = datetime.now(timezone.utc).date().isoformat()
    return [{
        "event_id":    f"postgres-cis:{result['resource_label']}:{today}",
        "event_type":  "infrastructure_finding",
        "actor":       "postgres_cis_tool",
        "action":      "db_config_audit",
        "resource":    result["resource_label"],
        "severity":    result["severity"],
        "raw_payload": {
            "infrastructure_finding": result["violated"],
            "check_id": "postgres-cis-v1",
            "infra_compliance": result["compliance"],
            "raw": result["raw"],
        },
    }]


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity by running one real audit — the same call
    pull_events() needs."""
    try:
        result = _audit_once(credentials, extra_config)
        return True, f"Connected to '{result['resource_label']}' — severity: {result['severity']}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return iaas_connectors._HAS_PSYCOPG2
