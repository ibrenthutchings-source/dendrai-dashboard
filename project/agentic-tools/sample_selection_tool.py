#!/usr/bin/env python3
"""
Audit sample selection — random, risk-based, and monetary unit sampling (MUS)
over a caller-supplied population.

"Sample selection is mechanical once the population and method are defined" —
this module is exactly that mechanical step, deliberately pure (no DB, no
population-specific assumptions) so it works over any population an auditor
already has in hand: a JE Testing findings export, a vendor list, an access
list, whatever. Each function returns both the selected sample AND a
methodology dict — the reproducible parameters (seed, interval, start point)
a workpaper needs to document, not just the resulting rows, since "how was
this sample selected" is itself part of the audit evidence.

Every function is deterministic given the same seed — re-running a sample
selection with the same population/method/seed always reproduces the exact
same sample, which is what makes a workpaper's methodology section
verifiable rather than just asserted.
"""

from __future__ import annotations

import random
from typing import Any, Optional


def random_sample(population: list[dict], n: int, seed: Optional[int] = None) -> dict:
    """Simple random sampling, no stratification. Returns
    {"sample": [...], "methodology": {...}}."""
    if n <= 0:
        return {"sample": [], "methodology": {"method": "random", "n_requested": n, "n_selected": 0,
                                                "population_size": len(population), "seed": seed}}
    n = min(n, len(population))
    rng = random.Random(seed)
    indices = sorted(rng.sample(range(len(population)), n))
    sample = [population[i] for i in indices]
    return {
        "sample": sample,
        "methodology": {
            "method": "random", "n_requested": n, "n_selected": len(sample),
            "population_size": len(population), "seed": seed,
            "coverage_pct": round(100 * len(sample) / len(population), 2) if population else 0.0,
        },
    }


def risk_based_sample(
    population: list[dict], risk_key: str, n: int,
    high_risk_threshold: float = 0.7, high_risk_coverage: float = 1.0, seed: Optional[int] = None,
) -> dict:
    """Stratified: every item at/above high_risk_threshold is selected up to
    high_risk_coverage (1.0 = all of them, since high-risk items are exactly
    the ones a risk-based approach exists to not under-sample), then the
    remaining sample budget is filled by simple random sampling from the
    rest of the population. Items missing risk_key are treated as the
    lowest-risk tier (0.0) — a missing score is not grounds to skip
    sampling it, only to deprioritize it behind scored items."""
    if n <= 0:
        return {"sample": [], "methodology": {"method": "risk_based", "n_requested": n, "n_selected": 0,
                                                "population_size": len(population), "seed": seed}}
    rng = random.Random(seed)

    def _score(item: dict) -> float:
        v = item.get(risk_key)
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    high_risk = [item for item in population if _score(item) >= high_risk_threshold]
    rest = [item for item in population if _score(item) < high_risk_threshold]

    high_risk_n = min(len(high_risk), max(0, round(len(high_risk) * high_risk_coverage)))
    high_risk_indices = sorted(rng.sample(range(len(high_risk)), high_risk_n)) if high_risk_n < len(high_risk) \
        else list(range(len(high_risk)))
    selected_high_risk = [high_risk[i] for i in high_risk_indices]

    remaining_budget = max(0, n - len(selected_high_risk))
    fill_n = min(remaining_budget, len(rest))
    fill_indices = sorted(rng.sample(range(len(rest)), fill_n))
    selected_fill = [rest[i] for i in fill_indices]

    sample = selected_high_risk + selected_fill
    return {
        "sample": sample,
        "methodology": {
            "method": "risk_based", "n_requested": n, "n_selected": len(sample),
            "population_size": len(population), "seed": seed, "risk_key": risk_key,
            "high_risk_threshold": high_risk_threshold, "high_risk_population": len(high_risk),
            "high_risk_selected": len(selected_high_risk), "random_fill_selected": len(selected_fill),
            "coverage_pct": round(100 * len(sample) / len(population), 2) if population else 0.0,
        },
    }


def mus_sample(
    population: list[dict], amount_key: str, sample_size: int, seed: Optional[int] = None,
) -> dict:
    """Classic monetary unit sampling: systematic selection with a fixed
    $-interval, walking cumulative population value and picking the item
    that contains each interval boundary — so an item's selection
    probability is proportional to its dollar value, the entire point of
    MUS over simple random sampling for a financial population. Negative
    and zero-value items are excluded from the walk (they can never be
    "hit" by a monetary-unit interval) but reported in the methodology so
    their exclusion is documented, not silent. A single very large item
    exceeding the interval can be hit more than once; each hit still only
    selects the item once (dedup by population index)."""
    valued = [(i, item, float(item.get(amount_key) or 0)) for i, item in enumerate(population)]
    excluded = [i for i, _item, amt in valued if amt <= 0]
    walk = [(i, item, amt) for i, item, amt in valued if amt > 0]
    total_value = sum(amt for _i, _item, amt in walk)

    if sample_size <= 0 or total_value <= 0 or not walk:
        return {
            "sample": [],
            "methodology": {
                "method": "mus", "n_requested": sample_size, "n_selected": 0,
                "population_size": len(population), "seed": seed, "amount_key": amount_key,
                "total_value": total_value, "interval": None, "excluded_non_positive": len(excluded),
            },
        }

    interval = total_value / sample_size
    rng = random.Random(seed)
    start = rng.uniform(0, interval)

    selected_indices: list[int] = []
    cumulative = 0.0
    walk_pos = 0
    boundary = start
    while len(selected_indices) < sample_size and boundary <= total_value:
        while walk_pos < len(walk) and cumulative + walk[walk_pos][2] < boundary:
            cumulative += walk[walk_pos][2]
            walk_pos += 1
        if walk_pos >= len(walk):
            break
        pop_index = walk[walk_pos][0]
        if pop_index not in selected_indices:
            selected_indices.append(pop_index)
        boundary += interval

    sample = [population[i] for i in selected_indices]
    return {
        "sample": sample,
        "methodology": {
            "method": "mus", "n_requested": sample_size, "n_selected": len(sample),
            "population_size": len(population), "seed": seed, "amount_key": amount_key,
            "total_value": round(total_value, 2), "interval": round(interval, 2),
            "start_point": round(start, 2), "excluded_non_positive": len(excluded),
            "coverage_pct": round(100 * len(sample) / len(population), 2) if population else 0.0,
        },
    }


_METHODS = {"random": random_sample, "risk_based": risk_based_sample, "mus": mus_sample}


def select(method: str, population: list[dict], params: dict[str, Any]) -> dict:
    """Dispatch by method name — the single entry point sample_selection_endpoints.py
    calls, so adding a fourth method later means one new function plus one
    _METHODS entry, not a new endpoint."""
    fn = _METHODS.get(method)
    if fn is None:
        raise ValueError(f"Unknown sampling method '{method}' — choose one of {sorted(_METHODS)}")
    return fn(population, **params)
