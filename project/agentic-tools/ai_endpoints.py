#!/usr/bin/env python3
"""
AI-augmented endpoints for the Dendrai Risk Loop (recommendations #1–#4).

These are the first endpoints that actually put a language model in the loop.
They sit alongside the existing deterministic /predictive/* pipeline and degrade
to HTTP 503 when ANTHROPIC_API_KEY is absent, so MCP mode keeps working unchanged.

Router prefix: /ai  (plus /agent/investigate for the tool-use agent)

    POST /ai/gate1/recommend     #2  per-risk HITL disposition drafts
    POST /ai/gate2/recommend     #2  per-objective scope drafts
    POST /ai/narrative-analysis  #3  Item 1A / proxy narrative extraction
    POST /ai/persona-brief       #4  role-tailored summary (CAE / CFO / COO)
    POST /ai/audit-report        #4  full markdown audit report
    POST /agent/investigate      #1  tool-use investigation agent
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import claude_client
import db

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_ai():
    if not claude_client.is_available():
        raise HTTPException(
            status_code=503,
            detail="AI features disabled — set ANTHROPIC_API_KEY in project/agentic-tools/.env",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Request models
# ─────────────────────────────────────────────────────────────────────────────

class Gate1Request(BaseModel):
    ticker: str
    run_id: Optional[int] = None
    risks: List[Dict[str, Any]] = []
    context: Dict[str, Any] = {}


class Gate2Request(BaseModel):
    ticker: str
    run_id: Optional[int] = None
    objectives: List[Dict[str, Any]] = []
    risks: List[Dict[str, Any]] = []


class NarrativeRequest(BaseModel):
    ticker: str
    run_id: Optional[int] = None
    max_filings: int = 1
    include_proxy: bool = True


class PersonaRequest(BaseModel):
    ticker: str
    run_id: Optional[int] = None
    persona: str = "CAE"
    risks: List[Dict[str, Any]] = []
    loop_stats: Dict[str, Any] = {}


class ReportRequest(BaseModel):
    ticker: str
    run_id: Optional[int] = None
    risks: List[Dict[str, Any]] = []
    objectives: List[Dict[str, Any]] = []
    maps: List[Dict[str, Any]] = []
    loop: Dict[str, Any] = {}


class InvestigateRequest(BaseModel):
    ticker: str
    run_id: Optional[int] = None
    focus: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# #2 — AI-assisted HITL Gate 1 (per-risk dispositions)
# ─────────────────────────────────────────────────────────────────────────────

_GATE1_SYSTEM = """You are a senior internal-audit reviewer assisting a Chief Audit \
Executive at HITL Gate 1 of an AI-driven risk loop. For each scored risk you receive \
the model's score, RAG band, velocity, and control effectiveness, plus available \
evidence (financial ratios, Beneish M-score, the company's own Item 1A risk-factor \
excerpts, and recent industry signals).

For each risk, recommend a disposition the human auditor will accept or override:
- "approve" when the AI scoring is well-supported by the evidence, or
- "adjust" with concrete suggested values when the evidence warrants a different \
RAG / score / velocity / control rating.

