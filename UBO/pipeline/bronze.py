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

        return URO(
            timestamp=ts,
            source_system=SourceSystem.GITHUB,
            event_type=event_type,
            actor_id=str(actor_login),
            actor_type=actor_type,
            environment=env,
            raw_payload=RawPayload(
                content=raw_event,
                schema_version="GitHub-Webhook-v3",
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
            SourceSystem.SAILPOINT:  SailPointBronzeHandler(),
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
