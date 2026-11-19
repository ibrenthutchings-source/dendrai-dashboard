#!/usr/bin/env python3
"""
Regression test for GET /observability/events' windowing bug: for a `days`
window whose true event volume exceeds `limit`, get_recent_adjudications_for_
domain_summary/get_recent_unreviewed_system_events must keep the MOST RECENT
`limit` rows, not the oldest — otherwise Continuous Monitoring's Event
Replay/Sankey/Heat Grid/Flow Graph charts ("By Core Domain & Risk" and
"Adjudication Flow" alike, since both read this same feed — see
continuous-monitoring-viz.jsx's module docstring) lose per-domain detail as
soon as a wider window's real volume passes the cap: a plain
`ORDER BY adjudicated_at LIMIT %s` keeps the earliest rows in the window and
silently drops everything newer, which thins out (or empties) exactly the
recent, high-density activity a 30d/90d view most needs to show.

db._conn is mocked at the cursor level — no real Postgres. This locks in the
query SHAPE (DESC inside, LIMIT, then re-sorted ASC outside) rather than
real ORDER BY/LIMIT semantics, which need a real database (see
tests/test_db_integration.py).

    pytest test_observability_events_window.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import db


def _mock_conn(fetchall_result):
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = fetchall_result
    return conn, cur


class TestGetRecentAdjudicationsForDomainSummary:
    def test_query_keeps_most_recent_rows_not_oldest(self):
        """The inner ORDER BY must be DESC (paired with LIMIT) so a window
        whose true volume exceeds `limit` keeps the latest rows; the outer
        query must re-sort ASC so callers still see oldest-first, per this
        function's own documented contract."""
        conn, cur = _mock_conn([])
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            db.get_recent_adjudications_for_domain_summary(days=90, limit=5000)
        sql, params = cur.execute.call_args[0]
        inner, _, outer = sql.partition(") recent")
        assert "ORDER BY adjudicated_at DESC" in inner
        assert "LIMIT %s" in inner
        assert "ORDER BY adjudicated_at" in outer
        assert "DESC" not in outer
        assert params == (90, 5000)

    def test_returns_rows_in_the_order_the_query_gives_back(self):
        now_rows = [
            (1, "t1", "CLEAR", "LOW", "sap", "tool", "srv", False, [], None, None),
            (2, "t2", "ESCALATE", "HIGH", "sap", "tool", "srv", True, [], None, None),
        ]
        conn, cur = _mock_conn(now_rows)
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            result = db.get_recent_adjudications_for_domain_summary(days=7, limit=5000)
        assert [r["id"] for r in result] == [1, 2]


class TestGetRecentUnreviewedSystemEvents:
    def test_query_keeps_most_recent_rows_not_oldest(self):
        conn, cur = _mock_conn([])
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            db.get_recent_unreviewed_system_events(days=90, limit=5000)
        sql, params = cur.execute.call_args[0]
        inner, _, outer = sql.partition(") recent")
        assert "ORDER BY st.created_at DESC" in inner
        assert "LIMIT %s" in inner
        assert "ORDER BY created_at" in outer
        assert "DESC" not in outer
        assert params == (90, 5000)

    def test_returns_rows_shaped_for_the_events_feed_merge(self):
        rows = [(5, "t1", "sailpoint", "res", "srv", {"case_id": "C1", "process_step": "Access Requested"})]
        conn, cur = _mock_conn(rows)
        with patch.object(db, "is_available", return_value=True), \
             patch.object(db, "_conn", return_value=conn):
            result = db.get_recent_unreviewed_system_events(days=7, limit=5000)
        assert result == [{
            "id": 10_000_000_005, "adjudicated_at": "t1",
            "final_verdict": None, "risk_tier": None,
            "source_system": "sailpoint", "target_tool": "res", "server_name": "srv",
            "requires_human_review": False, "policy_violations": [],
            "case_id": "C1", "process_step": "Access Requested",
        }]
