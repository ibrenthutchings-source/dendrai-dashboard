#!/usr/bin/env python3
"""
SOX Scope — FastAPI router

Mounted in api_server.py with prefix /sox.

Endpoints:
    POST /sox/scope                  Run or re-run SOX scoping for a pipeline run
    GET  /sox/scope/{run_id}         Retrieve saved scope for a run
    GET  /sox/scope/latest/{ticker}  Most recent scope for a ticker
    POST /sox/config/{ticker}        Create/update materiality config for a company
    GET  /sox/config/{ticker}/{fy}   Retrieve config for a company + fiscal year
    GET  /sox/systems/{ticker}       List all systems in the registry for a ticker
    POST /sox/systems/{ticker}       Add or update a system in the registry
    DELETE /sox/systems/{ticker}/{id} Deactivate a system
    POST /sox/segments/{ticker}      Add/update geographic or segment financials
    GET  /sox/segments/{ticker}/{fy} Retrieve segments for a company + fiscal year
    GET  /sox/rescoping/check/{run_id} Check if inputs changed vs. last scope
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import db
from sox_scoping_tool import run_sox_scoping

router = APIRouter(prefix="/sox", tags=["SOX Scoping"])


# ── Request / Response models ──────────────────────────────────────────────────

class SoxScopeRequest(BaseModel):
    run_id: int
    ticker: str
    forecast: Dict[str, Any] = {}
    risk_scores: Dict[str, Any] = {}
    ratios: Dict[str, Any] = {}
    systems: List[Dict[str, Any]] = []
    segments: List[Dict[str, Any]] = []
    fiscal_year: str = ""
    materiality_pct: float = 5.0
    performance_mat_pct: float = 75.0
    trigger_reason: str = "pipeline_run"


class SoxConfigRequest(BaseModel):
    fiscal_year: str
    fiscal_year_end: Optional[str] = None
    materiality_basis: str = "pretax_income"
    materiality_pct: float = 5.0
    performance_mat_pct: float = 75.0
    scope_note: Optional[str] = None


class SoxSystemRequest(BaseModel):
    system_name: str
    system_type: str = "erp"
    vendor: Optional[str] = None
    version: Optional[str] = None
    linked_processes: List[str] = []
    significance: str = "medium"
    notes: Optional[str] = None
    added_by: Optional[str] = None


class SoxSegmentRequest(BaseModel):
    segments: List[Dict[str, Any]]
    fiscal_year: str


# ── Helper ─────────────────────────────────────────────────────────────────────

def _resolve_company_id(ticker: str) -> int:
    """Resolve or create company_id for a ticker. Raises 404 if not found and DB unavailable."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL not set)")
    # Attempt to find existing company
    def _find():
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM companies WHERE ticker = %s", (ticker.upper(),))
                row = cur.fetchone()
                return row[0] if row else None
    company_id = db._run(_find)
    if not company_id:
        # Create a minimal placeholder — full metadata populated by edgar endpoints
        company_id = db.upsert_company({"ticker": ticker, "company_name": ticker.upper()})
    if not company_id:
        raise HTTPException(status_code=404, detail=f"Company not found for ticker {ticker.upper()}")
    return company_id


# ── Scope endpoints ────────────────────────────────────────────────────────────

@router.post("/scope")
def compute_sox_scope(req: SoxScopeRequest):
    """
    Run SOX scoping for a completed pipeline run. Persists result to DB and returns
    the full scoping decision including materiality, accounts, processes, systems,
    and segment coverage.

    Called automatically after /predictive/full-analysis when a SOX config exists,
    and can be re-triggered manually via this endpoint.
    """
    if not req.forecast or not req.risk_scores:
        raise HTTPException(
            status_code=400,
            detail="forecast and risk_scores are required — run the pipeline first",
        )

    company_id = _resolve_company_id(req.ticker)

    # Load registered systems from DB, merge with any supplied ad-hoc systems
    db_systems = db.list_sox_systems(company_id) if db.is_available() else []
    req_system_names = {s.get("system_name") for s in req.systems}
    merged_systems = req.systems + [s for s in db_systems if s["system_name"] not in req_system_names]

    # Load segments from DB for the fiscal year if not supplied
    fiscal_year = req.fiscal_year or f"FY{__import__('datetime').datetime.utcnow().year}"
    segments = req.segments
    if not segments and db.is_available():
        segments = db.get_sox_segments(company_id, fiscal_year)

    # Load materiality config if available
    mat_pct  = req.materiality_pct
    perf_pct = req.performance_mat_pct
    if db.is_available():
        cfg = db.get_sox_config(company_id, fiscal_year)
        if cfg:
            mat_pct  = cfg["materiality_pct"]
            perf_pct = cfg["performance_mat_pct"]

    result = run_sox_scoping(
        run_id=req.run_id,
        forecast=req.forecast,
        risk_scores=req.risk_scores,
        ratios=req.ratios,
        systems_registry=merged_systems,
        segments=segments,
        fiscal_year=fiscal_year,
        materiality_pct=mat_pct,
        performance_mat_pct=perf_pct,
        trigger_reason=req.trigger_reason,
    )

    if db.is_available():
        db.save_sox_scoping_result(req.run_id, company_id, result)

    return result


