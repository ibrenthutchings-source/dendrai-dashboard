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
