#!/usr/bin/env python3
"""
Regression tests for control-save/convert performance.

Two real inefficiencies, both the same shape: an operation on ONE control
paid for reading the ENTIRE controls_library table, and the table only grows
(every register import adds its own controls — a single SOX 404 upload added
19). What felt fine at 40 rows gets slower with every subsequent import.

  1. create/update/link-pac-control each did a full-table SELECT just to
     check one ref's existence or read its current values.
  2. convert_to_code called that same full-table SELECT ONCE PER CONTROL ON
     EVERY RISK — a register of 20 risks with 2 controls each cost ~40 round
     trips to look up a name, not the 1 it actually needed. This is the "Save
     process is taking too long" a user would actually feel, since Convert to
     Code / Save All is what "saving a register" triggers.

These tests assert call counts, not wall-clock time — timing is flaky in CI,
call counts pin the actual defect (repeated full-table reads) directly.

    pytest test_control_save_perf.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import risk_register_endpoints as rr


@pytest.fixture()
def api(monkeypatch):
    """Counts full-table reads (get_controls_library) separately from
    single-row reads (get_control_by_ref) so a regression that swaps one for
    the other is caught immediately."""
    calls = {"full_table": 0, "single_row": 0}
    library = {
        "SOX-IT-01": {"ref": "SOX-IT-01", "framework": "SOX 404", "name": "Access control",
                     "description": "d", "category": "IT", "domain": "IT", "tags": [], "pac_control_id": None},
        "AC-04": {"ref": "AC-04", "framework": "CIS Controls", "name": "Privileged Access",
                 "description": "d", "category": "IT", "domain": "IT", "tags": [], "pac_control_id": None},
    }

    def _full_table():
        calls["full_table"] += 1
        return list(library.values())

    def _single(ref):
        calls["single_row"] += 1
        return library.get(ref.strip().upper())

    monkeypatch.setattr(rr.db, "is_available", lambda: True)
    monkeypatch.setattr(rr.db, "get_controls_library", _full_table)
    monkeypatch.setattr(rr.db, "get_control_by_ref", _single)
    monkeypatch.setattr(rr.db, "upsert_control", lambda c: True)
    monkeypatch.setattr(rr.db, "set_control_pac_link", lambda ref, pid: True)
    monkeypatch.setattr(rr.db, "get_control", lambda cid: None)

    app = FastAPI()
    app.include_router(rr.router)
    for d in (rr.router.dependencies or []):
        app.dependency_overrides[d.dependency] = lambda: {"role": "admin"}
    return TestClient(app), calls


def test_updating_one_control_does_not_read_the_whole_library(api):
    client, calls = api
    r = client.put("/risk-register/controls/SOX-IT-01",
                   json={"name": "Renamed", "description": "d2"})
    assert r.status_code == 200
    assert calls["full_table"] == 0
    assert calls["single_row"] == 1


def test_creating_a_control_does_not_read_the_whole_library(api):
    client, calls = api
    r = client.post("/risk-register/controls",
                    json={"ref": "NEW-01", "framework": "Internal", "name": "x",
                          "category": "IT", "domain": "IT", "description": ""})
    assert r.status_code == 200
    assert calls["full_table"] == 0
    assert calls["single_row"] == 1


def test_linking_a_pac_control_does_not_read_the_whole_library(api):
    client, calls = api
    r = client.put("/risk-register/controls/SOX-IT-01/pac-link", json={"pac_control_id": None})
    assert r.status_code == 200
    assert calls["full_table"] == 0
    assert calls["single_row"] == 1


def test_convert_to_code_reads_the_control_library_exactly_once(api):
    """The actual bug: this used to call the full-table read once PER CONTROL
    ON EVERY RISK. A register of 3 risks x 2 controls must still cost exactly
    one read, not six."""
    client, calls = api
    risks = [
        {"id": f"R-{i}", "name": f"risk {i}", "included": True,
         "controls_assigned": ["SOX-IT-01", "AC-04"]}
        for i in range(3)
    ]
    r = client.post("/risk-register/convert-to-code", json={
        "risks": risks, "framework": "SOX 404", "review_type": "external",
        "include_controls": True,
    })
    assert r.status_code == 200
    assert calls["full_table"] == 1
    assert "SOX-IT-01" in r.json()["yaml"] or "Access control" in r.json()["yaml"]


def test_convert_to_code_skips_the_control_read_entirely_when_not_needed(api):
    client, calls = api
    r = client.post("/risk-register/convert-to-code", json={
        "risks": [{"id": "R-1", "name": "x", "included": True, "controls_assigned": ["SOX-IT-01"]}],
        "framework": "SOX 404", "review_type": "external",
        "include_controls": False,
    })
    assert r.status_code == 200
    assert calls["full_table"] == 0
