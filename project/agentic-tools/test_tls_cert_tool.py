#!/usr/bin/env python3
"""
Unit tests for tls_cert_tool.py — certificate expiry poll-connector adapter.
_parse_endpoints/_severity_for are pure and tested directly; _check_endpoint's
real TLS handshake is mocked at the module boundary (_check_endpoint itself)
so pull_events()/test_connection() are tested without any real network call.

    pytest test_tls_cert_tool.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import tls_cert_tool


# ── _parse_endpoints ─────────────────────────────────────────────────────────

def test_parse_endpoints_single():
    assert tls_cert_tool._parse_endpoints("api=api.example.com:443") == [("api", "api.example.com", 443)]


def test_parse_endpoints_multiple():
    result = tls_cert_tool._parse_endpoints("api=api.example.com:443,vpn=vpn.example.com:8443")
    assert result == [("api", "api.example.com", 443), ("vpn", "vpn.example.com", 8443)]


def test_parse_endpoints_skips_malformed_entries():
    """One typo in a long list shouldn't take down every other check."""
    result = tls_cert_tool._parse_endpoints("good=host.example.com:443, no-equals-sign, bad=nohost, worse=host:notaport")
    assert result == [("good", "host.example.com", 443)]


def test_parse_endpoints_empty_string_returns_empty_list():
    assert tls_cert_tool._parse_endpoints("") == []
    assert tls_cert_tool._parse_endpoints(None) == []


def test_parse_endpoints_strips_whitespace():
    assert tls_cert_tool._parse_endpoints("  api = api.example.com:443  ") == [("api", "api.example.com", 443)]


# ── _severity_for ────────────────────────────────────────────────────────────

def test_severity_unreachable_is_medium():
    check = {"reachable": False, "days_to_expiry": None}
    assert tls_cert_tool._severity_for(check, 30) == "MEDIUM"


def test_severity_already_expired_is_critical():
    check = {"reachable": True, "days_to_expiry": -5}
    assert tls_cert_tool._severity_for(check, 30) == "CRITICAL"


def test_severity_within_warn_window_is_high():
    check = {"reachable": True, "days_to_expiry": 10}
    assert tls_cert_tool._severity_for(check, 30) == "HIGH"


def test_severity_at_warn_boundary_is_high():
    check = {"reachable": True, "days_to_expiry": 30}
    assert tls_cert_tool._severity_for(check, 30) == "HIGH"


def test_severity_well_outside_window_is_info():
    check = {"reachable": True, "days_to_expiry": 300}
    assert tls_cert_tool._severity_for(check, 30) == "INFO"


# ── _audit_once / pull_events / test_connection (mocked _check_endpoint) ────

def _fake_check(name, host, port, warn_days, timeout_s, reachable=True, days_to_expiry=300, error=None):
    not_after = (datetime.now(timezone.utc) + timedelta(days=days_to_expiry)).isoformat() if reachable else None
    return {
        "name": name, "host": host, "port": port, "reachable": reachable,
        "not_after": not_after, "days_to_expiry": days_to_expiry if reachable else None,
        "subject": f"CN={host}" if reachable else None, "issuer": "CN=Test CA" if reachable else None,
        "error": error,
    }


def test_audit_once_requires_endpoints():
    try:
        tls_cert_tool._audit_once({}, {"endpoints": ""})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_audit_once_checks_every_configured_endpoint(monkeypatch):
    monkeypatch.setattr(tls_cert_tool, "_check_endpoint",
                         lambda name, host, port, warn_days, timeout_s: _fake_check(name, host, port, warn_days, timeout_s))
    audit = tls_cert_tool._audit_once({}, {"endpoints": "api=api.example.com:443,vpn=vpn.example.com:443"})
    assert audit["warn_days"] == 30
    assert len(audit["checks"]) == 2
    assert {c["name"] for c in audit["checks"]} == {"api", "vpn"}


def test_pull_events_flags_expiring_cert_as_infrastructure_finding(monkeypatch):
    monkeypatch.setattr(tls_cert_tool, "_check_endpoint",
                         lambda name, host, port, warn_days, timeout_s: _fake_check(name, host, port, warn_days, timeout_s, days_to_expiry=5))
    events = tls_cert_tool.pull_events(None, {}, {"endpoints": "api=api.example.com:443"}, None)
    assert len(events) == 1
    assert events[0]["severity"] == "HIGH"
    assert events[0]["raw_payload"]["infrastructure_finding"] is True
    assert events[0]["raw_payload"]["infra_compliance"]["cert_days_to_expiry"] == 5
    assert events[0]["event_id"].startswith("tls-cert:api:")


def test_pull_events_healthy_cert_not_flagged(monkeypatch):
    monkeypatch.setattr(tls_cert_tool, "_check_endpoint",
                         lambda name, host, port, warn_days, timeout_s: _fake_check(name, host, port, warn_days, timeout_s, days_to_expiry=300))
    events = tls_cert_tool.pull_events(None, {}, {"endpoints": "api=api.example.com:443"}, None)
    assert events[0]["severity"] == "INFO"
    assert events[0]["raw_payload"]["infrastructure_finding"] is False


def test_pull_events_unreachable_endpoint_reports_error_not_fake_expiry(monkeypatch):
    monkeypatch.setattr(tls_cert_tool, "_check_endpoint",
                         lambda name, host, port, warn_days, timeout_s: _fake_check(
                             name, host, port, warn_days, timeout_s, reachable=False, error="ConnectionRefusedError: refused"))
    events = tls_cert_tool.pull_events(None, {}, {"endpoints": "api=api.example.com:443"}, None)
    assert events[0]["severity"] == "MEDIUM"
    assert events[0]["raw_payload"]["infra_compliance"]["cert_reachable"] is False
    assert events[0]["raw_payload"]["infra_compliance"]["cert_error"] == "ConnectionRefusedError: refused"


def test_connection_reports_reachable_count(monkeypatch):
    monkeypatch.setattr(tls_cert_tool, "_check_endpoint",
                         lambda name, host, port, warn_days, timeout_s: _fake_check(name, host, port, warn_days, timeout_s, reachable=(name == "api")))
    ok, msg = tls_cert_tool.test_connection(None, {}, {"endpoints": "api=api.example.com:443,down=down.example.com:443"})
    assert ok is True
    assert "1 reachable" in msg


def test_connection_missing_endpoints_config_fails_gracefully():
    ok, msg = tls_cert_tool.test_connection(None, {}, {})
    assert ok is False
    assert "ValueError" in msg


def test_is_configured_reflects_cryptography_availability():
    assert tls_cert_tool.is_configured() == tls_cert_tool._HAS_CRYPTOGRAPHY
