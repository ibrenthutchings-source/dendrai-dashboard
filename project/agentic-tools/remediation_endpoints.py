#!/usr/bin/env python3
"""
Closed-loop remediation — propose step.

Exception Management and JE Testing both stop at "here's what happened";
this is the next link in the chain: draft a concrete fix for one finding
(exception_control_events row, either source — see db.get_exception_event_by_id),
route it through the existing 2-stage preparer/manager approval workflow
(approvals_endpoints.py, gate_type='remediation_github'), and — once a
manager approves — approvals_endpoints.py's execute-on-approve branch fires
the actual GitHub write via github_write_tool.py.

Deliberately its own router rather than a route added to exceptions_endpoints.py:
that router is Development-environment-only end-to-end (see its module
docstring), but a JE Testing finding — real, always-on, every environment —
needs to reach this exact same propose step. Gated on the "approvals" screen's
edit permission, not "exceptions" or "continuousmonitoring": proposing a
remediation is the first step of a chain that ends in an external write, so
it shares its authorization boundary with the write itself, not with
whichever screen happened to surface the source finding.

Router prefix: /remediation

    POST /remediation/propose/{event_id}   Draft a GitHub issue for one finding, submit for manager approval
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

import approvals_endpoints
import auth_db
import claude_client
import db
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.remediation")

router = APIRouter(prefix="/remediation", tags=["Closed-Loop Remediation"])

_SYSTEM_PROMPT = (
    "You draft a short, concrete GitHub issue for an auditor's remediation ticket. "
    "You are given one control-monitoring finding (what fired, on which system, the "
    "raw event data). Write:\n"
    "  TITLE: <one line, <=80 chars, names the specific problem>\n"
    "  BODY:\n"
    "  <2-5 short paragraphs or a short bullet list: what was observed, why it matters, "
    "  a concrete suggested fix or compensating control>\n\n"
    "Output ONLY those two sections in that exact TITLE:/BODY: format — no other text, "
    "no markdown code fences. Be specific to the actual data given, not generic."
)


def _draft_issue(event: dict) -> tuple[str, str]:
    """Returns (title, body). Falls back to a plain templated issue (still
    fully usable — every fact the LLM would have used is already in the
    finding) if the model call fails, so a remediation proposal never
    silently disappears just because drafting text failed."""
    user = (
        f"control_id: {event.get('control_id')}\n"
        f"system_source: {event.get('system_source')}\n"
        f"process: {event.get('process')}\n"
        f"event_type: {event.get('event_type')}\n"
        f"actor: {event.get('actor')}\n"
        f"action: {event.get('action')}\n"
        f"event_timestamp: {event.get('event_timestamp')}\n"
        f"raw_payload: {event.get('raw_payload')}\n"
    )
    try:
        text, _stop = claude_client.complete_text_meta(
            _SYSTEM_PROMPT, user, label="remediation_issue_draft", effort="low", max_tokens=1200,
        )
        if "TITLE:" in text and "BODY:" in text:
            title = text.split("TITLE:", 1)[1].split("BODY:", 1)[0].strip()
            body = text.split("BODY:", 1)[1].strip()
            if title and body:
                return title[:200], body
    except Exception as exc:
        logger.warning("remediation: LLM issue draft failed for event %s: %s", event.get("id"), exc)

    title = f"[{event.get('control_id')}] {event.get('system_source')} finding requires review"
    body = (
        f"**Control:** {event.get('control_id')}\n"
        f"**System:** {event.get('system_source')}\n"
        f"**Process:** {event.get('process') or '—'}\n"
        f"**Actor:** {event.get('actor') or '—'}\n"
        f"**Action:** {event.get('action') or '—'}\n"
        f"**When:** {event.get('event_timestamp') or '—'}\n\n"
        f"```\n{event.get('raw_payload')}\n```\n"
    )
    return title, body


def _display_name(user: dict) -> str:
    return user.get("display_name") or user.get("username") or f"User {user.get('id')}"


@router.post("/propose/{event_id}")
def propose_remediation(event_id: int, current_user: Dict[str, Any] = Depends(require_screen_permission("approvals", edit=True))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    event = db.get_exception_event_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"No finding with event_id={event_id}")

    title, body = _draft_issue(event)
    manager = auth_db.get_manager_of(current_user["id"])

    task = db.upsert_approval_task(
        run_id=None,
        gate_type="remediation_github",
        item_ref=str(event_id),
        item_label=title,
        # Always 'adjusted' — a remediation proposal is never "accepted as
        # computed" (there's no prior baseline to accept), so it always
        # requires a human review step before the GitHub write can fire.
        # This is what makes "approve = execute" safe: the review IS the gate.
        disposition="adjusted",
        adjustments={"title": title, "body": body, "labels": ["dendrai-remediation"],
                     "source_event_id": event_id, "control_id": event.get("control_id")},
        rationale=f"Proposed remediation for {event.get('control_id')} ({event.get('system_source')})",
        prepared_by=current_user["id"],
        prepared_by_name=_display_name(current_user),
        manager_id=manager["id"] if manager else None,
        manager_name=_display_name(manager) if manager else None,
    )
    if not task:
        raise HTTPException(status_code=500, detail="Failed to save remediation proposal")

    # No manager configured -> upsert_approval_task already finalized the
    # task as 'approved' — execute immediately, same as approvals_endpoints.py's
    # prepare_item does for devops_scm_exception's equivalent auto-approve path.
    if task.get("status") == "approved":
        approvals_endpoints._execute_remediation(task)
        task = db.get_approval_task(task["id"]) or task

    logger.info("remediation: proposed for event %s (task %s, status %s)", event_id, task.get("id"), task.get("status"))
    return {"task": task}
