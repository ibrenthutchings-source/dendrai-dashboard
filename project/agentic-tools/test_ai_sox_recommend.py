#!/usr/bin/env python3
"""
Tests for POST /ai/sox/recommend (ai_endpoints.py) — backfills "Suggest with
AI" to SOX HITL's three Adjust modals (materiality/account/process), the one
capability gap the UX audit found versus Gate 1/2 (which already have it).
claude_client.complete_json and db.save_ai_analysis are faked at the
boundary — no real Anthropic call, no real database.

    pytest test_ai_sox_recommend.py -v
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ai_endpoints as ae
import auth_endpoints


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ae.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "display_name": "Test Auditor", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(ae.claude_client, "is_available", lambda: True)
    monkeypatch.setattr(ae.db, "save_ai_analysis", lambda *a, **kw: None)
    return TestClient(app)


def test_rejects_unknown_kind(client):
    r = client.post("/ai/sox/recommend", json={"ticker": "ACME", "kind": "not_real", "item": {}})
    assert r.status_code == 422


def test_ai_disabled_returns_503(client, monkeypatch):
    monkeypatch.setattr(ae.claude_client, "is_available", lambda: False)
    r = client.post("/ai/sox/recommend", json={"ticker": "ACME", "kind": "materiality", "item": {}})
    assert r.status_code == 503


@pytest.mark.parametrize("kind,fake_result", [
    ("materiality", {
        "recommendation": "adjust", "suggested_materiality_pct": 4.5, "suggested_performance_pct": 70.0,
        "confidence": "medium", "rationale": "Prior-year restatement warrants a tighter basis.",
    }),
    ("account", {
        "recommendation": "approve", "suggested_in_scope": True, "suggested_priority": "P1",
        "confidence": "high", "rationale": "Balance exceeds performance materiality.",
    }),
    ("process", {
        "recommendation": "adjust", "suggested_coverage_level": "P1",
        "confidence": "medium", "rationale": "New system implementation raises control risk.",
    }),
])
def test_each_kind_dispatches_to_its_own_schema_and_returns_the_result(client, monkeypatch, kind, fake_result):
    captured = {}
    def _fake_complete_json(system, user, schema, *, label, **kw):
        captured.update(system=system, user=user, schema=schema, label=label)
        return fake_result
    monkeypatch.setattr(ae.claude_client, "complete_json", _fake_complete_json)

    r = client.post("/ai/sox/recommend", json={
        "ticker": "ACME", "run_id": 7, "kind": kind, "item": {"id": "x"}, "context": {},
    })

    assert r.status_code == 200
    assert r.json() == fake_result
    assert captured["label"] == f"sox_{kind}"
    assert captured["schema"] == ae._SOX_SCHEMAS[kind]
    assert kind in captured["user"]


def test_billing_error_maps_to_402(client, monkeypatch):
    def _raise(*a, **kw):
        raise RuntimeError("credit balance is too low")
    monkeypatch.setattr(ae.claude_client, "complete_json", _raise)
    r = client.post("/ai/sox/recommend", json={"ticker": "ACME", "kind": "process", "item": {}})
    assert r.status_code == 402
