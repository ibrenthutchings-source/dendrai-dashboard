# Integrations

How the Dendrai UBO Governance Brain connects to external systems and dashboards.

---

## GitHub Webhook Listener

**File:** `project/agentic-tools/github_endpoints.py`
**Registered at:** `POST /github/webhook`

Receives GitHub webhook events, verifies the HMAC-256 signature, and runs the payload through the full Bronze→Silver→Gold→Council pipeline. The adjudication result is written to the same `observability.adjudicated_tool_calls` table as MCP proxy events, distinguished by `source_system = 'GITHUB'`.

### Pipeline Flow

```
GitHub repo event
      │  POST /github/webhook
      │  X-GitHub-Event: <event_type>
      │  X-Hub-Signature-256: sha256=<hmac>
      ▼
_verify_signature()     ← HMAC-SHA256 timing-safe comparison
      │ 403 if invalid
      ▼
payload["X-GitHub-Event"] = gh_event   ← inject for GitHubBronzeHandler
      │
      ▼
BronzeIngestionLayer.ingest(payload, SourceSystem.GITHUB)
      ▼
SilverConformationLayer.conform(uro)   ← policy rules run here
      ▼
GoldAggregationLayer.score(uro)        ← composite risk score + tier
      ▼
CouncilOrchestrator.evaluate(uro)      ← three agents + adjudicator
      │
      ├─ asyncio.create_task(_write_adjudication(...))   ← fire-and-forget DB write
      │
      └─ return JSON response to GitHub
```

`GitHubBronzeHandler` reads `raw_event["X-GitHub-Event"]` to determine the `EventType`. This key is injected into the payload dict before ingestion so the handler works identically whether the event arrives live via webhook or from a test harness.

### Signature Verification

```python
def _verify_signature(body: bytes, sig_header: str | None) -> bool:
    if not WEBHOOK_SECRET:
        return True   # pass-through for local testing without a secret
    mac = hmac.new(WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, sig_header)   # timing-safe
```

If `GITHUB_WEBHOOK_SECRET` is not set, signature verification is skipped with a warning log. **Always set it in production.**

### Response JSON

```json
{
  "received": true,
  "adjudicated": true,
  "uro_id": "a1b2c3d4-...",
  "event": "push",
  "repo": "org/repo-name",
  "risk_tier": "HIGH",
  "risk_score": 0.72,
  "verdict": "ESCALATE",
  "requires_human_review": false,
  "policy_violations": ["POL-GH-002: Force push to protected branch..."]
}
```

GitHub's webhook dashboard shows this response for every delivery, making it easy to see the risk verdict without checking the database.

### Degraded Mode

If the UBO package is not importable (e.g., missing optional dependency), the endpoint returns:

```json
{"received": true, "adjudicated": false, "reason": "UBO pipeline not available"}
```

All GitHub events are still logged at `INFO` level. No 500 is returned to GitHub.

### One-Time Setup

**1. Set the webhook secret in `.env`:**

```
# project/agentic-tools/.env
GITHUB_WEBHOOK_SECRET=any-string-you-pick
```

**2. Restart `api_server.py`** so the new env var is loaded.

**3. Expose the local server (if running locally):**

```bash
ngrok http 8001
# copy the HTTPS forwarding URL, e.g. https://abc123.ngrok.io
```

**4. Register the webhook in the target GitHub repo:**

```
Settings → Webhooks → Add webhook

Payload URL:   https://<your-host>/github/webhook
Content type:  application/json
Secret:        <same value as GITHUB_WEBHOOK_SECRET>

Which events?  Let me select individual events:
  ☑ Branch or tag creation
  ☑ Branch protection rules
  ☑ Dependabot alerts
  ☑ Pull request reviews
  ☑ Pushes
  ☑ Secret scanning alerts
```

**5. Apply the schema migration** (one-time — idempotent):

```bash
psql $DATABASE_URL -f project/agentic-tools/telemetry_schema.sql
```

Or paste the v2 block from the bottom of that file into the Railway dashboard query editor.

---

## GitLab Support — Poll Connector + On-Demand Audit (no live webhook listener yet)

**Files:** `gitlab_scm_tool.py` (scheduled poll adapter), `scm_audit_endpoints.py` (on-demand "run now" REST endpoint)

