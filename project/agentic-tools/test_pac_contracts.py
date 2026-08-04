#!/usr/bin/env python3
"""
Unit tests for pac_contracts.py — the PaC negative-testing "schema contract"
layer (P0a). Pure-function tests plus two honesty checks that keep the
declared contracts from drifting away from the real producers:

  1. devops_monitoring's shipped Rego must pass its own contract cleanly —
     a regression guard so a future edit can't reintroduce the exact bug
     this module was built to catch (a rule keyed on an event.type literal
     or field the real pipeline never produces).
  2. The declared devops_monitoring field contract must be a superset of
     what scm_connectors.normalize_github_compliance/normalize_gitlab_compliance
     actually emit — so PROCESS_CONTRACTS can't quietly go stale.

    pytest test_pac_contracts.py -v
"""

from __future__ import annotations

import pac_contracts as pc
import pac_endpoints as pe
import pac_negative_tests as pnt
import scm_connectors


# ── extract_input_roots ─────────────────────────────────────────────────────

def test_extract_input_roots_finds_event_root():
    rego = 'deny[msg] if { input.event.type == "X" }'
    assert pc.extract_input_roots(rego) == {"event"}


def test_extract_input_roots_finds_multiple_distinct_roots():
    rego = """
    deny_a[msg] if { input.event.type == "X" }
    deny_b[msg] if { input.journal.amount > 100 }
    deny_c[msg] if { input.access_review.last_review_days > 90 }
    """
    assert pc.extract_input_roots(rego) == {"event", "journal", "access_review"}


def test_extract_input_roots_empty_for_no_input_refs():
    assert pc.extract_input_roots("package foo\n\ndeny[msg] { false }") == set()


# ── extract_input_event_refs ────────────────────────────────────────────────

def test_extract_input_event_refs():
    rego = 'deny[msg] if { input.event.enforce_admins == false; input.event.resource }'
    assert pc.extract_input_event_refs(rego) == {"enforce_admins", "resource"}


# ── extract_event_type_literals ─────────────────────────────────────────────

def test_extract_event_type_literals_both_orders():
    rego = '''
    deny_a[msg] if { input.event.type == "BRANCH_PROTECTION_BYPASSED" }
    deny_b[msg] if { "SAST_FINDING" == input.event.type }
    '''
    assert pc.extract_event_type_literals(rego) == {"BRANCH_PROTECTION_BYPASSED", "SAST_FINDING"}


# ── check_module_contract: the actual regression guard ─────────────────────

def test_devops_monitoring_rego_passes_its_own_contract():
    """Locks in the fix for the branch_protection_rule/BRANCH_PROTECTION_BYPASSED
    bug — if a future edit reintroduces a field or event-type mismatch, this
    test is the tripwire, not a manual code review."""
    rego = pe._REGO_DEFAULTS["devops_monitoring"]
    result = pc.check_module_contract("devops_monitoring", rego)
    assert result["ok"], result["findings"]
    assert result["unproducible_roots"] == []
    assert result["unknown_fields"] == []
    assert result["invalid_event_types"] == []
    assert result["unroutable_event_types"] == []


def test_hire_to_retire_rego_passes_its_own_contract():
    """Same tripwire as test_devops_monitoring_rego_passes_its_own_contract,
    for the Hire-to-Retire module added alongside oracle_hcm_tool.py."""
    rego = pe._REGO_DEFAULTS["hire_to_retire"]
    result = pc.check_module_contract("hire_to_retire", rego)
    assert result["ok"], result["findings"]
    assert result["unproducible_roots"] == []
    assert result["unknown_fields"] == []
    assert result["invalid_event_types"] == []
    assert result["unroutable_event_types"] == []


