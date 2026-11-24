#!/usr/bin/env python3
"""
PBC request reminders: the schedule rule and the sweep. No database or network.

    pytest test_pbc_reminders.py -v
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

import pbc_reminder_sweep as sweep
import pbc_request_tool as prt

TODAY = date(2026, 11, 23)


def kind(due, status="requested", sent=(), today=TODAY):
    return prt.reminder_kind(status, due, set(sent), today)


# ── schedule ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("due,expected", [
    ("2026-11-30", None),            # 7 days out: too early
    ("2026-11-27", None),            # 4 days out
    ("2026-11-26", "due_soon"),      # 3 days out
    ("2026-11-24", "due_soon"),
    ("2026-11-23", "due_today"),
    ("2026-11-22", "overdue"),
    ("2026-11-17", "overdue"),       # 6 days late
    ("2026-11-16", "escalation"),    # 7 days late
    ("2026-01-01", "escalation"),
])
def test_kind_by_days_to_due(due, expected):
    assert kind(due) == expected


def test_each_kind_is_sent_once_and_rejected_requests_are_chased_too():
    assert kind("2026-11-23", sent={"due_today"}) is None
    assert kind("2026-11-23", sent={"due_soon"}) == "due_today"
    assert kind("2026-11-20", status="rejected") == "overdue"


def test_answered_requests_get_no_reminder():
    for status in ("received", "accepted"):
        assert kind("2020-01-01", status=status) is None


def test_long_overdue_request_gets_only_the_current_kind_not_a_backlog():
    assert kind("2026-10-01", sent=set()) == "escalation"


def test_missing_due_date_is_ignored():
    assert prt.reminder_kind("requested", None, set(), TODAY) is None


# ── sweep ───────────────────────────────────────────────────────────────────

def req(i, due, status="requested", sent=()):
    return {"id": i, "engagement": "FY26", "control_id": f"AC-0{i}", "requirement_key": "k", "title": f"Evidence {i}",
            "due_date": due, "owner": "Dana", "status": status, "reminders_sent": list(sent)}


@pytest.fixture
def env(monkeypatch):
    posts, claims, audits = [], set(), []
    rows = []
    monkeypatch.setattr(sweep.mcp_governance, "_ALERT_WEBHOOK_URL", "http://hook")
    monkeypatch.setattr(sweep.mcp_governance, "_post_webhook_alert",
                        lambda text, fields, color="": posts.append({"text": text, "fields": fields, "color": color}))
    monkeypatch.setattr(sweep.db, "list_unanswered_pbc_requests", lambda: list(rows))

    def claim(rid, k):
        if (rid, k) in claims:
            return False
        claims.add((rid, k))
        return True

    monkeypatch.setattr(sweep.db, "claim_pbc_reminder", claim)
    monkeypatch.setattr(sweep.db, "insert_audit_log_entry", lambda *a, **k: audits.append({"args": a, **k}) or 1)
    # pin "today" inside the rule used by the sweep
    real = prt.reminder_kind
    monkeypatch.setattr(sweep.prt, "reminder_kind", lambda s, d, sent, today=None: real(s, d, sent, TODAY))
    return type("Env", (), {"posts": posts, "claims": claims, "audits": audits, "rows": rows})


def run():
    return asyncio.run(sweep.sweep_once())


def test_sweep_groups_by_kind_audits_and_claims(env):
    env.rows += [req(1, "2026-11-23"), req(2, "2026-11-22"), req(3, "2026-11-10"), req(4, "2027-02-01")]
    assert run() == 3
    texts = sorted(p["text"] for p in env.posts)
    assert texts == ["PBC requests ESCALATED: overdue a week or more: 1", "PBC requests OVERDUE: 1",
                     "PBC requests due today: 1"]
    assert env.audits[-1]["args"][:2] == ("pbc", "pbc_reminders_sent")
    assert env.audits[-1]["detail"] == {"due_today": 1, "overdue": 1, "escalation": 1}
    assert env.claims == {(1, "due_today"), (2, "overdue"), (3, "escalation")}


def test_second_pass_sends_nothing_because_reminders_are_claimed(env):
    env.rows += [req(1, "2026-11-23")]
    assert run() == 1
    env.posts.clear()
    assert run() == 0 and env.posts == []


def test_a_competing_instance_that_loses_the_claim_sends_nothing(env):
    env.rows += [req(1, "2026-11-23")]
    env.claims.add((1, "due_today"))     # the other instance claimed it first
    assert run() == 0 and env.posts == []


def test_no_webhook_means_nothing_sent_and_nothing_claimed(env, monkeypatch):
    monkeypatch.setattr(sweep.mcp_governance, "_ALERT_WEBHOOK_URL", "")
    env.rows += [req(1, "2026-11-23")]
    assert run() == 0 and not env.claims and not env.posts


def test_multi_tenant_never_posts_to_the_shared_webhook(env, monkeypatch):
    env.rows += [req(1, "2026-11-23")]
    token = sweep.db._current_tenant.set("acme")     # what _multi_tenant_loop does before each tick
    try:
        assert run() == 0
    finally:
        sweep.db._current_tenant.reset(token)
    assert not env.claims and not env.posts


def test_long_lists_are_truncated_with_a_count(env):
    env.rows += [req(i, "2026-11-22") for i in range(1, 31)]
    run()
    fields = env.posts[0]["fields"]
    assert len(fields) == sweep._MAX_LINES + 1 and "5 more" in fields[-1]["value"]
    assert env.posts[0]["text"].endswith(": 30")


def test_nothing_due_means_no_audit_entry(env):
    env.rows += [req(1, "2027-06-01")]
    assert run() == 0 and env.audits == []
