#!/usr/bin/env python3
"""
Endpoint-level tests for je_testing_endpoints.py's grouped/bulk-resolve
additions — the JE Testing half of the "unify the queue" UX-audit
recommendation (Exception Management's grouped view + bulk-triage extended
to JE, sharing db.list_pending_exceptions_grouped(scope="je_testing") and
db.bulk_submit_exception_triage). Same TestClient + dependency-override
pattern as test_exceptions_endpoints.py / test_ai_governance_endpoints.py.

    pytest test_je_testing_endpoints.py -v
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_endpoints
import je_testing_endpoints as jte


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(jte.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "display_name": "Test Reviewer", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(jte.db, "is_available", lambda: True)
    return TestClient(app)


# ── GET /je-testing/findings?group=true ──────────────────────────────────────

def test_get_findings_group_true_uses_grouped_listing_scoped_to_je(client, monkeypatch):
    called = {}
    monkeypatch.setattr(jte.db, "list_pending_exceptions_grouped", lambda **kw: called.update(kw) or [
        {"control_id": "round_dollar", "system_source": "sap_hana", "occurrence_count": 4},
    ])
    def _fail_flat(*a, **kw):
        raise AssertionError("flat listing should not be called when group=true")
    monkeypatch.setattr(jte.db, "list_je_testing_findings", _fail_flat)

    r = client.get("/je-testing/findings?group=true")

    assert r.status_code == 200
    body = r.json()
    assert body["rows"][0]["occurrence_count"] == 4
    assert called["scope"] == "je_testing"


def test_get_findings_default_uses_flat_listing(client, monkeypatch):
    monkeypatch.setattr(jte.db, "list_je_testing_findings", lambda **kw: [{"event_id": 1}])
    monkeypatch.setattr(jte.db, "count_je_testing_findings", lambda **kw: 1)

    r = client.get("/je-testing/findings")

    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_get_findings_no_db_returns_empty(client, monkeypatch):
    monkeypatch.setattr(jte.db, "is_available", lambda: False)
    r = client.get("/je-testing/findings")
    assert r.json()["rows"] == []


# ── POST /je-testing/findings/bulk-disposition ───────────────────────────────

def test_bulk_disposition_requires_rule_id_and_system_source(client):
    r = client.post("/je-testing/findings/bulk-disposition", json={"resolution_label": "BENIGN_OPERATIONAL_NOISE"})
    assert r.status_code == 422


def test_bulk_disposition_rejects_invalid_resolution_label(client):
    r = client.post("/je-testing/findings/bulk-disposition", json={
        "rule_id": "round_dollar", "system_source": "sap_hana", "resolution_label": "NOT_REAL",
    })
    assert r.status_code == 422


def test_bulk_disposition_requires_notes_for_gated_label(client):
    r = client.post("/je-testing/findings/bulk-disposition", json={
        "rule_id": "round_dollar", "system_source": "sap_hana", "resolution_label": "TRUE_CONTROL_FAILURE",
    })
    assert r.status_code == 422


def test_bulk_disposition_404_when_nothing_pending_for_the_pair(client, monkeypatch):
    monkeypatch.setattr(jte.db, "list_je_testing_findings", lambda **kw: [])
    r = client.post("/je-testing/findings/bulk-disposition", json={
        "rule_id": "round_dollar", "system_source": "sap_hana", "resolution_label": "BENIGN_OPERATIONAL_NOISE",
    })
    assert r.status_code == 404


def test_bulk_disposition_happy_path_uses_server_side_filters(client, monkeypatch):
    captured_filters = {}
    def _fake_findings(**kw):
        captured_filters.update(kw)
        return [{"event_id": 1}, {"event_id": 2}]
    monkeypatch.setattr(jte.db, "list_je_testing_findings", _fake_findings)
    captured_bulk = {}
    def _fake_bulk(event_ids, auditor, resolution_label, notes):
        captured_bulk.update(event_ids=event_ids, auditor=auditor, resolution_label=resolution_label, notes=notes)
        return len(event_ids)
    monkeypatch.setattr(jte.db, "bulk_submit_exception_triage", _fake_bulk)

    r = client.post("/je-testing/findings/bulk-disposition", json={
        "rule_id": "round_dollar", "system_source": "sap_hana",
        "resolution_label": "BENIGN_OPERATIONAL_NOISE", "justification_notes": None,
    })

    assert r.status_code == 200
    body = r.json()
    assert body["resolved_count"] == 2
    assert captured_filters["rule_id"] == "round_dollar"
    assert captured_filters["system_source"] == "sap_hana"
    assert captured_filters["only_pending"] is True
    assert sorted(captured_bulk["event_ids"]) == [1, 2]
    assert captured_bulk["auditor"] == "Test Reviewer"


def test_bulk_disposition_no_db_returns_503(client, monkeypatch):
    monkeypatch.setattr(jte.db, "is_available", lambda: False)
    r = client.post("/je-testing/findings/bulk-disposition", json={
        "rule_id": "round_dollar", "system_source": "sap_hana", "resolution_label": "BENIGN_OPERATIONAL_NOISE",
    })
    assert r.status_code == 503
