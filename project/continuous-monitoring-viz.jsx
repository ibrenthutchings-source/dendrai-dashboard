/* ============================================================
   Continuous Monitoring — domain-grouped process-mining views.

   Four tabs, all reading real adjudicated-event data (never
   synthetic), sharing one domain palette so a domain is the same
   color in every view, and all following the user's light/dark
   preference set in Mission Control (config-screen.jsx's
   "Appearance" card, which resolves to document.body's
   data-theme attribute — see useThemeColors() below):

     - DomainEventReplay : animated per-event playback (canvas),
       ported from the "Interaction & Motion Study" design artifact.
     - DomainSankey      : Core Domain -> Verdict flow (d3-sankey),
       same visual language as control-flow-map.jsx.
     - DomainHeatGrid    : Domain x Day density matrix.
     - DomainFlowGraph   : animated Directly-Follows Graph (dagre
       layout + D3 rendering) — Domain -> Risk Tier -> Verdict ->
       Rule, edge width/label = real observed transition counts,
       particles animate along each edge to show flow direction.

   Data:
     GET /api/mcp/observability/events?days=N          (replay, DFG)
     GET /api/mcp/observability/domain-summary?days=N   (sankey, heat grid)

   Both endpoints leave `domain` null when an event's policy
   violation can't yet be mapped to a Core Domain (pol_domain_
   mappings' honest-gap behavior) — every view here folds those
   into an explicit "Unclassified" bucket rather than dropping them.
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

// Fixed categorical order — never cycled, same domain always gets the same
// color across every tab, and deliberately theme-invariant (a domain's
// identity color shouldn't change when you flip light/dark).
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
const DOMAIN_PALETTE = [
  "#a855f7", "#22d3ee", "#ec4899", "#84cc16", "#f97316",
  "#3b82f6", "#eab308", "#14b8a6", "#f43f5e", "#64748b",
];
const DOMAIN_COLOR = Object.fromEntries(DOMAIN_ORDER.map((d, i) => [d, DOMAIN_PALETTE[i]]));
function domainColor(d) { return DOMAIN_COLOR[d || "Unclassified"] || "#64748b"; }

// Verdict/tier severity colors are also kept theme-invariant (saturated
// enough to read on both a light and dark panel) — only the surrounding
// chrome (backgrounds, gridlines, text) follows the theme.
const VERDICT_COLOR = { ESCALATE: "#ef4444", MONITOR: "#3b82f6", CLEAR: "#22c55e", UNKNOWN: "#64748b" };
const TIER_COLOR = { CRITICAL: "#ef4444", HIGH: "#f97316", MEDIUM: "#f59e0b", LOW: "#22c55e", UNKNOWN: "#64748b" };

function _fmtDay(iso) {
  return new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// ── Shared: theme-aware process-mining panel frame (matches ControlFlowMap's
// layout; colors now come from useThemeColors() instead of being fixed dark).
// `loading` (first fetch in flight, nothing to show yet) is visually distinct
// from `empty` (fetch finished, genuinely nothing there) so a tab switch
// reads as "working on it" rather than a blank flash. ──
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

/* ════════════════════════════════════════════════════════════════════════
   1. Domain Event Replay — animated per-event playback
   ════════════════════════════════════════════════════════════════════════ */

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

