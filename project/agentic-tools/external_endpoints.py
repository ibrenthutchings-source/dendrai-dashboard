"""
Audit portal API for external auditors, and the admin endpoints that scope them.

Router prefix: /external

Auditor side (role external_auditor only; every call is scoped to engagements an admin
assigned to the caller, and every call is audit-logged):
    GET /external/engagements                    my engagements, with request counts
    GET /external/requests?engagement=X          the PBC request list for one engagement
    GET /external/requests/{id}/evidence         the evidence item linked to a request
    GET /external/runs?engagement=X              signed report runs recorded for the engagement
    GET /external/runs/{id}                      a run: definition, parameters, manifests, rows
    GET /external/runs/{id}/verify               signature / row hashes / definition check
    GET /external/runs/{id}/html                 the run rendered from its stored rows
    GET /external/runs/{id}/xlsx                 workbook with the reperformance sheet
    GET /external/runs/{id}/pdf | pptx           document / deck

Admin side (require_admin):
    GET    /external/admin/access?engagement=X
    PUT    /external/admin/access                {"engagement", "user_id"}   grant
    DELETE /external/admin/access?engagement=X&user_id=N                      revoke

Rules that make this safe for someone outside the company:
  * The role is deny-by-default at two layers (external_auditor.py): this router and the
    session endpoints are the only paths such an account can reach.
  * Access is by assignment. An engagement the caller is not assigned to answers 404, the
    same as one that does not exist, and the attempt is audit-logged as denied.
  * A run is visible only if its engagement is assigned to the caller. Runs with no
    engagement, and runs that read restricted datasets (e.g. the audit log), are never
    visible. Evidence is reachable only through a request in an assigned engagement.
  * The portal is read-only. Auditors can verify and download; they cannot create, change,
    approve, link or review anything.
  * Grants require an active account that currently holds the external-auditor role.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

import auth_db
import db
import external_auditor
import pbc_request_tool as prt
import reporting_definitions as rd
import reporting_export as rx
import reporting_export_docs as rxd
from auth_endpoints import get_current_user, require_admin

logger = logging.getLogger("ubo.external")

router = APIRouter(prefix="/external", tags=["External Auditors"])


def _log(user: dict, action: str, target: Optional[str], outcome: str, detail: dict) -> None:
    try:
        db.insert_audit_log_entry("external", action, actor=user.get("username"), target=target,
                                  detail=detail, outcome=outcome)
    except Exception as exc:
        logger.warning("external audit write failed: %s", exc)


def _need_db() -> None:
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not available")


def _auditor(user: Dict[str, Any] = Depends(get_current_user)) -> dict:
    if not external_auditor.is_external(user.get("role")):
        raise HTTPException(status_code=403, detail="The audit portal is for external auditor accounts")
    return user


def _assigned(user: dict, engagement: Optional[str], what: str) -> str:
    """The engagement label if the caller is assigned to it; otherwise 404 (logged as denied)."""
    _need_db()
    eng = (engagement or "").strip()
    if not eng or not db.user_has_engagement_access(user["id"], eng):
        _log(user, "external_access_denied", (eng or "")[:128] or None, "denied",
             {"reason": "engagement_not_assigned", "resource": what})
        raise HTTPException(status_code=404, detail="Engagement not found")
    return eng


def _load_run(user: dict, run_id: int, what: str) -> dict:
    _need_db()
    run = db.get_report_run(run_id)
    if not run or run.get("restricted") or not run.get("engagement"):
        _log(user, "external_access_denied", str(run_id), "denied", {"reason": "run_not_available", "resource": what})
        raise HTTPException(status_code=404, detail="Run not found")
    _assigned(user, run["engagement"], what)
    return run


# ── auditor side ────────────────────────────────────────────────────────────

@router.get("/engagements", summary="My assigned engagements")
def my_engagements(user: dict = Depends(_auditor)):
    _need_db()
    rows = db.list_engagements_for_user(user["id"])
    _log(user, "external_view", None, "success", {"resource": "engagements", "count": len(rows)})
    return {"engagements": rows}


@router.get("/requests", summary="PBC requests for an assigned engagement")
def requests_for(engagement: str = Query(...), user: dict = Depends(_auditor)):
    eng = _assigned(user, engagement, "requests")
    rows = [{**r, "display_status": prt.derive_status(r["status"], r.get("due_date"))}
            for r in db.list_pbc_requests(engagement=eng)]
    _log(user, "external_view", eng, "success", {"resource": "requests", "count": len(rows)})
    return {"engagement": eng, "summary": prt.summarise(db.list_pbc_requests(engagement=eng)), "requests": rows}


@router.get("/requests/{request_id}/evidence", summary="The evidence item linked to a request")
def request_evidence(request_id: int, user: dict = Depends(_auditor)):
    _need_db()
    req = db.get_pbc_request(request_id)
    if not req:
        _log(user, "external_access_denied", str(request_id), "denied", {"reason": "request_not_found"})
        raise HTTPException(status_code=404, detail="Request not found")
    _assigned(user, req["engagement"], "evidence")
    if not req.get("evidence_id"):
        raise HTTPException(status_code=404, detail="No evidence has been linked to this request yet")
    ev = db.get_pbc_evidence(req["evidence_id"])
    if not ev:
        raise HTTPException(status_code=404, detail="Evidence not found")
    _log(user, "external_view", str(request_id), "success", {"resource": "evidence", "evidence_id": ev["id"]})
    keep = ("id", "control_id", "title", "description", "source_url", "period_start", "period_end",
            "collected_date", "has_signature", "requires_signature", "quality_flags", "created_by", "created_at")
    return {"request_id": request_id, "evidence": {k: ev.get(k) for k in keep}}


@router.get("/runs", summary="Signed report runs recorded for an assigned engagement")
def runs_for(engagement: str = Query(...), user: dict = Depends(_auditor)):
    eng = _assigned(user, engagement, "runs")
    rows = [r for r in db.list_report_runs(engagement=eng, limit=500) if not r.get("restricted")]
    _log(user, "external_view", eng, "success", {"resource": "runs", "count": len(rows)})
    return {"engagement": eng, "runs": rows}


@router.get("/runs/{run_id}", summary="A run: definition, parameters, manifests and rows")
def get_run(run_id: int, user: dict = Depends(_auditor)):
    run = _load_run(user, run_id, "run")
    payload, rows = json.loads(run.pop("payload_json")), json.loads(run.pop("rows_json"))
    _log(user, "external_view", str(run_id), "success", {"resource": "run"})
    return {**run, "payload": payload, "rows": rows}


@router.get("/runs/{run_id}/verify", summary="Check a run's signature, row hashes and definition")
def verify_run(run_id: int, user: dict = Depends(_auditor)):
    run = _load_run(user, run_id, "verify")
    try:
        result = rd.verify_run(run, sign=db.sign_evidence_record)
    except db.EvidenceSigningKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    _log(user, "external_view", str(run_id), "success" if result["intact"] else "failure",
         {"resource": "verify", **{k: result[k] for k in ("signature_valid", "rows_match_hash", "blocks_match",
                                                          "definition_matches")}})
    return {"id": run_id, **result}


@router.get("/runs/{run_id}/html", response_class=HTMLResponse, summary="Render a stored run")
def run_html(run_id: int, user: dict = Depends(_auditor)):
    run = _load_run(user, run_id, "html")
    payload, rows = json.loads(run["payload_json"]), json.loads(run["rows_json"])
    results = {bid: {"columns": payload["columns"][bid], "rows": rows.get(bid, [])} for bid in payload["manifests"]}
    d = payload["definition"]
    page = rd.render_html(d["definition"], payload["parameters"], results, {
        "Report": f"{d['key']} v{d['version']}", "Run": run_id, "As of": payload["as_of"],
        "Run by": payload["created_by"], "Engagement": payload.get("engagement"), "Note": payload.get("note"),
        "Result SHA-256": payload["result_sha256"], "Definition SHA-256": d["sha256"]})
    _log(user, "external_view", str(run_id), "success", {"resource": "html"})
    return HTMLResponse(page)


@router.get("/runs/{run_id}/xlsx", summary="Workbook with the reperformance sheet")
def run_xlsx(run_id: int, user: dict = Depends(_auditor)):
    run = _load_run(user, run_id, "xlsx")
    payload, rows = json.loads(run["payload_json"]), json.loads(run["rows_json"])
    data = rx.build_xlsx(run_id, payload, rows)
    _log(user, "external_download", str(run_id), "success", {"format": "xlsx", "bytes": len(data)})
    key = payload["definition"]["key"]
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{key}-run{run_id}.xlsx"'})


def _doc(run_id: int, user: dict, fmt: str) -> Response:
    run = _load_run(user, run_id, fmt)
    payload, rows = json.loads(run["payload_json"]), json.loads(run["rows_json"])
    media, build = {"pdf": ("application/pdf", rxd.build_pdf),
                    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation",
                             rxd.build_pptx)}[fmt]
    try:
        data = build(run_id, payload, rows)
    except Exception as exc:
        logger.warning("external %s export failed for run %s: %s", fmt, run_id, exc)
        _log(user, "external_download", str(run_id), "failure", {"format": fmt, "error": type(exc).__name__})
        raise HTTPException(status_code=500, detail=f"Could not build the {fmt.upper()}")
    _log(user, "external_download", str(run_id), "success", {"format": fmt, "bytes": len(data)})
    key = payload["definition"]["key"]
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{key}-run{run_id}.{fmt}"'})


@router.get("/runs/{run_id}/pdf", summary="Run as a PDF with the reperformance appendix")
def run_pdf(run_id: int, user: dict = Depends(_auditor)):
    return _doc(run_id, user, "pdf")


@router.get("/runs/{run_id}/pptx", summary="Run as a PowerPoint deck")
def run_pptx(run_id: int, user: dict = Depends(_auditor)):
    return _doc(run_id, user, "pptx")


# ── admin side ──────────────────────────────────────────────────────────────

from pydantic import BaseModel  # noqa: E402


class GrantIn(BaseModel):
    engagement: str
    user_id: int


def _admin_fail(admin: dict, action: str, target: str, status: int, message: str, reason: str):
    _log(admin, action, target, "failure", {"reason": reason, "http_status": status})
    raise HTTPException(status_code=status, detail=message)


@router.get("/admin/access", summary="Auditors assigned to an engagement (admin)")
def list_access(engagement: str = Query(...), admin: dict = Depends(require_admin)):
    _need_db()
    return {"engagement": engagement, "auditors": db.list_engagement_auditors(engagement.strip())}


@router.put("/admin/access", summary="Assign an external auditor to an engagement (admin)")
def grant_access(body: GrantIn, admin: dict = Depends(require_admin)):
    _need_db()
    eng = body.engagement.strip()[:128]
    target = f"{eng}:{body.user_id}"
    if not eng:
        _admin_fail(admin, "external_engagement_granted", target, 422, "engagement is required", "invalid_input")
    user = auth_db.get_user_by_id(body.user_id)
    if not user:
        _admin_fail(admin, "external_engagement_granted", target, 404, "User not found", "user_not_found")
    if not external_auditor.is_external(user.get("role")):
        _admin_fail(admin, "external_engagement_granted", target, 422,
                    f"Only accounts with the '{external_auditor.EXTERNAL_ROLE}' role can be assigned to an engagement",
                    "not_external_role")
    if not user.get("is_active"):
        _admin_fail(admin, "external_engagement_granted", target, 422, "That account is inactive", "inactive_user")
    if not db.list_pbc_requests(engagement=eng, limit=1) and not db.list_report_runs(engagement=eng, limit=1):
        _admin_fail(admin, "external_engagement_granted", target, 404,
                    "No requests or report runs exist for that engagement label. Check the spelling.", "unknown_engagement")
    created = db.grant_engagement_access(eng, user["id"], user["username"], admin["username"])
    if created is None:
        _admin_fail(admin, "external_engagement_granted", target, 500, "Failed to save", "db_error")
    _log(admin, "external_engagement_granted", target, "success",
         {"engagement": eng, "auditor": user["username"], "already_granted": not created})
    return {"engagement": eng, "user_id": user["id"], "username": user["username"], "newly_granted": created}


@router.delete("/admin/access", summary="Remove an external auditor from an engagement (admin)")
def revoke_access(engagement: str = Query(...), user_id: int = Query(...), admin: dict = Depends(require_admin)):
    _need_db()
    eng = engagement.strip()
    removed = db.revoke_engagement_access(eng, user_id)
    if removed is None:
        _admin_fail(admin, "external_engagement_revoked", f"{eng}:{user_id}", 500, "Failed to save", "db_error")
    if not removed:
        _admin_fail(admin, "external_engagement_revoked", f"{eng}:{user_id}", 404, "No such assignment", "not_assigned")
    _log(admin, "external_engagement_revoked", f"{eng}:{user_id}", "success", {"engagement": eng, "user_id": user_id})
    return {"ok": True}
