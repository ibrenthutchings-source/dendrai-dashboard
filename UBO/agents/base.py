"""
BaseAgent — abstract interface every Council agent must implement.

All three evaluating agents (Quant, Linguist, Graph Architect) are invoked
in parallel by the orchestrator. They must be:
  - Stateless between calls (safe to run concurrently)
  - Non-blocking (must not perform synchronous I/O; use async/await)
  - Deterministic given the same URO (for audit reproducibility)

The Adjudicator is NOT a BaseAgent — it has a different contract because
it receives the outputs of the other three agents rather than a raw URO.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models.risk_intelligence import AgentEvaluation
from ..models.uro import URO


class BaseAgent(ABC):
    """Abstract base class for all Council of Agents evaluators."""

    #: Unique display name; used in logs and AdjudicationResult.evaluations
    name: str = "BaseAgent"

    @abstractmethod
    async def evaluate(self, uro: URO) -> AgentEvaluation:
        """
        Analyse the URO and return a structured evaluation.

        The evaluation must include:
          - verdict:    ESCALATE | MONITOR | CLEAR | INSUFFICIENT_DATA
          - confidence: 0.0–1.0 (how certain the agent is)
          - risk_delta: signed float adjustment to the Gold-layer risk_score
          - reasoning:  one-paragraph rationale (for audit trail)
          - evidence:   key-value dict of supporting data points
        """

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
