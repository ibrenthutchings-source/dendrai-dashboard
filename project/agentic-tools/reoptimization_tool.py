#!/usr/bin/env python3
"""
Drift -> Re-optimization Loop — Dendrai Intelligenza

Closes the loop between Model Health drift detection and the forecasting
layer: when FRED regime drift or financial-ratio drift is flagged (see
drift_tool.py / api_server.py's model_health_drift_watch), this module
re-runs the forecast/backtest layer — which re-derives FRED correlations
(get_macro_leading_indicators re-tests the full series catalog fresh on every
call, no caching) and re-optimizes ensemble weights via inverse-MAPE
(walk_forward_backtest) — for every actively-tracked ticker/company,
including private companies.

Drift signals themselves are not ticker-scoped (compute_ratio_drift pools
across the whole population; compute_fred_regime_drift has no ticker
dimension at all — see drift_tool.py), so there is no "affected tickers"
list to derive. Re-optimization instead sweeps the actively-tracked set
(a completed run in the last 90 days), oldest-stale-first, capped per call.

Two callers:
  - api_server.py's _check_model_health_drift_once, automatically, on a NEW
    drift incident (trigger_reason="drift_auto_reoptimize").
  - POST /model-health/run-review, on demand (trigger_reason="manual_review").
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import db
import predictive_analytics_tool as pat

logger = logging.getLogger(__name__)

DEFAULT_MAX_TICKERS = int(os.environ.get("MODEL_HEALTH_REOPTIMIZE_MAX_TICKERS", "15"))
DEFAULT_ACTIVE_WINDOW_DAYS = 90


def reoptimize_ticker(
    ticker: str,
    forecast_metric: str = None,
    forecast_horizon: int = None,
    trigger_reason: str = "drift_auto_reoptimize",
    trigger_incident_id: int = None,
    fred_api_key: str = "",
) -> dict:
    """Re-run the forecast/backtest layer for one ticker/company (public or
    private — build_company_xbrl already branches on PVT-* tickers, no
    special-casing needed here). Persists as a NEW risk_loop_run row (matches
    create_risk_loop_run's existing never-overwrite convention, so MAPE/RMSE/R2
    history stays intact and comparable run-over-run). Never raises — returns
    {ticker, success: False, error} on any failure so a sweep over many
    tickers can isolate one bad ticker without aborting the rest."""
    prior = db.get_latest_run_meta(ticker) or {}
    forecast_metric  = forecast_metric  or prior.get("forecast_metric")  or "Revenue"
    forecast_horizon = forecast_horizon or prior.get("forecast_horizon") or 4

    try:
        xbrl, company_meta = pat.build_company_xbrl(ticker)
    except Exception as e:
        return {"ticker": ticker, "success": False, "error": f"company lookup failed: {e}"}

    industry   = prior.get("industry") or pat.detect_industry(company_meta.get("sic", "") or "")
    company_id = company_meta.get("company_id")

    # Re-derive FRED correlations fresh — this IS the "re-evaluate the
    # macro-economic indicators" step; get_macro_leading_indicators re-tests
    # the full series catalog against the correlation threshold on every
    # call, no caching/pinning, so a post-drift call naturally picks up
    # whichever indicators currently correlate.
    macro_info = None
    try:
        macro_result = pat.get_macro_leading_indicators(
            ticker, industry, api_key=fred_api_key or os.environ.get("FRED_API_KEY", ""),
        )
        if macro_result.get("source") == "live_fred_analysis":
            macro_info = macro_result.get("result")
    except Exception as exc:
        logger.info("Re-optimization %s: FRED indicators unavailable (%s) — forecasting without macro features", ticker, exc)

    try:
        fb = pat.run_forecast_backtest(xbrl, macro_info, forecast_metric, forecast_horizon, company_id)
    except Exception as e:
        return {"ticker": ticker, "success": False, "error": f"forecast/backtest failed: {e}"}

    if not db.is_available():
        return {"ticker": ticker, "success": False, "error": "database not configured"}

    if not company_id:
        company_id = db.upsert_company({
            "ticker": ticker,
            "company_name": company_meta.get("company_name", ticker),
            "cik": company_meta.get("cik") or "",
            "sic": company_meta.get("sic", ""),
            "sic_description": company_meta.get("sic_description", ""),
        })
        if not company_id:
            return {"ticker": ticker, "success": False, "error": "failed to resolve company record"}

    run_id = db.create_risk_loop_run(company_id, {
        "ticker": ticker, "industry": industry, "data_mode": "reoptimize",
        "forecast_metric": forecast_metric, "forecast_horizon": forecast_horizon,
        "trigger_reason": trigger_reason, "trigger_incident_id": trigger_incident_id,
    })
    if not run_id:
        return {"ticker": ticker, "success": False, "error": "failed to create risk_loop_run"}

    if fb.get("forecast"):
        db.save_forecasts(run_id, forecast_metric, fb["forecast"])
    if fb.get("backtest"):
        db.save_backtest_metrics(run_id, fb["backtest"])
    db.complete_risk_loop_run(run_id)

    model_metrics = (fb.get("backtest") or {}).get("model_metrics") or {}
    mape_by_model = {model: m.get("mape") for model, m in model_metrics.items()}

    return {
        "ticker": ticker,
        "success": True,
        "run_id": run_id,
        "is_private": bool(company_meta.get("is_private")),
        "mape_by_model": mape_by_model,
    }


def run_reoptimization_sweep(
    trigger_reason: str = "manual_review",
    trigger_incident_id: int = None,
    max_tickers: int = None,
) -> dict:
    """Sweep the actively-tracked ticker set, re-optimizing each independently.
    Mirrors connector_poller.py's due-item dispatch pattern — one ticker's
    EDGAR/FRED failure must not abort the sweep. Returns
    {trigger_reason, tickers_attempted, succeeded, failed, results: [...]}."""
    max_tickers = max_tickers or DEFAULT_MAX_TICKERS
    tickers = db.list_active_tickers(days=DEFAULT_ACTIVE_WINDOW_DAYS, limit=max_tickers)

    results = []
    for ticker in tickers:
        try:
            results.append(reoptimize_ticker(
                ticker, trigger_reason=trigger_reason, trigger_incident_id=trigger_incident_id,
            ))
        except Exception as exc:
            logger.warning("Re-optimization sweep: unhandled error for %s: %s", ticker, exc)
            results.append({"ticker": ticker, "success": False, "error": str(exc)})

    succeeded = sum(1 for r in results if r.get("success"))
    summary = {
        "trigger_reason": trigger_reason,
        "tickers_attempted": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }
    logger.info(
        "Re-optimization sweep (%s): %d/%d tickers succeeded",
        trigger_reason, succeeded, len(results),
    )
    return summary
