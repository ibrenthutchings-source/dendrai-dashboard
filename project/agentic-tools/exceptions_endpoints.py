#!/usr/bin/env python3
"""
Exception Management API — Continuous Control Monitoring triage.

Ported from devriskops-ccm (a standalone Streamlit + FastAPI + Airflow
service — app.py/backend_api.py/mcp_server.py/dags/ccm_dag.py — that was
committed to this repo but never wired into the main React dashboard) into
this app's own DB, auth, and connector infrastructure instead of a separate
service+database. Deliberately restricted to the Development Railway
environment only (deploy_env.py) — this is a new, still-settling feature,
not yet meant to reach UAT/Sandbox/Production.

Data model (db.py): exception_control_events (what happened) ->
exception_model_inferences (the anomaly/uncertainty score assigned to it) ->
exception_auditor_triage (a human's final call). Ingestion happens inside
connector_poller.py's per-event loop (exception_tool.score_event) —
"exceptions" here are scored connector events (real adapters and the
synthetic transaction simulator alike) that already flow through Continuous
Watch and Policy-as-Code, not a separately-fabricated stream.

Feature drift (population stability of anomaly_score/uncertainty_score per
system_source over time) reuses drift_tool.compute_psi verbatim and the
EXISTING Model Health drift-incident lifecycle (db.create_drift_incident /
list_drift_incidents / update_drift_incident / baseline resets — see
api_server.py's /model-health/drift-incidents and /model-health/baseline-reset
endpoints) with metric_kind="exception". No separate incident-tracking
backend was built for this screen.

Router prefix: /exceptions

    GET  /exceptions/pending             Events awaiting triage, highest uncertainty first
    POST /exceptions/{event_id}/triage   Record an auditor's resolution
    GET  /exceptions/summary             Headline counts (pending, resolution mix, by system)
    GET  /exceptions/history             Resolved triage decisions (Model Analytics tab)
    GET  /exceptions/drift-summary       Live PSI per system_source x {anomaly_score, uncertainty_score}

Drift incidents/baseline resets for THIS screen's metric_kind="exception"
rows are read/written via the existing /model-health/drift-incidents and
/model-health/baseline-reset endpoints — see exceptions.jsx.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

import db
import deploy_env
from auth_endpoints import get_current_user, require_screen_permission

logger = logging.getLogger("ubo.exceptions")


def _require_dev_environment() -> None:
    """Exception Management is a Development-only feature (still settling —
    see the module docstring). 404, not 403: a UAT/Sandbox/Production caller
    shouldn't be able to tell the feature exists at all, same as a route
    that was never registered."""
    if not deploy_env.IS_DEVELOPMENT:
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(
    prefix="/exceptions", tags=["Exception Management"],
    dependencies=[Depends(_require_dev_environment), Depends(require_screen_permission("exceptions"))],
)

_RESOLUTION_LABELS = [
    "TRUE_CONTROL_FAILURE", "BENIGN_OPERATIONAL_NOISE", "APPROVED_CARVE_OUT", "DATA_PIPELINE_ERROR",
]
_NOTES_REQUIRED_LABELS = {"TRUE_CONTROL_FAILURE", "APPROVED_CARVE_OUT"}


@router.get("/pending")
def get_pending(limit: int = Query(100, ge=1, le=1000), min_uncertainty: float = Query(0.0, ge=0.0, le=1.0)):
    if not db.is_available():
        return {"rows": [], "count": 0, "resolution_labels": _RESOLUTION_LABELS}
    rows = db.list_pending_exceptions(limit=limit, min_uncertainty=min_uncertainty)
    return {"rows": rows, "count": len(rows), "resolution_labels": _RESOLUTION_LABELS}


@router.post("/{event_id}/triage")
def submit_triage(
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
    result = db.submit_exception_triage(event_id, auditor, resolution_label, notes)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No pending exception with event_id={event_id}")
    logger.info("Exception Management: event %s triaged as %s by %s", event_id, resolution_label, auditor)
    return result


@router.get("/summary")
def get_summary():
    if not db.is_available():
        return {"pending_count": 0, "total_events": 0, "resolution_mix": {}, "pending_by_system": {}}
    return db.get_exception_summary()


@router.get("/history")
def get_history(limit: int = Query(200, ge=1, le=1000)):
    if not db.is_available():
        return {"rows": []}
    return {"rows": db.list_exception_triage_history(limit=limit)}


_DRIFT_METRICS = ("anomaly_score", "uncertainty_score")
_DRIFT_SPLIT_LAST_N = 30  # "current" window size — see compute_exception_drift


def compute_exception_drift(baseline_resets: Optional[dict] = None) -> list:
    """PSI on each system_source's anomaly_score/uncertainty_score
    distribution, split by recency count (last _DRIFT_SPLIT_LAST_N scored
    events = "current", everything before = "baseline") — the same
    recency-count split drift_tool.compute_ai_acceptance_drift uses for
    event streams with no notion of a "run" to split on. Reuses
    drift_tool.compute_psi/_flag verbatim; only the baseline/current split
    and a smaller bucket/sample floor (this data accumulates far slower than
    financial-ratio history) are specific to this screen. A baseline reset
    for a metric_key drops all pre-reset history from "baseline" — same
    "this is the new normal" intent as drift_tool's own resets, one fewer
    moving part since these events carry no separate reset timestamp filter."""
    import drift_tool

    baseline_resets = baseline_resets or {}
    results = []
    for system_source in db.list_exception_system_sources():
        for metric in _DRIFT_METRICS:
            metric_key = f"exception_{system_source}_{metric}"[:128]
            series = db.get_exception_score_history(system_source, metric)
            if len(series) <= _DRIFT_SPLIT_LAST_N:
                baseline_vals, current_vals = [], series
            else:
                baseline_vals = series[:-_DRIFT_SPLIT_LAST_N]
                current_vals = series[-_DRIFT_SPLIT_LAST_N:]
                if baseline_resets.get(metric_key):
                    baseline_vals = []
            psi = (
                drift_tool.compute_psi(baseline_vals, current_vals, buckets=5, min_bucket_samples=3)
                if baseline_vals else None
            )
            results.append({
                "system_source": system_source, "metric": metric, "metric_key": metric_key,
                "psi": psi, "flag": drift_tool._flag(psi),
                "n_baseline": len(baseline_vals), "n_current": len(current_vals),
            })
    return results


@router.get("/drift-summary")
def get_drift_summary():
    if not db.is_available():
        return {"rows": []}
    baseline_resets = db.get_baseline_resets()
    return {"rows": compute_exception_drift(baseline_resets)}


def check_exception_drift_once() -> list:
    """Same open-a-tracked-incident-on-drift pattern as
    api_server._check_model_health_drift_once, reusing the identical
    model_health_drift_incidents table/functions with metric_kind="exception"
    — called from api_server.py's existing 6h model_health_drift_watch loop
    (dev-gated there), not a separate background task."""
    if not db.is_available():
        return []
    alerted = []
    baseline_resets = db.get_baseline_resets()
    for entry in compute_exception_drift(baseline_resets):
        if entry.get("flag") != "drift":
            continue
        metric_key = entry["metric_key"]
        if db.get_open_drift_incident(metric_key):
            continue
        incident_id = db.create_drift_incident(
            metric_key, "exception", entry.get("psi"), entry.get("n_baseline"), entry.get("n_current"), detail=entry,
        )
        alerted.append({"metric": metric_key, "incident_id": incident_id, **entry})
        logger.info("Exception Management: drift incident #%s opened for %s", incident_id, metric_key)
    return alerted
