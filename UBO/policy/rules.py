"""
Policy-as-Code Rule Registry.

Rules are pure declarative data. The Silver layer's _check_rule() dispatcher
evaluates them against each URO. Adding a new governance requirement means
adding a rule here — no pipeline code changes required.

Naming convention: POL-{domain}-{sequence}
  POL-CORE  — cross-system baseline rules
  POL-SAP   — SAP-specific financial controls
  POL-GH    — GitHub DevSecOps rules
  POL-SP    — SailPoint identity rules
"""

from ..models.uro import SourceSystem
from ..pipeline.base import PolicyRule


# ── Cross-System Baseline Rules ───────────────────────────────────────────────

CORE_RULES: list[PolicyRule] = [
    PolicyRule(
        rule_id="POL-CORE-001",
        name="URO Completeness",
        description="actor_id must be non-empty and not equal to 'UNKNOWN'.",
        severity="HIGH",
    ),
    PolicyRule(
        rule_id="POL-CORE-002",
        name="Timestamp Freshness",
        description="Event timestamp must not be more than 72 hours in the past at ingestion time.",
        severity="MEDIUM",
    ),
    PolicyRule(
        rule_id="POL-CORE-003",
        name="Future Timestamp Rejection",
        description="Event timestamp must not be in the future (clock skew tolerance: 5 minutes).",
        severity="HIGH",
    ),
    PolicyRule(
        rule_id="POL-CORE-004",
        name="Payload Integrity",
        description="raw_payload.checksum must be present and non-empty.",
        severity="CRITICAL",
    ),
]

# ── SAP Financial Control Rules ───────────────────────────────────────────────

SAP_RULES: list[PolicyRule] = [
    PolicyRule(
        rule_id="POL-SAP-001",
        name="SoD Violation Mandatory Escalation",
        description=(
            "Any SAP SOD_VIOLATION event must have a risk_score >= 0.70 at Silver stage "
            "regardless of other signals, to ensure mandatory escalation path."
        ),
        severity="CRITICAL",
        applies_to=[SourceSystem.SAP.value],
    ),
    PolicyRule(
        rule_id="POL-SAP-002",
        name="Vendor Master Change Approver Presence",
        description=(
            "VENDOR_MASTER_CHANGE events must carry an approver ID in the raw payload. "
            "Missing approver indicates a control bypass."
        ),
        severity="HIGH",
        applies_to=[SourceSystem.SAP.value],
    ),
    PolicyRule(
        rule_id="POL-SAP-003",
        name="Journal Entry Weekend Anomaly",
        description=(
            "Journal entries posted on Saturday or Sunday without a weekend-posting "
            "authorisation code are flagged for review."
        ),
        severity="MEDIUM",
        applies_to=[SourceSystem.SAP.value],
    ),
    PolicyRule(
        rule_id="POL-SAP-004",
        name="Period-End Override Restriction",
        description=(
            "PERIOD_CLOSE_OVERRIDE events from actors not in the 'financial-controllers' "
            "group must be escalated immediately."
        ),
        severity="CRITICAL",
        applies_to=[SourceSystem.SAP.value],
    ),
]

# ── GitHub DevSecOps Rules ────────────────────────────────────────────────────

GITHUB_RULES: list[PolicyRule] = [
    PolicyRule(
        rule_id="POL-GH-001",
        name="Secret Exposure Zero-Tolerance",
        description=(
            "Any SECRET_DETECTED event is automatically CRITICAL regardless of confidence. "
            "Credential rotation must be initiated within 1 hour."
        ),
        severity="CRITICAL",
        applies_to=[SourceSystem.GITHUB.value],
    ),
    PolicyRule(
        rule_id="POL-GH-002",
        name="Main Branch Force Push Prohibition",
        description=(
            "FORCE_PUSH_MAIN events from non-admin actors violate the branch-protection policy."
        ),
        severity="HIGH",
        applies_to=[SourceSystem.GITHUB.value],
    ),
    PolicyRule(
        rule_id="POL-GH-003",
        name="Dependency CVE CVSS Floor",
        description=(
            "DEPENDENCY_VULNERABILITY events must carry a cvss_score field. "
            "Missing CVSS indicates the vulnerability scanner is not reporting correctly."
        ),
        severity="MEDIUM",
        applies_to=[SourceSystem.GITHUB.value],
    ),
    PolicyRule(
        rule_id="POL-GH-004",
        name="Branch Protection Admin Bypass",
        description=(
            "A branch-protection audit (BRANCH_PROTECTION_BYPASSED) that finds "
            "enforce_admins == false means administrators can bypass every other "
            "required check — automatically CRITICAL regardless of the other checks."
        ),
        severity="CRITICAL",
        applies_to=[SourceSystem.GITHUB.value],
    ),
]

