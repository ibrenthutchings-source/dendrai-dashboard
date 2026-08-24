#!/usr/bin/env python3
"""
Infrastructure Vulnerability & Currency Posture, Phase 1 — asset inventory
sync + credential/certificate expiry sweep. Development environment only
(see deploy_env.py; api_server.py gates this sweep's task creation on
deploy_env.IS_DEVELOPMENT, not a router 404 — there is no HTTP request to
404 for a background loop).

Two responsibilities, both daily:

1. **Asset sync** — calls postgres_cis_tool/tls_cert_tool's own
   _audit_once() directly (not through connector_poller, which already runs
   these on its own schedule purely for the system_telemetry Infrastructure
   Posture matrix) and upserts observability.infra_assets rows, then stamps
   last_assessed_at via db.mark_infra_asset_assessed(). This is what keeps
   "never assessed" from being permanent for any asset with a real check
   behind it.

2. **Expiry findings** — db.list_expiring_credentials() (poll_connectors'
   own credentials_expires_at) and db.list_expiring_infra_assets()
   (certificates, and any future asset_type with an expires_at) inside
   INFRA_EXPIRY_WARN_DAYS (default 30) or already past it. Raised with a
   day-scoped event_id (f"asset-expiry:{key}:{today}"), the same "re-emit
   current state every tick, not just on transition" idiom
   postgres_cis_tool.py/tls_cert_tool.py themselves use — deliberately NOT
   the once-only CURRENT->EXPIRED status-flip pattern risk_waiver_sweep.py/
   ai_governance_sweep.py use, since an asset here has no separate status
   column to flip; ON CONFLICT (server_name, event_id) DO NOTHING already
   makes a same-day re-tick a safe no-op, and a real day-over-day change in
   days-to-expiry is worth a fresh row for the history view.

Mirrors vendor_risk_sweep.py's shape exactly: infinite loop, errors caught
and logged, never exits on its own except cancellation. Started as an
asyncio task in api_server.py's lifespan alongside the other background
loops, gated on deploy_env.IS_DEVELOPMENT.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import aws_patch_tool
import db
import mcp_governance
import postgres_cis_tool
import tls_cert_tool

logger = logging.getLogger(__name__)

_TICK_S = 86400  # daily
_WARN_DAYS = int(os.environ.get("INFRA_EXPIRY_WARN_DAYS", "30"))

_ASSET_SYNC_TYPES = ("postgres_cis", "tls_cert", "aws_patch")


async def _sync_postgres_asset(connector: dict) -> bool:
    extra_config = connector.get("extra_config") or {}
    resource_label = extra_config.get("resource_label") or "postgres"
    try:
        result = await asyncio.to_thread(postgres_cis_tool._audit_once, connector["credentials"], extra_config)
    except Exception as exc:
        logger.warning("infra_asset_sweep: postgres audit failed for connector %s: %s", connector["id"], exc)
        return False
    compliance = result["compliance"]
    asset_key = f"postgres:{resource_label}"
    await asyncio.to_thread(
        db.upsert_infra_asset,
        asset_key, "database", resource_label, connector["id"],
        "default", None, None, None,
        "postgresql", compliance.get("server_version"), None, None, None, None,
        "connector", {"version_current": compliance.get("version_current"),
                       "latest_known_version": compliance.get("latest_known_version")},
    )
    await asyncio.to_thread(db.mark_infra_asset_assessed, asset_key, "postgres_cis")
    return True


async def _sync_tls_cert_assets(connector: dict) -> int:
    extra_config = connector.get("extra_config") or {}
    try:
        audit = await asyncio.to_thread(tls_cert_tool._audit_once, connector.get("credentials") or {}, extra_config)
    except Exception as exc:
        logger.warning("infra_asset_sweep: tls_cert audit failed for connector %s: %s", connector["id"], exc)
        return 0
    synced = 0
    for c in audit["checks"]:
        asset_key = f"cert:{c['host']}:{c['port']}"
        expires_at = c["not_after"]  # ISO string or None — upsert_infra_asset passes it straight to a TIMESTAMPTZ column, psycopg2 parses ISO 8601 directly
        await asyncio.to_thread(
            db.upsert_infra_asset,
            asset_key, "certificate", c["name"], connector["id"],
            "default", None, None, None,
            None, None, None, None, None, expires_at,
            "connector", {"subject": c["subject"], "issuer": c["issuer"], "reachable": c["reachable"], "error": c["error"]},
        )
        if c["reachable"]:
            await asyncio.to_thread(db.mark_infra_asset_assessed, asset_key, "tls_cert")
        synced += 1
    return synced


async def _sync_aws_patch_assets(connector: dict) -> int:
    """Phase 3: each SSM-managed EC2 instance becomes a 'host' asset, with
    patch-state counts in metadata and last_assessed_at stamped — real OS
    patch data, distinct from aws_iaas_tool's config-drift checks."""
    extra_config = connector.get("extra_config") or {}
    try:
        rows = await asyncio.to_thread(aws_patch_tool._audit_once, connector.get("credentials") or {}, extra_config)
    except Exception as exc:
        logger.warning("infra_asset_sweep: aws_patch audit failed for connector %s: %s", connector["id"], exc)
        return 0
    synced = 0
    for r in rows:
        asset_key = f"ec2:{r['instance_id']}:{r['region']}"
        await asyncio.to_thread(
            db.upsert_infra_asset,
            asset_key, "host", r["instance_id"], connector["id"],
            "default", None, r.get("os"), None, None, None,
            None, None, r["region"], None,
            "connector", {"installed_count": r["installed_count"], "missing_count": r["missing_count"],
                           "failed_count": r["failed_count"], "patch_group": r.get("patch_group")},
        )
        await asyncio.to_thread(db.mark_infra_asset_assessed, asset_key, "aws_patch")
        synced += 1
    return synced


