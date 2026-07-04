# Dendrai Agentic Tools

Python backend that powers the Dendrai Risk Loop dashboard. Exposes financial data, regulatory signals, risk analytics, and enterprise control data through two complementary interfaces:

- **REST API** (`api_server.py`) — called by the React frontend via `http://localhost:8001`
- **MCP servers** — individual `*_mcp_server.py` files that expose the same tools directly to Claude Code and Claude Desktop

---

## Quick start

```bash
cd project/agentic-tools

# 1. Install dependencies
pip install fastapi uvicorn pydantic python-dotenv requests anthropic \
            feedparser httpx psycopg2-binary mcp pyyaml \
            "passlib[bcrypt]" PyJWT

# 2. Configure credentials
cp .env.example .env
# Edit .env with your API keys (see Environment Variables below)

# 3. Start the API server
python api_server.py
# → http://localhost:8001
# → Swagger docs at http://localhost:8001/docs
```

---

## Environment variables

Copy `.env.example` to `.env` and fill in the values you need. All are optional — features unlock as keys are set.

| Variable | Required for | Description |
|---|---|---|
| `DATABASE_URL` | Persistence | PostgreSQL connection string. Without it, the pipeline runs but nothing is saved. |
| `ANTHROPIC_API_KEY` | AI features | Enables HITL gate recommendations, narrative analysis, persona briefs, and the investigation agent. Without it, those endpoints return HTTP 503. |
| `FRED_API_KEY` | Macro correlations | Free key from [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html). |
| `ORACLE_FUSION_HOST` | Oracle Fusion | Your pod URL, e.g. `https://mycompany.fa.us6.oraclecloud.com`. |
| `ORACLE_FUSION_USERNAME` | Oracle Fusion | Service account username (Basic auth). |
| `ORACLE_FUSION_PASSWORD` | Oracle Fusion | Service account password (Basic auth). |
| `ORACLE_FUSION_CLIENT_ID` | Oracle Fusion (OAuth) | OAuth 2.0 client ID — takes priority over basic auth when set. |
| `ORACLE_FUSION_CLIENT_SECRET` | Oracle Fusion (OAuth) | OAuth 2.0 client secret. |
| `ORACLE_FUSION_API_VERSION` | Oracle Fusion | REST API version (default `11.13.18.05`). |
| `DENDRAI_CLAUDE_MODEL` | AI features | Override the Claude model (default `claude-opus-4-8`). |
| `DENDRAI_MCP_URL` | Managed agents | Hosted MCP server URL for the cloud agent deployment. |
| `AUTH_JWT_SECRET` | Authentication | JWT signing key. Auto-generates a random key each restart if not set — set explicitly for stable sessions. |
| `PUBLIC_URL` | Authentication (SSO) | Base URL of your deployment, e.g. `https://app.railway.app`. Required for OAuth redirect URIs. |
| `AZURE_CLIENT_ID` + `AZURE_CLIENT_SECRET` + `AZURE_TENANT_ID` | Microsoft SSO | All three required to enable Microsoft/Azure AD login. |
| `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` | Google SSO | Both required to enable Google Workspace login. |
| `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET` | GitHub SSO | Both required to enable GitHub login. |
| `OKTA_CLIENT_ID` + `OKTA_CLIENT_SECRET` + `OKTA_DOMAIN` | Okta SSO | All three required to enable Okta login. |
| `AUTH_SESSION_TTL_HOURS` | Authentication | JWT session lifetime (default `24`). |
| `AUTH_COOKIE_SECURE` | Authentication | Set to `false` only for HTTP-only local dev (default `true`). |
| `MCP_READ_ONLY` | PaC / CaC MCP servers | Set to `true` to block all write operations from the PAC and CaC MCP servers. |

---

## Tool modules

### EDGAR (`edgar_tool.py` + `edgar_mcp_server.py`)

Connects to the SEC EDGAR financial database — the authoritative source of public filings for US-listed companies.

**Functions:**
- `get_company_info(ticker)` — CIK, SIC, entity type, exchanges, address
- `fetch_xbrl_facts(cik)` — XBRL financial time-series (revenue, income, assets, cash)
- `extract_risk_factors(text)` — Item 1A risk factors from 10-K filings
- `extract_proxy_sections(text)` — Exec comp, board composition, shareholder proposals from DEF 14A
- `fetch_sic_peers(sic)` — peer companies sharing the same SIC industry code
- `annotate_8k(filing)` — classify and annotate material 8-K events

