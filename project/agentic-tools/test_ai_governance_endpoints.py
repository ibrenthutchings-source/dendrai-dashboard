#!/usr/bin/env python3
"""
Endpoint-level tests for ai_governance_endpoints.py.

test_behavioral_audit.py already covers the UBO.behavioral analyzers
thoroughly (TheOverseer, TheFairnessAuditor, run_behavioral_audit) at the
unit level. Nothing exercises the FastAPI handlers wrapped around them —
this file fills that gap: status codes, request validation, the
register-membership guard, the non-fatal best-effort finding-ingestion
pattern used in three different places, the idempotent finding item_ref,
and the AI-narrative review gate (same "every generation is pending until a
human clears it" pattern MODEL_CARD.md documents for persona_brief/
audit_report — see ai_governance_endpoints.py's own comment above
_REQUIRE_REVIEW_FOR_NARRATIVE).

    pytest test_ai_governance_endpoints.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ai_governance_endpoints as age
import auth_endpoints


@pytest.fixture()
def client(monkeypatch):
    """App with auth stubbed out (admin bypass, so require_screen_permission's
    per-endpoint closures all resolve without touching auth_db) and the DB
    reported as available. ai_governance_endpoints.router declares its
    permission check per-endpoint rather than at router level, so overriding
    the shared auth_endpoints.get_current_user leaf — every require_screen_permission
    closure depends on it — is what actually reaches every route, not just one."""
    app = FastAPI()
    app.include_router(age.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(age.db, "is_available", lambda: True)
    return TestClient(app)


def _system(**over) -> dict:
    base = {
        "system_id": 1, "system_name": "Vendor Scoring Model", "vendor": "Acme AI",
        "business_owner": "ops@example.com", "risk_tier": "HIGH",
        "requires_human_oversight": True, "human_oversight_defined": True,
    }
    base.update(over)
    return base


# ── GET /ai-governance — list ───────────────────────────────────────────────

def test_list_returns_503_when_db_unavailable(client, monkeypatch):
    monkeypatch.setattr(age.db, "is_available", lambda: False)
    r = client.get("/ai-governance")
    assert r.status_code == 503


def test_list_returns_systems_from_db(client, monkeypatch):
    monkeypatch.setattr(age.db, "list_ai_systems", lambda high_risk_only=False: [_system()])
    r = client.get("/ai-governance")
    assert r.status_code == 200
    assert r.json() == {"systems": [_system()]}


def test_list_forwards_high_risk_only_query_param(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(age.db, "list_ai_systems",
                         lambda high_risk_only=False: captured.setdefault("v", high_risk_only) and [])
    client.get("/ai-governance?high_risk_only=true")
    assert captured["v"] is True


# ── PUT /ai-governance — upsert ─────────────────────────────────────────────

_UPSERT_BODY = {
    "system_name": "Vendor Scoring Model", "vendor": "Acme AI",
    "risk_tier": "HIGH", "requires_human_oversight": False, "human_oversight_defined": False,
}


def test_upsert_returns_503_when_db_unavailable(client, monkeypatch):
    monkeypatch.setattr(age.db, "is_available", lambda: False)
    r = client.put("/ai-governance", json=_UPSERT_BODY)
    assert r.status_code == 503


def test_upsert_returns_500_when_save_fails(client, monkeypatch):
    monkeypatch.setattr(age.db, "upsert_ai_system", lambda **kw: None)
    r = client.put("/ai-governance", json=_UPSERT_BODY)
    assert r.status_code == 500


def test_upsert_happy_path_returns_id(client, monkeypatch):
    monkeypatch.setattr(age.db, "upsert_ai_system", lambda **kw: 42)
    r = client.put("/ai-governance", json=_UPSERT_BODY)
    assert r.status_code == 200
    assert r.json() == {"id": 42}


def test_upsert_raises_oversight_missing_finding_when_required_but_undefined(client, monkeypatch):
    monkeypatch.setattr(age.db, "upsert_ai_system", lambda **kw: 42)
    ingested = []
    monkeypatch.setattr(age.mcp_governance, "_detect_system_flags", lambda payload: ["flag"])
    monkeypatch.setattr(age.mcp_governance, "_ingest_system_event",
                         lambda *a, **k: ingested.append((a, k)))

    body = {**_UPSERT_BODY, "requires_human_oversight": True, "human_oversight_defined": False}
    r = client.put("/ai-governance", json=body)

    assert r.status_code == 200
    assert len(ingested) == 1
    args, _ = ingested[0]
    # positional args: (server_name, system_type, event_type, event_id, actor,
    # action, resource, severity, flags, raw_payload, source_ip)
    assert args[2] == "ai_human_oversight_missing"
    assert args[6] == "Vendor Scoring Model"  # resource


def test_upsert_does_not_raise_finding_when_oversight_is_defined(client, monkeypatch):
    monkeypatch.setattr(age.db, "upsert_ai_system", lambda **kw: 42)
    ingested = []
    monkeypatch.setattr(age.mcp_governance, "_ingest_system_event",
                         lambda *a, **k: ingested.append((a, k)))

    body = {**_UPSERT_BODY, "requires_human_oversight": True, "human_oversight_defined": True}
    r = client.put("/ai-governance", json=body)

    assert r.status_code == 200
    assert ingested == []


def test_upsert_does_not_raise_finding_when_oversight_not_required(client, monkeypatch):
    monkeypatch.setattr(age.db, "upsert_ai_system", lambda **kw: 42)
    ingested = []
    monkeypatch.setattr(age.mcp_governance, "_ingest_system_event",
                         lambda *a, **k: ingested.append((a, k)))

    body = {**_UPSERT_BODY, "requires_human_oversight": False, "human_oversight_defined": False}
    r = client.put("/ai-governance", json=body)

    assert r.status_code == 200
    assert ingested == []


def test_upsert_survives_ingestion_failure_and_still_returns_id(client, monkeypatch):
    """Best-effort: the auditor's save must succeed even if raising the
    finding fails — see the endpoint's own comment on this exact behavior."""
    monkeypatch.setattr(age.db, "upsert_ai_system", lambda **kw: 42)
    monkeypatch.setattr(age.mcp_governance, "_detect_system_flags", lambda payload: ["flag"])

    def _boom(*a, **k):
        raise RuntimeError("ingestion backend down")
    monkeypatch.setattr(age.mcp_governance, "_ingest_system_event", _boom)

    body = {**_UPSERT_BODY, "requires_human_oversight": True, "human_oversight_defined": False}
    r = client.put("/ai-governance", json=body)

    assert r.status_code == 200
    assert r.json() == {"id": 42}


