"""
Silver Layer — conformation, normalisation, and Policy-as-Code validation.

Every Bronze URO passes through here before it can be scored by Gold.
The Silver layer:
  1. Extracts structured fields from raw_payload.content into a ConformedPayload
  2. Runs the Policy-as-Code rule engine (all registered PolicyRules)
  3. Attaches violations to the URO — does NOT drop the URO even if it violates policy
     (non-blocking; downstream agents weigh violations in their scoring)
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from ..models.uro import (
    ConformedPayload,
    EventType,
    NormalizedAttributes,
    PipelineStage,
    SourceSystem,
    URO,
)
from ..policy.rules import POLICY_REGISTRY
from .base import PolicyRule, SilverLayerBase


class SilverConformationLayer(SilverLayerBase):
    """
    Concrete Silver layer. Handles all registered source systems.

    Conformation is source-aware: the same ConformedPayload schema is produced
    regardless of whether the event came from SAP, GitHub, or SailPoint.
    """

    def __init__(self) -> None:
        super().__init__(rules=POLICY_REGISTRY)

    # ── Public entry point ────────────────────────────────────────────────────

    async def conform(self, uro: URO) -> URO:
        """Conform a Bronze URO → Silver URO with validated, normalised payload."""
        raw = uro.raw_payload.content

        # Route to source-specific conformation logic
        conformers = {
            SourceSystem.SAP:        self._conform_sap,
            SourceSystem.GITHUB:     self._conform_github,
            SourceSystem.GITLAB:     self._conform_gitlab,
            SourceSystem.BITBUCKET:  self._conform_bitbucket,
            SourceSystem.SAILPOINT:  self._conform_sailpoint,
            SourceSystem.MCP_PROXY:  self._conform_mcp_proxy,
            SourceSystem.SYSTEM_TELEMETRY: self._conform_system_telemetry,
        }
        conformer = conformers.get(uro.source_system, self._conform_generic)
        conformed = conformer(raw, uro)

        # Run the full Policy-as-Code rule engine
        violations = await self.validate(uro)

        return uro.as_silver(conformed, violations)

    # ── Policy-as-Code rule dispatcher ───────────────────────────────────────

    async def _check_rule(self, rule: PolicyRule, uro: URO) -> str | None:
        """
        Dispatch table for all policy rules.
        Returns a violation string, or None if the rule passes.
        """
        raw = uro.raw_payload.content
        now = datetime.now(tz=timezone.utc)
        ts  = uro.timestamp if uro.timestamp.tzinfo else uro.timestamp.replace(tzinfo=timezone.utc)

        # ── CORE rules ────────────────────────────────────────────────────────
        if rule.rule_id == "POL-CORE-001":
            if not uro.actor_id or uro.actor_id.strip().upper() == "UNKNOWN":
                return f"actor_id is empty or UNKNOWN (received: '{uro.actor_id}')"

        elif rule.rule_id == "POL-CORE-002":
            age = now - ts
            if age > timedelta(hours=72):
                return f"Event is {age.total_seconds() / 3600:.1f}h old — exceeds 72-hour freshness window"

        elif rule.rule_id == "POL-CORE-003":
            if ts > now + timedelta(minutes=5):
                return f"Event timestamp {ts.isoformat()} is in the future"

        elif rule.rule_id == "POL-CORE-004":
            if not uro.raw_payload.checksum:
                return "raw_payload.checksum is missing — payload integrity cannot be verified"

        # ── SAP rules ─────────────────────────────────────────────────────────
        elif rule.rule_id == "POL-SAP-001":
            if uro.event_type == EventType.SOD_VIOLATION:
                # Violation = signal to Gold/Agents that floor score is 0.70
                return "SOD violation detected — mandatory CRITICAL escalation path applies"

        elif rule.rule_id == "POL-SAP-002":
            if uro.event_type == EventType.VENDOR_MASTER_CHANGE:
                approver = raw.get("APPROVER_ID") or raw.get("approver")
                if not approver:
                    return "Vendor master change has no approver ID — control bypass suspected"

        elif rule.rule_id == "POL-SAP-003":
            if uro.event_type == EventType.JOURNAL_ENTRY_ANOMALY:
                weekday = ts.weekday()  # 5=Sat, 6=Sun
                if weekday >= 5 and not raw.get("weekend_auth_code"):
                    return (
                        f"Journal entry posted on {'Saturday' if weekday==5 else 'Sunday'} "
                        "without weekend authorisation code"
                    )

        elif rule.rule_id == "POL-SAP-004":
            if uro.event_type == EventType.PERIOD_CLOSE_OVERRIDE:
                groups = raw.get("actor_groups") or []
                if "financial-controllers" not in groups:
                    return (
                        f"Period-close override by actor '{uro.actor_id}' "
                        "who is not in the 'financial-controllers' group"
                    )

        # ── GitHub rules ─────────────────────────────────────────────────────
        elif rule.rule_id == "POL-GH-001":
            if uro.event_type == EventType.SECRET_DETECTED:
                return (
                    "Secret/credential exposure detected — zero-tolerance policy applies; "
                    "rotation must begin within 1 hour"
                )

        elif rule.rule_id == "POL-GH-002":
            if uro.event_type == EventType.FORCE_PUSH_MAIN:
                is_admin = raw.get("sender", {}).get("site_admin", False)
                if not is_admin:
                    return (
                        f"Force push to protected branch by non-admin actor '{uro.actor_id}'"
                    )

        elif rule.rule_id == "POL-GH-003":
            if uro.event_type == EventType.DEPENDENCY_VULNERABILITY:
                cvss = raw.get("cvss_score") or raw.get("severity_score")
                if cvss is None:
                    return "DEPENDENCY_VULNERABILITY event missing cvss_score field"

        elif rule.rule_id == "POL-GH-006":
            if uro.event_type == EventType.DEPENDENCY_VULNERABILITY:
                severity = str(raw.get("severity") or "").upper()
                state = str(raw.get("state") or "open").lower()
                first_detected = raw.get("first_detected_at") or raw.get("created_at")
                if severity in ("CRITICAL", "HIGH") and state == "open" and first_detected:
                    try:
                        detected_ts = first_detected if isinstance(first_detected, datetime) else datetime.fromisoformat(str(first_detected).replace("Z", "+00:00"))
                        if detected_ts.tzinfo is None:
                            detected_ts = detected_ts.replace(tzinfo=timezone.utc)
                        age_days = (now - detected_ts).total_seconds() / 86400
                    except (ValueError, TypeError):
                        age_days = None
                    if age_days is not None and age_days > 14:
                        repo = raw.get("repository", {}).get("full_name", "unknown")
                        return (
                            f"CRITICAL: {severity} vulnerability on '{repo}' has been open "
                            f"{age_days:.0f} days — exceeds the 14-day remediation SLA"
                        )

        elif rule.rule_id == "POL-GH-004":
            if uro.event_type == EventType.BRANCH_PROTECTION_BYPASSED and uro.source_system == SourceSystem.GITHUB:
                compliance = raw.get("compliance") or {}
                if compliance.get("enforce_admins") is False:
                    repo = raw.get("repository", {}).get("full_name", "unknown")
                    return (
                        f"CRITICAL: branch protection on '{repo}' does not "
                        "enforce rules for administrators — admins can bypass every required check"
                    )

        elif rule.rule_id == "POL-GH-005":
            if uro.event_type == EventType.DEPLOY_GATE_BYPASSED and uro.source_system == SourceSystem.GITHUB:
                gate = raw.get("gate_approval") or {}
                if not gate.get("approved"):
                    repo = raw.get("repository", {}).get("full_name", "unknown")
                    reason = "no associated pull request" if not gate.get("has_pr") else "pull request never approved"
                    return (
                        f"CRITICAL: commit '{raw.get('commit_sha', 'unknown')[:12]}' deployed to "
                        f"'{repo}' with {reason} — required review gate was bypassed"
                    )

        # ── GitLab rules ──────────────────────────────────────────────────────
        elif rule.rule_id == "POL-GL-001":
            if uro.event_type == EventType.BRANCH_PROTECTION_BYPASSED and uro.source_system == SourceSystem.GITLAB:
                compliance = raw.get("compliance") or {}
                if compliance.get("enforce_admins") is False:
                    repo = raw.get("project", {}).get("path_with_namespace", "unknown")
                    return (
                        f"CRITICAL: protected branch on '{repo}' allows admin/maintainer "
                        "bypass of required checks"
                    )

        # ── Bitbucket rules ───────────────────────────────────────────────────
        elif rule.rule_id == "POL-BB-001":
            if uro.event_type == EventType.BRANCH_PROTECTION_BYPASSED and uro.source_system == SourceSystem.BITBUCKET:
                compliance = raw.get("compliance") or {}
                if compliance.get("enforce_admins") is False:
                    repo = raw.get("repository", {}).get("full_name", "unknown")
                    return (
                        f"CRITICAL: branch restrictions on '{repo}' do not block direct "
                        "pushes for all users — admins/exempted users can bypass required checks"
                    )

        # ── DevOps Monitoring: SARIF/SAST evidence rules ─────────────────────
        elif rule.rule_id == "POL-DEVOPS-001":
            if uro.event_type == EventType.SAST_FINDING:
                severity = str(raw.get("severity") or "").upper()
                if severity in ("CRITICAL", "HIGH"):
                    rule_ref = (raw.get("raw_payload") or {}).get("rule_id", "unknown-rule")
                    return (
                        f"{severity}: SARIF finding '{rule_ref}' on '{raw.get('resource', 'unknown')}' "
                        "— remediation SLA clock started"
                    )

        elif rule.rule_id == "POL-DEVOPS-002":
            if uro.event_type == EventType.SLA_BREACH:
                payload = raw.get("raw_payload") or {}
                return (
                    f"ITSM ticket '{payload.get('external_ticket_key', 'unknown')}' for finding "
                    f"'{payload.get('finding_hash', 'unknown')}' breached its remediation SLA "
                    f"(due {payload.get('sla_due_at', 'unknown')})"
                )

        # ── DevOps Monitoring: Pipeline-as-Code (CI/CD workflow) audit ────────
        elif rule.rule_id == "POL-DEVOPS-003":
            if uro.event_type == EventType.PIPELINE_MISCONFIGURATION:
                compliance = raw.get("compliance") or (raw.get("raw_payload") or {}).get("pipeline_compliance") or {}
                repo = (raw.get("repository") or {}).get("full_name") or raw.get("resource") or "unknown"
                if compliance.get("has_risky_pull_request_target"):
                    return (
                        f"CRITICAL: workflow on '{repo}' triggers on pull_request_target with an "
                        "untrusted PR-head checkout — a fork PR can execute code with write-scoped secrets"
                    )
                if compliance.get("has_write_all_permissions"):
                    return f"Workflow permissions on '{repo}' are write-all — broader than least-privilege"

        # ── Infrastructure Monitoring: IaaS/OS/DB continuous audit ────────────
        elif rule.rule_id == "POL-INFRA-001":
            if uro.event_type == EventType.INFRASTRUCTURE_FINDING:
                payload = raw.get("raw_payload") or {}
                severity = str(raw.get("severity") or "").upper()
                if severity in ("CRITICAL", "HIGH"):
                    return (
                        f"{severity}: infrastructure finding on '{raw.get('resource', 'unknown')}' "
                        f"({payload.get('check_id', 'unknown check')})"
                    )

        # ── Financial Risk Pipeline rules ────────────────────────────────────
        elif rule.rule_id == "POL-FIN-001":
            if uro.event_type == EventType.JE_VELOCITY_ANOMALY:
                fc = (raw.get("raw_payload") or {}).get("financial_compliance") or {}
                if fc.get("anomaly"):
                    return (
                        f"HIGH: manual journal-entry velocity on '{raw.get('resource', 'unknown')}' "
                        f"is {fc.get('z_score')}σ above baseline (rate {fc.get('recent_daily_rate')}/day "
                        f"vs. baseline {fc.get('baseline_daily_mean')}/day)"
                    )

        elif rule.rule_id == "POL-FIN-002":
            if uro.event_type == EventType.LIQUIDITY_SHIFT:
                fc = (raw.get("raw_payload") or {}).get("financial_compliance") or {}
                if fc.get("shift_detected"):
                    return (
                        f"HIGH: liquidity shift on '{raw.get('resource', 'unknown')}' — "
                        f"QoQ ratio delta {fc.get('worst_z_score')}σ below historical norm"
                    )

        elif rule.rule_id == "POL-FIN-003":
            if uro.event_type == EventType.INVENTORY_DIVERGENCE:
                fc = (raw.get("raw_payload") or {}).get("financial_compliance") or {}
                if fc.get("divergence_detected"):
                    return (
                        f"MEDIUM: inventory/sales divergence on '{raw.get('resource', 'unknown')}' — "
                        f"ratio delta {fc.get('z_score')}σ above historical norm (toxic bloat)"
                    )

        # ── Hire-to-Retire rules (oracle_hcm_tool.py) ────────────────────────
        elif rule.rule_id == "POL-HR-001":
            if uro.event_type == EventType.GHOST_EMPLOYEE_SUSPECTED:
                pd = (raw.get("raw_payload") or {}).get("payroll_detail") or {}
                return (
                    f"HIGH: active pay run for employee '{pd.get('employee_id', 'unknown')}' "
                    f"who was terminated on {pd.get('termination_date', 'unknown date')} — "
                    "ghost-employee pattern"
                )

        elif rule.rule_id == "POL-HR-002":
            if uro.event_type == EventType.UNAUTHORIZED_PAY_RATE_CHANGE:
                pd = (raw.get("raw_payload") or {}).get("payroll_detail") or {}
                pct = pd.get("pay_rate_change_pct")
                if pct is not None and not pd.get("second_approver"):
                    return (
                        f"HIGH: pay-rate change of {pct}% for employee "
                        f"'{pd.get('employee_id', 'unknown')}' (${pd.get('prior_pay_rate')} -> "
                        f"${pd.get('new_pay_rate')}) has no second approver on file"
                    )

        elif rule.rule_id == "POL-HR-003":
            if uro.event_type == EventType.TERMINATED_EMPLOYEE_ACCESS_RETAINED:
                pd = (raw.get("raw_payload") or {}).get("payroll_detail") or {}
                days = pd.get("days_since_termination")
                if days is not None and int(days) > 7:
                    return (
                        f"MEDIUM: employee '{pd.get('employee_id', 'unknown')}' still has active "
                        f"system access {days} days after termination on "
                        f"{pd.get('termination_date', 'unknown date')}"
                    )

        # ── Treasury & Cash Management rules (oracle_fusion_tool.py) ─────────
        elif rule.rule_id == "POL-TREAS-001":
            if uro.event_type == EventType.WIRE_TRANSFER_SINGLE_APPROVAL:
                td = (raw.get("raw_payload") or {}).get("treasury_detail") or {}
                return (
                    f"HIGH: wire transfer '{td.get('payment_id', 'unknown')}' for "
                    f"{td.get('currency', '')} {td.get('amount', 'unknown amount')} was processed "
                    f"with only {td.get('approver_count', 1)} approver — dual-approval control bypassed"
                )

        elif rule.rule_id == "POL-TREAS-002":
            if uro.event_type == EventType.BANK_RECON_OVERDUE:
                td = (raw.get("raw_payload") or {}).get("treasury_detail") or {}
                days = td.get("days_overdue")
                if days is not None and int(days) > 0:
                    return (
                        f"HIGH: bank account '{td.get('bank_account', 'unknown')}' reconciliation is "
                        f"{days} day(s) overdue — last reconciled {td.get('last_reconciled_date', 'unknown date')}"
                    )

        elif rule.rule_id == "POL-TREAS-003":
            if uro.event_type == EventType.FX_HEDGE_DOCUMENTATION_MISSING:
                td = (raw.get("raw_payload") or {}).get("treasury_detail") or {}
                return (
                    f"MEDIUM: FX hedge '{td.get('hedge_id', 'unknown')}' on "
                    f"{td.get('currency_pair', 'unknown pair')} (notional {td.get('notional_amount', 'unknown')}) "
                    "has no completed hedge-accounting documentation on file"
                )

        # ── Export Control / Trade Compliance rules (denied_party_screening_tool.py) ─
        elif rule.rule_id == "POL-TC-001":
            if uro.event_type == EventType.EXPORT_CONTROL_MATCH:
                tc = (raw.get("raw_payload") or {}).get("trade_compliance_detail") or {}
                return (
                    f"CRITICAL: {tc.get('party_type', 'party')} '{tc.get('party_name', 'unknown')}' matched "
                    f"'{tc.get('matched_name', 'unknown')}' on the {tc.get('list_source', 'restricted-party')} "
                    f"list (score {tc.get('match_score', 'unknown')}, entity {tc.get('entity_number', 'unknown')}) "
                    "— zero-tolerance, transact only after compliance clearance"
                )

        # ── Continuous Third-Party/Vendor Risk rules ──────────────────────────
        elif rule.rule_id == "POL-VEN-001":
            if uro.event_type == EventType.VENDOR_SOC2_EXPIRED:
                vd = (raw.get("raw_payload") or {}).get("vendor_risk_detail") or {}
                return (
                    f"HIGH: vendor '{vd.get('vendor_name', 'unknown')}'"
                    f"{' (critical)' if vd.get('critical') else ''} SOC 2 report expired on "
                    f"{vd.get('soc2_expires_at', 'unknown date')} — no current attestation on file"
                )

        elif rule.rule_id == "POL-VEN-002":
            if uro.event_type == EventType.VENDOR_CONCENTRATION_BREACH:
                vd = (raw.get("raw_payload") or {}).get("vendor_risk_detail") or {}
                pct = vd.get("concentration_pct")
                if pct is not None:
                    return (
                        f"MEDIUM: vendor '{vd.get('vendor_name', 'unknown')}' accounts for "
                        f"{pct}% of trailing {vd.get('window_days', 90)}-day P2P spend — exceeds the "
                        f"{vd.get('threshold_pct', 'configured')}% concentration threshold"
                    )

        # ── AI Governance rules ────────────────────────────────────────────────
        elif rule.rule_id == "POL-AI-001":
            if uro.event_type == EventType.AI_ASSESSMENT_OVERDUE:
                ad = (raw.get("raw_payload") or {}).get("ai_governance_detail") or {}
                return (
                    f"HIGH: AI system '{ad.get('system_name', 'unknown')}' "
                    f"({ad.get('risk_tier', 'unknown')} risk) third-party assessment expired on "
                    f"{ad.get('assessment_expires_at', 'unknown date')} — AI-05 assessment lapsed"
                )

        elif rule.rule_id == "POL-AI-002":
            if uro.event_type == EventType.AI_HUMAN_OVERSIGHT_MISSING:
                ad = (raw.get("raw_payload") or {}).get("ai_governance_detail") or {}
                return (
                    f"HIGH: AI system '{ad.get('system_name', 'unknown')}' requires human oversight "
                    "but has no defined human review point on file — AI-06 control gap"
                )

        # ── SailPoint rules ──────────────────────────────────────────────────
        elif rule.rule_id == "POL-SP-001":
            if uro.event_type == EventType.PRIVILEGE_ESCALATION:
                req_id = raw.get("requestId") or raw.get("access_request_id")
                if not req_id:
                    return (
                        f"Privilege escalation for '{uro.actor_id}' has no approved request ID — "
                        "manual override suspected"
                    )

        elif rule.rule_id == "POL-SP-002":
            if uro.event_type == EventType.DORMANT_PRIVILEGED_ACCOUNT:
                days = raw.get("last_login_days") or raw.get("inactiveDays")
                if days is None:
                    return "DORMANT_PRIVILEGED_ACCOUNT event missing 'last_login_days' field"
                if int(days) < 30:
                    return (
                        f"Account inactive for only {days} days — below the 30-day dormancy threshold"
                    )

        elif rule.rule_id == "POL-SP-003":
            if uro.event_type == EventType.ROLE_EXPLOSION:
                role_count = raw.get("role_count") or len(raw.get("roles", []))
                if role_count > 25:
                    return (
                        f"Identity '{uro.actor_id}' holds {role_count} roles — "
                        "exceeds 25-role SoD limit (CRITICAL)"
                    )

        # ── MCP Proxy rules ───────────────────────────────────────────────────
        elif rule.rule_id == "POL-MCP-001":
            if uro.source_system == SourceSystem.MCP_PROXY:
                flags = raw.get("risk_flags") or []
                if "bypass_keyword" in flags:
                    tool = raw.get("target_tool", "unknown")
                    return (
                        f"MCP tool call to '{tool}' contains bypass keyword — "
                        "CI/review suppression detected; audit trail may be incomplete"
                    )

        elif rule.rule_id == "POL-MCP-002":
            if uro.source_system == SourceSystem.MCP_PROXY:
                flags = raw.get("risk_flags") or []
                if "sensitive_tool" in flags:
                    tool = raw.get("target_tool", "unknown")
                    return (
                        f"MCP call to sensitive/destructive tool '{tool}' — "
                        "requires authorization review before execution"
                    )

        elif rule.rule_id == "POL-MCP-003":
            if uro.source_system == SourceSystem.MCP_PROXY:
                exec_ms = raw.get("execution_time_ms")
                if exec_ms is not None and int(exec_ms) > 30_000:
                    tool = raw.get("target_tool", "unknown")
                    return (
                        f"MCP tool '{tool}' SLA breach: {exec_ms:,}ms > 30,000ms threshold — "
                        "potential resource exhaustion or hanging call"
                    )

        elif rule.rule_id == "POL-MCP-004":
            if uro.source_system == SourceSystem.MCP_PROXY:
                if raw.get("status") == "error" and raw.get("error_message"):
                    tool = raw.get("target_tool", "unknown")
                    msg  = str(raw.get("error_message", ""))[:200]
                    return (
                        f"MCP tool '{tool}' returned error — mandatory investigation: {msg}"
                    )

        elif rule.rule_id == "POL-MCP-005":
            if uro.source_system == SourceSystem.MCP_PROXY:
                flags = raw.get("risk_flags") or []
                if len(flags) >= 3:
                    return (
                        f"Compound MCP governance violation: {len(flags)} risk flags fired "
                        f"simultaneously ({', '.join(flags)}) — CRITICAL escalation required"
                    )

        # ── Generic Enterprise System rules ───────────────────────────────────
        elif rule.rule_id == "POL-SYS-001":
            if uro.source_system == SourceSystem.SYSTEM_TELEMETRY:
                flags = raw.get("risk_flags") or []
                if "sod_violation" in flags:
                    return (
                        f"System event on '{raw.get('server_name', 'unknown')}' tagged "
                        "sod_violation — mandatory CRITICAL escalation path applies"
                    )

        elif rule.rule_id == "POL-SYS-002":
            if uro.source_system == SourceSystem.SYSTEM_TELEMETRY:
                flags = raw.get("risk_flags") or []
                if "privileged_access" in flags and str(raw.get("severity", "")).upper() == "CRITICAL":
                    return (
                        f"Privileged-access event on '{raw.get('server_name', 'unknown')}' at "
                        "CRITICAL severity — requires authorization review"
                    )

        elif rule.rule_id == "POL-SYS-003":
            if uro.source_system == SourceSystem.SYSTEM_TELEMETRY:
                flags = raw.get("risk_flags") or []
                if len(flags) >= 2:
                    return (
                        f"Compound generic governance violation: {len(flags)} risk flags fired "
                        f"simultaneously ({', '.join(flags)}) — CRITICAL escalation required"
                    )

        return None  # Rule passed

    # ── Source-specific conformation ──────────────────────────────────────────

    def _conform_sap(self, raw: dict[str, Any], uro: URO) -> ConformedPayload:
        amount = raw.get("AMOUNT") or raw.get("amount")
        return ConformedPayload(
            resource_id=str(raw.get("OBJECT_ID") or raw.get("object_id") or ""),
            resource_type=raw.get("OBJECT_TYPE") or "SAP_OBJECT",
            action=raw.get("TCODE") or raw.get("event_code") or "UNKNOWN",
            outcome=raw.get("outcome") or "unknown",
            risk_indicators={
                "amount":        float(amount) if amount else None,
                "currency":      raw.get("WAERS") or raw.get("currency"),
                "cost_center":   raw.get("KOSTL") or raw.get("cost_center"),
                "company_code":  raw.get("BUKRS") or raw.get("company_code"),
                "approver":      raw.get("APPROVER_ID") or raw.get("approver"),
                "actor_groups":  raw.get("actor_groups", []),
            },
            affected_entities=[str(raw.get("OBJECT_ID", "")), uro.actor_id],
            conformation_rules_applied=["SAP-CDHDR-v1-conform"],
        )

    def _conform_github(self, raw: dict[str, Any], uro: URO) -> ConformedPayload:
        repo = raw.get("repository", {})
        return ConformedPayload(
            resource_id=repo.get("full_name") or str(repo.get("id", "")),
            resource_type="git_repository",
            action=raw.get("X-GitHub-Event") or raw.get("event_type") or "push",
            outcome="success" if not raw.get("error") else "failure",
            risk_indicators={
                "ref":           raw.get("ref"),
                "forced":        raw.get("forced", False),
                "cvss_score":    raw.get("cvss_score") or raw.get("severity_score"),
                # Technology Risk Pipeline: CVE remediation SLA aging (POL-GH-006) —
                # "severity"/"state"/"first_detected_at" are the flat fields that
                # rule reads directly off raw_payload; surfaced here too for
                # dashboards that only look at conformed risk_indicators.
                "severity":        raw.get("severity"),
                "state":           raw.get("state"),
                "first_detected_at": raw.get("first_detected_at") or raw.get("created_at"),
                "secret_type":   raw.get("alert", {}).get("secret_type"),
                "commits_count": len(raw.get("commits", [])),
                "is_admin":      raw.get("sender", {}).get("site_admin", False),
                # DevOps Monitoring: scm_audit_endpoints.py synthesizes a
                # branch_protection_rule event with a "compliance" sub-dict
                # (enforce_admins, required_approving_review_count, ...) —
                # spread here so PaC's devops_monitoring Rego can reference
                # input.event.enforce_admins etc. Absent on real GitHub
                # webhook payloads, so this is a no-op for those.
                **(raw.get("compliance") or {}),
                # DevOps Monitoring: secret_scanner_connectors.py's gitleaks scan
                # (event_type=='gitleaks_scan') attaches redacted findings under
                # "secret_findings" — surfaced as a count + distinct rule ids
                # rather than the findings themselves (which still carry
                # file/commit/author detail useful for triage, but no secret
                # value — already redacted by parse_gitleaks_report()).
                "secret_finding_count": len(raw.get("secret_findings") or []),
                "secret_rule_ids": sorted({
                    f.get("rule_id") for f in (raw.get("secret_findings") or []) if f.get("rule_id")
                }),
                # Technology Risk Pipeline: evidence_endpoints.py's deploy-gate
                # check attaches PR-approval lookup results under "gate_approval".
                **(raw.get("gate_approval") or {}),
            },
            normalized_attributes=self._technology_normalized_attributes(raw),
            affected_entities=[
                repo.get("full_name", ""),
                str(raw.get("sender", {}).get("login", "")),
            ],
            conformation_rules_applied=["GitHub-Webhook-v3-conform"],
        )

    @staticmethod
    def _technology_normalized_attributes(raw: dict[str, Any]) -> "NormalizedAttributes | None":
        """Multi-Domain Risk Pipeline spec's formal metric shape for the
        Technology-domain GitHub checks (deploy-gate bypass, CVE SLA aging) —
        additive, only populated for those events; every other GitHub event
        returns None here (unchanged risk_indicators-only path)."""
        gate = raw.get("gate_approval")
        if gate is not None:
            approved = 1.0 if gate.get("approved") else 0.0
            return NormalizedAttributes(
                entity_id=raw.get("repository", {}).get("full_name"),
                monitored_metric="deploy_gate_approved",
                metric_value=approved,
                threshold_limit=1.0,
                variance=approved - 1.0,
            )

        first_detected = raw.get("first_detected_at") or raw.get("created_at")
        if first_detected:
            try:
                detected_ts = first_detected if isinstance(first_detected, datetime) else datetime.fromisoformat(str(first_detected).replace("Z", "+00:00"))
                if detected_ts.tzinfo is None:
                    detected_ts = detected_ts.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(tz=timezone.utc) - detected_ts).total_seconds() / 86400
            except (ValueError, TypeError):
                return None
            return NormalizedAttributes(
                entity_id=raw.get("repository", {}).get("full_name"),
                monitored_metric="cve_age_days",
                metric_value=round(age_days, 1),
                threshold_limit=14.0,
                variance=round(age_days - 14.0, 1),
            )
        return None

    def _conform_gitlab(self, raw: dict[str, Any], uro: URO) -> ConformedPayload:
        project = raw.get("project", {})
        return ConformedPayload(
            resource_id=project.get("path_with_namespace") or str(project.get("id", "")),
            resource_type="git_repository",
            action=raw.get("X-Gitlab-Event") or raw.get("event_type") or "push",
            outcome="success" if not raw.get("error") else "failure",
            risk_indicators={
                "ref":           raw.get("ref"),
                "commits_count": len(raw.get("commits", [])),
                # Same convention as _conform_github: scm_audit_endpoints.py's
                # synthesized protected_branch_audit event carries its
                # structured findings under "compliance".
                **(raw.get("compliance") or {}),
            },
            affected_entities=[
                project.get("path_with_namespace", ""),
                str(raw.get("user", {}).get("username", "")),
            ],
            conformation_rules_applied=["GitLab-Webhook-v4-conform"],
        )

    def _conform_bitbucket(self, raw: dict[str, Any], uro: URO) -> ConformedPayload:
        repo = raw.get("repository", {})
        return ConformedPayload(
            resource_id=repo.get("full_name") or str(repo.get("uuid", "")),
            resource_type="git_repository",
            action=raw.get("event_type") or "branch_restriction_audit",
            outcome="success" if not raw.get("error") else "failure",
            risk_indicators={
                # Same convention as _conform_github/_conform_gitlab:
                # scm_audit_endpoints.py's synthesized branch_restriction_audit
                # event carries its structured findings under "compliance".
                **(raw.get("compliance") or {}),
            },
            affected_entities=[
                repo.get("full_name", ""),
                str(raw.get("actor", {}).get("username", "")),
            ],
            conformation_rules_applied=["Bitbucket-Webhook-v2-conform"],
        )

    def _conform_sailpoint(self, raw: dict[str, Any], uro: URO) -> ConformedPayload:
        return ConformedPayload(
            resource_id=raw.get("requestedFor", {}).get("id") or uro.actor_id,
            resource_type="identity",
            action=raw.get("action") or raw.get("type") or "UNKNOWN",
            outcome="approved" if raw.get("approved") else "pending",
            risk_indicators={
                "role_count":       raw.get("role_count") or len(raw.get("roles", [])),
                "last_login_days":  raw.get("last_login_days") or raw.get("inactiveDays"),
                "access_request_id": raw.get("requestId") or raw.get("access_request_id"),
                "entitlements":     raw.get("entitlements", []),
                "certification_id": raw.get("certificationId"),
            },
            affected_entities=[
                raw.get("requestedFor", {}).get("id", ""),
                raw.get("requestedBy", {}).get("id", ""),
            ],
            conformation_rules_applied=["SailPoint-IDN-v3-conform"],
        )

    def _conform_mcp_proxy(self, raw: dict[str, Any], uro: URO) -> ConformedPayload:
        risk_flags: list[str] = raw.get("risk_flags") or []
        tool = raw.get("target_tool") or raw.get("method") or "unknown"
        server = raw.get("server_name", "")
        narrative = (
            f"MCP tool '{tool}' on server '{server}' flagged: {', '.join(risk_flags)}"
            if risk_flags
            else f"MCP tool '{tool}' on server '{server}' returned {raw.get('status', 'unknown')}"
        )
        return ConformedPayload(
            resource_id=tool,
            resource_type="mcp_tool",
            action=raw.get("method") or "tools/call",
            outcome=raw.get("status") or "unknown",
            risk_indicators={
                "risk_flags":        risk_flags,
                "flag_count":        len(risk_flags),
                "execution_time_ms": raw.get("execution_time_ms"),
                "server_name":       server,
                "session_id":        str(raw.get("session_id", "")),
                "message_id":        raw.get("message_id"),
                "tool_args_hash":    raw.get("tool_args_hash"),
                "error_message":     raw.get("error_message"),
                "payload_hash":      raw.get("payload_hash"),
                "narrative":         narrative,
            },
            affected_entities=[tool, server, str(raw.get("session_id", ""))],
            conformation_rules_applied=["MCP-Telemetry-v1-conform"],
        )

    def _conform_system_telemetry(self, raw: dict[str, Any], uro: URO) -> ConformedPayload:
        risk_flags: list[str] = raw.get("risk_flags") or []
        server = raw.get("server_name", "")
        event_type = raw.get("event_type") or "unknown_event"
        narrative = (
            f"{raw.get('system_type', 'system')} event '{event_type}' on '{server}' flagged: {', '.join(risk_flags)}"
            if risk_flags
            else f"{raw.get('system_type', 'system')} event '{event_type}' on '{server}'"
        )
        return ConformedPayload(
            resource_id=raw.get("resource") or server,
            resource_type="enterprise_system_resource",
            action=raw.get("action") or event_type,
            outcome=str(raw.get("severity") or "INFO").lower(),
            risk_indicators={
                "risk_flags":   risk_flags,
                "flag_count":   len(risk_flags),
                "severity":     raw.get("severity"),
                "server_name":  server,
                "system_type":  raw.get("system_type"),
                "event_id":     raw.get("event_id"),
                "narrative":    narrative,
                # DevOps Monitoring evidence findings (event_type=='sast_finding')
                # carry rule/CWE detail in the nested raw_payload — surfaced here
                # so PaC's devops_monitoring Rego and the Silver POL-DEVOPS-001
                # rule can reference them without re-parsing raw_payload.
                "rule_id":      (raw.get("raw_payload") or {}).get("rule_id"),
                "cwe":          (raw.get("raw_payload") or {}).get("cwe"),
                # DevOps Monitoring scheduled branch-protection audits (github_scm_tool.py /
                # gitlab_scm_tool.py, event_type=='branch_protection_violation') carry the
                # same normalized compliance dict scm_audit_endpoints.py's on-demand path
                # embeds under raw_event["compliance"] for the GITHUB/GITLAB source-system
                # path — spread the same fields here so the devops_monitoring Rego sees
                # identical input.event.* fields regardless of which path produced the URO.
                # No-op (all None) for every other poll-connector type's telemetry.
                **(raw.get("raw_payload") or {}).get("compliance", {}),
                # ITSM SLA Bridge (itsm_sla_sweep.py, event_type=='sla_breach'):
                # ticket/finding identifiers so the devops_monitoring Rego's
                # deny_sla_breach rule and any downstream review UI can trace
                # the breach back to its ticket without re-parsing raw_payload.
                "external_system":      (raw.get("raw_payload") or {}).get("external_system"),
                "external_ticket_key":  (raw.get("raw_payload") or {}).get("external_ticket_key"),
                "finding_hash":         (raw.get("raw_payload") or {}).get("finding_hash"),
                "sla_due_at":           (raw.get("raw_payload") or {}).get("sla_due_at"),
                # Infrastructure Monitoring (postgres_cis_tool.py/railway_iaas_tool.py,
                # event_type=='infrastructure_finding'): the normalized compliance
                # dict iaas_connectors.normalize_postgres_compliance()/
                # normalize_railway_compliance() produces, spread the same way
                # scm_audit_endpoints.py's "compliance" sub-dict is above.
                **(raw.get("raw_payload") or {}).get("infra_compliance", {}),
                # DevOps Monitoring: Pipeline-as-Code audit (github_pipeline_tool.py,
                # event_type=='pipeline_misconfiguration'): the normalized dict
                # pipeline_security_connectors.normalize_pipeline_compliance()
                # produces. Named distinctly from "compliance" above (SCM branch
                # protection) since a single poll tick's telemetry row can't hold
                # both under the same key.
                **(raw.get("raw_payload") or {}).get("pipeline_compliance", {}),
                # Financial Risk Pipeline (predictive_analytics_tool.py, event_type
                # in JE_VELOCITY_ANOMALY/LIQUIDITY_SHIFT/INVENTORY_DIVERGENCE): the
                # calculation function's own result dict (z_score, rag_status, ...).
                # Named distinctly from infra_compliance/pipeline_compliance above.
                **(raw.get("raw_payload") or {}).get("financial_compliance", {}),
                # Hire-to-Retire (oracle_hcm_tool.py, event_type in
                # GHOST_EMPLOYEE_SUSPECTED/UNAUTHORIZED_PAY_RATE_CHANGE/
                # TERMINATED_EMPLOYEE_ACCESS_RETAINED/SOD_VIOLATION): payroll
                # finding detail — employee/pay-rate/termination fields the
                # hire_to_retire Rego module and POL-HR-* rules read.
                **(raw.get("raw_payload") or {}).get("payroll_detail", {}),
                # Treasury & Cash Management (oracle_fusion_tool.py, event_type
                # in WIRE_TRANSFER_SINGLE_APPROVAL/BANK_RECON_OVERDUE/
                # FX_HEDGE_DOCUMENTATION_MISSING): routed into record_to_report
                # (see mcp_governance._SOURCE_EVENT_TO_PAC_PROCESS) alongside
                # the Financial Risk Pipeline's financial_compliance fields.
                **(raw.get("raw_payload") or {}).get("treasury_detail", {}),
                # Export Control / Trade Compliance (denied_party_screening_tool.py,
                # event_type == EXPORT_CONTROL_MATCH): CSL screening hit detail.
                **(raw.get("raw_payload") or {}).get("trade_compliance_detail", {}),
                # Continuous Third-Party/Vendor Risk (vendor_risk_sweep.py /
                # oracle_fusion_tool.py, event_type in VENDOR_SOC2_EXPIRED/
                # VENDOR_CONCENTRATION_BREACH): routed into procure_to_pay.
                **(raw.get("raw_payload") or {}).get("vendor_risk_detail", {}),
                # AI Governance (ai_governance_sweep.py / ai_governance_endpoints.py,
                # event_type in AI_ASSESSMENT_OVERDUE/AI_HUMAN_OVERSIGHT_MISSING):
                # routed into itgc.
                **(raw.get("raw_payload") or {}).get("ai_governance_detail", {}),
                # Order-to-Cash / Procure-to-Pay (generate_o2c_p2p_synthetic_log.py,
                # event_type in REVENUE_RECOGNITION_EVENT/SALES_ORDER_CREDIT_EVENT/
                # BILLING_EVENT/CASH_APPLICATION_EVENT/CUSTOMER_MASTER_CHANGE/
                # AR_AGING_EVENT/PURCHASE_ORDER_EVENT/INVOICE_MATCH_EVENT/
                # VENDOR_MASTER_CHANGE/PAYMENT_RUN_EVENT/PROCUREMENT_SOD_CONFLICT):
                # routed into order_to_cash/procure_to_pay. Flat po_*/inv_*/so_*/
                # cash_*/ar_*/contract_*/txn_*-prefixed fields, matching every
                # other sub-dict above — see pac_contracts.py's
                # _ERP_TRANSACTION_FIELDS for the full declared set.
                **(raw.get("raw_payload") or {}).get("erp_transaction_detail", {}),
            },
            normalized_attributes=self._financial_normalized_attributes(raw),
            affected_entities=[server, str(raw.get("actor", ""))],
            conformation_rules_applied=["System-Telemetry-v1-conform"],
        )

    @staticmethod
    def _financial_normalized_attributes(raw: dict[str, Any]) -> "NormalizedAttributes | None":
        """Multi-Domain Risk Pipeline spec's formal metric shape for the
        Financial Risk Pipeline's three checks — additive, only populated for
        those events; every other system_telemetry event returns None here."""
        fc = (raw.get("raw_payload") or {}).get("financial_compliance")
        if not fc:
            return None
        # compute_liquidity_shift's top-level key is worst_z_score (it picks
        # the worse of current-ratio/quick-ratio); the other two checks use
        # z_score directly — see predictive_analytics_tool.py.
        z = fc.get("z_score") if fc.get("z_score") is not None else fc.get("worst_z_score")
        if z is None:
            return None
        threshold = 3.0 if raw.get("event_type") in ("je_velocity_anomaly", "inventory_divergence") else -3.0
        return NormalizedAttributes(
            entity_id=raw.get("resource") or raw.get("server_name"),
            monitored_metric="z_score",
            metric_value=z,
            threshold_limit=threshold,
            variance=round(z - threshold, 4),
        )

    def _conform_generic(self, raw: dict[str, Any], uro: URO) -> ConformedPayload:
        return ConformedPayload(
            resource_id=str(raw.get("resource_id", "")),
            resource_type=raw.get("resource_type", "unknown"),
            action=raw.get("action", "unknown"),
            outcome=raw.get("outcome", "unknown"),
            risk_indicators={k: v for k, v in raw.items() if k not in (
                "timestamp", "actor_id", "resource_id", "action", "outcome"
            )},
            conformation_rules_applied=["generic-conform"],
        )
