#!/usr/bin/env python3
"""
Audit sample selection API — thin FastAPI wrapper around
sample_selection_tool.py's pure random/risk_based/mus functions.

Stateless by design: the caller supplies the population (already pulled
from wherever it lives — a JE Testing findings export, a vendor list, an
access review extract) and gets back the sample plus a methodology dict
documenting exactly how it was drawn, reproducible from the same seed.
Nothing here reads or writes the database; this is a mechanical utility
step, not a system of record for which samples were tested.

Router prefix: /sample-selection

    POST /sample-selection/select   Draw a sample by method (random|risk_based|mus)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import sample_selection_tool as sst
from auth_endpoints import require_screen_permission

router = APIRouter(prefix="/sample-selection", tags=["Audit Sample Selection"])

# No dedicated nav item — gated on the closest existing real screen, same
# "reuse the nearest permission bucket" reasoning evidence_endpoints.py and
# itsm_endpoints.py document for themselves.
_SCREEN_ID = "infrastructuremonitoring"


class SelectRequest(BaseModel):
    method: str                          # 'random' | 'risk_based' | 'mus'
    population: List[Dict[str, Any]]
    params: Optional[Dict[str, Any]] = None


@router.post("/select")
def select_sample(req: SelectRequest, current_user: Dict[str, Any] = Depends(require_screen_permission(_SCREEN_ID))):
    if not req.population:
        raise HTTPException(status_code=422, detail="population must not be empty")
    try:
        return sst.select(req.method, req.population, req.params or {})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except TypeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid params for method '{req.method}': {exc}")
