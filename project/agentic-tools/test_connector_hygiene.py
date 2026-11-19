#!/usr/bin/env python3
"""
Unit tests for connector_hygiene.py: connector credential rotation hygiene,
dogfooded on Intelligenza's own observability.poll_connectors store. No DB
needed — normalize_connector_hygiene/evaluate_connector_hygiene_severity are
pure, and check_connector_credential_rotation degrades to "nothing stale"
(not an error, not a fabricated finding) when db.is_available() is False,
same precondition test_pac_negative_sweep.py documents.

    pytest test_connector_hygiene.py -v
"""

from __future__ import annotations

import connector_hygiene as ch
import db


# ── normalize_connector_hygiene ──────────────────────────────────────────────

def test_normalize_connector_hygiene_empty_input():
    result = ch.normalize_connector_hygiene([])
    assert result["stale_connector_count"] == 0
    assert result["oldest_credential_age_days"] == 0
    assert result["stale_connectors"] == []


def test_normalize_connector_hygiene_aggregates_and_finds_oldest():
    stale = [
        {"id": 1, "display_name": "GitHub Repo A", "connector_type": "github_scm", "credential_age_days": 95},
        {"id": 2, "display_name": "Postgres Prod", "connector_type": "postgres_cis", "credential_age_days": 200},
    ]
    result = ch.normalize_connector_hygiene(stale)
    assert result["stale_connector_count"] == 2
    assert result["oldest_credential_age_days"] == 200
    assert {c["id"] for c in result["stale_connectors"]} == {1, 2}


# ── evaluate_connector_hygiene_severity ──────────────────────────────────────

def test_evaluate_connector_hygiene_severity_high_when_any_stale():
    compliance = ch.normalize_connector_hygiene([
        {"id": 1, "display_name": "x", "connector_type": "github_scm", "credential_age_days": 91},
    ])
    assert ch.evaluate_connector_hygiene_severity(compliance) == "HIGH"


def test_evaluate_connector_hygiene_severity_info_when_none_stale():
    compliance = ch.normalize_connector_hygiene([])
    assert ch.evaluate_connector_hygiene_severity(compliance) == "INFO"


# ── check_connector_credential_rotation (no-DB degrade path) ────────────────

def test_check_connector_credential_rotation_does_not_raise_without_database():
    assert not db.is_available()  # documents the precondition this test relies on
    result = ch.check_connector_credential_rotation()
    assert result["violated"] is False
    assert result["severity"] == "INFO"
    assert result["compliance"]["stale_connector_count"] == 0


def test_check_connector_credential_rotation_result_shape():
    result = ch.check_connector_credential_rotation(stale_days=30)
    assert set(result.keys()) == {"compliance", "severity", "violated"}
