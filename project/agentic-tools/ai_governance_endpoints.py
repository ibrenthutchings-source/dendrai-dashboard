"""
AI Governance — AI system register CRUD.

Auditor-maintained register of the audited company's OWN AI system usage
(distinct from observability.mcp_telemetry / ai-inventory.jsx, which only
inventories this platform's own MCP tool calls — see
db.py's ai_system_registry DDL comment for the full "why a register, not a
connector" rationale).

Saving a system that requires human oversight but has none defined raises an
AI_HUMAN_OVERSIGHT_MISSING finding immediately (a static configuration gap,
not something that decays with time — ai_governance_sweep.py separately
handles the time-based half, assessment expiry).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
import mcp_governance
from auth_endpoints import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-governance", tags=["AI Governance"])


class AiSystemRequest(BaseModel):
    system_name: str
    vendor: Optional[str] = None
    business_owner: Optional[str] = None
    risk_tier: str = "MEDIUM"
    requires_human_oversight: bool = False
    human_oversight_defined: bool = False
    last_assessment_date: Optional[str] = None   # ISO date string
    assessment_expires_at: Optional[str] = None  # ISO date string


@router.get("")
async def list_ai_systems(high_risk_only: bool = False,
                           current_user: dict = Depends(get_current_user)):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"systems": db.list_ai_systems(high_risk_only=high_risk_only)}


@router.put("")
async def upsert_ai_system(req: AiSystemRequest,
                            current_user: dict = Depends(get_current_user)):
    """Create or update an AI system's governance profile. Also the
    mechanism for clearing an EXPIRED assessment status by recording a fresh
    assessment date."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    system_id = db.upsert_ai_system(
        system_name=req.system_name, vendor=req.vendor, business_owner=req.business_owner,
        risk_tier=req.risk_tier, requires_human_oversight=req.requires_human_oversight,
        human_oversight_defined=req.human_oversight_defined,
        last_assessment_date=req.last_assessment_date, assessment_expires_at=req.assessment_expires_at,
    )
    if not system_id:
        raise HTTPException(status_code=500, detail="Failed to save AI system profile")

    if req.requires_human_oversight and not req.human_oversight_defined:
        try:
            flags = mcp_governance._detect_system_flags({
                "action": "ai_human_oversight_missing", "resource": req.system_name,
                "severity": "HIGH", "event_type": "ai_human_oversight_missing",
                "payload": {"ai_human_oversight_missing": True},
            })
            mcp_governance._ingest_system_event(
                "ai-governance-endpoints", "ai_governance", "ai_human_oversight_missing",
                f"oversight-missing:{system_id}:{req.system_name}",
                current_user.get("username"), "human_oversight_check", req.system_name,
                "HIGH", flags,
                {
                    "ai_human_oversight_missing": True,
                    "ai_governance_detail": {
                        "system_name": req.system_name, "vendor": req.vendor,
                        "risk_tier": req.risk_tier,
                    },
                },
                None,
            )
        except Exception as exc:
            logger.warning("ai_governance_endpoints: failed to raise oversight-missing finding for %s: %s", req.system_name, exc)

    return {"id": system_id}
