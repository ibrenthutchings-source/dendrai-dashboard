#!/usr/bin/env python3
"""
Process Mining API — variant analysis, conformance checking, cycle-time/
bottleneck stats, and rework detection over case-tracked adjudications.

Router prefix: /process-mining

    GET /process-mining/processes      The known process templates (id, label, canonical steps)
    GET /process-mining/summary        Headline tiles: case counts, conformance/rework rate, bottleneck per process
    GET /process-mining/variants       Distinct step sequences observed, most frequent first
    GET /process-mining/conformance    Conformance rate + deviating cases against the matched template
    GET /process-mining/cycle-times    Per-edge duration stats + overall case duration + bottleneck
    GET /process-mining/rework         Cases that revisited an already-completed step
    GET /process-mining/cases          Per-case detail (steps, duration, variant, conformance)
    POST /process-mining/walkthrough-narrative  Draft a walkthrough narrative from an interview transcript + real process-mining/CaC/PaC evidence
    GET /process-mining/walkthrough-narrative/history  HITL status of this process's submitted walkthrough narratives

All read-only and computed on demand from the same
observability.adjudicated_tool_calls feed GET /observability/events and
CaseFlowGraph (continuous-monitoring-viz.jsx) already use — see
db.get_recent_adjudications_for_domain_summary. process_mining_tool.py does
the actual analysis and is pure (no DB); this router owns fetching the raw
rows and building cases from them once per request. Surfaced on the
Continuous Monitoring screen (nav id "continuousmonitoring") as additional
chart tabs alongside the existing Case Flow Graph.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import claude_client
import db
import pac_assurance
import process_mining_tool as pm
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.process_mining")

# Router-level: backs the "Continuous Monitoring" screen's Process Mining tabs.
router = APIRouter(prefix="/process-mining", tags=["Process Mining"],
                    dependencies=[Depends(require_screen_permission("continuousmonitoring"))])


async def _load_events(days: int) -> list:
    """Same union GET /observability/events does (api_server.py) — adjudicated
    rows plus the unreviewed system_telemetry tail, since only flagged rows
    ever reach adjudication (mcp_governance._fetch_unprocessed_system) and
    process mining's case_id/process_step live on the raw event regardless of
    whether it was ever selected for review. Before this, Variants/
    Conformance/Cycle Time/Rework silently saw only the same ~2.5% slice
    Continuous Monitoring's charts did until that was fixed — build_cases()
    only reads case_id/process_step/adjudicated_at, none of which require a
    real verdict, so the unreviewed rows are exactly as usable here as
    CaseFlowGraph already treats them."""
    if not db.is_available():
        return []
    adjudicated, unreviewed = await asyncio.gather(
        asyncio.to_thread(db.get_recent_adjudications_for_domain_summary, days=days, limit=5000),
        asyncio.to_thread(db.get_recent_unreviewed_system_events, days=days, limit=5000),
    )
    return adjudicated + unreviewed


async def _load_cases(days: int) -> dict:
    events = await _load_events(days)
    return pm.build_cases(events)


@router.get("/processes")
async def list_processes():
    return {
        "processes": [
            {"id": pid, "label": tmpl["label"], "steps": tmpl["steps"]}
            for pid, tmpl in pm.PROCESS_TEMPLATES.items()
        ]
    }


@router.get("/summary")
async def summary(days: int = 30):
    if not db.is_available():
        return {"total_cases": 0, "untemplated_cases": 0, "processes": {}, "note": "Database not configured"}
    events = await _load_events(days)
    return {**pm.summary(events), "window_days": days}


@router.get("/variants")
async def variants(days: int = 30, process: Optional[str] = None):
    cases = await _load_cases(days)
    return {"variants": pm.variant_analysis(cases, process), "process": process, "window_days": days}


@router.get("/conformance")
async def conformance(days: int = 30, process: Optional[str] = None):
    cases = await _load_cases(days)
    return {**pm.conformance_summary(cases, process), "window_days": days}


@router.get("/cycle-times")
async def cycle_times(days: int = 30, process: Optional[str] = None):
    cases = await _load_cases(days)
    return {**pm.cycle_time_stats(cases, process), "window_days": days}


@router.get("/rework")
async def rework(days: int = 30, process: Optional[str] = None):
    cases = await _load_cases(days)
    return {**pm.rework_summary(cases, process), "window_days": days}


@router.get("/cases")
async def cases_endpoint(days: int = 30, process: Optional[str] = None, limit: int = 200):
    cases = await _load_cases(days)
    out = pm.list_case_summaries(cases, process)
    return {"cases": out[:limit], "total": len(out), "window_days": days}


# ─────────────────────────────────────────────────────────────────────────────
# Walkthrough narrative drafting — first draft for a human to correct. What
# makes this more than a transcript summarizer: four independent real-data
# sources for the SAME process are computed and handed to the model
# alongside the transcript, and the prompt explicitly asks it to call out
# where the transcript disagrees with any of them:
#   - Case Flow  — real directly-follows edges (step -> step transition
#     counts), the same evidence CaseFlowGraph/DFG charts plot, so the
#     narrative can say what the process ACTUALLY does step to step, not
#     just how often each full path recurs.
#   - Variants   — distinct end-to-end step sequences observed, most
#     frequent first, so the model can call out when the process owner
#     described "the" process but multiple materially different paths
#     actually occur.
#   - CaC (Controls-as-Code) — every control this process's catalog
#     entry lists (controls_catalog, both manually catalogued and
#     policy-enforced) — what SHOULD be operating.
#   - PaC (Policy-as-Code) — of those, which are actually policy-enforced
#     and PROVEN working (fired recently and/or passed a negative-control
#     test) vs. unverified — what's actually enforced, not just catalogued.
# Once drafted, the narrative is submitted through the same real 2-stage
# preparer -> manager HITL workflow every other Assess Risk gate uses
# (gate_type='walkthrough_narrative' — see approvals_endpoints.py's generic
# /approvals/prepare + /approvals/review, and GET .../history below for this
# screen's own view of that trail) rather than being a throwaway textarea
# result — see this router's module docstring.
# ─────────────────────────────────────────────────────────────────────────────

class WalkthroughNarrativeRequest(BaseModel):
    process: str
    transcript: str
    days: int = 90


_WALKTHROUGH_SYSTEM_PROMPT = (
    "You are drafting a first-draft SOX/ITGC walkthrough narrative for a human auditor to "
    "review and correct — never treat this as final. You are given a process interview "
    "transcript (the process owner's own words) and four independent real-data sources "
    "computed from actual system data for the same process:\n"
    "  - case_flow: real step-to-step transition counts (the directly-follows graph) — what "
    "the process actually does, in order, and how often\n"
    "  - variants: distinct end-to-end step sequences observed, most frequent first\n"
    "  - controls_as_code: every control this process's catalog lists — what SHOULD be operating\n"
    "  - policy_as_code: which of those controls are actually policy-enforced and PROVEN working "
    "(fired recently and/or passed a negative-control test) vs. unverified\n"
    "Write a walkthrough narrative with these sections:\n"
    "  PROCESS DESCRIPTION: <what the process owner described, in clear prose, third person>\n"
    "  KEY CONTROLS: <control points the transcript mentions, as a short list>\n"
    "  SYSTEM EVIDENCE: <how case_flow/variants/controls_as_code/policy_as_code corroborate or "
    "CONTRADICT what was described — call out any discrepancy explicitly: a control the "
    "transcript claims exists but isn't in controls_as_code at all, a control that's catalogued "
    "but not policy_as_code-verified, a step sequence the transcript never mentioned, a "
    "transition case_flow shows happening regularly that the process owner described as rare or "
    "exceptional. This is the most valuable part of the narrative>\n"
    "  OPEN QUESTIONS: <anything the transcript left ambiguous that the auditor should follow up on>\n\n"
    "Output ONLY those four sections in that exact format, no markdown fences, no other text. "
    "Be specific to the actual transcript and data given, not generic boilerplate."
)


def _draft_walkthrough_narrative(process_label: str, transcript: str, stats: dict) -> Optional[dict]:
    """Returns the four-section draft, or None if the model call failed or
    the response didn't parse. Deliberately no templated fallback here
    (unlike remediation_endpoints._draft_issue) — a human explicitly
    requested this narrative and is standing by; a generic templated
    narrative would read as a real draft when it isn't, which is worse than
    surfacing a clear failure and letting them draft it themselves."""
    user = (
        f"process: {process_label}\n\n"
        f"--- interview transcript ---\n{transcript}\n--- end transcript ---\n\n"
        f"--- process-mining statistics (real, computed from system data) ---\n{stats}\n--- end statistics ---"
    )
    try:
        text, _stop = claude_client.complete_text_meta(
            _WALKTHROUGH_SYSTEM_PROMPT, user, label="walkthrough_narrative_draft", effort="high", max_tokens=3000,
        )
    except Exception as exc:
        logger.warning("process_mining: walkthrough narrative draft failed: %s", exc)
        return None

    markers = ("PROCESS DESCRIPTION:", "KEY CONTROLS:", "SYSTEM EVIDENCE:", "OPEN QUESTIONS:")
    if not all(m in text for m in markers):
        return None
    process_description = text.split("PROCESS DESCRIPTION:", 1)[1].split("KEY CONTROLS:", 1)[0].strip()
    rest = text.split("KEY CONTROLS:", 1)[1]
    key_controls = rest.split("SYSTEM EVIDENCE:", 1)[0].strip()
    rest = rest.split("SYSTEM EVIDENCE:", 1)[1]
    system_evidence = rest.split("OPEN QUESTIONS:", 1)[0].strip()
    open_questions = rest.split("OPEN QUESTIONS:", 1)[1].strip()
    if not (process_description and key_controls and system_evidence and open_questions):
        return None
    return {
        "process_description": process_description, "key_controls": key_controls,
        "system_evidence": system_evidence, "open_questions": open_questions,
    }


@router.post("/walkthrough-narrative")
async def walkthrough_narrative(req: WalkthroughNarrativeRequest):
    if not req.transcript.strip():
        raise HTTPException(status_code=422, detail="transcript is required")
    if req.process not in pm.PROCESS_TEMPLATES:
        raise HTTPException(status_code=404, detail=f"Unknown process '{req.process}'")

    cases = await _load_cases(req.days)
    cycle_times = pm.cycle_time_stats(cases, req.process)
    controls, pac = await asyncio.gather(
        asyncio.to_thread(db.list_controls, process=req.process),
        asyncio.to_thread(pac_assurance.assurance_summary, process=req.process),
    )
    stats = {
        "variants": pm.variant_analysis(cases, req.process)[:5],
        "conformance": pm.conformance_summary(cases, req.process),
        # Same edges cycle_times computes (step A -> step B, consecutive
        # within a case), re-sorted by transition COUNT rather than
        # duration — cycle_times answers "where does time accumulate",
        # case_flow answers "what does this process actually do and how
        # often" (the same lens CaseFlowGraph/DFG charts plot), and the
        # walkthrough prompt needs both, separately labeled.
        "case_flow": sorted(cycle_times.get("edges") or [], key=lambda e: e["count"], reverse=True)[:8],
        "cycle_times": cycle_times,
        "rework": pm.rework_summary(cases, req.process),
        # CaC: every control this process's catalog lists (controls_catalog,
        # manual and policy-enforced alike) — what SHOULD be operating.
        "controls_as_code": [
            {"control_id": c["control_id"], "name": c["name"], "source": c["source"]}
            for c in controls
        ],
        # PaC: of those, which are policy-enforced and PROVEN working
        # (recent real fire and/or a passing negative-control test) vs.
        # unverified — what's actually enforced, not just catalogued.
        "policy_as_code": {
            "enforced_count": pac["total"],
            "unverified_count": pac["unverified_count"],
            "unverified_control_ids": [c["control_id"] for c in pac["unverified"]],
        },
    }
    draft = await asyncio.to_thread(
        _draft_walkthrough_narrative, pm.PROCESS_TEMPLATES[req.process]["label"], req.transcript, stats,
    )
    if not draft:
        raise HTTPException(status_code=502, detail="Could not draft a narrative from this transcript — try again or draft manually")
    return {"narrative": draft, "supporting_stats": stats, "process": req.process, "window_days": req.days}


# ─────────────────────────────────────────────────────────────────────────────
# HITL history for this gate type — walkthrough narratives aren't tied to a
# risk_loop_runs row (run_id is NULL, same precedent as concept_link/
# devops_scm_exception — see approval_tasks.run_id's DROP NOT NULL migration
# in db.py), so GET /approvals/status/{run_id} can't answer "what's the
# status of this process's walkthrough narratives" the way it does for
# Gate 1/2. This is that same question, scoped by (gate_type, item_ref)
# instead of run_id.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/walkthrough-narrative/history")
async def walkthrough_narrative_history(process: str):
    if process not in pm.PROCESS_TEMPLATES:
        raise HTTPException(status_code=404, detail=f"Unknown process '{process}'")
    if not db.is_available():
        return {"tasks": [], "process": process}
    tasks = await asyncio.to_thread(db.get_approval_tasks_by_item_ref, "walkthrough_narrative", process)
    return {"tasks": tasks, "process": process}
