"""
Tests for ontology_seed.py's curated content and db.seed_ontology()'s
orchestration logic.

Two layers, matching the codebase's established split:
  - Pure content tests directly on ontology_seed.py's module-level data —
    no DB, no mocking.
  - Orchestration tests on db.seed_ontology() with db.upsert_concept /
    db.get_concept / db.upsert_concept_relation mocked as MagicMock instances
    passed into a single patch.object() call per target (never nested
    patch.object() calls on the same target — that silently shadows the
    outer mock, a bug this test suite has hit before elsewhere).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import db
import ontology_seed as seed

_STRM_TYPES = {"subset_of", "superset_of", "equal", "intersects_with", "no_relationship"}

# Verbatim from project/risk-engine.js:46 CATEGORY_IMPACT's keys — the
# vocabulary risk_category is meant to project. Duplicated here deliberately
# (not imported, JS can't be imported into a Python test) so a change to
# either side is caught as a test failure, not silent drift.
_EXPECTED_RISK_CATEGORIES = {
    "Revenue", "Operational", "Financial Reporting", "Supply", "Cybersecurity",
    "Trade Compliance", "ESG", "Compliance", "Legal", "Strategic",
}

# Verbatim labels from risk_register_endpoints.py's _keyword_domain().
_EXPECTED_ENTERPRISE_DOMAINS = {
    "Identity & Access Management", "Financial Reporting & Controls",
    "Cyber Security & Data Protection", "Third-Party & Vendor Risk",
    "Operational Resilience", "Regulatory & Compliance",
    "Technology & Change Management", "People & Organisational Risk",
    "Market & Economic Risk",
}


class TestSeedConceptContent:
    def test_risk_category_matches_risk_engine_category_impact(self):
        labels = {c["pref_label"] for c in seed.SEED_CONCEPTS["risk_category"]}
        assert labels == _EXPECTED_RISK_CATEGORIES

    def test_enterprise_domain_matches_keyword_domain_function(self):
        labels = {c["pref_label"] for c in seed.SEED_CONCEPTS["enterprise_domain"]}
        assert labels == _EXPECTED_ENTERPRISE_DOMAINS

    def test_enterprise_risk_fallback_is_not_seeded_as_a_domain(self):
        """_keyword_domain()'s fallback (`return category or "Enterprise Risk"`)
        is a fallback label, not a defined domain — seeding it would
        misrepresent an absence-of-match as a real concept."""
        labels = {c["pref_label"] for c in seed.SEED_CONCEPTS["enterprise_domain"]}
        assert "Enterprise Risk" not in labels

    def test_no_duplicate_pref_labels_within_a_scheme(self):
        for scheme, concepts in seed.SEED_CONCEPTS.items():
            labels = [c["pref_label"] for c in concepts]
            assert len(labels) == len(set(labels)), f"duplicate pref_label in {scheme}"

    def test_every_concept_has_a_definition(self):
        for scheme, concepts in seed.SEED_CONCEPTS.items():
            for c in concepts:
                assert c.get("definition"), f"{scheme}/{c['pref_label']} has no definition"

    def test_seed_order_covers_every_scheme_with_concepts(self):
        assert set(seed.SEED_ORDER) == set(seed.SEED_CONCEPTS.keys())


class TestSeedRelationContent:
    def test_every_relation_strm_type_is_one_of_the_five_nist_ir_8477_types(self):
        for rel in seed.SEED_RELATIONS:
            assert rel["strm_type"] in _STRM_TYPES

    def test_every_relation_references_a_concept_that_exists(self):
        known = {
            (scheme, c["pref_label"])
            for scheme, concepts in seed.SEED_CONCEPTS.items()
            for c in concepts
        }
        for rel in seed.SEED_RELATIONS:
            assert (rel["from_scheme"], rel["from_pref_label"]) in known, \
                f"unknown from-concept: {rel['from_scheme']}/{rel['from_pref_label']}"
            assert (rel["to_scheme"], rel["to_pref_label"]) in known, \
                f"unknown to-concept: {rel['to_scheme']}/{rel['to_pref_label']}"

    def test_no_relation_references_itself(self):
        for rel in seed.SEED_RELATIONS:
            same = (rel["from_scheme"], rel["from_pref_label"]) == (rel["to_scheme"], rel["to_pref_label"])
            assert not same, f"self-referential relation: {rel}"

    def test_market_and_economic_risk_intersects_strategic_not_equal(self):
        """Signed off: macro-driven risk overlaps Strategic but isn't the same
        concept — risk-engine.js/risks_as_code.py deliberately treat 'Macro'
        as a score modifier, not a risk category in its own right."""
        rel = next(
            r for r in seed.SEED_RELATIONS
            if r["from_pref_label"] == "Market & Economic Risk" and r["to_pref_label"] == "Strategic"
        )
        assert rel["strm_type"] == "intersects_with"

    def test_esg_has_no_seeded_enterprise_domain_relation(self):
        """Signed off: no enterprise_domain's keyword list covers ESG terms,
        so asserting a relation would be a guess, not a finding — an
        unreviewed gap, left absent rather than forced. (sox_risk_category's
        Macro->ESG no_relationship is a separate, deliberate assertion — a
        checked negative, not a guess — and is exempt from this check.)"""
        touches_esg_from_domain = [
            r for r in seed.SEED_RELATIONS
            if r["from_scheme"] == "enterprise_domain"
            and (r["from_pref_label"] == "ESG" or r["to_pref_label"] == "ESG")
        ]
        assert touches_esg_from_domain == []


class TestCosoErmHierarchy:
    def test_all_20_principles_present_with_broader_pointing_at_their_component(self):
        principles = [c for c in seed.SEED_CONCEPTS["coso_erm"] if c.get("notation", "").startswith("P")]
        assert len(principles) == 20
        components = {c["pref_label"] for c in seed.SEED_CONCEPTS["coso_erm"] if not c.get("notation")}
        assert len(components) == 5
        for p in principles:
            assert p["broader_scheme"] == "coso_erm"
            assert p["broader_pref_label"] in components

    def test_coso_icif_excludes_the_unmapped_fallback(self):
        """Same reasoning as enterprise_domain's excluded fallback: 'Unmapped'
        is the absence of a mapped control, not a real component."""
        labels = {c["pref_label"] for c in seed.SEED_CONCEPTS["coso_icif"]}
        assert "Unmapped" not in labels
        assert len(labels) == 5


class TestFrameworkCrosswalk:
    def test_crosswalk_relations_are_all_intersects_with(self):
        """This slice only asserts overlap, never a stronger claim (equal/
        subset_of) between framework codes — that would need per-code
        compliance review this slice doesn't carry out."""
        crosswalk = [
            r for r in seed.SEED_RELATIONS
            if r["from_scheme"] in ("soc2", "nist_800_53") and r["to_scheme"] in ("nist_800_53", "iso_27001")
        ]
        assert crosswalk
        assert all(r["strm_type"] == "intersects_with" for r in crosswalk)

    def test_every_framework_code_concept_has_notation_equal_to_pref_label(self):
        for scheme in ("soc2", "nist_800_53", "iso_27001"):
            for c in seed.SEED_CONCEPTS[scheme]:
                assert c["notation"] == c["pref_label"]


