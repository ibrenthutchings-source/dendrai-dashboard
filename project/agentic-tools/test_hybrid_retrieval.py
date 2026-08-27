"""
Tests for db.py's Stage 4 hybrid retrieval: the pure RRF fusion / graph
rerank functions (_rrf_fuse, _graph_rerank_factor), and
get_relevant_context_hybrid's orchestration with every DB call mocked.

DB calls mocked as MagicMock instances passed into a single patch.object()
per target — this suite's standard pattern, adopted after a nested-patch bug
elsewhere silently shadowed an outer mock.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import db


def _row(source_table="edgar_risk_factor_filings", source_id=1, chunk_index=0, **extra):
    row = {"source_table": source_table, "source_id": source_id, "content_type": "risk_factor",
           "model": "m", "chunk_index": chunk_index, "text_snippet": "text", "created_at": None}
    row.update(extra)
    return row


class TestRrfFuse:
    def test_dense_only_row_gets_rank_based_score(self):
        fused = db._rrf_fuse([_row(source_id=1)], [], rrf_k=60)
        key = ("edgar_risk_factor_filings", 1, 0)
        assert key in fused
        assert abs(fused[key]["score"] - 1.0 / 61) < 1e-9

    def test_lexical_literal_match_gets_full_weight(self):
        lex = _row(source_id=2, literal_rank=0.5)
        fused = db._rrf_fuse([], [lex], rrf_k=60)
        key = ("edgar_risk_factor_filings", 2, 0)
        assert abs(fused[key]["score"] - 1.0 / 61) < 1e-9

    def test_lexical_expansion_only_match_gets_half_weight(self):
        lex = _row(source_id=3, literal_rank=0.0)
        fused = db._rrf_fuse([], [lex], rrf_k=60)
        key = ("edgar_risk_factor_filings", 3, 0)
        assert abs(fused[key]["score"] - 0.5 * (1.0 / 61)) < 1e-9

    def test_same_key_from_both_lists_accumulates_score(self):
        dense = _row(source_id=4)
        lex = _row(source_id=4, literal_rank=0.9)
        fused = db._rrf_fuse([dense], [lex], rrf_k=60)
        key = ("edgar_risk_factor_filings", 4, 0)
        assert abs(fused[key]["score"] - (1.0 / 61 + 1.0 / 61)) < 1e-9


class TestGraphRerankFactor:
    def test_no_hop_is_neutral_factor(self):
        assert db._graph_rerank_factor(None, graph_weight=0.5) == 1.0

    def test_hop_zero_gets_full_boost(self):
        assert db._graph_rerank_factor(0, graph_weight=0.5) == 1.5

    def test_larger_hop_gets_smaller_boost(self):
        f1 = db._graph_rerank_factor(1, graph_weight=0.5)
        f2 = db._graph_rerank_factor(2, graph_weight=0.5)
        assert 1.0 < f2 < f1 < 1.5


class TestGetRelevantContextHybridOrchestration:
    def test_unlinked_candidates_are_never_penalised(self):
        dense_row = _row(source_id=1, distance=0.2)
        with patch.object(db, "get_relevant_context", MagicMock(return_value=[dense_row])), \
             patch.object(db, "_HAS_PGVECTOR", True), \
             patch.object(db, "_PGVECTOR_READY", True), \
             patch.object(db, "search_concepts_by_embedding", MagicMock(return_value=[])), \
             patch.object(db, "_fetch_lexical_candidates", MagicMock(return_value=[])), \
             patch.object(db, "list_concept_links", MagicMock(return_value=[])):
            results = db.get_relevant_context_hybrid([0.1] * 1536, "vendor breach", limit=5)
        assert len(results) == 1
        assert results[0]["final_score"] == results[0]["rrf_score"]  # factor 1.0, no boost/penalty

    def test_confirmed_link_at_a_resolved_hop_boosts_score(self):
        dense_row = _row(source_id=7, distance=0.2)
        concept_match = {"concept_id": 10, "scheme": "risk_category", "pref_label": "Supply", "distance": 0.1}
        closure = [{"concept_id": 10, "pref_label": "Supply", "hop": 0}]
        with patch.object(db, "get_relevant_context", MagicMock(return_value=[dense_row])), \
             patch.object(db, "_HAS_PGVECTOR", True), \
             patch.object(db, "_PGVECTOR_READY", True), \
             patch.object(db, "search_concepts_by_embedding", MagicMock(return_value=[concept_match])), \
             patch.object(db, "get_concept_closure", MagicMock(return_value=closure)), \
             patch.object(db, "get_concepts_by_ids", MagicMock(return_value=[])), \
             patch.object(db, "_fetch_lexical_candidates", MagicMock(return_value=[])), \
             patch.object(db, "list_concept_links",
                          MagicMock(return_value=[{"source_table": "edgar_risk_factor_filings",
                                                     "source_id": "7", "concept_id": 10, "status": "confirmed"}])):
            results = db.get_relevant_context_hybrid([0.1] * 1536, "vendor breach", limit=5, graph_weight=0.5)
        assert results[0]["final_score"] > results[0]["rrf_score"]
        assert results[0]["matched_concept"] == "Supply"

    def test_proposed_not_confirmed_link_is_ignored(self):
        """Only status='confirmed' links may affect ranking — a 'proposed'
        link is never authoritative (framework_mappings.py's guardrail)."""
        dense_row = _row(source_id=8, distance=0.2)
        with patch.object(db, "get_relevant_context", MagicMock(return_value=[dense_row])), \
             patch.object(db, "_HAS_PGVECTOR", True), \
             patch.object(db, "_PGVECTOR_READY", True), \
             patch.object(db, "search_concepts_by_embedding", MagicMock(return_value=[])), \
             patch.object(db, "_fetch_lexical_candidates", MagicMock(return_value=[])), \
             patch.object(db, "list_concept_links", MagicMock(return_value=[])) as list_links:
            db.get_relevant_context_hybrid([0.1] * 1536, "vendor breach", limit=5)
        # list_concept_links is always called scoped to status='confirmed' —
        # a 'proposed' link is filtered server-side, never fetched as if authoritative.
        list_links.assert_called_once_with(status="confirmed")

    def test_pgvector_unavailable_skips_concept_expansion_without_erroring(self):
        dense_row = _row(source_id=9, distance=0.3)
        with patch.object(db, "get_relevant_context", MagicMock(return_value=[dense_row])), \
             patch.object(db, "_HAS_PGVECTOR", False), \
             patch.object(db, "_fetch_lexical_candidates", MagicMock(return_value=[])), \
             patch.object(db, "list_concept_links", MagicMock(return_value=[])):
            results = db.get_relevant_context_hybrid([0.1] * 1536, "vendor breach", limit=5)
        assert results[0]["matched_concept"] is None

    def test_no_company_id_skips_risk_similarity_boost_without_erroring(self):
        """company_id is required to scope risk_relationships lookups — when
        it's absent, the risk-edge step must no-op, not raise."""
        dense_row = _row(source_table="risk_scores", source_id=5, distance=0.2)
        with patch.object(db, "get_relevant_context", MagicMock(return_value=[dense_row])), \
             patch.object(db, "_HAS_PGVECTOR", False), \
             patch.object(db, "_fetch_lexical_candidates", MagicMock(return_value=[])), \
             patch.object(db, "list_concept_links", MagicMock(return_value=[])), \
             patch.object(db, "get_risk_refs_for_score_ids", MagicMock()) as refs:
            results = db.get_relevant_context_hybrid([0.1] * 1536, "vendor breach", limit=5)
        refs.assert_not_called()
        assert results[0]["final_score"] == results[0]["rrf_score"]


class TestRiskSimilarityBoosts:
    def test_similar_risk_to_a_top_seed_gets_boosted(self):
        fused = {
            ("risk_scores", 1, 0): {"score": 0.02, "row": _row(source_table="risk_scores", source_id=1)},
            ("risk_scores", 2, 0): {"score": 0.001, "row": _row(source_table="risk_scores", source_id=2)},
        }
        with patch.object(db, "get_risk_refs_for_score_ids", MagicMock(return_value={1: "R1", 2: "R2"})), \
             patch.object(db, "get_similar_risk_edges", MagicMock(return_value={"R1": [("R2", 0.8)]})):
            boosts = db._risk_similarity_boosts(fused, company_id=42)
        assert boosts == {("risk_scores", 2, 0): 0.8}

    def test_no_edge_produces_no_boost(self):
        fused = {
            ("risk_scores", 1, 0): {"score": 0.02, "row": _row(source_table="risk_scores", source_id=1)},
            ("risk_scores", 2, 0): {"score": 0.001, "row": _row(source_table="risk_scores", source_id=2)},
        }
        with patch.object(db, "get_risk_refs_for_score_ids", MagicMock(return_value={1: "R1", 2: "R2"})), \
             patch.object(db, "get_similar_risk_edges", MagicMock(return_value={})):
            boosts = db._risk_similarity_boosts(fused, company_id=42)
        assert boosts == {}

    def test_non_risk_scores_candidates_are_ignored(self):
        fused = {("rss_articles", 1, 0): {"score": 0.02, "row": _row(source_table="rss_articles", source_id=1)}}
        refs = MagicMock()
        with patch.object(db, "get_risk_refs_for_score_ids", refs):
            boosts = db._risk_similarity_boosts(fused, company_id=42)
        assert boosts == {}
        refs.assert_not_called()

    def test_missing_company_id_short_circuits(self):
        fused = {("risk_scores", 1, 0): {"score": 0.02, "row": _row(source_table="risk_scores", source_id=1)}}
        refs = MagicMock()
        with patch.object(db, "get_risk_refs_for_score_ids", refs):
            boosts = db._risk_similarity_boosts(fused, company_id=None)
        assert boosts == {}
        refs.assert_not_called()

    def test_full_orchestration_applies_risk_edge_factor(self):
        dense_rows = [
            _row(source_table="risk_scores", source_id=1, distance=0.1),
            _row(source_table="risk_scores", source_id=2, distance=0.5),
        ]
        with patch.object(db, "get_relevant_context", MagicMock(return_value=dense_rows)), \
             patch.object(db, "_HAS_PGVECTOR", False), \
             patch.object(db, "_fetch_lexical_candidates", MagicMock(return_value=[])), \
             patch.object(db, "list_concept_links", MagicMock(return_value=[])), \
             patch.object(db, "get_risk_refs_for_score_ids", MagicMock(return_value={1: "R1", 2: "R2"})), \
             patch.object(db, "get_similar_risk_edges", MagicMock(return_value={"R1": [("R2", 1.0)]})):
            results = db.get_relevant_context_hybrid(
                [0.1] * 1536, "vendor breach", company_id=42, limit=5, risk_edge_weight=0.3,
            )
        by_id = {r["source_id"]: r for r in results}
        assert by_id[2]["final_score"] > by_id[2]["rrf_score"]
        assert by_id[1]["final_score"] == by_id[1]["rrf_score"]  # the seed itself gets no self-boost
