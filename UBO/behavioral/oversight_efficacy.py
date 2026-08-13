"""
The Overseer — evidence that a documented human review step is real.

Analytical lens: a signature is not a review.

AI-06 (Human Oversight of AI Systems) is currently satisfied by an auditor
ticking `human_oversight_defined` on the register. That records that a review
step EXISTS. It cannot record whether the reviewer is reading anything.

Dendrai already tracks whether a human *overrode* the AI
(`db.get_ai_acceptance_stats`, `approval_tasks.ai_accepted`). Override rate
alone cannot separate the two populations that matter here:

    a careful reviewer who reads and genuinely agrees   -> high approval rate
    a reviewer clicking approve without reading         -> high approval rate

They are indistinguishable on approval rate. **Time-to-decide is the
discriminating variable**, and nothing in the platform currently captures it.
That is what this analyzer adds.

Primary signal is NOT the mean. A mean time-to-approve is trivially inflated
by one reviewer who left a tab open over lunch, which is exactly the shape of
outlier that hides a rubber-stamping population. This uses:

  - median time-to-decide (robust to that outlier), and
  - the fraction of approvals decided faster than a human could plausibly
    have read the material (`implausible_read_fraction`) -- the direct
    measure, since a reviewer who cannot have read it did not review it.

Both must be present to escalate. A fast median alone is legitimate for
genuinely trivial low-risk queues; a fast median *plus* a large share of
physically-implausible decisions is not.
"""

from __future__ import annotations

import statistics
from typing import Any

from ..models.risk_intelligence import AgentEvaluation, AgentVerdict
from .base import BehavioralAnalyzer

# Below this, a reviewer cannot have read and considered a non-trivial item.
# Deliberately conservative: this is not "fast", it is "physically implausible
# for anything requiring judgment", so a finding built on it survives the
# "our reviewers are just efficient" objection.
_IMPLAUSIBLE_READ_SECONDS = 2.0

# Share of approvals under the implausible-read floor that constitutes a
# pattern rather than a handful of genuinely trivial items.
_IMPLAUSIBLE_FRACTION_ESCALATE = 0.50
_IMPLAUSIBLE_FRACTION_MONITOR = 0.25

# Approval rate at/above which the queue is doing no filtering worth the name.
_APPROVAL_RATE_ESCALATE = 0.95
_APPROVAL_RATE_MONITOR = 0.85

# Below this many decisions there is no defensible population to reason about
# and the analyzer declines to form a verdict (see base.py's contract note).
_MIN_DECISIONS = 5

# Sample size at which confidence saturates. Between _MIN_DECISIONS and this,
# confidence scales linearly -- a real signal off 6 events should not be
# reported with the same certainty as one off 600.
_CONFIDENCE_SATURATION_N = 100


