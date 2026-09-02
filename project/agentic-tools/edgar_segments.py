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

from edgar_tool import get_company_info, parse_filings, _filing_index, _get_safe, EDGAR_BASE, fetch_xbrl_facts

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


def extract_segments_from_xml(xml_text: str, tags: Optional[list[str]] = None) -> dict[str, Any]:
    """Pure function over an already-fetched instance document — the part
    covered by the regression test against ON Semi's known-good figures,
    with no network access needed to re-run it.

    `tags`: candidate us-gaap tag names to match, tried in the order given
    (mirrors edgar_tool.XBRL_METRICS's "first working tag wins" convention).
    Defaults to `_REVENUE_TAGS` for backward compatibility — every existing
    caller (fetch_segments/persist_segments/fetch_segment_history, all
    revenue-only today) is unaffected by this parameter's addition.

    The output shape keeps calling its value fields "revenue"/
    "consolidated_revenue" regardless of which tags were matched (the
    original callers, and the sox_financial_segments schema they persist
    to, are all revenue-keyed) — a caller walking this for a different
    account (material_accounts_tool.py) reads those same fields knowing
    they hold that account's value, not literal revenue.
    """
    tags = tags or _REVENUE_TAGS
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
        if tag not in tags or not el.text:
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
# Financial enrichment — gross profit / operating income / net income /
# assets / margins per segment member, filed when the filer reports it
# dimensionally, else estimated as consolidated_value * revenue_pct. Closes
# the gap sox-scope.jsx's Segments tab used to leave entirely to manual
# entry: revenue/revenue_pct come from extract_segments_from_xml above, and
# only a filer's own consolidated Revenue could ever be looked up
# automatically — every other financial field was a blank text box.
# ─────────────────────────────────────────────────────────────────────────────

# Reuses edgar_tool.XBRL_METRICS's own tag choices for these concepts so a
# segment-level figure and its consolidated counterpart are always read off
# the same taxonomy tag.
_DERIVED_METRIC_TAGS = {
    "gross_profit":     ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income":       ["NetIncomeLoss"],
    "assets":           ["Assets"],
}
# fetch_xbrl_facts' own metric-name keys for the same four concepts, for
# reading the CONSOLIDATED (default-member, no dimension) figure to
# allocate from when a segment doesn't report a metric dimensionally.
_CONSOLIDATED_METRIC_NAMES = {
    "gross_profit": "GrossProfit", "operating_income": "OperatingIncome",
    "net_income": "NetIncome", "assets": "TotalAssets",
}


def _filed_member_values(xml_text: str, axis_type: str, period_start: str, period_end: str,
                          field: str) -> dict[str, float]:
    """{member: value} for one derived metric (gross_profit/operating_income/
    net_income/assets), restricted to the same (axis_type, period) as an
    already-extracted revenue breakdown — a filer that reports segment
    operating income the same way it reports segment revenue (common under
    ASC 280's "measure of segment profit or loss" requirement) shows up
    here instead of being estimated below. Re-walks the same xml_text
    extract_segments_from_xml already parsed for revenue — one extra pass
    per metric, not a second fetch."""
    extracted = extract_segments_from_xml(xml_text, tags=_DERIVED_METRIC_TAGS[field])
    for b in extracted["breakdowns"]:
        if b["segment_type"] == axis_type and b["period_start"] == period_start and b["period_end"] == period_end:
            return {m["segment_name"]: m["revenue"] for m in b["members"]}
    return {}


def _consolidated_value_for_period(xbrl_facts: dict, metric: str, period_end: str,
                                    period_start: str = "") -> Optional[float]:
    """The consolidated (default-member) data point matching a segment
    breakdown's own period — the fiscal window a % allocation needs to
    allocate FROM, not just "whichever value fetch_xbrl_facts happened to
    return most recently." Assets is an instant (balance-sheet) fact with
    no start date; the flow metrics (gross profit/operating income/net
    income) are matched on both start and end so a quarterly segment figure
    is never allocated against an annual or YTD consolidated total."""
    points = (xbrl_facts.get(metric) or {}).get("data_points") or []
    for p in points:
        if p.get("end") != period_end:
            continue
        if period_start and p.get("start") and p.get("start") != period_start:
            continue
        return p.get("val")
    return None


def _compute_margins(revenue: Optional[float], gross_profit: Optional[float],
                      operating_income: Optional[float], net_income: Optional[float]) -> dict[str, Optional[float]]:
    """Pure ratio math on whatever revenue/gross_profit/operating_income/
    net_income ended up populated, filed or estimated alike — margins are
    never independently sourced or estimated, only ever derived from those
    four."""
    def pct(numerator):
        return round(numerator / revenue * 100, 2) if revenue and numerator is not None else None
    return {
        "gross_margin_pct": pct(gross_profit),
        "op_margin_pct": pct(operating_income),
        "net_margin_pct": pct(net_income),
    }