# ── POST /ai-governance/behavioral-audit ────────────────────────────────────

_AUDIT_BODY = {
    "system_name": "Vendor Scoring Model",
    "events": [{"event_type": "human_review", "decision": "approved", "seconds_to_decide": 12.0}],
}


def _report(**over) -> dict:
    base = {
        "system_name": "Vendor Scoring Model", "events_examined": 1,
        "overall_verdict": "CLEAR", "requires_human_review": False,
        "evaluations": [{"agent_name": "The Overseer", "verdict": "CLEAR"}],
        "duration_ms": 3,
    }
    base.update(over)
    return base


def test_behavioral_audit_returns_503_when_analyzers_unavailable(client, monkeypatch):
    monkeypatch.setattr(age, "_HAS_BEHAVIORAL", False)
    r = client.post("/ai-governance/behavioral-audit", json=_AUDIT_BODY)
    assert r.status_code == 503


def test_behavioral_audit_returns_503_when_db_unavailable(client, monkeypatch):
    monkeypatch.setattr(age, "_HAS_BEHAVIORAL", True)
    monkeypatch.setattr(age.db, "is_available", lambda: False)
    r = client.post("/ai-governance/behavioral-audit", json=_AUDIT_BODY)
    assert r.status_code == 503


def test_behavioral_audit_rejects_empty_event_batch(client, monkeypatch):
    monkeypatch.setattr(age, "_HAS_BEHAVIORAL", True)
    r = client.post("/ai-governance/behavioral-audit",
                     json={"system_name": "Vendor Scoring Model", "events": []})
    assert r.status_code == 400
    assert "No events supplied" in r.json()["detail"]


def test_behavioral_audit_rejects_an_unregistered_system(client, monkeypatch):
    """The audit tests an attestation against evidence — there is nothing to
    test for a system nobody has put on the register."""
    monkeypatch.setattr(age, "_HAS_BEHAVIORAL", True)
    monkeypatch.setattr(age.db, "list_ai_systems", lambda: [_system(system_name="Some Other System")])
    r = client.post("/ai-governance/behavioral-audit", json=_AUDIT_BODY)
    assert r.status_code == 404
    assert "not on the AI system register" in r.json()["detail"]


