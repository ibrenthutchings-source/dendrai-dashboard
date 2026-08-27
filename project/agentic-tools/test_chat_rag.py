"""
Tests for chat_endpoint.py's ONTOLOGY_HYBRID_RAG flag wiring
(_hybrid_rag_enabled, _retrieve_context) and _build_system's rendering of
hybrid-retrieval rows (which may carry final_score/matched_concept instead of
plain dense distance).

DB/embedding calls mocked as MagicMock instances passed into a single
patch.object() per target — this suite's standard pattern, adopted after a
nested-patch bug elsewhere silently shadowed an outer mock.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import chat_endpoint as ce
import db


class TestHybridRagFlag:
    def test_defaults_to_disabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ONTOLOGY_HYBRID_RAG", None)
            assert ce._hybrid_rag_enabled() is False

    def test_true_variants_enable_it(self):
        for val in ("1", "true", "True", "yes", "YES"):
            with patch.dict(os.environ, {"ONTOLOGY_HYBRID_RAG": val}):
                assert ce._hybrid_rag_enabled() is True

    def test_other_values_stay_disabled(self):
        with patch.dict(os.environ, {"ONTOLOGY_HYBRID_RAG": "0"}):
            assert ce._hybrid_rag_enabled() is False


class TestRetrieveContextRouting:
    def test_disabled_flag_calls_plain_dense_path(self):
        with patch.dict(os.environ, {"ONTOLOGY_HYBRID_RAG": "false"}), \
             patch.object(db, "is_available", MagicMock(return_value=True)), \
             patch.object(ce, "_embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "get_company_id", MagicMock(return_value=None)), \
             patch.object(db, "get_relevant_context", MagicMock(return_value=[])) as dense, \
             patch.object(db, "get_relevant_context_hybrid", MagicMock()) as hybrid:
            ce._retrieve_context("vendor breach", "")
        dense.assert_called_once()
        hybrid.assert_not_called()

    def test_enabled_flag_calls_hybrid_path(self):
        with patch.dict(os.environ, {"ONTOLOGY_HYBRID_RAG": "true"}), \
             patch.object(db, "is_available", MagicMock(return_value=True)), \
             patch.object(ce, "_embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "get_company_id", MagicMock(return_value=None)), \
             patch.object(db, "get_relevant_context", MagicMock()) as dense, \
             patch.object(db, "get_relevant_context_hybrid", MagicMock(return_value=[])) as hybrid:
            ce._retrieve_context("vendor breach", "")
        hybrid.assert_called_once()
        dense.assert_not_called()

    def test_no_embedding_returns_empty_regardless_of_flag(self):
        with patch.dict(os.environ, {"ONTOLOGY_HYBRID_RAG": "true"}), \
             patch.object(db, "is_available", MagicMock(return_value=True)), \
             patch.object(ce, "_embed_text", MagicMock(return_value=None)):
            assert ce._retrieve_context("vendor breach", "") == []


class TestBuildSystemSnippetRendering:
    def test_dense_row_renders_distance(self):
        text = ce._build_system("ACME", "", [], {}, retrieved=[
            {"content_type": "risk_factor", "distance": 0.123, "text_snippet": "supplier risk"},
        ])
        assert "distance=0.123" in text
        assert "supplier risk" in text

    def test_hybrid_lexical_only_row_renders_final_score_not_distance(self):
        """A hybrid row that only matched lexically carries no dense
        'distance' — must not KeyError, and must show something meaningful."""
        text = ce._build_system("ACME", "", [], {}, retrieved=[
            {"content_type": "risk_factor", "final_score": 0.045, "text_snippet": "vendor concentration"},
        ])
        assert "relevance=0.045" in text
        assert "vendor concentration" in text

    def test_matched_concept_note_included_when_present(self):
        text = ce._build_system("ACME", "", [], {}, retrieved=[
            {"content_type": "risk_factor", "final_score": 0.05, "text_snippet": "x",
             "matched_concept": "Third-Party & Vendor Risk"},
        ])
        assert "Third-Party & Vendor Risk" in text

    def test_no_matched_concept_key_omits_note(self):
        text = ce._build_system("ACME", "", [], {}, retrieved=[
            {"content_type": "risk_factor", "distance": 0.2, "text_snippet": "x"},
        ])
        assert "resolved to the concept" not in text
