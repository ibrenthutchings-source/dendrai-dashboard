"""
BehavioralAnalyzer — abstract interface for AI-behaviour evidence checks.

These are the batch counterpart to `UBO/agents/`. A Council agent
(`agents/base.py::BaseAgent`) evaluates ONE URO event; a BehavioralAnalyzer
evaluates a BATCH of a registered AI system's own operating logs and asks a
different question:

    ai_governance_endpoints.py (AI-06) asks "is human oversight DEFINED?"
    -- a boolean an auditor attests to on the register.

    This asks "is that oversight actually WORKING?" -- evidence from the
    system's own telemetry, which can contradict the attestation.

That gap is the whole point. A system can be registered with
`human_oversight_defined = TRUE` and still be rubber-stamped in practice;
only behavioural evidence distinguishes the two.

Contract (deliberately mirrors BaseAgent so results are interchangeable and
these can be registered with CouncilOrchestrator later if batch semantics are
ever reconciled with the single-URO path):

  - Deterministic. Same batch in, same evaluation out, always. No LLM call
    ever happens in here -- the audit-reproducibility requirement in
    agents/base.py applies with more force to an assessment that contradicts
    a human attestation. LLM commentary is a separate, human-gated pass
    (ai_governance_endpoints.py::behavioral_audit_narrative).
  - Non-blocking. Pure CPU over an in-memory batch; no I/O.
  - Emits the existing `AgentEvaluation` model, so downstream consumers
    (adjudicator, evidence chain, dashboards) need no new shape.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models.risk_intelligence import AgentEvaluation


class BehavioralAnalyzer(ABC):
    """Abstract base for batch behavioural-evidence analyzers."""

    #: Unique display name; surfaces in AgentEvaluation.agent_name and the UI.
    name: str = "BehavioralAnalyzer"

    #: Control reference this analyzer produces evidence for, so a finding can
    #: be traced to the register entry it contradicts (see framework_mappings).
    control_ref: str = ""

    @abstractmethod
    def analyze(self, events: list[dict[str, Any]]) -> AgentEvaluation:
        """
        Evaluate a batch of an AI system's log events.

        Must return INSUFFICIENT_DATA rather than guessing when the batch is
        too small to support a defensible conclusion -- a false accusation of
        rubber-stamping is worse than no finding, because it burns the
        auditor's credibility on the finding that matters.
        """

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
