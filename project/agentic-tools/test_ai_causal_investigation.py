#!/usr/bin/env python3
"""
Tests for POST /ai/five-whys and POST /ai/fishbone (ai_endpoints.py) — root-
cause HYPOTHESIS generation seeded strictly from one real, already-computed
process-mining finding (a CausalSeedFinding: process/source/target/avg_hours/
count/basis), never free text. See ai_endpoints.py's module comment above
both endpoints for the three guardrails this suite checks for: a real seed
only, schema-forced evidence/assumption self-labeling, and no semantic-search
embedding of the result.

claude_client.complete_json and db.save_ai_analysis/get_cached_ai_analysis
are faked at the boundary — no real Anthropic call, no real database.

    pytest test_ai_causal_investigation.py -v
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ai_endpoints as ae
import auth_endpoints


def _finding(**overrides):
    base = {
        "process": "procure_to_pay", "process_label": "Procure to Pay",
        "source": "Invoice Matched", "target": "Payment Released",
        "avg_hours": 212.4, "count": 7, "basis": "bottleneck",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ae.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "display_name": "Test Operator", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(ae.claude_client, "is_available", lambda: True)
    monkeypatch.setattr(ae.db, "get_cached_ai_analysis", lambda *a, **kw: None)
    monkeypatch.setattr(ae.db, "save_ai_analysis", lambda *a, **kw: 99)
    # Neither endpoint should ever call this — see the "never embedded"
    # guardrail; if either does, calling the real embedding pipeline in a
    # test with no DB/model would raise and fail the test loudly.
    def _unexpected_embed(*a, **kw):
        raise AssertionError("_embed_ai_summary must never be called for a causal-investigation result")
    monkeypatch.setattr(ae, "_embed_ai_summary", _unexpected_embed)
    return TestClient(app)


_FAKE_WHYS = {
    "problem_statement": "Invoice Matched -> Payment Released averages 212.4 hours across 7 cases.",
    "whys": [
        {"question": "Why does this transition take so long?", "answer": "212.4 hours is far above the other transitions in this process.", "basis": "evidence"},
        {"question": "Why would payment release be delayed after matching?", "answer": "Payment runs may be batched on a fixed weekly schedule.", "basis": "assumption"},
        {"question": "Why would a weekly batch cause this specific delay?", "answer": "Invoices matched just after a batch cutoff wait nearly a full cycle.", "basis": "assumption"},
        {"question": "Why hasn't the batch schedule been adjusted?", "answer": "No data given addresses scheduling policy.", "basis": "assumption"},
        {"question": "Why does this matter operationally?", "answer": "7 cases is a small sample, so this may not generalize.", "basis": "assumption"},
    ],
    "root_cause_hypothesis": "This delay may be due to a fixed weekly payment batch schedule, not a process failure.",
    "recommended_next_step": "Ask the AP process owner about the payment run schedule and compare against these 7 case timestamps.",
}

_FAKE_FISHBONE = {
    "problem_statement": "Invoice Matched -> Payment Released averages 212.4 hours across 7 cases.",
    "categories": [
        {"name": "Process", "causes": [{"cause": "Fixed weekly payment batch schedule", "basis": "assumption"}]},
        {"name": "Technology", "causes": [{"cause": "Payment system integration runs on a delay", "basis": "assumption"}]},
    ],
    "root_cause_candidates": ["Fixed weekly payment batch schedule"],
}


def test_ai_disabled_returns_503_for_both_endpoints(client, monkeypatch):
    monkeypatch.setattr(ae.claude_client, "is_available", lambda: False)
    assert client.post("/ai/five-whys", json={"finding": _finding()}).status_code == 503
    assert client.post("/ai/fishbone", json={"finding": _finding()}).status_code == 503


def test_five_whys_requires_a_real_seed_finding_not_free_text(client):
    """No `problem` or free-text field is accepted at all — omitting
    `finding` (the only way to describe a problem) is a 422, not a 200 with
    an invented problem statement."""
    r = client.post("/ai/five-whys", json={"days": 30})
    assert r.status_code == 422


def test_five_whys_generates_and_attaches_pending_review(client, monkeypatch):
    captured = {}
    def _fake_complete_json(system, user, schema, *, label, **kw):
        captured.update(system=system, user=user, schema=schema, label=label)
        return _FAKE_WHYS
    monkeypatch.setattr(ae.claude_client, "complete_json", _fake_complete_json)

    r = client.post("/ai/five-whys", json={"finding": _finding(), "days": 30})

    assert r.status_code == 200
    body = r.json()
    assert body["root_cause_hypothesis"] == _FAKE_WHYS["root_cause_hypothesis"]
    assert len(body["whys"]) == 5
    assert body["_review"] == {"id": 99, "status": "pending", "reviewed_by_name": None, "reviewed_at": None}
    assert captured["label"] == "five_whys"
    # The seed's real numbers must reach the prompt — nothing invented.
    assert "Invoice Matched -> Payment Released" in captured["user"]
    assert "212.4" in captured["user"]
    assert "7" in captured["user"]
    # The schema itself enforces the evidence/assumption guardrail.
    why_item_props = captured["schema"]["properties"]["whys"]["items"]["properties"]
    assert why_item_props["basis"]["enum"] == ["evidence", "assumption"]


def test_fishbone_generates_and_attaches_pending_review(client, monkeypatch):
    monkeypatch.setattr(ae.claude_client, "complete_json", lambda *a, **kw: _FAKE_FISHBONE)
    r = client.post("/ai/fishbone", json={"finding": _finding(), "days": 30})

    assert r.status_code == 200
    body = r.json()
    assert body["root_cause_candidates"] == ["Fixed weekly payment batch schedule"]
    assert body["_review"]["status"] == "pending"


def test_fishbone_cause_schema_forces_evidence_or_assumption_label(client, monkeypatch):
    captured = {}
    def _fake_complete_json(system, user, schema, *, label, **kw):
        captured["schema"] = schema
        return _FAKE_FISHBONE
    monkeypatch.setattr(ae.claude_client, "complete_json", _fake_complete_json)

    client.post("/ai/fishbone", json={"finding": _finding()})

    cause_props = captured["schema"]["properties"]["categories"]["items"]["properties"]["causes"]["items"]["properties"]
    assert cause_props["basis"]["enum"] == ["evidence", "assumption"]
    category_names = captured["schema"]["properties"]["categories"]["items"]["properties"]["name"]["enum"]
    assert category_names == ["People", "Process", "Technology", "Data/Materials", "Environment", "Measurement"]


def test_cache_hit_skips_the_model_call_for_five_whys(client, monkeypatch):
    monkeypatch.setattr(ae.db, "get_cached_ai_analysis", lambda *a, **kw: {
        "id": 7, "content": _FAKE_WHYS, "review_status": "reviewed",
        "reviewed_by_name": "jdoe", "reviewed_at": "2026-08-15T00:00:00Z",
    })
    called = {"n": 0}
    monkeypatch.setattr(ae.claude_client, "complete_json", lambda *a, **kw: called.update(n=called["n"] + 1))

    r = client.post("/ai/five-whys", json={"finding": _finding()})

    assert called["n"] == 0
    body = r.json()
    assert body["root_cause_hypothesis"] == _FAKE_WHYS["root_cause_hypothesis"]
    assert body["_review"]["status"] == "reviewed"
    assert body["_review"]["reviewed_by_name"] == "jdoe"


def test_different_edges_do_not_share_a_cache_key(client, monkeypatch):
    captured_subject_refs = []
    def _fake_get_cached(kind, run_id, subject_ref, input_hash):
        captured_subject_refs.append(subject_ref)
        return None
    monkeypatch.setattr(ae.db, "get_cached_ai_analysis", _fake_get_cached)
    monkeypatch.setattr(ae.claude_client, "complete_json", lambda *a, **kw: _FAKE_WHYS)

    client.post("/ai/five-whys", json={"finding": _finding()})
    client.post("/ai/five-whys", json={"finding": _finding(source="Goods Received", target="Putaway Confirmed")})

    assert captured_subject_refs[0] != captured_subject_refs[1]


def test_five_whys_and_fishbone_use_separate_cache_namespaces(client, monkeypatch):
    """The same finding must not let a cached 5 Whys answer leak into a
    Fishbone request or vice versa — both call get_cached_ai_analysis with a
    distinct `kind` ('five_whys' vs 'fishbone')."""
    kinds_seen = []
    def _fake_get_cached(kind, run_id, subject_ref, input_hash):
        kinds_seen.append(kind)
        return None
    monkeypatch.setattr(ae.db, "get_cached_ai_analysis", _fake_get_cached)
    monkeypatch.setattr(ae.claude_client, "complete_json", lambda system, user, schema, **kw: _FAKE_FISHBONE if "categories" in schema["properties"] else _FAKE_WHYS)

    client.post("/ai/five-whys", json={"finding": _finding()})
    client.post("/ai/fishbone", json={"finding": _finding()})

    assert kinds_seen == ["five_whys", "fishbone"]
