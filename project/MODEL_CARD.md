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
- [Recommended Next Steps](#recommended-next-steps)
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
| `POST /ai/persona-brief` | Role-tailored briefing narrative — CAE/CFO/COO by function, or Technical Executive (CTO/CIO/CISO) / Non-Technical Executive (CFO/COO/CEO) / Board by audience layer | **Gated (added 2026-08-11)** — every generation lands `review_status='pending'` in the AI Narrative Review queue (Approval Inbox); the response carries `_review` so the UI shows a "Pending Review" banner until a human clears it | Scored risk register + loop stats |
| `POST /ai/audit-report` | Full board-ready Markdown audit report | **Gated (added 2026-08-11)** — same 100%-review queue as persona-brief, same `_review` banner in the Loop Report modal | Risks, objectives, MAPs, loop data |
| `POST /ai/loop-calibrate` | Gate 3 next-cycle calibration recommendations | **Gated** — presented at Gate 3 for review | Score deltas, HITL override rate, lessons learned |
| `POST /agent/investigate` (+ `/stream`) | Free-form investigation memo (autonomous tool-use loop) | **Advisory memo** — a human auditor reads it; nothing is auto-applied | Ticker, prior memo, deterministic quant tool outputs |
| `POST /agent/investigate/council` (added 2026-07-23) | 3-perspective ensemble (financial / operational-cyber / compliance-regulatory analysts run in parallel, same tool access, different lens) + a synthesis pass reporting convergent vs. single-analyst findings | **Advisory memo** — same as above, ×3 perspectives; the synthesis explicitly does not resolve disagreements, only labels them | Ticker, prior memo, deterministic quant tool outputs |
| `POST /regulatory-change/scan` (regulatory horizon scanning) | Drafts a proposed control edit (summary, control reference, description) from a diff between two snapshots of a regulatory monitoring feed (EU AI Act, DORA, NIS2, state privacy) | **Gated** — every proposal sits in a review queue (`GET /regulatory-change/proposals`) until a human approves or rejects it; nothing reaches `controls_library` without that explicit decision | Unified diff of the feed's current vs. last-seen text |
| `POST /remediation/propose/{event_id}` (closed-loop remediation) | Drafts a GitHub issue title/body for one control-monitoring finding | **Gated** — routed through the existing 2-stage preparer→manager approval workflow (`gate_type='remediation_github'`); the real GitHub write only fires once a manager approves | One `exception_control_events` finding (control_id, system_source, actor, raw_payload) |
| `POST /remediation/propose-pr/{event_id}` (closed-loop remediation, added 2026-08-17) | Rewrites one named repo file's complete content to address a finding, drafted as a real pull request | **Gated** — same 2-stage workflow (`gate_type='remediation_github_pr'`); a computed unified diff is shown to the manager before approval, and the real GitHub write only fires once approved. No safe fallback on a failed/no-op draft — returns nothing rather than a fabricated fix | One `exception_control_events` finding + the named file's live content (fetched via `github_write_tool.get_file_content`, capped at 40K chars) |

Every AI output is persisted with provenance (model, effort, tokens, cost) in `ai_analyses`, readable via `GET /history/runs/{run_id}/ai-analyses`.

**The last two fully-ungated rows were closed out 2026-08-11.** `persona-brief` and `audit-report` used to reach a CFO or the board with no check *before* delivery, with only a ~20% after-the-fact spot-check sample (added 2026-07-23) catching problems after the fact. Every generation of either kind now lands in the same Approval Inbox reviewers already use for Gate 1/Gate 2 — 100% of generations, not a sample — and is visibly marked "Pending Review" wherever it's displayed until a human clears it. See [Ongoing Monitoring](#ongoing-monitoring--current-state) and [Known Limitations](#known-limitations--bias-findings) #3 for what this does and doesn't guarantee.

---

## Deterministic & Statistical Models

None of these are machine-learned — they're hand-authored formulas and rule tables, which makes them fully inspectable but also means any bias in them is a design choice someone made, not something a training set introduced.

- **Industry risk templates** (`risk-engine.js`, `TEMPLATES`) — per-industry arrays of risks, each with a `base` score and a `delta(r)` function driven by financial ratios. Six industry sets (Semiconductors, Automotive OEM, Software & Cloud, Financial Services, Healthcare & Pharma, Generic); unmatched industries fall back to Generic. **Baselines and control-effectiveness assumptions differ by design across industries** — see [Known Limitations](#known-limitations--bias-findings).
- **Forecasting ensemble** (`forecasting.js`) — ARIMA + Prophet + Random Forest, combined via `fitEnsemble` (equal weights initially) and reweighted by `updateEnsembleWeights` using inverse-MAPE from walk-forward backtesting (`backtesting.js`). No explicit industry/company-characteristic branching in the ensemble math itself.
- **Beneish M-score** (`risk-engine.js`, `computeRatios`) — simplified 5-of-8-variable formula; missing variables held at neutral defaults. Applied with the same coefficients regardless of industry.
- **Altman Z''-score** (`risk-engine.js`, adjacent to M-score) — book-equity variant (no market-price data source exists in this app).
- **Velocity-dampened quarterly projection** — `base + (velocity × CE_mult × 1.0 × 0.85^(q−1))`, capped at 25.0. `CE_mult` is driven by the same per-industry control-effectiveness seed as the risk templates above, so an industry's structural assumption compounds forward through every quarterly projection, not just the initial score.
- **Policy-as-Code evaluation/validation** (`agentic-tools/pac_endpoints.py`) — `POST /pac/evaluate` and Rego syntax validation run against the real [Open Policy Agent](https://www.openpolicyagent.org/) binary (`opa eval` / `opa check`), embedded directly in the production container (`project/Dockerfile`, OPA 0.69.0) as of 2026-07-23. A Python heuristic pattern-matcher exists as a fallback for environments without the binary (e.g. local dev without `OPA_BINARY` set); its output is explicitly labelled `"evaluation": "simulation (Python heuristic — not authoritative OPA)"` rather than presented as equivalent. Before this date, production had no OPA binary installed and silently ran the heuristic path for every evaluation and syntax check.

---

## Adjudication Ensemble (MCP Governance)

`agentic-tools/mcp_governance.py` adjudicates MCP tool-call risk through a five-voice pipeline (docstring: Bronze→Silver→Gold→Council→Adjudicator):

1. **Council** — three heuristic voters ("The Quant", "The Linguist", "The Graph Architect"), each returning a verdict/confidence/risk-delta/reasoning.
2. **The Reviewer (AI)** — a conditional 4th, LLM-generated opinion, added *only* when the heuristic ensemble already set `requires_human_review = TRUE`. **By design it can only raise the verdict toward ESCALATE, never downgrade it** — a deliberate conservative bias, not an oversight.
3. **Policy-as-Code veto** — a deterministic 5th voice; a fired Rego deny rule can force ESCALATE outright, overriding ensemble confidence.

**Human override**: `requires_human_review` / `human_verdict` are enforced end-to-end. A human reviewing an item can set `human_verdict ∈ {APPROVE, ESCALATE, CLEAR, MONITOR}`, which clears the review flag and can override `final_verdict`. A reconciliation query already compares `human_verdict` against `ai_final_verdict`/`council_votes` for accuracy tracking — currently the *only* place AI-vs-human disagreement is measured anywhere in the system, and it's scoped narrowly to MCP tool-call adjudication, not the Gate 1/2 recommendations above.

**Same pattern, second application (2026-07-23)**: `POST /agent/investigate/council` applies the same independent-voters-then-reconciliation shape to investigation instead of tool-call risk — three analyst perspectives run in parallel, a synthesis pass reports convergence, nothing is discarded or overridden. See [AI Components](#ai-components-claude). Deliberately *not* extended to pool signal across customers/tenants — this is client-confidential audit data, and cross-tenant aggregation was explicitly ruled out when this was scoped.

---

## Ongoing Monitoring — Current State

What's actually running today, so the gaps below are explicit rather than assumed:

- **Statistical/financial drift** (`agentic-tools/drift_tool.py`) — Population Stability Index (PSI) on 8 financial ratio fields (cross-sectional, all tickers) and 5 FRED macro series (regime drift, last 4 quarters vs. prior). Runs on a background timer (default every 6h, `_MODEL_HEALTH_CHECK_INTERVAL_S`), opens a tracked incident in `model_health_drift_incidents` when PSI ≥ 0.20 and no open incident already exists for that metric. Surfaced on the Model Vitals screen.
- **AI-recommendation drift** (`drift_tool.compute_ai_acceptance_drift`, added 2026-07-23) — PSI on the AI-suggestion accepted-vs-overridden proportion per `gate_type`, comparing the most recent 30 reviewed items against everything before them. Runs in the *same* background timer and incident table as the financial/macro checks above (`metric_kind='ai_acceptance'`) — no parallel monitoring system. Needs ≥10 events on both sides of the split to report a PSI rather than `insufficient_data`, so this is only meaningful once a reasonable review volume has accumulated.
- **AI acceptance-rate tracking** (`GET /approvals/ai-acceptance-stats`) — acceptance rate broken down by `gate_type`, risk **category**, and **industry** (industry/category breakdowns added 2026-07-23; `by_category` only covers `gate_type='risk'` items, since only those join to a `risk_scores.category`). Exists because `approval_tasks` already records `ai_suggested`/`ai_accepted` on every reviewed item.
- **Mandatory human review of the two previously-ungated narrative endpoints** (`persona_brief`, `audit_report`) — every generation, not a sample, is flagged `sampled_for_review` at save time (column name kept from its 2026-07-23 origin as a 20% spot-check; behavior changed to 100% on 2026-08-11) and queued via `GET /ai/review-queue`. The queue is now a section of **Approval Inbox** (`AI Narrative Review`, alongside the existing Gate 1/2 and UBO telemetry queues, all counted in the same nav badge) rather than a separate, undiscoverable endpoint with no UI — a reviewer clears an item there via `POST /ai/review-queue/{id}/review`. The API response for both endpoints now carries a `_review` block (`id`, `status`, `reviewed_by_name`, `reviewed_at`); the frontend (`rail.jsx`'s Persona tab, `report.jsx`'s Loop Report modal) renders it as a "Pending Review" / "Reviewed" banner directly on the generated content, so an unreviewed narrative can no longer look identical to a reviewed one. What this does *not* do: block the original requester from reading (or forwarding) their own freshly-generated draft before a reviewer acts — see [Known Limitations](#known-limitations--bias-findings) #3.
- **Adjudication reconciliation** — `human_verdict` vs. `ai_final_verdict` comparison in `mcp_governance.py`, scoped to MCP tool-call adjudication only.

**What this still does *not* cover, confirmed by direct code inspection, not inference:**
- No breakdown of the AI acceptance stat by *company* (only category/industry/gate_type) — a single-client pattern could still hide inside an industry-level aggregate.
- No fairness/bias/disparate-impact self-assessment exists anywhere for Dendrai's *own* AI or scoring models — the new breakdowns are a detection signal, not a fairness audit. (The app *does* flag "AI/Regulatory & Bias Risk" as a risk category — but that's about a client company's AI systems, not Dendrai's.)
- The AI Narrative Review queue (100% of generations as of 2026-08-11, formerly a 20% sample) makes an unreviewed narrative visibly marked and trackable everywhere it's displayed, and puts it in the same inbox reviewers already check for Gate 1/2 — but nothing technically stops the person who requested a persona-brief or audit-report from reading, copying, or forwarding it themselves before a reviewer clears it. That residual gap is the same one Gate 1/Gate 2 have always had (a UI convention, not a cryptographic block) — closing it further would mean withholding the draft from its own requester, which hasn't been judged worth the workflow cost.
- The industry-template asymmetry review and the LLM-escalate-only over-triggering audit (below) remain manual/process items — no code enforces either on a cadence.

---

## Known Limitations & Bias Findings

Concrete findings from direct inspection, not hypothetical concerns:

1. **Automotive OEM template asymmetry.** The Automotive OEM industry template's EV-transition risk is seeded with `ceBase: 'WEAK'` — the *only* risk item across all six industry templates assumed to have weak controls by default (every other template item defaults to `'ADEQUATE'`). Because `CE_mult` also drives the velocity-dampened quarterly projection, this asymmetry compounds forward through every forecast for automotive companies, regardless of their actual control environment. This is a real, structural scoring difference by industry that has not been reviewed for whether it's still justified.
2. **Simplified M-score applied uniformly.** The Beneish M-score uses only 5 of the standard 8 variables (missing ones held at neutral defaults) and the same coefficients across every industry — a modeling simplification, not an industry-conditioned model, which may under- or over-state manipulation risk differently by sector.
3. **Two previously fully-automated, ungated narrative outputs — now under mandatory review (closed 2026-08-11).** `persona-brief` and `audit-report` used to reach the user with no built-in check, only a 20% after-the-fact sample. Every generation of either kind now enters the AI Narrative Review queue in Approval Inbox and is visibly marked "Pending Review" wherever it's shown until a human clears it. The residual gap: the requester themselves can still read or forward their own unreviewed draft before that happens — see [Ongoing Monitoring](#ongoing-monitoring--current-state) for why that wasn't closed too.
4. **Fairness dimension added to acceptance stats, not yet acted on.** `GET /approvals/ai-acceptance-stats` now breaks down by risk category and industry (added 2026-07-23), and `drift_tool.compute_ai_acceptance_drift` flags when the accept/override rate shifts. Neither has real review volume behind it yet in production — this is instrumentation, not a finding; the Automotive OEM asymmetry above (#1) is still the only *confirmed* bias finding.
5. **Doc drift (now fixed):** `README.md` claimed the default model was `claude-opus-4-8` in three places; the code has used `claude-sonnet-4-6` — corrected as part of writing this card. A stale model card is worse than none; this is why the version note below exists.

---

## Recommended Next Steps

**Implemented 2026-08-11:**
- ~~Decide whether 20% is the right sample rate for the ungated-narrative review queue~~ → decided against sampling entirely. Every `persona_brief`/`audit_report` generation now requires review (was item 4 below); the review queue moved from a standalone, UI-less endpoint into a new **AI Narrative Review** section of Approval Inbox, alongside the existing Gate 1/2 and UBO telemetry queues, with a "Pending Review"/"Reviewed" banner rendered directly on the generated content (Persona tab, Loop Report modal) so the state is visible wherever the narrative itself is shown, not just in the queue.

**Implemented 2026-07-23** (were items 1, 2, and 4 in this list — see [Ongoing Monitoring](#ongoing-monitoring--current-state) for what each actually covers and its limits):
- ~~Break down `get_ai_acceptance_stats` by risk category and industry~~ → `by_category`/`by_industry` in the endpoint response.
- ~~Extend drift monitoring to AI-recommendation output~~ → `drift_tool.compute_ai_acceptance_drift`, feeding the same `model_health_drift_incidents` table.
- ~~Sampling-based human review for the two ungated narrative endpoints~~ → `GET/POST /ai/review-queue`, ~20% sample rate.
- ~~Give drift incidents a correction mechanism, not just status tracking~~ → two additions, same day: (a) **structured correction logging** — `PUT /model-health/drift-incidents/{id}` now accepts `correction_action` (rebaselined / recalibrated / escalated_for_review / false_positive / no_action_needed) + who/when, distinct from `status`; (b) **baseline reset** — `POST /model-health/baseline-reset` marks "now" as the new floor for a metric, and `drift_tool.py`'s three `compute_*_drift` functions now accept `baseline_resets` and exclude pre-reset data from the baseline window, so a corrected drift stops being perpetually re-flagged against pre-shift data. Reset only applies meaningfully to `ratio`/`fred_series` metrics — `ai_acceptance` is already a rolling window.
- ~~Second application of the Council ensemble pattern~~ → `POST /agent/investigate/council` (financial / operational-cyber / compliance-regulatory analysts in parallel + synthesis). See [Adjudication Ensemble](#adjudication-ensemble-mcp-governance).

**Still open — process, not code, so nothing here can be "implemented" away:**

1. **Periodic (quarterly) manual review of industry-template asymmetries** — starting with the Automotive OEM `ceBase` finding — and of forecasting-ensemble weight drift over time. Needs an owner and a calendar reminder, not a feature; a code-based reminder system would be process theater for a check that requires human judgment on whether an asymmetry is still justified.
2. **Document the LLM-escalate-only design explicitly as intentional** (already true — this card does it), and periodically audit whether it's over-triggering for particular tool/session patterns using the `human_verdict`-vs-`ai_final_verdict` reconciliation data that already exists.
3. **Act on the new fairness signals once there's real volume behind them** — the acceptance-stats breakdown and the AI-acceptance drift check are instrumentation; nobody has looked at their output in production yet. The first real review of these numbers is the actual next step, not more code.
4. **Watch reviewer bandwidth now that the queue is 100% of generations, not a 20% sample** — if AI Narrative Review backs up in practice, that's a real signal to revisit sampling (or add a second reviewer role), not something to pre-solve without evidence of load.
5. **Watch for correction-action misuse** — `false_positive`/`no_action_needed` and a baseline reset both make an incident stop recurring; nothing currently distinguishes "genuinely reviewed and dismissed" from "dismissed to clear the queue." Worth a periodic audit of who's logging which correction_action once there's volume, same as item 3.

---

## Versioning

This card reflects the codebase as of the date below. Update it when: the Claude model changes, a new AI-touching endpoint is added, the adjudication ensemble's voter composition changes, or a new deterministic/statistical model is introduced.

Last reviewed: 2026-08-11.
