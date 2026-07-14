#!/usr/bin/env python3
"""
auth_db.py — Authentication schema and helpers (auth schema in PostgreSQL).

Tables
------
auth.users            local + SSO-provisioned accounts
auth.password_history last N bcrypt hashes per user (anti-reuse)
auth.sso_identities   per-provider subject-ID mapping (multi-provider per user)
auth.sessions         server-side JWT revocation table
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import db

logger = logging.getLogger("auth.db")

# ── Schema DDL ────────────────────────────────────────────────────────────────

_AUTH_DDL = """
CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.users (
    id              BIGSERIAL    PRIMARY KEY,
    username        VARCHAR(64)  NOT NULL UNIQUE,
    email           VARCHAR(255) UNIQUE,
    display_name    VARCHAR(128),
    role            VARCHAR(32)  NOT NULL DEFAULT 'user',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    must_change_pw  BOOLEAN      NOT NULL DEFAULT FALSE,
    password_hash   TEXT,
    sso_only        BOOLEAN      NOT NULL DEFAULT FALSE,
    manager_id      BIGINT       REFERENCES auth.users(id),
    preferences     JSONB,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS auth.password_history (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     BIGINT       NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    hash        TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pw_history_user
    ON auth.password_history (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS auth.sso_identities (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     BIGINT       NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    provider    VARCHAR(32)  NOT NULL,
    provider_id TEXT         NOT NULL,
    email       VARCHAR(255),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_id)
);
CREATE INDEX IF NOT EXISTS idx_sso_user ON auth.sso_identities (user_id);

CREATE TABLE IF NOT EXISTS auth.sessions (
    jti         TEXT         PRIMARY KEY,
    user_id     BIGINT       NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ  NOT NULL,
    revoked     BOOLEAN      NOT NULL DEFAULT FALSE,
    ip_address  TEXT,
    user_agent  TEXT
);
-- Retrofit for auth.users created before manager_id existed — CREATE TABLE IF
-- NOT EXISTS above never adds columns to an already-existing table.
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS manager_id BIGINT REFERENCES auth.users(id);
-- Per-user appearance settings (accent/density/colorScheme) — synced via
-- PUT /users/me/preferences so they follow the account across browsers and
-- machines, unlike the tweaks panel's prior in-memory-only state.
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS preferences JSONB;

CREATE INDEX IF NOT EXISTS idx_sessions_user
    ON auth.sessions (user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_cleanup
    ON auth.sessions (expires_at) WHERE NOT revoked;

-- Retrofit: screen_permissions started out keyed by role; replaced with a
-- per-user matrix (each account gets its own Read/Edit set, configured from
-- Configuration > User Configuration > Screen Access). Drop the old
-- role-keyed table so CREATE TABLE IF NOT EXISTS below can lay down the new
-- shape — there's no meaningful data to preserve across that schema change.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'auth' AND table_name = 'screen_permissions' AND column_name = 'role'
    ) THEN
        DROP TABLE auth.screen_permissions;
    END IF;
END $$;

-- Per-user screen access matrix. A missing (user_id, screen_id) row means
-- "allowed" — this is a blocklist, not an allowlist, so newly added screens
-- aren't silently hidden from existing accounts. Admins are never subject to
-- this table (enforced in the endpoint, not here) so an admin can never lock
-- themselves out of the app.
CREATE TABLE IF NOT EXISTS auth.screen_permissions (
    user_id     BIGINT      NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    screen_id   VARCHAR(64) NOT NULL,
    can_read    BOOLEAN     NOT NULL DEFAULT TRUE,
    can_edit    BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, screen_id)
);
CREATE INDEX IF NOT EXISTS idx_screen_permissions_user ON auth.screen_permissions (user_id);

-- RBAC: named roles, each carrying its own default screen permission set.
-- auth.users.role stores the role NAME (already a free VARCHAR(32), no
-- migration needed) — 'admin' and 'user' are seeded below as protected
-- system roles matching the two values every existing account already has.
-- This is purely additive to auth.screen_permissions above: a user's
-- EFFECTIVE permission on a screen is their own per-user row (if any),
-- else their role's default (if any), else allowed — see
-- get_effective_screen_permissions(). Nothing about the existing per-user
-- table's behavior changes; it simply becomes an override layer instead of
-- the only source.
CREATE TABLE IF NOT EXISTS auth.roles (
    id          BIGSERIAL   PRIMARY KEY,
    name        VARCHAR(32) NOT NULL UNIQUE,
    description VARCHAR(255),
    is_system   BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS auth.role_screen_permissions (
    role_id     BIGINT      NOT NULL REFERENCES auth.roles(id) ON DELETE CASCADE,
    screen_id   VARCHAR(64) NOT NULL,
    can_read    BOOLEAN     NOT NULL DEFAULT TRUE,
    can_edit    BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (role_id, screen_id)
);
CREATE INDEX IF NOT EXISTS idx_role_screen_permissions_role ON auth.role_screen_permissions (role_id);
"""

PW_HISTORY_LIMIT  = 3
SESSION_TTL_HOURS = int(os.environ.get("AUTH_SESSION_TTL_HOURS", "24"))


def init_auth_db() -> bool:
    """Create auth schema and tables (idempotent). Returns True on success."""
    if not db.is_available():
        return False
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_AUTH_DDL)
                # Seed the two system roles matching the values every existing
                # auth.users.role already has — no data migration needed.
                # ON CONFLICT DO NOTHING keeps this idempotent across restarts
                # and safe if an admin has since edited the description.
                cur.execute(
                    """
                    INSERT INTO auth.roles (name, description, is_system) VALUES
                        ('admin', 'Full access — bypasses the screen permission matrix entirely.', TRUE),
                        ('user',  'Default role for new accounts.', TRUE)
                    ON CONFLICT (name) DO NOTHING
                    """
                )
            conn.commit()
        logger.info("Auth schema initialised")
        return True
    except Exception as exc:
        logger.error("auth_db init failed: %s", exc)
        return False


# ── User CRUD ─────────────────────────────────────────────────────────────────

def _row_to_dict(cur, row) -> dict:
    cols = [d[0] for d in cur.description]
    d = dict(zip(cols, row))
    for tf in ("created_at", "last_login_at"):
        if d.get(tf) and hasattr(d[tf], "isoformat"):
            d[tf] = d[tf].isoformat()
    return d


def get_user_by_id(user_id: int) -> Optional[dict]:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username, email, display_name, role, is_active, "
                    "must_change_pw, sso_only, manager_id, preferences, last_login_at "
                    "FROM auth.users WHERE id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                return _row_to_dict(cur, row) if row else None
    except Exception as exc:
        logger.warning("get_user_by_id error: %s", exc)
        return None


def list_active_users() -> list:
    """All active accounts (id, username, display_name, role, manager_id) —
    used to populate the manager picker and to resolve/display names for the
    approval workflow without a cross-request round trip per user."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username, display_name, role, manager_id "
                    "FROM auth.users WHERE is_active = TRUE ORDER BY username"
                )
                return [_row_to_dict(cur, row) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("list_active_users error: %s", exc)
        return []


def list_all_users() -> list:
    """Every account regardless of active status — for the admin config screen
    (org chart + role management), unlike list_active_users() which powers the
    manager picker and should only ever offer active accounts."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username, email, display_name, role, is_active, manager_id "
                    "FROM auth.users ORDER BY username"
                )
                return [_row_to_dict(cur, row) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("list_all_users error: %s", exc)
        return []


def set_role(user_id: int, role: str) -> bool:
    if not role_exists(role):
        return False
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE auth.users SET role = %s WHERE id = %s", (role, user_id))
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("set_role error: %s", exc)
        return False


# ── Roles (RBAC) ─────────────────────────────────────────────────────────────
# Named roles, each carrying a default screen permission set — see the DDL
# comment on auth.roles/auth.role_screen_permissions above for the resolution
# order. 'admin'/'user' are seeded, protected system roles (init_auth_db).

def role_exists(name: str) -> bool:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM auth.roles WHERE name = %s", (name,))
                return cur.fetchone() is not None
    except Exception as exc:
        logger.warning("role_exists error: %s", exc)
        return False


def list_roles() -> list:
    """Every role, with a live count of assigned users."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.id, r.name, r.description, r.is_system, r.created_at,
                           (SELECT COUNT(*) FROM auth.users u WHERE u.role = r.name) AS user_count
                    FROM auth.roles r
                    ORDER BY r.is_system DESC, r.name ASC
                    """
                )
                cols = ["id", "name", "description", "is_system", "created_at", "user_count"]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d["created_at"]:
                        d["created_at"] = d["created_at"].isoformat()
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.warning("list_roles error: %s", exc)
        return []


def create_role(name: str, description: Optional[str] = None) -> Optional[int]:
    name = name.strip().lower()
    if not re.match(r"^[a-z0-9_-]{2,32}$", name):
        return None
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO auth.roles (name, description) VALUES (%s, %s) "
                    "ON CONFLICT (name) DO NOTHING RETURNING id",
                    (name, description),
                )
                row = cur.fetchone()
            conn.commit()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("create_role error: %s", exc)
        return None


def update_role(role_id: int, description: Optional[str]) -> bool:
    """Description only — name and is_system are immutable after creation so
    auth.users.role string references never silently point at a renamed role."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth.roles SET description = %s, updated_at = NOW() WHERE id = %s",
                    (description, role_id),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("update_role error: %s", exc)
        return False


def delete_role(role_id: int) -> Optional[str]:
    """Delete a role. Returns None on success, or an error message explaining
    why it was refused (system role / still has assigned users) — the caller
    surfaces this as a 409, since silently deleting either would leave users
    with a dangling role string or destroy the built-in admin/user roles."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name, is_system FROM auth.roles WHERE id = %s", (role_id,))
                row = cur.fetchone()
                if not row:
                    return "Role not found"
                name, is_system = row
                if is_system:
                    return f"'{name}' is a system role and cannot be deleted"
                cur.execute("SELECT COUNT(*) FROM auth.users WHERE role = %s", (name,))
                (n,) = cur.fetchone()
                if n > 0:
                    return f"{n} user(s) still have this role — reassign them first"
                cur.execute("DELETE FROM auth.roles WHERE id = %s", (role_id,))
            conn.commit()
        return None
    except Exception as exc:
        logger.warning("delete_role error: %s", exc)
        return "Delete failed — see server logs"


def get_role_screen_permissions(role_id: int) -> dict:
    """{screen_id: {"can_read": bool, "can_edit": bool}} for a role's defaults."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT screen_id, can_read, can_edit FROM auth.role_screen_permissions WHERE role_id = %s",
                    (role_id,),
                )
                return {r[0]: {"can_read": r[1], "can_edit": r[2]} for r in cur.fetchall()}
    except Exception as exc:
        logger.warning("get_role_screen_permissions error: %s", exc)
        return {}


def set_role_screen_permissions(role_id: int, perms: list) -> bool:
    """Replace the full default permission set for a role. Same shape as
    set_screen_permissions: list of {"screen_id", "can_read", "can_edit"}."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth.role_screen_permissions WHERE role_id = %s", (role_id,))
                for p in perms:
                    cur.execute(
                        """
                        INSERT INTO auth.role_screen_permissions (role_id, screen_id, can_read, can_edit)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (role_id, p["screen_id"], bool(p.get("can_read", True)), bool(p.get("can_edit", True))),
                    )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("set_role_screen_permissions error: %s", exc)
        return False


