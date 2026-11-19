#!/usr/bin/env python3
"""
Segment/geography-specific risk assessment — Risk Coverage Cube Phase 3.

Phases 1-2 (edgar_segments.py) gave every entity a real operating-unit
axis: filed segment revenue actuals (persist_segments) and, where enough
quarterly history exists, a per-segment forecast (forecast_segments). This
module is the actual risk ASSESSMENT step Phase 3 promised — scoring real
segment-level exposure, not just displaying revenue context.

Deliberately NOT a per-segment clone of risk-engine.js's buildRisks(): that
model's templates lean on ratios no filer discloses per segment (DSRI,
Beneish M-score, R&D/SG&A intensity, cash ratio — all consolidated-only
under GAAP). Reusing it per segment would mean silently defaulting those
inputs to the CONSOLIDATED figures for every segment, manufacturing risk
scores that look segment-specific but are actually identical copies of the
consolidated score with a different label — worse than not having them.

Instead, three risk types built ONLY from what's genuinely disclosed per
segment (revenue level, revenue share, YoY growth):

  Segment Concentration  — revenue_pct at or above threshold: overexposure
                            to a single geography/business line.
  Segment Decline        — this segment's own YoY revenue growth at or
                            below threshold: real, disclosed contraction.
  Segment Divergence      — this segment underperforming the CONSOLIDATED
                            growth rate by a wide margin: the "hidden in
                            the average" pattern where a strong headline
                            number masks a weakening segment.

A segment with no forecast (Phase 2 skipped it — fewer than 8 reconciled
quarters) still gets assessed for Concentration (needs only the current
period's revenue_pct); Decline/Divergence require rev_growth_yoy and are
skipped for that segment rather than guessed.
"""

from __future__ import annotations

from typing import Optional

_CONCENTRATION_THRESHOLD_PCT = 25.0
_DECLINE_THRESHOLD_PCT = -10.0
_DIVERGENCE_THRESHOLD_PCT = 15.0  # segment growth this many points below consolidated

_SEGMENT_TYPE_ABBREV = {"geography": "G", "business_segment": "B"}


def _slug_ref(segment_type: str, index: int, kind: str) -> str:
    """Short, deterministic risk_ref — risk_scores.risk_ref is VARCHAR(16),
    far too narrow for a human-readable segment-name-based key. Only needs
    to be stable within one run (save_risk_scores upserts on
    (run_id, risk_ref)); a segment's position in this run's own segment
    list is a stable-enough key for that."""
    return f"SG{_SEGMENT_TYPE_ABBREV.get(segment_type, 'X')}{index:02d}{kind}"


def _rag(score: float) -> str:
    if score >= 15:
        return "R"
    if score >= 9:
        return "A"
    return "G"


def _latest_reconciled_breakdowns(breakdowns: list[dict]) -> dict[str, dict]:
    """Latest reconciled breakdown per axis — mirrors
    edgar_segments.persist_segments()'s own selection logic exactly, so the
    risks assessed here are always about the SAME revenue figures that
    were (or would be) persisted, never a different period."""
    latest: dict[str, dict] = {}
    for b in breakdowns:
        if not b.get("reconciled"):
            continue
        axis = b["segment_type"]
        if axis not in latest or b["period_end"] > latest[axis]["period_end"]:
            latest[axis] = b
    return latest


