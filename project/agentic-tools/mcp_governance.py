#!/usr/bin/env python3
"""
MCP Governance — bridges the Telemetry Proxy to the Dendrai UBO Governance Brain.

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
import hmac
import json
import logging
import os
import secrets
import sys
import urllib.request
import uuid as _uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
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
    from UBO.models.risk_intelligence import AgentVerdict, ConflictFlag
    _HAS_UBO = True
    logger.info("UBO Governance Brain loaded successfully")
except ImportError as exc:
    logger.warning("UBO not importable — MCP governance adjudication disabled: %s", exc)

import db  # project/agentic-tools/db.py — psycopg2 thread pool
import claude_client  # optional 4th-opinion reviewer for conflicted/low-confidence UROs
import pac_endpoints  # real Rego/OPA evaluation — see _evaluate_pac_policy below
import mcp_guards  # SSRF guard for user-supplied connector base_urls
import pol_domain_mappings  # POL-*/PaC control_id -> Core Domain, for the Adjudications tab's domain filter

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

def _post_webhook_alert(text: str, fields: list[dict], *, color: str = "#c0392b") -> None:
    """POST a Slack-compatible alert payload to MCP_ALERT_WEBHOOK_URL.
    Shared by ESCALATE-verdict alerting (_dispatch_alert below) and Model
    Health drift alerting (api_server.py's model_health_drift_watch) — the
    only ESCALATE-specific thing about the old _dispatch_alert was its field
    schema, not the POST mechanic itself, so this is the reusable half."""
    if not _ALERT_WEBHOOK_URL:
        return
    try:
        body = json.dumps({
            "text": text,
            "attachments": [{"color": color, "fields": fields}],
        }).encode("utf-8")
        req = urllib.request.Request(
            _ALERT_WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
        logger.info("Alert webhook dispatched")
    except Exception as exc:
        logger.warning("Alert webhook failed: %s", exc)


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
    _post_webhook_alert(
        f"\U0001f6a8 *MCP GOVERNANCE ESCALATE* — `{tool_name}` (session {session_id[:8]}…)",
        [
            {"title": "Tool",       "value": tool_name,            "short": True},
            {"title": "Risk Tier",  "value": risk_tier,            "short": True},
            {"title": "Risk Score", "value": f"{risk_score:.3f}",  "short": True},
            {"title": "Verdict",    "value": verdict,              "short": True},
            {"title": "Session",    "value": session_id[:8] + "…", "short": True},
            {"title": "Reasoning",  "value": (reasoning or "")[:300]},
        ],
    )
    logger.info("Alert dispatched for tool=%s verdict=%s", tool_name, verdict)


# ── LLM 4th-opinion reviewer ────────────────────────────────────────────────────
# The Quant / Linguist / Graph Architect are pure heuristics — regex, thresholds,
# temporal-graph correlation. When they disagree or land at low confidence (i.e.
# the Adjudicator already set requires_human_review), we get a real semantic read
# from Claude before the human ever sees it. This is strictly advisory: it is
# appended to council_votes as a 4th entry for the reviewer to read, and never
# changes the Adjudicator's deterministic verdict/score/requires_human_review —
# CRITICAL/HIGH-tier routing stays fully deterministic even if the LLM call fails
# or disagrees.

_COUNCIL_REVIEW_SYSTEM = """You are a senior fourth reviewer on the UBO Governance \
Council, brought in only when the three heuristic agents (The Quant, The Linguist, \
The Graph Architect) disagree or produced a low-confidence verdict. You have their \
full evaluations plus the Adjudicator's conflict analysis, and the underlying event \
evidence. Read the narrative/evidence directly and form your own independent \
judgment — do not simply average the other agents' verdicts. Call out anything the \
keyword-based heuristics likely missed: sarcasm, unusual phrasing, a legitimate-\
sounding justification that doesn't actually address the risk, or a false positive \
from an innocuous keyword match. Keep reasoning to 2-4 sentences."""

_COUNCIL_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict":    {"type": "string", "enum": ["ESCALATE", "MONITOR", "CLEAR", "INSUFFICIENT_DATA"]},
        "confidence": {"type": "number"},
        "risk_delta": {"type": "number"},
        "reasoning":  {"type": "string"},
    },
    "required": ["verdict", "confidence", "risk_delta", "reasoning"],
}


def _llm_council_opinion(uro: "URO", adj: Any) -> dict | None:
    """Advisory 4th evaluation from Claude. Returns an AgentEvaluation-shaped
    dict (same fields council_votes already stores) or None on any failure —
    this must never break adjudication."""
    if not claude_client.is_available():
        return None
    try:
        cp = uro.conformed_payload
        event_payload = {
            "event_type":     uro.event_type.value if uro.event_type else None,
            "source_system":  uro.source_system.value if uro.source_system else None,
            "actor":          getattr(uro, "actor_id", None),
            "resource":       cp.resource_id if cp else None,
            "risk_indicators": cp.risk_indicators if cp else {},
            "raw_narrative_fields": {
                k: v for k, v in (uro.raw_payload.content or {}).items()
                if isinstance(v, str) and len(v) < 2000
            },
        }
        agent_summaries = [
            {"agent": e.agent_name, "verdict": e.verdict.value, "confidence": e.confidence,
             "risk_delta": e.risk_delta, "reasoning": e.reasoning}
            for e in adj.evaluations
        ]
        user = (
            f"Event under review:\n{json.dumps(event_payload, indent=2, default=str)[:6000]}\n\n"
            f"Heuristic Council evaluations:\n{json.dumps(agent_summaries, indent=2)}\n\n"
            f"Adjudicator conflict analysis: {adj.conflict_reasoning}\n\n"
            "Form your own independent verdict."
        )
        result = claude_client.complete_json(
            _COUNCIL_REVIEW_SYSTEM, user, _COUNCIL_REVIEW_SCHEMA,
            label="ubo_council_review", model="claude-sonnet-4-6", effort="medium", max_tokens=1200,
        )
        return {
            "agent_name":    "The Reviewer (AI)",
            "verdict":       result.get("verdict", "INSUFFICIENT_DATA"),
            "confidence":    float(result.get("confidence", 0.5)),
            "risk_delta":    float(result.get("risk_delta", 0.0)),
            "reasoning":     result.get("reasoning", ""),
            "evidence":      {},
            "evaluation_ms": 0,
        }
    except Exception as exc:
        logger.warning("LLM council review skipped (uro=%s): %s", uro.id, exc)
        return None


# ── Database helpers (synchronous psycopg2, called via asyncio.to_thread) ─────

def _fetch_unprocessed(batch_size: int) -> list[dict]:
    """Fetch up to batch_size unprocessed flagged telemetry rows from mcp_telemetry."""
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
                    d["_origin"] = "mcp"
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_unprocessed error: %s", exc)
        return []


