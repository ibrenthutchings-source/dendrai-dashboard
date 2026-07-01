# Council of Agents

The Council is where the three agent evaluations converge into a single governance verdict. Two components handle this: the `CouncilOrchestrator` (lifecycle and fan-out) and `TheAdjudicator` (voting and conflict resolution).

---

## `CouncilOrchestrator` — `council/orchestrator.py`

The central nervous system. Manages agent lifecycle, parallel execution, and the shared observation window.

```python
council = CouncilOrchestrator(
    only_for_tiers={"CRITICAL", "HIGH", "MEDIUM"},  # default: {"CRITICAL", "HIGH"}
    observation_window=shared_window,               # optional; shared with Gold layer
)
```

### Construction

| Parameter | Default | Description |
|---|---|---|
| `only_for_tiers` | `{"CRITICAL", "HIGH"}` | Risk tiers that get full Council evaluation. UROs below this threshold are auto-cleared without invoking the agent swarm. |
| `observation_window` | `[]` | Shared list of UROs passed to `TheGraphArchitect` for cascade detection. Pass the same list used by `GoldAggregationLayer`. |

All four agents are instantiated once at construction and reused across calls. They are stateless per evaluation — only `TheGraphArchitect` reads from the shared window.

### `evaluate(uro: URO) → URO`

The primary entry point. Accepts a Gold-stage URO, returns an ADJUDICATED-stage URO.

**Processing steps:**

1. **Tier filter** — if `uro.risk_tier not in self._tiers`, call `_fast_path_clear()` and return immediately. No agents invoked.

2. **Parallel fan-out** — all three evaluating agents run concurrently:
   ```python
   quant_eval, linguist_eval, graph_eval = await asyncio.gather(
       self._quant.evaluate(uro),
       self._linguist.evaluate(uro),
       self._graph.evaluate(uro),
   )
   ```
   `return_exceptions=False` — individual agent errors surface immediately rather than being silently swallowed.

3. **Adjudication** — the three evaluations are passed to `TheAdjudicator`:
   ```python
   adjudication = await self._adjudicator.adjudicate(uro, evaluations)
   ```

4. **Window update** — the input URO is appended to `self._window` for future cascade detection.

5. **Return** — `uro.as_adjudicated(adjudication)` (new frozen copy at ADJUDICATED stage).

### `evaluate_batch(uros: list[URO], concurrency: int = 5) → list[URO]`

Rate-limited batch evaluation. Uses `asyncio.Semaphore(concurrency)` to prevent the agent swarm from overwhelming downstream services during high-volume bursts.

```python
results = await council.evaluate_batch(large_uro_list, concurrency=3)
```

### `run_pipeline(raw_event, source_system, bronze, silver, gold, orchestrator?)` → URO

Class method convenience wrapper: raw event → Bronze → Silver → Gold → Council in one call.

```python
final_uro = await CouncilOrchestrator.run_pipeline(
    raw_event=event_dict,
    source_system=SourceSystem.SAP,
    bronze_layer=bronze,
    silver_layer=silver,
    gold_layer=gold,
)
```

### Fast-Path Clear

When a URO's `risk_tier` is below the configured threshold, `_fast_path_clear()` returns an auto-cleared `AdjudicationResult` without agent invocation:

```python
AdjudicationResult(
    final_verdict=AgentVerdict.CLEAR,
    adjusted_risk_score=uro.risk_score,  # unchanged
    evaluations=[],                       # empty — no agents ran
    ensemble_confidence=1.0,             # max confidence (deterministic)
    requires_human_review=False,
    conflict_flags=[],
    conflict_reasoning="Auto-cleared: risk tier below Council threshold.",
)
```

This keeps latency near-zero for low-risk events (typically < 1 ms vs. 100–500 ms for a full Council run).

---

## The Adjudicator — `agents/adjudicator.py`

**Not a `BaseAgent`** — receives `list[AgentEvaluation]` rather than a URO directly.

```python
adjudication = await adjudicator.adjudicate(uro, [quant_eval, linguist_eval, graph_eval])
```

### Voting Model

Each agent's evaluation is converted to a scalar vote:

```
vote_i = verdict_weight(verdict_i) × confidence_i

verdict_weight:
  ESCALATE          → +1.0
  MONITOR           →  0.0
  CLEAR             → −1.0
  INSUFFICIENT_DATA →  0.0

ensemble_score = mean(votes)    ∈ [−1.0, +1.0]
```

**Example:**

| Agent | Verdict | Confidence | Vote |
|---|---|---|---|
| The Quant | ESCALATE | 0.80 | +0.80 |
| The Linguist | MONITOR | 0.65 | 0.00 |
| The Graph Architect | ESCALATE | 0.90 | +0.90 |
| **Ensemble** | | | **+0.57** |

`ensemble_score = +0.57 > 0.35` → final verdict: **ESCALATE**

### Thresholds

| Threshold | Value | Effect |
|---|---|---|
| `ESCALATE_THRESHOLD` | > 0.35 | ensemble_score above this → ESCALATE |
| `CLEAR_THRESHOLD` | < −0.35 | ensemble_score below this → CLEAR |
| — | between | → MONITOR |
| `CONFLICT_THRESHOLD` | > 1.20 | vote spread triggers AGENT_DIVERGENCE flag |
| `MIN_CONFIDENCE` | < 0.45 | mean confidence triggers LOW_CONFIDENCE flag |

