/* ============================================================
   Charts
   - Heatmap (impact × likelihood with Q4 projection arrows)
   - ForecastChart / MultiSeriesForecastChart — Recharts ComposedChart
   - M-Score gauge
   - Risk Flow Sankey (pure SVG)
   ============================================================ */
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer, Brush,
  PieChart, Pie, Cell,
} from 'recharts';

// Only worth showing zoom controls once there's actually something to zoom
// into — a 5-point chart (e.g. the risk-score forecast: 1 history + 4
// forecast quarters) just adds clutter.
const ZOOM_MIN_POINTS = 9;

// Shared zoom/pan state for both ForecastChart and MultiSeriesForecastChart —
// both render a Recharts <Brush>, which natively windows its parent chart's
// visible domain to [startIndex, endIndex], so this hook only needs to own
// that pair of indices; Recharts does the actual re-rendering of the visible
// range.
//
// Coordinated across every chart on the page: wrap them in <ChartZoomProvider>
// (Pipeline does, around the whole stage list) and zooming any one of them
// moves them all together. The shared state is a [startFrac, endFrac] window
// in [0,1] rather than raw indices — different KPIs can have different
// history lengths (real EDGAR-derived revenue/margin vs. the synthetic 8Q
// series for eps/opMargin/ebitda/netIncome/fcf), so a shared index pair would
// point at a different quarter in each chart. A fraction of the timeline
// means "zoom to the most recent third" reads the same on every chart
// regardless of how many points it actually has. Charts rendered without a
// provider (or a future standalone caller) fall back to local-only state.
const ChartZoomContext = React.createContext(null);

// Persisted across sessions — fraction-of-timeline (not raw indices) is
// exactly what makes this safe to restore regardless of which ticker's
// charts render into the provider next time: "zoomed to the most recent
// third" means the same thing whether that run's history is 8 quarters or
// 40.
const ZOOM_STORAGE_KEY = "dendrai.chartZoom";

// `?? null` (nullish coalescing) only replaces null/undefined — a literal
// NaN (e.g. a 0/0 in an upstream margin/growth-rate calculation) sails
// straight through it unchanged, unlike null/undefined which Recharts skips
// cleanly as a gap (connectNulls={false}). Recharts doesn't treat NaN as a
// gap — it tries to compute a real pixel coordinate from it and emits
// invalid SVG attributes (<rect x="NaN">, <line x1="NaN">) wherever that
// value ends up in the plotted path. Every numeric field built from
// external forecast/history/backtest data for a Recharts `data` array
// should go through this, not a bare `?? null`.
function _finiteOrNull(v) {
  return Number.isFinite(v) ? v : null;
}

function _loadStoredZoomFrac() {
  try {
    const raw = localStorage.getItem(ZOOM_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.length === 2 &&
        Number.isFinite(parsed[0]) && Number.isFinite(parsed[1]) &&
        parsed[0] >= 0 && parsed[1] <= 1 && parsed[0] < parsed[1]) {
      return parsed;
    }
  } catch {}
  return null;
}

function ChartZoomProvider({ children }) {
  const [frac, setFrac] = React.useState(_loadStoredZoomFrac); // null = full extent
  const zoomed = !!frac;
  const minSpan = 0.08;

  // Write-through on every change so a reload — or an entirely new session
  // days later — reopens the Risk Assessment charts at the same window.
  React.useEffect(() => {
    try {
      if (frac) localStorage.setItem(ZOOM_STORAGE_KEY, JSON.stringify(frac));
      else localStorage.removeItem(ZOOM_STORAGE_KEY);
    } catch {}
  }, [frac]);

  function zoomIn() {
    const [s, e] = frac || [0, 1];
    const shrink = (e - s) * 0.2;
    const ns = Math.min(s + shrink, e - minSpan);
    const ne = Math.max(e - shrink, ns + minSpan);
    setFrac([Math.max(0, ns), Math.min(1, ne)]);
  }
  function zoomOut() {
    const [s, e] = frac || [0, 1];
    const grow = (e - s) * 0.25;
    const ns = s - grow, ne = e + grow;
    setFrac(ns <= 0 && ne >= 1 ? null : [Math.max(0, ns), Math.min(1, ne)]);
  }
  function reset() { setFrac(null); }

  const value = React.useMemo(() => ({ frac, zoomed, zoomIn, zoomOut, reset, setFrac }), [frac]);
  return React.createElement(ChartZoomContext.Provider, { value }, children);
}

function _fracToIndices(frac, fullEnd) {
  // Number.isFinite, not just `!frac` — a NaN-poisoned frac (see
  // onBrushChange below) is still a truthy 2-element array, and
  // Math.round(NaN * fullEnd) is NaN, which Recharts' <Brush> renders as
  // <rect x="NaN">. Read-side sanitizer: even a frac that got poisoned
  // before onBrushChange's guard existed (e.g. one already sitting in a
  // browser's localStorage) self-heals to the full extent instead of
  // permanently wedging every chart under this ChartZoomProvider.
  if (!frac || fullEnd <= 0 || !Number.isFinite(frac[0]) || !Number.isFinite(frac[1])) return [0, fullEnd];
  const s = Math.round(frac[0] * fullEnd);
  const e = Math.round(frac[1] * fullEnd);
  return [Math.max(0, Math.min(s, fullEnd - 1)), Math.max(Math.min(s, fullEnd - 1) + 1, Math.min(e, fullEnd))];
}

function useChartZoom(dataLength) {
  const ctx = React.useContext(ChartZoomContext);
  const fullEnd = Math.max(0, dataLength - 1);

  // `range` stays null (full extent) until the user first zooms — locally
  // for the no-provider fallback — so a re-render with new data (e.g. a
  // fresh pipeline run) doesn't get stuck showing a stale window past the
  // new data's length.
  const [range, setRange] = React.useState(null);
  const inBounds = range && range[0] >= 0 && range[1] <= fullEnd && range[0] < range[1];
  const minSpan = Math.min(2, fullEnd);

  if (ctx) {
    const [startIndex, endIndex] = _fracToIndices(ctx.frac, fullEnd);
    return {
      startIndex, endIndex, zoomed: ctx.zoomed,
      zoomIn: ctx.zoomIn, zoomOut: ctx.zoomOut, reset: ctx.reset,
      onBrushChange(r) {
        // Number.isFinite, not `== null` — Recharts' <Brush> occasionally
        // fires onChange with NaN indices during a container-measurement
        // race (its own internal width/scale glitch, not application data),
        // and `NaN == null` is false, so a `== null` check lets it straight
        // through. Once written to ctx.setFrac, that NaN poisons the SHARED
        // fraction every chart under this provider reads on its next
        // render — the actual mechanism behind the NaN <rect> warning and,
        // once enough charts re-render off the poisoned value in a tight
        // cascade, "Maximum update depth exceeded".
        if (!Number.isFinite(r?.startIndex) || !Number.isFinite(r?.endIndex) || fullEnd <= 0) return;
        ctx.setFrac([r.startIndex / fullEnd, r.endIndex / fullEnd]);
      },
    };
  }

  const [startIndex, endIndex] = inBounds ? range : [0, fullEnd];
  const zoomed = startIndex > 0 || endIndex < fullEnd;

  function zoomIn() {
    const span = endIndex - startIndex;
    const shrink = Math.max(1, Math.round(span * 0.2));
    const s = Math.min(startIndex + shrink, endIndex - minSpan);
    const e = Math.max(endIndex - shrink, s + minSpan);
    setRange([Math.max(0, s), Math.min(fullEnd, e)]);
  }
  function zoomOut() {
    const span = endIndex - startIndex;
    const grow = Math.max(1, Math.round(span * 0.25));
    setRange([Math.max(0, startIndex - grow), Math.min(fullEnd, endIndex + grow)]);
  }
  function reset() { setRange(null); }
  function onBrushChange(r) {
    // Number.isFinite, not `== null` — same NaN-from-Recharts hazard as the
    // ctx branch's onBrushChange above (see its comment). `inBounds`'s
    // numeric comparisons happen to reject a NaN range on the following
    // render anyway (any comparison against NaN is false), but validating
    // here means this function's own contract doesn't depend on a reader
    // tracing that through — and it never sets state to a NaN range at all.
    if (!Number.isFinite(r?.startIndex) || !Number.isFinite(r?.endIndex)) return;
    setRange([r.startIndex, r.endIndex]);
  }

  return { startIndex, endIndex, zoomed, zoomIn, zoomOut, reset, onBrushChange };
}

// Recharts' <ResponsiveContainer> measures its width via a ResizeObserver,
// whose first callback always fires AFTER the initial render — so a chart
// that mounts fresh (e.g. opening a Stage accordion, which conditionally
// mounts its body rather than just hiding it — see Stage's `{isOpen && ...}`
// in pipeline.jsx) renders its very first frame against an unmeasured
// (effectively zero) width. <Brush> computes its traveller pixel positions
// from that width and produces transient NaN coordinates — a real Recharts
// limitation, not anything wrong with the startIndex/endIndex/data values
// we hand it (confirmed by instrumenting exactly those and finding them
// always finite when this fires). It self-corrects the moment
// ResizeObserver's real measurement arrives, but React still logs the
// invalid-attribute warning for that one frame, and enough charts doing
// this under the shared ChartZoomProvider at once can compound into more
// than a console warning. Deferring <Brush> by one tick past first paint —
// after the browser has already laid out a real width for
// ResponsiveContainer to measure — is the standard mitigation.
function useMountedAfterPaint() {
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => { setMounted(true); }, []);
  return mounted;
}

