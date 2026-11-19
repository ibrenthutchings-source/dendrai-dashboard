#!/usr/bin/env python3
"""
Integration tests for mcp_governance._write_adjudication — the merge point
where the heuristic Council ensemble, the LLM 4th opinion, and the
Policy-as-Code veto combine into the verdict that gets persisted and shown
to a human reviewer.

Two claims made publicly about this system (README.md "Governing non-MCP AI
agents", gtm/one-pager.md, gtm/narrative-deck.md Slide 6, MODEL_CARD.md
"Adjudication Ensemble") have no prior test coverage anywhere in this repo:

    1. The LLM 4th opinion can only ever raise a verdict toward ESCALATE —
       it structurally cannot talk a verdict back down, even if it tries.
    2. A fired Policy-as-Code deny rule vetoes the ensemble outright to
       ESCALATE, regardless of how confident the heuristic ensemble was.

Both behaviors live inline in _write_adjudication (mcp_governance.py) rather
than as a separately callable pure function, and the function's first line
is `if not db.is_available(): return` — so exercising the real merge logic
requires a live-looking DB connection. Real Postgres is not required: only
the I/O boundary (db.is_available / db.get_conn) is faked here, with a
recorder capturing the exact SQL parameters _write_adjudication sends to the
INSERT. Everything else — URO construction, the Adjudicator's AdjudicationResult,
the merge conditionals themselves — is the real production code path.

_llm_council_opinion and _evaluate_pac_policy are stubbed at the call site
(not claude_client/pac_endpoints/OPA underneath them) because the risk-bearing
logic under test is what _write_adjudication does with their output, not how
that output gets produced — that's covered separately by claude_client's own
usage and by test_pac_contracts.py / test_pac_negative_tests.py's real-OPA
evaluation tests.

    pytest test_mcp_governance_adjudication.py -v
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import mcp_governance as mg
from UBO.models.uro import (
    URO, SourceSystem, EventType, ActorType, CloudEnvironment,
    RawPayload, ConformedPayload,
)
from UBO.models.risk_intelligence import (
    AgentVerdict, AgentEvaluation, AdjudicationResult, RiskTier, ConflictFlag,
)


# ── Fake DB boundary — records exactly what _write_adjudication would send ──

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
    """Patches db.is_available/db.get_conn so _write_adjudication runs its
    real merge logic and issues a real-shaped INSERT against a fake cursor
    that just records (sql, params) tuples instead of touching Postgres."""
    rows: list[tuple[str, tuple]] = []
    monkeypatch.setattr(mg.db, "is_available", lambda: True)
    monkeypatch.setattr(mg.db, "get_conn", lambda: _FakeConnCtx(rows))
    return rows


def _insert_params(recorder) -> tuple:
    for sql, params in recorder:
        if "INSERT INTO observability.adjudicated_tool_calls" in sql:
            return params
    raise AssertionError("no adjudicated_tool_calls INSERT was recorded")


# Column order _write_adjudication binds into the INSERT — see mcp_governance.py.
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


# ── URO / AdjudicationResult builders ────────────────────────────────────────

def _uro(*, source_system=SourceSystem.MCP_PROXY, event_type=EventType.MCP_SENSITIVE_TOOL_CALL,
          adjudication: AdjudicationResult) -> URO:
    u = URO(
        timestamp=datetime.now(timezone.utc),
        source_system=source_system,
        event_type=event_type,
        actor_id="agent-under-test",
        actor_type=ActorType.SERVICE,
        environment=CloudEnvironment(tags={"server_name": "test-server"}),
        raw_payload=RawPayload(content={"risk_flags": ["sensitive_tool"], "execution_time_ms": 120}),
        conformed_payload=ConformedPayload(resource_id="update_vendor_bank_details", action="execute"),
    )
    u = u.as_gold(score=adjudication.adjusted_risk_score, tier=adjudication.adjusted_risk_tier.value)
    return u.as_adjudicated(adjudication)


def _eval(agent_name, verdict, confidence=0.7, risk_delta=0.0, reasoning="test"):
    return AgentEvaluation(agent_name=agent_name, verdict=verdict, confidence=confidence,
                            risk_delta=risk_delta, reasoning=reasoning)


def _adjudication(*, final_verdict, requires_human_review, tier=RiskTier.MEDIUM, score=0.5,
                   conflict_flags=None) -> AdjudicationResult:
    return AdjudicationResult(
        uro_id="test-uro",
        final_verdict=final_verdict,
        adjusted_risk_score=score,
        adjusted_risk_tier=tier,
        evaluations=[
            _eval("The Quant", final_verdict),
            _eval("The Linguist", AgentVerdict.MONITOR),
            _eval("The Graph Architect", final_verdict),
        ],
        ensemble_confidence=0.7,
        requires_human_review=requires_human_review,
        conflict_flags=conflict_flags or [],
        conflict_reasoning="synthetic test fixture",
    )


# ── 1. LLM can raise MONITOR/CLEAR to ESCALATE ──────────────────────────────

def test_llm_reviewer_raises_monitor_to_escalate(monkeypatch, recorder):
    adj = _adjudication(final_verdict=AgentVerdict.MONITOR, requires_human_review=True,
                         conflict_flags=[ConflictFlag.LOW_CONFIDENCE])
    uro = _uro(adjudication=adj)

    monkeypatch.setattr(mg, "_llm_council_opinion", lambda uro, adj: {
        "agent_name": "The Reviewer (AI)", "verdict": "ESCALATE", "confidence": 0.9,
        "risk_delta": 0.1, "reasoning": "independent read disagrees", "evidence": {}, "evaluation_ms": 0,
    })
    monkeypatch.setattr(mg, "_evaluate_pac_policy", lambda uro: None)

    mg._write_adjudication(1, "mcp", "session-1", uro)

    params = _insert_params(recorder)
    assert _field(params, "final_verdict") == "ESCALATE"
    assert _field(params, "requires_human_review") is True
    assert "LLM_ESCALATION_OVERRIDE" in _field(params, "conflict_flags")
    votes = json.loads(_field(params, "council_votes"))
    assert any(v["agent_name"] == "The Reviewer (AI)" and v["verdict"] == "ESCALATE" for v in votes)


# ── 2. The core invariant: LLM cannot talk ESCALATE back down ───────────────

def test_llm_reviewer_cannot_downgrade_escalate(monkeypatch, recorder):
    """This is the claim in gtm/narrative-deck.md Slide 6 — 'it can only
    escalate. It can never talk a verdict down' — and MODEL_CARD.md's
    'a deliberate conservative bias, not an oversight'. The ensemble already
    escalated; the LLM tries to clear it; the written verdict must stay
    ESCALATE because nothing in _write_adjudication ever lowers final_verdict."""
    adj = _adjudication(final_verdict=AgentVerdict.ESCALATE, requires_human_review=True,
                         conflict_flags=[ConflictFlag.AGENT_DIVERGENCE])
    uro = _uro(adjudication=adj)

    monkeypatch.setattr(mg, "_llm_council_opinion", lambda uro, adj: {
        "agent_name": "The Reviewer (AI)", "verdict": "CLEAR", "confidence": 0.95,
        "risk_delta": -0.3, "reasoning": "looks like a false positive to me", "evidence": {}, "evaluation_ms": 0,
    })
    monkeypatch.setattr(mg, "_evaluate_pac_policy", lambda uro: None)

    mg._write_adjudication(2, "mcp", "session-2", uro)

    params = _insert_params(recorder)
    assert _field(params, "final_verdict") == "ESCALATE"
    assert _field(params, "requires_human_review") is True
    # No downgrade flag exists — a CLEAR opinion on an already-ESCALATE case
    # is recorded as a council vote (for audit visibility) but must not move
    # final_verdict or clear conflict_flags.
    assert "LLM_ESCALATION_OVERRIDE" not in _field(params, "conflict_flags")
    votes = json.loads(_field(params, "council_votes"))
    assert any(v["agent_name"] == "The Reviewer (AI)" and v["verdict"] == "CLEAR" for v in votes), (
        "the LLM's disagreement must still be recorded in council_votes for audit visibility "
        "even though it has no power to change the verdict"
    )


def test_llm_reviewer_cannot_downgrade_when_ensemble_already_monitor(monkeypatch, recorder):
    """Same invariant, other direction: ensemble is at MONITOR (not CLEAR),
    LLM votes CLEAR — must not move MONITOR down to CLEAR either. The merge
    logic only special-cases the ESCALATE case; anything else must pass
    through the ensemble's verdict unchanged."""
    adj = _adjudication(final_verdict=AgentVerdict.MONITOR, requires_human_review=True,
                         conflict_flags=[ConflictFlag.LOW_CONFIDENCE])
    uro = _uro(adjudication=adj)

    monkeypatch.setattr(mg, "_llm_council_opinion", lambda uro, adj: {
        "agent_name": "The Reviewer (AI)", "verdict": "CLEAR", "confidence": 0.9,
        "risk_delta": -0.2, "reasoning": "nothing here", "evidence": {}, "evaluation_ms": 0,
    })
    monkeypatch.setattr(mg, "_evaluate_pac_policy", lambda uro: None)

    mg._write_adjudication(3, "mcp", "session-3", uro)

    params = _insert_params(recorder)
    assert _field(params, "final_verdict") == "MONITOR"


