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

# ── infrastructure_monitoring corpus ────────────────────────────────────────
# Mirrors iaas_connectors.normalize_postgres_compliance()'s output shape.

_FULLY_COMPLIANT_DB = {
    "ssl_enabled": True, "password_encryption": "scram-sha-256",
    "log_connections": True, "row_security_enabled": True,
    "superuser_count": 1, "unencrypted_connection_count": 0,
}

INFRASTRUCTURE_MONITORING_FIXTURES: list[Fixture] = [
    Fixture(
        name="ssl_not_enforced_must_fire",
        input_event={"event": {**_FULLY_COMPLIANT_DB, "type": "INFRASTRUCTURE_FINDING",
                                "resource": "primary-db", "ssl_enabled": False}},
        expect="fire", expected_control_id="INFRA-001",
        rationale="Connections can be made in plaintext.",
    ),
    Fixture(
        name="weak_password_encryption_must_fire",
        input_event={"event": {**_FULLY_COMPLIANT_DB, "type": "INFRASTRUCTURE_FINDING",
                                "resource": "primary-db", "password_encryption": "md5"}},
        expect="fire", expected_control_id="INFRA-002",
        rationale="md5 password hashing is materially weaker than scram-sha-256.",
    ),
    Fixture(
        name="superuser_sprawl_must_fire",
        input_event={"event": {**_FULLY_COMPLIANT_DB, "type": "INFRASTRUCTURE_FINDING",
                                "resource": "primary-db", "superuser_count": 5}},
        expect="fire", expected_control_id="INFRA-003",
        rationale="Excess superusers widen the blast radius of one compromised credential.",
    ),
    Fixture(
        name="unencrypted_active_connection_must_fire",
        input_event={"event": {**_FULLY_COMPLIANT_DB, "type": "INFRASTRUCTURE_FINDING",
                                "resource": "primary-db", "unencrypted_connection_count": 2}},
        expect="fire", expected_control_id="INFRA-004",
        rationale="A live connection is transmitting in plaintext right now.",
    ),
    Fixture(
        name="connection_logging_disabled_must_fire",
        input_event={"event": {**_FULLY_COMPLIANT_DB, "type": "INFRASTRUCTURE_FINDING",
                                "resource": "primary-db", "log_connections": False}},
        expect="fire", expected_control_id="INFRA-005",
        rationale="A compromised credential's access can't be reconstructed without connection logs.",
    ),
    Fixture(
        name="fully_compliant_db_must_be_silent",
        input_event={"event": {**_FULLY_COMPLIANT_DB, "type": "INFRASTRUCTURE_FINDING", "resource": "primary-db"}},
        expect="silent",
        rationale="Every control satisfied — a false positive here trains reviewers to ignore this process.",
    ),
    Fixture(
        name="unexpected_public_domain_must_fire",
        input_event={"event": {"type": "INFRASTRUCTURE_FINDING", "resource": "internal-worker",
                                "unexpected_public_domain": True}},
        expect="fire", expected_control_id="INFRA-006",
        rationale="A service quietly gaining public exposure should never go unnoticed.",
    ),
    Fixture(
        name="image_digest_mismatch_must_fire",
        input_event={"event": {"type": "INFRASTRUCTURE_FINDING", "resource": "dendrai-intelligenza",
                                "image_digest_mismatch": True}},
        expect="fire", expected_control_id="INFRA-007",
        rationale="A running deployment with no matching pipeline attestation can't be traced back to a known build.",
    ),
    Fixture(
        name="image_digest_mismatch_unknown_must_be_silent",
        # None (not False) — no attestation data ingested yet, so nothing to
        # compare against. Must NOT fire; firing here would mean every
        # deployment in an environment with no attestations flags forever.
        input_event={"event": {"type": "INFRASTRUCTURE_FINDING", "resource": "dendrai-intelligenza",
                                "image_digest_mismatch": None, "unexpected_public_domain": False}},
        expect="silent",
        rationale="No attestation data yet is 'unknown', not 'mismatched' — never fabricate a finding.",
    ),
]

