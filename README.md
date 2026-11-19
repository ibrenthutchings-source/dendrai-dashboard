# Dendrai — the audit-grade control plane for AI agents

**Your agents are already in production. Dendrai is what lets you defend that decision.**

Dendrai sits in front of every action your AI agents take, enforces policy your own people wrote
and approved, escalates the risky calls to a named human, and writes the evidence your auditor,
board, and regulator will ask for. Agent pilots don't stall on model quality — they stall at the
sign-off meeting, when Legal, Audit, or the board asks for a defensible record nobody can produce.
Dendrai is the wedge into that meeting.

This repo is the full running system: the React/Vite dashboard, the Python governance backend
(`project/agentic-tools/`), the UBO Governance Brain adjudication pipeline (`UBO/`), and the MCP
servers that expose it to Claude Code / Claude Desktop.

The sales narrative (one-pager, 10-slide deck, sample pilot deliverable) lives on the `gtm`
branch, not yet merged here — ask if you need it linked from this README. For what to trust and
what's still open in the AI/algorithmic components, see
[`project/MODEL_CARD.md`](project/MODEL_CARD.md).

---

## What Dendrai does

1. **Intercepts.** Every agent tool call is ingested as a governed event — one endpoint, any
   framework (MCP, LangChain, OpenAI function calling, or a custom loop).
2. **Adjudicates.** Each event runs Bronze→Silver→Gold into a Council of three independent
   evaluators (quantitative, narrative, systemic) plus an Adjudicator, producing a verdict —
   **CLEAR / MONITOR / ESCALATE** — with a composite risk score and conflict flags.
3. **Enforces.** Policy-as-Code runs on the real Open Policy Agent binary, not a pattern matcher.
   A fired deny rule vetoes the ensemble outright and forces human review — a control your people
   wrote and approved outranks anything the scoring says.
4. **Escalates.** Escalated calls land in an approval inbox with preparer/reviewer separation and
   multi-approver sign-off. A named human decides, and that decision is recorded against the AI's.
5. **Proves it.** Every event, verdict, policy hit, and human decision is written to a
   tamper-evident, hash-chained evidence record, mapped to SOC 2, NIST SP 800-53, ISO 27001, and
   COSO — verifiable on demand, not reconstructed after the fact from application logs.

The LLM reviewer in this pipeline can only ever **escalate** a verdict — never talk one down —
and it's only consulted on cases the deterministic ensemble already flagged for a human. That
asymmetry is enforced in code, not a prompt instruction.

## Framework-agnostic — one endpoint, any agent

The adjudication pipeline (Bronze → Silver → Gold → Council, see `project/agentic-tools/mcp_governance.py`)
is **not MCP-only**. MCP tool calls reach it via the telemetry proxy, but any other agent
framework — LangChain, OpenAI function calling, a custom agent loop, or any non-MCP system — can
report tool calls to the same generic ingestion endpoint and get the identical Council review,
risk scoring, and HITL escalation as MCP traffic:

```bash
curl -X POST https://<host>/observability/telemetry/ingest \
  -H "Authorization: Bearer <ingest_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "server_name": "langchain-finance-agent",
    "event_type":  "tool_call",
    "action":      "modify_permissions",
    "resource":    "erp.accounts_payable.vendor_master",
    "severity":    "HIGH",
    "payload":     {"tool": "update_vendor_bank_details", "args": {"vendor_id": "V-4471"}}
  }'
```

