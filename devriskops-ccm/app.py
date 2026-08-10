#!/usr/bin/env python3
"""
DevRiskOps CCM — Streamlit Auditor Workspace.

Three tabs:
    Tab 1  Exception Triage Queue   — pulls from backend_api.py (GET /triage/pending),
                                       posts decisions back through it (POST /triage/{event_id})
                                       so every write goes through the same validation and
                                       retrain-trigger logic every other caller uses.
    Tab 2  Model Analytics          — reads directly from Postgres (read-only, analytical
                                       aggregate queries — the standard BI-dashboard pattern,
                                       kept out of backend_api.py's request-scoped API surface).
    Tab 3  Feature Drift (PSI)      — reads control_events directly and computes PSI in-process
                                       via psi_monitor.calculate_psi, the identical methodology
                                       mcp_server.py's check_feature_drift tool and
                                       dags/ccm_dag.py's monitor_feature_drift task use.

Configuration (environment variables)
--------------------------------------
    CCM_BACKEND_API_URL   Default http://localhost:8000
    CCM_API_KEY            Must match backend_api.py's CCM_API_KEY
    DATABASE_URL             postgresql://user:pass@host:port/dbname

Run:
    streamlit run app.py
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import create_engine, text

from psi_monitor import calculate_psi, classify_psi, extract_feature_series

# ── Configuration ────────────────────────────────────────────────────────────
BACKEND_API_URL = os.environ.get("CCM_BACKEND_API_URL", "http://localhost:8000").rstrip("/")
CCM_API_KEY = os.environ.get("CCM_API_KEY", "dev-local-insecure-key-change-me")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ccm_user:ccm_password@localhost:5432/devriskops_ccm")
SQLALCHEMY_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

RESOLUTION_LABELS = [
    "TRUE_CONTROL_FAILURE", "BENIGN_OPERATIONAL_NOISE", "APPROVED_CARVE_OUT", "DATA_PIPELINE_ERROR",
]
NOTES_REQUIRED_LABELS = {"TRUE_CONTROL_FAILURE", "APPROVED_CARVE_OUT"}
SEVERITY_ICON = {"CRITICAL": "🔴", "WARNING": "🟠", "STABLE": "🟢"}

st.set_page_config(page_title="DevRiskOps CCM — Auditor Workspace", page_icon="🛡️", layout="wide")


@st.cache_resource
def get_engine():
    return create_engine(SQLALCHEMY_URL, pool_pre_ping=True)


def _api_headers() -> dict[str, str]:
    return {"X-API-Key": CCM_API_KEY}


@st.cache_data(ttl=30)
def fetch_pending_triage(limit: int) -> list[dict[str, Any]]:
    try:
        response = httpx.get(
            f"{BACKEND_API_URL}/api/v1/triage/pending",
            params={"limit": limit},
            headers=_api_headers(),
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        st.error(f"Could not reach the CCM backend API at {BACKEND_API_URL}: {exc}")
        return []


def submit_triage(event_id: str, auditor_id: str, label: str, notes: str) -> tuple[bool, str]:
    try:
        response = httpx.post(
            f"{BACKEND_API_URL}/api/v1/triage/{event_id}",
            json={"auditor_id": auditor_id, "resolution_label": label, "justification_notes": notes or None},
            headers=_api_headers(),
            timeout=15.0,
        )
        response.raise_for_status()
        return True, "Decision recorded."
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        try:
            detail = exc.response.json().get("detail", detail)
        except ValueError:
            pass
        return False, f"Backend rejected the decision ({exc.response.status_code}): {detail}"
    except httpx.HTTPError as exc:
        return False, f"Could not reach the CCM backend API: {exc}"


st.title("🛡️ DevRiskOps — Continuous Control Monitoring")
st.caption("Auditor workspace for exception triage, model analytics, and feature drift monitoring.")

tab_triage, tab_analytics, tab_drift = st.tabs(
    ["📋 Exception Triage Queue", "📊 Model Analytics", "📉 Feature Drift (PSI)"]
)

# ═══════════════════════════════ TAB 1 — Exception Triage Queue ══════════════
with tab_triage:
    st.subheader("Pending Exceptions Requiring Human Review")

    col_limit, col_uncertainty, col_refresh = st.columns([1, 1, 1])
    with col_limit:
        queue_limit = st.number_input("Max items to load", min_value=10, max_value=1000, value=200, step=10)
    with col_uncertainty:
        min_uncertainty = st.slider("Min uncertainty score", 0.0, 1.0, 0.0, 0.05)
    with col_refresh:
        st.write("")
        st.write("")
        if st.button("🔄 Refresh queue"):
            fetch_pending_triage.clear()

    queue = fetch_pending_triage(limit=int(queue_limit))
    queue = [item for item in queue if item["uncertainty_score"] >= min_uncertainty]

    metric_count, metric_anomaly, metric_uncertainty = st.columns(3)
    metric_count.metric("Pending items", len(queue))
    metric_anomaly.metric(
        "Avg anomaly score",
        f"{np.mean([q['anomaly_score'] for q in queue]):.3f}" if queue else "—",
    )
    metric_uncertainty.metric(
        "Avg uncertainty score",
        f"{np.mean([q['uncertainty_score'] for q in queue]):.3f}" if queue else "—",
    )

    st.divider()

    if not queue:
        st.success("No pending exceptions in the current filter — queue is clear.")
    else:
        for item in queue:
            header = (
                f"`{item['control_id']}` · {item['system_source']} · "
                f"anomaly {item['anomaly_score']:.3f} · uncertainty {item['uncertainty_score']:.3f} · "
                f"{item['event_timestamp']}"
            )
            with st.expander(header):
                left_col, right_col = st.columns([1, 1])

                with left_col:
                    item_metric_1, item_metric_2 = st.columns(2)
                    item_metric_1.metric("Anomaly Score", f"{item['anomaly_score']:.4f}")
                    item_metric_2.metric("Uncertainty Score", f"{item['uncertainty_score']:.4f}")
                    st.markdown("**Point-in-time features (frozen at scoring time)**")
                    st.json(item["point_in_time_features"])
                    st.caption(f"event_id: `{item['event_id']}` · model_version: `{item['model_version']}`")

                with right_col:
                    st.markdown("**Auditor decision**")
                    with st.form(key=f"triage_form_{item['event_id']}"):
                        auditor_id = st.text_input("Auditor ID", key=f"auditor_{item['event_id']}")
                        label = st.radio(
                            "Resolution",
                            options=RESOLUTION_LABELS,
                            key=f"label_{item['event_id']}",
                            format_func=lambda v: v.replace("_", " ").title(),
                        )
                        notes = st.text_area(
                            "Justification notes (required for Control Failure / Carve-Out)",
                            key=f"notes_{item['event_id']}",
                        )
                        submitted = st.form_submit_button("Submit decision")

                        if submitted:
                            if not auditor_id.strip():
                                st.error("Auditor ID is required.")
                            elif label in NOTES_REQUIRED_LABELS and not notes.strip():
                                st.error("Justification notes are required for this resolution label.")
                            else:
                                ok, message = submit_triage(item["event_id"], auditor_id.strip(), label, notes.strip())
                                if ok:
                                    st.success(message)
                                    fetch_pending_triage.clear()
                                    st.rerun()
                                else:
                                    st.error(message)

# ═══════════════════════════════ TAB 2 — Model Analytics ═════════════════════
with tab_analytics:
    st.subheader("Model Analytics")

    lookback_days = st.slider("Lookback window (days)", 7, 90, 30, key="analytics_lookback")

    @st.cache_data(ttl=60)
    def load_triage_distribution(days: int) -> pd.DataFrame:
        engine = get_engine()
        query = text(
            """
            SELECT date_trunc('day', reviewed_at) AS review_date, resolution_label, COUNT(*) AS label_count
            FROM auditor_triage
            WHERE reviewed_at >= now() - (:days || ' days')::interval
            GROUP BY 1, 2
            ORDER BY 1
            """
        )
        with engine.connect() as conn:
            return pd.read_sql_query(query, conn, params={"days": days})

    @st.cache_data(ttl=60)
    def load_false_positive_trend(days: int) -> pd.DataFrame:
        engine = get_engine()
        query = text(
            """
            SELECT
                date_trunc('day', reviewed_at) AS review_date,
                COUNT(*) FILTER (WHERE resolution_label = 'BENIGN_OPERATIONAL_NOISE') AS false_positives,
                COUNT(*) FILTER (WHERE resolution_label <> 'DATA_PIPELINE_ERROR') AS total_reviewed
            FROM auditor_triage
            WHERE reviewed_at >= now() - (:days || ' days')::interval
            GROUP BY 1
            ORDER BY 1
            """
        )
        with engine.connect() as conn:
            frame = pd.read_sql_query(query, conn, params={"days": days})
        if frame.empty:
            return frame
        frame["fp_rate"] = np.where(
            frame["total_reviewed"] > 0, frame["false_positives"] / frame["total_reviewed"], np.nan
        )
        frame["fp_rate_7d_ma"] = frame["fp_rate"].rolling(window=7, min_periods=1).mean()
        return frame

    distribution_df = load_triage_distribution(lookback_days)
    fp_trend_df = load_false_positive_trend(lookback_days)

    st.markdown(f"**{lookback_days}-Day Triage Label Distribution**")
    if distribution_df.empty:
        st.info("No triage decisions recorded in this window yet.")
    else:
        fig_distribution = px.bar(
            distribution_df, x="review_date", y="label_count", color="resolution_label",
            barmode="stack",
            labels={"review_date": "Date", "label_count": "Labels", "resolution_label": "Resolution"},
            title=f"Daily Triage Volume by Label — Trailing {lookback_days} Days",
        )
        st.plotly_chart(fig_distribution, use_container_width=True)

    st.markdown("**False Positive Rate — 7-Day Moving Average**")
    if fp_trend_df.empty:
        st.info("No reviewed events in this window yet.")
    else:
        fig_fp = go.Figure()
        fig_fp.add_trace(go.Scatter(
            x=fp_trend_df["review_date"], y=fp_trend_df["fp_rate"], mode="markers",
            name="Daily FP rate", marker=dict(size=6, opacity=0.5),
        ))
        fig_fp.add_trace(go.Scatter(
            x=fp_trend_df["review_date"], y=fp_trend_df["fp_rate_7d_ma"], mode="lines",
            name="7-day moving average", line=dict(width=3),
        ))
        fig_fp.update_layout(
            yaxis_tickformat=".0%", yaxis_title="False positive rate", xaxis_title="Date",
            title="Benign Operational Noise Rate Among Auditor Decisions",
        )
        st.plotly_chart(fig_fp, use_container_width=True)

# ═══════════════════════════════ TAB 3 — Feature Drift (PSI) ═════════════════
with tab_drift:
    st.subheader("Feature Drift — Population Stability Index")

    win_col_1, win_col_2, win_col_3 = st.columns(3)
    with win_col_1:
        baseline_start_days_ago = st.number_input("Baseline window start (days ago)", min_value=1, value=37)
    with win_col_2:
        baseline_window_days = st.number_input("Baseline window length (days)", min_value=1, value=30)
    with win_col_3:
        target_window_days = st.number_input("Target window length (days, most recent)", min_value=1, value=7)

    @st.cache_data(ttl=120)
    def load_feature_frames(
        baseline_start: int, baseline_len: int, target_len: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        engine = get_engine()
        baseline_query = text(
            """
            SELECT point_in_time_features FROM control_events
            WHERE event_timestamp >= now() - ((:start + :length) || ' days')::interval
              AND event_timestamp <  now() - (:start || ' days')::interval
            """
        )
        target_query = text(
            "SELECT point_in_time_features FROM control_events WHERE event_timestamp >= now() - (:length || ' days')::interval"
        )
        with engine.connect() as conn:
            baseline_rows = conn.execute(baseline_query, {"start": baseline_start, "length": baseline_len}).fetchall()
            target_rows = conn.execute(target_query, {"length": target_len}).fetchall()
        return (
            [dict(row[0]) if row[0] else {} for row in baseline_rows],
            [dict(row[0]) if row[0] else {} for row in target_rows],
        )

    baseline_events, target_events = load_feature_frames(
        int(baseline_start_days_ago), int(baseline_window_days), int(target_window_days)
    )

    if not baseline_events or not target_events:
        st.warning("Not enough historical control_events in the selected windows to compute drift yet.")
    else:
        feature_names = sorted({
            key for event in baseline_events for key, value in event.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        })

        if not feature_names:
            st.warning("No numeric features found in point_in_time_features for the selected windows.")
        else:
            summary_rows: list[dict[str, Any]] = []
            per_feature_detail: dict[str, pd.DataFrame] = {}

            for feature_name in feature_names:
                baseline_arr = extract_feature_series(baseline_events, feature_name)
                target_arr = extract_feature_series(target_events, feature_name)
                if baseline_arr.size < 5 or target_arr.size < 5:
                    continue
                psi_score, detail_df = calculate_psi(baseline_arr, target_arr, num_bins=10)
                severity = classify_psi(psi_score)
                summary_rows.append({"feature": feature_name, "psi_score": psi_score, "severity": severity.value})
                per_feature_detail[feature_name] = detail_df

            if not summary_rows:
                st.warning("Not enough samples per feature to compute a stable PSI in the selected windows.")
            else:
                summary_df = pd.DataFrame(summary_rows).sort_values("psi_score", ascending=False).reset_index(drop=True)
                summary_df["status"] = summary_df["severity"].map(SEVERITY_ICON) + " " + summary_df["severity"]

                st.markdown("**Feature Drift Summary Matrix**")
                st.caption("🟢 Stable: PSI < 0.10   ·   🟠 Warning: 0.10 ≤ PSI < 0.25   ·   🔴 Critical: PSI ≥ 0.25")
                st.dataframe(
                    summary_df[["feature", "psi_score", "status"]].rename(
                        columns={"feature": "Feature", "psi_score": "PSI Score", "status": "Status"}
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

                n_critical = int((summary_df["severity"] == "CRITICAL").sum())
                n_warning = int((summary_df["severity"] == "WARNING").sum())
                n_stable = len(summary_df) - n_critical - n_warning
                metric_critical, metric_warning, metric_stable = st.columns(3)
                metric_critical.metric("🔴 Critical", n_critical)
                metric_warning.metric("🟠 Warning", n_warning)
                metric_stable.metric("🟢 Stable", n_stable)

                st.divider()
                st.markdown("**Baseline vs. Target Quantile Distribution**")
                selected_feature = st.selectbox("Select a feature to inspect", options=summary_df["feature"].tolist())
                detail_df = per_feature_detail[selected_feature]
                selected_psi = float(summary_df.set_index("feature").loc[selected_feature, "psi_score"])

                fig_psi = go.Figure()
                fig_psi.add_trace(go.Bar(x=detail_df["bin"], y=detail_df["baseline_pct"], name="Baseline"))
                fig_psi.add_trace(go.Bar(x=detail_df["bin"], y=detail_df["target_pct"], name="Target"))
                fig_psi.update_layout(
                    barmode="group",
                    yaxis_tickformat=".0%",
                    title=f"Quantile Distribution — {selected_feature} (PSI = {selected_psi:.4f})",
                    xaxis_title="Quantile Bin",
                    yaxis_title="Population Share",
                )
                st.plotly_chart(fig_psi, use_container_width=True)
                st.dataframe(detail_df, use_container_width=True, hide_index=True)
