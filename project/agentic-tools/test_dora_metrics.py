#!/usr/bin/env python3
"""
Unit tests for DORA-style change-management metrics
(db._aggregate_dora_metrics / db.compute_dora_metrics / dora_metrics.py).
No DB needed: _aggregate_dora_metrics is pure, and compute_dora_metrics
degrades to a documented "nothing to report" shape when db.is_available()
is False, same precondition other db-function tests in this suite document.

    pytest test_dora_metrics.py -v
"""

from __future__ import annotations

import db
import dora_metrics


# ── _aggregate_dora_metrics (pure) ───────────────────────────────────────────

def test_aggregate_dora_metrics_computes_deployment_frequency():
    result = db._aggregate_dora_metrics(window_days=30, attestation_count=60, ticket_count=0,
                                         resolved_ticket_hours=[])
    assert result["deployment_frequency_per_day"] == 2.0


def test_aggregate_dora_metrics_computes_change_failure_rate():
    result = db._aggregate_dora_metrics(window_days=30, attestation_count=100, ticket_count=5,
                                         resolved_ticket_hours=[])
    assert result["change_failure_rate"] == 0.05


def test_aggregate_dora_metrics_change_failure_rate_none_when_no_deployments():
    """A rate with no denominator is undefined, not zero — must never
    fabricate a 0% failure rate when there were no deployments to fail."""
    result = db._aggregate_dora_metrics(window_days=30, attestation_count=0, ticket_count=0,
                                         resolved_ticket_hours=[])
    assert result["change_failure_rate"] is None


def test_aggregate_dora_metrics_computes_mttr():
    result = db._aggregate_dora_metrics(window_days=30, attestation_count=10, ticket_count=2,
                                         resolved_ticket_hours=[2.0, 4.0, 6.0])
    assert result["mttr_hours"] == 4.0
    assert result["resolved_ticket_count"] == 3


def test_aggregate_dora_metrics_mttr_none_when_nothing_resolved():
    """None (not 0) — 0 would falsely claim instant resolution when really
    nothing has resolved in the window at all."""
    result = db._aggregate_dora_metrics(window_days=30, attestation_count=10, ticket_count=2,
                                         resolved_ticket_hours=[])
    assert result["mttr_hours"] is None
    assert result["resolved_ticket_count"] == 0


def test_aggregate_dora_metrics_reports_raw_counts_alongside_derived_rates():
    result = db._aggregate_dora_metrics(window_days=7, attestation_count=14, ticket_count=1,
                                         resolved_ticket_hours=[3.5])
    assert result["window_days"] == 7
    assert result["deployment_count"] == 14
    assert result["incident_ticket_count"] == 1


# ── compute_dora_metrics (no-DB degrade path) ────────────────────────────────

def test_compute_dora_metrics_does_not_raise_without_database():
    assert not db.is_available()  # documents the precondition this test relies on
    result = db.compute_dora_metrics(window_days=30)
    # deployment_frequency_per_day is a legitimate 0.0 (window_days is a real,
    # positive denominator even with zero attestations) — only the metrics
    # whose OWN denominator is empty/zero must report None, never a
    # fabricated 0.
    assert result["deployment_frequency_per_day"] == 0.0
    assert result["change_failure_rate"] is None
    assert result["mttr_hours"] is None
    assert result["note"] == "Database not configured"


def test_dora_metrics_module_delegates_to_db():
    result = dora_metrics.compute_dora_metrics(window_days=14)
    assert result["window_days"] == 14
