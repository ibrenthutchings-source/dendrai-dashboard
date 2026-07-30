"""
Universal Risk Object (URO) — canonical data model for the Dendrai UBO Governance Brain.

Every event entering the system — regardless of origin (SAP, GitHub, SailPoint,
FRED, SEC EDGAR) — is immediately mapped into this structure at the Bronze layer.
The dual-payload design (raw + conformed) preserves source fidelity while
enabling uniform downstream processing.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ── Enumerations ──────────────────────────────────────────────────────────────

class SourceSystem(str, Enum):
    SAP            = "SAP"
    GITHUB         = "GITHUB"
    GITLAB         = "GITLAB"
    BITBUCKET      = "BITBUCKET"
    SAILPOINT      = "SAILPOINT"
    FRED           = "FRED"
    SEC_EDGAR      = "SEC_EDGAR"
    ORACLE_FUSION  = "ORACLE_FUSION"
    MCP_PROXY      = "MCP_PROXY"
    SYSTEM_TELEMETRY = "SYSTEM_TELEMETRY"  # generic REST-ingested enterprise systems (Saviynt, SAP, ServiceNow, ...)
    INTERNAL       = "INTERNAL"
    UNKNOWN        = "UNKNOWN"


class EventType(str, Enum):
    # ── SAP Financial Controls ────────────────────────────────
    SOD_VIOLATION              = "SOD_VIOLATION"
    JOURNAL_ENTRY_ANOMALY      = "JOURNAL_ENTRY_ANOMALY"
    VENDOR_MASTER_CHANGE       = "VENDOR_MASTER_CHANGE"
    PAYMENT_THRESHOLD_BREACH   = "PAYMENT_THRESHOLD_BREACH"
    PERIOD_CLOSE_OVERRIDE      = "PERIOD_CLOSE_OVERRIDE"

    # ── GitHub DevSecOps ──────────────────────────────────────
    SECRET_DETECTED            = "SECRET_DETECTED"
    BRANCH_PROTECTION_BYPASSED = "BRANCH_PROTECTION_BYPASSED"
    FORCE_PUSH_MAIN            = "FORCE_PUSH_MAIN"
    DEPENDENCY_VULNERABILITY   = "DEPENDENCY_VULNERABILITY"
    CODE_REVIEW_BYPASSED       = "CODE_REVIEW_BYPASSED"
    # Technology Risk Pipeline: a deployed commit has no associated
    # approved pull request (evidence_endpoints.py's attestation path).
    DEPLOY_GATE_BYPASSED       = "DEPLOY_GATE_BYPASSED"

    # ── DevOps Monitoring: SCM audits + SARIF/SAST evidence ───
    SAST_FINDING               = "SAST_FINDING"

    # ── DevOps Monitoring: pipeline-as-code (CI/CD workflow) audit ────
    PIPELINE_MISCONFIGURATION  = "PIPELINE_MISCONFIGURATION"

    # ── DevOps Monitoring: ITSM SLA Bridge ────────────────────
    SLA_BREACH                 = "SLA_BREACH"

    # ── Infrastructure Monitoring: IaaS/OS/DB continuous audit ─
    INFRASTRUCTURE_FINDING     = "INFRASTRUCTURE_FINDING"

    # ── Financial Risk Pipeline (predictive_analytics_tool.py) ─
    JE_VELOCITY_ANOMALY        = "JE_VELOCITY_ANOMALY"
    LIQUIDITY_SHIFT            = "LIQUIDITY_SHIFT"
    INVENTORY_DIVERGENCE       = "INVENTORY_DIVERGENCE"

    # ── SailPoint Identity ────────────────────────────────────
    PRIVILEGE_ESCALATION       = "PRIVILEGE_ESCALATION"
    ORPHANED_ACCOUNT           = "ORPHANED_ACCOUNT"
    ACCESS_CERTIFICATION_FAIL  = "ACCESS_CERTIFICATION_FAIL"
    DORMANT_PRIVILEGED_ACCOUNT = "DORMANT_PRIVILEGED_ACCOUNT"
    ROLE_EXPLOSION             = "ROLE_EXPLOSION"

    # ── Macro / Market Signals ────────────────────────────────
    MACRO_LEADING_INDICATOR    = "MACRO_LEADING_INDICATOR"
    EDGAR_FILING_ANOMALY       = "EDGAR_FILING_ANOMALY"
    BENEISH_THRESHOLD_BREACH   = "BENEISH_THRESHOLD_BREACH"

    # ── Cross-System ──────────────────────────────────────────
    CASCADING_FAILURE_SIGNAL   = "CASCADING_FAILURE_SIGNAL"
    POLICY_VIOLATION           = "POLICY_VIOLATION"
    ANOMALY                    = "ANOMALY"

    # ── MCP Proxy Governance ──────────────────────────────────
    MCP_TOOL_BYPASS            = "MCP_TOOL_BYPASS"           # bypass_keyword flag
    MCP_SENSITIVE_TOOL_CALL    = "MCP_SENSITIVE_TOOL_CALL"   # destructive/high-risk tool
    MCP_BULK_ARGS              = "MCP_BULK_ARGS"             # > 20 arguments
    MCP_LARGE_PAYLOAD          = "MCP_LARGE_PAYLOAD"         # payload > 50 KB
    MCP_TOOL_ERROR             = "MCP_TOOL_ERROR"            # tool returned error status
    MCP_GOVERNANCE_VIOLATION   = "MCP_GOVERNANCE_VIOLATION"  # 3+ flags simultaneously

    # ── Generic Enterprise Systems (system_telemetry ingest) ──
    SENSITIVE_RESOURCE_ACCESS   = "SENSITIVE_RESOURCE_ACCESS"    # sensitive_resource flag
    SYSTEM_GOVERNANCE_VIOLATION = "SYSTEM_GOVERNANCE_VIOLATION"  # 2+ flags simultaneously

    # ── Hire-to-Retire (oracle_hcm_tool.py) ────────────────────
    # Payroll SoD conflicts reuse the existing SOD_VIOLATION event type (same
    # "sod_violation" risk flag every other source sets) rather than a new
    # PAYROLL_SOD_VIOLATION — see bronze.py's SystemTelemetryBronzeHandler
    # docstring: "reuse existing EventTypes where the semantics already match."
    GHOST_EMPLOYEE_SUSPECTED           = "GHOST_EMPLOYEE_SUSPECTED"
    UNAUTHORIZED_PAY_RATE_CHANGE       = "UNAUTHORIZED_PAY_RATE_CHANGE"
    TERMINATED_EMPLOYEE_ACCESS_RETAINED = "TERMINATED_EMPLOYEE_ACCESS_RETAINED"


class RiskDomain(str, Enum):
    """Four enterprise risk categories from the Multi-Domain Continuous Risk
    Pipeline spec. Optional and additive — existing sources (SAP, GitHub,
    GitLab, Bitbucket, SailPoint, ...) leave `URO.domain` unset; only the new
    Technology/Financial pipelines populate it. OPERATIONAL/STRATEGIC are
    declared now so the enum doesn't need another migration when those
    domains ship, but nothing produces them yet."""
    TECHNOLOGY  = "TECHNOLOGY"
    OPERATIONAL = "OPERATIONAL"
    FINANCIAL   = "FINANCIAL"
    STRATEGIC   = "STRATEGIC"


class PipelineStage(str, Enum):
    BRONZE      = "BRONZE"
    SILVER      = "SILVER"
    GOLD        = "GOLD"
    ADJUDICATED = "ADJUDICATED"


class ActorType(str, Enum):
    HUMAN   = "human"
    SERVICE = "service"
    SYSTEM  = "system"
    UNKNOWN = "unknown"


# ── Supporting Models ─────────────────────────────────────────────────────────

class CloudEnvironment(BaseModel):
    """Multi-cloud context tags for the event's origin environment."""

    provider: str = "UNKNOWN"           # "AWS" | "Azure" | "GCP" | "On-Prem"
    region:     Optional[str] = None    # e.g. "us-east-1", "eastus"
    account_id: Optional[str] = None    # Cloud account / subscription ID
    tenant_id:  Optional[str] = None    # Azure AD tenant, GCP org, AWS org unit
    vpc_id:     Optional[str] = None
    tags: dict[str, str] = Field(default_factory=dict)

    model_config = {"frozen": True}