def test_record_to_report_event_shaped_rules_pass_their_contract():
    """record_to_report's original P-R2R-001..007 rules key on input.journal.*/
    input.account_recon.*/etc, which check_module_contract already knows are
    unproducible today (see pac_contracts.py's module docstring) — that's a
    pre-existing, accepted gap, not something this test re-litigates. This
    test locks in the *input.event.*-shaped* additions instead (Financial
    Risk Pipeline P-FIN-* and Treasury R2R-TREAS-*): no unknown fields, no
    invalid/unroutable event types, for the portion of the module that is
    actually reachable by the automated pipeline."""
    rego = pe._REGO_DEFAULTS["record_to_report"]
    result = pc.check_module_contract("record_to_report", rego)
    assert result["unknown_fields"] == [], result["findings"]
    assert result["invalid_event_types"] == [], result["findings"]
    assert result["unroutable_event_types"] == [], result["findings"]


def test_trade_compliance_rego_passes_its_own_contract():
    """Same tripwire as test_hire_to_retire_rego_passes_its_own_contract, for
    the Trade Compliance module added alongside denied_party_screening_tool.py."""
    rego = pe._REGO_DEFAULTS["trade_compliance"]
    result = pc.check_module_contract("trade_compliance", rego)
    assert result["ok"], result["findings"]
    assert result["unproducible_roots"] == []
    assert result["unknown_fields"] == []
    assert result["invalid_event_types"] == []
    assert result["unroutable_event_types"] == []


def test_procure_to_pay_vendor_risk_rules_pass_their_contract():
    """Same shape as test_record_to_report_event_shaped_rules_pass_their_contract:
    procure_to_pay's original P-P2P-* rules have pre-existing, documented gaps
    (input.purchase_order.*/input.invoice.*/etc unproducible roots, and a
    "new_vendor_activation" event type with no real producer) — not
    re-litigated here. This locks in the P-VEN-001/002 additions: no unknown
    fields for the vendor_risk_detail-derived fields, and the
    VENDOR_MASTER_CHANGE typo fix (was the lowercase "vendor_master_change",
    the same dead-rule bug shape as the original devops_monitoring bug)."""
    rego = pe._REGO_DEFAULTS["procure_to_pay"]
    result = pc.check_module_contract("procure_to_pay", rego)
    assert "vendor_name" not in result["unknown_fields"]
    assert "concentration_pct" not in result["unknown_fields"]
    assert "soc2_expires_at" not in result["unknown_fields"]
    assert "vendor_master_change" not in result["invalid_event_types"]
    assert result["unroutable_event_types"] == []


def test_itgc_ai_governance_rules_pass_their_contract():
    """Same shape as the record_to_report/procure_to_pay locked-in-additions
    tests: itgc's original rules have extensive pre-existing, documented gaps
    (input.access_review.*/input.backup.*/input.monitoring.* unproducible
    roots, and six invalid event-type literals like "user_provisioning") —
    not re-litigated here. This locks in the ITGC-AI-05/AI-06 additions."""
    rego = pe._REGO_DEFAULTS["itgc"]
    result = pc.check_module_contract("itgc", rego)
    assert "system_name" not in result["unknown_fields"]
    assert "risk_tier" not in result["unknown_fields"]
    assert "assessment_expires_at" not in result["unknown_fields"]
    assert "AI_ASSESSMENT_OVERDUE" not in result["invalid_event_types"]
    assert "AI_HUMAN_OVERSIGHT_MISSING" not in result["invalid_event_types"]
    assert result["unroutable_event_types"] == []


def test_check_module_contract_catches_the_original_bug_shape():
    """Reproduces the exact defect this module exists to catch, as a
    standalone fixture independent of whatever pac_endpoints.py ships today."""
    rego = '''
    package controls.devops.monitoring
    deny_branch_protection[msg] if {
        input.event.type == "branch_protection_rule"
        input.event.enforce_admins == false
        msg := "dead rule"
    }
    '''
    result = pc.check_module_contract("devops_monitoring", rego)
    assert not result["ok"]
    assert "branch_protection_rule" in result["invalid_event_types"]


def test_check_module_contract_flags_unproducible_root():
    rego = '''
    package controls.oracle_fusion.itgc
    deny_x[msg] if { input.access_review.last_review_days > 90; msg := "x" }
    '''
    result = pc.check_module_contract("itgc", rego)
    assert not result["ok"]
    assert "access_review" in result["unproducible_roots"]


