"""
Reporting subsystem, phase 0 API: discover datasets and run governed queries.

    GET  /reporting/datasets            datasets the caller may read
    GET  /reporting/datasets/{key}      one dataset's columns, types and labels
    POST /reporting/query               {"spec": {...}} -> rows + reperformance manifest

Access: the "reporting" screen permission (a blocklist like every other screen:
allowed unless an admin has switched it off for the user or role), plus
dataset-level restriction (admin_only datasets such as the audit log).
Queries run read-only with a statement timeout, on the request's tenant-bound
connection. Every run is written to the audit log, success or failure.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
import reporting_catalog as rc
import reporting_snapshot as rs
from auth_endpoints import get_current_user, require_screen_permission

logger = logging.getLogger("ubo.reporting")

router = APIRouter(prefix="/reporting", tags=["Reporting"],
                   dependencies=[Depends(require_screen_permission("reporting"))])

_STATEMENT_TIMEOUT_MS = 15_000


class QueryRequest(BaseModel):
    spec: Dict[str, Any]


def _is_admin(user: dict) -> bool:
    return user.get("role") == "admin"


def _audit(user: dict, dataset: str, outcome: str, detail: dict) -> None:
    try:
        db.insert_audit_log_entry("reporting", "report_query", actor=user.get("username"),
                                  target=dataset or None, detail=detail, outcome=outcome)
    except Exception as exc:  # audit must never block the query
        logger.warning("reporting audit write failed: %s", exc)


def execute(cq: rc.CompiledQuery) -> list[dict]:
    """Run a compiled query read-only with a timeout; return rows as dicts."""
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL transaction_read_only = on")
            cur.execute(f"SET LOCAL statement_timeout = {int(_STATEMENT_TIMEOUT_MS)}")
            cur.execute(cq.sql, cq.params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


@router.get("/datasets", summary="Datasets you can report on")
def list_datasets(user: dict = Depends(get_current_user)):
    return {"datasets": [d.describe() for d in rc.list_datasets(_is_admin(user))]}


@router.get("/datasets/{key}", summary="One dataset's schema")
def get_dataset(key: str, user: dict = Depends(get_current_user)):
    try:
        ds = rc.get_dataset(key)
    except rc.QueryError:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if ds.admin_only and not _is_admin(user):
        raise HTTPException(status_code=404, detail="Dataset not found")  # don't reveal restricted datasets
    return ds.describe()


@router.post("/query", summary="Run a governed query")
def run_query(req: QueryRequest, user: dict = Depends(get_current_user)):
    dataset_key = str(req.spec.get("dataset", "")) if isinstance(req.spec, dict) else ""
    try:
        cq = rc.compile_query(req.spec, is_admin=_is_admin(user))
    except rc.QueryError as exc:
        _audit(user, dataset_key[:64], "failure" if "restricted" not in str(exc) else "denied",
               {"reason": "invalid_query", "message": str(exc)[:200]})
        raise HTTPException(status_code=422, detail=str(exc))

    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not available")

    started = time.monotonic()
    try:
        rows = execute(cq)
    except Exception as exc:
        logger.warning("report query failed for %s: %s", cq.dataset.key, exc)
        _audit(user, cq.dataset.key, "failure", {"reason": "execution_error", "error": type(exc).__name__})
        raise HTTPException(status_code=500, detail="Query failed")

    manifest = rc.build_manifest(cq, rows, as_of=datetime.now(timezone.utc),
                                 truncated=len(rows) >= cq.spec["limit"])
    _audit(user, cq.dataset.key, "success",
           {"row_count": len(rows), "result_sha256": manifest["result_sha256"],
            "duration_ms": int((time.monotonic() - started) * 1000)})
    return {"columns": cq.output_columns, "rows": rows, "manifest": manifest}


# ── signed snapshots ────────────────────────────────────────────────────────

class SnapshotRequest(BaseModel):
    spec: Dict[str, Any]
    note: Optional[str] = None
    engagement: Optional[str] = None


def _snapshot_audit(user: dict, action: str, target: str, outcome: str, detail: dict) -> None:
    try:
        db.insert_audit_log_entry("reporting", action, actor=user.get("username"), target=target,
                                  detail=detail, outcome=outcome)
    except Exception as exc:
        logger.warning("reporting audit write failed: %s", exc)


def _snapshot_visible(snap: dict, user: dict) -> bool:
    """Snapshots of restricted datasets are readable only by admins; others see 404."""
    try:
        return _is_admin(user) or not rc.get_dataset(snap["dataset_key"]).admin_only
    except rc.QueryError:
        return _is_admin(user)


def _load_snapshot(snapshot_id: int, user: dict) -> dict:
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not available")
    snap = db.get_report_snapshot(snapshot_id)
    if not snap or not _snapshot_visible(snap, user):
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snap


@router.post("/snapshots", summary="Run a query and keep a signed, immutable copy of the result")
def create_snapshot(req: SnapshotRequest, user: dict = Depends(get_current_user)):
    dataset_key = str(req.spec.get("dataset", ""))[:64] if isinstance(req.spec, dict) else ""
    try:
        cq = rc.compile_query(req.spec, is_admin=_is_admin(user))
    except rc.QueryError as exc:
        _snapshot_audit(user, "report_snapshot_created", dataset_key, "failure" if "restricted" not in str(exc) else "denied",
                        {"reason": "invalid_query", "message": str(exc)[:200]})
        raise HTTPException(status_code=422, detail=str(exc))
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        rows = execute(cq)
    except Exception as exc:
        logger.warning("snapshot query failed for %s: %s", cq.dataset.key, exc)
        _snapshot_audit(user, "report_snapshot_created", cq.dataset.key, "failure",
                        {"reason": "execution_error", "error": type(exc).__name__})
        raise HTTPException(status_code=500, detail="Query failed")

    rows_json = rc.canonical_rows_json(rows)
    manifest = rc.build_manifest(cq, rows, as_of=datetime.now(timezone.utc),
                                 truncated=len(rows) >= cq.spec["limit"])
    try:
        record = rs.build_snapshot(manifest, rows_json, user.get("username", ""), req.note, req.engagement,
                                   sign=db.sign_evidence_record)
    except rs.SnapshotError as exc:
        _snapshot_audit(user, "report_snapshot_created", cq.dataset.key, "failure",
                        {"reason": "invalid_snapshot", "message": str(exc)})
        raise HTTPException(status_code=422, detail=str(exc))
    except db.EvidenceSigningKeyMissing as exc:
        _snapshot_audit(user, "report_snapshot_created", cq.dataset.key, "failure", {"reason": "signing_key_missing"})
        raise HTTPException(status_code=503, detail=str(exc))

    saved = db.insert_report_snapshot(record)
    if not saved:
        _snapshot_audit(user, "report_snapshot_created", cq.dataset.key, "failure", {"reason": "db_error"})
        raise HTTPException(status_code=500, detail="Failed to store snapshot")
    _snapshot_audit(user, "report_snapshot_created", str(saved["id"]), "success",
                    {"dataset": cq.dataset.key, "row_count": saved["row_count"],
                     "result_sha256": saved["result_sha256"], "engagement": record["engagement"]})
    return {**saved, "columns": cq.output_columns, "rows": rows, "manifest": manifest}


@router.get("/snapshots", summary="List snapshots (metadata only)")
def list_snapshots(dataset: Optional[str] = None, engagement: Optional[str] = None,
                   user: dict = Depends(get_current_user)):
    if not db.is_available():
        return {"snapshots": []}
    rows = [s for s in db.list_report_snapshots(dataset_key=dataset, engagement=engagement)
            if _snapshot_visible(s, user)]
    return {"snapshots": rows}


@router.get("/snapshots/{snapshot_id}", summary="One snapshot: manifest, rows and signature")
def get_snapshot(snapshot_id: int, user: dict = Depends(get_current_user)):
    snap = _load_snapshot(snapshot_id, user)
    payload = json.loads(snap.pop("payload_json"))
    rows = json.loads(snap.pop("rows_json"))
    return {**snap, "payload": payload, "rows": rows}


@router.get("/snapshots/{snapshot_id}/verify", summary="Check a snapshot's rows and signature")
def verify_snapshot(snapshot_id: int, user: dict = Depends(get_current_user)):
    snap = _load_snapshot(snapshot_id, user)
    try:
        result = rs.verify(snap, sign=db.sign_evidence_record)
    except db.EvidenceSigningKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    _snapshot_audit(user, "report_snapshot_verified", str(snapshot_id),
                    "success" if result["intact"] else "failure",
                    {"rows_match_hash": result["rows_match_hash"], "signature_valid": result["signature_valid"]})
    return {"id": snapshot_id, **result}


@router.post("/snapshots/{snapshot_id}/rerun", summary="Re-run a snapshot's query and compare with the snapshot")
def rerun_snapshot(snapshot_id: int, user: dict = Depends(get_current_user)):
    snap = _load_snapshot(snapshot_id, user)
    try:
        payload = json.loads(snap["payload_json"])
        cq = rc.compile_query(payload["manifest"]["spec"], is_admin=_is_admin(user))
        rows = execute(cq)
    except (rc.QueryError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=f"Cannot re-run this snapshot: {exc}")
    except Exception as exc:
        logger.warning("snapshot rerun failed: %s", exc)
        raise HTTPException(status_code=500, detail="Query failed")
    fresh_hash = hashlib.sha256(rc.canonical_rows_json(rows).encode("utf-8")).hexdigest()
    return {"id": snapshot_id, **rs.rerun_matches(payload, cq.sql, fresh_hash)}
