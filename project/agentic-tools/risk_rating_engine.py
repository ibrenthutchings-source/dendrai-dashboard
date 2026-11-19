#!/usr/bin/env python3
"""
Canonical risk rating/scoring engine — ONE methodology for the whole platform.

Every risk score in Dendrai lands on the same 0-25 scale
(impact 0-5 x likelihood 0-5) and the same R/A/G bands (R >= 15, A >= 9,
G below), regardless of what produced it: the Enterprise Risk Loop's own
register, a segment/geography risk, an operational control exception, or a
predictive-analytics run. This module is where those constants live.

── Why this module exists ────────────────────────────────────────────────────

Before it, four implementations disagreed:

    risk-engine.js  ragOf/buildRisks    0-25, >=15/>=9, "R"/"A"/"G"
    segment_risk_tool._rag              0-25, >=15/>=9, "R"/"A"/"G"
    exception_tool._risk_rating         NO SCORE — a severity x tier lookup
    predictive_analytics_tool           0-10, >=7.0/>=5.0, "Red"/"Amber"/"Green"

Three already agreed, so 0-25 / 15 / 9 / letters is the house standard and
predictive_analytics_tool was the outlier. That mismatch was not cosmetic:
mcp-data.js's mergeRiskScores rescaled predictive's score to 0-25
(score * 2.5) while taking the RAG letter from predictive's UNSCALED 0-10
banding, so a predictive score of 6.0 rendered as "15.0" wearing an Amber
badge — while risk-engine.js's own ragOf(15.0), the Board Risk Profile's
appetite threshold, and Coverage Gap Analysis all call >= 15 Red. Score and
rating on the same row contradicted each other.

── What is a port and what is new ────────────────────────────────────────────

PORTED VERBATIM from risk-engine.js (project/risk-engine.js), which stays the
client-side source of truth for the Enterprise Risk Loop itself:

    rag_of()            <- ragOf, line 16
    CATEGORY_IMPACT     <- CATEGORY_IMPACT, lines 46-57
    DEFAULT_IMPACT      <- its `CATEGORY_IMPACT[t.category] || 3` fallback
    score_from()        <- the impact x likelihood shape in buildRisks(),
                           lines 660-687, including the 0.5-5.0 likelihood clamp

NEW here, with no equivalent in risk-engine.js — these are design decisions,
not ports, and are called out as such so a later reader does not mistake them
for constants lifted from somewhere authoritative:

    CATEGORY_ALIASES        predictive_analytics_tool's INDUSTRY_TEMPLATES use
                            their own category words (Regulatory / Financial /
                            Macro) that CATEGORY_IMPACT has no entry for.
                            Aliased rather than editing either vocabulary in
                            place, so neither file has to be rewritten to
                            match the other.
    PROCESS_CATEGORY        connector process slug -> impact category, so an
                            exception (which has no financial ratios and no
                            category of its own) still gets a real impact.
    SEVERITY_LIKELIHOOD     event severity -> likelihood, the exception-side
    TIER_LIKELIHOOD_DELTA   stand-in for the ratio-driven likelihood
                            buildRisks() computes for a register risk.

── Vocabulary note ───────────────────────────────────────────────────────────

rag_status here is always a LETTER ("R"/"A"/"G"), matching
risk_scores.rag_status as written by the risk loop, management_action_plans.
risk_rating, and exception_model_inferences.risk_rating.

The full-word "Red"/"Amber"/"Green" labels on Beneish M-score and Altman
Z-score results (predictive_analytics_tool lines 648-716, 787) are a
DIFFERENT semantic — financial-health bands, not register risk scores — and
are deliberately NOT routed through this module. app.jsx matches those on the
literal strings.
"""
from __future__ import annotations

from typing import Optional

# ── Bands ─────────────────────────────────────────────────────────────────────
# Verbatim port of risk-engine.js line 16:
#     const ragOf = s => s >= 15 ? 'R' : s >= 9 ? 'A' : 'G';
RAG_RED_THRESHOLD = 15.0
RAG_AMBER_THRESHOLD = 9.0

# The 0-25 scale's own bounds: impact (0-5) x likelihood (0-5).
SCORE_MIN = 0.0
SCORE_MAX = 25.0

# Likelihood clamp, from buildRisks() line 665:
#     const likelihood = clamp(rawScore / 2, 0.5, 5.0);
LIKELIHOOD_MIN = 0.5
LIKELIHOOD_MAX = 5.0