def _fetch_unprocessed_system(batch_size: int) -> list[dict]:
    """Fetch up to batch_size unprocessed flagged rows from system_telemetry —
    the generic REST-ingest path for non-MCP monitored systems (Saviynt, SAP,
    ServiceNow, etc.).

    raw_payload must be selected here — Silver's _conform_system_telemetry
    and _check_rule read detail sub-dicts (payroll_detail, treasury_detail,
    trade_compliance_detail, vendor_risk_detail, ai_governance_detail) off
    raw.get("raw_payload"), where `raw` is exactly the dict this function
    returns (see bronze.py's SystemTelemetryBronzeHandler.ingest, which
    passes the row straight through as raw_payload.content). Without this
    column those detail-field rules silently evaluate against {} in real
    production processing — they only ever appeared to work in synthetic
    smoke tests that constructed the URO's raw_payload by hand."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, created_at, server_name, system_type, event_type,
                           event_id, actor, action, resource, severity, risk_flags,
                           raw_payload
                    FROM observability.system_telemetry
                    WHERE array_length(risk_flags, 1) > 0
                      AND processed_at IS NULL
                    ORDER BY created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (batch_size,),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("created_at") is not None:
                        d["created_at"] = d["created_at"].isoformat() if hasattr(d["created_at"], "isoformat") else str(d["created_at"])
                    if d.get("risk_flags") is None:
                        d["risk_flags"] = []
                    d["_origin"] = "system"
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_unprocessed_system error: %s", exc)
        return []


# Which PaC process's Rego module a URO gets checked against, keyed by
# source_system. There is no real per-event process signal on a URO today
# (Silver conformation doesn't tag one) — this is a reasonable starting
# default (favoring ITGC — access/change-management controls — as the most
# broadly applicable process for systems with no clearer business-process
# affinity), not a definitive mapping. Adjust per-system as real usage shows
# which process actually applies.
_SOURCE_SYSTEM_TO_PAC_PROCESS = {
    "GITHUB":           "itgc",              # code/access change management
    "GITLAB":           "itgc",
    "SAILPOINT":        "itgc",              # IAM — access governance
    "ORACLE_FUSION":     "procure_to_pay",    # existing Oracle Fusion tool surface is procurement/controls-centric
    "SAP":               "record_to_report",  # SAP is typically the financial-close system of record
    "SYSTEM_TELEMETRY":  "itgc",
    "MCP_PROXY":         "itgc",
}
_DEFAULT_PAC_PROCESS = "itgc"

# Finer-grained override checked before _SOURCE_SYSTEM_TO_PAC_PROCESS: some
# event types need a different process than the rest of their source system's
# traffic (SYSTEM_TELEMETRY in particular is shared by every push-model
# system), so process routing can't key on source_system alone here —
# event_type disambiguates the subset.
_SOURCE_EVENT_TO_PAC_PROCESS = {
    ("SYSTEM_TELEMETRY", "INFRASTRUCTURE_FINDING"): "infrastructure_monitoring",
    # Financial Risk Pipeline — Record-to-Report-flavored, alongside the
    # existing P-R2R-001 manual-JE-approval rule.
    ("SYSTEM_TELEMETRY", "JE_VELOCITY_ANOMALY"):  "record_to_report",
    ("SYSTEM_TELEMETRY", "LIQUIDITY_SHIFT"):      "record_to_report",
    ("SYSTEM_TELEMETRY", "INVENTORY_DIVERGENCE"): "record_to_report",
    # Hire-to-Retire — oracle_hcm_tool.py payroll findings.
    ("SYSTEM_TELEMETRY", "GHOST_EMPLOYEE_SUSPECTED"):            "hire_to_retire",
    ("SYSTEM_TELEMETRY", "UNAUTHORIZED_PAY_RATE_CHANGE"):        "hire_to_retire",
    ("SYSTEM_TELEMETRY", "TERMINATED_EMPLOYEE_ACCESS_RETAINED"): "hire_to_retire",
    # Treasury & Cash Management — oracle_fusion_tool.py findings, routed into
    # the existing record_to_report process (same precedent as the Financial
    # Risk Pipeline's JE_VELOCITY_ANOMALY/etc. above — Treasury is R2R-adjacent,
    # no new process needed).
    ("SYSTEM_TELEMETRY", "WIRE_TRANSFER_SINGLE_APPROVAL"):  "record_to_report",
    ("SYSTEM_TELEMETRY", "BANK_RECON_OVERDUE"):             "record_to_report",
    ("SYSTEM_TELEMETRY", "FX_HEDGE_DOCUMENTATION_MISSING"): "record_to_report",
    # Export Control / Trade Compliance — denied_party_screening_tool.py.
    # Own process rather than splitting across procure_to_pay/order_to_cash:
    # a match touches both vendor (P2P) and customer (O2C) master data, and a
    # dedicated process keeps every sanctioned-party hit in one audit trail.
    ("SYSTEM_TELEMETRY", "EXPORT_CONTROL_MATCH"): "trade_compliance",
    # Continuous Third-Party/Vendor Risk — reuses procure_to_pay (no new
    # process — vendor risk is P2P-adjacent, per the existing VM-01/VM-02
    # control-library entries already living under the Vendor category).
    ("SYSTEM_TELEMETRY", "VENDOR_SOC2_EXPIRED"):         "procure_to_pay",
    ("SYSTEM_TELEMETRY", "VENDOR_CONCENTRATION_BREACH"): "procure_to_pay",
    # AI Governance — reuses itgc (IT General Controls). AI-01..06 sit under
    # the Technology domain alongside every other ITGC-category control;
    # no new process needed for this first slice (AI-05/AI-06 coverage).
    ("SYSTEM_TELEMETRY", "AI_ASSESSMENT_OVERDUE"):      "itgc",
    ("SYSTEM_TELEMETRY", "AI_HUMAN_OVERSIGHT_MISSING"): "itgc",
    # Order-to-Cash — see UBO/models/uro.py's EventType docstring for why
    # each of these is its own type rather than reusing an existing one.
    ("SYSTEM_TELEMETRY", "REVENUE_RECOGNITION_EVENT"): "order_to_cash",
    ("SYSTEM_TELEMETRY", "SALES_ORDER_CREDIT_EVENT"):  "order_to_cash",
    ("SYSTEM_TELEMETRY", "BILLING_EVENT"):             "order_to_cash",
    ("SYSTEM_TELEMETRY", "CASH_APPLICATION_EVENT"):    "order_to_cash",
    ("SYSTEM_TELEMETRY", "CUSTOMER_MASTER_CHANGE"):    "order_to_cash",
    ("SYSTEM_TELEMETRY", "AR_AGING_EVENT"):            "order_to_cash",
    # Procure-to-Pay.
    ("SYSTEM_TELEMETRY", "PURCHASE_ORDER_EVENT"):   "procure_to_pay",
    ("SYSTEM_TELEMETRY", "INVOICE_MATCH_EVENT"):    "procure_to_pay",
    ("SYSTEM_TELEMETRY", "VENDOR_MASTER_CHANGE"):   "procure_to_pay",
    ("SYSTEM_TELEMETRY", "PAYMENT_RUN_EVENT"):      "procure_to_pay",
    ("SYSTEM_TELEMETRY", "PROCUREMENT_SOD_CONFLICT"): "procure_to_pay",
    # Inventory Cycle (Receive -> Putaway -> Ship) — reuses procure_to_pay,
    # see UBO/models/uro.py's EventType docstring for why no new process.
    ("SYSTEM_TELEMETRY", "GOODS_RECEIPT_EVENT"):     "procure_to_pay",
    ("SYSTEM_TELEMETRY", "INVENTORY_PUTAWAY_EVENT"): "procure_to_pay",
    ("SYSTEM_TELEMETRY", "GOODS_SHIPMENT_EVENT"):    "procure_to_pay",
}


def _evaluate_pac_policy(uro: "URO") -> Optional[dict]:
    """
    Check a URO against the saved (or default) Rego PaC module for whatever
    process its source system maps to. Best-effort: any failure (missing
    module, OPA unavailable, malformed event) returns None rather than
    breaking adjudication — this mirrors every other best-effort accounting
    path in this codebase (e.g. claude_client._record_cost).

    Returns {"process", "rules_fired": [...], "engine": "opa"|"heuristic"}
    or None when there's nothing to report (no rules fired, or evaluation
    couldn't run at all).
    """
    try:
        source_system = uro.source_system.value if hasattr(uro.source_system, "value") else str(uro.source_system)
        event_type = uro.event_type.value if hasattr(uro.event_type, "value") else str(uro.event_type)
        process = _SOURCE_EVENT_TO_PAC_PROCESS.get(
            (source_system, event_type),
            _SOURCE_SYSTEM_TO_PAC_PROCESS.get(source_system, _DEFAULT_PAC_PROCESS),
        )

        saved = db.get_latest_pac_module(process) if db.is_available() else None
        rego_content = saved["rego_content"] if saved else pac_endpoints._REGO_DEFAULTS.get(process)
        if not rego_content:
            return None

        cp = uro.conformed_payload
        input_event = {
            "event": {
                "type":        uro.event_type.value if hasattr(uro.event_type, "value") else str(uro.event_type),
                "resource":    cp.resource_id if cp else None,
                "resource_type": cp.resource_type if cp else None,
                "action":      cp.action if cp else None,
                "outcome":     cp.outcome if cp else None,
                **(cp.risk_indicators if cp and cp.risk_indicators else {}),
            }
        }

        result = pac_endpoints.evaluate_policy_event(rego_content, input_event)
        fired = result.get("rules_fired") or []
        if not fired:
            return None
        return {
            "process": process,
            "rules_fired": fired,
            "engine": "opa" if str(result.get("evaluation", "")).startswith("opa eval") else "heuristic",
        }
    except Exception as exc:
        logger.debug("_evaluate_pac_policy skipped: %s", exc)
        return None


def _write_adjudication(
    source_id: int,
    origin: str,
    session_id: str | None,
    uro: "URO",
) -> None:
    """
    Write adjudication result, stamp processed_at, and dispatch webhook on ESCALATE.

    origin distinguishes which source table source_id refers to:
      "mcp"    → observability.mcp_telemetry (telemetry_id FK)
      "system" → observability.system_telemetry (system_telemetry_id FK) —
                 generic REST-ingested events have no MCP session, so session_id
                 is None for these rows.
    """
    if not db.is_available():
        return
    adj = uro.adjudication
    council_votes_list = [
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
    ]
    # Only the cases the heuristic Council already flagged for human review get
    # a 4th, LLM-based opinion — most events never pay for the extra API call.
    if adj and adj.requires_human_review:
        llm_eval = _llm_council_opinion(uro, adj)
        if llm_eval:
            council_votes_list.append(llm_eval)
            # The LLM only ever runs on cases already flagged for human review,
            # so it can't invent a NEW escalation from nothing — but if it
            # independently reaches ESCALATE while the deterministic ensemble
            # landed on MONITOR/CLEAR, that disagreement is real signal, not
            # narration to discard. We only ever move the verdict UP
            # (toward ESCALATE), never let the LLM talk the case back down
            # from what the ensemble already decided — a false negative from
            # trusting the LLM's downgrade is a worse failure mode than an
            # extra human review.
            if llm_eval["verdict"] == "ESCALATE" and adj.final_verdict != AgentVerdict.ESCALATE:
                adj = adj.model_copy(update={
                    "final_verdict": AgentVerdict.ESCALATE,
                    "requires_human_review": True,
                    "conflict_flags": list(adj.conflict_flags) + [ConflictFlag.LLM_ESCALATION_OVERRIDE],
                    "conflict_reasoning": (
                        (adj.conflict_reasoning + " ") if adj.conflict_reasoning else ""
                    ) + (
                        f"LLM 4th opinion independently reached ESCALATE "
                        f"(confidence={llm_eval['confidence']:.2f}) against the ensemble's "
                        f"original verdict — verdict raised to ESCALATE."
                    ),
                })

    # Real Rego/OPA policy check — a genuinely different kind of judgment
    # (deterministic policy engine, not a heuristic/LLM agent vote). A fired
    # deny rule is a human-authored, approved control being violated in real
    # time — that is not advisory the way a keyword-matching heuristic is, so
    # unlike the original design this is NOT just appended as an extra council
    # voice for visibility: it forces human review and (mirroring the existing
    # single-agent high-confidence veto in TheAdjudicator._resolve_verdict)
    # vetoes the ensemble verdict to ESCALATE. Fired rules are still folded
    # into policy_violations alongside the existing Silver-layer heuristic
    # violations below, for the audit record either way.
    pac_violations: list[str] = []
    pac_result = _evaluate_pac_policy(uro)
    if pac_result:
        pac_violations = [
            r.get("control_id") or f"PAC-{pac_result['process'].upper()}: {r.get('rule', 'unknown_rule')}"
            for r in pac_result["rules_fired"]
        ]
        council_votes_list.append({
            "agent_name": "Policy-as-Code (Rego)",
            "verdict": "ESCALATE" if pac_violations else "PROCEED",
            "confidence": 1.0 if pac_result["engine"] == "opa" else 0.6,
            "risk_delta": 0.0,
            "reasoning": (
                f"{len(pac_result['rules_fired'])} deny rule(s) fired against the "
                f"{pac_result['process']} policy module ({pac_result['engine']})."
            ),
            "evidence": {"process": pac_result["process"], "engine": pac_result["engine"],
                         "rules_fired": [r.get("rule") for r in pac_result["rules_fired"]]},
            "evaluation_ms": None,
        })
        if adj and pac_violations:
            adj = adj.model_copy(update={
                "final_verdict": AgentVerdict.ESCALATE,
                "requires_human_review": True,
                "conflict_flags": list(adj.conflict_flags) + [ConflictFlag.POLICY_VIOLATION],
                "conflict_reasoning": (
                    (adj.conflict_reasoning + " ") if adj.conflict_reasoning else ""
                ) + (
                    f"Policy-as-Code veto: {len(pac_violations)} deny rule(s) fired against the "
                    f"{pac_result['process']} module ({pac_result['engine']}) — verdict forced to "
                    f"ESCALATE regardless of the heuristic ensemble's score."
                ),
            })

    council_votes = json.dumps(council_votes_list)
    telemetry_id        = source_id if origin == "mcp" else None
    system_telemetry_id  = source_id if origin == "system" else None
    source_system_label  = "MCP_PROXY" if origin == "mcp" else "SYSTEM_TELEMETRY"
    # case_id/process_step: only present when the producer explicitly set
    # them (today, generate_o2c_p2p_synthetic_log.py's linked lifecycles) —
    # uro.raw_payload.content is the full system_telemetry row for a
    # "system"-origin event, whose own raw_payload column is the original
    # event payload the producer sent (see _ingest_system_event). No case
    # concept exists for "mcp"-origin tool calls, so both stay None there.
    _origin_payload = uro.raw_payload.content.get("raw_payload") or {} if origin == "system" else {}
    case_id      = _origin_payload.get("case_id")
    process_step = _origin_payload.get("process_step")
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Insert adjudication
                cur.execute(
                    """
                    INSERT INTO observability.adjudicated_tool_calls (
                        telemetry_id, system_telemetry_id, session_id, source_system,
                        target_tool, server_name, risk_flags, execution_time_ms,
                        uro_id, risk_score, risk_tier,
                        final_verdict, ai_final_verdict, ensemble_confidence,
                        requires_human_review, conflict_flags,
                        policy_violations, adjudicator_reasoning,
                        council_votes, case_id, process_step
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s, %s
                    )
                    """,
                    (
                        telemetry_id,
                        system_telemetry_id,
                        session_id,
                        source_system_label,
                        uro.conformed_payload.resource_id if uro.conformed_payload else None,
                        uro.environment.tags.get("server_name"),
                        list(uro.raw_payload.content.get("risk_flags") or []),
                        uro.raw_payload.content.get("execution_time_ms"),
                        uro.id,
                        float(uro.risk_score) if uro.risk_score is not None else None,
                        uro.risk_tier,
                        adj.final_verdict.value if adj else None,
                        # ai_final_verdict is a permanent snapshot of what the AI system
                        # (ensemble + PaC veto + LLM escalation, all applied above) actually
                        # decided — final_verdict above is the "current/effective" one and
                        # can later be overwritten by a human reviewer; this one never is.
                        adj.final_verdict.value if adj else None,
                        float(adj.ensemble_confidence) if adj else None,
                        adj.requires_human_review if adj else False,
                        [f.value for f in adj.conflict_flags] if adj else [],
                        list(uro.silver_policy_violations) + pac_violations,
                        (adj.conflict_reasoning[:1000] if adj and adj.conflict_reasoning else None),
                        council_votes,
                        case_id,
                        process_step,
                    ),
                )
                # Stamp source row as processed (correct table per origin)
                if origin == "mcp":
                    cur.execute(
                        "UPDATE observability.mcp_telemetry SET processed_at = NOW() WHERE id = %s",
                        (source_id,),
                    )
                else:
                    cur.execute(
                        "UPDATE observability.system_telemetry SET processed_at = NOW() WHERE id = %s",
                        (source_id,),
                    )
            conn.commit()

        # Dispatch webhook alert for ESCALATE verdicts (non-fatal)
        if adj and getattr(getattr(adj, "final_verdict", None), "value", None) == "ESCALATE":
            try:
                _dispatch_alert(
                    tool_name=uro.conformed_payload.resource_id if uro.conformed_payload else "unknown",
                    session_id=session_id or f"system-{source_id}",
                    risk_tier=str(uro.risk_tier or "UNKNOWN"),
                    risk_score=float(uro.risk_score) if uro.risk_score is not None else 0.0,
                    verdict="ESCALATE",
                    reasoning=(adj.conflict_reasoning or "")[:500],
                )
            except Exception as alert_exc:
                logger.warning("Alert dispatch error: %s", alert_exc)

    except Exception as exc:
        logger.warning("_write_adjudication error (source_id=%s origin=%s): %s", source_id, origin, exc)
        try:
            conn.rollback()
        except Exception:
            pass


def _check_suppressed(row: dict) -> bool:
    """
    Return True if this telemetry row matches an active suppression rule.
    Works for both origins: MCP rows match on target_tool, system_telemetry
    rows have no target_tool column so action (the closest analog — the verb
    performed) is used instead, matching how _fetch_coverage and _add_suppression
    both already treat the two interchangeably.
    """
    if not db.is_available():
        return False
    try:
        tool_value = row.get("target_tool") or row.get("action")
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
                    (tool_value, row.get("server_name"), row.get("tool_args_hash")),
                )
                return cur.fetchone() is not None
    except Exception as exc:
        logger.warning("_check_suppressed error: %s", exc)
        return False


def _stamp_processed_suppressed(telemetry_id: int, origin: str = "mcp") -> None:
    """Stamp processed_at on a suppressed row without writing an adjudication —
    correct table per origin, same as _write_adjudication's own stamping."""
    if not db.is_available():
        return
    table = "observability.mcp_telemetry" if origin == "mcp" else "observability.system_telemetry"
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {table} SET processed_at = NOW() WHERE id = %s",
                    (telemetry_id,),
                )
            conn.commit()
    except Exception as exc:
        logger.warning("_stamp_processed_suppressed error (id=%s origin=%s): %s", telemetry_id, origin, exc)


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