function ZoomControls({ zoom, color }) {
  return (
    <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end', alignItems: 'center', marginBottom: 2 }}>
      {zoom.zoomed && (
        <button type="button" onClick={zoom.reset} className="mono"
          style={{ fontSize: 9, padding: '2px 6px', borderRadius: 4, border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--ink-3)', cursor: 'pointer' }}>
          Reset zoom
        </button>
      )}
      <button type="button" onClick={zoom.zoomOut} title="Zoom out"
        style={{ width: 20, height: 20, lineHeight: '18px', textAlign: 'center', fontSize: 13, padding: 0, borderRadius: 4, border: '1px solid var(--line)', background: 'var(--surface)', color: color, cursor: 'pointer' }}>
        −
      </button>
      <button type="button" onClick={zoom.zoomIn} title="Zoom in"
        style={{ width: 20, height: 20, lineHeight: '18px', textAlign: 'center', fontSize: 13, padding: 0, borderRadius: 4, border: '1px solid var(--line)', background: 'var(--surface)', color: color, cursor: 'pointer' }}>
        +
      </button>
    </div>
  );
}

// ---------- HEATMAP ----------
function Heatmap({ risks, activeQ = "Now", onSelect, selectedId }) {
  const W = 360, H = 320, PAD = 40;
  const plotW = W - PAD * 2, plotH = H - PAD * 2;

  const points = risks.map((r, ridx) => {
    const qs = projectQuarters(r);
    const qIdx = { "Now": -1, "Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3 }[activeQ] ?? -1;
    // "Now" baseline positions — impact and likelihood on 1-5 scale
    const nowImp = clamp(r.impact || likelihoodFromCE(r.ce), 1, 5);
    const nowLik = clamp(r.likelihood || likelihoodFromCE(r.ce), 1, 5);
    const nowX = PAD + ((nowLik - 1) / 4) * plotW;
    const nowY = H - PAD - ((nowImp - 1) / 4) * plotH;
    // Q4 projected positions
    const q4Sc = qs[3];
    const q4Imp = clamp(nowImp + (r.velocity || 0) * 0.15, 1, 5);
    const q4Lik = clamp(nowLik + (r.velocity || 0) * 0.10, 1, 5);
    const q4X = PAD + ((q4Lik - 1) / 4) * plotW;
    const q4Y = H - PAD - ((q4Imp - 1) / 4) * plotH;
    // Active-quarter bubble: interpolate from Now toward Q4
    const t = qIdx === -1 ? 0 : (qIdx + 1) / 4;
    const curX = nowX + (q4X - nowX) * t;
    const curY = nowY + (q4Y - nowY) * t;
    const curSc = qIdx === -1 ? r.score : qs[qIdx];
    const curImp = nowImp + (q4Imp - nowImp) * t;
    const curLik = nowLik + (q4Lik - nowLik) * t;
    const size = Math.sqrt(curImp * curLik) * 9.0;
    const q4Size = Math.sqrt(q4Imp * q4Lik) * 7.0;
    return { r, ridx, curX, curY, q4X, q4Y, size, q4Size,
      curRag: ragFromScore(curSc), q4Rag: ragFromScore(q4Sc), curSc, vel: r.velocity || 0 };
  });

  const ragSoft = { R: "color-mix(in oklch, var(--red) 65%, white)",
                    A: "color-mix(in oklch, var(--amber) 65%, white)",
                    G: "color-mix(in oklch, var(--green) 65%, white)" };
  const ragBorder = { R: "var(--red)", A: "var(--amber)", G: "var(--green)" };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width: "100%", display: "block"}} xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="ah-r" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="var(--red)"/></marker>
        <marker id="ah-g" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="var(--green)"/></marker>
        <marker id="ah-a" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="var(--amber)"/></marker>
        <linearGradient id="hm-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="color-mix(in oklch, var(--green) 8%, transparent)"/>
          <stop offset="100%" stopColor="color-mix(in oklch, var(--red) 8%, transparent)"/>
        </linearGradient>
      </defs>

      {/* Plot background */}
      <rect x={PAD} y={PAD} width={plotW} height={plotH} fill="url(#hm-bg)" rx="6"/>

      {/* Grid */}
      {[1,2,3,4].map((i) => {
        const gx = PAD + (i / 4) * plotW;
        const gy = H - PAD - (i / 4) * plotH;
        return (
          <g key={i}>
            <line x1={gx} y1={PAD} x2={gx} y2={H - PAD} stroke="var(--line)" strokeWidth="0.5" strokeDasharray="2 3"/>
            <line x1={PAD} y1={gy} x2={W - PAD} y2={gy} stroke="var(--line)" strokeWidth="0.5" strokeDasharray="2 3"/>
          </g>
        );
      })}
      {/* Axes */}
      <line x1={PAD} y1={PAD} x2={PAD} y2={H - PAD} stroke="var(--line-strong)" strokeWidth="1"/>
      <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="var(--line-strong)" strokeWidth="1"/>
      <text x={W/2} y={H-8} textAnchor="middle" fontSize="10" fill="var(--ink-3)" fontFamily="Geist Mono, monospace">LIKELIHOOD</text>
      <text x={12} y={H/2} textAnchor="middle" fontSize="10" fill="var(--ink-3)" fontFamily="Geist Mono, monospace" transform={`rotate(-90 12,${H/2})`}>IMPACT</text>

      {/* Tick labels */}
      {[1,2,3,4,5].map(v => (
        <g key={v}>
          <text x={PAD + (v-1)/4 * plotW} y={H - PAD + 14} textAnchor="middle" fontSize="9" fill="var(--ink-3)" fontFamily="Geist Mono, monospace">{v}</text>
          <text x={PAD - 6} y={H - PAD - (v-1)/4 * plotH + 3} textAnchor="end" fontSize="9" fill="var(--ink-3)" fontFamily="Geist Mono, monospace">{v}</text>
        </g>
      ))}

      {/* Q4 projection outlines */}
      {points.map((p, i) => {
        const dist = Math.sqrt((p.q4X - p.curX) ** 2 + (p.q4Y - p.curY) ** 2);
        if (dist < 3) return null;
        return <circle key={"q4-" + i} cx={p.q4X} cy={p.q4Y} r={p.q4Size} fill="none"
          stroke={ragBorder[p.q4Rag]} strokeWidth="1" strokeDasharray="3 2" opacity="0.4"/>;
      })}
      {/* Velocity arrows */}
      {points.map((p, i) => {
        const dx = p.q4X - p.curX, dy = p.q4Y - p.curY;
        const dist = Math.sqrt(dx*dx + dy*dy);
        if (dist < 4) return null;
        const aid = p.vel > 0 ? "ah-r" : p.vel < 0 ? "ah-g" : "ah-a";
        const sc = p.vel > 0 ? "var(--red)" : p.vel < 0 ? "var(--green)" : "var(--amber)";
        const sw = Math.max(1, Math.abs(p.vel) * 0.7);
        const ratio = Math.max(0, (dist - p.size - 6) / dist);
        const ex = p.curX + dx * (1 - 0.3 * (1 - ratio));
        const ey = p.curY + dy * (1 - 0.3 * (1 - ratio));
        const sx = p.curX + dx * (p.size / dist);
        const sy = p.curY + dy * (p.size / dist);
        return <line key={"v-" + i} x1={sx} y1={sy} x2={ex} y2={ey} stroke={sc} strokeWidth={sw} opacity="0.75" markerEnd={`url(#${aid})`}/>;
      })}
      {/* Bubbles */}
      {points.map((p) => {
        const isSel = selectedId === p.r.id;
        return (
          <g key={"b-" + p.r.id} style={{cursor: "pointer"}} onClick={() => onSelect && onSelect(p.r.id)}>
            <circle cx={p.curX} cy={p.curY} r={p.size + 8} fill="transparent"/>
            <circle cx={p.curX} cy={p.curY} r={p.size}
              fill={ragSoft[p.curRag]}
              stroke={ragBorder[p.curRag]}
              strokeWidth={isSel ? 2.4 : 1.2}
              opacity={isSel ? 1 : 0.92}/>
            {p.size > 14 ? (
              <text x={p.curX} y={p.curY + 3} textAnchor="middle" fontSize="9" fontWeight="500" fill="var(--ink)" pointerEvents="none">
                {p.r.id}
              </text>
            ) : (
              <text x={p.curX} y={p.curY + 3} textAnchor="middle" fontSize="9" fontWeight="600" fill="var(--ink)" pointerEvents="none">
                {p.r.id.replace("R-", "")}
              </text>
            )}
          </g>
        );
      })}

      {/* Active quarter label */}
      <g transform={`translate(${W - PAD}, ${PAD - 12})`}>
        <text textAnchor="end" fontSize="11" fontFamily="Geist Mono, monospace" fontWeight="500" fill="var(--acc-ink)">{activeQ}</text>
      </g>
    </svg>
  );
}

// ---------- SEVERITY MATRIX (5×5 discrete heat matrix) ----------
// Same props/contract as Heatmap above (drop-in alternative view over the
// same risk data) — the classic audit heat-matrix instead of a continuous
// bubble plot: cells shade by the register's own RAG thresholds
// (ragFromScore: ≥15 red, 9–14 amber, <9 green) and risks bucket into
// whichever cell their rounded impact/likelihood lands in, clustering as
// stacked dots when more than one risk shares a cell.
const _SEV_ROWS = [5, 4, 3, 2, 1]; // impact, top(5) -> bottom(1)
const _SEV_COLS = [1, 2, 3, 4, 5]; // likelihood, left(1) -> right(5)
const _SEV_CELL_CAP = 6; // dots shown before a cell switches to a count badge