def test_check_module_contract_flags_unknown_field_within_event_root():
    rego = '''
    package controls.devops.monitoring
    deny_x[msg] if { input.event.some_field_that_does_not_exist == true; msg := "x" }
    '''
    result = pc.check_module_contract("devops_monitoring", rego)
    assert not result["ok"]
    assert "some_field_that_does_not_exist" in result["unknown_fields"]


def test_check_module_contract_flags_unroutable_event_type():
    # SOD_VIOLATION is a real EventType (SAP source), but it never routes to
    # devops_monitoring — only devops_monitoring has a declared event-type
    # allowlist today, so the fixture targets that process.
    rego = '''
    package controls.devops.monitoring
    deny_x[msg] if { input.event.type == "SOD_VIOLATION"; msg := "x" }
    '''
    result = pc.check_module_contract("devops_monitoring", rego)
    assert not result["ok"]
    assert "SOD_VIOLATION" in result["unroutable_event_types"]


def test_check_module_contract_unknown_process_still_checks_roots():
    rego = 'deny_x[msg] if { input.something.field == 1; msg := "x" }'
    result = pc.check_module_contract("not_a_real_process", rego)
    assert not result["ok"]
    assert "something" in result["unproducible_roots"]


def test_remaining_three_original_builtin_modules_are_still_dead_by_root():
    """Documents the larger finding: itgc/receive_to_ship/record_to_report
    still reference top-level input roots the automated pipeline never
    constructs (input is always {"event": {...}}). order_to_cash and
    procure_to_pay were the same until generate_o2c_p2p_synthetic_log.py's
    wiring — see test_order_to_cash_and_procure_to_pay_are_no_longer_dead
    below. This test isn't asserting a bug should exist forever — it's a
    tripwire that will fail (loudly, as a welcome surprise) the day someone
    wires a real producer for one of these three and forgets to update it."""
    for process in ("itgc", "receive_to_ship", "record_to_report"):
        result = pc.check_module_contract(process, pe._REGO_DEFAULTS[process])
        assert result["unproducible_roots"], f"{process} unexpectedly has no unproducible roots"


def test_order_to_cash_is_no_longer_dead():
    """The other side of the tripwire above: order_to_cash now has a real
    producer (generate_o2c_p2p_synthetic_log.py, routed via
    mcp_governance._SOURCE_EVENT_TO_PAC_PROCESS, shaped by UBO/pipeline/
    silver.py's erp_transaction_detail spread) — check_module_contract must
    report a fully clean contract, matching devops_monitoring's."""
    result = pc.check_module_contract("order_to_cash", pe._REGO_DEFAULTS["order_to_cash"])
    assert result["ok"], result
    assert not result["unproducible_roots"]
    assert not result["unknown_fields"]
    assert not result["unroutable_event_types"]


def test_procure_to_pay_is_no_longer_dead_by_root_except_one_documented_gap():
    """Same wiring as order_to_cash for every rule except deny_vendor_event's
    second clause (input.event.type == "new_vendor_activation") — that one
    was already flagged in the Rego's own comment as pre-existing,
    deliberately-deferred debt ("no producer emits it... left as documented
    ... rather than invented a new EventType speculatively") and is out of
    scope here. Everything else must be clean: zero unproducible_roots (the
    nested input.purchase_order.*/input.invoice.*/etc. roots are gone), and
    the only unknown_fields/invalid_event_types entries are that one
    pre-existing gap, not a new one this wiring introduced."""
    result = pc.check_module_contract("procure_to_pay", pe._REGO_DEFAULTS["procure_to_pay"])
    assert not result["unproducible_roots"]
    assert result["unknown_fields"] == ["due_diligence_completed"]
    assert result["invalid_event_types"] == ["new_vendor_activation"]
    assert not result["unroutable_event_types"]


# ── check_observed_fields ────────────────────────────────────────────────────

def test_check_observed_fields_flags_undeclared():
    result = pc.check_observed_fields("devops_monitoring", {"resource", "a_brand_new_field"})
    assert not result["ok"]
    assert "a_brand_new_field" in result["undeclared"]