**REST endpoints:** `POST /edgar/financials`, `/edgar/risk-factors`, `/edgar/8k-events`, `/edgar/peers`, `/edgar/proxy`

**MCP server:** `edgar_mcp_server.py` — tools: `edgar_company_info`, `edgar_financial_metrics`, `edgar_risk_factors`, `edgar_proxy_data`, `edgar_filings_index`, `edgar_sic_peers`, `edgar_peer_financials`

---

### FRED (`fred_tool.py` + `fred_mcp_server.py`)

Identifies FRED macro leading indicators that correlate with a company's revenue.

**Functions:**
- `run_analysis(ticker, api_key, min_r)` — correlation analysis across hundreds of FRED series

**REST endpoint:** `POST /fred/correlations`

**MCP server:** `fred_mcp_server.py`

---

### RSS (`rss_tool.py` + `rss_ingest_service.py` + `rss_mcp_server.py`)

Industry news and compliance regulatory feed analysis.

**Functions:**
- `run_rss_analysis(ticker)` — discover top industry feeds for a company and download recent articles
- `ingest_feeds(feed_ids, force_refresh, ttl_minutes)` — fetch and grade the five compliance feeds registered in the dashboard (BIS Export Controls, CISA ICS, SEC EDGAR, Federal Reserve, EPA Climate)
- `get_feed_status()` — per-feed cache health

**REST endpoints:** `POST /rss/news`, `POST /rss/ingest`, `GET /rss/feeds/status`

**MCP server:** `rss_mcp_server.py`

---

### Predictive Analytics (`predictive_analytics_tool.py` + `predictive_analytics_mcp_server.py`)

Ten analytics models run as a batch for a single ticker.

**Models included:** Financial Ratio Analysis, Beneish M-Score, Risk Scoring, Scenario Analysis, Grey Swan, Revenue Forecast, Backtest, QoQ Momentum, Peer Benchmarking, Signal Aggregation

**REST endpoint:** `POST /predictive/full-analysis`

**MCP server:** `predictive_analytics_mcp_server.py`

---

### Risks-as-Code (`risks_as_code.py`)

Translates live pipeline risk signals into industry-standard YAML artifacts.

**Frameworks:** OSCAL (NIST SP 800-53 Assessment Results) and COSO ERM 2017 / ISO 31000:2018

**REST endpoints:**
- `POST /risks-as-code/generate` — generate + persist artifacts for a run
- `GET  /risks-as-code/stream/{run_id}` — SSE live stream as stages complete
- `GET  /risks-as-code/export/{run_id}/{framework}` — download YAML file
- `GET  /risks-as-code/latest/{ticker}` — most recent artifacts

---

### Oracle Fusion Controls (`oracle_fusion_tool.py` + `oracle_fusion_mcp_server.py` + `oracle_fusion_endpoints.py`)

Pulls automated control data from Oracle Fusion Cloud. Covers Oracle Risk Management Cloud (RMCS), FSCM audit history, and SCIM 2.0 access controls.

#### Prerequisites

1. **Oracle Fusion Cloud subscription** with at least one of:
   - Oracle Risk Management Cloud (for control library, test results, issues, SOD)
   - Oracle Fusion FSCM (for audit trail)
   - Oracle IDCS / Identity Domains (for user-role SCIM queries)

2. **Service account** with these minimum grants:
   - `RMCS_READ` or equivalent read role in Risk Management Cloud
   - `FND_AUDIT_ADMIN` or Audit Report read privilege in FSCM
   - SCIM read scope in Oracle Identity Domains

3. **Network access** — this server must be able to reach your Oracle Fusion pod. If Fusion is behind a VPN or IP allowlist, run the API server inside that network perimeter.

#### Authentication setup

**Option A — Basic auth** (quickest to configure):
```bash
ORACLE_FUSION_HOST=https://mycompany.fa.us6.oraclecloud.com
ORACLE_FUSION_USERNAME=svc_dendrai
ORACLE_FUSION_PASSWORD=your_password
```

