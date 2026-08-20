#!/usr/bin/env python3
"""
Unit tests for osv_client.py. query_batch()'s HTTP calls (requests.post to
querybatch, requests.get to /vulns/{id}) are monkeypatched at the module
boundary — no real network. normalize_severity/extract_fixed_version/
_parse_cvss_score are pure and tested directly.

    pytest test_osv_client.py -v
"""

from __future__ import annotations

import osv_client


# ── normalize_severity ───────────────────────────────────────────────────────

def test_normalize_severity_cvss_v3_critical():
    tier, score = osv_client.normalize_severity({"severity": [{"type": "CVSS_V3", "score": "9.8"}]})
    assert tier == "CRITICAL"
    assert score == 9.8


def test_normalize_severity_cvss_high():
    tier, score = osv_client.normalize_severity({"severity": [{"type": "CVSS_V3", "score": "7.5"}]})
    assert tier == "HIGH"


def test_normalize_severity_cvss_medium():
    tier, score = osv_client.normalize_severity({"severity": [{"type": "CVSS_V3", "score": "5.0"}]})
    assert tier == "MEDIUM"


def test_normalize_severity_cvss_low():
    tier, score = osv_client.normalize_severity({"severity": [{"type": "CVSS_V3", "score": "2.0"}]})
    assert tier == "LOW"


def test_normalize_severity_vector_string_unparseable_falls_through():
    """A full CVSS vector string (no bare numeric score) can't be parsed into
    a number here — falls through to database_specific.severity, or MEDIUM
    if that's absent too. Never crashes, never fabricates a score."""
    tier, score = osv_client.normalize_severity({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/S:U/C:H"}]})
    assert score is None
    assert tier == "MEDIUM"


def test_normalize_severity_falls_back_to_database_specific():
    tier, score = osv_client.normalize_severity({"database_specific": {"severity": "HIGH"}})
    assert tier == "HIGH"
    assert score is None


def test_normalize_severity_no_signal_at_all_defaults_to_medium_not_critical():
    """A CVE with zero severity signal must default to MEDIUM, never a worse
    tier fabricated to look more alarming than what's actually known."""
    tier, score = osv_client.normalize_severity({})
    assert tier == "MEDIUM"
    assert score is None


# ── extract_fixed_version ────────────────────────────────────────────────────

def test_extract_fixed_version_finds_fix_event():
    vuln = {"affected": [{
        "package": {"ecosystem": "PyPI"},
        "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.7.1"}]}],
    }]}
    assert osv_client.extract_fixed_version(vuln, "PyPI") == "2.7.1"


def test_extract_fixed_version_no_fix_returns_none():
    vuln = {"affected": [{"package": {"ecosystem": "PyPI"}, "ranges": [{"events": [{"introduced": "0"}]}]}]}
    assert osv_client.extract_fixed_version(vuln, "PyPI") is None


def test_extract_fixed_version_ignores_other_ecosystems():
    vuln = {"affected": [{"package": {"ecosystem": "npm"}, "ranges": [{"events": [{"fixed": "1.0.0"}]}]}]}
    assert osv_client.extract_fixed_version(vuln, "PyPI") is None


def test_extract_fixed_version_empty_affected_returns_none():
    assert osv_client.extract_fixed_version({}, "PyPI") is None


# ── query_batch (mocked HTTP) ────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, json_data, status=200):
        self._json = json_data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def test_query_batch_empty_input_returns_empty_dict():
    assert osv_client.query_batch([]) == {}


def test_query_batch_happy_path(monkeypatch):
    def _fake_post(url, json, timeout):
        assert url == osv_client._QUERYBATCH_URL
        return _FakeResponse({"results": [{"vulns": [{"id": "CVE-2021-1234"}]}]})

    def _fake_get(url, timeout):
        assert "CVE-2021-1234" in url
        return _FakeResponse({"id": "CVE-2021-1234", "summary": "Something bad", "severity": []})

    monkeypatch.setattr(osv_client.requests, "post", _fake_post)
    monkeypatch.setattr(osv_client.requests, "get", _fake_get)

    result = osv_client.query_batch([("PyPI", "requests", "2.6.0")])

    assert ("PyPI", "requests", "2.6.0") in result
    assert result[("PyPI", "requests", "2.6.0")][0]["id"] == "CVE-2021-1234"


def test_query_batch_no_vulns_returns_empty_list_for_the_triple(monkeypatch):
    monkeypatch.setattr(osv_client.requests, "post", lambda url, json, timeout: _FakeResponse({"results": [{}]}))
    result = osv_client.query_batch([("PyPI", "clean-pkg", "1.0.0")])
    assert result[("PyPI", "clean-pkg", "1.0.0")] == []


def test_query_batch_network_failure_leaves_triple_absent_not_empty(monkeypatch):
    """The core safety property: an unreachable OSV must NOT produce an
    empty (looks-clean) result — the triple must be missing entirely so the
    caller (vulnerability_sweep.py) can tell 'unreachable' apart from
    'queried, found nothing'."""
    def _raise(*a, **kw):
        raise ConnectionError("simulated network failure")
    monkeypatch.setattr(osv_client.requests, "post", _raise)

    result = osv_client.query_batch([("PyPI", "requests", "2.6.0")])

    assert ("PyPI", "requests", "2.6.0") not in result


def test_query_batch_hydration_failure_skips_only_that_cve(monkeypatch):
    def _fake_post(url, json, timeout):
        return _FakeResponse({"results": [{"vulns": [{"id": "CVE-BAD"}, {"id": "CVE-GOOD"}]}]})

    def _fake_get(url, timeout):
        if "CVE-BAD" in url:
            raise ConnectionError("hydration failed")
        return _FakeResponse({"id": "CVE-GOOD", "summary": "ok"})

    monkeypatch.setattr(osv_client.requests, "post", _fake_post)
    monkeypatch.setattr(osv_client.requests, "get", _fake_get)

    result = osv_client.query_batch([("PyPI", "pkg", "1.0.0")])

    ids = [v["id"] for v in result[("PyPI", "pkg", "1.0.0")]]
    assert ids == ["CVE-GOOD"]


def test_query_batch_dedups_hydration_across_packages(monkeypatch):
    """The same CVE shared by two packages in one call must only trigger one
    GET /vulns/{id} — not N."""
    get_calls = []

    def _fake_post(url, json, timeout):
        return _FakeResponse({"results": [
            {"vulns": [{"id": "CVE-SHARED"}]},
            {"vulns": [{"id": "CVE-SHARED"}]},
        ]})

    def _fake_get(url, timeout):
        get_calls.append(url)
        return _FakeResponse({"id": "CVE-SHARED", "summary": "shared"})

    monkeypatch.setattr(osv_client.requests, "post", _fake_post)
    monkeypatch.setattr(osv_client.requests, "get", _fake_get)

    osv_client.query_batch([("PyPI", "pkg-a", "1.0.0"), ("PyPI", "pkg-b", "1.0.0")])

    assert len(get_calls) == 1


def test_is_configured_reflects_requests_availability():
    assert osv_client.is_configured() == osv_client._HAS_REQUESTS
