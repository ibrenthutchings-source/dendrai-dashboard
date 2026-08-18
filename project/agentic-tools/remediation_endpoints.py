#!/usr/bin/env python3
"""
Closed-loop remediation — propose step.

Exception Management and JE Testing both stop at "here's what happened";
this is the next link in the chain: draft a concrete fix for one finding
(exception_control_events row, either source — see db.get_exception_event_by_id),
route it through the existing 2-stage preparer/manager approval workflow
(approvals_endpoints.py), and — once a manager approves —
approvals_endpoints.py's execute-on-approve branch fires the actual GitHub
write via github_write_tool.py. Two proposal shapes, two gate types:
  - gate_type='remediation_github'    — a tracked issue (propose_remediation)
  - gate_type='remediation_github_pr' — a real file-change PR (propose_pr_remediation),
    for a finding the reviewer can point at a specific repo file; get_file_content
    reads it, Claude drafts the fix, a unified diff is stored for the
    manager to review before approving.

Deliberately its own router rather than a route added to exceptions_endpoints.py:
that router is Development-environment-only end-to-end (see its module
docstring), but a JE Testing finding — real, always-on, every environment —
needs to reach this exact same propose step. Gated on the "approvals" screen's
edit permission, not "exceptions" or "continuousmonitoring": proposing a
remediation is the first step of a chain that ends in an external write, so
it shares its authorization boundary with the write itself, not with
whichever screen happened to surface the source finding.

Router prefix: /remediation

    POST /remediation/propose/{event_id}      Draft a GitHub issue for one finding, submit for manager approval
    POST /remediation/propose-pr/{event_id}   Draft a real file-change PR for one finding + a named file, submit for manager approval
"""
from __future__ import annotations

import difflib
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import approvals_endpoints
import auth_db
import claude_client
import db
import github_write_tool
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.remediation")

router = APIRouter(prefix="/remediation", tags=["Closed-Loop Remediation"])


class ProposePrRequest(BaseModel):
    file_path: str
    repo: Optional[str] = None
    base_branch: str = "main"

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


_PR_SYSTEM_PROMPT = (
    "You fix one specific problem in a source file for an auditor's remediation pull request. "
    "You are given the file's complete current content and one control-monitoring finding "
    "describing what's wrong. Make the minimal change needed to address the finding — do not "
    "reformat, refactor, or touch unrelated lines.\n\n"
    "Output ONLY in this exact format, no other text, no markdown code fences:\n"
    "TITLE: <one line, <=80 chars, names the specific fix>\n"
    "BODY:\n"
    "<2-4 short paragraphs: what was wrong, what changed, why>\n"
    "FILE:\n"
    "<the complete corrected file content, every line>"
)

# A file this large makes both the LLM's full-file-rewrite approach and a
# human's diff review unreliable — reject rather than propose a low-confidence
# PR against it. No hard technical ceiling, just a sanity bound.
_MAX_PR_FILE_CHARS = 40_000


