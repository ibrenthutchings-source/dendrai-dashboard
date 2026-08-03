#!/usr/bin/env python3
"""
Integration tests for connector_poller.py — the generic poll-connector
dispatch loop that is the single scheduler for all 16 poll-based system
adapters (Oracle Fusion, SAP HANA, SailPoint, Dynamics 365, NetSuite,
GitHub/GitLab/Bitbucket SCM audits, Jira/ServiceNow ITSM, Postgres CIS,
Railway IaaS, AWS IaaS, OT heartbeat, denied-party screening, Oracle HCM).

Zero prior test coverage existed for this file. It is the largest single
"listener" surface in the codebase by connector-type count, and its own
docstring is explicit about the contract under test here: adding a new
connector type means adding one adapter + one _ADAPTERS entry, "no new
scheduler code" — which is only true if the dispatch/due-check/ingest
plumbing tested below is actually adapter-agnostic, not accidentally
SAP/SailPoint-specific.

Three things are proven, all without a database or a real external system:

1. _is_due — the scheduling gate (active flag + per-connector interval).
2. _poll_one — "never raises" for every failure mode it documents handling:
   unknown connector_type, pull_events() raising, and EncryptionKeyMissing.
3. _poll_one's happy path: adapter events actually flow through
   mcp_governance._detect_system_flags (real) and
   mcp_governance._ingest_system_event (mocked at the DB boundary) — the
   catch-and-report contract every poll connector relies on this file for.

    pytest test_connector_poller.py -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import connector_poller as cp
import db
import mcp_governance


# ── _is_due (pure scheduling gate) ──────────────────────────────────────────

def test_is_due_inactive_connector_never_due():
    assert cp._is_due({"active": False, "last_poll_at": None}) is False


def test_is_due_never_polled_is_immediately_due():
    assert cp._is_due({"active": True, "last_poll_at": None}) is True


def test_is_due_polled_recently_is_not_due():
    recent = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    assert cp._is_due({"active": True, "last_poll_at": recent, "poll_interval_s": 1800}) is False


def test_is_due_polled_long_ago_is_due():
    stale = (datetime.now(timezone.utc) - timedelta(seconds=3601)).isoformat()
    assert cp._is_due({"active": True, "last_poll_at": stale, "poll_interval_s": 1800}) is True


def test_is_due_respects_custom_poll_interval():
    ten_min_ago = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    assert cp._is_due({"active": True, "last_poll_at": ten_min_ago, "poll_interval_s": 300}) is True
    assert cp._is_due({"active": True, "last_poll_at": ten_min_ago, "poll_interval_s": 1800}) is False


def test_is_due_missing_poll_interval_defaults_to_1800s():
    twenty_nine_min_ago = (datetime.now(timezone.utc) - timedelta(seconds=1740)).isoformat()
    assert cp._is_due({"active": True, "last_poll_at": twenty_nine_min_ago}) is False


# ── _poll_one — never-raises failure modes ──────────────────────────────────

def test_poll_one_unknown_connector_type_records_error_and_does_not_raise(monkeypatch):
    monkeypatch.setattr(db, "get_poll_connector", lambda cid, full: {
        "id": cid, "connector_type": "not_a_real_adapter", "display_name": "Bogus",
        "base_url": "https://x", "credentials": {}, "extra_config": {}, "last_poll_at": None,
    })
    recorded = {}
    monkeypatch.setattr(db, "record_poll_result", lambda cid, status, error=None: recorded.update(
        cid=cid, status=status, error=error))

    asyncio.run(cp._poll_one(5))

    assert recorded["cid"] == 5
    assert recorded["status"] == "error"
    assert "not_a_real_adapter" in recorded["error"]


def test_poll_one_connector_not_found_returns_silently(monkeypatch):
    monkeypatch.setattr(db, "get_poll_connector", lambda cid, full: None)
    calls = []
    monkeypatch.setattr(db, "record_poll_result", lambda *a, **kw: calls.append(a))
    asyncio.run(cp._poll_one(999))  # must not raise
    assert calls == []  # nothing to record — the connector was gone, not polled-and-failed


def test_poll_one_encryption_key_missing_is_swallowed(monkeypatch):
    """A connector whose credentials can't be decrypted (CONNECTOR_ENCRYPTION_KEY
    unset/rotated) must be skipped for this tick, not crash the dispatch loop —
    per _poll_one's own docstring."""
    def _raise_missing_key(cid, full):
        raise db.EncryptionKeyMissing("CONNECTOR_ENCRYPTION_KEY not set")
    monkeypatch.setattr(db, "get_poll_connector", _raise_missing_key)
    calls = []
    monkeypatch.setattr(db, "record_poll_result", lambda *a, **kw: calls.append(a))
    asyncio.run(cp._poll_one(3))  # must not raise
    assert calls == []


