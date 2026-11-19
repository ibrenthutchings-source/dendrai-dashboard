#!/usr/bin/env python3
"""
Unit tests for db.py's observability.infra_assets CRUD layer (Infrastructure
Vulnerability & Currency Posture, Phase 1): upsert_infra_asset,
mark_infra_asset_assessed, list_infra_assets, get_infra_asset,
list_expiring_credentials, list_expiring_infra_assets.

db._conn() is faked at the boundary — no real database. Same shape as
test_control_flow_map.py's _FakeCursor/_FakeConn, extended with
fetchone()/description since these functions read RETURNING/SELECT rows
back into dicts via db._infra_asset_row_to_dict.

    pytest test_infra_assets_db.py -v
"""

from __future__ import annotations

import db


_COLS = [c.strip() for c in db._INFRA_ASSET_COLUMNS.split(",")]


class _FakeCursor:
    def __init__(self, recorder, fetchone_result=None, fetchall_result=None):
        self._recorder = recorder
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result or []
        self.description = [(c,) for c in _COLS]

    def execute(self, sql, params=None):
        self._recorder.append((sql, params))

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return list(self._fetchall_result)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, recorder, fetchone_result, fetchall_result):
        self._recorder = recorder
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result

    def cursor(self):
        return _FakeCursor(self._recorder, self._fetchone_result, self._fetchall_result)

    def commit(self):
        pass


class _FakeConnCtx:
    def __init__(self, recorder, fetchone_result=None, fetchall_result=None):
        self._recorder = recorder
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result

    def __enter__(self):
        return _FakeConn(self._recorder, self._fetchone_result, self._fetchall_result)

    def __exit__(self, *a):
        return False


def _row(**overrides) -> tuple:
    """A full infra_assets row in _INFRA_ASSET_COLUMNS order, all-None
    baseline with named overrides — avoids 22 positional Nones per test."""
    base = {c: None for c in _COLS}
    base.update(overrides)
    return tuple(base[c] for c in _COLS)


# ── upsert_infra_asset ───────────────────────────────────────────────────────

def test_upsert_infra_asset_returns_row_and_does_not_touch_assessment_fields(monkeypatch):
    recorder = []
    fake_row = _row(id=1, asset_key="postgres:primary-db", asset_type="database",
                     name="primary-db", last_assessed_at=None)
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, fetchone_result=fake_row))

    result = db.upsert_infra_asset("postgres:primary-db", "database", "primary-db", connector_id=1)

    assert result["asset_key"] == "postgres:primary-db"
    assert result["last_assessed_at"] is None  # inventory discovery alone never marks assessed
    sql, params = recorder[0]
    assert "INSERT INTO observability.infra_assets" in sql
    assert "ON CONFLICT (estate_label, COALESCE(connector_id, 0), asset_key)" in sql
    # positional param order: estate_label, asset_key, connector_id, asset_type, name, ...
    assert params[0] == "default"
    assert params[1] == "postgres:primary-db"
    assert params[2] == 1
    assert params[3] == "database"


def test_upsert_infra_asset_serializes_metadata_as_json(monkeypatch):
    recorder = []
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, fetchone_result=_row(id=1)))
    db.upsert_infra_asset("cert:api:443", "certificate", "api", metadata={"issuer": "CN=Test CA"})
    _, params = recorder[0]
    # Json(...) wrapper — psycopg2's adapter type, not a plain dict
    assert params[-1] is not None
    assert type(params[-1]).__name__ == "Json"


# ── mark_infra_asset_assessed ────────────────────────────────────────────────

def test_mark_infra_asset_assessed_stamps_now_and_source(monkeypatch):
    recorder = []
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder))
    db.mark_infra_asset_assessed("cert:api:443", "tls_cert")
    sql, params = recorder[0]
    assert "SET last_assessed_at = NOW()" in sql
    assert params == ("tls_cert", "cert:api:443")


# ── list_infra_assets ─────────────────────────────────────────────────────────

def test_list_infra_assets_unassessed_only_filters_on_null(monkeypatch):
    recorder = []
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, fetchall_result=[]))
    db.list_infra_assets(unassessed_only=True)
    sql, params = recorder[0]
    assert "last_assessed_at IS NULL" in sql


def test_list_infra_assets_returns_dicts_preserving_null_last_assessed(monkeypatch):
    """The central honesty property: an asset row with last_assessed_at=None
    coming back from the DB must decode to a dict with that field still
    None — never coerced to a falsy-but-present value that could render
    the same as a real pass."""
    recorder = []
    rows = [_row(id=1, asset_key="cert:api:443", asset_type="certificate", name="api", last_assessed_at=None)]
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, fetchall_result=rows))
    result = db.list_infra_assets()
    assert len(result) == 1
    assert result[0]["last_assessed_at"] is None


def test_list_infra_assets_filters_by_asset_type(monkeypatch):
    recorder = []
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, fetchall_result=[]))
    db.list_infra_assets(asset_type="certificate")
    sql, params = recorder[0]
    assert "asset_type = %s" in sql
    assert params[0] == "certificate"


# ── get_infra_asset ────────────────────────────────────────────────────────────

def test_get_infra_asset_not_found_returns_none(monkeypatch):
    recorder = []
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, fetchone_result=None))
    assert db.get_infra_asset("nope") is None


def test_get_infra_asset_found_returns_dict(monkeypatch):
    recorder = []
    fake_row = _row(id=1, asset_key="postgres:primary-db", asset_type="database", name="primary-db")
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, fetchone_result=fake_row))
    result = db.get_infra_asset("postgres:primary-db")
    assert result["asset_key"] == "postgres:primary-db"


# ── list_expiring_credentials / list_expiring_infra_assets ──────────────────

def test_list_expiring_credentials_passes_warn_days(monkeypatch):
    recorder = []
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, fetchall_result=[]))
    db.list_expiring_credentials(warn_days=45)
    sql, params = recorder[0]
    assert "credentials_expires_at" in sql
    assert params == (45,)


def test_list_expiring_infra_assets_passes_warn_days(monkeypatch):
    recorder = []
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, fetchall_result=[]))
    db.list_expiring_infra_assets(warn_days=7)
    sql, params = recorder[0]
    assert "expires_at IS NOT NULL" in sql
    assert params == (7,)
