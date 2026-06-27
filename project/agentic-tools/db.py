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
    id                      SERIAL PRIMARY KEY,
    session_id              INT NOT NULL REFERENCES hitl_sessions(id),
    obj_id                  VARCHAR(16) NOT NULL,
    objective_text          TEXT,
    status                  VARCHAR(16) NOT NULL,
    adjusted_priority       VARCHAR(4),
    adjusted_sprint         INT,
    adjusted_hours          INT,
    adjusted_linked_risks   TEXT[],
    residual_risk_reduction NUMERIC,
    rationale               TEXT,
    adjusted_by             VARCHAR(64),
    adjusted_at             TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS objective_approval_signoffs (
    id          SERIAL PRIMARY KEY,
    approval_id INT        NOT NULL REFERENCES objective_approvals(id),
    role        VARCHAR(8) NOT NULL,
    signatory   VARCHAR(128),
    signed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_objectives (
    id                      SERIAL PRIMARY KEY,
    run_id                  INT         NOT NULL REFERENCES risk_loop_runs(id),
    obj_id                  VARCHAR(16) NOT NULL,
    objective_text          TEXT        NOT NULL,
    priority                VARCHAR(4),
    linked_risk_ref         VARCHAR(16),
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
    cost_usd           NUMERIC(12,8)
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
ALTER TABLE risk_scores ADD COLUMN IF NOT EXISTS source_framework VARCHAR(128);
ALTER TABLE risk_scores ADD COLUMN IF NOT EXISTS narrative         TEXT;
"""

# pgvector DDL — kept separate so a missing extension never breaks the core schema.
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
CREATE INDEX IF NOT EXISTS idx_embeddings_source  ON embeddings (source_table, source_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_company ON embeddings (company_id) WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw    ON embeddings USING hnsw (embedding vector_cosine_ops);
"""

# Column / constraint migrations for databases created before these columns existed.
_PGVECTOR_MIGRATIONS = """
ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS chunk_index SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS company_id  INT REFERENCES companies(id);
ALTER TABLE embeddings DROP CONSTRAINT IF EXISTS embeddings_source_table_source_id_content_type_model_key;
DO $$ BEGIN
    ALTER TABLE embeddings ADD CONSTRAINT uq_embeddings
        UNIQUE (source_table, source_id, content_type, model, chunk_index);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
CREATE INDEX IF NOT EXISTS idx_embeddings_company ON embeddings (company_id) WHERE company_id IS NOT NULL;
"""


# ─────────────────────────────────────────────────────────────────────────────
# Pool management
# ─────────────────────────────────────────────────────────────────────────────

def init_db() -> bool:
    """Initialize the thread-safe connection pool and create all tables."""
    global _pool
    if not _HAS_PSYCOPG2:
        logger.warning("psycopg2 not installed — database persistence disabled. "
                       "Run: pip install psycopg2-binary")
        return False
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        logger.info("DATABASE_URL not set — database persistence disabled")
        return False
    try:
        _pool = pg_pool.ThreadedConnectionPool(1, 10, dsn=url)
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
    """Borrow a connection, commit on success, rollback on error, always return."""
    if _pool is None:
        raise RuntimeError("Database not initialized")
    conn = _pool.getconn()
    try:
        if _HAS_PGVECTOR:
            _pg_register_vector(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


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
                rows.append((run_id, metric, model_name, horizon,
                             mf.get("point"), mf.get("ci_lower"), mf.get("ci_upper"), mf.get("sigma")))
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
                             adjusted_priority, adjusted_sprint, adjusted_hours,
                             adjusted_linked_risks, residual_risk_reduction,
                             rationale, adjusted_by, adjusted_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                CASE WHEN %s IS NOT NULL
                                     THEN to_timestamp(%s / 1000.0) ELSE NULL END)
                        RETURNING id
                        """,
                        (
                            session_id, obj_id, approval.get("objective_text"),
                            approval.get("status", "pending"),
                            adj.get("priority"), adj.get("sprint"), adj.get("hours"),
                            adj.get("linked_risks") or adj.get("linked_risk_ids"),
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


# ─────────────────────────────────────────────────────────────────────────────
# Audit plan
# ─────────────────────────────────────────────────────────────────────────────

def save_audit_objectives(run_id: int, objectives: list) -> None:
    if not objectives:
        return
    def _do():
        rows = [
            (
                run_id,
                o.get("id") or o.get("obj_id", ""),
                o.get("objective") or o.get("objective_text", ""),
                o.get("priority"),
                o.get("linked_risk") or o.get("linked_risk_ref"),
                o.get("controls") or [],
                o.get("hours"),
                o.get("sprint"),
                o.get("residualRiskReduction") or o.get("residual_risk_reduction"),
            )
            for o in objectives
        ]
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO audit_objectives
                        (run_id, obj_id, objective_text, priority, linked_risk_ref,
                         controls, hours, sprint, residual_risk_reduction)
                    VALUES %s
                    ON CONFLICT (run_id, obj_id) DO NOTHING
                    """,
                    rows,
                )
    _run(_do)


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
                         cache_read_tokens, cache_write_tokens, cost_usd)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        session_id, call.get("timestamp"), call.get("model"),
                        call.get("label"), call.get("input_tokens"), call.get("output_tokens"),
                        call.get("cache_read_tokens"), call.get("cache_write_tokens"),
                        call.get("cost_usd"),
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


def apply_review_wording(run_id: int, updates: list) -> None:
    """Persist reviewed risk wording back into risk_scores for a run.

    Writes current_wording into narrative (TEXT, uncapped).
    Also updates risk_name when the wording fits within 128 chars.
    """
    if not updates:
        return
    def _do():
        with _conn() as conn:
            with conn.cursor() as cur:
                for u in updates:
                    risk_ref = u.get("risk_ref") or u.get("id", "")
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
    _run(_do)


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
