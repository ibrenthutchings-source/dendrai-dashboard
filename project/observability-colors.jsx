/* ============================================================
   Shared verdict/risk-tier color tokens.

   Single source of truth for continuous-monitoring-viz.jsx and
   control-flow-map.jsx, which each held their own independent copy of
   the same two maps — already drifted (control-flow-map.jsx had no
   NOT_REVIEWED entry, so an event with that verdict rendered without a
   defined color there once continuous-monitoring-viz.jsx gained it).
   Verdict/tier severity colors are theme-invariant (saturated enough to
   read on both a light and dark panel) — only the surrounding chrome
   (backgrounds, gridlines, text) follows the theme.
   ============================================================ */

export const VERDICT_COLOR = {
  ESCALATE: "#ef4444",
  MONITOR: "#3b82f6",
  CLEAR: "#22c55e",
  // A real, healthy event that was simply never selected for adjudication
  // (mcp_governance's poll loop only pulls flagged rows) — deliberately a
  // lighter, cooler grey than UNKNOWN, since the two mean different things:
  // UNKNOWN is missing/malformed verdict data, NOT_REVIEWED is honest
  // "not yet looked at" volume.
  NOT_REVIEWED: "#cbd5e1",
  UNKNOWN: "#64748b",
};

export const TIER_COLOR = {
  CRITICAL: "#ef4444",
  HIGH: "#f97316",
  MEDIUM: "#f59e0b",
  LOW: "#22c55e",
  UNKNOWN: "#64748b",
};
