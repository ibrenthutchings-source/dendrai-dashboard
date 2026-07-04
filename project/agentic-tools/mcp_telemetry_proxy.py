#!/usr/bin/env python3
"""
MCP Telemetry Proxy
===================
Transparent stdio relay between an MCP client (Claude Desktop / Claude Code /
React) and a FastMCP subprocess. Captures every JSON-RPC 2.0 message, computes
per-call latency, and writes telemetry to PostgreSQL completely asynchronously.

The critical forwarding path (client ↔ FastMCP) NEVER waits for the database:
every DB write is fire-and-forget, silently dropped on timeout or pool error.

Architecture
------------
    CLIENT stdin/stdout (JSON-RPC 2.0)
         │
         ▼
    ┌─────────────────────────────┐
    │  MCP TELEMETRY PROXY        │
    │  relay_stdin ──────────────►│──► subprocess.stdin
    │  relay_stdout ◄────────────│◄── subprocess.stdout
    │     ├─ valid JSON → client  │
    │     └─ non-JSON  → stderr   │   (keeps client JSON parser clean)
    │  relay_stderr ◄────────────│◄── subprocess.stderr → our stderr
    │                             │
    │  asyncpg pool (min=1 max=3)─┤──► observability.mcp_telemetry
    │  (fire-and-forget, 2s cap)  │
    └─────────────────────────────┘
         │
         ▼
    FASTMCP SERVER (subprocess)

Usage
-----
    # Wrap any FastMCP server:
    python mcp_telemetry_proxy.py -- python edgar_mcp_server.py

    # Named server tag:
    python mcp_telemetry_proxy.py --name edgar -- python edgar_mcp_server.py

    # In claude_desktop_config.json or .claude/settings.json:
    {
      "mcpServers": {
        "edgar": {
          "command": "python",
          "args": [
            "/path/to/mcp_telemetry_proxy.py",
            "--name", "edgar",
            "--",
            "python", "/path/to/edgar_mcp_server.py"
          ]
        }
      }
    }

Environment variables
---------------------
    DATABASE_URL            postgresql://user:pass@host:5432/dbname
    PROXY_SERVER_NAME       tag written to every telemetry row
    PROXY_POOL_MIN          asyncpg pool minimum size   (default: 1)
    PROXY_POOL_MAX          asyncpg pool maximum size   (default: 3)
    PROXY_WRITE_TIMEOUT_S   seconds before a DB write is cancelled (default: 2.0)
    PROXY_LOG_LEVEL         DEBUG / INFO / WARNING (default: WARNING)
    PROXY_HOLD_TIMEOUT_S    seconds to wait for operator approval on a hold (default: 30)
    PROXY_HOLD_POLL_S       polling interval while waiting for hold resolution (default: 1.0)
    PROXY_BLOCKING_TOOLS    comma-separated tool names that trigger pre-execution holds
    PROXY_FREQ_WINDOW_S     rolling window in seconds for high-frequency detection (default: 60)
    PROXY_FREQ_THRESHOLD    call-count threshold within window before high_frequency fires (default: 10)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

# ── Logging (stderr only — stdout is reserved for JSON-RPC) ───────────────────

logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("PROXY_LOG_LEVEL", "WARNING").upper(),
    format="%(asctime)s [mcp-proxy] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mcp.proxy")

# ── Configuration ──────────────────────────────────────────────────────────────

SESSION_ID    = str(uuid.uuid4())
DATABASE_URL  = os.environ.get("DATABASE_URL", "")
POOL_MIN      = int(os.environ.get("PROXY_POOL_MIN", "1"))
POOL_MAX      = int(os.environ.get("PROXY_POOL_MAX", "3"))
WRITE_TIMEOUT = float(os.environ.get("PROXY_WRITE_TIMEOUT_S", "2.0"))

# Pre-execution hold (blocking gate) configuration
HOLD_TIMEOUT_S = float(os.environ.get("PROXY_HOLD_TIMEOUT_S", "30"))
HOLD_POLL_S    = float(os.environ.get("PROXY_HOLD_POLL_S", "1.0"))
_BLOCKING_TOOLS: frozenset[str] = frozenset(
    t.strip().lower()
    for t in os.environ.get(
        "PROXY_BLOCKING_TOOLS",
        "shell,execute,bash,run_command,drop,truncate,delete_file,exec_sql",
    ).split(",")
    if t.strip()
)

# Frequency anomaly detection
FREQ_WINDOW_S  = int(os.environ.get("PROXY_FREQ_WINDOW_S", "60"))
FREQ_THRESHOLD = int(os.environ.get("PROXY_FREQ_THRESHOLD", "10"))

# ── asyncpg pool (lazy-init, module-level singleton) ──────────────────────────

_pool: Any = None          # asyncpg.Pool | None
_pool_init_attempted = False


async def _get_pool() -> Any:
    """
    Return the asyncpg connection pool, creating it on first call.
    Returns None if DATABASE_URL is unset or the connection fails; in that
    case, all telemetry is silently dropped.
    """
    global _pool, _pool_init_attempted
    if _pool_init_attempted:
        return _pool
    _pool_init_attempted = True

    if not DATABASE_URL:
        logger.info("DATABASE_URL not set — telemetry disabled")
        return None

    try:
        import asyncpg  # type: ignore
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=POOL_MIN,
            max_size=POOL_MAX,
            command_timeout=5,
            # Close idle connections after 5 minutes — critical on Railway's
            # standard tier where idle connections count against the limit.
            max_inactive_connection_lifetime=300,
        )
        logger.warning(
            "Telemetry pool ready [min=%d max=%d session=%s]",
            POOL_MIN, POOL_MAX, SESSION_ID[:8],
        )
        return _pool
    except ImportError:
        logger.warning("asyncpg not installed — telemetry disabled (pip install asyncpg)")
        return None
    except Exception as exc:
        logger.warning("Telemetry pool failed: %s — continuing without telemetry", exc)
        return None


async def _close_pool() -> None:
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            pass
        _pool = None


# ── Session-level state for frequency and sequence detection ──────────────────
# One proxy process == one MCP session, so these are implicitly session-scoped
# without any DB overhead.

_session_call_times:   defaultdict[str, list[float]] = defaultdict(list)
_session_tool_history: list[str] = []

# ── Prompt-injection keyword detector ─────────────────────────────────────────

_INJECTION_KW: tuple[str, ...] = (
    "ignore previous instructions",
    "ignore all instructions",
    "you are now",
    "new system prompt",
    "disregard your",
    "forget everything",
    "act as if you",
    "jailbreak",
    "dan mode",
    "do anything now",
    "prompt injection",
    "override instructions",
    "ignore the above",
)

# ── PII / credential pattern detector ─────────────────────────────────────────

_SENSITIVE_RE = re.compile(
    r"(?:"
    r"\b\d{3}-\d{2}-\d{4}\b"                                 # SSN
    r"|\b(?:4\d{12}(?:\d{3})?|5[1-5]\d{14})\b"               # Visa / MC card
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"     # PEM private key
    r"|\bBearer\s+[A-Za-z0-9\-._~+/]{20,}={0,2}"             # Bearer token
    r"|(?:password|passwd|secret|pwd)\s*[:=]\s*[^\s,\"']{4,}" # password=value
    r"|(?:api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=\s][^\s,\"']{8,}"
    r")",
    re.IGNORECASE,
)

# ── Dangerous tool-call sequence patterns ──────────────────────────────────────

_ESCALATION_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("read_file",  "write_file",  "shell"),
    ("read_file",  "write_file",  "execute"),
    ("read_file",  "write_file",  "bash"),
    ("exec_sql",   "shell"),
    ("exec_sql",   "execute"),
    ("exec_sql",   "bash"),
    ("read_file",  "delete_file"),
    ("list_files", "delete_file"),
)

# ── Schema bootstrap (idempotent, runs once per process) ──────────────────────

_SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS observability;

CREATE TABLE IF NOT EXISTS observability.mcp_sessions (
    session_id    UUID         PRIMARY KEY,
    started_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    server_name   VARCHAR(128),
    process_id    INTEGER,
    proxy_version VARCHAR(16)  DEFAULT '1.0.0'
);

CREATE TABLE IF NOT EXISTS observability.mcp_telemetry (
    id                BIGSERIAL    PRIMARY KEY,
    ts                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    session_id        UUID         NOT NULL REFERENCES observability.mcp_sessions(session_id),
    message_id        TEXT,
    direction         VARCHAR(8)   NOT NULL CHECK (direction IN ('request','response')),
    method            VARCHAR(128),
    target_tool       VARCHAR(128),
    tool_args_hash    CHAR(64),
    execution_time_ms INTEGER,
    status            VARCHAR(16)  CHECK (status IN ('ok','error','timeout','unknown')),
    error_message     TEXT,
    payload_hash      CHAR(64)     NOT NULL,
    server_name       VARCHAR(128),
    risk_flags        TEXT[]
);

CREATE INDEX IF NOT EXISTS idx_tel_session  ON observability.mcp_telemetry (session_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tel_tool     ON observability.mcp_telemetry (target_tool, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tel_method   ON observability.mcp_telemetry (method, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tel_errors   ON observability.mcp_telemetry (status, ts DESC)
    WHERE status IN ('error', 'timeout');
CREATE INDEX IF NOT EXISTS idx_tel_ts       ON observability.mcp_telemetry (ts DESC);

-- Pre-execution governance holds (blocking gate)
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

-- Governance view: per-tool P50/P95 latency and error rate
CREATE OR REPLACE VIEW observability.tool_latency_summary AS
SELECT
    server_name,
    target_tool,
    COUNT(*)                                                    AS call_count,
    ROUND(AVG(execution_time_ms))                              AS avg_ms,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY execution_time_ms) AS p50_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY execution_time_ms) AS p95_ms,
    COUNT(*) FILTER (WHERE status = 'error')                   AS error_count,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE status = 'error') / NULLIF(COUNT(*), 0), 2
    )                                                           AS error_pct,
    MAX(ts)                                                     AS last_call_at
FROM observability.mcp_telemetry
WHERE direction = 'response' AND target_tool IS NOT NULL
GROUP BY server_name, target_tool;

-- Governance view: sessions with risk flags
CREATE OR REPLACE VIEW observability.flagged_calls AS
SELECT
    t.ts,
    t.session_id,
    t.target_tool,
    t.method,
    t.risk_flags,
    t.execution_time_ms,
    t.payload_hash,
    s.server_name
FROM observability.mcp_telemetry t
JOIN observability.mcp_sessions   s ON s.session_id = t.session_id
WHERE t.risk_flags IS NOT NULL AND array_length(t.risk_flags, 1) > 0
ORDER BY t.ts DESC;
"""

