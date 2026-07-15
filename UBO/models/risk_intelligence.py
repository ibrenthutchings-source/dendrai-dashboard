"""
Risk intelligence output models — populated by the Gold layer and Council of Agents.

These are the models that feed executive dashboards and SIEM integrations.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class RiskTier(str, Enum):
    CRITICAL = "CRITICAL"   # risk_score >= 0.85 → immediate escalation
    HIGH     = "HIGH"       # risk_score >= 0.65 → same-day review
    MEDIUM   = "MEDIUM"     # risk_score >= 0.40 → weekly review
    LOW      = "LOW"        # risk_score <  0.40 → monthly review


class AgentVerdict(str, Enum):
    ESCALATE           = "ESCALATE"           # Agent votes to escalate
    MONITOR            = "MONITOR"            # Agent recommends enhanced monitoring
    CLEAR              = "CLEAR"              # Agent sees no actionable signal
    INSUFFICIENT_DATA  = "INSUFFICIENT_DATA"  # Agent cannot form a verdict


# ── Per-Agent Evaluation ──────────────────────────────────────────────────────

class AgentEvaluation(BaseModel):
    """Output produced by a single Council agent for a given URO."""

    agent_name:        str
    verdict:           AgentVerdict
    confidence:        float        # 0.0–1.0; how certain this agent is
    risk_delta:        float        # Signed adjustment to the Gold-layer risk_score
    reasoning:         str          # One-paragraph natural language rationale
    evidence:          dict[str, Any] = Field(default_factory=dict)
    evaluation_ms:     int          = 0  # Wall-clock time for this evaluation
    evaluated_at:      datetime     = Field(default_factory=datetime.utcnow)

    model_config = {"frozen": True}

    @property
    def weighted_vote(self) -> float:
        """Numerical vote weighted by agent confidence. Used by Adjudicator."""
        mapping = {
            AgentVerdict.ESCALATE:           1.0,
            AgentVerdict.MONITOR:            0.0,
            AgentVerdict.CLEAR:             -1.0,
            AgentVerdict.INSUFFICIENT_DATA:  0.0,
        }
        return mapping[self.verdict] * self.confidence


# ── Adjudication ──────────────────────────────────────────────────────────────

class ConflictFlag(str, Enum):
    AGENT_DIVERGENCE     = "AGENT_DIVERGENCE"      # Agents disagree past threshold
    LOW_CONFIDENCE       = "LOW_CONFIDENCE"         # Mean confidence below threshold
    MISSING_EVALUATIONS  = "MISSING_EVALUATIONS"   # One or more agents returned INSUFFICIENT_DATA
    ANOMALOUS_RISK_DELTA = "ANOMALOUS_RISK_DELTA"  # Risk delta swing is extreme
    # Set outside the Adjudicator itself, by mcp_governance._write_adjudication,
    # when a downstream check overrides the Council's ensemble verdict — kept
    # here (not a bespoke string) so every conflict_flags consumer has one enum
    # to check against regardless of which stage raised it.
    POLICY_VIOLATION        = "POLICY_VIOLATION"         # A deterministic Rego/OPA deny rule fired
    LLM_ESCALATION_OVERRIDE = "LLM_ESCALATION_OVERRIDE"  # The 4th-opinion LLM escalated past the ensemble


class AdjudicationResult(BaseModel):
    """
    Final verdict produced by The Adjudicator after aggregating all agent evaluations.

    When `requires_human_review` is True the URO must NOT be auto-resolved;
    it is routed to the human-in-the-loop review queue.
    """

    uro_id:          str
    final_verdict:   AgentVerdict

    # Composite score after all agents' risk_deltas are applied
    adjusted_risk_score: float
    adjusted_risk_tier:  RiskTier

    # Per-agent breakdown
    evaluations:     list[AgentEvaluation]

    # Adjudicator's own confidence in the ensemble result
    ensemble_confidence: float

    # Conflict detection
    requires_human_review: bool                = False
    conflict_flags:        list[ConflictFlag]  = Field(default_factory=list)
    conflict_reasoning:    Optional[str]       = None

    adjudicated_at: datetime = Field(default_factory=datetime.utcnow)
    adjudicator_version: str = "1.0.0"

    model_config = {"frozen": True}


# ── Gold Layer Outputs ────────────────────────────────────────────────────────

class CascadeNode(BaseModel):
    """One node in a cascading failure dependency graph."""

    system:       str                # Source system name
    resource_id:  str                # Entity at risk
    failure_prob: float              # Conditional probability of failure
    blast_radius: int                # Estimated number of downstream entities affected
    depth:        int                # Hops from the triggering event
    children:     list["CascadeNode"] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class RiskIntelligenceReport(BaseModel):
    """
    Gold-layer aggregate risk intelligence for a time window.

    This is the object that populates the executive dashboard.
    It is NOT a single URO — it is derived from a batch of UROs
    over a rolling observation window.
    """

    report_id:     str
    generated_at:  datetime = Field(default_factory=datetime.utcnow)
    window_start:  datetime
    window_end:    datetime

    # Volume metrics
    total_events:    int
    critical_count:  int
    high_count:      int
    medium_count:    int
    low_count:       int

    # Composite scores
    enterprise_risk_score:       float   # 0.0–1.0 weighted mean across all UROs
    cascading_failure_probability: float  # cross-system correlation ratio scaled by mean risk score — see gold.py's _compute_cascade_probability; not an actual Bayesian update

    # Breakdown by source
    risk_by_source:  dict[str, float]   # {SourceSystem → mean_risk_score}
    risk_by_type:    dict[str, float]   # {EventType    → mean_risk_score}

    # Top-N risks for dashboard display
    top_risks: list[dict[str, Any]] = Field(default_factory=list)  # slimmed URO summaries

    # Cascade map (populated by The Graph Architect signals)
    cascade_map: Optional[CascadeNode] = None

    # URO IDs requiring human review
    human_review_queue: list[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}
