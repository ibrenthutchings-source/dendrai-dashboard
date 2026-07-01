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
            SourceSystem.SAILPOINT:  self._conform_sailpoint,
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
                "secret_type":   raw.get("alert", {}).get("secret_type"),
                "commits_count": len(raw.get("commits", [])),
                "is_admin":      raw.get("sender", {}).get("site_admin", False),
            },
            affected_entities=[
                repo.get("full_name", ""),
                str(raw.get("sender", {}).get("login", "")),
            ],
            conformation_rules_applied=["GitHub-Webhook-v3-conform"],
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
