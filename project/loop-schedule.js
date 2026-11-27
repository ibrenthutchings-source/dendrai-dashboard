// In-app recurring schedule for Run Loop (replaces copying a cron command).
// Pure date maths, no React and no storage, so it is unit-testable
// (loop-schedule.test.mjs). All times are the user's LOCAL time.
//
// schedule = { enabled, cadence: "daily"|"weekly"|"monthly"|"quarterly",
//              time: "HH:MM", weekday: 0-6 (Sun=0, weekly only),
//              day: 1-28 (monthly / quarterly), runIfMissed: bool,
//              lastRunAt: ms | null, enabledAt: ms | null, skippedAt: ms | null }

export const CADENCES = [
  { id: "daily",     label: "Daily" },
  { id: "weekly",    label: "Weekly" },
  { id: "monthly",   label: "Monthly" },
  { id: "quarterly", label: "Quarterly (Jan / Apr / Jul / Oct)" },
];

export const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export const DEFAULT_SCHEDULE = {
  enabled: false, cadence: "weekly", time: "08:00", weekday: 1, day: 1, runIfMissed: true, lastRunAt: null,
};

export function normalize(s) {
  const d = { ...DEFAULT_SCHEDULE, ...(s || {}) };
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(d.time || ""));
  const hh = m ? Math.min(23, +m[1]) : 8, mm = m ? Math.min(59, +m[2]) : 0;
  d.time = `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
  d.weekday = Math.min(6, Math.max(0, parseInt(d.weekday, 10) || 0));
  // 1-28 keeps "the 31st" from silently skipping short months.
  d.day = Math.min(28, Math.max(1, parseInt(d.day, 10) || 1));
  if (!CADENCES.some(c => c.id === d.cadence)) d.cadence = "weekly";
  d.enabled = !!d.enabled;
  d.runIfMissed = d.runIfMissed !== false;
  return d;
}

const _at = (date, time) => {
  const [h, m] = time.split(":").map(Number);
  const x = new Date(date); x.setHours(h, m, 0, 0); return x;
};

// The first scheduled slot STRICTLY after `after` (ms). Walks forward from `after`
// so the answer never depends on the current clock.
export function nextRun(schedule, after = Date.now()) {
  const s = normalize(schedule);
  const start = new Date(after);
  for (let i = 0; i < 400; i++) {
    const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    const slot = _at(day, s.time);
    if (slot.getTime() <= after) continue;
    if (s.cadence === "daily") return slot.getTime();
    if (s.cadence === "weekly" && day.getDay() === s.weekday) return slot.getTime();
    if (s.cadence === "monthly" && day.getDate() === s.day) return slot.getTime();
    if (s.cadence === "quarterly" && day.getDate() === s.day && [0, 3, 6, 9].includes(day.getMonth())) return slot.getTime();
  }
  return null;
}

// What to do at `now`. A run is due when a slot has passed since the last run. With no
// prior run, a fresh schedule waits for its first slot (it never fires on the instant
// it was switched on). A slot missed while the app was closed fires once (not once per
// missed slot) when runIfMissed is set, otherwise it is skipped.
export function dueState(schedule, now = Date.now(), appOpenedAt = now) {
  const s = normalize(schedule);
  if (!s.enabled) return { due: false, reason: "off", next: null };
  // Latest of: last run, when it was switched on, when a missed slot was skipped.
  const anchor = Math.max(s.lastRunAt || 0, s.enabledAt || 0, s.skippedAt || 0) || now;
  const slot = nextRun(s, anchor);
  if (slot == null) return { due: false, reason: "no-slot", next: null };
  if (now < slot) return { due: false, reason: "waiting", next: slot };
  const missed = slot < appOpenedAt;   // the slot passed before this session started
  if (missed && !s.runIfMissed) return { due: false, reason: "missed-skipped", next: nextRun(s, now), skippedAt: now };
  return { due: true, reason: missed ? "missed" : "on-time", next: slot, slot };
}

export function describe(schedule) {
  const s = normalize(schedule);
  const t = s.time;
  if (s.cadence === "daily") return `Every day at ${t}`;
  if (s.cadence === "weekly") return `Every ${WEEKDAYS[s.weekday]} at ${t}`;
  if (s.cadence === "monthly") return `Day ${s.day} of each month at ${t}`;
  return `Day ${s.day} of Jan / Apr / Jul / Oct at ${t}`;
}

export const storageKey = (ticker) => `dendrai.loopSchedule:${(ticker || "").toUpperCase()}`;

if (typeof window !== "undefined") window.LOOP_SCHEDULE = { CADENCES, WEEKDAYS, DEFAULT_SCHEDULE, normalize, nextRun, dueState, describe, storageKey };
