#!/usr/bin/env python3
"""
Universal, evidence-scored risk taxonomy — replaces the per-industry templates.

Every entity is assessed against the SAME fixed set of risk categories
(TAXONOMY). A category's score is derived only from evidence about THAT
entity — XBRL ratios, Beneish / Altman results, SEC 8-K events, DEF 14A
governance KRIs, SEC staff comment letters, RSS / FRED signals, segment
data. There is no industry baseline and no hand-set "base score".

    likelihood = clamp(BASE_LIKELIHOOD + sum(evidence points), 0.5, 5.0)
    score      = impact(category) x likelihood            (0-25, R>=15, A>=9)

A category with NO evidence input at all is reported as NOT ASSESSED
(assessed=False, score=None). It is excluded from RAG counts, appetite
breach and audit scope and surfaced to the HITL reviewer as a coverage gap.
An input that is present but benign (e.g. accruals within normal range) DOES
count as assessed — a low score backed by evidence is different from no view.

Each evidence item is `{source, label, points, trend, observed}`:
    points    likelihood points it adds (negative = mitigating)
    trend     True when the item reflects a RECENT CHANGE (YoY delta, a
              recent filing). `base` / velocity are derived from the score
              with trend items removed, so velocity means "how much worse
              than the structural picture", not an invented history.

risk-engine.js mirrors the rule tables below for Live-JS / Mock mode; the
Python side is authoritative when the MCP server is reachable.
"""
from __future__ import annotations

from typing import Callable, Optional

import risk_rating_engine as rre

BASE_LIKELIHOOD = 1.0
RAG_SCALE_NOTE = "score = impact x likelihood on the canonical 0-25 scale"

# id, name, category (must resolve in risk_rating_engine.CATEGORY_IMPACT)
TAXONOMY: list[dict] = [
    {"id": "TX-FRQ", "name": "Financial Reporting Quality",             "category": "Financial Reporting"},
    {"id": "TX-LIQ", "name": "Liquidity & Going Concern",               "category": "Liquidity"},
    {"id": "TX-PRF", "name": "Profitability & Margin Pressure",         "category": "Financial Reporting"},
    {"id": "TX-REV", "name": "Revenue Growth & Demand",                 "category": "Revenue"},
    {"id": "TX-CAP", "name": "Capital Structure & Financial Obligations", "category": "Financial Reporting"},
    {"id": "TX-CYB", "name": "Cybersecurity & Data Protection",         "category": "Cybersecurity"},
    {"id": "TX-GOV", "name": "Board & Executive Governance",            "category": "Governance"},
    {"id": "TX-AUD", "name": "Audit Oversight & Internal Control",      "category": "Audit Oversight"},
    {"id": "TX-REG", "name": "Regulatory, Legal & Enforcement",         "category": "Compliance"},
    {"id": "TX-OPS", "name": "Operations & Supply Chain",               "category": "Operational"},
    {"id": "TX-STR", "name": "Strategic Transactions & Concentration",  "category": "Strategic"},
    {"id": "TX-MAC", "name": "Macro & Market Conditions",               "category": "Strategic"},
    {"id": "TX-ESG", "name": "ESG & Climate Disclosure",                "category": "ESG"},
    {"id": "TX-TRD", "name": "Trade & Sanctions Compliance",            "category": "Trade Compliance"},
]
TAXONOMY_IDS = [t["id"] for t in TAXONOMY]


def ev(source: str, label: str, points: float, *, trend: bool = False,
       observed=None) -> dict:
    return {"source": source, "label": label, "points": round(float(points), 2),
            "trend": bool(trend), "observed": observed}


