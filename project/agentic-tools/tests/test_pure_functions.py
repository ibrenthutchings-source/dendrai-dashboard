"""
Fast, DB-free unit tests for pure helper functions across agentic-tools.

Deliberately targets the exact class of bug this codebase has hit in
production: silent regex/format mismatches (Rego code-fence stripping),
VARCHAR-width truncation, and case-sensitive RAG-status comparisons. None
of these tests touch a database or network — they run in every CI push.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pac_endpoints
import db
import drift_tool
from predictive_analytics_tool import compute_grey_swan
from api_server import _build_digest_payload, _DIGEST_INTERVALS


# ── pac_endpoints._extract_control_id ───────────────────────────────────────

def test_extract_control_id_simple():
    assert pac_endpoints._extract_control_id("SOX-01: missing approval") == "SOX-01"


def test_extract_control_id_no_match():
    assert pac_endpoints._extract_control_id("no leading id here") is None


def test_extract_control_id_non_string():
    assert pac_endpoints._extract_control_id(None) is None
    assert pac_endpoints._extract_control_id(42) is None


def test_extract_control_id_list_binding():
    # OPA's set-valued binding for a partial-set rule comes back as a list/set.
    assert pac_endpoints._extract_control_id(["ITGC-04: change without ticket"]) == "ITGC-04"
    assert pac_endpoints._extract_control_id([]) is None


def test_extract_control_id_lowercase_prefix_rejected():
    # _CONTROL_ID_RE requires [A-Z0-9-]+ — a lowercase prefix must not match.
    assert pac_endpoints._extract_control_id("sox-01: lowercase") is None


# ── pac_endpoints._strip_code_fence — regression test for the anchored-regex bug ──

def test_strip_code_fence_plain():
    raw = "```rego\npackage x\ndeny_x[msg] { true }\n```"
    assert pac_endpoints._strip_code_fence(raw) == 'package x\ndeny_x[msg] { true }'


def test_strip_code_fence_with_preamble():
    # The exact failure mode from the real production bug: an LLM preamble
    # sentence before the fence made the old ^...$-anchored regex silently
    # fail to match, falling through to validating prose+fence as Rego.
    raw = 'Here is the converted policy:\n```rego\npackage x\ndeny_x[msg] { true }\n```'
    assert pac_endpoints._strip_code_fence(raw) == 'package x\ndeny_x[msg] { true }'


def test_strip_code_fence_no_fence_passthrough():
    raw = "package x\ndeny_x[msg] { true }"
    assert pac_endpoints._strip_code_fence(raw) == raw


def test_strip_code_fence_no_language_tag():
    raw = "```\npackage x\n```"
    assert pac_endpoints._strip_code_fence(raw) == "package x"


# ── pac_endpoints._looks_like_rego ──────────────────────────────────────────

def test_looks_like_rego_true():
    assert pac_endpoints._looks_like_rego("package sox\ndeny_missing_control[msg] { true }")


def test_looks_like_rego_false_no_package():
    assert not pac_endpoints._looks_like_rego("deny_x[msg] { true }")


def test_looks_like_rego_false_no_deny():
    assert not pac_endpoints._looks_like_rego("package sox\nallow { true }")


def test_looks_like_rego_false_prose():
    assert not pac_endpoints._looks_like_rego("This document describes our SOX controls.")


# ── pac_endpoints._rule_coverage ────────────────────────────────────────────

def test_rule_coverage_full():
    rego = (
        'package sox\n'
        'deny_missing_approval[msg] if {\n'
        '  input.approved == false\n'
        '  msg := sprintf("SOX-01: missing approval for %v", [input.id])\n'
        '}\n'
    )
    cov = pac_endpoints._rule_coverage(rego)
    assert cov == {"total": 1, "with_control_id": 1}


def test_rule_coverage_missing_id():
    rego = (
        'package sox\n'
        'deny_missing_approval[msg] if {\n'
        '  input.approved == false\n'
        '  msg := "missing approval, no control id here"\n'
        '}\n'
    )
    cov = pac_endpoints._rule_coverage(rego)
    assert cov == {"total": 1, "with_control_id": 0}


def test_rule_coverage_empty_module():
    assert pac_endpoints._rule_coverage("") == {"total": 0, "with_control_id": 0}


# ── predictive_analytics_tool.compute_grey_swan — RAG boundary + impact tier ──

def _risk(name="R1", category="Financial Reporting", score=5.0, velocity=1, rag_status="Amber"):
    return {"name": name, "category": category, "score": score, "velocity": velocity, "rag_status": rag_status}


def test_grey_swan_picks_highest_velocity_amber():
    risks = [
        _risk(name="low-vel", score=5.0, velocity=1, rag_status="Amber"),
        _risk(name="high-vel", score=4.5, velocity=3, rag_status="Amber"),
        _risk(name="red", score=8.0, velocity=5, rag_status="Red"),
    ]
    result = compute_grey_swan({"risks": risks})
    assert result["trigger_risk"] == "high-vel"


def test_grey_swan_rag_boundaries_exact():
    # rag_status = Red if score >= 7.0, Amber if >= 5.0, else Green — exact
    # boundary values are exactly the kind of off-by-one this session's RAG
    # case-mismatch bug (Feature 4) fell into.
    risks = [_risk(score=4.0, velocity=1, rag_status="Amber")]
    result = compute_grey_swan({"risks": risks})
    scores_to_rag = {s["score"]: s["rag_status"] for s in result["timeline"]}
    for score, rag in scores_to_rag.items():
        expected = "Red" if score >= 7.0 else ("Amber" if score >= 5.0 else "Green")
        assert rag == expected, f"score {score} expected {expected}, got {rag}"


def test_grey_swan_no_risks_returns_error():
    assert compute_grey_swan({"risks": []}) == {"error": "no risks available for grey swan model"}


def test_grey_swan_falls_back_to_highest_score_when_no_amber():
    risks = [
        _risk(name="green", score=2.0, velocity=1, rag_status="Green"),
        _risk(name="red-highest", score=8.0, velocity=1, rag_status="Red"),
    ]
    result = compute_grey_swan({"risks": risks})
    assert result["trigger_risk"] == "red-highest"


# ── api_server._build_digest_payload (Feature 5) ────────────────────────────

def _posture_row(run_id, avg_score, red=0, amber=0, green=0, risk_count=None):
    return {
        "run_id": run_id, "avg_score": avg_score,
        "red_count": red, "amber_count": amber, "green_count": green,
        "risk_count": risk_count if risk_count is not None else red + amber + green,
    }


def test_build_digest_payload_first_snapshot_no_prior():
    to_row = _posture_row(1, 5.5, amber=4, green=4)
    payload = _build_digest_payload("ON", None, to_row)
    assert payload["avg_score_delta"] is None
    assert payload["red_delta"] is None
    assert "first posture snapshot" in payload["headline"]


def test_build_digest_payload_worsened():
    from_row = _posture_row(1, 5.0, amber=4, green=4)
    to_row = _posture_row(2, 5.8, red=1, amber=3, green=4)
    payload = _build_digest_payload("ON", from_row, to_row)
    assert round(payload["avg_score_delta"], 2) == 0.80
    assert payload["red_delta"] == 1
    assert payload["amber_delta"] == -1
    assert payload["green_delta"] == 0
    assert "worsened" in payload["headline"]


def test_build_digest_payload_improved():
    from_row = _posture_row(1, 6.0, red=1, amber=3, green=4)
    to_row = _posture_row(2, 5.0, amber=4, green=4)
    payload = _build_digest_payload("ON", from_row, to_row)
    assert round(payload["avg_score_delta"], 2) == -1.0
    assert "improved" in payload["headline"]


def test_build_digest_payload_unchanged():
    from_row = _posture_row(1, 5.0, amber=4, green=4)
    to_row = _posture_row(2, 5.0, amber=4, green=4)
    payload = _build_digest_payload("ON", from_row, to_row)
    assert payload["avg_score_delta"] == 0
    assert "unchanged" in payload["headline"]
    assert payload["red_delta"] == 0 and payload["amber_delta"] == 0 and payload["green_delta"] == 0


def test_digest_intervals_ordering():
    assert _DIGEST_INTERVALS["daily"] < _DIGEST_INTERVALS["weekly"]


# ── db.py DDL — regression guard for the VARCHAR(16) truncation bug ────────

def test_pac_processes_source_column_wide_enough_for_known_values():
    """
    Real production bug (this session): pac_processes.source was VARCHAR(16),
    one character too narrow for 'github_discovered' (17 chars), causing
    every GitHub-discovered process sync to fail with a DB insert error.
    Guards against that regressing silently.
    """
    m = re.search(r"CREATE TABLE IF NOT EXISTS pac_processes\s*\(.*?\);", db._DDL, re.DOTALL)
    assert m, "pac_processes table definition not found in db._DDL"
    table_def = m.group(0)
    col_m = re.search(r"\bsource\s+VARCHAR\((\d+)\)", table_def)
    assert col_m, "pac_processes.source column not found"
    width = int(col_m.group(1))
    for known_value in ("builtin", "github_discovered", "manual"):
        assert len(known_value) <= width, (
            f"pac_processes.source VARCHAR({width}) too narrow for {known_value!r} "
            f"({len(known_value)} chars)"
        )


# ── drift_tool.compute_ai_acceptance_drift (MODEL_CARD.md next-step #2) ────

def _events(gate_type, outcomes):
    return [{"gate_type": gate_type, "ai_accepted": o} for o in outcomes]


def test_ai_acceptance_drift_insufficient_data_below_threshold():
    # 2 buckets x min_bucket_samples(5) = 10 needed per side; fewer than that
    # must report insufficient_data, not a misleading PSI number.
    rows = _events("risk", [True, False, True] * 3)  # 9 events total
    result = drift_tool.compute_ai_acceptance_drift(rows, split_last_n=3)
    assert len(result) == 1
    assert result[0]["flag"] == "insufficient_data"
    assert result[0]["psi"] is None


def test_ai_acceptance_drift_stable_when_rate_unchanged():
    rows = _events("objective", ([True, False] * 20))  # 40 events, steady 50%
    result = drift_tool.compute_ai_acceptance_drift(rows, split_last_n=10)
    r = result[0]
    assert r["gate_type"] == "objective"
    assert r["flag"] == "stable"
    assert r["baseline_acceptance_rate"] == 0.5
    assert r["current_acceptance_rate"] == 0.5


def test_ai_acceptance_drift_flags_drift_on_rate_collapse():
    # Baseline: mostly accepted. Current: mostly overridden — a real shift.
    baseline = [True] * 25 + [False] * 5
    current = [False] * 9 + [True] * 1
    rows = _events("risk", baseline + current)
    result = drift_tool.compute_ai_acceptance_drift(rows, split_last_n=10)
    r = result[0]
    assert r["n_current"] == 10
    assert r["current_acceptance_rate"] == 0.1
    assert r["flag"] in ("watch", "drift")  # PSI-dependent, but must not read "stable"


def test_ai_acceptance_drift_groups_by_gate_type_independently():
    rows = _events("risk", [True] * 20) + _events("objective", [False] * 20)
    result = drift_tool.compute_ai_acceptance_drift(rows, split_last_n=5)
    by_gate = {r["gate_type"]: r for r in result}
    assert set(by_gate) == {"risk", "objective"}
    assert by_gate["risk"]["current_acceptance_rate"] == 1.0
    assert by_gate["objective"]["current_acceptance_rate"] == 0.0


def test_ai_acceptance_drift_ignores_none_outcomes():
    rows = _events("risk", [True, False, None, True, None])
    result = drift_tool.compute_ai_acceptance_drift(rows, split_last_n=2)
    assert result[0]["n_baseline"] + result[0]["n_current"] == 3  # the two Nones excluded
