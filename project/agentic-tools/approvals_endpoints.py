#!/usr/bin/env python3
"""
approvals_endpoints.py — Real 2-stage (preparer -> manager) HITL approval workflow.

Replaces the fixed CAE/CFO/Audit-Committee signoff chain used by Enterprise
Risk Gate 1/2 and SOX Gate S1/S2. A gate item is either:
  - "approved" as computed -> final immediately (nothing to check), or
  - "adjusted" by the preparer -> routed to the preparer's manager (from
    auth.users.manager_id) for review.

Identity is always taken from the authenticated session (get_current_user),
never from the request body — the frontend cannot claim to be someone else,
and a manager review is rejected server-side if the caller isn't the assigned
manager for that item.

Endpoints:
    POST /approvals/prepare            Record a preparer's disposition on a gate item
    POST /approvals/review             Manager approves or rejects a submitted item
    GET  /approvals/inbox              Items awaiting the current user's review
    GET  /approvals/status/{run_id}    All approval tasks for a run (restores gate UI state)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import auth_db
import db
from auth_endpoints import get_current_user

router = APIRouter(prefix="/approvals", tags=["Approval Workflow"])


class PrepareRequest(BaseModel):
    run_id: int
    gate_type: str            # 'risk' | 'objective' | 'sox_materiality' | 'sox_account' | 'sox_process'
    item_ref: str
    item_label: Optional[str] = None
    disposition: str          # 'approved' | 'adjusted'
    adjustments: Optional[Dict[str, Any]] = None
    rationale: Optional[str] = None


class ReviewRequest(BaseModel):
    task_id: int
    decision: str              # 'approved' | 'rejected'
    comment: Optional[str] = None


def _display_name(user: dict) -> str:
    return user.get("display_name") or user.get("username") or f"User {user.get('id')}"


@router.post("/prepare")
def prepare_item(req: PrepareRequest, current_user: dict = Depends(get_current_user)):
    """
    Record the preparer's disposition on a single gate item. Preparer identity
    comes from the session, not the request body. If disposition is 'adjusted',
    this resolves the preparer's manager server-side and routes the item to
    them; 'approved' finalises immediately.
    """
    if req.disposition not in ("approved", "adjusted"):
        raise HTTPException(status_code=400, detail="disposition must be 'approved' or 'adjusted'")
    if not db.is_available():
        return {"saved": False, "reason": "database not configured"}

    manager = auth_db.get_manager_of(current_user["id"]) if req.disposition == "adjusted" else None

    task = db.upsert_approval_task(
        run_id=req.run_id,
        gate_type=req.gate_type,
        item_ref=req.item_ref,
        item_label=req.item_label,
        disposition=req.disposition,
        adjustments=req.adjustments,
        rationale=req.rationale,
        prepared_by=current_user["id"],
        prepared_by_name=_display_name(current_user),
        manager_id=manager["id"] if manager else None,
        manager_name=_display_name(manager) if manager else None,
    )
    if not task:
        raise HTTPException(status_code=500, detail="Failed to save approval task")
    return {"saved": True, "task": task}


@router.post("/review")
def review_item(req: ReviewRequest, current_user: dict = Depends(get_current_user)):
    """Manager decision on a submitted item. 403s if the caller isn't the assigned manager."""
    if req.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'rejected'")
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    task = db.get_approval_task(req.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Approval task not found")
    if task.get("manager_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="You are not the assigned reviewer for this item")
    if task.get("status") != "submitted":
        raise HTTPException(status_code=409, detail=f"Item is not awaiting review (status: {task.get('status')})")

    updated = db.review_approval_task(
        task_id=req.task_id,
        reviewer_id=current_user["id"],
        reviewer_name=_display_name(current_user),
        decision=req.decision,
        comment=req.comment,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Item was already reviewed")
    return {"saved": True, "task": updated}


@router.get("/inbox")
def get_inbox(current_user: dict = Depends(get_current_user)):
    """Items currently awaiting the logged-in user's review, across all gate types."""
    if not db.is_available():
        return {"items": []}
    return {"items": db.get_approval_inbox(current_user["id"])}


@router.get("/status/{run_id}")
def get_run_status(run_id: int, gate_type: Optional[str] = Query(default=None), current_user: dict = Depends(get_current_user)):
    """All approval tasks for a run — used to restore Gate 1/2/S1/S2 UI state on reload."""
    if not db.is_available():
        return {"tasks": []}
    return {"tasks": db.get_approval_tasks_for_run(run_id, gate_type)}
