#!/usr/bin/env python3
"""
Dendrai MCP API Server  v2.1.0

HTTP bridge exposing Python MCP tool functions as REST endpoints,
plus MCP Streamable-HTTP endpoints for claude.ai integration.

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

    GET  /rss/feeds                          RSS feed registry (canonical — JS loads from here)
    GET  /scoring/config                     Domain vocab, severity weights, domain→risk mapping, Item 1A keywords
    GET  /edgar/8k-items                     All 24 8-K item code → SEC description entries
    GET  /industry/from-sic                  SIC code → Dendrai industry label

    POST /rac/from-loop   Risk-as-Code from JSON risk array (loop output)
    POST /rac/from-db     Risk-as-Code from PostgreSQL risk_scores table
    POST /rac/from-excel  Risk-as-Code from uploaded .xlsx / .xls / .csv file

    GET  /config/code-editor/{storage_key}   Retrieve saved YAML editor content
    PUT  /config/code-editor/{storage_key}   Persist YAML editor content to DB

    GET  /config/pipeline                    Retrieve saved pipeline configuration (ticker, signals, HITL, etc.)
    PUT  /config/pipeline                    Persist pipeline configuration to DB

    GET  /loop/last-state                    Retrieve last saved pipeline run state (for page-reload restoration)
    PUT  /loop/last-state                    Persist full pipeline run state to DB

MCP Streamable-HTTP (add these URLs to claude.ai → Settings → Integrations):
    /mcp/edgar/mcp              SEC EDGAR filings, financials, risk factors, 8-K events
    /mcp/fred/mcp               Federal Reserve macro economic indicators
    /mcp/rss/mcp                Industry & compliance RSS news feeds
    /mcp/token-cost/mcp         Anthropic API token cost tracking
    /mcp/predictive/mcp         Risk scoring, forecasting, predictive analytics
    /mcp/risk-as-code/mcp       Risk-as-Code OSCAL/COSO YAML generation
    /mcp/policy-as-code/mcp     Policy-as-Code Rego module management & approvals
    /mcp/controls-as-code/mcp   Controls-as-Code generation, evaluation & risk mapping
    /mcp/oracle/mcp             Oracle Fusion ERP data (requires ORACLE_FUSION_* env vars)

    GET  /mcp               Discovery — lists all mounted MCP servers and their URLs
"""

import argparse
import asyncio
import concurrent.futures
import logging
import os
import sys
import tempfile
from contextlib import asynccontextmanager, AsyncExitStack
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

import re
import requests
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, Security, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
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
from rss_ingest_service import (
    ingest_feeds, get_feed_status, FEEDS as RSS_INGEST_FEEDS,
    DOMAIN_VOCAB, SEVERITY_WORDS, DOMAIN_RISK_CATS, RISK_KW,
)
from edgar_tool import _8K_ITEMS as EDGAR_8K_ITEMS
import db

import ai_endpoints
import chat_endpoint
import claude_client
import peer_intel
import risks_as_code
import oracle_fusion_endpoints
import sox_endpoints
import risk_register_endpoints
import pac_endpoints
import approvals_endpoints
from sox_scoping_tool import run_sox_scoping, compute_input_hash

try:
    from fred_tool import run_analysis as fred_run_analysis
    _HAS_FRED = True
except ImportError:
    _HAS_FRED = False

# ── MCP server imports (FastMCP instances for HTTP mounting) ───────────────────
# Each server guards mcp.run() behind __main__, so importing is safe.

from edgar_mcp_server import mcp as _edgar_mcp
from fred_mcp_server import mcp as _fred_mcp
from rss_mcp_server import mcp as _rss_mcp
from token_cost_mcp_server import mcp as _token_cost_mcp
from predictive_analytics_mcp_server import mcp as _predictive_mcp
from risk_as_code_mcp_server import mcp as _rac_mcp
from pac_mcp_server import mcp as _pac_mcp
from cac_mcp_server import mcp as _cac_mcp

try:
    from oracle_fusion_mcp_server import mcp as _oracle_mcp
    _HAS_ORACLE_MCP = True
except Exception:
    _oracle_mcp = None
    _HAS_ORACLE_MCP = False

try:
    import mcp_governance
    _HAS_MCP_GOVERNANCE = True
except Exception as _gov_exc:
    mcp_governance = None  # type: ignore[assignment]
    _HAS_MCP_GOVERNANCE = False

try:
    import mcp_http_telemetry
    _HAS_HTTP_TELEMETRY = True
except Exception:
    mcp_http_telemetry = None  # type: ignore[assignment]
    _HAS_HTTP_TELEMETRY = False

