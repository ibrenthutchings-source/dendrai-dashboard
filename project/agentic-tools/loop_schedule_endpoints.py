#!/usr/bin/env python3
"""
loop_schedule_endpoints.py — server-side recurring Run Loop: schedules, scheduled-run
reviews and in-app notifications.

    GET    /loop-schedule/{ticker}                 The ticker's schedule (or null) + next run
    PUT    /loop-schedule/{ticker}                 Create / update it (caller becomes the owner)
    DELETE /loop-schedule/{ticker}                 Remove it
    POST   /loop-schedule/{ticker}/run-now         Collect now (same path the sweep uses)
    GET    /loop-schedule/reviews/mine             Pending reviews addressed to me (owner or manager)
    GET    /loop-schedule/reviews/{id}/snapshot    The collected payloads, replayed by the browser
    POST   /loop-schedule/reviews/{id}/resolve     Close a review (approved | overridden | dismissed)
    GET    /notifications                          My notifications (+ unread count)
    POST   /notifications/read                     Mark some / all read

Identity always comes from the session. Only the schedule owner, their manager or an admin
may open or resolve a review — the snapshot is the entity's data as of the run.
"""
from __future__ import annotations

import contextvars
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import db
import loop_schedule_logic as logic
import loop_schedule_runner as runner
from auth_endpoints import get_current_user, require_screen_permission

logger = logging.getLogger("ubo.loop_schedule")

router = APIRouter(tags=["Loop Schedule"])
_SCREEN = "pipeline"


class ScheduleBody(BaseModel):
    enabled: bool = True
    cadence: str = "weekly"
    run_time: str = "08:00"
    weekday: int = 1
    day_of_month: int = 1
    tz: str = "UTC"
    # Run options captured from the Pipeline config (industry, signal sources, RSS feeds, persona …).
    options: Dict[str, Any] = {}


class ResolveBody(BaseModel):
    resolution: str   # approved | overridden | dismissed


class ReadBody(BaseModel):
    ids: Optional[List[int]] = None


def _require_db() -> None:
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")


def _display(user: dict) -> str:
    return user.get("display_name") or user.get("username") or "user"


def _shape(s: Optional[dict]) -> Optional[dict]:
    if not s:
        return None
    return {k: s.get(k) for k in ("ticker", "enabled", "cadence", "run_time", "weekday", "day_of_month", "tz",
                                  "options", "owner_name", "next_run_at", "last_run_at", "last_status",
                                  "last_error", "last_review_id", "running_since")}


def _may_see(review: dict, user: dict) -> bool:
    return user.get("role") == "admin" or user.get("id") in (review.get("owner_id"), review.get("manager_id"))


# NOTE: the specific /reviews/... paths are declared BEFORE /{ticker} so they are not captured by it.

@router.get("/loop-schedule/reviews/mine")
def my_reviews(all_pending: bool = Query(default=False), user: dict = Depends(require_screen_permission(_SCREEN))):
    _require_db()
    return {"reviews": db.list_scheduled_reviews(user["id"], pending_only=True,
                                                 all_users=all_pending and user.get("role") == "admin")}


@router.get("/loop-schedule/reviews/{review_id}/snapshot")
def review_snapshot(review_id: int, user: dict = Depends(require_screen_permission(_SCREEN))):
    _require_db()
    review = db.get_scheduled_review(review_id, include_snapshot=True)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if not _may_see(review, user):
        raise HTTPException(status_code=403, detail="This review is assigned to someone else")
    if review["status"] != "pending" or not review.get("snapshot"):
        raise HTTPException(status_code=410, detail=f"This review is {review['status']} — its snapshot is no longer kept")
    return review


@router.post("/loop-schedule/reviews/{review_id}/resolve")
def resolve_review(review_id: int, body: ResolveBody, user: dict = Depends(require_screen_permission(_SCREEN, edit=True))):
    _require_db()
    if body.resolution not in ("approved", "overridden", "dismissed"):
        raise HTTPException(status_code=400, detail="resolution must be approved, overridden or dismissed")
    review = db.get_scheduled_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if not _may_see(review, user):
        raise HTTPException(status_code=403, detail="This review is assigned to someone else")
    return {"resolved": db.resolve_scheduled_review(review_id, user["id"], body.resolution)}


@router.get("/loop-schedule/{ticker}")
def get_schedule(ticker: str, user: dict = Depends(require_screen_permission(_SCREEN))):
    _require_db()
    return {"schedule": _shape(db.get_loop_schedule(ticker))}


@router.put("/loop-schedule/{ticker}")
def put_schedule(ticker: str, body: ScheduleBody, user: dict = Depends(require_screen_permission(_SCREEN, edit=True))):
    _require_db()
    n = logic.normalize(body.model_dump())
    nxt = logic.next_run(n, datetime.now(timezone.utc)) if body.enabled else None
    saved = db.upsert_loop_schedule(
        ticker, enabled=body.enabled, cadence=n["cadence"], run_time=n["run_time"], weekday=n["weekday"],
        day_of_month=n["day_of_month"], tz=n["tz"], options=body.options, owner_id=user["id"],
        owner_name=_display(user), next_run_at=nxt)
    if not saved:
        raise HTTPException(status_code=500, detail="Could not save the schedule")
    return {"schedule": _shape(saved)}


@router.delete("/loop-schedule/{ticker}")
def delete_schedule(ticker: str, user: dict = Depends(require_screen_permission(_SCREEN, edit=True))):
    _require_db()
    return {"deleted": db.delete_loop_schedule(ticker)}


@router.post("/loop-schedule/{ticker}/run-now")
def run_now(ticker: str, user: dict = Depends(require_screen_permission(_SCREEN, edit=True))):
    """Start a collection immediately. Returns as soon as it is claimed — a collection takes
    minutes; the owner and manager are notified when it finishes, exactly like a timed run."""
    _require_db()
    sched = db.get_loop_schedule(ticker)
    if not sched:
        raise HTTPException(status_code=404, detail="No schedule for this ticker — save one first")
    if not db.claim_loop_schedule(sched["id"]):
        raise HTTPException(status_code=409, detail="A run for this ticker is already in progress")

    def _work():
        try:
            runner.run_schedule(sched)
        except Exception:
            logger.exception("run-now failed for %s", ticker)
            db.finish_loop_schedule(sched["id"], status="failed", error="unexpected error", review_id=None,
                                    next_run_at=sched.get("next_run_at"))
    # copy_context(): the tenant database / secrets are bound in contextvars for THIS request;
    # a bare thread would lose them (and in multi-tenant mode have no database at all).
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(_work,), name=f"loop-run-now-{ticker}", daemon=True).start()
    return {"started": True}


@router.get("/notifications")
def my_notifications(unread_only: bool = Query(default=False), limit: int = Query(default=30, le=100),
                     user: dict = Depends(get_current_user)):
    if not db.is_available():
        return {"notifications": [], "unread": 0}
    return {"notifications": db.list_user_notifications(user["id"], unread_only, limit),
            "unread": db.count_unread_notifications(user["id"])}


@router.post("/notifications/read")
def read_notifications(body: ReadBody, user: dict = Depends(get_current_user)):
    _require_db()
    return {"marked": db.mark_notifications_read(user["id"], body.ids)}