def get_effective_screen_permissions(user_id: int, role_name: str) -> dict:
    """
    Resolution order: per-user override (auth.screen_permissions) beats the
    user's role default (auth.role_screen_permissions) beats "allowed" (screen
    absent from both — unchanged default from before roles existed).
    """
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM auth.roles WHERE name = %s", (role_name,))
                row = cur.fetchone()
                role_id = row[0] if row else None
    except Exception as exc:
        logger.warning("get_effective_screen_permissions role lookup error: %s", exc)
        role_id = None

    role_perms = get_role_screen_permissions(role_id) if role_id else {}
    user_perms = get_screen_permissions(user_id)
    return {**role_perms, **user_perms}


def set_active(user_id: int, is_active: bool) -> bool:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE auth.users SET is_active = %s WHERE id = %s", (is_active, user_id))
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("set_active error: %s", exc)
        return False


def update_profile(user_id: int, email: Optional[str], display_name: Optional[str]) -> bool:
    """Full replace of the admin-editable profile fields (not password/role/manager)."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth.users SET email = %s, display_name = %s WHERE id = %s",
                    (email, display_name, user_id),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("update_profile error: %s", exc)
        return False


def set_must_change_pw(user_id: int, must_change: bool) -> None:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth.users SET must_change_pw = %s WHERE id = %s",
                    (must_change, user_id),
                )
            conn.commit()
    except Exception:
        pass


def add_password_history(user_id: int, pw_hash: str) -> None:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO auth.password_history (user_id, hash) VALUES (%s, %s)",
                    (user_id, pw_hash),
                )
            conn.commit()
    except Exception:
        pass


def delete_user(user_id: int) -> bool:
    """Hard-delete a user. Direct reports have manager_id cleared first — the
    self-referencing FK has no ON DELETE behavior and would otherwise block
    the delete. Sessions/password history/SSO identities cascade. Historical
    approval_tasks rows keep the numeric id in their soft (non-FK) prepared_by/
    manager_id/reviewed_by columns, so the audit trail survives the delete."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE auth.users SET manager_id = NULL WHERE manager_id = %s", (user_id,))
                cur.execute("DELETE FROM auth.users WHERE id = %s", (user_id,))
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("delete_user error: %s", exc)
        return False


