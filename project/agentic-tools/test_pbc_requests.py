#!/usr/bin/env python3
"""
Request-as-Code for PBC: pure rules (pbc_request_tool) and the endpoint
workflow (pbc_request_endpoints) against an in-memory fake of the db layer.

    pytest test_pbc_requests.py -v
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

import pbc_request_endpoints as ep
import pbc_request_tool as prt

TODAY = date(2026, 11, 23)


def req(**kw):
    base = {"control_id": "AC-01", "requirement_key": "access-review", "title": "Quarterly access review",
            "frequency": "quarterly", "requires_signature": True, "max_age_days": 90, "owner": "it.manager"}
    base.update(kw)
    return prt.normalise_requirement(base)


# ── requirements ────────────────────────────────────────────────────────────

def test_normalise_defaults_and_trims():
    n = prt.normalise_requirement({"control_id": " AC-01 ", "requirement_key": "Access-Review", "title": " T "})
    assert n["control_id"] == "AC-01" and n["requirement_key"] == "access-review" and n["title"] == "T"
    assert n["source_system"] == "manual" and n["frequency"] == "once" and n["max_age_days"] == 90


@pytest.mark.parametrize("bad", [
    {"requirement_key": "k", "title": "t"},
    {"control_id": "C", "requirement_key": "bad key!", "title": "t"},
    {"control_id": "C", "requirement_key": "k", "title": " "},
    {"control_id": "C", "requirement_key": "k", "title": "t", "frequency": "daily"},
    {"control_id": "C", "requirement_key": "k", "title": "t", "max_age_days": -1},
    {"control_id": "C", "requirement_key": "k", "title": "t", "max_age_days": True},
])
def test_normalise_rejects_bad_input(bad):
    with pytest.raises(prt.RequestError):
        prt.normalise_requirement(bad)


# ── generation ──────────────────────────────────────────────────────────────

def test_generate_is_deterministic_and_scoped():
    reqs = [req(control_id="IT-02", requirement_key="b"), req(control_id="AC-01", requirement_key="z"),
            req(control_id="AC-01", requirement_key="a")]
    a = prt.generate_requests(reqs, "FY26 SOX", "2026-01-01", "2026-03-31", "2026-04-30")
    b = prt.generate_requests(list(reversed(reqs)), "FY26 SOX", "2026-01-01", "2026-03-31", "2026-04-30")
    assert a == b
    assert [(r["control_id"], r["requirement_key"]) for r in a] == [("AC-01", "a"), ("AC-01", "z"), ("IT-02", "b")]
    scoped = prt.generate_requests(reqs, "FY26 SOX", "2026-01-01", "2026-03-31", "2026-04-30", ["IT-02"])
    assert [r["control_id"] for r in scoped] == ["IT-02"]


def test_generated_request_freezes_the_requirement():
    r = req()
    row = prt.generate_requests([r], "E", "2026-01-01", "2026-03-31", "2026-04-30")[0]
    assert row["requirement_snapshot"]["requires_signature"] is True
    assert row["requirement_hash"] == prt.snapshot_hash(row["requirement_snapshot"])
    changed = prt.generate_requests([req(requires_signature=False)], "E", "2026-01-01", "2026-03-31", "2026-04-30")[0]
    assert changed["requirement_hash"] != row["requirement_hash"]


@pytest.mark.parametrize("args", [
    ("", "2026-01-01", "2026-03-31", "2026-04-30"),
    ("E", "2026-03-31", "2026-01-01", "2026-04-30"),
    ("E", "2026-01-01", "2026-03-31", ""),
    ("E", "not-a-date", "2026-03-31", "2026-04-30"),
])
def test_generate_rejects_bad_input(args):
    with pytest.raises(prt.RequestError):
        prt.generate_requests([req()], *args)


# ── status and transitions ──────────────────────────────────────────────────

def test_overdue_is_derived_not_stored():
    assert prt.derive_status("requested", "2026-11-22", TODAY) == "overdue"
    assert prt.derive_status("rejected", "2026-11-22", TODAY) == "overdue"
    assert prt.derive_status("requested", "2026-11-23", TODAY) == "requested"
    assert prt.derive_status("received", "2026-01-01", TODAY) == "received"
    assert prt.derive_status("accepted", "2026-01-01", TODAY) == "accepted"


@pytest.mark.parametrize("cur,new,ok", [
    ("requested", "received", True), ("received", "accepted", True), ("received", "rejected", True),
    ("rejected", "received", True), ("requested", "accepted", False), ("accepted", "rejected", False),
    ("accepted", "received", False), ("received", "requested", False),
])
def test_transitions(cur, new, ok):
    if ok:
        prt.check_transition(cur, new)
    else:
        with pytest.raises(prt.RequestError):
            prt.check_transition(cur, new)


def test_review_independence():
    prt.check_review_independence("carol", "alice", "bob")
    with pytest.raises(prt.RequestError):
        prt.check_review_independence("Alice", "alice", None)
    with pytest.raises(prt.RequestError):
        prt.check_review_independence("bob", "alice", "BOB")


# ── automatic evidence checks ───────────────────────────────────────────────

def request_row(**kw):
    row = prt.generate_requests([req()], "E", "2026-01-01", "2026-03-31", "2026-04-30")[0]
    row.update(kw)
    return row


def good_evidence(**kw):
    ev = {"control_id": "AC-01", "period_start": "2026-01-01", "period_end": "2026-03-31",
          "collected_date": "2026-11-01", "has_signature": True}
    ev.update(kw)
    return ev


def codes(findings):
    return [f["code"] for f in findings]


def test_clean_evidence_has_no_findings():
    assert prt.check_evidence(request_row(), good_evidence(), TODAY) == []


def test_each_check_fires_independently():
    r = request_row()
    assert codes(prt.check_evidence(r, good_evidence(control_id="AC-99"), TODAY)) == ["WRONG_CONTROL"]
    assert codes(prt.check_evidence(r, good_evidence(period_end="2026-02-28"), TODAY)) == ["PERIOD_NOT_COVERED"]
    assert codes(prt.check_evidence(r, good_evidence(period_start="2026-02-01"), TODAY)) == ["PERIOD_NOT_COVERED"]
    assert codes(prt.check_evidence(r, good_evidence(period_start=None, period_end=None), TODAY)) == ["PERIOD_MISSING"]
    assert codes(prt.check_evidence(r, good_evidence(has_signature=False), TODAY)) == ["SIGNATURE_REQUIRED"]
    assert codes(prt.check_evidence(r, good_evidence(collected_date="2026-05-01"), TODAY)) == ["STALE"]
    assert codes(prt.check_evidence(r, good_evidence(collected_date=None), TODAY)) == ["COLLECTED_DATE_MISSING"]


def test_checks_use_the_frozen_snapshot_not_the_live_requirement():
    r = request_row()
    r["requirement_snapshot"] = {**r["requirement_snapshot"], "requires_signature": False}
    assert prt.check_evidence(r, good_evidence(has_signature=False), TODAY) == []


def test_findings_are_sorted_most_severe_first():
    f = prt.check_evidence(request_row(), good_evidence(has_signature=False, control_id="X"), TODAY)
    assert [x["severity"] for x in f] == ["HIGH", "MEDIUM"]


def test_summarise_counts_and_percent():
    rows = [{"status": "accepted", "due_date": "2026-01-01"}, {"status": "requested", "due_date": "2026-01-01"},
            {"status": "requested", "due_date": "2027-01-01"}, {"status": "received", "due_date": "2026-01-01"}]
    s = prt.summarise(rows, TODAY)
    assert (s["accepted"], s["overdue"], s["requested"], s["received"], s["total"]) == (1, 1, 1, 1, 4)
    assert s["percent_accepted"] == 25.0
    assert prt.summarise([])["percent_accepted"] == 0.0


# ── endpoint workflow with a fake db ────────────────────────────────────────

class FakeDb:
    def __init__(self):
        self.requirements, self.requests, self.evidence, self.audits = [], [], {}, []
        self._id = 0

    def is_available(self):
        return True

    def insert_audit_log_entry(self, category, action, actor=None, target=None, detail=None,
                               ip_address=None, outcome="success"):
        self.audits.append({"action": action, "actor": actor, "target": target, "outcome": outcome,
                            "detail": detail})
        return 1

    def upsert_evidence_requirement(self, r, by):
        self.requirements = [x for x in self.requirements
                             if (x["control_id"], x["requirement_key"]) != (r["control_id"], r["requirement_key"])]
        row = {**r, "id": len(self.requirements) + 1, "updated_by": by, "active": r.get("active", True)}
        self.requirements.append(row)
        return row

    def list_evidence_requirements(self, control_id=None, active_only=True):
        return [r for r in self.requirements if (not active_only or r["active"])
                and (not control_id or r["control_id"] == control_id)]

    def create_pbc_requests(self, rows, by):
        keys = {(r["engagement"], r["control_id"], r["requirement_key"], r["period_start"], r["period_end"])
                for r in self.requests}
        created = 0
        for r in rows:
            k = (r["engagement"], r["control_id"], r["requirement_key"], r["period_start"], r["period_end"])
            if k in keys:
                continue
            self._id += 1
            self.requests.append({**r, "id": self._id, "status": "requested", "evidence_id": None,
                                  "submitted_by": None, "checks": None, "created_by": by})
            created += 1
        return {"created": created, "existing": len(rows) - created}

    def list_pbc_requests(self, engagement=None, status=None, control_id=None, limit=500):
        return [r for r in self.requests if (not engagement or r["engagement"] == engagement)
                and (not control_id or r["control_id"] == control_id)]

    def get_pbc_request(self, rid):
        return next((r for r in self.requests if r["id"] == rid), None)

    def get_pbc_evidence(self, eid):
        return self.evidence.get(eid)

    def receive_pbc_request(self, rid, eid, by, checks, version):
        r = self.get_pbc_request(rid)
        if not r or r["status"] not in ("requested", "rejected"):
            return None
        r.update(status="received", evidence_id=eid, submitted_by=by, checks=checks, checks_version=version)
        return r

    def review_pbc_request(self, rid, decision, reviewer, note):
        r = self.get_pbc_request(rid)
        if not r or r["status"] != "received":
            return None
        r.update(status=decision, reviewed_by=reviewer, review_note=note)
        return r


@pytest.fixture
def fake(monkeypatch):
    f = FakeDb()
    for name in ("is_available", "insert_audit_log_entry", "upsert_evidence_requirement",
                 "list_evidence_requirements", "create_pbc_requests", "list_pbc_requests", "get_pbc_request",
                 "get_pbc_evidence", "receive_pbc_request", "review_pbc_request"):
        monkeypatch.setattr(ep.db, name, getattr(f, name))
    return f


ALICE, BOB, CAROL = {"username": "alice"}, {"username": "bob"}, {"username": "carol"}


def declare(fake):
    ep.put_requirement(ep.RequirementIn(control_id="AC-01", requirement_key="access-review",
                                        title="Quarterly access review", requires_signature=True), ALICE)


def generate(fake, **kw):
    body = dict(engagement="FY26 SOX", period_start="2026-01-01", period_end="2026-03-31", due_date="2026-04-30")
    body.update(kw)
    return ep.generate(ep.GenerateIn(**body), ALICE)


def test_full_happy_path_and_audit_trail(fake):
    declare(fake)
    assert generate(fake)["created"] == 1
    rid = fake.requests[0]["id"]
    fake.evidence[10] = {"id": 10, "control_id": "AC-01", "period_start": "2026-01-01", "period_end": "2026-03-31",
                         "collected_date": date.today().isoformat(), "has_signature": True, "created_by": "bob"}
    got = ep.receive(rid, ep.ReceiveIn(evidence_id=10), BOB)
    assert got["status"] == "received" and got["checks"] == []
    done = ep.review(rid, ep.ReviewIn(decision="accepted"), CAROL)
    assert done["status"] == "accepted" and done["reviewed_by"] == "carol"
    assert [a["action"] for a in fake.audits] == ["pbc_requirement_saved", "pbc_requests_generated",
                                                   "pbc_request_received", "pbc_request_reviewed"]
    assert all(a["outcome"] == "success" for a in fake.audits)


def test_generating_twice_creates_nothing_new(fake):
    declare(fake)
    assert generate(fake) == {"engagement": "FY26 SOX", "created": 1, "existing": 0}
    assert generate(fake) == {"engagement": "FY26 SOX", "created": 0, "existing": 1}


def test_generate_without_requirements_fails_with_audit(fake):
    with pytest.raises(HTTPException) as ei:
        generate(fake)
    assert ei.value.status_code == 422
    assert fake.audits[-1]["outcome"] == "failure" and fake.audits[-1]["detail"]["reason"] == "no_requirements"


def test_findings_are_recorded_on_receipt(fake):
    declare(fake)
    generate(fake)
    fake.evidence[11] = {"id": 11, "control_id": "AC-01", "period_start": "2026-01-01", "period_end": "2026-03-31",
                         "collected_date": date.today().isoformat(), "has_signature": False, "created_by": "bob"}
    got = ep.receive(fake.requests[0]["id"], ep.ReceiveIn(evidence_id=11), BOB)
    assert [f["code"] for f in got["checks"]] == ["SIGNATURE_REQUIRED"]
    assert fake.audits[-1]["detail"]["findings"] == ["SIGNATURE_REQUIRED"]


def test_submitter_cannot_review_their_own_evidence(fake):
    declare(fake)
    generate(fake)
    rid = fake.requests[0]["id"]
    fake.evidence[10] = {"id": 10, "control_id": "AC-01", "period_start": "2026-01-01", "period_end": "2026-03-31",
                         "collected_date": date.today().isoformat(), "has_signature": True, "created_by": "bob"}
    ep.receive(rid, ep.ReceiveIn(evidence_id=10), BOB)
    for reviewer in (BOB, {"username": "Bob"}):    # the submitter, in any letter case
        with pytest.raises(HTTPException) as ei:
            ep.review(rid, ep.ReviewIn(decision="accepted"), reviewer)
        assert ei.value.status_code == 403
    assert fake.audits[-1]["outcome"] == "denied" and fake.requests[0]["status"] == "received"


def test_evidence_logger_cannot_review_even_if_someone_else_linked_it(fake):
    declare(fake)
    generate(fake)
    rid = fake.requests[0]["id"]
    fake.evidence[10] = {"id": 10, "control_id": "AC-01", "period_start": "2026-01-01", "period_end": "2026-03-31",
                         "collected_date": date.today().isoformat(), "has_signature": True, "created_by": "dave"}
    ep.receive(rid, ep.ReceiveIn(evidence_id=10), BOB)          # bob links dave's evidence
    with pytest.raises(HTTPException) as ei:
        ep.review(rid, ep.ReviewIn(decision="accepted"), {"username": "dave"})
    assert ei.value.status_code == 403


def test_rejection_needs_a_note_then_allows_resubmission(fake):
    declare(fake)
    generate(fake)
    rid = fake.requests[0]["id"]
    fake.evidence[10] = {"id": 10, "control_id": "AC-01", "period_start": "2026-01-01", "period_end": "2026-03-31",
                         "collected_date": date.today().isoformat(), "has_signature": True, "created_by": "bob"}
    ep.receive(rid, ep.ReceiveIn(evidence_id=10), BOB)
    with pytest.raises(HTTPException) as ei:
        ep.review(rid, ep.ReviewIn(decision="rejected"), CAROL)
    assert ei.value.status_code == 422
    assert ep.review(rid, ep.ReviewIn(decision="rejected", note="wrong quarter"), CAROL)["status"] == "rejected"
    assert ep.receive(rid, ep.ReceiveIn(evidence_id=10), BOB)["status"] == "received"


def test_illegal_transitions_are_409(fake):
    declare(fake)
    generate(fake)
    rid = fake.requests[0]["id"]
    with pytest.raises(HTTPException) as ei:
        ep.review(rid, ep.ReviewIn(decision="accepted"), CAROL)       # nothing received yet
    assert ei.value.status_code == 409
    with pytest.raises(HTTPException) as ei:
        ep.receive(999, ep.ReceiveIn(evidence_id=1), BOB)
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        ep.receive(rid, ep.ReceiveIn(evidence_id=404), BOB)
    assert ei.value.status_code == 404


def test_invalid_decision_is_rejected(fake):
    declare(fake)
    generate(fake)
    with pytest.raises(HTTPException) as ei:
        ep.review(fake.requests[0]["id"], ep.ReviewIn(decision="maybe"), CAROL)
    assert ei.value.status_code == 422


def test_invalid_requirement_is_422_and_audited(fake):
    with pytest.raises(HTTPException) as ei:
        ep.put_requirement(ep.RequirementIn(control_id="AC-01", requirement_key="bad key", title="t"), ALICE)
    assert ei.value.status_code == 422
    assert fake.audits[-1]["outcome"] == "failure" and fake.audits[-1]["detail"]["reason"] == "invalid_requirement"


def test_list_adds_display_status_and_filters_overdue(fake):
    declare(fake)
    generate(fake, due_date="2020-01-01")
    rows = ep.list_requests(user=ALICE)["requests"]
    assert rows[0]["status"] == "requested" and rows[0]["display_status"] == "overdue"
    assert len(ep.list_requests(status="overdue", user=ALICE)["requests"]) == 1
    assert ep.list_requests(status="accepted", user=ALICE)["requests"] == []
    assert ep.summary(user=ALICE)["overdue"] == 1
