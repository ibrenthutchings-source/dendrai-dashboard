#!/usr/bin/env python3
"""
Endpoint-level tests for itsm_endpoints.py — the ITSM webhook and ticket
CRUD that had a fully-built db.py data layer (create_itsm_ticket,
list_itsm_tickets, get_itsm_ticket, get_itsm_ticket_by_external_key,
update_itsm_ticket_status) with no endpoint ever calling any of it. Same
TestClient + dependency-override pattern as test_ai_governance_endpoints.py.

    pytest test_itsm_endpoints.py -v
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_endpoints
import itsm_endpoints as ie


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ie.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "role": "admin", "id": 1,
    }
    monkeypatch.setattr(ie.db, "is_available", lambda: True)
    return TestClient(app)


def _system(**over) -> dict:
    base = {"id": 1, "server_name": "jira-prod", "server_type": "jira", "active": True}
    base.update(over)
    return base


def _ticket(**over) -> dict:
    base = {
        "id": 5, "finding_hash": "abc123", "external_system": "jira", "external_ticket_key": "SEC-142",
        "status": "open", "severity": "HIGH",
    }
    base.update(over)
    return base


# ── POST /itsm/webhook ────────────────────────────────────────────────────────

def test_webhook_requires_bearer_auth(client):
    r = client.post("/itsm/webhook", json={"external_system": "jira", "external_ticket_key": "SEC-142", "status": "resolved"})
    assert r.status_code == 401


def test_webhook_rejects_invalid_api_key(client, monkeypatch):
    monkeypatch.setattr(ie.mcp_governance, "_get_system_by_api_key", lambda key: None)
    r = client.post(
        "/itsm/webhook",
        json={"external_system": "jira", "external_ticket_key": "SEC-142", "status": "resolved"},
        headers={"Authorization": "Bearer bad-key"},
    )
    assert r.status_code == 401


def test_webhook_rejects_unknown_status(client, monkeypatch):
    monkeypatch.setattr(ie.mcp_governance, "_get_system_by_api_key", lambda key: _system())
    r = client.post(
        "/itsm/webhook",
        json={"external_system": "jira", "external_ticket_key": "SEC-142", "status": "made_up_status"},
        headers={"Authorization": "Bearer good-key"},
    )
    assert r.status_code == 422


def test_webhook_rejects_unknown_external_system(client, monkeypatch):
    monkeypatch.setattr(ie.mcp_governance, "_get_system_by_api_key", lambda key: _system())
    r = client.post(
        "/itsm/webhook",
        json={"external_system": "not-a-real-itsm", "external_ticket_key": "SEC-142", "status": "resolved"},
        headers={"Authorization": "Bearer good-key"},
    )
    assert r.status_code == 422


def test_webhook_404s_when_no_tracked_ticket(client, monkeypatch):
    monkeypatch.setattr(ie.mcp_governance, "_get_system_by_api_key", lambda key: _system())
    monkeypatch.setattr(ie.db, "get_itsm_ticket_by_external_key", lambda system, key: None)
    r = client.post(
        "/itsm/webhook",
        json={"external_system": "jira", "external_ticket_key": "SEC-999", "status": "resolved"},
        headers={"Authorization": "Bearer good-key"},
    )
    assert r.status_code == 404


def test_webhook_updates_status_for_tracked_ticket(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(ie.mcp_governance, "_get_system_by_api_key", lambda key: _system())
    monkeypatch.setattr(ie.db, "get_itsm_ticket_by_external_key", lambda system, key: _ticket())

    def _fake_update(ticket_id, status):
        calls["update"] = (ticket_id, status)
        return True
    monkeypatch.setattr(ie.db, "update_itsm_ticket_status", _fake_update)

    r = client.post(
        "/itsm/webhook",
        json={"external_system": "JIRA", "external_ticket_key": "SEC-142", "status": "Resolved"},
        headers={"Authorization": "Bearer good-key"},
    )
    assert r.status_code == 200
    assert r.json() == {"received": True, "ticket_id": 5, "status": "resolved"}
    assert calls["update"] == (5, "resolved")


# ── GET /itsm/tickets ──────────────────────────────────────────────────────────

def test_list_tickets_returns_empty_list_when_db_unavailable(client, monkeypatch):
    monkeypatch.setattr(ie.db, "is_available", lambda: False)
    r = client.get("/itsm/tickets")
    assert r.status_code == 200
    assert r.json() == {"tickets": []}


def test_list_tickets_passes_filters_through(client, monkeypatch):
    captured = {}

    def _fake_list(status=None, external_system=None, breached_only=False, limit=100):
        captured.update(status=status, external_system=external_system, breached_only=breached_only, limit=limit)
        return [_ticket()]
    monkeypatch.setattr(ie.db, "list_itsm_tickets", _fake_list)

    r = client.get("/itsm/tickets?status=open&external_system=jira&breached_only=true&limit=10")
    assert r.status_code == 200
    assert captured == {"status": "open", "external_system": "jira", "breached_only": True, "limit": 10}


# ── GET /itsm/tickets/{id} ───────────────────────────────────────────────────

def test_get_ticket_404_when_missing(client, monkeypatch):
    monkeypatch.setattr(ie.db, "get_itsm_ticket", lambda ticket_id: None)
    r = client.get("/itsm/tickets/999")
    assert r.status_code == 404


def test_get_ticket_returns_ticket(client, monkeypatch):
    monkeypatch.setattr(ie.db, "get_itsm_ticket", lambda ticket_id: _ticket())
    r = client.get("/itsm/tickets/5")
    assert r.status_code == 200
    assert r.json()["external_ticket_key"] == "SEC-142"


# ── POST /itsm/tickets ────────────────────────────────────────────────────────

def test_create_ticket_rejects_bad_system(client):
    r = client.post("/itsm/tickets", json={
        "finding_hash": "abc123", "external_system": "not-real", "external_ticket_key": "X-1",
    })
    assert r.status_code == 422


def test_create_ticket_rejects_non_positive_sla_hours(client):
    r = client.post("/itsm/tickets", json={
        "finding_hash": "abc123", "external_system": "jira", "external_ticket_key": "X-1", "sla_hours": 0,
    })
    assert r.status_code == 422


def test_create_ticket_reuses_existing_open_ticket_for_same_finding(client, monkeypatch):
    monkeypatch.setattr(ie.db, "get_open_ticket_for_finding", lambda finding_hash: _ticket(id=42))
    r = client.post("/itsm/tickets", json={
        "finding_hash": "abc123", "external_system": "jira", "external_ticket_key": "SEC-142",
    })
    assert r.status_code == 200
    assert r.json() == {"id": 42, "reused_existing": True}


def test_create_ticket_creates_new_when_no_open_ticket_exists(client, monkeypatch):
    monkeypatch.setattr(ie.db, "get_open_ticket_for_finding", lambda finding_hash: None)
    captured = {}

    def _fake_create(**kw):
        captured.update(kw)
        return 77
    monkeypatch.setattr(ie.db, "create_itsm_ticket", _fake_create)

    r = client.post("/itsm/tickets", json={
        "finding_hash": "abc123", "external_system": "ServiceNow", "external_ticket_key": "INC0012345",
        "severity": "CRITICAL", "sla_hours": 24,
    })
    assert r.status_code == 200
    assert r.json() == {"id": 77, "reused_existing": False}
    assert captured["external_system"] == "servicenow"
    assert captured["sla_hours"] == 24
    assert captured["created_by"] == "tester"
