#!/usr/bin/env python3
"""
Continuous Monitoring: Management Action Plan (MAP) detection sweep.

Exception Management and JE Testing both stop at "here's what happened, one
event at a time" — this closes a real gap above that: when the SAME control
keeps requiring human review over and over, a single triage decision per
event never asks the question an auditor actually needs answered: why does
this keep happening, and what's the plan to make it stop. This sweep asks
it automatically, drafts an answer, and routes the answer through
human approval before it becomes an official MAP — exactly the same
propose -> human review/adjust -> approve discipline Enterprise Risk Gate 1
applies to a risk rating, not a shortcut around it.

Daily: db.detect_recurring_exceptions() finds any (control_id, system_source)
pair whose latest scored inference required human review at least
MAP_MIN_OCCURRENCES times (default 3) within MAP_WINDOW_DAYS (default 30),
with no MAP already open for that control. For each, _draft_map_proposal
drafts a risk rating (R/A/G — same vocabulary risk_scores.rag_status uses),
root cause, remediation action, success criteria, and a due date from the
actual recent event data (db.get_recent_exception_events_for_control),
falling back to a plain templated draft if the LLM call fails — a MAP
proposal is always human-reviewed before approval, so an unpolished
templated draft is a safe degrade, unlike remediation_endpoints.py's PR
path where a bad draft would be a real code change.

Mirrors vendor_risk_sweep.py's shape exactly: infinite loop, errors caught
and logged, never exits on its own except cancellation. Started as an
asyncio task in api_server.py's lifespan alongside the other background loops.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import claude_client
import db

logger = logging.getLogger(__name__)

# Daily — a recurrence pattern spans weeks by definition (MAP_WINDOW_DAYS),
# so nothing is lost checking once a day rather than more often.
_TICK_S = 86400

MIN_OCCURRENCES = int(os.environ.get("MAP_MIN_OCCURRENCES", "3"))
WINDOW_DAYS = int(os.environ.get("MAP_WINDOW_DAYS", "30"))

_RATING_DUE_DAYS = {"R": 14, "A": 30, "G": 60}

_SYSTEM_PROMPT = (
    "You are drafting a Management Action Plan for an auditor to review. You are given "
    "one control that has required human review multiple times recently, plus a sample "
    "of the actual recurring findings (actor, action, event data). Write:\n"
    "  RATING: <one of R, A, G — R=high risk/needs urgent action, A=moderate, G=low but still recurring>\n"
    "  ROOT_CAUSE: <1-2 sentences — the most likely underlying cause across the sample, "
    "not a restatement of the individual findings>\n"
    "  ACTION: <2-4 sentences — a concrete remediation plan that would stop the recurrence, "
    "not just close the individual findings>\n"
    "  SUCCESS_CRITERIA: <1 sentence — how someone would verify this is actually fixed>\n\n"
    "Output ONLY those four sections in that exact format, no other text, no markdown fences. "
    "Be specific to the actual data given, not generic."
)


def _draft_map_proposal(control_id: str, system_source: str, occurrence_count: int,
                         window_days: int, samples: list[dict]) -> tuple[str, str, str, str]:
    """Returns (risk_rating, root_cause, action, success_criteria). Falls
    back to a plain templated draft (still fully usable — every fact the
    LLM would have used is already in the finding) if the model call fails
    or its output doesn't parse, so a MAP proposal never silently
    disappears just because drafting text failed."""
    sample_text = "\n".join(
        f"- {s.get('event_timestamp')}: actor={s.get('actor')} action={s.get('action')} "
        f"event_type={s.get('event_type')} data={s.get('raw_payload')}"
        for s in samples
    ) or "(no sample event detail available)"
    user = (
        f"control_id: {control_id}\n"
        f"system_source: {system_source}\n"
        f"occurrence_count: {occurrence_count} times in the last {window_days} days\n\n"
        f"Recent sample findings:\n{sample_text}"
    )
    try:
        text, _stop = claude_client.complete_text_meta(
            _SYSTEM_PROMPT, user, label="map_proposal_draft", effort="medium", max_tokens=1200,
        )
        if all(marker in text for marker in ("RATING:", "ROOT_CAUSE:", "ACTION:", "SUCCESS_CRITERIA:")):
            rating = text.split("RATING:", 1)[1].split("ROOT_CAUSE:", 1)[0].strip().upper()[:1]
            root_cause = text.split("ROOT_CAUSE:", 1)[1].split("ACTION:", 1)[0].strip()
            action = text.split("ACTION:", 1)[1].split("SUCCESS_CRITERIA:", 1)[0].strip()
            success_criteria = text.split("SUCCESS_CRITERIA:", 1)[1].strip()
            if rating in ("R", "A", "G") and root_cause and action and success_criteria:
                return rating, root_cause, action, success_criteria
    except Exception as exc:
        logger.warning("map_detection_sweep: LLM draft failed for control %s: %s", control_id, exc)

    rating = "R" if occurrence_count >= MIN_OCCURRENCES * 2 else "A"
    root_cause = (
        f"{control_id} has required human review {occurrence_count} times in the last "
        f"{window_days} days on {system_source or 'an unspecified system'} — recurrence "
        f"at this rate indicates a systemic gap, not isolated one-off findings."
    )
    action = (
        f"Investigate the common thread across recent {control_id} findings (see linked events) "
        f"and implement a durable fix — process change, system configuration, or additional "
        f"control — rather than continuing to dispose of each occurrence individually."
    )
    success_criteria = f"{control_id} no longer requires human review within {window_days} days of the fix landing."
    return rating, root_cause, action, success_criteria


async def _propose_one(candidate: dict) -> bool:
    """Draft and persist one MAP proposal. Returns True if a new MAP row
    was actually created (False on the race where another process already
    opened one for this control_id between detection and insert)."""
    control_id = candidate["control_id"]
    system_source = candidate.get("system_source")
    occurrence_count = candidate["occurrence_count"]

    samples = await asyncio.to_thread(db.get_recent_exception_events_for_control, control_id, 5)
    rating, root_cause, action, success_criteria = await asyncio.to_thread(
        _draft_map_proposal, control_id, system_source, occurrence_count, WINDOW_DAYS, samples,
    )

    due_date = (datetime.now(timezone.utc) + timedelta(days=_RATING_DUE_DAYS.get(rating, 30))).date()
    finding = f"{control_id} required human review {occurrence_count} times in the last {WINDOW_DAYS} days"
    created = await asyncio.to_thread(
        db.create_map,
        control_id, system_source, finding, root_cause, rating, action,
        None, due_date, success_criteria, None, occurrence_count, WINDOW_DAYS,
        candidate.get("first_occurrence_at"), candidate.get("last_occurrence_at"),
        candidate.get("event_ids") or [],
    )
    if created:
        logger.info("map_detection_sweep: proposed %s for control %s (%d occurrences)",
                    created["map_ref"], control_id, occurrence_count)
    return created is not None


async def sweep_once() -> int:
    """Run one detection pass. Returns the number of MAPs newly proposed —
    exposed for tests and an on-demand admin trigger, not just the periodic loop."""
    candidates = await asyncio.to_thread(db.detect_recurring_exceptions, MIN_OCCURRENCES, WINDOW_DAYS)
    proposed = 0
    for candidate in candidates:
        try:
            if await _propose_one(candidate):
                proposed += 1
        except Exception as exc:
            logger.warning("map_detection_sweep: failed to propose MAP for control %s: %s",
                            candidate.get("control_id"), exc)
    if proposed:
        logger.info("map_detection_sweep: proposed %d new MAP(s)", proposed)
    return proposed


async def start_sweep() -> None:
    logger.info("MAP detection sweep started (tick=%.0fs, min_occurrences=%d, window_days=%d)",
                _TICK_S, MIN_OCCURRENCES, WINDOW_DAYS)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("MAP detection sweep stopped")
            break
        except Exception as exc:
            logger.warning("map_detection_sweep tick error: %s", exc)
