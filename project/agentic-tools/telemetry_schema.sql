-- =============================================================================
-- Dendrai MCP Telemetry Schema
-- Observability layer for the stdio telemetry proxy.
-- Run once against the Railway PostgreSQL database.
--
-- Usage:
--   psql $DATABASE_URL -f telemetry_schema.sql
--
-- All objects live in the "observability" schema, completely isolated from
-- the 28-table application schema in the public schema.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS observability;

-- =============================================================================
-- mcp_sessions
-- One row per proxy process invocation (i.e. per Claude Desktop/Code session).
-- The session_id is a UUID generated at proxy startup.
-- =============================================================================
CREATE TABLE IF NOT EXISTS observability.mcp_sessions (
    session_id      UUID         PRIMARY KEY,
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    server_name     VARCHAR(128),
    process_id      INTEGER,
    proxy_version   VARCHAR(16)  NOT NULL DEFAULT '1.0.0'
);

COMMENT ON TABLE  observability.mcp_sessions IS
    'One row per MCP Telemetry Proxy process invocation.';
COMMENT ON COLUMN observability.mcp_sessions.session_id IS
    'UUID generated at proxy startup; ties all messages to a single client session.';
COMMENT ON COLUMN observability.mcp_sessions.server_name IS
    'FastMCP server label (e.g. "edgar", "fred", "rss").';

-- =============================================================================
-- mcp_telemetry
-- One row per JSON-RPC 2.0 message (both requests and responses).
-- Requests and responses are linked by (session_id, message_id).
-- =============================================================================
CREATE TABLE IF NOT EXISTS observability.mcp_telemetry (
    id                BIGSERIAL    PRIMARY KEY,

    -- When
    ts                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Who / which session
    session_id        UUID         NOT NULL
                          REFERENCES observability.mcp_sessions (session_id)
                          ON DELETE CASCADE,

    -- JSON-RPC identity
    message_id        TEXT,                        -- JSON-RPC "id" field (string or number cast to text)
    direction         VARCHAR(8)   NOT NULL
                          CHECK (direction IN ('request', 'response')),
    method            VARCHAR(128),                -- e.g. "tools/call", "tools/list", "initialize"

    -- Tool-level detail (populated when method = 'tools/call')
    target_tool       VARCHAR(128),                -- params.name
    tool_args_hash    CHAR(64),                    -- SHA-256 of params.arguments (for audit, not raw args)

    -- Latency (NULL on request rows; computed by proxy on response rows)
    execution_time_ms INTEGER,

    -- Outcome (NULL on request rows)
    status            VARCHAR(16)
                          CHECK (status IN ('ok', 'error', 'timeout', 'unknown')),
    error_message     TEXT,                        -- first 500 chars of error.message

    -- Audit integrity
    payload_hash      CHAR(64)     NOT NULL,       -- SHA-256 of the full raw JSON-RPC payload

    -- Context
    server_name       VARCHAR(128),                -- copy from session for fast filtering

    -- Risk-as-Code governance assertions (array of flag names)
    --   bypass_keyword  — payload contains CI bypass language
    --   large_payload   — raw JSON > 50 KB
    --   bulk_args       — more than 20 tool arguments
    --   sensitive_tool  — tool name in a high-risk allowlist
    risk_flags        TEXT[]
);

COMMENT ON TABLE  observability.mcp_telemetry IS
    'One row per JSON-RPC 2.0 message traversing the MCP telemetry proxy.';
COMMENT ON COLUMN observability.mcp_telemetry.message_id IS
    'JSON-RPC id field; join request/response pairs on (session_id, message_id).';
COMMENT ON COLUMN observability.mcp_telemetry.execution_time_ms IS
    'Round-trip latency in milliseconds; only set on response rows.';
COMMENT ON COLUMN observability.mcp_telemetry.payload_hash IS
    'SHA-256 of the raw JSON payload for tamper-evidence audit trail.';
COMMENT ON COLUMN observability.mcp_telemetry.risk_flags IS
    'Governance assertion flags fired by the proxy Risk-as-Code engine.';

-- =============================================================================
-- Indexes — tuned for the likely query patterns
-- =============================================================================

-- Session timeline (primary access pattern for dashboards)
CREATE INDEX IF NOT EXISTS idx_tel_session
    ON observability.mcp_telemetry (session_id, ts DESC);

-- Per-tool latency and volume queries
CREATE INDEX IF NOT EXISTS idx_tel_tool
    ON observability.mcp_telemetry (target_tool, ts DESC);

-- Method-level filtering (tools/call vs initialize vs tools/list)
CREATE INDEX IF NOT EXISTS idx_tel_method
    ON observability.mcp_telemetry (method, ts DESC);

-- Error / timeout alerting — partial index keeps it narrow
CREATE INDEX IF NOT EXISTS idx_tel_errors
    ON observability.mcp_telemetry (ts DESC, server_name)
    WHERE status IN ('error', 'timeout');

