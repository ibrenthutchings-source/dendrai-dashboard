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
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import uuid
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
}


def _detect_risk_flags(payload: dict) -> list[str] | None:
    flags = []
    for name, check in _RISK_CHECKS.items():
        try:
            if check(payload):
                flags.append(name)
        except Exception:
            pass
    return flags or None


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
            _detect_risk_flags(payload),
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
        never blocked during a slow read.  Forwards each line to the subprocess
        immediately, then fires a telemetry task.
        """
        while True:
            # run_in_executor keeps the event loop free while readline() blocks
            line: bytes = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not line:
                break  # EOF — client closed the connection

            # ── Forward FIRST, parse second ────────────────────────────────────
            proc.stdin.write(line)
            await proc.stdin.drain()

            # ── Telemetry (best-effort, never blocks forwarding) ───────────────
            try:
                payload = json.loads(line)
                msg_id = str(payload.get("id", ""))
                if msg_id:
                    pending[msg_id] = loop.time()
                asyncio.create_task(
                    _db_write(
                        direction="request",
                        payload=payload,
                        server_name=server_name,
                    )
                )
            except (json.JSONDecodeError, Exception):
                pass  # malformed client input: still forwarded, never log to stdout

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
