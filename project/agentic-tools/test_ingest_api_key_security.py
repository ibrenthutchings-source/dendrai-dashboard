#!/usr/bin/env python3
"""
Integration tests for the ingest_api_key encryption-at-rest fix
(mcp_governance._create_system / _fetch_systems / _get_system_by_api_key /
_rotate_system_api_key, db.py's ingest_api_key_enc migration).

Security issue this closes: observability.monitored_systems.ingest_api_key
was a plaintext UUID column, looked up with a direct equality match. A
Postgres backup or DB compromise handed over every registered external
system's live, unexpiring bearer credential directly — no protection layer
the way local account passwords get (bcrypt). New systems now get a
high-entropy secret (secrets.token_urlsafe(32)) Fernet-encrypted with
CONNECTOR_ENCRYPTION_KEY before storage — the same reversible-encryption
pattern already used for poll-connector credentials, chosen over hashing
because the product's existing UX re-displays the key on every Monitored
Systems screen load (ubo-config.jsx's ApiKeyDisplay), which an irreversible
hash can't support.

Existing systems created before this fix keep working via a legacy fallback
(the plaintext ingest_api_key column) until explicitly rotated — this suite
proves both paths, and that _rotate_system_api_key is the migration path off
the legacy column for an existing system.

Only the DB I/O boundary is faked (same pattern as
test_mcp_governance_adjudication.py); the real Fernet encrypt/decrypt
(db.encrypt_sensitive_json/decrypt_sensitive_json) and the real
hmac.compare_digest comparison in _get_system_by_api_key both run for real.

    pytest test_ingest_api_key_security.py -v
"""

from __future__ import annotations

from cryptography.fernet import Fernet

import db
import mcp_governance as mg


# ── Fake DB boundary — a queue of (cols, rows) per cur.execute() call, so a
# ── function issuing multiple queries on one cursor (as _get_system_by_api_key
# ── does: encrypted scan, then legacy fallback) gets the right result each time.

class _FakeCursor:
    def __init__(self, results_queue, recorder):
        self._queue = results_queue
        self._recorder = recorder
        self._current = ([], [])

    def execute(self, sql, params=None):
        self._recorder.append((sql, params))
        self._current = self._queue.pop(0) if self._queue else ([], [])

    @property
    def description(self):
        return [(c,) for c in self._current[0]]

    def fetchall(self):
        return list(self._current[1])

    def fetchone(self):
        rows = self._current[1]
        return rows[0] if rows else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, results_queue, recorder):
        self._queue = results_queue
        self._recorder = recorder

    def cursor(self):
        return _FakeCursor(self._queue, self._recorder)

    def commit(self):
        pass


class _FakeConnCtx:
    def __init__(self, results_queue, recorder):
        self._queue = results_queue
        self._recorder = recorder

    def __enter__(self):
        return _FakeConn(self._queue, self._recorder)

    def __exit__(self, *a):
        return False


def _wire_db(monkeypatch, results_queue):
    """Returns the SQL/params recorder. results_queue is a list of
    (col_names, list_of_row_tuples) consumed in order, one per execute()."""
    recorder = []
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "get_conn", lambda: _FakeConnCtx(results_queue, recorder))
    return recorder


def _with_encryption_key(monkeypatch):
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())


_SYSTEM_COLS = ["id", "display_name", "server_name", "server_type", "active",
                "governance_tiers", "alert_webhook"]


# ── _create_system — issues an encrypted key, never the legacy plaintext one ─

def test_create_system_stores_encrypted_key_and_nulls_the_legacy_column(monkeypatch):
    _with_encryption_key(monkeypatch)
    recorder = _wire_db(monkeypatch, [([], [(42,)])])  # INSERT ... RETURNING id

    new_id = mg._create_system(
        "CodeQL CI", "codeql-ci", "sast", None, True,
        ["CRITICAL", "HIGH"], None, None, "operator", None, None, None,
    )

    assert new_id == 42
    sql, params = recorder[0]
    assert "ingest_api_key_enc" in sql
    # Column order in the INSERT: ... system_owner, ingest_api_key, ingest_api_key_enc
    ingest_api_key_param, ingest_api_key_enc_param = params[-2], params[-1]
    assert ingest_api_key_param is None  # legacy column explicitly nulled, not left to its old default
    assert ingest_api_key_enc_param is not None
    # The encrypted blob must not contain the plaintext key anywhere in it —
    # trivially true for Fernet, but worth asserting the shape is opaque.
    assert isinstance(ingest_api_key_enc_param, str)
    decrypted = db.decrypt_sensitive_json(ingest_api_key_enc_param)
    assert len(decrypted["key"]) >= 32  # secrets.token_urlsafe(32) -> 43 chars