def _count_adjudicated_rows(tier: str | None) -> int:
    """Total adjudicated_tool_calls rows (optionally tier-filtered) — the
    denominator _fetch_adjudicated_rows' LIMIT/OFFSET page is a slice of, so
    the UI can say "showing 50 of 3,204" instead of implying 50 is
    everything."""
    if not db.is_available():
        return 0
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                if tier:
                    cur.execute(
                        "SELECT COUNT(*) FROM observability.adjudicated_tool_calls WHERE risk_tier = %s",
                        (tier.upper(),),
                    )
                else:
                    cur.execute("SELECT COUNT(*) FROM observability.adjudicated_tool_calls")
                row = cur.fetchone()
                return row[0] if row else 0
    except Exception as exc:
        logger.warning("_count_adjudicated_rows error: %s", exc)
        return 0


def _fetch_adjudicated_rows(limit: int, tier: str | None, offset: int = 0) -> list[dict]:
    """Read observability.adjudicated_tool_calls for the REST endpoint."""
    if not db.is_available():
        return []

    _BASE_COLS = """
        SELECT id, telemetry_id, system_telemetry_id, adjudicated_at, session_id,
               target_tool, server_name,
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
                            LIMIT %s OFFSET %s
                            """,
                            (tier.upper(), limit, offset),
                        )
                    else:
                        cur.execute(
                            _BASE_COLS + extra + """
                            FROM observability.adjudicated_tool_calls
                            ORDER BY adjudicated_at DESC
                            LIMIT %s OFFSET %s
                            """,
                            (limit, offset),
                        )
                    cols = [d[0] for d in cur.description]
                    # Same resolution api_server.py's /observability/events uses —
                    # computed once per call, not per row, since it hits db.list_controls().
                    ctrl_to_process = {c["control_id"]: c["process"] for c in db.list_controls() if c.get("process")}
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
                        d["domain"] = pol_domain_mappings.domain_for_violations(d.get("policy_violations"), ctrl_to_process)
                        rows.append(d)
                    return rows
        except Exception as exc:
            if include_council and "council_votes" in str(exc):
                logger.debug("council_votes column not yet migrated, retrying without it")
                continue
            logger.warning("_fetch_adjudicated_rows error: %s", exc)
            return []
    return []


