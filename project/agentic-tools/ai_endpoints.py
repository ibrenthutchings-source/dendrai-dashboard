#!/usr/bin/env python3
"""
AI-augmented endpoints for the Dendrai Risk Loop (recommendations #1–#5).

These are the first endpoints that actually put a language model in the loop.
They sit alongside the existing deterministic /predictive/* pipeline and degrade
to HTTP 503 when ANTHROPIC_API_KEY is absent, so MCP mode keeps working unchanged.

Router prefix: /ai  (plus /agent/investigate for the tool-use agent)

    POST /ai/gate1/recommend     #2  per-risk HITL disposition drafts
    POST /ai/gate2/recommend     #2  per-objective scope drafts
    POST /ai/narrative-analysis  #3  Item 1A / proxy narrative extraction
    POST /ai/persona-brief       #4  role-tailored summary (CAE / CFO / COO)
    POST /ai/audit-report        #4  full markdown audit report
    POST /ai/loop-calibrate      #4b loop calibration recommendations (Gate 3)
    POST /agent/investigate      #1  tool-use investigation agent
    POST /agent/schedule         #5  provision Managed Agent + scheduled deployment
    POST /agent/schedule/run-now #5  trigger an immediate deployment run
    GET  /agent/schedule/status  #5  list recent deployment runs
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
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
# #1b — Streaming investigation agent (SSE)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/agent/investigate/stream")
def investigate_stream(req: InvestigateRequest):
    """
    Server-Sent Events version of /agent/investigate.
    Emits one JSON event per tool call/result, then a final 'done' event.
    Frontend can render a live thinking trace instead of a blank spinner.
    """
    _require_ai()
    import agent_tools

    prior_context = ""
    if db.is_available():
        prior = db.get_prior_investigation(req.ticker)
        if prior:
            memo = (prior.get("content") or {}).get("memo", prior.get("summary", ""))
            if memo:
                prior_context = (
                    f"\n\n--- Prior cycle findings ({prior['created_at']}) ---\n"
                    f"{memo[:4000]}\n"
                    "--- End of prior findings ---\n\n"
                    "Compare the current state against these prior findings. "
                    "Note what has changed, worsened, or improved. "
                    "Escalate with ‼️ ESCALATION only if a risk has materially worsened."
                )

    focus = f"\n\nSpecific focus from the auditor: {req.focus}" if req.focus else ""
    user = (
        f"Investigate the risk posture of {req.ticker.upper()} and produce an "
        f"investigation memo.{prior_context}{focus}"
    )

    def _generate():
        all_tool_calls: list[dict] = []
        final_event: dict = {}
        for event in claude_client.run_tool_loop_streaming(
            _AGENT_SYSTEM, user, agent_tools.TOOLS, agent_tools.IMPLS,
            label="investigate_stream", effort="high", max_tokens=10_000, max_iterations=14,
        ):
            if event.get("type") == "tool_call":
                all_tool_calls.append({"tool": event["tool"], "input": event["input"], "is_error": False})
            if event.get("type") == "done":
                final_event = event
            yield f"data: {json.dumps(event, default=str)}\n\n"

        # Persist after the stream finishes
        if final_event:
            db.save_ai_analysis(
                "agent_investigation",
                {"memo": final_event.get("final_text", ""), "tool_calls": all_tool_calls,
                 "iterations": final_event.get("iterations", 0), "stopped": final_event.get("stopped", "")},
                run_id=req.run_id, ticker=req.ticker, model=claude_client.MODEL, effort="high",
                summary=f"{final_event.get('iterations', 0)} iterations (stream), {len(all_tool_calls)} tool calls",
            )

    return StreamingResponse(_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ─────────────────────────────────────────────────────────────────────────────
# #4b — Loop Calibration AI assist (Gate 3)
# ─────────────────────────────────────────────────────────────────────────────

_CALIBRATE_SYSTEM = """You are a Chief Audit Executive reviewing the completed risk loop \
cycle and recommending calibration adjustments for the next cycle. You receive:
- Loop statistics (risk reduction achieved, MAPs open/closed, HITL override rate)
- The final risk register (scores, RAG, velocity after all adjustments)
- The initial risk register (scores before the cycle)
- Lessons learned from this cycle

