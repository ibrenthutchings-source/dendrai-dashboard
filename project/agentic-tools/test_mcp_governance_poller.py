#!/usr/bin/env python3
"""
Integration tests for the MCP Governance poller loop itself
(mcp_governance._process_one / _process_batch / _check_suppressed) — as
distinct from test_mcp_governance_adjudication.py, which covers what happens
*after* a row reaches _write_adjudication. This file covers the listener
half: does a flagged telemetry row actually get picked up, correctly routed
through suppression / the real UBO pipeline / persistence, and does one bad
row in a batch take down the rest.

No prior test coverage existed for _process_one, _process_batch, or
_check_suppressed despite this being the core polling loop described in
README.md's architecture diagram and mcp_governance.py's own module
docstring ("Polls observability.mcp_telemetry every POLL_INTERVAL_S
seconds...").

    pytest test_mcp_governance_poller.py -v
"""

from __future__ import annotations

import asyncio

import mcp_governance as mg
from UBO.models.uro import PipelineStage
from UBO.models.risk_intelligence import AgentVerdict


# ── _check_suppressed — fake DB boundary with configurable fetchone ─────────

class _FakeCursor:
    def __init__(self, fetchone_result):
        self._fetchone_result = fetchone_result
        self.last_query = None

    def execute(self, sql, params=None):
        self.last_query = (sql, params)

    def fetchone(self):
        return self._fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, fetchone_result):
        self._fetchone_result = fetchone_result

    def cursor(self):
        return _FakeCursor(self._fetchone_result)

    def commit(self):
        pass


class _FakeConnCtx:
    def __init__(self, fetchone_result):
        self._fetchone_result = fetchone_result

    def __enter__(self):
        return _FakeConn(self._fetchone_result)

    def __exit__(self, *a):
        return False


def test_check_suppressed_true_when_a_matching_active_rule_exists(monkeypatch):
    monkeypatch.setattr(mg.db, "is_available", lambda: True)
    monkeypatch.setattr(mg.db, "get_conn", lambda: _FakeConnCtx((1,)))  # row found
    assert mg._check_suppressed({"target_tool": "get_financials", "server_name": "edgar"}) is True


def test_check_suppressed_false_when_no_matching_rule(monkeypatch):
    monkeypatch.setattr(mg.db, "is_available", lambda: True)
    monkeypatch.setattr(mg.db, "get_conn", lambda: _FakeConnCtx(None))  # no row
    assert mg._check_suppressed({"target_tool": "delete_all", "server_name": "edgar"}) is False


def test_check_suppressed_false_without_database(monkeypatch):
    monkeypatch.setattr(mg.db, "is_available", lambda: False)
    assert mg._check_suppressed({"target_tool": "x"}) is False


def test_check_suppressed_falls_back_to_action_for_system_telemetry_rows(monkeypatch):
    """system_telemetry rows have no target_tool column — action is the
    closest analog, per _check_suppressed's own docstring."""
    monkeypatch.setattr(mg.db, "is_available", lambda: True)
    conn = _FakeConnCtx((1,))
    monkeypatch.setattr(mg.db, "get_conn", lambda: conn)
    row = {"action": "vendor_soc2_expired", "server_name": "vendor-risk-sweep"}
    assert mg._check_suppressed(row) is True


# ── _process_one — suppression short-circuit ────────────────────────────────

def test_process_one_suppressed_row_never_touches_the_pipeline(monkeypatch):
    """A suppressed (known-good) call must be auto-cleared without invoking
    Bronze/Silver/Gold/Council at all — this is the whole point of the
    suppression allowlist (avoid paying for adjudication + review queue noise
    on calls already vetted as safe)."""
    monkeypatch.setattr(mg, "_check_suppressed", lambda row: True)
    stamped = {}
    monkeypatch.setattr(mg, "_stamp_processed_suppressed", lambda source_id, origin="mcp": stamped.update(
        source_id=source_id, origin=origin))

    def _pipeline_should_not_be_called():
        raise AssertionError("_get_pipeline() must not be called for a suppressed row")
    monkeypatch.setattr(mg, "_get_pipeline", _pipeline_should_not_be_called)

    row = {"id": 42, "_origin": "mcp", "session_id": "s-1", "target_tool": "get_financials", "server_name": "edgar"}
    result = asyncio.run(mg._process_one(row))

    assert result is True
    assert stamped == {"source_id": 42, "origin": "mcp"}


def test_process_one_returns_false_when_ubo_unavailable(monkeypatch):
    monkeypatch.setattr(mg, "_check_suppressed", lambda row: False)
    monkeypatch.setattr(mg, "_get_pipeline", lambda: (None, None, None, None))
    write_calls = []
    monkeypatch.setattr(mg, "_write_adjudication", lambda *a, **kw: write_calls.append(a))

    row = {"id": 1, "_origin": "mcp", "session_id": "s-1", "target_tool": "x", "risk_flags": ["bypass_keyword"]}
    result = asyncio.run(mg._process_one(row))

    assert result is False
    assert write_calls == []


# ── _process_one — real pipeline, mocked persistence ────────────────────────

