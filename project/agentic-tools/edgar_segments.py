#!/usr/bin/env python3
"""
XBRL dimensional segment/geography extraction — Dendrai Intelligenza

Pulls business-segment and geographic revenue breakdowns straight from a
filing's XBRL instance document — NOT the data.sec.gov `companyfacts` API,
which only exposes default-member facts and silently drops every
dimensional breakdown a filer reports under ASC 280. The instance document
(`*_htm.xml` in the filing's own archive directory) still carries it in
full; `edgar_tool.py` already has the fetch path to reach it, this module
just walks the XBRL context/fact structure once it's there.

Feeds the `sox_financial_segments` table (db.py) so the Risk Coverage Cube
(see plan, Phase 3) has a real operating-unit axis instead of an
always-collapsed "Consolidated" placeholder.

Verified end-to-end against ON Semiconductor's 2026-08-03 10-Q (accession
0001097864-26-000017): extracted 3 business segments (Power Solutions
Group $829.0M / 51.7%, Analog & Mixed-Signal $545.7M / 34.0%, Intelligent
Sensing $228.8M / 14.3%) and 5 geographies (HK/SG/GB/US/Other), matching
the filed figures — see test_edgar_segments.py.

Usage:
    from edgar_segments import fetch_segments
    result = fetch_segments("ON")
"""

from __future__ import annotations

from typing import Any, Optional
from xml.etree import ElementTree as ET

from edgar_tool import get_company_info, parse_filings, _filing_index, _get_safe, EDGAR_BASE

# ── XBRL namespaces ──────────────────────────────────────────────────────────
_NS = {
    "xbrli":  "http://www.xbrl.org/2003/instance",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
}
_EXPLICIT_MEMBER_TAG = "{http://xbrl.org/2006/xbrldi}explicitMember"

# The two dimensional axes this module understands. These are the standard
# US-GAAP/SRT taxonomy axes essentially every filer with reportable segments
# uses under ASC 280 — extend here if a real filing needs a third, not by
# guessing at a filer-specific extension axis.
_SEGMENT_AXES = {
    "us-gaap:StatementBusinessSegmentsAxis": "business_segment",
    "srt:StatementGeographicalAxis":         "geography",
}

# Ordered by taxonomy era, not by a "nicer" number — Excluding/Including
# AssessedTax are the ASC 606 (post-2018) tags most current filers use;
# plain Revenues and the pre-2009 SalesRevenueNet are fallbacks for filers
# still on older tags. First tag to report a given (axis, member, period)
# wins; a later, less-preferred tag never overwrites an already-captured
# value for that same combination.
_REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]

# How far a segment breakdown's total may drift from the filing's own
# consolidated revenue figure before it's flagged as unreconciled rather
# than silently trusted — dimensional facts occasionally include an
# "Other"/elimination bucket that shifts the sum slightly.
_RECONCILIATION_TOLERANCE_PCT = 2.0


# ─────────────────────────────────────────────────────────────────────────────
# Instance document location + fetch
# ─────────────────────────────────────────────────────────────────────────────

def _find_instance_doc(cik: str, accession_number: str) -> Optional[str]:
    """Return the filename of the inline-XBRL instance document (the
    `*_htm.xml` file — not its _cal/_def/_lab/_pre/.xsd siblings), or None
    if the filing has no XBRL instance at all (pre-XBRL-mandate filing)."""
    items = _filing_index(cik, accession_number)
    candidates = [
        it["name"] for it in items
        if it.get("name", "").lower().endswith("_htm.xml")
    ]
    return candidates[0] if candidates else None


def _fetch_instance_xml(cik: str, accession_number: str, doc_name: str) -> Optional[str]:
    cik_int = int(cik)
    acc_clean = accession_number.replace("-", "")
    url = f"{EDGAR_BASE}/Archives/edgar/data/{cik_int}/{acc_clean}/{doc_name}"
    r = _get_safe(url)
    return r.text if r is not None else None


# ─────────────────────────────────────────────────────────────────────────────
# Pure parsing — no network access, directly unit-testable
# ─────────────────────────────────────────────────────────────────────────────

def _context_dims(context_el: ET.Element) -> dict[str, str]:
    """{axis_type: member} for a context — restricted to the two axes this
    module understands. Uses .iter() rather than a fixed entity/segment
    path: some filers nest dimensional members differently, and .iter()
    finds explicitMember regardless of the exact ancestor chain."""
    dims: dict[str, str] = {}
    for m in context_el.iter(_EXPLICIT_MEMBER_TAG):
        axis = m.get("dimension", "")
        if axis in _SEGMENT_AXES:
            dims[_SEGMENT_AXES[axis]] = (m.text or "").strip()
    return dims


