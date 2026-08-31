#!/usr/bin/env python3
"""
SOX Scope — FastAPI router

Mounted in api_server.py with prefix /sox.

Endpoints:
    POST /sox/scope                           Run or re-run SOX scoping for a pipeline run
    GET  /sox/scope/{run_id}                  Retrieve saved scope for a run
    GET  /sox/scope/latest/{ticker}           Most recent scope for a ticker
    POST /sox/config/{ticker}                 Create/update materiality config for a company
    GET  /sox/config/{ticker}/{fy}            Retrieve config for a company + fiscal year
    GET  /sox/systems/{ticker}                List all systems in the registry for a ticker
    POST /sox/systems/{ticker}                Add or update a system in the registry
    DELETE /sox/systems/{ticker}/{id}         Deactivate a system
    GET  /sox/accounts/{ticker}               List account detail/overrides (geography, segments, notes, manual scope)
    POST /sox/accounts/{ticker}/{account_id}  Add/update detail or manual override for a significant account
    GET  /sox/processes/{ticker}              List process detail/overrides (geography, segments, notes, manual coverage)
    POST /sox/processes/{ticker}/{process_id} Add/update detail or manual override for a SOX process
    POST /sox/hitl/scope-approvals            Gate S1 — materiality + per-account scope HITL decisions
    POST /sox/hitl/coverage-approvals         Gate S2 — per-process coverage HITL decisions
    GET  /sox/hitl/gate-status/{run_id}       Gate S1/S2 status for a run
    POST /sox/segments/{ticker}               Add/update geographic or segment financials (historical)
    POST /sox/segments/{ticker}/import-xbrl   Auto-ingest segment/geography revenue from the latest 10-K/10-Q XBRL
    GET  /sox/segments/{ticker}/latest        Most recent fiscal year's segments, no fy lookup required
    GET  /sox/segments/{ticker}/{fy}          Retrieve segments for a company + fiscal year
    DELETE /sox/segments/{ticker}/{id}        Delete a geography/segment financial record
    GET  /sox/segments/{ticker}/forecasts/{run_id}  Retrieve computed segment forecast KPIs for a run
    POST /sox/segments/{ticker}/peers         Add/update peer company segment breakdowns
    GET  /sox/segments/{ticker}/peers/{fy}    Retrieve peer segment data for a company + fiscal year
    GET  /sox/rescoping/check/{run_id}        Check if inputs changed vs. last scope
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import db
from sox_scoping_tool import run_sox_scoping
from auth_endpoints import require_screen_permission
from edgar_tool import get_company_info, fetch_xbrl_facts

# Router-level: SOX Scoping backs the "SOX Control Pulse" screen (nav id
# "sox") — see auth_endpoints.require_screen_permission's docstring.
router = APIRouter(prefix="/sox", tags=["SOX Scoping"],
                    dependencies=[Depends(require_screen_permission("sox"))])


# ── Request / Response models ──────────────────────────────────────────────────

class SoxScopeRequest(BaseModel):
    run_id: Optional[int] = None
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


class SoxAccountDetailRequest(BaseModel):
    geography: List[str] = []
    segments: List[str] = []
    notes: Optional[str] = None
    manual_in_scope: Optional[bool] = None
    manual_priority: Optional[str] = None
    updated_by: Optional[str] = None


class SoxProcessDetailRequest(BaseModel):
    geography: List[str] = []
    segments: List[str] = []
    notes: Optional[str] = None
    manual_coverage_level: Optional[str] = None
    estimated_exposure: Optional[float] = None
    updated_by: Optional[str] = None


class SoxSegmentRequest(BaseModel):
    segments: List[Dict[str, Any]]
    fiscal_year: str


class PeerSegmentRequest(BaseModel):
    peer_ticker: str
    peer_name: Optional[str] = None
    fiscal_year: str
    segments: List[Dict[str, Any]]


class SoxScopeApprovalsRequest(BaseModel):
    run_id: int
    materiality: Optional[Dict[str, Any]] = None
    accounts: Dict[str, Any] = {}
    persona: Optional[str] = None


class SoxCoverageApprovalsRequest(BaseModel):
    run_id: int
    processes: Dict[str, Any] = {}
    persona: Optional[str] = None


# ── Helper ─────────────────────────────────────────────────────────────────────

def _resolve_company_id(ticker: str) -> Optional[int]:
    """Resolve or create company_id for a ticker. Returns None when DB is unavailable."""
    if not db.is_available():
        return None
    def _find():
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM companies WHERE ticker = %s", (ticker.upper(),))
                row = cur.fetchone()
                return row[0] if row else None
    company_id = db._run(_find)
    if not company_id:
        company_id = db.upsert_company({"ticker": ticker, "company_name": ticker.upper()})
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
    db_ok = db.is_available() and company_id is not None

    # Load registered systems from DB, merge with any supplied ad-hoc systems
    db_systems = db.list_sox_systems(company_id) if db_ok else []
    req_system_names = {s.get("system_name") for s in req.systems}
    merged_systems = req.systems + [s for s in db_systems if s["system_name"] not in req_system_names]

    # Load segments from DB for the fiscal year if not supplied
    fiscal_year = req.fiscal_year or f"FY{__import__('datetime').datetime.utcnow().year}"
    segments = req.segments
    if not segments and db_ok:
        segments = db.get_sox_segments(company_id, fiscal_year)

    # Load materiality config if available
    mat_pct  = req.materiality_pct
    perf_pct = req.performance_mat_pct
    if db_ok:
        cfg = db.get_sox_config(company_id, fiscal_year)
        if cfg:
            mat_pct  = cfg["materiality_pct"]
            perf_pct = cfg["performance_mat_pct"]

    # Load user-supplied account / process detail & scope overrides
    account_overrides = db.get_sox_account_details(company_id) if db_ok else {}
    process_overrides = db.get_sox_process_details(company_id) if db_ok else {}

    # Real detected balances (material_accounts_tool.py) to prefer over
    # sox_scoping_tool's heuristic estimates — a single XBRL fetch for this
    # one ticker (user-triggered re-scope, not a peer fan-out), best-effort:
    # a failure here just falls back to the heuristic, same as before this
    # feature existed.
    real_balances = None
    try:
        import material_accounts_tool
        meta, _sub = get_company_info(req.ticker)
        subj_xbrl = fetch_xbrl_facts(meta["cik"]) or {}
        uploaded_xbrl = db.get_manual_financials(company_id, granularity=["annual", "quarterly"]) if db_ok else {}
        mat_accounts = material_accounts_tool.detect_material_accounts(
            subj_xbrl, meta.get("sic", ""), uploaded_xbrl)
        real_balances = material_accounts_tool.real_balances_for_sox(mat_accounts)
    except Exception as _mat_err:
        logger.warning("Material-account balance detection failed for SOX scoping (non-fatal, falling back to heuristic estimates): %s", _mat_err)

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
        account_overrides=account_overrides,
        process_overrides=process_overrides,
        real_balances=real_balances,
    )

    if db_ok and req.run_id is not None:
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
    if not company_id:
        raise HTTPException(status_code=404, detail=f"No SOX scope found for {ticker.upper()} (database not configured)")
    scope = db.get_latest_sox_scoping_result(company_id)
    if not scope:
        raise HTTPException(status_code=404, detail=f"No SOX scope found for {ticker.upper()}")
    return scope


# ── Config endpoints ───────────────────────────────────────────────────────────

@router.post("/config/{ticker}")
def upsert_sox_config(ticker: str, req: SoxConfigRequest):
    """Create or update SOX materiality configuration for a company + fiscal year."""
    company_id = _resolve_company_id(ticker)
    if not company_id:
        return {"saved": False, "reason": "database not configured", "ticker": ticker.upper()}
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
    if not company_id:
        raise HTTPException(status_code=404, detail=f"No config for {ticker.upper()} {fiscal_year}")
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
    systems = db.list_sox_systems(company_id, active_only=active_only) if company_id else []
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
    if not company_id:
        return {"saved": False, "reason": "database not configured", "system_name": req.system_name}
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
    if not company_id:
        return {"deactivated": False, "reason": "database not configured", "system_id": system_id}
    ok = db.deactivate_sox_system(company_id, system_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"System {system_id} not found for {ticker.upper()}")
    return {"deactivated": True, "system_id": system_id}


# ── Account / process detail & override endpoints ──────────────────────────────

@router.get("/accounts/{ticker}")
def list_sox_account_details(ticker: str):
    """
    Retrieve user-supplied detail/overrides for SOX significant accounts
    (geography, segments, notes, manual in-scope override), keyed by account_id.
    """
    company_id = _resolve_company_id(ticker)
    details = db.get_sox_account_details(company_id) if company_id else {}
    return {"ticker": ticker.upper(), "accounts": details}


@router.post("/accounts/{ticker}/{account_id}")
def upsert_sox_account_detail(ticker: str, account_id: str, req: SoxAccountDetailRequest):
    """
    Add or update geography, segment, notes, or a manual in-scope/priority
    override for a SOX significant account. Applied on the next scoping run
    (POST /sox/scope) — click "Rescope" to see the change reflected.
    """
    company_id = _resolve_company_id(ticker)
    if not company_id:
        return {"saved": False, "reason": "database not configured", "account_id": account_id}
    detail_id = db.upsert_sox_account_detail(company_id, account_id, req.dict())
    if not detail_id:
        raise HTTPException(status_code=500, detail="Failed to save account detail")
    return {"saved": True, "detail_id": detail_id, "ticker": ticker.upper(), "account_id": account_id}


@router.get("/processes/{ticker}")
def list_sox_process_details(ticker: str):
    """
    Retrieve user-supplied detail/overrides for SOX processes (geography,
    segments, notes, manual coverage-level override), keyed by process_id.
    """
    company_id = _resolve_company_id(ticker)
    details = db.get_sox_process_details(company_id) if company_id else {}
    return {"ticker": ticker.upper(), "processes": details}


@router.post("/processes/{ticker}/{process_id}")
def upsert_sox_process_detail(ticker: str, process_id: str, req: SoxProcessDetailRequest):
    """
    Add or update geography, segment, notes, estimated dollar exposure, or a
    manual coverage-level override (P1 | P2 | Out) for a SOX process. Applied
    on the next scoping run (POST /sox/scope) — click "Rescope" to see the
    change reflected.
    """
    company_id = _resolve_company_id(ticker)
    if not company_id:
        return {"saved": False, "reason": "database not configured", "process_id": process_id}
    detail_id = db.upsert_sox_process_detail(company_id, process_id, req.dict())
    if not detail_id:
        raise HTTPException(status_code=500, detail="Failed to save process detail")
    return {"saved": True, "detail_id": detail_id, "ticker": ticker.upper(), "process_id": process_id}


# ── HITL gate endpoints ─────────────────────────────────────────────────────────
# Gate S1 (materiality + significant accounts) and Gate S2 (process coverage),
# mirroring the Enterprise Risk pipeline's Gate 1 (risk approval) / Gate 2
# (scope approval) — per-item Approve/Adjust with a CAE → CFO → Audit Committee
# sign-off chain on adjustments. Persisted as an audit trail (no upsert/dedupe),
# same as /loop/hitl/risk-approvals and /loop/hitl/scope-approvals.

@router.post("/hitl/scope-approvals")
def save_sox_scope_approvals(req: SoxScopeApprovalsRequest):
    """Persist SOX HITL Gate S1 decisions (materiality basis + per-account scope)."""
    if not db.is_available():
        return {"saved": False, "reason": "database not configured"}
    if not req.materiality and not req.accounts:
        return {"saved": False, "reason": "no approvals provided"}
    db.save_sox_scope_approvals(req.run_id, req.materiality, req.accounts, req.persona or None)
    return {"saved": True, "run_id": req.run_id, "account_count": len(req.accounts)}


@router.post("/hitl/coverage-approvals")
def save_sox_coverage_approvals(req: SoxCoverageApprovalsRequest):
    """Persist SOX HITL Gate S2 decisions (per-process coverage level)."""
    if not db.is_available():
        return {"saved": False, "reason": "database not configured"}
    if not req.processes:
        return {"saved": False, "reason": "no approvals provided"}
    db.save_sox_coverage_approvals(req.run_id, req.processes, req.persona or None)
    return {"saved": True, "run_id": req.run_id, "process_count": len(req.processes)}


@router.get("/hitl/gate-status/{run_id}")
def get_sox_hitl_gate_status(run_id: int):
    """Retrieve SOX Gate S1 / Gate S2 status for a run (for restoring UI state on reload)."""
    if not db.is_available():
        return {"gate3_status": None, "gate4_status": None}
    return db.get_sox_hitl_gate_status(run_id)


# ── Segment endpoints ──────────────────────────────────────────────────────────

@router.post("/segments/{ticker}")
def upsert_sox_segments(ticker: str, req: SoxSegmentRequest, run_id: Optional[int] = Query(default=None)):
    """
    Add or update geographic / business-segment financial data.

    Used for risk-based materiality allocation across geographies and segments.
    When EDGAR XBRL includes segment data, POST .../import-xbrl ingests it
    directly (edgar_segments.py); use this endpoint to supply data for a
    private company/non-filer, or to correct an auto-ingested breakdown.

    segment_type: 'geography' | 'business_segment' | 'product_line'
    """
    company_id = _resolve_company_id(ticker)
    if not company_id:
        return {"saved": 0, "reason": "database not configured", "fiscal_year": req.fiscal_year}
    saved = 0
    for seg in req.segments:
        seg["fiscal_year"] = req.fiscal_year
        db.upsert_sox_segment(company_id, run_id, seg)
        saved += 1
    return {"saved": saved, "fiscal_year": req.fiscal_year}


@router.post("/segments/{ticker}/import-xbrl")
def import_xbrl_segments(ticker: str):
    """
    Auto-ingest geographic / business-segment revenue straight from the
    company's most recent 10-K/10-Q XBRL instance document (edgar_segments.py)
    — this is the "ingested automatically" path upsert_sox_segments' docstring
    above describes; that auto-ingestion didn't actually exist until now.

    Only breakdowns that reconcile to the filing's own consolidated revenue
    (within tolerance) are written, tagged source='filed' so they're never
    confused with a manually-entered row. Unreconciled or unextractable
    breakdowns are reported in the response but not persisted — see
    edgar_segments.persist_segments's docstring for why.
    """
    import edgar_segments
    result = edgar_segments.persist_segments(ticker)
    return result


@router.get("/segments/{ticker}/latest")
def get_latest_sox_segments_endpoint(ticker: str):
    """Retrieve the most recent fiscal year's segment/geography breakdown for
    a company, without the caller needing to know which fiscal_year that is —
    used by the Assess Risk loop's Stage 2 Forecasts panel, which only knows
    the ticker. Registered ahead of the /{fiscal_year} route below since
    "latest" would otherwise be swallowed by that pattern."""
    company_id = _resolve_company_id(ticker)
    segments = db.get_latest_sox_segments(company_id) if company_id else []
    return {
        "ticker": ticker.upper(),
        "count": len(segments),
        "segments": segments,
    }


@router.get("/segments/{ticker}/{fiscal_year}")
def get_sox_segments(ticker: str, fiscal_year: str):
    """Retrieve segment/geography financial breakdowns for a company + fiscal year."""
    company_id = _resolve_company_id(ticker)
    segments = db.get_sox_segments(company_id, fiscal_year) if company_id else []
    return {
        "ticker": ticker.upper(),
        "fiscal_year": fiscal_year,
        "count": len(segments),
        "segments": segments,
    }


@router.delete("/segments/{ticker}/{segment_id}")
def remove_sox_segment(ticker: str, segment_id: int):
    """Delete a geography / business-segment financial record."""
    company_id = _resolve_company_id(ticker)
    if not company_id:
        return {"deleted": False, "reason": "database not configured", "segment_id": segment_id}
    ok = db.delete_sox_segment(company_id, segment_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Segment {segment_id} not found for {ticker.upper()}")
    return {"deleted": True, "segment_id": segment_id}


@router.get("/segments/{ticker}/forecasts/{run_id}")
def get_segment_forecasts(ticker: str, run_id: int):
    """
    Retrieve computed segment / geography forecast KPIs for a pipeline run.

    Returns the rows saved by the pipeline (or manually via the backend) for
    forward-looking KPI estimates broken down by geography and business segment.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    rows = db.get_segment_forecasts(run_id)
    geo = [r for r in rows if r["segment_type"] == "geography"]
    seg = [r for r in rows if r["segment_type"] == "business_segment"]
    return {
        "ticker": ticker.upper(),
        "run_id": run_id,
        "geography": geo,
        "business_segment": seg,
    }


