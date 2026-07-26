#!/usr/bin/env python3
"""
Unit tests for the ITSM/Jira-ServiceNow SLA Bridge. Pure-function tests
only — no DB, no network. Mirrors test_devops_monitoring.py's shape;
itsm_sla_sweep.py itself is untested here for the same reason
risk_waiver_sweep.py is: it's a thin DB+adjudication-pipeline orchestrator
with no pure logic of its own to unit test in isolation.

    pytest test_itsm_bridge.py -v
"""

from __future__ import annotations

import itsm_connectors
import pac_endpoints


# ── itsm_connectors.sla_hours_for_severity ─────────────────────────────────────

def test_sla_hours_critical_is_shortest():
    assert itsm_connectors.sla_hours_for_severity("CRITICAL") == 48


def test_sla_hours_high():
    assert itsm_connectors.sla_hours_for_severity("HIGH") == 168


def test_sla_hours_case_insensitive():
    assert itsm_connectors.sla_hours_for_severity("critical") == itsm_connectors.sla_hours_for_severity("CRITICAL")


def test_sla_hours_unknown_severity_falls_back_to_medium():
    assert itsm_connectors.sla_hours_for_severity("NOT_A_SEVERITY") == itsm_connectors.sla_hours_for_severity("MEDIUM")


def test_sla_hours_ordering_critical_lt_high_lt_medium_lt_low():
    hours = [itsm_connectors.sla_hours_for_severity(s) for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")]
    assert hours == sorted(hours)


# ── itsm_connectors.normalize_status ────────────────────────────────────────────

def test_normalize_jira_open_statuses():
    for raw in ("To Do", "Open", "Backlog"):
        assert itsm_connectors.normalize_status("jira", raw) == "open"


def test_normalize_jira_in_progress():
    assert itsm_connectors.normalize_status("jira", "In Progress") == "in_progress"


def test_normalize_jira_resolved():
    for raw in ("Done", "Resolved"):
        assert itsm_connectors.normalize_status("jira", raw) == "resolved"


def test_normalize_jira_closed_and_cancelled():
    assert itsm_connectors.normalize_status("jira", "Closed") == "closed"
    assert itsm_connectors.normalize_status("jira", "Cancelled") == "cancelled"


def test_normalize_jira_unknown_status_defaults_to_in_progress_not_resolved():
    # Safer to under-report resolution than to stop an SLA clock early on an
    # unrecognized custom workflow status.
    assert itsm_connectors.normalize_status("jira", "Some Custom Status") == "in_progress"


def test_normalize_servicenow_state_codes():
    assert itsm_connectors.normalize_status("servicenow", "1") == "open"
    assert itsm_connectors.normalize_status("servicenow", "2") == "in_progress"
    assert itsm_connectors.normalize_status("servicenow", "6") == "resolved"
    assert itsm_connectors.normalize_status("servicenow", "7") == "closed"
    assert itsm_connectors.normalize_status("servicenow", "8") == "cancelled"


def test_normalize_servicenow_unknown_code_defaults_to_in_progress():
    assert itsm_connectors.normalize_status("servicenow", "99") == "in_progress"


def test_normalize_unknown_system_defaults_to_in_progress():
    assert itsm_connectors.normalize_status("not_a_real_system", "whatever") == "in_progress"


# ── itsm_connectors.is_terminal_status ──────────────────────────────────────────

def test_is_terminal_status():
    assert itsm_connectors.is_terminal_status("closed") is True
    assert itsm_connectors.is_terminal_status("cancelled") is True
    assert itsm_connectors.is_terminal_status("open") is False
    assert itsm_connectors.is_terminal_status("in_progress") is False
    assert itsm_connectors.is_terminal_status(None) is False


# ── devops_monitoring Rego: DEVOPS-009 SLA breach ───────────────────────────────

def _rego() -> str:
    return pac_endpoints._REGO_DEFAULTS["devops_monitoring"]


def test_devops_monitoring_rego_fires_on_sla_breach():
    input_event = {"event": {
        "type": "SLA_BREACH", "external_ticket_key": "SEC-142", "external_system": "jira",
        "finding_hash": "abc123", "sla_due_at": "2026-07-20T00:00:00Z",
    }}
    result = pac_endpoints.evaluate_policy_event(_rego(), input_event)
    fired_rules = {r["rule"] for r in result["rules_fired"]}
    assert "deny_sla_breach" in fired_rules


def test_devops_monitoring_rego_silent_when_not_sla_breach_type():
    input_event = {"event": {"type": "SOME_OTHER_EVENT", "external_ticket_key": "SEC-142"}}
    result = pac_endpoints.evaluate_policy_event(_rego(), input_event)
    fired_rules = {r["rule"] for r in result["rules_fired"]}
    assert "deny_sla_breach" not in fired_rules
