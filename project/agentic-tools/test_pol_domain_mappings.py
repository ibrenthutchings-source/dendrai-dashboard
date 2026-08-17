#!/usr/bin/env python3
"""
Unit tests for pol_domain_mappings.py — the POL-*/PaC-control_id -> Core
Domain crosswalk powering Continuous Monitoring's domain-grouped views.

The honesty check that matters most here: POL_DOMAIN_MAPPINGS must stay a
complete, exact match against UBO/policy/rules.py's live POLICY_REGISTRY —
a rule added there with no corresponding entry here would silently vanish
from every domain-grouped view instead of erroring, which is exactly the
kind of gap framework_mappings.py's docstring warns about.

    pytest test_pol_domain_mappings.py -v
"""

from __future__ import annotations

import re

import pol_domain_mappings as pdm


def _live_rule_ids() -> set[str]:
    """Parses UBO/policy/rules.py's rule_id="POL-..." literals directly from
    source rather than importing the UBO package — UBO.policy.rules and
    UBO.pipeline.silver import each other, and importing either in isolation
    from outside the package triggers a partial-init circular import."""
    import pathlib
    src_path = pathlib.Path(__file__).resolve().parents[2] / "UBO" / "policy" / "rules.py"
    src = src_path.read_text(encoding="utf-8")
    return set(re.findall(r'rule_id="(POL-[A-Z0-9-]+)"', src))


def test_every_live_pol_rule_has_a_domain_mapping():
    live = _live_rule_ids()
    assert live, "sanity check: found zero POL-* rules — the source parse itself may be broken"
    missing = live - set(pdm.POL_DOMAIN_MAPPINGS.keys())
    assert not missing, f"rule_ids live in UBO/policy/rules.py but missing from POL_DOMAIN_MAPPINGS: {missing}"


def test_no_stale_mappings_for_rules_that_no_longer_exist():
    live = _live_rule_ids()
    stale = set(pdm.POL_DOMAIN_MAPPINGS.keys()) - live
    assert not stale, f"POL_DOMAIN_MAPPINGS entries with no matching live rule: {stale}"


def test_every_mapped_domain_is_a_known_core_domain():
    unknown = set(pdm.POL_DOMAIN_MAPPINGS.values()) - set(pdm.CORE_DOMAINS)
    assert not unknown, f"domains used that aren't in CORE_DOMAINS: {unknown}"


def test_every_process_domain_is_a_known_core_domain():
    unknown = set(pdm.PROCESS_DOMAIN_MAPPINGS.values()) - set(pdm.CORE_DOMAINS)
    assert not unknown, f"process domains used that aren't in CORE_DOMAINS: {unknown}"


def test_get_domain_known_and_unknown():
    assert pdm.get_domain("POL-GH-002") == "Identity & Access Management"
    assert pdm.get_domain("POL-NOT-A-REAL-RULE") is None


def test_domain_for_violations_resolves_bracketed_pol_format():
    domain = pdm.domain_for_violations(
        ["[POL-CORE-001:HIGH] actor_id is empty or UNKNOWN (received: 'UNKNOWN')"]
    )
    assert domain == "Technology & Change Management"


def test_domain_for_violations_returns_none_for_empty_list():
    assert pdm.domain_for_violations([]) is None
    assert pdm.domain_for_violations(None) is None


def test_domain_for_violations_returns_none_for_unmapped_bracketed_rule():
    """An honest gap, not a guess — a POL-* id this table doesn't know about
    must resolve to None, never a fabricated domain."""
    assert pdm.domain_for_violations(["[POL-MADE-UP-999:LOW] fictitious rule"]) is None


def test_domain_for_violations_first_match_wins_across_multiple_entries():
    domain = pdm.domain_for_violations([
        "[POL-MADE-UP-999:LOW] unmapped, should be skipped",
        "[POL-TC-001:CRITICAL] restricted-party match",
    ])
    assert domain == "Regulatory & Compliance"


def test_domain_for_violations_resolves_bare_control_id_via_process_fallback():
    domain = pdm.domain_for_violations(["OTC-P005"], control_id_to_process={"OTC-P005": "order_to_cash"})
    assert domain == "Financial Reporting & Controls"


def test_domain_for_violations_bare_control_id_without_lookup_table_stays_none():
    """No control_id_to_process supplied — must not guess."""
    assert pdm.domain_for_violations(["OTC-P005"]) is None


