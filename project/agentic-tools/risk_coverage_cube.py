#!/usr/bin/env python3
"""
Risk Coverage Cube — a COSO-aligned illustration of how much of the risk
universe is actually watched, and by what.

    X — COSO ERM 2017 component (risks_as_code._COSO_PRINCIPLES), + Unmapped
    Y — COSO objective category  (risks_as_code._OBJECTIVE_CATEGORY), + Unmapped
    Z — operating unit (geography | business_segment). Real, not a display-
        only placeholder: segment_risk_tool.py (Risk Coverage Cube Phase 3)
        tags a risk's own segment_type/segment_name on risk_scores when it
        was derived from a specific segment's filed data (Concentration/
        Decline/Divergence). A risk with no such tag is "Consolidated" — the
        entity is real either way, never inferred here.

Each cell is one of three states — never collapsed to a binary green/red:
    empty              no risk in the current run falls in this cell
    mapped_unverified  a risk is here, but no linked control has real,
                        tested/observed assurance evidence
    verified           a risk is here AND at least one linked control has
                        last_test_passed=True or fired within the last
                        _STALE_DAYS days

This is deliberately conservative: a risk with no risk_control_mappings row
(review-session-scoped; often empty) renders mapped_unverified, not verified
and not empty — "a risk exists here" is a fact from risk_scores; "it's
actually covered" requires real evidence from controls_catalog, per the same
philosophy get_compliance_scorecard already applies ("that's the honest
state to surface, not a green checkmark a mapping alone hasn't earned").

Split into a pure aggregation function (build_cube, unit-testable with fake
rows) and a thin DB-fetching wrapper (get_coverage_cube), mirroring
edgar_segments.py / _aggregate_scorecard_rows's reasoning.

Router prefix: /coverage-cube
    GET /coverage-cube/{ticker}   the assembled cube for the ticker's latest run
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from risks_as_code import (
    _COSO_PRINCIPLES, _COSO_UNMAPPED,
    _OBJECTIVE_CATEGORY, _OBJECTIVE_UNMAPPED,
    _VELOCITY_LABEL,
)

router = APIRouter(prefix="/coverage-cube")

_STALE_DAYS = 30

COSO_COMPONENTS = [
    "Governance & Culture",
    "Strategy & Objective-Setting",
    "Performance",
    "Review & Revision",
    "Information, Communication & Reporting",
    "Unmapped",
]
OBJECTIVE_CATEGORIES = ["Strategic", "Operations", "Reporting", "Compliance", "Unmapped"]

_RAG_SEVERITY = {"R": 3, "A": 2, "G": 1}


def _control_verified(control: Optional[dict], cutoff: datetime) -> bool:
    if not control:
        return False
    if control.get("last_test_passed"):
        return True
    fired_at = control.get("last_fired_at")
    if not fired_at:
        return False
    try:
        fired_dt = datetime.fromisoformat(fired_at)
    except (TypeError, ValueError):
        return False
    if fired_dt.tzinfo is None:
        fired_dt = fired_dt.replace(tzinfo=timezone.utc)
    return fired_dt > cutoff


def build_cube(
    risks: List[dict],
    mappings: List[dict],
    library_by_ref: Dict[str, dict],
    catalog_by_id: Dict[str, dict],
    stale_days: int = _STALE_DAYS,
) -> dict:
    """Pure aggregation — no DB access.

    risks:          rows shaped like db.get_latest_risks_for_ticker()["risks"]
                     (needs: category, score, rag/rag_status, velocity, control_env, id/risk_ref)
    mappings:        rows shaped like db.get_risk_control_mappings_for_run()
                     ({risk_ref, control_ref, ...} — control_ref is a
                     controls_library ref, not a controls_catalog control_id)
    library_by_ref:  {control_ref: controls_library row} (needs pac_control_id)
    catalog_by_id:   {control_id: controls_catalog row} (needs last_test_passed,
                      last_fired_at, coso_component, source)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)

    mapped_refs_by_risk: Dict[str, List[str]] = {}
    for m in mappings:
        mapped_refs_by_risk.setdefault(m["risk_ref"], []).append(m["control_ref"])

    cells: Dict[tuple, dict] = {}
    entities_seen: set = set()

    def _cell(objective_category: str, coso_component: str, entity: str) -> dict:
        key = (objective_category, coso_component, entity)
        if key not in cells:
            cells[key] = {
                "objective_category": objective_category, "coso_component": coso_component,
                "entity": entity,
                "risk_count": 0, "worst_rag": None, "max_score": None,
                "velocity_label": None, "control_env_mix": {"WEAK": 0, "ADEQUATE": 0, "STRONG": 0},
                "mapped_control_count": 0, "verified_control_count": 0,
                "risk_refs": [],
            }
        return cells[key]

    for risk in risks:
        category = risk.get("category") or ""
        coso = _COSO_PRINCIPLES.get(category, _COSO_UNMAPPED)
        objective_category = _OBJECTIVE_CATEGORY.get(category, _OBJECTIVE_UNMAPPED)
        entity = risk.get("segment_name") or "Consolidated"
        entities_seen.add(entity)
        cell = _cell(objective_category, coso["component"], entity)

        risk_ref = risk.get("risk_ref") or risk.get("id") or ""
        rag = risk.get("rag") or risk.get("rag_status") or "G"
        score = risk.get("score")
        velocity = risk.get("velocity") or 0
        control_env = (risk.get("control_env") or "").upper()

        cell["risk_count"] += 1
        cell["risk_refs"].append(risk_ref)
        if cell["worst_rag"] is None or _RAG_SEVERITY.get(rag, 0) > _RAG_SEVERITY.get(cell["worst_rag"], 0):
            cell["worst_rag"] = rag
        if score is not None and (cell["max_score"] is None or score > cell["max_score"]):
            cell["max_score"] = score
            cell["velocity_label"] = _VELOCITY_LABEL.get(velocity, str(velocity))
        if control_env in cell["control_env_mix"]:
            cell["control_env_mix"][control_env] += 1

        for control_ref in mapped_refs_by_risk.get(risk_ref, []):
            library_row = library_by_ref.get(control_ref)
            pac_id = library_row.get("pac_control_id") if library_row else control_ref
            catalog_row = catalog_by_id.get(pac_id) or catalog_by_id.get(control_ref)
            cell["mapped_control_count"] += 1
            if _control_verified(catalog_row, cutoff):
                cell["verified_control_count"] += 1

    # "Consolidated" always exists as an entity, even with zero risks in it
    # (an all-segment risk set would be a very odd but not impossible state
    # to hide the axis for) — every OTHER entity is real risk-level data,
    # never a placeholder.
    entities = ["Consolidated"] + sorted(e for e in entities_seen if e != "Consolidated")

    grid = []
    for entity in entities:
        for objective_category in OBJECTIVE_CATEGORIES:
            for coso_component in COSO_COMPONENTS:
                cell = cells.get((objective_category, coso_component, entity))
                if cell is None:
                    grid.append({
                        "objective_category": objective_category, "coso_component": coso_component,
                        "entity": entity,
                        "state": "empty", "risk_count": 0, "worst_rag": None, "max_score": None,
                        "velocity_label": None, "control_env_mix": {"WEAK": 0, "ADEQUATE": 0, "STRONG": 0},
                        "mapped_control_count": 0, "verified_control_count": 0, "risk_refs": [],
                    })
                    continue
                state = "verified" if cell["verified_control_count"] > 0 else "mapped_unverified"
                grid.append({**cell, "state": state})

    return {
        "objective_categories": OBJECTIVE_CATEGORIES,
        "coso_components": COSO_COMPONENTS,
        "entities": entities,
        "cells": grid,
        "total_risks": len(risks),
        "unmapped_risk_count": sum(
            1 for r in risks
            if _COSO_PRINCIPLES.get(r.get("category") or "", _COSO_UNMAPPED) is _COSO_UNMAPPED
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DB-fetching wrapper
# ─────────────────────────────────────────────────────────────────────────────

def get_coverage_cube(ticker: str) -> dict:
    import db

    latest = db.get_latest_risks_for_ticker(ticker)
    run_id, risks = latest["run_id"], latest["risks"]

    if run_id is None:
        cube = build_cube([], [], {}, {})
        cube.update({"ticker": ticker.upper(), "run_id": None, "segments": []})
        return cube

    mappings = db.get_risk_control_mappings_for_run(run_id)

    library_by_ref: Dict[str, dict] = {}
    for m in mappings:
        ref = m["control_ref"]
        if ref not in library_by_ref:
            row = db.get_control_by_ref(ref)
            if row:
                library_by_ref[ref] = row

    catalog_by_id: Dict[str, dict] = {c["control_id"]: c for c in db.list_controls()}

    cube = build_cube(risks, mappings, library_by_ref, catalog_by_id)

    segments: List[dict] = []
    company_id = db.get_company_id(ticker)
    if company_id:
        segments = db.get_latest_sox_segments(company_id)

    cube.update({"ticker": ticker.upper(), "run_id": run_id, "segments": segments})
    return cube


@router.get("/{ticker}")
def coverage_cube_endpoint(ticker: str):
    """Assembled Risk Coverage Cube for a ticker's latest risk-loop run."""
    return get_coverage_cube(ticker)
