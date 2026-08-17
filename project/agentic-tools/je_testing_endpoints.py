#!/usr/bin/env python3
"""
Journal Entry Testing API — deterministic anomaly rules over real GL data.

Unlike exceptions_endpoints.py (Exception Management, a Development-only
ML-uncertainty demo — see that module's docstring), JE Testing is a real,
always-on control and this router carries no environment gate: je_testing_sweep.py
populates the same exception_control_events/exception_model_inferences/
exception_auditor_triage tables (see db.py's "Journal Entry Testing" section)
in every environment, discriminated by event_type = 'JOURNAL_ENTRY'.

Router prefix: /je-testing

    GET  /je-testing/summary               Headline tiles: entries tested, findings by rule, top preparers
    GET  /je-testing/findings              Findings list (?rule_id=&system_source=&preparer=&only_pending=&limit=&offset=)
    POST /je-testing/findings/{id}/disposition   Record an auditor's resolution (same 4 labels as Exception Management)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

import db
from auth_endpoints import get_current_user, require_screen_permission

logger = logging.getLogger("ubo.je_testing")

router = APIRouter(
    prefix="/je-testing", tags=["Journal Entry Testing"],
    dependencies=[Depends(require_screen_permission("continuousmonitoring"))],
)

# Same 4-label resolution vocabulary exceptions_endpoints.py uses — a finding
# either was a real issue, noise, an approved exception, or a data problem,
# regardless of whether a deterministic rule or an ML model raised it.
_RESOLUTION_LABELS = [
    "TRUE_CONTROL_FAILURE", "BENIGN_OPERATIONAL_NOISE", "APPROVED_CARVE_OUT", "DATA_PIPELINE_ERROR",
]
_NOTES_REQUIRED_LABELS = {"TRUE_CONTROL_FAILURE", "APPROVED_CARVE_OUT"}


@router.get("/summary")
def get_summary():
    if not db.is_available():
        return {"total_findings": 0, "findings_by_rule": {}, "top_preparers": [], "pending_count": 0}
    return db.get_je_testing_summary()


@router.get("/findings")
def get_findings(
    rule_id: Optional[str] = None,
    system_source: Optional[str] = None,
    preparer: Optional[str] = None,
    only_pending: bool = False,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    if not db.is_available():
        return {"rows": [], "count": 0, "total": 0, "offset": offset, "resolution_labels": _RESOLUTION_LABELS}
    rows = db.list_je_testing_findings(
        limit=limit, offset=offset, rule_id=rule_id, system_source=system_source,
        preparer=preparer, only_pending=only_pending,
    )
    total = db.count_je_testing_findings(
        rule_id=rule_id, system_source=system_source, preparer=preparer, only_pending=only_pending,
    )
    return {"rows": rows, "count": len(rows), "total": total, "offset": offset,
            "resolution_labels": _RESOLUTION_LABELS}


@router.post("/findings/{event_id}/disposition")
def submit_disposition(
    event_id: int,
    body: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    resolution_label = body.get("resolution_label")
    notes = body.get("justification_notes")
    if resolution_label not in _RESOLUTION_LABELS:
        raise HTTPException(status_code=422, detail=f"resolution_label must be one of {_RESOLUTION_LABELS}")
    if resolution_label in _NOTES_REQUIRED_LABELS and not (notes or "").strip():
        raise HTTPException(status_code=422, detail=f"justification_notes is required for {resolution_label}")
    auditor = current_user.get("display_name") or current_user.get("username") or "unknown"
    # submit_exception_triage is generic over exception_control_events — a JE
    # finding's event_id lives in the exact same table Exception Management's
    # rows do, so no JE-specific persistence function is needed here.
    result = db.submit_exception_triage(event_id, auditor, resolution_label, notes)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No JE finding with event_id={event_id}")
    logger.info("JE Testing: event %s disposed as %s by %s", event_id, resolution_label, auditor)
    return result
