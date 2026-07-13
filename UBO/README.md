# Dendrai UBO — Governance Brain

A production-grade agentic governance system that turns raw security events from multiple source systems into adjudicated risk verdicts, routed through a Medallion pipeline and a Council of parallel AI agents.

---

## Architecture Overview

```
Raw Source Event (SAP / GitHub / SailPoint / MCP Proxy / EDGAR / FRED)
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  MEDALLION PIPELINE                                        │
│                                                           │
│  Bronze Layer   →  URO stage = BRONZE                     │
│    • Verbatim ingestion, no transformation                 │
│    • SHA-256 checksum locked on arrival                    │
│                                                           │
│  Silver Layer   →  URO stage = SILVER                     │
│    • Source-specific conformation → ConformedPayload       │
│    • 19-rule Policy-as-Code engine runs against every URO  │
│    • Violations attached; event is never dropped           │
│                                                           │
│  Gold Layer     →  URO stage = GOLD                       │
│    • Composite risk score 0.0–1.0                          │
│    • Tier assignment: CRITICAL / HIGH / MEDIUM / LOW       │
│    • Executive RiskIntelligenceReport generation           │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  COUNCIL OF AGENTS  (asyncio.gather — parallel fan-out)   │
│                                                           │
│   The Quant          The Linguist     The Graph Architect │
│   (quantitative)     (narrative)      (systemic)          │
│         │                 │                 │             │
│         └────────────┬────┘─────────────────┘             │
│                      ↓  3 × AgentEvaluation               │
│               The Adjudicator                             │
│         (ensemble voting + conflict detection)            │
└───────────────────────────────────────────────────────────┘
        │
        ▼
   URO stage = ADJUDICATED
   • final_verdict  (ESCALATE / MONITOR / CLEAR)
   • adjusted_risk_score
   • requires_human_review flag
   • conflict_flags[]
```

---

## Directory Structure

```
UBO/
├── README.md                   ← this file
├── main.py                     ← end-to-end demo (3 synthetic events)
│
├── models/
│   ├── uro.py                  ← Universal Risk Object + all enums
│   └── risk_intelligence.py    ← AgentEvaluation, AdjudicationResult, RiskIntelligenceReport
│
├── pipeline/
│   ├── base.py                 ← Abstract interfaces: BronzeLayerBase, SilverLayerBase, GoldLayerBase
│   ├── bronze.py               ← Concrete per-source ingestion handlers
│   ├── silver.py               ← Conformation + Policy-as-Code rule dispatcher
│   └── gold.py                 ← Risk scoring + aggregate executive reports
│
├── agents/
│   ├── base.py                 ← BaseAgent abstract interface
│   ├── quant.py                ← The Quant — quantitative anomaly detection
│   ├── linguist.py             ← The Linguist — narrative drift analysis
│   ├── graph_architect.py      ← The Graph Architect — systemic dependency analysis
│   └── adjudicator.py         ← The Adjudicator — ensemble voting + conflict resolution
│
├── council/
│   └── orchestrator.py         ← CouncilOrchestrator — parallel fan-out + lifecycle management
│
└── policy/
    └── rules.py                ← Policy-as-Code rule registry (19 rules, 5 domains)
```

---

## Supported Source Systems

| Source System | Events Handled |
|---|---|
| `SAP` | SoD violations, journal entry anomalies, vendor master changes, payment threshold breaches, period-close overrides |
| `GITHUB` | Secret detection, branch protection bypass, force pushes, dependency vulnerabilities, code review bypass |
| `SAILPOINT` | Privilege escalation, orphaned accounts, access certification failures, dormant privileged accounts, role explosion |
| `MCP_PROXY` | Bypass keyword detection, sensitive tool calls, bulk argument payloads, tool errors, compound governance violations |
| `FRED` | Macro leading indicator signals |
| `SEC_EDGAR` | Filing anomalies, Beneish M-Score threshold breaches |

---

## Quick Start

```python
import asyncio
from UBO.models.uro import SourceSystem
from UBO.pipeline.bronze import BronzeIngestionLayer
from UBO.pipeline.silver import SilverConformationLayer
from UBO.pipeline.gold import GoldAggregationLayer
from UBO.council.orchestrator import CouncilOrchestrator

async def main():
    bronze  = BronzeIngestionLayer()
    silver  = SilverConformationLayer()
    gold    = GoldAggregationLayer()
    council = CouncilOrchestrator(only_for_tiers={"CRITICAL", "HIGH", "MEDIUM"})

    raw_event = {
        "timestamp": "2025-07-01T14:00:00Z",
        "TCODE": "SOD_VIOLATION",
        "UNAME": "jdoe@company.com",
        "AMOUNT": 1_200_000.0,
        "BKTXT": "misc adjustment",
    }

    uro = await bronze.ingest(raw_event, SourceSystem.SAP)
    uro = await silver.conform(uro)
    uro = await gold.score(uro)
    uro = await council.evaluate(uro)

    print(uro.risk_tier)                            # "CRITICAL"
    print(uro.adjudication.final_verdict.value)     # "ESCALATE"
    print(uro.adjudication.requires_human_review)   # True

asyncio.run(main())
```

Full demo with three correlated events:

```bash
python -m UBO.main
```

---

## Risk Scoring Formula

```
score = base_weight(event_type)
      + Σ policy_severity_weight(violation)   ← +0.20 CRITICAL, +0.12 HIGH, +0.06 MEDIUM
      + actor_type_penalty                    ← +0.08 for SERVICE accounts
      + cascade_correlation_bonus             ← +0.05 per related URO, max +0.20

clamped to [0.0, 1.0]
```

**Tier thresholds:** CRITICAL ≥ 0.85 · HIGH ≥ 0.65 · MEDIUM ≥ 0.40 · LOW < 0.40

---

## Council Voting Model

Each of the three evaluating agents returns:
- `verdict` ∈ {ESCALATE, MONITOR, CLEAR, INSUFFICIENT_DATA}
- `confidence` ∈ [0.0, 1.0]
- `risk_delta` — signed adjustment to the Gold score

The Adjudicator computes:

```
vote_i = verdict_weight(verdict_i) × confidence_i
         where ESCALATE=+1, MONITOR=0, CLEAR=−1

ensemble_score = mean(votes)     # ∈ [−1, +1]
```

A single agent with confidence ≥ 0.85 and verdict ESCALATE can veto the ensemble (hard override).

---

## Documentation

| Topic | File |
|---|---|
| Data models (URO, enums, payloads) | [docs/models.md](docs/models.md) |
| Medallion pipeline (Bronze/Silver/Gold) | [docs/pipeline.md](docs/pipeline.md) |
| Council agents (Quant, Linguist, Graph Architect) | [docs/agents.md](docs/agents.md) |
| Council orchestration and Adjudicator | [docs/council.md](docs/council.md) |
| Policy-as-Code rule registry | [docs/policy.md](docs/policy.md) |
| GitHub webhook, schema v2, Controls Monitor panel | [docs/integrations.md](docs/integrations.md) |