def test_create_system_fails_closed_without_encryption_key(monkeypatch):
    """No CONNECTOR_ENCRYPTION_KEY set at all — must refuse to create a
    system with an unencryptable key rather than silently falling back to
    plaintext storage (the exact bug being fixed)."""
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    recorder = _wire_db(monkeypatch, [])

    new_id = mg._create_system(
        "CodeQL CI", "codeql-ci", "sast", None, True,
        ["CRITICAL"], None, None, "operator", None, None, None,
    )

    assert new_id is None
    assert recorder == []  # never even reached the INSERT


# ── _fetch_systems — decrypts for display, falls back to legacy plaintext ──

def test_fetch_systems_decrypts_the_encrypted_key_for_display(monkeypatch):
    _with_encryption_key(monkeypatch)
    enc = db.encrypt_sensitive_json({"key": "plaintext-secret-abc123"})
    cols = ["id", "display_name", "server_name", "server_type", "description", "active",
            "governance_tiers", "blocking_tools", "alert_webhook", "created_at", "updated_at",
            "created_by", "ingest_api_key", "ingest_api_key_enc", "risk_tier", "data_sensitivity",
            "system_owner", "total_calls", "flagged_calls", "last_seen"]
    row = (1, "CodeQL CI", "codeql-ci", "sast", None, True, ["CRITICAL"], None, None,
           None, None, "operator", None, enc, None, None, None, 10, 2, None)
    _wire_db(monkeypatch, [(cols, [row])])

    rows = mg._fetch_systems()

    assert len(rows) == 1
    assert rows[0]["ingest_api_key"] == "plaintext-secret-abc123"
    assert "ingest_api_key_enc" not in rows[0]  # internal column, never leaked to the API response


def test_fetch_systems_falls_back_to_legacy_plaintext_column(monkeypatch):
    _with_encryption_key(monkeypatch)
    import uuid as _uuid
    legacy_uuid = _uuid.uuid4()
    cols = ["id", "display_name", "server_name", "server_type", "description", "active",
            "governance_tiers", "blocking_tools", "alert_webhook", "created_at", "updated_at",
            "created_by", "ingest_api_key", "ingest_api_key_enc", "risk_tier", "data_sensitivity",
            "system_owner", "total_calls", "flagged_calls", "last_seen"]
    row = (2, "Legacy SAP Connector", "sap-prod", "sap", None, True, ["CRITICAL"], None, None,
           None, None, "operator", legacy_uuid, None, None, None, None, 5, 0, None)
    _wire_db(monkeypatch, [(cols, [row])])

    rows = mg._fetch_systems()

    assert rows[0]["ingest_api_key"] == str(legacy_uuid)


def test_fetch_systems_reports_undecryptable_key_without_crashing_the_list(monkeypatch):
    """A rotated/missing CONNECTOR_ENCRYPTION_KEY must degrade one row's key
    display, not take down the whole Monitored Systems screen."""
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    enc = db.encrypt_sensitive_json({"key": "secret"})
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())  # different key now

    cols = ["id", "display_name", "server_name", "server_type", "description", "active",
            "governance_tiers", "blocking_tools", "alert_webhook", "created_at", "updated_at",
            "created_by", "ingest_api_key", "ingest_api_key_enc", "risk_tier", "data_sensitivity",
            "system_owner", "total_calls", "flagged_calls", "last_seen"]
    row = (3, "Orphaned Key System", "x", "custom", None, True, ["CRITICAL"], None, None,
           None, None, "operator", None, enc, None, None, None, 0, 0, None)
    _wire_db(monkeypatch, [(cols, [row])])

    rows = mg._fetch_systems()

    assert len(rows) == 1
    assert rows[0]["ingest_api_key"] is None
    assert "CONNECTOR_ENCRYPTION_KEY" in rows[0]["ingest_api_key_error"]


# ── _get_system_by_api_key — the actual auth path ───────────────────────────

def test_get_system_by_api_key_matches_encrypted_row(monkeypatch):
    _with_encryption_key(monkeypatch)
    real_key = "abc123-real-secret-xyz"
    enc = db.encrypt_sensitive_json({"key": real_key})
    scan_cols = mg._SYSTEM_LOOKUP_COLS.split(", ") + ["ingest_api_key_enc"]
    scan_row = (7, "CodeQL CI", "codeql-ci", "sast", True, ["CRITICAL"], None, enc)
    _wire_db(monkeypatch, [(scan_cols, [scan_row])])

    result = mg._get_system_by_api_key(real_key)

    assert result is not None
    assert result["id"] == 7
    assert result["server_name"] == "codeql-ci"


def test_get_system_by_api_key_rejects_wrong_key_for_an_encrypted_row(monkeypatch):
    _with_encryption_key(monkeypatch)
    enc = db.encrypt_sensitive_json({"key": "the-real-key"})
    scan_cols = mg._SYSTEM_LOOKUP_COLS.split(", ") + ["ingest_api_key_enc"]
    scan_row = (7, "CodeQL CI", "codeql-ci", "sast", True, ["CRITICAL"], None, enc)
    # Two execute() calls happen: the encrypted scan (no match -> falls
    # through), then the legacy-fallback query (never matches, non-UUID key
    # never even reaches it).
    _wire_db(monkeypatch, [(scan_cols, [scan_row])])

    result = mg._get_system_by_api_key("wrong-key-entirely")

    assert result is None