Unlike GitHub, there is currently no live `/gitlab/webhook` receiver — GitLab events reach the pipeline two ways instead, both calling `github_endpoints._write_adjudication()` generalized with a `source_system="GITLAB"` parameter (the function's hardcoded `'GITHUB'` literal in its SQL insert became a `%s` parameter defaulting to `"GITHUB"`, for backward compatibility with the real GitHub webhook path):

1. **Scheduled**: `gitlab_scm_tool.py`'s poll-connector adapter (`connector_type='gitlab_scm'`) re-audits the registered project/branch on a schedule and synthesizes a `protected_branch_audit`-shaped event.
2. **On-demand**: `scm_audit_endpoints.py`'s `POST /scm-audit/repositories/{id}/run` sets a synthetic `X-Gitlab-Event` header and runs the same synthesized-event path immediately, for a "run now" button.

Both paths produce the same normalized `compliance` sub-dict shape as the GitHub path (`scm_connectors.normalize_gitlab_compliance`) and route through `GitLabBronzeHandler` — see [pipeline.md](pipeline.md)'s Bronze layer section for its full event mapping table. Adding a real live GitLab webhook receiver (mirroring `github_endpoints.py`'s `/github/webhook` HMAC-verified route) would be a natural follow-on, not yet built.

---

## Poll-Connector Framework — the metadata/pull-model spine

**File:** `project/agentic-tools/connector_poller.py`