-- Global time-series (for dashboards, recent-activity panels)
CREATE INDEX IF NOT EXISTS idx_tel_ts
    ON observability.mcp_telemetry (ts DESC);

-- Risk flag queries (governance reports)
CREATE INDEX IF NOT EXISTS idx_tel_risk_flags
    ON observability.mcp_telemetry USING GIN (risk_flags)
    WHERE risk_flags IS NOT NULL;

-- =============================================================================
-- Materialized-view-friendly helper: request/response pairs with latency
-- =============================================================================
CREATE OR REPLACE VIEW observability.rpc_pairs AS
SELECT
    req.session_id,
    req.message_id,
    req.ts                                                      AS requested_at,
    resp.ts                                                     AS responded_at,
    req.method,
    req.target_tool,
    resp.status,
    resp.execution_time_ms,
    resp.error_message,
    req.server_name,
    resp.risk_flags,
    req.payload_hash                                            AS request_hash,
    resp.payload_hash                                           AS response_hash
FROM observability.mcp_telemetry req
JOIN observability.mcp_telemetry resp
     ON  resp.session_id = req.session_id
     AND resp.message_id  = req.message_id
     AND resp.direction   = 'response'
WHERE req.direction = 'request';

COMMENT ON VIEW observability.rpc_pairs IS
    'Joins request and response rows into matched RPC pairs with latency.';

-- =============================================================================
-- Governance view: per-tool latency percentiles and error rates
-- =============================================================================
CREATE OR REPLACE VIEW observability.tool_latency_summary AS
SELECT
    server_name,
    target_tool,
    COUNT(*)                                                            AS call_count,
    ROUND(AVG(execution_time_ms))                                      AS avg_ms,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY execution_time_ms)    AS p50_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY execution_time_ms)    AS p95_ms,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY execution_time_ms)    AS p99_ms,
    COUNT(*) FILTER (WHERE status = 'error')                           AS error_count,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE status = 'error')
               / NULLIF(COUNT(*), 0),
        2
    )                                                                   AS error_pct,
    MIN(ts)                                                             AS first_call_at,
    MAX(ts)                                                             AS last_call_at
FROM observability.mcp_telemetry
WHERE direction = 'response'
  AND target_tool IS NOT NULL
GROUP BY server_name, target_tool;

COMMENT ON VIEW observability.tool_latency_summary IS
    'P50/P95/P99 latency and error rates per tool — primary SLA monitoring view.';

-- =============================================================================
-- Governance view: all calls that fired at least one risk flag
-- =============================================================================
CREATE OR REPLACE VIEW observability.flagged_calls AS
SELECT
    t.ts,
    t.session_id,
    s.server_name,
    t.target_tool,
    t.method,
    t.direction,
    t.risk_flags,
    t.execution_time_ms,
    t.status,
    t.error_message,
    t.payload_hash
FROM observability.mcp_telemetry t
JOIN observability.mcp_sessions   s ON s.session_id = t.session_id
WHERE t.risk_flags IS NOT NULL
  AND array_length(t.risk_flags, 1) > 0
ORDER BY t.ts DESC;

COMMENT ON VIEW observability.flagged_calls IS
    'All messages where the Risk-as-Code engine fired at least one governance flag.';

-- =============================================================================
-- processed_at — stamped when the governance poller picks up the row and
-- feeds it through the UBO Bronze→Silver→Gold→Council pipeline.
-- NULL = unprocessed; NOT NULL = adjudicated (see adjudicated_tool_calls).
-- =============================================================================
ALTER TABLE observability.mcp_telemetry
    ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ;

-- Partial index the poller uses: only unprocessed flagged rows, ordered by ts
CREATE INDEX IF NOT EXISTS idx_tel_unprocessed
    ON observability.mcp_telemetry (ts ASC)
    WHERE risk_flags IS NOT NULL AND processed_at IS NULL;

-- =============================================================================
-- adjudicated_tool_calls — Gold-stage output for every MCP proxy event that
-- passed through the UBO Bronze→Silver→Gold→Council pipeline.
-- Linked back to the originating telemetry row via telemetry_id.
-- =============================================================================
CREATE TABLE IF NOT EXISTS observability.adjudicated_tool_calls (
    id                    BIGSERIAL    PRIMARY KEY,
    adjudicated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Link to source telemetry row
    telemetry_id          BIGINT       NOT NULL
                              REFERENCES observability.mcp_telemetry (id)
                              ON DELETE CASCADE,
    session_id            UUID         NOT NULL,

    -- Tool context (denormalised for fast dashboard queries)
    target_tool           VARCHAR(128),
    server_name           VARCHAR(128),
    risk_flags            TEXT[],
    execution_time_ms     INTEGER,

    -- Dendrai UBO Governance Brain output
    uro_id                VARCHAR(64)  NOT NULL,
    risk_score            NUMERIC(5,4),
    risk_tier             VARCHAR(16),
    final_verdict         VARCHAR(32),
    ensemble_confidence   NUMERIC(4,3),
    requires_human_review BOOLEAN      NOT NULL DEFAULT FALSE,
    conflict_flags        TEXT[],
    policy_violations     TEXT[],

    -- Reasoning (abbreviated — full reasoning lives in the URO)
    adjudicator_reasoning TEXT
);

