#!/usr/bin/env python3
"""
PBC/workpaper evidence quality — log a piece of collected evidence against a
control and get back deterministic quality flags (stale, unsigned, period
mismatch) plus one LLM-assisted content-plausibility check.

The content check is deliberately separate from evidence_quality_tool.py's
deterministic rules: "is this evidence dated inside its period" is a fact;
"does this evidence actually describe the control it's attached to" is a
judgment call an LLM can draft an opinion on but never authoritatively
decide — it's advisory, stored alongside the deterministic flags but never
merged into them, and a human reviewing the evidence log is the one who
acts on it.

Router prefix: /evidence-quality

    POST /evidence-quality/items   Log one evidence item; runs both check kinds, persists, returns flags
    GET  /evidence-quality/items   Filtered list (?control_id=&flagged_only=)
    GET  /evidence-quality/items/{id}  One item
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import claude_client
import db
import evidence_quality_tool as eqt
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.evidence_quality")

router = APIRouter(prefix="/evidence-quality", tags=["PBC Evidence Quality"])

# Dedicated nav item added 2026-08-19 (nav.jsx, "PBC Evidence Log") — id
# deliberately matches that nav entry, same convention ai_governance_endpoints.py
# documents for itself.
_SCREEN_ID = "evidencequality"

_CONTENT_CHECK_SYSTEM_PROMPT = (
    "You review one piece of audit evidence for plausibility, not authenticity. You are given "
    "the control it's meant to support and the auditor's own description of what the evidence "
    "shows. Judge only: does this description plausibly support this control, or does it "
    "describe something else entirely (wrong system, wrong control, or too vague to tell). "
    "Output exactly one line in this format, no other text:\n"
    "VERDICT: <PLAUSIBLE|MISMATCH|INCONCLUSIVE> | <one-sentence reason>"
)


class LogEvidenceRequest(BaseModel):
    control_id: str
    title: str
    description: Optional[str] = None
    source_url: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    collected_date: Optional[str] = None
    has_signature: bool = False
    requires_signature: bool = False
    max_age_days: int = 90
    control_description: Optional[str] = None  # if given, runs the LLM content-plausibility check


def _content_check(control_description: str, evidence_description: str) -> Optional[dict]:
    """Returns {"verdict": "...", "reason": "..."} or None if the model call
    fails or the response doesn't parse — this check is advisory, so a
    failure here must never block logging the evidence itself, only leave
    content_check null."""
    try:
        text, _stop = claude_client.complete_text_meta(
            _CONTENT_CHECK_SYSTEM_PROMPT,
            f"control: {control_description}\n\nevidence description: {evidence_description}",
            label="pbc_evidence_content_check", effort="low", max_tokens=200,
        )
        if "VERDICT:" not in text:
            return None
        rest = text.split("VERDICT:", 1)[1].strip()
        verdict, _, reason = rest.partition("|")
        verdict = verdict.strip().upper()
        if verdict not in ("PLAUSIBLE", "MISMATCH", "INCONCLUSIVE"):
            return None
        return {"verdict": verdict, "reason": reason.strip() or None}
    except Exception as exc:
        logger.warning("evidence_quality: content check failed: %s", exc)
        return None


@router.post("/items")
def log_evidence(req: LogEvidenceRequest, current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID, edit=True))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    flags = eqt.run_quality_checks({
        "collected_date": req.collected_date, "period_start": req.period_start, "period_end": req.period_end,
        "has_signature": req.has_signature, "requires_signature": req.requires_signature,
        "max_age_days": req.max_age_days,
    })

    content_check = None
    if req.control_description and req.description:
        content_check = _content_check(req.control_description, req.description)
        if content_check and content_check["verdict"] == "MISMATCH":
            flags = flags + [{
                "code": "CONTENT_MISMATCH", "severity": "HIGH",
                "message": f"AI plausibility check: {content_check.get('reason') or 'evidence does not appear to describe this control'} "
                           f"— advisory, confirm manually.",
            }]

    created = db.create_pbc_evidence(
        control_id=req.control_id, title=req.title, description=req.description, source_url=req.source_url,
        period_start=req.period_start, period_end=req.period_end, collected_date=req.collected_date,
        has_signature=req.has_signature, requires_signature=req.requires_signature,
        quality_flags=flags, content_check=content_check,
        created_by=current_user.get("username"),
    )
    if not created:
        raise HTTPException(status_code=500, detail="Failed to log evidence")
    return created


@router.get("/items")
def list_evidence(control_id: Optional[str] = None, flagged_only: bool = False, limit: int = 100,
                   current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        return {"items": []}
    return {"items": db.list_pbc_evidence(control_id=control_id, flagged_only=flagged_only, limit=limit)}


@router.get("/items/{evidence_id}")
def get_evidence(evidence_id: int, current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    item = db.get_pbc_evidence(evidence_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"No evidence item with id={evidence_id}")
    return item
