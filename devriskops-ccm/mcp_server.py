#!/usr/bin/env python3
"""
DevRiskOps CCM — FastMCP Server for AI Agent Workflows.

Exposes the Continuous Control Monitoring triage queue and feature-drift
tooling to AI coding agents (VS Code, Claude, or any other MCP client) over
the Model Context Protocol. get_pending_exceptions and submit_triage_decision
proxy backend_api.py so an agent-submitted triage decision goes through the
exact same validation and retrain-trigger path a human auditor's Streamlit
submission does; check_feature_drift queries Postgres directly and reuses
psi_monitor.calculate_psi so its answer is computed by the identical
methodology app.py's PSI tab and dags/ccm_dag.py's drift task use.

Configuration (environment variables)
--------------------------------------
    CCM_BACKEND_API_URL     Default http://localhost:8000
    CCM_API_KEY               Must match backend_api.py's CCM_API_KEY
    DATABASE_URL                 postgresql://user:pass@host:port/dbname
    CCM_PSI_BASELINE_DAYS          Default 30 — check_feature_drift's default baseline window
    CCM_PSI_TARGET_DAYS               Default 7  — check_feature_drift's default target window

Run standalone:
    python mcp_server.py

Registered for VS Code in .vscode/settings.json.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx
import psycopg2
import psycopg2.extras
from mcp.server.fastmcp import FastMCP

from psi_monitor import calculate_psi, classify_psi, extract_feature_series

BACKEND_API_URL = os.environ.get("CCM_BACKEND_API_URL", "http://localhost:8000").rstrip("/")
CCM_API_KEY = os.environ.get("CCM_API_KEY", "dev-local-insecure-key-change-me")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ccm_user:ccm_password@localhost:5432/devriskops_ccm")
DEFAULT_BASELINE_WINDOW_DAYS = int(os.environ.get("CCM_PSI_BASELINE_DAYS", "30"))
DEFAULT_TARGET_WINDOW_DAYS = int(os.environ.get("CCM_PSI_TARGET_DAYS", "7"))
MIN_SAMPLES_PER_WINDOW = 5

mcp = FastMCP("DevRiskOps-CCM-Server")


def _api_headers() -> dict[str, str]:
    return {"X-API-Key": CCM_API_KEY}


def _get_db_connection() -> "psycopg2.extensions.connection":
    return psycopg2.connect(DATABASE_URL)


@mcp.tool()
def get_pending_exceptions(limit: int = 50, min_uncertainty: float = 0.0) -> list[dict[str, Any]]:
    """Fetch control-monitoring exceptions still awaiting human auditor
    review, ordered by uncertainty score (most ambiguous, and therefore the
    most valuable to label next, first).

    Args:
        limit: Maximum number of items to return (1-1000).
        min_uncertainty: Only return items with uncertainty_score >= this value (0.0-1.0).

    Returns:
        A list of pending triage items, each with event_id, control_id,
        system_source, event_timestamp, point_in_time_features,
        model_version, anomaly_score, and uncertainty_score. An empty list
        means the queue is clear (never an error condition on its own).
    """
    with httpx.Client(timeout=15.0) as client:
        response = client.get(
            f"{BACKEND_API_URL}/api/v1/triage/pending",
            params={"limit": limit, "min_uncertainty": min_uncertainty},
            headers=_api_headers(),
        )
        response.raise_for_status()
        return response.json()


@mcp.tool()
def submit_triage_decision(event_id: str, auditor_id: str, label: str, notes: str = "") -> dict[str, Any]:
    """Submit an auditor's (human or agent) ground-truth resolution for a
    pending control exception. This is the label active-learning retraining
    is driven from — see dags/ccm_dag.py's evaluate_retraining_trigger.

    Args:
        event_id: The control_events.event_id (UUID string) being resolved.
        auditor_id: Identifier of the human or agent submitting the decision.
        label: One of TRUE_CONTROL_FAILURE, BENIGN_OPERATIONAL_NOISE,
            APPROVED_CARVE_OUT, DATA_PIPELINE_ERROR.
        notes: Justification notes. Required by the backend when label is
            TRUE_CONTROL_FAILURE or APPROVED_CARVE_OUT — omitting them for
            those two labels returns an error dict rather than persisting.

    Returns:
        The persisted triage record (triage_id, event_id, resolution_label,
        reviewed_at, retrain_evaluation_scheduled) on success, or
        {"error": True, "status_code": ..., "detail": ...} if the backend
        rejected the submission.
    """
    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"{BACKEND_API_URL}/api/v1/triage/{event_id}",
            json={"auditor_id": auditor_id, "resolution_label": label, "justification_notes": notes or None},
            headers=_api_headers(),
        )
        if response.status_code >= 400:
            detail: Any = response.text
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            return {"error": True, "status_code": response.status_code, "detail": detail}
        return response.json()


@mcp.tool()
def check_feature_drift(
    feature_name: str,
    baseline_window_days: int = DEFAULT_BASELINE_WINDOW_DAYS,
    target_window_days: int = DEFAULT_TARGET_WINDOW_DAYS,
) -> dict[str, Any]:
    """Compute the Population Stability Index (PSI) for one telemetry
    feature, comparing a recent 'target' window against an earlier
    'baseline' window of control_events.point_in_time_features, and
    classify the drift severity.

    Args:
        feature_name: The key inside point_in_time_features to analyze.
        baseline_window_days: Length of the baseline window, ending target_window_days ago.
        target_window_days: Length of the most-recent target window, ending now.

    Returns:
        A dict with feature_name, psi_score, severity (STABLE/WARNING/CRITICAL),
        sample sizes, and the windows used — or {"error": True, "detail": ...}
        if there isn't enough data in either window (fewer than 5 samples).
    """
    total_window_days = baseline_window_days + target_window_days
    query = """
        SELECT point_in_time_features, event_timestamp
        FROM control_events
        WHERE event_timestamp >= now() - (%s || ' days')::interval
    """

    conn = _get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (total_window_days,))
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return {"error": True, "detail": f"No control_events found in the trailing {total_window_days} days."}

    cutoff_epoch = datetime.now(timezone.utc).timestamp() - (target_window_days * 86400)
    baseline_events = [r["point_in_time_features"] or {} for r in rows if r["event_timestamp"].timestamp() < cutoff_epoch]
    target_events = [r["point_in_time_features"] or {} for r in rows if r["event_timestamp"].timestamp() >= cutoff_epoch]

    baseline_arr = extract_feature_series(baseline_events, feature_name)
    target_arr = extract_feature_series(target_events, feature_name)

    if baseline_arr.size < MIN_SAMPLES_PER_WINDOW or target_arr.size < MIN_SAMPLES_PER_WINDOW:
        return {
            "error": True,
            "detail": (
                f"Insufficient samples for '{feature_name}': "
                f"baseline={baseline_arr.size}, target={target_arr.size} "
                f"(need >= {MIN_SAMPLES_PER_WINDOW} each)."
            ),
        }

    psi_score, _ = calculate_psi(baseline_arr, target_arr, num_bins=10)
    severity = classify_psi(psi_score)

    return {
        "feature_name": feature_name,
        "psi_score": round(psi_score, 4),
        "severity": severity.value,
        "baseline_sample_size": int(baseline_arr.size),
        "target_sample_size": int(target_arr.size),
        "baseline_window_days": baseline_window_days,
        "target_window_days": target_window_days,
    }


if __name__ == "__main__":
    mcp.run()
