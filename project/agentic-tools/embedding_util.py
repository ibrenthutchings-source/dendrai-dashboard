#!/usr/bin/env python3
"""
Shared OpenAI embedding client for the Dendrai Intelligenza agentic layer.

Every call site that writes to the pgvector `embeddings` table (db.py's
save_embedding / save_embeddings_bulk) should compute its vector through
_embed_text()/_embed_and_chunk() here, rather than hand-rolling its own OpenAI
client or — the bug this module was extracted to fix — passing raw text
straight into db.save_embedding(), which only accepts a precomputed vector.

Activation:
    Set OPENAI_API_KEY in the environment (or project/agentic-tools/.env).
    Without it, embed_text() returns None and callers must degrade gracefully
    (embeddings are always a best-effort enrichment, never load-bearing).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

EMBED_MODEL = "text-embedding-3-small"

_openai_client = None
_checked = False


def _get_client():
    global _openai_client, _checked
    if _checked:
        return _openai_client
    _checked = True
    try:
        import openai  # optional dependency; pip install openai
        key = os.environ.get("OPENAI_API_KEY", "")
        if key:
            _openai_client = openai.OpenAI(api_key=key)
    except ImportError:
        pass
    return _openai_client


def is_available() -> bool:
    return _get_client() is not None


def embed_text(text: str) -> Optional[list]:
    """Return a text-embedding-3-small vector, or None when OpenAI is unavailable
    or the call fails. Never raises."""
    if not text:
        return None
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.embeddings.create(model=EMBED_MODEL, input=text[:8191])
        return resp.data[0].embedding
    except Exception as exc:
        logger.warning("embedding failed: %s", exc)
        return None


def chunk_text(text: str, chunk_chars: int = 600, overlap: int = 80) -> "list[str]":
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
