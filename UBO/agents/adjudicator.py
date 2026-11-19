"""
The Adjudicator — probabilistic conflict resolution and final verdict.

The Adjudicator is NOT a peer of the other three agents — it is the final
authority. It receives the completed evaluations from The Quant, The Linguist,
and The Graph Architect (which ran in parallel), and must:

  1. Compute a weighted ensemble vote
  2. Detect conflicts (agents disagree beyond a threshold)
  3. Apply Bayesian confidence aggregation to produce a final score
  4. Either sign off on the risk (auto-close or auto-escalate) or flag for
     human review when agent disagreement is too large to resolve automatically

Conflict resolution algorithm:
  ─────────────────────────────────────────────────────────────────────
  Each agent casts a weighted vote:
    vote_i = mapping[verdict_i] × confidence_i
    mapping: ESCALATE=+1.0, MONITOR=0.0, CLEAR=-1.0, INSUFFICIENT_DATA=0.0

  Ensemble score:  E = Σ(vote_i) / N_agents   ∈ [-1, +1]
  Conflict score:  C = max(votes) - min(votes)  ∈ [0, 2]

  Decision:
    E >  0.35           → ESCALATE   (consensus to escalate)
    E <  -0.35          → CLEAR      (consensus to clear)
    -0.35 ≤ E ≤ 0.35   → MONITOR    (no clear consensus)
    C >  1.20           → FLAG_HUMAN (agents too far apart; no auto-resolution)
  ─────────────────────────────────────────────────────────────────────

Adjusted risk score:
  adjusted = gold_score + Σ(risk_delta_i × confidence_i) / N_agents
  clamped to [0.0, 1.0]
"""

from __future__ import annotations

import statistics
import time
from typing import Any

from ..models.risk_intelligence import (
    AdjudicationResult,
    AgentEvaluation,
    AgentVerdict,
    ConflictFlag,
    RiskTier,
)
from ..models.uro import URO


_ESCALATE_THRESHOLD = 0.35   # Ensemble score above this → ESCALATE
_CLEAR_THRESHOLD    = -0.35  # Ensemble score below this → CLEAR
_CONFLICT_THRESHOLD = 1.20   # Max-min vote spread above this → human review
_MIN_CONFIDENCE     = 0.45   # Mean ensemble confidence below this → LOW_CONFIDENCE flag


class TheAdjudicator:
    name = "The Adjudicator"
    version = "1.0.0"

    async def adjudicate(
        self,
        uro: URO,
        evaluations: list[AgentEvaluation],
    ) -> AdjudicationResult:
        """
        Collect agent evaluations, resolve conflicts, produce final AdjudicationResult.

        Args:
            uro:         The Gold-stage URO being adjudicated.
            evaluations: Exactly three AgentEvaluations from Quant, Linguist, Graph Architect.

        Returns:
            AdjudicationResult with final verdict, adjusted score, and any conflict flags.
        """
        start = time.monotonic()

        # ── Compute weighted votes ────────────────────────────────────────────
        votes = [e.weighted_vote for e in evaluations]
        ensemble_score = statistics.mean(votes) if votes else 0.0
        vote_spread    = max(votes) - min(votes) if len(votes) > 1 else 0.0

        mean_confidence = statistics.mean(e.confidence for e in evaluations) if evaluations else 0.0

        # ── Conflict detection ────────────────────────────────────────────────
        conflict_flags: list[ConflictFlag] = []

        if vote_spread > _CONFLICT_THRESHOLD:
            conflict_flags.append(ConflictFlag.AGENT_DIVERGENCE)

        if mean_confidence < _MIN_CONFIDENCE:
            conflict_flags.append(ConflictFlag.LOW_CONFIDENCE)

        insufficient = [e for e in evaluations if e.verdict == AgentVerdict.INSUFFICIENT_DATA]
        if len(insufficient) >= 2:
            conflict_flags.append(ConflictFlag.MISSING_EVALUATIONS)

        # ── Adjusted risk score ───────────────────────────────────────────────
        base_score = uro.risk_score or 0.0
        weighted_delta = sum(
            e.risk_delta * e.confidence for e in evaluations
        ) / max(len(evaluations), 1)
        adjusted_score = max(0.0, min(1.0, base_score + weighted_delta))

        if abs(weighted_delta) > 0.25:
            conflict_flags.append(ConflictFlag.ANOMALOUS_RISK_DELTA)

        # ── Final verdict ─────────────────────────────────────────────────────
        requires_human_review = bool(conflict_flags)

        final_verdict = self._resolve_verdict(ensemble_score, evaluations)

        # Assign tier from adjusted score
        adjusted_tier = self._score_to_tier(adjusted_score)

        # ── Conflict reasoning ────────────────────────────────────────────────
        conflict_reasoning: str | None = None
        if conflict_flags:
            flag_names = [f.value for f in conflict_flags]
            conflict_reasoning = (
                f"Conflict flags raised: {', '.join(flag_names)}. "
                f"Ensemble score={ensemble_score:.3f}, vote spread={vote_spread:.3f}, "
                f"mean confidence={mean_confidence:.3f}. "
                f"Agent verdicts: "
                + ", ".join(f"{e.agent_name}={e.verdict.value}" for e in evaluations)
                + ". Human review required before auto-escalation."
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        result = AdjudicationResult(
            uro_id=uro.id,
            final_verdict=final_verdict,
            adjusted_risk_score=round(adjusted_score, 4),
            adjusted_risk_tier=adjusted_tier,
            evaluations=evaluations,
            ensemble_confidence=round(mean_confidence, 4),
            requires_human_review=requires_human_review,
            conflict_flags=conflict_flags,
            conflict_reasoning=conflict_reasoning,
        )

        return result

    # ── Verdict resolution ────────────────────────────────────────────────────

    @staticmethod
    def _resolve_verdict(
        ensemble_score: float, evaluations: list[AgentEvaluation]
    ) -> AgentVerdict:
        """
        Map the continuous ensemble score to a discrete AgentVerdict.

        If any agent cast an ESCALATE with high confidence (≥0.85), that
        signal is treated as a veto — we escalate regardless of ensemble score.
        This prevents a confident single agent from being outvoted on critical signals.
        """
        # High-confidence veto rule
        escalate_vetoes = [
            e for e in evaluations
            if e.verdict == AgentVerdict.ESCALATE and e.confidence >= 0.85
        ]
        if escalate_vetoes:
            return AgentVerdict.ESCALATE

        if ensemble_score > _ESCALATE_THRESHOLD:
            return AgentVerdict.ESCALATE
        if ensemble_score < _CLEAR_THRESHOLD:
            return AgentVerdict.CLEAR
        return AgentVerdict.MONITOR

    @staticmethod
    def _score_to_tier(score: float) -> RiskTier:
        if score >= 0.85:
            return RiskTier.CRITICAL
        if score >= 0.65:
            return RiskTier.HIGH
        if score >= 0.40:
            return RiskTier.MEDIUM
        return RiskTier.LOW