def test_behavioral_audit_happy_path_returns_the_report_verbatim(client, monkeypatch):
    monkeypatch.setattr(age, "_HAS_BEHAVIORAL", True)
    monkeypatch.setattr(age.db, "list_ai_systems", lambda: [_system()])
    report = _report()
    monkeypatch.setattr(age, "run_behavioral_audit", lambda name, events: report)
    r = client.post("/ai-governance/behavioral-audit", json=_AUDIT_BODY)
    assert r.status_code == 200
    assert r.json() == report


def test_behavioral_audit_does_not_ingest_a_finding_when_review_not_required(client, monkeypatch):
    monkeypatch.setattr(age, "_HAS_BEHAVIORAL", True)
    monkeypatch.setattr(age.db, "list_ai_systems", lambda: [_system()])
    monkeypatch.setattr(age, "run_behavioral_audit", lambda name, events: _report(requires_human_review=False))
    ingested = []
    monkeypatch.setattr(age.mcp_governance, "_ingest_system_event", lambda *a, **k: ingested.append(a))

    r = client.post("/ai-governance/behavioral-audit", json=_AUDIT_BODY)
    assert r.status_code == 200
    assert ingested == []


@pytest.mark.parametrize("verdict,expected_severity", [
    ("ESCALATE", "HIGH"),
    ("MONITOR", "MEDIUM"),
    ("INSUFFICIENT_DATA", "LOW"),
])
def test_behavioral_audit_ingests_a_finding_with_verdict_mapped_severity(client, monkeypatch, verdict, expected_severity):
    monkeypatch.setattr(age, "_HAS_BEHAVIORAL", True)
    monkeypatch.setattr(age.db, "list_ai_systems", lambda: [_system()])
    monkeypatch.setattr(age, "run_behavioral_audit",
                         lambda name, events: _report(requires_human_review=True, overall_verdict=verdict))
    monkeypatch.setattr(age.mcp_governance, "_detect_system_flags", lambda payload: [])
    ingested = []
    monkeypatch.setattr(age.mcp_governance, "_ingest_system_event", lambda *a, **k: ingested.append(a))

    r = client.post("/ai-governance/behavioral-audit", json=_AUDIT_BODY)
    assert r.status_code == 200
    assert len(ingested) == 1
    assert ingested[0][7] == expected_severity  # severity positional arg


def test_behavioral_audit_finding_ref_is_stable_for_identical_evaluations(client, monkeypatch):
    """Re-running an unchanged batch must be recognisably the same finding,
    not a fresh one each time — the endpoint hashes `evaluations` into the
    item_ref specifically so a duplicate submission doesn't spam new findings."""
    monkeypatch.setattr(age, "_HAS_BEHAVIORAL", True)
    monkeypatch.setattr(age.db, "list_ai_systems", lambda: [_system()])
    monkeypatch.setattr(age.mcp_governance, "_detect_system_flags", lambda payload: [])
    ingested = []
    monkeypatch.setattr(age.mcp_governance, "_ingest_system_event", lambda *a, **k: ingested.append(a))

    same_evals = [{"agent_name": "The Overseer", "verdict": "ESCALATE"}]
    monkeypatch.setattr(age, "run_behavioral_audit",
                         lambda name, events: _report(requires_human_review=True, overall_verdict="ESCALATE", evaluations=same_evals))

    client.post("/ai-governance/behavioral-audit", json=_AUDIT_BODY)
    client.post("/ai-governance/behavioral-audit", json=_AUDIT_BODY)

    assert len(ingested) == 2
    ref_1, ref_2 = ingested[0][3], ingested[1][3]  # item_ref positional arg
    assert ref_1 == ref_2

    different_evals = [{"agent_name": "The Overseer", "verdict": "MONITOR"}]
    monkeypatch.setattr(age, "run_behavioral_audit",
                         lambda name, events: _report(requires_human_review=True, overall_verdict="MONITOR", evaluations=different_evals))
    client.post("/ai-governance/behavioral-audit", json=_AUDIT_BODY)
    assert ingested[2][3] != ref_1


