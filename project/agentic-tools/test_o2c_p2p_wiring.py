#!/usr/bin/env python3
"""
Tests for the Order-to-Cash / Procure-to-Pay real-producer wiring: new
EventTypes (UBO/models/uro.py), _detect_system_flags + _SOURCE_EVENT_TO_
PAC_PROCESS (mcp_governance.py), Bronze's _FLAG_EVENT_MAP (UBO/pipeline/
bronze.py), Silver's erp_transaction_detail spread (UBO/pipeline/silver.py),
the flattened Rego field references (pac_endpoints.py), and
generate_o2c_p2p_synthetic_log.py's generator/push functions.

The load-bearing test runs a real system_telemetry-shaped row through the
REAL bronze -> silver -> gold -> council -> PaC pipeline exactly as
mcp_governance._process_one does (only the DB boundary and LLM/PaC-adjacent
calls unrelated to this wiring are faked, matching test_mcp_governance_
adjudication.py's established pattern) and asserts a real policy_violations
hit — proving these two Rego packages are no longer dead by construction.

    pytest test_o2c_p2p_wiring.py -v
"""
from __future__ import annotations

import asyncio
import json

import pytest

import db
import generate_o2c_p2p_synthetic_log as gen
import mcp_governance as mg
from UBO.models.uro import EventType


# ── _detect_system_flags: each new flag ─────────────────────────────────────

@pytest.mark.parametrize("flag", [
    "revenue_recognition_event", "sales_order_credit_event", "billing_event",
    "cash_application_event", "customer_master_change", "ar_aging_event",
    "purchase_order_event", "invoice_match_event", "vendor_master_change",
    "payment_run_event", "procurement_sod_conflict",
])
def test_detect_system_flags_sets_each_new_flag(flag):
    result = mg._detect_system_flags({
        "action": "x", "resource": "y", "severity": "INFO",
        "event_type": "x", "payload": {flag: True},
    })
    assert flag in result


# ── EventType values exist ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "REVENUE_RECOGNITION_EVENT", "SALES_ORDER_CREDIT_EVENT", "BILLING_EVENT",
    "CASH_APPLICATION_EVENT", "CUSTOMER_MASTER_CHANGE", "AR_AGING_EVENT",
    "PURCHASE_ORDER_EVENT", "INVOICE_MATCH_EVENT", "PAYMENT_RUN_EVENT",
    "PROCUREMENT_SOD_CONFLICT",
])
def test_event_type_exists(name):
    assert EventType(name).value == name


# ── Routing: _SOURCE_EVENT_TO_PAC_PROCESS ────────────────────────────────────

@pytest.mark.parametrize("event_type,expected_process", [
    ("REVENUE_RECOGNITION_EVENT", "order_to_cash"),
    ("SALES_ORDER_CREDIT_EVENT", "order_to_cash"),
    ("BILLING_EVENT", "order_to_cash"),
    ("CASH_APPLICATION_EVENT", "order_to_cash"),
    ("CUSTOMER_MASTER_CHANGE", "order_to_cash"),
    ("AR_AGING_EVENT", "order_to_cash"),
    ("PURCHASE_ORDER_EVENT", "procure_to_pay"),
    ("INVOICE_MATCH_EVENT", "procure_to_pay"),
    ("VENDOR_MASTER_CHANGE", "procure_to_pay"),
    ("PAYMENT_RUN_EVENT", "procure_to_pay"),
    ("PROCUREMENT_SOD_CONFLICT", "procure_to_pay"),
])
def test_source_event_to_pac_process_routing(event_type, expected_process):
    assert mg._SOURCE_EVENT_TO_PAC_PROCESS[("SYSTEM_TELEMETRY", event_type)] == expected_process


# ── Load-bearing: real pipeline, real policy_violations ──────────────────────
# Same fake-DB-boundary shape as test_mcp_governance_adjudication.py /
# test_graph_architect_enrichment.py.

class _FakeCursor:
    def __init__(self, recorder):
        self._recorder = recorder

    def execute(self, sql, params=None):
        self._recorder.append((sql, params))

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
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

    def __exit__(self, *a):
        return False


@pytest.fixture
def recorder(monkeypatch):
    rows: list[tuple[str, tuple]] = []
    monkeypatch.setattr(mg.db, "is_available", lambda: True)
    monkeypatch.setattr(mg.db, "get_conn", lambda: _FakeConnCtx(rows))
    monkeypatch.setattr(mg, "_check_suppressed", lambda row: False)
    monkeypatch.setattr(mg, "_llm_council_opinion", lambda uro, adj: None)
    # NOTE: _evaluate_pac_policy is NOT stubbed here — the real Rego
    # evaluation (heuristic simulator, since no opa binary in this test
    # environment) is exactly the thing under test.
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


def _system_telemetry_row(row_id: int, event_type: str, flag: str, detail: dict, actor: str = "jsmith@acme-corp.com") -> dict:
    return {
        "id": row_id,
        "_origin": "system",
        "actor": actor,
        "action": "synthetic_txn",
        "resource": "PO-9999",
        "severity": "HIGH",
        "risk_flags": [flag],
        "event_type": event_type,
        "server_name": "synthetic-o2c-p2p-generator",
        "system_type": "oracle_fusion",
        "erp_transaction_detail": detail,
    }


