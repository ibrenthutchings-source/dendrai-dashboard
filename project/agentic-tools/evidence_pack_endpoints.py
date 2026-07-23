"""
Audit Evidence Pack — one-shot assembly of everything defensible about a
specific pipeline run (risk scores, audit objectives, HITL sign-offs,
Risks-as-Code artifacts, risk->control mappings, PaC/CaC state, adjudicated
tool calls, AI narrative, and the execution log) into a single bundle an
auditor can hand to an external reviewer.

Reuses existing db.py getters wherever the data is already run_id-anchored
and clean; where a genuine gap exists (adjudications have no reliable run
link, Controls-as-Code isn't versioned per run, Policy-as-Code evaluation
isn't persisted at all) the gap is surfaced as an explicit caveat in the
response rather than silently omitted or implied to be more precise than
it is.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

import db
import mcp_governance
import risks_as_code
from auth_endpoints import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evidence-pack", tags=["Evidence Pack"])


@router.get("/{run_id}")
async def get_evidence_pack(run_id: int, current_user: dict = Depends(get_current_user)):
    """Assemble the full evidence bundle for one pipeline run."""
    run = db.get_run_meta_for_evidence_pack(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    ticker = run["ticker"]

    risk_scores = db.get_risk_scores_for_run(run_id)
    objectives = db.get_audit_objectives_for_run(run_id)
    approval_tasks = db.get_approval_tasks_for_run(run_id)
    loop_log = db.get_loop_log_for_run(run_id)

    rac_artifacts = {}
    for fw in risks_as_code.SUPPORTED_FRAMEWORKS:
        artifact = db.get_risks_as_code_artifact(run_id, fw)
        if artifact:
            rac_artifacts[fw] = artifact

    audit_report_rows = db.get_ai_analyses(run_id, kind="audit_report", limit=1)
    audit_report = audit_report_rows[0] if audit_report_rows else None

    reviews = db.list_risk_register_reviews(run_id=run_id)
    controls_by_risk: list[dict] = []
    for review in reviews:
        controls_by_risk.extend(db.get_review_risk_states(review["id"]))

    # Controls-as-Code is generated globally per ticker, not per pipeline
    # run — no per-run version exists. Include the latest one available
    # with an explicit caveat rather than implying it reflects run-time
    # state.
    cac_latest = db.get_latest_cac_artifact(ticker)

    # Policy-as-Code evaluation is stateless (POST /pac/evaluate persists
    # nothing) — there is no "evaluation result for this run" to include.
    # Show the current Rego module content per process instead, with a
    # caveat that it's current state, not a point-in-time record.
    pac_modules = []
    for meta in db.list_pac_modules():
        full = db.get_latest_pac_module(meta["process"])
        if full:
            pac_modules.append(full)

    adjudications = mcp_governance.fetch_adjudications_for_run(
        run_id, run.get("run_at"), run.get("completed_at"),
    )
    time_window_estimate_count = sum(
        1 for a in adjudications if a.get("linked_via") == "time_window_estimate"
    )

    caveats = [
        {
            "section": "adjudications",
            "note": (
                "MCP/tool adjudications are correlated to this run by a best-effort "
                "ticker+time-window join, not a verified run_id link, because the current "
                "telemetry write path has no run context available at write time. Rows are "
                "tagged 'linked_via' accordingly."
            ),
        },
        {
            "section": "controls_as_code",
            "note": (
                "Controls-as-Code shown is the latest as of export, not versioned per run. "
                "CaC generation is global per ticker, not tied to this specific pipeline run."
            ),
        },
        {
            "section": "policy_as_code",
            "note": (
                "Current policy state, not a point-in-time evaluation record from this run. "
                "Policy-as-Code evaluation results are not persisted; only the current Rego "
                "module content is shown."
            ),
        },
    ]

    return {
        "run": run,
        "generated_by": current_user.get("username"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "risk_scores": risk_scores,
        "audit_objectives": objectives,
        "approval_tasks": approval_tasks,
        "risks_as_code_artifacts": rac_artifacts,
        "controls_by_risk": controls_by_risk,
        "controls_as_code_latest": cac_latest,
        "policy_as_code_modules": pac_modules,
        "adjudications": adjudications,
        "adjudications_meta": {
            "total": len(adjudications),
            "time_window_estimate_count": time_window_estimate_count,
        },
        "audit_report": audit_report,
        "loop_log": loop_log,
        "caveats": caveats,
    }
