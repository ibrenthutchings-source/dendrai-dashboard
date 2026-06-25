#!/usr/bin/env python3
"""
Dendrai MCP API Server  v2.0.0

HTTP bridge exposing Python MCP tool functions as REST endpoints.

Endpoints:
    GET  /health
    GET  /db/status

    POST /predictive/full-analysis    All 10 analytics models
    POST /edgar/financials            XBRL financial time-series
    POST /edgar/risk-factors          Item 1A risk factors from 10-K filings
    POST /edgar/8k-events             Material 8-K events
    POST /edgar/peers                 SIC peer companies
    POST /edgar/proxy                 DEF 14A proxy governance sections
    POST /rss/news                    Industry RSS feed analysis (ticker-based discovery)
    POST /rss/ingest                  Compliance RSS feeds with server-side TTL cache
    GET  /rss/feeds/status            Cache health for all compliance RSS feeds
    POST /fred/correlations           FRED macro leading indicator correlations

    POST /loop/hitl/risk-approvals    Gate 1 per-risk HITL decisions
    POST /loop/hitl/scope-approvals   Gate 2 per-objective HITL decisions
    POST /loop/persist                Loop completion batch (log, CEM, objectives, manual audits)

    GET  /history/runs/{ticker}              Recent runs for a ticker
    GET  /history/runs/{ticker}/{run_id}     Single run detail
"""

import argparse
import concurrent.futures
import logging
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from predictive_analytics_tool import run_full_analysis
from edgar_tool import (
    get_company_info,
    fetch_xbrl_facts,
    summarize_xbrl_annual,
    extract_risk_factors,
    extract_proxy_sections,
    fetch_sic_peers,
    parse_filings,
    fetch_filing_text,
    annotate_8k,
)
from rss_tool import run_rss_analysis
from rss_ingest_service import ingest_feeds, get_feed_status, FEEDS as RSS_INGEST_FEEDS
import db

import ai_endpoints
import claude_client
import peer_intel
import risks_as_code
import oracle_fusion_endpoints
import sox_endpoints
from sox_scoping_tool import run_sox_scoping, compute_input_hash

try:
    from fred_tool import run_analysis as fred_run_analysis
    _HAS_FRED = True
except ImportError:
    _HAS_FRED = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    db_ready = db.init_db()
    logger.info("Database persistence: %s", "ENABLED" if db_ready else "DISABLED (set DATABASE_URL to enable)")
    yield


app = FastAPI(
    title="Dendrai MCP API",
    version="2.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# AI-augmented endpoints (recommendations #1–#4). Active only when ANTHROPIC_API_KEY
# is set; otherwise each route returns 503 and the deterministic pipeline is unaffected.
app.include_router(ai_endpoints.router)

# Risks-as-Code: OSCAL + COSO ERM translators + SSE live stream.
app.include_router(risks_as_code.router)

# Oracle Fusion: control library, test results, issues, SOD, audit events.
app.include_router(oracle_fusion_endpoints.router)

# SOX Scope: materiality, accounts, processes, systems, segment coverage.
app.include_router(sox_endpoints.router)


# ── Request models ─────────────────────────────────────────────────────────────

class TickerRequest(BaseModel):
    ticker: str

class FullAnalysisRequest(BaseModel):
    ticker: str
    industry: str = ""
    fred_api_key: str = ""
    forecast_horizon: int = 4
    forecast_metric: str = "Revenue"
    include_rss: bool = True
    include_fred: bool = True

class RiskFactorsRequest(BaseModel):
    ticker: str
    max_filings: int = 2

class FredRequest(BaseModel):
    ticker: str
    api_key: str = ""
    min_correlation: float = 0.75

class RssRequest(BaseModel):
    ticker: str

class RssIngestRequest(BaseModel):
    feed_ids: List[str] = []
    force_refresh: bool = False
    ttl_minutes: int = 30

class RiskApprovalsRequest(BaseModel):
    run_id: int
    persona: str = ""
    approvals: Dict[str, Any] = {}

class ScopeApprovalsRequest(BaseModel):
    run_id: int
    persona: str = ""
    approvals: Dict[str, Any] = {}

class LoopPersistRequest(BaseModel):
    run_id: int
    persona: str = ""
    loop_log: List[Any] = []
    objectives: List[Any] = []
    cem_events: List[Any] = []
    manual_audits: List[Any] = []


# ── Utility ────────────────────────────────────────────────────────────────────

def _company_name_from_result(result: dict, fallback: str = "") -> str:
    for key in ("company_name", "company", "name"):
        val = result.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, dict):
            return val.get("company_name", "") or val.get("name", "")
    return fallback