def enrich_breakdown_financials(xml_text: str, breakdown: dict, xbrl_facts: dict) -> dict:
    """Fills each member of an already-extracted, reconciled revenue
    breakdown with gross_profit/operating_income/net_income/assets and the
    three margin ratios. Mutates and returns `breakdown`.

    Each of the four derived fields is filed (read dimensionally off the
    same filing) when the filer reports it that way, else estimated as
    consolidated_value * (that member's own revenue_pct) — the standard
    allocation convention an audit workpaper uses when a filer doesn't
    break a figure out by segment. Each member's `financials_source` is
    'filed' (every derived field that resolved was filed), 'estimated'
    (none were), 'mixed' (some of each), or 'unavailable' (neither this
    filing nor its consolidated facts had the underlying figure at all —
    left null, never a fabricated 0)."""
    axis_type, start, end = breakdown["segment_type"], breakdown["period_start"], breakdown["period_end"]
    filed_by_field = {
        field: _filed_member_values(xml_text, axis_type, start, end, field)
        for field in _DERIVED_METRIC_TAGS
    }
    for m in breakdown["members"]:
        name = m["segment_name"]
        sources = set()
        for field in _DERIVED_METRIC_TAGS:
            filed_val = filed_by_field[field].get(name)
            if filed_val is not None:
                m[field] = filed_val
                sources.add("filed")
                continue
            consolidated = _consolidated_value_for_period(
                xbrl_facts, _CONSOLIDATED_METRIC_NAMES[field], end,
                start if field != "assets" else "",
            )
            if consolidated is not None and m.get("revenue_pct") is not None:
                m[field] = round(consolidated * m["revenue_pct"] / 100, 2)
                sources.add("estimated")
            else:
                m[field] = None
        m["financials_source"] = (
            "filed" if sources == {"filed"} else
            "estimated" if sources == {"estimated"} else
            "mixed" if sources else "unavailable"
        )
        m.update(_compute_margins(m.get("revenue"), m.get("gross_profit"), m.get("operating_income"), m.get("net_income")))
    return breakdown


def estimate_segment_financials(ticker: str, revenue_pct: float) -> dict[str, Any]:
    """Percentage-of-consolidated estimate for a segment that ISN'T broken
    out anywhere in the filer's own XBRL — e.g. an internally-defined
    business unit an auditor is scoping by hand, with nothing for
    enrich_breakdown_financials above to key off. Allocates the company's
    most recently reported consolidated Revenue/GrossProfit/OperatingIncome/
    NetIncome/TotalAssets by `revenue_pct`, then derives the three margin
    ratios from the result — the manual-entry-form equivalent of what
    enrich_breakdown_financials does for a filed breakdown. Always
    source='estimated'; never a substitute for real filed dimensional data
    when persist_segments below finds it."""
    try:
        meta, _ = get_company_info(ticker)
    except ValueError as e:
        return {"estimated": False, "reason": str(e)}

    facts = fetch_xbrl_facts(meta["cik_plain"])
    if not facts or "Revenue" not in facts:
        return {"estimated": False, "reason": "No consolidated XBRL facts available for this ticker"}

    pct = revenue_pct / 100.0
    values: dict[str, Optional[float]] = {}
    basis: dict[str, dict] = {}
    for field, metric in {"revenue": "Revenue", **_CONSOLIDATED_METRIC_NAMES}.items():
        points = (facts.get(metric) or {}).get("data_points") or []
        point = points[0] if points else None  # fetch_xbrl_facts sorts newest-`end`-first
        if point is None or point.get("val") is None:
            values[field] = None
            continue
        values[field] = round(point["val"] * pct, 2)
        basis[field] = {"consolidated_value": point["val"], "period_end": point.get("end"), "form": point.get("form")}

    result = {
        "estimated": True, "ticker": ticker.upper(), "revenue_pct": revenue_pct,
        "source": "estimated", "basis": basis, **values,
    }
    result.update(_compute_margins(values.get("revenue"), values.get("gross_profit"),
                                    values.get("operating_income"), values.get("net_income")))
    return result


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
        # "FY{year}" — matches the convention sox_scoping_tool.py and every
        # other fiscal_year-keyed lookup in this codebase uses (e.g.
        # f"FY{datetime.utcnow().year}" in run_sox_scoping). A bare "2026"
        # here would silently never match those lookups.
        "fiscal_year": f"FY{filing['date'][:4]}",
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

    # gross_profit/operating_income/net_income/assets/margins per member —
    # filed dimensionally when reported, else allocated from the
    # consolidated figure by revenue_pct. Best-effort: a companyfacts fetch
    # failure here must not blank out the revenue breakdown this call
    # already successfully extracted.
    try:
        xbrl_facts = fetch_xbrl_facts(cik) or {}
    except Exception:
        xbrl_facts = {}
    for b in result["breakdowns"]:
        enrich_breakdown_financials(xml_text, b, xbrl_facts)
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
            # 'filed' only if every derived financial field that resolved
            # at all came straight off this filing's own dimensional facts;
            # a member with anything allocated by revenue_pct (or nothing
            # resolvable at all) is 'filed+estimated' — the revenue/
            # revenue_pct themselves are always filed at this point (only
            # reconciled breakdowns reach this loop), but a reviewer must
            # still be able to tell that gross_profit/operating_income/
            # net_income/assets weren't all independently verified figures.
            fin_source = m.get("financials_source")
            row_source = "filed" if fin_source == "filed" else "filed+estimated"
            db.upsert_sox_segment(company_id, None, {
                "fiscal_year": result["fiscal_year"],
                "segment_type": axis,
                "segment_name": m["segment_name"],
                "revenue": m["revenue"],
                "revenue_pct": m["revenue_pct"],
                "gross_profit": m.get("gross_profit"),
                "operating_income": m.get("operating_income"),
                "net_income": m.get("net_income"),
                "assets": m.get("assets"),
                "gross_margin_pct": m.get("gross_margin_pct"),
                "op_margin_pct": m.get("op_margin_pct"),
                "net_margin_pct": m.get("net_margin_pct"),
                "source": row_source,
            })
            result["persisted"].append({
                "segment_type": axis, "segment_name": m["segment_name"],
                "financials_source": fin_source,
            })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Multi-filing history + forecasting — a single filing's own comparatives
