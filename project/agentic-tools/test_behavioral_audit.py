#!/usr/bin/env python3
"""
Tests for the AI behavioural audit — UBO/behavioral/.

These analyzers make an assertion that can contradict a human attestation on
the AI system register ("oversight is defined" vs. "oversight is working"), so
the bar for a finding is deliberately high and most of these tests exist to
prove the analyzers stay SILENT when they should:

  - too few decisions              -> INSUFFICIENT_DATA, not a guess
  - no decision-latency instrument -> INSUFFICIENT_DATA, not "looks fine"
  - a group below the sample floor -> excluded and named, not folded in
  - four-fifths breach that is not significant -> MONITOR, not ESCALATE

A false rubber-stamping accusation costs the auditor their credibility on the
finding that actually matters, which is why the negative cases outnumber the
positive ones here.

Nothing is mocked: these are pure functions over an in-memory batch, and the
determinism contract in UBO/behavioral/base.py means a mock would only be
hiding whatever the real code does.

    pytest test_behavioral_audit.py -v
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    os.path.normpath(os.path.join(_here, "..")),
    os.path.normpath(os.path.join(_here, "..", "..")),
):
    if os.path.isdir(os.path.join(_candidate, "UBO")) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)
        break

from UBO.behavioral import TheFairnessAuditor, TheOverseer, run_behavioral_audit
from UBO.behavioral.base import BehavioralAnalyzer
from UBO.models.risk_intelligence import AgentVerdict


# ── helpers ──────────────────────────────────────────────────────────────────

def _reviews(n, decision="approved", seconds=0.5):
    return [
        {"event_type": "human_review", "decision": decision, "seconds_to_decide": seconds}
        for _ in range(n)
    ]


def _decisions(group, n, favourable):
    """n decisions for `group`, `favourable` of them with a favourable outcome."""
    return [
        {
            "event_type": "ai_decision",
            "subject_group": group,
            "outcome": "favourable" if i < favourable else "adverse",
        }
        for i in range(n)
    ]


# ── The Overseer ─────────────────────────────────────────────────────────────

def test_overseer_escalates_on_instant_blanket_approval():
    ev = TheOverseer().analyze(_reviews(40, seconds=0.4) + _reviews(1, "rejected", 20.0))
    assert ev.verdict is AgentVerdict.ESCALATE
    assert ev.evidence["implausible_read_fraction"] == 1.0
    assert ev.evidence["control_ref"] == "AI-06"
    assert ev.risk_delta > 0


def test_overseer_clear_when_reviewers_take_real_time():
    ev = TheOverseer().analyze(_reviews(30, seconds=45.0) + _reviews(10, "rejected", 60.0))
    assert ev.verdict is AgentVerdict.CLEAR
    assert ev.evidence["implausible_read_fraction"] == 0.0


def test_overseer_insufficient_data_below_minimum():
    ev = TheOverseer().analyze(_reviews(3))
    assert ev.verdict is AgentVerdict.INSUFFICIENT_DATA
    assert ev.confidence == 0.0
    assert ev.risk_delta == 0.0


def test_overseer_insufficient_data_when_latency_not_instrumented():
    """Approval rate alone must not produce a rubber-stamping verdict — that is
    precisely the conflation this analyzer exists to break."""
    events = [{"event_type": "human_review", "decision": "approved"} for _ in range(50)]
    ev = TheOverseer().analyze(events)
    assert ev.verdict is AgentVerdict.INSUFFICIENT_DATA
    assert ev.evidence["approvals_with_timing"] == 0
    assert ev.evidence["approval_rate"] == 1.0
    assert "seconds_to_decide" in ev.reasoning


def test_overseer_median_resists_a_single_long_outlier():
    """One reviewer who left a tab open must not mask a rubber-stamped queue —
    the mean would be dragged over the floor here, the median is not."""
    ev = TheOverseer().analyze(_reviews(39, seconds=0.3) + _reviews(1, seconds=100000.0))
    assert ev.verdict is AgentVerdict.ESCALATE
    assert ev.evidence["median_seconds_to_decide"] < 2.0


def test_overseer_ignores_rejection_latency():
    """A fast rejection is oversight working, not failing, so it must not count
    toward the implausible-read fraction."""
    ev = TheOverseer().analyze(_reviews(20, seconds=60.0) + _reviews(20, "rejected", 0.1))
    assert ev.evidence["approvals_with_timing"] == 20
    assert ev.evidence["implausible_read_fraction"] == 0.0
    assert ev.verdict is AgentVerdict.CLEAR


def test_overseer_threshold_is_configurable_per_queue():
    fast = _reviews(30, seconds=3.0)
    assert TheOverseer().analyze(fast).verdict is AgentVerdict.CLEAR
    strict = TheOverseer(implausible_read_seconds=5.0).analyze(fast)
    assert strict.verdict is AgentVerdict.ESCALATE


def test_overseer_confidence_scales_with_sample_size():
    small = TheOverseer().analyze(_reviews(6, seconds=0.2))
    large = TheOverseer().analyze(_reviews(200, seconds=0.2))
    assert small.verdict is large.verdict is AgentVerdict.ESCALATE
    assert large.confidence > small.confidence


# ── The Fairness Auditor ─────────────────────────────────────────────────────

def test_fairness_escalates_on_significant_four_fifths_breach():
    # 40% vs 80% selection rate = 0.5 impact ratio, large n -> significant.
    events = _decisions("A", 100, 40) + _decisions("B", 100, 80)
    ev = TheFairnessAuditor().analyze(events)
    assert ev.verdict is AgentVerdict.ESCALATE
    assert ev.evidence["impact_ratio"] < 0.8
    assert ev.evidence["significant_at_alpha"] is True
    assert ev.evidence["lowest_selection_rate_group"] == "A"
    assert ev.evidence["control_ref"] == "AI-09"


def test_fairness_clear_when_rates_are_comparable():
    events = _decisions("A", 100, 70) + _decisions("B", 100, 72)
    ev = TheFairnessAuditor().analyze(events)
    assert ev.verdict is AgentVerdict.CLEAR
    assert ev.evidence["impact_ratio"] >= 0.8


def test_fairness_monitor_when_breach_is_not_significant():
    """A four-fifths breach on a thin sample is a prompt to collect more data,
    not a finding — the standard is notoriously unstable at low n."""
    events = _decisions("A", 20, 8) + _decisions("B", 20, 13)
    ev = TheFairnessAuditor().analyze(events)
    assert ev.evidence["impact_ratio"] < 0.8
    assert ev.evidence["significant_at_alpha"] is False
    assert ev.verdict is AgentVerdict.MONITOR


def test_fairness_excludes_and_names_undersized_groups():
    events = _decisions("A", 100, 40) + _decisions("B", 100, 80) + _decisions("C", 3, 0)
    ev = TheFairnessAuditor().analyze(events)
    assert "C" not in ev.evidence["groups_assessed"]
    assert "C" in ev.evidence["groups_below_minimum"]
    # Excluded, but still visible to the auditor rather than silently dropped.
    assert ev.evidence["by_group"]["C"]["assessed"] is False
    assert "C" in ev.reasoning


def test_fairness_insufficient_data_with_one_assessable_group():
    events = _decisions("A", 100, 40) + _decisions("B", 5, 4)
    ev = TheFairnessAuditor().analyze(events)
    assert ev.verdict is AgentVerdict.INSUFFICIENT_DATA
    assert ev.confidence == 0.0


def test_fairness_insufficient_data_without_group_labels():
    events = [{"event_type": "ai_decision", "outcome": "adverse"} for _ in range(100)]
    ev = TheFairnessAuditor().analyze(events)
    assert ev.verdict is AgentVerdict.INSUFFICIENT_DATA


def test_fairness_reasoning_does_not_claim_a_legal_finding():
    """Overstating a four-fifths screen as proof of discrimination is how the
    whole finding gets dismissed; the wording must stay a trigger, not a verdict."""
    events = _decisions("A", 100, 40) + _decisions("B", 100, 80)
    ev = TheFairnessAuditor().analyze(events)
    assert "not by itself" in ev.reasoning
    assert "screening" in ev.evidence["standard"]


# ── Batch runner ─────────────────────────────────────────────────────────────

def test_audit_reports_worst_verdict_across_analyzers():
    """Disjoint evidence must not be averaged: a fairness CLEAR is not evidence
    against rubber-stamping, so it cannot dilute the Overseer's ESCALATE."""
    events = _reviews(40, seconds=0.3) + _decisions("A", 100, 70) + _decisions("B", 100, 72)
    report = run_behavioral_audit("TestBot", events)
    verdicts = {e["agent_name"]: e["verdict"] for e in report["evaluations"]}
    assert verdicts["The Overseer"] == "ESCALATE"
    assert verdicts["The Fairness Auditor"] == "CLEAR"
    assert report["overall_verdict"] == "ESCALATE"
    assert report["requires_human_review"] is True


