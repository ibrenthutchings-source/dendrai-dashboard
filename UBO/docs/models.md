# Data Models

The two model files define every type used throughout the Dendrai UBO Governance Brain. No other layer imports from outside this package — data flows only as UROs.

---

## `models/uro.py` — Universal Risk Object

### Enumerations

#### `SourceSystem`

The originating system that produced the raw event.

| Value | Description |
|---|---|
| `SAP` | SAP ERP audit log (CDHDR / CDPOS schema) |
| `GITHUB` | GitHub webhook payloads |
| `SAILPOINT` | SailPoint IdentityNow activity stream |
| `FRED` | Federal Reserve macro economic indicators |
| `SEC_EDGAR` | SEC EDGAR filings |
| `ORACLE_FUSION` | Oracle Fusion ERP |
| `MCP_PROXY` | MCP Telemetry Proxy flagged tool calls |
| `INTERNAL` | Internal risk register or manual entry |
| `UNKNOWN` | Fallback for unrecognised sources |

#### `EventType`

The specific risk event class. Each value maps to a base severity weight in the Gold layer.

**SAP Financial Controls**

| Value | Base Weight | Description |
|---|---|---|
| `SOD_VIOLATION` | 0.80 | Segregation-of-duties conflict detected |
| `JOURNAL_ENTRY_ANOMALY` | 0.65 | Anomalous journal posting (weekend, no authorisation) |
| `VENDOR_MASTER_CHANGE` | 0.60 | Vendor record modified |
| `PAYMENT_THRESHOLD_BREACH` | 0.75 | Payment exceeds approved limit |
| `PERIOD_CLOSE_OVERRIDE` | 0.85 | Accounting period closed by unauthorised actor |

**GitHub DevSecOps**

| Value | Base Weight | Description |
|---|---|---|
| `SECRET_DETECTED` | 0.95 | Credential or secret committed to repo |
| `BRANCH_PROTECTION_BYPASSED` | 0.70 | Branch protection rule circumvented |
| `FORCE_PUSH_MAIN` | 0.65 | Force push to protected branch |
| `DEPENDENCY_VULNERABILITY` | 0.50 | Dependabot CVE alert |
| `CODE_REVIEW_BYPASSED` | 0.55 | Pull request merged without required review |

**SailPoint Identity**

| Value | Base Weight | Description |
|---|---|---|
| `PRIVILEGE_ESCALATION` | 0.80 | Role granted without approved request |
| `ORPHANED_ACCOUNT` | 0.45 | Account exists with no owner |
| `ACCESS_CERTIFICATION_FAIL` | 0.55 | Access certification campaign failed |
| `DORMANT_PRIVILEGED_ACCOUNT` | 0.60 | Privileged account inactive ≥ 30 days |
| `ROLE_EXPLOSION` | 0.75 | Identity holds > 25 roles (SoD limit) |

**Macro / Market Signals**

| Value | Base Weight | Description |
|---|---|---|
| `MACRO_LEADING_INDICATOR` | 0.40 | FRED macro signal in risk zone |
| `EDGAR_FILING_ANOMALY` | 0.65 | SEC filing anomaly detected |
| `BENEISH_THRESHOLD_BREACH` | 0.70 | Beneish M-Score indicates likely manipulation |

**Cross-System**

| Value | Base Weight | Description |
|---|---|---|
| `CASCADING_FAILURE_SIGNAL` | 0.90 | Failure propagating across systems |
| `POLICY_VIOLATION` | 0.60 | Generic policy rule breach |
| `ANOMALY` | 0.35 | General anomaly, source-agnostic |

**MCP Proxy Governance**

| Value | Base Weight | Description |
|---|---|---|
| `MCP_GOVERNANCE_VIOLATION` | 0.90 | 3+ risk flags on a single tool call |
| `MCP_TOOL_BYPASS` | 0.85 | Bypass keyword in tool call payload |
| `MCP_SENSITIVE_TOOL_CALL` | 0.70 | Call to destructive / high-risk tool |
| `MCP_BULK_ARGS` | 0.45 | Tool called with > 20 arguments |
| `MCP_TOOL_ERROR` | 0.40 | Tool returned error status |
| `MCP_LARGE_PAYLOAD` | 0.35 | Payload size > 50 KB |

#### `PipelineStage`

Tracks exactly where in the Medallion architecture a URO currently lives.

| Value | Set by |
|---|---|
| `BRONZE` | `BronzeIngestionLayer` on creation |
| `SILVER` | `SilverConformationLayer.conform()` |
| `GOLD` | `GoldAggregationLayer.score()` |
| `ADJUDICATED` | `CouncilOrchestrator.evaluate()` |