@router.get("/scope/{run_id}")
def get_sox_scope(run_id: int):
    """Retrieve the persisted SOX scope for a specific pipeline run."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    scope = db.get_sox_scoping_result(run_id)
    if not scope:
        raise HTTPException(status_code=404, detail=f"No SOX scope found for run_id {run_id}")
    return scope


@router.get("/scope/latest/{ticker}")
def get_latest_sox_scope(ticker: str):
    """Most recent SOX scope for a ticker (across all runs)."""
    company_id = _resolve_company_id(ticker)
    scope = db.get_latest_sox_scoping_result(company_id)
    if not scope:
        raise HTTPException(status_code=404, detail=f"No SOX scope found for {ticker.upper()}")
    return scope


# ── Config endpoints ───────────────────────────────────────────────────────────

@router.post("/config/{ticker}")
def upsert_sox_config(ticker: str, req: SoxConfigRequest):
    """Create or update SOX materiality configuration for a company + fiscal year."""
    company_id = _resolve_company_id(ticker)
    cfg_id = db.upsert_sox_config(company_id, req.fiscal_year, req.dict())
    return {
        "saved": True,
        "config_id": cfg_id,
        "ticker": ticker.upper(),
        "fiscal_year": req.fiscal_year,
    }


@router.get("/config/{ticker}/{fiscal_year}")
def get_sox_config(ticker: str, fiscal_year: str):
    """Retrieve SOX materiality config for a company + fiscal year."""
    company_id = _resolve_company_id(ticker)
    cfg = db.get_sox_config(company_id, fiscal_year)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"No config for {ticker.upper()} {fiscal_year}")
    return cfg


# ── System registry endpoints ──────────────────────────────────────────────────

@router.get("/systems/{ticker}")
def list_sox_systems(ticker: str, active_only: bool = Query(default=True)):
    """
    List all systems in the SOX registry for a ticker.
    Systems are open-ended — add via POST /sox/systems/{ticker}.
    """
    company_id = _resolve_company_id(ticker)
    systems = db.list_sox_systems(company_id, active_only=active_only)
    return {
        "ticker": ticker.upper(),
        "count": len(systems),
        "systems": systems,
    }


@router.post("/systems/{ticker}")
def add_sox_system(ticker: str, req: SoxSystemRequest):
    """
    Add or update a system in the SOX registry.

    system_type options: erp | consolidation | reporting | treasury |
                         hr_payroll | tax | sub_ledger | crm | billing | epm | custom

    significance: high | medium | low
    linked_processes: list of SOX process IDs that this system supports
        (order_to_cash, procure_to_pay, financial_close, itgc, treasury,
         payroll_hr, tax_provision, inventory_cost, fixed_assets,
         equity_goodwill, segment_reporting)
    """
    company_id = _resolve_company_id(ticker)
    sys_id = db.upsert_sox_system(company_id, req.dict())
    if not sys_id:
        raise HTTPException(status_code=500, detail="Failed to save system")
    return {
        "saved": True,
        "system_id": sys_id,
        "ticker": ticker.upper(),
        "system_name": req.system_name,
    }


@router.delete("/systems/{ticker}/{system_id}")
def remove_sox_system(ticker: str, system_id: int):
    """Deactivate a system from the SOX registry (soft delete — preserves audit trail)."""
    company_id = _resolve_company_id(ticker)
    ok = db.deactivate_sox_system(company_id, system_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"System {system_id} not found for {ticker.upper()}")
    return {"deactivated": True, "system_id": system_id}


# ── Segment endpoints ──────────────────────────────────────────────────────────

@router.post("/segments/{ticker}")
def upsert_sox_segments(ticker: str, req: SoxSegmentRequest, run_id: Optional[int] = Query(default=None)):
    """
    Add or update geographic / business-segment financial data.

    Used for risk-based materiality allocation across geographies and segments.
    When EDGAR XBRL includes segment data it is ingested automatically;
    use this endpoint to supply or correct segment breakdowns manually.

    segment_type: 'geography' | 'business_segment' | 'product_line'
    """
    company_id = _resolve_company_id(ticker)
    saved = 0
    for seg in req.segments:
        seg["fiscal_year"] = req.fiscal_year
        db.upsert_sox_segment(company_id, run_id, seg)
        saved += 1
    return {"saved": saved, "fiscal_year": req.fiscal_year}


@router.get("/segments/{ticker}/{fiscal_year}")
def get_sox_segments(ticker: str, fiscal_year: str):
    """Retrieve segment/geography financial breakdowns for a company + fiscal year."""
    company_id = _resolve_company_id(ticker)
    segments = db.get_sox_segments(company_id, fiscal_year)
    return {
        "ticker": ticker.upper(),
        "fiscal_year": fiscal_year,
        "count": len(segments),
        "segments": segments,
    }


# ── Rescoping check endpoint ───────────────────────────────────────────────────

@router.get("/rescoping/check/{run_id}")
def check_rescoping_needed(
    run_id: int,
    forecast_hash: str = Query(default=""),
):
    """
    Check whether the inputs for run_id differ from the last persisted scope.
    Returns needs_rescoping=True if the input hash has changed.
    """
    if not db.is_available():
        return {"needs_rescoping": False, "reason": "database not configured"}

    scope = db.get_sox_scoping_result(run_id)
    if not scope:
        return {"needs_rescoping": True, "reason": "no previous scope found for this run"}

    prev_hash = scope.get("input_hash", "")
    if forecast_hash and forecast_hash != prev_hash:
        return {
            "needs_rescoping": True,
            "reason": "input hash changed — forecast or risk scores updated",
            "prev_hash": prev_hash,
            "new_hash": forecast_hash,
        }

    return {"needs_rescoping": False, "current_hash": prev_hash}