class TheOverseer(BehavioralAnalyzer):
    """Detects automation bias / rubber-stamping in a human review queue."""

    name = "The Overseer"
    control_ref = "AI-06"

    def __init__(
        self,
        implausible_read_seconds: float = _IMPLAUSIBLE_READ_SECONDS,
        min_decisions: int = _MIN_DECISIONS,
    ) -> None:
        # Per-queue override: a fraud-alert triage queue and a board-report
        # sign-off do not share a plausible reading time. Configurable rather
        # than hardcoded for the same reason the register's risk tiers are.
        self.implausible_read_seconds = implausible_read_seconds
        self.min_decisions = min_decisions

    def analyze(self, events: list[dict[str, Any]]) -> AgentEvaluation:
        reviews = [e for e in events if e.get("event_type") == "human_review"]
        decided = [r for r in reviews if r.get("decision") in ("approved", "rejected")]

        if len(decided) < self.min_decisions:
            return self._insufficient(len(decided))

        approved = [r for r in decided if r.get("decision") == "approved"]
        approval_rate = len(approved) / len(decided)

        # Timing is only meaningful for approvals: a rejection taking 0.5s is
        # a reviewer catching something obvious, which is oversight working,
        # not failing.
        timings = [
            float(r["seconds_to_decide"])
            for r in approved
            if isinstance(r.get("seconds_to_decide"), (int, float))
        ]

        if not timings:
            return self._no_timing(len(decided), approval_rate)

        median_s = statistics.median(timings)
        implausible = [t for t in timings if t < self.implausible_read_seconds]
        implausible_fraction = len(implausible) / len(timings)

        verdict, reasoning = self._verdict(
            approval_rate, implausible_fraction, median_s, len(timings), len(decided)
        )

        return AgentEvaluation(
            agent_name=self.name,
            verdict=verdict,
            confidence=self._confidence(len(decided), len(timings)),
            risk_delta=self._risk_delta(verdict),
            reasoning=reasoning,
            evidence={
                "control_ref": self.control_ref,
                "decisions_examined": len(decided),
                "approval_rate": round(approval_rate, 4),
                "approvals_with_timing": len(timings),
                "median_seconds_to_decide": round(median_s, 3),
                "implausible_read_threshold_seconds": self.implausible_read_seconds,
                "implausible_read_count": len(implausible),
                "implausible_read_fraction": round(implausible_fraction, 4),
                # Kept for the auditor's own inspection: the fastest decisions
                # are the ones they will want to pull the underlying items for.
                "fastest_seconds": sorted(timings)[:5],
            },
        )

    # ── verdict logic ─────────────────────────────────────────────────────

    def _verdict(
        self, approval_rate: float, implausible_fraction: float, median_s: float,
        n_timed: int, n_decisions: int,
    ) -> tuple[AgentVerdict, str]:
        # Stated separately and explicitly: the approval rate is over all
        # decisions, the timing figures are over timed approvals only. Merging
        # the two denominators into one sentence is how a finding gets read as
        # overstated and then dismissed.
        basis = (
            f"{approval_rate:.0%} of {n_decisions} decisions were approvals; of the {n_timed} "
            f"approvals carrying a decision time, {implausible_fraction:.0%} were issued in under "
            f"{self.implausible_read_seconds}s (median {median_s:.2f}s)"
        )

        if approval_rate >= _APPROVAL_RATE_ESCALATE and implausible_fraction >= _IMPLAUSIBLE_FRACTION_ESCALATE:
            return AgentVerdict.ESCALATE, (
                f"The documented human review step is not functioning as a control. {basis} — faster "
                f"than the material could have been read. A review step that approves nearly "
                f"everything, nearly instantly, provides no independent check on the AI's output, so "
                f"any control reliance placed on it is unsupported."
            )

        if approval_rate >= _APPROVAL_RATE_MONITOR and implausible_fraction >= _IMPLAUSIBLE_FRACTION_MONITOR:
            return AgentVerdict.MONITOR, (
                f"Partial rubber-stamping signal. {basis}. Not yet conclusive — this pattern is "
                f"legitimate if the queue genuinely contains trivial items — but the review step "
                f"cannot currently be relied on as a control without confirming the queue's item mix."
            )

        if implausible_fraction >= _IMPLAUSIBLE_FRACTION_ESCALATE:
            return AgentVerdict.MONITOR, (
                f"{basis}. The approval rate shows reviewers are still rejecting a meaningful share, "
                f"so this is consistent with a queue split between trivial and substantive items "
                f"rather than blanket rubber-stamping; worth confirming the fast approvals are the "
                f"trivial ones."
            )

        return AgentVerdict.CLEAR, (
            f"Human review shows evidence of genuine consideration: {basis}, which is within the "
            f"range consistent with reviewers actually reading the material. No automation-bias "
            f"signal in this batch."
        )

    def _confidence(self, n_decisions: int, n_timed: int) -> float:
        """Scale with sample size, then discount if most approvals lack timing."""
        base = min(1.0, n_decisions / _CONFIDENCE_SATURATION_N)
        # Floor at 0.3 so a real signal off a small-but-adequate sample is not
        # dismissed outright by the adjudicator's weighted vote.
        scaled = 0.3 + 0.7 * base
        coverage = n_timed / n_decisions if n_decisions else 0.0
        return round(scaled * (0.5 + 0.5 * coverage), 4)

    @staticmethod
    def _risk_delta(verdict: AgentVerdict) -> float:
        return {
            AgentVerdict.ESCALATE: 0.25,
            AgentVerdict.MONITOR: 0.10,
            AgentVerdict.CLEAR: -0.05,
            AgentVerdict.INSUFFICIENT_DATA: 0.0,
        }[verdict]

    # ── degenerate cases ──────────────────────────────────────────────────

    def _insufficient(self, n: int) -> AgentEvaluation:
        return AgentEvaluation(
            agent_name=self.name,
            verdict=AgentVerdict.INSUFFICIENT_DATA,
            confidence=0.0,
            risk_delta=0.0,
            reasoning=(
                f"Only {n} human review decision(s) in this batch; at least {self.min_decisions} are "
                f"needed before an oversight-efficacy conclusion is defensible. No verdict formed."
            ),
            evidence={"control_ref": self.control_ref, "decisions_examined": n},
        )

    def _no_timing(self, n_decisions: int, approval_rate: float) -> AgentEvaluation:
        return AgentEvaluation(
            agent_name=self.name,
            verdict=AgentVerdict.INSUFFICIENT_DATA,
            confidence=0.0,
            risk_delta=0.0,
            reasoning=(
                f"{n_decisions} review decisions were found ({approval_rate:.0%} approved) but none "
                f"carried a `seconds_to_decide` value. Approval rate alone cannot distinguish a "
                f"careful reviewer who agrees from one who is not reading — instrument the review "
                f"UI to record decision latency before this control can be evidenced."
            ),
            evidence={
                "control_ref": self.control_ref,
                "decisions_examined": n_decisions,
                "approval_rate": round(approval_rate, 4),
                "approvals_with_timing": 0,
            },
        )
