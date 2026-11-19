#!/usr/bin/env python3
"""
Unit tests for the Control Flow Map: db._build_control_flow_map (pure
aggregation, no DB connection needed — same testability reasoning as
db._aggregate_scorecard_rows / test_compliance_scorecard.py),
db.get_control_flow_map (DB-boundary), and the /pac/control-flow-map
endpoint.

This is a directly-follows-graph mined from REAL observability.
adjudicated_tool_calls rows (source_system -> risk_tier -> final_verdict ->
fired control_id), not the static control-catalog taxonomy risk-sankey.jsx
renders — edge value is real observed event count.

    pytest test_control_flow_map.py -v
"""
from __future__ import annotations

import asyncio

import db
import pac_endpoints


# ── db._build_control_flow_map (pure) ───────────────────────────────────────

def _nodes_by_id(result):
    return {n["id"]: n for n in result["nodes"]}


def _links_by_pair(result):
    return {(l["source"], l["target"]): l["value"] for l in result["links"]}


def test_build_control_flow_map_empty_input():
    result = db._build_control_flow_map([], {})
    assert result == {"nodes": [], "links": []}


def test_build_control_flow_map_single_event_no_control_fired():
    """The majority case: an adjudicated event that didn't trip any policy
    rule terminates at the verdict node — no fabricated control edge."""
    rows = [("GITHUB", "MEDIUM", "MONITOR", [])]
    result = db._build_control_flow_map(rows, {})

    nodes = _nodes_by_id(result)
    assert set(nodes) == {"sys:GITHUB", "tier:MEDIUM", "verdict:MONITOR"}
    links = _links_by_pair(result)
    assert links == {("sys:GITHUB", "tier:MEDIUM"): 1, ("tier:MEDIUM", "verdict:MONITOR"): 1}


def test_build_control_flow_map_control_edge_only_when_fired():
    rows = [("SAP", "HIGH", "ESCALATE", ["VM-DENY-001"])]
    result = db._build_control_flow_map(rows, {})

    nodes = _nodes_by_id(result)
    assert "ctrl:VM-DENY-001" in nodes
    links = _links_by_pair(result)
    assert links[("verdict:ESCALATE", "ctrl:VM-DENY-001")] == 1


def test_build_control_flow_map_aggregates_repeated_paths():
    """Multiple events sharing the exact same path must accumulate edge
    value, not create duplicate edges — this is the real-frequency property
    that distinguishes this from a static taxonomy diagram."""
    rows = [
        ("SAP", "HIGH", "ESCALATE", ["VM-DENY-001"]),
        ("SAP", "HIGH", "ESCALATE", ["VM-DENY-001"]),
        ("SAP", "HIGH", "ESCALATE", ["VM-DENY-001"]),
    ]
    result = db._build_control_flow_map(rows, {})

    links = _links_by_pair(result)
    assert links[("sys:SAP", "tier:HIGH")] == 3
    assert links[("tier:HIGH", "verdict:ESCALATE")] == 3
    assert links[("verdict:ESCALATE", "ctrl:VM-DENY-001")] == 3
    # No duplicate nodes despite 3 events sharing every node on the path.
    assert len(result["nodes"]) == 4


def test_build_control_flow_map_one_event_multiple_controls_fans_out():
    rows = [("SAP", "CRITICAL", "ESCALATE", ["VM-DENY-001", "SC-DENY-002"])]
    result = db._build_control_flow_map(rows, {})

    links = _links_by_pair(result)
    assert links[("verdict:ESCALATE", "ctrl:VM-DENY-001")] == 1
    assert links[("verdict:ESCALATE", "ctrl:SC-DENY-002")] == 1


def test_build_control_flow_map_control_without_framework_mapping_still_included():
    """A control that hasn't been curated into controls_catalog's framework
    crosswalk yet must still appear in the graph — not silently dropped
    just because control_meta_by_id has nothing for it."""
    rows = [("SAP", "HIGH", "ESCALATE", ["UNMAPPED-001"])]
    result = db._build_control_flow_map(rows, {})

    nodes = _nodes_by_id(result)
    assert "ctrl:UNMAPPED-001" in nodes
    assert nodes["ctrl:UNMAPPED-001"]["type"] == "control"


def test_build_control_flow_map_attaches_framework_metadata_to_control_nodes():
    rows = [("SAP", "HIGH", "ESCALATE", ["VM-DENY-001"])]
    control_meta = {"VM-DENY-001": {
        "name": "Vendor bank change requires approver",
        "soc2_criteria": ["CC6.1"], "nist_800_53": ["AC-2"],
        "iso_27001": ["A.9.2"], "coso_component": "Performance",
    }}
    result = db._build_control_flow_map(rows, control_meta)

    ctrl_node = _nodes_by_id(result)["ctrl:VM-DENY-001"]
    assert ctrl_node["soc2_criteria"] == ["CC6.1"]
    assert ctrl_node["coso_component"] == "Performance"


