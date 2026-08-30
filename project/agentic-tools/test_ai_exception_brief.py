#!/usr/bin/env python3
"""
Tests for POST /ai/exception-brief (ai_endpoints.py) — the same role-tailored
persona-brief mechanism POST /ai/persona-brief already provides for a
risk-loop run (headline/sections/callouts, cached by input hash, held for
human review before delivery), fed a period's Continuous Control Monitoring
exception report (exceptions_endpoints.py's GET /exceptions/report shape)
instead of a risk register.

claude_client.complete_json and db.save_ai_analysis/get_cached_ai_analysis
are faked at the boundary — no real Anthropic call, no real database.

    pytest test_ai_exception_brief.py -v
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ai_endpoints as ae
import auth_endpoints


def _req(persona="BOARD"):
    return {
        "persona": persona,
        "date_from": "2026-08-01",
        "date_to": "2026-08-31",
        "summary": {
            "total_occurrences": 42, "total_impact_usd": 185000.0,
            "controls_total": 3, "controls_shown": 3,
            "by_system": {"sap_hana": 30}, "by_process": {"record_to_report": 42},
            "by_risk_rating": {"R": 10, "unrated": 32},
        },
        "by_control": [
            {"control_id": "JE-ROUND-DOLLAR", "system_source": "sap_hana", "process": "record_to_report",
             "occurrence_count": 12, "worst_risk_rating": "R",
             "impact_usd": 150000.0, "impact_source": "transaction_amount"},
        ],
    }


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ae.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "display_name": "Test Auditor", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(ae.claude_client, "is_available", lambda: True)
    monkeypatch.setattr(ae.db, "get_cached_ai_analysis", lambda *a, **kw: None)
    monkeypatch.setattr(ae.db, "save_ai_analysis", lambda *a, **kw: 99)
    return TestClient(app)


def test_ai_disabled_returns_503(client, monkeypatch):
    monkeypatch.setattr(ae.claude_client, "is_available", lambda: False)
    r = client.post("/ai/exception-brief", json=_req())
    assert r.status_code == 503


def test_generates_a_brief_and_attaches_pending_review(client, monkeypatch):
    fake_result = {
        "headline": "Record-to-report exceptions concentrated in one control this period.",
        "sections": [{"title": "What happened", "body": "JE-ROUND-DOLLAR fired 12 times, $150K in confirmed transaction impact."}],
        "callouts": ["Escalate JE-ROUND-DOLLAR for remediation review."],
    }
    captured = {}
    def _fake_complete_json(system, user, schema, *, label, **kw):
        captured.update(system=system, user=user, schema=schema, label=label)
        return fake_result
    monkeypatch.setattr(ae.claude_client, "complete_json", _fake_complete_json)

    r = client.post("/ai/exception-brief", json=_req(persona="board"))

    assert r.status_code == 200
    body = r.json()
    assert body["headline"] == fake_result["headline"]
    assert body["_review"] == {"id": 99, "status": "pending", "reviewed_by_name": None, "reviewed_at": None}
    # Persona is uppercased before it reaches the prompt/user message.
    assert "Persona: BOARD" in captured["user"]
    assert "2026-08-01 to 2026-08-31" in captured["user"]
    assert captured["label"] == "exception_persona"


def test_impact_source_and_control_fields_reach_the_prompt(client, monkeypatch):
    captured = {}
    def _fake_complete_json(system, user, schema, *, label, **kw):
        captured["user"] = user
        return {"headline": "x", "sections": [], "callouts": []}
    monkeypatch.setattr(ae.claude_client, "complete_json", _fake_complete_json)

    client.post("/ai/exception-brief", json=_req())

    assert "JE-ROUND-DOLLAR" in captured["user"]
    assert "transaction_amount" in captured["user"]
    assert "150000" in captured["user"] or "150000.0" in captured["user"]


def test_cache_hit_skips_the_model_call(client, monkeypatch):
    cached_content = {"headline": "cached", "sections": [], "callouts": []}
    monkeypatch.setattr(ae.db, "get_cached_ai_analysis", lambda *a, **kw: {
        "id": 7, "content": cached_content, "review_status": "approved",
        "reviewed_by_name": "jdoe", "reviewed_at": "2026-08-15T00:00:00Z",
    })
    called = {"n": 0}
    monkeypatch.setattr(ae.claude_client, "complete_json", lambda *a, **kw: called.update(n=called["n"] + 1))

    r = client.post("/ai/exception-brief", json=_req())

    assert called["n"] == 0
    body = r.json()
    assert body["headline"] == "cached"
    assert body["_review"]["status"] == "approved"
    assert body["_review"]["reviewed_by_name"] == "jdoe"


def test_different_periods_do_not_share_a_cache_key(client, monkeypatch):
    """subject_ref keys on persona + period — no run_id exists for exception
    data, so this is the only thing preventing a stale brief from a
    different date range being served for the "same" persona."""
    captured_subject_refs = []
    def _fake_get_cached(kind, run_id, subject_ref, input_hash):
        captured_subject_refs.append(subject_ref)
        return None
    monkeypatch.setattr(ae.db, "get_cached_ai_analysis", _fake_get_cached)
    monkeypatch.setattr(ae.claude_client, "complete_json", lambda *a, **kw: {"headline": "x", "sections": [], "callouts": []})

    req1 = _req(); req1["date_from"], req1["date_to"] = "2026-01-01", "2026-01-31"
    req2 = _req(); req2["date_from"], req2["date_to"] = "2026-02-01", "2026-02-28"
    client.post("/ai/exception-brief", json=req1)
    client.post("/ai/exception-brief", json=req2)

    assert captured_subject_refs[0] != captured_subject_refs[1]
