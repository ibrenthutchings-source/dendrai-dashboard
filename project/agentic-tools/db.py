#!/usr/bin/env python3
"""
PostgreSQL persistence layer — normalized schema for Dendrai Risk Loop.

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

import logging
import os
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

logger = logging.getLogger(__name__)
_pool: Optional["pg_pool.ThreadedConnectionPool"] = None

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
    accession_number VARCHAR(20)
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
ALTER TABLE risk_register_reviews ADD COLUMN IF NOT EXISTS rac_yaml TEXT;
ALTER TABLE hitl_sessions ADD COLUMN IF NOT EXISTS gate3_status VARCHAR(16);
ALTER TABLE hitl_sessions ADD COLUMN IF NOT EXISTS gate4_status VARCHAR(16);
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
"""

# pgvector DDL — kept separate so a missing extension never breaks the core schema.
# ── Observability schema DDL ──────────────────────────────────────────────────
# Mirrors telemetry_schema.sql. Applied in init_db() so the schema is
# self-healing on every container start — no manual psql run required.
_OBSERVABILITY_DDL = """
CREATE SCHEMA IF NOT EXISTS observability;

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

def init_db() -> bool:
    """Initialize the thread-safe connection pool and create all tables."""
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
        # connect_timeout=8 keeps DNS failures fast (vs. OS default ~75 s).
        dsn = url if "connect_timeout" in url else url + ("&" if "?" in url else "?") + "connect_timeout=8"
        _pool = pg_pool.ThreadedConnectionPool(1, 10, dsn=dsn)
        conn = _pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(_DDL)
                cur.execute(_MIGRATIONS)  # reconcile column drift on existing tables
            conn.commit()
        finally:
            _pool.putconn(conn)
        logger.info("PostgreSQL database initialized (tables + migrations applied)")
    except Exception as exc:
        logger.error("Database init failed: %s", exc)
        _pool = None
        return False

    # Observability schema — telemetry + governance tables.  Optional: non-fatal if Postgres
    # lacks permissions for CREATE SCHEMA (e.g. restricted managed DB users).
    obs_conn = _pool.getconn()
    try:
        with obs_conn.cursor() as cur:
            cur.execute(_OBSERVABILITY_DDL)
        obs_conn.commit()
        logger.info("Observability schema ready (mcp_sessions, mcp_telemetry, adjudicated_tool_calls)")
    except Exception as exc:
        obs_conn.rollback()
        logger.warning("Observability schema init failed (non-fatal): %s", exc)
    finally:
        _pool.putconn(obs_conn)

    # pgvector extension + embeddings table — optional; logged as warning if absent.
    vec_conn = _pool.getconn()
    try:
        with vec_conn.cursor() as cur:
            cur.execute(_PGVECTOR_DDL_TEMPLATE.format(dim=EMBEDDING_DIM))
            cur.execute(_PGVECTOR_MIGRATIONS)
        vec_conn.commit()
        logger.info("pgvector extension ready (EMBEDDING_DIM=%d)", EMBEDDING_DIM)
    except Exception as exc:
        vec_conn.rollback()
        logger.warning("pgvector not available — embedding features disabled: %s", exc)
    finally:
        _pool.putconn(vec_conn)

    return True


def is_available() -> bool:
    """Return True when a live connection pool is configured."""
    return _pool is not None


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
    if _pool is None:
        raise RuntimeError("Database not initialized")
    conn = _pool.getconn()
    broken = False
    try:
        if _HAS_PGVECTOR:
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
        _pool.putconn(conn, close=broken)


get_conn = _conn   # public alias used by mcp_governance and github_endpoints


def ping() -> dict:
    """Check the database connection and report pgvector availability.

    Returns a dict with keys: connected, pgvector, pg_version, vector_version, error.
    Never raises — safe to call at startup or in a health-check endpoint.
    """
    if _pool is None:
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


def _run(fn, default=None):
    """Call fn(), return default on any failure. Never raises."""
    if _pool is None:
        return default
    try:
        return fn()
    except Exception as exc:
        logger.error("db: %s", exc)
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Company
# ─────────────────────────────────────────────────────────────────────────────

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
                execute_values(
                    cur,
                    "INSERT INTO sic_peers (company_id, peer_ticker, peer_cik, peer_name, peer_state, sic) VALUES %s ON CONFLICT DO NOTHING",
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
                    "SELECT id, company_name, sic, sic_description FROM companies WHERE ticker = %s",
                    (ticker.upper(),),
                )
                comp = cur.fetchone()
                if not comp:
                    return None
                company_id, company_name, sic, sic_description = comp
                cur.execute(
                    """
                    SELECT peer_ticker, peer_cik, peer_name, peer_state, sic
                    FROM sic_peers WHERE company_id = %s
                    """,
                    (company_id,),
                )
                peers = [
                    {"ticker": r[0], "cik": r[1], "name": r[2], "state": r[3], "sic": r[4]}
                    for r in cur.fetchall()
                ]
                if not peers:
                    return None
                return {
                    "ticker": ticker.upper(),
                    "company_name": company_name,
                    "sic": sic,
                    "sic_description": sic_description,
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


def save_edgar_8k_events(company_id: int, events: list) -> None:
    """Save annotated 8-K events."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                for ev in events:
                    filing_date = ev.get("date") or ev.get("filing_date")
                    if not filing_date:
                        continue
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
) -> None:
    """Save DEF 14A proxy governance sections."""
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
                    """,
                    (
                        company_id, filing_date, accession_number,
                        sections.get("executive_compensation") or sections.get("Executive Compensation"),
                        sections.get("board_of_directors") or sections.get("Board of Directors"),
                        sections.get("say_on_pay") or sections.get("Say on Pay"),
                        sections.get("shareholder_proposals") or sections.get("Shareholder Proposals"),
                    ),
                )
    _run(_do)


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

def create_risk_loop_run(company_id: Optional[int], config: dict) -> Optional[int]:
    """Create a risk_loop_runs record and return run_id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO risk_loop_runs
                        (company_id, ticker, period_begin, period_end_col, industry,
                         appetite_level, persona, data_mode, signal_set,
                         forecast_metric, forecast_horizon)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                    ),
                )
                return cur.fetchone()[0]
    return _run(_do)


