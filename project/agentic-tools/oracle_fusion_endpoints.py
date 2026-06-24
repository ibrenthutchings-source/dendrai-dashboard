#!/usr/bin/env python3
"""
Oracle Fusion Controls — FastAPI router

Exposes oracle_fusion_tool functions as REST endpoints.
Mounted in api_server.py with prefix /oracle-fusion.

Endpoints:
    GET  /oracle-fusion/status
    GET  /oracle-fusion/summary
    POST /oracle-fusion/control-library
    POST /oracle-fusion/control-results
    POST /oracle-fusion/control-issues
    POST /oracle-fusion/user-roles
    POST /oracle-fusion/sod-violations
    POST /oracle-fusion/audit-events
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import oracle_fusion_tool as fusion

router = APIRouter(prefix="/oracle-fusion", tags=["Oracle Fusion"])

_503_detail = (
    "Oracle Fusion not configured. "
    "Set ORACLE_FUSION_HOST, ORACLE_FUSION_USERNAME, and ORACLE_FUSION_PASSWORD."
)


def _require_configured() -> None:
    if not fusion.is_configured():
        raise HTTPException(status_code=503, detail=_503_detail)


# ── Request models ─────────────────────────────────────────────────────────────

class ControlLibraryRequest(BaseModel):
    control_type: str = ""
    category:     str = ""
    status:       str = "Active"
    max_items:    int = 200

class ControlResultsRequest(BaseModel):
    date_from:     str = ""
    date_to:       str = ""
    effectiveness: str = ""
    max_items:     int = 200

class ControlIssuesRequest(BaseModel):
    status:    str = "Open"
    severity:  str = ""
    date_from: str = ""
    max_items: int = 200

class UserRolesRequest(BaseModel):
    username:  str = ""
    role_name: str = ""
    max_items: int = 500

class SodViolationsRequest(BaseModel):
    status:     str = "Open"
    risk_level: str = ""
    max_items:  int = 200

class AuditEventsRequest(BaseModel):
    module:     str = ""
    date_from:  str = ""
    date_to:    str = ""
    event_type: str = ""
    username:   str = ""
    max_items:  int = 500


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/status")
def oracle_fusion_status():
    """
    Returns whether Oracle Fusion credentials are configured and
    which authentication method is active.
    """
    host = os.environ.get("ORACLE_FUSION_HOST", "")
    has_oauth = bool(
        os.environ.get("ORACLE_FUSION_CLIENT_ID")
        and os.environ.get("ORACLE_FUSION_CLIENT_SECRET")
    )
    has_basic = bool(os.environ.get("ORACLE_FUSION_USERNAME"))
    return {
        "configured": bool(host),
        "host":       host or None,
        "auth_method": "oauth2" if has_oauth else ("basic" if has_basic else "none"),
        "api_version": os.environ.get("ORACLE_FUSION_API_VERSION", "11.13.18.05"),
    }


@router.get("/summary")
def oracle_fusion_summary():
    """
    Aggregated control health overview — combines control library effectiveness,
    open issues, and SOD violations. Returns risk_signals compatible with the
    Dendrai risk register schema and an overall RAG / control score.

    Recommended first call to get the full control health picture.
    """
    _require_configured()
    try:
        return fusion.get_control_summary()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/control-library")
def oracle_fusion_control_library(req: ControlLibraryRequest):
    """
    Return the Oracle Risk Management Cloud control library.

    Lists control definitions with type, frequency, effectiveness, owner,
    and last test date. Each control is tagged with a Dendrai risk category.
    """
    _require_configured()
    try:
        return fusion.get_control_library(
            control_type=req.control_type,
            category=req.category,
            status=req.status,
            max_items=req.max_items,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/control-results")
def oracle_fusion_control_results(req: ControlResultsRequest):
    """
    Return control test results (operating effectiveness evidence).

    Useful for populating internal audit workpapers with evidence that
    controls operated effectively (or didn't) during the period.
    """
    _require_configured()
    try:
        return fusion.get_control_results(
            date_from=req.date_from,
            date_to=req.date_to,
            effectiveness=req.effectiveness,
            max_items=req.max_items,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/control-issues")
def oracle_fusion_control_issues(req: ControlIssuesRequest):
    """
    Return open control deficiencies and remediation plans.

    Each issue includes severity, RAG status, root cause, remediation plan,
    owner, and due date. Pass status=All to include resolved issues.
    """
    _require_configured()
    try:
        return fusion.get_control_issues(
            status=req.status,
            severity=req.severity,
            date_from=req.date_from,
            max_items=req.max_items,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/user-roles")
def oracle_fusion_user_roles(req: UserRolesRequest):
    """
    Return user-to-role assignments (access control listing) via SCIM 2.0.

    Pass username to see a specific user's roles, or role_name to see all
    users holding that role. Useful for access certifications and privilege reviews.
    """
    _require_configured()
    try:
        return fusion.get_user_roles(
            username=req.username,
            role_name=req.role_name,
            max_items=req.max_items,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sod-violations")
def oracle_fusion_sod_violations(req: SodViolationsRequest):
    """
    Return segregation-of-duties violations from Oracle RMCS.

    Each violation identifies the user, conflicting role pair, SOD policy,
    risk level, and any mitigating control. Pass status=All to include resolved.
    """
    _require_configured()
    try:
        return fusion.get_sod_violations(
            status=req.status,
            risk_level=req.risk_level,
            max_items=req.max_items,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/audit-events")
def oracle_fusion_audit_events(req: AuditEventsRequest):
    """
    Return transaction audit trail events from Oracle Fusion FSCM modules.

    Module codes: FIN_AP (AP), FIN_AR (AR), FIN_GL (GL), FIN_FA (Fixed Assets),
    PRC (Procurement), HCM (HR). Leave empty to query all modules.
    """
    _require_configured()
    try:
        return fusion.get_audit_events(
            module=req.module,
            date_from=req.date_from,
            date_to=req.date_to,
            event_type=req.event_type,
            username=req.username,
            max_items=req.max_items,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