import github_endpoints
import auth_db
import auth_endpoints

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    db_ready = db.init_db()
    logger.info("Database persistence: %s", "ENABLED" if db_ready else "DISABLED (set DATABASE_URL to enable)")
    if db_ready:
        # Seed static reference data into DB on first startup (idempotent)
        risk_register_endpoints.seed_static_data()
        _seed_cem_templates()
        _seed_ticker_cik()
        logger.info("Static reference data seeded")
        # Auth schema + default users
        if auth_db.init_auth_db():
            seeded = auth_db.seed_default_users()
            if seeded:
                logger.info("Auth: seeded %d default user(s)", seeded)
    # FastMCP Streamable-HTTP requires each server's session_manager task group to be
    # initialized during app lifespan. Starlette does not automatically propagate
    # lifespan events to mounted sub-apps, so we initialize them here explicitly.
    # Each init is guarded by a 5-second timeout: if session_manager.run().__aenter__
    # hangs (e.g. due to a FastMCP / anyio version mismatch), we log a warning and
    # continue rather than blocking the lifespan forever and keeping uvicorn down.
    async with AsyncExitStack() as stack:
        for inst in _mcp_instances:
            inst_name = getattr(inst, 'name', repr(inst))
            try:
                await asyncio.wait_for(
                    stack.enter_async_context(inst.session_manager.run()),
                    timeout=5.0,
                )
                logger.info("MCP session manager initialized: %s", inst_name)
            except asyncio.TimeoutError:
                logger.warning("MCP session manager timed out for %s — Streamable-HTTP may not work", inst_name)
            except Exception as exc:
                logger.warning("MCP session manager init failed for %s: %s", inst_name, exc)

        # Start MCP Governance polling (non-blocking background task)
        _gov_task = None
        if _HAS_MCP_GOVERNANCE and db.is_available():
            _gov_task = asyncio.create_task(mcp_governance.start_polling())
            logger.info("MCP governance polling task started")
        elif _HAS_MCP_GOVERNANCE:
            logger.info("MCP governance available but DB not ready — polling not started")

        # Background DB reconnect loop — retries every 30 s if startup DB init failed.
        # db.init_db() is blocking (DNS + TCP), so run it in a thread to avoid
        # stalling the event loop (which would cause 502s on all in-flight requests).
        async def _db_reconnect_loop():
            while True:
                await asyncio.sleep(30)
                if db.is_available():
                    continue
                logger.info("DB not available — retrying connection…")
                connected = await asyncio.to_thread(db.init_db)
                if connected:
                    logger.info("DB reconnected successfully")
                    await asyncio.to_thread(risk_register_endpoints.seed_static_data)
                    await asyncio.to_thread(_seed_cem_templates)
                    await asyncio.to_thread(_seed_ticker_cik)
                    if _HAS_MCP_GOVERNANCE and _gov_task is None:
                        asyncio.create_task(mcp_governance.start_polling())
                        logger.info("MCP governance polling started after DB reconnect")

        _reconnect_task = asyncio.create_task(_db_reconnect_loop())

        try:
            yield
        finally:
            _reconnect_task.cancel()
            if _gov_task is not None:
                _gov_task.cancel()
                try:
                    await _gov_task
                except asyncio.CancelledError:
                    pass


_CEM_TEMPLATES_DEFAULT = [
    {"control": "Revenue Recognition — Contract Review Gate",   "area": "Revenue",             "risk": "Revenue overstatement",        "severity": "P1", "exposure": "$12–18M",              "category": "Financial Reporting",
     "rc": "Most likely root cause: distributor attestation workflow regression after Q4 platform release. Containment: block billing on un-attested distributor contracts pending manual review. Systemic fix: re-platform RC-402 on contract lifecycle tool with mandatory gate."},
    {"control": "Export License Validation — ECCN Check",       "area": "Trade Compliance",    "risk": "Export violation",             "severity": "P1", "exposure": "Regulatory",           "category": "Trade Compliance",
     "rc": "Likely root cause: self-classified ECCN on new SKU shipped to Greater China without engineering review. Containment: hold shipments to affected end-users; flag for trade-counsel screen. Systemic fix: mandate engineering ECCN sign-off in product launch workflow."},
    {"control": "Segregation of Duties — AP Approval",          "area": "Accounts Payable",    "risk": "Fraudulent disbursement",      "severity": "P2", "exposure": "$2–5M",                "category": "Fraud Risk",
     "rc": "Likely root cause: temporary delegation during Q4 close granted both initiate and approve. Containment: revoke delegation; reverse last 30 days of dual-approved entries for review. Systemic fix: SoD matrix enforcement at workflow layer, not via policy alone."},
    {"control": "Inventory Count Reconciliation",               "area": "Supply Chain",        "risk": "Inventory misstatement",       "severity": "P2", "exposure": "$4–8M",                "category": "Operations",
     "rc": "Likely root cause: cycle count cadence skipped during line conversion. Containment: full count of WIP; reconcile against ERP. Systemic fix: automated reminders + escalation for skipped counts."},
    {"control": "Access Provisioning — Privileged Accounts",    "area": "IT General Controls", "risk": "Unauthorized access",          "severity": "P2", "exposure": "Data breach",          "category": "Cybersecurity",
     "rc": "Likely root cause: org-chart change orphaned 147 user re-certifications. Containment: emergency re-cert via skip-level approvers. Systemic fix: auto-detect org deltas and re-route pending certifications."},
    {"control": "Management Override Exception Log",            "area": "Financial Reporting", "risk": "Management override",          "severity": "P1", "exposure": "Material misstatement","category": "Financial Reporting",
     "rc": "Likely root cause: Q4 accrual posted with verbal CFO approval, no documented business case. Containment: require contemporaneous business case for >$1M overrides. Systemic fix: workflow-enforced documentation + AC visibility."},
    {"control": "Third-Party Vendor SOC 2 Review",              "area": "Vendor Management",   "risk": "Supply chain exposure",        "severity": "P3", "exposure": "Reputational",         "category": "Third-Party Risk",
     "rc": "Likely root cause: SOC 2 Type II reports not refreshed for tier-1 vendors. Containment: request current attestations. Systemic fix: vendor-portal auto-renewal cadence."},
    {"control": "Journal Entry Authorization — Month-End",      "area": "Accounting",          "risk": "Unauthorized JE manipulation", "severity": "P2", "exposure": "$1–3M",               "category": "Financial Reporting",
     "rc": "Likely root cause: JE approver pool included terminated employee for 8 days. Containment: void affected JEs; re-route. Systemic fix: HRIS-to-ERP role-revocation real-time sync."},
]

