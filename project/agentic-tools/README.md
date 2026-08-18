# Dendrai Agentic Tools

Python backend that powers the Dendrai Intelligenza dashboard. Exposes financial data, regulatory signals, risk analytics, and enterprise control data through two complementary interfaces:

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
| `AUDIT_SIGNING_KEY` | Platform audit trail | HMAC-SHA256 key signing `observability.audit_log` rows (identity/access changes + MCP tool calls) so `GET /auth/admin/audit-log/verify` can prove they haven't been tampered with. Optional — falls back to a random per-process key if unset (chain-hash tamper-evidence still catches row deletion/reordering either way; a missing explicit key only means HMAC signatures won't re-verify across a restart). Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `PUBLIC_URL` | Authentication (SSO) | Base URL of your deployment, e.g. `https://app.railway.app`. Required for OAuth redirect URIs. |
| `AZURE_CLIENT_ID` + `AZURE_CLIENT_SECRET` + `AZURE_TENANT_ID` | Microsoft SSO | All three required to enable Microsoft/Azure AD login. |
| `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` | Google SSO | Both required to enable Google Workspace login. |
| `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET` | GitHub SSO | Both required to enable GitHub login. |
| `OKTA_CLIENT_ID` + `OKTA_CLIENT_SECRET` + `OKTA_DOMAIN` | Okta SSO | All three required to enable Okta login. |
| `AUTH_SESSION_TTL_HOURS` | Authentication | JWT session lifetime (default `24`). |
| `AUTH_COOKIE_SECURE` | Authentication | Set to `false` only for HTTP-only local dev (default `true`). |
| `MCP_READ_ONLY` | PaC / CaC / DevOps Monitoring / Infrastructure Monitoring MCP servers | Set to `true` to block all write operations from these MCP servers. |
| `OPA_BINARY` | Policy-as-Code (authoritative evaluation) | Path to a real OPA binary. Falls back to `opa` on PATH, then to a labelled Python heuristic simulation if neither is found. The Docker image (`project/Dockerfile`) always installs a real OPA binary — only local dev without OPA on PATH runs the heuristic. |
| `GITHUB_WEBHOOK_SECRET` | GitHub webhook / DevOps Monitoring | HMAC-SHA256 secret for verifying `POST /github/webhook` deliveries. Skipped (with a warning) if unset — always set it in production. |
| `GITHUB_WRITE_TOKEN` | Closed-Loop Remediation | Personal access token with `repo` (push) scope. Lets `github_write_tool.py` open a real GitHub issue (or PR, for a future connector-specific fixer) once a proposed remediation is manager-approved. Without it, `POST /remediation/propose/{event_id}` still drafts and routes the proposal for approval, but the eventual write fails and surfaces as a retryable error in the Approval Inbox. |
| `GITHUB_REMEDIATION_REPO` | Closed-Loop Remediation | Default `owner/repo` target for remediation issues/PRs, e.g. `acme-corp/infra`. Can be overridden per-proposal. |
| `CONNECTOR_ENCRYPTION_KEY` | Poll-based connectors (Oracle Fusion, SAP HANA, SailPoint, Dynamics 365, NetSuite, GitHub/GitLab SCM, Jira/ServiceNow ITSM, Postgres CIS, Railway IaaS); Monitored Systems ingest API keys; encrypted `payroll_detail`/`treasury_detail` telemetry sub-payloads; `exception_control_events.raw_payload` | Fernet key. Encrypts connector credentials at rest (`observability.poll_connectors.credentials_enc`), each Monitored System's ingest API key (`observability.monitored_systems.ingest_api_key_enc` — see `POST /observability/systems`), sensitive telemetry detail sub-dicts, and (see "Journal Entry Testing" below) `exception_control_events.raw_payload`. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Connector CRUD and new-system registration return HTTP 503/`None` without it — this was previously a plaintext UUID column for ingest keys; see `POST /observability/systems/{id}/rotate-key` to migrate a pre-existing system off the legacy column. Without it, `raw_payload` is stored as plaintext (logged as a warning), not blocked — same graceful-degradation choice as the other sub-payload encryption above. |
| `EXCEPTION_EVENT_RETENTION_DAYS` | PII retention (`pii_retention_sweep.py`) | Days `exception_control_events` rows (Exception Management / JE Testing findings — includes `raw_payload`/`actor` pulled from source-system connectors) are kept before the daily sweep purges them (cascading to `exception_model_inferences`/`exception_auditor_triage`). Default `400`. |
| `EVIDENCE_SIGNING_KEY` | DevOps Monitoring (SARIF evidence) | HMAC-SHA256 key signing `observability.evidence_records` rows so `/evidence/records/{id}/verify` can prove they haven't been tampered with. **Required** — `/evidence/webhook` and `/evidence/records/{id}/verify` return HTTP 503 if unset, rather than signing with an empty key (an empty-key HMAC needs no secret to recompute, which would make every record forgeable). Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `MCP_ALERT_WEBHOOK_URL` | Dendrai UBO Governance Brain | Slack-compatible webhook URL. When set, ESCALATE verdicts POST a JSON alert payload. |
| `MCP_GOV_POLL_INTERVAL_S` | Dendrai UBO Governance Brain | Seconds between governance poll cycles (default `30`). |
| `MCP_GOV_BATCH_SIZE` | Dendrai UBO Governance Brain | Telemetry rows processed per poll cycle (default `20`). |
| `PROXY_BLOCKING_TOOLS` | Telemetry Proxy | Comma-separated tool names that trigger pre-execution holds (default: `shell,execute,bash,run_command,drop,truncate,delete_file,exec_sql`). |
| `PROXY_HOLD_TIMEOUT_S` | Telemetry Proxy | Seconds to wait for operator approval before expiring a hold (default `30`). |
| `PROXY_HOLD_POLL_S` | Telemetry Proxy | Polling interval in seconds while waiting for hold resolution (default `1.0`). |
| `PROXY_FREQ_WINDOW_S` | Telemetry Proxy | Rolling window in seconds for high-frequency detection (default `60`). |
| `PROXY_FREQ_THRESHOLD` | Telemetry Proxy | Call-count threshold within window before `high_frequency` fires (default `10`). |
| `PROXY_WRITE_TIMEOUT_S` | Telemetry Proxy | Seconds before a DB write is silently cancelled (default `2.0`). |
| `PROXY_LOG_LEVEL` | Telemetry Proxy | Log verbosity: `DEBUG`, `INFO`, `WARNING` (default `WARNING`). |

