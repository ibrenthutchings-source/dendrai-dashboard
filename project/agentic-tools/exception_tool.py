#!/usr/bin/env python3
"""
Exception scoring — turns a connector event (the uniform
{event_type, actor, resource, severity, raw_payload} shape every
poll-connector adapter returns, real or synthetic; see connector_poller.py)
into the {features, anomaly_score, uncertainty_score, requires_human_review}
tuple Exception Management persists as one control_events + model_inferences
pair (db.insert_exception_event).

There is no trained ML model here — same "labeled, not invented" discipline
as fair_tool.py. anomaly_score is derived directly from the event's own
`severity`, the exact signal Policy-as-Code and the adjudication pipeline
already assign it (mcp_governance._detect_system_flags), not a separately
fabricated number. uncertainty_score is highest near the review threshold
(0.5) and lowest at the extremes — the standard shape for "how confident is
this near the decision boundary" — with a little jitter so same-severity
events don't all score identically.

risk_rating (R/A/G — same vocabulary management_action_plans.risk_rating /
risk_scores.rag_status already use) is a SEPARATE signal from
anomaly_score/uncertainty_score, deliberately: anomaly and uncertainty are
mathematically coupled (uncertainty is a function of anomaly), so neither
tells a reviewer anything uncertainty doesn't already encode. risk_rating
instead combines severity with the connector's own risk_tier
(poll_connectors.risk_tier — see connector_poller._score_exception_event,
which has the full connector dict in hand at scoring time) via
_RISK_RATING_MATRIX below, a real second input independent of the event's
own severity alone.

Called from connector_poller.py's per-event loop only when
deploy_env.IS_DEVELOPMENT — every other environment's ingestion path never
touches this module.
"""
from __future__ import annotations

import random
from typing import Optional

_SEVERITY_BASE = {
    "CRITICAL": 0.90, "HIGH": 0.80, "MEDIUM": 0.55, "WARN": 0.55,
    "WARNING": 0.55, "LOW": 0.20, "INFO": 0.12,
}
_ANOMALY_THRESHOLD = 0.70
_UNCERTAINTY_THRESHOLD = 0.50
MODEL_VERSION = "exception-heuristic-v1"

# connector risk_tier bucket -> severity bucket -> R/A/G. risk_tier values
# from the AI System Inventory screen's classification editor
# (ai-inventory.jsx / PUT /connectors/{id}/classification) are lowercase
# ("critical"/"high"/"medium"/"low") — normalized to upper() before lookup so
# this matches regardless of source casing. An unrecognized/unset tier is
# treated as "MEDIUM" (the same default poll_connectors implicitly has before
# anyone classifies it), never silently as the lowest-risk bucket.
_TIER_BUCKET = {"CRITICAL": "HIGH", "HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}
_SEVERITY_BUCKET = {
    "CRITICAL": "HIGH", "HIGH": "HIGH",
    "MEDIUM": "MEDIUM", "WARN": "MEDIUM", "WARNING": "MEDIUM",
    "LOW": "LOW", "INFO": "LOW",
}
_RISK_RATING_MATRIX = {
    ("HIGH", "HIGH"): "R", ("HIGH", "MEDIUM"): "R", ("HIGH", "LOW"): "A",
    ("MEDIUM", "HIGH"): "R", ("MEDIUM", "MEDIUM"): "A", ("MEDIUM", "LOW"): "G",
    ("LOW", "HIGH"): "A", ("LOW", "MEDIUM"): "G", ("LOW", "LOW"): "G",
}


def _risk_rating(severity: str, connector_risk_tier: Optional[str]) -> str:
    tier_bucket = _TIER_BUCKET.get((connector_risk_tier or "").strip().upper(), "MEDIUM")
    severity_bucket = _SEVERITY_BUCKET.get((severity or "INFO").upper(), "LOW")
    return _RISK_RATING_MATRIX[(tier_bucket, severity_bucket)]


def _numeric_features(raw_payload: Optional[dict]) -> dict:
    """Keeps only numeric/boolean fields from an event's raw_payload — the
    point_in_time_features snapshot a triage reviewer and feature-drift PSI
    actually look at. Strings (actor emails, references, case ids) aren't
    meaningful for either."""
    out: dict = {}
    for k, v in (raw_payload or {}).items():
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = v
    return out


def score_event(event_type: str, severity: str, raw_payload: Optional[dict],
                 rng: Optional[random.Random] = None,
                 connector_risk_tier: Optional[str] = None) -> dict:
    """Returns {features, anomaly_score, uncertainty_score,
    requires_human_review, model_version, risk_rating}. `event_type` is
    accepted for interface symmetry with the connector event shape and
    future per-type tuning, though only `severity` drives anomaly/uncertainty
    today. `connector_risk_tier` (poll_connectors.risk_tier — see this
    module's docstring) drives risk_rating, a genuinely independent signal
    from anomaly/uncertainty."""
    rng = rng or random
    base = _SEVERITY_BASE.get((severity or "INFO").upper(), 0.30)
    anomaly = min(1.0, max(0.0, base + rng.uniform(-0.08, 0.08)))
    uncertainty = min(1.0, max(0.0, 1.0 - 2.0 * abs(anomaly - 0.5) + rng.uniform(-0.05, 0.05)))
    requires_review = anomaly >= _ANOMALY_THRESHOLD or uncertainty >= _UNCERTAINTY_THRESHOLD
    return {
        "features": _numeric_features(raw_payload),
        "anomaly_score": round(anomaly, 4),
        "uncertainty_score": round(uncertainty, 4),
        "requires_human_review": requires_review,
        "model_version": MODEL_VERSION,
        "risk_rating": _risk_rating(severity, connector_risk_tier),
    }
