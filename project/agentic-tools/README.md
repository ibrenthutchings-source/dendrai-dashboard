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
| `MCP_ALERT_WEBHOOK_URL` | UBO Governance Brain | Slack-compatible webhook URL. When set, ESCALATE verdicts POST a JSON alert payload. |
| `MCP_GOV_POLL_INTERVAL_S` | UBO Governance Brain | Seconds between governance poll cycles (default `30`). |
| `MCP_GOV_BATCH_SIZE` | UBO Governance Brain | Telemetry rows processed per poll cycle (default `20`). |
| `PROXY_BLOCKING_TOOLS` | Telemetry Proxy | Comma-separated tool names that trigger pre-execution holds (default: `shell,execute,bash,run_command,drop,truncate,delete_file,exec_sql`). |
| `PROXY_HOLD_TIMEOUT_S` | Telemetry Proxy | Seconds to wait for operator approval before expiring a hold (default `30`). |
| `PROXY_HOLD_POLL_S` | Telemetry Proxy | Polling interval in seconds while waiting for hold resolution (default `1.0`). |
| `PROXY_FREQ_WINDOW_S` | Telemetry Proxy | Rolling window in seconds for high-frequency detection (default `60`). |
| `PROXY_FREQ_THRESHOLD` | Telemetry Proxy | Call-count threshold within window before `high_frequency` fires (default `10`). |
| `PROXY_WRITE_TIMEOUT_S` | Telemetry Proxy | Seconds before a DB write is silently cancelled (default `2.0`). |
| `PROXY_LOG_LEVEL` | Telemetry Proxy | Log verbosity: `DEBUG`, `INFO`, `WARNING` (default `WARNING`). |

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

Identifies FRED macro leading indicators that correlate with a company's revenue, EPS, NetIncome, and EBITDA.

**Functions:**
- `run_analysis(ticker, api_key, min_r)` — correlation analysis across hundreds of FRED series, computing each series' `optimal_lag_quarters`

**REST endpoint:** `POST /fred/correlations`

**MCP server:** `fred_mcp_server.py`

**Feeds forecasting, not just display**: when a live `FRED_API_KEY` is set, `predictive_analytics_tool.py`'s `_build_fred_feature_matrix()` turns the top-5 correlated indicators (by `|pearson_r|`) for each metric into lag-shifted feature arrays — each array position holds the macro reading from that series' own `optimal_lag_quarters` before the target quarter, so it's real, already-published data even when extended into the forecast horizon. These arrays feed the Random Forest leg of the Revenue/EPS/NetIncome/EBITDA ensemble forecasts (ARIMA/Prophet stay univariate). Without a key, forecasts are unchanged from the univariate baseline. The indicators actually used for a given forecast are reported back in `forecast.fred_features_used`.

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

**Token Usage screen** (`GET /token-usage/summary?days=N`, frontend `project/token-usage.jsx`): every LLM call made through `claude_client.py` (structured completions, free-form completions, the tool-use agent loops, and the chat streaming endpoint) is attributed to the authenticated caller and recorded to `token_usage_calls` with `user_id`/`username`, in addition to the existing `label`/`model`/token/cost columns. `label` is the "by feature/source" axis shown in the UI (`"chat"`, `"gate1"`, `"narrative"`, `"pac_draft_rego"`, etc.) — there's no separate per-MCP-server token cost, since MCP tool calls themselves are free function calls; only the orchestrating Claude call that decides to invoke them costs tokens. The endpoint also returns all-time calendar rollups (by month, month-to-date, by year, year-to-date) computed from `cost_usd` as stored at recording time, i.e. the pricing in effect when each call was actually made, not recomputed against current pricing. Calls made before this feature existed have no `user_id` and show as "Unknown". Nav-permission-gated like any other non-admin screen (`auth.screen_permissions`), not hardcoded admin-only, so an admin can grant/restrict it per user from User Configuration → Screen Access.

---

---

### MCP Telemetry Proxy (`mcp_telemetry_proxy.py`)

Transparent stdio relay that wraps any FastMCP server. Captures every JSON-RPC 2.0 message, computes per-call latency, runs real-time risk detection, and writes telemetry to PostgreSQL asynchronously — without adding latency to the forwarding path.

#### How it works

```
CLIENT stdin/stdout
     │
     ▼
MCP TELEMETRY PROXY
  relay_stdin  ────────────► subprocess.stdin
  relay_stdout ◄────────────  subprocess.stdout
  (fire-and-forget DB write)
     │
     ▼
FASTMCP SERVER (subprocess)
```

#### Usage

```bash
# Wrap any FastMCP server:
python mcp_telemetry_proxy.py -- python edgar_mcp_server.py

# With a named server tag:
python mcp_telemetry_proxy.py --name edgar -- python edgar_mcp_server.py
```

