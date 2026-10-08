#!/usr/bin/env python3
"""
Control Cost Efficiency — admin-entered cost profiles for a Head of
Operations to weigh against each control's share of risk reduction.

No system this platform ingests from (EDGAR, ITSM, CI, observability
telemetry) carries what a control actually costs to run — that number only
exists if a human enters it. This router is that entry point;
control-cost.jsx does the actual efficiency computation ($/risk-point) by
joining these profiles against the live risk register's risk/map data it
already has in memory (risk-engine.js buildObjectives' objective.controls
entries, which carry each control's ref — e.g. "CUS-101" — as the first
token of a free-text label), rather than this backend owning a
"controls" table it doesn't have.

Router prefix: /control-cost

    GET /control-cost/profiles          All cost profiles on file
    PUT /control-cost/profiles/{ref}    Set/update one control's cost profile
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import db
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.control_cost")

router = APIRouter(prefix="/control-cost", tags=["Control Cost Efficiency"])

# No prior screen owned this data, so this is its own screen id (nav id
# "controlcost") rather than piggybacking on an unrelated existing one —
# unlike itsm_endpoints.py/evidence_endpoints.py, which reuse
# "infrastructuremonitoring" because they predate having anywhere better.
_SCREEN_ID = "controlcost"


class ControlCostProfileRequest(BaseModel):
    annual_cost_usd: Optional[float] = Field(default=None, ge=0)
    hours_per_month: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None


@router.get("/profiles")
def list_profiles(current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        return {"profiles": []}
    return {"profiles": db.list_control_cost_profiles()}


@router.put("/profiles/{control_ref}")
def upsert_profile(
    control_ref: str, req: ControlCostProfileRequest,
    current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID, edit=True)),
):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    control_ref = control_ref.strip().upper()
    if not control_ref:
        raise HTTPException(status_code=422, detail="control_ref is required")
    ok = db.upsert_control_cost_profile(
        control_ref=control_ref, annual_cost_usd=req.annual_cost_usd, hours_per_month=req.hours_per_month,
        notes=req.notes, updated_by=current_user.get("username") or "unknown",
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save control cost profile")
    return {"control_ref": control_ref, "saved": True}