function SeverityMatrix({ risks, activeQ = "Now", onSelect, selectedId }) {
  if (!risks?.length) return null;

  // Same Now->Q4 interpolation Heatmap uses above, so the two views agree on
  // where a risk sits at any given quarter — just a different visual
  // encoding of identical underlying projection math.
  const qIdx = { "Now": -1, "Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3 }[activeQ] ?? -1;
  const t = qIdx === -1 ? 0 : (qIdx + 1) / 4;

  const points = risks.map(r => {
    const qs = projectQuarters(r);
    const nowImp = clamp(r.impact || likelihoodFromCE(r.ce), 1, 5);
    const nowLik = clamp(r.likelihood || likelihoodFromCE(r.ce), 1, 5);
    const q4Imp = clamp(nowImp + (r.velocity || 0) * 0.15, 1, 5);
    const q4Lik = clamp(nowLik + (r.velocity || 0) * 0.10, 1, 5);
    const curImp = nowImp + (q4Imp - nowImp) * t;
    const curLik = nowLik + (q4Lik - nowLik) * t;
    const curSc = qIdx === -1 ? r.score : qs[qIdx];
    return {
      r, score: curSc, rag: ragFromScore(curSc),
      impact: clamp(Math.round(curImp), 1, 5),
      likelihood: clamp(Math.round(curLik), 1, 5),
    };
  });

  const byCell = {};
  points.forEach(p => {
    const key = p.impact + "-" + p.likelihood;
    (byCell[key] = byCell[key] || []).push(p);
  });

  return (
    <div className="sevmatrix">
      <div className="sevmatrix-ylabel">Impact</div>
      <div className="sevmatrix-yticks">
        {_SEV_ROWS.map(v => <span key={v}>{v}</span>)}
      </div>
      <div className="sevmatrix-grid">
        {_SEV_ROWS.map(impact => _SEV_COLS.map(lik => {
          const cellPoints = byCell[impact + "-" + lik] || [];
          const zoneRag = ragFromScore(impact * lik);
          return (
            <div key={impact + "-" + lik} className={`sevmatrix-cell z-${zoneRag}`}>
              {cellPoints.slice(0, _SEV_CELL_CAP).map(p => (
                <span key={p.r.id}
                  className={"sevmatrix-dot rag-" + p.rag + (selectedId === p.r.id ? " sel" : "")}
                  title={`${p.r.name} — ${fmt2(p.score)}`}
                  onClick={() => onSelect && onSelect(p.r.id)}/>
              ))}
              {cellPoints.length > _SEV_CELL_CAP && (
                <span className="sevmatrix-count">{cellPoints.length}</span>
              )}
            </div>
          );
        }))}
      </div>
      <div className="sevmatrix-xticks">
        {_SEV_COLS.map(v => <span key={v}>{v}</span>)}
      </div>
      <div className="sevmatrix-xlabel">Likelihood</div>
    </div>
  );
}

// ---------- LINE + FORECAST CHART (Recharts) ----------
// Props unchanged from the old SVG version so all callers work without edits.
// Extra optional props: referenceValue / referenceLabel draw a horizontal threshold line.
function ForecastChart({ history, forecast, unit = "$M", color = "var(--acc)", decimals, chartMetrics, referenceValue, referenceLabel }) {
  // Called unconditionally, ahead of the early return below, per the Rules
  // of Hooks — dataLength safely degrades to 0 when history/forecast are
  // still empty on first render.
  const dataLength = (history?.length || 0) + (forecast?.length || 0);
  const zoom = useChartZoom(dataLength);
  const mounted = useMountedAfterPaint();

  // Build a unified data array. The last history point is also the forecast
  // anchor so the two lines connect without a gap.
  //
  // Memoized (not rebuilt inline every render) because <Brush> below treats
  // a new `data` array identity as "the series changed" and re-syncs its
  // internal window — including firing onChange even when the actual
  // numbers are identical. onChange feeds ctx.setFrac (shared zoom state),
  // whose update re-renders this component, which used to rebuild `data`
  // as a fresh array again, which Brush saw as another change... an
  // infinite loop that only shows up once something ELSE first triggers a
  // second render (e.g. confirming a HITL gate elsewhere on the page,
  // which re-renders every chart under the shared ChartZoomProvider) — it
  // never appears on the very first paint, since nothing has re-rendered
  // yet to trip it. React eventually throws "Maximum update depth
  // exceeded" once the loop runs away.
  const data = React.useMemo(() => {
    if (!history?.length || !forecast?.length) return null;
    const lastH = history[history.length - 1];
    const built = [
      ...history.map(d => ({ q: d.q, v: _finiteOrNull(d.v), base: null, lo: null, hiMinusLo: null, btPred: null })),
      ...forecast.map(d => ({
        q: d.q,
        v: null,
        base: _finiteOrNull(d.base),
        lo: _finiteOrNull(d.lo),
        hiMinusLo: _finiteOrNull(Number.isFinite(d.hi) && Number.isFinite(d.lo) ? d.hi - d.lo : null),
        btPred: null,
      })),
    ];
    // Seed forecast start at last history value so lines and band connect cleanly
    built[history.length - 1] = { ...built[history.length - 1], base: _finiteOrNull(lastH.v), lo: _finiteOrNull(lastH.v), hiMinusLo: 0 };

    // Backtest overlay: chartMetrics is the walk-forward backtest result
    // (backtesting.js's walkForwardBacktest) computed on this exact history —
    // it already carries `predicted`, the model's out-of-sample prediction for
    // each of the last `periods` quarters, made using only data available
    // BEFORE that quarter (never the actual for that quarter itself). Until
    // now only the aggregate RMSE/MAPE/R² numbers derived from it were shown;
    // the quarter-by-quarter predictions were computed and then thrown away.
    // Plotting them against the real `v` for the same quarters is what actually
    // answers "how did the forecast perform" — a single MAPE number tells you
    // the average miss, not whether it was consistently early/late or
    // right on a specific quarter.
    const btPeriods = chartMetrics?.predicted?.length || 0;
    if (btPeriods > 0 && btPeriods <= history.length) {
      const startIdx = history.length - btPeriods;
      for (let i = 0; i < btPeriods; i++) {
        built[startIdx + i] = { ...built[startIdx + i], btPred: _finiteOrNull(chartMetrics.predicted[i]) };
      }
      // Anchor the backtest line to the real value one quarter before its
      // first prediction, same reasoning as the history->forecast anchor
      // above — otherwise the dotted segment starts mid-air with a visible gap.
      // Flagged so the tooltip doesn't call this connector point a "prediction"
      // — it's just the actual value, repeated so the line has somewhere to
      // start from.
      if (startIdx > 0) {
        built[startIdx - 1] = { ...built[startIdx - 1], btPred: built[startIdx - 1].v, btIsAnchor: true };
      }
    }
    return built;
  }, [history, forecast, chartMetrics]);

  if (!data) return null;

  // Always at least 2 decimal places on the y-axis and hover tooltip —
  // $M charts used to default to 0dp, which hid meaningful sub-$1M movement.
  const dp = decimals ?? 2;

  const fmtV = v => {
    if (v == null || !Number.isFinite(v)) return '—';
    if (unit === "$M") return v >= 1000 ? `$${(v / 1000).toFixed(dp)}B` : `$${v.toFixed(dp)}M`;
    if (unit === "$") return `$${v.toFixed(dp)}`;
    if (unit === "%") return `${v.toFixed(dp)}%`;
    return v.toFixed(dp); // "score" and custom units
  };

  const lastH = history[history.length - 1];
  const splitQ = lastH.q;
  const btPeriods = chartMetrics?.predicted?.length || 0;

  const yAxisW = (unit === "%" && dp >= 2) ? 54 : (unit === "$M") ? (dp >= 2 ? 62 : 50) : 44;

  const fmtMt = (v, p = 2) => (v == null || !Number.isFinite(v)) ? '—' : v.toFixed(p);
  const mapeColor = v => v == null ? 'var(--ink-3)' : v < 5 ? 'var(--green-ink)' : v < 15 ? 'var(--amber-ink)' : 'var(--red-ink)';

  function ChartTooltip({ active, payload, label }) {
    if (!active || !payload?.length) return null;
    const pt = payload[0]?.payload ?? {};
    const isFc = pt.v == null && pt.base != null;
    const val = isFc ? pt.base : pt.v;
    const hi = (isFc && pt.lo != null && pt.hiMinusLo != null) ? pt.lo + pt.hiMinusLo : null;
    if (val == null) return null;
    // Real backtest prediction for this quarter (not the connector point) —
    // shown alongside the actual so a single hover answers "what did the
    // model say this quarter would be, and how far off was it."
    const hasBt = !isFc && pt.btPred != null && !pt.btIsAnchor;
    const btErrPct = hasBt && pt.v ? ((pt.btPred - pt.v) / pt.v) * 100 : null;
    return (
      <div style={{
        background: 'var(--bg)', border: '1px solid var(--line-strong)', borderRadius: 6,
        padding: '6px 10px', fontSize: 11, fontFamily: 'Geist Mono, monospace',
        boxShadow: '0 2px 8px rgba(0,0,0,0.12)', pointerEvents: 'none',
      }}>
        <div style={{ color: 'var(--ink-3)', fontSize: 9, marginBottom: 2 }}>{label}{isFc ? ' · FORECAST' : ''}</div>
        <div style={{ color: isFc ? color : 'var(--ink)', fontWeight: 600, fontSize: 13 }}>{fmtV(val)}</div>
        {isFc && pt.lo != null && hi != null && (
          <div style={{ color: 'var(--ink-3)', fontSize: 9 }}>{fmtV(pt.lo)} – {fmtV(hi)}</div>
        )}
        {hasBt && (
          <div style={{ color: 'var(--ink-3)', fontSize: 9, marginTop: 3, paddingTop: 3, borderTop: '1px solid var(--line)' }}>
            Model predicted <span style={{ color: 'var(--ink-2)' }}>{fmtV(pt.btPred)}</span>
            {btErrPct != null && (
              <span style={{ color: Math.abs(btErrPct) < 5 ? 'var(--green-ink)' : Math.abs(btErrPct) < 15 ? 'var(--amber-ink)' : 'var(--red-ink)' }}>
                {' '}({btErrPct >= 0 ? '+' : ''}{btErrPct.toFixed(1)}%)
              </span>
            )}
          </div>
        )}
      </div>
    );
  }

  const showZoom = dataLength >= ZOOM_MIN_POINTS;

  // The Brush windows the chart's x-domain down to [startIndex, endIndex] —
  // Recharts can't resolve a categorical ReferenceLine's x={splitQ} against a
  // domain that has scrolled/zoomed past that quarter, and silently emits a
  // NaN coordinate (`<line x1="NaN">`) instead of hiding it. Only render the
  // divider once its quarter is actually inside the visible window.
  const splitIndex = history.length - 1;
  const splitVisible = zoom.startIndex <= splitIndex && splitIndex <= zoom.endIndex;

  return (
    <>
      {showZoom && <ZoomControls zoom={zoom} color={color} />}
      <ResponsiveContainer width="100%" height={220}>
        {/* No syncId here on purpose. Recharts' own syncId cross-chart brush
            sync applies an incoming {startIndex, endIndex} to every synced
            chart unconditionally, with no bounds check against that chart's
            own data length (see recharts' useBrushSyncEventsListener). This
            screen renders several ForecastCharts side by side with genuinely
            different lengths (real EDGAR history vs. the synthetic 8Q
            fallback series), so one chart's broadcast lands out of bounds on
            another, which re-broadcasts its own (different) index back, and
            so on — a real cross-chart oscillation, not a false-positive
            identity issue, and it still reproduces the "Maximum update
            depth exceeded" crash in CartesianAxis's RenderedTicksReporter
            after upgrading recharts past the fix for that component's own
            unguarded dispatch. Cross-chart zoom sync is already handled
            correctly by ChartZoomProvider above (fraction-based, so it
            means the same thing across charts of different lengths) —
            syncId was redundant with it, not additive. */}
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
          <XAxis
            dataKey="q"
            tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
            tickLine={false}
            axisLine={{ stroke: 'var(--line)' }}
            interval={1}
          />
          <YAxis
            tickFormatter={fmtV}
            tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
            tickLine={false}
            axisLine={false}
            width={yAxisW}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: 'var(--line-strong)', strokeWidth: 1, strokeDasharray: '2 2' }} />

          {/* Confidence band: stacked areas give us lo→hi without background dependency */}
          <Area type="monotone" dataKey="lo" stackId="band"
            fill="transparent" stroke="none" legendType="none" activeDot={false} dot={false} />
          <Area type="monotone" dataKey="hiMinusLo" stackId="band"
            fill={color} fillOpacity={0.22} stroke="none" legendType="none" activeDot={false} dot={false} />

          {/* History line — solid */}
          <Line type="monotone" dataKey="v" stroke={color} strokeWidth={2}
            dot={{ r: 2.5, fill: color, strokeWidth: 0 }}
            activeDot={{ r: 5, fill: color, strokeWidth: 0 }}
            connectNulls={false} legendType="none" />

          {/* Forecast line — dashed */}
          <Line type="monotone" dataKey="base" stroke={color} strokeWidth={2}
            strokeDasharray="5 4" opacity={0.85}
            dot={{ r: 3, fill: 'var(--bg)', stroke: color, strokeWidth: 1.6 }}
            activeDot={{ r: 5, fill: color, strokeWidth: 0 }}
            connectNulls={false} legendType="none" />

          {/* Backtest overlay — dotted, neutral color (not the series color)
              so it reads as a diagnostic annotation over the actual line
              rather than a third competing data series. Only the last
              `periods` history quarters have a value here — everywhere else
              it's null, so this draws nothing outside that window. */}
          <Line type="monotone" dataKey="btPred" stroke="var(--ink-3)" strokeWidth={1.5}
            strokeDasharray="1 3" opacity={0.9}
            dot={{ r: 2.5, fill: 'var(--bg)', stroke: 'var(--ink-3)', strokeWidth: 1.4 }}
            activeDot={{ r: 4, fill: 'var(--ink-3)', strokeWidth: 0 }}
            connectNulls={false} legendType="none" />

          {/* History / forecast divider */}
          {splitVisible && (
            <ReferenceLine x={splitQ} stroke="var(--line-strong)" strokeDasharray="3 3" strokeWidth={0.8}
              label={{ value: 'FORECAST →', position: 'insideTopRight', fontSize: 8.5, fontFamily: 'Geist Mono, monospace', fill: 'var(--ink-3)', dy: -4 }} />
          )}

          {/* Optional horizontal threshold / target line */}
          {referenceValue != null && (
            <ReferenceLine y={referenceValue} stroke="var(--red)" strokeDasharray="4 3" strokeWidth={1} opacity={0.65}
              label={{ value: referenceLabel ?? 'Target', position: 'insideTopRight', fontSize: 8.5, fontFamily: 'Geist Mono, monospace', fill: 'var(--red-ink)' }} />
          )}

          {/* Zoom/pan: drag either handle to window the chart to a range of
              quarters, or use the +/− buttons above. Recharts windows the
              main chart's own domain to [startIndex, endIndex] automatically —
              `data` above always stays the full series regardless of zoom.
              Gated on `mounted` (see useMountedAfterPaint) — rendering Brush
              one tick after first paint, once ResponsiveContainer has a real
              measured width, avoids the transient NaN traveller coordinates
              it produces against an unmeasured container on the very first
              frame a Stage accordion opens. */}
          {showZoom && mounted && (
            <Brush dataKey="q" height={20} travellerWidth={8}
              startIndex={zoom.startIndex} endIndex={zoom.endIndex} onChange={zoom.onBrushChange}
              stroke={color} fill="var(--surface-2, var(--surface))"
              tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }} />
          )}
        </ComposedChart>
      </ResponsiveContainer>

      {btPeriods > 0 && (
        <div className="mono" style={{
          fontSize: 9, color: 'var(--ink-3)', padding: '4px 2px 0',
          display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
        }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ display: 'inline-block', width: 14, borderBottom: `2px solid ${color}` }} /> Actual
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ display: 'inline-block', width: 14, borderBottom: `2px dashed ${color}` }} /> Forecast
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ display: 'inline-block', width: 14, borderBottom: '2px dotted var(--ink-3)' }} />
            Backtest — the model's prediction for each of the last {btPeriods}Q, made before that quarter's actual was known
          </span>
        </div>
      )}

      {chartMetrics && (
        <div className="mono" style={{
          fontSize: 9.5, color: 'var(--ink-3)', padding: '5px 2px 0',
          display: 'flex', gap: 16, flexWrap: 'wrap', lineHeight: 1.6,
          borderTop: '1px solid var(--line)', marginTop: 2,
        }}>
          <span>RMSE <span style={{ color: 'var(--ink-2)' }}>{fmtMt(chartMetrics.rmse)}</span></span>
          <span>MAPE <span style={{ color: mapeColor(chartMetrics.mape) }}>{fmtMt(chartMetrics.mape)}%</span></span>
          <span>R² <span style={{ color: 'var(--ink-2)' }}>{fmtMt(chartMetrics.r2, 3)}</span></span>
          <span>MAE <span style={{ color: 'var(--ink-2)' }}>{fmtMt(chartMetrics.mae)}</span></span>
          <span>TME <span style={{ color: 'var(--ink-2)' }}>{chartMetrics.tme != null && Number.isFinite(chartMetrics.tme) ? (chartMetrics.tme >= 0 ? '+' : '') + chartMetrics.tme.toFixed(2) : '—'}</span></span>
        </div>
      )}
    </>
  );
}

