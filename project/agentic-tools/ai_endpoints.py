#!/usr/bin/env python3
"""
AI-augmented endpoints for Dendrai Intelligenza (recommendations #1–#5).

These are the first endpoints that actually put a language model in the loop.
They sit alongside the existing deterministic /predictive/* pipeline and degrade
to HTTP 503 when ANTHROPIC_API_KEY is absent, so MCP mode keeps working unchanged.

Router prefix: /ai  (plus /agent/investigate for the tool-use agent)

    POST /ai/gate1/recommend     #2  per-risk HITL disposition drafts
    POST /ai/gate2/recommend     #2  per-objective scope drafts
    POST /ai/approval/recommend  #2b manager review-assist for a submitted Approval Inbox item
    POST /ai/pac/draft-rego      #1b draft a Rego module from a policy narrative
    POST /ai/narrative-analysis  #3  Item 1A / proxy narrative extraction
    POST /ai/persona-brief       #4  role-tailored summary (CAE / CFO / COO)
    POST /ai/audit-report        #4  full markdown audit report
    POST /ai/loop-calibrate      #4b loop calibration recommendations (Gate 3)
    GET  /ai/review-queue        sampled ungated-narrative generations awaiting human spot-check
    POST /ai/review-queue/{id}/review  mark a sampled generation as reviewed
    POST /agent/investigate      #1  tool-use investigation agent
    POST /agent/investigate/council  #1b 3-perspective (financial/cyber/compliance) ensemble + synthesis
    POST /agent/schedule         #5  provision Managed Agent + scheduled deployment
    POST /agent/schedule/run-now #5  trigger an immediate deployment run
    GET  /agent/schedule/status  #5  list recent deployment runs
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import claude_client
import db
from auth_endpoints import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

# Model routing: Sonnet for all tasks; Opus reserved for absolute necessity.
_MODEL_STRUCTURED = "claude-sonnet-4-6"
_MODEL_AGENT = "claude-sonnet-4-6"

# Sampling-based human review for the two fully-automated, ungated narrative
# endpoints (persona_brief, audit_report) — MODEL_CARD.md "Recommended Next
# Steps" #4. Every other AI endpoint already has a human gate before its
# output takes effect; these two reach an executive/the board with none, so
# a random spot-check sample gets queued for after-the-fact human review
# instead. Stateless (no per-kind counter query needed) at the cost of exact
# cadence — over any reasonable volume this converges to ~1-in-5.
_UNGATED_REVIEW_SAMPLE_RATE = 0.20


def _should_sample_for_review() -> bool:
    return random.random() < _UNGATED_REVIEW_SAMPLE_RATE

# ── Embedding helpers ─────────────────────────────────────────────────────────
# Used by narrative_analysis to chunk and index EDGAR text so that future calls
# can retrieve relevant snippets from pgvector instead of re-sending entire docs.

_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    try:
        import openai  # optional dependency; pip install openai
        key = os.environ.get("OPENAI_API_KEY", "")
        if key:
            _openai_client = openai.OpenAI(api_key=key)
    except ImportError:
        pass
    return _openai_client


def _embed_text(text: str) -> "Optional[list]":
    """Return a text-embedding-3-small vector, or None when OpenAI is unavailable."""
    client = _get_openai()
    if client is None:
        return None
    try:
        resp = client.embeddings.create(model="text-embedding-3-small", input=text[:8191])
        return resp.data[0].embedding
    except Exception as exc:
        logger.warning("embedding failed: %s", exc)
        return None


def _chunk_text(text: str, chunk_chars: int = 600, overlap: int = 80) -> "list[str]":
    """Split text into overlapping chunks, breaking at paragraph/sentence boundaries."""
    if not text:
        return []
    chunks: list = []
    start = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        if end < len(text):
            for sep in ("\n\n", "\n", ". ", " "):
                pos = text.rfind(sep, start + chunk_chars // 2, end)
                if pos != -1:
                    end = pos + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
    return chunks


def _embed_risk_factors(ticker: str, analysis_id: "Optional[int]", rf_texts: list) -> None:
    """Chunk and embed EDGAR risk factor text. Best-effort — never raises."""
    if not analysis_id or not db.is_available():
        return
    company_id = db.get_company_id(ticker)
    rows: list = []
    chunk_idx = 0
    for rf_text in rf_texts:
        for chunk in _chunk_text(rf_text):
            vec = _embed_text(chunk)
            if vec:
                rows.append({
                    "source_table": "ai_analyses",
                    "source_id": analysis_id,
                    "content_type": db.EMBT_RISK_FACTOR,
                    "model": "text-embedding-3-small",
                    "chunk_index": chunk_idx,
                    "company_id": company_id,
                    "embedding": vec,
                    "text_snippet": chunk[:600],
                })
            chunk_idx += 1
    if rows:
        saved = db.save_embeddings_bulk(rows)
        logger.info("saved %d risk factor embeddings for %s", saved, ticker)


def _require_ai():
    if not claude_client.is_available():
        raise HTTPException(
            status_code=503,
            detail="AI features disabled — set ANTHROPIC_API_KEY in project/agentic-tools/.env",
        )


def _ai_exc(exc: Exception) -> HTTPException:
    """Convert an Anthropic SDK exception into an appropriate HTTPException.

    Billing / credit errors come back as HTTP 400 from the Anthropic API and
    contain the phrase 'credit balance is too low'.  Return a 402 so the UI
    can display a targeted, actionable message instead of the raw SDK dump.
    """
    msg = str(exc)
    if "credit balance is too low" in msg or "insufficient_quota" in msg:
        return HTTPException(
            status_code=402,
            detail="Anthropic API credits exhausted. Add credits at console.anthropic.com/settings/billing, then retry.",
        )
    return HTTPException(status_code=502, detail=f"AI call failed: {exc}")


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
def gate1_recommend(req: Gate1Request, current_user: dict = Depends(get_current_user)):
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
            _GATE1_SYSTEM, user, _GATE1_SCHEMA, label="gate1",
            model=_MODEL_STRUCTURED, effort="medium", max_tokens=4000,
            caller=current_user,
        )
    except Exception as exc:
        raise _ai_exc(exc)

    db.save_ai_analysis(
        "gate1_recommendation", result,
        run_id=req.run_id, ticker=req.ticker, model=_MODEL_STRUCTURED, effort="medium",
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
def gate2_recommend(req: Gate2Request, current_user: dict = Depends(get_current_user)):
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
            _GATE2_SYSTEM, user, _GATE2_SCHEMA, label="gate2",
            model=_MODEL_STRUCTURED, effort="medium", max_tokens=4000,
            caller=current_user,
        )
    except Exception as exc:
        raise _ai_exc(exc)

    db.save_ai_analysis(
        "gate2_recommendation", result,
        run_id=req.run_id, ticker=req.ticker, model=_MODEL_STRUCTURED, effort="medium",
        summary=f"{len(result.get('recommendations', []))} objective scopes",
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# #2b — AI-assisted manager review (Approval Inbox second-line review)
# ─────────────────────────────────────────────────────────────────────────────

_APPROVAL_REVIEW_SYSTEM = """You are assisting a manager who is second-line \
reviewing a preparer's HITL gate override before it is finalized. You receive the \
gate type, the item under review, the preparer's proposed field changes, and the \
preparer's written rationale.

