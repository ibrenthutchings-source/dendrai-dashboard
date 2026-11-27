"""Server-side recurring Run Loop: collection, review creation, owner + manager notification,
Slack / Teams payloads, reminders and the endpoint rules. DB / auth / HTTP are faked."""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import loop_schedule_endpoints as ep
import loop_schedule_runner as run
import report_delivery_endpoints as rd

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
OWNER = {"id": 1, "username": "ana", "display_name": "Ana Owner"}
MANAGER = {"id": 2, "username": "bo", "display_name": "Bo Manager"}


class FakeDB:
    def __init__(self):
        self.reviews, self.notes, self.finished, self.deliveries, self.reminded = [], [], [], [], []
        self.destinations = []
        self.schedules = {}

    # reviews / schedules
    def create_scheduled_review(self, **kw):
        self.reviews.append(kw); return len(self.reviews)
    def finish_loop_schedule(self, sid, **kw): self.finished.append((sid, kw))
    def insert_user_notification(self, user_id, kind, title, body, link, review_id, role):
        self.notes.append(dict(user_id=user_id, kind=kind, title=title, body=body, link=link, review_id=review_id, role=role)); return len(self.notes)
    def list_active_destinations_for(self, t): return [d for d in self.destinations if t in d["artifact_types"]]
    def record_report_delivery(self, **kw): self.deliveries.append(kw)
    def list_reviews_needing_reminder(self, h, m): return [{"id": 7, "ticker": "NVDA", "owner_id": 1, "reminders_sent": 1}]
    def mark_review_reminded(self, rid): self.reminded.append(rid)
    def is_available(self): return True
    def get_loop_schedule(self, t): return self.schedules.get(t.upper())
    def upsert_loop_schedule(self, ticker, **kw):
        self.schedules[ticker.upper()] = {"id": 1, "ticker": ticker.upper(), **kw}; return self.schedules[ticker.upper()]
    def claim_loop_schedule(self, sid): return True
    def get_scheduled_review(self, rid, include_snapshot=False):
        return {"id": rid, "owner_id": 1, "manager_id": 2, "status": "pending", "snapshot": {"x": 1}}
    def resolve_scheduled_review(self, rid, uid, res): return True


@pytest.fixture
def fake(monkeypatch):
    f = FakeDB()
    for name in ("create_scheduled_review", "finish_loop_schedule", "insert_user_notification", "list_active_destinations_for",
                 "record_report_delivery", "list_reviews_needing_reminder", "mark_review_reminded", "is_available",
                 "get_loop_schedule", "upsert_loop_schedule", "claim_loop_schedule", "get_scheduled_review",
                 "resolve_scheduled_review"):
        monkeypatch.setattr(run.db if hasattr(run.db, name) else ep.db, name, getattr(f, name))
    monkeypatch.setattr(run.auth_db, "get_user_by_id", lambda uid: {1: OWNER, 2: MANAGER}.get(uid))
    monkeypatch.setattr(run.auth_db, "get_manager_of", lambda uid: MANAGER if uid == 1 else None)
    monkeypatch.setattr(run, "_PUBLIC_URL", "https://app.example")
    return f


def _collectors(fail=(), material=True, calls=None):
    calls = calls if calls is not None else []
    def mk(name):
        def fn(t, o):
            calls.append(name)
            if name in fail:
                raise RuntimeError(f"{name} boom")
            if name == "full_analysis":
                return {"_db_id": 99, "ticker": t}
            if name == "material_accounts":
                return {"accounts": [{"is_material": material}]}
            return {"ok": name}
        return fn
    return {n: mk(n) for n in ["full_analysis", "material_accounts", "material_forecasts", "risk_factors", "eight_k",
                               "proxy", "comments", "rf_changes", "peers", "compliance_rss"]}


# ── collection ──
def test_collect_runs_sources_in_order_one_at_a_time():
    calls = []
    payloads, errors = run.collect("NVDA", {"rssEnabledFeeds": ["f1"]}, _collectors(calls=calls), sleep=lambda s: None)
    assert calls == ["full_analysis", "material_accounts", "material_forecasts", "risk_factors", "eight_k",
                     "proxy", "comments", "rf_changes", "peers", "compliance_rss"]
    assert errors == [] and payloads["full_analysis"]["_db_id"] == 99


def test_a_failed_source_is_recorded_and_the_rest_still_run():
    payloads, errors = run.collect("NVDA", {}, _collectors(fail={"proxy"}), sleep=lambda s: None)
    assert [e["source"] for e in errors] == ["proxy"] and "eight_k" in payloads and "peers" in payloads


def test_failed_financial_analysis_aborts_the_run():
    with pytest.raises(RuntimeError, match="financial analysis failed"):
        run.collect("NVDA", {}, _collectors(fail={"full_analysis"}), sleep=lambda s: None)


