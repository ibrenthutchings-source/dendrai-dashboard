"""
Gold Layer — risk scoring, tier assignment, and aggregate intelligence reports.

Takes conformed Silver UROs and produces:
  1. Per-URO risk scores and tier labels
  2. RiskIntelligenceReports for executive dashboards

Scoring is a weighted combination of:
  - Event type base severity (hardcoded escalation weights)
  - Policy violation count and severity
  - Actor type (service accounts weighted higher for anomalous actions)
  - Cascading failure correlation (any related UROs in the correlation window)
"""

from __future__ import annotations

import statistics
import uuid
from datetime import datetime, timezone
from typing import Any

from ..models.uro import EventType, PipelineStage, SourceSystem, URO
from ..models.risk_intelligence import CascadeNode, RiskIntelligenceReport, RiskTier
from .base import GoldLayerBase


# ── Event severity baseline weights ──────────────────────────────────────────
# These represent the inherent risk of an event type before any contextual signals.
# Range: 0.0 (benign telemetry) → 1.0 (existential risk event)

_EVENT_BASE_WEIGHTS: dict[EventType, float] = {
    # SAP
    EventType.SOD_VIOLATION:              0.80,
    EventType.JOURNAL_ENTRY_ANOMALY:      0.65,
    EventType.VENDOR_MASTER_CHANGE:       0.60,
    EventType.PAYMENT_THRESHOLD_BREACH:   0.75,
    EventType.PERIOD_CLOSE_OVERRIDE:      0.85,
    # GitHub
    EventType.SECRET_DETECTED:            0.95,
    EventType.BRANCH_PROTECTION_BYPASSED: 0.70,
    EventType.FORCE_PUSH_MAIN:            0.65,
    EventType.DEPENDENCY_VULNERABILITY:   0.50,
    EventType.CODE_REVIEW_BYPASSED:       0.55,
    # DevOps Monitoring (SARIF/SAST evidence — severity refines this in Silver's
    # risk_indicators; this is the pre-severity baseline)
    EventType.SAST_FINDING:               0.60,
    EventType.SLA_BREACH:                 0.70,
    EventType.INFRASTRUCTURE_FINDING:     0.65,
    EventType.PIPELINE_MISCONFIGURATION:  0.60,
    # SailPoint
    EventType.PRIVILEGE_ESCALATION:       0.80,
    EventType.ORPHANED_ACCOUNT:           0.45,
    EventType.ACCESS_CERTIFICATION_FAIL:  0.55,
    EventType.DORMANT_PRIVILEGED_ACCOUNT: 0.60,
    EventType.ROLE_EXPLOSION:             0.75,
    # Macro/Market
    EventType.MACRO_LEADING_INDICATOR:    0.40,
    EventType.EDGAR_FILING_ANOMALY:       0.65,
    EventType.BENEISH_THRESHOLD_BREACH:   0.70,
    # Cross-system
    EventType.CASCADING_FAILURE_SIGNAL:   0.90,
    EventType.POLICY_VIOLATION:           0.60,
    EventType.ANOMALY:                    0.35,
    # MCP Proxy Governance
    EventType.MCP_GOVERNANCE_VIOLATION:   0.90,
    EventType.MCP_TOOL_BYPASS:            0.85,
    EventType.MCP_SENSITIVE_TOOL_CALL:    0.70,
    EventType.MCP_BULK_ARGS:              0.45,
    EventType.MCP_TOOL_ERROR:             0.40,
    EventType.MCP_LARGE_PAYLOAD:          0.35,
    # Generic Enterprise Systems (system_telemetry ingest)
    EventType.SENSITIVE_RESOURCE_ACCESS:   0.65,
    EventType.SYSTEM_GOVERNANCE_VIOLATION: 0.85,
}

_POLICY_SEVERITY_WEIGHTS: dict[str, float] = {
    "CRITICAL": 0.20,
    "HIGH":     0.12,
    "MEDIUM":   0.06,
    "LOW":      0.02,
}


