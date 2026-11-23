#!/usr/bin/env python3
"""
Reporting subsystem phase 0: dataset catalog, query compiler, manifest, API.
No database needed (execute() is faked).

    pytest test_reporting.py -v
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

import reporting_catalog as rc
import reporting_endpoints as re_


def q(**spec):
    spec.setdefault("dataset", "controls")
    return rc.compile_query(spec)


# ── compiler: happy paths ───────────────────────────────────────────────────

def test_simple_select_is_parameterised():
    cq = q(columns=["control_ref", "name"],
           filters=[{"column": "framework", "op": "eq", "value": "SOX"}],
           order_by=[{"column": "control_ref", "dir": "asc"}], limit=10)
    assert cq.sql == ('SELECT "control_ref", "name" FROM (' + cq.dataset.source_sql + ') AS t '
                      'WHERE "framework" = %s ORDER BY "control_ref" ASC LIMIT %s')
    assert cq.params == ["SOX", 10]
    assert "SOX" not in cq.sql


def test_group_by_with_aggregates():
    cq = q(columns=["category"], group_by=["category"],
           aggregates=[{"fn": "count", "column": "*", "as": "n"}],
           order_by=[{"column": "n", "dir": "desc"}])
    assert 'COUNT(*) AS "n"' in cq.sql and 'GROUP BY "category"' in cq.sql
    assert [c["name"] for c in cq.output_columns] == ["category", "n"]


def test_numeric_aggregates_and_filters():
    cq = rc.compile_query({"dataset": "risk_scores", "columns": ["ticker"], "group_by": ["ticker"],
                           "aggregates": [{"fn": "avg", "column": "score", "as": "avg_score"}],
                           "filters": [{"column": "score", "op": "between", "value": [3, "7.5"]},
                                       {"column": "rag_status", "op": "in", "value": ["RED", "AMBER"]},
                                       {"column": "run_at", "op": "gte", "value": "2026-01-01T00:00:00Z"}]})
    assert cq.params[:2] == [3, 7.5] and cq.params[2:4] == ["RED", "AMBER"]
    assert 'AVG("score") AS "avg_score"' in cq.sql
    assert "IN (%s, %s)" in cq.sql


def test_limit_is_capped_at_the_dataset_maximum():
    cq = q(columns=["name"], limit=10_000_000)
    assert cq.params[-1] == cq.dataset.max_rows


def test_default_limit_applies():
    assert q(columns=["name"]).params[-1] == rc.get_dataset("controls").default_rows


def test_contains_escapes_wildcards():
    cq = q(columns=["name"], filters=[{"column": "name", "op": "contains", "value": "50%_off"}])
    assert cq.params[0] == "%50\\%\\_off%"


# ── compiler: rejections ────────────────────────────────────────────────────

@pytest.mark.parametrize("spec", [
    {"dataset": "nope", "columns": ["x"]},
    {"dataset": "controls"},                                                    # nothing selected
    {"dataset": "controls", "columns": ["secret"]},                             # unknown column
    {"dataset": "controls", "columns": ["name"], "filters": [{"column": "name", "op": "gt", "value": "a"}]},
    {"dataset": "controls", "columns": ["name"], "filters": [{"column": "name", "op": "eq; DROP", "value": "a"}]},
    {"dataset": "controls", "columns": ["name"], "filters": [{"column": "nope", "op": "eq", "value": "a"}]},
    {"dataset": "controls", "columns": ["name"], "aggregates": [{"fn": "count", "column": "*"}]},  # name not grouped
    {"dataset": "controls", "aggregates": [{"fn": "sum", "column": "name"}]},   # sum of a string
    {"dataset": "controls", "aggregates": [{"fn": "avg", "column": "*"}]},
    {"dataset": "controls", "aggregates": [{"fn": "count", "column": "*", "as": "bad alias"}]},
    {"dataset": "controls", "aggregates": [{"fn": "exec", "column": "*"}]},
    {"dataset": "controls", "columns": ["name"], "order_by": [{"column": "description", "dir": "asc"}]},
    {"dataset": "controls", "columns": ["name"], "order_by": [{"column": "name", "dir": "sideways"}]},
    {"dataset": "controls", "columns": ["name"], "limit": 0},
    {"dataset": "controls", "columns": ["name"], "limit": True},
    {"dataset": "risk_scores", "columns": ["ticker"], "filters": [{"column": "score", "op": "eq", "value": "abc"}]},
    {"dataset": "risk_scores", "columns": ["ticker"], "filters": [{"column": "score", "op": "eq", "value": "nan"}]},
    {"dataset": "risk_scores", "columns": ["ticker"], "filters": [{"column": "run_at", "op": "gt", "value": "yesterday"}]},
    {"dataset": "risk_scores", "columns": ["ticker"], "filters": [{"column": "score", "op": "between", "value": [1]}]},
    {"dataset": "controls", "columns": ["name"], "filters": [{"column": "name", "op": "in", "value": []}]},
    {"dataset": "controls", "columns": ["name"], "filters": "name=1"},
    "not a dict",
])
def test_invalid_specs_are_rejected(spec):
    with pytest.raises(rc.QueryError):
        rc.compile_query(spec)


def test_admin_only_dataset_is_restricted():
    with pytest.raises(rc.QueryError, match="restricted"):
        rc.compile_query({"dataset": "audit_log", "columns": ["action"]}, is_admin=False)
    assert rc.compile_query({"dataset": "audit_log", "columns": ["action"]}, is_admin=True)
    assert "audit_log" not in [d.key for d in rc.list_datasets(is_admin=False)]
    assert "audit_log" in [d.key for d in rc.list_datasets(is_admin=True)]


@pytest.mark.parametrize("payload", ["x'; DROP TABLE controls_library; --", '" OR 1=1 --', "%' OR '1'='1"])
def test_injection_attempts_stay_in_parameters(payload):
    cq = q(columns=["name"], filters=[{"column": "name", "op": "eq", "value": payload}])
    assert payload not in cq.sql
    assert payload in cq.params
    assert cq.sql.count("%s") == len(cq.params)


def test_injection_via_identifiers_is_rejected():
    for bad in ['name"; DROP TABLE x; --', "name) FROM controls_library; --", "name OR 1=1"]:
        with pytest.raises(rc.QueryError):
            q(columns=[bad])
        with pytest.raises(rc.QueryError):
            q(columns=["name"], order_by=[{"column": bad, "dir": "asc"}])


def test_every_registered_dataset_is_well_formed():
    for ds in rc.list_datasets(is_admin=True):
        assert ds.columns and ds.source_tables
        cols = ", ".join(c.name for c in ds.columns)
        cq = rc.compile_query({"dataset": ds.key, "columns": [c.name for c in ds.columns]}, is_admin=True)
        assert cq.sql.startswith("SELECT ") and cols.split(", ")[0] in cq.sql
        # the source SQL must expose every declared column by name
        for c in ds.columns:
            assert c.name in ds.source_sql, f"{ds.key}.{c.name} not in source_sql"


def test_registration_rejects_bad_definitions():
    with pytest.raises(ValueError):
        rc.Column("Bad-Name", "x")
    with pytest.raises(ValueError):
        rc.Column("ok", "x", "blob")
    with pytest.raises(ValueError):
        rc.Dataset("k", "l", "d", "1", "SELECT '%' AS a", (rc.Column("a", "a"),))
    with pytest.raises(ValueError):
        rc.register(rc.get_dataset("controls"))


# ── manifest / reperformance ────────────────────────────────────────────────

def test_same_spec_compiles_to_identical_sql_and_params():
    spec = {"dataset": "controls", "columns": ["name"],
            "filters": [{"column": "framework", "op": "eq", "value": "SOX"}]}
    a, b = rc.compile_query(spec), rc.compile_query(dict(spec))
    assert (a.sql, a.params, a.spec) == (b.sql, b.params, b.spec)


def test_manifest_contents_and_hash_are_reproducible():
    cq = q(columns=["control_ref"], limit=5)
    rows = [{"control_ref": "AC-01"}, {"control_ref": "AC-02"}]
    when = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    m = rc.build_manifest(cq, rows, as_of=when)
    assert m["sql"] == cq.sql and m["params"] == cq.params and m["spec"] == cq.spec
    assert m["dataset"] == {"key": "controls", "version": "1", "source_tables": ["controls_library"]}
    assert m["row_count"] == 2 and m["as_of"] == when.isoformat() and m["manifest_version"] == 1
    expected = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert m["result_sha256"] == expected
    assert rc.build_manifest(cq, rows, as_of=when) == m


def test_result_hash_changes_when_a_value_changes_and_ignores_key_order():
    cq = q(columns=["name"])
    h = lambda rows: rc.build_manifest(cq, rows)["result_sha256"]  # noqa: E731
    assert h([{"a": 1, "b": 2}]) == h([{"b": 2, "a": 1}])
    assert h([{"a": 1}]) != h([{"a": 2}])
    assert h([{"a": 1}, {"a": 2}]) != h([{"a": 2}, {"a": 1}])      # row order is part of the result


def test_decimals_and_datetimes_hash_stably():
    cq = q(columns=["name"])
    rows = [{"n": Decimal("1.50"), "t": datetime(2026, 1, 1, tzinfo=timezone.utc)}]
    assert rc.build_manifest(cq, rows)["result_sha256"] == rc.build_manifest(cq, rows)["result_sha256"]


# ── API ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def api(monkeypatch):
    audits = []
    monkeypatch.setattr(re_.db, "insert_audit_log_entry",
                        lambda *a, **k: audits.append({"args": a, **k}) or 1)
    monkeypatch.setattr(re_.db, "is_available", lambda: True)
    monkeypatch.setattr(re_, "execute", lambda cq: [{"control_ref": "AC-01"}])
    return audits


USER = {"username": "alice", "role": "user"}
ADMIN = {"username": "root", "role": "admin"}


def test_query_returns_rows_and_manifest_and_audits_success(api):
    out = re_.run_query(re_.QueryRequest(spec={"dataset": "controls", "columns": ["control_ref"]}), USER)
    assert out["rows"] == [{"control_ref": "AC-01"}]
    assert out["manifest"]["row_count"] == 1 and len(out["manifest"]["result_sha256"]) == 64
    assert api[0]["outcome"] == "success" and api[0]["target"] == "controls"
    assert api[0]["detail"]["result_sha256"] == out["manifest"]["result_sha256"]


def test_invalid_query_is_422_and_audited_as_failure(api):
    with pytest.raises(HTTPException) as ei:
        re_.run_query(re_.QueryRequest(spec={"dataset": "controls", "columns": ["nope"]}), USER)
    assert ei.value.status_code == 422
    assert api[0]["outcome"] == "failure" and api[0]["detail"]["reason"] == "invalid_query"


def test_restricted_dataset_is_audited_as_denied(api):
    with pytest.raises(HTTPException):
        re_.run_query(re_.QueryRequest(spec={"dataset": "audit_log", "columns": ["action"]}), USER)
    assert api[0]["outcome"] == "denied"
    assert re_.run_query(re_.QueryRequest(spec={"dataset": "audit_log", "columns": ["action"]}), ADMIN)


def test_execution_error_is_500_without_leaking_details(api, monkeypatch):
    def boom(cq):
        raise RuntimeError("relation \"secret_table\" does not exist")
    monkeypatch.setattr(re_, "execute", boom)
    with pytest.raises(HTTPException) as ei:
        re_.run_query(re_.QueryRequest(spec={"dataset": "controls", "columns": ["name"]}), USER)
    assert ei.value.status_code == 500 and "secret_table" not in ei.value.detail
    assert api[-1]["outcome"] == "failure" and api[-1]["detail"]["reason"] == "execution_error"


def test_dataset_listing_hides_restricted_datasets_from_non_admins(api):
    keys = [d["key"] for d in re_.list_datasets(USER)["datasets"]]
    assert "controls" in keys and "audit_log" not in keys
    with pytest.raises(HTTPException) as ei:
        re_.get_dataset("audit_log", USER)
    assert ei.value.status_code == 404
    assert re_.get_dataset("audit_log", ADMIN)["key"] == "audit_log"


def test_truncation_is_flagged_when_the_limit_is_reached(api, monkeypatch):
    monkeypatch.setattr(re_, "execute", lambda cq: [{"control_ref": f"C-{i}"} for i in range(3)])
    out = re_.run_query(re_.QueryRequest(spec={"dataset": "controls", "columns": ["control_ref"], "limit": 3}), USER)
    assert out["manifest"]["truncated"] is True
