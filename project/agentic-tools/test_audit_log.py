#!/usr/bin/env python3
"""
Unit tests for the platform audit trail (observability.audit_log —
db._audit_chain_hash / db.verify_audit_chain / db.insert_audit_log_entry),
and for exception_control_events.raw_payload at-rest encryption
(db._encrypt_raw_payload / db._decrypt_raw_payload).

Mirrors test_evidence_chain.py's structure exactly — same hash-chain
construction, same no-DB degrade-path precondition.

    pytest test_audit_log.py -v
"""

from __future__ import annotations

import os

import db


# ── _audit_chain_hash (pure) — same properties as _evidence_chain_hash ──────

def test_audit_chain_hash_is_deterministic():
    h1 = db._audit_chain_hash("prev123", "sig456")
    h2 = db._audit_chain_hash("prev123", "sig456")
    assert h1 == h2


def test_audit_chain_hash_changes_with_prev_hash():
    assert db._audit_chain_hash("prevA", "sig456") != db._audit_chain_hash("prevB", "sig456")


def test_audit_chain_hash_changes_with_signature():
    assert db._audit_chain_hash("prev123", "sigA") != db._audit_chain_hash("prev123", "sigB")


def test_audit_chain_hash_none_prev_treated_as_genesis():
    assert db._audit_chain_hash(None, "sig456") == db._audit_chain_hash(db.AUDIT_CHAIN_GENESIS_HASH, "sig456")


def test_audit_chain_hash_is_a_64_char_hex_digest():
    h = db._audit_chain_hash(None, "sig456")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_audit_chain_hash_detects_a_deleted_middle_record():
    """The property this chain exists to prove: deleting the admin_set_role
    row in the middle of a login -> role_changed -> user_deleted sequence
    must make the surviving next row's chain_hash irreconcilable with a
    chain re-derived from what's left."""
    sigs = ["login-sig", "role-changed-sig", "user-deleted-sig"]
    h1 = db._audit_chain_hash(None, sigs[0])
    h2 = db._audit_chain_hash(h1, sigs[1])
    h3_original = db._audit_chain_hash(h2, sigs[2])

    h3_if_recomputed_after_deleting_role_change = db._audit_chain_hash(h1, sigs[2])
    assert h3_if_recomputed_after_deleting_role_change != h3_original


def test_audit_chain_uses_a_distinct_lock_key_from_evidence_chain():
    """A shared advisory-lock key between the two chains would serialize
    unrelated insert paths against each other for no reason."""
    assert db._AUDIT_CHAIN_LOCK_KEY != db._EVIDENCE_CHAIN_LOCK_KEY


# ── verify_audit_chain / insert_audit_log_entry / list_audit_log (no-DB degrade) ──

def test_verify_audit_chain_does_not_raise_without_database():
    assert not db.is_available()  # documents the precondition this test relies on
    result = db.verify_audit_chain()
    assert isinstance(result, dict)
    assert "valid" in result and "checked" in result


def test_insert_audit_log_entry_returns_none_without_database():
    assert db.insert_audit_log_entry("auth", "login", actor="alice") is None


def test_list_audit_log_returns_empty_list_without_database():
    assert db.list_audit_log() == []


# ── _audit_signing_key (per-process fallback) ────────────────────────────────

def test_audit_signing_key_falls_back_to_a_cached_random_key_when_unset(monkeypatch):
    """A missing AUDIT_SIGNING_KEY must never raise — unlike
    CONNECTOR_ENCRYPTION_KEY, audit logging must not block the login/admin
    action it's recording. The fallback should also be stable within one
    process (cached), so a chain built during this process's uptime is
    still internally self-consistent."""
    monkeypatch.delenv("AUDIT_SIGNING_KEY", raising=False)
    db._audit_signing_key_cache = None
    k1 = db._audit_signing_key()
    k2 = db._audit_signing_key()
    assert k1 == k2
    assert len(k1) > 0
    db._audit_signing_key_cache = None  # don't leak state into other tests


def test_audit_signing_key_prefers_explicit_env_var(monkeypatch):
    monkeypatch.setenv("AUDIT_SIGNING_KEY", "explicit-test-key")
    assert db._audit_signing_key() == "explicit-test-key"


# ── exception_control_events.raw_payload encryption ──────────────────────────

def test_encrypt_raw_payload_returns_none_for_falsy_input():
    assert db._encrypt_raw_payload(None) is None
    assert db._encrypt_raw_payload({}) == {}


def test_encrypt_raw_payload_wraps_in_enc_sentinel_when_key_available(monkeypatch):
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", _fernet_key())
    payload = {"actor_email": "alice@example.com", "amount": 100}
    encrypted = db._encrypt_raw_payload(payload)
    assert list(encrypted.keys()) == [db._RAW_PAYLOAD_ENC_KEY]
    assert encrypted[db._RAW_PAYLOAD_ENC_KEY] != payload  # not stored verbatim


def test_encrypt_then_decrypt_raw_payload_round_trips(monkeypatch):
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", _fernet_key())
    payload = {"actor_email": "alice@example.com", "amount": 100, "nested": {"x": 1}}
    encrypted = db._encrypt_raw_payload(payload)
    assert db._decrypt_raw_payload(encrypted) == payload


def test_encrypt_raw_payload_falls_back_to_plaintext_without_a_key(monkeypatch):
    """A missing CONNECTOR_ENCRYPTION_KEY must not silently drop the payload
    from the audit trail — same discipline as
    mcp_governance._encrypt_sensitive_details."""
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    payload = {"actor_email": "alice@example.com"}
    assert db._encrypt_raw_payload(payload) == payload


def test_decrypt_raw_payload_is_a_no_op_for_unencrypted_input():
    payload = {"actor_email": "alice@example.com"}
    assert db._decrypt_raw_payload(payload) == payload
    assert db._decrypt_raw_payload(None) is None


def _fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()
