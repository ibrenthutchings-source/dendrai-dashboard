#!/usr/bin/env python3
"""
Regulatory change management — horizon scanning + HITL review.

Extends rss_ingest_service.py's feed fetching (which scores individual
articles as they appear) with a second, complementary lens: does a feed's
current content represent a MATERIAL CHANGE from what this system last saw?
regulatory_change_tool.py does the actual diffing; this router owns fetching
the raw text and running the HITL review workflow around it — same
fetch/interpret split process_mining_endpoints.py/process_mining_tool.py and
db._build_control_flow_map/db.get_control_flow_map already establish.

"Source text" for a feed is its current entries (title + summary,
concatenated) rather than a full scrape of statutory text — this codebase
has no dedicated legal-text scraper (EUR-Lex, Federal Register full-text
APIs, etc. are out of scope here), and fabricating one would mean inventing
a diff over content nothing actually fetched. What IS real: whether the
regulator/monitoring source's published content changed since the last
scan, which is exactly what a horizon-scanning analyst watches for day to
day. Same honesty discipline framework_mappings.py's "never LLM-generated
or auto-inferred" docstring establishes for its curated crosswalk — applied
here to what counts as "the source," not to the mapping step.

Nothing reaches controls_library without an explicit human decision — same
"no ungrounded generation reaches the register" guardrail pac_policy_docs.py
enforces for Rego modules (see db.py's regulatory_change_proposals docstring).

Router prefix: /regulatory-change

    POST /regulatory-change/scan                     Fetch feeds, diff against last version, draft proposals for material changes
    GET  /regulatory-change/versions                  Recent fetched snapshots (?feed_id=)
    GET  /regulatory-change/proposals                 Review queue (?status=)
    GET  /regulatory-change/proposals/{id}             One proposal, full diff included
    POST /regulatory-change/proposals/{id}/decision    approve | reject
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import feedparser
from fastapi import APIRouter, Body, Depends, HTTPException, Query

import claude_client
import db
import regulatory_change_tool as rct
import rss_ingest_service
from auth_endpoints import get_current_user, require_screen_permission

logger = logging.getLogger("ubo.regulatory_change")

router = APIRouter(prefix="/regulatory-change", tags=["Regulatory Change Management"],
                    dependencies=[Depends(require_screen_permission("regchange"))])

# Feeds this scan targets — the four horizon-scanning-oriented feeds added to
# rss_ingest_service.FEEDS alongside this feature, not the pre-existing
# company-gated ones (BIS/CISA/SEC/Fed/EPA), which already have their own
# per-article scoring path and aren't naturally "one text to version."
_SCAN_FEED_IDS = ("eu_ai_act", "dora", "nis2", "state_privacy")

_MAX_ENTRIES_PER_SNAPSHOT = 20

_SYSTEM_PROMPT = (
    "You review a diff between two snapshots of a regulatory monitoring feed "
    "(new items or changed descriptions from an EU/US regulator or standards body). "
    "Given the diff and the name of an internal control register, write:\n"
    "  SUMMARY: <one paragraph, what changed and why it matters for compliance>\n"
    "  CONTROL_REF: <a short control reference id this maps to, e.g. CM-02, or NEW if none fits>\n"
    "  CONTROL_NAME: <a short control name, <=80 chars>\n"
    "  CONTROL_EDIT: <1-3 sentences to add/update in that control's description>\n\n"
    "Output ONLY those four labeled lines — no other text, no markdown fences."
)


def _fetch_source_text(feed: dict) -> tuple[str, str]:
    """(title, text) for a feed's current content — its entries' titles and
    summaries concatenated, most recent _MAX_ENTRIES_PER_SNAPSHOT. Reuses
    rss_ingest_service's own raw fetch rather than a second HTTP client."""
    raw = rss_ingest_service._fetch_raw(feed["url"])
    if raw is None:
        return feed["name"], ""
    parsed = feedparser.parse(raw)
    lines = []
    for entry in parsed.entries[:_MAX_ENTRIES_PER_SNAPSHOT]:
        title = (getattr(entry, "title", "") or "").strip()
        summary = (getattr(entry, "summary", "") or getattr(entry, "description", "") or "").strip()
        if title or summary:
            lines.append(f"{title}\n{summary}")
    return feed["name"], "\n\n".join(lines)


