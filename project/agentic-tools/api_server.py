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
    POST /company/private             Create a private company (no SEC ticker/CIK)
    POST /financials/upload           Parse an uploaded financial statement (.xlsx/.xls/.csv/.pdf) for review
    POST /financials/commit           Persist reviewed manual financial line items
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

    GET  /token-usage/summary                Token usage by user/feature (window) + calendar rollups (month/year, MTD/YTD)
    GET  /model-health/summary               Backtest accuracy trend + PSI drift (financial ratios, FRED macro regime)

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
import contextvars
import logging
import os
import threading
import time
import sys
import tempfile
from contextlib import asynccontextmanager, AsyncExitStack
from datetime import datetime, timedelta, timezone
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
    has_material_item,
    classify_8k_event,
    XBRL_METRICS,
)
from rss_tool import run_rss_analysis
from rss_ingest_service import (
    ingest_feeds, get_feed_status, FEEDS as RSS_INGEST_FEEDS,
    DOMAIN_VOCAB, SEVERITY_WORDS, DOMAIN_RISK_CATS, RISK_KW,
)
from edgar_tool import _8K_ITEMS as EDGAR_8K_ITEMS
import db
import embedding_util
import control_plane
import manual_financials_tool

import ai_endpoints
import chat_endpoint
import claude_client
import peer_intel
import risks_as_code
import ontology_export
import ontology_endpoints
import concept_linking
import oracle_fusion_endpoints
import sox_endpoints
import risk_register_endpoints
import pac_endpoints
import pol_domain_mappings
import pac_policy_docs
import approvals_endpoints
import remediation_endpoints
import evidence_pack_endpoints
import evidence_endpoints
import itsm_endpoints
import map_endpoints
import sample_selection_endpoints
import evidence_quality_endpoints
import vendor_risk_endpoints
import ai_governance_endpoints
import infrastructure_monitoring_endpoints
import fair_endpoints
import deploy_env
import exceptions_endpoints
import process_mining_endpoints
import pac_negative_sweep
import connector_hygiene_sweep
import vendor_risk_sweep
import ai_governance_sweep
import identity_graph_sync
import je_testing_sweep
import je_testing_endpoints
import regulatory_change_endpoints
import pii_retention_sweep
import risk_waiver_sweep
import pac_auto_sync_sweep
import itsm_sla_sweep
import map_detection_sweep
import infra_asset_sweep
import vulnerability_sweep
import infra_posture_endpoints
import exception_staleness_sweep
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
from infrastructure_monitoring_mcp_server import mcp as _infrastructure_monitoring_mcp
from fair_mcp_server import mcp as _fair_mcp
from process_mining_mcp_server import mcp as _process_mining_mcp
from risk_rating_mcp_server import mcp as _risk_rating_mcp

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

try:
    import connector_poller
    _HAS_CONNECTOR_POLLER = True
except Exception:
    connector_poller = None  # type: ignore[assignment]
    _HAS_CONNECTOR_POLLER = False

import github_endpoints
import auth_db
import auth_endpoints

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────

async def _run_cycle_for_all_tenants(cycle_fn, label: str) -> None:
    """Run one cycle_fn() invocation per active tenant, with that tenant's
    db pool + all secrets bound for exactly the duration of its own call —
    the multi-tenant equivalent of each sweep module's single-tenant
    "run one cycle against the global DB" call. cycle_fn is one of the
    existing sweep_once()/_process_batch()/_poll_due_connectors()-style
    functions, completely unmodified: this wrapper is the only new surface
    area, the inner logic (already covered by each module's own tests)
    doesn't change at all.

    One tenant's cycle raising doesn't stop the others' — logged and
    skipped, same as every sweep's own tick-level except handler already
    does for a single-tenant failure."""
    for tenant in control_plane.list_active_tenants():
        try:
            secrets = control_plane.get_tenant_secrets(tenant.id)
        except control_plane.TenantNotFound:
            logger.warning("%s: tenant %s has no secrets provisioned — skipping", label, tenant.slug)
            continue
        db.bind_tenant_pool(tenant.id, tenant.db_dsn)
        db.bind_tenant_connector_key(secrets.connector_encryption_key)
        auth_endpoints.bind_tenant_secret(tenant.id, secrets.auth_jwt_secret)
        try:
            await cycle_fn()
        except Exception:
            logger.exception("%s failed for tenant %s", label, tenant.slug)
        finally:
            db.unbind_tenant()
            db.unbind_tenant_connector_key()
            auth_endpoints.unbind_tenant_secret()


async def _run_startup_reembed() -> None:
    """One-shot, backgrounded re-embed of any stale/never-embedded concept.
    Run via asyncio.to_thread since ontology_endpoints.reembed_stale_concepts
    is synchronous (blocking network calls to the embedding provider) — never
    awaited inline in the startup sequence, so a slow or failing provider
    (rate-limited, out of quota, unreachable) delays only this task, never
    uvicorn's readiness or Railway's healthcheck."""
    try:
        result = await asyncio.to_thread(ontology_endpoints.reembed_stale_concepts)
        logger.info("Startup concept re-embed: %s", result)
    except Exception as exc:
        logger.warning("Concept re-embed skipped at startup (non-fatal): %s", exc)


async def _multi_tenant_loop(cycle_fn, tick_s: float, label: str) -> None:
    """TENANT_MODE=multi's replacement for a sweep module's own start_sweep()/
    start_polling(): identical infinite-loop-with-sleep shape, but each tick
    iterates every active tenant (_run_cycle_for_all_tenants) instead of
    running once against a single global DB."""
    logger.info("%s (multi-tenant) started (tick=%.0fs)", label, tick_s)
    while True:
        try:
            await asyncio.sleep(tick_s)
            await _run_cycle_for_all_tenants(cycle_fn, label)
        except asyncio.CancelledError:
            logger.info("%s (multi-tenant) stopped", label)
            break
        except Exception as exc:
            logger.warning("%s (multi-tenant) tick error: %s", label, exc)


@asynccontextmanager
async def lifespan(application: FastAPI):
    db_ready = False
    if control_plane.is_multi_tenant():
        # TENANT_MODE=multi: there is no single DATABASE_URL to initialize
        # at process startup — each request binds its own tenant database
        # via _TenantResolutionMiddleware. Only the control plane (the
        # tenant registry itself) is process-global and needs eager init.
        # Per-tenant schema init happens once, at provisioning time
        # (db.init_tenant_db(), see provision_tenant.py), not here.
        control_plane.init_control_db()
        logger.info("Multi-tenant mode: control plane initialized; per-request tenant binding active")
    else:
        db_ready = db.init_db()
        logger.info("Database persistence: %s", "ENABLED" if db_ready else "DISABLED (set DATABASE_URL to enable)")
        if db_ready:
            # Seed static reference data into DB on first startup (idempotent)
            risk_register_endpoints.seed_static_data()
            _seed_cem_templates()
            _seed_ticker_cik()
            _seed_controls_catalog()
            _seed_synthetic_connectors()
            db.seed_builtin_pac_processes()
            db.seed_framework_mappings()
            db.seed_ontology()
            # Backgrounded like every other sweep in this codebase (see
            # risk_waiver_sweep.start_sweep's asyncio.create_task pattern
            # below) — NOT awaited inline. A synchronous call here blocked
            # the entire startup sequence (and Railway's 30s healthcheck
            # window) behind OpenAI's SDK retry/backoff on every one of the
            # ~70 seeded concepts when OPENAI_API_KEY was present but out of
            # quota — a real incident, not a hypothetical.
            asyncio.create_task(_run_startup_reembed())
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

        _gov_task = _connector_task = _drift_task = None
        _pac_negative_sweep_task = None
        _connector_hygiene_sweep_task = _vendor_risk_sweep_task = _ai_governance_sweep_task = None
        _identity_graph_sync_task = None
        _je_testing_sweep_task = None
        _pii_retention_sweep_task = None
        _risk_waiver_sweep_task = None
        _pac_auto_sync_sweep_task = None
        _itsm_sla_sweep_task = None
        _map_detection_sweep_task = None
        _infra_asset_sweep_task = None
        _vulnerability_sweep_task = None
        _exception_staleness_sweep_task = None
        _reconnect_task = None
        _multi_tenant_bg_tasks: list[asyncio.Task] = []

        if control_plane.is_multi_tenant():
            # One generic per-tenant scheduler per sweep (_multi_tenant_loop,
            # defined above lifespan()) instead of one instance of each sweep
            # against a single global DB that doesn't exist in this mode.
            # Each module's sweep_once()/_process_batch()/etc. is unchanged —
            # only which database/secrets are bound before calling it differs.
            if _HAS_MCP_GOVERNANCE:
                _multi_tenant_bg_tasks.append(asyncio.create_task(
                    _multi_tenant_loop(mcp_governance._process_batch, mcp_governance.POLL_INTERVAL_S, "MCP governance poll")
                ))
            if _HAS_CONNECTOR_POLLER:
                _multi_tenant_bg_tasks.append(asyncio.create_task(
                    _multi_tenant_loop(connector_poller._poll_due_connectors, connector_poller._TICK_S, "Connector poller")
                ))
            _multi_tenant_bg_tasks.extend([
                asyncio.create_task(_multi_tenant_loop(
                    lambda: asyncio.to_thread(_check_model_health_drift_once),
                    _MODEL_HEALTH_CHECK_INTERVAL_S, "Model Health drift watch",
                )),
                asyncio.create_task(_multi_tenant_loop(pac_negative_sweep.sweep_once, pac_negative_sweep._TICK_S, "PaC negative-testing sweep")),
                asyncio.create_task(_multi_tenant_loop(connector_hygiene_sweep.sweep_once, connector_hygiene_sweep._TICK_S, "Connector credential hygiene sweep")),
                asyncio.create_task(_multi_tenant_loop(vendor_risk_sweep.sweep_once, vendor_risk_sweep._TICK_S, "Vendor risk SOC 2 expiry sweep")),
                asyncio.create_task(_multi_tenant_loop(ai_governance_sweep.sweep_once, ai_governance_sweep._TICK_S, "AI Governance assessment expiry sweep")),
                asyncio.create_task(_multi_tenant_loop(identity_graph_sync.sweep_once, identity_graph_sync._TICK_S, "Identity graph sync")),
                asyncio.create_task(_multi_tenant_loop(je_testing_sweep.sweep_once, je_testing_sweep._TICK_S, "JE Testing sweep")),
                asyncio.create_task(_multi_tenant_loop(pii_retention_sweep.sweep_once, pii_retention_sweep._TICK_S, "PII retention sweep")),
                asyncio.create_task(_multi_tenant_loop(risk_waiver_sweep.sweep_once, risk_waiver_sweep._TICK_S, "Risk waiver expiry sweep")),
                asyncio.create_task(_multi_tenant_loop(pac_auto_sync_sweep.sweep_once, pac_auto_sync_sweep._TICK_S, "PaC auto-sync sweep")),
                asyncio.create_task(_multi_tenant_loop(itsm_sla_sweep.sweep_once, itsm_sla_sweep._TICK_S, "ITSM SLA breach sweep")),
                asyncio.create_task(_multi_tenant_loop(map_detection_sweep.sweep_once, map_detection_sweep._TICK_S, "MAP detection sweep")),
            ])
            # Infrastructure Vulnerability & Currency Posture: development
            # environment only. Unlike Exception Management's dev-only gate
            # (a router-level 404 — see exceptions_endpoints.py), a
            # background sweep has no HTTP request to 404; the only
            # enforcement point is not creating the task at all.
            if deploy_env.IS_DEVELOPMENT:
                _multi_tenant_bg_tasks.append(asyncio.create_task(
                    _multi_tenant_loop(infra_asset_sweep.sweep_once, infra_asset_sweep._TICK_S, "Infra asset/expiry sweep")
                ))
                _multi_tenant_bg_tasks.append(asyncio.create_task(
                    _multi_tenant_loop(vulnerability_sweep.sweep_once, vulnerability_sweep._TICK_S, "OSV vulnerability sweep")
                ))
                _multi_tenant_bg_tasks.append(asyncio.create_task(
                    _multi_tenant_loop(exception_staleness_sweep.sweep_once, exception_staleness_sweep._TICK_S, "Exception staleness sweep")
                ))
            logger.info("Multi-tenant background sweep schedulers started (%d loops, per-tenant iteration)",
                        len(_multi_tenant_bg_tasks))
        else:
            # Start MCP Governance polling (non-blocking background task)
            if _HAS_MCP_GOVERNANCE and db.is_available():
                _gov_task = asyncio.create_task(mcp_governance.start_polling())
                logger.info("MCP governance polling task started")
            elif _HAS_MCP_GOVERNANCE:
                logger.info("MCP governance available but DB not ready — polling not started")

            # Poll-based connectors (Oracle Fusion / SAP HANA / SailPoint / Dynamics 365 /
            # NetSuite) — dispatch loop only; which connectors actually poll is entirely
            # UI-configured (observability.poll_connectors), not gated by env vars here.
            if _HAS_CONNECTOR_POLLER and db.is_available():
                _connector_task = asyncio.create_task(connector_poller.start_polling())
                logger.info("Connector poller task started")
            elif _HAS_CONNECTOR_POLLER:
                logger.info("Connector poller available but DB not ready — polling not started")

            # Model Health drift watch — periodic (not per-request) so alerting can't
            # spam the webhook on every page view of the on-demand /model-health/summary
            # endpoint. See model_health_drift_watch() below.
            if db.is_available():
                _drift_task = asyncio.create_task(model_health_drift_watch())
                logger.info("Model Health drift watch task started")

            # Policy-as-Code negative-testing periodic full evaluation (P1).
            if db.is_available():
                _pac_negative_sweep_task = asyncio.create_task(pac_negative_sweep.start_sweep())
                logger.info("PaC negative-testing sweep task started")

            # Infrastructure Monitoring: connector credential rotation hygiene sweep.
            if db.is_available():
                _connector_hygiene_sweep_task = asyncio.create_task(connector_hygiene_sweep.start_sweep())
                logger.info("Connector credential hygiene sweep task started")

            # Continuous Third-Party/Vendor Risk: vendor SOC 2 expiry sweep.
            if db.is_available():
                _vendor_risk_sweep_task = asyncio.create_task(vendor_risk_sweep.start_sweep())
                logger.info("Vendor risk SOC 2 expiry sweep task started")

            # AI Governance: AI system assessment expiry sweep.
            if db.is_available():
                _ai_governance_sweep_task = asyncio.create_task(ai_governance_sweep.start_sweep())
                logger.info("AI Governance assessment expiry sweep task started")

            # Identity/role graph sync: real role_count/entitlements for The
            # Graph Architect (UBO/agents/graph_architect.py) — see
            # identity_graph_sync.py's module docstring.
            if db.is_available():
                _identity_graph_sync_task = asyncio.create_task(identity_graph_sync.start_sweep())
                logger.info("Identity graph sync task started")

            # Journal Entry Testing: pulls real GL journal entries from active
            # financial connectors and scores them with je_testing_tool.py's
            # deterministic anomaly rules — see je_testing_sweep.py.
            if db.is_available():
                _je_testing_sweep_task = asyncio.create_task(je_testing_sweep.start_sweep())
                logger.info("JE Testing sweep task started")

            # SOC 2 Privacy (P4/P5): purges exception_control_events rows
            # (and their cascaded scoring/triage history) past a configurable
            # retention window — see pii_retention_sweep.py.
            if db.is_available():
                _pii_retention_sweep_task = asyncio.create_task(pii_retention_sweep.start_sweep())
                logger.info("PII retention sweep task started")

            # DevOps Monitoring: expires time-boxed risk waivers past their
            # expires_at and re-raises the underlying finding — see risk_waiver_sweep.py.
            if db.is_available():
                _risk_waiver_sweep_task = asyncio.create_task(risk_waiver_sweep.start_sweep())
                logger.info("Risk waiver expiry sweep task started")

            # Policy-as-Code: polls every auto_sync_enabled repo and syncs it
            # when the branch's HEAD commit has moved — see pac_auto_sync_sweep.py.
            if db.is_available():
                _pac_auto_sync_sweep_task = asyncio.create_task(pac_auto_sync_sweep.start_sweep())
                logger.info("PaC auto-sync sweep task started")

            # DevOps Monitoring: flags ITSM tickets that blew their remediation
            # SLA and re-raises the underlying finding — see itsm_sla_sweep.py.
            if db.is_available():
                _itsm_sla_sweep_task = asyncio.create_task(itsm_sla_sweep.start_sweep())
                logger.info("ITSM SLA breach sweep task started")

            # Continuous Monitoring: proposes a Management Action Plan when a
            # control keeps requiring human review — see map_detection_sweep.py.
            if db.is_available():
                _map_detection_sweep_task = asyncio.create_task(map_detection_sweep.start_sweep())
                logger.info("MAP detection sweep task started")

            # Infrastructure Vulnerability & Currency Posture: development
            # environment only — see the multi-tenant branch's comment above
            # for why this is gated at task-creation time, not a 404.
            if db.is_available() and deploy_env.IS_DEVELOPMENT:
                _infra_asset_sweep_task = asyncio.create_task(infra_asset_sweep.start_sweep())
                logger.info("Infra asset/expiry sweep task started")
                _vulnerability_sweep_task = asyncio.create_task(vulnerability_sweep.start_sweep())
                logger.info("OSV vulnerability sweep task started")
                _exception_staleness_sweep_task = asyncio.create_task(exception_staleness_sweep.start_sweep())
                logger.info("Exception staleness sweep task started")

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
                        # Also missing from this reconnect list before now (noted while
                        # adding seed_ontology): a DB that comes back up after a startup
                        # failure previously never got framework_mappings.py's curated
                        # crosswalk applied until the next full process restart.
                        await asyncio.to_thread(db.seed_framework_mappings)
                        await asyncio.to_thread(db.seed_ontology)
                        if _HAS_MCP_GOVERNANCE and _gov_task is None:
                            asyncio.create_task(mcp_governance.start_polling())
                            logger.info("MCP governance polling started after DB reconnect")
                        if _HAS_CONNECTOR_POLLER and _connector_task is None:
                            asyncio.create_task(connector_poller.start_polling())
                            logger.info("Connector poller started after DB reconnect")
                        if _drift_task is None:
                            asyncio.create_task(model_health_drift_watch())
                            logger.info("Model Health drift watch started after DB reconnect")
                        if _pac_negative_sweep_task is None:
                            asyncio.create_task(pac_negative_sweep.start_sweep())
                            logger.info("PaC negative-testing sweep started after DB reconnect")
                        if _connector_hygiene_sweep_task is None:
                            asyncio.create_task(connector_hygiene_sweep.start_sweep())
                            logger.info("Connector credential hygiene sweep started after DB reconnect")
                        if _vendor_risk_sweep_task is None:
                            asyncio.create_task(vendor_risk_sweep.start_sweep())
                            logger.info("Vendor risk SOC 2 expiry sweep started after DB reconnect")
                        if _ai_governance_sweep_task is None:
                            asyncio.create_task(ai_governance_sweep.start_sweep())
                            logger.info("AI Governance assessment expiry sweep started after DB reconnect")
                        if _identity_graph_sync_task is None:
                            asyncio.create_task(identity_graph_sync.start_sweep())
                            logger.info("Identity graph sync started after DB reconnect")
                        if _je_testing_sweep_task is None:
                            asyncio.create_task(je_testing_sweep.start_sweep())
                            logger.info("JE Testing sweep started after DB reconnect")
                        if _pii_retention_sweep_task is None:
                            asyncio.create_task(pii_retention_sweep.start_sweep())
                            logger.info("PII retention sweep started after DB reconnect")
                        if _risk_waiver_sweep_task is None:
                            asyncio.create_task(risk_waiver_sweep.start_sweep())
                            logger.info("Risk waiver expiry sweep started after DB reconnect")
                        if _pac_auto_sync_sweep_task is None:
                            asyncio.create_task(pac_auto_sync_sweep.start_sweep())
                            logger.info("PaC auto-sync sweep started after DB reconnect")
                        if _itsm_sla_sweep_task is None:
                            asyncio.create_task(itsm_sla_sweep.start_sweep())
                            logger.info("ITSM SLA breach sweep started after DB reconnect")
                        if _map_detection_sweep_task is None:
                            asyncio.create_task(map_detection_sweep.start_sweep())
                            logger.info("MAP detection sweep started after DB reconnect")
                        if _infra_asset_sweep_task is None and deploy_env.IS_DEVELOPMENT:
                            asyncio.create_task(infra_asset_sweep.start_sweep())
                            logger.info("Infra asset/expiry sweep started after DB reconnect")
                        if _vulnerability_sweep_task is None and deploy_env.IS_DEVELOPMENT:
                            asyncio.create_task(vulnerability_sweep.start_sweep())
                            logger.info("OSV vulnerability sweep started after DB reconnect")
                        if _exception_staleness_sweep_task is None and deploy_env.IS_DEVELOPMENT:
                            asyncio.create_task(exception_staleness_sweep.start_sweep())
                            logger.info("Exception staleness sweep started after DB reconnect")

            _reconnect_task = asyncio.create_task(_db_reconnect_loop())

        try:
            yield
        finally:
            if _reconnect_task is not None:
                _reconnect_task.cancel()
            for _bg_task in (_gov_task, _connector_task, _drift_task,
                             _pac_negative_sweep_task, _connector_hygiene_sweep_task,
                             _vendor_risk_sweep_task, _ai_governance_sweep_task, _identity_graph_sync_task,
                             _je_testing_sweep_task, _pii_retention_sweep_task,
                             _risk_waiver_sweep_task, _pac_auto_sync_sweep_task,
                             _itsm_sla_sweep_task, _map_detection_sweep_task,
                             _infra_asset_sweep_task, _vulnerability_sweep_task, _exception_staleness_sweep_task,
                             *_multi_tenant_bg_tasks):
                if _bg_task is not None:
                    _bg_task.cancel()
                    try:
                        await _bg_task
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