#### `ActorType`

| Value | Meaning |
|---|---|
| `HUMAN` | Individual user account |
| `SERVICE` | Automated service account or bot (carries +0.08 Gold penalty) |
| `SYSTEM` | Internal system process |
| `UNKNOWN` | Cannot be determined |

---

### Supporting Models

#### `CloudEnvironment`

Multi-cloud context tags stamped at Bronze ingestion. Frozen (immutable).

| Field | Type | Description |
|---|---|---|
| `provider` | str | `"AWS"`, `"Azure"`, `"GCP"`, `"On-Prem"`, `"GitHub"`, `"MCP"`, etc. |
| `region` | str? | Cloud region or data-centre zone |
| `account_id` | str? | Cloud account, subscription, or SAP client ID |
| `tenant_id` | str? | Azure AD tenant, GCP org, AWS org unit, SailPoint pod |
| `vpc_id` | str? | VPC or network segment identifier |
| `tags` | dict | Arbitrary key-value pairs (landscape, repo, server_name, etc.) |

#### `RawPayload`

Immutable capture of the verbatim source event. Written once at Bronze; never modified.

| Field | Type | Description |
|---|---|---|
| `content` | dict | Verbatim JSON/dict from the source system |
| `encoding` | str | Character encoding (default `"utf-8"`) |
| `schema_version` | str? | Source schema identifier (e.g. `"SAP-CDHDR-v1"`) |
| `checksum` | str? | SHA-256 of `content` — computed automatically on creation |

The checksum is used by `POL-CORE-004` to verify payload integrity has not been tampered with downstream.

#### `ConformedPayload`

Source-agnostic normalised view. Populated by the Silver layer. Frozen.

| Field | Type | Description |
|---|---|---|
| `resource_id` | str? | What was acted upon (account ID, repo name, GL account, tool name) |
| `resource_type` | str? | Category: `"identity"`, `"git_repository"`, `"SAP_OBJECT"`, `"mcp_tool"`, etc. |
| `action` | str? | Verb describing what happened: `"escalated"`, `"bypassed"`, `"modified"` |
| `outcome` | str? | `"success"`, `"failure"`, `"blocked"`, `"unknown"` |
| `risk_indicators` | dict | Free-form extracted signals (amounts, counts, flags, narrative text) |
| `affected_entities` | list[str] | All entity IDs involved (actor, resource, approver) |
| `conformed_at` | datetime | When conformation was applied |
| `conformation_rules_applied` | list[str] | Which conformation rule sets ran |
| `data_quality_flags` | list[str] | Data quality issues noted during conformation |

---

### Universal Risk Object (URO)

The atomic unit of analysis. Every event entering the system becomes a URO at Bronze and stays a URO throughout its entire lifecycle.

```python
class URO(BaseModel):
    # Identity
    id:             str          # UUID generated at Bronze
    correlation_id: str | None   # Groups causally related cross-system events
    parent_id:      str | None   # For derived / child events

    # Temporal
    timestamp:   datetime        # Event time at source
    ingested_at: datetime        # When the proxy received it

    # Provenance
    source_system: SourceSystem
    event_type:    EventType
    actor_id:      str           # Who/what triggered the event
    actor_type:    ActorType

    # Environment
    environment: CloudEnvironment

    # Dual-container payload
    raw_payload:       RawPayload         # Immutable — Bronze only
    conformed_payload: ConformedPayload   # Added by Silver

    # Risk scoring (Gold)
    risk_score:               float | None   # 0.0–1.0
    risk_tier:                str | None     # CRITICAL / HIGH / MEDIUM / LOW
    silver_policy_violations: list[str]      # Violation strings from Silver

    # Adjudication (Council)
    adjudication: AdjudicationResult | None

    # Pipeline tracking
    pipeline_stage:   PipelineStage
    pipeline_version: str
```

**Transition methods** — each returns a new frozen copy, never mutating in place:

| Method | Returns | Sets |
|---|---|---|
| `as_bronze()` | URO | `pipeline_stage=BRONZE` |
| `as_silver(conformed, violations)` | URO | `pipeline_stage=SILVER`, `conformed_payload`, `silver_policy_violations` |
| `as_gold(score, tier)` | URO | `pipeline_stage=GOLD`, `risk_score`, `risk_tier` |
| `as_adjudicated(adjudication)` | URO | `pipeline_stage=ADJUDICATED`, `adjudication` |

**Convenience properties:**

