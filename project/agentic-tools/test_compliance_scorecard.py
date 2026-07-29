#!/usr/bin/env python3
"""
Unit tests for the Executive Compliance Scorecard (P2c): framework_mappings.py
(curated SOC 2/NIST/ISO/COSO crosswalk) and db._aggregate_scorecard_rows
(pure aggregation, no DB connection needed — same testability reasoning as
pac_endpoints._parse_opa_bindings).

    pytest test_compliance_scorecard.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import db
import framework_mappings as fm
import pac_endpoints


# ── framework_mappings.py ───────────────────────────────────────────────────

def test_get_mapping_returns_none_for_unmapped_control():
    assert fm.get_mapping("NOT-A-REAL-CONTROL") is None


def test_get_mapping_returns_curated_dict_for_known_control():
    mapping = fm.get_mapping("DEVOPS-001")
    assert mapping is not None
    assert "CC6.1" in mapping["soc2_criteria"]
    assert mapping["coso_component"] == "Control Activities"


def test_every_mapping_has_all_four_framework_keys():
    """Keeps the curated dict internally consistent — every entry should be
    reviewable against all four frameworks, even if a future entry
    legitimately has an empty list for one of them."""
    required_keys = {"soc2_criteria", "nist_800_53", "iso_27001", "coso_component"}
    for control_id, mapping in fm.FRAMEWORK_MAPPINGS.items():
        assert required_keys <= set(mapping.keys()), f"{control_id} missing keys"


def test_devops_and_infra_rego_control_ids_all_have_a_framework_mapping():
    """Honesty check: every control_id the devops_monitoring/
    infrastructure_monitoring Rego modules actually define (via
    extract_control_ids_from_defaults, the same parser that seeds
    controls_catalog) should have a curated mapping — otherwise the
    scorecard silently under-reports as new Rego rules are added without a
    matching crosswalk entry."""
    controls = pac_endpoints.extract_control_ids_from_defaults()
    relevant = [c for c in controls if c["process"] in ("devops_monitoring", "infrastructure_monitoring")]
    assert relevant, "expected at least one DEVOPS-*/INFRA-* control from the Rego defaults"
    missing = [c["control_id"] for c in relevant if fm.get_mapping(c["control_id"]) is None]
    assert not missing, f"control_ids with no framework mapping: {missing}"


# ── db._aggregate_scorecard_rows ────────────────────────────────────────────

def test_aggregate_scorecard_rows_empty_input():
    assert db._aggregate_scorecard_rows([]) == []


def test_aggregate_scorecard_rows_groups_by_criterion():
    rows = [
        ("CC6.1", "DEVOPS-001", True, None),
        ("CC6.1", "DEVOPS-005", False, None),
        ("CC7.2", "DEVOPS-007", True, None),
    ]
    result = db._aggregate_scorecard_rows(rows)
    by_criterion = {c["criterion"]: c for c in result}
    assert by_criterion["CC6.1"]["total_controls"] == 2
    assert by_criterion["CC7.2"]["total_controls"] == 1


def test_aggregate_scorecard_rows_dedupes_same_control_appearing_twice():
    rows = [("CC6.1", "DEVOPS-001", True, None), ("CC6.1", "DEVOPS-001", True, None)]
    result = db._aggregate_scorecard_rows(rows)
    assert result[0]["total_controls"] == 1


def test_aggregate_scorecard_rows_last_test_passed_counts_as_verified():
    rows = [("CC6.1", "DEVOPS-001", True, None)]
    result = db._aggregate_scorecard_rows(rows)
    assert result[0]["verified_controls"] == 1


def test_aggregate_scorecard_rows_recent_fire_counts_as_verified_without_a_test():
    now = datetime.now(timezone.utc)
    rows = [("CC6.1", "DEVOPS-001", False, now - timedelta(days=5))]
    result = db._aggregate_scorecard_rows(rows, stale_days=30)
    assert result[0]["verified_controls"] == 1


def test_aggregate_scorecard_rows_stale_fire_does_not_count_as_verified():
    now = datetime.now(timezone.utc)
    rows = [("CC6.1", "DEVOPS-001", False, now - timedelta(days=90))]
    result = db._aggregate_scorecard_rows(rows, stale_days=30)
    assert result[0]["verified_controls"] == 0


def test_aggregate_scorecard_rows_mapped_but_never_verified_is_not_hidden():
    """The core honesty property: a control that's mapped (appears in the
    input rows at all) but has neither a passing test nor a recent fire must
    still show up with verified_controls=0, not be silently dropped."""
    rows = [("CC6.1", "DEVOPS-001", False, None), ("CC6.1", "DEVOPS-001", None, None)]
    result = db._aggregate_scorecard_rows(rows)
    assert result[0]["total_controls"] == 1
    assert result[0]["verified_controls"] == 0


def test_get_compliance_scorecard_rejects_unknown_framework():
    result = db.get_compliance_scorecard("not_a_real_framework")
    assert result["criteria"] == []
    assert "error" in result