# ── GitLab DevSecOps Rules ────────────────────────────────────────────────────

GITLAB_RULES: list[PolicyRule] = [
    PolicyRule(
        rule_id="POL-GL-001",
        name="Protected Branch Admin Bypass",
        description=(
            "A protected-branch audit (BRANCH_PROTECTION_BYPASSED) that finds GitLab's "
            "admin/maintainer bypass allowed means the equivalent of enforce_admins=false — "
            "automatically CRITICAL."
        ),
        severity="CRITICAL",
        applies_to=[SourceSystem.GITLAB.value],
    ),
]

# ── DevOps Monitoring: SARIF/SAST Evidence Rules ──────────────────────────────
# Findings ride the generic system_telemetry ingestion path (see
# SystemTelemetryBronzeHandler / evidence_endpoints.py), so this rule is
# keyed to SourceSystem.SYSTEM_TELEMETRY rather than GITHUB/GITLAB.

DEVOPS_EVIDENCE_RULES: list[PolicyRule] = [
    PolicyRule(
        rule_id="POL-DEVOPS-001",
        name="SARIF Finding SLA Severity Floor",
        description=(
            "SAST_FINDING events at CRITICAL or HIGH severity start a remediation SLA "
            "clock (7 days / 30 days respectively) and must be escalated at ingestion, "
            "not left for the next periodic scan."
        ),
        severity="HIGH",
        applies_to=[SourceSystem.SYSTEM_TELEMETRY.value],
    ),
    PolicyRule(
        rule_id="POL-DEVOPS-002",
        name="ITSM Ticket SLA Breach",
        description=(
            "A ticket linked to a DevOps Monitoring finding (branch-protection "
            "weakness or SARIF finding) was not resolved before its SLA due date — "
            "the finding is re-escalated as failing, same as an expired risk waiver."
        ),
        severity="HIGH",
        applies_to=[SourceSystem.SYSTEM_TELEMETRY.value],
    ),
]

# ── Infrastructure Monitoring: IaaS/OS/DB continuous audit ────────────────────

INFRASTRUCTURE_RULES: list[PolicyRule] = [
    PolicyRule(
        rule_id="POL-INFRA-001",
        name="Infrastructure Configuration Finding Severity Floor",
        description=(
            "A continuous IaaS/DB configuration audit (postgres_cis_tool.py / "
            "railway_iaas_tool.py) found a CRITICAL or HIGH severity finding — "
            "e.g. SSL not enforced, weak password encryption, or a service "
            "unexpectedly exposed to the public internet."
        ),
        severity="HIGH",
        applies_to=[SourceSystem.SYSTEM_TELEMETRY.value],
    ),
]

# ── SailPoint Identity Rules ──────────────────────────────────────────────────

SAILPOINT_RULES: list[PolicyRule] = [
    PolicyRule(
        rule_id="POL-SP-001",
        name="Privilege Escalation Approval Workflow",
        description=(
            "PRIVILEGE_ESCALATION events must reference an approved access request ID. "
            "Escalations without approval evidence indicate a manual override."
        ),
        severity="CRITICAL",
        applies_to=[SourceSystem.SAILPOINT.value],
    ),
    PolicyRule(
        rule_id="POL-SP-002",
        name="Dormant Privileged Account Age Threshold",
        description=(
            "DORMANT_PRIVILEGED_ACCOUNT events must flag accounts inactive for >= 30 days. "
            "The raw payload must include 'last_login_days' >= 30."
        ),
        severity="HIGH",
        applies_to=[SourceSystem.SAILPOINT.value],
    ),
    PolicyRule(
        rule_id="POL-SP-003",
        name="Role Explosion Detection",
        description=(
            "ROLE_EXPLOSION events where a single identity holds > 25 roles must be "
            "treated as a CRITICAL segregation-of-duties failure."
        ),
        severity="CRITICAL",
        applies_to=[SourceSystem.SAILPOINT.value],
    ),
]

