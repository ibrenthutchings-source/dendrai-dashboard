#!/usr/bin/env python3
"""
Unit tests for the evidence_records tamper-evidence hash chain
(db._evidence_chain_hash / db.verify_evidence_chain). A per-record HMAC
(already tested via /evidence/records/{id}/verify) proves a row's own
content wasn't altered; the chain additionally proves trail completeness —
that no row was deleted or reordered — which no per-record check can catch
on its own. No real DB connection needed: _evidence_chain_hash is pure, and
verify_evidence_chain degrades to a documented default when
db.is_available() is False, same precondition other db-function tests in
this suite document.

    pytest test_evidence_chain.py -v
"""

from __future__ import annotations

import db


# ── _evidence_chain_hash (pure) ──────────────────────────────────────────────

def test_chain_hash_is_deterministic():
    h1 = db._evidence_chain_hash("prev123", "sig456")
    h2 = db._evidence_chain_hash("prev123", "sig456")
    assert h1 == h2


def test_chain_hash_changes_with_prev_hash():
    h1 = db._evidence_chain_hash("prevA", "sig456")
    h2 = db._evidence_chain_hash("prevB", "sig456")
    assert h1 != h2


def test_chain_hash_changes_with_signature():
    h1 = db._evidence_chain_hash("prev123", "sigA")
    h2 = db._evidence_chain_hash("prev123", "sigB")
    assert h1 != h2


def test_chain_hash_none_prev_treated_as_genesis():
    assert db._evidence_chain_hash(None, "sig456") == db._evidence_chain_hash(db.EVIDENCE_CHAIN_GENESIS_HASH, "sig456")


def test_chain_hash_is_a_64_char_hex_digest():
    h = db._evidence_chain_hash(None, "sig456")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_chain_hash_simulated_three_record_chain_is_self_consistent():
    """Simulates what insert_evidence_record/verify_evidence_chain do across
    three sequential inserts, without a DB — proves the linking logic itself
    (not just the single-hop function) is internally consistent."""
    sigs = ["sig-one", "sig-two", "sig-three"]
    chain = []
    prev = None
    for sig in sigs:
        h = db._evidence_chain_hash(prev, sig)
        chain.append(h)
        prev = h

    # Recomputing from genesis must reproduce the exact same chain.
    recomputed_prev = None
    for i, sig in enumerate(sigs):
        recomputed = db._evidence_chain_hash(recomputed_prev, sig)
        assert recomputed == chain[i]
        recomputed_prev = recomputed


def test_chain_hash_detects_a_deleted_middle_record():
    """The core property this chain exists to prove: if record #2 of 3 is
    deleted, record #3's stored chain_hash (computed against #2's hash) no
    longer matches what re-deriving the chain from the SURVIVING records
    (#1 then #3) would produce — exactly what verify_evidence_chain's
    row-by-row walk would flag as a break."""
    sigs = ["sig-one", "sig-two", "sig-three"]
    h1 = db._evidence_chain_hash(None, sigs[0])
    h2 = db._evidence_chain_hash(h1, sigs[1])
    h3_original = db._evidence_chain_hash(h2, sigs[2])

    # Record #2 deleted — re-deriving #3's expected hash from the surviving
    # record #1 directly (skipping #2) must NOT match the stored h3_original.
    h3_if_recomputed_after_deletion = db._evidence_chain_hash(h1, sigs[2])
    assert h3_if_recomputed_after_deletion != h3_original


# ── verify_evidence_chain (no-DB degrade path) ──────────────────────────────

def test_verify_evidence_chain_does_not_raise_without_database():
    assert not db.is_available()  # documents the precondition this test relies on
    result = db.verify_evidence_chain()
    assert isinstance(result, dict)
    assert "valid" in result and "checked" in result
