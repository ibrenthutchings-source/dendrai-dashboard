"""
Tests for auth_endpoints._client_ip / mcp_governance._client_ip.

uvicorn binds to 127.0.0.1 (project/start.sh) and is only ever reached via
nginx on the loopback interface — request.client.host is therefore ALWAYS
"127.0.0.1" in production, regardless of who the real caller is, which
previously collapsed the login rate limiter (_rate_check) and the audit/
session/telemetry IP fields into a single shared bucket for every visitor.
nginx.conf already sets X-Forwarded-For correctly on every proxied route;
these tests confirm the fix reads that instead of the loopback address, and
still falls back sanely for local dev where nothing proxies the request.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import auth_endpoints
import mcp_governance


def _make_app(client_ip_fn):
    app = FastAPI()

    @app.get("/probe")
    def probe(request: Request):
        return {"ip": client_ip_fn(request)}

    return TestClient(app)


class TestAuthEndpointsClientIp:
    def test_uses_forwarded_for_over_loopback_peer(self):
        client = _make_app(auth_endpoints._client_ip)
        # TestClient's own transport connects as 127.0.0.1 either way — the
        # header is what must win, matching nginx's real proxied behavior.
        res = client.get("/probe", headers={"X-Forwarded-For": "203.0.113.7"})
        assert res.json()["ip"] == "203.0.113.7"

    def test_takes_leftmost_ip_when_forwarded_for_has_a_chain(self):
        client = _make_app(auth_endpoints._client_ip)
        res = client.get("/probe", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.5"})
        assert res.json()["ip"] == "203.0.113.7"

    def test_falls_back_to_request_client_when_header_absent(self):
        client = _make_app(auth_endpoints._client_ip)
        res = client.get("/probe")
        # No proxy in front (e.g. local dev) — falls back to the direct peer.
        assert res.json()["ip"] in ("testclient", "127.0.0.1")

    def test_none_request_returns_none(self):
        assert auth_endpoints._client_ip(None) is None


class TestMcpGovernanceClientIp:
    def test_uses_forwarded_for_over_loopback_peer(self):
        client = _make_app(mcp_governance._client_ip)
        res = client.get("/probe", headers={"X-Forwarded-For": "198.51.100.9"})
        assert res.json()["ip"] == "198.51.100.9"

    def test_none_request_returns_none(self):
        assert mcp_governance._client_ip(None) is None