Recommend "approved" or "rejected" and explain why in 2-4 sentences, grounded \
specifically in whether the rationale actually justifies the proposed changes. Flag \
rationales that are vague, boilerplate, or don't address the specific fields being \
changed — that is exactly the kind of override this review step exists to catch. Do \
not rubber-stamp: only recommend "approved" when the rationale genuinely supports \
the change."""

_APPROVAL_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recommendation": {"type": "string", "enum": ["approved", "rejected"]},
        "confidence":      {"type": "string", "enum": ["low", "medium", "high"]},
        "reasoning":       {"type": "string"},
    },
    "required": ["recommendation", "confidence", "reasoning"],
}


class ApprovalReviewRequest(BaseModel):
    task_id: int


@router.post("/ai/approval/recommend")
def approval_review_recommend(req: ApprovalReviewRequest, current_user: dict = Depends(get_current_user)):
    """
    AI-drafted approve/reject recommendation for a manager reviewing a submitted
    HITL gate override in the Approval Inbox. Purely advisory — the manager still
    has to click Approve/Reject themselves; this never auto-decides.
    """
    _require_ai()
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    task = db.get_approval_task(req.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Approval task not found")
    if task.get("manager_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="You are not the assigned reviewer for this item")
    if task.get("status") != "submitted":
        raise HTTPException(status_code=409, detail=f"Item is not awaiting review (status: {task.get('status')})")

    user = (
        f"Gate type: {task.get('gate_type')}\n"
        f"Item: {task.get('item_label') or task.get('item_ref')}\n"
        f"Preparer: {task.get('prepared_by_name')}\n\n"
        f"Proposed changes:\n{json.dumps(task.get('adjustments') or {}, indent=2, default=str)}\n\n"
        f"Preparer's rationale:\n{task.get('rationale') or '(none provided)'}\n\n"
        "Recommend approve or reject."
    )
    try:
        result = claude_client.complete_json(
            _APPROVAL_REVIEW_SYSTEM, user, _APPROVAL_REVIEW_SCHEMA, label="approval_review",
            model=_MODEL_STRUCTURED, effort="medium", max_tokens=1200,
            caller=current_user,
        )
    except Exception as exc:
        raise _ai_exc(exc)

    db.save_ai_analysis(
        "approval_review_recommendation", result,
        subject_ref=str(req.task_id), model=_MODEL_STRUCTURED, effort="medium",
        summary=f"{result.get('recommendation')} ({result.get('confidence')})",
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# #1b — AI-drafted Rego from a policy narrative (Policy-as-Code screen)
# ─────────────────────────────────────────────────────────────────────────────

_DRAFT_REGO_SYSTEM = """You are an OPA/Rego policy engineer for the Dendrai \
Policy-as-Code screen. You are given a plain-language internal-controls policy \
narrative (often pulled directly from a GitHub policy-docs repo via the "Sync Now" \
feature) for one business process, and must draft an actual enforceable Rego module \
implementing it.

Follow the house style used by every other module in this system:
  package controls.oracle_fusion.<process>
  import future.keywords.in
  import future.keywords.if

  deny_<category>_event[msg] if {
      input.event.type == "<event_type>"
      not input.event.<some_condition>
      msg := sprintf("<CONTROL-ID>: <human-readable violation>", [input.event.<field>])
  }

Derive one or more deny_*_event rules per control described in the narrative. Invent \
control IDs in the pattern used by the narrative if none are given (e.g. AC-01, \
P2P-03). Add a header comment block (package, process, version 1.0, source note, \
last-revised date) matching the existing modules' style. Output ONLY the Rego \
source — no markdown fences, no prose before or after."""


class DraftRegoRequest(BaseModel):
    process: str
    narrative: str


@router.post("/ai/pac/draft-rego")
def draft_rego(req: DraftRegoRequest, current_user: dict = Depends(get_current_user)):
    """
    Convert a plain-language policy narrative (e.g. pulled in via the Policy-as-
    Code GitHub 'Sync Now' button) into an actual Rego module. The result only
    replaces the editor's draft — nothing is persisted until the user clicks
    Save, same as every other AI-assist feature in this app.
    """
    _require_ai()
    if not req.narrative.strip():
        raise HTTPException(status_code=422, detail="narrative must not be empty")

    user = (
        f"Process: {req.process}\n\n"
        f"Policy narrative to convert:\n{req.narrative[:12000]}\n\n"
        "Draft the Rego module now."
    )
    try:
        text = claude_client.complete_text(
            _DRAFT_REGO_SYSTEM, user, label="pac_draft_rego",
            model=_MODEL_STRUCTURED, effort="medium", max_tokens=4000,
            caller=current_user,
        )
    except Exception as exc:
        raise _ai_exc(exc)

    # Models sometimes wrap output in a ```rego fence despite instructions not to.
    rego_content = text.strip()
    if rego_content.startswith("```"):
        rego_content = re.sub(r"^```[a-zA-Z]*\n", "", rego_content)
        rego_content = re.sub(r"\n```$", "", rego_content)

    db.save_ai_analysis(
        "pac_rego_draft", {"rego_content": rego_content},
        subject_ref=req.process, model=_MODEL_STRUCTURED, effort="medium",
        summary=f"Drafted Rego for process '{req.process}'",
    )
    return {"rego_content": rego_content}


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
def narrative_analysis(req: NarrativeRequest, current_user: dict = Depends(get_current_user)):
    _require_ai()

    # ── 1. Cache check — serve a recent result without re-fetching EDGAR ────────
    # Narrative analysis is stable within a filing cycle (10-K is annual).
    # Re-running within 30 days costs ~50k input tokens for no new information.
    if db.is_available():
        cached = db.get_latest_ai_analysis(req.ticker, "narrative_analysis", max_age_days=30)
        if cached:
            logger.info("narrative_analysis: returning cached result for %s", req.ticker)
            result = dict(cached["content"] or {})
            result["_cached"] = True
            result["_cached_at"] = cached["created_at"]
            return result

    # ── 2. Fetch from EDGAR ──────────────────────────────────────────────────────
    from edgar_tool import (
        get_company_info, parse_filings, fetch_filing_text,
        extract_risk_factors, extract_proxy_sections,
    )
    try:
        meta, sub = get_company_info(req.ticker)
        filings = parse_filings(sub, {"10-K"})["10-K"][: max(1, min(req.max_filings, 2))]
        sections = []
        _raw_rf_texts: list = []  # full text kept for embedding (not truncated)
        for f in filings:
            text = fetch_filing_text(meta["cik"], f)
            rf = extract_risk_factors(text) if text else ""
            _raw_rf_texts.append(rf or "")
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

    # ── 3. Run the LLM ───────────────────────────────────────────────────────────
    user = (
        f"Company: {meta.get('company_name')} ({req.ticker})\n\n"
        f"Item 1A Risk Factors by filing:\n{json.dumps(sections, indent=2, default=str)[:48_000]}\n\n"
        + (f"Proxy governance sections:\n{proxy_text}\n\n" if proxy_text else "")
        + "Extract emerging risks, YoY language shifts, and category mapping."
    )
    try:
        result = claude_client.complete_json(
            _NARRATIVE_SYSTEM, user, _NARRATIVE_SCHEMA, label="narrative",
            model=_MODEL_STRUCTURED, effort="high", max_tokens=6000,
            caller=current_user,
        )
    except Exception as exc:
        raise _ai_exc(exc)

    analysis_id = db.save_ai_analysis(
        "narrative_analysis", result,
        run_id=req.run_id, ticker=req.ticker, model=_MODEL_STRUCTURED, effort="high",
        summary=result.get("summary", "")[:500],
    )

    # ── 4. Chunk + embed the raw risk factor text (best-effort) ─────────────────
    # Future calls to get_relevant_context() can then serve per-risk snippets
    # (~750 tokens) instead of re-sending the full 48k-char document.
    _embed_risk_factors(req.ticker, analysis_id, _raw_rf_texts)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# #4 — Persona brief (CAE / CFO / COO)
# ─────────────────────────────────────────────────────────────────────────────

_PERSONA_SYSTEM = """You write role-tailored executive briefings from a completed \
internal-audit risk loop. Given a target persona, the scored risk register, and loop \
statistics, write a brief that speaks to that audience's priorities and vocabulary:

- CAE: assurance coverage, residual risk, sign-off exposure, audit plan adequacy.
- CFO: financial-statement risk, margin/liquidity exposure, disclosure and reporting.
- COO: operational, supply-chain, execution, and people risks.
- TECH_EXEC (Technical Executive — CTO / CIO / CISO): technology, cyber, data, and \
IT-control risk; be specific about the control environment (automated/policy-enforced \
vs. manual controls), system resilience, and technical remediation feasibility. \
Technical vocabulary is appropriate for this audience.
- NONTECH_EXEC (Non-Technical Executive — CFO / COO / CEO): translate risk into \
business, financial, and operational impact — no technical jargon. Focus on what \
decision or resource ask this creates for leadership.
- BOARD (Board / Audit Committee): governance framing only — top risks relative to \
risk appetite, trend vs. the prior cycle (better/worse/unchanged), and whether \
management's response looks adequate. Assume the reader has 90 seconds: keep the \
whole brief under ~200 words and limit it to 2-3 short sections.

Lead with the single most important thing for that audience. Be specific and cite \
scores and RAG bands. Avoid generic filler."""

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
def persona_brief(req: PersonaRequest, current_user: dict = Depends(get_current_user)):
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

    # Cost reduction: hash the exact inputs and check for a prior identical
    # generation before spending a Claude call. Re-opening the same persona
    # brief on the same run with nothing changed (the common "just looking
    # again" case) is then free. Any real change to risks/loop_stats changes
    # the hash and falls through to a fresh call, same as before.
    input_hash = hashlib.sha256(user.encode("utf-8")).hexdigest()[:32]
    cached = db.get_cached_ai_analysis("persona_brief", req.run_id, persona, input_hash)
    if cached is not None:
        return cached

    try:
        result = claude_client.complete_json(
            _PERSONA_SYSTEM, user, _PERSONA_SCHEMA, label="persona",
            model=_MODEL_STRUCTURED, effort="medium", max_tokens=4000,
            caller=current_user,
        )
    except Exception as exc:
        raise _ai_exc(exc)

    db.save_ai_analysis(
        "persona_brief", result,
        run_id=req.run_id, ticker=req.ticker, subject_ref=persona,
        model=_MODEL_STRUCTURED, effort="medium",
        summary=result.get("headline", "")[:500],
        sampled_for_review=_should_sample_for_review(),
        input_hash=input_hash,
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
def audit_report(req: ReportRequest, current_user: dict = Depends(get_current_user)):
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

    # Same cache as persona_brief — this is the more expensive of the two
    # (effort="high", 10k max_tokens), so a re-open-with-nothing-changed
    # cache hit here is worth the most.
    input_hash = hashlib.sha256(user.encode("utf-8")).hexdigest()[:32]
    cached = db.get_cached_ai_analysis("audit_report", req.run_id, None, input_hash)
    if cached is not None:
        return {"ticker": req.ticker, "markdown": cached.get("markdown", "")}

    try:
        markdown = claude_client.complete_text(
            _REPORT_SYSTEM, user, label="report",
            model=_MODEL_STRUCTURED, effort="high", max_tokens=10_000,
            caller=current_user,
        )
    except Exception as exc:
        raise _ai_exc(exc)

    db.save_ai_analysis(
        "audit_report", {"markdown": markdown},
        run_id=req.run_id, ticker=req.ticker, model=_MODEL_STRUCTURED, effort="high",
        summary=f"{len(markdown)} char report",
        sampled_for_review=_should_sample_for_review(),
        input_hash=input_hash,
    )
    return {"ticker": req.ticker, "markdown": markdown}


# ─────────────────────────────────────────────────────────────────────────────
# Ungated-narrative review queue (MODEL_CARD.md "Recommended Next Steps" #4)
# ─────────────────────────────────────────────────────────────────────────────

class ReviewDecisionRequest(BaseModel):
    note: Optional[str] = None


@router.get("/ai/review-queue")
def get_review_queue(
    status: Optional[str] = "pending",
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    """Sampled persona_brief/audit_report generations awaiting (or having
    received) human spot-check review. status='pending'|'reviewed'|None (all)."""
    if not db.is_available():
        return {"items": [], "count": 0}
    items = db.list_ai_review_queue(status=status, limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/ai/review-queue/{analysis_id}/review")
def review_sampled_analysis(
    analysis_id: int,
    req: ReviewDecisionRequest,
    current_user: dict = Depends(get_current_user),
):
    """Mark a sampled ungated-narrative generation as human-reviewed."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    ok = db.mark_ai_analysis_reviewed(
        analysis_id, current_user["id"],
        current_user.get("display_name") or current_user.get("username", ""),
        note=req.note,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Analysis not found or not sampled for review")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# #1b — Streaming investigation agent (SSE)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/agent/investigate/stream")
def investigate_stream(req: InvestigateRequest, current_user: dict = Depends(get_current_user)):
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
            label="investigate_stream", model=_MODEL_AGENT, effort="high",
            max_tokens=10_000, max_iterations=14, caller=current_user,
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
                run_id=req.run_id, ticker=req.ticker, model=_MODEL_AGENT, effort="high",
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
def loop_calibrate(req: CalibrateRequest, current_user: dict = Depends(get_current_user)):
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
            _CALIBRATE_SYSTEM, user, _CALIBRATE_SCHEMA, label="loop_calibrate",
            model=_MODEL_STRUCTURED, effort="medium", max_tokens=4000,
            caller=current_user,
        )
    except Exception as exc:
        raise _ai_exc(exc)

    db.save_ai_analysis(
        "loop_calibration", result,
        run_id=req.run_id, ticker=req.ticker, model=_MODEL_STRUCTURED, effort="medium",
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
def investigate(req: InvestigateRequest, current_user: dict = Depends(get_current_user)):
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
            label="investigate", model=_MODEL_AGENT, effort="high",
            max_tokens=10_000, max_iterations=14, caller=current_user,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI agent failed: {exc}")

    db.save_ai_analysis(
        "agent_investigation",
        {"memo": result["final_text"], "tool_calls": result["tool_calls"],
         "iterations": result["iterations"], "stopped": result["stopped"]},
        run_id=req.run_id, ticker=req.ticker, model=_MODEL_AGENT, effort="high",
        summary=f"{result['iterations']} iterations, {len(result['tool_calls'])} tool calls",
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# #1b — Council investigation: the same multi-perspective ensemble pattern
# mcp_governance.py's adjudication ensemble already uses (independent voters,
# then reconciliation) applied to /agent/investigate. Three specialized
# angles run in parallel over the SAME tool access (agent_tools.TOOLS) —
# they differ by system-prompt lens, not by what data they can reach — then
# a lightweight synthesis pass surfaces where they agree (higher-confidence,
# triangulated findings) vs. where only one angle flagged something (still
# worth noting, just not cross-validated). Deliberately does NOT let one
# perspective override another — same "show disagreement, don't hide it"
# principle as the Council's own reconciliation.
# ─────────────────────────────────────────────────────────────────────────────

_PERSPECTIVES = [
    {
        "key": "financial",
        "label": "Financial Analyst",
        "system": """You are a financial-statement risk analyst on an internal-audit \
investigation team. Given a company ticker, focus exclusively on financial-statement \
risk: revenue quality and concentration, margin trends, liquidity and cash generation, \
manipulation risk (Beneish M-score), and going-concern indicators (Altman Z''-score). \
Pull financials and run the quant models — cite their numbers, never recompute by hand. \
Ignore cyber, operational, and regulatory matters unless they have a direct, material \
financial-statement consequence. Stop and write a concise memo: the 3-5 most material \
financial risks, the evidence for each, and a recommended audit focus.""",
    },
    {
        "key": "operational_cyber",
        "label": "Operational & Cyber Risk Analyst",
        "system": """You are an operational and cybersecurity risk analyst on an \
internal-audit investigation team. Given a company ticker, focus exclusively on \
operational and technology risk: cybersecurity incidents and IT control language (8-Ks, \
Item 1A), supply-chain and execution risk, business continuity, and technology/vendor \
concentration. Pull 8-K events and risk-factor text — cite specific incidents and \
language, don't speculate. Ignore pure financial-statement or regulatory/litigation \
matters unless they stem directly from an operational or cyber event. Stop and write a \
concise memo: the 3-5 most material operational/cyber risks, the evidence for each, and \
a recommended audit focus.""",
    },
    {
        "key": "compliance_regulatory",
        "label": "Compliance & Regulatory Analyst",
        "system": """You are a compliance and regulatory risk analyst on an internal-audit \
investigation team. Given a company ticker, focus exclusively on regulatory, legal, and \
governance risk: litigation and regulatory-action disclosures, industry-specific \
regulatory exposure, disclosure-control language, and governance/board matters. Pull \
risk-factor text and recent 8-Ks — cite the company's own disclosed language, don't \
speculate about matters it hasn't disclosed. Ignore pure financial-statement or \
operational/cyber matters unless they carry a direct regulatory or legal consequence. \
Stop and write a concise memo: the 3-5 most material compliance/regulatory risks, the \
evidence for each, and a recommended audit focus.""",
    },
]

_COUNCIL_SYNTHESIS_SYSTEM = """You reconcile three independent investigation memos — \
financial, operational/cyber, and compliance/regulatory — written by separate analysts \
who investigated the same company without seeing each other's work. Identify findings \
that multiple analysts converged on independently (these are higher-confidence, \
triangulated signals) versus findings only one analyst raised (still worth noting, just \
not cross-validated — do not discard or downgrade them, only label them accurately). Do \
not resolve disagreements by picking a side; report them as-is. Write one headline \
sentence summarizing the overall picture."""

_COUNCIL_SYNTHESIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "convergent_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "finding": {"type": "string"},
                    "perspectives": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["finding", "perspectives"],
            },
        },
        "divergent_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "finding": {"type": "string"},
                    "perspective": {"type": "string"},
                },
                "required": ["finding", "perspective"],
            },
        },
    },
    "required": ["headline", "convergent_findings", "divergent_findings"],
}