def fetch_adjudications_for_run(run_id: int, run_at: str | None, completed_at: str | None) -> list[dict]:
    """Best-effort adjudication set for a run's Evidence Pack.

    Prefers rows with a real run_id match (currently rare — see the run_id
    column comment in db.py's adjudicated_tool_calls migration). Falls back
    to a time-window join against this run's execution window, since the
    dominant HTTP write path (mcp_http_telemetry._HTTP_SESSION_ID) shares
    one session_id UUID across every ticker/run the process ever handles
    and genuinely cannot identify which run a call belongs to. Each row is
    tagged linked_via so the caller can show which kind of match it is —
    "time_window_estimate" is NOT a verified link.
    """
    if not db.is_available() or not run_at:
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, adjudicated_at, target_tool, server_name,
                           final_verdict, risk_tier, requires_human_review,
                           policy_violations, run_id
                    FROM observability.adjudicated_tool_calls
                    WHERE run_id = %s
                       OR (run_id IS NULL
                           AND adjudicated_at BETWEEN %s::timestamptz - interval '10 minutes'
                                               AND COALESCE(%s::timestamptz, %s::timestamptz + interval '3 hours') + interval '30 minutes')
                    ORDER BY adjudicated_at
                    LIMIT 500
                    """,
                    (run_id, run_at, completed_at, run_at),
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    d["linked_via"] = "run_id" if d["run_id"] == run_id else "time_window_estimate"
                    if d.get("adjudicated_at"):
                        d["adjudicated_at"] = d["adjudicated_at"].isoformat()
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("fetch_adjudications_for_run error (run_id=%s): %s", run_id, exc)
        return []


def _fetch_raw_telemetry(limit: int) -> list[dict]:
    """Fetch the most recent telemetry rows for the live-stream view, merged
    across mcp_telemetry (MCP tool calls) and system_telemetry (generic
    REST-ingested events from any monitored system), newest first."""
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
                mcp_rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("session_id"):
                        d["session_id"] = str(d["session_id"])
                    for tf in ("ts", "processed_at"):
                        if d.get(tf) and hasattr(d[tf], "isoformat"):
                            d[tf] = d[tf].isoformat()
                    d["origin"] = "mcp"
                    mcp_rows.append(d)

                cur.execute(
                    """
                    SELECT id, created_at, server_name, system_type, event_type,
                           actor, resource, severity, risk_flags, processed_at
                    FROM observability.system_telemetry
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                cols = [d[0] for d in cur.description]
                system_rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    for tf in ("created_at", "processed_at"):
                        if d.get(tf) and hasattr(d[tf], "isoformat"):
                            d[tf] = d[tf].isoformat()
                    severity = str(d.get("severity") or "INFO").upper()
                    system_rows.append({
                        "id":                d["id"],
                        "origin":            "system",
                        "ts":                d["created_at"],
                        "session_id":        None,
                        "direction":         "event",
                        "method":            d.get("event_type"),
                        "target_tool":       d.get("resource") or d.get("server_name"),
                        "execution_time_ms": None,
                        "status":            "error" if severity in ("CRITICAL", "HIGH") else "ok",
                        "server_name":       d.get("server_name"),
                        "risk_flags":        d.get("risk_flags") or [],
                        "processed_at":      d.get("processed_at"),
                        "actor":             d.get("actor"),
                        "system_type":       d.get("system_type"),
                        "severity":          severity,
                    })

                merged = mcp_rows + system_rows
                merged.sort(key=lambda d: d.get("ts") or "", reverse=True)
                return merged[:limit]
    except Exception as exc:
        logger.warning("_fetch_raw_telemetry error: %s", exc)
        return []


def _human_review_adjudication(row_id: int, human_verdict: str, notes: str) -> bool:
    """
    Mark an adjudicated_tool_calls row as human-reviewed.
    Clears requires_human_review, optionally overrides final_verdict (the
    "current/effective" verdict), and appends the reviewer note to
    adjudicator_reasoning.

    Also stamps human_verdict/human_reviewed_at, distinct from the
    final_verdict overwrite above, and never touches ai_final_verdict (the
    frozen snapshot of what the AI decided) — so per-agent calibration can
    compare what each agent voted against what the human actually decided,
    which the destructive final_verdict overwrite alone can't answer once a
    row's been reviewed. human_verdict may be the literal string "APPROVE"
    (the UI's "confirm the AI's verdict" action, not one of the four
    canonical AgentVerdict values) — calibration reads that as "agrees with
    ai_final_verdict".
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
                        final_verdict = CASE WHEN %s <> '' AND %s <> 'APPROVE' THEN %s ELSE final_verdict END,
                        human_verdict = CASE WHEN %s <> '' THEN %s ELSE human_verdict END,
                        human_reviewed_at = NOW(),
                        adjudicator_reasoning = COALESCE(adjudicator_reasoning, '')
                            || E'\\n\\n[HUMAN REVIEW ' || to_char(NOW(), 'YYYY-MM-DD HH24:MI') || '] '
                            || CASE WHEN %s <> '' THEN 'verdict=' || %s || ' ' ELSE '' END
                            || CASE WHEN %s <> '' THEN 'notes=' || %s ELSE '' END
                    WHERE id = %s
                    """,
                    (safe_verdict, safe_verdict, safe_verdict,
                     safe_verdict, safe_verdict,
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
    """
    Per-tool flag rate; 0% = potential blind spot (never triggered a governance
    rule). Unions BOTH telemetry paths — mcp_telemetry (MCP tool calls) and
    system_telemetry (the generic REST-ingest path any other system or non-MCP
    AI agent uses) — a coverage report that only looked at MCP traffic would
    itself be a blind spot: GitHub, SAP, Saviynt, and any non-MCP agent's
    calls would be invisible here even though they're adjudicated by the same
    Council. system_telemetry has no target_tool column — action is its
    closest analog (the verb performed) and is reported under the same
    'target_tool' key so the UI/suppression matching can treat both uniformly.
    """
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 'mcp' AS kind, server_name, target_tool,
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

                    UNION ALL

                    SELECT 'system' AS kind, server_name, action AS target_tool,
                        COUNT(*) AS call_count,
                        COUNT(*) FILTER (WHERE array_length(risk_flags, 1) > 0) AS flagged_count,
                        ROUND(
                            100.0 * COUNT(*) FILTER (WHERE array_length(risk_flags, 1) > 0)
                            / NULLIF(COUNT(*), 0), 1
                        ) AS flag_rate
                    FROM observability.system_telemetry
                    WHERE action IS NOT NULL
                    GROUP BY server_name, action

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
    Suppressed rows are auto-cleared without entering the pipeline — applies to
    both origins; system_telemetry rows have no target_tool column, so
    _check_suppressed falls back to action (the closest analog) for those.
    Returns True on success, False if any stage fails.
    """
    source_id = row["id"]
    origin    = row.get("_origin", "mcp")
    session_id = row.get("session_id") if origin == "mcp" else None
    ubo_source = UBOSourceSystem.MCP_PROXY if origin == "mcp" else UBOSourceSystem.SYSTEM_TELEMETRY

    if await asyncio.to_thread(_check_suppressed, row):
        await asyncio.to_thread(_stamp_processed_suppressed, source_id, origin)
        logger.info(
            "Suppressed %s telemetry %d: tool=%s server=%s — auto-cleared",
            origin, source_id, row.get("target_tool") or row.get("action"), row.get("server_name"),
        )
        return True

    bronze, silver, gold, council = _get_pipeline()
    if bronze is None:
        return False

    try:
        # Bronze: map raw telemetry/system dict → URO (strip internal routing marker
        # before it lands in the audit-grade raw_payload)
        raw_event = {k: v for k, v in row.items() if k != "_origin"}
        if origin == "system" and raw_event.get("raw_payload"):
            # Restore plaintext payroll_detail/treasury_detail (encrypted at
            # rest by _ingest_system_event) — decrypt here, at the pipeline
            # boundary, so bronze/silver never have to know encryption exists.
            raw_event["raw_payload"] = _decrypt_sensitive_details(raw_event["raw_payload"])
        uro = await bronze.ingest(raw_event, ubo_source)

        # Silver: conform + Policy-as-Code
        uro = await silver.conform(uro)

        # Real identity/role data, when available, for the actor who
        # triggered this event. No Silver conformer for MCP proxy or
        # system_telemetry events ever sets role_count/entitlements — see
        # identity_graph_sync.py's module docstring — so without this,
        # The Graph Architect's blast-radius/SPoF checks always operate on
        # zeroed inputs. The guard means this never overrides a conformer
        # that already supplied real data (e.g. a future real SailPoint
        # entitlement pull), only fills in when it's still at the default.
        if uro.conformed_payload and uro.actor_id and not uro.conformed_payload.risk_indicators.get("role_count"):
            role_count = await asyncio.to_thread(db.get_identity_role_count, uro.actor_id)
            if role_count:
                uro.conformed_payload.risk_indicators["role_count"] = role_count
                uro.conformed_payload.risk_indicators["entitlements"] = await asyncio.to_thread(
                    db.get_identity_role_names, uro.actor_id
                )

        # Gold: risk score + tier
        uro = await gold.score(uro)

        # Council: Quant + Linguist + Graph Architect → Adjudicator
        uro = await council.evaluate(uro)

        # Persist adjudication (blocking psycopg2 call, run in thread)
        await asyncio.to_thread(_write_adjudication, source_id, origin, session_id, uro)

        logger.info(
            "Adjudicated %s %d: resource=%s tier=%s verdict=%s human_review=%s",
            origin, source_id,
            uro.conformed_payload.resource_id if uro.conformed_payload else "?",
            uro.risk_tier,
            uro.adjudication.final_verdict.value if uro.adjudication else "?",
            uro.adjudication.requires_human_review if uro.adjudication else "?",
        )
        return True

    except Exception as exc:
        logger.warning("_process_one failed for %s id=%s: %s", origin, source_id, exc)
        return False


async def _process_batch() -> int:
    """Fetch one batch of unprocessed flagged rows from both mcp_telemetry and
    system_telemetry, and process them all."""
    mcp_rows, system_rows = await asyncio.gather(
        asyncio.to_thread(_fetch_unprocessed, BATCH_SIZE),
        asyncio.to_thread(_fetch_unprocessed_system, BATCH_SIZE),
    )
    rows = mcp_rows + system_rows
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
async def telemetry_adjudicated(limit: int = 50, tier: str | None = None, offset: int = 0):
    """Adjudicated MCP governance events, optionally filtered by risk tier.
    total is the real row count behind this page (same tier filter, no
    limit/offset) — callers should show "showing len(rows) of total", not
    treat len(rows) as the whole population."""
    rows = await asyncio.to_thread(_fetch_adjudicated_rows, min(limit, 200), tier, max(0, offset))
    total = await asyncio.to_thread(_count_adjudicated_rows, tier)
    return {"rows": rows, "count": len(rows), "total": total, "offset": max(0, offset)}


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


def _fetch_coverage_samples(server_name: str, target_tool: str, kind: str, limit: int = 5) -> list[dict]:
    """Most recent raw calls for one (server_name, target_tool) coverage row —
    lets a reviewer eyeball a few real payloads instead of judging a blind
    spot from the flag_rate number alone."""
    if not db.is_available():
        return []
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                if kind == "mcp":
                    cur.execute(
                        """
                        SELECT ts, status, error_message, execution_time_ms, risk_flags
                        FROM observability.mcp_telemetry
                        WHERE direction = 'response' AND server_name = %s AND target_tool = %s
                        ORDER BY ts DESC LIMIT %s
                        """,
                        (server_name, target_tool, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT created_at AS ts, actor, resource, severity, event_type, risk_flags
                        FROM observability.system_telemetry
                        WHERE server_name = %s AND action = %s
                        ORDER BY created_at DESC LIMIT %s
                        """,
                        (server_name, target_tool, limit),
                    )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("ts") and hasattr(d["ts"], "isoformat"):
                        d["ts"] = d["ts"].isoformat()
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("_fetch_coverage_samples error: %s", exc)
        return []