def test_audit_flags_review_for_insufficient_data():
    """"We could not evidence this control" is a finding an auditor must see,
    not a silent pass."""
    report = run_behavioral_audit("TestBot", [{"event_type": "unrelated"}])
    assert report["overall_verdict"] == "INSUFFICIENT_DATA"
    assert report["requires_human_review"] is True


def test_audit_clear_only_when_everything_is_clear():
    events = _reviews(30, seconds=45.0) + _decisions("A", 100, 70) + _decisions("B", 100, 72)
    report = run_behavioral_audit("TestBot", events)
    assert report["overall_verdict"] == "CLEAR"
    assert report["requires_human_review"] is False


def test_audit_isolates_a_failing_analyzer():
    """One analyzer raising must not discard the other's findings."""

    class Exploding(BehavioralAnalyzer):
        name = "Exploding Analyzer"
        control_ref = "TEST-01"

        def analyze(self, events):
            raise RuntimeError("boom")

    events = _reviews(40, seconds=0.3)
    report = run_behavioral_audit("TestBot", events, analyzers=[Exploding(), TheOverseer()])
    by_name = {e["agent_name"]: e for e in report["evaluations"]}
    assert by_name["Exploding Analyzer"]["verdict"] == "INSUFFICIENT_DATA"
    assert "boom" in by_name["Exploding Analyzer"]["evidence"]["error"]
    assert by_name["The Overseer"]["verdict"] == "ESCALATE"
    assert report["overall_verdict"] == "ESCALATE"


def test_audit_output_is_json_serialisable():
    """The endpoint returns this straight to FastAPI and re-hashes it for the
    finding's event_id, so a non-serialisable value would fail at runtime."""
    import json

    report = run_behavioral_audit("TestBot", _reviews(40, seconds=0.3))
    assert json.dumps(report, sort_keys=True)


def test_audit_is_deterministic():
    """The base.py contract: a result that contradicts a human attestation has
    to be reproducible on demand."""
    events = _reviews(40, seconds=0.3) + _decisions("A", 100, 40) + _decisions("B", 100, 80)
    first = run_behavioral_audit("TestBot", events)
    second = run_behavioral_audit("TestBot", events)
    for a, b in zip(first["evaluations"], second["evaluations"]):
        assert a["verdict"] == b["verdict"]
        assert a["confidence"] == b["confidence"]
        assert a["evidence"] == b["evidence"]
        assert a["reasoning"] == b["reasoning"]