def test_get_system_by_api_key_falls_back_to_legacy_plaintext_row(monkeypatch):
    _with_encryption_key(monkeypatch)
    import uuid as _uuid
    legacy_uuid = _uuid.uuid4()
    scan_cols = mg._SYSTEM_LOOKUP_COLS.split(", ") + ["ingest_api_key_enc"]
    legacy_cols = mg._SYSTEM_LOOKUP_COLS.split(", ")
    legacy_row = (9, "Legacy SAP Connector", "sap-prod", "sap", True, ["CRITICAL"], None)
    _wire_db(monkeypatch, [
        (scan_cols, []),               # no encrypted rows match (or exist)
        (legacy_cols, [legacy_row]),   # legacy UUID column match
    ])

    result = mg._get_system_by_api_key(str(legacy_uuid))

    assert result is not None
    assert result["id"] == 9
    assert result["server_name"] == "sap-prod"


def test_get_system_by_api_key_non_uuid_key_skips_legacy_query_entirely(monkeypatch):
    """A new-style secrets.token_urlsafe key isn't UUID-shaped — the legacy
    ::uuid cast must never even be attempted for it (it would raise a
    Postgres cast error on a real connection, not just return no rows)."""
    _with_encryption_key(monkeypatch)
    scan_cols = mg._SYSTEM_LOOKUP_COLS.split(", ") + ["ingest_api_key_enc"]
    recorder = _wire_db(monkeypatch, [(scan_cols, [])])

    result = mg._get_system_by_api_key("not-a-uuid-shaped-token-at-all-xyz")

    assert result is None
    assert len(recorder) == 1  # only the encrypted-scan query ran, no second (legacy) query


def test_get_system_by_api_key_skips_rows_it_cannot_decrypt(monkeypatch):
    """An orphaned row (encrypted with a since-rotated key) must not crash
    the lookup for every OTHER system's valid key."""
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    orphaned_enc = db.encrypt_sensitive_json({"key": "orphaned"})
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    good_key = "still-works"
    good_enc = db.encrypt_sensitive_json({"key": good_key})

    scan_cols = mg._SYSTEM_LOOKUP_COLS.split(", ") + ["ingest_api_key_enc"]
    rows = [
        (1, "Orphaned", "orphaned-sys", "custom", True, ["CRITICAL"], None, orphaned_enc),
        (2, "Good", "good-sys", "custom", True, ["CRITICAL"], None, good_enc),
    ]
    _wire_db(monkeypatch, [(scan_cols, rows)])

    result = mg._get_system_by_api_key(good_key)

    assert result is not None
    assert result["id"] == 2


# ── _rotate_system_api_key ───────────────────────────────────────────────────

def test_rotate_system_api_key_returns_a_new_key_and_updates_the_row(monkeypatch):
    _with_encryption_key(monkeypatch)
    recorder = _wire_db(monkeypatch, [([], [])])  # UPDATE; rowcount checked below

    class _CountedCursor(_FakeCursor):
        @property
        def rowcount(self):
            return 1

    # Patch the connection to hand back a cursor whose rowcount reads 1
    # (a real UPDATE ... WHERE id = %s that matched one row).
    class _ConnWithRowcount(_FakeConn):
        def cursor(self):
            return _CountedCursor(self._queue, self._recorder)

    class _CtxWithRowcount(_FakeConnCtx):
        def __enter__(self):
            return _ConnWithRowcount(self._queue, self._recorder)

    monkeypatch.setattr(db, "get_conn", lambda: _CtxWithRowcount([([], [])], recorder))

    new_key = mg._rotate_system_api_key(5)

    assert new_key is not None
    assert len(new_key) >= 32
    sql, params = recorder[0]
    assert "ingest_api_key = NULL" in sql
    assert params[0] is not None  # the new encrypted blob
    assert db.decrypt_sensitive_json(params[0])["key"] == new_key


def test_rotate_system_api_key_returns_none_when_system_not_found(monkeypatch):
    _with_encryption_key(monkeypatch)

    class _ZeroRowcountCursor(_FakeCursor):
        @property
        def rowcount(self):
            return 0

    class _ConnZero(_FakeConn):
        def cursor(self):
            return _ZeroRowcountCursor(self._queue, self._recorder)

    class _CtxZero(_FakeConnCtx):
        def __enter__(self):
            return _ConnZero(self._queue, self._recorder)

    monkeypatch.setattr(db, "get_conn", lambda: _CtxZero([([], [])], []))

    assert mg._rotate_system_api_key(999) is None
