#!/usr/bin/env python3
"""
Dendrai MCP API Server

HTTP bridge that exposes the Python MCP tool functions as REST endpoints so
the browser-based Risk Loop app can call them through the Vite dev-server proxy.

Usage:
    pip install -r requirements.txt
    python api_server.py              # default http://127.0.0.1:8001
    python api_server.py --port 8002

Vite proxy (add to vite.config.js server.proxy):
    '/api/mcp': { target: 'http://127.0.0.1:8001', changeOrigin: true,
                  rewrite: p => p.replace(/^[/]api[/]mcp/, '') }

Endpoints:
    GET  /health
    GET  /db/status
    POST /predictive/full-analysis    All 10 analytics models in one call
    POST /edgar/financials            XBRL financial time-series
    POST /edgar/risk-factors          Item 1A risk factors from 10-K filings
    POST /edgar/8k-events             Material 8-K events
    POST /edgar/peers                 SIC peer companies
    POST /edgar/proxy                 DEF 14A proxy governance sections
    POST /rss/news                    Industry RSS feed analysis
    POST /fred/correlations           FRED macro leading indicator correlations

    GET  /history/{tool_name}         Recent saved results for a tool
    GET  /history/{tool_name}/{ticker} Ticker-specific saved results
"""

import argparse
import logging
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

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
    extract_risk_factors,
    extract_proxy_sections,
    fetch_sic_peers,
    parse_filings,
    fetch_filing_text,
    annotate_8k,
)
from rss_tool import run_rss_analysis
import db

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
    if db_ready:
        logger.info("Database persistence: ENABLED")
    else:
        logger.info("Database persistence: DISABLED (set DATABASE_URL to enable)")
    yield


app = FastAPI(
    title="Dendrai MCP API",
    version="1.1.0",
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# ── Utility ────────────────────────────────────────────────────────────────────

def _company_name_from_result(result: dict, fallback: str = "") -> str:
    """Extract company_name from any tool result dict."""
    for key in ("company_name", "company", "name"):
        val = result.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, dict):
            return val.get("company_name", "") or val.get("name", "")
    return fallback


# ── Infrastructure endpoints ───────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "dendrai-mcp-api"}


@app.get("/db/status")
def db_status():
    """Report whether PostgreSQL persistence is active."""
    return {
        "database_enabled": db.is_available(),
        "note": "Set DATABASE_URL env var to enable persistence." if not db.is_available() else "",
    }


# ── Tool endpoints ─────────────────────────────────────────────────────────────

@app.post("/predictive/full-analysis")
def predictive_full_analysis(req: FullAnalysisRequest):
    """
    Run all 10 Dendrai Risk Loop predictive analytics models.
    Returns financial ratios, Beneish M-Score, industry risk scores,
    scenario analysis, Grey Swan, FRED macro indicators, ensemble forecast,
    backtest metrics, RSS signals, and QoQ revenue momentum.
    Result is saved to the database when DATABASE_URL is configured.
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

        company_name = _company_name_from_result(result, req.ticker)
        saved_id = db.save_result("risk_loop", req.ticker, company_name, result)
        if saved_id:
            result["_db_id"] = saved_id

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/edgar/financials")
def edgar_financials(req: TickerRequest):
    """Return XBRL financial time-series for the past 5 years."""
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
        saved_id = db.save_result("edgar_financials", req.ticker, meta["company_name"], result)
        if saved_id:
            result["_db_id"] = saved_id
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/edgar/risk-factors")
def edgar_risk_factors_endpoint(req: RiskFactorsRequest):
    """Return Item 1A Risk Factors from the most recent 10-K filings."""
    try:
        meta, sub = get_company_info(req.ticker)
        filings = parse_filings(sub, {"10-K"})["10-K"][: req.max_filings]
        results = []
        for f in filings:
            text = fetch_filing_text(meta["cik"], f)
            risks = extract_risk_factors(text) if text else ""
            results.append({
                "filing_date": f["date"],
                "accession_number": f["accession_number"],
                "risk_factors": risks[:30_000],
            })
        result = {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "filings": results,
        }
        saved_id = db.save_result("edgar_risk_factors", req.ticker, meta["company_name"], result)
        if saved_id:
            result["_db_id"] = saved_id
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rss/news")
def rss_news(req: RssRequest):
    """Find top RSS feeds for a company's industry and download recent articles."""
    out_path = Path(tempfile.mktemp(suffix=".json"))
    try:
        result = run_rss_analysis(ticker=req.ticker, output_path=out_path)
        company_name = result.get("company_name", req.ticker)
        saved_id = db.save_result("rss_news", req.ticker, company_name, result)
        if saved_id:
            result["_db_id"] = saved_id
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if out_path.exists():
            out_path.unlink(missing_ok=True)


