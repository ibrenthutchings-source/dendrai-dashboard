# Dendrai MCP Servers

All servers live in [`project/agentic-tools/`](project/agentic-tools/) and are built with **FastMCP** (`mcp.server.fastmcp`). Each runs as a standalone Python process registered in Claude Desktop or Claude Code via `mcp.run()`.

---

## 1. EDGAR MCP Server
**File:** [`edgar_mcp_server.py`](project/agentic-tools/edgar_mcp_server.py)  
**Server name:** `edgar`  
**Dependencies:** `edgar_tool.py` (same directory)  
**Auth:** None — uses the public EDGAR REST API (no key required)

Exposes SEC EDGAR company data, XBRL financial time-series, regulatory filings, and peer benchmarks for any publicly listed company.

| Tool | Input | Description |
|------|-------|-------------|
| `edgar_company_info` | `ticker` | CIK, SIC code & description, entity type, state of incorporation, fiscal year end, exchanges, address |
| `edgar_financial_metrics` | `ticker` | XBRL time-series for 20+ metrics (Revenue, NetIncome, TotalAssets, EPS, Cash, CapEx, R&D, …) — quarterly + annual, past 5 years |
| `edgar_risk_factors` | `ticker`, `max_filings=2` | Item 1A Risk Factors text from 10-K filings (up to 30 000 chars per filing) |
| `edgar_proxy_data` | `ticker`, `max_filings=2` | DEF 14A proxy data: exec comp (CD&A), board composition, say-on-pay votes, shareholder proposals |
| `edgar_filings_index` | `ticker` | Full list of 10-K / 10-Q / 8-K / DEF 14A filings (past 5 years); 8-K items annotated with human-readable descriptions |
| `edgar_sic_peers` | `ticker`, `max_peers=20` | Public companies sharing the same SIC code — CIK, ticker, name, state |
| `edgar_peer_financials` | `ticker`, `max_peers=10` | Latest annual XBRL snapshot for SIC-peer companies (same 20+ metrics as `edgar_financial_metrics`, latest value only) |

---

## 2. FRED Macro MCP Server
**File:** [`fred_mcp_server.py`](project/agentic-tools/fred_mcp_server.py)  
**Server name:** `fred-macro`  
**Dependencies:** `fred_tool.py`  
**Auth:** Requires a free FRED API key (`FRED_API_KEY` env var or passed per-call)

Finds leading macro-economic indicators from the St. Louis Fed FRED database that are statistically correlated with a company's quarterly financials.

| Tool | Input | Description |
|------|-------|-------------|
| `fred_macro_correlations` | `ticker`, `min_correlation=0.85`, `lags="1,2,3"`, `output_file`, `fred_api_key` | Computes Pearson correlations between 30 FRED macro series and the company's quarterly financials (Revenue, GrossProfit, NetIncome, EBITDA, EPS, OperatingCashFlow, TotalAssets, StockholdersEquity). Tests 1-, 2-, and 3-quarter leading lags. Saves results to JSON. |
| `fred_list_series` | — | Lists all 30 FRED series in the catalog grouped by category (GDP, Unemployment, CPI, Fed Funds, Yield Curve, VIX, Credit Spreads, Housing, etc.) |
| `fred_load_analysis` | `file_path="fred_macro_indicators.json"` | Summarizes a previously saved analysis file without re-running the full correlation. |

**FRED series catalog:** 30 series across categories including Growth, Labour, Inflation, Monetary Policy, Credit, Equity Volatility, Consumer, Trade, and Housing.

---

## 3. Predictive Analytics MCP Server
**File:** [`predictive_analytics_mcp_server.py`](project/agentic-tools/predictive_analytics_mcp_server.py)  
**Server name:** `predictive-analytics`  
**Dependencies:** `predictive_analytics_tool.py`, `edgar_tool.py`  
**Auth:** Optional `FRED_API_KEY` for live macro correlations

Runs 10 predictive risk and financial analytics models against EDGAR XBRL data. Covers financial statement analysis, fraud detection, industry risk scoring, scenario planning, forecasting, and news signal grading.