class RawPayload(BaseModel):
    """
    Immutable capture of the source event.

    This is written once at the Bronze layer and NEVER modified.
    It is the audit-grade source-of-truth for forensic reconstruction.
    """

    content:        dict[str, Any]     # The verbatim JSON/dict from source
    encoding:       str = "utf-8"
    schema_version: Optional[str] = None
    # SHA-256 of the serialised content — set automatically on creation
    checksum:       Optional[str] = None

    model_config = {"frozen": True}

    def model_post_init(self, __context: Any) -> None:
        # Compute checksum if not provided (Pydantic v2 frozen workaround)
        if self.checksum is None:
            raw = json.dumps(self.content, sort_keys=True, default=str).encode()
            object.__setattr__(self, "checksum", hashlib.sha256(raw).hexdigest())


class NormalizedAttributes(BaseModel):
    """Formal metric/threshold shape from the Multi-Domain Continuous Risk
    Pipeline spec's URO. Optional, additive alongside ConformedPayload's
    existing free-form `risk_indicators` dict — new Technology/Financial
    conformers populate this in addition to (not instead of) risk_indicators
    so Gold can compute a real `variance`; existing conformers (GitHub,
    GitLab, Bitbucket, SAP, ...) are unaffected and never set it."""

    entity_id:       Optional[str] = None
    monitored_metric: Optional[str] = None
    metric_value:    Optional[float | str | bool] = None
    threshold_limit: Optional[float | str | bool] = None
    variance:        Optional[float] = None  # computed by Gold when both values are numeric

    model_config = {"frozen": True}


