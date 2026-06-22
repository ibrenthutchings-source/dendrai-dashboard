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
from datetime import date
from typing import Optional

try:
    import psycopg2
    from psycopg2 import pool as pg_pool
    from psycopg2.extras import Json, execute_values
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

logger = logging.getLogger(__name__)
_pool: Optional["pg_pool.ThreadedConnectionPool"] = None

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
        return True
    except Exception as exc:
        logger.error("Database init failed: %s", exc)
        _pool = None
        return False


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
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


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

def upsert_xbrl_series(company_id: int, metric_name: str, xbrl_tag: str = None, unit: str = "USD") -> Optional[int]:
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
    edgar_url: str = None,
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

def _upsert_fred_series_inline(cur, series_id: str, name: str, category: str = None, units: str = None) -> Optional[int]:
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


def save_fred_correlations(company_id: int, correlations: list, run_date: str = None) -> None:
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

def _ensure_hitl_session(conn, run_id: int, persona: str = None) -> int:
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


def save_risk_approvals(run_id: int, approvals: dict, persona: str = None) -> None:
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


def save_objective_approvals(run_id: int, approvals: dict, persona: str = None) -> None:
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
# Backward-compat stubs (deprecated)
# ─────────────────────────────────────────────────────────────────────────────

def save_result(tool_name: str, ticker: str, company_name: str, data: dict) -> Optional[int]:
    """Deprecated — use typed save functions."""
    logger.warning("db.save_result() is deprecated; use typed save functions (tool: %s)", tool_name)
    return None


def get_results(tool_name: str, ticker: str = None, limit: int = 20) -> list:
    """Deprecated stub."""
    return []


def list_tickers(tool_name: str) -> list:
    """Deprecated stub."""
    return []
