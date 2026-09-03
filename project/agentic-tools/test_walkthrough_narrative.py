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


# ── Case Flow / CaC / PaC grounding (supporting_stats) ───────────────────────

def test_supporting_stats_include_case_flow_and_cac_pac(monkeypatch):
    """Regression: the walkthrough draft used to ground only on variants/
    conformance/cycle_times/rework — case_flow (real transition volume,
    distinct from cycle_times' duration framing), controls_as_code (CaC),
    and policy_as_code (PaC assurance) must all reach the model too."""
    client = _client(monkeypatch)
    process_id = next(iter(pme.pm.PROCESS_TEMPLATES))
    fake_draft = {"process_description": "x", "key_controls": "y", "system_evidence": "z", "open_questions": "w"}
    monkeypatch.setattr(pme, "_draft_walkthrough_narrative", lambda *a, **kw: fake_draft)
    monkeypatch.setattr(pme.db, "list_controls", lambda process=None, source=None: [
        {"control_id": "C-1", "name": "Segregation of duties", "process": process, "source": "pac_rego"},
        {"control_id": "C-2", "name": "Manual review", "process": process, "source": "manual"},
    ])
    monkeypatch.setattr(pme.pac_assurance, "assurance_summary", lambda process=None, stale_days=30: {
        "controls": [{"control_id": "C-1"}], "unverified": [{"control_id": "C-1"}],
        "total": 1, "unverified_count": 1,
    })

    r = client.post("/process-mining/walkthrough-narrative", json={
        "process": process_id, "transcript": "some interview text",
    })
    assert r.status_code == 200
    stats = r.json()["supporting_stats"]
    assert "case_flow" in stats
    assert stats["controls_as_code"] == [
        {"control_id": "C-1", "name": "Segregation of duties", "source": "pac_rego"},
        {"control_id": "C-2", "name": "Manual review", "source": "manual"},
    ]
    assert stats["policy_as_code"] == {
        "enforced_count": 1, "unverified_count": 1, "unverified_control_ids": ["C-1"],
    }


def test_case_flow_sorted_by_transition_volume_not_duration(monkeypatch):
    client = _client(monkeypatch)
    process_id = next(iter(pme.pm.PROCESS_TEMPLATES))
    fake_draft = {"process_description": "x", "key_controls": "y", "system_evidence": "z", "open_questions": "w"}
    monkeypatch.setattr(pme, "_draft_walkthrough_narrative", lambda *a, **kw: fake_draft)
    monkeypatch.setattr(pme.pm, "cycle_time_stats", lambda cases, process=None: {
        "process": process,
        "edges": [
            {"source": "A", "target": "B", "count": 3, "avg_hours": 100.0, "median_hours": 100.0, "p90_hours": 100.0},
            {"source": "C", "target": "D", "count": 50, "avg_hours": 1.0, "median_hours": 1.0, "p90_hours": 1.0},
        ],
        "bottleneck": None, "case_duration": None,
    })
    monkeypatch.setattr(pme.db, "list_controls", lambda process=None, source=None: [])
    monkeypatch.setattr(pme.pac_assurance, "assurance_summary", lambda process=None, stale_days=30: {
        "controls": [], "unverified": [], "total": 0, "unverified_count": 0,
    })

    r = client.post("/process-mining/walkthrough-narrative", json={
        "process": process_id, "transcript": "some interview text",
    })
    case_flow = r.json()["supporting_stats"]["case_flow"]
    assert [e["source"] for e in case_flow] == ["C", "A"]  # highest count first, not lowest duration first


# ── GET /process-mining/walkthrough-narrative/history ────────────────────────

def test_history_404s_on_unknown_process(monkeypatch):
    client = _client(monkeypatch)
    r = client.get("/process-mining/walkthrough-narrative/history", params={"process": "not_a_real_process"})
    assert r.status_code == 404


def test_history_empty_when_db_unavailable(monkeypatch):
    client = _client(monkeypatch)  # db.is_available already False
    process_id = next(iter(pme.pm.PROCESS_TEMPLATES))
    r = client.get("/process-mining/walkthrough-narrative/history", params={"process": process_id})
    assert r.status_code == 200
    assert r.json() == {"tasks": [], "process": process_id}


def test_history_passes_through_db_result(monkeypatch):
    client = _client(monkeypatch)
    process_id = next(iter(pme.pm.PROCESS_TEMPLATES))
    monkeypatch.setattr(pme.db, "is_available", lambda: True)
    captured = {}
    def _fake(gate_type, item_ref):
        captured.update(gate_type=gate_type, item_ref=item_ref)
        return [{"id": 1, "status": "submitted"}]
    monkeypatch.setattr(pme.db, "get_approval_tasks_by_item_ref", _fake)

    r = client.get("/process-mining/walkthrough-narrative/history", params={"process": process_id})
    assert r.status_code == 200
    assert r.json()["tasks"] == [{"id": 1, "status": "submitted"}]
    assert captured == {"gate_type": "walkthrough_narrative", "item_ref": process_id}