### High-Confidence Veto

Before computing the ensemble, the Adjudicator checks for a hard override:

> **If any single agent returns ESCALATE with confidence ≥ 0.85, the final verdict is ESCALATE regardless of what the other agents voted.**

This rule exists because one sufficiently confident agent seeing a critical signal (e.g., The Quant detecting a Beneish score confirming active manipulation) should override two agents that simply lack data on that dimension.

### Adjusted Risk Score

```
weighted_delta = Σ(risk_delta_i × confidence_i) / N_agents

adjusted_score = gold_score + weighted_delta
               = clamp(adjusted_score, 0.0, 1.0)
```

The adjusted score can exceed the Gold score (if agents find additional signals) or fall below it (if agents find the Gold score was elevated by noisy inputs).

### Conflict Detection

The Adjudicator raises conflict flags when the agent swarm disagrees or lacks confidence:

| Flag | Trigger Condition | Action |
|---|---|---|
| `AGENT_DIVERGENCE` | vote_spread = max(votes) − min(votes) > 1.20 | Sets `requires_human_review = True` |
| `LOW_CONFIDENCE` | mean(confidence) < 0.45 | Sets `requires_human_review = True` |
| `MISSING_EVALUATIONS` | ≥ 2 agents return INSUFFICIENT_DATA | Sets `requires_human_review = True` |
| `ANOMALOUS_RISK_DELTA` | abs(weighted_delta) > 0.25 | Sets `requires_human_review = True` |

**Any active conflict flag sets `requires_human_review = True`.**

`conflict_reasoning` is a plain-English string explaining which flags fired and why. This is what appears in the `human_review_queue` view and on-call dashboard.

### Output

```python
AdjudicationResult(
    uro_id=uro.id,
    final_verdict=AgentVerdict.ESCALATE,
    adjusted_risk_score=0.91,
    adjusted_risk_tier=RiskTier.CRITICAL,
    evaluations=[quant_eval, linguist_eval, graph_eval],
    ensemble_confidence=0.78,
    requires_human_review=True,            # conflict flags were raised
    conflict_flags=[ConflictFlag.AGENT_DIVERGENCE],
    conflict_reasoning="Agents diverged: Quant ESCALATE(0.90), Linguist MONITOR(0.65), ...",
    adjudicated_at=datetime.utcnow(),
    adjudicator_version="1.0.0",
)
```

---

## Full Lifecycle Example

```
Event: SAP SOD_VIOLATION for jdoe, amount=$1.2M, BKTXT="misc adjustment"

Bronze:
  URO.id          = "a1b2c3..."
  URO.actor_id    = "jdoe@dendrai.com"
  URO.event_type  = SOD_VIOLATION
  URO.raw_payload.checksum = "sha256:abc..."

Silver:
  conformed.resource_id = "GL-ACCT-1001"
  conformed.risk_indicators.amount = 1_200_000.0
  conformed.risk_indicators.narrative = "misc adjustment"
  violations = [
    "SOD violation detected — mandatory CRITICAL escalation path applies",
    "Journal entry posted on Sunday without weekend authorisation code",
    "SAP journal entry has no approver ID"
  ]

Gold:
  base_weight     = 0.80   (SOD_VIOLATION)
  policy_penalty  = 0.46   (CRITICAL + MEDIUM + HIGH = 0.20 + 0.06 + 0.12)
  actor_penalty   = 0.00   (HUMAN)
  cascade_bonus   = 0.10   (2 related UROs in window)
  raw_score       = 1.36 → clamped to 1.00
  risk_tier       = CRITICAL

Council (parallel):
  The Quant         → ESCALATE (0.88) — z=17.0σ outlier, SoD conflict found
  The Linguist      → ESCALATE (0.80) — "misc" obfuscation + weekend urgency
  The Graph Arch.   → ESCALATE (0.90) — critical asset in entitlements, cascade detected

Adjudicator:
  votes           = [+0.88, 0.00, +0.90] — wait, Linguist MONITOR? No, all ESCALATE here
  votes           = [+0.88, +0.80, +0.90]
  ensemble_score  = +0.86 > 0.35  → ESCALATE
  weighted_delta  = mean(0.12×0.88 + 0.12×0.80 + 0.18×0.90) / 3 ≈ +0.13
  adjusted_score  = min(1.0, 1.0 + 0.13) = 1.0
  conflict_flags  = []              (all agents agree)
  requires_human_review = False     (no conflicts)

Final URO:
  pipeline_stage     = ADJUDICATED
  final_verdict      = ESCALATE
  adjusted_risk_score = 1.0
  adjusted_risk_tier  = CRITICAL
  requires_human_review = False
```

---

## Concurrency and Latency

The Council is designed to minimise wall-clock latency:

| Phase | Latency profile |
|---|---|
| Fast-path clear (below tier threshold) | < 1 ms |
| Three agents in parallel | max(agent_i latency) rather than sum |
| Adjudication | < 5 ms (pure computation) |
| Total for a CRITICAL event | typically 50–300 ms depending on agent complexity |

The `evaluate_batch()` semaphore prevents runaway parallelism. A `concurrency=5` limit means at most 5 × 3 = 15 agent calls are in-flight simultaneously.
