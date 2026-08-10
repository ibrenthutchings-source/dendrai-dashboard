#!/usr/bin/env python3
"""
DevRiskOps CCM — Apache Airflow TaskFlow Pipeline.

Daily at 01:00 UTC: score 100% of staged telemetry, monitor feature drift,
evaluate whether accumulated labels/drift warrant a retrain, and — only
when warranted — retrain, shadow-evaluate, and canary-promote.

    score_100pct_population
            |
            v
    monitor_feature_drift
            |
            v
    evaluate_retraining_trigger
            |
            v
    trigger_model_retrain   (skipped, not failed, when not triggered)

Reference scorer
-----------------
No trained model artifact is assumed to exist on day one, so scoring uses a
transparent, fully-implemented statistical baseline: per-control_id,
per-feature z-scores against a rolling mean/std, squashed through a
logistic function centered at z=3 for anomaly_score, and the standard
"least confidence" active-learning margin heuristic
(1 - 2*|anomaly_score - 0.5|) for uncertainty_score. trigger_model_retrain
recomputes these baseline statistics from newly-labeled ground truth and
promotes the result only if it beats the incumbent on precision, recall,
and false-positive rate against the same labeled set (shadow evaluation).
_score_record is the single seam a real trained classifier would replace
this reference implementation through — every other task is agnostic to
how a score was produced.

Configuration (environment variables)
--------------------------------------
    DATABASE_URL                  postgresql://user:pass@host:port/dbname
    CCM_BACKEND_API_URL           Default http://localhost:8000
    CCM_API_KEY                     Must match backend_api.py's CCM_API_KEY
    CCM_SCORING_BATCH_SIZE             Default 5000 — raw_telemetry_staging rows per DAG run
    CCM_ANOMALY_THRESHOLD                 Default 0.70
    CCM_UNCERTAINTY_THRESHOLD                Default 0.50
    CCM_RETRAIN_LABEL_THRESHOLD                 Default 500
    CCM_PSI_MONITORED_FEATURES                     Comma-separated feature names to drift-monitor
    CCM_DRIFT_WEBHOOK_URL                             Optional Slack/Teams webhook for critical PSI alerts
    CCM_MODEL_VERSION                                    Default "reference-zscore-v1" — bootstrap model tag
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from airflow.decorators import dag, task
from airflow.exceptions import AirflowSkipException

# psi_monitor.py lives at the repo root (devriskops-ccm/), one level above
# this dags/ package — Airflow's DAG-folder discovery doesn't put that
# directory on sys.path automatically, so make it importable explicitly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from psi_monitor import PSIMonitorService, extract_feature_series  # noqa: E402

logger = logging.getLogger("devriskops.ccm.dag")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ccm_user:ccm_password@localhost:5432/devriskops_ccm")
CCM_BACKEND_API_URL = os.environ.get("CCM_BACKEND_API_URL", "http://localhost:8000").rstrip("/")
CCM_API_KEY = os.environ.get("CCM_API_KEY", "dev-local-insecure-key-change-me")

SCORING_BATCH_SIZE = int(os.environ.get("CCM_SCORING_BATCH_SIZE", "5000"))
INGEST_CHUNK_SIZE = 500
ANOMALY_SCORE_THRESHOLD = float(os.environ.get("CCM_ANOMALY_THRESHOLD", "0.70"))
UNCERTAINTY_SCORE_THRESHOLD = float(os.environ.get("CCM_UNCERTAINTY_THRESHOLD", "0.50"))
RETRAIN_LABEL_THRESHOLD = int(os.environ.get("CCM_RETRAIN_LABEL_THRESHOLD", "500"))
MIN_LABELS_FOR_RETRAIN = 20
PSI_MONITORED_FEATURES = [
    name.strip()
    for name in os.environ.get(
        "CCM_PSI_MONITORED_FEATURES",
        "dollar_amount,days_since_last_review,approval_count,policy_violation_score",
    ).split(",")
    if name.strip()
]
DRIFT_WEBHOOK_URL = os.environ.get("CCM_DRIFT_WEBHOOK_URL", "").strip()
CURRENT_MODEL_VERSION = os.environ.get("CCM_MODEL_VERSION", "reference-zscore-v1")
MIN_SAMPLES_PER_WINDOW = 5

DEFAULT_ARGS = {
    "owner": "devriskops",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}


def _get_connection() -> "psycopg2.extensions.connection":
    return psycopg2.connect(DATABASE_URL)


def _load_active_baseline_stats() -> dict[str, dict[str, dict[str, float]]]:
    """Loads {control_id: {feature: {mean, std}}} from the most recently
    promoted retrain, or bootstraps a fresh baseline from every historical
    control_events row (grouped by control_id) if no retrain has ever been
    promoted yet — so score_100pct_population always has SOMETHING
    principled to score against, even on a brand-new deployment."""
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT baseline_stats FROM model_retrain_log
                WHERE status = 'promoted' AND baseline_stats IS NOT NULL
                ORDER BY completed_at DESC LIMIT 1
                """
            )
            promoted_row = cur.fetchone()
            if promoted_row and promoted_row["baseline_stats"]:
                return promoted_row["baseline_stats"]

            cur.execute("SELECT control_id, point_in_time_features FROM control_events")
            history_rows = cur.fetchall()
    finally:
        conn.close()

    if not history_rows:
        return {}

    frame = pd.json_normalize(
        [{"control_id": row["control_id"], **(row["point_in_time_features"] or {})} for row in history_rows]
    )
    numeric_columns = [c for c in frame.columns if c != "control_id" and pd.api.types.is_numeric_dtype(frame[c])]

    baseline: dict[str, dict[str, dict[str, float]]] = {}
    for control_id, group in frame.groupby("control_id"):
        baseline[control_id] = {}
        for column in numeric_columns:
            series = group[column].dropna()
            if series.empty:
                continue
            std = float(series.std(ddof=0))
            baseline[control_id][column] = {"mean": float(series.mean()), "std": std if std > 1e-9 else 1.0}
    return baseline


