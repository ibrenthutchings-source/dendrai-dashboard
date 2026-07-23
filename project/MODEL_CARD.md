# Model Card — Dendrai Intelligenza

This document inventories every algorithmic and AI-driven component in the platform — Claude-based features, the deterministic/statistical scoring and forecasting models, and the MCP governance adjudication ensemble — states the human-oversight level for each, and records known limitations and bias findings. It complements [README.md § AI-Augmented Features](README.md#ai-augmented-features), which describes what each feature *does*; this card focuses on *what to trust, what to check, and what's still open*.

**Scope note:** Dendrai does not train its own machine-learning models. "Algorithmic" here covers three genuinely different things that get monitored and audited differently: (1) a third-party foundation model (Claude) used for advisory recommendations and narrative generation, (2) hand-authored deterministic/statistical models (industry risk templates, a 3-model forecasting ensemble, Beneish M-score / Altman Z''-score), and (3) a rule-plus-heuristic adjudication ensemble for MCP tool-call risk governance.

---

## Table of Contents

- [AI Components (Claude)](#ai-components-claude)
- [Deterministic & Statistical Models](#deterministic--statistical-models)
- [Adjudication Ensemble (MCP Governance)](#adjudication-ensemble-mcp-governance)
- [Ongoing Monitoring — Current State](#ongoing-monitoring--current-state)
- [Known Limitations & Bias Findings](#known-limitations--bias-findings)
- [Recommended Next Steps (Not Yet Implemented)](#recommended-next-steps-not-yet-implemented)
- [Versioning](#versioning)

---

## AI Components (Claude)

Model: `claude-sonnet-4-6` (override via `DENDRAI_CLAUDE_MODEL`), adaptive thinking, via the shared client `agentic-tools/claude_client.py`. Every deterministic number an AI feature cites is computed elsewhere and passed in as ground truth — the model is instructed to cite it, not recompute or invent it.

| Endpoint | What it produces | Human oversight | Input |
|---|---|---|---|
| `POST /ai/gate1/recommend` | Per-risk disposition (approve/adjust score, RAG, velocity, CE) | **Gated** — advisory only; preparer submits, manager reviews via Approval Inbox before it takes effect | Risk register subset + filing snippets + evidence |
| `POST /ai/gate2/recommend` | Per-objective scope (priority, sprint, hours) | **Gated** — same preparer→manager pattern | Audit objectives + scored risk register |
| `POST /ai/approval/recommend` | Manager-facing approve/reject suggestion on a preparer's override | **Gated** — "purely advisory... never auto-decides" (endpoint docstring); manager still clicks Approve/Reject | Gate type, adjustments, preparer rationale |
| `POST /ai/pac/draft-rego` | Draft OPA/Rego policy module from a plain-language narrative | **Gated** — not persisted until the user clicks Save | Policy narrative text |
| `POST /ai/narrative-analysis` | Emerging risks / YoY language shifts from 10-K & proxy text | **Ungated** — displayed directly, cached 30 days | EDGAR Item 1A + proxy text |
| `POST /ai/persona-brief` | CAE/CFO/COO executive briefing narrative | **Ungated — fully automated**, no review step before it reaches the user | Scored risk register + loop stats |
| `POST /ai/audit-report` | Full board-ready Markdown audit report | **Ungated — fully automated**, no review step | Risks, objectives, MAPs, loop data |
| `POST /ai/loop-calibrate` | Gate 3 next-cycle calibration recommendations | **Gated** — presented at Gate 3 for review | Score deltas, HITL override rate, lessons learned |
| `POST /agent/investigate` (+ `/stream`) | Free-form investigation memo (autonomous tool-use loop) | **Advisory memo** — a human auditor reads it; nothing is auto-applied | Ticker, prior memo, deterministic quant tool outputs |

Every AI output is persisted with provenance (model, effort, tokens, cost) in `ai_analyses`, readable via `GET /history/runs/{run_id}/ai-analyses`.

**The two "ungated" rows are the ones that matter most for oversight**: `persona-brief` and `audit-report` generate narrative that can reach a CFO or the board with zero built-in human check, unlike every gated feature above them. See [Recommended Next Steps](#recommended-next-steps-not-yet-implemented).

---

## Deterministic & Statistical Models

None of these are machine-learned — they're hand-authored formulas and rule tables, which makes them fully inspectable but also means any bias in them is a design choice someone made, not something a training set introduced.

- **Industry risk templates** (`risk-engine.js`, `TEMPLATES`) — per-industry arrays of risks, each with a `base` score and a `delta(r)` function driven by financial ratios. Six industry sets (Semiconductors, Automotive OEM, Software & Cloud, Financial Services, Healthcare & Pharma, Generic); unmatched industries fall back to Generic. **Baselines and control-effectiveness assumptions differ by design across industries** — see [Known Limitations](#known-limitations--bias-findings).
- **Forecasting ensemble** (`forecasting.js`) — ARIMA + Prophet + Random Forest, combined via `fitEnsemble` (equal weights initially) and reweighted by `updateEnsembleWeights` using inverse-MAPE from walk-forward backtesting (`backtesting.js`). No explicit industry/company-characteristic branching in the ensemble math itself.
- **Beneish M-score** (`risk-engine.js`, `computeRatios`) — simplified 5-of-8-variable formula; missing variables held at neutral defaults. Applied with the same coefficients regardless of industry.
- **Altman Z''-score** (`risk-engine.js`, adjacent to M-score) — book-equity variant (no market-price data source exists in this app).
- **Velocity-dampened quarterly projection** — `base + (velocity × CE_mult × 1.0 × 0.85^(q−1))`, capped at 25.0. `CE_mult` is driven by the same per-industry control-effectiveness seed as the risk templates above, so an industry's structural assumption compounds forward through every quarterly projection, not just the initial score.

---

## Adjudication Ensemble (MCP Governance)

`agentic-tools/mcp_governance.py` adjudicates MCP tool-call risk through a five-voice pipeline (docstring: Bronze→Silver→Gold→Council→Adjudicator):

1. **Council** — three heuristic voters ("The Quant", "The Linguist", "The Graph Architect"), each returning a verdict/confidence/risk-delta/reasoning.
2. **The Reviewer (AI)** — a conditional 4th, LLM-generated opinion, added *only* when the heuristic ensemble already set `requires_human_review = TRUE`. **By design it can only raise the verdict toward ESCALATE, never downgrade it** — a deliberate conservative bias, not an oversight.
3. **Policy-as-Code veto** — a deterministic 5th voice; a fired Rego deny rule can force ESCALATE outright, overriding ensemble confidence.

**Human override**: `requires_human_review` / `human_verdict` are enforced end-to-end. A human reviewing an item can set `human_verdict ∈ {APPROVE, ESCALATE, CLEAR, MONITOR}`, which clears the review flag and can override `final_verdict`. A reconciliation query already compares `human_verdict` against `ai_final_verdict`/`council_votes` for accuracy tracking — currently the *only* place AI-vs-human disagreement is measured anywhere in the system, and it's scoped narrowly to MCP tool-call adjudication, not the Gate 1/2 recommendations above.

---

## Ongoing Monitoring — Current State

What's actually running today, so the gaps below are explicit rather than assumed:

- **Statistical/financial drift** (`agentic-tools/drift_tool.py`) — Population Stability Index (PSI) on 8 financial ratio fields (cross-sectional, all tickers) and 5 FRED macro series (regime drift, last 4 quarters vs. prior). Runs on a background timer (default every 6h, `_MODEL_HEALTH_CHECK_INTERVAL_S`), opens a tracked incident in `model_health_drift_incidents` when PSI ≥ 0.20 and no open incident already exists for that metric. Surfaced on the Model Vitals screen.
- **AI acceptance-rate tracking** (`GET /approvals/ai-acceptance-stats`) — aggregate acceptance rate, grouped only by `gate_type`. Exists because `approval_tasks` already records `ai_suggested`/`ai_accepted` on every reviewed item.
- **Adjudication reconciliation** — `human_verdict` vs. `ai_final_verdict` comparison in `mcp_governance.py`, scoped to MCP tool-call adjudication only.

**What this does *not* cover today, confirmed by direct code inspection, not inference:**
- No drift/quality monitoring touches AI recommendation output at all — the existing PSI checks are 100% financial-population and macro-regime drift.
- The acceptance-rate stat has no breakdown by risk category, industry, or company — it cannot currently reveal whether the AI (or the deterministic templates it's built on) is systematically harsher or more lenient for a particular industry.
- No fairness/bias/disparate-impact self-assessment exists anywhere for Dendrai's *own* AI or scoring models. (The app *does* flag "AI/Regulatory & Bias Risk" as a risk category — but that's about a client company's AI systems, not Dendrai's.)

---

## Known Limitations & Bias Findings

Concrete findings from direct inspection, not hypothetical concerns:

1. **Automotive OEM template asymmetry.** The Automotive OEM industry template's EV-transition risk is seeded with `ceBase: 'WEAK'` — the *only* risk item across all six industry templates assumed to have weak controls by default (every other template item defaults to `'ADEQUATE'`). Because `CE_mult` also drives the velocity-dampened quarterly projection, this asymmetry compounds forward through every forecast for automotive companies, regardless of their actual control environment. This is a real, structural scoring difference by industry that has not been reviewed for whether it's still justified.
2. **Simplified M-score applied uniformly.** The Beneish M-score uses only 5 of the standard 8 variables (missing ones held at neutral defaults) and the same coefficients across every industry — a modeling simplification, not an industry-conditioned model, which may under- or over-state manipulation risk differently by sector.
3. **Two fully-automated, ungated narrative outputs.** `persona-brief` and `audit-report` reach the user with no built-in human check before generation completes — unlike every other AI feature in this system.
4. **No fairness dimension in the one metric built for this.** The AI acceptance-rate stat is the natural place to detect systematic bias (is the AI's advice accepted less often for certain risk categories or industries?) but currently only breaks down by `gate_type`.
5. **Doc drift (now fixed):** `README.md` claimed the default model was `claude-opus-4-8` in three places; the code has used `claude-sonnet-4-6` — corrected as part of writing this card. A stale model card is worse than none; this is why the version note below exists.

---

## Recommended Next Steps (Not Yet Implemented)

Ordered by cost-to-value, cheapest first — none of this is built yet:

1. **Break down `get_ai_acceptance_stats` by risk category and industry**, not just `gate_type`. The data (`ai_suggested`/`ai_accepted` on `approval_tasks`) already exists — this is a `GROUP BY` change, not new instrumentation, and it's the single highest-value fairness signal available today.
2. **Extend `drift_tool.py` / `model_health_drift_incidents` to track AI-recommendation drift** (e.g., PSI on acceptance rate over time, by category), not just financial/macro population drift — reuse the existing incident-tracking infrastructure rather than building a parallel system.
3. **Periodic (quarterly) manual review of industry-template asymmetries** — starting with the Automotive OEM `ceBase` finding above — and of forecasting-ensemble weight drift over time.
4. **Lightweight sampling-based human review for the two ungated narrative endpoints** (`persona-brief`, `audit-report`) — even a periodic spot-check, since no review happens today before those reach an executive or the board.
5. **Document the LLM-escalate-only design explicitly as intentional** (already true — this card does it), and periodically audit whether it's over-triggering for particular tool/session patterns using the reconciliation data that already exists.

---

## Versioning

This card reflects the codebase as of the date below. Update it when: the Claude model changes, a new AI-touching endpoint is added, the adjudication ensemble's voter composition changes, or a new deterministic/statistical model is introduced.

Last reviewed: 2026-07-23.