# (current Q, prior-year Q, YTD, prior YTD) top out around 4 points, nowhere
# near fit_arima's 8-observation minimum. Segment-level forecasting needs a
# real run built by walking several filings, the same way the consolidated
# forecast is built from a multi-year quarterly XBRL series.
# ─────────────────────────────────────────────────────────────────────────────

import datetime as _dt  # noqa: E402 (kept near its one use, matching this module's other lazy-ish imports)

_QUARTER_DAYS_RANGE = (75, 100)  # a fiscal quarter's period length; excludes YTD/annual breakdowns


def _is_quarterly_period(start: str, end: str) -> bool:
    """True for a ~90-day period (a single fiscal quarter) — excludes YTD
    (6/9-month) and annual (10-K) breakdowns, which report a different
    accumulation window and would corrupt a quarterly revenue series if
    mixed in with true quarterly points."""
    try:
        days = (_dt.date.fromisoformat(end) - _dt.date.fromisoformat(start)).days
    except ValueError:
        return False
    return _QUARTER_DAYS_RANGE[0] <= days <= _QUARTER_DAYS_RANGE[1]


def fetch_segment_history(ticker: str, max_filings: int = 10,
                           form_types: Optional[set[str]] = None) -> dict[str, Any]:
    """Walk the last `max_filings` 10-Q filings (10-Ks report only annual
    segment figures, a different accumulation window — excluded here, see
    _is_quarterly_period) and stitch each one's quarterly segment/geography
    breakdowns into one chronological revenue series per (segment_type,
    segment_name).

    Processes filings newest-first: each 10-Q reports both its own current
    quarter and the same quarter a year ago as a comparative, so a
    period_end already recorded is kept from whichever filing reported it
    as its OWN current period — not overwritten by an older filing's
    restated comparative for the same quarter.

    Returns {"extracted": bool, "series": {(segment_type, segment_name):
    [{"period_end", "revenue"}, ...]}, "filings_used": int} — series is
    empty (not fabricated) when fewer than 2 filings yield reconciled
    quarterly breakdowns.
    """
    form_types = form_types or {"10-Q"}
    ticker_u = ticker.upper()
    try:
        meta, sub = get_company_info(ticker)
    except ValueError as e:
        return {"extracted": False, "ticker": ticker_u, "reason": str(e), "series": {}, "filings_used": 0}

    filings = parse_filings(sub, form_types)
    candidates = sorted(
        (f for forms in filings.values() for f in forms),
        key=lambda f: f["date"], reverse=True,
    )[:max_filings]
    if not candidates:
        return {
            "extracted": False, "ticker": ticker_u, "series": {}, "filings_used": 0,
            "reason": f"No {'/'.join(sorted(form_types))} filings found for {ticker}",
        }

    cik = meta["cik_plain"]
    series: dict[tuple, dict[str, float]] = {}
    filings_used = 0

    for filing in candidates:
        doc_name = _find_instance_doc(cik, filing["accession_number"])
        if not doc_name:
            continue
        xml_text = _fetch_instance_xml(cik, filing["accession_number"], doc_name)
        if not xml_text:
            continue
        extracted = extract_segments_from_xml(xml_text)
        if not extracted["extracted"]:
            continue
        filings_used += 1
        for b in extracted["breakdowns"]:
            if not b["reconciled"] or not _is_quarterly_period(b["period_start"], b["period_end"]):
                continue
            for m in b["members"]:
                key = (b["segment_type"], m["segment_name"])
                bucket = series.setdefault(key, {})
                bucket.setdefault(b["period_end"], m["revenue"])

    if not series:
        return {
            "extracted": False, "ticker": ticker_u, "series": {}, "filings_used": filings_used,
            "reason": "No reconciled quarterly segment breakdowns found across recent 10-Q filings",
        }

    result_series = {
        key: [{"period_end": pe, "revenue": rev} for pe, rev in sorted(points.items())]
        for key, points in series.items()
    }
    return {"extracted": True, "ticker": ticker_u, "series": result_series, "filings_used": filings_used}