CREATE INDEX IF NOT EXISTS idx_adj_session
    ON observability.adjudicated_tool_calls (session_id, adjudicated_at DESC);
CREATE INDEX IF NOT EXISTS idx_adj_tool
    ON observability.adjudicated_tool_calls (target_tool, adjudicated_at DESC);
CREATE INDEX IF NOT EXISTS idx_adj_tier
    ON observability.adjudicated_tool_calls (risk_tier, adjudicated_at DESC);
CREATE INDEX IF NOT EXISTS idx_adj_human_review
    ON observability.adjudicated_tool_calls (adjudicated_at DESC)
    WHERE requires_human_review = TRUE;

COMMENT ON TABLE observability.adjudicated_tool_calls IS
    'Dendrai UBO Governance Brain output for MCP proxy events — one row per adjudicated URO.';
COMMENT ON COLUMN observability.adjudicated_tool_calls.telemetry_id IS
    'FK to the originating mcp_telemetry row (which is stamped processed_at after insert).';
COMMENT ON COLUMN observability.adjudicated_tool_calls.uro_id IS
    'UUID of the URO that traversed Bronze→Silver→Gold→Council.';

-- Governance view: human review queue (all adjudicated calls awaiting action)
CREATE OR REPLACE VIEW observability.human_review_queue AS
SELECT
    a.adjudicated_at,
    a.session_id,
    a.target_tool,
    a.server_name,
    a.risk_flags,
    a.risk_score,
    a.risk_tier,
    a.final_verdict,
    a.ensemble_confidence,
    a.conflict_flags,
    a.policy_violations,
    a.adjudicator_reasoning,
    t.ts                    AS telemetry_ts,
    t.execution_time_ms,
    t.error_message
FROM observability.adjudicated_tool_calls a
JOIN observability.mcp_telemetry          t ON t.id = a.telemetry_id
WHERE a.requires_human_review = TRUE
ORDER BY a.risk_score DESC, a.adjudicated_at DESC;

COMMENT ON VIEW observability.human_review_queue IS
    'All adjudicated MCP calls flagged for human review, ordered by risk score.';

-- =============================================================================
-- Retention helper: delete rows older than N days
-- Call periodically from a pg_cron job or Railway cron task:
--   SELECT observability.purge_telemetry(90);
-- =============================================================================
CREATE OR REPLACE FUNCTION observability.purge_telemetry(
    retain_days INTEGER DEFAULT 90
)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    deleted BIGINT;
BEGIN
    DELETE FROM observability.mcp_telemetry
    WHERE ts < NOW() - (retain_days || ' days')::INTERVAL;
    GET DIAGNOSTICS deleted = ROW_COUNT;
    RETURN deleted;
END;
$$;

COMMENT ON FUNCTION observability.purge_telemetry IS
    'Delete telemetry rows older than retain_days (default 90). Returns deleted row count.';

-- =============================================================================
-- v2 additions — GitHub webhook support
--
-- adjudicated_tool_calls was originally designed for MCP telemetry events only.
-- GitHub webhook events don't have a corresponding mcp_telemetry row, so:
--   1. telemetry_id is made nullable (NULL for GitHub events)
--   2. source_system column distinguishes GITHUB from MCP_PROXY events
-- =============================================================================

ALTER TABLE observability.adjudicated_tool_calls
    ALTER COLUMN telemetry_id DROP NOT NULL;

ALTER TABLE observability.adjudicated_tool_calls
    ADD COLUMN IF NOT EXISTS source_system VARCHAR(32) NOT NULL DEFAULT 'MCP_PROXY';

CREATE INDEX IF NOT EXISTS idx_adj_source
    ON observability.adjudicated_tool_calls (source_system, adjudicated_at DESC);

COMMENT ON COLUMN observability.adjudicated_tool_calls.telemetry_id IS
    'FK to mcp_telemetry for MCP_PROXY events; NULL for GITHUB and other source systems.';
COMMENT ON COLUMN observability.adjudicated_tool_calls.source_system IS
    'Source system that produced the adjudicated event: MCP_PROXY or GITHUB.';

-- =============================================================================
-- v3 additions — Council agent votes
--
-- Stores the per-agent AgentEvaluation objects produced by the Council of Agents
-- (The Quant, The Linguist, The Graph Architect) so the dashboard can display
-- the full deliberation trace.  Empty array for auto-cleared LOW/MEDIUM events.
-- =============================================================================

ALTER TABLE observability.adjudicated_tool_calls
    ADD COLUMN IF NOT EXISTS council_votes JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN observability.adjudicated_tool_calls.council_votes IS
    'JSON array of per-agent AgentEvaluation objects from the Council deliberation. Empty for auto-cleared events below the Council tier threshold.';
