"""
Bronze Layer — raw ingestion and URO mapping.

Each source system gets its own ingestion handler. All handlers:
  1. Accept the verbatim source event dict
  2. Extract header fields (actor, timestamp, event_type)
  3. Wrap content in RawPayload (checksum computed automatically)
  4. Return a URO at BRONZE stage — no cleaning, no transformation

The BronzeIngestionLayer class is the dispatcher that routes a raw event
to the correct handler based on the `source_system` field.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..models.uro import (
    ActorType,
    CloudEnvironment,
    ConformedPayload,
    EventType,
    PipelineStage,
    RawPayload,
    RiskDomain,
    SourceSystem,
    URO,
)
from .base import BronzeLayerBase


# ── Per-Source Ingestion Handlers ─────────────────────────────────────────────

class SAPBronzeHandler(BronzeLayerBase):
    """Ingests SAP audit log entries (CDHDR / CDPOS schema)."""

    source_system = SourceSystem.SAP

    # SAP action codes → EventType mapping
    _ACTION_MAP: dict[str, EventType] = {
        "VENDOR_CHANGE":   EventType.VENDOR_MASTER_CHANGE,
        "JRNL_ANOMALY":    EventType.JOURNAL_ENTRY_ANOMALY,
        "SOD_VIOLATION":   EventType.SOD_VIOLATION,
        "PERIOD_OVERRIDE": EventType.PERIOD_CLOSE_OVERRIDE,
        "PAY_THRESHOLD":   EventType.PAYMENT_THRESHOLD_BREACH,
    }

    async def ingest(self, raw_event: dict[str, Any]) -> URO:
        ts_raw = raw_event.get("UZEIT") or raw_event.get("timestamp")
        ts = _parse_ts(ts_raw)

        event_code = str(raw_event.get("TCODE") or raw_event.get("event_code", ""))
        event_type = self._ACTION_MAP.get(event_code, EventType.ANOMALY)

        actor = str(raw_event.get("UNAME") or raw_event.get("actor_id", "UNKNOWN"))

        env = CloudEnvironment(
            provider=raw_event.get("env_provider", "On-Prem"),
            region=raw_event.get("env_region"),
            account_id=raw_event.get("sap_client"),
            tags={"landscape": raw_event.get("sap_landscape", "PRD")},
        )

        return URO(
            timestamp=ts,
            source_system=SourceSystem.SAP,
            event_type=event_type,
            actor_id=actor,
            actor_type=ActorType.HUMAN,
            environment=env,
            raw_payload=RawPayload(
                content=raw_event,
                schema_version="SAP-CDHDR-v1",
            ),
            pipeline_stage=PipelineStage.BRONZE,
        )


class GitHubBronzeHandler(BronzeLayerBase):
    """Ingests GitHub webhook payloads (push, secret_scanning, branch_protection)."""

    source_system = SourceSystem.GITHUB

    _ACTION_MAP: dict[str, EventType] = {
        "secret_scanning_alert":  EventType.SECRET_DETECTED,
        "branch_protection_rule": EventType.BRANCH_PROTECTION_BYPASSED,
        "push":                   EventType.FORCE_PUSH_MAIN,
        "dependabot_alert":       EventType.DEPENDENCY_VULNERABILITY,
        "pull_request_review":    EventType.CODE_REVIEW_BYPASSED,
        # DevOps Monitoring: scm_audit_endpoints.py's on-demand pipeline-security
        # audit synthesizes this event name, same convention as branch_protection_rule.
        "workflow_security_audit": EventType.PIPELINE_MISCONFIGURATION,
        # DevOps Monitoring: scm_audit_endpoints.py's on-demand gitleaks secret
        # scan synthesizes this event name — only sent when gitleaks actually
        # found something (see scm_audit_endpoints._run_github_secret_scan),
        # so this mapping firing is itself already a real positive.
        "gitleaks_scan": EventType.SECRET_DETECTED,
        # Technology Risk Pipeline: evidence_endpoints.py's attestation-ingest
        # path synthesizes this event when a deployed commit has no
        # approved pull request behind it (see scm_audit_endpoints.py's
        # deploy-gate check, scm_connectors.fetch_github_commit_pr_approval).
        "deploy_gate_audit": EventType.DEPLOY_GATE_BYPASSED,
    }

    async def ingest(self, raw_event: dict[str, Any]) -> URO:
        ts_raw = (
            raw_event.get("created_at")
            or raw_event.get("pushed_at")
            or raw_event.get("timestamp")
        )
        ts = _parse_ts(ts_raw)

        gh_event = str(raw_event.get("X-GitHub-Event") or raw_event.get("event_type", "push"))
        event_type = self._ACTION_MAP.get(gh_event, EventType.ANOMALY)

        # GitHub actors can be users or GitHub Actions bots
        actor_login = (
            raw_event.get("sender", {}).get("login")
            or raw_event.get("pusher", {}).get("name")
            or raw_event.get("actor", "UNKNOWN")
        )
        actor_type = (
            ActorType.SERVICE
            if str(actor_login).endswith("[bot]")
            else ActorType.HUMAN
        )

        repo = raw_event.get("repository", {})
        env = CloudEnvironment(
            provider="GitHub",
            account_id=str(repo.get("id", "")),
            tags={
                "org":        raw_event.get("organization", {}).get("login", ""),
                "repo":       repo.get("full_name", ""),
                "visibility": repo.get("visibility", "private"),
            },
        )

        # Multi-Domain Risk Pipeline classification — additive, only for the
        # Technology-domain events (deploy-gate audit, real Dependabot alert
        # webhooks); every other GitHub event (branch_protection_rule,
        # gitleaks_scan, push, ...) keeps domain=None exactly as before.
        _TECH_SUB_DOMAIN = {"deploy_gate_audit": "CI_CD", "dependabot_alert": "CVE_SLA"}
        domain = RiskDomain.TECHNOLOGY if gh_event in _TECH_SUB_DOMAIN else None
        sub_domain = _TECH_SUB_DOMAIN.get(gh_event)

        return URO(
            timestamp=ts,
            source_system=SourceSystem.GITHUB,
            event_type=event_type,
            actor_id=str(actor_login),
            actor_type=actor_type,
            environment=env,
            domain=domain,
            sub_domain=sub_domain,
            raw_payload=RawPayload(
                content=raw_event,
                schema_version="GitHub-Webhook-v3",
            ),
            pipeline_stage=PipelineStage.BRONZE,
        )


class GitLabBronzeHandler(BronzeLayerBase):
    """
    Ingests GitLab events — both real webhook payloads and the synthetic
    branch-protection audit events scm_audit_endpoints.py produces (same
    shape convention as GitHubBronzeHandler's synthetic branch_protection_rule
    events: a "compliance" sub-dict carrying the structured check results,
    consumed by SilverConformationLayer._conform_gitlab).
    """

    source_system = SourceSystem.GITLAB

    _ACTION_MAP: dict[str, EventType] = {
        "protected_branch_audit": EventType.BRANCH_PROTECTION_BYPASSED,
        "merge_request":          EventType.CODE_REVIEW_BYPASSED,
        "push":                   EventType.FORCE_PUSH_MAIN,
        "vulnerability":          EventType.DEPENDENCY_VULNERABILITY,
    }

    async def ingest(self, raw_event: dict[str, Any]) -> URO:
        ts_raw = (
            raw_event.get("created_at")
            or raw_event.get("timestamp")
        )
        ts = _parse_ts(ts_raw)

        gl_event = str(raw_event.get("X-Gitlab-Event") or raw_event.get("event_type", "push"))
        event_type = self._ACTION_MAP.get(gl_event, EventType.ANOMALY)

        actor_login = (
            raw_event.get("user", {}).get("username")
            or raw_event.get("actor", "UNKNOWN")
        )

        project = raw_event.get("project", {})
        env = CloudEnvironment(
            provider="GitLab",
            account_id=str(project.get("id", "")),
            tags={
                "namespace":  project.get("namespace", ""),
                "repo":       project.get("path_with_namespace", ""),
                "visibility": project.get("visibility", "private"),
            },
        )

        return URO(
            timestamp=ts,
            source_system=SourceSystem.GITLAB,
            event_type=event_type,
            actor_id=str(actor_login),
            actor_type=ActorType.HUMAN,
            environment=env,
            raw_payload=RawPayload(
                content=raw_event,
                schema_version="GitLab-Webhook-v4",
            ),
            pipeline_stage=PipelineStage.BRONZE,
        )


class BitbucketBronzeHandler(BronzeLayerBase):
    """
    Ingests the synthetic branch-restriction audit events
    scm_audit_endpoints.py produces (same shape convention as
    GitHubBronzeHandler/GitLabBronzeHandler's synthetic events: a
    "compliance" sub-dict carrying the structured check results, consumed by
    SilverConformationLayer._conform_bitbucket). Bitbucket Cloud has no
    equivalent push-based webhook this platform subscribes to yet, so this
    handler only ever sees on-demand "run now" audits, not live webhooks.
    """

    source_system = SourceSystem.BITBUCKET

    _ACTION_MAP: dict[str, EventType] = {
        "branch_restriction_audit": EventType.BRANCH_PROTECTION_BYPASSED,
    }

    async def ingest(self, raw_event: dict[str, Any]) -> URO:
        ts_raw = raw_event.get("created_at") or raw_event.get("timestamp")
        ts = _parse_ts(ts_raw)

        bb_event = str(raw_event.get("event_type", "branch_restriction_audit"))
        event_type = self._ACTION_MAP.get(bb_event, EventType.ANOMALY)

        actor_login = (
            raw_event.get("actor", {}).get("username")
            or raw_event.get("actor", "UNKNOWN")
        )

        repo = raw_event.get("repository", {})
        env = CloudEnvironment(
            provider="Bitbucket",
            account_id=str(repo.get("uuid", "")),
            tags={
                "workspace":  repo.get("workspace", ""),
                "repo":       repo.get("full_name", ""),
                "visibility": "private" if repo.get("is_private", True) else "public",
            },
        )

        return URO(
            timestamp=ts,
            source_system=SourceSystem.BITBUCKET,
            event_type=event_type,
            actor_id=str(actor_login),
            actor_type=ActorType.HUMAN,
            environment=env,
            raw_payload=RawPayload(
                content=raw_event,
                schema_version="Bitbucket-Audit-v2",
            ),
            pipeline_stage=PipelineStage.BRONZE,
        )


class SailPointBronzeHandler(BronzeLayerBase):
    """Ingests SailPoint IdentityNow activity stream events."""

    source_system = SourceSystem.SAILPOINT

    _ACTION_MAP: dict[str, EventType] = {
        "ROLE_ADDED":            EventType.PRIVILEGE_ESCALATION,
        "ACCESS_REQUEST_DENIED": EventType.ACCESS_CERTIFICATION_FAIL,
        "ACCOUNT_ORPHANED":      EventType.ORPHANED_ACCOUNT,
        "DORMANT_PRIV_ACCOUNT":  EventType.DORMANT_PRIVILEGED_ACCOUNT,
        "ROLE_EXPLOSION":        EventType.ROLE_EXPLOSION,
    }

    async def ingest(self, raw_event: dict[str, Any]) -> URO:
        ts_raw = raw_event.get("created") or raw_event.get("timestamp")
        ts = _parse_ts(ts_raw)

        action = str(raw_event.get("action") or raw_event.get("type", ""))
        event_type = self._ACTION_MAP.get(action, EventType.POLICY_VIOLATION)

        actor = (
            raw_event.get("requestedFor", {}).get("id")
            or raw_event.get("actor")
            or "UNKNOWN"
        )

        env = CloudEnvironment(
            provider="SailPoint",
            tenant_id=raw_event.get("org"),
            tags={"pod": raw_event.get("pod", "")},
        )

        return URO(
            timestamp=ts,
            source_system=SourceSystem.SAILPOINT,
            event_type=event_type,
            actor_id=str(actor),
            actor_type=ActorType.HUMAN,
            environment=env,
            raw_payload=RawPayload(
                content=raw_event,
                schema_version="SailPoint-IDN-v3",
            ),
            pipeline_stage=PipelineStage.BRONZE,
        )


class McpProxyBronzeHandler(BronzeLayerBase):
    """
    Ingests flagged rows from observability.mcp_telemetry.

    Each row represents one JSON-RPC 2.0 message that the telemetry proxy
    tagged with at least one Risk-as-Code governance flag.  Multiple simultaneous
    flags produce a compound MCP_GOVERNANCE_VIOLATION event type.
    """

    source_system = SourceSystem.MCP_PROXY

    # Priority-ordered: most severe flag drives the EventType when only one fires
    _FLAG_EVENT_MAP: dict[str, EventType] = {
        "bypass_keyword": EventType.MCP_TOOL_BYPASS,
        "sensitive_tool": EventType.MCP_SENSITIVE_TOOL_CALL,
        "bulk_args":      EventType.MCP_BULK_ARGS,
        "large_payload":  EventType.MCP_LARGE_PAYLOAD,
    }

    async def ingest(self, raw_event: dict[str, Any]) -> URO:
        ts = _parse_ts(raw_event.get("ts") or raw_event.get("timestamp"))

        risk_flags: list[str] = raw_event.get("risk_flags") or []

        if len(risk_flags) >= 3:
            event_type = EventType.MCP_GOVERNANCE_VIOLATION
        elif risk_flags:
            # Walk priority map; fall back to error or anomaly
            event_type = next(
                (self._FLAG_EVENT_MAP[f] for f in self._FLAG_EVENT_MAP if f in risk_flags),
                EventType.MCP_TOOL_ERROR,
            )
        elif raw_event.get("status") == "error":
            event_type = EventType.MCP_TOOL_ERROR
        else:
            event_type = EventType.ANOMALY

        # actor_id = session UUID — the identity of the client session that made the call
        actor_id = str(raw_event.get("session_id", "UNKNOWN"))

        env = CloudEnvironment(
            provider="MCP",
            tags={
                "server_name": raw_event.get("server_name", ""),
                "session_id":  actor_id,
                "method":      raw_event.get("method", ""),
            },
        )

        return URO(
            timestamp=ts,
            source_system=SourceSystem.MCP_PROXY,
            event_type=event_type,
            actor_id=actor_id,
            actor_type=ActorType.SERVICE,
            environment=env,
            raw_payload=RawPayload(
                content={
                    # Normalise UUID fields to strings for JSON-serialisability
                    **{k: str(v) if hasattr(v, "hex") else v for k, v in raw_event.items()}
                },
                schema_version="MCP-Telemetry-v1",
            ),
            pipeline_stage=PipelineStage.BRONZE,
        )


class SystemTelemetryBronzeHandler(BronzeLayerBase):
    """
    Ingests flagged rows from observability.system_telemetry — the generic REST
    ingest path any monitored system (Saviynt, SAP, Oracle Fusion, ServiceNow,
    Workday, Entra, custom) pushes events to via its per-system ingest_api_key.

    Detection rules run at ingest time (_detect_system_flags in mcp_governance.py)
    tag each row with 0+ of: privileged_access, sod_violation, sensitive_resource,
    policy_violation. Multiple simultaneous flags produce a compound
    SYSTEM_GOVERNANCE_VIOLATION event type, mirroring McpProxyBronzeHandler.
    """

    source_system = SourceSystem.SYSTEM_TELEMETRY

    # Priority-ordered: most severe flag drives the EventType when only one fires.
    # Reuses existing EventTypes where the semantics already match (SOD_VIOLATION,
    # PRIVILEGE_ESCALATION, POLICY_VIOLATION already carry Gold-layer base weights).
    _FLAG_EVENT_MAP: dict[str, EventType] = {
        "sod_violation":      EventType.SOD_VIOLATION,
        "privileged_access":  EventType.PRIVILEGE_ESCALATION,
        "sensitive_resource": EventType.SENSITIVE_RESOURCE_ACCESS,
        "policy_violation":   EventType.POLICY_VIOLATION,
        # DevOps Monitoring: scheduled poll-connector audits (github_scm_tool.py /
        # gitlab_scm_tool.py) and evidence_endpoints.py's SARIF webhook both ride
        # this generic system_telemetry path — see mcp_governance._detect_system_flags.
        "branch_protection_violation": EventType.BRANCH_PROTECTION_BYPASSED,
        "sast_finding":                EventType.SAST_FINDING,
        # ITSM SLA Bridge: itsm_sla_sweep.py re-ingests an overdue ticket's
        # underlying finding tagged with this flag — see risk_waiver_sweep.py's
        # near-identical "waiver expired, re-open as failing" precedent.
        "sla_breach":                  EventType.SLA_BREACH,
        # Infrastructure Monitoring: postgres_cis_tool.py / railway_iaas_tool.py
        # poll-connector audits set this explicit flag on findings.
        "infrastructure_finding":      EventType.INFRASTRUCTURE_FINDING,
        # DevOps Monitoring: github_scm_tool.py's scheduled poll additionally
        # runs a pipeline-as-code (workflow YAML) audit each tick alongside
        # its branch-protection check — see pipeline_security_connectors.py.
        "pipeline_misconfiguration":   EventType.PIPELINE_MISCONFIGURATION,
        # Financial Risk Pipeline: predictive_analytics_tool.py's three
        # calculation functions set the matching flag on the ingested event.
        "je_velocity_anomaly":  EventType.JE_VELOCITY_ANOMALY,
        "liquidity_shift":      EventType.LIQUIDITY_SHIFT,
        "inventory_divergence": EventType.INVENTORY_DIVERGENCE,
        # Hire-to-Retire: oracle_hcm_tool.py sets these explicit flags on
        # payroll findings. A payroll SoD conflict (same actor can both
        # create and approve a pay-rate change) sets the existing generic
        # "sod_violation" flag above rather than a payroll-specific one.
        "ghost_employee_suspected":            EventType.GHOST_EMPLOYEE_SUSPECTED,
        "unauthorized_pay_rate_change":         EventType.UNAUTHORIZED_PAY_RATE_CHANGE,
        "terminated_employee_access_retained":  EventType.TERMINATED_EMPLOYEE_ACCESS_RETAINED,
        # Treasury & Cash Management: oracle_fusion_tool.py's treasury checks
        # set these explicit flags — same producer-driven pattern as every
        # other flag in this map.
        "wire_transfer_single_approval": EventType.WIRE_TRANSFER_SINGLE_APPROVAL,
        "bank_recon_overdue":            EventType.BANK_RECON_OVERDUE,
        "fx_hedge_documentation_missing": EventType.FX_HEDGE_DOCUMENTATION_MISSING,
        # Export Control / Trade Compliance: denied_party_screening_tool.py's
        # Consolidated Screening List match.
        "export_control_match": EventType.EXPORT_CONTROL_MATCH,
    }

    # Multi-Domain Risk Pipeline classification for the Financial-domain flags
    # above — additive, only for those three; every other system_telemetry
    # event (SoD, privileged access, DevOps/Infra findings, ...) keeps
    # domain=None exactly as before.
    _FINANCIAL_FLAGS = {"je_velocity_anomaly", "liquidity_shift", "inventory_divergence"}

    async def ingest(self, raw_event: dict[str, Any]) -> URO:
        ts = _parse_ts(raw_event.get("created_at") or raw_event.get("timestamp"))

        risk_flags: list[str] = raw_event.get("risk_flags") or []

        if len(risk_flags) >= 2:
            event_type = EventType.SYSTEM_GOVERNANCE_VIOLATION
        elif risk_flags:
            event_type = next(
                (self._FLAG_EVENT_MAP[f] for f in self._FLAG_EVENT_MAP if f in risk_flags),
                EventType.POLICY_VIOLATION,
            )
        else:
            event_type = EventType.ANOMALY

        actor = str(raw_event.get("actor") or "UNKNOWN")

        env = CloudEnvironment(
            provider=raw_event.get("system_type", "custom"),
            tags={
                "server_name": raw_event.get("server_name", ""),
                "event_type":  raw_event.get("event_type", ""),
                "severity":    raw_event.get("severity", "INFO"),
            },
        )

        domain = RiskDomain.FINANCIAL if self._FINANCIAL_FLAGS.intersection(risk_flags) else None
        sub_domain = "RECORD_TO_REPORT" if domain else None

        return URO(
            timestamp=ts,
            source_system=SourceSystem.SYSTEM_TELEMETRY,
            event_type=event_type,
            actor_id=actor,
            actor_type=ActorType.HUMAN,
            environment=env,
            domain=domain,
            sub_domain=sub_domain,
            raw_payload=RawPayload(
                content={
                    **{k: str(v) if hasattr(v, "hex") else v for k, v in raw_event.items()}
                },
                schema_version="System-Telemetry-v1",
            ),
            pipeline_stage=PipelineStage.BRONZE,
        )


# ── Bronze Dispatcher ─────────────────────────────────────────────────────────

class BronzeIngestionLayer:
    """
    Routes raw source events to the correct per-source Bronze handler.

    Usage:
        layer = BronzeIngestionLayer()
        uro = await layer.ingest(raw_event, source_system=SourceSystem.SAP)
    """

    def __init__(self) -> None:
        self._handlers: dict[SourceSystem, BronzeLayerBase] = {
            SourceSystem.SAP:        SAPBronzeHandler(),
            SourceSystem.GITHUB:     GitHubBronzeHandler(),
            SourceSystem.GITLAB:     GitLabBronzeHandler(),
            SourceSystem.BITBUCKET:  BitbucketBronzeHandler(),
            SourceSystem.SAILPOINT:  SailPointBronzeHandler(),
            SourceSystem.MCP_PROXY:  McpProxyBronzeHandler(),
            SourceSystem.SYSTEM_TELEMETRY: SystemTelemetryBronzeHandler(),
        }

    async def ingest(
        self,
        raw_event: dict[str, Any],
        source_system: SourceSystem,
        correlation_id: str | None = None,
    ) -> URO:
        handler = self._handlers.get(source_system)
        if handler is None:
            # Fallback: generic UNKNOWN handler preserves the raw payload
            return _generic_ingest(raw_event, source_system, correlation_id)

        uro = await handler.ingest(raw_event)

        # Attach correlation_id if provided (e.g. from an upstream alert ID)
        if correlation_id:
            uro = uro.model_copy(update={"correlation_id": correlation_id})

        return uro

    async def ingest_batch(
        self,
        events: list[dict[str, Any]],
        source_system: SourceSystem,
    ) -> list[URO]:
        import asyncio
        return await asyncio.gather(
            *[self.ingest(e, source_system) for e in events]
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_ts(raw: Any) -> datetime:
    """Best-effort datetime parse from heterogeneous source timestamp formats."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        # Unix epoch seconds
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str):
        for fmt in (
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(raw, fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return datetime.now(tz=timezone.utc)


def _generic_ingest(
    raw_event: dict[str, Any],
    source_system: SourceSystem,
    correlation_id: str | None,
) -> URO:
    return URO(
        correlation_id=correlation_id,
        timestamp=_parse_ts(raw_event.get("timestamp")),
        source_system=source_system,
        event_type=EventType.ANOMALY,
        actor_id=str(raw_event.get("actor_id", "UNKNOWN")),
        environment=CloudEnvironment(provider="UNKNOWN"),
        raw_payload=RawPayload(content=raw_event),
        pipeline_stage=PipelineStage.BRONZE,
    )