def _find_rule_references(target_tool: str) -> list[str]:
    """Best-effort: which PaC processes' Rego content mentions this tool/action
    by name at all (live-saved module if one exists, else the built-in
    default — same fallback pac_approval_drift.py uses). A case-insensitive
    substring match, not a real Rego parse — good enough to distinguish 'no
    rule anywhere references this' from 'a rule exists but has never fired',
    which call for very different proposed resolutions."""
    if not target_tool:
        return []
    import pac_endpoints
    needle = target_tool.lower()
    processes = set(pac_endpoints._REGO_DEFAULTS.keys())
    if db.is_available():
        try:
            processes |= pac_endpoints._valid_processes()
        except Exception:
            pass
    matches = []
    for process in sorted(processes):
        content = None
        if db.is_available():
            saved = db.get_latest_pac_module(process)
            content = saved["rego_content"] if saved else None
        if content is None:
            content = pac_endpoints._REGO_DEFAULTS.get(process, "")
        if content and needle in content.lower():
            matches.append(process)
    return matches


@router.get("/coverage/detail")
async def coverage_detail(server_name: str, target_tool: str, kind: str = "mcp"):
    """
    Drill-down for one coverage row — recent sample calls plus a proposed
    resolution, so clicking a blind spot gives an operator enough to actually
    decide (author a rule / suppress / leave for now) instead of just a
    flag_rate number.
    """
    samples = await asyncio.to_thread(_fetch_coverage_samples, server_name, target_tool, kind)
    rule_matches = await asyncio.to_thread(_find_rule_references, target_tool)

    if rule_matches:
        proposed_resolution = (
            f"A policy rule already references '{target_tool}' by name in the "
            f"{'process' if len(rule_matches) == 1 else 'processes'} {', '.join(rule_matches)}, "
            "but it has never actually fired despite real calls. That can mean the "
            "activity genuinely never met the rule's condition — or that the rule is "
            "silently unreachable (referencing a field/event-type the pipeline never "
            "produces, the same failure class the PaC contract checker exists to catch). "
            f"Check the contract report for {rule_matches[0]} before assuming this is fine."
        )
    else:
        proposed_resolution = (
            f"No Policy-as-Code rule currently references '{target_tool}' by name in any "
            "process. Either author a rule for it in the Policy-as-Code Engine, or — if "
            "the activity is genuinely low-risk — use Suppress to record that as a "
            "reviewed, documented decision rather than leaving it an open question."
        )

    return {
        "server_name": server_name,
        "target_tool": target_tool,
        "kind": kind,
        "recent_samples": samples,
        "rule_references": rule_matches,
        "proposed_resolution": proposed_resolution,
    }


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
                        s.ingest_api_key, s.ingest_api_key_enc,
                        s.risk_tier, s.data_sensitivity, s.system_owner,
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
                    enc = d.pop("ingest_api_key_enc", None)
                    if enc:
                        # New-style: decrypt for display. A missing/rotated
                        # CONNECTOR_ENCRYPTION_KEY must not break the whole
                        # systems list — surface it per-row instead.
                        try:
                            d["ingest_api_key"] = db.decrypt_sensitive_json(enc).get("key")
                        except db.EncryptionKeyMissing:
                            d["ingest_api_key"] = None
                            d["ingest_api_key_error"] = "Cannot decrypt — CONNECTOR_ENCRYPTION_KEY missing or changed"
                    elif d.get("ingest_api_key"):
                        # Legacy: plaintext UUID column, for systems created
                        # before ingest_api_key_enc existed.
                        d["ingest_api_key"] = str(d["ingest_api_key"])
                    else:
                        d["ingest_api_key"] = None
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
    risk_tier: str | None = None,
    data_sensitivity: str | None = None,
    system_owner: str | None = None,
) -> int | None:
    """Register a system and issue it a fresh ingest API key, encrypted at
    rest (CONNECTOR_ENCRYPTION_KEY) rather than the plaintext UUID legacy
    systems used — a DB backup/leak no longer hands over a directly-usable
    bearer credential. ingest_api_key is left NULL for new rows (overriding
    its gen_random_uuid() column default) since ingest_api_key_enc is now
    the source of truth."""
    if not db.is_available():
        return None
    try:
        api_key = secrets.token_urlsafe(32)
        api_key_enc = db.encrypt_sensitive_json({"key": api_key})
    except db.EncryptionKeyMissing as exc:
        logger.warning("_create_system: cannot issue ingest API key — %s", exc)
        return None
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.monitored_systems
                        (display_name, server_name, server_type, description, active,
                         governance_tiers, blocking_tools, alert_webhook, created_by,
                         risk_tier, data_sensitivity, system_owner,
                         ingest_api_key, ingest_api_key_enc)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        (risk_tier or None),
                        (data_sensitivity or None),
                        (system_owner[:128] if system_owner else None),
                        None,          # ingest_api_key — explicitly NULL, overriding the column default
                        api_key_enc,
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
    risk_tier: str | None = None,
    data_sensitivity: str | None = None,
    system_owner: str | None = None,
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
                        risk_tier        = %s,
                        data_sensitivity = %s,
                        system_owner     = %s,
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
                        (risk_tier or None),
                        (data_sensitivity or None),
                        (system_owner[:128] if system_owner else None),
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


def _payload_keyword_hit(payload: dict, keywords: set[str]) -> bool:
    """True if some payload field signals one of `keywords` — a free-text
    STRING value containing the keyword (a producer with no fixed schema
    saying so in words), or a BOOLEAN field whose own NAME contains the
    keyword and whose value is True (a producer with a fixed schema, e.g.
    synthetic_transaction_tool.py's IAM steps setting
    {"sod_conflict_detected": True/False}).

    Deliberately does NOT substring-match against str(payload) wholesale —
    that would match a boolean field's NAME regardless of its value (every
    sod_conflict_detected row, true or false, contains the substring "sod"),
    flagging 100% of that field's occurrences instead of just the ones that
    are actually True."""
    for k, v in payload.items():
        if isinstance(v, bool):
            if v and any(kw in str(k).lower() for kw in keywords):
                return True
        elif isinstance(v, str) and any(kw in v.lower() for kw in keywords):
            return True
    return False