// ---------- MULTI-SERIES FORECAST CHART (Recharts) ----------
// series: [{name, color, history:[{q,v}], forecast:[{q,base,lo,hi}]}]
function MultiSeriesForecastChart({ series, unit = "$M", decimals }) {
  const first = series?.[0];
  const dataLength = (first?.history?.length || 0) + (first?.forecast?.length || 0);
  const zoom = useChartZoom(dataLength);
  const mounted = useMountedAfterPaint();

  // Same stale-array hazard as ForecastChart above, compounded here: the
  // `series` prop itself is a brand-new array/objects literal built fresh
  // by ComparableChart on every render (peers spread into it), so a plain
  // useMemo keyed on `series` would never hit its cache. Instead we key off
  // the actual history/forecast array REFERENCES nested inside it (stable
  // as long as the underlying data hasn't changed) via a ref-compared
  // signature, so <Brush> only ever sees a new `data` array when a series
  // was actually added/removed/changed — not on every unrelated re-render.
  const _dataCacheRef = React.useRef({ sig: [], data: null });
  const _sig = [];
  (series || []).forEach(s => { _sig.push(s.name, s.history, s.forecast); });
  const _sigMatches = _sig.length === _dataCacheRef.current.sig.length
    && _sig.every((v, i) => v === _dataCacheRef.current.sig[i]);

  if (!series?.length) return null;
  if (!first?.history?.length || !first?.forecast?.length) return null;

  const dp      = decimals ?? (unit === "$M" ? 0 : 1);
  const histLen = first.history.length;

  const fmtV = v => {
    if (!Number.isFinite(v)) return '—';
    if (unit === "$M") return v >= 1000 ? `$${(v / 1000).toFixed(dp)}B` : `$${v.toFixed(dp)}M`;
    if (unit === "$")  return `$${v.toFixed(dp)}`;
    return `${v.toFixed(dp)}%`;
  };

  let data;
  if (_sigMatches) {
    data = _dataCacheRef.current.data;
  } else {
    const allPeriods = [...first.history, ...first.forecast];
    // Build a unified data array. Each period has keys `{name}_h` (history)
    // and `{name}_f` (forecast) for every series so Recharts can render N×2 lines.
    data = allPeriods.map((period, i) => {
      const isFc = i >= histLen;
      const row = { q: period.q, isFc };
      series.forEach(s => {
        row[`${s.name}_h`] = !isFc ? _finiteOrNull(s.history[i]?.v) : null;
        row[`${s.name}_f`] = isFc  ? _finiteOrNull(s.forecast[i - histLen]?.base) : null;
      });
      return row;
    });
    // Anchor forecast start at last history value so lines connect
    series.forEach(s => {
      data[histLen - 1][`${s.name}_f`] = _finiteOrNull(s.history[histLen - 1]?.v);
    });
    _dataCacheRef.current = { sig: _sig, data };
  }

  const splitQ  = first.history[histLen - 1]?.q;
  const yAxisW  = unit === "$M" ? 52 : 48;

  function ChartTooltip({ active, payload, label }) {
    if (!active || !payload?.length) return null;
    const pt    = payload[0]?.payload ?? {};
    const isFc  = !!pt.isFc;
    const vals  = series.map(s => ({
      name: s.name,
      color: s.color,
      value: isFc ? pt[`${s.name}_f`] : pt[`${s.name}_h`],
    })).filter(v => v.value != null);
    if (!vals.length) return null;
    return (
      <div style={{
        background: 'var(--bg)', border: '1px solid var(--line-strong)', borderRadius: 6,
        padding: '6px 10px', fontSize: 11, fontFamily: 'Geist Mono, monospace',
        boxShadow: '0 2px 8px rgba(0,0,0,0.12)', pointerEvents: 'none',
      }}>
        <div style={{ color: 'var(--ink-3)', fontSize: 9, marginBottom: 4 }}>{label}{isFc ? ' · FORECAST' : ''}</div>
        {vals.map(v => (
          <div key={v.name} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: v.color, flexShrink: 0 }}/>
            <span style={{ color: 'var(--ink-2)', flex: 1, fontSize: 10, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {v.name.length > 14 ? v.name.slice(0, 13) + '…' : v.name}
            </span>
            <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{fmtV(v.value)}</span>
          </div>
        ))}
      </div>
    );
  }

  const showZoom = dataLength >= ZOOM_MIN_POINTS;

  // See ForecastChart's identical guard above — a categorical ReferenceLine
  // whose x quarter has scrolled outside the Brush-windowed domain resolves
  // to NaN instead of being hidden, so gate it on the split quarter still
  // being inside [startIndex, endIndex].
  const splitIndex = histLen - 1;
  const splitVisible = zoom.startIndex <= splitIndex && splitIndex <= zoom.endIndex;

  return (
    <div>
      {showZoom && <ZoomControls zoom={zoom} color={first.color} />}
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
          <XAxis
            dataKey="q"
            tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
            tickLine={false}
            axisLine={{ stroke: 'var(--line)' }}
            interval={1}
          />
          <YAxis
            tickFormatter={fmtV}
            tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
            tickLine={false}
            axisLine={false}
            width={yAxisW}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: 'var(--line-strong)', strokeWidth: 1, strokeDasharray: '2 2' }} />

          {/* History / forecast divider */}
          {splitVisible && (
            <ReferenceLine x={splitQ} stroke="var(--line-strong)" strokeDasharray="3 3" strokeWidth={0.8}
              label={{ value: 'FORECAST →', position: 'insideTopRight', fontSize: 8.5, fontFamily: 'Geist Mono, monospace', fill: 'var(--ink-3)', dy: -4 }} />
          )}

          {/* One solid history line + one dashed forecast line per series */}
          {series.map(s => (
            <React.Fragment key={s.name}>
              <Line type="monotone" dataKey={`${s.name}_h`} stroke={s.color} strokeWidth={1.8}
                dot={{ r: 2, fill: s.color, strokeWidth: 0 }}
                activeDot={{ r: 4, fill: s.color, strokeWidth: 0 }}
                connectNulls={false} legendType="none" />
              <Line type="monotone" dataKey={`${s.name}_f`} stroke={s.color} strokeWidth={1.8}
                strokeDasharray="5 4" opacity={0.85}
                dot={{ r: 2.5, fill: 'var(--bg)', stroke: s.color, strokeWidth: 1.4 }}
                activeDot={{ r: 4, fill: s.color, strokeWidth: 0 }}
                connectNulls={false} legendType="none" />
            </React.Fragment>
          ))}

          {/* See useMountedAfterPaint's comment on ForecastChart's identical
              gate — same ResponsiveContainer/Brush first-paint hazard. */}
          {showZoom && mounted && (
            <Brush dataKey="q" height={20} travellerWidth={8}
              startIndex={zoom.startIndex} endIndex={zoom.endIndex} onChange={zoom.onBrushChange}
              stroke={first.color} fill="var(--surface-2, var(--surface))"
              tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }} />
          )}
        </ComposedChart>
      </ResponsiveContainer>

      {/* Legend rendered as HTML below the chart */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 18px', marginTop: 8, paddingLeft: yAxisW + 8 }}>
        {series.map(s => (
          <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 9.5, fontFamily: 'Geist Mono, monospace', color: 'var(--ink-2)' }}>
            <span style={{ display: 'inline-block', width: 14, height: 2, background: s.color, borderRadius: 1, flexShrink: 0 }}/>
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: s.color, flexShrink: 0 }}/>
            {s.name}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- M-Score gauge ----------