def _draft_proposal(feed_label: str, diff_text: str) -> tuple[str, str, dict]:
    """Returns (control_ref, control_name, proposed_edit dict). Falls back to
    a plain templated proposal (still fully reviewable — nothing is silently
    dropped) if the LLM call fails."""
    user = f"Feed: {feed_label}\n\nDiff:\n{diff_text[:8000]}"
    try:
        text, _stop = claude_client.complete_text_meta(
            _SYSTEM_PROMPT, user, label="regulatory_change_proposal", effort="low", max_tokens=800,
        )
        fields = {}
        for line in text.splitlines():
            for key in ("SUMMARY", "CONTROL_REF", "CONTROL_NAME", "CONTROL_EDIT"):
                prefix = f"{key}:"
                if line.strip().startswith(prefix):
                    fields[key] = line.strip()[len(prefix):].strip()
        if fields.get("CONTROL_EDIT"):
            control_ref = (fields.get("CONTROL_REF") or "NEW").strip() or "NEW"
            return (
                None if control_ref.upper() == "NEW" else control_ref,
                fields.get("CONTROL_NAME") or feed_label,
                {"summary": fields.get("SUMMARY", ""), "description": fields["CONTROL_EDIT"]},
            )
    except Exception as exc:
        logger.warning("regulatory_change: LLM proposal draft failed for %s: %s", feed_label, exc)

    return None, feed_label, {
        "summary": f"Content change detected on {feed_label} — see diff for details.",
        "description": f"Review the {feed_label} monitoring feed for a possible new or updated regulatory obligation.",
    }


@router.post("/scan")
def scan(current_user: Dict[str, Any] = Depends(get_current_user)):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    results = []
    for feed_id in _SCAN_FEED_IDS:
        feed = rss_ingest_service.FEEDS_BY_ID.get(feed_id)
        if not feed:
            continue
        title, text = _fetch_source_text(feed)
        if not text:
            results.append({"feed_id": feed_id, "status": "fetch_failed"})
            continue

        prior = db.get_latest_regulatory_change_version(feed_id, feed["url"])
        prior_text = prior["fetched_text"] if prior else None
        new_hash = rct.content_hash(text)

        if prior and prior["sha256"] == new_hash:
            results.append({"feed_id": feed_id, "status": "unchanged"})
            continue

        if not rct.is_material_change(prior_text, text):
            # Store the snapshot (so the next scan compares against THIS
            # text, not the stale one before it) but skip drafting a
            # proposal — boilerplate churn shouldn't reach the review queue.
            db.save_regulatory_change_version(feed_id, feed["url"], title, text, new_hash,
                                               prior["id"] if prior else None)
            results.append({"feed_id": feed_id, "status": "minor_change_stored"})
            continue

        version_id = db.save_regulatory_change_version(
            feed_id, feed["url"], title, text, new_hash, prior["id"] if prior else None,
        )
        diff_text = rct.diff_summary(prior_text, text)
        control_ref, control_name, proposed_edit = _draft_proposal(feed["name"], diff_text)
        proposed_edit["name"] = control_name
        proposal_id = db.save_regulatory_change_proposal(version_id, diff_text, control_ref, proposed_edit)
        results.append({"feed_id": feed_id, "status": "proposal_created", "version_id": version_id,
                         "proposal_id": proposal_id})
        logger.info("regulatory_change: scan created proposal %s for feed %s", proposal_id, feed_id)

    return {"results": results}


@router.get("/versions")
def get_versions(feed_id: Optional[str] = None, limit: int = Query(100, ge=1, le=500)):
    if not db.is_available():
        return {"versions": []}
    return {"versions": db.list_regulatory_change_versions(feed_id=feed_id, limit=limit)}


@router.get("/proposals")
def get_proposals(status: Optional[str] = None, limit: int = Query(100, ge=1, le=500)):
    if not db.is_available():
        return {"proposals": []}
    return {"proposals": db.list_regulatory_change_proposals(status=status, limit=limit)}


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: int):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    proposal = db.get_regulatory_change_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"No proposal with id={proposal_id}")
    return proposal


def _display_name(user: dict) -> str:
    return user.get("display_name") or user.get("username") or f"User {user.get('id')}"


@router.post("/proposals/{proposal_id}/decision")
def decide_proposal(
    proposal_id: int,
    body: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = Depends(require_screen_permission("regchange", edit=True)),
):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    decision = body.get("decision")
    notes = body.get("review_notes")
    if decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="decision must be 'approved' or 'rejected'")

    proposal = db.get_regulatory_change_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"No proposal with id={proposal_id}")

    updated = db.record_regulatory_change_proposal_decision(proposal_id, decision, _display_name(current_user), notes)
    if not updated:
        raise HTTPException(status_code=409, detail="Proposal was already decided")

    if decision == "approved":
        edit = proposal.get("proposed_edit") or {}
        control_ref = proposal.get("proposed_control_ref") or f"REG-{proposal_id}"
        db.upsert_control({
            "ref": control_ref,
            "name": edit.get("name") or f"Regulatory control {control_ref}",
            "description": edit.get("description") or edit.get("summary") or "",
            "framework": "Regulatory",
            "category": "Compliance",
            "domain": "Legal",
        })
        logger.info("regulatory_change: proposal %s approved, applied to controls_library[%s]", proposal_id, control_ref)

    return {"proposal": updated}
