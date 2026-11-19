# Policy-as-Code Rule Registry

All governance rules live in `policy/rules.py` as declarative data. The Silver layer's `_check_rule()` dispatcher evaluates them — **adding a new governance requirement means adding a rule here, with no pipeline code changes required.**

---

## How Rules Work

Rules are `PolicyRule` frozen dataclasses:

```python
PolicyRule(
    rule_id="POL-SAP-002",
    name="Vendor Master Change Approver Presence",
    description="...",
    severity="HIGH",
    applies_to=["SAP"],    # empty list = applies to all source systems
)
```

At Silver stage, `SilverConformationLayer.validate(uro)` iterates the full `POLICY_REGISTRY` and calls `_check_rule(rule, uro)` for each:
- Returns `None` → rule passes, no violation recorded
- Returns a string → violation message attached to `uro.silver_policy_violations`

**UROs are never dropped due to policy violations.** Violations increase the Gold score via severity weights (+0.20 per CRITICAL violation, +0.12 HIGH, +0.06 MEDIUM, +0.02 LOW).

---

## Naming Convention

```
POL-{DOMAIN}-{SEQUENCE}

POL-CORE   cross-system baseline rules (all source systems)
POL-SAP    SAP financial controls
POL-GH     GitHub DevSecOps
POL-GL     GitLab DevSecOps
POL-DEVOPS DevOps Monitoring — SCM audits, SARIF evidence, ITSM SLA breaches, pipeline-as-code security
POL-INFRA  Infrastructure Monitoring — IaaS/OS/DB continuous audit
POL-SP     SailPoint identity governance
POL-MCP    MCP proxy tool-call governance
POL-SYS    Generic enterprise system telemetry
```

---

## CORE Rules — Cross-System Baseline

Applied to every URO regardless of source system.

### POL-CORE-001 — URO Completeness
**Severity:** HIGH

`actor_id` must be non-empty and not equal to the string `"UNKNOWN"`.

A URO without a known actor cannot be attributed, investigated, or escalated to a responsible party. This rule fires during Silver conformation before any source-specific checks.

**Violation message:** `"actor_id is empty or UNKNOWN (received: '<value>')"`

---

### POL-CORE-002 — Timestamp Freshness
**Severity:** MEDIUM

Event timestamp must not be more than 72 hours in the past at the time of ingestion.

Stale events cannot be acted upon in time to contain the risk. Their presence indicates a pipeline delay, log backlog, or potential timestamp manipulation.

**Violation message:** `"Event is {N}h old — exceeds 72-hour freshness window"`

---

### POL-CORE-003 — Future Timestamp Rejection
**Severity:** HIGH

Event timestamp must not be in the future (clock-skew tolerance: 5 minutes).

Future-dated events indicate either clock synchronisation failure at the source or an attempt to time-bomb a transaction past the review window.

**Violation message:** `"Event timestamp {ts} is in the future"`

---

### POL-CORE-004 — Payload Integrity
**Severity:** CRITICAL

`raw_payload.checksum` must be present and non-empty.

The SHA-256 checksum is set at Bronze ingestion from the verbatim `content` dict. Its absence means the payload was created or mutated after ingestion — a direct integrity violation.

**Violation message:** `"raw_payload.checksum is missing — payload integrity cannot be verified"`

---

## SAP Rules — Financial Controls

Applied only to UROs where `source_system = SAP`.

### POL-SAP-001 — SoD Violation Mandatory Escalation
**Severity:** CRITICAL

Any `SOD_VIOLATION` event is automatically flagged for mandatory escalation. This signals to the Gold layer that the risk score floor must be ≥ 0.70.

SOD violations represent control breakdowns where a single person can initiate and approve financial transactions — the core fraud risk in financial statement fraud.

**Violation message:** `"SOD violation detected — mandatory CRITICAL escalation path applies"`

---

### POL-SAP-002 — Vendor Master Change Approver Presence
**Severity:** HIGH

`VENDOR_MASTER_CHANGE` events must carry an `APPROVER_ID` (or `approver`) field in the raw payload.

Vendor master changes without an approver are a classic payment fraud vector — an attacker can redirect payments to a fraudulent vendor without the second-set-of-eyes control.

**Violation message:** `"Vendor master change has no approver ID — control bypass suspected"`

---

### POL-SAP-003 — Journal Entry Weekend Anomaly
**Severity:** MEDIUM

Journal entries posted on Saturday (weekday=5) or Sunday (weekday=6) without a `weekend_auth_code` field in the raw payload.

