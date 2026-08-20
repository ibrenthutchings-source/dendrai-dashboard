#!/usr/bin/env python3
"""
Unit tests for sample_selection_tool.py — random, risk-based, and monetary
unit sampling. Pure functions, no DB/HTTP.

    pytest test_sample_selection_tool.py -v
"""
from __future__ import annotations

import pytest

import sample_selection_tool as sst


def _population(n: int, amount_key="amount", risk_key="risk_score"):
    return [{"id": i, amount_key: (i + 1) * 100, risk_key: (i % 10) / 10} for i in range(n)]


# ── random_sample ─────────────────────────────────────────────────────────────

def test_random_sample_selects_requested_count():
    result = sst.random_sample(_population(50), 10, seed=1)
    assert len(result["sample"]) == 10
    assert result["methodology"]["n_selected"] == 10
    assert result["methodology"]["population_size"] == 50


def test_random_sample_is_deterministic_given_same_seed():
    pop = _population(50)
    a = sst.random_sample(pop, 10, seed=42)
    b = sst.random_sample(pop, 10, seed=42)
    assert [item["id"] for item in a["sample"]] == [item["id"] for item in b["sample"]]


def test_random_sample_different_seeds_usually_differ():
    pop = _population(50)
    a = sst.random_sample(pop, 10, seed=1)
    b = sst.random_sample(pop, 10, seed=2)
    assert [item["id"] for item in a["sample"]] != [item["id"] for item in b["sample"]]


def test_random_sample_caps_at_population_size():
    result = sst.random_sample(_population(5), 100, seed=1)
    assert len(result["sample"]) == 5
    assert result["methodology"]["n_selected"] == 5


def test_random_sample_no_duplicates():
    result = sst.random_sample(_population(30), 15, seed=7)
    ids = [item["id"] for item in result["sample"]]
    assert len(ids) == len(set(ids))


def test_random_sample_zero_n_returns_empty():
    result = sst.random_sample(_population(10), 0, seed=1)
    assert result["sample"] == []


# ── risk_based_sample ──────────────────────────────────────────────────────────

def test_risk_based_sample_includes_all_high_risk_by_default():
    pop = [{"id": i, "risk_score": 0.9 if i < 3 else 0.1} for i in range(20)]
    result = sst.risk_based_sample(pop, "risk_score", n=5, high_risk_threshold=0.7, seed=1)
    high_risk_ids = {item["id"] for item in result["sample"] if item["risk_score"] >= 0.7}
    assert high_risk_ids == {0, 1, 2}
    assert result["methodology"]["high_risk_population"] == 3
    assert result["methodology"]["high_risk_selected"] == 3


def test_risk_based_sample_fills_remainder_randomly():
    pop = [{"id": i, "risk_score": 0.9 if i < 2 else 0.1} for i in range(20)]
    result = sst.risk_based_sample(pop, "risk_score", n=6, high_risk_threshold=0.7, seed=1)
    assert len(result["sample"]) == 6
    assert result["methodology"]["high_risk_selected"] == 2
    assert result["methodology"]["random_fill_selected"] == 4


def test_risk_based_sample_missing_score_treated_as_lowest_tier():
    pop = [{"id": 0, "risk_score": 0.9}, {"id": 1}, {"id": 2}]  # ids 1,2 have no risk_score
    result = sst.risk_based_sample(pop, "risk_score", n=3, high_risk_threshold=0.7, seed=1)
    assert len(result["sample"]) == 3  # nothing excluded, just deprioritized


def test_risk_based_sample_is_deterministic_given_same_seed():
    pop = _population(40)
    a = sst.risk_based_sample(pop, "risk_score", n=10, seed=5)
    b = sst.risk_based_sample(pop, "risk_score", n=10, seed=5)
    assert [item["id"] for item in a["sample"]] == [item["id"] for item in b["sample"]]


# ── mus_sample ──────────────────────────────────────────────────────────────

def test_mus_sample_selection_probability_favors_high_value_items():
    """A single item worth 90% of total population value should be selected
    by nearly every MUS draw — that's the entire point of MUS over random
    sampling for a financial population."""
    pop = [{"id": 0, "amount": 9000}] + [{"id": i, "amount": 10} for i in range(1, 101)]
    hits = 0
    for seed in range(20):
        result = sst.mus_sample(pop, "amount", sample_size=5, seed=seed)
        if any(item["id"] == 0 for item in result["sample"]):
            hits += 1
    assert hits >= 18  # overwhelming majority, allow rare edge-case misses


def test_mus_sample_interval_math():
    pop = [{"id": i, "amount": 1000} for i in range(10)]  # total = 10,000
    result = sst.mus_sample(pop, "amount", sample_size=5, seed=1)
    assert result["methodology"]["total_value"] == 10000
    assert result["methodology"]["interval"] == 2000


def test_mus_sample_excludes_non_positive_amounts():
    pop = [{"id": 0, "amount": 100}, {"id": 1, "amount": 0}, {"id": 2, "amount": -50}, {"id": 3, "amount": 200}]
    result = sst.mus_sample(pop, "amount", sample_size=2, seed=1)
    assert result["methodology"]["excluded_non_positive"] == 2
    assert all(item["id"] in (0, 3) for item in result["sample"])


def test_mus_sample_is_deterministic_given_same_seed():
    pop = _population(50)
    a = sst.mus_sample(pop, "amount", sample_size=10, seed=3)
    b = sst.mus_sample(pop, "amount", sample_size=10, seed=3)
    assert [item["id"] for item in a["sample"]] == [item["id"] for item in b["sample"]]


def test_mus_sample_no_selection_when_total_value_is_zero():
    pop = [{"id": i, "amount": 0} for i in range(10)]
    result = sst.mus_sample(pop, "amount", sample_size=5, seed=1)
    assert result["sample"] == []
    assert result["methodology"]["interval"] is None


def test_mus_sample_selects_at_most_sample_size_distinct_items():
    pop = _population(200)
    result = sst.mus_sample(pop, "amount", sample_size=20, seed=1)
    assert len(result["sample"]) <= 20
    ids = [item["id"] for item in result["sample"]]
    assert len(ids) == len(set(ids))


# ── select() dispatch ─────────────────────────────────────────────────────────

def test_select_dispatches_to_correct_method():
    pop = _population(20)
    result = sst.select("random", pop, {"n": 5, "seed": 1})
    assert result["methodology"]["method"] == "random"


def test_select_raises_for_unknown_method():
    with pytest.raises(ValueError):
        sst.select("not_a_real_method", _population(5), {})


def test_select_mus_via_dispatch():
    pop = _population(20)
    result = sst.select("mus", pop, {"amount_key": "amount", "sample_size": 5, "seed": 1})
    assert result["methodology"]["method"] == "mus"