def test_domain_for_violations_bare_control_id_not_in_catalog_stays_none():
    """A control_id absent from the caller's lookup (e.g. a legacy id never
    seeded into controls_catalog) must resolve to None, not error or guess."""
    domain = pdm.domain_for_violations(["ITGC-CM004"], control_id_to_process={"OTC-P005": "order_to_cash"})
    assert domain is None


# ── domain_for_process_step (NOT_REVIEWED events — no policy_violations exist) ──

def test_domain_for_process_step_resolves_known_step():
    assert pdm.domain_for_process_step("Journal Entry Posted") == "Financial Reporting & Controls"
    assert pdm.domain_for_process_step("Access Requested") == "Identity & Access Management"
    assert pdm.domain_for_process_step("Goods Shipped") == "Operational Resilience"


def test_domain_for_process_step_none_for_unknown_step():
    assert pdm.domain_for_process_step("Some Step Nobody Defined") is None


def test_domain_for_process_step_none_for_empty_input():
    assert pdm.domain_for_process_step(None) is None
    assert pdm.domain_for_process_step("") is None


def test_domain_for_process_step_covers_every_process_template():
    """Every process_mining_tool.PROCESS_TEMPLATES id must resolve through
    PROCESS_DOMAIN_MAPPINGS — the same completeness discipline
    test_every_live_pol_rule_has_a_domain_mapping applies to POL_DOMAIN_MAPPINGS,
    applied here so a new synthetic process can't silently reintroduce the
    'NOT_REVIEWED always Unclassified' gap this function closes."""
    import process_mining_tool as pm
    for process_id, template in pm.PROCESS_TEMPLATES.items():
        for step in template["steps"]:
            assert pdm.domain_for_process_step(step) is not None, (
                f"step '{step}' (process '{process_id}') has no domain — "
                f"add '{process_id}' to PROCESS_DOMAIN_MAPPINGS"
            )


# ── subdomain_for_violations (IAM rule-family breakdown) ────────────────────────

def test_subdomain_for_violations_sod_family():
    domain = pdm.subdomain_for_violations(["[POL-SYS-001:CRITICAL] SoD violation"])
    assert domain == "SoD & Privilege Conflicts"


def test_subdomain_for_violations_access_governance_family():
    domain = pdm.subdomain_for_violations(["[POL-SP-002:HIGH] dormant privileged account"])
    assert domain == "Access Governance & Reviews"


def test_subdomain_for_violations_repo_branch_family():
    domain = pdm.subdomain_for_violations(["[POL-GH-002:HIGH] force push to protected branch"])
    assert domain == "Repo & Branch Access"


def test_subdomain_for_violations_none_for_non_iam_rule():
    domain = pdm.subdomain_for_violations(["[POL-TC-001:CRITICAL] restricted-party match"])
    assert domain is None


def test_subdomain_for_violations_none_for_itgc_catchall():
    """The itgc bare-control-id catch-all resolves to the IAM domain via
    PROCESS_DOMAIN_MAPPINGS but is deliberately left unsplit — it's real
    ITGC volume, not identity-specific, and IAM_SUBDOMAIN_MAPPINGS only
    covers per-rule bracketed POL-* ids."""
    domain = pdm.subdomain_for_violations(["ITGC-007"], control_id_to_process={"ITGC-007": "itgc"})
    assert domain is None


def test_subdomain_for_violations_first_match_wins():
    domain = pdm.subdomain_for_violations([
        "[POL-TC-001:CRITICAL] unrelated, not in IAM_SUBDOMAIN_MAPPINGS",
        "[POL-GH-002:HIGH] force push",
    ])
    assert domain == "Repo & Branch Access"


def test_subdomain_for_violations_every_value_domain_maps_to_iam():
    """Every rule mapped into IAM_SUBDOMAIN_MAPPINGS must itself resolve to
    the IAM domain in POL_DOMAIN_MAPPINGS — a sub-domain for a rule that
    isn't even IAM at the top level would be a contradiction."""
    for rule_id in pdm.IAM_SUBDOMAIN_MAPPINGS:
        assert pdm.POL_DOMAIN_MAPPINGS.get(rule_id) == "Identity & Access Management", (
            f"{rule_id} has an IAM sub-domain but POL_DOMAIN_MAPPINGS doesn't route it to IAM"
        )
