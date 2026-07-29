#!/usr/bin/env python3
"""
Unit tests for connector_hygiene_sweep.py — the daily periodic sweep.
db.is_available() is False in this environment, so sweep_once() exercises
the no-DB degrade path (connector_hygiene.check_connector_credential_rotation
reports "nothing stale", never adjudicates) — same precondition
test_pac_negative_sweep.py documents for its own sweep.

    pytest test_connector_hygiene_sweep.py -v
"""

from __future__ import annotations

import asyncio

import connector_hygiene_sweep as chs
import db


def test_sweep_once_does_not_raise_without_database():
    assert not db.is_available()  # documents the precondition
    result = asyncio.run(chs.sweep_once())
    assert isinstance(result, dict)
    assert result["violated"] is False


def test_sweep_once_result_shape():
    result = asyncio.run(chs.sweep_once())
    assert set(result.keys()) == {"compliance", "severity", "violated"}
