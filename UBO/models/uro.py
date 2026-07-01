"""
Universal Risk Object (URO) — canonical data model for the Governance Brain.

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
    SAILPOINT      = "SAILPOINT"
    FRED           = "FRED"
    SEC_EDGAR      = "SEC_EDGAR"
    ORACLE_FUSION  = "ORACLE_FUSION"
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
    Universal Risk Object — the atomic unit of analysis for the Governance Brain.

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

    def as_gold(self, score: float, tier: str) -> "URO":
        return self.model_copy(update={
            "pipeline_stage": PipelineStage.GOLD,
            "risk_score":     score,
            "risk_tier":      tier,
        })

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
