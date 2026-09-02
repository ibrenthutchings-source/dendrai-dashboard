#!/usr/bin/env python3
"""
Tests for the drift -> re-optimization loop's target-company scoping:
db.get_target_ticker() and reoptimization_tool.run_reoptimization_sweep().

Regression coverage for a real bug: run_reoptimization_sweep used to sweep
db.list_active_tickers() — every ticker with any completed risk_loop_runs
row in the last 90 days, SIC peer/benchmark tickers (sic_peers) included —
instead of scoping to Mission Control's actual target company
(app_config['pipeline_config'].cfg.ticker). On a real deployment this both
diluted the signal (re-optimizing peer companies alongside the real target
on every drift trigger) and failed outright for every one of them, before
risk_loop_runs.data_mode's VARCHAR(8) -> VARCHAR(16) widening (data_mode
'reoptimize' is 10 chars).

    pytest test_reoptimization_tool.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import db
import reoptimization_tool as rt


class TestGetTargetTicker:
    def test_none_when_no_pipeline_config_saved(self):
        with patch.object(db, "get_app_config", return_value=None):
            assert db.get_target_ticker() is None

    def test_none_when_cfg_has_no_ticker(self):
        with patch.object(db, "get_app_config", return_value={"cfg": {}}):
            assert db.get_target_ticker() is None

    def test_none_when_ticker_is_blank(self):
        with patch.object(db, "get_app_config", return_value={"cfg": {"ticker": "   "}}):
            assert db.get_target_ticker() is None

    def test_returns_uppercased_stripped_ticker(self):
        with patch.object(db, "get_app_config", return_value={"cfg": {"ticker": " on "}}):
            assert db.get_target_ticker() == "ON"

    def test_looks_up_the_pipeline_config_key(self):
        captured = {}
        def _fake_get(key, default=None):
            captured["key"] = key
            return {"cfg": {"ticker": "ON"}}
        with patch.object(db, "get_app_config", side_effect=_fake_get):
            db.get_target_ticker()
        assert captured["key"] == "pipeline_config"


class TestRunReoptimizationSweep:
    def test_no_target_configured_returns_empty_summary_without_reoptimizing(self):
        with patch.object(rt.db, "get_target_ticker", return_value=None), \
             patch.object(rt, "reoptimize_ticker") as mock_reopt:
            summary = rt.run_reoptimization_sweep(trigger_reason="drift_auto_reoptimize")
        mock_reopt.assert_not_called()
        assert summary == {
            "trigger_reason": "drift_auto_reoptimize",
            "tickers_attempted": 0, "succeeded": 0, "failed": 0, "results": [],
        }

    def test_reoptimizes_only_the_target_ticker_not_a_swept_set(self):
        with patch.object(rt.db, "get_target_ticker", return_value="ON"), \
             patch.object(rt, "reoptimize_ticker", return_value={"ticker": "ON", "success": True}) as mock_reopt:
            summary = rt.run_reoptimization_sweep(trigger_reason="manual_review", trigger_incident_id=None)
        mock_reopt.assert_called_once_with("ON", trigger_reason="manual_review", trigger_incident_id=None)
        assert summary["tickers_attempted"] == 1
        assert summary["succeeded"] == 1
        assert summary["failed"] == 0
        assert [r["ticker"] for r in summary["results"]] == ["ON"]

    def test_a_failed_reoptimize_still_returns_a_summary_not_an_exception(self):
        with patch.object(rt.db, "get_target_ticker", return_value="ON"), \
             patch.object(rt, "reoptimize_ticker", side_effect=RuntimeError("EDGAR down")):
            summary = rt.run_reoptimization_sweep()
        assert summary["tickers_attempted"] == 1
        assert summary["succeeded"] == 0
        assert summary["failed"] == 1
        assert "EDGAR down" in summary["results"][0]["error"]
