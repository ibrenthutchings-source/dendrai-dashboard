#!/usr/bin/env python3
"""
Server-side recurring Run Loop.

What a scheduled run does — and deliberately does NOT do:

  * It COLLECTS every external input the browser loop would (full financial analysis,
    8-K events, DEF 14A, comment letters, Item 1A changes, SIC peers, risk factors,
    compliance RSS, material accounts), one source at a time through the same
    heavy-endpoint limiter the UI uses, so a scheduled run can never stack up memory.
  * It stores the raw payloads as a snapshot and raises a PENDING REVIEW (Gate 1).
    It does not score the register and it does not approve anything: when the reviewer
    opens the snapshot, the browser replays it through the same deterministic scorer a
    live run uses, so there is one scoring implementation and no parity to drift.
  * It notifies the schedule owner AND their immediate manager — in-app, plus every
    active Slack / Microsoft Teams destination subscribed to "review_ready" (the existing
    Report Delivery destinations: encrypted credentials, SSRF guard, delivery log).

Notification text never carries risk data — only that a review is ready and a link —
because chat messages are forwardable and retained.

Mirrors the other sweeps' shape (sweep_once / start_sweep) so api_server.py can run it
single-tenant or per-tenant (_multi_tenant_loop).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import auth_db
import db
import loop_schedule_logic as logic

logger = logging.getLogger("ubo.loop_schedule")

_TICK_S = float(os.environ.get("LOOP_SCHEDULE_TICK_S", "300"))                # check for due schedules every 5 min
_REMINDER_AFTER_H = float(os.environ.get("LOOP_REVIEW_REMINDER_HOURS", "24"))
_MAX_REMINDERS = int(os.environ.get("LOOP_REVIEW_MAX_REMINDERS", "3"))
_STALE_REVIEW_DAYS = int(os.environ.get("LOOP_REVIEW_EXPIRE_DAYS", "30"))
_BUSY_RETRY_S = float(os.environ.get("LOOP_SCHEDULE_BUSY_RETRY_S", "30"))
_PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

REVIEW_ARTIFACT = "review_ready"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Collection ───────────────────────────────────────────────────────────────

def default_collectors() -> Dict[str, Callable[..., Any]]:
    """The collection calls, bound lazily to api_server's own endpoint functions so a
    scheduled run exercises exactly the code path (and memory limiter) the UI does."""
    import api_server as a
    return {
        "full_analysis": lambda t, o: a.predictive_full_analysis(a.FullAnalysisRequest(
            ticker=t, industry=o.get("industry", ""), include_rss=o.get("includeRss", True),
            include_fred=o.get("includeFred", True), persona=o.get("persona", ""),
            appetite_level=o.get("appetiteLevel", ""), period_begin=o.get("periodBegin", ""),
            period_end=o.get("periodEnd", ""))),
        "material_accounts": lambda t, o: a.material_accounts(t),
        "material_forecasts": lambda t, o: a.material_accounts_forecast(t, a.MaterialAccountsForecastRequest()),
        "risk_factors": lambda t, o: a.edgar_risk_factors_endpoint(a.RiskFactorsRequest(ticker=t, max_filings=2)),
        "eight_k": lambda t, o: a.edgar_8k_events(a.TickerRequest(ticker=t)),
        "proxy": lambda t, o: a.edgar_proxy(a.RiskFactorsRequest(ticker=t, max_filings=2)),
        "comments": lambda t, o: a.edgar_comment_letters(a.TickerRequest(ticker=t)),
        "rf_changes": lambda t, o: a.edgar_risk_factor_changes(a.TickerRequest(ticker=t)),
        "peers": lambda t, o: a.edgar_peers(a.TickerRequest(ticker=t)),
        "compliance_rss": lambda t, o: a.rss_ingest(a.RssIngestRequest(
            feed_ids=list(o.get("rssEnabledFeeds") or []), ticker=t)),
    }


# Collection order. Everything after the first is best-effort: a failed source is
# recorded and the review still goes out ("partial"), because the reviewer sees the
# gaps the same way the live loop reports them. The 10-K diff runs after the proxy for
# the same memory reason the browser sequences them.
_ORDER = ["full_analysis", "material_accounts", "material_forecasts", "risk_factors", "eight_k",
          "proxy", "comments", "rf_changes", "peers", "compliance_rss"]
# Sources the options can switch off.
_NEEDS_EDGAR = {"risk_factors", "eight_k", "proxy", "comments", "rf_changes", "peers"}


def _is_busy(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 503


def _call(name: str, fn: Callable[[], Any], sleep: Callable[[float], None]) -> Any:
    """One collection call; retried once if the heavy-endpoint limiter said 'busy' (a UI user
    is mid-run) — a scheduled job should wait its turn, not fail because of it."""
    try:
        return fn()
    except Exception as exc:
        if not _is_busy(exc):
            raise
        logger.info("scheduled run: %s busy — retrying in %.0fs", name, _BUSY_RETRY_S)
        sleep(_BUSY_RETRY_S)
        return fn()


def collect(ticker: str, options: dict, collectors: Optional[Dict[str, Callable]] = None,
            sleep: Callable[[float], None] = None) -> Tuple[Dict[str, Any], List[dict]]:
    """Collect every payload for a ticker. Returns (payloads, errors); raises only if the
    FIRST (financial analysis) step fails, since nothing can be assessed without it."""
    import time
    sleep = sleep or time.sleep
    collectors = collectors or default_collectors()
    options = options or {}
    private = bool(options.get("isPrivate")) or ticker.upper().startswith("PVT-")
    signals = set(options.get("signals") or ["edgar", "peers", "industry", "internal", "fred", "incidents"])
    payloads: Dict[str, Any] = {}
    errors: List[dict] = []
    skip: set = set()
    for name in _ORDER:
        if name in skip:
            continue
        if name in _NEEDS_EDGAR and private:
            continue
        if name == "risk_factors" and "edgar" not in signals:
            continue
        if name == "compliance_rss" and ("industry" not in signals or not options.get("rssEnabledFeeds")):
            continue
        try:
            payloads[name] = _call(name, lambda n=name: collectors[n](ticker, options), sleep)
        except Exception as exc:
            msg = getattr(exc, "detail", None) or str(exc)
            if name == "full_analysis":
                raise RuntimeError(f"financial analysis failed: {msg}") from exc
            errors.append({"source": name, "message": str(msg)[:300]})
            logger.warning("scheduled run %s: %s failed: %s", ticker, name, msg)
        # forecasts only make sense if detection found at least one material account
        if name == "material_accounts" and not any(
                a.get("is_material") for a in (payloads.get("material_accounts") or {}).get("accounts", [])):
            skip.add("material_forecasts")
    return payloads, errors


# ── Recipients and notifications ──────────────────────────────────────────────

def recipients_for(owner_id: Optional[int]) -> List[dict]:
    """The schedule owner (who runs the loop) and their immediate manager, de-duplicated.
    A manager who is also the owner, or an unset owner/manager, simply yields fewer people."""
    out: List[dict] = []
    seen = set()
    if owner_id:
        owner = auth_db.get_user_by_id(owner_id)
        if owner and owner.get("id") not in seen:
            out.append({"id": owner["id"], "name": owner.get("display_name") or owner.get("username"), "role": "owner"})
            seen.add(owner["id"])
        mgr = auth_db.get_manager_of(owner_id)
        if mgr and mgr["id"] not in seen:
            out.append({"id": mgr["id"], "name": mgr.get("display_name") or mgr.get("username"), "role": "manager"})
            seen.add(mgr["id"])
    return out


def _link(review_id: int) -> str:
    # The app has no URL router; ?review=<id> is read on load and opens that snapshot.
    return f"{_PUBLIC_URL}/?review={review_id}" if _PUBLIC_URL else f"/?review={review_id}"


def _message(kind: str, ticker: str, partial: bool, reminders: int = 0) -> Tuple[str, str]:
    if kind == "schedule_failed":
        return (f"Scheduled run for {ticker} failed", "The scheduled risk assessment could not collect its data. Open the schedule for details.")
    if kind == "review_reminder":
        return (f"Reminder: {ticker} risk assessment still awaiting review",
                f"The Gate 1 review for {ticker} has been waiting (reminder {reminders}).")
    body = f"The scheduled {ticker} risk assessment has finished and is waiting for Gate 1 review."
    if partial:
        body += " Some sources could not be collected — the gaps are listed in the review."
    return (f"{ticker} risk assessment ready for review", body)


def _send_webhooks(title: str, body: str, link: str, ticker: str, review_id: Optional[int], recipients: List[dict]) -> List[dict]:
    """One message per event to every active destination subscribed to review_ready. Reuses the
    Report Delivery dispatcher, so SSRF rules, credentials and the delivery log all apply."""
    import report_delivery_endpoints as rd
    results = []
    for dest in db.list_active_destinations_for(REVIEW_ARTIFACT):
        payload = {"ticker": ticker, "review_id": review_id, "title": title, "text": body, "link": link,
                   "recipients": [r["name"] for r in recipients]}
        res = rd._dispatch(url=dest["url"], http_method=dest["http_method"], auth_type=dest["auth_type"],
                           credentials=dest.get("credentials"), extra_headers=dest.get("headers"),
                           payload_format=dest["payload_format"], artifact_type=REVIEW_ARTIFACT, payload=payload)
        try:
            db.record_report_delivery(
                destination_id=dest["id"], destination_name=dest["display_name"], artifact_type=REVIEW_ARTIFACT,
                status=res["status"], url=dest["url"], run_id=None, ticker=ticker,
                http_status=res["http_status"], error=res["error"], response_excerpt=res["response_excerpt"],
                content_sha256=res["content_sha256"], content_bytes=res["content_bytes"],
                duration_ms=res["duration_ms"], sent_by="system:loop-schedule")
        except Exception as exc:  # the log must never mask the send result
            logger.warning("delivery log write failed: %s", exc)
        results.append({"destination": dest["display_name"], "status": res["status"], "error": res.get("error")})
    return results


def notify(kind: str, *, ticker: str, review_id: Optional[int], owner_id: Optional[int],
           partial: bool = False, reminders: int = 0) -> dict:
    """Notify the owner and manager in-app and fire the Slack/Teams webhooks once. Never raises."""
    people = recipients_for(owner_id)
    title, body = _message(kind, ticker, partial, reminders)
    link = _link(review_id) if review_id else "/"
    in_app = 0
    for p in people:
        try:
            if db.insert_user_notification(p["id"], kind, title, body, link, review_id, p["role"]):
                in_app += 1
        except Exception as exc:
            logger.warning("in-app notification failed for user %s: %s", p["id"], exc)
    hooks: List[dict] = []
    try:
        hooks = _send_webhooks(title, body, link, ticker, review_id, people)
    except Exception as exc:
        logger.warning("webhook notification failed: %s", exc)
    return {"in_app": in_app, "recipients": [p["name"] for p in people], "webhooks": hooks}


# ── Running one schedule ──────────────────────────────────────────────────────

def run_schedule(schedule: dict, *, collectors: Optional[Dict[str, Callable]] = None,
                 sleep: Optional[Callable[[float], None]] = None, now: Optional[datetime] = None) -> dict:
    """Run one claimed schedule end to end. Always releases the claim and advances next_run_at,
    even on failure, so a broken ticker cannot wedge the sweep or retry every tick."""
    ticker = schedule["ticker"]
    now = now or _utcnow()
    next_at = logic.after_a_run(schedule, now) if schedule.get("enabled", True) else None
    owner_id = schedule.get("owner_id")
    try:
        payloads, errors = collect(ticker, schedule.get("options") or {}, collectors, sleep)
    except Exception as exc:
        logger.error("scheduled run %s failed: %s", ticker, exc)
        db.finish_loop_schedule(schedule["id"], status="failed", error=str(exc), review_id=None, next_run_at=next_at)
        notify("schedule_failed", ticker=ticker, review_id=None, owner_id=owner_id)
        return {"status": "failed", "error": str(exc)}

    fa = payloads.get("full_analysis") or {}
    snapshot = {"ticker": ticker, "collected_at": now.isoformat(), "options": schedule.get("options") or {},
                "payloads": payloads, "errors": errors}
    mgr = auth_db.get_manager_of(owner_id) if owner_id else None
    review_id = db.create_scheduled_review(
        schedule_id=schedule["id"], ticker=ticker, run_id=fa.get("_db_id"), snapshot=snapshot,
        collection_errors=errors, owner_id=owner_id, manager_id=mgr["id"] if mgr else None)
    status = "partial" if errors else "ok"
    db.finish_loop_schedule(schedule["id"], status=status,
                            error="; ".join(f"{e['source']}: {e['message']}" for e in errors) or None,
                            review_id=review_id, next_run_at=next_at)
    sent = notify("review_ready", ticker=ticker, review_id=review_id, owner_id=owner_id, partial=bool(errors))
    logger.info("scheduled run %s: %s, review %s, notified %s", ticker, status, review_id, sent["recipients"])
    return {"status": status, "review_id": review_id, "errors": errors, "notified": sent}


def send_reminders() -> int:
    """Remind owner + manager of a review that has waited, at most _MAX_REMINDERS times."""
    n = 0
    for r in db.list_reviews_needing_reminder(_REMINDER_AFTER_H, _MAX_REMINDERS):
        notify("review_reminder", ticker=r["ticker"], review_id=r["id"], owner_id=r.get("owner_id"),
               reminders=r["reminders_sent"] + 1)
        db.mark_review_reminded(r["id"])
        n += 1
    return n


# ── Sweep ─────────────────────────────────────────────────────────────────────

async def sweep_once() -> int:
    """One pass: run due schedules (one at a time, each claimed atomically), send reminders,
    expire abandoned reviews. Returns the number of schedules run. Collection is blocking
    and memory-heavy, so it runs in a worker thread, never concurrently with itself."""
    ran = 0
    for sched in await asyncio.to_thread(db.list_due_loop_schedules, 3):
        if not await asyncio.to_thread(db.claim_loop_schedule, sched["id"]):
            continue
        await asyncio.to_thread(run_schedule, sched)
        ran += 1
    await asyncio.to_thread(send_reminders)
    await asyncio.to_thread(db.purge_stale_scheduled_reviews, _STALE_REVIEW_DAYS)
    return ran


async def start_sweep() -> None:
    logger.info("Loop schedule sweep started (tick=%.0fs)", _TICK_S)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("Loop schedule sweep stopped")
            break
        except Exception as exc:
            logger.warning("Loop schedule sweep error: %s", exc)