_TICKER_CIK_DEFAULT = {
    "ON":   "0001097864", "TXN":  "0000097476", "STM":  "0001114448", "MCHP": "0000827054",
    "NXPI": "0001413447", "ADI":  "0000006951", "SWKS": "0000004127", "QRVO": "0001604778",
    "MPWR": "0001280452", "WOLF": "0000895419", "AVGO": "0001730168", "NVDA": "0001045810",
    "INTC": "0000050863", "AMD":  "0000002488", "QCOM": "0000804328", "MRVL": "0001058057",
    "AMAT": "0000003153", "KLAC": "0000319201", "LRCX": "0000707549", "ASML": "0000937966",
    "AMKR": "0001047127", "ONTO": "0000074260", "TER":  "0000097216", "ENTG": "0001101302",
    "MU":   "0000723125", "WDC":  "0000106040", "F":    "0000037996",
}


def _seed_cem_templates() -> None:
    if not db.is_available():
        return
    seeded = db.seed_cem_event_templates(_CEM_TEMPLATES_DEFAULT)
    if seeded:
        logger.info("Seeded %d CEM event templates", seeded)


def _seed_ticker_cik() -> None:
    if not db.is_available():
        return
    seeded = db.seed_ticker_cik_map(_TICKER_CIK_DEFAULT)
    if seeded:
        logger.info("Seeded %d ticker→CIK mappings into companies table", seeded)


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

# ── Write-endpoint authentication ──────────────────────────────────────────────
# Set DENDRAI_API_KEY (Railway env var) and VITE_API_KEY (same value, build-time
# Vite var) to protect mutating endpoints from unauthenticated external writes.
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
_REQUIRED_API_KEY = os.environ.get("DENDRAI_API_KEY", "")

async def _require_api_key(key: str | None = Security(_API_KEY_HEADER)) -> None:
    if not _REQUIRED_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="DENDRAI_API_KEY not configured — set this env var to enable write endpoints",
        )
    if key != _REQUIRED_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing X-API-Key header")

# ── SSRF blocklist for rss-proxy ───────────────────────────────────────────────
# Blocks RFC-1918, loopback, link-local (AWS IMDS 169.254.x.x), IPv6 loopback,
# and the 0.x.x.x range (resolves to localhost on Linux).
_PRIVATE_HOST_RE = re.compile(
    r"^("
    r"localhost"
    r"|127\."
    r"|10\."
    r"|172\.(1[6-9]|2\d|3[01])\."
    r"|192\.168\."
    r"|169\.254\."     # link-local / AWS IMDS
    r"|0\."            # 0.x.x.x → loopback on Linux
    r"|::1$"           # IPv6 loopback
    r"|fc[0-9a-f]{2}:" # IPv6 unique local
    r"|fe[89ab][0-9a-f]:"  # IPv6 link-local
    r")",
    re.IGNORECASE,
)

# MCP HTTP Telemetry: captures tool calls arriving via Streamable-HTTP endpoints.
# Must be added after CORS so it wraps the full app (processes requests first).
if _HAS_HTTP_TELEMETRY:
    app.add_middleware(mcp_http_telemetry.MCPHttpTelemetryMiddleware)
    logger.info("MCP HTTP telemetry middleware registered")

