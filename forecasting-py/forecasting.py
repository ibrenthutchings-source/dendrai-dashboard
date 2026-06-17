"""
Dendrai Risk Loop — Forecasting Engine (Python)
Replaces forecasting.js using statsmodels / scikit-learn / prophet.

Models: ARIMA · Prophet · Random Forest · Ensemble
Visualization: Plotly (interactive, replaces custom SVG ForecastChart)
"""

from __future__ import annotations

import warnings
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

warnings.filterwarnings("ignore", category=UserWarning)


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class ForecastResult:
    base: list[float]
    lo: list[float]
    hi: list[float]
    components: dict[str, list[float]] = field(default_factory=dict)


# ── ARIMA ─────────────────────────────────────────────────────────────────────

def fit_predict_arima(
    values: list[float],
    steps: int,
    order: tuple[int, int, int] = (2, 1, 1),
) -> ForecastResult:
    """ARIMA(p,d,q) via statsmodels — replaces the hand-rolled OLS AR+MA in forecasting.js."""
    from statsmodels.tsa.arima.model import ARIMA

    if len(values) < sum(order) + 6:
        order = (1, 1, 0)

    result = ARIMA(values, order=order).fit()
    fc = result.get_forecast(steps=steps).summary_frame(alpha=0.05)

    return ForecastResult(
        base=fc["mean"].tolist(),
        lo=fc["mean_ci_lower"].tolist(),
        hi=fc["mean_ci_upper"].tolist(),
    )


# ── Prophet ───────────────────────────────────────────────────────────────────

def fit_predict_prophet(values: list[float], steps: int) -> ForecastResult:
    """
    Facebook Prophet with quarterly seasonality.
    Falls back to Fourier regression (the JS model) if prophet is not installed.
    """
    try:
        from prophet import Prophet

        n = len(values)
        dates = pd.date_range(start="2021-01-01", periods=n, freq="QS")
        df = pd.DataFrame({"ds": dates, "y": values})

        m = Prophet(
            seasonality_mode="multiplicative",
            yearly_seasonality=True,
            weekly_seasonality=False,
            daily_seasonality=False,
            interval_width=0.95,
        )
        m.fit(df)

        future = m.make_future_dataframe(periods=steps, freq="QS")
        fc = m.predict(future).tail(steps)

        return ForecastResult(
            base=fc["yhat"].tolist(),
            lo=fc["yhat_lower"].tolist(),
            hi=fc["yhat_upper"].tolist(),
        )

    except ImportError:
        return _fourier_fallback(values, steps)


def _fourier_fallback(values: list[float], steps: int) -> ForecastResult:
    """
    Mirrors the JS Prophet-like implementation exactly:
    y(t) = β₀ + β₁·t + Σ [aₖ·sin(2πkt/P) + bₖ·cos(2πkt/P)], P=4, k=1..2
    """
    n = len(values)

    def features(i: int) -> list[float]:
        t = i / max(n + steps - 1, 1)
        ph = i % 4
        return [
            1, t,
            np.sin(2 * np.pi * ph / 4), np.cos(2 * np.pi * ph / 4),
            np.sin(4 * np.pi * ph / 4), np.cos(4 * np.pi * ph / 4),
        ]

    X = np.array([features(i) for i in range(n)])
    y = np.array(values)
    coeffs, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    sigma = float(np.std(y - X @ coeffs))

    base = [float(np.array(features(n + s)) @ coeffs) for s in range(steps)]
    lo = [b - 1.96 * sigma * np.sqrt(i + 1) for i, b in enumerate(base)]
    hi = [b + 1.96 * sigma * np.sqrt(i + 1) for i, b in enumerate(base)]
    return ForecastResult(base=base, lo=lo, hi=hi)


# ── Random Forest ─────────────────────────────────────────────────────────────

def _lag_features(values: list[float], fred_arrays: Optional[dict], i: int) -> list[float]:
    """Mirrors buildFeatures() in forecasting.js: lags 1-4, rolling mean/std, time, quarter, FRED."""
    feats = [values[i - lag] if i >= lag else values[0] for lag in [1, 2, 3, 4]]
    win = values[max(0, i - 4):i]
    feats.append(float(np.mean(win)) if win else values[0])
    feats.append(float(np.std(win)) if len(win) > 1 else 0.0)
    feats.extend([float(i), float(i % 4)])
    if fred_arrays:
        for arr in fred_arrays.values():
            feats.append(arr[i] if i < len(arr) else (arr[-1] if arr else 0.0))
    return feats


