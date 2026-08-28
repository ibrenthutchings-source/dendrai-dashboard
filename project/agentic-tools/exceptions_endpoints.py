#!/usr/bin/env python3
"""
Exception Management API — Continuous Control Monitoring triage.

Ported from devriskops-ccm (a standalone Streamlit + FastAPI + Airflow
service — app.py/backend_api.py/mcp_server.py/dags/ccm_dag.py — that was
committed to this repo but never wired into the main React dashboard) into
this app's own DB, auth, and connector infrastructure instead of a separate
service+database. Was restricted to the Development Railway environment
only while the feature was still settling; lifted once the board/executive
reporting screen needed real production exception data (see GET
/exceptions/report below) — the underlying triage/scoring pipeline was
already stable, the gate had simply never been revisited.

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

    GET  /exceptions/pending             Events awaiting triage, risk_rating then uncertainty first
                                          (?group=true collapses recurring control_id/system_source
                                          pairs into one row each — see list_pending_exceptions_grouped)
    POST /exceptions/bulk-triage         Resolve every pending event for one control_id/system_source at once
    POST /exceptions/{event_id}/triage   Record an auditor's resolution for one event
    GET  /exceptions/summary             Headline counts (pending, resolution mix, by system/owner/risk_rating)
    GET  /exceptions/history             Resolved triage decisions (Model Analytics tab)
    GET  /exceptions/drift-summary       Live PSI per system_source x {anomaly_score, uncertainty_score}
    GET  /exceptions/report              Board/executive period report — summary + by-control $ impact + drill-down

Curation/risk-rating/delegation (added after volume review — see
exception_tool.py's module docstring for the risk_rating design):
pending items can be filtered by risk_rating (R/A/G) and by assigned_owner
(snapshotted from poll_connectors.system_owner at scoring time — see
connector_poller._score_exception_event), and grouped by (control_id,
system_source) so a reviewer can bulk-resolve an entire recurring pattern
instead of triaging one event at a time.

Drift incidents/baseline resets for THIS screen's metric_kind="exception"
rows are read/written via the existing /model-health/drift-incidents and
/model-health/baseline-reset endpoints — see exceptions.jsx.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

import db
import fair_tool
from auth_endpoints import get_current_user, require_screen_permission

logger = logging.getLogger("ubo.exceptions")

router = APIRouter(
    prefix="/exceptions", tags=["Exception Management"],
    dependencies=[Depends(require_screen_permission("exceptions"))],
)

_RESOLUTION_LABELS = [
    "TRUE_CONTROL_FAILURE", "BENIGN_OPERATIONAL_NOISE", "APPROVED_CARVE_OUT", "DATA_PIPELINE_ERROR",
]
_NOTES_REQUIRED_LABELS = {"TRUE_CONTROL_FAILURE", "APPROVED_CARVE_OUT"}


@router.get("/pending")
def get_pending(
    limit: int = Query(100, ge=1, le=1000), min_uncertainty: float = Query(0.0, ge=0.0, le=1.0),
    risk_rating: Optional[str] = None, owner: Optional[str] = None, group: bool = False,
):
    if not db.is_available():
        return {"rows": [], "count": 0, "resolution_labels": _RESOLUTION_LABELS}
    if group:
        rows = db.list_pending_exceptions_grouped(limit=limit, risk_rating=risk_rating, owner=owner)
    else:
        rows = db.list_pending_exceptions(limit=limit, min_uncertainty=min_uncertainty,
                                           risk_rating=risk_rating, owner=owner)
    return {"rows": rows, "count": len(rows), "resolution_labels": _RESOLUTION_LABELS}


@router.post("/bulk-triage")
def submit_bulk_triage(
    body: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Resolves every currently-pending event for one (control_id,
    system_source) pair in a single action — the curation lever behind the
    grouped Triage Queue view. Same validation as the single-event endpoint."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    control_id = body.get("control_id")
    system_source = body.get("system_source")
    resolution_label = body.get("resolution_label")
    notes = body.get("justification_notes")
    if not control_id or not system_source:
        raise HTTPException(status_code=422, detail="control_id and system_source are required")
    if resolution_label not in _RESOLUTION_LABELS:
        raise HTTPException(status_code=422, detail=f"resolution_label must be one of {_RESOLUTION_LABELS}")
    if resolution_label in _NOTES_REQUIRED_LABELS and not (notes or "").strip():
        raise HTTPException(status_code=422, detail=f"justification_notes is required for {resolution_label}")
    auditor = current_user.get("display_name") or current_user.get("username") or "unknown"
    pending = db.list_pending_exceptions(limit=1000)
    event_ids = [r["event_id"] for r in pending if r["control_id"] == control_id and r["system_source"] == system_source]
    if not event_ids:
        raise HTTPException(status_code=404, detail=f"No pending exceptions for control_id={control_id}, system_source={system_source}")
    resolved = db.bulk_submit_exception_triage(event_ids, auditor, resolution_label, notes)
    logger.info("Exception Management: bulk-triaged %d event(s) for %s/%s as %s by %s",
                resolved, control_id, system_source, resolution_label, auditor)
    return {"control_id": control_id, "system_source": system_source, "resolved_count": resolved}


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
        return {"pending_count": 0, "total_events": 0, "resolution_mix": {}, "pending_by_system": {},
                 "pending_by_owner": {}, "pending_by_risk_rating": {}}
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


# ── Board/executive period report ────────────────────────────────────────────

def _parse_report_dates(date_from: str, date_to: str) -> tuple[date, date]:
    try:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(status_code=422, detail="date_from/date_to must be YYYY-MM-DD")
    if d_to < d_from:
        raise HTTPException(status_code=422, detail="date_to must be on or after date_from")
    return d_from, d_to


@router.get("/report")
def get_exceptions_report(
    date_from: str = Query(..., description="YYYY-MM-DD, inclusive"),
    date_to: str = Query(..., description="YYYY-MM-DD, inclusive"),
):
    """Board/executive period report: every exception in [date_from, date_to]
    grouped by (control_id, system_source, process), with a $ impact per
    group and headline totals. Deliberately not scoped to requires_human_
    review/untriaged like /pending — a period report covers everything that
    happened, triaged or not, JE Testing included (see
    db.list_exceptions_report_grouped's docstring).

    $ impact per group: the literal transaction amount when one was captured
    (JE Testing findings carry a real dollar amount in
    point_in_time_features), otherwise a FAIR (Factor Analysis of
    Information Risk) Monte Carlo estimate — same engine as POST
    /fair/quantify — using this group's occurrence count as the period's
    threat-event frequency. A group with a mix of priced and unpriced
    occurrences reports the literal sum only, flagged partial, rather than
    silently blending a real dollar figure with a modeled one.
    """
    d_from, d_to = _parse_report_dates(date_from, date_to)
    if not db.is_available():
        return {
            "period": {"date_from": date_from, "date_to": date_to},
            "summary": {"total_occurrences": 0, "total_impact_usd": 0, "controls_affected": 0,
                        "by_system": {}, "by_process": {}, "by_risk_rating": {}},
            "by_control": [],
        }

    groups = db.list_exceptions_report_grouped(date_from, date_to)
    window_days = max(1, (d_to - d_from).days + 1)

    by_control = []
    total_impact = 0.0
    total_occurrences = 0
    by_system: Dict[str, int] = {}
    by_process: Dict[str, int] = {}
    by_risk_rating: Dict[str, int] = {}

    for g in groups:
        count = g["occurrence_count"]
        total_occurrences += count
        by_system[g["system_source"]] = by_system.get(g["system_source"], 0) + count
        by_process[g["process"]] = by_process.get(g["process"], 0) + count
        rating_label = g["worst_risk_rating"] or "unrated"
        by_risk_rating[rating_label] = by_risk_rating.get(rating_label, 0) + count

        literal_total = g["literal_amount_total"]
        unpriced = g["unpriced_count"]
        if unpriced == 0:
            impact_usd, impact_source = literal_total, "transaction_amount"
        elif unpriced == count:
            # No occurrence in this group carries a literal amount — estimate
            # the whole group's exposure via FAIR, never invent a number.
            fair = fair_tool.quantify(fire_count_window=count, window_days=window_days)
            impact_usd, impact_source = fair["ale"], "fair_estimate"
        else:
            impact_usd, impact_source = literal_total, "transaction_amount_partial"

        total_impact += impact_usd
        by_control.append({
            **g,
            "impact_usd": round(impact_usd, 2),
            "impact_source": impact_source,
        })

    return {
        "period": {"date_from": date_from, "date_to": date_to},
        "summary": {
            "total_occurrences": total_occurrences,
            "total_impact_usd": round(total_impact, 2),
            "controls_affected": len(groups),
            "by_system": by_system,
            "by_process": by_process,
            "by_risk_rating": by_risk_rating,
        },
        "by_control": by_control,
    }


@router.get("/report/detail")
def get_exceptions_report_detail(
    date_from: str = Query(..., description="YYYY-MM-DD, inclusive"),
    date_to: str = Query(..., description="YYYY-MM-DD, inclusive"),
    control_id: Optional[str] = None,
):
    """Row-level drill-down for /report — every individual occurrence in the
    period, optionally scoped to one control_id (the group a user clicked
    into from the report's summary table)."""
    _parse_report_dates(date_from, date_to)
    if not db.is_available():
        return {"events": []}
    return {"events": db.list_exceptions_report_detail(date_from, date_to, control_id)}
