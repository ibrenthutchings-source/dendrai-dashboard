"""
audit_events.py — catalog of what observability.audit_log records.

The audit log is for events that are NOT captured in a domain table and that
help trace problems or security incidents: who signed in (or failed to), who
changed access, which permission checks refused a request, what privileged
background jobs did. Domain data (approvals, findings, risk decisions, ...)
has its own tables and is deliberately not duplicated here.

Every row carries an `outcome`:
    success  the action completed
    failure  it was attempted and did not complete (bad password, validation
             error, blocked by a safety rule, internal error) — detail.reason
             holds a short machine-readable code
    denied   a permission / session check refused it

To audit a new event:
  1. Call db.insert_audit_log_entry(category, action, actor=..., target=...,
     detail=..., outcome=...) at the call site (auth_endpoints._audit /
     _reject wrap this for the auth router; mcp_guards.audit_log for tools).
     Never put passwords, tokens or other secrets in `detail`.
  2. Add an entry below so it shows up in GET /auth/admin/audit-log/events and
     in the docs. test_audit_events.py fails if an action used in
     auth_endpoints.py is missing from this catalog.
"""
from __future__ import annotations

_S, _F, _D = "success", "failure", "denied"


def _e(category: str, action: str, outcomes: list[str], description: str) -> dict:
    return {"category": category, "action": action, "outcomes": outcomes, "description": description}


