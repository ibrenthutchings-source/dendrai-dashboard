#!/usr/bin/env python3
"""
FAIR (Factor Analysis of Information Risk) quantification engine — Risk
Quantification.

The gap this closes: every dollar figure in the platform before this module
was either a hardcoded label (api_server.py's _CEM_TEMPLATES_DEFAULT
`exposure` strings — "$12-18M", "Regulatory", "Material misstatement", all
in one VARCHAR column that can't be summed) or a single point estimate
derived from real data but carrying no uncertainty (sox_scoping_tool.py's
estimated_exposure is an account BALANCE — what's at stake if a control
fails completely, not an expected loss; risk-engine.js's
allocateRiskDollarExposure() proportionally splits a Bear-scenario total
across risks, again a point value). Nothing in the platform priced *how
likely* a loss is, only *how big* it would be if it happened.

This module adds the missing factor — frequency — and combines it with
magnitude via Monte Carlo simulation, the standard FAIR method (Freund &
Jones, "Measuring and Managing Information Risk"): for each of N simulated
years, draw a Poisson-distributed count of loss events from the Threat
Event Frequency (TEF), then a modified-PERT-distributed magnitude per event
from the three-point Loss Magnitude (LM) estimate, sum to that year's total
loss. N years of totals form the Annual Loss Expectancy (ALE) distribution —
a mean (ALE), percentiles, and a loss-exceedance curve — instead of a single
number with no confidence interval.

Deliberately pure — no DB, no HTTP. Every input this module needs (a
control's real fire count from observability.adjudicated_tool_calls, a SOX
process's estimated_exposure, a risk's allocated dollarExposureM) is fetched
by the caller (fair_endpoints.py) via db.py and passed in; this module never
invents a number it wasn't given, and every magnitude/frequency result
carries an explicit `_source` tag naming exactly where it came from — same
discipline framework_mappings.py and risk-engine.js's
allocateRiskDollarExposure() already established, just extended to a
distribution instead of a point value. This separation (db.py owns I/O,
this module owns computation) mirrors db._build_control_flow_map /
db.get_control_flow_map's split.

    from fair_tool import quantify, control_roi
    result = quantify(fire_count_window=4, window_days=30,
                       sox_estimated_exposure=12_500_000, simulations=5000)
    # result["ale"], result["p90"], result["exceedance_curve"], ...
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Optional

# ── Bounds ───────────────────────────────────────────────────────────────────

MIN_SIMULATIONS = 500
MAX_SIMULATIONS = 20_000
DEFAULT_SIMULATIONS = 5_000

# ── Threat Event Frequency (TEF) ─────────────────────────────────────────────


def resolve_tef(fire_count_window: int, window_days: int, manual_tef: Optional[float] = None) -> tuple[float, str]:
    """
    Annualized threat-event frequency (events/year) and where it came from.

    manual_tef always wins when supplied (a reviewer's own estimate, e.g. for
    a control with no fire history yet — an empty history is "hasn't fired
    recently," not "will never fire," so forcing a manual estimate rather
    than silently defaulting to 0 events/year matters here). Otherwise the
    real observed fire count over the window (db.get_control_fire_stats) is
    annualized by simple proportion — the same "recent window, projected to
    a year" approach dora_metrics.py and connector_hygiene.py already use
    elsewhere in this codebase, not a new statistical assumption.
    """
    if manual_tef is not None:
        return float(manual_tef), "manual"
    if window_days <= 0:
        return 0.0, "empirical"
    annualized = (max(0, fire_count_window) / window_days) * 365.0
    return annualized, "empirical"


# ── Loss Magnitude (LM) ──────────────────────────────────────────────────────

# $ millions — PERT (min, most-likely, max) bands, keyed by CEM severity.
# Only reached when NEITHER a SOX process balance NOR an allocated risk
# dollar exposure is available for the event being quantified — an honest,
# clearly-labeled placeholder (magnitude_source="cem_severity_default"), not
# presented as derived from filings the way the other two sources are.
CEM_SEVERITY_BANDS = {
    "P1": (5.0, 15.0, 30.0),
    "P2": (1.0, 3.0, 8.0),
    "P3": (0.1, 0.5, 2.0),
}


def resolve_magnitude(
    *,
    manual: Optional[tuple[float, float, float]] = None,
    sox_estimated_exposure: Optional[float] = None,
    risk_dollar_exposure_m: Optional[float] = None,
    cem_severity: Optional[str] = None,
) -> tuple[float, float, float, str]:
    """
    Resolve (loss_min, loss_likely, loss_max, source), first available
    source wins, in priority order:

        1. manual                 — a reviewer-entered three-point estimate
        2. sox_estimated_exposure — sox_scoping_tool.py's derived account
                                     balance for the linked SOX process
        3. risk_dollar_exposure_m — risk-engine.js's allocateRiskDollarExposure()
                                     output for the linked risk register entry
        4. cem_severity            — CEM_SEVERITY_BANDS default for the
                                     event's P1/P2/P3 severity

    A single point value (SOX exposure, risk exposure) is spread into a PERT
    triple by treating it as "most likely" and bracketing -40%/+100% (min =
    0.6x, max = 2x): that point is what's AT STAKE if the control fails
    completely (an account balance, an allocated revenue share), not itself
    a loss distribution — the spread models uncertainty in HOW MUCH of that
    exposure actually converts to a loss, not uncertainty in the exposure
    figure itself, which came from real filed financials and isn't being
    second-guessed here.
    """
    if manual is not None:
        lo, likely, hi = manual
        return float(lo), float(likely), float(hi), "manual"
    if sox_estimated_exposure is not None:
        likely = float(sox_estimated_exposure)
        return likely * 0.6, likely, likely * 2.0, "sox_exposure"
    if risk_dollar_exposure_m is not None:
        likely = float(risk_dollar_exposure_m)
        return likely * 0.6, likely, likely * 2.0, "risk_dollar_exposure"
    band = CEM_SEVERITY_BANDS.get((cem_severity or "").upper(), CEM_SEVERITY_BANDS["P2"])
    return band[0], band[1], band[2], "cem_severity_default"


# ── Monte Carlo primitives ───────────────────────────────────────────────────


def _sample_pert(rng: random.Random, lo: float, likely: float, hi: float, lam: float = 4.0) -> float:
    """Modified-PERT sample via a reparameterized Beta distribution — the
    standard way to turn an expert-elicited three-point (min/likely/max)
    estimate into a distribution (Vose, "Risk Analysis"). lam=4 is the
    conventional PERT shape parameter; higher clusters samples tighter
    around `likely`."""
    if hi <= lo:
        return likely
    mid = min(max(likely, lo), hi)  # guard a degenerate/inverted estimate
    alpha = 1 + lam * (mid - lo) / (hi - lo)
    beta = 1 + lam * (hi - mid) / (hi - lo)
    return lo + rng.betavariate(alpha, beta) * (hi - lo)


def _sample_poisson(rng: random.Random, lam: float) -> int:
    """Knuth's algorithm — the stdlib `random` module has no Poisson
    variate. lam is events/year; each simulated year draws its own count."""
    if lam <= 0:
        return 0
    if lam > 30:
        # Normal approximation avoids Knuth's O(lambda) loop for a
        # pathologically high empirical TEF — FAIR TEFs in practice are
        # almost always single digits per year, but this keeps the
        # simulation from hanging on bad input rather than assuming it away.
        return max(0, round(rng.gauss(lam, lam ** 0.5)))
    threshold = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= threshold:
            return k - 1


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def run_simulation(
    tef_mean: float,
    loss_min: float,
    loss_likely: float,
    loss_max: float,
    simulations: int = DEFAULT_SIMULATIONS,
    seed: Optional[int] = None,
) -> dict:
    """
    Core Monte Carlo: `simulations` simulated years, each drawing a Poisson
    event count from tef_mean and a PERT magnitude per event, summed to that
    year's total loss. Returns ALE (mean of the annual-loss distribution),
    percentiles, and the full sorted sample (`_sorted_losses`) for
    build_exceedance_curve.

    Deterministic when `seed` is given — same reproducibility convention
    generate_o2c_p2p_synthetic_log.py's random.Random(seed) uses, so tests
    don't need to mock the RNG. simulations is clamped to
    [MIN_SIMULATIONS, MAX_SIMULATIONS] regardless of what's requested, so an
    unbounded value from a request body can't turn this into a
    denial-of-service against the API process.
    """
    simulations = max(MIN_SIMULATIONS, min(int(simulations), MAX_SIMULATIONS))
    rng = random.Random(seed)
    annual_losses: list[float] = []
    for _ in range(simulations):
        n_events = _sample_poisson(rng, tef_mean)
        total = sum(_sample_pert(rng, loss_min, loss_likely, loss_max) for _ in range(n_events))
        annual_losses.append(total)
    annual_losses.sort()

    return {
        "ale": statistics.fmean(annual_losses) if annual_losses else 0.0,
        "p10": _percentile(annual_losses, 0.10),
        "p50": _percentile(annual_losses, 0.50),
        "p90": _percentile(annual_losses, 0.90),
        "p95": _percentile(annual_losses, 0.95),
        "min": annual_losses[0] if annual_losses else 0.0,
        "max": annual_losses[-1] if annual_losses else 0.0,
        "simulations": simulations,
        "_sorted_losses": annual_losses,
    }


def build_exceedance_curve(sorted_losses: list[float], points: int = 21) -> list[dict]:
    """
    Loss Exceedance Curve: for each probability p (1.0 down to ~0.01), the
    loss value with a p chance of being EQUALED OR EXCEEDED in a given
    simulated year — the standard FAIR chart, read as "there's a 10% chance
    annual loss exceeds $X." Consecutive duplicate loss values (common at
    the tails when `simulations` is small relative to `points`) are
    collapsed so the chart doesn't render a flat run as a data artifact.
    """
    if not sorted_losses:
        return []
    n = len(sorted_losses)
    curve = []
    for i in range(points):
        p = max(0.01, min(0.99, 1.0 - (i / (points - 1)))) if points > 1 else 0.5
        idx = min(n - 1, max(0, int(round((1 - p) * (n - 1)))))
        curve.append({"probability": round(p, 4), "loss": round(sorted_losses[idx], 4)})
    dedup: list[dict] = []
    for pt in curve:
        if not dedup or dedup[-1]["loss"] != pt["loss"]:
            dedup.append(pt)
    return dedup


def quantify(
    *,
    fire_count_window: int = 0,
    window_days: int = 30,
    manual_tef: Optional[float] = None,
    manual_magnitude: Optional[tuple[float, float, float]] = None,
    sox_estimated_exposure: Optional[float] = None,
    risk_dollar_exposure_m: Optional[float] = None,
    cem_severity: Optional[str] = None,
    simulations: int = DEFAULT_SIMULATIONS,
    seed: Optional[int] = None,
) -> dict:
    """
    One-call convenience: resolve TEF + magnitude from whatever real inputs
    are available, run the Monte Carlo simulation, build the exceedance
    curve, and return everything fair_endpoints.py needs to persist via
    db.save_fair_quantification — this function itself never touches the
    database.
    """
    tef_mean, tef_source = resolve_tef(fire_count_window, window_days, manual_tef)
    loss_min, loss_likely, loss_max, magnitude_source = resolve_magnitude(
        manual=manual_magnitude,
        sox_estimated_exposure=sox_estimated_exposure,
        risk_dollar_exposure_m=risk_dollar_exposure_m,
        cem_severity=cem_severity,
    )
    sim = run_simulation(tef_mean, loss_min, loss_likely, loss_max, simulations, seed)
    curve = build_exceedance_curve(sim.pop("_sorted_losses"))
    return {
        "tef_mean": round(tef_mean, 4),
        "tef_source": tef_source,
        "loss_min": round(loss_min, 4),
        "loss_likely": round(loss_likely, 4),
        "loss_max": round(loss_max, 4),
        "magnitude_source": magnitude_source,
        "simulations": sim["simulations"],
        "ale": round(sim["ale"], 4),
        "p10": round(sim["p10"], 4),
        "p50": round(sim["p50"], 4),
        "p90": round(sim["p90"], 4),
        "p95": round(sim["p95"], 4),
        "min": round(sim["min"], 4),
        "max": round(sim["max"], 4),
        "exceedance_curve": curve,
    }


# ── Control ROI ───────────────────────────────────────────────────────────────


def control_roi(ale_before: float, ale_after: float, annual_control_cost: float) -> dict:
    """
    Risk-adjusted ROI of a control: annualized loss it removes (ale_before -
    ale_after) versus what it costs to run per year. Two FAIR runs feed
    this — one quantified with the control's failure history/no control,
    one quantified assuming the control holds (a lower or zero TEF) — the
    caller (fair_endpoints.py's /fair/control-roi) is responsible for
    producing both; this function only does the comparison.

    This is what a MAP's reduction_pct (risk-engine.js buildMaps(), today a
    hardcoded 12% template constant) should eventually cite instead — once a
    MAP has FAIR runs on both sides of its proposed control, pass
    ale_reduction_pct back as that MAP's reduction_pct.
    """
    ale_before = max(0.0, float(ale_before))
    ale_after = max(0.0, float(ale_after))
    annual_control_cost = max(0.0, float(annual_control_cost))
    risk_reduction = ale_before - ale_after
    ale_reduction_pct = (risk_reduction / ale_before * 100.0) if ale_before > 0 else 0.0
    net_benefit = risk_reduction - annual_control_cost
    roi_pct = (net_benefit / annual_control_cost * 100.0) if annual_control_cost > 0 else None
    return {
        "ale_before": round(ale_before, 4),
        "ale_after": round(ale_after, 4),
        "risk_reduction": round(risk_reduction, 4),
        "ale_reduction_pct": round(ale_reduction_pct, 2),
        "annual_control_cost": round(annual_control_cost, 4),
        "net_benefit": round(net_benefit, 4),
        "roi_pct": round(roi_pct, 2) if roi_pct is not None else None,
        "worth_it": net_benefit > 0,
    }