def _seed_controls_catalog() -> None:
    """Idempotently register both control vocabularies into controls_catalog:
    the IDs embedded in PAC's Rego deny rules (source='pac_rego') and the
    business-level controls used by RaC's manual Review UI (source='manual').

    Also reconciles away pac_rego rows for a process that no longer exists in
    _REGO_DEFAULTS (e.g. a retired built-in process like DevOps Monitoring) —
    upsert alone never deletes, so without this a removed process's old
    control IDs stay in the catalog forever as phantom entries.
    """
    if not db.is_available():
        return
    count = 0
    pac_defaults = pac_endpoints.extract_control_ids_from_defaults()
    for ctrl in pac_defaults:
        db.upsert_catalog_control(
            ctrl["control_id"], ctrl["name"],
            process=ctrl.get("process"), source="pac_rego",
        )
        count += 1
    for ctrl in _rac_mcp._CTRL_MAP_LOCAL.values():
        db.upsert_catalog_control(ctrl["ref"], ctrl["name"], source="manual")
        count += 1
    logger.info("Seeded %d controls into controls_catalog", count)

    valid_processes = list(pac_endpoints._REGO_DEFAULTS.keys())
    removed = db.delete_stale_catalog_controls(valid_processes)
    if removed:
        logger.info("Removed %d stale controls_catalog rows for retired processes", removed)


# Default poll connectors for synthetic_transaction_tool.py's simulator — one
# per (system, process) pair, all sharing connector_type "synthetic_transaction"
# and differentiated only by extra_config.process. 300s so Continuous Watch
# visibly gains new activity every few minutes rather than every 30 (the
# default poll_interval_s real connectors use).
_SYNTHETIC_CONNECTORS_DEFAULT = [
    ("Oracle Fusion — Hire to Retire",            "hire_to_retire",       "Oracle Fusion"),
    ("SailPoint — Identity & Access Management",  "iam",                  "SailPoint"),
    ("SAP HANA — Order to Cash",                  "order_to_cash",        "SAP HANA"),
    ("SAP HANA — Procure to Pay",                 "procure_to_pay",       "SAP HANA"),
    ("SAP HANA — Record to Report",               "record_to_report",     "SAP HANA"),
    ("SAP HANA — Fixed Assets",                   "fixed_assets",         "SAP HANA"),
    ("SAP HANA — Vendor Management",              "vendor_management",    "SAP HANA"),
    ("SAP HANA — Payroll",                        "payroll",              "SAP HANA"),
    ("Dynamics 365 — Receive to Ship",            "receive_to_ship",      "Dynamics 365"),
    ("Dynamics 365 — Inventory Master",           "inventory_master",     "Dynamics 365"),
    ("ServiceNow — Customer Master File",         "customer_master_file", "ServiceNow"),
]


def _seed_synthetic_connectors() -> None:
    if not db.is_available():
        return
    rows = [
        {
            "connector_type": "synthetic_transaction", "display_name": display_name,
            "auth_type": "none", "poll_interval_s": 300,
            "extra_config": {"process": process, "system_label": system_label},
        }
        for display_name, process, system_label in _SYNTHETIC_CONNECTORS_DEFAULT
    ]
    try:
        seeded = db.seed_synthetic_connectors(rows)
    except db.EncryptionKeyMissing as exc:
        # Reference-data seeding must never crash startup — see every other
        # _seed_* helper's db.is_available() guard for the same principle.
        # Unlike those, this one can additionally fail on a missing
        # CONNECTOR_ENCRYPTION_KEY (create_poll_connector encrypts even an
        # empty synthetic credentials dict), so it needs its own guard.
        logger.warning("Synthetic connector seeding skipped: %s", exc)
        return
    if seeded:
        logger.info("Seeded %d synthetic transaction simulator connector(s)", seeded)


# ── Tenant resolution (multi-tenancy) ────────────────────────────────────────
# A no-op unless TENANT_MODE=multi (see control_plane.py). A plain ASGI
# middleware — not BaseHTTPMiddleware — deliberately: it runs call_next
# (self.app) in the *same* coroutine/task, so the contextvars it sets
# (db.bind_tenant_pool, auth_endpoints.bind_tenant_secret, etc.) are
# guaranteed to propagate into every downstream handler. BaseHTTPMiddleware
# has historically run request handling in a way that doesn't always give
# that guarantee, which is not a risk worth taking on the layer this whole
# tenant-isolation design rests on.
_tenant_api_key: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "tenant_api_key", default=None
)


