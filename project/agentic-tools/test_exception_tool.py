#!/usr/bin/env python3
"""
Unit tests for exception_tool.py — heuristic anomaly/uncertainty scoring plus
the risk_rating matrix (Exception Management: curate, risk-rate, delegate).
Pure functions, no DB, no network.

    pytest test_exception_tool.py -v
"""

from __future__ import annotations

import random

import exception_tool as et


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


# ── _risk_rating matrix ───────────────────────────────────────────────────────

def test_risk_rating_high_tier_high_severity_is_urgent():
    assert et._risk_rating("CRITICAL", "high") == "R"
    assert et._risk_rating("HIGH", "critical") == "R"


def test_risk_rating_high_tier_medium_severity_is_urgent():
    assert et._risk_rating("MEDIUM", "high") == "R"


def test_risk_rating_high_tier_low_severity_is_moderate():
    assert et._risk_rating("LOW", "high") == "A"


def test_risk_rating_medium_tier_high_severity_is_urgent():
    assert et._risk_rating("CRITICAL", "medium") == "R"


def test_risk_rating_medium_tier_medium_severity_is_moderate():
    assert et._risk_rating("MEDIUM", "medium") == "A"


def test_risk_rating_medium_tier_low_severity_is_low():
    assert et._risk_rating("INFO", "medium") == "G"


def test_risk_rating_low_tier_high_severity_is_moderate():
    assert et._risk_rating("CRITICAL", "low") == "A"


def test_risk_rating_low_tier_low_severity_is_low():
    assert et._risk_rating("INFO", "low") == "G"


def test_risk_rating_unset_tier_defaults_to_medium_bucket():
    """An unclassified connector (risk_tier never set via AI System Inventory)
    must land in the MEDIUM bucket, not silently the lowest-risk one — an
    unclassified connector isn't known to be safe."""
    assert et._risk_rating("CRITICAL", None) == "R"
    assert et._risk_rating("INFO", None) == "G"
    assert et._risk_rating("MEDIUM", "") == "A"


def test_risk_rating_case_insensitive_and_whitespace_tolerant():
    """risk_tier values from ai-inventory.jsx are lowercase — must match
    regardless of casing or incidental whitespace."""
    assert et._risk_rating("CRITICAL", "  High  ") == "R"
    assert et._risk_rating("CRITICAL", "HIGH") == "R"


def test_risk_rating_unrecognized_tier_falls_back_to_medium():
    assert et._risk_rating("CRITICAL", "not-a-real-tier") == "R"  # medium-bucket + high-severity = R


def test_score_event_includes_risk_rating(monkeypatch):
    result = et.score_event("x", "CRITICAL", {}, connector_risk_tier="high")
    assert result["risk_rating"] == "R"


def test_score_event_risk_rating_independent_of_anomaly_jitter():
    """risk_rating must be deterministic given (severity, risk_tier) even
    though anomaly/uncertainty have random jitter — that's the whole point
    of it being a separate signal."""
    r1 = et.score_event("x", "MEDIUM", {}, rng=random.Random(1), connector_risk_tier="low")
    r2 = et.score_event("x", "MEDIUM", {}, rng=random.Random(999), connector_risk_tier="low")
    assert r1["risk_rating"] == r2["risk_rating"] == "G"
    assert r1["anomaly_score"] != r2["anomaly_score"]  # jitter still differs
