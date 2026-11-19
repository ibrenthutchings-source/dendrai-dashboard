#!/usr/bin/env python3
"""
Tests for revealing an imported framework on the Framework Matrix.

The Framework Matrix renders a CONFIGURED column list, and hidden_frameworks
deliberately suppresses re-detection so a column the user removed stays
removed. That combination made importing into a previously-hidden framework
completely invisible: the risks saved, the controls saved, the UI reported
success — and the matrix still had no SOX 404 column. Indistinguishable from
a failed import, and it cost several rounds of debugging the wrong layer.

    pytest test_matrix_reveal.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import risk_register_endpoints as rr


@pytest.fixture()
def api(monkeypatch):
    cfg = {"hidden_frameworks": ["SOX 404", "COSO ERM"], "matrix_frameworks": ["CIS Controls"]}
    store = {}

    monkeypatch.setattr(rr.db, "is_available", lambda: True)
    monkeypatch.setattr(rr.db, "save_framework_catalog", lambda n, r: store.__setitem__(n, r))
    monkeypatch.setattr(rr.db, "list_framework_catalogs",
                        lambda: [{"framework": k, "risks": v, "fetched_at": None} for k, v in store.items()])
    monkeypatch.setattr(rr.db, "upsert_control", lambda c: None)
    monkeypatch.setattr(rr.db, "get_app_config", lambda k, d=None: cfg.get(k, d))
    monkeypatch.setattr(rr.db, "set_app_config", lambda k, v: cfg.__setitem__(k, v))

    app = FastAPI()
    app.include_router(rr.router)
    for d in (rr.router.dependencies or []):
        app.dependency_overrides[d.dependency] = lambda: {"role": "admin"}
    return TestClient(app), cfg


SOX = [{"id": "SOX-IT-01", "name": "Unauthorized access", "source_framework": "SOX 404"}]


def test_importing_unhides_a_hidden_framework(api):
    client, cfg = api
    r = client.post("/risk-register/save-catalog", json={"risks": SOX})
    assert r.status_code == 200
    assert "SOX 404" not in cfg["hidden_frameworks"]
    assert "SOX 404" in cfg["matrix_frameworks"]
    assert r.json()["revealed_on_matrix"] == ["SOX 404"]


def test_other_hidden_frameworks_are_left_hidden(api):
    """Un-hiding must be scoped to what was imported — it is not a licence to
    undo every column the user deliberately removed."""
    client, cfg = api
    client.post("/risk-register/save-catalog", json={"risks": SOX})
    assert cfg["hidden_frameworks"] == ["COSO ERM"]


def test_a_framework_already_on_the_matrix_is_not_duplicated(api):
    client, cfg = api
    client.post("/risk-register/save-catalog", json={
        "risks": [{"id": "C-1", "name": "x", "source_framework": "CIS Controls"}]})
    assert cfg["matrix_frameworks"].count("CIS Controls") == 1
    assert cfg["matrix_frameworks"] == ["CIS Controls"]


def test_a_brand_new_framework_is_added_to_the_matrix(api):
    client, cfg = api
    r = client.post("/risk-register/save-catalog", json={
        "risks": [{"id": "N-1", "name": "x", "source_framework": "NIST CSF"}]})
    assert "NIST CSF" in cfg["matrix_frameworks"]
    assert r.json()["revealed_on_matrix"] == ["NIST CSF"]


def test_every_imported_framework_in_a_mixed_register_is_revealed(api):
    client, cfg = api
    r = client.post("/risk-register/save-catalog", json={
        "risks": SOX + [{"id": "N-1", "name": "x", "source_framework": "NIST CSF"}]})
    assert set(r.json()["revealed_on_matrix"]) == {"SOX 404", "NIST CSF"}


def test_importing_nothing_changes_no_configuration(api):
    client, cfg = api
    client.post("/risk-register/save-catalog", json={"risks": []})
    assert cfg["matrix_frameworks"] == ["CIS Controls"]
    assert cfg["hidden_frameworks"] == ["SOX 404", "COSO ERM"]


def test_a_deleted_framework_stays_deleted_when_something_else_is_imported():
    """Un-hiding is scoped to the import and nothing else. Deleting a column
    is a deliberate act; an unrelated import must not quietly undo it, and a
    bulk 'reveal everything with data' pass is exactly the wrong behaviour."""
    import risk_register_endpoints as rr_
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import pytest as _pytest

    cfg = {"hidden_frameworks": ["ISO/IEC 27001", "SOC 2", "SOX 404"],
           "matrix_frameworks": ["CIS Controls"]}
    store = {"ISO/IEC 27001": [{"id": "I-1"}], "SOC 2": [{"id": "S-1"}]}

    mp = _pytest.MonkeyPatch()
    try:
        mp.setattr(rr_.db, "is_available", lambda: True)
        mp.setattr(rr_.db, "save_framework_catalog", lambda n, r: store.__setitem__(n, r))
        mp.setattr(rr_.db, "list_framework_catalogs",
                   lambda: [{"framework": k, "risks": v, "fetched_at": None} for k, v in store.items()])
        mp.setattr(rr_.db, "upsert_control", lambda c: None)
        mp.setattr(rr_.db, "get_app_config", lambda k, d=None: cfg.get(k, d))
        mp.setattr(rr_.db, "set_app_config", lambda k, v: cfg.__setitem__(k, v))

        app = FastAPI()
        app.include_router(rr_.router)
        for d in (rr_.router.dependencies or []):
            app.dependency_overrides[d.dependency] = lambda: {"role": "admin"}
        client = TestClient(app)

        client.post("/risk-register/save-catalog", json={
            "risks": [{"id": "SOX-IT-01", "name": "x", "source_framework": "SOX 404"}]})
    finally:
        mp.undo()

    # Only the imported framework was revealed...
    assert cfg["matrix_frameworks"] == ["CIS Controls", "SOX 404"]
    # ...and the other deletions survived, despite both having catalog data.
    assert cfg["hidden_frameworks"] == ["ISO/IEC 27001", "SOC 2"]
