"""
The Graph Architect — evaluates systemic dependencies and cascading failure paths.

Analytical lens: no event is an island. Every actor, resource, and system
is a node in a dependency graph, and risk propagates along edges.

The Graph Architect looks for:
  - Blast radius: how many downstream systems/users are affected if this node fails?
  - Single-point-of-failure (SPoF) actors: identities with outsized reach
  - Bridging nodes: resources that connect otherwise isolated system clusters
  - Multi-hop compromise paths: can attacker A reach critical asset B in ≤3 hops?
  - Temporal clustering: multiple high-risk events on the same node within a time window
    indicate an active, coordinated attack rather than an isolated incident

In production, this agent would query a live graph database (Neo4j, Amazon Neptune,
or a purpose-built CMDB graph). Here, it works from the URO's conformed_payload
and the correlation window passed in at construction.
"""

from __future__ import annotations

import time
from typing import Any

from ..models.risk_intelligence import AgentEvaluation, AgentVerdict
from ..models.uro import EventType, URO
from .base import BaseAgent


# Synthetic "known critical assets" map (production: graph DB query)
_CRITICAL_ASSETS: set[str] = {
    "finance-erp",
    "payroll-db",
    "identity-vault",
    "secrets-manager",
    "prod-k8s-cluster",
    "treasury-system",
}

# Max number of roles before an identity is flagged as a SPoF
_SPOF_ROLE_THRESHOLD = 20

# Minimum number of affected entities to flag blast radius
_BLAST_RADIUS_THRESHOLD = 50