def set_manager(user_id: int, manager_id: Optional[int]) -> bool:
    """Self-service (or admin) assignment of a user's manager. manager_id=None clears it."""
    if manager_id == user_id:
        return False
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth.users SET manager_id = %s WHERE id = %s",
                    (manager_id, user_id),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("set_manager error: %s", exc)
        return False


def set_preferences(user_id: int, prefs: dict) -> bool:
    """Merge (not replace) appearance preferences (accent/density/colorScheme)
    into the user's stored JSONB blob, so a partial update — e.g. just the
    accent changing — doesn't clobber previously-saved keys."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth.users SET preferences = COALESCE(preferences, '{}'::jsonb) || %s::jsonb WHERE id = %s",
                    (db.Json(prefs), user_id),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("set_preferences error: %s", exc)
        return False


def get_manager_of(user_id: int) -> Optional[dict]:
    """The resolved manager (id, username, display_name) for a user, or None if unset."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT m.id, m.username, m.display_name
                    FROM auth.users u JOIN auth.users m ON m.id = u.manager_id
                    WHERE u.id = %s AND m.is_active = TRUE
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                return _row_to_dict(cur, row) if row else None
    except Exception as exc:
        logger.warning("get_manager_of error: %s", exc)
        return None


def get_user_by_username(username: str) -> Optional[dict]:
    """Returns full row including password_hash for local auth."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username, email, display_name, role, is_active, "
                    "must_change_pw, sso_only, password_hash, preferences "
                    "FROM auth.users WHERE username = %s",
                    (username.strip().lower(),),
                )
                row = cur.fetchone()
                return _row_to_dict(cur, row) if row else None
    except Exception as exc:
        logger.warning("get_user_by_username error: %s", exc)
        return None


def get_user_by_email(email: str) -> Optional[dict]:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username, email, display_name, role, is_active, "
                    "must_change_pw, sso_only "
                    "FROM auth.users WHERE email = %s",
                    (email.strip().lower(),),
                )
                row = cur.fetchone()
                return _row_to_dict(cur, row) if row else None
    except Exception as exc:
        logger.warning("get_user_by_email error: %s", exc)
        return None


def get_user_by_sso(provider: str, provider_id: str) -> Optional[dict]:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.id, u.username, u.email, u.display_name, u.role,
                           u.is_active, u.must_change_pw, u.sso_only
                    FROM auth.users u
                    JOIN auth.sso_identities s ON s.user_id = u.id
                    WHERE s.provider = %s AND s.provider_id = %s
                    """,
                    (provider, provider_id),
                )
                row = cur.fetchone()
                return _row_to_dict(cur, row) if row else None
    except Exception as exc:
        logger.warning("get_user_by_sso error: %s", exc)
        return None


