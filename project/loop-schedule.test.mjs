// Run: node loop-schedule.test.mjs
// Date maths lives on the server (test_loop_schedule_logic.py); this covers the display helpers.
import assert from "node:assert/strict";
import { describe, fmtWhen, CADENCES, WEEKDAYS } from "./loop-schedule.js";

assert.equal(describe({ cadence: "daily", run_time: "07:30" }), "Every day at 07:30");
assert.equal(describe({ cadence: "weekly", weekday: 1, run_time: "08:00", tz: "America/Chicago" }), "Every Monday at 08:00 (America/Chicago)");
assert.equal(describe({ cadence: "monthly", day_of_month: 15, run_time: "06:00" }), "Day 15 of each month at 06:00");
assert.match(describe({ cadence: "quarterly", day_of_month: 1, run_time: "08:00" }), /Jan \/ Apr \/ Jul \/ Oct/);
assert.equal(describe(null), "Every Monday at 08:00", "defaults when nothing is saved");
assert.equal(fmtWhen(null), "—");
assert.ok(fmtWhen("2026-11-30T14:00:00Z").length > 5);
assert.equal(CADENCES.length, 4);
assert.equal(WEEKDAYS[0], "Sunday");
console.log("loop-schedule.test.mjs: all passed");
