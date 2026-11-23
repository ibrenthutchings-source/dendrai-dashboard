#!/usr/bin/env python3
"""
Tests for audit-log outcomes (success / failure / denied) and the event catalog.

No database needed: db._conn/_run are faked to capture the INSERT, and
auth_db / auth_endpoints collaborators are monkeypatched.

    pytest test_audit_outcomes.py -v
"""
from __future__ import annotations

import ast
import asyncio
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import audit_events
import auth_endpoints
import db


# ── capture what insert_audit_log_entry writes ──────────────────────────────

class _FakeCursor:
    def __init__(self, sink):
        self.sink = sink
        self._last = ""

    def execute(self, sql, params=None):
        self._last = sql
        if "INSERT INTO observability.audit_log" in sql:
            self.sink.append(params)

    def fetchone(self):
        # SELECT chain_hash ... -> no previous row; INSERT ... RETURNING id -> (1,)
        return None if "SELECT chain_hash" in self._last else (1,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, sink):
        self.sink = sink

    def cursor(self):
        return _FakeCursor(self.sink)


@pytest.fixture
def inserts(monkeypatch):
    sink: list = []

    @contextmanager
    def fake_conn():
        yield _FakeConn(sink)

    monkeypatch.setattr(db, "_conn", fake_conn)
    monkeypatch.setattr(db, "_run", lambda fn, **kw: fn())
    monkeypatch.setenv("AUDIT_SIGNING_KEY", "test-key")
    return sink


def _outcome_of(params) -> str:
    return params[-1]            # outcome is the last bound parameter


def _record_of(params) -> dict:
    return json.loads(params[6])  # record_json


def test_default_outcome_is_success(inserts):
    db.insert_audit_log_entry("auth", "login", actor="a")
    assert _outcome_of(inserts[0]) == "success"
    assert _record_of(inserts[0])["outcome"] == "success"


@pytest.mark.parametrize("outcome", ["success", "failure", "denied"])
def test_outcome_is_stored_and_signed(inserts, outcome):
    db.insert_audit_log_entry("auth", "x", actor="a", outcome=outcome)
    assert _outcome_of(inserts[0]) == outcome
    assert _record_of(inserts[0])["outcome"] == outcome


def test_outcome_changes_the_signature(inserts):
    db.insert_audit_log_entry("auth", "x", actor="a", outcome="success")
    db.insert_audit_log_entry("auth", "x", actor="a", outcome="failure")
    assert inserts[0][7] != inserts[1][7]   # signature


def test_unknown_outcome_is_recorded_as_failure(inserts):
    db.insert_audit_log_entry("auth", "x", actor="a", outcome="banana")
    assert _outcome_of(inserts[0]) == "failure"


# ── auth_endpoints: failures are audited ────────────────────────────────────

@pytest.fixture
def audited(monkeypatch):
    calls: list[dict] = []

    def fake_insert(category, action, actor=None, target=None, detail=None, ip_address=None, outcome="success"):
        calls.append({"category": category, "action": action, "actor": actor, "target": target,
                      "detail": detail, "outcome": outcome})
        return 1

    monkeypatch.setattr(auth_endpoints.db, "insert_audit_log_entry", fake_insert)
    return calls


def _request(path="/auth/x", method="POST") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": [],
                    "client": ("203.0.113.9", 1234), "query_string": b"", "scheme": "http",
                    "server": ("test", 80)})


def test_reject_audits_then_raises(audited):
    with pytest.raises(HTTPException) as ei:
        auth_endpoints._reject(422, "nope", "role_changed", "admin1", target="bob",
                               reason="unknown_role", extra={"requested_role": "x"}, request=_request())
    assert ei.value.status_code == 422
    assert audited[0]["outcome"] == "failure"
    assert audited[0]["detail"]["reason"] == "unknown_role"
    assert audited[0]["detail"]["http_status"] == 422
    assert audited[0]["detail"]["requested_role"] == "x"


def test_failed_login_is_audited_without_password(audited, monkeypatch):
    monkeypatch.setattr(auth_endpoints, "_HAS_PASSLIB", True)
    monkeypatch.setattr(auth_endpoints.auth_db, "get_user_by_username", lambda u: None)
    monkeypatch.setattr(auth_endpoints, "_rate_check", lambda ip: True)

    class _Ctx:
        def dummy_verify(self):
            pass

    monkeypatch.setattr(auth_endpoints, "_pwd_ctx", _Ctx())
    req = auth_endpoints.LoginRequest(username="mallory", password="hunter2-SECRET")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(auth_endpoints.local_login(req, _request(), response=None))
    assert ei.value.status_code == 401
    assert audited[0]["action"] == "login_failed" and audited[0]["outcome"] == "failure"
    assert audited[0]["actor"] == "mallory"
    assert "hunter2" not in json.dumps(audited)


