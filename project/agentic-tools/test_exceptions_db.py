#!/usr/bin/env python3
"""
Unit tests for db.py's Exception Management curation/risk-rating/delegation
layer: insert_exception_event's new connector_id/assigned_owner/risk_rating
columns, list_pending_exceptions' filters/sort, list_pending_exceptions_grouped,
bulk_submit_exception_triage, get_exception_summary's new breakdowns,
escalate_stale_exceptions, and the JOURNAL_ENTRY exclusion applied across
every Exception-Management-only query over the shared tables.

db._conn() is faked at the boundary — no real database. A single fake
cursor supports MULTIPLE sequential execute() calls in one `with
conn.cursor()` block (functions like get_exception_summary/insert_exception_event
run several queries on one cursor), each with its own canned
fetchone/fetchall/rowcount/description, consumed in call order.

    pytest test_exceptions_db.py -v
"""

from __future__ import annotations

import db


class _Call:
    def __init__(self, fetchone=None, fetchall=None, rowcount=0, cols=None):
        self.fetchone_result = fetchone
        self.fetchall_result = fetchall or []
        self.rowcount = rowcount
        self.cols = cols or []


class _FakeCursor:
    def __init__(self, recorder, calls):
        self._recorder = recorder
        self._calls = list(calls)
        self._current = None

    def execute(self, sql, params=None):
        self._recorder.append((sql, params))
        self._current = self._calls.pop(0) if self._calls else _Call()
        self.rowcount = self._current.rowcount
        self.description = [(c,) for c in self._current.cols]

    def fetchone(self):
        return self._current.fetchone_result

    def fetchall(self):
        return list(self._current.fetchall_result)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, recorder, calls):
        self._recorder = recorder
        self._calls = calls

    def cursor(self):
        return _FakeCursor(self._recorder, self._calls)

    def commit(self):
        pass


class _FakeConnCtx:
    def __init__(self, recorder, calls):
        self._recorder = recorder
        self._calls = calls

    def __enter__(self):
        return _FakeConn(self._recorder, self._calls)

    def __exit__(self, *a):
        return False


def _patch(monkeypatch, recorder, calls):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, calls))


# ── insert_exception_event ───────────────────────────────────────────────────

def test_insert_exception_event_threads_new_columns(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchone=(101,)), _Call()])

    event_id = db.insert_exception_event(
        "je-1", "sap_hana", "P2P", "2026-08-01T00:00:00Z", {}, "exception-heuristic-v1",
        0.9, 0.2, True, connector_id=42, assigned_owner="treasury-team@acme.com",
        risk_rating="R", risk_score=20.0,
    )

    assert event_id == 101
    control_sql, control_params = recorder[0]
    assert "connector_id, assigned_owner" in control_sql
    assert control_params[-2:] == (42, "treasury-team@acme.com")
    inference_sql, inference_params = recorder[1]
    assert "risk_rating" in inference_sql
    assert "risk_score" in inference_sql
    assert inference_params[-2:] == ("R", 20.0)


def test_insert_exception_event_defaults_new_columns_to_none(monkeypatch):
    """je_testing_sweep.py calls this without the new kwargs — must not
    error, and must persist NULL rather than a fabricated owner/rating/score."""
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchone=(1,)), _Call()])

    db.insert_exception_event("JE-DUP-01", "oracle_fusion", None, "2026-08-01T00:00:00Z",
                               {}, "je-testing-v1", 1.0, 0.0, True)

    _, control_params = recorder[0]
    assert control_params[-2:] == (None, None)
    _, inference_params = recorder[1]
    assert inference_params[-2:] == (None, None)


# ── list_pending_exceptions ────────────────────────────────────────────────────

def test_list_pending_exceptions_excludes_journal_entry_rows(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_pending_exceptions()
    sql, _ = recorder[0]
    assert "JOURNAL_ENTRY" in sql


def test_list_pending_exceptions_orders_by_risk_rating_before_uncertainty(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_pending_exceptions()
    sql, _ = recorder[0]
    order_clause = sql.split("ORDER BY", 1)[1]
    assert order_clause.index("CASE mi.risk_rating") < order_clause.index("mi.uncertainty_score DESC")


def test_list_pending_exceptions_filters_by_risk_rating_and_owner(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_pending_exceptions(risk_rating="R", owner="treasury-team@acme.com")
    sql, params = recorder[0]
    assert "mi.risk_rating = %s" in sql
    assert "ce.assigned_owner = %s" in sql
    assert "R" in params and "treasury-team@acme.com" in params


def test_list_pending_exceptions_decodes_new_columns(monkeypatch):
    row = (1, "ctrl-1", "sap_hana", "P2P", None, {}, "jdoe", "post", "sod_violation",
           None, 7, 42, "treasury-team@acme.com", 9, "exception-heuristic-v1", 0.9, 0.6, "R", 20.0, None)
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[row])])
    result = db.list_pending_exceptions()
    assert result[0]["connector_id"] == 42
    assert result[0]["assigned_owner"] == "treasury-team@acme.com"
    assert result[0]["risk_rating"] == "R"
    assert result[0]["risk_score"] == 20.0


