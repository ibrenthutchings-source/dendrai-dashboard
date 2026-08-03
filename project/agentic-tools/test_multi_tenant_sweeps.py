"""
test_multi_tenant_sweeps.py — api_server._run_cycle_for_all_tenants(), the
Phase 5b mechanism that makes the 9 background pollers/sweeps
(mcp_governance, connector_poller, risk_waiver_sweep, ...) iterate every
active tenant instead of assuming one global database.

Each sweep module's own sweep_once()/_process_batch()/etc. is completely
unmodified by this — the only new logic is the per-tenant bind/call/unbind
wrapper itself, so that's what these tests target directly. No pytest-asyncio
in this repo's test deps, so async calls are driven with asyncio.run()
directly, matching the convention used elsewhere in this suite.
"""
from __future__ import annotations

import asyncio

import pytest

import api_server
import control_plane
import db


class _FakePool:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def closeall(self):
        pass


TENANT_A = control_plane.Tenant(
    id="tenant-a-id", slug="companya", display_name="Company A",
    status="active", db_dsn="postgresql://tenant-a-host/db",
)
TENANT_B = control_plane.Tenant(
    id="tenant-b-id", slug="companyb", display_name="Company B",
    status="active", db_dsn="postgresql://tenant-b-host/db",
)
SECRETS_A = control_plane.TenantSecrets(
    auth_jwt_secret="jwt-a", evidence_signing_key="ev-a",
    connector_encryption_key="conn-a", api_key="api-a",
)
SECRETS_B = control_plane.TenantSecrets(
    auth_jwt_secret="jwt-b", evidence_signing_key="ev-b",
    connector_encryption_key="conn-b", api_key="api-b",
)

_SECRETS_BY_ID = {"tenant-a-id": SECRETS_A, "tenant-b-id": SECRETS_B}


@pytest.fixture(autouse=True)
def _fake_pools(monkeypatch):
    monkeypatch.setattr(db.pg_pool, "ThreadedConnectionPool", lambda *a, **kw: _FakePool(kw.get("dsn")))
    db._tenant_pools.clear()
    db._tenant_pool_last_used.clear()
    yield
    db._tenant_pools.clear()
    db._tenant_pool_last_used.clear()


def test_cycle_runs_once_per_active_tenant_with_correct_binding(monkeypatch):
    monkeypatch.setattr(control_plane, "list_active_tenants", lambda: [TENANT_A, TENANT_B])
    monkeypatch.setattr(control_plane, "get_tenant_secrets", lambda tid: _SECRETS_BY_ID[tid])

    seen_tenants = []

    async def cycle_fn():
        seen_tenants.append(db._current_tenant.get())

    asyncio.run(api_server._run_cycle_for_all_tenants(cycle_fn, "test-sweep"))

    assert seen_tenants == ["tenant-a-id", "tenant-b-id"]
    # Fully unbound once the whole pass completes — no tenant left bound
    # for whatever runs next in this process.
    assert db._current_tenant.get() is None


def test_one_tenants_cycle_failure_does_not_block_the_other(monkeypatch):
    monkeypatch.setattr(control_plane, "list_active_tenants", lambda: [TENANT_A, TENANT_B])
    monkeypatch.setattr(control_plane, "get_tenant_secrets", lambda tid: _SECRETS_BY_ID[tid])

    seen_tenants = []

    async def cycle_fn():
        tid = db._current_tenant.get()
        if tid == "tenant-a-id":
            raise RuntimeError("simulated failure for tenant A")
        seen_tenants.append(tid)

    asyncio.run(api_server._run_cycle_for_all_tenants(cycle_fn, "test-sweep"))

    assert seen_tenants == ["tenant-b-id"]
    assert db._current_tenant.get() is None


def test_tenant_with_no_provisioned_secrets_is_skipped_not_crashed(monkeypatch):
    monkeypatch.setattr(control_plane, "list_active_tenants", lambda: [TENANT_A, TENANT_B])

    def _get_secrets(tid):
        if tid == "tenant-a-id":
            raise control_plane.TenantNotFound(tid)
        return SECRETS_B

    monkeypatch.setattr(control_plane, "get_tenant_secrets", _get_secrets)

    seen_tenants = []

    async def cycle_fn():
        seen_tenants.append(db._current_tenant.get())

    asyncio.run(api_server._run_cycle_for_all_tenants(cycle_fn, "test-sweep"))

    assert seen_tenants == ["tenant-b-id"]


def test_cycle_never_sees_two_tenants_pool_at_once(monkeypatch):
    """The property the whole per-tenant scheduler exists to guarantee:
    while tenant A's cycle is executing, db._active_pool() must resolve to
    tenant A's pool and nothing else — never tenant B's, even though both
    are registered in db._tenant_pools by the time tenant B's turn comes."""
    monkeypatch.setattr(control_plane, "list_active_tenants", lambda: [TENANT_A, TENANT_B])
    monkeypatch.setattr(control_plane, "get_tenant_secrets", lambda tid: _SECRETS_BY_ID[tid])

    seen_dsns = []

    async def cycle_fn():
        seen_dsns.append(db._active_pool().dsn)

    asyncio.run(api_server._run_cycle_for_all_tenants(cycle_fn, "test-sweep"))

    assert seen_dsns[0].startswith("postgresql://tenant-a-host")
    assert seen_dsns[1].startswith("postgresql://tenant-b-host")