Weekend postings without authorisation bypass the separation of duties that operates during business hours. This is a common window exploited for fraudulent adjustments.

**Violation message:** `"Journal entry posted on {Saturday|Sunday} without weekend authorisation code"`

---

### POL-SAP-004 — Period-End Override Restriction
**Severity:** CRITICAL

`PERIOD_CLOSE_OVERRIDE` events from actors whose `actor_groups` list does not include `"financial-controllers"`.

Period-close overrides modify accounting periods that should be locked. Only the financial-controllers group has the authority to reopen a period. Any other actor performing this action constitutes an unauthorised override.

**Violation message:** `"Period-close override by actor '{actor_id}' who is not in the 'financial-controllers' group"`

---

## GitHub Rules — DevSecOps

Applied only to UROs where `source_system = GITHUB`.

### POL-GH-001 — Secret Exposure Zero-Tolerance
**Severity:** CRITICAL

Any `SECRET_DETECTED` event is automatically CRITICAL. Credential rotation must begin within 1 hour.

Exposed credentials have an immediate blast radius: they can be used to authenticate to any system the credential grants access to, instantly. There is no safe version of a SECRET_DETECTED event.

**Violation message:** `"Secret/credential exposure detected — zero-tolerance policy applies; rotation must begin within 1 hour"`

---

### POL-GH-002 — Main Branch Force Push Prohibition
**Severity:** HIGH

`FORCE_PUSH_MAIN` events from actors where `sender.site_admin = False`.

Force pushes to protected branches rewrite history, can silently remove code review evidence, and bypass the merge-commit audit trail. Only repository administrators are permitted to perform them (for recovery operations only).

**Violation message:** `"Force push to protected branch by non-admin actor '{actor_id}'"`

---

### POL-GH-003 — Dependency CVE CVSS Floor
**Severity:** MEDIUM

`DEPENDENCY_VULNERABILITY` events must carry a `cvss_score` or `severity_score` field.

Without a CVSS score the severity cannot be assessed and the vulnerability cannot be prioritised in the remediation queue. The absence indicates the vulnerability scanner is misconfigured or not reporting correctly.

**Violation message:** `"DEPENDENCY_VULNERABILITY event missing cvss_score field"`

---

### POL-GH-004 — Branch Protection Admin Bypass
**Severity:** CRITICAL

A branch-protection audit (`BRANCH_PROTECTION_BYPASSED`) that finds `enforce_admins == false` in the compliance sub-dict.

Administrators bypassing every required check (reviews, status checks) is automatically CRITICAL regardless of how the other controls score — this one setting undoes all the others. Fed by `scm_audit_endpoints.py`'s on-demand runs and `github_scm_tool.py`'s scheduled poll-connector audits, both of which synthesize a `branch_protection_rule`-shaped event with a `compliance` sub-dict (`scm_connectors.normalize_github_compliance`).

**Violation message:** `"CRITICAL: branch protection on '{repo}' does not enforce rules for administrators — admins can bypass every required check"`

---

## GitLab Rules — DevSecOps

Applied only to UROs where `source_system = GITLAB`. GitLab's `GitLabBronzeHandler` mirrors `GitHubBronzeHandler`'s event mapping (`protected_branch_audit`/`merge_request` → `BRANCH_PROTECTION_BYPASSED`/`CODE_REVIEW_BYPASSED`, etc.), fed by `scm_audit_endpoints.py` and `gitlab_scm_tool.py`'s scheduled poll-connector audits.

### POL-GL-001 — Protected Branch Admin Bypass
**Severity:** CRITICAL

A protected-branch audit (`BRANCH_PROTECTION_BYPASSED`) where GitLab's admin/maintainer bypass is allowed — the equivalent of GitHub's `enforce_admins == false`.

**Violation message:** `"CRITICAL: protected branch on '{project}' allows admin/maintainer bypass of required checks"`

---

## DevOps Monitoring Rules — SCM Audits, SARIF Evidence, ITSM SLA Breaches

