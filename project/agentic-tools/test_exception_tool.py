#!/usr/bin/env python3
"""
Unit tests for exception_tool.py — heuristic anomaly/uncertainty scoring plus
its delegation to risk_rating_engine.score_exception for risk_rating/
risk_score (Exception Management: curate, risk-rate, delegate). Pure
functions, no DB, no network.

The banding thresholds/impact-category mapping themselves are
risk_rating_engine's own responsibility and are tested exhaustively in
test_risk_rating_engine.py — the tests here verify exception_tool wires its
inputs (severity, connector_risk_tier, process) into that engine correctly
and that the result stays internally consistent, not the exact letter a
given combination lands on.

    pytest test_exception_tool.py -v
"""

from __future__ import annotations

import random

import exception_tool as et
import risk_rating_engine as rre


# ── score_event: existing anomaly/uncertainty behavior unchanged ────────────

def test_score_event_critical_severity_high_anomaly():
    rng = random.Random(1)
    result = et.score_event("x", "CRITICAL", {}, rng=rng)
    assert result["anomaly_score"] >= 0.80
    assert result["requires_human_review"] is True


def test_score_event_info_severity_rarely_requires_review():
    rng = random.Random(1)
    result = et.score_event("x", "INFO", {}, rng=rng)
    assert result["anomaly_score"] < 0.30


def test_score_event_returns_model_version():
    result = et.score_event("x", "LOW", {})
    assert result["model_version"] == et.MODEL_VERSION


def test_score_event_features_only_numeric_and_boolean():
    result = et.score_event("x", "INFO", {"amount": 100, "flagged": True, "note": "text", "nested": {"a": 1}})
    assert result["features"] == {"amount": 100, "flagged": True}


# ── score_event's delegation to risk_rating_engine.score_exception ──────────

def test_score_event_includes_risk_rating_and_risk_score():
    result = et.score_event("x", "CRITICAL", {}, connector_risk_tier="high")
    assert result["risk_rating"] in ("R", "A", "G")
    assert result["risk_score"] > 0


def test_score_event_risk_rating_and_score_agree_with_the_canonical_engine():
    """risk_rating/risk_score must be exactly what risk_rating_engine itself
    would compute for the same inputs — score_event should be a thin pass-
    through, not a second implementation drifting from the first."""
    for severity in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
        for tier in ["critical", "high", "medium", "low", None]:
            for process in ["record_to_report", "iam", None]:
                expected = rre.score_exception(severity, process=process, connector_risk_tier=tier)
                result = et.score_event("x", severity, {}, connector_risk_tier=tier, process=process)
                assert result["risk_rating"] == expected["rag_status"]
                assert result["risk_score"] == expected["score"]


def test_score_event_risk_rating_and_score_are_consistent_with_each_other():
    """The letter must always match the number it's supposedly banding — the
    exact defect this migration closes for predictive_analytics_tool (see
    risk_rating_engine.py's module docstring): a score and its rating
    disagreeing on the same row."""
    result = et.score_event("x", "HIGH", {}, connector_risk_tier="medium", process="payroll")
    assert result["risk_rating"] == rre.rag_of(result["risk_score"])


def test_score_event_risk_rating_independent_of_anomaly_jitter():
    """risk_rating/risk_score must be deterministic given (severity,
    risk_tier, process) even though anomaly/uncertainty have random jitter —
    that's the whole point of it being a separate signal."""
    r1 = et.score_event("x", "MEDIUM", {}, rng=random.Random(1), connector_risk_tier="low")
    r2 = et.score_event("x", "MEDIUM", {}, rng=random.Random(999), connector_risk_tier="low")
    assert r1["risk_rating"] == r2["risk_rating"]
    assert r1["risk_score"] == r2["risk_score"]
    assert r1["anomaly_score"] != r2["anomaly_score"]  # jitter still differs


def test_score_event_unset_tier_defaults_to_medium_bucket():
    """An unclassified connector (risk_tier never set via AI System Inventory)
    must land in the neutral MEDIUM bucket, not silently the lowest-risk
    one — an unclassified connector isn't known to be safe."""
    with_none = et.score_event("x", "CRITICAL", {}, connector_risk_tier=None)
    with_medium = et.score_event("x", "CRITICAL", {}, connector_risk_tier="medium")
    assert with_none["risk_score"] == with_medium["risk_score"]


def test_score_event_case_insensitive_and_whitespace_tolerant_tier():
    """risk_tier values from ai-inventory.jsx are lowercase — must match
    regardless of casing or incidental whitespace."""
    a = et.score_event("x", "CRITICAL", {}, connector_risk_tier="  High  ")
    b = et.score_event("x", "CRITICAL", {}, connector_risk_tier="HIGH")
    assert a["risk_score"] == b["risk_score"]
