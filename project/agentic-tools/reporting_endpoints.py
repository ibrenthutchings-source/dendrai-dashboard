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

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
import reporting_catalog as rc
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
