"""
Tests for ontology_endpoints.py's concept-embedding orchestration
(embed_concept, reembed_stale_concepts) and the /ontology/search,
/ontology/reembed endpoints.

DB and embedding calls mocked as MagicMock instances passed into a single
patch.object() per target — this suite's standard pattern, adopted after a
nested-patch bug elsewhere silently shadowed an outer mock.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
import embedding_util
import ontology_endpoints as oe


def _concept(id_=1, scheme="risk_category", pref_label="Cybersecurity", *,
             alt_labels=None, definition="Risk of breach.", label_hash="abc123"):
    return {
        "id": id_, "scheme": scheme, "pref_label": pref_label,
        "alt_labels": alt_labels or [], "definition": definition,
        "broader_id": None, "source": "curated", "label_hash": label_hash,
    }


class TestConceptEmbeddingText:
    def test_includes_pref_label_and_definition(self):
        text = oe._concept_embedding_text(_concept())
        assert "Cybersecurity" in text
        assert "Risk of breach." in text

    def test_includes_alt_labels_when_present(self):
        text = oe._concept_embedding_text(_concept(alt_labels=["Cyber Risk", "InfoSec Risk"]))
        assert "Cyber Risk" in text
        assert "InfoSec Risk" in text

    def test_no_also_line_when_no_alt_labels(self):
        text = oe._concept_embedding_text(_concept(alt_labels=[]))
        assert "Also:" not in text


class TestEmbedConcept:
    def test_returns_false_when_embedding_unavailable(self):
        with patch.object(embedding_util, "is_available", MagicMock(return_value=False)):
            assert oe.embed_concept(_concept()) is False

    def test_returns_false_when_embed_text_fails(self):
        with patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=None)):
            assert oe.embed_concept(_concept()) is False

    def test_saves_with_concept_source_table_and_label_hash(self):
        save_embedding = MagicMock(return_value=42)
        with patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "save_embedding", save_embedding):
            result = oe.embed_concept(_concept(id_=7, label_hash="h1"))
        assert result is True
        save_embedding.assert_called_once()
        kwargs = save_embedding.call_args.kwargs
        assert kwargs["source_table"] == "concepts"
        assert kwargs["source_id"] == 7
        assert kwargs["content_type"] == db.EMBT_CONCEPT
        assert kwargs["company_id"] is None
        assert kwargs["source_hash"] == "h1"


class TestReembedStaleConcepts:
    def test_concept_with_matching_hash_is_not_reembedded(self):
        concepts = [_concept(id_=1, label_hash="same")]
        with patch.object(db, "list_concepts", MagicMock(return_value=concepts)), \
             patch.object(db, "get_concept_embedding_hashes", MagicMock(return_value={1: "same"})), \
             patch.object(embedding_util, "is_available", MagicMock(return_value=True)) as embed_avail:
            result = oe.reembed_stale_concepts()
        assert result == {"checked": 1, "stale": 0, "embedded": 0}
        embed_avail.assert_not_called()  # never even tries — nothing was stale

    def test_concept_with_changed_hash_is_reembedded(self):
        concepts = [_concept(id_=1, label_hash="new")]
        with patch.object(db, "list_concepts", MagicMock(return_value=concepts)), \
             patch.object(db, "get_concept_embedding_hashes", MagicMock(return_value={1: "old"})), \
             patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "save_embedding", MagicMock(return_value=1)):
            result = oe.reembed_stale_concepts()
        assert result == {"checked": 1, "stale": 1, "embedded": 1}

    def test_never_embedded_concept_counts_as_stale(self):
        """A concept absent from get_concept_embedding_hashes() (never
        embedded at all) must be treated as stale, not skipped."""
        concepts = [_concept(id_=99, label_hash="h")]
        with patch.object(db, "list_concepts", MagicMock(return_value=concepts)), \
             patch.object(db, "get_concept_embedding_hashes", MagicMock(return_value={})), \
             patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "save_embedding", MagicMock(return_value=1)):
            result = oe.reembed_stale_concepts()
        assert result["stale"] == 1
        assert result["embedded"] == 1

    def test_respects_limit(self):
        concepts = [_concept(id_=i, label_hash=f"h{i}") for i in range(5)]
        with patch.object(db, "list_concepts", MagicMock(return_value=concepts)), \
             patch.object(db, "get_concept_embedding_hashes", MagicMock(return_value={})), \
             patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "save_embedding", MagicMock(return_value=1)):
            result = oe.reembed_stale_concepts(limit=2)
        assert result["checked"] == 5
        assert result["stale"] == 2
        assert result["embedded"] == 2


class TestEndpoints:
    def _client(self):
        app = FastAPI()
        app.include_router(oe.router)
        return TestClient(app)

    def test_search_returns_note_when_embedding_unavailable(self):
        with patch.object(embedding_util, "is_available", MagicMock(return_value=False)):
            r = self._client().post("/ontology/search", json={"text": "vendor breach"})
        assert r.status_code == 200
        body = r.json()
        assert body["results"] == []
        assert "note" in body

    def test_search_returns_nearest_concepts(self):
        fake_results = [{"concept_id": 1, "scheme": "risk_category", "pref_label": "Supply",
                          "notation": None, "distance": 0.12}]
        with patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "search_concepts_by_embedding", MagicMock(return_value=fake_results)):
            r = self._client().post("/ontology/search", json={"text": "vendor breach"})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["results"][0]["pref_label"] == "Supply"

    def test_search_rejects_empty_text_without_embedding_call(self):
        embed_text = MagicMock()
        with patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", embed_text):
            r = self._client().post("/ontology/search", json={"text": "   "})
        assert r.status_code == 200
        assert r.json()["results"] == []
        embed_text.assert_not_called()

    def test_reembed_endpoint_returns_note_when_unavailable(self):
        with patch.object(embedding_util, "is_available", MagicMock(return_value=False)):
            r = self._client().post("/ontology/reembed")
        assert r.status_code == 200
        assert "note" in r.json()
