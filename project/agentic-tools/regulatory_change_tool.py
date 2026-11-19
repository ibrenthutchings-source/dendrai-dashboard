#!/usr/bin/env python3
"""
Regulatory change detection — diff a regulatory source's text against the
last version this system saw, and decide whether the delta is worth a
human's attention.

The gap this closes: rss_ingest_service.py already fetches and scores
regulatory feed articles (grade_article), but it has no notion of "this is
the SAME underlying regulation, and its text just changed" — every fetch
either dedups on (title, feed_name) or is scored as a brand-new item. Nothing
diffs two fetches of the same source_url against each other, so a real
change to (say) the EU AI Act's guidance page is invisible unless the title
itself changes. regulatory_change_endpoints.py's scan step is the producer
of the two text snapshots this module compares; db.py's
regulatory_change_versions table (modeled on pac_policy_documents' sha256
dedup pattern) is where "have we already stored this exact text" is decided
before a diff is even computed.

Deliberately pure — no DB, no HTTP, no LLM call. Two responsibilities:
    content_hash(text)                          -> stable hash for dedup
    is_material_change(old_text, new_text)      -> filter boilerplate churn
    diff_summary(old_text, new_text)             -> the actual unified diff

    from regulatory_change_tool import content_hash, is_material_change, diff_summary
"""

from __future__ import annotations

import difflib
import hashlib

# Below this fraction-changed threshold, a diff is treated as boilerplate
# churn (a "last updated" timestamp footer, a nav-menu tweak) rather than a
# real regulatory-text change — same "don't fire a proposal on every
# re-fetch" reasoning pac_policy_docs.py's sha256 dedup applies at the
# document level; this applies it at the sub-document (per-fetch) level.
_MATERIAL_CHANGE_RATIO = 0.02

# A unified diff over a full regulation's text can run to megabytes; this is
# a generous cap for what a reviewer or an LLM-drafted summary actually
# needs to see, same order of magnitude as pac_policy_docs.py's
# CONVERSION_INPUT_CHARS bound on LLM input.
_MAX_DIFF_CHARS = 20_000


def content_hash(text: str) -> str:
    """Stable hash of a fetched source's text — the same role
    pac_policy_documents.sha256 plays for uploaded policy documents:
    "have we already stored this exact text" without a full-text compare."""
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def is_material_change(old_text: str, new_text: str, min_ratio_delta: float = _MATERIAL_CHANGE_RATIO) -> bool:
    """True if new_text differs from old_text by at least min_ratio_delta
    (fraction of content changed, via difflib's real quick-ratio, not a
    naive length compare). old_text=None/empty means "first time we've seen
    this source" — always material, there's nothing to compare against."""
    if not old_text:
        return bool(new_text)
    if old_text == new_text:
        return False
    sm = difflib.SequenceMatcher(None, old_text, new_text, autojunk=False)
    fraction_changed = 1 - sm.ratio()
    return fraction_changed >= min_ratio_delta


def diff_summary(old_text: str, new_text: str, context: int = 2) -> str:
    """Unified diff (old -> new), line-based, capped to _MAX_DIFF_CHARS.
    Empty old_text produces a diff that's entirely '+' lines — correct for
    "first time seeing this source", not a special case."""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines, fromfile="previous", tofile="current", lineterm="", n=context,
    ))
    text = "\n".join(diff_lines)
    if len(text) > _MAX_DIFF_CHARS:
        text = text[:_MAX_DIFF_CHARS] + "\n… (diff truncated)"
    return text
