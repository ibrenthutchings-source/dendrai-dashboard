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
    POST /predictive/full-analysis    All 10 analytics models in one call
    POST /edgar/financials            XBRL financial time-series
    POST /edgar/risk-factors          Item 1A risk factors from 10-K filings
    POST /rss/news                    Industry RSS feed analysis
    POST /fred/correlations           FRED macro leading indicator correlations
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
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

try:
    from fred_tool import run_analysis as fred_run_analysis
    _HAS_FRED = True
except ImportError:
    _HAS_FRED = False


app = FastAPI(title="Dendrai MCP API", version="1.0.0", docs_url="/docs")

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


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "dendrai-mcp-api"}


@app.post("/predictive/full-analysis")
def predictive_full_analysis(req: FullAnalysisRequest):
    """
    Run all 10 Dendrai Risk Loop predictive analytics models.
    Returns financial ratios, Beneish M-Score, industry risk scores,
    scenario analysis, Grey Swan, FRED macro indicators, ensemble forecast,
    backtest metrics, RSS signals, and QoQ revenue momentum.
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
        return {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "cik": meta["cik_plain"],
            "sic": meta.get("sic", ""),
            "sic_description": meta.get("sic_description", ""),
            "xbrl": xbrl,
        }
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
        return {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "filings": results,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rss/news")
def rss_news(req: RssRequest):
    """Find top RSS feeds for a company's industry and download recent articles."""
    out_path = Path(tempfile.mktemp(suffix=".json"))
    try:
        result = run_rss_analysis(ticker=req.ticker, output_path=out_path)
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
        return {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "events": events,
        }
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
        return {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "sic": sic,
            "sic_description": meta.get("sic_description", ""),
            "peers": peers,
        }
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
        return {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "proxy_filings": results,
        }
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
    try:
        out_path = Path(tempfile.mktemp(suffix=".json"))
        result = fred_run_analysis(
            ticker=req.ticker,
            api_key=api_key,
            min_r=req.min_correlation,
            output_path=out_path,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if out_path.exists():
            out_path.unlink(missing_ok=True)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dendrai MCP API Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    print(f"Dendrai MCP API  →  http://{args.host}:{args.port}")
    print(f"  Docs           →  http://{args.host}:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port)