@router.post("/segments/{ticker}/peers")
def upsert_peer_segments(ticker: str, req: PeerSegmentRequest):
    """
    Add or update peer company segment / geography financial data for benchmarking.

    segment_type: 'geography' | 'business_segment'
    Each segment dict may include: segment_name, revenue_m, revenue_pct,
    gross_margin, op_margin, net_margin, source.
    """
    company_id = _resolve_company_id(ticker)
    if not company_id:
        return {"saved": 0, "reason": "database not configured", "fiscal_year": req.fiscal_year}
    for seg in req.segments:
        seg.setdefault("fiscal_year", req.fiscal_year)
    saved = db.upsert_peer_segment(
        company_id,
        req.peer_ticker,
        req.peer_name or req.peer_ticker.upper(),
        req.segments,
    )
    return {
        "saved": saved,
        "ticker": ticker.upper(),
        "peer_ticker": req.peer_ticker.upper(),
        "fiscal_year": req.fiscal_year,
    }


@router.get("/segments/{ticker}/peers/{fiscal_year}")
def get_peer_segments(ticker: str, fiscal_year: str, peer: Optional[str] = Query(default=None)):
    """
    Retrieve peer company segment / geography breakdowns for a company + fiscal year.

    Optionally filter to a single peer with ?peer=TICK.
    """
    company_id = _resolve_company_id(ticker)
    rows = db.get_peer_segments(company_id, peer_ticker=peer, fiscal_year=fiscal_year) if company_id else []
    by_peer: Dict[str, list] = {}
    for r in rows:
        by_peer.setdefault(r["peer_ticker"], []).append(r)
    return {
        "ticker": ticker.upper(),
        "fiscal_year": fiscal_year,
        "peer_count": len(by_peer),
        "peers": [
            {"peer_ticker": pt, "peer_name": segs[0]["peer_name"], "segments": segs}
            for pt, segs in by_peer.items()
        ],
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
