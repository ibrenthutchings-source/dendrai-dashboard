"""
Request-as-Code for PBC: requirements, request generation, evidence intake,
review and the status board. Logic lives in pbc_request_tool.py; this file is
HTTP, permissions, persistence and audit.

Router prefix: /pbc-requests
    PUT  /requirements              declare/update what evidence a control needs
    GET  /requirements              list requirements
    POST /generate                  build an engagement's request list from requirements
    GET  /                          requests (with derived overdue status)
    GET  /summary                   status board counts
    POST /{id}/receive              link a logged evidence item; runs the automatic checks
    POST /{id}/review               accept or reject (reviewer must differ from submitter)

Permissions reuse the PBC evidence log's screen id ("evidencequality"): read to
view, edit to declare requirements, generate, submit or review. Every change is
written to the audit log (category "pbc"), success or failure.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
import pbc_request_tool as prt
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.pbc_requests")

router = APIRouter(prefix="/pbc-requests", tags=["PBC Requests"])

_SCREEN_ID = "evidencequality"
_read = require_screen_permission(_SCREEN_ID)
_edit = require_screen_permission(_SCREEN_ID, edit=True)


class RequirementIn(BaseModel):
    control_id: str
    requirement_key: str
    title: str
    description: Optional[str] = None
    artifact_type: Optional[str] = None
    source_system: Optional[str] = "manual"
    frequency: str = "once"
    requires_signature: bool = False
    max_age_days: int = 90
    owner: Optional[str] = None
    acceptance_notes: Optional[str] = None
    active: bool = True


class GenerateIn(BaseModel):
    engagement: str
    period_start: str
    period_end: str
    due_date: str
    control_ids: Optional[List[str]] = None


class ReceiveIn(BaseModel):
    evidence_id: int


class ReviewIn(BaseModel):
    decision: str            # accepted | rejected
    note: Optional[str] = None


def _audit(user: dict, action: str, target: Optional[str], outcome: str, detail: dict) -> None:
    try:
        db.insert_audit_log_entry("pbc", action, actor=user.get("username"), target=target,
                                  detail=detail, outcome=outcome)
    except Exception as exc:
        logger.warning("pbc audit write failed: %s", exc)


def _fail(user: dict, action: str, target: Optional[str], status: int, message: str, reason: str,
          extra: Optional[dict] = None):
    _audit(user, action, target, "failure", {"reason": reason, "http_status": status, **(extra or {})})
    raise HTTPException(status_code=status, detail=message)


def _need_db():
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")


def _with_status(r: dict) -> dict:
    return {**r, "display_status": prt.derive_status(r["status"], r.get("due_date"))}


@router.put("/requirements", summary="Declare what evidence a control needs")
def put_requirement(req: RequirementIn, user: Dict[str, Any] = Depends(_edit)):
    _need_db()
    try:
        norm = prt.normalise_requirement(req.model_dump())
    except prt.RequestError as exc:
        _fail(user, "pbc_requirement_saved", req.control_id[:128], 422, str(exc), "invalid_requirement")
    norm["active"] = req.active
    saved = db.upsert_evidence_requirement(norm, user.get("username"))
    if not saved:
        _fail(user, "pbc_requirement_saved", norm["control_id"], 500, "Failed to save requirement", "db_error")
    _audit(user, "pbc_requirement_saved", f"{norm['control_id']}/{norm['requirement_key']}", "success",
           {"active": req.active})
    return saved


@router.get("/requirements", summary="List evidence requirements")
def list_requirements(control_id: Optional[str] = None, include_inactive: bool = False,
                      user: Dict[str, Any] = Depends(_read)):
    if not db.is_available():
        return {"requirements": []}
    return {"requirements": db.list_evidence_requirements(control_id=control_id, active_only=not include_inactive)}


@router.post("/generate", summary="Generate an engagement's request list")
def generate(req: GenerateIn, user: Dict[str, Any] = Depends(_edit)):
    _need_db()
    reqs = db.list_evidence_requirements(active_only=True)
    try:
        rows = prt.generate_requests(reqs, req.engagement, req.period_start, req.period_end,
                                     req.due_date, req.control_ids)
    except prt.RequestError as exc:
        _fail(user, "pbc_requests_generated", req.engagement[:128], 422, str(exc), "invalid_input")
    if not rows:
        _fail(user, "pbc_requests_generated", req.engagement[:128], 422,
              "No active evidence requirements match this scope. Declare requirements first.", "no_requirements")
    result = db.create_pbc_requests(rows, user.get("username"))
    if result is None:
        _fail(user, "pbc_requests_generated", req.engagement[:128], 500, "Failed to create requests", "db_error")
    _audit(user, "pbc_requests_generated", req.engagement[:128], "success",
           {**result, "period_start": req.period_start, "period_end": req.period_end, "due_date": req.due_date,
            "scoped_controls": len(req.control_ids) if req.control_ids else None})
    return {"engagement": req.engagement, **result}


@router.get("/", summary="List requests")
def list_requests(engagement: Optional[str] = None, status: Optional[str] = None,
                  control_id: Optional[str] = None, user: Dict[str, Any] = Depends(_read)):
    if not db.is_available():
        return {"requests": []}
    rows = [_with_status(r) for r in db.list_pbc_requests(engagement=engagement, control_id=control_id)]
    if status:
        rows = [r for r in rows if r["display_status"] == status]
    return {"requests": rows}


@router.get("/summary", summary="Status board for an engagement")
def summary(engagement: Optional[str] = None, user: Dict[str, Any] = Depends(_read)):
    if not db.is_available():
        return prt.summarise([])
    return prt.summarise(db.list_pbc_requests(engagement=engagement))


@router.post("/{request_id}/receive", summary="Link a logged evidence item to a request")
def receive(request_id: int, body: ReceiveIn, user: Dict[str, Any] = Depends(_edit)):
    _need_db()
    target = str(request_id)
    request = db.get_pbc_request(request_id)
    if not request:
        _fail(user, "pbc_request_received", target, 404, "Request not found", "not_found")
    evidence = db.get_pbc_evidence(body.evidence_id)
    if not evidence:
        _fail(user, "pbc_request_received", target, 404, "Evidence item not found", "evidence_not_found")
    try:
        prt.check_transition(request["status"], "received")
    except prt.RequestError as exc:
        _fail(user, "pbc_request_received", target, 409, str(exc), "invalid_state", {"status": request["status"]})

    findings = prt.check_evidence(request, evidence)
    updated = db.receive_pbc_request(request_id, body.evidence_id, user.get("username"),
                                     findings, prt.CHECKS_VERSION)
    if not updated:
        # lost a race with another submission, or the state changed after the read above
        _fail(user, "pbc_request_received", target, 409, "Request is no longer awaiting evidence", "state_changed")
    _audit(user, "pbc_request_received", target, "success",
           {"evidence_id": body.evidence_id, "control_id": request["control_id"],
            "findings": [f["code"] for f in findings]})
    return _with_status(updated)


@router.post("/{request_id}/review", summary="Accept or reject received evidence")
def review(request_id: int, body: ReviewIn, user: Dict[str, Any] = Depends(_edit)):
    _need_db()
    target = str(request_id)
    decision = body.decision.strip().lower()
    if decision not in ("accepted", "rejected"):
        _fail(user, "pbc_request_reviewed", target, 422, "decision must be 'accepted' or 'rejected'", "invalid_decision")
    note = (body.note or "").strip() or None
    if decision == "rejected" and not note:
        _fail(user, "pbc_request_reviewed", target, 422, "A rejection needs a note saying why", "note_required")

    request = db.get_pbc_request(request_id)
    if not request:
        _fail(user, "pbc_request_reviewed", target, 404, "Request not found", "not_found")
    try:
        prt.check_transition(request["status"], decision)
    except prt.RequestError as exc:
        _fail(user, "pbc_request_reviewed", target, 409, str(exc), "invalid_state", {"status": request["status"]})

    evidence = db.get_pbc_evidence(request["evidence_id"]) if request.get("evidence_id") else None
    try:
        prt.check_review_independence(user.get("username", ""), request.get("submitted_by"),
                                      (evidence or {}).get("created_by"))
    except prt.RequestError as exc:
        _audit(user, "pbc_request_reviewed", target, "denied", {"reason": "reviewer_not_independent"})
        raise HTTPException(status_code=403, detail=str(exc))

    updated = db.review_pbc_request(request_id, decision, user.get("username", ""), note)
    if not updated:
        _fail(user, "pbc_request_reviewed", target, 409, "Request is no longer awaiting review", "state_changed")
    _audit(user, "pbc_request_reviewed", target, "success",
           {"decision": decision, "control_id": request["control_id"], "had_findings": bool(request.get("checks"))})
    return _with_status(updated)
