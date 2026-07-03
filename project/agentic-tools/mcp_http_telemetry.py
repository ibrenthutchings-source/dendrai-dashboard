#!/usr/bin/env python3
"""
MCP HTTP Telemetry Middleware
=============================
Pure-ASGI middleware that intercepts MCP Streamable-HTTP tool calls arriving
at the api_server and writes telemetry to observability.mcp_telemetry.

Unlike the stdio proxy (which wraps Claude Desktop ↔ MCP subprocess traffic),
this middleware captures tool calls coming FROM any HTTP client — the Dendrai
dashboard, claude.ai integrations, or any other consumer of the mounted MCP
servers at /mcp/<name>/mcp.

GitHub webhook events are handled separately in github_endpoints.py, which
writes directly to observability.adjudicated_tool_calls (source_system=GITHUB)
and does not go through mcp_telemetry.

Wiring (api_server.py):
    import mcp_http_telemetry
    app.add_middleware(mcp_http_telemetry.MCPHttpTelemetryMiddleware)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any

import db

logger = logging.getLogger("ubo.http_telemetry")

# ── Per-process session UUID ────────────────────────────────────────────────────
# One mcp_sessions row is shared across all HTTP-originated telemetry rows
# produced by this server process.  Inserted lazily (ON CONFLICT DO NOTHING).

_HTTP_SESSION_ID: str = str(uuid.uuid4())
_SESSION_REGISTERED = False


# ── Path helpers ────────────────────────────────────────────────────────────────

# REST API prefixes served by api_server.py (nginx strips /api/mcp before forwarding)
_REST_API_PREFIXES = ("/edgar/", "/fred/", "/rss/", "/predictive/", "/risk-as-code/", "/oracle/")


def _is_monitored_path(path: str) -> bool:
    """True for MCP Streamable-HTTP endpoints AND REST API tool endpoints."""
    return path.startswith("/mcp/") or any(path.startswith(p) for p in _REST_API_PREFIXES)


def _server_name_from_path(path: str) -> str:
    """Extract the server label from an MCP or REST path.

    /mcp/edgar/mcp      → 'edgar'
    /edgar/8k-events    → 'edgar'
    """
    parts = [p for p in path.strip("/").split("/") if p]
    if path.startswith("/mcp/"):
        return parts[1] if len(parts) > 1 else "unknown"
    return parts[0] if parts else "unknown"


def _tool_name_from_rest_path(path: str) -> str | None:
    """Extract tool name from a REST path. /edgar/8k-events → '8k-events'"""
    parts = [p for p in path.strip("/").split("/") if p]
    return "/".join(parts[1:]) if len(parts) > 1 else None


# ── Risk-as-Code flag detection (mirrors mcp_telemetry_proxy.py) ────────────────

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
        ((p.get("params") or {}).get("name") or "")
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


# ── DB writes (synchronous psycopg2, called via asyncio.to_thread) ─────────────

def _ensure_session() -> None:
    """Insert the HTTP monitor session row if it doesn't exist yet."""
    global _SESSION_REGISTERED
    if _SESSION_REGISTERED or not db.is_available():
        return
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.mcp_sessions
                        (session_id, server_name, process_id)
                    VALUES (%s, 'http-monitor', %s)
                    ON CONFLICT (session_id) DO NOTHING
                    """,
                    (uuid.UUID(_HTTP_SESSION_ID), os.getpid()),
                )
        _SESSION_REGISTERED = True
    except Exception as exc:
        logger.debug("Session registration failed: %s", exc)


def _db_write_telemetry(
    *,
    server_name: str,
    message_id: str,
    tool_name: str | None,
    args_hash: str | None,
    elapsed_ms: int,
    status: str,
    payload_hash: str,
    risk_flags: list[str] | None,
) -> None:
    if not db.is_available():
        return
    _ensure_session()
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.mcp_telemetry
                        (session_id, message_id, direction, method,
                         target_tool, tool_args_hash,
                         execution_time_ms, status,
                         payload_hash, server_name, risk_flags)
                    VALUES (%s, %s, 'response', 'tools/call',
                            %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.UUID(_HTTP_SESSION_ID),
                        message_id,
                        tool_name,
                        args_hash,
                        elapsed_ms,
                        status,
                        payload_hash,
                        server_name,
                        risk_flags,
                    ),
                )
        logger.debug(
            "HTTP telemetry: server=%s tool=%s %dms %s flags=%s",
            server_name, tool_name, elapsed_ms, status, risk_flags,
        )
    except Exception as exc:
        logger.debug("HTTP telemetry write failed: %s", exc)


# ── Async log helper (called as a fire-and-forget task) ────────────────────────

async def _log_tool_call(
    body: bytes,
    server_name: str,
    elapsed_ms: int,
    http_status: int,
) -> None:
    try:
        payload = json.loads(body)
    except Exception:
        return

    # Only log actual tool invocations
    if payload.get("method") != "tools/call":
        return

    params    = payload.get("params") or {}
    tool_name = params.get("name") or params.get("tool")
    args      = params.get("arguments") or params.get("args")
    raw       = body.decode("utf-8", errors="replace")

    args_hash    = (
        hashlib.sha256(
            json.dumps(args, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if args else None
    )
    payload_hash = hashlib.sha256(raw.encode()).hexdigest()
    risk_flags   = _detect_risk_flags(payload)
    status       = "ok" if http_status < 400 else "error"
    message_id   = str(payload.get("id", ""))

    await asyncio.to_thread(
        _db_write_telemetry,
        server_name=server_name,
        message_id=message_id,
        tool_name=tool_name,
        args_hash=args_hash,
        elapsed_ms=elapsed_ms,
        status=status,
        payload_hash=payload_hash,
        risk_flags=risk_flags,
    )


async def _log_rest_call(
    body: bytes,
    server_name: str,
    tool_name: str | None,
    elapsed_ms: int,
    http_status: int,
) -> None:
    """Log a dashboard REST API call (plain JSON, not JSON-RPC) to mcp_telemetry."""
    raw = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    args_hash    = hashlib.sha256(raw.encode()).hexdigest() if raw.strip() else None
    payload_hash = hashlib.sha256(raw.encode()).hexdigest()
    risk_flags   = _detect_risk_flags({"params": {"name": tool_name or "", "arguments": payload}})
    status       = "ok" if http_status < 400 else "error"

    await asyncio.to_thread(
        _db_write_telemetry,
        server_name=server_name,
        message_id=str(uuid.uuid4()),
        tool_name=tool_name,
        args_hash=args_hash,
        elapsed_ms=elapsed_ms,
        status=status,
        payload_hash=payload_hash,
        risk_flags=risk_flags,
    )


# ── Pure-ASGI middleware ────────────────────────────────────────────────────────

class MCPHttpTelemetryMiddleware:
    """
    Intercepts POST requests to /mcp/* endpoints, buffers the request body,
    replays it to the downstream MCP app, and fires a background telemetry
    write task after the response status is known.

    Uses pure ASGI (not BaseHTTPMiddleware) to avoid the body-consumption
    problem that occurs when BaseHTTPMiddleware reads request.body() before
    the downstream handler can.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path   = scope.get("path", "")
        method = scope.get("method", "")

        if method != "POST" or not _is_monitored_path(path):
            await self.app(scope, receive, send)
            return

        # ── Buffer the full request body ───────────────────────────────────────
        # MCP Streamable-HTTP sends a single JSON-RPC message per request, so
        # the body is always one chunk (more_body=False). We still loop for
        # correctness with any chunked clients.

        body_chunks: list[bytes] = []
        buffered_messages: list[dict] = []

        while True:
            message = await receive()
            buffered_messages.append(message)
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        body = b"".join(body_chunks)

        # ── Replay receive for the downstream MCP app ──────────────────────────
        _replay_idx = [0]

        async def replay_receive() -> dict:
            if _replay_idx[0] < len(buffered_messages):
                msg = buffered_messages[_replay_idx[0]]
                _replay_idx[0] += 1
                return msg
            return {"type": "http.disconnect"}

        # ── Capture response status ────────────────────────────────────────────
        response_status = [200]

        async def capture_send(message: dict) -> None:
            if message["type"] == "http.response.start":
                response_status[0] = message.get("status", 200)
            await send(message)

        # ── Process request, then fire telemetry ───────────────────────────────
        start_ns = time.monotonic_ns()
        try:
            await self.app(scope, replay_receive, capture_send)
        finally:
            elapsed_ms  = (time.monotonic_ns() - start_ns) // 1_000_000
            server_name = _server_name_from_path(path)
            if path.startswith("/mcp/"):
                asyncio.create_task(
                    _log_tool_call(body, server_name, elapsed_ms, response_status[0])
                )
            else:
                asyncio.create_task(
                    _log_rest_call(
                        body, server_name,
                        _tool_name_from_rest_path(path),
                        elapsed_ms, response_status[0],
                    )
                )
