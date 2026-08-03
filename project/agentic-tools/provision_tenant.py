#!/usr/bin/env python3
"""
provision_tenant.py — create a new Dendrai tenant (database-per-tenant).

Internal operator script, not a public/self-serve endpoint — matches the
"tens of enterprise logos" scale this design targets (see the multi-tenancy
plan). Run once per new customer:

    python provision_tenant.py --slug companyx --display-name "Company X Inc."

What it does, in order:
  1. CREATE DATABASE tenant_<slug> on the shared Postgres cluster (via
     TENANT_DB_ADMIN_DSN, a connection with CREATEDB privilege).
  2. Applies the full schema to that fresh database (db.init_tenant_db —
     identical DDL to every other tenant and to today's single-tenant
     deployment; nothing tenant-specific in the schema itself).
  3. Generates four independent secrets for this tenant (JWT signing key,
     evidence signing key, connector encryption key, write-guard API key)
     — never shared with any other tenant, never derived from a global.
  4. Seeds the auth schema + the two default accounts into the new
     database, reusing auth_db.py's existing seed logic unchanged (initial
     passwords come from AUTH_SEED_ADMIN_PASSWORD/AUTH_SEED_USER_PASSWORD
     or a random one-time password printed to this script's output, exactly
     as documented in .env.example for single-tenant mode).
  5. Registers the tenant + its encrypted secrets in the control-plane
     database (CONTROL_DATABASE_URL), visible to every app instance the
     moment this script exits (no restart needed).

Requires TENANT_MODE=multi's env vars: CONTROL_DATABASE_URL,
CONTROL_PLANE_ROOT_KEY, TENANT_DB_ADMIN_DSN.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import secrets
import sys
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

load_dotenv()

import auth_db
import control_plane
import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("provision_tenant")

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,61}[a-z0-9]$")


def _validate_slug(slug: str) -> str:
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"invalid slug {slug!r} — must be 3-63 lowercase alphanumeric/hyphen "
            "characters, starting with a letter, not ending in a hyphen "
            "(it becomes <slug>.dendrai.ai and a Postgres database name)"
        )
    return slug


def _db_name_for_slug(slug: str) -> str:
    # Postgres identifiers: hyphens aren't valid unquoted, so fold to
    # underscores. Collisions between two different slugs mapping to the
    # same db name are already precluded by tenants.slug being UNIQUE.
    return "tenant_" + slug.replace("-", "_")


def _dsn_with_dbname(admin_dsn: str, db_name: str) -> str:
    """Swap the path (database name) component of a DSN, keeping host/user/
    password/query string intact — used to derive both the maintenance
    connection's target and, after CREATE DATABASE, the new tenant's own
    connection DSN."""
    parts = urlsplit(admin_dsn)
    return urlunsplit((parts.scheme, parts.netloc, "/" + db_name, parts.query, parts.fragment))


def _create_database(admin_dsn: str, db_name: str) -> None:
    import psycopg2
    from psycopg2 import sql

    conn = psycopg2.connect(db._build_dsn(admin_dsn))
    conn.autocommit = True  # CREATE DATABASE cannot run inside a transaction block
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone():
                logger.info("Database %s already exists — reusing it", db_name)
                return
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
        logger.info("Created database %s", db_name)
    finally:
        conn.close()


def provision(slug: str, display_name: str, admin_dsn: str, db_name: str | None = None) -> control_plane.Tenant:
    slug = _validate_slug(slug)
    db_name = db_name or _db_name_for_slug(slug)

    if not control_plane.is_multi_tenant():
        raise RuntimeError("TENANT_MODE=multi must be set to provision a tenant")
    control_plane.init_control_db()

    logger.info("Provisioning tenant slug=%s db=%s", slug, db_name)
    _create_database(admin_dsn, db_name)
    tenant_dsn = _dsn_with_dbname(admin_dsn, db_name)

    logger.info("Applying schema to %s", db_name)
    db.init_tenant_db(tenant_dsn)

    tenant_secrets = control_plane.TenantSecrets(
        auth_jwt_secret=secrets.token_hex(32),
        evidence_signing_key=secrets.token_hex(32),
        connector_encryption_key=_generate_fernet_key(),
        api_key=secrets.token_urlsafe(32),
    )

    # Registering before seeding auth so a failure partway through seeding
    # still leaves a resolvable tenant record an operator can retry against,
    # rather than a half-provisioned database control_plane doesn't know
    # about at all.
    tenant = control_plane.create_tenant_record(slug, display_name, tenant_dsn)
    control_plane.store_tenant_secrets(tenant.id, tenant_secrets)

    logger.info("Seeding auth schema + default accounts for %s", slug)
    db.bind_tenant_pool(tenant.id, tenant_dsn)
    db.bind_tenant_connector_key(tenant_secrets.connector_encryption_key)
    try:
        if not auth_db.init_auth_db():
            raise RuntimeError(f"auth_db.init_auth_db() failed for tenant {slug!r}")
        seeded = auth_db.seed_default_users()
        logger.info("Seeded %d default account(s) for %s", seeded, slug)
    finally:
        db.unbind_tenant_connector_key()
        db.unbind_tenant()

    logger.info(
        "Tenant %s provisioned. Subdomain: https://%s.%s — initial login "
        "credentials follow the same AUTH_SEED_ADMIN_PASSWORD/"
        "AUTH_SEED_USER_PASSWORD (or printed one-time password) convention "
        "as single-tenant mode; see the seed_default_users() log output above.",
        slug, slug, os.environ.get("TENANT_ROOT_DOMAIN", "dendrai.ai"),
    )
    return tenant


def _generate_fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--slug", required=True, help="Subdomain slug, e.g. 'companyx' -> companyx.dendrai.ai")
    parser.add_argument("--display-name", required=True, help="Human-readable tenant name")
    parser.add_argument("--db-name", default=None, help="Override the Postgres database name (default: tenant_<slug>)")
    parser.add_argument(
        "--admin-dsn", default=None,
        help="Postgres DSN with CREATEDB privilege (default: $TENANT_DB_ADMIN_DSN)",
    )
    args = parser.parse_args()

    admin_dsn = args.admin_dsn or os.environ.get("TENANT_DB_ADMIN_DSN", "").strip()
    if not admin_dsn:
        parser.error("--admin-dsn or TENANT_DB_ADMIN_DSN is required")

    try:
        provision(args.slug, args.display_name, admin_dsn, args.db_name)
    except Exception as exc:
        logger.error("Provisioning failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