def test_check_observed_fields_clean_for_known_fields():
    result = pc.check_observed_fields("devops_monitoring", {"enforce_admins", "codeowners_present"})
    assert result["ok"]
    assert result["undeclared"] == []


# ── Contract-vs-producer honesty check ──────────────────────────────────────

def test_parse_opa_bindings_flattens_set_valued_object_not_array():
    """Regression test for a real production bug found while building the
    negative-control corpus: `opa eval -f json` serializes a Rego partial-set
    rule (`deny_x[msg] if {...; msg := "..."}`) as a JSON OBJECT whose KEYS
    are the message strings — {"DEVOPS-001: admin bypass": true} — never a
    JSON array, not even for a single-member set. The original parser called
    _extract_control_id on that whole dict (expecting a list/str), which
    silently returned None for every control_id on every real-OPA
    evaluation — invisible locally because this dev environment has no OPA
    binary, so tests only ever exercised the heuristic fallback, which
    parses Rego source directly and never hit this code path at all."""
    bindings = {
        "deny_branch_protection": {
            "DEVOPS-001: admins can bypass required checks (CRITICAL)": True,
        },
        "deny_evidence_finding": {},   # silent — empty set
    }
    fired, passed = pe._parse_opa_bindings(bindings)
    assert len(fired) == 1
    assert fired[0]["control_id"] == "DEVOPS-001"
    assert len(passed) == 1
    assert passed[0]["rule"] == "deny_evidence_finding"


def test_parse_opa_bindings_flattens_multiple_members_of_one_set():
    """A single deny_* rule with multiple simultaneously-fired messages
    (e.g. two separate deny_branch_protection[msg] blocks both matching)
    must produce one finding per message, not one finding for the whole set."""
    bindings = {
        "deny_branch_protection": {
            "DEVOPS-001: admin bypass": True,
            "DEVOPS-002: zero reviews": True,
        },
    }
    fired, passed = pe._parse_opa_bindings(bindings)
    assert passed == []
    assert {f["control_id"] for f in fired} == {"DEVOPS-001", "DEVOPS-002"}


def test_devops_monitoring_negative_corpus_passes_real_opa_when_available():
    """When a real OPA binary is on PATH (OPA_BINARY env var or `opa` on
    PATH — always true in the Docker image, per project/Dockerfile), run the
    full must-fire/must-not-fire corpus through the AUTHORITATIVE engine,
    not just the heuristic fallback every other test here exercises. Skips
    itself (rather than failing) when no OPA binary is available, since that
    reflects a real environment difference (local dev vs. Docker), not a
    defect — see pac_endpoints._find_opa_binary."""
    if not pe._find_opa_binary():
        return  # no OPA on this machine — nothing to prove here, not a failure
    result = pnt.run_corpus("devops_monitoring", pe._REGO_DEFAULTS["devops_monitoring"])
    assert result["ok"], [r for r in result["results"] if not r["passed"]]
    assert all(r["engine"] == "opa eval (authoritative)" for r in result["results"])


def test_scm_compliance_contract_matches_real_normalizer_output():
    """PROCESS_CONTRACTS['devops_monitoring']'s field set must be a superset
    of what the real normalizers emit — otherwise the contract itself is the
    thing that's out of date, and would start reporting false positives on
    every real compliance field."""
    protection = {
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {"required_approving_review_count": 2, "dismiss_stale_reviews": True},
        "required_status_checks": {"contexts": ["ci/codeql", "ci/unit-tests"]},
    }
    github_keys = set(scm_connectors.normalize_github_compliance(protection, "* @team\n").keys())
    gitlab_keys = set(scm_connectors.normalize_gitlab_compliance(
        {"code_owner_approval_required": True}, [], "* @team\n"
    ).keys())

    declared = pc.PROCESS_CONTRACTS["devops_monitoring"]["allowed_fields"]
    assert github_keys <= declared, github_keys - declared
    assert gitlab_keys <= declared, gitlab_keys - declared