def complete_risk_loop_run(run_id: int) -> None:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE risk_loop_runs SET completed = TRUE, completed_at = NOW() WHERE id = %s",
                    (run_id,),
                )
    _run(_do)


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


def save_risk_scores(run_id: int, risks: list) -> None:
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
            )
            for r in risks
        ]
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO risk_scores
                        (run_id, risk_ref, risk_name, category, base_score, delta, score,
                         rag_status, velocity, control_env, peer_benchmark)
                    VALUES %s
                    """,
                    rows,
                )
    _run(_do)


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


def save_forecasts(run_id: int, metric: str, forecast_data: dict) -> None:
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
    _run(_do)


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


def save_backtest_metrics(run_id: int, backtest_data: dict) -> None:
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
    _run(_do)


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


def save_rss_articles_full(company_id: Optional[int], articles_result: dict) -> None:
    """Save full RSS articles from rss_tool.py output (includes URLs, authors)."""
    def _do():
        feeds = articles_result.get("feeds", articles_result.get("feed_results", []))
        with _conn() as conn:
            with conn.cursor() as cur:
                for feed in feeds:
                    feed_name = feed.get("feed") or feed.get("name", "")
                    for art in feed.get("articles", feed.get("signals", [])):
                        title = (art.get("title") or "")[:500]
                        if not title:
                            continue
                        cur.execute(
                            """
                            INSERT INTO rss_articles
                                (company_id, feed_name, feed_url, industry_category,
                                 title, article_url, published_at, summary)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (title, feed_name) DO NOTHING
                            """,
                            (
                                company_id,
                                feed_name,
                                feed.get("url"),
                                feed.get("industry") or feed.get("category"),
                                title,
                                art.get("url") or art.get("link"),
                                art.get("published") or art.get("date"),
                                (art.get("summary") or "")[:2000] or None,
                            ),
                        )
    _run(_do)


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
    run_id: int,
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
                    RETURNING id, run_id, gate_type, item_ref, status
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
                           disposition, adjustments, rationale, prepared_by_name
                    FROM approval_tasks WHERE id = %s
                    """,
                    (task_id,),
                )
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
                return dict(zip(cols, row)) if row else None
    return _run(_do)