class RiskScoreImpact(BaseModel):
    """Split view of Gold's scoring alongside the existing collapsed
    `URO.risk_score`. Optional/additive — only set for URO's producing
    `NormalizedAttributes`; every other pipeline keeps using the single
    `risk_score` field exactly as it does today."""

    inherent_risk_score:        Optional[float] = None  # pre-mitigation baseline for this metric
    normalized_risk_index_delta: Optional[float] = None  # this event's contribution to the pooled NRI

    model_config = {"frozen": True}


class ConformedPayload(BaseModel):
    """
    Source-agnostic normalised view of the event.

    Populated by the Silver layer after schema conformation rules are applied.
    Fields use a common vocabulary regardless of the originating system.
    """

    # Normalised identifiers
    resource_id:   Optional[str] = None  # What was acted upon
    resource_type: Optional[str] = None  # "user_account", "code_repo", "vendor", …
    action:        Optional[str] = None  # Verb: "escalated", "bypassed", "modified"
    outcome:       Optional[str] = None  # "success" | "failure" | "blocked" | "unknown"

    # Extracted risk signals — free-form but typed
    risk_indicators: dict[str, Any] = Field(default_factory=dict)

    # Multi-Domain Risk Pipeline spec's formal metric shape — optional,
    # additive alongside risk_indicators (see NormalizedAttributes docstring).
    normalized_attributes: Optional[NormalizedAttributes] = None

    # Entities involved (user IDs, system names, account refs)
    affected_entities: list[str] = Field(default_factory=list)

    # Structured annotations added by conformation rules
    conformed_at: datetime = Field(default_factory=datetime.utcnow)
    conformation_rules_applied: list[str] = Field(default_factory=list)
    data_quality_flags: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


# ── Universal Risk Object ─────────────────────────────────────────────────────