class _TenantResolutionMiddleware:
    """First middleware in the stack — resolves the Host header to a
    tenant, binds db.py's tenant-scoped connection pool and every
    per-tenant secret (JWT signing key, evidence signing key, connector
    encryption key, write-guard API key) for the duration of the request,
    and rejects an unknown/suspended tenant before any downstream code
    (CORS, telemetry, auth, a route handler) touches a database or secret."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not control_plane.is_multi_tenant():
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("latin-1")
        slug = host.split(":")[0].split(".")[0].strip().lower()

        try:
            tenant = control_plane.resolve_tenant(slug)
            secrets = control_plane.get_tenant_secrets(tenant.id)
        except control_plane.TenantNotFound:
            response = JSONResponse({"detail": "Unknown tenant"}, status_code=404)
            await response(scope, receive, send)
            return
        except control_plane.ControlPlaneUnavailable as exc:
            logger.error("Control plane unavailable: %s", exc)
            response = JSONResponse({"detail": "Service temporarily unavailable"}, status_code=503)
            await response(scope, receive, send)
            return

        db.bind_tenant_pool(tenant.id, tenant.db_dsn)
        db.bind_tenant_connector_key(secrets.connector_encryption_key)
        auth_endpoints.bind_tenant_secret(tenant.id, secrets.auth_jwt_secret)
        _tenant_api_key.set(secrets.api_key)
        scope.setdefault("state", {})["tenant"] = tenant
        try:
            await self.app(scope, receive, send)
        finally:
            db.unbind_tenant()
            db.unbind_tenant_connector_key()
            auth_endpoints.unbind_tenant_secret()
            _tenant_api_key.set(None)


app = FastAPI(
    title="Dendrai MCP API",
    version="2.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

# Registered before CORS so it's the outermost layer (Starlette executes
# add_middleware calls in the order added, first-added = outermost = runs
# first) — every other middleware and every route handler runs inside a
# resolved tenant context, or the request never reaches them.
app.add_middleware(_TenantResolutionMiddleware)

# Under TENANT_MODE=multi, an arbitrary third-party origin must not be able
# to read responses from a tenant's own subdomain (browsers otherwise let a
# malicious page fetch() cross-origin and read back anything not gated by
# the SameSite=Strict session cookie — e.g. an X-API-Key-guarded write
# endpoint called from attacker-controlled JS holding a leaked key). Locked
# to TENANT_ROOT_DOMAIN's subdomains once multi-tenancy is on; unchanged
# (wildcard) for today's single-tenant deployments so nothing here can
# break an existing deployment that hasn't opted in.
if control_plane.is_multi_tenant():
    _tenant_root_domain = os.environ.get("TENANT_ROOT_DOMAIN", "dendrai.ai")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=rf"^https://([a-z0-9-]+\.)?{re.escape(_tenant_root_domain)}$",
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ── Write-endpoint authentication ──────────────────────────────────────────────
# Set DENDRAI_API_KEY (Railway env var) and VITE_API_KEY (same value, build-time
# Vite var) to protect mutating endpoints from unauthenticated external writes.
# Under TENANT_MODE=multi, _tenant_api_key (bound by _TenantResolutionMiddleware
# above) takes priority over the shared env var — a global key would otherwise
# let any tenant write to any other tenant now reachable from the same process.
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
_REQUIRED_API_KEY = os.environ.get("DENDRAI_API_KEY", "")

async def _require_api_key(key: str | None = Security(_API_KEY_HEADER)) -> None:
    required = _tenant_api_key.get() or _REQUIRED_API_KEY
    if not required:
        raise HTTPException(
            status_code=503,
            detail="DENDRAI_API_KEY not configured — set this env var to enable write endpoints",
        )
    if key != required:
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
    "/rss-proxy",                    # server-side CORS bypass for feed XML — no user data, SSRF-guarded
    "/scoring/config",               # read-only scoring vocabulary, fetched at JS module init time
    "/observability/telemetry/ingest", # external systems auth via per-system Bearer key
    "/evidence/webhook",             # SARIF evidence ingestion — own per-system Bearer key auth
    "/itsm/webhook",                 # ITSM ticket-status push — own per-system Bearer key auth
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
        jti = payload.get("jti", "")
        user_id, reason = auth_db.validate_session_reason(jti)
        if not user_id:
            # This runs ahead of every route's get_current_user dependency, so
            # it — not auth_endpoints._resolve_session — is what actually sees
            # idle sessions first; revoke here so the reason survives for the
            # frontend even though the route handler never runs.
            if reason == "idle":
                auth_db.revoke_session(jti)
                return JSONResponse({"detail": "Session expired due to inactivity"}, status_code=401)
            return JSONResponse({"detail": "Session expired"}, status_code=401)
        return await call_next(request)

app.add_middleware(_DendraiAuthMiddleware)
logger.info("Dendrai auth middleware registered")

class _HSTSMiddleware(_BaseHTTPMiddleware):
    """Asserts the app's own transport-security policy on every response
    instead of relying solely on the hosting platform's edge TLS termination
    to keep clients on HTTPS — a browser that's seen this header once will
    refuse to downgrade to plain HTTP for the max-age window, even if a
    future request is somehow offered one."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

app.add_middleware(_HSTSMiddleware)
logger.info("HSTS middleware registered")

# AI-augmented endpoints (recommendations #1–#4). Active only when ANTHROPIC_API_KEY
# is set; otherwise each route returns 503 and the deterministic pipeline is unaffected.
app.include_router(ai_endpoints.router)

# AI Chat: streaming conversational interface with MCP tool access.
app.include_router(chat_endpoint.router)

# Risks-as-Code: OSCAL + COSO ERM translators + SSE live stream.
app.include_router(risks_as_code.router)
app.include_router(ontology_export.router)
app.include_router(ontology_endpoints.router)
app.include_router(concept_linking.router)

# Oracle Fusion: control library, test results, issues, SOD, audit events.
app.include_router(oracle_fusion_endpoints.router)

# SOX Scope: materiality, accounts, processes, systems, segment coverage.
app.include_router(sox_endpoints.router)

# Risk Register Review: internal register management, framework ingestion, control mapping.
app.include_router(risk_register_endpoints.router)

# Policy-as-Code & Controls-as-Code: Rego module management, approvals, external hooks.
# No extra prefix here — pac_endpoints.router already declares prefix="/pac", and
# nginx's /api/ catch-all strips "/api/" before forwarding to uvicorn (mirroring
# every other router below). The extra "/api" here doubled it to /api/pac/... on
# the FastAPI side, which nginx-stripped requests to /api/pac/... could never
# reach — every /api/pac/* route (hooks, processes, modules, evaluate, the
# legacy GitHub sync) has been 404ing in production as a result.
app.include_router(pac_endpoints.router)

# Plain-language policy intake + HITL review of its conversion to Rego. Shares
# the /pac prefix and the same screen gate; kept in its own module because it
# is a distinct workflow (document -> draft -> human decision -> module) rather
# than more CRUD over modules.
app.include_router(pac_policy_docs.router)

# MCP Governance: telemetry observability + adjudicated governance events.
if _HAS_MCP_GOVERNANCE:
    app.include_router(mcp_governance.router)
    logger.info("MCP governance router registered at /observability")

# Authentication: local login, logout, me, change-password, SSO OAuth flows.
app.include_router(auth_endpoints.router)
logger.info("Auth router registered at /auth")

# Approval workflow: real 2-stage preparer -> manager review for HITL gates.
app.include_router(approvals_endpoints.router)
app.include_router(remediation_endpoints.router)

# Audit Evidence Pack: one-shot assembly of everything defensible about a run.
app.include_router(evidence_pack_endpoints.router)

# DevOps Monitoring: SARIF/SAST evidence ingestion (tamper-evident, hash-chained).
app.include_router(evidence_endpoints.router)
logger.info("Evidence ingestion router registered at /evidence/webhook")

# DevOps Monitoring: ITSM/Jira-ServiceNow ticket status sync.
app.include_router(itsm_endpoints.router)
logger.info("ITSM ticket sync router registered at /itsm/webhook")

# Continuous Monitoring: Management Action Plans for recurring exceptions.
app.include_router(map_endpoints.router)

# Audit sample selection: random / risk-based / monetary unit sampling.
app.include_router(sample_selection_endpoints.router)

# PBC/workpaper evidence quality: stale/unsigned/period-mismatch flags + content check.
app.include_router(evidence_quality_endpoints.router)

# GitHub Webhook: receive repo events and run them through the UBO pipeline.
app.include_router(github_endpoints.router)
logger.info("GitHub webhook router registered at /github/webhook")

# Infrastructure Monitoring: Postgres CIS hardening, Railway platform/deployment
# drift, and connector-credential rotation hygiene.
app.include_router(infrastructure_monitoring_endpoints.router)

# Risk Quantification: FAIR Monte Carlo loss modeling over adjudicated events,
# SOX processes, risk register entries, and CEM events.
app.include_router(fair_endpoints.router)
app.include_router(exceptions_endpoints.router)

# Infrastructure Vulnerability & Currency Posture: asset inventory + OSV.dev
# vulnerability register — Development environment only (404 gate inside
# the router itself, same pattern as exceptions_endpoints.router above).
app.include_router(infra_posture_endpoints.router)

# Process Mining: variant analysis, conformance checking, cycle-time/bottleneck
# stats, and rework detection over case-tracked adjudications.
app.include_router(process_mining_endpoints.router)

# Journal Entry Testing: deterministic anomaly rules (round-dollar, weekend/
# after-hours postings, preparer==approver SoD, rare accounts, velocity
# spikes) over real GL journal entries pulled from active financial connectors.
app.include_router(je_testing_endpoints.router)

# Regulatory Change Management: horizon-scan regulatory feeds, diff against
# the last snapshot, and route material changes to a HITL review queue.
app.include_router(regulatory_change_endpoints.router)

# Continuous Third-Party/Vendor Risk: vendor SOC 2 register CRUD.
app.include_router(vendor_risk_endpoints.router)

# AI Governance: AI system register CRUD.
app.include_router(ai_governance_endpoints.router)

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
_mount_mcp("/mcp/infrastructure-monitoring", "Infrastructure Monitoring: IaaS/OS/DB continuous audit", _infrastructure_monitoring_mcp)
_mount_mcp("/mcp/fair",             "Risk Quantification: FAIR Monte Carlo loss modeling",      _fair_mcp)
_mount_mcp("/mcp/process-mining",   "Process Mining: variant/conformance/cycle-time analysis",  _process_mining_mcp)
_mount_mcp("/mcp/risk-rating",      "Canonical RAG risk scoring (0-25 scale, R/A/G) — pass any risk through the same methodology as the register", _risk_rating_mcp)


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
    period_begin: str = ""
    period_end: str = ""
    persona: str = ""
    appetite_level: str = ""

class RiskFactorsRequest(BaseModel):
    ticker: str
    max_filings: int = 2

class PrivateCompanyRequest(BaseModel):
    name: str
    industry: str = ""
    fiscal_year_end: str = ""

class CommitFinancialsRequest(BaseModel):
    ticker: str
    line_items: List[Dict[str, Any]]

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


def _embed_new_rss_articles(company_id: Optional[int], new_articles: list) -> None:
    """Embed newly-inserted RSS articles (EMBT_ARTICLE) so chat RAG retrieval
    can actually surface them — save_rss_articles_full() only returns rows it
    just inserted, so this never re-embeds the same article twice."""
    if not new_articles or not embedding_util.is_available():
        return
    try:
        for art in new_articles:
            text = f"{art['title']}: {art.get('summary') or ''}".strip()
            vec = embedding_util.embed_text(text)
            if vec:
                db.save_embedding(
                    source_table="rss_articles", source_id=art["id"],
                    content_type=db.EMBT_ARTICLE, embedding=vec,
                    company_id=company_id, text_snippet=text[:600],
                )
    except Exception as exc:
        logger.warning("RSS article embedding failed (non-fatal): %s", exc)


def _embed_new_cem_root_causes(run_id: int, new_events: list) -> None:
    """Embed CEM (Continuous Exception Monitoring) root-cause narratives
    (EMBT_CEM_RC) — save_cem_events() only returns rows that actually have a
    narrative, via RETURNING, so no separate lookup query is needed here."""
    if not new_events or not embedding_util.is_available():
        return
    try:
        company_id = db.get_company_id_for_run(run_id)
        for ev in new_events:
            vec = embedding_util.embed_text(ev["root_cause"])
            if vec:
                db.save_embedding(
                    source_table="cem_events", source_id=ev["pk_id"],
                    content_type=db.EMBT_CEM_RC, embedding=vec,
                    company_id=company_id, text_snippet=ev["root_cause"][:600],
                )
    except Exception as exc:
        logger.warning("CEM root-cause embedding failed (non-fatal): %s", exc)


def _embed_proxy_sections(company_id: Optional[int], proxy_id: Optional[int], sections: dict) -> None:
    """Chunk and embed DEF 14A governance sections (EMBT_PROXY) — the four
    section texts (exec comp, board, say-on-pay, shareholder proposals) can
    each run to thousands of characters, so this chunks the same way
    _embed_risk_factors chunks Item 1A text, rather than truncating to one
    embedding per filing."""
    if not proxy_id or not embedding_util.is_available():
        return
    try:
        rows: list = []
        chunk_idx = 0
        for section_name, section_text in (sections or {}).items():
            if not section_text:
                continue
            for chunk in embedding_util.chunk_text(section_text):
                vec = embedding_util.embed_text(f"{section_name}: {chunk}")
                if vec:
                    rows.append({
                        "source_table": "edgar_proxy_filings", "source_id": proxy_id,
                        "content_type": db.EMBT_PROXY, "chunk_index": chunk_idx,
                        "company_id": company_id, "embedding": vec,
                        "text_snippet": chunk[:600],
                    })
                chunk_idx += 1
        if rows:
            db.save_embeddings_bulk(rows)
    except Exception as exc:
        logger.warning("Proxy section embedding failed (non-fatal): %s", exc)


def _embed_new_scenario_narratives(company_id: Optional[int], run_id: int) -> None:
    """Embed scenario_analyses narratives (EMBT_SCENARIO) so chat RAG can
    surface scenario outlook text, not just risk-factor chunks."""
    if not run_id or not embedding_util.is_available():
        return
    try:
        for row in db.get_scenario_rows_for_embedding(run_id):
            if not row.get("narrative"):
                continue
            text = f"{row['scenario']} scenario: {row['narrative']}".strip()
            vec = embedding_util.embed_text(text)
            if vec:
                db.save_embedding(
                    source_table="scenario_analyses", source_id=row["pk_id"],
                    content_type=db.EMBT_SCENARIO, embedding=vec,
                    company_id=company_id, text_snippet=text[:600],
                )
    except Exception as exc:
        logger.warning("Scenario narrative embedding failed (non-fatal): %s", exc)


