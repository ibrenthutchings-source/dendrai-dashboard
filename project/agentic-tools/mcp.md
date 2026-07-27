# Dendrai MCP Servers

All servers live in `project/agentic-tools/`. They can be used two ways:

- **Local / Claude Code** — run as stdio processes, registered in `settings.json`
- **Remote / claude.ai** — mounted as Streamable-HTTP endpoints by `api_server.py`, accessible over the network (Railway, Docker, etc.)

---

## Remote access via Railway (or any deployment)

`api_server.py` mounts every MCP server as a FastMCP Streamable-HTTP sub-app. When the stack is deployed (Railway, Docker Compose, etc.) each server is reachable at:

```
https://<your-host>/mcp/edgar/mcp
https://<your-host>/mcp/fred/mcp
https://<your-host>/mcp/rss/mcp
https://<your-host>/mcp/token-cost/mcp
https://<your-host>/mcp/predictive/mcp
https://<your-host>/mcp/risk-as-code/mcp
https://<your-host>/mcp/policy-as-code/mcp
https://<your-host>/mcp/controls-as-code/mcp
https://<your-host>/mcp/oracle/mcp
https://<your-host>/mcp/devops-monitoring/mcp
https://<your-host>/mcp/infrastructure-monitoring/mcp
```

A discovery endpoint lists all mounted servers and their URLs:

```
GET https://<your-host>/mcp
```

Set `PUBLIC_URL=https://your-railway-app.up.railway.app` in the Railway environment so the discovery endpoint returns absolute URLs.

### Connect to claude.ai

1. Go to **claude.ai → Settings → Integrations**
2. Add each URL above as a remote MCP server
3. Claude will have access to all tools immediately — no local process needed

### nginx proxy