# ── order_to_cash / procure_to_pay corpora ──────────────────────────────────
# Legitimate as of generate_o2c_p2p_synthetic_log.py's wiring — per this
# module's own docstring, these were withheld until a real producer existed
# ("Corpora for those processes belong in the same commit that wires a real
# producer for them, not before"). Mirrors pac_contracts._ERP_TRANSACTION_
# FIELDS' flat field names exactly. One "fully compliant" baseline per
# EventType, one must-fire fixture per control_id (breaking exactly the
# field that control checks), sharing the baseline as the must-not-fire
# fixture wherever one EventType backs more than one control_id (INVOICE_
# MATCH_EVENT backs both P2P-P002 and P2P-P004).

_COMPLIANT_REVENUE_EVENT = {
    "type": "REVENUE_RECOGNITION_EVENT",
    "txn_type": "revenue_recognition", "txn_performance_obligation_satisfied": True,
    "txn_amount": 50000, "txn_order_number": "SO-1001",
    "contract_value": 500000, "contract_reviewed_by_legal": True, "contract_id": "CTR-1",
    "txn_constrained_estimate_documented": True, "txn_contract_id": "CTR-1",
}
_COMPLIANT_SALES_ORDER_EVENT = {
    "type": "SALES_ORDER_CREDIT_EVENT",
    "so_status": "booked", "customer_credit_limit": 100000, "so_total": 50000,
    "so_credit_override_approved_by": "manager1", "so_order_number": "SO-2001",
}
_COMPLIANT_BILLING_EVENT = {
    "type": "BILLING_EVENT",
    "inv_type": "manual", "inv_approved_by": "mgr1", "inv_amount": 5000,
    "inv_number": "INV-3001", "inv_billing_date": "2026-01-10",
    "inv_shipment_date": "2026-01-05", "inv_days_billed_before_shipment": 5,
}
_COMPLIANT_CASH_EVENT = {
    "type": "CASH_APPLICATION_EVENT",
    "cash_unapplied_days": 5, "cash_receipt_number": "CR-4001", "cash_amount": 20000,
}
_COMPLIANT_CUSTOMER_MASTER_EVENT = {
    "type": "CUSTOMER_MASTER_CHANGE",
    "field": "bank_account", "dual_approved": True, "customer_name": "Acme Co",
}
_COMPLIANT_AR_AGING_EVENT = {
    "type": "AR_AGING_EVENT",
    "ar_days_outstanding": 30, "ar_amount": 100000, "ar_customer_name": "Acme",
    "ar_collection_action_documented": True,
}

