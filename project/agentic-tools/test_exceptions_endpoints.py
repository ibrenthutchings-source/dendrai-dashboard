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
    monkeypatch.setattr(ee.deploy_env, "IS_DEVELOPMENT", True)
    monkeypatch.setattr(ee.db, "is_available", lambda: True)
    return TestClient(app)


# ── dev-only 404 gate ─────────────────────────────────────────────────────────

def test_returns_404_outside_development_environment(monkeypatch):
    app = FastAPI()
    app.include_router(ee.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {"username": "tester", "role": "admin", "id": 1}
    monkeypatch.setattr(ee.deploy_env, "IS_DEVELOPMENT", False)
    r = TestClient(app).get("/exceptions/pending")
    assert r.status_code == 404


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