AUDIT_EVENT_CATALOG: list[dict] = [
    # ── Sign-in and sessions ────────────────────────────────────────────────
    _e("auth", "login", [_S], "Successful sign-in (detail.provider: local, or the SSO provider)."),
    _e("auth", "login_failed", [_F],
       "Failed sign-in. detail.reason: invalid_credentials, account_inactive, or for SSO: "
       "provider_error, invalid_state, state_expired, unknown_provider, sso_failed, no_subject, "
       "provisioning_failed. Kept as its own action name for continuity with pre-outcome rows."),
    _e("auth", "login_rate_limited", [_D],
       "Sign-in refused before any password check. detail.reason: rate_limited (client IP exceeded 5 attempts in "
       "15 minutes) or account_locked (too many failed attempts against that username from any IPs; "
       "detail.retry_after_s). Many account_locked rows for one target from different ips = distributed attack."),
    _e("auth", "logout", [_S], "User signed out; the session was revoked."),
    _e("auth", "session_expired", [_D], "Session ended by the idle timeout (detail.reason: idle_timeout)."),

    # ── Passwords ───────────────────────────────────────────────────────────
    _e("auth", "password_changed", [_S, _F],
       "User changed their own password. Failure reasons: no_local_password, wrong_current_password, "
       "password_policy, password_reuse, db_error. All of the user's sessions are revoked on success."),
    _e("auth", "password_reset_by_admin", [_S, _F],
       "Admin set or generated a password for a user (forces a change at next login, revokes sessions). "
       "Failure reasons: user_not_found, password_policy, db_error."),

    # ── Accounts ────────────────────────────────────────────────────────────
    _e("auth", "user_created", [_S, _F],
       "Account created by an admin, or just-in-time by SSO (detail.jit_provisioned). Failure reasons: "
       "invalid_username, unknown_role, username_exists, email_in_use, password_policy, create_failed."),
    _e("auth", "user_updated", [_S, _F], "Admin edited a user's email or display name (detail flags which changed)."),
    _e("auth", "user_activated", [_S, _F], "Admin re-enabled an account."),
    _e("auth", "user_deactivated", [_S, _F],
       "Admin disabled an account (its sessions are revoked). Blocked for your own account: self_deactivation_blocked."),
    _e("auth", "user_deleted", [_S, _F], "Admin permanently removed an account. Blocked for your own account."),
    _e("auth", "manager_changed", [_S, _F], "Admin changed a user's reporting line (detail.manager_id)."),

    # ── Roles and screen access ─────────────────────────────────────────────
    _e("auth", "role_changed", [_S, _F],
       "A user's role changed (detail.old_role / new_role). Failure reasons: unknown_role, "
       "self_demotion_blocked, user_not_found, db_error."),
    _e("auth", "role_created", [_S, _F], "New role defined."),
    _e("auth", "role_updated", [_S, _F], "A role's description changed."),
    _e("auth", "role_deleted", [_S, _F], "Role deleted (refused while users still hold it: delete_rejected)."),
    _e("auth", "role_permissions_changed", [_S, _F],
       "A role's default screen permissions were replaced (detail has old_permissions and permissions)."),
    _e("auth", "screen_permissions_changed", [_S, _F],
       "A user's per-screen overrides were replaced (detail has old_permissions and permissions)."),

    # ── Access refused ──────────────────────────────────────────────────────
    _e("auth", "admin_access_denied", [_D],
       "A non-admin called an admin-only endpoint (detail.method / path). A burst from one actor is a probing signal."),
    _e("auth", "screen_access_denied", [_D],
       "A user called an endpoint for a screen they lack read/edit permission on (target = screen id)."),

    # ── Tools and background jobs ───────────────────────────────────────────
    _e("mcp_tool", "<tool name>", [_S, _F],
       "One row per MCP tool call, named after the tool (success = call made; tools that log on completion "
       "record failure on error, e.g. github_write_tool.*.result). Detail holds a safe summary of the arguments."),
    _e("reporting", "report_query", [_S, _F, _D],
       "A governed report query was run (target = dataset). Success records row_count and the result's SHA-256 "
       "(the reperformance check); failure reasons: invalid_query, execution_error; denied = restricted dataset."),
    _e("reporting", "report_snapshot_created", [_S, _F, _D],
       "A signed, immutable copy of a report result was stored (target = snapshot id; success records the dataset, "
       "row_count and result SHA-256; failure reasons: invalid_query, execution_error, signing_key_missing, db_error)."),
    _e("reporting", "report_snapshot_verified", [_S, _F],
       "A snapshot's rows and signature were checked (target = snapshot id). Failure means the snapshot does not "
       "verify: the detail says whether the rows or the signature failed. Treat a failure as a security event."),
    _e("reporting", "report_definition_saved", [_S, _F, _D],
       "A new draft version of a report definition was saved (target = key@version; success records the definition "
       "SHA-256). Failure reason: invalid_definition (denied = it reads a restricted dataset)."),
    _e("reporting", "report_definition_approved", [_S, _F, _D],
       "An admin approved a report version (target = key@version). Denied: not an admin, or the approver is the "
       "author. Failure reasons: invalid_state, narrative_missing, state_changed."),
    _e("reporting", "report_definition_retired", [_S, _F, _D], "A report version was retired and can no longer be run."),
    _e("reporting", "report_run", [_S, _F],
       "An approved report was run and a signed record stored (target = run id; success records report, version "
       "and result SHA-256). Failure reasons: no_approved_version, invalid_parameters, execution_error, db_error."),
    _e("reporting", "report_run_verified", [_S, _F],
       "A stored run's signature, row hashes and embedded definition were checked (target = run id). Failure means "
       "the record does not verify; treat as a security event."),
    _e("pbc", "pbc_requirement_saved", [_S, _F], "A control's evidence requirement was declared or changed (target = control/key)."),
    _e("pbc", "pbc_requests_generated", [_S, _F],
       "An engagement's PBC request list was generated from requirements (target = engagement; detail has created/existing counts)."),
    _e("pbc", "pbc_request_received", [_S, _F],
       "Evidence was linked to a request and the automatic checks ran (detail.findings lists the check codes that fired). "
       "Failure reasons: not_found, evidence_not_found, invalid_state, state_changed."),
    _e("pbc", "pbc_request_reviewed", [_S, _F, _D],
       "A request was accepted or rejected. denied = the reviewer submitted the evidence themselves (reviewer_not_independent)."),
    _e("retention", "exception_events_purged", [_S],
       "The PII retention sweep deleted expired exception events (detail.rows_deleted / retention_days)."),
]


def known_auth_actions() -> set[str]:
    return {e["action"] for e in AUDIT_EVENT_CATALOG if e["category"] == "auth"}