def _draft_pr_fix(event: dict, file_path: str, current_content: str) -> Optional[tuple[str, str, str]]:
    """Returns (title, body, new_content), or None if a fix couldn't be
    drafted with enough confidence to propose as a real code change. Unlike
    _draft_issue, there is deliberately no safe templated fallback here — a
    fabricated 'best guess' file rewrite would be worse than no PR at all."""
    if len(current_content) > _MAX_PR_FILE_CHARS:
        logger.warning("remediation: %s too large for PR drafting (%d chars)", file_path, len(current_content))
        return None
    user = (
        f"file_path: {file_path}\n"
        f"control_id: {event.get('control_id')}\n"
        f"system_source: {event.get('system_source')}\n"
        f"event_type: {event.get('event_type')}\n"
        f"finding_detail: {event.get('raw_payload')}\n\n"
        f"--- current file content ---\n{current_content}\n--- end file content ---"
    )
    try:
        text, _stop = claude_client.complete_text_meta(
            _PR_SYSTEM_PROMPT, user, label="remediation_pr_draft", effort="medium",
            max_tokens=max(2000, len(current_content) // 2 + 1000),
        )
    except Exception as exc:
        logger.warning("remediation: LLM PR draft failed for event %s: %s", event.get("id"), exc)
        return None

    if "TITLE:" not in text or "BODY:" not in text or "FILE:" not in text:
        return None
    title = text.split("TITLE:", 1)[1].split("BODY:", 1)[0].strip()
    rest = text.split("BODY:", 1)[1]
    body = rest.split("FILE:", 1)[0].strip()
    # lstrip only — the newline right after the "FILE:" marker line, not any
    # trailing newline the real file content is supposed to end with (a
    # naive .strip("\n") would silently drop every file's trailing newline,
    # turning every single proposed PR into a spurious "no trailing newline" diff).
    new_content = rest.split("FILE:", 1)[1].lstrip("\n")
    if not title or not body or not new_content or new_content == current_content:
        return None  # empty draft, or no actual change proposed — nothing to open a PR for
    return title[:200], body, new_content


@router.post("/propose-pr/{event_id}")
def propose_pr_remediation(
    event_id: int, req: ProposePrRequest,
    current_user: Dict[str, Any] = Depends(require_screen_permission("approvals", edit=True)),
):
    """Unlike propose_remediation (an issue needs no file target), a PR
    needs the reviewer to name the specific repo file this finding maps to —
    there's no principled way to auto-detect that from an arbitrary
    business-exception finding. get_file_content then reads the real file
    (its failure is exactly what stops a bogus PR from ever being drafted),
    Claude drafts the fix, and a unified diff is computed and stored so the
    approving manager can review the actual change before approving."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    event = db.get_exception_event_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"No finding with event_id={event_id}")

    file_result = github_write_tool.get_file_content(req.file_path, repo=req.repo, ref=req.base_branch)
    if file_result.get("error"):
        raise HTTPException(status_code=502, detail=f"Could not read {req.file_path}: {file_result['error']}")

    drafted = _draft_pr_fix(event, req.file_path, file_result["content"])
    if not drafted:
        raise HTTPException(
            status_code=502,
            detail="Could not draft a confident fix for this file — propose a GitHub issue instead",
        )
    title, body, new_content = drafted

    diff = "".join(difflib.unified_diff(
        file_result["content"].splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=req.file_path, tofile=req.file_path,
    ))

    manager = auth_db.get_manager_of(current_user["id"])
    task = db.upsert_approval_task(
        run_id=None,
        gate_type="remediation_github_pr",
        item_ref=str(event_id),
        item_label=title,
        disposition="adjusted",
        adjustments={
            "title": title, "body": body, "repo": req.repo, "base_branch": req.base_branch,
            "file_path": req.file_path, "source_event_id": event_id, "control_id": event.get("control_id"),
            # Underscore-prefixed: excluded from approval-inbox.jsx's generic
            # AdjustmentSummary chip renderer (object/giant-string values would
            # render as "[object Object]" or an unreadable inline blob there) —
            # shown instead via a dedicated diff-preview component.
            "_files": {req.file_path: new_content}, "_diff": diff,
        },
        rationale=f"Proposed PR fix for {event.get('control_id')} ({event.get('system_source')}) in {req.file_path}",
        prepared_by=current_user["id"],
        prepared_by_name=_display_name(current_user),
        manager_id=manager["id"] if manager else None,
        manager_name=_display_name(manager) if manager else None,
    )
    if not task:
        raise HTTPException(status_code=500, detail="Failed to save remediation proposal")

    if task.get("status") == "approved":
        approvals_endpoints._execute_remediation(task)
        task = db.get_approval_task(task["id"]) or task

    logger.info("remediation: PR proposed for event %s file %s (task %s, status %s)",
                event_id, req.file_path, task.get("id"), task.get("status"))
    return {"task": task, "diff": diff}