def test_private_company_skips_every_edgar_source():
    calls = []
    run.collect("PVT-ACME", {}, _collectors(calls=calls), sleep=lambda s: None)
    assert not set(calls) & {"risk_factors", "eight_k", "proxy", "comments", "rf_changes", "peers"}


def test_no_material_accounts_skips_their_forecast():
    calls = []
    run.collect("NVDA", {}, _collectors(material=False, calls=calls), sleep=lambda s: None)
    assert "material_forecasts" not in calls


def test_busy_limiter_is_retried_once_not_failed():
    n = {"c": 0}
    def busy_then_ok(t, o):
        n["c"] += 1
        if n["c"] == 1:
            raise HTTPException(status_code=503, detail="busy")
        return {"_db_id": 5}
    c = _collectors(); c["full_analysis"] = busy_then_ok
    slept = []
    payloads, _ = run.collect("NVDA", {}, c, sleep=slept.append)
    assert payloads["full_analysis"]["_db_id"] == 5 and len(slept) == 1


# ── recipients ──
def test_owner_and_manager_are_both_notified(fake):
    out = run.notify("review_ready", ticker="NVDA", review_id=3, owner_id=1)
    assert [(n["user_id"], n["role"]) for n in fake.notes] == [(1, "owner"), (2, "manager")]
    assert out["recipients"] == ["Ana Owner", "Bo Manager"]
    assert fake.notes[0]["link"] == "https://app.example/?review=3"


def test_no_manager_means_only_the_owner(fake, monkeypatch):
    monkeypatch.setattr(run.auth_db, "get_manager_of", lambda uid: None)
    run.notify("review_ready", ticker="NVDA", review_id=3, owner_id=1)
    assert [n["user_id"] for n in fake.notes] == [1]


def test_manager_who_is_also_owner_is_notified_once(fake, monkeypatch):
    monkeypatch.setattr(run.auth_db, "get_manager_of", lambda uid: OWNER)
    run.notify("review_ready", ticker="NVDA", review_id=3, owner_id=1)
    assert [n["user_id"] for n in fake.notes] == [1]


def test_message_carries_no_risk_data(fake):
    run.notify("review_ready", ticker="NVDA", review_id=3, owner_id=1, partial=True)
    text = fake.notes[0]["title"] + fake.notes[0]["body"]
    assert "NVDA" in text and "RED" not in text.upper().replace("REVIEW", "") and "score" not in text.lower()


# ── running a schedule ──
def _sched(**kw):
    return {"id": 1, "ticker": "NVDA", "owner_id": 1, "enabled": True, "cadence": "daily", "run_time": "08:00",
            "tz": "UTC", "options": {}, **kw}


def test_run_creates_a_pending_review_notifies_and_advances(fake):
    res = run.run_schedule(_sched(), collectors=_collectors(), sleep=lambda s: None, now=NOW)
    assert res["status"] == "ok" and res["review_id"] == 1
    rv = fake.reviews[0]
    assert rv["run_id"] == 99 and rv["owner_id"] == 1 and rv["manager_id"] == 2
    assert rv["snapshot"]["payloads"]["full_analysis"]["_db_id"] == 99
    sid, fin = fake.finished[0]
    assert fin["status"] == "ok" and fin["review_id"] == 1
    assert fin["next_run_at"] == datetime(2026, 11, 28, 8, 0, tzinfo=timezone.utc)
    assert {n["kind"] for n in fake.notes} == {"review_ready"}


def test_partial_run_is_flagged(fake):
    res = run.run_schedule(_sched(), collectors=_collectors(fail={"peers"}), sleep=lambda s: None, now=NOW)
    assert res["status"] == "partial" and fake.finished[0][1]["status"] == "partial"
    assert "could not be collected" in fake.notes[0]["body"]


def test_failed_run_still_releases_the_claim_and_tells_people(fake):
    res = run.run_schedule(_sched(), collectors=_collectors(fail={"full_analysis"}), sleep=lambda s: None, now=NOW)
    assert res["status"] == "failed" and fake.reviews == []
    assert fake.finished[0][1]["status"] == "failed" and fake.finished[0][1]["next_run_at"] is not None
    assert {n["kind"] for n in fake.notes} == {"schedule_failed"} and len(fake.notes) == 2


def test_disabled_schedule_has_no_next_run(fake):
    run.run_schedule(_sched(enabled=False), collectors=_collectors(), sleep=lambda s: None, now=NOW)
    assert fake.finished[0][1]["next_run_at"] is None


# ── Slack / Teams ──
def _dest(fmt):
    return {"id": 1, "display_name": f"{fmt} chan", "url": "https://hooks.example/x", "http_method": "POST",
            "auth_type": "none", "credentials": None, "headers": None, "payload_format": fmt,
            "artifact_types": ["review_ready"]}