def test_list_pending_exceptions_decodes_null_risk_score_as_none(monkeypatch):
    """Legacy rows scored before risk_score existed — must stay None, not
    coerce to 0.0 (0.0 would be a real, very-low-but-scored risk)."""
    row = (1, "ctrl-1", "sap_hana", "P2P", None, {}, "jdoe", "post", "sod_violation",
           None, 7, 42, "treasury-team@acme.com", 9, "exception-heuristic-v1", 0.9, 0.6, "R", None, None)
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[row])])
    result = db.list_pending_exceptions()
    assert result[0]["risk_score"] is None


# ── list_pending_exceptions_grouped ──────────────────────────────────────────

def test_list_pending_exceptions_grouped_excludes_journal_entry_and_orders_by_worst_rating(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_pending_exceptions_grouped()
    sql, _ = recorder[0]
    assert "JOURNAL_ENTRY" in sql
    assert "GROUP BY ce.control_id, ce.system_source" in sql
    assert "ORDER BY worst_rating_order" in sql


def test_list_pending_exceptions_grouped_decodes_worst_rating_and_map_badge(monkeypatch):
    cols = ["control_id", "system_source", "occurrence_count", "worst_rating_order",
            "first_seen_at", "last_seen_at", "sample_event_id", "owner", "has_open_map", "map_ref"]
    row = ("ctrl-1", "sap_hana", 5, 0, None, None, 99, "treasury-team@acme.com", True, "MAP-CM-000042")
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[row], cols=cols)])

    result = db.list_pending_exceptions_grouped()

    assert result[0]["worst_risk_rating"] == "R"
    assert "worst_rating_order" not in result[0]
    assert result[0]["occurrence_count"] == 5
    assert result[0]["has_open_map"] is True
    assert result[0]["map_ref"] == "MAP-CM-000042"


def test_list_pending_exceptions_grouped_decodes_worst_risk_score(monkeypatch):
    cols = ["control_id", "system_source", "occurrence_count", "worst_rating_order", "worst_risk_score",
            "first_seen_at", "last_seen_at", "sample_event_id", "owner", "has_open_map", "map_ref"]
    row = ("ctrl-1", "sap_hana", 5, 0, 20.0, None, None, 99, "treasury-team@acme.com", True, "MAP-CM-000042")
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[row], cols=cols)])

    result = db.list_pending_exceptions_grouped()

    assert result[0]["worst_risk_score"] == 20.0


