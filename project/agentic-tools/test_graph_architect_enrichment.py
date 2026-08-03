#!/usr/bin/env python3
"""
Proves mcp_governance._process_one's identity/role enrichment step actually
makes a difference: with real role data available, The Graph Architect's
existing, UNMODIFIED Single-Point-of-Failure check
(UBO/agents/graph_architect.py's `role_count >= _SPOF_ROLE_THRESHOLD`, still
20, untouched) fires for the first time in this pipeline — where before this
change it structurally could not, since every real-production Silver
conformer left role_count at 0.

Runs the REAL bronze -> silver -> (enrichment) -> gold -> council pipeline
exactly as _process_one does; only the DB boundary (db.is_available/get_conn,
plus the two new identity-lookup functions) and the LLM/PaC calls are faked,
matching test_mcp_governance_adjudication.py's established pattern.

    pytest test_graph_architect_enrichment.py -v
"""
from __future__ import annotations

import asyncio
import json

import pytest

import db
import mcp_governance as mg


# ── Fake DB boundary — same shape as test_mcp_governance_adjudication.py ────

class _FakeCursor:
    def __init__(self, recorder):
        self._recorder = recorder

    def execute(self, sql, params=None):
        self._recorder.append((sql, params))

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, recorder):
        self._recorder = recorder

    def cursor(self):
        return _FakeCursor(self._recorder)

    def commit(self):
        pass

    def rollback(self):
        pass


class _FakeConnCtx:
    def __init__(self, recorder):
        self._recorder = recorder

    def __enter__(self):
        return _FakeConn(self._recorder)

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def recorder(monkeypatch):
    rows: list[tuple[str, tuple]] = []
    monkeypatch.setattr(mg.db, "is_available", lambda: True)
    monkeypatch.setattr(mg.db, "get_conn", lambda: _FakeConnCtx(rows))
    # Not the subject under test — bypass exactly like the adjudication tests do.
    monkeypatch.setattr(mg, "_check_suppressed", lambda row: False)
    monkeypatch.setattr(mg, "_llm_council_opinion", lambda uro, adj: None)
    monkeypatch.setattr(mg, "_evaluate_pac_policy", lambda uro: None)
    return rows


def _insert_params(recorder) -> tuple:
    for sql, params in recorder:
        if "INSERT INTO observability.adjudicated_tool_calls" in sql:
            return params
    raise AssertionError("no adjudicated_tool_calls INSERT was recorded")


_COL = {
    name: i for i, name in enumerate([
        "telemetry_id", "system_telemetry_id", "session_id", "source_system",
        "target_tool", "server_name", "risk_flags", "execution_time_ms",
        "uro_id", "risk_score", "risk_tier",
        "final_verdict", "ai_final_verdict", "ensemble_confidence",
        "requires_human_review", "conflict_flags",
        "policy_violations", "adjudicator_reasoning",
        "council_votes",
    ])
}


def _field(params, name):
    return params[_COL[name]]


def _graph_architect_evidence(params) -> dict:
    votes = json.loads(_field(params, "council_votes"))
    for v in votes:
        if v["agent_name"] == "The Graph Architect":
            return v["evidence"]
    raise AssertionError("The Graph Architect did not vote — Council likely fast-pathed")


# A privilege-escalation event on a real enterprise identity — base weight
# 0.80 in Gold's scoring (UBO/pipeline/gold.py), comfortably clears
# COUNCIL_TIERS={"CRITICAL","HIGH","MEDIUM"} so the full agent swarm runs
# rather than the tier-gated fast-path auto-clear.
def _system_telemetry_row(row_id: int, actor: str) -> dict:
    return {
        "id": row_id,
        "_origin": "system",
        "actor": actor,
        "action": "assign_role",
        "resource": "finance-erp",
        "severity": "HIGH",
        "risk_flags": ["privileged_access"],
        "event_type": "privilege_escalation",
        "server_name": "oracle-fusion-prod",
        "system_type": "oracle_fusion",
    }


def test_spof_check_fires_when_real_role_data_present(monkeypatch, recorder):
    """The load-bearing test: an actor with 25 real role edges (above the
    unmodified _SPOF_ROLE_THRESHOLD=20) triggers The Graph Architect's SPoF
    signal — proving the previously-structurally-dead check is now alive,
    using agent code that is completely unchanged."""
    monkeypatch.setattr(db, "get_identity_role_count", lambda username: 25 if username == "alice@acme.com" else 0)
    monkeypatch.setattr(db, "get_identity_role_names", lambda username: [f"Role{i}" for i in range(25)])

    row = _system_telemetry_row(101, "alice@acme.com")
    result = asyncio.run(mg._process_one(row))

    assert result is True
    evidence = _graph_architect_evidence(_insert_params(recorder))
    assert evidence.get("spof_actor") is True


def test_spof_check_does_not_fire_below_threshold(monkeypatch, recorder):
    """Control case: an actor with role_count under the threshold must NOT
    trip the SPoF signal — proves the enrichment carries the real number
    through rather than always tripping the check regardless of value."""
    monkeypatch.setattr(db, "get_identity_role_count", lambda username: 3)
    monkeypatch.setattr(db, "get_identity_role_names", lambda username: ["Role1", "Role2", "Role3"])

    row = _system_telemetry_row(102, "bob@acme.com")
    result = asyncio.run(mg._process_one(row))

    assert result is True
    evidence = _graph_architect_evidence(_insert_params(recorder))
    assert "spof_actor" not in evidence


def test_enrichment_does_not_overwrite_a_conformer_that_already_set_role_count(monkeypatch, recorder):
    """If a Silver conformer ever does supply real role_count itself (e.g. a
    future real SailPoint entitlement pull), the enrichment's fallback must
    not clobber it with a possibly-stale identity-graph lookup. Simulated
    here by asserting the identity-graph lookup is never even called when
    role_count is already non-zero — this test targets _process_one
    directly since no current conformer sets role_count, so it monkeypatches
    silver.conform to simulate one that does."""
    import UBO.pipeline.silver as silver_module

    real_conform = silver_module.SilverConformationLayer.conform

    async def _conform_with_preset_role_count(self, uro):
        conformed = await real_conform(self, uro)
        if conformed.conformed_payload:
            conformed.conformed_payload.risk_indicators["role_count"] = 99
        return conformed

    monkeypatch.setattr(silver_module.SilverConformationLayer, "conform", _conform_with_preset_role_count)

    lookup_called = {"n": 0}

    def _should_not_be_called(username):
        lookup_called["n"] += 1
        return 1  # would fail the "no SPoF" expectation below if it were used

    monkeypatch.setattr(db, "get_identity_role_count", _should_not_be_called)

    row = _system_telemetry_row(103, "carol@acme.com")
    result = asyncio.run(mg._process_one(row))

    assert result is True
    assert lookup_called["n"] == 0
    evidence = _graph_architect_evidence(_insert_params(recorder))
    # role_count=99 (from the simulated conformer) is well above the
    # threshold — SPoF must fire from THAT value, confirming it passed
    # through untouched rather than being overwritten by the (unused) lookup.
    assert evidence.get("spof_actor") is True