def get_approval_inbox(manager_id: int) -> list:
    """Items awaiting this user's review, newest-submitted first."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.id, t.run_id, t.gate_type, t.item_ref, t.item_label, t.disposition,
                           t.adjustments, t.rationale, t.prepared_by_name, t.prepared_at, r.ticker,
                           t.ai_suggested, t.ai_accepted
                    FROM approval_tasks t
                    JOIN risk_loop_runs r ON r.id = t.run_id
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


def save_cem_events(run_id: int, events: list) -> None:
    if not events:
        return
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
            )
            for e in events
        ]
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO cem_events
                        (run_id, control, area, risk_label, severity,
                         exposure, category, root_cause_narrative)
                    VALUES %s
                    """,
                    rows,
                )
    _run(_do)


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
) -> Optional[int]:
    """Persist a single AI/LLM output with provenance. Returns the row id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_analyses
                        (run_id, ticker, kind, subject_ref, model, effort,
                         content, summary, input_tokens, output_tokens, cost_usd)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        run_id, (ticker or None) and ticker.upper(), kind, subject_ref,
                        model, effort, Json(content), summary,
                        input_tokens, output_tokens, cost_usd,
                    ),
                )
                return cur.fetchone()[0]
    return _run(_do)


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
                return result
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
                         manual_coverage_level, updated_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id, process_id) DO UPDATE SET
                        geography              = EXCLUDED.geography,
                        segments               = EXCLUDED.segments,
                        notes                  = EXCLUDED.notes,
                        manual_coverage_level  = EXCLUDED.manual_coverage_level,
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
                    "updated_at FROM sox_process_details WHERE company_id = %s",
                    (company_id,),
                )
                return {
                    r[0]: {
                        "geography": r[1] or [], "segments": r[2] or [], "notes": r[3],
                        "manual_coverage_level": r[4],
                        "updated_at": r[5].isoformat() if r[5] else None,
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
) -> Optional[int]:
    """Store a vector embedding for any source row. Returns embedding id (or None).

    source_table : originating table name  (e.g. 'rss_articles', 'ai_analyses')
    source_id    : PK of the row in that table
    content_type : use an EMBT_* constant (e.g. EMBT_RISK_FACTOR, EMBT_ARTICLE)
    embedding    : list[float] from your embedding model — len must equal EMBEDDING_DIM
    chunk_index  : 0-based chunk position for long documents split before embedding
    company_id   : companies.id — enables fast per-company filtering in searches
    """
    if not embedding or not _HAS_PGVECTOR:
        return None
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO embeddings
                        (source_table, source_id, content_type, model,
                         chunk_index, company_id, embedding, text_snippet)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT ON CONSTRAINT uq_embeddings DO UPDATE
                        SET embedding    = EXCLUDED.embedding,
                            company_id   = COALESCE(EXCLUDED.company_id, embeddings.company_id),
                            text_snippet = EXCLUDED.text_snippet,
                            created_at   = NOW()
                    RETURNING id
                    """,
                    (source_table, source_id, content_type, model or "unknown",
                     chunk_index, company_id, embedding, text_snippet),
                )
                row = cur.fetchone()
                return row[0] if row else None
    return _run(_do)


_DISTANCE_OPS = {"cosine": "<=>", "l2": "<->", "ip": "<#>"}