In `claude_desktop_config.json` or `.claude/settings.json`:
```json
{
  "mcpServers": {
    "edgar": {
      "command": "python",
      "args": [
        "/path/to/mcp_telemetry_proxy.py",
        "--name", "edgar",
        "--",
        "python", "/path/to/edgar_mcp_server.py"
      ]
    }
  }
}
```

#### Detection rules (run on every message)

| Flag | Description |
|---|---|
| `prompt_injection` | Keyword scan in tool arguments for known injection phrases |
| `sensitive_data` | Regex match for SSNs, credit card numbers, PEM keys, Bearer tokens, API keys |
| `large_response` | Response payload >100 KB |
| `high_frequency` | Same tool called >`PROXY_FREQ_THRESHOLD` times within `PROXY_FREQ_WINDOW_S` |
| `escalation_sequence` | Tail of session history matches a known dangerous sequence (e.g. `read_file → write_file → shell`) |

#### Pre-execution blocking gate

Tools in `PROXY_BLOCKING_TOOLS` are paused before forwarding. The proxy inserts a `PENDING` hold into `observability.tool_call_holds`, then polls for an operator decision from the dashboard Holds tab. `DENIED` sends a JSON-RPC error `-32600` back to the client; `APPROVED` or `TIMEOUT` resumes forwarding normally.

Default blocking tools: `shell, execute, bash, run_command, drop, truncate, delete_file, exec_sql`

---

### UBO Governance Brain (`mcp_governance.py`)

Background service that consumes flagged telemetry rows and runs them through the full UBO medallion pipeline:

```
Bronze → Silver (PaC) → Gold (risk score) → Council (Quant + Linguist + Graph Architect → Adjudicator)
```

Adjudication results are written to `observability.adjudicated_tool_calls`. Started automatically alongside `api_server.py`.

**Verdict types:** `ESCALATE`, `MONITOR`, `CLEAR`, `INSUFFICIENT_DATA`  
**Risk tiers:** `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`

#### Additional capabilities

| Capability | Description |
|---|---|
| Alert webhook | ESCALATE verdicts POST a Slack-compatible payload to `MCP_ALERT_WEBHOOK_URL` |
| Suppression allowlist | Known-good `(server, tool, args-hash)` triplets auto-clear without entering the pipeline |
| Session timeline | Chronological call view joined to adjudication verdicts for a given session UUID |
| Coverage report | Per-tool flag rate; 0% flag-rate = potential governance blind spot |
| Pre-execution holds | Approve or deny blocking-gate holds from the dashboard |

#### Observability REST endpoints (prefix `/observability`)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/telemetry/summary` | P50/P95/P99 latency + error rate per tool |
| `GET` | `/telemetry/flagged` | Recent tool calls that fired ≥ 1 governance flag |
| `GET` | `/telemetry/adjudicated` | UBO adjudication results (filter by `?tier=HIGH`) |
| `GET` | `/telemetry/human-review` | Adjudications requiring human review, sorted by risk score |
| `POST` | `/telemetry/process` | Manually trigger one governance batch |
| `GET` | `/telemetry/raw` | Raw mcp_telemetry rows for the live-stream feed |
| `PUT` | `/telemetry/adjudicated/{id}/review` | Mark as human-reviewed; optionally override verdict |
| `GET` | `/holds` | Pending pre-execution holds awaiting operator decision |
| `PUT` | `/holds/{id}/resolve` | Approve or deny a hold — body: `{status, resolved_by}` |
| `GET` | `/session/{session_id}/timeline` | All calls for a session in chronological order |
| `GET` | `/coverage` | Per-tool flag rate table; annotates blind spots |
| `GET` | `/suppressions` | Active + inactive suppression rules |
| `POST` | `/suppressions` | Add a suppression rule — body: `{server_name, target_tool, tool_args_hash, reason}` |
| `DELETE` | `/suppressions/{id}` | Soft-delete (deactivate) a rule |

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
- **MCP observability** — `observability` schema:
  - `mcp_telemetry` — every JSON-RPC call logged by the proxy
  - `adjudicated_tool_calls` — UBO Governance Brain verdicts
  - `tool_call_holds` — pre-execution holds (PENDING / APPROVED / DENIED / EXPIRED)
  - `tool_call_suppressions` — suppression allowlist rules
  - `tool_latency_summary` — materialized view: P50/P95/P99 per tool
  - `flagged_calls` — view: telemetry rows with at least one risk flag

Without `DATABASE_URL` the pipeline runs in stateless mode — all data is returned in the API response but nothing is persisted.

Run history endpoints require the database: `GET /history/runs/{ticker}`, `GET /history/runs/{ticker}/{run_id}`.

---

## Infrastructure endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Service health, AI availability, model name |
| `GET /db/status` | Database connectivity and configuration |
| `GET /docs` | Swagger UI (all endpoints) |