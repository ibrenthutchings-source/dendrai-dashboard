"""
Dendrai forecasting-py — runnable example.
Mirrors the mock data shapes used by the React app (mock-data.js / risk-engine.js).

Run:  python example.py
      Then open the saved HTML files in your browser.
"""

from forecasting import (
    fit_predict_ensemble,
    update_ensemble_weights,
    find_leading_indicators,
    plot_forecast,
    plot_ensemble_components,
    plot_leading_indicators,
    plot_forecast_dashboard,
)

# ── Mock data (mirrors the shape from risk-engine.js) ─────────────────────────

REVENUE_HISTORY = [
    {"q": "Q1 2022", "v": 3821}, {"q": "Q2 2022", "v": 4102},
    {"q": "Q3 2022", "v": 4388}, {"q": "Q4 2022", "v": 4715},
    {"q": "Q1 2023", "v": 4501}, {"q": "Q2 2023", "v": 4823},
    {"q": "Q3 2023", "v": 5140}, {"q": "Q4 2023", "v": 5512},
]

MARGIN_HISTORY = [
    {"q": "Q1 2022", "v": 52.1}, {"q": "Q2 2022", "v": 53.4},
    {"q": "Q3 2022", "v": 54.0}, {"q": "Q4 2022", "v": 55.2},
    {"q": "Q1 2023", "v": 53.8}, {"q": "Q2 2023", "v": 54.9},
    {"q": "Q3 2023", "v": 55.7}, {"q": "Q4 2023", "v": 56.1},
]

# Mock FRED series (quarterly, same length as history)
FRED_ARRAYS = {
    "PPIACO":  [240.1, 244.5, 248.2, 251.0, 249.8, 253.1, 257.4, 261.0],
    "UNRATE":  [3.8, 3.6, 3.5, 3.4, 3.7, 3.6, 3.5, 3.4],
    "FEDFUNDS": [0.25, 0.75, 2.25, 3.75, 4.50, 5.00, 5.25, 5.25],
    "DXY":     [99.2, 103.5, 109.1, 104.3, 102.4, 103.8, 105.2, 106.0],
}

FORECAST_QUARTERS = ["Q1 2024", "Q2 2024", "Q3 2024", "Q4 2024"]


# ── 1. Ensemble forecast ───────────────────────────────────────────────────────

rev_values = [d["v"] for d in REVENUE_HISTORY]
result = fit_predict_ensemble(rev_values, steps=4, fred_arrays=FRED_ARRAYS)

# Convert result back to the dict shape the React app uses
revenue_forecast = [
    {"q": FORECAST_QUARTERS[i], "base": result.base[i], "lo": result.lo[i], "hi": result.hi[i]}
    for i in range(4)
]

print("Revenue forecast:")
for fc in revenue_forecast:
    print(f"  {fc['q']}: ${fc['base']:,.0f}M  [{fc['lo']:,.0f} – {fc['hi']:,.0f}]")


# ── 2. Margin forecast ─────────────────────────────────────────────────────────

mgn_values = [d["v"] for d in MARGIN_HISTORY]
mgn_result = fit_predict_ensemble(mgn_values, steps=4)

margin_forecast = [
    {"q": FORECAST_QUARTERS[i], "base": mgn_result.base[i], "lo": mgn_result.lo[i], "hi": mgn_result.hi[i]}
    for i in range(4)
]


# ── 3. Update weights from backtesting MAPEs (if available) ───────────────────
# Mirrors updateEnsembleWeights() — call this after backtesting.py produces MAPEs
# new_weights = update_ensemble_weights([0.08, 0.06, 0.11])  # ARIMA, Prophet, RF MAPEs


# ── 4. FRED leading indicators ────────────────────────────────────────────────

indicators = find_leading_indicators(rev_values, FRED_ARRAYS, max_lag=4)
print(f"\nTop 3 FRED leading indicators:")
for ind in indicators[:3]:
    sig = " *" if ind["p_value"] < 0.05 else ""
    print(f"  {ind['id']} lag={ind['lag']}Q  r={ind['r']:+.3f}{sig}")


# ── 5. Visualizations ─────────────────────────────────────────────────────────

# Single revenue forecast chart (mirrors ForecastChart SVG)
fig1 = plot_forecast(REVENUE_HISTORY, revenue_forecast, title="ON Semiconductor — Revenue Forecast", unit="$M")
fig1.write_html("revenue_forecast.html")
print("\nSaved: revenue_forecast.html")

# Ensemble component breakdown (diagnostic — not in the JS app)
fig2 = plot_ensemble_components(REVENUE_HISTORY, result, FORECAST_QUARTERS, unit="$M")
fig2.write_html("ensemble_components.html")
print("Saved: ensemble_components.html")

# FRED heatmap (leading indicator matrix)
fig3 = plot_leading_indicators(indicators)
fig3.write_html("leading_indicators.html")
print("Saved: leading_indicators.html")

# Synchronized revenue + margin dashboard (single crosshair across both rows)
fig4 = plot_forecast_dashboard(REVENUE_HISTORY, revenue_forecast, MARGIN_HISTORY, margin_forecast)
fig4.write_html("forecast_dashboard.html")
print("Saved: forecast_dashboard.html")