**Option B — OAuth 2.0 Client Credentials** (recommended for production):
1. In Oracle IDCS / Identity Domains, create a **Confidential Application**
2. Grant it the scopes: `urn:opc:resource:consumer::all` (or scope-specific grants for RMCS + FSCM + SCIM)
3. Copy the Client ID and Secret:
```bash
ORACLE_FUSION_HOST=https://mycompany.fa.us6.oraclecloud.com
ORACLE_FUSION_CLIENT_ID=your_client_id
ORACLE_FUSION_CLIENT_SECRET=your_client_secret
```
OAuth credentials take priority over basic auth when both are set.

#### REST endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/oracle-fusion/status` | Connectivity check — shows host, auth method, API version |
| `GET` | `/oracle-fusion/summary` | Aggregated control health: RAG, score, risk signals |
| `POST` | `/oracle-fusion/control-library` | RMCS control definitions |
| `POST` | `/oracle-fusion/control-results` | Control test results / operating effectiveness evidence |
| `POST` | `/oracle-fusion/control-issues` | Open deficiencies and remediation plans |
| `POST` | `/oracle-fusion/user-roles` | User-to-role assignments (SCIM 2.0) |
| `POST` | `/oracle-fusion/sod-violations` | Segregation-of-duties policy violations |
| `POST` | `/oracle-fusion/audit-events` | FSCM transaction audit trail |

**Example — get control health summary:**
```bash
curl http://localhost:8001/oracle-fusion/summary
```

**Example — pull open SOD violations:**
```bash
curl -X POST http://localhost:8001/oracle-fusion/sod-violations \
     -H "Content-Type: application/json" \
     -d '{"status": "Open", "risk_level": "High"}'
```

**Example — audit trail for Accounts Payable last 30 days:**
```bash
curl -X POST http://localhost:8001/oracle-fusion/audit-events \
     -H "Content-Type: application/json" \
     -d '{"module": "FIN_AP", "date_from": "2026-05-24T00:00:00"}'
```

#### Module codes for audit events

| Code | Module |
|---|---|
| `FIN_AP` | Accounts Payable |
| `FIN_AR` | Accounts Receivable |
| `FIN_GL` | General Ledger |
| `FIN_FA` | Fixed Assets |
| `PRC` | Procurement |
| `HCM` | Human Capital Management |

#### MCP server — Claude integration

Add to `.claude/settings.json` in your project:
```json
{
  "mcpServers": {
    "oracle-fusion": {
      "command": "python",
      "args": ["C:/path/to/agentic-tools/oracle_fusion_mcp_server.py"],
      "env": {
        "ORACLE_FUSION_HOST":     "https://mycompany.fa.us6.oraclecloud.com",
        "ORACLE_FUSION_USERNAME": "svc_dendrai",
        "ORACLE_FUSION_PASSWORD": "..."
      }
    }
  }
}
```

Or for Claude Desktop, add the same block to `~/.claude/claude_desktop_config.json`.

**Available MCP tools:**

| Tool | Description |
|---|---|
| `fusion_control_summary` | Full health overview — start here |
| `fusion_control_library` | Control definitions with effectiveness ratings |
| `fusion_control_results` | Test evidence by date range / effectiveness |
| `fusion_control_issues` | Open deficiencies by severity |
| `fusion_user_roles` | User access listing by username or role |
| `fusion_sod_violations` | SOD violations by risk level |
| `fusion_audit_events` | Audit trail by module, date, event type, or user |

#### Risk register integration

`/oracle-fusion/summary` returns a `risk_signals[]` array in the same schema the pipeline uses for EDGAR and RSS signals. To feed Oracle Fusion controls into a loop run, pass `risk_signals` from the summary response into the `signals` field of `POST /predictive/full-analysis` or `POST /risks-as-code/generate`.

---

### Policy-as-Code (`pac_endpoints.py` + `pac_mcp_server.py`)

Manages Rego policy modules for five Oracle Fusion ERP processes (ITGC, O2C, P2P, R2S, R2R). Each process ships with a production-grade built-in Rego default; saved versions are stored immutably with version history and multi-approver sign-offs.