The inverse of the webhook listeners above: instead of an external system pushing events to Dendrai, Dendrai polls the external system on a schedule and holds its credentials (Fernet-encrypted, `db.encrypt_credentials`, keyed by `CONNECTOR_ENCRYPTION_KEY`). One generic dispatch loop reads `observability.poll_connectors` (configured entirely from the app UI — Dendrai UBO Configuration screen's "Poll-Based Connectors" card — never env vars) and hands each due connector to its adapter module.

```
connector_poller.start_polling()  ← background task, started in api_server.py's lifespan
  │  every CONNECTOR_POLLER_TICK_S (default 60s), checks which connectors are due
  ▼
_poll_one(connector_id)
  │  adapter = _ADAPTERS[connector["connector_type"]]
  │  events = adapter.pull_events(base_url, credentials, extra_config, since)
  ▼
mcp_governance._detect_system_flags(event) → risk_flags
mcp_governance._ingest_system_event(...)   → observability.system_telemetry row
  │  (ON CONFLICT (server_name, event_id) DO NOTHING — dedup on overlapping polls)
  ▼
mcp_governance.start_polling()'s own loop picks up any row with non-empty risk_flags
  and runs it through Bronze → Silver → Gold → Council, same as every other source
```

Adding a new connector type means one adapter module implementing this contract, plus one `_ADAPTERS` entry — no scheduler or pipeline changes:

```python
def pull_events(base_url, credentials, extra_config, since) -> list[dict]:
    """Returns [{event_id, event_type, actor, action, resource, severity, raw_payload}, ...]"""

def test_connection(base_url, credentials, extra_config) -> tuple[bool, str]:
    """Verify connectivity — same call pull_events() needs, but no event ingestion."""

def is_configured(base_url=None) -> bool:
    """Whether this adapter's runtime dependencies (e.g. a Python package) are available."""
```

| `connector_type` | Adapter module | What it audits |
|---|---|---|
| `oracle_fusion` | `oracle_fusion_tool.py` | Oracle Fusion Cloud RMCS/FSCM controls |
| `sap_hana` | `sap_hana_tool.py` | SAP HANA audit log |
| `sailpoint` | `sailpoint_tool.py` | SailPoint IdentityNow activity |
| `dynamics365` | `dynamics365_tool.py` | Dataverse `audit` entity |
| `netsuite` | `netsuite_tool.py` | NetSuite audit trail |
| `github_scm` | `github_scm_tool.py` | GitHub branch-protection/CODEOWNERS (DevOps Monitoring) |
| `gitlab_scm` | `gitlab_scm_tool.py` | GitLab protected-branch/CODEOWNERS (DevOps Monitoring) |
| `itsm_jira` | `itsm_jira_tool.py` | Jira ticket status reconciliation (ITSM SLA Bridge) |
| `itsm_servicenow` | `itsm_servicenow_tool.py` | ServiceNow incident status reconciliation (ITSM SLA Bridge) |
| `postgres_cis` | `postgres_cis_tool.py` | Postgres CIS-style hardening (Infrastructure Monitoring) |
| `railway_iaas` | `railway_iaas_tool.py` | Railway platform/deployment drift (Infrastructure Monitoring) |

Most adapters are point-in-time configuration/state checks (not an append-only event log), so `since` is unused by the SCM/ITSM/Postgres/Railway adapters — each poll simply re-evaluates the target's *current* state.

---

## DevOps Monitoring: SCM Branch-Protection Auditing + SARIF Evidence

**Files:** `scm_connectors.py` (pure REST + normalization), `scm_audit_endpoints.py` (on-demand "run now" REST API), `github_scm_tool.py` / `gitlab_scm_tool.py` (scheduled poll adapters), `evidence_endpoints.py` (SARIF ingestion), `devops_monitoring_mcp_server.py`

Reuses the exact same Bronze→Silver→Gold→Council pipeline every other source rides — a "branch-protection audit" is just a synthesized event carrying a normalized `compliance` dict (`enforce_admins`, `required_approving_review_count`, `dismiss_stale_reviews`, `has_required_sast_check`, `has_required_test_check`, `codeowners_present`, `codeowners_covers_workflows`), whether it came from a real webhook or a scheduled poll.

### Drift & Time-Series Detection

`db.record_scm_audit_snapshot(resource, compliance)` keeps one "last known good" row per `resource` (`observability.scm_repository_state`) and diffs every new audit against it (`scm_connectors.diff_compliance`). Every control that flips — either direction — becomes an `observability.scm_drift_events` row. A row `resolved_at` shortly after `detected_at` is the "2am override" pattern: a control briefly weakened then restored, which a single point-in-time check would never catch.

### SARIF Evidence Ingestion

```
POST /evidence/webhook
Authorization: Bearer <ingest_api_key>   (per-system key from the monitored-systems registry)
{
  "repository": "org/repo", "commit_sha": "...", "source": "github_actions" | "snyk" | "sonarqube" | ...,
  "scan_status": "PASS" | "FAIL", "sarif": { ... OASIS SARIF 2.1.0 ... }
}
```

Each finding becomes one immutable `observability.evidence_records` row — SHA-256 fingerprinted (`repository|file_path|rule_id|line_snippet`, deduped against `(fingerprint, commit_sha)`) and HMAC-signed (`EVIDENCE_SIGNING_KEY`) so `/evidence/records/{id}/verify` can prove it hasn't been tampered with since ingestion. HIGH/CRITICAL findings are additionally mirrored into `system_telemetry` (flag `sast_finding`), which is what actually pushes them through adjudication.

### Risk Waiver & Exception Hub

A documented, time-boxed exception to a failing finding — reuses the generic HITL `approval_tasks` workflow (`gate_type='devops_scm_exception'`, zero schema changes) rather than a bespoke approval flow. A manager-approved exception becomes an `observability.risk_waivers` row; `risk_waiver_sweep.py`'s hourly sweep flips overdue waivers to `EXPIRED` and re-ingests the underlying finding — automated expiry re-opens the control as failing, it never silently lapses.

### Pipeline Provenance / Attestation

`POST /evidence/attestation` ingests OIDC identity claims, a SLSA provenance statement (structural 0–3 level estimate via `attestation.validate_slsa_provenance`), an environment-variable hash (`attestation.hash_env_vars` — detects an injected `SKIP_TESTS=true`/`DISABLE_SAST=1` **without ever storing the raw values**), runner metadata, an optional Cosign/Sigstore bundle (`attestation.verify_cosign_bundle` — **structural validation only**; reports `"unknown"` rather than fabricating `"true"`/`"false"` without a real `cosign` binary), and an SBOM (CycloneDX/SPDX, with copyleft-license flagging). Stored in `observability.pipeline_attestations`.

---

## DevOps Monitoring: ITSM/Jira-ServiceNow SLA Bridge

**Files:** `itsm_connectors.py`, `itsm_jira_tool.py` / `itsm_servicenow_tool.py`, `itsm_endpoints.py`, `itsm_sla_sweep.py`

Opens a real Jira/ServiceNow ticket for a finding (`POST /itsm/tickets`, via a registered `itsm_jira`/`itsm_servicenow` poll connector's credentials) and tracks its remediation SLA — independent of the external system, in `observability.itsm_tickets` (`UNIQUE` active-hash index prevents duplicate open tickets for the same finding). SLA hours are severity-based: CRITICAL 48h, HIGH 168h (7d), MEDIUM 240h (10d), LOW 720h (30d).

Two independent concerns, deliberately not conflated:
- **Status reconciliation** (`itsm_jira_tool.py`/`itsm_servicenow_tool.py`'s poll adapter) — did a human close the ticket in Jira/ServiceNow's own UI? Normalizes both systems' vocabularies (Jira workflow names, ServiceNow numeric `state` codes) to `open | in_progress | resolved | closed | cancelled`.
- **SLA breach detection** (`itsm_sla_sweep.py`'s hourly sweep, `db.expire_overdue_sla()`) — is the ticket overdue regardless of what status it's in? A ticket that's never synced still gets its SLA breach detected on schedule. A breach re-ingests the finding tagged `sla_breach`, re-opening it as failing.

Real-time status can also arrive via `POST /itsm/webhook` (Bearer-key authenticated, same per-system key model as the SARIF webhook) — configure a Jira Automation rule or ServiceNow Business Rule to POST on transition instead of waiting for the next poll.

---

## Infrastructure Monitoring: Continuous IaaS/OS/DB Audit

**Files:** `iaas_connectors.py`, `postgres_cis_tool.py`, `railway_iaas_tool.py`, `infrastructure_monitoring_mcp_server.py`

**Scope note:** Railway (and most PaaS hosting) is immutable-container, no-SSH infrastructure — classic OS-level CIS benchmarks (sshd config, auditd, file permissions) are neither auditable nor applicable. This integration covers what actually is: SQL-queryable Postgres configuration, and the Railway platform/deployment metadata exposed via its GraphQL API.

### Postgres CIS-style Hardening (`postgres_cis_tool.py`, connector_type `postgres_cis`)

Connects read-only (`iaas_connectors.fetch_postgres_config` — plain `SELECT`/`SHOW` statements; a `pg_read_all_settings`/`pg_monitor` role is sufficient, superuser is not required) and checks: SSL enforcement (`SHOW ssl`), password hashing scheme (`SHOW password_encryption` — should be `scram-sha-256`, not `md5`), superuser count (`pg_roles`), currently-unencrypted live connections (`pg_stat_ssl` joined to `pg_stat_activity`), and connection/disconnection logging (`SHOW log_connections`).

### Railway Platform/Deployment Drift (`railway_iaas_tool.py`, connector_type `railway_iaas`)

Queries the Railway GraphQL API (`https://backboard.railway.com/graphql/v2`) for every service instance in a registered environment. **A request without a browser-like `User-Agent` header gets a Cloudflare 403 (error code 1010), not an auth failure** — this is load-bearing, not decorative, in `iaas_connectors.fetch_railway_environment`. Two checks:

- **Unexpected public domain exposure** — a service with a `serviceDomains`/`customDomains` entry that isn't in the connector's `approved_public_service_ids` allow-list. A service quietly gaining public exposure should never go unnoticed.
- **Deployment image provenance mismatch** — the running deployment's `imageDigest` doesn't match ANY digest this platform has a pipeline attestation for (`observability.pipeline_attestations.container_image_sha`, from the SARIF/evidence pipeline's `POST /evidence/attestation` above). **Only evaluated once at least one attestation has been ingested** — with zero attestations on record, this reports `None` (unknown), never a fabricated `True`, mirroring the Cosign-verification principle above: never report a negative finding when there's genuinely nothing to compare against.

The API token should be a real Railway Account/Team API token (dashboard → Account Settings → Tokens) — not a CLI OAuth session token, which is short-lived and not meant for long-running automation.

Both adapters register into the same `infrastructure_monitoring` Policy-as-Code process and `INFRASTRUCTURE_FINDING` event type — see [policy.md](policy.md)'s POL-INFRA-001 and the `devops_monitoring_mcp_server.py`-adjacent `infrastructure_monitoring_mcp_server.py` for the MCP tool surface.

---

## Policy-as-Code: Real Rego Evaluation in the Adjudication Pipeline

**File:** `project/agentic-tools/mcp_governance.py` — `_evaluate_pac_policy()`

Silver's own policy checks ([policy.md](policy.md)'s `POL-*` rules, hardcoded Python) are a separate rule set from the actual Rego modules editable on the Policy-as-Code screen. `_evaluate_pac_policy()` closes that gap: every URO is *additionally* checked against the real saved-or-default Rego module for whichever process its `(source_system, event_type)` maps to.

```python
process = _SOURCE_EVENT_TO_PAC_PROCESS.get(
    (source_system, event_type),                                    # fine-grained override, checked first
    _SOURCE_SYSTEM_TO_PAC_PROCESS.get(source_system, _DEFAULT_PAC_PROCESS),  # coarse fallback
)
rego_content = db.get_latest_pac_module(process) or pac_endpoints._REGO_DEFAULTS[process]
input_event = {"event": {
    "type": uro.event_type.value, "resource": ..., "resource_type": ..., "action": ..., "outcome": ...,
    **conformed_payload.risk_indicators,   # flattened — see pipeline.md's Silver conformation table
}}
result = pac_endpoints.evaluate_policy_event(rego_content, input_event)  # real OPA, heuristic fallback
```

The override map exists because GitHub/GitLab/system_telemetry traffic is NOT all one process — e.g. `(GITHUB, BRANCH_PROTECTION_BYPASSED)` routes to `devops_monitoring`, while other GitHub traffic still routes to `itgc`:

```python
_SOURCE_EVENT_TO_PAC_PROCESS = {
    ("GITHUB", "BRANCH_PROTECTION_BYPASSED"):  "devops_monitoring",
    ("GITHUB", "CODE_REVIEW_BYPASSED"):        "devops_monitoring",
    ("GITLAB", "BRANCH_PROTECTION_BYPASSED"):  "devops_monitoring",
    ("GITLAB", "CODE_REVIEW_BYPASSED"):        "devops_monitoring",
    ("SYSTEM_TELEMETRY", "SAST_FINDING"):      "devops_monitoring",
    ("SYSTEM_TELEMETRY", "BRANCH_PROTECTION_BYPASSED"): "devops_monitoring",
    ("SYSTEM_TELEMETRY", "SLA_BREACH"):        "devops_monitoring",
    ("SYSTEM_TELEMETRY", "INFRASTRUCTURE_FINDING"): "infrastructure_monitoring",
}
```

Fired rules are folded into `policy_violations` and appear as a distinct `"Policy-as-Code (Rego)"` voice in `council_votes`.

### Negative Testing — proving a policy actually works, not just that it evaluates

**Files:** `pac_contracts.py`, `pac_negative_tests.py`, `pac_assurance.py`, `pac_negative_sweep.py`

Found during development: a Rego rule that references a field or event-type literal the pipeline above never actually produces evaluates without error and silently never fires — indistinguishable from a policy that found nothing wrong. Three layers catch this:

1. **Schema-contract check** (`pac_contracts.check_module_contract`) — static analysis of a module's Rego source: every `input.event.<field>` reference must appear in `PROCESS_CONTRACTS[process]["allowed_fields"]` (kept honest against the real producers by `test_pac_contracts.py`), and every `input.event.type == "..."` literal must be a real `EventType` value that actually routes to this process per the maps above. Also flags any top-level `input.<root>.*` reference other than `event` — `_evaluate_pac_policy` only ever constructs `{"event": {...}}`, so any other root is unreachable by the automated pipeline (this is exactly how the original 5 built-in ERP process modules were found to be dead-by-construction — they read as real SAP/ERP policy but no producer has ever fed them `input.journal.*`/`input.invoice.*`/etc.).
2. **Must-fire / must-not-fire corpus** (`pac_negative_tests.run_corpus`) — curated fixtures per process asserting a specific known-bad input makes the expected `control_id` fire, and a known-good input stays silent. Run through the real `evaluate_policy_event` (authoritative OPA when available, labelled heuristic fallback otherwise).
3. **Assurance metadata + periodic full evaluation** (`pac_assurance.evaluate_and_record`, `pac_negative_sweep.py`'s hourly sweep) — persists every test run as audit evidence (`observability.pac_test_runs`) and updates `controls_catalog.last_verified_at`/`last_test_passed`/`last_fired_at` per control, so `db.list_unverified_controls()` can answer "which policy-enforced controls does nothing currently prove are working" — not just "how many controls do we have."

The negative-testing gate runs (and records evidence) on module approval (`POST /pac/modules/{process}/approve`) and on the hourly sweep, advisory rather than blocking today — see `pac_endpoints.py`'s `approve_module` docstring for why.

---

## Database Schema v2

**File:** `project/agentic-tools/telemetry_schema.sql`

The original `adjudicated_tool_calls` table assumed all adjudicated events were MCP proxy events with a corresponding `mcp_telemetry` row. GitHub webhook events have no telemetry row, so two schema changes were needed.

### Changes

```sql
-- 1. Make telemetry_id nullable
--    NULL for GITHUB events; references mcp_telemetry for MCP_PROXY events
ALTER TABLE observability.adjudicated_tool_calls
    ALTER COLUMN telemetry_id DROP NOT NULL;

-- 2. Add source_system column to distinguish event origin
ALTER TABLE observability.adjudicated_tool_calls
    ADD COLUMN IF NOT EXISTS source_system VARCHAR(32) NOT NULL DEFAULT 'MCP_PROXY';

-- 3. Index for dashboard source filters
CREATE INDEX IF NOT EXISTS idx_adj_source
    ON observability.adjudicated_tool_calls (source_system, adjudicated_at DESC);
```

### `source_system` Values

| Value | Description |
|---|---|
| `MCP_PROXY` | Event originated from `mcp_telemetry_proxy.py` via the telemetry proxy |
| `GITHUB` | Event originated from the GitHub webhook listener |

All three statements are idempotent (`IF NOT EXISTS`, `DROP NOT NULL` is a no-op if already nullable) and safe to re-run.

### `human_review_queue` View

The view joins `adjudicated_tool_calls` to `mcp_telemetry` via `telemetry_id`. After the migration, this join becomes a `LEFT JOIN` because GitHub rows have `telemetry_id = NULL`. The existing view definition was not changed — the `JOIN` in the original view filters to `requires_human_review = TRUE` rows, so GitHub events that require review will appear in the queue with `telemetry_ts = NULL` and `execution_time_ms = NULL`.

> **Note:** If you want the human review queue to include GitHub events, replace the `JOIN` with a `LEFT JOIN` in the view definition. The current schema migration does not make this change.

---

## Controls Monitor: UBO Governance Panel

**Component:** `UBOGovPanel` in `project/cem.jsx`
**Screen:** Controls Monitor (the same screen as the CEM panel)

The panel polls the observability API every 30 seconds and displays all adjudicated events, the human review queue, and per-tool latency statistics.

### API Endpoints Consumed

| Endpoint | Used for |
|---|---|
| `GET /observability/telemetry/adjudicated?limit=100` | Adjudicated event log |
| `GET /observability/telemetry/human-review` | Human review queue |
| `GET /observability/telemetry/summary` | Latency stats per tool |
| `POST /observability/telemetry/process` | Trigger a manual governance run |

All requests go to `window.MCP_API_BASE || 'http://127.0.0.1:8001'`.

### Stats Ticker (top bar)

| Stat | Source |
|---|---|
| Total Adjudicated | `adjudicated.length` |
| Critical | events where `risk_tier === "CRITICAL"` |
| Human Review | `humanReview.length` |
| GitHub Events | events where `source_system === "GITHUB"` |
| Last Refresh | formatted timestamp |

### Filter Tabs

| Tab | Filter Logic |
|---|---|
| All | No filter |
| Critical | `risk_tier === "CRITICAL"` |
| High | `risk_tier === "HIGH"` |
| Medium | `risk_tier === "MEDIUM"` |
| Low | `risk_tier === "LOW"` |
| Needs Review | `requires_human_review === true` |
| GitHub | `source_system === "GITHUB"` |
| MCP | `source_system === "MCP_PROXY"` (or source_system absent) |

### Adjudication Log Rows

Each `UBOAdjRow` shows:
- **Risk tier badge** — colour-coded: CRITICAL=red, HIGH=amber, MEDIUM=blue, LOW=green
- **Verdict badge** — ESCALATE=red, MONITOR=amber, CLEAR=green
- **GH badge** — blue, shown only when `source_system === "GITHUB"`
- **Tool name** / event type, server name / repo name
- **Human review flag** — red `REVIEW` chip when `requires_human_review`
- **Risk score** and **timestamp**

Expanding a row reveals:
- Session ID, confidence, risk flags, conflict flags (meta grid)
- Policy violations list (amber-bordered items)
- Adjudicator reasoning (full text in rca-box)

### Human Review Queue

Shown above the filter toolbar when `humanReview.length > 0`. Each `UBOReviewRow` shows tier badge, tool/event name, server/repo, and risk score. The section has a red heading and background tint to make it visually prominent.

### Latency Table

Shown below the adjudication log. Columns: Server · Tool · Calls · Avg ms · P50 · P95 · P99 · Errors · Err%. P95 and P99 values above 30,000 ms are highlighted red (`ubo-lat-breach`).

### Manual Process Trigger

The `PROCESS NOW` button in the header posts to `/observability/telemetry/process` and then refreshes all three data sources. This triggers the MCP governance poller to pick up any unprocessed flagged rows from `mcp_telemetry` — it does not re-process GitHub events (those are processed inline at webhook receipt time).
