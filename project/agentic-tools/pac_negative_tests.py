#!/usr/bin/env python3
"""
Policy-as-Code negative-control corpus — negative testing, layer 2.

Layer 1 (pac_contracts.py) proves a rule *can* fire — its fields and event-
type literals correspond to something the real pipeline produces. This layer
proves it actually *does*: a curated set of "must-fire" (known-bad state
that a specific control_id must catch) and "must-not-fire" (known-good state
that must produce zero denials) fixtures, run against whatever Rego is
currently live for a process.

Two things this corpus is for for, deliberately kept separate:

  1. A REGRESSION GATE on policy edits. Before/after saving a new Rego
     version, run the corpus — a control that used to catch the admin-bypass
     case and no longer does is a silent regression, not a diff a human
     reviewing Rego text reliably spots.
  2. A PERIODIC FULL EVALUATION (see itsm_sla_sweep.py/risk_waiver_sweep.py
     for the established "scheduled sweep" shape this will mirror once
     wired into api_server.py's lifespan — that wiring is P0d/P1a, not this
     file) — proving on a schedule, not just at edit time, that every
     control this platform claims to enforce still does.

Only devops_monitoring gets real fixtures here. pac_contracts.py already
proved the other five built-in processes (itgc, order_to_cash,
procure_to_pay, receive_to_ship, record_to_report) reference input roots
the automated pipeline never constructs — writing must-fire fixtures for
input.journal.*/input.invoice.*/etc. would just be testing a manual
POST /pac/evaluate sandbox scenario, not anything the real system does.
That would be exactly the theater this whole effort exists to eliminate.
Corpora for those processes belong in the same commit that wires a real
producer for them, not before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pac_endpoints


@dataclass
class Fixture:
    name: str
    input_event: dict[str, Any]
    expect: str                              # "fire" | "silent"
    expected_control_id: Optional[str] = None  # required when expect == "fire"
    rationale: str = ""


# ── devops_monitoring corpus ───────────────────────────────────────────────
# Mirrors the real shapes _evaluate_pac_policy constructs: base fields
# (type/resource/resource_type/action/outcome) plus whichever of
# scm_connectors.normalize_*_compliance's or evidence_endpoints' fields the
# scenario needs — see pac_contracts.PROCESS_CONTRACTS['devops_monitoring'].

_FULLY_COMPLIANT_SCM = {
    "enforce_admins": True, "required_approving_review_count": 2,
    "dismiss_stale_reviews": True, "has_required_sast_check": True,
    "has_required_test_check": True, "codeowners_present": True,
    "codeowners_covers_workflows": True,
}

DEVOPS_MONITORING_FIXTURES: list[Fixture] = [
    Fixture(
        name="admin_bypass_must_fire",
        input_event={"event": {**_FULLY_COMPLIANT_SCM, "type": "BRANCH_PROTECTION_BYPASSED",
                                "resource": "org/repo@main", "enforce_admins": False}},
        expect="fire", expected_control_id="DEVOPS-001",
        rationale="Admins can bypass required checks — the single most severe SCM finding.",
    ),
    Fixture(
        name="zero_required_reviews_must_fire",
        input_event={"event": {**_FULLY_COMPLIANT_SCM, "type": "BRANCH_PROTECTION_BYPASSED",
                                "resource": "org/repo@main", "required_approving_review_count": 0}},
        expect="fire", expected_control_id="DEVOPS-002",
        rationale="Merge requires zero approvals.",
    ),
    Fixture(
        name="stale_reviews_not_dismissed_must_fire",
        input_event={"event": {**_FULLY_COMPLIANT_SCM, "type": "BRANCH_PROTECTION_BYPASSED",
                                "resource": "org/repo@main", "dismiss_stale_reviews": False}},
        expect="fire", expected_control_id="DEVOPS-003",
        rationale="A stale approval survives new commits.",
    ),
    Fixture(
        name="no_sast_check_must_fire",
        input_event={"event": {**_FULLY_COMPLIANT_SCM, "type": "BRANCH_PROTECTION_BYPASSED",
                                "resource": "org/repo@main", "has_required_sast_check": False}},
        expect="fire", expected_control_id="DEVOPS-004",
        rationale="No required security scan gate before merge.",
    ),
    Fixture(
        name="no_codeowners_must_fire",
        input_event={"event": {**_FULLY_COMPLIANT_SCM, "type": "BRANCH_PROTECTION_BYPASSED",
                                "resource": "org/repo@main", "codeowners_present": False}},
        expect="fire", expected_control_id="DEVOPS-005",
        rationale="No CODEOWNERS file at all — nobody is a mandatory reviewer.",
    ),
    Fixture(
        name="codeowners_missing_workflow_coverage_must_fire",
        input_event={"event": {**_FULLY_COMPLIANT_SCM, "type": "BRANCH_PROTECTION_BYPASSED",
                                "resource": "org/repo@main", "codeowners_covers_workflows": False}},
        expect="fire", expected_control_id="DEVOPS-006",
        rationale="CI workflow definitions can be modified without security review.",
    ),
    Fixture(
        name="fully_compliant_branch_must_be_silent",
        input_event={"event": {**_FULLY_COMPLIANT_SCM, "type": "BRANCH_PROTECTION_BYPASSED",
                                "resource": "org/repo@main"}},
        expect="silent",
        rationale="Every control satisfied — a false positive here is exactly what "
                   "erodes trust in the policy and trains reviewers to ignore it.",
    ),
    Fixture(
        name="critical_sarif_finding_must_fire",
        input_event={"event": {"type": "SAST_FINDING", "severity": "CRITICAL",
                                "rule_id": "py/sql-injection", "resource": "app/db.py"}},
        expect="fire", expected_control_id="DEVOPS-007",
        rationale="CRITICAL SARIF finding must start the 7-day remediation SLA.",
    ),
    Fixture(
        name="high_sarif_finding_must_fire",
        input_event={"event": {"type": "SAST_FINDING", "severity": "HIGH",
                                "rule_id": "py/weak-crypto", "resource": "app/crypto.py"}},
        expect="fire", expected_control_id="DEVOPS-008",
        rationale="HIGH SARIF finding must start the 30-day remediation SLA.",
    ),
    Fixture(
        name="low_sarif_finding_must_be_silent",
        input_event={"event": {"type": "SAST_FINDING", "severity": "LOW",
                                "rule_id": "py/unused-import", "resource": "app/utils.py"}},
        expect="silent",
        rationale="LOW findings are logged as evidence but don't carry an SLA — "
                   "firing here would mean every scan escalates something.",
    ),
    Fixture(
        name="itsm_sla_breach_must_fire",
        input_event={"event": {"type": "SLA_BREACH", "external_system": "jira",
                                "external_ticket_key": "SEC-142", "finding_hash": "abc123",
                                "sla_due_at": "2026-01-01T00:00:00Z", "resource": "SEC-142"}},
        expect="fire", expected_control_id="DEVOPS-009",
        rationale="A ticket that missed its remediation SLA must re-escalate the finding.",
    ),
]

CORPORA: dict[str, list[Fixture]] = {
    "devops_monitoring": DEVOPS_MONITORING_FIXTURES,
}


def run_fixture(rego_content: str, fixture: Fixture) -> dict:
    result = pac_endpoints.evaluate_policy_event(rego_content, fixture.input_event)
    fired = result.get("rules_fired") or []
    fired_control_ids = {r.get("control_id") for r in fired if r.get("control_id")}
    fired_rule_names = {r.get("rule") for r in fired}

    if fixture.expect == "fire":
        passed = bool(fired) and (
            fixture.expected_control_id is None or fixture.expected_control_id in fired_control_ids
        )
    elif fixture.expect == "silent":
        passed = not fired
    else:
        raise ValueError(f"Fixture {fixture.name!r} has invalid expect={fixture.expect!r}")

    return {
        "name": fixture.name,
        "expect": fixture.expect,
        "expected_control_id": fixture.expected_control_id,
        "passed": passed,
        "fired_control_ids": sorted(fired_control_ids),
        "fired_rule_names": sorted(fired_rule_names),
        "engine": result.get("evaluation"),
        "rationale": fixture.rationale,
    }


def run_corpus(process: str, rego_content: str) -> dict:
    """Run every fixture registered for `process` against `rego_content`.
    Returns per-fixture results plus a pass/fail summary. A process with no
    registered corpus reports that explicitly rather than a false "all
    passed" from an empty loop — see pac_contracts's unproducible_roots for
    why (no live producer means no honest fixture can be written yet)."""
    fixtures = CORPORA.get(process)
    if not fixtures:
        return {
            "process": process, "ok": None, "total": 0, "passed": 0, "failed": 0,
            "results": [],
            "note": f"No negative-control corpus registered for '{process}' — "
                    f"see pac_contracts.check_module_contract for why this process "
                    f"may have no live producer to test against yet.",
        }
    results = [run_fixture(rego_content, f) for f in fixtures]
    passed = sum(1 for r in results if r["passed"])
    return {
        "process": process,
        "ok": passed == len(results),
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }


def run_all_corpora(rego_by_process: dict[str, str]) -> dict:
    """rego_by_process: {process: rego_content} for every process to check —
    typically the caller's currently-live (saved-or-default) module per
    process. Used by the periodic full-evaluation sweep (P1a)."""
    return {process: run_corpus(process, rego) for process, rego in rego_by_process.items()}