def _persist_full_analysis(req: FullAnalysisRequest, result: dict) -> Optional[int]:
    """Normalize and save a full_analysis result to the DB. Returns run_id."""
    if not db.is_available():
        return None

    company_meta = {
        "ticker": result.get("ticker", req.ticker),
        "company_name": result.get("company_name", req.ticker),
        "cik": result.get("cik", ""),
        "sic": result.get("sic", ""),
        "sic_description": result.get("sic_description", ""),
    }
    company_id = db.upsert_company(company_meta)

    run_config = {
        "ticker": result.get("ticker", req.ticker),
        "industry": result.get("industry", req.industry),
        "data_mode": "mcp",
        "forecast_metric": req.forecast_metric,
        "forecast_horizon": req.forecast_horizon,
    }
    run_id = db.create_risk_loop_run(company_id, run_config)
    if not run_id:
        return None

    db.save_financial_ratios(run_id, result.get("financial_ratios", {}))
    db.save_beneish_mscore(run_id, result.get("beneish_mscore", {}))

    risk_data = result.get("risk_scores", {})
    db.save_risk_scores(run_id, risk_data.get("risks", []))

    db.save_scenario_analyses(run_id, result.get("scenario_analysis", {}))
    db.save_grey_swan(run_id, result.get("grey_swan", {}))

    if result.get("forecast"):
        db.save_forecasts(run_id, req.forecast_metric, result["forecast"])
    if result.get("backtest"):
        db.save_backtest_metrics(run_id, result["backtest"])
    if result.get("qoq_momentum"):
        db.save_qoq_momentum(run_id, result["qoq_momentum"])
    if result.get("rss_signals"):
        db.save_rss_signals(run_id, result["rss_signals"])
    if result.get("analyst_series"):
        db.save_analyst_kpi_series(run_id, result["analyst_series"])
        if result["analyst_series"].get("eps_forecast"):
            db.save_forecasts(run_id, "EPS_Diluted", result["analyst_series"]["eps_forecast"])

    db.complete_risk_loop_run(run_id)

    # Auto-rescope SOX if a config exists for this company + current FY
    if company_id:
        try:
            from datetime import datetime as _dt
            fiscal_year = f"FY{_dt.utcnow().year}"
            sox_cfg = db.get_sox_config(company_id, fiscal_year)
            forecast_data  = result.get("forecast") or {}
            risk_data      = result.get("risk_scores") or {}
            ratios_data    = result.get("financial_ratios") or {}

            if forecast_data.get("forecasts") and risk_data.get("risks"):
                input_hash = compute_input_hash(forecast_data, risk_data, ratios_data)
                prev_scope = db.get_sox_scoping_result(run_id)
                needs_rescope = (prev_scope is None) or (prev_scope.get("input_hash") != input_hash)

                if needs_rescope:
                    systems = db.list_sox_systems(company_id)
                    segments = db.get_sox_segments(company_id, fiscal_year)
                    mat_pct  = sox_cfg["materiality_pct"]  if sox_cfg else 5.0
                    perf_pct = sox_cfg["performance_mat_pct"] if sox_cfg else 75.0
                    sox_result = run_sox_scoping(
                        run_id=run_id,
                        forecast=forecast_data,
                        risk_scores=risk_data,
                        ratios=ratios_data,
                        systems_registry=systems,
                        segments=segments,
                        fiscal_year=fiscal_year,
                        materiality_pct=mat_pct,
                        performance_mat_pct=perf_pct,
                        trigger_reason="auto_rescope_on_new_run",
                    )
                    db.save_sox_scoping_result(run_id, company_id, sox_result)
                    prev_run_id = prev_scope.get("run_id") if prev_scope else None
                    db.log_sox_rescoping_trigger(
                        company_id=company_id,
                        trigger_type="new_forecast" if prev_scope else "initial_scope",
                        trigger_detail={"input_hash": input_hash, "fiscal_year": fiscal_year},
                        prev_run_id=prev_run_id,
                        new_run_id=run_id,
                        rescoped=True,
                    )
        except Exception as _sox_err:
            logger.warning("SOX auto-scoping failed (non-fatal): %s", _sox_err)

    return run_id


