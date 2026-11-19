#!/usr/bin/env python3
"""
Tests for db.py's identity/role graph functions (observability.
identity_role_edges / .sod_violations) — the persistence layer
identity_graph_sync.py writes to and mcp_governance.py's _process_one
enrichment reads from. Only the DB I/O boundary is faked (db._conn — the
module-private name db.py's own functions call, distinct from db.get_conn,
the public alias mcp_governance.py uses), matching test_ingest_api_key_
security.py's pattern.

execute_values (psycopg2.extras) itself is stubbed rather than faked at the
cursor level — it needs real cur.mogrify() batching that isn't worth
reimplementing in a test double; what matters here is that
upsert_identity_role_edges/upsert_sod_violations call it with the right
(sql, rows) after filtering, which this proves directly.

    pytest test_identity_graph_db.py -v
"""
from __future__ import annotations

import db


class _FakeCursor:
    def __init__(self, recorder, fetch_result=None):
        self._recorder = recorder
        self._fetch_result = fetch_result or []

    def execute(self, sql, params=None):
        self._recorder.append((sql, params))

    def fetchall(self):
        return list(self._fetch_result)

    def fetchone(self):
        return self._fetch_result[0] if self._fetch_result else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, recorder, fetch_result=None):
        self._recorder = recorder
        self._fetch_result = fetch_result

    def cursor(self):
        return _FakeCursor(self._recorder, self._fetch_result)

    def commit(self):
        pass

    def rollback(self):
        pass


class _FakeConnCtx:
    def __init__(self, recorder, fetch_result=None):
        self._recorder = recorder
        self._fetch_result = fetch_result

    def __enter__(self):
        return _FakeConn(self._recorder, self._fetch_result)

    def __exit__(self, *a):
        return False


def _wire(monkeypatch, fetch_result=None):
    recorder = []
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, fetch_result))
    return recorder


# ── upsert_identity_role_edges — filters + delete-then-insert ───────────────

def test_upsert_identity_role_edges_filters_incomplete_rows_and_inserts_the_rest(monkeypatch):
    recorder = _wire(monkeypatch)
    captured = {}
    monkeypatch.setattr(db, "execute_values", lambda cur, sql, rows: captured.update(sql=sql, rows=rows))

    n = db.upsert_identity_role_edges(7, [
        {"username": "alice", "role": "AP_Manager", "role_id": "r1"},
        {"username": "", "role": "Bad"},        # missing username — filtered
        {"username": "bob", "role": ""},        # missing role — filtered
        {"username": "carol", "role": "Approver"},  # no role_id — allowed, None
    ])

    assert n == 2
    assert captured["rows"] == [(7, "alice", "AP_Manager", "r1"), (7, "carol", "Approver", None)]
    assert "INSERT INTO observability.identity_role_edges" in captured["sql"]
    assert any("DELETE FROM observability.identity_role_edges" in sql and params == (7,) for sql, params in recorder)


def test_upsert_identity_role_edges_empty_list_still_deletes_but_skips_insert(monkeypatch):
    """A connector that now reports zero role assignments must clear out
    every previously-recorded edge for it (full refresh), not leave stale
    edges from a prior sync — execute_values must not be called on an
    empty row set (it would be a no-op INSERT at best, a wasted round trip)."""
    recorder = _wire(monkeypatch)
    called = {"n": 0}
    monkeypatch.setattr(db, "execute_values", lambda cur, sql, rows: called.__setitem__("n", called["n"] + 1))

    n = db.upsert_identity_role_edges(7, [])

    assert n == 0
    assert called["n"] == 0
    assert any("DELETE FROM observability.identity_role_edges" in sql for sql, params in recorder)


# ── upsert_sod_violations — same filter/delete-then-insert shape ────────────

def test_upsert_sod_violations_filters_incomplete_and_persists_conflict_roles(monkeypatch):
    recorder = _wire(monkeypatch)
    captured = {}
    monkeypatch.setattr(db, "execute_values", lambda cur, sql, rows: captured.update(sql=sql, rows=rows))

    n = db.upsert_sod_violations(3, [
        {"violation_id": "v1", "username": "alice", "policy_name": "AP-SoD-01",
         "conflict_roles": ["AP_Manager", "AP_Approver"], "risk_level": "High", "status": "Open"},
        {"violation_id": "", "username": "bob"},  # missing violation_id — filtered
    ])

    assert n == 1
    assert "INSERT INTO observability.sod_violations" in captured["sql"]
    row = captured["rows"][0]
    assert row[0] == 3 and row[1] == "v1" and row[2] == "alice"


# ── get_identity_role_count / get_identity_role_names ────────────────────────

def test_get_identity_role_count_returns_row_value(monkeypatch):
    _wire(monkeypatch, fetch_result=[(25,)])
    assert db.get_identity_role_count("alice") == 25


def test_get_identity_role_count_defaults_to_zero_on_no_rows(monkeypatch):
    _wire(monkeypatch, fetch_result=[])
    assert db.get_identity_role_count("nobody") == 0


def test_get_identity_role_names_returns_flat_list(monkeypatch):
    _wire(monkeypatch, fetch_result=[("AP_Approver",), ("AP_Manager",)])
    assert db.get_identity_role_names("alice") == ["AP_Approver", "AP_Manager"]


# ── list_open_sod_violations_for_user ────────────────────────────────────────

def test_list_open_sod_violations_for_user_maps_fields(monkeypatch):
    _wire(monkeypatch, fetch_result=[
        ("v1", "AP-SoD-01", ["AP_Manager", "AP_Approver"], "High", "Open", None),
    ])
    violations = db.list_open_sod_violations_for_user("alice")
    assert violations == [{
        "violation_id": "v1", "policy_name": "AP-SoD-01",
        "conflict_roles": ["AP_Manager", "AP_Approver"], "risk_level": "High",
        "status": "Open", "detected_date": None,
    }]