def _score_record(
    control_id: str, features: dict[str, Any], baseline: dict[str, dict[str, dict[str, float]]]
) -> tuple[float, float]:
    """Reference statistical scorer — see module docstring. Returns
    (anomaly_score, uncertainty_score), both in [0, 1]."""
    control_baseline = baseline.get(control_id, {})
    z_scores: list[float] = []
    for feature_name, value in features.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        stats = control_baseline.get(feature_name)
        if not stats:
            continue
        z_scores.append(abs((float(value) - stats["mean"]) / stats["std"]))

    max_z = max(z_scores) if z_scores else 0.0
    anomaly_score = float(1.0 / (1.0 + np.exp(-(max_z - 3.0))))  # logistic centered at z=3 (~99.7th percentile)
    uncertainty_score = float(max(0.0, 1.0 - 2.0 * abs(anomaly_score - 0.5)))
    return round(anomaly_score, 4), round(uncertainty_score, 4)


def _shadow_metrics(
    labeled_df: pd.DataFrame, numeric_columns: list[str], baseline: dict[str, dict[str, dict[str, float]]]
) -> dict[str, float]:
    """Scores every labeled row with `baseline` and computes precision,
    recall, and false-positive rate against the TRUE_CONTROL_FAILURE /
    everything-else ground truth — the shadow-evaluation step
    trigger_model_retrain uses to decide whether a freshly retrained
    baseline is actually better than the one currently in production."""
    predictions = []
    for _, row in labeled_df.iterrows():
        features = {col: row[col] for col in numeric_columns if pd.notna(row[col])}
        anomaly_score, _ = _score_record(row["control_id"], features, baseline)
        predictions.append(1 if anomaly_score >= ANOMALY_SCORE_THRESHOLD else 0)

    preds = np.array(predictions)
    truth = (labeled_df["label"] == "TRUE_CONTROL_FAILURE").astype(int).to_numpy()

    true_positive = int(np.sum((preds == 1) & (truth == 1)))
    false_positive = int(np.sum((preds == 1) & (truth == 0)))
    false_negative = int(np.sum((preds == 0) & (truth == 1)))
    true_negative = int(np.sum((preds == 0) & (truth == 0)))

    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) > 0 else 0.0
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) > 0 else 0.0
    false_positive_rate = false_positive / (false_positive + true_negative) if (false_positive + true_negative) > 0 else 0.0

    return {"precision": precision, "recall": recall, "fpr": false_positive_rate}


