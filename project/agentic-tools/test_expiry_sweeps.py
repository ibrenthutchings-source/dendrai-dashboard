#!/usr/bin/env python3
"""
Integration tests for the four periodic expiry-sweep "listeners" —
vendor_risk_sweep.py, ai_governance_sweep.py, risk_waiver_sweep.py, and
itsm_sla_sweep.py. Zero prior test coverage existed for vendor_risk_sweep.py
or ai_governance_sweep.py at all; risk_waiver_sweep.py and itsm_sla_sweep.py
were only touched incidentally (SLA-hour/status helpers) by
test_itsm_bridge.py, never their actual sweep_once() re-escalation logic.

All four modules share one shape, by design (each docstring says so
explicitly — "mirrors risk_waiver_sweep.py's shape exactly"):

    db.expire_overdue_X()  ->  list of newly-lapsed rows
        for each row:  mcp_governance._detect_system_flags (pure)
                     -> mcp_governance._ingest_system_event (DB write)

This is the "catch" (a control-reliance basis lapsed — a SOC 2 report
expired, an AI assessment went overdue, a risk waiver's grace period ended,
an ITSM ticket blew its SLA) and the "report" (re-ingest as a fresh,
adjudicatable system_telemetry event, so the finding reappears in
Continuous Monitoring / the HITL inbox as failing again, not silently).

Only the DB write boundary (mcp_governance._ingest_system_event) is mocked;
_detect_system_flags runs for real, so the risk_flags asserted below are
whatever the real detector actually produces from the real payload each
sweep constructs — not an assumption about what it should produce.

    pytest test_expiry_sweeps.py -v
"""

from __future__ import annotations

import asyncio

import db
import mcp_governance
import vendor_risk_sweep
import ai_governance_sweep
import risk_waiver_sweep
import itsm_sla_sweep


def _recorder(monkeypatch):
    calls = []

    def _fake_ingest(server_name, system_type, event_type, event_id, actor, action,
                      resource, severity, flags, raw_payload, source_ip):
        calls.append({
            "server_name": server_name, "system_type": system_type, "event_type": event_type,
            "event_id": event_id, "actor": actor, "action": action, "resource": resource,
            "severity": severity, "flags": flags, "raw_payload": raw_payload,
        })
        return len(calls)

    monkeypatch.setattr(mcp_governance, "_ingest_system_event", _fake_ingest)
    return calls


# ── vendor_risk_sweep.py — SOC 2 expiry ─────────────────────────────────────

def test_vendor_risk_sweep_no_expired_vendors_is_a_no_op(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_vendor_soc2", lambda: [])
    calls = _recorder(monkeypatch)
    assert asyncio.run(vendor_risk_sweep.sweep_once()) == 0
    assert calls == []


def test_vendor_risk_sweep_re_ingests_each_expired_vendor(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_vendor_soc2", lambda: [
        {"id": 1, "vendor_name": "Acme Payments Ltd", "vendor_id": "V-4471",
         "critical": True, "soc2_expires_at": "2026-07-01"},
        {"id": 2, "vendor_name": "Small Supplier Co", "vendor_id": "V-9001",
         "critical": False, "soc2_expires_at": "2026-07-15"},
    ])
    calls = _recorder(monkeypatch)

    n = asyncio.run(vendor_risk_sweep.sweep_once())

    assert n == 2
    assert len(calls) == 2
    critical_call = next(c for c in calls if c["resource"] == "Acme Payments Ltd")
    assert critical_call["severity"] == "HIGH"
    assert critical_call["action"] == "soc2_expiry_check"
    assert "vendor_soc2_expired" in critical_call["flags"]
    assert critical_call["raw_payload"]["vendor_risk_detail"]["critical"] is True
    assert critical_call["event_id"] == "soc2-expired:1:2026-07-01"


def test_vendor_risk_sweep_one_failure_does_not_block_the_rest(monkeypatch):
    """sweep_once() wraps each _raise_expired call in its own try/except —
    one vendor's re-ingestion failing must not silently swallow the others."""
    monkeypatch.setattr(db, "expire_overdue_vendor_soc2", lambda: [
        {"id": 1, "vendor_name": "Broken Vendor", "vendor_id": "V-1", "critical": False, "soc2_expires_at": "x"},
        {"id": 2, "vendor_name": "Fine Vendor", "vendor_id": "V-2", "critical": False, "soc2_expires_at": "2026-07-15"},
    ])
    calls = []
    def _flaky_ingest(server_name, system_type, event_type, event_id, *a, **kw):
        if event_id.startswith("soc2-expired:1"):
            raise RuntimeError("simulated DB hiccup")
        calls.append(event_id)
        return 1
    monkeypatch.setattr(mcp_governance, "_ingest_system_event", _flaky_ingest)

    n = asyncio.run(vendor_risk_sweep.sweep_once())

    assert n == 2  # count reflects rows the DB actually flipped, not ingestion success
    assert calls == ["soc2-expired:2:2026-07-15"]  # the second vendor still got reported