# ── Infrastructure endpoints ───────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "dendrai-mcp-api",
        "version": "2.0.0",
        "ai_enabled": claude_client.is_available(),
        "ai_model": claude_client.MODEL if claude_client.is_available() else None,
    }


@app.get("/db/status")
def db_status():
    return {
        "database_enabled": db.is_available(),
        "note": "" if db.is_available() else "Set DATABASE_URL env var to enable persistence.",
    }


# ── Tool endpoints ─────────────────────────────────────────────────────────────

@app.post("/predictive/full-analysis")
def predictive_full_analysis(req: FullAnalysisRequest):
    """
    Run all 10 Dendrai Risk Loop predictive analytics models and persist
    the full result to the normalized DB schema.
    """
    try:
        result = run_full_analysis(
            ticker=req.ticker,
            industry=req.industry,
            fred_api_key=req.fred_api_key or os.environ.get("FRED_API_KEY", ""),
            forecast_horizon=req.forecast_horizon,
            forecast_metric=req.forecast_metric,
            include_rss=req.include_rss,
            include_fred=req.include_fred,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

        run_id = _persist_full_analysis(req, result)
        if run_id:
            result["_db_id"] = run_id

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/edgar/financials")
def edgar_financials(req: TickerRequest):
    """Return XBRL financial time-series and save to normalized DB tables."""
    try:
        meta, _ = get_company_info(req.ticker)
        xbrl = fetch_xbrl_facts(meta["cik"])
        result = {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "cik": meta["cik_plain"],
            "sic": meta.get("sic", ""),
            "sic_description": meta.get("sic_description", ""),
            "xbrl": xbrl,
        }

        if db.is_available():
            company_id = db.upsert_company({
                "ticker": req.ticker,
                "company_name": meta["company_name"],
                "cik": meta.get("cik", ""),
                "sic": meta.get("sic", ""),
                "sic_description": meta.get("sic_description", ""),
                "entity_type": meta.get("entity_type"),
                "state_of_inc": meta.get("state_of_inc"),
                "fiscal_year_end": meta.get("fiscal_year_end"),
                "exchanges": meta.get("exchanges"),
            })
            if company_id:
                for metric_name, data_points in (xbrl or {}).items():
                    if isinstance(data_points, list) and data_points:
                        series_id = db.upsert_xbrl_series(company_id, metric_name)
                        if series_id:
                            db.save_xbrl_data_points(series_id, data_points)

        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/edgar/risk-factors")
def edgar_risk_factors_endpoint(req: RiskFactorsRequest):
    """Return Item 1A Risk Factors and save to edgar_risk_factor_filings."""
    try:
        meta, sub = get_company_info(req.ticker)
        filings = parse_filings(sub, {"10-K"})["10-K"][: req.max_filings]
        results = []
        company_id = None

        if db.is_available():
            company_id = db.upsert_company({
                "ticker": req.ticker,
                "company_name": meta["company_name"],
                "cik": meta.get("cik", ""),
                "sic": meta.get("sic", ""),
                "sic_description": meta.get("sic_description", ""),
            })

        for f in filings:
            text = fetch_filing_text(meta["cik"], f)
            risks = extract_risk_factors(text) if text else ""
            results.append({
                "filing_date": f["date"],
                "accession_number": f["accession_number"],
                "risk_factors": risks[:30_000],
            })
            if company_id:
                db.save_edgar_risk_factors(
                    company_id,
                    f["date"],
                    f["accession_number"],
                    risks[:30_000],
                )

        result = {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "filings": results,
        }
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rss/news")
def rss_news(req: RssRequest):
    """Find top RSS feeds for a company's industry and download recent articles."""
    out_path = Path(tempfile.mktemp(suffix=".json"))
    try:
        result = run_rss_analysis(ticker=req.ticker, output_path=out_path)

        if db.is_available():
            company_name = result.get("company_name", req.ticker)
            company_id = db.upsert_company({
                "ticker": req.ticker,
                "company_name": company_name,
            })
            if company_id:
                db.save_rss_articles_full(company_id, result)

        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if out_path.exists():
            out_path.unlink(missing_ok=True)


@app.post("/rss/ingest")
def rss_ingest(req: RssIngestRequest):
    """
    Fetch and grade the compliance/regulatory RSS feeds registered in the dashboard
    (BIS Export Controls, CISA ICS, SEC EDGAR, Federal Reserve Press, EPA Climate).

    Results are cached server-side (default 30-min TTL) so repeated pipeline runs
    reuse warm signals without re-fetching every feed. Pass force_refresh=true to
    bypass the cache.

    Returns articles in the same shape as RSS_ENGINE.ingestAll() on the frontend,
    so the existing signal-mapping code in app.jsx applies without modification.
    """
    try:
        result = ingest_feeds(
            feed_ids=req.feed_ids or None,
            force_refresh=req.force_refresh,
            ttl_minutes=req.ttl_minutes,
        )

        # Persist graded articles to DB for velocity trending (best-effort)
        if db.is_available() and result.get("feeds"):
            db.save_rss_articles_full(None, {"feeds": [
                {
                    "name": r["feed"]["name"],
                    "url":  r["feed"]["url"],
                    "articles": [
                        {
                            "title":     a["title"],
                            "url":       a.get("url"),
                            "published": a.get("pubDate"),
                            "summary":   a.get("label"),
                        }
                        for a in r["articles"]
                    ],
                }
                for r in result["feeds"]
            ]})

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/rss/feeds/status")
def rss_feeds_status():
    """
    Return per-feed cache health for all compliance RSS feeds registered in the
    dashboard. Shows last_fetched timestamp, article count, and fetch_status
    (ok / failed / not_fetched) for each feed.
    """
    return {
        "feeds": get_feed_status(),
        "registered": len(RSS_INGEST_FEEDS),
    }


@app.post("/edgar/8k-events")
def edgar_8k_events(req: TickerRequest):
    """Return annotated 8-K material events and save to edgar_8k_events."""
    try:
        meta, sub = get_company_info(req.ticker)
        filings = parse_filings(sub, {"8-K"})["8-K"][:30]
        events = [annotate_8k(dict(f)) for f in filings]
        result = {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "events": events,
        }

        if db.is_available():
            company_id = db.upsert_company({
                "ticker": req.ticker,
                "company_name": meta["company_name"],
                "cik": meta.get("cik", ""),
                "sic": meta.get("sic", ""),
            })
            if company_id:
                db.save_edgar_8k_events(company_id, events)

        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _enrich_peer_financials(peer: dict) -> dict:
    """Fetch XBRL facts for a peer and attach gross_margin, rd_intensity, revenue_growth."""
    try:
        cik = str(peer.get("cik") or peer.get("cik_plain") or "").zfill(10)
        if not cik or cik == "0000000000":
            return peer
        xbrl = fetch_xbrl_facts(cik)
        if not xbrl:
            return peer

        def latest_two_annual(metric):
            pts = [p for p in xbrl.get(metric, {}).get("data_points", [])
                   if p.get("form") in {"10-K", "20-F", "10-K/A"} and p.get("val") is not None]
            pts.sort(key=lambda p: p.get("end", ""), reverse=True)
            curr = pts[0]["val"] if len(pts) > 0 else None
            prev = pts[1]["val"] if len(pts) > 1 else None
            return curr, prev

        rev,    rev_prev = latest_two_annual("Revenue")
        gp,     _        = latest_two_annual("GrossProfit")
        rd,     _        = latest_two_annual("ResearchAndDevelopment")

        peer["gross_margin"]   = (gp  / rev) if rev and gp  is not None else None
        peer["rd_intensity"]   = (rd  / rev) if rev and rd  is not None else None
        peer["revenue_growth"] = ((rev - rev_prev) / rev_prev) if rev and rev_prev else None
    except Exception:
        pass
    return peer


def _peer_has_data(peer: dict) -> bool:
    """A peer is kept only if at least one financial benchmark resolved."""
    return any(peer.get(k) is not None for k in ("gross_margin", "rd_intensity", "revenue_growth"))


@app.post("/edgar/peers")
def edgar_peers(req: TickerRequest):
    """
    Peer intelligence. Primary source is the competitors the company names in its
    own latest 10-K (resolved to EDGAR CIK/ticker); falls back to SIC-code peers
    when 10-K extraction is unavailable. Companies with no financial data are dropped.
    """
    try:
        meta, sub = get_company_info(req.ticker)
        sic = meta.get("sic", "")

        # 1) Primary — competitors the filer names itself in the 10-K.
        named_competitors: list = []
        peers: list = []
        peer_source = "10-K named competitors"
        try:
            named_competitors = peer_intel.extract_competitor_names(req.ticker, meta, sub)
            if named_competitors:
                peers = peer_intel.resolve_names_to_edgar(
                    named_competitors, exclude_cik=meta.get("cik_plain", "")
                )
        except Exception as exc:
            logger.info("10-K competitor extraction failed: %s", exc)

        # 2) Fallback — SIC peers when no 10-K-named peers resolved.
        if not peers:
            peer_source = "SIC peers"
            peers = fetch_sic_peers(sic, max_peers=15)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            peers = list(pool.map(_enrich_peer_financials, peers))

        # 3) Remove all companies that have no data.
        peers = [p for p in peers if _peer_has_data(p)]

        result = {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "sic": sic,
            "sic_description": meta.get("sic_description", ""),
            "peer_source": peer_source,
            "named_competitors": named_competitors,
            "peers": peers,
        }

        if db.is_available():
            company_id = db.upsert_company({
                "ticker": req.ticker,
                "company_name": meta["company_name"],
                "cik": meta.get("cik", ""),
                "sic": sic,
                "sic_description": meta.get("sic_description", ""),
            })
            if company_id:
                db.save_sic_peers(company_id, peers)

        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/edgar/proxy")
def edgar_proxy(req: RiskFactorsRequest):
    """Return DEF 14A proxy governance sections and save to edgar_proxy_filings."""
    try:
        meta, sub = get_company_info(req.ticker)
        filings = parse_filings(sub, {"DEF 14A"})["DEF 14A"][: req.max_filings]
        results = []
        company_id = None

        if db.is_available():
            company_id = db.upsert_company({
                "ticker": req.ticker,
                "company_name": meta["company_name"],
                "cik": meta.get("cik", ""),
                "sic": meta.get("sic", ""),
            })

        for f in filings:
            text = fetch_filing_text(meta["cik"], f)
            sections = extract_proxy_sections(text) if text else {}
            truncated = {k: v[:8_000] for k, v in sections.items()}
            results.append({
                "filing_date": f["date"],
                "accession_number": f["accession_number"],
                "sections": truncated,
            })
            if company_id:
                db.save_edgar_proxy(
                    company_id,
                    f["date"],
                    f["accession_number"],
                    sections,
                )

        result = {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "proxy_filings": results,
        }
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fred/correlations")
def fred_correlations(req: FredRequest):
    """Identify FRED macro leading indicators and save correlations to DB."""
    if not _HAS_FRED:
        raise HTTPException(status_code=503, detail="fred_tool not available")
    api_key = req.api_key or os.environ.get("FRED_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="FRED_API_KEY required — set env var or pass api_key in request body",
        )
    out_path = Path(tempfile.mktemp(suffix=".json"))
    try:
        result = fred_run_analysis(
            ticker=req.ticker,
            api_key=api_key,
            min_r=req.min_correlation,
            output_path=out_path,
        )

        if db.is_available():
            company_name = result.get("company_name", req.ticker)
            company_id = db.upsert_company({
                "ticker": req.ticker,
                "company_name": company_name,
            })
            if company_id:
                # Save series + observations
                if result.get("series_data"):
                    db.save_fred_series_and_observations(result["series_data"])
                # Save correlations
                indicators = result.get("significant_indicators", result.get("correlations", []))
                if indicators:
                    db.save_fred_correlations(company_id, indicators)

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if out_path.exists():
            out_path.unlink(missing_ok=True)


# ── HITL persistence endpoints ─────────────────────────────────────────────────

@app.post("/loop/hitl/risk-approvals")
def persist_risk_approvals(req: RiskApprovalsRequest):
    """
    Persist Gate 1 per-risk HITL decisions from the frontend.
    Called fire-and-forget from app.jsx when the user confirms Gate 1.
    """
    if not db.is_available():
        return {"saved": False, "reason": "database not configured"}
    if not req.approvals:
        return {"saved": False, "reason": "no approvals provided"}

    db.save_risk_approvals(req.run_id, req.approvals, req.persona or None)
    return {"saved": True, "run_id": req.run_id, "count": len(req.approvals)}


@app.post("/loop/hitl/scope-approvals")
def persist_scope_approvals(req: ScopeApprovalsRequest):
    """
    Persist Gate 2 per-objective HITL decisions from the frontend.
    Called fire-and-forget from app.jsx when the user confirms Gate 2.
    """
    if not db.is_available():
        return {"saved": False, "reason": "database not configured"}
    if not req.approvals:
        return {"saved": False, "reason": "no approvals provided"}

    db.save_objective_approvals(req.run_id, req.approvals, req.persona or None)
    return {"saved": True, "run_id": req.run_id, "count": len(req.approvals)}


@app.post("/loop/persist")
def persist_loop_completion(req: LoopPersistRequest):
    """
    Batch-save loop completion data: loop log, audit objectives, CEM events,
    manual audits. Called fire-and-forget from app.jsx after the loop finishes.
    """
    if not db.is_available():
        return {"saved": False, "reason": "database not configured"}

    db.save_loop_log(req.run_id, req.loop_log)
    db.save_audit_objectives(req.run_id, req.objectives)
    db.save_cem_events(req.run_id, req.cem_events)
    db.save_manual_audits(req.run_id, req.manual_audits)

    return {
        "saved": True,
        "run_id": req.run_id,
        "log_entries": len(req.loop_log),
        "objectives": len(req.objectives),
        "cem_events": len(req.cem_events),
        "manual_audits": len(req.manual_audits),
    }


# ── History endpoints ──────────────────────────────────────────────────────────

@app.get("/history/runs/{ticker}")
def history_runs(
    ticker: str,
    limit: int = Query(default=20, ge=1, le=100),
):
    """Recent risk loop runs for a ticker, newest first."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL not set)")
    rows = db.get_run_history(ticker, limit=limit)
    return {"ticker": ticker.upper(), "count": len(rows), "runs": rows}


@app.get("/history/runs/{run_id}/ai-analyses")
def history_ai_analyses(run_id: int, kind: str = Query(default="")):
    """AI/LLM outputs persisted for a run (gate recs, narrative, persona, agent memo)."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL not set)")
    rows = db.get_ai_analyses(run_id, kind=kind or None)
    return {"run_id": run_id, "kind": kind or "all", "count": len(rows), "analyses": rows}


@app.get("/history/runs/{run_id}/token-cost")
def history_token_cost(run_id: int):
    """Aggregate token usage and estimated cost for all AI calls in a run."""
    if not db.is_available():
        return {"run_id": run_id, "total_cost_usd": 0.0, "total_input_tokens": 0,
                "total_output_tokens": 0, "by_kind": [], "db_unavailable": True}
    return db.get_run_token_cost(run_id)


@app.get("/history/runs/{ticker}/{run_id}")
def history_run_detail(ticker: str, run_id: int):
    """Full detail for a single run including risk scores and Beneish M-Score."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL not set)")
    detail = db.get_run_detail(run_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if detail.get("ticker", "").upper() != ticker.upper():
        raise HTTPException(status_code=404, detail=f"Run {run_id} does not belong to ticker {ticker.upper()}")
    return detail


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dendrai MCP API Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    print(f"Dendrai MCP API  →  http://{args.host}:{args.port}")
    print(f"  Docs           →  http://{args.host}:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port)