def fit_predict_rf(
    values: list[float],
    steps: int,
    fred_arrays: Optional[dict] = None,
    n_estimators: int = 100,
) -> ForecastResult:
    """Random Forest with recursive multi-step forecasting and per-tree CI (mirrors forecasting.js RF)."""
    from sklearn.ensemble import RandomForestRegressor

    n = len(values)
    X = np.array([_lag_features(values, fred_arrays, i) for i in range(n)])
    y = np.array(values)

    rf = RandomForestRegressor(
        n_estimators=n_estimators, max_depth=4, min_samples_leaf=2, random_state=7
    )
    rf.fit(X, y)

    extended = list(values)
    base, lo_list, hi_list = [], [], []

    for _ in range(steps):
        x = np.array([_lag_features(extended, fred_arrays, len(extended))])
        tree_preds = np.array([t.predict(x)[0] for t in rf.estimators_])
        avg, sigma = float(tree_preds.mean()), float(tree_preds.std())
        base.append(avg)
        lo_list.append(avg - 1.96 * sigma)
        hi_list.append(avg + 1.96 * sigma)
        extended.append(avg)

    return ForecastResult(base=base, lo=lo_list, hi=hi_list)


# ── Ensemble ──────────────────────────────────────────────────────────────────

def fit_predict_ensemble(
    values: list[float],
    steps: int,
    fred_arrays: Optional[dict] = None,
    weights: Optional[list[float]] = None,
) -> ForecastResult:
    """Weighted ensemble of ARIMA + Prophet + Random Forest (mirrors fitEnsemble / predictEnsemble)."""
    w = weights or [1 / 3, 1 / 3, 1 / 3]

    p1 = fit_predict_arima(values, steps)
    p2 = fit_predict_prophet(values, steps)
    p3 = fit_predict_rf(values, steps, fred_arrays)

    blend = lambda a, b, c: [w[0]*a[i] + w[1]*b[i] + w[2]*c[i] for i in range(steps)]

    return ForecastResult(
        base=blend(p1.base, p2.base, p3.base),
        lo=blend(p1.lo, p2.lo, p3.lo),
        hi=blend(p1.hi, p2.hi, p3.hi),
        components={"arima": p1.base, "prophet": p2.base, "rf": p3.base},
    )


def update_ensemble_weights(mapes: list[float]) -> list[float]:
    """Inverse-MAPE weight update — mirrors updateEnsembleWeights() in forecasting.js."""
    inv = [1 / max(m, 0.01) for m in mapes]
    total = sum(inv)
    return [v / total for v in inv]


# ── FRED leading indicator analysis ───────────────────────────────────────────

def find_leading_indicators(
    kpi_values: list[float],
    fred_series: dict[str, list[float]],
    max_lag: int = 4,
) -> list[dict]:
    """
    Pearson r at each lag for each FRED series.
    Mirrors findLeadingIndicators() — but adds p-value (not available in the JS version).
    """
    from scipy.stats import pearsonr

    kpi = np.array(kpi_values)
    results = []
    for series_id, arr in fred_series.items():
        fred = np.array(arr)
        for lag in range(1, max_lag + 1):
            shifted = fred[: len(fred) - lag]
            aligned = kpi[lag:]
            n = min(len(shifted), len(aligned))
            if n < 4:
                continue
            r, p = pearsonr(shifted[-n:], aligned[-n:])
            if not np.isnan(r):
                results.append({
                    "id": series_id, "lag": lag,
                    "r": float(r), "abs_r": abs(float(r)),
                    "p_value": float(p),
                })
    return sorted(results, key=lambda x: -x["abs_r"])


# ── Plotly visualizations ─────────────────────────────────────────────────────

