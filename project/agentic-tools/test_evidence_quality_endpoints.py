#!/usr/bin/env python3
"""
Endpoint-level tests for evidence_quality_endpoints.py — same TestClient +
dependency-override pattern as test_ai_governance_endpoints.py.

    pytest test_evidence_quality_endpoints.py -v
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_endpoints
import evidence_quality_endpoints as eqe


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(eqe.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(eqe.db, "is_available", lambda: True)
    return TestClient(app)


def test_log_evidence_503_when_db_unavailable(client, monkeypatch):
    monkeypatch.setattr(eqe.db, "is_available", lambda: False)
    r = client.post("/evidence-quality/items", json={"control_id": "SOX-01", "title": "Access list export"})
    assert r.status_code == 503


def test_log_evidence_persists_computed_flags(client, monkeypatch):
    captured = {}

    def _fake_create(**kw):
        captured.update(kw)
        return {"id": 1, **kw}
    monkeypatch.setattr(eqe.db, "create_pbc_evidence", _fake_create)

    r = client.post("/evidence-quality/items", json={
        "control_id": "SOX-01", "title": "Access list export",
        "collected_date": "2025-01-01", "period_start": "2026-01-01", "period_end": "2026-03-31",
        "has_signature": False, "requires_signature": True,
    })
    assert r.status_code == 200
    codes = {f["code"] for f in captured["quality_flags"]}
    assert "PERIOD_MISMATCH" in codes
    assert "UNSIGNED" in codes
    assert captured["created_by"] == "tester"


def test_log_evidence_no_content_check_without_control_description(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(eqe.db, "create_pbc_evidence", lambda **kw: captured.update(kw) or {"id": 1, **kw})

    r = client.post("/evidence-quality/items", json={
        "control_id": "SOX-01", "title": "Access list export", "description": "Screenshot of the access review",
    })
    assert r.status_code == 200
    assert captured["content_check"] is None


def test_log_evidence_content_check_mismatch_adds_flag(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(eqe.db, "create_pbc_evidence", lambda **kw: captured.update(kw) or {"id": 1, **kw})
    monkeypatch.setattr(eqe.claude_client, "complete_text_meta",
                         lambda *a, **kw: ("VERDICT: MISMATCH | Describes payroll, not access review", "end_turn"))

    r = client.post("/evidence-quality/items", json={
        "control_id": "SOX-01", "title": "Access list export",
        "description": "Screenshot of a payroll run", "control_description": "Quarterly user access review",
    })
    assert r.status_code == 200
    assert captured["content_check"]["verdict"] == "MISMATCH"
    codes = {f["code"] for f in captured["quality_flags"]}
    assert "CONTENT_MISMATCH" in codes


def test_log_evidence_content_check_plausible_does_not_add_flag(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(eqe.db, "create_pbc_evidence", lambda **kw: captured.update(kw) or {"id": 1, **kw})
    monkeypatch.setattr(eqe.claude_client, "complete_text_meta",
                         lambda *a, **kw: ("VERDICT: PLAUSIBLE | Matches the described control", "end_turn"))

    from datetime import date
    r = client.post("/evidence-quality/items", json={
        "control_id": "SOX-01", "title": "Access list export",
        "collected_date": date.today().isoformat(), "max_age_days": 365,
        "description": "Screenshot of the quarterly access review sign-off", "control_description": "Quarterly user access review",
    })
    assert r.status_code == 200
    assert captured["content_check"]["verdict"] == "PLAUSIBLE"
    assert captured["quality_flags"] == []


def test_log_evidence_content_check_failure_does_not_block_logging(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(eqe.db, "create_pbc_evidence", lambda **kw: captured.update(kw) or {"id": 1, **kw})

    def _raise(*a, **kw):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(eqe.claude_client, "complete_text_meta", _raise)

    r = client.post("/evidence-quality/items", json={
        "control_id": "SOX-01", "title": "Access list export",
        "description": "Screenshot", "control_description": "Quarterly user access review",
    })
    assert r.status_code == 200
    assert captured["content_check"] is None


# ── GET /evidence-quality/items ───────────────────────────────────────────────

def test_list_evidence_returns_empty_when_db_unavailable(client, monkeypatch):
    monkeypatch.setattr(eqe.db, "is_available", lambda: False)
    r = client.get("/evidence-quality/items")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_list_evidence_passes_filters_through(client, monkeypatch):
    captured = {}

    def _fake_list(control_id=None, flagged_only=False, limit=100):
        captured.update(control_id=control_id, flagged_only=flagged_only, limit=limit)
        return []
    monkeypatch.setattr(eqe.db, "list_pbc_evidence", _fake_list)

    r = client.get("/evidence-quality/items?control_id=SOX-01&flagged_only=true&limit=25")
    assert r.status_code == 200
    assert captured == {"control_id": "SOX-01", "flagged_only": True, "limit": 25}


# ── GET /evidence-quality/items/{id} ─────────────────────────────────────────

def test_get_evidence_404_when_missing(client, monkeypatch):
    monkeypatch.setattr(eqe.db, "get_pbc_evidence", lambda evidence_id: None)
    r = client.get("/evidence-quality/items/999")
    assert r.status_code == 404


def test_get_evidence_returns_item(client, monkeypatch):
    monkeypatch.setattr(eqe.db, "get_pbc_evidence", lambda evidence_id: {"id": 1, "control_id": "SOX-01"})
    r = client.get("/evidence-quality/items/1")
    assert r.status_code == 200
    assert r.json()["control_id"] == "SOX-01"