def assess_segment_risks(
    persist_result: dict,
    forecast_result: Optional[dict] = None,
    consolidated_revenue_growth_pct: Optional[float] = None,
    concentration_threshold_pct: float = _CONCENTRATION_THRESHOLD_PCT,
    decline_threshold_pct: float = _DECLINE_THRESHOLD_PCT,
    divergence_threshold_pct: float = _DIVERGENCE_THRESHOLD_PCT,
) -> list[dict]:
    """Pure function over edgar_segments.persist_segments()'s and
    forecast_segments()'s own return dicts — no network, no DB. Returns a
    list of risk_scores-shaped dicts (risk_ref, name, category, score, rag,
    segment_type, segment_name, source_framework='segment_risk', ...),
    ready for db.save_risk_scores(). Never fabricates a risk: an entity
    with no reconciled segment breakdown, or a segment with no growth
    figure, simply contributes fewer (never guessed) risks.
    """
    if not persist_result or not persist_result.get("extracted"):
        return []

    growth_by_segment: dict[tuple, dict] = {}
    for f in (forecast_result or {}).get("forecasts", []):
        growth_by_segment[(f["segment_type"], f["segment_name"])] = f

    risks: list[dict] = []
    for axis, breakdown in _latest_reconciled_breakdowns(persist_result["breakdowns"]).items():
        for i, member in enumerate(breakdown["members"], start=1):
            seg_name = member["segment_name"]
            revenue_pct = member.get("revenue_pct")
            fc = growth_by_segment.get((axis, seg_name))
            rev_growth_yoy = fc.get("rev_growth_yoy") if fc else None

            if revenue_pct is not None and revenue_pct >= concentration_threshold_pct:
                score = round(min(25.0, 10.0 + (revenue_pct - concentration_threshold_pct) * 0.5), 1)
                risks.append({
                    "risk_ref": _slug_ref(axis, i, "C"),
                    "name": f"{seg_name} revenue concentration",
                    "category": "Segment Concentration",
                    "score": score, "base_score": score, "delta": 0.0,
                    "rag_status": _rag(score), "velocity": 0,
                    "segment_type": axis, "segment_name": seg_name,
                    "source_framework": "segment_risk",
                    "narrative": (
                        f"{seg_name} represents {revenue_pct:.1f}% of consolidated revenue "
                        f"(threshold {concentration_threshold_pct:.0f}%) — a disruption specific to this "
                        f"{('geography' if axis == 'geography' else 'business segment')} would have an "
                        f"outsized effect on the consolidated result."
                    ),
                })

            if rev_growth_yoy is not None and rev_growth_yoy <= decline_threshold_pct:
                score = round(min(25.0, 8.0 + abs(rev_growth_yoy) * 0.4), 1)
                risks.append({
                    "risk_ref": _slug_ref(axis, i, "D"),
                    "name": f"{seg_name} revenue decline",
                    "category": "Segment Decline",
                    "score": score, "base_score": score, "delta": 0.0,
                    "rag_status": _rag(score), "velocity": 1 if rev_growth_yoy < 0 else 0,
                    "segment_type": axis, "segment_name": seg_name,
                    "source_framework": "segment_risk",
                    "narrative": (
                        f"{seg_name} revenue is down {abs(rev_growth_yoy):.1f}% YoY "
                        f"(filed quarterly segment history, {fc.get('quarters_used')} quarters) — "
                        f"real, disclosed contraction in this segment, not a consolidated-average estimate."
                    ),
                })

            if (rev_growth_yoy is not None and consolidated_revenue_growth_pct is not None):
                gap = consolidated_revenue_growth_pct - rev_growth_yoy
                if gap >= divergence_threshold_pct:
                    score = round(min(25.0, 8.0 + gap * 0.3), 1)
                    risks.append({
                        "risk_ref": _slug_ref(axis, i, "V"),
                        "name": f"{seg_name} underperformance masked by consolidated results",
                        "category": "Segment Divergence",
                        "score": score, "base_score": score, "delta": 0.0,
                        "rag_status": _rag(score), "velocity": 1,
                        "segment_type": axis, "segment_name": seg_name,
                        "source_framework": "segment_risk",
                        "narrative": (
                            f"{seg_name} grew {rev_growth_yoy:+.1f}% YoY vs. {consolidated_revenue_growth_pct:+.1f}% "
                            f"consolidated — a {gap:.1f}-point gap. Consolidated headline growth is masking "
                            f"real weakness specific to this segment."
                        ),
                    })

    return risks
