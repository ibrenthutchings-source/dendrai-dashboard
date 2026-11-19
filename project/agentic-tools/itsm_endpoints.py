#!/usr/bin/env python3
"""
DevOps Monitoring: ITSM/Jira-ServiceNow Ticket Sync.

observability.itsm_tickets and its full CRUD (db.py: create_itsm_ticket,
list_itsm_tickets, get_itsm_ticket, get_itsm_ticket_by_external_key,
update_itsm_ticket_status, get_open_ticket_for_finding, expire_overdue_sla)
already existed with no endpoint ever calling them — this file is that
endpoint. It covers the real-time webhook half of the "ITSM/Jira-ServiceNow
SLA Bridge" the README describes; the other half — an hourly sweep calling
db.expire_overdue_sla() to catch tickets that missed their SLA without a
status-change event, and poll-based itsm_jira_tool.py/itsm_servicenow_tool.py
connectors for systems that can't push webhooks — is not built yet (see
db.update_itsm_ticket_status's own docstring, which already anticipates
those two poll adapters by name).

POST /itsm/webhook
    Real-time ticket status push from a Jira Automation rule / ServiceNow
    Business Rule. Reconciles our tracked ticket the moment its status
    changes, rather than waiting for a poll that doesn't exist yet.

POST /itsm/tickets
    Open a new tracked ticket for a finding (manual/on-demand path — an
    auditor filing a ticket against an evidence finding or SCM exception and
    wanting its SLA tracked here). One open ticket per finding_hash
    (idx_itsm_tickets_active_hash); reuses the existing open one if present
    rather than erroring, since a caller retrying after a timeout shouldn't
    fail just because the first attempt actually succeeded.

Auth (webhook only): Authorization: Bearer <ingest_api_key> — the same
per-system Monitored Systems mechanism POST /observability/telemetry/ingest
and POST /evidence/webhook both use.

Router prefix: /itsm

    POST /itsm/webhook          Real-time ticket status push
    GET  /itsm/tickets          Filtered list (status/external_system/breached_only)
    GET  /itsm/tickets/{id}     One ticket
    POST /itsm/tickets          Open a new tracked ticket for a finding
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import db
import mcp_governance
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.itsm")

router = APIRouter(prefix="/itsm", tags=["DevOps Monitoring: ITSM"])

# Same reasoning as evidence_endpoints._SCREEN_ID: no dedicated nav item for
# ticket tracking yet, gated on the closest existing real screen instead.
_SCREEN_ID = "infrastructuremonitoring"

_VALID_STATUSES = {"open", "in_progress", "resolved", "closed", "cancelled"}
_VALID_SYSTEMS = {"jira", "servicenow"}


class ItsmWebhookRequest(BaseModel):
    external_system: str
    external_ticket_key: str
    status: str


class CreateTicketRequest(BaseModel):
    finding_hash: str
    external_system: str
    external_ticket_key: str
    summary: Optional[str] = None
    severity: str = "MEDIUM"
    sla_hours: int = 72


@router.post("/webhook")
async def itsm_webhook(req: ItsmWebhookRequest, request: Request):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    status = req.status.strip().lower()
    if status not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(_VALID_STATUSES)}")
    system_name = req.external_system.strip().lower()
    if system_name not in _VALID_SYSTEMS:
        raise HTTPException(status_code=422, detail=f"external_system must be one of {sorted(_VALID_SYSTEMS)}")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <ingest_api_key>")
    api_key = auth_header[len("Bearer "):].strip()
    system = await asyncio.to_thread(mcp_governance._get_system_by_api_key, api_key)
    if not system:
        raise HTTPException(status_code=401, detail="Invalid ingest API key")

    ticket = await asyncio.to_thread(db.get_itsm_ticket_by_external_key, system_name, req.external_ticket_key)
    if not ticket:
        raise HTTPException(
            status_code=404,
            detail=f"No tracked ticket for {system_name}:{req.external_ticket_key} — open one via POST /itsm/tickets first",
        )

    ok = await asyncio.to_thread(db.update_itsm_ticket_status, ticket["id"], status)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update ticket status")

    logger.info("itsm: %s:%s -> %s (ticket %s)", system_name, req.external_ticket_key, status, ticket["id"])
    return {"received": True, "ticket_id": ticket["id"], "status": status}


@router.get("/tickets")
def list_tickets(
    status: Optional[str] = None, external_system: Optional[str] = None,
    breached_only: bool = False, limit: int = 100,
    current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID)),
):
    if not db.is_available():
        return {"tickets": []}
    return {"tickets": db.list_itsm_tickets(
        status=status, external_system=external_system, breached_only=breached_only, limit=limit,
    )}


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int, current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    ticket = db.get_itsm_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.post("/tickets")
def create_ticket(
    req: CreateTicketRequest,
    current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID, edit=True)),
):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    system_name = req.external_system.strip().lower()
    if system_name not in _VALID_SYSTEMS:
        raise HTTPException(status_code=422, detail=f"external_system must be one of {sorted(_VALID_SYSTEMS)}")
    if req.sla_hours <= 0:
        raise HTTPException(status_code=422, detail="sla_hours must be positive")

    existing = db.get_open_ticket_for_finding(req.finding_hash)
    if existing:
        return {"id": existing["id"], "reused_existing": True}

    sla_due_at = datetime.now(timezone.utc) + timedelta(hours=req.sla_hours)
    ticket_id = db.create_itsm_ticket(
        finding_hash=req.finding_hash, external_system=system_name,
        external_ticket_key=req.external_ticket_key, connector_id=None,
        summary=req.summary, severity=req.severity, sla_hours=req.sla_hours,
        sla_due_at=sla_due_at, created_by=current_user.get("username") or "unknown",
    )
    if not ticket_id:
        raise HTTPException(status_code=500, detail="Failed to create ticket")
    return {"id": ticket_id, "reused_existing": False}
