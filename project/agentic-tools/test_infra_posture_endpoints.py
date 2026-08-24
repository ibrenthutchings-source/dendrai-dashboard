#!/usr/bin/env python3
"""
Endpoint-level tests for infra_posture_endpoints.py — same TestClient +
dependency-override pattern as test_ai_governance_endpoints.py/
test_map_endpoints.py. Two things need stubbing beyond the usual
get_current_user override: deploy_env.IS_DEVELOPMENT (the router's 404 gate —
see _require_dev_environment) and db.is_available().

    pytest test_infra_posture_endpoints.py -v
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_endpoints
import infra_posture_endpoints as ipe


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ipe.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "email": "tester@example.com", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(ipe.deploy_env, "IS_DEVELOPMENT", True)
    monkeypatch.setattr(ipe.db, "is_available", lambda: True)
    return TestClient(app)


# ── dev-only 404 gate ─────────────────────────────────────────────────────────

def test_returns_404_outside_development_environment(monkeypatch):
    """The real enforcement boundary: a non-Development caller must get a
    plain 404, indistinguishable from a route that was never registered —
    not a 403 that would confirm the feature exists."""
    app = FastAPI()
    app.include_router(ipe.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(ipe.deploy_env, "IS_DEVELOPMENT", False)
    client = TestClient(app)
    r = client.get("/infra-posture/summary")
    assert r.status_code == 404


# ── GET /infra-posture/summary ───────────────────────────────────────────────

def test_summary_no_db_returns_zeroed_shape(client, monkeypatch):
    monkeypatch.setattr(ipe.db, "is_available", lambda: False)
    r = client.get("/infra-posture/summary")
    assert r.status_code == 200
    assert r.json()["assets_total"] == 0


def test_summary_combines_vuln_summary_and_expiry_counts(client, monkeypatch):
    monkeypatch.setattr(ipe.db, "get_vulnerability_summary", lambda: {
        "open_by_severity": {"HIGH": 1}, "open_total": 1, "assets_total": 5,
        "assets_assessed": 3, "remediated_last_30d": 0,
    })
    monkeypatch.setattr(ipe.db, "list_expiring_credentials", lambda warn_days: [{"id": 1}])
    monkeypatch.setattr(ipe.db, "list_expiring_infra_assets", lambda warn_days: [{"id": 1}, {"id": 2}])

    r = client.get("/infra-posture/summary?warn_days=15")

    body = r.json()
    assert body["open_total"] == 1
    assert body["expiring_credentials"] == 1
    assert body["expiring_certificates"] == 2


# ── GET /infra-posture/assets ────────────────────────────────────────────────

def test_get_assets_passes_filters_through(client, monkeypatch):
    captured = {}
    def _fake_list(asset_type=None, unassessed_only=False, limit=500):
        captured.update(asset_type=asset_type, unassessed_only=unassessed_only, limit=limit)
        return [{"id": 1, "asset_key": "cert:api:443"}]
    monkeypatch.setattr(ipe.db, "list_infra_assets", _fake_list)

    r = client.get("/infra-posture/assets?asset_type=certificate&unassessed_only=true")

    assert r.status_code == 200
    assert captured["asset_type"] == "certificate"
    assert captured["unassessed_only"] is True
    assert r.json()["assets"][0]["asset_key"] == "cert:api:443"


# ── GET /infra-posture/vulnerabilities ───────────────────────────────────────

def test_get_vulnerabilities_passes_filters_through(client, monkeypatch):
    captured = {}
    def _fake_list(status=None, severity=None, asset_id=None, source=None, limit=500):
        captured.update(status=status, severity=severity)
        return []
    monkeypatch.setattr(ipe.db, "list_infra_vulnerabilities", _fake_list)

    r = client.get("/infra-posture/vulnerabilities?status=open&severity=CRITICAL")

    assert r.status_code == 200
    assert captured == {"status": "open", "severity": "CRITICAL"}


# ── POST /infra-posture/vulnerabilities/{id}/disposition ────────────────────

def test_disposition_requires_reason(client):
    r = client.post("/infra-posture/vulnerabilities/1/disposition", json={"status": "false_positive", "reason": ""})
    assert r.status_code == 422


def test_disposition_rejects_unknown_status(client):
    r = client.post("/infra-posture/vulnerabilities/1/disposition", json={"status": "closed", "reason": "because"})
    assert r.status_code == 422


def test_disposition_false_positive_does_not_create_waiver(client, monkeypatch):
    waiver_calls = []
    monkeypatch.setattr(ipe.db, "create_risk_waiver", lambda *a, **kw: waiver_calls.append(a) or 99)
    monkeypatch.setattr(ipe.db, "update_infra_vulnerability_status", lambda *a: True)

    r = client.post("/infra-posture/vulnerabilities/1/disposition",
                     json={"status": "false_positive", "reason": "not exploitable in this context"})

    assert r.status_code == 200
    assert waiver_calls == []
    assert r.json()["waiver_id"] is None


def test_disposition_accepted_risk_creates_waiver_and_links_it(client, monkeypatch):
    monkeypatch.setattr(ipe.db, "create_risk_waiver", lambda *a, **kw: 77)
    update_calls = []
    monkeypatch.setattr(ipe.db, "update_infra_vulnerability_status", lambda *a: update_calls.append(a) or True)

    r = client.post("/infra-posture/vulnerabilities/42/disposition",
                     json={"status": "accepted_risk", "reason": "compensating control in place",
                           "compensating_control": "WAF rule blocks the vector"})

    assert r.status_code == 200
    body = r.json()
    assert body["waiver_id"] == 77
    assert update_calls[0] == (42, "accepted_risk", None, 77, "compensating control in place", "tester@example.com")


def test_disposition_404_when_vulnerability_not_found(client, monkeypatch):
    monkeypatch.setattr(ipe.db, "create_risk_waiver", lambda *a, **kw: 1)
    monkeypatch.setattr(ipe.db, "update_infra_vulnerability_status", lambda *a: False)
    r = client.post("/infra-posture/vulnerabilities/999/disposition",
                     json={"status": "accepted_risk", "reason": "x"})
    assert r.status_code == 404


def test_disposition_no_db_returns_503(client, monkeypatch):
    monkeypatch.setattr(ipe.db, "is_available", lambda: False)
    r = client.post("/infra-posture/vulnerabilities/1/disposition", json={"status": "false_positive", "reason": "x"})
    assert r.status_code == 503
