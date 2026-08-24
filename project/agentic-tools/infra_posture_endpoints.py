#!/usr/bin/env python3
"""
Infrastructure Vulnerability & Currency Posture API — Development
environment only (see deploy_env.py; same 404-not-403 gate exceptions_endpoints.py
uses, so a UAT/Sandbox/Production caller can't tell this feature exists).

Surfaces the Phase 1/2 register tables (observability.infra_assets,
observability.infra_vulnerabilities) built by infra_asset_sweep.py and
vulnerability_sweep.py. Every summary count here MUST be read alongside
coverage (assets_assessed / assets_total) — see get_vulnerability_summary's
docstring: this reports "no known-open findings from connected sources", not
"no vulnerabilities exist". The frontend tabs (infrastructure-monitoring.jsx)
are responsible for keeping that framing visible, not just this endpoint.

Router prefix: /infra-posture

    GET  /infra-posture/summary                        Coverage-aware headline counts
    GET  /infra-posture/assets                          Asset inventory (filterable)
    GET  /infra-posture/vulnerabilities                 Vulnerability register (filterable)
    POST /infra-posture/vulnerabilities/{id}/disposition  accept-risk or false-positive

Accept-risk creates a real observability.risk_waivers row (reused, not a
second acceptance mechanism) but — unlike the SCM exception flow's HITL
gate_type='devops_scm_exception' approval — does so directly, with no
separate manager-approval step. This is a deliberate, documented scope
simplification for a still-settling, dev-only feature (approval_task_id is
left NULL, which the schema already treats as valid for a waiver with no
backing HITL task), not an oversight; wiring a real approval step is future
work if this graduates out of Development.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

import db
import deploy_env
from auth_endpoints import get_current_user, require_screen_permission

logger = logging.getLogger("ubo.infra_posture")

# No dedicated nav screen id exists for this yet — reuses Infrastructure
# Monitoring's, same "no nav yet, closest existing permission bucket"
# reasoning evidence_endpoints.py documents for itself; the frontend adds
# tabs to that same screen rather than a new nav entry.
_SCREEN_ID = "infrastructuremonitoring"

_DEFAULT_WAIVER_DAYS = 90
_ACCEPT_RISK_STATUSES = ("accepted_risk", "false_positive")


def _require_dev_environment() -> None:
    if not deploy_env.IS_DEVELOPMENT:
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(
    prefix="/infra-posture", tags=["Infrastructure Vulnerability & Currency Posture"],
    dependencies=[Depends(_require_dev_environment), Depends(require_screen_permission(_SCREEN_ID))],
)


@router.get("/summary")
def get_summary(warn_days: int = Query(30, ge=1, le=365)):
    if not db.is_available():
        return {
            "open_by_severity": {}, "open_total": 0, "assets_total": 0, "assets_assessed": 0,
            "remediated_last_30d": 0, "expiring_credentials": 0, "expiring_certificates": 0,
        }
    summary = db.get_vulnerability_summary()
    summary["expiring_credentials"] = len(db.list_expiring_credentials(warn_days))
    summary["expiring_certificates"] = len(db.list_expiring_infra_assets(warn_days))
    return summary


@router.get("/assets")
def get_assets(
    asset_type: Optional[str] = None, unassessed_only: bool = False, limit: int = Query(500, ge=1, le=2000),
):
    if not db.is_available():
        return {"assets": []}
    return {"assets": db.list_infra_assets(asset_type=asset_type, unassessed_only=unassessed_only, limit=limit)}


@router.get("/vulnerabilities")
def get_vulnerabilities(
    status: Optional[str] = None, severity: Optional[str] = None,
    asset_id: Optional[int] = None, source: Optional[str] = None,
    limit: int = Query(500, ge=1, le=2000),
):
    if not db.is_available():
        return {"vulnerabilities": []}
    return {"vulnerabilities": db.list_infra_vulnerabilities(
        status=status, severity=severity, asset_id=asset_id, source=source, limit=limit,
    )}


@router.post("/vulnerabilities/{vuln_id}/disposition")
def disposition_vulnerability(
    vuln_id: int,
    body: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """body: {"status": "accepted_risk"|"false_positive", "reason": str,
    "compensating_control": str (optional, accepted_risk only)}. reason is
    mandatory for both — a disposition with no rationale is exactly the kind
    of silent downgrade this register exists to prevent."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    status = (body or {}).get("status")
    reason = (body or {}).get("reason", "").strip()
    if status not in _ACCEPT_RISK_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {_ACCEPT_RISK_STATUSES}")
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")

    disposed_by = current_user.get("email") or current_user.get("username") or "unknown"
    waiver_id = None
    if status == "accepted_risk":
        vuln_hash = hashlib.sha256(f"infra-vuln:{vuln_id}".encode("utf-8")).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(days=_DEFAULT_WAIVER_DAYS)
        waiver_id = db.create_risk_waiver(
            vuln_hash, reason, (body or {}).get("compensating_control"), disposed_by, None, expires_at,
        )

    ok = db.update_infra_vulnerability_status(vuln_id, status, None, waiver_id, reason, disposed_by)
    if not ok:
        raise HTTPException(status_code=404, detail="Vulnerability not found")
    return {"id": vuln_id, "status": status, "waiver_id": waiver_id}