class GoldAggregationLayer(GoldLayerBase):
    """
    Concrete Gold layer implementation.

    Scoring algorithm (additive capped at 1.0):
      score = base_weight
            + Σ(policy_violation_severity_weights)
            + actor_type_penalty
            + cascade_correlation_bonus

    The score is then normalised and clamped to [0.0, 1.0].
    """

    def __init__(self, correlation_window_uros: list[URO] | None = None) -> None:
        # UROs seen in the current observation window; used for cascade correlation
        self._window: list[URO] = correlation_window_uros or []

    # ── Per-URO scoring ───────────────────────────────────────────────────────

    async def score(self, uro: URO) -> URO:
        """Compute composite risk_score and risk_tier; advance to GOLD stage."""
        base = _EVENT_BASE_WEIGHTS.get(uro.event_type, 0.35)

        # Policy violation penalty
        violation_penalty = sum(
            _POLICY_SEVERITY_WEIGHTS.get(
                self._extract_severity(v), 0.06
            )
            for v in uro.silver_policy_violations
        )

        # Actor type penalty: service accounts doing risky things is more suspicious
        from ..models.uro import ActorType
        actor_penalty = 0.08 if uro.actor_type == ActorType.SERVICE else 0.0

        # Cascade correlation bonus: related events in window amplify risk
        cascade_bonus = self._cascade_correlation(uro)

        raw_score = base + violation_penalty + actor_penalty + cascade_bonus
        score = max(0.0, min(1.0, raw_score))
        tier  = self._assign_tier(score)

        # Register in the observation window for future cascade detection
        self._window.append(uro)

        return uro.as_gold(score=score, tier=tier)

    # ── Aggregate reporting ───────────────────────────────────────────────────

    async def aggregate(
        self,
        uros: list[URO],
        window_start: datetime,
        window_end: datetime,
    ) -> RiskIntelligenceReport:
        """Build a RiskIntelligenceReport from a batch of Gold-stage UROs."""
        if not uros:
            return self._empty_report(window_start, window_end)

        gold_uros = [u for u in uros if u.pipeline_stage in (
            PipelineStage.GOLD, PipelineStage.ADJUDICATED
        )]

        scores = [u.risk_score for u in gold_uros if u.risk_score is not None]
        enterprise_score = statistics.mean(scores) if scores else 0.0

        tier_counts: dict[str, int] = {
            "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0,
        }
        for u in gold_uros:
            tier_counts[u.risk_tier or "LOW"] += 1

        risk_by_source = self._mean_by_field(gold_uros, lambda u: u.source_system.value)
        risk_by_type   = self._mean_by_field(gold_uros, lambda u: u.event_type.value)

        cascade_prob = self._compute_cascade_probability(gold_uros)
        cascade_map  = self._build_cascade_map(gold_uros)

        human_review_queue = [
            u.id for u in gold_uros
            if u.adjudication and getattr(u.adjudication, "requires_human_review", False)
        ]

        top_risks = sorted(
            gold_uros, key=lambda u: u.risk_score or 0.0, reverse=True
        )[:10]

        return RiskIntelligenceReport(
            report_id=str(uuid.uuid4()),
            window_start=window_start,
            window_end=window_end,
            total_events=len(uros),
            critical_count=tier_counts["CRITICAL"],
            high_count=tier_counts["HIGH"],
            medium_count=tier_counts["MEDIUM"],
            low_count=tier_counts["LOW"],
            enterprise_risk_score=round(enterprise_score, 4),
            cascading_failure_probability=round(cascade_prob, 4),
            risk_by_source={k: round(v, 4) for k, v in risk_by_source.items()},
            risk_by_type={k: round(v, 4) for k, v in risk_by_type.items()},
            top_risks=[self._slim_uro(u) for u in top_risks],
            cascade_map=cascade_map,
            human_review_queue=human_review_queue,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _cascade_correlation(self, uro: URO) -> float:
        """
        Bonus score when the current URO shares a correlation_id with other
        recent events — indicating a multi-system cascading pattern.
        """
        if not uro.correlation_id:
            return 0.0
        related = sum(
            1 for w in self._window
            if w.correlation_id == uro.correlation_id and w.id != uro.id
        )
        # Each correlated sibling adds 0.05, capped at 0.20
        return min(0.20, related * 0.05)

    def _compute_cascade_probability(self, uros: list[URO]) -> float:
        """
        Cross-system correlation ratio, scaled by mean risk score — NOT a
        Bayesian update (no prior distribution is maintained or revised; this
        is (multi_system_cluster_ratio) × (0.5 + mean_score), a single-pass
        arithmetic estimate that happens to be labeled "probability").

        If ≥2 different source systems appear in the same correlation_id group,
        that cluster is treated as a confirmed multi-system cascade signal.
        """
        if not uros:
            return 0.0

        clusters: dict[str, set[str]] = {}
        for u in uros:
            if u.correlation_id:
                clusters.setdefault(u.correlation_id, set()).add(u.source_system.value)

        multi_system_clusters = sum(1 for sources in clusters.values() if len(sources) >= 2)
        if not clusters:
            return 0.0

        base_rate = multi_system_clusters / len(clusters)
        # Scale by mean enterprise risk score
        scores = [u.risk_score or 0.0 for u in uros]
        mean_score = statistics.mean(scores) if scores else 0.0
        return min(1.0, base_rate * (0.5 + mean_score))

    def _build_cascade_map(self, uros: list[URO]) -> CascadeNode | None:
        """
        Build a simple cascade dependency tree rooted at the highest-risk URO.
        In production this would use a graph database (Neo4j / Neptune).
        """
        if not uros:
            return None

        root = max(uros, key=lambda u: u.risk_score or 0.0)
        children = [
            CascadeNode(
                system=u.source_system.value,
                resource_id=u.conformed_payload.resource_id or u.id if u.conformed_payload else u.id,
                failure_prob=u.risk_score or 0.0,
                blast_radius=self._estimate_blast_radius(u),
                depth=1,
            )
            for u in uros
            if u.id != root.id and (u.risk_score or 0.0) >= 0.40
        ]

        return CascadeNode(
            system=root.source_system.value,
            resource_id=root.conformed_payload.resource_id or root.id if root.conformed_payload else root.id,
            failure_prob=root.risk_score or 0.0,
            blast_radius=self._estimate_blast_radius(root),
            depth=0,
            children=children[:5],  # cap cascade display depth
        )

    @staticmethod
    def _estimate_blast_radius(uro: URO) -> int:
        """Heuristic blast radius from event type and SailPoint role counts."""
        base = {
            EventType.SECRET_DETECTED:           500,
            EventType.PRIVILEGE_ESCALATION:      250,
            EventType.SOD_VIOLATION:             100,
            EventType.ROLE_EXPLOSION:            200,
            EventType.CASCADING_FAILURE_SIGNAL: 1000,
        }.get(uro.event_type, 10)

        if uro.conformed_payload:
            rc = uro.conformed_payload.risk_indicators.get("role_count") or 0
            base += int(rc) * 5

        return base

    @staticmethod
    def _mean_by_field(uros: list[URO], key_fn) -> dict[str, float]:
        buckets: dict[str, list[float]] = {}
        for u in uros:
            k = key_fn(u)
            if k:
                buckets.setdefault(k, []).append(u.risk_score or 0.0)
        return {k: statistics.mean(v) for k, v in buckets.items() if v}

    @staticmethod
    def _extract_severity(violation: str) -> str:
        """Parse severity tag from violation string '[POL-XXX:SEVERITY] ...'."""
        if "CRITICAL" in violation:
            return "CRITICAL"
        if "HIGH" in violation:
            return "HIGH"
        if "MEDIUM" in violation:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _slim_uro(uro: URO) -> dict[str, Any]:
        return {
            "id":           uro.id,
            "event_type":   uro.event_type.value,
            "source":       uro.source_system.value,
            "actor":        uro.actor_id,
            "risk_score":   uro.risk_score,
            "risk_tier":    uro.risk_tier,
            "timestamp":    uro.timestamp.isoformat(),
            "violations":   uro.silver_policy_violations,
            "payload":      uro.payload_summary,
        }

    @staticmethod
    def _empty_report(start: datetime, end: datetime) -> RiskIntelligenceReport:
        return RiskIntelligenceReport(
            report_id=str(uuid.uuid4()),
            window_start=start,
            window_end=end,
            total_events=0,
            critical_count=0,
            high_count=0,
            medium_count=0,
            low_count=0,
            enterprise_risk_score=0.0,
            cascading_failure_probability=0.0,
            risk_by_source={},
            risk_by_type={},
        )