def _embed_and_link_risk_narratives(company_id: int, run_id: int) -> None:
    """Embed each risk's name+category+narrative and use the vectors to find
    cross-category 'similar_to' relationships the rule-based graph edges
    (compute_and_save_risk_relationships) can't see — those only ever connect
    risks already in the same category. Best-effort: a missing OPENAI_API_KEY
    or pgvector just means this run's graph has no similar_to edges, same as
    every run before this feature existed."""
    if not company_id or not run_id or not embedding_util.is_available():
        return
    try:
        rows = db.get_risk_score_rows_for_embedding(run_id)
        for r in rows:
            text = f"{r['risk_name']} ({r['category'] or 'Uncategorised'}): {r['narrative'] or ''}".strip()
            vec = embedding_util.embed_text(text)
            if vec:
                db.save_embedding(
                    source_table="risk_scores", source_id=r["pk_id"],
                    content_type=db.EMBT_RISK_NARRATIVE, embedding=vec,
                    company_id=company_id, text_snippet=text[:600],
                )
        db.link_similar_risks_by_embedding(company_id, run_id)
    except Exception as exc:
        logger.warning("Risk narrative embedding/similarity linking failed (non-fatal): %s", exc)


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
        "period_begin": req.period_begin or None,
        "period_end": req.period_end or None,
        "persona": req.persona or None,
        "appetite_level": req.appetite_level or None,
    }
    run_id = db.create_risk_loop_run(company_id, run_config)
    if not run_id:
        return None

    db.save_financial_ratios(run_id, result.get("financial_ratios", {}))
    db.save_beneish_mscore(run_id, result.get("beneish_mscore", {}))
    db.save_altman_zscore(run_id, result.get("altman_zscore", {}))

    risk_data = result.get("risk_scores", {})
    risks_list = risk_data.get("risks", [])
    db.save_risk_scores(run_id, risks_list)

    scenario_dict = result.get("scenario_analysis", {})
    db.save_scenario_analyses(run_id, scenario_dict)
    db.save_grey_swan(run_id, result.get("grey_swan", {}))
    _embed_new_scenario_narratives(company_id, run_id)

    # Compute and persist graph relationships from this run's risk set
    if company_id and risks_list:
        try:
            db.compute_and_save_risk_relationships(company_id, run_id, risks_list)
            db.save_scenario_risk_impacts(run_id, risks_list, scenario_dict)
        except Exception as _rel_err:
            logger.warning("Risk relationship computation failed (non-fatal): %s", _rel_err)
        _embed_and_link_risk_narratives(company_id, run_id)

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

    # Ingest the entity's filed segment/geography revenue breakdown on every
    # run, unconditionally — not gated behind SOX materiality (a segment can
    # be immaterial for SOX purposes and still be exactly what a risk
    # assessment or the Coverage Cube needs to see). Runs BEFORE the SOX
    # auto-rescope block below so db.get_sox_segments() has fresh data on
    # the same run instead of scoping against whatever was last persisted
    # (or nothing, if this ticker had never hit the manual import path).
    # Skipped for private tickers (no CIK/SEC filings to parse) and never
    # fatal to the run itself — a filer with no reportable segments, or a
    # transient EDGAR fetch failure, is not a reason to fail the whole
    # analysis.
    ticker_for_segments = result.get("ticker", req.ticker)
    _seg_persist_result = None
    _seg_forecast_result = None
    if company_id and not db.is_private_ticker(ticker_for_segments):
        try:
            import edgar_segments
            _seg_persist_result = edgar_segments.persist_segments(ticker_for_segments)
        except Exception as _seg_err:
            logger.warning("Segment ingestion failed (non-fatal): %s", _seg_err)

        # Segment-level forecasts — walks up to 10 recent 10-Qs to build a
        # real per-segment quarterly history (a single filing's own
        # comparatives top out around 4 points, short of the 8-quarter
        # minimum the ensemble forecast model needs) and persists to
        # segment_forecasts. Noticeably heavier than the actuals-only
        # ingestion above (up to ~20 EDGAR requests vs. ~2), so it's still
        # unconditional but kept as its own try/except: a slow or failed
        # segment forecast should never take down the analysis, and
        # shouldn't block the actuals ingestion above from having already
        # succeeded.
        try:
            import edgar_segments
            _seg_forecast_result = edgar_segments.forecast_segments(ticker_for_segments, run_id=run_id)
        except Exception as _seg_fc_err:
            logger.warning("Segment forecasting failed (non-fatal): %s", _seg_fc_err)

        # Segment/geography-specific risk assessment (Coverage Cube Phase 3)
        # — scores real segment concentration/decline/divergence risk from
        # the actuals + forecast just gathered above, and writes them into
        # the SAME risk_scores table the consolidated risk register uses
        # (tagged segment_type/segment_name, source_framework='segment_risk'),
        # so they show up in Stage 2's register and the Coverage Cube
        # alongside consolidated risks rather than living in a side table
        # nothing else reads.
        if _seg_persist_result and _seg_persist_result.get("extracted"):
            try:
                import segment_risk_tool
                consolidated_growth = (result.get("financial_ratios") or {}).get("revenue_growth")
                consolidated_growth_pct = consolidated_growth * 100 if consolidated_growth is not None else None
                segment_risks = segment_risk_tool.assess_segment_risks(
                    _seg_persist_result, _seg_forecast_result,
                    consolidated_revenue_growth_pct=consolidated_growth_pct,
                )
                if segment_risks:
                    db.save_risk_scores(run_id, segment_risks)
                    # Mutating `result` here reaches the caller: predictive_full_analysis()
                    # holds the same dict and returns it as the API response body, so this
                    # is how a LIVE run (not just a DB-backed reload) gets segment risks in
                    # front of the frontend — see mcp-data.js's mapSegmentRisks(), which
                    # reads this key to fold them into output.s2.risks for Stage 3 and the
                    # Sankey to see, closing the loop Phase 3 otherwise left DB-only.
                    result["segment_risks"] = segment_risks
            except Exception as _seg_risk_err:
                logger.warning("Segment risk assessment failed (non-fatal): %s", _seg_risk_err)

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

                    # Real detected balances (material_accounts_tool.py) to
                    # prefer over sox_scoping_tool's heuristic estimates —
                    # a single extra XBRL fetch for the SUBJECT only (not a
                    # peer fan-out, so none of the OOM-bounding concerns
                    # that apply to peer enrichment apply here). Best-effort:
                    # a failure here just means scoping falls back to the
                    # heuristic, same as before this feature existed.
                    real_balances = None
                    try:
                        import material_accounts_tool
                        subj_cik = result.get("cik", "")
                        if subj_cik:
                            subj_xbrl = fetch_xbrl_facts(subj_cik) or {}
                            uploaded_xbrl = db.get_manual_financials(
                                company_id, granularity=["annual", "quarterly"]) or {}
                            mat_accounts = material_accounts_tool.detect_material_accounts(
                                subj_xbrl, result.get("sic", ""), uploaded_xbrl)
                            real_balances = material_accounts_tool.real_balances_for_sox(mat_accounts)
                    except Exception as _mat_err:
                        logger.warning("Material-account balance detection failed for SOX scoping (non-fatal, falling back to heuristic estimates): %s", _mat_err)

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
                        real_balances=real_balances,
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
        # True once claude_client._create_message has actually had to retry a
        # call against FALLBACK_MODEL — the cheap public signal that
        # DENDRAI_CLAUDE_MODEL is stale. Full detail: GET /auth/admin/model-config.
        "ai_model_fallback_active": claude_client.get_model_status()["fallback_active"],
        "environment": deploy_env.ENVIRONMENT_NAME,
        "environment_is_dev": deploy_env.IS_DEVELOPMENT,
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


@app.get("/cem-events")
def get_cem_events(limit: int = 200):
    """Recently-logged CEM incidents (real cem_events rows, not templates) —
    backs the Risk Quantification screen's "CEM Event" resource picker."""
    return {"events": db.list_recent_cem_events(limit) if db.is_available() else []}


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

# Moved to sic_industry.py (single source of truth — also used by
# material_accounts_tool.py, which can't import this FastAPI app module
# without a circular import). Kept as a thin alias here so every existing
# call site below (_classify_sic(sic)) is unaffected.
from sic_industry import classify_sic as _classify_sic  # noqa: E402


@app.get("/rss/feeds")
def get_rss_feeds():
    """RSS feed registry — canonical list of compliance/regulatory feeds with weights and domains."""
    return {"feeds": RSS_INGEST_FEEDS}


# Per-host User-Agent overrides for feed sources with UA-based bot protection
# that runs the opposite direction of SEC.gov's fair-access policy (see
# rss_proxy() below) — federalregister.gov blocks non-browser UAs rather than
# requiring one.
_RSS_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_RSS_IDENTIFIED_UA = "Dendrai Intelligenza research@dendrai.ai"
_RSS_BROWSER_UA_HOSTS = ("federalregister.gov",)


def _rss_user_agent(url: str) -> str:
    from urllib.parse import urlparse as _up
    host = _up(url).hostname or ""
    if any(host == h or host.endswith("." + h) for h in _RSS_BROWSER_UA_HOSTS):
        return _RSS_BROWSER_UA
    return _RSS_IDENTIFIED_UA


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

    # SEC.gov rejects any User-Agent that doesn't declare a company + contact
    # per its fair-access policy (https://www.sec.gov/os/webmaster-faq#developers)
    # — a generic "Mozilla/5.0 (compatible; ...)" string gets a 200 response
    # whose body is actually an HTML block page ("Undeclared Automated Tool"),
    # not the requested feed. This format satisfies SEC while remaining a
    # normal identifiable UA for every other feed source.
    #
    # federalregister.gov (the EPA Climate Enforcement feed's source) is the
    # opposite case: its bot-protection redirects any non-browser-looking UA —
    # including the identified UA above — to unblock.federalregister.gov's
    # "Request Access" HTML challenge page, with a 200 status that looks like
    # success right up until the parser chokes on HTML instead of RSS. A
    # standard browser UA sails through untouched. Recomputed per redirect
    # hop (not just once up front) in case a chain crosses onto a host with
    # a different requirement.
    current_url = url
    try:
        for _ in range(6):  # max 5 redirects
            _headers = {"User-Agent": _rss_user_agent(current_url)}
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
    Run all 10 Dendrai Intelligenza predictive analytics models and persist
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


class SyncRiskScoresRequest(BaseModel):
    risks: List[Dict[str, Any]]


@app.post("/risk-scores/{run_id}/sync")
def sync_risk_scores(run_id: int, req: SyncRiskScoresRequest):
    """Re-sync risk_scores for a run after Stage 2's RSS/8-K/FRED
    signal-driven adjustments move scores/RAG/velocity beyond what
    _persist_full_analysis wrote at initial-analysis time (before those
    signals were applied). save_risk_scores is now an upsert (keyed on
    run_id + risk_ref), so this is safe to call repeatedly as the displayed
    risk set changes over the course of a run — see save_risk_scores'
    docstring for the full "why" (this closes the gap where Posture Trend's
    RAG counts silently showed pre-adjustment data)."""
    if not db.is_available():
        return {"synced": False, "reason": "database not configured"}
    db.save_risk_scores(run_id, req.risks)
    return {"synced": True, "count": len(req.risks)}


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


