#!/usr/bin/env python3
"""
DevRiskOps CCM — Core Ingestion & Triage Service (FastAPI)

The single write path for control_events / model_inferences / auditor_triage
(schema.sql). Every other component in this repo — the Streamlit auditor
workspace (app.py), the FastMCP server (mcp_server.py), and the Airflow DAG
(dags/ccm_dag.py) — reaches the database exclusively through this service
for anything that mutates state, so there is exactly one place validation,
the retrain-trigger heuristic, and the human-review threshold logic live.

Endpoints
---------
    GET  /api/v1/triage/pending      Exceptions awaiting human review, highest uncertainty first
    POST /api/v1/triage/{event_id}   Record an auditor's ground-truth resolution
    POST /api/v1/events/ingest       Batch-ingest scored telemetry (control_events + model_inferences)

Configuration (environment variables)
--------------------------------------
    DATABASE_URL                  postgresql://user:pass@host:port/dbname
    CCM_API_KEY                   Shared-secret required on every request (X-API-Key header)
    CCM_ANOMALY_THRESHOLD         Default 0.70 — anomaly_score at/above this flags for review
    CCM_UNCERTAINTY_THRESHOLD     Default 0.50 — uncertainty_score at/above this flags for review
    CCM_RETRAIN_LABEL_THRESHOLD   Default 500  — new labels that trigger a retrain-readiness alert
    CCM_RETRAIN_WEBHOOK_URL       Optional Slack/Teams webhook for the retrain-trigger notice
    CCM_DB_POOL_MIN / _MAX        Default 2 / 10 — asyncpg pool sizing
    CCM_API_PORT                  Default 8000 — only used by `python backend_api.py` directly

Run:
    uvicorn backend_api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import asyncpg
import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("devriskops.ccm.api")

# ── Configuration ────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ccm_user:ccm_password@localhost:5432/devriskops_ccm")
CCM_API_KEY = os.environ.get("CCM_API_KEY", "dev-local-insecure-key-change-me")
ANOMALY_SCORE_THRESHOLD = float(os.environ.get("CCM_ANOMALY_THRESHOLD", "0.70"))
UNCERTAINTY_SCORE_THRESHOLD = float(os.environ.get("CCM_UNCERTAINTY_THRESHOLD", "0.50"))
RETRAIN_LABEL_THRESHOLD = int(os.environ.get("CCM_RETRAIN_LABEL_THRESHOLD", "500"))
RETRAIN_WEBHOOK_URL = os.environ.get("CCM_RETRAIN_WEBHOOK_URL", "").strip()
DB_POOL_MIN_SIZE = int(os.environ.get("CCM_DB_POOL_MIN", "2"))
DB_POOL_MAX_SIZE = int(os.environ.get("CCM_DB_POOL_MAX", "10"))


# ── Enums / Pydantic models ──────────────────────────────────────────────────

class TriageLabel(str, Enum):
    TRUE_CONTROL_FAILURE = "TRUE_CONTROL_FAILURE"
    BENIGN_OPERATIONAL_NOISE = "BENIGN_OPERATIONAL_NOISE"
    APPROVED_CARVE_OUT = "APPROVED_CARVE_OUT"
    DATA_PIPELINE_ERROR = "DATA_PIPELINE_ERROR"


_NOTES_REQUIRED_LABELS = {TriageLabel.TRUE_CONTROL_FAILURE, TriageLabel.APPROVED_CARVE_OUT}


class PendingTriageItem(BaseModel):
    event_id: uuid.UUID
    control_id: str
    system_source: str
    event_timestamp: datetime
    point_in_time_features: dict[str, Any]
    inference_id: uuid.UUID
    model_version: str
    anomaly_score: float
    uncertainty_score: float
    scored_at: datetime


class TriageDecisionRequest(BaseModel):
    auditor_id: str = Field(..., min_length=1, max_length=128)
    resolution_label: TriageLabel
    justification_notes: Optional[str] = Field(default=None, max_length=8000)

    @field_validator("justification_notes")
    @classmethod
    def _strip_notes(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        stripped = v.strip()
        return stripped or None


class TriageDecisionResponse(BaseModel):
    triage_id: uuid.UUID
    event_id: uuid.UUID
    resolution_label: TriageLabel
    reviewed_at: datetime
    retrain_evaluation_scheduled: bool


class ControlEventIngest(BaseModel):
    control_id: str = Field(..., min_length=1, max_length=128)
    system_source: str = Field(..., min_length=1, max_length=64)
    event_timestamp: datetime
    point_in_time_features: dict[str, Any]
    model_version: str = Field(..., min_length=1, max_length=64)
    anomaly_score: float = Field(..., ge=0.0, le=1.0)
    uncertainty_score: float = Field(..., ge=0.0, le=1.0)
    requires_human_review: Optional[bool] = Field(
        default=None,
        description="Leave unset to let the server compute this from CCM_ANOMALY_THRESHOLD / CCM_UNCERTAINTY_THRESHOLD.",
    )


class IngestBatchRequest(BaseModel):
    events: list[ControlEventIngest] = Field(..., min_length=1, max_length=50000)


class IngestedEventResult(BaseModel):
    event_id: uuid.UUID
    inference_id: uuid.UUID
    requires_human_review: bool


class IngestBatchResponse(BaseModel):
    ingested_count: int
    flagged_for_review_count: int
    results: list[IngestedEventResult]


# ── DB pool lifecycle ─────────────────────────────────────────────────────────

class _AppState:
    pool: Optional[asyncpg.Pool] = None


_state = _AppState()


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Registers a JSON/JSONB codec on every pooled connection so
    point_in_time_features round-trips as a plain Python dict in both
    directions, instead of callers having to json.dumps/json.loads by hand."""
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Connecting to Postgres pool.")
    _state.pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=DB_POOL_MIN_SIZE,
        max_size=DB_POOL_MAX_SIZE,
        command_timeout=30,
        init=_init_connection,
    )
    logger.info("DB pool ready (min=%d, max=%d).", DB_POOL_MIN_SIZE, DB_POOL_MAX_SIZE)
    try:
        yield
    finally:
        if _state.pool is not None:
            await _state.pool.close()
        logger.info("DB pool closed.")