def _detect_system_flags(event: dict) -> list[str]:
    """Apply generic detection rules to a system telemetry event. Returns risk flag list."""
    flags: set[str] = set()
    action   = (event.get("action")   or "").lower()
    resource = (event.get("resource") or "").lower()
    severity = (event.get("severity") or "INFO").upper()
    payload  = event.get("payload") or {}

    if action in _PRIVILEGED_ACTIONS or any(k in resource for k in _PRIVILEGED_RESOURCE_KW):
        flags.add("privileged_access")
    if any(k in resource for k in _SENSITIVE_RESOURCE_KW):
        flags.add("sensitive_resource")
    if (payload.get("sod_violation") or _payload_keyword_hit(payload, _SOD_KW)
            or "sod" in (event.get("event_type") or "").lower()):
        flags.add("sod_violation")
    if severity == "CRITICAL" or payload.get("policy_violation"):
        flags.add("policy_violation")
    if payload.get("infrastructure_finding"):
        flags.add("infrastructure_finding")
    # Financial Risk Pipeline: explicit signals set by predictive_analytics_tool.py's
    # compute_je_velocity_anomaly/compute_liquidity_shift/compute_inventory_sales_divergence.
    if payload.get("je_velocity_anomaly"):
        flags.add("je_velocity_anomaly")
    if payload.get("liquidity_shift"):
        flags.add("liquidity_shift")
    if payload.get("inventory_divergence"):
        flags.add("inventory_divergence")
    # Hire-to-Retire: explicit signals set by oracle_hcm_tool.py's payroll
    # findings — same "producer knows exactly which event it's emitting"
    # pattern as the Financial Risk Pipeline flags above.
    if payload.get("ghost_employee_suspected"):
        flags.add("ghost_employee_suspected")
    if payload.get("unauthorized_pay_rate_change"):
        flags.add("unauthorized_pay_rate_change")
    if payload.get("terminated_employee_access_retained"):
        flags.add("terminated_employee_access_retained")
    # Treasury & Cash Management: explicit signals set by oracle_fusion_tool.py's
    # treasury checks.
    if payload.get("wire_transfer_single_approval"):
        flags.add("wire_transfer_single_approval")
    if payload.get("bank_recon_overdue"):
        flags.add("bank_recon_overdue")
    if payload.get("fx_hedge_documentation_missing"):
        flags.add("fx_hedge_documentation_missing")
    # Export Control / Trade Compliance: explicit signal set by
    # denied_party_screening_tool.py's CSL screening pass.
    if payload.get("export_control_match"):
        flags.add("export_control_match")
    # Continuous Third-Party/Vendor Risk: explicit signals set by
    # vendor_risk_sweep.py and oracle_fusion_tool.py's vendor checks.
    if payload.get("vendor_soc2_expired"):
        flags.add("vendor_soc2_expired")
    if payload.get("vendor_concentration_breach"):
        flags.add("vendor_concentration_breach")
    # AI Governance: explicit signals set by ai_governance_sweep.py and
    # ai_governance_endpoints.py.
    if payload.get("ai_assessment_overdue"):
        flags.add("ai_assessment_overdue")
    if payload.get("ai_human_oversight_missing"):
        flags.add("ai_human_oversight_missing")
    # Order-to-Cash / Procure-to-Pay: explicit signals set by
    # generate_o2c_p2p_synthetic_log.py (and, going forward, any real Oracle
    # Fusion O2C/P2P producer) — same "producer knows exactly which event
    # it's emitting" pattern as every flag above.
    if payload.get("revenue_recognition_event"):
        flags.add("revenue_recognition_event")
    if payload.get("sales_order_credit_event"):
        flags.add("sales_order_credit_event")
    if payload.get("billing_event"):
        flags.add("billing_event")
    if payload.get("cash_application_event"):
        flags.add("cash_application_event")
    if payload.get("customer_master_change"):
        flags.add("customer_master_change")
    if payload.get("ar_aging_event"):
        flags.add("ar_aging_event")
    if payload.get("purchase_order_event"):
        flags.add("purchase_order_event")
    if payload.get("invoice_match_event"):
        flags.add("invoice_match_event")
    if payload.get("vendor_master_change"):
        flags.add("vendor_master_change")
    if payload.get("payment_run_event"):
        flags.add("payment_run_event")
    if payload.get("procurement_sod_conflict"):
        flags.add("procurement_sod_conflict")
    # Inventory Cycle: explicit signals set by generate_o2c_p2p_synthetic_log.py's
    # third linked case (Receive -> Putaway -> Ship), same producer-driven
    # pattern as every flag above.
    if payload.get("goods_receipt_event"):
        flags.add("goods_receipt_event")
    if payload.get("inventory_putaway_event"):
        flags.add("inventory_putaway_event")
    if payload.get("goods_shipment_event"):
        flags.add("goods_shipment_event")
    return sorted(flags)


_SYSTEM_LOOKUP_COLS = "id, display_name, server_name, server_type, active, governance_tiers, alert_webhook"


def _get_system_by_api_key(api_key: str) -> dict | None:
    """Look up a monitored system by its ingest API key.

    Checks the encrypted column first — decrypt-and-constant-time-compare
    over the (small — single digits to low tens, same scale as
    db.get_poll_connector's own full-table-scan precedent) set of active
    systems that have a key. Falls back to the legacy plaintext UUID column
    only for systems created before ingest_api_key_enc existed; new systems
    never populate that column (see _create_system), so this fallback path
    shrinks to zero as systems get rotated onto the encrypted scheme.
    """
    if not db.is_available() or not api_key:
        return None
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_SYSTEM_LOOKUP_COLS}, ingest_api_key_enc
                    FROM observability.monitored_systems
                    WHERE active = TRUE AND ingest_api_key_enc IS NOT NULL
                    """
                )
                cols = [d[0] for d in cur.description]
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    enc = d.pop("ingest_api_key_enc")
                    try:
                        stored_key = db.decrypt_sensitive_json(enc).get("key", "")
                    except db.EncryptionKeyMissing:
                        # Can't decrypt this row (key missing/rotated) — skip
                        # it rather than fail the whole lookup; another row
                        # (or the legacy fallback below) may still match.
                        continue
                    if stored_key and hmac.compare_digest(stored_key, api_key):
                        return d

                # Legacy fallback: plaintext UUID column, only for systems
                # never migrated to ingest_api_key_enc. Guard the ::uuid cast
                # — a new-style token_urlsafe key isn't UUID-shaped and would
                # otherwise raise a cast error instead of just "no match".
                try:
                    _uuid.UUID(api_key)
                except ValueError:
                    return None
                cur.execute(
                    f"""
                    SELECT {_SYSTEM_LOOKUP_COLS}
                    FROM observability.monitored_systems
                    WHERE ingest_api_key = %s::uuid AND active = TRUE AND ingest_api_key_enc IS NULL
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


def _rotate_system_api_key(system_id: int) -> str | None:
    """Issue a fresh, encrypted ingest API key for an existing system,
    immediately invalidating whatever key it had before (legacy plaintext or
    a previous encrypted one) — the explicit migration path off the legacy
    ingest_api_key column for a system that predates ingest_api_key_enc.
    Returns the new plaintext key (shown once via the caller's response,
    same as it's shown on every /systems list load for encrypted rows)."""
    if not db.is_available():
        return None
    try:
        api_key = secrets.token_urlsafe(32)
        api_key_enc = db.encrypt_sensitive_json({"key": api_key})
    except db.EncryptionKeyMissing as exc:
        logger.warning("_rotate_system_api_key: cannot issue new key — %s", exc)
        return None
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.monitored_systems
                    SET ingest_api_key = NULL, ingest_api_key_enc = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (api_key_enc, system_id),
                )
                updated = cur.rowcount
            conn.commit()
        return api_key if updated > 0 else None
    except Exception as exc:
        logger.warning("_rotate_system_api_key error (id=%s): %s", system_id, exc)
        return None


# Sub-keys of raw_payload carrying data sensitive enough to encrypt at rest
# rather than leave as plain JSONB — compensation history (payroll_detail:
# prior/new pay rate) and wire-transfer/bank-reconciliation detail
# (treasury_detail). Encrypted transparently on the way in here and decrypted
# back to plaintext in _process_one before Bronze ever sees the row, so
# UBO/pipeline code stays entirely encryption-agnostic — see
# db.encrypt_sensitive_json/decrypt_sensitive_json.
_SENSITIVE_DETAIL_KEYS = ("payroll_detail", "treasury_detail")


def _encrypt_sensitive_details(raw_payload: dict) -> dict:
    """Replace any _SENSITIVE_DETAIL_KEYS sub-dict in raw_payload with an
    encrypted `<key>_enc` string. Falls back to storing the sub-dict as
    plaintext (with a warning) if CONNECTOR_ENCRYPTION_KEY isn't configured —
    a missing encryption key must not silently drop a HIGH-severity payroll/
    treasury finding from the audit trail."""
    result = dict(raw_payload)
    for key in _SENSITIVE_DETAIL_KEYS:
        detail = result.pop(key, None)
        if not detail:
            continue
        try:
            result[f"{key}_enc"] = db.encrypt_sensitive_json(detail)
        except Exception as exc:
            logger.warning(
                "Could not encrypt %s (storing as plaintext — set CONNECTOR_ENCRYPTION_KEY "
                "to enable at-rest encryption for this field): %s", key, exc,
            )
            result[key] = detail
    return result


