#!/usr/bin/env python3
"""
DevRiskOps CCM — Population Stability Index (PSI) Feature Drift Engine.

Pure, dependency-light drift math (numpy/pandas only) plus a thin webhook
dispatcher, so the exact same PSI computation is shared by every consumer:
app.py's "Feature Drift (PSI)" tab, mcp_server.py's check_feature_drift
tool, and dags/ccm_dag.py's monitor_feature_drift task.

PSI methodology
----------------
Bin edges are derived from the BASELINE distribution's quantiles (so every
bin holds an equal share of the baseline population by construction), then
both baseline and target populations are re-histogrammed against those same
edges. PSI is the sum, over bins, of (target_pct - baseline_pct) *
ln(target_pct / baseline_pct) — the symmetric KL-divergence-like measure
standard in credit-risk and ML-ops model monitoring. Conventional cutoffs:

    PSI < 0.10              stable — no action needed
    0.10 <= PSI < 0.25       warning — investigate, watch closely
    PSI >= 0.25              critical — feature distribution has materially
                             shifted; the model's input assumptions may no
                             longer hold
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import httpx
import numpy as np
import pandas as pd

logger = logging.getLogger("devriskops.ccm.psi_monitor")

PSI_WARNING_THRESHOLD = 0.10
PSI_CRITICAL_THRESHOLD = 0.25
_EPSILON = 1e-4  # smoothing floor so a zero-count bin never produces a divide-by-zero / log(0)


class DriftSeverity(str, Enum):
    STABLE = "STABLE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


def classify_psi(
    psi_score: float,
    warning_threshold: float = PSI_WARNING_THRESHOLD,
    critical_threshold: float = PSI_CRITICAL_THRESHOLD,
) -> DriftSeverity:
    """Maps a raw PSI score to the standard three-tier severity band."""
    if psi_score >= critical_threshold:
        return DriftSeverity.CRITICAL
    if psi_score >= warning_threshold:
        return DriftSeverity.WARNING
    return DriftSeverity.STABLE


def calculate_psi(
    baseline: np.ndarray,
    target: np.ndarray,
    num_bins: int = 10,
) -> tuple[float, pd.DataFrame]:
    """Computes the Population Stability Index between a baseline and a
    target sample of the same continuous feature, using quantile binning
    derived from `baseline` with epsilon smoothing for empty bins.

    Args:
        baseline: 1-D array of the feature's historical/reference values.
        target: 1-D array of the feature's current/recent values.
        num_bins: Number of quantile bins to derive from `baseline`. The
            actual bin count may end up smaller if `baseline` has too few
            distinct values to support `num_bins` distinct quantile edges
            (e.g. a highly discrete or constant feature).

    Returns:
        (total_psi, detail_df) — the scalar PSI score, and a per-bin
        DataFrame (bin label, edges, counts, population shares, and each
        bin's individual PSI contribution) suitable for a grouped bar chart.

    Raises:
        ValueError: if either array has zero non-NaN observations.
    """
    baseline_arr = np.asarray(baseline, dtype=float)
    target_arr = np.asarray(target, dtype=float)
    baseline_arr = baseline_arr[~np.isnan(baseline_arr)]
    target_arr = target_arr[~np.isnan(target_arr)]

    if baseline_arr.size == 0 or target_arr.size == 0:
        raise ValueError("calculate_psi: baseline and target must each contain at least one non-NaN value.")

    quantile_points = np.linspace(0, 100, num_bins + 1)
    bin_edges = np.unique(np.percentile(baseline_arr, quantile_points))

    # A highly discrete or near-constant baseline can collapse quantile
    # edges below num_bins + 1 distinct values — fall back to a single bin
    # spanning the observed range rather than letting np.histogram choke on
    # a degenerate (or single-value) edge array.
    if bin_edges.size < 2:
        lo = float(baseline_arr.min())
        hi = float(baseline_arr.max())
        if lo == hi:
            lo, hi = lo - 0.5, hi + 0.5
        bin_edges = np.array([lo, hi])

    # Open-ended outer edges: a target value below the baseline's historical
    # min or above its historical max is exactly the kind of shift PSI
    # should capture, not silently drop from the histogram.
    bin_edges = bin_edges.copy()
    bin_edges[0] = -np.inf
    bin_edges[-1] = np.inf

    baseline_counts, _ = np.histogram(baseline_arr, bins=bin_edges)
    target_counts, _ = np.histogram(target_arr, bins=bin_edges)

    baseline_pct = baseline_counts / baseline_counts.sum()
    target_pct = target_counts / target_counts.sum()
    baseline_pct_smoothed = np.where(baseline_pct == 0, _EPSILON, baseline_pct)
    target_pct_smoothed = np.where(target_pct == 0, _EPSILON, target_pct)

    psi_per_bin = (target_pct_smoothed - baseline_pct_smoothed) * np.log(
        target_pct_smoothed / baseline_pct_smoothed
    )

    detail_df = pd.DataFrame({
        "bin": [f"Bin {i + 1}" for i in range(len(psi_per_bin))],
        "bin_lower": bin_edges[:-1],
        "bin_upper": bin_edges[1:],
        "baseline_count": baseline_counts,
        "target_count": target_counts,
        "baseline_pct": baseline_pct,
        "target_pct": target_pct,
        "psi_contribution": psi_per_bin,
    })

    total_psi = float(np.sum(psi_per_bin))
    return total_psi, detail_df


def extract_feature_series(events: list[dict[str, Any]], feature_name: str) -> np.ndarray:
    """Pulls one numeric feature out of a list of point_in_time_features
    dicts (as stored in control_events.point_in_time_features), silently
    skipping events where the feature is missing, null, or non-numeric.
    Booleans are excluded even though `bool` is technically an `int`
    subclass in Python — a True/False feature isn't a continuous quantity
    PSI's quantile-binning methodology is meaningful for.
    """
    values: list[float] = []
    for event in events:
        raw_value = event.get(feature_name)
        if raw_value is None or isinstance(raw_value, bool):
            continue
        try:
            values.append(float(raw_value))
        except (TypeError, ValueError):
            continue
    return np.array(values, dtype=float)


@dataclass
class FeatureDriftResult:
    feature_name: str
    psi_score: float
    severity: DriftSeverity
    detail: pd.DataFrame
    baseline_sample_size: int
    target_sample_size: int


class PSIMonitorService:
    """Evaluates a {feature_name: (baseline_array, target_array)} mapping
    for population stability drift and — the moment any feature's PSI
    crosses the critical threshold — dispatches a webhook alert (Slack
    Block Kit by default, a Microsoft Teams Adaptive Card when
    webhook_style="teams") summarizing every critical feature in one message.
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        webhook_style: str = "slack",
        num_bins: int = 10,
        warning_threshold: float = PSI_WARNING_THRESHOLD,
        critical_threshold: float = PSI_CRITICAL_THRESHOLD,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.webhook_style = webhook_style.lower().strip()
        self.num_bins = num_bins
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self._client = http_client

    def evaluate(
        self, feature_distributions: dict[str, tuple[np.ndarray, np.ndarray]]
    ) -> dict[str, FeatureDriftResult]:
        """Computes PSI for every feature in `feature_distributions`,
        classifies severity, and fires a single consolidated webhook alert
        if one or more features came back CRITICAL. Returns every feature's
        result regardless of severity — callers decide what to do with
        WARNING/STABLE results (e.g. app.py renders all of them in a table)."""
        results: dict[str, FeatureDriftResult] = {}
        for feature_name, (baseline_arr, target_arr) in feature_distributions.items():
            psi_score, detail_df = calculate_psi(baseline_arr, target_arr, num_bins=self.num_bins)
            severity = classify_psi(psi_score, self.warning_threshold, self.critical_threshold)
            results[feature_name] = FeatureDriftResult(
                feature_name=feature_name,
                psi_score=psi_score,
                severity=severity,
                detail=detail_df,
                baseline_sample_size=int(len(baseline_arr)),
                target_sample_size=int(len(target_arr)),
            )

        critical_results = [r for r in results.values() if r.severity == DriftSeverity.CRITICAL]
        if critical_results:
            self._dispatch_alert(critical_results)
        return results

    def _dispatch_alert(self, critical_results: list[FeatureDriftResult]) -> None:
        summary = ", ".join(f"{r.feature_name}={r.psi_score:.4f}" for r in critical_results)
        if not self.webhook_url:
            logger.warning(
                "PSI CRITICAL drift on %d feature(s), no webhook_url configured: %s",
                len(critical_results), summary,
            )
            return

        payload = (
            self._build_teams_payload(critical_results)
            if self.webhook_style == "teams"
            else self._build_slack_payload(critical_results)
        )

        client = self._client or httpx.Client(timeout=10.0)
        owns_client = self._client is None
        try:
            response = client.post(self.webhook_url, json=payload)
            response.raise_for_status()
            logger.info("PSI drift webhook dispatched (%s) for %d critical feature(s).", self.webhook_style, len(critical_results))
        except httpx.HTTPError:
            logger.exception("PSI drift webhook dispatch failed: %s", summary)
        finally:
            if owns_client:
                client.close()

    def _build_slack_payload(self, results: list[FeatureDriftResult]) -> dict[str, Any]:
        lines = [
            f"*{r.feature_name}* — PSI `{r.psi_score:.4f}` "
            f"(baseline n={r.baseline_sample_size}, target n={r.target_sample_size})"
            for r in results
        ]
        return {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "🚨 CCM Feature Drift — Critical PSI", "emoji": True},
                },
                {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"Threshold: >= {self.critical_threshold:.2f} · Evaluated {datetime.now(timezone.utc).isoformat()}"}
                    ],
                },
            ]
        }

    def _build_teams_payload(self, results: list[FeatureDriftResult]) -> dict[str, Any]:
        facts = [
            {"title": r.feature_name, "value": f"PSI {r.psi_score:.4f} (n_base={r.baseline_sample_size}, n_target={r.target_sample_size})"}
            for r in results
        ]
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": "CCM Feature Drift — Critical PSI",
                                "weight": "Bolder",
                                "size": "Medium",
                                "color": "Attention",
                            },
                            {"type": "FactSet", "facts": facts},
                            {
                                "type": "TextBlock",
                                "text": f"Threshold: >= {self.critical_threshold:.2f} · Evaluated {datetime.now(timezone.utc).isoformat()}",
                                "isSubtle": True,
                                "wrap": True,
                            },
                        ],
                    },
                }
            ],
        }