# ── ai_governance_sweep.py — AI-05 assessment expiry ────────────────────────

def test_ai_governance_sweep_no_expired_assessments_is_a_no_op(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_ai_assessments", lambda: [])
    calls = _recorder(monkeypatch)
    assert asyncio.run(ai_governance_sweep.sweep_once()) == 0
    assert calls == []


def test_ai_governance_sweep_re_ingests_each_expired_assessment(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_ai_assessments", lambda: [
        {"id": 7, "system_name": "Vendor Support Chatbot", "vendor": "Acme AI Inc",
         "risk_tier": "HIGH", "assessment_expires_at": "2026-06-01"},
    ])
    calls = _recorder(monkeypatch)

    n = asyncio.run(ai_governance_sweep.sweep_once())

    assert n == 1
    assert len(calls) == 1
    assert calls[0]["resource"] == "Vendor Support Chatbot"
    assert calls[0]["severity"] == "HIGH"
    assert calls[0]["event_type"] == "ai_assessment_overdue"
    assert "ai_assessment_overdue" in calls[0]["flags"]
    assert calls[0]["raw_payload"]["ai_governance_detail"]["vendor"] == "Acme AI Inc"


# ── risk_waiver_sweep.py — waiver expiry re-opens the finding as failing ────

def test_risk_waiver_sweep_no_expired_waivers_is_a_no_op(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_waivers", lambda: [])
    calls = _recorder(monkeypatch)
    assert asyncio.run(risk_waiver_sweep.sweep_once()) == 0
    assert calls == []


def test_risk_waiver_sweep_reescalates_each_expired_waiver_as_sast_finding(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_waivers", lambda: [
        {"id": 42, "vulnerability_hash": "abc123def456", "reason": "compensating control in place",
         "compensating_control": "WAF rule blocking the exploit path",
         "approved_by": "jane.doe@acme.com", "expires_at": "2026-07-01"},
    ])
    calls = _recorder(monkeypatch)

    n = asyncio.run(risk_waiver_sweep.sweep_once())

    assert n == 1
    assert calls[0]["event_type"] == "sast_finding"
    assert calls[0]["severity"] == "HIGH"
    assert calls[0]["resource"] == "abc123def456"
    assert "sast_finding" in calls[0]["flags"]
    assert calls[0]["raw_payload"]["waiver_id"] == 42
    assert "re-opened as failing" in calls[0]["raw_payload"]["note"]


# ── itsm_sla_sweep.py — ITSM ticket SLA breach re-opens the finding ────────

def test_itsm_sla_sweep_no_breaches_is_a_no_op(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_sla", lambda: [])
    calls = _recorder(monkeypatch)
    assert asyncio.run(itsm_sla_sweep.sweep_once()) == 0
    assert calls == []


def test_itsm_sla_sweep_reescalates_each_breached_ticket(monkeypatch):
    monkeypatch.setattr(db, "expire_overdue_sla", lambda: [
        {"id": 9, "external_system": "jira", "external_ticket_key": "SEC-4471",
         "finding_hash": "fedcba987654", "severity": "CRITICAL", "sla_due_at": "2026-07-20"},
    ])
    calls = _recorder(monkeypatch)

    n = asyncio.run(itsm_sla_sweep.sweep_once())

    assert n == 1
    assert calls[0]["event_type"] == "sla_breach"
    assert calls[0]["severity"] == "CRITICAL"  # propagates the ticket's own severity, not a fixed default
    assert calls[0]["resource"] == "fedcba987654"
    assert "sla_breach" in calls[0]["flags"]
    assert calls[0]["raw_payload"]["external_ticket_key"] == "SEC-4471"


def test_itsm_sla_sweep_defaults_severity_to_high_when_ticket_has_none(monkeypatch):
    """itsm_sla_sweep.py: `ticket.get("severity") or "HIGH"` — a ticket record
    with no severity recorded must not silently become an INFO-level event."""
    monkeypatch.setattr(db, "expire_overdue_sla", lambda: [
        {"id": 10, "external_system": "servicenow", "external_ticket_key": "INC0012345",
         "finding_hash": "aaa111", "severity": None, "sla_due_at": "2026-07-20"},
    ])
    calls = _recorder(monkeypatch)
    asyncio.run(itsm_sla_sweep.sweep_once())
    assert calls[0]["severity"] == "HIGH"
