#!/usr/bin/env python3
"""
Tests for db.py's passive shadow-AI candidate functions:
upsert_ai_shadow_candidate, list_ai_shadow_candidates,
dismiss_ai_shadow_candidate, resolve_ai_shadow_candidate_by_name.

db._conn is mocked at the cursor level — no real Postgres. These lock in
the Python-level contract (which SQL runs, with what params, and what a
given cursor result maps to) rather than real ON CONFLICT semantics, which
need a real database (see tests/test_db_integration.py).

    pytest test_ai_shadow_candidates.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import db


def _mock_conn(fetchone_results=None, fetchall_result=None):
    """A MagicMock standing in for db._conn()'s context manager, whose
    cursor's fetchone() returns each of `fetchone_results` in order across
    successive execute() calls (one SELECT then one INSERT, say)."""
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    if fetchone_results is not None:
        cur.fetchone.side_effect = fetchone_results
    if fetchall_result is not None:
        cur.fetchall.return_value = fetchall_result
        cur.description = [(k,) for k in fetchall_result[0].keys()] if fetchall_result and isinstance(fetchall_result[0], dict) else cur.description
    return conn, cur


class TestUpsertAiShadowCandidate:
    def test_skips_and_returns_none_when_already_registered(self):
        conn, cur = _mock_conn(fetchone_results=[("existing",)])  # SELECT finds a registered system
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            result = db.upsert_ai_shadow_candidate("OpenAI", "OPENAI_ENTERPRISE_ACCESS", "jsmith@acme-corp.com")
        assert result is None
        # Only the SELECT ran — no INSERT attempted.
        assert cur.execute.call_count == 1

    def test_inserts_and_returns_id_when_not_registered(self):
        conn, cur = _mock_conn(fetchone_results=[None, (17,)])  # SELECT: no match; INSERT: new id
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            result = db.upsert_ai_shadow_candidate("OpenAI", "OPENAI_ENTERPRISE_ACCESS", "jsmith@acme-corp.com")
        assert result == 17
        assert cur.execute.call_count == 2
        insert_sql, insert_params = cur.execute.call_args_list[1][0]
        assert "INSERT INTO observability.ai_shadow_candidates" in insert_sql
        assert insert_params == ("OpenAI", "OPENAI_ENTERPRISE_ACCESS", "jsmith@acme-corp.com")

    def test_returns_none_when_conflicting_row_is_not_pending(self):
        """ON CONFLICT ... WHERE status = 'pending' — a real Postgres would
        simply not update a non-pending row and RETURNING would yield
        nothing. Simulated here as fetchone() returning None on the insert."""
        conn, cur = _mock_conn(fetchone_results=[None, None])
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            result = db.upsert_ai_shadow_candidate("OpenAI", "OPENAI_ENTERPRISE_ACCESS", "jsmith@acme-corp.com")
        assert result is None


class TestListAiShadowCandidates:
    def test_defaults_to_pending_and_isoformats_dates(self):
        import datetime
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.description = [(c,) for c in (
            "id", "detected_name", "source_detail", "first_detected_at", "last_seen_at",
            "occurrence_count", "last_actor", "status", "linked_system_id", "reviewed_by", "reviewed_at",
        )]
        now = datetime.datetime(2026, 8, 15, 12, 0, 0)
        cur.fetchall.return_value = [
            (7, "OpenAI", "OPENAI_ENTERPRISE_ACCESS", now, now, 3, "jsmith@acme-corp.com", "pending", None, None, None),
        ]
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            result = db.list_ai_shadow_candidates()

        assert len(result) == 1
        assert result[0]["detected_name"] == "OpenAI"
        assert result[0]["first_detected_at"] == now.isoformat()
        params = cur.execute.call_args[0][1]
        assert params == ("pending",)

    def test_forwards_status_param(self):
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.description = [("id",)]
        cur.fetchall.return_value = []
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            db.list_ai_shadow_candidates(status="dismissed")
        params = cur.execute.call_args[0][1]
        assert params == ("dismissed",)

    def test_returns_empty_list_when_db_unavailable(self):
        with patch.object(db, "is_available", return_value=False):
            assert db.list_ai_shadow_candidates() == []


class TestDismissAiShadowCandidate:
    def test_returns_true_when_a_pending_row_matched(self):
        conn, cur = _mock_conn(fetchone_results=[(7,)])
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            assert db.dismiss_ai_shadow_candidate(7, "tester") is True

    def test_returns_false_when_no_row_matched(self):
        conn, cur = _mock_conn(fetchone_results=[None])
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            assert db.dismiss_ai_shadow_candidate(7, "tester") is False

    def test_returns_false_when_db_unavailable(self):
        with patch.object(db, "is_available", return_value=False):
            assert db.dismiss_ai_shadow_candidate(7, "tester") is False


class TestResolveAiShadowCandidateByName:
    def test_never_raises_when_db_unavailable(self):
        with patch.object(db, "is_available", return_value=False):
            # Must not raise — the caller (register save) can't be broken by this.
            db.resolve_ai_shadow_candidate_by_name("OpenAI", 42, "tester")

    def test_never_raises_on_a_db_error(self):
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", side_effect=RuntimeError("connection refused")):
            db.resolve_ai_shadow_candidate_by_name("OpenAI", 42, "tester")

    def test_issues_the_expected_update(self):
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            db.resolve_ai_shadow_candidate_by_name("OpenAI", 42, "tester")
        sql, params = cur.execute.call_args[0]
        assert "UPDATE observability.ai_shadow_candidates" in sql
        assert "status = 'accepted'" in sql
        assert params == (42, "tester", "OpenAI")
