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
    GET  /approvals/remediations           Recent closed-loop remediation tasks (any status)
    POST /approvals/remediations/{id}/retry  Re-fire a failed remediation's GitHub write

Closed-loop remediation (gate_type='remediation_github' | 'remediation_github_pr',
proposed via remediation_endpoints.py) is the first gate type whose approval
triggers a real external write, not just a DB row — an issue for the former,
a real file-change PR for the latter — see _execute_remediation below.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import auth_db
import db
import github_write_tool
import mcp_guards
from auth_endpoints import require_admin, require_screen_permission

logger = logging.getLogger("ubo.approvals")

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

    # Closed-loop remediation: "approve = execute" — the manager's approval
    # IS the human-in-the-loop gate; nothing further blocks the write. A
    # rejected proposal fires nothing and the source finding stays open,
    # same as any other rejected gate item. Covers both the issue gate type
    # and the PR gate type — _execute_remediation itself branches on which.
    if updated.get("status") == "manager_approved" and updated.get("gate_type") in ("remediation_github", "remediation_github_pr"):
        full_task = db.get_approval_task(updated["id"]) or updated
        _execute_remediation(full_task)

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


def _execute_remediation(task: dict) -> None:
    """Fire the actual GitHub write for an approved remediation_github or
    remediation_github_pr task, persist the outcome (github_write_tool.py's
    own {"number","url",...} or {"error": "..."} shape, verbatim) via
    db.set_approval_task_execution_result, and — only on success — mark the
    source exception resolved. A failed write is NOT swallowed:
    execution_result carries the error so the Approval Inbox can offer
    /approvals/remediations/{id}/retry instead of the task silently
    vanishing once decided, and the source exception stays open
    (submit_exception_triage is only called on success).

    Called from three places: review_item (manager approved a submitted
    proposal), remediation_endpoints.propose_remediation, and
    propose_pr_remediation (no manager configured -> auto-approved, same
    immediate-execution reasoning prepare_item already applies to
    devops_scm_exception's auto-approve path). Never raises — a remediation
    failure must not break the approval request/response it was triggered
    from."""
    adjustments = task.get("adjustments") or {}
    title = adjustments.get("title") or task.get("item_label") or f"Remediation for {task.get('item_ref')}"
    body = adjustments.get("body") or task.get("rationale") or ""
    repo = adjustments.get("repo")
    is_pr = task.get("gate_type") == "remediation_github_pr"

    tool_name = "github_write_tool.create_pull_request" if is_pr else "github_write_tool.create_issue"
    mcp_guards.audit_log(tool_name, task_id=task.get("id"), item_ref=task.get("item_ref"), repo=repo or "default")
    try:
        if is_pr:
            files = adjustments.get("_files") or {}
            base_branch = adjustments.get("base_branch") or "main"
            result = github_write_tool.create_pull_request(title, body, files, repo=repo, base_branch=base_branch)
        else:
            labels = adjustments.get("labels") or ["dendrai-remediation"]
            result = github_write_tool.create_issue(title, body, repo=repo, labels=labels)
    except Exception as exc:
        result = {"error": str(exc)}
    mcp_guards.audit_log(f"{tool_name}.result", task_id=task.get("id"),
                          ok=not result.get("error"), url=result.get("url") or result.get("error"))

    db.set_approval_task_execution_result(task["id"], result)

    if not result.get("error"):
        source_event_id = adjustments.get("source_event_id")
        if source_event_id is not None:
            try:
                what = "GitHub PR" if is_pr else "GitHub issue"
                db.submit_exception_triage(
                    int(source_event_id), "system:remediation", "TRUE_CONTROL_FAILURE",
                    f"Auto-resolved by closed-loop remediation — {what} opened: {result.get('url')}",
                )
            except Exception as exc:
                logger.warning("remediation: could not auto-resolve source event %s: %s", source_event_id, exc)


@router.get("/remediations")
def get_remediations(current_user: dict = Depends(require_screen_permission("approvals"))):
    """Recent closed-loop remediation tasks, any status — unlike /inbox
    (only 'submitted', awaiting-review items), this is how the frontend
    shows what happened after a decision: the created issue link, or a
    failure to retry."""
    if not db.is_available():
        return {"tasks": []}
    return {"tasks": db.list_remediation_tasks()}


@router.post("/remediations/{task_id}/retry")
def retry_remediation(task_id: int, current_user: dict = Depends(require_screen_permission("approvals", edit=True))):
    """Re-fire a failed remediation's GitHub write with the same approved
    content — for a transient failure (rate limit, momentary outage), not a
    way to re-propose different content (that's a new /remediation/propose call)."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    task = db.get_approval_task(task_id)
    if not task or task.get("gate_type") not in ("remediation_github", "remediation_github_pr"):
        raise HTTPException(status_code=404, detail=f"No remediation task with id={task_id}")
    if task.get("status") not in ("approved", "manager_approved"):
        raise HTTPException(status_code=409, detail=f"Task is not in an approved state (status: {task.get('status')})")
    _execute_remediation(task)
    return {"task": db.get_approval_task(task_id)}


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