Produce calibration guidance the audit team can act on immediately:
1. What worked well this cycle (model accuracy, gate efficiency, risk coverage)
2. What to tune for the next cycle (velocity thresholds, appetite levels, scope focus)
3. Recommended re-run frequency based on the risk velocity profile
4. The top 3 risks to prioritize in the next cycle (cite scores and trends)
5. Any model drift indicators (high override rate, appetite threshold mismatches)

Be specific, cite numbers, and write in the measured voice of an audit review. \
Do not invent data beyond what is supplied."""

_CALIBRATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "what_worked": {"type": "array", "items": {"type": "string"}},
        "tune_for_next_cycle": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "area": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["area", "recommendation", "rationale"],
            },
        },
        "recommended_frequency": {
            "type": "string",
            "enum": ["weekly", "monthly", "quarterly", "semi-annual"],
        },
        "next_cycle_focus_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "risk_ref": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["risk_ref", "reason"],
            },
        },
        "drift_indicators": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["what_worked", "tune_for_next_cycle", "recommended_frequency",
                 "next_cycle_focus_risks", "drift_indicators", "summary"],
}


class CalibrateRequest(BaseModel):
    ticker: str
    run_id: Optional[int] = None
    loop_stats: Dict[str, Any] = {}
    risks_final: List[Dict[str, Any]] = []
    risks_initial: List[Dict[str, Any]] = []
    hitl_override_rate: float = 0.0
    lessons_learned: List[str] = []


@router.post("/ai/loop-calibrate")
def loop_calibrate(req: CalibrateRequest):
    """Gate 3 — AI-assisted loop calibration recommendations for the next cycle."""
    _require_ai()
    risk_delta = []
    for rf in req.risks_final:
        ri = next((r for r in req.risks_initial if r.get("id") == rf.get("id")), None)
        if ri:
            risk_delta.append({
                "risk_ref": rf.get("id"),
                "name": rf.get("name"),
                "initial_score": ri.get("score"),
                "final_score": rf.get("score"),
                "delta": round((rf.get("score") or 0) - (ri.get("score") or 0), 2),
                "rag": rf.get("rag"),
                "velocity": rf.get("velocity"),
                "ce": rf.get("ce"),
            })

    user = (
        f"Company: {req.ticker}\n\n"
        f"Loop statistics:\n{json.dumps(req.loop_stats, indent=2, default=str)}\n\n"
        f"HITL override rate: {req.hitl_override_rate:.0%}\n\n"
        f"Risk score delta (initial → final):\n{json.dumps(risk_delta, indent=2, default=str)}\n\n"
        f"Lessons learned:\n{json.dumps(req.lessons_learned, indent=2, default=str)}\n\n"
        "Produce loop calibration recommendations."
    )
    try:
        result = claude_client.complete_json(
            _CALIBRATE_SYSTEM, user, _CALIBRATE_SCHEMA, label="loop_calibrate", effort="high",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI call failed: {exc}")

    db.save_ai_analysis(
        "loop_calibration", result,
        run_id=req.run_id, ticker=req.ticker, model=claude_client.MODEL, effort="high",
        summary=result.get("summary", "")[:500],
    )
    return result


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

    # Cross-run memory: prepend the most recent prior investigation so the agent
    # can detect drift (new risks, changed metrics) without starting cold every cycle.
    prior_context = ""
    if db.is_available():
        prior = db.get_prior_investigation(req.ticker)
        if prior:
            memo = (prior.get("content") or {}).get("memo", prior.get("summary", ""))
            if memo:
                prior_context = (
                    f"\n\n--- Prior cycle findings ({prior['created_at']}) ---\n"
                    f"{memo[:4000]}\n"
                    "--- End of prior findings ---\n\n"
                    "Compare the current state against these prior findings. "
                    "Note what has changed, worsened, or improved. "
                    "Escalate with ‼️ ESCALATION only if a risk has materially worsened."
                )

    focus = f"\n\nSpecific focus from the auditor: {req.focus}" if req.focus else ""
    user = (
        f"Investigate the risk posture of {req.ticker.upper()} and produce an "
        f"investigation memo.{prior_context}{focus}"
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


# ─────────────────────────────────────────────────────────────────────────────
# #5 — Managed Agent scheduled deployment
# ─────────────────────────────────────────────────────────────────────────────

class ScheduleRequest(BaseModel):
    ticker: str
    cron: str = "0 8 * * 1"
    mcp_url: str = ""


class ScheduleRunNowRequest(BaseModel):
    ticker: str


@router.post("/agent/schedule")
def agent_schedule(req: ScheduleRequest):
    """Provision (or reuse) the Dendrai Managed Agent + Deployment for a ticker."""
    _require_ai()
    try:
        import managed_agent_setup
        import anthropic
        client = anthropic.Anthropic()
        env = managed_agent_setup.ensure_environment(client)
        import os
        if req.mcp_url:
            os.environ["DENDRAI_MCP_URL"] = req.mcp_url
        agent = managed_agent_setup.ensure_agent(client)
        deployment = managed_agent_setup.create_deployment(client, agent, env, req.ticker, req.cron)
        if deployment is None:
            # SDK pre-dates deployments; return the shell command instead.
            return {
                "status": "sdk_upgrade_required",
                "agent_id": agent.id,
                "message": "Upgrade the anthropic SDK to create deployments. See server logs for the curl command.",
            }
        return {
            "status": "ok",
            "deployment_id": deployment.id,
            "agent_id": agent.id,
            "cron": req.cron,
            "ticker": req.ticker,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Managed Agent setup failed: {exc}")


@router.post("/agent/schedule/run-now")
def agent_schedule_run_now(req: ScheduleRunNowRequest):
    """Trigger an immediate run of the existing scheduled deployment."""
    _require_ai()
    try:
        import managed_agent_setup
        import anthropic
        client = anthropic.Anthropic()
        if not hasattr(client.beta, "deployments"):
            raise HTTPException(status_code=400, detail="SDK has no deployments support; upgrade anthropic.")
        deps = list(client.beta.deployments.list()) if hasattr(client.beta.deployments, "list") else []
        target = managed_agent_setup._find_by_name(deps, f"Dendrai {req.ticker} risk loop")
        if not target:
            raise HTTPException(status_code=404, detail=f"No deployment found for {req.ticker}. Call /agent/schedule first.")
        run = client.beta.deployments.run(target.id)
        sid = getattr(run, "session_id", None)
        return {"status": "ok", "session_id": sid, "deployment_id": target.id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Run trigger failed: {exc}")


@router.get("/agent/schedule/status/{ticker}")
def agent_schedule_status(ticker: str):
    """List recent scheduled runs for a ticker's deployment."""
    _require_ai()
    try:
        import managed_agent_setup
        import anthropic
        client = anthropic.Anthropic()
        if not hasattr(client.beta, "deployments"):
            return {"status": "sdk_upgrade_required", "runs": []}
        deps = list(client.beta.deployments.list()) if hasattr(client.beta.deployments, "list") else []
        target = managed_agent_setup._find_by_name(deps, f"Dendrai {ticker.upper()} risk loop")
        if not target:
            return {"status": "not_provisioned", "runs": []}
        runs = []
        for run in client.beta.deployment_runs.list(deployment_id=target.id):
            runs.append({
                "session_id": getattr(run, "session_id", None),
                "created_at": str(getattr(run, "created_at", "")),
                "error": str(getattr(run, "error", "")) or None,
            })
        return {
            "status": "ok",
            "deployment_id": target.id,
            "deployment_status": str(getattr(target, "status", "")),
            "runs": runs[:10],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Status check failed: {exc}")