---

## Background loops & listeners

Every mechanism below runs as an `asyncio` task started in `api_server.py`'s lifespan (the pollers/sweeps) or as a FastAPI route (the webhook listeners) — none require a separate worker process or cron. All are best-effort: an individual failure (one connector, one row, one tick) is caught, logged, and never takes down the loop or the API server. This table is the map; each module's own section below has the detail. It is not agent/AI-specific — most of it governs ERP, identity, DevOps, and infrastructure telemetry with no AI agent involved at all; the Dendrai UBO Governance Brain (MCP telemetry) is one row among many, not the whole picture.

**Inbound listeners (push — an external system calls us):**

| Listener | Endpoint | Auth | Catches |
|---|---|---|---|
| GitHub Webhook Listener | `POST /github/webhook` | HMAC-SHA256 (`GITHUB_WEBHOOK_SECRET`) | Real-time GitHub events (secret scanning, branch protection, pushes, Dependabot, pipeline security) → full Bronze→Silver→Gold→Council adjudication |
| Evidence/SARIF Webhook | `POST /evidence/webhook` | Bearer `ingest_api_key` | SARIF findings from any CI/SAST tool; HIGH/CRITICAL findings additionally escalate through adjudication |
| ITSM Webhook | `POST /itsm/webhook` | Bearer `ingest_api_key` | Real-time ticket status push from a Jira Automation rule / ServiceNow Business Rule |
| Generic Telemetry Ingest | `POST /observability/telemetry/ingest` | Bearer `ingest_api_key` | Any other registered system (or non-MCP AI agent — LangChain, OpenAI function calling, a custom loop) — see "Monitored Systems" below and the top-level README's "Governing non-MCP AI agents" |

**Outbound pollers (pull — we call an external system) and periodic sweeps:**

| Loop | Cadence | Catches |
|---|---|---|
| `mcp_governance.start_polling()` | `MCP_GOV_POLL_INTERVAL_S` (default 30s) | Flagged `mcp_telemetry`/`system_telemetry` rows → full adjudication pipeline |
| `connector_poller.start_polling()` | `CONNECTOR_POLLER_TICK_S` tick (default 60s), per-connector `poll_interval_s` (default 1800s) | All 16 pull-model connector types (Oracle Fusion, SAP HANA, SailPoint, Dynamics 365, NetSuite, GitHub/GitLab/Bitbucket SCM, Jira/ServiceNow ITSM, Postgres CIS, Railway IaaS, AWS IaaS, OT heartbeat, denied-party screening, Oracle HCM) |
| `risk_waiver_sweep.py` | Hourly | Expired risk waivers — re-opens the underlying SAST finding as failing |
| `itsm_sla_sweep.py` | Hourly | ITSM tickets that blew their remediation SLA |
| `pac_negative_sweep.py` | Hourly | Regressions in Policy-as-Code negative-test coverage (a process that passed last sweep and fails this one) |
| `connector_hygiene_sweep.py` | Daily | Poll-connector credentials overdue for rotation (default 90 days) — the one check with no external system, Intelligenza checking itself |
| `vendor_risk_sweep.py` | Daily | Vendor SOC 2 reports past their expiry date |
| `ai_governance_sweep.py` | Daily | AI system assessments past their expiry date |
| `identity_graph_sync.py` | Hourly | Full-refresh pull of user↔role assignments and open SoD violations from every active Oracle Fusion connector — feeds The Graph Architect's blast-radius/SPoF checks with real `role_count`/`entitlements` data (previously always zero/empty for every production event) |
| `je_testing_sweep.py` | `JE_TESTING_SWEEP_TICK_S` (default 1800s/30min), `JE_TESTING_LOOKBACK_DAYS` lookback (default 7d) | Pulls journal entries from every active Oracle Fusion/NetSuite/SAP HANA/Dynamics 365/synthetic-transaction connector and scores them via `je_testing_tool.py`'s deterministic anomaly rules |
| `pii_retention_sweep.py` | Daily | Purges `exception_control_events` rows (cascading to `exception_model_inferences`/`exception_auditor_triage`) past `EXCEPTION_EVENT_RETENTION_DAYS` (default 400d) — every purge run is itself recorded to the audit trail |
| `model_health_drift_watch()` (`api_server.py`) | `MODEL_HEALTH_CHECK_INTERVAL_S` (default 6h) | PSI drift on financial ratios, FRED macro regime, and AI-suggestion acceptance rate — opens a tracked incident, deduplicated against any already-open one for that metric |

**Outbound reporting (the "report" half — turns a caught signal into a notification):**

`mcp_governance._post_webhook_alert()` / `_dispatch_alert()` — a Slack-compatible POST to `MCP_ALERT_WEBHOOK_URL`, fired on every ESCALATE verdict and every newly-opened Model Health drift incident. Silently a no-op if the URL isn't configured; a delivery failure is logged and swallowed, never breaks the caller (adjudication, or the drift watcher).

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

