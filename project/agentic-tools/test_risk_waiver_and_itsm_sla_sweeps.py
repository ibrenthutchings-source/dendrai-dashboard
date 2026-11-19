#!/usr/bin/env python3
"""
Integration tests for risk_waiver_sweep.py and itsm_sla_sweep.py — same
shape, and same test approach, as test_expiry_sweeps.py covers for
vendor_risk_sweep.py/ai_governance_sweep.py:

    db.expire_overdue_X()  ->  list of newly-lapsed rows
        for each row:  mcp_governance._detect_system_flags (pure)
                     -> mcp_governance._ingest_system_event (DB write)

Zero prior test coverage existed for either sweep — neither file existed at
all until this pass built them from db.py's already-complete
expire_overdue_waivers()/expire_overdue_sla() data layer. Only the DB write
boundary (mcp_governance._ingest_system_event) is mocked; _detect_system_flags
runs for real.

    pytest test_risk_waiver_and_itsm_sla_sweeps.py -v
"""

from __future__ import annotations

import asyncio

import db
import itsm_sla_sweep
import mcp_governance
import risk_waiver_sweep


def _recorder(monkeypatch, module=mcp_governance):
    calls = []

    def _fake_ingest(server_name, system_type, event_type, event_id, actor, action,
                      resource, severity, flags, raw_payload, source_ip):
        calls.append({
            "server_name": server_name, "system_type": system_type, "event_type": event_type,
            "event_id": event_id, "actor": actor, "action": action, "resource": resource,
            "severity": severity, "flags": flags, "raw_payload": raw_payload,
        })
        return len(calls)

    monkeypatch.setattr(module, "_ingest_system_event", _fake_ingest)
    return calls


# ── risk_waiver_sweep.py — time-boxed compensating-control expiry ───────────

def test_risk_waiver_sweep_no_expired_waivers_is_a_no_op(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_waivers", lambda: [])
    calls = _recorder(monkeypatch)
    assert asyncio.run(risk_waiver_sweep.sweep_once()) == 0
    assert calls == []


def test_risk_waiver_sweep_re_ingests_each_expired_waiver(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_waivers", lambda: [
        {"id": 1, "vulnerability_hash": "fp-abc123", "reason": "Vendor patch scheduled next sprint",
         "compensating_control": "WAF rule blocks the affected endpoint", "approved_by": "jane.manager",
         "expires_at": "2026-08-01"},
        {"id": 2, "vulnerability_hash": "fp-def456", "reason": "False positive under review",
         "compensating_control": None, "approved_by": "john.manager", "expires_at": "2026-08-05"},
    ])
    calls = _recorder(monkeypatch)

    n = asyncio.run(risk_waiver_sweep.sweep_once())

    assert n == 2
    assert len(calls) == 2
    first = next(c for c in calls if c["resource"] == "fp-abc123")
    assert first["severity"] == "HIGH"
    assert first["action"] == "waiver_expiry_check"
    assert first["actor"] == "jane.manager"
    assert first["event_type"] == "risk_waiver_expired"
    assert "risk_waiver_expired" in first["flags"]
    assert first["raw_payload"]["risk_waiver_detail"]["compensating_control"] == "WAF rule blocks the affected endpoint"
    assert first["event_id"] == "waiver-expired:1:2026-08-01"


def test_risk_waiver_sweep_one_failure_does_not_block_the_rest(monkeypatch):
    """sweep_once() wraps each _raise_expired call in its own try/except —
    one waiver's re-ingestion failing must not silently swallow the others."""
    monkeypatch.setattr(db, "expire_overdue_waivers", lambda: [
        {"id": 1, "vulnerability_hash": "fp-broken", "reason": "x", "compensating_control": None,
         "approved_by": "a", "expires_at": "2026-08-01"},
        {"id": 2, "vulnerability_hash": "fp-fine", "reason": "x", "compensating_control": None,
         "approved_by": "b", "expires_at": "2026-08-05"},
    ])
    calls = []

    def _flaky_ingest(server_name, system_type, event_type, event_id, *a, **kw):
        if event_id.startswith("waiver-expired:1"):
            raise RuntimeError("simulated DB hiccup")
        calls.append(event_id)
        return 1
    monkeypatch.setattr(mcp_governance, "_ingest_system_event", _flaky_ingest)

    n = asyncio.run(risk_waiver_sweep.sweep_once())

    assert n == 2  # count reflects rows the DB actually flipped, not ingestion success
    assert calls == ["waiver-expired:2:2026-08-05"]


# ── itsm_sla_sweep.py — SLA breach detection ────────────────────────────────

def test_itsm_sla_sweep_no_breached_tickets_is_a_no_op(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_sla", lambda: [])
    calls = _recorder(monkeypatch)
    assert asyncio.run(itsm_sla_sweep.sweep_once()) == 0
    assert calls == []


def test_itsm_sla_sweep_re_ingests_each_breached_ticket(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_sla", lambda: [
        {"id": 5, "finding_hash": "fp-abc123", "external_system": "jira",
         "external_ticket_key": "SEC-142", "severity": "CRITICAL", "sla_due_at": "2026-08-01T00:00:00"},
    ])
    calls = _recorder(monkeypatch)

    n = asyncio.run(itsm_sla_sweep.sweep_once())

    assert n == 1
    assert len(calls) == 1
    assert calls[0]["resource"] == "SEC-142"
    assert calls[0]["server_name"] == "itsm-sla-sweep"
    assert calls[0]["system_type"] == "jira"
    assert calls[0]["severity"] == "CRITICAL"
    assert calls[0]["event_type"] == "itsm_sla_breached"
    assert "itsm_sla_breached" in calls[0]["flags"]
    assert calls[0]["raw_payload"]["itsm_ticket_detail"]["finding_hash"] == "fp-abc123"
    assert calls[0]["event_id"] == "sla-breach:5:2026-08-01T00:00:00"


def test_itsm_sla_sweep_defaults_severity_when_missing(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_sla", lambda: [
        {"id": 6, "finding_hash": "fp-xyz", "external_system": "servicenow",
         "external_ticket_key": "INC0012345", "severity": None, "sla_due_at": "2026-08-02T00:00:00"},
    ])
    calls = _recorder(monkeypatch)

    asyncio.run(itsm_sla_sweep.sweep_once())

    assert calls[0]["severity"] == "MEDIUM"


def test_itsm_sla_sweep_one_failure_does_not_block_the_rest(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_sla", lambda: [
        {"id": 1, "finding_hash": "a", "external_system": "jira", "external_ticket_key": "SEC-1",
         "severity": "HIGH", "sla_due_at": "2026-08-01T00:00:00"},
        {"id": 2, "finding_hash": "b", "external_system": "jira", "external_ticket_key": "SEC-2",
         "severity": "HIGH", "sla_due_at": "2026-08-02T00:00:00"},
    ])
    calls = []

    def _flaky_ingest(server_name, system_type, event_type, event_id, *a, **kw):
        if event_id.startswith("sla-breach:1"):
            raise RuntimeError("simulated DB hiccup")
        calls.append(event_id)
        return 1
    monkeypatch.setattr(mcp_governance, "_ingest_system_event", _flaky_ingest)

    n = asyncio.run(itsm_sla_sweep.sweep_once())

    assert n == 2
    assert calls == ["sla-breach:2:2026-08-02T00:00:00"]