# ── 3. LLM is only ever consulted on cases already flagged for human review ─

def test_llm_reviewer_not_consulted_when_not_flagged(monkeypatch, recorder):
    """MODEL_CARD.md: 'added only when the heuristic ensemble already set
    requires_human_review = TRUE'. A clean CLEAR verdict with no conflicts
    must never even call the LLM path — most events shouldn't pay for the
    extra API call, and an unflagged case has no opportunity to be escalated
    by a reviewer that was never invoked."""
    adj = _adjudication(final_verdict=AgentVerdict.CLEAR, requires_human_review=False)
    uro = _uro(adjudication=adj)

    calls = {"n": 0}

    def _should_not_be_called(uro, adj):
        calls["n"] += 1
        return {"agent_name": "The Reviewer (AI)", "verdict": "ESCALATE", "confidence": 1.0,
                "risk_delta": 0.0, "reasoning": "should never run", "evidence": {}, "evaluation_ms": 0}

    monkeypatch.setattr(mg, "_llm_council_opinion", _should_not_be_called)
    monkeypatch.setattr(mg, "_evaluate_pac_policy", lambda uro: None)

    mg._write_adjudication(4, "mcp", "session-4", uro)

    assert calls["n"] == 0
    params = _insert_params(recorder)
    assert _field(params, "final_verdict") == "CLEAR"
    assert _field(params, "requires_human_review") is False