def _decrypt_sensitive_details(raw_payload: dict) -> dict:
    """Inverse of _encrypt_sensitive_details() — restores plaintext
    payroll_detail/treasury_detail sub-dicts so bronze/silver see exactly the
    same shape they always have."""
    result = dict(raw_payload)
    for key in _SENSITIVE_DETAIL_KEYS:
        enc = result.pop(f"{key}_enc", None)
        if enc:
            try:
                result[key] = db.decrypt_sensitive_json(enc)
            except Exception as exc:
                logger.warning("Could not decrypt %s: %s", key, exc)
    return result


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
    created_at: "datetime | None" = None,
) -> int | None:
    """Insert a single event into system_telemetry. Returns new row id, or None
    if the insert was skipped as a duplicate (server_name, event_id) — a poll
    connector re-fetching an overlapping time window is expected to hit this.

    created_at defaults to now — every existing caller keeps getting exactly
    that. Only generate_o2c_p2p_synthetic_log.py passes an explicit
    backdated value, so a synthetic run can spread realistic-looking history
    across Control Flow Map's 7/30/90-day window instead of clustering
    everything at the moment the script ran."""
    if not db.is_available():
        return None
    try:
        import json as _json
        if created_at is None:
            created_at = datetime.now(timezone.utc)
        if raw_payload:
            raw_payload = _encrypt_sensitive_details(raw_payload)
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.system_telemetry
                        (server_name, system_type, event_type, event_id, actor,
                         action, resource, severity, risk_flags, raw_payload, source_ip, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (server_name, event_id) DO NOTHING
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
                        created_at,
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
    """All monitored systems registered with the Dendrai UBO Governance Brain."""
    rows = await asyncio.to_thread(_fetch_systems)
    return {"rows": rows, "count": len(rows)}


@router.post("/systems")
async def create_system(body: dict = Body(...)):
    """Register a new system for Dendrai UBO Governance Brain monitoring."""
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
        body.get("risk_tier"),
        body.get("data_sensitivity"),
        body.get("system_owner"),
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
        body.get("risk_tier"),
        body.get("data_sensitivity"),
        body.get("system_owner"),
    )
    return {"ok": ok, "id": system_id}


@router.delete("/systems/{system_id}")
async def delete_system(system_id: int):
    """Deactivate a monitored system (soft delete)."""
    ok = await asyncio.to_thread(_delete_system, system_id)
    return {"ok": ok, "id": system_id}


@router.post("/systems/{system_id}/rotate-key")
async def rotate_system_api_key(system_id: int):
    """Issue a fresh, encrypted ingest API key for this system, invalidating
    whatever it had before. The explicit migration path for a system still
    on the legacy plaintext ingest_api_key column — after this call it's on
    ingest_api_key_enc like any newly-created system. The caller must update
    the external system's configured Bearer token to the returned key."""
    new_key = await asyncio.to_thread(_rotate_system_api_key, system_id)
    if new_key is None:
        raise HTTPException(status_code=404, detail="System not found, or key rotation failed")
    return {"ok": True, "id": system_id, "ingest_api_key": new_key}


# ── Poll-based connectors CRUD ──────────────────────────────────────────────────
# The inverse of /systems above: /systems is push-model (external systems POST
# to us with an issued ingest_api_key); these are pull-model connectors (we
# poll them, holding their credentials — encrypted, see db.encrypt_credentials).
# Configured entirely from the app UI, no env vars — see connector_poller.py
# for the background dispatch loop that actually polls these.

# Connector types whose base_url points at a public SaaS API rather than a
# customer's own on-prem/VPN-internal system — only these would get the SSRF
# guard. Oracle Fusion/SAP HANA/etc. connectors are legitimately configured
# with private/internal addresses, so validating those would break real
# deployments. Empty today — the public-SaaS connector types that populated
# this set have been removed; add back here if a future connector type needs it.
_SSRF_GUARDED_CONNECTOR_TYPES: set[str] = set()


def _validate_connector_base_url(connector_type: str, base_url: Optional[str]) -> None:
    if not base_url or connector_type not in _SSRF_GUARDED_CONNECTOR_TYPES:
        return
    try:
        mcp_guards.validate_external_url(base_url, field="base_url")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/connectors")
async def list_connectors():
    """All poll-based connectors. Never includes credentials."""
    rows = await asyncio.to_thread(db.list_poll_connectors)
    return {"rows": rows, "count": len(rows)}


@router.post("/connectors")
async def create_connector(body: dict = Body(...)):
    """Register a new poll connector. Body includes plaintext credentials —
    encrypted before storage, never echoed back."""
    connector_type = str(body.get("connector_type") or "")[:32]
    _validate_connector_base_url(connector_type, body.get("base_url"))
    try:
        new_id = await asyncio.to_thread(
            db.create_poll_connector,
            connector_type,
            str(body.get("display_name") or "")[:128],
            body.get("base_url"),
            str(body.get("auth_type") or "")[:32],
            dict(body.get("credentials") or {}),
            body.get("extra_config"),
            int(body.get("poll_interval_s") or 1800),
            str(body.get("created_by") or "operator")[:128],
            body.get("risk_tier"),
            body.get("data_sensitivity"),
            body.get("system_owner"),
        )
    except db.EncryptionKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"ok": new_id is not None, "id": new_id}


@router.put("/connectors/{connector_id}")
async def update_connector(connector_id: int, body: dict = Body(...)):
    """Update a connector. Omit `credentials` to keep the existing encrypted value."""
    if body.get("base_url"):
        existing = await asyncio.to_thread(db.get_poll_connector, connector_id, False)
        if existing:
            _validate_connector_base_url(existing["connector_type"], body.get("base_url"))
    try:
        ok = await asyncio.to_thread(
            db.update_poll_connector,
            connector_id,
            display_name=body.get("display_name"),
            base_url=body.get("base_url"),
            auth_type=body.get("auth_type"),
            credentials=body.get("credentials"),
            extra_config=body.get("extra_config"),
            poll_interval_s=body.get("poll_interval_s"),
            active=body.get("active"),
            risk_tier=body.get("risk_tier"),
            data_sensitivity=body.get("data_sensitivity"),
            system_owner=body.get("system_owner"),
        )
    except db.EncryptionKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"ok": ok, "id": connector_id}


@router.delete("/connectors/{connector_id}")
async def delete_connector(connector_id: int):
    ok = await asyncio.to_thread(db.delete_poll_connector, connector_id)
    return {"ok": ok, "id": connector_id}


@router.post("/connectors/{connector_id}/test")
async def test_connector(connector_id: int):
    """Test a connector's credentials/connectivity without waiting for a full
    poll cycle. Returns {ok, message} — never raises on a failed test, only
    on a genuinely missing connector or a missing encryption key."""
    try:
        conn_row = await asyncio.to_thread(db.get_poll_connector, connector_id, True)
    except db.EncryptionKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not conn_row:
        raise HTTPException(status_code=404, detail=f"No connector with id {connector_id}")
    import connector_poller
    adapter = connector_poller._ADAPTERS.get(conn_row["connector_type"])
    if adapter is None:
        raise HTTPException(status_code=400, detail=f"Unknown connector_type '{conn_row['connector_type']}'")
    try:
        ok, message = await asyncio.to_thread(
            adapter.test_connection, conn_row["base_url"], conn_row["credentials"], conn_row["extra_config"] or {}
        )
    except Exception as exc:
        ok, message = False, f"{type(exc).__name__}: {exc}"
    return {"ok": ok, "message": message}


_RISK_TIERS = ("critical", "high", "medium", "low")
_DATA_SENSITIVITIES = ("pii", "financial", "confidential", "internal", "public")


def _update_system_classification(system_id: int, risk_tier: str | None, data_sensitivity: str | None,
                                    system_owner: str | None) -> bool:
    """Partial update of just the inventory classification fields on a
    monitored (push) system — unlike _update_system above, this never
    touches display_name/server_name/governance_tiers/etc., so the
    inventory screen's inline editor can't accidentally blank out a
    system's other configuration."""
    if not db.is_available():
        return False
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.monitored_systems
                    SET risk_tier = %s, data_sensitivity = %s, system_owner = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (risk_tier or None, data_sensitivity or None,
                     (system_owner[:128] if system_owner else None), system_id),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as exc:
        logger.warning("_update_system_classification error (id=%s): %s", system_id, exc)
        return False


@router.put("/systems/{system_id}/classification")
async def update_system_classification(system_id: int, body: dict = Body(...)):
    """Set only risk_tier / data_sensitivity / system_owner on a push system —
    used by the AI System Inventory screen's inline editor."""
    ok = await asyncio.to_thread(
        _update_system_classification, system_id,
        body.get("risk_tier"), body.get("data_sensitivity"), body.get("system_owner"),
    )
    return {"ok": ok, "id": system_id}


@router.put("/connectors/{connector_id}/classification")
async def update_connector_classification(connector_id: int, body: dict = Body(...)):
    """Set only risk_tier / data_sensitivity / system_owner on a poll
    connector — used by the AI System Inventory screen's inline editor."""
    ok = await asyncio.to_thread(
        db.update_poll_connector, connector_id,
        risk_tier=body.get("risk_tier"), data_sensitivity=body.get("data_sensitivity"),
        system_owner=body.get("system_owner"),
    )
    return {"ok": ok, "id": connector_id}