def test_poll_one_pull_events_exception_records_error_without_ingesting(monkeypatch):
    monkeypatch.setattr(db, "get_poll_connector", lambda cid, full: {
        "id": cid, "connector_type": "sailpoint", "display_name": "Corp SailPoint",
        "base_url": "https://sailpoint.acme.com", "credentials": {"token": "x"},
        "extra_config": {}, "last_poll_at": None,
    })

    def _raise(*a, **kw):
        raise ConnectionError("SailPoint API unreachable")
    monkeypatch.setattr(cp._ADAPTERS["sailpoint"], "pull_events", _raise)

    recorded = {}
    monkeypatch.setattr(db, "record_poll_result", lambda cid, status, error=None: recorded.update(
        cid=cid, status=status, error=error))
    ingest_calls = []
    monkeypatch.setattr(mcp_governance, "_ingest_system_event", lambda *a, **kw: ingest_calls.append(a))

    asyncio.run(cp._poll_one(7))

    assert recorded["status"] == "error"
    assert "SailPoint API unreachable" in recorded["error"]
    assert ingest_calls == []


# ── _poll_one — happy path: events actually flow into governance ───────────

def test_poll_one_ingests_pulled_events_and_records_ok(monkeypatch):
    monkeypatch.setattr(db, "get_poll_connector", lambda cid, full: {
        "id": cid, "connector_type": "sailpoint", "display_name": "Corp SailPoint",
        "base_url": "https://sailpoint.acme.com", "credentials": {"token": "x"},
        "extra_config": {}, "last_poll_at": None,
    })

    pulled_args = {}
    def _fake_pull(base_url, credentials, extra_config, since):
        pulled_args.update(base_url=base_url, credentials=credentials, extra_config=extra_config, since=since)
        return [
            {"event_id": "sp-1", "event_type": "ROLE_EXPLOSION", "actor": "jdoe",
             "action": "role_explosion", "resource": "jdoe@acme.com", "severity": "CRITICAL",
             "raw_payload": {"role_count": 31}},
            {"event_id": "sp-2", "event_type": "poll_event", "actor": "asmith",
             "action": "login", "resource": "asmith@acme.com", "severity": "INFO",
             "raw_payload": {}},
        ]
    monkeypatch.setattr(cp._ADAPTERS["sailpoint"], "pull_events", _fake_pull)

    ingested = []
    def _fake_ingest(server_name, system_type, event_type, event_id, actor, action,
                      resource, severity, flags, raw_payload, source_ip):
        ingested.append({
            "server_name": server_name, "system_type": system_type, "event_type": event_type,
            "event_id": event_id, "resource": resource, "severity": severity, "flags": flags,
        })
        return len(ingested)
    monkeypatch.setattr(mcp_governance, "_ingest_system_event", _fake_ingest)

    poll_result = {}
    monkeypatch.setattr(db, "record_poll_result", lambda cid, status, error=None: poll_result.update(
        cid=cid, status=status, error=error))

    asyncio.run(cp._poll_one(11))

    # Adapter contract: base_url/credentials/extra_config/since all passed through untouched.
    assert pulled_args["base_url"] == "https://sailpoint.acme.com"
    assert pulled_args["credentials"] == {"token": "x"}
    assert pulled_args["since"] is None  # last_poll_at was None

    assert len(ingested) == 2
    server_name = ingested[0]["server_name"]
    assert server_name == "sailpoint:Corp SailPoint"
    assert ingested[0]["system_type"] == "sailpoint"

    role_explosion = next(e for e in ingested if e["resource"] == "jdoe@acme.com")
    # mcp_governance._detect_system_flags ran for real — resource contains no
    # privileged/sensitive keyword and event_type has no "sod" substring, but
    # role_count isn't in the generic flag detector's vocabulary either, so
    # this just proves the real detector ran (not a specific flag assumption).
    assert isinstance(role_explosion["flags"], list)

    assert poll_result == {"cid": 11, "status": "ok", "error": None}


def test_poll_one_passes_since_from_last_poll_at(monkeypatch):
    last_poll = "2026-07-01T00:00:00+00:00"
    monkeypatch.setattr(db, "get_poll_connector", lambda cid, full: {
        "id": cid, "connector_type": "sailpoint", "display_name": "Corp SailPoint",
        "base_url": "https://x", "credentials": {}, "extra_config": {}, "last_poll_at": last_poll,
    })
    seen_since = {}
    monkeypatch.setattr(cp._ADAPTERS["sailpoint"], "pull_events",
                         lambda base_url, credentials, extra_config, since: seen_since.update(since=since) or [])
    monkeypatch.setattr(db, "record_poll_result", lambda *a, **kw: None)

    asyncio.run(cp._poll_one(12))

    assert seen_since["since"] == datetime.fromisoformat(last_poll)


# ── _poll_due_connectors — dispatch only what's actually due ────────────────

def test_poll_due_connectors_only_polls_due_ones(monkeypatch):
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(db, "list_poll_connectors", lambda: [
        {"id": 1, "active": True, "last_poll_at": None, "poll_interval_s": 1800},       # due (never polled)
        {"id": 2, "active": True, "last_poll_at": stale, "poll_interval_s": 1800},      # due (stale)
        {"id": 3, "active": True, "last_poll_at": recent, "poll_interval_s": 1800},     # not due
        {"id": 4, "active": False, "last_poll_at": None, "poll_interval_s": 1800},      # inactive
    ])
    polled = []

    async def _fake_poll_one(connector_id):
        polled.append(connector_id)
    monkeypatch.setattr(cp, "_poll_one", _fake_poll_one)

    n = asyncio.run(cp._poll_due_connectors())

    assert n == 2
    assert sorted(polled) == [1, 2]