class TestSoxReconciliation:
    def test_macro_has_no_relationship_to_all_ten_risk_categories(self):
        macro_rels = [r for r in seed.SEED_RELATIONS if r["from_pref_label"] == "Macro" and r["from_scheme"] == "sox_risk_category"]
        assert len(macro_rels) == 10
        assert all(r["strm_type"] == "no_relationship" for r in macro_rels)
        assert {r["to_pref_label"] for r in macro_rels} == _EXPECTED_RISK_CATEGORIES

    def test_financial_intersects_both_financial_reporting_and_revenue(self):
        targets = {
            r["to_pref_label"] for r in seed.SEED_RELATIONS
            if r["from_scheme"] == "sox_risk_category" and r["from_pref_label"] == "Financial"
        }
        assert targets == {"Financial Reporting", "Revenue"}

    def test_regulatory_is_superset_of_compliance_trade_compliance_and_legal(self):
        rels = [
            r for r in seed.SEED_RELATIONS
            if r["from_scheme"] == "sox_risk_category" and r["from_pref_label"] == "Regulatory"
        ]
        assert {r["to_pref_label"] for r in rels} == {"Compliance", "Trade Compliance", "Legal"}
        assert all(r["strm_type"] == "superset_of" for r in rels)


class TestSeedOntologyOrchestration:
    """db.seed_ontology()'s control flow — concepts seeded before relations,
    relations skipped (not raised) when a referenced concept can't be found,
    and correct counts returned."""

    def _run_seed_ontology(self, *, upsert_concept, get_concept, upsert_concept_relation):
        """Single patch.object() per target, all as pre-built MagicMocks —
        the pattern this suite standardized on after a nested-patch bug
        elsewhere silently shadowed an outer mock."""
        with patch.object(db, "upsert_concept", upsert_concept), \
             patch.object(db, "get_concept", get_concept), \
             patch.object(db, "upsert_concept_relation", upsert_concept_relation):
            return db.seed_ontology()

    def test_seeds_all_concepts_and_relations_on_a_clean_run(self):
        next_id = iter(range(1, 1000))
        upsert_concept = MagicMock(side_effect=lambda *a, **k: next(next_id))
        upsert_concept_relation = MagicMock(return_value=1)
        get_concept = MagicMock(return_value=None)  # only consulted on cache miss

        result = self._run_seed_ontology(
            upsert_concept=upsert_concept, get_concept=get_concept,
            upsert_concept_relation=upsert_concept_relation,
        )

        expected_concept_count = sum(len(v) for v in seed.SEED_CONCEPTS.values())
        assert result["concepts_upserted"] == expected_concept_count
        assert upsert_concept.call_count == expected_concept_count
        assert result["relations_upserted"] == len(seed.SEED_RELATIONS)
        assert upsert_concept_relation.call_count == len(seed.SEED_RELATIONS)

    def test_rerun_is_idempotent_same_counts(self):
        """seed_ontology is safe to re-run — same input, same output counts,
        every time (upsert_concept/upsert_concept_relation themselves are
        the idempotent ON CONFLICT DO UPDATE layer; this checks the
        orchestration doesn't skip or double-count on a second pass)."""
        next_id = iter(list(range(1, 1000)) * 2)
        upsert_concept = MagicMock(side_effect=lambda *a, **k: next(next_id))
        upsert_concept_relation = MagicMock(return_value=1)
        get_concept = MagicMock(return_value=None)

        first = self._run_seed_ontology(
            upsert_concept=upsert_concept, get_concept=get_concept,
            upsert_concept_relation=upsert_concept_relation,
        )
        second = self._run_seed_ontology(
            upsert_concept=upsert_concept, get_concept=get_concept,
            upsert_concept_relation=upsert_concept_relation,
        )
        assert first == second

    def test_relation_skipped_not_raised_when_concept_upsert_failed(self):
        """If upsert_concept returned None for some concept (e.g. a DB error
        _run() swallowed), the dependent relation is skipped with a warning,
        never raises and never partially writes a relation with a null id."""
        upsert_concept = MagicMock(return_value=None)
        get_concept = MagicMock(return_value=None)
        upsert_concept_relation = MagicMock(return_value=1)

        result = self._run_seed_ontology(
            upsert_concept=upsert_concept, get_concept=get_concept,
            upsert_concept_relation=upsert_concept_relation,
        )
        assert result["relations_upserted"] == 0
        upsert_concept_relation.assert_not_called()