def _num(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _gt_tiers(value, tiers, label, source="XBRL", trend=False, fmt="{:.3f}"):
    """tiers: [(threshold, points)] checked highest-threshold first; value is
    adverse when GREATER than the threshold. Returns None when value missing,
    else one evidence item (points 0 when benign)."""
    v = _num(value)
    if v is None:
        return None
    for thr, pts in sorted(tiers, key=lambda t: -t[0]):
        if v > thr:
            return ev(source, f"{label} {fmt.format(v)} > {thr}", pts, trend=trend, observed=v)
    return ev(source, f"{label} {fmt.format(v)} within normal range", 0.0, trend=trend, observed=v)


def _lt_tiers(value, tiers, label, source="XBRL", trend=False, fmt="{:.3f}"):
    """Adverse when LESS than threshold; lowest threshold checked first."""
    v = _num(value)
    if v is None:
        return None
    for thr, pts in sorted(tiers, key=lambda t: t[0]):
        if v < thr:
            return ev(source, f"{label} {fmt.format(v)} < {thr}", pts, trend=trend, observed=v)
    return ev(source, f"{label} {fmt.format(v)} within normal range", 0.0, trend=trend, observed=v)


def _compact(items):
    return [i for i in items if i]


# ── Ratio rules per category ─────────────────────────────────────────────
# Each takes (ratios, m, z) -> list[evidence]. m = Beneish result dict,
# z = Altman result dict (either may be None).

def _frq(r, m, z):
    out = [
        _gt_tiers(r.get("tata"), [(0.10, 2.0), (0.05, 1.2), (0.02, 0.5)], "Total accruals / assets"),
        _gt_tiers(r.get("dsri"), [(1.20, 1.0), (1.05, 0.4)], "Receivables-to-sales index (DSRI)", trend=True, fmt="{:.2f}"),
    ]
    if m and _num(m.get("m_score")) is not None:
        out.append(_gt_tiers(m["m_score"], [(-1.78, 1.5), (-2.22, 0.7)], "Beneish M-Score", source="Beneish", fmt="{:.2f}"))
    return _compact(out)


def _liq(r, m, z):
    out = [
        _lt_tiers(r.get("cash_ratio"), [(0.05, 1.5), (0.10, 0.8)], "Cash + securities / assets"),
        _lt_tiers(r.get("fcf_margin"), [(-0.10, 1.5), (0.0, 0.7)], "FCF margin"),
    ]
    if z and _num(z.get("z_score")) is not None:
        out.append(_lt_tiers(z["z_score"], [(1.1, 2.0), (2.6, 0.8)], "Altman Z''-Score", source="Altman", fmt="{:.2f}"))
    out.append(_lt_tiers(r.get("zscore_x1"), [(0.0, 0.5)], "Working capital / assets"))
    return _compact(out)


def _prf(r, m, z):
    out = []
    gm, gmp = _num(r.get("gross_margin")), _num(r.get("gross_margin_prev"))
    if gm is not None and gmp is not None:
        out.append(_lt_tiers(gm - gmp, [(-0.03, 1.2), (-0.01, 0.5)], "Gross-margin change YoY",
                             trend=True, fmt="{:+.3f}"))
    out.append(_lt_tiers(r.get("net_margin"), [(0.0, 1.2), (0.03, 0.4)], "Net margin"))
    return _compact(out)


def _rev(r, m, z):
    return _compact([
        _lt_tiers(r.get("revenue_growth"), [(-0.10, 2.0), (-0.05, 1.2), (0.0, 0.6)],
                  "Revenue growth YoY", trend=True),
        _gt_tiers(r.get("sgi"), [(1.5, 0.6)], "Sales growth index (SGI)", trend=True, fmt="{:.2f}"),
    ])


def _cap(r, m, z):
    return _compact([
        _lt_tiers(r.get("zscore_x4"), [(0.1, 1.5), (0.3, 0.8)], "Equity / liabilities"),
        _gt_tiers(r.get("lvgi"), [(1.2, 0.8)], "Leverage index (LVGI)", trend=True, fmt="{:.2f}"),
    ])


_RATIO_RULES: dict[str, Callable] = {
    "TX-FRQ": _frq, "TX-LIQ": _liq, "TX-PRF": _prf, "TX-REV": _rev, "TX-CAP": _cap,
}


def _score(impact: float, items: list[dict]) -> dict:
    pts = sum(i["points"] for i in items)
    structural = sum(i["points"] for i in items if not i["trend"])
    full = rre.score_from(impact, BASE_LIKELIHOOD + pts)
    base = rre.score_from(impact, BASE_LIKELIHOOD + structural)
    # An evidence item may carry floor="R" (e.g. 8-K Item 1.03 bankruptcy):
    # the category can never read below Red while that evidence stands,
    # however the point arithmetic lands.
    if any(i.get("floor") == "R" for i in items) and full["score"] < rre.RAG_RED_THRESHOLD:
        full = {**full, "score": rre.RAG_RED_THRESHOLD, "rag_status": "R"}
    return {"full": full, "base": base}


def score_taxonomy(ratios: Optional[dict], *, mscore: Optional[dict] = None,
                   zscore: Optional[dict] = None,
                   extra_evidence: Optional[dict] = None) -> dict:
    """Score every taxonomy category from evidence.

    extra_evidence: {taxonomy_id: [evidence items]} — 8-K events, proxy KRIs,
    comment letters etc. gathered by the caller. Unknown ids are ignored.
    """
    ratios = ratios or {}
    extra_evidence = extra_evidence or {}
    risks: list[dict] = []
    for i, t in enumerate(TAXONOMY):
        rule = _RATIO_RULES.get(t["id"])
        items = rule(ratios, mscore, zscore) if rule else []
        items = items + list(extra_evidence.get(t["id"], []))
        impact = rre.impact_for(t["category"])
        row = {
            "id": t["id"], "risk_ref": t["id"], "taxonomy_id": t["id"],
            "name": t["name"], "category": t["category"],
            "impact": impact, "evidence": items, "assessed": bool(items),
            "peer_benchmark": "in-line", "basis": "evidence",
        }
        if not items:
            row.update({"score": None, "base_score": None, "rag_status": None,
                        "likelihood": None, "velocity": 0, "control_env": None,
                        "delta": 0.0})
        else:
            s = _score(impact, items)
            vel = rre.velocity_of(score=s["full"]["score"], base=s["base"]["score"])
            row.update({
                "score": s["full"]["score"], "rag_status": s["full"]["rag_status"],
                "likelihood": s["full"]["likelihood"],
                "base_score": s["base"]["score"],
                "delta": round(s["full"]["score"] - s["base"]["score"], 2),
                "velocity": vel,
                # Control environment derived from how far recent evidence has
                # moved the score off the structural picture — same bands the
                # JS ceOf() uses, not a hard-coded "ADEQUATE".
                "control_env": ("WEAK" if s["full"]["score"] - s["base"]["score"] > 5.0
                                else "ADEQUATE" if s["full"]["score"] - s["base"]["score"] > 1.25
                                else "STRONG"),
            })
        risks.append(row)

    assessed = [r for r in risks if r["assessed"]]
    rag_counts = {"R": 0, "A": 0, "G": 0}
    for r in assessed:
        rag_counts[r["rag_status"]] += 1
    return {
        "industry": None,
        "model": "universal-taxonomy/evidence-scored",
        "rag_summary": rag_counts,
        "assessed_count": len(assessed),
        "not_assessed": [r["id"] for r in risks if not r["assessed"]],
        "risks": risks,
    }