The `project/nginx.conf` proxies `/mcp/` to `api_server.py` on port 8001 with `proxy_buffering off` (required for MCP's SSE transport).

---

## Local setup (Claude Code / Claude Desktop)

### Install dependencies


```bash
pip install mcp python-dotenv
# predictive analytics extras
pip install -r requirements.txt
```

### Register with Claude Code

Add to `~/.claude/settings.json` (user-wide) or `.claude/settings.json` (project-scoped). Replace the path prefix with your local clone location.

**Without telemetry** (plain servers):
```json
{
  "mcpServers": {
    "edgar":                { "command": "python", "args": ["<path>/edgar_mcp_server.py"] },
    "fred-macro":           { "command": "python", "args": ["<path>/fred_mcp_server.py"] },
    "rss-news":             { "command": "python", "args": ["<path>/rss_mcp_server.py"] },
    "predictive-analytics": { "command": "python", "args": ["<path>/predictive_analytics_mcp_server.py"] },
    "token-cost":           { "command": "python", "args": ["<path>/token_cost_mcp_server.py"] },
    "risk-as-code":         { "command": "python", "args": ["<path>/risk_as_code_mcp_server.py"] },
    "policy-as-code":       { "command": "python", "args": ["<path>/pac_mcp_server.py"] },
    "controls-as-code":     { "command": "python", "args": ["<path>/cac_mcp_server.py"] },
    "oracle-fusion":        { "command": "python", "args": ["<path>/oracle_fusion_mcp_server.py"] },
    "devops-monitoring":    { "command": "python", "args": ["<path>/devops_monitoring_mcp_server.py"] },
    "infrastructure-monitoring": { "command": "python", "args": ["<path>/infrastructure_monitoring_mcp_server.py"] },
    "opa": {
      "command": "npx",
      "args": ["-y", "@orygn/opa-mcp"],
      "env": { "OPA_MCP_ALLOWED_PATHS": "<path>" }
    }
  }
}
```

**With telemetry proxy** (recommended — enables Dendrai UBO Governance Brain, holds, and detection):
```json
{
  "mcpServers": {
    "edgar": {
      "command": "python",
      "args": ["<path>/mcp_telemetry_proxy.py", "--name", "edgar", "--", "python", "<path>/edgar_mcp_server.py"],
      "env": { "DATABASE_URL": "postgresql://..." }
    },
    "fred-macro": {
      "command": "python",
      "args": ["<path>/mcp_telemetry_proxy.py", "--name", "fred-macro", "--", "python", "<path>/fred_mcp_server.py"],
      "env": { "DATABASE_URL": "postgresql://..." }
    }
  }
}
```

Restart Claude Code after editing. Each server's tools become available immediately on next startup.

### Environment variables

Create a `.env` file in `project/agentic-tools/` (loaded automatically by servers that need it):

```env
FRED_API_KEY=your_key_here              # fred-macro, predictive-analytics
ANTHROPIC_API_KEY=sk-ant-...            # token-cost (optional — falls back to approximation)
ORACLE_FUSION_HOST=https://company.fa.us6.oraclecloud.com
ORACLE_FUSION_USERNAME=svc_dendrai
ORACLE_FUSION_PASSWORD=...
# Oracle OAuth (recommended for production)
ORACLE_FUSION_CLIENT_ID=...
ORACLE_FUSION_CLIENT_SECRET=...
```

---

## edgar

**File:** `edgar_mcp_server.py`  
**Dependency:** `edgar_tool.py` must be in the same directory.

Fetches public company data directly from SEC EDGAR. No API key required.

| Tool | Description |
|---|---|
| `edgar_company_info` | CIK, SIC code, entity type, state, fiscal year end, exchanges, address |
| `edgar_financial_metrics` | XBRL time-series for 20+ metrics (Revenue, NetIncome, EPS, Cash, etc.) — 5 years |
| `edgar_risk_factors` | Item 1A Risk Factors from 10-K filings (`max_filings` default 2) |
| `edgar_proxy_data` | DEF 14A: exec comp, board composition, say-on-pay, shareholder proposals |
| `edgar_filings_index` | Full 5-year index of 10-K / 10-Q / 8-K / DEF 14A with human-readable 8-K item labels |
| `edgar_sic_peers` | Companies sharing the target's SIC code (`max_peers` default 20) |
| `edgar_peer_financials` | Latest annual financials for SIC-matched peers (`max_peers` default 10) |

**Example:**
```
edgar_company_info("NVDA")
edgar_financial_metrics("AAPL")
edgar_risk_factors("MSFT", max_filings=3)
```

---

## fred-macro

**File:** `fred_mcp_server.py`  
**Requires:** `FRED_API_KEY` — free key at https://fred.stlouisfed.org/docs/api/api_key.html

Identifies leading macro-economic indicators (from the St. Louis Fed FRED database) most correlated with a company's quarterly financials.

| Tool | Description |
|---|---|
| `fred_macro_correlations` | Pearson correlation analysis across 30 FRED series vs. 9 company financial metrics. Tests 1–3 quarter leading lags. Saves to `fred_macro_indicators.json`. |
| `fred_list_series` | Lists all 30 FRED series in the catalog grouped by category (GDP, CPI, unemployment, VIX, yield curve, etc.) |
| `fred_load_analysis` | Loads and summarizes a previously saved `fred_macro_indicators.json` without re-fetching |

**Parameters for `fred_macro_correlations`:**

| Param | Default | Notes |
|---|---|---|
| `ticker` | — | NYSE/NASDAQ symbol |
| `min_correlation` | `0.85` | Lower to `0.70`–`0.75` for volatile companies |
| `lags` | `"1,2,3"` | Comma-separated quarters |
| `output_file` | `fred_macro_indicators.json` | Output path |
| `fred_api_key` | env var | Falls back to `FRED_API_KEY` |

---

## rss-news

**File:** `rss_mcp_server.py`  
**Requires:** Nothing — no API key needed.

Fetches industry-relevant RSS news for a company based on its EDGAR SIC code. Covers 25+ industry categories.

| Tool | Description |
|---|---|
| `rss_industry_news` | Finds the top 3 RSS feeds for the company's SIC industry and downloads articles from the past 12 months. Saves to `rss_industry.json`. |
| `rss_load_results` | Loads and summarizes a previously saved `rss_industry.json` with sample headlines |
| `rss_list_feeds` | Lists every feed in the curated catalog grouped by industry category |

Note: RSS feeds typically retain 20–100 articles. Full 12-month coverage depends on each feed's own retention.

---

## predictive-analytics

**File:** `predictive_analytics_mcp_server.py`  
**Requires:** `edgar_tool.py` in same directory. `FRED_API_KEY` optional (falls back to pre-computed benchmarks).

10 predictive analytics models built on Dendrai Intelligenza. All pull live data from EDGAR.

| Tool | Description |
|---|---|
| `predictive_financial_ratios` | Revenue growth, gross/net margins, FCF margin, DSRI, TATA, SGI, GMI, asset growth |
| `predictive_beneish_mscore` | Beneish M-Score earnings manipulation detection. Red >−1.78, Amber >−2.22, Green ≤−2.22 |
| `predictive_industry_risks` | 8 industry-specific risks scored Red/Amber/Green with velocity (−1 to +3). Auto-detects industry from SIC. |
| `predictive_scenario_analysis` | Bear / Base / Bull scenarios with revenue change %, gross margin impact (bps), and projected net income |
| `predictive_grey_swan` | 4-stage T+0→T+90 day escalation cascade from highest-velocity Amber risk |
| `predictive_macro_indicators` | Leading FRED macro indicators (live with API key, pre-computed benchmarks without) |
| `predictive_forecast` | ARIMA / Prophet / RF / Ensemble forecast with 95% CI for any XBRL metric |
| `predictive_backtest` | Walk-forward MAPE, RMSE, R², directional F1 across all 3 base models |
| `predictive_rss_signals` | Grades live RSS signals by relevance × severity across 8 domain vocabularies |
| `predictive_qoq_momentum` | 8-quarter rolling QoQ revenue momentum with IMPROVING / STABLE / DETERIORATING classification |
| `predictive_full_analysis` | All 10 models in one call. Takes 30–90 seconds due to EDGAR rate limits. |
| `predictive_list_industries` | Lists supported industry templates and the 8 risks scored within each |

**Supported industries:** Semiconductors, Automotive OEM, Software & Cloud, Financial Services, Healthcare & Pharma, Energy & Utilities, Retail & Consumer, Generic

---

## token-cost

**File:** `token_cost_mcp_server.py`  
**Requires:** `ANTHROPIC_API_KEY` optional — falls back to character-based approximation (±20–30%).

Estimates and tracks Claude API token costs.

| Tool | Description |
|---|---|
| `cost_estimate` | Estimate input + output tokens and USD cost before making an API call |
| `cost_count_tokens` | Count tokens in text (exact via API or approximate locally) |
| `cost_track` | Record actual usage from an API response; accumulates per session |
| `cost_session_summary` | Show accumulated cost and last 10 calls for a session |
| `cost_reset_session` | Clear a session's accumulated data |
| `cost_list_models` | All supported Claude models with per-MTok pricing and context windows |
| `cost_list_sessions` | List all sessions tracked in `token_costs.json` |

**Model aliases:** `opus` → claude-opus-4-8, `sonnet` → claude-sonnet-4-6, `haiku` → claude-haiku-4-5, `fable` → claude-fable-5

---

## policy-as-code

**File:** `pac_mcp_server.py`  
**Requires:** `DATABASE_URL` optional (falls back gracefully). Set `MCP_READ_ONLY=true` to disable writes.

Manages Rego policy modules for seven processes: the five original Oracle Fusion ERP processes plus DevOps Monitoring and Infrastructure Monitoring. Each process has a built-in default Rego module; saved versions are stored with immutable version history and multi-approver sign-offs. Also runs negative-control testing — proving a policy actually catches what it claims to, not just that it evaluates without error.

| Tool | Description |
|---|---|
| `pac_list_modules` | Latest module metadata for all processes; unsaved processes show built-in defaults |
| `pac_get_module` | Full Rego content + version + approvals for a process |
| `pac_save_module` | Save a new versioned module (auto-increments version when omitted) |
| `pac_module_history` | Version history, newest first (provides module_id for approve) |
| `pac_approve_module` | Add a named approver sign-off. Also runs the negative-testing gate against the exact version approved (advisory, not yet blocking) |
| `pac_get_hooks` | GitHub and/or Confluence hook configs |
| `pac_save_hook` | Save/update a GitHub (push Rego on save) or Confluence (sync narratives) hook |
| `pac_get_default` | Built-in Dendrai Rego default for any process — no DB required |
| `pac_validate_rego` | Static analysis: package, brace balance, deny rule inventory, sprintf sanity |
| `pac_diff_modules` | Unified diff of the two most recent saved module versions |
| `pac_run_negative_tests` | Schema-contract check (every referenced field/event-type actually produced by the real pipeline?) + must-fire/must-not-fire fixture corpus, via real OPA when available |
| `pac_negative_test_history` | Past negative-control test runs for a process, newest first |
| `pac_assurance_summary` | Which policy-enforced controls are proven working (real fire and/or passing test) vs. unverified |
| `pac_run_negative_sweep_now` | Run the periodic full-evaluation sweep for every process immediately; detects regressions |
| `pac_compliance_scorecard` | Executive Compliance Scorecard — SOC 2/NIST 800-53/ISO 27001/COSO ERM crosswalk (curated, never auto-generated); reports mapped vs. verified controls per criterion |

**Supported processes:** `itgc` · `order_to_cash` · `procure_to_pay` · `receive_to_ship` · `record_to_report` · `devops_monitoring` · `infrastructure_monitoring`

**Negative testing today:** only `devops_monitoring` and `infrastructure_monitoring` pass the schema-contract check and have a registered fixture corpus — the original five ERP processes reference input fields/roots (`input.journal.*`, `input.invoice.*`, etc.) no real producer feeds yet, discovered *by* this checking tool, not a defect in it.

---

## controls-as-code

**File:** `cac_mcp_server.py`  
**Requires:** `DATABASE_URL` optional. Set `MCP_READ_ONLY=true` to disable writes.

Generates Rego Controls-as-Code artifacts from a controls list or by synthesising testable harnesses directly from PAC deny rules. Supports evaluation simulation, structured export, and risk coverage mapping.

| Tool | Description |
|---|---|
| `cac_generate` | Generate `control_active[ref]` Rego from a JSON controls array; persists to DB with embedding |
| `cac_get_latest` | Most recent CaC artifact (full Rego + metadata), optionally filtered by ticker |
| `cac_list_artifacts` | Paginated metadata list of saved CaC artifacts (no Rego content) |
| `cac_from_pac` | Synthesise a test-harness CaC from PAC deny rules — one `control_active` per deny rule |
| `cac_validate` | Structural validation: package, `control_active` rules, required fields, no duplicate refs |
| `cac_evaluate_event` | Deny-rule evaluation against a sample OPA input event — uses the real `opa eval` binary when found (`OPA_BINARY` env var or `opa` on PATH), falls back to a labelled Python heuristic otherwise |
| `cac_export` | Export any artifact as `rego`, `json`, or `yaml` |
| `cac_map_to_risks` | Map controls to `risk_scores` rows → coverage matrix + uncovered risks list |

---

## opa

**Third-party server:** [`@orygn/opa-mcp`](https://github.com/OrygnsCode/opa-mcp-server) (MIT) — a full Rego/OPA authoring environment with 52 tools (`rego_eval`, `rego_fmt`, `rego_lint` via Regal, `rego_explain_decision`, OPA server policy CRUD, bundle build/sign, and more). Reference copy of its source lives at `project/agentic-tools/opa-mcp-server/` for reading — it is **not** built or run from that checkout. The standard install (`npx -y @orygn/opa-mcp`) always pulls the current published package.

Unlike this project's other MCP servers, `opa` is Node.js and runs the actual OPA binary (fetched automatically per-platform), so `rego_eval` and friends are authoritative — not the heuristic simulation `cac_evaluate_event` falls back to when OPA isn't installed.

**Requires:** Node.js ≥ 20. Optionally set `OPA_BINARY` / `REGAL_BINARY` to point at existing installs instead of the bundled ones, and `OPA_MCP_ALLOWED_PATHS` to scope which directories it may read/write Rego files in (defaults are conservative).

See its own [README](opa-mcp-server/README.md) for the full 52-tool reference, prompts, and resources (OPA builtin catalog, Rego style guide, RBAC/ABAC/K8s pattern library).

---

## oracle-fusion

**File:** `oracle_fusion_mcp_server.py`  
**Requires:** `ORACLE_FUSION_HOST`, `ORACLE_FUSION_USERNAME`, `ORACLE_FUSION_PASSWORD` (or OAuth client credentials).

Pulls control data from Oracle Fusion Cloud (Risk Management Cloud + FSCM).

| Tool | Description |
|---|---|
| `fusion_control_summary` | Aggregated control health dashboard — RAG status, 0–25 risk score, Dendrai-compatible risk signals. **Recommended first call.** |
| `fusion_control_library` | All controls from RMCS: type, frequency, owner, effectiveness, last test date |
| `fusion_control_results` | Operating effectiveness test results and evidence |
| `fusion_control_issues` | Open deficiencies and remediation plans with severity and due dates |
| `fusion_user_roles` | User-to-role assignments via SCIM 2.0 — filter by username or role |
| `fusion_sod_violations` | Segregation-of-duties policy violations with conflicting role pairs |
| `fusion_audit_events` | Transaction audit trail from FSCM modules (AP, AR, GL, FA, PRC, HCM) |

**Module codes for `fusion_audit_events`:** `FIN_AP`, `FIN_AR`, `FIN_GL`, `FIN_FA`, `PRC`, `HCM`

---

## devops-monitoring

**File:** `devops_monitoring_mcp_server.py`  
**Requires:** `DATABASE_URL`. Set `MCP_READ_ONLY=true` to disable writes.

SCM branch-protection auditing (GitHub/GitLab), SARIF/SAST evidence ingestion, the Risk Waiver & Exception Hub, pipeline provenance/attestation, and the ITSM/Jira-ServiceNow SLA Bridge — see [`../../UBO/docs/integrations.md`](../../UBO/docs/integrations.md) for how each rides the Bronze→Silver→Gold→Council pipeline.

| Tool | Description |
|---|---|
| `scm_list_repositories` | Registered GitHub/GitLab repos under audit (no tokens) |
| `scm_run_audit` | Run a branch-protection/CODEOWNERS audit now, adjudicated through the full pipeline |
| `scm_list_drift` | Drift/time-series log — controls that flipped between consecutive audits, either direction (the "2am override" pattern) |
| `evidence_list_records` | Filtered SARIF evidence records |
| `evidence_verify_record` | Recompute the HMAC signature for one record — proves it hasn't been tampered with since ingestion |
| `waiver_list` | List Risk Waivers (ACTIVE/EXPIRED/REVOKED) |
| `waiver_sweep_now` | Run the automated waiver-expiry sweep immediately |
| `attestation_list` | List pipeline provenance/attestation records (OIDC, SLSA level, Cosign, SBOM license-risk) |
| `itsm_list_tickets` | ITSM tickets tracking findings, with SLA status |
| `itsm_sla_summary` | Open/breached/at-risk-within-24h counts |
| `itsm_sweep_now` | Run the SLA breach-detection sweep immediately |

---

## infrastructure-monitoring

**File:** `infrastructure_monitoring_mcp_server.py`  
**Requires:** `DATABASE_URL`. Set `MCP_READ_ONLY=true` to disable writes.

Continuous IaaS/OS/DB configuration auditing — Postgres CIS-style hardening (SSL enforcement, password encryption, superuser sprawl, live unencrypted connections, connection logging) and Railway platform/deployment drift (unexpected public domain exposure, deployment image digest with no matching pipeline attestation). Findings ride the generic `system_telemetry` → adjudication path, so they already surface in Continuous Monitoring / Controls Monitor with no dedicated findings viewer needed.

| Tool | Description |
|---|---|
| `iaas_list_targets` | Registered Postgres/Railway audit connectors (no credentials) |
| `iaas_run_postgres_audit` | Run a Postgres CIS-style hardening audit now |
| `iaas_run_railway_audit` | Run a Railway platform/deployment drift audit now |

Railway API tokens should be a real Account/Team token (dashboard → Account Settings → Tokens), not a CLI OAuth session token.

---

## telemetry-proxy

**File:** `mcp_telemetry_proxy.py`  
**Requires:** `DATABASE_URL` (telemetry and holds are silently no-ops without it).

Wraps any FastMCP server with transparent stdio relay, real-time risk detection, and a pre-execution blocking gate. All tool calls and responses are logged to `observability.mcp_telemetry` without adding latency to the forwarding path.

The proxy is not an MCP server itself — it is a wrapper process. See the **With telemetry proxy** configuration example above.

#### Risk detection flags

| Flag | Trigger |
|---|---|
| `prompt_injection` | Tool arguments contain known injection keywords |
| `sensitive_data` | PII or credential patterns detected (SSN, card numbers, PEM keys, Bearer tokens) |
| `large_response` | Response payload exceeds 100 KB |
| `high_frequency` | Same tool called more than `PROXY_FREQ_THRESHOLD` times in `PROXY_FREQ_WINDOW_S` seconds |
| `escalation_sequence` | Session tail matches a dangerous sequence (e.g. `read_file → write_file → shell`) |

#### Pre-execution blocking gate

When a tool name matches `PROXY_BLOCKING_TOOLS`, the proxy:
1. Inserts a `PENDING` hold in `observability.tool_call_holds`
2. Polls every `PROXY_HOLD_POLL_S` seconds for an operator decision
3. On `DENIED`: sends JSON-RPC error `-32600` back to the client (tool never runs)
4. On `APPROVED` or timeout: forwards the call normally

Holds are resolved from the **Holds** tab in the Dendrai dashboard.

#### Environment variables

| Variable | Default | Description |
|---|---|---|
| `PROXY_BLOCKING_TOOLS` | `shell,execute,bash,…` | Comma-separated tool names requiring pre-execution approval |
| `PROXY_HOLD_TIMEOUT_S` | `30` | Seconds before an unresolved hold auto-expires |
| `PROXY_HOLD_POLL_S` | `1.0` | Polling interval while awaiting hold resolution |
| `PROXY_FREQ_WINDOW_S` | `60` | Rolling window for high-frequency detection |
| `PROXY_FREQ_THRESHOLD` | `10` | Max calls within window before `high_frequency` fires |
| `PROXY_WRITE_TIMEOUT_S` | `2.0` | DB write cancellation threshold |
| `PROXY_LOG_LEVEL` | `WARNING` | `DEBUG` / `INFO` / `WARNING` |

---

## ubo-governance-brain

**File:** `mcp_governance.py`  
**Requires:** `DATABASE_URL` + UBO package on Python path. Starts automatically as a background task in `api_server.py`.

Polls `observability.mcp_telemetry` every `MCP_GOV_POLL_INTERVAL_S` seconds for rows that the proxy flagged but haven't been processed yet. Runs each through the four-layer UBO medallion pipeline and persists the adjudication.

#### Pipeline stages

| Stage | Layer | Output |
|---|---|---|
| Bronze | Raw ingestion | URO (Unified Risk Object) from telemetry dict |
| Silver | Conformation + Policy-as-Code | Structured payload + policy violations |
| Gold | Risk scoring | `risk_score` (0.0–1.0), `risk_tier` (LOW/MEDIUM/HIGH/CRITICAL) |
| Council | Quant + Linguist + Graph Architect → Adjudicator | `final_verdict`, `ensemble_confidence`, `council_votes` |

**Verdict types:** `ESCALATE` · `MONITOR` · `CLEAR` · `INSUFFICIENT_DATA`

#### Additional features

| Feature | Description |
|---|---|
| Alert webhook | ESCALATE verdicts POST a Slack-compatible payload to `MCP_ALERT_WEBHOOK_URL` |
| Suppression allowlist | `(server, tool, args-hash)` triplets auto-clear without running the pipeline |
| Session timeline | `/observability/session/{id}/timeline` — chronological call + verdict view |
| Coverage report | `/observability/coverage` — per-tool flag rate; 0% = potential blind spot |
| Holds management | `/observability/holds` — list/approve/deny pre-execution blocking holds |

#### Environment variables

| Variable | Default | Description |
|---|---|---|
| `MCP_ALERT_WEBHOOK_URL` | `""` | Slack webhook URL for ESCALATE alerts (unset = silent) |
| `MCP_GOV_POLL_INTERVAL_S` | `30` | Seconds between poll cycles |
| `MCP_GOV_BATCH_SIZE` | `20` | Rows processed per cycle |
