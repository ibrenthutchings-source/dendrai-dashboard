#!/usr/bin/env python3
"""
PostgreSQL persistence layer — normalized schema for Dendrai Intelligenza.

28 tables covering company reference, EDGAR financial data, FRED macro data,
risk loop analytics, RSS signals, HITL decisions, audit plan, and token usage.

Usage:
    Set DATABASE_URL=postgresql://user:pass@host:5432/dbname in .env

    import db
    db.init_db()   # call once at startup
    company_id = db.upsert_company(meta)
    run_id = db.create_risk_loop_run(company_id, config)
    db.save_financial_ratios(run_id, ratios)
    ...
"""

import contextvars
import logging
import os
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

# TYPE_CHECKING block guarantees Pylance sees these names as always bound.
# At runtime, the try/except below imports them when the packages are installed.
if TYPE_CHECKING:
    import psycopg2
    from psycopg2 import pool as pg_pool
    from psycopg2.extras import Json, execute_values
    from pgvector.psycopg2 import register_vector as _pg_register_vector  # type: ignore[import]

_HAS_PSYCOPG2 = False
try:
    import psycopg2  # noqa: F811
    from psycopg2 import pool as pg_pool  # noqa: F811
    from psycopg2.extras import Json, execute_values  # noqa: F811
    _HAS_PSYCOPG2 = True
except ImportError:
    pass

_HAS_PGVECTOR = False
try:
    from pgvector.psycopg2 import register_vector as _pg_register_vector  # type: ignore[import]  # noqa: F811
    _HAS_PGVECTOR = True
except ImportError:
    pass

# _HAS_PGVECTOR only means "the pgvector Python package is installed" — it
# says nothing about whether the `vector` extension actually exists on the
# connected Postgres server (e.g. a plain postgres:16 image, as used by CI,
# has no pgvector extension at all). init_db()'s pgvector setup block sets
# this True only after `CREATE EXTENSION IF NOT EXISTS vector` actually
# succeeds; _conn() must gate register_vector() on this, not _HAS_PGVECTOR,
# or every single connection checkout raises psycopg2.ProgrammingError
# ("vector type not found in the database") on a server without the
# extension — breaking all persistence, not just embedding features.
_PGVECTOR_READY = False

_HAS_CRYPTOGRAPHY = False
try:
    from cryptography.fernet import Fernet, InvalidToken  # noqa: F811
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    pass

logger = logging.getLogger(__name__)
_pool: Optional["pg_pool.ThreadedConnectionPool"] = None

# ── Multi-tenant connection routing ──────────────────────────────────────────
# In TENANT_MODE=single (default), nothing below is used — every function
# keeps going through the single global `_pool` above, unchanged.
#
# In TENANT_MODE=multi, api_server.py's resolution middleware calls
# bind_tenant_pool(tenant_id, dsn) once at the top of each request (after
# resolving the Host header via control_plane.resolve_tenant), which sets
# _current_tenant for the remainder of that request/task. _conn() below
# prefers the tenant-scoped pool whenever one is bound, so ~85 tables' worth
# of existing call sites (db.upsert_company, db.save_financial_ratios, ...)
# need zero changes — they transparently talk to the right tenant's database
# because the context var, not a function argument, carries the routing.
#
# Fails closed: if TENANT_MODE=multi and no tenant is bound, _conn() raises
# rather than silently falling back to a shared/default database.
_current_tenant: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "db_current_tenant", default=None
)
_tenant_pools: dict[str, "pg_pool.ThreadedConnectionPool"] = {}
_tenant_pool_last_used: dict[str, float] = {}
_MAX_TENANT_POOLS = int(os.environ.get("TENANT_POOL_CACHE_SIZE", "50"))

# Embedding dimension — must match the model used to generate vectors.
# text-embedding-3-small / ada-002 → 1536  |  text-embedding-3-large → 3072
# Change BEFORE calling init_db() if using a different model.
EMBEDDING_DIM: int = 1536

# Canonical content_type values for save_embedding / get_relevant_context.
# Using these constants avoids string typos and makes cross-tool searches reliable.
EMBT_RISK_FACTOR   = "risk_factor_text"     # EDGAR 10-K Item 1A chunks
EMBT_ARTICLE       = "article_summary"      # RSS article title + summary
EMBT_AI_SUMMARY    = "ai_analysis_summary"  # LLM-generated analysis summaries
EMBT_SCENARIO      = "scenario_narrative"   # Scenario analysis narrative text
EMBT_CEM_RC        = "cem_root_cause"       # CEM root-cause narratives
EMBT_PROXY         = "proxy_governance"     # DEF 14A governance section chunks
EMBT_RAC           = "risks_as_code"        # Risks-as-Code YAML content
EMBT_RISK_NARRATIVE = "risk_narrative"      # Risk name + category + narrative for similarity search
EMBT_CAC           = "controls_as_code"     # Controls-as-Code Rego content
EMBT_PAC           = "policy_as_code"       # Policy-as-Code Rego content per process
# Concept-layer embeddings (concepts table) — the point where pgvector starts
# serving the ontology rather than just document similarity: concepts and
# documents share this same vector space, so a query can resolve to "which
# concept is this about" via the identical ANN mechanism as everything else.
# source_table='concepts', source_id=concepts.id, company_id=NULL (concepts
# are global, not per-company) — see db.embed_concept / reembed_stale_concepts.
EMBT_CONCEPT       = "concept"

# ─────────────────────────────────────────────────────────────────────────────
# DDL — 28 tables
# ─────────────────────────────────────────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS companies (
    id                      SERIAL PRIMARY KEY,
    ticker                  VARCHAR(16)  NOT NULL UNIQUE,
    cik                     VARCHAR(10)  UNIQUE,
    company_name            VARCHAR(255) NOT NULL,
    sic                     VARCHAR(6),
    sic_description         VARCHAR(255),
    entity_type             VARCHAR(64),
    state_of_incorporation  VARCHAR(4),
    fiscal_year_end         VARCHAR(4),
    exchanges               TEXT[],
    is_private              BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sic_peers (
    id          SERIAL PRIMARY KEY,
    company_id  INT  NOT NULL REFERENCES companies(id),
    peer_ticker VARCHAR(16),
    peer_cik    VARCHAR(10),
    peer_name   VARCHAR(255),
    peer_state  VARCHAR(4),
    sic         VARCHAR(6),
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS xbrl_metric_series (
    id          SERIAL PRIMARY KEY,
    company_id  INT         NOT NULL REFERENCES companies(id),
    metric_name VARCHAR(64) NOT NULL,
    xbrl_tag    VARCHAR(128),
    unit        VARCHAR(16),
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, metric_name)
);

CREATE TABLE IF NOT EXISTS xbrl_data_points (
    id               SERIAL PRIMARY KEY,
    series_id        INT     NOT NULL REFERENCES xbrl_metric_series(id),
    period_end       DATE    NOT NULL,
    period_start     DATE,
    fiscal_period    VARCHAR(4),
    form             VARCHAR(16),
    value            NUMERIC,
    filed_date       DATE,
    accession_number VARCHAR(20),
    source           VARCHAR(16) NOT NULL DEFAULT 'sec_edgar',
    granularity      VARCHAR(8)  NOT NULL DEFAULT 'annual'
);
CREATE INDEX IF NOT EXISTS idx_xbrl_dp_series ON xbrl_data_points (series_id, period_end DESC);

CREATE TABLE IF NOT EXISTS edgar_filings (
    id               SERIAL PRIMARY KEY,
    company_id       INT         NOT NULL REFERENCES companies(id),
    form_type        VARCHAR(16) NOT NULL,
    filing_date      DATE        NOT NULL,
    accession_number VARCHAR(20) NOT NULL UNIQUE,
    primary_document VARCHAR(255),
    items_field      VARCHAR(255),
    edgar_url        TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS edgar_8k_events (
    id                SERIAL PRIMARY KEY,
    company_id        INT NOT NULL REFERENCES companies(id),
    event_date        DATE NOT NULL,
    items             VARCHAR(255),
    item_descriptions JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- accession_number is the SEC's own unique filing identifier — without it,
-- re-running the same ticker re-inserts duplicate rows for the same filing
-- on every pipeline run, and there's no reliable way to tell "have we
-- already seen and classified this specific 8-K" (the basis for real
-- change-detection: a NEW accession_number here is a genuinely new event).
-- classification holds the LLM-extracted structured content (counterparty,
-- consideration, what was acquired/sold, rationale) for material items only
-- — see edgar_tool.classify_8k_event.
ALTER TABLE edgar_8k_events ADD COLUMN IF NOT EXISTS accession_number VARCHAR(20);
ALTER TABLE edgar_8k_events ADD COLUMN IF NOT EXISTS is_material       BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE edgar_8k_events ADD COLUMN IF NOT EXISTS classification    JSONB;
-- Turns a detected material event into a governed, trackable record instead
-- of a row that just sits there — same "detect -> tracked incident, not just
-- an alert" shape already used for Model Health drift (see
-- model_health_drift_incidents). status is only meaningful for is_material
-- rows; non-material rows stay NULL/untracked.
ALTER TABLE edgar_8k_events ADD COLUMN IF NOT EXISTS status      VARCHAR(16);  -- new | reviewing | assessed | dismissed
ALTER TABLE edgar_8k_events ADD COLUMN IF NOT EXISTS owner       VARCHAR(128);
ALTER TABLE edgar_8k_events ADD COLUMN IF NOT EXISTS notes       TEXT;
ALTER TABLE edgar_8k_events ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE edgar_8k_events ADD COLUMN IF NOT EXISTS alerted_at  TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_edgar_8k_events_open
    ON edgar_8k_events (company_id)
    WHERE is_material AND status != 'dismissed' AND status != 'assessed';
CREATE UNIQUE INDEX IF NOT EXISTS idx_edgar_8k_events_accession
    ON edgar_8k_events (company_id, accession_number)
    WHERE accession_number IS NOT NULL;

CREATE TABLE IF NOT EXISTS edgar_risk_factor_filings (
    id               SERIAL PRIMARY KEY,
    company_id       INT  NOT NULL REFERENCES companies(id),
    filing_date      DATE NOT NULL,
    accession_number VARCHAR(20),
    risk_factors_text TEXT,
    word_count       INT,
    edgar_url        TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS edgar_proxy_filings (
    id                     SERIAL PRIMARY KEY,
    company_id             INT  NOT NULL REFERENCES companies(id),
    filing_date            DATE NOT NULL,
    accession_number       VARCHAR(20),
    executive_compensation TEXT,
    board_of_directors     TEXT,
    say_on_pay             TEXT,
    shareholder_proposals  TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fred_series (
    id        SERIAL PRIMARY KEY,
    series_id VARCHAR(32)  NOT NULL UNIQUE,
    name      VARCHAR(255) NOT NULL,
    category  VARCHAR(64),
    units     VARCHAR(128),
    description TEXT
);

CREATE TABLE IF NOT EXISTS fred_observations (
    id          SERIAL PRIMARY KEY,
    series_id   INT     NOT NULL REFERENCES fred_series(id),
    quarter_end DATE    NOT NULL,
    value       NUMERIC,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (series_id, quarter_end)
);

CREATE TABLE IF NOT EXISTS fred_correlations (
    id                   SERIAL PRIMARY KEY,
    company_id           INT     NOT NULL REFERENCES companies(id),
    financial_metric     VARCHAR(64) NOT NULL,
    fred_series_id       INT     NOT NULL REFERENCES fred_series(id),
    run_date             DATE    NOT NULL,
    optimal_lag_quarters INT,
    pearson_r            NUMERIC(6,4),
    p_value              NUMERIC(10,6),
    significant_p05      BOOLEAN,
    n_quarter_pairs      INT,
    direction            VARCHAR(8),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fred_corr ON fred_correlations (company_id, financial_metric);

CREATE TABLE IF NOT EXISTS risk_loop_runs (
    id               SERIAL PRIMARY KEY,
    company_id       INT REFERENCES companies(id),
    ticker           VARCHAR(16)  NOT NULL,
    run_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    period_begin     VARCHAR(16),
    period_end_col   VARCHAR(16),
    industry         VARCHAR(64),
    appetite_level   VARCHAR(8),
    persona          VARCHAR(64),
    data_mode        VARCHAR(8),
    signal_set       TEXT[],
    forecast_metric  VARCHAR(64),
    forecast_horizon INT,
    completed        BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_runs_ticker ON risk_loop_runs (ticker, run_at DESC);

CREATE TABLE IF NOT EXISTS financial_ratios (
    id                 SERIAL PRIMARY KEY,
    run_id             INT NOT NULL REFERENCES risk_loop_runs(id) UNIQUE,
    revenue_now        NUMERIC, revenue_prev       NUMERIC,
    revenue_growth     NUMERIC, gross_margin       NUMERIC,
    gross_margin_prev  NUMERIC, gross_margin_index NUMERIC,
    net_margin         NUMERIC, fcf_margin         NUMERIC,
    rd_intensity       NUMERIC, sga_intensity      NUMERIC,
    asset_growth       NUMERIC, cash_ratio         NUMERIC,
    tata               NUMERIC, dsri               NUMERIC,
    sgi                NUMERIC, assets_now         NUMERIC,
    cash_now           NUMERIC, net_income_now     NUMERIC,
    operating_cashflow NUMERIC
);

CREATE TABLE IF NOT EXISTS beneish_mscores (
    id             SERIAL PRIMARY KEY,
    run_id         INT         NOT NULL REFERENCES risk_loop_runs(id) UNIQUE,
    m_score        NUMERIC(6,3),
    interpretation VARCHAR(32),
    rag_status     VARCHAR(8),
    dsri_input     NUMERIC,
    gmi_input      NUMERIC,
    sgi_input      NUMERIC,
    tata_input     NUMERIC,
    missing_inputs TEXT[]
);

CREATE TABLE IF NOT EXISTS altman_zscores (
    id             SERIAL PRIMARY KEY,
    run_id         INT         NOT NULL REFERENCES risk_loop_runs(id) UNIQUE,
    z_score        NUMERIC(6,3),
    interpretation VARCHAR(32),
    rag_status     VARCHAR(8),
    x1_input       NUMERIC,
    x2_input       NUMERIC,
    x3_input       NUMERIC,
    x4_input       NUMERIC,
    missing_inputs TEXT[]
);

-- Forecast accuracy tracking: one row per (ticker, metric, target quarter,
-- horizon, model) point forecast ever made. predicted_value is recorded the
-- moment a forecast is produced; actual_value is filled in later, once that
-- quarter's real XBRL data arrives (see reconcile_forecast_actuals). This is
-- what lets the forecasting ensemble eventually be judged against genuinely
-- new out-of-sample evidence quarter over quarter, rather than only the
-- walk-forward backtest re-run over the same fixed history every time —
-- with real 13-quarter-ish histories that backtest has as few as 4-5
-- out-of-sample steps to calibrate ensemble weights from.
-- ON CONFLICT DO NOTHING on insert: if this (ticker, metric, target_quarter,
-- horizon, model) combination was already forecast once, keep the ORIGINAL
-- prediction — the whole point is capturing what was genuinely predicted
-- ahead of time, not letting a later, hindsight-informed re-run overwrite it.
CREATE TABLE IF NOT EXISTS forecast_accuracy_history (
    id                  BIGSERIAL PRIMARY KEY,
    company_id          INT REFERENCES companies(id),
    ticker              VARCHAR(16) NOT NULL,
    metric              VARCHAR(64) NOT NULL,
    forecast_made_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    target_quarter_end  DATE NOT NULL,
    horizon             SMALLINT NOT NULL,
    model               VARCHAR(32) NOT NULL,
    predicted_value     DOUBLE PRECISION NOT NULL,
    actual_value        DOUBLE PRECISION,
    reconciled_at       TIMESTAMPTZ,
    UNIQUE (ticker, metric, target_quarter_end, horizon, model)
);
CREATE INDEX IF NOT EXISTS idx_forecast_accuracy_ticker
    ON forecast_accuracy_history (ticker, metric, target_quarter_end);
CREATE INDEX IF NOT EXISTS idx_forecast_accuracy_unreconciled
    ON forecast_accuracy_history (ticker, metric) WHERE actual_value IS NULL;

CREATE TABLE IF NOT EXISTS risk_scores (
    id             SERIAL PRIMARY KEY,
    run_id         INT          NOT NULL REFERENCES risk_loop_runs(id),
    risk_ref       VARCHAR(16),
    risk_name      VARCHAR(128) NOT NULL,
    category       VARCHAR(64),
    base_score     NUMERIC(4,1),
    delta          NUMERIC(5,2),
    score          NUMERIC(4,2),
    rag_status     VARCHAR(8),
    velocity       SMALLINT,
    control_env    VARCHAR(16),
    peer_benchmark VARCHAR(32)
);
CREATE INDEX IF NOT EXISTS idx_risk_scores_run ON risk_scores (run_id);

CREATE TABLE IF NOT EXISTS scenario_analyses (
    id                      SERIAL PRIMARY KEY,
    run_id                  INT NOT NULL REFERENCES risk_loop_runs(id),
    scenario                VARCHAR(8) NOT NULL,
    revenue_change_pct      NUMERIC,
    projected_revenue       NUMERIC,
    gross_margin_impact_bps INT,
    projected_gross_margin  NUMERIC,
    indicative_net_income   NUMERIC,
    narrative               TEXT
);

CREATE TABLE IF NOT EXISTS grey_swan_models (
    id                      SERIAL PRIMARY KEY,
    run_id                  INT NOT NULL REFERENCES risk_loop_runs(id) UNIQUE,
    trigger_risk            VARCHAR(128),
    trigger_category        VARCHAR(64),
    trigger_base_score      NUMERIC,
    trigger_velocity        SMALLINT,
    quarterly_revenue_proxy NUMERIC,
    peak_score              NUMERIC,
    peak_rag                VARCHAR(8),
    timeline                JSONB
);

CREATE TABLE IF NOT EXISTS forecasts (
    id              SERIAL PRIMARY KEY,
    run_id          INT         NOT NULL REFERENCES risk_loop_runs(id),
    metric          VARCHAR(64) NOT NULL,
    model           VARCHAR(32) NOT NULL,
    horizon_quarter SMALLINT    NOT NULL,
    point_forecast  NUMERIC,
    ci_lower        NUMERIC,
    ci_upper        NUMERIC,
    sigma           NUMERIC,
    UNIQUE (run_id, metric, model, horizon_quarter)
);

CREATE TABLE IF NOT EXISTS backtest_metrics (
    id                SERIAL PRIMARY KEY,
    run_id            INT         NOT NULL REFERENCES risk_loop_runs(id),
    metric            VARCHAR(64),
    model             VARCHAR(32),
    n_observations    INT,
    n_backtest_steps  INT,
    mape              NUMERIC,
    rmse              NUMERIC,
    r_squared         NUMERIC,
    precision_score   NUMERIC,
    recall_score      NUMERIC,
    f1_score          NUMERIC,
    calibrated_weight NUMERIC
);

CREATE TABLE IF NOT EXISTS qoq_revenue_momentum (
    id          SERIAL PRIMARY KEY,
    run_id      INT  NOT NULL REFERENCES risk_loop_runs(id),
    quarter_end DATE NOT NULL,
    qoq_pct     NUMERIC,
    sentiment   VARCHAR(16)
);

CREATE TABLE IF NOT EXISTS rss_articles (
    id                SERIAL PRIMARY KEY,
    company_id        INT REFERENCES companies(id),
    feed_name         VARCHAR(128),
    feed_url          TEXT,
    industry_category VARCHAR(64),
    title             TEXT NOT NULL,
    article_url       TEXT,
    published_at      TIMESTAMPTZ,
    summary           TEXT,
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (title, feed_name)
);
CREATE INDEX IF NOT EXISTS idx_rss_articles_co ON rss_articles (company_id, published_at DESC);

CREATE TABLE IF NOT EXISTS rss_signals (
    id              SERIAL PRIMARY KEY,
    run_id          INT REFERENCES risk_loop_runs(id),
    article_id      INT REFERENCES rss_articles(id),
    domain          VARCHAR(64),
    relevance_score NUMERIC,
    severity_score  NUMERIC,
    velocity_delta  SMALLINT
);

CREATE TABLE IF NOT EXISTS hitl_sessions (
    id           SERIAL PRIMARY KEY,
    run_id       INT NOT NULL REFERENCES risk_loop_runs(id) UNIQUE,
    persona      VARCHAR(64),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    gate1_status VARCHAR(16),
    gate2_status VARCHAR(16)
);

CREATE TABLE IF NOT EXISTS risk_approvals (
    id                SERIAL PRIMARY KEY,
    session_id        INT         NOT NULL REFERENCES hitl_sessions(id),
    risk_ref          VARCHAR(16),
    risk_name         VARCHAR(128),
    status            VARCHAR(16) NOT NULL,
    adjusted_rag      VARCHAR(8),
    adjusted_score    NUMERIC,
    adjusted_velocity SMALLINT,
    adjusted_ce       VARCHAR(16),
    rationale         TEXT,
    appetite_level    VARCHAR(8),
    adjusted_by       VARCHAR(64),
    adjusted_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS risk_approval_signoffs (
    id          SERIAL PRIMARY KEY,
    approval_id INT        NOT NULL REFERENCES risk_approvals(id),
    role        VARCHAR(8) NOT NULL,
    signatory   VARCHAR(128),
    signed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS objective_approvals (
    id                       SERIAL PRIMARY KEY,
    session_id               INT NOT NULL REFERENCES hitl_sessions(id),
    obj_id                   VARCHAR(16) NOT NULL,
    objective_text           TEXT,
    status                   VARCHAR(16) NOT NULL,
    adjusted_objective_text  TEXT,
    adjusted_priority        VARCHAR(4),
    adjusted_sprint          INT,
    adjusted_hours           INT,
    adjusted_linked_risks    TEXT[],
    adjusted_controls        TEXT[],
    residual_risk_reduction  NUMERIC,
    rationale                TEXT,
    adjusted_by              VARCHAR(64),
    adjusted_at              TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS objective_approval_signoffs (
    id          SERIAL PRIMARY KEY,
    approval_id INT        NOT NULL REFERENCES objective_approvals(id),
    role        VARCHAR(8) NOT NULL,
    signatory   VARCHAR(128),
    signed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SOX HITL Gate S1 — materiality basis approval (single item per session)
CREATE TABLE IF NOT EXISTS sox_materiality_approvals (
    id                       SERIAL PRIMARY KEY,
    session_id               INT NOT NULL REFERENCES hitl_sessions(id),
    status                   VARCHAR(16) NOT NULL,
    adjusted_materiality_pct NUMERIC(5,3),
    adjusted_performance_pct NUMERIC(5,3),
    rationale                TEXT,
    adjusted_by              VARCHAR(64),
    adjusted_at              TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sox_materiality_approval_signoffs (
    id          SERIAL PRIMARY KEY,
    approval_id INT        NOT NULL REFERENCES sox_materiality_approvals(id),
    role        VARCHAR(8) NOT NULL,
    signatory   VARCHAR(128),
    signed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SOX HITL Gate S1 — per-account significant-account scope approval
CREATE TABLE IF NOT EXISTS sox_account_approvals (
    id                SERIAL PRIMARY KEY,
    session_id        INT NOT NULL REFERENCES hitl_sessions(id),
    account_id        VARCHAR(64) NOT NULL,
    account_name      VARCHAR(128),
    status            VARCHAR(16) NOT NULL,
    adjusted_in_scope BOOLEAN,
    adjusted_priority VARCHAR(4),
    rationale         TEXT,
    adjusted_by       VARCHAR(64),
    adjusted_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sox_account_approval_signoffs (
    id          SERIAL PRIMARY KEY,
    approval_id INT        NOT NULL REFERENCES sox_account_approvals(id),
    role        VARCHAR(8) NOT NULL,
    signatory   VARCHAR(128),
    signed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SOX HITL Gate S2 — per-process coverage approval
CREATE TABLE IF NOT EXISTS sox_process_approvals (
    id                      SERIAL PRIMARY KEY,
    session_id              INT NOT NULL REFERENCES hitl_sessions(id),
    process_id              VARCHAR(64) NOT NULL,
    process_name            VARCHAR(128),
    status                  VARCHAR(16) NOT NULL,
    adjusted_coverage_level VARCHAR(8),
    rationale               TEXT,
    adjusted_by             VARCHAR(64),
    adjusted_at             TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sox_process_approval_signoffs (
    id          SERIAL PRIMARY KEY,
    approval_id INT        NOT NULL REFERENCES sox_process_approvals(id),
    role        VARCHAR(8) NOT NULL,
    signatory   VARCHAR(128),
    signed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Real 2-stage (preparer -> manager) HITL approval workflow, unified across all
-- gate types (Enterprise Risk Gate 1/2, SOX Gate S1/S2). Replaces the fixed
-- 3-name CAE/CFO/Audit-Committee signoff chain above on the write path — those
-- tables are left in place (unused) for historical data, nothing reads them
-- going forward. prepared_by / manager_id / reviewed_by are soft references to
-- auth.users(id) (no FK: db.init_db() runs before auth_db.init_auth_db(), so
-- auth.users may not exist yet when this table is first created) — identity is
-- always populated server-side from the authenticated session, never trusted
-- from the request body.
CREATE TABLE IF NOT EXISTS approval_tasks (
    id                BIGSERIAL   PRIMARY KEY,
    run_id            INT         NOT NULL REFERENCES risk_loop_runs(id),
    gate_type         VARCHAR(24) NOT NULL,  -- 'risk' | 'objective' | 'sox_materiality' | 'sox_account' | 'sox_process'
    item_ref          VARCHAR(64) NOT NULL,  -- risk_ref / obj_id / 'materiality' / account_id / process_id
    item_label        VARCHAR(255),
    status            VARCHAR(24) NOT NULL DEFAULT 'pending', -- pending | approved | submitted | manager_approved | rejected
    disposition       VARCHAR(16),           -- 'approved' | 'adjusted' — what the preparer did
    adjustments       JSONB,
    rationale         TEXT,
    prepared_by       BIGINT,
    prepared_by_name  VARCHAR(128),
    prepared_at       TIMESTAMPTZ,
    manager_id        BIGINT,
    manager_name      VARCHAR(128),
    reviewed_by       BIGINT,
    reviewed_by_name  VARCHAR(128),
    reviewed_at       TIMESTAMPTZ,
    review_comment    TEXT,
    ai_suggested      JSONB,       -- AI "Suggest with AI" values, keyed like `adjustments`; NULL if no AI involved
    ai_accepted       BOOLEAN,     -- true=preparer kept AI's values as-is, false=overrode, NULL=no AI suggestion
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, gate_type, item_ref)
);
CREATE INDEX IF NOT EXISTS idx_approval_tasks_manager ON approval_tasks (manager_id, status);
CREATE INDEX IF NOT EXISTS idx_approval_tasks_run ON approval_tasks (run_id, gate_type);
-- DevOps Monitoring's devops_scm_exception gate_type has no risk_loop_runs
-- association — a NULL run_id still satisfies the FK (NULL never violates a
-- REFERENCES constraint), same reasoning as adjudicated_tool_calls.session_id
-- above. Known limitation: two devops_scm_exception submissions for the same
-- item_ref both have run_id=NULL, and Postgres treats NULLs as distinct for
-- UNIQUE(run_id, gate_type, item_ref) — so they insert as two rows rather
-- than upserting one. Acceptable for now; risk_waivers' own unique-active-hash
-- index is what actually prevents two simultaneous ACTIVE waivers.
ALTER TABLE approval_tasks ALTER COLUMN run_id DROP NOT NULL;
-- remediation_github gate_type (closed-loop remediation — see
-- remediation_endpoints.py / approvals_endpoints.py's execute-on-approve
-- branch): the outcome of the actual GitHub write (issue/PR url, or an
-- error) fired once a manager approves. NULL until execution is attempted;
-- {"error": "..."} on failure so the Approval Inbox can offer a retry
-- instead of the task silently vanishing once decided.
ALTER TABLE approval_tasks ADD COLUMN IF NOT EXISTS execution_result JSONB;

CREATE TABLE IF NOT EXISTS audit_objectives (
    id                      SERIAL PRIMARY KEY,
    run_id                  INT         NOT NULL REFERENCES risk_loop_runs(id),
    obj_id                  VARCHAR(16) NOT NULL,
    objective_text          TEXT        NOT NULL,
    priority                VARCHAR(4),
    linked_risk_ref         VARCHAR(16),
    linked_risks            TEXT[],
    controls                TEXT[],
    hours                   INT,
    sprint                  INT,
    residual_risk_reduction NUMERIC,
    UNIQUE (run_id, obj_id)
);

CREATE TABLE IF NOT EXISTS manual_audit_plans (
    id              SERIAL PRIMARY KEY,
    run_id          INT NOT NULL REFERENCES risk_loop_runs(id),
    title           VARCHAR(255),
    when_scheduled  VARCHAR(64),
    linked_risk_ref VARCHAR(16),
    added_by        VARCHAR(64),
    added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cem_events (
    id                   SERIAL PRIMARY KEY,
    run_id               INT NOT NULL REFERENCES risk_loop_runs(id),
    control              VARCHAR(255),
    area                 VARCHAR(64),
    risk_label           VARCHAR(128),
    severity             VARCHAR(4),
    exposure             VARCHAR(64),
    category             VARCHAR(64),
    root_cause_narrative TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS loop_log_entries (
    id        BIGSERIAL PRIMARY KEY,
    run_id    INT         NOT NULL REFERENCES risk_loop_runs(id),
    logged_at TIMESTAMPTZ NOT NULL,
    message   TEXT        NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_loop_log_run ON loop_log_entries (run_id, logged_at);

CREATE TABLE IF NOT EXISTS token_usage_sessions (
    id                       SERIAL PRIMARY KEY,
    session_name             VARCHAR(128) NOT NULL UNIQUE,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    total_calls              INT          NOT NULL DEFAULT 0,
    total_input_tokens       BIGINT       NOT NULL DEFAULT 0,
    total_output_tokens      BIGINT       NOT NULL DEFAULT 0,
    total_cache_read_tokens  BIGINT       NOT NULL DEFAULT 0,
    total_cache_write_tokens BIGINT       NOT NULL DEFAULT 0,
    total_cost_usd           NUMERIC(12,8) NOT NULL DEFAULT 0
);

-- user_id is a soft reference to auth.users(id) — no FK constraint, since
-- db.init_db() runs before auth_db.init_auth_db() and auth.users may not
-- exist yet when this table is first created (see approval_tasks above for
-- the same pattern). username is denormalized alongside it so history
-- survives a user being deleted later.
CREATE TABLE IF NOT EXISTS token_usage_calls (
    id                 SERIAL PRIMARY KEY,
    session_id         INT         NOT NULL REFERENCES token_usage_sessions(id),
    called_at          TIMESTAMPTZ NOT NULL,
    model              VARCHAR(64),
    label              VARCHAR(128),
    input_tokens       INT,
    output_tokens      INT,
    cache_read_tokens  INT,
    cache_write_tokens INT,
    cost_usd           NUMERIC(12,8),
    user_id            BIGINT,
    username           TEXT
);

-- AI-generated analyses (LLM provenance, kept distinct from human HITL decisions).
-- One row per model output: gate recommendations, narrative analysis, persona
-- briefs, the investigation agent transcript, generated audit reports.
CREATE TABLE IF NOT EXISTS ai_analyses (
    id            BIGSERIAL PRIMARY KEY,
    run_id        INT REFERENCES risk_loop_runs(id),
    ticker        VARCHAR(16),
    kind          VARCHAR(48) NOT NULL,
    subject_ref   VARCHAR(64),
    model         VARCHAR(64),
    effort        VARCHAR(16),
    content       JSONB       NOT NULL,
    summary       TEXT,
    input_tokens  INT,
    output_tokens INT,
    cost_usd      NUMERIC(12,8),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_analyses_run  ON ai_analyses (run_id, kind);
CREATE INDEX IF NOT EXISTS idx_ai_analyses_tick ON ai_analyses (ticker, kind, created_at DESC);

-- ── SOX Scoping ─────────────────────────────────────────────────────────────
-- Materiality configuration per company + fiscal year
CREATE TABLE IF NOT EXISTS sox_scoping_configs (
    id                   SERIAL PRIMARY KEY,
    company_id           INT NOT NULL REFERENCES companies(id),
    fiscal_year          VARCHAR(8)  NOT NULL,
    fiscal_year_end      DATE,
    materiality_basis    VARCHAR(16) NOT NULL DEFAULT 'pretax_income',
    materiality_pct      NUMERIC(5,3) NOT NULL DEFAULT 5.0,
    performance_mat_pct  NUMERIC(5,3) NOT NULL DEFAULT 75.0,
    scope_note           TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, fiscal_year)
);

-- Optional geography / business-segment financial breakdowns (historical actuals)
CREATE TABLE IF NOT EXISTS sox_financial_segments (
    id                 SERIAL PRIMARY KEY,
    company_id         INT NOT NULL REFERENCES companies(id),
    run_id             INT REFERENCES risk_loop_runs(id),
    fiscal_year        VARCHAR(8),
    segment_type       VARCHAR(16)  NOT NULL,
    segment_name       VARCHAR(128) NOT NULL,
    revenue            NUMERIC,
    revenue_pct        NUMERIC(5,2),
    gross_profit       NUMERIC,
    operating_income   NUMERIC,
    assets             NUMERIC,
    rev_growth_yoy_pct NUMERIC(7,3),
    net_income         NUMERIC,
    gross_margin_pct   NUMERIC(7,3),
    op_margin_pct      NUMERIC(7,3),
    net_margin_pct     NUMERIC(7,3),
    source             VARCHAR(64),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, fiscal_year, segment_type, segment_name)
);

-- Per-run segment / geography forecast KPIs (target company, forward-looking)
CREATE TABLE IF NOT EXISTS segment_forecasts (
    id             SERIAL PRIMARY KEY,
    run_id         INT          NOT NULL REFERENCES risk_loop_runs(id),
    segment_type   VARCHAR(16)  NOT NULL,   -- 'geography' | 'business_segment'
    segment_name   VARCHAR(128) NOT NULL,
    fiscal_year    VARCHAR(8),
    revenue_m      NUMERIC,                  -- annualised revenue $M
    revenue_pct    NUMERIC(5,2),             -- % of consolidated total
    rev_growth_yoy NUMERIC(7,3),             -- YoY growth %
    gross_margin   NUMERIC(7,3),             -- GM %
    op_margin      NUMERIC(7,3),             -- OM %
    net_margin     NUMERIC(7,3),             -- NM %
    source         VARCHAR(32),              -- 'db' | 'seeded' | 'estimated'
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, segment_type, segment_name)
);
CREATE INDEX IF NOT EXISTS idx_seg_forecasts_run ON segment_forecasts (run_id, segment_type);

-- Peer company segment / geography breakdowns for benchmarking
CREATE TABLE IF NOT EXISTS peer_segment_financials (
    id           SERIAL PRIMARY KEY,
    company_id   INT          NOT NULL REFERENCES companies(id),
    peer_ticker  VARCHAR(16)  NOT NULL,
    peer_name    VARCHAR(255),
    fiscal_year  VARCHAR(8),
    segment_type VARCHAR(16)  NOT NULL,   -- 'geography' | 'business_segment'
    segment_name VARCHAR(128) NOT NULL,
    revenue_m    NUMERIC,
    revenue_pct  NUMERIC(5,2),
    gross_margin NUMERIC(7,3),
    op_margin    NUMERIC(7,3),
    net_margin   NUMERIC(7,3),
    source       VARCHAR(32),             -- 'edgar' | 'manual' | 'estimated'
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, peer_ticker, fiscal_year, segment_type, segment_name)
);
CREATE INDEX IF NOT EXISTS idx_peer_seg_co ON peer_segment_financials (company_id, peer_ticker, fiscal_year);

-- Extensible system registry: ERP, consolidation, reporting, sub-ledgers, etc.
CREATE TABLE IF NOT EXISTS sox_systems (
    id               SERIAL PRIMARY KEY,
    company_id       INT NOT NULL REFERENCES companies(id),
    system_name      VARCHAR(128) NOT NULL,
    system_type      VARCHAR(32)  NOT NULL,
    vendor           VARCHAR(128),
    version          VARCHAR(32),
    linked_processes TEXT[],
    significance     VARCHAR(8)   NOT NULL DEFAULT 'medium',
    active           BOOLEAN      NOT NULL DEFAULT TRUE,
    notes            TEXT,
    added_by         VARCHAR(128),
    added_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, system_name)
);
CREATE INDEX IF NOT EXISTS idx_sox_systems_co ON sox_systems (company_id);

-- Computed SOX scope per risk-loop run
CREATE TABLE IF NOT EXISTS sox_scoping_results (
    id                      SERIAL PRIMARY KEY,
    run_id                  INT NOT NULL REFERENCES risk_loop_runs(id),
    company_id              INT NOT NULL REFERENCES companies(id),
    fiscal_year             VARCHAR(8) NOT NULL,
    scoped_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    planning_materiality    NUMERIC,
    performance_materiality NUMERIC,
    trivial_threshold       NUMERIC,
    materiality_basis       VARCHAR(128),
    revenue_forecast_fy     NUMERIC,
    pretax_income_estimate  NUMERIC,
    accounts_in_scope       JSONB,
    processes_in_scope      JSONB,
    systems_in_scope        JSONB,
    segments_coverage       JSONB,
    trigger_reason          TEXT,
    input_hash              VARCHAR(64),
    UNIQUE (run_id)
);
CREATE INDEX IF NOT EXISTS idx_sox_results_co ON sox_scoping_results (company_id, scoped_at DESC);

-- Audit trail of rescoping triggers
CREATE TABLE IF NOT EXISTS sox_rescoping_triggers (
    id             BIGSERIAL PRIMARY KEY,
    company_id     INT NOT NULL REFERENCES companies(id),
    triggered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trigger_type   VARCHAR(32) NOT NULL,
    trigger_detail JSONB,
    prev_run_id    INT REFERENCES risk_loop_runs(id),
    new_run_id     INT REFERENCES risk_loop_runs(id),
    rescoped       BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_sox_rescoping_co ON sox_rescoping_triggers (company_id, triggered_at DESC);

-- User-editable detail/override records for individual SOX significant accounts
-- (keyed to the SOX_ACCOUNTS catalogue ids in sox_scoping_tool.py)
CREATE TABLE IF NOT EXISTS sox_account_details (
    id               SERIAL PRIMARY KEY,
    company_id       INT NOT NULL REFERENCES companies(id),
    account_id       VARCHAR(64) NOT NULL,
    geography        TEXT[],
    segments         TEXT[],
    notes            TEXT,
    manual_in_scope  BOOLEAN,
    manual_priority  VARCHAR(4),
    updated_by       VARCHAR(128),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, account_id)
);
CREATE INDEX IF NOT EXISTS idx_sox_acct_details_co ON sox_account_details (company_id);

-- User-editable detail/override records for individual SOX processes
-- (keyed to the SOX_PROCESSES catalogue ids in sox_scoping_tool.py)
CREATE TABLE IF NOT EXISTS sox_process_details (
    id                     SERIAL PRIMARY KEY,
    company_id             INT NOT NULL REFERENCES companies(id),
    process_id             VARCHAR(64) NOT NULL,
    geography              TEXT[],
    segments               TEXT[],
    notes                  TEXT,
    manual_coverage_level  VARCHAR(8),
    estimated_exposure     NUMERIC,
    updated_by             VARCHAR(128),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, process_id)
);
CREATE INDEX IF NOT EXISTS idx_sox_proc_details_co ON sox_process_details (company_id);

-- Risks-as-Code artifacts: OSCAL and COSO ERM / ISO 31000 outputs per run
CREATE TABLE IF NOT EXISTS risks_as_code_artifacts (
    id           BIGSERIAL    PRIMARY KEY,
    run_id       INT          REFERENCES risk_loop_runs(id) ON DELETE CASCADE,
    ticker       VARCHAR(16)  NOT NULL,
    framework    VARCHAR(32)  NOT NULL,  -- 'oscal' | 'coso_erm'
    content_yaml TEXT         NOT NULL,
    generated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, framework)
);
CREATE INDEX IF NOT EXISTS idx_rac_artifacts_ticker ON risks_as_code_artifacts (ticker, framework, generated_at DESC);

-- Code editor configs: Risk-as-Code and Policy-as-Code YAML rule sets (global, keyed by storageKey)
CREATE TABLE IF NOT EXISTS code_editor_configs (
    id          BIGSERIAL    PRIMARY KEY,
    storage_key VARCHAR(64)  NOT NULL UNIQUE,  -- mirrors localStorage key, e.g. 'dendrai.riskcode'
    content     TEXT         NOT NULL,
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- CEM event templates: seeded from mock-data.js defaults, editable at runtime
CREATE TABLE IF NOT EXISTS cem_event_templates (
    id          SERIAL       PRIMARY KEY,
    control     VARCHAR(255) NOT NULL,
    area        VARCHAR(64)  NOT NULL,
    risk_label  VARCHAR(128) NOT NULL,
    severity    VARCHAR(4)   NOT NULL DEFAULT 'P2',
    exposure    VARCHAR(64),
    category    VARCHAR(64),
    rc_narrative TEXT,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    sort_order  INT          NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (control, area)
);
CREATE INDEX IF NOT EXISTS idx_cem_templates_active ON cem_event_templates (is_active, sort_order);

-- Generic key-value app config (stores MATRIX_FRAMEWORKS, PRESET_FRAMEWORKS, etc. as JSON)
CREATE TABLE IF NOT EXISTS app_config (
    key        VARCHAR(128) PRIMARY KEY,
    value_json JSONB        NOT NULL,
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Analyst KPI quarterly time series (EPS, OpMargin, NetIncome, FCF, EBITDA) ─
CREATE TABLE IF NOT EXISTS analyst_kpi_series (
    id          SERIAL PRIMARY KEY,
    run_id      INT         NOT NULL REFERENCES risk_loop_runs(id),
    metric_name VARCHAR(64) NOT NULL,
    quarter_end DATE        NOT NULL,
    value       NUMERIC,
    UNIQUE (run_id, metric_name, quarter_end)
);
CREATE INDEX IF NOT EXISTS idx_analyst_kpi ON analyst_kpi_series (run_id, metric_name);

-- ── Risk Register Review ─────────────────────────────────────────────────────
-- Tracks interactive review sessions for internal pipeline risks and
-- externally ingested framework risk catalogs.

CREATE TABLE IF NOT EXISTS risk_register_reviews (
    id           SERIAL PRIMARY KEY,
    run_id       INT          REFERENCES risk_loop_runs(id) ON DELETE SET NULL,
    review_type  VARCHAR(16)  NOT NULL DEFAULT 'internal',
    framework    VARCHAR(128),
    status       VARCHAR(16)  NOT NULL DEFAULT 'in_progress',
    rac_yaml     TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_rrr_run ON risk_register_reviews (run_id, created_at DESC);

CREATE TABLE IF NOT EXISTS review_risk_states (
    id                BIGSERIAL    PRIMARY KEY,
    review_id         INT          NOT NULL REFERENCES risk_register_reviews(id) ON DELETE CASCADE,
    risk_ref          VARCHAR(32)  NOT NULL,
    original_wording  TEXT,
    current_wording   TEXT,
    included          BOOLEAN      NOT NULL DEFAULT TRUE,
    reason_for_change TEXT,
    controls_assigned JSONB        NOT NULL DEFAULT '[]',
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (review_id, risk_ref)
);
CREATE INDEX IF NOT EXISTS idx_rrs_review ON review_risk_states (review_id);

CREATE TABLE IF NOT EXISTS controls_library (
    id           SERIAL PRIMARY KEY,
    control_ref  VARCHAR(32)  NOT NULL UNIQUE,
    framework    VARCHAR(64),
    name         VARCHAR(255) NOT NULL,
    description  TEXT,
    category     VARCHAR(64),
    domain       VARCHAR(64),
    tags         TEXT[]
);

-- Manual, curator-set link from a Risk & Controls Register control to the
-- controls_catalog control_id (PaC/CaC/RaC's shared vocabulary — see that
-- table's own comment) it corresponds to. Deliberately NOT a foreign key:
-- controls_catalog rows are reseeded/regenerated as Rego modules evolve, so a
-- hard FK would be brittle; validity is checked at the API layer instead,
-- same soft-reference convention already used by control_ref/risk_ref
-- columns elsewhere (risk_control_mappings, cem_event_risk_links). This is
-- an explicit human-asserted link, not auto-inferred — same "no ungrounded
-- generation" guardrail as the framework crosswalk columns below.
ALTER TABLE controls_library ADD COLUMN IF NOT EXISTS pac_control_id VARCHAR(32);

CREATE TABLE IF NOT EXISTS risk_control_mappings (
    id             SERIAL PRIMARY KEY,
    review_id      INT         NOT NULL REFERENCES risk_register_reviews(id) ON DELETE CASCADE,
    risk_ref       VARCHAR(32) NOT NULL,
    control_ref    VARCHAR(32) NOT NULL,
    mapping_type   VARCHAR(16) NOT NULL DEFAULT 'auto',
    generate_code  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (review_id, risk_ref, control_ref)
);
CREATE INDEX IF NOT EXISTS idx_rcm_review ON risk_control_mappings (review_id, risk_ref);

CREATE TABLE IF NOT EXISTS framework_risk_catalogs (
    id             SERIAL PRIMARY KEY,
    framework_name VARCHAR(128) NOT NULL,
    framework_ver  VARCHAR(32),
    risks_json     JSONB        NOT NULL,
    fetched_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (framework_name, framework_ver)
);

-- Graph: directed edges between risks for the same company.
-- relationship_type: 'correlates_with' (same category, bidirectional),
--   'amplifies' (high-score/velocity risk drives others in category),
--   'similar_to' (embedding cosine similarity, populated when pgvector available).
-- strength: 0.000–1.000; higher = stronger relationship.
-- source: 'computed' (rule-based) | 'embedding' (vector similarity) | 'manual'.
CREATE TABLE IF NOT EXISTS risk_relationships (
    id                SERIAL PRIMARY KEY,
    company_id        INT          NOT NULL REFERENCES companies(id),
    from_risk_ref     VARCHAR(32)  NOT NULL,
    to_risk_ref       VARCHAR(32)  NOT NULL,
    relationship_type VARCHAR(32)  NOT NULL,
    strength          NUMERIC(4,3),
    source            VARCHAR(32)  NOT NULL DEFAULT 'computed',
    run_id            INT          REFERENCES risk_loop_runs(id),
    computed_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, from_risk_ref, to_risk_ref, relationship_type)
);
CREATE INDEX IF NOT EXISTS idx_risk_rel_from ON risk_relationships (company_id, from_risk_ref);
CREATE INDEX IF NOT EXISTS idx_risk_rel_to   ON risk_relationships (company_id, to_risk_ref);

-- Graph: which risks each scenario amplifies (FK from scenario_analyses → risk_ref).
-- impact_multiplier: expected score amplification (e.g. 1.5 = risk 50% worse under this scenario).
CREATE TABLE IF NOT EXISTS scenario_risk_impacts (
    id                SERIAL PRIMARY KEY,
    scenario_id       INT         NOT NULL REFERENCES scenario_analyses(id) ON DELETE CASCADE,
    risk_ref          VARCHAR(32) NOT NULL,
    impact_multiplier NUMERIC(5,3),
    impact_narrative  TEXT,
    UNIQUE (scenario_id, risk_ref)
);
CREATE INDEX IF NOT EXISTS idx_sri_scenario ON scenario_risk_impacts (scenario_id);
CREATE INDEX IF NOT EXISTS idx_sri_risk     ON scenario_risk_impacts (risk_ref);

-- Graph: normalised FK edges from CEM events to the risks and controls they affect.
-- Resolves cem_events.risk_label (VARCHAR) → structured risk_ref and control_ref.
-- link_type: 'affected' (risk impacted by event) | 'caused_by_control' (control failure).
CREATE TABLE IF NOT EXISTS cem_event_risk_links (
    id           SERIAL PRIMARY KEY,
    cem_event_id INT         NOT NULL REFERENCES cem_events(id) ON DELETE CASCADE,
    risk_ref     VARCHAR(32) NOT NULL,
    control_ref  VARCHAR(32),
    link_type    VARCHAR(32) NOT NULL DEFAULT 'affected',
    UNIQUE (cem_event_id, risk_ref)
);
CREATE INDEX IF NOT EXISTS idx_cerl_event ON cem_event_risk_links (cem_event_id);
CREATE INDEX IF NOT EXISTS idx_cerl_risk  ON cem_event_risk_links (risk_ref);

-- Controls-as-Code artifacts: Rego representation of the controls library (global, not run-bound)
CREATE TABLE IF NOT EXISTS controls_as_code_artifacts (
    id           BIGSERIAL    PRIMARY KEY,
    ticker       VARCHAR(16),
    run_id       INT          REFERENCES risk_loop_runs(id) ON DELETE SET NULL,
    content_rego TEXT         NOT NULL,
    generated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cac_artifacts_ticker ON controls_as_code_artifacts (ticker, generated_at DESC);

-- Canonical control catalog — the real, structured relationship PaC, CaC,
-- and RaC were each missing. Two sources register into the same table
-- rather than being forced into one ID vocabulary: 'pac_rego' entries are
-- parsed straight out of the Rego modules' own msg strings (real, already
-- consistently formatted); 'manual' entries are the pre-existing
-- business-level control set used by RaC's Review UI. They stay distinct
-- (different abstraction levels — automated technical enforcement vs.
-- auditor-assigned business control), but now share one lookup table.
CREATE TABLE IF NOT EXISTS controls_catalog (
    control_id  VARCHAR(32)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    process     VARCHAR(64),   -- PaC process key, when known ('itgc' etc.); NULL for manual/business-level entries
    source      VARCHAR(16)  NOT NULL DEFAULT 'manual',  -- 'pac_rego' | 'manual'
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_controls_catalog_process ON controls_catalog (process);

-- Negative-testing assurance metadata (PaC negative-control effort, see
-- pac_contracts.py/pac_negative_tests.py). "We have 400 controls" and "we
-- have 400 controls, 12 of which nothing proves are working" are very
-- different claims — these columns are what makes the second one answerable.
-- last_fired_at: most recent real production adjudication whose
--   policy_violations included this control_id (db.get_control_fire_stats).
-- last_verified_at/last_test_passed: most recent negative-control corpus run
--   (pac_negative_tests.run_corpus) that exercised a must-fire fixture for
--   this control_id, and whether it passed.
ALTER TABLE controls_catalog ADD COLUMN IF NOT EXISTS last_fired_at     TIMESTAMPTZ;
ALTER TABLE controls_catalog ADD COLUMN IF NOT EXISTS last_verified_at  TIMESTAMPTZ;
ALTER TABLE controls_catalog ADD COLUMN IF NOT EXISTS last_test_passed BOOLEAN;

-- Framework crosswalk metadata (Executive Compliance Scorecard). Curated
-- mappings only (db.seed_framework_mappings, called once at startup from a
-- static Python dict — see framework_mappings.py) — never LLM-generated or
-- auto-inferred, same "no ungrounded generation" guardrail as RaC/CaC
-- (commit 2b98f45's retired Framework Sync). A control_id with no mapping
-- row here simply isn't scored against any framework yet; that's an honest
-- gap, not hidden by a fabricated mapping.
ALTER TABLE controls_catalog ADD COLUMN IF NOT EXISTS soc2_criteria   TEXT[];  -- e.g. {CC6.1,CC7.2}
ALTER TABLE controls_catalog ADD COLUMN IF NOT EXISTS nist_800_53     TEXT[];  -- e.g. {AC-3,AU-2,SC-8}
ALTER TABLE controls_catalog ADD COLUMN IF NOT EXISTS iso_27001       TEXT[];  -- e.g. {A.9.4.1,A.12.4.1}
ALTER TABLE controls_catalog ADD COLUMN IF NOT EXISTS coso_component  VARCHAR(64);  -- COSO ERM 2017 component name
-- Independent of coso_component above, not derived from it: a control's IC-IF
-- 2013 component (Control Environment / Risk Assessment / Control Activities /
-- Information & Communication / Monitoring Activities) is a different fact
-- about the control than its ERM 2017 component — see framework_mappings.py's
-- 2026-08-26 note. Powers the Risk Coverage Cube's IC-IF view; coso_component
-- powers its ERM 2017 view. Same "honest gap, never inferred" policy applies.
ALTER TABLE controls_catalog ADD COLUMN IF NOT EXISTS icif_component VARCHAR(64);  -- COSO IC-IF 2013 component name

-- Concept layer — a controlled vocabulary + typed crosswalk, SKOS-shaped
-- (prefLabel/altLabel/broader/narrower) plus NIST IR 8477 Set Theory
-- Relationship Mapping (STRM) for typed relations between concepts across
-- schemes/frameworks. Deliberately NOT an OWL ontology and NOT backed by a
-- reasoner or triplestore: intersects_with is not transitive (A intersects B
-- and B intersects C implies nothing about A and C), and this codebase's own
-- guardrail (framework_mappings.py's "curated, hand-reviewed only, never
-- auto-inferred") means an inferred relation could never be authoritative
-- here without human review anyway — so a reasoner would only produce
-- conclusions this app isn't allowed to act on unreviewed. See
-- ontology_seed.py for the curated seed content and its sign-off history.
--
-- Seeding is a PROJECTION of the existing hardcoded vocabularies
-- (risk-engine.js's CATEGORY_IMPACT, risks_as_code.py's COSO tables,
-- framework_mappings.py, sox_scoping_tool.py, pac_processes above), never a
-- replacement for them — those literals stay authoritative; nothing existing
-- reads from these tables yet. See db.seed_ontology().
CREATE TABLE IF NOT EXISTS concepts (
    id          BIGSERIAL PRIMARY KEY,
    scheme      VARCHAR(64)  NOT NULL,   -- 'risk_category' | 'enterprise_domain' |
                                         -- 'coso_erm' | 'coso_icif' | 'sox_risk_category' |
                                         -- 'sox_process' | 'pac_process' | 'scf' |
                                         -- 'soc2' | 'nist_800_53' | 'iso_27001'
    notation    VARCHAR(64),             -- stable external code, e.g. 'AC-2', 'CC6.1', 'P13'
    pref_label  VARCHAR(255) NOT NULL,
    alt_labels  TEXT[]       NOT NULL DEFAULT '{}',  -- synonyms; drives entity-linking recall
    definition  TEXT,
    broader_id  BIGINT REFERENCES concepts(id),      -- SKOS hierarchy (tree, not the STRM graph below)
    source      VARCHAR(32)  NOT NULL DEFAULT 'curated',  -- 'curated' | 'scf_import'
    reviewed_by VARCHAR(128),
    reviewed_at TIMESTAMPTZ,
    label_hash  VARCHAR(64),             -- sha256(pref_label|definition|sorted(alt_labels));
                                         -- staleness check for the concept's embedding (Stage 2)
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (scheme, pref_label)
);
CREATE INDEX IF NOT EXISTS idx_concepts_scheme  ON concepts (scheme);
CREATE INDEX IF NOT EXISTS idx_concepts_broader ON concepts (broader_id);

-- Typed crosswalk relations between concepts (often across schemes/frameworks —
-- this is the SOC2<->NIST-shaped relation the app has never had). strm_type is
-- one of NIST IR 8477's five relations. 'no_relationship' is stored
-- deliberately: an asserted negative is evidence ("we checked; these don't
-- relate"), not the absence of a row — the same "honest gap, not papered
-- over" standard framework_mappings.py already holds itself to.
CREATE TABLE IF NOT EXISTS concept_relations (
    id              BIGSERIAL PRIMARY KEY,
    from_concept_id BIGINT      NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    to_concept_id   BIGINT      NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    strm_type       VARCHAR(24) NOT NULL,
    strength        NUMERIC(4,3),
    rationale       TEXT,
    source          VARCHAR(32) NOT NULL DEFAULT 'curated',  -- 'curated' | 'scf_import'
    reviewed_by     VARCHAR(128),
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_concept_relations_strm_type CHECK (
        strm_type IN ('subset_of', 'superset_of', 'equal', 'intersects_with', 'no_relationship')
    ),
    UNIQUE (from_concept_id, to_concept_id, strm_type)
);
CREATE INDEX IF NOT EXISTS idx_concept_relations_from ON concept_relations (from_concept_id);
CREATE INDEX IF NOT EXISTS idx_concept_relations_to   ON concept_relations (to_concept_id);

-- Policy-as-Code business processes: was a hardcoded 5-entry Python set
-- (VALID_PROCESSES); now a real table so sync_github() can register a new
-- process discovered in a synced repo instead of silently skipping it.
CREATE TABLE IF NOT EXISTS pac_processes (
    id             VARCHAR(32)  PRIMARY KEY,
    label          VARCHAR(128) NOT NULL,
    short_label    VARCHAR(16)  NOT NULL,
    control_prefix VARCHAR(16),
    color          VARCHAR(16),
    icon           VARCHAR(8),
    description    TEXT,
    is_builtin     BOOLEAN      NOT NULL DEFAULT FALSE,
    source         VARCHAR(32)  NOT NULL DEFAULT 'manual',  -- builtin | github_discovered | manual
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
-- 'github_discovered' (17 chars) always exceeded the original VARCHAR(16),
-- so every sync_github auto-registration failed the INSERT unconditionally —
-- widen for databases created before this fix.
ALTER TABLE pac_processes ALTER COLUMN source TYPE VARCHAR(32);

-- Model Health drift incidents: turns a drift-detection webhook ping into a
-- tracked record with an owner, status, and closure note — the process trail
-- an AI-governance committee expects, not just an ephemeral alert. Replaces
-- the old timestamp-cooldown app_config key: "don't re-alert" now means
-- "there's already an OPEN incident for this metric," which re-alerts
-- correctly if drift recurs after a prior incident was resolved.
CREATE TABLE IF NOT EXISTS model_health_drift_incidents (
    id              BIGSERIAL    PRIMARY KEY,
    metric_key      VARCHAR(128) NOT NULL,
    metric_kind     VARCHAR(16)  NOT NULL,   -- ratio | fred_series
    psi             NUMERIC,
    n_baseline      INTEGER,
    n_current       INTEGER,
    detail          JSONB,
    status          VARCHAR(16)  NOT NULL DEFAULT 'open',  -- open | acknowledged | resolved
    owner           VARCHAR(128),
    notes           TEXT,
    detected_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    acknowledged_at TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_drift_incidents_open
    ON model_health_drift_incidents (metric_key)
    WHERE status != 'resolved';

-- Policy-as-Code modules: versioned Rego per business process
-- One row per version; latest version is the one with the highest id per process.
CREATE TABLE IF NOT EXISTS pac_policy_modules (
    id              BIGSERIAL    PRIMARY KEY,
    process         VARCHAR(64)  NOT NULL,   -- 'itgc' | 'order_to_cash' | 'procure_to_pay' | 'receive_to_ship' | 'record_to_report'
    module_name     VARCHAR(128) NOT NULL,
    rego_content    TEXT         NOT NULL,
    version         VARCHAR(16)  NOT NULL DEFAULT '1.0',
    last_revised_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pac_modules_process ON pac_policy_modules (process, created_at DESC);

-- Policy-as-Code approvals: multiple approvers per module version
CREATE TABLE IF NOT EXISTS pac_policy_approvals (
    id          BIGSERIAL    PRIMARY KEY,
    module_id   BIGINT       NOT NULL REFERENCES pac_policy_modules(id) ON DELETE CASCADE,
    approver    VARCHAR(128) NOT NULL,
    role        VARCHAR(64),
    approved_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pac_approvals_module ON pac_policy_approvals (module_id);

-- Policy-as-Code external hooks: one active row per hook_type (github | confluence)
-- Persists until updated or deleted by the user.
CREATE TABLE IF NOT EXISTS pac_external_hooks (
    id          BIGSERIAL    PRIMARY KEY,
    hook_type   VARCHAR(16)  NOT NULL UNIQUE,  -- 'github' | 'confluence'
    config_json JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Plain-language policy documents & their HITL conversion review ───────────
-- The upload side of Policy-as-Code: the prose policy the org actually wrote
-- (a Word-exported Markdown SOP, a PDF standard, a pasted paragraph) is
-- persisted here VERBATIM as the immutable source of record, separate from
-- any Rego derived from it. pac_policy_modules.source_format already records
-- that a module *was* LLM-converted (sync_github's Markdown->Rego path), but
-- that path throws the source prose away and writes straight into the live
-- module — there is no artifact to re-review, re-convert, or show an auditor
-- next to the rule. These two tables are that artifact.
--
-- No generated Rego ever reaches pac_policy_modules on its own: every draft
-- lands in pac_policy_conversions at status 'pending_review' and only an
-- explicit human decision (record_pac_conversion_decision) promotes it into a
-- module version. Same "no ungrounded generation reaches the register"
-- guardrail as the controls_library framework-crosswalk columns above.
CREATE TABLE IF NOT EXISTS pac_policy_documents (
    id          BIGSERIAL    PRIMARY KEY,
    process     VARCHAR(64)  NOT NULL,
    title       VARCHAR(256) NOT NULL,
    filename    VARCHAR(256),
    source      VARCHAR(16)  NOT NULL DEFAULT 'upload',   -- 'upload' | 'paste' | 'github'
    doc_text    TEXT         NOT NULL,                    -- extracted plain text, never rewritten
    byte_size   INTEGER      NOT NULL DEFAULT 0,          -- of the ORIGINAL upload, not doc_text
    sha256      CHAR(64),                                 -- of doc_text; powers re-upload detection
    uploaded_by VARCHAR(128),
    status      VARCHAR(24)  NOT NULL DEFAULT 'uploaded',
        -- uploaded | converting | in_review | published | rejected | failed
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pac_policy_docs_process ON pac_policy_documents (process, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pac_policy_docs_status  ON pac_policy_documents (status, created_at DESC);

-- One row per conversion ATTEMPT (re-converting a document adds a row rather
-- than overwriting), so a rejected draft and the reviewer's reason for
-- rejecting it stay on record next to the accepted one.
--   generated_rego — exactly what the model emitted, never edited afterwards
--   draft_rego     — the reviewer's working copy; what actually gets published
-- Keeping both is the point of the HITL step: the diff between them IS the
-- evidence of human oversight, and collapsing them into one column would
-- erase it.
CREATE TABLE IF NOT EXISTS pac_policy_conversions (
    id             BIGSERIAL    PRIMARY KEY,
    document_id    BIGINT       NOT NULL REFERENCES pac_policy_documents(id) ON DELETE CASCADE,
    process        VARCHAR(64)  NOT NULL,
    generated_rego TEXT         NOT NULL,
    draft_rego     TEXT         NOT NULL,
    model          VARCHAR(64),
    syntax_valid   BOOLEAN      NOT NULL DEFAULT FALSE,
    syntax_errors  JSONB        NOT NULL DEFAULT '[]',
    control_ids    JSONB        NOT NULL DEFAULT '[]',
    status         VARCHAR(24)  NOT NULL DEFAULT 'pending_review',
        -- pending_review | changes_requested | approved | rejected
    reviewer       VARCHAR(128),
    reviewer_role  VARCHAR(64),
    review_notes   TEXT,
    reviewed_at    TIMESTAMPTZ,
    published_module_id BIGINT  REFERENCES pac_policy_modules(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pac_conversions_doc    ON pac_policy_conversions (document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pac_conversions_status ON pac_policy_conversions (status, created_at DESC);

-- Regulatory change management (regulatory_change_tool.py / regulatory_change_endpoints.py)
-- — same immutable-source / reviewable-proposal split as pac_policy_documents/
-- pac_policy_conversions above, applied to horizon-scanning instead of manual
-- policy upload: a version is a fetched snapshot of one regulatory source's
-- text (sha256-deduped, same "don't re-store identical content" reasoning as
-- pac_policy_documents.sha256's re-upload detection); a proposal is the
-- reviewable "here's what changed and here's a suggested control edit" draft
-- a human approves before anything touches controls_library.
CREATE TABLE IF NOT EXISTS regulatory_change_versions (
    id            BIGSERIAL    PRIMARY KEY,
    feed_id       VARCHAR(32)  NOT NULL,      -- rss_ingest_service.FEEDS[i]["id"], e.g. "eu_ai_act"
    source_url    TEXT         NOT NULL,
    title         VARCHAR(512),
    fetched_text  TEXT         NOT NULL,
    sha256        CHAR(64)     NOT NULL,
    previous_version_id BIGINT REFERENCES regulatory_change_versions(id),
    fetched_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_regchange_versions_feed ON regulatory_change_versions (feed_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_regchange_versions_url  ON regulatory_change_versions (source_url, fetched_at DESC);

-- proposed_control_ref is a soft (non-FK) reference into controls_library.control_ref
-- — same non-enforced-link pattern controls_library.pac_control_id already
-- uses for its controls_catalog crosswalk, since a regulatory change may
-- propose editing a control that doesn't exist yet (a genuinely new
-- requirement with nothing in the register to attach to).
CREATE TABLE IF NOT EXISTS regulatory_change_proposals (
    id                  BIGSERIAL    PRIMARY KEY,
    version_id          BIGINT       NOT NULL REFERENCES regulatory_change_versions(id) ON DELETE CASCADE,
    diff_summary        TEXT         NOT NULL,
    proposed_control_ref VARCHAR(32),
    proposed_edit       JSONB        NOT NULL DEFAULT '{}',  -- {"description": "...", "name": "..."} draft merge onto the control
    status              VARCHAR(24)  NOT NULL DEFAULT 'pending_review',
        -- pending_review | approved | rejected
    reviewer            VARCHAR(128),
    reviewed_at         TIMESTAMPTZ,
    review_notes        TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_regchange_proposals_version ON regulatory_change_proposals (version_id);
CREATE INDEX IF NOT EXISTS idx_regchange_proposals_status  ON regulatory_change_proposals (status, created_at DESC);

-- Scheduled digest notifications (Feature 5) — a deterministic, zero-LLM-cost
-- "what changed since your last visit" summary, generated lazily on the
-- frontend's existing approval-inbox poll rather than a blind cron, so an
-- inactive account never triggers generation. user_id is a soft reference to
-- auth.users(id) (no FK: db.init_db() runs before auth_db.init_auth_db(), see
-- approval_tasks above) — identity is always populated server-side.
CREATE TABLE IF NOT EXISTS digest_notifications (
    id           BIGSERIAL    PRIMARY KEY,
    user_id      BIGINT       NOT NULL,
    ticker       VARCHAR(16)  NOT NULL,
    from_run_id  INT          REFERENCES risk_loop_runs(id),
    to_run_id    INT          NOT NULL REFERENCES risk_loop_runs(id),
    payload      JSONB        NOT NULL,
    generated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    read_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_digest_user ON digest_notifications (user_id, generated_at DESC);

-- Per-metric baseline reset for Model Health drift (drift_tool.py's
-- compute_ratio_drift/compute_fred_regime_drift/compute_ai_acceptance_drift
-- always split "baseline" vs "current" out of rolling history — there's no
-- separate stored baseline to edit directly). A reset instead tells those
-- functions "ignore everything before this timestamp for this metric" —
-- i.e. treat the post-shift population as the new normal going forward,
-- rather than perpetually comparing against pre-shift data. One row per
-- metric_key; a fresh reset overwrites the prior one.
CREATE TABLE IF NOT EXISTS model_health_baseline_resets (
    metric_key VARCHAR(128) PRIMARY KEY,
    reset_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    reset_by   VARCHAR(128),
    reason     TEXT
);

-- FAIR-style (Factor Analysis of Information Risk) loss quantification runs.
-- Turns an adjudicated verdict, a SOX process, or a risk register entry into
-- a dollar distribution instead of the ordinal P1/P2/P3 or 5x5 RAG score
-- those carry natively — see fair_tool.py for the Monte Carlo engine (Poisson
-- threat-event frequency x PERT loss magnitude, same discipline
-- allocateRiskDollarExposure() in risk-engine.js already established:
-- never invent a magnitude, always cite where it came from).
-- resource_type/resource_ref together identify what was quantified —
-- deliberately not a typed FK, since the three resource kinds
-- (cem_events.id is int, sox process id is a slug, risk_scores.risk_ref is a
-- slug, controls_catalog.control_id is a string) share no single key space.
-- One row per run (never overwritten) so ALE trend over time is answerable
-- without a separate history table, same pattern as risk_loop_runs.
CREATE TABLE IF NOT EXISTS fair_quantifications (
    id                SERIAL       PRIMARY KEY,
    resource_type     VARCHAR(32)  NOT NULL,   -- cem_event | sox_process | risk | control
    resource_ref      VARCHAR(128) NOT NULL,
    company_id        INT          REFERENCES companies(id),
    run_id            INT          REFERENCES risk_loop_runs(id),
    control_id        VARCHAR(64),
    process           VARCHAR(64),
    tef_mean          NUMERIC      NOT NULL,   -- threat event frequency, events/year
    tef_source        VARCHAR(16)  NOT NULL,   -- empirical (adjudication history) | manual
    loss_min          NUMERIC,
    loss_likely       NUMERIC,
    loss_max          NUMERIC,
    magnitude_source  VARCHAR(24)  NOT NULL,   -- sox_exposure | risk_dollar_exposure | cem_severity_default | manual
    simulations       INT          NOT NULL,
    ale               NUMERIC      NOT NULL,   -- annualized loss expectancy ($M), mean of the simulated distribution
    p10               NUMERIC,
    p50               NUMERIC,
    p90               NUMERIC,
    p95               NUMERIC,
    exceedance_curve  JSONB,                   -- [{"probability":, "loss":}, ...] for the loss-exceedance chart
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by        VARCHAR(128)
);
CREATE INDEX IF NOT EXISTS idx_fair_quant_resource ON fair_quantifications (resource_type, resource_ref, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fair_quant_control   ON fair_quantifications (control_id, created_at DESC) WHERE control_id IS NOT NULL;

-- Exception Management (Continuous Control Monitoring triage) — Development
-- environment only (see deploy_env.py / exceptions_endpoints.py). Ported
-- from devriskops-ccm/schema.sql (a standalone Streamlit+FastAPI+Airflow
-- service that was never wired into this app) into this app's own tables,
-- BIGSERIAL ids instead of devriskops-ccm's UUID/pgcrypto to match this
-- schema's existing convention (no new extension dependency).
CREATE TABLE IF NOT EXISTS exception_control_events (
    id                      BIGSERIAL     PRIMARY KEY,
    control_id              VARCHAR(128)  NOT NULL,
    system_source           VARCHAR(64)   NOT NULL,
    process                 VARCHAR(64),
    event_timestamp         TIMESTAMPTZ   NOT NULL,
    point_in_time_features  JSONB         NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_exc_events_control   ON exception_control_events (control_id);
CREATE INDEX IF NOT EXISTS idx_exc_events_timestamp ON exception_control_events (event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_exc_events_source    ON exception_control_events (system_source);

-- What actually happened, captured at scoring time (connector_poller.py's
-- _score_exception_event has the full polled event in hand right there) so
-- a reviewer isn't left triaging a bare anomaly score with no idea what
-- event produced it. system_telemetry_id links back to the exact source
-- row in observability.system_telemetry — no FK constraint (cross-schema,
-- and system_telemetry rows are never deleted, but this stays a soft
-- reference like the rest of this table's system_source/process do).
ALTER TABLE exception_control_events ADD COLUMN IF NOT EXISTS actor               VARCHAR(128);
ALTER TABLE exception_control_events ADD COLUMN IF NOT EXISTS action              VARCHAR(128);
ALTER TABLE exception_control_events ADD COLUMN IF NOT EXISTS event_type          VARCHAR(128);
ALTER TABLE exception_control_events ADD COLUMN IF NOT EXISTS raw_payload         JSONB;
ALTER TABLE exception_control_events ADD COLUMN IF NOT EXISTS system_telemetry_id BIGINT;
-- Curation/delegation (Exception Management: curate, risk-rate, delegate):
-- both snapshotted at scoring time from the connector that produced the
-- event, not joined live — an owner/tier edited later on poll_connectors
-- must not retroactively rewrite how an already-scored exception reads,
-- same "capture what was true when scored" discipline
-- management_action_plans.risk_rating/owner already use. connector_id is a
-- soft reference (no FK), same style as system_telemetry_id above.
ALTER TABLE exception_control_events ADD COLUMN IF NOT EXISTS connector_id        BIGINT;
ALTER TABLE exception_control_events ADD COLUMN IF NOT EXISTS assigned_owner      VARCHAR(128);
CREATE INDEX IF NOT EXISTS idx_exc_events_owner ON exception_control_events (assigned_owner);

CREATE TABLE IF NOT EXISTS exception_model_inferences (
    id                      BIGSERIAL    PRIMARY KEY,
    event_id                BIGINT       NOT NULL REFERENCES exception_control_events(id) ON DELETE CASCADE,
    model_version           VARCHAR(64)  NOT NULL,
    anomaly_score           NUMERIC(5,4) NOT NULL CHECK (anomaly_score BETWEEN 0 AND 1),
    uncertainty_score       NUMERIC(5,4) NOT NULL CHECK (uncertainty_score BETWEEN 0 AND 1),
    requires_human_review   BOOLEAN      NOT NULL DEFAULT FALSE,
    scored_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_exc_inferences_event ON exception_model_inferences (event_id);
CREATE INDEX IF NOT EXISTS idx_exc_inferences_pending
    ON exception_model_inferences (uncertainty_score DESC) WHERE requires_human_review = TRUE;
-- risk_rating: R|A|G, same vocabulary management_action_plans.risk_rating /
-- risk_scores.rag_status already use — an independent signal from
-- severity+connector risk_tier (exception_tool.score_event), NOT derived
-- from anomaly_score/uncertainty_score the way those two are derived from
-- each other. Nullable: rows scored before this column existed (or by
-- je_testing_sweep.py, which never sets it) simply have no risk_rating,
-- sorting last rather than being coerced into a fake tier.
ALTER TABLE exception_model_inferences ADD COLUMN IF NOT EXISTS risk_rating VARCHAR(8);

CREATE TABLE IF NOT EXISTS exception_auditor_triage (
    id                      BIGSERIAL    PRIMARY KEY,
    event_id                BIGINT       NOT NULL UNIQUE REFERENCES exception_control_events(id) ON DELETE CASCADE,
    auditor                 VARCHAR(128) NOT NULL,
    resolution_label        VARCHAR(32)  NOT NULL,
    justification_notes     TEXT,
    reviewed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_exc_justification_required CHECK (
        resolution_label NOT IN ('TRUE_CONTROL_FAILURE', 'APPROVED_CARVE_OUT')
        OR (justification_notes IS NOT NULL AND length(btrim(justification_notes)) > 0)
    )
);
CREATE INDEX IF NOT EXISTS idx_exc_triage_reviewed ON exception_auditor_triage (reviewed_at DESC);
"""

# Idempotent column migrations. CREATE TABLE IF NOT EXISTS never adds columns to a
# table that already exists, so databases created from an older DDL drift silently.
# These ADD COLUMN IF NOT EXISTS statements reconcile that drift on every startup.
_MIGRATIONS = """
ALTER TABLE risk_loop_runs ADD COLUMN IF NOT EXISTS period_end_col VARCHAR(16);
ALTER TABLE risk_loop_runs ADD COLUMN IF NOT EXISTS period_begin   VARCHAR(16);
ALTER TABLE risk_loop_runs ADD COLUMN IF NOT EXISTS appetite_level VARCHAR(8);
ALTER TABLE risk_loop_runs ADD COLUMN IF NOT EXISTS persona        VARCHAR(64);
ALTER TABLE risk_loop_runs ADD COLUMN IF NOT EXISTS signal_set     TEXT[];
ALTER TABLE sox_systems     ADD COLUMN IF NOT EXISTS version        VARCHAR(32);
ALTER TABLE sox_systems     ADD COLUMN IF NOT EXISTS notes          TEXT;
ALTER TABLE sox_financial_segments ADD COLUMN IF NOT EXISTS rev_growth_yoy_pct NUMERIC(7,3);
ALTER TABLE sox_financial_segments ADD COLUMN IF NOT EXISTS net_income         NUMERIC;
ALTER TABLE sox_financial_segments ADD COLUMN IF NOT EXISTS gross_margin_pct   NUMERIC(7,3);
ALTER TABLE sox_financial_segments ADD COLUMN IF NOT EXISTS op_margin_pct      NUMERIC(7,3);
ALTER TABLE sox_financial_segments ADD COLUMN IF NOT EXISTS net_margin_pct     NUMERIC(7,3);
ALTER TABLE risk_scores ADD COLUMN IF NOT EXISTS source_framework  VARCHAR(128);
ALTER TABLE risk_scores ADD COLUMN IF NOT EXISTS narrative          TEXT;
ALTER TABLE risk_scores ADD COLUMN IF NOT EXISTS assigned_domain   VARCHAR(128);
-- Operating-unit attribution for a risk — NULL for every consolidated risk
-- (the vast majority); set only by segment_risk_tool.py's per-segment risk
-- assessment (Risk Coverage Cube Phase 3), so a risk can be traced to the
-- specific geography/business segment it was actually derived from instead
-- of always reading as "Consolidated".
ALTER TABLE risk_scores ADD COLUMN IF NOT EXISTS segment_type      VARCHAR(16);
ALTER TABLE risk_scores ADD COLUMN IF NOT EXISTS segment_name      VARCHAR(128);

-- risk_scores was write-once (save_risk_scores only ever INSERTed, never
-- updated) — Stage 2's signal-driven score adjustments and Gate 1's
-- human-adjusted-and-approved scores never made it back into this table, so
-- every downstream reader keyed on it (get_posture_trend's RAG counts chief
-- among them) silently showed the run's initial pre-adjustment snapshot
-- forever. Fixing that requires save_risk_scores to become a real upsert,
-- which requires a uniqueness guarantee on (run_id, risk_ref) first — dedupe
-- any pre-existing duplicates (there shouldn't be any in practice, since
-- this table was only ever written once per run, but a clean guard costs
-- nothing) before adding the index ON CONFLICT will target.
DELETE FROM risk_scores rs USING risk_scores dup
    WHERE rs.run_id = dup.run_id AND rs.risk_ref = dup.risk_ref
      AND rs.risk_ref IS NOT NULL AND rs.id > dup.id;
CREATE UNIQUE INDEX IF NOT EXISTS idx_risk_scores_run_ref_unique
    ON risk_scores (run_id, risk_ref) WHERE risk_ref IS NOT NULL;
ALTER TABLE risk_register_reviews ADD COLUMN IF NOT EXISTS rac_yaml TEXT;
ALTER TABLE hitl_sessions ADD COLUMN IF NOT EXISTS gate3_status VARCHAR(16);
ALTER TABLE hitl_sessions ADD COLUMN IF NOT EXISTS gate4_status VARCHAR(16);
ALTER TABLE sox_process_details ADD COLUMN IF NOT EXISTS estimated_exposure NUMERIC;
ALTER TABLE objective_approvals ADD COLUMN IF NOT EXISTS adjusted_objective_text TEXT;
ALTER TABLE objective_approvals ADD COLUMN IF NOT EXISTS adjusted_controls TEXT[];
ALTER TABLE audit_objectives ADD COLUMN IF NOT EXISTS linked_risks TEXT[];
-- AI-suggestion acceptance tracking: when a Gate 1/2 "Suggest with AI" drafted
-- the disposition, ai_suggested holds the normalized field values it proposed
-- (same key names as `adjustments`) and ai_accepted records whether the
-- preparer's final submission matched them exactly or was overridden. Both
-- are NULL when no AI suggestion was involved in that item.
ALTER TABLE approval_tasks ADD COLUMN IF NOT EXISTS ai_suggested JSONB;
ALTER TABLE approval_tasks ADD COLUMN IF NOT EXISTS ai_accepted BOOLEAN;

-- Token Usage screen: attribute LLM calls to the authenticated user who
-- triggered them (soft reference, see the comment on token_usage_calls above).
ALTER TABLE token_usage_calls ADD COLUMN IF NOT EXISTS user_id  BIGINT;
ALTER TABLE token_usage_calls ADD COLUMN IF NOT EXISTS username TEXT;

-- forecasts.UNIQUE(run_id, metric, model, horizon_quarter) was added to the
-- CREATE TABLE statement after some databases already had the table created
-- without it — CREATE TABLE IF NOT EXISTS never retrofits constraints onto an
-- existing table, so save_forecasts()'s targeted ON CONFLICT silently failed
-- with "no unique or exclusion constraint matching the ON CONFLICT specification"
-- on any such database. Idempotent: no-ops once the constraint exists.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'forecasts_run_id_metric_model_horizon_quarter_key'
          AND conrelid = 'forecasts'::regclass
    ) THEN
        ALTER TABLE forecasts ADD CONSTRAINT forecasts_run_id_metric_model_horizon_quarter_key
            UNIQUE (run_id, metric, model, horizon_quarter);
    END IF;
END $$;

-- Same retrofit gap on rss_articles: the CREATE TABLE statement moved from
-- UNIQUE(article_url) to UNIQUE(title, feed_name) (articles without a URL,
-- or re-crawled under a slightly different URL, need title+feed as the real
-- dedup key), but existing databases kept the old constraint forever. That
-- made save_rss_signals()/save_rss_articles_full()'s ON CONFLICT (title, feed_name)
-- fail with "no unique or exclusion constraint matching the ON CONFLICT
-- specification" on any such database. Drop the stale constraint (nothing
-- targets it) and add the one the code actually references.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'rss_articles_article_url_key'
          AND conrelid = 'rss_articles'::regclass
    ) THEN
        ALTER TABLE rss_articles DROP CONSTRAINT rss_articles_article_url_key;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'rss_articles_title_feed_name_key'
          AND conrelid = 'rss_articles'::regclass
    ) THEN
        ALTER TABLE rss_articles ADD CONSTRAINT rss_articles_title_feed_name_key
            UNIQUE (title, feed_name);
    END IF;
END $$;

-- Tracks whether a saved PaC module's rego_content was authored/synced as
-- real Rego, or produced by the Markdown->Rego LLM conversion step in
-- pac_endpoints.sync_github (external sources can supply prose policy docs,
-- not just .rego files).
ALTER TABLE pac_policy_modules ADD COLUMN IF NOT EXISTS source_format VARCHAR(16) NOT NULL DEFAULT 'rego';

-- edgar_proxy_filings had no uniqueness on (company_id, accession_number), so
-- every live /edgar/proxy re-pull re-inserted the same 1-2 real filings as
-- brand-new rows. get_edgar_proxy's "ORDER BY filing_date DESC LIMIT 5" then
-- surfaced N duplicate rows of the same 1-2 filings instead of up to 5
-- distinct ones — the Governance Intelligence screen's filing picker showed
-- the same date repeated. Dedup existing rows (keep the newest id per
-- filing) before adding the constraint so this heals current data, not just
-- future inserts.
DELETE FROM edgar_proxy_filings a USING edgar_proxy_filings b
WHERE a.id < b.id
  AND a.company_id = b.company_id
  AND a.accession_number = b.accession_number
  AND a.accession_number IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'edgar_proxy_filings_company_accession_key'
          AND conrelid = 'edgar_proxy_filings'::regclass
    ) THEN
        ALTER TABLE edgar_proxy_filings ADD CONSTRAINT edgar_proxy_filings_company_accession_key
            UNIQUE (company_id, accession_number);
    END IF;
END $$;

-- Mandatory human review for the two fully-automated narrative endpoints
-- (persona_brief, audit_report) — MODEL_CARD.md known limitation #3. Every
-- generation is flagged sampled_for_review at save time (see ai_endpoints.py;
-- column name kept from when this was a 20% spot-check sample, now always
-- True); reviewers work the queue via GET/POST /ai/review-queue. Applies only
-- to the two ungated kinds — every other AI endpoint already has a human gate
-- before its output takes effect.
ALTER TABLE ai_analyses ADD COLUMN IF NOT EXISTS sampled_for_review BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE ai_analyses ADD COLUMN IF NOT EXISTS review_status      VARCHAR(16);
ALTER TABLE ai_analyses ADD COLUMN IF NOT EXISTS reviewed_by        BIGINT;
ALTER TABLE ai_analyses ADD COLUMN IF NOT EXISTS reviewed_by_name   VARCHAR(128);
ALTER TABLE ai_analyses ADD COLUMN IF NOT EXISTS reviewed_at        TIMESTAMPTZ;
ALTER TABLE ai_analyses ADD COLUMN IF NOT EXISTS review_note        TEXT;
CREATE INDEX IF NOT EXISTS idx_ai_analyses_review_queue
    ON ai_analyses (created_at DESC) WHERE sampled_for_review AND review_status IS DISTINCT FROM 'reviewed';

-- Cost-reduction cache: persona_brief/audit_report recompute their full
-- prompt from (persona/role, risk register, loop stats) every single call,
-- including on a plain "reopen the same modal" with nothing changed since
-- the last generation. input_hash lets the endpoint skip the Claude call
-- entirely on a hit — see get_cached_ai_analysis()/ai_endpoints.py.
ALTER TABLE ai_analyses ADD COLUMN IF NOT EXISTS input_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_ai_analyses_cache
    ON ai_analyses (kind, run_id, subject_ref, input_hash) WHERE input_hash IS NOT NULL;

-- Structured correction logging for Model Health drift incidents — distinct
-- from `status`/`notes`. Resolving an incident says "we're done with this";
-- correction_action says *why* — the audit trail a model-governance
-- reviewer actually asks for (MODEL_CARD.md "Recommended Next Steps").
ALTER TABLE model_health_drift_incidents ADD COLUMN IF NOT EXISTS correction_action VARCHAR(32);
ALTER TABLE model_health_drift_incidents ADD COLUMN IF NOT EXISTS corrected_by      VARCHAR(128);
ALTER TABLE model_health_drift_incidents ADD COLUMN IF NOT EXISTS corrected_at      TIMESTAMPTZ;

-- Closes the drift -> re-optimization loop: reoptimization_tool.run_reoptimization_sweep
-- re-runs the forecast/backtest layer per actively-tracked ticker, either
-- automatically (model_health_drift_watch, on a new ratio or fred_series
-- drift incident) or on demand (POST /model-health/run-review). Every
-- re-optimization run is a NEW risk_loop_runs row (matches the existing
-- create_risk_loop_run convention of never overwriting prior runs, so
-- MAPE/RMSE/R2 history stays intact) — trigger_reason/trigger_incident_id
-- distinguish it from an organic user-initiated pipeline run, and
-- reoptimize_triggered_at/reoptimize_summary on the incident itself let the
-- Model Vitals UI show "auto re-evaluated" without cross-referencing runs.
ALTER TABLE risk_loop_runs ADD COLUMN IF NOT EXISTS trigger_reason VARCHAR(32) NOT NULL DEFAULT 'user_run';
ALTER TABLE risk_loop_runs ADD COLUMN IF NOT EXISTS trigger_incident_id INT REFERENCES model_health_drift_incidents(id);
ALTER TABLE model_health_drift_incidents ADD COLUMN IF NOT EXISTS reoptimize_triggered_at TIMESTAMPTZ;
ALTER TABLE model_health_drift_incidents ADD COLUMN IF NOT EXISTS reoptimize_summary      JSONB;

-- cem_events.exposure / cem_event_templates.exposure is VARCHAR(64) and
-- always was — it holds "$12-18M" and "Regulatory" and "Material
-- misstatement" side by side, so it can never be summed or sorted, only
-- displayed. That column is kept as-is (the qualitative label a preparer
-- wrote, or the template default) and a real NUMERIC sits next to it,
-- populated by fair_tool.py's Monte Carlo engine (see fair_quantifications
-- above) when a CEM event has been run through Risk Quantification.
-- exposure_source distinguishes a FAIR-computed figure from a hand-typed
-- override, same provenance convention sox_scoping_tool.py's
-- estimated_exposure/exposure_source already established. NULL until
-- someone actually quantifies that event/template — never a guess.
ALTER TABLE cem_events ADD COLUMN IF NOT EXISTS exposure_amount_m NUMERIC;
ALTER TABLE cem_events ADD COLUMN IF NOT EXISTS exposure_source   VARCHAR(16);
ALTER TABLE cem_event_templates ADD COLUMN IF NOT EXISTS exposure_amount_m NUMERIC;
ALTER TABLE cem_event_templates ADD COLUMN IF NOT EXISTS exposure_source   VARCHAR(16);
"""

# observability.system_telemetry had no uniqueness on (server_name, event_id),
# so a poll-based connector re-fetching an overlapping time window (or a
# retried poll cycle) would re-insert the same external event as a brand-new
# row every time — never adjudicated twice thanks to processed_at, but
# silently duplicated in the raw telemetry and in any "24h event count" view.
# Same fix as edgar_proxy_filings: dedup existing rows first, then add the
# constraint so this heals current data, not just future inserts.
_OBSERVABILITY_MIGRATIONS = """
DELETE FROM observability.system_telemetry a USING observability.system_telemetry b
WHERE a.id < b.id
  AND a.server_name = b.server_name
  AND a.event_id = b.event_id
  AND a.event_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'system_telemetry_server_event_key'
          AND conrelid = 'observability.system_telemetry'::regclass
    ) THEN
        ALTER TABLE observability.system_telemetry ADD CONSTRAINT system_telemetry_server_event_key
            UNIQUE (server_name, event_id);
    END IF;
END $$;
"""

# pgvector DDL — kept separate so a missing extension never breaks the core schema.
# ── Observability schema DDL ──────────────────────────────────────────────────
# Mirrors telemetry_schema.sql. Applied in init_db() so the schema is
# self-healing on every container start — no manual psql run required.
_OBSERVABILITY_DDL = """
CREATE SCHEMA IF NOT EXISTS observability;

-- One row per negative-control corpus run (pac_negative_tests.run_corpus),
-- whether triggered manually, by a module-approval gate, or the periodic
-- sweep (P1a) — this is audit evidence ("prove the control was tested on
-- date X"), not a log line, so it's a table with its own retention, not
-- something that scrolls out of application logs.
-- Lives here (not in _DDL) because it targets the observability schema
-- created on the line above — _DDL runs first and fails outright on a fresh
-- database if it references a schema that doesn't exist yet.
CREATE TABLE IF NOT EXISTS observability.pac_test_runs (
    id                BIGSERIAL    PRIMARY KEY,
    process           VARCHAR(64)  NOT NULL,
    module_id         BIGINT       REFERENCES pac_policy_modules(id),  -- NULL when testing the built-in default
    triggered_by      VARCHAR(32)  NOT NULL,  -- 'manual' | 'approval_gate' | 'scheduled_sweep'
    triggered_by_user  VARCHAR(128),
    contract_ok       BOOLEAN,
    contract_findings  JSONB,
    total             INTEGER      NOT NULL,
    passed            INTEGER      NOT NULL,
    failed            INTEGER      NOT NULL,
    results           JSONB        NOT NULL,   -- full per-fixture results, for drill-down
    run_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pac_test_runs_process ON observability.pac_test_runs (process, run_at DESC);

CREATE TABLE IF NOT EXISTS observability.mcp_sessions (
    session_id      UUID         PRIMARY KEY,
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    server_name     VARCHAR(128),
    process_id      INTEGER,
    proxy_version   VARCHAR(16)  NOT NULL DEFAULT '1.0.0'
);

CREATE TABLE IF NOT EXISTS observability.mcp_telemetry (
    id                BIGSERIAL    PRIMARY KEY,
    ts                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    session_id        UUID         NOT NULL
                          REFERENCES observability.mcp_sessions (session_id)
                          ON DELETE CASCADE,
    message_id        TEXT,
    direction         VARCHAR(8)   NOT NULL
                          CHECK (direction IN ('request', 'response')),
    method            VARCHAR(128),
    target_tool       VARCHAR(128),
    tool_args_hash    CHAR(64),
    execution_time_ms INTEGER,
    status            VARCHAR(16)
                          CHECK (status IN ('ok', 'error', 'timeout', 'unknown')),
    error_message     TEXT,
    payload_hash      CHAR(64)     NOT NULL,
    server_name       VARCHAR(128),
    risk_flags        TEXT[]
);

ALTER TABLE observability.mcp_telemetry
    ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS observability.adjudicated_tool_calls (
    id                    BIGSERIAL    PRIMARY KEY,
    adjudicated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    telemetry_id          BIGINT
                              REFERENCES observability.mcp_telemetry (id)
                              ON DELETE CASCADE,
    session_id            UUID         NOT NULL,
    target_tool           VARCHAR(128),
    server_name           VARCHAR(128),
    risk_flags            TEXT[],
    execution_time_ms     INTEGER,
    uro_id                VARCHAR(64)  NOT NULL,
    risk_score            NUMERIC(5,4),
    risk_tier             VARCHAR(16),
    final_verdict         VARCHAR(32),
    ensemble_confidence   NUMERIC(4,3),
    requires_human_review BOOLEAN      NOT NULL DEFAULT FALSE,
    conflict_flags        TEXT[],
    policy_violations     TEXT[],
    adjudicator_reasoning TEXT,
    source_system         VARCHAR(32)  NOT NULL DEFAULT 'MCP_PROXY',
    council_votes         JSONB        NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_tel_session
    ON observability.mcp_telemetry (session_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tel_tool
    ON observability.mcp_telemetry (target_tool, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tel_ts
    ON observability.mcp_telemetry (ts DESC);
CREATE INDEX IF NOT EXISTS idx_tel_unprocessed
    ON observability.mcp_telemetry (ts ASC)
    WHERE risk_flags IS NOT NULL AND processed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_adj_session
    ON observability.adjudicated_tool_calls (session_id, adjudicated_at DESC);
CREATE INDEX IF NOT EXISTS idx_adj_source
    ON observability.adjudicated_tool_calls (source_system, adjudicated_at DESC);

-- ai_final_verdict is a snapshot of the AI system's own verdict, stamped once
-- at insert and never touched again — human_review_adjudication overwrites
-- final_verdict (the "current/effective" verdict) but previously had no way
-- to preserve what the AI originally said, which is what per-agent
-- calibration (agent said ESCALATE -> did a human confirm it?) requires.
ALTER TABLE observability.adjudicated_tool_calls ADD COLUMN IF NOT EXISTS ai_final_verdict  VARCHAR(32);
ALTER TABLE observability.adjudicated_tool_calls ADD COLUMN IF NOT EXISTS human_verdict      VARCHAR(32);
ALTER TABLE observability.adjudicated_tool_calls ADD COLUMN IF NOT EXISTS human_reviewed_at  TIMESTAMPTZ;

-- run_id links an adjudication to the pipeline run it occurred during, when
-- known. In practice this is almost always NULL today: the dominant write
-- path (mcp_http_telemetry._HTTP_SESSION_ID) is one UUID shared by every
-- HTTP-originated MCP/REST tool call the process ever makes, across every
-- ticker and run, so there is no run context available at INSERT time. The
-- column exists so (a) any future write path that DOES have real run
-- context can populate it directly, and (b) the Evidence Pack query can
-- prefer a real run_id match over its best-effort time-window fallback
-- (see mcp_governance.fetch_adjudications_for_run).
ALTER TABLE observability.adjudicated_tool_calls ADD COLUMN IF NOT EXISTS run_id INT REFERENCES risk_loop_runs(id);
CREATE INDEX IF NOT EXISTS idx_adj_run ON observability.adjudicated_tool_calls (run_id) WHERE run_id IS NOT NULL;

-- case_id/process_step enable a REAL directly-follows graph — "activity A
-- immediately preceded activity B within the same tracked transaction" —
-- as opposed to the categorical Domain/Tier/Verdict/Rule breakdown every
-- adjudication already supports regardless of whether it's part of any
-- multi-step transaction. Both are NULL for the overwhelming majority of
-- rows (an ad-hoc MCP tool call has no "case" concept at all); populated
-- today only by generate_o2c_p2p_synthetic_log.py's linked O2C/P2P
-- lifecycles, via _write_adjudication reading them out of the event's
-- raw_payload. No FK — case_id is a soft business key (e.g. a PO number),
-- same soft-reference convention as risk_ref/control_ref elsewhere.
ALTER TABLE observability.adjudicated_tool_calls ADD COLUMN IF NOT EXISTS case_id      VARCHAR(64);
ALTER TABLE observability.adjudicated_tool_calls ADD COLUMN IF NOT EXISTS process_step VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_adj_case ON observability.adjudicated_tool_calls (case_id, adjudicated_at) WHERE case_id IS NOT NULL;

DROP VIEW IF EXISTS observability.tool_latency_summary;
CREATE OR REPLACE VIEW observability.tool_latency_summary AS
SELECT
    server_name,
    target_tool,
    COUNT(*)                                                            AS call_count,
    ROUND(AVG(execution_time_ms))                                       AS avg_ms,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY execution_time_ms)    AS p50_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY execution_time_ms)    AS p95_ms,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY execution_time_ms)    AS p99_ms,
    COUNT(*) FILTER (WHERE status = 'error')                           AS error_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'error') / NULLIF(COUNT(*), 0), 2) AS error_pct,
    MIN(ts)                                                             AS first_call_at,
    MAX(ts)                                                             AS last_call_at
FROM observability.mcp_telemetry
WHERE direction = 'response'
  AND target_tool IS NOT NULL
GROUP BY server_name, target_tool;

-- Pre-execution governance holds (written by the telemetry proxy, resolved by operators)
CREATE TABLE IF NOT EXISTS observability.tool_call_holds (
    id              BIGSERIAL    PRIMARY KEY,
    session_id      UUID         NOT NULL,
    message_id      TEXT,
    target_tool     VARCHAR(128) NOT NULL,
    tool_args_hash  CHAR(64),
    status          VARCHAR(16)  NOT NULL DEFAULT 'PENDING'
                                 CHECK (status IN ('PENDING','APPROVED','DENIED','EXPIRED')),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    resolved_by     VARCHAR(128)
);
CREATE INDEX IF NOT EXISTS idx_holds_pending
    ON observability.tool_call_holds (status, created_at DESC)
    WHERE status = 'PENDING';

-- Suppression allowlist — known-good (tool, args-hash) pairs that skip the pipeline
CREATE TABLE IF NOT EXISTS observability.tool_call_suppressions (
    id              BIGSERIAL    PRIMARY KEY,
    server_name     VARCHAR(128),
    target_tool     VARCHAR(128),
    tool_args_hash  CHAR(64),
    reason          TEXT,
    active          BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(128)
);
CREATE INDEX IF NOT EXISTS idx_suppress_active
    ON observability.tool_call_suppressions (target_tool, server_name)
    WHERE active;

CREATE TABLE IF NOT EXISTS observability.monitored_systems (
    id               BIGSERIAL    PRIMARY KEY,
    display_name     VARCHAR(128) NOT NULL,
    server_name      VARCHAR(128) NOT NULL,
    server_type      VARCHAR(64)  NOT NULL DEFAULT 'custom',
    description      TEXT,
    active           BOOLEAN      NOT NULL DEFAULT TRUE,
    governance_tiers TEXT[]       NOT NULL DEFAULT '{CRITICAL,HIGH,MEDIUM}',
    blocking_tools   TEXT[],
    alert_webhook    TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by       VARCHAR(128)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_monitored_systems_server_name
    ON observability.monitored_systems (server_name)
    WHERE active;

-- Per-system ingest API key (added after initial release — idempotent)
ALTER TABLE observability.monitored_systems
    ADD COLUMN IF NOT EXISTS ingest_api_key UUID NOT NULL DEFAULT gen_random_uuid();

-- Encrypted replacement for the plaintext ingest_api_key column above
-- (security fix: a DB backup/leak previously handed over every registered
-- system's live bearer credential directly, with no protection layer the
-- way local account passwords get via bcrypt). New systems store a
-- high-entropy secret here, Fernet-encrypted with CONNECTOR_ENCRYPTION_KEY
-- (mcp_governance._create_system) instead of relying on the ingest_api_key
-- column's gen_random_uuid() default. ingest_api_key is kept, nullable-in-
-- practice-for-new-rows, purely so systems created before this migration
-- keep working without forced key rotation — mcp_governance._get_system_by_api_key
-- checks ingest_api_key_enc first and falls back to the legacy column only
-- for rows that never got one. See mcp_governance._rotate_system_api_key
-- for migrating an existing system onto the encrypted column on demand.
ALTER TABLE observability.monitored_systems
    ADD COLUMN IF NOT EXISTS ingest_api_key_enc TEXT;

-- New systems no longer populate the legacy plaintext column at all (see
-- _create_system) — DROP NOT NULL so that explicit NULL insert succeeds,
-- and DROP DEFAULT so gen_random_uuid() can never mint a second, unencrypted,
-- equally-valid credential alongside ingest_api_key_enc for a new row.
-- Existing rows' values are left untouched; they still authenticate via the
-- legacy fallback in mcp_governance._get_system_by_api_key until rotated.
ALTER TABLE observability.monitored_systems
    ALTER COLUMN ingest_api_key DROP NOT NULL;
ALTER TABLE observability.monitored_systems
    ALTER COLUMN ingest_api_key DROP DEFAULT;

-- AI system inventory / risk-tiering (idempotent) — the classification fields
-- a NIST AI RMF "Map" inventory or EU AI Act system register expects: how
-- risky is this system, what does it touch, who owns it. Mirrored onto
-- poll_connectors below so the unified inventory view covers both push- and
-- pull-model systems.
ALTER TABLE observability.monitored_systems ADD COLUMN IF NOT EXISTS risk_tier        VARCHAR(16);
ALTER TABLE observability.monitored_systems ADD COLUMN IF NOT EXISTS data_sensitivity VARCHAR(32);
ALTER TABLE observability.monitored_systems ADD COLUMN IF NOT EXISTS system_owner     VARCHAR(128);

-- Poll-based connectors: the inverse of monitored_systems. monitored_systems
-- is push-model (the external system authenticates to us with an
-- ingest_api_key WE issue); poll_connectors is pull-model (WE authenticate
-- to THEM, so we hold THEIR credentials — encrypted, see
-- encrypt_credentials/decrypt_credentials below). Configured entirely from
-- the app UI (Dendrai UBO Configuration screen), not env vars — that's the
-- whole point of this table existing separately from the env-var-configured
-- ORACLE_FUSION_* style connectors.
CREATE TABLE IF NOT EXISTS observability.poll_connectors (
    id                BIGSERIAL    PRIMARY KEY,
    connector_type    VARCHAR(32)  NOT NULL,  -- oracle_fusion | sap_hana | sailpoint | dynamics365 | netsuite
    display_name      VARCHAR(128) NOT NULL,
    base_url          TEXT,
    auth_type         VARCHAR(32)  NOT NULL,
    credentials_enc   BYTEA        NOT NULL,  -- Fernet-encrypted JSON blob
    extra_config      JSONB,
    poll_interval_s   INTEGER      NOT NULL DEFAULT 1800,
    active            BOOLEAN      NOT NULL DEFAULT TRUE,
    last_poll_at      TIMESTAMPTZ,
    last_poll_status  VARCHAR(16),            -- ok | error | never_run
    last_poll_error   TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by        VARCHAR(128)
);
CREATE INDEX IF NOT EXISTS idx_poll_connectors_active ON observability.poll_connectors (active);
ALTER TABLE observability.poll_connectors ADD COLUMN IF NOT EXISTS risk_tier        VARCHAR(16);
ALTER TABLE observability.poll_connectors ADD COLUMN IF NOT EXISTS data_sensitivity VARCHAR(32);
ALTER TABLE observability.poll_connectors ADD COLUMN IF NOT EXISTS system_owner     VARCHAR(128);
-- Infrastructure Monitoring: connector credential rotation hygiene
-- (connector_hygiene.py). Distinct from updated_at, which bumps on ANY
-- field edit (display_name, poll_interval_s, ...) — this column changes
-- ONLY when the credentials themselves are rotated, so it's an honest
-- "credential age" signal rather than "row last touched for any reason".
ALTER TABLE observability.poll_connectors ADD COLUMN IF NOT EXISTS credentials_rotated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
-- Infrastructure Vulnerability & Currency Posture: when this credential/API
-- token actually expires, if known — distinct from credentials_rotated_at
-- (when it was LAST rotated) the same way a driver's license issue date and
-- expiry date are distinct facts. Nullable and never defaulted: an unset
-- expiry means "no known expiry was configured," never "expired" — a
-- connector created before this column existed, or one whose credential
-- genuinely doesn't expire (a long-lived DB role), must not silently read
-- as overdue. See expiry_sweep.py for how this is checked.
ALTER TABLE observability.poll_connectors ADD COLUMN IF NOT EXISTS credentials_expires_at TIMESTAMPTZ;

-- Identity/role graph: real user<->role edges pulled from connectors that
-- expose them (currently Oracle Fusion's SCIM Users/Groups API via
-- identity_graph_sync.py). Feeds The Graph Architect's blast-radius/SPoF
-- checks (UBO/agents/graph_architect.py) with real role_count/entitlements
-- instead of the zeroed defaults every other event path leaves them at —
-- see mcp_governance.py's _process_one() enrichment step.
CREATE TABLE IF NOT EXISTS observability.identity_role_edges (
    id           BIGSERIAL    PRIMARY KEY,
    connector_id BIGINT       NOT NULL REFERENCES observability.poll_connectors(id) ON DELETE CASCADE,
    username     VARCHAR(255) NOT NULL,
    role_name    VARCHAR(255) NOT NULL,
    role_id      VARCHAR(255),
    fetched_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (connector_id, username, role_name)
);
CREATE INDEX IF NOT EXISTS idx_identity_role_edges_username ON observability.identity_role_edges (username);

-- Segregation-of-Duties violations pulled from the same sync (Oracle Risk
-- Management Cloud's segregationOfDutiesViolations). Persisted now; raising
-- these as adjudicated events is a separate follow-up (see identity_graph_sync.py
-- module docstring) — this table only backs list_open_sod_violations_for_user()
-- today.
CREATE TABLE IF NOT EXISTS observability.sod_violations (
    id             BIGSERIAL    PRIMARY KEY,
    connector_id   BIGINT       NOT NULL REFERENCES observability.poll_connectors(id) ON DELETE CASCADE,
    violation_id   VARCHAR(255) NOT NULL,
    username       VARCHAR(255) NOT NULL,
    policy_name    VARCHAR(255),
    conflict_roles JSONB,
    risk_level     VARCHAR(16),
    status         VARCHAR(32),
    detected_date  TIMESTAMPTZ,
    fetched_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (connector_id, violation_id)
);
CREATE INDEX IF NOT EXISTS idx_sod_violations_username ON observability.sod_violations (username);

-- Generic system telemetry: any registered system pushes events here via REST.
-- Enterprise systems (Saviynt, SAP, Oracle Fusion, ServiceNow, etc.) authenticate
-- with their per-system ingest_api_key and POST structured events to /observability/telemetry/ingest.
CREATE TABLE IF NOT EXISTS observability.system_telemetry (
    id              BIGSERIAL    PRIMARY KEY,
    server_name     VARCHAR(128) NOT NULL,
    system_type     VARCHAR(64)  NOT NULL DEFAULT 'custom',
    event_type      VARCHAR(128) NOT NULL,
    event_id        VARCHAR(256),
    actor           VARCHAR(256),
    action          VARCHAR(256),
    resource        VARCHAR(512),
    severity        VARCHAR(32)  NOT NULL DEFAULT 'INFO',
    risk_flags      TEXT[]       NOT NULL DEFAULT '{}',
    raw_payload     JSONB,
    source_ip       VARCHAR(64),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_system_telemetry_server
    ON observability.system_telemetry (server_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_telemetry_created
    ON observability.system_telemetry (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_telemetry_flags
    ON observability.system_telemetry USING GIN (risk_flags);

-- Adjudication pipeline support for generic system_telemetry rows (added after
-- initial release — idempotent). processed_at mirrors mcp_telemetry's polling
-- marker; system_telemetry_id lets adjudicated_tool_calls reference this table
-- too, since its original telemetry_id FK points only at mcp_telemetry (which
-- pre-dates system_telemetry and can't be repointed to it).
ALTER TABLE observability.system_telemetry
    ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_system_telemetry_unprocessed
    ON observability.system_telemetry (created_at ASC)
    WHERE processed_at IS NULL AND array_length(risk_flags, 1) > 0;

ALTER TABLE observability.adjudicated_tool_calls
    ADD COLUMN IF NOT EXISTS system_telemetry_id BIGINT
        REFERENCES observability.system_telemetry (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_adj_system_telemetry
    ON observability.adjudicated_tool_calls (system_telemetry_id)
    WHERE system_telemetry_id IS NOT NULL;
-- system_telemetry-sourced adjudications have no MCP session
ALTER TABLE observability.adjudicated_tool_calls
    ALTER COLUMN session_id DROP NOT NULL;

CREATE TABLE IF NOT EXISTS observability.pac_repositories (
    id           BIGSERIAL    PRIMARY KEY,
    display_name VARCHAR(128) NOT NULL,
    provider     VARCHAR(32)  NOT NULL DEFAULT 'github',
    repo_url     TEXT         NOT NULL,
    branch       VARCHAR(128) NOT NULL DEFAULT 'main',
    rego_path    VARCHAR(256) NOT NULL DEFAULT 'policies/',
    process      VARCHAR(64)  NOT NULL DEFAULT 'all',
    description  TEXT,
    active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by   VARCHAR(128)
);
-- token_enc holds a Fernet-encrypted {"token": "..."} blob (same
-- CONNECTOR_ENCRYPTION_KEY scheme as observability.poll_connectors) so a
-- registered repo can actually be synced, not just listed as metadata.
ALTER TABLE observability.pac_repositories ADD COLUMN IF NOT EXISTS token_enc        BYTEA;
ALTER TABLE observability.pac_repositories ADD COLUMN IF NOT EXISTS last_synced_at   TIMESTAMPTZ;
ALTER TABLE observability.pac_repositories ADD COLUMN IF NOT EXISTS last_sync_status VARCHAR(16);
ALTER TABLE observability.pac_repositories ADD COLUMN IF NOT EXISTS last_sync_error  TEXT;

-- Private-company support (no SEC ticker/CIK) and manual financial-statement
-- ingestion: companies.is_private flags entities created via
-- upsert_private_company (synthetic PVT-<SLUG> ticker, cik NULL) rather than
-- resolved from EDGAR. xbrl_data_points.source distinguishes rows written by
-- the live /edgar/financials pull ('sec_edgar', insert-only, matches SEC's
-- append-only filing history) from rows a user uploaded/entered by hand
-- ('manual_upload', upsertable — correcting a typo shouldn't accumulate
-- duplicate rows). granularity ('annual'|'quarterly'|'monthly') lets manual
-- monthly entries be excluded from the annual/quarterly ratio pipeline and
-- routed to the forecast-only path instead.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS is_private BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE xbrl_data_points ADD COLUMN IF NOT EXISTS source      VARCHAR(16) NOT NULL DEFAULT 'sec_edgar';
ALTER TABLE xbrl_data_points ADD COLUMN IF NOT EXISTS granularity VARCHAR(8)  NOT NULL DEFAULT 'annual';

-- xbrl_data_points had no uniqueness constraint at all, so save_xbrl_data_points
-- (insert-only, still used by the SEC pull) has always silently duplicated rows
-- on re-fetch. That was harmless while these tables were write-only, but the
-- new manual-upload upsert path needs a real ON CONFLICT target. Dedupe any
-- pre-existing rows (keep the lowest id per group) before adding the index,
-- the same way idx_risk_scores_run_ref_unique was retrofitted above.
DELETE FROM xbrl_data_points d USING xbrl_data_points dup
    WHERE d.series_id = dup.series_id
      AND d.period_end = dup.period_end
      AND COALESCE(d.form, '') = COALESCE(dup.form, '')
      AND d.source = dup.source
      AND d.id > dup.id;
CREATE UNIQUE INDEX IF NOT EXISTS idx_xbrl_dp_unique
    ON xbrl_data_points (series_id, period_end, form, source);

-- DevOps Monitoring: append-only, immutable SARIF/SAST evidence log (evidence_endpoints.py).
-- Nothing else in the schema carries SARIF's per-finding shape (rule/CWE/CVE/file/line) or
-- the dedup-fingerprint + cryptographic-signature requirements, so this is a purpose-built
-- table rather than a reuse of system_telemetry (which HIGH/CRITICAL findings ALSO get
-- mirrored into, for adjudication — see evidence_endpoints.py's ingest handler).
CREATE TABLE IF NOT EXISTS observability.evidence_records (
    id              BIGSERIAL    PRIMARY KEY,
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    repository      VARCHAR(256) NOT NULL,
    commit_sha      VARCHAR(64),
    pipeline_run_id VARCHAR(128),
    source          VARCHAR(32)  NOT NULL DEFAULT 'other',  -- github_actions | gitlab_ci | snyk | sonarqube | checkmarx | other
    rule_id         VARCHAR(256),
    severity        VARCHAR(16)  NOT NULL DEFAULT 'INFO',   -- CRITICAL | HIGH | MEDIUM | LOW | INFO
    cwe             VARCHAR(32),
    cve             VARCHAR(32),
    file_path       TEXT,
    line_number     INTEGER,
    line_snippet    TEXT,
    fingerprint     CHAR(64)     NOT NULL,  -- SHA256(repository|file_path|rule_id|line_snippet)
    author          VARCHAR(256),
    approver        VARCHAR(256),
    scan_status     VARCHAR(16)  NOT NULL DEFAULT 'FAIL',   -- PASS | FAIL
    raw_sarif       JSONB,
    record_json     JSONB        NOT NULL,   -- canonical payload the signature below covers
    signature       CHAR(64)     NOT NULL    -- HMAC-SHA256(record_json, EVIDENCE_SIGNING_KEY)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_fingerprint_commit
    ON observability.evidence_records (fingerprint, commit_sha);
CREATE INDEX IF NOT EXISTS idx_evidence_repository
    ON observability.evidence_records (repository, ingested_at DESC);
-- Tamper-evidence chain: a per-record HMAC (signature above) proves a row's
-- OWN content wasn't altered, but proves nothing about whether a row was
-- deleted or reordered out from between its neighbors — that requires a
-- link to the prior row. chain_hash = sha256(prev_row.chain_hash + this
-- row's signature), computed under an advisory lock at insert time (see
-- insert_evidence_record) so concurrent inserts can't fork the chain.
-- Nullable because pre-existing rows from before this column existed have
-- no prior chain_hash to link from — verify_evidence_chain() only walks
-- rows where chain_hash IS NOT NULL and documents that boundary rather than
-- fabricating a retroactive chain over rows that were never signed for it.
ALTER TABLE observability.evidence_records ADD COLUMN IF NOT EXISTS chain_hash CHAR(64);
CREATE INDEX IF NOT EXISTS idx_evidence_severity
    ON observability.evidence_records (severity, ingested_at DESC);

-- DevOps Monitoring: last-known compliance snapshot per repo, so each new audit
-- (scheduled poll or on-demand run) has something to diff against. Not the
-- audit history itself (that's system_telemetry / adjudicated_tool_calls) —
-- just "what did we see last time", overwritten every audit.
CREATE TABLE IF NOT EXISTS observability.scm_repository_state (
    resource     VARCHAR(256) PRIMARY KEY,   -- e.g. "org/repo@main"
    compliance   JSONB        NOT NULL,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- DevOps Monitoring: drift/time-series log. Catches "2am override" style
-- incidents — an admin temporarily weakens a control, merges, restores it —
-- that a webhook-only or single-snapshot system would never see, because the
-- state is compliant again by the time anyone looks. Populated by comparing
-- each audit's compliance dict against scm_repository_state's prior value
-- (see scm_connectors.diff_compliance / db.record_scm_audit_snapshot).
CREATE TABLE IF NOT EXISTS observability.scm_drift_events (
    id              BIGSERIAL    PRIMARY KEY,
    resource        VARCHAR(256) NOT NULL,
    control_name    VARCHAR(64)  NOT NULL,
    expected_state  JSONB        NOT NULL,
    actual_state    JSONB        NOT NULL,
    direction       VARCHAR(16)  NOT NULL,   -- regressed | improved
    detected_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_scm_drift_open
    ON observability.scm_drift_events (resource, control_name)
    WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_scm_drift_resource
    ON observability.scm_drift_events (resource, detected_at DESC);

-- DevOps Monitoring: Risk Waiver & Exception Hub. A preparer requests a waiver
-- via the existing HITL approval_tasks workflow (gate_type='devops_scm_exception',
-- see approvals_endpoints.py); when a manager approves it, one row lands here.
-- Kept as its own table (rather than living only in approval_tasks.adjustments
-- JSONB) because automated expiry needs an indexed, queryable expires_at — you
-- cannot efficiently sweep JSONB-buried dates across many rows.
CREATE TABLE IF NOT EXISTS observability.risk_waivers (
    id                   BIGSERIAL    PRIMARY KEY,
    vulnerability_hash   CHAR(64)     NOT NULL,  -- evidence_records.fingerprint or a scm control key
    reason               TEXT         NOT NULL,
    compensating_control TEXT,
    approved_by          VARCHAR(128) NOT NULL,
    approval_task_id     BIGINT       REFERENCES approval_tasks(id),
    expires_at           TIMESTAMPTZ  NOT NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    status               VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'  -- ACTIVE | EXPIRED | REVOKED
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_risk_waivers_active_hash
    ON observability.risk_waivers (vulnerability_hash) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_risk_waivers_expiry
    ON observability.risk_waivers (expires_at) WHERE status = 'ACTIVE';

-- Continuous Third-Party/Vendor Risk: the auditor-maintained register of which
-- vendors are "critical" and what their current SOC 2 report's coverage
-- window is — turns VM-01/VM-02's static control-library entries into a
-- live-monitored program. status flips CURRENT -> EXPIRED the same way
-- risk_waivers.status flips ACTIVE -> EXPIRED, so vendor_risk_sweep.py's
-- UPDATE...RETURNING only returns vendors newly crossing expiry each tick
-- instead of re-flagging the same expired vendor on every sweep.
CREATE TABLE IF NOT EXISTS observability.vendor_risk_profiles (
    id                BIGSERIAL    PRIMARY KEY,
    vendor_name       VARCHAR(256) NOT NULL,
    vendor_id         VARCHAR(128),          -- ERP supplier ID, when known (oracle_fusion_tool.py SupplierId)
    critical          BOOLEAN      NOT NULL DEFAULT FALSE,
    soc2_report_date  DATE,
    soc2_expires_at   DATE,
    status            VARCHAR(16)  NOT NULL DEFAULT 'CURRENT',  -- CURRENT | EXPIRED
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vendor_risk_profiles_name
    ON observability.vendor_risk_profiles (vendor_name);
CREATE INDEX IF NOT EXISTS idx_vendor_risk_profiles_soc2_expiry
    ON observability.vendor_risk_profiles (soc2_expires_at) WHERE status = 'CURRENT';

-- AI Governance: the audited company's OWN AI system usage — distinct from
-- observability.mcp_telemetry, which inventories only this platform's own
-- MCP tool calls (see ai-inventory.jsx). This is a manual attestation
-- register, not a live connector — there's no API to poll for "unauthorized
-- shadow AI usage" the way there is for GitHub secrets or SAP journal
-- entries, so an auditor records each known AI system here (AI-05: Third-
-- Party AI Tool Assessment). requires_human_oversight/human_oversight_defined
-- back AI-06 (Human Oversight of AI Systems): a gap between them is flagged
-- immediately on save (ai_governance_endpoints.py), not swept — it's a
-- static configuration gap, not something that decays with time the way a
-- SOC 2 report's coverage window does. assessment_expires_at IS swept
-- (ai_governance_sweep.py), same CURRENT->EXPIRED status-flip pattern as
-- vendor_risk_profiles.soc2_expires_at.
CREATE TABLE IF NOT EXISTS observability.ai_system_registry (
    id                        BIGSERIAL    PRIMARY KEY,
    system_name               VARCHAR(256) NOT NULL,
    vendor                    VARCHAR(256),
    business_owner            VARCHAR(256),
    risk_tier                 VARCHAR(16)  NOT NULL DEFAULT 'MEDIUM',  -- LOW | MEDIUM | HIGH
    requires_human_oversight  BOOLEAN      NOT NULL DEFAULT FALSE,
    human_oversight_defined   BOOLEAN      NOT NULL DEFAULT FALSE,
    last_assessment_date      DATE,
    assessment_expires_at     DATE,
    status                    VARCHAR(16)  NOT NULL DEFAULT 'CURRENT',  -- CURRENT | EXPIRED
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_system_registry_name
    ON observability.ai_system_registry (system_name);
CREATE INDEX IF NOT EXISTS idx_ai_system_registry_assessment_expiry
    ON observability.ai_system_registry (assessment_expires_at) WHERE status = 'CURRENT';

-- DevOps Monitoring: pipeline provenance/attestation metadata (SLSA-adjacent).
-- Stores what a CI run claims about itself — OIDC identity claims, SLSA
-- provenance statement, a hash (never the raw values) of its environment
-- variables so an injected SKIP_TESTS=true/DISABLE_SAST=1 flag is detectable,
-- runner metadata, an optional Cosign/Sigstore bundle, and an SBOM. See
-- attestation.py for the (structural, not full Sigstore-trust-root) validation
-- this data gets — real cryptographic verification requires the actual `cosign`
-- binary and network access to Rekor, which this environment does not assume.
CREATE TABLE IF NOT EXISTS observability.pipeline_attestations (
    id                BIGSERIAL    PRIMARY KEY,
    commit_sha        VARCHAR(64)  NOT NULL,
    pipeline_run_id   VARCHAR(128),
    oidc_actor        VARCHAR(256),
    oidc_claims       JSONB,
    slsa_provenance   JSONB,
    slsa_level        SMALLINT,             -- 0-3, structural estimate — see attestation.validate_slsa_provenance
    env_vars_hash     CHAR(64),
    runner_type       VARCHAR(32),          -- github-hosted | self-hosted | gitlab-shared | gitlab-self-managed | other
    runner_id         VARCHAR(256),
    container_image_sha VARCHAR(128),
    cosign_bundle     JSONB,
    cosign_verified    VARCHAR(16),         -- true | false | unknown (no cosign binary available)
    sbom_format       VARCHAR(16),          -- cyclonedx | spdx
    sbom              JSONB,
    license_risk      BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pipeline_attestations_commit
    ON observability.pipeline_attestations (commit_sha, created_at DESC);

-- DevOps Monitoring: ITSM/Jira-ServiceNow SLA Bridge. A ticket is opened
-- against an external ITSM system for a finding (SARIF evidence fingerprint
-- or an SCM control key — same key space as risk_waivers.vulnerability_hash),
-- and its remediation SLA is tracked here rather than only in the external
-- system, so a breach can be detected and re-escalated even if nobody is
-- watching Jira/ServiceNow (see itsm_sla_sweep.py). Kept as its own table for
-- the same reason as scm_drift_events/risk_waivers: an indexed sla_due_at is
-- required for the sweep, which JSONB-buried dates can't give efficiently.
CREATE TABLE IF NOT EXISTS observability.itsm_tickets (
    id                    BIGSERIAL    PRIMARY KEY,
    finding_hash          CHAR(64)     NOT NULL,  -- evidence_records.fingerprint or an scm control key
    external_system       VARCHAR(16)  NOT NULL,  -- jira | servicenow
    external_ticket_key   VARCHAR(64)  NOT NULL,  -- e.g. 'SEC-142' (Jira) or 'INC0012345' (ServiceNow)
    connector_id          BIGINT       REFERENCES observability.poll_connectors(id),
    summary               TEXT,
    severity              VARCHAR(16)  NOT NULL,  -- CRITICAL | HIGH | MEDIUM | LOW
    status                VARCHAR(24)  NOT NULL DEFAULT 'open',  -- open | in_progress | resolved | closed | cancelled
    sla_hours             INTEGER      NOT NULL,
    sla_due_at            TIMESTAMPTZ  NOT NULL,
    sla_breached_at       TIMESTAMPTZ,
    created_by            VARCHAR(128),
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_itsm_tickets_active_hash
    ON observability.itsm_tickets (finding_hash)
    WHERE status NOT IN ('closed', 'cancelled');
-- DORA metrics (dora_metrics.py / compute_dora_metrics): MTTR needs a real
-- resolution timestamp, not updated_at (which bumps on ANY field change,
-- same reason poll_connectors got its own credentials_rotated_at rather
-- than reusing updated_at) — set only on the open/in_progress -> resolved
-- transition by update_itsm_ticket_status, mirroring model_health alerts'
-- existing resolved_at precedent.
ALTER TABLE observability.itsm_tickets ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_itsm_tickets_sla_sweep
    ON observability.itsm_tickets (sla_due_at)
    WHERE sla_breached_at IS NULL AND status NOT IN ('closed', 'cancelled');
CREATE INDEX IF NOT EXISTS idx_itsm_tickets_system
    ON observability.itsm_tickets (external_system, status);

-- Platform-wide tamper-evident audit trail (identity/access changes + MCP
-- tool calls). Distinct from evidence_records above (SARIF/SAST-shaped,
-- scoped to DevOps findings) — this is a generic append-only ledger for "who
-- did what, when", reusing the same hash-chain + HMAC-signature discipline
-- rather than inventing a second tamper-evidence mechanism. category
-- distinguishes the current producers: auth_endpoints.py's admin/session
-- actions, and mcp_guards.py's MCP tool-call log (which used to be a local
-- flat file, wiped on every redeploy since no volume is mounted for it).
CREATE TABLE IF NOT EXISTS observability.audit_log (
    id            BIGSERIAL    PRIMARY KEY,
    occurred_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    category      VARCHAR(32)  NOT NULL,   -- auth | mcp_tool | ...
    action        VARCHAR(64)  NOT NULL,   -- e.g. login, admin_set_role, user_created
    actor         VARCHAR(128),            -- who did it (username, or 'system')
    target        VARCHAR(128),            -- affected user/resource, if any
    ip_address    TEXT,
    detail        JSONB,                   -- old/new values, or a safe kwargs summary
    record_json   JSONB        NOT NULL,   -- canonical payload the signature below covers
    signature     CHAR(64)     NOT NULL,   -- HMAC-SHA256(record_json, AUDIT_SIGNING_KEY)
    chain_hash    CHAR(64)                 -- sha256(prev_row.chain_hash + this row's signature)
);
CREATE INDEX IF NOT EXISTS idx_audit_log_category
    ON observability.audit_log (category, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor
    ON observability.audit_log (actor, occurred_at DESC);

CREATE SEQUENCE IF NOT EXISTS observability.map_ref_seq;

-- Continuous Monitoring: Management Action Plans raised against a control
-- that keeps requiring human review — map_detection_sweep.py's recurrence
-- detector, not a one-off finding. Deliberately its own table rather than a
-- row in exception_control_events (a MAP outlives and aggregates many of
-- those rows) or in approval_tasks alone (a MAP has its own lifecycle —
-- proposed -> approved -> in_progress -> closed — that outlasts the single
-- decision approval_tasks tracks; approval_task_id links to that decision,
-- not the whole plan). Field names deliberately match the existing
-- client-only MAP shape risk-engine.js/rail.jsx already use (finding,
-- root_cause, action, owner, due_date, completion_pct, reduction_pct,
-- success_criteria) so the same MapsTab card UI can render both populations.
CREATE TABLE IF NOT EXISTS observability.management_action_plans (
    id                   BIGSERIAL    PRIMARY KEY,
    map_ref              VARCHAR(32)  NOT NULL UNIQUE,   -- e.g. "MAP-CM-000042"
    control_id           VARCHAR(128) NOT NULL,
    system_source        VARCHAR(64),
    finding              TEXT         NOT NULL,          -- what's recurring, human-readable
    root_cause           TEXT,                            -- proposed by AI, reviewable/editable before approval
    risk_rating          VARCHAR(8),                       -- R | A | G — same vocabulary as risk_scores.rag_status
    action               TEXT,                            -- proposed remediation plan
    owner                VARCHAR(128),
    due_date             DATE,
    success_criteria     TEXT,
    reduction_pct        NUMERIC(5,2),
    completion_pct       INTEGER      NOT NULL DEFAULT 0,
    occurrence_count     INTEGER      NOT NULL,
    window_days          INTEGER      NOT NULL,
    first_occurrence_at  TIMESTAMPTZ,
    last_occurrence_at   TIMESTAMPTZ,
    source_event_ids     BIGINT[],
    status               VARCHAR(16)  NOT NULL DEFAULT 'proposed',  -- proposed | approved | rejected | in_progress | closed
    approval_task_id     BIGINT       REFERENCES approval_tasks(id),
    reviewed_by_name     VARCHAR(128),
    reviewed_at          TIMESTAMPTZ,
    review_comment       TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
-- Only one open MAP per control at a time — a second recurrence pass while
-- one is already proposed/approved/in_progress should never open a
-- duplicate, same "one ACTIVE waiver per hash" discipline risk_waivers uses.
CREATE UNIQUE INDEX IF NOT EXISTS idx_map_open_per_control
    ON observability.management_action_plans (control_id)
    WHERE status IN ('proposed', 'approved', 'in_progress');
CREATE INDEX IF NOT EXISTS idx_map_status
    ON observability.management_action_plans (status, created_at DESC);

-- PBC/workpaper evidence quality (evidence_quality_tool.py's deterministic
-- checks + evidence_quality_endpoints.py's one LLM-assisted content check).
-- Distinct from observability.evidence_records (SARIF/SAST findings from
-- automated scanners) — this is auditor-logged evidence for a manual
-- control test (a screenshot, a config export, an approval email), where
-- the question isn't "did a scanner find something" but "does this
-- artifact actually, and currently, support the control it's attached to."
CREATE TABLE IF NOT EXISTS observability.pbc_evidence (
    id                  BIGSERIAL    PRIMARY KEY,
    control_id          VARCHAR(128) NOT NULL,
    title               TEXT         NOT NULL,
    description         TEXT,
    source_url          TEXT,
    period_start        DATE,
    period_end          DATE,
    collected_date      DATE,
    has_signature       BOOLEAN      NOT NULL DEFAULT FALSE,
    requires_signature  BOOLEAN      NOT NULL DEFAULT FALSE,
    quality_flags       JSONB        NOT NULL DEFAULT '[]',
    content_check       JSONB,
    created_by          VARCHAR(128),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pbc_evidence_control
    ON observability.pbc_evidence (control_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pbc_evidence_flagged
    ON observability.pbc_evidence (created_at DESC) WHERE quality_flags != '[]'::jsonb;

-- Infrastructure Vulnerability & Currency Posture (Phase 1): a unified
-- asset inventory across DB/OS/container/network/credential/certificate —
-- host/database/container config posture already lives in system_telemetry
-- (postgres_cis/aws_iaas/railway_iaas events), but "what's still open vs.
-- remediated" is inherently STATEFUL (an asset's patch/CVE/expiry status
-- persists and changes over time), the same reasoning risk_waivers/
-- vendor_risk_profiles/itsm_tickets/management_action_plans each earned
-- their own table for instead of trying to re-derive current state from an
-- event log on every read. last_assessed_at IS NULL is the load-bearing
-- field here: it's what lets "never scanned" render distinctly from "scanned
-- and clean" — an unscanned asset must never look the same as a clean one.
CREATE TABLE IF NOT EXISTS observability.infra_assets (
    id                 BIGSERIAL    PRIMARY KEY,
    -- Dev/test tenants routinely register more than one customer estate
    -- under one connector row (or none, for ingest-sourced assets) — without
    -- this, two customers' "i-0abc123" or "primary-db" collide on asset_key.
    estate_label       VARCHAR(128) NOT NULL DEFAULT 'default',
    asset_key          VARCHAR(256) NOT NULL,  -- adapter-stable id: "postgres:primary-db", "aws:i-0abc123", "cert:api.example.com:443"
    connector_id       BIGINT       REFERENCES observability.poll_connectors(id),
    asset_type         VARCHAR(24)  NOT NULL,  -- host | database | container | network_device | credential | certificate
    name               VARCHAR(256) NOT NULL,
    environment        VARCHAR(64),
    os_name            VARCHAR(64),
    os_version         VARCHAR(64),
    software_name      VARCHAR(128),
    software_version   VARCHAR(64),
    -- OSV.dev ecosystem string, e.g. "PyPI" | "Debian:12" | "npm" — see
    -- osv_client.py. Deliberately NULL-able and often NULL: OSV has no
    -- "PostgreSQL"/"generic DB engine" ecosystem, so a bare DSN-derived
    -- version (no known host distro) can never populate this honestly.
    -- version_baselines.py is the separate, non-OSV currency check for
    -- exactly that case — see its module docstring.
    ecosystem          VARCHAR(32),
    image_digest       VARCHAR(128),
    region             VARCHAR(64),
    expires_at         TIMESTAMPTZ,   -- credential/certificate expiry, if applicable to this asset_type
    last_assessed_at   TIMESTAMPTZ,   -- NULL = never assessed; distinct from "assessed and clean"
    assessment_source  VARCHAR(24),   -- e.g. "postgres_cis" | "tls_cert" | "osv" | "scanner"
    source             VARCHAR(24)  NOT NULL DEFAULT 'connector',  -- connector | ingest | manual
    metadata           JSONB,
    first_seen_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    active             BOOLEAN      NOT NULL DEFAULT TRUE
);
-- COALESCE(connector_id, 0) so ingest-sourced assets (connector_id NULL,
-- e.g. a manually-registered credential or a future scanner upload) still
-- dedup on (estate, asset_key) — a plain UNIQUE(connector_id, asset_key)
-- would treat every NULL as distinct and re-insert the same asset per call.
CREATE UNIQUE INDEX IF NOT EXISTS idx_infra_assets_key
    ON observability.infra_assets (estate_label, COALESCE(connector_id, 0), asset_key);
CREATE INDEX IF NOT EXISTS idx_infra_assets_type
    ON observability.infra_assets (asset_type, active);
CREATE INDEX IF NOT EXISTS idx_infra_assets_connector
    ON observability.infra_assets (connector_id);
CREATE INDEX IF NOT EXISTS idx_infra_assets_expiring
    ON observability.infra_assets (expires_at) WHERE expires_at IS NOT NULL AND active = TRUE;
CREATE INDEX IF NOT EXISTS idx_infra_assets_unassessed
    ON observability.infra_assets (last_assessed_at) WHERE active = TRUE;

-- Infrastructure Vulnerability & Currency Posture (Phase 2): the
-- vulnerability register itself. asset_id is nullable because a SARIF
-- scanner finding (container image scan, SAST) often has no matching
-- infra_assets row — it's keyed on a repo/commit, not a tracked asset —
-- so the OSV-enrichment path (asset_id set) and the SARIF-bridge path
-- (asset_id NULL, evidence_record_id set) both write here without one
-- forcing a fake asset row for the other.
CREATE TABLE IF NOT EXISTS observability.infra_vulnerabilities (
    id                  BIGSERIAL    PRIMARY KEY,
    asset_id            BIGINT       REFERENCES observability.infra_assets(id),
    vuln_id             VARCHAR(64)  NOT NULL,   -- CVE-... | GHSA-... | OSV id
    aliases             TEXT[],                  -- other ids OSV reports for the same vuln
    source              VARCHAR(16)  NOT NULL DEFAULT 'osv',  -- osv | scanner | connector
    source_ref          VARCHAR(256),            -- SARIF ruleId / evidence fingerprint, when source='scanner'
    severity            VARCHAR(16)  NOT NULL DEFAULT 'INFO',
    cvss_score          NUMERIC(4,1),
    title               TEXT,
    summary             TEXT,
    affected_version    VARCHAR(64),
    fixed_version       VARCHAR(64),
    published_at        TIMESTAMPTZ,
    first_detected_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    status              VARCHAR(24)  NOT NULL DEFAULT 'open',  -- open | remediated | accepted_risk | false_positive
    remediated_at       TIMESTAMPTZ,
    -- HOW a status flip to remediated was decided — never inferred from a
    -- scan simply not re-reporting the finding (absence isn't evidence of a
    -- fix). "version_advanced" is the one sweep-driven case: the asset's
    -- software_version moved past fixed_version. Everything else is a human
    -- action (waiver, manual close).
    remediation_basis   VARCHAR(32),   -- version_advanced | waiver | manual
    waiver_id           BIGINT       REFERENCES observability.risk_waivers(id),
    evidence_record_id  BIGINT       REFERENCES observability.evidence_records(id),
    disposition_reason  TEXT,
    disposed_by         VARCHAR(128),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
-- COALESCE both nullable dedup legs the same way idx_infra_assets_key does
-- for connector_id — a NULL asset_id (SARIF path) or NULL source_ref
-- (OSV path) must not make every row look "distinct" to a plain UNIQUE.
CREATE UNIQUE INDEX IF NOT EXISTS idx_infra_vulns_dedup
    ON observability.infra_vulnerabilities (COALESCE(asset_id, 0), vuln_id, COALESCE(source_ref, ''));
CREATE INDEX IF NOT EXISTS idx_infra_vulns_status
    ON observability.infra_vulnerabilities (status, severity);
CREATE INDEX IF NOT EXISTS idx_infra_vulns_asset
    ON observability.infra_vulnerabilities (asset_id) WHERE asset_id IS NOT NULL;

-- OSV.dev response cache, keyed on the exact query triple. OSV is free and
-- unauthenticated but re-querying the same (ecosystem, package, version)
-- every sweep tick is wasted API load for an answer that essentially never
-- changes intra-day — osv_client.py treats a cache row as fresh for
-- OSV_CACHE_TTL_HOURS (default 24h) before re-querying.
CREATE TABLE IF NOT EXISTS observability.osv_cache (
    ecosystem     VARCHAR(32)  NOT NULL,
    package_name  VARCHAR(256) NOT NULL,
    version       VARCHAR(64)  NOT NULL,
    vulns         JSONB        NOT NULL,   -- raw OSV vulns[] array for this triple, [] = queried, none found
    queried_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ecosystem, package_name, version)
);
"""

# Formatted at init time with the module-level EMBEDDING_DIM.
# chunk_index allows long documents (risk factors, proxy filings) to be split into
# multiple chunks so each gets its own vector — critical for accurate retrieval.
# company_id enables fast per-company filtering without joining source tables.
_PGVECTOR_DDL_TEMPLATE = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS embeddings (
    id           BIGSERIAL    PRIMARY KEY,
    source_table VARCHAR(64)  NOT NULL,
    source_id    BIGINT       NOT NULL,
    content_type VARCHAR(64)  NOT NULL,
    model        VARCHAR(64)  NOT NULL DEFAULT 'unknown',
    chunk_index  SMALLINT     NOT NULL DEFAULT 0,
    company_id   INT          REFERENCES companies(id),
    embedding    vector({dim}),
    text_snippet TEXT,
    source_hash  VARCHAR(64),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_embeddings UNIQUE (source_table, source_id, content_type, model, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_source ON embeddings (source_table, source_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw   ON embeddings USING hnsw (embedding vector_cosine_ops);
"""

# Column / constraint migrations for databases created before these columns existed.
_PGVECTOR_MIGRATIONS = """
ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS chunk_index SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS company_id  INT REFERENCES companies(id);
-- Freshness sidecar for concept embeddings (EMBT_CONCEPT): mirrors
-- concepts.label_hash. When the two differ, the concept's label/definition/
-- alt_labels changed since it was last embedded — reembed_stale_concepts()
-- finds these and re-embeds, relying on uq_embeddings' upsert to overwrite
-- in place (no delete needed). NULL for every other content type; nothing
-- else uses staleness detection today.
ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64);
ALTER TABLE embeddings DROP CONSTRAINT IF EXISTS embeddings_source_table_source_id_content_type_model_key;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_embeddings'
          AND conrelid = 'embeddings'::regclass
    ) THEN
        ALTER TABLE embeddings ADD CONSTRAINT uq_embeddings
            UNIQUE (source_table, source_id, content_type, model, chunk_index);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_embeddings_company ON embeddings (company_id) WHERE company_id IS NOT NULL;
"""


# ─────────────────────────────────────────────────────────────────────────────
# Pool management
# ─────────────────────────────────────────────────────────────────────────────

def _build_dsn(url: str) -> str:
    # connect_timeout=8 keeps DNS failures fast (vs. OS default ~75 s).
    dsn = url if "connect_timeout" in url else url + ("&" if "?" in url else "?") + "connect_timeout=8"
    # Enforce encryption in transit rather than trusting the URL alone to have
    # specified it — a bare/copy-pasted DSN would otherwise connect over
    # plaintext TCP with no signal anything is wrong. Only applied when the
    # DSN doesn't already declare a mode, so an operator's explicit choice
    # (including a deliberate "disable" for a local/private-network Postgres)
    # is always respected. DATABASE_SSL_MODE overrides the "require" default
    # for environments where TLS genuinely isn't available.
    if "sslmode" not in dsn:
        ssl_mode = os.environ.get("DATABASE_SSL_MODE", "require").strip()
        if ssl_mode:
            dsn = dsn + "&" + "sslmode=" + ssl_mode
    return dsn


def _apply_schema(pool: "pg_pool.ThreadedConnectionPool") -> None:
    """Apply core DDL/migrations (raises on failure) plus the optional
    observability and pgvector schemas (best-effort — non-fatal if Postgres
    lacks permissions for CREATE SCHEMA/EXTENSION, e.g. a restricted managed
    DB user). Shared by init_db() (legacy global pool, TENANT_MODE=single)
    and init_tenant_db() (one fresh tenant database, TENANT_MODE=multi) so
    every tenant gets the identical, unmodified schema."""
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            cur.execute(_MIGRATIONS)  # reconcile column drift on existing tables
        conn.commit()
    finally:
        pool.putconn(conn)
    logger.info("PostgreSQL database initialized (tables + migrations applied)")

    obs_conn = pool.getconn()
    try:
        with obs_conn.cursor() as cur:
            cur.execute(_OBSERVABILITY_DDL)
            cur.execute(_OBSERVABILITY_MIGRATIONS)  # runs after DDL — targets tables _DDL/_OBSERVABILITY_DDL just created
        obs_conn.commit()
        logger.info("Observability schema ready (mcp_sessions, mcp_telemetry, adjudicated_tool_calls)")
    except Exception as exc:
        obs_conn.rollback()
        logger.warning("Observability schema init failed (non-fatal): %s", exc)
    finally:
        pool.putconn(obs_conn)

    # pgvector extension + embeddings table — optional; logged as warning if absent.
    # _PGVECTOR_READY is process-wide, not per-pool: under TENANT_MODE=multi
    # every tenant is provisioned through this same function, so pgvector
    # availability is expected to be uniform across tenants. It's a
    # feature-availability flag only (whether _conn() registers the vector
    # type), not a data-isolation boundary, so this coarseness is deliberate.
    global _PGVECTOR_READY
    vec_conn = pool.getconn()
    try:
        with vec_conn.cursor() as cur:
            cur.execute(_PGVECTOR_DDL_TEMPLATE.format(dim=EMBEDDING_DIM))
            cur.execute(_PGVECTOR_MIGRATIONS)
        vec_conn.commit()
        _PGVECTOR_READY = True
        logger.info("pgvector extension ready (EMBEDDING_DIM=%d)", EMBEDDING_DIM)
    except Exception as exc:
        vec_conn.rollback()
        logger.warning("pgvector not available — embedding features disabled: %s", exc)
    finally:
        pool.putconn(vec_conn)


def init_db() -> bool:
    """Initialize the legacy single-tenant global connection pool
    (TENANT_MODE=single, the default). Under TENANT_MODE=multi this is not
    called at request time at all — see init_tenant_db()/bind_tenant_pool()."""
    global _pool
    if _pool is not None:
        return True  # already connected — don't clobber a live pool
    if not _HAS_PSYCOPG2:
        logger.warning("psycopg2 not installed — database persistence disabled. "
                       "Run: pip install psycopg2-binary")
        return False
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        logger.info("DATABASE_URL not set — database persistence disabled")
        return False
    try:
        _pool = pg_pool.ThreadedConnectionPool(1, 10, dsn=_build_dsn(url))
        _apply_schema(_pool)
    except Exception as exc:
        logger.error("Database init failed: %s", exc)
        _pool = None
        return False
    return True


def init_tenant_db(dsn: str) -> bool:
    """Provision a fresh tenant database: apply the full schema (identical
    to init_db()'s) against an arbitrary DSN, independent of the legacy
    global pool. Called once per tenant at provisioning time by
    provision_tenant.py — NOT on every request; per-request routing uses
    bind_tenant_pool() against the small long-lived pool it maintains."""
    if not _HAS_PSYCOPG2:
        raise RuntimeError("psycopg2 not installed — run: pip install psycopg2-binary")
    tmp_pool = pg_pool.ThreadedConnectionPool(1, 2, dsn=_build_dsn(dsn))
    try:
        _apply_schema(tmp_pool)
    finally:
        tmp_pool.closeall()
    return True


def bind_tenant_pool(tenant_id: str, dsn: str) -> None:
    """Lazily create (or reuse) a small connection pool for this tenant and
    make it the target for every db.* call made in the current
    request/task context. Called once, at the top of each request, by
    api_server.py's tenant-resolution middleware — never by application/
    business-logic code, which should keep calling db.* exactly as before."""
    if tenant_id not in _tenant_pools:
        if len(_tenant_pools) >= _MAX_TENANT_POOLS:
            _evict_lru_tenant_pool()
        _tenant_pools[tenant_id] = pg_pool.ThreadedConnectionPool(1, 3, dsn=_build_dsn(dsn))
    _tenant_pool_last_used[tenant_id] = time.monotonic()
    _current_tenant.set(tenant_id)


def unbind_tenant() -> None:
    """Clear the tenant bound to the current request/task context. Call in a
    `finally` at request end so a later single-tenant-mode call site (or a
    stray background task without its own binding) never inherits a
    previous request's tenant by accident."""
    _current_tenant.set(None)


def _evict_lru_tenant_pool() -> None:
    """Bound _tenant_pools' size (TENANT_POOL_CACHE_SIZE, default 50) by
    closing the least-recently-bound tenant's pool. At "tens of tenants"
    this should rarely trigger; it exists so an operator provisioning far
    more tenants than expected degrades to reconnecting a cold pool on next
    use, rather than exhausting the server's max_connections."""
    if not _tenant_pool_last_used:
        return
    oldest_tenant = min(_tenant_pool_last_used, key=_tenant_pool_last_used.get)
    pool = _tenant_pools.pop(oldest_tenant, None)
    _tenant_pool_last_used.pop(oldest_tenant, None)
    if pool is not None:
        pool.closeall()


def _active_pool() -> Optional["pg_pool.ThreadedConnectionPool"]:
    """The pool _conn()/is_available()/_run() should use: the tenant pool
    bound to the current request/task if one is set, else the legacy global
    pool (single-tenant mode). Fails closed — if a tenant is bound but its
    pool is somehow missing, that's a bug in the caller (bind_tenant_pool()
    wasn't awaited/applied), not a reason to silently fall back to the
    global pool, which in TENANT_MODE=multi may not even be initialized."""
    tenant_id = _current_tenant.get()
    if tenant_id is not None:
        pool = _tenant_pools.get(tenant_id)
        if pool is None:
            raise RuntimeError(
                f"db._current_tenant is set to {tenant_id!r} but no pool is bound for it — "
                "bind_tenant_pool() must be called before any db.* call in this request."
            )
        return pool
    return _pool


def is_available() -> bool:
    """Return True when a connection pool is available for the current
    context — the tenant pool bound to this request, or the legacy global
    pool in single-tenant mode."""
    try:
        return _active_pool() is not None
    except RuntimeError:
        return False


@contextmanager
def _conn():
    """
    Borrow a connection, commit on success, rollback on error, always return.

    A connection whose underlying socket was silently killed while idle (e.g.
    Railway's proxy dropping it) isn't detected by psycopg2 until the
    connection is actually used — it surfaces as OperationalError/
    InterfaceError on the first query, not as `conn.closed`. Without eviction,
    `_pool.putconn(conn)` would hand that same dead connection straight back
    out on the next borrow, so every caller keeps failing with "connection
    already closed" until the process restarts. Detect that case and discard
    the connection (close=True) so the pool opens a fresh one next time,
    instead of recycling a corpse.
    """
    pool = _active_pool()
    if pool is None:
        raise RuntimeError("Database not initialized")
    conn = pool.getconn()
    broken = False
    try:
        if _HAS_PGVECTOR and _PGVECTOR_READY:
            _pg_register_vector(conn)
        yield conn
        conn.commit()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        broken = True
        raise
    except Exception:
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        pool.putconn(conn, close=broken)


get_conn = _conn   # public alias used by mcp_governance and github_endpoints


def ping() -> dict:
    """Check the database connection and report pgvector availability.

    Returns a dict with keys: connected, pgvector, pg_version, vector_version, error.
    Never raises — safe to call at startup or in a health-check endpoint.
    """
    if not is_available():
        return {"connected": False, "pgvector": False, "error": "pool not initialised"}
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                pg_version: str = (cur.fetchone() or ("",))[0]
                cur.execute(
                    "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                )
                row = cur.fetchone()
                vector_version: Optional[str] = row[0] if row else None
        return {
            "connected": True,
            "pgvector": vector_version is not None,
            "pg_version": pg_version,
            "vector_version": vector_version,
            "error": None,
        }
    except Exception as exc:
        return {"connected": False, "pgvector": False, "error": str(exc)}


def _run(fn, default=None, on_error=None):
    """Call fn(), return default on any failure. Never raises.

    on_error, when given, receives the exception before it's swallowed — a
    caller-local capture (not a shared/global "last error", which would race
    under concurrent requests) for the handful of call sites where silently
    returning `default` makes a real failure look identical to "nothing to
    do" (e.g. reoptimization_tool.py's create_risk_loop_run call: without
    this, a schema-mismatch INSERT failure and an ordinary empty result both
    just produce None, and the caller can't tell which happened)."""
    if not is_available():
        return default
    try:
        return fn()
    except Exception as exc:
        logger.error("db: %s", exc)
        if on_error is not None:
            try:
                on_error(exc)
            except Exception:
                pass
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Company
# ─────────────────────────────────────────────────────────────────────────────

def get_company_id_by_ticker(ticker: str) -> Optional[int]:
    """DB-only ticker → company_id lookup, no live EDGAR round-trip. Used to
    scope per-company reads (e.g. corporate events) without needing to
    re-resolve the ticker on every request — a live EDGAR lookup on every
    page load is both wasteful and, under SEC rate-limiting, a failure mode
    that must not be treated as "no company filter"."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM companies WHERE ticker = %s", (ticker.upper(),))
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def upsert_company(meta: dict) -> Optional[int]:
    """Insert or update a company record. Returns company_id."""
    def _do():
        cik_raw = meta.get("cik", "") or ""
        cik = cik_raw.replace("CIK", "").lstrip("0")[:10] or None
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO companies
                        (ticker, cik, company_name, sic, sic_description,
                         entity_type, state_of_incorporation, fiscal_year_end, exchanges)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (ticker) DO UPDATE SET
                        cik                    = EXCLUDED.cik,
                        company_name           = EXCLUDED.company_name,
                        sic                    = EXCLUDED.sic,
                        sic_description        = EXCLUDED.sic_description,
                        entity_type            = EXCLUDED.entity_type,
                        state_of_incorporation = EXCLUDED.state_of_incorporation,
                        fiscal_year_end        = EXCLUDED.fiscal_year_end,
                        exchanges              = EXCLUDED.exchanges,
                        updated_at             = NOW()
                    RETURNING id
                    """,
                    (
                        meta.get("ticker", "").upper(),
                        cik,
                        meta.get("company_name") or meta.get("ticker", ""),
                        meta.get("sic") or None,
                        meta.get("sic_description") or None,
                        meta.get("entity_type") or None,
                        meta.get("state_of_inc") or meta.get("state_of_incorporation") or None,
                        meta.get("fiscal_year_end") or None,
                        meta.get("exchanges") or None,
                    ),
                )
                return cur.fetchone()[0]
    return _run(_do)


def get_company_id(ticker: str) -> Optional[int]:
    """Return companies.id for a ticker, or None if not found."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM companies WHERE ticker = %s", (ticker.upper(),)
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def upsert_private_company(name: str, industry: str = "", fiscal_year_end: str = "") -> Optional[str]:
    """Create a company with no SEC ticker/CIK, keyed by a synthetic PVT-<SLUG>
    pseudo-ticker instead. Every other table (xbrl_metric_series, sic_peers,
    risk_loop_runs, ...) references company_id, and every endpoint/frontend
    call is keyed by ticker (see companies.ticker NOT NULL UNIQUE) — a synthetic
    ticker lets a private company flow through that same plumbing unmodified
    rather than requiring a schema-wide refactor to company_id. Returns the
    assigned ticker, or None on failure."""
    def _do():
        import re
        slug = re.sub(r"[^A-Z0-9]", "", (name or "").upper())[:10] or "COMPANY"
        with _conn() as conn:
            with conn.cursor() as cur:
                ticker = f"PVT-{slug}"
                attempt = 1
                while True:
                    cur.execute("SELECT 1 FROM companies WHERE ticker = %s", (ticker,))
                    if not cur.fetchone():
                        break
                    attempt += 1
                    ticker = f"PVT-{slug[:8]}{attempt}"
                cur.execute(
                    """
                    INSERT INTO companies
                        (ticker, cik, company_name, sic_description, fiscal_year_end, is_private)
                    VALUES (%s, NULL, %s, %s, %s, TRUE)
                    RETURNING ticker
                    """,
                    (ticker, name, industry or None, fiscal_year_end or None),
                )
                return cur.fetchone()[0]
    return _run(_do)


def get_company_meta(ticker: str) -> Optional[dict]:
    """Return the full companies row for a ticker (id, cik, name, sic, is_private,
    ...), or None if not found. Used by build_company_xbrl to resolve a private
    company's identity from the DB instead of an EDGAR lookup — private
    companies have no CIK to look up in the first place."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, ticker, cik, company_name, sic, sic_description,
                           fiscal_year_end, is_private
                    FROM companies WHERE ticker = %s
                    """,
                    (ticker.upper(),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0], "ticker": row[1], "cik": row[2], "company_name": row[3],
                    "sic": row[4], "sic_description": row[5], "fiscal_year_end": row[6],
                    "is_private": row[7],
                }
    return _run(_do)


def is_private_ticker(ticker: str) -> bool:
    """True if `ticker` is a synthetic pseudo-ticker minted by
    upsert_private_company (PVT-<SLUG>). Checked by prefix rather than a DB
    round-trip so callers can branch before doing any company lookup at all —
    mirrors the frontend's identical cfg.ticker.startsWith('PVT-') check."""
    return (ticker or "").upper().startswith("PVT-")


def save_sic_peers(company_id: int, peers: list) -> None:
    """Save SIC peer companies."""
    def _do():
        rows = [
            (company_id,
             p.get("ticker") or None,
             p.get("cik") or None,
             p.get("name") or p.get("company_name") or None,
             p.get("state") or None,
             p.get("sic") or None)
            for p in peers
        ]
        if not rows:
            return
        with _conn() as conn:
            with conn.cursor() as cur:
                # Replace this company's peer set wholesale. sic_peers has no
                # unique constraint, so the previous "INSERT ... ON CONFLICT DO
                # NOTHING" never deduped — every run re-appended the full peer
                # list, accumulating hundreds of duplicate rows (the "552 peers"
                # / repeated-ticker bug). Peer identities are stable per run, so
                # a clean delete-then-insert is the correct semantics.
                cur.execute("DELETE FROM sic_peers WHERE company_id = %s", (company_id,))
                execute_values(
                    cur,
                    "INSERT INTO sic_peers (company_id, peer_ticker, peer_cik, peer_name, peer_state, sic) VALUES %s",
                    rows,
                )
    _run(_do)


def get_sic_peers(ticker: str) -> Optional[dict]:
    """Return the most recently saved SIC peer identities for a ticker, or None
    if nothing has been saved yet. Callers should re-enrich with fresh
    financials (peer identities are stable; financial ratios are not)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, company_name, sic, sic_description, cik FROM companies WHERE ticker = %s",
                    (ticker.upper(),),
                )
                comp = cur.fetchone()
                if not comp:
                    return None
                company_id, company_name, sic, sic_description, cik = comp
                cur.execute(
                    """
                    SELECT DISTINCT peer_ticker, peer_cik, peer_name, peer_state, sic
                    FROM sic_peers WHERE company_id = %s
                    LIMIT 50
                    """,
                    (company_id,),
                )
                peers = [
                    # "company_name" (not "name") to match the field every other
                    # peer object in the codebase uses — PeerTable/PeerTimeSeriesChart
                    # and _enrich_peer_financials() all read p.company_name, so a
                    # "name"-keyed dict here silently rendered as a blank company
                    # column and a "Peer N" chart legend fallback.
                    {"ticker": r[0], "cik": r[1], "company_name": r[2], "state": r[3], "sic": r[4]}
                    for r in cur.fetchall()
                ]
                if not peers:
                    return None
                return {
                    "ticker": ticker.upper(),
                    "company_name": company_name,
                    "sic": sic,
                    "sic_description": sic_description,
                    "cik": cik,
                    "peers": peers,
                }
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# EDGAR
# ─────────────────────────────────────────────────────────────────────────────

def upsert_xbrl_series(company_id: int, metric_name: str, xbrl_tag: Optional[str] = None, unit: str = "USD") -> Optional[int]:
    """Get or create an xbrl_metric_series row. Returns series_id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO xbrl_metric_series (company_id, metric_name, xbrl_tag, unit)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (company_id, metric_name) DO UPDATE SET
                        xbrl_tag   = COALESCE(EXCLUDED.xbrl_tag, xbrl_metric_series.xbrl_tag),
                        fetched_at = NOW()
                    RETURNING id
                    """,
                    (company_id, metric_name, xbrl_tag, unit),
                )
                return cur.fetchone()[0]
    return _run(_do)


def save_xbrl_data_points(series_id: int, data_points: list) -> None:
    """Bulk-insert XBRL data points."""
    def _do():
        rows = [
            (
                series_id,
                dp.get("period_end") or dp.get("end") or dp.get("date"),
                dp.get("period_start") or dp.get("start"),
                dp.get("fiscal_period") or dp.get("fp"),
                dp.get("form"),
                dp.get("value") if dp.get("value") is not None else dp.get("val"),
                dp.get("filed") or dp.get("filed_date"),
                dp.get("accn") or dp.get("accession_number"),
            )
            for dp in data_points
        ]
        rows = [r for r in rows if r[1]]
        if not rows:
            return
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO xbrl_data_points
                        (series_id, period_end, period_start, fiscal_period,
                         form, value, filed_date, accession_number)
                    VALUES %s
                    """,
                    rows,
                )
    _run(_do)


def upsert_manual_data_points(series_id: int, data_points: list) -> None:
    """Upsert user-entered/uploaded data points (source='manual_upload').
    Unlike save_xbrl_data_points (insert-only, appropriate for SEC's append-only
    filing history), re-uploading the same period here updates the value in
    place — a user correcting a typo or replacing a draft trial balance
    shouldn't accumulate duplicate rows. Relies on idx_xbrl_dp_unique
    (series_id, period_end, form, source)."""
    def _do():
        rows = [
            (
                series_id,
                dp.get("period_end") or dp.get("end"),
                dp.get("period_start") or dp.get("start"),
                dp.get("fiscal_period") or dp.get("fp"),
                dp.get("form") or "MANUAL",
                dp.get("value") if dp.get("value") is not None else dp.get("val"),
                dp.get("filed") or dp.get("filed_date"),
                dp.get("accn") or dp.get("accession_number"),
                "manual_upload",
                dp.get("granularity") or "annual",
            )
            for dp in data_points
        ]
        rows = [r for r in rows if r[1]]
        if not rows:
            return
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO xbrl_data_points
                        (series_id, period_end, period_start, fiscal_period,
                         form, value, filed_date, accession_number, source, granularity)
                    VALUES %s
                    ON CONFLICT (series_id, period_end, form, source) DO UPDATE SET
                        value            = EXCLUDED.value,
                        period_start     = EXCLUDED.period_start,
                        fiscal_period    = EXCLUDED.fiscal_period,
                        filed_date       = EXCLUDED.filed_date,
                        accession_number = EXCLUDED.accession_number,
                        granularity      = EXCLUDED.granularity
                    """,
                    rows,
                )
    _run(_do)


def get_manual_financials(company_id: int, granularity: Optional[list[str]] = None) -> dict:
    """Reconstruct an xbrl-shaped dict ({metric_name: {tag, label, unit,
    data_points: [{end, start, val, fp, form, filed, accn}, ...]}}) from
    manually-uploaded data points — this is the first read path for
    xbrl_metric_series/xbrl_data_points, which were write-only before manual
    ingestion existed (fetch_xbrl_facts always re-pulls live from EDGAR
    instead of reading them back). Pass granularity=['annual','quarterly'] to
    feed the ratio/Beneish/Altman pipeline, or granularity=['monthly'] to feed
    the forecast-only path."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                params: list = [company_id]
                gran_clause = ""
                if granularity:
                    gran_clause = "AND dp.granularity = ANY(%s)"
                    params.append(list(granularity))
                cur.execute(
                    f"""
                    SELECT s.metric_name, s.xbrl_tag, s.unit,
                           dp.period_end, dp.period_start, dp.fiscal_period,
                           dp.form, dp.value, dp.filed_date, dp.accession_number
                    FROM xbrl_data_points dp
                    JOIN xbrl_metric_series s ON s.id = dp.series_id
                    WHERE s.company_id = %s AND dp.source = 'manual_upload' {gran_clause}
                    ORDER BY dp.period_end DESC
                    """,
                    params,
                )
                xbrl: dict = {}
                for (metric_name, xbrl_tag, unit, period_end, period_start,
                     fiscal_period, form, value, filed_date, accn) in cur.fetchall():
                    entry = xbrl.setdefault(metric_name, {
                        "tag": xbrl_tag or metric_name,
                        "label": metric_name,
                        "unit": unit or "USD",
                        "data_points": [],
                    })
                    entry["data_points"].append({
                        "end":   period_end.isoformat() if period_end else None,
                        "start": period_start.isoformat() if period_start else None,
                        "val":   float(value) if value is not None else None,
                        "fp":    fiscal_period,
                        "form":  form,
                        "filed": filed_date.isoformat() if filed_date else None,
                        "accn":  accn,
                    })
                return xbrl
    return _run(_do, default={})


def save_edgar_8k_events(company_id: int, events: list) -> list[str]:
    """
    Save annotated 8-K events, keyed by (company_id, accession_number) so
    re-running the same ticker never creates duplicate rows for a filing
    already seen. Returns the accession_numbers that were genuinely new this
    call — the basis for change-detection: a non-empty return means new
    material corporate events since the last time this ran.

    Events with no accession_number (legacy callers, or a filing whose
    accession number wasn't available) fall back to a plain insert, same as
    the old behavior — they just won't be deduplicated.
    """
    new_accessions: list[str] = []

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                for ev in events:
                    filing_date = ev.get("date") or ev.get("filing_date")
                    if not filing_date:
                        continue
                    accession = ev.get("accession_number") or None
                    if accession:
                        is_material = bool(ev.get("is_material"))
                        cur.execute(
                            """
                            INSERT INTO edgar_8k_events
                                (company_id, event_date, items, item_descriptions,
                                 accession_number, is_material, classification, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (company_id, accession_number) WHERE accession_number IS NOT NULL
                            DO NOTHING
                            RETURNING id
                            """,
                            (company_id, filing_date,
                             ev.get("items") or ev.get("form"),
                             Json(ev.get("item_descriptions") or {}),
                             accession,
                             is_material,
                             Json(ev.get("classification")) if ev.get("classification") else None,
                             "new" if is_material else None),
                        )
                        if cur.fetchone() is not None:
                            new_accessions.append(accession)
                    else:
                        cur.execute(
                            """
                            INSERT INTO edgar_8k_events (company_id, event_date, items, item_descriptions)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (company_id, filing_date,
                             ev.get("items") or ev.get("form"),
                             Json(ev.get("item_descriptions") or {})),
                        )
    _run(_do)
    return new_accessions


def list_corporate_events(company_id: Optional[int] = None, status: Optional[str] = None) -> list:
    """Material 8-K events (acquisitions, divestitures, restructuring, etc.),
    with their tracked review status. Only is_material rows are returned —
    routine 8-Ks (director changes, Reg FD disclosures) aren't part of this
    trail."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                filters = ["e.is_material"]
                params: list = []
                if company_id is not None:
                    filters.append("e.company_id = %s")
                    params.append(company_id)
                if status is not None:
                    filters.append("e.status = %s")
                    params.append(status)
                cur.execute(
                    f"""
                    SELECT e.id, e.company_id, c.ticker, c.company_name, e.event_date,
                           e.items, e.item_descriptions, e.accession_number, e.classification,
                           e.status, e.owner, e.notes, e.reviewed_at, e.alerted_at, e.created_at
                    FROM edgar_8k_events e
                    JOIN companies c ON c.id = e.company_id
                    WHERE {' AND '.join(filters)}
                    ORDER BY e.event_date DESC
                    """,
                    params,
                )
                cols = ["id", "company_id", "ticker", "company_name", "event_date",
                        "items", "item_descriptions", "accession_number", "classification",
                        "status", "owner", "notes", "reviewed_at", "alerted_at", "created_at"]
                out = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    for tf in ("event_date", "reviewed_at", "alerted_at", "created_at"):
                        if d.get(tf) and hasattr(d[tf], "isoformat"):
                            d[tf] = d[tf].isoformat()
                    out.append(d)
                return out
    return _run(_do) or []


def update_corporate_event(event_id: int, *, status: Optional[str] = None,
                            owner: Optional[str] = None, notes: Optional[str] = None) -> bool:
    """Any field left as None is unchanged. A transition to 'assessed' or
    'dismissed' stamps reviewed_at."""
    sets, params = [], []
    if status is not None:
        sets.append("status = %s")
        params.append(status)
        if status in ("assessed", "dismissed"):
            sets.append("reviewed_at = NOW()")
        elif status == "new":
            sets.append("reviewed_at = NULL")
    if owner is not None:
        sets.append("owner = %s")
        params.append(owner[:128])
    if notes is not None:
        sets.append("notes = %s")
        params.append(notes)
    if not sets:
        return False
    params.append(event_id)
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE edgar_8k_events SET {', '.join(sets)} WHERE id = %s AND is_material",
                    tuple(params),
                )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


def mark_corporate_events_alerted(accession_numbers: list[str]) -> None:
    """Stamp alerted_at so the same material event doesn't re-alert on every
    subsequent pipeline run — mirrors the drift-incident dedup idea, just
    keyed by accession_number instead of an open/closed status."""
    if not accession_numbers:
        return
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE edgar_8k_events SET alerted_at = NOW() WHERE accession_number = ANY(%s) AND alerted_at IS NULL",
                    (accession_numbers,),
                )
    _run(_do)


def save_edgar_risk_factors(
    company_id: int,
    filing_date: str,
    accession_number: str,
    risk_factors_text: str,
    edgar_url: Optional[str] = None,
) -> None:
    """Save Item 1A risk factor text from a 10-K filing."""
    def _do():
        word_count = len((risk_factors_text or "").split())
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO edgar_risk_factor_filings
                        (company_id, filing_date, accession_number,
                         risk_factors_text, word_count, edgar_url)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (company_id, filing_date, accession_number,
                     risk_factors_text, word_count, edgar_url),
                )
    _run(_do)


def save_edgar_proxy(
    company_id: int,
    filing_date: str,
    accession_number: str,
    sections: dict,
) -> Optional[int]:
    """Save DEF 14A proxy governance sections. Upserts on
    (company_id, accession_number) so re-pulling the same filing refreshes it
    in place instead of piling up duplicate rows (see the edgar_proxy_filings
    migration in _MIGRATIONS). Returns the row id (for EMBT_PROXY embedding)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO edgar_proxy_filings
                        (company_id, filing_date, accession_number,
                         executive_compensation, board_of_directors,
                         say_on_pay, shareholder_proposals)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (company_id, accession_number) DO UPDATE SET
                        filing_date            = EXCLUDED.filing_date,
                        executive_compensation = EXCLUDED.executive_compensation,
                        board_of_directors     = EXCLUDED.board_of_directors,
                        say_on_pay              = EXCLUDED.say_on_pay,
                        shareholder_proposals   = EXCLUDED.shareholder_proposals
                    RETURNING id
                    """,
                    (
                        company_id, filing_date, accession_number,
                        sections.get("executive_compensation") or sections.get("Executive Compensation"),
                        sections.get("board_of_directors") or sections.get("Board of Directors"),
                        sections.get("say_on_pay") or sections.get("Say on Pay"),
                        sections.get("shareholder_proposals") or sections.get("Shareholder Proposals"),
                    ),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def get_edgar_proxy(ticker: str) -> Optional[dict]:
    """Return the most recently saved DEF 14A proxy filings for a ticker, or
    None if nothing has been saved yet. Section text is stored in full, so
    this fully reconstructs the /edgar/proxy response without a live fetch."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.company_name, p.filing_date, p.accession_number,
                           p.executive_compensation, p.board_of_directors,
                           p.say_on_pay, p.shareholder_proposals
                    FROM edgar_proxy_filings p
                    JOIN companies c ON c.id = p.company_id
                    WHERE c.ticker = %s
                    ORDER BY p.filing_date DESC
                    LIMIT 5
                    """,
                    (ticker.upper(),),
                )
                rows = cur.fetchall()
                if not rows:
                    return None
                company_name = rows[0][0]
                proxy_filings = [
                    {
                        "filing_date": r[1].isoformat() if hasattr(r[1], "isoformat") else r[1],
                        "accession_number": r[2],
                        "sections": {
                            k: v for k, v in {
                                "executive_compensation": r[3],
                                "board_of_directors":     r[4],
                                "say_on_pay":              r[5],
                                "shareholder_proposals":   r[6],
                            }.items() if v
                        },
                    }
                    for r in rows
                ]
                return {
                    "ticker": ticker.upper(),
                    "company_name": company_name,
                    "proxy_filings": proxy_filings,
                }
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# FRED
# ─────────────────────────────────────────────────────────────────────────────

def _upsert_fred_series_inline(cur, series_id: str, name: str, category: Optional[str] = None, units: Optional[str] = None) -> Optional[int]:
    """Upsert a fred_series row within an existing cursor transaction."""
    cur.execute(
        """
        INSERT INTO fred_series (series_id, name, category, units)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (series_id) DO UPDATE SET
            name     = EXCLUDED.name,
            category = COALESCE(EXCLUDED.category, fred_series.category),
            units    = COALESCE(EXCLUDED.units, fred_series.units)
        RETURNING id
        """,
        (series_id, name or series_id, category, units),
    )
    row = cur.fetchone()
    return row[0] if row else None


def save_fred_series_and_observations(series_map: dict) -> None:
    """
    Persist FRED series metadata and observations.
    series_map: { series_id: { name, category, units, observations: [{date, value}] } }
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                for sid, info in series_map.items():
                    db_id = _upsert_fred_series_inline(
                        cur, sid,
                        info.get("name", sid),
                        info.get("category"),
                        info.get("units"),
                    )
                    if not db_id:
                        continue
                    obs_rows = [
                        (db_id, o.get("date") or o.get("quarter_end"), o.get("value"))
                        for o in info.get("observations", [])
                        if o.get("value") is not None
                    ]
                    if obs_rows:
                        execute_values(
                            cur,
                            """
                            INSERT INTO fred_observations (series_id, quarter_end, value)
                            VALUES %s
                            ON CONFLICT (series_id, quarter_end) DO UPDATE SET value = EXCLUDED.value
                            """,
                            obs_rows,
                        )
    _run(_do)


def save_fred_correlations(company_id: int, correlations: list, run_date: Optional[str] = None) -> None:
    """Save Pearson correlation results linking company financials to FRED series."""
    def _do():
        today = run_date or date.today().isoformat()
        with _conn() as conn:
            with conn.cursor() as cur:
                for c in correlations:
                    db_sid = _upsert_fred_series_inline(
                        cur,
                        c.get("series_id", ""),
                        c.get("series_name") or c.get("name", ""),
                        c.get("category"),
                        c.get("units"),
                    )
                    if not db_sid:
                        continue
                    cur.execute(
                        """
                        INSERT INTO fred_correlations
                            (company_id, financial_metric, fred_series_id, run_date,
                             optimal_lag_quarters, pearson_r, p_value, significant_p05,
                             n_quarter_pairs, direction)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            company_id,
                            c.get("financial_metric", "Revenue"),
                            db_sid,
                            today,
                            c.get("optimal_lag") or c.get("lag_quarters"),
                            c.get("pearson_r") or c.get("r"),
                            c.get("p_value"),
                            c.get("significant") or c.get("significant_p05"),
                            c.get("n_pairs") or c.get("n_quarter_pairs"),
                            c.get("direction"),
                        ),
                    )
    _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# Risk Loop Run
# ─────────────────────────────────────────────────────────────────────────────

def create_risk_loop_run(company_id: Optional[int], config: dict, on_error=None) -> Optional[int]:
    """Create a risk_loop_runs record and return run_id.

    config.trigger_reason distinguishes an organic user-initiated run
    ('user_run', the default) from one created by reoptimization_tool.py:
    'drift_auto_reoptimize' (model_health_drift_watch, on a new drift
    incident) or 'manual_review' (POST /model-health/run-review).
    trigger_incident_id links a drift-triggered run back to the incident
    that caused it, when applicable. See _run's on_error docstring — pass
    on_error to distinguish a real failure from "nothing to do"."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO risk_loop_runs
                        (company_id, ticker, period_begin, period_end_col, industry,
                         appetite_level, persona, data_mode, signal_set,
                         forecast_metric, forecast_horizon, trigger_reason, trigger_incident_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        company_id,
                        config.get("ticker", "").upper(),
                        config.get("period_begin"),
                        config.get("period_end"),
                        config.get("industry"),
                        config.get("appetite_level"),
                        config.get("persona"),
                        config.get("data_mode", "mcp"),
                        config.get("signal_set") or [],
                        config.get("forecast_metric", "Revenue"),
                        config.get("forecast_horizon", 4),
                        config.get("trigger_reason", "user_run"),
                        config.get("trigger_incident_id"),
                    ),
                )
                return cur.fetchone()[0]
    return _run(_do, on_error=on_error)


def complete_risk_loop_run(run_id: int, on_error=None) -> None:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE risk_loop_runs SET completed = TRUE, completed_at = NOW() WHERE id = %s",
                    (run_id,),
                )
    _run(_do, on_error=on_error)


def list_active_tickers(days: int = 90, limit: int = 15) -> list[str]:
    """Tickers (public or private — PVT-* tickers included, no special-casing)
    with a completed run in the last `days` days, oldest-last-run-first. Used
    by reoptimization_tool.run_reoptimization_sweep to pick which tickers get
    re-optimized on a drift trigger — drift signals themselves aren't
    ticker-scoped (see drift_tool.py), so there's no "affected tickers" list
    to derive; sweeping the actively-tracked set, oldest-stale-first, means
    repeated triggers cycle through different tickers instead of always
    hitting the same top N."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticker, MAX(run_at) AS last_run
                    FROM risk_loop_runs
                    WHERE run_at > NOW() - (%s || ' days')::interval
                    GROUP BY ticker
                    ORDER BY last_run ASC
                    LIMIT %s
                    """,
                    (days, limit),
                )
                return [r[0] for r in cur.fetchall()]
    return _run(_do) or []


def get_latest_run_meta(ticker: str) -> Optional[dict]:
    """Most recent completed run's config for a ticker — industry/forecast
    settings a re-optimization run (reoptimization_tool.py) should inherit
    rather than re-derive from a bare SIC code. None if the ticker has never
    completed a run."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT industry, forecast_metric, forecast_horizon
                    FROM risk_loop_runs
                    WHERE ticker = %s AND completed = TRUE
                    ORDER BY run_at DESC LIMIT 1
                    """,
                    (ticker.upper(),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "industry": row[0],
                    "forecast_metric": row[1] or "Revenue",
                    "forecast_horizon": row[2] or 4,
                }
    return _run(_do)


def save_financial_ratios(run_id: int, ratios: dict) -> None:
    if not ratios:
        return
    def _do():
        cols = [
            "revenue_now", "revenue_prev", "revenue_growth", "gross_margin",
            "gross_margin_prev", "gross_margin_index", "net_margin", "fcf_margin",
            "rd_intensity", "sga_intensity", "asset_growth", "cash_ratio",
            "tata", "dsri", "sgi", "assets_now", "cash_now", "net_income_now",
            "operating_cashflow",
        ]
        vals = [ratios.get(c) for c in cols]
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO financial_ratios (run_id, {', '.join(cols)}) "
                    f"VALUES (%s, {', '.join(['%s'] * len(cols))}) ON CONFLICT DO NOTHING",
                    [run_id, *vals],
                )
    _run(_do)


def save_beneish_mscore(run_id: int, mscore: dict) -> None:
    if not mscore or mscore.get("error"):
        return
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO beneish_mscores
                        (run_id, m_score, interpretation, rag_status,
                         dsri_input, gmi_input, sgi_input, tata_input, missing_inputs)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        run_id,
                        mscore.get("m_score"),
                        mscore.get("interpretation"),
                        mscore.get("rag_status"),
                        (mscore.get("inputs") or {}).get("dsri"),
                        (mscore.get("inputs") or {}).get("gmi"),
                        (mscore.get("inputs") or {}).get("sgi"),
                        (mscore.get("inputs") or {}).get("tata"),
                        mscore.get("missing_inputs") or [],
                    ),
                )
    _run(_do)


def save_altman_zscore(run_id: int, zscore: dict) -> None:
    """Persist the Altman Z''-Score for a run (mirrors save_beneish_mscore).
    Without this the Z-score existed only transiently in the live response and
    was lost to any DB-backed reader (run detail, evidence pack)."""
    if not zscore or zscore.get("error"):
        return
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO altman_zscores
                        (run_id, z_score, interpretation, rag_status,
                         x1_input, x2_input, x3_input, x4_input, missing_inputs)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        run_id,
                        zscore.get("z_score"),
                        zscore.get("interpretation"),
                        zscore.get("rag_status"),
                        (zscore.get("inputs") or {}).get("x1"),
                        (zscore.get("inputs") or {}).get("x2"),
                        (zscore.get("inputs") or {}).get("x3"),
                        (zscore.get("inputs") or {}).get("x4"),
                        zscore.get("missing_inputs") or [],
                    ),
                )
    _run(_do)


def get_beneish_mscore(run_id: int) -> Optional[dict]:
    """Single-row Beneish M-Score lookup for a run — lighter than get_run_detail
    when a caller (e.g. the Change Layer) only needs this one figure for two runs."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT m_score, interpretation, rag_status FROM beneish_mscores WHERE run_id = %s",
                    (run_id,),
                )
                r = cur.fetchone()
                if not r:
                    return None
                return {
                    "m_score": float(r[0]) if r[0] is not None else None,
                    "interpretation": r[1], "rag_status": r[2],
                }
    return _run(_do)


def get_altman_zscore(run_id: int) -> Optional[dict]:
    """Single-row Altman Z''-Score lookup for a run (mirrors get_beneish_mscore)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT z_score, interpretation, rag_status FROM altman_zscores WHERE run_id = %s",
                    (run_id,),
                )
                r = cur.fetchone()
                if not r:
                    return None
                return {
                    "z_score": float(r[0]) if r[0] is not None else None,
                    "interpretation": r[1], "rag_status": r[2],
                }
    return _run(_do)


def save_risk_scores(run_id: int, risks: list) -> None:
    """Upsert risk_scores rows for a run, keyed on (run_id, risk_ref).

    Originally insert-only, called exactly once per run at initial analysis
    time — Stage 2's signal-driven adjustments and Gate 1's approved human
    adjustments never made it back in, so every downstream reader keyed on
    this table (get_posture_trend's RAG counts chief among them) silently
    showed the run's pre-adjustment snapshot forever. Now safe to call again
    whenever the true risk state changes — see the risk-scores/{run_id}/sync
    endpoint (Stage 2) and update_risk_score_fields (Gate 1 approvals) below.
    Risks with no risk_ref are skipped (can't upsert without a stable key,
    same rows that would have been dropped by the old insert path anyway).

    narrative/source_framework/segment_type/segment_name are optional and
    COALESCE-preserved on conflict rather than overwritten with NULL — most
    callers (Stage 2 resync, Gate 1 approvals) don't know about these fields
    at all, and an update from one of them must never erase a segment tag or
    narrative a different caller (segment_risk_tool.py, risk-register review)
    already set for the same risk_ref.
    """
    if not risks:
        return
    def _do():
        rows = [
            (
                run_id,
                r.get("risk_ref") or r.get("id"),
                r.get("name") or r.get("risk_name", ""),
                r.get("category"),
                r.get("inherent_score") or r.get("base_score"),
                r.get("delta"),
                r.get("score"),
                r.get("rag_status") or r.get("rag"),
                r.get("velocity"),
                r.get("control_env") or r.get("ce"),
                r.get("peer_benchmark"),
                r.get("narrative"),
                r.get("source_framework"),
                r.get("segment_type"),
                r.get("segment_name"),
            )
            for r in risks
            if (r.get("risk_ref") or r.get("id"))
        ]
        if not rows:
            return
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO risk_scores
                        (run_id, risk_ref, risk_name, category, base_score, delta, score,
                         rag_status, velocity, control_env, peer_benchmark,
                         narrative, source_framework, segment_type, segment_name)
                    VALUES %s
                    ON CONFLICT (run_id, risk_ref) WHERE risk_ref IS NOT NULL DO UPDATE SET
                        risk_name        = EXCLUDED.risk_name,
                        category         = EXCLUDED.category,
                        base_score       = EXCLUDED.base_score,
                        delta            = EXCLUDED.delta,
                        score            = EXCLUDED.score,
                        rag_status       = EXCLUDED.rag_status,
                        velocity         = EXCLUDED.velocity,
                        control_env      = EXCLUDED.control_env,
                        peer_benchmark   = EXCLUDED.peer_benchmark,
                        narrative        = COALESCE(EXCLUDED.narrative, risk_scores.narrative),
                        source_framework = COALESCE(EXCLUDED.source_framework, risk_scores.source_framework),
                        segment_type     = COALESCE(EXCLUDED.segment_type, risk_scores.segment_type),
                        segment_name     = COALESCE(EXCLUDED.segment_name, risk_scores.segment_name)
                    """,
                    rows,
                )
    _run(_do)


_RAG_LETTER_TO_WORD = {"R": "Red", "A": "Amber", "G": "Green"}


def update_risk_score_fields(run_id: int, risk_ref: str, adjustments: dict) -> bool:
    """Patch a single risk_scores row after a Gate 1 adjustment reaches a
    final approved state (auto-approved with no manager, or manager-
    approved) — called from approvals_endpoints.py's prepare/review handlers,
    not the frontend, so it fires regardless of whether the browser that
    submitted the adjustment is still open when a manager finally reviews it.

    adjustments keys mirror AdjustRiskModal's onSubmit payload: name,
    category, rag ('R'/'A'/'G' — risk_scores.rag_status stores full words,
    so this normalizes), score, velocity, ce. Only present keys are applied
    (COALESCE against the existing value), so a partial adjustments dict
    can't null out fields it didn't touch."""
    if not adjustments:
        return False
    rag_letter = (adjustments.get("rag") or "")[:1].upper()
    rag_word = _RAG_LETTER_TO_WORD.get(rag_letter)
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE risk_scores SET
                        risk_name   = COALESCE(%s, risk_name),
                        category    = COALESCE(%s, category),
                        rag_status  = COALESCE(%s, rag_status),
                        score       = COALESCE(%s, score),
                        velocity    = COALESCE(%s, velocity),
                        control_env = COALESCE(%s, control_env)
                    WHERE run_id = %s AND risk_ref = %s
                    """,
                    (
                        adjustments.get("name"), adjustments.get("category"), rag_word,
                        adjustments.get("score"), adjustments.get("velocity"), adjustments.get("ce"),
                        run_id, risk_ref,
                    ),
                )
                return cur.rowcount > 0
    return _run(_do, default=False)


def save_scenario_analyses(run_id: int, scenarios: dict) -> None:
    if not scenarios:
        return
    def _do():
        rows = []
        for name, s in scenarios.items():
            if not isinstance(s, dict):
                continue
            pct = s.get("revenue_change_pct")
            if pct is None:
                try:
                    pct = float(str(s.get("revenue_change", "0")).replace("%", ""))
                except (ValueError, TypeError):
                    pct = None
            rows.append((
                run_id, name, pct,
                s.get("projected_revenue"),
                s.get("gross_margin_impact_bps"),
                s.get("projected_gross_margin"),
                s.get("indicative_net_income"),
                s.get("narrative"),
            ))
        if not rows:
            return
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO scenario_analyses
                        (run_id, scenario, revenue_change_pct, projected_revenue,
                         gross_margin_impact_bps, projected_gross_margin,
                         indicative_net_income, narrative)
                    VALUES %s
                    """,
                    rows,
                )
    _run(_do)


def get_company_id_for_run(run_id: int) -> Optional[int]:
    """company_id for a risk_loop_runs row — needed by embedding call sites
    (CEM events, etc.) that only receive a run_id from the frontend."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT company_id FROM risk_loop_runs WHERE id = %s", (run_id,))
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def get_scenario_rows_for_embedding(run_id: int) -> list:
    """scenario_analyses id + narrative for a run, for EMBT_SCENARIO embeddings.
    Same lookup-after-insert pattern as save_scenario_risk_impacts, since the
    bulk insert above has no RETURNING clause."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, scenario, narrative FROM scenario_analyses WHERE run_id = %s",
                    (run_id,),
                )
                return [{"pk_id": r[0], "scenario": r[1], "narrative": r[2]} for r in cur.fetchall()]
    return _run(_do) or []


def save_grey_swan(run_id: int, grey_swan: dict) -> None:
    if not grey_swan or grey_swan.get("error"):
        return
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO grey_swan_models
                        (run_id, trigger_risk, trigger_category, trigger_base_score,
                         trigger_velocity, quarterly_revenue_proxy, peak_score, peak_rag, timeline)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        run_id,
                        grey_swan.get("trigger_risk"),
                        grey_swan.get("trigger_category"),
                        grey_swan.get("trigger_base_score"),
                        grey_swan.get("trigger_velocity"),
                        grey_swan.get("quarterly_revenue_proxy"),
                        grey_swan.get("peak_score"),
                        grey_swan.get("peak_rag"),
                        Json(grey_swan.get("timeline") or []),
                    ),
                )
    _run(_do)


def save_forecasts(run_id: int, metric: str, forecast_data: dict, on_error=None) -> None:
    if not forecast_data or forecast_data.get("note") or forecast_data.get("error"):
        return
    def _do():
        rows = []
        for f in forecast_data.get("forecasts", []):
            horizon = f.get("horizon", 0)
            rows.append((run_id, metric, "Ensemble", horizon,
                         f.get("point"), f.get("ci_lower"), f.get("ci_upper"), None))
            for model_name, mf in (f.get("per_model") or {}).items():
                # per_model values are usually a bare point forecast (a number);
                # accept a {point, ci_lower, ci_upper, sigma} dict too in case a
                # future producer supplies per-model confidence intervals.
                if isinstance(mf, dict):
                    rows.append((run_id, metric, model_name, horizon,
                                 mf.get("point"), mf.get("ci_lower"), mf.get("ci_upper"), mf.get("sigma")))
                else:
                    rows.append((run_id, metric, model_name, horizon, mf, None, None, None))
        if not rows:
            return
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO forecasts
                        (run_id, metric, model, horizon_quarter,
                         point_forecast, ci_lower, ci_upper, sigma)
                    VALUES %s
                    ON CONFLICT (run_id, metric, model, horizon_quarter) DO NOTHING
                    """,
                    rows,
                )
    _run(_do, on_error=on_error)


def save_analyst_kpi_series(run_id: int, analyst_series: dict) -> None:
    """Persist raw quarterly series for analyst KPIs (EPS, OpMargin, NetIncome, FCF, EBITDA)."""
    if not analyst_series:
        return
    rows = []
    for metric_name, series in analyst_series.items():
        if not isinstance(series, list):
            continue
        for p in series:
            qe  = p.get("quarter_end")
            val = p.get("value")
            if qe and val is not None:
                rows.append((run_id, metric_name, qe, val))
    if not rows:
        return
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO analyst_kpi_series (run_id, metric_name, quarter_end, value)
                    VALUES %s
                    ON CONFLICT (run_id, metric_name, quarter_end) DO NOTHING
                    """,
                    rows,
                )
    _run(_do)


def save_backtest_metrics(run_id: int, backtest_data: dict, on_error=None) -> None:
    if not backtest_data:
        return
    def _do():
        model_metrics = backtest_data.get("model_metrics", {})
        calibrated_weights = backtest_data.get("calibrated_weights", {})
        n_steps = backtest_data.get("n_backtest_steps")
        rows = [
            (
                run_id, None, model,
                m.get("n_observations") or m.get("n_obs"),
                n_steps,
                m.get("mape"),
                m.get("rmse"),
                m.get("r2") or m.get("r_squared"),
                m.get("precision"),
                m.get("recall"),
                m.get("f1"),
                calibrated_weights.get(model),
            )
            for model, m in model_metrics.items()
        ]
        if not rows:
            return
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO backtest_metrics
                        (run_id, metric, model, n_observations, n_backtest_steps,
                         mape, rmse, r_squared, precision_score, recall_score, f1_score, calibrated_weight)
                    VALUES %s
                    """,
                    rows,
                )
    _run(_do, on_error=on_error)


def save_qoq_momentum(run_id: int, qoq_data: dict) -> None:
    if not qoq_data or qoq_data.get("error") or qoq_data.get("note"):
        return
    def _do():
        trend = qoq_data.get("trend", "STABLE")
        rows = [
            (run_id, p.get("quarter_end"), p.get("score"), trend)
            for p in qoq_data.get("momentum_series", [])
            if p.get("quarter_end")
        ]
        if not rows:
            return
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    "INSERT INTO qoq_revenue_momentum (run_id, quarter_end, qoq_pct, sentiment) VALUES %s",
                    rows,
                )
    _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# RSS
# ─────────────────────────────────────────────────────────────────────────────

def save_rss_signals(run_id: int, rss_result: dict) -> None:
    """Save graded RSS signals from predictive_analytics_tool output."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                for feed in rss_result.get("feed_results", []):
                    feed_name = feed.get("feed", "")
                    domains = feed.get("domains", [])
                    domain_str = domains[0] if domains else None
                    for sig in feed.get("signals", []):
                        title = (sig.get("title") or "")[:500]
                        if not title:
                            continue
                        cur.execute(
                            """
                            INSERT INTO rss_articles (feed_name, feed_url, title, industry_category)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (title, feed_name) DO NOTHING
                            RETURNING id
                            """,
                            (feed_name, feed.get("url"), title, domain_str),
                        )
                        row = cur.fetchone()
                        if not row:
                            cur.execute(
                                "SELECT id FROM rss_articles WHERE title = %s AND feed_name = %s",
                                (title, feed_name),
                            )
                            row = cur.fetchone()
                        article_id = row[0] if row else None
                        cur.execute(
                            """
                            INSERT INTO rss_signals
                                (run_id, article_id, domain, relevance_score, severity_score, velocity_delta)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (run_id, article_id,
                             sig.get("top_domain") or domain_str,
                             sig.get("relevance_score"), sig.get("severity_score"),
                             sig.get("velocity")),
                        )
    _run(_do)


def save_rss_articles_full(company_id: Optional[int], articles_result: dict) -> list:
    """Save full RSS articles from rss_tool.py output (includes URLs, authors).

    Returns the newly-inserted rows as [{id, title, summary}, ...] — rows that
    already existed (ON CONFLICT DO NOTHING) are excluded, so the caller can
    embed (EMBT_ARTICLE) only genuinely new content instead of re-embedding
    the same article on every ingest.
    """
    def _do():
        feeds = articles_result.get("feeds", articles_result.get("feed_results", []))
        new_rows: list = []
        with _conn() as conn:
            with conn.cursor() as cur:
                for feed in feeds:
                    feed_name = feed.get("feed") or feed.get("name", "")
                    for art in feed.get("articles", feed.get("signals", [])):
                        title = (art.get("title") or "")[:500]
                        if not title:
                            continue
                        summary = (art.get("summary") or "")[:2000] or None
                        cur.execute(
                            """
                            INSERT INTO rss_articles
                                (company_id, feed_name, feed_url, industry_category,
                                 title, article_url, published_at, summary)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (title, feed_name) DO NOTHING
                            RETURNING id
                            """,
                            (
                                company_id,
                                feed_name,
                                feed.get("url"),
                                feed.get("industry") or feed.get("category"),
                                title,
                                art.get("url") or art.get("link"),
                                art.get("published") or art.get("date"),
                                summary,
                            ),
                        )
                        row = cur.fetchone()
                        if row:
                            new_rows.append({"id": row[0], "title": title, "summary": summary})
        return new_rows
    return _run(_do) or []


# ─────────────────────────────────────────────────────────────────────────────
# HITL decisions
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_hitl_session(conn, run_id: int, persona: Optional[str] = None) -> int:
    """Get or create a hitl_sessions row within an existing connection."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM hitl_sessions WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO hitl_sessions (run_id, persona) VALUES (%s, %s) RETURNING id",
            (run_id, persona),
        )
        return cur.fetchone()[0]


def save_risk_approvals(run_id: int, approvals: dict, persona: Optional[str] = None) -> None:
    """Persist Gate 1 per-risk decisions from the frontend."""
    if not approvals:
        return
    def _do():
        with _conn() as conn:
            session_id = _ensure_hitl_session(conn, run_id, persona)
            statuses = [v.get("status", "pending") for v in approvals.values()]
            gate1 = "approved" if all(s in ("approved", "adjusted", "signed") for s in statuses) else "partial"
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE hitl_sessions SET gate1_status = %s WHERE id = %s",
                    (gate1, session_id),
                )
                for risk_ref, approval in approvals.items():
                    adj = approval.get("adjustments") or {}
                    adj_at = approval.get("adjustedAt")
                    cur.execute(
                        """
                        INSERT INTO risk_approvals
                            (session_id, risk_ref, risk_name, status,
                             adjusted_rag, adjusted_score, adjusted_velocity, adjusted_ce,
                             rationale, adjusted_by, adjusted_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                CASE WHEN %s IS NOT NULL
                                     THEN to_timestamp(%s / 1000.0) ELSE NULL END)
                        RETURNING id
                        """,
                        (
                            session_id, risk_ref, approval.get("risk_name"),
                            approval.get("status", "pending"),
                            adj.get("rag"), adj.get("score"), adj.get("velocity"), adj.get("ce"),
                            approval.get("rationale"), approval.get("adjustedBy"),
                            adj_at, adj_at,
                        ),
                    )
                    approval_id = cur.fetchone()[0]
                    for role, sig in (approval.get("signoffs") or {}).items():
                        sig_at = sig.get("signedAt")
                        cur.execute(
                            """
                            INSERT INTO risk_approval_signoffs (approval_id, role, signatory, signed_at)
                            VALUES (%s, %s, %s,
                                    CASE WHEN %s IS NOT NULL
                                         THEN to_timestamp(%s / 1000.0) ELSE NOW() END)
                            """,
                            (approval_id, role, sig.get("who"), sig_at, sig_at),
                        )
    _run(_do)


def save_objective_approvals(run_id: int, approvals: dict, persona: Optional[str] = None) -> None:
    """Persist Gate 2 per-objective decisions from the frontend."""
    if not approvals:
        return
    def _do():
        with _conn() as conn:
            session_id = _ensure_hitl_session(conn, run_id, persona)
            statuses = [v.get("status", "pending") for v in approvals.values()]
            gate2 = "approved" if all(s in ("approved", "adjusted", "signed") for s in statuses) else "partial"
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE hitl_sessions SET gate2_status = %s, completed_at = NOW() WHERE id = %s",
                    (gate2, session_id),
                )
                for obj_id, approval in approvals.items():
                    adj = approval.get("adjustments") or {}
                    adj_at = approval.get("adjustedAt")
                    cur.execute(
                        """
                        INSERT INTO objective_approvals
                            (session_id, obj_id, objective_text, status,
                             adjusted_objective_text, adjusted_priority, adjusted_sprint, adjusted_hours,
                             adjusted_linked_risks, adjusted_controls, residual_risk_reduction,
                             rationale, adjusted_by, adjusted_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                CASE WHEN %s IS NOT NULL
                                     THEN to_timestamp(%s / 1000.0) ELSE NULL END)
                        RETURNING id
                        """,
                        (
                            session_id, obj_id, approval.get("objective_text"),
                            approval.get("status", "pending"),
                            adj.get("objective"),
                            adj.get("priority"), adj.get("sprint"), adj.get("hours"),
                            adj.get("linked_risks") or adj.get("linked_risk_ids"),
                            adj.get("controls"),
                            adj.get("residualRiskReduction") or adj.get("residual_risk_reduction"),
                            approval.get("rationale"), approval.get("adjustedBy"),
                            adj_at, adj_at,
                        ),
                    )
                    approval_id = cur.fetchone()[0]
                    for role, sig in (approval.get("signoffs") or {}).items():
                        sig_at = sig.get("signedAt")
                        cur.execute(
                            """
                            INSERT INTO objective_approval_signoffs (approval_id, role, signatory, signed_at)
                            VALUES (%s, %s, %s,
                                    CASE WHEN %s IS NOT NULL
                                         THEN to_timestamp(%s / 1000.0) ELSE NOW() END)
                            """,
                            (approval_id, role, sig.get("who"), sig_at, sig_at),
                        )
    _run(_do)


def save_sox_scope_approvals(
    run_id: int,
    materiality_approval: Optional[dict],
    account_approvals: dict,
    persona: Optional[str] = None,
) -> None:
    """Persist SOX HITL Gate S1 decisions: materiality basis + per-account scope."""
    if not materiality_approval and not account_approvals:
        return
    def _do():
        with _conn() as conn:
            session_id = _ensure_hitl_session(conn, run_id, persona)
            statuses = [materiality_approval.get("status", "pending")] if materiality_approval else []
            statuses += [v.get("status", "pending") for v in account_approvals.values()]
            gate3 = "approved" if statuses and all(s in ("approved", "adjusted", "signed") for s in statuses) else "partial"
            with conn.cursor() as cur:
                cur.execute("UPDATE hitl_sessions SET gate3_status = %s WHERE id = %s", (gate3, session_id))

                if materiality_approval:
                    adj = materiality_approval.get("adjustments") or {}
                    adj_at = materiality_approval.get("adjustedAt")
                    cur.execute(
                        """
                        INSERT INTO sox_materiality_approvals
                            (session_id, status, adjusted_materiality_pct, adjusted_performance_pct,
                             rationale, adjusted_by, adjusted_at)
                        VALUES (%s,%s,%s,%s,%s,%s,
                                CASE WHEN %s IS NOT NULL
                                     THEN to_timestamp(%s / 1000.0) ELSE NULL END)
                        RETURNING id
                        """,
                        (
                            session_id, materiality_approval.get("status", "pending"),
                            adj.get("materiality_pct"), adj.get("performance_mat_pct"),
                            materiality_approval.get("rationale"), materiality_approval.get("adjustedBy"),
                            adj_at, adj_at,
                        ),
                    )
                    approval_id = cur.fetchone()[0]
                    for role, sig in (materiality_approval.get("signoffs") or {}).items():
                        sig_at = sig.get("signedAt")
                        cur.execute(
                            """
                            INSERT INTO sox_materiality_approval_signoffs (approval_id, role, signatory, signed_at)
                            VALUES (%s, %s, %s,
                                    CASE WHEN %s IS NOT NULL
                                         THEN to_timestamp(%s / 1000.0) ELSE NOW() END)
                            """,
                            (approval_id, role, sig.get("who"), sig_at, sig_at),
                        )

                for account_id, approval in account_approvals.items():
                    adj = approval.get("adjustments") or {}
                    adj_at = approval.get("adjustedAt")
                    cur.execute(
                        """
                        INSERT INTO sox_account_approvals
                            (session_id, account_id, account_name, status,
                             adjusted_in_scope, adjusted_priority, rationale, adjusted_by, adjusted_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
                                CASE WHEN %s IS NOT NULL
                                     THEN to_timestamp(%s / 1000.0) ELSE NULL END)
                        RETURNING id
                        """,
                        (
                            session_id, account_id, approval.get("account_name"),
                            approval.get("status", "pending"),
                            adj.get("in_scope"), adj.get("priority"),
                            approval.get("rationale"), approval.get("adjustedBy"),
                            adj_at, adj_at,
                        ),
                    )
                    approval_id = cur.fetchone()[0]
                    for role, sig in (approval.get("signoffs") or {}).items():
                        sig_at = sig.get("signedAt")
                        cur.execute(
                            """
                            INSERT INTO sox_account_approval_signoffs (approval_id, role, signatory, signed_at)
                            VALUES (%s, %s, %s,
                                    CASE WHEN %s IS NOT NULL
                                         THEN to_timestamp(%s / 1000.0) ELSE NOW() END)
                            """,
                            (approval_id, role, sig.get("who"), sig_at, sig_at),
                        )
    _run(_do)


def save_sox_coverage_approvals(
    run_id: int,
    process_approvals: dict,
    persona: Optional[str] = None,
) -> None:
    """Persist SOX HITL Gate S2 decisions: per-process coverage level."""
    if not process_approvals:
        return
    def _do():
        with _conn() as conn:
            session_id = _ensure_hitl_session(conn, run_id, persona)
            statuses = [v.get("status", "pending") for v in process_approvals.values()]
            gate4 = "approved" if all(s in ("approved", "adjusted", "signed") for s in statuses) else "partial"
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE hitl_sessions SET gate4_status = %s, completed_at = NOW() WHERE id = %s",
                    (gate4, session_id),
                )
                for process_id, approval in process_approvals.items():
                    adj = approval.get("adjustments") or {}
                    adj_at = approval.get("adjustedAt")
                    cur.execute(
                        """
                        INSERT INTO sox_process_approvals
                            (session_id, process_id, process_name, status,
                             adjusted_coverage_level, rationale, adjusted_by, adjusted_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,
                                CASE WHEN %s IS NOT NULL
                                     THEN to_timestamp(%s / 1000.0) ELSE NULL END)
                        RETURNING id
                        """,
                        (
                            session_id, process_id, approval.get("process_name"),
                            approval.get("status", "pending"),
                            adj.get("coverage_level"),
                            approval.get("rationale"), approval.get("adjustedBy"),
                            adj_at, adj_at,
                        ),
                    )
                    approval_id = cur.fetchone()[0]
                    for role, sig in (approval.get("signoffs") or {}).items():
                        sig_at = sig.get("signedAt")
                        cur.execute(
                            """
                            INSERT INTO sox_process_approval_signoffs (approval_id, role, signatory, signed_at)
                            VALUES (%s, %s, %s,
                                    CASE WHEN %s IS NOT NULL
                                         THEN to_timestamp(%s / 1000.0) ELSE NOW() END)
                            """,
                            (approval_id, role, sig.get("who"), sig_at, sig_at),
                        )
    _run(_do)


def get_sox_hitl_gate_status(run_id: int) -> dict:
    """Retrieve SOX Gate S1 (gate3) / Gate S2 (gate4) status for a run, if any."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT gate3_status, gate4_status FROM hitl_sessions WHERE run_id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
                return {"gate3_status": row[0], "gate4_status": row[1]} if row else {}
    return _run(_do) or {}


# ─────────────────────────────────────────────────────────────────────────────
# Approval workflow (real 2-stage preparer -> manager review, replacing the
# fixed CAE/CFO/Audit-Committee signoff chain above)
# ─────────────────────────────────────────────────────────────────────────────

def upsert_approval_task(
    run_id: Optional[int],
    gate_type: str,
    item_ref: str,
    item_label: Optional[str],
    disposition: str,          # 'approved' | 'adjusted'
    adjustments: Optional[dict],
    rationale: Optional[str],
    prepared_by: int,
    prepared_by_name: str,
    manager_id: Optional[int] = None,
    manager_name: Optional[str] = None,
    ai_suggested: Optional[dict] = None,
) -> Optional[dict]:
    """
    Record a preparer's disposition on a HITL gate item.

    'approved' (accepted as computed, nothing changed) finalises immediately —
    there is nothing for a second person to check. 'adjusted' (a real override)
    routes to the preparer's manager for review; if the preparer has no
    manager configured it auto-approves with a note, so the workflow still
    functions before an org chart / manager assignments exist.

    ai_suggested: when the preparer used "Suggest with AI", the suggested
    field values keyed the same as `adjustments` (e.g. {"rag": "A", "score": 8}).
    ai_accepted is derived here — True if the final adjustments match the
    suggestion on every key the AI proposed, False if any differ, None if no
    AI suggestion was involved. This is the raw data for measuring how often
    preparers accept vs. override each AI-assisted gate over time.
    """
    if disposition == "adjusted" and manager_id:
        status, review_comment = "submitted", None
    elif disposition == "adjusted":
        status, review_comment = "approved", "Auto-approved — preparer has no manager configured"
    else:
        status, review_comment = "approved", None

    ai_accepted = None
    if ai_suggested:
        adjustments = adjustments or {}
        ai_accepted = all(adjustments.get(k) == v for k, v in ai_suggested.items())

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO approval_tasks
                        (run_id, gate_type, item_ref, item_label, status, disposition,
                         adjustments, rationale, prepared_by, prepared_by_name, prepared_at,
                         manager_id, manager_name, review_comment, ai_suggested, ai_accepted, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (run_id, gate_type, item_ref) DO UPDATE SET
                        item_label       = EXCLUDED.item_label,
                        status           = EXCLUDED.status,
                        disposition      = EXCLUDED.disposition,
                        adjustments      = EXCLUDED.adjustments,
                        rationale        = EXCLUDED.rationale,
                        prepared_by      = EXCLUDED.prepared_by,
                        prepared_by_name = EXCLUDED.prepared_by_name,
                        prepared_at      = NOW(),
                        manager_id       = EXCLUDED.manager_id,
                        manager_name     = EXCLUDED.manager_name,
                        reviewed_by      = NULL,
                        reviewed_by_name = NULL,
                        reviewed_at      = NULL,
                        review_comment   = EXCLUDED.review_comment,
                        ai_suggested     = EXCLUDED.ai_suggested,
                        ai_accepted      = EXCLUDED.ai_accepted,
                        updated_at       = NOW()
                    RETURNING id, run_id, gate_type, item_ref, item_label, status, disposition,
                              adjustments, rationale, prepared_by, prepared_by_name, prepared_at,
                              manager_id, manager_name, reviewed_by, reviewed_by_name, reviewed_at,
                              review_comment, ai_suggested, ai_accepted
                    """,
                    (
                        run_id, gate_type, item_ref, item_label, status, disposition,
                        Json(adjustments) if adjustments is not None else None,
                        rationale, prepared_by, prepared_by_name,
                        manager_id, manager_name, review_comment,
                        Json(ai_suggested) if ai_suggested is not None else None,
                        ai_accepted,
                    ),
                )
                row = cur.fetchone()
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row)) if row else None
    return _run(_do)


def get_ai_acceptance_stats(gate_type: Optional[str] = None) -> list:
    """Aggregate AI-suggestion acceptance rate per gate_type — the measurable
    trail for 'did preparers keep what the AI suggested or override it'."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT gate_type,
                           COUNT(*) FILTER (WHERE ai_suggested IS NOT NULL) AS ai_assisted_count,
                           COUNT(*) FILTER (WHERE ai_accepted = TRUE)       AS accepted_count,
                           COUNT(*) FILTER (WHERE ai_accepted = FALSE)      AS overridden_count
                    FROM approval_tasks
                    WHERE ai_suggested IS NOT NULL
                      AND (%s IS NULL OR gate_type = %s)
                    GROUP BY gate_type
                    ORDER BY gate_type
                    """,
                    (gate_type, gate_type),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    return _run(_do) or []


def get_ai_acceptance_stats_by_category() -> list:
    """
    Same acceptance-rate signal as get_ai_acceptance_stats, but broken down
    by risk category — the fairness/bias breakdown MODEL_CARD.md flagged as
    missing: does the AI's advice get overridden more often for certain risk
    categories? Only 'risk' gate_type rows carry a category (item_ref is a
    risk_ref that joins to risk_scores); other gate types are excluded, not
    silently miscounted.
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rs.category,
                           COUNT(*) FILTER (WHERE t.ai_suggested IS NOT NULL) AS ai_assisted_count,
                           COUNT(*) FILTER (WHERE t.ai_accepted = TRUE)       AS accepted_count,
                           COUNT(*) FILTER (WHERE t.ai_accepted = FALSE)      AS overridden_count
                    FROM approval_tasks t
                    JOIN risk_scores rs ON rs.run_id = t.run_id AND rs.risk_ref = t.item_ref
                    WHERE t.gate_type = 'risk' AND t.ai_suggested IS NOT NULL
                    GROUP BY rs.category
                    ORDER BY rs.category
                    """
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    return _run(_do) or []


def get_ai_acceptance_stats_by_industry() -> list:
    """
    Same signal, broken down by the run's industry (all gate types — every
    approval_tasks row has a run_id, so this doesn't need the category join's
    gate_type restriction). Answers the other half of MODEL_CARD.md's fairness
    gap: is the AI's advice systematically overridden more for some industries?
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.industry,
                           COUNT(*) FILTER (WHERE t.ai_suggested IS NOT NULL) AS ai_assisted_count,
                           COUNT(*) FILTER (WHERE t.ai_accepted = TRUE)       AS accepted_count,
                           COUNT(*) FILTER (WHERE t.ai_accepted = FALSE)      AS overridden_count
                    FROM approval_tasks t
                    JOIN risk_loop_runs r ON r.id = t.run_id
                    WHERE t.ai_suggested IS NOT NULL
                    GROUP BY r.industry
                    ORDER BY r.industry
                    """
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    return _run(_do) or []


def get_ai_acceptance_history() -> list:
    """
    Individual AI-suggestion accept/override outcomes, oldest-first — the raw
    events behind get_ai_acceptance_stats' aggregates, needed for
    drift_tool.compute_ai_acceptance_drift's baseline-vs-current PSI split
    (MODEL_CARD.md "Recommended Next Steps" #2).
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT gate_type, ai_accepted,
                           COALESCE(reviewed_at, prepared_at, created_at) AS event_at
                    FROM approval_tasks
                    WHERE ai_suggested IS NOT NULL AND ai_accepted IS NOT NULL
                    ORDER BY COALESCE(reviewed_at, prepared_at, created_at) ASC
                    """
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                for r in rows:
                    r["event_at"] = r["event_at"].isoformat() if r["event_at"] else None
                return rows
    return _run(_do) or []


def review_approval_task(task_id: int, reviewer_id: int, reviewer_name: str, decision: str, comment: Optional[str]) -> Optional[dict]:
    """
    Manager decision on a submitted item ('approved' or 'rejected').
    Caller (API layer) must verify reviewer_id matches the task's manager_id
    before calling this — it only re-checks status='submitted' to avoid a
    double-decision race, not identity.
    """
    status = "manager_approved" if decision == "approved" else "rejected"
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE approval_tasks SET
                        status = %s, reviewed_by = %s, reviewed_by_name = %s,
                        reviewed_at = NOW(), review_comment = %s, updated_at = NOW()
                    WHERE id = %s AND status = 'submitted'
                    RETURNING id, run_id, gate_type, item_ref, status, disposition, adjustments
                    """,
                    (status, reviewer_id, reviewer_name, comment, task_id),
                )
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
                return dict(zip(cols, row)) if row else None
    return _run(_do)


def get_approval_task(task_id: int) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, run_id, gate_type, item_ref, item_label, manager_id, status,
                           disposition, adjustments, rationale, prepared_by_name, execution_result
                    FROM approval_tasks WHERE id = %s
                    """,
                    (task_id,),
                )
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
                return dict(zip(cols, row)) if row else None
    return _run(_do)


def get_approval_inbox(manager_id: int) -> list:
    """Items awaiting this user's review, newest-submitted first.

    LEFT JOIN (not INNER) — devops_scm_exception tasks have run_id=NULL (no
    risk_loop_runs association; see approval_tasks.run_id's DROP NOT NULL
    migration above), and an INNER JOIN would silently drop them from every
    manager's inbox instead of just leaving ticker NULL for those rows."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.id, t.run_id, t.gate_type, t.item_ref, t.item_label, t.disposition,
                           t.adjustments, t.rationale, t.prepared_by_name, t.prepared_at, r.ticker,
                           t.ai_suggested, t.ai_accepted
                    FROM approval_tasks t
                    LEFT JOIN risk_loop_runs r ON r.id = t.run_id
                    WHERE t.manager_id = %s AND t.status = 'submitted'
                    ORDER BY t.prepared_at DESC
                    """,
                    (manager_id,),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("prepared_at") and hasattr(d["prepared_at"], "isoformat"):
                        d["prepared_at"] = d["prepared_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def get_approval_tasks_for_run(run_id: int, gate_type: Optional[str] = None) -> list:
    """All approval tasks for a run (optionally filtered to one gate type) —
    used to restore Gate 1/2/S1/S2 UI state on reload."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                q = ("SELECT id, gate_type, item_ref, item_label, status, disposition, adjustments, "
                     "rationale, prepared_by, prepared_by_name, prepared_at, manager_id, manager_name, "
                     "reviewed_by, reviewed_by_name, reviewed_at, review_comment "
                     "FROM approval_tasks WHERE run_id = %s")
                params = [run_id]
                if gate_type:
                    q += " AND gate_type = %s"
                    params.append(gate_type)
                cur.execute(q, params)
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    for tf in ("prepared_at", "reviewed_at"):
                        if d.get(tf) and hasattr(d[tf], "isoformat"):
                            d[tf] = d[tf].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


# ── Closed-loop remediation (remediation_github / remediation_github_pr gate types) ──

def set_approval_task_execution_result(task_id: int, result: dict) -> None:
    """Persist the outcome of the actual external write (github_write_tool
    call) fired once a remediation_github or remediation_github_pr task is
    approved. `result` is either {"number","url",...} on success or
    {"error": "..."} on failure — written verbatim, same shape
    github_write_tool.create_issue/create_pull_request both return."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE approval_tasks SET execution_result = %s, updated_at = NOW() WHERE id = %s",
                    (Json(result), task_id),
                )
    _run(_do)


def list_remediation_tasks(limit: int = 50) -> list:
    """Recent closed-loop remediation tasks regardless of status, newest
    first, covering both remediation_github (issue) and remediation_github_pr
    (real file-change PR) gate types — unlike get_approval_inbox (which only
    shows 'submitted', awaiting-review items), this is how the Approval Inbox
    shows what happened AFTER a decision: the created issue/PR link, or a
    failure to retry. gate_type is selected so the frontend can distinguish
    "Issue #N opened" from "PR #N opened"."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, gate_type, item_ref, item_label, status, adjustments, rationale,
                           prepared_by_name, prepared_at, manager_name, reviewed_by_name,
                           reviewed_at, review_comment, execution_result, updated_at
                    FROM approval_tasks WHERE gate_type IN ('remediation_github', 'remediation_github_pr')
                    ORDER BY updated_at DESC LIMIT %s
                    """,
                    (limit,),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    for tf in ("prepared_at", "reviewed_at", "updated_at"):
                        if d.get(tf) and hasattr(d[tf], "isoformat"):
                            d[tf] = d[tf].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def get_exception_event_by_id(event_id: int) -> Optional[dict]:
    """One exception_control_events row (either Exception Management's dev-only
    ML-flagged events or JE Testing's deterministic findings — same table,
    see je_testing_endpoints.py's module docstring) — the source-of-truth a
    remediation proposal is drafted from."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, control_id, system_source, process, event_timestamp,
                           actor, action, event_type, raw_payload
                    FROM exception_control_events WHERE id = %s
                    """,
                    (event_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                d = dict(zip(cols, row))
                if d.get("event_timestamp") and hasattr(d["event_timestamp"], "isoformat"):
                    d["event_timestamp"] = d["event_timestamp"].isoformat()
                d["raw_payload"] = d.get("raw_payload") or {}
                return d
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# Audit plan
# ─────────────────────────────────────────────────────────────────────────────

def save_audit_objectives(run_id: int, objectives: list) -> None:
    if not objectives:
        return
    def _do():
        rows = []
        for o in objectives:
            linked_risks = o.get("linked_risks") or ([o["linked_risk"]] if o.get("linked_risk") else [])
            linked_risk_ref = o.get("linked_risk") or o.get("linked_risk_ref") or (linked_risks[0] if linked_risks else None)
            rows.append((
                run_id,
                o.get("id") or o.get("obj_id", ""),
                o.get("objective") or o.get("objective_text", ""),
                o.get("priority"),
                linked_risk_ref,
                linked_risks,
                o.get("controls") or [],
                o.get("hours"),
                o.get("sprint"),
                o.get("residualRiskReduction") or o.get("residual_risk_reduction"),
            ))
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO audit_objectives
                        (run_id, obj_id, objective_text, priority, linked_risk_ref,
                         linked_risks, controls, hours, sprint, residual_risk_reduction)
                    VALUES %s
                    ON CONFLICT (run_id, obj_id) DO NOTHING
                    """,
                    rows,
                )
    _run(_do)


def get_latest_audit_objectives(ticker: str) -> Optional[dict]:
    """Most recently completed run's saved audit objectives for a ticker.

    Used by the Audit Scope screen to show real prior-run data (instead of
    the generic industry-template mock) before Assess Enterprise Risk has
    been run in the current session.
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, run_at FROM risk_loop_runs "
                    "WHERE ticker = %s AND completed = TRUE "
                    "ORDER BY run_at DESC LIMIT 1",
                    (ticker.upper(),),
                )
                run_row = cur.fetchone()
                if not run_row:
                    return None
                run_id, run_at = run_row
                cur.execute(
                    "SELECT obj_id, objective_text, priority, linked_risk_ref, linked_risks, "
                    "controls, hours, sprint, residual_risk_reduction "
                    "FROM audit_objectives WHERE run_id = %s ORDER BY obj_id",
                    (run_id,),
                )
                objectives = [
                    {
                        "id": r[0], "objective": r[1], "priority": r[2],
                        "linked_risk": r[3], "linked_risks": r[4] or ([r[3]] if r[3] else []),
                        "controls": r[5] or [], "hours": r[6], "sprint": r[7],
                        "residualRiskReduction": float(r[8]) if r[8] is not None else None,
                    }
                    for r in cur.fetchall()
                ]
                if not objectives:
                    return None
                return {"run_id": run_id, "run_at": run_at.isoformat() if run_at else None, "objectives": objectives}
    return _run(_do)


def get_audit_objectives_for_run(run_id: int) -> list:
    """Audit objectives for a specific run_id directly, rather than
    get_latest_audit_objectives()'s "most recently completed run for
    ticker" lookup — the Evidence Pack already has the exact run_id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT obj_id, objective_text, priority, linked_risk_ref, linked_risks, "
                    "controls, hours, sprint, residual_risk_reduction "
                    "FROM audit_objectives WHERE run_id = %s ORDER BY obj_id",
                    (run_id,),
                )
                return [
                    {
                        "id": r[0], "objective": r[1], "priority": r[2],
                        "linked_risk": r[3], "linked_risks": r[4] or ([r[3]] if r[3] else []),
                        "controls": r[5] or [], "hours": r[6], "sprint": r[7],
                        "residualRiskReduction": float(r[8]) if r[8] is not None else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def save_manual_audits(run_id: int, audits: list) -> None:
    if not audits:
        return
    def _do():
        rows = [
            (
                run_id,
                a.get("title"),
                a.get("when") or a.get("when_scheduled"),
                a.get("riskId") or a.get("linked_risk_ref"),
                a.get("addedBy") or a.get("added_by"),
            )
            for a in audits
        ]
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    "INSERT INTO manual_audit_plans (run_id, title, when_scheduled, linked_risk_ref, added_by) VALUES %s",
                    rows,
                )
    _run(_do)


def save_cem_events(run_id: int, events: list) -> list:
    """Bulk-insert CEM (Continuous Exception Monitoring) events for a run.

    Returns [{pk_id, root_cause}, ...] for rows that have a root-cause
    narrative, using execute_values(fetch=True) + RETURNING, so the caller
    can embed them (EMBT_CEM_RC) without a separate lookup query.
    """
    if not events:
        return []
    def _do():
        rows = [
            (
                run_id,
                e.get("control"),
                e.get("area"),
                e.get("risk") or e.get("risk_label"),
                e.get("severity"),
                e.get("exposure"),
                e.get("category"),
                e.get("rc") or e.get("rootCause") or e.get("root_cause_narrative"),
                e.get("exposureAmountM") if e.get("exposureAmountM") is not None else e.get("exposure_amount_m"),
                e.get("exposureSource") or e.get("exposure_source"),
            )
            for e in events
        ]
        with _conn() as conn:
            with conn.cursor() as cur:
                inserted = execute_values(
                    cur,
                    """
                    INSERT INTO cem_events
                        (run_id, control, area, risk_label, severity,
                         exposure, category, root_cause_narrative,
                         exposure_amount_m, exposure_source)
                    VALUES %s
                    RETURNING id, root_cause_narrative
                    """,
                    rows,
                    fetch=True,
                )
        return [{"pk_id": r[0], "root_cause": r[1]} for r in (inserted or []) if r[1]]
    return _run(_do) or []


def list_recent_cem_events(limit: int = 200) -> list:
    """Recently-logged CEM incidents across every run, newest first — backs
    the Risk Quantification screen's "CEM Event" resource picker so a user
    can pick a real, already-adjudicated incident by name instead of having
    to know its raw cem_events.id. Joins risk_loop_runs for the ticker each
    event belongs to, since cem_events itself has no company/ticker column."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ce.id, ce.run_id, r.ticker, ce.control, ce.area, ce.risk_label,
                           ce.severity, ce.exposure, ce.exposure_amount_m, ce.exposure_source,
                           ce.category, ce.created_at
                    FROM cem_events ce
                    JOIN risk_loop_runs r ON r.id = ce.run_id
                    ORDER BY ce.created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [
                    {
                        "id": r[0], "run_id": r[1], "ticker": r[2], "control": r[3],
                        "area": r[4], "risk_label": r[5], "severity": r[6], "exposure": r[7],
                        "exposure_amount_m": float(r[8]) if r[8] is not None else None,
                        "exposure_source": r[9], "category": r[10],
                        "created_at": r[11].isoformat() if r[11] else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do, default=[])


def save_loop_log(run_id: int, entries: list) -> None:
    if not entries:
        return
    def _do():
        rows = [
            (run_id, e.get("ts") or e.get("logged_at"), e.get("msg") or e.get("message", ""))
            for e in entries
            if e.get("msg") or e.get("message")
        ]
        if not rows:
            return
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    "INSERT INTO loop_log_entries (run_id, logged_at, message) VALUES %s",
                    rows,
                )
    _run(_do)


def get_loop_log_for_run(run_id: int) -> list:
    """Pipeline execution trail for a run — no getter existed for this
    table before the Evidence Pack needed it."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT logged_at, message FROM loop_log_entries "
                    "WHERE run_id = %s ORDER BY logged_at",
                    (run_id,),
                )
                return [{"logged_at": r[0].isoformat() if r[0] else None, "message": r[1]}
                        for r in cur.fetchall()]
    return _run(_do) or []


# ─────────────────────────────────────────────────────────────────────────────
# Token usage
# ─────────────────────────────────────────────────────────────────────────────

def upsert_token_session(session_name: str) -> Optional[int]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO token_usage_sessions (session_name)
                    VALUES (%s)
                    ON CONFLICT (session_name) DO UPDATE SET updated_at = NOW()
                    RETURNING id
                    """,
                    (session_name,),
                )
                return cur.fetchone()[0]
    return _run(_do)


def save_token_call(session_id: int, call: dict, session_totals: dict) -> None:
    def _do():
        t = session_totals
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO token_usage_calls
                        (session_id, called_at, model, label, input_tokens, output_tokens,
                         cache_read_tokens, cache_write_tokens, cost_usd, user_id, username)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        session_id, call.get("timestamp"), call.get("model"),
                        call.get("label"), call.get("input_tokens"), call.get("output_tokens"),
                        call.get("cache_read_tokens"), call.get("cache_write_tokens"),
                        call.get("cost_usd"), call.get("user_id"), call.get("username"),
                    ),
                )
                cur.execute(
                    """
                    UPDATE token_usage_sessions SET
                        updated_at               = NOW(),
                        total_calls              = %s,
                        total_input_tokens       = %s,
                        total_output_tokens      = %s,
                        total_cache_read_tokens  = %s,
                        total_cache_write_tokens = %s,
                        total_cost_usd           = %s
                    WHERE id = %s
                    """,
                    (
                        t.get("calls", 0), t.get("input_tokens", 0), t.get("output_tokens", 0),
                        t.get("cache_read_tokens", 0), t.get("cache_write_tokens", 0),
                        t.get("cost_usd", 0), session_id,
                    ),
                )
    _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# AI analyses (LLM outputs)
# ─────────────────────────────────────────────────────────────────────────────

def save_ai_analysis(
    kind: str,
    content: dict,
    *,
    run_id: Optional[int] = None,
    ticker: Optional[str] = None,
    subject_ref: Optional[str] = None,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    summary: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    cost_usd: Optional[float] = None,
    sampled_for_review: bool = False,
    input_hash: Optional[str] = None,
) -> Optional[int]:
    """Persist a single AI/LLM output with provenance. Returns the row id.

    sampled_for_review: set by callers for the ungated narrative endpoints
    (persona_brief, audit_report) per MODEL_CARD.md "Recommended Next Steps"
    #4 — every other kind leaves this False, since every other AI endpoint
    already has a human gate before its output takes effect.

    input_hash: set by callers that support the cost-reduction cache (see
    get_cached_ai_analysis) — a stable hash of the exact inputs that
    produced `content`, so a later call with identical inputs can be served
    from this row instead of re-calling the model.
    """
    review_status = "pending" if sampled_for_review else None
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_analyses
                        (run_id, ticker, kind, subject_ref, model, effort,
                         content, summary, input_tokens, output_tokens, cost_usd,
                         sampled_for_review, review_status, input_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        run_id, (ticker or None) and ticker.upper(), kind, subject_ref,
                        model, effort, Json(content), summary,
                        input_tokens, output_tokens, cost_usd,
                        sampled_for_review, review_status, input_hash,
                    ),
                )
                return cur.fetchone()[0]
    return _run(_do)


def get_cached_ai_analysis(kind: str, run_id: Optional[int], subject_ref: Optional[str], input_hash: str) -> Optional[dict]:
    """Look up a prior AI generation with identical inputs (see save_ai_analysis's
    input_hash param). Returns {"id", "content", "review_status", "reviewed_by_name",
    "reviewed_at"}, or None on a cache miss — callers should fall through to a
    fresh model call on None, not treat it as an error. run_id may be None (e.g.
    mock/offline mode); subject_ref scopes the lookup to e.g. a specific persona
    so different personas never collide.

    Returns the review fields alongside content (not content alone) so a caller
    re-opening a cached persona brief / audit report sees its ACTUAL current
    review status — reviewed or still pending — rather than the frontend having
    no way to tell a stale cache hit apart from a fresh, unreviewed generation."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, content, review_status, reviewed_by_name, reviewed_at
                    FROM ai_analyses
                    WHERE kind = %s AND input_hash = %s
                      AND run_id IS NOT DISTINCT FROM %s
                      AND subject_ref IS NOT DISTINCT FROM %s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (kind, input_hash, run_id, subject_ref),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0], "content": row[1], "review_status": row[2],
                    "reviewed_by_name": row[3],
                    "reviewed_at": row[4].isoformat() if row[4] else None,
                }
    return _run(_do)


def list_ai_review_queue(status: Optional[str] = "pending", limit: int = 50) -> list:
    """Every ungated-narrative generation (persona_brief/audit_report) awaiting
    (or having received) human review, newest first — not a sample."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, run_id, ticker, kind, subject_ref, model, summary,
                           review_status, reviewed_by_name, reviewed_at, review_note, created_at
                    FROM ai_analyses
                    WHERE sampled_for_review = TRUE
                      AND (%s IS NULL OR review_status = %s)
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (status, status, limit),
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                for r in rows:
                    r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
                    r["reviewed_at"] = r["reviewed_at"].isoformat() if r["reviewed_at"] else None
                return rows
    return _run(_do) or []


def mark_ai_analysis_reviewed(analysis_id: int, reviewer_id: int, reviewer_name: str,
                               note: Optional[str] = None) -> bool:
    """Record that a human reviewed an ungated-narrative generation."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ai_analyses SET
                        review_status = 'reviewed', reviewed_by = %s,
                        reviewed_by_name = %s, reviewed_at = NOW(), review_note = %s
                    WHERE id = %s AND sampled_for_review = TRUE
                    """,
                    (reviewer_id, reviewer_name, note, analysis_id),
                )
                updated = cur.rowcount > 0
            conn.commit()
            return updated
    return _run(_do) or False


def get_ai_analyses(run_id: int, kind: Optional[str] = None, limit: int = 50) -> list:
    """Recent AI analyses for a run, optionally filtered by kind."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                if kind:
                    cur.execute(
                        "SELECT id, kind, subject_ref, model, content, summary, created_at "
                        "FROM ai_analyses WHERE run_id = %s AND kind = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (run_id, kind, limit),
                    )
                else:
                    cur.execute(
                        "SELECT id, kind, subject_ref, model, content, summary, created_at "
                        "FROM ai_analyses WHERE run_id = %s ORDER BY created_at DESC LIMIT %s",
                        (run_id, limit),
                    )
                return [
                    {
                        "id": r[0], "kind": r[1], "subject_ref": r[2], "model": r[3],
                        "content": r[4], "summary": r[5],
                        "created_at": r[6].isoformat() if r[6] else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_run_token_cost(run_id: int) -> dict:
    """Aggregate token usage and estimated cost for all AI analyses in a run."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT kind, SUM(input_tokens), SUM(output_tokens), SUM(cost_usd), COUNT(*) "
                    "FROM ai_analyses WHERE run_id = %s "
                    "GROUP BY kind ORDER BY SUM(cost_usd) DESC NULLS LAST",
                    (run_id,),
                )
                rows = cur.fetchall()
                by_kind = [
                    {
                        "kind": r[0],
                        "input_tokens": int(r[1] or 0),
                        "output_tokens": int(r[2] or 0),
                        "cost_usd": float(r[3] or 0),
                        "calls": int(r[4] or 0),
                    }
                    for r in rows
                ]
                total_cost = sum(k["cost_usd"] for k in by_kind)
                total_in   = sum(k["input_tokens"] for k in by_kind)
                total_out  = sum(k["output_tokens"] for k in by_kind)
                return {
                    "run_id": run_id,
                    "total_cost_usd": total_cost,
                    "total_input_tokens": total_in,
                    "total_output_tokens": total_out,
                    "by_kind": by_kind,
                }
    return _run(_do) or {
        "run_id": run_id, "total_cost_usd": 0.0,
        "total_input_tokens": 0, "total_output_tokens": 0, "by_kind": [],
    }


def get_token_usage_summary(days: int = 30) -> dict:
    """
    Rolling-window token usage grouped by (user, label/feature, model), for
    the Token Usage screen. Returns raw grouped rows plus overall totals —
    the frontend re-aggregates by user or by label from the same row set
    rather than issuing separate queries for each cut.
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, username, label, model,
                           SUM(input_tokens), SUM(output_tokens), SUM(cost_usd), COUNT(*)
                    FROM token_usage_calls
                    WHERE called_at >= NOW() - (%s || ' days')::INTERVAL
                    GROUP BY user_id, username, label, model
                    ORDER BY SUM(cost_usd) DESC NULLS LAST
                    """,
                    (days,),
                )
                rows = [
                    {
                        "user_id": r[0],
                        "username": r[1] or "Unknown",
                        "label": r[2] or "unlabeled",
                        "model": r[3],
                        "input_tokens": int(r[4] or 0),
                        "output_tokens": int(r[5] or 0),
                        "cost_usd": float(r[6] or 0),
                        "calls": int(r[7] or 0),
                    }
                    for r in cur.fetchall()
                ]
                totals = {
                    "input_tokens":  sum(r["input_tokens"] for r in rows),
                    "output_tokens": sum(r["output_tokens"] for r in rows),
                    "cost_usd":      sum(r["cost_usd"] for r in rows),
                    "calls":         sum(r["calls"] for r in rows),
                }
                return {"days": days, "rows": rows, "totals": totals}
    return _run(_do) or {"days": days, "rows": [], "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}}


def get_token_usage_time_summary() -> dict:
    """
    Calendar-period rollups (all-time, independent of get_token_usage_summary's
    rolling window) for the Token Usage screen: by-month / month-to-date /
    by-year / year-to-date, in both tokens and USD. cost_usd is summed as
    stored — it was computed at recording time against the pricing catalog
    in effect then, so this reflects cost at the time incurred, not today's
    pricing.
    """
    def _row(r) -> dict:
        return {
            "calls":          int(r[1] or 0),
            "input_tokens":   int(r[2] or 0),
            "output_tokens":  int(r[3] or 0),
            "cost_usd":       float(r[4] or 0),
        }

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT date_trunc('month', called_at) AS bucket,
                           COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd)
                    FROM token_usage_calls
                    WHERE called_at >= NOW() - INTERVAL '24 months'
                    GROUP BY bucket ORDER BY bucket DESC
                    """
                )
                by_month = [{"month": r[0].strftime("%Y-%m"), **_row(r)} for r in cur.fetchall()]

                cur.execute(
                    """
                    SELECT NULL, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd)
                    FROM token_usage_calls WHERE called_at >= date_trunc('month', NOW())
                    """
                )
                month_to_date = _row(cur.fetchone())

                cur.execute(
                    """
                    SELECT date_trunc('year', called_at) AS bucket,
                           COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd)
                    FROM token_usage_calls
                    GROUP BY bucket ORDER BY bucket DESC
                    """
                )
                by_year = [{"year": r[0].strftime("%Y"), **_row(r)} for r in cur.fetchall()]

                cur.execute(
                    """
                    SELECT NULL, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd)
                    FROM token_usage_calls WHERE called_at >= date_trunc('year', NOW())
                    """
                )
                year_to_date = _row(cur.fetchone())

                return {
                    "by_month": by_month, "month_to_date": month_to_date,
                    "by_year": by_year, "year_to_date": year_to_date,
                }
    empty = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    return _run(_do) or {"by_month": [], "month_to_date": empty, "by_year": [], "year_to_date": empty}


def get_token_usage_by_month_by_label(months: int = 12) -> list:
    """
    Cost per month, split by feature/label — the composition behind
    get_token_usage_time_summary's by_month totals (that's cost over time;
    this is cost over time by WHAT). Feeds the Token Usage screen's stacked
    cost trend. Raw grouped rows, oldest month first — the frontend pivots
    into a per-label series itself, same convention as get_token_usage_summary.
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT date_trunc('month', called_at) AS bucket, COALESCE(label, 'unlabeled'),
                           SUM(cost_usd)
                    FROM token_usage_calls
                    WHERE called_at >= NOW() - (%s || ' months')::INTERVAL
                    GROUP BY bucket, COALESCE(label, 'unlabeled')
                    ORDER BY bucket ASC
                    """,
                    (months,),
                )
                return [
                    {"month": r[0].strftime("%Y-%m"), "label": r[1], "cost_usd": float(r[2] or 0)}
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_backtest_trend(limit_runs: int = 30) -> list:
    """
    Forecast backtest accuracy across recent runs, for the Model Health
    screen's accuracy-drift chart. backtest_metrics is write-only until now
    (one row per model per run, never previously aggregated). Returns raw
    rows — the frontend groups by model itself, mirroring the pattern used
    by get_token_usage_summary.

    Note: backtest_metrics.metric is always NULL (save_backtest_metrics
    never populates it) — model is the only real grouping key.
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH recent_runs AS (
                        SELECT id, ticker, run_at FROM risk_loop_runs
                        ORDER BY run_at DESC LIMIT %s
                    )
                    SELECT b.model, r.ticker, r.run_at, b.mape, b.rmse, b.r_squared, b.calibrated_weight
                    FROM backtest_metrics b
                    JOIN recent_runs r ON r.id = b.run_id
                    ORDER BY r.run_at ASC
                    """,
                    (limit_runs,),
                )
                return [
                    {
                        "model": r[0], "ticker": r[1],
                        "run_at": r[2].isoformat() if r[2] else None,
                        "mape": float(r[3]) if r[3] is not None else None,
                        "rmse": float(r[4]) if r[4] is not None else None,
                        "r_squared": float(r[5]) if r[5] is not None else None,
                        "calibrated_weight": float(r[6]) if r[6] is not None else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_financial_ratios_history(limit_runs: int = 200) -> list:
    """
    Financial ratios across all tickers/runs, for the Model Health screen's
    cross-sectional PSI drift check. financial_ratios is write-only until
    now (one row per run, never previously read back). Returns raw rows
    across every ticker (not scoped to one) — drift here means "has the
    population of companies we're analyzing shifted from what the industry
    risk-scoring templates were calibrated against," not a single company's
    own trend (which the existing forecast charts already cover per ticker).
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.ticker, r.run_at, f.revenue_growth, f.gross_margin, f.net_margin,
                           f.fcf_margin, f.rd_intensity, f.sga_intensity, f.asset_growth, f.cash_ratio
                    FROM financial_ratios f
                    JOIN risk_loop_runs r ON r.id = f.run_id
                    ORDER BY r.run_at DESC
                    LIMIT %s
                    """,
                    (limit_runs,),
                )
                cols = ["ticker", "run_at", "revenue_growth", "gross_margin", "net_margin",
                        "fcf_margin", "rd_intensity", "sga_intensity", "asset_growth", "cash_ratio"]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    d["run_at"] = d["run_at"].isoformat() if d["run_at"] else None
                    for k in cols[2:]:
                        d[k] = float(d[k]) if d[k] is not None else None
                    rows.append(d)
                rows.reverse()  # oldest-first, matching get_backtest_trend's ordering
                return rows
    return _run(_do) or []


def record_forecast(ticker: str, metric: str, target_quarter_end: str, horizon: int,
                     model: str, predicted_value: float, company_id: Optional[int] = None) -> None:
    """Record one point forecast for later accuracy reconciliation. Silently
    no-ops (ON CONFLICT DO NOTHING) if this exact (ticker, metric,
    target_quarter_end, horizon, model) was already forecast — see
    forecast_accuracy_history's DDL comment for why the original prediction
    is kept rather than overwritten by a later re-run."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO forecast_accuracy_history
                        (company_id, ticker, metric, target_quarter_end, horizon, model, predicted_value)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, metric, target_quarter_end, horizon, model) DO NOTHING
                    """,
                    (company_id, ticker.upper(), metric, target_quarter_end, horizon, model, predicted_value),
                )
            conn.commit()
    _run(_do)


def reconcile_forecast_actuals(ticker: str, metric: str, actuals: list) -> int:
    """
    Fill in actual_value for any past forecasts whose target quarter now has
    real data. `actuals`: [{"quarter_end": "YYYY-MM-DD", "value": float}, ...]
    (the same shape extract_quarterly_series returns). Only touches rows
    that don't already have an actual recorded — a forecast's accuracy
    record, once reconciled, doesn't change on a later re-run. Returns the
    number of rows reconciled.
    """
    if not actuals:
        return 0
    def _do():
        n = 0
        with _conn() as conn:
            with conn.cursor() as cur:
                for a in actuals:
                    if a.get("value") is None or not a.get("quarter_end"):
                        continue
                    cur.execute(
                        """
                        UPDATE forecast_accuracy_history
                        SET actual_value = %s, reconciled_at = NOW()
                        WHERE ticker = %s AND metric = %s AND target_quarter_end = %s
                          AND actual_value IS NULL
                        """,
                        (a["value"], ticker.upper(), metric, a["quarter_end"]),
                    )
                    n += cur.rowcount
            conn.commit()
        return n
    return _run(_do) or 0


def get_forecast_accuracy_history(ticker: Optional[str] = None, metric: Optional[str] = None,
                                   model: Optional[str] = None, reconciled_only: bool = False,
                                   limit: int = 500) -> list:
    """Reconciled (and pending) forecast-vs-actual records, for future
    calibration/reporting use. reconciled_only=True returns only rows where
    a real actual_value has landed."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                filters, params = [], []
                if ticker:
                    filters.append("ticker = %s"); params.append(ticker.upper())
                if metric:
                    filters.append("metric = %s"); params.append(metric)
                if model:
                    filters.append("model = %s"); params.append(model)
                if reconciled_only:
                    filters.append("actual_value IS NOT NULL")
                where = ("WHERE " + " AND ".join(filters)) if filters else ""
                params.append(min(limit, 2000))
                cur.execute(
                    f"""
                    SELECT ticker, metric, target_quarter_end, horizon, model,
                           predicted_value, actual_value, forecast_made_at, reconciled_at
                    FROM forecast_accuracy_history
                    {where}
                    ORDER BY target_quarter_end DESC, ticker, metric, horizon
                    LIMIT %s
                    """,
                    params,
                )
                cols = ["ticker", "metric", "target_quarter_end", "horizon", "model",
                        "predicted_value", "actual_value", "forecast_made_at", "reconciled_at"]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    for k in ("target_quarter_end", "forecast_made_at", "reconciled_at"):
                        if d.get(k) and hasattr(d[k], "isoformat"):
                            d[k] = d[k].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def get_prior_investigation(ticker: str) -> Optional[dict]:
    """Return the most recent agent_investigation memo for a ticker (cross-run memory)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content, summary, created_at FROM ai_analyses "
                    "WHERE ticker = %s AND kind = 'agent_investigation' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (ticker.upper(),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "content": row[0],
                    "summary": row[1],
                    "created_at": row[2].isoformat() if row[2] else None,
                }
    return _run(_do)


def get_latest_ai_analysis(
    ticker: str,
    kind: str,
    *,
    max_age_days: int = 30,
) -> Optional[dict]:
    """Return the most recent AI analysis of a given kind for a ticker, if within max_age_days.

    Returns None when no recent result exists. Use this as a cache check before running
    an expensive LLM call — if a recent result is returned, serve it directly.
    """
    def _do():
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, content, summary, model, created_at
                    FROM ai_analyses
                    WHERE ticker = %s AND kind = %s AND created_at >= %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (ticker.upper(), kind, cutoff),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "content": row[1],
                    "summary": row[2],
                    "model": row[3],
                    "created_at": row[4].isoformat() if row[4] else None,
                }
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# Query helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_run_history(ticker: str, limit: int = 20) -> list:
    """Recent runs for a ticker with summary data."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.id, r.ticker, r.run_at, r.industry, r.completed,
                           c.company_name,
                           (SELECT COUNT(*) FROM risk_scores WHERE run_id = r.id) AS risk_count
                    FROM risk_loop_runs r
                    LEFT JOIN companies c ON c.id = r.company_id
                    WHERE r.ticker = %s
                    ORDER BY r.run_at DESC
                    LIMIT %s
                    """,
                    (ticker.upper(), limit),
                )
                return [
                    {
                        "run_id": r[0], "ticker": r[1],
                        "run_at": r[2].isoformat() if r[2] else None,
                        "industry": r[3], "completed": r[4],
                        "company_name": r[5], "risk_count": r[6],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_posture_trend(ticker: str, limit: int = 20) -> list:
    """
    Completed runs for a ticker, oldest-first, each with an aggregate risk-score
    snapshot (avg/max score, RAG counts) computed from risk_scores. Feature 4 —
    backward-looking "how has this company's actual posture changed run-over-run,"
    distinct from forecasting.js's forward-looking quarter-ahead projections.
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH ticker_runs AS (
                        SELECT id, run_at, industry, appetite_level
                        FROM risk_loop_runs
                        WHERE ticker = %s AND completed = TRUE
                        ORDER BY run_at DESC
                        LIMIT %s
                    ),
                    agg AS (
                        SELECT run_id,
                               COUNT(*) AS risk_count,
                               AVG(score) AS avg_score,
                               MAX(score) AS max_score,
                               COUNT(*) FILTER (WHERE LEFT(rag_status, 1) ILIKE 'r') AS red_count,
                               COUNT(*) FILTER (WHERE LEFT(rag_status, 1) ILIKE 'a') AS amber_count,
                               COUNT(*) FILTER (WHERE LEFT(rag_status, 1) ILIKE 'g') AS green_count
                        FROM risk_scores
                        WHERE run_id IN (SELECT id FROM ticker_runs)
                        GROUP BY run_id
                    )
                    SELECT tr.id, tr.run_at, tr.industry, tr.appetite_level,
                           COALESCE(a.risk_count, 0), a.avg_score, a.max_score,
                           COALESCE(a.red_count, 0), COALESCE(a.amber_count, 0), COALESCE(a.green_count, 0)
                    FROM ticker_runs tr
                    LEFT JOIN agg a ON a.run_id = tr.id
                    ORDER BY tr.run_at ASC
                    """,
                    (ticker.upper(), limit),
                )
                return [
                    {
                        "run_id": r[0],
                        "run_at": r[1].isoformat() if r[1] else None,
                        "industry": r[2], "appetite_level": r[3],
                        "risk_count": r[4],
                        "avg_score": float(r[5]) if r[5] is not None else None,
                        "max_score": float(r[6]) if r[6] is not None else None,
                        "red_count": r[7], "amber_count": r[8], "green_count": r[9],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_last_digest(user_id: int, ticker: str) -> Optional[dict]:
    """Most recent digest notification (any read state) for this user+ticker."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, to_run_id, generated_at FROM digest_notifications
                    WHERE user_id = %s AND ticker = %s
                    ORDER BY generated_at DESC LIMIT 1
                    """,
                    (user_id, ticker.upper()),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {"id": row[0], "to_run_id": row[1], "generated_at": row[2]}
    return _run(_do)


def save_digest_notification(user_id: int, ticker: str, from_run_id: Optional[int],
                              to_run_id: int, payload: dict) -> Optional[int]:
    """Persist a generated digest and return its id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO digest_notifications (user_id, ticker, from_run_id, to_run_id, payload)
                    VALUES (%s, %s, %s, %s, %s) RETURNING id
                    """,
                    (user_id, ticker.upper(), from_run_id, to_run_id, Json(payload)),
                )
                new_id = cur.fetchone()[0]
            conn.commit()
            return new_id
    return _run(_do)


def list_digest_notifications(user_id: int, limit: int = 20) -> list:
    """Recent digests for a user, newest first, for the Notifications inbox."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, ticker, payload, generated_at, read_at
                    FROM digest_notifications
                    WHERE user_id = %s
                    ORDER BY generated_at DESC LIMIT %s
                    """,
                    (user_id, limit),
                )
                return [
                    {
                        "id": r[0], "ticker": r[1], **(r[2] or {}),
                        "generated_at": r[3].isoformat() if r[3] else None,
                        "read_at": r[4].isoformat() if r[4] else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def count_unread_digests(user_id: int) -> int:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM digest_notifications WHERE user_id = %s AND read_at IS NULL",
                    (user_id,),
                )
                return cur.fetchone()[0]
    return _run(_do) or 0


def mark_digest_read(digest_id: int, user_id: int) -> bool:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE digest_notifications SET read_at = NOW() WHERE id = %s AND user_id = %s AND read_at IS NULL",
                    (digest_id, user_id),
                )
                updated = cur.rowcount > 0
            conn.commit()
            return updated
    return _run(_do) or False


def get_run_detail(run_id: int) -> Optional[dict]:
    """Single run with key sub-tables."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.id, r.ticker, r.run_at, r.industry, r.appetite_level,
                           r.period_begin, r.period_end_col, r.completed, c.company_name
                    FROM risk_loop_runs r
                    LEFT JOIN companies c ON c.id = r.company_id
                    WHERE r.id = %s
                    """,
                    (run_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                result = {
                    "run_id": row[0], "ticker": row[1],
                    "run_at": row[2].isoformat() if row[2] else None,
                    "industry": row[3], "appetite_level": row[4],
                    "period_begin": row[5], "period_end": row[6],
                    "completed": row[7], "company_name": row[8],
                }
                cur.execute(
                    "SELECT risk_ref, risk_name, score, rag_status, velocity FROM risk_scores WHERE run_id = %s",
                    (run_id,),
                )
                result["risk_scores"] = [
                    {
                        "risk_ref": r[0], "risk_name": r[1],
                        "score": float(r[2]) if r[2] is not None else None,
                        "rag_status": r[3], "velocity": r[4],
                    }
                    for r in cur.fetchall()
                ]
                cur.execute(
                    "SELECT m_score, interpretation, rag_status FROM beneish_mscores WHERE run_id = %s",
                    (run_id,),
                )
                brow = cur.fetchone()
                result["beneish_mscore"] = {
                    "m_score": float(brow[0]) if brow and brow[0] is not None else None,
                    "interpretation": brow[1] if brow else None,
                    "rag_status": brow[2] if brow else None,
                } if brow else None
                cur.execute(
                    "SELECT z_score, interpretation, rag_status FROM altman_zscores WHERE run_id = %s",
                    (run_id,),
                )
                zrow = cur.fetchone()
                result["altman_zscore"] = {
                    "z_score": float(zrow[0]) if zrow and zrow[0] is not None else None,
                    "interpretation": zrow[1] if zrow else None,
                    "rag_status": zrow[2] if zrow else None,
                } if zrow else None
                return result
    return _run(_do)


def get_run_meta_for_evidence_pack(run_id: int) -> Optional[dict]:
    """Full risk_loop_runs row + company identity, for the Evidence Pack
    header. get_run_detail() above is missing completed_at/persona/
    signal_set/cik — it was built for a different caller (run detail
    lookup) and trimmed to what that needed."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.id, r.ticker, r.run_at, r.period_begin, r.period_end_col,
                           r.industry, r.appetite_level, r.persona, r.data_mode,
                           r.signal_set, r.completed, r.completed_at,
                           c.company_name, c.cik
                    FROM risk_loop_runs r
                    LEFT JOIN companies c ON c.id = r.company_id
                    WHERE r.id = %s
                    """,
                    (run_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "run_id": row[0], "ticker": row[1],
                    "run_at": row[2].isoformat() if row[2] else None,
                    "period_begin": row[3], "period_end": row[4],
                    "industry": row[5], "appetite_level": row[6], "persona": row[7],
                    "data_mode": row[8], "signal_set": row[9] or [],
                    "completed": row[10],
                    "completed_at": row[11].isoformat() if row[11] else None,
                    "company_name": row[12], "cik": row[13],
                }
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# Risks-as-Code artifacts
# ─────────────────────────────────────────────────────────────────────────────

def save_risks_as_code_artifact(run_id: int, ticker: str, framework: str, content: str) -> Optional[int]:
    """Upsert a Risks-as-Code artifact (OSCAL or COSO ERM) for a run. Returns artifact id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO risks_as_code_artifacts (run_id, ticker, framework, content_yaml, generated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (run_id, framework) DO UPDATE
                        SET content_yaml = EXCLUDED.content_yaml,
                            generated_at = NOW()
                    RETURNING id
                    """,
                    (run_id, ticker.upper(), framework, content),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def get_risks_as_code_artifact(run_id: int, framework: str) -> Optional[dict]:
    """Fetch a single artifact by run_id + framework."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, ticker, framework, content_yaml, generated_at FROM risks_as_code_artifacts WHERE run_id = %s AND framework = %s",
                    (run_id, framework),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id":           row[0],
                    "ticker":       row[1],
                    "framework":    row[2],
                    "content":      row[3],
                    "generated_at": row[4].isoformat() if row[4] else None,
                }
    return _run(_do)


def get_latest_risks_as_code_artifacts(ticker: str) -> list:
    """Return the most recent artifact per framework for a ticker."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (framework)
                        id, run_id, framework, content_yaml, generated_at
                    FROM risks_as_code_artifacts
                    WHERE ticker = %s
                    ORDER BY framework, generated_at DESC
                    """,
                    (ticker.upper(),),
                )
                return [
                    {
                        "id":           r[0],
                        "run_id":       r[1],
                        "framework":    r[2],
                        "content":      r[3],
                        "generated_at": r[4].isoformat() if r[4] else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


# ─────────────────────────────────────────────────────────────────────────────
# SOX Scoping
# ─────────────────────────────────────────────────────────────────────────────

def upsert_sox_config(company_id: int, fiscal_year: str, config: dict) -> Optional[int]:
    """Create or update SOX scoping config for a company + fiscal year."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sox_scoping_configs
                        (company_id, fiscal_year, fiscal_year_end, materiality_basis,
                         materiality_pct, performance_mat_pct, scope_note)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id, fiscal_year) DO UPDATE SET
                        fiscal_year_end     = COALESCE(EXCLUDED.fiscal_year_end,     sox_scoping_configs.fiscal_year_end),
                        materiality_basis   = EXCLUDED.materiality_basis,
                        materiality_pct     = EXCLUDED.materiality_pct,
                        performance_mat_pct = EXCLUDED.performance_mat_pct,
                        scope_note          = COALESCE(EXCLUDED.scope_note, sox_scoping_configs.scope_note),
                        updated_at          = NOW()
                    RETURNING id
                    """,
                    (
                        company_id, fiscal_year,
                        config.get("fiscal_year_end"),
                        config.get("materiality_basis", "pretax_income"),
                        config.get("materiality_pct", 5.0),
                        config.get("performance_mat_pct", 75.0),
                        config.get("scope_note"),
                    ),
                )
                return cur.fetchone()[0]
    return _run(_do)


def get_sox_config(company_id: int, fiscal_year: str) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, fiscal_year, fiscal_year_end, materiality_basis, "
                    "materiality_pct, performance_mat_pct, scope_note, updated_at "
                    "FROM sox_scoping_configs WHERE company_id = %s AND fiscal_year = %s",
                    (company_id, fiscal_year),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0], "fiscal_year": row[1],
                    "fiscal_year_end": row[2].isoformat() if row[2] else None,
                    "materiality_basis": row[3], "materiality_pct": float(row[4]),
                    "performance_mat_pct": float(row[5]), "scope_note": row[6],
                    "updated_at": row[7].isoformat() if row[7] else None,
                }
    return _run(_do)


def save_sox_scoping_result(run_id: int, company_id: int, result: dict) -> Optional[int]:
    """Persist a computed SOX scope. Returns row id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sox_scoping_results
                        (run_id, company_id, fiscal_year, planning_materiality,
                         performance_materiality, trivial_threshold, materiality_basis,
                         revenue_forecast_fy, pretax_income_estimate,
                         accounts_in_scope, processes_in_scope, systems_in_scope,
                         segments_coverage, trigger_reason, input_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        scoped_at               = NOW(),
                        planning_materiality    = EXCLUDED.planning_materiality,
                        performance_materiality = EXCLUDED.performance_materiality,
                        trivial_threshold       = EXCLUDED.trivial_threshold,
                        materiality_basis       = EXCLUDED.materiality_basis,
                        revenue_forecast_fy     = EXCLUDED.revenue_forecast_fy,
                        pretax_income_estimate  = EXCLUDED.pretax_income_estimate,
                        accounts_in_scope       = EXCLUDED.accounts_in_scope,
                        processes_in_scope      = EXCLUDED.processes_in_scope,
                        systems_in_scope        = EXCLUDED.systems_in_scope,
                        segments_coverage       = EXCLUDED.segments_coverage,
                        trigger_reason          = EXCLUDED.trigger_reason,
                        input_hash              = EXCLUDED.input_hash
                    RETURNING id
                    """,
                    (
                        run_id, company_id,
                        result.get("fiscal_year", ""),
                        result.get("planning_materiality"),
                        result.get("performance_materiality"),
                        result.get("trivial_threshold"),
                        result.get("materiality_basis"),
                        result.get("revenue_forecast_fy"),
                        result.get("pretax_income_estimate"),
                        Json(result.get("accounts_in_scope") or []),
                        Json(result.get("processes_in_scope") or []),
                        Json(result.get("systems_in_scope") or []),
                        Json(result.get("segments_coverage") or []),
                        result.get("trigger_reason"),
                        result.get("input_hash"),
                    ),
                )
                return cur.fetchone()[0]
    return _run(_do)


def get_sox_scoping_result(run_id: int) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, fiscal_year, scoped_at, planning_materiality, "
                    "performance_materiality, trivial_threshold, materiality_basis, "
                    "revenue_forecast_fy, pretax_income_estimate, accounts_in_scope, "
                    "processes_in_scope, systems_in_scope, segments_coverage, "
                    "trigger_reason, input_hash "
                    "FROM sox_scoping_results WHERE run_id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0], "run_id": run_id,
                    "fiscal_year": row[1],
                    "scoped_at": row[2].isoformat() if row[2] else None,
                    "planning_materiality": float(row[3]) if row[3] is not None else None,
                    "performance_materiality": float(row[4]) if row[4] is not None else None,
                    "trivial_threshold": float(row[5]) if row[5] is not None else None,
                    "materiality_basis": row[6],
                    "revenue_forecast_fy": float(row[7]) if row[7] is not None else None,
                    "pretax_income_estimate": float(row[8]) if row[8] is not None else None,
                    "accounts_in_scope": row[9] or [],
                    "processes_in_scope": row[10] or [],
                    "systems_in_scope": row[11] or [],
                    "segments_coverage": row[12] or [],
                    "trigger_reason": row[13],
                    "input_hash": row[14],
                }
    return _run(_do)


def get_latest_sox_scoping_result(company_id: int) -> Optional[dict]:
    """Most recent SOX scoping result for a company, across all runs."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT run_id FROM sox_scoping_results WHERE company_id = %s "
                    "ORDER BY scoped_at DESC LIMIT 1",
                    (company_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    run_id = _run(_do)
    return get_sox_scoping_result(run_id) if run_id else None


def upsert_sox_system(company_id: int, system: dict) -> Optional[int]:
    """Add or update a system in the SOX system registry."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sox_systems
                        (company_id, system_name, system_type, vendor, version,
                         linked_processes, significance, active, notes, added_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id, system_name) DO UPDATE SET
                        system_type      = EXCLUDED.system_type,
                        vendor           = COALESCE(EXCLUDED.vendor,    sox_systems.vendor),
                        version          = COALESCE(EXCLUDED.version,   sox_systems.version),
                        linked_processes = COALESCE(EXCLUDED.linked_processes, sox_systems.linked_processes),
                        significance     = EXCLUDED.significance,
                        active           = EXCLUDED.active,
                        notes            = COALESCE(EXCLUDED.notes, sox_systems.notes),
                        updated_at       = NOW()
                    RETURNING id
                    """,
                    (
                        company_id,
                        system.get("system_name", ""),
                        system.get("system_type", "custom"),
                        system.get("vendor"),
                        system.get("version"),
                        system.get("linked_processes") or [],
                        system.get("significance", "medium"),
                        system.get("active", True),
                        system.get("notes"),
                        system.get("added_by"),
                    ),
                )
                return cur.fetchone()[0]
    return _run(_do)


def deactivate_sox_system(company_id: int, system_id: int) -> bool:
    """Mark a system as inactive (soft-delete)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sox_systems SET active = FALSE, updated_at = NOW() "
                    "WHERE id = %s AND company_id = %s",
                    (system_id, company_id),
                )
                return cur.rowcount > 0
    return _run(_do) or False


def list_sox_systems(company_id: int, active_only: bool = True) -> list:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                q = ("SELECT id, system_name, system_type, vendor, version, linked_processes, "
                     "significance, active, notes, added_by, added_at, updated_at "
                     "FROM sox_systems WHERE company_id = %s")
                params = [company_id]
                if active_only:
                    q += " AND active = TRUE"
                q += " ORDER BY significance DESC, system_name"
                cur.execute(q, params)
                return [
                    {
                        "id": r[0], "system_name": r[1], "system_type": r[2],
                        "vendor": r[3], "version": r[4],
                        "linked_processes": r[5] or [],
                        "significance": r[6], "active": r[7], "notes": r[8],
                        "added_by": r[9],
                        "added_at": r[10].isoformat() if r[10] else None,
                        "updated_at": r[11].isoformat() if r[11] else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def upsert_sox_account_detail(company_id: int, account_id: str, detail: dict) -> Optional[int]:
    """Add or update a user-supplied detail/override for a SOX significant account."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sox_account_details
                        (company_id, account_id, geography, segments, notes,
                         manual_in_scope, manual_priority, updated_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id, account_id) DO UPDATE SET
                        geography       = EXCLUDED.geography,
                        segments        = EXCLUDED.segments,
                        notes           = EXCLUDED.notes,
                        manual_in_scope = EXCLUDED.manual_in_scope,
                        manual_priority = EXCLUDED.manual_priority,
                        updated_by      = EXCLUDED.updated_by,
                        updated_at      = NOW()
                    RETURNING id
                    """,
                    (
                        company_id, account_id,
                        detail.get("geography") or [],
                        detail.get("segments") or [],
                        detail.get("notes"),
                        detail.get("manual_in_scope"),
                        detail.get("manual_priority"),
                        detail.get("updated_by"),
                    ),
                )
                return cur.fetchone()[0]
    return _run(_do)


def get_sox_account_details(company_id: int) -> dict:
    """All account detail/override rows for a company, keyed by account_id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT account_id, geography, segments, notes, manual_in_scope, "
                    "manual_priority, updated_at FROM sox_account_details WHERE company_id = %s",
                    (company_id,),
                )
                return {
                    r[0]: {
                        "geography": r[1] or [], "segments": r[2] or [], "notes": r[3],
                        "manual_in_scope": r[4], "manual_priority": r[5],
                        "updated_at": r[6].isoformat() if r[6] else None,
                    }
                    for r in cur.fetchall()
                }
    return _run(_do) or {}


def upsert_sox_process_detail(company_id: int, process_id: str, detail: dict) -> Optional[int]:
    """Add or update a user-supplied detail/override for a SOX process."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sox_process_details
                        (company_id, process_id, geography, segments, notes,
                         manual_coverage_level, estimated_exposure, updated_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id, process_id) DO UPDATE SET
                        geography              = EXCLUDED.geography,
                        segments               = EXCLUDED.segments,
                        notes                  = EXCLUDED.notes,
                        manual_coverage_level  = EXCLUDED.manual_coverage_level,
                        estimated_exposure     = EXCLUDED.estimated_exposure,
                        updated_by             = EXCLUDED.updated_by,
                        updated_at             = NOW()
                    RETURNING id
                    """,
                    (
                        company_id, process_id,
                        detail.get("geography") or [],
                        detail.get("segments") or [],
                        detail.get("notes"),
                        detail.get("manual_coverage_level"),
                        detail.get("estimated_exposure"),
                        detail.get("updated_by"),
                    ),
                )
                return cur.fetchone()[0]
    return _run(_do)


def get_sox_process_details(company_id: int) -> dict:
    """All process detail/override rows for a company, keyed by process_id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT process_id, geography, segments, notes, manual_coverage_level, "
                    "estimated_exposure, updated_at FROM sox_process_details WHERE company_id = %s",
                    (company_id,),
                )
                return {
                    r[0]: {
                        "geography": r[1] or [], "segments": r[2] or [], "notes": r[3],
                        "manual_coverage_level": r[4], "estimated_exposure": float(r[5]) if r[5] is not None else None,
                        "updated_at": r[6].isoformat() if r[6] else None,
                    }
                    for r in cur.fetchall()
                }
    return _run(_do) or {}


def upsert_sox_segment(company_id: int, run_id: Optional[int], segment: dict) -> None:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sox_financial_segments
                        (company_id, run_id, fiscal_year, segment_type, segment_name,
                         revenue, revenue_pct, gross_profit, operating_income, assets,
                         rev_growth_yoy_pct, net_income, gross_margin_pct, op_margin_pct,
                         net_margin_pct, source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id, fiscal_year, segment_type, segment_name) DO UPDATE SET
                        run_id             = COALESCE(EXCLUDED.run_id, sox_financial_segments.run_id),
                        revenue            = EXCLUDED.revenue,
                        revenue_pct        = EXCLUDED.revenue_pct,
                        gross_profit       = EXCLUDED.gross_profit,
                        operating_income   = EXCLUDED.operating_income,
                        assets             = EXCLUDED.assets,
                        rev_growth_yoy_pct = EXCLUDED.rev_growth_yoy_pct,
                        net_income         = EXCLUDED.net_income,
                        gross_margin_pct   = EXCLUDED.gross_margin_pct,
                        op_margin_pct      = EXCLUDED.op_margin_pct,
                        net_margin_pct     = EXCLUDED.net_margin_pct,
                        source             = EXCLUDED.source
                    """,
                    (
                        company_id, run_id,
                        segment.get("fiscal_year", ""),
                        segment.get("segment_type", "geography"),
                        segment.get("segment_name", ""),
                        segment.get("revenue"),
                        segment.get("revenue_pct"),
                        segment.get("gross_profit"),
                        segment.get("operating_income"),
                        segment.get("assets"),
                        segment.get("rev_growth_yoy_pct"),
                        segment.get("net_income"),
                        segment.get("gross_margin_pct"),
                        segment.get("op_margin_pct"),
                        segment.get("net_margin_pct"),
                        segment.get("source", "manual"),
                    ),
                )
    _run(_do)


def get_sox_segments(company_id: int, fiscal_year: str) -> list:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, segment_type, segment_name, revenue, revenue_pct, "
                    "gross_profit, operating_income, assets, "
                    "rev_growth_yoy_pct, net_income, gross_margin_pct, op_margin_pct, net_margin_pct, source "
                    "FROM sox_financial_segments "
                    "WHERE company_id = %s AND fiscal_year = %s "
                    "ORDER BY segment_type, revenue DESC NULLS LAST",
                    (company_id, fiscal_year),
                )
                return [
                    {
                        "id": r[0], "segment_type": r[1], "segment_name": r[2],
                        "revenue": float(r[3]) if r[3] is not None else None,
                        "revenue_pct": float(r[4]) if r[4] is not None else None,
                        "gross_profit": float(r[5]) if r[5] is not None else None,
                        "operating_income": float(r[6]) if r[6] is not None else None,
                        "assets": float(r[7]) if r[7] is not None else None,
                        "rev_growth_yoy_pct": float(r[8]) if r[8] is not None else None,
                        "net_income": float(r[9]) if r[9] is not None else None,
                        "gross_margin_pct": float(r[10]) if r[10] is not None else None,
                        "op_margin_pct": float(r[11]) if r[11] is not None else None,
                        "net_margin_pct": float(r[12]) if r[12] is not None else None,
                        "source": r[13],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_latest_sox_segments(company_id: int) -> list:
    """get_sox_segments requires a fiscal_year; this resolves the most
    recent one for the company first, mirroring get_latest_sox_scoping_result.
    Used by the Risk Coverage Cube's optional operating-unit (Z) axis."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT fiscal_year FROM sox_financial_segments WHERE company_id = %s "
                    "AND fiscal_year IS NOT NULL ORDER BY fiscal_year DESC LIMIT 1",
                    (company_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    fiscal_year = _run(_do)
    return get_sox_segments(company_id, fiscal_year) if fiscal_year else []


def delete_sox_segment(company_id: int, segment_id: int) -> bool:
    """Delete a geography / business-segment financial record."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM sox_financial_segments WHERE id = %s AND company_id = %s",
                    (segment_id, company_id),
                )
                return cur.rowcount > 0
    return _run(_do) or False


def save_segment_forecasts(run_id: int, rows: list) -> None:
    """Persist per-segment forecast KPIs for a pipeline run.

    Each dict in rows must contain: segment_type, segment_name, and any of
    fiscal_year, revenue_m, revenue_pct, rev_growth_yoy, gross_margin,
    op_margin, net_margin, source.
    """
    if not rows:
        return
    def _do():
        data = [
            (
                run_id,
                r.get("segment_type", "geography"),
                r.get("segment_name", ""),
                r.get("fiscal_year"),
                r.get("revenue_m"),
                r.get("revenue_pct"),
                r.get("rev_growth_yoy"),
                r.get("gross_margin"),
                r.get("op_margin"),
                r.get("net_margin"),
                r.get("source", "seeded"),
            )
            for r in rows
        ]
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO segment_forecasts
                        (run_id, segment_type, segment_name, fiscal_year,
                         revenue_m, revenue_pct, rev_growth_yoy,
                         gross_margin, op_margin, net_margin, source)
                    VALUES %s
                    ON CONFLICT (run_id, segment_type, segment_name) DO UPDATE SET
                        fiscal_year    = EXCLUDED.fiscal_year,
                        revenue_m      = EXCLUDED.revenue_m,
                        revenue_pct    = EXCLUDED.revenue_pct,
                        rev_growth_yoy = EXCLUDED.rev_growth_yoy,
                        gross_margin   = EXCLUDED.gross_margin,
                        op_margin      = EXCLUDED.op_margin,
                        net_margin     = EXCLUDED.net_margin,
                        source         = EXCLUDED.source
                    """,
                    data,
                )
    _run(_do)


def get_segment_forecasts(run_id: int) -> list:
    """Return all segment / geography forecast KPIs for a pipeline run."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT segment_type, segment_name, fiscal_year, revenue_m, revenue_pct, "
                    "rev_growth_yoy, gross_margin, op_margin, net_margin, source "
                    "FROM segment_forecasts WHERE run_id = %s "
                    "ORDER BY segment_type, revenue_pct DESC NULLS LAST",
                    (run_id,),
                )
                return [
                    {
                        "segment_type": r[0], "segment_name": r[1], "fiscal_year": r[2],
                        "revenue_m": float(r[3]) if r[3] is not None else None,
                        "revenue_pct": float(r[4]) if r[4] is not None else None,
                        "rev_growth_yoy": float(r[5]) if r[5] is not None else None,
                        "gross_margin": float(r[6]) if r[6] is not None else None,
                        "op_margin": float(r[7]) if r[7] is not None else None,
                        "net_margin": float(r[8]) if r[8] is not None else None,
                        "source": r[9],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def upsert_peer_segment(
    company_id: int,
    peer_ticker: str,
    peer_name: str,
    rows: list,
) -> int:
    """Upsert peer company segment / geography financial data. Returns rows saved."""
    if not rows:
        return 0
    count = 0
    def _do():
        nonlocal count
        with _conn() as conn:
            with conn.cursor() as cur:
                for r in rows:
                    cur.execute(
                        """
                        INSERT INTO peer_segment_financials
                            (company_id, peer_ticker, peer_name, fiscal_year,
                             segment_type, segment_name, revenue_m, revenue_pct,
                             gross_margin, op_margin, net_margin, source)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (company_id, peer_ticker, fiscal_year, segment_type, segment_name)
                        DO UPDATE SET
                            peer_name    = EXCLUDED.peer_name,
                            revenue_m    = EXCLUDED.revenue_m,
                            revenue_pct  = EXCLUDED.revenue_pct,
                            gross_margin = EXCLUDED.gross_margin,
                            op_margin    = EXCLUDED.op_margin,
                            net_margin   = EXCLUDED.net_margin,
                            source       = EXCLUDED.source,
                            fetched_at   = NOW()
                        """,
                        (
                            company_id, peer_ticker.upper(),
                            peer_name or peer_ticker.upper(),
                            r.get("fiscal_year"),
                            r.get("segment_type", "geography"),
                            r.get("segment_name", ""),
                            r.get("revenue_m"),
                            r.get("revenue_pct"),
                            r.get("gross_margin"),
                            r.get("op_margin"),
                            r.get("net_margin"),
                            r.get("source", "manual"),
                        ),
                    )
                    count += 1
    _run(_do)
    return count


def get_peer_segments(
    company_id: int,
    peer_ticker: Optional[str] = None,
    fiscal_year: Optional[str] = None,
) -> list:
    """Return peer segment / geography breakdowns for comparison.

    Omit peer_ticker to return all peers; omit fiscal_year to return all years.
    """
    def _do():
        clauses = ["company_id = %s"]
        params: list = [company_id]
        if peer_ticker:
            clauses.append("peer_ticker = %s")
            params.append(peer_ticker.upper())
        if fiscal_year:
            clauses.append("fiscal_year = %s")
            params.append(fiscal_year)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT peer_ticker, peer_name, fiscal_year, segment_type, segment_name, "
                    "revenue_m, revenue_pct, gross_margin, op_margin, net_margin, source "
                    "FROM peer_segment_financials WHERE " + " AND ".join(clauses) +
                    " ORDER BY peer_ticker, segment_type, revenue_pct DESC NULLS LAST",
                    params,
                )
                return [
                    {
                        "peer_ticker": r[0], "peer_name": r[1], "fiscal_year": r[2],
                        "segment_type": r[3], "segment_name": r[4],
                        "revenue_m": float(r[5]) if r[5] is not None else None,
                        "revenue_pct": float(r[6]) if r[6] is not None else None,
                        "gross_margin": float(r[7]) if r[7] is not None else None,
                        "op_margin": float(r[8]) if r[8] is not None else None,
                        "net_margin": float(r[9]) if r[9] is not None else None,
                        "source": r[10],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def log_sox_rescoping_trigger(
    company_id: int,
    trigger_type: str,
    trigger_detail: dict,
    prev_run_id: Optional[int] = None,
    new_run_id: Optional[int] = None,
    rescoped: bool = False,
) -> None:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sox_rescoping_triggers
                        (company_id, trigger_type, trigger_detail,
                         prev_run_id, new_run_id, rescoped)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (company_id, trigger_type, Json(trigger_detail),
                     prev_run_id, new_run_id, rescoped),
                )
    _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# pgvector — embeddings
# ─────────────────────────────────────────────────────────────────────────────

def save_embedding(
    source_table: str,
    source_id: int,
    content_type: str,
    embedding: list,
    *,
    model: Optional[str] = None,
    text_snippet: Optional[str] = None,
    chunk_index: int = 0,
    company_id: Optional[int] = None,
    source_hash: Optional[str] = None,
) -> Optional[int]:
    """Store a vector embedding for any source row. Returns embedding id (or None).

    source_table : originating table name  (e.g. 'rss_articles', 'ai_analyses')
    source_id    : PK of the row in that table
    content_type : use an EMBT_* constant (e.g. EMBT_RISK_FACTOR, EMBT_ARTICLE)
    embedding    : list[float] from your embedding model — len must equal EMBEDDING_DIM
    chunk_index  : 0-based chunk position for long documents split before embedding
    company_id   : companies.id — enables fast per-company filtering in searches
    source_hash  : freshness fingerprint of the embedded source content (e.g.
                   concepts.label_hash for EMBT_CONCEPT); NULL for content
                   types with no staleness check
    """
    if not embedding or not (_HAS_PGVECTOR and _PGVECTOR_READY):
        return None
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO embeddings
                        (source_table, source_id, content_type, model,
                         chunk_index, company_id, embedding, text_snippet, source_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT ON CONSTRAINT uq_embeddings DO UPDATE
                        SET embedding    = EXCLUDED.embedding,
                            company_id   = COALESCE(EXCLUDED.company_id, embeddings.company_id),
                            text_snippet = EXCLUDED.text_snippet,
                            source_hash  = EXCLUDED.source_hash,
                            created_at   = NOW()
                    RETURNING id
                    """,
                    (source_table, source_id, content_type, model or "unknown",
                     chunk_index, company_id, embedding, text_snippet, source_hash),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


_DISTANCE_OPS = {"cosine": "<=>", "l2": "<->", "ip": "<#>"}


def save_embeddings_bulk(rows: list) -> int:
    """Batch-upsert embeddings in a single transaction. Preferred over calling
    save_embedding() in a loop when chunking long documents.

    Each item in rows must be a dict with:
        source_table, source_id, content_type, embedding
    Optional keys: model, text_snippet, chunk_index (default 0), company_id

    Returns the number of rows processed (0 when pgvector is unavailable).
    """
    if not rows or not (_HAS_PGVECTOR and _PGVECTOR_READY):
        return 0
    def _do():
        data = [
            (
                r["source_table"], r["source_id"], r["content_type"],
                r.get("model") or "unknown",
                r.get("chunk_index", 0),
                r.get("company_id"),
                r["embedding"],
                r.get("text_snippet"),
            )
            for r in rows
            if r.get("embedding")
        ]
        if not data:
            return 0
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO embeddings
                        (source_table, source_id, content_type, model,
                         chunk_index, company_id, embedding, text_snippet)
                    VALUES %s
                    ON CONFLICT ON CONSTRAINT uq_embeddings DO UPDATE
                        SET embedding    = EXCLUDED.embedding,
                            company_id   = COALESCE(EXCLUDED.company_id, embeddings.company_id),
                            text_snippet = EXCLUDED.text_snippet,
                            created_at   = NOW()
                    """,
                    data,
                )
        return len(data)
    return _run(_do, default=0) or 0


def find_similar_risks_cross_company(
    embedding: list, *, exclude_company_id: Optional[int] = None, limit: int = 10,
) -> list:
    """Nearest-neighbour risk narratives (EMBT_RISK_NARRATIVE) across every
    company, not just the caller's own — peer-benchmarking search that
    get_relevant_context() intentionally can't do (it always scopes to one
    company_id for chat RAG). Optionally excludes the querying company so a
    risk doesn't just match itself/its own near-duplicates.

    Returns [{risk_ref, risk_name, category, score, rag, ticker, company_name,
    distance}, ...], nearest first. [] when pgvector is unavailable.
    """
    if not embedding or not (_HAS_PGVECTOR and _PGVECTOR_READY):
        return []

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rs.risk_ref, rs.risk_name, rs.category, rs.score, rs.rag_status,
                           c.ticker, c.company_name,
                           (e.embedding <=> %s) AS distance
                    FROM embeddings e
                    JOIN risk_scores rs ON rs.id = e.source_id AND e.source_table = 'risk_scores'
                    JOIN risk_loop_runs r ON r.id = rs.run_id
                    JOIN companies c ON c.id = r.company_id
                    WHERE e.content_type = %s
                      AND (%s::int IS NULL OR r.company_id != %s)
                    ORDER BY e.embedding <=> %s
                    LIMIT %s
                    """,
                    (embedding, EMBT_RISK_NARRATIVE, exclude_company_id, exclude_company_id,
                     embedding, limit),
                )
                return [
                    {
                        "risk_ref": r[0], "risk_name": r[1], "category": r[2],
                        "score": float(r[3]) if r[3] is not None else None,
                        "rag": r[4], "ticker": r[5], "company_name": r[6],
                        "distance": float(r[7]) if r[7] is not None else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_relevant_context(
    query_embedding: list,
    *,
    company_id: Optional[int] = None,
    content_types: Optional[list] = None,
    source_tables: Optional[list] = None,
    limit: int = 5,
    max_distance: float = 1.0,
    metric: str = "cosine",
) -> list:
    """Return the top-k semantically relevant stored text snippets.

    This is the primary token-cost reduction API.  Instead of sending entire
    documents to the LLM, embed the question once and retrieve only the most
    relevant stored chunks.  Typical savings vs. full-document context: 10–100×
    fewer input tokens.

    Args:
        query_embedding : embed(question) — must use the same model as stored embeddings
        company_id      : restrict to a single company's embeddings (strongly recommended)
        content_types   : list of EMBT_* constants, e.g. [EMBT_RISK_FACTOR, EMBT_ARTICLE]
        source_tables   : e.g. ['edgar_risk_factor_filings', 'rss_articles']
        limit           : max snippets returned — keep small (3–8) for lean prompts
        max_distance    : cosine upper-bound; 0 = identical, 2 = opposite (default 1.0)
        metric          : 'cosine' | 'l2' | 'ip'

    Returns list of dicts: source_table, source_id, content_type, model,
        chunk_index, text_snippet, distance, created_at.
    Sorted by ascending distance (most relevant first).
    """
    if not query_embedding or not (_HAS_PGVECTOR and _PGVECTOR_READY):
        return []
    op = _DISTANCE_OPS.get(metric, "<=>")
    def _do():
        clauses: list = []
        params: list = []
        if company_id:
            clauses.append("company_id = %s")
            params.append(company_id)
        if content_types:
            clauses.append("content_type = ANY(%s)")
            params.append(content_types)
        if source_tables:
            clauses.append("source_table = ANY(%s)")
            params.append(source_tables)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        # Over-fetch so distance threshold doesn't leave us with fewer than limit.
        fetch_limit = limit * 3
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT source_table, source_id, content_type, model,
                           chunk_index, text_snippet,
                           embedding {op} %s AS distance, created_at
                    FROM embeddings
                    {where}
                    ORDER BY embedding {op} %s
                    LIMIT %s
                    """,
                    params + [query_embedding, query_embedding, fetch_limit],
                )
                rows = cur.fetchall()
        return [
            {
                "source_table": r[0], "source_id": r[1],
                "content_type": r[2], "model": r[3],
                "chunk_index": r[4], "text_snippet": r[5],
                "distance": float(r[6]) if r[6] is not None else None,
                "created_at": r[7].isoformat() if r[7] else None,
            }
            for r in rows
            if r[6] is not None and float(r[6]) <= max_distance
        ][:limit]
    return _run(_do) or []


def get_concept_embedding_hashes() -> Dict[int, Optional[str]]:
    """{concept_id: source_hash} for every concept that currently has an
    EMBT_CONCEPT embedding — the freshness check reembed_stale_concepts()
    compares against concepts.label_hash. A concept absent from this dict has
    never been embedded at all (also stale, by the same comparison: dict.get
    returns None, which only equals a concept's label_hash if that too is
    somehow None)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT source_id, source_hash FROM embeddings WHERE source_table = 'concepts' AND content_type = %s",
                    (EMBT_CONCEPT,),
                )
                return {r[0]: r[1] for r in cur.fetchall()}
    return _run(_do) or {}


def search_concepts_by_embedding(query_embedding: list, *, scheme: Optional[str] = None, limit: int = 10) -> list:
    """Nearest EMBT_CONCEPT neighbours to a query vector — "which concept is
    this text about". Joins back to concepts for identity (scheme,
    pref_label), since the embeddings row alone only carries the concept id."""
    if not query_embedding or not (_HAS_PGVECTOR and _PGVECTOR_READY):
        return []
    def _do():
        clauses = ["e.source_table = 'concepts'", "e.content_type = %s"]
        params: list = [EMBT_CONCEPT]
        if scheme:
            clauses.append("c.scheme = %s")
            params.append(scheme)
        where = " AND ".join(clauses)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT c.id, c.scheme, c.pref_label, c.notation,
                           (e.embedding <=> %s) AS distance
                    FROM embeddings e
                    JOIN concepts c ON c.id = e.source_id
                    WHERE {where}
                    ORDER BY e.embedding <=> %s
                    LIMIT %s
                    """,
                    [query_embedding] + params + [query_embedding, limit],
                )
                return [
                    {
                        "concept_id": r[0], "scheme": r[1], "pref_label": r[2], "notation": r[3],
                        "distance": float(r[4]) if r[4] is not None else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


# ─────────────────────────────────────────────────────────────────────────────
# Code editor configs
# ─────────────────────────────────────────────────────────────────────────────

def save_code_editor_config(storage_key: str, content: str) -> bool:
    """Upsert a code editor config (Risk-as-Code or Policy-as-Code YAML rules). Returns True on success."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO code_editor_configs (storage_key, content)
                    VALUES (%s, %s)
                    ON CONFLICT (storage_key) DO UPDATE SET
                        content    = EXCLUDED.content,
                        updated_at = NOW()
                    """,
                    (storage_key, content),
                )
        return True
    return _run(_do, default=False) or False


def get_code_editor_config(storage_key: str) -> Optional[dict]:
    """Return saved editor content for a key, or None if not found."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content, updated_at FROM code_editor_configs WHERE storage_key = %s",
                    (storage_key,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "storage_key": storage_key,
                    "content": row[0],
                    "updated_at": row[1].isoformat() if row[1] else None,
                }
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat stubs (deprecated)
# ─────────────────────────────────────────────────────────────────────────────

def save_result(tool_name: str, ticker: str, company_name: str, data: dict) -> Optional[int]:
    """Deprecated — use typed save functions."""
    logger.warning("db.save_result() is deprecated; use typed save functions (tool: %s)", tool_name)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Risk Register Review
# ─────────────────────────────────────────────────────────────────────────────

def create_risk_register_review(
    run_id: Optional[int],
    review_type: str = "internal",
    framework: Optional[str] = None,
) -> Optional[int]:
    """Create a risk_register_reviews record. Returns review_id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO risk_register_reviews (run_id, review_type, framework)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (run_id, review_type, framework),
                )
                return cur.fetchone()[0]
    return _run(_do)


def save_review_risk_states(review_id: int, states: list) -> None:
    """Upsert per-risk state rows for a review session.

    Also normalizes each row's controls_assigned JSONB blob into
    risk_control_mappings — that table existed in the schema (with exactly
    the right shape: review_id, risk_ref, control_ref) but had zero write
    path anywhere in the codebase until now, so cac_map_to_risks and anything
    else wanting "which controls did the auditor actually assign to this
    risk" had no real data to query and fell back to fuzzy keyword matching.
    This is a full replace for the review's mappings each call (delete then
    re-insert), not a merge — controls_assigned in the request is always the
    complete current set for the risks it includes, same as the JSONB column
    itself.
    """
    if not states:
        return
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                for s in states:
                    risk_ref = s.get("risk_ref")
                    cur.execute(
                        """
                        INSERT INTO review_risk_states
                            (review_id, risk_ref, original_wording, current_wording,
                             included, reason_for_change, controls_assigned)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (review_id, risk_ref) DO UPDATE SET
                            current_wording   = EXCLUDED.current_wording,
                            included          = EXCLUDED.included,
                            reason_for_change = EXCLUDED.reason_for_change,
                            controls_assigned = EXCLUDED.controls_assigned,
                            updated_at        = NOW()
                        """,
                        (
                            review_id,
                            risk_ref,
                            s.get("original_wording"),
                            s.get("current_wording"),
                            s.get("included", True),
                            s.get("reason_for_change"),
                            Json(s.get("controls_assigned") or []),
                        ),
                    )
                    cur.execute(
                        "DELETE FROM risk_control_mappings WHERE review_id = %s AND risk_ref = %s",
                        (review_id, risk_ref),
                    )
                    ctrl_rows = [
                        (review_id, risk_ref, c.get("ref"), "manual", bool(c.get("generate_code", False)))
                        for c in (s.get("controls_assigned") or [])
                        if c.get("ref")
                    ]
                    if ctrl_rows:
                        execute_values(
                            cur,
                            """
                            INSERT INTO risk_control_mappings
                                (review_id, risk_ref, control_ref, mapping_type, generate_code)
                            VALUES %s
                            ON CONFLICT (review_id, risk_ref, control_ref) DO UPDATE SET
                                generate_code = EXCLUDED.generate_code
                            """,
                            ctrl_rows,
                        )
    _run(_do)


def get_risk_control_mappings(review_id: Optional[int] = None) -> list:
    """Return risk_control_mappings rows, optionally scoped to one review.
    Each row: {risk_ref, control_ref, mapping_type, generate_code}."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                if review_id is not None:
                    cur.execute(
                        "SELECT risk_ref, control_ref, mapping_type, generate_code "
                        "FROM risk_control_mappings WHERE review_id = %s ORDER BY risk_ref",
                        (review_id,),
                    )
                else:
                    cur.execute(
                        "SELECT risk_ref, control_ref, mapping_type, generate_code "
                        "FROM risk_control_mappings ORDER BY review_id DESC, risk_ref"
                    )
                return [
                    {"risk_ref": r[0], "control_ref": r[1], "mapping_type": r[2], "generate_code": r[3]}
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_risk_control_mappings_for_run(run_id: int) -> list:
    """Curated risk<->control mappings for a pipeline run, via its most
    recent review session. Used by cac_map_to_risks to prefer the
    auditor-assigned mapping over its fuzzy keyword-matching fallback."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT m.risk_ref, m.control_ref, m.mapping_type, m.generate_code
                    FROM risk_control_mappings m
                    JOIN risk_register_reviews r ON r.id = m.review_id
                    WHERE r.run_id = %s
                      AND r.id = (
                          SELECT id FROM risk_register_reviews
                          WHERE run_id = %s ORDER BY created_at DESC LIMIT 1
                      )
                    ORDER BY m.risk_ref
                    """,
                    (run_id, run_id),
                )
                return [
                    {"risk_ref": r[0], "control_ref": r[1], "mapping_type": r[2], "generate_code": r[3]}
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_review_risk_states(review_id: int) -> list:
    """Return all risk state rows for a review session."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT risk_ref, original_wording, current_wording,
                           included, reason_for_change, controls_assigned, updated_at
                    FROM review_risk_states
                    WHERE review_id = %s
                    ORDER BY risk_ref
                    """,
                    (review_id,),
                )
                rows = cur.fetchall()
                return [
                    {
                        "risk_ref": r[0],
                        "original_wording": r[1],
                        "current_wording": r[2],
                        "included": r[3],
                        "reason_for_change": r[4],
                        "controls_assigned": r[5] or [],
                        "updated_at": r[6].isoformat() if r[6] else None,
                    }
                    for r in rows
                ]
    return _run(_do) or []


def save_rac_yaml(review_id: int, yaml_str: str) -> None:
    """Persist generated Risk-as-Code YAML to the review record and mark it completed."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE risk_register_reviews
                    SET rac_yaml = %s, status = 'completed', completed_at = NOW()
                    WHERE id = %s
                    """,
                    (yaml_str, review_id),
                )
    _run(_do)


def complete_risk_register_review(review_id: int) -> None:
    """Mark a review session as completed."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE risk_register_reviews SET status = 'completed', completed_at = NOW() WHERE id = %s",
                    (review_id,),
                )
    _run(_do)


def list_risk_register_reviews(run_id: Optional[int] = None, limit: int = 20) -> list:
    """List recent review sessions, optionally filtered by run_id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                if run_id:
                    cur.execute(
                        """
                        SELECT id, run_id, review_type, framework, status, created_at, completed_at
                        FROM risk_register_reviews
                        WHERE run_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (run_id, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, run_id, review_type, framework, status, created_at, completed_at
                        FROM risk_register_reviews
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                rows = cur.fetchall()
                return [
                    {
                        "id": r[0], "run_id": r[1], "review_type": r[2],
                        "framework": r[3], "status": r[4],
                        "created_at": r[5].isoformat() if r[5] else None,
                        "completed_at": r[6].isoformat() if r[6] else None,
                    }
                    for r in rows
                ]
    return _run(_do) or []


def save_framework_catalog(framework_name: str, risks_json: list) -> None:
    """Upsert a framework risk catalog (keyed by name, version='latest')."""
    if not framework_name or not risks_json:
        return
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO framework_risk_catalogs (framework_name, framework_ver, risks_json)
                    VALUES (%s, 'latest', %s)
                    ON CONFLICT (framework_name, framework_ver) DO UPDATE SET
                        risks_json = EXCLUDED.risks_json,
                        fetched_at = NOW()
                    """,
                    (framework_name, Json(risks_json)),
                )
    _run(_do)


def list_framework_catalogs() -> list:
    """Return all saved framework risk catalogs."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT framework_name, risks_json, fetched_at
                    FROM framework_risk_catalogs
                    ORDER BY framework_name
                    """
                )
                return [
                    {
                        "framework": r[0],
                        "risks": r[1] or [],
                        "fetched_at": r[2].isoformat() if r[2] else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_risk_register_review(review_id: int) -> Optional[dict]:
    """Return a single risk_register_reviews record by ID."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, run_id, review_type, framework, status, created_at, completed_at
                    FROM risk_register_reviews
                    WHERE id = %s
                    """,
                    (review_id,),
                )
                r = cur.fetchone()
                if not r:
                    return None
                return {
                    "id": r[0], "run_id": r[1], "review_type": r[2],
                    "framework": r[3], "status": r[4],
                    "created_at": r[5].isoformat() if r[5] else None,
                    "completed_at": r[6].isoformat() if r[6] else None,
                }
    return _run(_do)


def apply_review_wording(run_id: int, updates: list) -> int:
    """Persist reviewed risk wording back into risk_scores for a run.

    Writes current_wording into narrative (TEXT, uncapped).
    Also updates risk_name when the wording fits within 128 chars.
    Returns the number of rows actually updated.
    """
    if not updates:
        return 0
    rows_updated = 0
    def _do():
        nonlocal rows_updated
        with _conn() as conn:
            with conn.cursor() as cur:
                for u in updates:
                    risk_ref = u.get("risk_ref") or u.get("id") or ""
                    wording  = (u.get("current_wording") or u.get("wording") or "").strip()
                    if not risk_ref or not wording:
                        continue
                    cur.execute(
                        """
                        UPDATE risk_scores
                           SET narrative  = %s,
                               risk_name  = CASE WHEN length(%s) <= 128 THEN %s ELSE risk_name END
                         WHERE run_id = %s AND risk_ref = %s
                        """,
                        (wording, wording, wording, run_id, risk_ref),
                    )
                    rows_updated += cur.rowcount
    _run(_do)
    return rows_updated


def get_latest_risks_for_ticker(ticker: str) -> dict:
    """Return risks from the most recent run for a ticker.

    Overlays current_wording from the most recent review session so the
    Internal tab always shows the latest reviewed names, not just the raw
    pipeline output.  Returns {"run_id": int|None, "risks": list}.
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM risk_loop_runs
                    WHERE ticker = %s
                    ORDER BY run_at DESC LIMIT 1
                    """,
                    (ticker.upper(),),
                )
                row = cur.fetchone()
                if not row:
                    return {"run_id": None, "risks": []}
                run_id = row[0]
                cur.execute(
                    """
                    SELECT rs.risk_ref,
                           COALESCE(rrs.current_wording, rs.narrative, rs.risk_name) AS name,
                           rs.risk_name,
                           rs.narrative,
                           rs.category,
                           rs.score,
                           rs.rag_status,
                           rs.source_framework,
                           rs.base_score,
                           rs.delta,
                           rs.velocity,
                           rs.control_env,
                           rs.peer_benchmark,
                           rrs.current_wording,
                           rs.assigned_domain,
                           rs.segment_type,
                           rs.segment_name
                    FROM risk_scores rs
                    LEFT JOIN LATERAL (
                        SELECT rrs2.current_wording
                        FROM review_risk_states rrs2
                        JOIN risk_register_reviews rrr ON rrr.id = rrs2.review_id
                        WHERE rrr.run_id = rs.run_id
                          AND rrs2.risk_ref = rs.risk_ref
                        ORDER BY rrr.created_at DESC
                        LIMIT 1
                    ) rrs ON true
                    WHERE rs.run_id = %s
                    ORDER BY rs.score DESC NULLS LAST
                    """,
                    (run_id,),
                )
                rows = cur.fetchall()
                risks = [
                    {
                        "id":             r[0],
                        "risk_ref":       r[0],
                        "name":           r[1],
                        "risk_name":      r[2],
                        "narrative":      r[3],
                        "category":       r[4],
                        "score":          float(r[5])  if r[5]  is not None else None,
                        "rag":            r[6],
                        "rag_status":     r[6],
                        "source_framework": r[7],
                        "base_score":     float(r[8])  if r[8]  is not None else None,
                        "delta":          float(r[9])  if r[9]  is not None else None,
                        "velocity":       r[10],
                        "control_env":    r[11],
                        "peer_benchmark": r[12],
                        "current_wording": r[13],
                        "assigned_domain": r[14],
                        "segment_type": r[15],
                        "segment_name": r[16],
                    }
                    for r in rows
                ]
                return {"run_id": run_id, "risks": risks}
    return _run(_do) or {"run_id": None, "risks": []}


def get_risk_scores_for_run(run_id: int) -> list:
    """Return all risk_scores for a run, preferring narrative wording when present."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT risk_ref, risk_name, narrative, category,
                           score, rag_status, source_framework,
                           base_score, delta, velocity, control_env, peer_benchmark,
                           assigned_domain, segment_type, segment_name
                      FROM risk_scores
                     WHERE run_id = %s
                     ORDER BY score DESC NULLS LAST
                    """,
                    (run_id,),
                )
                rows = cur.fetchall()
                return [
                    {
                        "id":             r[0],
                        "risk_ref":       r[0],
                        "name":           r[2] or r[1],   # narrative preferred, else risk_name
                        "risk_name":      r[1],
                        "narrative":      r[2],
                        "current_wording": r[2],          # narrative = last applied reviewed wording
                        "category":       r[3],
                        "score":          float(r[4])  if r[4]  is not None else None,
                        "rag":            r[5],
                        "rag_status":     r[5],
                        "source_framework": r[6],
                        "base_score":     float(r[7])  if r[7]  is not None else None,
                        "delta":          float(r[8])  if r[8]  is not None else None,
                        "velocity":       r[9],
                        "control_env":    r[10],
                        "peer_benchmark": r[11],
                        "assigned_domain": r[12],
                        "segment_type":   r[13],
                        "segment_name":   r[14],
                    }
                    for r in rows
                ]
    return _run(_do) or []


def get_results(tool_name: str, ticker: Optional[str] = None, limit: int = 20) -> list:
    """Deprecated stub."""
    return []


def list_tickers(tool_name: str) -> list:
    """Deprecated stub."""
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Graph relationships — risk-to-risk edges
# ─────────────────────────────────────────────────────────────────────────────

def compute_and_save_risk_relationships(company_id: int, run_id: int, risks: list) -> int:
    """Compute risk-to-risk graph edges from a run's risk set and persist them.

    Relationship types produced:
    - 'correlates_with': risks sharing the same category (bidirectional, strength 0.60–0.75)
    - 'amplifies': highest-scoring/velocity risk in a category → lower-ranked peers
                   (directional, strength proportional to score × velocity factor)

    Returns number of edges upserted.
    """
    if not risks or not company_id:
        return 0

    edges: list = []

    by_category: dict = {}
    for r in risks:
        cat = r.get("category") or "Other"
        by_category.setdefault(cat, []).append(r)

    for cat, cat_risks in by_category.items():
        if len(cat_risks) < 2:
            continue

        sorted_risks = sorted(cat_risks, key=lambda r: float(r.get("score") or 0), reverse=True)
        top = sorted_risks[0]
        top_ref   = top.get("id") or top.get("risk_ref")
        top_score = float(top.get("score") or 0)
        top_vel   = int(top.get("velocity") or 0)
        top_rag   = (top.get("rag") or top.get("rag_status") or "").lower()

        # correlates_with edges between every pair in the same category
        for i, ri in enumerate(sorted_risks):
            ref_i = ri.get("id") or ri.get("risk_ref")
            s_i   = float(ri.get("score") or 0)
            for j in range(i + 1, len(sorted_risks)):
                rj    = sorted_risks[j]
                ref_j = rj.get("id") or rj.get("risk_ref")
                s_j   = float(rj.get("score") or 0)
                strength = round(min(0.75, 0.60 + (s_i + s_j) / 50.0 * 0.15), 3)
                edges.append((company_id, ref_i, ref_j, "correlates_with", strength, "computed", run_id))

        # amplifies edges: top risk → others when it is red or high-velocity
        if top_vel >= 2 or top_rag.startswith("r"):
            amp_base = min(1.0, top_score / 25.0) * (0.50 + top_vel * 0.10)
            for other in sorted_risks[1:]:
                other_ref = other.get("id") or other.get("risk_ref")
                edges.append((company_id, top_ref, other_ref, "amplifies",
                               round(amp_base, 3), "computed", run_id))

    if not edges:
        return 0
    return _upsert_risk_relationship_edges(edges)


def _upsert_risk_relationship_edges(edges: list) -> int:
    """Shared upsert for risk_relationships rows. Each edge tuple:
    (company_id, from_risk_ref, to_risk_ref, relationship_type, strength, source, run_id).
    Used by both the rule-based edges (compute_and_save_risk_relationships) and
    the embedding-based 'similar_to' edges (link_similar_risks_by_embedding)."""
    if not edges:
        return 0

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO risk_relationships
                        (company_id, from_risk_ref, to_risk_ref,
                         relationship_type, strength, source, run_id)
                    VALUES %s
                    ON CONFLICT (company_id, from_risk_ref, to_risk_ref, relationship_type)
                    DO UPDATE SET
                        strength    = EXCLUDED.strength,
                        run_id      = EXCLUDED.run_id,
                        computed_at = NOW()
                    """,
                    edges,
                )
        return len(edges)

    return _run(_do, default=0)


def get_risk_score_rows_for_embedding(run_id: int) -> list:
    """risk_scores PK id + text fields for a run, for computing narrative embeddings.
    Distinct from get_risk_scores_for_run(), which returns display-shaped dicts
    without the surrogate PK the `embeddings` table needs as source_id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, risk_ref, risk_name, category, narrative
                    FROM risk_scores
                    WHERE run_id = %s AND risk_ref IS NOT NULL
                    """,
                    (run_id,),
                )
                return [
                    {"pk_id": r[0], "risk_ref": r[1], "risk_name": r[2],
                     "category": r[3], "narrative": r[4]}
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def link_similar_risks_by_embedding(
    company_id: int, run_id: int, *, max_distance: float = 0.30, max_edges: int = 40,
) -> int:
    """Populate 'similar_to' risk_relationships edges from EMBT_RISK_NARRATIVE
    embeddings already saved for this run (see get_risk_score_rows_for_embedding
    + the caller that embeds and saves them, api_server.py's
    _embed_and_link_risk_narratives).

    Restricted to cross-category pairs — same-category pairs are already
    covered by compute_and_save_risk_relationships's rule-based
    'correlates_with' edges, so this is the one genuinely new signal pgvector
    adds to the graph: conceptual similarity that crosses category
    boundaries a rule-based pass can't see (e.g. a supply-chain risk and a
    geopolitical risk that read similarly but sit in different categories).

    Returns number of edges upserted. No-ops (returns 0) when pgvector is
    unavailable or this run has no risk-narrative embeddings yet.
    """
    if not company_id or not run_id or not (_HAS_PGVECTOR and _PGVECTOR_READY):
        return 0

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH run_risk_emb AS (
                        SELECT rs.id AS pk_id, rs.risk_ref, rs.category, e.embedding
                        FROM risk_scores rs
                        JOIN embeddings e
                          ON e.source_table = 'risk_scores' AND e.source_id = rs.id
                         AND e.content_type = %s
                        WHERE rs.run_id = %s
                    )
                    SELECT a.risk_ref, b.risk_ref, (a.embedding <=> b.embedding) AS distance
                    FROM run_risk_emb a
                    JOIN run_risk_emb b
                      ON a.risk_ref < b.risk_ref
                     AND a.category IS DISTINCT FROM b.category
                    WHERE (a.embedding <=> b.embedding) <= %s
                    ORDER BY distance ASC
                    LIMIT %s
                    """,
                    (EMBT_RISK_NARRATIVE, run_id, max_distance, max_edges),
                )
                pairs = cur.fetchall()
        edges = [
            (company_id, from_ref, to_ref, "similar_to",
             round(max(0.0, min(1.0, 1.0 - float(dist))), 3), "embedding", run_id)
            for from_ref, to_ref, dist in pairs
        ]
        return _upsert_risk_relationship_edges(edges)

    return _run(_do, default=0)


def get_risk_graph(company_id: int, run_id: Optional[int] = None) -> dict:
    """Return graph nodes (latest risk scores) and edges (risk_relationships) for a company.

    When run_id is supplied only that run's scores and edges are returned;
    otherwise the most recent score per risk_ref is used (DISTINCT ON).
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                if run_id:
                    cur.execute(
                        """
                        SELECT risk_ref, risk_name, category, score, rag_status,
                               velocity, control_env, assigned_domain, source_framework
                        FROM risk_scores
                        WHERE run_id = %s
                        ORDER BY score DESC NULLS LAST
                        """,
                        (run_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT DISTINCT ON (rs.risk_ref)
                            rs.risk_ref, rs.risk_name, rs.category, rs.score, rs.rag_status,
                            rs.velocity, rs.control_env, rs.assigned_domain, rs.source_framework
                        FROM risk_scores rs
                        JOIN risk_loop_runs r ON r.id = rs.run_id
                        WHERE r.company_id = %s
                        ORDER BY rs.risk_ref, r.run_at DESC
                        """,
                        (company_id,),
                    )
                risk_rows = cur.fetchall()

                where_clause = "company_id = %s"
                params: list = [company_id]
                if run_id:
                    where_clause += " AND run_id = %s"
                    params.append(run_id)

                cur.execute(
                    f"""
                    SELECT from_risk_ref, to_risk_ref, relationship_type,
                           strength, source, computed_at
                    FROM risk_relationships
                    WHERE {where_clause}
                    ORDER BY strength DESC
                    """,
                    params,
                )
                edge_rows = cur.fetchall()

        nodes = [
            {
                "id": r[0], "name": r[1], "category": r[2],
                "score": float(r[3]) if r[3] is not None else None,
                "rag": r[4], "velocity": r[5], "ce": r[6],
                "domain": r[7], "source_framework": r[8],
            }
            for r in risk_rows
        ]
        edges = [
            {
                "from": e[0], "to": e[1], "type": e[2],
                "strength": float(e[3]) if e[3] is not None else 0,
                "source": e[4],
                "computed_at": e[5].isoformat() if e[5] else None,
            }
            for e in edge_rows
        ]
        return {"nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges)}

    return _run(_do, default={"nodes": [], "edges": [], "node_count": 0, "edge_count": 0})


def get_risk_graph_expanded(
    company_id: int, risk_ref: str, *, max_hops: int = 2, run_id: Optional[int] = None,
) -> dict:
    """Multi-hop traversal from one risk over risk_relationships, via a
    recursive CTE — get_risk_graph() above only ever returns direct (1-hop)
    edges. Answers "what does this risk transitively touch N hops out?" —
    e.g. a Red risk's amplifies edge into a peer, and that peer's similar_to
    edge into a third, differently-categorised risk two hops away that a
    single-hop view would never surface.

    Implementation: BFS out to max_hops over risk_relationships treated as an
    undirected adjacency (recursive CTE, tracking each path's visited-node
    array so cycles stop it rather than looping), which gives the reached
    node set with its hop distance; then a second, non-recursive query pulls
    the induced subgraph — every edge with both endpoints in that node set —
    so an edge between two 2-hop-away nodes is included even though neither
    endpoint is the root.

    Returns {nodes, edges, hops, root, node_count, edge_count}; each node
    carries `hop`, its shortest distance from the root (0 for the root itself).
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH RECURSIVE bfs AS (
                        SELECT %s::varchar AS node, 0 AS depth, ARRAY[%s::varchar] AS visited

                        UNION ALL

                        SELECT
                            CASE WHEN r.from_risk_ref = b.node THEN r.to_risk_ref ELSE r.from_risk_ref END,
                            b.depth + 1,
                            b.visited || (CASE WHEN r.from_risk_ref = b.node THEN r.to_risk_ref ELSE r.from_risk_ref END)
                        FROM risk_relationships r
                        JOIN bfs b
                          ON (r.from_risk_ref = b.node OR r.to_risk_ref = b.node)
                        WHERE r.company_id = %s
                          AND b.depth < %s
                          AND NOT (
                              (CASE WHEN r.from_risk_ref = b.node THEN r.to_risk_ref ELSE r.from_risk_ref END)
                              = ANY(b.visited)
                          )
                    )
                    SELECT node, MIN(depth) AS depth
                    FROM bfs
                    GROUP BY node
                    """,
                    (risk_ref, risk_ref, company_id, max_hops),
                )
                hop_rows = cur.fetchall()
                hop_by_ref = {r[0]: r[1] for r in hop_rows}
                risk_refs = list(hop_by_ref.keys()) or [risk_ref]

                cur.execute(
                    """
                    SELECT from_risk_ref, to_risk_ref, relationship_type, strength, source
                    FROM risk_relationships
                    WHERE company_id = %s
                      AND from_risk_ref = ANY(%s) AND to_risk_ref = ANY(%s)
                    """,
                    (company_id, risk_refs, risk_refs),
                )
                edge_rows = cur.fetchall()

                if run_id:
                    cur.execute(
                        """
                        SELECT risk_ref, risk_name, category, score, rag_status, velocity
                        FROM risk_scores
                        WHERE run_id = %s AND risk_ref = ANY(%s)
                        """,
                        (run_id, risk_refs),
                    )
                else:
                    cur.execute(
                        """
                        SELECT DISTINCT ON (rs.risk_ref)
                            rs.risk_ref, rs.risk_name, rs.category, rs.score, rs.rag_status, rs.velocity
                        FROM risk_scores rs
                        JOIN risk_loop_runs r ON r.id = rs.run_id
                        WHERE r.company_id = %s AND rs.risk_ref = ANY(%s)
                        ORDER BY rs.risk_ref, r.run_at DESC
                        """,
                        (company_id, risk_refs),
                    )
                node_rows = cur.fetchall()

        nodes = [
            {
                "id": r[0], "name": r[1], "category": r[2],
                "score": float(r[3]) if r[3] is not None else None,
                "rag": r[4], "velocity": r[5],
                "hop": hop_by_ref.get(r[0]),
            }
            for r in node_rows
        ]
        edges = [
            {
                "from": e[0], "to": e[1], "type": e[2],
                "strength": float(e[3]) if e[3] is not None else 0,
                "source": e[4],
            }
            for e in edge_rows
        ]
        return {"nodes": nodes, "edges": edges, "hops": max_hops, "root": risk_ref,
                "node_count": len(nodes), "edge_count": len(edges)}

    return _run(_do, default={"nodes": [], "edges": [], "hops": max_hops, "root": risk_ref,
                               "node_count": 0, "edge_count": 0})


# ─────────────────────────────────────────────────────────────────────────────
# COSO ERM 2017 evidence counts — for the Risk Coverage Cube's ERM view
# ─────────────────────────────────────────────────────────────────────────────

def get_erm_evidence_counts(run_id: int, company_id: Optional[int]) -> dict:
    """One row-count (or 0/1 flag) per evidence key in
    risks_as_code.ERM_PRINCIPLES, for a specific run. Every query here
    answers "does a real, persisted artifact exist for this principle" —
    never an inference. risk_coverage_cube.build_erm_evidence() turns these
    counts into evidenced/no_evidence states; principles with `evidence=None`
    in ERM_PRINCIPLES never reach this function at all (no_source, always).

    Two principles are intentionally coarser than a per-run count:
      - risk_appetite (P7): risk_loop_runs.appetite_level is a level
        (conservative/moderate/aggressive), not a quantified threshold —
        evidenced as 1/0, caveated as "coarse" by the caller.
      - Some counts (P6, P14, P18) are company-wide rather than run-scoped,
        because their source tables (rss_articles, risk_relationships) don't
        carry a run_id — they reflect "as of now" state, not this run's
        snapshot, same honest-scoping caveat the Evidence Pack already makes
        for Controls-as-Code/Policy-as-Code (evidence_pack_endpoints.py).
    """
    def _do():
        counts: Dict[str, int] = {}
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM approval_tasks WHERE run_id = %s", (run_id,))
                counts["gate_approvals"] = cur.fetchone()[0]

                if company_id:
                    cur.execute("SELECT COUNT(*) FROM rss_articles WHERE company_id = %s", (company_id,))
                    rss_n = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM fred_correlations WHERE company_id = %s", (company_id,))
                    fred_n = cur.fetchone()[0]
                else:
                    rss_n = fred_n = 0
                counts["market_context"] = rss_n + fred_n

                cur.execute("SELECT appetite_level FROM risk_loop_runs WHERE id = %s", (run_id,))
                row = cur.fetchone()
                counts["risk_appetite"] = 1 if (row and row[0]) else 0

                cur.execute("SELECT COUNT(*) FROM scenario_analyses WHERE run_id = %s", (run_id,))
                scen_n = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM grey_swan_models WHERE run_id = %s", (run_id,))
                gs_n = cur.fetchone()[0]
                counts["scenario_analysis"] = scen_n + gs_n

                cur.execute("SELECT COUNT(*) FROM risk_scores WHERE run_id = %s", (run_id,))
                counts["risk_register"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM risk_scores WHERE run_id = %s AND score IS NOT NULL", (run_id,))
                counts["risk_scoring"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM risk_scores WHERE run_id = %s AND rag_status IS NOT NULL", (run_id,))
                counts["risk_prioritization"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM audit_objectives WHERE run_id = %s", (run_id,))
                counts["audit_objectives"] = cur.fetchone()[0]

                if company_id:
                    cur.execute("SELECT COUNT(*) FROM risk_relationships WHERE company_id = %s", (company_id,))
                    counts["risk_graph"] = cur.fetchone()[0]
                else:
                    counts["risk_graph"] = 0

                cur.execute("SELECT COUNT(*) FROM rss_signals WHERE run_id = %s AND velocity_delta IS NOT NULL", (run_id,))
                rss_sig_n = cur.fetchone()[0]
                if company_id:
                    cur.execute("SELECT COUNT(*) FROM edgar_8k_events WHERE company_id = %s", (company_id,))
                    events_n = cur.fetchone()[0]
                else:
                    events_n = 0
                counts["change_signals"] = rss_sig_n + events_n

                cur.execute("SELECT COUNT(*) FROM backtest_metrics WHERE run_id = %s", (run_id,))
                counts["backtest_review"] = cur.fetchone()[0]

                cur.execute(
                    "SELECT COUNT(*) FROM backtest_metrics WHERE run_id = %s AND calibrated_weight IS NOT NULL",
                    (run_id,),
                )
                counts["loop_calibration"] = cur.fetchone()[0]

                cur.execute("SELECT data_mode FROM risk_loop_runs WHERE id = %s", (run_id,))
                row = cur.fetchone()
                counts["mcp_ingestion"] = 1 if (row and row[0] == "mcp") else 0

                cur.execute(
                    "SELECT COUNT(*) FROM ai_analyses WHERE run_id = %s AND kind = 'persona_brief'",
                    (run_id,),
                )
                counts["notifications"] = cur.fetchone()[0]

                cur.execute(
                    "SELECT COUNT(*) FROM ai_analyses WHERE run_id = %s AND kind = 'audit_report'",
                    (run_id,),
                )
                ai_report_n = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM risks_as_code_artifacts WHERE run_id = %s", (run_id,))
                rac_n = cur.fetchone()[0]
                counts["audit_reporting"] = ai_report_n + rac_n
        return counts
    return _run(_do, default={}) or {}


# ─────────────────────────────────────────────────────────────────────────────
# Graph relationships — scenario → risk impact edges
# ─────────────────────────────────────────────────────────────────────────────

def save_scenario_risk_impacts(run_id: int, risks: list, scenario_dict: dict) -> int:
    """Link each scenario_analyses row to the risks it most affects.

    Infers relevance by matching risk name / category keywords against the
    scenario narrative.  Creates a FK edge with an estimated impact_multiplier:
    bear scenario → 1.5×, base → 1.25×, bull → 1.10×.

    Returns number of impact links created.
    """
    if not risks or not scenario_dict:
        return 0

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, scenario, narrative FROM scenario_analyses WHERE run_id = %s",
                    (run_id,),
                )
                scenario_rows = cur.fetchall()
                if not scenario_rows:
                    return 0

                rows = []
                for sid, scenario_name, narrative in scenario_rows:
                    narrative_lower = (narrative or "").lower()
                    scen_lower = (scenario_name or "").lower()
                    mult = 1.50 if "bear" in scen_lower else 1.10 if "bull" in scen_lower else 1.25

                    for risk in risks:
                        risk_ref  = risk.get("id") or risk.get("risk_ref")
                        risk_name = (risk.get("name") or risk.get("risk_name") or "").lower()
                        risk_cat  = (risk.get("category") or "").lower()

                        # Match if ≥2 meaningful words from the risk name appear in the narrative
                        keywords  = [w for w in risk_name.split() if len(w) > 4]
                        keywords += [risk_cat]
                        match_count = sum(1 for kw in keywords if kw and kw in narrative_lower)

                        if match_count >= 2 or (risk_cat and risk_cat in narrative_lower):
                            rows.append((sid, risk_ref, mult,
                                         f"Scenario '{scenario_name}' references this risk area"))

                if not rows:
                    return 0

                execute_values(
                    cur,
                    """
                    INSERT INTO scenario_risk_impacts
                        (scenario_id, risk_ref, impact_multiplier, impact_narrative)
                    VALUES %s
                    ON CONFLICT (scenario_id, risk_ref) DO NOTHING
                    """,
                    rows,
                )
                return len(rows)

    return _run(_do, default=0)


# ─────────────────────────────────────────────────────────────────────────────
# Graph relationships — CEM event → risk / control FK edges
# ─────────────────────────────────────────────────────────────────────────────

def save_cem_event_risk_links(run_id: int, events: list) -> int:
    """Resolve CEM event risk_label / control text → structured FK edges.

    For each cem_events row in this run:
    - Fuzzy-matches risk_label against risk_scores.risk_name to find risk_ref
    - Fuzzy-matches control text against controls_library.name to find control_ref

    Inserts into cem_event_risk_links for graph traversal.
    Returns number of link rows created.
    """
    if not events:
        return 0

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, risk_label, control FROM cem_events WHERE run_id = %s",
                    (run_id,),
                )
                cem_rows = cur.fetchall()
                if not cem_rows:
                    return 0

                cur.execute(
                    "SELECT risk_ref, risk_name FROM risk_scores WHERE run_id = %s",
                    (run_id,),
                )
                risk_lookup = {r[0]: r[1].lower() for r in cur.fetchall()}

                cur.execute("SELECT control_ref, name FROM controls_library")
                ctrl_rows = cur.fetchall()
                ctrl_name_idx = [(ref, name.lower()) for ref, name in ctrl_rows]

                rows = []
                for eid, risk_label, ctrl_name in cem_rows:
                    label_lower = (risk_label or "").lower()
                    ctrl_lower  = (ctrl_name  or "").lower()

                    # Fuzzy risk match: substring overlap
                    best_risk_ref = None
                    for ref, rname in risk_lookup.items():
                        if label_lower and (label_lower in rname or rname in label_lower):
                            best_risk_ref = ref
                            break

                    if not best_risk_ref:
                        # Word-level fallback
                        words = [w for w in label_lower.split() if len(w) > 4]
                        best_ref, best_hits = None, 0
                        for ref, rname in risk_lookup.items():
                            hits = sum(1 for w in words if w in rname)
                            if hits > best_hits:
                                best_ref, best_hits = ref, hits
                        if best_hits >= 2:
                            best_risk_ref = best_ref

                    if not best_risk_ref:
                        continue

                    # Fuzzy control match
                    best_ctrl_ref = None
                    for ref, cname in ctrl_name_idx:
                        if ctrl_lower and (ctrl_lower in cname or cname in ctrl_lower):
                            best_ctrl_ref = ref
                            break

                    rows.append((eid, best_risk_ref, best_ctrl_ref, "affected"))

                if not rows:
                    return 0

                execute_values(
                    cur,
                    """
                    INSERT INTO cem_event_risk_links
                        (cem_event_id, risk_ref, control_ref, link_type)
                    VALUES %s
                    ON CONFLICT (cem_event_id, risk_ref) DO NOTHING
                    """,
                    rows,
                )
                return len(rows)

    return _run(_do, default=0)


# ─────────────────────────────────────────────────────────────────────────────
# Domain persistence
# ─────────────────────────────────────────────────────────────────────────────

def bulk_save_risk_domains(run_id: int, risks: list) -> int:
    """Persist domain assignments to risk_scores.assigned_domain.

    risks: [{"ref": risk_ref_or_none, "name": risk_name, "domain": domain_name}, ...]

    Matches by risk_ref when the row has one; falls back to risk_name for
    rows where risk_ref is NULL in the database. A plain `risk_ref = %s`
    match alone silently updates zero rows for NULL-ref risks — SQL's `=`
    never matches NULL, no error raised — which is exactly what made this
    function a no-op for every quant/baseline risk (risk_scores rows with no
    risk_ref assigned, ~2/3 of all risks in practice) despite the caller
    believing persistence succeeded. `ref` is trusted only when non-empty;
    an empty string is treated the same as None (the frontend's `r.id ||
    r.risk_ref` fallback can hand back a synthetic id that isn't a real
    risk_ref column value at all, so name-matching for anything not
    confirmed to be a real ref is the safer default).
    """
    if not risks:
        return 0

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                updated = 0
                for r in risks:
                    ref = r.get("ref") or None
                    name = r.get("name") or ""
                    domain = r.get("domain")
                    if not domain:
                        continue
                    cur.execute(
                        """
                        UPDATE risk_scores
                        SET assigned_domain = %s
                        WHERE run_id = %s
                          AND (
                            (risk_ref IS NOT NULL AND risk_ref = %s)
                            OR (risk_ref IS NULL AND risk_name = %s)
                          )
                        """,
                        (domain, run_id, ref, name),
                    )
                    updated += cur.rowcount
                return updated

    return _run(_do, default=0)


# ─────────────────────────────────────────────────────────────────────────────
# App config (key-value JSON store for MATRIX_FRAMEWORKS, PRESET_FRAMEWORKS, etc.)
# ─────────────────────────────────────────────────────────────────────────────

def get_app_config(key: str, default=None):
    """Return the parsed JSON value for a config key, or default if not set."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value_json FROM app_config WHERE key = %s", (key,))
                row = cur.fetchone()
                return row[0] if row else None
    result = _run(_do)
    return result if result is not None else default


def get_app_configs(keys: list) -> dict:
    """Batched get_app_config: one round trip for several keys instead of one
    per key. Callers that need multiple config values (e.g. matrix/preset/
    hidden frameworks together) used to pay for N sequential DB round trips
    where one query answers all of them. Missing keys are simply absent from
    the returned dict — callers apply their own per-key default."""
    if not keys:
        return {}
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value_json FROM app_config WHERE key = ANY(%s)", (list(keys),))
                return {row[0]: row[1] for row in cur.fetchall()}
    return _run(_do) or {}


def set_app_config(key: str, value) -> bool:
    """Upsert a JSON config value. Returns True on success."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app_config (key, value_json)
                    VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET
                        value_json = EXCLUDED.value_json,
                        updated_at = NOW()
                    """,
                    (key, Json(value)),
                )
        return True
    return _run(_do, default=False) or False


def delete_app_config(key: str) -> bool:
    """Remove a config key. Returns True on success (including if it never existed)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM app_config WHERE key = %s", (key,))
        return True
    return _run(_do, default=False) or False


# ─────────────────────────────────────────────────────────────────────────────
# Controls library (seed + query)
# ─────────────────────────────────────────────────────────────────────────────

def seed_controls_library(controls: list) -> int:
    """Insert default controls into controls_library (skips existing refs). Returns rows inserted."""
    if not controls:
        return 0
    def _do():
        rows = [
            (
                c.get("ref") or c.get("control_ref", ""),
                c.get("framework", ""),
                c.get("name") or c.get("control_name", ""),
                c.get("description") or c.get("desc", ""),
                c.get("category", ""),
                c.get("domain", ""),
            )
            for c in controls
            if (c.get("ref") or c.get("control_ref")) and (c.get("name") or c.get("control_name"))
        ]
        if not rows:
            return 0
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO controls_library
                        (control_ref, framework, name, description, category, domain)
                    VALUES %s
                    ON CONFLICT (control_ref) DO NOTHING
                    """,
                    rows,
                )
                return cur.rowcount
    return _run(_do, default=0) or 0


def get_controls_library() -> list:
    """Return all controls from the controls_library table."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT control_ref, framework, name, description, category, domain, tags, pac_control_id "
                    "FROM controls_library ORDER BY control_ref"
                )
                return [
                    {
                        "ref":            r[0],
                        "framework":      r[1] or "",
                        "name":           r[2],
                        "description":    r[3] or "",
                        "category":       r[4] or "",
                        "domain":         r[5] or "",
                        "tags":           r[6] or [],
                        "pac_control_id": r[7],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_control_by_ref(control_ref: str) -> Optional[dict]:
    """Single-row lookup for one Risk & Control register entry.

    Split out of get_controls_library() because every edit to ONE control
    (rename, description tweak, PaC link) only ever needed to check that row's
    existence and read its current values — but the only lookup available was
    the full-table SELECT, so every such edit paid for fetching every OTHER
    row in the library too. That cost scales with the library's size, and the
    library only grows (each register import adds its own controls), so a
    save that felt fine at 40 rows keeps getting slower as more registers are
    imported. A WHERE control_ref = %s lookup costs the same one round trip
    regardless of table size.
    """
    ref = (control_ref or "").strip().upper()
    if not ref:
        return None
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT control_ref, framework, name, description, category, domain, tags, pac_control_id "
                    "FROM controls_library WHERE control_ref = %s",
                    (ref,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "ref": row[0], "framework": row[1] or "", "name": row[2],
                    "description": row[3] or "", "category": row[4] or "",
                    "domain": row[5] or "", "tags": row[6] or [], "pac_control_id": row[7],
                }
    return _run(_do)


def upsert_control(control: dict) -> bool:
    """Insert or update a single control in the controls_library. Returns True on success."""
    ref = (control.get("ref") or control.get("control_ref", "")).strip().upper()
    if not ref:
        return False
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO controls_library
                        (control_ref, framework, name, description, category, domain, pac_control_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (control_ref) DO UPDATE SET
                        framework      = COALESCE(EXCLUDED.framework,   controls_library.framework),
                        name           = EXCLUDED.name,
                        description    = COALESCE(EXCLUDED.description, controls_library.description),
                        category       = COALESCE(EXCLUDED.category,    controls_library.category),
                        domain         = COALESCE(EXCLUDED.domain,      controls_library.domain),
                        pac_control_id = COALESCE(EXCLUDED.pac_control_id, controls_library.pac_control_id)
                    """,
                    (
                        ref,
                        control.get("framework", "Custom"),
                        control.get("name", ""),
                        control.get("description") or control.get("desc", ""),
                        control.get("category", "Custom"),
                        control.get("domain", "Custom"),
                        control.get("pac_control_id") or None,
                    ),
                )
        return True
    return _run(_do, default=False) or False


def set_control_pac_link(control_ref: str, pac_control_id: Optional[str]) -> bool:
    """Set (or clear, when pac_control_id is None/empty) the controls_library ->
    controls_catalog link for an existing control. Separate from upsert_control
    so linking doesn't require re-supplying every other field (which would risk
    clobbering them) and can explicitly null out an existing link."""
    ref = (control_ref or "").strip().upper()
    if not ref:
        return False
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE controls_library SET pac_control_id = %s WHERE control_ref = %s",
                    (pac_control_id or None, ref),
                )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


# ─────────────────────────────────────────────────────────────────────────────
# CEM event templates
# ─────────────────────────────────────────────────────────────────────────────

def seed_cem_event_templates(templates: list) -> int:
    """Insert default CEM event templates (skips duplicates). Returns rows inserted."""
    if not templates:
        return 0
    def _do():
        rows = [
            (
                t.get("control", ""),
                t.get("area", ""),
                t.get("risk") or t.get("risk_label", ""),
                t.get("severity", "P2"),
                t.get("exposure"),
                t.get("category"),
                t.get("rc") or t.get("rc_narrative"),
                i,
            )
            for i, t in enumerate(templates)
            if t.get("control") and t.get("area")
        ]
        if not rows:
            return 0
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO cem_event_templates
                        (control, area, risk_label, severity, exposure, category, rc_narrative, sort_order)
                    VALUES %s
                    ON CONFLICT (control, area) DO NOTHING
                    """,
                    rows,
                )
                return cur.rowcount
    return _run(_do, default=0) or 0


def get_cem_event_templates(active_only: bool = True) -> list:
    """Return CEM event templates, ordered by sort_order. exposure_amount_m/
    exposure_source are the FAIR-quantified companions to the qualitative
    `exposure` label — see the cem_event_templates migration block and
    fair_tool.py — null until someone runs Risk Quantification against this
    template's control/area."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                q = (
                    "SELECT id, control, area, risk_label, severity, exposure, category, rc_narrative, "
                    "exposure_amount_m, exposure_source "
                    "FROM cem_event_templates"
                )
                if active_only:
                    q += " WHERE is_active = TRUE"
                q += " ORDER BY sort_order, id"
                cur.execute(q)
                return [
                    {
                        "id":       r[0],
                        "control":  r[1],
                        "area":     r[2],
                        "risk":     r[3],
                        "severity": r[4],
                        "exposure": r[5],
                        "category": r[6],
                        "rc":       r[7],
                        "exposure_amount_m": float(r[8]) if r[8] is not None else None,
                        "exposure_source":   r[9],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def upsert_cem_event_template(template: dict) -> Optional[int]:
    """Insert or update a CEM event template. Returns the row id."""
    if not template.get("control") or not template.get("area"):
        return None
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cem_event_templates
                        (control, area, risk_label, severity, exposure, category, rc_narrative, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (control, area) DO UPDATE SET
                        risk_label   = EXCLUDED.risk_label,
                        severity     = EXCLUDED.severity,
                        exposure     = COALESCE(EXCLUDED.exposure,     cem_event_templates.exposure),
                        category     = COALESCE(EXCLUDED.category,     cem_event_templates.category),
                        rc_narrative = COALESCE(EXCLUDED.rc_narrative, cem_event_templates.rc_narrative),
                        updated_at   = NOW()
                    RETURNING id
                    """,
                    (
                        template["control"],
                        template["area"],
                        template.get("risk") or template.get("risk_label", ""),
                        template.get("severity", "P2"),
                        template.get("exposure"),
                        template.get("category"),
                        template.get("rc") or template.get("rc_narrative"),
                    ),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def update_cem_event_template_exposure(template_id: int, exposure_amount_m: float, exposure_source: str) -> bool:
    """Write a fair_tool.py quantification result back onto a CEM event
    template (see cem_event_templates.exposure_amount_m). Does not touch the
    qualitative `exposure` label column — that stays whatever the preparer
    typed."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE cem_event_templates SET exposure_amount_m = %s, exposure_source = %s, "
                    "updated_at = NOW() WHERE id = %s",
                    (exposure_amount_m, exposure_source, template_id),
                )
                return cur.rowcount > 0
    return bool(_run(_do, default=False))


def update_cem_event_exposure(cem_event_id: int, exposure_amount_m: float, exposure_source: str) -> bool:
    """Same as update_cem_event_template_exposure but for a real, run-scoped
    cem_events row (an actual 8-K-derived or Loop-Report event, not a
    template)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE cem_events SET exposure_amount_m = %s, exposure_source = %s WHERE id = %s",
                    (exposure_amount_m, exposure_source, cem_event_id),
                )
                return cur.rowcount > 0
    return bool(_run(_do, default=False))


# ─────────────────────────────────────────────────────────────────────────────
# FAIR (Factor Analysis of Information Risk) quantification runs
# ─────────────────────────────────────────────────────────────────────────────

def save_fair_quantification(q: dict) -> Optional[int]:
    """Persist one fair_tool.py Monte Carlo run. See the fair_quantifications
    migration block for column meaning. Never overwrites a prior run for the
    same resource — each call is a new row, same write-once convention as
    risk_loop_runs, so ALE-over-time is answerable without a separate
    history table."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fair_quantifications
                        (resource_type, resource_ref, company_id, run_id, control_id, process,
                         tef_mean, tef_source, loss_min, loss_likely, loss_max, magnitude_source,
                         simulations, ale, p10, p50, p90, p95, exceedance_curve, created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        q["resource_type"], str(q["resource_ref"]), q.get("company_id"), q.get("run_id"),
                        q.get("control_id"), q.get("process"),
                        q["tef_mean"], q["tef_source"],
                        q.get("loss_min"), q.get("loss_likely"), q.get("loss_max"), q["magnitude_source"],
                        q["simulations"], q["ale"], q.get("p10"), q.get("p50"), q.get("p90"), q.get("p95"),
                        Json(q.get("exceedance_curve")) if q.get("exceedance_curve") is not None else None,
                        q.get("created_by"),
                    ),
                )
                return cur.fetchone()[0]
    return _run(_do)


def _row_to_fair_quant(r) -> dict:
    return {
        "id": r[0], "resource_type": r[1], "resource_ref": r[2], "company_id": r[3], "run_id": r[4],
        "control_id": r[5], "process": r[6],
        "tef_mean": float(r[7]) if r[7] is not None else None, "tef_source": r[8],
        "loss_min": float(r[9]) if r[9] is not None else None,
        "loss_likely": float(r[10]) if r[10] is not None else None,
        "loss_max": float(r[11]) if r[11] is not None else None,
        "magnitude_source": r[12], "simulations": r[13],
        "ale": float(r[14]) if r[14] is not None else None,
        "p10": float(r[15]) if r[15] is not None else None, "p50": float(r[16]) if r[16] is not None else None,
        "p90": float(r[17]) if r[17] is not None else None, "p95": float(r[18]) if r[18] is not None else None,
        "exceedance_curve": r[19], "created_at": r[20].isoformat() if r[20] else None, "created_by": r[21],
    }


_FAIR_QUANT_COLUMNS = (
    "id, resource_type, resource_ref, company_id, run_id, control_id, process, "
    "tef_mean, tef_source, loss_min, loss_likely, loss_max, magnitude_source, "
    "simulations, ale, p10, p50, p90, p95, exceedance_curve, created_at, created_by"
)


def get_latest_fair_quantification(resource_type: str, resource_ref) -> Optional[dict]:
    """Most recent FAIR run for one resource (a CEM event, a SOX process, a
    risk, or a control) — what a detail panel shows without re-running the
    simulation."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_FAIR_QUANT_COLUMNS} FROM fair_quantifications "
                    "WHERE resource_type = %s AND resource_ref = %s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (resource_type, str(resource_ref)),
                )
                row = cur.fetchone()
                return _row_to_fair_quant(row) if row else None
    return _run(_do)


def list_fair_quantifications(resource_type: Optional[str] = None, days: int = 365, limit: int = 500) -> list:
    """Every FAIR run in the trailing window, most recent first — the feed
    behind the ALE-by-control summary and Risk Quantification's history
    view."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                q = f"SELECT {_FAIR_QUANT_COLUMNS} FROM fair_quantifications WHERE created_at > NOW() - (%s || ' days')::interval"
                params: list = [days]
                if resource_type:
                    q += " AND resource_type = %s"
                    params.append(resource_type)
                q += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)
                cur.execute(q, tuple(params))
                return [_row_to_fair_quant(r) for r in cur.fetchall()]
    return _run(_do) or []


def get_fair_ale_summary(days: int = 365) -> list:
    """Latest ALE per (resource_type, resource_ref), highest first — 'what's
    the most expensive open risk right now' for the Risk Quantification
    dashboard. Uses DISTINCT ON to keep only the newest run per resource
    within the window, so a resource quantified twice isn't double-counted."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT DISTINCT ON (resource_type, resource_ref) {_FAIR_QUANT_COLUMNS}
                    FROM fair_quantifications
                    WHERE created_at > NOW() - (%s || ' days')::interval
                    ORDER BY resource_type, resource_ref, created_at DESC
                    """,
                    (days,),
                )
                rows = [_row_to_fair_quant(r) for r in cur.fetchall()]
                rows.sort(key=lambda r: r["ale"] or 0, reverse=True)
                return rows
    return _run(_do) or []


# ─────────────────────────────────────────────────────────────────────────────
# Ticker → CIK seed (populates companies table from a static map)
# ─────────────────────────────────────────────────────────────────────────────

def seed_ticker_cik_map(ticker_cik: dict) -> int:
    """Upsert minimal company rows from a ticker→CIK dict. Returns rows affected."""
    if not ticker_cik:
        return 0
    def _do():
        rows = [
            (tick.upper(), cik.lstrip("0") or "0", tick.upper())
            for tick, cik in ticker_cik.items()
        ]
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO companies (ticker, cik, company_name)
                    VALUES %s
                    ON CONFLICT (ticker) DO UPDATE SET
                        cik = COALESCE(EXCLUDED.cik, companies.cik),
                        updated_at = NOW()
                    """,
                    rows,
                )
                return cur.rowcount
    return _run(_do, default=0) or 0


def get_cik_for_ticker(ticker: str) -> Optional[str]:
    """Return the CIK (zero-padded to 10 digits) for a ticker, or None if not found."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cik FROM companies WHERE ticker = %s AND cik IS NOT NULL",
                    (ticker.upper(),),
                )
                row = cur.fetchone()
                if not row or not row[0]:
                    return None
                return str(row[0]).zfill(10)
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# Controls-as-Code artifacts
# ─────────────────────────────────────────────────────────────────────────────

def save_controls_as_code_artifact(content_rego: str, ticker: Optional[str] = None, run_id: Optional[int] = None) -> Optional[int]:
    """Persist a Controls-as-Code Rego artifact. Returns the row id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO controls_as_code_artifacts (ticker, run_id, content_rego, generated_at)
                    VALUES (%s, %s, %s, NOW())
                    RETURNING id
                    """,
                    (ticker.upper() if ticker else None, run_id, content_rego),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def get_latest_cac_artifact(ticker: Optional[str] = None) -> Optional[dict]:
    """Return the most recent CaC artifact (optionally filtered by ticker)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                if ticker:
                    cur.execute(
                        "SELECT id, ticker, run_id, content_rego, generated_at "
                        "FROM controls_as_code_artifacts WHERE ticker = %s "
                        "ORDER BY generated_at DESC LIMIT 1",
                        (ticker.upper(),),
                    )
                else:
                    cur.execute(
                        "SELECT id, ticker, run_id, content_rego, generated_at "
                        "FROM controls_as_code_artifacts ORDER BY generated_at DESC LIMIT 1"
                    )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0], "ticker": row[1], "run_id": row[2],
                    "content_rego": row[3],
                    "generated_at": row[4].isoformat() if row[4] else None,
                }
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# Policy-as-Code modules
# ─────────────────────────────────────────────────────────────────────────────

def save_pac_module(process: str, module_name: str, rego_content: str, version: str = "1.0",
                     source_format: str = "rego") -> Optional[int]:
    """Insert a new versioned Rego module for a process. Returns the row id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pac_policy_modules
                        (process, module_name, rego_content, version, source_format, last_revised_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    RETURNING id
                    """,
                    (process, module_name, rego_content, version, source_format),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def get_latest_pac_module(process: str) -> Optional[dict]:
    """Return the most recent module version for a process, with its approvers."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, process, module_name, rego_content, version, source_format, last_revised_at, created_at
                    FROM pac_policy_modules
                    WHERE process = %s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (process,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                module_id = row[0]
                cur.execute(
                    "SELECT id, approver, role, approved_at FROM pac_policy_approvals WHERE module_id = %s ORDER BY approved_at",
                    (module_id,),
                )
                approvals = [
                    {"id": a[0], "approver": a[1], "role": a[2],
                     "approved_at": a[3].isoformat() if a[3] else None}
                    for a in cur.fetchall()
                ]
                return {
                    "id": module_id, "process": row[1], "module_name": row[2],
                    "rego_content": row[3], "version": row[4], "source_format": row[5],
                    "last_revised_at": row[6].isoformat() if row[6] else None,
                    "created_at": row[7].isoformat() if row[7] else None,
                    "approvals": approvals,
                }
    return _run(_do)


def get_latest_approved_pac_module(process: str) -> Optional[dict]:
    """The newest module version for a process that has EVER received an
    approval sign-off — may be OLDER than get_latest_pac_module's result,
    since saving a new draft (PUT /pac/modules/{process}) doesn't require or
    wait for approval before becoming the version real adjudication
    evaluates. Used by pac_approval_drift.py to detect exactly that gap:
    the live module and the latest APPROVED module are not necessarily the
    same row."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT m.id, m.process, m.module_name, m.rego_content, m.version,
                           m.source_format, m.last_revised_at, m.created_at
                    FROM pac_policy_modules m
                    WHERE m.process = %s
                      AND EXISTS (SELECT 1 FROM pac_policy_approvals a WHERE a.module_id = m.id)
                    ORDER BY m.created_at DESC
                    LIMIT 1
                    """,
                    (process,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0], "process": row[1], "module_name": row[2],
                    "rego_content": row[3], "version": row[4], "source_format": row[5],
                    "last_revised_at": row[6].isoformat() if row[6] else None,
                    "created_at": row[7].isoformat() if row[7] else None,
                }
    return _run(_do)


def list_pac_modules() -> list:
    """Return the latest module for every process that has been saved."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (process)
                        id, process, module_name, version, source_format, last_revised_at, created_at
                    FROM pac_policy_modules
                    ORDER BY process, created_at DESC
                    """
                )
                rows = cur.fetchall()
                result = []
                for row in rows:
                    module_id = row[0]
                    cur.execute(
                        "SELECT approver, role, approved_at FROM pac_policy_approvals WHERE module_id = %s ORDER BY approved_at",
                        (module_id,),
                    )
                    approvals = [
                        {"approver": a[0], "role": a[1],
                         "approved_at": a[2].isoformat() if a[2] else None}
                        for a in cur.fetchall()
                    ]
                    result.append({
                        "id": module_id, "process": row[1], "module_name": row[2],
                        "version": row[3], "source_format": row[4],
                        "last_revised_at": row[5].isoformat() if row[5] else None,
                        "created_at": row[6].isoformat() if row[6] else None,
                        "approvals": approvals,
                    })
                return result
    return _run(_do) or []


def upsert_catalog_control(control_id: str, name: str, description: Optional[str] = None,
                            process: Optional[str] = None, source: str = "manual") -> bool:
    """Insert or update a controls_catalog entry. Used both by the one-time
    startup seed and by cac_from_pac (self-registers newly generated CaC
    controls that reuse a real PaC control_id).

    Named distinctly from the pre-existing upsert_control(dict) (controls_library
    table, used by risk_register_endpoints.py) to avoid shadowing it."""
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO controls_catalog (control_id, name, description, process, source)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (control_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = COALESCE(EXCLUDED.description, controls_catalog.description),
                        process = COALESCE(EXCLUDED.process, controls_catalog.process),
                        updated_at = NOW()
                    """,
                    (control_id, name, description, process, source),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("upsert_control error (control_id=%s): %s", control_id, exc)
        return False


def delete_stale_catalog_controls(valid_processes: list[str]) -> int:
    """Remove pac_rego-sourced controls_catalog rows for a process that no
    longer exists in _REGO_DEFAULTS — the counterpart _seed_controls_catalog
    never had, since upsert_catalog_control only inserts/updates and never
    deletes. Without this, removing a whole built-in process (as happened
    when DevOps Monitoring was retired) leaves its old DEVOPS-*/etc. rows in
    the catalog forever, showing up as phantom entries in any process- or
    control-driven view (framework crosswalk, compliance scorecard, domain
    roll-ups).

    Scoped to whole missing PROCESSES, not individual missing control_ids —
    an admin-edited/saved custom Rego module for a still-valid process can
    legitimately differ from _REGO_DEFAULTS's built-in text (extract_control_
    ids_from_defaults only scans the built-in defaults, not saved custom
    versions), so diffing at the control_id level would risk deleting rows
    for rules that still exist in a live, edited module. source='manual'
    rows (RaC's business controls) are never touched — they aren't process-
    seeded from Rego at all.
    """
    if not valid_processes:
        return 0  # refuse to wipe every pac_rego row on an empty/misconfigured call
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM controls_catalog
                    WHERE source = 'pac_rego' AND process != ALL(%s)
                    """,
                    (list(valid_processes),),
                )
                n = cur.rowcount
            conn.commit()
        return n
    return _run(_do, default=0) or 0


def list_controls(process: Optional[str] = None, source: Optional[str] = None) -> list:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                clauses, params = [], []
                if process:
                    clauses.append("process = %s"); params.append(process)
                if source:
                    clauses.append("source = %s"); params.append(source)
                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                cur.execute(
                    f"SELECT control_id, name, description, process, source, created_at, "
                    f"       last_fired_at, last_verified_at, last_test_passed, "
                    f"       soc2_criteria, nist_800_53, iso_27001, coso_component, icif_component "
                    f"FROM controls_catalog {where} ORDER BY control_id",
                    params,
                )
                return [
                    {
                        "control_id": r[0], "name": r[1], "description": r[2],
                        "process": r[3], "source": r[4],
                        "created_at": r[5].isoformat() if r[5] else None,
                        "last_fired_at": r[6].isoformat() if r[6] else None,
                        "last_verified_at": r[7].isoformat() if r[7] else None,
                        "last_test_passed": r[8],
                        "soc2_criteria": r[9] or [], "nist_800_53": r[10] or [],
                        "iso_27001": r[11] or [], "coso_component": r[12],
                        "icif_component": r[13],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_control(control_id: str) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT control_id, name, description, process, source, created_at, "
                    "       last_fired_at, last_verified_at, last_test_passed, "
                    "       soc2_criteria, nist_800_53, iso_27001, coso_component, icif_component "
                    "FROM controls_catalog WHERE control_id = %s",
                    (control_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "control_id": row[0], "name": row[1], "description": row[2],
                    "process": row[3], "source": row[4],
                    "created_at": row[5].isoformat() if row[5] else None,
                    "last_fired_at": row[6].isoformat() if row[6] else None,
                    "last_verified_at": row[7].isoformat() if row[7] else None,
                    "last_test_passed": row[8],
                    "soc2_criteria": row[9] or [], "nist_800_53": row[10] or [],
                    "iso_27001": row[11] or [], "coso_component": row[12],
                    "icif_component": row[13],
                }
    return _run(_do)


def upsert_framework_mapping(control_id: str, soc2_criteria: Optional[list] = None,
                              nist_800_53: Optional[list] = None, iso_27001: Optional[list] = None,
                              coso_component: Optional[str] = None,
                              icif_component: Optional[str] = None) -> bool:
    """Set framework crosswalk metadata for one control. No-ops (returns
    False) if control_id doesn't exist yet — mappings are seeded after
    controls_catalog itself (see seed_framework_mappings, called after
    _seed_controls_catalog in api_server.py's startup sequence).

    coso_component and icif_component are independent fields (a control's ERM
    2017 component vs. its IC-IF 2013 component — see framework_mappings.py's
    2026-08-26 note); neither is derived from the other."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE controls_catalog
                    SET soc2_criteria = %s, nist_800_53 = %s, iso_27001 = %s,
                        coso_component = %s, icif_component = %s, updated_at = NOW()
                    WHERE control_id = %s
                    """,
                    (soc2_criteria, nist_800_53, iso_27001, coso_component, icif_component, control_id),
                )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


_SCORECARD_ARRAY_COL = {"soc2": "soc2_criteria", "nist_800_53": "nist_800_53", "iso_27001": "iso_27001"}


def _aggregate_scorecard_rows(rows: list, stale_days: int = 30) -> list:
    """
    Pure aggregation step, split out of get_compliance_scorecard so it's
    unit-testable with fake rows — no DB connection needed (mirrors
    _parse_opa_bindings' reasoning in pac_endpoints.py: a bug in aggregation
    logic shouldn't require a live Postgres connection to catch).

    rows: [(criterion, control_id, last_test_passed, last_fired_at), ...] —
    last_fired_at is a timezone-aware datetime or None.

    Deliberately two different numbers, never conflated: "mapped" (a human
    curated this crosswalk in framework_mappings.py) and "verified" (P0's
    negative-testing assurance metadata actually backs it up). A criterion
    can be fully mapped and 0% verified — that's the honest state to
    surface, not a green checkmark a mapping alone hasn't earned.
    """
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    by_criterion: dict[str, dict] = {}
    for criterion, control_id, last_test_passed, last_fired_at in rows:
        bucket = by_criterion.setdefault(criterion, {"criterion": criterion, "control_ids": [], "verified_control_ids": []})
        bucket["control_ids"].append(control_id)
        fired_recently = bool(last_fired_at) and last_fired_at > cutoff
        if last_test_passed or fired_recently:
            bucket["verified_control_ids"].append(control_id)

    criteria = []
    for c in sorted(by_criterion.values(), key=lambda x: x["criterion"]):
        criteria.append({
            "criterion": c["criterion"],
            "control_ids": sorted(set(c["control_ids"])),
            "total_controls": len(set(c["control_ids"])),
            "verified_controls": len(set(c["verified_control_ids"])),
        })
    return criteria


def get_compliance_scorecard(framework: str, stale_days: int = 30) -> dict:
    """
    Executive Compliance Scorecard: for one framework
    ('soc2' | 'nist_800_53' | 'iso_27001' | 'coso'), every distinct
    criterion any control is mapped to, how many controls map to it, and
    how many are actually PROVEN working — see _aggregate_scorecard_rows.
    """
    if framework not in ("soc2", "nist_800_53", "iso_27001", "coso"):
        return {"framework": framework, "criteria": [], "error": f"Unknown framework '{framework}'"}

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                if framework == "coso":
                    cur.execute(
                        """
                        SELECT coso_component, control_id, last_test_passed, last_fired_at
                        FROM controls_catalog WHERE coso_component IS NOT NULL
                        """,
                    )
                else:
                    array_col = _SCORECARD_ARRAY_COL[framework]
                    cur.execute(
                        f"""
                        SELECT unnest({array_col}) AS criterion, control_id, last_test_passed, last_fired_at
                        FROM controls_catalog WHERE {array_col} IS NOT NULL
                        """,
                    )
                return cur.fetchall()

    rows = _run(_do) or []
    criteria = _aggregate_scorecard_rows(rows, stale_days=stale_days)
    fully_verified = sum(1 for c in criteria if c["total_controls"] > 0 and c["verified_controls"] == c["total_controls"])
    return {
        "framework": framework,
        "criteria": criteria,
        "total_criteria": len(criteria),
        "fully_verified_criteria": fully_verified,
    }


def _build_control_flow_map(event_rows: list, control_meta_by_id: dict) -> dict:
    """
    Pure aggregation step, split out of get_control_flow_map so it's
    unit-testable with fake rows — no DB connection needed (mirrors
    _aggregate_scorecard_rows's reasoning above).

    event_rows: [(source_system, risk_tier, final_verdict, policy_violations), ...]
    control_meta_by_id: {control_id: {name, soc2_criteria, nist_800_53, iso_27001, coso_component}}

    Builds a directly-follows-graph over REAL adjudicated events — contrast
    risk-sankey.jsx's Controls->Frameworks->Domains, which renders the
    curated control catalog's static structure (what controls exist), not
    observed event flow (what actually happened and how often). Here:
    source_system -> risk_tier -> final_verdict -> control_id, one edge per
    fired control, edge value = real observed event count.

    An event with no fired control (policy_violations empty — most
    adjudicated events, since most don't trip a policy rule) terminates at
    the verdict node. No fabricated control edge for it — that would
    overstate how often controls actually fire.
    """
    node_seen: dict[str, dict] = {}
    link_counts: dict[tuple, int] = {}

    def _node(node_id: str, label: str, node_type: str, **extra) -> None:
        if node_id not in node_seen:
            node_seen[node_id] = {"id": node_id, "label": label, "type": node_type, **extra}

    def _link(a: str, b: str) -> None:
        link_counts[(a, b)] = link_counts.get((a, b), 0) + 1

    for source_system, risk_tier, final_verdict, policy_violations in event_rows:
        sys_id  = f"sys:{source_system or 'UNKNOWN'}"
        tier_id = f"tier:{risk_tier or 'UNKNOWN'}"
        verd_id = f"verdict:{final_verdict or 'UNKNOWN'}"
        _node(sys_id, source_system or "UNKNOWN", "system")
        _node(tier_id, risk_tier or "UNKNOWN", "tier")
        _node(verd_id, final_verdict or "UNKNOWN", "verdict")
        _link(sys_id, tier_id)
        _link(tier_id, verd_id)

        for control_id in (policy_violations or []):
            ctrl_id = f"ctrl:{control_id}"
            _node(ctrl_id, control_id, "control", **control_meta_by_id.get(control_id, {}))
            _link(verd_id, ctrl_id)

    links = [{"source": a, "target": b, "value": v} for (a, b), v in link_counts.items()]
    return {"nodes": list(node_seen.values()), "links": links}


def get_control_flow_map(days: int = 30) -> dict:
    """
    Real event flow -> control -> framework, mined from
    observability.adjudicated_tool_calls over the last `days` days, UNIONed
    with the unreviewed system_telemetry tail (same reasoning as
    get_recent_unreviewed_system_events: only flagged rows ever reach
    adjudication, so adjudicated_tool_calls alone was undercounting real
    traffic by the same ~97% Continuous Monitoring's charts and Process
    Mining were both found and fixed to include). An unreviewed row has no
    risk_tier/verdict/fired-control — it terminates at a synthetic
    "NOT_REVIEWED" verdict node, same convention GET /observability/events
    and continuous-monitoring-viz.jsx's DimensionFlowGraph already use, so
    a reviewer can see real system_telemetry volume behind each source
    system without it being mistaken for a real verdict. See
    _build_control_flow_map for the graph-building logic.
    """
    def _events():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT source_system, risk_tier, final_verdict, policy_violations "
                    "FROM observability.adjudicated_tool_calls "
                    "WHERE adjudicated_at > NOW() - (%s || ' days')::interval",
                    (days,),
                )
                return cur.fetchall()
    event_rows = list(_run(_events) or [])

    def _unreviewed():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT st.system_type
                    FROM observability.system_telemetry st
                    LEFT JOIN observability.adjudicated_tool_calls atc
                        ON atc.system_telemetry_id = st.id
                    WHERE st.created_at > NOW() - (%s || ' days')::interval
                      AND atc.id IS NULL
                    """,
                    (days,),
                )
                return cur.fetchall()
    event_rows.extend((system_type, None, "NOT_REVIEWED", []) for (system_type,) in (_run(_unreviewed) or []))

    control_ids = sorted({c for row in event_rows for c in (row[3] or [])})
    control_meta_by_id: dict = {}
    if control_ids:
        def _controls():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT control_id, name, soc2_criteria, nist_800_53, iso_27001, coso_component "
                        "FROM controls_catalog WHERE control_id = ANY(%s)",
                        (control_ids,),
                    )
                    return cur.fetchall()
        for r in (_run(_controls) or []):
            control_meta_by_id[r[0]] = {
                "name": r[1], "soc2_criteria": r[2] or [], "nist_800_53": r[3] or [],
                "iso_27001": r[4] or [], "coso_component": r[5],
            }

    return _build_control_flow_map(event_rows, control_meta_by_id)


def get_recent_adjudications_for_domain_summary(days: int = 30, limit: int = 5000) -> list:
    """
    Raw per-event rows for Continuous Monitoring — both the domain roll-up
    (pol_domain_mappings.domain_for_violations resolves each row's domain,
    deliberately NOT done here; db.py stays free of app-layer/policy-mapping
    imports, same separation get_control_flow_map keeps by returning raw rows
    for _build_control_flow_map to interpret) and the per-event Playback/
    Motion views (GET /observability/events), which need the extra identity/
    context columns the domain roll-up itself ignores.

    Returns [{"id", "adjudicated_at", "final_verdict", "risk_tier",
    "source_system", "target_tool", "server_name", "requires_human_review",
    "policy_violations", "case_id", "process_step"}, ...], oldest first (the
    natural order for time-axis playback; a summary consumer that doesn't
    care about order can ignore it). case_id/process_step are NULL for the
    overwhelming majority of rows — see their column comment in this file's
    migration block — and exist so a real (not categorical) directly-follows
    graph can be built for the rows that do have them. `limit` guards
    against an unbounded response on a `days` window far larger than this
    platform's actual event volume today (a few hundred/month) — raise it
    if that volume genuinely grows.
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, adjudicated_at, final_verdict, risk_tier, source_system,
                           target_tool, server_name, requires_human_review, policy_violations,
                           case_id, process_step
                    FROM observability.adjudicated_tool_calls
                    WHERE adjudicated_at > NOW() - (%s || ' days')::interval
                    ORDER BY adjudicated_at
                    LIMIT %s
                    """,
                    (days, limit),
                )
                return [
                    {
                        "id": r[0], "adjudicated_at": r[1], "final_verdict": r[2], "risk_tier": r[3],
                        "source_system": r[4], "target_tool": r[5], "server_name": r[6],
                        "requires_human_review": r[7], "policy_violations": r[8] or [],
                        "case_id": r[9], "process_step": r[10],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_recent_unreviewed_system_events(days: int = 30, limit: int = 5000) -> list:
    """The system_telemetry rows get_recent_adjudications_for_domain_summary
    can't see: every row that was never adjudicated (no matching
    adjudicated_tool_calls.system_telemetry_id) because mcp_governance's
    poll loop only ever pulls flagged rows (risk_flags IS NOT NULL) into
    the adjudication pipeline — see _fetch_unprocessed_system. That's the
    right call for the expensive Bronze/Silver/Gold/Council pipeline, but it
    means Continuous Monitoring's charts, sourced from adjudicated_tool_calls
    alone, only ever showed the reviewed slice — never the true transaction
    volume behind it. This exists so GET /observability/events can union
    both in and let "how many happened" and "how many got escalated" be
    honestly different numbers instead of the same one.

    Returns rows shaped to merge into get_recent_adjudications_for_domain_
    summary's own shape (api_server.py does the merge): final_verdict/
    risk_tier/requires_human_review/policy_violations are always None/[]
    here — this row was never scored, not scored-and-clear. case_id/
    process_step come straight out of raw_payload (set by the producer at
    ingestion time — see generate_o2c_p2p_synthetic_log.py/
    synthetic_transaction_tool.py — so they're present here just as often as
    on an adjudicated row, unlike the other columns above).

    id is offset by 10_000_000_000 to guarantee no collision with
    adjudicated_tool_calls' own BIGSERIAL ids when both lists are merged and
    used as a single id-keyed collection client-side."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT st.id, st.created_at, st.system_type, st.resource, st.server_name,
                           st.raw_payload
                    FROM observability.system_telemetry st
                    LEFT JOIN observability.adjudicated_tool_calls atc
                        ON atc.system_telemetry_id = st.id
                    WHERE st.created_at > NOW() - (%s || ' days')::interval
                      AND atc.id IS NULL
                    ORDER BY st.created_at
                    LIMIT %s
                    """,
                    (days, limit),
                )
                out = []
                for r in cur.fetchall():
                    payload = r[5] or {}
                    out.append({
                        "id": 10_000_000_000 + r[0], "adjudicated_at": r[1],
                        "final_verdict": None, "risk_tier": None,
                        "source_system": r[2], "target_tool": r[3], "server_name": r[4],
                        "requires_human_review": False, "policy_violations": [],
                        "case_id": payload.get("case_id"), "process_step": payload.get("process_step"),
                    })
                return out
    return _run(_do) or []


# ─────────────────────────────────────────────────────────────────────────────
# DORA-style change-management metrics (SOC 2 CC8.1 operational evidence)
# ─────────────────────────────────────────────────────────────────────────────

def _aggregate_dora_metrics(window_days: int, attestation_count: int, ticket_count: int,
                             resolved_ticket_hours: list) -> dict:
    """
    Pure aggregation, split out of compute_dora_metrics for testability —
    same reasoning as _aggregate_scorecard_rows.

    Two of DORA's four metrics, computed from data this platform actually
    ingests (not fabricated): pipeline_attestations (one row per CI pipeline
    run — evidence_endpoints.py's POST /evidence/attestation) as the
    deployment-frequency proxy, and itsm_tickets (findings escalated into a
    real Jira/ServiceNow ticket) as the change-failure/incident proxy.

    deployment_frequency_per_day: attestation_count / window_days.
    change_failure_rate: ticket_count / attestation_count — the fraction of
        pipeline runs in the window that produced a finding serious enough
        to open an incident ticket. None (not 0) when attestation_count==0 —
        a rate with no denominator is undefined, not "zero failures".
    mttr_hours: mean of resolved_ticket_hours — None when no ticket resolved
        in the window, not 0 (0 would falsely claim instant resolution).

    Lead Time for Changes (DORA's fourth metric) is deliberately NOT
    computed — see dora_metrics.py's module docstring for why.
    """
    deployment_frequency_per_day = round(attestation_count / window_days, 3) if window_days > 0 else None
    change_failure_rate = round(ticket_count / attestation_count, 3) if attestation_count > 0 else None
    mttr_hours = round(sum(resolved_ticket_hours) / len(resolved_ticket_hours), 2) if resolved_ticket_hours else None
    return {
        "window_days": window_days,
        "deployment_count": attestation_count,
        "deployment_frequency_per_day": deployment_frequency_per_day,
        "incident_ticket_count": ticket_count,
        "change_failure_rate": change_failure_rate,
        "resolved_ticket_count": len(resolved_ticket_hours),
        "mttr_hours": mttr_hours,
    }


def compute_dora_metrics(window_days: int = 30) -> dict:
    """Real deployment-frequency / change-failure-rate / MTTR metrics over
    the trailing window_days — see _aggregate_dora_metrics for the exact
    proxies and their honest-null semantics."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM observability.pipeline_attestations "
                    "WHERE created_at >= NOW() - (%s || ' days')::interval",
                    (window_days,),
                )
                attestation_count = cur.fetchone()[0]

                cur.execute(
                    "SELECT COUNT(*) FROM observability.itsm_tickets "
                    "WHERE created_at >= NOW() - (%s || ' days')::interval",
                    (window_days,),
                )
                ticket_count = cur.fetchone()[0]

                cur.execute(
                    """
                    SELECT EXTRACT(EPOCH FROM (resolved_at - created_at)) / 3600.0
                    FROM observability.itsm_tickets
                    WHERE resolved_at IS NOT NULL
                      AND created_at >= NOW() - (%s || ' days')::interval
                    """,
                    (window_days,),
                )
                resolved_ticket_hours = [r[0] for r in cur.fetchall() if r[0] is not None]

                return attestation_count, ticket_count, resolved_ticket_hours
    result = _run(_do)
    if result is None:
        return _aggregate_dora_metrics(window_days, 0, 0, []) | {"note": "Database not configured"}
    return _aggregate_dora_metrics(window_days, *result)


# ─────────────────────────────────────────────────────────────────────────────
# PaC negative-testing assurance (pac_contracts.py / pac_negative_tests.py)
# ─────────────────────────────────────────────────────────────────────────────

def get_control_fire_stats(control_id: str, window_days: int = 30) -> dict:
    """Real-production evidence a control is doing something: the most
    recent adjudicated_tool_calls row whose policy_violations included this
    control_id, and how many times in the trailing window. NULL/0 doesn't
    necessarily mean the control is broken — it may just mean the underlying
    bad state hasn't occurred recently — but combined with no passing
    negative-control test, it means nothing currently proves this control
    works at all (see list_unverified_controls)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT MAX(adjudicated_at),
                           COUNT(*) FILTER (WHERE adjudicated_at > NOW() - (%s || ' days')::interval)
                    FROM observability.adjudicated_tool_calls
                    WHERE %s = ANY(policy_violations)
                    """,
                    (window_days, control_id),
                )
                last_fired_at, fire_count = cur.fetchone()
                return {
                    "control_id": control_id,
                    "last_fired_at": last_fired_at.isoformat() if last_fired_at else None,
                    "fire_count_window": int(fire_count or 0),
                    "window_days": window_days,
                }
    return _run(_do) or {"control_id": control_id, "last_fired_at": None, "fire_count_window": 0, "window_days": window_days}


def update_control_verification(control_id: str, passed: bool, verified_at=None) -> bool:
    """Record the outcome of the most recent negative-control fixture that
    exercised this control_id. Called once per control_id after a corpus run
    (pac_negative_tests.run_corpus), not once per fixture — a control tested
    by two fixtures gets the AND of their results via the caller, not two
    competing writes here."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE controls_catalog
                    SET last_verified_at = COALESCE(%s, NOW()), last_test_passed = %s, updated_at = NOW()
                    WHERE control_id = %s
                    """,
                    (verified_at, passed, control_id),
                )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


def refresh_control_fire_stats(control_ids: Optional[list] = None) -> int:
    """Batch-refresh last_fired_at for every control (or a given subset)
    from real adjudication history. Cheap to run often — a single query per
    control_id, no full-table scan needed thanks to the GIN-able
    policy_violations = ANY(...) pattern already used elsewhere on this table."""
    ids = control_ids if control_ids is not None else [c["control_id"] for c in list_controls(source="pac_rego")]
    updated = 0
    for control_id in ids:
        stats = get_control_fire_stats(control_id)
        if stats["last_fired_at"]:
            def _do(cid=control_id, ts=stats["last_fired_at"]):
                with _conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE controls_catalog SET last_fired_at = %s WHERE control_id = %s",
                            (ts, cid),
                        )
                        return cur.rowcount > 0
            if _run(_do, default=False):
                updated += 1
    return updated


def list_unverified_controls(process: Optional[str] = None, stale_days: int = 30) -> list:
    """Controls that are 'policy-enforced' (source='pac_rego') but have
    NEITHER fired in real production within stale_days NOR passed a
    negative-control test within stale_days — the silent-rule / unverified-
    policy signal from the negative-testing plan: a control nothing currently
    proves is working."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                # Placeholder order must match their literal position in the
                # SQL text below: any "process = %s" (inside `where`) comes
                # first, then the two stale_days interval comparisons.
                clauses = ["source = 'pac_rego'"]
                params: list = []
                if process:
                    clauses.append("process = %s")
                    params.append(process)
                where = " AND ".join(clauses)
                params.extend([stale_days, stale_days])
                cur.execute(
                    f"""
                    SELECT control_id, name, process, last_fired_at, last_verified_at, last_test_passed
                    FROM controls_catalog
                    WHERE {where}
                      AND (last_fired_at IS NULL OR last_fired_at < NOW() - (%s || ' days')::interval)
                      AND (last_test_passed IS NOT TRUE OR last_verified_at < NOW() - (%s || ' days')::interval)
                    ORDER BY process, control_id
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    for k in ("last_fired_at", "last_verified_at"):
                        if d.get(k):
                            d[k] = d[k].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def insert_pac_test_run(process: str, module_id: Optional[int], triggered_by: str,
                         triggered_by_user: Optional[str], contract_ok: Optional[bool],
                         contract_findings: Optional[list], total: int, passed: int,
                         failed: int, results: list) -> Optional[int]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.pac_test_runs
                        (process, module_id, triggered_by, triggered_by_user,
                         contract_ok, contract_findings, total, passed, failed, results)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (process, module_id, triggered_by, triggered_by_user, contract_ok,
                     Json(contract_findings or []), total, passed, failed, Json(results)),
                )
                return cur.fetchone()[0]
    return _run(_do)


def list_pac_test_runs(process: Optional[str] = None, limit: int = 50) -> list:
    def _do():
        filters, params = [], []
        if process:
            filters.append("process = %s"); params.append(process)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(min(limit, 500))
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, process, module_id, triggered_by, triggered_by_user,
                           contract_ok, contract_findings, total, passed, failed, results, run_at
                    FROM observability.pac_test_runs
                    {where}
                    ORDER BY run_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("run_at"):
                        d["run_at"] = d["run_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def get_pac_module_history(process: str, limit: int = 20) -> list:
    """Return version history for a process (newest first)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, version, last_revised_at, created_at
                    FROM pac_policy_modules
                    WHERE process = %s
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    (process, limit),
                )
                return [
                    {
                        "id": r[0], "version": r[1],
                        "last_revised_at": r[2].isoformat() if r[2] else None,
                        "created_at": r[3].isoformat() if r[3] else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_pac_module_by_id(module_id: int) -> Optional[dict]:
    """Fetch one module version by id, rego_content included — needed by the
    negative-testing approval gate (pac_assurance.evaluate_and_record), which
    must test the EXACT version being approved, not just 'whatever is
    currently latest for the process' (those can differ mid-review)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, process, module_name, rego_content, version, last_revised_at, created_at
                    FROM pac_policy_modules WHERE id = %s
                    """,
                    (module_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0], "process": row[1], "module_name": row[2],
                    "rego_content": row[3], "version": row[4],
                    "last_revised_at": row[5].isoformat() if row[5] else None,
                    "created_at": row[6].isoformat() if row[6] else None,
                }
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# Plain-language policy documents + HITL conversion review
# (pac_policy_docs.py — see the pac_policy_documents DDL comment for why the
# source prose is kept separately from anything derived from it)
# ─────────────────────────────────────────────────────────────────────────────

# doc_text is deliberately excluded from every LIST query: policy documents run
# to tens of thousands of characters and the review queue only needs enough to
# recognise the document. The full text comes back from get_pac_policy_document.
_DOC_PREVIEW_CHARS = 400


def save_pac_policy_document(process: str, title: str, doc_text: str, *,
                             filename: Optional[str] = None, source: str = "upload",
                             byte_size: int = 0, sha256: Optional[str] = None,
                             uploaded_by: Optional[str] = None) -> Optional[int]:
    """Persist an uploaded/pasted plain-language policy document. Returns the row id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pac_policy_documents
                        (process, title, filename, source, doc_text, byte_size, sha256, uploaded_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (process, title, filename, source, doc_text, byte_size, sha256, uploaded_by),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def find_pac_policy_document_by_hash(process: str, sha256: str) -> Optional[dict]:
    """Most recent document for this process with identical text. Lets the API
    warn on a re-upload of the same file instead of silently accumulating
    duplicates — enforced here rather than by a UNIQUE index because
    re-uploading on purpose (to re-convert with a newer model) is legitimate."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, title, filename, status, created_at
                    FROM pac_policy_documents
                    WHERE process = %s AND sha256 = %s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (process, sha256),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0], "title": row[1], "filename": row[2], "status": row[3],
                    "created_at": row[4].isoformat() if row[4] else None,
                }
    return _run(_do)


def list_pac_policy_documents(process: Optional[str] = None, status: Optional[str] = None,
                              limit: int = 200) -> list:
    """Documents newest-first, with a text preview and a rollup of their
    conversion attempts (so the list can show 'awaiting review' without a
    second round-trip per row)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                clauses, params = [], []
                if process:
                    clauses.append("d.process = %s")
                    params.append(process)
                if status:
                    clauses.append("d.status = %s")
                    params.append(status)
                where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                params.append(limit)
                cur.execute(
                    f"""
                    SELECT d.id, d.process, d.title, d.filename, d.source, d.byte_size,
                           d.sha256, d.uploaded_by, d.status, d.created_at, d.updated_at,
                           LENGTH(d.doc_text)      AS text_length,
                           LEFT(d.doc_text, {_DOC_PREVIEW_CHARS}) AS preview,
                           COUNT(c.id)                                              AS conversion_count,
                           COUNT(*) FILTER (WHERE c.status = 'pending_review')       AS pending_review_count,
                           MAX(c.id) FILTER (WHERE c.status = 'pending_review')      AS pending_conversion_id,
                           MAX(c.id)                                                AS latest_conversion_id
                    FROM pac_policy_documents d
                    LEFT JOIN pac_policy_conversions c ON c.document_id = d.id
                    {where}
                    GROUP BY d.id
                    ORDER BY d.created_at DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                cols = [c[0] for c in cur.description]
                out = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    for k in ("created_at", "updated_at"):
                        if d.get(k):
                            d[k] = d[k].isoformat()
                    out.append(d)
                return out
    return _run(_do) or []


def get_pac_policy_document(doc_id: int, include_conversions: bool = True) -> Optional[dict]:
    """One document with its full text and, by default, every conversion
    attempt against it (newest first)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, process, title, filename, source, doc_text, byte_size,
                           sha256, uploaded_by, status, created_at, updated_at
                    FROM pac_policy_documents WHERE id = %s
                    """,
                    (doc_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [c[0] for c in cur.description]
                doc = dict(zip(cols, row))
                for k in ("created_at", "updated_at"):
                    if doc.get(k):
                        doc[k] = doc[k].isoformat()
                if include_conversions:
                    doc["conversions"] = _list_conversions(cur, "WHERE c.document_id = %s", (doc_id,))
                return doc
    return _run(_do)


def set_pac_policy_document_status(doc_id: int, status: str) -> bool:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE pac_policy_documents SET status = %s, updated_at = NOW() WHERE id = %s",
                    (status, doc_id),
                )
                return cur.rowcount > 0
    return bool(_run(_do))


def delete_pac_policy_document(doc_id: int) -> bool:
    """Conversions cascade (FK ON DELETE CASCADE); any module already published
    from one does not — a published policy stays published."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pac_policy_documents WHERE id = %s", (doc_id,))
                return cur.rowcount > 0
    return bool(_run(_do))


def _list_conversions(cur, where: str, params: tuple, limit: Optional[int] = None) -> list:
    """Shared conversion SELECT used by the review queue, the per-document
    fetch, and the single-conversion getter so all three return the same shape
    (including the parent document's title/filename, which the queue needs to
    be usable at all)."""
    cur.execute(
        f"""
        SELECT c.id, c.document_id, c.process, c.generated_rego, c.draft_rego, c.model,
               c.syntax_valid, c.syntax_errors, c.control_ids, c.status,
               c.reviewer, c.reviewer_role, c.review_notes, c.reviewed_at,
               c.published_module_id, c.created_at, c.updated_at,
               d.title AS document_title, d.filename AS document_filename,
               d.uploaded_by AS document_uploaded_by
        FROM pac_policy_conversions c
        JOIN pac_policy_documents d ON d.id = c.document_id
        {where}
        ORDER BY c.created_at DESC
        {'LIMIT %s' if limit is not None else ''}
        """,
        params + ((limit,) if limit is not None else ()),
    )
    cols = [c[0] for c in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        for k in ("reviewed_at", "created_at", "updated_at"):
            if d.get(k):
                d[k] = d[k].isoformat()
        out.append(d)
    return out


def save_pac_policy_conversion(document_id: int, process: str, generated_rego: str, *,
                               draft_rego: Optional[str] = None, model: Optional[str] = None,
                               syntax_valid: bool = False, syntax_errors: Optional[list] = None,
                               control_ids: Optional[list] = None) -> Optional[int]:
    """Record one Markdown->Rego conversion attempt, always at
    status 'pending_review' — there is deliberately no parameter to create an
    already-approved conversion, so no code path can bypass the human step."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pac_policy_conversions
                        (document_id, process, generated_rego, draft_rego, model,
                         syntax_valid, syntax_errors, control_ids)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (document_id, process, generated_rego,
                     draft_rego if draft_rego is not None else generated_rego,
                     model, syntax_valid, Json(syntax_errors or []), Json(control_ids or [])),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def get_pac_policy_conversion(conversion_id: int) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                rows = _list_conversions(cur, "WHERE c.id = %s", (conversion_id,))
                return rows[0] if rows else None
    return _run(_do)


def list_pac_policy_conversions(process: Optional[str] = None, status: Optional[str] = None,
                                limit: int = 100) -> list:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                clauses, params = [], []
                if process:
                    clauses.append("c.process = %s")
                    params.append(process)
                if status:
                    clauses.append("c.status = %s")
                    params.append(status)
                where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                return _list_conversions(cur, where, tuple(params), limit=limit)
    return _run(_do) or []


def update_pac_policy_conversion_draft(conversion_id: int, draft_rego: str, *,
                                       syntax_valid: bool, syntax_errors: list,
                                       control_ids: list) -> bool:
    """Save the reviewer's edits to draft_rego. generated_rego is never touched
    — the gap between the two is the audit trail of what the human changed.

    Only allowed while the conversion is still open: once a decision has been
    recorded, the draft is frozen (the WHERE clause enforces it rather than
    trusting the caller, since a silent post-approval edit would mean the
    reviewed text and the published text diverge)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE pac_policy_conversions
                    SET draft_rego = %s, syntax_valid = %s, syntax_errors = %s,
                        control_ids = %s, updated_at = NOW()
                    WHERE id = %s AND status IN ('pending_review', 'changes_requested')
                    """,
                    (draft_rego, syntax_valid, Json(syntax_errors or []),
                     Json(control_ids or []), conversion_id),
                )
                return cur.rowcount > 0
    return bool(_run(_do))


def record_pac_conversion_decision(conversion_id: int, status: str, reviewer: str, *,
                                   reviewer_role: Optional[str] = None,
                                   review_notes: Optional[str] = None,
                                   published_module_id: Optional[int] = None) -> bool:
    """Record the human decision on a conversion. 'changes_requested' leaves it
    reviewable (reviewed_at stays NULL so it still reads as open work);
    approve/reject stamp it closed."""
    closing = status in ("approved", "rejected")
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE pac_policy_conversions
                    SET status = %s, reviewer = %s, reviewer_role = %s, review_notes = %s,
                        reviewed_at = {'NOW()' if closing else 'NULL'},
                        published_module_id = COALESCE(%s, published_module_id),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (status, reviewer, reviewer_role, review_notes, published_module_id, conversion_id),
                )
                return cur.rowcount > 0
    return bool(_run(_do))


def save_pac_approval(module_id: int, approver: str, role: Optional[str] = None) -> Optional[int]:
    """Add an approver sign-off for a module version. Returns the row id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pac_policy_approvals (module_id, approver, role) VALUES (%s, %s, %s) RETURNING id",
                    (module_id, approver, role),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# Policy-as-Code business processes (formerly a hardcoded Python set)
# ─────────────────────────────────────────────────────────────────────────────

# The 5 processes this app shipped with, migrated into pac_processes on first
# startup exactly as they were previously hardcoded (labels/colors/icons from
# code-screens.jsx's PAC_PROCESSES array, prefixes from pac_endpoints.py's
# _PROCESS_ID_PREFIX). Kept here (not in pac_endpoints.py) since seeding is a
# db-layer concern, matching the controls_catalog seed pattern.
_BUILTIN_PAC_PROCESSES = [
    {"id": "itgc", "label": "ITGCs", "short_label": "ITGC", "control_prefix": "ITGC",
     "color": "#6366f1", "icon": "🔒",
     "description": "IT General Controls — Oracle Fusion access provisioning, SOD, change management, audit logging via IDCS and Security Console."},
    {"id": "order_to_cash", "label": "Order to Cash", "short_label": "O2C", "control_prefix": "OTC",
     "color": "#0ea5e9", "icon": "💰",
     "description": "Order Management → AR Invoice → Revenue Recognition — Oracle OM, Configurator, AR, Revenue Management modules."},
    {"id": "procure_to_pay", "label": "Procure to Pay", "short_label": "P2P", "control_prefix": "P2P",
     "color": "#f59e0b", "icon": "📦",
     "description": "Requisition → PO → Receipt → Invoice → Payment — Oracle Purchasing, iProcurement, AP, Payment modules."},
    {"id": "receive_to_ship", "label": "Receive to Ship", "short_label": "R2S", "control_prefix": "R2S",
     "color": "#10b981", "icon": "🚢",
     "description": "Inbound Receipt → WMS Putaway → Pick/Pack/Ship → POD — Oracle WMS, Shipping Execution, Inventory modules."},
    {"id": "record_to_report", "label": "Record to Report", "short_label": "R2R", "control_prefix": "R2R",
     "color": "#ef4444", "icon": "📊",
     "description": "Journal Entry → Sub-ledger → GL Close → Financial Statements — Oracle GL, SLA, FAH, Financial Reporting modules."},
    {"id": "devops_monitoring", "label": "DevOps Monitoring", "short_label": "DevOps", "control_prefix": "DEVOPS",
     "color": "#22d3ee", "icon": "🛠️",
     "description": "SCM branch-protection auditing and SARIF/SAST evidence ingestion — GitHub/GitLab repo integrity, CODEOWNERS coverage, and vulnerability severity SLAs."},
    {"id": "infrastructure_monitoring", "label": "Infrastructure Monitoring", "short_label": "Infra", "control_prefix": "INFRA",
     "color": "#a855f7", "icon": "🖥️",
     "description": "Continuous IaaS/OS/DB configuration audit — Postgres CIS-style hardening checks and Railway platform/deployment drift."},
    {"id": "hire_to_retire", "label": "Hire to Retire", "short_label": "H2R", "control_prefix": "H2R",
     "color": "#f472b6", "icon": "🧑‍💼",
     "description": "Payroll/HR continuous audit — ghost-employee detection, unauthorized pay-rate changes, and terminated-employee access retention via Oracle Fusion HCM."},
    {"id": "trade_compliance", "label": "Trade Compliance", "short_label": "TC", "control_prefix": "TC",
     "color": "#dc2626", "icon": "🚫",
     "description": "Export control / restricted-party screening — vendor and customer master data screened against the U.S. government Consolidated Screening List (OFAC SDN, BIS Entity List)."},
]


def seed_builtin_pac_processes() -> int:
    """Idempotently insert the 5 built-in processes. Returns count created."""
    created = 0
    for p in _BUILTIN_PAC_PROCESSES:
        if create_pac_process(
            p["id"], p["label"], p["short_label"],
            control_prefix=p["control_prefix"], color=p["color"], icon=p["icon"],
            description=p["description"], is_builtin=True, source="builtin",
        ):
            created += 1
    return created


def seed_framework_mappings() -> int:
    """Idempotently apply framework_mappings.FRAMEWORK_MAPPINGS to
    controls_catalog. Must run AFTER _seed_controls_catalog (api_server.py's
    startup order) — upsert_framework_mapping no-ops for a control_id that
    doesn't exist yet. Safe to re-run: always overwrites with the current
    curated dict, so an edit to framework_mappings.py takes effect on next
    restart without a manual migration."""
    import framework_mappings
    updated = 0
    for control_id, mapping in framework_mappings.FRAMEWORK_MAPPINGS.items():
        if upsert_framework_mapping(
            control_id,
            soc2_criteria=mapping.get("soc2_criteria"),
            nist_800_53=mapping.get("nist_800_53"),
            iso_27001=mapping.get("iso_27001"),
            coso_component=mapping.get("coso_component"),
            icif_component=mapping.get("icif_component"),
        ):
            updated += 1
    return updated


# ─────────────────────────────────────────────────────────────────────────────
# Concept layer — controlled vocabulary + NIST IR 8477 (STRM) crosswalk
# ─────────────────────────────────────────────────────────────────────────────

def upsert_concept(
    scheme: str, pref_label: str, *,
    notation: Optional[str] = None, alt_labels: Optional[list] = None,
    definition: Optional[str] = None, broader_scheme: Optional[str] = None,
    broader_pref_label: Optional[str] = None, source: str = "curated",
) -> Optional[int]:
    """Insert or update one concept, keyed on (scheme, pref_label). Recomputes
    label_hash so Stage 2's re-embed check can detect the change. broader_id is
    resolved from (broader_scheme, broader_pref_label) if given — the parent
    concept must already exist (seed schemes in dependency order, parents first)."""
    import hashlib
    alt_labels = alt_labels or []

    def _do():
        broader_id = None
        with _conn() as conn:
            with conn.cursor() as cur:
                if broader_scheme and broader_pref_label:
                    cur.execute(
                        "SELECT id FROM concepts WHERE scheme = %s AND pref_label = %s",
                        (broader_scheme, broader_pref_label),
                    )
                    row = cur.fetchone()
                    broader_id = row[0] if row else None

                label_hash = hashlib.sha256(
                    f"{pref_label}|{definition or ''}|{'|'.join(sorted(alt_labels))}".encode("utf-8")
                ).hexdigest()

                cur.execute(
                    """
                    INSERT INTO concepts
                        (scheme, notation, pref_label, alt_labels, definition,
                         broader_id, source, label_hash, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (scheme, pref_label) DO UPDATE SET
                        notation   = EXCLUDED.notation,
                        alt_labels = EXCLUDED.alt_labels,
                        definition = EXCLUDED.definition,
                        broader_id = EXCLUDED.broader_id,
                        source     = EXCLUDED.source,
                        label_hash = EXCLUDED.label_hash,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (scheme, notation, pref_label, alt_labels, definition,
                     broader_id, source, label_hash),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def upsert_concept_relation(
    from_concept_id: int, to_concept_id: int, strm_type: str, *,
    strength: Optional[float] = None, rationale: Optional[str] = None,
    source: str = "curated",
) -> Optional[int]:
    """Insert or update one typed crosswalk relation. strm_type must be one of
    NIST IR 8477's five: subset_of, superset_of, equal, intersects_with,
    no_relationship — enforced by the ck_concept_relations_strm_type CHECK."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO concept_relations
                        (from_concept_id, to_concept_id, strm_type, strength, rationale, source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (from_concept_id, to_concept_id, strm_type) DO UPDATE SET
                        strength  = EXCLUDED.strength,
                        rationale = EXCLUDED.rationale,
                        source    = EXCLUDED.source
                    RETURNING id
                    """,
                    (from_concept_id, to_concept_id, strm_type, strength, rationale, source),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def get_concept(scheme: str, pref_label: str) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, scheme, notation, pref_label, alt_labels, definition,
                           broader_id, source, label_hash
                    FROM concepts WHERE scheme = %s AND pref_label = %s
                    """,
                    (scheme, pref_label),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0], "scheme": row[1], "notation": row[2], "pref_label": row[3],
                    "alt_labels": row[4] or [], "definition": row[5], "broader_id": row[6],
                    "source": row[7], "label_hash": row[8],
                }
    return _run(_do)


def list_concepts(scheme: Optional[str] = None) -> list:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                if scheme:
                    cur.execute(
                        """
                        SELECT id, scheme, notation, pref_label, alt_labels, definition,
                               broader_id, source, label_hash
                        FROM concepts WHERE scheme = %s ORDER BY pref_label
                        """,
                        (scheme,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, scheme, notation, pref_label, alt_labels, definition,
                               broader_id, source, label_hash
                        FROM concepts ORDER BY scheme, pref_label
                        """
                    )
                return [
                    {
                        "id": r[0], "scheme": r[1], "notation": r[2], "pref_label": r[3],
                        "alt_labels": r[4] or [], "definition": r[5], "broader_id": r[6],
                        "source": r[7], "label_hash": r[8],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_concept_relations(concept_id: int) -> list:
    """All STRM relations touching this concept, either direction."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT from_concept_id, to_concept_id, strm_type, strength, rationale, source
                    FROM concept_relations
                    WHERE from_concept_id = %s OR to_concept_id = %s
                    """,
                    (concept_id, concept_id),
                )
                return [
                    {
                        "from_concept_id": r[0], "to_concept_id": r[1], "strm_type": r[2],
                        "strength": float(r[3]) if r[3] is not None else None,
                        "rationale": r[4], "source": r[5],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def list_concept_relations() -> list:
    """Every STRM relation, globally, with both endpoints' identity denormalized
    (scheme + pref_label, not just the internal id) — for full-graph consumers
    like ontology_export.py that need concept identity, not DB row ids."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        r.id, fc.scheme, fc.pref_label,
                        tc.scheme, tc.pref_label,
                        r.strm_type, r.strength, r.rationale, r.source
                    FROM concept_relations r
                    JOIN concepts fc ON fc.id = r.from_concept_id
                    JOIN concepts tc ON tc.id = r.to_concept_id
                    ORDER BY fc.scheme, fc.pref_label, tc.scheme, tc.pref_label
                    """
                )
                return [
                    {
                        "id": r[0], "from_scheme": r[1], "from_pref_label": r[2],
                        "to_scheme": r[3], "to_pref_label": r[4],
                        "strm_type": r[5], "strength": float(r[6]) if r[6] is not None else None,
                        "rationale": r[7], "source": r[8],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_concept_closure(concept_id: int, *, direction: str = "both", max_hops: int = 2) -> list:
    """Walk the SKOS broader/narrower TREE (concepts.broader_id) out to
    max_hops — separate from the STRM crosswalk graph in concept_relations.
    direction: 'broader' (ancestors), 'narrower' (descendants), or 'both'.

    Same recursive-CTE-with-visited-array shape as get_risk_graph_expanded
    (this module, risk_relationships section) — cloned deliberately rather
    than reinvented, adapted for a directed tree instead of an undirected
    graph with multiple relationship types.

    Returns [{concept_id, pref_label, hop}], nearest first. hop 0 is the
    concept itself.
    """
    def _do():
        results: Dict[int, dict] = {}

        def _walk(sql: str, params: tuple):
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    for cid, pref_label, depth in cur.fetchall():
                        if cid not in results or depth < results[cid]["hop"]:
                            results[cid] = {"concept_id": cid, "pref_label": pref_label, "hop": depth}

        if direction in ("broader", "both"):
            _walk(
                """
                WITH RECURSIVE up AS (
                    SELECT id, broader_id, 0 AS depth, ARRAY[id] AS visited
                    FROM concepts WHERE id = %s

                    UNION ALL

                    SELECT c.id, c.broader_id, u.depth + 1, u.visited || c.id
                    FROM concepts c
                    JOIN up u ON c.id = u.broader_id
                    WHERE u.depth < %s AND NOT (c.id = ANY(u.visited))
                )
                SELECT up.id, c2.pref_label, MIN(up.depth)
                FROM up JOIN concepts c2 ON c2.id = up.id
                GROUP BY up.id, c2.pref_label
                """,
                (concept_id, max_hops),
            )

        if direction in ("narrower", "both"):
            _walk(
                """
                WITH RECURSIVE down AS (
                    SELECT id, 0 AS depth, ARRAY[id] AS visited
                    FROM concepts WHERE id = %s

                    UNION ALL

                    SELECT c.id, d.depth + 1, d.visited || c.id
                    FROM concepts c
                    JOIN down d ON c.broader_id = d.id
                    WHERE d.depth < %s AND NOT (c.id = ANY(d.visited))
                )
                SELECT down.id, c2.pref_label, MIN(down.depth)
                FROM down JOIN concepts c2 ON c2.id = down.id
                GROUP BY down.id, c2.pref_label
                """,
                (concept_id, max_hops),
            )

        return sorted(results.values(), key=lambda r: r["hop"])
    return _run(_do, default=[]) or []


def seed_ontology() -> dict:
    """Idempotently apply ontology_seed.py's curated CONCEPTS/RELATIONS to the
    concepts/concept_relations tables. Same pattern as seed_framework_mappings:
    a projection of the existing hardcoded vocabularies, safe to re-run, always
    overwrites with the current curated content so an edit to ontology_seed.py
    takes effect on next restart without a manual migration.

    Concepts are seeded before relations (relations reference concept ids), and
    within concepts, schemes are seeded in ontology_seed.SEED_ORDER so a child's
    broader_scheme/broader_pref_label parent already exists."""
    import ontology_seed
    concepts_upserted = 0
    relations_upserted = 0
    label_to_id: Dict[tuple, int] = {}

    for scheme in ontology_seed.SEED_ORDER:
        for c in ontology_seed.SEED_CONCEPTS.get(scheme, []):
            cid = upsert_concept(
                scheme, c["pref_label"],
                notation=c.get("notation"), alt_labels=c.get("alt_labels"),
                definition=c.get("definition"),
                broader_scheme=c.get("broader_scheme"), broader_pref_label=c.get("broader_pref_label"),
                source=c.get("source", "curated"),
            )
            if cid:
                concepts_upserted += 1
                label_to_id[(scheme, c["pref_label"])] = cid

    for rel in ontology_seed.SEED_RELATIONS:
        from_id = label_to_id.get((rel["from_scheme"], rel["from_pref_label"])) \
            or (get_concept(rel["from_scheme"], rel["from_pref_label"]) or {}).get("id")
        to_id = label_to_id.get((rel["to_scheme"], rel["to_pref_label"])) \
            or (get_concept(rel["to_scheme"], rel["to_pref_label"]) or {}).get("id")
        if not from_id or not to_id:
            logger.warning(
                "seed_ontology: skipping relation %s -[%s]-> %s — concept(s) not found",
                rel["from_pref_label"], rel["strm_type"], rel["to_pref_label"],
            )
            continue
        if upsert_concept_relation(
            from_id, to_id, rel["strm_type"],
            strength=rel.get("strength"), rationale=rel.get("rationale"),
            source=rel.get("source", "curated"),
        ):
            relations_upserted += 1

    return {"concepts_upserted": concepts_upserted, "relations_upserted": relations_upserted}


def create_pac_process(process_id: str, label: str, short_label: str, *,
                        control_prefix: Optional[str] = None, color: Optional[str] = None,
                        icon: Optional[str] = None, description: Optional[str] = None,
                        is_builtin: bool = False, source: str = "manual") -> bool:
    """Insert a new PaC business process. Returns False (no-op) if the id already exists —
    callers (e.g. sync_github's auto-register) should treat that as 'already there', not an error."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pac_processes
                        (id, label, short_label, control_prefix, color, icon, description, is_builtin, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (process_id, label, short_label, control_prefix, color, icon, description, is_builtin, source),
                )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


def list_pac_processes() -> list:
    """All registered PaC business processes, builtin first then by creation order."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, label, short_label, control_prefix, color, icon, description,
                           is_builtin, source, created_at
                    FROM pac_processes
                    ORDER BY is_builtin DESC, created_at
                    """
                )
                return [
                    {
                        "id": r[0], "label": r[1], "short_label": r[2], "control_prefix": r[3],
                        "color": r[4], "icon": r[5], "description": r[6],
                        "is_builtin": r[7], "source": r[8],
                        "created_at": r[9].isoformat() if r[9] else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def delete_pac_process(process_id: str) -> bool:
    """Delete a non-builtin process. Refuses (returns False) for builtin ones."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pac_processes WHERE id = %s AND is_builtin = FALSE", (process_id,))
                return cur.rowcount > 0
    return _run(_do, default=False) or False


# ─────────────────────────────────────────────────────────────────────────────
# Model Health drift incidents
# ─────────────────────────────────────────────────────────────────────────────

def get_open_drift_incident(metric_key: str) -> Optional[dict]:
    """The one thing the drift-check loop actually needs: is there already an
    unresolved incident for this metric? If so, don't create a duplicate or
    re-alert — that's the job the old timestamp cooldown used to do."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM model_health_drift_incidents
                    WHERE metric_key = %s AND status != 'resolved'
                    ORDER BY detected_at DESC LIMIT 1
                    """,
                    (metric_key,),
                )
                row = cur.fetchone()
                return {"id": row[0]} if row else None
    return _run(_do)


def create_drift_incident(metric_key: str, metric_kind: str, psi: Optional[float],
                           n_baseline: Optional[int], n_current: Optional[int],
                           detail: Optional[dict] = None) -> Optional[int]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO model_health_drift_incidents
                        (metric_key, metric_kind, psi, n_baseline, n_current, detail)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (metric_key[:128], metric_kind[:16], psi, n_baseline, n_current,
                     Json(detail) if detail else None),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def list_drift_incidents(status: Optional[str] = None) -> list:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cols = ("id, metric_key, metric_kind, psi, n_baseline, n_current, detail, "
                        "status, owner, notes, detected_at, acknowledged_at, resolved_at, updated_at, "
                        "correction_action, corrected_by, corrected_at, "
                        "reoptimize_triggered_at, reoptimize_summary")
                if status:
                    cur.execute(
                        f"SELECT {cols} FROM model_health_drift_incidents WHERE status = %s ORDER BY detected_at DESC",
                        (status,),
                    )
                else:
                    cur.execute(f"SELECT {cols} FROM model_health_drift_incidents ORDER BY detected_at DESC")
                out = []
                for r in cur.fetchall():
                    out.append({
                        "id": r[0], "metric_key": r[1], "metric_kind": r[2],
                        "psi": float(r[3]) if r[3] is not None else None,
                        "n_baseline": r[4], "n_current": r[5], "detail": r[6],
                        "status": r[7], "owner": r[8], "notes": r[9],
                        "detected_at": r[10].isoformat() if r[10] else None,
                        "acknowledged_at": r[11].isoformat() if r[11] else None,
                        "resolved_at": r[12].isoformat() if r[12] else None,
                        "updated_at": r[13].isoformat() if r[13] else None,
                        "correction_action": r[14], "corrected_by": r[15],
                        "corrected_at": r[16].isoformat() if r[16] else None,
                        "reoptimize_triggered_at": r[17].isoformat() if r[17] else None,
                        "reoptimize_summary": r[18],
                    })
                return out
    return _run(_do) or []


def record_drift_reoptimization(incident_id: int, summary: dict) -> None:
    """Stamp a drift incident with the outcome of the automated re-optimization
    sweep it triggered (reoptimization_tool.run_reoptimization_sweep's return
    value) — separate from update_drift_incident's human-driven
    status/correction_action fields, since this is a system-recorded fact,
    not a governance decision."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE model_health_drift_incidents
                    SET reoptimize_triggered_at = NOW(), reoptimize_summary = %s
                    WHERE id = %s
                    """,
                    (Json(summary), incident_id),
                )
    _run(_do)


_VALID_CORRECTION_ACTIONS = {
    "rebaselined", "recalibrated", "escalated_for_review", "false_positive", "no_action_needed",
}


def update_drift_incident(incident_id: int, *, status: Optional[str] = None,
                           owner: Optional[str] = None, notes: Optional[str] = None,
                           correction_action: Optional[str] = None,
                           corrected_by: Optional[str] = None) -> bool:
    """Any field left as None is unchanged. A transition to 'acknowledged' or
    'resolved' stamps the matching timestamp; moving back to 'open' clears both,
    so re-opening a previously-resolved incident (drift recurred) reads cleanly.

    correction_action is the structured "what was actually done" record
    (MODEL_CARD.md "Recommended Next Steps") — distinct from status, which is
    just lifecycle state. Must be one of _VALID_CORRECTION_ACTIONS; silently
    ignored (not applied) otherwise rather than raising, matching this
    function's existing all-fields-optional style."""
    sets, params = ["updated_at = NOW()"], []
    if status is not None:
        sets.append("status = %s")
        params.append(status)
        if status == "acknowledged":
            sets.append("acknowledged_at = NOW()")
        elif status == "resolved":
            sets.append("resolved_at = NOW()")
        elif status == "open":
            sets.append("acknowledged_at = NULL")
            sets.append("resolved_at = NULL")
    if owner is not None:
        sets.append("owner = %s")
        params.append(owner[:128])
    if notes is not None:
        sets.append("notes = %s")
        params.append(notes)
    if correction_action is not None and correction_action in _VALID_CORRECTION_ACTIONS:
        sets.append("correction_action = %s")
        params.append(correction_action)
        sets.append("corrected_at = NOW()")
        if corrected_by is not None:
            sets.append("corrected_by = %s")
            params.append(corrected_by[:128])
    if len(sets) == 1:  # only updated_at — nothing real to change
        return False
    params.append(incident_id)
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE model_health_drift_incidents SET {', '.join(sets)} WHERE id = %s",
                    tuple(params),
                )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


def set_baseline_reset(metric_key: str, reset_by: Optional[str] = None, reason: Optional[str] = None) -> bool:
    """Mark 'now' as the new baseline floor for a metric — future drift
    computations (drift_tool.py) exclude data before this point. A fresh
    reset overwrites any prior one for the same metric_key."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO model_health_baseline_resets (metric_key, reset_at, reset_by, reason)
                    VALUES (%s, NOW(), %s, %s)
                    ON CONFLICT (metric_key) DO UPDATE SET
                        reset_at = NOW(), reset_by = EXCLUDED.reset_by, reason = EXCLUDED.reason
                    """,
                    (metric_key[:128], (reset_by or None) and reset_by[:128], reason),
                )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


def clear_baseline_reset(metric_key: str) -> bool:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM model_health_baseline_resets WHERE metric_key = %s", (metric_key,))
                return cur.rowcount > 0
    return _run(_do, default=False) or False


def get_baseline_resets() -> dict:
    """{metric_key: reset_at (ISO string)} for every metric with an active
    baseline reset — fed into drift_tool.py's compute_* functions so they
    exclude pre-reset history."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT metric_key, reset_at FROM model_health_baseline_resets")
                return {r[0]: r[1].isoformat() for r in cur.fetchall()}
    return _run(_do) or {}


# ─────────────────────────────────────────────────────────────────────────────
# Exception Management (Continuous Control Monitoring triage) — dev-only,
# see deploy_env.py / exceptions_endpoints.py
# ─────────────────────────────────────────────────────────────────────────────

_VALID_TRIAGE_LABELS = {
    "TRUE_CONTROL_FAILURE", "BENIGN_OPERATIONAL_NOISE", "APPROVED_CARVE_OUT", "DATA_PIPELINE_ERROR",
}
_TRIAGE_NOTES_REQUIRED_LABELS = {"TRUE_CONTROL_FAILURE", "APPROVED_CARVE_OUT"}



# exception_control_events.raw_payload can carry PII pulled straight from a
# source-system event (employee names/emails from HR/SoD connectors,
# transaction detail) — encrypted at rest here the same way mcp_governance.py
# encrypts payroll_detail/treasury_detail sub-keys, but wrapping the whole
# payload rather than named sub-keys, since raw_payload's shape varies by
# connector and there's no single well-known PII sub-key to target generically.
# actor is left as plaintext: it's an equality-filtered column (JE Testing's
# "filter by preparer", Exception Management's triage view), and Fernet
# encryption is randomized, so an encrypted actor could never be filtered on
# without a separate blind-index column — out of scope for this pass.
_RAW_PAYLOAD_ENC_KEY = "_enc"


def _encrypt_raw_payload(raw_payload: Optional[dict]) -> Optional[dict]:
    if not raw_payload:
        return raw_payload
    try:
        return {_RAW_PAYLOAD_ENC_KEY: encrypt_sensitive_json(raw_payload)}
    except Exception as exc:
        logging.getLogger("db").warning(
            "Could not encrypt exception_control_events.raw_payload (storing as "
            "plaintext — set CONNECTOR_ENCRYPTION_KEY to enable at-rest encryption): %s", exc,
        )
        return raw_payload


def _decrypt_raw_payload(raw_payload: Optional[dict]) -> Optional[dict]:
    if not raw_payload or _RAW_PAYLOAD_ENC_KEY not in raw_payload:
        return raw_payload
    try:
        return decrypt_sensitive_json(raw_payload[_RAW_PAYLOAD_ENC_KEY])
    except Exception as exc:
        logging.getLogger("db").warning("Could not decrypt exception_control_events.raw_payload: %s", exc)
        return raw_payload


def insert_exception_event(control_id: str, system_source: str, process: Optional[str],
                            event_timestamp, features: dict, model_version: str,
                            anomaly_score: float, uncertainty_score: float,
                            requires_human_review: bool, actor: Optional[str] = None,
                            action: Optional[str] = None, event_type: Optional[str] = None,
                            raw_payload: Optional[dict] = None,
                            system_telemetry_id: Optional[int] = None,
                            connector_id: Optional[int] = None, assigned_owner: Optional[str] = None,
                            risk_rating: Optional[str] = None) -> Optional[int]:
    """One exception_control_events row + its exception_model_inferences row,
    in a single connection — connector_poller.py's per-event scoring hook
    calls this for every polled event once deploy_env.IS_DEVELOPMENT, and
    je_testing_sweep.py calls it unconditionally for JE findings (leaving
    connector_id/assigned_owner/risk_rating at their default None — JE
    findings aren't connector-scored events and are excluded from every
    Exception Management query anyway, see _EXCLUDE_JE_TESTING_SQL)."""
    raw_payload = _encrypt_raw_payload(raw_payload)
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exception_control_events
                        (control_id, system_source, process, event_timestamp, point_in_time_features,
                         actor, action, event_type, raw_payload, system_telemetry_id,
                         connector_id, assigned_owner)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (control_id[:128], system_source[:64], (process[:64] if process else None),
                     event_timestamp, Json(features or {}),
                     (actor[:128] if actor else None), (action[:128] if action else None),
                     (event_type[:128] if event_type else None),
                     (Json(raw_payload) if raw_payload else None), system_telemetry_id,
                     connector_id, (assigned_owner[:128] if assigned_owner else None)),
                )
                event_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO exception_model_inferences
                        (event_id, model_version, anomaly_score, uncertainty_score, requires_human_review, risk_rating)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (event_id, model_version[:64], anomaly_score, uncertainty_score, requires_human_review,
                     risk_rating),
                )
                return event_id
    return _run(_do)


# Sort order for the R/A/G risk_rating vocabulary (management_action_plans.risk_rating /
# risk_scores.rag_status) — R (urgent) first, an unset/legacy-scored row last so it
# doesn't silently jump the queue ahead of a real R-rated item.
_RISK_RATING_ORDER_SQL = "CASE mi.risk_rating WHEN 'R' THEN 0 WHEN 'A' THEN 1 WHEN 'G' THEN 2 ELSE 3 END"

# Excludes je_testing_sweep.py's rows from exception_control_events/
# exception_model_inferences — the two features share these tables verbatim,
# discriminated only by event_type='JOURNAL_ENTRY' (see that module's own
# comment above list_je_testing_findings). Every Exception-Management-only
# query over these tables needs this, or JE Testing findings inflate Exception
# Management's counts and queue.
_EXCLUDE_JE_TESTING_SQL = "ce.event_type IS DISTINCT FROM 'JOURNAL_ENTRY'"


def list_pending_exceptions(limit: int = 100, min_uncertainty: float = 0.0,
                             risk_rating: Optional[str] = None, owner: Optional[str] = None) -> list:
    """Latest inference per event, for events flagged for review with no
    triage decision yet — risk_rating (R/A/G) first, then highest uncertainty
    (most ambiguous, most valuable-to-label) as the tiebreak. Same base
    predicate as devriskops-ccm's GET /api/v1/triage/pending, extended with
    the risk_rating/owner filters and JE Testing exclusion."""
    def _do():
        filters = [f"mi.requires_human_review = TRUE", "tri.id IS NULL",
                   "mi.uncertainty_score >= %s", _EXCLUDE_JE_TESTING_SQL]
        params: list = [min_uncertainty]
        if risk_rating:
            filters.append("mi.risk_rating = %s")
            params.append(risk_rating)
        if owner:
            filters.append("ce.assigned_owner = %s")
            params.append(owner)
        params.append(limit)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT ce.id, ce.control_id, ce.system_source, ce.process, ce.event_timestamp,
                           ce.point_in_time_features, ce.actor, ce.action, ce.event_type,
                           ce.raw_payload, ce.system_telemetry_id, ce.connector_id, ce.assigned_owner,
                           mi.id, mi.model_version, mi.anomaly_score, mi.uncertainty_score, mi.risk_rating, mi.scored_at
                    FROM exception_control_events ce
                    JOIN LATERAL (
                        SELECT * FROM exception_model_inferences m
                        WHERE m.event_id = ce.id ORDER BY m.scored_at DESC LIMIT 1
                    ) mi ON TRUE
                    LEFT JOIN exception_auditor_triage tri ON tri.event_id = ce.id
                    WHERE {" AND ".join(filters)}
                    ORDER BY {_RISK_RATING_ORDER_SQL}, mi.uncertainty_score DESC, ce.event_timestamp DESC
                    LIMIT %s
                    """,
                    params,
                )
                out = []
                for r in cur.fetchall():
                    out.append({
                        "event_id": r[0], "control_id": r[1], "system_source": r[2], "process": r[3],
                        "event_timestamp": r[4].isoformat() if r[4] else None,
                        "point_in_time_features": r[5] or {},
                        "actor": r[6], "action": r[7], "event_type": r[8],
                        "raw_payload": _decrypt_raw_payload(r[9]) or {}, "system_telemetry_id": r[10],
                        "connector_id": r[11], "assigned_owner": r[12],
                        "inference_id": r[13], "model_version": r[14],
                        "anomaly_score": float(r[15]), "uncertainty_score": float(r[16]),
                        "risk_rating": r[17],
                        "scored_at": r[18].isoformat() if r[18] else None,
                    })
                return out
    return _run(_do) or []


def list_pending_exceptions_grouped(limit: int = 200, risk_rating: Optional[str] = None,
                                     owner: Optional[str] = None, scope: str = "exception") -> list:
    """One row per (control_id, system_source) pair with a pending item,
    occurrence_count, the worst (lowest-order) risk_rating in the group, the
    most recent event's id/timestamp for drill-in, and whether the control is
    already tracked by an open Management Action Plan (same open-status set
    detect_recurring_exceptions checks) — surfaced so a reviewer doesn't
    re-litigate one occurrence at a time when the recurrence has already been
    escalated to a remediation plan. Curation lever: pairs this with
    bulk_submit_exception_triage() so a reviewer can clear an entire
    recurring group in one action instead of N.

    scope="exception" (default) excludes JE Testing's rows, same split every
    other Exception Management query uses. scope="je_testing" is the mirror
    image — je_testing_sweep.py stores its rule_id as control_id (see that
    module's _persist_finding), so the exact same GROUP BY already collapses
    recurring rule/system pairs; only the WHERE-filter direction changes.
    This is the one grouped-listing query both screens share, per the
    "Unify the queue" UX-audit recommendation — Exception Management and JE
    Testing are structurally the same shape (same tables, same 4-label
    taxonomy), unlike the Approve/Adjust compliance gates or CEM's holds."""
    def _do():
        scope_sql = "ce.event_type = 'JOURNAL_ENTRY'" if scope == "je_testing" else _EXCLUDE_JE_TESTING_SQL
        filters = ["mi.requires_human_review = TRUE", "tri.id IS NULL", scope_sql]
        params: list = []
        if risk_rating:
            filters.append("mi.risk_rating = %s")
            params.append(risk_rating)
        if owner:
            filters.append("ce.assigned_owner = %s")
            params.append(owner)
        params.append(limit)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT ce.control_id, ce.system_source, COUNT(*) AS occurrence_count,
                           MIN({_RISK_RATING_ORDER_SQL}) AS worst_rating_order,
                           MIN(ce.event_timestamp) AS first_seen_at, MAX(ce.event_timestamp) AS last_seen_at,
                           (array_agg(ce.id ORDER BY ce.event_timestamp DESC))[1] AS sample_event_id,
                           (array_agg(ce.assigned_owner ORDER BY ce.event_timestamp DESC))[1] AS owner,
                           bool_or(mp.id IS NOT NULL) AS has_open_map,
                           (array_agg(mp.map_ref ORDER BY ce.event_timestamp DESC) FILTER (WHERE mp.id IS NOT NULL))[1] AS map_ref
                    FROM exception_control_events ce
                    JOIN LATERAL (
                        SELECT * FROM exception_model_inferences m
                        WHERE m.event_id = ce.id ORDER BY m.scored_at DESC LIMIT 1
                    ) mi ON TRUE
                    LEFT JOIN exception_auditor_triage tri ON tri.event_id = ce.id
                    LEFT JOIN observability.management_action_plans mp
                        ON mp.control_id = ce.control_id AND mp.status IN ('proposed', 'approved', 'in_progress')
                    WHERE {" AND ".join(filters)}
                    GROUP BY ce.control_id, ce.system_source
                    ORDER BY worst_rating_order, occurrence_count DESC, last_seen_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                out = []
                _rating_by_order = {0: "R", 1: "A", 2: "G"}
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    d["worst_risk_rating"] = _rating_by_order.get(d.pop("worst_rating_order"))
                    for k in ("first_seen_at", "last_seen_at"):
                        if d.get(k):
                            d[k] = d[k].isoformat()
                    out.append(d)
                return out
    return _run(_do) or []


def bulk_submit_exception_triage(event_ids: list, auditor: str, resolution_label: str,
                                  justification_notes: Optional[str]) -> int:
    """Applies one auditor resolution to every event_id in one batch — the
    volume lever behind list_pending_exceptions_grouped's "resolve all N as
    X" action. Same ON CONFLICT (event_id) DO UPDATE upsert as
    submit_exception_triage, just applied to many rows in one round trip.
    Returns the number of rows written; validation (label/notes) is the
    caller's responsibility, same split as submit_exception_triage."""
    if resolution_label not in _VALID_TRIAGE_LABELS:
        return 0
    if resolution_label in _TRIAGE_NOTES_REQUIRED_LABELS and not (justification_notes or "").strip():
        return 0
    if not event_ids:
        return 0
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exception_auditor_triage (event_id, auditor, resolution_label, justification_notes)
                    SELECT unnest(%s::bigint[]), %s, %s, %s
                    ON CONFLICT (event_id) DO UPDATE SET
                        auditor = EXCLUDED.auditor, resolution_label = EXCLUDED.resolution_label,
                        justification_notes = EXCLUDED.justification_notes, reviewed_at = NOW()
                    """,
                    (event_ids, auditor[:128], resolution_label, (justification_notes or None)),
                )
                return cur.rowcount
    return _run(_do, default=0) or 0


def submit_exception_triage(event_id: int, auditor: str, resolution_label: str,
                             justification_notes: Optional[str]) -> Optional[dict]:
    """Records (or revises, via upsert on the UNIQUE event_id) an auditor's
    resolution. Returns None if event_id doesn't exist or resolution_label
    is invalid/missing required notes — caller (exceptions_endpoints.py)
    turns that into the appropriate HTTP error."""
    if resolution_label not in _VALID_TRIAGE_LABELS:
        return None
    if resolution_label in _TRIAGE_NOTES_REQUIRED_LABELS and not (justification_notes or "").strip():
        return None
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM exception_control_events WHERE id = %s", (event_id,))
                if not cur.fetchone():
                    return None
                cur.execute(
                    """
                    INSERT INTO exception_auditor_triage (event_id, auditor, resolution_label, justification_notes)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (event_id) DO UPDATE SET
                        auditor = EXCLUDED.auditor, resolution_label = EXCLUDED.resolution_label,
                        justification_notes = EXCLUDED.justification_notes, reviewed_at = NOW()
                    RETURNING id, event_id, resolution_label, reviewed_at
                    """,
                    (event_id, auditor[:128], resolution_label, (justification_notes or None)),
                )
                row = cur.fetchone()
                return {"triage_id": row[0], "event_id": row[1], "resolution_label": row[2],
                        "reviewed_at": row[3].isoformat() if row[3] else None}
    return _run(_do)


def get_exception_summary() -> dict:
    """Headline counts for the Triage Queue + Model Analytics tabs. Every
    query here excludes je_testing_sweep.py's rows (_EXCLUDE_JE_TESTING_SQL) —
    JE Testing has its own summary (get_je_testing_summary) and must not
    inflate these counts."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM exception_control_events ce
                    JOIN exception_model_inferences mi ON mi.event_id = ce.id
                    LEFT JOIN exception_auditor_triage tri ON tri.event_id = ce.id
                    WHERE mi.requires_human_review = TRUE AND tri.id IS NULL AND {_EXCLUDE_JE_TESTING_SQL}
                    """
                )
                pending = cur.fetchone()[0]
                cur.execute(
                    f"""
                    SELECT tri.resolution_label, COUNT(*) FROM exception_auditor_triage tri
                    JOIN exception_control_events ce ON ce.id = tri.event_id
                    WHERE {_EXCLUDE_JE_TESTING_SQL}
                    GROUP BY tri.resolution_label
                    """
                )
                by_label = {r[0]: r[1] for r in cur.fetchall()}
                cur.execute(
                    f"""
                    SELECT ce.system_source, COUNT(*) FROM exception_control_events ce
                    JOIN exception_model_inferences mi ON mi.event_id = ce.id
                    LEFT JOIN exception_auditor_triage tri ON tri.event_id = ce.id
                    WHERE mi.requires_human_review = TRUE AND tri.id IS NULL AND {_EXCLUDE_JE_TESTING_SQL}
                    GROUP BY ce.system_source
                    """
                )
                by_system = {r[0]: r[1] for r in cur.fetchall()}
                cur.execute(
                    f"""
                    SELECT COALESCE(ce.assigned_owner, '(unassigned)'), COUNT(*) FROM exception_control_events ce
                    JOIN exception_model_inferences mi ON mi.event_id = ce.id
                    LEFT JOIN exception_auditor_triage tri ON tri.event_id = ce.id
                    WHERE mi.requires_human_review = TRUE AND tri.id IS NULL AND {_EXCLUDE_JE_TESTING_SQL}
                    GROUP BY COALESCE(ce.assigned_owner, '(unassigned)')
                    """
                )
                by_owner = {r[0]: r[1] for r in cur.fetchall()}
                cur.execute(
                    f"""
                    SELECT COALESCE(mi.risk_rating, '(unrated)'), COUNT(*) FROM exception_control_events ce
                    JOIN exception_model_inferences mi ON mi.event_id = ce.id
                    LEFT JOIN exception_auditor_triage tri ON tri.event_id = ce.id
                    WHERE mi.requires_human_review = TRUE AND tri.id IS NULL AND {_EXCLUDE_JE_TESTING_SQL}
                    GROUP BY COALESCE(mi.risk_rating, '(unrated)')
                    """
                )
                by_risk_rating = {r[0]: r[1] for r in cur.fetchall()}
                cur.execute(f"SELECT COUNT(*) FROM exception_control_events ce WHERE {_EXCLUDE_JE_TESTING_SQL}")
                total_events = cur.fetchone()[0]
                return {
                    "pending_count": pending, "total_events": total_events,
                    "resolution_mix": by_label, "pending_by_system": by_system,
                    "pending_by_owner": by_owner, "pending_by_risk_rating": by_risk_rating,
                }
    return _run(_do) or {"pending_count": 0, "total_events": 0, "resolution_mix": {}, "pending_by_system": {},
                          "pending_by_owner": {}, "pending_by_risk_rating": {}}


def list_exception_triage_history(limit: int = 200) -> list:
    """Resolved triage decisions, most recent first — Model Analytics tab's
    review-volume/resolution-mix trend. Excludes je_testing_sweep.py's rows —
    see _EXCLUDE_JE_TESTING_SQL."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT tri.id, tri.event_id, ce.control_id, ce.system_source, ce.process,
                           tri.auditor, tri.resolution_label, tri.justification_notes, tri.reviewed_at,
                           mi.anomaly_score, mi.uncertainty_score
                    FROM exception_auditor_triage tri
                    JOIN exception_control_events ce ON ce.id = tri.event_id
                    JOIN LATERAL (
                        SELECT * FROM exception_model_inferences m WHERE m.event_id = ce.id
                        ORDER BY m.scored_at DESC LIMIT 1
                    ) mi ON TRUE
                    WHERE {_EXCLUDE_JE_TESTING_SQL}
                    ORDER BY tri.reviewed_at DESC LIMIT %s
                    """,
                    (limit,),
                )
                out = []
                for r in cur.fetchall():
                    out.append({
                        "triage_id": r[0], "event_id": r[1], "control_id": r[2], "system_source": r[3],
                        "process": r[4], "auditor": r[5], "resolution_label": r[6],
                        "justification_notes": r[7], "reviewed_at": r[8].isoformat() if r[8] else None,
                        "anomaly_score": float(r[9]), "uncertainty_score": float(r[10]),
                    })
                return out
    return _run(_do) or []


def get_exception_score_history(system_source: str, metric: str, limit: int = 500) -> list:
    """Chronological anomaly_score or uncertainty_score series for one
    system_source, oldest first — the input to feature-drift PSI
    (drift_tool.compute_psi via exceptions_endpoints.compute_exception_drift),
    split into baseline/current windows by the caller exactly like
    drift_tool.compute_ai_acceptance_drift already does for AI-acceptance
    events. `metric` must be "anomaly_score" or "uncertainty_score" — the
    caller validates this before it ever reaches the interpolated column name."""
    col = "anomaly_score" if metric == "anomaly_score" else "uncertainty_score"
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT mi.{col} FROM exception_model_inferences mi
                    JOIN exception_control_events ce ON ce.id = mi.event_id
                    WHERE ce.system_source = %s AND {_EXCLUDE_JE_TESTING_SQL}
                    ORDER BY mi.scored_at ASC LIMIT %s
                    """,
                    (system_source, limit),
                )
                return [float(r[0]) for r in cur.fetchall()]
    return _run(_do) or []


def list_exception_system_sources() -> list:
    """Excludes je_testing_sweep.py's rows — see _EXCLUDE_JE_TESTING_SQL. Feeds
    compute_exception_drift, which would otherwise compute PSI over
    JE Testing's score distribution as if it were an Exception Management
    system_source."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT DISTINCT system_source FROM exception_control_events ce WHERE {_EXCLUDE_JE_TESTING_SQL} ORDER BY 1")
                return [r[0] for r in cur.fetchall()]
    return _run(_do) or []


def escalate_stale_exceptions(stale_days: int = 14) -> int:
    """exception_staleness_sweep.py's daily pass: flips risk_rating to 'R'
    for any still-pending exception older than stale_days that isn't already
    'R' — never touches requires_human_review/status, only visibility
    ordering. Excludes je_testing_sweep.py's rows, same as every other
    Exception-Management-only query (_EXCLUDE_JE_TESTING_SQL)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE exception_model_inferences mi
                    SET risk_rating = 'R'
                    FROM exception_control_events ce
                    LEFT JOIN exception_auditor_triage tri ON tri.event_id = ce.id
                    WHERE mi.event_id = ce.id
                      AND mi.requires_human_review = TRUE
                      AND tri.id IS NULL
                      AND COALESCE(mi.risk_rating, '') != 'R'
                      AND {_EXCLUDE_JE_TESTING_SQL}
                      AND ce.event_timestamp < NOW() - (%s || ' days')::interval
                    """,
                    (stale_days,),
                )
                return cur.rowcount
    return _run(_do, default=0) or 0


# ─────────────────────────────────────────────────────────────────────────────
# Journal Entry Testing (je_testing_sweep.py / je_testing_endpoints.py)
# ─────────────────────────────────────────────────────────────────────────────
# Reuses exception_control_events/exception_model_inferences/exception_auditor_triage
# verbatim (je_testing_sweep._persist_finding writes via insert_exception_event) —
# discriminated from Exception Management's dev-only ML-uncertainty demo purely by
# event_type = 'JOURNAL_ENTRY', which only je_testing_sweep.py ever sets. Unlike
# list_pending_exceptions (hardcoded to the pending/unreviewed slice for that
# screen's Triage Queue), these read every JE finding regardless of triage state —
# JE Testing's findings table is a record of what the deterministic rule engine
# found, not just what still needs a human.

def _je_finding_filters(rule_id, system_source, preparer, only_pending):
    clauses = ["ce.event_type = 'JOURNAL_ENTRY'"]
    params: list = []
    if rule_id:
        clauses.append("ce.control_id = %s")
        params.append(rule_id)
    if system_source:
        clauses.append("ce.system_source = %s")
        params.append(system_source)
    if preparer:
        clauses.append("ce.actor = %s")
        params.append(preparer)
    if only_pending:
        clauses.append("mi.requires_human_review = TRUE AND tri.id IS NULL")
    return " AND ".join(clauses), params


def list_je_testing_findings(limit: int = 100, offset: int = 0, rule_id: Optional[str] = None,
                              system_source: Optional[str] = None, preparer: Optional[str] = None,
                              only_pending: bool = False) -> list:
    where, params = _je_finding_filters(rule_id, system_source, preparer, only_pending)
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT ce.id, ce.control_id, ce.system_source, ce.process, ce.event_timestamp,
                           ce.actor, ce.raw_payload, mi.anomaly_score, mi.requires_human_review,
                           tri.resolution_label, tri.reviewed_at
                    FROM exception_control_events ce
                    JOIN LATERAL (
                        SELECT * FROM exception_model_inferences m
                        WHERE m.event_id = ce.id ORDER BY m.scored_at DESC LIMIT 1
                    ) mi ON TRUE
                    LEFT JOIN exception_auditor_triage tri ON tri.event_id = ce.id
                    WHERE {where}
                    ORDER BY ce.event_timestamp DESC
                    LIMIT %s OFFSET %s
                    """,
                    (*params, limit, offset),
                )
                out = []
                for r in cur.fetchall():
                    out.append({
                        "event_id": r[0], "rule_id": r[1], "system_source": r[2], "process": r[3],
                        "event_timestamp": r[4].isoformat() if r[4] else None, "preparer": r[5],
                        "finding": _decrypt_raw_payload(r[6]) or {}, "anomaly_score": float(r[7]),
                        "requires_human_review": r[8],
                        "resolution_label": r[9], "reviewed_at": r[10].isoformat() if r[10] else None,
                    })
                return out
    return _run(_do) or []


def count_je_testing_findings(rule_id: Optional[str] = None, system_source: Optional[str] = None,
                               preparer: Optional[str] = None, only_pending: bool = False) -> int:
    where, params = _je_finding_filters(rule_id, system_source, preparer, only_pending)
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM exception_control_events ce
                    JOIN LATERAL (
                        SELECT * FROM exception_model_inferences m
                        WHERE m.event_id = ce.id ORDER BY m.scored_at DESC LIMIT 1
                    ) mi ON TRUE
                    LEFT JOIN exception_auditor_triage tri ON tri.event_id = ce.id
                    WHERE {where}
                    """,
                    params,
                )
                return cur.fetchone()[0]
    return _run(_do) or 0


def get_je_testing_summary() -> dict:
    """Headline tiles for the JE Testing tab: entries tested (distinct
    postings scored), findings by rule, and the preparers/accounts most
    represented among findings."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM exception_control_events WHERE event_type = 'JOURNAL_ENTRY'")
                total_findings = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT control_id, COUNT(*) FROM exception_control_events
                    WHERE event_type = 'JOURNAL_ENTRY' GROUP BY control_id ORDER BY 2 DESC
                    """
                )
                by_rule = {r[0]: r[1] for r in cur.fetchall()}
                cur.execute(
                    """
                    SELECT actor, COUNT(*) FROM exception_control_events
                    WHERE event_type = 'JOURNAL_ENTRY' AND actor IS NOT NULL
                    GROUP BY actor ORDER BY 2 DESC LIMIT 10
                    """
                )
                top_preparers = [{"preparer": r[0], "count": r[1]} for r in cur.fetchall()]
                cur.execute(
                    """
                    SELECT ce.id FROM exception_control_events ce
                    JOIN exception_model_inferences mi ON mi.event_id = ce.id
                    LEFT JOIN exception_auditor_triage tri ON tri.event_id = ce.id
                    WHERE ce.event_type = 'JOURNAL_ENTRY' AND mi.requires_human_review = TRUE AND tri.id IS NULL
                    """
                )
                pending_count = len(cur.fetchall())
                return {
                    "total_findings": total_findings, "findings_by_rule": by_rule,
                    "top_preparers": top_preparers, "pending_count": pending_count,
                }
    return _run(_do) or {"total_findings": 0, "findings_by_rule": {}, "top_preparers": [], "pending_count": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Regulatory change management (regulatory_change_tool.py / regulatory_change_endpoints.py)
# ─────────────────────────────────────────────────────────────────────────────

def get_latest_regulatory_change_version(feed_id: str, source_url: str) -> Optional[dict]:
    """Most recent stored snapshot of one (feed_id, source_url) pair, or None
    if this is the first time it's been fetched — the comparison point for
    regulatory_change_tool.is_material_change."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, feed_id, source_url, title, fetched_text, sha256, fetched_at
                    FROM regulatory_change_versions
                    WHERE feed_id = %s AND source_url = %s
                    ORDER BY fetched_at DESC LIMIT 1
                    """,
                    (feed_id, source_url),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                d = dict(zip(cols, row))
                d["fetched_at"] = d["fetched_at"].isoformat() if d["fetched_at"] else None
                return d
    return _run(_do)


def save_regulatory_change_version(feed_id: str, source_url: str, title: Optional[str],
                                    fetched_text: str, sha256: str,
                                    previous_version_id: Optional[int]) -> Optional[int]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO regulatory_change_versions
                        (feed_id, source_url, title, fetched_text, sha256, previous_version_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (feed_id[:32], source_url, (title[:512] if title else None), fetched_text, sha256, previous_version_id),
                )
                return cur.fetchone()[0]
    return _run(_do)


def list_regulatory_change_versions(feed_id: Optional[str] = None, limit: int = 100) -> list:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                q = ("SELECT id, feed_id, source_url, title, sha256, previous_version_id, fetched_at "
                     "FROM regulatory_change_versions")
                params: list = []
                if feed_id:
                    q += " WHERE feed_id = %s"
                    params.append(feed_id)
                q += " ORDER BY fetched_at DESC LIMIT %s"
                params.append(limit)
                cur.execute(q, params)
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    d["fetched_at"] = d["fetched_at"].isoformat() if d["fetched_at"] else None
                    rows.append(d)
                return rows
    return _run(_do) or []


def save_regulatory_change_proposal(version_id: int, diff_summary: str,
                                     proposed_control_ref: Optional[str], proposed_edit: dict) -> Optional[int]:
    """pending_review only — same 'nothing can publish without a decision'
    discipline as pac_policy_docs.save_pac_policy_conversion; approval is a
    separate explicit step (record_regulatory_change_proposal_decision)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO regulatory_change_proposals
                        (version_id, diff_summary, proposed_control_ref, proposed_edit)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (version_id, diff_summary, (proposed_control_ref[:32] if proposed_control_ref else None),
                     Json(proposed_edit or {})),
                )
                return cur.fetchone()[0]
    return _run(_do)


def list_regulatory_change_proposals(status: Optional[str] = None, limit: int = 100) -> list:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                q = (
                    "SELECT p.id, p.version_id, p.diff_summary, p.proposed_control_ref, p.proposed_edit, "
                    "p.status, p.reviewer, p.reviewed_at, p.review_notes, p.created_at, "
                    "v.feed_id, v.source_url, v.title, v.fetched_at "
                    "FROM regulatory_change_proposals p JOIN regulatory_change_versions v ON v.id = p.version_id"
                )
                params: list = []
                if status:
                    q += " WHERE p.status = %s"
                    params.append(status)
                q += " ORDER BY p.created_at DESC LIMIT %s"
                params.append(limit)
                cur.execute(q, params)
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    for tf in ("reviewed_at", "created_at", "fetched_at"):
                        if d.get(tf) and hasattr(d[tf], "isoformat"):
                            d[tf] = d[tf].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def get_regulatory_change_proposal(proposal_id: int) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.id, p.version_id, p.diff_summary, p.proposed_control_ref, p.proposed_edit,
                           p.status, p.reviewer, p.reviewed_at, p.review_notes, p.created_at,
                           v.feed_id, v.source_url, v.title, v.fetched_text, v.fetched_at
                    FROM regulatory_change_proposals p JOIN regulatory_change_versions v ON v.id = p.version_id
                    WHERE p.id = %s
                    """,
                    (proposal_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                d = dict(zip(cols, row))
                for tf in ("reviewed_at", "created_at", "fetched_at"):
                    if d.get(tf) and hasattr(d[tf], "isoformat"):
                        d[tf] = d[tf].isoformat()
                return d
    return _run(_do)


def record_regulatory_change_proposal_decision(proposal_id: int, decision: str, reviewer: str,
                                                review_notes: Optional[str]) -> Optional[dict]:
    """decision: 'approved' | 'rejected'. Caller (regulatory_change_endpoints.py)
    applies the proposed_edit to controls_library on 'approved' — this
    function only records the decision itself, same status-machine split
    record_pac_conversion_decision uses (decide, then separately publish)."""
    if decision not in ("approved", "rejected"):
        return None
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE regulatory_change_proposals SET
                        status = %s, reviewer = %s, reviewed_at = NOW(), review_notes = %s
                    WHERE id = %s AND status = 'pending_review'
                    RETURNING id, version_id, status, proposed_control_ref, proposed_edit
                    """,
                    (decision, reviewer[:128], review_notes, proposal_id),
                )
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
                return dict(zip(cols, row)) if row else None
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# Connector credential encryption (Fernet, CONNECTOR_ENCRYPTION_KEY)
# ─────────────────────────────────────────────────────────────────────────────
# A stable key is required — unlike auth_endpoints.py's AUTH_JWT_SECRET (which
# safely falls back to a random per-process value since sessions are meant to
# expire anyway), a randomly-regenerated key here would silently make every
# previously-encrypted connector credential permanently undecryptable on the
# next restart. So there is deliberately NO insecure fallback: if the env var
# isn't set, encrypt/decrypt raise instead of pretending to work.

class EncryptionKeyMissing(RuntimeError):
    """CONNECTOR_ENCRYPTION_KEY is not set — cannot encrypt/decrypt connector credentials."""


# Multi-tenant connector-key binding — mirrors auth_endpoints.py's
# bind_tenant_secret()/_active_jwt_secret() pattern. In TENANT_MODE=single
# (default) nothing here is used and _fernet() falls through to the module
# env var, unchanged. In TENANT_MODE=multi, api_server.py's resolution
# middleware binds each tenant's own CONNECTOR_ENCRYPTION_KEY for the
# duration of the request, so one tenant's key can never decrypt another
# tenant's connector credentials / system_telemetry sensitive payloads —
# though under database-per-tenant that data isn't even reachable across
# tenants in the first place; this is defense in depth on top of that.
_tenant_connector_key: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "db_tenant_connector_key", default=None
)


def bind_tenant_connector_key(key: str) -> None:
    _tenant_connector_key.set(key)


def unbind_tenant_connector_key() -> None:
    _tenant_connector_key.set(None)


def _fernet() -> "Fernet":
    if not _HAS_CRYPTOGRAPHY:
        raise EncryptionKeyMissing("cryptography package not installed — run: pip install cryptography")
    key = _tenant_connector_key.get() or os.environ.get("CONNECTOR_ENCRYPTION_KEY", "").strip()
    if not key:
        raise EncryptionKeyMissing(
            "CONNECTOR_ENCRYPTION_KEY is not set — generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "and set it before saving connector credentials."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_credentials(credentials: dict) -> bytes:
    """Encrypt a credentials dict to an opaque Fernet token for storage in poll_connectors.credentials_enc."""
    import json as _json
    return _fernet().encrypt(_json.dumps(credentials).encode())


def decrypt_credentials(token: bytes) -> dict:
    """Decrypt a poll_connectors.credentials_enc value back to a plain dict."""
    import json as _json
    try:
        return _json.loads(_fernet().decrypt(bytes(token)).decode())
    except InvalidToken:
        raise EncryptionKeyMissing(
            "Could not decrypt connector credentials — CONNECTOR_ENCRYPTION_KEY has "
            "changed since these were saved, or the value is corrupted."
        )


def encrypt_sensitive_json(data: dict) -> str:
    """Encrypt an arbitrary JSON-able dict with the same Fernet scheme as
    connector credentials (CONNECTOR_ENCRYPTION_KEY) — used for sub-payloads
    that carry compensation/bank/wire-transfer data (e.g. system_telemetry's
    payroll_detail / treasury_detail, see mcp_governance._ingest_system_event).
    Returns text (Fernet tokens are already URL-safe base64) rather than raw
    bytes so the result drops straight into a JSONB column value."""
    import json as _json
    return _fernet().encrypt(_json.dumps(data).encode()).decode("ascii")


def decrypt_sensitive_json(token: str | bytes) -> dict:
    """Inverse of encrypt_sensitive_json()."""
    import json as _json
    if isinstance(token, str):
        token = token.encode("ascii")
    try:
        return _json.loads(_fernet().decrypt(token).decode())
    except InvalidToken:
        raise EncryptionKeyMissing(
            "Could not decrypt sensitive data — CONNECTOR_ENCRYPTION_KEY has "
            "changed since it was saved, or the value is corrupted."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Poll-based connectors (observability.poll_connectors)
# ─────────────────────────────────────────────────────────────────────────────

def create_poll_connector(connector_type: str, display_name: str, base_url: Optional[str],
                           auth_type: str, credentials: dict, extra_config: Optional[dict] = None,
                           poll_interval_s: int = 1800, created_by: Optional[str] = None,
                           risk_tier: Optional[str] = None, data_sensitivity: Optional[str] = None,
                           system_owner: Optional[str] = None) -> Optional[int]:
    """Create a poll connector. `credentials` is a plain dict — encrypted here before storage."""
    enc = encrypt_credentials(credentials)
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.poll_connectors
                        (connector_type, display_name, base_url, auth_type, credentials_enc,
                         extra_config, poll_interval_s, created_by,
                         risk_tier, data_sensitivity, system_owner)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (connector_type, display_name, base_url, auth_type, enc,
                     Json(extra_config) if extra_config else None, poll_interval_s, created_by,
                     risk_tier, data_sensitivity, (system_owner[:128] if system_owner else None)),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def seed_synthetic_connectors(connectors: list) -> int:
    """Idempotently create default poll connectors — skips any whose
    (connector_type, display_name) pair already exists. `connectors[i]`:
    {connector_type, display_name, extra_config, poll_interval_s}. There's no
    DB constraint backing the dedup (poll_connectors has no natural unique
    key), so this is a plain existing-rows check, same tolerance
    seed_cem_event_templates' ON CONFLICT gives itself via a real constraint —
    here it's just done in Python instead. Used at startup to register the
    eleven synthetic-transaction simulator connectors (see
    synthetic_transaction_tool.py) without re-creating them on every restart."""
    if not connectors:
        return 0
    existing = {(c["connector_type"], c["display_name"]) for c in (list_poll_connectors() or [])}
    created = 0
    for c in connectors:
        if (c["connector_type"], c["display_name"]) in existing:
            continue
        new_id = create_poll_connector(
            connector_type=c["connector_type"], display_name=c["display_name"],
            base_url=c.get("base_url"), auth_type=c.get("auth_type", "none"),
            credentials=c.get("credentials", {}), extra_config=c.get("extra_config"),
            poll_interval_s=c.get("poll_interval_s", 1800), created_by="system:seed",
        )
        if new_id is not None:
            created += 1
    return created


def list_poll_connectors(include_credentials: bool = False) -> list:
    """List all connectors. Credentials are NEVER included unless explicitly requested
    (only the polling loop itself should pass include_credentials=True)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cols = "id, connector_type, display_name, base_url, auth_type, extra_config, poll_interval_s, active, last_poll_at, last_poll_status, last_poll_error, created_at, updated_at, created_by, risk_tier, data_sensitivity, system_owner, credentials_rotated_at"
                if include_credentials:
                    cols += ", credentials_enc"
                cur.execute(f"SELECT {cols} FROM observability.poll_connectors ORDER BY created_at DESC")
                rows = cur.fetchall()
                out = []
                for r in rows:
                    d = {
                        "id": r[0], "connector_type": r[1], "display_name": r[2], "base_url": r[3],
                        "auth_type": r[4], "extra_config": r[5], "poll_interval_s": r[6], "active": r[7],
                        "last_poll_at": r[8].isoformat() if r[8] else None,
                        "last_poll_status": r[9], "last_poll_error": r[10],
                        "created_at": r[11].isoformat() if r[11] else None,
                        "updated_at": r[12].isoformat() if r[12] else None,
                        "created_by": r[13],
                        "risk_tier": r[14], "data_sensitivity": r[15], "system_owner": r[16],
                        "credentials_rotated_at": r[17].isoformat() if r[17] else None,
                    }
                    if include_credentials:
                        d["credentials"] = decrypt_credentials(r[18]) if r[18] else {}
                    out.append(d)
                return out
    return _run(_do) or []


def get_poll_connector(connector_id: int, include_credentials: bool = False) -> Optional[dict]:
    rows = [c for c in list_poll_connectors(include_credentials=include_credentials) if c["id"] == connector_id]
    return rows[0] if rows else None
    # Simple filter over list_poll_connectors rather than a second query — connector
    # counts are small (single digits to low tens), not worth a separate code path.


def update_poll_connector(connector_id: int, *, display_name: Optional[str] = None,
                           base_url: Optional[str] = None, auth_type: Optional[str] = None,
                           credentials: Optional[dict] = None, extra_config: Optional[dict] = None,
                           poll_interval_s: Optional[int] = None, active: Optional[bool] = None,
                           risk_tier: Optional[str] = None, data_sensitivity: Optional[str] = None,
                           system_owner: Optional[str] = None) -> bool:
    """Update a connector. Any field left as None is unchanged — critically, omitting
    `credentials` keeps the existing encrypted value rather than requiring re-entry."""
    sets, params = [], []
    for col, val in (("display_name", display_name), ("base_url", base_url), ("auth_type", auth_type),
                      ("poll_interval_s", poll_interval_s), ("active", active),
                      ("risk_tier", risk_tier), ("data_sensitivity", data_sensitivity),
                      ("system_owner", system_owner)):
        if val is not None:
            sets.append(f"{col} = %s")
            params.append(val)
    if extra_config is not None:
        sets.append("extra_config = %s")
        params.append(Json(extra_config))
    if credentials is not None:
        sets.append("credentials_enc = %s")
        params.append(encrypt_credentials(credentials))
        sets.append("credentials_rotated_at = NOW()")
    if not sets:
        return False
    sets.append("updated_at = NOW()")
    params.append(connector_id)
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE observability.poll_connectors SET {', '.join(sets)} WHERE id = %s",
                    params,
                )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


# ─────────────────────────────────────────────────────────────────────────────
# Identity/role graph (observability.identity_role_edges, .sod_violations)
# ─────────────────────────────────────────────────────────────────────────────

def upsert_identity_role_edges(connector_id: int, assignments: list) -> int:
    """Full-refresh a connector's identity<->role edges: delete every edge
    previously recorded for this connector, then insert the current set —
    so a role revoked upstream actually disappears here instead of
    accumulating forever. `assignments` items need `username`/`role`, with
    optional `role_id` (matches oracle_fusion_tool.get_user_roles()'s
    ["assignments"] shape)."""
    rows = [
        (connector_id, a.get("username") or "", a.get("role") or "", a.get("role_id") or None)
        for a in assignments
        if a.get("username") and a.get("role")
    ]
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM observability.identity_role_edges WHERE connector_id = %s",
                    (connector_id,),
                )
                if rows:
                    execute_values(
                        cur,
                        "INSERT INTO observability.identity_role_edges "
                        "(connector_id, username, role_name, role_id) VALUES %s "
                        "ON CONFLICT (connector_id, username, role_name) DO NOTHING",
                        rows,
                    )
                return len(rows)
    return _run(_do, default=0) or 0


def upsert_sod_violations(connector_id: int, violations: list) -> int:
    """Full-refresh a connector's open SoD violations. `violations` items
    match oracle_fusion_tool.get_sod_violations()'s ["violations"] shape
    (violation_id, username, policy_name, conflict_roles, risk_level, status,
    detected_date)."""
    rows = [
        (connector_id, v.get("violation_id") or "", v.get("username") or "",
         v.get("policy_name") or None, Json(v.get("conflict_roles") or []),
         v.get("risk_level") or None, v.get("status") or None,
         v.get("detected_date") or None)
        for v in violations
        if v.get("violation_id") and v.get("username")
    ]
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM observability.sod_violations WHERE connector_id = %s",
                    (connector_id,),
                )
                if rows:
                    execute_values(
                        cur,
                        "INSERT INTO observability.sod_violations "
                        "(connector_id, violation_id, username, policy_name, conflict_roles, "
                        " risk_level, status, detected_date) VALUES %s "
                        "ON CONFLICT (connector_id, violation_id) DO NOTHING",
                        rows,
                    )
                return len(rows)
    return _run(_do, default=0) or 0


def get_identity_role_count(username: str) -> int:
    """Total role edges for this username across every connector — an
    identity can exist in more than one connected system, and the field
    this feeds (The Graph Architect's role_count) was always a single
    number, so summing is the closest match to that existing shape."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM observability.identity_role_edges WHERE username = %s",
                    (username,),
                )
                row = cur.fetchone()
                return row[0] if row else 0
    return _run(_do, default=0) or 0


def get_identity_role_names(username: str) -> list:
    """Role/entitlement names for this username, across every connector —
    feeds The Graph Architect's `entitlements` list (only its length is used
    by _estimate_blast(), but the names themselves are useful evidence)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT role_name FROM observability.identity_role_edges WHERE username = %s "
                    "ORDER BY role_name",
                    (username,),
                )
                return [r[0] for r in cur.fetchall()]
    return _run(_do, default=[]) or []


def list_open_sod_violations_for_user(username: str) -> list:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT violation_id, policy_name, conflict_roles, risk_level, status, detected_date "
                    "FROM observability.sod_violations WHERE username = %s AND status = 'Open' "
                    "ORDER BY detected_date DESC NULLS LAST",
                    (username,),
                )
                return [
                    {
                        "violation_id": r[0], "policy_name": r[1], "conflict_roles": r[2],
                        "risk_level": r[3], "status": r[4],
                        "detected_date": r[5].isoformat() if r[5] else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do, default=[]) or []


def list_connectors_with_stale_credentials(stale_days: int = 90) -> list:
    """Active connectors whose credential hasn't been rotated in over
    stale_days — the producer for INFRA-008 (connector_hygiene.py). Only
    active connectors count: a disabled connector's stale credential isn't
    an active exposure. Dogfoods Infrastructure Monitoring on Intelligenza's
    own credential store (Oracle Fusion, SAP HANA, GitHub/GitLab PATs,
    Postgres DSNs, Railway tokens, ...), not just external audit targets."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, connector_type, display_name, credentials_rotated_at,
                           EXTRACT(DAY FROM NOW() - credentials_rotated_at)::int AS credential_age_days
                    FROM observability.poll_connectors
                    WHERE active = TRUE
                      AND credentials_rotated_at < NOW() - (%s || ' days')::interval
                    ORDER BY credentials_rotated_at ASC
                    """,
                    (stale_days,),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("credentials_rotated_at"):
                        d["credentials_rotated_at"] = d["credentials_rotated_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def delete_poll_connector(connector_id: int) -> bool:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM observability.poll_connectors WHERE id = %s", (connector_id,))
                return cur.rowcount > 0
    return _run(_do, default=False) or False


def record_poll_result(connector_id: int, status: str, error: Optional[str] = None) -> None:
    """Stamp the outcome of a poll cycle. status is 'ok' or 'error'."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.poll_connectors
                    SET last_poll_at = NOW(), last_poll_status = %s, last_poll_error = %s
                    WHERE id = %s
                    """,
                    (status, error, connector_id),
                )
    _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# DevOps Monitoring: SARIF/SAST evidence records (observability.evidence_records)
# ─────────────────────────────────────────────────────────────────────────────
# HMAC signing (EVIDENCE_SIGNING_KEY). Unlike AUDIT_SIGNING_KEY (db._audit_signing_key),
# there is deliberately NO random-key fallback here: an empty/absent key would
# make every signature trivially forgeable (HMAC with no secret authenticates
# nothing), so evidence ingestion and verification both refuse outright rather
# than silently producing signatures nobody should trust. Documented as
# REQUIRED in .env.example; evidence_endpoints.py's /evidence/webhook and
# /evidence/records/{id}/verify both surface this as an HTTP 503.

class EvidenceSigningKeyMissing(RuntimeError):
    """EVIDENCE_SIGNING_KEY is not set — cannot sign or verify evidence records."""


def _evidence_signing_key() -> str:
    key = os.environ.get("EVIDENCE_SIGNING_KEY", "").strip()
    if not key:
        raise EvidenceSigningKeyMissing(
            "EVIDENCE_SIGNING_KEY is not set — generate one with "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"` "
            "and set it before ingesting or verifying evidence records."
        )
    return key


def sign_evidence_record(record_json: str) -> str:
    """HMAC-SHA256 over the canonical (sort_keys=True) JSON string of an
    evidence record's own fields — the caller builds that string identically
    on both sign (evidence_endpoints._ingest_one_finding) and verify
    (evidence_endpoints.verify_evidence) so the same input always reproduces
    the same signature."""
    import hashlib
    import hmac as _hmac
    return _hmac.new(_evidence_signing_key().encode("utf-8"), record_json.encode("utf-8"), hashlib.sha256).hexdigest()


# Fixed seed for the first record in the tamper-evidence chain — documented,
# not secret (the chain's integrity comes from linking, not from this value
# being unguessable). Arbitrary Postgres advisory-lock key for serializing
# chain_hash computation across concurrent inserts (same session-scoped
# xact-lock idiom Postgres recommends for "compute next value, insert" races).
EVIDENCE_CHAIN_GENESIS_HASH = "0" * 64
_EVIDENCE_CHAIN_LOCK_KEY = 918_273_645


def _evidence_chain_hash(prev_hash: Optional[str], signature: str) -> str:
    """Pure chain-link function, extracted for testability without a real
    DB connection — same reasoning as pac_endpoints._parse_opa_bindings /
    _aggregate_scorecard_rows. prev_hash=None (a fresh table, or the row
    before this one was a pre-chain legacy row) is treated as genesis."""
    import hashlib
    return hashlib.sha256(((prev_hash or EVIDENCE_CHAIN_GENESIS_HASH) + signature).encode("utf-8")).hexdigest()


def insert_evidence_record(
    repository: str, commit_sha: Optional[str], pipeline_run_id: Optional[str],
    source: str, rule_id: Optional[str], severity: str, cwe: Optional[str], cve: Optional[str],
    file_path: Optional[str], line_number: Optional[int], line_snippet: Optional[str],
    fingerprint: str, author: Optional[str], approver: Optional[str], scan_status: str,
    raw_sarif: Optional[dict], record_json: dict, signature: str,
) -> Optional[int]:
    """Insert one immutable evidence row. Returns None (no row id) when
    (fingerprint, commit_sha) already exists — the same finding re-ingested
    from a repeated scan of the same commit, not a new occurrence.

    Also computes and stores chain_hash = sha256(prev_chain_hash + signature)
    under a Postgres advisory transaction lock, so two concurrent ingests
    can't both read the same "previous" row and fork the tamper-evidence
    chain — see verify_evidence_chain()."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_EVIDENCE_CHAIN_LOCK_KEY,))
                cur.execute(
                    "SELECT chain_hash FROM observability.evidence_records ORDER BY id DESC LIMIT 1"
                )
                prev_row = cur.fetchone()
                prev_hash = prev_row[0] if prev_row else None
                chain_hash = _evidence_chain_hash(prev_hash, signature)
                cur.execute(
                    """
                    INSERT INTO observability.evidence_records (
                        repository, commit_sha, pipeline_run_id, source, rule_id, severity,
                        cwe, cve, file_path, line_number, line_snippet, fingerprint,
                        author, approver, scan_status, raw_sarif, record_json, signature, chain_hash
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s
                    )
                    ON CONFLICT (fingerprint, commit_sha) DO NOTHING
                    RETURNING id
                    """,
                    (repository, commit_sha, pipeline_run_id, source, rule_id, severity,
                     cwe, cve, file_path, line_number, line_snippet, fingerprint,
                     author, approver, scan_status,
                     Json(raw_sarif) if raw_sarif is not None else None, Json(record_json), signature, chain_hash),
                )
                row = cur.fetchone()
            return row[0] if row else None
    return _run(_do)


def list_evidence_records(repository: Optional[str] = None, severity: Optional[str] = None,
                           commit_sha: Optional[str] = None, limit: int = 100) -> list:
    """Filtered list, newest first — feeds the Evidence Inspector modal."""
    def _do():
        filters, params = [], []
        if repository:
            filters.append("repository = %s"); params.append(repository)
        if severity:
            filters.append("severity = %s"); params.append(severity.upper())
        if commit_sha:
            filters.append("commit_sha = %s"); params.append(commit_sha)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(min(limit, 500))
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, ingested_at, repository, commit_sha, pipeline_run_id, source,
                           rule_id, severity, cwe, cve, file_path, line_number, line_snippet,
                           fingerprint, author, approver, scan_status
                    FROM observability.evidence_records
                    {where}
                    ORDER BY ingested_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("ingested_at"):
                        d["ingested_at"] = d["ingested_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def get_evidence_record(record_id: int) -> Optional[dict]:
    """Full row including raw_sarif/record_json/signature — used by the
    /evidence/records/{id}/verify endpoint to recompute the HMAC."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, ingested_at, repository, commit_sha, pipeline_run_id, source,
                           rule_id, severity, cwe, cve, file_path, line_number, line_snippet,
                           fingerprint, author, approver, scan_status, raw_sarif, record_json, signature
                    FROM observability.evidence_records
                    WHERE id = %s
                    """,
                    (record_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                d = dict(zip(cols, row))
                if d.get("ingested_at"):
                    d["ingested_at"] = d["ingested_at"].isoformat()
                return d
    return _run(_do)


def verify_evidence_chain(limit: Optional[int] = None) -> dict:
    """Walk observability.evidence_records in insertion order and recompute
    each row's chain_hash from the previous chained row's chain_hash plus
    this row's own signature. This is what actually proves trail
    completeness: a per-record HMAC (checked by GET
    /evidence/records/{id}/verify) proves a row's OWN content wasn't
    altered, but says nothing about whether a row was deleted from between
    its neighbors — only the chain linkage can catch that, since a deleted
    row's absence breaks the next surviving row's expected chain_hash.

    Only walks rows where chain_hash IS NOT NULL — rows inserted before this
    column existed have nothing to link from and are deliberately excluded
    rather than folded into a fabricated retroactive chain (see the column's
    comment in the schema).

    Returns {"valid", "checked", "break_at_id", "unchained_legacy_count"}.
    valid=True with checked=0 means there is nothing yet to verify (a fresh
    or all-legacy table), not a pass — callers should treat that distinctly
    from a real, non-empty, unbroken chain.
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM observability.evidence_records WHERE chain_hash IS NULL"
                )
                unchained_legacy_count = cur.fetchone()[0]
                q = ("SELECT id, signature, chain_hash FROM observability.evidence_records "
                     "WHERE chain_hash IS NOT NULL ORDER BY id ASC")
                if limit:
                    cur.execute(q + " LIMIT %s", (limit,))
                else:
                    cur.execute(q)
                rows = cur.fetchall()

        prev_hash = None
        checked = 0
        for rid, signature, stored_chain_hash in rows:
            expected = _evidence_chain_hash(prev_hash, signature)
            checked += 1
            if expected != stored_chain_hash:
                return {"valid": False, "checked": checked, "break_at_id": rid,
                        "unchained_legacy_count": unchained_legacy_count}
            prev_hash = stored_chain_hash
        return {"valid": True, "checked": checked, "break_at_id": None,
                "unchained_legacy_count": unchained_legacy_count}
    return _run(_do, default={"valid": False, "checked": 0, "break_at_id": None,
                               "unchained_legacy_count": 0, "error": "query failed"})


# ─────────────────────────────────────────────────────────────────────────────
# Platform audit trail (observability.audit_log) — tamper-evident log of
# identity/access changes (auth_endpoints.py) and MCP tool calls
# (mcp_guards.py), replacing the latter's flat-file logger. Same hash-chain
# construction as evidence_records above, kept as a separate table because its
# rows carry a different shape (actor/action/target, not a SARIF finding) and
# are never deduplicated — every call is its own event, not a re-ingest.
# ─────────────────────────────────────────────────────────────────────────────

AUDIT_CHAIN_GENESIS_HASH = "0" * 64
_AUDIT_CHAIN_LOCK_KEY = 274_581_396  # distinct advisory-lock key from _EVIDENCE_CHAIN_LOCK_KEY
_audit_signing_key_cache: Optional[str] = None


def _audit_signing_key() -> str:
    """AUDIT_SIGNING_KEY should be set explicitly in production so HMAC
    signatures stay verifiable across restarts (same reasoning as
    EVIDENCE_SIGNING_KEY's docstring in .env.example). Unlike
    CONNECTOR_ENCRYPTION_KEY, a missing key must NOT block the login/admin
    action being audited — so this falls back to a random key generated once
    per process and cached, rather than raising. The chain_hash linkage still
    catches row deletion/reordering regardless of key stability; only HMAC
    re-verification of old signatures is lost across a restart without an
    explicit key, which is a documented degradation, not a silent one."""
    global _audit_signing_key_cache
    key = os.environ.get("AUDIT_SIGNING_KEY", "").strip()
    if key:
        return key
    if _audit_signing_key_cache is None:
        import secrets as _secrets
        _audit_signing_key_cache = _secrets.token_hex(32)
        logging.getLogger("db").warning(
            "AUDIT_SIGNING_KEY is not set — using a random per-process key. "
            "Audit log entries will still chain-hash correctly, but HMAC "
            "signatures won't re-verify after a restart. Set AUDIT_SIGNING_KEY "
            "for durable tamper-evidence."
        )
    return _audit_signing_key_cache


def _audit_chain_hash(prev_hash: Optional[str], signature: str) -> str:
    import hashlib
    return hashlib.sha256(((prev_hash or AUDIT_CHAIN_GENESIS_HASH) + signature).encode("utf-8")).hexdigest()


def insert_audit_log_entry(
    category: str, action: str, actor: Optional[str] = None, target: Optional[str] = None,
    detail: Optional[dict] = None, ip_address: Optional[str] = None,
) -> Optional[int]:
    """Insert one immutable, chain-hashed audit row. Never raises — audit
    logging must not block the action it's auditing (same discipline
    mcp_guards.audit_log already followed for its flat-file log); callers
    that care can check the None return."""
    import hashlib
    import hmac as _hmac
    import json as _json

    record_json = _json.dumps(
        {"category": category, "action": action, "actor": actor,
         "target": target, "ip_address": ip_address, "detail": detail},
        sort_keys=True, default=str,
    )
    signature = _hmac.new(
        _audit_signing_key().encode("utf-8"), record_json.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_AUDIT_CHAIN_LOCK_KEY,))
                cur.execute("SELECT chain_hash FROM observability.audit_log ORDER BY id DESC LIMIT 1")
                prev_row = cur.fetchone()
                prev_hash = prev_row[0] if prev_row else None
                chain_hash = _audit_chain_hash(prev_hash, signature)
                cur.execute(
                    """
                    INSERT INTO observability.audit_log (
                        category, action, actor, target, ip_address, detail, record_json, signature, chain_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                    RETURNING id
                    """,
                    (category, action, actor, target, ip_address,
                     Json(detail) if detail is not None else None, record_json, signature, chain_hash),
                )
                row = cur.fetchone()
            return row[0] if row else None
    try:
        return _run(_do)
    except Exception as exc:
        logging.getLogger("db").warning("insert_audit_log_entry failed: %s", exc)
        return None


def list_audit_log(category: Optional[str] = None, actor: Optional[str] = None, limit: int = 200) -> list:
    """Newest-first, optionally filtered — feeds an admin-facing audit viewer."""
    def _do():
        filters, params = [], []
        if category:
            filters.append("category = %s"); params.append(category)
        if actor:
            filters.append("actor = %s"); params.append(actor)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(min(limit, 1000))
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, occurred_at, category, action, actor, target, ip_address, detail
                    FROM observability.audit_log
                    {where}
                    ORDER BY occurred_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("occurred_at"):
                        d["occurred_at"] = d["occurred_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do, default=[])


def verify_audit_chain(limit: Optional[int] = None) -> dict:
    """Mirrors verify_evidence_chain — walks observability.audit_log in
    insertion order and recomputes each row's chain_hash from the previous
    row's chain_hash plus this row's own signature."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                q = "SELECT id, signature, chain_hash FROM observability.audit_log ORDER BY id ASC"
                if limit:
                    cur.execute(q + " LIMIT %s", (limit,))
                else:
                    cur.execute(q)
                rows = cur.fetchall()
        prev_hash = None
        checked = 0
        for rid, signature, stored_chain_hash in rows:
            expected = _audit_chain_hash(prev_hash, signature)
            checked += 1
            if expected != stored_chain_hash:
                return {"valid": False, "checked": checked, "break_at_id": rid}
            prev_hash = stored_chain_hash
        return {"valid": True, "checked": checked, "break_at_id": None}
    return _run(_do, default={"valid": False, "checked": 0, "break_at_id": None, "error": "query failed"})


# ─────────────────────────────────────────────────────────────────────────────
# Continuous Monitoring: Management Action Plans (observability.management_action_plans)
# ─────────────────────────────────────────────────────────────────────────────

_MAP_COLUMNS = (
    "id, map_ref, control_id, system_source, finding, root_cause, risk_rating, action, owner, "
    "due_date, success_criteria, reduction_pct, completion_pct, occurrence_count, window_days, "
    "first_occurrence_at, last_occurrence_at, source_event_ids, status, approval_task_id, "
    "reviewed_by_name, reviewed_at, review_comment, created_at, updated_at"
)
_MAP_TIMESTAMP_FIELDS = (
    "due_date", "first_occurrence_at", "last_occurrence_at", "reviewed_at", "created_at", "updated_at",
)


def _map_row_to_dict(cols: list, row: tuple) -> dict:
    d = dict(zip(cols, row))
    for k in _MAP_TIMESTAMP_FIELDS:
        if d.get(k) and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    if d.get("reduction_pct") is not None:
        d["reduction_pct"] = float(d["reduction_pct"])
    return d


def detect_recurring_exceptions(min_occurrences: int = 3, window_days: int = 30) -> list:
    """Finds (control_id, system_source) pairs whose latest scored inference
    required human review at least min_occurrences times within window_days —
    a control that keeps escalating for review, not a single one-off. Skips
    any control that already has an open MAP (idx_map_open_per_control),
    so a repeated detection pass never proposes a duplicate. Returns
    occurrence_count, the occurrence window, and up to 20 of the underlying
    event ids for map_detection_sweep.py to draft a proposal from."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT ce.control_id, ce.system_source, COUNT(*) AS occurrence_count,
                           MIN(ce.event_timestamp) AS first_occurrence_at,
                           MAX(ce.event_timestamp) AS last_occurrence_at,
                           (array_agg(ce.id ORDER BY ce.event_timestamp DESC))[1:20] AS event_ids
                    FROM exception_control_events ce
                    JOIN LATERAL (
                        SELECT requires_human_review FROM exception_model_inferences m
                        WHERE m.event_id = ce.id ORDER BY m.scored_at DESC LIMIT 1
                    ) mi ON TRUE
                    WHERE mi.requires_human_review = TRUE
                      AND {_EXCLUDE_JE_TESTING_SQL}
                      AND ce.event_timestamp > NOW() - (%s || ' days')::interval
                      AND NOT EXISTS (
                          SELECT 1 FROM observability.management_action_plans mp
                          WHERE mp.control_id = ce.control_id
                            AND mp.status IN ('proposed', 'approved', 'in_progress')
                      )
                    GROUP BY ce.control_id, ce.system_source
                    HAVING COUNT(*) >= %s
                    ORDER BY occurrence_count DESC
                    """,
                    (window_days, min_occurrences),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    for k in ("first_occurrence_at", "last_occurrence_at"):
                        if d.get(k):
                            d[k] = d[k].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def get_recent_exception_events_for_control(control_id: str, limit: int = 5) -> list:
    """Newest-first sample of a control's recent events, raw_payload
    decrypted — the factual basis map_detection_sweep._draft_map_proposal
    drafts a root cause/remediation plan from, same decrypt-on-read pattern
    list_pending_exceptions already uses."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, event_timestamp, actor, action, event_type, raw_payload
                    FROM exception_control_events
                    WHERE control_id = %s
                    ORDER BY event_timestamp DESC
                    LIMIT %s
                    """,
                    (control_id, limit),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("event_timestamp"):
                        d["event_timestamp"] = d["event_timestamp"].isoformat()
                    d["raw_payload"] = _decrypt_raw_payload(d.get("raw_payload")) or {}
                    rows.append(d)
                return rows
    return _run(_do) or []


def create_map(
    control_id: str, system_source: Optional[str], finding: str,
    root_cause: Optional[str], risk_rating: Optional[str], action: Optional[str],
    owner: Optional[str], due_date, success_criteria: Optional[str], reduction_pct: Optional[float],
    occurrence_count: int, window_days: int, first_occurrence_at, last_occurrence_at,
    source_event_ids: list,
) -> Optional[dict]:
    """Insert one MAP in status='proposed' — always pending human review;
    nothing about this function finalizes a MAP, only decide_map's approval
    path does. map_ref is generated here from observability.map_ref_seq
    (not passed in — a Python-side id would either need a DB round-trip to
    stay unique anyway, or risk a collision under concurrent sweeps).
    Returns None if idx_map_open_per_control already has an open MAP for
    this control_id — same race-safe dedup discipline as risk_waivers'
    unique-active-hash index, rather than trusting the caller's own
    detect_recurring_exceptions NOT EXISTS check alone."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT nextval('observability.map_ref_seq')")
                map_ref = f"MAP-CM-{cur.fetchone()[0]:06d}"
                cur.execute(
                    f"""
                    INSERT INTO observability.management_action_plans (
                        map_ref, control_id, system_source, finding, root_cause, risk_rating, action,
                        owner, due_date, success_criteria, reduction_pct, occurrence_count, window_days,
                        first_occurrence_at, last_occurrence_at, source_event_ids, status
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'proposed')
                    ON CONFLICT (control_id) WHERE status IN ('proposed', 'approved', 'in_progress')
                    DO NOTHING
                    RETURNING {_MAP_COLUMNS}
                    """,
                    (map_ref, control_id, system_source, finding, root_cause, risk_rating, action,
                     owner, due_date, success_criteria, reduction_pct, occurrence_count, window_days,
                     first_occurrence_at, last_occurrence_at, source_event_ids),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                return _map_row_to_dict(cols, row)
    return _run(_do)


def list_maps(status: Optional[str] = None, limit: int = 100) -> list:
    def _do():
        filters, params = [], []
        if status:
            filters.append("status = %s"); params.append(status)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(min(limit, 500))
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_MAP_COLUMNS} FROM observability.management_action_plans
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                return [_map_row_to_dict(cols, r) for r in cur.fetchall()]
    return _run(_do) or []


def get_map(map_ref: str) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_MAP_COLUMNS} FROM observability.management_action_plans WHERE map_ref = %s",
                    (map_ref,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                return _map_row_to_dict(cols, row)
    return _run(_do)


def decide_map(
    map_ref: str, decision: str, reviewer_name: str, comment: Optional[str] = None,
    adjustments: Optional[dict] = None, approval_task_id: Optional[int] = None,
) -> Optional[dict]:
    """Human decision on a proposed MAP — approve (optionally editing any of
    the AI-drafted fields first, same 'adjust before approving' pattern
    Gate 1/2 already use) or reject. Only acts on status='proposed' rows —
    a MAP already decided is not re-decidable through this path (use
    update_map_progress for an approved MAP's execution tracking)."""
    if decision not in ("approved", "rejected"):
        return None
    adjustments = adjustments or {}
    editable = {"risk_rating", "root_cause", "action", "owner", "due_date", "success_criteria", "reduction_pct"}
    set_clauses = ["status = %s", "reviewed_by_name = %s", "reviewed_at = NOW()", "review_comment = %s", "updated_at = NOW()"]
    params: list = [decision, reviewer_name, comment]
    for key in editable:
        if key in adjustments:
            set_clauses.append(f"{key} = %s")
            params.append(adjustments[key])
    if approval_task_id is not None:
        set_clauses.append("approval_task_id = %s")
        params.append(approval_task_id)
    params.append(map_ref)

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE observability.management_action_plans
                    SET {', '.join(set_clauses)}
                    WHERE map_ref = %s AND status = 'proposed'
                    RETURNING {_MAP_COLUMNS}
                    """,
                    params,
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                return _map_row_to_dict(cols, row)
    return _run(_do)


def update_map_progress(map_ref: str, completion_pct: int) -> Optional[dict]:
    """Execution tracking for an approved MAP. Only acts on 'approved' or
    'in_progress' rows — a rejected or still-proposed MAP has no execution
    to track yet. completion_pct=100 closes the MAP; any positive value
    below that moves a freshly-approved MAP into 'in_progress' so the
    MapsTab card status badge reflects real movement, not just a number."""
    completion_pct = max(0, min(100, completion_pct))
    status = "closed" if completion_pct >= 100 else "in_progress" if completion_pct > 0 else "approved"

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE observability.management_action_plans
                    SET completion_pct = %s, status = %s, updated_at = NOW()
                    WHERE map_ref = %s AND status IN ('approved', 'in_progress')
                    RETURNING {_MAP_COLUMNS}
                    """,
                    (completion_pct, status, map_ref),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                return _map_row_to_dict(cols, row)
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# PBC/workpaper evidence quality (observability.pbc_evidence)
# ─────────────────────────────────────────────────────────────────────────────

_PBC_EVIDENCE_COLUMNS = (
    "id, control_id, title, description, source_url, period_start, period_end, collected_date, "
    "has_signature, requires_signature, quality_flags, content_check, created_by, created_at"
)
_PBC_EVIDENCE_DATE_FIELDS = ("period_start", "period_end", "collected_date", "created_at")


def _pbc_evidence_row_to_dict(cols: list, row: tuple) -> dict:
    d = dict(zip(cols, row))
    for k in _PBC_EVIDENCE_DATE_FIELDS:
        if d.get(k) and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


def create_pbc_evidence(
    control_id: str, title: str, description: Optional[str], source_url: Optional[str],
    period_start, period_end, collected_date, has_signature: bool, requires_signature: bool,
    quality_flags: list, content_check: Optional[dict], created_by: Optional[str],
) -> Optional[dict]:
    """Insert one evidence log entry with its quality checks already
    computed (evidence_quality_endpoints.py runs evidence_quality_tool.py's
    checks before calling this — the flags are stored, not recomputed on
    every read, so a later change to the check logic doesn't silently
    rewrite history for evidence already logged)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO observability.pbc_evidence (
                        control_id, title, description, source_url, period_start, period_end,
                        collected_date, has_signature, requires_signature, quality_flags, content_check, created_by
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                    RETURNING {_PBC_EVIDENCE_COLUMNS}
                    """,
                    (control_id, title, description, source_url, period_start, period_end, collected_date,
                     has_signature, requires_signature, Json(quality_flags), Json(content_check) if content_check else None,
                     created_by),
                )
                row = cur.fetchone()
                cols = [d[0] for d in cur.description]
                return _pbc_evidence_row_to_dict(cols, row)
    return _run(_do)


def list_pbc_evidence(control_id: Optional[str] = None, flagged_only: bool = False, limit: int = 100) -> list:
    def _do():
        filters, params = [], []
        if control_id:
            filters.append("control_id = %s"); params.append(control_id)
        if flagged_only:
            filters.append("quality_flags != '[]'::jsonb")
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(min(limit, 500))
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_PBC_EVIDENCE_COLUMNS} FROM observability.pbc_evidence
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                return [_pbc_evidence_row_to_dict(cols, r) for r in cur.fetchall()]
    return _run(_do) or []


def get_pbc_evidence(evidence_id: int) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_PBC_EVIDENCE_COLUMNS} FROM observability.pbc_evidence WHERE id = %s",
                    (evidence_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                return _pbc_evidence_row_to_dict(cols, row)
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# Infrastructure Vulnerability & Currency Posture: asset inventory
# (observability.infra_assets)
# ─────────────────────────────────────────────────────────────────────────────

_INFRA_ASSET_COLUMNS = (
    "id, estate_label, asset_key, connector_id, asset_type, name, environment, os_name, os_version, "
    "software_name, software_version, ecosystem, image_digest, region, expires_at, "
    "last_assessed_at, assessment_source, source, metadata, first_seen_at, last_seen_at, active"
)
_INFRA_ASSET_TS_FIELDS = ("expires_at", "last_assessed_at", "first_seen_at", "last_seen_at")


def _infra_asset_row_to_dict(cols: list, row: tuple) -> dict:
    d = dict(zip(cols, row))
    for k in _INFRA_ASSET_TS_FIELDS:
        if d.get(k) and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


def upsert_infra_asset(
    asset_key: str, asset_type: str, name: str, connector_id: Optional[int] = None,
    estate_label: str = "default", environment: Optional[str] = None,
    os_name: Optional[str] = None, os_version: Optional[str] = None,
    software_name: Optional[str] = None, software_version: Optional[str] = None,
    ecosystem: Optional[str] = None, image_digest: Optional[str] = None, region: Optional[str] = None,
    expires_at=None, source: str = "connector", metadata: Optional[dict] = None,
) -> Optional[dict]:
    """Create or refresh one asset's identity/inventory fields, keyed on
    (estate_label, connector_id, asset_key) — see idx_infra_assets_key's
    COALESCE(connector_id,0) comment for why ingest-sourced (connector_id
    NULL) assets still dedup correctly. Deliberately does NOT touch
    last_assessed_at/assessment_source here — inventory discovery (an asset
    exists, here's its version) and assessment (we actually checked it for
    findings) are different events; conflating them would let a connector
    that only lists assets accidentally mark them "assessed" with no real
    check behind that claim. Call mark_infra_asset_assessed() separately
    once a real check has run."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO observability.infra_assets (
                        estate_label, asset_key, connector_id, asset_type, name, environment, os_name, os_version,
                        software_name, software_version, ecosystem, image_digest, region, expires_at,
                        source, metadata, first_seen_at, last_seen_at, active
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW(),NOW(),TRUE)
                    ON CONFLICT (estate_label, COALESCE(connector_id, 0), asset_key) DO UPDATE SET
                        asset_type = EXCLUDED.asset_type,
                        name = EXCLUDED.name, environment = EXCLUDED.environment,
                        os_name = EXCLUDED.os_name, os_version = EXCLUDED.os_version,
                        software_name = EXCLUDED.software_name, software_version = EXCLUDED.software_version,
                        ecosystem = EXCLUDED.ecosystem, image_digest = EXCLUDED.image_digest,
                        region = EXCLUDED.region, expires_at = EXCLUDED.expires_at,
                        source = EXCLUDED.source,
                        metadata = EXCLUDED.metadata, last_seen_at = NOW(), active = TRUE
                    RETURNING {_INFRA_ASSET_COLUMNS}
                    """,
                    (estate_label, asset_key, connector_id, asset_type, name, environment, os_name, os_version,
                     software_name, software_version, ecosystem, image_digest, region, expires_at,
                     source, Json(metadata) if metadata is not None else None),
                )
                row = cur.fetchone()
                cols = [d[0] for d in cur.description]
                return _infra_asset_row_to_dict(cols, row)
    return _run(_do)


def mark_infra_asset_assessed(asset_key: str, assessment_source: str) -> bool:
    """Stamps last_assessed_at = NOW() — the one function that gets to make
    an asset stop looking "never assessed". Called by whatever actually ran
    a real check (tls_cert_tool, osv enrichment, a future scanner), never by
    inventory discovery alone."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.infra_assets
                    SET last_assessed_at = NOW(), assessment_source = %s
                    WHERE asset_key = %s
                    """,
                    (assessment_source, asset_key),
                )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


def list_assets_for_vuln_enrichment(limit: int = 500) -> list:
    """Active assets with enough identity to actually query OSV against:
    ecosystem + software_name + software_version all set. Nothing in Phase 1
    populates ecosystem (OSV has no PostgreSQL ecosystem — see
    version_baselines.py's docstring); this becomes non-empty once a Phase 3
    connector (AWS Inspector, container scanning) or a manual/ingest asset
    sets those three fields. Returning [] here is a real "nothing enrichable
    yet", not a bug — vulnerability_sweep.py must not treat it as failure."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_INFRA_ASSET_COLUMNS} FROM observability.infra_assets
                    WHERE active = TRUE AND ecosystem IS NOT NULL
                      AND software_name IS NOT NULL AND software_version IS NOT NULL
                    ORDER BY name
                    LIMIT %s
                    """,
                    (min(limit, 2000),),
                )
                cols = [d[0] for d in cur.description]
                return [_infra_asset_row_to_dict(cols, r) for r in cur.fetchall()]
    return _run(_do) or []


def list_infra_assets(asset_type: Optional[str] = None, unassessed_only: bool = False, limit: int = 500) -> list:
    def _do():
        filters, params = ["active = TRUE"], []
        if asset_type:
            filters.append("asset_type = %s"); params.append(asset_type)
        if unassessed_only:
            filters.append("last_assessed_at IS NULL")
        where = "WHERE " + " AND ".join(filters)
        params.append(min(limit, 2000))
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_INFRA_ASSET_COLUMNS} FROM observability.infra_assets
                    {where}
                    ORDER BY name
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                return [_infra_asset_row_to_dict(cols, r) for r in cur.fetchall()]
    return _run(_do) or []


def get_infra_asset(asset_key: str) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_INFRA_ASSET_COLUMNS} FROM observability.infra_assets WHERE asset_key = %s",
                    (asset_key,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                return _infra_asset_row_to_dict(cols, row)
    return _run(_do)


def list_expiring_credentials(warn_days: int = 30) -> list:
    """poll_connectors rows with credentials_expires_at inside the warning
    window or already past it — the credential-expiry half of Phase 1.
    Separate from list_infra_assets/certificate rows because a connector's
    credential isn't itself an infra_assets row; expiry_sweep.py checks
    both this and the certificate assets below in one pass."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, connector_type, display_name, credentials_expires_at
                    FROM observability.poll_connectors
                    WHERE active = TRUE AND credentials_expires_at IS NOT NULL
                      AND credentials_expires_at < NOW() + (%s || ' days')::interval
                    ORDER BY credentials_expires_at ASC
                    """,
                    (warn_days,),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("credentials_expires_at"):
                        d["credentials_expires_at"] = d["credentials_expires_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def list_expiring_infra_assets(warn_days: int = 30) -> list:
    """infra_assets rows (certificates, and any future expiring asset type)
    inside the warning window or already past it."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_INFRA_ASSET_COLUMNS} FROM observability.infra_assets
                    WHERE active = TRUE AND expires_at IS NOT NULL
                      AND expires_at < NOW() + (%s || ' days')::interval
                    ORDER BY expires_at ASC
                    """,
                    (warn_days,),
                )
                cols = [d[0] for d in cur.description]
                return [_infra_asset_row_to_dict(cols, r) for r in cur.fetchall()]
    return _run(_do) or []


# ─────────────────────────────────────────────────────────────────────────────
# Infrastructure Vulnerability & Currency Posture (Phase 2): vulnerability
# register (observability.infra_vulnerabilities) + OSV.dev response cache
# (observability.osv_cache)
# ─────────────────────────────────────────────────────────────────────────────

_INFRA_VULN_COLUMNS = (
    "id, asset_id, vuln_id, aliases, source, source_ref, severity, cvss_score, title, summary, "
    "affected_version, fixed_version, published_at, first_detected_at, last_seen_at, status, "
    "remediated_at, remediation_basis, waiver_id, evidence_record_id, disposition_reason, disposed_by, "
    "created_at, updated_at"
)
_INFRA_VULN_TS_FIELDS = ("published_at", "first_detected_at", "last_seen_at", "remediated_at", "created_at", "updated_at")


def _infra_vuln_row_to_dict(cols: list, row: tuple) -> dict:
    d = dict(zip(cols, row))
    for k in _INFRA_VULN_TS_FIELDS:
        if d.get(k) and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


def upsert_infra_vulnerability(
    vuln_id: str, asset_id: Optional[int] = None, aliases: Optional[list] = None,
    source: str = "osv", source_ref: Optional[str] = None, severity: str = "INFO",
    cvss_score: Optional[float] = None, title: Optional[str] = None, summary: Optional[str] = None,
    affected_version: Optional[str] = None, fixed_version: Optional[str] = None,
    published_at=None, evidence_record_id: Optional[int] = None,
) -> Optional[dict]:
    """Create or refresh one vulnerability finding, keyed on
    (asset_id, vuln_id, source_ref) — see idx_infra_vulns_dedup's
    COALESCE comment. A re-detected finding never resets status/remediated_at
    (ON CONFLICT DO UPDATE below deliberately omits both) — only
    update_infra_vulnerability_status() gets to move a finding off 'open'.
    A previously-remediated finding that OSV/a scanner reports again (e.g. a
    downgrade, or the initial detection was itself wrong) simply keeps its
    existing status/remediated_at; a human or the sweep's own
    version-comparison must explicitly re-open it."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO observability.infra_vulnerabilities (
                        asset_id, vuln_id, aliases, source, source_ref, severity, cvss_score,
                        title, summary, affected_version, fixed_version, published_at, evidence_record_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (COALESCE(asset_id, 0), vuln_id, COALESCE(source_ref, '')) DO UPDATE SET
                        aliases = EXCLUDED.aliases, severity = EXCLUDED.severity, cvss_score = EXCLUDED.cvss_score,
                        title = EXCLUDED.title, summary = EXCLUDED.summary,
                        affected_version = EXCLUDED.affected_version, fixed_version = EXCLUDED.fixed_version,
                        published_at = EXCLUDED.published_at, evidence_record_id = EXCLUDED.evidence_record_id,
                        last_seen_at = NOW(), updated_at = NOW()
                    RETURNING {_INFRA_VULN_COLUMNS}
                    """,
                    (asset_id, vuln_id, aliases, source, source_ref, severity, cvss_score,
                     title, summary, affected_version, fixed_version, published_at, evidence_record_id),
                )
                row = cur.fetchone()
                cols = [d[0] for d in cur.description]
                return _infra_vuln_row_to_dict(cols, row)
    return _run(_do)


def update_infra_vulnerability_status(
    vuln_row_id: int, status: str, remediation_basis: Optional[str] = None,
    waiver_id: Optional[int] = None, disposition_reason: Optional[str] = None,
    disposed_by: Optional[str] = None,
) -> bool:
    """The only function that moves a finding off 'open' — called by
    vulnerability_sweep.py's version-advanced detection (status='remediated',
    remediation_basis='version_advanced') or by an analyst's disposition
    (accepted_risk/false_positive, with a mandatory reason). remediated_at is
    stamped only when status='remediated', never for accepted_risk/
    false_positive — those aren't fixes, they're decisions about an open one."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.infra_vulnerabilities
                    SET status = %s, remediation_basis = %s, waiver_id = %s,
                        disposition_reason = %s, disposed_by = %s, updated_at = NOW(),
                        remediated_at = CASE WHEN %s = 'remediated' THEN NOW() ELSE remediated_at END
                    WHERE id = %s
                    """,
                    (status, remediation_basis, waiver_id, disposition_reason, disposed_by, status, vuln_row_id),
                )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


def list_infra_vulnerabilities(
    status: Optional[str] = None, severity: Optional[str] = None,
    asset_id: Optional[int] = None, source: Optional[str] = None, limit: int = 500,
) -> list:
    def _do():
        filters, params = [], []
        if status:
            filters.append("status = %s"); params.append(status)
        if severity:
            filters.append("severity = %s"); params.append(severity)
        if asset_id:
            filters.append("asset_id = %s"); params.append(asset_id)
        if source:
            filters.append("source = %s"); params.append(source)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(min(limit, 2000))
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_INFRA_VULN_COLUMNS} FROM observability.infra_vulnerabilities
                    {where}
                    ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                             first_detected_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                return [_infra_vuln_row_to_dict(cols, r) for r in cur.fetchall()]
    return _run(_do) or []


def list_open_vulnerabilities_with_asset_version() -> list:
    """Open vulnerabilities joined to their asset's CURRENT software_version —
    the exact pair vulnerability_sweep.py's version-advanced remediation
    check needs, in one query instead of N+1 per-asset lookups. Only rows
    where both fixed_version and the asset's software_version are non-NULL
    are returned — nothing here has ever compared strings against 'unknown'."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT v.id, v.vuln_id, v.fixed_version, a.software_version, a.asset_key
                    FROM observability.infra_vulnerabilities v
                    JOIN observability.infra_assets a ON a.id = v.asset_id
                    WHERE v.status = 'open' AND v.fixed_version IS NOT NULL
                      AND a.software_version IS NOT NULL AND a.active = TRUE
                    """
                )
                cols = ["id", "vuln_id", "fixed_version", "software_version", "asset_key"]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
    return _run(_do) or []


def get_vulnerability_summary() -> dict:
    """Coverage-aware summary: open-finding counts by severity, PLUS how many
    assets have ever been assessed vs. total — every count in this dict must
    be read alongside that coverage fraction, never as a standalone "all
    clear" (see the feature's honest-gaps note: this reports 'no known-open
    findings from connected sources', not 'no vulnerabilities exist')."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT severity, COUNT(*) FROM observability.infra_vulnerabilities WHERE status = 'open' GROUP BY severity"
                )
                by_severity = {row[0]: row[1] for row in cur.fetchall()}
                cur.execute("SELECT COUNT(*) FROM observability.infra_assets WHERE active = TRUE")
                total_assets = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM observability.infra_assets WHERE active = TRUE AND last_assessed_at IS NOT NULL")
                assessed_assets = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM observability.infra_vulnerabilities WHERE status = 'remediated' AND remediated_at > NOW() - INTERVAL '30 days'"
                )
                remediated_last_30d = cur.fetchone()[0]
                return {
                    "open_by_severity": by_severity,
                    "open_total": sum(by_severity.values()),
                    "assets_total": total_assets,
                    "assets_assessed": assessed_assets,
                    "remediated_last_30d": remediated_last_30d,
                }
    return _run(_do) or {"open_by_severity": {}, "open_total": 0, "assets_total": 0, "assets_assessed": 0, "remediated_last_30d": 0}


def get_osv_cache_entry(ecosystem: str, package_name: str, version: str, max_age_hours: int = 24) -> Optional[list]:
    """Returns the cached vulns[] list if a fresh-enough entry exists, else
    None — None means "go query OSV", not "no vulnerabilities" (an empty
    list [] is itself a valid, cached "queried, found none" result)."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT vulns FROM observability.osv_cache
                    WHERE ecosystem = %s AND package_name = %s AND version = %s
                      AND queried_at > NOW() - (%s || ' hours')::interval
                    """,
                    (ecosystem, package_name, version, max_age_hours),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def put_osv_cache_entry(ecosystem: str, package_name: str, version: str, vulns: list) -> None:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.osv_cache (ecosystem, package_name, version, vulns, queried_at)
                    VALUES (%s, %s, %s, %s::jsonb, NOW())
                    ON CONFLICT (ecosystem, package_name, version) DO UPDATE SET
                        vulns = EXCLUDED.vulns, queried_at = NOW()
                    """,
                    (ecosystem, package_name, version, Json(vulns)),
                )
    _run(_do)


def get_scm_repository_state(resource: str) -> Optional[dict]:
    """Last-known compliance dict for one repo (server_name/repo_ref@branch), or
    None if this is the first audit ever recorded for it."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT compliance FROM observability.scm_repository_state WHERE resource = %s",
                    (resource,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


def record_scm_audit_snapshot(resource: str, compliance: dict) -> list:
    """Diff `compliance` against the last-recorded state for `resource` (via
    scm_connectors.diff_compliance), persist the new state, and open/resolve
    scm_drift_events rows for whatever changed. Returns the list of drift
    events detected THIS call (empty on a repo's very first audit, and empty
    when nothing changed since the last one).

    Called after every audit — both the scheduled poll-connector path
    (github_scm_tool.py/gitlab_scm_tool.py) and the on-demand "run now" path
    (scm_audit_endpoints.py) — so a control that's flipped and flipped back
    between two consecutive audits still leaves a resolved drift_events row,
    which is the whole point (catching a short-lived "2am override")."""
    import scm_connectors as _scm_connectors

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT compliance FROM observability.scm_repository_state WHERE resource = %s",
                    (resource,),
                )
                row = cur.fetchone()
                baseline = row[0] if row else None

                cur.execute(
                    """
                    INSERT INTO observability.scm_repository_state (resource, compliance, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (resource) DO UPDATE SET compliance = EXCLUDED.compliance, updated_at = NOW()
                    """,
                    (resource, Json(compliance)),
                )

                if baseline is None:
                    return []

                diffs = _scm_connectors.diff_compliance(baseline, compliance)
                detected: list[dict] = []
                for d in diffs:
                    if d["direction"] == "regressed":
                        cur.execute(
                            """
                            INSERT INTO observability.scm_drift_events
                                (resource, control_name, expected_state, actual_state, direction)
                            VALUES (%s, %s, %s::jsonb, %s::jsonb, 'regressed')
                            RETURNING id, detected_at
                            """,
                            (resource, d["control_name"], Json(d["expected_state"]), Json(d["actual_state"])),
                        )
                        new_id, detected_at = cur.fetchone()
                        detected.append({**d, "id": new_id, "detected_at": detected_at.isoformat(), "resource": resource})
                    else:  # improved -> auto-resolve the most recent open regression for this control
                        cur.execute(
                            """
                            UPDATE observability.scm_drift_events
                            SET resolved_at = NOW()
                            WHERE id = (
                                SELECT id FROM observability.scm_drift_events
                                WHERE resource = %s AND control_name = %s AND resolved_at IS NULL
                                ORDER BY detected_at DESC LIMIT 1
                            )
                            RETURNING id
                            """,
                            (resource, d["control_name"]),
                        )
                        resolved = cur.fetchone()
                        if resolved:
                            detected.append({**d, "id": resolved[0], "resource": resource})
            return detected
    return _run(_do) or []


def list_scm_drift_events(resource: Optional[str] = None, open_only: bool = False, limit: int = 100) -> list:
    def _do():
        filters, params = [], []
        if resource:
            filters.append("resource = %s"); params.append(resource)
        if open_only:
            filters.append("resolved_at IS NULL")
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(min(limit, 500))
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, resource, control_name, expected_state, actual_state, direction,
                           detected_at, resolved_at
                    FROM observability.scm_drift_events
                    {where}
                    ORDER BY detected_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("detected_at"):
                        d["detected_at"] = d["detected_at"].isoformat()
                    if d.get("resolved_at"):
                        d["resolved_at"] = d["resolved_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


# ─────────────────────────────────────────────────────────────────────────────
# DevOps Monitoring: Risk Waiver & Exception Hub (observability.risk_waivers)
# ─────────────────────────────────────────────────────────────────────────────

def create_risk_waiver(vulnerability_hash: str, reason: str, compensating_control: Optional[str],
                        approved_by: str, approval_task_id: Optional[int], expires_at) -> Optional[int]:
    """Insert an ACTIVE waiver. Only one ACTIVE waiver per vulnerability_hash
    can exist (idx_risk_waivers_active_hash) — approving a new one for an
    already-waived hash should REVOKE the old one first (the approvals
    endpoint checks this), not silently collide."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.risk_waivers
                        (vulnerability_hash, reason, compensating_control, approved_by,
                         approval_task_id, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (vulnerability_hash, reason, compensating_control, approved_by,
                     approval_task_id, expires_at),
                )
                return cur.fetchone()[0]
    return _run(_do)


def list_risk_waivers(status: Optional[str] = None, limit: int = 100) -> list:
    def _do():
        filters, params = [], []
        if status:
            filters.append("status = %s"); params.append(status.upper())
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(min(limit, 500))
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, vulnerability_hash, reason, compensating_control, approved_by,
                           approval_task_id, expires_at, created_at, status
                    FROM observability.risk_waivers
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    for k in ("expires_at", "created_at"):
                        if d.get(k):
                            d[k] = d[k].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def get_active_waiver(vulnerability_hash: str) -> Optional[dict]:
    rows = [w for w in list_risk_waivers(status="ACTIVE", limit=500) if w["vulnerability_hash"] == vulnerability_hash]
    return rows[0] if rows else None


def revoke_risk_waiver(waiver_id: int) -> bool:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE observability.risk_waivers SET status = 'REVOKED' WHERE id = %s AND status = 'ACTIVE'",
                    (waiver_id,),
                )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


def expire_overdue_waivers() -> list:
    """Flip ACTIVE -> EXPIRED for every waiver past its expires_at. Returns the
    rows just expired so the caller (risk_waiver_sweep.py) can re-open/re-escalate
    each one — 'automated expiry' means the control goes back to failing, not
    that the waiver silently lapses with no one told."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.risk_waivers
                    SET status = 'EXPIRED'
                    WHERE status = 'ACTIVE' AND expires_at < NOW()
                    RETURNING id, vulnerability_hash, reason, compensating_control, approved_by, expires_at
                    """,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("expires_at"):
                        d["expires_at"] = d["expires_at"].isoformat()
                    rows.append(d)
            return rows
    return _run(_do) or []


# ─────────────────────────────────────────────────────────────────────────────
# Continuous Third-Party/Vendor Risk (observability.vendor_risk_profiles)
# ─────────────────────────────────────────────────────────────────────────────

def upsert_vendor_risk_profile(
    vendor_name: str, vendor_id: Optional[str] = None, critical: bool = False,
    soc2_report_date: Optional[str] = None, soc2_expires_at: Optional[str] = None,
) -> Optional[int]:
    """Create or update a vendor's risk profile. A new/renewed SOC 2 date
    resets status back to CURRENT — an auditor uploading a fresh report is
    exactly the "un-expire" action, mirroring how a new risk_waivers row
    naturally supersedes the old ACTIVE-vs-EXPIRED state."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.vendor_risk_profiles
                        (vendor_name, vendor_id, critical, soc2_report_date, soc2_expires_at, status, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'CURRENT', NOW())
                    ON CONFLICT (vendor_name) DO UPDATE SET
                        vendor_id = EXCLUDED.vendor_id,
                        critical = EXCLUDED.critical,
                        soc2_report_date = EXCLUDED.soc2_report_date,
                        soc2_expires_at = EXCLUDED.soc2_expires_at,
                        status = 'CURRENT',
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (vendor_name, vendor_id, critical, soc2_report_date, soc2_expires_at),
                )
                return cur.fetchone()[0]
    return _run(_do)


def list_vendor_risk_profiles(critical_only: bool = False) -> list:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, vendor_name, vendor_id, critical, soc2_report_date,
                           soc2_expires_at, status, created_at, updated_at
                    FROM observability.vendor_risk_profiles
                    WHERE (%s = FALSE OR critical = TRUE)
                    ORDER BY vendor_name
                    """,
                    (critical_only,),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    for k in ("soc2_report_date", "soc2_expires_at", "created_at", "updated_at"):
                        if d.get(k) is not None:
                            d[k] = d[k].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def expire_overdue_vendor_soc2() -> list:
    """Flip CURRENT -> EXPIRED for every vendor past its soc2_expires_at.
    Returns the rows just expired so vendor_risk_sweep.py can raise a fresh
    finding for each one — mirrors expire_overdue_waivers exactly."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.vendor_risk_profiles
                    SET status = 'EXPIRED', updated_at = NOW()
                    WHERE status = 'CURRENT' AND soc2_expires_at < NOW()
                    RETURNING id, vendor_name, vendor_id, critical, soc2_expires_at
                    """,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("soc2_expires_at"):
                        d["soc2_expires_at"] = d["soc2_expires_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


# ─────────────────────────────────────────────────────────────────────────────
# Exception Management retention (exception_control_events)
# ─────────────────────────────────────────────────────────────────────────────
# exception_control_events.raw_payload/actor carries unredacted source-system
# event data (can include employee names/emails from HR/SoD connectors) with
# no prior TTL — this closes that gap. exception_model_inferences and
# exception_auditor_triage both reference event_id ON DELETE CASCADE, so
# deleting the parent row here cleanly removes its scoring/triage history too
# rather than leaving orphans.

DEFAULT_EXCEPTION_EVENT_RETENTION_DAYS = 400


def purge_expired_exception_events(retention_days: int = DEFAULT_EXCEPTION_EVENT_RETENTION_DAYS) -> int:
    """Delete exception_control_events rows (and their cascaded
    inferences/triage) older than retention_days, keyed off created_at (when
    the row was ingested), not event_timestamp (which reflects the source
    system's own clock and can be backdated by a connector's historical
    backfill). Returns the number of rows deleted."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM exception_control_events
                    WHERE created_at < NOW() - (%s || ' days')::interval
                    """,
                    (retention_days,),
                )
                return cur.rowcount
    return _run(_do, default=0)


# ─────────────────────────────────────────────────────────────────────────────
# AI Governance (observability.ai_system_registry)
# ─────────────────────────────────────────────────────────────────────────────

def upsert_ai_system(
    system_name: str, vendor: Optional[str] = None, business_owner: Optional[str] = None,
    risk_tier: str = "MEDIUM", requires_human_oversight: bool = False,
    human_oversight_defined: bool = False,
    last_assessment_date: Optional[str] = None, assessment_expires_at: Optional[str] = None,
) -> Optional[int]:
    """Create or update an AI system's governance profile. A fresh
    assessment date resets status back to CURRENT, same "un-expire" semantics
    as upsert_vendor_risk_profile."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.ai_system_registry
                        (system_name, vendor, business_owner, risk_tier,
                         requires_human_oversight, human_oversight_defined,
                         last_assessment_date, assessment_expires_at, status, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'CURRENT', NOW())
                    ON CONFLICT (system_name) DO UPDATE SET
                        vendor = EXCLUDED.vendor,
                        business_owner = EXCLUDED.business_owner,
                        risk_tier = EXCLUDED.risk_tier,
                        requires_human_oversight = EXCLUDED.requires_human_oversight,
                        human_oversight_defined = EXCLUDED.human_oversight_defined,
                        last_assessment_date = EXCLUDED.last_assessment_date,
                        assessment_expires_at = EXCLUDED.assessment_expires_at,
                        status = 'CURRENT',
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (system_name, vendor, business_owner, risk_tier,
                     requires_human_oversight, human_oversight_defined,
                     last_assessment_date, assessment_expires_at),
                )
                return cur.fetchone()[0]
    return _run(_do)


def list_ai_systems(high_risk_only: bool = False) -> list:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, system_name, vendor, business_owner, risk_tier,
                           requires_human_oversight, human_oversight_defined,
                           last_assessment_date, assessment_expires_at, status,
                           created_at, updated_at
                    FROM observability.ai_system_registry
                    WHERE (%s = FALSE OR risk_tier = 'HIGH')
                    ORDER BY system_name
                    """,
                    (high_risk_only,),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    for k in ("last_assessment_date", "assessment_expires_at", "created_at", "updated_at"):
                        if d.get(k) is not None:
                            d[k] = d[k].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def expire_overdue_ai_assessments() -> list:
    """Flip CURRENT -> EXPIRED for every AI system past its
    assessment_expires_at. Mirrors expire_overdue_vendor_soc2 exactly."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.ai_system_registry
                    SET status = 'EXPIRED', updated_at = NOW()
                    WHERE status = 'CURRENT' AND assessment_expires_at < NOW()
                    RETURNING id, system_name, vendor, risk_tier, assessment_expires_at
                    """,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("assessment_expires_at"):
                        d["assessment_expires_at"] = d["assessment_expires_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


# ─────────────────────────────────────────────────────────────────────────────
# DevOps Monitoring: pipeline provenance/attestation (observability.pipeline_attestations)
# ─────────────────────────────────────────────────────────────────────────────

def insert_pipeline_attestation(
    commit_sha: str, pipeline_run_id: Optional[str], oidc_actor: Optional[str],
    oidc_claims: Optional[dict], slsa_provenance: Optional[dict], slsa_level: Optional[int],
    env_vars_hash: Optional[str], runner_type: Optional[str], runner_id: Optional[str],
    container_image_sha: Optional[str], cosign_bundle: Optional[dict], cosign_verified: Optional[str],
    sbom_format: Optional[str], sbom: Optional[dict], license_risk: bool,
) -> Optional[int]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.pipeline_attestations (
                        commit_sha, pipeline_run_id, oidc_actor, oidc_claims, slsa_provenance,
                        slsa_level, env_vars_hash, runner_type, runner_id, container_image_sha,
                        cosign_bundle, cosign_verified, sbom_format, sbom, license_risk
                    ) VALUES (
                        %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s
                    )
                    RETURNING id
                    """,
                    (commit_sha, pipeline_run_id, oidc_actor,
                     Json(oidc_claims) if oidc_claims is not None else None,
                     Json(slsa_provenance) if slsa_provenance is not None else None,
                     slsa_level, env_vars_hash, runner_type, runner_id, container_image_sha,
                     Json(cosign_bundle) if cosign_bundle is not None else None,
                     cosign_verified, sbom_format,
                     Json(sbom) if sbom is not None else None, license_risk),
                )
                return cur.fetchone()[0]
    return _run(_do)


def list_pipeline_attestations(commit_sha: Optional[str] = None, limit: int = 50) -> list:
    def _do():
        filters, params = [], []
        if commit_sha:
            filters.append("commit_sha = %s"); params.append(commit_sha)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(min(limit, 500))
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, commit_sha, pipeline_run_id, oidc_actor, slsa_level, env_vars_hash,
                           runner_type, runner_id, container_image_sha, cosign_verified,
                           sbom_format, license_risk, created_at
                    FROM observability.pipeline_attestations
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("created_at"):
                        d["created_at"] = d["created_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def get_pipeline_attestation(attestation_id: int) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, commit_sha, pipeline_run_id, oidc_actor, oidc_claims, slsa_provenance,
                           slsa_level, env_vars_hash, runner_type, runner_id, container_image_sha,
                           cosign_bundle, cosign_verified, sbom_format, sbom, license_risk, created_at
                    FROM observability.pipeline_attestations
                    WHERE id = %s
                    """,
                    (attestation_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                d = dict(zip(cols, row))
                if d.get("created_at"):
                    d["created_at"] = d["created_at"].isoformat()
                return d
    return _run(_do)


# ─────────────────────────────────────────────────────────────────────────────
# DevOps Monitoring: ITSM/Jira-ServiceNow SLA Bridge (observability.itsm_tickets)
# ─────────────────────────────────────────────────────────────────────────────

_ITSM_TICKET_COLUMNS = (
    "id, finding_hash, external_system, external_ticket_key, connector_id, summary, "
    "severity, status, sla_hours, sla_due_at, sla_breached_at, created_by, created_at, updated_at"
)
_ITSM_TIMESTAMP_FIELDS = ("sla_due_at", "sla_breached_at", "created_at", "updated_at")


def _itsm_row_to_dict(cols: list, row: tuple) -> dict:
    d = dict(zip(cols, row))
    for k in _ITSM_TIMESTAMP_FIELDS:
        if d.get(k):
            d[k] = d[k].isoformat()
    return d


def create_itsm_ticket(finding_hash: str, external_system: str, external_ticket_key: str,
                        connector_id: Optional[int], summary: Optional[str], severity: str,
                        sla_hours: int, sla_due_at, created_by: str) -> Optional[int]:
    """Insert a ticket tracking row. Only one open (non-closed/cancelled)
    ticket per finding_hash can exist (idx_itsm_tickets_active_hash) — the
    caller is expected to check get_open_ticket_for_finding first and reuse
    it rather than opening a duplicate against the same finding."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.itsm_tickets
                        (finding_hash, external_system, external_ticket_key, connector_id,
                         summary, severity, sla_hours, sla_due_at, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (finding_hash, external_system, external_ticket_key, connector_id,
                     summary, severity.upper(), sla_hours, sla_due_at, created_by),
                )
                return cur.fetchone()[0]
    return _run(_do)


def list_itsm_tickets(status: Optional[str] = None, external_system: Optional[str] = None,
                       breached_only: bool = False, limit: int = 100) -> list:
    def _do():
        filters, params = [], []
        if status:
            filters.append("status = %s"); params.append(status.lower())
        if external_system:
            filters.append("external_system = %s"); params.append(external_system.lower())
        if breached_only:
            filters.append("sla_breached_at IS NOT NULL")
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(min(limit, 500))
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_ITSM_TICKET_COLUMNS}
                    FROM observability.itsm_tickets
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                return [_itsm_row_to_dict(cols, r) for r in cur.fetchall()]
    return _run(_do) or []


def get_itsm_ticket(ticket_id: int) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_ITSM_TICKET_COLUMNS} FROM observability.itsm_tickets WHERE id = %s",
                    (ticket_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                return _itsm_row_to_dict(cols, row)
    return _run(_do)


def get_open_ticket_for_finding(finding_hash: str) -> Optional[dict]:
    rows = [t for t in list_itsm_tickets(limit=500) if t["finding_hash"] == finding_hash
            and t["status"] not in ("closed", "cancelled")]
    return rows[0] if rows else None


def get_itsm_ticket_by_external_key(external_system: str, external_ticket_key: str) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_ITSM_TICKET_COLUMNS} FROM observability.itsm_tickets
                    WHERE external_system = %s AND external_ticket_key = %s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (external_system.lower(), external_ticket_key),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                return _itsm_row_to_dict(cols, row)
    return _run(_do)


def update_itsm_ticket_status(ticket_id: int, status: str) -> bool:
    """Reconciles our record with the external system's current status —
    called by itsm_jira_tool.py/itsm_servicenow_tool.py's poll adapter and by
    the webhook path for real-time closure. Does not touch sla_breached_at —
    that's itsm_sla_sweep.py's job, run independently of ticket status so a
    late-arriving 'resolved' doesn't erase the fact that it missed its SLA.

    Sets resolved_at exactly once, on the transition INTO resolved/closed —
    the DORA MTTR metric (compute_dora_metrics) needs a real resolution
    timestamp, not updated_at (which bumps on any field change). Re-closing
    an already-resolved ticket (idempotent re-sync) leaves resolved_at as
    the FIRST resolution time, not the latest sync — COALESCE keeps it from
    being overwritten on every subsequent poll."""
    status = status.lower()
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                if status in ("resolved", "closed"):
                    cur.execute(
                        "UPDATE observability.itsm_tickets "
                        "SET status = %s, updated_at = NOW(), resolved_at = COALESCE(resolved_at, NOW()) "
                        "WHERE id = %s",
                        (status, ticket_id),
                    )
                else:
                    cur.execute(
                        "UPDATE observability.itsm_tickets SET status = %s, updated_at = NOW(), resolved_at = NULL "
                        "WHERE id = %s",
                        (status, ticket_id),
                    )
                return cur.rowcount > 0
    return _run(_do, default=False) or False


def expire_overdue_sla() -> list:
    """Flag every open ticket past its sla_due_at with sla_breached_at, once.
    Returns the rows just flagged so the caller (itsm_sla_sweep.py) can
    re-escalate each one's underlying finding — mirrors expire_overdue_waivers'
    'automated expiry re-opens the finding, doesn't silently lapse' contract."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.itsm_tickets
                    SET sla_breached_at = NOW()
                    WHERE sla_breached_at IS NULL AND status NOT IN ('closed', 'cancelled')
                      AND sla_due_at < NOW()
                    RETURNING id, finding_hash, external_system, external_ticket_key, severity, sla_due_at
                    """,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("sla_due_at"):
                        d["sla_due_at"] = d["sla_due_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def fetch_scm_audit_results(resource: Optional[str] = None, limit: int = 50) -> list:
    """SCM branch-protection audit rows (scm_audit_endpoints.py's on-demand
    'run now' path and real GitHub/GitLab webhooks both land here via
    github_endpoints._write_adjudication). Without `resource`, returns the
    single latest row per repo (server_name) — the Branch Integrity Matrix
    feed; with it, full history for that one repo, newest first."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                params: list = ["GITHUB", "GITLAB", "BITBUCKET",
                                 "branch_protection_rule", "protected_branch_audit", "branch_restriction_audit"]
                if resource:
                    query = """
                        SELECT id, adjudicated_at, source_system, server_name, target_tool,
                               uro_id, risk_score, risk_tier, final_verdict, requires_human_review,
                               policy_violations
                        FROM observability.adjudicated_tool_calls
                        WHERE source_system IN (%s, %s, %s) AND target_tool IN (%s, %s, %s)
                          AND server_name = %s
                        ORDER BY adjudicated_at DESC
                        LIMIT %s
                    """
                    params += [resource, min(limit, 500)]
                else:
                    query = """
                        SELECT DISTINCT ON (server_name)
                               id, adjudicated_at, source_system, server_name, target_tool,
                               uro_id, risk_score, risk_tier, final_verdict, requires_human_review,
                               policy_violations
                        FROM observability.adjudicated_tool_calls
                        WHERE source_system IN (%s, %s, %s) AND target_tool IN (%s, %s, %s)
                        ORDER BY server_name, adjudicated_at DESC
                        LIMIT %s
                    """
                    params += [min(limit, 500)]
                cur.execute(query, params)
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("adjudicated_at"):
                        d["adjudicated_at"] = d["adjudicated_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def fetch_pipeline_security_results(limit: int = 50) -> list:
    """Pipeline-as-code (GitHub Actions workflow) audit rows — same shape and
    idiom as fetch_scm_audit_results, filtered to the distinct
    workflow_security_audit target_tool scm_audit_endpoints.py's on-demand
    path and github_scm_tool.py's scheduled poll both write. Single-value
    filter (not IN, unlike branch protection) since this check is GitHub-only."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (server_name)
                           id, adjudicated_at, source_system, server_name, target_tool,
                           uro_id, risk_score, risk_tier, final_verdict, requires_human_review,
                           policy_violations
                    FROM observability.adjudicated_tool_calls
                    WHERE source_system = 'GITHUB' AND target_tool = 'workflow_security_audit'
                    ORDER BY server_name, adjudicated_at DESC
                    LIMIT %s
                    """,
                    (min(limit, 500),),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("adjudicated_at"):
                        d["adjudicated_at"] = d["adjudicated_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def fetch_deploy_gate_results(limit: int = 50) -> list:
    """Deploy-gate-bypass audit rows (Technology Risk Pipeline, POL-GH-005) —
    same shape/idiom as fetch_pipeline_security_results. Every row here
    represents a real check outcome (approved or not); unlike secret-scan,
    a clean/approved deploy is still adjudicated and shows up here, so this
    is a status feed, not a violations-only one."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (server_name)
                           id, adjudicated_at, source_system, server_name, target_tool,
                           uro_id, risk_score, risk_tier, final_verdict, requires_human_review,
                           policy_violations
                    FROM observability.adjudicated_tool_calls
                    WHERE source_system = 'GITHUB' AND target_tool = 'deploy_gate_audit'
                    ORDER BY server_name, adjudicated_at DESC
                    LIMIT %s
                    """,
                    (min(limit, 500),),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("adjudicated_at"):
                        d["adjudicated_at"] = d["adjudicated_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def fetch_secret_scan_results(limit: int = 50) -> list:
    """Real gitleaks scan rows — same shape/idiom as
    fetch_pipeline_security_results. A clean scan is never adjudicated (see
    scm_audit_endpoints._adjudicate_secret_scan), so every row returned here
    represents an actual finding, never a false 'compliant' status."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (server_name)
                           id, adjudicated_at, source_system, server_name, target_tool,
                           uro_id, risk_score, risk_tier, final_verdict, requires_human_review,
                           policy_violations
                    FROM observability.adjudicated_tool_calls
                    WHERE source_system = 'GITHUB' AND target_tool = 'gitleaks_scan'
                    ORDER BY server_name, adjudicated_at DESC
                    LIMIT %s
                    """,
                    (min(limit, 500),),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("adjudicated_at"):
                        d["adjudicated_at"] = d["adjudicated_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


# Every connector_type whose adapter writes event_type='infrastructure_finding'
# rows on every poll tick (pass or fail — see postgres_cis_tool.pull_events'
# docstring). A single module constant rather than three separate hardcoded
# tuples (previously one per query branch here) — a new infra connector type
# needing to appear in the Infrastructure Posture matrix now means editing
# this one line, not hunting three literal IN-lists that drift out of sync.
_INFRA_MONITORING_SYSTEM_TYPES = ("postgres_cis", "railway_iaas", "aws_iaas", "aws_patch", "aws_inspector", "ot_heartbeat", "tls_cert")


def fetch_infra_monitoring_results(resource: Optional[str] = None, limit: int = 50) -> list:
    """Postgres CIS + Railway platform/deployment drift + TLS certificate
    expiry audit rows (postgres_cis_tool.py / railway_iaas_tool.py /
    tls_cert_tool.py, via connector_poller.py's scheduled ticks and
    infrastructure_monitoring_endpoints.py's on-demand 'run now' — both
    write through the same mcp_governance._ingest_system_event path into
    observability.system_telemetry, unlike the SCM checks above which go
    through the UBO adjudication pipeline). Unlike SCM/pipeline security,
    event_type='infrastructure_finding' is written on EVERY poll tick
    regardless of pass/fail, so this is a full status matrix, not
    violations-only. Grouped by (server_name, resource) rather than
    server_name alone — a single Railway connector covers many services,
    one event per service instance."""
    def _do():
        placeholders = ", ".join(["%s"] * len(_INFRA_MONITORING_SYSTEM_TYPES))
        params: list = list(_INFRA_MONITORING_SYSTEM_TYPES) + ["infrastructure_finding"]
        with _conn() as conn:
            with conn.cursor() as cur:
                if resource:
                    query = f"""
                        SELECT id, created_at, system_type, server_name, resource,
                               actor, action, severity, risk_flags, raw_payload
                        FROM observability.system_telemetry
                        WHERE system_type IN ({placeholders}) AND event_type = %s
                          AND resource = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    """
                    params += [resource, min(limit, 500)]
                else:
                    query = f"""
                        SELECT DISTINCT ON (server_name, resource)
                               id, created_at, system_type, server_name, resource,
                               actor, action, severity, risk_flags, raw_payload
                        FROM observability.system_telemetry
                        WHERE system_type IN ({placeholders}) AND event_type = %s
                        ORDER BY server_name, resource, created_at DESC
                        LIMIT %s
                    """
                    params += [min(limit, 500)]
                cur.execute(query, params)
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    if d.get("created_at"):
                        d["created_at"] = d["created_at"].isoformat()
                    rows.append(d)
                return rows
    return _run(_do) or []


def get_observability_24h_counts() -> dict:
    """Rows adjudicated / escalated / PaC-violation-flagged in the last 24h —
    none of the existing observability endpoints (/systems, /holds, /coverage)
    are time-windowed, so this is the one new query the Continuous Monitoring
    command-center screen needs that nothing else already provides."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS adjudicated,
                        COUNT(*) FILTER (WHERE final_verdict = 'ESCALATE') AS escalated,
                        COUNT(*) FILTER (WHERE policy_violations IS NOT NULL AND array_length(policy_violations, 1) > 0) AS pac_violations
                    FROM observability.adjudicated_tool_calls
                    WHERE adjudicated_at > NOW() - INTERVAL '24 hours'
                    """
                )
                row = cur.fetchone()
                return {"adjudicated": row[0] or 0, "escalated": row[1] or 0, "pac_violations": row[2] or 0}
    return _run(_do) or {"adjudicated": 0, "escalated": 0, "pac_violations": 0}


def get_observability_hourly_series() -> dict:
    """
    24 hourly buckets (oldest first) of adjudicated/escalated counts — the
    per-tile trend behind the Continuous Monitoring command-center's static
    24h totals. generate_series fills hours with zero activity so the series
    has no gaps (a real requirement for a sparkline, not a nice-to-have —
    a gapped series reads as a data problem, not as "nothing happened").
    """
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        h.hour,
                        COALESCE(COUNT(a.id), 0) AS adjudicated,
                        COALESCE(COUNT(a.id) FILTER (WHERE a.final_verdict = 'ESCALATE'), 0) AS escalated
                    FROM generate_series(
                        date_trunc('hour', NOW() - INTERVAL '23 hours'),
                        date_trunc('hour', NOW()),
                        INTERVAL '1 hour'
                    ) AS h(hour)
                    LEFT JOIN observability.adjudicated_tool_calls a
                        ON date_trunc('hour', a.adjudicated_at) = h.hour
                    GROUP BY h.hour
                    ORDER BY h.hour
                    """
                )
                rows = cur.fetchall()
                return {
                    "hours": [r[0].isoformat() for r in rows],
                    "adjudicated": [int(r[1]) for r in rows],
                    "escalated": [int(r[2]) for r in rows],
                }
    return _run(_do) or {"hours": [], "adjudicated": [], "escalated": []}


# ─────────────────────────────────────────────────────────────────────────────
# Policy-as-Code external hooks
# ─────────────────────────────────────────────────────────────────────────────

def upsert_pac_hook(hook_type: str, config: dict) -> bool:
    """Upsert a GitHub or Confluence hook config. Returns True on success."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pac_external_hooks (hook_type, config_json, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (hook_type) DO UPDATE SET
                        config_json = EXCLUDED.config_json,
                        updated_at  = NOW()
                    """,
                    (hook_type, Json(config)),
                )
        return True
    return _run(_do, default=False) or False


def get_pac_hook(hook_type: str) -> Optional[dict]:
    """Return config for a specific hook type, or None."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT hook_type, config_json, updated_at FROM pac_external_hooks WHERE hook_type = %s",
                    (hook_type,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "hook_type": row[0], "config": row[1],
                    "updated_at": row[2].isoformat() if row[2] else None,
                }
    return _run(_do)


def get_all_pac_hooks() -> dict:
    """Return all hook configs keyed by hook_type."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT hook_type, config_json, updated_at FROM pac_external_hooks")
                return {
                    row[0]: {
                        "config": row[1],
                        "updated_at": row[2].isoformat() if row[2] else None,
                    }
                    for row in cur.fetchall()
                }
    return _run(_do) or {}
