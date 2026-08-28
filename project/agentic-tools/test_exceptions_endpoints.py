#!/usr/bin/env python3
"""
Endpoint-level tests for exceptions_endpoints.py — same TestClient +
dependency-override pattern as test_infra_posture_endpoints.py. Covers the
curation/risk-rating/delegation additions: /pending's group/risk_rating/owner
params, the new POST /bulk-triage endpoint, and /summary's new breakdowns.
Pre-existing single-event triage/history/drift endpoints are exercised only
where this change touches their defaults.

    pytest test_exceptions_endpoints.py -v
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_endpoints
import exceptions_endpoints as ee


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ee.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "display_name": "Test Reviewer", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(ee.db, "is_available", lambda: True)
    return TestClient(app)


# ── GET /exceptions/pending ──────────────────────────────────────────────────

def test_get_pending_default_uses_flat_listing(client, monkeypatch):
    captured = {}
    def _fake_flat(limit=100, min_uncertainty=0.0, risk_rating=None, owner=None):
        captured.update(limit=limit, min_uncertainty=min_uncertainty, risk_rating=risk_rating, owner=owner)
        return [{"event_id": 1}]
    monkeypatch.setattr(ee.db, "list_pending_exceptions", _fake_flat)

    r = client.get("/exceptions/pending?risk_rating=R&owner=treasury-team@acme.com")

    assert r.status_code == 200
    assert captured["risk_rating"] == "R"
    assert captured["owner"] == "treasury-team@acme.com"
    assert r.json()["count"] == 1


def test_get_pending_group_true_uses_grouped_listing(client, monkeypatch):
    called = {}
    monkeypatch.setattr(ee.db, "list_pending_exceptions_grouped", lambda **kw: called.update(kw) or [
        {"control_id": "ctrl-1", "system_source": "sap_hana", "occurrence_count": 3},
    ])
    def _fail_flat(*a, **kw):
        raise AssertionError("flat listing should not be called when group=true")
    monkeypatch.setattr(ee.db, "list_pending_exceptions", _fail_flat)

    r = client.get("/exceptions/pending?group=true")

    assert r.status_code == 200
    assert r.json()["rows"][0]["occurrence_count"] == 3
    assert "risk_rating" in called and "owner" in called


def test_get_pending_no_db_returns_empty(client, monkeypatch):
    monkeypatch.setattr(ee.db, "is_available", lambda: False)
    r = client.get("/exceptions/pending")
    assert r.json() == {"rows": [], "count": 0, "resolution_labels": ee._RESOLUTION_LABELS}


# ── POST /exceptions/bulk-triage ─────────────────────────────────────────────

def test_bulk_triage_requires_control_id_and_system_source(client):
    r = client.post("/exceptions/bulk-triage", json={"resolution_label": "BENIGN_OPERATIONAL_NOISE"})
    assert r.status_code == 422


def test_bulk_triage_rejects_invalid_resolution_label(client):
    r = client.post("/exceptions/bulk-triage", json={
        "control_id": "ctrl-1", "system_source": "sap_hana", "resolution_label": "NOT_REAL",
    })
    assert r.status_code == 422


def test_bulk_triage_requires_notes_for_gated_label(client):
    r = client.post("/exceptions/bulk-triage", json={
        "control_id": "ctrl-1", "system_source": "sap_hana", "resolution_label": "TRUE_CONTROL_FAILURE",
    })
    assert r.status_code == 422


def test_bulk_triage_404_when_nothing_pending_for_the_pair(client, monkeypatch):
    monkeypatch.setattr(ee.db, "list_pending_exceptions", lambda limit=1000: [
        {"event_id": 1, "control_id": "ctrl-OTHER", "system_source": "sap_hana"},
    ])
    r = client.post("/exceptions/bulk-triage", json={
        "control_id": "ctrl-1", "system_source": "sap_hana", "resolution_label": "BENIGN_OPERATIONAL_NOISE",
    })
    assert r.status_code == 404


def test_bulk_triage_happy_path_resolves_matching_events_only(client, monkeypatch):
    monkeypatch.setattr(ee.db, "list_pending_exceptions", lambda limit=1000: [
        {"event_id": 1, "control_id": "ctrl-1", "system_source": "sap_hana"},
        {"event_id": 2, "control_id": "ctrl-1", "system_source": "sap_hana"},
        {"event_id": 3, "control_id": "ctrl-1", "system_source": "oracle_fusion"},  # different system_source
        {"event_id": 4, "control_id": "ctrl-OTHER", "system_source": "sap_hana"},   # different control_id
    ])
    captured = {}
    def _fake_bulk(event_ids, auditor, resolution_label, notes):
        captured.update(event_ids=event_ids, auditor=auditor, resolution_label=resolution_label, notes=notes)
        return len(event_ids)
    monkeypatch.setattr(ee.db, "bulk_submit_exception_triage", _fake_bulk)

    r = client.post("/exceptions/bulk-triage", json={
        "control_id": "ctrl-1", "system_source": "sap_hana",
        "resolution_label": "BENIGN_OPERATIONAL_NOISE", "justification_notes": None,
    })

    assert r.status_code == 200
    body = r.json()
    assert body["resolved_count"] == 2
    assert sorted(captured["event_ids"]) == [1, 2]
    assert captured["auditor"] == "Test Reviewer"


def test_bulk_triage_no_db_returns_503(client, monkeypatch):
    monkeypatch.setattr(ee.db, "is_available", lambda: False)
    r = client.post("/exceptions/bulk-triage", json={
        "control_id": "ctrl-1", "system_source": "sap_hana", "resolution_label": "BENIGN_OPERATIONAL_NOISE",
    })
    assert r.status_code == 503


# ── GET /exceptions/summary ──────────────────────────────────────────────────

def test_summary_no_db_returns_new_breakdowns_too(client, monkeypatch):
    monkeypatch.setattr(ee.db, "is_available", lambda: False)
    body = client.get("/exceptions/summary").json()
    assert body["pending_by_owner"] == {}
    assert body["pending_by_risk_rating"] == {}


def test_summary_passes_through_db_result(client, monkeypatch):
    monkeypatch.setattr(ee.db, "get_exception_summary", lambda: {
        "pending_count": 5, "total_events": 50, "resolution_mix": {}, "pending_by_system": {},
        "pending_by_owner": {"treasury-team@acme.com": 5}, "pending_by_risk_rating": {"R": 5},
    })
    body = client.get("/exceptions/summary").json()
    assert body["pending_by_owner"] == {"treasury-team@acme.com": 5}
    assert body["pending_by_risk_rating"] == {"R": 5}


# ── GET /exceptions/report — board/executive period report ──────────────────

def test_report_rejects_malformed_dates(client):
    r = client.get("/exceptions/report", params={"date_from": "not-a-date", "date_to": "2026-08-01"})
    assert r.status_code == 422


def test_report_rejects_date_to_before_date_from(client):
    r = client.get("/exceptions/report", params={"date_from": "2026-08-10", "date_to": "2026-08-01"})
    assert r.status_code == 422


def test_report_group_with_literal_amounts_skips_fair(client, monkeypatch):
    monkeypatch.setattr(ee.db, "list_exceptions_report_grouped", lambda date_from, date_to: [
        {"control_id": "JE-ROUND-DOLLAR", "system_source": "oracle_fusion", "process": "record_to_report",
         "occurrence_count": 3, "worst_risk_rating": "A", "first_seen_at": "2026-08-01T00:00:00",
         "last_seen_at": "2026-08-05T00:00:00", "literal_amount_total": 45000.0, "unpriced_count": 0},
    ])
    fair_called = {"n": 0}
    monkeypatch.setattr(ee.fair_tool, "quantify", lambda **kw: fair_called.update(n=fair_called["n"] + 1) or {"ale": 999})

    body = client.get("/exceptions/report", params={"date_from": "2026-08-01", "date_to": "2026-08-31"}).json()

    assert fair_called["n"] == 0
    assert body["by_control"][0]["impact_usd"] == 45000.0
    assert body["by_control"][0]["impact_source"] == "transaction_amount"
    assert body["summary"]["total_impact_usd"] == 45000.0
    assert body["summary"]["total_occurrences"] == 3
    assert body["summary"]["by_risk_rating"] == {"A": 3}


def test_report_group_with_no_literal_amounts_uses_fair_estimate(client, monkeypatch):
    monkeypatch.setattr(ee.db, "list_exceptions_report_grouped", lambda date_from, date_to: [
        {"control_id": "ITGC-AC-01", "system_source": "sap_hana", "process": "itgc",
         "occurrence_count": 12, "worst_risk_rating": "R", "first_seen_at": "2026-08-01T00:00:00",
         "last_seen_at": "2026-08-20T00:00:00", "literal_amount_total": 0.0, "unpriced_count": 12},
    ])
    captured = {}
    def _fake_quantify(**kw):
        captured.update(kw)
        return {"ale": 123456.0, "tef_mean": 12.0, "tef_source": "empirical"}
    monkeypatch.setattr(ee.fair_tool, "quantify", _fake_quantify)

    body = client.get("/exceptions/report", params={"date_from": "2026-08-01", "date_to": "2026-08-31"}).json()

    assert captured["fire_count_window"] == 12
    assert body["by_control"][0]["impact_usd"] == 123456.0
    assert body["by_control"][0]["impact_source"] == "fair_estimate"
    assert body["summary"]["total_impact_usd"] == 123456.0


def test_report_mixed_group_reports_literal_sum_flagged_partial(client, monkeypatch):
    monkeypatch.setattr(ee.db, "list_exceptions_report_grouped", lambda date_from, date_to: [
        {"control_id": "JE-WEEKEND", "system_source": "netsuite", "process": "record_to_report",
         "occurrence_count": 5, "worst_risk_rating": "G", "first_seen_at": "2026-08-01T00:00:00",
         "last_seen_at": "2026-08-15T00:00:00", "literal_amount_total": 8000.0, "unpriced_count": 2},
    ])
    fair_called = {"n": 0}
    monkeypatch.setattr(ee.fair_tool, "quantify", lambda **kw: fair_called.update(n=fair_called["n"] + 1) or {"ale": 0})

    body = client.get("/exceptions/report", params={"date_from": "2026-08-01", "date_to": "2026-08-31"}).json()

    assert fair_called["n"] == 0  # never blend a real dollar figure with a modeled one
    assert body["by_control"][0]["impact_usd"] == 8000.0
    assert body["by_control"][0]["impact_source"] == "transaction_amount_partial"


def test_report_summary_aggregates_across_multiple_groups(client, monkeypatch):
    monkeypatch.setattr(ee.db, "list_exceptions_report_grouped", lambda date_from, date_to: [
        {"control_id": "A", "system_source": "sap_hana", "process": "itgc", "occurrence_count": 2,
         "worst_risk_rating": "R", "first_seen_at": "x", "last_seen_at": "y",
         "literal_amount_total": 1000.0, "unpriced_count": 0},
        {"control_id": "B", "system_source": "oracle_fusion", "process": "order_to_cash", "occurrence_count": 3,
         "worst_risk_rating": None, "first_seen_at": "x", "last_seen_at": "y",
         "literal_amount_total": 2000.0, "unpriced_count": 0},
    ])
    body = client.get("/exceptions/report", params={"date_from": "2026-08-01", "date_to": "2026-08-31"}).json()

    assert body["summary"]["total_occurrences"] == 5
    assert body["summary"]["controls_affected"] == 2
    assert body["summary"]["total_impact_usd"] == 3000.0
    assert body["summary"]["by_system"] == {"sap_hana": 2, "oracle_fusion": 3}
    assert body["summary"]["by_process"] == {"itgc": 2, "order_to_cash": 3}
    assert body["summary"]["by_risk_rating"] == {"R": 2, "unrated": 3}


def test_report_no_db_returns_empty_shape_not_error(client, monkeypatch):
    monkeypatch.setattr(ee.db, "is_available", lambda: False)
    r = client.get("/exceptions/report", params={"date_from": "2026-08-01", "date_to": "2026-08-31"})
    assert r.status_code == 200
    body = r.json()
    assert body["by_control"] == []
    assert body["summary"]["total_occurrences"] == 0


# ── GET /exceptions/report/detail — drill-down ───────────────────────────────

def test_report_detail_rejects_malformed_dates(client):
    r = client.get("/exceptions/report/detail", params={"date_from": "bad", "date_to": "2026-08-01"})
    assert r.status_code == 422


def test_report_detail_passes_control_id_scope_through(client, monkeypatch):
    captured = {}
    def _fake_detail(date_from, date_to, control_id=None):
        captured.update(date_from=date_from, date_to=date_to, control_id=control_id)
        return [{"event_id": 1, "control_id": control_id}]
    monkeypatch.setattr(ee.db, "list_exceptions_report_detail", _fake_detail)

    r = client.get("/exceptions/report/detail", params={
        "date_from": "2026-08-01", "date_to": "2026-08-31", "control_id": "ITGC-AC-01",
    })

    assert r.status_code == 200
    assert captured["control_id"] == "ITGC-AC-01"
    assert r.json()["events"] == [{"event_id": 1, "control_id": "ITGC-AC-01"}]