_schema_bootstrapped = False


async def _bootstrap_schema(pool: Any) -> None:
    global _schema_bootstrapped
    if _schema_bootstrapped:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(_SCHEMA_DDL)
        _schema_bootstrapped = True
        logger.info("Observability schema ready")
    except Exception as exc:
        logger.warning("Schema bootstrap failed: %s — telemetry may be partially disabled", exc)


async def _register_session(pool: Any, server_name: str) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO observability.mcp_sessions
                    (session_id, server_name, process_id)
                VALUES ($1, $2, $3)
                ON CONFLICT (session_id) DO NOTHING
                """,
                uuid.UUID(SESSION_ID),
                server_name,
                os.getpid(),
            )
    except Exception as exc:
        logger.debug("Session registration failed: %s", exc)


# ── Telemetry helpers ──────────────────────────────────────────────────────────

def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _extract_tool(payload: dict) -> str | None:
    """
    Pull the tool name from a tools/call request:
      {"method":"tools/call","params":{"name":"edgar_company_info",...}}
    """
    params = payload.get("params")
    if isinstance(params, dict):
        return params.get("name") or params.get("tool")
    return None


def _extract_tool_args_hash(payload: dict) -> str | None:
    """SHA-256 of the tool arguments dict for audit integrity."""
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    args = params.get("arguments") or params.get("args")
    if args:
        return _sha256(json.dumps(args, sort_keys=True, ensure_ascii=False))
    return None


def _infer_status(payload: dict) -> str:
    if "error" in payload:
        return "error"
    if "result" in payload:
        return "ok"
    return "unknown"


def _extract_error(payload: dict) -> str | None:
    err = payload.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message", ""))
        return msg[:500] if msg else None
    if isinstance(err, str):
        return err[:500]
    return None


# ── Lightweight Risk-as-Code assertions ───────────────────────────────────────
# These run on every forwarded payload and tag rows that match governance rules.
# Add domain-specific assertions here without touching FastMCP server code.

_RISK_CHECKS: dict[str, Any] = {
    "bypass_keyword": lambda p: any(
        kw in json.dumps(p).lower()
        for kw in ("skip-ci", "no-verify", "force-push", "bypass", "skip_ci")
    ),
    "large_payload": lambda p: len(json.dumps(p)) > 50_000,
    "bulk_args": lambda p: (
        isinstance((p.get("params") or {}).get("arguments"), dict)
        and len((p.get("params") or {}).get("arguments", {})) > 20
    ),
    "sensitive_tool": lambda p: (
        _extract_tool(p) or ""
    ).lower() in {
        "delete", "drop", "truncate", "exec_sql", "run_query",
        "write_file", "shell", "execute",
    },
    # Prompt injection: look for instructions-override keywords in tool arguments
    "prompt_injection": lambda p: any(
        kw in json.dumps(p.get("params") or {}).lower()
        for kw in _INJECTION_KW
    ),
    # Sensitive data: PII, credentials, or private keys in arguments
    "sensitive_data": lambda p: bool(
        _SENSITIVE_RE.search(json.dumps(p.get("params") or {}))
    ),
}


def _detect_risk_flags(payload: dict, direction: str = "unknown") -> list[str] | None:
    """
    Run all governance checks against a payload.
    Direction-sensitive checks (large_response, high_frequency, escalation_sequence)
    require the direction to be passed.
    """
    flags: list[str] = []

    raw_str = json.dumps(payload)

    # Static checks (direction-agnostic)
    for name, check in _RISK_CHECKS.items():
        try:
            if check(payload):
                flags.append(name)
        except Exception:
            pass

    # Large response — server-side exfiltration indicator
    if direction == "response" and len(raw_str) > 100_000:
        flags.append("large_response")

    # Frequency and sequence checks apply only to tool-call requests
    if direction == "request":
        tool = _extract_tool(payload)
        if tool:
            tool_lc = tool.lower()
            now = time.monotonic()

            # High-frequency: rolling count within FREQ_WINDOW_S
            times = _session_call_times[tool_lc]
            times[:] = [t for t in times if now - t < FREQ_WINDOW_S]
            times.append(now)
            if len(times) > FREQ_THRESHOLD:
                flags.append("high_frequency")

            # Escalation sequence: append to history and scan for known patterns
            _session_tool_history.append(tool_lc)
            if len(_session_tool_history) > 30:
                del _session_tool_history[0]

            for seq in _ESCALATION_SEQUENCES:
                seq_len = len(seq)
                if len(_session_tool_history) >= seq_len:
                    if _session_tool_history[-seq_len:] == list(seq):
                        flags.append("escalation_sequence")
                        break

    return flags or None


# ── Governance hold management ─────────────────────────────────────────────────

async def _insert_hold(
    pool: Any,
    tool_name: str,
    tool_args_hash: str | None,
    message_id: str,
) -> int | None:
    """Insert a PENDING pre-execution hold; return its ID."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO observability.tool_call_holds
                    (session_id, message_id, target_tool, tool_args_hash, status)
                VALUES ($1, $2, $3, $4, 'PENDING')
                RETURNING id
                """,
                uuid.UUID(SESSION_ID),
                message_id or None,
                tool_name,
                tool_args_hash,
            )
            return int(row["id"]) if row else None
    except Exception as exc:
        logger.warning("_insert_hold error: %s — hold skipped", exc)
        return None


async def _poll_hold_status(pool: Any, hold_id: int) -> str:
    """
    Poll the hold row until its status changes from PENDING or HOLD_TIMEOUT_S
    elapses.  Returns the final status string ('APPROVED', 'DENIED', 'TIMEOUT').
    On timeout the row is stamped EXPIRED.
    """
    loop     = asyncio.get_running_loop()
    deadline = loop.time() + HOLD_TIMEOUT_S
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE observability.tool_call_holds
                        SET status = 'EXPIRED', resolved_at = NOW()
                        WHERE id = $1 AND status = 'PENDING'
                        """,
                        hold_id,
                    )
            except Exception:
                pass
            return "TIMEOUT"
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT status FROM observability.tool_call_holds WHERE id = $1",
                    hold_id,
                )
                if row and row["status"] != "PENDING":
                    return row["status"]
        except Exception as exc:
            logger.warning("_poll_hold_status error: %s", exc)
            return "TIMEOUT"
        await asyncio.sleep(min(HOLD_POLL_S, remaining))


