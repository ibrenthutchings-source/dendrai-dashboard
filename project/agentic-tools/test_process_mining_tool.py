#!/usr/bin/env python3
"""
Unit tests for process_mining_tool.py — variant analysis, conformance
checking, cycle-time/bottleneck stats, and rework detection over
case-tracked adjudications. Entirely pure: no DB needed, same testability
reasoning as test_control_flow_map.py's db._build_control_flow_map tests.

Fixture events use the same shape
db.get_recent_adjudications_for_domain_summary produces and the same
process_step labels generate_o2c_p2p_synthetic_log.py's _P2P_CASE/_O2C_CASE/
_INVENTORY_CASE emit, so a template drift between the two files would show
up here as a classify_case_process() miss.

    pytest test_process_mining_tool.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import process_mining_tool as pm

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ev(case_id, step, day_offset, verdict="CLEAR", violations=None, event_id=1):
    return {
        "id": event_id,
        "adjudicated_at": _BASE + timedelta(days=day_offset),
        "final_verdict": verdict,
        "risk_tier": "LOW",
        "source_system": "ORACLE_FUSION",
        "target_tool": None,
        "server_name": "oracle-fusion",
        "requires_human_review": verdict == "ESCALATE",
        "policy_violations": violations or [],
        "case_id": case_id,
        "process_step": step,
    }


def _clean_p2p_case(case_id="PO-1"):
    return [
        _ev(case_id, "Purchase Order Created", 0),
        _ev(case_id, "Invoice Matched", 3),
        _ev(case_id, "Payment Released", 6),
    ]


# ── build_cases ────────────────────────────────────────────────────────────────

def test_build_cases_groups_by_case_id_and_sorts_by_time():
    events = [
        _ev("A", "Payment Released", 5),
        _ev("A", "Purchase Order Created", 0),
        _ev("A", "Invoice Matched", 2),
    ]
    cases = pm.build_cases(events)
    assert list(cases.keys()) == ["A"]
    labels = [e["process_step"] for e in cases["A"]]
    assert labels == ["Purchase Order Created", "Invoice Matched", "Payment Released"]


def test_build_cases_ignores_events_without_case_id():
    events = [_ev(None, "Purchase Order Created", 0), _ev("B", "Purchase Order Created", 0)]
    events[0]["case_id"] = None
    cases = pm.build_cases(events)
    assert list(cases.keys()) == ["B"]


def test_build_cases_empty_input():
    assert pm.build_cases([]) == {}


# ── classify_case_process ────────────────────────────────────────────────────

def test_classify_case_process_matches_procure_to_pay():
    steps = ["Purchase Order Created", "Invoice Matched", "Payment Released"]
    assert pm.classify_case_process(steps) == "procure_to_pay"


def test_classify_case_process_matches_order_to_cash():
    steps = ["Sales Order Booked", "Invoice Billed", "Cash Applied"]
    assert pm.classify_case_process(steps) == "order_to_cash"


def test_classify_case_process_matches_receive_to_ship():
    steps = ["Goods Received", "Putaway Confirmed", "Goods Shipped"]
    assert pm.classify_case_process(steps) == "receive_to_ship"


def test_classify_case_process_none_for_standalone_kinds():
    # generate_o2c_p2p_synthetic_log.py's standalone kinds share no step
    # label with any template — must be "untemplated", not force-matched.
    assert pm.classify_case_process(["Revenue Recognized"]) is None
    assert pm.classify_case_process(["SoD Check"]) is None


def test_classify_case_process_empty_steps():
    assert pm.classify_case_process([]) is None


# ── conformance_check_case ───────────────────────────────────────────────────

def test_conformance_unscored_when_no_process_match():
    result = pm.conformance_check_case(["Revenue Recognized"], None)
    assert result["scored"] is False
    assert result["conforming"] is None


def test_conformance_conforming_case():
    steps = ["Purchase Order Created", "Invoice Matched", "Payment Released"]
    result = pm.conformance_check_case(steps, "procure_to_pay")
    assert result["scored"] is True
    assert result["conforming"] is True
    assert result["missing_steps"] == []
    assert result["out_of_order"] is False


def test_conformance_detects_missing_step():
    steps = ["Purchase Order Created", "Payment Released"]
    result = pm.conformance_check_case(steps, "procure_to_pay")
    assert result["conforming"] is False
    assert result["missing_steps"] == ["Invoice Matched"]
    assert result["out_of_order"] is False


def test_conformance_detects_out_of_order():
    steps = ["Payment Released", "Purchase Order Created", "Invoice Matched"]
    result = pm.conformance_check_case(steps, "procure_to_pay")
    assert result["conforming"] is False
    assert result["out_of_order"] is True
    assert result["missing_steps"] == []


def test_conformance_detects_repeated_step_as_non_conforming():
    steps = ["Purchase Order Created", "Invoice Matched", "Invoice Matched", "Payment Released"]
    result = pm.conformance_check_case(steps, "procure_to_pay")
    assert result["conforming"] is False
    assert result["repeated_steps"] == ["Invoice Matched"]
    # out-of-order is judged on first-occurrence order, which is still
    # correct here -- the rework, not the ordering, is what fails this case.
    assert result["out_of_order"] is False


def test_conformance_detects_extra_step():
    steps = ["Purchase Order Created", "Invoice Matched", "Payment Released", "Something Unexpected"]
    result = pm.conformance_check_case(steps, "procure_to_pay")
    assert result["conforming"] is False
    assert result["extra_steps"] == ["Something Unexpected"]


# ── variant_analysis ──────────────────────────────────────────────────────────

def test_variant_analysis_most_frequent_variant_is_happy_path():
    events = (
        _clean_p2p_case("A") + _clean_p2p_case("B") + _clean_p2p_case("C")
        + [_ev("D", "Purchase Order Created", 0), _ev("D", "Payment Released", 1)]  # deviant, skips a step
    )
    cases = pm.build_cases(events)
    variants = pm.variant_analysis(cases, "procure_to_pay")
    assert variants[0]["case_count"] == 3
    assert variants[0]["is_happy_path"] is True
    assert variants[0]["is_canonical"] is True
    assert variants[1]["case_count"] == 1
    assert variants[1]["is_happy_path"] is False


def test_variant_analysis_violation_rate():
    events = _clean_p2p_case("A") + [
        _ev("B", "Purchase Order Created", 0),
        _ev("B", "Invoice Matched", 1, verdict="ESCALATE", violations=["P2P-P002"]),
        _ev("B", "Payment Released", 2),
    ]
    cases = pm.build_cases(events)
    variants = pm.variant_analysis(cases, "procure_to_pay")
    # Both cases share the same (canonical) variant here, so violation_rate
    # should reflect exactly one of the two cases having a violation.
    assert len(variants) == 1
    assert variants[0]["case_count"] == 2
    assert variants[0]["violation_rate"] == 0.5


def test_variant_analysis_empty_cases():
    assert pm.variant_analysis({}) == []


# ── conformance_summary ───────────────────────────────────────────────────────

def test_conformance_summary_rate_and_breakdown():
    events = (
        _clean_p2p_case("A") + _clean_p2p_case("B")
        + [_ev("C", "Purchase Order Created", 0), _ev("C", "Payment Released", 1)]  # missing step
    )
    cases = pm.build_cases(events)
    summary = pm.conformance_summary(cases, "procure_to_pay")
    assert summary["scored_cases"] == 3
    assert summary["conforming_cases"] == 2
    assert summary["conformance_rate"] == round(2 / 3, 4)
    assert summary["deviation_breakdown"]["missing_step"] == 1
    assert len(summary["deviating_cases"]) == 1
    assert summary["deviating_cases"][0]["case_id"] == "C"


def test_conformance_summary_no_scored_cases_gives_none_rate():
    events = [_ev("R1", "Revenue Recognized", 0)]
    cases = pm.build_cases(events)
    summary = pm.conformance_summary(cases)
    assert summary["scored_cases"] == 0
    assert summary["conformance_rate"] is None


# ── cycle_time_stats ──────────────────────────────────────────────────────────

def test_cycle_time_stats_computes_edge_durations_and_bottleneck():
    # PO -> Invoice: 3 days (72h). Invoice -> Payment: 3 days (72h) for one
    # case and 9 days (216h) for another, so that edge should be flagged the
    # bottleneck (higher avg).
    events = _clean_p2p_case("A") + [
        _ev("B", "Purchase Order Created", 0),
        _ev("B", "Invoice Matched", 3),
        _ev("B", "Payment Released", 12),
    ]
    cases = pm.build_cases(events)
    stats = pm.cycle_time_stats(cases, "procure_to_pay")
    edge_by_pair = {(e["source"], e["target"]): e for e in stats["edges"]}
    assert edge_by_pair[("Purchase Order Created", "Invoice Matched")]["avg_hours"] == 72.0
    invoice_to_payment = edge_by_pair[("Invoice Matched", "Payment Released")]
    assert invoice_to_payment["count"] == 2
    assert invoice_to_payment["avg_hours"] == (72.0 + 216.0) / 2
    assert stats["bottleneck"]["source"] == "Invoice Matched"
    assert stats["case_duration"]["count"] == 2


def test_cycle_time_stats_no_cases():
    assert pm.cycle_time_stats({}, "procure_to_pay")["edges"] == []
    assert pm.cycle_time_stats({}, "procure_to_pay")["bottleneck"] is None


# ── rework_summary ────────────────────────────────────────────────────────────

def test_rework_summary_detects_repeated_steps():
    events = _clean_p2p_case("A") + [
        _ev("B", "Purchase Order Created", 0),
        _ev("B", "Invoice Matched", 1, verdict="ESCALATE", violations=["P2P-P002"]),
        _ev("B", "Invoice Matched", 2),  # rework: re-matched after a bounce
        _ev("B", "Payment Released", 4),
    ]
    cases = pm.build_cases(events)
    rw = pm.rework_summary(cases, "procure_to_pay")
    assert rw["total_cases"] == 2
    assert rw["reworked_cases"] == 1
    assert rw["rework_rate"] == 0.5
    assert rw["cases"][0]["case_id"] == "B"
    assert rw["cases"][0]["repeated_steps"] == ["Invoice Matched"]


def test_rework_summary_no_cases_gives_none_rate():
    rw = pm.rework_summary({})
    assert rw["total_cases"] == 0
    assert rw["rework_rate"] is None


# ── summary (one-shot overview) ──────────────────────────────────────────────

def test_summary_buckets_untemplated_and_per_process():
    events = _clean_p2p_case("A") + [_ev("R1", "Revenue Recognized", 0)]
    result = pm.summary(events)
    assert result["total_cases"] == 2
    assert result["untemplated_cases"] == 1
    assert "procure_to_pay" in result["processes"]
    assert result["processes"]["procure_to_pay"]["case_count"] == 1
    assert result["processes"]["procure_to_pay"]["conformance_rate"] == 1.0


def test_summary_empty_events():
    result = pm.summary([])
    assert result == {"total_cases": 0, "untemplated_cases": 0, "processes": {}}


# ── value_stream_map ──────────────────────────────────────────────────────────

def test_value_stream_map_splits_canonical_from_deviation_hours():
    # Clean case: both edges are consecutive template pairs -> all canonical.
    # Reworked case: an extra Invoice Matched -> Purchase Order Created loop
    # (not a template-consecutive pair) -> that edge is a deviation.
    events = _clean_p2p_case("A") + [
        _ev("B", "Purchase Order Created", 0),
        _ev("B", "Invoice Matched", 1),
        _ev("B", "Purchase Order Created", 2),  # loops back: deviation edge
        _ev("B", "Invoice Matched", 3),
        _ev("B", "Payment Released", 4),
    ]
    cases = pm.build_cases(events)
    vsm = pm.value_stream_map(cases, "procure_to_pay")
    assert vsm["scored_cases"] == 2
    dev_pairs = {(e["source"], e["target"]) for e in vsm["deviation_edges"]}
    assert ("Invoice Matched", "Purchase Order Created") in dev_pairs
    canon_pairs = {(e["source"], e["target"]) for e in vsm["canonical_edges"]}
    assert ("Purchase Order Created", "Invoice Matched") in canon_pairs
    assert ("Invoice Matched", "Payment Released") in canon_pairs
    assert 0.0 < vsm["process_cycle_efficiency"] < 1.0


def test_value_stream_map_no_cases_is_honest_null():
    vsm = pm.value_stream_map({}, "procure_to_pay")
    assert vsm["scored_cases"] == 0
    assert vsm["process_cycle_efficiency"] is None
    assert vsm["deviation_edges"] == []


def test_value_stream_map_all_canonical_is_100pct_efficient():
    cases = pm.build_cases(_clean_p2p_case("A"))
    vsm = pm.value_stream_map(cases, "procure_to_pay")
    assert vsm["process_cycle_efficiency"] == 1.0
    assert vsm["deviation_edges"] == []


# ── pareto_rank / pareto_breakdown ────────────────────────────────────────────

def test_pareto_rank_flags_vital_few_at_80pct_cumulative():
    items = [{"name": "a", "hrs": 50}, {"name": "b", "hrs": 30}, {"name": "c", "hrs": 15}, {"name": "d", "hrs": 5}]
    ranked = pm.pareto_rank(items, "hrs", lambda i: i["name"])
    assert [r["name"] for r in ranked] == ["a", "b", "c", "d"]
    # a=50% b=80% (crosses 80% here) c/d are past it.
    assert ranked[0]["is_vital_few"] is True
    assert ranked[1]["is_vital_few"] is True
    assert ranked[2]["is_vital_few"] is False
    assert ranked[3]["is_vital_few"] is False
    assert ranked[-1]["cumulative_pct"] == 1.0


def test_pareto_rank_empty_items():
    assert pm.pareto_rank([], "hrs", lambda i: i) == []


def test_pareto_breakdown_ranks_deviation_edges_by_total_hours():
    events = _clean_p2p_case("A") + [
        _ev("B", "Purchase Order Created", 0),
        _ev("B", "Invoice Matched", 1),
        _ev("B", "Purchase Order Created", 11),  # long deviation loop: 10 days
        _ev("B", "Invoice Matched", 12),
        _ev("B", "Payment Released", 13),
    ]
    cases = pm.build_cases(events)
    pareto = pm.pareto_breakdown(cases, "procure_to_pay")
    assert pareto["basis"] == "deviation_edge_total_hours"
    assert pareto["ranked"][0]["source"] == "Invoice Matched"
    assert pareto["ranked"][0]["target"] == "Purchase Order Created"
    assert pareto["vital_few_count"] >= 1
    assert pareto["total_deviation_hours"] == pareto["ranked"][0]["total_hours"]


def test_pareto_breakdown_no_deviations_gives_empty_ranking():
    cases = pm.build_cases(_clean_p2p_case("A"))
    pareto = pm.pareto_breakdown(cases, "procure_to_pay")
    assert pareto["ranked"] == []
    assert pareto["vital_few_count"] == 0
    assert pareto["vital_few_share_of_hours"] is None


# ── cycle_time_control_chart ──────────────────────────────────────────────────

def test_control_chart_buckets_cases_into_trailing_periods():
    # process_mining_tool anchors periods to datetime.now(), not _BASE, so
    # fixture cases must be "recent" (within the last few days) to land in
    # any scored period rather than falling outside every window.
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)

    def _recent_case(case_id, days_ago, step_gap_hours=2):
        base = now - timedelta(days=days_ago)
        return [
            _ev_at(case_id, "Purchase Order Created", base),
            _ev_at(case_id, "Invoice Matched", base + timedelta(hours=step_gap_hours)),
            _ev_at(case_id, "Payment Released", base + timedelta(hours=2 * step_gap_hours)),
        ]

    events = _recent_case("A", 0.5) + _recent_case("B", 0.5) + _recent_case("C", 2.5, step_gap_hours=20)
    cases = pm.build_cases(events)
    cc = pm.cycle_time_control_chart(cases, "procure_to_pay", period_days=1, periods=4)
    assert len(cc["periods"]) == 4
    assert cc["periods"][-1]["case_count"] == 2       # A, B landed in the most recent period
    assert cc["scored_periods"] >= 2
    assert cc["center_line_hours"] is not None
    assert cc["ucl_hours"] >= cc["center_line_hours"] >= cc["lcl_hours"]


def test_control_chart_insufficient_periods_is_honest_null():
    cc = pm.cycle_time_control_chart({}, "procure_to_pay", period_days=1, periods=4)
    assert cc["scored_periods"] == 0
    assert cc["center_line_hours"] is None
    assert cc["ucl_hours"] is None
    assert all(not p["out_of_control"] for p in cc["periods"])


def _ev_at(case_id, step, dt, event_id=1):
    return {
        "id": event_id, "adjudicated_at": dt, "final_verdict": "CLEAR", "risk_tier": "LOW",
        "source_system": "ORACLE_FUSION", "target_tool": None, "server_name": "oracle-fusion",
        "requires_human_review": False, "policy_violations": [], "case_id": case_id, "process_step": step,
    }