def plot_forecast(
    history: list[dict],
    forecast: list[dict],
    title: str = "Revenue Forecast",
    unit: str = "$M",
    accent: str = "#10b981",
) -> "go.Figure":
    """
    Interactive replacement for ForecastChart in charts.jsx.

    history : [{"q": "Q1 2024", "v": 4821}, ...]
    forecast: [{"q": "Q2 2025", "base": 5100, "lo": 4800, "hi": 5400}, ...]

    Adds over the SVG version:
      - Brush/range-slider for time-range zoom
      - Unified crosshair tooltip with CI values
      - Synchronized hover across subplots (when embedded in make_subplots)
      - Export to PNG via the Plotly toolbar
    """
    import plotly.graph_objects as go

    hx = [d["q"] for d in history]
    hy = [d["v"] for d in history]
    fx = [d["q"] for d in forecast]
    fy = [d["base"] for d in forecast]
    flo = [d["lo"] for d in forecast]
    fhi = [d["hi"] for d in forecast]

    tick_prefix = "$" if unit == "$M" else ""
    tick_suffix = "M" if unit == "$M" else "%"

    fig = go.Figure()

    # Confidence band
    fig.add_trace(go.Scatter(
        x=fx + fx[::-1],
        y=fhi + flo[::-1],
        fill="toself",
        fillcolor=f"rgba(16,185,129,0.10)",
        line=dict(color="rgba(0,0,0,0)"),
        hoverinfo="skip",
        name="95% CI",
        legendrank=3,
    ))

    # History line
    fig.add_trace(go.Scatter(
        x=hx, y=hy,
        mode="lines+markers",
        line=dict(color=accent, width=2),
        marker=dict(size=5, color=accent),
        name="Actual",
        hovertemplate=f"<b>%{{x}}</b><br>{unit}: %{{y:,.1f}}<extra></extra>",
        legendrank=1,
    ))

    # Bridge (last actual → first forecast)
    fig.add_trace(go.Scatter(
        x=[hx[-1], fx[0]], y=[hy[-1], fy[0]],
        mode="lines",
        line=dict(color=accent, width=2, dash="dash"),
        showlegend=False, hoverinfo="skip",
    ))

    # Forecast line
    fig.add_trace(go.Scatter(
        x=fx, y=fy,
        mode="lines+markers",
        line=dict(color=accent, width=2, dash="dash"),
        marker=dict(size=7, color="white", line=dict(color=accent, width=2)),
        name="Forecast",
        customdata=list(zip(flo, fhi)),
        hovertemplate=(
            f"<b>%{{x}}</b><br>{unit}: %{{y:,.1f}}<br>"
            "Range: %{customdata[0]:,.1f} – %{customdata[1]:,.1f}"
            "<extra></extra>"
        ),
        legendrank=2,
    ))

    # Forecast zone shading
    fig.add_vrect(
        x0=hx[-1], x1=fx[-1],
        fillcolor="rgba(16,185,129,0.04)",
        layer="below", line_width=0,
        annotation_text="FORECAST →",
        annotation_position="top left",
        annotation=dict(font_size=10, font_color="rgba(100,100,100,0.7)"),
    )

    fig.update_layout(
        title=dict(text=title, font=dict(size=14, family="Inter, system-ui, sans-serif")),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=64, r=20, t=52, b=40),
        xaxis=dict(
            showgrid=True, gridcolor="#f0f0f0",
            tickfont=dict(family="Geist Mono, monospace", size=10),
            rangeslider=dict(visible=True, thickness=0.06),
        ),
        yaxis=dict(
            showgrid=True, gridcolor="#f0f0f0",
            tickprefix=tick_prefix, ticksuffix=tick_suffix,
            tickfont=dict(family="Geist Mono, monospace", size=10),
        ),
    )

    return fig


def plot_ensemble_components(
    history: list[dict],
    result: ForecastResult,
    forecast_quarters: list[str],
    unit: str = "$M",
) -> "go.Figure":
    """
    Overlays ARIMA / Prophet / RF individual forecasts against the ensemble blend.
    Diagnostic view not present in the JS version.
    """
    import plotly.graph_objects as go

    COLOR = {"arima": "#6366f1", "prophet": "#f59e0b", "rf": "#3b82f6", "ensemble": "#10b981"}

    fig = go.Figure()

    # History
    fig.add_trace(go.Scatter(
        x=[d["q"] for d in history], y=[d["v"] for d in history],
        mode="lines+markers", line=dict(color="#111827", width=2.5),
        name="Actual",
    ))

    # Individual model traces
    for name, vals in result.components.items():
        if vals:
            fig.add_trace(go.Scatter(
                x=forecast_quarters, y=vals,
                mode="lines",
                line=dict(color=COLOR.get(name, "#999"), width=1.5, dash="dot"),
                name=name.upper(), opacity=0.75,
            ))

    # Ensemble blend (prominent)
    fig.add_trace(go.Scatter(
        x=forecast_quarters, y=result.base,
        mode="lines+markers",
        line=dict(color=COLOR["ensemble"], width=2.5),
        marker=dict(size=6),
        name="Ensemble",
    ))

    fig.update_layout(
        title="Ensemble Components",
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=64, r=20, t=52, b=40),
        xaxis=dict(showgrid=True, gridcolor="#f0f0f0",
                   tickfont=dict(family="Geist Mono, monospace", size=10)),
        yaxis=dict(showgrid=True, gridcolor="#f0f0f0",
                   tickfont=dict(family="Geist Mono, monospace", size=10)),
    )
    return fig


