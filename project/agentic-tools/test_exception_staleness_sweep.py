#!/usr/bin/env python3
"""
Unit tests for exception_staleness_sweep.py — daily risk_rating escalation
for exceptions that have sat pending past EXCEPTION_STALE_DAYS. db is
monkeypatched at the boundary — no real database.

    pytest test_exception_staleness_sweep.py -v
"""

from __future__ import annotations

import asyncio

import db
import exception_staleness_sweep as ess


def test_sweep_once_no_db_is_a_no_op(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: False)
    assert asyncio.run(ess.sweep_once()) == 0


def test_sweep_once_returns_escalated_count(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)
    captured = {}
    def _fake_escalate(stale_days):
        captured["stale_days"] = stale_days
        return 4
    monkeypatch.setattr(db, "escalate_stale_exceptions", _fake_escalate)

    n = asyncio.run(ess.sweep_once())

    assert n == 4
    assert captured["stale_days"] == ess._STALE_DAYS


def test_sweep_once_zero_escalated_is_quiet(monkeypatch):
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "escalate_stale_exceptions", lambda stale_days: 0)
    assert asyncio.run(ess.sweep_once()) == 0
