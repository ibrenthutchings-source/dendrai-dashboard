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

# ── Global Registry ───────────────────────────────────────────────────────────

POLICY_REGISTRY: list[PolicyRule] = [
    *CORE_RULES,
    *SAP_RULES,
    *GITHUB_RULES,
    *SAILPOINT_RULES,
]


def get_rules_for_source(source: SourceSystem) -> list[PolicyRule]:
    return [r for r in POLICY_REGISTRY if r.applies(source)]