Applied only to UROs where `source_system = SYSTEM_TELEMETRY` and the event carries the corresponding flag. These rules are the Silver-layer counterpart to the `devops_monitoring` Policy-as-Code Rego module (`pac_endpoints.py`'s `_REGO_DEFAULTS["devops_monitoring"]`, evaluated separately via `mcp_governance._evaluate_pac_policy` — see [integrations.md](integrations.md)) — same underlying findings, checked twice by two independent mechanisms.

### POL-DEVOPS-001 — SARIF Finding SLA Severity Floor
**Severity:** HIGH

`SAST_FINDING` events at CRITICAL or HIGH severity start a remediation SLA clock (7 days / 30 days respectively, per the `devops_monitoring` Rego's DEVOPS-007/008) and must be escalated at ingestion, not left for the next periodic scan. Fed by `evidence_endpoints.py`'s `POST /evidence/webhook` (SARIF ingestion from CI/SAST tooling).

**Violation message:** `"{severity}: SARIF finding '{rule_id}' on '{resource}' — remediation SLA clock started"`

---

### POL-DEVOPS-002 — ITSM Ticket SLA Breach
**Severity:** HIGH

A ticket linked to a DevOps Monitoring finding (branch-protection weakness or SARIF finding) was not resolved before its SLA due date — the finding is re-escalated as failing, same as an expired risk waiver. Fed by `itsm_sla_sweep.py`'s hourly breach-detection sweep re-ingesting the underlying finding tagged `sla_breach`.

**Violation message:** `"ITSM ticket '{external_ticket_key}' for finding '{finding_hash}' breached its remediation SLA (due {sla_due_at})"`

---

### POL-DEVOPS-003 — GitHub Actions Workflow Security
**Severity:** CRITICAL

A `PIPELINE_MISCONFIGURATION` event (`pipeline_security_connectors.py` — GitHub Actions workflow YAML static analysis: token permissions, unpinned third-party actions, `pull_request_target` risk) where the workflow grants write-all `GITHUB_TOKEN` permissions, or triggers on `pull_request_target` with an untrusted PR-head checkout (the classic fork-PR code-execution pattern). Both are automatically escalated regardless of any other finding — the same zero-tolerance treatment POL-GH-004 gives an `enforce_admins=false` branch-protection finding. Applies to both the GITHUB (on-demand) and SYSTEM_TELEMETRY (scheduled poll) source-system paths, since this check can be produced by either.

**Violation message:** `"CRITICAL: workflow on '{repo}' triggers on pull_request_target with an untrusted PR-head checkout — a fork PR can execute code with write-scoped secrets"` or `"Workflow permissions on '{repo}' are write-all — broader than least-privilege"`

---

## Infrastructure Monitoring Rules — IaaS/OS/DB Continuous Audit

Applied only to UROs where `source_system = SYSTEM_TELEMETRY` and `event_type = INFRASTRUCTURE_FINDING`. Fed by `postgres_cis_tool.py` (Postgres CIS-style hardening checks — SSL enforcement, password encryption, superuser sprawl, live unencrypted connections, connection logging), `railway_iaas_tool.py` (Railway platform/deployment drift — unexpected public domain exposure, deployment image digest with no matching pipeline attestation), and `connector_hygiene.py` (connector credential rotation staleness — dogfooded on Intelligenza's own `observability.poll_connectors` store, the one check with no external system to poll), all under the `infrastructure_monitoring` Policy-as-Code process.

### POL-INFRA-001 — Infrastructure Configuration Finding Severity Floor
**Severity:** HIGH

A continuous IaaS/DB configuration audit found a CRITICAL or HIGH severity finding — e.g. SSL not enforced, weak password encryption, a service unexpectedly exposed to the public internet, or (INFRA-008, via `connector_hygiene_sweep.py`'s daily sweep) a stored connector credential that has gone unrotated past the staleness threshold. This one Silver rule covers every Infrastructure Monitoring check by severity alone — no per-check-type rule is needed since each producer already computes its own severity.

**Violation message:** `"{severity}: infrastructure finding on '{resource}' ({check_id})"`

---

## System Rules — Generic Enterprise Telemetry

Applied only to UROs where `source_system = SYSTEM_TELEMETRY`, evaluated against the generic `risk_flags[]` array every poll-connector adapter (Oracle Fusion, SAP HANA, SailPoint, Dynamics 365, NetSuite, SCM, ITSM, Postgres, Railway) can set via `mcp_governance._detect_system_flags()`, independent of which specific system produced the event.

### POL-SYS-001 — Generic SoD Violation Mandatory Escalation
**Severity:** CRITICAL

Any `system_telemetry` event tagged `sod_violation` must be treated as a mandatory CRITICAL escalation path, regardless of source system.

**Violation message:** `"System event on '{server_name}' tagged sod_violation — mandatory CRITICAL escalation path applies"`

---

### POL-SYS-002 — Privileged Access on Critical Severity
**Severity:** HIGH

Events tagged `privileged_access` with `severity = CRITICAL` require authorization review before the action is considered closed.

**Violation message:** `"Privileged-access event on '{server_name}' at CRITICAL severity — requires authorization review"`

---

### POL-SYS-003 — Compound Generic Governance Violation
**Severity:** CRITICAL

Two or more risk flags firing simultaneously on a single generic system event indicates a compound governance failure requiring CRITICAL escalation.

**Violation message:** `"Compound generic governance violation: {N} risk flags fired simultaneously ({flags}) — CRITICAL escalation required"`

---

## SailPoint Rules — Identity Governance

Applied only to UROs where `source_system = SAILPOINT`.

### POL-SP-001 — Privilege Escalation Approval Workflow
**Severity:** CRITICAL

`PRIVILEGE_ESCALATION` events must reference an approved access request ID (`requestId` or `access_request_id`).

Escalations without approval evidence indicate a manual override, a workflow bypass, or a compromised provisioning system. Every access grant above normal privileges must have a documented and approved request.

**Violation message:** `"Privilege escalation for '{actor_id}' has no approved request ID — manual override suspected"`

---

### POL-SP-002 — Dormant Privileged Account Age Threshold
**Severity:** HIGH

`DORMANT_PRIVILEGED_ACCOUNT` events must include a `last_login_days` field ≥ 30.

Accounts inactive for fewer than 30 days have not yet met the dormancy threshold. The field being absent entirely indicates the identity system is not tracking last-login dates — a data quality issue that must be remediated before the rule can function.

**Violation message:** `"DORMANT_PRIVILEGED_ACCOUNT event missing 'last_login_days' field"` or `"Account inactive for only {N} days — below the 30-day dormancy threshold"`

---

### POL-SP-003 — Role Explosion Detection
**Severity:** CRITICAL

`ROLE_EXPLOSION` events where `role_count > 25`.

An identity holding more than 25 roles has accumulated permissions across so many systems that it effectively has no meaningful segregation of duties. A single compromised credential at this level is a single-point-of-failure for the entire organisation.

**Violation message:** `"Identity '{actor_id}' holds {N} roles — exceeds 25-role SoD limit (CRITICAL)"`

---

## MCP Rules — Proxy Tool-Call Governance

Applied only to UROs where `source_system = MCP_PROXY`. These rules are evaluated against flagged rows from `observability.mcp_telemetry` after the telemetry proxy has already detected the raw flag. The Silver layer converts the proxy flags into structured policy violations with detailed messages.

### POL-MCP-001 — MCP Bypass Keyword Detection
**Severity:** CRITICAL

Any MCP tool call payload containing bypass keywords (`skip-ci`, `no-verify`, `force-push`, `bypass`, `skip_ci`) is automatically CRITICAL.

These keywords are associated with suppressing CI pipelines, audit hooks, and code review gates. Their presence in a tool call payload suggests the agentic system is being used to subvert the controls it is supposed to enforce.

**Violation message:** `"MCP tool call to '{tool}' contains bypass keyword — CI/review suppression detected; audit trail may be incomplete"`

---

### POL-MCP-002 — Sensitive Tool Call Authorization
**Severity:** HIGH

MCP calls to destructive or high-risk tools (`delete`, `drop`, `truncate`, `exec_sql`, `run_query`, `write_file`, `shell`, `execute`) require authorization review.

These tool names indicate operations that cannot be easily reversed (database drops, file overwrites, arbitrary shell execution). Their presence in an agentic workflow requires explicit human authorization before the tool is invoked.

**Violation message:** `"MCP call to sensitive/destructive tool '{tool}' — requires authorization review before execution"`

---

### POL-MCP-003 — MCP Tool SLA Breach
**Severity:** MEDIUM

MCP tool calls exceeding 30,000 ms (30 seconds) execution time breach the operational SLA.

Long-running tool calls can indicate resource exhaustion, an infinite loop in the agentic workflow, a hanging external API call, or a denial-of-service condition. The 30-second threshold is a conservative ceiling that catches genuine hangs without triggering on slow-but-legitimate EDGAR or FRED fetches.

**Violation message:** `"MCP tool '{tool}' SLA breach: {N}ms > 30,000ms threshold — potential resource exhaustion or hanging call"`

---

### POL-MCP-004 — MCP Tool Error Mandatory Investigation
**Severity:** MEDIUM

MCP tool calls that return an error status with an error message must be investigated.

Repeated errors from the same tool indicate systematic failures that could leave governance controls partially or completely disabled. Even transient errors should be logged and trended to detect degradation patterns.

**Violation message:** `"MCP tool '{tool}' returned error — mandatory investigation: {error_message[:200]}"`

---

### POL-MCP-005 — Compound MCP Governance Violation
**Severity:** CRITICAL

Three or more risk flags firing simultaneously on a single MCP call indicates a compound governance failure.

A single flag can represent noise or edge-case behaviour. Three simultaneous flags on one tool call (e.g., bypass keyword + sensitive tool + bulk args) represent a pattern that crosses the threshold from coincidence to coordinated behaviour.

**Violation message:** `"Compound MCP governance violation: {N} risk flags fired simultaneously ({flags}) — CRITICAL escalation required"`

---

## Registry Summary

| Rule ID | Severity | Source | Summary |
|---|---|---|---|
| POL-CORE-001 | HIGH | All | actor_id completeness |
| POL-CORE-002 | MEDIUM | All | 72-hour event freshness |
| POL-CORE-003 | HIGH | All | Future timestamp rejection |
| POL-CORE-004 | CRITICAL | All | Payload checksum integrity |
| POL-SAP-001 | CRITICAL | SAP | SoD violation mandatory escalation |
| POL-SAP-002 | HIGH | SAP | Vendor change approver presence |
| POL-SAP-003 | MEDIUM | SAP | Weekend journal entry anomaly |
| POL-SAP-004 | CRITICAL | SAP | Period-end override restriction |
| POL-GH-001 | CRITICAL | GITHUB | Secret exposure zero-tolerance |
| POL-GH-002 | HIGH | GITHUB | Force push prohibition |
| POL-GH-003 | MEDIUM | GITHUB | CVSS floor on CVEs |
| POL-GH-004 | CRITICAL | GITHUB | Branch protection admin bypass |
| POL-GL-001 | CRITICAL | GITLAB | Protected branch admin bypass |
| POL-DEVOPS-001 | HIGH | SYSTEM_TELEMETRY | SARIF finding SLA severity floor |
| POL-DEVOPS-002 | HIGH | SYSTEM_TELEMETRY | ITSM ticket SLA breach |
| POL-DEVOPS-003 | CRITICAL | GITHUB, SYSTEM_TELEMETRY | GitHub Actions workflow security (write-all perms, risky pull_request_target) |
| POL-INFRA-001 | HIGH | SYSTEM_TELEMETRY | Infrastructure config finding severity floor (incl. connector credential staleness) |
| POL-SP-001 | CRITICAL | SAILPOINT | Privilege escalation approval workflow |
| POL-SP-002 | HIGH | SAILPOINT | Dormant account age threshold |
| POL-SP-003 | CRITICAL | SAILPOINT | Role explosion SoD limit |
| POL-MCP-001 | CRITICAL | MCP_PROXY | Bypass keyword detection |
| POL-MCP-002 | HIGH | MCP_PROXY | Sensitive tool call authorization |
| POL-MCP-003 | MEDIUM | MCP_PROXY | Tool SLA breach (30 s) |
| POL-MCP-004 | MEDIUM | MCP_PROXY | Tool error investigation |
| POL-MCP-005 | CRITICAL | MCP_PROXY | Compound violation (3+ flags) |
| POL-SYS-001 | CRITICAL | SYSTEM_TELEMETRY | Generic SoD violation mandatory escalation |
| POL-SYS-002 | HIGH | SYSTEM_TELEMETRY | Privileged access on critical severity |
| POL-SYS-003 | CRITICAL | SYSTEM_TELEMETRY | Compound generic governance violation (2+ flags) |

**Total: 28 rules across 9 domains.** (Also evaluated independently, in parallel, against the real saved/default Rego for the process the event routes to — see `mcp_governance._evaluate_pac_policy` in [integrations.md](integrations.md); these Silver rules and the Rego rules are two separate mechanisms checking overlapping ground, not one calling the other.)

---

## Adding a New Rule

1. Add the `PolicyRule` definition to the appropriate list in `policy/rules.py` (or a new list for a new domain, then add to `POLICY_REGISTRY`).
2. Add an `elif rule.rule_id == "POL-XYZ-NNN":` block to `SilverConformationLayer._check_rule()` in `pipeline/silver.py`.
3. If the rule applies to a new `SourceSystem`, add that system to `SourceSystem` in `models/uro.py` and add a Bronze handler in `pipeline/bronze.py`.
4. Document the rule in this file.

No other files need to change.