def _context_period(context_el: ET.Element) -> tuple[str, str]:
    per = context_el.find("xbrli:period", _NS)
    if per is None:
        return "", ""
    start = per.findtext("xbrli:startDate", default="", namespaces=_NS)
    end = (per.findtext("xbrli:endDate", default="", namespaces=_NS)
           or per.findtext("xbrli:instant", default="", namespaces=_NS))
    return start, end


def _clean_member_name(member: str) -> str:
    """'us-gaap:PowerSolutionsGroupMember' -> 'PowerSolutionsGroup';
    'country:HK' -> 'HK' (ISO-3166 — caller maps to a display name;
    business-segment names are filer-specific and need the label linkbase
    for a human-readable form, tracked as a follow-up, not done here)."""
    name = member.split(":")[-1]
    if name.endswith("Member"):
        name = name[: -len("Member")]
    return name


def extract_segments_from_xml(xml_text: str) -> dict[str, Any]:
    """Pure function over an already-fetched instance document — the part
    covered by the regression test against ON Semi's known-good figures,
    with no network access needed to re-run it."""
    root = ET.fromstring(xml_text)

    # contextRef -> (axis_type, member, start, end), restricted to contexts
    # carrying EXACTLY one of the two segment axes. A context with two
    # dimensions (e.g. segment x geography cross-tab, or segment x
    # ConsolidationItemsAxis) is a finer breakdown that would double-count
    # against a single-axis segment total if summed alongside it — those
    # are skipped, not netted out.
    single_axis_ctx: dict[str, tuple[str, str, str, str]] = {}
    # contextRef -> (start, end), for contexts with NO dimension at all —
    # the true consolidated total to reconcile against.
    no_dim_ctx: dict[str, tuple[str, str]] = {}

    for c in root.findall("xbrli:context", _NS):
        cid = c.get("id")
        if not cid:
            continue
        dims = _context_dims(c)
        has_any_member = next(c.iter(_EXPLICIT_MEMBER_TAG), None) is not None
        if not has_any_member:
            start, end = _context_period(c)
            if start and end:
                no_dim_ctx.setdefault(cid, (start, end))
            continue
        if len(dims) != 1:
            continue  # multi-axis cross-tab, or a dimension we don't track
        start, end = _context_period(c)
        (axis_type, member), = dims.items()
        single_axis_ctx[cid] = (axis_type, member, start, end)

    # (axis_type, start, end) -> {member: value}
    buckets: dict[tuple[str, str, str], dict[str, float]] = {}
    # (start, end) -> consolidated revenue value
    consolidated_revenue: dict[tuple[str, str], float] = {}

    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in _REVENUE_TAGS or not el.text:
            continue
        cid = el.get("contextRef")
        try:
            val = float(el.text) * (10 ** int(el.get("scale") or 0))
        except ValueError:
            continue

        if cid in single_axis_ctx:
            axis_type, member, start, end = single_axis_ctx[cid]
            key = (axis_type, start, end)
            buckets.setdefault(key, {})
            buckets[key].setdefault(member, val)  # first (best) tag wins
        elif cid in no_dim_ctx:
            period = no_dim_ctx[cid]
            consolidated_revenue.setdefault(period, val)

    breakdowns = []
    for (axis_type, start, end), members in buckets.items():
        if len(members) < 2:
            continue  # not actually a breakdown
        total = sum(members.values())
        cons_total = consolidated_revenue.get((start, end))
        reconciled = None
        if cons_total:
            diff_pct = abs(total - cons_total) / cons_total * 100
            reconciled = diff_pct <= _RECONCILIATION_TOLERANCE_PCT
        breakdowns.append({
            "segment_type": axis_type,
            "period_start": start,
            "period_end": end,
            "consolidated_revenue": cons_total,
            "reconciled": reconciled,
            "members": [
                {
                    "segment_name": _clean_member_name(member),
                    "raw_member": member,
                    "revenue": value,
                    "revenue_pct": round(value / total * 100, 2) if total else None,
                }
                for member, value in sorted(members.items(), key=lambda kv: -kv[1])
            ],
        })

    return {"breakdowns": breakdowns, "extracted": bool(breakdowns)}


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end: ticker -> latest filing -> extraction
# ─────────────────────────────────────────────────────────────────────────────

