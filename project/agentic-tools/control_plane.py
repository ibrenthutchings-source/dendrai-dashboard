#!/usr/bin/env python3
"""
control_plane.py — Multi-tenant control plane: tenant registry + per-tenant secrets.

This is the one deliberately global piece of a database-per-tenant deployment:
a small database (CONTROL_DATABASE_URL) mapping subdomain slugs
("companyx" -> companyx.dendrai.ai) to an encrypted per-tenant Postgres DSN
and an encrypted bundle of per-tenant secrets (JWT signing key, evidence
signing key, connector encryption key, ingest API key). Every other table in
the system — companies, observability.*, auth.* — lives inside the tenant's
own database (see db.py's tenant-keyed pool registry) and needs no tenant_id
column: physical database separation is the isolation boundary, not a WHERE
clause.

TENANT_MODE controls whether this module is consulted at all:
    TENANT_MODE=single (default) — control plane is not used. api_server.py's
        resolution middleware short-circuits to today's single-tenant
        behavior (DATABASE_URL / AUTH_JWT_SECRET / etc. straight from env),
        so local dev and any deployment that hasn't opted into multi-tenancy
        keeps working unchanged.
    TENANT_MODE=multi — CONTROL_DATABASE_URL and CONTROL_PLANE_ROOT_KEY are
        required; every request must resolve to a known, active tenant or
        is rejected before touching any tenant database.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

_HAS_PSYCOPG2 = False
try:
    import psycopg2
    from psycopg2 import pool as pg_pool
    _HAS_PSYCOPG2 = True
except ImportError:
    pass

_HAS_CRYPTOGRAPHY = False
try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    pass

logger = logging.getLogger("control_plane")

_pool: Optional["pg_pool.ThreadedConnectionPool"] = None

_DDL = """
CREATE TABLE IF NOT EXISTS tenants (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug             VARCHAR(63) UNIQUE NOT NULL,
    display_name     VARCHAR(255) NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'active',
    db_dsn_enc       TEXT NOT NULL,
    isolation_tier   VARCHAR(16) NOT NULL DEFAULT 'shared_cluster',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_secrets (
    tenant_id                     UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    auth_jwt_secret_enc           TEXT NOT NULL,
    evidence_signing_key_enc      TEXT NOT NULL,
    connector_encryption_key_enc  TEXT NOT NULL,
    api_key_enc                   TEXT NOT NULL,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class ControlPlaneUnavailable(RuntimeError):
    """CONTROL_DATABASE_URL/CONTROL_PLANE_ROOT_KEY not configured, or the control DB is unreachable."""


class TenantNotFound(RuntimeError):
    """No active tenant matches the resolved subdomain slug — request must be rejected, not defaulted."""


@dataclass(frozen=True)
class Tenant:
    id: str
    slug: str
    display_name: str
    status: str
    db_dsn: str  # decrypted


@dataclass(frozen=True)
class TenantSecrets:
    auth_jwt_secret: str
    evidence_signing_key: str
    connector_encryption_key: str
    api_key: str


def is_multi_tenant() -> bool:
    return os.environ.get("TENANT_MODE", "single").strip().lower() == "multi"


def _root_fernet() -> "Fernet":
    if not _HAS_CRYPTOGRAPHY:
        raise ControlPlaneUnavailable("cryptography package not installed — run: pip install cryptography")
    key = os.environ.get("CONTROL_PLANE_ROOT_KEY", "").strip()
    if not key:
        raise ControlPlaneUnavailable(
            "CONTROL_PLANE_ROOT_KEY is not set — generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "and set it before running in TENANT_MODE=multi. This key protects every tenant's DB DSN "
            "and per-tenant secrets — rotating it orphans every provisioned tenant, so it must stay stable."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    return _root_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        return _root_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise ControlPlaneUnavailable(
            "Could not decrypt a control-plane secret — CONTROL_PLANE_ROOT_KEY has changed "
            "since it was written, or the value is corrupted."
        )


def init_control_db() -> bool:
    """Initialize the control-plane connection pool and create tenants/tenant_secrets."""
    global _pool
    if _pool is not None:
        return True
    if not _HAS_PSYCOPG2:
        raise ControlPlaneUnavailable("psycopg2 not installed — run: pip install psycopg2-binary")
    url = os.environ.get("CONTROL_DATABASE_URL", "").strip()
    if not url:
        raise ControlPlaneUnavailable("CONTROL_DATABASE_URL is not set — required when TENANT_MODE=multi")
    dsn = url if "connect_timeout" in url else url + ("&" if "?" in url else "?") + "connect_timeout=8"
    if "sslmode" not in dsn:
        ssl_mode = os.environ.get("DATABASE_SSL_MODE", "require").strip()
        if ssl_mode:
            dsn = dsn + "&sslmode=" + ssl_mode
    _pool = pg_pool.ThreadedConnectionPool(1, 5, dsn=dsn)
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    finally:
        _pool.putconn(conn)
    logger.info("Control-plane database initialized (tenants, tenant_secrets)")
    return True


@contextmanager
def _conn():
    if _pool is None:
        raise ControlPlaneUnavailable("Control-plane database not initialized — call init_control_db() first")
    conn = _pool.getconn()
    broken = False
    try:
        yield conn
        conn.commit()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        broken = True
        raise
    except Exception:
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        _pool.putconn(conn, close=broken)


# ── Resolution cache ──────────────────────────────────────────────────────────
# Tenant metadata changes only on provisioning/suspension — a short TTL cache
# keeps every request from hitting the control-plane DB, while still picking
# up a suspension within a bounded window rather than requiring a restart.

_CACHE_TTL_S = float(os.environ.get("TENANT_CACHE_TTL_S", "30"))
_tenant_cache: dict[str, tuple[float, Optional[Tenant]]] = {}


def _row_to_tenant(row) -> Tenant:
    tid, slug, display_name, status, db_dsn_enc = row
    return Tenant(
        id=str(tid),
        slug=slug,
        display_name=display_name,
        status=status,
        db_dsn=decrypt_secret(db_dsn_enc),
    )


def resolve_tenant(slug: str, *, use_cache: bool = True) -> Tenant:
    """Resolve a subdomain slug to an active Tenant. Raises TenantNotFound for
    anything unknown, inactive, or malformed — callers must fail closed, never
    fall back to a default tenant or database."""
    slug = (slug or "").strip().lower()
    if not slug:
        raise TenantNotFound("empty tenant slug")

    if use_cache:
        cached = _tenant_cache.get(slug)
        if cached is not None and (time.time() - cached[0]) < _CACHE_TTL_S:
            if cached[1] is None:
                raise TenantNotFound(f"no active tenant for slug={slug!r}")
            return cached[1]

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, slug, display_name, status, db_dsn_enc FROM tenants "
                "WHERE slug = %s AND status = 'active'",
                (slug,),
            )
            row = cur.fetchone()

    tenant = _row_to_tenant(row) if row else None
    _tenant_cache[slug] = (time.time(), tenant)
    if tenant is None:
        raise TenantNotFound(f"no active tenant for slug={slug!r}")
    return tenant


def invalidate_cache(slug: Optional[str] = None) -> None:
    """Called after provisioning/suspending a tenant so the change is visible
    immediately instead of waiting out the TTL."""
    if slug is None:
        _tenant_cache.clear()
    else:
        _tenant_cache.pop(slug.strip().lower(), None)


def list_active_tenants() -> list[Tenant]:
    """Every active tenant — used by background loops to iterate all tenant databases."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, slug, display_name, status, db_dsn_enc FROM tenants WHERE status = 'active'"
            )
            rows = cur.fetchall()
    return [_row_to_tenant(r) for r in rows]


def get_tenant_secrets(tenant_id: str) -> TenantSecrets:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT auth_jwt_secret_enc, evidence_signing_key_enc, "
                "connector_encryption_key_enc, api_key_enc "
                "FROM tenant_secrets WHERE tenant_id = %s",
                (tenant_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise TenantNotFound(f"no secrets provisioned for tenant_id={tenant_id!r}")
    return TenantSecrets(
        auth_jwt_secret=decrypt_secret(row[0]),
        evidence_signing_key=decrypt_secret(row[1]),
        connector_encryption_key=decrypt_secret(row[2]),
        api_key=decrypt_secret(row[3]),
    )


# ── Provisioning primitives (used by provision_tenant.py) ────────────────────

def create_tenant_record(slug: str, display_name: str, db_dsn: str,
                          isolation_tier: str = "shared_cluster") -> Tenant:
    """Insert a new tenant row. Does NOT create the underlying database or
    secrets — see provision_tenant.py for the full provisioning sequence."""
    slug = slug.strip().lower()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (slug, display_name, db_dsn_enc, isolation_tier) "
                "VALUES (%s, %s, %s, %s) RETURNING id, slug, display_name, status, db_dsn_enc",
                (slug, display_name, encrypt_secret(db_dsn), isolation_tier),
            )
            row = cur.fetchone()
    invalidate_cache(slug)
    return _row_to_tenant(row)


def store_tenant_secrets(tenant_id: str, secrets: TenantSecrets) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenant_secrets "
                "(tenant_id, auth_jwt_secret_enc, evidence_signing_key_enc, "
                " connector_encryption_key_enc, api_key_enc) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "  auth_jwt_secret_enc = EXCLUDED.auth_jwt_secret_enc, "
                "  evidence_signing_key_enc = EXCLUDED.evidence_signing_key_enc, "
                "  connector_encryption_key_enc = EXCLUDED.connector_encryption_key_enc, "
                "  api_key_enc = EXCLUDED.api_key_enc",
                (
                    tenant_id,
                    encrypt_secret(secrets.auth_jwt_secret),
                    encrypt_secret(secrets.evidence_signing_key),
                    encrypt_secret(secrets.connector_encryption_key),
                    encrypt_secret(secrets.api_key),
                ),
            )


def set_tenant_status(slug: str, status: str) -> None:
    """status: 'active' or 'suspended'. Suspension takes effect within
    TENANT_CACHE_TTL_S even without a restart, via invalidate_cache()."""
    if status not in ("active", "suspended"):
        raise ValueError(f"invalid tenant status: {status!r}")
    slug = slug.strip().lower()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE tenants SET status = %s WHERE slug = %s", (status, slug))
    invalidate_cache(slug)