def create_user(
    *,
    username: str,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
    role: str = "user",
    password_hash: Optional[str] = None,
    sso_only: bool = False,
    must_change_pw: bool = False,
) -> Optional[int]:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auth.users
                        (username, email, display_name, role, password_hash, sso_only, must_change_pw)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (username.strip().lower(), email, display_name,
                     role, password_hash, sso_only, must_change_pw),
                )
                row = cur.fetchone()
            conn.commit()
            return row[0] if row else None
    except Exception as exc:
        logger.warning("create_user error: %s", exc)
        return None


def link_sso_identity(user_id: int, provider: str, provider_id: str, email: Optional[str] = None) -> bool:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auth.sso_identities (user_id, provider, provider_id, email)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (provider, provider_id) DO NOTHING
                    """,
                    (user_id, provider, provider_id, email),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("link_sso_identity error: %s", exc)
        return False


def update_last_login(user_id: int) -> None:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth.users SET last_login_at = NOW() WHERE id = %s",
                    (user_id,),
                )
            conn.commit()
    except Exception:
        pass


def update_password(user_id: int, new_hash: str) -> bool:
    """Set new password hash, clear must_change_pw, push to history, prune old entries."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth.users SET password_hash = %s, must_change_pw = FALSE WHERE id = %s",
                    (new_hash, user_id),
                )
                cur.execute(
                    "INSERT INTO auth.password_history (user_id, hash) VALUES (%s, %s)",
                    (user_id, new_hash),
                )
                cur.execute(
                    """
                    DELETE FROM auth.password_history
                    WHERE user_id = %s
                      AND id NOT IN (
                          SELECT id FROM auth.password_history
                          WHERE user_id = %s
                          ORDER BY created_at DESC
                          LIMIT %s
                      )
                    """,
                    (user_id, user_id, PW_HISTORY_LIMIT),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("update_password error: %s", exc)
        return False


def get_password_history(user_id: int) -> list[str]:
    """Return last PW_HISTORY_LIMIT bcrypt hashes for the user."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT hash FROM auth.password_history WHERE user_id = %s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (user_id, PW_HISTORY_LIMIT),
                )
                return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(
    user_id: int,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> str:
    """Insert a session row and return its jti (UUID string)."""
    jti = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.sessions (jti, user_id, expires_at, ip_address, user_agent) "
                "VALUES (%s, %s, %s, %s, %s)",
                (jti, user_id, expires_at, ip_address, (user_agent or "")[:512]),
            )
        conn.commit()
    return jti


def validate_session(jti: str) -> Optional[int]:
    """Return user_id if the session is active, else None."""
    if not jti:
        return None
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id FROM auth.sessions "
                    "WHERE jti = %s AND NOT revoked AND expires_at > NOW()",
                    (jti,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception:
        return None


def revoke_session(jti: str) -> None:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth.sessions SET revoked = TRUE WHERE jti = %s", (jti,)
                )
            conn.commit()
    except Exception:
        pass


def revoke_all_user_sessions(user_id: int) -> None:
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth.sessions SET revoked = TRUE WHERE user_id = %s", (user_id,)
                )
            conn.commit()
    except Exception:
        pass


# ── Screen access permissions (per user) ────────────────────────────────────────

def get_screen_permissions(user_id: int) -> dict:
    """{screen_id: {"can_read": bool, "can_edit": bool}} for the given user.
    Screens with no row are intentionally absent — callers should treat a
    missing screen_id as allowed (see table comment in _AUTH_DDL)."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT screen_id, can_read, can_edit FROM auth.screen_permissions WHERE user_id = %s",
                    (user_id,),
                )
                return {r[0]: {"can_read": r[1], "can_edit": r[2]} for r in cur.fetchall()}
    except Exception as exc:
        logger.warning("get_screen_permissions error: %s", exc)
        return {}