async def _run_perspective(perspective: dict, user: str, current_user: dict) -> dict:
    import agent_tools
    result = await asyncio.to_thread(
        claude_client.run_tool_loop,
        perspective["system"], user, agent_tools.TOOLS, agent_tools.IMPLS,
        label=f"investigate:{perspective['key']}", model=_MODEL_AGENT, effort="high",
        max_tokens=10_000, max_iterations=14, caller=current_user,
    )
    return {"key": perspective["key"], "label": perspective["label"], **result}


@router.post("/agent/investigate/council")
async def investigate_council(req: InvestigateRequest, current_user: dict = Depends(get_current_user)):
    """
    Multi-perspective investigation: the financial, operational/cyber, and
    compliance/regulatory analysts run in parallel over the same ticker, then
    a synthesis pass reports where they converged vs. where only one flagged
    something. Costs ~3x a single /agent/investigate call in tokens — use
    when triangulation matters, not as the default for every cycle.
    """
    _require_ai()

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
                    "Compare the current state against these prior findings within your lens."
                )

    focus = f"\n\nSpecific focus from the auditor: {req.focus}" if req.focus else ""
    user = (
        f"Investigate the risk posture of {req.ticker.upper()} within your assigned lens "
        f"and produce an investigation memo.{prior_context}{focus}"
    )

    try:
        perspective_results = await asyncio.gather(
            *[_run_perspective(p, user, current_user) for p in _PERSPECTIVES]
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI council investigation failed: {exc}")

    for pr in perspective_results:
        db.save_ai_analysis(
            "agent_investigation", {"memo": pr["final_text"], "tool_calls": pr["tool_calls"],
                                     "iterations": pr["iterations"], "stopped": pr["stopped"]},
            run_id=req.run_id, ticker=req.ticker, subject_ref=pr["key"],
            model=_MODEL_AGENT, effort="high",
            summary=f"[{pr['label']}] {pr['iterations']} iterations, {len(pr['tool_calls'])} tool calls",
        )

    synthesis_input = "\n\n".join(
        f"=== {pr['label']} ===\n{pr['final_text']}" for pr in perspective_results
    )
    try:
        synthesis = claude_client.complete_json(
            _COUNCIL_SYNTHESIS_SYSTEM, synthesis_input, _COUNCIL_SYNTHESIS_SCHEMA,
            label="investigate_council_synthesis", model=_MODEL_STRUCTURED, effort="medium",
            max_tokens=3000, caller=current_user,
        )
    except Exception as exc:
        raise _ai_exc(exc)

    db.save_ai_analysis(
        "agent_investigation_council", synthesis,
        run_id=req.run_id, ticker=req.ticker,
        model=_MODEL_STRUCTURED, effort="medium",
        summary=synthesis.get("headline", "")[:500],
    )

    return {
        "ticker": req.ticker,
        "perspectives": [
            {"key": pr["key"], "label": pr["label"], "memo": pr["final_text"],
             "tool_calls": pr["tool_calls"], "iterations": pr["iterations"], "stopped": pr["stopped"]}
            for pr in perspective_results
        ],
        "synthesis": synthesis,
    }


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
