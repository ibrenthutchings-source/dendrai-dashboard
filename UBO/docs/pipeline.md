# Medallion Pipeline

The pipeline implements the Medallion Architecture (Bronze → Silver → Gold) as a three-stage async processing chain. Every URO passes through all three stages before entering the Council of Agents.

**Design invariants:**
- UROs are **immutable** — each stage returns a new copy via `model_copy(update={...})`, never mutating in place.
- The **raw payload is sacred** — `RawPayload.content` is written at Bronze and never touched again. Downstream layers read from it but cannot overwrite it.
- **Non-blocking** — every public method is `async`. Batch methods use `asyncio.gather()`.
- **Non-dropping** — a URO with policy violations is still promoted to Silver and Gold. Violations attach as signals for the agents to weigh, not hard stops.

---

## Bronze Layer — `pipeline/bronze.py`

**Responsibility:** Accept a raw source event dict, map it to a URO, lock in the raw payload with checksum, set `pipeline_stage = BRONZE`. No cleaning, no transformation, no enrichment.

### `BronzeIngestionLayer`

The public entry point. Routes events to the correct per-source handler.

```python
layer = BronzeIngestionLayer()
uro = await layer.ingest(raw_event, SourceSystem.SAP)
uro = await layer.ingest(raw_event, SourceSystem.SAP, correlation_id="INC-2025-001")

# Batch
uros = await layer.ingest_batch([event1, event2], SourceSystem.GITHUB)
```

If no handler is registered for a source system, `_generic_ingest()` is called as fallback, preserving the raw payload with `EventType.ANOMALY`.

### Per-Source Handlers

Each handler implements `BronzeLayerBase.ingest(raw_event: dict) → URO`.

#### `SAPBronzeHandler`

Maps SAP audit log entries (CDHDR / CDPOS schema).

| Raw Field | Mapped To |
|---|---|
| `UZEIT` / `timestamp` | `URO.timestamp` |
| `TCODE` / `event_code` | `URO.event_type` (via ACTION_MAP) |
| `UNAME` / `actor_id` | `URO.actor_id` |
| `sap_landscape` | `environment.tags["landscape"]` |
| `sap_client` | `environment.account_id` |

TCODE → EventType:
| TCODE | EventType |
|---|---|
| `VENDOR_CHANGE` | `VENDOR_MASTER_CHANGE` |
| `JRNL_ANOMALY` | `JOURNAL_ENTRY_ANOMALY` |
| `SOD_VIOLATION` | `SOD_VIOLATION` |
| `PERIOD_OVERRIDE` | `PERIOD_CLOSE_OVERRIDE` |
| `PAY_THRESHOLD` | `PAYMENT_THRESHOLD_BREACH` |

Schema version: `"SAP-CDHDR-v1"`

#### `GitHubBronzeHandler`

Maps GitHub webhook payloads.

| Raw Field | Mapped To |
|---|---|
| `created_at` / `pushed_at` | `URO.timestamp` |
| `X-GitHub-Event` / `event_type` | `URO.event_type` (via ACTION_MAP) |
| `sender.login` / `pusher.name` | `URO.actor_id` |
| `sender.login.endswith("[bot]")` | `actor_type = SERVICE` |
| `repository.id` | `environment.account_id` |
| `organization.login`, `repo.full_name` | `environment.tags` |

Event → EventType:
| Webhook event | EventType |
|---|---|
| `secret_scanning_alert` | `SECRET_DETECTED` |
| `branch_protection_rule` | `BRANCH_PROTECTION_BYPASSED` |
| `push` | `FORCE_PUSH_MAIN` |
| `dependabot_alert` | `DEPENDENCY_VULNERABILITY` |
| `pull_request_review` | `CODE_REVIEW_BYPASSED` |

Schema version: `"GitHub-Webhook-v3"`

#### `GitLabBronzeHandler`

DevOps Monitoring — mirrors `GitHubBronzeHandler` almost line for line (branch-protection + CODEOWNERS fields instead of push/PR fields). Fed by real GitLab webhooks and by `scm_audit_endpoints.py` / `gitlab_scm_tool.py`'s synthesized `protected_branch_audit` events, both carrying the same normalized `compliance` sub-dict shape as the GitHub path (`scm_connectors.normalize_gitlab_compliance`).

| Raw Field | Mapped To |
|---|---|
| `created_at` | `URO.timestamp` |
| `X-Gitlab-Event` / `event_type` | `URO.event_type` (via ACTION_MAP) |
| `user.username` | `URO.actor_id` |
| `project.id` | `environment.account_id` |
| `project.path_with_namespace` | `environment.tags` |