@dag(
    dag_id="ccm_continuous_control_monitoring",
    description="DevRiskOps CCM — 100% population scoring, feature drift monitoring, and active-learning retraining.",
    schedule="0 1 * * *",
    start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["devriskops", "ccm", "audit", "ml-ops"],
    max_active_runs=1,
)
def ccm_dag():

    @task()
    def score_100pct_population() -> dict[str, Any]:
        """Scores every unprocessed row in raw_telemetry_staging — the
        landing zone upstream source-system connectors (Oracle Fusion, SAP
        HANA, SailPoint, Dynamics 365, ServiceNow, ...) populate — then
        ingests each scored record through backend_api.py's
        /api/v1/events/ingest so control_events and model_inferences stay
        the single source of truth for both the auditor workspace and this
        pipeline. Marks staged rows processed only after a successful
        ingest, so a mid-batch failure leaves them for the next run rather
        than silently dropping them."""
        import httpx

        baseline = _load_active_baseline_stats()

        conn = _get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT staging_id, control_id, system_source, event_timestamp, raw_payload
                    FROM raw_telemetry_staging
                    WHERE processed = FALSE
                    ORDER BY staged_at
                    LIMIT %s
                    """,
                    (SCORING_BATCH_SIZE,),
                )
                staged_rows = cur.fetchall()
        finally:
            conn.close()

        if not staged_rows:
            logger.info("score_100pct_population: no unprocessed telemetry staged — nothing to score.")
            return {"scored_count": 0, "flagged_count": 0}

        events_payload = []
        for row in staged_rows:
            features = row["raw_payload"] or {}
            anomaly_score, uncertainty_score = _score_record(row["control_id"], features, baseline)
            requires_review = anomaly_score >= ANOMALY_SCORE_THRESHOLD or uncertainty_score >= UNCERTAINTY_SCORE_THRESHOLD
            events_payload.append({
                "control_id": row["control_id"],
                "system_source": row["system_source"],
                "event_timestamp": row["event_timestamp"].isoformat(),
                "point_in_time_features": features,
                "model_version": CURRENT_MODEL_VERSION,
                "anomaly_score": anomaly_score,
                "uncertainty_score": uncertainty_score,
                "requires_human_review": requires_review,
            })

        flagged_count = 0
        with httpx.Client(timeout=60.0) as client:
            for start in range(0, len(events_payload), INGEST_CHUNK_SIZE):
                chunk = events_payload[start : start + INGEST_CHUNK_SIZE]
                response = client.post(
                    f"{CCM_BACKEND_API_URL}/api/v1/events/ingest",
                    json={"events": chunk},
                    headers={"X-API-Key": CCM_API_KEY},
                )
                response.raise_for_status()
                flagged_count += response.json()["flagged_for_review_count"]

        staging_ids = [row["staging_id"] for row in staged_rows]
        conn = _get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE raw_telemetry_staging SET processed = TRUE, processed_at = now() WHERE staging_id = ANY(%s)",
                    (staging_ids,),
                )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "score_100pct_population: scored %d record(s), %d flagged for review.",
            len(events_payload), flagged_count,
        )
        return {"scored_count": len(events_payload), "flagged_count": flagged_count}

    @task()
    def monitor_feature_drift(scoring_result: dict[str, Any]) -> dict[str, Any]:
        """Computes PSI for every feature in CCM_PSI_MONITORED_FEATURES
        (baseline: the 30 days before the trailing target window; target:
        the trailing 7 days) and dispatches a webhook alert via
        psi_monitor.PSIMonitorService the moment any feature crosses the
        critical (>= 0.25) threshold."""
        logger.info("monitor_feature_drift: upstream scoring result: %s", scoring_result)

        conn = _get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT point_in_time_features FROM control_events
                    WHERE event_timestamp >= now() - interval '37 days'
                      AND event_timestamp <  now() - interval '7 days'
                    """
                )
                baseline_events = [row["point_in_time_features"] or {} for row in cur.fetchall()]

                cur.execute(
                    "SELECT point_in_time_features FROM control_events WHERE event_timestamp >= now() - interval '7 days'"
                )
                target_events = [row["point_in_time_features"] or {} for row in cur.fetchall()]
        finally:
            conn.close()

        distributions = {}
        for feature_name in PSI_MONITORED_FEATURES:
            baseline_arr = extract_feature_series(baseline_events, feature_name)
            target_arr = extract_feature_series(target_events, feature_name)
            if baseline_arr.size < MIN_SAMPLES_PER_WINDOW or target_arr.size < MIN_SAMPLES_PER_WINDOW:
                logger.warning("monitor_feature_drift: skipping '%s' — insufficient samples.", feature_name)
                continue
            distributions[feature_name] = (baseline_arr, target_arr)

        if not distributions:
            logger.warning("monitor_feature_drift: no monitored feature had sufficient data to evaluate.")
            return {"critical_drift": False, "features_evaluated": 0, "critical_features": []}

        service = PSIMonitorService(webhook_url=DRIFT_WEBHOOK_URL or None, webhook_style="slack")
        results = service.evaluate(distributions)
        critical_features = [name for name, result in results.items() if result.severity.value == "CRITICAL"]

        logger.info(
            "monitor_feature_drift: evaluated %d feature(s), %d critical: %s",
            len(results), len(critical_features), critical_features,
        )
        return {
            "critical_drift": bool(critical_features),
            "features_evaluated": len(results),
            "critical_features": critical_features,
            "psi_scores": {name: round(result.psi_score, 4) for name, result in results.items()},
        }

    @task()
    def evaluate_retraining_trigger(drift_result: dict[str, Any]) -> dict[str, Any]:
        """should_retrain fires if EITHER (a) unprocessed labels — auditor
        decisions recorded since the last promoted retrain — have reached
        CCM_RETRAIN_LABEL_THRESHOLD, OR (b) monitor_feature_drift found
        critical drift on any monitored feature. Either condition alone
        means the production model's assumptions may no longer hold."""
        conn = _get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT completed_at FROM model_retrain_log WHERE status = 'promoted' ORDER BY completed_at DESC LIMIT 1"
                )
                last_promoted = cur.fetchone()
                since = last_promoted["completed_at"] if last_promoted else datetime(1970, 1, 1, tzinfo=timezone.utc)

                cur.execute(
                    """
                    SELECT COUNT(*) AS unprocessed_labels FROM auditor_triage
                    WHERE resolution_label <> 'DATA_PIPELINE_ERROR' AND reviewed_at > %s
                    """,
                    (since,),
                )
                unprocessed_labels = cur.fetchone()["unprocessed_labels"]
        finally:
            conn.close()

        label_triggered = unprocessed_labels >= RETRAIN_LABEL_THRESHOLD
        drift_triggered = bool(drift_result.get("critical_drift"))
        should_retrain = label_triggered or drift_triggered

        reasons = []
        if label_triggered:
            reasons.append(f"label_volume({unprocessed_labels}>={RETRAIN_LABEL_THRESHOLD})")
        if drift_triggered:
            reasons.append(f"critical_drift({','.join(drift_result.get('critical_features', []))})")

        logger.info(
            "evaluate_retraining_trigger: unprocessed_labels=%d, drift_triggered=%s, should_retrain=%s",
            unprocessed_labels, drift_triggered, should_retrain,
        )
        return {
            "should_retrain": should_retrain,
            "unprocessed_labels": unprocessed_labels,
            "trigger_reason": ",".join(reasons) if reasons else "none",
        }

    @task()
    def trigger_model_retrain(trigger_eval: dict[str, Any]) -> dict[str, Any]:
        """Active-learning retrain: recompute per-control_id/per-feature
        baseline statistics from every currently-labeled event, shadow-
        evaluate the new baseline against the current promoted one on the
        same labeled set, and promote it only if precision and recall are
        at least as good and the false-positive rate doesn't meaningfully
        regress. A no-op (Airflow-skipped, not failed) when trigger_eval
        says retraining isn't warranted, or when there isn't yet enough
        labeled data to retrain responsibly."""
        if not trigger_eval.get("should_retrain"):
            raise AirflowSkipException(f"Retrain not triggered: {trigger_eval}")

        conn = _get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT ce.control_id, ce.point_in_time_features, tri.resolution_label
                    FROM control_events ce
                    JOIN auditor_triage tri ON tri.event_id = ce.event_id
                    WHERE tri.resolution_label <> 'DATA_PIPELINE_ERROR'
                    """
                )
                labeled_rows = cur.fetchall()
        finally:
            conn.close()

        if len(labeled_rows) < MIN_LABELS_FOR_RETRAIN:
            raise AirflowSkipException(
                f"Insufficient labeled data for retrain: {len(labeled_rows)} examples (need >= {MIN_LABELS_FOR_RETRAIN})."
            )

        labeled_df = pd.DataFrame([
            {"control_id": row["control_id"], "label": row["resolution_label"], **(row["point_in_time_features"] or {})}
            for row in labeled_rows
        ])
        numeric_columns = [
            c for c in labeled_df.columns if c not in ("control_id", "label") and pd.api.types.is_numeric_dtype(labeled_df[c])
        ]

        # ── Retrain: recompute baseline mean/std per control_id/feature ──
        new_baseline: dict[str, dict[str, dict[str, float]]] = {}
        for control_id, group in labeled_df.groupby("control_id"):
            new_baseline[control_id] = {}
            for column in numeric_columns:
                series = group[column].dropna()
                if series.empty:
                    continue
                std = float(series.std(ddof=0))
                new_baseline[control_id][column] = {"mean": float(series.mean()), "std": std if std > 1e-9 else 1.0}

        # ── Shadow evaluation against the current incumbent ──
        current_baseline = _load_active_baseline_stats()
        new_metrics = _shadow_metrics(labeled_df, numeric_columns, new_baseline)
        incumbent_metrics = (
            _shadow_metrics(labeled_df, numeric_columns, current_baseline)
            if current_baseline
            else {"precision": 0.0, "recall": 0.0, "fpr": 1.0}
        )

        fpr_regression_tolerance = 0.02
        promote = (
            new_metrics["precision"] >= incumbent_metrics["precision"]
            and new_metrics["recall"] >= incumbent_metrics["recall"]
            and new_metrics["fpr"] <= incumbent_metrics["fpr"] + fpr_regression_tolerance
        )
        status = "promoted" if promote else "rejected"
        new_model_version = f"reference-zscore-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        conn = _get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO model_retrain_log
                        (trigger_reason, labels_included_count, model_version, baseline_stats,
                         shadow_precision, shadow_recall, shadow_false_positive_rate, status, completed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    """,
                    (
                        trigger_eval.get("trigger_reason", "unknown"),
                        len(labeled_rows),
                        new_model_version,
                        psycopg2.extras.Json(new_baseline),
                        new_metrics["precision"],
                        new_metrics["recall"],
                        new_metrics["fpr"],
                        status,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "trigger_model_retrain: %s — new(precision=%.4f recall=%.4f fpr=%.4f) vs "
            "incumbent(precision=%.4f recall=%.4f fpr=%.4f)",
            status, new_metrics["precision"], new_metrics["recall"], new_metrics["fpr"],
            incumbent_metrics["precision"], incumbent_metrics["recall"], incumbent_metrics["fpr"],
        )
        return {
            "status": status,
            "model_version": new_model_version,
            "shadow_metrics": new_metrics,
            "incumbent_metrics": incumbent_metrics,
        }

    scoring_result = score_100pct_population()
    drift_result = monitor_feature_drift(scoring_result)
    trigger_eval = evaluate_retraining_trigger(drift_result)
    trigger_model_retrain(trigger_eval)


ccm_dag()
