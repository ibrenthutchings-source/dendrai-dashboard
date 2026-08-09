#!/usr/bin/env python3
"""
Unit tests for fair_tool.py — the FAIR Monte Carlo loss-quantification
engine (Risk Quantification). Entirely pure: no DB, no HTTP, no mocking
needed — same testability reasoning as test_control_flow_map.py's
db._build_control_flow_map tests.

    pytest test_fair_tool.py -v
"""

from __future__ import annotations

import fair_tool as ft


# ── resolve_tef ───────────────────────────────────────────────────────────────

def test_resolve_tef_manual_override_wins():
    tef, source = ft.resolve_tef(fire_count_window=999, window_days=30, manual_tef=2.5)
    assert tef == 2.5
    assert source == "manual"


def test_resolve_tef_annualizes_empirical_window():
    # 6 fires in 30 days -> 6/30*365 = 73 events/year
    tef, source = ft.resolve_tef(fire_count_window=6, window_days=30)
    assert tef == 73.0
    assert source == "empirical"


def test_resolve_tef_zero_window_days_never_divides_by_zero():
    tef, source = ft.resolve_tef(fire_count_window=5, window_days=0)
    assert tef == 0.0
    assert source == "empirical"


def test_resolve_tef_negative_fire_count_clamped_to_zero():
    tef, _ = ft.resolve_tef(fire_count_window=-3, window_days=30)
    assert tef == 0.0


# ── resolve_magnitude ─────────────────────────────────────────────────────────

def test_resolve_magnitude_priority_manual_first():
    lo, likely, hi, source = ft.resolve_magnitude(
        manual=(1.0, 2.0, 3.0), sox_estimated_exposure=100.0, risk_dollar_exposure_m=50.0, cem_severity="P1",
    )
    assert (lo, likely, hi, source) == (1.0, 2.0, 3.0, "manual")


def test_resolve_magnitude_priority_sox_before_risk_and_severity():
    lo, likely, hi, source = ft.resolve_magnitude(
        sox_estimated_exposure=10.0, risk_dollar_exposure_m=50.0, cem_severity="P1",
    )
    assert source == "sox_exposure"
    assert likely == 10.0
    assert lo == 6.0    # 0.6x
    assert hi == 20.0   # 2x


def test_resolve_magnitude_priority_risk_before_severity():
    lo, likely, hi, source = ft.resolve_magnitude(risk_dollar_exposure_m=20.0, cem_severity="P1")
    assert source == "risk_dollar_exposure"
    assert likely == 20.0


def test_resolve_magnitude_falls_back_to_cem_severity_band():
    lo, likely, hi, source = ft.resolve_magnitude(cem_severity="P1")
    assert source == "cem_severity_default"
    assert (lo, likely, hi) == ft.CEM_SEVERITY_BANDS["P1"]


def test_resolve_magnitude_unknown_severity_falls_back_to_p2_band():
    lo, likely, hi, source = ft.resolve_magnitude(cem_severity="not-a-real-severity")
    assert (lo, likely, hi) == ft.CEM_SEVERITY_BANDS["P2"]


def test_resolve_magnitude_absolute_fallback_when_nothing_supplied():
    lo, likely, hi, source = ft.resolve_magnitude()
    assert source == "cem_severity_default"
    assert (lo, likely, hi) == ft.CEM_SEVERITY_BANDS["P2"]


# ── run_simulation ────────────────────────────────────────────────────────────

def test_run_simulation_zero_tef_gives_zero_ale():
    result = ft.run_simulation(tef_mean=0.0, loss_min=1.0, loss_likely=5.0, loss_max=10.0,
                                simulations=500, seed=1)
    assert result["ale"] == 0.0
    assert result["p50"] == 0.0
    assert result["max"] == 0.0


def test_run_simulation_deterministic_with_seed():
    a = ft.run_simulation(tef_mean=4.0, loss_min=1.0, loss_likely=3.0, loss_max=8.0, simulations=1000, seed=7)
    b = ft.run_simulation(tef_mean=4.0, loss_min=1.0, loss_likely=3.0, loss_max=8.0, simulations=1000, seed=7)
    assert a["ale"] == b["ale"]
    assert a["p90"] == b["p90"]


def test_run_simulation_different_seeds_can_differ():
    a = ft.run_simulation(tef_mean=4.0, loss_min=1.0, loss_likely=3.0, loss_max=8.0, simulations=1000, seed=1)
    b = ft.run_simulation(tef_mean=4.0, loss_min=1.0, loss_likely=3.0, loss_max=8.0, simulations=1000, seed=2)
    assert a["ale"] != b["ale"]


