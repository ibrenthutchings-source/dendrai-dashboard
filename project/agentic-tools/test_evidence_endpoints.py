#!/usr/bin/env python3
"""
Unit tests for evidence_endpoints.py's SARIF-parsing pure functions
(_severity_for, _cwe_cve_for, _location_for, _build_record) and db.py's
evidence-signing helpers (_evidence_signing_key / sign_evidence_record).

    pytest test_evidence_endpoints.py -v
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_endpoints
import db
import evidence_endpoints as ee


def _req(**overrides) -> ee.EvidenceWebhookRequest:
    base = dict(repository="acme/infra", commit_sha="abc123", pipeline_run_id="run-1",
                source="github_actions", author=None, approver=None, sarif={})
    base.update(overrides)
    return ee.EvidenceWebhookRequest(**base)


# ── _severity_for ─────────────────────────────────────────────────────────────

def test_severity_for_prefers_numeric_security_severity_score():
    result = {"properties": {"security-severity": "9.5"}, "level": "warning"}
    assert ee._severity_for(None, result) == "CRITICAL"


def test_severity_for_score_bands():
    assert ee._severity_for(None, {"properties": {"security-severity": "9.0"}}) == "CRITICAL"
    assert ee._severity_for(None, {"properties": {"security-severity": "7.0"}}) == "HIGH"
    assert ee._severity_for(None, {"properties": {"security-severity": "4.0"}}) == "MEDIUM"
    assert ee._severity_for(None, {"properties": {"security-severity": "1.0"}}) == "LOW"


def test_severity_for_falls_back_to_sarif_level_when_no_score():
    assert ee._severity_for(None, {"level": "error"}) == "HIGH"
    assert ee._severity_for(None, {"level": "warning"}) == "MEDIUM"
    assert ee._severity_for(None, {"level": "note"}) == "LOW"
    assert ee._severity_for(None, {}) == "INFO"


def test_severity_for_falls_back_to_rule_default_configuration_level():
    rule = {"defaultConfiguration": {"level": "error"}}
    assert ee._severity_for(rule, {}) == "HIGH"


def test_severity_for_ignores_malformed_score():
    result = {"properties": {"security-severity": "not-a-number"}, "level": "note"}
    assert ee._severity_for(None, result) == "LOW"


# ── _cwe_cve_for ───────────────────────────────────────────────────────────────

def test_cwe_cve_for_extracts_both_from_tags():
    rule = {"properties": {"tags": ["security", "CWE-79", "CVE-2024-12345", "other-tag"]}}
    cwe, cve = ee._cwe_cve_for(rule)
    assert cwe == "CWE-79"
    assert cve == "CVE-2024-12345"


def test_cwe_cve_for_none_when_no_matching_tags():
    rule = {"properties": {"tags": ["security", "external/cwe/cwe-079"]}}
    cwe, cve = ee._cwe_cve_for(rule)
    assert cwe is None and cve is None


def test_cwe_cve_for_handles_missing_rule():
    assert ee._cwe_cve_for(None) == (None, None)


# ── _location_for ────────────────────────────────────────────────────────────

def test_location_for_extracts_file_line_and_snippet():
    result = {
        "locations": [{
            "physicalLocation": {
                "artifactLocation": {"uri": "src/app.py"},
                "region": {"startLine": 42, "snippet": {"text": "eval(user_input)"}},
            },
        }],
    }
    file_path, line_number, snippet = ee._location_for(result)
    assert file_path == "src/app.py"
    assert line_number == 42
    assert snippet == "eval(user_input)"


def test_location_for_no_locations_returns_all_none():
    assert ee._location_for({}) == (None, None, None)


# ── _build_record / fingerprint ──────────────────────────────────────────────

def test_build_record_fingerprint_is_stable_for_same_inputs():
    req = _req()
    r1 = ee._build_record(req, "py/sql-injection", "HIGH", "CWE-89", None, "src/db.py", 10, "query(sql)", "FAIL")
    r2 = ee._build_record(req, "py/sql-injection", "HIGH", "CWE-89", None, "src/db.py", 10, "query(sql)", "FAIL")
    assert r1["fingerprint"] == r2["fingerprint"]


def test_build_record_fingerprint_differs_for_different_findings():
    req = _req()
    r1 = ee._build_record(req, "rule-a", "HIGH", None, None, "a.py", 1, "x", "FAIL")
    r2 = ee._build_record(req, "rule-b", "HIGH", None, None, "a.py", 1, "x", "FAIL")
    assert r1["fingerprint"] != r2["fingerprint"]


def test_build_record_carries_request_fields():
    req = _req(repository="acme/infra", commit_sha="deadbeef")
    record = ee._build_record(req, "rule-a", "MEDIUM", None, None, None, None, None, "PASS")
    assert record["repository"] == "acme/infra"
    assert record["commit_sha"] == "deadbeef"
    assert record["scan_status"] == "PASS"


# ── db.sign_evidence_record / _evidence_signing_key ─────────────────────────

def test_evidence_signing_key_raises_when_unset(monkeypatch):
    monkeypatch.delenv("EVIDENCE_SIGNING_KEY", raising=False)
    with pytest.raises(db.EvidenceSigningKeyMissing):
        db._evidence_signing_key()


def test_sign_evidence_record_raises_when_key_unset(monkeypatch):
    monkeypatch.delenv("EVIDENCE_SIGNING_KEY", raising=False)
    with pytest.raises(db.EvidenceSigningKeyMissing):
        db.sign_evidence_record('{"a": 1}')


def test_sign_evidence_record_is_deterministic_and_verifiable(monkeypatch):
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY", "test-signing-key")
    record = {"repository": "acme/infra", "rule_id": "py/sql-injection", "severity": "HIGH"}
    record_json = json.dumps(record, sort_keys=True, default=str)

    sig1 = db.sign_evidence_record(record_json)
    sig2 = db.sign_evidence_record(record_json)
    assert sig1 == sig2

    # Re-serializing the same dict (as a GET .../verify handler would after
    # reading it back from JSONB) must reproduce the same signature.
    reserialized = json.dumps(dict(record), sort_keys=True, default=str)
    assert db.sign_evidence_record(reserialized) == sig1


def test_sign_evidence_record_changes_with_content(monkeypatch):
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY", "test-signing-key")
    sig_a = db.sign_evidence_record('{"severity": "HIGH"}')
    sig_b = db.sign_evidence_record('{"severity": "LOW"}')
    assert sig_a != sig_b


def test_sign_evidence_record_changes_with_key(monkeypatch):
    record_json = '{"severity": "HIGH"}'
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY", "key-one")
    sig1 = db.sign_evidence_record(record_json)
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY", "key-two")
    sig2 = db.sign_evidence_record(record_json)
    assert sig1 != sig2


# ── Endpoint-level tests (TestClient, same pattern as test_ai_governance_endpoints.py) ──

@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ee.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(ee.db, "is_available", lambda: True)
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY", "test-signing-key")
    return TestClient(app)


def _system(**over) -> dict:
    base = {"id": 1, "server_name": "github-actions-prod", "server_type": "github_actions", "active": True}
    base.update(over)
    return base


_SARIF_ONE_FINDING = {
    "runs": [{
        "tool": {"driver": {"name": "CodeQL", "rules": [
            {"id": "py/sql-injection", "properties": {"tags": ["CWE-89"], "security-severity": "9.8"}},
        ]}},
        "results": [{
            "ruleId": "py/sql-injection", "level": "error",
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": "src/db.py"},
                "region": {"startLine": 42, "snippet": {"text": "cur.execute(sql)"}},
            }}],
        }],
    }],
}

_SARIF_CLEAN_RUN = {"runs": [{"tool": {"driver": {"name": "CodeQL", "rules": []}}, "results": []}]}


def test_webhook_requires_bearer_auth(client):
    r = client.post("/evidence/webhook", json={"repository": "acme/infra", "sarif": _SARIF_CLEAN_RUN})
    assert r.status_code == 401


def test_webhook_rejects_invalid_api_key(client, monkeypatch):
    monkeypatch.setattr(ee.mcp_governance, "_get_system_by_api_key", lambda key: None)
    r = client.post(
        "/evidence/webhook", json={"repository": "acme/infra", "sarif": _SARIF_CLEAN_RUN},
        headers={"Authorization": "Bearer bad-key"},
    )
    assert r.status_code == 401


def test_webhook_503_when_signing_key_missing(client, monkeypatch):
    monkeypatch.delenv("EVIDENCE_SIGNING_KEY", raising=False)
    r = client.post(
        "/evidence/webhook", json={"repository": "acme/infra", "sarif": _SARIF_CLEAN_RUN},
        headers={"Authorization": "Bearer good-key"},
    )
    assert r.status_code == 503


def test_webhook_422_when_sarif_has_no_runs(client, monkeypatch):
    monkeypatch.setattr(ee.mcp_governance, "_get_system_by_api_key", lambda key: _system())
    r = client.post(
        "/evidence/webhook", json={"repository": "acme/infra", "sarif": {"runs": []}},
        headers={"Authorization": "Bearer good-key"},
    )
    assert r.status_code == 422


def test_webhook_inserts_one_pass_row_for_a_clean_run(client, monkeypatch):
    monkeypatch.setattr(ee.mcp_governance, "_get_system_by_api_key", lambda key: _system())
    inserted = []
    monkeypatch.setattr(ee.db, "insert_evidence_record", lambda **kw: inserted.append(kw) or 1)

    r = client.post(
        "/evidence/webhook", json={"repository": "acme/infra", "sarif": _SARIF_CLEAN_RUN},
        headers={"Authorization": "Bearer good-key"},
    )
    assert r.status_code == 200
    assert r.json() == {"received": True, "records_inserted": 1, "escalated_to_adjudication": 0}
    assert inserted[0]["scan_status"] == "PASS"
    assert inserted[0]["rule_id"] is None


def test_webhook_inserts_finding_and_escalates_high_severity(client, monkeypatch):
    monkeypatch.setattr(ee.mcp_governance, "_get_system_by_api_key", lambda key: _system())
    inserted = []
    monkeypatch.setattr(ee.db, "insert_evidence_record", lambda **kw: inserted.append(kw) or 1)
    escalated = []
    monkeypatch.setattr(ee.mcp_governance, "_detect_system_flags", lambda payload: [])
    monkeypatch.setattr(ee.mcp_governance, "_ingest_system_event", lambda *a, **kw: escalated.append(a) or 1)

    r = client.post(
        "/evidence/webhook", json={"repository": "acme/infra", "sarif": _SARIF_ONE_FINDING},
        headers={"Authorization": "Bearer good-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["records_inserted"] == 1
    assert body["escalated_to_adjudication"] == 1
    assert inserted[0]["rule_id"] == "py/sql-injection"
    assert inserted[0]["severity"] == "CRITICAL"  # security-severity 9.8
    assert inserted[0]["cwe"] == "CWE-89"
    assert inserted[0]["file_path"] == "src/db.py"
    assert len(escalated) == 1


def test_webhook_does_not_count_a_duplicate_finding_as_inserted(client, monkeypatch):
    """insert_evidence_record returns None on (fingerprint, commit_sha)
    conflict — a repeated scan of the same commit re-ingesting the same
    finding must not be double-counted or double-escalated."""
    monkeypatch.setattr(ee.mcp_governance, "_get_system_by_api_key", lambda key: _system())
    monkeypatch.setattr(ee.db, "insert_evidence_record", lambda **kw: None)
    escalate_calls = []
    monkeypatch.setattr(ee, "_escalate_finding", lambda *a, **kw: escalate_calls.append(1))

    r = client.post(
        "/evidence/webhook", json={"repository": "acme/infra", "sarif": _SARIF_ONE_FINDING},
        headers={"Authorization": "Bearer good-key"},
    )
    assert r.status_code == 200
    assert r.json() == {"received": True, "records_inserted": 0, "escalated_to_adjudication": 0}
    assert escalate_calls == []


# ── GET /evidence/records* ────────────────────────────────────────────────────

def test_list_records_returns_empty_when_db_unavailable(client, monkeypatch):
    monkeypatch.setattr(ee.db, "is_available", lambda: False)
    r = client.get("/evidence/records")
    assert r.status_code == 200
    assert r.json() == {"records": []}


def test_get_record_404_when_missing(client, monkeypatch):
    monkeypatch.setattr(ee.db, "get_evidence_record", lambda record_id: None)
    r = client.get("/evidence/records/999")
    assert r.status_code == 404


def test_verify_record_valid_signature(client, monkeypatch):
    record = {"repository": "acme/infra", "rule_id": "py/sql-injection", "severity": "HIGH"}
    record_json_str = json.dumps(record, sort_keys=True, default=str)
    signature = db.sign_evidence_record(record_json_str)
    monkeypatch.setattr(ee.db, "get_evidence_record", lambda record_id: {"record_json": record, "signature": signature})

    r = client.get("/evidence/records/1/verify")
    assert r.status_code == 200
    assert r.json() == {"id": 1, "valid": True}


def test_verify_record_detects_tampered_content(client, monkeypatch):
    record = {"repository": "acme/infra", "rule_id": "py/sql-injection", "severity": "HIGH"}
    record_json_str = json.dumps(record, sort_keys=True, default=str)
    signature = db.sign_evidence_record(record_json_str)
    tampered = dict(record, severity="LOW")  # signature no longer matches this content
    monkeypatch.setattr(ee.db, "get_evidence_record", lambda record_id: {"record_json": tampered, "signature": signature})

    r = client.get("/evidence/records/1/verify")
    assert r.status_code == 200
    assert r.json() == {"id": 1, "valid": False}