# ── Auth middleware ────────────────────────────────────────────────────────────
# Validates the dendrai_session JWT cookie on all routes except the exemptions
# below.  Returns 401 JSON for API calls and 302 to /login for browser navigations.
_AUTH_EXEMPT = (
    "/auth/",
    "/health",
    "/db/status",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/mcp",           # MCP Streamable-HTTP — Claude authenticates separately
    "/github/",       # GitHub webhook uses its own HMAC verification
    "/rss/feeds",                    # read-only feed registry, fetched at JS module init time
    "/scoring/config",               # read-only scoring vocabulary, fetched at JS module init time
    "/observability/telemetry/ingest", # external systems auth via per-system Bearer key
)

from starlette.middleware.base import BaseHTTPMiddleware as _BaseHTTPMiddleware

class _DendraiAuthMiddleware(_BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path == p or path.startswith(p) for p in _AUTH_EXEMPT):
            return await call_next(request)
        cookie = request.cookies.get("dendrai_session")
        if not cookie:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        payload = auth_endpoints.decode_jwt(cookie)
        if not payload:
            return JSONResponse({"detail": "Invalid session token"}, status_code=401)
        if not auth_db.validate_session(payload.get("jti", "")):
            return JSONResponse({"detail": "Session expired"}, status_code=401)
        return await call_next(request)

app.add_middleware(_DendraiAuthMiddleware)
logger.info("Dendrai auth middleware registered")

# AI-augmented endpoints (recommendations #1–#4). Active only when ANTHROPIC_API_KEY
# is set; otherwise each route returns 503 and the deterministic pipeline is unaffected.
app.include_router(ai_endpoints.router)

# AI Chat: streaming conversational interface with MCP tool access.
app.include_router(chat_endpoint.router)

# Risks-as-Code: OSCAL + COSO ERM translators + SSE live stream.
app.include_router(risks_as_code.router)

# Oracle Fusion: control library, test results, issues, SOD, audit events.
app.include_router(oracle_fusion_endpoints.router)

# SOX Scope: materiality, accounts, processes, systems, segment coverage.
app.include_router(sox_endpoints.router)

# Risk Register Review: internal register management, framework ingestion, control mapping.
app.include_router(risk_register_endpoints.router)

# Policy-as-Code & Controls-as-Code: Rego module management, approvals, external hooks.
app.include_router(pac_endpoints.router, prefix="/api")

# MCP Governance: telemetry observability + adjudicated governance events.
if _HAS_MCP_GOVERNANCE:
    app.include_router(mcp_governance.router)
    logger.info("MCP governance router registered at /observability")

# Authentication: local login, logout, me, change-password, SSO OAuth flows.
app.include_router(auth_endpoints.router)
logger.info("Auth router registered at /auth")

# Approval workflow: real 2-stage preparer -> manager review for HITL gates.
app.include_router(approvals_endpoints.router)

# GitHub Webhook: receive repo events and run them through the UBO pipeline.
app.include_router(github_endpoints.router)
logger.info("GitHub webhook router registered at /github/webhook")

# ── MCP Streamable-HTTP mounts ─────────────────────────────────────────────────
# Each FastMCP instance is mounted as an ASGI sub-app so claude.ai can connect
# directly to this server without a separate process.
# Add URLs to claude.ai → Settings → Integrations as: https://<host>/mcp/<name>

_MCP_MOUNTS: list[tuple[str, str]] = []
_mcp_instances: list = []  # FastMCP instances captured at mount time for lifespan init

def _mount_mcp(path: str, label: str, mcp_instance) -> None:
    if mcp_instance is None:
        return
    try:
        # Disable FastMCP's built-in DNS-rebinding protection — host validation is
        # handled upstream by nginx and Railway's ingress, so allowing all hosts here
        # is safe and required for the Railway hostname to pass the 421 check.
        mcp_instance.settings.transport_security.enable_dns_rebinding_protection = False
        app.mount(path, mcp_instance.streamable_http_app())
        _MCP_MOUNTS.append((path, label))
        _mcp_instances.append(mcp_instance)
        logger.info("MCP HTTP mounted: %s", path)
    except Exception as exc:
        logger.warning("Failed to mount MCP server at %s: %s", path, exc)

_mount_mcp("/mcp/edgar",            "EDGAR filings & financials",               _edgar_mcp)
_mount_mcp("/mcp/fred",             "FRED macro indicators",                    _fred_mcp)
_mount_mcp("/mcp/rss",              "RSS news & compliance feeds",              _rss_mcp)
_mount_mcp("/mcp/token-cost",       "Anthropic API token cost tracking",        _token_cost_mcp)
_mount_mcp("/mcp/predictive",       "Risk scoring & predictive analytics",      _predictive_mcp)
_mount_mcp("/mcp/risk-as-code",     "Risk-as-Code OSCAL/COSO YAML generation",  _rac_mcp)
_mount_mcp("/mcp/policy-as-code",   "Policy-as-Code Rego module management",    _pac_mcp)
_mount_mcp("/mcp/controls-as-code", "Controls-as-Code generation & evaluation", _cac_mcp)
_mount_mcp("/mcp/oracle",           "Oracle Fusion ERP data",                   _oracle_mcp)


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
    ticker: Optional[str] = None

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
    risks_list = risk_data.get("risks", [])
    db.save_risk_scores(run_id, risks_list)

    scenario_dict = result.get("scenario_analysis", {})
    db.save_scenario_analyses(run_id, scenario_dict)
    db.save_grey_swan(run_id, result.get("grey_swan", {}))

    # Compute and persist graph relationships from this run's risk set
    if company_id and risks_list:
        try:
            db.compute_and_save_risk_relationships(company_id, run_id, risks_list)
            db.save_scenario_risk_impacts(run_id, risks_list, scenario_dict)
        except Exception as _rel_err:
            logger.warning("Risk relationship computation failed (non-fatal): %s", _rel_err)

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

