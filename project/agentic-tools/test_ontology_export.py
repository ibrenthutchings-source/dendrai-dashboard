"""
Tests for ontology_export.py's pure build_skos_graph() — no DB access, mirrors
risk_coverage_cube.py's build_icif_cube tests: fake rows in, assert the graph
out.
"""

from __future__ import annotations

from rdflib import Graph
from rdflib.namespace import SKOS

import ontology_export as oe


def _concept(id_, scheme, pref_label, *, alt_labels=None, definition=None,
             notation=None, broader_id=None, source="curated"):
    return {
        "id": id_, "scheme": scheme, "notation": notation, "pref_label": pref_label,
        "alt_labels": alt_labels or [], "definition": definition,
        "broader_id": broader_id, "source": source,
    }


def _relation(from_scheme, from_pref_label, to_scheme, to_pref_label, strm_type,
              *, strength=None, rationale=None, source="curated", id_=1):
    return {
        "id": id_, "from_scheme": from_scheme, "from_pref_label": from_pref_label,
        "to_scheme": to_scheme, "to_pref_label": to_pref_label,
        "strm_type": strm_type, "strength": strength, "rationale": rationale, "source": source,
    }


class TestBasicShape:
    def test_concept_gets_skos_concept_type_and_prefLabel(self):
        concepts = [_concept(1, "risk_category", "Cybersecurity", definition="Risk of breach.")]
        g = oe.build_skos_graph(concepts, [])
        subject = oe._concept_iri("risk_category", "Cybersecurity")
        assert (subject, SKOS.prefLabel, None) in g
        assert (subject, SKOS.definition, None) in g
        types = list(g.objects(subject, SKOS.inScheme))
        assert types == [oe._scheme_iri("risk_category")]

    def test_alt_labels_all_emitted(self):
        concepts = [_concept(1, "risk_category", "Supply", alt_labels=["Supply Chain Risk", "Vendor Risk"])]
        g = oe.build_skos_graph(concepts, [])
        subject = oe._concept_iri("risk_category", "Supply")
        alts = {str(o) for o in g.objects(subject, SKOS.altLabel)}
        assert alts == {"Supply Chain Risk", "Vendor Risk"}

    def test_scheme_gets_concept_scheme_triple(self):
        concepts = [_concept(1, "risk_category", "Supply")]
        g = oe.build_skos_graph(concepts, [])
        assert (oe._scheme_iri("risk_category"), None, SKOS.ConceptScheme) in g


class TestHierarchy:
    def test_broader_id_produces_skos_broader_and_narrower(self):
        concepts = [
            _concept(1, "coso_icif", "Control Activities"),
            _concept(2, "coso_icif", "Access Control Review", broader_id=1),
        ]
        g = oe.build_skos_graph(concepts, [])
        child = oe._concept_iri("coso_icif", "Access Control Review")
        parent = oe._concept_iri("coso_icif", "Control Activities")
        assert (child, SKOS.broader, parent) in g
        assert (parent, SKOS.narrower, child) in g

    def test_broader_reference_outside_requested_scheme_still_resolves(self):
        """A concept's parent in a different scheme is still emitted (with its
        own scheme triples) even when the export is scoped to the child's
        scheme only — otherwise skos:broader would dangle."""
        concepts = [
            _concept(1, "coso_erm", "Performance"),
            _concept(2, "sox_process", "Access Review", broader_id=1),
        ]
        g = oe.build_skos_graph(concepts, [], scheme="sox_process")
        child = oe._concept_iri("sox_process", "Access Review")
        parent = oe._concept_iri("coso_erm", "Performance")
        assert (child, SKOS.broader, parent) in g
        assert (parent, SKOS.prefLabel, None) in g  # parent concept itself got emitted


class TestSchemeScoping:
    def test_scheme_filter_excludes_other_schemes_concepts(self):
        concepts = [
            _concept(1, "risk_category", "Cybersecurity"),
            _concept(2, "enterprise_domain", "Cyber Security & Data Protection"),
        ]
        g = oe.build_skos_graph(concepts, [], scheme="risk_category")
        assert (oe._concept_iri("risk_category", "Cybersecurity"), SKOS.prefLabel, None) in g
        assert (oe._concept_iri("enterprise_domain", "Cyber Security & Data Protection"), SKOS.prefLabel, None) not in g

    def test_relation_touching_in_scope_concept_is_kept_even_if_other_side_out_of_scope(self):
        concepts = [
            _concept(1, "risk_category", "Cybersecurity"),
            _concept(2, "enterprise_domain", "Cyber Security & Data Protection"),
        ]
        relations = [_relation("enterprise_domain", "Cyber Security & Data Protection",
                                "risk_category", "Cybersecurity", "equal")]
        g = oe.build_skos_graph(concepts, relations, scheme="risk_category")
        assert len(list(g.triples((None, oe.STRM.equal, None)))) == 1