Event → EventType:
| Event | EventType |
|---|---|
| `protected_branch_audit` | `BRANCH_PROTECTION_BYPASSED` |
| `merge_request` | `CODE_REVIEW_BYPASSED` |
| `push` | `FORCE_PUSH_MAIN` |
| `vulnerability` | `DEPENDENCY_VULNERABILITY` |

Schema version: `"GitLab-Webhook-v4"`

#### `SailPointBronzeHandler`

Maps SailPoint IdentityNow activity stream events.

| Raw Field | Mapped To |
|---|---|
| `created` / `timestamp` | `URO.timestamp` |
| `action` / `type` | `URO.event_type` (via ACTION_MAP) |
| `requestedFor.id` / `actor` | `URO.actor_id` |
| `org` | `environment.tenant_id` |
| `pod` | `environment.tags["pod"]` |

Action → EventType:
| Action | EventType |
|---|---|
| `ROLE_ADDED` | `PRIVILEGE_ESCALATION` |
| `ACCESS_REQUEST_DENIED` | `ACCESS_CERTIFICATION_FAIL` |
| `ACCOUNT_ORPHANED` | `ORPHANED_ACCOUNT` |
| `DORMANT_PRIV_ACCOUNT` | `DORMANT_PRIVILEGED_ACCOUNT` |
| `ROLE_EXPLOSION` | `ROLE_EXPLOSION` |

Schema version: `"SailPoint-IDN-v3"`

#### `McpProxyBronzeHandler`

Maps flagged rows from `observability.mcp_telemetry`. Each row is a JSON-RPC 2.0 message that the telemetry proxy tagged with at least one Risk-as-Code governance flag.

| Raw Field | Mapped To |
|---|---|
| `ts` / `timestamp` | `URO.timestamp` |
| `risk_flags[]` | `URO.event_type` (via FLAG_EVENT_MAP) |
| `session_id` | `URO.actor_id` |
| `server_name` | `environment.tags["server_name"]` |
| Always | `actor_type = SERVICE` |

Flag → EventType (priority order):
| Flag | EventType |
|---|---|
| `bypass_keyword` | `MCP_TOOL_BYPASS` |
| `sensitive_tool` | `MCP_SENSITIVE_TOOL_CALL` |
| `bulk_args` | `MCP_BULK_ARGS` |
| `large_payload` | `MCP_LARGE_PAYLOAD` |
| 3+ flags simultaneously | `MCP_GOVERNANCE_VIOLATION` |
| `status == "error"` | `MCP_TOOL_ERROR` |

Schema version: `"MCP-Telemetry-v1"`

#### `SystemTelemetryBronzeHandler`

Generic enterprise-system handler: every poll-connector adapter (Oracle Fusion, SAP HANA, SailPoint, Dynamics 365, NetSuite) and every DevOps Monitoring / Infrastructure Monitoring adapter (SCM audits, SARIF evidence, ITSM SLA breaches, Postgres CIS checks, Railway platform drift) ultimately lands here via `observability.system_telemetry`, distinguished only by which risk flags each producer sets — see `mcp_governance._detect_system_flags()`.

| Raw Field | Mapped To |
|---|---|
| `created_at` / `timestamp` | `URO.timestamp` |
| `risk_flags[]` | `URO.event_type` (via FLAG_EVENT_MAP) |
| `actor` | `URO.actor_id` |
| `system_type` | `environment.provider` |
| `server_name`, `event_type`, `severity` | `environment.tags` |

Flag → EventType:
| Flag | EventType | Set by |
|---|---|---|
| `sod_violation` | `SOD_VIOLATION` | Generic keyword/heuristic detection |
| `privileged_access` | `PRIVILEGE_ESCALATION` | Generic keyword/heuristic detection |
| `sensitive_resource` | `SENSITIVE_RESOURCE_ACCESS` | Generic keyword/heuristic detection |
| `policy_violation` | `POLICY_VIOLATION` | `severity == CRITICAL` or explicit payload flag |
| `branch_protection_violation` | `BRANCH_PROTECTION_BYPASSED` | `github_scm_tool.py` / `gitlab_scm_tool.py` (explicit — not inferred) |
| `sast_finding` | `SAST_FINDING` | `evidence_endpoints.py` SARIF ingestion (explicit) |
| `sla_breach` | `SLA_BREACH` | `itsm_sla_sweep.py`'s hourly breach sweep (explicit) |
| `infrastructure_finding` | `INFRASTRUCTURE_FINDING` | `postgres_cis_tool.py` / `railway_iaas_tool.py` / `connector_hygiene_sweep.py` (explicit) |
| `pipeline_misconfiguration` | `PIPELINE_MISCONFIGURATION` | `github_scm_tool.py`'s scheduled poll (explicit — a second event per tick, alongside `branch_protection_violation`) |

