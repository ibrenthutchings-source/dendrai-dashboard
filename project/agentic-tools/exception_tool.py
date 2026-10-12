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
risk_scores.rag_status already use) and risk_score (0-25) are a SEPARATE
signal from anomaly_score/uncertainty_score, deliberately: anomaly and
uncertainty are mathematically coupled (uncertainty is a function of
anomaly), so neither tells a reviewer anything uncertainty doesn't already
encode. risk_rating/risk_score instead come from risk_rating_engine.
score_exception(), the SAME canonical 0-25 / R-A-G methodology risk-engine.js
uses for the Enterprise Risk Loop's own register — severity drives likelihood,
the connector's own risk_tier (poll_connectors.risk_tier — see
connector_poller._score_exception_event, which has the full connector dict
in hand at scoring time) applies a real, independent modifier on top, and
process drives impact via risk_rating_engine.PROCESS_CATEGORY. Before this,
risk_rating came from a hand-tuned severity x tier lookup matrix with no
numeric score at all — a disconnected methodology from Risk Assessment; see
risk_rating_engine.py's module docstring for the full history.

Called from connector_poller.py's per-event loop only when
deploy_env.IS_DEVELOPMENT — every other environment's ingestion path never
touches this module.
"""
from __future__ import annotations

import random
from typing import Optional

import risk_rating_engine

_SEVERITY_BASE = {
    "CRITICAL": 0.90, "HIGH": 0.80, "MEDIUM": 0.55, "WARN": 0.55,
    "WARNING": 0.55, "LOW": 0.20, "INFO": 0.12,
}
_ANOMALY_THRESHOLD = 0.70
_UNCERTAINTY_THRESHOLD = 0.50
MODEL_VERSION = "exception-heuristic-v1"


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
                 connector_risk_tier: Optional[str] = None,
                 process: Optional[str] = None) -> dict:
    """Returns {features, anomaly_score, uncertainty_score,
    requires_human_review, model_version, risk_rating, risk_score}.
    `event_type` is accepted for interface symmetry with the connector event
    shape and future per-type tuning, though only `severity` drives
    anomaly/uncertainty today. `connector_risk_tier` and `process`
    (poll_connectors.risk_tier / extra_config.process — see this module's
    docstring) drive risk_rating/risk_score via risk_rating_engine.
    score_exception, a genuinely independent signal from anomaly/uncertainty."""
    rng = rng or random
    base = _SEVERITY_BASE.get((severity or "INFO").upper(), 0.30)
    anomaly = min(1.0, max(0.0, base + rng.uniform(-0.08, 0.08)))
    uncertainty = min(1.0, max(0.0, 1.0 - 2.0 * abs(anomaly - 0.5) + rng.uniform(-0.05, 0.05)))
    requires_review = anomaly >= _ANOMALY_THRESHOLD or uncertainty >= _UNCERTAINTY_THRESHOLD
    risk = risk_rating_engine.score_exception(severity, process=process, connector_risk_tier=connector_risk_tier)
    return {
        "features": _numeric_features(raw_payload),
        "anomaly_score": round(anomaly, 4),
        "uncertainty_score": round(uncertainty, 4),
        "requires_human_review": requires_review,
        "model_version": MODEL_VERSION,
        "risk_rating": risk["rag_status"],
        "risk_score": risk["score"],
    }
