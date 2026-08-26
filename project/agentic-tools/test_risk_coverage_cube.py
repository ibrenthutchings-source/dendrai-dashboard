"""
Tests for risk_coverage_cube.py's pure aggregation functions — no DB access,
mirrors the reasoning in test_edgar_segments.py / db.py's own
_aggregate_scorecard_rows tests: fake rows in, assert the grid out.

Covers build_icif_cube (the real COSO Cube, corrected 2026-08-26 — see that
module's docstring) and build_erm_evidence (COSO ERM 2017, not a cube).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import risk_coverage_cube as cube_mod


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _risk(risk_ref, category, score=10.0, rag="G", velocity=0, control_env="ADEQUATE",
          segment_type=None, segment_name=None):
    return {
        "risk_ref": risk_ref, "id": risk_ref, "category": category,
        "score": score, "rag": rag, "velocity": velocity, "control_env": control_env,
        "segment_type": segment_type, "segment_name": segment_name,
    }


class TestEmptyCells:
    def test_no_risks_all_cells_empty(self):
        cube = cube_mod.build_icif_cube([], [], {}, {})
        assert cube["total_risks"] == 0
        assert all(c["state"] == "empty" for c in cube["cells"])
        assert len(cube["cells"]) == len(cube_mod.ICIF_OBJECTIVES) * len(cube_mod.ICIF_COMPONENTS)
        assert cube["framework"] == "icif_2013"


class TestComponentAxisIsControlDriven:
    """The key structural fix: the component axis (X) comes from the mapped
    CONTROL's icif_component, never from the risk's category — the original
    bug keyed it off the risk, which made 2 of 5 columns unreachable."""

    def test_risk_with_no_control_mapping_lands_in_unmapped_component(self):
        risks = [_risk("R1", "Cybersecurity", score=18.0, rag="R")]
        cube = cube_mod.build_icif_cube(risks, [], {}, {})
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Unmapped")
        assert cell["state"] == "mapped_unverified"
        assert cell["risk_count"] == 1
        assert cell["worst_rag"] == "R"
        assert cell["max_score"] == 18.0
        assert cell["risk_refs"] == ["R1"]
        assert cube["unmapped_risk_count"] == 1

    def test_unmapped_category_lands_in_unmapped_objective_row(self):
        risks = [_risk("R2", "SomeFutureCategory")]
        cube = cube_mod.build_icif_cube(risks, [], {}, {})
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Unmapped" and c["coso_component"] == "Unmapped")
        assert cell["risk_count"] == 1

    def test_every_icif_component_is_reachable(self):
        """Anti-regression for the original bug: a risk mapped to a control
        tagged with EACH real IC-IF component actually lands in that
        component's column — none are structurally dead."""
        risks = [_risk(f"R{i}", "Cybersecurity") for i in range(5)]
        components = [
            "Control Environment", "Risk Assessment", "Control Activities",
            "Information & Communication", "Monitoring Activities",
        ]
        mappings = [{"risk_ref": f"R{i}", "control_ref": f"CTRL-{i}"} for i in range(5)]
        library_by_ref = {f"CTRL-{i}": {"ref": f"CTRL-{i}", "pac_control_id": f"PAC-{i}"} for i in range(5)}
        catalog_by_id = {
            f"PAC-{i}": {"control_id": f"PAC-{i}", "icif_component": components[i], "last_test_passed": True}
            for i in range(5)
        }
        cube = cube_mod.build_icif_cube(risks, mappings, library_by_ref, catalog_by_id)
        for i, component in enumerate(components):
            cell = next(c for c in cube["cells"]
                        if c["objective_category"] == "Operations" and c["coso_component"] == component)
            assert cell["state"] == "verified", f"{component} should be reachable and verified"
            assert cell["risk_refs"] == [f"R{i}"]

    def test_risk_mapped_to_controls_in_two_components_lands_in_both_cells(self):
        risks = [_risk("R1", "Cybersecurity")]
        mappings = [
            {"risk_ref": "R1", "control_ref": "CTRL-A"},
            {"risk_ref": "R1", "control_ref": "CTRL-B"},
        ]
        library_by_ref = {
            "CTRL-A": {"ref": "CTRL-A", "pac_control_id": "PAC-A"},
            "CTRL-B": {"ref": "CTRL-B", "pac_control_id": "PAC-B"},
        }
        catalog_by_id = {
            "PAC-A": {"control_id": "PAC-A", "icif_component": "Control Activities"},
            "PAC-B": {"control_id": "PAC-B", "icif_component": "Monitoring Activities"},
        }
        cube = cube_mod.build_icif_cube(risks, mappings, library_by_ref, catalog_by_id)
        ca_cell = next(c for c in cube["cells"]
                       if c["objective_category"] == "Operations" and c["coso_component"] == "Control Activities")
        ma_cell = next(c for c in cube["cells"]
                       if c["objective_category"] == "Operations" and c["coso_component"] == "Monitoring Activities")
        assert ca_cell["risk_refs"] == ["R1"]
        assert ma_cell["risk_refs"] == ["R1"]
        # One risk, two cells — risk_count summed across cells legitimately
        # exceeds total_risks, same non-dedup caveat as mapped_control_count.
        assert cube["total_risks"] == 1