ORDER_TO_CASH_FIXTURES: list[Fixture] = [
    Fixture(
        name="revenue_recognized_before_performance_obligation_must_fire",
        input_event={"event": {**_COMPLIANT_REVENUE_EVENT, "txn_performance_obligation_satisfied": False}},
        expect="fire", expected_control_id="OTC-P001",
        rationale="Revenue booked before the performance obligation is satisfied is an ASC 606 violation.",
    ),
    Fixture(
        name="fully_compliant_revenue_event_must_be_silent",
        input_event={"event": _COMPLIANT_REVENUE_EVENT},
        expect="silent",
        rationale="Every revenue-recognition control satisfied — must not fire.",
    ),
    Fixture(
        name="sales_order_over_credit_limit_without_override_must_fire",
        input_event={"event": {**_COMPLIANT_SALES_ORDER_EVENT, "so_total": 150000,
                                "so_credit_override_approved_by": None}},
        expect="fire", expected_control_id="OTC-P002",
        rationale="Order exceeds the customer's credit limit with no override approval on file.",
    ),
    Fixture(
        name="fully_compliant_sales_order_event_must_be_silent",
        input_event={"event": _COMPLIANT_SALES_ORDER_EVENT},
        expect="silent",
        rationale="Order within credit limit — must not fire.",
    ),
    Fixture(
        name="unapproved_manual_invoice_over_threshold_must_fire",
        input_event={"event": {**_COMPLIANT_BILLING_EVENT, "inv_approved_by": None, "inv_amount": 15000}},
        expect="fire", expected_control_id="OTC-P003",
        rationale="Manual invoice over $10K with no manager approval.",
    ),
    Fixture(
        name="fully_compliant_billing_event_must_be_silent",
        input_event={"event": _COMPLIANT_BILLING_EVENT},
        expect="silent",
        rationale="Invoice approved and within threshold — must not fire.",
    ),
    Fixture(
        name="cash_receipt_unapplied_past_sla_must_fire",
        input_event={"event": {**_COMPLIANT_CASH_EVENT, "cash_unapplied_days": 45}},
        expect="fire", expected_control_id="OTC-P004",
        rationale="Cash receipt unapplied for 45 days breaches the 30-day AR SLA.",
    ),
    Fixture(
        name="fully_compliant_cash_event_must_be_silent",
        input_event={"event": _COMPLIANT_CASH_EVENT},
        expect="silent",
        rationale="Receipt applied within SLA — must not fire.",
    ),
    Fixture(
        name="customer_bank_account_change_without_dual_approval_must_fire",
        input_event={"event": {**_COMPLIANT_CUSTOMER_MASTER_EVENT, "dual_approved": False}},
        expect="fire", expected_control_id="OTC-P005",
        rationale="Bank-account change to customer master data requires dual approval.",
    ),
    Fixture(
        name="fully_compliant_customer_master_event_must_be_silent",
        input_event={"event": _COMPLIANT_CUSTOMER_MASTER_EVENT},
        expect="silent",
        rationale="Dual-approved sensitive-field change — must not fire.",
    ),
    Fixture(
        name="ar_aging_over_90_days_without_collection_action_must_fire",
        input_event={"event": {**_COMPLIANT_AR_AGING_EVENT, "ar_days_outstanding": 120,
                                "ar_amount": 75000, "ar_collection_action_documented": False}},
        expect="fire", expected_control_id="OTC-P006",
        rationale="Material AR balance over 90 days outstanding with no documented collection action.",
    ),
    Fixture(
        name="fully_compliant_ar_aging_event_must_be_silent",
        input_event={"event": _COMPLIANT_AR_AGING_EVENT},
        expect="silent",
        rationale="AR balance current and documented — must not fire.",
    ),
]

_COMPLIANT_PO_EVENT = {
    "type": "PURCHASE_ORDER_EVENT",
    "po_total": 30000, "po_vp_approved": True, "po_number": "PO-5001",
    "po_cfo_approved": True, "po_type": "standard", "po_annual_review_completed": True,
}
_COMPLIANT_INVOICE_MATCH_EVENT = {
    "type": "INVOICE_MATCH_EVENT",
    "inv_matching_type": "3_way", "inv_amount": 10000, "po_total": 10000,
    "inv_number": "INV-6001", "goods_receipt_confirmed": True,
    "inv_duplicate_score": 0.1, "inv_duplicate_override_reason": None,
}
_COMPLIANT_VENDOR_MASTER_EVENT = {
    "type": "VENDOR_MASTER_CHANGE",
    "field": "bank_account_number", "dual_approved": True, "vendor_name": "Acme Supplies",
}
_COMPLIANT_PAYMENT_RUN_EVENT = {
    "type": "PAYMENT_RUN_EVENT",
    "pay_batch_total": 50000, "pay_batch_treasury_approved": True, "pay_batch_name": "Batch-7001",
    "pay_type": "ach", "pay_two_factor_confirmed": True, "pay_id": "PAY-7001", "pay_amount": 50000,
}
_COMPLIANT_SOD_EVENT = {
    "type": "PROCUREMENT_SOD_CONFLICT",
    "user_oracle_roles": ["AP_INVOICE_ENTRY"], "user_username": "jdoe",
}

