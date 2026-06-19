#!/usr/bin/env python3
"""
Peer intelligence from the target's own 10-K.

Instead of pulling every company that shares a 4-digit SIC code (noisy, and many
are shells with no XBRL data), this module reads the competitors the target
*names itself* in its latest 10-K "Competition" discussion, resolves them to
EDGAR CIK/ticker, and hands them back for financial enrichment. Companies that
can't be resolved or have no financial data are dropped by the caller.

Falls back gracefully: if no language model is configured, extract_competitor_names
returns [] and the caller uses SIC peers instead.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from edgar_tool import (
    get_company_info,
    parse_filings,
    fetch_filing_text,
    _extract_section,
    _get_safe,
    EDGAR_BASE,
)

logger = logging.getLogger(__name__)

# Corporate suffixes / filler stripped before fuzzy-matching company names.
_SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|companies|ltd|limited|plc|"
    r"llc|lp|holdings?|group|technologies|technology|systems|semiconductor|"
    r"semiconductors|international|industries|labs?|laboratories|the|and|&)\b",
    re.IGNORECASE,
)
_NONWORD = re.compile(r"[^a-z0-9 ]+")


def _norm(name: str) -> str:
    s = (name or "").lower()
    s = _NONWORD.sub(" ", s)
    s = _SUFFIXES.sub(" ", s)
    return " ".join(s.split())


# ── Competitor name extraction (LLM over the 10-K Competition section) ──────────

def extract_competitor_names(ticker: str, meta: Optional[dict] = None, sub: Optional[dict] = None) -> list[str]:
    """
    Read the target's latest 10-K, isolate the Competition discussion, and use the
    language model to extract the named competitor companies. Returns [] when no
    model is configured or nothing is found (caller falls back to SIC peers).
    """
    try:
        import claude_client
    except Exception:
        return []
    if not claude_client.is_available():
        return []

    if meta is None or sub is None:
        meta, sub = get_company_info(ticker)
    tens = parse_filings(sub, {"10-K"}).get("10-K", [])
    if not tens:
        return []
    text = fetch_filing_text(meta["cik"], tens[0])
    if not text:
        return []

    # Pull the Competition section (Item 1 Business). Fall back to the head of the
    # business narrative if no explicit heading is found.
    section = _extract_section(
        text,
        start_patterns=[r"\bcompetition\b", r"\bcompetitors?\b", r"competitive landscape", r"we compete"],
        stop_patterns=[r"item\s*1a", r"risk factors", r"human capital", r"\bemployees\b",
                       r"intellectual property", r"government regulation"],
        max_chars=18_000,
    ) or text[:18_000]

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "competitors": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["competitors"],
    }
    system = (
        "You extract named competitor companies from a SEC 10-K Competition section. "
        "Return only real, publicly identifiable company names the filer names as competitors "
        "(e.g. 'Texas Instruments', 'Infineon', 'NXP Semiconductors'). Exclude the filer itself, "
        "generic phrases ('larger competitors', 'numerous companies'), products, and customers. "
        "Use the company's common name, not legal suffixes."
    )
    user = (
        f"Filer: {meta.get('company_name')} ({ticker.upper()}).\n\n"
        f"Competition section:\n{section}\n\n"
        "List the named competitor companies."
    )
    try:
        result = claude_client.complete_json(system, user, schema, label="competitors", effort="medium", max_tokens=2000)
    except Exception as exc:
        logger.info("competitor extraction failed: %s", exc)
        return []

    names, seen = [], set()
    target_norm = _norm(meta.get("company_name", "")) or _norm(ticker)
    for n in result.get("competitors", []):
        n = (n or "").strip()
        if not n:
            continue
        key = _norm(n)
        if not key or key == target_norm or key in seen:
            continue
        seen.add(key)
        names.append(n)
    return names[:25]


# ── EDGAR resolution (competitor name → CIK/ticker) ─────────────────────────────

_TICKER_INDEX: Optional[list[tuple[str, dict]]] = None


def _ticker_index() -> list[tuple[str, dict]]:
    """[(normalized_title, {ticker, cik_plain, cik, company_name}), ...] from EDGAR."""
    global _TICKER_INDEX
    if _TICKER_INDEX is not None:
        return _TICKER_INDEX
    resp = _get_safe(f"{EDGAR_BASE}/files/company_tickers.json")
    idx: list[tuple[str, dict]] = []
    if resp is not None:
        for v in resp.json().values():
            title = v.get("title", "")
            cik_int = v.get("cik_str")
            if not title or cik_int is None:
                continue
            idx.append((_norm(title), {
                "ticker": v.get("ticker", ""),
                "cik_plain": str(cik_int),
                "cik": str(cik_int).zfill(10),
                "company_name": title,
            }))
    _TICKER_INDEX = idx
    return idx


def resolve_names_to_edgar(names: list[str], exclude_cik: str = "") -> list[dict]:
    """
    Match competitor names to EDGAR-registered companies. Unmatched names are
    dropped (a company with no EDGAR registration has no financial data anyway).
    """
    index = _ticker_index()
    if not index:
        return []
    exclude = re.sub(r"\D", "", exclude_cik or "")
    out, used = [], set()

    for raw in names:
        target = _norm(raw)
        if not target:
            continue
        best, best_score = None, 0.0
        for norm_title, entry in index:
            if not norm_title:
                continue
            # Strong signal: one name contains the other (token-bounded).
            if target == norm_title:
                score = 1.0
            elif f" {target} " in f" {norm_title} " or f" {norm_title} " in f" {target} ":
                score = 0.92
            else:
                score = SequenceMatcher(None, target, norm_title).ratio()
            if score > best_score:
                best, best_score = entry, score
        if best and best_score >= 0.82:
            if best["cik_plain"] == exclude or best["cik_plain"] in used:
                continue
            used.add(best["cik_plain"])
            out.append({**best, "matched_from": raw})
    return out