| Tool | Input | Description |
|------|-------|-------------|
| `predictive_financial_ratios` | `ticker` | Revenue growth (YoY), gross/net/FCF margins, R&D intensity, asset growth, cash ratio, TATA, DSRI, SGI, GMI |
| `predictive_beneish_mscore` | `ticker` | Beneish M-Score earnings manipulation detection (5-variable model). RAG: Red > −1.78, Amber > −2.22, Green ≤ −2.22 |
| `predictive_industry_risks` | `ticker`, `industry=""` | Scores 8 industry-specific risks with RAG status, velocity (−1 to +3), control environment, and peer benchmark. Auto-detects industry from SIC. Supports: Semiconductors, Automotive OEM, Software & Cloud, Financial Services, Healthcare & Pharma, Energy & Utilities, Retail & Consumer, Generic |
| `predictive_scenario_analysis` | `ticker`, `industry=""` | Bear / Base / Bull scenarios: revenue change %, projected revenue, gross margin impact (bps), projected net income, narrative |
| `predictive_grey_swan` | `ticker`, `industry=""` | 4-stage escalation cascade (T+0 → T+90 days) modelled from the highest-velocity Amber risk. Includes score trajectory and impact estimates scaled from quarterly revenue |
| `predictive_macro_indicators` | `ticker`, `industry=""`, `fred_api_key=""`, `min_correlation=0.60`, `lags="1,2,3,4"` | FRED leading macro indicator correlations. Returns pre-computed industry benchmarks without an API key; runs live 30-series analysis with one |
| `predictive_forecast` | `ticker`, `metric="Revenue"`, `horizon=4`, `model="ensemble"` | Time-series forecast with 95% CI. Models: `arima` (ARIMA(2,1,1)), `prophet` (linear + Fourier), `rf` (Random Forest, 25 bootstrap trees), `ensemble` (inverse-MAPE weighted blend) |
| `predictive_backtest` | `ticker`, `metric="Revenue"` | Walk-forward expanding-window backtest across all 3 models. Returns MAPE, RMSE, R², directional precision/recall/F1, calibrated ensemble weights |
| `predictive_rss_signals` | `ticker`, `company_name=""`, `max_articles=20` | Grades live RSS articles (SEC 8-K, Federal Reserve, CISA, BIS, EPA) by relevance × severity. Velocity = relevance × severity × 5 × feedWeight. RAG: Red ≥ 3, Amber ≥ 2, Green < 2 |
| `predictive_qoq_momentum` | `ticker`, `window=8` | Rolling QoQ revenue momentum over N quarters. Trend: IMPROVING (> +5%), DETERIORATING (< −5%), STABLE. Also derives hedge ratio direction. |
| `predictive_full_analysis` | `ticker`, `industry=""`, `fred_api_key=""`, `forecast_horizon=4`, `forecast_metric="Revenue"`, `include_rss=True`, `include_fred=True` | Runs all 10 models in a single call. Returns comprehensive JSON report. Takes 30–90 s due to EDGAR rate limits. |
| `predictive_list_industries` | — | Lists all supported industry templates and their risk names, categories, and base scores |

---

## 4. RSS Industry News MCP Server
**File:** [`rss_mcp_server.py`](project/agentic-tools/rss_mcp_server.py)  
**Server name:** `rss-news`  
**Dependencies:** `rss_tool.py`  
**Auth:** None

Identifies the top 3 RSS news feeds for a company's industry (using EDGAR SIC codes) and downloads articles from the past 12 months.

| Tool | Input | Description |
|------|-------|-------------|
| `rss_industry_news` | `ticker`, `output_file="rss_industry.json"` | Looks up the company's SIC category, selects the 3 most relevant feeds from a 25+ category catalog, fetches all articles from the last year, and saves to JSON. Returns per-feed article counts and date ranges. |
| `rss_load_results` | `file_path="rss_industry.json"` | Loads and summarizes a saved analysis: per-feed article counts, date ranges, and the 5 most recent headlines per feed |
| `rss_list_feeds` | — | Lists every feed in the curated catalog, grouped by industry category, with name, URL, and description |

**Coverage:** 25+ industry categories including Technology, Healthcare, Finance, Retail, Energy, Automotive, and more.