def plot_leading_indicators(
    indicators: list[dict],
    top_n: int = 8,
) -> "go.Figure":
    """
    Heatmap of FRED series × lag → Pearson r.
    Replaces leadingIndicatorMatrix() plain data return with a visual output.
    Green = positive lead, Red = negative lead.
    """
    import plotly.graph_objects as go

    top_ids = list(dict.fromkeys(d["id"] for d in indicators))[:top_n]
    lags = [1, 2, 3, 4]

    z, text = [], []
    for sid in top_ids:
        row, trow = [], []
        for lag in lags:
            entry = next((d for d in indicators if d["id"] == sid and d["lag"] == lag), None)
            r = entry["r"] if entry else 0.0
            row.append(r)
            sig = "**" if (entry and entry.get("p_value", 1) < 0.05) else ""
            trow.append(f"{r:+.2f}{sig}")
        z.append(row)
        text.append(trow)

    fig = go.Figure(go.Heatmap(
        z=z,
        x=[f"Lag {l}Q" for l in lags],
        y=top_ids,
        colorscale="RdYlGn",
        zmid=0, zmin=-1, zmax=1,
        text=text,
        texttemplate="%{text}",
        hoverongaps=False,
        colorbar=dict(title="Pearson r", tickfont=dict(size=10)),
    ))

    fig.update_layout(
        title="FRED Leading Indicators — Pearson r by Lag  (** p < 0.05)",
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        xaxis=dict(side="top", tickfont=dict(family="Geist Mono, monospace", size=10)),
        yaxis=dict(tickfont=dict(family="Geist Mono, monospace", size=10)),
        margin=dict(l=160, r=20, t=80, b=20),
        height=max(300, top_n * 44 + 100),
    )
    return fig


def plot_forecast_dashboard(
    revenue_history: list[dict],
    revenue_forecast: list[dict],
    margin_history: list[dict],
    margin_forecast: list[dict],
) -> "go.Figure":
    """
    Two-row synchronized dashboard: revenue (top) + margin (bottom).
    Both rows share the x-axis so hover crosshair is unified — this is not
    possible with the separate SVG ForecastChart instances in the React app.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("Revenue ($M)", "Gross Margin (%)"),
    )

    def add_series(row: int, history: list[dict], forecast: list[dict],
                   accent: str, unit: str) -> None:
        hx = [d["q"] for d in history]
        hy = [d["v"] for d in history]
        fx = [d["q"] for d in forecast]
        fy = [d["base"] for d in forecast]
        flo = [d["lo"] for d in forecast]
        fhi = [d["hi"] for d in forecast]

        # CI band
        fig.add_trace(go.Scatter(
            x=fx + fx[::-1], y=fhi + flo[::-1],
            fill="toself", fillcolor=f"rgba(16,185,129,0.10)" if accent == "#10b981" else "rgba(99,102,241,0.10)",
            line=dict(color="rgba(0,0,0,0)"), hoverinfo="skip",
            showlegend=False,
        ), row=row, col=1)

        # Actual
        fig.add_trace(go.Scatter(
            x=hx, y=hy, mode="lines+markers",
            line=dict(color=accent, width=2), marker=dict(size=4, color=accent),
            name=f"Actual {'Rev' if row == 1 else 'Margin'}",
            hovertemplate=f"%{{x}}: %{{y:,.1f}}{unit}<extra></extra>",
        ), row=row, col=1)

        # Forecast
        fig.add_trace(go.Scatter(
            x=fx, y=fy, mode="lines+markers",
            line=dict(color=accent, width=2, dash="dash"),
            marker=dict(size=6, color="white", line=dict(color=accent, width=2)),
            name=f"Forecast {'Rev' if row == 1 else 'Margin'}",
            customdata=list(zip(flo, fhi)),
            hovertemplate=f"%{{x}}: %{{y:,.1f}}{unit} (%{{customdata[0]:,.1f}}–%{{customdata[1]:,.1f}})<extra></extra>",
        ), row=row, col=1)

    add_series(1, revenue_history, revenue_forecast, "#10b981", "M")
    add_series(2, margin_history, margin_forecast, "#6366f1", "%")

    fig.update_layout(
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=64, r=20, t=60, b=40),
        height=480,
        xaxis2=dict(rangeslider=dict(visible=True, thickness=0.05)),
    )
    fig.update_xaxes(showgrid=True, gridcolor="#f0f0f0",
                     tickfont=dict(family="Geist Mono, monospace", size=10))
    fig.update_yaxes(showgrid=True, gridcolor="#f0f0f0",
                     tickfont=dict(family="Geist Mono, monospace", size=10))

    return fig