@app.post("/edgar/8k-events")
def edgar_8k_events(req: TickerRequest):
    """Return annotated 8-K material events from the past 5 years."""
    try:
        meta, sub = get_company_info(req.ticker)
        filings = parse_filings(sub, {"8-K"})["8-K"][:30]
        events = [annotate_8k(dict(f)) for f in filings]
        result = {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "events": events,
        }
        saved_id = db.save_result("edgar_8k_events", req.ticker, meta["company_name"], result)
        if saved_id:
            result["_db_id"] = saved_id
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/edgar/peers")
def edgar_peers(req: TickerRequest):
    """Return SIC peer companies (name, ticker, CIK, SIC)."""
    try:
        meta, _ = get_company_info(req.ticker)
        sic = meta.get("sic", "")
        peers = fetch_sic_peers(sic, max_peers=15)
        result = {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "sic": sic,
            "sic_description": meta.get("sic_description", ""),
            "peers": peers,
        }
        saved_id = db.save_result("edgar_peers", req.ticker, meta["company_name"], result)
        if saved_id:
            result["_db_id"] = saved_id
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/edgar/proxy")
def edgar_proxy(req: RiskFactorsRequest):
    """Return governance sections extracted from DEF 14A proxy filings."""
    try:
        meta, sub = get_company_info(req.ticker)
        filings = parse_filings(sub, {"DEF 14A"})["DEF 14A"][: req.max_filings]
        results = []
        for f in filings:
            text = fetch_filing_text(meta["cik"], f)
            sections = extract_proxy_sections(text) if text else {}
            results.append({
                "filing_date": f["date"],
                "accession_number": f["accession_number"],
                "sections": {k: v[:8_000] for k, v in sections.items()},
            })
        result = {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "proxy_filings": results,
        }
        saved_id = db.save_result("edgar_proxy", req.ticker, meta["company_name"], result)
        if saved_id:
            result["_db_id"] = saved_id
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fred/correlations")
def fred_correlations(req: FredRequest):
    """Identify leading FRED macro indicators correlated with company financials."""
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
        company_name = result.get("company_name", req.ticker)
        saved_id = db.save_result("fred_correlations", req.ticker, company_name, result)
        if saved_id:
            result["_db_id"] = saved_id
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if out_path.exists():
            out_path.unlink(missing_ok=True)


# ── History endpoints ──────────────────────────────────────────────────────────

_VALID_TOOLS = {
    "risk_loop",
    "edgar_financials",
    "edgar_risk_factors",
    "edgar_8k_events",
    "edgar_peers",
    "edgar_proxy",
    "rss_news",
    "fred_correlations",
}


@app.get("/history/{tool_name}")
def history_tool(
    tool_name: str,
    limit: int = Query(default=20, ge=1, le=100),
):
    """
    Return the most recent saved results for a tool across all tickers.
    tool_name must be one of: risk_loop, edgar_financials, edgar_risk_factors,
    edgar_8k_events, edgar_peers, edgar_proxy, rss_news, fred_correlations.
    """
    if tool_name not in _VALID_TOOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tool '{tool_name}'. Valid tools: {sorted(_VALID_TOOLS)}",
        )
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL not set)")
    rows = db.get_results(tool_name, limit=limit)
    return {
        "tool_name": tool_name,
        "count": len(rows),
        "results": rows,
    }


@app.get("/history/{tool_name}/{ticker}")
def history_ticker(
    tool_name: str,
    ticker: str,
    limit: int = Query(default=10, ge=1, le=50),
):
    """
    Return saved results for a specific tool + ticker combination.
    Useful for comparing successive runs of the Risk Loop on the same company.
    """
    if tool_name not in _VALID_TOOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tool '{tool_name}'. Valid tools: {sorted(_VALID_TOOLS)}",
        )
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL not set)")
    rows = db.get_results(tool_name, ticker=ticker, limit=limit)
    return {
        "tool_name": tool_name,
        "ticker": ticker.upper(),
        "count": len(rows),
        "results": rows,
    }


@app.get("/history/{tool_name}/{ticker}/latest")
def history_ticker_latest(tool_name: str, ticker: str):
    """Return only the most recent saved result for a tool + ticker."""
    if tool_name not in _VALID_TOOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tool '{tool_name}'. Valid tools: {sorted(_VALID_TOOLS)}",
        )
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL not set)")
    rows = db.get_results(tool_name, ticker=ticker, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No saved results for {tool_name}/{ticker.upper()}")
    return rows[0]


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dendrai MCP API Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    print(f"Dendrai MCP API  →  http://{args.host}:{args.port}")
    print(f"  Docs           →  http://{args.host}:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port)
