"""
Schedule arithmetic for the server-side recurring Run Loop. Pure functions, no DB and no
clock reads (callers pass `now`), so every rule is unit-testable. Mirrors the browser's
loop-schedule.js, but works in the schedule's own IANA timezone because the server runs in UTC.

A schedule is a dict with: cadence (daily|weekly|monthly|quarterly), run_time "HH:MM",
weekday 0-6 (Sunday=0, weekly), day_of_month 1-28 (monthly/quarterly), tz (IANA name).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CADENCES = ("daily", "weekly", "monthly", "quarterly")
_QUARTER_MONTHS = (1, 4, 7, 10)


def normalize(raw: dict) -> dict:
    """Clamp a user-supplied schedule into valid ranges; unknown values fall back to defaults.
    Day-of-month is capped at 28 so 'the 31st' never silently skips a short month."""
    raw = raw or {}
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(raw.get("run_time") or raw.get("time") or ""))
    hh, mm = (min(23, int(m.group(1))), min(59, int(m.group(2)))) if m else (8, 0)
    cadence = raw.get("cadence") if raw.get("cadence") in CADENCES else "weekly"
    try:
        weekday = min(6, max(0, int(raw.get("weekday", 1))))
    except (TypeError, ValueError):
        weekday = 1
    try:
        dom = min(28, max(1, int(raw.get("day_of_month", raw.get("day", 1)))))
    except (TypeError, ValueError):
        dom = 1
    tz = str(raw.get("tz") or "UTC")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        tz = "UTC"
    return {"cadence": cadence, "run_time": f"{hh:02d}:{mm:02d}", "weekday": weekday,
            "day_of_month": dom, "tz": tz, "enabled": bool(raw.get("enabled", True))}


def next_run(schedule: dict, after: datetime) -> Optional[datetime]:
    """First scheduled slot strictly after `after` (tz-aware), as a UTC datetime."""
    s = normalize(schedule)
    zone = ZoneInfo(s["tz"])
    hh, mm = (int(x) for x in s["run_time"].split(":"))
    local_after = after.astimezone(zone)
    for i in range(0, 400):
        day = (local_after + timedelta(days=i)).date()
        # Build the slot from wall-clock parts in the zone (handles DST: a nonexistent
        # 02:30 resolves forward, an ambiguous 01:30 takes the first occurrence).
        slot = datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone)
        if slot.astimezone(timezone.utc) <= after.astimezone(timezone.utc):
            continue
        weekday_sun0 = (day.weekday() + 1) % 7    # Python Monday=0 -> Sunday=0
        c = s["cadence"]
        if (c == "daily"
                or (c == "weekly" and weekday_sun0 == s["weekday"])
                or (c == "monthly" and day.day == s["day_of_month"])
                or (c == "quarterly" and day.day == s["day_of_month"] and day.month in _QUARTER_MONTHS)):
            return slot.astimezone(timezone.utc)
    return None


def is_due(next_run_at: Optional[datetime], now: datetime) -> bool:
    return next_run_at is not None and next_run_at <= now


def after_a_run(schedule: dict, now: datetime) -> Optional[datetime]:
    """The next slot once a run has just happened at `now`. Always computed from NOW (not
    from the slot that was due) so several slots missed while the server was down produce
    exactly one catch-up run and then resume the normal rhythm."""
    return next_run(schedule, now)
