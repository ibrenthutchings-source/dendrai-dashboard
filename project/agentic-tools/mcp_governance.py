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

Additional capabilities
-----------------------
    Pre-execution holds   — proxy inserts PENDING rows; operator approves / denies
    Alert webhook         — ESCALATE verdicts POST to MCP_ALERT_WEBHOOK_URL
    Suppression allowlist — known-good (tool, args-hash) pairs skip the pipeline
    Session timeline      — chronological view of all calls in a session
    Coverage report       — tools with zero flag rate (potential blind spots)

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
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

logger = logging.getLogger("ubo.governance")

# ── UBO pipeline import (optional — degrades to no-op if package not on path) ─
# Try one level up first (Docker: /app/UBO), then two levels up (local: repo-root/UBO).

_here = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    os.path.normpath(os.path.join(_here, "..")),        # Docker: /app
    os.path.normpath(os.path.join(_here, "..", "..")),  # local dev: repo root
):
    if os.path.isdir(os.path.join(_candidate, "UBO")) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)
        break

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

POLL_INTERVAL_S    = float(os.environ.get("MCP_GOV_POLL_INTERVAL_S", "30"))
BATCH_SIZE         = int(os.environ.get("MCP_GOV_BATCH_SIZE", "20"))
# Only run the full Council for HIGH and above; MEDIUM/LOW get auto-cleared
COUNCIL_TIERS      = {"CRITICAL", "HIGH", "MEDIUM"}
# Slack-compatible webhook for ESCALATE verdicts (optional)
_ALERT_WEBHOOK_URL = os.environ.get("MCP_ALERT_WEBHOOK_URL", "")

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


# ── Alert webhook ──────────────────────────────────────────────────────────────

