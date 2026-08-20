#!/usr/bin/env python3
"""
Unit tests for infra_asset_sweep.py — Phase 1 asset sync + credential/
certificate expiry sweep. db, mcp_governance._ingest_system_event, and the
postgres_cis_tool/tls_cert_tool _audit_once() calls are all monkeypatched;
mcp_governance._detect_system_flags runs for real (same discipline as
test_expiry_sweeps.py), so risk_flags asserted below are whatever the real
detector actually produces.

    pytest test_infra_asset_sweep.py -v
"""

from __future__ import annotations

import asyncio

import db
import mcp_governance
import postgres_cis_tool
import tls_cert_tool
import infra_asset_sweep


def _recorder(monkeypatch):
    calls = []

    def _fake_ingest(server_name, system_type, event_type, event_id, actor, action,
                      resource, severity, flags, raw_payload, source_ip):
        calls.append({
            "server_name": server_name, "system_type": system_type, "event_type": event_type,
            "event_id": event_id, "actor": actor, "action": action, "resource": resource,
            "severity": severity, "flags": flags, "raw_payload": raw_payload,
        })
        return len(calls)

    monkeypatch.setattr(mcp_governance, "_ingest_system_event", _fake_ingest)
    return calls


# ── _severity_for_expiry ─────────────────────────────────────────────────────

def test_severity_for_expiry_past_is_critical():
    assert infra_asset_sweep._severity_for_expiry("2020-01-01T00:00:00Z") == "CRITICAL"


def test_severity_for_expiry_future_is_high():
    import datetime as dt
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=10)).isoformat()
    assert infra_asset_sweep._severity_for_expiry(future) == "HIGH"


def test_severity_for_expiry_unparseable_is_medium():
    assert infra_asset_sweep._severity_for_expiry("not-a-date") == "MEDIUM"
    assert infra_asset_sweep._severity_for_expiry(None) == "MEDIUM"


# ── _sync_assets ──────────────────────────────────────────────────────────────

def test_sync_assets_no_db_returns_zero(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: False)
    assert asyncio.run(infra_asset_sweep._sync_assets()) == 0


def test_sync_assets_skips_inactive_and_unrelated_connectors(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "list_poll_connectors", lambda include_credentials: [
        {"id": 1, "connector_type": "postgres_cis", "active": False, "credentials": {}, "extra_config": {}},
        {"id": 2, "connector_type": "github_scm", "active": True, "credentials": {}, "extra_config": {}},
    ])
    assert asyncio.run(infra_asset_sweep._sync_assets()) == 0


def test_sync_assets_postgres_connector_upserts_and_marks_assessed(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "list_poll_connectors", lambda include_credentials: [
        {"id": 1, "connector_type": "postgres_cis", "active": True,
         "credentials": {"dsn": "postgresql://x"}, "extra_config": {"resource_label": "primary-db"}},
    ])
    monkeypatch.setattr(postgres_cis_tool, "_audit_once", lambda credentials, extra_config: {
        "resource_label": "primary-db", "compliance": {"server_version": "16.4", "version_current": True,
                                                         "latest_known_version": "16.4"},
        "raw": {}, "violated": False, "severity": "INFO",
    })
    upserts = []
    assessed = []
    monkeypatch.setattr(db, "upsert_infra_asset", lambda *a, **kw: upserts.append(a))
    monkeypatch.setattr(db, "mark_infra_asset_assessed", lambda asset_key, source: assessed.append((asset_key, source)))

    synced = asyncio.run(infra_asset_sweep._sync_assets())

    assert synced == 1
    assert len(upserts) == 1
    assert upserts[0][0] == "postgres:primary-db"
    assert upserts[0][1] == "database"
    assert assessed == [("postgres:primary-db", "postgres_cis")]


def test_sync_assets_postgres_audit_failure_does_not_block_other_connectors(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "list_poll_connectors", lambda include_credentials: [
        {"id": 1, "connector_type": "postgres_cis", "active": True, "credentials": {}, "extra_config": {}},
        {"id": 2, "connector_type": "postgres_cis", "active": True, "credentials": {"dsn": "postgresql://x"},
         "extra_config": {"resource_label": "secondary-db"}},
    ])
    def _audit(credentials, extra_config):
        if not credentials.get("dsn"):
            raise ValueError("credentials.dsn is required")
        return {"resource_label": "secondary-db", "compliance": {"server_version": "16.4"}, "raw": {}, "violated": False, "severity": "INFO"}
    monkeypatch.setattr(postgres_cis_tool, "_audit_once", _audit)
    monkeypatch.setattr(db, "upsert_infra_asset", lambda *a, **kw: None)
    monkeypatch.setattr(db, "mark_infra_asset_assessed", lambda *a, **kw: None)

    synced = asyncio.run(infra_asset_sweep._sync_assets())
    assert synced == 1  # only the second connector succeeded