async def _sync_assets() -> int:
    if not db.is_available():
        return 0
    connectors = await asyncio.to_thread(db.list_poll_connectors, True)
    synced = 0
    for c in connectors:
        if not c.get("active") or c["connector_type"] not in _ASSET_SYNC_TYPES:
            continue
        try:
            if c["connector_type"] == "postgres_cis":
                if await _sync_postgres_asset(c):
                    synced += 1
            elif c["connector_type"] == "tls_cert":
                synced += await _sync_tls_cert_assets(c)
            elif c["connector_type"] == "aws_patch":
                synced += await _sync_aws_patch_assets(c)
        except Exception as exc:
            logger.warning("infra_asset_sweep: asset sync failed for connector %s (%s): %s",
                            c.get("id"), c.get("connector_type"), exc)
    return synced


async def _raise_expiry_finding(resource: str, kind: str, expires_at: str, detail: dict, severity: str) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    flags = await asyncio.to_thread(mcp_governance._detect_system_flags, {
        "action": f"{kind}_expiry_check", "resource": resource, "severity": severity,
        "payload": {"infrastructure_finding": True},
    })
    await asyncio.to_thread(
        mcp_governance._ingest_system_event,
        "infra-asset-sweep", "infra_posture", "infrastructure_finding",
        f"asset-expiry:{kind}:{resource}:{today}",
        None, f"{kind}_expiry_check", resource, severity, flags,
        {
            "infrastructure_finding": True,
            "check_id": "infra-expiry-v1",
            "infra_compliance": {"kind": kind, "expires_at": expires_at, **detail},
        },
        None,
    )


def _severity_for_expiry(expires_at_iso: str) -> str:
    try:
        expires = datetime.fromisoformat(expires_at_iso.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return "MEDIUM"
    days = (expires - datetime.now(timezone.utc)).days
    return "CRITICAL" if days < 0 else "HIGH"


async def _check_expiry() -> int:
    if not db.is_available():
        return 0
    raised = 0
    for c in await asyncio.to_thread(db.list_expiring_credentials, _WARN_DAYS):
        severity = _severity_for_expiry(c["credentials_expires_at"])
        try:
            await _raise_expiry_finding(
                f"{c['connector_type']}:{c['display_name']}", "credential",
                c["credentials_expires_at"], {"connector_id": c["id"], "connector_type": c["connector_type"]},
                severity,
            )
            raised += 1
        except Exception as exc:
            logger.warning("infra_asset_sweep: failed to raise credential-expiry finding for connector %s: %s", c.get("id"), exc)

    for a in await asyncio.to_thread(db.list_expiring_infra_assets, _WARN_DAYS):
        severity = _severity_for_expiry(a["expires_at"])
        try:
            await _raise_expiry_finding(
                a["name"], "certificate" if a["asset_type"] == "certificate" else a["asset_type"],
                a["expires_at"], {"asset_key": a["asset_key"], "asset_id": a["id"]},
                severity,
            )
            raised += 1
        except Exception as exc:
            logger.warning("infra_asset_sweep: failed to raise expiry finding for asset %s: %s", a.get("asset_key"), exc)
    return raised


async def sweep_once() -> dict:
    """Run one full pass: sync assets, then check/raise expiry findings.
    Returns counts — exposed for tests and an on-demand admin trigger, not
    just the periodic loop."""
    synced = await _sync_assets()
    raised = await _check_expiry()
    if synced or raised:
        logger.info("infra_asset_sweep: synced %d asset(s), raised %d expiry finding(s)", synced, raised)
    return {"assets_synced": synced, "expiry_findings_raised": raised}


async def start_sweep() -> None:
    logger.info("Infra asset/expiry sweep started (tick=%.0fs, warn_days=%d)", _TICK_S, _WARN_DAYS)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("Infra asset/expiry sweep stopped")
            break
        except Exception as exc:
            logger.warning("infra_asset_sweep tick error: %s", exc)