# ── 4. Policy-as-Code veto forces ESCALATE regardless of ensemble score ─────

def test_pac_veto_forces_escalate_over_a_clear_ensemble(monkeypatch, recorder):
    """gtm/one-pager.md: 'A fired deny rule vetoes the ensemble outright and
    forces human review.' Ensemble is confidently CLEAR with no human review
    flagged; a Rego deny rule fires anyway; the persisted verdict must be
    ESCALATE with requires_human_review True — the veto overrides ensemble
    confidence, it doesn't just get appended as another opinion."""
    adj = _adjudication(final_verdict=AgentVerdict.CLEAR, requires_human_review=False)
    uro = _uro(adjudication=adj)

    monkeypatch.setattr(mg, "_llm_council_opinion", lambda uro, adj: None)
    monkeypatch.setattr(mg, "_evaluate_pac_policy", lambda uro: {
        "process": "procure_to_pay",
        "rules_fired": [{"rule": "vendor_bank_change_no_approver", "control_id": "VM-DENY-001"}],
        "engine": "opa",
    })

    mg._write_adjudication(5, "mcp", "session-5", uro)

    params = _insert_params(recorder)
    assert _field(params, "final_verdict") == "ESCALATE"
    assert _field(params, "requires_human_review") is True
    assert "POLICY_VIOLATION" in _field(params, "conflict_flags")
    assert "VM-DENY-001" in _field(params, "policy_violations")
    votes = json.loads(_field(params, "council_votes"))
    pac_vote = next(v for v in votes if v["agent_name"] == "Policy-as-Code (Rego)")
    assert pac_vote["verdict"] == "ESCALATE"
    assert pac_vote["confidence"] == 1.0  # engine == "opa", not the heuristic fallback


