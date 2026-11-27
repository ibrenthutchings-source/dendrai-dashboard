// Run: node loop-schedule.test.mjs
import assert from "node:assert/strict";
import { nextRun, dueState, normalize, describe } from "./loop-schedule.js";

const L = (y, mo, d, h = 0, mi = 0) => new Date(y, mo - 1, d, h, mi, 0, 0).getTime();   // local time
const S = (o) => ({ enabled: true, ...o });

// ── nextRun ──
assert.equal(nextRun(S({ cadence: "daily", time: "08:00" }), L(2026, 11, 27, 7, 0)), L(2026, 11, 27, 8, 0));
assert.equal(nextRun(S({ cadence: "daily", time: "08:00" }), L(2026, 11, 27, 8, 0)), L(2026, 11, 28, 8, 0), "strictly after");
// 2026-11-27 is a Friday; next Monday is the 30th
assert.equal(nextRun(S({ cadence: "weekly", weekday: 1, time: "08:00" }), L(2026, 11, 27, 9, 0)), L(2026, 11, 30, 8, 0));
assert.equal(nextRun(S({ cadence: "monthly", day: 1, time: "06:30" }), L(2026, 11, 27, 9, 0)), L(2026, 12, 1, 6, 30));
assert.equal(nextRun(S({ cadence: "quarterly", day: 1, time: "08:00" }), L(2026, 11, 27)), L(2027, 1, 1, 8, 0));
assert.equal(nextRun(S({ cadence: "quarterly", day: 1, time: "08:00" }), L(2027, 1, 1, 9, 0)), L(2027, 4, 1, 8, 0));
// "day 31" is clamped so short months are never skipped
assert.equal(normalize({ day: 31 }).day, 28);
assert.equal(normalize({ time: "99:99" }).time, "23:59");
assert.equal(normalize({ time: "garbage" }).time, "08:00");
assert.equal(normalize({ cadence: "hourly" }).cadence, "weekly");

// ── dueState ──
const base = S({ cadence: "daily", time: "08:00", lastRunAt: L(2026, 11, 26, 8, 0) });
assert.equal(dueState(base, L(2026, 11, 27, 7, 59)).due, false, "not yet");
assert.equal(dueState(base, L(2026, 11, 27, 8, 0), L(2026, 11, 27, 7, 0)).due, true, "slot reached while app open");
assert.equal(dueState(base, L(2026, 11, 27, 8, 0), L(2026, 11, 27, 7, 0)).reason, "on-time");
assert.equal(dueState(S({ cadence: "daily", time: "08:00" }), L(2026, 11, 27, 7, 0)).due, false, "off");
assert.equal(dueState({ ...base, enabled: false }, L(2026, 12, 30)).due, false);

// a fresh schedule never fires the instant it is switched on
const fresh = S({ cadence: "daily", time: "08:00", enabledAt: L(2026, 11, 27, 9, 0) });
assert.equal(dueState(fresh, L(2026, 11, 27, 9, 1)).due, false);
assert.equal(dueState(fresh, L(2026, 11, 28, 8, 0), L(2026, 11, 27, 9, 0)).due, true);

// several slots missed while closed -> exactly one catch-up run, flagged "missed"
const closed = dueState(base, L(2026, 12, 5, 12, 0), L(2026, 12, 5, 11, 0));
assert.equal(closed.due, true); assert.equal(closed.reason, "missed");
// ...unless the user opted out: the slot is skipped and the next one is computed from now
const skip = dueState({ ...base, runIfMissed: false }, L(2026, 12, 5, 12, 0), L(2026, 12, 5, 11, 0));
assert.equal(skip.due, false); assert.equal(skip.reason, "missed-skipped");
assert.equal(skip.next, L(2026, 12, 6, 8, 0));
// after the skip is recorded it stays quiet until that next slot
const afterSkip = { ...base, runIfMissed: false, skippedAt: L(2026, 12, 5, 12, 0) };
assert.equal(dueState(afterSkip, L(2026, 12, 5, 12, 1), L(2026, 12, 5, 11, 0)).due, false);
// after a run, the anchor moves forward: no immediate re-fire
const ran = { ...base, lastRunAt: L(2026, 12, 5, 12, 0) };
assert.equal(dueState(ran, L(2026, 12, 5, 12, 1), L(2026, 12, 5, 11, 0)).due, false);

assert.match(describe(S({ cadence: "weekly", weekday: 1, time: "08:00" })), /Every Monday at 08:00/);
console.log("loop-schedule.test.mjs: all passed");
