#!/usr/bin/env python3
"""
Per-account login throttling (auth_endpoints._acct_*): stops a distributed
attack where many IPs each stay under the per-IP limit but all target one
account.

    pytest test_login_throttle.py -v
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import auth_endpoints as ae


def _request(ip: str) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/auth/login", "headers": [],
                    "client": (ip, 1234), "query_string": b"", "scheme": "http", "server": ("t", 80)})


class _Pwd:
    """Accepts only the password 'right'. Counts verifications."""
    verifies = 0

    def verify(self, pw, h):
        _Pwd.verifies += 1
        return pw == "right"

    def dummy_verify(self):
        pass


@pytest.fixture(autouse=True)
def env(monkeypatch):
    ae._acct_fail.clear()
    ae._rl_store.clear()
    _Pwd.verifies = 0
    monkeypatch.setattr(ae, "_HAS_PASSLIB", True)
    monkeypatch.setattr(ae, "_pwd_ctx", _Pwd())
    monkeypatch.setattr(ae.db, "insert_audit_log_entry", lambda *a, **k: 1)
    monkeypatch.setattr(ae.auth_db, "get_user_by_username",
                        lambda u: {"id": 1, "username": u, "password_hash": "h", "is_active": True,
                                   "role": "user", "must_change_pw": False} if u in ("alice", "bob") else None)
    monkeypatch.setattr(ae, "_finish_login", lambda user, request, response: user)
    monkeypatch.setattr(ae, "_LOCK_THRESHOLD", 5)
    yield
    ae._acct_fail.clear()


def _login(user, pw, ip):
    return asyncio.run(ae.local_login(ae.LoginRequest(username=user, password=pw), _request(ip), response=None))


def _status(user, pw, ip) -> int:
    try:
        _login(user, pw, ip)
        return 200
    except HTTPException as e:
        return e.status_code


def test_distributed_attack_on_one_account_is_stopped():
    # 5 wrong guesses, each from a DIFFERENT IP — every IP is far under its own limit.
    assert [_status("alice", "bad", f"203.0.113.{i}") for i in range(1, 6)] == [401] * 5
    # A 6th IP is refused outright, and even the correct password is not honoured while locked.
    verifies_before = _Pwd.verifies
    assert _status("alice", "right", "198.51.100.77") == 429
    assert _Pwd.verifies == verifies_before, "locked account must not reach password verification"


def test_other_accounts_are_unaffected():
    for i in range(5):
        _status("alice", "bad", f"203.0.113.{i + 1}")
    assert _status("bob", "right", "198.51.100.1") == 200


def test_unknown_usernames_lock_identically_so_accounts_cannot_be_enumerated():
    for i in range(5):
        assert _status("nobody", "bad", f"203.0.113.{i + 1}") == 401
    assert _status("nobody", "bad", "198.51.100.1") == 429


def test_backoff_doubles_and_is_capped(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ae.time, "monotonic", lambda: now[0])
    for i in range(5):
        _status("alice", "bad", f"203.0.113.{i + 1}")
    assert 59 <= ae._acct_retry_after("alice") <= 61         # 60s after the 5th failure
    now[0] += 61                                              # lock expires
    assert ae._acct_retry_after("alice") == 0
    assert _status("alice", "bad", "203.0.113.50") == 401     # 6th failure
    assert 119 <= ae._acct_retry_after("alice") <= 121        # doubled to 120s
    for _ in range(10):                                       # keep failing past each lock
        now[0] += ae._acct_retry_after("alice") + 1
        _status("alice", "bad", "203.0.113.51")
    assert ae._acct_retry_after("alice") <= ae._LOCK_MAX_S + 1


def test_lock_clears_after_the_window_with_no_failures(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ae.time, "monotonic", lambda: now[0])
    for i in range(5):
        _status("alice", "bad", f"203.0.113.{i + 1}")
    now[0] += ae._RL_WINDOW_S + 1
    assert _status("alice", "right", "198.51.100.1") == 200


def test_success_resets_the_failure_count():
    for i in range(4):                      # one short of the threshold
        _status("alice", "bad", f"203.0.113.{i + 1}")
    assert _status("alice", "right", "198.51.100.1") == 200
    for i in range(4):                      # counter restarted, so still not locked
        assert _status("alice", "bad", f"203.0.113.{i + 20}") == 401


def test_admin_reset_clears_the_lock():
    for i in range(5):
        _status("alice", "bad", f"203.0.113.{i + 1}")
    assert ae._acct_retry_after("alice") > 0
    ae._acct_clear("alice")
    assert ae._acct_retry_after("alice") == 0


def test_lock_is_scoped_per_tenant():
    ae._tenant_id_ctx.set("tenant-a")
    try:
        for i in range(5):
            _status("alice", "bad", f"203.0.113.{i + 1}")
        assert ae._acct_retry_after("alice") > 0
        ae._tenant_id_ctx.set("tenant-b")
        assert ae._acct_retry_after("alice") == 0
    finally:
        ae._tenant_id_ctx.set(None)


def test_username_case_and_whitespace_do_not_dodge_the_lock():
    for i, name in enumerate(["alice", "ALICE", " Alice ", "alice", "aLiCe"]):
        _status(name, "bad", f"203.0.113.{i + 1}")
    assert _status("alice", "right", "198.51.100.1") == 429


def test_failure_store_is_bounded(monkeypatch):
    monkeypatch.setattr(ae, "_LOCK_MAX_KEYS", 50)
    for i in range(200):
        ae._acct_record_failure(f"user{i}")
    assert len(ae._acct_fail) <= 50