def test_run_simulation_clamps_simulation_count():
    result = ft.run_simulation(tef_mean=1.0, loss_min=1.0, loss_likely=2.0, loss_max=3.0,
                                simulations=10, seed=1)  # below MIN_SIMULATIONS
    assert result["simulations"] == ft.MIN_SIMULATIONS

    result2 = ft.run_simulation(tef_mean=1.0, loss_min=1.0, loss_likely=2.0, loss_max=3.0,
                                 simulations=10_000_000, seed=1)  # above MAX_SIMULATIONS
    assert result2["simulations"] == ft.MAX_SIMULATIONS


def test_run_simulation_higher_tef_increases_ale():
    low = ft.run_simulation(tef_mean=1.0, loss_min=1.0, loss_likely=2.0, loss_max=3.0, simulations=4000, seed=3)
    high = ft.run_simulation(tef_mean=10.0, loss_min=1.0, loss_likely=2.0, loss_max=3.0, simulations=4000, seed=3)
    assert high["ale"] > low["ale"]


def test_run_simulation_percentiles_are_non_decreasing():
    result = ft.run_simulation(tef_mean=5.0, loss_min=1.0, loss_likely=3.0, loss_max=10.0, simulations=3000, seed=9)
    assert result["min"] <= result["p10"] <= result["p50"] <= result["p90"] <= result["p95"] <= result["max"]


# ── build_exceedance_curve ────────────────────────────────────────────────────

def test_build_exceedance_curve_empty_input():
    assert ft.build_exceedance_curve([]) == []


def test_build_exceedance_curve_loss_increases_as_probability_decreases():
    losses = sorted(float(i) for i in range(1000))
    curve = ft.build_exceedance_curve(losses, points=11)
    # Higher probability of exceedance -> lower loss threshold; the curve
    # must be sorted the same direction as probability descends.
    for a, b in zip(curve, curve[1:]):
        assert a["probability"] >= b["probability"]
        assert a["loss"] <= b["loss"]


def test_build_exceedance_curve_dedupes_flat_tails():
    losses = [0.0] * 50 + [100.0]
    curve = ft.build_exceedance_curve(losses, points=21)
    # A long flat run at the bottom should collapse to one point, not 20.
    zero_points = [pt for pt in curve if pt["loss"] == 0.0]
    assert len(zero_points) == 1


# ── quantify (end-to-end, still pure) ─────────────────────────────────────────

def test_quantify_end_to_end_uses_empirical_tef_and_sox_magnitude():
    result = ft.quantify(fire_count_window=3, window_days=30, sox_estimated_exposure=5.0,
                          simulations=1000, seed=11)
    assert result["tef_source"] == "empirical"
    assert result["magnitude_source"] == "sox_exposure"
    assert result["ale"] >= 0
    assert len(result["exceedance_curve"]) > 0


def test_quantify_zero_frequency_gives_zero_loss_regardless_of_magnitude():
    result = ft.quantify(fire_count_window=0, window_days=30, cem_severity="P1", simulations=500, seed=1)
    assert result["ale"] == 0.0


# ── control_roi ────────────────────────────────────────────────────────────────

def test_control_roi_worth_it_when_net_benefit_positive():
    roi = ft.control_roi(ale_before=10.0, ale_after=2.0, annual_control_cost=1.0)
    assert roi["risk_reduction"] == 8.0
    assert roi["ale_reduction_pct"] == 80.0
    assert roi["net_benefit"] == 7.0
    assert roi["roi_pct"] == 700.0
    assert roi["worth_it"] is True


def test_control_roi_not_worth_it_when_cost_exceeds_reduction():
    roi = ft.control_roi(ale_before=10.0, ale_after=9.5, annual_control_cost=5.0)
    assert roi["worth_it"] is False
    assert roi["net_benefit"] < 0


def test_control_roi_pct_is_none_when_cost_is_zero():
    roi = ft.control_roi(ale_before=10.0, ale_after=2.0, annual_control_cost=0.0)
    assert roi["roi_pct"] is None


def test_control_roi_negative_inputs_clamped_to_zero():
    roi = ft.control_roi(ale_before=-5.0, ale_after=-1.0, annual_control_cost=-2.0)
    assert roi["ale_before"] == 0.0
    assert roi["ale_after"] == 0.0
    assert roi["annual_control_cost"] == 0.0
