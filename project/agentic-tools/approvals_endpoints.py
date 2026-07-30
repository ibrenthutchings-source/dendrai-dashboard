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
    GET  /approvals/ai-acceptance-stats  Admin: how often preparers keep vs. override AI suggestions
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import auth_db
import db
from auth_endpoints import require_admin, require_screen_permission

router = APIRouter(prefix="/approvals", tags=["Approval Workflow"])


class PrepareRequest(BaseModel):
    # None only for gate_type='devops_scm_exception' — no risk_loop_runs association.
    run_id: Optional[int] = None
    gate_type: str            # 'risk' | 'objective' | 'sox_materiality' | 'sox_account' | 'sox_process' | 'devops_scm_exception'
    item_ref: str
    item_label: Optional[str] = None
    disposition: str          # 'approved' | 'adjusted'
    adjustments: Optional[Dict[str, Any]] = None
    rationale: Optional[str] = None
    ai_suggested: Optional[Dict[str, Any]] = None  # "Suggest with AI" values, if that was used


class ReviewRequest(BaseModel):
    task_id: int
    decision: str              # 'approved' | 'rejected'
    comment: Optional[str] = None


def _display_name(user: dict) -> str:
    return user.get("display_name") or user.get("username") or f"User {user.get('id')}"


@router.post("/prepare")
def prepare_item(req: PrepareRequest, current_user: dict = Depends(require_screen_permission("approvals", edit=True))):
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
        ai_suggested=req.ai_suggested,
    )
    if not task:
        raise HTTPException(status_code=500, detail="Failed to save approval task")

    # A risk adjustment that's final immediately (auto-approved — no manager
    # configured) must propagate to risk_scores now, same reasoning as the
    # manager-approval path in review_item below: without this, every reader
    # keyed on risk_scores (get_posture_trend's RAG counts chief among them)
    # keeps showing the run's pre-adjustment snapshot forever.
    if task.get("status") == "approved" and task.get("gate_type") == "risk" and task.get("disposition") == "adjusted":
        db.update_risk_score_fields(task["run_id"], task["item_ref"], task.get("adjustments") or {})

    if task.get("status") == "approved" and task.get("gate_type") == "devops_scm_exception":
        _create_waiver_from_task(task, _display_name(current_user))

    return {"saved": True, "task": task}


@router.post("/review")
def review_item(req: ReviewRequest, current_user: dict = Depends(require_screen_permission("approvals", edit=True))):
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

    # Manager-approved risk adjustment -> propagate to risk_scores. This runs
    # server-side (not triggered by the frontend) because the manager review
    # can happen long after the preparer's browser session that submitted the
    # adjustment is gone — risk_scores otherwise stays frozen at the run's
    # original computed values even after a human explicitly overrode them
    # and a manager signed off, which is exactly the discrepancy Posture
    # Trend's RAG counts were silently showing.
    if updated.get("status") == "manager_approved" and updated.get("gate_type") == "risk" and updated.get("disposition") == "adjusted":
        db.update_risk_score_fields(updated["run_id"], updated["item_ref"], updated.get("adjustments") or {})

    # DevOps Monitoring: a manager-approved SCM exception becomes a real,
    # time-boxed Risk Waiver (observability.risk_waivers) — see
    # _create_waiver_from_task. Rejected exceptions create no waiver; the
    # finding stays open/failing exactly as if nothing had been requested.
    if updated.get("status") == "manager_approved" and updated.get("gate_type") == "devops_scm_exception":
        _create_waiver_from_task(updated, _display_name(current_user))

    return {"saved": True, "task": updated}


def _create_waiver_from_task(task: dict, approved_by: str) -> None:
    """Turn an approved devops_scm_exception approval_tasks row into an ACTIVE
    observability.risk_waivers row. item_ref is the vulnerability_hash
    (evidence_records.fingerprint, or an scm control key for a branch-
    protection exception); adjustments carries reason/compensating_control/
    expires_at from the preparer's request. A pre-existing ACTIVE waiver for
    the same hash is revoked first — the unique index on (vulnerability_hash)
    WHERE status='ACTIVE' would otherwise reject the insert outright, and
    silently colliding two waivers for the same finding is worse than making
    the newest approval the one that counts."""
    adjustments = task.get("adjustments") or {}
    expires_at = adjustments.get("expires_at")
    if not expires_at:
        return  # a waiver with no expiration isn't a waiver — nothing to create
    existing = db.get_active_waiver(task["item_ref"])
    if existing:
        db.revoke_risk_waiver(existing["id"])
    db.create_risk_waiver(
        vulnerability_hash=task["item_ref"],
        reason=task.get("rationale") or adjustments.get("reason") or "No rationale provided",
        compensating_control=adjustments.get("compensating_control"),
        approved_by=approved_by,
        approval_task_id=task["id"],
        expires_at=expires_at,
    )


@router.get("/inbox")
def get_inbox(current_user: dict = Depends(require_screen_permission("approvals"))):
    """Items currently awaiting the logged-in user's review, across all gate types."""
    if not db.is_available():
        return {"items": []}
    return {"items": db.get_approval_inbox(current_user["id"])}


@router.get("/status/{run_id}")
def get_run_status(run_id: int, gate_type: Optional[str] = Query(default=None), current_user: dict = Depends(require_screen_permission("approvals"))):
    """All approval tasks for a run — used to restore Gate 1/2/S1/S2 UI state on reload."""
    if not db.is_available():
        return {"tasks": []}
    return {"tasks": db.get_approval_tasks_for_run(run_id, gate_type)}


@router.get("/ai-acceptance-stats")
def get_ai_acceptance_stats(gate_type: Optional[str] = Query(default=None), current_user: dict = Depends(require_admin)):
    """
    Per-gate-type counts of how often a preparer kept an AI 'Suggest with AI'
    disposition as-is vs. overrode it — the measurable trail for whether the
    AI recommendations are actually trusted, not just offered.

    Also breaks the same signal down by risk category and by industry
    (MODEL_CARD.md "Recommended Next Steps" #1) — the fairness question this
    exists to answer is whether AI advice gets overridden more often for
    particular categories/industries, which a gate_type-only breakdown can't
    show.
    """
    if not db.is_available():
        return {"stats": [], "by_category": [], "by_industry": []}
    return {
        "stats": db.get_ai_acceptance_stats(gate_type),
        "by_category": db.get_ai_acceptance_stats_by_category(),
        "by_industry": db.get_ai_acceptance_stats_by_industry(),
    }
