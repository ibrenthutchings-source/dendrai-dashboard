#!/usr/bin/env python3
"""
MCP Governance — bridges the Telemetry Proxy to the UBO Governance Brain.

Polls observability.mcp_telemetry every POLL_INTERVAL_S seconds for rows that
the proxy flagged (risk_flags IS NOT NULL) and have not yet been processed
(processed_at IS NULL).  Each row is fed through the full UBO medallion pipeline:

    Bronze  → raw telemetry row → URO (source_system=MCP_PROXY)
    Silver  → ConformedPayload + Policy-as-Code violations
    Gold    → risk_score (0.0–1.0), risk_tier (LOW/MEDIUM/HIGH/CRITICAL)
    Council → The Quant + The Linguist + The Graph Architect → The Adjudicator

The adjudication result is written to observability.adjudicated_tool_calls and
the source telemetry row is stamped with processed_at = NOW().

All DB calls use asyncio.to_thread() so the asyncio event loop is never blocked
by psycopg2's synchronous driver.  The polling loop is silent-fail: errors are
logged but never crash the api_server process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger("ubo.governance")

# ── UBO pipeline import (optional — degrades to no-op if package not on path) ─

_REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_HAS_UBO = False
try:
    from UBO.pipeline.bronze import BronzeIngestionLayer
    from UBO.pipeline.silver import SilverConformationLayer
    from UBO.pipeline.gold import GoldAggregationLayer
    from UBO.council.orchestrator import CouncilOrchestrator
    from UBO.models.uro import SourceSystem as UBOSourceSystem, URO
    _HAS_UBO = True
    logger.info("UBO Governance Brain loaded successfully")
except ImportError as exc:
    logger.warning("UBO not importable — MCP governance adjudication disabled: %s", exc)

import db  # project/agentic-tools/db.py — psycopg2 thread pool

# ── Configuration ──────────────────────────────────────────────────────────────

POLL_INTERVAL_S = float(os.environ.get("MCP_GOV_POLL_INTERVAL_S", "30"))
BATCH_SIZE      = int(os.environ.get("MCP_GOV_BATCH_SIZE", "20"))
# Only run the full Council for HIGH and above; MEDIUM/LOW get auto-cleared
COUNCIL_TIERS   = {"CRITICAL", "HIGH", "MEDIUM"}

# ── Lazy UBO pipeline instances (one set shared across all poll cycles) ────────

_bronze:    Any = None
_silver:    Any = None
_gold:      Any = None
_council:   Any = None


def _get_pipeline():
    global _bronze, _silver, _gold, _council
    if not _HAS_UBO:
        return None, None, None, None
    if _bronze is None:
        _bronze  = BronzeIngestionLayer()
        _silver  = SilverConformationLayer()
        _gold    = GoldAggregationLayer()
        _council = CouncilOrchestrator(only_for_tiers=COUNCIL_TIERS)
    return _bronze, _silver, _gold, _council


# ── Database helpers (synchronous psycopg2, called via asyncio.to_thread) ─────

def _fetch_unprocessed(batch_size: int) -> list[dict]:
    """Fetch up to batch_size unprocessed flagged telemetry rows."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, ts, session_id, message_id, direction, method,
                           target_tool, tool_args_hash, execution_time_ms,
                           status, error_message, payload_hash,
                           server_name, risk_flags
                    FROM observability.mcp_telemetry
                    WHERE risk_flags IS NOT NULL
                      AND processed_at IS NULL
                    ORDER BY ts ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (batch_size,),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    # Normalise types for JSON / URO compatibility
                    if d.get("session_id") is not None:
                        d["session_id"] = str(d["session_id"])
                    if d.get("ts") is not None:
                        d["ts"] = d["ts"].isoformat() if hasattr(d["ts"], "isoformat") else str(d["ts"])
                    if d.get("risk_flags") is None:
                        d["risk_flags"] = []
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_unprocessed error: %s", exc)
        return []