---

## 5. Token Cost MCP Server
**File:** [`token_cost_mcp_server.py`](project/agentic-tools/token_cost_mcp_server.py)  
**Server name:** `token-cost`  
**Dependencies:** `token_cost_tool.py`  
**Auth:** Optional `ANTHROPIC_API_KEY` for exact token counts (falls back to character-based approximation ~4 chars/token, ±20–30%)

Estimates and tracks Claude API token usage and USD costs before and after API calls. Persists session data to `token_costs.json`.

| Tool | Input | Description |
|------|-------|-------------|
| `cost_estimate` | `text`, `model="claude-opus-4-8"`, `max_output_tokens=4096`, `system_prompt=""`, `anthropic_api_key=""` | Estimates input + output token count and USD cost before an API call. Shows context window, cache pricing (write 1.25×, read 0.10×). Warns if input exceeds context window. |
| `cost_count_tokens` | `text`, `model`, `system_prompt=""`, `anthropic_api_key=""` | Counts tokens for a text string — exact via Anthropic API or approximated locally |
| `cost_track` | `model`, `input_tokens`, `output_tokens`, `cache_read_tokens=0`, `cache_write_tokens=0`, `label=""`, `session="default"`, `data_file` | Records actual token usage from an API response and accumulates per-session costs |
| `cost_session_summary` | `session="default"`, `data_file` | Shows totals (calls, input/output/cache tokens, USD) and the 10 most recent calls for a session |
| `cost_reset_session` | `session="default"`, `data_file` | Clears all accumulated data for a session (irreversible) |
| `cost_list_models` | — | Lists all Claude models in the pricing catalog with per-MTok input/output prices, context window, and max output |
| `cost_list_sessions` | `data_file` | Lists all tracked sessions in the data file with call count and total cost |

**Model aliases:** `opus` → `claude-opus-4-8`, `sonnet` → `claude-sonnet-4-6`, `haiku` → `claude-haiku-4-5`, `fable` → `claude-fable-5`

---

## 6. Oracle Fusion Controls MCP Server
**File:** [`oracle_fusion_mcp_server.py`](project/agentic-tools/oracle_fusion_mcp_server.py)  
**Server name:** `oracle-fusion`  
**Dependencies:** `oracle_fusion_tool.py`  
**Auth:** Required — `ORACLE_FUSION_HOST`, `ORACLE_FUSION_USERNAME`, `ORACLE_FUSION_PASSWORD` env vars. Optional OAuth 2.0: `ORACLE_FUSION_CLIENT_ID`, `ORACLE_FUSION_CLIENT_SECRET`

Exposes Oracle Fusion Cloud control data from the Risk Management Cloud Service (RMCS) for internal audit and controls review workflows.

| Tool | Input | Description |
|------|-------|-------------|
| `fusion_control_summary` | — | **Recommended first call.** Aggregated control health dashboard combining library effectiveness, open issues, and SOD violations. Returns RAG status (R/A/G), 0–25 control risk score, and `risk_signals` compatible with the Dendrai risk register schema. |
| `fusion_control_library` | `control_type=""`, `category=""`, `status="Active"` | All control definitions: type (Preventive / Detective / Corrective), frequency, owner, effectiveness rating, last test date |
| `fusion_control_results` | `date_from=""`, `date_to=""`, `effectiveness=""` | Control operating effectiveness test results — tester, conclusion, exceptions noted. Useful for audit workpaper evidence. |
| `fusion_control_issues` | `status="Open"`, `severity=""`, `date_from=""` | Open deficiencies and remediation plans — severity (Critical / High / Medium / Low), RAG, root cause, owner, due date |
| `fusion_user_roles` | `username=""`, `role_name=""` | User-to-role assignments via SCIM 2.0 API. Filter by user or role for access reviews and privilege checks. |
| `fusion_sod_violations` | `status="Open"`, `risk_level=""` | Segregation-of-duties violations — conflicting role pair, SOD policy breached, risk level, mitigating control |
| `fusion_audit_events` | `module=""`, `date_from=""`, `date_to=""`, `event_type=""`, `username=""` | Transaction audit trail from FSCM modules (FIN_AP, FIN_AR, FIN_GL, FIN_FA, PRC, HCM). Filter by module, event type (Create / Update / Delete), date range, or user. |