class TestStrategicOutOfIcifScope:
    """IC-IF has no 'Strategic' objective — never a 4th row (that would
    re-commit the ERM-2004-labelled-as-IC-IF error)."""

    def test_strategic_risk_creates_no_cell_and_is_counted_separately(self):
        risks = [_risk("R1", "Strategic")]
        cube = cube_mod.build_icif_cube(risks, [], {}, {})
        assert cube["out_of_icif_scope_risk_count"] == 1
        assert all(c["risk_count"] == 0 for c in cube["cells"])
        assert "Strategic" not in cube["objective_categories"]

    def test_strategic_and_in_scope_risks_mixed(self):
        risks = [_risk("R1", "Strategic"), _risk("R2", "Revenue")]
        cube = cube_mod.build_icif_cube(risks, [], {}, {})
        assert cube["out_of_icif_scope_risk_count"] == 1
        assert cube["total_risks"] == 2
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Unmapped")
        assert cell["risk_refs"] == ["R2"]


class TestVerifiedAssurance:
    def test_control_with_last_test_passed_marks_cell_verified(self):
        risks = [_risk("R1", "Cybersecurity")]
        mappings = [{"risk_ref": "R1", "control_ref": "CTRL-1", "mapping_type": "auto", "generate_code": ""}]
        library_by_ref = {"CTRL-1": {"ref": "CTRL-1", "pac_control_id": "PAC-1"}}
        catalog_by_id = {"PAC-1": {"control_id": "PAC-1", "icif_component": "Control Activities",
                                    "last_test_passed": True, "last_fired_at": None}}
        cube = cube_mod.build_icif_cube(risks, mappings, library_by_ref, catalog_by_id)
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Control Activities")
        assert cell["state"] == "verified"
        assert cell["mapped_control_count"] == 1
        assert cell["verified_control_count"] == 1

    def test_stale_fired_at_does_not_count_as_verified(self):
        risks = [_risk("R1", "Cybersecurity")]
        mappings = [{"risk_ref": "R1", "control_ref": "CTRL-1", "mapping_type": "auto", "generate_code": ""}]
        library_by_ref = {"CTRL-1": {"ref": "CTRL-1", "pac_control_id": "PAC-1"}}
        stale = _iso(datetime.now(timezone.utc) - timedelta(days=90))
        catalog_by_id = {"PAC-1": {"control_id": "PAC-1", "icif_component": "Control Activities",
                                    "last_test_passed": None, "last_fired_at": stale}}
        cube = cube_mod.build_icif_cube(risks, mappings, library_by_ref, catalog_by_id)
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Control Activities")
        assert cell["state"] == "mapped_unverified"
        assert cell["mapped_control_count"] == 1
        assert cell["verified_control_count"] == 0

    def test_recent_fired_at_counts_as_verified(self):
        risks = [_risk("R1", "Cybersecurity")]
        mappings = [{"risk_ref": "R1", "control_ref": "CTRL-1", "mapping_type": "auto", "generate_code": ""}]
        library_by_ref = {"CTRL-1": {"ref": "CTRL-1", "pac_control_id": "PAC-1"}}
        recent = _iso(datetime.now(timezone.utc) - timedelta(days=2))
        catalog_by_id = {"PAC-1": {"control_id": "PAC-1", "icif_component": "Control Activities",
                                    "last_test_passed": None, "last_fired_at": recent}}
        cube = cube_mod.build_icif_cube(risks, mappings, library_by_ref, catalog_by_id)
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Control Activities")
        assert cell["state"] == "verified"

    def test_mapping_to_control_not_in_catalog_stays_unverified_and_unmapped(self):
        risks = [_risk("R1", "Cybersecurity")]
        mappings = [{"risk_ref": "R1", "control_ref": "CTRL-GHOST", "mapping_type": "manual", "generate_code": ""}]
        cube = cube_mod.build_icif_cube(risks, mappings, {}, {})
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Unmapped")
        assert cell["state"] == "mapped_unverified"
        assert cell["mapped_control_count"] == 1
        assert cell["verified_control_count"] == 0

    def test_control_env_strong_never_produces_verified(self):
        """control_env (the loop's inferred CE) must not affect `state` — only
        real controls_catalog assurance evidence can."""
        risks = [_risk("R1", "Cybersecurity", control_env="STRONG")]
        cube = cube_mod.build_icif_cube(risks, [], {}, {})
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Unmapped")
        assert cell["control_env_mix"]["STRONG"] == 1
        assert cell["state"] == "mapped_unverified"