def test_rate_limited_login_is_denied(audited, monkeypatch):
    monkeypatch.setattr(auth_endpoints, "_rate_check", lambda ip: False)
    req = auth_endpoints.LoginRequest(username="mallory", password="x")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(auth_endpoints.local_login(req, _request(), response=None))
    assert ei.value.status_code == 429
    assert audited[0]["action"] == "login_rate_limited" and audited[0]["outcome"] == "denied"


def test_non_admin_hitting_admin_endpoint_is_denied(audited):
    with pytest.raises(HTTPException) as ei:
        auth_endpoints.require_admin(_request("/auth/admin/users", "GET"), {"username": "eve", "role": "user"})
    assert ei.value.status_code == 403
    assert audited[0]["action"] == "admin_access_denied" and audited[0]["outcome"] == "denied"
    assert audited[0]["detail"]["path"] == "/auth/admin/users"


def test_admin_passes_without_an_audit_row(audited):
    user = {"username": "root", "role": "admin"}
    assert auth_endpoints.require_admin(_request(), user) is user
    assert audited == []


def test_screen_permission_denial_is_audited(audited, monkeypatch):
    monkeypatch.setattr(auth_endpoints.auth_db, "get_effective_screen_permissions",
                        lambda uid, role: {"rrreview": {"can_read": False, "can_edit": False}})
    check = auth_endpoints.require_screen_permission("rrreview")
    with pytest.raises(HTTPException) as ei:
        check(_request("/risk-register/x", "GET"), {"id": 7, "username": "eve", "role": "user"})
    assert ei.value.status_code == 403
    assert audited[0]["action"] == "screen_access_denied"
    assert audited[0]["target"] == "rrreview" and audited[0]["outcome"] == "denied"


def test_wrong_current_password_on_change_is_audited(audited, monkeypatch):
    monkeypatch.setattr(auth_endpoints, "_HAS_PASSLIB", True)
    monkeypatch.setattr(auth_endpoints.auth_db, "get_user_by_username",
                        lambda u: {"password_hash": "h"})

    class _Ctx:
        def verify(self, pw, h):
            return False

    monkeypatch.setattr(auth_endpoints, "_pwd_ctx", _Ctx())
    body = auth_endpoints.ChangePasswordRequest(current_password="old-SECRET", new_password="New-SECRET-1!")
    with pytest.raises(HTTPException) as ei:
        auth_endpoints.change_password(body, _request(), {"id": 1, "username": "alice"})
    assert ei.value.status_code == 401
    assert audited[0]["action"] == "password_changed" and audited[0]["outcome"] == "failure"
    assert audited[0]["detail"]["reason"] == "wrong_current_password"
    assert "SECRET" not in json.dumps(audited)


def test_self_demotion_blocked_is_audited(audited, monkeypatch):
    monkeypatch.setattr(auth_endpoints.auth_db, "role_exists", lambda r: True)
    with pytest.raises(HTTPException):
        auth_endpoints.admin_set_role(1, auth_endpoints.AdminSetRoleRequest(role="user"), _request(),
                                      {"id": 1, "username": "root", "role": "admin"})
    assert audited[0]["action"] == "role_changed"
    assert audited[0]["outcome"] == "failure"
    assert audited[0]["detail"]["reason"] == "self_demotion_blocked"


def test_successful_role_change_records_old_and_new(audited, monkeypatch):
    monkeypatch.setattr(auth_endpoints.auth_db, "role_exists", lambda r: True)
    monkeypatch.setattr(auth_endpoints.auth_db, "get_user_by_id", lambda i: {"username": "bob", "role": "user"})
    monkeypatch.setattr(auth_endpoints.auth_db, "set_role", lambda i, r: True)
    auth_endpoints.admin_set_role(2, auth_endpoints.AdminSetRoleRequest(role="auditor"), _request(),
                                  {"id": 1, "username": "root", "role": "admin"})
    assert audited[0]["outcome"] == "success"
    assert audited[0]["detail"] == {"old_role": "user", "new_role": "auditor"}


# ── catalog ─────────────────────────────────────────────────────────────────

def _actions_used_in_auth_endpoints() -> set[str]:
    tree = ast.parse(Path(auth_endpoints.__file__).read_text(encoding="utf8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        idx = {"_audit": 0, "_reject": 2}.get(node.func.id)
        if idx is not None and len(node.args) > idx and isinstance(node.args[idx], ast.Constant) \
                and isinstance(node.args[idx].value, str):
            found.add(node.args[idx].value)
    return found


def test_every_audited_auth_action_is_in_the_catalog():
    missing = _actions_used_in_auth_endpoints() - audit_events.known_auth_actions()
    assert not missing, f"add to audit_events.AUDIT_EVENT_CATALOG: {sorted(missing)}"


def test_catalog_entries_are_well_formed_and_unique():
    seen = set()
    for e in audit_events.AUDIT_EVENT_CATALOG:
        assert set(e) == {"category", "action", "outcomes", "description"}
        assert e["outcomes"] and set(e["outcomes"]) <= set(db.AUDIT_OUTCOMES)
        key = (e["category"], e["action"])
        assert key not in seen
        seen.add(key)
