#!/usr/bin/env python3
"""
Entity linking (Stage 3 of the ontology plan) — free-text risk/control names
-> concept_id, via the same EMBT_CONCEPT ANN space Stage 2 already built.

Honesty rules (per framework_mappings.py's guardrail, applied here):
  - A row is ALWAYS written to concept_links, even when nothing resolves.
    Silence would be indistinguishable from "linking never ran".
  - Only status='confirmed' (a human reviewed it) is authoritative. 'proposed'
    must never be treated as ground truth by any downstream aggregate.
  - This module writes ALONGSIDE the existing free-text vocabulary assignment
    (risk_register_endpoints.py's _keyword_domain / assigned_domain) and does
    not touch it — disagreement between them is meant to be visible, not
    silently resolved.

Router prefix: /ontology (shares the prefix with ontology_export.py and
ontology_endpoints.py — distinct paths, so all three mount without collision)
    POST /ontology/link         link one free-text subject to its nearest
                                concept in a scheme
    GET  /ontology/links        look up existing links for one subject
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

import db
import embedding_util

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ontology")

# NIST IR 8477 gives no numeric thresholds — these bands are this app's own
# calibration, subject to the plan's S-4 sign-off gate once real link data
# exists to check them against.
DISTANCE_PROPOSED = 0.25    # d <= this: confident match
DISTANCE_AMBIGUOUS = 0.45   # this < d <= DISTANCE_AMBIGUOUS: match, but flag the runner-up
# d > DISTANCE_AMBIGUOUS: no match — write 'unresolved', never snap to nearest-but-wrong


def link_entity(source_table: str, source_id: str, scheme: str, text: str, *, method: str = "ann") -> dict:
    """Link one free-text subject to its nearest concept in `scheme`. Always
    writes a concept_links row (see module docstring); returns it. Degrades to
    an 'unresolved' write (never an exception) when embedding is unavailable,
    matching every other EMBT_* caller's best-effort contract."""
    if not text or not text.strip():
        return db.upsert_concept_link(
            source_table, source_id, scheme,
            concept_id=None, status="unresolved", method=method,
        )
    if not embedding_util.is_available():
        return db.upsert_concept_link(
            source_table, source_id, scheme,
            concept_id=None, status="unresolved", method=method,
        )
    vec = embedding_util.embed_text(text)
    if not vec:
        return db.upsert_concept_link(
            source_table, source_id, scheme,
            concept_id=None, status="unresolved", method=method,
        )
    candidates = db.search_concepts_by_embedding(vec, scheme=scheme, limit=2)
    if not candidates or candidates[0].get("distance") is None or candidates[0]["distance"] > DISTANCE_AMBIGUOUS:
        return db.upsert_concept_link(
            source_table, source_id, scheme,
            concept_id=None, status="unresolved", method=method,
        )

    best = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None
    confidence = 1.0 - best["distance"]
    link = db.upsert_concept_link(
        source_table, source_id, scheme,
        concept_id=best["concept_id"], status="proposed", confidence=confidence,
        method=method, runner_up_concept_id=(runner_up or {}).get("concept_id"),
    )

    item_ref = f"{source_table}:{source_id}:{scheme}"
    db.upsert_concept_link_task(
        item_ref, best.get("pref_label"),
        {
            "concept_id": best["concept_id"], "pref_label": best.get("pref_label"),
            "confidence": confidence,
            "runner_up_concept_id": (runner_up or {}).get("concept_id"),
            "ambiguous": best["distance"] > DISTANCE_PROPOSED,
        },
    )
    return link


class LinkRequest(BaseModel):
    source_table: str
    source_id: str
    scheme: str
    text: str
    method: str = "ann"


@router.post("/link")
def link_endpoint(req: LinkRequest):
    """Link one free-text subject (a risk name, a control description, ...)
    to its nearest concept. Best-effort — never errors on a missing
    embedding provider, just writes 'unresolved'."""
    link = link_entity(req.source_table, req.source_id, req.scheme, req.text, method=req.method)
    return {"link": link}


@router.get("/links")
def get_links_endpoint(source_table: str, source_id: str):
    """Every link proposal recorded for one subject, across methods."""
    return {"links": db.get_concept_links(source_table, source_id)}