def rag_of(score: Optional[float]) -> str:
    """0-25 score -> "R" | "A" | "G". A missing score is Green rather than
    an exception — same permissive shape as the JS original, which has no
    null branch because a risk always carries a number by the time it is
    banded."""
    s = float(score or 0.0)
    if s >= RAG_RED_THRESHOLD:
        return "R"
    if s >= RAG_AMBER_THRESHOLD:
        return "A"
    return "G"


# ── Impact by category ────────────────────────────────────────────────────────
# Verbatim port of risk-engine.js's CATEGORY_IMPACT (lines 46-57), 0-5 scale.
CATEGORY_IMPACT = {
    "Revenue":             4,
    "Operational":         3,
    "Financial Reporting": 4,
    "Supply":              4,
    "Cybersecurity":       4,
    "Trade Compliance":    5,
    "ESG":                 2,
    "Compliance":          3,
    "Legal":               3,
    "Strategic":           3,
}
DEFAULT_IMPACT = 3  # matches risk-engine.js's `CATEGORY_IMPACT[t.category] || 3`

# NEW (see module docstring). predictive_analytics_tool.INDUSTRY_TEMPLATES
# categorises risks with words CATEGORY_IMPACT has no entry for. Without these
# aliases every such risk would silently take DEFAULT_IMPACT, flattening real
# differences (a Regulatory risk and a Macro risk would score identically).
CATEGORY_ALIASES = {
    "Regulatory": "Compliance",
    "Financial":  "Financial Reporting",
    "Macro":      "Strategic",
}

# NEW (see module docstring). Connector process slug -> impact category.
# Vocabulary confirmed against api_server.py's _SYNTHETIC_CONNECTORS_DEFAULT.
# Real (non-synthetic) connectors carry a free-form extra_config that may have
# no "process" at all — those fall through to DEFAULT_IMPACT, the same
# discipline CATEGORY_IMPACT's own `|| 3` fallback already uses.
PROCESS_CATEGORY = {
    "hire_to_retire":       "Operational",
    "iam":                  "Cybersecurity",
    "order_to_cash":        "Revenue",
    "procure_to_pay":       "Financial Reporting",
    "record_to_report":     "Financial Reporting",
    "fixed_assets":         "Financial Reporting",
    "vendor_management":    "Compliance",
    "payroll":              "Financial Reporting",
    "receive_to_ship":      "Supply",
    "inventory_master":     "Supply",
    "customer_master_file": "Operational",
}


def canonical_category(category: Optional[str] = None,
                        process: Optional[str] = None) -> Optional[str]:
    """Resolve a category name (alias-aware) or a connector process slug to a
    CATEGORY_IMPACT key. Returns None when neither resolves, so the caller can
    tell "fell back to the default" apart from "explicitly Operational"."""
    if category:
        c = str(category).strip()
        if c in CATEGORY_IMPACT:
            return c
        aliased = CATEGORY_ALIASES.get(c)
        if aliased:
            return aliased
    if process:
        mapped = PROCESS_CATEGORY.get(str(process).strip().lower())
        if mapped:
            return mapped
    return None


def impact_for(category: Optional[str] = None,
                process: Optional[str] = None) -> int:
    """Impact on the 0-5 scale. `category` wins over `process` when both are
    given (a risk that names its own category is more specific than the
    process it happened to arrive through)."""
    return CATEGORY_IMPACT.get(canonical_category(category, process), DEFAULT_IMPACT)


# ── Scoring ───────────────────────────────────────────────────────────────────

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def score_from(impact: float, likelihood: float) -> dict:
    """The canonical calculation, matching buildRisks() lines 665-678:
    likelihood is clamped to 0.5-5.0, score is impact x likelihood on the
    0-25 scale rounded to 1dp, and rag_status is that score banded.

    Returns {impact, likelihood, score, rag_status}."""
    lik = clamp(float(likelihood), LIKELIHOOD_MIN, LIKELIHOOD_MAX)
    imp = float(impact)
    score = round(clamp(imp * lik, SCORE_MIN, SCORE_MAX), 1)
    return {
        "impact":     round(imp, 1),
        "likelihood": round(lik, 1),
        "score":      score,
        "rag_status": rag_of(score),
    }


