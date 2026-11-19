"""
Abstract base classes for the Medallion Architecture pipeline layers.

Bronze → Silver → Gold is a strict one-way progression.
Each layer takes a URO, enriches it, and returns a new URO at the next stage.
UROs are IMMUTABLE (Pydantic frozen=False but by convention never mutated in-place;
always use .model_copy(update=...) or the .as_*() convenience methods).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..models.uro import URO, SourceSystem
from ..models.risk_intelligence import RiskIntelligenceReport


# ── Policy-as-Code Rule Interface ─────────────────────────────────────────────

@dataclass(frozen=True)
class PolicyRule:
    """
    A single declarative validation rule for the Silver layer.

    Rules are loaded at startup and applied to every URO during conformation.
    A failed rule produces a violation string that is stored on the URO
    and can trigger escalation.
    """

    rule_id:      str                   # e.g. "POL-001"
    name:         str                   # Human-readable label
    description:  str
    severity:     str = "HIGH"          # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    applies_to:   list[str] = field(default_factory=list)  # SourceSystem values; empty = all

    def applies(self, source: SourceSystem) -> bool:
        return not self.applies_to or source.value in self.applies_to


# ── Bronze Layer ──────────────────────────────────────────────────────────────

class BronzeLayerBase(ABC):
    """
    Ingests raw, immutable log events and maps them into the URO structure.

    Responsibilities:
      - Accept raw dict payloads from source connectors
      - Map source-specific fields to URO header fields
      - Wrap the verbatim source data in a RawPayload (with checksum)
      - Set pipeline_stage = BRONZE
      - NEVER transform or clean data — that is Silver's job

    One concrete subclass per source system (SapBronze, GitHubBronze, etc.)
    registers itself via `source_system`.
    """

    source_system: SourceSystem

    @abstractmethod
    async def ingest(self, raw_event: dict[str, Any]) -> URO:
        """
        Map a raw source event into a Bronze URO.

        The returned URO must have:
          - raw_payload.content == raw_event (verbatim)
          - pipeline_stage == PipelineStage.BRONZE
          - conformed_payload == None
        """

    async def ingest_batch(self, events: list[dict[str, Any]]) -> list[URO]:
        """Ingest a batch; subclasses may override for vectorised parsing."""
        import asyncio
        return await asyncio.gather(*[self.ingest(e) for e in events])


# ── Silver Layer ──────────────────────────────────────────────────────────────

class SilverLayerBase(ABC):
    """
    Cleans, conforms, and validates Bronze-stage UROs.

    Responsibilities:
      - Parse raw_payload.content and extract normalised fields
      - Populate conformed_payload (resource_id, action, risk_indicators, …)
      - Run the Policy-as-Code rule engine against each URO
      - Store any violations in silver_policy_violations
      - Set pipeline_stage = SILVER
      - NEVER discard data — always preserve raw_payload intact
    """

    def __init__(self, rules: list[PolicyRule] | None = None) -> None:
        self.rules: list[PolicyRule] = rules or []

    @abstractmethod
    async def conform(self, uro: URO) -> URO:
        """
        Normalise the URO and return it at Silver stage.

        The returned URO must have:
          - conformed_payload populated
          - pipeline_stage == PipelineStage.SILVER
          - silver_policy_violations set (may be empty)
        """

    async def validate(self, uro: URO) -> list[str]:
        """
        Run all registered Policy-as-Code rules against a URO.
        Returns a list of violation strings (empty = all clear).
        """
        violations: list[str] = []
        for rule in self.rules:
            if not rule.applies(uro.source_system):
                continue
            violation = await self._check_rule(rule, uro)
            if violation:
                violations.append(f"[{rule.rule_id}:{rule.severity}] {violation}")
        return violations

    @abstractmethod
    async def _check_rule(self, rule: PolicyRule, uro: URO) -> str | None:
        """
        Evaluate a single policy rule against a URO.
        Return a violation description string, or None if the rule passes.

        Concrete Silver implementations dispatch on rule.rule_id.
        """

    async def conform_batch(self, uros: list[URO]) -> list[URO]:
        import asyncio
        return await asyncio.gather(*[self.conform(u) for u in uros])


# ── Gold Layer ────────────────────────────────────────────────────────────────

class GoldLayerBase(ABC):
    """
    Aggregates Silver-stage UROs into executive-level risk intelligence.

    Responsibilities:
      - Compute composite risk scores per URO
      - Assign risk tiers (CRITICAL / HIGH / MEDIUM / LOW)
      - Aggregate batches of UROs into RiskIntelligenceReports
      - Compute cascading failure probabilities
      - Set pipeline_stage = GOLD on each scored URO
    """

    TIER_THRESHOLDS: dict[str, float] = {
        "CRITICAL": 0.85,
        "HIGH":     0.65,
        "MEDIUM":   0.40,
        "LOW":      0.0,
    }

    @abstractmethod
    async def score(self, uro: URO) -> URO:
        """
        Compute a risk_score [0.0, 1.0] and assign a risk_tier.
        Return the URO at Gold stage.
        """

    @abstractmethod
    async def aggregate(
        self,
        uros: list[URO],
        window_start: datetime,
        window_end: datetime,
    ) -> RiskIntelligenceReport:
        """
        Aggregate a batch of Gold-stage UROs into a RiskIntelligenceReport.
        This is the output that feeds the executive dashboard.
        """

    def _assign_tier(self, score: float) -> str:
        for tier, threshold in self.TIER_THRESHOLDS.items():
            if score >= threshold:
                return tier
        return "LOW"

    async def score_batch(self, uros: list[URO]) -> list[URO]:
        import asyncio
        return await asyncio.gather(*[self.score(u) for u in uros])
