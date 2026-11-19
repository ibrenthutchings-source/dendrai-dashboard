"""
test_tenant_isolation.py — end-to-end proof that a request resolved to one
tenant's subdomain can never read, write, sign, or authenticate against
another tenant's data (Phases 3/4/5 of multi-tenancy: database-per-tenant).

Deliberately does NOT import the full api_server.app (heavy MCP-server
mounting, 10+ mounted MCP sub-apps, real background-loop startup — far more
than this file needs). Instead, following the pattern in
test_github_webhook_listener.py, this mounts just
api_server._TenantResolutionMiddleware onto a minimal FastAPI app with a
couple of probe routes — exactly what's needed to exercise the real
middleware, the real db.py pool routing, and the real auth_endpoints JWT
tenant-claim check, against two fake tenants (control_plane.resolve_tenant/get_tenant_secrets
mocked — the fake-DB-boundary pattern used throughout this repo's suite;
psycopg2 connections are faked too, so no live Postgres is needed).

What this file does NOT prove (left to the manual two-tenant Postgres
verification step in the plan): that two *real* databases with real rows
stay separated under real queries. That's a structural consequence of
Phase 2's pool routing (test_tenant_pool_routing.py) plus every db.* call
site being unmodified — there's no per-query tenant filter to get wrong —
but it's still worth the manual check before declaring this shippable.
"""
from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

import api_server
import auth_endpoints
import control_plane
import db


class _FakePool:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def closeall(self):
        pass


_CONN_KEY_A = Fernet.generate_key().decode()
_CONN_KEY_B = Fernet.generate_key().decode()

TENANT_A = control_plane.Tenant(
    id="tenant-a-id", slug="companya", display_name="Company A",
    status="active", db_dsn="postgresql://tenant-a-host/db",
)
TENANT_B = control_plane.Tenant(
    id="tenant-b-id", slug="companyb", display_name="Company B",
    status="active", db_dsn="postgresql://tenant-b-host/db",
)
SECRETS_A = control_plane.TenantSecrets(
    auth_jwt_secret="jwt-secret-a", evidence_signing_key="evidence-key-a",
    connector_encryption_key=_CONN_KEY_A, api_key="api-key-a",
)
SECRETS_B = control_plane.TenantSecrets(
    auth_jwt_secret="jwt-secret-b", evidence_signing_key="evidence-key-b",
    connector_encryption_key=_CONN_KEY_B, api_key="api-key-b",
)

_TENANTS_BY_SLUG = {"companya": TENANT_A, "companyb": TENANT_B}
_SECRETS_BY_ID = {"tenant-a-id": SECRETS_A, "tenant-b-id": SECRETS_B}


def _fake_resolve_tenant(slug, use_cache=True):
    tenant = _TENANTS_BY_SLUG.get(slug)
    if tenant is None or tenant.status != "active":
        raise control_plane.TenantNotFound(slug)
    return tenant


def _fake_get_tenant_secrets(tenant_id):
    secrets = _SECRETS_BY_ID.get(tenant_id)
    if secrets is None:
        raise control_plane.TenantNotFound(tenant_id)
    return secrets


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(control_plane, "is_multi_tenant", lambda: True)
    monkeypatch.setattr(control_plane, "resolve_tenant", _fake_resolve_tenant)
    monkeypatch.setattr(control_plane, "get_tenant_secrets", _fake_get_tenant_secrets)
    monkeypatch.setattr(db.pg_pool, "ThreadedConnectionPool", lambda *a, **kw: _FakePool(kw.get("dsn")))

    db._tenant_pools.clear()
    db._tenant_pool_last_used.clear()

    app = FastAPI()
    app.add_middleware(api_server._TenantResolutionMiddleware)

    @app.get("/whoami")
    async def whoami(request: Request):
        tenant = getattr(request.state, "tenant", None)
        bound_pool = db._tenant_pools.get(db._current_tenant.get()) if db._current_tenant.get() else None
        return {
            "tenant_slug": tenant.slug if tenant else None,
            "pool_dsn": bound_pool.dsn if bound_pool else None,
        }

    @app.get("/guarded", dependencies=[Depends(api_server._require_api_key)])
    async def guarded():
        return {"ok": True}

    with TestClient(app) as c:
        yield c

    db._tenant_pools.clear()
    db._tenant_pool_last_used.clear()


# ── Host-header resolution ────────────────────────────────────────────────────

