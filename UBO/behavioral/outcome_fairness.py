"""
The Fairness Auditor — disparate impact in an AI system's own decisions.

Analytical lens: the same model can be fair on average and unfair to you.

AI-09 (AI Performance & Bias Measurement) currently exists in Dendrai only as
a row in the risk register (`risk_register_endpoints.py`) — a control an
auditor asserts, with no instrument behind it. The nearest real
instrumentation, `db.get_ai_acceptance_stats_by_category`, breaks AI
acceptance down by *risk category* and *industry*: it answers "is our AI
advice trusted differently across risk types". It does not answer the AI-09
question, which is about the *people the decisions land on*.

This closes that gap using the four-fifths (80%) rule — the EEOC Uniform
Guidelines screening standard (29 CFR 1607.4(D)) — rather than an ad-hoc
concentration threshold, so a finding is stated in the terms a regulator,
employment counsel, or opposing expert already argues in.

Two guards against the standard's well-known failure mode (it is unstable on
small samples and will happily call a 3-of-4 vs 4-of-4 split "adverse
impact"):

  - a per-group minimum before that group is scored at all, and
  - a two-proportion z-test on the widest disparity, so the evidence carries
    a significance figure and not just a ratio.

Both are reported. The four-fifths ratio is a *screening indicator that
triggers investigation*, never a legal conclusion, and the reasoning text
says so — overstating it is how a fairness finding gets dismissed wholesale.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from ..models.risk_intelligence import AgentEvaluation, AgentVerdict
from .base import BehavioralAnalyzer

# EEOC Uniform Guidelines: a selection rate for any group less than 80% of the
# rate for the highest group is "generally regarded as evidence of adverse
# impact" (29 CFR 1607.4(D)).
_FOUR_FIFTHS = 0.80

# Ratio at which to raise MONITOR rather than ESCALATE — approaching the
# threshold is worth watching before it crosses.
_MONITOR_RATIO = 0.90

# Per-group minimum. Below this a group's selection rate is too noisy to
# compare; the group is reported as unassessable rather than silently folded
# into the comparison and generating a spurious ratio.
_MIN_PER_GROUP = 20

# Conventional alpha. A disparity that fails four-fifths but is not
# significant is reported as MONITOR, not ESCALATE.
_ALPHA = 0.05


def _two_proportion_p(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-tailed p-value for H0: p1 == p2, pooled-variance z-test.

    Implemented against math.erfc rather than scipy — this is the only
    inferential statistic in the module and pulling a numerical stack in for
    one normal tail is not worth the dependency.
    """
    if n1 == 0 or n2 == 0:
        return 1.0
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (p1 - p2) / se
    return math.erfc(abs(z) / math.sqrt(2))


