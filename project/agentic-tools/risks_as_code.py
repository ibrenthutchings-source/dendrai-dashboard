#!/usr/bin/env python3
"""
Risks-as-Code module — translates live pipeline risk signals into
industry-standard artifacts: OSCAL (NIST SP 800-53) and COSO ERM 2017 / ISO 31000:2018.

The module subscribes to the pipeline run state via SSE and re-emits updated
artifacts whenever Stage 2 risk scores are persisted to the database.

Router prefix: /risks-as-code

    POST /risks-as-code/generate              generate + persist artifacts for a run
    GET  /risks-as-code/stream/{run_id}       SSE: live updates as stages complete
    GET  /risks-as-code/export/{run_id}/{fw}  download YAML file
    GET  /risks-as-code/latest/{ticker}       most recent artifacts across all runs
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import db
import embedding_util

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/risks-as-code")

SUPPORTED_FRAMEWORKS = ("oscal", "coso_erm")


# ─────────────────────────────────────────────────────────────────────────────
# Request models
# ─────────────────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    ticker: str
    run_id: Optional[int] = None
    risks: List[Dict[str, Any]] = []
    objectives: List[Dict[str, Any]] = []
    maps: List[Dict[str, Any]] = []
    ratios: Dict[str, Any] = {}
    signals: List[Dict[str, Any]] = []
    industry: str = ""
    period: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Shared utilities
# ─────────────────────────────────────────────────────────────────────────────

def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_yaml(doc: dict) -> str:
    if _HAS_YAML:
        return _yaml.dump(doc, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=120)
    return json.dumps(doc, indent=2, default=str)


_RAG_STATE = {"R": "not-satisfied", "A": "not-satisfied", "G": "satisfied"}

_VELOCITY_LABEL = {
    -1: "improving",
     0: "stable",
     1: "slightly-elevated",
     2: "elevated",
     3: "critical",
}

# COSO ERM 2017's real five components and 20 principles (the previous table
# used "Risk Assessment"/"Risk Response" — those are IC-IF 2013 / ERM *2004*
# component names, not 2017's Governance & Culture / Strategy & Objective-
# Setting / Performance / Review & Revision / Information, Communication &
# Reporting). Reviewed and approved 2026-08-25 — see conversation for the
# per-row rationale; several are judgment calls (a risk category maps to the
# ERM stage most associated with how that risk type is identified/managed,
# not a 1:1 taxonomy — multiple categories legitimately sharing a principle
# is expected, not a bug).
#
# "Macro" was removed: it's not a risk category the engine or any manual-entry
# path ever produces — it's macro-economic context (FRED indicators) that
# adjusts OTHER risks' scores, not a risk in its own right (see
# adjustRiskScores in app.jsx). "Regulatory" and "Supply Chain" were removed
# as genuinely unreachable dead keys: not in risk-engine.js's CATEGORY_IMPACT
# (the 10 categories the loop actually emits) and not in FW_MOCK_RISKS (the
# only other source AdjustRiskModal's category dropdown draws from) —
# Regulatory's ground is already covered by Compliance/Trade Compliance/Legal,
# and Supply Chain was a near-miss of the real "Supply" key.
_COSO_PRINCIPLES: Dict[str, Dict] = {
    "Revenue":             {"principle": 10, "label": "Identifies Risk",                    "component": "Performance"},
    "Financial Reporting": {"principle": 11, "label": "Assesses Severity of Risk",          "component": "Performance"},
    "Operational":         {"principle": 10, "label": "Identifies Risk",                    "component": "Performance"},
    "Supply":              {"principle": 12, "label": "Prioritizes Risks",                  "component": "Performance"},
    "Cybersecurity":       {"principle": 13, "label": "Implements Risk Responses",          "component": "Performance"},
    "Trade Compliance":    {"principle": 8,  "label": "Evaluates Alternative Strategies",   "component": "Strategy & Objective-Setting"},
    "ESG":                 {"principle": 4,  "label": "Demonstrates Commitment to Core Values", "component": "Governance & Culture"},
    "Compliance":          {"principle": 7,  "label": "Defines Risk Appetite",              "component": "Strategy & Objective-Setting"},
    "Legal":               {"principle": 14, "label": "Develops Portfolio View",             "component": "Performance"},
    "Strategic":           {"principle": 9,  "label": "Formulates Business Objectives",      "component": "Strategy & Objective-Setting"},
    "Governance":          {"principle": 3,  "label": "Defines Desired Culture",             "component": "Governance & Culture"},
}
# Explicit, visible fallback — replaces the old silent default (which quietly
# stamped every unrecognised category as principle 9 "Identifies Risk",
# manufacturing a false concentration in one component). An uncategorised
# risk now renders as its own "Unmapped" bucket instead of being absorbed
# into a real principle it was never actually assessed against.
_COSO_UNMAPPED = {"principle": None, "label": "Unmapped", "component": "Unmapped"}

# COSO's classic objective categories (Operations / Reporting / Compliance),
# plus Strategic (added in the 2013 IC-IF update and carried into ERM 2017).
# This is the Risk Coverage Cube's Y-axis — a second, independent judgment
# call from the component mapping above, so it's kept in its own dict rather
# than folded into _COSO_PRINCIPLES. Reviewed and approved 2026-08-25
# (4-category model): Strategic risks are their own row; Revenue, Operational,
# Supply and Cybersecurity are read as core-operations risk (day-to-day
# ability to run the business); Financial Reporting stands alone as
# Reporting; Trade Compliance, Compliance, Legal, ESG and Governance are
# grouped as Compliance — each is fundamentally about conforming to an
# external or internal standard rather than running or reporting the
# business, ESG and Governance included (ESG risk here is predominantly
# disclosure/regulatory-driven; Governance risk is about oversight failing
# to meet an expected standard, not a strategy or reporting failure).
_OBJECTIVE_CATEGORY: Dict[str, str] = {
    "Strategic":           "Strategic",
    "Revenue":             "Operations",
    "Operational":         "Operations",
    "Supply":              "Operations",
    "Cybersecurity":       "Operations",
    "Financial Reporting": "Reporting",
    "Trade Compliance":    "Compliance",
    "Compliance":          "Compliance",
    "Legal":               "Compliance",
    "ESG":                 "Compliance",
    "Governance":          "Compliance",
}
_OBJECTIVE_UNMAPPED = "Unmapped"


# ─────────────────────────────────────────────────────────────────────────────
# COSO Internal Control — Integrated Framework (2013) — still current, and the
# framework that actually has a "cube": COSO replaced the cube with a helix/
# ribbon diagram when it published ERM 2017 above. Added 2026-08-26, reviewed
# and approved, ALONGSIDE the ERM 2017 tables above, not instead of them — the
# Risk Coverage Cube now offers both views rather than mislabelling an
# ERM-2017 cube that actually mixed in ERM 2004's objective categories.
# ─────────────────────────────────────────────────────────────────────────────

ICIF_COMPONENTS = [
    "Control Environment",
    "Risk Assessment",
    "Control Activities",
    "Information & Communication",
    "Monitoring Activities",
    "Unmapped",
]

# IC-IF's real three objective categories, plus an Unmapped catch-all for any
# risk category this dict doesn't yet know about (same purpose as
# _OBJECTIVE_UNMAPPED above). NOT the same list as the ERM-2004-shaped
# OBJECTIVE_CATEGORIES in risk_coverage_cube.py (pre-correction) — that one
# included "Strategic", which belongs to ERM 2004/2013's IC-IF update carried
# it forward for entities that track it separately, but classic IC-IF 2013
# itself has only these three.
ICIF_OBJECTIVES = ["Operations", "Reporting", "Compliance", "Unmapped"]

# Same 11 risk categories as _OBJECTIVE_CATEGORY, reviewed 2026-08-26: IC-IF
# has no "Strategic" objective, so Strategic risks are explicitly OUT OF SCOPE
# for the IC-IF cube — None, not a guess at Operations/Reporting/Compliance,
# and not a fourth row (a fourth row would silently re-commit the ERM-2004-
# labelled-as-IC-IF error this correction exists to fix). The caller
# (risk_coverage_cube.build_icif_cube) counts these risks into
# out_of_icif_scope_risk_count and surfaces them as a footnote, never folds
# them into a cell.
_OBJECTIVE_CATEGORY_ICIF: Dict[str, Optional[str]] = {
    "Strategic":           None,
    "Revenue":             "Operations",
    "Operational":         "Operations",
    "Supply":              "Operations",
    "Cybersecurity":       "Operations",
    "Financial Reporting": "Reporting",
    "Trade Compliance":    "Compliance",
    "Compliance":          "Compliance",
    "Legal":               "Compliance",
    "ESG":                 "Compliance",
    "Governance":          "Compliance",
}

# Deliberately no risk-category -> IC-IF-component dict here. The IC-IF cube's
# component axis (X) is NOT keyed off a risk's category — that was the
# original bug (a risk taxonomy standing in for a control-activity taxonomy).
# It's keyed off the icif_component of whichever controls_catalog control(s)
# are actually mapped to that risk (see framework_mappings.py). A risk with no
# IC-IF-tagged control lands in the "Unmapped" column — a real, informative
# finding, not a structural dead zone. See risk_coverage_cube.build_icif_cube.


# ─────────────────────────────────────────────────────────────────────────────
# COSO ERM 2017 principle evidence sourcing — reviewed and approved 2026-08-26.
#
# NOT a cross-product: a principle exists only under its own component (P7
# exists only under "Strategy & Objective-Setting", never anywhere else).
#
# Each principle names either a real, persisted artifact this app can query
# (`evidence` = a short machine key), or `evidence=None` when no such artifact
# exists anywhere in the schema. `_COSO_PRINCIPLES` above must NEVER be reused
# as "evidence of ERM activity" — it maps risk categories to principles for
# the RaC artifact, a different question (what principle does THIS RISK
# relate to) from what this table answers (is this ERM ACTIVITY evidenced at
# all, anywhere in the app). Reusing it here would re-introduce the exact
# category-as-lifecycle-stage error the ERM component mapping was corrected
# for, through the back door.
#
# `evidence` keys are dispatched by risk_coverage_cube.py's
# _ERM_EVIDENCE_PROBES to the db getter(s) that actually answer them — kept
# declarative here so the principle list and its evidence source are reviewed
# together in one table.
# ─────────────────────────────────────────────────────────────────────────────

ERM_PRINCIPLES: List[Dict[str, Any]] = [
    {"component": "Governance & Culture", "principles": [
        {"number": 1, "label": "Exercises Board Risk Oversight",               "evidence": "gate_approvals"},
        {"number": 2, "label": "Establishes Operating Structures",             "evidence": None},
        {"number": 3, "label": "Defines Desired Culture",                      "evidence": None},
        {"number": 4, "label": "Demonstrates Commitment to Core Values",       "evidence": None},
        {"number": 5, "label": "Attracts, Develops, and Retains Individuals",  "evidence": None},
    ]},
    {"component": "Strategy & Objective-Setting", "principles": [
        {"number": 6, "label": "Analyzes Business Context",           "evidence": "market_context"},
        {"number": 7, "label": "Defines Risk Appetite",               "evidence": "risk_appetite"},
        {"number": 8, "label": "Evaluates Alternative Strategies",    "evidence": "scenario_analysis"},
        {"number": 9, "label": "Formulates Business Objectives",      "evidence": None},
    ]},
    {"component": "Performance", "principles": [
        {"number": 10, "label": "Identifies Risk",             "evidence": "risk_register"},
        {"number": 11, "label": "Assesses Severity of Risk",   "evidence": "risk_scoring"},
        {"number": 12, "label": "Prioritizes Risks",           "evidence": "risk_prioritization"},
        {"number": 13, "label": "Implements Risk Responses",   "evidence": "audit_objectives"},
        {"number": 14, "label": "Develops Portfolio View",     "evidence": "risk_graph"},
    ]},
    {"component": "Review & Revision", "principles": [
        {"number": 15, "label": "Assesses Substantial Change",   "evidence": "change_signals"},
        {"number": 16, "label": "Reviews Risk and Performance",  "evidence": "backtest_review"},
        {"number": 17, "label": "Pursues Improvement in ERM",    "evidence": "loop_calibration"},
    ]},
    {"component": "Information, Communication & Reporting", "principles": [
        {"number": 18, "label": "Leverages Information and Technology",     "evidence": "mcp_ingestion"},
        {"number": 19, "label": "Communicates Risk Information",            "evidence": "notifications"},
        {"number": 20, "label": "Reports on Risk, Culture, and Performance","evidence": "audit_reporting"},
    ]},
]

_ISO_TREATMENT = {"R": "risk_modification", "A": "risk_modification", "G": "risk_retention"}
_COSO_RESPONSE = {"R": "Reduce", "A": "Reduce", "G": "Accept"}
# P13 "Implements Risk Responses" sits in the Performance component
# structurally, for every risk regardless of category — not a per-category
# lookup, so it's named here rather than left as a literal at the call site
# (see the risk_response block below).
_RISK_RESPONSE_COMPONENT = "Performance"


def _index_by_risk(items: list, *keys: str) -> Dict[str, list]:
    """Build a lookup from risk_id → list of items using a set of candidate key names."""
    index: Dict[str, list] = {}
    for item in items:
        refs = []
        for k in keys:
            val = item.get(k)
            if isinstance(val, list):
                refs.extend(val)
            elif isinstance(val, str) and val:
                refs.append(val)
        for ref in refs:
            index.setdefault(ref, []).append(item)
    return index


# ─────────────────────────────────────────────────────────────────────────────
# OSCAL translator  (NIST OSCAL 1.1.2 — Assessment Results)
# ─────────────────────────────────────────────────────────────────────────────

def to_oscal(
    ticker: str,
    risks: list,
    objectives: list,
    maps: list,
    ratios: dict,
    signals: list,
    industry: str,
    period: str,
    run_id: Optional[int] = None,
) -> str:
    """
    Map the Dendrai risk register to an OSCAL Assessment Results document.

    Mapping:
      Risk[]          → assessment-results.results[0].risks[]
      Risk findings   → assessment-results.results[0].findings[]
      MAPs            → risk.remediations[]
      Financial ratios → results[0].observations[]
    """
    obj_by_risk  = _index_by_risk(objectives, "linkedRisks", "linkedRisk", "linked_risk")
    map_by_risk  = _index_by_risk(maps,       "linkedRisks", "linkedRisk", "linked_risk")

    oscal_risks    = []
    oscal_findings = []

    for risk in risks:
        risk_id   = risk.get("id", "")
        risk_uuid = _uid()
        score     = float(risk.get("score", 0))
        rag       = risk.get("rag", "G")
        velocity  = risk.get("velocity", 0)

        characterization = {
            "origin": {
                "actors": [{
                    "type": "tool",
                    "actor-uuid": "dendrai-risk-engine",
                    "title": "Dendrai Risk Engine",
                }]
            },
            "facets": [
                {"name": "likelihood",           "system": "https://dendrai.ai/scoring/v1", "value": str(round(float(risk.get("likelihood", 0)), 2))},
                {"name": "impact",               "system": "https://dendrai.ai/scoring/v1", "value": str(round(float(risk.get("impact", 0)), 2))},
                {"name": "risk-score",           "system": "https://dendrai.ai/scoring/v1", "value": str(round(score, 2))},
                {"name": "rag-status",           "system": "https://dendrai.ai/scoring/v1", "value": rag},
                {"name": "velocity",             "system": "https://dendrai.ai/scoring/v1", "value": str(velocity)},
                {"name": "control-effectiveness","system": "https://dendrai.ai/scoring/v1", "value": risk.get("ce", "ADEQUATE")},
                {"name": "peer-benchmark",       "system": "https://dendrai.ai/scoring/v1", "value": risk.get("peer", "in-line")},
            ],
        }

        remediations = []
        for m in map_by_risk.get(risk_id, []):
            tasks = []
            if m.get("successCriteria"):
                tasks.append({
                    "uuid": _uid(),
                    "type": "action",
                    "title": m.get("successCriteria", ""),
                    "timing": {
                        "within-date-range": {
                            "start": _now()[:10],
                            "end":   m.get("dueDate", ""),
                        }
                    },
                })
            remediations.append({
                "uuid":      _uid(),
                "lifecycle": "recommendation",
                "title":     m.get("finding", f"Management Action Plan — {risk_id}"),
                "description": m.get("action", ""),
                "origins": [{"actors": [{"type": "party", "actor-uuid": "management",
                                         "title": m.get("owner", "Management")}]}],
                "tasks": tasks,
            })

        oscal_risks.append({
            "uuid":  risk_uuid,
            "title": risk.get("name", risk_id),
            "description": risk.get("narrative", ""),
            "status": "open" if rag in ("R", "A") else "closed",
            "characterizations": [characterization],
            "mitigating-factors": [],
            "controls": risk.get("controls", []),
            "remediations": remediations,
            "risk-log": {
                "entries": [{
                    "uuid":  _uid(),
                    "title": f"Scored {score} ({rag}) — velocity {_VELOCITY_LABEL.get(velocity, str(velocity))}",
                    "start": _now(),
                    "logged-by": [{"party-uuid": "dendrai-risk-engine"}],
                    "related-risks": [{"risk-uuid": risk_uuid}],
                }]
            },
        })

        linked_objs = obj_by_risk.get(risk_id, [])
        oscal_findings.append({
            "uuid":  _uid(),
            "title": risk.get("name", risk_id),
            "description": risk.get("narrative", ""),
            "target": {
                "type":      "objective-id",
                "target-id": risk_id,
                "description": f"Category: {risk.get('category', '')} | Industry: {industry}",
                "status": {
                    "state":  _RAG_STATE.get(rag, "not-satisfied"),
                    "reason": f"Score {score} — {rag} — velocity {_VELOCITY_LABEL.get(velocity, str(velocity))}",
                },
            },
            "implementation-statement-uuid": linked_objs[0].get("id", "") if linked_objs else None,
            "related-risks": [{"risk-uuid": oscal_risks[-1]["uuid"]}],
            "remarks": risk.get("filingSnippet", ""),
        })

    red_count   = sum(1 for r in risks if r.get("rag") == "R")
    amber_count = sum(1 for r in risks if r.get("rag") == "A")
    green_count = sum(1 for r in risks if r.get("rag") == "G")

    doc = {
        "assessment-results": {
            "uuid": _uid(),
            "metadata": {
                "title": f"Risk Assessment — {ticker} {period}",
                "published":     _now(),
                "last-modified": _now(),
                "version":       "1.0",
                "oscal-version": "1.1.2",
                "remarks": (
                    f"Generated by Dendrai Risk Loop v2.0. "
                    f"Run ID: {run_id}. Industry: {industry}. "
                    f"Register: {red_count} RED / {amber_count} AMBER / {green_count} GREEN."
                ),
                "roles": [
                    {"id": "cae",          "title": "Chief Audit Executive"},
                    {"id": "cfo",          "title": "Chief Financial Officer"},
                    {"id": "risk-officer", "title": "Risk Officer"},
                    {"id": "tool",         "title": "Dendrai Risk Engine"},
                ],
                "parties": [
                    {"uuid": "dendrai-risk-engine", "type": "tool",
                     "name": "Dendrai Risk Engine v2.0"},
                ],
            },
            "import-ap": {"href": f"#audit-plan-{ticker.lower()}"},
            "local-definitions": {
                "activities": [{
                    "uuid":  _uid(),
                    "title": "Automated Risk Scoring — Signal-Adjusted Six-Stage Loop",
                    "description": (
                        "EDGAR 10-K, FRED macro, RSS, and internal KRI signals are aggregated "
                        "(Stage 1), scored with a signal-adjusted impact × likelihood model "
                        "(Stage 2), mapped to audit objectives (Stage 3), reviewed by HITL "
                        "(Gate 1), resolved through MAPs (Stage 4), and loop-calibrated (Stage 6)."
                    ),
                    "steps": [
                        {"uuid": _uid(), "title": "Stage 1 — Signal Intake",
                         "description": "Aggregate EDGAR, FRED, RSS, and internal KRI signals"},
                        {"uuid": _uid(), "title": "Stage 2 — Risk Assessment",
                         "description": "Score risks using signal-adjusted model (Impact × Likelihood, 0–25 scale)"},
                        {"uuid": _uid(), "title": "Stage 3 — Audit Scope",
                         "description": "Derive prioritised audit objectives from top risks"},
                        {"uuid": _uid(), "title": "Gate 1 — HITL Risk Review",
                         "description": "Human-in-the-loop per-risk disposition (CAE → CFO → Audit Committee)"},
                        {"uuid": _uid(), "title": "Stage 4 — Management Action Plans",
                         "description": "Generate MAPs for high-risk objectives"},
                        {"uuid": _uid(), "title": "Stage 5 — Closure Evidence",
                         "description": "Quantify projected risk reduction from MAP execution"},
                        {"uuid": _uid(), "title": "Stage 6 — Loop Calibration",
                         "description": "Re-calibrate scoring model and set next-run frequency"},
                    ],
                }]
            },
            "results": [{
                "uuid":  _uid(),
                "title": f"Risk Register — {period} Loop Run",
                "description": (
                    f"Signal-adjusted risk register for {ticker} ({industry}). "
                    f"{len(risks)} risks assessed across {len(objectives)} audit objectives."
                ),
                "start": _now(),
                "end":   _now(),
                "reviewed-controls": {
                    "control-selections": [{
                        "description": "Enterprise Risk Management control environment",
                        "include-all": {},
                    }]
                },
                "attestations": [{
                    "parts": [{
                        "name":  "assessment-log",
                        "prose": (
                            f"Risk loop completed for {ticker}. "
                            f"{red_count} RED, {amber_count} AMBER, {green_count} GREEN. "
                            f"M-Score: {ratios.get('m_score', 'N/A')}. "
                            f"Revenue growth: {ratios.get('revenue_growth_pct', 'N/A')}%."
                        ),
                    }]
                }],
                "observations": [{
                    "uuid":  _uid(),
                    "title": "Financial Ratio Analysis — XBRL + Beneish M-Score",
                    "description": (
                        f"Beneish M-Score: {ratios.get('m_score', 'N/A')} "
                        f"({ratios.get('m_score_interpretation', 'N/A')}). "
                        f"Revenue Growth: {ratios.get('revenue_growth_pct', 'N/A')}%. "
                        f"Gross Margin: {ratios.get('gross_margin_pct', 'N/A')}%. "
                        f"DSRI: {ratios.get('dsri', 'N/A')}. "
                        f"AQI: {ratios.get('aqi', 'N/A')}."
                    ),
                    "methods": ["AUTOMATED"],
                    "types":   ["finding"],
                    "relevant-evidence": [{
                        "description": "EDGAR XBRL financial data, FRED macro indicators, RSS industry signals"
                    }],
                }],
                "findings": oscal_findings,
                "risks":    oscal_risks,
            }],
        }
    }

    return _to_yaml(doc)


# ─────────────────────────────────────────────────────────────────────────────
# COSO ERM 2017 / ISO 31000:2018 translator
# ─────────────────────────────────────────────────────────────────────────────

def to_coso_erm(
    ticker: str,
    risks: list,
    objectives: list,
    maps: list,
    ratios: dict,
    signals: list,
    industry: str,
    period: str,
    run_id: Optional[int] = None,
) -> str:
    """
    Map the Dendrai risk register to a COSO ERM 2017 / ISO 31000:2018 YAML document.

    COSO ERM 2017 five components:
      1. Governance & Culture
      2. Strategy & Objective-Setting
      3. Performance (Risk Assessment)
      4. Review & Revision
      5. Information, Communication & Reporting

    ISO 31000:2018 clauses:
      6.4.2 Risk identification
      6.4.3 Risk analysis
      6.4.4 Risk evaluation
      6.5   Risk treatment
    """
    obj_by_risk = _index_by_risk(objectives, "linkedRisks", "linkedRisk", "linked_risk")
    map_by_risk = _index_by_risk(maps,       "linkedRisks", "linkedRisk", "linked_risk")
    sig_by_risk = _index_by_risk(signals,    "affectedRisks")

    risk_universe = []

    for risk in risks:
        risk_id  = risk.get("id", "")
        rag      = risk.get("rag", "G")
        score    = float(risk.get("score", 0))
        inherent = float(risk.get("inherent", round(score * 1.25, 2)))
        residual = float(risk.get("residual", score))
        velocity = risk.get("velocity", 0)
        category = risk.get("category", "Operational")
        coso     = _COSO_PRINCIPLES.get(category, _COSO_UNMAPPED)

        linked_objs = obj_by_risk.get(risk_id, [])
        linked_maps = map_by_risk.get(risk_id, [])
        linked_sigs = sig_by_risk.get(risk_id, [])

        audit_link = None
        if linked_objs:
            o = linked_objs[0]
            audit_link = {
                "objective_id":   o.get("id", ""),
                "title":          o.get("objective", ""),
                "priority":       o.get("priority", ""),
                "sprint":         o.get("sprint", ""),
                "budgeted_hours": o.get("hours", 0),
            }

        entry: Dict[str, Any] = {
            "risk_id":  risk_id,
            "name":     risk.get("name", ""),
            "category": category,

            # COSO ERM alignment
            "coso_component":        coso["component"],
            "coso_principle":        coso["principle"],
            "coso_principle_label":  coso["label"],

            # ISO 31000 clause
            "iso31000_clause": "6.4.2",  # Risk identification

            "context": {
                "internal": (risk.get("narrative", "") or "")[:300],
                "external": next(
                    (s.get("label", "") for s in linked_sigs if s.get("category") == "Market"),
                    "",
                ),
                "filing_evidence": (risk.get("filingSnippet", "") or "")[:400],
            },

            "inherent_risk": {
                "likelihood":  round(float(risk.get("likelihood", 0)), 2),
                "impact":      round(float(risk.get("impact", 0)), 2),
                "score":       round(inherent, 2),
                "rating":      rag,
            },

            "residual_risk": {
                "likelihood":           round(float(risk.get("likelihood", 0)) * 0.85, 2),
                "impact":               round(float(risk.get("impact", 0)), 2),
                "score":                round(residual, 2),
                "control_effectiveness": risk.get("ce", "ADEQUATE"),
                "rating":               rag,
            },

            "velocity":       velocity,
            "velocity_label": _VELOCITY_LABEL.get(velocity, str(velocity)),
            "peer_benchmark": risk.get("peer", "in-line"),
            "controls":       risk.get("controls", []),

            # COSO ERM component 3 — Risk Response
            "risk_response": {
                "coso_component": _RISK_RESPONSE_COMPONENT,
                "strategy": _COSO_RESPONSE.get(rag, "Accept"),
                "owner": linked_maps[0].get("owner", "Risk Owner") if linked_maps else "Risk Owner",
                "actions": [
                    {
                        "description":          m.get("action", ""),
                        "root_cause":           m.get("rootCause", ""),
                        "target_date":          m.get("dueDate", ""),
                        "success_criteria":     m.get("successCriteria", ""),
                        "expected_reduction_pct": m.get("reductionPct", 0),
                    }
                    for m in linked_maps
                ],
            },

            # ISO 31000 clause 6.5 — Risk Treatment
            "iso31000_treatment": {
                "clause":               "6.5",
                "type":                 _ISO_TREATMENT.get(rag, "risk_retention"),
                "monitoring_frequency": "Monthly" if rag == "R" else "Quarterly",
                "review_date": linked_maps[0].get("dueDate", "") if linked_maps else "",
                "kri_monitoring":       rag in ("R", "A"),
            },

            "signals": [
                {
                    "source":   s.get("src", ""),
                    "label":    s.get("label", ""),
                    "delta":    s.get("delta", ""),
                    "velocity": s.get("velocity", 0),
                    "category": s.get("category", ""),
                }
                for s in linked_sigs[:5]
            ],
        }

        if audit_link:
            entry["audit_link"] = audit_link

        risk_universe.append(entry)

    red_count   = sum(1 for r in risks if r.get("rag") == "R")
    amber_count = sum(1 for r in risks if r.get("rag") == "A")
    green_count = sum(1 for r in risks if r.get("rag") == "G")

    doc = {
        "framework":  "COSO ERM 2017 / ISO 31000:2018",
        "generator":  "Dendrai Risk Loop v2.0",
        "entity":     ticker,
        "industry":   industry,
        "period":     period,
        "generated_at": _now(),
        "run_id":     run_id,

        "executive_summary": {
            "total_risks": len(risks),
            "red":   red_count,
            "amber": amber_count,
            "green": green_count,
            "top_risk": risks[0].get("name", "") if risks else "",
            "signal_count": len(signals),
        },

        "financial_context": {
            "m_score":             ratios.get("m_score"),
            "m_score_interpretation": ratios.get("m_score_interpretation"),
            "revenue_growth_pct":  ratios.get("revenue_growth_pct"),
            "gross_margin_pct":    ratios.get("gross_margin_pct"),
            "dsri":                ratios.get("dsri"),
            "aqi":                 ratios.get("aqi"),
        },

        # COSO ERM component 1 — Governance & Culture
        "governance": {
            "coso_component": "Governance & Culture",
            "board_oversight": (
                "Audit Committee reviews risk register quarterly and receives "
                "ad-hoc briefings on RED and appetite-breaching risks."
            ),
            "risk_culture": "Tone-at-the-top supports proactive, data-driven risk management.",
            "three_lines": {
                "first":  "Management — owns and manages risks day-to-day",
                "second": "Risk & Compliance — oversees risk framework and appetite",
                "third":  "Internal Audit — provides independent assurance (this report)",
            },
        },

        # COSO ERM component 5 — Information, Communication & Reporting
        "reporting": {
            "coso_component":  "Information, Communication & Reporting",
            "iso31000_clause": "7.0",
            "cadence":         "Loop runs monthly (configurable); CAE brief after each run",
            "escalation":      "RED risks auto-escalate to CFO within 24 h of detection",
            "audit_trail":     f"Persisted to Dendrai DB run_id={run_id}",
        },

        "risk_universe": risk_universe,
    }

    return _to_yaml(doc)


# ─────────────────────────────────────────────────────────────────────────────
# Core generation helper
# ─────────────────────────────────────────────────────────────────────────────

def _generate_all(data: dict) -> Dict[str, str]:
    """Return {framework_name: yaml_str} for every supported framework."""
    common = dict(
        ticker     = str(data.get("ticker") or ""),
        risks      = data.get("risks", []),
        objectives = data.get("objectives", []),
        maps       = data.get("maps", []),
        ratios     = data.get("ratios", {}),
        signals    = data.get("signals", []),
        industry   = data.get("industry", ""),
        period     = data.get("period", ""),
        run_id     = data.get("run_id"),
    )
    return {
        "oscal":    to_oscal(**common),
        "coso_erm": to_coso_erm(**common),
    }


# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _embed_rac_artifact(ticker: str, artifact_id: Optional[int], framework: str, content: str) -> None:
    """Embed a Risks-as-Code artifact (EMBT_RAC) so it's searchable via
    pgvector, same as Controls-as-Code already is. Best-effort — never raises."""
    if not artifact_id or not content or not embedding_util.is_available():
        return
    try:
        company_id = db.get_company_id(ticker) if ticker else None
        vec = embedding_util.embed_text(content[:8000])
        if vec:
            db.save_embedding(
                source_table="risks_as_code_artifacts", source_id=artifact_id,
                content_type=db.EMBT_RAC, embedding=vec,
                company_id=company_id, text_snippet=f"{framework}: {content[:600]}",
            )
    except Exception:
        pass  # embedding is non-fatal, same as CaC


@router.post("/generate")
async def generate(req: GenerateRequest):
    """Generate OSCAL + COSO ERM artifacts from the supplied risk payload and persist to DB."""
    artifacts = _generate_all(req.model_dump())

    saved: Dict[str, Optional[int]] = {}
    if req.run_id and db.is_available():
        for framework, content in artifacts.items():
            artifact_id = db.save_risks_as_code_artifact(req.run_id, req.ticker, framework, content)
            saved[framework] = artifact_id
            _embed_rac_artifact(req.ticker, artifact_id, framework, content)

    return {
        "ticker":       req.ticker,
        "run_id":       req.run_id,
        "generated_at": _now(),
        "artifacts": {
            fw: {"content": content, "artifact_id": saved.get(fw)}
            for fw, content in artifacts.items()
        },
    }


@router.get("/export/{run_id}/{framework}")
async def export_artifact(run_id: int, framework: str):
    """Download a persisted artifact as a YAML file."""
    if framework not in SUPPORTED_FRAMEWORKS:
        raise HTTPException(status_code=400, detail=f"Unknown framework '{framework}'. Supported: {SUPPORTED_FRAMEWORKS}")

    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured — set DATABASE_URL to enable persistence")

    artifact = db.get_risks_as_code_artifact(run_id, framework)
    if not artifact:
        raise HTTPException(status_code=404, detail=f"No {framework} artifact found for run {run_id}. Run /generate first.")

    filename = f"dendrai_{framework}_{artifact['ticker']}_{run_id}.yaml"
    return StreamingResponse(
        iter([artifact["content"]]),
        media_type="application/x-yaml",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/latest/{ticker}")
async def latest_artifacts(ticker: str):
    """Get the most recent generated artifacts for a ticker (one per framework)."""
    if not db.is_available():
        return {"ticker": ticker.upper(), "artifacts": [], "note": "Database not configured"}

    artifacts = db.get_latest_risks_as_code_artifacts(ticker.upper())
    return {"ticker": ticker.upper(), "artifacts": artifacts}


@router.get("/stream/{run_id}")
async def stream_artifacts(run_id: int):
    """
    SSE stream: polls the DB for new Stage 2 risk scores every 2 s and emits
    fresh OSCAL + COSO ERM artifacts whenever the risk count changes or the
    run is marked completed.

    Events:
      {type: "connected"}  — handshake on open
      {type: "update", artifacts: {oscal: "...", coso_erm: "..."}, ...}
      {type: "done"}       — run completed; stream closes
      {type: "timeout"}    — 2-minute polling window elapsed
      {type: "error"}      — run not found
    """

    async def _generate():
        last_risk_count = -1
        max_polls = 60  # 2 min at 2-second interval

        yield "data: " + json.dumps({"type": "connected", "run_id": run_id}) + "\n\n"

        for _ in range(max_polls):
            await asyncio.sleep(2)

            if not db.is_available():
                yield "data: " + json.dumps({"type": "error", "message": "Database unavailable"}) + "\n\n"
                return

            run = db.get_run_detail(run_id)
            if not run:
                yield "data: " + json.dumps({"type": "error", "message": f"Run {run_id} not found"}) + "\n\n"
                return

            risk_scores = run.get("risk_scores", [])
            current_count = len(risk_scores)
            completed = bool(run.get("completed"))

            if current_count != last_risk_count or completed:
                last_risk_count = current_count

                if risk_scores:
                    artifacts = _generate_all({
                        "ticker":   run.get("ticker", ""),
                        "risks":    risk_scores,
                        "run_id":   run_id,
                        "industry": run.get("industry", ""),
                        "period":   run.get("period_end", ""),
                    })

                    if db.is_available():
                        for fw, content in artifacts.items():
                            artifact_id = db.save_risks_as_code_artifact(run_id, run.get("ticker", ""), fw, content)
                            _embed_rac_artifact(run.get("ticker", ""), artifact_id, fw, content)

                    yield "data: " + json.dumps({
                        "type":        "update",
                        "run_id":      run_id,
                        "risk_count":  current_count,
                        "completed":   completed,
                        "frameworks":  list(artifacts.keys()),
                        "artifacts":   artifacts,
                    }) + "\n\n"

                if completed:
                    yield "data: " + json.dumps({"type": "done", "run_id": run_id}) + "\n\n"
                    return

        yield "data: " + json.dumps({"type": "timeout", "run_id": run_id}) + "\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