**REST endpoints (prefix `/api/pac`):**

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/pac/modules` | All processes — latest saved or default |
| `GET` | `/pac/modules/{process}` | Full Rego + approvals for a process |
| `PUT` | `/pac/modules/{process}` | Save a new versioned module |
| `GET` | `/pac/modules/{process}/history` | Version history (last 20) |
| `POST` | `/pac/modules/{process}/approve` | Add approver sign-off |
| `GET` | `/pac/hooks` | All external hook configs |
| `PUT` | `/pac/hooks/{hook_type}` | Save/update GitHub or Confluence hook |
| `POST` | `/pac/cac/generate` | Generate Controls-as-Code Rego from a controls list |
| `GET` | `/pac/cac/latest` | Most recent CaC artifact |
| `GET` | `/pac/defaults/{process}` | Built-in default Rego (no DB) |

**MCP server:** `pac_mcp_server.py` — 10 tools. See `mcp.md` → `policy-as-code`.

---

### Controls-as-Code (`cac_mcp_server.py`)

Generates and manages Rego Controls-as-Code artifacts. Synthesises testable control harnesses from PAC deny rules, evaluates policy against sample input events, and maps control coverage to the risk register.

**MCP server:** `cac_mcp_server.py` — 8 tools. See `mcp.md` → `controls-as-code`.

CaC artifacts are stored in the `controls_as_code_artifacts` table and indexed via vector embeddings for semantic search.

---

### Authentication (`auth_db.py` + `auth_endpoints.py`)

JWT-based auth system integrated into `api_server.py`. Provides local login with bcrypt hashing, four SSO providers via PKCE OAuth 2.0, and JIT provisioning for new SSO users.

**Default accounts (both require password change on first login):**

| Username | Password | Role |
|---|---|---|
| `admin` | `Admin@Dendrai1!` | admin |
| `dendrai` | `Dendrai@Pass1!` | user |

**Auth endpoints (prefix `/auth`):**

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/login` | Local username + password login (rate-limited) |
| `POST` | `/auth/logout` | Revoke session cookie |
| `GET` | `/auth/me` | Current user info |
| `POST` | `/auth/change-password` | Change password (history-checked, revokes all sessions) |
| `GET` | `/auth/sso/providers` | List enabled SSO providers |
| `GET` | `/auth/sso/{provider}/start` | Begin PKCE OAuth flow |
| `GET` | `/auth/sso/{provider}/callback` | Token exchange + JIT provisioning |

**Middleware:** `_DendraiAuthMiddleware` (Starlette `BaseHTTPMiddleware`) validates JWT on all routes except `/auth/`, `/health`, `/db/status`, `/docs`, `/redoc`, `/openapi.json`, `/mcp/`, `/github/`.

**Database schema:** `auth` PostgreSQL schema with 4 tables — `auth.users`, `auth.password_history`, `auth.sso_identities`, `auth.sessions`.

---

### AI endpoints (`ai_endpoints.py`)

Claude-powered analysis layers. All require `ANTHROPIC_API_KEY`; return HTTP 503 without it.

- Gate 1 / Gate 2 HITL recommendations
- Narrative risk analysis
- Persona briefs (CAE, CFO, Audit Committee)
- Agent investigation memo

---

### Token cost tracking (`token_cost_tool.py` + `token_cost_mcp_server.py`)

Tracks input/output token counts and estimated USD cost per AI call, per run.

**REST endpoints:** `GET /history/runs/{run_id}/token-cost`, `GET /history/runs/{run_id}/ai-analyses`

---

## Database persistence

Set `DATABASE_URL` to enable a PostgreSQL-backed schema covering:

- Company reference data + EDGAR metadata
- XBRL financial time-series
- FRED macro series and correlations
- Risk loop runs, risk scores, scenario analyses, forecasts
- HITL decisions (Gate 1 per-risk, Gate 2 per-objective)
- RSS signals and articles
- Risks-as-Code OSCAL + COSO ERM artifacts
- AI analysis outputs and token usage
- **Policy-as-Code modules** (`pac_policy_modules`, `pac_policy_approvals`, `pac_external_hooks`)
- **Controls-as-Code artifacts** (`controls_as_code_artifacts`)
- **Authentication** (`auth.users`, `auth.password_history`, `auth.sso_identities`, `auth.sessions`)
- **MCP observability** (`observability.mcp_telemetry`, `observability.adjudicated_events`)

Without `DATABASE_URL` the pipeline runs in stateless mode — all data is returned in the API response but nothing is persisted.

Run history endpoints require the database: `GET /history/runs/{ticker}`, `GET /history/runs/{ticker}/{run_id}`.

---

## Infrastructure endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Service health, AI availability, model name |
| `GET /db/status` | Database connectivity and configuration |
| `GET /docs` | Swagger UI (all endpoints) |