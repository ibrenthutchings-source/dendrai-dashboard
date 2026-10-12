#!/usr/bin/env python3
"""
Tests for db.get_approval_tasks_by_item_ref — the get_approval_tasks_for_run
equivalent for a gate type not tied to a risk_loop_run (run_id IS NULL),
used by process_mining_endpoints.py's walkthrough-narrative/history.

db._conn is mocked at the cursor level — no real Postgres.

    pytest test_get_approval_tasks_by_item_ref.py -v
"""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import db


def test_scopes_to_run_id_is_null_and_the_given_gate_type_and_item_ref():
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.description = [("id",), ("gate_type",), ("item_ref",), ("item_label",), ("status",),
                        ("disposition",), ("adjustments",), ("rationale",), ("prepared_by",),
                        ("prepared_by_name",), ("prepared_at",), ("manager_id",), ("manager_name",),
                        ("reviewed_by",), ("reviewed_by_name",), ("reviewed_at",), ("review_comment",),
                        ("ai_suggested",), ("ai_accepted",)]
    cur.fetchall.return_value = []
    with patch.object(db, "is_available", return_value=True), \
         patch.object(db, "_conn", return_value=conn):
        db.get_approval_tasks_by_item_ref("walkthrough_narrative", "procure_to_pay")

    sql, params = cur.execute.call_args[0]
    assert "run_id IS NULL" in sql
    assert "gate_type = %s" in sql
    assert "item_ref = %s" in sql
    assert params == ("walkthrough_narrative", "procure_to_pay")


def test_isoformats_timestamps_and_preserves_row_order():
    now = datetime.datetime(2026, 8, 15, 12, 0, 0)
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cols = ["id", "gate_type", "item_ref", "item_label", "status", "disposition", "adjustments",
            "rationale", "prepared_by", "prepared_by_name", "prepared_at", "manager_id", "manager_name",
            "reviewed_by", "reviewed_by_name", "reviewed_at", "review_comment", "ai_suggested", "ai_accepted"]
    cur.description = [(c,) for c in cols]
    cur.fetchall.return_value = [
        (2, "walkthrough_narrative", "procure_to_pay", "P2P walkthrough — 8/15", "submitted", "adjusted",
         {"process_description": "edited"}, "note", 1, "Preparer", now, 10, "Manager",
         None, None, None, None, {"process_description": "original"}, False),
        (1, "walkthrough_narrative", "procure_to_pay", "P2P walkthrough — 8/1", "approved", "approved",
         {"process_description": "x"}, None, 1, "Preparer", now, None, None,
         None, None, None, None, None, None),
    ]
    with patch.object(db, "is_available", return_value=True), \
         patch.object(db, "_conn", return_value=conn):
        result = db.get_approval_tasks_by_item_ref("walkthrough_narrative", "procure_to_pay")

    assert [r["id"] for r in result] == [2, 1]  # newest first, as the query returns
    assert result[0]["prepared_at"] == now.isoformat()
    assert result[0]["status"] == "submitted"


def test_returns_empty_list_when_db_unavailable():
    with patch.object(db, "is_available", return_value=False):
        assert db.get_approval_tasks_by_item_ref("walkthrough_narrative", "procure_to_pay") == []