Ground every recommendation in the supplied evidence. Cite the specific figure or \
disclosure. Never invent numbers — if evidence is thin, say so and lean toward \
"approve". Keep each rationale to 1–3 sentences; it is captured verbatim into the \
audit trail and routed to CAE → CFO → Audit Committee."""

_RAG_ENUM = ["R", "A", "G"]
_CE_ENUM = ["NONE", "WEAK", "ADEQUATE", "STRONG"]

_GATE1_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "risk_ref": {"type": "string"},
                    "recommendation": {"type": "string", "enum": ["approve", "adjust"]},
                    "suggested_rag": {"type": "string", "enum": _RAG_ENUM},
                    "suggested_score": {"type": "number"},
                    "suggested_velocity": {"type": "integer"},
                    "suggested_ce": {"type": "string", "enum": _CE_ENUM},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "rationale": {"type": "string"},
                },
                "required": ["risk_ref", "recommendation", "suggested_rag",
                             "suggested_score", "suggested_velocity", "suggested_ce",
                             "confidence", "rationale"],
            },
        },
    },
    "required": ["recommendations"],
}


@router.post("/ai/gate1/recommend")
def gate1_recommend(req: Gate1Request):
    _require_ai()
    risks_min = [
        {
            "risk_ref": r.get("id") or r.get("risk_ref"),
            "name": r.get("name"),
            "category": r.get("category"),
            "score": r.get("score"),
            "rag": r.get("rag"),
            "velocity": r.get("velocity"),
            "ce": r.get("ce"),
            "filing_snippet": (r.get("filingSnippet") or "")[:600] or None,
        }
        for r in req.risks
    ]
    user = (
        f"Company: {req.ticker}\n\n"
        f"Risks under review:\n{json.dumps(risks_min, indent=2, default=str)}\n\n"
        f"Available evidence:\n{json.dumps(req.context, indent=2, default=str)[:24_000]}\n\n"
        "Return per-risk dispositions."
    )
    try:
        result = claude_client.complete_json(
            _GATE1_SYSTEM, user, _GATE1_SCHEMA, label="gate1", effort="high",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI call failed: {exc}")

    db.save_ai_analysis(
        "gate1_recommendation", result,
        run_id=req.run_id, ticker=req.ticker, model=claude_client.MODEL, effort="high",
        summary=f"{len(result.get('recommendations', []))} risk dispositions",
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# #2 — AI-assisted HITL Gate 2 (per-objective scope drafts)
# ─────────────────────────────────────────────────────────────────────────────

_GATE2_SYSTEM = """You are a senior internal-audit planner assisting at HITL Gate 2, \
where audit objectives derived from the risk register are reviewed before the audit \
plan is committed. For each objective you receive its text, priority, sprint, budgeted \
hours, and linked risks, alongside the full scored risk register.

For each objective, recommend whether to "approve" the proposed scope or "adjust" it, \
with concrete suggested priority (P1–P3), sprint number, hours, linked risk refs, and \
expected residual-risk reduction (%). Justify sizing against the linked risks' scores \
and velocity. Keep each rationale to 1–3 sentences."""

_GATE2_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "obj_id": {"type": "string"},
                    "recommendation": {"type": "string", "enum": ["approve", "adjust"]},
                    "suggested_priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
                    "suggested_sprint": {"type": "integer"},
                    "suggested_hours": {"type": "integer"},
                    "suggested_linked_risks": {"type": "array", "items": {"type": "string"}},
                    "suggested_residual_reduction": {"type": "number"},
                    "rationale": {"type": "string"},
                },
                "required": ["obj_id", "recommendation", "suggested_priority",
                             "suggested_sprint", "suggested_hours",
                             "suggested_linked_risks", "suggested_residual_reduction",
                             "rationale"],
            },
        },
    },
    "required": ["recommendations"],
}


@router.post("/ai/gate2/recommend")
def gate2_recommend(req: Gate2Request):
    _require_ai()
    objs_min = [
        {
            "obj_id": o.get("id") or o.get("obj_id"),
            "objective": o.get("objective") or o.get("objective_text"),
            "priority": o.get("priority"),
            "sprint": o.get("sprint"),
            "hours": o.get("hours"),
            "linked_risk": o.get("linked_risk") or o.get("linked_risk_ref"),
        }
        for o in req.objectives
    ]
    risks_min = [
        {"risk_ref": r.get("id") or r.get("risk_ref"), "name": r.get("name"),
         "score": r.get("score"), "rag": r.get("rag"), "velocity": r.get("velocity")}
        for r in req.risks
    ]
    user = (
        f"Company: {req.ticker}\n\n"
        f"Audit objectives under review:\n{json.dumps(objs_min, indent=2, default=str)}\n\n"
        f"Scored risk register:\n{json.dumps(risks_min, indent=2, default=str)}\n\n"
        "Return per-objective scope recommendations."
    )
    try:
        result = claude_client.complete_json(
            _GATE2_SYSTEM, user, _GATE2_SCHEMA, label="gate2", effort="high",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI call failed: {exc}")

    db.save_ai_analysis(
        "gate2_recommendation", result,
        run_id=req.run_id, ticker=req.ticker, model=claude_client.MODEL, effort="high",
        summary=f"{len(result.get('recommendations', []))} objective scopes",
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# #3 — Item 1A / proxy narrative analysis
# ─────────────────────────────────────────────────────────────────────────────

_NARRATIVE_SYSTEM = """You are an internal-audit analyst specialising in SEC filing \
narratives. You are given Item 1A Risk Factors text (and optionally DEF 14A proxy \
governance sections) from a company's recent filings. Extract what a deterministic \
keyword pipeline cannot:

1. Emerging risks — newly disclosed or materially expanded risks.
2. Year-over-year language shifts — hedging, new litigation/going-concern wording, \
softened or hardened tone (only if multiple filings are supplied).
3. Map each salient risk onto the standard register categories: Financial Reporting, \
Revenue Concentration, Gross Margin, Supply Chain, Cybersecurity, Trade Compliance, \
Regulatory, ESG, R&D, CapEx, Operational, Macro.

Quote short phrases from the filing as evidence. Be precise and conservative — do not \
infer risks the text does not support."""

_NARRATIVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "emerging_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["title", "category", "evidence_quote", "severity"],
            },
        },
        "yoy_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "change": {"type": "string"},
                    "direction": {"type": "string", "enum": ["new", "expanded", "softened", "removed"]},
                },
                "required": ["change", "direction"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["emerging_risks", "yoy_changes", "summary"],
}