def test_process_one_runs_the_real_pipeline_and_calls_write_adjudication(monkeypatch):
    """The core listener claim: a flagged mcp_telemetry row reaches a real
    Bronze -> Silver -> Gold -> Council run (not a stub), and the resulting
    ADJUDICATED URO is hand off to _write_adjudication for persistence.
    Only the DB write itself is mocked (recorder, not a live INSERT) —
    exactly the same boundary test_mcp_governance_adjudication.py mocks."""
    monkeypatch.setattr(mg, "_check_suppressed", lambda row: False)

    written = []
    def _fake_write(source_id, origin, session_id, uro):
        written.append({"source_id": source_id, "origin": origin, "session_id": session_id, "uro": uro})
    monkeypatch.setattr(mg, "_write_adjudication", _fake_write)

    row = {
        "id": 100, "_origin": "mcp", "session_id": "session-abc",
        "ts": "2026-08-01T12:00:00Z", "direction": "response", "method": "tools/call",
        "target_tool": "delete_production_data", "server_name": "internal-mcp",
        "risk_flags": ["bypass_keyword", "sensitive_tool"], "execution_time_ms": 120,
        "status": "ok", "error_message": None, "tool_args_hash": "abc123", "payload_hash": "def456",
    }
    result = asyncio.run(mg._process_one(row))

    assert result is True
    assert len(written) == 1
    w = written[0]
    assert w["source_id"] == 100
    assert w["origin"] == "mcp"
    assert w["session_id"] == "session-abc"
    uro = w["uro"]
    assert uro.pipeline_stage == PipelineStage.ADJUDICATED
    assert uro.adjudication is not None
    assert uro.adjudication.final_verdict in (AgentVerdict.ESCALATE, AgentVerdict.MONITOR, AgentVerdict.CLEAR)
    # 2 risk_flags -> MCP_TOOL_BYPASS takes priority in McpProxyBronzeHandler's
    # _FLAG_EVENT_MAP walk order (bypass_keyword before sensitive_tool).
    assert uro.event_type.value == "MCP_TOOL_BYPASS"


def test_process_one_system_origin_row_uses_system_telemetry_source(monkeypatch):
    monkeypatch.setattr(mg, "_check_suppressed", lambda row: False)
    written = []
    monkeypatch.setattr(mg, "_write_adjudication", lambda *a, **kw: written.append(a))

    row = {
        "id": 200, "_origin": "system", "created_at": "2026-08-01T12:00:00Z",
        "server_name": "sap-connector", "system_type": "sap_hana", "event_type": "sod_violation",
        "event_id": "sap-1", "actor": "jdoe", "action": "post_journal_entry", "resource": "GL-1001",
        "severity": "CRITICAL", "risk_flags": ["sod_violation"], "raw_payload": {"sod_violation": True},
    }
    result = asyncio.run(mg._process_one(row))

    assert result is True
    assert len(written) == 1
    source_id, origin, session_id, uro = written[0]
    assert origin == "system"
    assert session_id is None  # system_telemetry rows have no MCP session
    assert uro.source_system.value == "SYSTEM_TELEMETRY"


def test_process_one_pipeline_exception_is_caught_and_returns_false(monkeypatch):
    monkeypatch.setattr(mg, "_check_suppressed", lambda row: False)

    class _BoomBronze:
        async def ingest(self, *a, **kw):
            raise RuntimeError("simulated Bronze ingest failure")
    monkeypatch.setattr(mg, "_get_pipeline", lambda: (_BoomBronze(), object(), object(), object()))

    row = {"id": 300, "_origin": "mcp", "session_id": "s-1", "target_tool": "x", "risk_flags": ["bulk_args"]}
    result = asyncio.run(mg._process_one(row))  # must not raise
    assert result is False


# ── _process_batch — fan-out across both origins, resilient to failures ────

def test_process_batch_no_unprocessed_rows_is_a_clean_zero(monkeypatch):
    monkeypatch.setattr(mg, "_fetch_unprocessed", lambda batch_size: [])
    monkeypatch.setattr(mg, "_fetch_unprocessed_system", lambda batch_size: [])
    called = []
    monkeypatch.setattr(mg, "_process_one", lambda row: called.append(row))
    assert asyncio.run(mg._process_batch()) == 0
    assert called == []


def test_process_batch_counts_successes_across_both_origins(monkeypatch):
    monkeypatch.setattr(mg, "_fetch_unprocessed", lambda batch_size: [
        {"id": 1, "_origin": "mcp"}, {"id": 2, "_origin": "mcp"},
    ])
    monkeypatch.setattr(mg, "_fetch_unprocessed_system", lambda batch_size: [
        {"id": 3, "_origin": "system"},
    ])

    async def _fake_process_one(row):
        return row["id"] != 2  # row 2 "fails" (returns False), the others succeed
    monkeypatch.setattr(mg, "_process_one", _fake_process_one)

    n = asyncio.run(mg._process_batch())
    assert n == 2  # ids 1 and 3 succeeded, id 2 did not


def test_process_batch_one_row_raising_does_not_take_down_the_others(monkeypatch):
    """asyncio.gather(..., return_exceptions=True) — a single row throwing
    must not prevent the rest of the batch from being counted."""
    monkeypatch.setattr(mg, "_fetch_unprocessed", lambda batch_size: [
        {"id": 1, "_origin": "mcp"}, {"id": 2, "_origin": "mcp"}, {"id": 3, "_origin": "mcp"},
    ])
    monkeypatch.setattr(mg, "_fetch_unprocessed_system", lambda batch_size: [])

    async def _flaky_process_one(row):
        if row["id"] == 2:
            raise RuntimeError("simulated processing crash for row 2")
        return True
    monkeypatch.setattr(mg, "_process_one", _flaky_process_one)

    n = asyncio.run(mg._process_batch())
    assert n == 2  # rows 1 and 3 still counted despite row 2 raising
