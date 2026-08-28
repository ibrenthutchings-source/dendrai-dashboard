#!/usr/bin/env python3
"""
Concept embeddings (Stage 2 of the ontology plan) — the point where pgvector
starts serving the concept layer rather than just document similarity:
concepts (db.py's `concepts` table) get embedded into the SAME vector space
as every other EMBT_* content type, via the same shared embedding_util client
everything else uses. No second embedding pipeline, no second index.

Router prefix: /ontology (shares the prefix with ontology_export.py's Turtle
export — distinct paths, so both routers mount without collision)
    POST /ontology/reembed   re-embed any concept whose label/definition/
                             alt_labels changed since it was last embedded
    POST /ontology/search    "which concept is this text about" — nearest
                             EMBT_CONCEPT neighbours to a free-text query
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


def _concept_embedding_text(concept: dict) -> str:
    """The exact string embedded for a concept — pref_label + definition,
    plus alt_labels so a synonym alone can resolve to the right concept via
    plain ANN similarity (no separate synonym-matching code needed)."""
    text = f"{concept['pref_label']} — {concept.get('definition') or ''}"
    alt_labels = concept.get("alt_labels") or []
    if alt_labels:
        text += f"\nAlso: {', '.join(alt_labels)}"
    return text.strip()


def embed_concept(concept: dict) -> bool:
    """Embed one concept (EMBT_CONCEPT) and save it, stamped with its current
    label_hash so reembed_stale_concepts() can detect future drift. Best
    effort — returns False (never raises) when OPENAI_API_KEY isn't
    configured or the embedding call fails, same degrade-gracefully contract
    every other EMBT_* writer follows."""
    if not embedding_util.is_available():
        return False
    text = _concept_embedding_text(concept)
    vec = embedding_util.embed_text(text)
    if not vec:
        return False
    saved = db.save_embedding(
        source_table="concepts", source_id=concept["id"], content_type=db.EMBT_CONCEPT,
        embedding=vec, company_id=None, text_snippet=text[:600],
        source_hash=concept.get("label_hash"),
    )
    return saved is not None


def reembed_stale_concepts(limit: int = 200) -> dict:
    """Find every concept whose label_hash no longer matches its stored
    embedding's source_hash (including concepts never embedded at all — an
    absent hash never matches a real one) and re-embed them. db.save_embedding
    upserts on (source_table, source_id, content_type, model, chunk_index),
    so this is always a plain overwrite, never a delete-then-insert."""
    concepts = db.list_concepts()
    existing_hashes = db.get_concept_embedding_hashes()
    stale = [c for c in concepts if existing_hashes.get(c["id"]) != c.get("label_hash")][:limit]
    embedded = 0
    for c in stale:
        if embed_concept(c):
            embedded += 1
    return {"checked": len(concepts), "stale": len(stale), "embedded": embedded}


@router.post("/reembed")
def reembed_endpoint():
    """Re-embed any concept whose content changed since it was last embedded."""
    if not embedding_util.is_available():
        return {"checked": 0, "stale": 0, "embedded": 0,
                "note": "OPENAI_API_KEY not configured — concept embedding unavailable"}
    return reembed_stale_concepts()


class ConceptSearchRequest(BaseModel):
    text: str
    scheme: Optional[str] = None
    limit: int = 10


@router.post("/search")
def search_endpoint(req: ConceptSearchRequest):
    """Which concept(s) is this free text about — nearest EMBT_CONCEPT
    neighbours by cosine similarity, optionally scoped to one scheme."""
    if not embedding_util.is_available():
        return {"results": [], "count": 0,
                "note": "OPENAI_API_KEY not configured — concept search unavailable"}
    if not req.text.strip():
        return {"results": [], "count": 0}
    vec = embedding_util.embed_text(req.text)
    if not vec:
        return {"results": [], "count": 0, "note": "Embedding failed — see server logs"}
    results = db.search_concepts_by_embedding(vec, scheme=req.scheme, limit=req.limit)
    return {"results": results, "count": len(results)}
