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
  ai-chat-panel.jsx   — slide-out AI chat panel (Claude / Gemini)
  styles-modules.css  — all component CSS
```

## Architecture

- **State**: All pipeline state lives in `app.jsx` (`stageState`, `output`, `gateState`). Persisted to `localStorage` as `dendrai.lastLoop` on each loop run; restored on page load.
- **Pipeline**: Six sequential stages (`s1`–`s6`) plus an optional `s7` for scenarios/personas. HITL gates after Stage 2 (Gate 1 — risk review) and Stage 3 (Gate 2 — scope review).
- **Global components**: Each component file registers itself on `window` (e.g. `window.ForecastChart`, `window.RiskFlowSankey`, `window.ScenariosPanel`) so pipeline sub-panels can poll and mount them lazily.
- **Live mode**: When enabled, fetches EDGAR companyfacts from `data.sec.gov` and FRED macro data from bundled snapshot. Disabled = mock dataset.

## AI Chat panel

A slide-out conversational interface accessible from the **"Ask Claude" / "Ask Gemini" button** in the top-right header. Click the button to open; click again (or press ✕) to close.

- **Claude mode** — full agentic tool-use loop: Claude can call EDGAR, FRED, RSS, and the quant analytics suite to answer questions with live data. Requires `ANTHROPIC_API_KEY` in `.env` and `api_server.py` running.
- **Gemini mode** — conversational streaming with the current dashboard context (entity, risk register) injected as system context. Requires a Gemini API key entered in Setup.
- **Suggestions** — the empty state shows three context-aware prompts based on the current ticker.
- **Tool trace** — while Claude fetches data you see each tool call appear live before the response streams in.

**Configuration** (Setup → AI Chat Assistant):
- Provider: Claude or Gemini
- Button label: free-form text (default "Ask Claude" / "Ask Gemini")
- Gemini API key: stored in browser `localStorage`; get one at [aistudio.google.com](https://aistudio.google.com)

## Screens (left navigation)

| Nav item | Description |
|---|---|
| Setup | Configuration — ticker, industry, audit focus, signal sources, AI chat provider |
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

## Agentic tools backend

The Python backend (`project/agentic-tools/`) exposes all data and analytics as a REST API on `http://localhost:8001` and as individual MCP servers for Claude Code / Claude Desktop.

```bash
cd project/agentic-tools
pip install fastapi uvicorn pydantic python-dotenv requests anthropic \
            feedparser httpx psycopg2-binary mcp pyyaml
# Optional: Gemini support for the AI chat panel
pip install google-generativeai
cp .env.example .env   # fill in API keys
python api_server.py   # → http://localhost:8001/docs
```

### Data sources

| Module | What it provides | Credentials needed |
|---|---|---|
| EDGAR | SEC 10-K/10-Q/8-K/DEF 14A filings, XBRL financials, peer companies | None (public) |
| FRED | Macro leading-indicator correlations | `FRED_API_KEY` (free) |
| RSS | Industry news + five compliance feeds (BIS, CISA, SEC, Fed, EPA) | None (public) |
| Predictive Analytics | Ten risk models: M-Score, forecasting, scenarios, grey swan | None |
| Risks-as-Code | OSCAL + COSO ERM YAML artifacts from live risk register | None |
| AI endpoints | HITL gate recommendations, narrative analysis, persona briefs, **AI chat** | `ANTHROPIC_API_KEY` |
| **Oracle Fusion** | **Control library, test results, deficiencies, SOD violations, audit trail** | **See below** |

### Oracle Fusion controls integration

The Oracle Fusion module (`oracle_fusion_tool.py`) pulls automated control data from Oracle Fusion Cloud and makes it available through the REST API and as MCP tools for Claude.

**What it pulls:**
- **Control library** — all active RMCS control definitions with type, frequency, and effectiveness rating
- **Control test results** — operating effectiveness evidence (pass/fail, exceptions noted)
- **Control issues** — open deficiencies with severity, root cause, and remediation plan
- **User role assignments** — SCIM 2.0 access listings for access certification reviews
- **SOD violations** — segregation-of-duties conflicts with conflicting role pairs
- **Audit trail** — FSCM transaction audit events by module (AP, AR, GL, Procurement, HCM)
- **Control health summary** — aggregated RAG status + risk signals compatible with the Dendrai risk register

**Setup (add to `project/agentic-tools/.env`):**
```bash
# Basic auth — quickest to configure
ORACLE_FUSION_HOST=https://mycompany.fa.us6.oraclecloud.com
ORACLE_FUSION_USERNAME=svc_dendrai
ORACLE_FUSION_PASSWORD=your_password

# OAuth 2.0 — recommended for production (takes priority over basic auth)
ORACLE_FUSION_CLIENT_ID=your_client_id
ORACLE_FUSION_CLIENT_SECRET=your_client_secret
```

The service account needs read access to Oracle Risk Management Cloud (RMCS), FSCM Audit, and Oracle IDCS / Identity Domains (SCIM). See `project/agentic-tools/README.md` for full Oracle Fusion setup instructions, endpoint reference, MCP server configuration, and module codes.

**Key endpoints once configured:**
```
GET  /oracle-fusion/status          — connectivity check
GET  /oracle-fusion/summary         — aggregated control health (start here)
POST /oracle-fusion/sod-violations  — SOD violations by risk level
POST /oracle-fusion/control-issues  — open deficiencies
POST /oracle-fusion/audit-events    — transaction audit trail
```

**MCP tools for Claude** — run `oracle_fusion_mcp_server.py` and add it to `.claude/settings.json`:
```json
{
  "mcpServers": {
    "oracle-fusion": {
      "command": "python",
      "args": ["C:/path/to/agentic-tools/oracle_fusion_mcp_server.py"]
    }
  }
}
```
Claude can then call `fusion_control_summary`, `fusion_sod_violations`, `fusion_audit_events`, and four other tools directly.

See `project/agentic-tools/README.md` for the complete backend reference.

## Backend (dendrai-app)

See `dendrai-app/README.md` for the Express backend, Docker setup, and production deployment instructions.