`ingest_api_key` is issued per registered system in **Dendrai UBO™ Configuration → Monitored
Systems**, encrypted at rest, and revocable independently of every other system's key. A LangChain
callback handler or an OpenAI function-calling wrapper needs only to POST its tool-call events
here — no MCP server required. Inbound webhook listeners (GitHub, SARIF/evidence, ITSM) and 16
poll-based connector types (Oracle Fusion, SAP HANA, SailPoint, ...) feed the same pipeline through
the same generic path — see [`project/agentic-tools/README.md`](project/agentic-tools/README.md#background-loops--listeners)
for the full inventory of what's listening and how often.

## Expands from the same engine

The adjudication pipeline isn't agent-only. Point it at the rest of the estate and the evidence
chain, control mapping, and approval workflow all carry over — this is the platform the wedge
opens the door to, not a separate product:

| | |
|---|---|
| **ERP** | Oracle Fusion RMCS, SAP, NetSuite, Dynamics 365 — SoD violations, control test results, deficiencies |
| **Identity** | SailPoint — privilege escalation, orphaned accounts, certification failures |
| **DevOps** | Branch-protection drift, secret scanning, pipeline security, SARIF findings, DORA change metrics |
| **Cloud** | Postgres CIS hardening, platform drift, connector credential hygiene |
| **Third-party / Vendor risk** | Continuous SOC 2 coverage tracking, expiry re-escalation |
| **AI Governance** | Third-party AI tool assessment tracking, human-oversight gap detection |
| **Enterprise risk** | Continuous risk register → audit scope → management action plans → board report |

The **Dendrai Intelligenza** dashboard in this repo is that platform's front end: a six-stage
continuous audit loop (signal intake → risk scoring → HITL gate review → scope → management
action plans → closure/calibration) built on live SEC EDGAR/FRED/RSS data, ten deterministic
risk/forecasting models, and the same Claude-powered advisory layer used elsewhere in the
pipeline — see [`project/README.md`](project/README.md) for the full feature reference.

---

## Running the app

```bash
cd project
npm install
npm run dev        # Vite dev server at http://localhost:5173
```

```bash
cd project/agentic-tools
pip install -r requirements.txt
cp .env.example .env   # fill in API keys — see project/agentic-tools/README.md
python api_server.py   # → http://localhost:8001/docs
```

Enable **MCP mode** in the sidebar to route data through the Python backend instead of the JS
fetch layer. See [`project/agentic-tools/README.md`](project/agentic-tools/README.md) for the
complete backend reference — environment variables, every tool module, the background loops and
listeners inventory, and the standalone MCP servers for Claude Code / Claude Desktop.

### Project structure

```
project/
  src/main.jsx        — entry point; imports all component modules
  app.jsx             — root component; state, pipeline orchestration, routing
  pipeline.jsx         — six-stage pipeline UI + substep rendering
  rail.jsx            — Live Register right-hand panel (Risks · Heatmap · Loop)
  nav.jsx             — left navigation (Configuration · Execution · Governance Intelligence)
  report.jsx          — Loop Report modal + Override modal
  scenarios.jsx       — Grey Swan Scenarios panel
  ai-chat-panel.jsx   — slide-out AI chat panel (Claude / Gemini)
  agentic-tools/      — Python governance backend + UBO adjudication pipeline
  styles-modules.css  — all component CSS

UBO/                  — the Governance Brain: Bronze/Silver/Gold/Council pipeline, policy rules
mcp-server/           — standalone Node.js MCP server (EDGAR/FRED tools)
gtm/                  — sales narrative, one-pager, pilot deliverable sample
```

### Dashboard screens (left navigation)

| Nav item | Description |
|---|---|
| Setup | Configuration — ticker, industry, audit focus, signal sources, AI chat provider |
| Pipeline | Six-stage loop execution with HITL gates |
| Controls Monitor | KRI / control-effectiveness tracker |
| MAPs | Management Action Plans dashboard |
| Notifications | Live signal notifications |
| Audit Scope | Audit objectives and sprint plan |
| Risk-as-Code | YAML risk register editor with live Generate CaC button |
| Policy-as-Code | Rego policy editor — 7 processes (5 ERP + DevOps Monitoring + Infrastructure Monitoring), version history, multi-approver sign-off, negative-control testing |
| DevOps Monitoring | Branch Integrity Matrix, Pipeline Security, Secret Scanning (real `gitleaks`), Evidence Inspector (tamper-evidence hash-chain), Drift Timeline, Risk Waivers, Pipeline Attestations, ITSM Tickets & SLA — all riding the same UBO adjudication pipeline as agent governance |
| Grey Swan Scenarios | Bear / Base / Bull scenarios + Grey Swan cascade |
| Governance Intelligence | Board, exec comp, shareholder proposals, peer benchmarking |

The **Live Register rail** (right panel, Pipeline screen only) has three tabs: Risks · Heatmap ·
Loop. The **Loop Report** (header button, enabled after a run completes) is the board-ready output —
executive summary, per-stage execution detail, methodology, HITL changes, scenario outlook, and
audit trail — with an optional Claude-generated narrative on top of the deterministic numbers.

### AI Chat panel

A slide-out conversational interface ("Ask Claude" / "Ask Gemini", top-right header). Claude mode
runs a full agentic tool-use loop (EDGAR, FRED, RSS, quant analytics) and requires
`ANTHROPIC_API_KEY` + `api_server.py` running; Gemini mode is conversational streaming with the
current dashboard context injected, using a browser-stored API key from Setup.

---

## Authentication

JWT-based auth integrated into `api_server.py`. Two local accounts (`admin`, `dendrai`) are seeded
on first startup — initial passwords come from `AUTH_SEED_ADMIN_PASSWORD` /
`AUTH_SEED_USER_PASSWORD`, or a random one-time password printed to the startup logs; both force a
password change at first login.

- **Local login** — bcrypt hashing, rate-limited to 5 attempts / IP / 15 minutes
- **SSO** — Microsoft/Azure AD, Google Workspace, GitHub, Okta (PKCE OAuth 2.0, JIT provisioning) —
  a provider only appears on the login screen once its env vars are fully set
- **JWT sessions** — HS256-signed, HTTP-only + Secure + SameSite=Strict cookies, 24-hour TTL
  (`AUTH_JWT_SECRET` required for stable sessions across restarts)
- **Password policy** — 8+ chars, upper/lower/digit/special, last-3 history check

See [`project/agentic-tools/README.md`](project/agentic-tools/README.md#authentication-auth_dbpy--auth_endpointspy)
for the full environment variable list and auth endpoint reference.

## Backend (dendrai-app)

See [`dendrai-app/README.md`](dendrai-app/README.md) for the Express backend, Docker setup, and
production deployment instructions.