# ── MCP Proxy Governance Rules ────────────────────────────────────────────────

MCP_RULES: list[PolicyRule] = [
    PolicyRule(
        rule_id="POL-MCP-001",
        name="MCP Bypass Keyword Detection",
        description=(
            "Any MCP tool call payload containing bypass keywords (skip-ci, no-verify, force-push) "
            "is automatically CRITICAL — these keywords suppress the audit trail."
        ),
        severity="CRITICAL",
        applies_to=[SourceSystem.MCP_PROXY.value],
    ),
    PolicyRule(
        rule_id="POL-MCP-002",
        name="Sensitive Tool Call Authorization",
        description=(
            "MCP calls to destructive or high-risk tools (delete, drop, exec_sql, shell) "
            "require authorization review before execution is permitted."
        ),
        severity="HIGH",
        applies_to=[SourceSystem.MCP_PROXY.value],
    ),
    PolicyRule(
        rule_id="POL-MCP-003",
        name="MCP Tool SLA Breach",
        description=(
            "MCP tool calls exceeding 30,000 ms execution time breach the operational SLA "
            "and indicate potential resource exhaustion or a hanging call."
        ),
        severity="MEDIUM",
        applies_to=[SourceSystem.MCP_PROXY.value],
    ),
    PolicyRule(
        rule_id="POL-MCP-004",
        name="MCP Tool Error Mandatory Investigation",
        description=(
            "MCP tool calls that return an error status with an error message "
            "must be investigated for systematic failures or misconfiguration."
        ),
        severity="MEDIUM",
        applies_to=[SourceSystem.MCP_PROXY.value],
    ),
    PolicyRule(
        rule_id="POL-MCP-005",
        name="Compound MCP Governance Violation",
        description=(
            "Three or more risk flags firing simultaneously on a single MCP call "
            "indicates a compound governance failure requiring CRITICAL escalation."
        ),
        severity="CRITICAL",
        applies_to=[SourceSystem.MCP_PROXY.value],
    ),
]

# ── Generic Enterprise System Rules (system_telemetry ingest) ────────────────

SYSTEM_RULES: list[PolicyRule] = [
    PolicyRule(
        rule_id="POL-SYS-001",
        name="Generic SoD Violation Mandatory Escalation",
        description=(
            "Any system_telemetry event tagged sod_violation must be treated as a "
            "mandatory CRITICAL escalation path, regardless of source system."
        ),
        severity="CRITICAL",
        applies_to=[SourceSystem.SYSTEM_TELEMETRY.value],
    ),
    PolicyRule(
        rule_id="POL-SYS-002",
        name="Privileged Access on Critical Severity",
        description=(
            "Events tagged privileged_access with severity=CRITICAL require authorization "
            "review before the action is considered closed."
        ),
        severity="HIGH",
        applies_to=[SourceSystem.SYSTEM_TELEMETRY.value],
    ),
    PolicyRule(
        rule_id="POL-SYS-003",
        name="Compound Generic Governance Violation",
        description=(
            "Two or more risk flags firing simultaneously on a single generic system event "
            "indicates a compound governance failure requiring CRITICAL escalation."
        ),
        severity="CRITICAL",
        applies_to=[SourceSystem.SYSTEM_TELEMETRY.value],
    ),
]

# ── Global Registry ───────────────────────────────────────────────────────────

POLICY_REGISTRY: list[PolicyRule] = [
    *CORE_RULES,
    *SAP_RULES,
    *GITHUB_RULES,
    *GITLAB_RULES,
    *DEVOPS_EVIDENCE_RULES,
    *INFRASTRUCTURE_RULES,
    *SAILPOINT_RULES,
    *MCP_RULES,
    *SYSTEM_RULES,
]


def get_rules_for_source(source: SourceSystem) -> list[PolicyRule]:
    return [r for r in POLICY_REGISTRY if r.applies(source)]
