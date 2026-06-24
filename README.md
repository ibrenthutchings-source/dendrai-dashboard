# Dendrai Risk Loop — Dashboard

A React/Vite risk governance dashboard implementing a six-stage continuous audit loop with live signal ingestion, AI-assisted risk scoring, HITL gate review, and scenario planning.

## Running the app

```bash
cd project
npm install
npm run dev        # Vite dev server at http://localhost:5173
```

## Project structure

```
project/
  src/main.jsx        — entry point; imports all component modules
  app.jsx             — root component; state, pipeline orchestration, routing
  pipeline.jsx        — six-stage pipeline UI + substep rendering
  rail.jsx            — Live Register right-hand panel (Risks · Heatmap · Loop)
  nav.jsx             — left navigation (Configuration · Execution · Governance Intelligence)
  report.jsx          — Loop Report modal + Override modal
  scenarios.jsx       — Grey Swan Scenarios panel
  styles-modules.css  — all component CSS
```

## Architecture

- **State**: All pipeline state lives in `app.jsx` (`stageState`, `output`, `gateState`). Persisted to `localStorage` as `dendrai.lastLoop` on each loop run; restored on page load.
- **Pipeline**: Six sequential stages (`s1`–`s6`) plus an optional `s7` for scenarios/personas. HITL gates after Stage 2 (Gate 1 — risk review) and Stage 3 (Gate 2 — scope review).
- **Global components**: Each component file registers itself on `window` (e.g. `window.ForecastChart`, `window.RiskFlowSankey`, `window.ScenariosPanel`) so pipeline sub-panels can poll and mount them lazily.
- **Live mode**: When enabled, fetches EDGAR companyfacts from `data.sec.gov` and FRED macro data from bundled snapshot. Disabled = mock dataset.

## Screens (left navigation)

| Nav item | Description |
|---|---|
| Setup | Configuration — ticker, industry, audit focus, signal sources |
| Pipeline | Six-stage loop execution with HITL gates |
| Controls Monitor | KRI / control-effectiveness tracker |
| MAPs | Management Action Plans dashboard |
| Notifications | Live signal notifications |
| Audit Scope | Audit objectives and sprint plan |
| Risk-as-Code | YAML risk register editor |
| Policy-as-Code | Policy definition editor |
| Grey Swan Scenarios | Bear / Base / Bull scenarios + Grey Swan cascade |
| Governance Intelligence | Board, exec comp, shareholder proposals, peer benchmarking |

## Live Register rail (right panel — Pipeline screen only)

Three tabs: **Risks** · **Heatmap** · **Loop**

## Loop Report

Opened via the "Loop Report" button in the pipeline header (enabled after a loop run completes). Includes:

1. Executive Summary
2. **Pipeline Execution** — all six stages with status, metrics, tasks, and stage reasoning
3. Methodology — signal source breakdown and scoring model
4. Changes to Risks & Audit Plan — HITL adjustments
5. Risk Register — 4-quarter score projections
6. Audit Objectives
7. Management Action Plans
8. Scenario Outlook (Bear / Base / Bull)
9. Grey Swan Risk
10. Stakeholder Highlights
11. Analytical Assumptions
12. Obstacles & Flags
13. Audit Trail

An "Generate AI report" button (Claude API) produces a board-ready narrative if an API key is configured.

## Key fixes and changes (post-handoff)

- **CSS bug**: `.stage-body { display: none }` in `styles-modules.css` requires the `.open` class to show content. React conditional rendering (`{isOpen && <div className="stage-body">}`) never added `.open`, making all stage body content invisible. Fixed by adding `open` to `className` in `Stage` and `PipelinePanel` components in `pipeline.jsx`.
- **Risk Flow Sankey visibility**: Was gated on Stage 5 completion (required two HITL approvals). Moved to show immediately after Stage 2 completes, alongside Forecast charts.
- **Forecast + Risk Flow charts**: Both appear after Stage 2 completes without requiring HITL gate approval.
- **Live Register rail**: Removed Notifs, Persona, and Scenarios tabs. Rail now shows only Risks · Heatmap · Loop.
- **Grey Swan Scenarios**: Moved from the Live Register rail to a dedicated center-pane screen, accessible via "Grey Swan Scenarios" in the left navigation.
- **Loop Report — Pipeline Execution section**: Added a full per-stage breakdown showing each stage's status, key metrics, task list (signals / risks / objectives / MAPs / recommendations), and stage reasoning trace.
- **Bug fix**: `narr.result.summary` (undefined variable) corrected to `narrativeResult.summary` in `pipeline.jsx`.

## Backend (dendrai-app)

See `dendrai-app/README.md` for the Express backend, Docker setup, and production deployment instructions.
