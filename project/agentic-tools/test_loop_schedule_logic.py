from datetime import datetime, timezone

import loop_schedule_logic as L

U = lambda *a: datetime(*a, tzinfo=timezone.utc)


def test_weekly_next_monday_in_utc():
    s = {"cadence": "weekly", "weekday": 1, "run_time": "08:00", "tz": "UTC"}
    # 2026-11-27 is a Friday
    assert L.next_run(s, U(2026, 11, 27, 9)) == U(2026, 11, 30, 8)


def test_strictly_after():
    s = {"cadence": "daily", "run_time": "08:00", "tz": "UTC"}
    assert L.next_run(s, U(2026, 11, 27, 8, 0)) == U(2026, 11, 28, 8)
    assert L.next_run(s, U(2026, 11, 27, 7, 59)) == U(2026, 11, 27, 8)


def test_timezone_is_respected():
    s = {"cadence": "daily", "run_time": "08:00", "tz": "America/Chicago"}   # CST = UTC-6 in November
    assert L.next_run(s, U(2026, 11, 27, 9)) == U(2026, 11, 27, 14)


def test_dst_shift_keeps_wall_clock_time():
    s = {"cadence": "daily", "run_time": "08:00", "tz": "America/Chicago"}
    # US DST began 2026-03-08: 08:00 local is UTC-5 afterwards
    assert L.next_run(s, U(2026, 3, 9, 0)) == U(2026, 3, 9, 13)
    assert L.next_run(s, U(2026, 3, 7, 15)) == U(2026, 3, 8, 13)


def test_monthly_and_quarterly():
    m = {"cadence": "monthly", "day_of_month": 1, "run_time": "06:30", "tz": "UTC"}
    assert L.next_run(m, U(2026, 11, 27, 9)) == U(2026, 12, 1, 6, 30)
    q = {"cadence": "quarterly", "day_of_month": 1, "run_time": "08:00", "tz": "UTC"}
    assert L.next_run(q, U(2026, 11, 27)) == U(2027, 1, 1, 8)
    assert L.next_run(q, U(2027, 1, 1, 9)) == U(2027, 4, 1, 8)


def test_normalize_clamps_and_falls_back():
    n = L.normalize({"cadence": "hourly", "run_time": "99:99", "day_of_month": 31, "weekday": 9, "tz": "Mars/Phobos"})
    assert (n["cadence"], n["run_time"], n["day_of_month"], n["weekday"], n["tz"]) == ("weekly", "23:59", 28, 6, "UTC")
    assert L.normalize({"run_time": "nonsense"})["run_time"] == "08:00"


def test_catch_up_is_a_single_run_then_resumes_normally():
    s = {"cadence": "daily", "run_time": "08:00", "tz": "UTC"}
    due = U(2026, 11, 20, 8)                       # slot missed for a week
    now = U(2026, 11, 27, 12)
    assert L.is_due(due, now)
    assert L.after_a_run(s, now) == U(2026, 11, 28, 8)   # one run, next slot is tomorrow — not seven back-to-back


def test_is_due_boundaries():
    assert not L.is_due(None, U(2026, 1, 1))
    assert L.is_due(U(2026, 1, 1), U(2026, 1, 1))
    assert not L.is_due(U(2026, 1, 2), U(2026, 1, 1))