def test_no_pac_violation_and_no_llm_disagreement_leaves_ensemble_verdict_untouched(monkeypatch, recorder):
    """Control case — proves the merge logic doesn't escalate everything by
    default. Without a policy hit or an LLM disagreement, the ensemble's own
    verdict (here: ESCALATE, on its own merits) passes straight through."""
    adj = _adjudication(final_verdict=AgentVerdict.ESCALATE, requires_human_review=True)
    uro = _uro(adjudication=adj)

    monkeypatch.setattr(mg, "_llm_council_opinion", lambda uro, adj: {
        "agent_name": "The Reviewer (AI)", "verdict": "MONITOR", "confidence": 0.6,
        "risk_delta": 0.0, "reasoning": "agrees roughly", "evidence": {}, "evaluation_ms": 0,
    })
    monkeypatch.setattr(mg, "_evaluate_pac_policy", lambda uro: None)

    mg._write_adjudication(6, "mcp", "session-6", uro)

    params = _insert_params(recorder)
    assert _field(params, "final_verdict") == "ESCALATE"
    assert "LLM_ESCALATION_OVERRIDE" not in _field(params, "conflict_flags")
    assert "POLICY_VIOLATION" not in _field(params, "conflict_flags")


# ── 5. ai_final_verdict is a frozen snapshot, independent of human review ───

def test_ai_final_verdict_matches_final_verdict_at_write_time(monkeypatch, recorder):
    """db.py / MODEL_CARD.md: ai_final_verdict is a permanent snapshot of what
    the AI system decided, distinct from final_verdict (which a human can
    later overwrite via _human_review_adjudication). At write time — before
    any human has reviewed it — the two must agree, otherwise the frozen
    snapshot wouldn't be capturing the AI's actual decision."""
    adj = _adjudication(final_verdict=AgentVerdict.MONITOR, requires_human_review=True)
    uro = _uro(adjudication=adj)

    monkeypatch.setattr(mg, "_llm_council_opinion", lambda uro, adj: {
        "agent_name": "The Reviewer (AI)", "verdict": "ESCALATE", "confidence": 0.88,
        "risk_delta": 0.15, "reasoning": "independent escalation", "evidence": {}, "evaluation_ms": 0,
    })
    monkeypatch.setattr(mg, "_evaluate_pac_policy", lambda uro: None)

    mg._write_adjudication(7, "mcp", "session-7", uro)

    params = _insert_params(recorder)
    assert _field(params, "final_verdict") == _field(params, "ai_final_verdict") == "ESCALATE"


# ── 6. Framework-agnostic claim: the identical merge path serves non-MCP ────
# ── ("system_telemetry") events, not just MCP tool calls ────────────────────

def test_pac_veto_applies_identically_to_non_mcp_system_telemetry_origin(monkeypatch, recorder):
    """README.md 'Governing non-MCP AI agents': a LangChain/OpenAI/custom-loop
    agent's events arrive via the generic system_telemetry ingest path
    (origin='system'), not MCP. This proves it is the SAME _write_adjudication
    function and the SAME PaC-veto merge logic — not a parallel, lesser path —
    that governs it: a policy veto still forces ESCALATE, and the row is
    correctly attributed to system_telemetry rather than mcp_telemetry."""
    adj = _adjudication(final_verdict=AgentVerdict.CLEAR, requires_human_review=False)
    uro = _uro(source_system=SourceSystem.SYSTEM_TELEMETRY,
               event_type=EventType.SYSTEM_GOVERNANCE_VIOLATION, adjudication=adj)

    monkeypatch.setattr(mg, "_llm_council_opinion", lambda uro, adj: None)
    monkeypatch.setattr(mg, "_evaluate_pac_policy", lambda uro: {
        "process": "record_to_report",
        "rules_fired": [{"rule": "manual_je_over_threshold_no_approval", "control_id": "P-R2R-001"}],
        "engine": "opa",
    })

    # origin="system" — a LangChain callback / OpenAI function-calling wrapper /
    # custom loop posting to the generic ingestion endpoint, never touching MCP.
    mg._write_adjudication(9001, "system", None, uro)

    params = _insert_params(recorder)
    assert _field(params, "final_verdict") == "ESCALATE"
    assert _field(params, "requires_human_review") is True
    assert _field(params, "source_system") == "SYSTEM_TELEMETRY"
    assert _field(params, "telemetry_id") is None          # not an MCP row
    assert _field(params, "system_telemetry_id") == 9001    # FK into system_telemetry instead