// peers: [{ ticker, m_score }] — peer benchmarking data from /edgar/peers.
// Peers plot as muted tick marks on the same scale as the subject company,
// with a rank + median summary and a sorted chip list below.
function MScoreGauge({ m, redThreshold = -1.78, amberThreshold = -2.22, peers = [] }) {
  // Scale: -4 (left/green) → 0 (right/red). Visual mapping: clamp.
  const min = -4, max = 0;
  const band = m > redThreshold ? "RED" : m > amberThreshold ? "AMBER" : "GREEN";
  const bandColor = band === "RED" ? "var(--red)" : band === "AMBER" ? "var(--amber)" : "var(--green)";
  const bandInk   = band === "RED" ? "var(--red-ink)" : band === "AMBER" ? "var(--amber-ink)" : "var(--green-ink)";

  const peerScores = (peers || [])
    .filter(p => p.m_score != null && Number.isFinite(p.m_score))
    .map(p => ({ ticker: p.ticker || p.name || p.company_name || "?", m: p.m_score }));

  let peerStats = null;
  if (peerScores.length > 0) {
    const sorted = [...peerScores].sort((a, b) => a.m - b.m); // most negative (safest) first
    const combined = [...sorted.map(p => p.m), m].sort((a, b) => a - b);
    const rank = combined.indexOf(m) + 1; // 1 = safest of the group
    const mid = Math.floor(sorted.length / 2);
    const median = sorted.length % 2 === 1
      ? sorted[mid].m
      : (sorted[mid - 1].m + sorted[mid].m) / 2;
    peerStats = { sorted, rank, total: combined.length, median };
  }

  // Semicircular gauge geometry — angles in the standard math convention
  // (0° = 3 o'clock, increasing counter-clockwise), matching Recharts' Pie
  // startAngle=180/endAngle=0 sweep over the top of the arc.
  const angleOf = v => 180 - clamp((v - min) / (max - min), 0, 1) * 180;
  const toXY = (angleDeg, r, cx, cy) => {
    const rad = (angleDeg * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy - r * Math.sin(rad) };
  };

  const W = 280, H = 160;
  const cx = W / 2, cy = H - 16;
  const rOuter = 108, rInner = 80;
  const needleLen = rInner - 6;
  const tickInner = rOuter + 3, tickOuter = rOuter + 11;
  const labelR = rOuter + 14;

  const bandData = [
    { name: "NORMAL",   value: amberThreshold - min,          color: "var(--green)" },
    { name: "GRAY ZONE", value: redThreshold - amberThreshold, color: "var(--amber)" },
    { name: "ELEVATED", value: max - redThreshold,            color: "var(--red)" },
  ];

  const needleTip = toXY(angleOf(m), needleLen, cx, cy);

  return (
    <div>
      <div style={{position:"relative", width: "100%", maxWidth: W, margin: "0 auto"}}>
        <PieChart width={W} height={H}>
          <Pie
            data={bandData}
            dataKey="value"
            cx={cx} cy={cy}
            startAngle={180} endAngle={0}
            innerRadius={rInner} outerRadius={rOuter}
            stroke="none"
            isAnimationActive={false}
          >
            {bandData.map((d, i) => <Cell key={i} fill={d.color} fillOpacity={0.30}/>)}
          </Pie>
        </PieChart>
        <svg width={W} height={H} style={{position:"absolute", top:0, left:0, pointerEvents:"none"}}>
          {/* Peer tick marks — same scale, muted, hoverable */}
          {peerStats && peerStats.sorted.map((p, i) => {
            const a = angleOf(p.m);
            const p1 = toXY(a, tickInner, cx, cy);
            const p2 = toXY(a, tickOuter, cx, cy);
            return (
              <line key={i} x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
                stroke="var(--ink-3)" strokeWidth="2.5" strokeLinecap="round" opacity="0.65">
                <title>{`${p.ticker}: ${p.m.toFixed(2)}`}</title>
              </line>
            );
          })}
          {/* Needle — className (not a static line) so it sweeps to its new
              position via CSS transition on x2/y2 rather than snapping,
              whenever the score changes (e.g. a fresh run moves it). */}
          <line className="gauge-needle" x1={cx} y1={cy} x2={needleTip.x} y2={needleTip.y}
            stroke="var(--ink)" strokeWidth="3" strokeLinecap="round"/>
          <circle cx={cx} cy={cy} r="5.5" fill="var(--ink)"/>
          {/* Threshold labels along the arc */}
          {[min, amberThreshold, redThreshold, max].map((v, i) => {
            const p = toXY(angleOf(v), labelR, cx, cy);
            return (
              <text key={i} x={p.x} y={p.y} textAnchor="middle" fontSize="9"
                fontFamily="Geist Mono, monospace" fill="var(--ink-3)">
                {v.toFixed(2)}
              </text>
            );
          })}
        </svg>
      </div>
      <div style={{display:"flex", alignItems:"baseline", gap: 8, marginTop: -4, flexWrap:"wrap", justifyContent:"center"}}>
        <span className="mono" style={{fontSize: 22, fontWeight: 500, letterSpacing: "-0.02em"}}>{m.toFixed(2)}</span>
        <span className="rag-chip" style={{background: `color-mix(in oklch, ${bandColor} 14%, transparent)`, color: bandInk}}>{band}</span>
        {peerStats && (
          <span style={{fontSize: 10.5, color: "var(--ink-3)"}}>
            vs {peerStats.total - 1} peer{peerStats.total - 1 !== 1 ? "s" : ""} · rank {peerStats.rank}/{peerStats.total} (1 = safest) · peer median {peerStats.median.toFixed(2)}
          </span>
        )}
      </div>
      {peerStats && (
        <div style={{display:"flex", flexWrap:"wrap", gap:4, marginTop:8}}>
          {peerStats.sorted.map((p, i) => (
            <span key={i} className="mono" style={{fontSize:9.5, padding:"2px 6px", borderRadius:4,
              background:"var(--surface-2,var(--surface))", border:"1px solid var(--line)", color:"var(--ink-2)"}}>
              {p.ticker} {p.m.toFixed(2)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------- Altman Z''-Score gauge ----------
// peers: [{ ticker, z_score }] — peer benchmarking data from /edgar/peers.
// Same visual pattern as MScoreGauge, but polarity is inverted: for Z'',
// LOWER is worse (distress), so the gauge's numeric scale runs from a
// high, safe value (min, left/green) down to a low, distressed value
// (max, right/red) — min > max on purpose, reusing angleOf() unchanged.
function ZScoreGauge({ z, distressThreshold = 1.1, greyThreshold = 2.6, peers = [] }) {
  const min = 8, max = -3;
  const band = z <= distressThreshold ? "DISTRESS" : z <= greyThreshold ? "GRAY ZONE" : "SAFE";
  const bandColor = band === "DISTRESS" ? "var(--red)" : band === "GRAY ZONE" ? "var(--amber)" : "var(--green)";
  const bandInk   = band === "DISTRESS" ? "var(--red-ink)" : band === "GRAY ZONE" ? "var(--amber-ink)" : "var(--green-ink)";

  const peerScores = (peers || [])
    .filter(p => p.z_score != null && Number.isFinite(p.z_score))
    .map(p => ({ ticker: p.ticker || p.name || p.company_name || "?", z: p.z_score }));

  let peerStats = null;
  if (peerScores.length > 0) {
    const sorted = [...peerScores].sort((a, b) => b.z - a.z); // highest (safest) first
    const combined = [...sorted.map(p => p.z), z].sort((a, b) => b - a);
    const rank = combined.indexOf(z) + 1; // 1 = safest of the group
    const mid = Math.floor(sorted.length / 2);
    const median = sorted.length % 2 === 1
      ? sorted[mid].z
      : (sorted[mid - 1].z + sorted[mid].z) / 2;
    peerStats = { sorted, rank, total: combined.length, median };
  }

  const angleOf = v => 180 - clamp((v - min) / (max - min), 0, 1) * 180;
  const toXY = (angleDeg, r, cx, cy) => {
    const rad = (angleDeg * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy - r * Math.sin(rad) };
  };

  const W = 280, H = 160;
  const cx = W / 2, cy = H - 16;
  const rOuter = 108, rInner = 80;
  const needleLen = rInner - 6;
  const tickInner = rOuter + 3, tickOuter = rOuter + 11;
  const labelR = rOuter + 14;

  const bandData = [
    { name: "SAFE",     value: min - greyThreshold,               color: "var(--green)" },
    { name: "GRAY ZONE", value: greyThreshold - distressThreshold, color: "var(--amber)" },
    { name: "DISTRESS", value: distressThreshold - max,           color: "var(--red)" },
  ];

  const needleTip = toXY(angleOf(z), needleLen, cx, cy);

  return (
    <div>
      <div style={{position:"relative", width: "100%", maxWidth: W, margin: "0 auto"}}>
        <PieChart width={W} height={H}>
          <Pie
            data={bandData}
            dataKey="value"
            cx={cx} cy={cy}
            startAngle={180} endAngle={0}
            innerRadius={rInner} outerRadius={rOuter}
            stroke="none"
            isAnimationActive={false}
          >
            {bandData.map((d, i) => <Cell key={i} fill={d.color} fillOpacity={0.30}/>)}
          </Pie>
        </PieChart>
        <svg width={W} height={H} style={{position:"absolute", top:0, left:0, pointerEvents:"none"}}>
          {/* Peer tick marks — same scale, muted, hoverable */}
          {peerStats && peerStats.sorted.map((p, i) => {
            const a = angleOf(p.z);
            const p1 = toXY(a, tickInner, cx, cy);
            const p2 = toXY(a, tickOuter, cx, cy);
            return (
              <line key={i} x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
                stroke="var(--ink-3)" strokeWidth="2.5" strokeLinecap="round" opacity="0.65">
                <title>{`${p.ticker}: ${p.z.toFixed(2)}`}</title>
              </line>
            );
          })}
          {/* Needle — className (not a static line) so it sweeps to its new
              position via CSS transition on x2/y2 rather than snapping,
              whenever the score changes (e.g. a fresh run moves it). */}
          <line className="gauge-needle" x1={cx} y1={cy} x2={needleTip.x} y2={needleTip.y}
            stroke="var(--ink)" strokeWidth="3" strokeLinecap="round"/>
          <circle cx={cx} cy={cy} r="5.5" fill="var(--ink)"/>
          {/* Threshold labels along the arc */}
          {[min, greyThreshold, distressThreshold, max].map((v, i) => {
            const p = toXY(angleOf(v), labelR, cx, cy);
            return (
              <text key={i} x={p.x} y={p.y} textAnchor="middle" fontSize="9"
                fontFamily="Geist Mono, monospace" fill="var(--ink-3)">
                {v.toFixed(2)}
              </text>
            );
          })}
        </svg>
      </div>
      <div style={{display:"flex", alignItems:"baseline", gap: 8, marginTop: -4, flexWrap:"wrap", justifyContent:"center"}}>
        <span className="mono" style={{fontSize: 22, fontWeight: 500, letterSpacing: "-0.02em"}}>{z.toFixed(2)}</span>
        <span className="rag-chip" style={{background: `color-mix(in oklch, ${bandColor} 14%, transparent)`, color: bandInk}}>{band}</span>
        {peerStats && (
          <span style={{fontSize: 10.5, color: "var(--ink-3)"}}>
            vs {peerStats.total - 1} peer{peerStats.total - 1 !== 1 ? "s" : ""} · rank {peerStats.rank}/{peerStats.total} (1 = safest) · peer median {peerStats.median.toFixed(2)}
          </span>
        )}
      </div>
      {peerStats && (
        <div style={{display:"flex", flexWrap:"wrap", gap:4, marginTop:8}}>
          {peerStats.sorted.map((p, i) => (
            <span key={i} className="mono" style={{fontSize:9.5, padding:"2px 6px", borderRadius:4,
              background:"var(--surface-2,var(--surface))", border:"1px solid var(--line)", color:"var(--ink-2)"}}>
              {p.ticker} {p.z.toFixed(2)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------- RISK FLOW SANKEY (audit closed loop) ----------
// 3-column sankey: each KEY RISK fans out to the business areas
// it impacts and to the audit/control work addressing it. Hovering
// or clicking a risk highlights its full path. A velocity strip
// below the chart shows when oversight cadence ramps up over 90d.
function RiskFlowSankey({ risks, maps, flowMeta, objectives = [], gate2Reductions = {}, selectedId, onSelect, onHover, hoverId, rssSignals, fredData, appetiteThreshold = 7.0 }) {
  if (!risks?.length || !flowMeta) return null;

  // All risks sorted by score descending
  const topRisks = [...risks].sort((a, b) => b.score - a.score);

  // Collect distinct impact areas across selected risks
  const impactSet = new Set();
  topRisks.forEach(r => (flowMeta[r.id]?.impacts || []).forEach(im => impactSet.add(im)));
  const impacts = [...impactSet];

  // Audit nodes: derive from linked MAPs + listed audits
  // We group into: "In-flight MAP", "Audit on plan", "Closed MAP"
  const auditGroups = [
    { id: "open",   label: "MAP · in flight",       color: "var(--amber)",  ink: "var(--amber-ink)" },
    { id: "plan",   label: "Audit on plan",         color: "var(--acc)",    ink: "var(--acc-ink)" },
    { id: "closed", label: "Closed / completed",    color: "var(--green)",  ink: "var(--green-ink)" },
  ];

  // For each risk, classify its audit count into the 3 buckets
  // based on the linked MAP completion % + Gate 2 objectives.
  function audCountsFor(rid) {
    const meta = flowMeta[rid];
    const linkedMaps = (maps || []).filter(m => m.linked_risk === rid);
    const gate2Objs = (objectives || []).filter(o => {
      const linkedRisks = o.linked_risks || (o.linked_risk ? [o.linked_risk] : []);
      return linkedRisks.includes(rid);
    });
    const basePlanned = meta ? Math.max(0, (meta.audits?.length || 0) - linkedMaps.length) : 0;
    const open = linkedMaps.filter(m => (m.completion_pct || 0) < 100).length;
    const closed = linkedMaps.filter(m => (m.completion_pct || 0) >= 100).length;
    return { open, plan: basePlanned + gate2Objs.length, closed };
  }

  // Residual risk computation
  function computeResidual(risk) {
    const velContrib = (risk.velocity || 0) * 0.875;
    const ceAdj = ({ STRONG: -1.75, ADEQUATE: -0.75, WEAK: 0.25, NONE: 1.0 })[risk.ce] || 0;

    // RSS contribution: sum velocity of signals linked to this risk
    const rssVel = (rssSignals || [])
      .filter(s => (s.affectedRisks || []).includes(risk.id) ||
        (s.domains || []).some(d => (flowMeta[risk.id]?.impacts || []).some(im => im.toLowerCase().includes(d.toLowerCase()))))
      .reduce((sum, s) => sum + (s.velocity || 0) * 0.20, 0);

    // Macro: FRED contractionary signals add pressure
    const macroAdj = (fredData || []).filter(f => f.dir === "CONTRACTIONARY").length * 0.20;

    const gate2Adj = (gate2Reductions || {})[risk.id] || 0;
    const projected = risk.score + velContrib + ceAdj + rssVel + macroAdj - gate2Adj;
    return Math.max(1, Math.min(25, parseFloat(projected.toFixed(1))));
  }

  // ---- Layout ----
  const W = 940;
  const padT = 36, padB = 28;
  const H = Math.max(380, topRisks.length * 38 + padT + padB);
  const colW = 8;                      // node bar width
  const labelGapL = 14;                // left col label gap
  const labelGapR = 14;
  // Column X positions for the node BARS
  const xRisk    = 168;
  const xImpact  = 360;
  const xAudit   = 560;
  const xResidual = 760;
  const plotH = H - padT - padB;

  // Compute risk node sizes by score weight (relative)
  const ragColor = { R: "var(--red)", A: "var(--amber)", G: "var(--green)" };
  const ragInk   = { R: "var(--red-ink)", A: "var(--amber-ink)", G: "var(--green-ink)" };

  const riskWeights = topRisks.map(r => Math.max(2, r.score));
  const totalRW = riskWeights.reduce((a, b) => a + b, 0);

  // Risk node heights
  const gapR = 8;
  const usableR = plotH - gapR * (topRisks.length - 1);
  const riskNodes = {};
  {
    let y = padT;
    topRisks.forEach((r, i) => {
      const h = (riskWeights[i] / totalRW) * usableR;
      riskNodes[r.id] = { y, h, r };
      y += h + gapR;
    });
  }

  // Impact nodes — height = sum of risk-weights flowing into it
  const impactWeights = {};
  impacts.forEach(im => { impactWeights[im] = 0; });
  topRisks.forEach((r, i) => {
    (flowMeta[r.id]?.impacts || []).forEach(im => {
      if (impactWeights[im] === undefined) return;
      // Each risk contributes equally to each of its impacts
      const n = (flowMeta[r.id].impacts || []).length || 1;
      impactWeights[im] += riskWeights[i] / n;
    });
  });
  const totalImW = Object.values(impactWeights).reduce((a, b) => a + b, 0);
  const gapI = 6;
  const usableI = plotH - gapI * (impacts.length - 1);
  const impactNodes = {};
  {
    // Sort impacts by descending weight for cleaner ribbons
    const sorted = [...impacts].sort((a, b) => impactWeights[b] - impactWeights[a]);
    let y = padT;
    sorted.forEach(im => {
      const h = (impactWeights[im] / totalImW) * usableI;
      impactNodes[im] = { y, h, im };
      y += h + gapI;
    });
  }

  // Audit nodes — width = sum of risk-weights contributing items in each bucket
  const audGroupWeights = { open: 0, plan: 0, closed: 0 };
  topRisks.forEach((r, i) => {
    const c = audCountsFor(r.id);
    const total = c.open + c.plan + c.closed;
    if (total === 0) return;
    audGroupWeights.open   += (riskWeights[i] * c.open)   / total;
    audGroupWeights.plan   += (riskWeights[i] * c.plan)   / total;
    audGroupWeights.closed += (riskWeights[i] * c.closed) / total;
  });
  const totalAW = Object.values(audGroupWeights).reduce((a, b) => a + b, 0) || 1;
  const gapA = 10;
  const usableA = plotH - gapA * (auditGroups.length - 1);
  const auditNodes = {};
  {
    let y = padT;
    auditGroups.forEach(g => {
      const h = (audGroupWeights[g.id] / totalAW) * usableA;
      auditNodes[g.id] = { y, h, ...g };
      y += h + gapA;
    });
  }

  // Build ribbons risk → impact
  const ribbonsRI = [];
  // Track per-source/per-dest offsets so stacking works
  const offR = {}; // risk side outflow
  const offI = {}; // impact side inflow
  topRisks.forEach((r, i) => offR[r.id] = 0);
  impacts.forEach(im => offI[im] = 0);
  topRisks.forEach((r, i) => {
    const meta = flowMeta[r.id];
    if (!meta) return;
    const list = meta.impacts || [];
    const n = list.length || 1;
    list.forEach(im => {
      if (!impactNodes[im]) return;
      const w = riskWeights[i] / n;
      const hSrc = (w / totalRW) * usableR;
      const hDst = (w / totalImW) * usableI;
      const ry = riskNodes[r.id].y + offR[r.id];
      const iy = impactNodes[im].y + offI[im];
      offR[r.id] += hSrc;
      offI[im]   += hDst;
      const x1 = xRisk + colW;
      const x2 = xImpact;
      const cx = (x1 + x2) / 2;
      const path =
        `M${x1},${ry} C${cx},${ry} ${cx},${iy} ${x2},${iy}` +
        ` L${x2},${iy + hDst} C${cx},${iy + hDst} ${cx},${ry + hSrc} ${x1},${ry + hSrc} Z`;
      ribbonsRI.push({ riskId: r.id, path, rag: r.rag });
    });
  });

  // Build ribbons impact → audit (route each impact's weight across audit
  // buckets weighted by the same per-risk audit-bucket split)
  const ribbonsIA = [];
  // Per impact, we know its inflow composition by risk. Re-derive that.
  const impactRiskShare = {}; // {impact: {riskId: weight}}
  impacts.forEach(im => impactRiskShare[im] = {});
  topRisks.forEach((r, i) => {
    const list = flowMeta[r.id]?.impacts || [];
    const n = list.length || 1;
    list.forEach(im => {
      if (impactRiskShare[im] === undefined) return;
      impactRiskShare[im][r.id] = (impactRiskShare[im][r.id] || 0) + riskWeights[i] / n;
    });
  });
  const offI_out = {}; impacts.forEach(im => offI_out[im] = 0);
  const offA_in = { open: 0, plan: 0, closed: 0 };
  // Iterate impacts in same sort order as nodes for stacking parity
  const sortedImpacts = [...impacts].sort((a, b) => impactNodes[a].y - impactNodes[b].y);
  sortedImpacts.forEach(im => {
    const riskShare = impactRiskShare[im];
    Object.entries(riskShare).forEach(([rid, w]) => {
      const ric = audCountsFor(rid);
      const tot = ric.open + ric.plan + ric.closed;
      if (tot === 0) return;
      ["open", "plan", "closed"].forEach(g => {
        const portion = (ric[g] / tot) * w;
        if (!portion) return;
        const hDst = (portion / totalAW) * usableA;
        // src side stacking uses impact column
        const hSrc = (portion / totalImW) * usableI;
        const iy = impactNodes[im].y + offI_out[im];
        const ay = auditNodes[g].y + offA_in[g];
        offI_out[im] += hSrc;
        offA_in[g]   += hDst;
        const x1 = xImpact + colW;
        const x2 = xAudit;
        const cx = (x1 + x2) / 2;
        const path =
          `M${x1},${iy} C${cx},${iy} ${cx},${ay} ${x2},${ay}` +
          ` L${x2},${ay + hDst} C${cx},${ay + hDst} ${cx},${iy + hSrc} ${x1},${iy + hSrc} Z`;
        ribbonsIA.push({ riskId: rid, path, rag: risks.find(rr => rr.id === rid)?.rag || "A", group: g });
      });
    });
  });

  // Pre-compute all projections so we can sort residual column by projected score
  const projectionsByRisk = {};
  topRisks.forEach(r => { projectionsByRisk[r.id] = computeResidual(r); });

  // Sort residual nodes by projected score descending (highest breach shown first)
  const sortedByResidual = [...topRisks].sort((a, b) => projectionsByRisk[b.id] - projectionsByRisk[a.id]);

  const residualNodes = {};
  {
    let y = padT;
    sortedByResidual.forEach(r => {
      const origIdx = topRisks.indexOf(r);
      const proj = projectionsByRisk[r.id];
      const projRag = proj >= 15 ? "R" : proj >= 9 ? "A" : "G";
      const h = (riskWeights[origIdx] / totalRW) * usableR;
      residualNodes[r.id] = {
        y, h, proj, projRag,
        delta: proj - r.score,
        breachesAppetite: proj >= appetiteThreshold,
      };
      y += h + gapR;
    });
  }

  // Build ribbons audit → residual (per risk, proportional to its audit weight)
  const ribbonsAR = [];
  const offA_out = { open: 0, plan: 0, closed: 0 };
  const offRes_in = {};
  topRisks.forEach(r => { offRes_in[r.id] = 0; });

  topRisks.forEach((r, i) => {
    const rac = audCountsFor(r.id);
    const tot = rac.open + rac.plan + rac.closed;
    if (tot === 0) return;
    const rn = residualNodes[r.id];
    ["open", "plan", "closed"].forEach(g => {
      const portion = (rac[g] / tot) * riskWeights[i];
      if (!portion) return;
      const hSrc = (portion / totalAW) * usableA;
      const hDst = (portion / totalRW) * usableR;
      const ay = auditNodes[g].y + offA_out[g];
      const ry2 = rn.y + offRes_in[r.id];
      offA_out[g]     += hSrc;
      offRes_in[r.id] += hDst;
      const x1 = xAudit + colW;
      const x2 = xResidual;
      const cx = (x1 + x2) / 2;
      const path =
        `M${x1},${ay} C${cx},${ay} ${cx},${ry2} ${x2},${ry2}` +
        ` L${x2},${ry2 + hDst} C${cx},${ry2 + hDst} ${cx},${ay + hSrc} ${x1},${ay + hSrc} Z`;
      ribbonsAR.push({ riskId: r.id, path, projRag: rn.projRag, group: g });
    });
  });

  const activeId = hoverId || selectedId;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", display: "block" }} xmlns="http://www.w3.org/2000/svg"
      onMouseLeave={() => onHover && onHover(null)}>
      <defs>
        <linearGradient id="rfs-band" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%"   stopColor="var(--surface-2)" stopOpacity="0.4"/>
          <stop offset="100%" stopColor="var(--surface-2)" stopOpacity="0"/>
        </linearGradient>
      </defs>

      {/* Column headers */}
      <text x={xRisk + colW / 2} y={18} textAnchor="middle" fontSize="10" fontFamily="Geist Mono, monospace"
            letterSpacing="0.06em" fill="var(--ink-3)">RISK</text>
      <text x={xImpact + colW / 2} y={18} textAnchor="middle" fontSize="10" fontFamily="Geist Mono, monospace"
            letterSpacing="0.06em" fill="var(--ink-3)">IMPACT AREA</text>
      <text x={xAudit + colW / 2} y={18} textAnchor="middle" fontSize="10" fontFamily="Geist Mono, monospace"
            letterSpacing="0.06em" fill="var(--ink-3)">AUDIT &amp; MAP</text>
      <text x={xResidual + colW / 2} y={14} textAnchor="middle" fontSize="10" fontFamily="Geist Mono, monospace"
            letterSpacing="0.06em" fill="var(--ink-3)">RESIDUAL RISK</text>
      <text x={xResidual + colW / 2} y={26} textAnchor="middle" fontSize="8" fontFamily="Geist Mono, monospace"
            fill="var(--red-ink)" opacity="0.75">appetite ≥ {appetiteThreshold}</text>

      {/* Ribbons risk → impact */}
      {ribbonsRI.map((rb, i) => {
        const isActive = activeId ? rb.riskId === activeId : false;
        const dimmed = activeId && !isActive;
        return (
          <path key={`ri-${i}`} d={rb.path} fill={ragColor[rb.rag]}
                opacity={dimmed ? 0.06 : isActive ? 0.55 : 0.22}/>
        );
      })}
      {/* Ribbons impact → audit */}
      {ribbonsIA.map((rb, i) => {
        const isActive = activeId ? rb.riskId === activeId : false;
        const dimmed = activeId && !isActive;
        return (
          <path key={`ia-${i}`} d={rb.path} fill={auditGroups.find(g => g.id === rb.group).color}
                opacity={dimmed ? 0.05 : isActive ? 0.5 : 0.18}/>
        );
      })}
      {/* Ribbons audit → residual */}
      {ribbonsAR.map((rb, i) => {
        const isActive = activeId ? rb.riskId === activeId : false;
        const dimmed = activeId && !isActive;
        return (
          <path key={`ar-${i}`} d={rb.path} fill={ragColor[rb.projRag]}
                opacity={dimmed ? 0.05 : isActive ? 0.45 : 0.16}/>
        );
      })}

      {/* Risk nodes + labels */}
      {topRisks.map(r => {
        const n = riskNodes[r.id];
        const isActive = activeId === r.id;
        const dimmed = activeId && !isActive;
        return (
          <g key={`rn-${r.id}`} style={{ cursor: "pointer" }}
             onMouseEnter={() => onHover && onHover(r.id)}
             onClick={() => onSelect && onSelect(r.id === selectedId ? null : r.id)}>
            <rect x={xRisk - 162} y={n.y - 2} width={162 + colW + 4} height={n.h + 4} fill="transparent"/>
            <rect x={xRisk} y={n.y} width={colW} height={Math.max(n.h, 2)} rx={2}
                  fill={ragColor[r.rag]} opacity={dimmed ? 0.4 : 1}/>
            <text x={xRisk - labelGapL} y={n.y + n.h / 2 - 2} textAnchor="end"
                  fontSize="11" fontWeight={isActive ? 600 : 500}
                  fill={dimmed ? "var(--ink-4)" : "var(--ink)"}>
              {truncate(r.name, 22)}
            </text>
            <text x={xRisk - labelGapL} y={n.y + n.h / 2 + 10} textAnchor="end"
                  fontSize="9.5" fontFamily="Geist Mono, monospace"
                  fill={dimmed ? "var(--ink-4)" : ragInk[r.rag]}>
              {r.id} · {r.score.toFixed(1)} · v{r.velocity >= 0 ? "+" : ""}{r.velocity}
            </text>
          </g>
        );
      })}

      {/* Impact nodes + labels */}
      {impacts.map(im => {
        const n = impactNodes[im];
        if (!n) return null;
        // Find which highlighted risks pass through
        const involved = activeId
          ? (flowMeta[activeId]?.impacts || []).includes(im)
          : false;
        const dimmed = activeId && !involved;
        return (
          <g key={`in-${im}`}>
            <rect x={xImpact} y={n.y} width={colW} height={Math.max(n.h, 2)} rx={2}
                  fill="var(--ink-2)" opacity={dimmed ? 0.25 : 0.8}/>
            <text x={xImpact + colW + 6} y={n.y + n.h / 2 + 4}
                  fontSize="10.5" fontWeight={involved ? 500 : 400}
                  fill={dimmed ? "var(--ink-4)" : "var(--ink-2)"}>
              {truncate(im, 18)}
            </text>
          </g>
        );
      })}

      {/* Audit nodes + labels */}
      {auditGroups.map(g => {
        const n = auditNodes[g.id];
        if (!n) return null;
        return (
          <g key={`an-${g.id}`}>
            <rect x={xAudit} y={n.y} width={colW} height={Math.max(n.h, 2)} rx={2}
                  fill={g.color}/>
            <text x={xAudit + colW + 6} y={n.y + n.h / 2 - 2}
                  fontSize="11" fontWeight="500" fill={g.ink}>
              {g.label}
            </text>
            <text x={xAudit + colW + 6} y={n.y + n.h / 2 + 11}
                  fontSize="9.5" fontFamily="Geist Mono, monospace" fill="var(--ink-3)">
              {/* compute count */}
              {(() => {
                let c = 0;
                topRisks.forEach(r => { c += audCountsFor(r.id)[g.id]; });
                return `${c} item${c === 1 ? "" : "s"}`;
              })()}
            </text>
          </g>
        );
      })}

      {/* Residual risk nodes + labels — sorted by projected score, breaches flagged */}
      {topRisks.map(r => {
        const rn = residualNodes[r.id];
        if (!rn) return null;
        const isActive = activeId === r.id;
        const dimmed = activeId && !isActive;
        const deltaSign = rn.delta >= 0 ? "+" : "";
        const labelColor = ragColor[rn.projRag];
        const labelInk = ragInk[rn.projRag];
        return (
          <g key={`res-${r.id}`}>
            {rn.breachesAppetite && (
              <rect x={xResidual - 3} y={rn.y - 3} width={colW + 6} height={Math.max(rn.h, 2) + 6} rx={3}
                    fill="none" stroke="var(--red)" strokeWidth={1.5} strokeDasharray="3 2"
                    opacity={dimmed ? 0.2 : 0.65}/>
            )}
            <rect x={xResidual} y={rn.y} width={colW} height={Math.max(rn.h, 2)} rx={2}
                  fill={labelColor} opacity={dimmed ? 0.3 : 1}/>
            <text x={xResidual + colW + 6} y={rn.y + rn.h / 2 - 2}
                  fontSize="11" fontWeight="500" fill={dimmed ? "var(--ink-4)" : labelInk}>
              {rn.proj.toFixed(1)}{rn.breachesAppetite && !dimmed ? " !" : ""}
            </text>
            <text x={xResidual + colW + 6} y={rn.y + rn.h / 2 + 11}
                  fontSize="9.5" fontFamily="Geist Mono, monospace"
                  fill={dimmed ? "var(--ink-4)" : "var(--ink-3)"}>
              {deltaSign}{rn.delta.toFixed(1)} projected
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function truncate(s, n) { return s.length > n ? s.slice(0, n - 1) + "…" : s; }

// useChartZoom/ZoomControls/useMountedAfterPaint/ZOOM_MIN_POINTS exported so
// other chart-bearing files with no build-time import of this one (e.g.
// governance.jsx's Peer Benchmarking time-series chart) can add the same
// zoom/pan UX without reimplementing it — see governance.jsx's
// PeerTimeSeriesChart for a caller with no ChartZoomProvider ancestor (falls
// back to independent local-only zoom state per useChartZoom's own docs).
Object.assign(window, {
  Heatmap, SeverityMatrix, ForecastChart, MultiSeriesForecastChart, MScoreGauge, ZScoreGauge,
  RiskFlowSankey, truncate, ChartZoomProvider, useChartZoom, ZoomControls, useMountedAfterPaint,
  ZOOM_MIN_POINTS,
});