def _fetch_agent_calibration() -> dict:
    """
    Per-agent calibration: of the cases each Council member (or the LLM 4th
    opinion, or Policy-as-Code) voted ESCALATE on, what fraction did a human
    reviewer actually confirm? This is the evidence a skeptical AI-governance
    buyer will ask for — it turns "author-chosen confidence formula" into
    something empirically checkable, using human_verdict/ai_final_verdict
    (see _human_review_adjudication) rather than the old final_verdict-only
    trail that a review would have already overwritten.

    "APPROVE" (the UI's "confirm the AI's verdict" click) reads as agreement
    with ai_final_verdict — every other human_verdict value is compared
    directly against each agent's own vote.
    """
    if not db.is_available():
        return {"agents": [], "reviewed_count": 0}
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ai_final_verdict, human_verdict, council_votes
                    FROM observability.adjudicated_tool_calls
                    WHERE human_reviewed_at IS NOT NULL
                      AND human_verdict IS NOT NULL
                      AND council_votes IS NOT NULL
                    """
                )
                rows = cur.fetchall()
    except Exception as exc:
        logger.warning("_fetch_agent_calibration error: %s", exc)
        return {"agents": [], "reviewed_count": 0}

    stats: dict[str, dict[str, int]] = {}
    for ai_final_verdict, human_verdict, council_votes in rows:
        effective_human = ai_final_verdict if human_verdict == "APPROVE" else human_verdict
        for vote in (council_votes or []):
            name = vote.get("agent_name")
            verdict = vote.get("verdict")
            if not name or not verdict:
                continue
            s = stats.setdefault(name, {
                "total_votes": 0, "agreements": 0,
                "escalate_calls": 0, "escalate_confirmed": 0,
            })
            s["total_votes"] += 1
            if verdict == effective_human:
                s["agreements"] += 1
            if verdict == "ESCALATE":
                s["escalate_calls"] += 1
                if effective_human == "ESCALATE":
                    s["escalate_confirmed"] += 1

    agents = []
    for name, s in stats.items():
        agents.append({
            "agent_name": name,
            "total_votes": s["total_votes"],
            "overall_agreement_rate": round(s["agreements"] / s["total_votes"], 3) if s["total_votes"] else None,
            "escalate_calls": s["escalate_calls"],
            "escalate_confirmed": s["escalate_confirmed"],
            "escalate_confirmation_rate": (
                round(s["escalate_confirmed"] / s["escalate_calls"], 3) if s["escalate_calls"] else None
            ),
        })
    agents.sort(key=lambda a: a["total_votes"], reverse=True)
    return {"agents": agents, "reviewed_count": len(rows)}


@router.get("/agent-calibration")
async def agent_calibration():
    """See _fetch_agent_calibration."""
    return await asyncio.to_thread(_fetch_agent_calibration)


@router.get("/ai-inventory")
async def ai_inventory():
    """
    Unified AI/agent system register across both connection models —
    push-based monitored_systems and pull-based poll_connectors — each
    tagged with risk tier, data sensitivity, and owner. This is the
    inventory artifact NIST AI RMF's "Map" function and the EU AI Act's
    system register both start from: what AI-adjacent systems exist, what
    do they touch, how risky are they, who's accountable. Classification
    fields are edited via the existing PUT /systems/{id} and
    PUT /connectors/{id} endpoints (risk_tier/data_sensitivity/system_owner
    body fields) — this endpoint only reads and merges.
    """
    systems, connectors = await asyncio.gather(
        asyncio.to_thread(_fetch_systems),
        asyncio.to_thread(db.list_poll_connectors),
    )
    rows = []
    for s in systems:
        rows.append({
            "id": s["id"], "kind": "push", "display_name": s["display_name"],
            "type": s["server_type"], "active": s["active"],
            "risk_tier": s.get("risk_tier"), "data_sensitivity": s.get("data_sensitivity"),
            "system_owner": s.get("system_owner"),
            "last_activity": s.get("last_seen"), "total_calls": s.get("total_calls"),
            "flagged_calls": s.get("flagged_calls"),
        })
    for c in connectors:
        rows.append({
            "id": c["id"], "kind": "poll", "display_name": c["display_name"],
            "type": c["connector_type"], "active": c["active"],
            "risk_tier": c.get("risk_tier"), "data_sensitivity": c.get("data_sensitivity"),
            "system_owner": c.get("system_owner"),
            "last_activity": c.get("last_poll_at"), "total_calls": None,
            "flagged_calls": None,
        })
    def _tier_rank(r):
        tier = r["risk_tier"]
        return _RISK_TIERS.index(tier) if tier in _RISK_TIERS else len(_RISK_TIERS)
    rows.sort(key=lambda r: (_tier_rank(r), r["display_name"] or ""))
    untiered = sum(1 for r in rows if not r["risk_tier"])
    return {
        "rows": rows, "count": len(rows), "untiered_count": untiered,
        "risk_tiers": list(_RISK_TIERS), "data_sensitivities": list(_DATA_SENSITIVITIES),
    }


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
                           created_at, updated_at, created_by,
                           (token_enc IS NOT NULL) AS has_token,
                           last_synced_at, last_sync_status, last_sync_error
                    FROM observability.pac_repositories
                    ORDER BY active DESC, display_name ASC
                    """
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    for tf in ("created_at", "updated_at", "last_synced_at"):
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
    token: str | None = None,
) -> int | None:
    if not db.is_available():
        return None
    try:
        token_enc = db.encrypt_credentials({"token": token}) if token else None
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.pac_repositories
                        (display_name, provider, repo_url, branch, rego_path,
                         process, description, active, created_by, token_enc)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        token_enc,
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
    token: str | None = None,
) -> bool:
    if not db.is_available():
        return False
    try:
        # token is write-only and optional on update — omit it (None) to
        # keep whatever's already saved rather than clobbering it, matching
        # observability.poll_connectors' "send only if replacing" convention.
        sets = [
            "display_name = %s", "provider = %s", "repo_url = %s",
            "branch = %s", "rego_path = %s", "process = %s",
            "description = %s", "active = %s", "updated_at = NOW()",
        ]
        params: list = [
            display_name[:128], provider[:32], repo_url, branch[:128],
            rego_path[:256], process[:64], description or None, active,
        ]
        if token:
            sets.append("token_enc = %s")
            params.append(db.encrypt_credentials({"token": token}))
        params.append(repo_id)
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE observability.pac_repositories SET {', '.join(sets)} WHERE id = %s",
                    tuple(params),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as exc:
        logger.warning("_update_pac_repo error (id=%s): %s", repo_id, exc)
        return False


def _fetch_pac_repo_with_token(repo_id: int) -> tuple[dict, str | None] | None:
    """Load one repo row plus its decrypted token (if any) — used only by
    the sync endpoint, never by the general list/CRUD responses."""
    if not db.is_available():
        return None
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, display_name, provider, repo_url, branch,
                           rego_path, process, description, active, token_enc
                    FROM observability.pac_repositories
                    WHERE id = %s
                    """,
                    (repo_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        cols = ["id", "display_name", "provider", "repo_url", "branch",
                "rego_path", "process", "description", "active", "token_enc"]
        d = dict(zip(cols, row))
        token_enc = d.pop("token_enc")
        token = db.decrypt_credentials(token_enc).get("token") if token_enc else None
        return d, token
    except Exception as exc:
        logger.warning("_fetch_pac_repo_with_token error (id=%s): %s", repo_id, exc)
        return None


def _record_pac_repo_sync(repo_id: int, status: str, error: str | None = None) -> None:
    if not db.is_available():
        return
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE observability.pac_repositories
                    SET last_synced_at = NOW(), last_sync_status = %s, last_sync_error = %s
                    WHERE id = %s
                    """,
                    (status[:16], (error or None), repo_id),
                )
            conn.commit()
    except Exception as exc:
        logger.warning("_record_pac_repo_sync error (id=%s): %s", repo_id, exc)


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
        (str(body.get("token")).strip() or None) if body.get("token") else None,
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
        (str(body.get("token")).strip() or None) if body.get("token") else None,
    )
    return {"ok": ok, "id": repo_id}


@router.delete("/pac-repos/{repo_id}")
async def delete_pac_repo(repo_id: int):
    """Deactivate a PAC repository (soft delete)."""
    ok = await asyncio.to_thread(_delete_pac_repo, repo_id)
    return {"ok": ok, "id": repo_id}


@router.post("/pac-repos/{repo_id}/sync")
async def sync_pac_repo(repo_id: int):
    """
    Pull the latest policy files from a registered repository and import
    them as Policy-as-Code modules — the actual sync action behind the
    repository registry (list/CRUD only otherwise). Reuses the same
    GitHub-pull-and-import logic as the legacy single-hook sync
    (pac_endpoints._sync_github_repo), just sourced from this repo's own
    saved URL/branch/path/token instead of the one global hook.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    loaded = await asyncio.to_thread(_fetch_pac_repo_with_token, repo_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Repository not found")
    repo, token = loaded

    if repo["provider"] != "github":
        raise HTTPException(
            status_code=400,
            detail=f"Sync isn't implemented yet for '{repo['provider']}' — only GitHub repositories can be synced today.",
        )
    if not token:
        raise HTTPException(status_code=400, detail="No Personal Access Token saved for this repository — edit it and add one.")

    try:
        owner, repo_name = pac_endpoints._parse_github_repo(repo["repo_url"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    process_hint = repo.get("process") if repo.get("process") not in (None, "all") else None
    path_filter = (repo.get("rego_path") or "").strip().strip("/")

    try:
        result = await pac_endpoints._sync_github_repo(
            owner, repo_name, repo.get("branch") or "main", path_filter, token,
            process_hint=process_hint,
        )
    except HTTPException as exc:
        await asyncio.to_thread(_record_pac_repo_sync, repo_id, "error", str(exc.detail))
        raise
    except Exception as exc:
        await asyncio.to_thread(_record_pac_repo_sync, repo_id, "error", str(exc))
        raise HTTPException(status_code=502, detail=f"Sync failed: {exc}")

    await asyncio.to_thread(_record_pac_repo_sync, repo_id, "ok", None)
    return result