@app.get("/mcp", tags=["mcp"])
def mcp_discovery():
    """List all mounted MCP servers. Add each URL to claude.ai → Settings → Integrations."""
    base = os.environ.get("PUBLIC_URL", "").rstrip("/")
    return {
        "servers": [
            {"name": label, "url": f"{base}{path}/mcp"}
            for path, label in _MCP_MOUNTS
        ],
        "note": "Set PUBLIC_URL env var to get absolute URLs (e.g. https://your-railway-app.up.railway.app)",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "dendrai-mcp-api",
        "version": "2.1.0",
        "ai_enabled": claude_client.is_available(),
        "ai_model": claude_client.MODEL if claude_client.is_available() else None,
    }


@app.get("/db/status")
def db_status():
    return {
        "database_enabled": db.is_available(),
        "note": "" if db.is_available() else "Set DATABASE_URL env var to enable persistence.",
    }


# ── CEM event templates ───────────────────────────────────────────────────────

@app.get("/cem-templates")
def get_cem_templates():
    """Return CEM event templates from DB (falls back to defaults if DB unavailable)."""
    if db.is_available():
        templates = db.get_cem_event_templates()
        if templates:
            return {"templates": templates, "source": "db"}
    return {"templates": _CEM_TEMPLATES_DEFAULT, "source": "default"}


@app.post("/cem-templates")
def upsert_cem_template(template: dict):
    """Add or update a CEM event template."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not connected — set DATABASE_URL")
    row_id = db.upsert_cem_event_template(template)
    return {"saved": row_id is not None, "id": row_id}


# ── Company / ticker lookup ───────────────────────────────────────────────────

@app.get("/company/cik/{ticker}")
def get_company_cik(ticker: str):
    """Return the CIK for a ticker from the companies table (zero-padded to 10 digits)."""
    cik = db.get_cik_for_ticker(ticker) if db.is_available() else None
    if cik:
        return {"ticker": ticker.upper(), "cik": cik, "source": "db"}
    # Fallback to in-process seed map
    cik_raw = _TICKER_CIK_DEFAULT.get(ticker.upper())
    if cik_raw:
        return {"ticker": ticker.upper(), "cik": cik_raw, "source": "seed"}
    raise HTTPException(status_code=404, detail=f"CIK not found for ticker '{ticker}'")


# ── Config / reference-data endpoints ────────────────────────────────────────
# These serve the authoritative copies of data that the JS frontend previously
# duplicated inline. The frontend fetches on startup and falls back to its
# hardcoded values if the backend is unavailable.

_SIC_RANGES = [
    (lambda n: n == 3674 or (3672 <= n <= 3679) or n in (3559, 3577), "Semiconductors"),
    (lambda n: n in (3711, 3714, 3716, 3519),                          "Automotive OEM"),
    (lambda n: 7370 <= n <= 7379,                                       "Software & Cloud"),
    (lambda n: 6020 <= n <= 6199,                                       "Financial Services"),
    (lambda n: (2830 <= n <= 2836) or (8010 <= n <= 8099),             "Healthcare & Pharma"),
    (lambda n: 5200 <= n <= 5999,                                       "Retail & Consumer"),
    (lambda n: (1300 <= n <= 1382) or n == 2911,                       "Energy & Resources"),
    (lambda n: 4911 <= n <= 4939,                                       "Utilities"),
    (lambda n: 2000 <= n <= 3999,                                       "Industrial & Manufacturing"),
]

def _classify_sic(sic: str) -> str:
    try:
        n = int(sic)
    except (ValueError, TypeError):
        return "Generic"
    for pred, industry in _SIC_RANGES:
        if pred(n):
            return industry
    return "Generic"


@app.get("/rss/feeds")
def get_rss_feeds():
    """RSS feed registry — canonical list of compliance/regulatory feeds with weights and domains."""
    return {"feeds": RSS_INGEST_FEEDS}


def _validate_rss_host(url: str) -> None:
    """Raise 400 if the URL's host resolves to a private/loopback address."""
    from urllib.parse import urlparse as _up
    host = _up(url).hostname or ""
    if not host or _PRIVATE_HOST_RE.match(host):
        raise HTTPException(status_code=400, detail=f"URL host is not allowed: {host or '(empty)'}")


