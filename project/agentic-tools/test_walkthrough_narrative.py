#!/usr/bin/env python3
"""
Tests for process_mining_endpoints.py's walkthrough-narrative drafting:
_draft_walkthrough_narrative's parsing (pure, claude_client monkeypatched)
and the POST /process-mining/walkthrough-narrative endpoint end to end.

    pytest test_walkthrough_narrative.py -v
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_endpoints
import process_mining_endpoints as pme


# ── _draft_walkthrough_narrative (pure parsing) ──────────────────────────────

def test_draft_walkthrough_narrative_parses_all_four_sections(monkeypatch):
    llm_output = (
        "PROCESS DESCRIPTION: The controller described a monthly close process with three approval steps.\n"
        "KEY CONTROLS: Journal entry approval, account reconciliation, management review.\n"
        "SYSTEM EVIDENCE: Process mining shows 92% conformance to this description, but 8% of cases "
        "skip the reconciliation step entirely, contradicting the stated process.\n"
        "OPEN QUESTIONS: Who approves entries when the primary approver is on leave?\n"
    )
    monkeypatch.setattr(pme.claude_client, "complete_text_meta", lambda *a, **kw: (llm_output, "end_turn"))

    draft = pme._draft_walkthrough_narrative("Record to Report", "transcript text", {"conformance": {}})

    assert draft is not None
    assert "three approval steps" in draft["process_description"]
    assert "Journal entry approval" in draft["key_controls"]
    assert "contradicting the stated process" in draft["system_evidence"]
    assert "on leave" in draft["open_questions"]


def test_draft_walkthrough_narrative_returns_none_when_llm_call_fails(monkeypatch):
    def _raise(*a, **kw):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(pme.claude_client, "complete_text_meta", _raise)
    assert pme._draft_walkthrough_narrative("Record to Report", "transcript", {}) is None


def test_draft_walkthrough_narrative_returns_none_on_malformed_output(monkeypatch):
    monkeypatch.setattr(pme.claude_client, "complete_text_meta", lambda *a, **kw: ("not the expected format", "end_turn"))
    assert pme._draft_walkthrough_narrative("Record to Report", "transcript", {}) is None


def test_draft_walkthrough_narrative_returns_none_on_empty_section(monkeypatch):
    llm_output = "PROCESS DESCRIPTION: \nKEY CONTROLS: x\nSYSTEM EVIDENCE: y\nOPEN QUESTIONS: z\n"
    monkeypatch.setattr(pme.claude_client, "complete_text_meta", lambda *a, **kw: (llm_output, "end_turn"))
    assert pme._draft_walkthrough_narrative("Record to Report", "transcript", {}) is None


# ── POST /process-mining/walkthrough-narrative ───────────────────────────────

def _client(monkeypatch):
    app = FastAPI()
    app.include_router(pme.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(pme.db, "is_available", lambda: False)  # _load_events degrades to [] cleanly
    return TestClient(app)


def test_walkthrough_narrative_requires_transcript(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/process-mining/walkthrough-narrative", json={"process": "record_to_report", "transcript": "  "})
    assert r.status_code == 422


def test_walkthrough_narrative_404s_on_unknown_process(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/process-mining/walkthrough-narrative", json={
        "process": "not_a_real_process", "transcript": "some interview text",
    })
    assert r.status_code == 404


def test_walkthrough_narrative_502s_when_drafting_fails(monkeypatch):
    client = _client(monkeypatch)
    process_id = next(iter(pme.pm.PROCESS_TEMPLATES))
    monkeypatch.setattr(pme, "_draft_walkthrough_narrative", lambda *a, **kw: None)
    r = client.post("/process-mining/walkthrough-narrative", json={
        "process": process_id, "transcript": "some interview text",
    })
    assert r.status_code == 502


def test_walkthrough_narrative_success_includes_supporting_stats(monkeypatch):
    client = _client(monkeypatch)
    process_id = next(iter(pme.pm.PROCESS_TEMPLATES))
    fake_draft = {"process_description": "x", "key_controls": "y", "system_evidence": "z", "open_questions": "w"}
    monkeypatch.setattr(pme, "_draft_walkthrough_narrative", lambda *a, **kw: fake_draft)

    r = client.post("/process-mining/walkthrough-narrative", json={
        "process": process_id, "transcript": "some interview text", "days": 60,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["narrative"] == fake_draft
    assert body["process"] == process_id
    assert body["window_days"] == 60
    assert "conformance" in body["supporting_stats"]