---

## 7. Risk-as-Code MCP Server
**File:** [`risk_as_code_mcp_server.py`](project/agentic-tools/risk_as_code_mcp_server.py)  
**Server name:** `risk-as-code`  
**Dependencies:** `risks_as_code.py`, `db.py`  
**Auth:** Optional `DATABASE_URL` for PostgreSQL persistence

Converts risk lists from three sources (live loop output, PostgreSQL, Excel/CSV) into structured YAML artifacts conforming to **OSCAL (NIST SP 800-53)** and **COSO ERM 2017 / ISO 31000:2018**. Used by the Frameworks screen.

> **Note:** This server generates OSCAL/COSO ERM framework artifacts. The `RiskAsCodeScreen` in the UI uses a separate internal scoring-rules format (not validated here). The `rac_validate` tool only validates OSCAL and COSO ERM structure.

| Tool | Input | Description |
|------|-------|-------------|
| `rac_from_loop_output` | `risks_json`, `ticker`, `period=""`, `framework="both"`, `industry=""`, `ratios_json=""`, `objectives_json=""`, `maps_json=""`, `signals_json=""`, `run_id=None`, `save_to_db=False` | Converts a JSON array from `output.s2.risks` (Dendrai loop) to OSCAL and/or COSO ERM YAML. Optionally enriches with financial ratios, audit objectives, MAPs, and signals. Can persist to DB. |
| `rac_from_database` | `ticker`, `run_id=None`, `framework="both"` | Fetches risk scores from the PostgreSQL `risk_scores` table (most recent completed run if `run_id` omitted) and converts to YAML artifacts |
| `rac_from_excel` | `file_path`, `ticker`, `period=""`, `industry=""`, `framework="both"`, `sheet_name="0"`, `save_to_db=False` | Parses an Excel (.xlsx/.xls) or CSV risk register and converts to YAML. Flexible column name matching (case-insensitive): ID, Name, Category, Score, Base Score, RAG, Velocity, CE, Peer, Narrative, Impact, Likelihood |
| `rac_validate` | `yaml_content`, `framework="oscal"` | Validates OSCAL or COSO ERM YAML structure. Checks required keys, UUID presence, risk/findings alignment, and ISO/COSO required fields. Returns `{valid, errors, warnings}` |
| `rac_list_runs` | `ticker`, `limit=10` | Lists recent risk loop runs for a ticker showing which have saved YAML artifacts and which frameworks were generated |

**Output formats:**
- `oscal` — NIST SP 800-53 Assessment Results YAML with UUIDs, risk statements, findings, and control references
- `coso_erm` — COSO ERM 2017 risk universe YAML with component classification, performance metrics, and review cadence
- `both` — Returns both documents in the same response

---

---

## 8. Policy-as-Code MCP Server
**File:** [`pac_mcp_server.py`](project/agentic-tools/pac_mcp_server.py)  
**Server name:** `policy-as-code`  
**Dependencies:** `pac_endpoints.py`, `db.py`  
**Auth:** None. `DATABASE_URL` optional for persistence; `MCP_READ_ONLY=true` blocks writes.

Manages Rego policy modules for the five Oracle Fusion ERP processes (ITGC, O2C, P2P, R2S, R2R). Handles version history, multi-approver sign-offs, and GitHub / Confluence integration hooks.

