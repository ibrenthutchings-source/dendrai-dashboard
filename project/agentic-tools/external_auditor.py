"""
External-auditor access policy (pure module: no DB, no HTTP).

An external auditor is an account whose role is EXTERNAL_ROLE. It exists to let a
third-party auditor look at what they have been given for specific engagements,
and nothing else. The rest of the platform authorises with a blocklist
(a role with no permission rows is allowed everywhere), which is the wrong default
for someone outside the company. So this role is handled the other way round:

  DENY BY DEFAULT. An external auditor may call only the paths in `path_allowed`:
  their own session endpoints and the /external/ portal API. Everything else is
  refused no matter which dependency the route uses, because the check runs in two
  independent places:
    * the global auth middleware (api_server._DendraiAuthMiddleware), using the role
      in the session token, before any route runs; and
    * get_current_user (auth_endpoints), using the role read from the database now,
      so a stale token cannot out-live a role change.
  Both call path_allowed(), so there is exactly one rule to review.

Within /external/ every call is additionally scoped to the engagements an admin has
assigned to that user (external_endpoints.py); this module only decides whether a
path is reachable at all.
"""
from __future__ import annotations

EXTERNAL_ROLE = "external_auditor"

# Exact paths (session housekeeping the portal needs).
_ALLOWED_EXACT = frozenset({
    "/auth/me",
    "/auth/logout",
    "/auth/change-password",
    "/auth/touch",          # idle keep-alive the front end sends while the user is active
})
# Prefixes: the portal API only. Admin-only sub-paths (/external/admin/...) are
# reachable by the router but refused there by require_admin.
_ALLOWED_PREFIXES = ("/external/",)


def is_external(role: object) -> bool:
    return role == EXTERNAL_ROLE


def path_allowed(path: str) -> bool:
    """Whether an external auditor may reach `path` at all."""
    if not isinstance(path, str) or not path.startswith("/"):
        return False
    # Refuse anything that is not a plain, normalised path: traversal and doubled
    # slashes are never legitimate here and are a classic way to dodge prefix checks.
    if ".." in path or "//" in path or "\\" in path or "%" in path or "\x00" in path:
        return False
    if path in _ALLOWED_EXACT:
        return True
    return any(path.startswith(p) for p in _ALLOWED_PREFIXES)
