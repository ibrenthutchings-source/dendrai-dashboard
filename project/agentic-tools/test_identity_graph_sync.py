#!/usr/bin/env python3
"""
Tests for identity_graph_sync.py — the sync that pulls real user<->role
assignments and open SoD violations from every active Oracle Fusion
connector into observability.identity_role_edges/.sod_violations, so
The Graph Architect's blast-radius/SPoF checks stop operating on
permanently-zeroed inputs (see mcp_governance.py's _process_one enrichment
and this module's docstring for the full story).

Follows test_connector_poller.py's pattern: mock db.* and the adapter
function at the boundary, exercise real dispatch/aggregation logic.
"""
from __future__ import annotations

import asyncio

import db
import identity_graph_sync as igs
import oracle_fusion_tool


def _connector(cid=1, connector_type="oracle_fusion", active=True):
    return {"id": cid, "connector_type": connector_type, "display_name": f"OF-{cid}", "active": active}


def _full_connector(cid=1):
    return {
        "id": cid, "connector_type": "oracle_fusion", "display_name": f"OF-{cid}",
        "base_url": "https://fusion.example.com", "active": True,
        "credentials": {"client_id": "cid", "client_secret": "secret"},
    }


# ── sweep_once — connector-type/active filtering ────────────────────────────

def test_sweep_once_skips_non_oracle_fusion_connectors(monkeypatch):
    monkeypatch.setattr(db, "list_poll_connectors", lambda: [
        _connector(1, "sap_hana"), _connector(2, "sailpoint"),
    ])
    called = []
    monkeypatch.setattr(igs, "_sync_one", lambda c: called.append(c["id"]) or asyncio.sleep(0, result={}))

    result = asyncio.run(igs.sweep_once())

    assert called == []
    assert result == {}


def test_sweep_once_skips_inactive_oracle_fusion_connectors(monkeypatch):
    monkeypatch.setattr(db, "list_poll_connectors", lambda: [_connector(1, active=False)])
    called = []
    monkeypatch.setattr(igs, "_sync_one", lambda c: called.append(c["id"]) or asyncio.sleep(0, result={}))

    asyncio.run(igs.sweep_once())

    assert called == []


def test_sweep_once_syncs_every_active_oracle_fusion_connector(monkeypatch):
    monkeypatch.setattr(db, "list_poll_connectors", lambda: [
        _connector(1), _connector(2), _connector(3, "sap_hana"),
    ])

    async def _fake_sync_one(c):
        return {"connector_id": c["id"], "roles": 5}

    monkeypatch.setattr(igs, "_sync_one", _fake_sync_one)

    result = asyncio.run(igs.sweep_once())

    assert set(result.keys()) == {1, 2}
    assert result[1]["roles"] == 5


# ── _sync_one — never-raises failure modes (mirrors test_connector_poller.py) ─

def test_sync_one_encryption_key_missing_does_not_raise(monkeypatch):
    def _raise(cid, full):
        raise db.EncryptionKeyMissing("no key")
    monkeypatch.setattr(db, "get_poll_connector", _raise)

    result = asyncio.run(igs._sync_one(_connector(1)))

    assert result["connector_id"] == 1
    assert "error" in result


def test_sync_one_connector_not_found_does_not_raise(monkeypatch):
    monkeypatch.setattr(db, "get_poll_connector", lambda cid, full: None)

    result = asyncio.run(igs._sync_one(_connector(1)))

    assert "error" in result


def test_sync_one_get_user_roles_error_does_not_raise(monkeypatch):
    monkeypatch.setattr(db, "get_poll_connector", lambda cid, full: _full_connector(cid))
    monkeypatch.setattr(oracle_fusion_tool, "get_user_roles", lambda *a, **kw: {"error": "boom", "assignments": []})

    result = asyncio.run(igs._sync_one(_connector(1)))

    assert "error" in result
    assert "boom" in result["error"]


# ── _sync_one — happy path: real data reaches db.upsert_* ───────────────────

def test_sync_one_happy_path_persists_roles_and_sod_violations(monkeypatch):
    monkeypatch.setattr(db, "get_poll_connector", lambda cid, full: _full_connector(cid))
    monkeypatch.setattr(oracle_fusion_tool, "get_user_roles", lambda *a, **kw: {
        "assignments": [{"username": "alice@co.com", "role": "AP_Manager", "role_id": "r1"}],
    })
    monkeypatch.setattr(oracle_fusion_tool, "get_sod_violations", lambda *a, **kw: {
        "violations": [{"violation_id": "v1", "username": "alice@co.com", "policy_name": "AP-SoD-01",
                        "conflict_roles": ["AP_Manager", "AP_Approver"], "risk_level": "High", "status": "Open"}],
    })

    upserted_roles = {}
    upserted_sod = {}
    monkeypatch.setattr(db, "upsert_identity_role_edges", lambda cid, assignments: upserted_roles.setdefault(cid, assignments) and len(assignments))
    monkeypatch.setattr(db, "upsert_sod_violations", lambda cid, violations: upserted_sod.setdefault(cid, violations) and len(violations))

    result = asyncio.run(igs._sync_one(_connector(7)))

    assert result["connector_id"] == 7
    assert result["roles"] == 1
    assert result["sod_violations"] == 1
    assert upserted_roles[7][0]["username"] == "alice@co.com"
    assert upserted_sod[7][0]["violation_id"] == "v1"


def test_sync_one_client_uses_connector_credentials(monkeypatch):
    """The client passed to get_user_roles/get_sod_violations must be built
    from THIS connector's own base_url/credentials, not a default/global
    client — the whole point of a per-connector sync."""
    monkeypatch.setattr(db, "get_poll_connector", lambda cid, full: {
        "id": cid, "connector_type": "oracle_fusion", "display_name": "OF",
        "base_url": "https://tenant-specific.example.com", "active": True,
        "credentials": {"client_id": "tenant-cid", "client_secret": "tenant-secret"},
    })

    seen_hosts = []

    def _fake_get_user_roles(username, role_name, max_items, client):
        seen_hosts.append(client.host)
        return {"assignments": []}

    monkeypatch.setattr(oracle_fusion_tool, "get_user_roles", _fake_get_user_roles)
    monkeypatch.setattr(oracle_fusion_tool, "get_sod_violations", lambda *a, **kw: {"violations": []})
    monkeypatch.setattr(db, "upsert_identity_role_edges", lambda cid, assignments: 0)
    monkeypatch.setattr(db, "upsert_sod_violations", lambda cid, violations: 0)

    asyncio.run(igs._sync_one(_connector(1)))

    assert seen_hosts == ["https://tenant-specific.example.com"]
