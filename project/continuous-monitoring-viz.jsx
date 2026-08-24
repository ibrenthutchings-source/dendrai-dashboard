/* ============================================================
   Continuous Monitoring — grouped process-mining views.

   Four chart types, generalized over a "dimension" (what to group
   events by) so the exact same charts serve two different tabs:

     - ContinuousMonitoringDomainViz       groups by Core Domain
       ("By Core Domain & Risk")
     - ContinuousMonitoringSourceSystemViz groups by Source System
       ("Adjudication Flow" — supersedes the old static
       control-flow-map.jsx Sankey with an animated equivalent
       plus three more views built the same way)

   Chart types (all reading real adjudicated-event data, never
   synthetic, all following the user's light/dark preference set in
   Mission Control — see useThemeColors() below):

     - EventReplayChart  : animated per-event playback (canvas),
       ported from the "Interaction & Motion Study" design artifact.
     - DimensionSankey   : Group -> Verdict flow (d3-sankey), same
       visual language as the original control-flow-map.jsx.
     - DimensionHeatGrid : Group x Day density matrix.
     - DimensionFlowGraph: animated Directly-Follows Graph (dagre
       layout + D3 rendering) — Group -> Risk Tier -> Verdict ->
       Rule, edge width/label = real observed transition counts,
       particles animate along each edge to show flow direction.

   Data: GET /api/mcp/observability/events?days=N — a single fetch
   per (dimension, days) powers all four charts; each aggregates
   client-side from the same per-event feed rather than depending on
   a dimension-specific backend endpoint, which is what makes this
   generalize to a second dimension for free.

   A null/empty group value (e.g. an event whose policy violation
   isn't domain-mapped yet) always becomes an explicit "Unclassified"
   /"Unknown" bucket — never silently dropped.
   ============================================================ */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sankey, sankeyLinkHorizontal } from "d3-sankey";
import * as d3 from "d3";
import dagre from "dagre";
import { VERDICT_COLOR, TIER_COLOR } from "./observability-colors.jsx";

function _obsBase() {
  return (window.MCP_API_BASE || "/api/mcp") + "/observability";
}

/* ── Theme: read the app's live CSS custom properties (light/dark/accent,
   set by Mission Control) so canvas/D3-drawn pixels — which can't just
   inherit CSS like DOM elements can — match whatever the rest of the app
   is doing. document.body carries the resolved data-theme attribute
   (app.jsx applies it, including resolving "system" to a concrete value),
   so a MutationObserver there is sufficient; no separate matchMedia
   listener is needed. ── */
const THEME_VARS = [
  "bg", "surface", "surface-2", "surface-3",
  "ink", "ink-2", "ink-3", "ink-4",
  "line", "line-2", "line-strong",
  "acc", "acc-ink",
  "red", "red-ink", "amber", "amber-ink", "green", "green-ink",
];
const THEME_FALLBACK = {
  bg: "#080c14", surface: "#0f172a", "surface-2": "#111827", "surface-3": "#1f2937",
  ink: "#e2e8f0", "ink-2": "#94a3b8", "ink-3": "#64748b", "ink-4": "#475569",
  line: "#1e293b", "line-2": "#334155", "line-strong": "#475569",
  acc: "#22d3ee", "acc-ink": "#22d3ee",
  red: "#ef4444", "red-ink": "#ef4444",
  amber: "#f59e0b", "amber-ink": "#f59e0b",
  green: "#22c55e", "green-ink": "#22c55e",
};
function readThemeColors() {
  if (typeof document === "undefined") return THEME_FALLBACK;
  const cs = getComputedStyle(document.body);
  const out = {};
  THEME_VARS.forEach(k => { const v = cs.getPropertyValue(`--${k}`).trim(); out[k] = v || THEME_FALLBACK[k]; });
  return out;
}
function useThemeColors() {
  const [colors, setColors] = useState(readThemeColors);
  useEffect(() => {
    const update = () => setColors(readThemeColors());
    update();
    const mo = new MutationObserver(update);
    mo.observe(document.body, { attributes: true, attributeFilter: ["data-theme", "data-accent"] });
    return () => mo.disconnect();
  }, []);
  return colors;
}

// Shared categorical palette, assigned in fixed order — never cycled, and
// deliberately theme-invariant (a group's identity color shouldn't change
// when you flip light/dark).
const CATEGORICAL_PALETTE = [
  "#a855f7", "#22d3ee", "#ec4899", "#84cc16", "#f97316",
  "#3b82f6", "#eab308", "#14b8a6", "#f43f5e", "#64748b",
];

// ── Dimension: Core Domain — fixed known set, so colors are assigned by
// position in that list rather than order of appearance.
//
// Identity & Access Management is split into its rule-family sub-domains
// right here rather than as a separate dimension/tab — IAM alone is 86% of
// all ADJUDICATED domain-classified volume (see pol_domain_mappings.
// IAM_SUBDOMAIN_MAPPINGS's module comment), almost entirely one system-
// agnostic-by-design rule, and SoD/privilege conflicts, stale-access
// governance, and repo/branch-access bypass are three different
// remediation owners today flattened into one bar. Every other domain
// stays a single bucket — this is IAM-specific granularity, not a general
// two-tier taxonomy. A row whose domain resolves to IAM but whose specific
// rule isn't yet in IAM_SUBDOMAIN_MAPPINGS (the itgc catch-all, mostly)
// lands in "IAM: Other" rather than disappearing. ──
const DOMAIN_ORDER = [
  "IAM: SoD & Privilege Conflicts",
  "IAM: Access Governance & Reviews",
  "IAM: Repo & Branch Access",
  "IAM: Other",
  "Financial Reporting & Controls",
  "Cyber Security & Data Protection",
  "Third-Party & Vendor Risk",
  "Operational Resilience",
  "Regulatory & Compliance",
  "Technology & Change Management",
  "People & Organisational Risk",
  "Market & Economic Risk",
  "Unclassified",
];
const DOMAIN_COLOR_MAP = Object.fromEntries(DOMAIN_ORDER.map((d, i) => [d, CATEGORICAL_PALETTE[i % CATEGORICAL_PALETTE.length]]));
const _IAM_DOMAIN = "Identity & Access Management";
function domainGroupLabel(e) {
  if (e.domain !== _IAM_DOMAIN) return e.domain || "Unclassified";
  return `IAM: ${e.sub_domain || "Other"}`;
}
const DOMAIN_DIM = {
  key: "domain",
  label: "Core Domain",
  noun: "domain",
  extract: domainGroupLabel,
  order: DOMAIN_ORDER,
  color: v => DOMAIN_COLOR_MAP[v || "Unclassified"] || "#64748b",
};

// ── Dimension: Source System — set isn't known ahead of time, so colors
// are assigned on first sight and cached (same approach control-flow-map.jsx
// uses for its system column). ──
const _systemColorCache = new Map();
function sourceSystemColor(name) {
  const key = name || "Unknown";
  if (!_systemColorCache.has(key)) _systemColorCache.set(key, CATEGORICAL_PALETTE[_systemColorCache.size % CATEGORICAL_PALETTE.length]);
  return _systemColorCache.get(key);
}
const SOURCE_SYSTEM_DIM = {
  key: "source_system",
  label: "Source System",
  noun: "source system",
  extract: e => e.source_system || "Unknown",
  order: null, // dynamic — sorted by volume at render time
  color: sourceSystemColor,
};

// Click-through: cem.jsx's Adjudications tab (UBOGovPanel) accepts an
// `initialFilter` of { domain, source, tier, verdict } (see cem.jsx) — and
// its "domain" filter only knows the real Core Domain values (e.g.
// "Identity & Access Management"), not the "IAM: SoD & Privilege Conflicts"
// display sub-label DOMAIN_DIM.extract produces. This maps a clicked group
// value back to the real filterable value before building the CEM filter.
function domainFilterValue(dim, groupValue) {
  if (dim.key === "domain" && typeof groupValue === "string" && groupValue.startsWith("IAM: ")) {
    return _IAM_DOMAIN;
  }
  return groupValue;
}
function groupFilterKey(dim) {
  if (dim.key === "domain") return "domain";
  if (dim.key === "source_system") return "source";
  return null;
}
function groupCemFilter(dim, groupValue, extra) {
  const key = groupFilterKey(dim);
  const f = key && groupValue ? { [key]: domainFilterValue(dim, groupValue) } : {};
  return { ...f, ...extra };
}
// Same idea for a Flow Graph node of any kind (group/tier/verdict/rule) —
// "rule" has no corresponding filter in the Adjudications tab (policy
// violation text isn't a filterable column there), so it resolves to null
// and the caller should treat that as "not clickable."
function dfgNodeCemFilter(n, dim) {
  if (n.kind === dim.key) return groupCemFilter(dim, n.label);
  if (n.kind === "tier") return { tier: n.label };
  // A NOT_REVIEWED verdict node has nothing in Adjudications to jump to —
  // by definition, it was never adjudicated.
  if (n.kind === "verdict" && n.label !== "NOT_REVIEWED") return { verdict: n.label };
  return null;
}

// VERDICT_COLOR/TIER_COLOR now live in observability-colors.jsx — shared
// with control-flow-map.jsx, which had its own independent (and already
// drifted) copy of both. See that file for the NOT_REVIEWED rationale.

function _fmtDay(iso) {
  return new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// ── Shared: theme-aware process-mining panel frame (matches ControlFlowMap's
// original layout; colors now come from useThemeColors() instead of being
// fixed dark). `loading` (first fetch in flight, nothing to show yet) is
// visually distinct from `empty` (fetch finished, genuinely nothing there)
// so a tab switch reads as "working on it" rather than a blank flash. ──
function VizFrame({ kicker, sub, controls, height = 620, children, error, empty, loading, theme }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8, gap: 12, flexWrap: "wrap" }}>
        <div className="kicker">{kicker}</div>
        {controls}
      </div>
      {sub && <div className="panel-sub" style={{ marginBottom: 10 }}>{sub}</div>}
      {error && <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 10 }}>{error}</div>}
      <div style={{
        position: "relative", width: "100%", height,
        borderRadius: 8, overflow: "hidden",
        border: `1px solid ${theme.line}`, background: theme.bg,
      }}>
        {loading && (
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", gap: 9, color: theme["ink-3"], fontSize: 12, fontFamily: "system-ui, sans-serif" }}>
            <span style={{
              width: 13, height: 13, borderRadius: "50%",
              border: `2px solid ${theme["line-2"]}`, borderTopColor: theme.acc,
              animation: "cm-viz-spin 0.8s linear infinite",
            }} />
            Loading…
          </div>
        )}
        {!loading && empty && (
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: theme["ink-3"], fontSize: 12, fontFamily: "system-ui, sans-serif", textAlign: "center", padding: 24 }}>
            {empty}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

function DaysSelect({ days, setDays }) {
  return (
    <select value={days} onChange={e => setDays(Number(e.target.value))}
      style={{ fontSize: 11, padding: "3px 6px", borderRadius: 4, border: "1px solid var(--line)", background: "var(--surface)", color: "var(--ink)" }}>
      <option value={7}>Last 7 days</option>
      <option value={30}>Last 30 days</option>
      <option value={90}>Last 90 days</option>
    </select>
  );
}

/* ── Shared event fetch: one row per adjudicated event, powers all four
   chart types for whichever dimension is active. ── */
function useObservabilityEvents(days) {
  const [state, setState] = useState({ events: [], loading: true, error: null });
  const load = useCallback(() => {
    setState(s => ({ ...s, loading: true }));
    return fetch(`${_obsBase()}/events?days=${days}&limit=5000`, { credentials: "include" })
      .then(res => { if (!res.ok) throw new Error(`Failed to load events (${res.status})`); return res.json(); })
      .then(d => setState({ events: d.events || [], loading: false, error: d.note || null }))
      .catch(e => setState({ events: [], loading: false, error: e.message }));
  }, [days]);
  useEffect(() => { load(); }, [load]);
  return { ...state, reload: load };
}

// Client-side aggregation shared by DimensionSankey and DimensionHeatGrid:
// per-group totals/verdict split/daily bucketing, from the same raw events
// EventReplayChart and DimensionFlowGraph already consume — one data source
// for all four charts.
function aggregateByGroup(events, dim, days) {
  const dayKeys = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today); d.setUTCDate(d.getUTCDate() - i);
    dayKeys.push(d.toISOString().slice(0, 10));
  }
  const byGroup = new Map();
  events.forEach(e => {
    const key = dim.extract(e);
    let g = byGroup.get(key);
    if (!g) { g = { key, total: 0, escalated: 0, monitor: 0, clear: 0, not_reviewed: 0, daily: new Map() }; byGroup.set(key, g); }
    g.total++;
    const vk = e.verdict === "ESCALATE" ? "escalated" : e.verdict === "MONITOR" ? "monitor"
      : e.verdict === "CLEAR" ? "clear" : e.verdict === "NOT_REVIEWED" ? "not_reviewed" : null;
    if (vk) g[vk]++;
    const dayKey = e.adjudicated_at ? e.adjudicated_at.slice(0, 10) : null;
    if (dayKey) {
      let day = g.daily.get(dayKey);
      if (!day) { day = { date: dayKey, total: 0, escalated: 0 }; g.daily.set(dayKey, day); }
      day.total++;
      if (e.verdict === "ESCALATE") day.escalated++;
    }
  });
  const groups = [...byGroup.values()]
    .map(g => ({ ...g, daily: [...g.daily.values()].sort((a, b) => a.date.localeCompare(b.date)) }))
    .sort((a, b) => b.total - a.total);
  return { groups, dayKeys };
}

// Groups present in this window, in the dimension's fixed order if it has
// one, otherwise sorted by volume.
function orderedGroups(events, dim) {
  const counts = new Map();
  events.forEach(e => { const k = dim.extract(e); counts.set(k, (counts.get(k) || 0) + 1); });
  if (dim.order) return dim.order.filter(k => counts.has(k));
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
}

/* ════════════════════════════════════════════════════════════════════════
   1. Event Replay — animated per-event playback, grouped by `dim`
   ════════════════════════════════════════════════════════════════════════ */