def test_sync_assets_tls_connector_syncs_one_asset_per_endpoint(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "list_poll_connectors", lambda include_credentials: [
        {"id": 3, "connector_type": "tls_cert", "active": True, "credentials": {},
         "extra_config": {"endpoints": "api=api.example.com:443,vpn=vpn.example.com:443"}},
    ])
    monkeypatch.setattr(tls_cert_tool, "_audit_once", lambda credentials, extra_config: {
        "warn_days": 30,
        "checks": [
            {"name": "api", "host": "api.example.com", "port": 443, "reachable": True,
             "not_after": "2027-01-01T00:00:00+00:00", "subject": "CN=api", "issuer": "CN=CA", "error": None},
            {"name": "vpn", "host": "vpn.example.com", "port": 443, "reachable": False,
             "not_after": None, "subject": None, "issuer": None, "error": "TimeoutError: timed out"},
        ],
    })
    upserts = []
    assessed = []
    monkeypatch.setattr(db, "upsert_infra_asset", lambda *a, **kw: upserts.append(a))
    monkeypatch.setattr(db, "mark_infra_asset_assessed", lambda asset_key, source: assessed.append(asset_key))

    synced = asyncio.run(infra_asset_sweep._sync_assets())

    assert synced == 2
    assert len(upserts) == 2
    # only the reachable endpoint gets marked assessed — an unreachable
    # endpoint must not look like a real check happened
    assert assessed == ["cert:api.example.com:443"]


# ── _check_expiry ─────────────────────────────────────────────────────────────

def test_check_expiry_no_db_returns_zero(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: False)
    assert asyncio.run(infra_asset_sweep._check_expiry()) == 0


def test_check_expiry_no_op_when_nothing_expiring(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "list_expiring_credentials", lambda warn_days: [])
    monkeypatch.setattr(db, "list_expiring_infra_assets", lambda warn_days: [])
    calls = _recorder(monkeypatch)
    assert asyncio.run(infra_asset_sweep._check_expiry()) == 0
    assert calls == []


def test_check_expiry_raises_finding_for_expiring_credential(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "list_expiring_credentials", lambda warn_days: [
        {"id": 5, "connector_type": "github_scm", "display_name": "GitHub CI Token",
         "credentials_expires_at": "2020-01-01T00:00:00Z"},
    ])
    monkeypatch.setattr(db, "list_expiring_infra_assets", lambda warn_days: [])
    calls = _recorder(monkeypatch)

    raised = asyncio.run(infra_asset_sweep._check_expiry())

    assert raised == 1
    assert len(calls) == 1
    assert calls[0]["severity"] == "CRITICAL"  # already past expiry
    assert calls[0]["resource"] == "github_scm:GitHub CI Token"
    assert calls[0]["raw_payload"]["infrastructure_finding"] is True
    assert calls[0]["raw_payload"]["infra_compliance"]["kind"] == "credential"
    assert "asset-expiry:credential:github_scm:GitHub CI Token" in calls[0]["event_id"]


def test_check_expiry_raises_finding_for_expiring_certificate(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "list_expiring_credentials", lambda warn_days: [])
    monkeypatch.setattr(db, "list_expiring_infra_assets", lambda warn_days: [
        {"id": 9, "asset_key": "cert:api.example.com:443", "asset_type": "certificate",
         "name": "api", "expires_at": "2020-01-01T00:00:00Z"},
    ])
    calls = _recorder(monkeypatch)

    raised = asyncio.run(infra_asset_sweep._check_expiry())

    assert raised == 1
    assert calls[0]["resource"] == "api"
    assert calls[0]["raw_payload"]["infra_compliance"]["kind"] == "certificate"


def test_check_expiry_one_failure_does_not_block_the_rest(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "list_expiring_credentials", lambda warn_days: [
        {"id": 1, "connector_type": "broken", "display_name": "Broken", "credentials_expires_at": "bad-date"},
        {"id": 2, "connector_type": "fine", "display_name": "Fine", "credentials_expires_at": "2020-01-01T00:00:00Z"},
    ])
    monkeypatch.setattr(db, "list_expiring_infra_assets", lambda warn_days: [])
    calls = []
    def _flaky_ingest(server_name, system_type, event_type, event_id, *a, **kw):
        if "Broken" in event_id:
            raise RuntimeError("simulated DB hiccup")
        calls.append(event_id)
        return 1
    monkeypatch.setattr(mcp_governance, "_ingest_system_event", _flaky_ingest)

    raised = asyncio.run(infra_asset_sweep._check_expiry())

    assert raised == 1  # only the successful one counted
    assert len(calls) == 1
    assert "Fine" in calls[0]


# ── sweep_once ────────────────────────────────────────────────────────────────

def test_sweep_once_reports_combined_counts(monkeypatch):
    monkeypatch.setattr(infra_asset_sweep, "_sync_assets", lambda: asyncio.sleep(0, result=3))
    monkeypatch.setattr(infra_asset_sweep, "_check_expiry", lambda: asyncio.sleep(0, result=2))
    result = asyncio.run(infra_asset_sweep.sweep_once())
    assert result == {"assets_synced": 3, "expiry_findings_raised": 2}
