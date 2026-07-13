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
