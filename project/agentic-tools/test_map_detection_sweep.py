#!/usr/bin/env python3
"""
Unit + integration tests for map_detection_sweep.py — the recurrence
detector that drafts a Management Action Plan (risk rating, root cause,
remediation action, success criteria) when a control keeps requiring human
review. Mirrors test_remediation_pr.py's approach for the LLM-draft parsing
(claude_client.complete_text_meta monkeypatched, no real LLM call) and
test_expiry_sweeps.py's approach for the sweep_once() integration shape.

    pytest test_map_detection_sweep.py -v
"""
from __future__ import annotations

import asyncio

import db
import map_detection_sweep as mds


# ── _draft_map_proposal (LLM parsing + fallback) ─────────────────────────────

def test_draft_map_proposal_parses_all_four_sections(monkeypatch):
    llm_output = (
        "RATING: R\n"
        "ROOT_CAUSE: The approval workflow silently permits self-approval under a delegation edge case.\n"
        "ACTION: Close the delegation loophole in the approval-matrix config and add a SoD check at submission time.\n"
        "SUCCESS_CRITERIA: Zero preparer==approver JE findings for 30 consecutive days.\n"
    )
    monkeypatch.setattr(mds.claude_client, "complete_text_meta", lambda *a, **kw: (llm_output, "end_turn"))

    rating, root_cause, action, success_criteria = mds._draft_map_proposal(
        "JE-SOD-PREPARER-APPROVER", "oracle_fusion", 5, 30, [],
    )

    assert rating == "R"
    assert "delegation edge case" in root_cause
    assert "SoD check" in action
    assert "30 consecutive days" in success_criteria


def test_draft_map_proposal_falls_back_when_llm_call_fails(monkeypatch):
    def _raise(*a, **kw):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(mds.claude_client, "complete_text_meta", _raise)

    rating, root_cause, action, success_criteria = mds._draft_map_proposal(
        "JE-SOD-PREPARER-APPROVER", "oracle_fusion", 3, 30, [],
    )

    assert rating in ("R", "A", "G")
    assert "JE-SOD-PREPARER-APPROVER" in root_cause
    assert "JE-SOD-PREPARER-APPROVER" in action
    assert action  # non-empty templated fallback, never blank


def test_draft_map_proposal_falls_back_on_malformed_output(monkeypatch):
    monkeypatch.setattr(mds.claude_client, "complete_text_meta", lambda *a, **kw: ("not the expected format", "end_turn"))
    rating, root_cause, action, success_criteria = mds._draft_map_proposal("CTRL-1", "sap_hana", 4, 30, [])
    assert rating in ("R", "A", "G")
    assert root_cause and action and success_criteria


def test_draft_map_proposal_falls_back_on_invalid_rating_letter(monkeypatch):
    llm_output = "RATING: X\nROOT_CAUSE: x\nACTION: y\nSUCCESS_CRITERIA: z\n"
    monkeypatch.setattr(mds.claude_client, "complete_text_meta", lambda *a, **kw: (llm_output, "end_turn"))
    rating, root_cause, action, success_criteria = mds._draft_map_proposal("CTRL-1", "sap_hana", 4, 30, [])
    assert rating in ("R", "A", "G")
    # fell all the way through to the templated fallback, not the malformed "X"
    assert "CTRL-1" in root_cause


def test_draft_map_proposal_fallback_rating_scales_with_occurrence_count(monkeypatch):
    monkeypatch.setattr(mds.claude_client, "complete_text_meta", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))
    low, *_ = mds._draft_map_proposal("CTRL-1", "sap_hana", mds.MIN_OCCURRENCES, 30, [])
    high, *_ = mds._draft_map_proposal("CTRL-1", "sap_hana", mds.MIN_OCCURRENCES * 3, 30, [])
    assert low == "A"
    assert high == "R"


# ── sweep_once() — same shape/mocking approach as test_expiry_sweeps.py ─────

def test_sweep_once_no_candidates_is_a_no_op(monkeypatch):
    monkeypatch.setattr(db, "detect_recurring_exceptions", lambda min_occurrences, window_days: [])
    assert asyncio.run(mds.sweep_once()) == 0


def test_sweep_once_proposes_a_map_for_each_candidate(monkeypatch):
    monkeypatch.setattr(db, "detect_recurring_exceptions", lambda min_occurrences, window_days: [
        {"control_id": "JE-SOD-PREPARER-APPROVER", "system_source": "oracle_fusion", "occurrence_count": 4,
         "first_occurrence_at": "2026-07-01T00:00:00", "last_occurrence_at": "2026-07-20T00:00:00",
         "event_ids": [1, 2, 3, 4]},
    ])
    monkeypatch.setattr(db, "get_recent_exception_events_for_control", lambda control_id, limit=5: [])
    monkeypatch.setattr(mds.claude_client, "complete_text_meta", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))

    created = []

    def _fake_create_map(*args):
        created.append(args)
        return {"map_ref": "MAP-CM-000001", "id": 1}
    monkeypatch.setattr(db, "create_map", _fake_create_map)

    n = asyncio.run(mds.sweep_once())

    assert n == 1
    assert len(created) == 1
    assert created[0][0] == "JE-SOD-PREPARER-APPROVER"  # control_id
    assert created[0][1] == "oracle_fusion"              # system_source


def test_sweep_once_counts_only_actually_created_maps(monkeypatch):
    """create_map returns None on the idx_map_open_per_control race (another
    process already opened one) — that must not be counted as proposed."""
    monkeypatch.setattr(db, "detect_recurring_exceptions", lambda min_occurrences, window_days: [
        {"control_id": "CTRL-1", "system_source": "sap_hana", "occurrence_count": 3,
         "first_occurrence_at": None, "last_occurrence_at": None, "event_ids": []},
    ])
    monkeypatch.setattr(db, "get_recent_exception_events_for_control", lambda control_id, limit=5: [])
    monkeypatch.setattr(mds.claude_client, "complete_text_meta", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(db, "create_map", lambda *args: None)

    assert asyncio.run(mds.sweep_once()) == 0


def test_sweep_once_one_failure_does_not_block_the_rest(monkeypatch):
    monkeypatch.setattr(db, "detect_recurring_exceptions", lambda min_occurrences, window_days: [
        {"control_id": "CTRL-BROKEN", "system_source": "x", "occurrence_count": 3,
         "first_occurrence_at": None, "last_occurrence_at": None, "event_ids": []},
        {"control_id": "CTRL-FINE", "system_source": "x", "occurrence_count": 3,
         "first_occurrence_at": None, "last_occurrence_at": None, "event_ids": []},
    ])
    monkeypatch.setattr(db, "get_recent_exception_events_for_control", lambda control_id, limit=5: [])
    monkeypatch.setattr(mds.claude_client, "complete_text_meta", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))

    def _flaky_create(control_id, *rest):
        if control_id == "CTRL-BROKEN":
            raise RuntimeError("simulated DB hiccup")
        return {"map_ref": "MAP-CM-000002", "id": 2}
    monkeypatch.setattr(db, "create_map", _flaky_create)

    assert asyncio.run(mds.sweep_once()) == 1