The last five flags are set directly by their producers rather than inferred from generic keyword matching — those producers know exactly which event they're emitting, so there's no ambiguity to heuristically resolve.

On the GITHUB source-system path (not SYSTEM_TELEMETRY), `bronze.py`'s `GitHubBronzeHandler._ACTION_MAP` separately maps `scm_audit_endpoints.py`'s synthesized on-demand event names directly to `EventType`: `"workflow_security_audit"` → `PIPELINE_MISCONFIGURATION`, `"gitleaks_scan"` → `SECRET_DETECTED` (only ever sent when a real gitleaks scan actually found something — a clean scan is never adjudicated).

Schema version: `"System-Telemetry-v1"`

---

## Silver Layer — `pipeline/silver.py`

**Responsibility:** Parse `raw_payload.content` into a structured `ConformedPayload`, then run the full Policy-as-Code rule engine against the URO. Returns a Silver-stage URO with violations attached.

```python
layer = SilverConformationLayer()
silver_uro = await layer.conform(bronze_uro)
# silver_uro.conformed_payload is now populated
# silver_uro.silver_policy_violations contains any violation strings
```

### Conformation

Routes to a source-specific conformer based on `uro.source_system`. Each conformer extracts a `ConformedPayload` using the common vocabulary:

| Source | resource_type | Key risk_indicators |
|---|---|---|
| SAP | `"SAP_OBJECT"` | amount, currency, cost_center, approver, actor_groups |
| GitHub | `"git_repository"` | ref, forced, cvss_score, secret_type, commits_count, is_admin, secret_finding_count, secret_rule_ids (gitleaks), + spread `compliance` sub-dict (enforce_admins, required_approving_review_count, … / pipeline-security fields for `PIPELINE_MISCONFIGURATION` events — has_write_all_permissions, unpinned_action_count, has_risky_pull_request_target, …) |
| GitLab | `"git_repository"` | ref, commits_count, + spread `compliance` sub-dict (same shape as GitHub's) |
| SailPoint | `"identity"` | role_count, last_login_days, access_request_id, entitlements |
| MCP Proxy | `"mcp_tool"` | risk_flags, flag_count, execution_time_ms, error_message, narrative |
| System Telemetry | `"enterprise_system_resource"` | risk_flags, flag_count, severity, rule_id, cwe (SARIF), external_system/external_ticket_key/finding_hash/sla_due_at (ITSM), + spread `compliance`/`infra_compliance`/`pipeline_compliance` sub-dict (SCM branch-protection / Postgres CIS + Railway drift + connector credential hygiene / pipeline-as-code security) |
| Generic | `"unknown"` | All non-reserved fields passed through |

`compliance`/`infra_compliance` are spread into `risk_indicators` (not nested) specifically so `mcp_governance._evaluate_pac_policy`'s real Rego evaluation — which flattens `conformed_payload.risk_indicators` straight into `input.event.*` — sees the exact same field names regardless of whether the event came from a live webhook or a scheduled poll-connector audit. See [integrations.md](integrations.md) for that flattening and `pac_contracts.py`'s per-process schema-contract declarations, which is what caught this shape actually needing to match in the first place.

The `narrative` field in the MCP conformer is set to a plain-English description such as:
> `"MCP tool 'edgar_company_info' on server 'edgar' flagged: bypass_keyword, large_payload"`

This is what The Linguist reads during Council evaluation.

### Policy-as-Code Rule Engine

`validate(uro)` iterates through every rule in `POLICY_REGISTRY` and calls `_check_rule(rule, uro)`. Rules return a violation string (non-empty) or `None` (pass). All violations are collected and attached to the URO as `silver_policy_violations`.

The engine is **non-dropping**: a URO with 10 violations still proceeds to Gold. Violations increase the Gold score via the `_POLICY_SEVERITY_WEIGHTS` multipliers (+0.20 per CRITICAL violation, +0.12 per HIGH, etc.).

See [policy.md](policy.md) for the full rule catalogue.

---

## Gold Layer — `pipeline/gold.py`

**Responsibility:** Compute a composite risk score (0.0–1.0), assign a tier, and generate executive `RiskIntelligenceReport` aggregates.

### Per-URO Scoring

```python
layer = GoldAggregationLayer()
gold_uro = await layer.score(silver_uro)
# gold_uro.risk_score: float
# gold_uro.risk_tier:  "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
```

**Algorithm:**

```
base      = _EVENT_BASE_WEIGHTS[event_type]          # see EventType table in models.md
policy    = Σ _POLICY_SEVERITY_WEIGHTS[severity]     # per violation
actor     = 0.08 if actor_type == SERVICE else 0.0
cascade   = _cascade_correlation(uro)                # 0.0–0.20

raw_score = base + policy + actor + cascade
score     = clamp(raw_score, 0.0, 1.0)
```

**Policy severity weights:**

| Severity | Per-violation weight |
|---|---|
| CRITICAL | +0.20 |
| HIGH | +0.12 |
| MEDIUM | +0.06 |
| LOW | +0.02 |

**Cascade correlation:** Scans the observation window for UROs sharing the same `correlation_id`. Each related URO adds +0.05, capped at +0.20.

**Tier thresholds:**

| Tier | Minimum Score |
|---|---|
| CRITICAL | 0.85 |
| HIGH | 0.65 |
| MEDIUM | 0.40 |
| LOW | 0.0 |

### Aggregate Reporting

```python
from datetime import datetime, timedelta, timezone

window_end   = datetime.now(tz=timezone.utc)
window_start = window_end - timedelta(hours=24)
report = await layer.aggregate(adjudicated_uros, window_start, window_end)
```

`aggregate()` builds a `RiskIntelligenceReport` covering:

1. **Volume** — event counts per tier
2. **Enterprise score** — mean risk score across all UROs
3. **Cascading failure probability** — Bayesian model:
   ```
   P(cascade) = (multi_system_clusters / total_clusters) × (0.5 + mean_score)
   ```
   A "multi-system cluster" is a correlation group spanning ≥ 2 different source systems.
4. **Cascade map** — blast-radius tree rooted at the highest-scoring URO, with up to 5 depth-1 children
5. **Blast radius per node** — estimated from event type base × role_count multiplier:
   - `SECRET_DETECTED`: 500 base entities
   - `ROLE_EXPLOSION` / `PRIVILEGE_ESCALATION`: role_count × 5 entities
6. **Human review queue** — URO IDs where `adjudication.requires_human_review == True`

### `GoldAggregationLayer` — Observation Window

The layer maintains an internal `_window: list[URO]` that accumulates every scored URO. This window is shared with `TheGraphArchitect` (passed at `CouncilOrchestrator` construction time) to enable cross-event cascade detection without a database query.

```python
# Pass a shared window to both Gold and the Graph Architect
window = []
gold    = GoldAggregationLayer(correlation_window_uros=window)
council = CouncilOrchestrator(observation_window=window)
```

---

## `pipeline/base.py` — Abstract Interfaces

Defines the abstract base classes that the concrete layers implement. All public methods are typed and documented here.

### `PolicyRule` (frozen dataclass)

```python
@dataclass(frozen=True)
class PolicyRule:
    rule_id:     str         # e.g. "POL-SAP-001"
    name:        str
    description: str
    severity:    str         # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    applies_to:  list[str]   # SourceSystem values; empty = applies to all
```

`applies(source: SourceSystem) → bool` returns `True` if this rule should run for the given source.

### `BronzeLayerBase(ABC)`

- `ingest(raw_event: dict) → URO` — must be implemented
- `ingest_batch(events: list[dict]) → list[URO]` — default uses `asyncio.gather`

### `SilverLayerBase(ABC)`

- `conform(uro: URO) → URO` — must be implemented
- `validate(uro: URO) → list[str]` — iterates POLICY_REGISTRY, calls `_check_rule` for each
- `_check_rule(rule: PolicyRule, uro: URO) → str | None` — must be implemented
- `conform_batch(uros: list[URO]) → list[URO]` — default uses `asyncio.gather`

### `GoldLayerBase(ABC)`

- `score(uro: URO) → URO` — must be implemented
- `aggregate(uros, window_start, window_end) → RiskIntelligenceReport` — must be implemented
- `_assign_tier(score: float) → str` — provided; uses `TIER_THRESHOLDS`
- `score_batch(uros: list[URO]) → list[URO]` — default uses `asyncio.gather`

```python
TIER_THRESHOLDS = {
    "CRITICAL": 0.85,
    "HIGH":     0.65,
    "MEDIUM":   0.40,
    "LOW":      0.0,
}
```