def test_list_pending_exceptions_grouped_filters_by_risk_rating_and_owner(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_pending_exceptions_grouped(risk_rating="A", owner="ops@acme.com")
    sql, params = recorder[0]
    assert "mi.risk_rating = %s" in sql
    assert "ce.assigned_owner = %s" in sql
    assert "A" in params and "ops@acme.com" in params


def test_list_pending_exceptions_grouped_scope_je_testing_includes_instead_of_excludes(monkeypatch):
    """JE Testing's grouped view (the 'unify the queue' UX-audit recommendation)
    reuses this exact query — scope='je_testing' flips the JOURNAL_ENTRY
    filter direction instead of forking a second SQL statement."""
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_pending_exceptions_grouped(scope="je_testing")
    sql, _ = recorder[0]
    assert "ce.event_type = 'JOURNAL_ENTRY'" in sql
    assert "IS DISTINCT FROM" not in sql
    assert "GROUP BY ce.control_id, ce.system_source" in sql


def test_list_pending_exceptions_grouped_default_scope_still_excludes_journal_entry(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_pending_exceptions_grouped()
    sql, _ = recorder[0]
    assert "IS DISTINCT FROM 'JOURNAL_ENTRY'" in sql


# ── bulk_submit_exception_triage ─────────────────────────────────────────────

def test_bulk_submit_exception_triage_rejects_invalid_label(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [])
    assert db.bulk_submit_exception_triage([1, 2], "auditor", "NOT_A_REAL_LABEL", None) == 0
    assert recorder == []  # never touched the DB


def test_bulk_submit_exception_triage_requires_notes_for_gated_labels(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [])
    assert db.bulk_submit_exception_triage([1, 2], "auditor", "TRUE_CONTROL_FAILURE", "") == 0
    assert recorder == []


def test_bulk_submit_exception_triage_empty_ids_returns_zero(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [])
    assert db.bulk_submit_exception_triage([], "auditor", "BENIGN_OPERATIONAL_NOISE", None) == 0
    assert recorder == []


def test_bulk_submit_exception_triage_happy_path(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(rowcount=3)])
    resolved = db.bulk_submit_exception_triage([1, 2, 3], "auditor@acme.com", "BENIGN_OPERATIONAL_NOISE", None)
    assert resolved == 3
    sql, params = recorder[0]
    assert "unnest(%s::bigint[])" in sql
    assert "ON CONFLICT (event_id) DO UPDATE" in sql
    assert params[0] == [1, 2, 3]


# ── get_exception_summary ────────────────────────────────────────────────────

def test_get_exception_summary_excludes_journal_entry_and_includes_new_breakdowns(monkeypatch):
    recorder = []
    calls = [
        _Call(fetchone=(4,)),                                        # pending count
        _Call(fetchall=[("BENIGN_OPERATIONAL_NOISE", 2)]),           # resolution mix
        _Call(fetchall=[("sap_hana", 3)]),                            # pending_by_system
        _Call(fetchall=[("treasury-team@acme.com", 3)]),              # pending_by_owner
        _Call(fetchall=[("R", 1), ("A", 3)]),                         # pending_by_risk_rating
        _Call(fetchone=(50,)),                                        # total_events
    ]
    _patch(monkeypatch, recorder, calls)

    summary = db.get_exception_summary()

    assert summary["pending_count"] == 4
    assert summary["pending_by_owner"] == {"treasury-team@acme.com": 3}
    assert summary["pending_by_risk_rating"] == {"R": 1, "A": 3}
    assert summary["total_events"] == 50
    for sql, _ in recorder:
        assert "JOURNAL_ENTRY" in sql


def test_get_exception_summary_no_db_returns_honest_zeroed_shape(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: False)
    summary = db.get_exception_summary()
    assert summary["pending_by_owner"] == {}
    assert summary["pending_by_risk_rating"] == {}


# ── detect_recurring_exceptions / list_exception_system_sources /
#    get_exception_score_history / list_exception_triage_history: JE exclusion ──

def test_detect_recurring_exceptions_excludes_journal_entry(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.detect_recurring_exceptions()
    sql, _ = recorder[0]
    assert "JOURNAL_ENTRY" in sql


def test_list_exception_system_sources_excludes_journal_entry(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_exception_system_sources()
    sql, _ = recorder[0]
    assert "JOURNAL_ENTRY" in sql


def test_get_exception_score_history_excludes_journal_entry(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.get_exception_score_history("sap_hana", "anomaly_score")
    sql, _ = recorder[0]
    assert "JOURNAL_ENTRY" in sql


def test_list_exception_triage_history_excludes_journal_entry(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_exception_triage_history()
    sql, _ = recorder[0]
    assert "JOURNAL_ENTRY" in sql


# ── escalate_stale_exceptions ─────────────────────────────────────────────────

def test_escalate_stale_exceptions_returns_rowcount_and_excludes_journal_entry(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(rowcount=7)])
    n = db.escalate_stale_exceptions(14)
    assert n == 7
    sql, params = recorder[0]
    assert "SET risk_rating = 'R'" in sql
    assert "JOURNAL_ENTRY" in sql
    assert params == (14,)


def test_escalate_stale_exceptions_no_db_returns_zero(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: False)
    assert db.escalate_stale_exceptions() == 0


# ── list_exceptions_report_grouped ───────────────────────────────────────────

def test_report_grouped_does_not_exclude_journal_entry(monkeypatch):
    """Unlike every other Exception Management query, the board report must
    include JE Testing rows — it's a period-of-everything-that-happened
    report, not the operational triage queue."""
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_exceptions_report_grouped("2026-08-01", "2026-08-31")
    sql, _ = recorder[0]
    assert "JOURNAL_ENTRY" not in sql
    assert "LEFT JOIN LATERAL" in sql  # not an inner join — JE rows have no inference row


def test_report_grouped_filters_by_date_range(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_exceptions_report_grouped("2026-08-01", "2026-08-31")
    sql, params = recorder[0]
    assert "ce.event_timestamp >= %s" in sql
    assert params == ("2026-08-01", "2026-08-31", 200)


def test_report_grouped_applies_a_real_limit_clause(monkeypatch):
    """Load-bearing, not a nicety — a busy period can produce tens of
    thousands of distinct control groups, each a candidate for an
    unvectorized FAIR Monte Carlo simulation upstream."""
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_exceptions_report_grouped("2026-08-01", "2026-08-31", limit=50)
    sql, params = recorder[0]
    assert "LIMIT %s" in sql
    assert params == ("2026-08-01", "2026-08-31", 50)


def test_report_grouped_decodes_worst_rating_and_literal_amount(monkeypatch):
    row = ("ITGC-AC-01", "sap_hana", "itgc", 3, 0, None, None, 1500.5, 1)
    cols = ["control_id", "system_source", "process", "occurrence_count", "worst_rating_order",
            "first_seen_at", "last_seen_at", "literal_amount_total", "unpriced_count"]
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[row], cols=cols)])
    result = db.list_exceptions_report_grouped("2026-08-01", "2026-08-31")
    assert result[0]["worst_risk_rating"] == "R"
    assert result[0]["literal_amount_total"] == 1500.5
    assert result[0]["unpriced_count"] == 1


def test_report_grouped_decodes_worst_risk_score(monkeypatch):
    row = ("ITGC-AC-01", "sap_hana", "itgc", 3, 0, 20.0, None, None, 1500.5, 1)
    cols = ["control_id", "system_source", "process", "occurrence_count", "worst_rating_order",
            "worst_risk_score", "first_seen_at", "last_seen_at", "literal_amount_total", "unpriced_count"]
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[row], cols=cols)])
    result = db.list_exceptions_report_grouped("2026-08-01", "2026-08-31")
    assert result[0]["worst_risk_score"] == 20.0