def set_screen_permissions(user_id: int, perms: list) -> bool:
    """Replace the full permission set for a user. perms: list of
    {"screen_id": str, "can_read": bool, "can_edit": bool}."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth.screen_permissions WHERE user_id = %s", (user_id,))
                for p in perms:
                    cur.execute(
                        """
                        INSERT INTO auth.screen_permissions (user_id, screen_id, can_read, can_edit)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (user_id, p["screen_id"], bool(p.get("can_read", True)), bool(p.get("can_edit", True))),
                    )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("set_screen_permissions error: %s", exc)
        return False


# ── Seed default users ────────────────────────────────────────────────────────

_SEED_USERS = [
    {
        "username":      "admin",
        "email":         "admin@dendrai.local",
        "display_name":  "Administrator",
        "role":          "admin",
        "password":      "Admin@Dendrai1!",
        "must_change_pw": True,
    },
    {
        "username":      "dendrai",
        "email":         "dendrai@dendrai.local",
        "display_name":  "Dendrai User",
        "role":          "user",
        "password":      "Dendrai@Pass1!",
        "must_change_pw": True,
    },
]


def seed_default_users() -> int:
    """Idempotently insert the two default local accounts. Returns count created."""
    try:
        from passlib.context import CryptContext
        pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    except ImportError:
        logger.warning("passlib not installed — skipping default user seed")
        return 0

    created = 0
    for u in _SEED_USERS:
        if get_user_by_username(u["username"]):
            continue
        pw_hash = pwd_ctx.hash(u["password"])
        uid = create_user(
            username=u["username"],
            email=u["email"],
            display_name=u["display_name"],
            role=u["role"],
            password_hash=pw_hash,
            must_change_pw=u["must_change_pw"],
        )
        if uid:
            try:
                with db._conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO auth.password_history (user_id, hash) VALUES (%s, %s)",
                            (uid, pw_hash),
                        )
                    conn.commit()
            except Exception:
                pass
            created += 1
            logger.info("Seeded user: %s (role=%s, must_change_pw=%s)",
                        u["username"], u["role"], u["must_change_pw"])
    return created