PROCURE_TO_PAY_FIXTURES: list[Fixture] = [
    Fixture(
        name="po_over_50k_without_vp_approval_must_fire",
        input_event={"event": {**_COMPLIANT_PO_EVENT, "po_total": 68000, "po_vp_approved": False}},
        expect="fire", expected_control_id="P2P-P001",
        rationale="PO over the $50K VP-approval threshold with no VP sign-off.",
    ),
    Fixture(
        name="fully_compliant_po_event_must_be_silent",
        input_event={"event": _COMPLIANT_PO_EVENT},
        expect="silent",
        rationale="PO within threshold and approved — must not fire.",
    ),
    Fixture(
        name="three_way_match_variance_over_tolerance_must_fire",
        input_event={"event": {**_COMPLIANT_INVOICE_MATCH_EVENT, "inv_amount": 15000, "po_total": 10000}},
        expect="fire", expected_control_id="P2P-P002",
        rationale="50% invoice/PO variance is far outside the 5% three-way-match tolerance.",
    ),
    Fixture(
        name="duplicate_invoice_without_override_must_fire",
        input_event={"event": {**_COMPLIANT_INVOICE_MATCH_EVENT, "inv_duplicate_score": 0.95,
                                "inv_duplicate_override_reason": None}},
        expect="fire", expected_control_id="P2P-P004",
        rationale="High duplicate-detection score with no documented override reason.",
    ),
    Fixture(
        name="fully_compliant_invoice_match_event_must_be_silent",
        input_event={"event": _COMPLIANT_INVOICE_MATCH_EVENT},
        expect="silent",
        rationale="Matched, receipted, and not a duplicate — must not fire for either P2P-P002 or P2P-P004.",
    ),
    Fixture(
        name="vendor_bank_account_change_without_dual_approval_must_fire",
        input_event={"event": {**_COMPLIANT_VENDOR_MASTER_EVENT, "dual_approved": False}},
        expect="fire", expected_control_id="P2P-P003",
        rationale="Vendor bank-detail change requires dual approval.",
    ),
    Fixture(
        name="fully_compliant_vendor_master_event_must_be_silent",
        input_event={"event": _COMPLIANT_VENDOR_MASTER_EVENT},
        expect="silent",
        rationale="Dual-approved sensitive-field change — must not fire.",
    ),
    Fixture(
        name="payment_batch_over_100k_without_treasury_approval_must_fire",
        input_event={"event": {**_COMPLIANT_PAYMENT_RUN_EVENT, "pay_batch_total": 150000,
                                "pay_batch_treasury_approved": False}},
        expect="fire", expected_control_id="P2P-P005",
        rationale="Payment batch over the $100K Treasury-approval threshold with no sign-off.",
    ),
    Fixture(
        name="fully_compliant_payment_run_event_must_be_silent",
        input_event={"event": _COMPLIANT_PAYMENT_RUN_EVENT},
        expect="silent",
        rationale="Batch within threshold, wire two-factor confirmed — must not fire.",
    ),
    Fixture(
        name="invoice_entry_and_payment_approval_role_conflict_must_fire",
        input_event={"event": {**_COMPLIANT_SOD_EVENT,
                                "user_oracle_roles": ["AP_INVOICE_ENTRY", "AP_PAYMENT_APPROVAL"]}},
        expect="fire", expected_control_id="P2P-P006",
        rationale="One user holding both AP Invoice Entry and Payment Approval is a classic P2P SoD conflict.",
    ),
    Fixture(
        name="fully_compliant_sod_event_must_be_silent",
        input_event={"event": _COMPLIANT_SOD_EVENT},
        expect="silent",
        rationale="Single, non-conflicting role — must not fire.",
    ),
]

CORPORA: dict[str, list[Fixture]] = {
    "devops_monitoring": DEVOPS_MONITORING_FIXTURES,
    "infrastructure_monitoring": INFRASTRUCTURE_MONITORING_FIXTURES,
    "order_to_cash": ORDER_TO_CASH_FIXTURES,
    "procure_to_pay": PROCURE_TO_PAY_FIXTURES,
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