**Feeds forecasting, not just display**: when a live `FRED_API_KEY` is set, `predictive_analytics_tool.py`'s `_build_fred_feature_matrix()` turns the top-5 correlated indicators (by `|pearson_r|`) for each metric into lag-shifted feature arrays — each array position holds the macro reading from that series' own `optimal_lag_quarters` before the target quarter, so it's real, already-published data even when extended into the forecast horizon. These arrays feed the Random Forest leg of every forecast on the Assess Enterprise Risk screen (ARIMA/Prophet stay univariate): Revenue, EPS, NetIncome, EBITDA use their own correlation entries directly; Gross Margin and Operating Margin are derived ratios with no correlation entry of their own, so they borrow the correlated indicators/lags from the raw metric that drives them (GrossProfit and OperatingIncome respectively), applied on the margin series' own quarter positions. Without a key, forecasts are unchanged from the univariate baseline. The indicators actually used for a given forecast are reported back in `forecast.fred_features_used` / `op_margin_forecast.fred_features_used` / `forecast.margin_forecast.fred_features_used`. FCF has no ensemble forecast at all yet (client-side linear extrapolation only) — not extended here.

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

### Regulatory Change Management (`regulatory_change_tool.py` + `regulatory_change_endpoints.py`)

Horizon scanning: extends `rss_ingest_service.py`'s feed fetching (which scores individual articles as they appear) with a second lens — does a feed's current content represent a MATERIAL CHANGE from what this system last saw, not just "is this a new article." Targets four horizon-scanning feeds (`eu_ai_act`, `dora`, `nis2`, `state_privacy`, distinct from the pre-existing company-gated BIS/CISA/SEC/Fed/EPA feeds `rss_tool.py` already scores per-article).

**How it works:** `POST /regulatory-change/scan` fetches each target feed's current entries (title + summary, most recent 20), hashes the text, and compares it against the last stored snapshot (`regulatory_change_versions`, sha256-deduped the same way `pac_policy_documents` dedups uploaded policy text). An unchanged hash is a no-op; a changed hash below a 2%-of-content materiality threshold (`regulatory_change_tool.is_material_change`, via `difflib`) is stored (so the *next* scan diffs against current text) but doesn't reach the review queue — boilerplate churn (nav tweaks, "last updated" footers) shouldn't. A material change gets a unified diff (`diff_summary`, capped 20K chars) and an LLM-drafted proposal (`claude_client`, falls back to a plain templated proposal if the call fails): a summary, a control reference (existing `ref` or `NEW`), and a proposed control-description edit.

**HITL review:** nothing reaches `controls_library` without an explicit human decision — same guardrail `pac_policy_docs.py` enforces for Rego modules. `POST /regulatory-change/proposals/{id}/decision` (`approved`/`rejected`) is gated on the `regchange` screen's edit permission; approval calls `db.upsert_control` with the proposed edit, rejection just records the decision.

**REST endpoints (prefix `/regulatory-change`):**

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/scan` | Fetch target feeds, diff against last version, draft proposals for material changes |
| `GET` | `/versions` | Recent fetched snapshots (`?feed_id=`) |
| `GET` | `/proposals` | Review queue (`?status=`) |
| `GET` | `/proposals/{id}` | One proposal, full diff included |
| `POST` | `/proposals/{id}/decision` | `approved` \| `rejected` |

**Frontend:** `project/regulatory-change.jsx`, nav entry `regchange` ("Regulatory Change Management").

---

### Journal Entry Testing (`je_testing_tool.py` + `je_testing_sweep.py` + `je_testing_endpoints.py`)

Classic JE-testing anomaly rules run against real GL data — the gap this closes: `pac_endpoints.py`'s `record_to_report` Rego package already encodes real audit JE-testing logic in prose (manual JEs over $10K need approval, preparer and approver can't be the same person, weekend postings need an authorization code), but nothing ever constructed the `input.journal.*` payload those rules match on, so they've never fired against anything real. This module makes that vocabulary real, deterministically, without routing every JE through the OPA/Rego evaluator.

**Rules** (`je_testing_tool.run_je_tests`), per-entry:
- `JE-ROUND-DOLLAR` (MEDIUM) — exact multiple of $1,000
- `JE-WEEKEND-POSTING` (HIGH) / `JE-AFTER-HOURS` (MEDIUM) — posted outside 06:00–20:00 UTC or on a weekend
- `JE-SOD-PREPARER-APPROVER` (CRITICAL) — preparer and approver are the same person
- `JE-THRESHOLD-UNAPPROVED` (HIGH) — over $10,000 with no approver
- `JE-TOPSIDE-UNAPPROVED` (CRITICAL) — over $500,000 with no approver (a proxy for a top-side/CFO-approval flag; no connector exposes that as an explicit field)

...and population-level, gated on a minimum 20-entry population so "rare" is meaningful:
- `JE-RARE-ACCOUNT` (MEDIUM) — GL account seen ≤1 time across the tested population
- `JE-UNUSUAL-DESCRIPTION` (LOW) — description unique across the population, amount ≥ $5,000
- `JE-VELOCITY-SPIKE` (HIGH) — a preparer's daily posting count >2σ above their own baseline (needs ≥3 distinct posting days to establish one)

**Sweep** (`je_testing_sweep.py`, tick `JE_TESTING_SWEEP_TICK_S` default 1800s/30min, lookback `JE_TESTING_LOOKBACK_DAYS` default 7d): pulls journal entries from every active `oracle_fusion`/`netsuite`/`sap_hana`/`dynamics365`/`synthetic_transaction` connector via each tool's `get_journal_entries()`, runs the rule set, and persists findings via `db.insert_exception_event` — reusing Exception Management's `exception_control_events`/`exception_model_inferences` schema (already generic enough: `control_id`, `system_source`, `process`, `raw_payload`). Unlike Exception Management's own ingestion path (`exceptions_endpoints.py`, gated to `deploy_env.IS_DEVELOPMENT` — a demo of ML-uncertainty scoring), JE Testing is a real, always-on control in every environment; findings are deterministic (`model_version="je-rules-v1"`), not from a trained model, so `uncertainty_score` is always 0. CRITICAL/HIGH findings set `requires_human_review=True`; MEDIUM/LOW don't.

**REST endpoints (prefix `/je-testing`, screen permission `continuousmonitoring`):**

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/summary` | Headline tiles — entries tested, findings by rule, top preparers |
| `GET` | `/findings` | Findings list (`?rule_id=&system_source=&preparer=&only_pending=&limit=&offset=`) |
| `POST` | `/findings/{event_id}/disposition` | Auditor resolution — same 4-label vocabulary as Exception Management (`TRUE_CONTROL_FAILURE`, `BENIGN_OPERATIONAL_NOISE`, `APPROVED_CARVE_OUT`, `DATA_PIPELINE_ERROR`) |

