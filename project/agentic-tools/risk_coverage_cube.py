#!/usr/bin/env python3
"""
Risk Coverage Cube — two COSO-aligned views of how much of the risk universe
is actually watched, and by what. Corrected 2026-08-26 after an audit found
the original single "cube" mixed three things that don't belong together:
COSO ERM 2017 components on one axis, COSO ERM 2004's objective categories
(Strategic/Operations/Reporting/Compliance) on another, and a cube shape that
COSO itself retired when it published ERM 2017 (replaced by a helix). See the
project plan / commit history around 2026-08-26 for the full rationale.

Two views now, selected by ?framework=:

    icif_2013 (default) — the real COSO Cube, from the framework that still
        has one (Internal Control — Integrated Framework, 2013, still
        current):
            X — IC-IF component, driven by the CONTROL, not the risk
                (risks_as_code.ICIF_COMPONENTS + Unmapped). This is the key
                structural fix: the original cube keyed its component axis off
                the risk's category, which made two of five columns
                structurally unreachable (see risks_as_code.py's comment on
                why there is deliberately no risk-category -> IC-IF-component
                dict). Keying off the control's icif_component
                (controls_catalog, via framework_mappings.py) means every
                column is reachable — a risk with no IC-IF-tagged control
                lands in "Unmapped", a real finding, not a dead zone.
            Y — IC-IF objective category (risks_as_code._OBJECTIVE_CATEGORY_ICIF
                + Unmapped). Only 3 real categories — "Strategic" has no home
                in IC-IF and is counted separately (out_of_icif_scope_risk_count),
                never folded into a 4th row.
            Z — operating unit, real not display-only: "Consolidated" plus
                every segment_risk_tool.py-tagged segment. Division/Function
                are omitted entirely (no data source), not rendered empty.

    erm_2017 — NOT a cube (COSO ERM 2017 doesn't have one): a 5-component x
        20-principle conformance view answering "is this ERM activity
        evidenced?", sourced from risks_as_code.ERM_PRINCIPLES + real
        persisted artifacts (db.get_erm_evidence_counts). Never uses
        risks_as_code._COSO_PRINCIPLES (that maps risk categories to
        principles for the RaC artifact — a different question).

Each IC-IF cell is one of three states — never collapsed to a binary
green/red:
    empty              no risk in the current run falls in this cell
    mapped_unverified  a risk is here, but no linked control here has real,
                        tested/observed assurance evidence
    verified           a risk is here AND at least one linked control here
                        has last_test_passed=True or fired within the last
                        _STALE_DAYS days

This is deliberately conservative: a risk with no risk_control_mappings row
(review-session-scoped; often empty) renders mapped_unverified, not verified
and not empty — "a risk exists here" is a fact from risk_scores; "it's
actually covered" requires real evidence from controls_catalog, per the same
philosophy get_compliance_scorecard already applies.

Because the IC-IF component column now comes from the control rather than
the risk, a single risk mapped to controls in more than one IC-IF component
legitimately appears in more than one cell — risk_count summed across all
cells can exceed total_risks, the same non-deduplication caveat the frontend
already carries for mapped/verified control counts.

Split into pure aggregation functions (build_icif_cube / build_erm_evidence,
unit-testable with fake rows) and thin DB-fetching wrappers, mirroring
edgar_segments.py / _aggregate_scorecard_rows's reasoning.

Router prefix: /coverage-cube
    GET /coverage-cube/{ticker}?framework=icif_2013|erm_2017
        the assembled cube/evidence view for the ticker's latest run
        (framework defaults to icif_2013)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from risks_as_code import (
    ICIF_COMPONENTS,
    ICIF_OBJECTIVES,
    _OBJECTIVE_CATEGORY_ICIF,
    ERM_PRINCIPLES,
    _VELOCITY_LABEL,
)

router = APIRouter(prefix="/coverage-cube")

_STALE_DAYS = 30
_ICIF_OBJECTIVE_UNMAPPED = "Unmapped"
_ICIF_COMPONENT_UNMAPPED = "Unmapped"

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


# ─────────────────────────────────────────────────────────────────────────────
# IC-IF 2013 view — the real COSO Cube
# ─────────────────────────────────────────────────────────────────────────────

def build_icif_cube(
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
                      last_fired_at, icif_component, source)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)

    mapped_refs_by_risk: Dict[str, List[str]] = {}
    for m in mappings:
        mapped_refs_by_risk.setdefault(m["risk_ref"], []).append(m["control_ref"])

    cells: Dict[tuple, dict] = {}
    entities_seen: set = set()
    out_of_icif_scope_risk_count = 0
    unmapped_risk_count = 0

    def _cell(objective_category: str, icif_component: str, entity: str) -> dict:
        key = (objective_category, icif_component, entity)
        if key not in cells:
            cells[key] = {
                "objective_category": objective_category, "coso_component": icif_component,
                "entity": entity,
                "risk_count": 0, "worst_rag": None, "max_score": None,
                "velocity_label": None, "control_env_mix": {"WEAK": 0, "ADEQUATE": 0, "STRONG": 0},
                "mapped_control_count": 0, "verified_control_count": 0,
                "risk_refs": [],
            }
        return cells[key]

    for risk in risks:
        category = risk.get("category") or ""

        if category not in _OBJECTIVE_CATEGORY_ICIF:
            objective_category = _ICIF_OBJECTIVE_UNMAPPED
        else:
            objective_category = _OBJECTIVE_CATEGORY_ICIF[category]
            if objective_category is None:
                # IC-IF has no "Strategic" objective — never folded into a
                # 4th row (that would re-commit the ERM-2004-as-IC-IF error
                # this module was corrected for). Counted, not cell'd.
                out_of_icif_scope_risk_count += 1
                continue

        entity = risk.get("segment_name") or "Consolidated"
        entities_seen.add(entity)

        risk_ref = risk.get("risk_ref") or risk.get("id") or ""
        rag = risk.get("rag") or risk.get("rag_status") or "G"
        score = risk.get("score")
        velocity = risk.get("velocity") or 0
        control_env = (risk.get("control_env") or "").upper()

        # Component axis is driven by the CONTROL, not the risk — the fix for
        # the original "2 of 5 columns unreachable" bug. Group this risk's
        # mapped controls by icif_component; a risk touching more than one
        # component legitimately lands in more than one cell.
        catalog_rows_by_component: Dict[str, List[dict]] = {}
        for control_ref in mapped_refs_by_risk.get(risk_ref, []):
            library_row = library_by_ref.get(control_ref)
            pac_id = library_row.get("pac_control_id") if library_row else control_ref
            catalog_row = catalog_by_id.get(pac_id) or catalog_by_id.get(control_ref)
            component = (catalog_row or {}).get("icif_component") or _ICIF_COMPONENT_UNMAPPED
            catalog_rows_by_component.setdefault(component, []).append(catalog_row)

        if not catalog_rows_by_component:
            catalog_rows_by_component = {_ICIF_COMPONENT_UNMAPPED: []}
        if list(catalog_rows_by_component.keys()) == [_ICIF_COMPONENT_UNMAPPED]:
            unmapped_risk_count += 1

        for component, catalog_rows in catalog_rows_by_component.items():
            cell = _cell(objective_category, component, entity)
            cell["risk_count"] += 1
            cell["risk_refs"].append(risk_ref)
            if cell["worst_rag"] is None or _RAG_SEVERITY.get(rag, 0) > _RAG_SEVERITY.get(cell["worst_rag"], 0):
                cell["worst_rag"] = rag
            if score is not None and (cell["max_score"] is None or score > cell["max_score"]):
                cell["max_score"] = score
                cell["velocity_label"] = _VELOCITY_LABEL.get(velocity, str(velocity))
            if control_env in cell["control_env_mix"]:
                cell["control_env_mix"][control_env] += 1

            for catalog_row in catalog_rows:
                cell["mapped_control_count"] += 1
                if _control_verified(catalog_row, cutoff):
                    cell["verified_control_count"] += 1

    # "Consolidated" always exists as an entity, even with zero risks in it —
    # every OTHER entity is real risk-level data, never a placeholder.
    entities = ["Consolidated"] + sorted(e for e in entities_seen if e != "Consolidated")

    grid = []
    for entity in entities:
        for objective_category in ICIF_OBJECTIVES:
            for icif_component in ICIF_COMPONENTS:
                cell = cells.get((objective_category, icif_component, entity))
                if cell is None:
                    grid.append({
                        "objective_category": objective_category, "coso_component": icif_component,
                        "entity": entity,
                        "state": "empty", "risk_count": 0, "worst_rag": None, "max_score": None,
                        "velocity_label": None, "control_env_mix": {"WEAK": 0, "ADEQUATE": 0, "STRONG": 0},
                        "mapped_control_count": 0, "verified_control_count": 0, "risk_refs": [],
                    })
                    continue
                state = "verified" if cell["verified_control_count"] > 0 else "mapped_unverified"
                grid.append({**cell, "state": state})

    return {
        "framework": "icif_2013",
        "framework_label": "COSO Internal Control — Integrated Framework (2013)",
        "x_axis": "IC-IF component (driven by mapped control)",
        "y_axis": "Objective category",
        "z_axis": "Operating unit",
        "objective_categories": ICIF_OBJECTIVES,
        "coso_components": ICIF_COMPONENTS,
        "entities": entities,
        # Division and Function (IC-IF's other two org-structure levels) are
        # omitted from the Z axis entirely, not rendered as permanently-empty
        # levels — no organisational-hierarchy table and no functional
        # dimension exist anywhere in the schema. Rendering them empty would
        # be exactly the axis-error bug this module was corrected for.
        "omitted_z_levels": [
            {"level": "Division", "reason": "no organisational-hierarchy source in schema"},
            {"level": "Function", "reason": "no functional dimension on risks or controls in schema"},
        ],
        "cells": grid,
        "total_risks": len(risks),
        "unmapped_risk_count": unmapped_risk_count,
        "out_of_icif_scope_risk_count": out_of_icif_scope_risk_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ERM 2017 view — NOT a cube; component x principle conformance
# ─────────────────────────────────────────────────────────────────────────────

def build_erm_evidence(evidence_counts: Dict[str, int]) -> dict:
    """Pure aggregation — no DB access.

    evidence_counts: {evidence_key: count}, from db.get_erm_evidence_counts().
    Each of the 20 principles in risks_as_code.ERM_PRINCIPLES resolves to one
    of three states:
        no_source    principle's `evidence` is None — no artifact exists
                     anywhere in the schema for it. Never inferred.
        no_evidence  a real evidence key exists, but its count is 0 this run.
        evidenced    count > 0.
    """
    components = []
    total = evidenced = no_source = no_evidence = 0
    for comp in ERM_PRINCIPLES:
        principles = []
        for p in comp["principles"]:
            key = p["evidence"]
            total += 1
            if key is None:
                state, count = "no_source", None
                no_source += 1
            else:
                count = evidence_counts.get(key, 0)
                if count > 0:
                    state = "evidenced"
                    evidenced += 1
                else:
                    state = "no_evidence"
                    no_evidence += 1
            principles.append({
                "number": p["number"], "label": p["label"],
                "evidence_source": key, "state": state, "count": count,
            })
        components.append({"component": comp["component"], "principles": principles})

    return {
        "framework": "erm_2017",
        "framework_label": "COSO ERM 2017 (framework uses a helix, not a cube)",
        "x_axis": "ERM component",
        "y_axis": "Principle (nested under its component, not a cross-product)",
        "components": components,
        "total_principles": total,
        "evidenced_count": evidenced,
        "no_evidence_count": no_evidence,
        "no_source_count": no_source,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DB-fetching wrappers
# ─────────────────────────────────────────────────────────────────────────────

def get_icif_cube(ticker: str) -> dict:
    import db

    latest = db.get_latest_risks_for_ticker(ticker)
    run_id, risks = latest["run_id"], latest["risks"]

    if run_id is None:
        cube = build_icif_cube([], [], {}, {})
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

    cube = build_icif_cube(risks, mappings, library_by_ref, catalog_by_id)

    segments: List[dict] = []
    company_id = db.get_company_id(ticker)
    if company_id:
        segments = db.get_latest_sox_segments(company_id)

    cube.update({"ticker": ticker.upper(), "run_id": run_id, "segments": segments})
    return cube


def get_erm_evidence(ticker: str) -> dict:
    import db

    latest = db.get_latest_risks_for_ticker(ticker)
    run_id = latest["run_id"]

    if run_id is None:
        result = build_erm_evidence({})
        result.update({"ticker": ticker.upper(), "run_id": None, "segments": []})
        return result

    company_id = db.get_company_id(ticker)
    evidence_counts = db.get_erm_evidence_counts(run_id, company_id)
    result = build_erm_evidence(evidence_counts)

    segments: List[dict] = []
    if company_id:
        segments = db.get_latest_sox_segments(company_id)

    result.update({"ticker": ticker.upper(), "run_id": run_id, "segments": segments})
    return result


@router.get("/{ticker}")
def coverage_cube_endpoint(
    ticker: str,
    framework: str = Query("icif_2013", pattern="^(icif_2013|erm_2017)$"),
):
    """Risk Coverage Cube (IC-IF 2013, default) or ERM 2017 conformance view
    for a ticker's latest risk-loop run. framework=icif_2013|erm_2017."""
    if framework == "erm_2017":
        return get_erm_evidence(ticker)
    return get_icif_cube(ticker)