def test_unknown_subdomain_rejected_before_reaching_the_route(client):
    resp = client.get("/whoami", headers={"Host": "unregistered-co.dendrai.ai"})
    assert resp.status_code == 404


def test_known_subdomain_resolves_to_its_own_tenant_and_pool(client):
    resp = client.get("/whoami", headers={"Host": "companya.dendrai.ai"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_slug"] == "companya"
    assert body["pool_dsn"].startswith("postgresql://tenant-a-host")


def test_switching_host_header_switches_tenant_with_no_leakage(client):
    """The same TestClient/process serves both tenants back to back — the
    property under test is that request N+1 never sees request N's tenant."""
    resp_a = client.get("/whoami", headers={"Host": "companya.dendrai.ai"})
    resp_b = client.get("/whoami", headers={"Host": "companyb.dendrai.ai"})
    assert resp_a.json()["pool_dsn"].startswith("postgresql://tenant-a-host")
    assert resp_b.json()["pool_dsn"].startswith("postgresql://tenant-b-host")
    # And context is fully unbound between/after requests — no residual tenant.
    assert db._current_tenant.get() is None


def test_context_unbound_even_after_rejected_request(client):
    client.get("/whoami", headers={"Host": "unregistered-co.dendrai.ai"})
    assert db._current_tenant.get() is None


# ── Write-guard API key scoped per tenant ────────────────────────────────────

def test_write_guard_rejects_wrong_tenants_api_key(client):
    resp = client.get(
        "/guarded",
        headers={"Host": "companya.dendrai.ai", "X-API-Key": "api-key-b"},
    )
    assert resp.status_code == 403


def test_write_guard_accepts_matching_tenants_api_key(client):
    resp = client.get(
        "/guarded",
        headers={"Host": "companya.dendrai.ai", "X-API-Key": "api-key-a"},
    )
    assert resp.status_code == 200


# ── JWT tenant binding (auth_endpoints) ──────────────────────────────────────

def test_jwt_minted_for_tenant_a_rejected_when_verified_as_tenant_b():
    auth_endpoints.bind_tenant_secret("tenant-a-id", "secret-a")
    try:
        token = auth_endpoints._create_jwt({"id": 1, "username": "u", "role": "user"}, "jti-1")
    finally:
        auth_endpoints.unbind_tenant_secret()

    auth_endpoints.bind_tenant_secret("tenant-b-id", "secret-b")
    try:
        assert auth_endpoints.decode_jwt(token) is None
    finally:
        auth_endpoints.unbind_tenant_secret()


def test_jwt_tenant_claim_independently_catches_a_shared_signing_key():
    """Simulates a provisioning bug where both tenants end up with the same
    JWT secret — the explicit tenant_id claim check must still reject the
    cross-tenant replay on its own, not rely solely on key mismatch."""
    auth_endpoints.bind_tenant_secret("tenant-a-id", "same-secret-by-mistake")
    try:
        token = auth_endpoints._create_jwt({"id": 1, "username": "u", "role": "user"}, "jti-2")
    finally:
        auth_endpoints.unbind_tenant_secret()

    auth_endpoints.bind_tenant_secret("tenant-b-id", "same-secret-by-mistake")
    try:
        assert auth_endpoints.decode_jwt(token) is None
    finally:
        auth_endpoints.unbind_tenant_secret()


def test_jwt_accepted_on_its_own_tenant():
    auth_endpoints.bind_tenant_secret("tenant-a-id", "secret-a")
    try:
        token = auth_endpoints._create_jwt({"id": 1, "username": "u", "role": "user"}, "jti-3")
        payload = auth_endpoints.decode_jwt(token)
        assert payload is not None
        assert payload["tenant_id"] == "tenant-a-id"
    finally:
        auth_endpoints.unbind_tenant_secret()


# ── Connector-credential encryption key scoped per tenant ───────────────────

def test_connector_credentials_undecryptable_across_tenants():
    db.bind_tenant_connector_key(_CONN_KEY_A)
    try:
        token = db.encrypt_sensitive_json({"secret": "tenant-a-only"})
    finally:
        db.unbind_tenant_connector_key()

    db.bind_tenant_connector_key(_CONN_KEY_B)
    try:
        with pytest.raises(db.EncryptionKeyMissing):
            db.decrypt_sensitive_json(token)
    finally:
        db.unbind_tenant_connector_key()

    db.bind_tenant_connector_key(_CONN_KEY_A)
    try:
        assert db.decrypt_sensitive_json(token) == {"secret": "tenant-a-only"}
    finally:
        db.unbind_tenant_connector_key()
