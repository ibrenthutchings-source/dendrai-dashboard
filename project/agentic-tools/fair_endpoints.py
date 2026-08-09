#!/usr/bin/env python3
"""
Risk Quantification API — FAIR-style Monte Carlo loss quantification over
adjudicated events, SOX processes, risk register entries, and CEM events.

Router prefix: /fair

    POST /fair/quantify                 Run a quantification, persist it, optionally write back
                                         a dollar exposure onto the source CEM event/template
    GET  /fair/quantifications           History (?resource_type=&days=&limit=)
    GET  /fair/quantifications/latest    Most recent run for one resource (?resource_type=&resource_ref=)
    GET  /fair/ale-summary               Highest-ALE resources right now (?days=) — the dashboard table
    POST /fair/control-roi               Risk-adjusted ROI of a control (two ALE figures + annual cost)
    GET  /fair/severity-bands            The CEM-severity PERT default bands fair_tool.py falls back to

fair_tool.py does the actual Monte Carlo simulation and is pure (no I/O);
this router owns every DB read/write around it — same split
db._build_control_flow_map / db.get_control_flow_map established for the
Control Flow Map. A quantification always tries the best REAL source first
(a control's empirical fire history for frequency; a SOX process's derived
account-balance exposure, or a risk's already-allocated dollarExposureM, for
magnitude) and only falls back to the CEM-severity default band when
nothing else was supplied — see fair_tool.resolve_tef/resolve_magnitude for
the exact priority order, and every response's tef_source/magnitude_source
for which one actually fired.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
import fair_tool
from auth_endpoints import get_current_user, require_screen_permission

logger = logging.getLogger("ubo.fair")
# Router-level: backs the "Risk Quantification" screen (nav id "riskquant").
router = APIRouter(prefix="/fair", tags=["Risk Quantification"],
                    dependencies=[Depends(require_screen_permission("riskquant"))])

_VALID_RESOURCE_TYPES = {"cem_event", "cem_event_template", "sox_process", "risk", "control"}


class QuantifyRequest(BaseModel):
    resource_type: str          # cem_event | cem_event_template | sox_process | risk | control
    resource_ref: str           # id/slug within that resource's own table (cem_events.id, a SOX
                                 # process_id, a risk_ref, a control_id — stringified either way)
    company_id: Optional[int] = None
    run_id: Optional[int] = None
    control_id: Optional[str] = None    # whose fire history feeds TEF, when different from resource_ref
    process: Optional[str] = None       # PaC process id, for cross-navigation from the result
    window_days: int = 90               # lookback window for empirical control-fire frequency

    # Frequency override
    manual_tef: Optional[float] = None  # events/year — skips the empirical fire-history lookup entirely

    # Magnitude — first non-null wins, same priority order as fair_tool.resolve_magnitude
    manual_loss_min: Optional[float] = None
    manual_loss_likely: Optional[float] = None
    manual_loss_max: Optional[float] = None
    sox_estimated_exposure: Optional[float] = None    # pass directly, or omit + company_id to look it up
    risk_dollar_exposure_m: Optional[float] = None     # risk-engine.js's allocateRiskDollarExposure() output
    cem_severity: Optional[str] = None                 # P1 | P2 | P3 — last-resort default band

    simulations: int = fair_tool.DEFAULT_SIMULATIONS
    persist: bool = True   # False for a "what-if" preview that shouldn't join the history/ALE summary


class ControlRoiRequest(BaseModel):
    ale_before: float
    ale_after: float
    annual_control_cost: float
    control_id: Optional[str] = None


@router.get("/severity-bands")
async def severity_bands():
    return {"bands": fair_tool.CEM_SEVERITY_BANDS}


@router.post("/quantify")
async def quantify(req: QuantifyRequest, current_user: dict = Depends(get_current_user)):
    if req.resource_type not in _VALID_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail=f"resource_type must be one of {sorted(_VALID_RESOURCE_TYPES)}")

    fire_count_window = 0
    if req.control_id and req.manual_tef is None and db.is_available():
        stats = await asyncio.to_thread(db.get_control_fire_stats, req.control_id, req.window_days)
        fire_count_window = stats["fire_count_window"]

    sox_exposure = req.sox_estimated_exposure
    if req.resource_type == "sox_process" and sox_exposure is None and req.company_id and db.is_available():
        details = await asyncio.to_thread(db.get_sox_process_details, req.company_id)
        sox_exposure = (details.get(req.resource_ref) or {}).get("estimated_exposure")

    manual_magnitude = None
    if None not in (req.manual_loss_min, req.manual_loss_likely, req.manual_loss_max):
        manual_magnitude = (req.manual_loss_min, req.manual_loss_likely, req.manual_loss_max)

    result = fair_tool.quantify(
        fire_count_window=fire_count_window,
        window_days=req.window_days,
        manual_tef=req.manual_tef,
        manual_magnitude=manual_magnitude,
        sox_estimated_exposure=sox_exposure,
        risk_dollar_exposure_m=req.risk_dollar_exposure_m,
        cem_severity=req.cem_severity,
        simulations=req.simulations,
    )

    quant_id = None
    if req.persist and db.is_available():
        quant_id = await asyncio.to_thread(db.save_fair_quantification, {
            "resource_type": req.resource_type, "resource_ref": req.resource_ref,
            "company_id": req.company_id, "run_id": req.run_id,
            "control_id": req.control_id, "process": req.process,
            **result,
            "created_by": current_user.get("username"),
        })
        # Write the headline ALE back onto the source row as its numeric
        # exposure companion — see the cem_events/cem_event_templates
        # migration block. Best-effort: a bad resource_ref (e.g. a
        # non-numeric CEM id typo) fails the quantify persist itself, never
        # the whole request.
        try:
            if req.resource_type == "cem_event":
                await asyncio.to_thread(db.update_cem_event_exposure, int(req.resource_ref), result["ale"], "fair")
            elif req.resource_type == "cem_event_template":
                await asyncio.to_thread(db.update_cem_event_template_exposure, int(req.resource_ref), result["ale"], "fair")
        except (TypeError, ValueError) as exc:
            logger.warning("fair/quantify: could not write back exposure for %s=%s: %s",
                            req.resource_type, req.resource_ref, exc)

    return {"id": quant_id, "resource_type": req.resource_type, "resource_ref": req.resource_ref,
            "fire_count_window": fire_count_window, **result}


@router.get("/quantifications")
async def list_quantifications(resource_type: Optional[str] = None, days: int = 365, limit: int = 500):
    if not db.is_available():
        return {"quantifications": []}
    rows = await asyncio.to_thread(db.list_fair_quantifications, resource_type, days, limit)
    return {"quantifications": rows}


@router.get("/quantifications/latest")
async def latest_quantification(resource_type: str, resource_ref: str):
    if resource_type not in _VALID_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail=f"resource_type must be one of {sorted(_VALID_RESOURCE_TYPES)}")
    if not db.is_available():
        return {"quantification": None}
    row = await asyncio.to_thread(db.get_latest_fair_quantification, resource_type, resource_ref)
    return {"quantification": row}


@router.get("/ale-summary")
async def ale_summary(days: int = 365):
    """Latest ALE per resource, highest first — 'what's the most expensive
    open risk right now,' the Risk Quantification screen's headline table."""
    if not db.is_available():
        return {"resources": [], "total_ale": 0}
    rows = await asyncio.to_thread(db.get_fair_ale_summary, days)
    total_ale = round(sum(r["ale"] or 0 for r in rows), 4)
    return {"resources": rows, "total_ale": total_ale, "window_days": days}


@router.post("/control-roi")
async def control_roi(req: ControlRoiRequest):
    result = fair_tool.control_roi(req.ale_before, req.ale_after, req.annual_control_cost)
    return {"control_id": req.control_id, **result}
