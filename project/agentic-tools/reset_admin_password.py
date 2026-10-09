#!/usr/bin/env python3
"""
One-off admin password reset — for exactly the situation seed_default_users()
can't help with: the seeded 'admin' account's original password is gone
(set via AUTH_SEED_ADMIN_PASSWORD, or a one-time generated password only
ever printed to that first deploy's logs — both unrecoverable once lost) and
there's no OTHER admin account to use PUT /auth/admin/users/{id}/password.

Does exactly what that admin-facing endpoint does (auth_endpoints.py's
admin_set_password), via the same auth_db functions, run directly against
the database instead of through the API — because the API path requires
being logged in as an admin already, which is the thing we don't have:
  1. update_password()      — new bcrypt hash + push to password_history
  2. set_must_change_pw()   — force a change at next login, same as any
                               admin-issued credential
  3. revoke_all_user_sessions() — any stale session for this user is killed

Never run this against a database you don't control. It reads DATABASE_URL
(or whatever this environment's db.py normally resolves) from the process
environment — nothing is passed on the command line, and the generated
password is printed ONCE to this script's own stdout, never logged or
persisted anywhere else.

Usage (against Railway's sandbox environment, env vars injected by the CLI
rather than typed in — this never touches a hardcoded credential):
    railway run --service dendrai-app --environment sandbox \\
        python agentic-tools/reset_admin_password.py

    railway run --service dendrai-app --environment sandbox \\
        python agentic-tools/reset_admin_password.py --username dendrai
"""

from __future__ import annotations

import argparse
import os
import secrets
import string
import sys
from urllib.parse import urlsplit, urlunsplit

# sandbox's DATABASE_URL points at postgres-sandbox.railway.internal, which
# only resolves inside Railway's network — `railway run` injects the real
# env vars into this process, but doesn't put the local machine ON that
# network. A public TCP proxy already exists for postgres-sandbox (created
# 2026-09-09, not something this script sets up), so when the internal host
# can't resolve, rewrite just the host:port to that proxy's public endpoint
# and keep everything else (scheme, user, password, dbname, query) from the
# real DATABASE_URL untouched — the credential itself never has to be read
# or typed by a human, only passed through.
_SANDBOX_INTERNAL_HOST = "postgres-sandbox.railway.internal"
_SANDBOX_PROXY_HOSTPORT = "trolley.proxy.rlwy.net:26897"


def _rewrite_for_local_run(url: str) -> str:
    parts = urlsplit(url)
    if parts.hostname != _SANDBOX_INTERNAL_HOST.split(":")[0]:
        return url
    userinfo = parts.netloc.split("@", 1)[0] if "@" in parts.netloc else ""
    new_netloc = f"{userinfo}@{_SANDBOX_PROXY_HOSTPORT}" if userinfo else _SANDBOX_PROXY_HOSTPORT
    return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))


_url = os.environ.get("DATABASE_URL", "")
if _url:
    os.environ["DATABASE_URL"] = _rewrite_for_local_run(_url)

import auth_db
import db

_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits + "!@#$%^&*()-_=+"


def _generate_password(length: int = 20) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="admin", help="Account to reset (default: admin)")
    args = parser.parse_args()

    if not db.init_db():
        print("ERROR: could not connect using this environment's DATABASE_URL. Either it wasn't "
              "injected (run via `railway run --service <svc> --environment <env> python "
              "reset_admin_password.py`, not locally with no flags), or the DB is only reachable "
              "from inside Railway's network (an internal *.railway.internal host won't resolve "
              "from your machine even under `railway run` — check the Railway dashboard for a "
              "public proxy host/port for this database if so).",
              file=sys.stderr)
        return 1

    try:
        from passlib.context import CryptContext
    except ImportError:
        print("ERROR: passlib not installed in this environment.", file=sys.stderr)
        return 1

    user = auth_db.get_user_by_username(args.username)
    if not user:
        print(f"ERROR: no user '{args.username}' found.", file=sys.stderr)
        return 1

    password = _generate_password()
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    pw_hash = pwd_ctx.hash(password)

    if not auth_db.update_password(user["id"], pw_hash):
        print("ERROR: failed to write new password hash.", file=sys.stderr)
        return 1
    auth_db.set_must_change_pw(user["id"], True)
    auth_db.revoke_all_user_sessions(user["id"])

    print(f"Password reset for '{args.username}'. Forced to change at next login.")
    print(f"One-time password (record it now — it will not be shown again):\n\n    {password}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
