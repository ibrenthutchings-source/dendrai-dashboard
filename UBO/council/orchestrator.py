"""
CouncilOrchestrator — the central nervous system of the Dendrai UBO Governance Brain.

This is where the four agents come together. The orchestrator:

  1. Receives a Gold-stage URO (already scored and tiered)
  2. Fans out to The Quant, The Linguist, and The Graph Architect IN PARALLEL
     using asyncio.gather() — all three run concurrently to minimise latency
  3. Collects the three AgentEvaluations
  4. Passes them to The Adjudicator for conflict resolution
  5. Returns a final, fully adjudicated URO with pipeline_stage = "ADJUDICATED"

The orchestrator also maintains a shared observation window (list of recent UROs)
that is passed to The Graph Architect so it can detect temporal clustering.

                      ┌─────────────────┐
                      │  Gold-stage URO │
                      └────────┬────────┘
                               │
                    ┌──────────┴──────────┐
             asyncio.gather (parallel fan-out)
            ┌──────┴──────┐   ┌──────┴──────┐   ┌──────┴──────┐
            │  The Quant  │   │The Linguist │   │The Graph    │
            │             │   │             │   │Architect    │
            └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
                   │                 │                   │
                   └────────────┬────┘──────────────────┘
                                │  3 × AgentEvaluation
                       ┌────────┴────────┐
                       │ The Adjudicator │
                       └────────┬────────┘
                                │  AdjudicationResult
                       ┌────────┴─────────┐
                       │ Adjudicated URO  │
                       └──────────────────┘
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable

from ..agents.adjudicator import TheAdjudicator
from ..agents.graph_architect import TheGraphArchitect
from ..agents.linguist import TheLinguist
from ..agents.quant import TheQuant
from ..models.risk_intelligence import AgentEvaluation, AdjudicationResult
from ..models.uro import URO

logger = logging.getLogger("ubo.council")


class CouncilOrchestrator:
    """
    Orchestrates the Council of Agents for a Gold-stage URO.

    Configuration:
        only_for_tiers: If set, only UROs at these risk tiers are evaluated by
                        the full Council. Lower-tier UROs are auto-cleared.
                        Default: {"CRITICAL", "HIGH"} (MEDIUM and LOW are skipped).
    """

    def __init__(
        self,
        only_for_tiers: set[str] | None = None,
        observation_window: list[URO] | None = None,
    ) -> None:
        self._tiers = only_for_tiers or {"CRITICAL", "HIGH"}

        # Shared observation window across evaluations within this orchestrator instance
        self._window: list[URO] = list(observation_window or [])

        # Instantiate agents once — they are stateless per-call
        self._quant          = TheQuant()
        self._linguist       = TheLinguist()
        self._graph          = TheGraphArchitect(correlation_window=self._window)
        self._adjudicator    = TheAdjudicator()

    # ── Main entry point ──────────────────────────────────────────────────────

    async def evaluate(self, uro: URO) -> URO:
        """
        Run the full Council of Agents on a single Gold-stage URO.

        Returns the URO advanced to ADJUDICATED stage with the AdjudicationResult attached.
        If the URO's risk_tier is below the configured threshold, returns a fast-path
        auto-clear without running the full agent swarm.
        """
        if uro.risk_tier not in self._tiers:
            logger.debug(
                "CouncilOrchestrator: fast-path clear for %s (tier=%s, below threshold)",
                uro.id,
                uro.risk_tier,
            )
            return self._fast_path_clear(uro)

        logger.info(
            "CouncilOrchestrator: evaluating URO %s [%s | %s | tier=%s]",
            uro.id,
            uro.source_system.value,
            uro.event_type.value,
            uro.risk_tier,
        )

        # ── Parallel fan-out to the three evaluating agents ───────────────────
        quant_eval, linguist_eval, graph_eval = await asyncio.gather(
            self._quant.evaluate(uro),
            self._linguist.evaluate(uro),
            self._graph.evaluate(uro),
            return_exceptions=False,  # let individual agent errors bubble up
        )

        evaluations: list[AgentEvaluation] = [quant_eval, linguist_eval, graph_eval]

        self._log_evaluations(uro.id, evaluations)

        # ── Adjudicator collects and resolves ─────────────────────────────────
        adjudication: AdjudicationResult = await self._adjudicator.adjudicate(
            uro, evaluations
        )

        logger.info(
            "CouncilOrchestrator: adjudication complete for %s — "
            "verdict=%s, adjusted_score=%.3f, human_review=%s, conflicts=%s",
            uro.id,
            adjudication.final_verdict.value,
            adjudication.adjusted_risk_score,
            adjudication.requires_human_review,
            [f.value for f in adjudication.conflict_flags],
        )

        # Register URO in the shared observation window for future cascade detection
        self._window.append(uro)

        # Return the URO advanced to ADJUDICATED stage
        return uro.as_adjudicated(adjudication)

    async def evaluate_batch(
        self,
        uros: list[URO],
        concurrency: int = 5,
    ) -> list[URO]:
        """
        Evaluate a batch of Gold-stage UROs, rate-limited to `concurrency` in-flight.

        A semaphore prevents the agent swarm from overwhelming downstream services
        when processing a large burst of events.
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def _bounded_evaluate(u: URO) -> URO:
            async with semaphore:
                return await self.evaluate(u)

        return list(await asyncio.gather(*[_bounded_evaluate(u) for u in uros]))

    # ── Full pipeline (Bronze → Silver → Gold → Council) ─────────────────────

    @classmethod
    async def run_pipeline(
        cls,
        raw_event: dict,
        source_system,
        bronze_layer,
        silver_layer,
        gold_layer,
        orchestrator: "CouncilOrchestrator | None" = None,
    ) -> URO:
        """
        Convenience method: run a raw event through the full Medallion pipeline
        and then through the Council of Agents.

        Returns the fully adjudicated URO.
        """
        orchestrator = orchestrator or cls()

        # Bronze: raw ingestion
        bronze_uro = await bronze_layer.ingest(raw_event, source_system)
        logger.debug("Pipeline: bronze URO %s created", bronze_uro.id)

        # Silver: conform + validate
        silver_uro = await silver_layer.conform(bronze_uro)
        logger.debug(
            "Pipeline: silver URO %s — %d violation(s)",
            silver_uro.id,
            len(silver_uro.silver_policy_violations),
        )

        # Gold: score + tier
        gold_uro = await gold_layer.score(silver_uro)
        logger.debug(
            "Pipeline: gold URO %s — score=%.3f tier=%s",
            gold_uro.id,
            gold_uro.risk_score or 0.0,
            gold_uro.risk_tier,
        )

        # Council: adjudicate
        final_uro = await orchestrator.evaluate(gold_uro)
        logger.info("Pipeline: URO %s adjudicated — stage=ADJUDICATED", final_uro.id)

        return final_uro

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fast_path_clear(self, uro: URO) -> URO:
        """Return an auto-cleared URO without invoking the full agent swarm."""
        from ..models.risk_intelligence import AgentVerdict, RiskTier
        from ..agents.adjudicator import TheAdjudicator
        auto_adjudication = AdjudicationResult(
            uro_id=uro.id,
            final_verdict=AgentVerdict.CLEAR,
            adjusted_risk_score=uro.risk_score or 0.0,
            adjusted_risk_tier=self._adjudicator._score_to_tier(uro.risk_score or 0.0),
            evaluations=[],
            ensemble_confidence=1.0,
            requires_human_review=False,
            conflict_flags=[],
            conflict_reasoning="Auto-cleared: risk tier below Council threshold.",
        )
        self._window.append(uro)
        return uro.as_adjudicated(auto_adjudication)

    @staticmethod
    def _log_evaluations(uro_id: str, evaluations: list[AgentEvaluation]) -> None:
        for ev in evaluations:
            logger.info(
                "  [%s] verdict=%s confidence=%.2f delta=%+.3f time=%dms",
                ev.agent_name,
                ev.verdict.value,
                ev.confidence,
                ev.risk_delta,
                ev.evaluation_ms,
            )
