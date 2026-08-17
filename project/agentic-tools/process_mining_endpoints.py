#!/usr/bin/env python3
"""
Process Mining API — variant analysis, conformance checking, cycle-time/
bottleneck stats, and rework detection over case-tracked adjudications.

Router prefix: /process-mining

    GET /process-mining/processes      The known process templates (id, label, canonical steps)
    GET /process-mining/summary        Headline tiles: case counts, conformance/rework rate, bottleneck per process
    GET /process-mining/variants       Distinct step sequences observed, most frequent first
    GET /process-mining/conformance    Conformance rate + deviating cases against the matched template
    GET /process-mining/cycle-times    Per-edge duration stats + overall case duration + bottleneck
    GET /process-mining/rework         Cases that revisited an already-completed step
    GET /process-mining/cases          Per-case detail (steps, duration, variant, conformance)

All read-only and computed on demand from the same
observability.adjudicated_tool_calls feed GET /observability/events and
CaseFlowGraph (continuous-monitoring-viz.jsx) already use — see
db.get_recent_adjudications_for_domain_summary. process_mining_tool.py does
the actual analysis and is pure (no DB); this router owns fetching the raw
rows and building cases from them once per request. Surfaced on the
Continuous Monitoring screen (nav id "continuousmonitoring") as additional
chart tabs alongside the existing Case Flow Graph.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends

import db
import process_mining_tool as pm
from auth_endpoints import require_screen_permission

# Router-level: backs the "Continuous Monitoring" screen's Process Mining tabs.
router = APIRouter(prefix="/process-mining", tags=["Process Mining"],
                    dependencies=[Depends(require_screen_permission("continuousmonitoring"))])


async def _load_events(days: int) -> list:
    """Same union GET /observability/events does (api_server.py) — adjudicated
    rows plus the unreviewed system_telemetry tail, since only flagged rows
    ever reach adjudication (mcp_governance._fetch_unprocessed_system) and
    process mining's case_id/process_step live on the raw event regardless of
    whether it was ever selected for review. Before this, Variants/
    Conformance/Cycle Time/Rework silently saw only the same ~2.5% slice
    Continuous Monitoring's charts did until that was fixed — build_cases()
    only reads case_id/process_step/adjudicated_at, none of which require a
    real verdict, so the unreviewed rows are exactly as usable here as
    CaseFlowGraph already treats them."""
    if not db.is_available():
        return []
    adjudicated, unreviewed = await asyncio.gather(
        asyncio.to_thread(db.get_recent_adjudications_for_domain_summary, days=days, limit=5000),
        asyncio.to_thread(db.get_recent_unreviewed_system_events, days=days, limit=5000),
    )
    return adjudicated + unreviewed


async def _load_cases(days: int) -> dict:
    events = await _load_events(days)
    return pm.build_cases(events)


@router.get("/processes")
async def list_processes():
    return {
        "processes": [
            {"id": pid, "label": tmpl["label"], "steps": tmpl["steps"]}
            for pid, tmpl in pm.PROCESS_TEMPLATES.items()
        ]
    }


@router.get("/summary")
async def summary(days: int = 30):
    if not db.is_available():
        return {"total_cases": 0, "untemplated_cases": 0, "processes": {}, "note": "Database not configured"}
    events = await _load_events(days)
    return {**pm.summary(events), "window_days": days}


@router.get("/variants")
async def variants(days: int = 30, process: Optional[str] = None):
    cases = await _load_cases(days)
    return {"variants": pm.variant_analysis(cases, process), "process": process, "window_days": days}


@router.get("/conformance")
async def conformance(days: int = 30, process: Optional[str] = None):
    cases = await _load_cases(days)
    return {**pm.conformance_summary(cases, process), "window_days": days}


@router.get("/cycle-times")
async def cycle_times(days: int = 30, process: Optional[str] = None):
    cases = await _load_cases(days)
    return {**pm.cycle_time_stats(cases, process), "window_days": days}


@router.get("/rework")
async def rework(days: int = 30, process: Optional[str] = None):
    cases = await _load_cases(days)
    return {**pm.rework_summary(cases, process), "window_days": days}


@router.get("/cases")
async def cases_endpoint(days: int = 30, process: Optional[str] = None, limit: int = 200):
    cases = await _load_cases(days)
    out = pm.list_case_summaries(cases, process)
    return {"cases": out[:limit], "total": len(out), "window_days": days}