def _dispatch_alert(
    *,
    tool_name: str,
    session_id: str,
    risk_tier: str,
    risk_score: float,
    verdict: str,
    reasoning: str,
) -> None:
    """POST a Slack-compatible alert payload for ESCALATE verdicts."""
    if not _ALERT_WEBHOOK_URL:
        return
    try:
        body = json.dumps({
            "text": (
                f"\U0001f6a8 *MCP GOVERNANCE ESCALATE* — `{tool_name}` "
                f"(session {session_id[:8]}…)"
            ),
            "attachments": [{
                "color": "#c0392b",
                "fields": [
                    {"title": "Tool",       "value": tool_name,            "short": True},
                    {"title": "Risk Tier",  "value": risk_tier,            "short": True},
                    {"title": "Risk Score", "value": f"{risk_score:.3f}",  "short": True},
                    {"title": "Verdict",    "value": verdict,              "short": True},
                    {"title": "Session",    "value": session_id[:8] + "…", "short": True},
                    {"title": "Reasoning",  "value": (reasoning or "")[:300]},
                ],
            }],
        }).encode("utf-8")
        req = urllib.request.Request(
            _ALERT_WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
        logger.info("Alert dispatched for tool=%s verdict=%s", tool_name, verdict)
    except Exception as exc:
        logger.warning("Alert webhook failed: %s", exc)


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
    """Write adjudication result, stamp processed_at, and dispatch webhook on ESCALATE."""
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

        # Dispatch webhook alert for ESCALATE verdicts (non-fatal)
        if adj and getattr(getattr(adj, "final_verdict", None), "value", None) == "ESCALATE":
            try:
                _dispatch_alert(
                    tool_name=uro.conformed_payload.resource_id if uro.conformed_payload else "unknown",
                    session_id=session_id,
                    risk_tier=str(uro.risk_tier or "UNKNOWN"),
                    risk_score=float(uro.risk_score) if uro.risk_score is not None else 0.0,
                    verdict="ESCALATE",
                    reasoning=(adj.conflict_reasoning or "")[:500],
                )
            except Exception as alert_exc:
                logger.warning("Alert dispatch error: %s", alert_exc)

    except Exception as exc:
        logger.warning("_write_adjudication error (telemetry_id=%s): %s", telemetry_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass


def _check_suppressed(row: dict) -> bool:
    """Return True if this telemetry row matches an active suppression rule."""
    if not db.is_available():
        return False
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM observability.tool_call_suppressions
                    WHERE active
                      AND (target_tool   IS NULL OR target_tool   = %s)
                      AND (server_name   IS NULL OR server_name   = %s)
                      AND (tool_args_hash IS NULL OR tool_args_hash = %s)
                    LIMIT 1
                    """,
                    (row.get("target_tool"), row.get("server_name"), row.get("tool_args_hash")),
                )
                return cur.fetchone() is not None
    except Exception as exc:
        logger.warning("_check_suppressed error: %s", exc)
        return False


def _stamp_processed_suppressed(telemetry_id: int) -> None:
    """Stamp processed_at on a suppressed row without writing an adjudication."""
    if not db.is_available():
        return
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE observability.mcp_telemetry SET processed_at = NOW() WHERE id = %s",
                    (telemetry_id,),
                )
            conn.commit()
    except Exception as exc:
        logger.warning("_stamp_processed_suppressed error (id=%s): %s", telemetry_id, exc)


# ── Read helpers ───────────────────────────────────────────────────────────────

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

    _BASE_COLS = """
        SELECT id, adjudicated_at, session_id, target_tool, server_name,
               risk_flags, risk_score, risk_tier, final_verdict,
               ensemble_confidence, requires_human_review,
               conflict_flags, policy_violations, adjudicator_reasoning,
               source_system
    """

    for include_council in (True, False):
        extra = ", council_votes" if include_council else ""
        try:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    if tier:
                        cur.execute(
                            _BASE_COLS + extra + """
                            FROM observability.adjudicated_tool_calls
                            WHERE risk_tier = %s
                            ORDER BY adjudicated_at DESC
                            LIMIT %s
                            """,
                            (tier.upper(), limit),
                        )
                    else:
                        cur.execute(
                            _BASE_COLS + extra + """
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
            if include_council and "council_votes" in str(exc):
                logger.debug("council_votes column not yet migrated, retrying without it")
                continue
            logger.warning("_fetch_adjudicated_rows error: %s", exc)
            return []
    return []


def _fetch_raw_telemetry(limit: int) -> list[dict]:
    """Fetch the most recent mcp_telemetry rows for the live-stream view."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, ts, session_id, direction, method,
                           target_tool, execution_time_ms, status,
                           server_name, risk_flags, processed_at
                    FROM observability.mcp_telemetry
                    ORDER BY ts DESC
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
                    for tf in ("ts", "processed_at"):
                        if d.get(tf) and hasattr(d[tf], "isoformat"):
                            d[tf] = d[tf].isoformat()
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_raw_telemetry error: %s", exc)
        return []


def _human_review_adjudication(row_id: int, human_verdict: str, notes: str) -> bool:
    """
    Mark an adjudicated_tool_calls row as human-reviewed.
    Clears requires_human_review, optionally overrides final_verdict,
    and appends the reviewer note to adjudicator_reasoning.
    """
    if not db.is_available():
        return False
    try:
        safe_verdict = human_verdict.strip()[:32] if human_verdict else ""
        safe_notes   = (notes or "").strip()[:2000]
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.adjudicated_tool_calls
                    SET requires_human_review = FALSE,
                        final_verdict = CASE WHEN %s <> '' THEN %s ELSE final_verdict END,
                        adjudicator_reasoning = COALESCE(adjudicator_reasoning, '')
                            || E'\\n\\n[HUMAN REVIEW ' || to_char(NOW(), 'YYYY-MM-DD HH24:MI') || '] '
                            || CASE WHEN %s <> '' THEN 'verdict=' || %s || ' ' ELSE '' END
                            || CASE WHEN %s <> '' THEN 'notes=' || %s ELSE '' END
                    WHERE id = %s
                    """,
                    (safe_verdict, safe_verdict,
                     safe_verdict, safe_verdict,
                     safe_notes,  safe_notes,
                     row_id),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("_human_review_adjudication error (id=%s): %s", row_id, exc)
        return False


# ── Governance holds ───────────────────────────────────────────────────────────

def _fetch_pending_holds() -> list[dict]:
    """Fetch all PENDING governance holds (pre-execution blocking gate)."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, session_id, message_id, target_tool, tool_args_hash,
                           status, created_at
                    FROM observability.tool_call_holds
                    WHERE status = 'PENDING'
                    ORDER BY created_at ASC
                    """
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("session_id"):
                        d["session_id"] = str(d["session_id"])
                    if d.get("created_at") and hasattr(d["created_at"], "isoformat"):
                        d["created_at"] = d["created_at"].isoformat()
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_pending_holds error: %s", exc)
        return []


def _resolve_hold(hold_id: int, status: str, resolved_by: str = "operator") -> bool:
    """Approve or deny a pending governance hold."""
    if not db.is_available():
        return False
    safe_status = status.upper()
    if safe_status not in ("APPROVED", "DENIED"):
        return False
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.tool_call_holds
                    SET status = %s, resolved_at = NOW(), resolved_by = %s
                    WHERE id = %s AND status = 'PENDING'
                    """,
                    (safe_status, resolved_by[:128], hold_id),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as exc:
        logger.warning("_resolve_hold error (id=%s): %s", hold_id, exc)
        return False


# ── Session timeline ───────────────────────────────────────────────────────────

def _fetch_session_timeline(session_id: str) -> list[dict]:
    """All telemetry rows for a session in chronological order, joined to adjudications."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.id, t.ts, t.direction, t.method, t.target_tool,
                           t.execution_time_ms, t.status, t.error_message,
                           t.server_name, t.risk_flags, t.processed_at,
                           a.final_verdict, a.risk_tier, a.risk_score
                    FROM observability.mcp_telemetry t
                    LEFT JOIN observability.adjudicated_tool_calls a
                           ON a.telemetry_id = t.id
                    WHERE t.session_id = %s::uuid
                    ORDER BY t.ts ASC
                    LIMIT 500
                    """,
                    (session_id,),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    for tf in ("ts", "processed_at"):
                        if d.get(tf) and hasattr(d[tf], "isoformat"):
                            d[tf] = d[tf].isoformat()
                    if d.get("risk_score") is not None:
                        d["risk_score"] = float(d["risk_score"])
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_session_timeline error: %s", exc)
        return []


# ── Coverage report ────────────────────────────────────────────────────────────

def _fetch_coverage() -> list[dict]:
    """Per-tool flag rate; 0% = potential blind spot (never triggered a governance rule)."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        server_name,
                        target_tool,
                        COUNT(*) AS call_count,
                        COUNT(*) FILTER (
                            WHERE risk_flags IS NOT NULL
                              AND array_length(risk_flags, 1) > 0
                        ) AS flagged_count,
                        ROUND(
                            100.0 * COUNT(*) FILTER (
                                WHERE risk_flags IS NOT NULL
                                  AND array_length(risk_flags, 1) > 0
                            ) / NULLIF(COUNT(*), 0), 1
                        ) AS flag_rate
                    FROM observability.mcp_telemetry
                    WHERE direction = 'response'
                      AND target_tool IS NOT NULL
                    GROUP BY server_name, target_tool
                    ORDER BY flagged_count DESC, call_count DESC
                    """
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("flag_rate") is not None:
                        d["flag_rate"] = float(d["flag_rate"])
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_coverage error: %s", exc)
        return []


# ── Suppression allowlist ──────────────────────────────────────────────────────

def _fetch_suppressions() -> list[dict]:
    """Fetch all suppression rules (active and inactive)."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, server_name, target_tool, tool_args_hash,
                           reason, active, created_at, created_by
                    FROM observability.tool_call_suppressions
                    ORDER BY created_at DESC
                    LIMIT 200
                    """
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("created_at") and hasattr(d["created_at"], "isoformat"):
                        d["created_at"] = d["created_at"].isoformat()
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_suppressions error: %s", exc)
        return []


def _add_suppression(
    server_name: str | None,
    target_tool: str | None,
    tool_args_hash: str | None,
    reason: str,
    created_by: str = "operator",
) -> int | None:
    """Insert a new suppression rule; returns the new row ID."""
    if not db.is_available():
        return None
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.tool_call_suppressions
                        (server_name, target_tool, tool_args_hash, reason, created_by)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        server_name or None,
                        target_tool or None,
                        tool_args_hash or None,
                        reason[:500],
                        created_by[:128],
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("_add_suppression error: %s", exc)
        return None


def _delete_suppression(suppression_id: int) -> bool:
    """Soft-delete (deactivate) a suppression rule."""
    if not db.is_available():
        return False
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.tool_call_suppressions
                    SET active = FALSE
                    WHERE id = %s AND active
                    """,
                    (suppression_id,),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as exc:
        logger.warning("_delete_suppression error (id=%s): %s", suppression_id, exc)
        return False


# ── Core processing logic ──────────────────────────────────────────────────────

async def _process_one(row: dict) -> bool:
    """
    Run one telemetry row through the full UBO pipeline and persist the result.
    Suppressed rows are auto-cleared without entering the pipeline.
    Returns True on success, False if any stage fails.
    """
    telemetry_id = row["id"]
    session_id   = row.get("session_id", "UNKNOWN")

    # Check suppression list first — if matched, auto-clear without UBO pipeline
    if await asyncio.to_thread(_check_suppressed, row):
        await asyncio.to_thread(_stamp_processed_suppressed, telemetry_id)
        logger.info(
            "Suppressed telemetry %d: tool=%s server=%s — auto-cleared",
            telemetry_id, row.get("target_tool"), row.get("server_name"),
        )
        return True

    bronze, silver, gold, council = _get_pipeline()
    if bronze is None:
        return False

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


@router.get("/telemetry/raw")
async def raw_telemetry(limit: int = 100):
    """Most recent mcp_telemetry rows for the live-stream feed view."""
    rows = await asyncio.to_thread(_fetch_raw_telemetry, min(limit, 500))
    return {"rows": rows, "count": len(rows)}


@router.put("/telemetry/adjudicated/{row_id}/review")
async def human_review_adjudication(row_id: int, body: dict = Body(...)):
    """
    Mark an adjudicated record as human-reviewed.
    Body: { human_verdict: "APPROVE|ESCALATE|CLEAR|MONITOR", notes: "..." }
    """
    human_verdict = str(body.get("human_verdict") or "")
    notes         = str(body.get("notes") or "")
    ok = await asyncio.to_thread(_human_review_adjudication, row_id, human_verdict, notes)
    return {"ok": ok, "id": row_id}


# ── Governance holds endpoints ─────────────────────────────────────────────────

@router.get("/holds")
async def list_holds():
    """Pending pre-execution governance holds awaiting operator approval."""
    rows = await asyncio.to_thread(_fetch_pending_holds)
    return {"rows": rows, "count": len(rows)}


@router.put("/holds/{hold_id}/resolve")
async def resolve_hold(hold_id: int, body: dict = Body(...)):
    """
    Approve or deny a pending governance hold.
    Body: { status: "APPROVED" | "DENIED", resolved_by: "operator" }
    """
    status = str(body.get("status") or "").upper()
    if status not in ("APPROVED", "DENIED"):
        return JSONResponse({"error": "status must be APPROVED or DENIED"}, status_code=400)
    resolved_by = str(body.get("resolved_by") or "operator")[:128]
    ok = await asyncio.to_thread(_resolve_hold, hold_id, status, resolved_by)
    return {"ok": ok, "id": hold_id, "status": status}


# ── Session timeline endpoint ──────────────────────────────────────────────────

@router.get("/session/{session_id}/timeline")
async def session_timeline(session_id: str):
    """All telemetry rows for a session in chronological order."""
    rows = await asyncio.to_thread(_fetch_session_timeline, session_id)
    return {"rows": rows, "count": len(rows), "session_id": session_id}


# ── Coverage report endpoint ───────────────────────────────────────────────────

@router.get("/coverage")
async def tool_coverage():
    """Per-tool flag rate; 0% flag_rate entries are governance blind spots."""
    rows = await asyncio.to_thread(_fetch_coverage)
    blind_spots = sum(1 for r in rows if (r.get("flag_rate") or 0) == 0)
    return {"rows": rows, "count": len(rows), "blind_spots": blind_spots}


# ── Suppression allowlist endpoints ───────────────────────────────────────────

@router.get("/suppressions")
async def list_suppressions():
    """Active and inactive suppression rules (tool/server allowlist)."""
    rows = await asyncio.to_thread(_fetch_suppressions)
    return {"rows": rows, "count": len(rows)}


@router.post("/suppressions")
async def add_suppression(body: dict = Body(...)):
    """
    Add a suppression rule to auto-clear matching flagged tool calls.
    Body: { server_name, target_tool, tool_args_hash, reason, created_by }
    All filter fields are optional; omitting one means "match any".
    """
    new_id = await asyncio.to_thread(
        _add_suppression,
        body.get("server_name"),
        body.get("target_tool"),
        body.get("tool_args_hash"),
        str(body.get("reason") or "")[:500],
        str(body.get("created_by") or "operator")[:128],
    )
    return {"ok": new_id is not None, "id": new_id}


@router.delete("/suppressions/{suppression_id}")
async def delete_suppression(suppression_id: int):
    """Deactivate a suppression rule (soft delete)."""
    ok = await asyncio.to_thread(_delete_suppression, suppression_id)
    return {"ok": ok, "id": suppression_id}


# ── Monitored systems CRUD ─────────────────────────────────────────────────────

def _fetch_systems() -> list[dict]:
    """Return all monitored systems with activity stats unioned from both telemetry tables."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        s.id, s.display_name, s.server_name, s.server_type,
                        s.description, s.active, s.governance_tiers,
                        s.blocking_tools, s.alert_webhook,
                        s.created_at, s.updated_at, s.created_by,
                        s.ingest_api_key,
                        COALESCE(mt.total_calls,   0) + COALESCE(st.total_calls,   0) AS total_calls,
                        COALESCE(mt.flagged_calls, 0) + COALESCE(st.flagged_calls, 0) AS flagged_calls,
                        GREATEST(mt.last_seen, st.last_seen) AS last_seen
                    FROM observability.monitored_systems s
                    LEFT JOIN (
                        SELECT server_name,
                               COUNT(*)  AS total_calls,
                               COUNT(*) FILTER (WHERE risk_flags IS NOT NULL
                                                  AND array_length(risk_flags, 1) > 0) AS flagged_calls,
                               MAX(ts)  AS last_seen
                        FROM observability.mcp_telemetry
                        GROUP BY server_name
                    ) mt ON mt.server_name = s.server_name
                    LEFT JOIN (
                        SELECT server_name,
                               COUNT(*)  AS total_calls,
                               COUNT(*) FILTER (WHERE array_length(risk_flags, 1) > 0) AS flagged_calls,
                               MAX(created_at) AS last_seen
                        FROM observability.system_telemetry
                        GROUP BY server_name
                    ) st ON st.server_name = s.server_name
                    GROUP BY s.id, mt.total_calls, mt.flagged_calls, mt.last_seen,
                             st.total_calls, st.flagged_calls, st.last_seen
                    ORDER BY s.active DESC, s.display_name ASC
                    """
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    for tf in ("created_at", "updated_at", "last_seen"):
                        if d.get(tf) and hasattr(d[tf], "isoformat"):
                            d[tf] = d[tf].isoformat()
                    if d.get("ingest_api_key"):
                        d["ingest_api_key"] = str(d["ingest_api_key"])
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_systems error: %s", exc)
        return []


def _create_system(
    display_name: str,
    server_name: str,
    server_type: str,
    description: str | None,
    active: bool,
    governance_tiers: list[str],
    blocking_tools: list[str] | None,
    alert_webhook: str | None,
    created_by: str = "operator",
) -> int | None:
    if not db.is_available():
        return None
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.monitored_systems
                        (display_name, server_name, server_type, description, active,
                         governance_tiers, blocking_tools, alert_webhook, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        display_name[:128],
                        server_name[:128],
                        server_type[:64],
                        (description or None),
                        active,
                        governance_tiers or ["CRITICAL", "HIGH", "MEDIUM"],
                        blocking_tools or None,
                        (alert_webhook or None),
                        created_by[:128],
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("_create_system error: %s", exc)
        return None


def _update_system(
    system_id: int,
    display_name: str,
    server_name: str,
    server_type: str,
    description: str | None,
    active: bool,
    governance_tiers: list[str],
    blocking_tools: list[str] | None,
    alert_webhook: str | None,
) -> bool:
    if not db.is_available():
        return False
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.monitored_systems
                    SET display_name     = %s,
                        server_name      = %s,
                        server_type      = %s,
                        description      = %s,
                        active           = %s,
                        governance_tiers = %s,
                        blocking_tools   = %s,
                        alert_webhook    = %s,
                        updated_at       = NOW()
                    WHERE id = %s
                    """,
                    (
                        display_name[:128],
                        server_name[:128],
                        server_type[:64],
                        (description or None),
                        active,
                        governance_tiers or ["CRITICAL", "HIGH", "MEDIUM"],
                        blocking_tools or None,
                        (alert_webhook or None),
                        system_id,
                    ),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as exc:
        logger.warning("_update_system error (id=%s): %s", system_id, exc)
        return False


def _delete_system(system_id: int) -> bool:
    """Soft-delete a monitored system (sets active=FALSE)."""
    if not db.is_available():
        return False
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE observability.monitored_systems SET active = FALSE, updated_at = NOW() WHERE id = %s",
                    (system_id,),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as exc:
        logger.warning("_delete_system error (id=%s): %s", system_id, exc)
        return False


# ── Generic system telemetry ───────────────────────────────────────────────────
# Enterprise systems (Saviynt, SAP, Oracle Fusion, ServiceNow, etc.) authenticate
# with their per-system ingest_api_key and POST events to /observability/telemetry/ingest.

_PRIVILEGED_ACTIONS = {
    "grant_role", "revoke_role", "assign_role", "reset_password", "modify_permissions",
    "override", "bypass", "delete_audit", "provision_admin", "disable_user",
    "enable_user", "unlock_account", "approve_access", "elevate_privilege",
}
_PRIVILEGED_RESOURCE_KW = {"admin", "root", "superuser", "privileged", "elevated", "sysadmin", "basis"}
_SENSITIVE_RESOURCE_KW  = {"financial", "payroll", "pii", "ssn", "credit", "audit_log", "compliance", "salary"}
_SOD_KW                 = {"sod", "segregation", "conflict", "violation", "dual_control", "incompatible"}


def _detect_system_flags(event: dict) -> list[str]:
    """Apply generic detection rules to a system telemetry event. Returns risk flag list."""
    flags: set[str] = set()
    action   = (event.get("action")   or "").lower()
    resource = (event.get("resource") or "").lower()
    severity = (event.get("severity") or "INFO").upper()
    payload  = event.get("payload") or {}
    payload_str = str(payload).lower()

    if action in _PRIVILEGED_ACTIONS or any(k in resource for k in _PRIVILEGED_RESOURCE_KW):
        flags.add("privileged_access")
    if any(k in resource for k in _SENSITIVE_RESOURCE_KW):
        flags.add("sensitive_resource")
    if (payload.get("sod_violation") or any(k in payload_str for k in _SOD_KW)
            or "sod" in (event.get("event_type") or "").lower()):
        flags.add("sod_violation")
    if severity == "CRITICAL" or payload.get("policy_violation"):
        flags.add("policy_violation")
    return sorted(flags)


def _get_system_by_api_key(api_key: str) -> dict | None:
    """Look up a monitored system by its ingest API key."""
    if not db.is_available() or not api_key:
        return None
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, display_name, server_name, server_type, active,
                           governance_tiers, alert_webhook
                    FROM observability.monitored_systems
                    WHERE ingest_api_key = %s::uuid AND active = TRUE
                    """,
                    (api_key,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
    except Exception as exc:
        logger.warning("_get_system_by_api_key error: %s", exc)
        return None


def _ingest_system_event(
    server_name: str,
    system_type: str,
    event_type: str,
    event_id: str | None,
    actor: str | None,
    action: str | None,
    resource: str | None,
    severity: str,
    risk_flags: list[str],
    raw_payload: dict | None,
    source_ip: str | None,
) -> int | None:
    """Insert a single event into system_telemetry. Returns new row id."""
    if not db.is_available():
        return None
    try:
        import json as _json
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.system_telemetry
                        (server_name, system_type, event_type, event_id, actor,
                         action, resource, severity, risk_flags, raw_payload, source_ip)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        server_name[:128],
                        system_type[:64],
                        event_type[:128],
                        (event_id or None),
                        (actor or None),
                        (action or None),
                        (resource or None),
                        severity[:32],
                        risk_flags or [],
                        (_json.dumps(raw_payload) if raw_payload else None),
                        (source_ip or None),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("_ingest_system_event error: %s", exc)
        return None


def _fetch_system_telemetry(
    server_name: str | None = None,
    severity: str | None = None,
    flagged_only: bool = False,
    limit: int = 100,
) -> list[dict]:
    """Fetch recent events from system_telemetry with optional filters."""
    if not db.is_available():
        return []
    try:
        filters = []
        params: list = []
        if server_name:
            filters.append("server_name = %s")
            params.append(server_name)
        if severity:
            filters.append("severity = %s")
            params.append(severity.upper())
        if flagged_only:
            filters.append("array_length(risk_flags, 1) > 0")
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(min(limit, 500))
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, server_name, system_type, event_type, event_id,
                           actor, action, resource, severity, risk_flags,
                           raw_payload, source_ip, created_at
                    FROM observability.system_telemetry
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("created_at") and hasattr(d["created_at"], "isoformat"):
                        d["created_at"] = d["created_at"].isoformat()
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_system_telemetry error: %s", exc)
        return []


# ── Telemetry ingest endpoint ──────────────────────────────────────────────────

@router.post("/telemetry/ingest")
async def ingest_system_telemetry(request: Request, body: dict = Body(...)):
    """
    Receive a telemetry event from any registered system.

    Authentication: Authorization: Bearer <ingest_api_key>
    The ingest_api_key is shown per system in the UBO Configuration screen.

    Required fields: server_name, event_type
    Optional fields: event_id, actor, action, resource, severity (INFO/WARNING/HIGH/CRITICAL),
                     payload (arbitrary JSON object)

    Example (Saviynt access provisioning):
        {
          "server_name": "saviynt-prod",
          "event_type":  "access_provisioned",
          "event_id":    "SAV-12345",
          "actor":       "john.doe@company.com",
          "action":      "GRANT_ROLE",
          "resource":    "SAP_BASIS_ADMIN",
          "severity":    "HIGH",
          "payload":     { "sod_violation": true, "approver": "jane.smith@company.com" }
        }
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <ingest_api_key>")
    api_key = auth_header[len("Bearer "):].strip()

    system = await asyncio.to_thread(_get_system_by_api_key, api_key)
    if not system:
        raise HTTPException(status_code=401, detail="Invalid ingest API key")

    server_name = body.get("server_name", "")
    if server_name and server_name != system["server_name"]:
        raise HTTPException(
            status_code=403,
            detail=f"server_name '{server_name}' does not match the key's registered system '{system['server_name']}'",
        )
    server_name = system["server_name"]

    event_type = (body.get("event_type") or "").strip()
    if not event_type:
        raise HTTPException(status_code=422, detail="event_type is required")

    flags = _detect_system_flags(body)
    source_ip = request.client.host if request.client else None

    row_id = await asyncio.to_thread(
        _ingest_system_event,
        server_name,
        system["server_type"],
        event_type,
        body.get("event_id"),
        body.get("actor"),
        body.get("action"),
        body.get("resource"),
        (body.get("severity") or "INFO").upper(),
        flags,
        body.get("payload"),
        source_ip,
    )

    return {
        "ok": True,
        "id": row_id,
        "server_name": server_name,
        "flags": flags,
    }


@router.get("/telemetry")
async def list_system_telemetry(
    server_name: str = Query(default=""),
    severity: str = Query(default=""),
    flagged_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Recent telemetry events from all registered (non-MCP) systems."""
    rows = await asyncio.to_thread(
        _fetch_system_telemetry,
        server_name or None,
        severity or None,
        flagged_only,
        limit,
    )
    return {"rows": rows, "count": len(rows)}


@router.get("/systems")
async def list_systems():
    """All monitored systems registered with the UBO Governance Brain."""
    rows = await asyncio.to_thread(_fetch_systems)
    return {"rows": rows, "count": len(rows)}


@router.post("/systems")
async def create_system(body: dict = Body(...)):
    """Register a new system for UBO Governance Brain monitoring."""
    new_id = await asyncio.to_thread(
        _create_system,
        str(body.get("display_name") or "")[:128],
        str(body.get("server_name") or "")[:128],
        str(body.get("server_type") or "custom")[:64],
        body.get("description"),
        bool(body.get("active", True)),
        list(body.get("governance_tiers") or ["CRITICAL", "HIGH", "MEDIUM"]),
        list(body.get("blocking_tools") or []) or None,
        body.get("alert_webhook"),
        str(body.get("created_by") or "operator")[:128],
    )
    return {"ok": new_id is not None, "id": new_id}


@router.put("/systems/{system_id}")
async def update_system(system_id: int, body: dict = Body(...)):
    """Update an existing monitored system."""
    ok = await asyncio.to_thread(
        _update_system,
        system_id,
        str(body.get("display_name") or "")[:128],
        str(body.get("server_name") or "")[:128],
        str(body.get("server_type") or "custom")[:64],
        body.get("description"),
        bool(body.get("active", True)),
        list(body.get("governance_tiers") or ["CRITICAL", "HIGH", "MEDIUM"]),
        list(body.get("blocking_tools") or []) or None,
        body.get("alert_webhook"),
    )
    return {"ok": ok, "id": system_id}


@router.delete("/systems/{system_id}")
async def delete_system(system_id: int):
    """Deactivate a monitored system (soft delete)."""
    ok = await asyncio.to_thread(_delete_system, system_id)
    return {"ok": ok, "id": system_id}


# ── PAC repositories CRUD ──────────────────────────────────────────────────────

def _fetch_pac_repos() -> list[dict]:
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, display_name, provider, repo_url, branch,
                           rego_path, process, description, active,
                           created_at, updated_at, created_by
                    FROM observability.pac_repositories
                    ORDER BY active DESC, display_name ASC
                    """
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    for tf in ("created_at", "updated_at"):
                        if d.get(tf) and hasattr(d[tf], "isoformat"):
                            d[tf] = d[tf].isoformat()
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_pac_repos error: %s", exc)
        return []


def _create_pac_repo(
    display_name: str,
    provider: str,
    repo_url: str,
    branch: str,
    rego_path: str,
    process: str,
    description: str | None,
    active: bool,
    created_by: str = "operator",
) -> int | None:
    if not db.is_available():
        return None
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.pac_repositories
                        (display_name, provider, repo_url, branch, rego_path,
                         process, description, active, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        display_name[:128],
                        provider[:32],
                        repo_url,
                        branch[:128],
                        rego_path[:256],
                        process[:64],
                        description or None,
                        active,
                        created_by[:128],
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("_create_pac_repo error: %s", exc)
        return None


def _update_pac_repo(
    repo_id: int,
    display_name: str,
    provider: str,
    repo_url: str,
    branch: str,
    rego_path: str,
    process: str,
    description: str | None,
    active: bool,
) -> bool:
    if not db.is_available():
        return False
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.pac_repositories
                    SET display_name = %s, provider = %s, repo_url = %s,
                        branch = %s, rego_path = %s, process = %s,
                        description = %s, active = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        display_name[:128],
                        provider[:32],
                        repo_url,
                        branch[:128],
                        rego_path[:256],
                        process[:64],
                        description or None,
                        active,
                        repo_id,
                    ),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as exc:
        logger.warning("_update_pac_repo error (id=%s): %s", repo_id, exc)
        return False


def _delete_pac_repo(repo_id: int) -> bool:
    if not db.is_available():
        return False
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE observability.pac_repositories SET active = FALSE, updated_at = NOW() WHERE id = %s",
                    (repo_id,),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as exc:
        logger.warning("_delete_pac_repo error (id=%s): %s", repo_id, exc)
        return False


@router.get("/pac-repos")
async def list_pac_repos():
    """Policy-as-Code source repositories."""
    rows = await asyncio.to_thread(_fetch_pac_repos)
    return {"rows": rows, "count": len(rows)}


@router.post("/pac-repos")
async def create_pac_repo(body: dict = Body(...)):
    """Register a Policy-as-Code source repository."""
    new_id = await asyncio.to_thread(
        _create_pac_repo,
        str(body.get("display_name") or "")[:128],
        str(body.get("provider") or "github")[:32],
        str(body.get("repo_url") or ""),
        str(body.get("branch") or "main")[:128],
        str(body.get("rego_path") or "policies/")[:256],
        str(body.get("process") or "all")[:64],
        body.get("description"),
        bool(body.get("active", True)),
        str(body.get("created_by") or "operator")[:128],
    )
    return {"ok": new_id is not None, "id": new_id}


@router.put("/pac-repos/{repo_id}")
async def update_pac_repo(repo_id: int, body: dict = Body(...)):
    """Update a Policy-as-Code source repository."""
    ok = await asyncio.to_thread(
        _update_pac_repo,
        repo_id,
        str(body.get("display_name") or "")[:128],
        str(body.get("provider") or "github")[:32],
        str(body.get("repo_url") or ""),
        str(body.get("branch") or "main")[:128],
        str(body.get("rego_path") or "policies/")[:256],
        str(body.get("process") or "all")[:64],
        body.get("description"),
        bool(body.get("active", True)),
    )
    return {"ok": ok, "id": repo_id}


@router.delete("/pac-repos/{repo_id}")
async def delete_pac_repo(repo_id: int):
    """Deactivate a PAC repository (soft delete)."""
    ok = await asyncio.to_thread(_delete_pac_repo, repo_id)
    return {"ok": ok, "id": repo_id}
