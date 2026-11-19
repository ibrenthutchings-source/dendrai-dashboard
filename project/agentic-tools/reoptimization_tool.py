#!/usr/bin/env python3
"""
Drift -> Re-optimization Loop — Dendrai Intelligenza

Closes the loop between Model Health drift detection and the forecasting
layer: when FRED regime drift or financial-ratio drift is flagged (see
drift_tool.py / api_server.py's model_health_drift_watch), this module
re-runs the forecast/backtest layer — which re-derives FRED correlations
(get_macro_leading_indicators re-tests the full series catalog fresh on every
call, no caching) and re-optimizes ensemble weights via inverse-MAPE
(walk_forward_backtest) — for the target company (private included, if
that's what's configured).

Drift signals themselves are not ticker-scoped (compute_ratio_drift pools
across the whole population; compute_fred_regime_drift has no ticker
dimension at all — see drift_tool.py), so there is no "affected tickers"
list to derive from the drift signal itself either way. Re-optimization
targets db.get_target_ticker() — Mission Control's currently-configured
entity (app.jsx's cfg.ticker) — and nothing else: SIC peer/benchmark
tickers (sic_peers) that happen to have their own completed risk_loop_runs
rows from peer-comps backtests or demo/seed activity are never swept just
because they're sitting in the same table. A prior version of this module
swept every ticker with *any* completed run in the last 90 days
(db.list_active_tickers) — real signal diluted by peer noise, and (before
risk_loop_runs.data_mode's VARCHAR(16) widening) an outright failure for
every one of them.

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

# "Selected" mirrors get_macro_leading_indicators' own min_r=0.60 — the bar an
# indicator actually has to clear to feed run_forecast_backtest's FRED feature
# matrix. 0.85 is a stricter *reporting* tier only (not a second selection
# pass) so a review result can show how many indicators were reviewed at
# review-grade strength, without silently dropping indicators between 0.60
# and 0.85 that are still genuinely used in forecasting.
FRED_STRONG_CORRELATION_THRESHOLD = 0.85


def _summarize_fred_diagnostics(macro_result: dict) -> dict:
    """Condense get_macro_leading_indicators' raw output into what a "Run
    review" result needs to show: how many of the 30 FRED series were
    actually fetched, how many indicator/metric pairs cleared the selection
    threshold (what's actually in play for forecasting) vs. the stricter
    0.85 reporting tier, and which specific indicators cleared 0.85."""
    if not macro_result:
        return {"source": "not_run"}
    source = macro_result.get("source")
    if source != "live_fred_analysis":
        # No FRED_API_KEY, or the live call failed — get_macro_leading_indicators
        # already fell back to static industry benchmarks in this case.
        return {"source": source or "unavailable", "note": macro_result.get("note")}

    result = macro_result.get("result") or {}
    params = result.get("parameters", {})
    correlation_results = result.get("correlation_results", {}) or {}
    selected = [
        {**hit, "metric": metric}
        for metric, hits in correlation_results.items()
        for hit in hits
    ]
    strong = sorted(
        (h for h in selected if abs(h.get("pearson_r") or 0) >= FRED_STRONG_CORRELATION_THRESHOLD),
        key=lambda h: abs(h.get("pearson_r") or 0), reverse=True,
    )
    return {
        "source": "live_fred_analysis",
        "fred_series_fetched": params.get("fred_series_fetched"),
        "fred_series_attempted": params.get("fred_series_attempted"),
        "financial_metrics_analyzed": params.get("financial_metrics_analyzed"),
        "selection_threshold": params.get("min_correlation_threshold"),
        "indicators_selected": len(selected),
        "strong_threshold": FRED_STRONG_CORRELATION_THRESHOLD,
        "indicators_strong": len(strong),
        "strong_indicators": [
            {
                "series_id": h.get("series_id"), "name": h.get("name"), "metric": h.get("metric"),
                "pearson_r": h.get("pearson_r"), "lag_quarters": h.get("optimal_lag_quarters"),
            }
            for h in strong[:10]
        ],
    }


def _summarize_backtest_diagnostics(backtest: dict) -> dict:
    """Which models were actually attempted and backtested, and their
    RMSE/MAPE/R² — the "were additional models attempted and backtested"
    question a bare mape_by_model dict couldn't answer."""
    model_metrics = (backtest or {}).get("model_metrics") or {}
    if not model_metrics:
        note = (backtest or {}).get("error") or "insufficient history for backtesting (<10 quarterly observations)"
        return {"models_attempted": [], "note": note}
    weights = backtest.get("calibrated_weights") or {}
    return {
        "models_attempted": list(model_metrics.keys()),
        "metrics_by_model": {
            model: {
                "mape": m.get("mape"), "rmse": m.get("rmse"), "r_squared": m.get("r_squared"),
                "n_backtest_steps": m.get("n_backtest_steps"),
            }
            for model, m in model_metrics.items()
        },
        "ensemble_weights": weights,
    }


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
    # whichever indicators currently correlate. macro_result is kept (not
    # just macro_info) so _summarize_fred_diagnostics can report on it even
    # when the call fails or falls back to static benchmarks.
    macro_info = None
    macro_result = None
    try:
        macro_result = pat.get_macro_leading_indicators(
            ticker, industry, api_key=fred_api_key or os.environ.get("FRED_API_KEY", ""),
        )
        if macro_result.get("source") == "live_fred_analysis":
            macro_info = macro_result.get("result")
    except Exception as exc:
        logger.info("Re-optimization %s: FRED indicators unavailable (%s) — forecasting without macro features", ticker, exc)
        macro_result = {"source": "unavailable", "note": str(exc)}

    try:
        fb = pat.run_forecast_backtest(xbrl, macro_info, forecast_metric, forecast_horizon, company_id)
    except Exception as e:
        return {"ticker": ticker, "success": False, "error": f"forecast/backtest failed: {e}"}

    fred_diagnostics = _summarize_fred_diagnostics(macro_result)
    backtest_diagnostics = _summarize_backtest_diagnostics(fb.get("backtest"))

    if not db.is_available():
        return {
            "ticker": ticker, "success": False, "error": "database not configured",
            "fred_diagnostics": fred_diagnostics, "backtest_diagnostics": backtest_diagnostics,
        }

    if not company_id:
        company_id = db.upsert_company({
            "ticker": ticker,
            "company_name": company_meta.get("company_name", ticker),
            "cik": company_meta.get("cik") or "",
            "sic": company_meta.get("sic", ""),
            "sic_description": company_meta.get("sic_description", ""),
        })
        if not company_id:
            return {
                "ticker": ticker, "success": False, "error": "failed to resolve company record",
                "fred_diagnostics": fred_diagnostics, "backtest_diagnostics": backtest_diagnostics,
            }

    # Capture the real DB error rather than letting db.py's _run() swallow it
    # into a bare None — a schema mismatch (e.g. a pending migration that
    # hasn't been applied yet because the server hasn't restarted since it
    # was added) and "nothing to persist" would otherwise look identical.
    errors = []
    run_id = db.create_risk_loop_run(company_id, {
        "ticker": ticker, "industry": industry, "data_mode": "reoptimize",
        "forecast_metric": forecast_metric, "forecast_horizon": forecast_horizon,
        "trigger_reason": trigger_reason, "trigger_incident_id": trigger_incident_id,
    }, on_error=lambda e: errors.append(f"create_risk_loop_run: {e}"))
    if not run_id:
        return {
            "ticker": ticker, "success": False,
            "error": errors[0] if errors else "failed to create risk_loop_run (unknown reason — check server logs)",
            "fred_diagnostics": fred_diagnostics, "backtest_diagnostics": backtest_diagnostics,
        }

    if fb.get("forecast"):
        db.save_forecasts(run_id, forecast_metric, fb["forecast"],
                           on_error=lambda e: errors.append(f"save_forecasts: {e}"))
    if fb.get("backtest"):
        db.save_backtest_metrics(run_id, fb["backtest"],
                                  on_error=lambda e: errors.append(f"save_backtest_metrics: {e}"))
    db.complete_risk_loop_run(run_id, on_error=lambda e: errors.append(f"complete_risk_loop_run: {e}"))

    model_metrics = (fb.get("backtest") or {}).get("model_metrics") or {}
    mape_by_model = {model: m.get("mape") for model, m in model_metrics.items()}

    # run_id exists even if a later persistence step failed — still False,
    # not a partial/ambiguous "success", so failures stay readily apparent
    # rather than reading as a clean pass with quietly-missing data.
    return {
        "ticker": ticker,
        "success": not errors,
        "run_id": run_id,
        "is_private": bool(company_meta.get("is_private")),
        "mape_by_model": mape_by_model,
        "fred_diagnostics": fred_diagnostics,
        "backtest_diagnostics": backtest_diagnostics,
        **({"error": "; ".join(errors)} if errors else {}),
    }


def run_reoptimization_sweep(
    trigger_reason: str = "manual_review",
    trigger_incident_id: int = None,
    max_tickers: int = None,
) -> dict:
    """Re-optimize the target company — Mission Control's currently-
    configured entity (db.get_target_ticker()), never a swept set of
    tickers. `max_tickers` is accepted (not removed — api_server.py's two
    callers both pass it) but has no effect now that there is exactly one
    possible target; kept only so neither call site needs to change.
    Returns {trigger_reason, tickers_attempted, succeeded, failed,
    results: [...]} — the same shape as the old multi-ticker sweep, so
    Model Health's UI (reoptimize_summary.succeeded/tickers_attempted)
    doesn't need to change either, just now with tickers_attempted always
    0 or 1."""
    target = db.get_target_ticker()
    if not target:
        logger.info(
            "Re-optimization sweep (%s): no Mission Control target company configured (pipeline_config.cfg.ticker) — nothing to do",
            trigger_reason,
        )
        return {"trigger_reason": trigger_reason, "tickers_attempted": 0, "succeeded": 0, "failed": 0, "results": []}
    tickers = [target]

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