class TestStrmRelations:
    def test_each_strm_type_maps_to_its_own_predicate_never_a_skos_match(self):
        concepts = [_concept(1, "a", "X"), _concept(2, "b", "Y")]
        for strm_type in ("subset_of", "superset_of", "equal", "intersects_with", "no_relationship"):
            relations = [_relation("a", "X", "b", "Y", strm_type)]
            g = oe.build_skos_graph(concepts, relations)
            predicate = oe._STRM_PREDICATE[strm_type]
            assert (oe._concept_iri("a", "X"), predicate, oe._concept_iri("b", "Y")) in g

    def test_no_strm_relation_is_ever_emitted_as_a_skos_match_predicate(self):
        """The core misrepresentation guard: a curated, non-transitive STRM
        relation (e.g. intersects_with) must never be coerced into a SKOS
        mapping relation (broadMatch/closeMatch/exactMatch), which would
        assert a different and stronger claim than what was reviewed."""
        concepts = [_concept(1, "a", "X"), _concept(2, "b", "Y")]
        relations = [_relation("a", "X", "b", "Y", t) for t in
                     ("subset_of", "superset_of", "equal", "intersects_with", "no_relationship")]
        g = oe.build_skos_graph(concepts, relations)
        for bad_predicate in (SKOS.broadMatch, SKOS.closeMatch, SKOS.exactMatch, SKOS.narrowMatch, SKOS.relatedMatch):
            assert (None, bad_predicate, None) not in g

    def test_unknown_strm_type_is_skipped_not_guessed(self):
        concepts = [_concept(1, "a", "X"), _concept(2, "b", "Y")]
        relations = [_relation("a", "X", "b", "Y", "totally_unknown_type")]
        g = oe.build_skos_graph(concepts, relations)
        # No predicate at all should connect X and Y.
        assert list(g.predicates(oe._concept_iri("a", "X"), oe._concept_iri("b", "Y"))) == []

    def test_relation_metadata_reified_as_statement(self):
        concepts = [_concept(1, "a", "X"), _concept(2, "b", "Y")]
        relations = [_relation("a", "X", "b", "Y", "equal", strength=0.95, rationale="Same scope.")]
        g = oe.build_skos_graph(concepts, relations)
        from rdflib.namespace import RDF
        stmts = list(g.subjects(RDF.type, RDF.Statement))
        assert len(stmts) == 1
        strengths = list(g.objects(stmts[0], oe.STRM.strength))
        assert strengths and float(strengths[0]) == 0.95


class TestDeterminism:
    def test_output_is_byte_stable_across_two_runs_on_identical_input(self):
        concepts = [
            _concept(1, "risk_category", "Cybersecurity", alt_labels=["Cyber Risk"]),
            _concept(2, "enterprise_domain", "Cyber Security & Data Protection"),
        ]
        relations = [_relation("enterprise_domain", "Cyber Security & Data Protection",
                                "risk_category", "Cybersecurity", "equal", strength=0.95,
                                rationale="Same scope.")]
        ttl1 = oe.build_skos_graph(concepts, relations).serialize(format="turtle")
        ttl2 = oe.build_skos_graph(concepts, relations).serialize(format="turtle")
        assert ttl1 == ttl2

    def test_emitted_turtle_round_trips_to_the_same_triple_count(self):
        concepts = [
            _concept(1, "risk_category", "Cybersecurity"),
            _concept(2, "enterprise_domain", "Cyber Security & Data Protection"),
        ]
        relations = [_relation("enterprise_domain", "Cyber Security & Data Protection",
                                "risk_category", "Cybersecurity", "equal")]
        g = oe.build_skos_graph(concepts, relations)
        ttl = g.serialize(format="turtle")
        g2 = Graph()
        g2.parse(data=ttl, format="turtle")
        assert len(g2) == len(g)
