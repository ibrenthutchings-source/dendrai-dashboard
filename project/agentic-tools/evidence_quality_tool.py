#!/usr/bin/env python3
"""
PBC/workpaper evidence quality checks — deterministic rules that flag
evidence that's stale, unsigned, or collected outside the period it's
supposed to support.

Deliberately pure (no DB, no HTTP) — same discipline as je_testing_tool.py
and sample_selection_tool.py. evidence_quality_endpoints.py owns persistence
and the one LLM-assisted check (does the evidence's own description
plausibly support the control it's attached to), which is advisory and kept
separate from these deterministic checks on purpose: a reproducible rule
and a probabilistic judgment call should never be mixed into one "quality
score" that hides which kind of finding it actually is.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional


def _parse_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def check_period_mismatch(collected_date, period_start=None, period_end=None) -> Optional[dict]:
    """Flags evidence collected outside its own control-testing period —
    the single most common PBC quality defect: a screenshot pulled today
    to support evidence of a control that operated last quarter."""
    cd = _parse_date(collected_date)
    if cd is None:
        return {
            "code": "MISSING_COLLECTED_DATE", "severity": "HIGH",
            "message": "No collected_date recorded — cannot verify this evidence supports the stated period.",
        }
    ps, pe = _parse_date(period_start), _parse_date(period_end)
    if ps and cd < ps:
        return {
            "code": "PERIOD_MISMATCH", "severity": "HIGH",
            "message": f"Collected {cd.isoformat()}, before the testing period started ({ps.isoformat()}).",
        }
    if pe and cd > pe:
        return {
            "code": "PERIOD_MISMATCH", "severity": "HIGH",
            "message": f"Collected {cd.isoformat()}, after the testing period ended ({pe.isoformat()}).",
        }
    return None


def check_staleness(collected_date, max_age_days: int = 90, today: Optional[date] = None) -> Optional[dict]:
    """Independent of period alignment — evidence can be correctly inside
    its period and still be old enough that it should be refreshed before
    being relied on (e.g., an access list pulled the first week of a
    quarter-long period)."""
    cd = _parse_date(collected_date)
    if cd is None:
        return None  # already covered by check_period_mismatch
    age_days = ((today or date.today()) - cd).days
    if age_days > max_age_days:
        return {
            "code": "STALE", "severity": "MEDIUM",
            "message": f"Collected {age_days} days ago (threshold {max_age_days}d) — consider re-collecting.",
        }
    return None


def check_signature(has_signature: bool, requires_signature: bool) -> Optional[dict]:
    if requires_signature and not has_signature:
        return {
            "code": "UNSIGNED", "severity": "HIGH",
            "message": "This control requires an approver signature/sign-off, but the evidence has none recorded.",
        }
    return None


def run_quality_checks(evidence: dict, today: Optional[date] = None) -> list[dict]:
    """Runs every deterministic check and returns the flags that actually
    fired, most severe first. An evidence item with an empty return is
    clean by these rules — not proof the evidence is good, only that it
    isn't stale, unsigned, or mis-dated."""
    checks = [
        check_period_mismatch(evidence.get("collected_date"), evidence.get("period_start"), evidence.get("period_end")),
        check_staleness(evidence.get("collected_date"), evidence.get("max_age_days", 90), today),
        check_signature(bool(evidence.get("has_signature")), bool(evidence.get("requires_signature"))),
    ]
    flags = [c for c in checks if c]
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    flags.sort(key=lambda f: order.get(f["severity"], 3))
    return flags