class TheGraphArchitect(BaseAgent):
    name = "The Graph Architect"

    def __init__(self, correlation_window: list[URO] | None = None) -> None:
        # UROs seen in the current observation window (same correlation_id or actor)
        self._window: list[URO] = correlation_window or []

    async def evaluate(self, uro: URO) -> AgentEvaluation:
        start = time.monotonic()
        evidence: dict[str, Any] = {}
        signals: list[str] = []

        cp = uro.conformed_payload
        indicators = cp.risk_indicators if cp else {}

        # ── 1. Blast radius estimate ──────────────────────────────────────────
        role_count = int(indicators.get("role_count") or 0)
        entitlements = indicators.get("entitlements") or []
        blast = self._estimate_blast(uro, role_count, entitlements)
        evidence["estimated_blast_radius"] = blast
        if blast >= _BLAST_RADIUS_THRESHOLD:
            signals.append(
                f"Blast radius estimate {blast} entities — "
                f"a compromise of this node would affect >{_BLAST_RADIUS_THRESHOLD} resources"
            )

        # ── 2. Single-Point-of-Failure actor detection ────────────────────────
        if role_count >= _SPOF_ROLE_THRESHOLD:
            signals.append(
                f"Actor '{uro.actor_id}' holds {role_count} roles — "
                f"qualifies as a Single-Point-of-Failure identity (>={_SPOF_ROLE_THRESHOLD} roles)"
            )
            evidence["spof_actor"] = True

        # ── 3. Critical asset targeting ───────────────────────────────────────
        resource = (cp.resource_id or "").lower() if cp else ""
        for asset in _CRITICAL_ASSETS:
            if asset in resource:
                signals.append(
                    f"Event targets critical asset '{asset}' — "
                    "direct access to protected resource"
                )
                evidence["critical_asset_targeted"] = asset
                break

        # ── 4. Multi-system cascade detection (temporal clustering) ───────────
        same_actor_window = [
            w for w in self._window
            if w.actor_id == uro.actor_id and w.id != uro.id
        ]
        same_corr_window = [
            w for w in self._window
            if w.correlation_id and w.correlation_id == uro.correlation_id
            and w.id != uro.id
        ]
        evidence["same_actor_recent_events"] = len(same_actor_window)
        evidence["correlated_events_count"]  = len(same_corr_window)

        if len(same_actor_window) >= 3:
            systems_hit = {w.source_system.value for w in same_actor_window}
            signals.append(
                f"Actor '{uro.actor_id}' has triggered {len(same_actor_window)} events "
                f"in the observation window across {len(systems_hit)} system(s): "
                f"{', '.join(sorted(systems_hit))} — temporal clustering indicates coordinated activity"
            )

        if len(same_corr_window) >= 2:
            systems_in_cluster = {w.source_system.value for w in same_corr_window} | {uro.source_system.value}
            signals.append(
                f"Correlation cluster contains {len(same_corr_window)+1} events "
                f"across {len(systems_in_cluster)} system(s) — multi-system cascade in progress"
            )
            evidence["cascade_detected"] = True
            evidence["cascade_systems"]  = sorted(systems_in_cluster)

        # ── 5. Privilege escalation + critical system = max-severity path ─────
        if uro.event_type == EventType.PRIVILEGE_ESCALATION and evidence.get("critical_asset_targeted"):
            signals.append(
                "CRITICAL PATH: Privilege escalation targeting a critical asset "
                "— this is a ≤2-hop compromise scenario"
            )

        # ── 6. Dormant account re-activation on privileged path ───────────────
        if uro.event_type == EventType.DORMANT_PRIVILEGED_ACCOUNT:
            days = indicators.get("last_login_days") or 0
            if int(days) > 90:
                signals.append(
                    f"Dormant privileged account re-activated after {days} days — "
                    "ghost account / insider threat indicator"
                )
                evidence["dormant_days"] = days

        # Register current URO in window for future evaluations
        self._window.append(uro)

        verdict, confidence, risk_delta = self._derive_verdict(signals, evidence)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        return AgentEvaluation(
            agent_name=self.name,
            verdict=verdict,
            confidence=confidence,
            risk_delta=risk_delta,
            reasoning=self._build_reasoning(signals, uro),
            evidence=evidence,
            evaluation_ms=elapsed_ms,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _estimate_blast(uro: URO, role_count: int, entitlements: list) -> int:
        """
        Heuristic blast radius: combines role count, entitlement count,
        and event-type amplifiers.
        """
        base = role_count * 8 + len(entitlements) * 3
        amplifier = {
            EventType.PRIVILEGE_ESCALATION:   3.0,
            EventType.ROLE_EXPLOSION:         4.0,
            EventType.SECRET_DETECTED:        10.0,
            EventType.CASCADING_FAILURE_SIGNAL: 20.0,
            EventType.SOD_VIOLATION:          2.0,
        }.get(uro.event_type, 1.0)
        return int(base * amplifier)

    @staticmethod
    def _derive_verdict(
        signals: list[str], evidence: dict
    ) -> tuple[AgentVerdict, float, float]:
        if not signals:
            return AgentVerdict.CLEAR, 0.65, -0.02

        critical_signals = (
            evidence.get("cascade_detected")
            or evidence.get("critical_asset_targeted")
            or any("CRITICAL PATH" in s for s in signals)
        )

        if critical_signals:
            confidence = min(0.95, 0.70 + len(signals) * 0.05)
            return AgentVerdict.ESCALATE, confidence, +0.18

        if len(signals) >= 2:
            return AgentVerdict.ESCALATE, 0.72, +0.12

        return AgentVerdict.MONITOR, 0.60, +0.05

    @staticmethod
    def _build_reasoning(signals: list[str], uro: URO) -> str:
        if not signals:
            return (
                f"Graph analysis of '{uro.event_type.value}' found no critical dependency paths, "
                f"no SPoF actors, and no multi-system cascade signals for actor '{uro.actor_id}'. "
                "The event is topologically isolated."
            )
        bullets = "\n  • ".join(signals)
        return (
            f"Graph architecture analysis of '{uro.event_type.value}' "
            f"(source: {uro.source_system.value}, actor: '{uro.actor_id}') "
            f"identified {len(signals)} systemic signal(s):\n  • {bullets}"
        )