# ── Async DB write — fire-and-forget, silent-fail ─────────────────────────────

async def _db_write(
    *,
    direction: str,
    payload: dict,
    server_name: str,
    execution_time_ms: int | None = None,
) -> None:
    """
    Write one telemetry row.  Never raises — all errors are caught and the
    packet is silently dropped.  The caller uses create_task() so the
    forwarding path does not await this coroutine.
    """
    pool = await _get_pool()
    if pool is None:
        return

    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    status = _infer_status(payload) if direction == "response" else None
    error  = _extract_error(payload) if direction == "response" else None

    try:
        await asyncio.wait_for(
            _write_row(
                pool=pool,
                direction=direction,
                payload=payload,
                server_name=server_name,
                raw=raw,
                status=status,
                error=error,
                execution_time_ms=execution_time_ms,
            ),
            timeout=WRITE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.debug("Telemetry write timed out after %.1fs — packet dropped", WRITE_TIMEOUT)
    except Exception as exc:
        logger.debug("Telemetry write error: %s — packet dropped", exc)


async def _write_row(
    *,
    pool: Any,
    direction: str,
    payload: dict,
    server_name: str,
    raw: str,
    status: str | None,
    error: str | None,
    execution_time_ms: int | None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO observability.mcp_telemetry
                (session_id, message_id, direction, method, target_tool,
                 tool_args_hash, execution_time_ms, status, error_message,
                 payload_hash, server_name, risk_flags)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """,
            uuid.UUID(SESSION_ID),
            str(payload.get("id", "")),
            direction,
            payload.get("method"),
            _extract_tool(payload),
            _extract_tool_args_hash(payload),
            execution_time_ms,
            status,
            error,
            _sha256(raw),
            server_name,
            _detect_risk_flags(payload, direction),
        )


# ── Core proxy streams ─────────────────────────────────────────────────────────

async def run_proxy(server_argv: list[str], server_name: str) -> None:
    """
    Spawn the FastMCP server as a subprocess and relay stdio bidirectionally,
    capturing telemetry on every JSON-RPC message.
    """
    # pending[message_id] = monotonic arrival time — used to compute latency
    pending: dict[str, float] = {}

    proc = await asyncio.create_subprocess_exec(
        *server_argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    logger.warning(
        "Spawned %s (pid=%d) session=%s",
        " ".join(server_argv[:2]),
        proc.pid,
        SESSION_ID[:8],
    )

    # Bootstrap schema and register this session — non-blocking
    pool = await _get_pool()
    if pool is not None:
        asyncio.create_task(_bootstrap_schema(pool))
        asyncio.create_task(_register_session(pool, server_name))

    loop = asyncio.get_running_loop()

    # ── stdin relay: client → proxy → subprocess ───────────────────────────────
    async def relay_stdin() -> None:
        """
        Read from our stdin using asyncio.to_thread() so the event loop is
        never blocked during a slow read.  For non-blocking tool calls, forwards
        each line to the subprocess immediately.  For blocking-tier tools
        (shell, execute, drop, truncate, …) waits for operator approval before
        forwarding — DENIED calls send a JSON-RPC error to the client instead.
        """
        while True:
            line: bytes = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not line:
                break

            # ── Parse first — needed for hold checks and telemetry ─────────────
            payload:   dict | None = None
            tool_name: str  | None = None
            msg_id = ""
            try:
                payload   = json.loads(line)
                msg_id    = str(payload.get("id", ""))
                tool_name = (
                    _extract_tool(payload)
                    if payload.get("method") == "tools/call"
                    else None
                )
            except (json.JSONDecodeError, Exception):
                pass

            # ── Pre-execution hold for blocking-tier tools ─────────────────────
            if tool_name is not None and tool_name.lower() in _BLOCKING_TOOLS:
                hold_pool = await _get_pool()
                if hold_pool is not None:
                    args_hash = _extract_tool_args_hash(payload) if payload else None
                    hold_id   = await _insert_hold(hold_pool, tool_name, args_hash, msg_id)
                    if hold_id is not None:
                        logger.warning(
                            "GOV HOLD — tool=%s hold_id=%d timeout=%.0fs session=%s",
                            tool_name, hold_id, HOLD_TIMEOUT_S, SESSION_ID[:8],
                        )
                        status = await _poll_hold_status(hold_pool, hold_id)
                        if status == "DENIED":
                            logger.warning(
                                "GOV DENIED — tool=%s hold_id=%d", tool_name, hold_id
                            )
                            err_resp = (
                                json.dumps({
                                    "jsonrpc": "2.0",
                                    "id": payload.get("id") if payload else None,
                                    "error": {
                                        "code": -32600,
                                        "message": f"UBO governance gate DENIED: {tool_name}",
                                    },
                                }) + "\n"
                            ).encode("utf-8")
                            sys.stdout.buffer.write(err_resp)
                            sys.stdout.buffer.flush()
                            if payload is not None:
                                asyncio.create_task(
                                    _db_write(
                                        direction="request",
                                        payload=payload,
                                        server_name=server_name,
                                    )
                                )
                            continue
                        if status == "TIMEOUT":
                            logger.warning(
                                "GOV TIMEOUT — tool=%s hold_id=%d forwarding without approval",
                                tool_name, hold_id,
                            )
                        # APPROVED or TIMEOUT: fall through to forward below

            # ── Forward to subprocess ──────────────────────────────────────────
            proc.stdin.write(line)
            await proc.stdin.drain()

            # ── Telemetry (fire-and-forget) ────────────────────────────────────
            if payload is not None:
                if msg_id:
                    pending[msg_id] = loop.time()
                asyncio.create_task(
                    _db_write(
                        direction="request",
                        payload=payload,
                        server_name=server_name,
                    )
                )

        try:
            proc.stdin.close()
        except Exception:
            pass

    # ── stdout relay: subprocess → proxy → client ──────────────────────────────
    async def relay_stdout() -> None:
        """
        Read subprocess stdout line by line.
        - Valid JSON-RPC → write to our stdout immediately, then telemetry task.
        - Non-JSON (print statements, tracebacks) → redirect to stderr.
          This is the key guard that prevents log contamination from crashing
          the MCP client's JSON parser.
        """
        async for line_bytes in proc.stdout:
            line_str = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")

            try:
                payload = json.loads(line_str)
            except json.JSONDecodeError:
                # Non-JSON output from the FastMCP server (print, traceback, etc.)
                # Route to stderr so it's visible in logs but never reaches the client.
                print(f"[proxy:server] {line_str}", file=sys.stderr, flush=True)
                continue

            # ── Forward valid JSON-RPC to client immediately ───────────────────
            out = (line_str + "\n").encode("utf-8")
            sys.stdout.buffer.write(out)
            sys.stdout.buffer.flush()

            # ── Compute latency using pending request registry ─────────────────
            msg_id = str(payload.get("id", ""))
            elapsed_ms: int | None = None
            if msg_id and msg_id in pending:
                elapsed_ms = int((loop.time() - pending.pop(msg_id)) * 1000)

            asyncio.create_task(
                _db_write(
                    direction="response",
                    payload=payload,
                    server_name=server_name,
                    execution_time_ms=elapsed_ms,
                )
            )

    # ── stderr relay: subprocess stderr → our stderr ───────────────────────────
    async def relay_stderr() -> None:
        """Forward the FastMCP server's stderr verbatim to our stderr."""
        async for line_bytes in proc.stderr:
            sys.stderr.buffer.write(line_bytes)
            sys.stderr.buffer.flush()

    # ── Run all three relays concurrently ──────────────────────────────────────
    try:
        await asyncio.gather(relay_stdin(), relay_stdout(), relay_stderr())
    finally:
        # Give in-flight telemetry tasks a brief window to flush before teardown
        active = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if active:
            logger.info("Flushing %d pending telemetry tasks…", len(active))
            await asyncio.gather(*active, return_exceptions=True)

        await proc.wait()
        await _close_pool()

        logger.warning(
            "Proxy exited (session=%s returncode=%s)",
            SESSION_ID[:8],
            proc.returncode,
        )


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Parse CLI args and launch the proxy.

    Supported forms:
        mcp_telemetry_proxy.py -- python server.py [server-args...]
        mcp_telemetry_proxy.py --name edgar -- python server.py [server-args...]
    """
    args = sys.argv[1:]

    if not args:
        print(
            "Usage: mcp_telemetry_proxy.py [--name SERVER_NAME] -- <command> [args...]",
            file=sys.stderr,
        )
        sys.exit(1)

    # --name flag (optional; overrides PROXY_SERVER_NAME env var)
    server_name = os.environ.get("PROXY_SERVER_NAME", "")
    if "--name" in args:
        idx = args.index("--name")
        if idx + 1 >= len(args):
            print("--name requires a value", file=sys.stderr)
            sys.exit(1)
        server_name = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    # Everything after "--" is the server command
    if "--" in args:
        server_argv = args[args.index("--") + 1:]
    else:
        server_argv = args

    if not server_argv:
        print("No server command given after --", file=sys.stderr)
        sys.exit(1)

    if not server_name:
        server_name = os.path.splitext(os.path.basename(server_argv[-1]))[0]

    asyncio.run(run_proxy(server_argv, server_name))


if __name__ == "__main__":
    main()
