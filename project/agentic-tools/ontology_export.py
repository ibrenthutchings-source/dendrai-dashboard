#!/usr/bin/env python3
"""
SKOS/Turtle export of the concept layer (db.py's concepts/concept_relations).

Not a runtime dependency of anything else in the app — this is an
interoperability escape hatch, not a triplestore. Nothing here does OWL
reasoning or SPARQL; it only serializes what's already in Postgres into a
standard, external-tool-readable format (any SKOS-aware tool, a manual
reviewer, or a future migration to a real triplestore if that's ever
justified).

Router prefix: /ontology
    GET /ontology/export.ttl[?scheme=risk_category]   Turtle export

Split into a pure graph-building function (build_skos_graph — unit-testable
with fake rows, no DB) and a thin DB-fetching wrapper (export_skos), same
split as risk_coverage_cube.py's build_icif_cube/get_icif_cube.
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, RDF, SKOS

router = APIRouter(prefix="/ontology")

BASE_IRI = "https://dendrai.ai/ontology/"
DENDRAI = Namespace(BASE_IRI)
# NIST IR 8477's five STRM relation types get their OWN predicate namespace —
# deliberately NOT skos:broadMatch/closeMatch/exactMatch. A curated crosswalk
# relation (e.g. "intersects_with", which is NOT transitive) is not the same
# claim as a SKOS mapping relation, and coercing one into the other would
# misrepresent the data the same way this codebase's COSO-framework
# mislabelling once did (ERM 2017 components rendered as an ERM 2004 cube).
STRM = Namespace(f"{BASE_IRI}strm#")

_STRM_PREDICATE = {
    "subset_of": STRM.subsetOf,
    "superset_of": STRM.supersetOf,
    "equal": STRM.equal,
    "intersects_with": STRM.intersectsWith,
    "no_relationship": STRM.noRelationship,
}


def _slug(text: str) -> str:
    """Deterministic, human-legible IRI fragment from a label — lowercase,
    non-alphanumerics collapsed to single hyphens. Concept/relation IRIs are
    minted from (scheme, pref_label) content, never from the DB's numeric
    row id, so the same concept produces the same IRI across environments
    and the exported Turtle is stable enough to commit and diff in review."""
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    return s.strip("-") or "concept"


def _concept_iri(scheme: str, pref_label: str) -> URIRef:
    return DENDRAI[f"concept/{_slug(scheme)}/{_slug(pref_label)}"]


def _scheme_iri(scheme: str) -> URIRef:
    return DENDRAI[f"scheme/{_slug(scheme)}"]


def build_skos_graph(concepts: list, relations: list, *, scheme: Optional[str] = None) -> Graph:
    """Pure — no DB access. concepts: db.list_concepts()'s shape (a superset
    covering every scheme, so a concept's broader_id can resolve even when
    `scheme` narrows which concepts actually get emitted). relations:
    db.list_concept_relations()'s shape (denormalized scheme/pref_label on
    both endpoints already, so no id lookup is needed here).

    When `scheme` is given, only concepts in that scheme (and relations
    touching at least one concept in that scheme) are emitted — but a
    skos:broader reference to a concept in a DIFFERENT scheme is still
    resolved and included, so the exported graph never has a dangling
    skos:broader pointing at nothing.
    """
    g = Graph()
    g.bind("skos", SKOS)
    g.bind("dendrai", DENDRAI)
    g.bind("strm", STRM)
    g.bind("dcterms", DCTERMS)

    by_id = {c["id"]: c for c in concepts}
    schemes_seen: set = set()

    in_scope = [c for c in concepts if scheme is None or c["scheme"] == scheme]
    referenced_broader_ids = {
        c["broader_id"] for c in in_scope if c.get("broader_id") is not None
    }

    def _emit_concept(c: dict) -> None:
        subject = _concept_iri(c["scheme"], c["pref_label"])
        g.add((subject, RDF.type, SKOS.Concept))
        g.add((subject, SKOS.prefLabel, Literal(c["pref_label"])))
        for alt in c.get("alt_labels") or []:
            g.add((subject, SKOS.altLabel, Literal(alt)))
        if c.get("definition"):
            g.add((subject, SKOS.definition, Literal(c["definition"])))
        if c.get("notation"):
            g.add((subject, SKOS.notation, Literal(c["notation"])))
        g.add((subject, SKOS.inScheme, _scheme_iri(c["scheme"])))
        schemes_seen.add(c["scheme"])
        if c.get("broader_id") is not None and c["broader_id"] in by_id:
            parent = by_id[c["broader_id"]]
            g.add((subject, SKOS.broader, _concept_iri(parent["scheme"], parent["pref_label"])))
            g.add((_concept_iri(parent["scheme"], parent["pref_label"]), SKOS.narrower, subject))

    for c in sorted(in_scope, key=lambda c: (c["scheme"], c["pref_label"])):
        _emit_concept(c)
    # Emit referenced-but-out-of-scope parents too, so skos:broader never
    # dangles — but without duplicating anything already emitted above.
    for parent_id in sorted(referenced_broader_ids):
        parent = by_id.get(parent_id)
        if parent and parent["scheme"] != scheme:
            _emit_concept(parent)

    for s in sorted(schemes_seen):
        g.add((_scheme_iri(s), RDF.type, SKOS.ConceptScheme))
        g.add((_scheme_iri(s), SKOS.prefLabel, Literal(s)))

    in_scope_labels = {(c["scheme"], c["pref_label"]) for c in in_scope}
    for rel in sorted(relations, key=lambda r: (
        r["from_scheme"], r["from_pref_label"], r["strm_type"], r["to_scheme"], r["to_pref_label"],
    )):
        if scheme is not None and (rel["from_scheme"], rel["from_pref_label"]) not in in_scope_labels \
           and (rel["to_scheme"], rel["to_pref_label"]) not in in_scope_labels:
            continue
        predicate = _STRM_PREDICATE.get(rel["strm_type"])
        if predicate is None:
            continue  # unknown strm_type — never guess a predicate for it
        from_iri = _concept_iri(rel["from_scheme"], rel["from_pref_label"])
        to_iri = _concept_iri(rel["to_scheme"], rel["to_pref_label"])
        g.add((from_iri, predicate, to_iri))

        # strength/rationale/source are relation-level metadata that don't
        # fit as attributes of a plain triple — reify via a blank node
        # (deterministic per relation, since rdflib's Turtle serializer
        # numbers blank nodes by first-seen order and we always add
        # relations in the same sorted order above).
        stmt = BNode()
        g.add((stmt, RDF.type, RDF.Statement))
        g.add((stmt, RDF.subject, from_iri))
        g.add((stmt, RDF.predicate, predicate))
        g.add((stmt, RDF.object, to_iri))
        if rel.get("strength") is not None:
            g.add((stmt, STRM.strength, Literal(rel["strength"])))
        if rel.get("rationale"):
            g.add((stmt, DCTERMS.description, Literal(rel["rationale"])))
        if rel.get("source"):
            g.add((stmt, DCTERMS.provenance, Literal(rel["source"])))

    return g


def export_skos(scheme: Optional[str] = None) -> str:
    import db

    concepts = db.list_concepts()  # always fetch ALL schemes — see build_skos_graph's docstring
    relations = db.list_concept_relations()
    graph = build_skos_graph(concepts, relations, scheme=scheme)
    return graph.serialize(format="turtle")


@router.get("/export.ttl", response_class=PlainTextResponse)
def export_ttl_endpoint(scheme: Optional[str] = Query(default=None)):
    """SKOS/Turtle export of the concept layer, optionally scoped to one scheme."""
    return export_skos(scheme)