| Tool | Input | Description |
|------|-------|-------------|
| `pac_list_modules` | `process=""` | Latest module metadata for all 5 processes. Falls back to built-in defaults for processes not yet saved. |
| `pac_get_module` | `process` | Full Rego content + version + approvals. Falls back to built-in default if no saved version. |
| `pac_save_module` | `process`, `rego_content`, `version=""`, `module_name=""` | Save a new versioned module. Auto-increments version when omitted. Blocked by `MCP_READ_ONLY`. |
| `pac_module_history` | `process`, `limit=10` | Version history (newest first). Each entry includes `module_id` for `pac_approve_module`. |
| `pac_approve_module` | `module_id`, `approver`, `role=""` | Add a named approver sign-off to a specific module version. |
| `pac_get_hooks` | `hook_type=""` | GitHub and/or Confluence hook configs. |
| `pac_save_hook` | `hook_type`, `repo_url=""`, `branch="main"`, `token=""`, `confluence_url=""`, `space_key=""`, `page_id=""` | Save/update a GitHub or Confluence integration hook. |
| `pac_get_default` | `process` | Built-in Dendrai Rego default for any process — no DB required. |
| `pac_validate_rego` | `rego_content` | Static analysis: package declaration, brace balance, deny rule inventory, sprintf format sanity. No OPA binary required. |
| `pac_diff_modules` | `process`, `context_lines=5` | Unified diff of the two most recent saved versions. |

**Processes:** `itgc` · `order_to_cash` · `procure_to_pay` · `receive_to_ship` · `record_to_report`

---

## 9. Controls-as-Code MCP Server
**File:** [`cac_mcp_server.py`](project/agentic-tools/cac_mcp_server.py)  
**Server name:** `controls-as-code`  
**Dependencies:** `pac_endpoints.py`, `db.py`  
**Auth:** None. `DATABASE_URL` optional for persistence; `MCP_READ_ONLY=true` blocks writes.

Generates and manages Rego Controls-as-Code artifacts. Synthesises testable control harnesses from PAC deny rules, evaluates controls against sample OPA input events, and maps control coverage to the risk register.

| Tool | Input | Description |
|------|-------|-------------|
| `cac_generate` | `controls_json`, `ticker=""`, `run_id=0`, `persist=True` | Generate `control_active[ref]` Rego from a JSON controls array. Groups by category. Persists to DB with vector embedding. |
| `cac_get_latest` | `ticker=""` | Most recent CaC artifact from the database (full Rego + metadata). |
| `cac_list_artifacts` | `ticker=""`, `limit=20` | Paginated metadata list (id, ticker, run_id, generated_at). No Rego content. |
| `cac_from_pac` | `process=""`, `ticker=""` | Synthesise a test-harness CaC from PAC deny rules — one `control_active` entry per deny rule. Persists to DB. |
| `cac_validate` | `rego_content` | Structural validation: package, `control_active` rules, required fields, no duplicate refs. |
| `cac_evaluate_event` | `rego_content`, `input_event_json` | Heuristic simulation of deny rule evaluation against a sample OPA input event. No OPA binary required. Returns fired / passed / skipped rules with confidence scores. |
| `cac_export` | `artifact_id=0`, `format="rego"` | Export an artifact as `rego`, `json` (parsed controls array), or `yaml`. Defaults to latest artifact. |
| `cac_map_to_risks` | `ticker=""`, `run_id=0`, `limit=50` | Token-match controls against `risk_scores` rows → coverage matrix showing mapped controls per risk and uncovered risks. |

---

## Setup Summary

All servers are registered identically in Claude Code (`.claude/settings.json`) or Claude Desktop (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "<server-name>": {
      "command": "python",
      "args": ["/absolute/path/to/agentic-tools/<server_file>.py"]
    }
  }
}
```

| Server | Name | Key Required |
|--------|------|--------------|
| `edgar_mcp_server.py` | `edgar` | None |
| `fred_mcp_server.py` | `fred-macro` | `FRED_API_KEY` |
| `predictive_analytics_mcp_server.py` | `predictive-analytics` | None (`FRED_API_KEY` optional) |
| `rss_mcp_server.py` | `rss-news` | None |
| `token_cost_mcp_server.py` | `token-cost` | `ANTHROPIC_API_KEY` optional |
| `oracle_fusion_mcp_server.py` | `oracle-fusion` | `ORACLE_FUSION_HOST/USERNAME/PASSWORD` |
| `risk_as_code_mcp_server.py` | `risk-as-code` | `DATABASE_URL` optional |
| `pac_mcp_server.py` | `policy-as-code` | `DATABASE_URL` optional |
| `cac_mcp_server.py` | `controls-as-code` | `DATABASE_URL` optional |
