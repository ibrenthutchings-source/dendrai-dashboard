"""
Tests for risk_coverage_cube.py's pure build_cube() aggregation — no DB
access, mirrors the reasoning in test_edgar_segments.py / db.py's own
_aggregate_scorecard_rows tests: fake rows in, assert the grid out.
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
        cube = cube_mod.build_cube([], [], {}, {})
        assert cube["total_risks"] == 0
        assert all(c["state"] == "empty" for c in cube["cells"])
        assert len(cube["cells"]) == len(cube_mod.OBJECTIVE_CATEGORIES) * len(cube_mod.COSO_COMPONENTS)


class TestCoverageWithoutMapping:
    def test_risk_with_no_control_mapping_is_mapped_unverified(self):
        risks = [_risk("R1", "Cybersecurity", score=18.0, rag="R")]
        cube = cube_mod.build_cube(risks, [], {}, {})
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Performance")
        assert cell["state"] == "mapped_unverified"
        assert cell["risk_count"] == 1
        assert cell["worst_rag"] == "R"
        assert cell["max_score"] == 18.0
        assert cell["risk_refs"] == ["R1"]

    def test_unmapped_category_lands_in_unmapped_unmapped_cell(self):
        risks = [_risk("R2", "SomeFutureCategory")]
        cube = cube_mod.build_cube(risks, [], {}, {})
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Unmapped" and c["coso_component"] == "Unmapped")
        assert cell["risk_count"] == 1
        assert cube["unmapped_risk_count"] == 1


class TestVerifiedAssurance:
    def test_control_with_last_test_passed_marks_cell_verified(self):
        risks = [_risk("R1", "Cybersecurity")]
        mappings = [{"risk_ref": "R1", "control_ref": "CTRL-1", "mapping_type": "auto", "generate_code": ""}]
        library_by_ref = {"CTRL-1": {"ref": "CTRL-1", "pac_control_id": "PAC-1"}}
        catalog_by_id = {"PAC-1": {"control_id": "PAC-1", "last_test_passed": True, "last_fired_at": None}}
        cube = cube_mod.build_cube(risks, mappings, library_by_ref, catalog_by_id)
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Performance")
        assert cell["state"] == "verified"
        assert cell["mapped_control_count"] == 1
        assert cell["verified_control_count"] == 1

    def test_stale_fired_at_does_not_count_as_verified(self):
        risks = [_risk("R1", "Cybersecurity")]
        mappings = [{"risk_ref": "R1", "control_ref": "CTRL-1", "mapping_type": "auto", "generate_code": ""}]
        library_by_ref = {"CTRL-1": {"ref": "CTRL-1", "pac_control_id": "PAC-1"}}
        stale = _iso(datetime.now(timezone.utc) - timedelta(days=90))
        catalog_by_id = {"PAC-1": {"control_id": "PAC-1", "last_test_passed": None, "last_fired_at": stale}}
        cube = cube_mod.build_cube(risks, mappings, library_by_ref, catalog_by_id)
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Performance")
        assert cell["state"] == "mapped_unverified"
        assert cell["mapped_control_count"] == 1
        assert cell["verified_control_count"] == 0

    def test_recent_fired_at_counts_as_verified(self):
        risks = [_risk("R1", "Cybersecurity")]
        mappings = [{"risk_ref": "R1", "control_ref": "CTRL-1", "mapping_type": "auto", "generate_code": ""}]
        library_by_ref = {"CTRL-1": {"ref": "CTRL-1", "pac_control_id": "PAC-1"}}
        recent = _iso(datetime.now(timezone.utc) - timedelta(days=2))
        catalog_by_id = {"PAC-1": {"control_id": "PAC-1", "last_test_passed": None, "last_fired_at": recent}}
        cube = cube_mod.build_cube(risks, mappings, library_by_ref, catalog_by_id)
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Performance")
        assert cell["state"] == "verified"

    def test_mapping_to_control_not_in_catalog_stays_unverified(self):
        risks = [_risk("R1", "Cybersecurity")]
        mappings = [{"risk_ref": "R1", "control_ref": "CTRL-GHOST", "mapping_type": "manual", "generate_code": ""}]
        cube = cube_mod.build_cube(risks, mappings, {}, {})
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Performance")
        assert cell["state"] == "mapped_unverified"
        assert cell["mapped_control_count"] == 1
        assert cell["verified_control_count"] == 0


class TestAggregationAcrossRisks:
    def test_worst_rag_and_control_env_mix_aggregate_across_multiple_risks_same_cell(self):
        risks = [
            _risk("R1", "Revenue", score=12.0, rag="G", control_env="STRONG"),
            _risk("R2", "Operational", score=20.0, rag="R", control_env="WEAK"),
        ]
        cube = cube_mod.build_cube(risks, [], {}, {})
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Performance")
        assert cell["risk_count"] == 2
        assert cell["worst_rag"] == "R"
        assert cell["max_score"] == 20.0
        assert cell["control_env_mix"] == {"WEAK": 1, "ADEQUATE": 0, "STRONG": 1}
        assert set(cell["risk_refs"]) == {"R1", "R2"}

    def test_never_never_merges_inferred_ce_into_verified_state(self):
        """control_env (the loop's inferred CE) must not affect `state` — only
        real controls_catalog assurance evidence can, per Finding #3."""
        risks = [_risk("R1", "Cybersecurity", control_env="STRONG")]
        cube = cube_mod.build_cube(risks, [], {}, {})
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Performance")
        assert cell["control_env_mix"]["STRONG"] == 1
        assert cell["state"] == "mapped_unverified"


class TestEntityAxis:
    """The Z axis — Phase 3 (segment_risk_tool.py) tags real risks with a
    segment_type/segment_name; a risk with no tag is Consolidated. This is
    the real join, not a display-only placeholder."""

    def test_untagged_risk_lands_in_consolidated_entity(self):
        risks = [_risk("R1", "Cybersecurity")]
        cube = cube_mod.build_cube(risks, [], {}, {})
        assert cube["entities"] == ["Consolidated"]
        cell = next(c for c in cube["cells"]
                    if c["objective_category"] == "Operations" and c["coso_component"] == "Performance"
                    and c["entity"] == "Consolidated")
        assert cell["risk_count"] == 1

    def test_segment_tagged_risk_gets_its_own_entity(self):
        risks = [
            _risk("R1", "Cybersecurity"),
            _risk("SGG01C", "Revenue", segment_type="geography", segment_name="United States"),
        ]
        cube = cube_mod.build_cube(risks, [], {}, {})
        assert cube["entities"] == ["Consolidated", "United States"]

        us_cell = next(c for c in cube["cells"]
                       if c["entity"] == "United States"
                       and c["objective_category"] == "Operations" and c["coso_component"] == "Performance")
        assert us_cell["risk_count"] == 1
        assert us_cell["risk_refs"] == ["SGG01C"]

        consolidated_cell = next(c for c in cube["cells"]
                                  if c["entity"] == "Consolidated"
                                  and c["objective_category"] == "Operations" and c["coso_component"] == "Performance")
        assert consolidated_cell["risk_count"] == 1
        assert consolidated_cell["risk_refs"] == ["R1"]

    def test_consolidated_entity_always_present_even_with_zero_risks(self):
        cube = cube_mod.build_cube([], [], {}, {})
        assert cube["entities"] == ["Consolidated"]

    def test_multiple_segments_each_get_a_distinct_entity(self):
        risks = [
            _risk("SGG01C", "Revenue", segment_type="geography", segment_name="United States"),
            _risk("SGG02C", "Revenue", segment_type="geography", segment_name="EMEA"),
        ]
        cube = cube_mod.build_cube(risks, [], {}, {})
        assert cube["entities"] == ["Consolidated", "EMEA", "United States"]

    def test_grid_size_scales_with_entity_count(self):
        risks = [
            _risk("R1", "Cybersecurity"),
            _risk("SGG01C", "Revenue", segment_type="geography", segment_name="United States"),
        ]
        cube = cube_mod.build_cube(risks, [], {}, {})
        assert len(cube["cells"]) == len(cube_mod.OBJECTIVE_CATEGORIES) * len(cube_mod.COSO_COMPONENTS) * 2