def test_build_control_flow_map_null_fields_fall_back_to_unknown():
    rows = [(None, None, None, None)]
    result = db._build_control_flow_map(rows, {})
    nodes = _nodes_by_id(result)
    assert "sys:UNKNOWN" in nodes and "tier:UNKNOWN" in nodes and "verdict:UNKNOWN" in nodes


# ── db.get_control_flow_map (DB boundary) ────────────────────────────────────

class _FakeCursor:
    def __init__(self, recorder, fetch_result=None):
        self._recorder = recorder
        self._fetch_result = fetch_result or []

    def execute(self, sql, params=None):
        self._recorder.append((sql, params))

    def fetchall(self):
        return list(self._fetch_result)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, recorder, results):
        self._recorder = recorder
        self._results = results

    def cursor(self):
        fetch_result = self._results.pop(0) if self._results else []
        return _FakeCursor(self._recorder, fetch_result)

    def commit(self):
        pass


class _FakeConnCtx:
    def __init__(self, recorder, results):
        self._recorder = recorder
        self._results = results

    def __enter__(self):
        return _FakeConn(self._recorder, self._results)

    def __exit__(self, *a):
        return False


def test_get_control_flow_map_passes_days_window_and_only_queries_controls_that_fired(monkeypatch):
    recorder = []
    # _conn() calls, in order: adjudicated events, unreviewed system_telemetry
    # tail, controls_catalog lookup (only reached because a control fired).
    results = [
        [("SAP", "HIGH", "ESCALATE", ["VM-DENY-001"])],
        [],
        [("VM-DENY-001", "Vendor bank change", ["CC6.1"], [], [], "Performance")],
    ]
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, results))

    result = db.get_control_flow_map(days=7)

    events_call = recorder[0]
    assert events_call[1] == (7,)
    unreviewed_call = recorder[1]
    assert unreviewed_call[1] == (7,)
    controls_call = recorder[2]
    assert controls_call[1] == (["VM-DENY-001"],)
    assert any(n["id"] == "ctrl:VM-DENY-001" and n.get("soc2_criteria") == ["CC6.1"] for n in result["nodes"])


def test_get_control_flow_map_skips_controls_query_when_nothing_fired(monkeypatch):
    recorder = []
    results = [[("GITHUB", "LOW", "CLEAR", [])], []]
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, results))

    result = db.get_control_flow_map(days=30)

    assert len(recorder) == 2  # events + unreviewed tail ran, no wasted controls round trip
    assert result["nodes"]


def test_get_control_flow_map_includes_unreviewed_events_as_not_reviewed_verdict(monkeypatch):
    """The fix this test guards: previously get_control_flow_map only ever
    saw adjudicated_tool_calls rows, which are exclusively the flagged
    subset mcp_governance's poll loop selects for review — every other
    captured event was invisible here, same undercount already found and
    fixed in Continuous Monitoring's charts and Process Mining."""
    recorder = []
    results = [
        [],                     # no adjudicated events in window
        [("SAILPOINT",), ("SAILPOINT",), ("SAP",)],  # 3 unreviewed system_telemetry rows
    ]
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "_conn", lambda: _FakeConnCtx(recorder, results))

    result = db.get_control_flow_map(days=30)

    assert len(recorder) == 2  # no controls query — nothing fired, nothing to look up
    verdict_node = next(n for n in result["nodes"] if n["id"] == "verdict:NOT_REVIEWED")
    assert verdict_node["type"] == "verdict"
    sailpoint_link = next(l for l in result["links"] if l["source"] == "sys:SAILPOINT" and l["target"].startswith("tier:"))
    assert sailpoint_link["value"] == 2


# ── endpoint ──────────────────────────────────────────────────────────────────

def test_endpoint_returns_empty_shape_when_db_unavailable(monkeypatch):
    monkeypatch.setattr(pac_endpoints.db, "is_available", lambda: False)
    result = asyncio.run(pac_endpoints.get_control_flow_map(days=30))
    assert result == {"nodes": [], "links": [], "note": "Database not configured"}


def test_endpoint_delegates_to_db_with_days_param(monkeypatch):
    monkeypatch.setattr(pac_endpoints.db, "is_available", lambda: True)
    captured = {}
    monkeypatch.setattr(pac_endpoints.db, "get_control_flow_map", lambda days: captured.setdefault("days", days) or {"nodes": [], "links": []})

    asyncio.run(pac_endpoints.get_control_flow_map(days=90))

    assert captured["days"] == 90
