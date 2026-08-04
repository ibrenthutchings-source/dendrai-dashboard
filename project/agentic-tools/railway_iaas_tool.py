#!/usr/bin/env python3
"""
Railway platform/deployment drift poll-connector adapter
(observability.poll_connectors, connector_type='railway_iaas').

Scheduled by connector_poller.py alongside the other adapters — same
pull_events()/test_connection() contract. Unlike the Postgres CIS adapter
(one instance, one event per poll), a Railway environment typically has
several services — each poll produces one event per service instance, so
findings are attributed to the specific service that has them.

Two checks (matching the infrastructure_monitoring Rego's INFRA-006/INFRA-007
rules):
  - unexpected_public_domain: a service has a public domain that isn't in
    the connector's approved allow-list. A service quietly gaining a public
    domain is exactly the kind of change that should be noticed, not
    discovered later.
  - image_digest_mismatch: the currently-running deployment's image digest
    doesn't match ANY digest this platform has a pipeline attestation for
    (observability.pipeline_attestations, from evidence_endpoints.py's
    POST /evidence/attestation). Only evaluated once at least one
    attestation has been ingested — otherwise reported as unknown, never a
    fabricated finding (see iaas_connectors.normalize_railway_service_compliance).

Required per-connector config (set via the app UI, not env vars):
  credentials:  {"api_token": "<Railway account or project API token>"}
  extra_config: {
    "environment_id": "<Railway environment id>",
    "approved_public_service_ids": "svc-id-1,svc-id-2"  (comma-separated, optional)
  }

The API token should be a real Railway Account/Team API token (generated via
the Railway dashboard's Account Settings -> Tokens), NOT a CLI OAuth session
token — those are short-lived and not meant for long-running automation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import db
import iaas_connectors

logger = logging.getLogger(__name__)


def _known_image_digests() -> set:
    if not db.is_available():
        return set()
    rows = db.list_pipeline_attestations(limit=500)
    return {r["container_image_sha"] for r in rows if r.get("container_image_sha")}


def _audit_once(credentials: dict, extra_config: dict) -> list[dict]:
    extra_config = extra_config or {}
    api_token = (credentials or {}).get("api_token")
    environment_id = extra_config.get("environment_id")
    if not api_token:
        raise ValueError("credentials.api_token is required (a Railway API token)")
    if not environment_id:
        raise ValueError("extra_config.environment_id is required")

    approved_raw = extra_config.get("approved_public_service_ids") or ""
    approved_ids = {s.strip() for s in approved_raw.split(",") if s.strip()}
    known_digests = _known_image_digests()

    nodes = iaas_connectors.fetch_railway_environment(api_token, environment_id)
    results = []
    for node in nodes:
        compliance = iaas_connectors.normalize_railway_service_compliance(node, approved_ids, known_digests)
        severity = iaas_connectors.evaluate_railway_severity(compliance)
        violated = severity in ("CRITICAL", "HIGH")
        results.append({"compliance": compliance, "severity": severity, "violated": violated})
    return results


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    """One audit event per service instance per poll tick — point-in-time
    configuration check, so `since` is unused, same as postgres_cis_tool.py."""
    audits = _audit_once(credentials, extra_config)
    today = datetime.now(timezone.utc).date().isoformat()
    events = []
    for a in audits:
        c = a["compliance"]
        resource = c.get("service_name") or c.get("service_id") or "unknown-service"
        events.append({
            "event_id":    f"railway-iaas:{c.get('service_id')}:{today}",
            "event_type":  "infrastructure_finding",
            "actor":       "railway_iaas_tool",
            "action":      "railway_config_audit",
            "resource":    resource,
            "severity":    a["severity"],
            "raw_payload": {
                "infrastructure_finding": a["violated"],
                "check_id": "railway-iaas-v1",
                "infra_compliance": c,
            },
        })
    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity by fetching the environment's service list — the
    same call pull_events() needs."""
    try:
        audits = _audit_once(credentials, extra_config)
        return True, f"Fetched {len(audits)} service instance(s)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return iaas_connectors._HAS_REQUESTS