class TestAggregationAcrossRisks:
    def test_worst_rag_and_control_env_mix_aggregate_across_multiple_risks_same_cell(self):
        risks = [
            _risk("R1", "Revenue", score=12.0, rag="G", control_env="STRONG"),
            _risk("R2", "Operational", score=20.0, rag="R", control_env="WEAK"),
        ]
        cube = cube_mod.build_icif_cube(risks, [], {}, {})
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Unmapped")
        assert cell["risk_count"] == 2
        assert cell["worst_rag"] == "R"
        assert cell["max_score"] == 20.0
        assert cell["control_env_mix"] == {"WEAK": 1, "ADEQUATE": 0, "STRONG": 1}
        assert set(cell["risk_refs"]) == {"R1", "R2"}


class TestEntityAxis:
    """The Z axis — Phase 3 (segment_risk_tool.py) tags real risks with a
    segment_type/segment_name; a risk with no tag is Consolidated. This is
    the real join, not a display-only placeholder. Division/Function (IC-IF's
    other two org-structure levels) are omitted entirely — no data source."""

    def test_untagged_risk_lands_in_consolidated_entity(self):
        risks = [_risk("R1", "Cybersecurity")]
        cube = cube_mod.build_icif_cube(risks, [], {}, {})
        assert cube["entities"] == ["Consolidated"]
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Unmapped"
                    and c["entity"] == "Consolidated")
        assert cell["risk_count"] == 1

    def test_segment_tagged_risk_gets_its_own_entity(self):
        risks = [
            _risk("R1", "Cybersecurity"),
            _risk("SGG01C", "Revenue", segment_type="geography", segment_name="United States"),
        ]
        cube = cube_mod.build_icif_cube(risks, [], {}, {})
        assert cube["entities"] == ["Consolidated", "United States"]

        us_cell = next(c for c in cube["cells"]
                       if c["entity"] == "United States"
                       and c["objective_category"] == "Operations" and c["coso_component"] == "Unmapped")
        assert us_cell["risk_count"] == 1
        assert us_cell["risk_refs"] == ["SGG01C"]

        consolidated_cell = next(c for c in cube["cells"]
                                  if c["entity"] == "Consolidated"
                                  and c["objective_category"] == "Operations" and c["coso_component"] == "Unmapped")
        assert consolidated_cell["risk_count"] == 1
        assert consolidated_cell["risk_refs"] == ["R1"]

    def test_consolidated_entity_always_present_even_with_zero_risks(self):
        cube = cube_mod.build_icif_cube([], [], {}, {})
        assert cube["entities"] == ["Consolidated"]

    def test_multiple_segments_each_get_a_distinct_entity(self):
        risks = [
            _risk("SGG01C", "Revenue", segment_type="geography", segment_name="United States"),
            _risk("SGG02C", "Revenue", segment_type="geography", segment_name="EMEA"),
        ]
        cube = cube_mod.build_icif_cube(risks, [], {}, {})
        assert cube["entities"] == ["Consolidated", "EMEA", "United States"]

    def test_grid_size_scales_with_entity_count(self):
        risks = [
            _risk("R1", "Cybersecurity"),
            _risk("SGG01C", "Revenue", segment_type="geography", segment_name="United States"),
        ]
        cube = cube_mod.build_icif_cube(risks, [], {}, {})
        assert len(cube["cells"]) == len(cube_mod.ICIF_OBJECTIVES) * len(cube_mod.ICIF_COMPONENTS) * 2

    def test_division_and_function_never_appear_and_are_disclosed_as_omitted(self):
        risks = [
            _risk("R1", "Cybersecurity"),
            _risk("SGG01C", "Revenue", segment_type="business_segment", segment_name="Power Solutions"),
        ]
        cube = cube_mod.build_icif_cube(risks, [], {}, {})
        assert "Division" not in cube["entities"]
        assert "Function" not in cube["entities"]
        omitted_levels = {o["level"] for o in cube["omitted_z_levels"]}
        assert omitted_levels == {"Division", "Function"}
        assert all(o.get("reason") for o in cube["omitted_z_levels"])


