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
import secrets
import string
import sys

import auth_db
import db

_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits + "!@#$%^&*()-_=+"


def _generate_password(length: int = 20) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="admin", help="Account to reset (default: admin)")
    args = parser.parse_args()

    if not db.is_available():
        print("ERROR: database not reachable from this environment — run this via `railway run` "
              "inside the target service/environment so DATABASE_URL is injected, not locally.",
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