@app.post("/company/private")
def create_private_company(req: PrivateCompanyRequest):
    """Create a company with no SEC ticker/CIK — for private entities that
    don't file 10-K/10-Q/8-K with the SEC. Assigns a synthetic PVT-<SLUG>
    pseudo-ticker so the rest of the ticker-keyed pipeline (this endpoint's
    siblings, the frontend's cfg.ticker, /predictive/full-analysis) works
    unmodified — see db.upsert_private_company."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    ticker = db.upsert_private_company(name, req.industry, req.fiscal_year_end)
    if not ticker:
        raise HTTPException(status_code=500, detail="Failed to create private company")
    return {"ticker": ticker, "company_name": name, "industry": req.industry, "is_private": True}


@app.post("/financials/upload")
async def upload_financials(file: UploadFile = File(...), ticker: str = Form(...)):
    """Parse an uploaded financial statement (.xlsx/.xls/.csv or .pdf) into
    normalized line items for review. Nothing is persisted here — the
    (possibly user-edited) reviewed set is POSTed to /financials/commit
    separately, mirroring /risk-register/upload's parse-then-review-then-
    persist flow."""
    suffix = (file.filename or "upload").rsplit(".", 1)[-1].lower()
    content = await file.read()
    try:
        if suffix == "pdf":
            result = manual_financials_tool.parse_pdf(content, file.filename or "upload")
        elif suffix in ("xlsx", "xls", "csv"):
            result = manual_financials_tool.parse_spreadsheet(content, file.filename or "upload")
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '.{suffix}' — upload a .xlsx, .xls, .csv, or .pdf file",
            )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result["ticker"] = ticker.upper()
    return result


@app.post("/financials/commit")
def commit_financials(req: CommitFinancialsRequest):
    """Persist a reviewed set of line items (from /financials/upload,
    possibly edited by the user) for `ticker`. Resolves/creates the company
    row if it doesn't exist yet for a public ticker (mirrors /edgar/financials'
    upsert-on-write); a private ticker must already exist via
    POST /company/private."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    ticker = req.ticker.upper()
    if db.is_private_ticker(ticker):
        company_id = db.get_company_id(ticker)
        if not company_id:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown private company '{ticker}' — create it via POST /company/private first",
            )
    else:
        company_id = db.get_company_id(ticker)
        if not company_id:
            try:
                meta, _ = get_company_info(ticker)
            except Exception as e:
                raise HTTPException(status_code=404, detail=f"Could not resolve ticker '{ticker}': {e}")
            company_id = db.upsert_company({
                "ticker": ticker,
                "company_name": meta["company_name"],
                "cik": meta.get("cik", ""),
                "sic": meta.get("sic", ""),
                "sic_description": meta.get("sic_description", ""),
                "entity_type": meta.get("entity_type"),
                "state_of_inc": meta.get("state_of_inc"),
                "fiscal_year_end": meta.get("fiscal_year_end"),
                "exchanges": meta.get("exchanges"),
            })
            if not company_id:
                raise HTTPException(status_code=500, detail="Failed to create company record")

    return manual_financials_tool.commit_line_items(company_id, req.line_items)


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
                new_articles = db.save_rss_articles_full(company_id, result)
                _embed_new_rss_articles(company_id, new_articles)

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
            new_articles = db.save_rss_articles_full(None, {"feeds": [
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
            _embed_new_rss_articles(None, new_articles)

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


_8K_CLASSIFY_MAX_PER_CALL = 10  # bounds per-request latency/LLM cost — see comment below


@app.post("/edgar/8k-events")
def edgar_8k_events(req: TickerRequest):
    """
    Return annotated 8-K events and save to edgar_8k_events. Filings whose
    item codes intersect the "material" set (acquisitions, divestitures,
    bankruptcy, impairments, restatements, change of control — see
    edgar_tool._MATERIAL_8K_ITEMS) get their actual filing body fetched and
    classified by an LLM into structured content: counterparty, consideration,
    what was acquired/sold, stated rationale. Previously these item codes were
    only ever surfaced as a bare label ("Completion of Acquisition or
    Disposition of Assets") with no indication of WHAT was acquired or from
    whom — this is what actually answers that question.

    Classification is capped at the _8K_CLASSIFY_MAX_PER_CALL most recent
    material filings per call to bound latency and LLM cost; the rest are
    still returned with is_material=True but classification=None.
    """
    try:
        meta, sub = get_company_info(req.ticker)
        cik = meta["cik"]
        filings = parse_filings(sub, {"8-K"})["8-K"][:30]
        events = [annotate_8k(dict(f)) for f in filings]

        classified_count = 0
        new_material_events = []
        for ev in events:
            ev["is_material"] = has_material_item(ev)
            ev["classification"] = None
            if ev["is_material"] and classified_count < _8K_CLASSIFY_MAX_PER_CALL:
                classified_count += 1
                try:
                    text = fetch_filing_text(cik, ev)
                    ev["classification"] = classify_8k_event(ev.get("item_descriptions"), text) if text else None
                except Exception:
                    ev["classification"] = None

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
                new_accessions = set(db.save_edgar_8k_events(company_id, events))
                new_material_events = [
                    ev for ev in events
                    if ev.get("is_material") and ev.get("accession_number") in new_accessions
                ]
                if new_material_events and _HAS_MCP_GOVERNANCE:
                    for ev in new_material_events:
                        cls = ev.get("classification") or {}
                        try:
                            mcp_governance._post_webhook_alert(
                                f"\U0001f4c8 *New material corporate event* — {meta['company_name']} ({req.ticker.upper()})",
                                [
                                    {"title": "Filed", "value": ev.get("date", ""), "short": True},
                                    {"title": "Item(s)", "value": ev.get("items", ""), "short": True},
                                    {"title": "Type", "value": cls.get("action_type", "unclassified"), "short": True},
                                    {"title": "Summary", "value": cls.get("summary") or "; ".join((ev.get("item_descriptions") or {}).values()), "short": False},
                                ] + ([{"title": "Suggested next step", "value": cls["suggested_risk_note"], "short": False}] if cls.get("suggested_risk_note") else []),
                                color="#2563eb",
                            )
                        except Exception as exc:
                            logger.warning("Corporate event alert dispatch failed: %s", exc)
                    db.mark_corporate_events_alerted([ev["accession_number"] for ev in new_material_events])

        result["new_material_events"] = new_material_events
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/edgar/corporate-events")
def list_corporate_events(
    ticker: Optional[str] = None,
    status: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """
    The tracked record behind material 8-K detection — every acquisition,
    divestiture, restructuring, bankruptcy, impairment, restatement, or
    change-of-control event, with a governed status (new/reviewing/assessed/
    dismissed), owner, and notes, not just a filing that silently sat there.
    """
    if not db.is_available():
        return {"rows": [], "count": 0, "new_count": 0}
    if not ticker:
        rows = db.list_corporate_events(status=status)
        return {"rows": rows, "count": len(rows), "new_count": sum(1 for r in rows if r["status"] == "new")}

    # DB-first lookup — this endpoint is hit on every Scenario Analysis page
    # load, so it must not depend on a live EDGAR round-trip just to resolve
    # a ticker that was almost certainly already ingested earlier. A live
    # lookup is only attempted as a fallback for a genuinely new ticker.
    company_id = db.get_company_id_by_ticker(ticker)
    if company_id is None:
        try:
            meta, _sub = get_company_info(ticker)
            company_id = db.upsert_company({
                "ticker": ticker, "company_name": meta["company_name"],
                "cik": meta.get("cik", ""), "sic": meta.get("sic", ""),
            })
        except Exception:
            company_id = None

    if company_id is None:
        # A ticker was explicitly requested — if it can't be resolved (unknown
        # ticker, or a transient EDGAR failure like a 429), returning every
        # company's events instead would leak other companies' material
        # corporate events onto this ticker's Scenario Analysis page.
        return {"rows": [], "count": 0, "new_count": 0}

    rows = db.list_corporate_events(company_id=company_id, status=status)
    return {"rows": rows, "count": len(rows), "new_count": sum(1 for r in rows if r["status"] == "new")}


@app.put("/edgar/corporate-events/{event_id}")
def update_corporate_event_endpoint(
    event_id: int,
    body: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """Assign an owner, change status, or add notes on a detected corporate
    event. Any field omitted from the body is left unchanged."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    ok = db.update_corporate_event(
        event_id, status=body.get("status"), owner=body.get("owner"), notes=body.get("notes"),
    )
    return {"ok": ok, "id": event_id}


def _annual_series_by_end(xbrl: dict, metric: str) -> Dict[str, float]:
    """{fiscal_period_end: value} for a metric's 10-K/20-F annual data points,
    deduped by period end (later filings — e.g. a 10-K/A restatement — win)."""
    pts = [p for p in xbrl.get(metric, {}).get("data_points", [])
           if p.get("form") in {"10-K", "20-F", "10-K/A"} and p.get("val") is not None and p.get("end")]
    pts.sort(key=lambda p: p.get("filed", ""))  # earliest-filed first, so later filings overwrite in the dict below
    return {p["end"]: p["val"] for p in pts}


def _build_ratio_history(xbrl: dict, max_years: int = 6) -> list:
    """Multi-year gross_margin/rd_intensity/revenue_growth series (oldest → newest),
    for the peer-benchmarking time series chart. Reuses whatever periods Revenue
    resolved for; a year with no Revenue point is dropped rather than guessed at."""
    rev_by_end = _annual_series_by_end(xbrl, "Revenue")
    gp_by_end  = _annual_series_by_end(xbrl, "GrossProfit")
    rd_by_end  = _annual_series_by_end(xbrl, "ResearchAndDevelopment")
    if not rev_by_end:
        return []

    # One extra year of bootstrap history so the earliest kept year still gets
    # a revenue_growth value instead of starting the series with a null.
    ends = sorted(rev_by_end.keys())[-(max_years + 1):]
    history = []
    for i, end in enumerate(ends):
        rev = rev_by_end.get(end)
        gp  = gp_by_end.get(end)
        rd  = rd_by_end.get(end)
        growth = None
        if i > 0:
            prev_rev = rev_by_end.get(ends[i - 1])
            if rev and prev_rev:
                growth = (rev - prev_rev) / prev_rev
        history.append({
            "period":          end,
            "gross_margin":    (gp / rev) if (rev and gp is not None) else None,
            "rd_intensity":    (rd / rev) if (rev and rd is not None) else None,
            "revenue_growth":  growth,
        })
    return history[-max_years:]



# Cross-request cache for peer XBRL enrichment, keyed by CIK. A peer's
# fundamentals (gross_margin, m_score, etc.) depend only on its own CIK, not
# on who's asking — so two different subject companies that happen to share
# a peer (a common case: AMZN/MSFT/etc. show up as a competitor across many
# tickers' peer sets) were each independently re-fetching and re-computing
# the exact same peer's financials. TTL is long (fundamentals are annual
# 10-K data, not intraday) purely to bound memory growth over a long-running
# process, not because the data goes stale quickly.
_PEER_ENRICH_CACHE_TTL = 6 * 3600
# "_flat_metrics" is cached (subject-independent — a peer's own latest XBRL
# values don't depend on who's comparing against it) but deliberately not
# exposed on the peer dict returned to callers; "material_accounts" is
# NEVER cached here — see _attach_peer_material_accounts, it's derived
# fresh per call from the subject's own industry template, which differs
# request to request even for the same cached peer.
_PEER_ENRICH_FIELDS = ("gross_margin", "rd_intensity", "revenue_growth", "history", "m_score", "z_score", "_flat_metrics")
_peer_enrich_cache: Dict[str, tuple] = {}  # cik -> (expires_at, fields_dict)

# Peer enrichment concurrency — bounds peak memory, not just wall-clock time.
# Confirmed live (2026-08-30 development OOM incident): fetch_sic_peers now
# ranks currently-active companies first (the peer-benchmarking data fix),
# which means a "peer" is now typically a real large-cap filer instead of
# the decades-defunct shell companies the old ranking mostly returned. A
# large-cap's companyfacts.json measured 3.5-4.5MB raw over the wire and
# ~4.4x that (15-20MB) once parsed into Python objects. At max_workers=8,
# a single /edgar/peers call could hold 8 of these in memory at once — and
# the SAME page load can trigger this twice (the live POST /edgar/peers call
# and GET /edgar/peers/{ticker}'s self-heal re-enrichment), so a genuinely
# unbounded amount of large-cap XBRL data could be in flight simultaneously
# for one user action. This is what actually OOM-killed the backend that
# night, not anything in the same deploy's unrelated risk-scoring work
# (confirmed by measurement, not guessed at — see the incident notes).
# 3 concurrent fetches keeps enrichment reasonably fast without the
# unbounded blow-up; it does not fix the per-document size, which is
# capped by what SEC's bulk companyfacts endpoint returns.
_PEER_ENRICH_MAX_WORKERS = 3
_peer_enrich_lock = threading.Lock()


def _attach_peer_material_accounts(peer: dict, flat_metrics: dict, subject_sic: str) -> None:
    """Material-account ratios for this peer, scored against the SUBJECT
    company's industry template (not the peer's own) — so every peer in a
    comparison is measured on the same line items rather than each peer
    picking its own set. Never cached (see _PEER_ENRICH_FIELDS) — the same
    peer compared against a different subject's industry gets a different
    template, and flat_metrics (cheap: a handful of floats) makes
    recomputing this free even on a cache hit for the base fields.
    """
    if not flat_metrics or not subject_sic:
        return
    import material_accounts_tool
    synthetic_xbrl = {m: {"label": m, "data_points": [{"val": v}]} for m, v in flat_metrics.items()}
    try:
        peer["material_accounts"] = [
            a for a in material_accounts_tool.detect_material_accounts(synthetic_xbrl, subject_sic)
            if a["is_material"]
        ]
    except Exception:
        pass


def _enrich_peer_financials(peer: dict, subject_sic: str = "") -> dict:
    """Fetch XBRL facts for a peer and attach gross_margin, rd_intensity,
    revenue_growth, a simplified Beneish M-score and Altman Z''-Score for
    cross-peer benchmarking, a multi-year `history` series for the
    peer-benchmarking time series chart, and (when `subject_sic` is given)
    material-account ratios scored against the SUBJECT's industry template.
    Base fields are cached by CIK — see _peer_enrich_cache above; material
    accounts are always recomputed per call (see _attach_peer_material_accounts)."""
    cik = str(peer.get("cik") or peer.get("cik_plain") or "").zfill(10)
    if not cik or cik == "0000000000":
        return peer

    now = time.time()
    with _peer_enrich_lock:
        hit = _peer_enrich_cache.get(cik)
    if hit and hit[0] > now:
        peer.update(hit[1])
        _attach_peer_material_accounts(peer, peer.pop("_flat_metrics", None) or {}, subject_sic)
        return peer

    try:
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
        cur_assets, _    = latest_two_annual("CurrentAssets")
        cur_liab, _      = latest_two_annual("CurrentLiabilities")
        tot_liab, _      = latest_two_annual("TotalLiabilities")
        equity, _        = latest_two_annual("StockholdersEquity")
        retained, _      = latest_two_annual("RetainedEarnings")
        ebit, _          = latest_two_annual("OperatingIncome")

        peer["gross_margin"]   = (gp  / rev) if rev and gp  is not None else None
        peer["rd_intensity"]   = (rd  / rev) if rev and rd  is not None else None
        peer["revenue_growth"] = ((rev - rev_prev) / rev_prev) if rev and rev_prev else None
        peer["history"]        = _build_ratio_history(xbrl)

        # A cheap, small snapshot of every whitelisted XBRL metric's latest
        # value (a few dozen floats) — kept and cached below (unlike the
        # full parsed document) so a LATER request for this same peer under
        # a DIFFERENT subject's industry template can still detect material
        # accounts for it without re-fetching/re-parsing its XBRL.
        flat_metrics = {}
        for metric in XBRL_METRICS:
            v, _ = latest_two_annual(metric)
            if v is not None:
                flat_metrics[metric] = v
        peer["_flat_metrics"] = flat_metrics

        # Done with the full parsed document (15-20MB in memory for a
        # large-cap peer, see _PEER_ENRICH_MAX_WORKERS above) — only the
        # small extracted ratios above (and flat_metrics, just extracted)
        # are kept. Drop the reference now rather than waiting for the
        # function to return, since this thread may sit in the pool a while
        # longer processing the next peer.
        del xbrl

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

        # Altman Z''-Score (general/non-manufacturer variant, book equity in
        # place of market equity — same formula as risk-engine.js's
        # computeRatios(), coefficients per Altman 1995). Missing sub-terms
        # are held at a neutral 0 rather than nulling the whole score, same
        # resilience pattern as m_score above.
        wc   = (cur_assets - cur_liab) if (cur_assets is not None and cur_liab is not None) else None
        x1   = (wc / assets) if (wc is not None and assets) else None
        x2   = (retained / assets) if (retained is not None and assets) else None
        x3   = (ebit / assets) if (ebit is not None and assets) else None
        x4   = (equity / tot_liab) if (equity is not None and tot_liab) else None
        if x1 is not None or x2 is not None or x3 is not None or x4 is not None:
            peer["z_score"] = (
                6.56 * (x1 if x1 is not None else 0.0)
                + 3.26 * (x2 if x2 is not None else 0.0)
                + 6.72 * (x3 if x3 is not None else 0.0)
                + 1.05 * (x4 if x4 is not None else 0.0)
            )
    except Exception:
        pass

    fields = {k: peer[k] for k in _PEER_ENRICH_FIELDS if k in peer}
    if fields:
        with _peer_enrich_lock:
            _peer_enrich_cache[cik] = (now + _PEER_ENRICH_CACHE_TTL, fields)

    # _flat_metrics is cache/internal-only — never returned to a caller.
    _attach_peer_material_accounts(peer, peer.pop("_flat_metrics", None) or {}, subject_sic)
    return peer


def _peer_has_data(peer: dict) -> bool:
    """A peer is kept only if at least one financial benchmark resolved.
    m_score and z_score are each computed from their own distinct sets of
    XBRL fields, different from the other ratios and from each other — a
    peer can have a valid m_score or z_score with none of the others, and
    must not be dropped before it reaches the corresponding gauge's peer
    comparison."""
    return any(peer.get(k) is not None for k in
               ("gross_margin", "rd_intensity", "revenue_growth", "m_score", "z_score"))


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

        with concurrent.futures.ThreadPoolExecutor(max_workers=_PEER_ENRICH_MAX_WORKERS) as pool:
            peers = list(pool.map(lambda p: _enrich_peer_financials(p, sic), peers))

        # 3) Remove all companies that have no data.
        peers = [p for p in peers if _peer_has_data(p)]

        try:
            subject_xbrl = fetch_xbrl_facts(meta["cik"])
            subject_history = _build_ratio_history(subject_xbrl) if subject_xbrl else []
        except Exception:
            subject_history = []

        result = {
            "ticker": req.ticker.upper(),
            "company_name": meta["company_name"],
            "sic": sic,
            "sic_description": meta.get("sic_description", ""),
            "peer_source": peer_source,
            "named_competitors": named_competitors,
            "peers": peers,
            "subject_history": subject_history,
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



# A saved peer set below this count of usable, enriched peers is treated as
# starved and worth re-deriving — see the self-heal block in
# edgar_peers_saved below.
_MIN_USEFUL_SIC_PEERS = 3


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

    with concurrent.futures.ThreadPoolExecutor(max_workers=_PEER_ENRICH_MAX_WORKERS) as pool:
        peers = list(pool.map(lambda p: _enrich_peer_financials(p, saved.get("sic") or ""), saved["peers"]))
    peers = [p for p in peers if _peer_has_data(p)]

    # Self-heal a starved peer set. fetch_sic_peers() used to return an
    # alphabetically-first slice of EDGAR's full SIC roster — dominated by
    # decades-defunct companies (confirmed live for SIC=3674: 13 of the
    # first 15 had zero enrichable financial data) — before it was fixed to
    # rank companies with a live ticker first. Any peer set SAVED before
    # that fix is stuck starved forever under the old logic: this endpoint
    # only re-enriches the identities already in sic_peers, it never
    # re-derives them, so the fix alone did nothing for a ticker that had
    # already been through /edgar/peers once. Re-run discovery once when
    # the saved set is thin and only replace it if that actually does
    # better — never a downgrade, and a no-op once a ticker has re-derived
    # a healthy set (the new fetch_sic_peers ranking keeps producing one).
    if len(peers) < _MIN_USEFUL_SIC_PEERS and saved.get("sic"):
        fresh_identities = fetch_sic_peers(saved["sic"], max_peers=15)
        with concurrent.futures.ThreadPoolExecutor(max_workers=_PEER_ENRICH_MAX_WORKERS) as pool:
            fresh_peers = list(pool.map(lambda p: _enrich_peer_financials(p, saved["sic"]), fresh_identities))
        fresh_peers = [p for p in fresh_peers if _peer_has_data(p)]
        if len(fresh_peers) > len(peers):
            peers = fresh_peers
            company_id = db.upsert_company({
                "ticker": ticker, "company_name": saved["company_name"],
                "cik": saved.get("cik") or "", "sic": saved["sic"],
                "sic_description": saved.get("sic_description", ""),
            })
            if company_id:
                db.save_sic_peers(company_id, fresh_identities)

    saved["peers"] = peers
    saved["peer_source"] = "saved SIC peers"
    saved["named_competitors"] = []
    try:
        subject_xbrl = fetch_xbrl_facts(saved["cik"]) if saved.get("cik") else None
        saved["subject_history"] = _build_ratio_history(subject_xbrl) if subject_xbrl else []
    except Exception:
        saved["subject_history"] = []
    return saved


def _material_accounts_context(ticker: str):
    """Shared setup for both /material-accounts endpoints below: resolve the
    filer, pull its filed XBRL, and layer in any Mission Control uploads
    (which win over the filed value for the same metric — see
    material_accounts_tool.detect_material_accounts's uploaded_xbrl param).
    Returns (meta, sic, xbrl, accounts, company_id) — company_id is None
    when the DB isn't configured, which forecast_material_accounts already
    treats as "no manually-uploaded monthly detail available", not an error.
    """
    import material_accounts_tool

    meta, sub = get_company_info(ticker)
    sic = meta.get("sic", "")
    xbrl = fetch_xbrl_facts(meta["cik"]) or {}

    uploaded_xbrl: dict = {}
    company_id = None
    if db.is_available():
        company_id = db.upsert_company({
            "ticker": ticker, "company_name": meta["company_name"],
            "cik": meta.get("cik", ""), "sic": sic,
            "sic_description": meta.get("sic_description", ""),
        })
        if company_id:
            uploaded_xbrl = db.get_manual_financials(company_id, granularity=["annual", "quarterly"]) or {}

    accounts = material_accounts_tool.detect_material_accounts(xbrl, sic, uploaded_xbrl)
    return meta, sic, xbrl, accounts, company_id


@app.get("/material-accounts/{ticker}")
def material_accounts(ticker: str):
    """
    Dynamically detect which financial-statement accounts are material for
    this filer — industry-template (manufacturing/financial_services/saas)
    plus a materiality-ratio cutoff, see material_accounts_tool.py — rather
    than the fixed revenue/margin/eps/etc. set every ticker gets today.
    """
    try:
        meta, sic, _xbrl, accounts, _company_id = _material_accounts_context(ticker)
        return {
            "ticker": ticker.upper(), "company_name": meta["company_name"],
            "sic": sic, "sic_description": meta.get("sic_description", ""),
            "accounts": accounts,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class MaterialAccountsForecastRequest(BaseModel):
    horizon: int = 4
    macro_info: Optional[dict] = None


@app.post("/material-accounts/{ticker}/forecast")
def material_accounts_forecast(ticker: str, req: MaterialAccountsForecastRequest = MaterialAccountsForecastRequest()):
    """
    Forecast every detected material account, capped at
    material_accounts_tool._MAX_FORECAST_ACCOUNTS — the same lesson as the
    2026-08-30 peer-enrichment OOM incident applied to a new fan-out point.
    Uses the same generic ensemble forecasting engine
    (predictive_analytics_tool.run_forecast_backtest) every other KPI chart
    already relies on; no new modeling code.
    """
    import material_accounts_tool
    try:
        _meta, _sic, xbrl, accounts, company_id = _material_accounts_context(ticker)
        forecasts = material_accounts_tool.forecast_material_accounts(
            xbrl, req.macro_info, accounts, horizon=req.horizon, company_id=company_id,
        )
        return {"ticker": ticker.upper(), "accounts": accounts, "forecasts": forecasts}
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
                proxy_id = db.save_edgar_proxy(
                    company_id,
                    f["date"],
                    f["accession_number"],
                    sections,
                )
                _embed_proxy_sections(company_id, proxy_id, sections)

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
    new_cem_rows = db.save_cem_events(req.run_id, req.cem_events)
    _embed_new_cem_root_causes(req.run_id, new_cem_rows)
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


@app.get("/history/runs/{ticker}/posture-trend")
def history_posture_trend(
    ticker: str,
    limit: int = Query(default=20, ge=1, le=100),
):
    """Completed-run risk-posture snapshots for a ticker, oldest first (Feature 4)."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL not set)")
    rows = db.get_posture_trend(ticker, limit=limit)
    return {"ticker": ticker.upper(), "count": len(rows), "runs": rows}


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


# ── Digest notifications (Feature 5) ───────────────────────────────────────────
#
#  Deterministic "what changed since your last digest" summary — no LLM call,
#  built entirely from get_posture_trend's aggregate rows. Generation is lazy:
#  the frontend's existing 30s approval-inbox poll also calls check-due, which
#  only writes a new row when the user's digestFrequency preference interval
#  has elapsed AND a newer completed run exists. This avoids both a blind
#  backend cron and generating digests nobody will ever see.

_DIGEST_INTERVALS = {"daily": timedelta(hours=24), "weekly": timedelta(days=7)}


class DigestCheckRequest(BaseModel):
    ticker: str


def _build_digest_payload(ticker: str, from_row: Optional[dict], to_row: dict) -> dict:
    to_score = to_row.get("avg_score")
    from_score = from_row.get("avg_score") if from_row else None
    score_delta = (to_score - from_score) if (to_score is not None and from_score is not None) else None

    if not from_row:
        headline = (
            f"{ticker}: first posture snapshot — avg score "
            f"{to_score:.2f} · {to_row['red_count']}R/{to_row['amber_count']}A/{to_row['green_count']}G "
            f"across {to_row['risk_count']} risks."
        )
        red_delta = amber_delta = green_delta = risk_count_delta = None
    else:
        red_delta = to_row["red_count"] - from_row["red_count"]
        amber_delta = to_row["amber_count"] - from_row["amber_count"]
        green_delta = to_row["green_count"] - from_row["green_count"]
        risk_count_delta = to_row["risk_count"] - from_row["risk_count"]
        direction = "worsened" if (score_delta or 0) > 0 else "improved" if (score_delta or 0) < 0 else "unchanged"
        rag_bits = [f"{d:+d} {label}" for d, label in
                    ((red_delta, "RED"), (amber_delta, "AMBER"), (green_delta, "GREEN")) if d]
        rag_note = ", ".join(rag_bits) if rag_bits else "no RAG band changes"
        headline = (
            f"{ticker}: posture {direction} — avg score {from_score:.2f} -> {to_score:.2f} "
            f"({score_delta:+.2f}). {rag_note}."
        )

    return {
        "headline": headline,
        "from_run": from_row, "to_run": to_row,
        "avg_score_delta": score_delta,
        "red_delta": red_delta, "amber_delta": amber_delta,
        "green_delta": green_delta, "risk_count_delta": risk_count_delta,
    }


@app.post("/digests/check-due")
def digests_check_due(
    req: DigestCheckRequest,
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """Lazily generate a digest for `ticker` if the user's frequency preference
    interval has elapsed and a new completed run exists since their last one."""
    if not db.is_available():
        return {"generated": False, "reason": "db_unavailable", "unread_count": 0}

    freq = (current_user.get("preferences") or {}).get("digestFrequency", "off")
    interval = _DIGEST_INTERVALS.get(freq)
    if not interval:
        return {"generated": False, "reason": "off", "unread_count": db.count_unread_digests(current_user["id"])}

    ticker = req.ticker.upper()
    last = db.get_last_digest(current_user["id"], ticker)
    now = datetime.now(timezone.utc)
    if last and (now - last["generated_at"]) < interval:
        return {"generated": False, "reason": "not_due", "unread_count": db.count_unread_digests(current_user["id"])}

    trend = db.get_posture_trend(ticker, limit=2)
    if not trend:
        return {"generated": False, "reason": "no_completed_runs", "unread_count": db.count_unread_digests(current_user["id"])}

    to_row = trend[-1]
    from_row = trend[0] if len(trend) > 1 else None
    if last and to_row["run_id"] == last["to_run_id"]:
        return {"generated": False, "reason": "no_new_run", "unread_count": db.count_unread_digests(current_user["id"])}

    payload = _build_digest_payload(ticker, from_row, to_row)
    digest_id = db.save_digest_notification(
        current_user["id"], ticker,
        from_row["run_id"] if from_row else None, to_row["run_id"], payload,
    )
    return {
        "generated": True,
        "digest": {"id": digest_id, "ticker": ticker, "generated_at": now.isoformat(), "read_at": None, **payload},
        "unread_count": db.count_unread_digests(current_user["id"]),
    }


@app.get("/digests")
def digests_list(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    if not db.is_available():
        return {"digests": [], "unread_count": 0}
    return {
        "digests": db.list_digest_notifications(current_user["id"], limit=limit),
        "unread_count": db.count_unread_digests(current_user["id"]),
    }


@app.post("/digests/{digest_id}/read")
def digests_mark_read(
    digest_id: int,
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL not set)")
    ok = db.mark_digest_read(digest_id, current_user["id"])
    return {"ok": ok, "unread_count": db.count_unread_digests(current_user["id"])}


# ── Change Layer ────────────────────────────────────────────────────────────────
#
#  "What changed since your last run" — deterministic, no LLM call. Distinct from
#  the digest system above: digests are user-scoped, interval-gated, and written
#  to a notifications table for the inbox. This is ticker-scoped, always computed
#  fresh from the two most recent completed runs, and meant to render inline on
#  Risk Radar every time there IS a prior run — not gated by anyone's frequency
#  preference. Reuses get_posture_trend (already aggregates avg/RAG deltas) and
#  extends it with per-risk band transitions and M-Score/Z-Score band crossings,
#  which get_posture_trend's aggregate view can't see.
#
#  Materiality is intentionally narrow: a RAG/band crossing is always "high"; a
#  risk score move of >= _RISK_DELTA_MATERIAL points (on the 1-25 scale) without
#  a band crossing is "medium". Anything smaller is omitted — the point is to
#  avoid crying wolf on noise, matching the thresholds already used elsewhere
#  (see risk-engine.js's ragOf and drift_tool.py's PSI bands).
#
#  Peer-rank shift and forecast-direction flips are deliberately NOT included
#  yet: peer benchmarking snapshots aren't persisted per-run (only current
#  identities + live-re-enriched ratios), and forecasts aren't stored in a
#  run-comparable shape. Surfacing either now would mean fabricating a
#  comparison from data that doesn't actually exist at two points in time.

_RISK_DELTA_MATERIAL = 2.0  # points, on the 1-25 risk score scale


def _rag_word(v: Optional[str]) -> Optional[str]:
    """Normalize a rag_status value (may be a single letter or a full word,
    per risk_scores' historical inconsistency) to Red/Amber/Green/None."""
    if not v:
        return None
    c = v[0].upper()
    return {"R": "Red", "A": "Amber", "G": "Green"}.get(c)


def _score_band_change(kind: str, from_score: Optional[dict], to_score: Optional[dict], value_key: str) -> Optional[dict]:
    """Build a mscore_band/zscore_band change entry if the RAG band crossed
    between the two runs. Returns None if either run is missing the score,
    or the band is unchanged (not material)."""
    if not from_score or not to_score:
        return None
    from_rag = _rag_word(from_score.get("rag_status"))
    to_rag = _rag_word(to_score.get("rag_status"))
    if not from_rag or not to_rag or from_rag == to_rag:
        return None
    return {
        "type": kind,
        "materiality": "high",
        "from_band": from_rag, "to_band": to_rag,
        "from_value": from_score.get(value_key), "to_value": to_score.get(value_key),
    }


def _build_change_summary(ticker: str) -> dict:
    ticker = ticker.upper()
    trend = db.get_posture_trend(ticker, limit=2)
    if len(trend) < 2:
        return {
            "has_prior": False,
            "to_run": trend[-1] if trend else None,
            "headline": None, "posture": None, "changes": [],
        }

    from_row, to_row = trend[0], trend[1]
    digest = _build_digest_payload(ticker, from_row, to_row)

    changes: List[Dict[str, Any]] = []

    # Per-risk band/score transitions, matched by the stable risk_ref key.
    from_risks = {r["risk_ref"]: r for r in db.get_risk_scores_for_run(from_row["run_id"]) if r.get("risk_ref")}
    to_risks = {r["risk_ref"]: r for r in db.get_risk_scores_for_run(to_row["run_id"]) if r.get("risk_ref")}
    for ref, to_r in to_risks.items():
        from_r = from_risks.get(ref)
        if not from_r:
            continue  # new risk this run — not a "change" to an existing one
        from_rag, to_rag = _rag_word(from_r.get("rag")), _rag_word(to_r.get("rag"))
        from_score, to_score = from_r.get("score"), to_r.get("score")
        if from_score is None or to_score is None:
            continue
        delta = to_score - from_score
        band_crossed = bool(from_rag and to_rag and from_rag != to_rag)
        if not band_crossed and abs(delta) < _RISK_DELTA_MATERIAL:
            continue
        changes.append({
            "type": "risk_band",
            "materiality": "high" if band_crossed else "medium",
            "risk_ref": ref, "name": to_r.get("name") or to_r.get("risk_name"),
            "category": to_r.get("category"),
            "from_band": from_rag, "to_band": to_rag,
            "from_score": from_score, "to_score": to_score, "delta": delta,
        })

    # M-Score / Z-Score band crossings.
    m_change = _score_band_change("mscore_band", db.get_beneish_mscore(from_row["run_id"]), db.get_beneish_mscore(to_row["run_id"]), "m_score")
    if m_change:
        changes.append(m_change)
    z_change = _score_band_change("zscore_band", db.get_altman_zscore(from_row["run_id"]), db.get_altman_zscore(to_row["run_id"]), "z_score")
    if z_change:
        changes.append(z_change)

    # Rank: band crossings ("high") before score-move-only ("medium"); within
    # each tier, largest |delta| first (score-band entries have no delta — treat as 0).
    changes.sort(key=lambda c: (c["materiality"] != "high", -abs(c.get("delta") or 0)))

    return {
        "has_prior": True,
        "from_run": {"run_id": from_row["run_id"], "run_at": from_row["run_at"]},
        "to_run": {"run_id": to_row["run_id"], "run_at": to_row["run_at"]},
        "headline": digest["headline"],
        "posture": {
            "avg_score_delta": digest["avg_score_delta"],
            "red_delta": digest["red_delta"], "amber_delta": digest["amber_delta"],
            "green_delta": digest["green_delta"], "risk_count_delta": digest["risk_count_delta"],
        },
        "changes": changes,
    }


@app.get("/changes/{ticker}")
def get_changes(ticker: str):
    """What changed since this ticker's previous completed run — see module
    comment above for scope and materiality rules."""
    if not db.is_available():
        return {"has_prior": False, "to_run": None, "headline": None, "posture": None, "changes": []}
    return _build_change_summary(ticker)


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
# Keyed per-ticker (not a single global blob) so restoring on login shows the
# last run for the ticker actually selected, not whichever ticker anyone last
# ran the loop for. Falls back to the legacy unscoped key only when no ticker
# is supplied, for callers that predate this change.

def _loop_state_key(ticker: Optional[str]) -> str:
    t = (ticker or "").strip().upper()
    return f"last_loop_state:{t}" if t else "last_loop_state"


@app.get("/loop/last-state")
def get_last_loop_state(ticker: Optional[str] = None):
    """Return the last persisted pipeline run state for restoration on page reload.

    Returns 503 when DATABASE_URL is not configured; 404 when no state has been saved yet.
    The frontend falls back to localStorage when this returns non-2xx.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured — set DATABASE_URL")
    state = db.get_app_config(_loop_state_key(ticker))
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
def save_last_loop_state(body: Dict[str, Any] = Body(...), ticker: Optional[str] = None):
    """Persist the full pipeline run state to the database for restoration on reload.

    Accepts the complete loop blob from app.jsx (output, stageState, gateState,
    loopLog, livefacts, perRiskAppetite, riskApprovals, scopeApprovals, manualAudits,
    narrativeResult, hubFocus, profile). Stored as JSONB in app_config table,
    keyed per-ticker — see _loop_state_key. Returns {saved: false} gracefully
    when the database is unavailable.
    """
    if not db.is_available():
        return {"saved": False, "reason": "database not configured"}
    ok = db.set_app_config(_loop_state_key(ticker), body)
    return {"saved": ok}


@app.delete("/loop/last-state", dependencies=[Depends(_require_api_key)])
def clear_last_loop_state(ticker: Optional[str] = None):
    """Clear the persisted pipeline run state for a ticker (used by 'Reset run'
    so a reset doesn't leave a stale run to resurface on the next login)."""
    if not db.is_available():
        return {"deleted": False, "reason": "database not configured"}
    ok = db.delete_app_config(_loop_state_key(ticker))
    return {"deleted": ok}


@app.get("/token-usage/summary")
def get_token_usage_summary_endpoint(
    days: int = 30,
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """
    Token Usage screen data: rolling by-user/by-feature breakdown for the
    given window, plus all-time calendar rollups (by month, month-to-date,
    by year, year-to-date). No admin gate beyond authentication — this
    screen is nav-permission-gated (auth.screen_permissions) like every
    other non-adminOnly screen, not hardcoded admin-only.
    """
    if not db.is_available():
        return {"rows": [], "totals": {}, "by_month": [], "month_to_date": {}, "by_year": [], "year_to_date": {}, "by_month_by_label": []}
    return {
        **db.get_token_usage_summary(days),
        **db.get_token_usage_time_summary(),
        "by_month_by_label": db.get_token_usage_by_month_by_label(),
    }


# ── Model Health drift watch (background) ───────────────────────────────────
# get_model_health_summary (below) is on-demand only — computed live on every
# request, no caching. Dispatching an alert from inside that handler would
# spam the webhook on every page view, not just on genuine new drift. So
# alerting runs from this separate periodic loop instead, using the exact
# same compute calls the on-demand endpoint uses.

_MODEL_HEALTH_CHECK_INTERVAL_S = float(os.environ.get("MODEL_HEALTH_CHECK_INTERVAL_S", "21600"))  # 6h


def _check_model_health_drift_once() -> list[dict]:
    """One drift-check cycle. Returns the list of newly-opened (metric, flag)
    entries, for logging/testing. Never raises — all failures are caught and
    logged, matching every other best-effort background path in this codebase.

    Each newly-detected drift becomes a persisted model_health_drift_incidents
    row (status=open) rather than just a webhook ping — the governed process
    trail an AI-governance committee expects (owner, status, closure), not
    just an ephemeral alert. "Don't re-alert" is now "there's already an open
    incident for this metric" instead of a blind 24h timestamp cooldown, so a
    metric that drifts again *after* a prior incident was resolved correctly
    opens a new one instead of staying silent."""
    import drift_tool

    alerted: list[dict] = []
    if not db.is_available():
        return alerted

    baseline_resets = db.get_baseline_resets()
    ratio_drift = drift_tool.compute_ratio_drift(db.get_financial_ratios_history(), baseline_resets=baseline_resets)
    fred_api_key = os.environ.get("FRED_API_KEY", "")
    fred_drift = drift_tool.compute_fred_regime_drift(fred_api_key, baseline_resets=baseline_resets)
    acceptance_drift = drift_tool.compute_ai_acceptance_drift(db.get_ai_acceptance_history(), baseline_resets=baseline_resets)

    entries = (
        [(r["ratio"], "ratio", r) for r in ratio_drift] +
        [(r["series_id"], "fred_series", r) for r in fred_drift] +
        [(f"ai_acceptance_{r['gate_type']}", "ai_acceptance", r) for r in acceptance_drift]
    )
    for metric_key, metric_kind, entry in entries:
        if entry.get("flag") != "drift":
            continue
        if db.get_open_drift_incident(metric_key):
            continue  # already a tracked, unresolved incident for this metric
        incident_id = db.create_drift_incident(
            metric_key, metric_kind, entry.get("psi"),
            entry.get("n_baseline"), entry.get("n_current"), detail=entry,
        )
        # Close the loop: a ratio or FRED-regime drift means the forecast
        # layer (FRED correlations + ensemble weights) may be stale for every
        # actively-tracked ticker, not just whichever one happened to be
        # open in the UI — see reoptimization_tool.py's docstring for why
        # this sweeps the tracked set rather than a computed "affected"
        # subset (neither drift signal is ticker-scoped). ai_acceptance
        # drift is a governance-process signal, not a forecasting one, so it
        # doesn't trigger this. Best-effort: a re-optimization failure must
        # never break the drift-check loop itself.
        reoptimize_summary = None
        if (
            incident_id
            and metric_kind in ("ratio", "fred_series")
            and os.environ.get("MODEL_HEALTH_AUTO_REOPTIMIZE", "true").lower() == "true"
        ):
            try:
                import reoptimization_tool
                reoptimize_summary = reoptimization_tool.run_reoptimization_sweep(
                    trigger_reason="drift_auto_reoptimize",
                    trigger_incident_id=incident_id,
                    max_tickers=int(os.environ.get("MODEL_HEALTH_REOPTIMIZE_MAX_TICKERS", "15")),
                )
                db.record_drift_reoptimization(incident_id, reoptimize_summary)
            except Exception as exc:
                logger.warning("Drift-triggered re-optimization failed for incident #%s: %s", incident_id, exc)

        if _HAS_MCP_GOVERNANCE:
            fields = [
                {"title": "Metric", "value": metric_key, "short": True},
                {"title": "PSI",    "value": f"{entry.get('psi'):.3f}" if entry.get("psi") is not None else "n/a", "short": True},
                {"title": "n (baseline / current)", "value": f"{entry.get('n_baseline')} / {entry.get('n_current')}", "short": True},
                {"title": "Incident", "value": f"#{incident_id}" if incident_id else "not persisted (db unavailable)", "short": True},
            ]
            if reoptimize_summary:
                fields.append({
                    "title": "Auto re-optimization",
                    "value": f"{reoptimize_summary['succeeded']}/{reoptimize_summary['tickers_attempted']} tickers succeeded",
                    "short": True,
                })
            mcp_governance._post_webhook_alert(
                f"\U0001f4c9 *Model Health drift detected* — `{metric_key}`",
                fields,
                color="#d97706",
            )
        alerted.append({"metric": metric_key, "incident_id": incident_id, **entry})
        logger.info("Model Health: drift incident #%s opened for %s", incident_id, metric_key)
    return alerted


async def model_health_drift_watch() -> None:
    """Infinite periodic loop. Started as an asyncio task in lifespan;
    cancelled gracefully on shutdown. Mirrors mcp_governance.start_polling()'s
    shape — errors are caught and logged, the loop never exits on its own."""
    logger.info("Model Health drift watch started (interval=%.0fs)", _MODEL_HEALTH_CHECK_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(_MODEL_HEALTH_CHECK_INTERVAL_S)
            alerted = await asyncio.to_thread(_check_model_health_drift_once)
            if alerted:
                logger.info("Model Health drift watch: %d new alert(s)", len(alerted))
            if deploy_env.IS_DEVELOPMENT:
                exc_alerted = await asyncio.to_thread(exceptions_endpoints.check_exception_drift_once)
                if exc_alerted:
                    logger.info("Exception Management drift watch: %d new alert(s)", len(exc_alerted))
        except asyncio.CancelledError:
            logger.info("Model Health drift watch stopped")
            break
        except Exception as exc:
            logger.warning("Model Health drift watch cycle error: %s", exc)


@app.get("/model-health/summary")
def get_model_health_summary(
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """
    Model Health screen data: forecast backtest accuracy trend across recent
    runs, cross-sectional PSI drift on financial ratios (has the population
    of companies being analyzed shifted from what the risk-scoring templates
    were calibrated against), PSI regime-shift drift on a small set of broad
    FRED macro indicators (empty when no FRED_API_KEY is configured), and PSI
    drift on the AI-suggestion acceptance rate per gate_type (MODEL_CARD.md
    "Recommended Next Steps" #2 — is the AI's advice being accepted/overridden
    at a materially different rate than its own recent history, not just
    whether the underlying financial population has shifted).
    On-demand only — computed live on each request, no background job.
    Same nav-permission-gated convention as Token Usage, not admin-only.
    """
    import drift_tool

    backtest_trend: list = []
    ratio_drift: list = []
    acceptance_drift: list = []
    baseline_resets: dict = {}
    if db.is_available():
        baseline_resets = db.get_baseline_resets()
        backtest_trend = db.get_backtest_trend()
        ratio_drift = drift_tool.compute_ratio_drift(db.get_financial_ratios_history(), baseline_resets=baseline_resets)
        acceptance_drift = drift_tool.compute_ai_acceptance_drift(db.get_ai_acceptance_history(), baseline_resets=baseline_resets)

    fred_api_key = os.environ.get("FRED_API_KEY", "")
    fred_drift = drift_tool.compute_fred_regime_drift(fred_api_key, baseline_resets=baseline_resets)

    return {
        "backtest_trend": backtest_trend,
        "ratio_drift": ratio_drift,
        "fred_drift": fred_drift,
        "acceptance_drift": acceptance_drift,
        "fred_configured": bool(fred_api_key),
        "baseline_resets": baseline_resets,
    }


@app.get("/model-health/drift-incidents")
def list_model_health_drift_incidents(
    status: str = Query(None, description="Filter: open | acknowledged | resolved"),
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """The governed process trail behind Model Health drift alerts — each
    detected drift is a tracked incident here, not just a webhook that fired
    and was forgotten."""
    rows = db.list_drift_incidents(status=status) if db.is_available() else []
    return {"rows": rows, "count": len(rows), "open_count": sum(1 for r in rows if r["status"] != "resolved")}


@app.put("/model-health/drift-incidents/{incident_id}")
def update_model_health_drift_incident(
    incident_id: int,
    body: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """Assign an owner, change status (open/acknowledged/resolved), add
    notes, or log a structured correction_action on a drift incident. Any
    field omitted from the body is left unchanged.

    correction_action must be one of db._VALID_CORRECTION_ACTIONS
    (rebaselined | recalibrated | escalated_for_review | false_positive |
    no_action_needed) — this is the "what was actually done" record
    MODEL_CARD.md's "Recommended Next Steps" asked for, distinct from
    `status`. corrected_by defaults to the caller's display name."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    ok = db.update_drift_incident(
        incident_id, status=body.get("status"), owner=body.get("owner"), notes=body.get("notes"),
        correction_action=body.get("correction_action"),
        corrected_by=body.get("correction_action") and (current_user.get("display_name") or current_user.get("username")),
    )
    return {"ok": ok, "id": incident_id}


@app.post("/model-health/baseline-reset")
def set_model_health_baseline_reset(
    body: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """
    Mark 'now' as the new baseline floor for a metric (metric_key: the same
    ratio field / FRED series_id / 'ai_acceptance_<gate_type>' used by drift
    incidents). Future drift computations exclude data before this point —
    the "this is the new normal, stop comparing against pre-shift data"
    correction option (MODEL_CARD.md "Recommended Next Steps"). Only
    meaningful for 'ratio' and 'fred_series' metric kinds; ai_acceptance
    drift already uses a rolling window, so a reset there is a no-op in
    practice (nothing to exclude that the window doesn't already drop).
    """
    metric_key = body.get("metric_key")
    if not metric_key:
        raise HTTPException(status_code=422, detail="metric_key is required")
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    ok = db.set_baseline_reset(
        metric_key,
        reset_by=current_user.get("display_name") or current_user.get("username"),
        reason=body.get("reason"),
    )
    return {"ok": ok, "metric_key": metric_key}


@app.delete("/model-health/baseline-reset/{metric_key}")
def clear_model_health_baseline_reset(
    metric_key: str,
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    ok = db.clear_baseline_reset(metric_key)
    return {"ok": ok, "metric_key": metric_key}


@app.post("/model-health/run-review")
def run_model_health_review(
    body: Dict[str, Any] = Body(default={}),
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """User-initiated version of the same sweep model_health_drift_watch runs
    automatically on drift — re-derives FRED correlations and re-optimizes
    ensemble weights (walk-forward backtest MAPE/RMSE/R²) for every
    actively-tracked ticker/company, without waiting for the next drift flag
    or the 6h background check. Synchronous — bounded by max_tickers, same
    cost profile as the existing synchronous /predictive/full-analysis call.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    import reoptimization_tool
    max_tickers = body.get("max_tickers")
    summary = reoptimization_tool.run_reoptimization_sweep(
        trigger_reason="manual_review",
        max_tickers=int(max_tickers) if max_tickers else None,
    )
    logger.info(
        "Model Health: manual review run by %s — %d/%d tickers succeeded",
        current_user.get("username") or current_user.get("display_name") or "unknown",
        summary["succeeded"], summary["tickers_attempted"],
    )
    return summary


@app.get("/observability/command-center")
def get_command_center(
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """
    Continuous Monitoring command center: one call answering "what's being
    watched right now, what fired recently, what's stale." Composes existing
    observability building blocks (registered systems + poll connectors with
    last-seen timestamps, pending holds, coverage blind spots) with two things
    nothing else already provides — a 24h activity window, and a PaC coverage
    + Model Health drift summary — rather than duplicating any of their logic.
    Added directly here (not mcp_governance.py, whose router this augments)
    since this file already imports mcp_governance, pac_endpoints, and the
    drift_tool compute calls model-health uses.
    """
    if not db.is_available():
        return {
            "systems": [], "connectors": [], "pending_holds": 0, "coverage_blind_spots": 0,
            "last_24h": {"adjudicated": 0, "escalated": 0, "pac_violations": 0},
            "pac_processes": [], "model_health_drift": False,
            "note": "Database not configured",
        }

    systems = mcp_governance._fetch_systems() if _HAS_MCP_GOVERNANCE else []
    connectors = db.list_poll_connectors()
    pending_holds = len(mcp_governance._fetch_pending_holds()) if _HAS_MCP_GOVERNANCE else 0
    coverage_rows = mcp_governance._fetch_coverage() if _HAS_MCP_GOVERNANCE else []
    coverage_blind_spots = sum(1 for r in coverage_rows if (r.get("flag_rate") or 0) == 0)
    last_24h = db.get_observability_24h_counts()
    hourly = db.get_observability_hourly_series()

    pac_processes = []
    for proc in db.list_pac_processes():
        mod = db.get_latest_pac_module(proc["id"])
        pac_processes.append({
            "id": proc["id"],
            "label": proc["label"],
            "source": proc["source"],
            "source_format": mod.get("source_format") if mod else "default",
            "rule_coverage": pac_endpoints._rule_coverage(mod["rego_content"]) if mod else None,
        })

    import drift_tool
    baseline_resets = db.get_baseline_resets()
    ratio_drift = drift_tool.compute_ratio_drift(db.get_financial_ratios_history(), baseline_resets=baseline_resets)
    fred_drift = drift_tool.compute_fred_regime_drift(os.environ.get("FRED_API_KEY", ""), baseline_resets=baseline_resets)
    acceptance_drift = drift_tool.compute_ai_acceptance_drift(db.get_ai_acceptance_history(), baseline_resets=baseline_resets)
    model_health_drift = any(r.get("flag") == "drift" for r in ratio_drift + fred_drift + acceptance_drift)

    return {
        "systems": systems,
        "connectors": connectors,
        "pending_holds": pending_holds,
        "coverage_blind_spots": coverage_blind_spots,
        "last_24h": last_24h,
        "hourly": hourly,
        "pac_processes": pac_processes,
        "model_health_drift": model_health_drift,
    }


@app.get("/observability/domain-summary")
def get_domain_summary(
    days: int = 30,
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """
    Adjudicated events grouped by Core Domain (Identity & Access Management,
    Cyber Security & Data Protection, ...) over the trailing `days` days —
    the data source for Continuous Monitoring's domain-grouped views.

    Scoped to events that actually carry a policy_violations entry:
    pol_domain_mappings resolves a domain from the violation's rule_id/
    control_id, and a CLEAR event with nothing to complain about carries no
    such signal — there is no other link from a raw adjudication row to a
    Core Domain today (that's Blocker 1: controls_library.pac_control_id is
    0% populated, and risk_scores.assigned_domain only 15%). So this answers
    "where are violations concentrating," not "where is all activity" — the
    latter needs that link built first. `unresolved_violations` counts rows
    that DID carry a violation but whose rule_id/control_id isn't in either
    mapping table yet (an honest gap, not folded into any domain's total).
    """
    if not db.is_available():
        return {"domains": [], "unresolved_violations": 0, "total_violations": 0,
                "window_days": days, "note": "Database not configured"}

    events = db.get_recent_adjudications_for_domain_summary(days)
    ctrl_to_process = {c["control_id"]: c["process"] for c in db.list_controls() if c.get("process")}

    by_domain: Dict[str, Dict[str, Any]] = {}
    unresolved = 0
    total_violations = 0

    for ev in events:
        violations = ev["policy_violations"]
        if not violations:
            continue
        total_violations += 1
        domain = pol_domain_mappings.domain_for_violations(violations, ctrl_to_process)
        if domain is None:
            unresolved += 1
            continue
        bucket = by_domain.setdefault(domain, {
            "domain": domain, "total": 0, "escalated": 0, "monitor": 0, "clear": 0, "daily": {},
        })
        bucket["total"] += 1
        verdict_key = {"ESCALATE": "escalated", "MONITOR": "monitor", "CLEAR": "clear"}.get(ev["final_verdict"])
        if verdict_key:
            bucket[verdict_key] += 1
        day_key = ev["adjudicated_at"].date().isoformat() if ev["adjudicated_at"] else "unknown"
        day = bucket["daily"].setdefault(day_key, {"date": day_key, "total": 0, "escalated": 0})
        day["total"] += 1
        if ev["final_verdict"] == "ESCALATE":
            day["escalated"] += 1

    domains_out = []
    for bucket in by_domain.values():
        bucket["daily"] = sorted(bucket["daily"].values(), key=lambda d: d["date"])
        domains_out.append(bucket)
    domains_out.sort(key=lambda d: d["total"], reverse=True)

    return {
        "domains": domains_out,
        "unresolved_violations": unresolved,
        "total_violations": total_violations,
        "window_days": days,
    }


@app.get("/observability/events")
def get_observability_events(
    days: int = 30,
    limit: int = 5000,
    current_user: Dict[str, Any] = Depends(auth_endpoints.get_current_user),
):
    """
    One row per event over the trailing `days` days — both adjudicated
    (reviewed) events and, since only flagged rows ever reach adjudication
    (mcp_governance._fetch_unprocessed_system's risk_flags filter), every
    other captured-but-never-reviewed event too. Each row carries its real
    timestamp and resolved Core Domain (when resolvable — see domain
    resolution note below) — the flat, per-event feed Continuous Monitoring's
    Playback/Motion views (scrub, speed, replay, arrival animation, recency
    trail) need and /observability/domain-summary and /pac/control-flow-map
    deliberately don't provide, since both pre-aggregate into a static
    summary/graph rather than exposing individual events with real
    timestamps.

    An unreviewed row's verdict is the literal string "NOT_REVIEWED", never
    null and never "CLEAR" — it was never scored, which is a different fact
    than being scored-and-clear, and a consumer that conflated the two would
    quietly overstate how much traffic was actually looked at. This is what
    lets a chart show genuine transaction scale (every row) side by side with
    the escalated/reviewed subset (only rows with a real verdict), instead of
    the two numbers always being identical.

    domain is resolved the same way get_domain_summary does (pol_domain_
    mappings, via each event's policy_violations) and carries the same
    honest-gap behavior: null when the event's rule_id/control_id isn't
    mapped yet, or when the event has no policy_violations to key off at all
    (a CLEAR or NOT_REVIEWED event, most of the time) — never a guess. A
    frontend grouping by domain should treat a null domain as its own
    explicit "unclassified" bucket, not silently drop the event.

    case_id/process_step are null for almost every row — an ad-hoc MCP tool
    call has no "case" concept — and are populated today only by
    generate_o2c_p2p_synthetic_log.py's/synthetic_transaction_tool.py's
    linked O2C/P2P/Inventory Cycle lifecycles. They exist so a consumer can
    build a REAL directly-follows graph ("step A immediately preceded step B
    within the same tracked transaction") for rows that have them, as
    opposed to the categorical Domain/Tier/Verdict/Rule breakdown every
    adjudicated row supports regardless of case membership. Unreviewed rows
    carry them too (read straight off raw_payload, set at ingestion —
    see db.get_recent_unreviewed_system_events), so a case's full lifecycle
    shows up in the graph even for the steps that were never flagged for
    review — previously the graph only ever showed whichever steps happened
    to get flagged, undercounting every process's actual volume.
    """
    if not db.is_available():
        return {"events": [], "window_days": days, "note": "Database not configured"}

    raw = db.get_recent_adjudications_for_domain_summary(days=days, limit=limit)
    unreviewed = db.get_recent_unreviewed_system_events(days=days, limit=limit)
    ctrl_to_process = {c["control_id"]: c["process"] for c in db.list_controls() if c.get("process")}

    events = [
        {
            "id": ev["id"],
            "adjudicated_at": ev["adjudicated_at"].isoformat() if ev["adjudicated_at"] else None,
            "verdict": ev["final_verdict"],
            "risk_tier": ev["risk_tier"],
            "source_system": ev["source_system"],
            "target_tool": ev["target_tool"],
            "server_name": ev["server_name"],
            "requires_human_review": ev["requires_human_review"],
            "policy_violations": ev["policy_violations"],
            "domain": pol_domain_mappings.domain_for_violations(ev["policy_violations"], ctrl_to_process),
            "sub_domain": pol_domain_mappings.subdomain_for_violations(ev["policy_violations"], ctrl_to_process),
            "case_id": ev.get("case_id"),
            "process_step": ev.get("process_step"),
        }
        for ev in raw
    ] + [
        {
            "id": ev["id"],
            "adjudicated_at": ev["adjudicated_at"].isoformat() if ev["adjudicated_at"] else None,
            "verdict": "NOT_REVIEWED",
            "risk_tier": None,
            "source_system": ev["source_system"],
            "target_tool": ev["target_tool"],
            "server_name": ev["server_name"],
            "requires_human_review": False,
            "policy_violations": [],
            # No policy_violations exist for a row that was never adjudicated —
            # process_step (captured at ingestion regardless of review status)
            # is the only signal available; see pol_domain_mappings.
            # domain_for_process_step's docstring for why this used to be a
            # hardcoded None even though ~95% of all volume is this branch.
            "domain": pol_domain_mappings.domain_for_process_step(ev.get("process_step")),
            "sub_domain": None,  # would need a case_id join to a sibling step's payload — not available here
            "case_id": ev.get("case_id"),
            "process_step": ev.get("process_step"),
        }
        for ev in unreviewed
    ]
    events.sort(key=lambda e: e["adjudicated_at"] or "")
    events = events[-limit:]

    return {"events": events, "count": len(events), "window_days": days}


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dendrai MCP API Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    print(f"Dendrai MCP API  -> http://{args.host}:{args.port}")
    print(f"  Docs           -> http://{args.host}:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port)
