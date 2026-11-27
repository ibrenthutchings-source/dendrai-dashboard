// Display helpers for the recurring Run Loop schedule. The schedule itself lives on the
// server (loop_schedule_runner.py / loop_schedule_logic.py): it runs whether or not this
// app is open, and next-run times are computed there in the schedule's own timezone.
// Field names match the API: cadence, run_time "HH:MM", weekday (Sunday=0), day_of_month, tz.

export const CADENCES = [
  { id: "daily",     label: "Daily" },
  { id: "weekly",    label: "Weekly" },
  { id: "monthly",   label: "Monthly" },
  { id: "quarterly", label: "Quarterly (Jan / Apr / Jul / Oct)" },
];

export const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export const DEFAULT_SCHEDULE = { enabled: true, cadence: "weekly", run_time: "08:00", weekday: 1, day_of_month: 1 };

export const localTz = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
};

export function describe(s) {
  const x = { ...DEFAULT_SCHEDULE, ...(s || {}) };
  const tz = x.tz ? ` (${x.tz})` : "";
  if (x.cadence === "daily") return `Every day at ${x.run_time}${tz}`;
  if (x.cadence === "weekly") return `Every ${WEEKDAYS[x.weekday] ?? "Monday"} at ${x.run_time}${tz}`;
  if (x.cadence === "monthly") return `Day ${x.day_of_month} of each month at ${x.run_time}${tz}`;
  return `Day ${x.day_of_month} of Jan / Apr / Jul / Oct at ${x.run_time}${tz}`;
}

// Formats a server timestamp in the viewer's locale.
export const fmtWhen = (iso) => iso
  ? new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
  : "—";

if (typeof window !== "undefined") window.LOOP_SCHEDULE = { CADENCES, WEEKDAYS, DEFAULT_SCHEDULE, localTz, describe, fmtWhen };
