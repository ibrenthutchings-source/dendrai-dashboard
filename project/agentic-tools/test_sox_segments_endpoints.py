#!/usr/bin/env python3
"""
Endpoint-level tests for sox_endpoints.py's two auto-fill segment routes:
POST /sox/segments/{ticker}/import-xbrl and
POST /sox/segments/{ticker}/estimate-financials. Both wrap edgar_segments.py
functions already covered at the unit level (test_edgar_segments.py) — this
file only locks in the HTTP wiring (route registration, param validation,
response passthrough), not the extraction/allocation math itself.

    pytest test_sox_segments_endpoints.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sox_endpoints as se
import auth_endpoints


@pytest.fixture()
def client():
    """App with auth stubbed out (router-level require_screen_permission
    dependency) — same pattern as test_ai_governance_endpoints.py's fixture."""
    app = FastAPI()
    app.include_router(se.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: {
        "username": "tester", "role": "admin", "id": 1,
    }
    return TestClient(app)


class TestImportXbrlSegments:
    def test_passes_through_persist_segments_result(self, client):
        fake_result = {"extracted": True, "ticker": "ON", "persisted": [{"segment_name": "PowerSolutionsGroup"}], "skipped": []}
        with patch("edgar_segments.persist_segments", return_value=fake_result) as mock_persist:
            r = client.post("/sox/segments/ON/import-xbrl")
        assert r.status_code == 200
        assert r.json() == fake_result
        mock_persist.assert_called_once_with("ON")


class TestEstimateSegmentFinancials:
    def test_passes_through_estimate_result(self, client):
        fake_result = {
            "estimated": True, "ticker": "ON", "revenue_pct": 25.0, "source": "estimated",
            "revenue": 400_000_000.0, "gross_profit": 225_000_000.0, "gross_margin_pct": 56.25,
        }
        with patch("edgar_segments.estimate_segment_financials", return_value=fake_result) as mock_est:
            r = client.post("/sox/segments/ON/estimate-financials", params={"revenue_pct": 25.0})
        assert r.status_code == 200
        assert r.json() == fake_result
        mock_est.assert_called_once_with("ON", 25.0)

    def test_revenue_pct_out_of_range_is_rejected(self, client):
        r = client.post("/sox/segments/ON/estimate-financials", params={"revenue_pct": 150})
        assert r.status_code == 422

    def test_revenue_pct_is_required(self, client):
        r = client.post("/sox/segments/ON/estimate-financials")
        assert r.status_code == 422
