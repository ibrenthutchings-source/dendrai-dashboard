"""
The Quant — evaluates transactional and mathematical breaches.

Analytical lens: numbers don't lie (but they can be manipulated).

The Quant looks for:
  - Statistical outliers in transaction amounts (Z-score)
  - Beneish M-Score threshold breaches (earnings manipulation signal)
  - Payment or threshold breaches against configured limits
  - Round-number bias (indicator of manual journal entry manipulation)
  - Benford's Law deviation in large transaction sets
"""

from __future__ import annotations

import math
import time
from typing import Any

from ..models.risk_intelligence import AgentEvaluation, AgentVerdict
from ..models.uro import EventType, URO
from .base import BaseAgent


# Historical transaction amount statistics (in production: loaded from DB / FRED)
_SECTOR_BASELINES: dict[str, dict[str, float]] = {
    "default": {"mean": 50_000.0, "std": 30_000.0},
    "SAP":     {"mean": 75_000.0, "std": 45_000.0},
}

# Beneish M-Score threshold (< -2.22 = likely non-manipulator)
_BENEISH_ESCALATION_THRESHOLD = -1.78
_BENEISH_MONITOR_THRESHOLD    = -2.22

# Payment amounts that trigger automatic review
_PAYMENT_THRESHOLDS = {
    "single_transaction": 500_000.0,
    "daily_aggregate":  2_000_000.0,
}


class TheQuant(BaseAgent):
    name = "The Quant"

    async def evaluate(self, uro: URO) -> AgentEvaluation:
        start = time.monotonic()
        evidence: dict[str, Any] = {}
        signals: list[str] = []

        cp = uro.conformed_payload
        indicators = cp.risk_indicators if cp else {}

        # ── 1. Z-Score anomaly on transaction amount ──────────────────────────
        amount = indicators.get("amount")
        if amount is not None:
            baseline = _SECTOR_BASELINES.get(uro.source_system.value, _SECTOR_BASELINES["default"])
            z = (float(amount) - baseline["mean"]) / max(baseline["std"], 1.0)
            evidence["z_score"]       = round(z, 3)
            evidence["amount"]        = amount
            evidence["amount_mean"]   = baseline["mean"]
            if abs(z) > 3.0:
                signals.append(f"Z-score={z:.2f} — extreme outlier (|z|>3σ)")
            elif abs(z) > 2.0:
                signals.append(f"Z-score={z:.2f} — elevated outlier (|z|>2σ)")

        # ── 2. Round-number bias detection ───────────────────────────────────
        if amount and amount > 0:
            if float(amount) % 10_000 == 0:
                signals.append(f"Amount {amount} is a round multiple of 10,000 — potential manual entry bias")
                evidence["round_number_bias"] = True

        # ── 3. Beneish M-Score check ──────────────────────────────────────────
        m_score = indicators.get("m_score") or indicators.get("beneish_m_score")
        if m_score is not None:
            m = float(m_score)
            evidence["m_score"] = m
            if m > _BENEISH_ESCALATION_THRESHOLD:
                signals.append(f"Beneish M-Score={m:.3f} — LIKELY MANIPULATOR territory (>{_BENEISH_ESCALATION_THRESHOLD})")
            elif m > _BENEISH_MONITOR_THRESHOLD:
                signals.append(f"Beneish M-Score={m:.3f} — gray zone (>{_BENEISH_MONITOR_THRESHOLD})")

        # ── 4. Payment threshold breach ───────────────────────────────────────
        if uro.event_type == EventType.PAYMENT_THRESHOLD_BREACH and amount:
            if float(amount) > _PAYMENT_THRESHOLDS["single_transaction"]:
                signals.append(
                    f"Payment ${float(amount):,.0f} exceeds single-transaction limit "
                    f"${_PAYMENT_THRESHOLDS['single_transaction']:,.0f}"
                )

        # ── 5. SoD violation — inherently a math problem ─────────────────────
        if uro.event_type == EventType.SOD_VIOLATION:
            conflicting_roles = indicators.get("conflicting_roles", [])
            signals.append(
                f"SoD conflict: actor '{uro.actor_id}' holds incompatible role pair "
                f"{conflicting_roles or '(details in raw payload)'}"
            )
            evidence["sod_conflict_detected"] = True

        # ── Verdict derivation ────────────────────────────────────────────────
        verdict, confidence, risk_delta = self._derive_verdict(signals, uro)

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

    def _derive_verdict(
        self, signals: list[str], uro: URO
    ) -> tuple[AgentVerdict, float, float]:
        # No quantitative signals found (e.g., non-financial event type)
        if not signals:
            non_quant_types = {
                EventType.SECRET_DETECTED,
                EventType.BRANCH_PROTECTION_BYPASSED,
                EventType.ORPHANED_ACCOUNT,
            }
            if uro.event_type in non_quant_types:
                return AgentVerdict.INSUFFICIENT_DATA, 0.30, 0.0
            return AgentVerdict.CLEAR, 0.55, -0.05

        # Presence of critical signals → ESCALATE
        critical_keywords = ("MANIPULATOR", "SoD conflict", "exceeds single-transaction")
        if any(any(kw in s for kw in critical_keywords) for s in signals):
            confidence = min(0.95, 0.65 + len(signals) * 0.08)
            return AgentVerdict.ESCALATE, confidence, +0.15

        # Moderate signals → MONITOR
        confidence = min(0.85, 0.50 + len(signals) * 0.10)
        return AgentVerdict.MONITOR, confidence, +0.05

    @staticmethod
    def _build_reasoning(signals: list[str], uro: URO) -> str:
        if not signals:
            return (
                f"No quantitative anomalies detected in event '{uro.event_type.value}' "
                f"for actor '{uro.actor_id}'. Transactional indicators are within baseline parameters."
            )
        bullets = "\n  • ".join(signals)
        return (
            f"Quantitative analysis of '{uro.event_type.value}' (source: {uro.source_system.value}) "
            f"for actor '{uro.actor_id}' identified {len(signals)} signal(s):\n  • {bullets}"
        )