@router.post("/ai/narrative-analysis")
def narrative_analysis(req: NarrativeRequest):
    _require_ai()
    # Reuse the existing EDGAR fetchers — we already pay to download this text.
    from edgar_tool import (
        get_company_info, parse_filings, fetch_filing_text,
        extract_risk_factors, extract_proxy_sections,
    )
    try:
        meta, sub = get_company_info(req.ticker)
        filings = parse_filings(sub, {"10-K"})["10-K"][: max(1, min(req.max_filings, 2))]
        sections = []
        for f in filings:
            text = fetch_filing_text(meta["cik"], f)
            rf = extract_risk_factors(text) if text else ""
            sections.append({"filing_date": f["date"], "risk_factors": (rf or "")[:24_000]})
        proxy_text = ""
        if req.include_proxy:
            pf = parse_filings(sub, {"DEF 14A"}).get("DEF 14A", [])[:1]
            for f in pf:
                ptext = fetch_filing_text(meta["cik"], f)
                psec = extract_proxy_sections(ptext) if ptext else {}
                proxy_text = json.dumps({k: (v or "")[:4_000] for k, v in psec.items()})[:12_000]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"EDGAR fetch failed: {exc}")

    user = (
        f"Company: {meta.get('company_name')} ({req.ticker})\n\n"
        f"Item 1A Risk Factors by filing:\n{json.dumps(sections, indent=2, default=str)[:48_000]}\n\n"
        + (f"Proxy governance sections:\n{proxy_text}\n\n" if proxy_text else "")
        + "Extract emerging risks, YoY language shifts, and category mapping."
    )
    try:
        result = claude_client.complete_json(
            _NARRATIVE_SYSTEM, user, _NARRATIVE_SCHEMA, label="narrative",
            effort="high", max_tokens=10_000,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI call failed: {exc}")

    db.save_ai_analysis(
        "narrative_analysis", result,
        run_id=req.run_id, ticker=req.ticker, model=claude_client.MODEL, effort="high",
        summary=result.get("summary", "")[:500],
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# #4 — Persona brief (CAE / CFO / COO)
# ─────────────────────────────────────────────────────────────────────────────

_PERSONA_SYSTEM = """You write role-tailored executive briefings from a completed \
internal-audit risk loop. Given a target persona (CAE, CFO, or COO), the scored risk \
register, and loop statistics, write a brief that speaks to that role's priorities:
- CAE: assurance coverage, residual risk, sign-off exposure, audit plan adequacy.
- CFO: financial-statement risk, margin/liquidity exposure, disclosure and reporting.
- COO: operational, supply-chain, execution, and people risks.

Lead with the single most important thing for that role. Be specific and cite scores \
and RAG bands. Avoid generic filler."""

_PERSONA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["title", "body"],
            },
        },
        "callouts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "sections", "callouts"],
}


