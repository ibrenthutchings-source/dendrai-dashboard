#!/usr/bin/env python3
"""
tenant_admin.py — day-2 operations for TENANT_MODE=multi.

    python tenant_admin.py list
    python tenant_admin.py suspend  --slug companyx
    python tenant_admin.py activate --slug companyx
    python tenant_admin.py migrate  [--slug companyx]

`migrate` re-applies the full (idempotent) schema to every active tenant's
database, or just one. Run it after deploying a release that changes DDL:
the schema is applied once at provisioning (provision_tenant.py), not at app
startup, so existing tenant databases otherwise never receive new tables or
columns.

Requires CONTROL_DATABASE_URL and CONTROL_PLANE_ROOT_KEY (same env as the app).
Suspension takes effect on running app instances within TENANT_CACHE_TTL_S.
"""
from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

import control_plane  # noqa: E402
import db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("tenant_admin")


def _all_tenants() -> list[tuple]:
    """(id, slug, display_name, status) for every tenant, including suspended."""
    with control_plane._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, slug, display_name, status FROM tenants ORDER BY slug")
            return cur.fetchall()


def cmd_list(_args) -> int:
    for _id, slug, name, status in _all_tenants():
        print(f"{slug:<30} {status:<10} {name}")
    return 0


def cmd_status(args, status: str) -> int:
    known = {row[1] for row in _all_tenants()}
    if args.slug.strip().lower() not in known:
        logger.error("Unknown tenant %r", args.slug)
        return 1
    control_plane.set_tenant_status(args.slug, status)
    logger.info("Tenant %s is now %s", args.slug, status)
    return 0


def cmd_migrate(args) -> int:
    tenants = control_plane.list_active_tenants()
    if args.slug:
        tenants = [t for t in tenants if t.slug == args.slug.strip().lower()]
        if not tenants:
            logger.error("No active tenant %r", args.slug)
            return 1
    failed = 0
    for t in tenants:
        try:
            db.init_tenant_db(t.db_dsn)
            logger.info("migrated %s", t.slug)
        except Exception as exc:  # keep going; report at the end
            failed += 1
            logger.error("FAILED %s: %s", t.slug, exc)
    logger.info("%d migrated, %d failed", len(tenants) - failed, failed)
    return 1 if failed else 0


def main() -> int:
    if not control_plane.is_multi_tenant():
        print("TENANT_MODE is not 'multi' — nothing to administer.", file=sys.stderr)
        return 2
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("suspend", "activate"):
        sub.add_parser(name).add_argument("--slug", required=True)
    sub.add_parser("migrate").add_argument("--slug", default=None)
    args = p.parse_args()
    try:
        control_plane.init_control_db()
    except control_plane.ControlPlaneUnavailable as exc:
        logger.error("Control plane unavailable: %s", exc)
        return 1
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "migrate":
        return cmd_migrate(args)
    return cmd_status(args, "suspended" if args.cmd == "suspend" else "active")


if __name__ == "__main__":
    sys.exit(main())