| Property | Returns |
|---|---|
| `is_high_severity` | `True` if `risk_tier` is `"CRITICAL"` or `"HIGH"` |
| `payload_summary` | Dict with `resource`, `action`, `indicators` — for logging |

---

## `models/risk_intelligence.py` — Agent and Reporting Types

### `RiskTier`

| Value | Score Range |
|---|---|
| `CRITICAL` | ≥ 0.85 |
| `HIGH` | ≥ 0.65 |
| `MEDIUM` | ≥ 0.40 |
| `LOW` | < 0.40 |

### `AgentVerdict`

| Value | Meaning | Vote Weight |
|---|---|---|
| `ESCALATE` | Elevate for immediate human review | +1.0 |
| `MONITOR` | Flag for ongoing observation | 0.0 |
| `CLEAR` | No elevated risk detected | −1.0 |
| `INSUFFICIENT_DATA` | Not enough signal to evaluate | 0.0 |

### `ConflictFlag`

Raised by the Adjudicator when the agent swarm disagrees.

| Value | Trigger |
|---|---|
| `AGENT_DIVERGENCE` | vote_spread (max − min) > 1.20 |
| `LOW_CONFIDENCE` | mean agent confidence < 0.45 |
| `MISSING_EVALUATIONS` | ≥ 2 agents return INSUFFICIENT_DATA |
| `ANOMALOUS_RISK_DELTA` | Weighted risk delta > ±0.25 |

### `AgentEvaluation`

Output of a single agent's `evaluate(uro)` call.

| Field | Type | Description |
|---|---|---|
| `agent_name` | str | Display name of the agent |
| `verdict` | AgentVerdict | Agent's verdict on this URO |
| `confidence` | float | 0.0–1.0; scales the vote weight |
| `risk_delta` | float | Signed adjustment to Gold score (e.g. +0.12 or −0.05) |
| `reasoning` | str | Human-readable explanation string |
| `evidence` | dict | Key-value diagnostic evidence (amounts, flag lists, hit counts) |
| `evaluation_ms` | int | Wall-clock time taken |
| `evaluated_at` | datetime | When the evaluation completed |

**Property:** `weighted_vote` = `verdict_weight × confidence` ∈ [−1, +1]

### `AdjudicationResult`

Final output of the Council of Agents for one URO.

| Field | Type | Description |
|---|---|---|
| `uro_id` | str | ID of the adjudicated URO |
| `final_verdict` | AgentVerdict | ESCALATE / MONITOR / CLEAR |
| `adjusted_risk_score` | float | Gold score ± weighted agent deltas, clamped 0–1 |
| `adjusted_risk_tier` | RiskTier | Tier derived from adjusted_risk_score |
| `evaluations` | list[AgentEvaluation] | All three agent evaluations |
| `ensemble_confidence` | float | Mean agent confidence |
| `requires_human_review` | bool | True if any ConflictFlag was raised |
| `conflict_flags` | list[ConflictFlag] | Active conflict flags |
| `conflict_reasoning` | str? | Plain-English description of conflicts |
| `adjudicated_at` | datetime | Timestamp |
| `adjudicator_version` | str | Version string for audit trail |

### `CascadeNode`

One node in the blast-radius cascade tree.

| Field | Type | Description |
|---|---|---|
| `system` | str | Source system name |
| `resource_id` | str | Resource at risk |
| `failure_prob` | float | Estimated failure probability at this node |
| `blast_radius` | int | Number of downstream entities affected |
| `depth` | int | Depth in the cascade tree |
| `children` | list[CascadeNode] | Downstream propagation nodes |

### `RiskIntelligenceReport`

Executive dashboard report generated by `GoldAggregationLayer.aggregate()`.

| Field | Type | Description |
|---|---|---|
| `report_id` | str | UUID |
| `generated_at` | datetime | Report generation time |
| `window_start/end` | datetime | Time window covered |
| `total_events` | int | Total UROs in window |
| `critical_count` | int | UROs at CRITICAL tier |
| `high_count` | int | UROs at HIGH tier |
| `medium_count` | int | UROs at MEDIUM tier |
| `low_count` | int | UROs at LOW tier |
| `enterprise_risk_score` | float | Mean score across all UROs |
| `cascading_failure_probability` | float | Bayesian P(cascade) |
| `risk_by_source` | dict | Mean score per SourceSystem |
| `risk_by_type` | dict | Mean score per EventType |
| `top_risks` | list[dict] | Top 10 highest-scoring UROs (summary dicts) |
| `cascade_map` | CascadeNode? | Blast-radius tree from highest-risk URO |
| `human_review_queue` | list[str] | URO IDs requiring human action |