def search_similar_embeddings(
    embedding: list,
    *,
    source_table: Optional[str] = None,
    content_type: Optional[str] = None,
    company_id: Optional[int] = None,
    limit: int = 10,
    metric: str = "cosine",
) -> list:
    """Return the top-k nearest embeddings via ANN (HNSW index).

    metric: 'cosine' (default) | 'l2' | 'ip'
    Returns list of dicts: id, source_table, source_id, content_type, model,
    chunk_index, text_snippet, distance, created_at.
    """
    if not embedding or not _HAS_PGVECTOR:
        return []
    op = _DISTANCE_OPS.get(metric, "<=>")
    def _do():
        clauses: list = []
        params: list = []
        if source_table:
            clauses.append("source_table = %s")
            params.append(source_table)
        if content_type:
            clauses.append("content_type = %s")
            params.append(content_type)
        if company_id:
            clauses.append("company_id = %s")
            params.append(company_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, source_table, source_id, content_type, model,
                           chunk_index, text_snippet,
                           embedding {op} %s AS distance, created_at
                    FROM embeddings
                    {where}
                    ORDER BY embedding {op} %s
                    LIMIT %s
                    """,
                    params + [embedding, embedding, limit],
                )
                return [
                    {
                        "id": r[0], "source_table": r[1], "source_id": r[2],
                        "content_type": r[3], "model": r[4],
                        "chunk_index": r[5], "text_snippet": r[6],
                        "distance": float(r[7]) if r[7] is not None else None,
                        "created_at": r[8].isoformat() if r[8] else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def save_embeddings_bulk(rows: list) -> int:
    """Batch-upsert embeddings in a single transaction. Preferred over calling
    save_embedding() in a loop when chunking long documents.

    Each item in rows must be a dict with:
        source_table, source_id, content_type, embedding
    Optional keys: model, text_snippet, chunk_index (default 0), company_id

    Returns the number of rows processed (0 when pgvector is unavailable).
    """
    if not rows or not _HAS_PGVECTOR:
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
    if not query_embedding or not _HAS_PGVECTOR:
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
    """Upsert per-risk state rows for a review session."""
    if not states:
        return
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                for s in states:
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
                            s.get("risk_ref"),
                            s.get("original_wording"),
                            s.get("current_wording"),
                            s.get("included", True),
                            s.get("reason_for_change"),
                            Json(s.get("controls_assigned") or []),
                        ),
                    )
    _run(_do)


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
                           rrs.current_wording
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
                           base_score, delta, velocity, control_env, peer_benchmark
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

def bulk_save_risk_domains(run_id: int, domain_map: dict) -> int:
    """Persist domain assignments (risk_ref → domain) to risk_scores.assigned_domain.

    domain_map: {risk_ref: domain_name, ...}
    Returns number of rows updated.
    """
    if not domain_map:
        return 0

    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                updated = 0
                for risk_ref, domain in domain_map.items():
                    cur.execute(
                        """
                        UPDATE risk_scores
                        SET assigned_domain = %s
                        WHERE run_id = %s AND risk_ref = %s
                        """,
                        (domain, run_id, risk_ref),
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
                    "SELECT control_ref, framework, name, description, category, domain, tags "
                    "FROM controls_library ORDER BY control_ref"
                )
                return [
                    {
                        "ref":         r[0],
                        "framework":   r[1] or "",
                        "name":        r[2],
                        "description": r[3] or "",
                        "category":    r[4] or "",
                        "domain":      r[5] or "",
                        "tags":        r[6] or [],
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


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
                        (control_ref, framework, name, description, category, domain)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (control_ref) DO UPDATE SET
                        framework   = COALESCE(EXCLUDED.framework,   controls_library.framework),
                        name        = EXCLUDED.name,
                        description = COALESCE(EXCLUDED.description, controls_library.description),
                        category    = COALESCE(EXCLUDED.category,    controls_library.category),
                        domain      = COALESCE(EXCLUDED.domain,      controls_library.domain)
                    """,
                    (
                        ref,
                        control.get("framework", "Custom"),
                        control.get("name", ""),
                        control.get("description") or control.get("desc", ""),
                        control.get("category", "Custom"),
                        control.get("domain", "Custom"),
                    ),
                )
        return True
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
    """Return CEM event templates, ordered by sort_order."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                q = (
                    "SELECT id, control, area, risk_label, severity, exposure, category, rc_narrative "
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

def save_pac_module(process: str, module_name: str, rego_content: str, version: str = "1.0") -> Optional[int]:
    """Insert a new versioned Rego module for a process. Returns the row id."""
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pac_policy_modules
                        (process, module_name, rego_content, version, last_revised_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    RETURNING id
                    """,
                    (process, module_name, rego_content, version),
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
                    SELECT id, process, module_name, rego_content, version, last_revised_at, created_at
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
                    "rego_content": row[3], "version": row[4],
                    "last_revised_at": row[5].isoformat() if row[5] else None,
                    "created_at": row[6].isoformat() if row[6] else None,
                    "approvals": approvals,
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
                        id, process, module_name, version, last_revised_at, created_at
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
                        "version": row[3],
                        "last_revised_at": row[4].isoformat() if row[4] else None,
                        "created_at": row[5].isoformat() if row[5] else None,
                        "approvals": approvals,
                    })
                return result
    return _run(_do) or []


def upsert_control(control_id: str, name: str, description: Optional[str] = None,
                    process: Optional[str] = None, source: str = "manual") -> bool:
    """Insert or update a control_catalog entry. Used both by the one-time
    startup seed and by cac_from_pac (self-registers newly generated CaC
    controls that reuse a real PaC control_id)."""
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
                    f"SELECT control_id, name, description, process, source, created_at "
                    f"FROM controls_catalog {where} ORDER BY control_id",
                    params,
                )
                return [
                    {
                        "control_id": r[0], "name": r[1], "description": r[2],
                        "process": r[3], "source": r[4],
                        "created_at": r[5].isoformat() if r[5] else None,
                    }
                    for r in cur.fetchall()
                ]
    return _run(_do) or []


def get_control(control_id: str) -> Optional[dict]:
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT control_id, name, description, process, source, created_at "
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
                }
    return _run(_do)


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