def test_purchase_order_over_threshold_produces_real_policy_violation(monkeypatch, recorder):
    """The load-bearing test: a PO over the $50K VP-approval threshold with
    no VP sign-off, run through the real pipeline, must land in
    adjudicated_tool_calls.policy_violations as P2P-P001 — proving the
    routing + Silver's erp_transaction_detail spread actually connect end to
    end (mutation-tested: commenting out that spread breaks this test).

    This does NOT reliably catch every field-reference regression on its
    own, though — no opa binary is available in this dev/CI environment
    (pac_endpoints._find_opa_binary() returns None here), so this exercises
    the heuristic fallback evaluator, which treats a field that's simply
    ABSENT (e.g. because a rule still referenced the old nested
    input.purchase_order.* root) as trivially satisfying a `not input.X`
    condition — the same "absence looks like satisfaction" blind spot that
    made this whole class of bug invisible before pac_contracts.py's static
    Layer-1 check existed. Confirmed by mutation-testing: reverting
    po_vp_approved's flattening did NOT fail this test, but DID fail
    test_pac_contracts.py::test_procure_to_pay_is_no_longer_dead_by_root_
    except_one_documented_gap. That static check — not this dynamic one —
    is the reliable regression guard for root/field-name mistakes; this
    test's job is proving the routing and data-flow wiring itself."""
    row = _system_telemetry_row(
        201, "PURCHASE_ORDER_EVENT", "purchase_order_event",
        {"po_total": 68000, "po_vp_approved": False, "po_number": "PO-9999",
         "po_cfo_approved": False, "po_type": "standard", "po_annual_review_completed": True},
    )
    row["raw_payload"] = {"erp_transaction_detail": row.pop("erp_transaction_detail")}

    result = asyncio.run(mg._process_one(row))

    assert result is True
    params = _insert_params(recorder)
    assert "P2P-P001" in _field(params, "policy_violations")


def test_fully_compliant_purchase_order_produces_no_policy_violation(monkeypatch, recorder):
    row = _system_telemetry_row(
        202, "PURCHASE_ORDER_EVENT", "purchase_order_event",
        {"po_total": 30000, "po_vp_approved": True, "po_number": "PO-8888",
         "po_cfo_approved": True, "po_type": "standard", "po_annual_review_completed": True},
    )
    row["raw_payload"] = {"erp_transaction_detail": row.pop("erp_transaction_detail")}

    result = asyncio.run(mg._process_one(row))

    assert result is True
    params = _insert_params(recorder)
    assert "P2P-P001" not in _field(params, "policy_violations")


def test_customer_master_change_uses_real_uppercase_event_type_literal(monkeypatch, recorder):
    """Regression guard for the exact bug shape this whole effort exists to
    catch (pac_contracts.py's module docstring): a Rego rule comparing
    input.event.type against a lowercase literal while the real EventType
    value is uppercase. OTC-P005 must actually fire for a real
    CUSTOMER_MASTER_CHANGE event with an unapproved sensitive-field change."""
    row = _system_telemetry_row(
        203, "CUSTOMER_MASTER_CHANGE", "customer_master_change",
        {"field": "bank_account", "dual_approved": False, "customer_name": "Northwind Traders"},
    )
    row["raw_payload"] = {"erp_transaction_detail": row.pop("erp_transaction_detail")}

    result = asyncio.run(mg._process_one(row))

    assert result is True
    params = _insert_params(recorder)
    assert "OTC-P005" in _field(params, "policy_violations")


# ── Generator ─────────────────────────────────────────────────────────────────

def test_generate_produces_requested_count():
    records = gen.generate(count=50, violation_rate=0.2, days=10, seed=1)
    assert len(records) == 50


def test_generate_is_reproducible_with_a_seed():
    """event_id is deliberately NOT seed-derived (it's a true uuid4, so a
    reused --seed across separate real runs can't collide on the DB's
    (server_name, event_id) uniqueness constraint) — reproducibility is
    about which transactions get generated, not their identifiers."""
    a = gen.generate(count=20, violation_rate=0.15, days=30, seed=7)
    b = gen.generate(count=20, violation_rate=0.15, days=30, seed=7)
    key = lambda records: [(r["event_type"], r["action"], r["resource"], r["severity"], r["payload"]) for r in records]
    assert key(a) == key(b)


def test_generate_spreads_timestamps_within_the_requested_window():
    from datetime import datetime, timezone
    records = gen.generate(count=100, violation_rate=0.15, days=7, seed=3)
    now = datetime.now(timezone.utc)
    for r in records:
        age_days = (now - r["created_at"]).total_seconds() / 86400
        assert 0 <= age_days <= 7.01


def test_generate_violation_rate_is_approximately_respected():
    records = gen.generate(count=2000, violation_rate=0.15, days=30, seed=11)
    violating = sum(1 for r in records if r["severity"] == "HIGH")
    rate = violating / len(records)
    assert 0.10 <= rate <= 0.20


def test_push_calls_ingest_with_backdated_created_at(monkeypatch):
    captured = []

    def _fake_ingest(*args, **kwargs):
        captured.append((args, kwargs))
        return 1

    monkeypatch.setattr(gen.mg, "_ingest_system_event", _fake_ingest)
    records = gen.generate(count=3, violation_rate=0.5, days=5, seed=2)

    result = gen.push(records)

    assert result["ingested"] == 3
    for (args, kwargs), record in zip(captured, records):
        assert kwargs["created_at"] == record["created_at"]


# ── Corpus, gated on real OPA (mirrors test_pac_contracts.py's precedent) ───

def test_o2c_p2p_corpus_passes_real_opa_when_available():
    import pac_endpoints as pe
    import pac_negative_tests as pnt

    if not pe._find_opa_binary():
        return  # no OPA on this machine — nothing to prove here, not a failure
    for process in ("order_to_cash", "procure_to_pay"):
        result = pnt.run_corpus(process, pe._REGO_DEFAULTS[process])
        assert result["ok"], [r for r in result["results"] if not r["passed"]]
