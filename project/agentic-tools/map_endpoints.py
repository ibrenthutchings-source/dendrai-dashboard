#!/usr/bin/env python3
"""
Continuous Monitoring: Management Action Plans (MAPs).

map_detection_sweep.py drafts a proposal (risk rating, root cause,
remediation action, success criteria) whenever a control keeps requiring
human review; this router is where a human reviews, adjusts, and decides
it — the same propose -> human review/adjust -> approve shape Enterprise
Risk Gate 1 applies to a risk rating (risk-approval.jsx), not a shortcut
around it. Deliberately its own review path rather than routed through the
generic approval_tasks preparer->manager flow (approvals_endpoints.py):
these proposals have no human preparer to derive a manager from (a system
detector raised them), and that flow's "no manager configured -> auto-approve"
escape hatch — a reasonable default for a human's own unmanaged submission —
would be exactly wrong here, where mandatory review is the entire point.
Any reviewer with edit access to the "maps" screen can decide one, the same
way any authorized reviewer decides a regulatory_change_endpoints.py proposal.

Router prefix: /maps

    GET  /maps                    List (?status=proposed|approved|rejected|in_progress|closed)
    GET  /maps/{map_ref}          One MAP, including its source event ids
    POST /maps/{map_ref}/decision Approve (optionally adjusting drafted fields first) or reject
    PUT  /maps/{map_ref}/progress Execution tracking — update completion_pct
    POST /maps/detect             On-demand recurrence-detection pass (normally runs daily)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
import map_detection_sweep
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.maps")

router = APIRouter(prefix="/maps", tags=["Continuous Monitoring: Management Action Plans"])

_SCREEN_ID = "maps"
_EDITABLE_FIELDS = {"risk_rating", "root_cause", "action", "owner", "due_date", "success_criteria", "reduction_pct"}


class MapDecisionRequest(BaseModel):
    decision: str  # 'approved' | 'rejected'
    comment: Optional[str] = None
    adjustments: Optional[Dict[str, Any]] = None


class MapProgressRequest(BaseModel):
    completion_pct: int


def _display_name(user: dict) -> str:
    return user.get("display_name") or user.get("username") or f"User {user.get('id')}"


@router.get("")
def list_maps(status: Optional[str] = None, limit: int = 100,
              current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        return {"maps": []}
    return {"maps": db.list_maps(status=status, limit=limit)}


@router.get("/{map_ref}")
def get_map(map_ref: str, current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    m = db.get_map(map_ref)
    if not m:
        raise HTTPException(status_code=404, detail=f"No MAP with ref={map_ref}")
    return m


@router.post("/{map_ref}/decision")
def decide_map(
    map_ref: str, req: MapDecisionRequest,
    current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID, edit=True)),
):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    if req.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="decision must be 'approved' or 'rejected'")

    adjustments = req.adjustments or {}
    bad_keys = set(adjustments) - _EDITABLE_FIELDS
    if bad_keys:
        raise HTTPException(status_code=422, detail=f"Cannot adjust field(s): {sorted(bad_keys)}")
    if req.decision == "rejected" and not (req.comment or "").strip():
        raise HTTPException(status_code=422, detail="A comment is required when rejecting a MAP")

    updated = db.decide_map(map_ref, req.decision, _display_name(current_user), req.comment, adjustments)
    if not updated:
        existing = db.get_map(map_ref)
        if not existing:
            raise HTTPException(status_code=404, detail=f"No MAP with ref={map_ref}")
        raise HTTPException(status_code=409, detail=f"MAP is not awaiting review (status: {existing['status']})")

    logger.info("maps: %s %s by %s", map_ref, req.decision, _display_name(current_user))
    return {"map": updated}


@router.put("/{map_ref}/progress")
def update_progress(
    map_ref: str, req: MapProgressRequest,
    current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID, edit=True)),
):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    if not (0 <= req.completion_pct <= 100):
        raise HTTPException(status_code=422, detail="completion_pct must be between 0 and 100")

    updated = db.update_map_progress(map_ref, req.completion_pct)
    if not updated:
        existing = db.get_map(map_ref)
        if not existing:
            raise HTTPException(status_code=404, detail=f"No MAP with ref={map_ref}")
        raise HTTPException(status_code=409, detail=f"MAP has no execution to track yet (status: {existing['status']})")
    return {"map": updated}


@router.post("/detect")
async def trigger_detection(current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID, edit=True))):
    """On-demand recurrence-detection pass — normally runs daily
    (map_detection_sweep.start_sweep), exposed here for testing/demo and
    for a reviewer who doesn't want to wait for the next scheduled tick."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    proposed = await map_detection_sweep.sweep_once()
    return {"proposed": proposed}
