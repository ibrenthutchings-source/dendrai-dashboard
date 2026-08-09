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
// position in that list rather than order of appearance. ──
const DOMAIN_ORDER = [
  "Identity & Access Management",
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
const DOMAIN_DIM = {
  key: "domain",
  label: "Core Domain",
  noun: "domain",
  extract: e => e.domain || "Unclassified",
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
// `initialFilter` of { domain, source, tier, verdict } (see cem.jsx). Only
// "domain" and "source" map to a dimension's group value — "source" reuses
// the tab's pre-existing source_system filter, "domain" is the new one
// added alongside it — so a click anywhere in these charts can deep-link
// into exactly the slice it represents.
function groupFilterKey(dim) { return dim.key === "domain" ? "domain" : "source"; }
function groupCemFilter(dim, groupValue, extra) {
  const f = {};
  if (groupValue) f[groupFilterKey(dim)] = groupValue;
  return { ...f, ...extra };
}
// Same idea for a Flow Graph node of any kind (group/tier/verdict/rule) —
// "rule" has no corresponding filter in the Adjudications tab (policy
// violation text isn't a filterable column there), so it resolves to null
// and the caller should treat that as "not clickable."
function dfgNodeCemFilter(n, dim) {
  if (n.kind === dim.key) return groupCemFilter(dim, n.label);
  if (n.kind === "tier") return { tier: n.label };
  if (n.kind === "verdict") return { verdict: n.label };
  return null;
}

// Verdict/tier severity colors are also kept theme-invariant (saturated
// enough to read on both a light and dark panel) — only the surrounding
// chrome (backgrounds, gridlines, text) follows the theme.
const VERDICT_COLOR = { ESCALATE: "#ef4444", MONITOR: "#3b82f6", CLEAR: "#22c55e", UNKNOWN: "#64748b" };
const TIER_COLOR = { CRITICAL: "#ef4444", HIGH: "#f97316", MEDIUM: "#f59e0b", LOW: "#22c55e", UNKNOWN: "#64748b" };

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
    if (!g) { g = { key, total: 0, escalated: 0, monitor: 0, clear: 0, daily: new Map() }; byGroup.set(key, g); }
    g.total++;
    const vk = e.verdict === "ESCALATE" ? "escalated" : e.verdict === "MONITOR" ? "monitor" : e.verdict === "CLEAR" ? "clear" : null;
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
      if (dim2) op *= 0.18;

      let rad = e.verdict === "ESCALATE" ? 3.6 : e.verdict === "MONITOR" ? 2.8 : 2.1;
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

  const escalatedShown = events.filter(e => e.t <= playhead && e.verdict === "ESCALATE").length;
  const shown = events.filter(e => e.t <= playhead && (!escOnly || e.verdict === "ESCALATE")).length;

  return (
    <VizFrame
      theme={theme}
      kicker={`Continuous evidence · ${dim.label} Event Replay`}
      sub={`Real adjudicated events replayed over the ${days}-day window, grouped by ${dim.label} — click a label to isolate it, click an event to open it in Adjudications, hover for detail.`}
      error={error && !events.length ? error : null}
      empty={!loading && !events.length && !error ? `No adjudicated events in the last ${days} days yet.` : null}
      loading={loading && !events.length}
      height={Math.max(360, groups.length * 29 + 60)}
    >
      {events.length > 0 && (
        <>
          <div ref={hostRef} style={{ position: "absolute", inset: 0, top: 0, bottom: 56 }}>
            <canvas
              ref={cvRef}
              style={{ width: "100%", display: "block", cursor: "crosshair" }}
              onMouseMove={handleMouseMove}
              onMouseLeave={() => setTip(null)}
              onClick={handleClickCanvas}
            />
          </div>

          {tip && (
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
    ["escalated", "monitor", "clear"].forEach(v => {
      nodes.push({ id: `v:${v}`, label: v.toUpperCase(), type: "verdict", kind: "verdict", color: VERDICT_COLOR[v.toUpperCase()] || VERDICT_COLOR.UNKNOWN });
    });
    agg.groups.forEach(g => {
      ["escalated", "monitor", "clear"].forEach(v => {
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
        if (!onNavigate) return;
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
        if (!onNavigate) return;
        // Links only ever run group -> verdict here, so both filters apply.
        onNavigate("ubogov", { cemTab: "adjudications", cemFilter: groupCemFilter(dim, d.source.label, { verdict: d.target.label }) });
      });
  }, [graph, theme, dim, onNavigate]);

  const hasData = !!graph;

  return (
    <VizFrame
      theme={theme}
      kicker={`Continuous evidence · ${dim.label} Sankey`}
      sub={`Every adjudication in the last ${days} days, grouped by ${dim.label}, flowing to its verdict — flow width is the real event count. Click a node or flow to open that slice in Adjudications.`}
      error={error && !hasData ? error : null}
      empty={!loading && !hasData && !error ? `No adjudicated events in the last ${days} days yet.` : null}
      loading={loading && !hasData}
    >
      {hasData && (
        <div ref={hostRef} style={{ position: "absolute", inset: 0 }}>
          <svg ref={svgRef} style={{ width: "100%", height: "100%", display: "block" }} />
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
      sub={`Event density by ${dim.label} and day over the last ${days} days — color intensity is volume; the red tick marks a day with at least one escalation. Click a row to open it in Adjudications.`}
      error={error && !hasData ? error : null}
      empty={!loading && !hasData && !error ? `No adjudicated events in the last ${days} days yet.` : null}
      loading={loading && !hasData}
      height={Math.max(440, rowCount * 34 + 90)}
    >
      {hasData && (
        <div ref={hostRef} style={{ position: "absolute", inset: 0, overflow: "auto", padding: "16px 16px 12px" }}>
          <svg width={Math.max(size.w - SIDE_PAD, labelW + gridW)} height={gridH + TOP + BOTTOM} style={{ display: "block" }}>
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

  // First pass: find the top rule labels so long tails collapse to "Other".
  events.forEach(e => {
    const rule = primaryRuleLabel(e.policy_violations);
    const label = rule || (e.verdict === "CLEAR" ? null : "Unmapped rule");
    if (label) ruleTotals.set(label, (ruleTotals.get(label) || 0) + 1);
  });
  const topRules = new Set(
    [...ruleTotals.entries()].sort((a, b) => b[1] - a[1]).slice(0, DFG_MAX_RULE_NODES).map(r => r[0])
  );

  events.forEach(e => {
    const group = dim.extract(e);
    const tier = e.risk_tier || "UNKNOWN";
    const verdict = e.verdict || "UNKNOWN";
    const rawRule = primaryRuleLabel(e.policy_violations) || (verdict === "CLEAR" ? null : "Unmapped rule");
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

export function DimensionFlowGraph({ theme, days, dim, rawEvents, loading, error, onNavigate }) {
  const hostRef = useRef(null);
  const svgRef = useRef(null);
  const rafRef = useRef(null);
  const [tooltip, setTooltip] = useState(null);

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

    const gw = g.graph().width || 800;
    const gh = g.graph().height || 400;
    const W = Math.max(hostRef.current.clientWidth, gw + 40);
    const H = Math.max(360, gh + 40);

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("viewBox", `0 0 ${W} ${H}`);
    svg.append("rect").attr("width", W).attr("height", H).attr("fill", theme.bg);

    const maxVal = Math.max(1, ...graph.edges.map(e => e.value));
    const lineGen = d3.line().x(d => d.x + 20).y(d => d.y + 20).curve(d3.curveBasis);

    const edgeG = svg.append("g");
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

    const nodeG = svg.append("g");
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
      const particleG = svg.append("g");
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

    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [graph, hasData, theme, dim, onNavigate]);

  return (
    <VizFrame
      theme={theme}
      kicker={`Continuous evidence · ${dim.label} Flow Graph (DFG)`}
      sub={`Directly-follows graph of the last ${days} days: ${dim.label} → Risk Tier → Verdict → Rule, edge width and moving particles both reflect real observed transition counts. Click a ${dim.label.toLowerCase()}/tier/verdict node or edge to open that slice in Adjudications.`}
      error={error && !hasData ? error : null}
      empty={!loading && !hasData && !error ? `No adjudicated events in the last ${days} days yet.` : null}
      loading={loading && !hasData}
      height={620}
    >
      {hasData && (
        <div ref={hostRef} style={{ position: "absolute", inset: 0, overflow: "auto" }}>
          <svg ref={svgRef} style={{ display: "block", width: "100%", height: "100%" }} />
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
  const [tooltip, setTooltip] = useState(null);

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

    const gw = g.graph().width || 800;
    const gh = g.graph().height || 300;
    const W = Math.max(hostRef.current.clientWidth, gw + 40);
    const H = Math.max(300, gh + 40);

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("viewBox", `0 0 ${W} ${H}`);
    svg.append("rect").attr("width", W).attr("height", H).attr("fill", theme.bg);

    const maxVal = Math.max(1, ...graph.edges.map(e => e.value));
    const lineGen = d3.line().x(d => d.x + 20).y(d => d.y + 20).curve(d3.curveBasis);

    const edgeG = svg.append("g");
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

    const nodeG = svg.append("g");
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
      const particleG = svg.append("g");
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

    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [graph, hasData, theme]);

  return (
    <VizFrame
      theme={theme}
      kicker="Continuous evidence · Case Flow Graph (real DFG)"
      sub={graph
        ? `${graph.caseCount} tracked transaction${graph.caseCount !== 1 ? "s" : ""} in the last ${days} days — real step-to-step sequences, not a categorical breakdown. Populated today by the O2C/P2P/Inventory Cycle synthetic generator; a real ERP connector emitting case_id/process_step would appear here the same way.`
        : `Real transaction lifecycles over the last ${days} days, traced step by step — needs events carrying a case_id (see generate_o2c_p2p_synthetic_log.py).`}
      error={error && !hasData ? error : null}
      empty={!loading && !hasData && !error ? `No case-tracked transactions in the last ${days} days yet — run generate_o2c_p2p_synthetic_log.py to populate this view, or wait for a real case-tracked producer.` : null}
      loading={loading && !hasData}
      height={520}
    >
      {hasData && (
        <div ref={hostRef} style={{ position: "absolute", inset: 0, overflow: "auto" }}>
          <svg ref={svgRef} style={{ display: "block", width: "100%", height: "100%" }} />
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
