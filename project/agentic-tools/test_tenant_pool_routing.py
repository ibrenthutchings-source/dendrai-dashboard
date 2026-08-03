"""
test_tenant_pool_routing.py — db.py's tenant-keyed pool registry and
contextvar-based routing (Phase 2 of multi-tenancy: database-per-tenant).

These exercise the real routing/eviction/fail-closed logic in db.py with a
fake pool object standing in for psycopg2.ThreadedConnectionPool (no real
Postgres needed) — the fake-DB-boundary pattern used throughout this repo's
test suite. The end-to-end HTTP-level isolation tests (Host header ->
middleware -> correct tenant database) belong in a separate test file once
the resolution middleware (Phase 3) lands; this file only proves the
primitive db.py itself now exposes is sound in isolation.
"""
import contextvars

import pytest

import db


class _FakePool:
    """Stands in for pg_pool.ThreadedConnectionPool — records calls, makes no
    real connections."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.closed = False

    def closeall(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_tenant_state():
    """Every test starts from a clean slate: no tenant bound, no pools
    registered, legacy global pool untouched. Restores afterward so this
    file can't leak state into other test modules run in the same process."""
    saved_pools = dict(db._tenant_pools)
    saved_last_used = dict(db._tenant_pool_last_used)
    saved_max = db._MAX_TENANT_POOLS
    token = db._current_tenant.set(None)
    db._tenant_pools.clear()
    db._tenant_pool_last_used.clear()
    yield
    db._current_tenant.reset(token)
    db._tenant_pools.clear()
    db._tenant_pools.update(saved_pools)
    db._tenant_pool_last_used.clear()
    db._tenant_pool_last_used.update(saved_last_used)
    db._MAX_TENANT_POOLS = saved_max


def _bind(monkeypatch, tenant_id: str, dsn: str = "postgresql://x/y"):
    """bind_tenant_pool() using the fake pool class instead of a real one."""
    monkeypatch.setattr(db.pg_pool, "ThreadedConnectionPool", lambda *a, **kw: _FakePool(kw.get("dsn", a[-1] if a else "")))
    db.bind_tenant_pool(tenant_id, dsn)


def test_no_tenant_bound_falls_back_to_legacy_pool():
    """Single-tenant mode (TENANT_MODE=single, the default): with no tenant
    ever bound, _active_pool() must return the legacy global _pool — exactly
    today's behavior, unmodified."""
    assert db._current_tenant.get() is None
    assert db._active_pool() is db._pool


def test_bind_tenant_pool_routes_to_that_tenants_pool(monkeypatch):
    _bind(monkeypatch, "tenant-a", "postgresql://tenant-a-host/db")
    pool = db._active_pool()
    assert isinstance(pool, _FakePool)
    assert pool is db._tenant_pools["tenant-a"]
    assert db._current_tenant.get() == "tenant-a"


def test_unbind_tenant_clears_context(monkeypatch):
    _bind(monkeypatch, "tenant-a")
    db.unbind_tenant()
    assert db._current_tenant.get() is None
    assert db._active_pool() is db._pool


def test_bound_tenant_never_reads_another_tenants_pool(monkeypatch):
    """The core isolation property of this whole layer: binding tenant A
    must make _active_pool() return tenant A's pool object and nothing else
    — never tenant B's, even though both are registered simultaneously."""
    monkeypatch.setattr(db.pg_pool, "ThreadedConnectionPool", lambda *a, **kw: _FakePool(kw.get("dsn")))
    db.bind_tenant_pool("tenant-a", "postgresql://a")
    pool_a = db._tenant_pools["tenant-a"]
    db.bind_tenant_pool("tenant-b", "postgresql://b")
    pool_b = db._tenant_pools["tenant-b"]
    assert pool_a is not pool_b

    db._current_tenant.set("tenant-a")
    assert db._active_pool() is pool_a

    db._current_tenant.set("tenant-b")
    assert db._active_pool() is pool_b


def test_fail_closed_when_tenant_set_but_pool_missing():
    """A tenant_id bound in the context without a corresponding pool is a
    programming error (bind_tenant_pool() wasn't called) — must raise, never
    silently fall back to the legacy/global pool, which could belong to a
    different tenant's data."""
    db._current_tenant.set("ghost-tenant")
    with pytest.raises(RuntimeError, match="ghost-tenant"):
        db._active_pool()
    # is_available() must translate that failure into False, not propagate —
    # it's used as a health-check-style boolean elsewhere in the codebase.
    assert db.is_available() is False


def test_lru_eviction_bounds_pool_count(monkeypatch):
    monkeypatch.setattr(db.pg_pool, "ThreadedConnectionPool", lambda *a, **kw: _FakePool(kw.get("dsn")))
    monkeypatch.setattr(db, "_MAX_TENANT_POOLS", 2)

    db.bind_tenant_pool("tenant-1", "postgresql://1")
    pool_1 = db._tenant_pools["tenant-1"]
    db.bind_tenant_pool("tenant-2", "postgresql://2")
    db.bind_tenant_pool("tenant-3", "postgresql://3")  # should evict tenant-1 (least recently used)

    assert "tenant-1" not in db._tenant_pools
    assert pool_1.closed is True
    assert set(db._tenant_pools) == {"tenant-2", "tenant-3"}


def test_contextvar_isolation_across_concurrent_contexts(monkeypatch):
    """Simulates two concurrent requests each binding a different tenant.
    contextvars.Context makes each run's state genuinely independent — this
    test exists to prove that property explicitly rather than trust the
    primitive, since it's the mechanism the whole isolation story rests on."""
    monkeypatch.setattr(db.pg_pool, "ThreadedConnectionPool", lambda *a, **kw: _FakePool(kw.get("dsn")))

    results = {}

    def _run_as_tenant(tenant_id):
        db.bind_tenant_pool(tenant_id, f"postgresql://{tenant_id}")
        results[tenant_id] = db._active_pool().dsn

    ctx_a = contextvars.copy_context()
    ctx_b = contextvars.copy_context()
    ctx_a.run(_run_as_tenant, "tenant-a")
    ctx_b.run(_run_as_tenant, "tenant-b")

    assert results["tenant-a"].startswith("postgresql://tenant-a")
    assert results["tenant-b"].startswith("postgresql://tenant-b")
    # Neither copied context's binding should have leaked into this (the
    # parent) context.
    assert db._current_tenant.get() is None
