#!/usr/bin/env python3
"""
Endpoint-level tests for map_endpoints.py — same TestClient +
dependency-override pattern as test_ai_governance_endpoints.py /
test_itsm_endpoints.py.

    pytest test_map_endpoints.py -v
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_endpoints
import map_endpoints as me


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(me.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "display_name": "Test Reviewer", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(me.db, "is_available", lambda: True)
    return TestClient(app)


def _map(**over) -> dict:
    base = {
        "id": 1, "map_ref": "MAP-CM-000001", "control_id": "JE-SOD-PREPARER-APPROVER",
        "system_source": "oracle_fusion", "finding": "Recurring SoD conflict",
        "root_cause": "Delegation loophole", "risk_rating": "R", "action": "Close the loophole",
        "owner": None, "due_date": "2026-09-01", "success_criteria": "Zero findings for 30 days",
        "reduction_pct": None, "completion_pct": 0, "occurrence_count": 4, "window_days": 30,
        "status": "proposed",
    }
    base.update(over)
    return base


# ── GET /maps ──────────────────────────────────────────────────────────────

def test_list_maps_returns_empty_when_db_unavailable(client, monkeypatch):
    monkeypatch.setattr(me.db, "is_available", lambda: False)
    r = client.get("/maps")
    assert r.status_code == 200
    assert r.json() == {"maps": []}


def test_list_maps_passes_filters_through(client, monkeypatch):
    captured = {}

    def _fake_list(status=None, limit=100):
        captured.update(status=status, limit=limit)
        return [_map()]
    monkeypatch.setattr(me.db, "list_maps", _fake_list)

    r = client.get("/maps?status=proposed&limit=10")
    assert r.status_code == 200
    assert captured == {"status": "proposed", "limit": 10}
    assert r.json()["maps"][0]["map_ref"] == "MAP-CM-000001"


# ── GET /maps/{map_ref} ──────────────────────────────────────────────────────

def test_get_map_404_when_missing(client, monkeypatch):
    monkeypatch.setattr(me.db, "get_map", lambda map_ref: None)
    r = client.get("/maps/MAP-CM-999999")
    assert r.status_code == 404


def test_get_map_returns_map(client, monkeypatch):
    monkeypatch.setattr(me.db, "get_map", lambda map_ref: _map())
    r = client.get("/maps/MAP-CM-000001")
    assert r.status_code == 200
    assert r.json()["control_id"] == "JE-SOD-PREPARER-APPROVER"


# ── POST /maps/{map_ref}/decision ────────────────────────────────────────────

def test_decision_rejects_bad_decision_value(client):
    r = client.post("/maps/MAP-CM-000001/decision", json={"decision": "maybe"})
    assert r.status_code == 422


def test_decision_rejects_non_editable_field_in_adjustments(client):
    r = client.post("/maps/MAP-CM-000001/decision", json={
        "decision": "approved", "adjustments": {"control_id": "hack-the-control"},
    })
    assert r.status_code == 422


def test_decision_requires_comment_when_rejecting(client):
    r = client.post("/maps/MAP-CM-000001/decision", json={"decision": "rejected"})
    assert r.status_code == 422


def test_decision_approves_and_passes_reviewer_identity(client, monkeypatch):
    captured = {}

    def _fake_decide(map_ref, decision, reviewer_name, comment, adjustments):
        captured.update(map_ref=map_ref, decision=decision, reviewer_name=reviewer_name,
                         comment=comment, adjustments=adjustments)
        return _map(status="approved", risk_rating=adjustments.get("risk_rating", "R"))
    monkeypatch.setattr(me.db, "decide_map", _fake_decide)

    r = client.post("/maps/MAP-CM-000001/decision", json={
        "decision": "approved", "comment": "Agreed, proceeding", "adjustments": {"risk_rating": "A"},
    })
    assert r.status_code == 200
    assert captured["reviewer_name"] == "Test Reviewer"
    assert captured["decision"] == "approved"
    assert captured["adjustments"] == {"risk_rating": "A"}
    assert r.json()["map"]["status"] == "approved"


def test_decision_409_when_map_not_awaiting_review(client, monkeypatch):
    monkeypatch.setattr(me.db, "decide_map", lambda *a, **kw: None)
    monkeypatch.setattr(me.db, "get_map", lambda map_ref: _map(status="approved"))
    r = client.post("/maps/MAP-CM-000001/decision", json={"decision": "approved"})
    assert r.status_code == 409


def test_decision_404_when_map_does_not_exist(client, monkeypatch):
    monkeypatch.setattr(me.db, "decide_map", lambda *a, **kw: None)
    monkeypatch.setattr(me.db, "get_map", lambda map_ref: None)
    r = client.post("/maps/MAP-CM-999999/decision", json={"decision": "approved"})
    assert r.status_code == 404


# ── PUT /maps/{map_ref}/progress ─────────────────────────────────────────────

def test_progress_rejects_out_of_range_value(client):
    r = client.put("/maps/MAP-CM-000001/progress", json={"completion_pct": 150})
    assert r.status_code == 422


def test_progress_updates_and_returns_map(client, monkeypatch):
    monkeypatch.setattr(me.db, "update_map_progress", lambda map_ref, pct: _map(status="in_progress", completion_pct=pct))
    r = client.put("/maps/MAP-CM-000001/progress", json={"completion_pct": 40})
    assert r.status_code == 200
    assert r.json()["map"]["completion_pct"] == 40


def test_progress_409_when_map_has_no_execution_to_track(client, monkeypatch):
    monkeypatch.setattr(me.db, "update_map_progress", lambda map_ref, pct: None)
    monkeypatch.setattr(me.db, "get_map", lambda map_ref: _map(status="proposed"))
    r = client.put("/maps/MAP-CM-000001/progress", json={"completion_pct": 40})
    assert r.status_code == 409


# ── POST /maps/detect ────────────────────────────────────────────────────────

def test_trigger_detection_returns_proposed_count(client, monkeypatch):
    async def _fake_sweep_once():
        return 3
    monkeypatch.setattr(me.map_detection_sweep, "sweep_once", _fake_sweep_once)
    r = client.post("/maps/detect")
    assert r.status_code == 200
    assert r.json() == {"proposed": 3}