class URO(BaseModel):
    """
    Universal Risk Object — the atomic unit of analysis for the Dendrai UBO Governance Brain.

    A URO is born at the Bronze layer (raw ingestion), enriched by the Silver layer
    (conformation + policy validation), scored by the Gold layer (risk metrics),
    and finally adjudicated by the Council of Agents.

    The `pipeline_stage` field tracks exactly where in the Medallion architecture
    this instance currently lives.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    id:             str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Groups causally related events across systems (e.g. a SailPoint escalation
    # that triggers a SAP SoD violation and a GitHub force-push)
    correlation_id: Optional[str] = None
    # ID of the URO that caused this one to be generated (derived events)
    parent_id:      Optional[str] = None

    # ── Temporal ──────────────────────────────────────────────────────────────
    timestamp:   datetime                              # Event time at source
    ingested_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Provenance ────────────────────────────────────────────────────────────
    source_system: SourceSystem
    event_type:    EventType
    actor_id:      str                                 # Who/what triggered the event
    actor_type:    ActorType = ActorType.UNKNOWN

    # ── Multi-Domain Risk Pipeline classification ─────────────────────────────
    # Optional/additive — see RiskDomain docstring. Existing sources leave both unset.
    domain:     Optional[RiskDomain] = None
    sub_domain: Optional[str] = None

    # ── Multi-Cloud Context ───────────────────────────────────────────────────
    environment: CloudEnvironment

    # ── Dual-Container Payload ────────────────────────────────────────────────
    # raw_payload:      immutable, written at Bronze, never touched again
    # conformed_payload: populated by Silver layer conformation rules
    raw_payload:       RawPayload
    conformed_payload: Optional[ConformedPayload] = None

    # ── Risk Scoring (Gold layer) ─────────────────────────────────────────────
    risk_score: Optional[float] = None   # Composite 0.0–1.0
    risk_tier:  Optional[str]  = None   # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    silver_policy_violations: list[str] = Field(default_factory=list)
    # Split view alongside risk_score above — optional/additive, only set
    # when conformed_payload.normalized_attributes was populated.
    risk_score_impact: Optional[RiskScoreImpact] = None

    # ── Adjudication result (Council of Agents) ───────────────────────────────
    # Populated only after the full agent swarm has run
    adjudication: Optional[Any] = None  # AdjudicationResult — typed in risk_intelligence.py

    # ── Pipeline Tracking ─────────────────────────────────────────────────────
    pipeline_stage:   PipelineStage = PipelineStage.BRONZE
    pipeline_version: str           = "1.0.0"

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("risk_score", mode="before")
    @classmethod
    def clamp_risk_score(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            return max(0.0, min(1.0, float(v)))
        return v

    def as_bronze(self) -> "URO":
        return self.model_copy(update={"pipeline_stage": PipelineStage.BRONZE})

    def as_silver(
        self,
        conformed: ConformedPayload,
        violations: list[str],
    ) -> "URO":
        return self.model_copy(update={
            "pipeline_stage":          PipelineStage.SILVER,
            "conformed_payload":       conformed,
            "silver_policy_violations": violations,
        })

    def as_gold(self, score: float, tier: str, risk_score_impact: Optional["RiskScoreImpact"] = None) -> "URO":
        update: dict[str, Any] = {
            "pipeline_stage": PipelineStage.GOLD,
            "risk_score":     score,
            "risk_tier":      tier,
        }
        if risk_score_impact is not None:
            update["risk_score_impact"] = risk_score_impact
        return self.model_copy(update=update)

    def as_adjudicated(self, adjudication: Any) -> "URO":
        return self.model_copy(update={
            "pipeline_stage": PipelineStage.ADJUDICATED,
            "adjudication":   adjudication,
        })

    @property
    def is_high_severity(self) -> bool:
        return self.risk_tier in ("CRITICAL", "HIGH")

    @property
    def payload_summary(self) -> dict[str, Any]:
        """Quick diagnostic view without the full raw payload blob."""
        return {
            "resource":   (self.conformed_payload.resource_id if self.conformed_payload else None),
            "action":     (self.conformed_payload.action      if self.conformed_payload else None),
            "indicators": (self.conformed_payload.risk_indicators if self.conformed_payload else {}),
        }
