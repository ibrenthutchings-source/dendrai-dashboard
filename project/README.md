# Dendrai Intelligenza

A six-stage, AI-augmented internal audit and risk governance platform built in React. It derives live risk profiles from SEC EDGAR filings, FRED macro data, and industry RSS signals, runs them through a human-in-the-loop (HITL) review pipeline, and produces a Management Action Plan (MAP) register with quarterly projections and a closing loop calibration.

The platform pairs a **deterministic analytics core** (statistical models — ARIMA, Beneish M-score, correlation, templated scoring) with an **agentic layer** powered by Claude (`claude-sonnet-4-6`): AI-drafted HITL gate dispositions, SEC filing narrative analysis, role-tailored persona briefs, board-ready report generation, and a tool-use investigation agent. The deterministic numbers remain ground truth the model cites — never invents. See [AI-Augmented Features](#ai-augmented-features) and [MODEL_CARD.md](MODEL_CARD.md) for the full AI-component inventory, human-oversight level per feature, and known limitations.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Navigation & Layout](#navigation--layout)
- [Six-Stage Pipeline](#six-stage-pipeline)
- [Data Modes](#data-modes)
- [Live Register Rail](#live-register-rail)
- [Risk Engine](#risk-engine)
- [Predictive Analytics (10 Models)](#predictive-analytics-10-models)
- [AI-Augmented Features](#ai-augmented-features)
- [Peer Intelligence](#peer-intelligence)
- [API & MCP Servers](#api--mcp-servers)
- [File Structure](#file-structure)
- [Development Setup](#development-setup)
- [Environment Variables](#environment-variables)
- [Multi-Tenancy](#multi-tenancy)
- [Operations & Maintenance](#operations--maintenance)
- [Supported Tickers](#supported-tickers)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Browser  (React + Vite)                                    │
│                                                             │
│  Sidebar ─► Six-Stage Pipeline ─► Right Rail (Live Reg.)   │
│               │         │                                   │
│            HITL Gate 1  HITL Gate 2                         │
│          (Risk Review) (Scope Review)                       │
│                                                             │
│  Data sources:                                              │
│    Mock     — static industry template                      │
│    Live JS  — EDGAR direct fetch via Vite proxy             │
│    MCP      — Python FastAPI bridge → 10 analytics models   │
└───────────────────────┬─────────────────────────────────────┘
                        │ /api/mcp  (Vite proxy)
                        ▼
          ┌─────────────────────────┐
          │  Python MCP API Server  │
          │  api_server.py :8001    │
          │                         │
          │  /predictive/full-analysis
          │  /edgar/financials       │
          │  /edgar/risk-factors     │
          │  /rss/news               │
          │  /fred/correlations      │
          └─────────────────────────┘
                        │
          ┌─────────────┴────────────┐
          │                          │
    data.sec.gov               FRED API
    (SEC EDGAR XBRL)      (Federal Reserve)
```

A separate **Node.js MCP server** (`mcp-server/`) exposes the same EDGAR and FRED data as MCP tools for use inside Claude Code and other MCP-compatible agents.

---

## Six-Stage Pipeline

Each stage runs sequentially. HITL gates pause execution and surface per-risk or per-objective review UIs before proceeding.

| Stage | Name | Output |
|-------|------|--------|
| S1 | Signal Intake | Aggregated signals from EDGAR, peers, FRED, RSS, internal KRIs, incidents |
| S2 | Risk Assessment | 8 scored risks with velocity, RAG, CE, quarterly hist; signal-adjusted |
| **Gate 1** | Risk HITL | Per-risk approve / adjust / sign-off (CAE, CFO, Audit Committee) |
| S3 | Audit Scope | Up to 6 audit objectives prioritised P1–P3 by risk score |
| **Gate 2** | Scope HITL | Per-objective approve / adjust sprint, hours, linked risks |
| S4 | Management Action Plans | One MAP per objective with owner, due date, completion %, success criteria |
| S5 | Closure | Risk reduction stats, artifacts count, re-run recommendations |
| S6 | Loop Calibration | Audit impact score, next-cycle focus, lessons learned, schedule trigger |

### HITL Gate 1 — Risk Review
- Each risk can be **Approved**, **Adjusted** (auditor overrides score/RAG/velocity/CE), or escalated for **sign-off** (CAE → CFO → Audit Committee).
- Adjustments are merged back into `output.s2.risks` before Stage 3.
- "Bulk approve" clears all remaining pending risks at once.

### HITL Gate 2 — Scope Review
- Each audit objective can be **Approved** or **Adjusted** (change priority, sprint, hours, linked risks, residual risk reduction %).
- New objectives can be added ad-hoc.
- Adjusted objectives flow into MAP and Sankey chart calculations.

---

## Data Modes

Select in the sidebar. Each mode changes what gets fetched during a run.

### Mock Mode (default)
- No network calls. Industry templates in `risk-engine.js` generate synthetic risk profiles.
- Ratios default to `null`; all delta functions return 0. Scores equal the template base.
- Useful for demos and offline development.

### Live JS Mode
- Fetches EDGAR `companyfacts` JSON directly from `data.sec.gov` through the Vite CORS proxy.
- Extracts XBRL metrics (revenue, COGS, R&D, SG&A, assets, AR, net income, CFO, capex) via `live-data.js`.
- Computes financial ratios (gross margin, R&D intensity, FCF margin, M-score, DSRI, TATA, SGI).
- FRED bundled snapshot loaded from `project/data/fred_data.json`.
- RSS signals fetched in real time through the Vite RSS proxy (`/api/rss-proxy`).

### MCP Mode
- Delegates all ingestion to the Python FastAPI bridge (`api_server.py`).
- Runs all 10 predictive analytics models server-side (see [Predictive Analytics](#predictive-analytics-10-models)).
- Returns financial ratios, scored risks, FRED correlations, RSS signals, ensemble forecast, backtesting metrics, and QoQ momentum in a single `/predictive/full-analysis` response.
- Falls back to the industry template if the bridge is unreachable.

---

## Navigation & Layout

The left nav routes the main canvas (Setup, Pipeline, Controls Monitor, MAPs, Notifications, Audit Scope, Risk-as-Code, Policy-as-Code, Governance Intelligence, Token Usage, User Configuration).

The **Live Register rail** is a contextual right-hand pane that appears **only on the Pipeline screen, and only after a run has produced data** (`hasRun`). It is the single home for the risk-output views that used to be scattered as separate nav screens and pipeline sub-tabs — **Risk Register, Risk Flow, Forecasts, and Scenarios all live here now** (they are no longer separate left-nav items or sub-tabs). The main canvas widens to a third grid column (`.app-body.has-rail`) when the rail is shown.

## Live Register Rail

Nine tabs, populated progressively as stages complete:

| Tab | Contents |
|-----|----------|
| **Risks** | Full risk register — RAG dot, score, velocity pill, sparkline trend, control effectiveness. Click any row to expand the 4-quarter projection panel. |
| **Heatmap** | Impact × Likelihood bubble chart. Animated Q1–Q4 projections driven by velocity. Dashed circles show Q4 forecast positions. Click a bubble for velocity/delta detail. |
| **Risk Flow** | Mini Risk Flow — top risks fanning out to impact areas and audit buckets. "Full view" drills into the complete Sankey chart. |
| **Forecasts** | Revenue / gross-margin history + 4-quarter ensemble forecast with confidence band (moved in from the old pipeline sub-tab). |
| **Scenarios** | Bear / Base / Bull scenario outlook + Grey Swan escalation model (moved in from the old pipeline sub-tab). |
| **MAPs** | Management Action Plan cards — finding, owner, due date, progress bar, completion %, estimated risk reduction %. |
| **Loop** | Loop calibration stats (risk reduction %, MAPs open, risks closed, next cycle). Lessons learned. **Recurring Schedule** panel to generate and copy a `/schedule` command (monthly / weekly / quarterly cadence). |
| **Notifs** | Escalation notification log from the Controls Event Monitor (CEM). Shows tier, message, sent time, ACK status. |
| **Persona** | Role-tailored summaries for Chief Audit Executive, CFO, and COO. Includes a **"Generate with AI"** button that replaces the template brief with a Claude-generated one (see [AI-Augmented Features](#ai-augmented-features)). |

### Recurring Schedule Panel (Loop tab)
After a run completes, the **Loop → Recurring Schedule** panel generates a pre-filled `/schedule` command. Pick a cadence (Monthly / Weekly / Quarterly), review the auto-populated next-cycle focus text (derived from velocity-3 risks), then copy the command and paste it into the Claude Code terminal to register a cloud agent that runs the loop automatically.

---

## Risk Engine

`risk-engine.js` — pure JavaScript, runs entirely in the browser.

### Industry Templates
Eight industries, eight risks each, all parameterised by EDGAR financial ratios:

| Industry | Key Risks |
|----------|-----------|
| **Semiconductors** | Revenue Concentration, R&D Execution, Financial Reporting (Accruals), Supply Chain, Gross Margin Compression, Cybersecurity (IP), Export Controls, CapEx & Capacity |
| **Automotive OEM** | EV Transition, Captive Finance, Supply Chain (Semiconductors), Labor/CBA, Product Recall, Gross Margin/Incentives, Connected Vehicle Cyber, ESG/ICE Phase-out |
| **Software & Cloud** | Revenue Recognition (ASC 606), Customer Churn/NRR, Cybersecurity, AI/Regulatory Bias, Competitive Disruption, Gross Margin/Operating Leverage, M&A Integration, Talent |
| **Financial Services** | Credit Quality/CECL, NIM Sensitivity, Liquidity, CET1/DFAST, AML/Fraud, Cybersecurity, Model Risk, Market Risk/Derivatives |
| **Healthcare & Pharma** | Pipeline/FDA, Pricing/IRA, Patent Cliff, GMP/Regulatory, R&D Productivity, Litigation/FCA, Cybersecurity (PHI), ESG |
| **Generic** | Financial Reporting, Revenue Concentration, Margin Compression, Liquidity/FCF, Cybersecurity, Regulatory Compliance, Operational Execution, Strategic Concentration |

Industrial & Manufacturing, Retail & Consumer, Energy & Resources, and Utilities alias to Generic.

### Score Computation
```
score = clamp(base + delta(ratios), 1.5, 9.5)
velocity = velOf(score, base)   // -1 to +3 from ratio delta
ce = ceOf(score, base)          // STRONG / ADEQUATE / WEAK based on delta
hist = histOf(score, base)      // 6-point linear history base→score
```

After Stage 1, `adjustRiskScores()` overlays signal adjustments:
- **FRED contractionary signals** → +0.08 per signal to macro-category risks
- **RSS signals linked to a risk** → +(velocity × 0.08) and velocity = max(base, RSS max)
- **High-velocity industry signals** (velocity ≥ 3) → +0.05 each, capped at +0.20, applied to all risks

The `hist` array and `ce` are regenerated after adjustment so the sparkline direction and likelihood position on the heatmap always agree with the adjusted velocity.

### RAG Thresholds
| Score | RAG |
|-------|-----|
| ≥ 7.5 | RED |
| 5.0 – 7.4 | AMBER |
| < 5.0 | GREEN |

### Quarterly Projections
```
Q(n) = score + velocity × 0.85^(n-1) × ceMultiplier × 0.4
```
CE multipliers: `NONE → 1.20 × | WEAK → 1.10 × | ADEQUATE → 0.95 × | STRONG → 0.80 ×`

### Other Engine Outputs
- **Objectives** — top 6 risks by score mapped to audit objectives with sprint, hours, controls
- **MAPs** — one per objective with finding, root cause, action, owner, success criteria, reduction %
- **Scenarios** — Bear / Base / Bull with probability, revenue impact, gross margin impact, audit focus
- **Grey Swan** — escalation cascade model (T+0 → T+90) from highest-velocity amber risk
- **Forecasts** — revenue and gross margin history + 4-quarter forecast with confidence band
- **FRED signals** — 5 industry-relevant macro series with correlation, lead time, and direction
- **Risk Flow** — impact area mapping, control catalogue, audit cadence by velocity tier
- **Personas** — CAE, CFO, COO role-filtered summaries, plus AI-generated audience-layer briefs (Technical Executive, Non-Technical Executive, Board)

---

## Predictive Analytics (10 Models)

Implemented in `project/agentic-tools/predictive_analytics_tool.py`. Activated via MCP mode.

| # | Model | Description |
|---|-------|-------------|
| 1 | **Financial Ratio Analysis** | Revenue growth, gross margin, R&D intensity, FCF margin, DSRI, TATA, SGI from EDGAR XBRL |
| 2 | **Beneish M-Score** | 5-variable earnings manipulation detection (M > −1.78 = elevated, M > −2.22 = gray zone) |
| 3 | **Industry-Templated Risk Scoring** | Delta-adjusted scores across 8 verticals × 8 risks using live ratios |
| 4 | **Scenario Analysis** | Bear / Base / Bull with revenue impact %, gross margin delta, liquidity rating, recovery horizon |
| 5 | **Grey Swan Model** | 4-stage escalation cascade (T+0/+30/+60/+90) with probability, impact $M, catalysts, mitigations |
| 6 | **FRED Macro Leading Indicators** | Pearson cross-correlation at lags 1–4 between FRED series and company revenue; selects top correlators |
| 7 | **Time-Series Forecasting** | ARIMA, Prophet-like trend+seasonality, Random Forest (lags 1–4, rolling stats, lag-aligned FRED leading-indicator features — Revenue/EPS/NetIncome/EBITDA, real `FRED_API_KEY` only), ensemble by inverse MAPE |
| 8 | **Walk-Forward Backtesting** | MAPE, RMSE, R², directional accuracy F1 across rolling windows |
| 9 | **RSS Signal Grading** | NLP-lite relevance × severity pipeline on industry RSS feeds; maps signals to affected risk IDs |
| 10 | **QoQ Revenue Momentum / Sentiment** | Rolling 8-quarter momentum score, hedge ratio trend, deteriorating/stable/improving classification |

---

## AI-Augmented Features

The agentic layer puts Claude (`claude-sonnet-4-6`, adaptive thinking) in the loop alongside the deterministic models. All of it goes through one shared client, `agentic-tools/claude_client.py` (model selection, prompt caching, structured-output handling, token-cost accounting). Every feature **degrades gracefully**: if `ANTHROPIC_API_KEY` is not set on the Python bridge, the AI routes return HTTP `503` and the deterministic pipeline is unaffected — the UI affordances simply don't appear.

See [MODEL_CARD.md](MODEL_CARD.md) for the human-oversight level of each feature below, the deterministic/statistical models alongside them, and known bias/limitation findings.

| Feature | UI surface | Endpoint | What it does |
|---------|-----------|----------|--------------|
| **AI-assisted HITL — Gate 1** | "Suggest with AI" in the Adjust Risk modal | `POST /ai/gate1/recommend` | Drafts a per-risk disposition (approve / adjust RAG·score·velocity·CE) with a cited rationale; the auditor accepts or overrides. |
| **AI-assisted HITL — Gate 2** | "Suggest with AI" in the Adjust Objective modal | `POST /ai/gate2/recommend` | Drafts per-objective scope (priority, sprint, hours, linked risks) justified against the linked risks. |
| **Narrative analysis** | — (server) | `POST /ai/narrative-analysis` | Extracts emerging risks and year-over-year language shifts from Item 1A / DEF 14A text the app already downloads, mapped to register categories. |
| **Persona brief** | "Generate with AI" on the Persona tab | `POST /ai/persona-brief` | Role-tailored briefing from the scored register — by function (CAE / CFO / COO) or by audience layer (Technical Executive / Non-Technical Executive / Board). |
| **Audit report** | "Generate AI report" in the Loop Report modal | `POST /ai/audit-report` | Board-ready Markdown report from the full run output. |
| **Investigation agent** | "Run investigation" card on the Setup screen | `POST /agent/investigate` | Tool-use agent that decides its own path — pulls financials, follows anomalies into filings, benchmarks peers, runs the quant models — and writes an investigation memo. |
| **Council investigation** | "Run council" card on the Setup screen | `POST /agent/investigate/council` | Three independent analysts (financial, operational/cyber, compliance/regulatory) investigate the same ticker in parallel over the same tools, then a synthesis pass reports convergent (multi-analyst) vs. single-analyst findings — the same independent-voters-then-reconciliation pattern as the MCP governance adjudication ensemble, applied to investigation instead of tool-call risk. |

Every AI output is persisted with provenance (model, effort, tokens, cost) to the `ai_analyses` table and is readable via `GET /history/runs/{run_id}/ai-analyses`.

The investigation agent's tool surface (`agentic-tools/agent_tools.py`) wraps the existing EDGAR / FRED / RSS / analytics functions as Claude tools: `get_financials`, `get_risk_factors`, `get_8k_events`, `get_peers`, `get_industry_news`, `run_quant_models`. The quant models are presented to the agent as ground truth to cite.

### Managed Agents (scheduled cloud agent)

`agentic-tools/managed_agent_setup.py` provisions a **Managed Agents** deployment — a persisted Agent + Environment + cron Deployment that re-investigates a ticker autonomously on a schedule, the real version of the Loop tab's copy-paste `/schedule` panel. Run once: `python managed_agent_setup.py --ticker ON --cron "0 8 * * 1"` (optional `--run-now` to smoke-test, `--list` to see runs).

---

## Peer Intelligence

Governance Intelligence → **Peer Benchmarking** sources peers from the **competitors the target names in its own 10-K**, not from a noisy SIC-code sweep.

1. The latest 10-K's Competition discussion (Item 1 Business) is located and the named competitors are extracted by Claude (`agentic-tools/peer_intel.py`).
2. Each name is resolved to an EDGAR CIK/ticker — by exact ticker symbol (catches abbreviations like "AMD") or fuzzy title match.
3. Resolved peers are enriched with gross margin, R&D intensity, and revenue growth from XBRL.
4. **Companies with no financial data are dropped** — including foreign competitors with no US EDGAR financials.

If the company names no competitors (some 10-Ks are generic) or `ANTHROPIC_API_KEY` is unset, it **falls back to SIC-code peers** — also filtered for data. The response carries `peer_source` (`"10-K named competitors"` or `"SIC peers"`) and the full `named_competitors` list, surfaced in the UI (e.g. for Intel: AMD, NVIDIA, Qualcomm, Broadcom, … with TSMC / Samsung / MediaTek dropped as having no US financial data).

---

## API & MCP Servers

### Python FastAPI Bridge (`project/agentic-tools/api_server.py`)

HTTP bridge between the browser app and the Python analytics tools.

```bash
cd project/agentic-tools
pip install -r requirements.txt
python api_server.py              # http://127.0.0.1:8001
python api_server.py --port 8002  # custom port
```

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Liveness check — reports `ai_enabled` / `ai_model` |
| GET | `/db/status` | Whether Postgres persistence is configured |
| POST | `/predictive/full-analysis` | All 10 analytics models in one call |
| POST | `/edgar/financials` | XBRL financial time-series |
| POST | `/edgar/risk-factors` | Item 1A risk factors from 10-K filings |
| POST | `/edgar/8k-events` | Annotated 8-K material events |
| POST | `/edgar/peers` | Peer intelligence — 10-K-named competitors, no-data dropped (see [Peer Intelligence](#peer-intelligence)) |
| POST | `/edgar/proxy` | DEF 14A governance sections |
| POST | `/rss/news` | Industry RSS feed analysis |
| POST | `/fred/correlations` | FRED macro leading indicator correlations |
| POST | `/ai/gate1/recommend` · `/ai/gate2/recommend` | AI-assisted HITL gate dispositions |
| POST | `/ai/narrative-analysis` | Item 1A / proxy narrative extraction |
| POST | `/ai/persona-brief` · `/ai/audit-report` | Persona brief / Markdown audit report |
| POST | `/agent/investigate` · `/agent/investigate/council` | Tool-use investigation agent (single or 3-perspective ensemble) |
| POST | `/loop/hitl/risk-approvals` · `/loop/hitl/scope-approvals` · `/loop/persist` | Persist HITL decisions + loop completion |
| GET | `/history/runs/{ticker}` · `/history/runs/{ticker}/{run_id}` | Run history |
| GET | `/history/runs/{run_id}/ai-analyses` | Persisted AI outputs for a run |
| GET | `/token-usage/summary` | Token usage by user and by feature/source (rolling window) + month/year, MTD/YTD rollups — powers the Token Usage screen |

The `/ai/*` and `/agent/*` routes require `ANTHROPIC_API_KEY`; without it they return `503`. Interactive API docs available at `http://127.0.0.1:8001/docs`.

The Vite dev server proxies `/api/mcp/*` → `http://127.0.0.1:8001/*` so the browser never makes cross-origin requests.

### Python MCP Tool Servers (individual)

Each tool also ships as a standalone MCP server for use with Claude Desktop or other MCP clients:

| Server | Tool | Description |
|--------|------|-------------|
| `edgar_mcp_server.py` | `edgar_tool.py` | SEC EDGAR XBRL financials, risk factors, peer lookup |
| `fred_mcp_server.py` | `fred_tool.py` | FRED macro series correlation analysis |
| `rss_mcp_server.py` | `rss_tool.py` | Industry RSS signal grading |
| `predictive_analytics_mcp_server.py` | `predictive_analytics_tool.py` | All 10 analytics models |
| `token_cost_mcp_server.py` | `token_cost_tool.py` | Anthropic API token cost estimation |
| `oracle_fusion_mcp_server.py` | `oracle_fusion_tool.py` | Oracle Fusion Cloud RMCS/FSCM control data |
| `risk_as_code_mcp_server.py` | `risks_as_code.py` | OSCAL / COSO ERM Risk-as-Code artifact generation |
| `pac_mcp_server.py` | `pac_endpoints.py` | Policy-as-Code Rego modules + negative-control testing |
| `cac_mcp_server.py` | `pac_endpoints.py` | Controls-as-Code artifact generation from PaC deny rules |
| `devops_monitoring_mcp_server.py` | `scm_audit_endpoints.py` / `evidence_endpoints.py` | SCM branch-protection + pipeline-as-code security audits, real `gitleaks` secret scanning, SARIF evidence with tamper-evidence hash chain, risk waivers, ITSM SLA bridge, DORA change-management metrics |
| `infrastructure_monitoring_mcp_server.py` | `postgres_cis_tool.py` / `railway_iaas_tool.py` | Continuous Postgres CIS hardening + Railway platform/deployment drift + connector credential rotation hygiene audits |

### Node.js MCP Server (`mcp-server/`)

TypeScript MCP server with 7 tools. Built with `@modelcontextprotocol/sdk`.

```bash
cd mcp-server
npm install
npm run build
node dist/index.js
```

| Tool | Description |
|------|-------------|
| `search_fred_series` | Search FRED by keyword — returns series IDs and metadata |
| `get_fred_series_info` | Get metadata for a specific FRED series ID |
| `get_fred_observations` | Fetch time-series observations with date range, frequency, unit transform |
| `lookup_company` | Look up SEC EDGAR company by ticker or name — returns CIK |
| `get_company_financials` | Fetch 10-K/10-Q XBRL financial metrics |
| `get_peers_by_industry` | Find peers by 4-digit SIC code |
| `get_company_risks` | Extract Item 1A Risk Factors from recent 10-K/10-Q filings |

Add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "dendrai-financial": {
      "command": "node",
      "args": ["<absolute-path>/mcp-server/dist/index.js"],
      "env": {
        "FRED_API_KEY": "<your-fred-api-key>"
      }
    }
  }
}
```

---

## File Structure

```
dendrai-dashboard/
├── project/                        # Main React app (Vite)
│   ├── src/
│   │   └── main.jsx                # Entry point, module load order
│   ├── app.jsx                     # Root component, run loop, HITL gates
│   ├── sidebar.jsx                 # Config panel, data mode toggles, run controls
│   ├── pipeline.jsx                # Six-stage pipeline UI, gate approval UI
│   ├── rail.jsx                    # Right rail — Risks/Heatmap/MAPs/Loop/Notifs/Flow/Persona tabs
│   ├── charts.jsx                  # SVG charts — Heatmap, ForecastChart, MScoreGauge, RiskFlowSankey
│   ├── components.jsx              # Shared primitives — Icon, Sparkline, VelocityPill, projectQuarters
│   ├── risk-engine.js              # Risk profile builder — templates, scoring, objectives, MAPs
│   ├── live-data.js                # EDGAR direct fetch + financial ratio extraction
│   ├── mcp-data.js                 # Python MCP API client (window.MCP)
│   ├── mock-data.js                # CEM event templates
│   ├── rss-engine.js               # RSS feed ingestion + signal grading
│   ├── forecasting.js              # ARIMA/ensemble forecasting (JS)
│   ├── backtesting.js              # Walk-forward backtesting (JS)
│   ├── nav.jsx                     # Left navigation rail (menu → main canvas)
│   ├── rail.jsx                    # Right-hand Live Register rail (pipeline, post-run)
│   ├── cem.jsx                     # Controls Event Monitor panel
│   ├── forecasts.jsx               # Forecasts panel (now a rail tab)
│   ├── scenarios.jsx               # Scenarios + Grey Swan panel (now a rail tab)
│   ├── flow.jsx                    # Risk Flow Sankey full panel
│   ├── governance.jsx              # Governance Intelligence + Peer Benchmarking
│   ├── config-screen.jsx           # Setup screen (config, schedule, investigation agent)
│   ├── report.jsx                  # Audit report modal (+ AI report generation)
│   ├── risk-approval.jsx           # HITL Gate 1 per-risk adjust modal (+ AI suggest)
│   ├── audit-scope-review.jsx      # HITL Gate 2 per-objective adjust modal (+ AI suggest)
│   ├── rss.jsx                     # RSS Signals sub-tab
│   ├── tweaks.jsx                  # Tweaks panel hook + state
│   ├── tweaks-panel.jsx            # Tweaks panel UI (accent, density, run speed)
│   ├── data-config-modal.jsx       # Company/period config modal
│   ├── user-config.jsx             # Admin: local accounts + per-user Screen Access matrix
│   ├── token-usage.jsx             # Token usage by user / by feature / calendar rollups (MTD, YTD)
│   ├── vite.config.js              # Vite config — CORS proxies (EDGAR, SEC, MCP, RSS)
│   ├── data/
│   │   └── fred_data.json          # Bundled FRED snapshot (Q1 2021 → Q1 2026)
│   └── agentic-tools/              # Python analytics + AI backend
│       ├── api_server.py           # FastAPI bridge — exposes tools as REST endpoints
│       ├── claude_client.py        # Shared Claude client (model, caching, tool loop, cost)
│       ├── ai_endpoints.py         # AI router — gate recs, narrative, persona, report
│       ├── agent_tools.py          # Investigation agent tool surface (EDGAR/FRED/RSS/quant)
│       ├── peer_intel.py           # 10-K competitor extraction + EDGAR resolution
│       ├── managed_agent_setup.py  # Managed Agents scheduled deployment (control plane)
│       ├── db.py                   # Postgres persistence (incl. ai_analyses + migrations)
│       ├── integration_test.py     # Live DB INSERT/UPDATE/UPSERT integration test
│       ├── predictive_analytics_tool.py   # 10 analytics models
│       ├── predictive_analytics_mcp_server.py
│       ├── edgar_tool.py           # SEC EDGAR XBRL fetcher
│       ├── edgar_mcp_server.py
│       ├── fred_tool.py            # FRED macro correlation analysis
│       ├── fred_mcp_server.py
│       ├── rss_tool.py             # RSS signal grading
│       ├── rss_mcp_server.py
│       ├── token_cost_tool.py      # Token cost estimator
│       ├── token_cost_mcp_server.py
│       ├── requirements.txt
│       ├── .env.example            # DATABASE_URL · ANTHROPIC_API_KEY · FRED_API_KEY
│       └── ON/                     # Cached EDGAR data for onsemi (ON)
│           ├── financials.json
│           ├── proxy.json
│           └── risks.json
└── mcp-server/                     # Node.js MCP server
    ├── src/
    │   ├── index.ts                # Server entry — registers 7 MCP tools
    │   ├── tools/
    │   │   ├── edgar.ts            # EDGAR tools (lookup, financials, peers, risks)
    │   │   └── fred.ts             # FRED tools (search, info, observations)
    │   └── utils/
    │       └── http.ts             # Shared fetch helpers
    ├── dist/                       # Compiled output
    ├── package.json
    └── tsconfig.json
```

---

## Development Setup

### Prerequisites
- Node.js 18+
- Python 3.11+

### 1. React App (Vite)

```bash
cd project
npm install
npm run dev
```

Opens at `http://localhost:5173`. Hot-reload on all `.jsx` and `.js` changes.

```bash
npm run build    # production build → project/dist
npm run preview  # serve the production build locally
```

### 2. Python MCP API Server (optional — required for MCP mode)

```bash
cd project/agentic-tools
pip install -r requirements.txt
python api_server.py              # http://127.0.0.1:8001
```

With the server running, enable **MCP mode** in the sidebar to route all data through the Python analytics stack instead of the JS fetch layer.

### 3. Node.js MCP Server (optional — for Claude Desktop / Claude Code)

```bash
cd mcp-server
npm install
npm run build
```

Then register in `claude_desktop_config.json` as shown in [Node.js MCP Server](#nodejs-mcp-server-mcp-server).

---

## Environment Variables

Place a `.env` file in `project/agentic-tools/` (copy `.env.example`). The bridge loads it automatically via `python-dotenv`. **None are required to start** — features unlock as each is set.

| Variable | Enables | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | All AI features (`/ai/*`, `/agent/*`, 10-K peer intelligence) | Without it those routes return `503` and the deterministic pipeline is unaffected. |
| `DATABASE_URL` | Postgres persistence | `postgresql://user:pass@host:port/db`. Unset = persistence disabled (app still runs). On Railway use the public proxy host (`…proxy.rlwy.net:PORT`) from outside Railway, or the internal host when deployed on Railway. The schema self-heals on startup (`db.init_db()` runs DDL + idempotent column migrations). |
| `FRED_API_KEY` | FRED correlation analysis | Free key from [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html). |
| `DENDRAI_CLAUDE_MODEL` | — | Optional model override (default `claude-sonnet-4-6`). |
| `DENDRAI_MCP_URL` | Managed Agents tool access | Optional hosted MCP server URL for the scheduled cloud agent. |

> **Secrets:** `.env` files are git-ignored — never commit real keys; the committed `.env.example` files are the templates.

### Database persistence

When `DATABASE_URL` is set, runs persist to a normalized Postgres schema (companies, XBRL series, risk scores, HITL approvals, AI analyses, token usage, …). `integration_test.py` exercises the INSERT / UPDATE / UPSERT path end-to-end against a live database and cleans up after itself:

```bash
cd project/agentic-tools
DATABASE_URL=postgresql://… python integration_test.py
```

### Automated tests + CI

`project/agentic-tools/tests/` is a pytest suite, split in two:

- `test_pure_functions.py` — fast, DB-free unit tests for regex/parsing/formatting helpers (Rego code-fence stripping, control-ID extraction, RAG-band boundaries, digest delta math), including regression guards for bugs this codebase has actually hit in production (e.g. a `VARCHAR` column too narrow for a real value).
- `test_db_integration.py` — a pytest port of `integration_test.py`'s live-Postgres round-trip checks, `skip`ped automatically when `DATABASE_URL` isn't set.

```bash
cd project/agentic-tools
python -m pytest tests -v                                    # pure-function tests only (no DB needed)
DATABASE_URL=postgresql://… python -m pytest tests -v         # full suite, including DB integration tests
```

`.github/workflows/ci.yml` runs the full suite (with a throwaway `postgres:16` service container) on every push and pull request to `main`.

---

## Multi-Tenancy

The backend runs in one of two modes, chosen by the `TENANT_MODE` environment variable (set it in `project/agentic-tools/.env` locally, or in the backend service's variables on your host; restart after changing it).

| Mode | Behavior |
|------|----------|
| `single` (default) | One database (`DATABASE_URL`), one set of secrets. No subdomain resolution. Local dev and any deployment that hasn't opted in. |
| `multi` | **Database-per-tenant.** Each customer gets a physically separate Postgres database and its own secrets, resolved per request from the `Host` header (`companyx.dendrai.ai` → tenant `companyx`). No `tenant_id` columns — database separation is the isolation boundary. |

### How it works

- **Control plane** (`control_plane.py`) — a small separate database (`CONTROL_DATABASE_URL`) holding only the tenant registry and each tenant's encrypted DSN + secret bundle. Encrypted with `CONTROL_PLANE_ROOT_KEY`.
- **Resolution** — `_TenantResolutionMiddleware` (`api_server.py`) is the outermost middleware. It resolves the subdomain, binds that tenant's connection pool and secrets for the request, and rejects unknown or suspended tenants (`404 Unknown tenant`) before any route runs. There is no fallback to a default tenant.
- **Per-tenant secrets** — JWT signing key, evidence signing key, connector encryption key and the write-guard API key are generated per tenant at provisioning. The audit-log signing key is derived from the tenant's evidence key.
- **Background jobs** — sweeps and pollers iterate every active tenant; one tenant's failure does not stop the others.
- **Caches** — in-process caches that hold tenant-derived data (e.g. RSS grading) are keyed by tenant.

### Required environment variables (`TENANT_MODE=multi`)

| Variable | Description |
|----------|-------------|
| `CONTROL_DATABASE_URL` | Postgres URL of the control-plane database (registry only — no customer data). Every app instance needs it. |
| `CONTROL_PLANE_ROOT_KEY` | Fernet key encrypting tenant DSNs and secrets. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. **Must stay stable** — rotating it makes every tenant unreachable until re-provisioned. Back it up outside the database it protects. |
| `TENANT_DB_ADMIN_DSN` | Postgres DSN with `CREATEDB` privilege. Used **only** by `provision_tenant.py`, not at request time. |
| `TENANT_ROOT_DOMAIN` | Wildcard domain tenants are served under (default `dendrai.ai`). Drives the CORS allow-list. |
| `TENANT_CACHE_TTL_S` | How long a resolved tenant is cached (default `30`). Bounds how fast a suspension takes effect. |
| `TENANT_POOL_CACHE_SIZE` | Max open tenant connection pools per app instance (default `50`, least-recently-used evicted). |

In multi mode the global `DATABASE_URL`, `AUTH_JWT_SECRET`, `EVIDENCE_SIGNING_KEY` and `CONNECTOR_ENCRYPTION_KEY` are not used for tenant traffic.

### Adding a tenant

Prerequisites: `TENANT_MODE=multi`, `CONTROL_DATABASE_URL`, `CONTROL_PLANE_ROOT_KEY` and `TENANT_DB_ADMIN_DSN` set, and a wildcard DNS record (`*.dendrai.ai`) plus wildcard TLS certificate pointing at the app.

```bash
cd project/agentic-tools
python provision_tenant.py --slug companyx --display-name "Company X Inc."
```

The slug must be 3–63 lowercase letters, digits or hyphens, start with a letter and not end in a hyphen. It becomes both the subdomain and the database name (`tenant_<slug>`, hyphens folded to underscores). Optional flags: `--db-name` to override the database name, `--admin-dsn` to override `TENANT_DB_ADMIN_DSN`.

The script, in order: creates the database, applies the full schema, generates the tenant's four secrets, seeds the auth schema and the two default accounts, then registers the tenant in the control plane (visible to every instance immediately, no restart). Initial passwords come from `AUTH_SEED_ADMIN_PASSWORD` / `AUTH_SEED_USER_PASSWORD`, or a random one-time password printed in the script output — **capture it from the output**. An existing database with the same name is reused.

Verify: open `https://companyx.<TENANT_ROOT_DOMAIN>`, sign in with the seeded admin, and change the password.

### Managing tenants

`tenant_admin.py` covers day-2 operations (needs the same control-plane env vars):

```bash
python tenant_admin.py list                       # every tenant and its status
python tenant_admin.py suspend  --slug companyx   # requests get 404 within TENANT_CACHE_TTL_S
python tenant_admin.py activate --slug companyx
python tenant_admin.py migrate                    # re-apply schema to ALL active tenants
python tenant_admin.py migrate  --slug companyx   # ...or just one
```

Suspension blocks access but keeps the database. There is no automated tenant deletion; removing a tenant's data means dropping its `tenant_<slug>` database and its control-plane rows by hand, which is irreversible — take a backup first.

### Local development in multi mode

Requests must carry a tenant subdomain, including on localhost. Browsers resolve `companyx.localhost` to `127.0.0.1`, so open `http://companyx.localhost:5173`. The Vite dev proxy must forward the original `Host` header to the API for tenant resolution to work.

### Known limitations

- Schema changes reach existing tenants only when you run `tenant_admin.py migrate`; app startup does not migrate tenant databases.
- The frontend has no tenant concept; it relies on being served from the tenant's subdomain.
- The standalone `risk-control-register/` app (on the `Prova` branch) runs single-tenant only.

---

## Operations & Maintenance

A checklist for the team running a deployment. Items marked *(multi)* apply only with `TENANT_MODE=multi`.

### Every release

1. CI (`.github/workflows/ci.yml`) must be green — it runs the full pytest suite against a throwaway Postgres on every push and PR to `main`. Locally: `cd project/agentic-tools && python -m pytest tests -v`.
2. Build the frontend (`cd project && npm run build`) — `project/dist/` is committed, so rebuild and commit it with frontend changes.
3. Deploy the backend. In single mode the schema self-heals on startup (`db.init_db()` runs DDL and idempotent migrations).
4. *(multi)* Run `python tenant_admin.py migrate` after any release that changes the schema, then spot-check one tenant, before users hit the new code paths.
5. If releasing a version: `npm version patch|minor|major && git push --follow-tags` (see `RELEASING.md`).
6. Smoke test: `GET /health` on the backend, sign in, open the Framework Matrix, remove a control, reload and confirm it stays removed.

### Secrets and keys

| Secret | Rotation guidance |
|--------|-------------------|
| `CONTROL_PLANE_ROOT_KEY` *(multi)* | **Do not rotate casually** — it makes every tenant unreachable. Back it up in a secrets manager separate from the control database. |
| `AUTH_JWT_SECRET` | If unset it is random per restart, which logs everyone out on every deploy — set it explicitly. Rotating it signs all users out (plan for it). *(multi: generated per tenant.)* |
| `CONNECTOR_ENCRYPTION_KEY` | Encrypts stored connector credentials and ingest keys. Changing it makes existing encrypted values undecryptable — re-enter connector credentials afterwards. |
| `EVIDENCE_SIGNING_KEY` / `AUDIT_SIGNING_KEY` | Changing them stops old HMAC signatures re-verifying (chain hashes still work). Keep stable; set `AUDIT_SIGNING_KEY` explicitly in production. |
| Connector credentials | The daily `connector_hygiene_sweep` flags credentials older than 90 days — rotate when flagged. `POST /observability/systems/{id}/rotate-key` rotates a monitored system's ingest key. |
| SSO / webhook / write tokens (`GITHUB_WEBHOOK_SECRET`, `GITHUB_WRITE_TOKEN`, OAuth client secrets) | Rotate on staff changes and per your policy; always set `GITHUB_WEBHOOK_SECRET` in production. |

Never commit `.env`; `.env.example` is the template. Review who can read host-level variables when staff with access leave.

### Database

- **Backups:** enable automated Postgres backups and test a restore at least quarterly. *(multi)* Back up the control-plane database **and** every tenant database — each is useless without the other.
- **Connections:** *(multi)* each instance holds up to `TENANT_POOL_CACHE_SIZE` pools of up to 3 connections each; size Postgres `max_connections` for instances × pools × 3.
- **Retention:** `pii_retention_sweep` purges exception events older than `EXCEPTION_EVENT_RETENTION_DAYS` (default 400) daily. Align this with your data-retention policy and document any change.
- **Growth:** watch the size of `observability.*` tables (telemetry, audit log, evidence records) and plan archiving or partitioning before they become slow.

### Monitoring and routine checks

| Cadence | Check |
|---------|-------|
| Continuous | Uptime on `GET /health`; alerts on 5xx rate and database connection errors. Set `MCP_ALERT_WEBHOOK_URL` so ESCALATE verdicts and model-drift incidents reach Slack. |
| Daily | Skim backend logs for sweep failures (each sweep logs and continues on error — failures are quiet, not fatal). *(multi)* Look for `failed for tenant` lines. |
| Weekly | Review open Model Health drift incidents, the Approval Inbox backlog, and connector status (stale or failing pollers). |
| Monthly | Run `GET /auth/admin/audit-log/verify` to confirm the audit chain is intact; review user accounts and role assignments; review SSO provider config. |
| Quarterly | Test a backup restore; rotate stale credentials; review `EXCEPTION_EVENT_RETENTION_DAYS`; check `pip list --outdated` / `npm audit` and patch dependencies; review `DENDRAI_CLAUDE_MODEL` against current models. |

### Dependencies and runtime

- Python 3.11+ and Node 18+. Keep `requirements.txt` and `package-lock.json` committed; update through a PR with CI.
- The Docker image installs a real OPA binary for Policy-as-Code; local dev without OPA falls back to a labelled heuristic — don't treat heuristic results as authoritative.
- AI features need `ANTHROPIC_API_KEY`; without it AI routes return 503 and the deterministic pipeline keeps working. Watch token spend on the Token Usage screen.

### Incident basics

- **Users logged out after a deploy:** check `AUTH_JWT_SECRET` is set and unchanged.
- **Connector or ingest calls returning 503:** `CONNECTOR_ENCRYPTION_KEY` is missing or changed.
- **`404 Unknown tenant`** *(multi)*: wrong subdomain or tenant suspended (an unreachable control plane returns 503 instead). Check `tenant_admin.py list`.
- **A tenant is missing new features or tables after a release** *(multi)*: run `tenant_admin.py migrate`.
- **Suspected tampering:** run the audit-log verify endpoint and `/evidence/records/{id}/verify`; preserve logs before changing anything.

---

## Supported Tickers (Live JS mode — pre-seeded CIK map)

The following tickers resolve to CIK numbers without requiring a dynamic SEC lookup. Any other US-listed ticker is looked up dynamically via `company_tickers.json`.

**Semiconductors:** ON, TXN, STM, MCHP, NXPI, ADI, SWKS, QRVO, MPWR, WOLF, AVGO, NVDA, INTC, AMD, QCOM, MRVL

**Equipment / Packaging:** AMAT, KLAC, LRCX, ASML, AMKR, ONTO, TER, ENTG

**Memory:** MU, WDC

**Automotive:** F

---

## Notes

- `project/agentic-tools/ON/` contains pre-fetched EDGAR data for onsemi (ticker: ON) so the app can demonstrate live financial data without a network call during development.
- All SVG charts are pure hand-coded SVG — no chart library dependency.
- The Vite RSS proxy (`/api/rss-proxy`) bypasses CORS on external RSS feeds during development; it is not available in the production build without a reverse proxy.
- Global helpers (`window.RISK_ENGINE`, `window.LIVE`, `window.MCP`, `window.RSS_ENGINE`, etc.) are used for cross-file sharing because the non-ESM source files predate the Vite migration. `src/main.jsx` controls load order.
- The legacy prototype `project/Dendrai Risk Loop.html` is preserved as a reference.