def test_report_grouped_worst_rating_unrated_when_no_inference(monkeypatch):
    row = ("JE-ROUND-DOLLAR", "oracle_fusion", "record_to_report", 2, 3, None, None, 900.0, 0)
    cols = ["control_id", "system_source", "process", "occurrence_count", "worst_rating_order",
            "first_seen_at", "last_seen_at", "literal_amount_total", "unpriced_count"]
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[row], cols=cols)])
    result = db.list_exceptions_report_grouped("2026-08-01", "2026-08-31")
    assert result[0]["worst_risk_rating"] is None


def test_report_grouped_no_db_returns_empty_list(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: False)
    assert db.list_exceptions_report_grouped("2026-08-01", "2026-08-31") == []


# ── list_exceptions_report_detail ────────────────────────────────────────────

def test_report_detail_does_not_exclude_journal_entry(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_exceptions_report_detail("2026-08-01", "2026-08-31")
    sql, _ = recorder[0]
    assert "JOURNAL_ENTRY" not in sql


def test_report_detail_filters_by_control_id_when_given(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_exceptions_report_detail("2026-08-01", "2026-08-31", control_id="ITGC-AC-01")
    sql, params = recorder[0]
    assert "ce.control_id = %s" in sql
    assert "ITGC-AC-01" in params


def test_report_detail_omits_control_filter_when_not_given(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchall=[])])
    db.list_exceptions_report_detail("2026-08-01", "2026-08-31")
    sql, _ = recorder[0]
    assert "ce.control_id = %s" not in sql


# ── count_exceptions_report_groups ───────────────────────────────────────────

def test_count_report_groups_returns_the_distinct_count(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [_Call(fetchone=(91503,))])
    assert db.count_exceptions_report_groups("2026-07-30", "2026-08-29") == 91503
    sql, params = recorder[0]
    assert "COUNT(DISTINCT" in sql
    assert params == ("2026-07-30", "2026-08-29")


def test_count_report_groups_no_db_returns_zero(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: False)
    assert db.count_exceptions_report_groups("2026-07-30", "2026-08-29") == 0


# ── get_exceptions_report_totals ──────────────────────────────────────────────

def test_report_totals_aggregates_across_all_exceptions(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [
        _Call(fetchone=(243612, {"sap_hana": 100000, "oracle_fusion": 143612})),
        _Call(fetchone=({"itgc": 243612},)),
        _Call(fetchone=({"R": 200000, "unrated": 43612},)),
    ])
    result = db.get_exceptions_report_totals("2026-07-30", "2026-08-29")
    assert result["total_occurrences"] == 243612
    assert result["by_system"] == {"sap_hana": 100000, "oracle_fusion": 143612}
    assert result["by_process"] == {"itgc": 243612}
    assert result["by_risk_rating"] == {"R": 200000, "unrated": 43612}


def test_report_totals_handles_no_rows_gracefully(monkeypatch):
    recorder = []
    _patch(monkeypatch, recorder, [
        _Call(fetchone=(0, None)),
        _Call(fetchone=(None,)),
        _Call(fetchone=(None,)),
    ])
    result = db.get_exceptions_report_totals("2026-07-30", "2026-08-29")
    assert result == {"total_occurrences": 0, "by_system": {}, "by_process": {}, "by_risk_rating": {}}


def test_report_totals_no_db_returns_empty_shape(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: False)
    result = db.get_exceptions_report_totals("2026-07-30", "2026-08-29")
    assert result == {"total_occurrences": 0, "by_system": {}, "by_process": {}, "by_risk_rating": {}}