**Frontend:** a "JE Testing" tab on the Continuous Monitoring screen (`continuous-monitoring-viz.jsx`) — a findings table (rule filter, pending-only toggle), not a chart of adjudicated events like the other tabs: each row is a rule that actually fired against a real posting.

**PII at rest:** `exception_control_events.raw_payload` (which can carry employee names/emails and transaction detail pulled straight from a source-system event) is Fernet-encrypted end-to-end (`db._encrypt_raw_payload`/`_decrypt_raw_payload`, `CONNECTOR_ENCRYPTION_KEY`) — the whole payload wrapped rather than named sub-keys, since its shape varies by connector. `actor` is deliberately left as plaintext: it's an equality-filtered column (JE Testing's "filter by preparer", Exception Management's triage view), and Fernet encryption is randomized, so an encrypted `actor` could never be filtered on without a separate blind-index column. Rows past `EXCEPTION_EVENT_RETENTION_DAYS` (default 400d) are purged daily by `pii_retention_sweep.py` — see "Background loops & listeners" above.

---

### Closed-Loop Remediation (`github_write_tool.py` + `remediation_endpoints.py`)

The first write-capable connector primitive in this codebase — every `connector_poller.py` adapter is strictly `pull_events()`/`test_connection()`. Deliberately scoped to the lowest-blast-radius external system available: opening a GitHub issue is fully reversible and touches no access grant.

**Flow:** `POST /remediation/propose/{event_id}` (screen permission `approvals`, edit) drafts a GitHub issue title/body for one `exception_control_events` finding (LLM-drafted via `claude_client`, falls back to a plain templated issue if the call fails) and submits it through the existing 2-stage preparer→manager approval workflow (`approvals_endpoints.py`, `gate_type='remediation_github'`). A submission is always `disposition='adjusted'` — there's no "accept the finding as computed" path, so a remediation always requires a human review step before the write can fire. Once a manager approves (or, with no manager configured, immediately — same auto-approve reasoning `devops_scm_exception` already uses), `approvals_endpoints._execute_remediation` fires the real write via `github_write_tool.create_issue`. On success, the source finding is auto-resolved (`TRUE_CONTROL_FAILURE`, noting the issue URL); on failure the error is persisted on the task (never swallowed) and the source finding stays open, surfaced in the Approval Inbox with a retry action.

**`github_write_tool.py`:** `create_issue(title, body, repo, labels)` — the flow's only wired action. `create_pull_request(title, body, files, repo, base_branch)` is a complete, independently-tested primitive (full Git Data API flow: blob → tree → commit → ref → PR) for a future connector-specific fixer with real file changes ready — not called by today's remediation-proposal flow, since there's no principled way to synthesize a code diff from an arbitrary business-exception finding. `test_connection()` verifies the token can both read and push before first use. Configured via `GITHUB_WRITE_TOKEN` / `GITHUB_REMEDIATION_REPO` (env vars, not the `poll_connectors` table — this is a write-only target with no events to poll).

**REST endpoints:**

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/remediation/propose/{event_id}` | Draft a GitHub issue for one finding, submit for manager approval |
| `GET` | `/approvals/remediations` | Recent closed-loop remediation tasks, any status |
| `POST` | `/approvals/remediations/{id}/retry` | Re-fire a failed remediation's GitHub write with the same approved content |

**Frontend:** `project/exceptions.jsx` — a "Propose remediation" button on a finding, showing status ("awaiting manager approval" / task status) once proposed. `project/approval-inbox.jsx` renders `remediation_github` tasks with a distinct label ("Closed-Loop Remediation · GitHub Issue") and exposes the retry action for a failed write.

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

Manages Rego policy modules for seven processes: the five original Oracle Fusion ERP processes (ITGC, O2C, P2P, R2S, R2R) plus `devops_monitoring` and `infrastructure_monitoring`. Each process ships with a production-grade built-in Rego default; saved versions are stored immutably with version history and multi-approver sign-offs.

**REST endpoints (prefix `/api/pac`):**

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/pac/modules` | All processes — latest saved or default |
| `GET` | `/pac/modules/{process}` | Full Rego + approvals for a process |
| `PUT` | `/pac/modules/{process}` | Save a new versioned module |
| `GET` | `/pac/modules/{process}/history` | Version history (last 20) |
| `POST` | `/pac/modules/{process}/approve` | Add approver sign-off; also runs the negative-testing gate against the exact version approved (advisory) |
| `GET` | `/pac/hooks` | All external hook configs |
| `PUT` | `/pac/hooks/{hook_type}` | Save/update GitHub or Confluence hook |
| `POST` | `/pac/cac/generate` | Generate Controls-as-Code Rego from a controls list |
| `GET` | `/pac/cac/latest` | Most recent CaC artifact |
| `GET` | `/pac/defaults/{process}` | Built-in default Rego (no DB) |
| `POST` | `/pac/negative-tests/run/{process}` | Schema-contract check + must-fire/must-not-fire fixture corpus |
| `GET` | `/pac/negative-tests/history/{process}` | Past negative-control test runs (audit evidence) |
| `GET` | `/pac/assurance` | Which policy-enforced controls are proven working vs. unverified |
| `GET` | `/pac/compliance-scorecard` | Executive Compliance Scorecard — SOC 2/NIST/ISO/COSO framework coverage, mapped vs. verified |
| `GET` | `/pac/approval-drift` | Compares what's actually evaluating (latest saved module) against the latest module that ever received a real approval sign-off, per process |