def score_from_raw10(raw_score: float, category: Optional[str] = None,
                      process: Optional[str] = None) -> dict:
    """For a scorer that natively produces a 1-10 intermediate (the shape
    predictive_analytics_tool's industry templates and risk-engine.js's own
    `base + delta` both produce): convert it to the canonical 0-25 result
    exactly as buildRisks() does — halve it into a 0-5 likelihood, multiply by
    the category's impact.

    This is the migration path for any existing 0-10 scorer: keep its template
    logic, hand the intermediate here, and it lands on the shared scale."""
    likelihood = clamp(float(raw_score) / 2.0, LIKELIHOOD_MIN, LIKELIHOOD_MAX)
    return score_from(impact_for(category, process), likelihood)


# ── Exception scoring ─────────────────────────────────────────────────────────
# NEW (see module docstring). An operational control exception has no financial
# ratios, so its likelihood comes from the event's own severity — the same
# signal Policy-as-Code and the adjudication pipeline already assign it — with
# the connector's configured risk_tier applying a delta on top.
#
# Keeping risk_tier as a modifier (rather than folding it into impact, or
# dropping it) preserves the property exception_tool.py's docstring calls out
# as deliberate: risk_tier is a genuinely independent second input, not
# something already implied by severity.
SEVERITY_LIKELIHOOD = {
    "CRITICAL": 5.0,
    "HIGH":     4.0,
    "MEDIUM":   2.5,
    "WARN":     2.5,
    "WARNING":  2.5,
    "LOW":      1.0,
    "INFO":     0.5,
}
DEFAULT_SEVERITY_LIKELIHOOD = 0.5  # an unrecognised severity is treated as INFO

# risk_tier values arrive lowercase from the AI System Inventory screen's
# classification editor (ai-inventory.jsx / PUT /connectors/{id}/classification)
# — normalised to upper() before lookup. An unset/unrecognised tier is treated
# as MEDIUM (the neutral delta), never silently as the lowest-risk bucket.
TIER_LIKELIHOOD_DELTA = {
    "CRITICAL": 0.5,
    "HIGH":     0.5,
    "MEDIUM":   0.0,
    "LOW":     -0.5,
}
DEFAULT_TIER_DELTA = 0.0


def score_exception(severity: str, process: Optional[str] = None,
                     connector_risk_tier: Optional[str] = None) -> dict:
    """Score one control exception onto the canonical 0-25 / R-A-G scale.

    Returns {impact, likelihood, score, rag_status} — the same shape every
    other scorer in this module returns, so an exception's number means the
    same thing as a register risk's number.
    """
    base = SEVERITY_LIKELIHOOD.get(
        str(severity or "INFO").strip().upper(), DEFAULT_SEVERITY_LIKELIHOOD)
    delta = TIER_LIKELIHOOD_DELTA.get(
        str(connector_risk_tier or "").strip().upper(), DEFAULT_TIER_DELTA)
    return score_from(impact_for(process=process), base + delta)


# ── Velocity ──────────────────────────────────────────────────────────────────
# Verbatim port of risk-engine.js's velOf (lines 23-30) — the deltas are
# already expressed on the 0-25 scale there, so they carry over unchanged.
# Shared so anything modelling escalation (grey swan, projections) speaks the
# same language as the register's own velocity column instead of inventing
# its own step sizes.
VELOCITY_BANDS = [
    (3.75, 3),
    (1.75, 2),
    (0.25, 1),
]
VELOCITY_DECLINE_THRESHOLD = -2.0


def velocity_of(score: float, base: float) -> int:
    """Signed velocity in {-1, 0, 1, 2, 3} from a score's distance above its
    own baseline, on the 0-25 scale."""
    d = float(score) - float(base)
    for threshold, v in VELOCITY_BANDS:
        if d > threshold:
            return v
    if d < VELOCITY_DECLINE_THRESHOLD:
        return -1
    return 0


def escalation_step(velocity_level: int) -> float:
    """The 0-25-scale score delta one velocity band represents — the inverse
    of velocity_of. Used to project an escalation forward (grey swan's
    stage ladder) in the register's own units rather than arbitrary constants.

    velocity_of(base + escalation_step(v), base) == v holds for v in 1..3.
    """
    for threshold, v in VELOCITY_BANDS:
        if v == velocity_level:
            # Just past the band's lower edge — the smallest delta that
            # actually reads as this velocity level.
            return round(threshold + 0.25, 2)
    return 0.0
