#!/usr/bin/env python3
"""
Endpoint-level tests for sample_selection_endpoints.py.

    pytest test_sample_selection_endpoints.py -v
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_endpoints
import sample_selection_endpoints as sse


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(sse.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "role": "admin", "id": 1,
    }
    return TestClient(app)


def _population(n=20):
    return [{"id": i, "amount": (i + 1) * 100, "risk_score": (i % 10) / 10} for i in range(n)]


def test_select_rejects_empty_population(client):
    r = client.post("/sample-selection/select", json={"method": "random", "population": [], "params": {"n": 5}})
    assert r.status_code == 422


def test_select_rejects_unknown_method(client):
    r = client.post("/sample-selection/select", json={
        "method": "not_a_method", "population": _population(), "params": {},
    })
    assert r.status_code == 422


def test_select_rejects_invalid_params_for_method(client):
    r = client.post("/sample-selection/select", json={
        "method": "random", "population": _population(), "params": {"not_a_real_param": 5},
    })
    assert r.status_code == 422


def test_select_random_returns_sample_and_methodology(client):
    r = client.post("/sample-selection/select", json={
        "method": "random", "population": _population(), "params": {"n": 5, "seed": 1},
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["sample"]) == 5
    assert body["methodology"]["method"] == "random"


def test_select_mus_returns_sample_and_methodology(client):
    r = client.post("/sample-selection/select", json={
        "method": "mus", "population": _population(), "params": {"amount_key": "amount", "sample_size": 5, "seed": 1},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["methodology"]["method"] == "mus"
    assert body["methodology"]["interval"] is not None


def test_select_risk_based_returns_sample_and_methodology(client):
    r = client.post("/sample-selection/select", json={
        "method": "risk_based", "population": _population(),
        "params": {"risk_key": "risk_score", "n": 5, "seed": 1},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["methodology"]["method"] == "risk_based"