@router.post("/ai/persona-brief")
def persona_brief(req: PersonaRequest):
    _require_ai()
    persona = (req.persona or "CAE").upper()
    risks_min = [
        {"risk_ref": r.get("id") or r.get("risk_ref"), "name": r.get("name"),
         "category": r.get("category"), "score": r.get("score"),
         "rag": r.get("rag"), "velocity": r.get("velocity"), "ce": r.get("ce")}
        for r in req.risks
    ]
    user = (
        f"Persona: {persona}\nCompany: {req.ticker}\n\n"
        f"Risk register:\n{json.dumps(risks_min, indent=2, default=str)}\n\n"
        f"Loop statistics:\n{json.dumps(req.loop_stats, indent=2, default=str)}\n\n"
        f"Write the {persona} brief."
    )
    try:
        result = claude_client.complete_json(
            _PERSONA_SYSTEM, user, _PERSONA_SCHEMA, label="persona", effort="high",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI call failed: {exc}")

    db.save_ai_analysis(
        "persona_brief", result,
        run_id=req.run_id, ticker=req.ticker, subject_ref=persona,
        model=claude_client.MODEL, effort="high",
        summary=result.get("headline", "")[:500],
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# #4 — Full audit report (markdown)
# ─────────────────────────────────────────────────────────────────────────────

_REPORT_SYSTEM = """You are a Chief Audit Executive drafting a formal internal-audit \
report from a completed risk loop. Produce a clear, board-ready Markdown report with:
# Executive Summary, ## Risk Assessment, ## Audit Objectives & Scope, \
## Management Action Plans, ## Loop Calibration & Next Cycle.

Be precise, cite scores/RAG/velocity, and write in the measured voice of an audit \
report — findings, not marketing. Do not invent data beyond what is supplied."""


@router.post("/ai/audit-report")
def audit_report(req: ReportRequest):
    _require_ai()
    payload = {
        "ticker": req.ticker,
        "risks": req.risks,
        "objectives": req.objectives,
        "management_action_plans": req.maps,
        "loop": req.loop,
    }
    user = (
        f"Generate the audit report for {req.ticker} from this risk-loop output:\n\n"
        f"{json.dumps(payload, indent=2, default=str)[:60_000]}"
    )
    try:
        markdown = claude_client.complete_text(
            _REPORT_SYSTEM, user, label="report", effort="high", max_tokens=16_000,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI call failed: {exc}")

    db.save_ai_analysis(
        "audit_report", {"markdown": markdown},
        run_id=req.run_id, ticker=req.ticker, model=claude_client.MODEL, effort="high",
        summary=f"{len(markdown)} char report",
    )
    return {"ticker": req.ticker, "markdown": markdown}


# ─────────────────────────────────────────────────────────────────────────────
# #1 — Tool-use investigation agent
# ─────────────────────────────────────────────────────────────────────────────

_AGENT_SYSTEM = """You are an autonomous internal-audit investigation agent. Given a \
company ticker, investigate its risk posture the way a senior auditor would: start by \
pulling the financials, follow anomalies into the filings (8-Ks, Item 1A), benchmark \
against industry peers, and run the deterministic quant models for ground-truth \
numbers. Decide your own path — do not run every tool reflexively; call a tool only \
when the question in front of you needs it.

The quant models (Beneish M-score, financial ratios, templated risk scoring) are \
ground truth — cite their numbers, never recompute by hand. When you have enough to \
form a view, stop and write a concise investigation memo: the 3–5 most material risks, \
the evidence for each, and a recommended audit focus. Be specific and cite figures."""


@router.post("/agent/investigate")
def investigate(req: InvestigateRequest):
    _require_ai()
    import agent_tools
    focus = f"\n\nSpecific focus from the auditor: {req.focus}" if req.focus else ""
    user = (
        f"Investigate the risk posture of {req.ticker.upper()} and produce an "
        f"investigation memo.{focus}"
    )
    try:
        result = claude_client.run_tool_loop(
            _AGENT_SYSTEM, user, agent_tools.TOOLS, agent_tools.IMPLS,
            label="investigate", effort="high", max_tokens=10_000, max_iterations=14,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI agent failed: {exc}")

    db.save_ai_analysis(
        "agent_investigation",
        {"memo": result["final_text"], "tool_calls": result["tool_calls"],
         "iterations": result["iterations"], "stopped": result["stopped"]},
        run_id=req.run_id, ticker=req.ticker, model=claude_client.MODEL, effort="high",
        summary=f"{result['iterations']} iterations, {len(result['tool_calls'])} tool calls",
    )
    return result
