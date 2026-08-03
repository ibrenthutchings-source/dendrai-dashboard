#!/usr/bin/env python3
"""
Integration tests for the GitHub Webhook Listener (github_endpoints.py) —
the push-based inbound listener that catches real-time control-state events
from GitHub (secret scanning alerts, branch protection changes, force
pushes, ...) and reports them into the same UBO adjudication pipeline as
every other governed source.

No prior test coverage existed for this file at all: 0 hits for
"github_endpoints" across every test_*.py before this one.

Two things are proven here, both without a database:

1. _verify_signature — the HMAC-256 gate deciding whether an inbound POST is
   trusted. A listener that doesn't actually verify its signature isn't a
   security control, it's an open POST endpoint with a signature-shaped
   decoration on it.

2. The full listener is "active and functional" end-to-end: a real HTTP POST
   through FastAPI's TestClient, with a real HMAC signature, runs through the
   real Bronze -> Silver -> Gold -> Council pipeline (github_endpoints.py's
   own _get_pipeline(), not a stub) and returns a real adjudication verdict.
   Only the final DB write is skipped (db.is_available() is False without
   DATABASE_URL, same precondition test_pac_approval_drift.py documents) —
   the response returned to GitHub's webhook dashboard is computed before
   that write is even attempted, so this is exactly what GitHub itself would
   see on delivery.

    pytest test_github_webhook_listener.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import github_endpoints as gh
import db


# ── _verify_signature (pure HMAC check) ─────────────────────────────────────

def test_verify_signature_accepts_correct_hmac(monkeypatch):
    monkeypatch.setattr(gh, "WEBHOOK_SECRET", "s3cr3t")
    body = b'{"hello":"world"}'
    sig = "sha256=" + hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
    assert gh._verify_signature(body, sig) is True


def test_verify_signature_rejects_wrong_hmac(monkeypatch):
    monkeypatch.setattr(gh, "WEBHOOK_SECRET", "s3cr3t")
    body = b'{"hello":"world"}'
    sig = "sha256=" + hmac.new(b"WRONG-SECRET", body, hashlib.sha256).hexdigest()
    assert gh._verify_signature(body, sig) is False


def test_verify_signature_rejects_tampered_body(monkeypatch):
    """The signature must cover the exact bytes received — proving the
    listener would catch a payload altered in transit, not just a wrong key."""
    monkeypatch.setattr(gh, "WEBHOOK_SECRET", "s3cr3t")
    original = b'{"amount": 100}'
    sig = "sha256=" + hmac.new(b"s3cr3t", original, hashlib.sha256).hexdigest()
    tampered = b'{"amount": 100000}'
    assert gh._verify_signature(tampered, sig) is False


def test_verify_signature_rejects_missing_header(monkeypatch):
    monkeypatch.setattr(gh, "WEBHOOK_SECRET", "s3cr3t")
    assert gh._verify_signature(b"{}", None) is False


def test_verify_signature_rejects_header_without_sha256_prefix(monkeypatch):
    monkeypatch.setattr(gh, "WEBHOOK_SECRET", "s3cr3t")
    mac = hmac.new(b"s3cr3t", b"{}", hashlib.sha256).hexdigest()
    assert gh._verify_signature(b"{}", mac) is False  # missing "sha256=" prefix


def test_verify_signature_rejects_everything_when_secret_unconfigured(monkeypatch):
    """If GITHUB_WEBHOOK_SECRET was never set, the listener must fail closed
    (reject every delivery) rather than silently accept unsigned payloads —
    an unset secret is a misconfiguration, not an 'allow all' state."""
    monkeypatch.setattr(gh, "WEBHOOK_SECRET", "")
    body = b'{"hello":"world"}'
    sig = "sha256=" + hmac.new(b"anything", body, hashlib.sha256).hexdigest()
    assert gh._verify_signature(body, sig) is False


# ── End-to-end: real HTTP POST -> real HMAC check -> real UBO pipeline ─────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gh, "WEBHOOK_SECRET", "test-webhook-secret")
    assert not db.is_available()  # documents the precondition this suite relies on
    app = FastAPI()
    app.include_router(gh.router)
    return TestClient(app)


def _signed_post(client, payload: dict, event: str):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    return client.post(
        "/github/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": event,
            "Content-Type": "application/json",
        },
    )


def test_webhook_rejects_missing_signature(client):
    resp = client.post("/github/webhook", json={"repository": {"full_name": "acme/api"}})
    assert resp.status_code == 403


def test_webhook_rejects_forged_signature(client):
    body = json.dumps({"repository": {"full_name": "acme/api"}}).encode()
    resp = client.post(
        "/github/webhook", content=body,
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64, "X-GitHub-Event": "push"},
    )
    assert resp.status_code == 403


def test_webhook_rejects_invalid_json_even_with_valid_signature(client):
    body = b"not-json{{{"
    sig = "sha256=" + hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/github/webhook", content=body,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"},
    )
    assert resp.status_code == 400


def test_webhook_secret_scanning_alert_is_adjudicated_critical(client):
    """POL-GH-001 (policy.md): 'Any SECRET_DETECTED event is automatically
    CRITICAL... zero-tolerance.' Gold's base weight for SECRET_DETECTED is
    0.95 (UBO/pipeline/gold.py) — high enough alone to land CRITICAL even
    before the policy penalty. This proves the listener catches a real
    control-state signal (a leaked secret) and reports a real, correctly
    severe verdict — not a 200 OK that silently dropped the payload."""
    resp = _signed_post(
        client,
        {
            "repository": {"full_name": "acme/api", "id": 12345, "visibility": "private"},
            "sender": {"login": "octocat"},
            "alert": {"secret_type": "aws_access_key_id"},
        },
        event="secret_scanning_alert",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["received"] is True
    assert data["adjudicated"] is True
    assert data["risk_tier"] == "CRITICAL"
    assert data["verdict"] == "ESCALATE"
    assert data["requires_human_review"] is True
    assert any("zero-tolerance" in v.lower() or "secret" in v.lower()
               for v in data["policy_violations"]), data["policy_violations"]


def test_webhook_unrecognized_event_type_is_low_risk_and_auto_cleared(client):
    """Control case: an event type the listener doesn't recognize (bronze.py's
    GitHubBronzeHandler falls back to EventType.ANOMALY, base weight 0.35)
    must NOT be escalated by default — proving the listener doesn't just
    escalate everything, and that low-tier events correctly skip the full
    Council (fast-path clear, per council.md)."""
    resp = _signed_post(
        client,
        {"repository": {"full_name": "acme/api"}, "sender": {"login": "octocat"}},
        event="ping",  # not in GitHubBronzeHandler._ACTION_MAP
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["adjudicated"] is True
    assert data["risk_tier"] == "LOW"
    assert data["verdict"] == "CLEAR"
    assert data["requires_human_review"] is False


def test_webhook_response_identifies_the_correct_repo_and_event(client):
    resp = _signed_post(
        client,
        {"repository": {"full_name": "acme/payments-api"}, "sender": {"login": "bot[bot]"}},
        event="push",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo"] == "acme/payments-api"
    assert data["event"] == "push"