**MCP server:** `pac_mcp_server.py` — 16 tools. See `mcp.md` → `policy-as-code`.

#### Negative testing (`pac_contracts.py` + `pac_negative_tests.py` + `pac_assurance.py` + `pac_negative_sweep.py`)

A Rego rule that references a field or event-type literal the real adjudication pipeline never produces (`mcp_governance._evaluate_pac_policy` — see "Dendrai UBO Governance Brain" below) evaluates without error and silently never fires — indistinguishable from a policy that found nothing wrong. Two independent checks close that gap, plus assurance metadata that tracks which controls are actually proven working:

- **Schema-contract check** (`pac_contracts.check_module_contract`) — static analysis: every `input.event.<field>` reference must be in the process's declared `PROCESS_CONTRACTS["allowed_fields"]`, every `input.event.type == "..."` literal must be a real `EventType` that actually routes to that process, and any top-level `input.<root>.*` reference other than `event` is flagged (the pipeline only ever constructs `{"event": {...}}`). This is how the original five ERP process modules were found to be dead-by-construction — real-sounding SAP/ERP policy language with no producer ever feeding `input.journal.*`/`input.invoice.*`/etc.
- **Must-fire/must-not-fire corpus** (`pac_negative_tests.run_corpus`) — curated fixtures per process, run through the real `evaluate_policy_event` (authoritative OPA when available, labelled heuristic fallback otherwise). Only `devops_monitoring` and `infrastructure_monitoring` have a registered corpus today.
- **Assurance metadata + periodic sweep** (`pac_assurance.evaluate_and_record`, `pac_negative_sweep.py`'s hourly background task) — persists every test run to `observability.pac_test_runs` and updates `controls_catalog.last_fired_at`/`last_verified_at`/`last_test_passed`, so `db.list_unverified_controls()` answers "which controls does nothing currently prove are working," not just "how many controls exist." The sweep also detects regressions — a process that passed last sweep and fails this one, even if its Rego text didn't change (a Silver-layer conformer edit can break a contract just as easily as editing the policy itself).

#### Executive Compliance Scorecard (`framework_mappings.py`)

Curated (never auto-generated — same guardrail as the retired Framework Sync pattern, commit `2b98f45`) SOC 2 / NIST SP 800-53 / ISO 27001 / COSO ERM crosswalk for policy-enforced controls. `GET /pac/compliance-scorecard?framework=soc2|nist_800_53|iso_27001|coso` reports, per criterion, how many controls are *mapped* to it vs. how many are actually *verified* (per the assurance metadata above) — deliberately two separate numbers, since a criterion can be 100% mapped and 0% verified. Edit `framework_mappings.FRAMEWORK_MAPPINGS` directly to correct or extend a mapping; `test_compliance_scorecard.py` asserts every `DEVOPS-*`/`INFRA-*` control_id the Rego defaults actually define has a mapping, so new rules can't silently go unmapped.

#### Approval/Evaluation Drift Detection (`pac_approval_drift.py`)

A real governance gap found while building the negative-testing sweep: `db.get_latest_pac_module(process)` (what `_evaluate_pac_policy` actually evaluates) returns the most recently **saved** module — `pac_policy_modules` has no status/approved column, so saving a draft via `PUT /pac/modules/{process}` makes it live in production before any approval; the negative-testing gate on `POST /modules/{process}/approve` is advisory and runs strictly afterward. `check_process_drift(process)` compares the content hash of what's live against the content hash of the latest module version that ever received a real approval — a mismatch means an unapproved or since-edited module is currently adjudicating real events. Run via `GET /pac/approval-drift`, the `pac_check_approval_drift` MCP tool, or automatically as part of the hourly negative-testing sweep (logged as a warning); shown as a red banner on the Policy-as-Code screen when any process has drifted.

---

### Controls-as-Code (`cac_mcp_server.py`)

Generates and manages Rego Controls-as-Code artifacts. Synthesises testable control harnesses from PAC deny rules, evaluates policy against sample input events, and maps control coverage to the risk register.

**MCP server:** `cac_mcp_server.py` — 8 tools. See `mcp.md` → `controls-as-code`.

CaC artifacts are stored in the `controls_as_code_artifacts` table and indexed via vector embeddings for semantic search.

---

### DevOps Monitoring (`scm_connectors.py` + `scm_audit_endpoints.py` + `evidence_endpoints.py` + `devops_monitoring_mcp_server.py`)

SCM branch-protection auditing (GitHub/GitLab), GitHub Actions pipeline-as-code security auditing, real `gitleaks` secret scanning, SARIF/SAST evidence ingestion (with a tamper-evidence hash chain), drift detection, the Risk Waiver & Exception Hub, pipeline provenance/attestation, and DORA-style change-management metrics — all riding the same Bronze→Silver→Gold→Council pipeline every other source uses, not a parallel system. See [`../../UBO/docs/integrations.md`](../../UBO/docs/integrations.md) for the full architecture.

**Repos are registered as poll connectors** on the Dendrai UBO Configuration screen (`connector_type` `github_scm`/`gitlab_scm`), the same way Oracle Fusion/SAP HANA/etc. are — no bespoke registration form. Pipeline-security auditing and secret scanning both reuse the same registered repo+token rather than requiring a second registration.

**REST endpoints (prefix `/api/scm-audit`):** `POST/GET/DELETE /repositories`, `POST /repositories/{id}/run`, `POST /repositories/{id}/run-pipeline-security`, `POST /repositories/{id}/run-secret-scan`, `POST /run-all`, `GET /results`, `GET /results/history`, `GET /pipeline-security/results`, `GET /secret-scan/results`, `GET /drift`, `GET /waivers`, `POST /waivers/{id}/revoke`

**REST endpoints (prefix `/api/evidence`):** `POST /webhook` (SARIF ingestion, Bearer-key auth), `GET /records`, `GET /records/{id}/verify`, `GET /chain/verify` (tamper-evidence hash-chain verification), `GET /dora-metrics`, `POST /attestation`, `GET /attestations`, `GET /attestations/{id}`

**Any SARIF-producing scanner works out of the box** — `source` is a free-text field, no per-tool code required. Verified against real Trivy output (container/dependency CVE scanning): correctly extracts CVSS-based severity and CVE IDs, though Trivy's SARIF never tags a CWE (CVE/GHSA-centric, unlike CodeQL) — see [`../../UBO/docs/integrations.md`](../../UBO/docs/integrations.md) for a copy-pasteable GitHub Actions recipe.

**Pipeline-as-code security** (`pipeline_security_connectors.py`): static analysis of every `.github/workflows/*.yml` — write-all `GITHUB_TOKEN` permissions, unpinned third-party actions, and `pull_request_target` combined with an untrusted PR-head checkout (CRITICAL — the fork-PR code-execution pattern). No runtime execution.

**Real secret scanning** (`secret_scanner_connectors.py`): a real `gitleaks` binary scans the repo's full git history — the producer for `SECRET_DETECTED` outside a paid GitHub Advanced Security webhook. Secret values are never persisted, logged, or returned; a clean scan is never adjudicated as a false "compliant". Requires `gitleaks` + `git` in the runtime image (`project/Dockerfile`).

**Tamper-evidence hash chain**: every `evidence_records` insert computes `chain_hash = sha256(prev.chain_hash + this.signature)` under an advisory lock — proves no row was deleted/reordered, which the per-record HMAC alone can't.

**MCP server:** `devops_monitoring_mcp_server.py` — 15 tools (SCM audit, pipeline security, secret scan, drift, evidence, chain verification, waivers, attestations, DORA metrics, ITSM). See `mcp.md` → `devops-monitoring`.

**Background sweeps:** `risk_waiver_sweep.py` (hourly — expires overdue waivers, re-opens the underlying finding), `itsm_sla_sweep.py` (hourly — flags overdue ITSM tickets, re-escalates the finding).

---

### ITSM/Jira-ServiceNow SLA Bridge (`itsm_connectors.py` + `itsm_endpoints.py` + `itsm_sla_sweep.py`)

Opens a real Jira/ServiceNow ticket for a DevOps Monitoring finding and tracks its remediation SLA independent of the external system. SLA hours are severity-based: CRITICAL 48h, HIGH 168h (7d), MEDIUM 240h (10d), LOW 720h (30d).

**REST endpoints (prefix `/api/itsm`):**

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/itsm/tickets` | Open a real ticket via a registered `itsm_jira`/`itsm_servicenow` connector |
| `GET` | `/itsm/tickets` | Filtered list (status, external_system, breached_only) |
| `GET` | `/itsm/tickets/{id}` | Single ticket |
| `POST` | `/itsm/tickets/{id}/sync` | Resync one ticket's status from the external system now |
| `POST` | `/itsm/webhook` | Real-time status push (Jira Automation / ServiceNow Business Rule), Bearer-key auth |
| `GET` | `/itsm/sla-summary` | Open/breached/at-risk-24h counts |

Status reconciliation (`itsm_jira_tool.py`/`itsm_servicenow_tool.py` poll adapters) and SLA breach detection (`itsm_sla_sweep.py`) are deliberately separate concerns — a ticket that's never synced still gets its SLA breach detected on schedule.

---

### Infrastructure Monitoring (`iaas_connectors.py` + `postgres_cis_tool.py` + `railway_iaas_tool.py` + `infrastructure_monitoring_mcp_server.py`)

Continuous IaaS/OS/DB configuration audit. Railway (and most PaaS hosting) is immutable-container, no-SSH infrastructure — classic OS-level CIS benchmarks aren't auditable or applicable, so this covers what actually is: SQL-queryable Postgres configuration, and the Railway platform/deployment metadata exposed via its GraphQL API.

- **Postgres CIS-style hardening** (`postgres_cis_tool.py`, connector_type `postgres_cis`): SSL enforcement, password encryption scheme, superuser count, live unencrypted connections, connection logging. Read-only — a `pg_read_all_settings`/`pg_monitor` role is sufficient, superuser is not required.
- **Railway platform/deployment drift** (`railway_iaas_tool.py`, connector_type `railway_iaas`): unexpected public domain exposure (against a connector-configured allow-list) and deployment image digest with no matching pipeline attestation (`observability.pipeline_attestations.container_image_sha` — reports "unknown," never a fabricated finding, until at least one real attestation exists to compare against).
- **Connector credential rotation hygiene** (`connector_hygiene.py` + `connector_hygiene_sweep.py`, daily): the one check with no external system to poll — the system being checked is Intelligenza itself. Flags any active `poll_connectors` row whose credential (`credentials_rotated_at`, bumped only on an actual credential change, never on any other field edit) exceeds the staleness threshold (default 90 days).

No dedicated findings viewer — findings ride the generic `system_telemetry` → adjudication path and surface in Continuous Monitoring / Controls Monitor automatically.

**MCP server:** `infrastructure_monitoring_mcp_server.py` — 4 tools. See `mcp.md` → `infrastructure-monitoring`.

---

### Continuous Third-Party/Vendor Risk (`vendor_risk_endpoints.py` + `vendor_risk_sweep.py`)

Auditor-maintained register (VM-01, Vendor Security Assessment) of which vendors are "critical" and their current SOC 2 report coverage window — turns the assessment from a point-in-time checklist item into a continuously monitored one. Vendor spend-concentration breaches (`VENDOR_CONCENTRATION_BREACH`) are checked separately, from live ERP payment data, by `oracle_fusion_tool.py`'s poll-connector `pull_events()` rather than this register.

**REST endpoints (prefix `/vendor-risk`):** `GET ""` (list, optional `critical_only`), `PUT ""` (create/update a vendor's profile — recording a fresh `soc2_expires_at` also clears an EXPIRED status).

**Background sweep:** `vendor_risk_sweep.py` (daily) — flips any `CURRENT` profile past its `soc2_expires_at` to `EXPIRED` and re-ingests a fresh `vendor_soc2_expired` finding through the normal adjudication pipeline, same "control reliance basis has lapsed, goes back to failing" semantics as the DevOps Monitoring risk-waiver sweep below.

---

### AI Governance (`ai_governance_endpoints.py` + `ai_governance_sweep.py`)

Auditor-maintained register of the audited company's own AI system usage (AI-05 Third-Party AI Tool Assessment, AI-06 Human Oversight) — distinct from `observability.mcp_telemetry`/`ai-inventory.jsx`, which only inventories *this platform's own* MCP tool calls. Saving a system that requires human oversight but has none defined raises an `AI_HUMAN_OVERSIGHT_MISSING` finding immediately (AI-06 — a static configuration gap, not something that decays with time).

**REST endpoints (prefix `/ai-governance`):** `GET ""` (list, optional `high_risk_only`), `PUT ""` (create/update a system's governance profile).

**Background sweep:** `ai_governance_sweep.py` (daily) — the AI-05 time-based half: flips any `CURRENT` assessment past its `assessment_expires_at` to `EXPIRED` and re-ingests a fresh `ai_assessment_overdue` finding, mirroring `vendor_risk_sweep.py`'s shape exactly.

---

### Identity Graph Sync (`identity_graph_sync.py`)

Hourly full-refresh pull of real user↔role assignments and open SoD violations from every active Oracle Fusion connector, via `oracle_fusion_tool.get_user_roles()`/`get_sod_violations()` (both pre-existing and correct, but previously only reachable on-demand, never scheduled). Feeds `observability.identity_role_edges`/`.sod_violations`, which `mcp_governance.py` reads to populate `role_count`/`entitlements` on a URO's `risk_indicators` before it reaches The Graph Architect (`UBO/agents/graph_architect.py`) — those fields were always zero/empty for every real production event, so the agent's blast-radius/SPoF checks, though correctly implemented, were structurally dead. A delete-then-insert full refresh rather than an incremental pull: identity/role state is a snapshot to diff, not a stream of discrete events, so a revoked role actually disappears here rather than lingering. SoD violations are persisted but not (yet) re-raised as adjudicated events.

---

### Poll-Connector Dispatch Loop (`connector_poller.py`)

The single scheduler behind every pull-model connector — Oracle Fusion, Oracle HCM, SAP HANA, SailPoint, Dynamics 365, NetSuite, denied-party screening, GitHub/GitLab/Bitbucket SCM, Jira/ServiceNow ITSM, Postgres CIS, Railway IaaS, AWS IaaS, OT heartbeat (16 adapter types as of this writing). Adding a 17th means one new adapter module plus one `_ADAPTERS` entry — no new scheduler code.

Ticks every `CONNECTOR_POLLER_TICK_S` seconds (default `60`) and polls whichever registered connectors are actually due, per that connector's own `poll_interval_s` (configured per-connector in the Dendrai UBO Configuration screen, default `1800`/30min — not an env var). Each adapter's `pull_events()` output is normalized and fed into `mcp_governance._ingest_system_event`, the same `system_telemetry` insert path GitHub-sourced and internal-sweep-sourced events already go through — `mcp_governance.start_polling()` picks up any resulting flagged row and adjudicates it exactly as it would any other source. A connector whose `pull_events()` raises, or whose credentials can't be decrypted (`CONNECTOR_ENCRYPTION_KEY` missing/rotated), is skipped for that tick and recorded via `record_poll_result(..., "error", ...)` — never crashes the loop or blocks other connectors in the same tick.

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
| `GET` | `/auth/admin/audit-log` | Tamper-evident identity/access audit trail (admin) — `?category=&actor=&limit=` |
| `GET` | `/auth/admin/audit-log/verify` | Verify the audit trail's hash chain (admin) |

**Middleware:** `_DendraiAuthMiddleware` (Starlette `BaseHTTPMiddleware`) validates JWT on all routes except `/auth/`, `/health`, `/db/status`, `/docs`, `/redoc`, `/openapi.json`, `/mcp/`, `/github/`.

**Database schema:** `auth` PostgreSQL schema with 4 tables — `auth.users`, `auth.password_history`, `auth.sso_identities`, `auth.sessions`.

**Platform audit trail (`observability.audit_log`, `db.py`):** a hash-chained, HMAC-signed, append-only log — same tamper-evidence construction as `evidence_records` (see DevOps Monitoring above), but generic (`category`/`action`/`actor`/`target`/`detail`) rather than SARIF-shaped, and never deduplicated (every call is its own row). A small `_audit()` helper in `auth_endpoints.py` records every login success/failure, logout, SSO JIT-provisioning, role change, user create/delete/activate, password reset, screen-permission edit, and role CRUD (`category="auth"`). `mcp_guards.audit_log()` now writes here too (`category="mcp_tool"`), replacing what used to be a local flat file (`mcp_audit.log`) that was silently wiped on every redeploy since no volume was mounted for it — the flat file is kept only as a last-resort fallback when the database itself is unreachable. Each row's `signature` is `HMAC-SHA256(record_json, AUDIT_SIGNING_KEY)` and `chain_hash = sha256(prev.chain_hash + this.signature)`, inserted under an advisory lock; `db.verify_audit_chain()` walks the table and reports the first broken link, if any. `AUDIT_SIGNING_KEY` is optional — a missing key falls back to a random per-process key (chain-hash tamper-evidence still works; only HMAC re-verification of old signatures is lost across a restart, a documented degradation, not a silent one). Insertion never raises — an audit-logging failure must never block the action it's recording.

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

### Dendrai UBO Governance Brain (`mcp_governance.py`)

Background service that consumes flagged telemetry rows and runs them through the full UBO medallion pipeline:

```
Bronze → Silver (heuristic policy checks) → Gold (risk score) → Council (Quant + Linguist + Graph Architect + real PaC/Rego → Adjudicator)
```

Adjudication results are written to `observability.adjudicated_tool_calls`. Started automatically alongside `api_server.py`.

**Real Policy-as-Code evaluation, not just Silver's heuristics**: Silver's own policy checks (`POL-CORE-*`/`POL-SAP-*`/`POL-GH-*`/etc., `UBO/pipeline/silver.py`) are a separate, hardcoded Python rule set — they were never connected to the actual saved Rego modules editable on the Policy-as-Code screen. `_evaluate_pac_policy()` (`mcp_governance.py`) closes that gap: every URO is additionally checked against the real Rego module (via `db.get_latest_pac_module`, falling back to the built-in defaults) for whichever PaC process its `source_system` maps to (`_SOURCE_SYSTEM_TO_PAC_PROCESS` — a starting default, e.g. GitHub → `itgc`, Oracle Fusion → `procure_to_pay`; adjust as real usage clarifies which process actually applies per system). Any fired deny rules are folded into `policy_violations` alongside Silver's own, and the evaluation appears as a distinct `"Policy-as-Code (Rego)"` voice in `council_votes` — same generic append pattern used for the optional LLM 4th-opinion voice, so the existing UI (`cem.jsx`) renders it with zero frontend changes. Uses the real `opa` binary when available, the same heuristic simulation as the Evaluate button otherwise — never blocks adjudication on failure (best-effort, like the rest of this pipeline's optional enrichments).

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

#### Monitored Systems — push-model ingestion for non-MCP agents/systems

Any external system (a LangChain/OpenAI/custom-loop agent, Saviynt, SAP, ServiceNow, ...) that isn't behind an MCP server registers here to get a per-system, revocable ingest API key, then POSTs its own events to `POST /observability/telemetry/ingest` with `Authorization: Bearer <ingest_api_key>` — the framework-agnostic path described in the top-level README's "Governing non-MCP AI agents." The key is issued encrypted at rest (`CONNECTOR_ENCRYPTION_KEY`, see the environment variable table above) rather than as a plaintext UUID.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/systems` | All registered systems, with activity stats and the (decrypted, for display) ingest API key |
| `POST` | `/systems` | Register a new system — issues a fresh encrypted ingest API key |
| `PUT` | `/systems/{id}` | Update a system's config (does not touch its key) |
| `DELETE` | `/systems/{id}` | Deactivate (soft delete) |
| `POST` | `/systems/{id}/rotate-key` | Issue a fresh encrypted key, invalidating the old one — the migration path for a system still on the legacy plaintext `ingest_api_key` column |

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
- **Controls catalog + assurance metadata** (`controls_catalog` — `last_fired_at`/`last_verified_at`/`last_test_passed` per control_id)
- **Authentication** (`auth.users`, `auth.password_history`, `auth.sso_identities`, `auth.sessions`)
- **MCP observability** — `observability` schema:
  - `mcp_telemetry` — every JSON-RPC call logged by the proxy
  - `adjudicated_tool_calls` — Dendrai UBO Governance Brain verdicts
  - `tool_call_holds` — pre-execution holds (PENDING / APPROVED / DENIED / EXPIRED)
  - `tool_call_suppressions` — suppression allowlist rules
  - `tool_latency_summary` — materialized view: P50/P95/P99 per tool
  - `flagged_calls` — view: telemetry rows with at least one risk flag
  - `poll_connectors` — Fernet-encrypted credentials for every scheduled poll-based connector (Oracle Fusion, SAP HANA, SailPoint, Dynamics 365, NetSuite, GitHub/GitLab SCM, Jira/ServiceNow ITSM, Postgres CIS, Railway IaaS)
  - `evidence_records` — immutable, HMAC-signed SARIF findings log
  - `scm_repository_state` / `scm_drift_events` — last-known-good branch-protection snapshot + drift/time-series log ("2am override" detection)
  - `risk_waivers` — time-boxed, hash-keyed HITL-approved exceptions, auto-expired hourly
  - `pipeline_attestations` — OIDC/SLSA/env-hash/Cosign/SBOM pipeline provenance
  - `itsm_tickets` — Jira/ServiceNow tickets tracking findings, with SLA due/breach timestamps
  - `pac_test_runs` — negative-control test run history (schema-contract + corpus results), audit evidence
  - `identity_role_edges` / `sod_violations` — Oracle Fusion user↔role assignments and open SoD violations, full-refreshed hourly by `identity_graph_sync.py`
  - `audit_log` — hash-chained, HMAC-signed, append-only platform audit trail (identity/access changes + MCP tool calls) — see "Authentication" above
- **Journal Entry Testing** — reuses Exception Management's `exception_control_events`/`exception_model_inferences`/`exception_auditor_triage` tables (`control_id` prefixed `JE-*`, `event_type='JOURNAL_ENTRY'`); `raw_payload` is Fernet-encrypted at rest (`CONNECTOR_ENCRYPTION_KEY`) and purged past `EXCEPTION_EVENT_RETENTION_DAYS` (default 400d) by `pii_retention_sweep.py`
- **Regulatory Change Management** (`regulatory_change_versions`, `regulatory_change_proposals`) — fetched feed-text snapshots (sha256-deduped) and the LLM-drafted proposals awaiting/recording a human decision

Without `DATABASE_URL` the pipeline runs in stateless mode — all data is returned in the API response but nothing is persisted.

Run history endpoints require the database: `GET /history/runs/{ticker}`, `GET /history/runs/{ticker}/{run_id}`.

---

## Infrastructure endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Service health, AI availability, model name |
| `GET /db/status` | Database connectivity and configuration |
| `GET /docs` | Swagger UI (all endpoints) |