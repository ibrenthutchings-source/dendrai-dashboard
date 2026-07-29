#!/usr/bin/env python3
"""
ITSM/Jira-ServiceNow SLA Bridge — DevOps Monitoring category.

Router prefix: /itsm

    POST /itsm/tickets              Open a real Jira/ServiceNow ticket for a finding
    GET  /itsm/tickets              Filtered list (status, external_system, breached_only)
    GET  /itsm/tickets/{id}         Single ticket
    POST /itsm/tickets/{id}/sync    Resync one ticket's status from the external system now
    POST /itsm/webhook              Real-time ticket-status push from Jira Automation /
                                     ServiceNow Business Rule (Bearer-key auth, same shape
                                     as evidence_endpoints.py's webhook)
    GET  /itsm/sla-summary          Counts for the DevOps Monitoring dashboard

A ticket is opened against a connector already registered in Poll-Based
Connectors (connector_type='itsm_jira'|'itsm_servicenow') — the same
Fernet-encrypted-credential connector used for status-reconciliation polling
(itsm_jira_tool.py/itsm_servicenow_tool.py), so there's exactly one place
Jira/ServiceNow credentials live. SLA breach detection is NOT done here —
that's itsm_sla_sweep.py's independent hourly sweep (db.expire_overdue_sla),
so a ticket that's simply never synced doesn't silently avoid ever being
flagged overdue.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel

import db
import itsm_connectors

router = APIRouter(prefix="/itsm", tags=["ITSM SLA Bridge"])

_JIRA_TYPE = "itsm_jira"
_SERVICENOW_TYPE = "itsm_servicenow"
_EXTERNAL_SYSTEM_BY_CONNECTOR_TYPE = {_JIRA_TYPE: "jira", _SERVICENOW_TYPE: "servicenow"}


class CreateTicketBody(BaseModel):
    finding_hash: str
    connector_id: int
    summary: str
    description: Optional[str] = None
    severity: str = "MEDIUM"
    created_by: Optional[str] = None


@router.post("/tickets")
async def create_ticket(body: CreateTicketBody):
    """Opens a real ticket in the external system, then records it here with
    a computed SLA due date. Reuses an existing open ticket for the same
    finding_hash rather than opening a duplicate (idx_itsm_tickets_active_hash
    would reject the insert anyway)."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    existing = await asyncio.to_thread(db.get_open_ticket_for_finding, body.finding_hash)
    if existing:
        return {"ticket": existing, "created": False, "note": "An open ticket already exists for this finding"}

    try:
        connector = await asyncio.to_thread(db.get_poll_connector, body.connector_id, True)
    except db.EncryptionKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not connector:
        raise HTTPException(status_code=404, detail=f"No connector with id {body.connector_id}")
    connector_type = connector["connector_type"]
    external_system = _EXTERNAL_SYSTEM_BY_CONNECTOR_TYPE.get(connector_type)
    if not external_system:
        raise HTTPException(status_code=400,
                             detail=f"Connector {body.connector_id} is not an ITSM connector (type={connector_type})")

    base_url = connector.get("base_url")
    credentials = connector.get("credentials") or {}
    extra_config = connector.get("extra_config") or {}
    if not base_url:
        raise HTTPException(status_code=400, detail="Connector has no base_url configured")

    try:
        if external_system == "jira":
            project_key = extra_config.get("project_key")
            if not project_key:
                raise HTTPException(status_code=400, detail="Connector extra_config.project_key is required for Jira")
            created = await asyncio.to_thread(
                itsm_connectors.jira_create_issue, base_url, credentials.get("email"),
                credentials.get("api_token"), project_key, body.summary,
                body.description or body.summary,
            )
        else:
            created = await asyncio.to_thread(
                itsm_connectors.servicenow_create_incident, base_url, credentials.get("username"),
                credentials.get("password"), body.summary, body.description or body.summary,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to create {external_system} ticket: {exc}")

    sla_hours = itsm_connectors.sla_hours_for_severity(body.severity)
    sla_due_at = datetime.now(timezone.utc) + timedelta(hours=sla_hours)

    ticket_id = await asyncio.to_thread(
        db.create_itsm_ticket, body.finding_hash, external_system, created["key"], body.connector_id,
        body.summary, body.severity, sla_hours, sla_due_at, body.created_by or "operator",
    )
    ticket = await asyncio.to_thread(db.get_itsm_ticket, ticket_id)
    return {"ticket": ticket, "created": True}


@router.get("/tickets")
async def list_tickets(status: Optional[str] = None, external_system: Optional[str] = None,
                        breached_only: bool = False, limit: int = 100):
    if not db.is_available():
        return {"tickets": []}
    return {"tickets": db.list_itsm_tickets(
        status=status, external_system=external_system, breached_only=breached_only, limit=limit)}


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: int):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    ticket = db.get_itsm_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.post("/tickets/{ticket_id}/sync")
async def sync_ticket(ticket_id: int):
    """Resync one ticket's status from the external system right now, instead
    of waiting for the next scheduled poll — the manual 'Test'-button
    equivalent for a single ticket."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    ticket = db.get_itsm_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if not ticket.get("connector_id"):
        raise HTTPException(status_code=400, detail="Ticket has no linked connector to sync from")

    try:
        connector = await asyncio.to_thread(db.get_poll_connector, ticket["connector_id"], True)
    except db.EncryptionKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not connector:
        raise HTTPException(status_code=404, detail="Linked connector no longer exists")

    base_url = connector.get("base_url")
    credentials = connector.get("credentials") or {}
    try:
        if ticket["external_system"] == "jira":
            new_status = await asyncio.to_thread(
                itsm_connectors.jira_get_issue_status, base_url, credentials.get("email"),
                credentials.get("api_token"), ticket["external_ticket_key"],
            )
        else:
            import itsm_servicenow_tool
            sys_id = await asyncio.to_thread(
                itsm_servicenow_tool._resolve_sys_id, base_url, credentials.get("username"),
                credentials.get("password"), ticket["external_ticket_key"],
            )
            if not sys_id:
                raise HTTPException(status_code=404, detail="Ticket number not found in ServiceNow")
            new_status = await asyncio.to_thread(
                itsm_connectors.servicenow_get_incident_status, base_url, credentials.get("username"),
                credentials.get("password"), sys_id,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to sync ticket: {exc}")

    if new_status != ticket["status"]:
        await asyncio.to_thread(db.update_itsm_ticket_status, ticket_id, new_status)
    return await asyncio.to_thread(db.get_itsm_ticket, ticket_id)


class WebhookBody(BaseModel):
    external_system: str        # jira | servicenow
    external_ticket_key: str
    status: str                 # raw system-specific status text/state code


@router.post("/webhook")
async def itsm_webhook(request: Request, body: WebhookBody):
    """Real-time status push — configure a Jira Automation rule or ServiceNow
    Business Rule to POST here on transition, with:
        Authorization: Bearer <ingest_api_key>
    (the same per-system key issued by the Dendrai UBO Configuration screen's
    monitored-systems registry that evidence_endpoints.py's webhook uses)."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <ingest_api_key>")
    api_key = auth_header[len("Bearer "):].strip()

    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    import mcp_governance
    system = await asyncio.to_thread(mcp_governance._get_system_by_api_key, api_key)
    if not system:
        raise HTTPException(status_code=401, detail="Invalid ingest API key")

    external_system = body.external_system.lower()
    ticket = await asyncio.to_thread(
        db.get_itsm_ticket_by_external_key, external_system, body.external_ticket_key)
    if not ticket:
        raise HTTPException(status_code=404, detail="No tracked ticket with that external_ticket_key")

    new_status = itsm_connectors.normalize_status(external_system, body.status)
    if new_status != ticket["status"]:
        await asyncio.to_thread(db.update_itsm_ticket_status, ticket["id"], new_status)
    return {"ok": True, "ticket_id": ticket["id"], "status": new_status}


@router.get("/sla-summary")
async def sla_summary():
    if not db.is_available():
        return {"open": 0, "breached": 0, "at_risk_24h": 0}
    tickets = db.list_itsm_tickets(limit=500)
    open_tickets = [t for t in tickets if t["status"] not in ("closed", "cancelled")]
    breached = [t for t in open_tickets if t.get("sla_breached_at")]
    now = datetime.now(timezone.utc)
    at_risk = [
        t for t in open_tickets
        if not t.get("sla_breached_at")
        and datetime.fromisoformat(t["sla_due_at"]) - now <= timedelta(hours=24)
    ]
    return {"open": len(open_tickets), "breached": len(breached), "at_risk_24h": len(at_risk)}
