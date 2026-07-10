#!/usr/bin/env python3
"""
Statistical drift detection for the Model Health screen.

Population Stability Index (PSI) between a baseline window and a current
window, applied two ways:

  - compute_ratio_drift(): cross-sectional — compares the distribution of
    each financial ratio across ALL analyzed tickers/runs between an early
    baseline period and the most recent runs. Tests whether the population
    of companies being analyzed has drifted from what the industry
    risk-scoring templates were calibrated against.

  - compute_fred_regime_drift(): a straight regime-shift check on a small
    fixed set of broad macro indicators, comparing each series' own earlier
    quarters against its most recent quarters.

Standard PSI convention: < 0.10 stable, 0.10-0.20 watch, > 0.20 drift.
"""

from __future__ import annotations

import math
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))

try:
    from fred_tool import fetch_fred_series, FRED_SERIES
    _HAS_FRED = True
except ImportError:
    _HAS_FRED = False

# Broad, ticker-agnostic macro regime indicators — not tied to any specific
# company's correlation run (macro series values are the same for everyone).
_REGIME_SERIES = ["GDPC1", "FEDFUNDS", "UNRATE", "VIXCLS", "T10Y2Y"]

_RATIO_FIELDS = [
    "revenue_growth", "gross_margin", "net_margin", "fcf_margin",
    "rd_intensity", "sga_intensity", "asset_growth", "cash_ratio",
]

_MIN_BUCKET_SAMPLES = 5  # need at least this many points per bucket for a stable PSI estimate


def _flag(psi: Optional[float]) -> str:
    if psi is None:
        return "insufficient_data"
    if psi < 0.10:
        return "stable"
    if psi < 0.20:
        return "watch"
    return "drift"


def compute_psi(baseline: list[float], current: list[float], buckets: int = 10,
                 min_bucket_samples: int = _MIN_BUCKET_SAMPLES) -> Optional[float]:
    """
    Population Stability Index between two samples.
    Bucket edges are baseline quantiles; both samples are then binned into
    the same edges. Returns None when either sample is too small to bin
    meaningfully rather than a misleading number.

    `min_bucket_samples` is overridable because not every caller has the
    same amount of data to work with: compute_fred_regime_drift() only ever
    has 4 "current" quarters by design (that's a genuine data constraint,
    not a bug) and explicitly accepts a coarser, less reliable estimate in
    exchange for using fewer buckets — the default of 5/bucket is tuned for
    compute_ratio_drift()'s much larger samples, where a false "drift" flag
    from bucket noise is more consequential.
    """
    baseline = [v for v in baseline if v is not None and not math.isnan(v)]
    current = [v for v in current if v is not None and not math.isnan(v)]
    n = min(len(baseline), len(current))
    if n < buckets * min_bucket_samples:
        return None

    sorted_base = sorted(baseline)
    edges = sorted(set(
        sorted_base[int(round(i * (len(sorted_base) - 1) / buckets))]
        for i in range(buckets + 1)
    ))
    if len(edges) < 3:
        return None  # baseline has too little spread to form distinct buckets

    def _bucket_pcts(sample: list[float]) -> list[float]:
        counts = [0] * (len(edges) - 1)
        for v in sample:
            idx = 0
            while idx < len(edges) - 2 and v > edges[idx + 1]:
                idx += 1
            counts[idx] += 1
        total = len(sample) or 1
        # Floor each bucket's share so a zero-count bucket doesn't blow up
        # the log term — standard PSI practice.
        return [max(c / total, 1e-4) for c in counts]

    base_pct = _bucket_pcts(baseline)
    cur_pct = _bucket_pcts(current)
    return round(sum((c - b) * math.log(c / b) for b, c in zip(base_pct, cur_pct)), 4)


def compute_ratio_drift(rows: list[dict], split_last_n: int = 8) -> list[dict]:
    """
    rows: output of db.get_financial_ratios_history() — oldest-first, one
    dict per (ticker, run) with the _RATIO_FIELDS. Splits into baseline
    (everything before the last `split_last_n` distinct run timestamps) vs
    current (the last `split_last_n`), per ratio field.
    """
    if not rows:
        return []

    distinct_run_ats = sorted(set(r["run_at"] for r in rows if r.get("run_at")))
    if len(distinct_run_ats) <= split_last_n:
        # Not enough history to form two distinct windows yet.
        cutoff = None
    else:
        cutoff = distinct_run_ats[-split_last_n]

    results = []
    for field in _RATIO_FIELDS:
        if cutoff is None:
            baseline_vals: list[float] = []
            current_vals = [r[field] for r in rows if r.get(field) is not None]
        else:
            baseline_vals = [r[field] for r in rows if r.get("run_at") and r["run_at"] < cutoff and r.get(field) is not None]
            current_vals = [r[field] for r in rows if r.get("run_at") and r["run_at"] >= cutoff and r.get(field) is not None]
        psi = compute_psi(baseline_vals, current_vals) if baseline_vals else None
        results.append({
            "ratio": field,
            "psi": psi,
            "flag": _flag(psi),
            "n_baseline": len(baseline_vals),
            "n_current": len(current_vals),
        })
    return results


def compute_fred_regime_drift(api_key: str, series_ids: Optional[list[str]] = None) -> list[dict]:
    """
    Regime-shift PSI on a small set of broad macro indicators, comparing
    each series' own earlier quarters to its most recent quarters. Returns
    [] immediately if no api_key or fred_tool isn't importable.
    """
    if not api_key or not _HAS_FRED:
        return []

    results = []
    for sid in (series_ids or _REGIME_SERIES):
        info = FRED_SERIES.get(sid)
        if not info:
            continue
        try:
            series = fetch_fred_series(sid, api_key, info.get("agg_method", "avg"))
        except Exception:
            continue
        if not series:
            continue
        quarters = sorted(series.keys())
        n = len(quarters)
        split = max(1, n - 4)  # last 4 quarters = "current"; rest = baseline
        baseline_vals = [series[q] for q in quarters[:split]]
        current_vals = [series[q] for q in quarters[split:]]
        # Only 4 "current" quarters by design (a genuinely small sample) —
        # a 2-bucket (above/below baseline median) PSI with a relaxed
        # per-bucket floor is coarse but still a legitimate, honest
        # regime-shift signal given how little "current" data exists.
        psi = compute_psi(baseline_vals, current_vals, buckets=2, min_bucket_samples=2)
        results.append({
            "series_id": sid,
            "name": info.get("name", sid),
            "psi": psi,
            "flag": _flag(psi),
            "n_baseline": len(baseline_vals),
            "n_current": len(current_vals),
        })
    return results