@app.get("/rss-proxy")
def rss_proxy(url: str = Query(..., description="RSS feed URL to fetch server-side")):
    """
    Server-side RSS proxy — bypasses browser CORS restrictions.
    Only http/https URLs are allowed; private/loopback/link-local addresses are
    rejected on every redirect hop to prevent SSRF.
    """
    from urllib.parse import urlparse as _up
    from fastapi.responses import Response as _Response

    if _up(url).scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Only http/https URLs are allowed")
    _validate_rss_host(url)

    _headers = {"User-Agent": "Mozilla/5.0 (compatible; DendraiRSSProxy/1.0)"}
    current_url = url
    try:
        for _ in range(6):  # max 5 redirects
            resp = requests.get(current_url, headers=_headers, timeout=10, allow_redirects=False)
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            location = resp.headers.get("Location", "")
            if not location:
                break
            # Re-validate each redirect hop before following it
            if _up(location).scheme not in {"http", "https"}:
                raise HTTPException(status_code=400, detail="Redirect to non-http(s) scheme blocked")
            _validate_rss_host(location)
            current_url = location
        else:
            raise HTTPException(status_code=400, detail="Too many redirects")
        content_type = resp.headers.get("Content-Type", "application/xml")
        return _Response(content=resp.content, media_type=content_type)
    except HTTPException:
        raise
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="RSS feed fetch timed out")
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"RSS fetch failed: {exc}")


@app.get("/scoring/config")
def get_scoring_config():
    """Scoring vocabulary config: domain keywords, severity weights, domain→risk mappings, Item 1A keywords."""
    return {
        "domain_vocab":      DOMAIN_VOCAB,
        "severity_words":    SEVERITY_WORDS,
        "domain_risk_cats":  DOMAIN_RISK_CATS,
        "risk_kw":           RISK_KW,
    }


@app.get("/edgar/8k-items")
def get_8k_items():
    """8-K item code → SEC description mapping (all 24 reportable items)."""
    return {"items": EDGAR_8K_ITEMS}


@app.get("/industry/from-sic")
def industry_from_sic(sic: str = Query(..., description="SIC code as string (e.g. '3674')")):
    """Classify a SIC code into a Dendrai industry label (mirrors risk-engine.js sic2industry)."""
    return {"sic": sic, "industry": _classify_sic(sic)}


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
            ticker=req.ticker,
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
    """Fetch XBRL facts for a peer and attach gross_margin, rd_intensity,
    revenue_growth, and a simplified Beneish M-score for cross-peer benchmarking."""
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
        ar,     ar_prev  = latest_two_annual("AccountsReceivable")
        ni,     _        = latest_two_annual("NetIncome")
        cfo,    _        = latest_two_annual("OperatingCashFlow")
        assets, _        = latest_two_annual("TotalAssets")

        peer["gross_margin"]   = (gp  / rev) if rev and gp  is not None else None
        peer["rd_intensity"]   = (rd  / rev) if rev and rd  is not None else None
        peer["revenue_growth"] = ((rev - rev_prev) / rev_prev) if rev and rev_prev else None

        # Simplified Beneish M-score (same 3-of-8-variable formula as risk-engine.js's
        # computeRatios(), with GMI/AQI/DEPI/SGAI/LVGI held at their neutral defaults)
        # so a peer's score is directly comparable to the subject company's gauge.
        sgi  = (rev / rev_prev) if rev and rev_prev else None
        dsri = ((ar / rev) / (ar_prev / rev_prev)) if (ar and rev and ar_prev and rev_prev) else None
        tata = ((ni - cfo) / assets) if (ni is not None and cfo is not None and assets) else None
        if sgi is not None or dsri is not None or tata is not None:
            d = dsri if dsri is not None else 1.0
            t = tata if tata is not None else 0.0
            s = sgi  if sgi  is not None else 1.0
            peer["m_score"] = -4.84 + 0.920 * d + 0.528 * 1.0 + 0.892 * s + 4.679 * t
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


@app.get("/edgar/peers/{ticker}")
def edgar_peers_saved(ticker: str):
    """
    Return previously-saved SIC peers for a ticker from the DB, without redoing
    the (slow) 10-K named-competitor extraction. Peer identities are cached;
    financial ratios are re-enriched live since they go stale between runs.
    Returns 404 if this ticker has never been through /edgar/peers before.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured — set DATABASE_URL")
    saved = db.get_sic_peers(ticker)
    if not saved:
        raise HTTPException(status_code=404, detail="No saved peer data for this ticker")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        peers = list(pool.map(_enrich_peer_financials, saved["peers"]))
    saved["peers"] = [p for p in peers if _peer_has_data(p)]
    saved["peer_source"] = "saved SIC peers"
    saved["named_competitors"] = []
    return saved


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


@app.get("/edgar/proxy/{ticker}")
def edgar_proxy_saved(ticker: str):
    """
    Return previously-saved DEF 14A proxy sections for a ticker from the DB,
    without a live EDGAR fetch. Section text is stored in full at save time,
    so this fully reconstructs the /edgar/proxy response.
    Returns 404 if this ticker has never been through /edgar/proxy before.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured — set DATABASE_URL")
    saved = db.get_edgar_proxy(ticker)
    if not saved:
        raise HTTPException(status_code=404, detail="No saved proxy filings for this ticker")
    return saved


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

