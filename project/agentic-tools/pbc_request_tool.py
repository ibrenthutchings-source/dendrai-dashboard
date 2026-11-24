"""
Request-as-Code for PBC ("prepared by client") document requests.

Each control can declare what evidence it needs (an *evidence requirement*:
artifact, owner, source, period rule, signature and freshness rules). From an
engagement's scope, generate_requests() turns those declarations into the
actual request list, instead of someone building a spreadsheet by hand. When
evidence arrives, check_evidence() checks it against the requirement it was
requested under, using the same deterministic rules evidence_quality_tool.py
applies elsewhere. Humans still review and accept.

Pure module (no DB, no HTTP), same discipline as evidence_quality_tool.py and
sample_selection_tool.py: reproducible rules in one place, persistence and
workflow in pbc_request_endpoints.py.

Reperformance: a request freezes a copy of its requirement at generation time
(`requirement_snapshot`), so editing a requirement later never changes what a
past request asked for, and check_evidence() records exactly which checks ran
and what they found. A trained reviewer who was not involved can read a
request and see: what was asked, of whom, for which period, by when, what came
back, what the automatic checks said, and who accepted or rejected it and why.

What this does NOT do yet: automatic collection from a connected system (a
requirement's `source_system` is recorded but not acted on) and automatic
reminders. Those are the next increments.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Optional

CHECKS_VERSION = "1"

STATUSES = ("requested", "received", "accepted", "rejected")
# from -> allowed next states. rejected -> received is resubmission.
_TRANSITIONS = {
    "requested": {"received"},
    "received": {"accepted", "rejected"},
    "rejected": {"received"},
    "accepted": set(),
}
FREQUENCIES = ("once", "monthly", "quarterly", "annual")


class RequestError(ValueError):
    """Invalid input or illegal state change. Safe to show to the caller."""


def _as_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise RequestError(f"invalid date {v!r}")


def normalise_requirement(req: dict) -> dict:
    """Validate and normalise a requirement declaration."""
    control_id = str(req.get("control_id", "")).strip()
    key = str(req.get("requirement_key", "")).strip().lower()
    title = str(req.get("title", "")).strip()
    if not control_id:
        raise RequestError("control_id is required")
    if not key or not all(ch.isalnum() or ch in "_-" for ch in key) or len(key) > 64:
        raise RequestError("requirement_key must be 1-64 characters: letters, digits, _ or -")
    if not title:
        raise RequestError("title is required")
    freq = str(req.get("frequency", "once")).lower()
    if freq not in FREQUENCIES:
        raise RequestError(f"frequency must be one of {list(FREQUENCIES)}")
    max_age = req.get("max_age_days", 90)
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age < 0:
        raise RequestError("max_age_days must be a non-negative integer")
    return {
        "control_id": control_id,
        "requirement_key": key,
        "title": title,
        "description": (req.get("description") or "").strip() or None,
        "artifact_type": (req.get("artifact_type") or "").strip() or None,
        "source_system": (req.get("source_system") or "manual").strip().lower(),
        "frequency": freq,
        "requires_signature": bool(req.get("requires_signature", False)),
        "max_age_days": max_age,
        "owner": (req.get("owner") or "").strip() or None,
        "acceptance_notes": (req.get("acceptance_notes") or "").strip() or None,
    }


def snapshot(requirement: dict) -> dict:
    """The frozen copy stored on each request (stable key order, no volatile fields)."""
    keys = ("control_id", "requirement_key", "title", "description", "artifact_type", "source_system",
            "frequency", "requires_signature", "max_age_days", "owner", "acceptance_notes")
    return {k: requirement.get(k) for k in keys}


def snapshot_hash(snap: dict) -> str:
    return hashlib.sha256(json.dumps(snap, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def generate_requests(requirements: list[dict], engagement: str, period_start: Any, period_end: Any,
                      due_date: Any, control_ids: Optional[list[str]] = None) -> list[dict]:
    """One request per active requirement of each in-scope control. Deterministic:
    same inputs, same list, same order. `control_ids` None means every control that
    has a requirement."""
    engagement = (engagement or "").strip()
    if not engagement:
        raise RequestError("engagement is required")
    ps, pe, due = _as_date(period_start), _as_date(period_end), _as_date(due_date)
    if not ps or not pe or not due:
        raise RequestError("period_start, period_end and due_date are required")
    if pe < ps:
        raise RequestError("period_end is before period_start")
    scope = set(control_ids) if control_ids else None

    out = []
    for r in sorted(requirements, key=lambda x: (x["control_id"], x["requirement_key"])):
        if scope is not None and r["control_id"] not in scope:
            continue
        snap = snapshot(r)
        out.append({
            "engagement": engagement,
            "control_id": r["control_id"],
            "requirement_key": r["requirement_key"],
            "title": r["title"],
            "period_start": ps.isoformat(),
            "period_end": pe.isoformat(),
            "due_date": due.isoformat(),
            "owner": r.get("owner"),
            "requirement_snapshot": snap,
            "requirement_hash": snapshot_hash(snap),
        })
    return out


def derive_status(status: str, due_date: Any, today: Optional[date] = None) -> str:
    """Stored status, except an unanswered request past its due date reads 'overdue'."""
    today = today or date.today()
    due = _as_date(due_date)
    if status in ("requested", "rejected") and due and due < today:
        return "overdue"
    return status


def check_transition(current: str, new: str) -> None:
    if new not in _TRANSITIONS.get(current, set()):
        raise RequestError(f"cannot move a request from {current!r} to {new!r}")


def check_review_independence(reviewer: str, submitter: Optional[str], evidence_logged_by: Optional[str]) -> None:
    """The person who supplied or logged the evidence cannot also accept or reject it."""
    r = (reviewer or "").strip().lower()
    for who in (submitter, evidence_logged_by):
        if who and r == who.strip().lower():
            raise RequestError("the reviewer must be different from the person who submitted the evidence")


def check_evidence(request: dict, evidence: dict, today: Optional[date] = None) -> list[dict]:
    """Deterministic checks of an evidence item against the request it answers.
    Returns the findings that fired (empty = nothing wrong by these rules, which
    is not proof the evidence is good). Each finding: {code, severity, message}."""
    today = today or date.today()
    snap = request.get("requirement_snapshot") or {}
    findings: list[dict] = []

    if evidence.get("control_id") != request.get("control_id"):
        findings.append({"code": "WRONG_CONTROL", "severity": "HIGH",
                         "message": f"Evidence is logged against {evidence.get('control_id')!r}, "
                                    f"but the request is for {request.get('control_id')!r}."})

    rs, re_ = _as_date(request.get("period_start")), _as_date(request.get("period_end"))
    es, ee = _as_date(evidence.get("period_start")), _as_date(evidence.get("period_end"))
    if not es or not ee:
        findings.append({"code": "PERIOD_MISSING", "severity": "HIGH",
                         "message": "Evidence has no coverage period, so it cannot be tied to the requested period."})
    elif rs and re_ and (es > rs or ee < re_):
        findings.append({"code": "PERIOD_NOT_COVERED", "severity": "HIGH",
                         "message": f"Evidence covers {es} to {ee}; the request is for {rs} to {re_}."})

    if snap.get("requires_signature") and not evidence.get("has_signature"):
        findings.append({"code": "SIGNATURE_REQUIRED", "severity": "MEDIUM",
                         "message": "This requirement needs signed evidence, and it is not signed."})

    collected = _as_date(evidence.get("collected_date"))
    max_age = snap.get("max_age_days")
    if collected is None:
        findings.append({"code": "COLLECTED_DATE_MISSING", "severity": "MEDIUM",
                         "message": "Evidence has no collection date, so its freshness cannot be checked."})
    elif max_age is not None and (today - collected).days > max_age:
        findings.append({"code": "STALE", "severity": "MEDIUM",
                         "message": f"Evidence was collected {(today - collected).days} days ago; "
                                    f"the limit for this requirement is {max_age}."})

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))
    return findings


# Reminder schedule, in days relative to the due date. A request gets at most one
# reminder of each kind, and only the CURRENT kind is sent: if the sweep was down
# and a request is already 10 days overdue, it gets "escalation", not a backlog of
# the earlier kinds.
REMIND_BEFORE_DAYS = 3
ESCALATE_AFTER_DAYS = 7
REMINDER_KINDS = ("due_soon", "due_today", "overdue", "escalation")


def reminder_kind(status: str, due_date: Any, already_sent: set, today: Optional[date] = None) -> Optional[str]:
    """Which reminder (if any) an unanswered request should get today."""
    if status not in ("requested", "rejected"):
        return None
    today = today or date.today()
    due = _as_date(due_date)
    if due is None:
        return None
    delta = (due - today).days
    if delta > REMIND_BEFORE_DAYS:
        return None
    if delta > 0:
        kind = "due_soon"
    elif delta == 0:
        kind = "due_today"
    elif -delta < ESCALATE_AFTER_DAYS:
        kind = "overdue"
    else:
        kind = "escalation"
    return None if kind in already_sent else kind


def summarise(requests: list[dict], today: Optional[date] = None) -> dict:
    """Status board counts, with overdue broken out."""
    counts = {s: 0 for s in (*STATUSES, "overdue")}
    for r in requests:
        counts[derive_status(r["status"], r.get("due_date"), today)] += 1
    total = len(requests)
    counts["total"] = total
    counts["percent_accepted"] = round(100 * counts["accepted"] / total, 1) if total else 0.0
    return counts