def test_slack_and_teams_payloads_are_shaped_and_link_to_the_review():
    p = {"title": "NVDA risk assessment ready for review", "text": "waiting", "link": "https://app.example/?review=3",
         "recipients": ["Ana Owner", "Bo Manager"]}
    slack = rd._shape_payload("slack", "review_ready", p)
    assert "https://app.example/?review=3" in slack["text"] and slack["attachments"][0]["fields"][0]["value"] == "Ana Owner, Bo Manager"
    teams = rd._shape_payload("msteams", "review_ready", p)
    assert teams["@type"] == "MessageCard" and teams["potentialAction"][0]["targets"][0]["uri"] == "https://app.example/?review=3"
    # a relative (no PUBLIC_URL) link must not be sent as an action — Teams rejects it
    rel = rd._shape_payload("msteams", "review_ready", {**p, "link": "/?review=3"})
    assert "potentialAction" not in rel


def test_webhooks_go_to_every_subscribed_destination_and_are_logged(fake, monkeypatch):
    fake.destinations = [_dest("slack"), _dest("msteams"), {**_dest("slack"), "artifact_types": ["loop_report"]}]
    sent = []
    monkeypatch.setattr(rd, "_dispatch", lambda **kw: sent.append(kw) or
                        {"status": "ok", "http_status": 200, "error": None, "response_excerpt": "ok",
                         "content_sha256": "0" * 64, "content_bytes": 10, "duration_ms": 5})
    out = run.notify("review_ready", ticker="NVDA", review_id=3, owner_id=1)
    assert [s["payload_format"] for s in sent] == ["slack", "msteams"]          # the unsubscribed one is skipped
    assert all(s["artifact_type"] == "review_ready" for s in sent)
    assert len(fake.deliveries) == 2 and all(d["sent_by"] == "system:loop-schedule" for d in fake.deliveries)
    assert [h["status"] for h in out["webhooks"]] == ["ok", "ok"]


def test_a_failing_webhook_never_blocks_in_app_notification(fake, monkeypatch):
    fake.destinations = [_dest("slack")]
    def boom(**kw): raise RuntimeError("network down")
    monkeypatch.setattr(rd, "_dispatch", boom)
    out = run.notify("review_ready", ticker="NVDA", review_id=3, owner_id=1)
    assert out["in_app"] == 2 and out["webhooks"] == []


def test_reminders_go_to_owner_and_manager_and_are_counted(fake):
    assert run.send_reminders() == 1 and fake.reminded == [7]
    assert {n["kind"] for n in fake.notes} == {"review_reminder"} and len(fake.notes) == 2


# ── endpoints ──
USER = {"id": 1, "username": "ana", "display_name": "Ana Owner", "role": "analyst"}


def test_put_schedule_makes_caller_owner_and_computes_next_run(fake):
    out = ep.put_schedule("nvda", ep.ScheduleBody(cadence="weekly", weekday=1, run_time="08:00", tz="America/Chicago",
                                                  options={"industry": "semis"}), USER)
    s = out["schedule"]
    assert s["ticker"] == "NVDA" and s["owner_name"] == "Ana Owner" and s["next_run_at"] is not None
    assert fake.schedules["NVDA"]["owner_id"] == 1


def test_disabled_schedule_saves_without_a_next_run(fake):
    ep.put_schedule("NVDA", ep.ScheduleBody(enabled=False), USER)
    assert fake.schedules["NVDA"]["next_run_at"] is None


def test_review_visible_to_owner_manager_and_admin_only(fake):
    assert ep.review_snapshot(5, {"id": 1, "role": "analyst"})["snapshot"] == {"x": 1}
    assert ep.review_snapshot(5, {"id": 2, "role": "analyst"})["snapshot"] == {"x": 1}
    assert ep.review_snapshot(5, {"id": 9, "role": "admin"})["snapshot"] == {"x": 1}
    with pytest.raises(HTTPException) as e:
        ep.review_snapshot(5, {"id": 9, "role": "analyst"})
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e2:
        ep.resolve_review(5, ep.ResolveBody(resolution="approved"), {"id": 9, "role": "analyst"})
    assert e2.value.status_code == 403


def test_resolve_rejects_unknown_resolution(fake):
    with pytest.raises(HTTPException) as e:
        ep.resolve_review(5, ep.ResolveBody(resolution="whatever"), USER)
    assert e.value.status_code == 400


def test_run_now_needs_a_saved_schedule_and_a_free_claim(fake, monkeypatch):
    with pytest.raises(HTTPException) as e:
        ep.run_now("NVDA", USER)
    assert e.value.status_code == 404
    fake.schedules["NVDA"] = {"id": 1, "ticker": "NVDA"}
    monkeypatch.setattr(ep.db, "claim_loop_schedule", lambda sid: False)
    with pytest.raises(HTTPException) as e2:
        ep.run_now("NVDA", USER)
    assert e2.value.status_code == 409
