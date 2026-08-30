"""
Tests for risk_rating_mcp_server.py — the canonical Risk Rating MCP server.

FastMCP tools remain plain Python functions under their @mcp.tool()
decorator, so these are called directly (no HTTP layer, no MCP transport) —
same approach as calling exception_tool.score_event directly in
test_exception_tool.py. db.get_risk_scores_for_run is faked at the
boundary — no real database.

    pytest test_risk_rating_mcp_server.py -v
"""
from __future__ import annotations

import json

import risk_rating_mcp_server as rms


def test_risk_rating_thresholds_returns_the_canonical_bands():
    result = json.loads(rms.risk_rating_thresholds())
    assert result["rag_bands"]["R"] == ">= 15.0"
    assert result["rag_bands"]["A"] == ">= 9.0"
    assert result["category_impact"]["Cybersecurity"] == 4


def test_risk_score_exception_returns_a_scored_shape():
    result = json.loads(rms.risk_score_exception("CRITICAL", process="record_to_report", connector_risk_tier="high"))
    assert result["severity"] == "CRITICAL"
    assert result["process"] == "record_to_report"
    assert 0 < result["score"] <= 25
    assert result["rag_status"] in ("R", "A", "G")


def test_risk_score_exception_defaults_process_and_tier():
    result = json.loads(rms.risk_score_exception("LOW"))
    assert result["score"] > 0


def test_risk_score_register_returns_the_canonical_scale(monkeypatch):
    result = json.loads(rms.risk_score_register(
        json.dumps({"revenue_growth": -0.12}), "Semiconductors"))
    assert result["industry"] == "Semiconductors"
    assert set(result["rag_summary"].keys()) == {"R", "A", "G"}
    for r in result["risks"]:
        assert r["rag_status"] in ("R", "A", "G")
        assert 0 <= r["score"] <= 25


def test_risk_score_register_accepts_a_dict_not_just_a_json_string():
    # The MCP client boundary always sends a JSON string, but calling the
    # plain function directly (as these tests do) with a dict must not crash.
    result = json.loads(rms.risk_score_register({"revenue_growth": 0.05}, "Generic"))
    assert result["industry"] == "Generic"


def test_risk_score_register_bad_json_returns_an_error_string_not_a_crash():
    result = rms.risk_score_exception  # sanity import check
    assert callable(result)
    out = rms.risk_score_register("{not valid json", "Generic")
    assert out.startswith("Error:")


def test_risk_register_for_run_wraps_db_get_risk_scores_for_run(monkeypatch):
    fake_risks = [
        {"risk_ref": "R-01", "name": "Test Risk", "score": 20.0, "rag_status": "R", "velocity": 2},
        {"risk_ref": "R-02", "name": "Other Risk", "score": 5.0, "rag_status": "G", "velocity": 0},
    ]
    monkeypatch.setattr(rms.db, "is_available", lambda: True)
    monkeypatch.setattr(rms.db, "get_risk_scores_for_run", lambda run_id: fake_risks)

    result = json.loads(rms.risk_register_for_run(42))

    assert result["run_id"] == 42
    assert result["risks"] == fake_risks
    assert result["rag_summary"] == {"R": 1, "A": 0, "G": 1}


def test_risk_register_for_run_no_db_returns_empty_note(monkeypatch):
    monkeypatch.setattr(rms.db, "is_available", lambda: False)
    result = json.loads(rms.risk_register_for_run(42))
    assert result["risks"] == []
    assert "note" in result


def test_risk_register_for_run_unrecognized_rag_status_counts_as_green(monkeypatch):
    """A row from before letters were canonical (or any stray value) must not
    raise a KeyError building rag_summary — group it under G rather than crash."""
    monkeypatch.setattr(rms.db, "is_available", lambda: True)
    monkeypatch.setattr(rms.db, "get_risk_scores_for_run", lambda run_id: [
        {"risk_ref": "R-01", "score": 6.0, "rag_status": "Amber"},  # legacy full-word, unrecognized here
    ])
    result = json.loads(rms.risk_register_for_run(1))
    assert result["rag_summary"]["G"] == 1
