#!/usr/bin/env python3
"""
Unit tests for version_baselines.py — the non-OSV software currency check.
Pure functions, no DB, no network.

    pytest test_version_baselines.py -v
"""

from __future__ import annotations

import version_baselines as vb


# ── check_currency: known products ──────────────────────────────────────────

def test_postgres_current_version_is_true():
    is_current, latest = vb.check_currency("postgresql", "15.8 (Debian 15.8-1)")
    assert is_current is True
    assert latest == "15.8"


def test_postgres_outdated_version_is_false():
    is_current, latest = vb.check_currency("postgresql", "12.0")
    assert is_current is False
    assert latest == "12.20"


def test_postgres_patch_above_baseline_is_true():
    is_current, latest = vb.check_currency("postgresql", "15.20")
    assert is_current is True
    assert latest == "15.8"


def test_openssl_current_version_is_true():
    is_current, latest = vb.check_currency("openssl", "3.2.3")
    assert is_current is True


def test_openssl_outdated_version_is_false():
    is_current, latest = vb.check_currency("openssl", "1.1.0")
    assert is_current is False
    assert latest == "1.1.1w"


# ── check_currency: honest "don't know" cases ───────────────────────────────

def test_unrecognized_product_returns_none_not_false():
    is_current, latest = vb.check_currency("mysql", "8.0.30")
    assert is_current is None
    assert latest is None


def test_unrecognized_major_line_returns_none_not_false():
    """Postgres 9.x isn't in BASELINES at all — must not render as
    'out of date' just because the table hasn't been taught about it."""
    is_current, latest = vb.check_currency("postgresql", "9.6.24")
    assert is_current is None
    assert latest is None


def test_empty_version_string_returns_none():
    is_current, latest = vb.check_currency("postgresql", "")
    assert is_current is None
    assert latest is None


def test_unparseable_version_returns_none():
    is_current, latest = vb.check_currency("postgresql", "not-a-version")
    assert is_current is None
    assert latest is None


def test_product_name_is_case_and_whitespace_insensitive():
    is_current, latest = vb.check_currency("  PostgreSQL  ", "16.4")
    assert is_current is True
    assert latest == "16.4"
