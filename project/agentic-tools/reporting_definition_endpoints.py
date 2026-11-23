"""
Report-as-Code API: versioned report definitions, approval, preview, signed runs.

Router prefix: /reporting/definitions   (same "reporting" screen permission as /reporting)

    GET  /                               latest version of each report
    PUT  /{key}                          {"definition": {...}} -> saves a NEW draft version
    GET  /{key}                          version history
    GET  /{key}/versions/{v}             one version, including its definition
    POST /{key}/versions/{v}/approve     admin; approver must differ from the author; narrative required
    POST /{key}/versions/{v}/retire      admin
    POST /{key}/preview                  run any version without keeping a record (HTML + data)
    POST /{key}/run                      run an APPROVED version and keep a signed, immutable run record
    GET  /runs                           run records (metadata)
    GET  /runs/{id}                      stored definition, parameters, manifests and rows
    GET  /runs/{id}/verify               signature, row hashes and embedded definition
    GET  /runs/{id}/html                 the report rendered from the stored rows (not re-queried)
    POST /runs/{id}/rerun                fresh run of the stored definition, compared block by block

Definitions or runs that read an admin-only dataset (such as the audit log) are
visible and runnable by admins only; others get 404. Everything that changes
state or checks integrity is audit-logged (category "reporting"), success or failure.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import db
import reporting_definitions as rd
import reporting_endpoints as re_
from auth_endpoints import get_current_user, require_screen_permission

logger = logging.getLogger("ubo.reporting.definitions")

router = APIRouter(prefix="/reporting/definitions", tags=["Reporting"],
                   dependencies=[Depends(require_screen_permission("reporting"))])


class SaveIn(BaseModel):
    definition: Dict[str, Any]


class RunIn(BaseModel):
    version: Optional[int] = None
    parameters: Optional[Dict[str, Any]] = None
    note: Optional[str] = None
    engagement: Optional[str] = None


def _admin(user: dict) -> bool:
    return user.get("role") == "admin"


def _audit(user: dict, action: str, target: str, outcome: str, detail: dict) -> None:
    try:
        db.insert_audit_log_entry("reporting", action, actor=user.get("username"), target=target,
                                  detail=detail, outcome=outcome)
    except Exception as exc:
        logger.warning("reporting audit write failed: %s", exc)


def _fail(user: dict, action: str, target: str, status: int, message: str, reason: str, outcome: str = "failure"):
    _audit(user, action, target, outcome, {"reason": reason, "http_status": status})
    raise HTTPException(status_code=status, detail=message)


def _need_db():
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not available")


def _visible(row: Optional[dict], user: dict) -> bool:
    return bool(row) and (_admin(user) or not row.get("restricted"))


def _get_version(key: str, version: Optional[int], user: dict, approved_only: bool = False) -> dict:
    _need_db()
    row = db.get_report_definition_version(key, version, approved_only=approved_only)
    if not _visible(row, user):
        raise HTTPException(status_code=404, detail="Report version not found")
    return row


def _sign_guard(fn):
    try:
        return fn()
    except db.EvidenceSigningKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ── runs (static paths first so "runs" is never read as a report key) ───────

def _get_run(run_id: int, user: dict) -> dict:
    _need_db()
    run = db.get_report_run(run_id)
    if not _visible(run, user):
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs", summary="Run records (metadata)")
def list_runs(definition_key: Optional[str] = None, engagement: Optional[str] = None,
              user: dict = Depends(get_current_user)):
    if not db.is_available():
        return {"runs": []}
    return {"runs": [r for r in db.list_report_runs(definition_key, engagement) if _visible(r, user)]}


@router.get("/runs/{run_id}", summary="A stored run: definition, parameters, manifests and rows")
def get_run(run_id: int, user: dict = Depends(get_current_user)):
    run = _get_run(run_id, user)
    payload = json.loads(run.pop("payload_json"))
    rows = json.loads(run.pop("rows_json"))
    return {**run, "payload": payload, "rows": rows}


@router.get("/runs/{run_id}/verify", summary="Check a run's signature, row hashes and definition")
def verify_run(run_id: int, user: dict = Depends(get_current_user)):
    run = _get_run(run_id, user)
    result = _sign_guard(lambda: rd.verify_run(run, sign=db.sign_evidence_record))
    _audit(user, "report_run_verified", str(run_id), "success" if result["intact"] else "failure",
           {k: result[k] for k in ("signature_valid", "rows_match_hash", "blocks_match", "definition_matches")})
    return {"id": run_id, **result}


@router.get("/runs/{run_id}/html", response_class=HTMLResponse, summary="Render a stored run")
def run_html(run_id: int, user: dict = Depends(get_current_user)):
    run = _get_run(run_id, user)
    payload, rows = json.loads(run["payload_json"]), json.loads(run["rows_json"])
    results = {bid: {"columns": payload["columns"][bid], "rows": rows.get(bid, [])} for bid in payload["manifests"]}
    d = payload["definition"]
    return HTMLResponse(rd.render_html(d["definition"], payload["parameters"], results, {
        "Report": f"{d['key']} v{d['version']}", "Run": run_id, "As of": payload["as_of"],
        "Run by": payload["created_by"], "Engagement": payload.get("engagement"), "Note": payload.get("note"),
        "Result SHA-256": payload["result_sha256"], "Definition SHA-256": d["sha256"]}))


@router.post("/runs/{run_id}/rerun", summary="Re-run a stored run's definition and compare")
def rerun(run_id: int, user: dict = Depends(get_current_user)):
    run = _get_run(run_id, user)
    payload = json.loads(run["payload_json"])
    defn = payload["definition"]["definition"]
    try:
        ran = rd.run_blocks(defn, payload["parameters"], _admin(user), re_.execute, datetime.now(timezone.utc))
    except rd.DefinitionError as exc:
        raise HTTPException(status_code=409, detail=f"Cannot re-run: {exc}")
    except Exception as exc:
        logger.warning("report rerun failed: %s", exc)
        raise HTTPException(status_code=500, detail="Query failed")
    return {"id": run_id, **rd.compare_rerun(payload, ran["manifests"])}


# ── definitions ─────────────────────────────────────────────────────────────

@router.get("/", summary="Latest version of each report")
def list_definitions(user: dict = Depends(get_current_user)):
    if not db.is_available():
        return {"definitions": []}
    return {"definitions": [d for d in db.list_report_definitions() if _visible(d, user)]}


@router.put("/{key}", summary="Save a new draft version of a report")
def save_definition(key: str, body: SaveIn, user: dict = Depends(get_current_user)):
    _need_db()
    try:
        key = rd.check_key(key)
        defn = rd.validate_definition(body.definition, is_admin=_admin(user))
    except rd.DefinitionError as exc:
        _fail(user, "report_definition_saved", key[:64], 422, str(exc), "invalid_definition",
              "denied" if "restricted" in str(exc) else "failure")
    existing = db.get_report_definition_version(key)
    if existing and not _visible(existing, user):
        raise HTTPException(status_code=404, detail="Report not found")
    restricted = rd.is_restricted(defn)
    saved = db.insert_report_definition_version(key, defn["title"], rd.canonical(defn), rd.definition_hash(defn),
                                                user.get("username", ""), restricted)
    if not saved:
        _fail(user, "report_definition_saved", key, 409, "Could not save (a concurrent edit?). Retry.", "save_failed")
    _audit(user, "report_definition_saved", f"{key}@{saved['version']}", "success",
           {"sha256": saved["definition_sha256"], "restricted": restricted})
    return {**saved, "missing_narrative": rd.missing_narrative(defn)}


@router.get("/{key}", summary="Version history")
def history(key: str, user: dict = Depends(get_current_user)):
    _need_db()
    versions = [v for v in db.list_report_definition_versions(key) if _visible(v, user)]
    if not versions:
        raise HTTPException(status_code=404, detail="Report not found")
    return {"key": key, "versions": versions}


@router.get("/{key}/versions/{version}", summary="One version, with its definition")
def get_version(key: str, version: int, user: dict = Depends(get_current_user)):
    row = _get_version(key, version, user)
    return {**{k: v for k, v in row.items() if k != "definition_json"}, "definition": json.loads(row["definition_json"])}


@router.post("/{key}/versions/{version}/approve", summary="Approve a draft version")
def approve(key: str, version: int, user: dict = Depends(get_current_user)):
    target = f"{key}@{version}"
    if not _admin(user):
        _fail(user, "report_definition_approved", target, 403, "Admin access required", "not_admin", "denied")
    row = _get_version(key, version, user)
    if row["status"] != "draft":
        _fail(user, "report_definition_approved", target, 409, f"Only a draft can be approved (this is {row['status']})",
              "invalid_state")
    if row["created_by"].lower() == user.get("username", "").lower():
        _fail(user, "report_definition_approved", target, 403,
              "The approver must be different from the author", "not_independent", "denied")
    missing = rd.missing_narrative(json.loads(row["definition_json"]))
    if missing:
        _fail(user, "report_definition_approved", target, 422,
              "Before approval the report must state its " + ", ".join(missing) +
              " so an independent reviewer can reperform the work", "narrative_missing")
    updated = db.set_report_definition_status(key, version, "approved", user.get("username", ""))
    if not updated:
        _fail(user, "report_definition_approved", target, 409, "Version is no longer a draft", "state_changed")
    _audit(user, "report_definition_approved", target, "success", {"sha256": updated["definition_sha256"]})
    return updated


@router.post("/{key}/versions/{version}/retire", summary="Retire a version (it can no longer be run)")
def retire(key: str, version: int, user: dict = Depends(get_current_user)):
    target = f"{key}@{version}"
    if not _admin(user):
        _fail(user, "report_definition_retired", target, 403, "Admin access required", "not_admin", "denied")
    _get_version(key, version, user)
    updated = db.set_report_definition_status(key, version, "retired", user.get("username", ""))
    if not updated:
        _fail(user, "report_definition_retired", target, 409, "Version is already retired", "invalid_state")
    _audit(user, "report_definition_retired", target, "success", {})
    return updated


# ── preview and run ─────────────────────────────────────────────────────────

def _execute_report(row: dict, params_in: Optional[dict], user: dict):
    """Resolve parameters and run every block. Returns (definition, params, ran, as_of)."""
    defn = json.loads(row["definition_json"])
    try:
        params = rd.resolve_params(defn, params_in)
        as_of = datetime.now(timezone.utc)
        ran = rd.run_blocks(defn, params, _admin(user), re_.execute, as_of)
    except rd.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("report execution failed (%s): %s", row.get("definition_key"), exc)
        raise HTTPException(status_code=500, detail="Query failed")
    return defn, params, ran, as_of


@router.post("/{key}/preview", summary="Run a version without keeping a record")
def preview(key: str, body: RunIn, user: dict = Depends(get_current_user)):
    row = _get_version(key, body.version, user)
    defn, params, ran, as_of = _execute_report(row, body.parameters, user)
    page = rd.render_html(defn, params, ran["results"], {
        "Report": f"{key} v{row['version']} (preview, not a record)", "As of": as_of.isoformat()})
    return {"version": row["version"], "status": row["status"], "parameters": params,
            "results": ran["results"], "html": page, "missing_narrative": rd.missing_narrative(defn)}


@router.post("/{key}/run", summary="Run an approved version and keep a signed record")
def run(key: str, body: RunIn, user: dict = Depends(get_current_user)):
    _need_db()
    row = db.get_report_definition_version(key, body.version, approved_only=True)
    if not _visible(row, user):
        _fail(user, "report_run", key[:64], 404, "No approved version of this report", "no_approved_version")
    try:
        defn, params, ran, as_of = _execute_report(row, body.parameters, user)
    except HTTPException as exc:
        _audit(user, "report_run", f"{key}@{row['version']}", "failure",
               {"reason": "execution_error" if exc.status_code == 500 else "invalid_parameters"})
        raise
    record = _sign_guard(lambda: rd.build_run(key, row["version"], defn, params, ran, as_of,
                                              user.get("username", ""), body.note, body.engagement,
                                              sign=db.sign_evidence_record))
    record["restricted"] = bool(row.get("restricted"))
    saved = db.insert_report_run(record)
    if not saved:
        _fail(user, "report_run", f"{key}@{row['version']}", 500, "Failed to store the run", "db_error")
    _audit(user, "report_run", str(saved["id"]), "success",
           {"report": key, "version": row["version"], "result_sha256": saved["result_sha256"],
            "engagement": saved.get("engagement")})
    page = rd.render_html(defn, params, ran["results"], {
        "Report": f"{key} v{row['version']}", "Run": saved["id"], "As of": as_of.isoformat(),
        "Run by": user.get("username"), "Engagement": saved.get("engagement"), "Note": saved.get("note"),
        "Result SHA-256": saved["result_sha256"], "Definition SHA-256": row["definition_sha256"]})
    return {**saved, "parameters": params, "results": ran["results"], "html": page}