def _write_adjudication(
    telemetry_id: int,
    session_id: str,
    uro: "URO",
) -> None:
    """Write adjudication result and stamp processed_at on the source row."""
    if not db.is_available():
        return
    adj = uro.adjudication
    council_votes = json.dumps([
        {
            "agent_name":    e.agent_name,
            "verdict":       e.verdict.value,
            "confidence":    float(e.confidence),
            "risk_delta":    float(e.risk_delta),
            "reasoning":     e.reasoning,
            "evidence":      dict(e.evidence),
            "evaluation_ms": e.evaluation_ms,
        }
        for e in (adj.evaluations if adj else [])
    ])
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Insert adjudication
                cur.execute(
                    """
                    INSERT INTO observability.adjudicated_tool_calls (
                        telemetry_id, session_id,
                        target_tool, server_name, risk_flags, execution_time_ms,
                        uro_id, risk_score, risk_tier,
                        final_verdict, ensemble_confidence,
                        requires_human_review, conflict_flags,
                        policy_violations, adjudicator_reasoning,
                        council_votes
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb
                    )
                    """,
                    (
                        telemetry_id,
                        session_id,
                        uro.conformed_payload.resource_id if uro.conformed_payload else None,
                        uro.environment.tags.get("server_name"),
                        list(uro.raw_payload.content.get("risk_flags") or []),
                        uro.raw_payload.content.get("execution_time_ms"),
                        uro.id,
                        float(uro.risk_score) if uro.risk_score is not None else None,
                        uro.risk_tier,
                        adj.final_verdict.value if adj else None,
                        float(adj.ensemble_confidence) if adj else None,
                        adj.requires_human_review if adj else False,
                        [f.value for f in adj.conflict_flags] if adj else [],
                        list(uro.silver_policy_violations),
                        (adj.conflict_reasoning[:1000] if adj and adj.conflict_reasoning else None),
                        council_votes,
                    ),
                )
                # Stamp source row as processed
                cur.execute(
                    "UPDATE observability.mcp_telemetry SET processed_at = NOW() WHERE id = %s",
                    (telemetry_id,),
                )
            conn.commit()
    except Exception as exc:
        logger.warning("_write_adjudication error (telemetry_id=%s): %s", telemetry_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass


def _fetch_summary_rows() -> list[dict]:
    """Read observability.tool_latency_summary for the REST endpoint."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT server_name, target_tool, call_count,
                           avg_ms, p50_ms, p95_ms, p99_ms,
                           error_count, error_pct, last_call_at
                    FROM observability.tool_latency_summary
                    ORDER BY call_count DESC
                    LIMIT 100
                    """
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:
        logger.warning("_fetch_summary_rows error: %s", exc)
        return []


def _fetch_flagged_rows(limit: int) -> list[dict]:
    """Read observability.flagged_calls for the REST endpoint."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ts, session_id, server_name, target_tool, method,
                           direction, risk_flags, execution_time_ms, status,
                           error_message, payload_hash
                    FROM observability.flagged_calls
                    LIMIT %s
                    """,
                    (limit,),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("session_id"):
                        d["session_id"] = str(d["session_id"])
                    if d.get("ts"):
                        d["ts"] = d["ts"].isoformat()
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_flagged_rows error: %s", exc)
        return []


def _fetch_adjudicated_rows(limit: int, tier: str | None) -> list[dict]:
    """Read observability.adjudicated_tool_calls for the REST endpoint."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                if tier:
                    cur.execute(
                        """
                        SELECT adjudicated_at, session_id, target_tool, server_name,
                               risk_flags, risk_score, risk_tier, final_verdict,
                               ensemble_confidence, requires_human_review,
                               conflict_flags, policy_violations, adjudicator_reasoning,
                               source_system, council_votes
                        FROM observability.adjudicated_tool_calls
                        WHERE risk_tier = %s
                        ORDER BY adjudicated_at DESC
                        LIMIT %s
                        """,
                        (tier.upper(), limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT adjudicated_at, session_id, target_tool, server_name,
                               risk_flags, risk_score, risk_tier, final_verdict,
                               ensemble_confidence, requires_human_review,
                               conflict_flags, policy_violations, adjudicator_reasoning,
                               source_system, council_votes
                        FROM observability.adjudicated_tool_calls
                        ORDER BY adjudicated_at DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("session_id"):
                        d["session_id"] = str(d["session_id"])
                    if d.get("adjudicated_at"):
                        d["adjudicated_at"] = d["adjudicated_at"].isoformat()
                    if d.get("risk_score") is not None:
                        d["risk_score"] = float(d["risk_score"])
                    if d.get("ensemble_confidence") is not None:
                        d["ensemble_confidence"] = float(d["ensemble_confidence"])
                    if d.get("council_votes") is None:
                        d["council_votes"] = []
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_adjudicated_rows error: %s", exc)
        return []


# ── Core processing logic ──────────────────────────────────────────────────────

async def _process_one(row: dict) -> bool:
    """
    Run one telemetry row through the full UBO pipeline and persist the result.
    Returns True on success, False if any stage fails.
    """
    bronze, silver, gold, council = _get_pipeline()
    if bronze is None:
        return False

    telemetry_id = row["id"]
    session_id   = row.get("session_id", "UNKNOWN")

    try:
        # Bronze: map raw telemetry dict → URO
        uro = await bronze.ingest(row, UBOSourceSystem.MCP_PROXY)

        # Silver: conform + Policy-as-Code
        uro = await silver.conform(uro)

        # Gold: risk score + tier
        uro = await gold.score(uro)

        # Council: Quant + Linguist + Graph Architect → Adjudicator
        uro = await council.evaluate(uro)

        # Persist adjudication (blocking psycopg2 call, run in thread)
        await asyncio.to_thread(_write_adjudication, telemetry_id, session_id, uro)

        logger.info(
            "Adjudicated telemetry %d: tool=%s tier=%s verdict=%s human_review=%s",
            telemetry_id,
            uro.conformed_payload.resource_id if uro.conformed_payload else "?",
            uro.risk_tier,
            uro.adjudication.final_verdict.value if uro.adjudication else "?",
            uro.adjudication.requires_human_review if uro.adjudication else "?",
        )
        return True

    except Exception as exc:
        logger.warning("_process_one failed for telemetry_id=%d: %s", telemetry_id, exc)
        return False


async def _process_batch() -> int:
    """Fetch one batch of unprocessed flagged rows and process them all."""
    rows = await asyncio.to_thread(_fetch_unprocessed, BATCH_SIZE)
    if not rows:
        return 0

    results = await asyncio.gather(
        *[_process_one(row) for row in rows],
        return_exceptions=True,
    )
    return sum(1 for r in results if r is True)


# ── Background polling loop ────────────────────────────────────────────────────

async def start_polling() -> None:
    """
    Infinite polling loop.  Started as an asyncio task in api_server.py's
    lifespan; cancelled gracefully on shutdown.

    Sleeps POLL_INTERVAL_S between cycles.  All errors are caught and logged
    — the loop never exits on its own unless cancelled.
    """
    logger.info(
        "MCP governance polling started (interval=%.0fs batch=%d ubo=%s)",
        POLL_INTERVAL_S,
        BATCH_SIZE,
        _HAS_UBO,
    )
    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL_S)
            n = await _process_batch()
            if n > 0:
                logger.info("MCP governance: adjudicated %d telemetry row(s)", n)
        except asyncio.CancelledError:
            logger.info("MCP governance polling stopped")
            break
        except Exception as exc:
            logger.warning("MCP governance poll cycle error: %s", exc)


# ── FastAPI router ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/observability", tags=["Observability"])


@router.get("/telemetry/summary")
async def telemetry_summary():
    """P50/P95/P99 latency and error rates per MCP tool."""
    rows = await asyncio.to_thread(_fetch_summary_rows)
    return {"rows": rows, "count": len(rows)}


@router.get("/telemetry/flagged")
async def telemetry_flagged(limit: int = 50):
    """Most recent MCP tool calls that fired at least one governance flag."""
    rows = await asyncio.to_thread(_fetch_flagged_rows, min(limit, 200))
    return {"rows": rows, "count": len(rows)}


@router.get("/telemetry/adjudicated")
async def telemetry_adjudicated(limit: int = 50, tier: str | None = None):
    """Adjudicated MCP governance events, optionally filtered by risk tier."""
    rows = await asyncio.to_thread(_fetch_adjudicated_rows, min(limit, 200), tier)
    return {"rows": rows, "count": len(rows)}


@router.get("/telemetry/human-review")
async def human_review_queue():
    """All adjudicated MCP calls requiring human review, ordered by risk score."""
    if not db.is_available():
        return JSONResponse({"rows": [], "count": 0, "db": "unavailable"})
    try:
        rows = await asyncio.to_thread(_fetch_adjudicated_rows, 100, None)
        flagged = [r for r in rows if r.get("requires_human_review")]
        flagged.sort(key=lambda r: r.get("risk_score") or 0, reverse=True)
        return {"rows": flagged, "count": len(flagged)}
    except Exception as exc:
        return JSONResponse({"rows": [], "error": str(exc)}, status_code=500)


@router.post("/telemetry/process")
async def trigger_process():
    """Manually trigger one batch of MCP governance processing."""
    n = await _process_batch()
    return {"adjudicated": n, "ubo_available": _HAS_UBO}