def get_pool() -> asyncpg.Pool:
    if _state.pool is None:
        raise HTTPException(status_code=503, detail="Database pool not initialized.")
    return _state.pool


async def require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    if x_api_key != CCM_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key.")
    return x_api_key


app = FastAPI(
    title="DevRiskOps CCM — Continuous Control Monitoring API",
    description="Core ingestion, triage, and active-learning feedback service for 100%-population control monitoring.",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Retrain-trigger evaluation (background task) ─────────────────────────────

async def _evaluate_retrain_trigger(pool: asyncpg.Pool) -> None:
    """Counts real (non-pipeline-error) auditor labels and fires a webhook
    the moment the running total crosses a multiple of
    RETRAIN_LABEL_THRESHOLD. Deliberately stateless: any two processes
    computing this from the same table will agree, so the nightly Airflow
    DAG's own evaluate_retraining_trigger task independently re-derives the
    same signal (against a "since last promoted retrain" window there, vs.
    this endpoint's simpler "crossed another multiple" heuristic) without
    needing to share state with this API process — this is a fast, best-
    effort early warning, not the system of record for retrain decisions."""
    try:
        async with pool.acquire() as conn:
            total_labels: int = await conn.fetchval(
                "SELECT COUNT(*) FROM auditor_triage WHERE resolution_label <> 'DATA_PIPELINE_ERROR'"
            )
    except Exception:
        logger.exception("Retrain-trigger evaluation: failed to query label count.")
        return

    if total_labels <= 0 or total_labels % RETRAIN_LABEL_THRESHOLD != 0:
        return

    logger.warning(
        "Retrain label threshold crossed: %d non-pipeline-error labels on file (threshold=%d).",
        total_labels, RETRAIN_LABEL_THRESHOLD,
    )
    if not RETRAIN_WEBHOOK_URL:
        return

    payload = {
        "text": (
            ":robot_face: *CCM Active Learning* — retrain label threshold reached.\n"
            f"Total accumulated labels: *{total_labels}* (threshold: {RETRAIN_LABEL_THRESHOLD}).\n"
            "Recommend running `ccm_dag.evaluate_retraining_trigger` ahead of its next scheduled run."
        )
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(RETRAIN_WEBHOOK_URL, json=payload)
            response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Retrain-trigger webhook dispatch failed.")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/api/v1/triage/pending",
    response_model=list[PendingTriageItem],
    tags=["triage"],
    dependencies=[Depends(require_api_key)],
)
async def get_pending_triage(
    limit: int = Query(default=100, ge=1, le=1000),
    min_uncertainty: float = Query(default=0.0, ge=0.0, le=1.0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[PendingTriageItem]:
    """Pending exceptions flagged for human review: the latest model
    inference per event, for every event that requires review and has not
    yet been resolved by an auditor, ordered by uncertainty score (the most
    ambiguous — and therefore most valuable to label — items first)."""
    rows = await pool.fetch(
        """
        SELECT
            ce.event_id, ce.control_id, ce.system_source, ce.event_timestamp,
            ce.point_in_time_features,
            mi.inference_id, mi.model_version, mi.anomaly_score, mi.uncertainty_score, mi.scored_at
        FROM control_events ce
        JOIN LATERAL (
            SELECT *
            FROM model_inferences m
            WHERE m.event_id = ce.event_id
            ORDER BY m.scored_at DESC
            LIMIT 1
        ) mi ON TRUE
        LEFT JOIN auditor_triage tri ON tri.event_id = ce.event_id
        WHERE mi.requires_human_review = TRUE
          AND tri.triage_id IS NULL
          AND mi.uncertainty_score >= $1
        ORDER BY mi.uncertainty_score DESC, ce.event_timestamp DESC
        LIMIT $2
        """,
        min_uncertainty, limit,
    )
    return [
        PendingTriageItem(
            event_id=row["event_id"],
            control_id=row["control_id"],
            system_source=row["system_source"],
            event_timestamp=row["event_timestamp"],
            point_in_time_features=row["point_in_time_features"] or {},
            inference_id=row["inference_id"],
            model_version=row["model_version"],
            anomaly_score=float(row["anomaly_score"]),
            uncertainty_score=float(row["uncertainty_score"]),
            scored_at=row["scored_at"],
        )
        for row in rows
    ]


@app.post(
    "/api/v1/triage/{event_id}",
    response_model=TriageDecisionResponse,
    tags=["triage"],
    dependencies=[Depends(require_api_key)],
)
async def submit_triage_decision(
    event_id: uuid.UUID,
    decision: TriageDecisionRequest,
    background_tasks: BackgroundTasks,
    pool: asyncpg.Pool = Depends(get_pool),
) -> TriageDecisionResponse:
    """Records (or revises, via upsert on the UNIQUE event_id) an auditor's
    ground-truth resolution for one control_events row, then schedules a
    background evaluation of whether the new label volume crosses the
    active-learning retrain threshold — deferred via BackgroundTasks so the
    auditor's UI gets an immediate response rather than waiting on that
    (cheap, but non-essential-to-this-request) check."""
    if decision.resolution_label in _NOTES_REQUIRED_LABELS and not decision.justification_notes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"justification_notes is required when resolution_label is {decision.resolution_label.value}.",
        )

    async with pool.acquire() as conn:
        event_exists = await conn.fetchval("SELECT 1 FROM control_events WHERE event_id = $1", event_id)
        if not event_exists:
            raise HTTPException(status_code=404, detail=f"No control_events row for event_id={event_id}.")

        try:
            row = await conn.fetchrow(
                """
                INSERT INTO auditor_triage (event_id, auditor_id, resolution_label, justification_notes)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (event_id) DO UPDATE SET
                    auditor_id = EXCLUDED.auditor_id,
                    resolution_label = EXCLUDED.resolution_label,
                    justification_notes = EXCLUDED.justification_notes,
                    reviewed_at = now()
                RETURNING triage_id, event_id, resolution_label, reviewed_at
                """,
                event_id, decision.auditor_id, decision.resolution_label.value, decision.justification_notes,
            )
        except asyncpg.exceptions.CheckViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Triage row failed a database integrity check: {exc}",
            ) from exc

    background_tasks.add_task(_evaluate_retrain_trigger, pool)

    return TriageDecisionResponse(
        triage_id=row["triage_id"],
        event_id=row["event_id"],
        resolution_label=TriageLabel(row["resolution_label"]),
        reviewed_at=row["reviewed_at"],
        retrain_evaluation_scheduled=True,
    )


@app.post(
    "/api/v1/events/ingest",
    response_model=IngestBatchResponse,
    tags=["ingestion"],
    dependencies=[Depends(require_api_key)],
)
async def ingest_events(
    batch: IngestBatchRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> IngestBatchResponse:
    """Ingests raw control telemetry + its model prediction in one
    transactional batch — the entry point dags/ccm_dag.py's
    score_100pct_population task calls once per scored chunk. Every event
    gets its own control_events row (point-in-time features frozen exactly
    as scored, for reproducible later audit) and exactly one
    model_inferences row for its model_version. requires_human_review is
    computed server-side from CCM_ANOMALY_THRESHOLD / CCM_UNCERTAINTY_THRESHOLD
    when the caller doesn't supply it explicitly, so review-triggering logic
    lives in exactly one place regardless of which caller is scoring."""
    results: list[IngestedEventResult] = []
    flagged_count = 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            for event in batch.events:
                requires_review = event.requires_human_review
                if requires_review is None:
                    requires_review = (
                        event.anomaly_score >= ANOMALY_SCORE_THRESHOLD
                        or event.uncertainty_score >= UNCERTAINTY_SCORE_THRESHOLD
                    )

                event_row = await conn.fetchrow(
                    """
                    INSERT INTO control_events (control_id, system_source, event_timestamp, point_in_time_features)
                    VALUES ($1, $2, $3, $4)
                    RETURNING event_id
                    """,
                    event.control_id, event.system_source, event.event_timestamp, event.point_in_time_features,
                )
                event_id = event_row["event_id"]

                inference_row = await conn.fetchrow(
                    """
                    INSERT INTO model_inferences
                        (event_id, model_version, anomaly_score, uncertainty_score, requires_human_review)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING inference_id
                    """,
                    event_id, event.model_version, event.anomaly_score, event.uncertainty_score, requires_review,
                )

                if requires_review:
                    flagged_count += 1
                results.append(IngestedEventResult(
                    event_id=event_id,
                    inference_id=inference_row["inference_id"],
                    requires_human_review=requires_review,
                ))

    logger.info("Ingested %d event(s), %d flagged for human review.", len(results), flagged_count)
    return IngestBatchResponse(
        ingested_count=len(results), flagged_for_review_count=flagged_count, results=results,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend_api:app", host="0.0.0.0", port=int(os.environ.get("CCM_API_PORT", "8000")), reload=False)
