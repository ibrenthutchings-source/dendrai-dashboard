"""
Tests for GET /sox/segments/{ticker}/latest (sox_endpoints.py) — the Assess
Risk loop's Stage 2 "Forecasts" panel calls this to render the disaggregated
geography/business-segment revenue breakdown without knowing which fiscal
year is current for the ticker.

Route ORDER matters here: this route and the pre-existing
GET /sox/segments/{ticker}/{fiscal_year} are both two-segment path patterns,
so "latest" must be registered first or it gets swallowed as a literal
fiscal_year value. That regression is exactly what this suite guards against.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_endpoints
import sox_endpoints as se


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(se.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "display_name": "Test Auditor", "role": "admin", "id": 1,
    }
    return TestClient(app)


def test_latest_route_not_swallowed_by_fiscal_year_pattern(client):
    with patch.object(se, "_resolve_company_id", return_value=42), \
         patch.object(se.db, "get_latest_sox_segments", return_value=[
             {"segment_type": "geography", "segment_name": "United States",
              "revenue": 300.6, "revenue_pct": 42.1, "source": "filed"},
         ]):
        r = client.get("/sox/segments/ON/latest")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "ON"
    assert body["count"] == 1
    assert body["segments"][0]["segment_name"] == "United States"


def test_fiscal_year_route_still_reachable_after_latest_added(client):
    with patch.object(se, "_resolve_company_id", return_value=42), \
         patch.object(se.db, "get_sox_segments", return_value=[]) as mock_get:
        r = client.get("/sox/segments/ON/2025")
    assert r.status_code == 200
    assert r.json()["fiscal_year"] == "2025"
    mock_get.assert_called_once_with(42, "2025")


def test_latest_unknown_ticker_returns_empty_not_error(client):
    with patch.object(se, "_resolve_company_id", return_value=None):
        r = client.get("/sox/segments/UNKNOWNTICKER/latest")
    assert r.status_code == 200
    assert r.json() == {"ticker": "UNKNOWNTICKER", "count": 0, "segments": []}