@app.post("/loop/hitl/risk-approvals", dependencies=[Depends(_require_api_key)])
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


@app.post("/loop/hitl/scope-approvals", dependencies=[Depends(_require_api_key)])
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


@app.post("/loop/persist", dependencies=[Depends(_require_api_key)])
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

    # Resolve CEM events → structured risk/control FK edges (graph layer)
    try:
        db.save_cem_event_risk_links(req.run_id, req.cem_events)
    except Exception as _link_err:
        logger.warning("CEM event risk link resolution failed (non-fatal): %s", _link_err)

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


# ── Risk-as-Code: multi-source bridge ─────────────────────────────────────────
#
#  These three endpoints complement /risks-as-code/generate (JSON-only) by adding:
#    POST /rac/from-loop   — same as /risks-as-code/generate but with extra fields
#    POST /rac/from-db     — pull risks directly from the DB, no frontend payload needed
#    POST /rac/from-excel  — multipart file upload (.xlsx / .xls / .csv)
#
#  All three call risk_as_code_mcp_server tools so that Claude can also invoke
#  them directly as MCP tools without going through the HTTP bridge.

import risk_as_code_mcp_server as _rac_mcp


class RacFromLoopRequest(BaseModel):
    risks: List[Dict[str, Any]]
    ticker: str
    period: str = ""
    framework: str = "both"
    industry: str = ""
    ratios: Dict[str, Any] = {}
    objectives: List[Dict[str, Any]] = []
    maps: List[Dict[str, Any]] = []
    signals: List[Dict[str, Any]] = []
    run_id: Optional[int] = None
    save_to_db: bool = False


class RacFromDbRequest(BaseModel):
    ticker: str
    run_id: Optional[int] = None
    framework: str = "both"


@app.post("/rac/from-loop")
def rac_from_loop(req: RacFromLoopRequest):
    """Generate Risk-as-Code YAML from a risk array supplied in the request body.

    Accepts the same risk objects that the Dendrai pipeline stores in output.s2.risks.
    Also accepts optional objectives, MAPs, signals, and financial ratios for richer output.
    """
    import json
    raw = _rac_mcp.rac_from_loop_output(
        risks_json      = json.dumps(req.risks),
        ticker          = req.ticker,
        period          = req.period,
        framework       = req.framework,
        industry        = req.industry,
        ratios_json     = json.dumps(req.ratios)     if req.ratios     else "",
        objectives_json = json.dumps(req.objectives) if req.objectives else "",
        maps_json       = json.dumps(req.maps)       if req.maps       else "",
        signals_json    = json.dumps(req.signals)    if req.signals    else "",
        run_id          = req.run_id,
        save_to_db      = req.save_to_db,
    )
    result = json.loads(raw)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/rac/from-db")
def rac_from_db(req: RacFromDbRequest):
    """Fetch risks from the PostgreSQL risk_scores table and return Risk-as-Code YAML.

    If run_id is omitted the most recent completed run for the ticker is used.
    Returns 503 when DATABASE_URL is not configured.
    """
    import json
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured — set DATABASE_URL")
    raw = _rac_mcp.rac_from_database(
        ticker=req.ticker, run_id=req.run_id, framework=req.framework,
    )
    result = json.loads(raw)
    if "error" in result:
        status = 404 if "not found" in result["error"].lower() else 400
        raise HTTPException(status_code=status, detail=result["error"])
    return result


@app.post("/rac/from-excel", dependencies=[Depends(_require_api_key)])
async def rac_from_excel(
    file:       UploadFile  = File(...),
    ticker:     str         = Form(...),
    period:     str         = Form(""),
    industry:   str         = Form(""),
    framework:  str         = Form("both"),
    sheet_name: str         = Form("0"),
    save_to_db: bool        = Form(False),
):
    """Upload a risk register spreadsheet (.xlsx, .xls, or .csv) and convert to Risk-as-Code YAML.

    Multipart form fields:
      file        — the spreadsheet file (required)
      ticker      — company ticker, e.g. "ON"
      period      — audit period label, e.g. "Q4 2025"
      industry    — industry label, e.g. "Semiconductors"
      framework   — "oscal" | "coso_erm" | "both"  (default: "both")
      sheet_name  — sheet name or zero-based index  (default: "0" = first sheet)
      save_to_db  — persist to DB as a new run      (default: false)

    Expected column headers (case-insensitive, flexible naming):
      ID / Risk ID, Name / Risk Name, Category, Score, Base Score,
      RAG / Status / Rating, Velocity / Trend, CE / Control Effectiveness,
      Peer / Benchmark, Narrative / Description, Impact, Likelihood
    """
    import json
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in (".xlsx", ".xls", ".csv"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}' — upload a .xlsx, .xls, or .csv file",
        )

    # Write to a temp file so the MCP tool can read it via file path
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        raw = _rac_mcp.rac_from_excel(
            file_path=tmp_path, ticker=ticker, period=period,
            industry=industry, framework=framework,
            sheet_name=sheet_name, save_to_db=save_to_db,
        )
        result = json.loads(raw)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Code editor config endpoints ──────────────────────────────────────────────

