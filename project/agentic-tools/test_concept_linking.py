"""
Tests for concept_linking.py's link_entity() (Stage 3 entity linking) and the
/ontology/link, /ontology/links endpoints.

DB and embedding calls mocked as MagicMock instances passed into a single
patch.object() per target — this suite's standard pattern, adopted after a
nested-patch bug elsewhere silently shadowed an outer mock.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import concept_linking as cl
import db
import embedding_util


def _candidate(concept_id=1, pref_label="Cybersecurity", distance=0.1):
    return {"concept_id": concept_id, "scheme": "risk_category", "pref_label": pref_label,
            "notation": None, "distance": distance}


class TestLinkEntityDegradesToUnresolved:
    def test_empty_text_is_unresolved_without_any_embedding_call(self):
        embed_avail = MagicMock()
        upsert_link = MagicMock(return_value={"status": "unresolved"})
        with patch.object(embedding_util, "is_available", embed_avail), \
             patch.object(db, "upsert_concept_link", upsert_link):
            result = cl.link_entity("risk_scores", "7", "risk_category", "   ")
        embed_avail.assert_not_called()
        upsert_link.assert_called_once_with(
            "risk_scores", "7", "risk_category", concept_id=None, status="unresolved", method="ann",
        )
        assert result == {"status": "unresolved"}

    def test_embedding_unavailable_is_unresolved(self):
        upsert_link = MagicMock(return_value={"status": "unresolved"})
        with patch.object(embedding_util, "is_available", MagicMock(return_value=False)), \
             patch.object(db, "upsert_concept_link", upsert_link):
            cl.link_entity("risk_scores", "7", "risk_category", "vendor breach")
        assert upsert_link.call_args.kwargs["status"] == "unresolved"

    def test_embed_text_failure_is_unresolved(self):
        upsert_link = MagicMock(return_value={"status": "unresolved"})
        with patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=None)), \
             patch.object(db, "upsert_concept_link", upsert_link):
            cl.link_entity("risk_scores", "7", "risk_category", "vendor breach")
        assert upsert_link.call_args.kwargs["status"] == "unresolved"

    def test_distance_above_ambiguous_band_is_unresolved_never_snapped(self):
        """d > 0.45: must NOT snap to the nearest-but-wrong concept."""
        upsert_link = MagicMock(return_value={"status": "unresolved"})
        task = MagicMock()
        with patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "search_concepts_by_embedding", MagicMock(return_value=[_candidate(distance=0.9)])), \
             patch.object(db, "upsert_concept_link", upsert_link), \
             patch.object(db, "upsert_concept_link_task", task):
            cl.link_entity("risk_scores", "7", "risk_category", "vendor breach")
        assert upsert_link.call_args.kwargs["status"] == "unresolved"
        assert upsert_link.call_args.kwargs["concept_id"] is None
        task.assert_not_called()  # unresolved never reaches the Approval Inbox

    def test_no_candidates_is_unresolved(self):
        upsert_link = MagicMock(return_value={"status": "unresolved"})
        with patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "search_concepts_by_embedding", MagicMock(return_value=[])), \
             patch.object(db, "upsert_concept_link", upsert_link):
            cl.link_entity("risk_scores", "7", "risk_category", "vendor breach")
        assert upsert_link.call_args.kwargs["status"] == "unresolved"


class TestLinkEntityProposes:
    def test_confident_match_is_proposed_with_confidence_and_no_ambiguous_flag(self):
        upsert_link = MagicMock(return_value={"status": "proposed", "concept_id": 1})
        task = MagicMock()
        with patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "search_concepts_by_embedding",
                          MagicMock(return_value=[_candidate(distance=0.1)])), \
             patch.object(db, "upsert_concept_link", upsert_link), \
             patch.object(db, "upsert_concept_link_task", task):
            cl.link_entity("risk_scores", "7", "risk_category", "vendor breach")
        kwargs = upsert_link.call_args.kwargs
        assert kwargs["status"] == "proposed"
        assert kwargs["concept_id"] == 1
        assert abs(kwargs["confidence"] - 0.9) < 1e-9
        task.assert_called_once()
        task_args = task.call_args.args
        assert task_args[0] == "risk_scores:7:risk_category"
        assert task.call_args.args[2]["ambiguous"] is False

    def test_ambiguous_band_still_proposes_but_flags_and_records_runner_up(self):
        candidates = [_candidate(concept_id=1, distance=0.35), _candidate(concept_id=2, pref_label="Supply", distance=0.4)]
        upsert_link = MagicMock(return_value={"status": "proposed", "concept_id": 1})
        task = MagicMock()
        with patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "search_concepts_by_embedding", MagicMock(return_value=candidates)), \
             patch.object(db, "upsert_concept_link", upsert_link), \
             patch.object(db, "upsert_concept_link_task", task):
            cl.link_entity("risk_scores", "7", "risk_category", "vendor breach")
        kwargs = upsert_link.call_args.kwargs
        assert kwargs["status"] == "proposed"
        assert kwargs["runner_up_concept_id"] == 2
        assert task.call_args.args[2]["ambiguous"] is True

    def test_boundary_distance_exactly_at_ambiguous_cutoff_still_proposes(self):
        upsert_link = MagicMock(return_value={"status": "proposed"})
        with patch.object(embedding_util, "is_available", MagicMock(return_value=True)), \
             patch.object(embedding_util, "embed_text", MagicMock(return_value=[0.1] * 1536)), \
             patch.object(db, "search_concepts_by_embedding",
                          MagicMock(return_value=[_candidate(distance=cl.DISTANCE_AMBIGUOUS)])), \
             patch.object(db, "upsert_concept_link", upsert_link), \
             patch.object(db, "upsert_concept_link_task", MagicMock()):
            cl.link_entity("risk_scores", "7", "risk_category", "vendor breach")
        assert upsert_link.call_args.kwargs["status"] == "proposed"


class TestEndpoints:
    def _client(self):
        app = FastAPI()
        app.include_router(cl.router)
        return TestClient(app)

    def test_link_endpoint_returns_link(self):
        with patch.object(embedding_util, "is_available", MagicMock(return_value=False)), \
             patch.object(db, "upsert_concept_link", MagicMock(return_value={"status": "unresolved"})):
            r = self._client().post("/ontology/link", json={
                "source_table": "risk_scores", "source_id": "7",
                "scheme": "risk_category", "text": "vendor breach",
            })
        assert r.status_code == 200
        assert r.json()["link"]["status"] == "unresolved"

    def test_links_endpoint_returns_existing_links(self):
        with patch.object(db, "get_concept_links", MagicMock(return_value=[{"scheme": "risk_category"}])):
            r = self._client().get("/ontology/links", params={"source_table": "risk_scores", "source_id": "7"})
        assert r.status_code == 200
        assert r.json()["links"] == [{"scheme": "risk_category"}]
