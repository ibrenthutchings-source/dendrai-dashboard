-- =============================================================================
-- DevRiskOps CCM — PostgreSQL Schema
--
-- Two groups of tables:
--   1. Core audit trail (control_events, model_inferences, auditor_triage) —
--      the durable record of what was scored, what the model said, and what
--      a human auditor ultimately decided. This is the system of record for
--      the Streamlit auditor workspace (app.py), the FastAPI service
--      (backend_api.py), and every downstream analytic.
--   2. Pipeline support (raw_telemetry_staging, model_retrain_log) — state
--      the Airflow DAG (dags/ccm_dag.py) needs to do real, stateful,
--      closed-loop active-learning retraining across runs: an inbound
--      landing zone for not-yet-scored telemetry from source-system
--      connectors, and a log of every retrain attempt with its shadow-
--      evaluation metrics and promotion outcome.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

-- ─────────────────────────────────────────────────────────────────────────────
-- Core audit trail
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TYPE triage_label AS ENUM (
    'TRUE_CONTROL_FAILURE',
    'BENIGN_OPERATIONAL_NOISE',
    'APPROVED_CARVE_OUT',
    'DATA_PIPELINE_ERROR'
);

CREATE TABLE control_events (
    event_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    control_id           VARCHAR(128) NOT NULL,
    system_source        VARCHAR(64) NOT NULL,
    event_timestamp      TIMESTAMPTZ NOT NULL,
    point_in_time_features JSONB NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_control_events_control_id ON control_events (control_id);
CREATE INDEX idx_control_events_timestamp  ON control_events (event_timestamp DESC);
CREATE INDEX idx_control_events_source     ON control_events (system_source);

-- GIN index so ad-hoc "does this feature exist / what's its value" queries
-- against the frozen feature snapshot (app.py's PSI tab, mcp_server.py's
-- check_feature_drift tool) don't force a sequential scan as the table grows.
CREATE INDEX idx_control_events_features_gin ON control_events USING GIN (point_in_time_features);

CREATE TABLE model_inferences (
    inference_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id               UUID NOT NULL REFERENCES control_events(event_id) ON DELETE CASCADE,
    model_version           VARCHAR(64) NOT NULL,
    anomaly_score           NUMERIC(5,4) NOT NULL CHECK (anomaly_score BETWEEN 0 AND 1),
    uncertainty_score       NUMERIC(5,4) NOT NULL CHECK (uncertainty_score BETWEEN 0 AND 1),
    requires_human_review   BOOLEAN NOT NULL DEFAULT FALSE,
    scored_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_model_inferences_event_id ON model_inferences (event_id);

-- The exact predicate backend_api.py's GET /api/v1/triage/pending filters
-- on — keeps that query's "everything currently awaiting review, worst
-- (highest-uncertainty) first" scan an index-only lookup regardless of how
-- large model_inferences grows.
CREATE INDEX idx_model_inferences_pending
    ON model_inferences (uncertainty_score DESC)
    WHERE requires_human_review = TRUE;

CREATE TABLE auditor_triage (
    triage_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id               UUID NOT NULL UNIQUE REFERENCES control_events(event_id) ON DELETE CASCADE,
    auditor_id              VARCHAR(128) NOT NULL,
    resolution_label        triage_label NOT NULL,
    justification_notes     TEXT,
    reviewed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Mirrors the Streamlit form's own client-side requirement (app.py Tab 1)
    -- at the database layer, so no caller — including direct SQL, the MCP
    -- server, or a future integration — can silently record a control
    -- failure or an approved carve-out with no rationale on file.
    CONSTRAINT chk_justification_required_for_material_labels CHECK (
        resolution_label NOT IN ('TRUE_CONTROL_FAILURE', 'APPROVED_CARVE_OUT')
        OR (justification_notes IS NOT NULL AND length(btrim(justification_notes)) > 0)
    )
);

-- Excludes DATA_PIPELINE_ERROR: a pipeline error isn't a real audit decision
-- about the underlying control, so it shouldn't count toward review-volume
-- or false-positive-rate reporting. backend_api.py's retrain-trigger check
-- and ccm_dag.py's evaluate_retraining_trigger task both filter on exactly
-- this condition — this partial index makes that filtered COUNT(*)/scan fast.
CREATE INDEX idx_auditor_triage_reviewed_at_excl_pipeline_error
    ON auditor_triage (reviewed_at)
    WHERE resolution_label <> 'DATA_PIPELINE_ERROR';

-- ─────────────────────────────────────────────────────────────────────────────
-- Pipeline support (dags/ccm_dag.py)
-- ─────────────────────────────────────────────────────────────────────────────

-- Landing zone for not-yet-scored telemetry from source-system connectors
-- (Oracle Fusion, SAP HANA, SailPoint, Dynamics 365, ServiceNow, ...).
-- score_100pct_population reads every unprocessed row here, in batches,
-- scores it, and ingests the scored result into control_events +
-- model_inferences via backend_api.py's /api/v1/events/ingest — that is
-- the ONLY path either of those two core tables gets written to.
CREATE TABLE raw_telemetry_staging (
    staging_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    control_id         VARCHAR(128) NOT NULL,
    system_source       VARCHAR(64) NOT NULL,
    event_timestamp      TIMESTAMPTZ NOT NULL,
    raw_payload           JSONB NOT NULL,
    staged_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed               BOOLEAN NOT NULL DEFAULT FALSE,
    processed_at              TIMESTAMPTZ
);

CREATE INDEX idx_raw_telemetry_unprocessed
    ON raw_telemetry_staging (staged_at)
    WHERE processed = FALSE;

-- One row per retrain attempt: what triggered it, how many labels fed it,
-- the recomputed baseline statistics themselves (JSONB, so
-- score_100pct_population's next run can load the currently-promoted
-- baseline without needing a separate model-artifact store), its shadow
-- evaluation metrics against the incumbent, and the promotion outcome.
CREATE TABLE model_retrain_log (
    retrain_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    triggered_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger_reason                VARCHAR(256) NOT NULL,
    labels_included_count           INTEGER NOT NULL,
    model_version                     VARCHAR(64) NOT NULL,
    baseline_stats                     JSONB,
    shadow_precision                   NUMERIC(5,4),
    shadow_recall                      NUMERIC(5,4),
    shadow_false_positive_rate         NUMERIC(5,4),
    status                             VARCHAR(24) NOT NULL DEFAULT 'training'
        CHECK (status IN ('training', 'shadow_evaluated', 'promoted', 'rejected')),
    completed_at                       TIMESTAMPTZ
);

-- _load_active_baseline_stats() (ccm_dag.py) always wants the single most
-- recent promoted baseline — this index makes that lookup O(log n) instead
-- of a full table scan + sort as the retrain log accumulates.
CREATE INDEX idx_model_retrain_log_promoted
    ON model_retrain_log (completed_at DESC)
    WHERE status = 'promoted';