export function EventReplayChart({ theme, days, dim, rawEvents, loading, error, onNavigate }) {
  const cvRef = useRef(null);
  const hostRef = useRef(null);
  const [tip, setTip] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [loop, setLoop] = useState(true);
  const [focusGroup, setFocusGroup] = useState(null);
  const [escOnly, setEscOnly] = useState(false);
  // The canvas below has no accessible fallback at all — no DOM nodes to
  // inspect, no keyboard path to the hover-to-read interaction. This is the
  // escape hatch: the exact same event set, as a real, keyboard-navigable,
  // screen-reader-readable table.
  const [tableView, setTableView] = useState(false);
  const playheadRef = useRef(days);
  const [playhead, setPlayheadState] = useState(days);
  const rafRef = useRef(null);
  const lastFrameRef = useRef(0);

  const reducedMotion = useMemo(
    () => (window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)").matches : false),
    []
  );

  // Reset the playhead to "now" whenever the window size changes.
  useEffect(() => { playheadRef.current = days; setPlayheadState(days); setPlaying(false); }, [days]);
  // A dimension switch (domain <-> source system) should also reset focus.
  useEffect(() => { setFocusGroup(null); }, [dim]);

  const groups = useMemo(() => orderedGroups(rawEvents, dim), [rawEvents, dim]);

  // Events with a `t` field: days-ago-from-window-start, 0..days.
  const events = useMemo(() => {
    if (!rawEvents.length) return [];
    const now = Date.now();
    return rawEvents
      .map(e => {
        const ts = e.adjudicated_at ? new Date(e.adjudicated_at).getTime() : now;
        const t = Math.max(0, Math.min(days, days - (now - ts) / 86400000));
        return { ...e, _group: dim.extract(e), t };
      })
      .sort((a, b) => a.t - b.t);
  }, [rawEvents, days, dim]);

  function setPlayhead(v) { playheadRef.current = v; setPlayheadState(v); }

  const laneLayout = useCallback(() => {
    const lanes = [];
    let y = 30;
    groups.forEach(g => { lanes.push({ group: g, y, h: 26 }); y += 26 + 3; });
    return { lanes, height: y + 24 };
  }, [groups]);

  const draw = useCallback((head) => {
    const cv = cvRef.current, host = hostRef.current;
    if (!cv || !host) return;
    const ctx = cv.getContext("2d");
    const { lanes, height } = laneLayout();
    const cssW = Math.max(320, host.clientWidth - 4);
    const dpr = window.devicePixelRatio || 1;
    cv.style.height = height + "px";
    cv.width = cssW * dpr; cv.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, height);

    const PAD_L = 210, PAD_R = 20;
    const plotW = cssW - PAD_L - PAD_R;
    const t2x = t => PAD_L + (t / days) * plotW;
    const laneY = {}; lanes.forEach(ln => { laneY[ln.group] = ln.y + ln.h / 2; });

    // Day gridlines
    ctx.strokeStyle = theme.line; ctx.lineWidth = 1;
    ctx.font = '9px "Geist Mono", ui-monospace, monospace';
    ctx.fillStyle = theme["ink-3"]; ctx.textAlign = "center";
    const step = days <= 7 ? 1 : days <= 30 ? 5 : 10;
    for (let g = 0; g <= days; g += step) {
      const gx = Math.round(t2x(g)) + 0.5;
      ctx.setLineDash([2, 3]); ctx.beginPath();
      ctx.moveTo(gx, 12); ctx.lineTo(gx, height - 16); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillText(g === days ? "now" : `-${days - g}d`, gx, 22);
    }

    const visible = e => !escOnly || e.verdict === "ESCALATE";

    // Lanes + count
    ctx.textAlign = "left";
    lanes.forEach(ln => {
      const dim2 = focusGroup !== null && focusGroup !== ln.group;
      ctx.globalAlpha = dim2 ? 0.22 : 1;
      ctx.fillStyle = theme["surface-2"];
      ctx.beginPath();
      const r = 3, x = 0, y = ln.y, w = cssW, h = ln.h - 4;
      ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath(); ctx.fill();

      ctx.fillStyle = dim.color(ln.group);
      ctx.beginPath(); ctx.arc(10, ln.y + ln.h / 2 - 2, 3.5, 0, 6.2832); ctx.fill();
      ctx.fillStyle = theme.ink;
      ctx.font = '650 11px "Geist", -apple-system, sans-serif';
      ctx.fillText(ln.group.length > 26 ? ln.group.slice(0, 25) + "…" : ln.group, 20, ln.y + ln.h / 2 + 2);

      const shown = events.filter(e => e._group === ln.group && e.t <= head && visible(e)).length;
      ctx.fillStyle = theme["ink-2"];
      ctx.font = '700 10px "Geist Mono", ui-monospace, monospace';
      ctx.textAlign = "right";
      ctx.fillText(String(shown), PAD_L - 10, ln.y + ln.h / 2 + 3);
      ctx.textAlign = "left";
      ctx.globalAlpha = 1;
    });

    // Events (jittered within the lane, arrival + trail + escalation pulse)
    const now = performance.now();
    events.forEach(e => {
      if (e.t > head || !visible(e)) return;
      const cy0 = laneY[e._group];
      if (cy0 === undefined) return;
      const dim2 = focusGroup !== null && focusGroup !== e._group;
      const jitter = ((e.id * 2654435761) % 1000) / 1000 - 0.5;
      const cy = cy0 + jitter * 9;
      const cx = t2x(e.t);
      const age = head - e.t;

      let op = reducedMotion ? 0.7 : Math.max(0.12, 1 - age / (days * 0.7));
      if (e.verdict === "CLEAR") op *= 0.6;
      if (e.verdict === "NOT_REVIEWED") op *= 0.4;
      if (dim2) op *= 0.18;

      let rad = e.verdict === "ESCALATE" ? 3.6 : e.verdict === "MONITOR" ? 2.8 : e.verdict === "NOT_REVIEWED" ? 1.7 : 2.1;
      if (!reducedMotion && age < days * 0.012) {
        const k = age / (days * 0.012);
        rad *= 0.35 + 0.65 * (1 - Math.pow(1 - k, 3));
        op *= 0.4 + 0.6 * k;
      }
      if (!reducedMotion && e.verdict === "ESCALATE" && !dim2) {
        const ph = 0.10 + 0.10 * Math.sin(now / 520 + e.t);
        ctx.beginPath();
        ctx.arc(cx, cy, rad + 3.5 + 1.6 * Math.sin(now / 520 + e.t), 0, 6.2832);
        ctx.fillStyle = VERDICT_COLOR.ESCALATE; ctx.globalAlpha = ph; ctx.fill();
      }
      ctx.beginPath(); ctx.arc(cx, cy, rad, 0, 6.2832);
      ctx.fillStyle = VERDICT_COLOR[e.verdict] || VERDICT_COLOR.UNKNOWN;
      ctx.globalAlpha = Math.min(op, 0.95);
      ctx.fill();
      ctx.globalAlpha = 1;
    });

    // Time cursor
    if (head < days) {
      const hx = Math.round(t2x(head)) + 0.5;
      ctx.strokeStyle = theme.acc; ctx.lineWidth = 1.5; ctx.globalAlpha = 0.85;
      ctx.beginPath(); ctx.moveTo(hx, 12); ctx.lineTo(hx, height - 16); ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }, [laneLayout, events, days, focusGroup, escOnly, reducedMotion, theme, dim]);

  // Animation loop
  useEffect(() => {
    function frame(ts) {
      const dt = lastFrameRef.current ? (ts - lastFrameRef.current) / 1000 : 0;
      lastFrameRef.current = ts;
      if (playing && !reducedMotion) {
        let h = playheadRef.current + dt * speed * (days / 55);
        if (h >= days) { h = loop ? 0 : days; if (!loop) setPlaying(false); }
        setPlayhead(h);
      }
      draw(playheadRef.current);
      rafRef.current = requestAnimationFrame(frame);
    }
    rafRef.current = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(rafRef.current);
  }, [playing, speed, loop, days, draw, reducedMotion]);

  useEffect(() => { draw(playheadRef.current); }, [draw]);
  useEffect(() => {
    const ro = new ResizeObserver(() => draw(playheadRef.current));
    if (hostRef.current) ro.observe(hostRef.current);
    return () => ro.disconnect();
  }, [draw]);

  function handleMouseMove(evt) {
    const cv = cvRef.current;
    if (!cv) return;
    const rect = cv.getBoundingClientRect();
    const mx = evt.clientX - rect.left, my = evt.clientY - rect.top;
    const { lanes } = laneLayout();
    const cssW = Math.max(320, hostRef.current.clientWidth - 4);
    const PAD_L = 210, PAD_R = 20;
    const plotW = cssW - PAD_L - PAD_R;
    const head = playheadRef.current;
    const laneY = {}; lanes.forEach(ln => { laneY[ln.group] = ln.y + ln.h / 2; });
    const visible = e => !escOnly || e.verdict === "ESCALATE";
    let best = null, bestD = 12;
    events.forEach(e => {
      if (e.t > head || !visible(e)) return;
      const cy0 = laneY[e._group]; if (cy0 === undefined) return;
      const jitter = ((e.id * 2654435761) % 1000) / 1000 - 0.5;
      const cy = cy0 + jitter * 9;
      const cx = PAD_L + (e.t / days) * plotW;
      const dd = Math.hypot(cx - mx, cy - my);
      if (dd < bestD) { bestD = dd; best = e; }
    });
    if (!best) { setTip(null); return; }
    setTip({ x: evt.clientX, y: evt.clientY, event: best });
  }

  function handleClickCanvas(evt) {
    const cv = cvRef.current;
    const rect = cv.getBoundingClientRect();
    const mx = evt.clientX - rect.left;
    const my = evt.clientY - rect.top;
    if (mx <= 205) {
      // Label gutter: toggle isolate-this-group, same as before.
      const { lanes } = laneLayout();
      const hit = lanes.find(ln => my >= ln.y && my <= ln.y + ln.h);
      if (hit) setFocusGroup(prev => prev === hit.group ? null : hit.group);
      return;
    }
    // Plot area: click-through to the Adjudications tab pre-filtered to the
    // exact event under the cursor, if any (same hit-test as the hover tooltip).
    if (!onNavigate) return;
    const { lanes } = laneLayout();
    const cssW = Math.max(320, hostRef.current.clientWidth - 4);
    const PAD_L = 210, PAD_R = 20;
    const plotW = cssW - PAD_L - PAD_R;
    const head = playheadRef.current;
    const laneY = {}; lanes.forEach(ln => { laneY[ln.group] = ln.y + ln.h / 2; });
    const visible = e => !escOnly || e.verdict === "ESCALATE";
    let best = null, bestD = 12;
    events.forEach(e => {
      if (e.t > head || !visible(e)) return;
      const cy0 = laneY[e._group]; if (cy0 === undefined) return;
      const jitter = ((e.id * 2654435761) % 1000) / 1000 - 0.5;
      const cy = cy0 + jitter * 9;
      const cx = PAD_L + (e.t / days) * plotW;
      const dd = Math.hypot(cx - mx, cy - my);
      if (dd < bestD) { bestD = dd; best = e; }
    });
    if (!best) return;
    onNavigate("ubogov", { cemTab: "adjudications", cemFilter: groupCemFilter(dim, best._group, { verdict: best.verdict }) });
  }

  const visibleEvents = useMemo(
    () => events.filter(e => e.t <= playhead && (!escOnly || e.verdict === "ESCALATE"))
      .sort((a, b) => b.t - a.t),
    [events, playhead, escOnly]
  );
  const escalatedShown = events.filter(e => e.t <= playhead && e.verdict === "ESCALATE").length;
  const shown = visibleEvents.length;

  return (
    <VizFrame
      theme={theme}
      kicker={`Continuous evidence · ${dim.label} Event Replay`}
      sub={`Every observed event replayed over the ${days}-day window, grouped by ${dim.label} — pale dots were never selected for review, colored dots got a real verdict. Click a label to isolate it, click an event to open it in Adjudications, hover for detail.`}
      error={error && !events.length ? error : null}
      empty={!loading && !events.length && !error ? `No events in the last ${days} days yet.` : null}
      loading={loading && !events.length}
      height={Math.max(360, groups.length * 29 + 60)}
    >
      {events.length > 0 && (
        <>
          <div ref={hostRef} style={{ position: "absolute", inset: 0, top: 0, bottom: 56, overflow: tableView ? "auto" : "hidden" }}>
            {tableView ? (
              <table className="cm-replay-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <caption style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
                  {dim.label} Event Replay, table view — {visibleEvents.length} events, most recent first
                </caption>
                <thead>
                  <tr style={{ position: "sticky", top: 0, background: theme.surface, zIndex: 1 }}>
                    <th scope="col" style={{ textAlign: "left", padding: "6px 14px", borderBottom: `1px solid ${theme.line}`, color: theme["ink-3"], fontWeight: 600 }}>When</th>
                    <th scope="col" style={{ textAlign: "left", padding: "6px 10px", borderBottom: `1px solid ${theme.line}`, color: theme["ink-3"], fontWeight: 600 }}>{dim.label}</th>
                    <th scope="col" style={{ textAlign: "left", padding: "6px 10px", borderBottom: `1px solid ${theme.line}`, color: theme["ink-3"], fontWeight: 600 }}>Verdict</th>
                    <th scope="col" style={{ textAlign: "left", padding: "6px 10px", borderBottom: `1px solid ${theme.line}`, color: theme["ink-3"], fontWeight: 600 }}>Tier</th>
                    <th scope="col" style={{ textAlign: "left", padding: "6px 14px", borderBottom: `1px solid ${theme.line}`, color: theme["ink-3"], fontWeight: 600 }}>Target</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleEvents.map(e => (
                    <tr key={e.id}
                      style={{ cursor: onNavigate ? "pointer" : "default" }}
                      tabIndex={onNavigate ? 0 : undefined}
                      onClick={() => onNavigate && onNavigate("ubogov", { cemTab: "adjudications", cemFilter: groupCemFilter(dim, e._group, { verdict: e.verdict }) })}
                      onKeyDown={ev => { if (onNavigate && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); onNavigate("ubogov", { cemTab: "adjudications", cemFilter: groupCemFilter(dim, e._group, { verdict: e.verdict }) }); } }}>
                      <td className="mono" style={{ padding: "5px 14px", color: theme["ink-3"], whiteSpace: "nowrap" }}>{e.adjudicated_at ? new Date(e.adjudicated_at).toLocaleString() : "—"}</td>
                      <td style={{ padding: "5px 10px", color: dim.color(e._group) }}>{e._group}</td>
                      <td style={{ padding: "5px 10px", fontWeight: 700, color: VERDICT_COLOR[e.verdict] || theme["ink-2"] }}>{e.verdict}</td>
                      <td style={{ padding: "5px 10px", color: theme["ink-2"] }}>{e.risk_tier || "—"}</td>
                      <td style={{ padding: "5px 14px", color: theme.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 260 }}>{e.target_tool || e.server_name || e.source_system || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <canvas
                ref={cvRef}
                role="img"
                aria-label={`${dim.label} Event Replay chart — ${shown} events plotted. Use "View as table" for a screen-reader- and keyboard-accessible version of the same data.`}
                style={{ width: "100%", display: "block", cursor: "crosshair" }}
                onMouseMove={handleMouseMove}
                onMouseLeave={() => setTip(null)}
                onClick={handleClickCanvas}
              />
            )}
          </div>

          {!tableView && tip && (
            <div style={{
              position: "fixed",
              // Flip to the cursor's left once there isn't room for the
              // 300px-wide box on the right (events near the chart's right
              // edge otherwise popped the tooltip half off-screen).
              // translateX(-100%) anchors by the box's own right edge rather
              // than assuming the exact rendered width, so it still lands
              // flush against the cursor even when the content is narrower
              // than the 300px cap.
              left: tip.x > window.innerWidth - 320 ? tip.x - 16 : tip.x + 16,
              transform: tip.x > window.innerWidth - 320 ? "translateX(-100%)" : "none",
              top: tip.y - 10,
              background: theme.surface, border: `1px solid ${dim.color(tip.event._group)}`,
              borderLeft: `3px solid ${VERDICT_COLOR[tip.event.verdict] || theme["ink-3"]}`,
              borderRadius: 7, padding: "9px 13px", maxWidth: 300, zIndex: 9999, pointerEvents: "none",
              boxShadow: "0 6px 32px oklch(0% 0 0 / .4)", fontFamily: "system-ui, sans-serif",
            }}>
              <div style={{ fontSize: 9, color: dim.color(tip.event._group), fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4 }}>
                {tip.event._group}
              </div>
              <div style={{ fontSize: 11.5, color: theme.ink, fontWeight: 600, marginBottom: 4 }}>
                {tip.event.target_tool || tip.event.server_name || tip.event.source_system || "—"}
              </div>
              <div style={{ fontSize: 10, color: theme["ink-2"], marginBottom: 2 }}>
                <span style={{ color: VERDICT_COLOR[tip.event.verdict] || theme["ink-2"], fontWeight: 700 }}>{tip.event.verdict}</span>
                {"  ·  "}{tip.event.risk_tier || "UNKNOWN"}{"  ·  "}{tip.event.source_system || "—"}
              </div>
              {tip.event.policy_violations?.length > 0 && (
                <div style={{ fontSize: 9.5, color: theme["ink-3"], marginTop: 4, maxWidth: 270 }}>
                  {tip.event.policy_violations[0]}
                </div>
              )}
              <div style={{ fontSize: 9, color: theme["ink-4"], marginTop: 5 }}>
                {tip.event.adjudicated_at ? new Date(tip.event.adjudicated_at).toLocaleString() : "—"}
              </div>
            </div>
          )}

          <div style={{
            position: "absolute", bottom: 0, left: 0, right: 0, height: 56,
            display: "flex", alignItems: "center", gap: 10, padding: "0 14px",
            borderTop: `1px solid ${theme.line}`, background: theme["surface-2"], flexWrap: "wrap",
          }}>
            <button type="button" className="btn btn-sm"
              onClick={() => { if (playheadRef.current >= days && !playing) setPlayhead(0); setPlaying(p => !p); }}>
              {playing ? "❚❚ Pause" : "▶ Play"}
            </button>
            <button type="button" className="btn btn-sm" title="Back one day"
              onClick={() => { setPlaying(false); setPlayhead(Math.max(0, playheadRef.current - 1)); }}>◀ Day</button>
            <button type="button" className="btn btn-sm" title="Forward one day"
              onClick={() => { setPlaying(false); setPlayhead(Math.min(days, playheadRef.current + 1)); }}>Day ▶</button>
            <input type="range" min={0} max={days} step={0.05} value={playhead}
              onChange={e => { setPlaying(false); setPlayhead(parseFloat(e.target.value)); }}
              style={{ flex: "1 1 160px", minWidth: 140 }} aria-label={`Scrub through the ${days}-day window`} />
            <div style={{ display: "inline-flex", border: `1px solid ${theme["line-2"]}`, borderRadius: 6, overflow: "hidden" }}>
              {[1, 4, 12, 40].map(s => (
                <button key={s} type="button" onClick={() => setSpeed(s)}
                  style={{
                    fontFamily: "monospace", fontSize: 10.5, fontWeight: 600, padding: "5px 8px", cursor: "pointer",
                    border: "none", borderRight: `1px solid ${theme["line-2"]}`,
                    background: speed === s ? theme["acc-ink"] + "22" : "transparent",
                    color: speed === s ? theme["acc-ink"] : theme["ink-2"],
                  }}>{s}×</button>
              ))}
            </div>
            <button type="button" className="btn btn-sm" aria-pressed={loop} onClick={() => setLoop(l => !l)}
              style={loop ? { borderColor: theme.acc, color: theme["acc-ink"] } : undefined}>↻ Loop</button>
            <button type="button" className="btn btn-sm" aria-pressed={escOnly} onClick={() => setEscOnly(v => !v)}
              style={escOnly ? { borderColor: VERDICT_COLOR.ESCALATE, color: theme["red-ink"] } : undefined}>Escalations only</button>
            <button type="button" className="btn btn-sm" aria-pressed={tableView} onClick={() => setTableView(v => !v)}
              title="Screen-reader- and keyboard-accessible view of the same data"
              style={tableView ? { borderColor: theme.acc, color: theme["acc-ink"] } : undefined}>
              {tableView ? "▤ Chart view" : "☰ View as table"}
            </button>
            <div className="mono" style={{ fontSize: 10.5, color: theme["ink-2"], marginLeft: "auto", whiteSpace: "nowrap" }}>
              -{(days - playhead).toFixed(1)}d · {shown} shown · {escalatedShown} escalated
            </div>
          </div>
        </>
      )}
    </VizFrame>
  );
}

/* ════════════════════════════════════════════════════════════════════════
   2. Dimension Sankey — Group -> Verdict
   ════════════════════════════════════════════════════════════════════════ */

export function DimensionSankey({ theme, days, dim, rawEvents, loading, error, onNavigate }) {
  const svgRef = useRef(null);
  const hostRef = useRef(null);
  const [tooltip, setTooltip] = useState(null);

  const agg = useMemo(() => (rawEvents.length ? aggregateByGroup(rawEvents, dim, days) : null), [rawEvents, dim, days]);

  const graph = useMemo(() => {
    if (!agg?.groups?.length) return null;
    const nodes = [];
    const links = [];
    const groupNodeId = {};
    agg.groups.forEach(g => {
      const id = `g:${g.key}`;
      groupNodeId[g.key] = id;
      nodes.push({ id, label: g.key, type: dim.label, kind: dim.key, color: dim.color(g.key), value: g.total });
    });
    ["escalated", "monitor", "clear", "not_reviewed"].forEach(v => {
      const label = v === "not_reviewed" ? "NOT REVIEWED" : v.toUpperCase();
      nodes.push({ id: `v:${v}`, label, type: "verdict", kind: "verdict", color: VERDICT_COLOR[v === "not_reviewed" ? "NOT_REVIEWED" : v.toUpperCase()] || VERDICT_COLOR.UNKNOWN });
    });
    agg.groups.forEach(g => {
      ["escalated", "monitor", "clear", "not_reviewed"].forEach(v => {
        if (g[v] > 0) links.push({ source: groupNodeId[g.key], target: `v:${v}`, value: g[v] });
      });
    });
    return { nodes, links };
  }, [agg, dim]);

  useEffect(() => {
    if (!svgRef.current || !hostRef.current || !graph) return;
    const svgEl = svgRef.current;
    const svg = d3.select(svgEl);
    svg.selectAll("*").remove();

    const W = hostRef.current.clientWidth || 960;
    const H = hostRef.current.clientHeight || 560;
    const PAD = { top: 40, bottom: 20, left: 190, right: 100 };

    svg.append("rect").attr("width", "100%").attr("height", "100%").attr("fill", theme.bg);

    const layout = sankey().nodeId(d => d.id).nodeWidth(14).nodePadding(10).nodeSort(null)
      .extent([[PAD.left, PAD.top], [W - PAD.right, H - PAD.bottom]]);
    const nodes = graph.nodes.map(d => ({ ...d }));
    const links = graph.links.map(d => ({ ...d }));
    const { nodes: N, links: L } = layout({ nodes, links });

    const defs = svg.append("defs");
    L.forEach((l, i) => {
      const g = defs.append("linearGradient").attr("id", `dsk-g-${i}`).attr("gradientUnits", "userSpaceOnUse")
        .attr("x1", l.source.x1).attr("y1", (l.source.y0 + l.source.y1) / 2)
        .attr("x2", l.target.x0).attr("y2", (l.target.y0 + l.target.y1) / 2);
      g.append("stop").attr("offset", "0%").attr("stop-color", l.source.color).attr("stop-opacity", 0.55);
      g.append("stop").attr("offset", "100%").attr("stop-color", l.target.color).attr("stop-opacity", 0.55);
    });

    const linkGen = sankeyLinkHorizontal();
    const linkPaths = svg.append("g").selectAll("path").data(L).join("path")
      .attr("d", linkGen).attr("fill", "none")
      .attr("stroke", (_, i) => `url(#dsk-g-${i})`)
      .attr("stroke-width", d => Math.max(1, d.width))
      .attr("stroke-opacity", 0.4).attr("cursor", "pointer");

    const nodeGrp = svg.append("g").selectAll("g").data(N).join("g").attr("cursor", "pointer");
    nodeGrp.append("rect")
      .attr("x", d => d.x0).attr("y", d => d.y0)
      .attr("width", d => d.x1 - d.x0).attr("height", d => Math.max(2, d.y1 - d.y0))
      .attr("rx", 2).attr("fill", d => d.color).attr("fill-opacity", 0.88)
      .attr("stroke", d => d.color).attr("stroke-width", 0.4).attr("stroke-opacity", 0.5);
    nodeGrp.filter(d => (d.y1 - d.y0) >= 14).append("text")
      .attr("x", d => (d.x0 + d.x1) / 2).attr("y", d => (d.y0 + d.y1) / 2)
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("fill", "#fff").attr("fill-opacity", 0.9).attr("font-size", 8).attr("font-weight", 700)
      .attr("font-family", "system-ui, sans-serif").attr("pointer-events", "none")
      .text(d => d.value ?? "");
    nodeGrp.append("text")
      .attr("x", d => d.depth === 0 ? d.x0 - 8 : d.x1 + 8)
      .attr("y", d => (d.y0 + d.y1) / 2).attr("dy", "0.35em")
      .attr("text-anchor", d => d.depth === 0 ? "end" : "start")
      .attr("fill", d => d.color).attr("fill-opacity", 0.9)
      .attr("font-size", 10.5).attr("font-weight", 600)
      .attr("font-family", "system-ui, sans-serif").attr("pointer-events", "none")
      .text(d => d.label);

    function linkId(l, end) { const v = l[end]; return typeof v === "object" ? v.id : v; }
    function connects(l, id) { return linkId(l, "source") === id || linkId(l, "target") === id; }
    function highlight(id) {
      linkPaths.attr("stroke-opacity", l => connects(l, id) ? 0.85 : 0.06);
      nodeGrp.style("opacity", d => d.id === id || L.some(l => connects(l, id) && connects(l, d.id)) ? 1 : 0.2);
    }
    function clear() { linkPaths.attr("stroke-opacity", 0.4); nodeGrp.style("opacity", 1); }

    nodeGrp
      .on("mouseover", (evt, d) => { highlight(d.id); setTooltip({ x: evt.clientX, y: evt.clientY, label: d.label, value: d.value, type: d.type, color: d.color }); })
      .on("mousemove", evt => setTooltip(p => p ? { ...p, x: evt.clientX, y: evt.clientY } : null))
      .on("mouseout", () => { clear(); setTooltip(null); })
      .on("click", (evt, d) => {
        // A NOT REVIEWED node/flow has nothing in Adjudications to jump to —
        // by definition, it was never adjudicated.
        if (!onNavigate || d.id === "v:not_reviewed") return;
        const f = d.kind === dim.key ? groupCemFilter(dim, d.label) : d.kind === "verdict" ? { verdict: d.label } : null;
        if (f) onNavigate("ubogov", { cemTab: "adjudications", cemFilter: f });
      });
    linkPaths
      .on("mouseover", (evt, d) => {
        linkPaths.attr("stroke-opacity", l => l === d ? 0.9 : 0.06);
        nodeGrp.style("opacity", n => n.id === d.source.id || n.id === d.target.id ? 1 : 0.15);
        setTooltip({ x: evt.clientX, y: evt.clientY, isLink: true, sourceLabel: d.source.label, sourceColor: d.source.color, targetLabel: d.target.label, targetColor: d.target.color, value: d.value });
      })
      .on("mousemove", evt => setTooltip(p => p ? { ...p, x: evt.clientX, y: evt.clientY } : null))
      .on("mouseout", () => { clear(); setTooltip(null); })
      .on("click", (evt, d) => {
        if (!onNavigate || d.target.id === "v:not_reviewed") return;
        // Links only ever run group -> verdict here, so both filters apply.
        onNavigate("ubogov", { cemTab: "adjudications", cemFilter: groupCemFilter(dim, d.source.label, { verdict: d.target.label }) });
      });
  }, [graph, theme, dim, onNavigate]);

  const hasData = !!graph;

  return (
    <VizFrame
      theme={theme}
      kicker={`Continuous evidence · ${dim.label} Sankey`}
      sub={`Every observed event in the last ${days} days, grouped by ${dim.label}, flowing to its verdict — NOT REVIEWED is real volume that was never selected for adjudication, not a data gap. Flow width is the real event count. Click a node or flow to open that slice in Adjudications.`}
      error={error && !hasData ? error : null}
      empty={!loading && !hasData && !error ? `No adjudicated events in the last ${days} days yet.` : null}
      loading={loading && !hasData}
    >
      {hasData && (
        <div ref={hostRef} style={{ position: "absolute", inset: 0 }}>
          {/* This diagram has no keyboard/screen-reader path of its own —
              the same underlying events are available as a real table via
              Event Replay's "View as table" toggle, same dim/day window. */}
          <svg ref={svgRef} role="img"
            aria-label={`${dim.label} Sankey diagram — ${agg.groups.length} ${dim.label.toLowerCase()} groups flowing to their verdict outcome. See Event Replay's table view for this data in an accessible form.`}
            style={{ width: "100%", height: "100%", display: "block" }} />
        </div>
      )}
      {tooltip && (
        <div style={{
          position: "fixed",
          // Same right-edge flip as EventReplayChart's tooltip — anchors by
          // the box's own right edge via translateX so it doesn't overflow
          // the viewport for nodes/edges near the chart's right side.
          left: tooltip.x > window.innerWidth - 280 ? tooltip.x - 16 : tooltip.x + 16,
          transform: tooltip.x > window.innerWidth - 280 ? "translateX(-100%)" : "none",
          top: tooltip.y - 10,
          background: theme.surface, border: `1px solid ${tooltip.isLink ? theme["line-strong"] : tooltip.color}`,
          borderRadius: 7, padding: "9px 13px", maxWidth: 260, zIndex: 9999, pointerEvents: "none",
          boxShadow: "0 6px 32px oklch(0% 0 0 / .4)", fontFamily: "system-ui, sans-serif",
        }}>
          {tooltip.isLink ? (
            <>
              <div style={{ fontSize: 11, color: theme.ink, fontWeight: 600 }}>
                <span style={{ color: tooltip.sourceColor }}>{tooltip.sourceLabel}</span>
                <span style={{ color: theme["ink-4"], margin: "0 6px" }}>→</span>
                <span style={{ color: tooltip.targetColor }}>{tooltip.targetLabel}</span>
              </div>
              <div style={{ marginTop: 5, fontSize: 10, color: theme["ink-2"] }}>
                <span style={{ color: theme.ink, fontWeight: 700 }}>{tooltip.value}</span> event{tooltip.value !== 1 ? "s" : ""}
              </div>
            </>
          ) : (
            <>
              <div style={{ fontSize: 9, color: tooltip.color, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5 }}>{tooltip.type}</div>
              <div style={{ fontSize: 12, color: theme.ink, fontWeight: 600, marginBottom: 5 }}>{tooltip.label}</div>
              {tooltip.value != null && (
                <div style={{ fontSize: 10, color: theme["ink-2"] }}>
                  <span style={{ color: theme.ink, fontWeight: 700 }}>{tooltip.value}</span> event{tooltip.value !== 1 ? "s" : ""}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </VizFrame>
  );
}

/* ════════════════════════════════════════════════════════════════════════
   3. Dimension Heat Grid — Group x Day density matrix
   ════════════════════════════════════════════════════════════════════════ */

export function DimensionHeatGrid({ theme, days, dim, rawEvents, loading, error, onNavigate }) {
  const [hover, setHover] = useState(null);
  const hostRef = useRef(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    if (!hostRef.current) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ w: width, h: height });
    });
    ro.observe(hostRef.current);
    return () => ro.disconnect();
  }, []);

  const agg = useMemo(() => (rawEvents.length ? aggregateByGroup(rawEvents, dim, days) : null), [rawEvents, dim, days]);

  const grid = useMemo(() => {
    if (!agg?.groups?.length) return null;
    const rows = agg.groups.map(g => {
      const byDay = Object.fromEntries(g.daily.map(x => [x.date, x]));
      return { key: g.key, total: g.total, cells: agg.dayKeys.map(k => byDay[k] || { date: k, total: 0, escalated: 0 }) };
    });
    const maxCell = Math.max(1, ...rows.flatMap(r => r.cells.map(c => c.total)));
    return { dayKeys: agg.dayKeys, rows, maxCell };
  }, [agg]);

  const hasData = !!grid;
  const labelW = 190;
  const TOP = 20, BOTTOM = 34, SIDE_PAD = 20;
  // Stretch cells to actually fill the frame instead of sitting fixed-size
  // in a corner — width fills the panel's real measured width; height fills
  // whatever room the panel's own height (set below) leaves for rows.
  const dayCount = grid ? grid.dayKeys.length : 1;
  const rowCount = grid ? grid.rows.length : 1;
  const availW = Math.max(0, size.w - labelW - SIDE_PAD);
  const availH = Math.max(0, size.h - TOP - BOTTOM);
  const cellW = grid && size.w ? Math.min(64, Math.max(18, availW / dayCount)) : 20;
  const cellH = grid && size.h ? Math.min(48, Math.max(24, availH / rowCount)) : 26;
  const gridW = grid ? grid.dayKeys.length * cellW : 0;
  const gridH = grid ? grid.rows.length * cellH : 0;
  const showEveryNth = days <= 14 ? 1 : days <= 30 ? 5 : 10;

  return (
    <VizFrame
      theme={theme}
      kicker={`Continuous evidence · ${dim.label} Heat Grid`}
      sub={`Total event density by ${dim.label} and day over the last ${days} days, reviewed and unreviewed alike — color intensity is volume; the red tick marks a day with at least one escalation. Click a row to open it in Adjudications.`}
      error={error && !hasData ? error : null}
      empty={!loading && !hasData && !error ? `No events in the last ${days} days yet.` : null}
      loading={loading && !hasData}
      height={Math.max(440, rowCount * 34 + 90)}
    >
      {hasData && (
        <div ref={hostRef} style={{ position: "absolute", inset: 0, overflow: "auto", padding: "16px 16px 12px" }}>
          <svg width={Math.max(size.w - SIDE_PAD, labelW + gridW)} height={gridH + TOP + BOTTOM} style={{ display: "block" }}
            role="img"
            aria-label={`${dim.label} Heat Grid — event density for ${grid.rows.length} ${dim.label.toLowerCase()} groups over ${grid.dayKeys.length} days. See Event Replay's table view for this data in an accessible form.`}>
            {grid.rows.map((row, ri) => (
              <g key={row.key} transform={`translate(0, ${ri * cellH + TOP})`}
                onClick={() => onNavigate && onNavigate("ubogov", { cemTab: "adjudications", cemFilter: groupCemFilter(dim, row.key) })}
                style={{ cursor: onNavigate ? "pointer" : "default" }}>
                <rect x={0} y={0} width={labelW - 10} height={cellH - 3} rx={3} fill={theme["surface-2"]} />
                <circle cx={9} cy={(cellH - 3) / 2} r={3.5} fill={dim.color(row.key)} />
                <text x={18} y={(cellH - 3) / 2 + 3.5} fontSize={10.5} fontWeight={600} fill={theme.ink} fontFamily="system-ui, sans-serif">
                  {row.key.length > 24 ? row.key.slice(0, 23) + "…" : row.key}
                </text>
                <text x={labelW - 16} y={(cellH - 3) / 2 + 3.5} fontSize={9.5} fontWeight={700} fill={theme["ink-2"]} textAnchor="end" fontFamily="monospace">
                  {row.total}
                </text>
                {row.cells.map((c, ci) => {
                  const intensity = grid.maxCell ? c.total / grid.maxCell : 0;
                  const alpha = c.total === 0 ? 0.05 : 0.18 + intensity * 0.72;
                  return (
                    <g key={c.date}
                      onMouseEnter={e => setHover({ x: e.clientX, y: e.clientY, key: row.key, cell: c })}
                      onMouseMove={e => setHover(h => h ? { ...h, x: e.clientX, y: e.clientY } : null)}
                      onMouseLeave={() => setHover(null)}
                      style={{ cursor: "pointer" }}>
                      <rect x={labelW + ci * cellW} y={0} width={cellW - 2} height={cellH - 3} rx={2}
                        fill={theme.acc} fillOpacity={alpha} stroke={theme["surface-2"]} strokeWidth={1} />
                      {c.escalated > 0 && (
                        <rect x={labelW + ci * cellW} y={cellH - 6} width={cellW - 2} height={3} fill={VERDICT_COLOR.ESCALATE} />
                      )}
                    </g>
                  );
                })}
              </g>
            ))}
            {grid.dayKeys.map((k, ci) => (
              ci % showEveryNth === 0 && (
                <text key={k} x={labelW + ci * cellW + (cellW - 2) / 2} y={gridH + TOP + 14}
                  fontSize={9} fill={theme["ink-3"]} textAnchor="middle" fontFamily="monospace">
                  {_fmtDay(k)}
                </text>
              )
            ))}
          </svg>
        </div>
      )}
      {hover && (
        <div style={{
          position: "fixed",
          left: hover.x > window.innerWidth - 240 ? hover.x - 14 : hover.x + 14,
          transform: hover.x > window.innerWidth - 240 ? "translateX(-100%)" : "none",
          top: hover.y - 10,
          background: theme.surface, border: `1px solid ${dim.color(hover.key)}`,
          borderRadius: 7, padding: "8px 12px", zIndex: 9999, pointerEvents: "none",
          boxShadow: "0 6px 32px oklch(0% 0 0 / .4)", fontFamily: "system-ui, sans-serif",
        }}>
          <div style={{ fontSize: 10, color: dim.color(hover.key), fontWeight: 700 }}>{hover.key}</div>
          <div style={{ fontSize: 11, color: theme.ink, marginTop: 3 }}>{_fmtDay(hover.cell.date)}</div>
          <div style={{ fontSize: 10, color: theme["ink-2"], marginTop: 3 }}>
            <span style={{ color: theme.ink, fontWeight: 700 }}>{hover.cell.total}</span> event{hover.cell.total !== 1 ? "s" : ""}
            {hover.cell.escalated > 0 && <span style={{ color: theme["red-ink"] }}> · {hover.cell.escalated} escalated</span>}
          </div>
        </div>
      )}
    </VizFrame>
  );
}

/* ════════════════════════════════════════════════════════════════════════
   4. Dimension Flow Graph — animated Directly-Follows Graph (dagre + D3)
   ════════════════════════════════════════════════════════════════════════ */

// Pulls a display label for the specific rule/control a violation cites,
// mirroring pol_domain_mappings' own parsing of the two live vocabularies
// (bracketed "[POL-X:SEV] msg" and bare control_ids like "OTC-P005") —
// display-only, not a re-derivation of domain assignment.
function primaryRuleLabel(violations) {
  if (!violations || !violations.length) return null;
  const v = violations[0];
  const bracketed = /^\[(POL-[A-Z0-9-]+):/.exec(v);
  if (bracketed) return bracketed[1];
  if (/^[A-Z][A-Z0-9-]{2,}$/.test(v)) return v;
  return v.length > 22 ? v.slice(0, 21) + "…" : v;
}

const DFG_MAX_RULE_NODES = 10;

function buildDfgGraph(events, dim) {
  const nodeMeta = new Map(); // id -> { label, kind }
  const edgeCount = new Map(); // "src|tgt" -> count
  const ruleTotals = new Map(); // rule label -> count

  function node(id, label, kind) {
    if (!nodeMeta.has(id)) nodeMeta.set(id, { id, label, kind, value: 0 });
    nodeMeta.get(id).value += 1; // one event passing through this node
    return id;
  }
  function edge(src, tgt) {
    const k = `${src}|${tgt}`;
    edgeCount.set(k, (edgeCount.get(k) || 0) + 1);
  }

  // A NOT_REVIEWED event was never checked against any rule at all — it
  // stops at the verdict node, same as CLEAR, rather than piling into
  // "Unmapped rule" alongside events that genuinely escalated/monitored
  // with no rule mapped. Without this, NOT_REVIEWED's much larger volume
  // would swamp "Unmapped rule" and crowd out the real signal it exists for.
  function _needsRuleLabel(verdict) { return verdict !== "CLEAR" && verdict !== "NOT_REVIEWED"; }

  // First pass: find the top rule labels so long tails collapse to "Other".
  events.forEach(e => {
    const rule = primaryRuleLabel(e.policy_violations);
    const label = rule || (_needsRuleLabel(e.verdict) ? "Unmapped rule" : null);
    if (label) ruleTotals.set(label, (ruleTotals.get(label) || 0) + 1);
  });
  const topRules = new Set(
    [...ruleTotals.entries()].sort((a, b) => b[1] - a[1]).slice(0, DFG_MAX_RULE_NODES).map(r => r[0])
  );

  events.forEach(e => {
    const group = dim.extract(e);
    const tier = e.risk_tier || "UNKNOWN";
    const verdict = e.verdict || "UNKNOWN";
    const rawRule = primaryRuleLabel(e.policy_violations) || (_needsRuleLabel(verdict) ? "Unmapped rule" : null);
    const rule = rawRule && !topRules.has(rawRule) ? "Other" : rawRule;

    const gId = node(`${dim.key}:${group}`, group, dim.key);
    const tId = node(`t:${tier}`, tier, "tier");
    const vId = node(`v:${verdict}`, verdict, "verdict");
    edge(gId, tId);
    edge(tId, vId);
    if (rule) {
      const rId = node(`r:${rule}`, rule, "rule");
      edge(vId, rId);
    }
  });

  const nodes = [...nodeMeta.values()];
  const edges = [...edgeCount.entries()].map(([k, value]) => {
    const [source, target] = k.split("|");
    return { source, target, value };
  });
  return { nodes, edges };
}

function dfgNodeColor(n, theme, dim) {
  if (n.kind === dim.key) return dim.color(n.label);
  if (n.kind === "tier") return TIER_COLOR[n.label] || TIER_COLOR.UNKNOWN;
  if (n.kind === "verdict") return VERDICT_COLOR[n.label] || VERDICT_COLOR.UNKNOWN;
  return theme.acc;
}

/* ── Shared zoom/pan for SVG directly-follows graphs (DimensionFlowGraph,
   CaseFlowGraph). Previously both charts set the SVG's viewBox to the
   graph's own full layout size with no zoom — for a small graph that's
   fine, but a large one (many domains/tiers/rules, or a busy case-flow
   window) got scaled down to fit the fixed-height pane no matter how much
   room the pane actually had, shrinking labels to illegible. This keeps
   the SVG's rendered box fixed at the host pane's real pixel size and
   moves/scales the content within it instead — wheel to zoom, drag to
   pan, starting from a computed "whole graph, centered" transform so nothing
   opens pre-shrunk. `fitToView` is returned so a toolbar button can reset. ── */
function attachGraphZoom(svg, contentG, { graphWidth, graphHeight, hostWidth, hostHeight, minScale = 0.15, maxScale = 4 }) {
  const zoom = d3.zoom()
    .scaleExtent([minScale, maxScale])
    .on("zoom", event => contentG.attr("transform", event.transform));

  svg.call(zoom).on("dblclick.zoom", null); // double-click reserved for future node focus, not zoom-step

  function fitToView(animate = true) {
    const w = Math.max(1, graphWidth), h = Math.max(1, graphHeight);
    const scale = Math.min(maxScale, Math.max(minScale, Math.min(hostWidth / w, hostHeight / h) * 0.94));
    const tx = (hostWidth - w * scale) / 2;
    const ty = (hostHeight - h * scale) / 2;
    const t = d3.zoomIdentity.translate(tx, ty).scale(scale);
    (animate ? svg.transition().duration(320) : svg).call(zoom.transform, t);
  }

  function step(factor) {
    svg.transition().duration(180).call(zoom.scaleBy, factor);
  }

  fitToView(false);
  return { zoom, fitToView, zoomIn: () => step(1.4), zoomOut: () => step(1 / 1.4) };
}

// Small on-pane control cluster — zoom in/out/fit plus an "Expand" toggle
// that grows the VizFrame pane itself (see height state in each chart
// below) rather than just the SVG content, for graphs too busy to read
// comfortably even at a good zoom level inside the default pane height.
function GraphZoomToolbar({ theme, onZoomIn, onZoomOut, onFit, expanded, onToggleExpand }) {
  const btnStyle = {
    width: 24, height: 24, display: "flex", alignItems: "center", justifyContent: "center",
    border: `1px solid ${theme.line}`, borderRadius: 5, background: theme.surface, color: theme["ink-2"],
    cursor: "pointer", fontSize: 13, fontWeight: 600, lineHeight: 1, padding: 0,
  };
  return (
    <div style={{
      position: "absolute", top: 10, right: 10, zIndex: 5, display: "flex", gap: 4,
      background: theme["surface-2"], border: `1px solid ${theme.line}`, borderRadius: 7, padding: 4,
      boxShadow: "0 2px 10px oklch(0% 0 0 / .18)",
    }}>
      <button type="button" title="Zoom in" onClick={onZoomIn} style={btnStyle}>+</button>
      <button type="button" title="Zoom out" onClick={onZoomOut} style={btnStyle}>−</button>
      <button type="button" title="Fit to view" onClick={onFit} style={{ ...btnStyle, fontSize: 10 }}>⤢</button>
      <button type="button" title={expanded ? "Collapse pane" : "Expand pane"} onClick={onToggleExpand}
        style={{ ...btnStyle, fontSize: 10, color: expanded ? theme.acc : theme["ink-2"] }}>
        {expanded ? "⤓" : "⤒"}
      </button>
    </div>
  );
}

export function DimensionFlowGraph({ theme, days, dim, rawEvents, loading, error, onNavigate }) {
  const hostRef = useRef(null);
  const svgRef = useRef(null);
  const rafRef = useRef(null);
  const zoomApiRef = useRef(null);
  const [tooltip, setTooltip] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const paneHeight = expanded ? 900 : 620;

  const events = useMemo(() => rawEvents.map(e => ({ ...e })), [rawEvents]);
  const graph = useMemo(() => (events.length ? buildDfgGraph(events, dim) : null), [events, dim]);
  const hasData = !!graph && graph.nodes.length > 0;

  useEffect(() => {
    if (!svgRef.current || !hostRef.current || !hasData) return;
    if (rafRef.current) cancelAnimationFrame(rafRef.current);

    const reducedMotion = window.matchMedia
      ? window.matchMedia("(prefers-reduced-motion: reduce)").matches : false;

    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 10, ranksep: 80, marginx: 10, marginy: 10 });
    g.setDefaultEdgeLabel(() => ({}));
    graph.nodes.forEach(n => {
      const w = Math.min(180, Math.max(60, n.label.length * 6.4 + 20));
      g.setNode(n.id, { ...n, width: w, height: 22 });
    });
    graph.edges.forEach(e => g.setEdge(e.source, e.target, { value: e.value }));
    dagre.layout(g);

    // Graph's own natural size — no longer clamped to at-least-the-host-
    // width, since zoom/pan (not viewBox scale-to-fit) now reconciles graph
    // size against pane size; see attachGraphZoom.
    const W = (g.graph().width || 800) + 40;
    const H = (g.graph().height || 400) + 40;
    const hostW = hostRef.current.clientWidth;
    const hostH = hostRef.current.clientHeight;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("viewBox", null).attr("width", "100%").attr("height", "100%");
    // Background sits outside the zoom-content layer so it always fills the
    // pane at any zoom/pan position, rather than only covering the graph's
    // own bounds and leaving a gap when zoomed out past the content.
    svg.append("rect").attr("width", "100%").attr("height", "100%").attr("fill", theme.bg);
    const contentG = svg.append("g").attr("class", "zoom-content");

    const maxVal = Math.max(1, ...graph.edges.map(e => e.value));
    const lineGen = d3.line().x(d => d.x + 20).y(d => d.y + 20).curve(d3.curveBasis);

    const edgeG = contentG.append("g");
    const edgeSel = edgeG.selectAll("path").data(g.edges().map(e => ({ ...g.edge(e), _e: e }))).join("path")
      .attr("d", d => lineGen(d.points))
      .attr("fill", "none")
      .attr("stroke", d => dfgNodeColor(g.node(d._e.v), theme, dim))
      .attr("stroke-opacity", 0.28)
      .attr("stroke-width", d => Math.max(1, Math.sqrt(d.value / maxVal) * 7))
      .attr("cursor", "pointer")
      .on("mouseover", (evt, d) => {
        edgeSel.attr("stroke-opacity", o => o === d ? 0.85 : 0.08);
        setTooltip({
          x: evt.clientX, y: evt.clientY, isLink: true,
          sourceLabel: g.node(d._e.v).label, sourceColor: dfgNodeColor(g.node(d._e.v), theme, dim),
          targetLabel: g.node(d._e.w).label, targetColor: dfgNodeColor(g.node(d._e.w), theme, dim),
          value: d.value,
        });
      })
      .on("mousemove", evt => setTooltip(p => p ? { ...p, x: evt.clientX, y: evt.clientY } : null))
      .on("mouseout", () => { edgeSel.attr("stroke-opacity", 0.28); setTooltip(null); })
      .on("click", (evt, d) => {
        if (!onNavigate) return;
        const f = { ...(dfgNodeCemFilter(g.node(d._e.v), dim) || {}), ...(dfgNodeCemFilter(g.node(d._e.w), dim) || {}) };
        if (Object.keys(f).length) onNavigate("ubogov", { cemTab: "adjudications", cemFilter: f });
      });

    const nodeG = contentG.append("g");
    const nodeSel = nodeG.selectAll("g").data(g.nodes().map(id => g.node(id))).join("g")
      .attr("transform", d => `translate(${d.x - d.width / 2 + 20}, ${d.y - d.height / 2 + 20})`)
      .attr("cursor", "pointer");
    nodeSel.append("rect")
      .attr("width", d => d.width).attr("height", d => d.height).attr("rx", 3)
      .attr("fill", d => dfgNodeColor(d, theme, dim)).attr("fill-opacity", 0.85)
      .attr("stroke", d => dfgNodeColor(d, theme, dim)).attr("stroke-width", 0.5).attr("stroke-opacity", 0.5);
    nodeSel.append("text")
      .attr("x", d => d.width / 2).attr("y", d => d.height / 2)
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("fill", "#fff").attr("font-size", 9.5).attr("font-weight", 650)
      .attr("font-family", "system-ui, sans-serif").attr("pointer-events", "none")
      .text(d => d.label.length > 24 ? d.label.slice(0, 23) + "…" : d.label);
    nodeSel
      .attr("cursor", d => onNavigate && dfgNodeCemFilter(d, dim) ? "pointer" : "default")
      .on("mouseover", (evt, d) => {
        nodeSel.style("opacity", n => n.id === d.id ? 1 : 0.3);
        edgeSel.attr("stroke-opacity", e => (e._e.v === d.id || e._e.w === d.id) ? 0.85 : 0.05);
        setTooltip({ x: evt.clientX, y: evt.clientY, label: d.label, kind: d.kind, value: d.value, color: dfgNodeColor(d, theme, dim) });
      })
      .on("mousemove", evt => setTooltip(p => p ? { ...p, x: evt.clientX, y: evt.clientY } : null))
      .on("mouseout", () => { nodeSel.style("opacity", 1); edgeSel.attr("stroke-opacity", 0.28); setTooltip(null); })
      .on("click", (evt, d) => {
        if (!onNavigate) return;
        const f = dfgNodeCemFilter(d, dim);
        if (f) onNavigate("ubogov", { cemTab: "adjudications", cemFilter: f });
      });

    // Animated flow particles — one small dot per edge (up to 3, scaled by
    // relative volume), walked along the laid-out path each frame.
    if (!reducedMotion) {
      const particleSpecs = [];
      edgeSel.each(function (d) {
        const len = this.getTotalLength();
        if (!len) return;
        const n = Math.max(1, Math.min(3, Math.round((d.value / maxVal) * 3)));
        for (let i = 0; i < n; i++) {
          particleSpecs.push({ path: this, len, phase: i / n, color: dfgNodeColor(g.node(d._e.v), theme, dim) });
        }
      });
      const particleG = contentG.append("g");
      const particles = particleG.selectAll("circle").data(particleSpecs).join("circle")
        .attr("r", 2.4).attr("fill", p => p.color).attr("fill-opacity", 0.9);

      const SPEED = 0.00025; // fraction of path per ms
      function tick(ts) {
        particles.attr("transform", p => {
          const t = ((ts * SPEED + p.phase) % 1);
          const pt = p.path.getPointAtLength(t * p.len);
          return `translate(${pt.x}, ${pt.y})`; // path's own d already bakes in the +20 canvas offset
        });
        rafRef.current = requestAnimationFrame(tick);
      }
      rafRef.current = requestAnimationFrame(tick);
    }

    zoomApiRef.current = attachGraphZoom(svg, contentG, { graphWidth: W, graphHeight: H, hostWidth: hostW, hostHeight: hostH });

    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [graph, hasData, theme, dim, onNavigate, expanded]);

  // Re-fit (not a full rebuild) on window resize — the pane's own size can
  // change without `expanded` changing, e.g. the browser window resizing.
  useEffect(() => {
    function onResize() {
      if (!zoomApiRef.current || !hostRef.current) return;
      zoomApiRef.current.fitToView(false);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return (
    <VizFrame
      theme={theme}
      kicker={`Continuous evidence · ${dim.label} Flow Graph (DFG)`}
      sub={`Directly-follows graph of the last ${days} days: ${dim.label} → Risk Tier → Verdict → Rule, covering every observed event, not just reviewed ones — a NOT_REVIEWED verdict node is real volume never selected for adjudication. Edge width and moving particles both reflect real observed transition counts. Scroll to zoom, drag to pan. Click a ${dim.label.toLowerCase()}/tier/verdict node or edge to open that slice in Adjudications.`}
      error={error && !hasData ? error : null}
      empty={!loading && !hasData && !error ? `No events in the last ${days} days yet.` : null}
      loading={loading && !hasData}
      height={paneHeight}
    >
      {hasData && (
        <div ref={hostRef} style={{ position: "absolute", inset: 0, overflow: "hidden" }}>
          <svg ref={svgRef} role="img"
            aria-label={`${dim.label} directly-follows graph: ${dim.label} to Risk Tier to Verdict to Rule. See Event Replay's table view for this data in an accessible form.`}
            style={{ display: "block", width: "100%", height: "100%", cursor: "grab" }} />
          <GraphZoomToolbar theme={theme}
            onZoomIn={() => zoomApiRef.current?.zoomIn()}
            onZoomOut={() => zoomApiRef.current?.zoomOut()}
            onFit={() => zoomApiRef.current?.fitToView(true)}
            expanded={expanded} onToggleExpand={() => setExpanded(e => !e)} />
        </div>
      )}
      {tooltip && (
        <div style={{
          position: "fixed",
          // Same right-edge flip as EventReplayChart's tooltip — anchors by
          // the box's own right edge via translateX so it doesn't overflow
          // the viewport for nodes/edges near the chart's right side.
          left: tooltip.x > window.innerWidth - 280 ? tooltip.x - 16 : tooltip.x + 16,
          transform: tooltip.x > window.innerWidth - 280 ? "translateX(-100%)" : "none",
          top: tooltip.y - 10,
          background: theme.surface, border: `1px solid ${tooltip.isLink ? theme["line-strong"] : tooltip.color}`,
          borderRadius: 7, padding: "9px 13px", maxWidth: 260, zIndex: 9999, pointerEvents: "none",
          boxShadow: "0 6px 32px oklch(0% 0 0 / .4)", fontFamily: "system-ui, sans-serif",
        }}>
          {tooltip.isLink ? (
            <>
              <div style={{ fontSize: 11, color: theme.ink, fontWeight: 600 }}>
                <span style={{ color: tooltip.sourceColor }}>{tooltip.sourceLabel}</span>
                <span style={{ color: theme["ink-4"], margin: "0 6px" }}>→</span>
                <span style={{ color: tooltip.targetColor }}>{tooltip.targetLabel}</span>
              </div>
              <div style={{ marginTop: 5, fontSize: 10, color: theme["ink-2"] }}>
                <span style={{ color: theme.ink, fontWeight: 700 }}>{tooltip.value}</span> event{tooltip.value !== 1 ? "s" : ""}
              </div>
            </>
          ) : (
            <>
              <div style={{ fontSize: 9, color: tooltip.color, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5 }}>{tooltip.kind}</div>
              <div style={{ fontSize: 12, color: theme.ink, fontWeight: 600, marginBottom: 5 }}>{tooltip.label}</div>
              <div style={{ fontSize: 10, color: theme["ink-2"] }}>
                <span style={{ color: theme.ink, fontWeight: 700 }}>{tooltip.value}</span> event{tooltip.value !== 1 ? "s" : ""}
              </div>
            </>
          )}
        </div>
      )}
    </VizFrame>
  );
}

/* ════════════════════════════════════════════════════════════════════════
   5. Case Flow Graph — a REAL directly-follows graph: "step A immediately
   preceded step B within the same tracked transaction," built from case_id/
   process_step (populated today only by generate_o2c_p2p_synthetic_log.py's
   linked O2C/P2P lifecycles — see that script and adjudicated_tool_calls'
   case_id column comment in db.py). Independent of the Domain/Source System
   dimension split above: a case's steps are what they are regardless of how
   any single step's event happens to be domain- or system-classified.
   ════════════════════════════════════════════════════════════════════════ */

const CASE_FLOW_START = "▶ Start";
const CASE_FLOW_END = "■ End";
const _stepColorCache = new Map();
function stepColor(label, theme) {
  if (label === CASE_FLOW_START) return theme["ink-3"];
  if (label === CASE_FLOW_END) return theme.acc;
  if (!_stepColorCache.has(label)) _stepColorCache.set(label, CATEGORICAL_PALETTE[_stepColorCache.size % CATEGORICAL_PALETTE.length]);
  return _stepColorCache.get(label);
}

function buildCaseDfgGraph(events) {
  const byCase = new Map();
  events.forEach(e => {
    if (!e.case_id) return;
    if (!byCase.has(e.case_id)) byCase.set(e.case_id, []);
    byCase.get(e.case_id).push(e);
  });
  if (!byCase.size) return null;

  const nodeMeta = new Map(); // label -> { id, label, value }
  const edgeCount = new Map(); // "src|tgt" -> count
  function node(label) {
    if (!nodeMeta.has(label)) nodeMeta.set(label, { id: `s:${label}`, label, value: 0 });
    const n = nodeMeta.get(label);
    n.value += 1;
    return n.id;
  }
  function edge(src, tgt) {
    const k = `${src}|${tgt}`;
    edgeCount.set(k, (edgeCount.get(k) || 0) + 1);
  }

  byCase.forEach(stepsRaw => {
    const steps = [...stepsRaw].sort((a, b) => new Date(a.adjudicated_at) - new Date(b.adjudicated_at));
    let prevId = node(CASE_FLOW_START);
    steps.forEach(s => {
      const id = node(s.process_step || "Unknown step");
      edge(prevId, id);
      prevId = id;
    });
    edge(prevId, node(CASE_FLOW_END));
  });

  const nodes = [...nodeMeta.values()];
  const edges = [...edgeCount.entries()].map(([k, value]) => {
    const [source, target] = k.split("|");
    return { source, target, value };
  });
  return { nodes, edges, caseCount: byCase.size };
}

export function CaseFlowGraph({ theme, days, rawEvents, loading, error }) {
  const hostRef = useRef(null);
  const svgRef = useRef(null);
  const rafRef = useRef(null);
  const zoomApiRef = useRef(null);
  const [tooltip, setTooltip] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const paneHeight = expanded ? 900 : 520;

  const graph = useMemo(() => buildCaseDfgGraph(rawEvents), [rawEvents]);
  const hasData = !!graph && graph.nodes.length > 0;

  useEffect(() => {
    if (!svgRef.current || !hostRef.current || !hasData) return;
    if (rafRef.current) cancelAnimationFrame(rafRef.current);

    const reducedMotion = window.matchMedia
      ? window.matchMedia("(prefers-reduced-motion: reduce)").matches : false;

    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 14, ranksep: 90, marginx: 10, marginy: 10 });
    g.setDefaultEdgeLabel(() => ({}));
    graph.nodes.forEach(n => {
      const w = Math.min(200, Math.max(70, n.label.length * 6.6 + 24));
      g.setNode(n.id, { ...n, width: w, height: 24 });
    });
    graph.edges.forEach(e => g.setEdge(e.source, e.target, { value: e.value }));
    dagre.layout(g);

    // Graph's own natural size — zoom/pan (not viewBox scale-to-fit)
    // reconciles it against the pane; see attachGraphZoom.
    const W = (g.graph().width || 800) + 40;
    const H = (g.graph().height || 300) + 40;
    const hostW = hostRef.current.clientWidth;
    const hostH = hostRef.current.clientHeight;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("viewBox", null).attr("width", "100%").attr("height", "100%");
    svg.append("rect").attr("width", "100%").attr("height", "100%").attr("fill", theme.bg);
    const contentG = svg.append("g").attr("class", "zoom-content");

    const maxVal = Math.max(1, ...graph.edges.map(e => e.value));
    const lineGen = d3.line().x(d => d.x + 20).y(d => d.y + 20).curve(d3.curveBasis);

    const edgeG = contentG.append("g");
    const edgeSel = edgeG.selectAll("path").data(g.edges().map(e => ({ ...g.edge(e), _e: e }))).join("path")
      .attr("d", d => lineGen(d.points))
      .attr("fill", "none")
      .attr("stroke", d => stepColor(g.node(d._e.v).label, theme))
      .attr("stroke-opacity", 0.3)
      .attr("stroke-width", d => Math.max(1, Math.sqrt(d.value / maxVal) * 8))
      .on("mouseover", (evt, d) => {
        edgeSel.attr("stroke-opacity", o => o === d ? 0.9 : 0.08);
        setTooltip({
          x: evt.clientX, y: evt.clientY, isLink: true,
          sourceLabel: g.node(d._e.v).label, sourceColor: stepColor(g.node(d._e.v).label, theme),
          targetLabel: g.node(d._e.w).label, targetColor: stepColor(g.node(d._e.w).label, theme),
          value: d.value,
        });
      })
      .on("mousemove", evt => setTooltip(p => p ? { ...p, x: evt.clientX, y: evt.clientY } : null))
      .on("mouseout", () => { edgeSel.attr("stroke-opacity", 0.3); setTooltip(null); });

    const nodeG = contentG.append("g");
    const nodeSel = nodeG.selectAll("g").data(g.nodes().map(id => g.node(id))).join("g")
      .attr("transform", d => `translate(${d.x - d.width / 2 + 20}, ${d.y - d.height / 2 + 20})`);
    nodeSel.append("rect")
      .attr("width", d => d.width).attr("height", d => d.height).attr("rx", 4)
      .attr("fill", d => stepColor(d.label, theme)).attr("fill-opacity", 0.85)
      .attr("stroke", d => stepColor(d.label, theme)).attr("stroke-width", 0.5).attr("stroke-opacity", 0.6);
    nodeSel.append("text")
      .attr("x", d => d.width / 2).attr("y", d => d.height / 2)
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("fill", "#fff").attr("font-size", 10).attr("font-weight", 650)
      .attr("font-family", "system-ui, sans-serif").attr("pointer-events", "none")
      .text(d => d.label.length > 26 ? d.label.slice(0, 25) + "…" : d.label);
    nodeSel
      .on("mouseover", (evt, d) => {
        nodeSel.style("opacity", n => n.id === d.id ? 1 : 0.3);
        edgeSel.attr("stroke-opacity", e => (e._e.v === d.id || e._e.w === d.id) ? 0.9 : 0.05);
        setTooltip({ x: evt.clientX, y: evt.clientY, label: d.label, kind: "step", value: d.value, color: stepColor(d.label, theme) });
      })
      .on("mousemove", evt => setTooltip(p => p ? { ...p, x: evt.clientX, y: evt.clientY } : null))
      .on("mouseout", () => { nodeSel.style("opacity", 1); edgeSel.attr("stroke-opacity", 0.3); setTooltip(null); });

    // Animated flow particles, same technique as DimensionFlowGraph.
    if (!reducedMotion) {
      const particleSpecs = [];
      edgeSel.each(function (d) {
        const len = this.getTotalLength();
        if (!len) return;
        const n = Math.max(1, Math.min(3, Math.round((d.value / maxVal) * 3)));
        for (let i = 0; i < n; i++) {
          particleSpecs.push({ path: this, len, phase: i / n, color: stepColor(g.node(d._e.v).label, theme) });
        }
      });
      const particleG = contentG.append("g");
      const particles = particleG.selectAll("circle").data(particleSpecs).join("circle")
        .attr("r", 2.6).attr("fill", p => p.color).attr("fill-opacity", 0.9);
      const SPEED = 0.00022;
      function tick(ts) {
        particles.attr("transform", p => {
          const t = (ts * SPEED + p.phase) % 1;
          const pt = p.path.getPointAtLength(t * p.len);
          return `translate(${pt.x}, ${pt.y})`;
        });
        rafRef.current = requestAnimationFrame(tick);
      }
      rafRef.current = requestAnimationFrame(tick);
    }

    zoomApiRef.current = attachGraphZoom(svg, contentG, { graphWidth: W, graphHeight: H, hostWidth: hostW, hostHeight: hostH });

    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [graph, hasData, theme, expanded]);

  // Re-fit (not a full rebuild) on window resize.
  useEffect(() => {
    function onResize() {
      if (!zoomApiRef.current || !hostRef.current) return;
      zoomApiRef.current.fitToView(false);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return (
    <VizFrame
      theme={theme}
      kicker="Continuous evidence · Case Flow Graph (real DFG)"
      sub={graph
        ? `${graph.caseCount} tracked transaction${graph.caseCount !== 1 ? "s" : ""} in the last ${days} days — real step-to-step sequences, not a categorical breakdown. Populated today by the O2C/P2P/Inventory Cycle synthetic generator; a real ERP connector emitting case_id/process_step would appear here the same way. Scroll to zoom, drag to pan.`
        : `Real transaction lifecycles over the last ${days} days, traced step by step — needs events carrying a case_id (see generate_o2c_p2p_synthetic_log.py).`}
      error={error && !hasData ? error : null}
      empty={!loading && !hasData && !error ? `No case-tracked transactions in the last ${days} days yet — run generate_o2c_p2p_synthetic_log.py to populate this view, or wait for a real case-tracked producer.` : null}
      loading={loading && !hasData}
      height={paneHeight}
    >
      {hasData && (
        <div ref={hostRef} style={{ position: "absolute", inset: 0, overflow: "hidden" }}>
          <svg ref={svgRef} role="img"
            aria-label={`Case Flow Graph — ${graph.caseCount} tracked transaction${graph.caseCount !== 1 ? "s" : ""} over the last ${days} days, plotted as a step-to-step directly-follows graph.`}
            style={{ display: "block", width: "100%", height: "100%", cursor: "grab" }} />
          <GraphZoomToolbar theme={theme}
            onZoomIn={() => zoomApiRef.current?.zoomIn()}
            onZoomOut={() => zoomApiRef.current?.zoomOut()}
            onFit={() => zoomApiRef.current?.fitToView(true)}
            expanded={expanded} onToggleExpand={() => setExpanded(e => !e)} />
        </div>
      )}
      {tooltip && (
        <div style={{
          position: "fixed",
          // Same right-edge flip as EventReplayChart's tooltip — anchors by
          // the box's own right edge via translateX so it doesn't overflow
          // the viewport for nodes/edges near the chart's right side.
          left: tooltip.x > window.innerWidth - 280 ? tooltip.x - 16 : tooltip.x + 16,
          transform: tooltip.x > window.innerWidth - 280 ? "translateX(-100%)" : "none",
          top: tooltip.y - 10,
          background: theme.surface, border: `1px solid ${tooltip.isLink ? theme["line-strong"] : tooltip.color}`,
          borderRadius: 7, padding: "9px 13px", maxWidth: 260, zIndex: 9999, pointerEvents: "none",
          boxShadow: "0 6px 32px oklch(0% 0 0 / .4)", fontFamily: "system-ui, sans-serif",
        }}>
          {tooltip.isLink ? (
            <>
              <div style={{ fontSize: 11, color: theme.ink, fontWeight: 600 }}>
                <span style={{ color: tooltip.sourceColor }}>{tooltip.sourceLabel}</span>
                <span style={{ color: theme["ink-4"], margin: "0 6px" }}>→</span>
                <span style={{ color: tooltip.targetColor }}>{tooltip.targetLabel}</span>
              </div>
              <div style={{ marginTop: 5, fontSize: 10, color: theme["ink-2"] }}>
                <span style={{ color: theme.ink, fontWeight: 700 }}>{tooltip.value}</span> case{tooltip.value !== 1 ? "s" : ""} took this step
              </div>
            </>
          ) : (
            <>
              <div style={{ fontSize: 9, color: tooltip.color, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5 }}>{tooltip.kind}</div>
              <div style={{ fontSize: 12, color: theme.ink, fontWeight: 600, marginBottom: 5 }}>{tooltip.label}</div>
              <div style={{ fontSize: 10, color: theme["ink-2"] }}>
                <span style={{ color: theme.ink, fontWeight: 700 }}>{tooltip.value}</span> case{tooltip.value !== 1 ? "s" : ""} passed through
              </div>
            </>
          )}
        </div>
      )}
    </VizFrame>
  );
}

/* ════════════════════════════════════════════════════════════════════════
   6-8. Process Mining — Variants, Conformance, Cycle Time. The Case Flow
   Graph above is a directly-follows GRAPH (topology: which step follows
   which, and how often). These three answer what the graph alone can't:
   which path is actually "normal" and how often reality deviates from it
   (Variants), exactly how a deviating case differs from its documented
   process template (Conformance), and where time actually accumulates
   (Cycle Time). Server-computed by process_mining_tool.py via
   GET /process-mining/* (see that module's docstring) rather than
   re-aggregated client-side like the four dimension-based charts above —
   variant/conformance/cycle-time logic is genuinely stateful reasoning
   (template matching, order comparison), not a simple group-by.
   ════════════════════════════════════════════════════════════════════════ */

function useProcessList() {
  const [processes, setProcesses] = useState([]);
  useEffect(() => {
    window.MCP.pmListProcesses().then(d => setProcesses(d.processes || [])).catch(() => {});
  }, []);
  return processes;
}

function ProcessFilterSelect({ value, onChange, processes }) {
  return (
    <select value={value || ""} onChange={e => onChange(e.target.value || null)}
      style={{ fontSize: 11, padding: "3px 6px", borderRadius: 4, border: "1px solid var(--line)", background: "var(--surface)", color: "var(--ink)" }}>
      <option value="">All processes</option>
      {processes.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
    </select>
  );
}

function _pmFmtHours(h) {
  if (h == null) return "—";
  if (h < 24) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

function _pmUsePmFetch(fetcher, days, process) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  useEffect(() => {
    setState(s => ({ ...s, loading: true }));
    fetcher(days, process)
      .then(d => setState({ data: d, loading: false, error: d.note || null }))
      .catch(e => setState({ data: null, loading: false, error: e.message || String(e) }));
  }, [days, process]);
  return state;
}

export function ProcessVariantsView({ theme, days }) {
  const processes = useProcessList();
  const [process, setProcess] = useState(null);
  const { data, loading, error } = _pmUsePmFetch((d, p) => window.MCP.pmVariants(d, p), days, process);
  const variants = data?.variants || [];
  const hasData = variants.length > 0;

  return (
    <VizFrame theme={theme} height={520}
      kicker="Process mining · Variants — every path actually taken"
      sub="Distinct step sequences observed over the trailing window, most frequent first. The top row is the happy path; a green tag means it also matches the documented process template exactly — the two can diverge when the 'normal' path has quietly drifted."
      controls={<ProcessFilterSelect value={process} onChange={setProcess} processes={processes} />}
      error={error} empty={!loading && !hasData ? "No case-tracked transactions in this window yet — see generate_o2c_p2p_synthetic_log.py." : null}
      loading={loading && !hasData}>
      {hasData && (
        <div style={{ position: "absolute", inset: 0, overflow: "auto", padding: 12 }}>
          {variants.map((v, i) => (
            <div key={v.variant} style={{
              display: "flex", alignItems: "center", gap: 12, padding: "9px 10px", marginBottom: 6,
              borderRadius: 6, border: `1px solid ${theme.line}`,
              background: v.is_happy_path ? theme["surface-2"] : theme.surface,
            }}>
              <div style={{ width: 22, textAlign: "center", fontSize: 10, fontWeight: 700, color: theme["ink-3"] }}>#{i + 1}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: 11.5, color: theme.ink, fontWeight: v.is_happy_path ? 700 : 500,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {v.steps.join("  →  ")}
                </div>
                <div style={{ fontSize: 10, color: theme["ink-4"], marginTop: 2 }}>
                  {v.process_label || "Untemplated"}
                  {v.is_happy_path && <span style={{ color: theme.acc, fontWeight: 600 }}> · happy path</span>}
                  {v.process && (v.is_canonical
                    ? <span style={{ color: theme["green-ink"] }}> · matches template</span>
                    : <span style={{ color: theme["amber-ink"] }}> · deviates from template</span>)}
                </div>
              </div>
              <div style={{ textAlign: "right", minWidth: 66 }}>
                <div className="mono" style={{ fontSize: 13, fontWeight: 700, color: theme.ink }}>{v.case_count}</div>
                <div style={{ fontSize: 9.5, color: theme["ink-4"] }}>{(v.pct_of_cases * 100).toFixed(0)}% of cases</div>
              </div>
              <div style={{ textAlign: "right", minWidth: 60 }}>
                <div className="mono" style={{ fontSize: 11, color: v.violation_rate > 0 ? theme["red-ink"] : theme["ink-3"] }}>
                  {(v.violation_rate * 100).toFixed(0)}%
                </div>
                <div style={{ fontSize: 9.5, color: theme["ink-4"] }}>violation rate</div>
              </div>
              <div style={{ textAlign: "right", minWidth: 56 }}>
                <div className="mono" style={{ fontSize: 11, color: theme["ink-2"] }}>{_pmFmtHours(v.avg_duration_hours)}</div>
                <div style={{ fontSize: 9.5, color: theme["ink-4"] }}>avg duration</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </VizFrame>
  );
}

const _PM_DEVIATION_LABELS = {
  missing_step: "Missing step", extra_step: "Extra step",
  repeated_step: "Repeated step (rework)", out_of_order: "Out of order",
};

export function ProcessConformanceView({ theme, days }) {
  const processes = useProcessList();
  const [process, setProcess] = useState(null);
  const { data, loading, error } = _pmUsePmFetch((d, p) => window.MCP.pmConformance(d, p), days, process);
  const hasData = !!data && data.scored_cases > 0;
  const rate = data?.conformance_rate;

  return (
    <VizFrame theme={theme} height={520}
      kicker="Process mining · Conformance — does reality match the documented process?"
      sub="Every case whose steps matched a known process template (Procure to Pay, Order to Cash, Receive to Ship), scored against that template's exact order. A case with no matching template (most standalone events) is excluded here, not silently counted as conforming."
      controls={<ProcessFilterSelect value={process} onChange={setProcess} processes={processes} />}
      error={error} empty={!loading && !hasData ? "No template-matched cases in this window yet." : null}
      loading={loading && !hasData}>
      {hasData && (
        <div style={{ position: "absolute", inset: 0, overflow: "auto", padding: 14 }}>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
            <div style={{ minWidth: 140, padding: "10px 14px", borderRadius: 6, border: `1px solid ${theme.line}`, background: theme["surface-2"] }}>
              <div style={{ fontSize: 9.5, color: theme["ink-4"], textTransform: "uppercase", letterSpacing: "0.05em" }}>Conformance rate</div>
              <div className="mono" style={{ fontSize: 22, fontWeight: 700, color: rate >= 0.9 ? theme["green-ink"] : rate >= 0.6 ? theme["amber-ink"] : theme["red-ink"] }}>
                {rate == null ? "—" : `${(rate * 100).toFixed(0)}%`}
              </div>
              <div style={{ fontSize: 10, color: theme["ink-3"] }}>{data.conforming_cases} / {data.scored_cases} cases</div>
            </div>
            {Object.entries(data.deviation_breakdown).map(([k, v]) => (
              <div key={k} style={{ minWidth: 110, padding: "10px 14px", borderRadius: 6, border: `1px solid ${theme.line}`, background: theme.surface }}>
                <div style={{ fontSize: 9.5, color: theme["ink-4"], textTransform: "uppercase", letterSpacing: "0.05em" }}>{_PM_DEVIATION_LABELS[k]}</div>
                <div className="mono" style={{ fontSize: 18, fontWeight: 700, color: v > 0 ? theme["amber-ink"] : theme["ink-2"] }}>{v}</div>
              </div>
            ))}
          </div>

          <div style={{ fontSize: 10, color: theme["ink-4"], textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
            Deviating cases ({data.deviating_cases.length})
          </div>
          {!data.deviating_cases.length ? (
            <div style={{ fontSize: 12, color: theme["green-ink"] }}>Every scored case matched its process template exactly.</div>
          ) : data.deviating_cases.map(c => (
            <div key={c.case_id} style={{ padding: "8px 10px", marginBottom: 5, borderRadius: 6, border: `1px solid ${theme.line}`, background: theme.surface }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                <span className="mono" style={{ fontSize: 11.5, fontWeight: 700, color: theme.ink }}>{c.case_id}</span>
                <span style={{ fontSize: 10, color: theme["ink-4"] }}>{c.process_label}</span>
              </div>
              <div style={{ fontSize: 10.5, color: theme["ink-3"], marginTop: 3 }}>
                {c.missing_steps.length > 0 && <span style={{ marginRight: 10, color: theme["amber-ink"] }}>Missing: {c.missing_steps.join(", ")}</span>}
                {c.extra_steps.length > 0 && <span style={{ marginRight: 10, color: theme["amber-ink"] }}>Extra: {c.extra_steps.join(", ")}</span>}
                {c.repeated_steps.length > 0 && <span style={{ marginRight: 10, color: theme["red-ink"] }}>Rework: {c.repeated_steps.join(", ")}</span>}
                {c.out_of_order && <span style={{ color: theme["red-ink"] }}>Out of order</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </VizFrame>
  );
}

export function ProcessCycleTimeView({ theme, days }) {
  const processes = useProcessList();
  const [process, setProcess] = useState(null);
  const { data, loading, error } = _pmUsePmFetch((d, p) => window.MCP.pmCycleTimes(d, p), days, process);
  const edges = data?.edges || [];
  const hasData = edges.length > 0;
  const maxHours = Math.max(1, ...edges.map(e => e.avg_hours));

  return (
    <VizFrame theme={theme} height={520}
      kicker="Process mining · Cycle Time — where time actually accumulates"
      sub="Mean/median/p90 duration for every step-to-step transition, slowest first — the bottleneck, as opposed to the Case Flow Graph's edge width, which shows volume, not speed."
      controls={<ProcessFilterSelect value={process} onChange={setProcess} processes={processes} />}
      error={error} empty={!loading && !hasData ? "No case-tracked transitions in this window yet." : null}
      loading={loading && !hasData}>
      {hasData && (
        <div style={{ position: "absolute", inset: 0, overflow: "auto", padding: 14 }}>
          {data.case_duration && (
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
              {[["mean_hours", "Mean case duration"], ["median_hours", "Median"], ["p90_hours", "P90"]].map(([k, label]) => (
                <div key={k} style={{ minWidth: 120, padding: "10px 14px", borderRadius: 6, border: `1px solid ${theme.line}`, background: theme["surface-2"] }}>
                  <div style={{ fontSize: 9.5, color: theme["ink-4"], textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
                  <div className="mono" style={{ fontSize: 18, fontWeight: 700, color: theme.ink }}>{_pmFmtHours(data.case_duration[k])}</div>
                </div>
              ))}
            </div>
          )}
          {edges.map((e, i) => (
            <div key={`${e.source}|${e.target}`} style={{ marginBottom: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 3 }}>
                <span style={{ color: theme.ink }}>
                  {e.source} <span style={{ color: theme["ink-4"] }}>→</span> {e.target}
                  {i === 0 && <span style={{ marginLeft: 8, fontSize: 9.5, color: theme["red-ink"], fontWeight: 700, textTransform: "uppercase" }}>Bottleneck</span>}
                </span>
                <span className="mono" style={{ color: theme["ink-2"] }}>{_pmFmtHours(e.avg_hours)} avg · {e.count} case{e.count !== 1 ? "s" : ""}</span>
              </div>
              <div style={{ height: 7, borderRadius: 4, background: theme["surface-2"], overflow: "hidden" }}>
                <div style={{
                  height: "100%", width: `${Math.max(2, (e.avg_hours / maxHours) * 100)}%`,
                  background: i === 0 ? theme["red-ink"] : theme.acc, borderRadius: 4,
                }} />
              </div>
            </div>
          ))}
        </div>
      )}
    </VizFrame>
  );
}

/* ════════════════════════════════════════════════════════════════════════
   Journal Entry Testing — je_testing_tool.py's deterministic anomaly rules
   (round-dollar, weekend/after-hours, preparer==approver SoD, threshold-
   unapproved, rare account, unusual description, velocity spike) over real
   GL journal entries. Unlike the other tabs above, this isn't a chart of
   adjudicated events — it's a findings list + disposition workflow, same
   shape as exceptions.jsx's Triage Queue, reading the SAME underlying
   exception_control_events table (discriminated by event_type =
   'JOURNAL_ENTRY') but through je_testing_endpoints.py's own ungated
   endpoints rather than Exception Management's dev-only router.
   ════════════════════════════════════════════════════════════════════════ */

// Shared with Exception Management (components.jsx's ATTENTION_* constants) —
// both queues resolve to the same 4 root causes over the same backend
// tables; see that file's comment for why this isn't duplicated per screen
// anymore (the "unify the queue" UX-audit recommendation).
const _JE_RESOLUTION_LABELS = ATTENTION_RESOLUTION_LABELS;
const _JE_NOTES_REQUIRED = ATTENTION_NOTES_REQUIRED;

function JeSeverityBadge({ severity, theme }) {
  const color = severity === "CRITICAL" || severity === "HIGH" ? theme["red-ink"]
    : severity === "MEDIUM" ? theme["amber-ink"] : theme["ink-3"];
  return (
    <span className="mono" style={{
      fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999,
      background: theme["surface-2"], color, border: `1px solid ${color}`,
    }}>{severity}</span>
  );
}

function JeFindingRow({ row, theme, onDisposed }) {
  const [expanded, setExpanded] = useState(false);
  const [label, setLabel] = useState(null);
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const f = row.finding || {};
  const needsNotes = label && _JE_NOTES_REQUIRED.has(label);
  const canSubmit = label && (!needsNotes || notes.trim().length > 0) && !submitting;
  const disposed = !!row.resolution_label;

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      await window.MCP.submitJeTestingDisposition(row.event_id, label, notes);
      onDisposed(row.event_id);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={{ border: `1px solid ${theme.line}`, borderRadius: 6, padding: "9px 11px", marginBottom: 6, background: theme.surface }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, cursor: "pointer" }}
        onClick={() => setExpanded(e => !e)}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 11.5, fontWeight: 600, color: theme.ink }}>
            {row.rule_id} <span style={{ fontWeight: 400, color: theme["ink-4"] }}>· {row.system_source}</span>
          </div>
          <div style={{ fontSize: 10, color: theme["ink-3"], marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {f.detail || "—"}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {disposed
            ? <span className="mono" style={{ fontSize: 9.5, color: theme["ink-4"] }}>{row.resolution_label}</span>
            : <JeSeverityBadge severity={f.severity} theme={theme} />}
          {f.amount != null && (
            <span className="mono" style={{ fontSize: 10, color: theme["ink-3"] }}>${Number(f.amount).toLocaleString()}</span>
          )}
        </div>
      </div>
      {expanded && (
        <div style={{ marginTop: 8, paddingTop: 8, borderTop: `1px solid ${theme.line}` }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, fontSize: 10.5, color: theme["ink-2"], marginBottom: 8 }}>
            <span>Preparer: <strong>{row.preparer || "—"}</strong></span>
            <span>Approver: <strong>{f.approver || "—"}</strong></span>
            <span>Account: <strong>{f.account || "—"}</strong></span>
            <span>Posted: <strong>{row.event_timestamp ? new Date(row.event_timestamp).toLocaleString() : "—"}</strong></span>
          </div>
          {!disposed ? (
            <>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                {_JE_RESOLUTION_LABELS.map(l => (
                  <button key={l.value} type="button" onClick={() => setLabel(l.value)} title={l.what}
                    style={{
                      fontSize: 10.5, padding: "5px 10px", borderRadius: 5, cursor: "pointer",
                      border: l.value === label ? `1px solid ${theme.acc}` : `1px solid ${theme.line}`,
                      background: l.value === label ? theme.acc : "transparent",
                      color: l.value === label ? "#fff" : theme["ink-2"],
                      fontWeight: l.value === label ? 600 : 400,
                    }}>
                    {l.label}
                  </button>
                ))}
              </div>
              {needsNotes && (
                <textarea rows={2} placeholder="Justification notes (required for this resolution)…"
                  value={notes} onChange={e => setNotes(e.target.value)}
                  style={{
                    width: "100%", fontSize: 11, marginBottom: 8, resize: "vertical", boxSizing: "border-box",
                    background: theme.surface, color: theme.ink, border: `1px solid ${theme.line}`, borderRadius: 4, padding: 6,
                  }} />
              )}
              {error && <div className="mono" style={{ fontSize: 10.5, color: theme["red-ink"], marginBottom: 8 }}>{error}</div>}
              <button className="btn btn-acc btn-sm" disabled={!canSubmit} onClick={handleSubmit}>
                {submitting ? "Submitting…" : "Resolve finding"}
              </button>
            </>
          ) : (
            <div style={{ fontSize: 10.5, color: theme["ink-4"] }}>
              Resolved {row.reviewed_at ? new Date(row.reviewed_at).toLocaleString() : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function JETestingView({ theme }) {
  const [summary, setSummary] = useState(null);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [ruleFilter, setRuleFilter] = useState("");
  const [onlyPending, setOnlyPending] = useState(true);
  // Grouped/bulk-resolve view — same curation lever Exception Management
  // has (recurring rule/system pairs collapsed into one row), sharing
  // AttentionGroupRow from components.jsx. The grouped listing is always
  // implicitly pending-only and doesn't support the rule/preparer filters
  // the flat list does, so those controls are hidden while grouped.
  const [grouped, setGrouped] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    return Promise.all([
      window.MCP.jeTestingSummary(),
      grouped
        ? window.MCP.jeTestingFindings({ group: true, limit: 200 })
        : window.MCP.jeTestingFindings({ ruleId: ruleFilter || null, onlyPending, limit: 100 }),
    ]).then(([s, f]) => { setSummary(s); setRows(f.rows || []); setError(null); })
      .catch(e => setError(e.message || String(e)))
      .finally(() => setLoading(false));
  }, [ruleFilter, onlyPending, grouped]);

  useEffect(() => { load(); }, [load]);

  function handleDisposed(eventId) {
    setRows(rs => rs.filter(r => r.event_id !== eventId));
  }

  function handleGroupResolved(eventId, group) {
    if (eventId === null) {
      setRows(rs => rs.filter(r => !(r.control_id === group.control_id && r.system_source === group.system_source)));
    }
  }

  const hasData = rows.length > 0;
  const ruleOptions = Object.keys(summary?.findings_by_rule || {});

  return (
    <VizFrame theme={theme} height={620}
      kicker="Journal Entry Testing — deterministic anomaly rules over real GL data"
      sub="je_testing_tool.py's rule engine (round-dollar, weekend/after-hours, preparer==approver SoD, threshold-unapproved, rare account, unusual description, velocity spike) run against journal entries pulled from every active financial connector — not a chart of adjudicated events like the other tabs, but the same real-data discipline: each row is a rule that actually fired against a real posting."
      controls={
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label style={{ fontSize: 10.5, color: theme["ink-3"], display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
            <input type="checkbox" checked={grouped} onChange={e => setGrouped(e.target.checked)} />
            Group by rule/system
          </label>
          {!grouped && (
            <>
              <select value={ruleFilter} onChange={e => setRuleFilter(e.target.value)}
                style={{ fontSize: 11, padding: "3px 6px", borderRadius: 4, border: `1px solid ${theme.line}`, background: theme.surface, color: theme.ink }}>
                <option value="">All rules</option>
                {ruleOptions.map(r => <option key={r} value={r}>{r} ({summary.findings_by_rule[r]})</option>)}
              </select>
              <label style={{ fontSize: 10.5, color: theme["ink-3"], display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                <input type="checkbox" checked={onlyPending} onChange={e => setOnlyPending(e.target.checked)} />
                Pending only
              </label>
            </>
          )}
        </div>
      }
      error={error}
      empty={!loading && !hasData ? "No JE findings match this filter right now — either nothing has fired yet, or everything's been resolved." : null}
      loading={loading && !hasData}>
      {(summary || hasData) && (
        <div style={{ position: "absolute", inset: 0, overflow: "auto", padding: 14 }}>
          {summary && (
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
              <div style={{ minWidth: 130, padding: "10px 14px", borderRadius: 6, border: `1px solid ${theme.line}`, background: theme["surface-2"] }}>
                <div style={{ fontSize: 9.5, color: theme["ink-4"], textTransform: "uppercase", letterSpacing: "0.05em" }}>Total findings</div>
                <div className="mono" style={{ fontSize: 20, fontWeight: 700, color: theme.ink }}>{summary.total_findings}</div>
              </div>
              <div style={{ minWidth: 130, padding: "10px 14px", borderRadius: 6, border: `1px solid ${theme.line}`, background: theme["surface-2"] }}>
                <div style={{ fontSize: 9.5, color: theme["ink-4"], textTransform: "uppercase", letterSpacing: "0.05em" }}>Awaiting review</div>
                <div className="mono" style={{ fontSize: 20, fontWeight: 700, color: summary.pending_count > 0 ? theme["red-ink"] : theme.ink }}>{summary.pending_count}</div>
              </div>
              {summary.top_preparers && summary.top_preparers[0] && (
                <div style={{ minWidth: 170, padding: "10px 14px", borderRadius: 6, border: `1px solid ${theme.line}`, background: theme.surface }}>
                  <div style={{ fontSize: 9.5, color: theme["ink-4"], textTransform: "uppercase", letterSpacing: "0.05em" }}>Top flagged preparer</div>
                  <div style={{ fontSize: 12.5, fontWeight: 600, color: theme.ink, marginTop: 2 }}>{summary.top_preparers[0].preparer}</div>
                  <div style={{ fontSize: 9.5, color: theme["ink-4"] }}>{summary.top_preparers[0].count} finding(s)</div>
                </div>
              )}
            </div>
          )}
          {hasData && (grouped ? (
            rows.map(group => (
              <AttentionGroupRow key={`${group.control_id}::${group.system_source}`} group={group}
                onResolved={handleGroupResolved}
                getMembers={g => window.MCP.jeTestingFindings({
                  ruleId: g.control_id, systemSource: g.system_source, onlyPending: true, limit: 200,
                }).then(d => d.rows || [])}
                renderMember={(row, onMemberResolved) => (
                  <JeFindingRow key={row.event_id} row={row} theme={theme} onDisposed={onMemberResolved} />
                )}
                onBulkResolve={(g, label, notes) => window.MCP.jeTestingBulkDisposition(g.control_id, g.system_source, label, notes)}
                resolveAllLabel="finding" />
            ))
          ) : (
            rows.map(row => <JeFindingRow key={row.event_id} row={row} theme={theme} onDisposed={handleDisposed} />)
          ))}
        </div>
      )}
    </VizFrame>
  );
}

function WalkthroughStatTile({ theme, label, value, sub }) {
  return (
    <div style={{ minWidth: 130, padding: "10px 14px", borderRadius: 6, border: `1px solid ${theme.line}`, background: theme["surface-2"] }}>
      <div style={{ fontSize: 9.5, color: theme["ink-4"], textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
      <div className="mono" style={{ fontSize: 18, fontWeight: 700, color: theme.ink }}>{value}</div>
      {sub && <div style={{ fontSize: 9.5, color: theme["ink-4"], marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function WalkthroughStatsSummary({ theme, stats }) {
  const conf = stats?.conformance;
  const cyc = stats?.cycle_times;
  const rw = stats?.rework;
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
      <WalkthroughStatTile theme={theme} label="Conformance rate"
        value={conf?.conformance_rate != null ? `${(conf.conformance_rate * 100).toFixed(0)}%` : "—"}
        sub={conf ? `${conf.conforming_cases}/${conf.scored_cases} cases` : null} />
      <WalkthroughStatTile theme={theme} label="Rework rate"
        value={rw?.rework_rate != null ? `${(rw.rework_rate * 100).toFixed(0)}%` : "—"}
        sub={rw ? `${rw.reworked_cases}/${rw.total_cases} cases` : null} />
      <WalkthroughStatTile theme={theme} label="Avg case duration"
        value={_pmFmtHours(cyc?.case_duration?.mean_hours)}
        sub={cyc?.bottleneck ? `bottleneck: ${cyc.bottleneck.source} → ${cyc.bottleneck.target}` : null} />
    </div>
  );
}

function WalkthroughSection({ theme, label, text, highlight }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div className="mono" style={{ fontSize: 9.5, color: theme["ink-4"], letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 4 }}>
        {label}
      </div>
      <div style={{
        fontSize: 12, lineHeight: 1.55, color: theme.ink, whiteSpace: "pre-wrap",
        padding: highlight ? "8px 10px" : 0, borderRadius: 6,
        background: highlight ? theme["surface-2"] : "transparent",
        border: highlight ? `1px solid ${theme.line}` : "none",
      }}>
        {text}
      </div>
    </div>
  );
}

export function WalkthroughNarrativeView({ theme }) {
  const processes = useProcessList();
  const [process, setProcess] = useState(null);
  const [transcript, setTranscript] = useState("");
  const [days, setDays] = useState(90);
  const [drafting, setDrafting] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  async function draft() {
    if (!process) { setError("Select a process first."); return; }
    if (!transcript.trim()) { setError("Paste the interview transcript first."); return; }
    setDrafting(true); setError(null);
    try {
      const res = await window.MCP.draftWalkthroughNarrative(process, transcript, days);
      setResult(res);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setDrafting(false);
    }
  }

  return (
    <VizFrame theme={theme} height={720}
      kicker="Process mining · Walkthrough narrative — first draft for a human to correct"
      sub="Combines a process interview transcript with real process-mining statistics for the same process, explicitly prompted to call out where the two disagree. Never published or persisted on its own — paste/edit the result into your actual workpaper."
      controls={<ProcessFilterSelect value={process} onChange={setProcess} processes={processes} />}
      error={error} loading={false} empty={null}>
      <div style={{ position: "absolute", inset: 0, overflow: "auto", padding: 14 }}>
        <div style={{ display: "flex", gap: 10, marginBottom: 10, alignItems: "flex-end" }}>
          <label style={{ fontSize: 10.5, color: theme["ink-4"] }}>
            Lookback window
            <select value={days} onChange={e => setDays(Number(e.target.value))}
              style={{ display: "block", marginTop: 3, fontSize: 11, padding: "3px 6px", borderRadius: 4, border: `1px solid ${theme.line}`, background: theme.surface, color: theme.ink }}>
              {[30, 60, 90, 180].map(d => <option key={d} value={d}>{d} days</option>)}
            </select>
          </label>
        </div>
        <textarea value={transcript} onChange={e => setTranscript(e.target.value)}
          placeholder="Paste the process interview transcript here…"
          className="code-input mono" style={{ width: "100%", height: 140, fontSize: 12 }} />
        <div style={{ marginTop: 8, marginBottom: 4 }}>
          <button type="button" className="btn btn-sm" disabled={drafting} onClick={draft}>
            {drafting ? "Drafting…" : "Draft narrative"}
          </button>
        </div>

        {result && (
          <div style={{ marginTop: 16 }}>
            <WalkthroughStatsSummary theme={theme} stats={result.supporting_stats} />
            <WalkthroughSection theme={theme} label="Process description" text={result.narrative.process_description} />
            <WalkthroughSection theme={theme} label="Key controls" text={result.narrative.key_controls} />
            <WalkthroughSection theme={theme} label="System evidence" text={result.narrative.system_evidence} highlight />
            <WalkthroughSection theme={theme} label="Open questions" text={result.narrative.open_questions} />
          </div>
        )}
      </div>
    </VizFrame>
  );
}

/* ════════════════════════════════════════════════════════════════════════
   Tabbed container — owns theme, the shared day-range, and one data
   fetch for whichever dimension is active, so switching chart tabs is
   instant (no re-fetch, no blank flash) and "90 days" doesn't reset when
   you switch chart type.
   ════════════════════════════════════════════════════════════════════════ */

const CHART_TABS = [
  { id: "replay", label: "Event Replay" },
  { id: "sankey", label: "Sankey" },
  { id: "heatgrid", label: "Heat Grid" },
  { id: "dfg", label: "Flow Graph (DFG)" },
  { id: "caseflow", label: "Case Flow (real DFG)" },
  { id: "variants", label: "Variants" },
  { id: "conformance", label: "Conformance" },
  { id: "cycletime", label: "Cycle Time" },
  { id: "jetesting", label: "JE Testing" },
  { id: "walkthrough", label: "Walkthrough" },
];

function ContinuousMonitoringGroupedViz({ dim, onNavigate }) {
  const theme = useThemeColors();
  const [days, setDays] = useState(30);
  const [tab, setTab] = useState("replay");
  const { events: rawEvents, loading, error } = useObservabilityEvents(days);

  return (
    <div>
      {/* One-time keyframe for VizFrame's loading spinner — cheap and
          idempotent to declare here regardless of which tab is active. */}
      <style>{"@keyframes cm-viz-spin{to{transform:rotate(360deg)}}"}</style>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 4, borderBottom: "1px solid var(--line)" }}>
          {CHART_TABS.map(t => (
            <button key={t.id} type="button" onClick={() => setTab(t.id)}
              style={{
                fontSize: 11.5, padding: "7px 13px", cursor: "pointer", marginBottom: -1,
                border: "none", borderBottom: t.id === tab ? "2px solid var(--acc)" : "2px solid transparent",
                background: "transparent",
                color: t.id === tab ? "var(--acc-ink)" : "var(--ink-3)",
                fontWeight: t.id === tab ? 650 : 500,
                transition: "color .12s, border-color .12s",
              }}>
              {t.label}
            </button>
          ))}
        </div>
        <DaysSelect days={days} setDays={setDays} />
      </div>

      {tab === "replay" && <EventReplayChart theme={theme} days={days} dim={dim} rawEvents={rawEvents} loading={loading} error={error} onNavigate={onNavigate} />}
      {tab === "sankey" && <DimensionSankey theme={theme} days={days} dim={dim} rawEvents={rawEvents} loading={loading} error={error} onNavigate={onNavigate} />}
      {tab === "heatgrid" && <DimensionHeatGrid theme={theme} days={days} dim={dim} rawEvents={rawEvents} loading={loading} error={error} onNavigate={onNavigate} />}
      {tab === "dfg" && <DimensionFlowGraph theme={theme} days={days} dim={dim} rawEvents={rawEvents} loading={loading} error={error} onNavigate={onNavigate} />}
      {tab === "caseflow" && <CaseFlowGraph theme={theme} days={days} rawEvents={rawEvents} loading={loading} error={error} />}
      {tab === "variants" && <ProcessVariantsView theme={theme} days={days} />}
      {tab === "conformance" && <ProcessConformanceView theme={theme} days={days} />}
      {tab === "cycletime" && <ProcessCycleTimeView theme={theme} days={days} />}
      {tab === "jetesting" && <JETestingView theme={theme} />}
      {tab === "walkthrough" && <WalkthroughNarrativeView theme={theme} />}
    </div>
  );
}

export function ContinuousMonitoringDomainViz({ onNavigate } = {}) {
  return <ContinuousMonitoringGroupedViz dim={DOMAIN_DIM} onNavigate={onNavigate} />;
}

export function ContinuousMonitoringSourceSystemViz({ onNavigate } = {}) {
  return <ContinuousMonitoringGroupedViz dim={SOURCE_SYSTEM_DIM} onNavigate={onNavigate} />;
}

Object.assign(window, {
  ContinuousMonitoringDomainViz, ContinuousMonitoringSourceSystemViz,
  EventReplayChart, DimensionSankey, DimensionHeatGrid, DimensionFlowGraph, CaseFlowGraph,
});