class CodeEditorSaveRequest(BaseModel):
    content: str


@app.get("/config/code-editor/{storage_key}")
def get_code_editor(storage_key: str):
    """Return the saved YAML content for a code editor key (e.g. 'dendrai.riskcode').

    Returns 503 when DATABASE_URL is not configured; 404 when no content has been saved yet.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured — set DATABASE_URL")
    row = db.get_code_editor_config(storage_key)
    if not row:
        raise HTTPException(status_code=404, detail=f"No saved config for key '{storage_key}'")
    return row


@app.put("/config/code-editor/{storage_key}", dependencies=[Depends(_require_api_key)])
def save_code_editor(storage_key: str, req: CodeEditorSaveRequest):
    """Persist Risk-as-Code or Policy-as-Code YAML editor content to the database.

    Falls back gracefully (saved: false) when DATABASE_URL is not configured so
    the frontend can still save to localStorage.
    """
    if not db.is_available():
        return {"saved": False, "reason": "database not configured"}
    ok = db.save_code_editor_config(storage_key, req.content)
    return {"saved": ok, "storage_key": storage_key}


# ── Pipeline config endpoints ─────────────────────────────────────────────────

@app.get("/config/pipeline")
def get_pipeline_config():
    """Return the saved pipeline configuration (ticker, industry, signals, HITL settings, etc.).

    Returns 503 when DATABASE_URL is not configured; 404 when no config has been saved yet.
    The frontend falls back to localStorage when this returns non-2xx.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured — set DATABASE_URL")
    config = db.get_app_config("pipeline_config")
    if config is None:
        raise HTTPException(status_code=404, detail="No pipeline config saved yet")
    return config


@app.put("/config/pipeline", dependencies=[Depends(_require_api_key)])
def save_pipeline_config(body: Dict[str, Any] = Body(...)):
    """Persist the pipeline configuration to the database.

    Accepts the full config blob from app.jsx (cfg, signals, velocity, hitl,
    rssEnabledFeeds, aiChatCfg, savedAt). Stored as JSONB in app_config table.
    Returns {saved: false} gracefully when the database is unavailable.
    """
    if not db.is_available():
        return {"saved": False, "reason": "database not configured"}
    ok = db.set_app_config("pipeline_config", body)
    return {"saved": ok}


# ── Last loop state endpoints ─────────────────────────────────────────────────

@app.get("/loop/last-state")
def get_last_loop_state():
    """Return the last persisted pipeline run state for restoration on page reload.

    Returns 503 when DATABASE_URL is not configured; 404 when no state has been saved yet.
    The frontend falls back to localStorage when this returns non-2xx.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured — set DATABASE_URL")
    state = db.get_app_config("last_loop_state")
    if state is None:
        raise HTTPException(status_code=404, detail="No saved loop state")
    return state


@app.get("/audit-scope/{ticker}")
def get_saved_audit_scope(ticker: str):
    """
    Most recently completed run's saved audit objectives for a ticker.

    Used by the Audit Scope screen to show real prior-run data before
    Assess Enterprise Risk has been run in the current browser session.
    Returns 404 when no completed run with objectives has been saved yet.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured — set DATABASE_URL")
    saved = db.get_latest_audit_objectives(ticker)
    if not saved:
        raise HTTPException(status_code=404, detail=f"No saved audit objectives for {ticker.upper()}")
    return saved


@app.put("/loop/last-state", dependencies=[Depends(_require_api_key)])
def save_last_loop_state(body: Dict[str, Any] = Body(...)):
    """Persist the full pipeline run state to the database for restoration on reload.

    Accepts the complete loop blob from app.jsx (output, stageState, gateState,
    loopLog, livefacts, perRiskAppetite, riskApprovals, scopeApprovals, manualAudits,
    narrativeResult, openStages, profile). Stored as JSONB in app_config table.
    Returns {saved: false} gracefully when the database is unavailable.
    """
    if not db.is_available():
        return {"saved": False, "reason": "database not configured"}
    ok = db.set_app_config("last_loop_state", body)
    return {"saved": ok}


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dendrai MCP API Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    print(f"Dendrai MCP API  →  http://{args.host}:{args.port}")
    print(f"  Docs           →  http://{args.host}:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port)