def fetch_segments(ticker: str, form_types: Optional[set[str]] = None) -> dict[str, Any]:
    """Resolve the ticker's most recent 10-K/10-Q, fetch its XBRL instance,
    extract segment/geography revenue. Returns an honest
    {"extracted": False, "reason": ...} rather than a partial or zeroed
    result when nothing dimensional is found — a filer with no reportable
    segments legitimately has nothing to extract, and that's a different,
    equally honest outcome from a fetch/parse failure; both are reported,
    neither is papered over with a silent zero.
    """
    form_types = form_types or {"10-K", "10-Q"}
    base: dict[str, Any] = {"extracted": False, "ticker": ticker.upper(), "breakdowns": []}
    try:
        meta, sub = get_company_info(ticker)
    except ValueError as e:
        return {**base, "reason": str(e)}

    filings = parse_filings(sub, form_types)
    candidates = sorted(
        (f for forms in filings.values() for f in forms),
        key=lambda f: f["date"], reverse=True,
    )
    if not candidates:
        return {**base, "reason": f"No {'/'.join(sorted(form_types))} filings found for {ticker}"}

    filing = candidates[0]
    base.update({
        "fiscal_year": filing["date"][:4],
        "accession_number": filing["accession_number"],
        "source_form": filing["form"],
    })
    cik = meta["cik_plain"]
    doc_name = _find_instance_doc(cik, filing["accession_number"])
    if not doc_name:
        return {**base, "reason": "No XBRL instance document in this filing (pre-XBRL-mandate filing?)"}

    xml_text = _fetch_instance_xml(cik, filing["accession_number"], doc_name)
    if not xml_text:
        return {**base, "reason": "Failed to fetch the XBRL instance document"}

    result = {**base, **extract_segments_from_xml(xml_text)}
    if not result["extracted"]:
        result["reason"] = "No dimensional segment/geography revenue facts found in this filing"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Persistence — sox_financial_segments (db.py already has the upsert)
# ─────────────────────────────────────────────────────────────────────────────

def persist_segments(ticker: str, form_types: Optional[set[str]] = None) -> dict[str, Any]:
    """fetch_segments() + persist the reconciled breakdowns to
    sox_financial_segments via the existing db.upsert_sox_segment (already
    used by the manual /sox/segments/{ticker} entry path — same table, same
    upsert, source='filed' instead of 'manual').

    Only the MOST RECENT period per axis is persisted (the "current
    actuals" the coverage cube's Z-axis needs) — the extractor finds prior-
    year/YTD comparatives too, but the segments table is keyed one row per
    (company, fiscal_year, segment_type, segment_name) and isn't a time
    series; persisting comparatives here would just churn the same
    fiscal_year key. Historical trending is a separate, later concern.

    An UNRECONCILED breakdown is never persisted — writing data this module
    itself doesn't trust would make a mapped-but-wrong figure indistinguishable
    from a verified one downstream. It's still returned in the response
    (visible, just not saved) so the caller can see what was found and why
    it was skipped.
    """
    import db  # local import: matches this file's other DB-adjacent modules'
               # convention of not hard-importing db at module load time,
               # so extract_segments_from_xml() stays usable with zero DB
               # configured (as the pure-function test suite requires).

    result = fetch_segments(ticker, form_types)
    result["persisted"] = []
    result["skipped"] = []
    if not result["extracted"] or not db.is_available():
        return result

    meta, _ = get_company_info(ticker)
    company_id = db.upsert_company(meta)
    if not company_id:
        result["skipped"].append({"reason": "Could not resolve/create a company record"})
        return result

    latest_by_axis: dict[str, dict] = {}
    for b in result["breakdowns"]:
        axis = b["segment_type"]
        if axis not in latest_by_axis or b["period_end"] > latest_by_axis[axis]["period_end"]:
            latest_by_axis[axis] = b

    for axis, b in latest_by_axis.items():
        if not b["reconciled"]:
            result["skipped"].append({
                "segment_type": axis, "period_end": b["period_end"],
                "reason": "Breakdown does not reconcile to consolidated revenue within tolerance",
            })
            continue
        for m in b["members"]:
            db.upsert_sox_segment(company_id, None, {
                "fiscal_year": result["fiscal_year"],
                "segment_type": axis,
                "segment_name": m["segment_name"],
                "revenue": m["revenue"],
                "revenue_pct": m["revenue_pct"],
                "source": "filed",
            })
            result["persisted"].append({"segment_type": axis, "segment_name": m["segment_name"]})

    return result
