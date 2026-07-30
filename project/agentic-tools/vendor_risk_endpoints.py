"""
Continuous Third-Party/Vendor Risk — vendor register CRUD.

Auditor-maintained register of which vendors are "critical" and their
current SOC 2 report coverage window. vendor_risk_sweep.py reads this table
to raise a finding when a vendor's SOC 2 report expires — this endpoint set
is how the register itself gets populated/updated (upserting a new SOC 2
date is also how a vendor's expired status gets cleared, see
db.upsert_vendor_risk_profile's docstring).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
from auth_endpoints import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vendor-risk", tags=["Vendor Risk"])


class VendorRiskProfileRequest(BaseModel):
    vendor_name: str
    vendor_id: Optional[str] = None
    critical: bool = False
    soc2_report_date: Optional[str] = None   # ISO date string
    soc2_expires_at: Optional[str] = None    # ISO date string


@router.get("")
async def list_vendor_risk_profiles(critical_only: bool = False,
                                     current_user: dict = Depends(get_current_user)):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"profiles": db.list_vendor_risk_profiles(critical_only=critical_only)}


@router.put("")
async def upsert_vendor_risk_profile(req: VendorRiskProfileRequest,
                                      current_user: dict = Depends(get_current_user)):
    """Create or update a vendor's risk profile — also the mechanism for
    clearing an EXPIRED status by recording a freshly renewed SOC 2 report."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    profile_id = db.upsert_vendor_risk_profile(
        vendor_name=req.vendor_name, vendor_id=req.vendor_id, critical=req.critical,
        soc2_report_date=req.soc2_report_date, soc2_expires_at=req.soc2_expires_at,
    )
    if not profile_id:
        raise HTTPException(status_code=500, detail="Failed to save vendor risk profile")
    return {"id": profile_id}
