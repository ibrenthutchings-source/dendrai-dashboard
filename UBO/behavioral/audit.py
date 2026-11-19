"""
BehavioralAudit — runs the behavioural analyzers over one AI system's batch.

Deliberately NOT registered with CouncilOrchestrator. The Council evaluates a
single URO event and its agents are built around that contract; these
analyzers need a whole batch to say anything at all (one review decision
cannot evidence rubber-stamping). Forcing batch semantics through the
single-URO path would distort both. They emit the same `AgentEvaluation`
model, so Council registration stays available later if that reconciliation
is ever worth doing.

The output verdict is the most severe across analyzers rather than a weighted
ensemble. The two analyzers examine disjoint evidence (review latency vs.
decision outcomes) — a CLEAR on fairness is not evidence against
rubber-stamping, so letting it dilute the other's ESCALATE would be averaging
unrelated quantities. The Council's adjudicator averages because its three
agents genuinely evaluate the *same* event from different angles; that
premise does not hold here.
"""

from __future__ import annotations

import time
from typing import Any

from ..models.risk_intelligence import AgentEvaluation, AgentVerdict
from .base import BehavioralAnalyzer
from .outcome_fairness import TheFairnessAuditor
from .oversight_efficacy import TheOverseer

# Most severe first — index position is the severity ordering used below.
_SEVERITY = [
    AgentVerdict.ESCALATE,
    AgentVerdict.MONITOR,
    AgentVerdict.CLEAR,
    AgentVerdict.INSUFFICIENT_DATA,
]


def default_analyzers() -> list[BehavioralAnalyzer]:
    return [TheOverseer(), TheFairnessAuditor()]


def _worst(verdicts: list[AgentVerdict]) -> AgentVerdict:
    if not verdicts:
        return AgentVerdict.INSUFFICIENT_DATA
    return min(verdicts, key=_SEVERITY.index)


def run_behavioral_audit(
    system_name: str,
    events: list[dict[str, Any]],
    analyzers: list[BehavioralAnalyzer] | None = None,
) -> dict[str, Any]:
    """Run every analyzer over `events` and return a serialisable report.

    Never raises on an individual analyzer failure: one analyzer erroring must
    not discard the other's findings, so the failure is recorded as an
    INSUFFICIENT_DATA evaluation carrying the error and the audit continues.
    That mirrors how the expiry sweeps treat a per-row failure.
    """
    analyzers = analyzers if analyzers is not None else default_analyzers()
    started = time.perf_counter()
    evaluations: list[AgentEvaluation] = []

    for analyzer in analyzers:
        analyzer_started = time.perf_counter()
        try:
            evaluation = analyzer.analyze(events)
            elapsed_ms = int((time.perf_counter() - analyzer_started) * 1000)
            # AgentEvaluation is frozen; rebuild rather than mutate.
            evaluations.append(evaluation.model_copy(update={"evaluation_ms": elapsed_ms}))
        except Exception as exc:  # noqa: BLE001 — see docstring
            evaluations.append(
                AgentEvaluation(
                    agent_name=analyzer.name,
                    verdict=AgentVerdict.INSUFFICIENT_DATA,
                    confidence=0.0,
                    risk_delta=0.0,
                    reasoning=f"Analyzer failed and produced no verdict: {exc}",
                    evidence={"error": str(exc), "control_ref": analyzer.control_ref},
                    evaluation_ms=int((time.perf_counter() - analyzer_started) * 1000),
                )
            )

    overall = _worst([e.verdict for e in evaluations])

    return {
        "system_name": system_name,
        "events_examined": len(events),
        "overall_verdict": overall.value,
        # Anything not CLEAR needs a human to look; INSUFFICIENT_DATA counts,
        # because "we could not evidence this control" is itself a finding an
        # auditor must see rather than a silent pass.
        "requires_human_review": overall != AgentVerdict.CLEAR,
        "evaluations": [e.model_dump(mode="json") for e in evaluations],
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
