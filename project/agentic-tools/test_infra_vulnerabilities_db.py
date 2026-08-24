#!/usr/bin/env python3
"""
Unit tests for db.py's Phase 2 vulnerability register layer:
upsert_infra_vulnerability, update_infra_vulnerability_status,
list_infra_vulnerabilities, list_open_vulnerabilities_with_asset_version,
get_vulnerability_summary, list_assets_for_vuln_enrichment,
get_osv_cache_entry/put_osv_cache_entry.

db._conn() is faked at the boundary — no real database. Same
_FakeCursor/_FakeConn shape as test_infra_assets_db.py.

    pytest test_infra_vulnerabilities_db.py -v
"""

from __future__ import annotations

import db


_VULN_COLS = [c.strip() for c in db._INFRA_VULN_COLUMNS.split(",")]


class _FakeCursor:
    def __init__(self, recorder, fetchone_result=None, fetchall_result=None, cols=None):
        self._recorder = recorder
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result or []
        self.description = [(c,) for c in (cols or _VULN_COLS)]
        self.rowcount = 1

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
    def __init__(self, recorder, fetchone_result, fetchall_result, cols):
        self._recorder = recorder
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result
        self._cols = cols

    def cursor(self):
        return _FakeCursor(self._recorder, self._fetchone_result, self._fetchall_result, self._cols)

    def commit(self):
        pass


class _FakeConnCtx:
    def __init__(self, recorder, fetchone_result=None, fetchall_result=None, cols=None):
        self._recorder = recorder
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result
        self._cols = cols

    def __enter__(self):
        return _FakeConn(self._recorder, self._fetchone_result, self._fetchall_result, self._cols)

    def __exit__(self, *a):
        return False


def _vuln_row(**overrides) -> tuple:
    base = {c: None for c in _VULN_COLS}
    base.update(overrides)
    return tuple(base[c] for c in _VULN_COLS)


def _patch(monkeypatch, **kw):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(**kw))


# ── upsert_infra_vulnerability ───────────────────────────────────────────────

def test_upsert_infra_vulnerability_inserts_with_expected_param_order(monkeypatch):
    recorder = []
    fake_row = _vuln_row(id=1, vuln_id="CVE-2024-1234", status="open")
    _patch(monkeypatch, recorder=recorder, fetchone_result=fake_row)

    result = db.upsert_infra_vulnerability("CVE-2024-1234", asset_id=5, severity="HIGH")

    assert result["vuln_id"] == "CVE-2024-1234"
    assert result["status"] == "open"
    sql, params = recorder[0]
    assert "INSERT INTO observability.infra_vulnerabilities" in sql
    assert "ON CONFLICT (COALESCE(asset_id, 0), vuln_id, COALESCE(source_ref, ''))" in sql
    assert params[0] == 5           # asset_id
    assert params[1] == "CVE-2024-1234"  # vuln_id
    assert params[5] == "HIGH"      # severity


def test_upsert_infra_vulnerability_conflict_update_never_touches_status(monkeypatch):
    """A re-detected finding must never have its DO UPDATE clause reset
    status/remediated_at — only update_infra_vulnerability_status() may."""
    recorder = []
    _patch(monkeypatch, recorder=recorder, fetchone_result=_vuln_row(id=1))
    db.upsert_infra_vulnerability("CVE-2024-1234")
    sql, _ = recorder[0]
    do_update_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "status" not in do_update_clause.split("WHERE")[0].split("RETURNING")[0].lower().replace("remediation_basis", "")


# ── update_infra_vulnerability_status ────────────────────────────────────────

def test_update_infra_vulnerability_status_remediated_stamps_remediated_at(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder=recorder)
    ok = db.update_infra_vulnerability_status(42, "remediated", "version_advanced")
    assert ok is True
    sql, params = recorder[0]
    assert "remediated_at = CASE WHEN %s = 'remediated' THEN NOW()" in sql
    assert params == ("remediated", "version_advanced", None, None, None, "remediated", 42)


def test_update_infra_vulnerability_status_accepted_risk_carries_waiver(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder=recorder)
    db.update_infra_vulnerability_status(42, "accepted_risk", None, 7, "compensating control in place", "auditor@example.com")
    sql, params = recorder[0]
    assert params == ("accepted_risk", None, 7, "compensating control in place", "auditor@example.com", "accepted_risk", 42)


# ── list_infra_vulnerabilities ────────────────────────────────────────────────

def test_list_infra_vulnerabilities_filters_by_status_and_severity(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder=recorder, fetchall_result=[])
    db.list_infra_vulnerabilities(status="open", severity="CRITICAL")
    sql, params = recorder[0]
    assert "status = %s" in sql and "severity = %s" in sql
    assert params[:2] == ["open", "CRITICAL"]