_MIN_FORECAST_POINTS = 8  # fit_arima's own floor (predictive_analytics_tool.py)


def forecast_segments(ticker: str, run_id: Optional[int] = None, horizon: int = 4,
                       max_filings: int = 10) -> dict[str, Any]:
    """fetch_segment_history() + an ensemble forecast (ARIMA / Prophet-like /
    Random Forest blend — the same model predictive_analytics_tool.py uses
    for consolidated revenue) per segment with enough history, persisted to
    segment_forecasts via db.save_segment_forecasts (source='filed').

    A segment with fewer than _MIN_FORECAST_POINTS quarters of reconciled
    history is reported as skipped with a reason, never forced through a
    model that isn't backed by enough real observations.
    """
    import predictive_analytics_tool as pat  # local import: keeps this
    # module's pure-parsing path (extract_segments_from_xml, tested with
    # zero DB/model dependencies) usable without numpy installed.

    history = fetch_segment_history(ticker, max_filings=max_filings)
    result: dict[str, Any] = {
        "ticker": ticker.upper(), "extracted": history["extracted"],
        "forecasts": [], "skipped": [], "filings_used": history.get("filings_used", 0),
    }
    if not history["extracted"]:
        result["reason"] = history.get("reason")
        return result

    for (segment_type, segment_name), points in history["series"].items():
        if len(points) < _MIN_FORECAST_POINTS:
            result["skipped"].append({
                "segment_type": segment_type, "segment_name": segment_name,
                "reason": f"Only {len(points)} reconciled quarters found — need at least {_MIN_FORECAST_POINTS}",
            })
            continue

        series = [p["revenue"] for p in points]
        ensemble = pat.compute_ensemble_forecast(series, horizon=horizon)
        latest = points[-1]
        # Same-quarter-last-year comparison (4 quarters back), not just the
        # prior point — matches how rev_growth_yoy is computed everywhere
        # else in this app.
        yoy_point = points[-5] if len(points) >= 5 else None
        rev_growth_yoy = (
            round((latest["revenue"] - yoy_point["revenue"]) / yoy_point["revenue"] * 100, 2)
            if yoy_point and yoy_point["revenue"] else None
        )

        forecast_entry = {
            "segment_type": segment_type, "segment_name": segment_name,
            "fiscal_year": f"FY{latest['period_end'][:4]}",
            "revenue_m": round(latest["revenue"] / 1e6, 2),
            "rev_growth_yoy": rev_growth_yoy,
            "quarters_used": len(points),
            "forecast": ensemble["forecasts"],
            "source": "filed",
        }
        result["forecasts"].append(forecast_entry)

        if run_id is not None:
            try:
                import db
                db.save_segment_forecasts(run_id, [{
                    "segment_type": segment_type, "segment_name": segment_name,
                    "fiscal_year": forecast_entry["fiscal_year"],
                    "revenue_m": forecast_entry["revenue_m"],
                    "rev_growth_yoy": rev_growth_yoy,
                    "source": "filed",
                }])
            except Exception:
                pass  # persistence is best-effort here; the forecast is still returned to the caller

    return result