class TestBuildErmEvidence:
    """COSO ERM 2017 — not a cube; a component x principle conformance list."""

    def test_no_evidence_counts_all_sourced_principles_are_no_evidence(self):
        result = cube_mod.build_erm_evidence({})
        assert result["framework"] == "erm_2017"
        assert result["total_principles"] == 20
        # Every principle with a real evidence source but a 0 count this run.
        sourced = [p for c in result["components"] for p in c["principles"] if p["evidence_source"] is not None]
        assert all(p["state"] == "no_evidence" for p in sourced)

    def test_principles_with_no_evidence_source_are_no_source_never_evidenced(self):
        # Even a huge evidence_counts dict must never flip a no-source
        # principle to evidenced — no_source means no artifact exists at all.
        huge_counts = {f"key_{i}": 999 for i in range(50)}
        result = cube_mod.build_erm_evidence(huge_counts)
        no_source = [p for c in result["components"] for p in c["principles"] if p["evidence_source"] is None]
        assert len(no_source) == result["no_source_count"]
        assert all(p["state"] == "no_source" for p in no_source)

    def test_evidenced_when_count_positive(self):
        result = cube_mod.build_erm_evidence({"risk_register": 5, "gate_approvals": 1})
        p10 = next(p for c in result["components"] for p in c["principles"] if p["number"] == 10)
        assert p10["state"] == "evidenced"
        assert p10["count"] == 5
        p1 = next(p for c in result["components"] for p in c["principles"] if p["number"] == 1)
        assert p1["state"] == "evidenced"

    def test_principles_are_nested_not_a_cross_product(self):
        """P7 exists only under its own component — this is a flat
        conformance list, never a cube, so there's no cross-product to check
        against other components."""
        result = cube_mod.build_erm_evidence({})
        strategy_comp = next(c for c in result["components"] if c["component"] == "Strategy & Objective-Setting")
        assert any(p["number"] == 7 for p in strategy_comp["principles"])
        for comp in result["components"]:
            if comp["component"] != "Strategy & Objective-Setting":
                assert not any(p["number"] == 7 for p in comp["principles"])

    def test_exactly_five_principles_have_no_source(self):
        """Signed off 2026-08-26: P2-P5 (board structure/culture/values/HR)
        and P9 (business objectives, distinct from audit objectives) have no
        persisted artifact anywhere in this app."""
        result = cube_mod.build_erm_evidence({})
        assert result["no_source_count"] == 5
        no_source_numbers = {p["number"] for c in result["components"] for p in c["principles"]
                              if p["state"] == "no_source"}
        assert no_source_numbers == {2, 3, 4, 5, 9}