def test_behavioral_audit_survives_ingestion_failure_and_still_returns_report(client, monkeypatch):
    monkeypatch.setattr(age, "_HAS_BEHAVIORAL", True)
    monkeypatch.setattr(age.db, "list_ai_systems", lambda: [_system()])
    report = _report(requires_human_review=True, overall_verdict="ESCALATE")
    monkeypatch.setattr(age, "run_behavioral_audit", lambda name, events: report)
    monkeypatch.setattr(age.mcp_governance, "_detect_system_flags", lambda payload: [])

    def _boom(*a, **k):
        raise RuntimeError("ingestion backend down")
    monkeypatch.setattr(age.mcp_governance, "_ingest_system_event", _boom)

    r = client.post("/ai-governance/behavioral-audit", json=_AUDIT_BODY)
    assert r.status_code == 200
    assert r.json() == report


# ── POST /ai-governance/behavioral-audit/narrative ──────────────────────────

_NARRATIVE_BODY = {
    "system_name": "Vendor Scoring Model",
    "report": {
        "overall_verdict": "ESCALATE", "events_examined": 10,
        "evaluations": [{"agent_name": "The Overseer", "verdict": "ESCALATE"}],
    },
}

_NARRATIVE_RESULT = {
    "headline": "Oversight review times are implausibly fast",
    "summary": "...", "control_reliance_impact": "...", "recommended_actions": ["..."],
}


def test_narrative_rejects_a_report_with_no_evaluations(client):
    body = {"system_name": "Vendor Scoring Model", "report": {"evaluations": []}}
    r = client.post("/ai-governance/behavioral-audit/narrative", json=body)
    assert r.status_code == 400
    assert "no evaluations" in r.json()["detail"]


def test_narrative_returns_502_when_generation_fails(client, monkeypatch):
    monkeypatch.setattr(age.db, "get_cached_ai_analysis", lambda *a, **k: None)

    def _boom(*a, **k):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(age.claude_client, "complete_json", _boom)

    r = client.post("/ai-governance/behavioral-audit/narrative", json=_NARRATIVE_BODY)
    assert r.status_code == 502


def test_narrative_fresh_generation_is_pending_review_and_is_saved_with_mandatory_review(client, monkeypatch):
    """Same gate MODEL_CARD.md documents for persona_brief/audit_report:
    every narrative generation is flagged for review, not sampled."""
    monkeypatch.setattr(age.db, "get_cached_ai_analysis", lambda *a, **k: None)
    monkeypatch.setattr(age.claude_client, "complete_json", lambda *a, **k: _NARRATIVE_RESULT)
    saved = {}
    monkeypatch.setattr(age.db, "save_ai_analysis",
                         lambda kind, content, **kw: saved.update(kind=kind, content=content, **kw) or 99)

    r = client.post("/ai-governance/behavioral-audit/narrative", json=_NARRATIVE_BODY)

    assert r.status_code == 200
    body = r.json()
    assert body["headline"] == _NARRATIVE_RESULT["headline"]
    assert body["_review"] == {"id": 99, "status": "pending", "reviewed_by_name": None, "reviewed_at": None}
    assert saved["sampled_for_review"] is True
    assert saved["kind"] == "ai_behavioral_narrative"


def test_narrative_cache_hit_reflects_actual_review_status_not_a_hardcoded_pending(client, monkeypatch):
    """A stale "always pending" response on a cache hit would misrepresent a
    narrative a reviewer already cleared — this pins the exact bug this
    pattern was written to avoid (see MODEL_CARD.md / ai_endpoints.py)."""
    cached = {
        "id": 7, "content": _NARRATIVE_RESULT, "review_status": "reviewed",
        "reviewed_by_name": "Dana", "reviewed_at": "2026-08-01T00:00:00",
    }
    monkeypatch.setattr(age.db, "get_cached_ai_analysis", lambda *a, **k: cached)

    r = client.post("/ai-governance/behavioral-audit/narrative", json=_NARRATIVE_BODY)

    assert r.status_code == 200
    body = r.json()
    assert body["headline"] == _NARRATIVE_RESULT["headline"]
    assert body["_review"] == {
        "id": 7, "status": "reviewed", "reviewed_by_name": "Dana", "reviewed_at": "2026-08-01T00:00:00",
    }