class TheFairnessAuditor(BehavioralAnalyzer):
    """Detects demographic disparate impact in an AI system's decisions."""

    name = "The Fairness Auditor"
    control_ref = "AI-09"

    def __init__(self, min_per_group: int = _MIN_PER_GROUP) -> None:
        self.min_per_group = min_per_group

    def analyze(self, events: list[dict[str, Any]]) -> AgentEvaluation:
        decisions = [
            e for e in events
            if e.get("event_type") == "ai_decision"
            and e.get("subject_group")
            and e.get("outcome") in ("favourable", "adverse")
        ]

        if not decisions:
            return self._insufficient("No AI decision events carrying a subject_group and outcome.", {})

        totals: dict[str, int] = defaultdict(int)
        favourable: dict[str, int] = defaultdict(int)
        for d in decisions:
            group = str(d["subject_group"])
            totals[group] += 1
            if d["outcome"] == "favourable":
                favourable[group] += 1

        assessable = {g: n for g, n in totals.items() if n >= self.min_per_group}
        undersized = {g: n for g, n in totals.items() if n < self.min_per_group}

        breakdown = {
            g: {
                "decisions": totals[g],
                "favourable": favourable[g],
                "selection_rate": round(favourable[g] / totals[g], 4),
                "assessed": g in assessable,
            }
            for g in sorted(totals)
        }

        if len(assessable) < 2:
            return self._insufficient(
                f"Only {len(assessable)} group(s) had the {self.min_per_group} decisions needed for a "
                f"defensible selection-rate comparison; disparate impact requires at least two. "
                f"Groups below the minimum: {sorted(undersized)}.",
                breakdown,
            )

        rates = {g: favourable[g] / assessable[g] for g in assessable}
        best_group = max(rates, key=lambda g: rates[g])
        worst_group = min(rates, key=lambda g: rates[g])
        best_rate, worst_rate = rates[best_group], rates[worst_group]

        impact_ratio = (worst_rate / best_rate) if best_rate > 0 else 1.0
        p_value = _two_proportion_p(
            favourable[worst_group], totals[worst_group],
            favourable[best_group], totals[best_group],
        )

        verdict, reasoning = self._verdict(
            impact_ratio, p_value, worst_group, best_group, worst_rate, best_rate,
            totals[worst_group], totals[best_group], undersized,
        )

        return AgentEvaluation(
            agent_name=self.name,
            verdict=verdict,
            confidence=self._confidence(assessable, p_value),
            risk_delta=self._risk_delta(verdict),
            reasoning=reasoning,
            evidence={
                "control_ref": self.control_ref,
                "standard": "EEOC four-fifths rule (29 CFR 1607.4(D)) — screening indicator, not a legal finding",
                "decisions_examined": len(decisions),
                "groups_assessed": sorted(assessable),
                "groups_below_minimum": sorted(undersized),
                "min_per_group": self.min_per_group,
                "impact_ratio": round(impact_ratio, 4),
                "four_fifths_threshold": _FOUR_FIFTHS,
                "lowest_selection_rate_group": worst_group,
                "highest_selection_rate_group": best_group,
                "p_value": round(p_value, 6),
                "significant_at_alpha": p_value < _ALPHA,
                "alpha": _ALPHA,
                "by_group": breakdown,
            },
        )

    # ── verdict logic ─────────────────────────────────────────────────────

    def _verdict(
        self, ratio: float, p_value: float, worst: str, best: str,
        worst_rate: float, best_rate: float, n_worst: int, n_best: int,
        undersized: dict[str, int],
    ) -> tuple[AgentVerdict, str]:
        caveat = ""
        if undersized:
            caveat = (
                f" Note {len(undersized)} group(s) ({', '.join(sorted(undersized))}) fell below the "
                f"{self.min_per_group}-decision minimum and were excluded, so this comparison is not "
                f"exhaustive."
            )

        detail = (
            f"Group '{worst}' received a favourable outcome in {worst_rate:.1%} of {n_worst} decisions "
            f"versus {best_rate:.1%} of {n_best} for group '{best}' — an impact ratio of {ratio:.2f} "
            f"(p={p_value:.4f})."
        )

        if ratio < _FOUR_FIFTHS and p_value < _ALPHA:
            return AgentVerdict.ESCALATE, (
                f"Adverse impact indicated. {detail} This is below the EEOC four-fifths threshold of "
                f"{_FOUR_FIFTHS:.2f} and the difference is statistically significant at α={_ALPHA}. "
                f"Under the Uniform Guidelines this is the screening result that shifts the burden to "
                f"demonstrating the decision rule is job-related and consistent with business "
                f"necessity — it is a trigger for investigation and validation evidence, not by itself "
                f"a finding of discrimination.{caveat}"
            )

        if ratio < _FOUR_FIFTHS:
            return AgentVerdict.MONITOR, (
                f"Four-fifths threshold breached but not statistically significant. {detail} The ratio "
                f"is below {_FOUR_FIFTHS:.2f}, but at p={p_value:.4f} this disparity is within what "
                f"sampling variation could produce, so it does not yet support a conclusion. Accumulate "
                f"more decisions before acting on it.{caveat}"
            )

        if ratio < _MONITOR_RATIO:
            return AgentVerdict.MONITOR, (
                f"Approaching the adverse-impact threshold. {detail} Still above the {_FOUR_FIFTHS:.2f} "
                f"four-fifths line but below {_MONITOR_RATIO:.2f}; worth watching for drift rather than "
                f"acting on now.{caveat}"
            )

        return AgentVerdict.CLEAR, (
            f"No disparate impact signal. {detail} The impact ratio is at or above the "
            f"{_FOUR_FIFTHS:.2f} four-fifths threshold across all assessed groups.{caveat}"
        )

    def _confidence(self, assessable: dict[str, int], p_value: float) -> float:
        """Driven by the smallest assessed group — the comparison is only as
        sound as its weakest arm — then nudged by the significance result."""
        smallest = min(assessable.values())
        base = min(1.0, smallest / 200)
        scaled = 0.3 + 0.6 * base
        if p_value < _ALPHA:
            scaled = min(1.0, scaled + 0.1)
        return round(scaled, 4)

    @staticmethod
    def _risk_delta(verdict: AgentVerdict) -> float:
        return {
            AgentVerdict.ESCALATE: 0.30,
            AgentVerdict.MONITOR: 0.10,
            AgentVerdict.CLEAR: -0.05,
            AgentVerdict.INSUFFICIENT_DATA: 0.0,
        }[verdict]

    def _insufficient(self, reason: str, breakdown: dict) -> AgentEvaluation:
        return AgentEvaluation(
            agent_name=self.name,
            verdict=AgentVerdict.INSUFFICIENT_DATA,
            confidence=0.0,
            risk_delta=0.0,
            reasoning=reason,
            evidence={
                "control_ref": self.control_ref,
                "min_per_group": self.min_per_group,
                "by_group": breakdown,
            },
        )