export function DomainEventReplay({ theme, days, rawEvents, loading, error }) {
  const cvRef = useRef(null);
  const hostRef = useRef(null);
  const [tip, setTip] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [loop, setLoop] = useState(true);
  const [focusDomain, setFocusDomain] = useState(null);
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

  // Domain lanes present in this window, in fixed categorical order.
  const domains = useMemo(() => {
    const present = new Set(rawEvents.map(e => e.domain || "Unclassified"));
    return DOMAIN_ORDER.filter(d => present.has(d));
  }, [rawEvents]);

  // Events with a `t` field: days-ago-from-window-start, 0..days.
  const events = useMemo(() => {
    if (!rawEvents.length) return [];
    const now = Date.now();
    return rawEvents
      .map(e => {
        const ts = e.adjudicated_at ? new Date(e.adjudicated_at).getTime() : now;
        const t = Math.max(0, Math.min(days, days - (now - ts) / 86400000));
        return { ...e, domain: e.domain || "Unclassified", t };
      })
      .sort((a, b) => a.t - b.t);
  }, [rawEvents, days]);

  function setPlayhead(v) { playheadRef.current = v; setPlayheadState(v); }

  const laneLayout = useCallback(() => {
    const lanes = [];
    let y = 30;
    domains.forEach(d => { lanes.push({ domain: d, y, h: 26 }); y += 26 + 3; });
    return { lanes, height: y + 24 };
  }, [domains]);

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
    const laneY = {}; lanes.forEach(ln => { laneY[ln.domain] = ln.y + ln.h / 2; });

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

    // Lanes + sparkline + count
    ctx.textAlign = "left";
    lanes.forEach(ln => {
      const dim = focusDomain !== null && focusDomain !== ln.domain;
      ctx.globalAlpha = dim ? 0.22 : 1;
      ctx.fillStyle = theme["surface-2"];
      ctx.beginPath();
      const r = 3, x = 0, y = ln.y, w = cssW, h = ln.h - 4;
      ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath(); ctx.fill();

      ctx.fillStyle = domainColor(ln.domain);
      ctx.beginPath(); ctx.arc(10, ln.y + ln.h / 2 - 2, 3.5, 0, 6.2832); ctx.fill();
      ctx.fillStyle = theme.ink;
      ctx.font = '650 11px "Geist", -apple-system, sans-serif';
      ctx.fillText(ln.domain.length > 26 ? ln.domain.slice(0, 25) + "…" : ln.domain, 20, ln.y + ln.h / 2 + 2);

      const shown = events.filter(e => e.domain === ln.domain && e.t <= head && visible(e)).length;
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
      const cy0 = laneY[e.domain];
      if (cy0 === undefined) return;
      const dim = focusDomain !== null && focusDomain !== e.domain;
      const jitter = ((e.id * 2654435761) % 1000) / 1000 - 0.5;
      const cy = cy0 + jitter * 9;
      const cx = t2x(e.t);
      const age = head - e.t;

      let op = reducedMotion ? 0.7 : Math.max(0.12, 1 - age / (days * 0.7));
      if (e.verdict === "CLEAR") op *= 0.6;
      if (dim) op *= 0.18;

      let rad = e.verdict === "ESCALATE" ? 3.6 : e.verdict === "MONITOR" ? 2.8 : 2.1;
      if (!reducedMotion && age < days * 0.012) {
        const k = age / (days * 0.012);
        rad *= 0.35 + 0.65 * (1 - Math.pow(1 - k, 3));
        op *= 0.4 + 0.6 * k;
      }
      if (!reducedMotion && e.verdict === "ESCALATE" && !dim) {
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
  }, [laneLayout, events, days, focusDomain, escOnly, reducedMotion, theme]);

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
    const laneY = {}; lanes.forEach(ln => { laneY[ln.domain] = ln.y + ln.h / 2; });
    const visible = e => !escOnly || e.verdict === "ESCALATE";
    let best = null, bestD = 12;
    events.forEach(e => {
      if (e.t > head || !visible(e)) return;
      const cy0 = laneY[e.domain]; if (cy0 === undefined) return;
      const jitter = ((e.id * 2654435761) % 1000) / 1000 - 0.5;
      const cy = cy0 + jitter * 9;
      const cx = PAD_L + (e.t / days) * plotW;
      const dd = Math.hypot(cx - mx, cy - my);
      if (dd < bestD) { bestD = dd; best = e; }
    });
    if (!best) { setTip(null); return; }
    setTip({ x: evt.clientX, y: evt.clientY, event: best });
  }

  function handleClickLane(evt) {
    const cv = cvRef.current;
    const rect = cv.getBoundingClientRect();
    const mx = evt.clientX - rect.left;
    if (mx > 205) return; // only the label gutter is clickable
    const my = evt.clientY - rect.top;
    const { lanes } = laneLayout();
    const hit = lanes.find(ln => my >= ln.y && my <= ln.y + ln.h);
    if (!hit) return;
    setFocusDomain(prev => prev === hit.domain ? null : hit.domain);
  }

  const escalatedShown = events.filter(e => e.t <= playhead && e.verdict === "ESCALATE").length;
  const shown = events.filter(e => e.t <= playhead && (!escOnly || e.verdict === "ESCALATE")).length;

  return (
    <VizFrame
      theme={theme}
      kicker="Continuous evidence · Domain Event Replay"
      sub={`Real adjudicated events replayed over the ${days}-day window, grouped by Core Domain — click a domain label to isolate it, hover an event for detail.`}
      error={error && !events.length ? error : null}
      empty={!loading && !events.length && !error ? `No adjudicated events in the last ${days} days yet.` : null}
      loading={loading && !events.length}
      height={Math.max(360, domains.length * 29 + 60)}
    >
      {events.length > 0 && (
        <>
          <div ref={hostRef} style={{ position: "absolute", inset: 0, top: 0, bottom: 56 }}>
            <canvas
              ref={cvRef}
              style={{ width: "100%", display: "block", cursor: "crosshair" }}
              onMouseMove={handleMouseMove}
              onMouseLeave={() => setTip(null)}
              onClick={handleClickLane}
            />
          </div>

          {tip && (
            <div style={{
              position: "fixed", left: tip.x + 16, top: tip.y - 10,
              background: theme.surface, border: `1px solid ${domainColor(tip.event.domain)}`,
              borderLeft: `3px solid ${VERDICT_COLOR[tip.event.verdict] || theme["ink-3"]}`,
              borderRadius: 7, padding: "9px 13px", maxWidth: 300, zIndex: 9999, pointerEvents: "none",
              boxShadow: "0 6px 32px oklch(0% 0 0 / .4)", fontFamily: "system-ui, sans-serif",
            }}>
              <div style={{ fontSize: 9, color: domainColor(tip.event.domain), fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4 }}>
                {tip.event.domain}
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
   2. Domain-Anchored Sankey — Core Domain -> Verdict
   ════════════════════════════════════════════════════════════════════════ */

function useDomainSummary(days) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  const load = useCallback(() => {
    setState(s => ({ ...s, loading: true }));
    return fetch(`${_obsBase()}/domain-summary?days=${days}`, { credentials: "include" })
      .then(res => { if (!res.ok) throw new Error(`Failed to load domain summary (${res.status})`); return res.json(); })
      .then(d => setState({ data: d, loading: false, error: d.note || null }))
      .catch(e => setState({ data: null, loading: false, error: e.message }));
  }, [days]);
  useEffect(() => { load(); }, [load]);
  return { ...state, reload: load };
}

export function DomainSankey({ theme, days, data, loading, error }) {
  const svgRef = useRef(null);
  const hostRef = useRef(null);
  const [tooltip, setTooltip] = useState(null);

  const graph = useMemo(() => {
    if (!data?.domains?.length) return null;
    const nodes = [];
    const links = [];
    const domainNodeId = {};
    data.domains.forEach(d => {
      const id = `dom:${d.domain}`;
      domainNodeId[d.domain] = id;
      nodes.push({ id, label: d.domain, type: "domain", color: domainColor(d.domain), value: d.total });
    });
    ["escalated", "monitor", "clear"].forEach(v => {
      nodes.push({ id: `v:${v}`, label: v.toUpperCase(), type: "verdict", color: VERDICT_COLOR[v.toUpperCase()] || VERDICT_COLOR.UNKNOWN });
    });
    data.domains.forEach(d => {
      ["escalated", "monitor", "clear"].forEach(v => {
        if (d[v] > 0) links.push({ source: domainNodeId[d.domain], target: `v:${v}`, value: d[v] });
      });
    });
    return { nodes, links };
  }, [data]);

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
      .on("mouseout", () => { clear(); setTooltip(null); });
    linkPaths
      .on("mouseover", (evt, d) => {
        linkPaths.attr("stroke-opacity", l => l === d ? 0.9 : 0.06);
        nodeGrp.style("opacity", n => n.id === d.source.id || n.id === d.target.id ? 1 : 0.15);
        setTooltip({ x: evt.clientX, y: evt.clientY, isLink: true, sourceLabel: d.source.label, sourceColor: d.source.color, targetLabel: d.target.label, targetColor: d.target.color, value: d.value });
      })
      .on("mousemove", evt => setTooltip(p => p ? { ...p, x: evt.clientX, y: evt.clientY } : null))
      .on("mouseout", () => { clear(); setTooltip(null); });
  }, [graph, theme]);

  const hasData = !!graph;

  return (
    <VizFrame
      theme={theme}
      kicker="Continuous evidence · Domain-Anchored Sankey"
      sub={`Every adjudication in the last ${days} days, grouped by Core Domain, flowing to its verdict — flow width is the real event count.`}
      error={error && !hasData ? error : null}
      empty={!loading && !hasData && !error ? `No domain-resolved events in the last ${days} days yet.` : null}
      loading={loading && !hasData}
    >
      {hasData && (
        <div ref={hostRef} style={{ position: "absolute", inset: 0 }}>
          <svg ref={svgRef} style={{ width: "100%", height: "100%", display: "block" }} />
        </div>
      )}
      {tooltip && (
        <div style={{
          position: "fixed", left: tooltip.x + 16, top: tooltip.y - 10,
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
   3. Domain Heat Grid — Domain x Day density matrix
   ════════════════════════════════════════════════════════════════════════ */

export function DomainHeatGrid({ theme, days, data, loading, error }) {
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

  const grid = useMemo(() => {
    if (!data?.domains?.length) return null;
    const dayKeys = [];
    const today = new Date();
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(today); d.setUTCDate(d.getUTCDate() - i);
      dayKeys.push(d.toISOString().slice(0, 10));
    }
    const domains = [...data.domains].sort((a, b) => b.total - a.total);
    const maxCell = Math.max(1, ...domains.flatMap(d => (d.daily || []).map(x => x.total)));
    const rows = domains.map(d => {
      const byDay = Object.fromEntries((d.daily || []).map(x => [x.date, x]));
      return { domain: d.domain, total: d.total, cells: dayKeys.map(k => byDay[k] || { date: k, total: 0, escalated: 0 }) };
    });
    return { dayKeys, rows, maxCell };
  }, [data, days]);

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
      kicker="Continuous evidence · Domain Heat Grid"
      sub={`Event density by Core Domain and day over the last ${days} days — color intensity is volume; the red tick marks a day with at least one escalation.`}
      error={error && !hasData ? error : null}
      empty={!loading && !hasData && !error ? `No domain-resolved events in the last ${days} days yet.` : null}
      loading={loading && !hasData}
      height={Math.max(440, rowCount * 34 + 90)}
    >
      {hasData && (
        <div ref={hostRef} style={{ position: "absolute", inset: 0, overflow: "auto", padding: "16px 16px 12px" }}>
          <svg width={Math.max(size.w - SIDE_PAD, labelW + gridW)} height={gridH + TOP + BOTTOM} style={{ display: "block" }}>
            {grid.rows.map((row, ri) => (
              <g key={row.domain} transform={`translate(0, ${ri * cellH + TOP})`}>
                <rect x={0} y={0} width={labelW - 10} height={cellH - 3} rx={3} fill={theme["surface-2"]} />
                <circle cx={9} cy={(cellH - 3) / 2} r={3.5} fill={domainColor(row.domain)} />
                <text x={18} y={(cellH - 3) / 2 + 3.5} fontSize={10.5} fontWeight={600} fill={theme.ink} fontFamily="system-ui, sans-serif">
                  {row.domain.length > 24 ? row.domain.slice(0, 23) + "…" : row.domain}
                </text>
                <text x={labelW - 16} y={(cellH - 3) / 2 + 3.5} fontSize={9.5} fontWeight={700} fill={theme["ink-2"]} textAnchor="end" fontFamily="monospace">
                  {row.total}
                </text>
                {row.cells.map((c, ci) => {
                  const intensity = grid.maxCell ? c.total / grid.maxCell : 0;
                  const alpha = c.total === 0 ? 0.05 : 0.18 + intensity * 0.72;
                  return (
                    <g key={c.date}
                      onMouseEnter={e => setHover({ x: e.clientX, y: e.clientY, domain: row.domain, cell: c })}
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
          position: "fixed", left: hover.x + 14, top: hover.y - 10,
          background: theme.surface, border: `1px solid ${domainColor(hover.domain)}`,
          borderRadius: 7, padding: "8px 12px", zIndex: 9999, pointerEvents: "none",
          boxShadow: "0 6px 32px oklch(0% 0 0 / .4)", fontFamily: "system-ui, sans-serif",
        }}>
          <div style={{ fontSize: 10, color: domainColor(hover.domain), fontWeight: 700 }}>{hover.domain}</div>
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
   4. Domain Flow Graph — animated Directly-Follows Graph (dagre + D3)
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

function buildDfgGraph(events) {
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
    const domain = e.domain || "Unclassified";
    const tier = e.risk_tier || "UNKNOWN";
    const verdict = e.verdict || "UNKNOWN";
    const rawRule = primaryRuleLabel(e.policy_violations) || (verdict === "CLEAR" ? null : "Unmapped rule");
    const rule = rawRule && !topRules.has(rawRule) ? "Other" : rawRule;

    const dId = node(`d:${domain}`, domain, "domain");
    const tId = node(`t:${tier}`, tier, "tier");
    const vId = node(`v:${verdict}`, verdict, "verdict");
    edge(dId, tId);
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

function dfgNodeColor(n, theme) {
  if (n.kind === "domain") return domainColor(n.label);
  if (n.kind === "tier") return TIER_COLOR[n.label] || TIER_COLOR.UNKNOWN;
  if (n.kind === "verdict") return VERDICT_COLOR[n.label] || VERDICT_COLOR.UNKNOWN;
  return theme.acc;
}

export function DomainFlowGraph({ theme, days, rawEvents, loading, error }) {
  const hostRef = useRef(null);
  const svgRef = useRef(null);
  const rafRef = useRef(null);
  const [tooltip, setTooltip] = useState(null);

  const events = useMemo(() => rawEvents.map(e => ({ ...e })), [rawEvents]);
  const graph = useMemo(() => (events.length ? buildDfgGraph(events) : null), [events]);
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
      .attr("stroke", d => dfgNodeColor(g.node(d._e.v), theme))
      .attr("stroke-opacity", 0.28)
      .attr("stroke-width", d => Math.max(1, Math.sqrt(d.value / maxVal) * 7))
      .attr("cursor", "pointer")
      .on("mouseover", (evt, d) => {
        edgeSel.attr("stroke-opacity", o => o === d ? 0.85 : 0.08);
        setTooltip({
          x: evt.clientX, y: evt.clientY, isLink: true,
          sourceLabel: g.node(d._e.v).label, sourceColor: dfgNodeColor(g.node(d._e.v), theme),
          targetLabel: g.node(d._e.w).label, targetColor: dfgNodeColor(g.node(d._e.w), theme),
          value: d.value,
        });
      })
      .on("mousemove", evt => setTooltip(p => p ? { ...p, x: evt.clientX, y: evt.clientY } : null))
      .on("mouseout", () => { edgeSel.attr("stroke-opacity", 0.28); setTooltip(null); });

    const nodeG = svg.append("g");
    const nodeSel = nodeG.selectAll("g").data(g.nodes().map(id => g.node(id))).join("g")
      .attr("transform", d => `translate(${d.x - d.width / 2 + 20}, ${d.y - d.height / 2 + 20})`)
      .attr("cursor", "pointer");
    nodeSel.append("rect")
      .attr("width", d => d.width).attr("height", d => d.height).attr("rx", 3)
      .attr("fill", d => dfgNodeColor(d, theme)).attr("fill-opacity", 0.85)
      .attr("stroke", d => dfgNodeColor(d, theme)).attr("stroke-width", 0.5).attr("stroke-opacity", 0.5);
    nodeSel.append("text")
      .attr("x", d => d.width / 2).attr("y", d => d.height / 2)
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("fill", "#fff").attr("font-size", 9.5).attr("font-weight", 650)
      .attr("font-family", "system-ui, sans-serif").attr("pointer-events", "none")
      .text(d => d.label.length > 24 ? d.label.slice(0, 23) + "…" : d.label);
    nodeSel
      .on("mouseover", (evt, d) => {
        nodeSel.style("opacity", n => n.id === d.id ? 1 : 0.3);
        edgeSel.attr("stroke-opacity", e => (e._e.v === d.id || e._e.w === d.id) ? 0.85 : 0.05);
        setTooltip({ x: evt.clientX, y: evt.clientY, label: d.label, kind: d.kind, value: d.value, color: dfgNodeColor(d, theme) });
      })
      .on("mousemove", evt => setTooltip(p => p ? { ...p, x: evt.clientX, y: evt.clientY } : null))
      .on("mouseout", () => { nodeSel.style("opacity", 1); edgeSel.attr("stroke-opacity", 0.28); setTooltip(null); });

    // Animated flow particles — one small dot per edge (up to 3, scaled by
    // relative volume), walked along the laid-out path each frame.
    if (!reducedMotion) {
      const particleSpecs = [];
      edgeSel.each(function (d) {
        const len = this.getTotalLength();
        if (!len) return;
        const n = Math.max(1, Math.min(3, Math.round((d.value / maxVal) * 3)));
        for (let i = 0; i < n; i++) {
          particleSpecs.push({ path: this, len, phase: i / n, color: dfgNodeColor(g.node(d._e.v), theme) });
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
  }, [graph, hasData, theme]);

  return (
    <VizFrame
      theme={theme}
      kicker="Continuous evidence · Domain Flow Graph (DFG)"
      sub={`Directly-follows graph of the last ${days} days: Core Domain → Risk Tier → Verdict → Rule, edge width and moving particles both reflect real observed transition counts.`}
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
          position: "fixed", left: tooltip.x + 16, top: tooltip.y - 10,
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
   Tabbed container — owns theme, the shared day-range, and both data
   fetches ONCE, so switching tabs is instant (no re-fetch, no blank flash)
   and picking "90 days" in one tab doesn't reset when you switch to another.
   ════════════════════════════════════════════════════════════════════════ */

const TABS = [
  { id: "replay", label: "Event Replay" },
  { id: "sankey", label: "Domain Sankey" },
  { id: "heatgrid", label: "Domain Heat Grid" },
  { id: "dfg", label: "Flow Graph (DFG)" },
];

export function ContinuousMonitoringDomainViz() {
  const theme = useThemeColors();
  const [days, setDays] = useState(30);
  const [tab, setTab] = useState("replay");
  const { events: rawEvents, loading: eventsLoading, error: eventsError } = useObservabilityEvents(days);
  const { data: summaryData, loading: summaryLoading, error: summaryError } = useDomainSummary(days);

  return (
    <div>
      {/* One-time keyframe for VizFrame's loading spinner — cheap and
          idempotent to declare here regardless of which tab is active. */}
      <style>{"@keyframes cm-viz-spin{to{transform:rotate(360deg)}}"}</style>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 4, borderBottom: "1px solid var(--line)" }}>
          {TABS.map(t => (
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

      {tab === "replay" && <DomainEventReplay theme={theme} days={days} rawEvents={rawEvents} loading={eventsLoading} error={eventsError} />}
      {tab === "sankey" && <DomainSankey theme={theme} days={days} data={summaryData} loading={summaryLoading} error={summaryError} />}
      {tab === "heatgrid" && <DomainHeatGrid theme={theme} days={days} data={summaryData} loading={summaryLoading} error={summaryError} />}
      {tab === "dfg" && <DomainFlowGraph theme={theme} days={days} rawEvents={rawEvents} loading={eventsLoading} error={eventsError} />}
    </div>
  );
}

Object.assign(window, { ContinuousMonitoringDomainViz, DomainEventReplay, DomainSankey, DomainHeatGrid, DomainFlowGraph });
