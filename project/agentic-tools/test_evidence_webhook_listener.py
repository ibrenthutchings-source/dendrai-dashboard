#!/usr/bin/env python3
"""
Integration tests for the Evidence/SARIF Webhook Listener
(evidence_endpoints.py POST /evidence/webhook) — the second push-based
inbound listener, alongside the GitHub Webhook Listener
(test_github_webhook_listener.py). CI/SAST tooling (CodeQL, Trivy, Semgrep,
...) POSTs SARIF here; this is the entry point behind DevOps Monitoring's
"real gitleaks secret scanning" and "SARIF evidence" claims in README.md.

No prior test coverage existed for this endpoint's wiring — parse_sarif /
compute_fingerprint / sign_record (the pure functions it calls) are already
covered by test_devops_monitoring.py and test_trivy_sarif.py, but nothing
exercised the listener itself: auth, the fail-closed-without-a-database
behavior, or whether a HIGH/CRITICAL finding actually gets re-reported into
the governance pipeline (mcp_governance._ingest_system_event) versus just
being logged into the evidence table and going nowhere.

Unlike the GitHub webhook listener, this one hard-fails (503) with no
database rather than degrading — it can't check the ingest API key without
one, so "active and functional" here specifically includes "correctly
refuses to run open-loop when its dependency is down."

    pytest test_evidence_webhook_listener.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
import evidence_endpoints
import mcp_governance
from test_devops_monitoring import _SARIF_FIXTURE  # 2 findings: CRITICAL sql-injection, MEDIUM weak-crypto


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(evidence_endpoints.router)
    return TestClient(app)


def _post(client, sarif=_SARIF_FIXTURE, *, token="valid-key", repository="acme/api", **extra):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    body = {"repository": repository, "sarif": sarif, **extra}
    return client.post("/evidence/webhook", json=body, headers=headers)


# ── Auth gate ────────────────────────────────────────────────────────────────

def test_webhook_rejects_missing_authorization_header(client):
    resp = _post(client, token=None)
    assert resp.status_code == 401
    assert "Bearer" in resp.json()["detail"]


def test_webhook_rejects_non_bearer_authorization_header(client):
    resp = client.post(
        "/evidence/webhook",
        json={"repository": "acme/api", "sarif": _SARIF_FIXTURE},
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert resp.status_code == 401


def test_webhook_fails_closed_when_database_unavailable(client):
    """assert not db.is_available() documents the precondition (same as
    test_pac_approval_drift.py) — without a database there's no ingest-key
    table to check against, so the listener must refuse rather than silently
    accept an unverifiable caller."""
    assert not db.is_available()
    resp = _post(client)
    assert resp.status_code == 503


# ── With a mocked database boundary — same fake-DB-boundary pattern as ────
# ── test_mcp_governance_adjudication.py, applied to the higher-level         ──
# ── db.*/mcp_governance.* functions this endpoint calls directly rather      ──
# ── than raw SQL.                                                            ──

@pytest.fixture
def db_available(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)


def test_webhook_rejects_unknown_api_key(client, db_available, monkeypatch):
    monkeypatch.setattr(mcp_governance, "_get_system_by_api_key", lambda key: None)
    resp = _post(client, token="not-a-real-key")
    assert resp.status_code == 401
    assert "Invalid ingest API key" in resp.json()["detail"]


def test_webhook_ingests_findings_and_escalates_high_and_critical_only(client, db_available, monkeypatch):
    """The core 'catch and report' claim: parse_sarif finds one CRITICAL
    (sql-injection) and one MEDIUM (weak-crypto) finding in the fixture.
    Both get persisted as evidence records, but only the CRITICAL one is
    re-reported into the governance pipeline via
    mcp_governance._ingest_system_event — MEDIUM findings are evidence but
    not an active escalation, matching evidence_endpoints.py's
    `if f["severity"] in ("HIGH", "CRITICAL")` gate exactly."""
    monkeypatch.setattr(mcp_governance, "_get_system_by_api_key",
                         lambda key: {"server_name": "codeql-ci", "server_type": "sast"})
    monkeypatch.setattr(db, "insert_evidence_record", lambda *a, **kw: hash(a[4]) & 0xFFFF)  # a[4] = rule_id

    escalations = []

    def _fake_ingest(server_name, system_type, event_type, event_id, actor, action,
                      resource, severity, flags, raw_payload, source_ip):
        escalations.append({
            "server_name": server_name, "system_type": system_type, "event_type": event_type,
            "severity": severity, "resource": resource, "flags": flags,
        })
        return 1

    monkeypatch.setattr(mcp_governance, "_ingest_system_event", _fake_ingest)

    resp = _post(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["findings_count"] == 2
    assert data["ingested_count"] == 2
    assert data["skipped_duplicate_count"] == 0
    assert data["escalated_count"] == 1  # only the CRITICAL one

    assert len(escalations) == 1
    esc = escalations[0]
    assert esc["severity"] == "CRITICAL"
    assert esc["event_type"] == "sast_finding"
    assert esc["server_name"] == "codeql-ci"
    assert esc["resource"] == "app/db.py"
    assert "sast_finding" in esc["flags"]


def test_webhook_skips_duplicate_findings_without_re_escalating(client, db_available, monkeypatch):
    """db.insert_evidence_record returning None means the row is a duplicate
    (evidence_endpoints.py: 'if record_id is None: skipped_duplicate += 1;
    continue') — a re-delivered/re-scanned identical finding must not count
    as a second ingestion or fire a second escalation event."""
    monkeypatch.setattr(mcp_governance, "_get_system_by_api_key",
                         lambda key: {"server_name": "codeql-ci", "server_type": "sast"})
    monkeypatch.setattr(db, "insert_evidence_record", lambda *a, **kw: None)

    calls = {"n": 0}
    def _counting_ingest(*a, **kw):
        calls["n"] += 1
        return 1
    monkeypatch.setattr(mcp_governance, "_ingest_system_event", _counting_ingest)

    resp = _post(client)
    data = resp.json()
    assert data["ingested_count"] == 0
    assert data["skipped_duplicate_count"] == 2
    assert data["escalated_count"] == 0
    assert calls["n"] == 0


def test_webhook_empty_sarif_is_a_clean_no_op(client, db_available, monkeypatch):
    monkeypatch.setattr(mcp_governance, "_get_system_by_api_key",
                         lambda key: {"server_name": "codeql-ci", "server_type": "sast"})
    resp = _post(client, sarif={"runs": []})
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "received": True, "findings_count": 0, "ingested_count": 0,
        "skipped_duplicate_count": 0, "escalated_count": 0,
    }