def test_list_infra_vulnerabilities_orders_by_severity_then_recency(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder=recorder, fetchall_result=[])
    db.list_infra_vulnerabilities()
    sql, _ = recorder[0]
    assert "ORDER BY CASE severity" in sql


def test_list_infra_vulnerabilities_returns_dicts(monkeypatch):
    recorder = []
    rows = [_vuln_row(id=1, vuln_id="CVE-1", severity="HIGH", status="open")]
    _patch(monkeypatch, recorder=recorder, fetchall_result=rows)
    result = db.list_infra_vulnerabilities()
    assert result[0]["vuln_id"] == "CVE-1"
    assert result[0]["status"] == "open"


# ── list_open_vulnerabilities_with_asset_version ─────────────────────────────

def test_list_open_vulnerabilities_with_asset_version_shape(monkeypatch):
    recorder = []
    cols = ["id", "vuln_id", "fixed_version", "software_version", "asset_key"]
    rows = [(1, "CVE-1", "2.7.1", "2.8.0", "pypi:requests")]
    _patch(monkeypatch, recorder=recorder, fetchall_result=rows, cols=cols)
    result = db.list_open_vulnerabilities_with_asset_version()
    assert result == [{"id": 1, "vuln_id": "CVE-1", "fixed_version": "2.7.1",
                        "software_version": "2.8.0", "asset_key": "pypi:requests"}]
    sql, _ = recorder[0]
    assert "v.status = 'open'" in sql
    assert "a.software_version IS NOT NULL" in sql


# ── get_vulnerability_summary ────────────────────────────────────────────────

class _SummaryCursor:
    """get_vulnerability_summary runs 4 sequential queries on ONE cursor
    (not one per _conn() call like the other functions) — needs its own
    fake that returns different canned results per execute() call, in order."""
    def __init__(self, results):
        self._results = list(results)
        self._current = None

    def execute(self, sql, params=None):
        self._current = self._results.pop(0)

    def fetchall(self):
        return self._current

    def fetchone(self):
        return self._current

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _SummaryConn:
    def __init__(self, results):
        self._results = results

    def cursor(self):
        return _SummaryCursor(self._results)


class _SummaryConnCtx:
    def __init__(self, results):
        self._results = results

    def __enter__(self):
        return _SummaryConn(self._results)

    def __exit__(self, *a):
        return False


def test_get_vulnerability_summary_shape(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _SummaryConnCtx([
        [("CRITICAL", 2), ("HIGH", 5)],  # by-severity GROUP BY
        (20,),                            # assets_total
        (12,),                            # assets_assessed
        (3,),                              # remediated_last_30d
    ]))

    summary = db.get_vulnerability_summary()

    assert summary["open_by_severity"] == {"CRITICAL": 2, "HIGH": 5}
    assert summary["open_total"] == 7
    assert summary["assets_total"] == 20
    assert summary["assets_assessed"] == 12
    assert summary["remediated_last_30d"] == 3


def test_get_vulnerability_summary_no_db_returns_honest_zeroed_shape(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: False)
    summary = db.get_vulnerability_summary()
    assert summary["assets_total"] == 0
    assert summary["open_total"] == 0


# ── list_assets_for_vuln_enrichment ──────────────────────────────────────────

def test_list_assets_for_vuln_enrichment_filters_on_ecosystem_and_software(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder=recorder, fetchall_result=[])
    db.list_assets_for_vuln_enrichment()
    sql, _ = recorder[0]
    assert "ecosystem IS NOT NULL" in sql
    assert "software_name IS NOT NULL AND software_version IS NOT NULL" in sql


# ── osv_cache ─────────────────────────────────────────────────────────────────

def test_get_osv_cache_entry_hit(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder=recorder, fetchone_result=([{"id": "CVE-1"}],))
    result = db.get_osv_cache_entry("PyPI", "requests", "2.6.0", max_age_hours=24)
    assert result == [{"id": "CVE-1"}]
    sql, params = recorder[0]
    assert params == ("PyPI", "requests", "2.6.0", 24)


def test_get_osv_cache_entry_miss_returns_none(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder=recorder, fetchone_result=None)
    assert db.get_osv_cache_entry("PyPI", "requests", "2.6.0") is None


def test_put_osv_cache_entry_upserts(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder=recorder)
    db.put_osv_cache_entry("PyPI", "requests", "2.6.0", [{"id": "CVE-1"}])
    sql, params = recorder[0]
    assert "INSERT INTO observability.osv_cache" in sql
    assert "ON CONFLICT (ecosystem, package_name, version) DO UPDATE" in sql
    assert params[0] == "PyPI" and params[1] == "requests" and params[2] == "2.6.0"
