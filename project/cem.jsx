/* ============================================================
   Control Event Monitor — real-time event feed
   Tiered stakeholder notifications + AI root cause
   ============================================================ */

const TIERS = [
  { id: "owner", label: "Control Owner",   sevs: ["P1","P2","P3"], delay: 0    },
  { id: "mgmt",  label: "Management",      sevs: ["P1","P2"],      delay: 1500 },
  { id: "cae",   label: "CAE",             sevs: ["P1","P2"],      delay: 2400 },
  { id: "cfo",   label: "CFO",             sevs: ["P1"],           delay: 3500 },
  { id: "board", label: "Board / AC",      sevs: ["P1"],           delay: 6000 },
];

function notifMsgFor(tier, ev) {
  switch (tier.id) {
    case "owner": return `Control "${ev.control}" failed. Investigation required. Risk: ${ev.risk}.`;
    case "mgmt":  return `${ev.severity} control breakdown in ${ev.area}. MAP required within ${ev.severity === "P1" ? "24" : "72"} hrs.`;
    case "cae":   return `${ev.severity} control event: ${ev.control}. Audit response under assessment.`;
    case "cfo":   return `MATERIAL control failure: ${ev.control}. Exposure: ${ev.exposure}.`;
    case "board": return `Board AC: material control failure in ${ev.area}. CFO and CAE engaged.`;
    default:      return "";
  }
}

function CEMPanel({ events, setEvents, filter, setFilter, expanded, setExpanded, onAckNotif, onInject, ticker }) {
  const seenIdsRef = useRef(null);
  const [pollState, setPollState] = useState({ checking: false, lastChecked: null });

  // Poll SEC EDGAR for new 8-K filings every 5 minutes
  useEffect(() => {
    if (!ticker) return;
    seenIdsRef.current = new Set(events.map(e => e.id));

    async function poll() {
      if (!window.MCP?.fetch8kEvents || !window.MCP?.map8kToCemEvents) return;
      setPollState(s => ({ ...s, checking: true }));
      try {
        const result = await window.MCP.fetch8kEvents(ticker);
        const allMapped = window.MCP.map8kToCemEvents(result);
        const seen = seenIdsRef.current;
        const newEvs = allMapped.filter(e => !seen.has(e.id));
        if (newEvs.length > 0) {
          newEvs.forEach(e => seen.add(e.id));
          setEvents(prev => [...newEvs, ...prev]);
          newEvs.forEach(ev => {
            TIERS.filter(t => t.sevs.includes(ev.severity)).forEach(tier => {
              setTimeout(() => {
                setEvents(prev => prev.map(e => e.id !== ev.id ? e : {
                  ...e,
                  notifs: [...(e.notifs || []), { tid: tier.id, tier: tier.label, msg: notifMsgFor(tier, ev), sentAt: Date.now(), status: "pending" }],
                }));
              }, tier.delay);
            });
          });
        }
        setPollState({ checking: false, lastChecked: new Date() });
      } catch (_) {
        setPollState(s => ({ ...s, checking: false, lastChecked: new Date() }));
      }
    }

    poll();
    const id = setInterval(poll, 5 * 60 * 1000);
    return () => clearInterval(id);
  }, [ticker]); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = filter === "all" ? events : events.filter(e => e.severity === filter);
  const counts = events.reduce((acc, e) => {
    if (e.severity === "P1") acc.p1++; else if (e.severity === "P2") acc.p2++;
    const ack = (e.notifs || []).some(n => n.status === "ack");
    if (ack) acc.ack++;
    return acc;
  }, { p1: 0, p2: 0, ack: 0 });
  const ackTimes = events.flatMap(e => (e.notifs || []).filter(n => n.status === "ack").map(n => (n.ackAt - n.sentAt) / 60000));
  const avgMin = ackTimes.length ? Math.round(ackTimes.reduce((a,b) => a+b, 0) / ackTimes.length) : null;

  return (
    <div data-screen-label="Control Event Monitor" className="bb-panel" style={{height:"calc(100% + 40px)", overflow:"hidden"}}>
      <BBTermHeader
        section="CONTROL EVENT MONITOR"
        title="Real-time Control Breakdown Detection"
        status={`${events.length} EVENTS  ·  TIERED STAKEHOLDER CASCADE  ·  AI ROOT-CAUSE ANALYSIS`}
        actions={
          <div style={{display:"flex",gap:8,alignItems:"center"}}>
            {pollState.lastChecked && (
              <span style={{fontSize:10,color:"var(--ink-3)",fontFamily:"'Geist Mono',monospace"}}>
                {pollState.checking
                  ? "⟳ CHECKING 8-K…"
                  : `8-K ${pollState.lastChecked.toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false})}`}
              </span>
            )}
            <button className="btn btn-sm" onClick={onInject}><Icon name="bolt" size={12}/> INJECT EVENT</button>
          </div>
        }
      />

      {/* Stat ticker */}
      <div className="bb-stat-ticker">
        <div className="bb-ticker-item"><div className="bb-ticker-label">P1 CRITICAL</div><div className={`bb-ticker-val${counts.p1 > 0 ? " red" : ""}`}>{counts.p1}</div></div>
        <div className="bb-ticker-item"><div className="bb-ticker-label">P2 HIGH</div><div className={`bb-ticker-val${counts.p2 > 0 ? " amber" : ""}`}>{counts.p2}</div></div>
        <div className="bb-ticker-item"><div className="bb-ticker-label">ACKNOWLEDGED</div><div className={`bb-ticker-val${counts.ack > 0 ? " green" : ""}`}>{counts.ack}</div></div>
        <div className="bb-ticker-item"><div className="bb-ticker-label">AVG RESP (MIN)</div><div className="bb-ticker-val">{avgMin == null ? "—" : avgMin}</div></div>
        <div className="bb-ticker-item"><div className="bb-ticker-label">TOTAL EVENTS</div><div className="bb-ticker-val">{events.length}</div></div>
      </div>

      <div className="cem-toolbar">
        {[
          { id: "all", l: "All" },
          { id: "P1",  l: "P1 Critical" },
          { id: "P2",  l: "P2 High" },
          { id: "P3",  l: "P3 Medium" },
        ].map(f => (
          <button key={f.id} className={"cem-filter" + (filter === f.id ? " active" : "")} onClick={() => setFilter(f.id)}>{f.l}</button>
        ))}
      </div>

      <div className="cem-event-list">
        <div className="bb-section-sep">
          <span>EVENT LOG</span>
          <span>{filtered.length} EVENTS SHOWN</span>
        </div>

        {filtered.length === 0 ? (
          <Empty>No control events match this filter. Click "Inject event" to fire a synthetic alert, or run in MCP/Live mode to load real 8-K events.</Empty>
        ) : (
          filtered.map(ev => (
            <CEMEvent
              key={ev.id}
              ev={ev}
              expanded={expanded.has(ev.id)}
              onToggle={() => {
                const next = new Set(expanded);
                next.has(ev.id) ? next.delete(ev.id) : next.add(ev.id);
                setExpanded(next);
              }}
              onAckNotif={(tierId) => onAckNotif(ev.id, tierId)}
            />
          ))
        )}
      </div>
    </div>
  );
}

function CEMStat({ l, v, color }) {
  return (
    <div className="cem-stat">
      <div className="v" style={color ? {color} : null}>{v}</div>
      <div className="l">{l}</div>
    </div>
  );
}

function CEMEvent({ ev, expanded, onToggle, onAckNotif }) {
  const tiers = TIERS.filter(t => t.sevs.includes(ev.severity));
  const [fair, setFair] = React.useState(null);
  const [fairBusy, setFairBusy] = React.useState(false);
  const [fairError, setFairError] = React.useState(null);

  // A live CEM event (from an 8-K filing, before the pipeline run that
  // created it has been saved) has no cem_events.id yet, so this is always
  // a persist:false preview — see fair_endpoints.QuantifyRequest.persist.
  // Once the run is saved, the same event carries a real DB id and Risk
  // Quantification's own screen can re-run this quantification persisted.
  async function runQuantify(e) {
    e.stopPropagation();
    if (fairBusy) return;
    setFairBusy(true); setFairError(null);
    try {
      const result = await window.MCP.fairQuantify({
        resource_type: "cem_event",
        resource_ref: String(ev.id),
        cem_severity: ev.severity,
        persist: false,
      });
      setFair(result);
    } catch (err) {
      setFairError(err.message || String(err));
    } finally {
      setFairBusy(false);
    }
  }

  return (
    <div className={`cem-event ${ev.severity}`}>
      <Clickable className="cem-head" onClick={onToggle}>
        <span className="sev">{ev.severity}</span>
        <span className="name">{ev.control}</span>
        {ev.source === "8-K Filing" && (
          <span style={{fontSize:10, padding:"1px 6px", borderRadius:4, background:"var(--blue-soft)", color:"var(--blue-ink)", fontWeight:600, letterSpacing:".04em", flexShrink:0}}>
            8-K {ev.filingDate || ""}
          </span>
        )}
        <span className="ts mono">{new Date(ev.ts).toLocaleTimeString("en-US", {hour:"2-digit",minute:"2-digit",second:"2-digit"})}</span>
        <Icon name={expanded ? "chev-u" : "chev-d"} size={14} className="muted"/>
      </Clickable>
      {expanded && (
        <div className="cem-body">
          <div className="cem-meta">
            <CEMMeta l="Area"     v={ev.area}/>
            <CEMMeta l="Category" v={ev.category}/>
            <CEMMeta l="Risk"     v={ev.risk}/>
            <CEMMeta l="Exposure" v={ev.exposure}/>
            <div className="mi">
              <div className="ml">Modeled exposure (FAIR)</div>
              <div className="mv">
                {fair ? (
                  <span className="mono" style={{ fontWeight: 700 }}>
                    ${fair.ale.toFixed(2)}M ALE
                    <span style={{ fontWeight: 400, color: "var(--ink-4)", marginLeft: 6 }}>
                      (P90 ${fair.p90.toFixed(2)}M · {fair.magnitude_source})
                    </span>
                  </span>
                ) : (
                  <button type="button" className="btn btn-sm" onClick={runQuantify} disabled={fairBusy}>
                    {fairBusy ? "Quantifying…" : "Quantify"}
                  </button>
                )}
                {fairError && <div className="mono" style={{ fontSize: 10, color: "var(--red-ink)", marginTop: 3 }}>{fairError}</div>}
              </div>
            </div>
          </div>

          <div className="cem-section-lbl">Root cause analysis</div>
          <div className="rca-box">
            {ev.rcLoading ? <><span className="spin"/> Analysing root cause…</> : ev.rc}
          </div>

          <div className="cem-section-lbl">Stakeholder notifications</div>
          <div>
            {tiers.map(tier => {
              const n = (ev.notifs || []).find(x => x.tid === tier.id);
              if (!n) return (
                <div key={tier.id} className="notif-row">
                  <div className="a pend">…</div>
                  <div className="c"><div className="role">{tier.label}</div><div className="msg muted">Queued…</div><div className="status">PENDING</div></div>
                </div>
              );
              if (n.status === "ack") {
                const rt = Math.round((n.ackAt - n.sentAt) / 60000);
                return (
                  <div key={tier.id} className="notif-row">
                    <div className="a ack"><Icon name="check" size={11}/></div>
                    <div className="c"><div className="role">{tier.label}</div><div className="msg">{n.msg}</div><div className="status ack">ACKNOWLEDGED · {rt}m</div></div>
                  </div>
                );
              }
              return (
                <div key={tier.id} className="notif-row">
                  <div className="a sent">!</div>
                  <div className="c">
                    <div className="role">{tier.label}</div>
                    <div className="msg">{n.msg}</div>
                    <div className="status sent">SENT · {new Date(n.sentAt).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit"})}</div>
                    <button className="ack-btn" onClick={() => onAckNotif(tier.id)}>Acknowledge</button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function CEMMeta({ l, v }) {
  return (
    <div className="mi">
      <div className="ml">{l}</div>
      <div className="mv">{v}</div>
    </div>
  );
}

// ── Dendrai UBO Governance Brain panel ─────────────────────────────────────────

const _CEM_API_KEY = import.meta.env.VITE_API_KEY || "";
const _cemAuthHdr  = () => _CEM_API_KEY ? { "X-API-Key": _CEM_API_KEY } : {};

function _uboBase() { return window.MCP_API_BASE || "/api/mcp"; }

const _UBO_TIER_STYLE = {
  CRITICAL: { bg: "var(--red-soft)",   ink: "var(--red-ink)"   },
  HIGH:     { bg: "var(--amber-soft)", ink: "var(--amber-ink)" },
  MEDIUM:   { bg: "var(--blue-soft)",  ink: "var(--blue-ink)"  },
  LOW:      { bg: "var(--green-soft)", ink: "var(--green-ink)" },
};
const _UBO_VERDICT_STYLE = {
  ESCALATE:          { bg: "var(--red-soft)",    ink: "var(--red-ink)"   },
  MONITOR:           { bg: "var(--amber-soft)",  ink: "var(--amber-ink)" },
  CLEAR:             { bg: "var(--green-soft)",  ink: "var(--green-ink)" },
  INSUFFICIENT_DATA: { bg: "var(--surface-2)",   ink: "var(--ink-3)"     },
};

// ── Adjudication funnel — proportional SVG flow (same hand-rolled banded-path
// technique as charts.jsx's RiskFlowSankey, not the d3-sankey library) telling
// the governance story in one glance: how much traffic, how much gets caught,
// what the Council decided, how often humans agreed. Built entirely from data
// the screen already has loaded — no extra fetch. Counts are windowed to
// whatever the underlying queries already cap at (raw feed ~200, adjudicated
// ~100), so this reads as "recent activity," not an all-time total — labeled
// accordingly rather than implying a false precision.
// Up/down reorder controls shared by every pane in UBOGovPanel's
// user-customizable pane order (Human Review Queue / Adjudication Funnel /
// Risk Pulse Timeline). No "arrow-down" in the shared Icon set, so the down
// button reuses "arrow-up" rotated 180deg rather than adding a near-duplicate
// icon just for this.
function PaneMoveButtons({ onMoveUp, onMoveDown, canMoveUp, canMoveDown }) {
  if (!onMoveUp && !onMoveDown) return null;
  return (
    <div style={{ display: "flex", gap: 2, marginLeft: "auto" }}>
      <button className="btn btn-sm btn-ghost" style={{ padding: "2px 5px" }} disabled={!canMoveUp}
        onClick={onMoveUp} title="Move pane up">
        <Icon name="arrow-up" size={11} />
      </button>
      <button className="btn btn-sm btn-ghost" style={{ padding: "2px 5px" }} disabled={!canMoveDown}
        onClick={onMoveDown} title="Move pane down">
        <span style={{ display: "inline-flex", transform: "rotate(180deg)" }}><Icon name="arrow-up" size={11} /></span>
      </button>
    </div>
  );
}

function AdjudicationFunnel({ rawRows, adjudicated, onMoveUp, onMoveDown, canMoveUp, canMoveDown }) {
  const flagged  = rawRows.filter(r => (r.risk_flags || []).length > 0);
  const clean    = rawRows.length - flagged.length;

  const verdictOrder = ["CLEAR", "MONITOR", "ESCALATE", "INSUFFICIENT_DATA"];
  const verdictCounts = {};
  verdictOrder.forEach(v => { verdictCounts[v] = 0; });
  adjudicated.forEach(r => {
    const v = r.final_verdict || "INSUFFICIENT_DATA";
    if (verdictCounts[v] == null) verdictCounts[v] = 0;
    verdictCounts[v]++;
  });

  // Human-review outcome: rows that either still need review, or carry the
  // "[HUMAN REVIEW" marker _human_review_adjudication appends to
  // adjudicator_reasoning once reviewed. Confirmed/Overridden is a proxy
  // (verdict still ESCALATE after review = confirmed) using only fields this
  // screen already fetches — Model Health's Agent Calibration panel has the
  // exact ai_final_verdict-vs-human_verdict comparison if precision matters.
  const reviewed = adjudicated.filter(r => (r.adjudicator_reasoning || "").includes("[HUMAN REVIEW"));
  const pending  = adjudicated.filter(r => r.requires_human_review);
  const confirmed = reviewed.filter(r => r.final_verdict === "ESCALATE").length;
  const overridden = reviewed.length - confirmed;

  if (!rawRows.length && !adjudicated.length) return null;

  const W = 900, H = 168, PAD_T = 30, PAD_B = 14;
  const plotH = H - PAD_T - PAD_B;
  const cols = [40, 260, 500, 760]; // x centers of the 4 stage columns
  const nodeW = 10;

  const stageA = [{ key: "ingested", label: "Telemetry", n: rawRows.length, color: "var(--ink-3)" }];
  const stageB = [
    { key: "flagged", label: "Flagged", n: flagged.length, color: "var(--amber)" },
    { key: "clean",   label: "Clean",   n: clean,           color: "var(--ink-4)" },
  ].filter(s => s.n > 0);
  const stageC = [
    { key: "CLEAR",             label: "Clear",     n: verdictCounts.CLEAR,             color: "var(--green)" },
    { key: "MONITOR",           label: "Monitor",   n: verdictCounts.MONITOR,           color: "var(--amber)" },
    { key: "ESCALATE",          label: "Escalate",  n: verdictCounts.ESCALATE,          color: "var(--red)" },
    { key: "INSUFFICIENT_DATA", label: "Insuff.",   n: verdictCounts.INSUFFICIENT_DATA, color: "var(--ink-4)" },
  ].filter(s => s.n > 0);
  const stageD = [
    { key: "confirmed",  label: "Confirmed",  n: confirmed,          color: "var(--green)" },
    { key: "overridden", label: "Overridden", n: overridden,         color: "var(--blue)" },
    { key: "pending",    label: "Pending",    n: pending.length,     color: "var(--amber)" },
  ].filter(s => s.n > 0);

  function layoutCol(stage, x) {
    const total = stage.reduce((a, s) => a + s.n, 0) || 1;
    const gap = 6;
    const usable = plotH - gap * Math.max(0, stage.length - 1);
    let y = PAD_T;
    return stage.map(s => {
      const h = Math.max(3, (s.n / total) * usable);
      const node = { ...s, x, y, h };
      y += h + gap;
      return node;
    });
  }

  const nodesA = layoutCol(stageA, cols[0]);
  const nodesB = layoutCol(stageB, cols[1]);
  const nodesC = layoutCol(stageC, cols[2]);
  const nodesD = layoutCol(stageD, cols[3]);

  // Straight-proportional band between two adjacent-column node sets, split
  // by relative weight (good enough for a 2-4 node funnel; no crossing-
  // minimization needed at this node count).
  function bands(fromNodes, toNodes) {
    if (!fromNodes.length || !toNodes.length) return [];
    const totalFrom = fromNodes.reduce((a, n) => a + n.n, 0) || 1;
    const totalTo   = toNodes.reduce((a, n) => a + n.n, 0) || 1;
    const out = [];
    let curTo = toNodes[0] ? toNodes[0].y : PAD_T;
    const toCursor = {};
    toNodes.forEach(n => { toCursor[n.key] = n.y; });
    fromNodes.forEach(fn => {
      let curFrom = fn.y;
      // Distribute this "from" node's flow across "to" nodes proportional to their share.
      toNodes.forEach(tn => {
        const w = (fn.n / totalFrom) * (tn.n / totalTo) * Math.min(totalFrom, totalTo);
        if (w <= 0.01) return;
        const fromH = (w / fn.n) * fn.h;
        const toH   = (w / tn.n) * tn.h;
        out.push({
          x1: fn.x + nodeW, y1: curFrom, h1: fromH,
          x2: tn.x,         y2: toCursor[tn.key], h2: toH,
          color: tn.color,
        });
        curFrom += fromH;
        toCursor[tn.key] += toH;
      });
    });
    return out;
  }

  function Band({ x1, y1, h1, x2, y2, h2, color }) {
    const mid = (x1 + x2) / 2;
    const d = `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2} L${x2},${y2 + h2} C${mid},${y2 + h2} ${mid},${y1 + h1} ${x1},${y1 + h1} Z`;
    return <path d={d} fill={color} opacity={0.16} />;
  }

  function Node({ n }) {
    return (
      <g>
        <rect x={n.x} y={n.y} width={nodeW} height={n.h} fill={n.color} rx={2} />
        <text x={n.x + nodeW + 6} y={n.y + n.h / 2 - 5} fontSize={9.5} fontWeight={700} fill="var(--ink-2)">
          {n.label}
        </text>
        <text x={n.x + nodeW + 6} y={n.y + n.h / 2 + 8} fontSize={10.5} fontWeight={700} fontFamily="'Geist Mono',monospace" fill={n.color}>
          {n.n}
        </text>
      </g>
    );
  }

  const colHeaders = [
    { x: cols[0], label: "TELEMETRY" },
    { x: cols[1], label: "TRIAGE" },
    { x: cols[2], label: "VERDICT" },
    { x: cols[3], label: "HUMAN REVIEW" },
  ];

  return (
    <div style={{ padding: "8px 18px 4px", borderBottom: "1px solid var(--line)" }}>
      <div style={{ display: "flex", alignItems: "center", fontSize: 9.5, fontWeight: 700, letterSpacing: "0.06em", color: "var(--ink-4)", marginBottom: 2 }}>
        ADJUDICATION FUNNEL · RECENT WINDOW
        <PaneMoveButtons onMoveUp={onMoveUp} onMoveDown={onMoveDown} canMoveUp={canMoveUp} canMoveDown={canMoveDown} />
      </div>
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMinYMin meet" style={{ maxWidth: 900 }}>
        {colHeaders.map(c => (
          <text key={c.label} x={c.x} y={14} fontSize={8.5} fontWeight={700} letterSpacing="0.08em" fill="var(--ink-4)">{c.label}</text>
        ))}
        {bands(nodesA, nodesB).map((b, i) => <Band key={`ab-${i}`} {...b} />)}
        {bands(nodesB, nodesC).map((b, i) => <Band key={`bc-${i}`} {...b} />)}
        {bands(nodesC, nodesD).map((b, i) => <Band key={`cd-${i}`} {...b} />)}
        {nodesA.map(n => <Node key={n.key} n={n} />)}
        {nodesB.map(n => <Node key={n.key} n={n} />)}
        {nodesC.map(n => <Node key={n.key} n={n} />)}
        {nodesD.map(n => <Node key={n.key} n={n} />)}
      </svg>
    </div>
  );
}

// ── Risk-pulse timeline — scatter of adjudicated risk_score over time,
// colored by tier. Bursts/attack windows are invisible in a sortable table
// but jump out immediately here. Click a point to jump straight to that row
// in the Adjudications tab.
function RiskPulseTimeline({ adjudicated, onSelect, onMoveUp, onMoveDown, canMoveUp, canMoveDown }) {
  const pts = adjudicated
    .filter(r => r.adjudicated_at && r.risk_score != null)
    .map(r => ({ ...r, t: new Date(r.adjudicated_at).getTime() }))
    .sort((a, b) => a.t - b.t);

  if (pts.length < 2) return null;

  const W = 900, H = 90, PAD_L = 8, PAD_R = 8, PAD_T = 14, PAD_B = 8;
  const plotW = W - PAD_L - PAD_R, plotH = H - PAD_T - PAD_B;
  const tMin = pts[0].t, tMax = pts[pts.length - 1].t;
  const tSpan = Math.max(1, tMax - tMin);

  const x = t => PAD_L + ((t - tMin) / tSpan) * plotW;
  const y = score => PAD_T + (1 - Math.max(0, Math.min(1, score))) * plotH;

  return (
    <div style={{ padding: "8px 18px 10px", borderBottom: "1px solid var(--line)" }}>
      <div style={{ display: "flex", alignItems: "center", fontSize: 9.5, fontWeight: 700, letterSpacing: "0.06em", color: "var(--ink-4)", marginBottom: 2 }}>
        RISK PULSE · {pts.length} ADJUDICATIONS OVER TIME
        <PaneMoveButtons onMoveUp={onMoveUp} onMoveDown={onMoveDown} canMoveUp={canMoveUp} canMoveDown={canMoveDown} />
      </div>
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMinYMin meet" style={{ maxWidth: 900 }}>
        <line x1={PAD_L} x2={W - PAD_R} y1={y(0.85)} y2={y(0.85)} stroke="var(--red)" strokeOpacity={0.25} strokeDasharray="3 3" />
        <line x1={PAD_L} x2={W - PAD_R} y1={y(0.65)} y2={y(0.65)} stroke="var(--amber)" strokeOpacity={0.25} strokeDasharray="3 3" />
        {pts.map((p, i) => {
          const ts = _UBO_TIER_STYLE[p.risk_tier] || _UBO_TIER_STYLE.LOW;
          return (
            <circle key={p.id ?? i} cx={x(p.t)} cy={y(p.risk_score)} r={p.requires_human_review ? 3.4 : 2.4}
              fill={ts.ink} opacity={0.85} stroke={p.requires_human_review ? ts.ink : "none"} strokeWidth={p.requires_human_review ? 1.5 : 0}
              style={{ cursor: onSelect ? "pointer" : "default" }}
              onClick={() => onSelect && onSelect(p)}>
              <title>{`${p.target_tool || "?"} · ${p.risk_tier} · score=${Number(p.risk_score).toFixed(3)} · ${new Date(p.t).toLocaleString()}`}</title>
            </circle>
          );
        })}
      </svg>
    </div>
  );
}

// Extracted out of the Adjudications tab body (where it used to live buried
// below the tab switcher) so it can sit alongside AdjudicationFunnel/
// RiskPulseTimeline as a reorderable top-level pane, visible regardless of
// which tab is active — it's the one thing on this screen that actually
// needs a human to act on it.
function HumanReviewQueuePane({ humanReview, onMoveUp, onMoveDown, canMoveUp, canMoveDown, expanded, onExpandToggle, onReview }) {
  if (!humanReview.length) return null;
  return (
    <div style={{ borderBottom: "1px solid var(--line)" }}>
      <div className="bb-section-sep">
        <span style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--red-ink)" }}>
          ⚠ HUMAN REVIEW QUEUE
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {humanReview.length} REQUIRING ATTENTION
          <PaneMoveButtons onMoveUp={onMoveUp} onMoveDown={onMoveDown} canMoveUp={canMoveUp} canMoveDown={canMoveDown} />
        </span>
      </div>
      {/* Reuses UBOAdjRow (same component the Adjudications tab uses) rather
          than a stripped-down row, so escalation cause — adjudicator
          reasoning, per-agent council votes, conflict flags — is one click
          away here too, not only in a different tab. */}
      <div style={{ padding: "0 18px 10px" }}>
        {humanReview.slice(0, 5).map((r, i) => (
          <UBOAdjRow
            key={r.id ?? i}
            row={r}
            expanded={expanded.has(r.id ?? `hr-${i}`)}
            onToggle={() => onExpandToggle(r.id ?? `hr-${i}`)}
            onReview={onReview}
          />
        ))}
        {humanReview.length > 5 && (
          <div style={{ fontSize: 11, color: "var(--ink-3)", padding: "4px 0" }}>
            + {humanReview.length - 5} more — set filter to "Needs Review" to see all
          </div>
        )}
      </div>
    </div>
  );
}

// Funnel + Risk Pulse Timeline merged into one collapsible pane, closed by
// default — they're diagnostic/exploratory, not action items, and having
// both open by default was most of what pushed the adjudications table (the
// thing you're actually there to work with) out of the visible viewport.
function AnalyticsPane({ rawRows, adjudicated, onSelect, onMoveUp, onMoveDown, canMoveUp, canMoveDown }) {
  const [open, setOpen] = useState(false);
  if (!rawRows.length && !adjudicated.length) return null;

  const flagged = rawRows.filter(r => (r.risk_flags || []).length > 0).length;
  const escalated = adjudicated.filter(r => r.final_verdict === "ESCALATE").length;

  return (
    <div style={{ borderBottom: "1px solid var(--line)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 18px", cursor: "pointer" }}
        onClick={() => setOpen(o => !o)}>
        <svg width="9" height="9" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.5"
          strokeLinecap="round" strokeLinejoin="round"
          style={{ color: "var(--ink-4)", flexShrink: 0, transform: open ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform .1s" }}>
          <path d="M2 4l3 3 3-3" />
        </svg>
        <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: "0.06em", color: "var(--ink-4)" }}>
          ANALYTICS · FUNNEL &amp; RISK PULSE
        </span>
        <span style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
          {rawRows.length} telemetry · {flagged} flagged · {escalated} escalated{!open ? " — click to expand" : ""}
        </span>
        <PaneMoveButtons onMoveUp={onMoveUp} onMoveDown={onMoveDown} canMoveUp={canMoveUp} canMoveDown={canMoveDown} />
      </div>
      {open && (
        <>
          <AdjudicationFunnel rawRows={rawRows} adjudicated={adjudicated} />
          <RiskPulseTimeline adjudicated={adjudicated} onSelect={onSelect} />
        </>
      )}
    </div>
  );
}

const UBO_PANE_DEFAULT_ORDER = ["humanReview", "analytics"];

function UBOGovPanel({ initialTab, initialFilter } = {}) {
  const LiveBadge = window.LiveBadge;
  const [adjudicated,  setAdjudicated]  = useState([]);
  // Real total behind the adjudicated page (server-computed, ignores
  // limit/offset) — lets the Adjudications tab say "showing 100 of 3,204"
  // instead of implying the fetched page is the whole population.
  const [adjTotal,     setAdjTotal]     = useState(0);
  const [loadingMore,  setLoadingMore]  = useState(false);
  const [humanReview,  setHumanReview]  = useState([]);
  const [latency,      setLatency]      = useState([]);
  const [rawRows,      setRawRows]      = useState([]);
  const [holds,        setHolds]        = useState([]);
  const [coverage,     setCoverage]     = useState([]);
  const [suppressions, setSuppressions] = useState([]);
  const [loading,      setLoading]      = useState(true);
  const [triggering,   setTriggering]   = useState(false);
  const [filter,       setFilter]       = useState(initialFilter?.tier || initialFilter?.source || "all");
  // Verdict and domain are independent filter dimensions from the legacy
  // tier/source/review `filter` above (AND'd together, not OR'd) — added so
  // Continuous Monitoring's charts can deep-link into exactly the slice a
  // click represents (e.g. "Identity & Access Management" + "ESCALATE",
  // or source-system-dimensioned charts' "GITHUB" + "ESCALATE").
  const [verdictFilter, setVerdictFilter] = useState(initialFilter?.verdict || "all");
  const [domainFilter,  setDomainFilter]  = useState(initialFilter?.domain || "all");
  const [expanded,     setExpanded]     = useState(new Set());
  const [lastRefresh,  setLastRefresh]  = useState(null);
  const [fetchErr,       setFetchErr]       = useState(null);
  const [tab,            setTab]            = useState(initialTab || "adjudications");
  const [processStatus,  setProcessStatus]  = useState(null);
  const [isPaused,       setIsPaused]       = useState(false);
  const [newIds,         setNewIds]         = useState(new Set());
  const [paneOrder,      setPaneOrder]      = useState(UBO_PANE_DEFAULT_ORDER);

  const knownIdsRef       = useRef(new Set());
  const highlightTimerRef = useRef(null);
  const paneOrderHydratedRef = useRef(false);

  // Pane order is a per-user preference, stored the same way appearance
  // settings are (auth.users.preferences JSONB, merged not replaced — see
  // app.jsx's tweaks hydration for the identical pattern). Hydrate once the
  // session resolves; ignore a saved order that doesn't match today's set of
  // panes (e.g. a pane was added/removed later) rather than silently
  // dropping a pane from view.
  const auth = window.useAuth ? window.useAuth() : null;
  useEffect(() => {
    if (auth?.user === undefined) return;
    const saved = auth?.user?.preferences?.uboPaneOrder;
    if (Array.isArray(saved) && saved.length === UBO_PANE_DEFAULT_ORDER.length &&
        UBO_PANE_DEFAULT_ORDER.every(k => saved.includes(k))) {
      setPaneOrder(saved);
    }
    paneOrderHydratedRef.current = true;
  }, [auth?.user]); // eslint-disable-line react-hooks/exhaustive-deps

  function movePane(key, dir) {
    setPaneOrder(prev => {
      const idx = prev.indexOf(key);
      const swapIdx = idx + dir;
      if (idx === -1 || swapIdx < 0 || swapIdx >= prev.length) return prev;
      const next = [...prev];
      [next[idx], next[swapIdx]] = [next[swapIdx], next[idx]];
      if (paneOrderHydratedRef.current) {
        fetch("/auth/users/me/preferences", {
          method: "PUT", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ uboPaneOrder: next }),
        }).catch(() => {});
      }
      return next;
    });
  }

  async function refresh() {
    const base = _uboBase();
    try {
      const responses = await Promise.all([
        fetch(`${base}/observability/telemetry/adjudicated?limit=100`),
        fetch(`${base}/observability/telemetry/human-review`),
        fetch(`${base}/observability/telemetry/summary`),
        fetch(`${base}/observability/telemetry/raw?limit=200`),
        fetch(`${base}/observability/holds`),
        fetch(`${base}/observability/coverage`),
      ]);
      const [adjRes, hrRes, latRes, rawRes, holdsRes, covRes] = responses;
      // These 6 calls share one session cookie — if any came back 401 the
      // session is dead, not just that one endpoint. Signal it distinctly so
      // usePolling's counter can stop retrying and log out instead of
      // silently no-op'ing every 5 seconds forever (each fetch below is
      // otherwise treated as independently best-effort).
      if (responses.some(r => r.status === 401)) {
        const err = new Error("Session expired");
        err.status = 401;
        throw err;
      }
      if (adjRes.ok) {
        const d = await adjRes.json();
        const rows = d.rows || [];
        const freshIds = new Set(rows.filter(r => r.id != null && !knownIdsRef.current.has(r.id)).map(r => r.id));
        rows.forEach(r => r.id != null && knownIdsRef.current.add(r.id));
        setAdjudicated(rows);
        setAdjTotal(d.total ?? rows.length);
        if (freshIds.size > 0) {
          setNewIds(freshIds);
          clearTimeout(highlightTimerRef.current);
          highlightTimerRef.current = setTimeout(() => setNewIds(new Set()), 4000);
        }
      }
      if (hrRes.ok)    { const d = await hrRes.json();    setHumanReview(d.rows || []); }
      if (latRes.ok)   { const d = await latRes.json();   setLatency(d.rows || []); }
      if (rawRes.ok)   { const d = await rawRes.json();   setRawRows(d.rows || []); }
      if (holdsRes.ok) { const d = await holdsRes.json(); setHolds(d.rows || []); }
      if (covRes.ok)   { const d = await covRes.json();   setCoverage(d.rows || []); }
      setFetchErr(null);
      setLastRefresh(new Date());
    } catch (e) {
      setFetchErr(e.message);
      if (e && e.status === 401) throw e;
    } finally {
      setLoading(false);
    }
  }

  async function refreshSuppressions() {
    const base = _uboBase();
    try {
      const res = await fetch(`${base}/observability/suppressions`);
      if (res.ok) { const d = await res.json(); setSuppressions(d.rows || []); }
    } catch (_) {}
  }

  async function resolveHold(holdId, status) {
    const base = _uboBase();
    const res = await fetch(`${base}/observability/holds/${holdId}/resolve`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ..._cemAuthHdr() },
      body: JSON.stringify({ status }),
    });
    if (res.ok) setHolds(prev => prev.filter(h => h.id !== holdId));
    return res.ok;
  }

  async function addSuppression(data) {
    const base = _uboBase();
    const res = await fetch(`${base}/observability/suppressions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ..._cemAuthHdr() },
      body: JSON.stringify(data),
    });
    if (res.ok) await refreshSuppressions();
    return res.ok;
  }

  async function deleteSuppression(id) {
    const base = _uboBase();
    const res = await fetch(`${base}/observability/suppressions/${id}`, {
      method: "DELETE",
      headers: { ..._cemAuthHdr() },
    });
    if (res.ok) setSuppressions(prev => prev.filter(s => s.id !== id));
    return res.ok;
  }

  window.usePolling(refresh, 5_000, { paused: isPaused });

  async function triggerProcess() {
    setTriggering(true);
    setProcessStatus(null);
    try {
      const base = _uboBase();
      const res = await fetch(`${base}/observability/telemetry/process`, { method: "POST" });
      const data = res.ok ? await res.json() : { error: `HTTP ${res.status}` };
      setProcessStatus(data);
      await refresh();
    } catch (e) {
      setProcessStatus({ error: e.message });
    } finally {
      setTriggering(false);
    }
  }

  // Pages further into history, appending to the current list. Only makes
  // sense while live refresh is paused — the next 5s poll re-fetches just
  // the latest 100 and would otherwise wipe out anything paged in here, so
  // the "Load more" control (below) only renders while isPaused is true.
  async function loadMoreAdjudicated() {
    setLoadingMore(true);
    try {
      const base = _uboBase();
      const params = new URLSearchParams({ limit: "100", offset: String(adjudicated.length) });
      if (filter !== "all" && !["review", "GITHUB", "MCP_PROXY", "SYSTEM_TELEMETRY"].includes(filter)) {
        params.set("tier", filter);
      }
      const res = await fetch(`${base}/observability/telemetry/adjudicated?${params}`);
      if (res.ok) {
        const d = await res.json();
        setAdjudicated(prev => [...prev, ...(d.rows || [])]);
        setAdjTotal(d.total ?? adjTotal);
      }
    } finally {
      setLoadingMore(false);
    }
  }

  async function submitReview(rowId, humanVerdict, notes) {
    const base = _uboBase();
    const res = await fetch(`${base}/observability/telemetry/adjudicated/${rowId}/review`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ..._cemAuthHdr() },
      body: JSON.stringify({ human_verdict: humanVerdict, notes }),
    });
    if (res.ok) {
      setAdjudicated(prev => prev.map(r =>
        r.id === rowId
          ? { ...r, requires_human_review: false,
              adjudicator_reasoning: (r.adjudicator_reasoning || "") +
                `\n\n[HUMAN REVIEW] verdict=${humanVerdict} notes=${notes}` }
          : r
      ));
      setHumanReview(prev => prev.filter(r => r.id !== rowId));
    }
    return res.ok;
  }

  const counts = adjudicated.reduce(
    (acc, r) => {
      if (r.risk_tier === "CRITICAL") acc.critical++;
      else if (r.risk_tier === "HIGH") acc.high++;
      if (r.requires_human_review) acc.review++;
      acc.total++;
      return acc;
    },
    { critical: 0, high: 0, review: 0, total: 0 },
  );

  const filtered = adjudicated.filter(r => {
    if (verdictFilter !== "all" && (r.final_verdict || "INSUFFICIENT_DATA") !== verdictFilter) return false;
    if (domainFilter !== "all" && (r.domain || "Unclassified") !== domainFilter) return false;
    if (filter === "all")    return true;
    if (filter === "review") return r.requires_human_review;
    if (filter === "GITHUB" || filter === "MCP_PROXY" || filter === "SYSTEM_TELEMETRY") return (r.source_system || "MCP_PROXY") === filter;
    return r.risk_tier === filter;
  });

  return (
    <div data-screen-label="UBO Governance Brain" className="bb-panel" style={{height:"calc(100% + 40px)", overflow:"hidden"}}>
      <BBTermHeader
        section="DENDRAI UBO™ GOVERNANCE BRAIN"
        title="Telemetry Adjudication Pipeline · MCP + Any-Agent Events"
        status={`${adjTotal > counts.total ? `${counts.total} OF ${adjTotal} LOADED` : `${counts.total} ADJUDICATED`}  ·  ${counts.review} NEEDS HUMAN REVIEW  ·  BRONZE → SILVER → GOLD → COUNCIL`}
        actions={
          <div style={{display:"flex",gap:8,alignItems:"center"}}>
            <LiveBadge lastRefresh={lastRefresh} isPaused={isPaused}
              onToggle={() => setIsPaused(p => !p)} intervalLabel="5s"/>
            <button className="btn btn-sm" onClick={refresh} disabled={loading}>
              <Icon name="bolt" size={12}/> REFRESH
            </button>
            <button className="btn btn-sm btn-primary" onClick={triggerProcess} disabled={triggering}>
              {triggering ? <><span className="spin"/> Processing…</> : "▶ PROCESS QUEUE"}
            </button>
          </div>
        }
      />

      <div className="bb-stat-ticker">
        <div className="bb-ticker-item"><div className="bb-ticker-label">TOTAL ADJUDICATED</div><div className="bb-ticker-val">{counts.total}</div></div>
        <div className="bb-ticker-item"><div className="bb-ticker-label">CRITICAL</div><div className={`bb-ticker-val${counts.critical > 0 ? " red" : ""}`}>{counts.critical}</div></div>
        <div className="bb-ticker-item"><div className="bb-ticker-label">HIGH</div><div className={`bb-ticker-val${counts.high > 0 ? " amber" : ""}`}>{counts.high}</div></div>
        <div className="bb-ticker-item"><div className="bb-ticker-label">NEEDS REVIEW</div><div className={`bb-ticker-val${counts.review > 0 ? " orange" : ""}`}>{counts.review}</div></div>
        <div className="bb-ticker-item"><div className="bb-ticker-label">TOOLS MONITORED</div><div className="bb-ticker-val">{latency.length}</div></div>
        <div className="bb-ticker-item"><div className="bb-ticker-label">PENDING HOLDS</div><div className={`bb-ticker-val${holds.length > 0 ? " red" : ""}`}>{holds.length}</div></div>
      </div>

      {fetchErr && (
        <div style={{margin:"8px 18px",padding:"8px 12px",background:"var(--red-soft)",borderRadius:6,fontSize:11,color:"var(--red-ink)"}}>
          ⚠ API unavailable: {fetchErr} — ensure api_server.py is running
        </div>
      )}

      {paneOrder.map((key, i) => {
        const moveProps = {
          onMoveUp: () => movePane(key, -1), onMoveDown: () => movePane(key, 1),
          canMoveUp: i > 0, canMoveDown: i < paneOrder.length - 1,
        };
        if (key === "humanReview") return (
          <HumanReviewQueuePane key={key} humanReview={humanReview} expanded={expanded}
            onExpandToggle={(k) => {
              const next = new Set(expanded);
              next.has(k) ? next.delete(k) : next.add(k);
              setExpanded(next);
            }}
            onReview={submitReview}
            {...moveProps}
          />
        );
        if (key === "analytics") return (
          <AnalyticsPane key={key} rawRows={rawRows} adjudicated={adjudicated} onSelect={(row) => {
            setTab("adjudications");
            setFilter("all");
            setExpanded(prev => new Set(prev).add(row.id));
          }} {...moveProps} />
        );
        return null;
      })}

      {processStatus && (
        <div style={{
          margin:"4px 18px 0",padding:"6px 12px",borderRadius:6,fontSize:11,
          background: processStatus.error ? "var(--red-soft)" : processStatus.adjudicated > 0 ? "var(--green-soft,#e8f5e9)" : "var(--ink-bg-2,#f5f5f0)",
          color: processStatus.error ? "var(--red-ink)" : "var(--ink-1)",
          fontFamily:"'Geist Mono',monospace",display:"flex",alignItems:"center",gap:8,
        }}>
          {processStatus.error
            ? `⚠ Process queue error: ${processStatus.error}`
            : processStatus.adjudicated > 0
              ? `✓ Adjudicated ${processStatus.adjudicated} row${processStatus.adjudicated !== 1 ? "s" : ""}`
              : `— Queue empty: no flagged MCP calls pending${processStatus.ubo_available === false ? " · UBO™ pipeline unavailable" : ""}`
          }
          <button style={{marginLeft:"auto",fontSize:10,cursor:"pointer",background:"none",border:"none",color:"inherit",opacity:0.6}} onClick={() => setProcessStatus(null)}>✕</button>
        </div>
      )}

      {/* ── Tab switcher ─────────────────────────────────────────────────── */}
      <div style={{padding:"0 18px"}}>
        <div className="cem-toolbar">
          <button className={"cem-filter" + (tab === "adjudications" ? " active" : "")} onClick={() => setTab("adjudications")}>Adjudications</button>
          <button className={"cem-filter" + (tab === "council" ? " active" : "")} onClick={() => setTab("council")}>Council Activity</button>
          <button className={"cem-filter" + (tab === "stream" ? " active" : "")} onClick={() => setTab("stream")}>
            Raw Feed {rawRows.length > 0 && <span style={{marginLeft:4,fontSize:9,opacity:.7}}>{rawRows.length}</span>}
          </button>
          <button className={"cem-filter" + (tab === "holds" ? " active" : "")} onClick={() => setTab("holds")}
            style={holds.length > 0 ? {color:"var(--red-ink)",fontWeight:700} : {}}>
            Holds {holds.length > 0 && <span style={{marginLeft:4,fontSize:9,background:"var(--red-soft)",color:"var(--red-ink)",padding:"1px 5px",borderRadius:8,fontWeight:700}}>{holds.length}</span>}
          </button>
          <button className={"cem-filter" + (tab === "coverage" ? " active" : "")} onClick={() => setTab("coverage")}>Coverage</button>
          <button className={"cem-filter" + (tab === "timeline" ? " active" : "")} onClick={() => setTab("timeline")}>Timeline</button>
          <button className={"cem-filter" + (tab === "suppressions" ? " active" : "")} onClick={() => { setTab("suppressions"); refreshSuppressions(); }}>Suppressions</button>
        </div>
        {/* Filter row lives outside .cem-event-list (stays visible while the
            table scrolls, instead of scrolling away with it) — was previously
            nested inside the scrollable area, so it disappeared the moment
            you scrolled the adjudications table at all. */}
        {tab === "adjudications" && (
          <div className="cem-toolbar">
            {[
              { id:"all",      l:"All" },
              { id:"CRITICAL", l:"Critical" },
              { id:"HIGH",     l:"High" },
              { id:"MEDIUM",   l:"Medium" },
              { id:"LOW",      l:"Low" },
              { id:"review",   l:"Needs Review" },
              { id:"GITHUB",   l:"GitHub" },
              { id:"MCP_PROXY",l:"MCP" },
              { id:"SYSTEM_TELEMETRY", l:"Systems" },
            ].map(f => (
              <button key={f.id} className={"cem-filter" + (filter === f.id ? " active" : "")} onClick={() => setFilter(f.id)}>
                {f.l}{f.id === "review" && counts.review > 0 ? ` (${counts.review})` : ""}
              </button>
            ))}
          </div>
        )}
        {tab === "adjudications" && (verdictFilter !== "all" || domainFilter !== "all") && (
          <div className="cem-toolbar" style={{alignItems:"center",gap:8}}>
            <span style={{fontSize:10,color:"var(--ink-3)",textTransform:"uppercase",letterSpacing:"0.05em"}}>Deep-link filter:</span>
            {verdictFilter !== "all" && (
              <button className="cem-filter active" onClick={() => setVerdictFilter("all")}>
                {verdictFilter} ✕
              </button>
            )}
            {domainFilter !== "all" && (
              <button className="cem-filter active" onClick={() => setDomainFilter("all")}>
                {domainFilter} ✕
              </button>
            )}
          </div>
        )}
      </div>

      <div className="cem-event-list">
        {tab === "adjudications" && (<>
          <div className="bb-section-sep">
            <span>ADJUDICATION LOG</span>
            <span>
              {filtered.length !== adjudicated.length ? `${filtered.length} MATCH FILTER · ` : ""}
              {adjTotal > adjudicated.length ? `${adjudicated.length} OF ${adjTotal} LOADED` : `${adjudicated.length} EVENTS SHOWN`}
            </span>
          </div>

          {loading ? (
            <div style={{padding:"32px 18px",textAlign:"center",color:"var(--ink-3)",fontSize:12}}>
              <span className="spin"/> Loading UBO™ governance data…
            </div>
          ) : filtered.length === 0 ? (
            <Empty>
              {counts.total === 0
                ? "No adjudications yet. Click \"▶ PROCESS QUEUE\" to run the UBO™ pipeline against flagged MCP telemetry, or wait for the 30-second polling cycle."
                : "No events match this filter."}
            </Empty>
          ) : (
            <div style={{padding:"0 18px 18px"}}>
              {filtered.map((r, i) => (
                <UBOAdjRow
                  key={r.id ?? i}
                  row={r}
                  isNew={r.id != null && newIds.has(r.id)}
                  expanded={expanded.has(r.id ?? i)}
                  onToggle={() => {
                    const key = r.id ?? i;
                    const next = new Set(expanded);
                    next.has(key) ? next.delete(key) : next.add(key);
                    setExpanded(next);
                  }}
                  onReview={submitReview}
                />
              ))}
              {adjTotal > adjudicated.length && (
                isPaused ? (
                  <button className="btn btn-sm" style={{ marginTop: 8 }} onClick={loadMoreAdjudicated} disabled={loadingMore}>
                    {loadingMore ? <><span className="spin" /> Loading…</> : `Load more (${adjTotal - adjudicated.length} remaining)`}
                  </button>
                ) : (
                  <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 8 }}>
                    {adjTotal - adjudicated.length} more not shown — pause live refresh to page through them.
                  </div>
                )
              )}
            </div>
          )}

          {latency.length > 0 && (
            <>
              <div className="bb-section-sep">
                <span>MCP TOOL LATENCY SUMMARY</span>
                <span>{latency.length} TOOLS</span>
              </div>
              <div style={{padding:"0 18px 18px",overflowX:"auto"}}>
                <table className="ubo-lat-table">
                  <thead>
                    <tr>
                      <th>Server</th><th>Tool</th><th>Calls</th>
                      <th>Avg ms</th><th>P50</th><th>P95</th><th>P99</th>
                      <th>Errors</th><th>Err %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {latency.map((r, i) => (
                      <tr key={i}>
                        <td className="mono">{r.server_name || "—"}</td>
                        <td className="mono">{r.target_tool || "—"}</td>
                        <td>{r.call_count}</td>
                        <td>{r.avg_ms != null ? Math.round(r.avg_ms) : "—"}</td>
                        <td>{r.p50_ms != null ? Math.round(r.p50_ms) : "—"}</td>
                        <td className={r.p95_ms > 30000 ? "ubo-lat-breach" : ""}>{r.p95_ms != null ? Math.round(r.p95_ms) : "—"}</td>
                        <td className={r.p99_ms > 30000 ? "ubo-lat-breach" : ""}>{r.p99_ms != null ? Math.round(r.p99_ms) : "—"}</td>
                        <td className={r.error_count > 0 ? "ubo-lat-err" : ""}>{r.error_count ?? "—"}</td>
                        <td className={r.error_pct > 0 ? "ubo-lat-warn" : ""}>{r.error_pct != null ? `${r.error_pct.toFixed(1)}%` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>)}

        {tab === "council" && (
          <UBOCouncilTab adjudicated={adjudicated} loading={loading} />
        )}

        {tab === "stream" && (
          <RawFeedTab rows={rawRows} adjudicated={adjudicated} loading={loading} isPaused={isPaused} />
        )}

        {tab === "holds" && (
          <HoldsTab holds={holds} onResolve={resolveHold} />
        )}

        {tab === "coverage" && (
          <CoverageTab coverage={coverage} loading={loading} onSuppress={addSuppression} />
        )}

        {tab === "timeline" && (
          <TimelineTab adjudicated={adjudicated} loading={loading} />
        )}

        {tab === "suppressions" && (
          <SuppressionsTab suppressions={suppressions} onAdd={addSuppression} onDelete={deleteSuppression} />
        )}
      </div>
    </div>
  );
}

// Builds a self-contained, portable record of "why was this escalated" — the
// full reasoning trail (verdict, policy violations, adjudicator synthesis,
// per-agent council votes) as one JSON file, so an AI-governance leader can
// attach a specific decision to an incident ticket or an oversight-committee
// packet without screenshotting the UI.
function _exportAdjudicationRecord(row) {
  const record = {
    exported_at: new Date().toISOString(),
    adjudication_id: row.id,
    adjudicated_at: row.adjudicated_at,
    tool: row.target_tool,
    server: row.server_name,
    source_system: row.source_system,
    session_id: row.session_id,
    risk_tier: row.risk_tier,
    risk_score: row.risk_score,
    final_verdict: row.final_verdict,
    ensemble_confidence: row.ensemble_confidence,
    requires_human_review: row.requires_human_review,
    risk_flags: row.risk_flags || [],
    conflict_flags: row.conflict_flags || [],
    policy_violations: row.policy_violations || [],
    adjudicator_reasoning: row.adjudicator_reasoning || null,
    council_votes: row.council_votes || [],
  };
  const blob = new Blob([JSON.stringify(record, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `dendrai-adjudication-${row.id ?? "record"}-${(row.adjudicated_at || new Date().toISOString()).slice(0, 10)}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function UBOAdjRow({ row, expanded, onToggle, isNew, onReview }) {
  const tier    = row.risk_tier     || "LOW";
  const verdict = row.final_verdict || "CLEAR";
  const ts  = _UBO_TIER_STYLE[tier]       || _UBO_TIER_STYLE.LOW;
  const vs  = _UBO_VERDICT_STYLE[verdict] || _UBO_VERDICT_STYLE.CLEAR;
  const violations = row.policy_violations || [];
  const flags      = row.risk_flags        || [];
  const conflicts  = row.conflict_flags    || [];

  // Inline review state
  const [reviewing,     setReviewing]     = useState(false);
  const [reviewVerdict, setReviewVerdict] = useState("APPROVE");
  const [reviewNotes,   setReviewNotes]   = useState("");
  const [reviewBusy,    setReviewBusy]    = useState(false);
  const [reviewDone,    setReviewDone]    = useState(false);

  async function handleReviewSubmit(e) {
    e.preventDefault();
    if (!onReview || !row.id) return;
    setReviewBusy(true);
    const ok = await onReview(row.id, reviewVerdict, reviewNotes);
    setReviewBusy(false);
    if (ok) { setReviewDone(true); setReviewing(false); }
  }

  return (
    <div className={`ubo-adj-row${row.requires_human_review && !reviewDone ? " needs-review" : ""}${isNew ? " ubo-adj-new" : ""}`}>
      <Clickable className="ubo-adj-head" onClick={onToggle}>
        <span className="ubo-tier-badge"    style={{background:ts.bg, color:ts.ink}}>{tier}</span>
        <span className="ubo-verdict-badge" style={{background:vs.bg, color:vs.ink}}>{verdict}</span>
        {(row.source_system || "MCP_PROXY") === "GITHUB" && (
          <span style={{fontSize:9,fontWeight:700,padding:"2px 6px",borderRadius:4,background:"#1a1a2e",color:"#58a6ff",fontFamily:"Geist Mono,monospace",flexShrink:0,letterSpacing:".06em"}}>GH</span>
        )}
        {isNew && (
          <span style={{fontSize:9,fontWeight:700,padding:"2px 6px",borderRadius:4,background:"var(--green-soft,#e8f5e9)",color:"var(--green-ink,#166534)",fontFamily:"Geist Mono,monospace",flexShrink:0,letterSpacing:".06em"}}>NEW</span>
        )}
        <span className="mono ubo-tool-name">{row.target_tool || "unknown"}</span>
        <span style={{fontSize:10,color:"var(--ink-3)",flexShrink:0}}>{row.server_name}</span>
        {row.requires_human_review && !reviewDone && <span className="ubo-review-flag">⚠ REVIEW</span>}
        {reviewDone && <span style={{fontSize:9,fontWeight:700,padding:"2px 6px",borderRadius:4,background:"var(--green-soft)",color:"var(--green-ink)",fontFamily:"Geist Mono,monospace",flexShrink:0}}>✓ REVIEWED</span>}
        <span className="mono" style={{fontSize:11,fontWeight:600,flexShrink:0}}>
          {row.risk_score != null ? row.risk_score.toFixed(3) : "—"}
        </span>
        <span className="mono ubo-ts">
          {row.adjudicated_at ? new Date(row.adjudicated_at).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit"}) : ""}
        </span>
        <Icon name={expanded ? "chev-u" : "chev-d"} size={13} className="muted"/>
      </Clickable>
      {expanded && (
        <div className="ubo-adj-body">
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:6}}>
            <ProvenanceChip verdict={row.final_verdict}
              confidence={row.ensemble_confidence != null ? `${(row.ensemble_confidence * 100).toFixed(0)}%` : null}
              reviewedByName={reviewDone ? "you (this session)" : null} />
            <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); _exportAdjudicationRecord(row); }}
              title="Download the full reasoning trail for this decision as a JSON record">
              ⬇ Export audit record
            </button>
          </div>
          <div className="cem-meta">
            <CEMMeta l="Session ID"    v={row.session_id ? row.session_id.slice(0, 8) + "…" : "—"} />
            <CEMMeta l="Confidence"    v={row.ensemble_confidence != null ? `${(row.ensemble_confidence * 100).toFixed(0)}%` : "—"} />
            <CEMMeta l="Risk Flags"    v={flags.length > 0 ? flags.join(", ") : "none"} />
            <CEMMeta l="Conflict Flags" v={conflicts.length > 0 ? conflicts.join(", ") : "none"} />
          </div>
          {violations.length > 0 && (
            <>
              <div className="cem-section-lbl">Policy violations ({violations.length})</div>
              <div className="ubo-violations">
                {violations.map((v, i) => <div key={i} className="ubo-violation-item">{v}</div>)}
              </div>
            </>
          )}
          {row.adjudicator_reasoning && (
            <>
              <div className="cem-section-lbl">Adjudicator reasoning</div>
              <div className="rca-box">{row.adjudicator_reasoning}</div>
            </>
          )}
          {(row.council_votes || []).length > 0 && (
            <>
              <div className="cem-section-lbl">Council votes ({row.council_votes.length})</div>
              <div style={{display:"flex",flexDirection:"column",gap:6,marginBottom:4}}>
                {row.council_votes.map((vote, vi) => {
                  const vvs = _UBO_VERDICT_STYLE[vote.verdict] || _UBO_VERDICT_STYLE.CLEAR;
                  return (
                    <div key={vi} style={{
                      display:"flex", alignItems:"baseline", gap:8, fontSize:10.5,
                      padding:"5px 8px", borderRadius:4, background:"var(--surface-1)", border:"1px solid var(--line)",
                    }}>
                      <span style={{fontWeight:700,fontFamily:"'Geist Mono',monospace",minWidth:120}}>{vote.agent_name}</span>
                      <span className="ubo-verdict-badge" style={{background:vvs.bg,color:vvs.ink,fontSize:9,padding:"1px 5px",flexShrink:0}}>{vote.verdict}</span>
                      <span style={{color:"var(--ink-3)",flexShrink:0}}>{(vote.confidence*100).toFixed(0)}% conf</span>
                      <span style={{color:"var(--ink-2)",lineHeight:1.4}}>{vote.reasoning}</span>
                    </div>
                  );
                })}
              </div>
            </>
          )}

          {/* ── Human review panel ───────────────────────────────── */}
          {row.requires_human_review && !reviewDone && row.id != null && (
            <div style={{marginTop:10,padding:"10px 12px",borderRadius:6,border:"1.5px solid var(--amber-ink,#b45309)",background:"var(--amber-soft,#fff8e1)"}}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8}}>
                <span style={{fontSize:11,fontWeight:700,color:"var(--amber-ink,#b45309)",fontFamily:"'Geist Mono',monospace",letterSpacing:".04em"}}>
                  ⚠ HUMAN REVIEW REQUIRED
                </span>
                <button className="btn btn-sm" onClick={() => setReviewing(r => !r)}>
                  {reviewing ? "Cancel" : "Open Review"}
                </button>
              </div>
              {reviewing && (
                <form onSubmit={handleReviewSubmit} style={{display:"flex",flexDirection:"column",gap:8}}>
                  <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                    {["APPROVE","ESCALATE","CLEAR","MONITOR"].map(v => (
                      <label key={v} style={{display:"flex",alignItems:"center",gap:4,fontSize:11,cursor:"pointer",
                        padding:"3px 10px",borderRadius:4,border:`1.5px solid ${reviewVerdict===v?"var(--acc)":"var(--line)"}`,
                        background:reviewVerdict===v?"var(--acc-soft,#eff6ff)":"var(--surface-1)",
                        color:reviewVerdict===v?"var(--acc)":"var(--ink-2)",fontWeight:reviewVerdict===v?700:400}}>
                        <input type="radio" name="verdict" value={v} checked={reviewVerdict===v}
                          onChange={() => setReviewVerdict(v)} style={{display:"none"}}/>
                        {v === "APPROVE" ? "✓ Approve AI verdict"
                          : v === "ESCALATE" ? "↑ Escalate"
                          : v === "CLEAR"    ? "○ Override → CLEAR"
                          :                   "~ Override → MONITOR"}
                      </label>
                    ))}
                  </div>
                  <textarea
                    placeholder="Review notes (optional)…"
                    value={reviewNotes}
                    onChange={e => setReviewNotes(e.target.value)}
                    rows={2}
                    style={{fontSize:11,padding:"6px 8px",borderRadius:4,border:"1px solid var(--line)",
                      fontFamily:"'Geist Mono',monospace",resize:"vertical",background:"var(--surface-1)",color:"var(--ink-1)"}}
                  />
                  <div style={{display:"flex",gap:6}}>
                    <button type="submit" className="btn btn-sm btn-primary" disabled={reviewBusy}>
                      {reviewBusy ? <span><span className="spin"/> Submitting…</span> : "Submit Review"}
                    </button>
                    <button type="button" className="btn btn-sm" onClick={() => setReviewing(false)}>Cancel</button>
                  </div>
                </form>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Council Activity tab ──────────────────────────────────────────────────────

const _AGENT_STYLE = {
  "The Quant":           { accent: "var(--blue-ink)",   bg: "var(--blue-soft)"   },
  "The Linguist":        { accent: "var(--amber-ink)",  bg: "var(--amber-soft)"  },
  "The Graph Architect": { accent: "var(--green-ink)",  bg: "var(--green-soft)"  },
  // LLM-based 4th opinion — only present when the heuristic Council already
  // flagged conflict/low-confidence and requires_human_review is true.
  "The Reviewer (AI)":   { accent: "var(--violet-ink)", bg: "var(--violet-soft)" },
};

function UBOCouncilRow({ row, expanded, onToggle }) {
  const tier    = row.risk_tier     || "LOW";
  const verdict = row.final_verdict || "CLEAR";
  const ts  = _UBO_TIER_STYLE[tier]       || _UBO_TIER_STYLE.LOW;
  const vs  = _UBO_VERDICT_STYLE[verdict] || _UBO_VERDICT_STYLE.CLEAR;
  const votes = row.council_votes || [];

  return (
    <div className="ubo-adj-row">
      <Clickable className="ubo-adj-head" onClick={onToggle}>
        <span className="ubo-tier-badge"    style={{background:ts.bg, color:ts.ink}}>{tier}</span>
        <span className="ubo-verdict-badge" style={{background:vs.bg, color:vs.ink}}>{verdict}</span>
        <span className="mono ubo-tool-name">{row.target_tool || "unknown"}</span>
        <span style={{fontSize:10,color:"var(--ink-3)",flexShrink:0}}>{row.server_name}</span>
        <span className="mono" style={{fontSize:11,fontWeight:600,flexShrink:0}}>
          {row.risk_score != null ? row.risk_score.toFixed(3) : "—"}
        </span>
        <span style={{fontSize:10,color:"var(--ink-3)",flexShrink:0}}>
          {votes.length} agent{votes.length !== 1 ? "s" : ""}
        </span>
        <span className="mono ubo-ts">
          {row.adjudicated_at ? new Date(row.adjudicated_at).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit"}) : ""}
        </span>
        <Icon name={expanded ? "chev-u" : "chev-d"} size={13} className="muted"/>
      </Clickable>
      {expanded && (
        <div className="ubo-adj-body">
          <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:10,marginBottom:12}}>
            {votes.map((vote, vi) => {
              const ag  = _AGENT_STYLE[vote.agent_name] || { accent:"var(--ink-2)", bg:"var(--surface-2)" };
              const vvs = _UBO_VERDICT_STYLE[vote.verdict] || _UBO_VERDICT_STYLE.CLEAR;
              return (
                <div key={vi} style={{
                  background:"var(--surface-1)",
                  border:"1px solid var(--line)",
                  borderTop:`3px solid ${ag.accent}`,
                  borderRadius:6,
                  padding:"10px 12px",
                }}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:6}}>
                    <span style={{fontSize:11,fontWeight:700,color:ag.accent,fontFamily:"'Geist Mono',monospace"}}>{vote.agent_name}</span>
                    <span className="ubo-verdict-badge" style={{background:vvs.bg,color:vvs.ink,fontSize:9,padding:"2px 5px"}}>{vote.verdict}</span>
                  </div>
                  <div style={{display:"flex",gap:12,marginBottom:8,fontSize:10,color:"var(--ink-3)"}}>
                    <span>Conf: <span style={{color:"var(--ink-1)",fontWeight:600}}>{(vote.confidence*100).toFixed(0)}%</span></span>
                    <span>Δ: <span style={{color:vote.risk_delta>0?"var(--red-ink)":vote.risk_delta<0?"var(--green-ink)":"var(--ink-2)",fontWeight:600}}>{vote.risk_delta>=0?"+":""}{vote.risk_delta.toFixed(3)}</span></span>
                    <span style={{marginLeft:"auto"}}>{vote.evaluation_ms}ms</span>
                  </div>
                  <div style={{fontSize:10,color:"var(--ink-2)",lineHeight:1.5,maxHeight:72,overflow:"hidden"}}>
                    {vote.reasoning}
                  </div>
                </div>
              );
            })}
          </div>
          {row.adjudicator_reasoning && (<>
            <div className="cem-section-lbl">Adjudicator synthesis</div>
            <div className="rca-box">{row.adjudicator_reasoning}</div>
          </>)}
          {row.conflict_flags && row.conflict_flags.length > 0 && (
            <div style={{marginTop:6,fontSize:10,color:"var(--amber-ink)"}}>
              ⚡ Conflicts: {row.conflict_flags.join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function UBOCouncilTab({ adjudicated, loading }) {
  const [expanded, setExpanded] = useState(new Set());
  const withVotes = adjudicated.filter(r => (r.council_votes || []).length > 0);

  if (loading) return (
    <div style={{padding:"32px 18px",textAlign:"center",color:"var(--ink-3)",fontSize:12}}>
      <span className="spin"/> Loading council data…
    </div>
  );
  if (withVotes.length === 0) return (
    <Empty>No council deliberation records yet. The full agent swarm — The Quant, The Linguist, and The Graph Architect — runs only for HIGH and CRITICAL tier events. Process the queue to generate records.</Empty>
  );

  return (<>
    <div className="bb-section-sep">
      <span>COUNCIL DELIBERATIONS</span>
      <span>{withVotes.length} RECORDS</span>
    </div>
    <div style={{padding:"0 18px 18px"}}>
      {withVotes.map((row, i) => (
        <UBOCouncilRow
          key={i}
          row={row}
          expanded={expanded.has(i)}
          onToggle={() => {
            const next = new Set(expanded);
            next.has(i) ? next.delete(i) : next.add(i);
            setExpanded(next);
          }}
        />
      ))}
    </div>
  </>);
}

// ── Raw telemetry live-feed tab — Bronze → Silver → Gold medallion flow ────────

const _FLAG_COLOR = {
  bypass_keyword:      { bg:"var(--red-soft)",    ink:"var(--red-ink)"   },
  sensitive_tool:      { bg:"var(--amber-soft)",  ink:"var(--amber-ink)" },
  bulk_args:           { bg:"var(--blue-soft)",   ink:"var(--blue-ink)"  },
  large_payload:       { bg:"var(--surface-2)",   ink:"var(--ink-2)"     },
  HIGH_LATENCY:        { bg:"var(--amber-soft)",  ink:"var(--amber-ink)" },
  ELEVATED_RISK:       { bg:"var(--red-soft)",    ink:"var(--red-ink)"   },
  POLICY_VIOLATION:    { bg:"var(--red-soft)",    ink:"var(--red-ink)"   },
  // New detection rules
  prompt_injection:    { bg:"var(--red-soft)",    ink:"var(--red-ink)"   },
  sensitive_data:      { bg:"var(--red-soft)",    ink:"var(--red-ink)"   },
  large_response:      { bg:"var(--amber-soft)",  ink:"var(--amber-ink)" },
  high_frequency:      { bg:"var(--amber-soft)",  ink:"var(--amber-ink)" },
  escalation_sequence: { bg:"var(--red-soft)",    ink:"var(--red-ink)"   },
  // Generic system_telemetry detection rules (any monitored enterprise system)
  privileged_access:   { bg:"var(--amber-soft)",  ink:"var(--amber-ink)" },
  sod_violation:       { bg:"var(--red-soft)",    ink:"var(--red-ink)"   },
  sensitive_resource:  { bg:"var(--red-soft)",    ink:"var(--red-ink)"   },
  policy_violation:    { bg:"var(--red-soft)",    ink:"var(--red-ink)"   },
};

const _BRONZE_HDR = { bg:"rgba(180,110,40,0.12)",  border:"rgba(180,110,40,0.28)",  lbl:"var(--amber-ink,#b45309)" };
const _SILVER_HDR = { bg:"rgba(100,116,139,0.10)", border:"rgba(100,116,139,0.25)", lbl:"var(--ink-2)"             };
const _GOLD_HDR   = { bg:"rgba(155,130,10,0.12)",  border:"rgba(155,130,10,0.28)",  lbl:"var(--amber-ink,#b45309)" };

const _GENERIC_SYSTEM_FLAGS = ["sod_violation", "privileged_access", "sensitive_resource", "policy_violation"];

function _flagToEventType(flags, status, origin) {
  if (origin === "system" || flags?.some(f => _GENERIC_SYSTEM_FLAGS.includes(f))) {
    if (!flags?.length) return "ANOMALY";
    if (flags.length >= 2)                      return "SYSTEM_GOVERNANCE_VIOLATION";
    if (flags.includes("sod_violation"))        return "SOD_VIOLATION";
    if (flags.includes("privileged_access"))    return "PRIVILEGE_ESCALATION";
    if (flags.includes("sensitive_resource"))   return "SENSITIVE_RESOURCE_ACCESS";
    if (flags.includes("policy_violation"))     return "POLICY_VIOLATION";
    return "ANOMALY";
  }
  if (status === "error") return "MCP_TOOL_ERROR";
  if (!flags?.length)     return "ANOMALY";
  if (flags.length >= 3)                            return "MCP_GOVERNANCE_VIOLATION";
  if (flags.includes("prompt_injection"))        return "MCP_PROMPT_INJECTION";
  if (flags.includes("escalation_sequence"))     return "MCP_ESCALATION";
  if (flags.includes("sensitive_data"))          return "MCP_DATA_EXPOSURE";
  if (flags.includes("bypass_keyword"))          return "MCP_TOOL_BYPASS";
  if (flags.includes("sensitive_tool"))          return "MCP_SENSITIVE_TOOL_CALL";
  if (flags.includes("high_frequency"))          return "MCP_HIGH_FREQUENCY";
  if (flags.includes("bulk_args"))               return "MCP_BULK_ARGS";
  if (flags.includes("large_response"))          return "MCP_LARGE_RESPONSE";
  if (flags.includes("large_payload"))           return "MCP_LARGE_PAYLOAD";
  return "ANOMALY";
}

const _MCP_BASE_WEIGHT = {
  MCP_GOVERNANCE_VIOLATION: 0.90,
  MCP_PROMPT_INJECTION:     0.92,
  MCP_ESCALATION:           0.88,
  SYSTEM_GOVERNANCE_VIOLATION: 0.85,
  SOD_VIOLATION:              0.80,
  PRIVILEGE_ESCALATION:       0.80,
  SENSITIVE_RESOURCE_ACCESS:  0.65,
  POLICY_VIOLATION:           0.60,
  MCP_DATA_EXPOSURE:        0.80,
  MCP_TOOL_BYPASS:          0.85,
  MCP_SENSITIVE_TOOL_CALL:  0.70,
  MCP_HIGH_FREQUENCY:       0.55,
  MCP_BULK_ARGS:            0.45,
  MCP_TOOL_ERROR:           0.40,
  MCP_LARGE_RESPONSE:       0.38,
  MCP_LARGE_PAYLOAD:        0.35,
  ANOMALY:                  0.35,
};

function _violSeverityWeight(v) {
  if (/CRITICAL/i.test(v)) return 0.20;
  if (/HIGH/i.test(v))     return 0.12;
  if (/MEDIUM/i.test(v))   return 0.06;
  return 0.02;
}

function MedallionPaneHdr({ tier, subtitle, color }) {
  return (
    <div style={{ padding:"8px 12px", background:color.bg, borderBottom:`1px solid ${color.border}`,
      display:"flex", alignItems:"baseline", gap:6, flexShrink:0 }}>
      <span style={{ fontSize:10, fontWeight:700, fontFamily:"'Geist Mono',monospace",
        letterSpacing:".08em", color:color.lbl }}>{tier}</span>
      <span style={{ fontSize:9, color:"var(--ink-4)", fontFamily:"'Geist Mono',monospace" }}>{subtitle}</span>
    </div>
  );
}

function PaneSection({ label, children }) {
  return (
    <div style={{ marginBottom:10 }}>
      <div style={{ fontSize:9, fontWeight:700, color:"var(--ink-4)", letterSpacing:".07em",
        fontFamily:"'Geist Mono',monospace", marginBottom:4 }}>{label}</div>
      {children}
    </div>
  );
}

function PaneKV({ k, v, vColor }) {
  return (
    <div style={{ display:"flex", justifyContent:"space-between", alignItems:"flex-start", gap:8,
      fontSize:10, padding:"2px 0", borderBottom:"1px solid var(--line)" }}>
      <span style={{ color:"var(--ink-3)", flexShrink:0 }}>{k}</span>
      <span style={{ color:vColor||"var(--ink)", fontFamily:"'Geist Mono',monospace",
        textAlign:"right", wordBreak:"break-all", maxWidth:"62%" }}>{v}</span>
    </div>
  );
}

function BronzeDetail({ row }) {
  if (!row) return (
    <div style={{ padding:16, color:"var(--ink-4)", fontSize:11, textAlign:"center", paddingTop:32 }}>
      Select an event from the list
    </div>
  );
  const flags    = row.risk_flags || [];
  const isSystem = row.origin === "system";
  const evType   = _flagToEventType(flags, row.status, row.origin);
  return (
    <div style={{ padding:"10px 12px", overflowY:"auto", flex:1 }}>
      <PaneSection label="Ingestion Handler">
        <PaneKV k="Handler"    v={isSystem ? "SystemTelemetryBronzeHandler" : "McpProxyBronzeHandler"} />
        <PaneKV k="Schema"     v={isSystem ? "System-Telemetry-v1" : "MCP-Telemetry-v1"} />
        <PaneKV k="Source"     v={isSystem ? (row.system_type || "SYSTEM_TELEMETRY").toUpperCase() : "MCP_PROXY"} />
        <PaneKV k="Actor ID"   v={isSystem ? (row.actor || "—") : (row.session_id ? row.session_id.slice(0,8)+"…" : "—")} />
        <PaneKV k="Actor Type" v={isSystem ? "HUMAN" : "SERVICE"} />
        <PaneKV k="Event Type" v={evType} />
        <PaneKV k="Checksum"   v="✓ SHA-256" vColor="var(--green-ink)" />
        <PaneKV k="Stage Out"  v="BRONZE" vColor="var(--amber-ink,#b45309)" />
      </PaneSection>
      <PaneSection label="Raw Fields">
        <PaneKV k="Tool"      v={row.target_tool || row.method || "—"} />
        <PaneKV k="Server"    v={row.server_name || "—"} />
        <PaneKV k="Direction" v={(row.direction||"—").toUpperCase()} />
        <PaneKV k="Status"    v={row.status || "—"} />
        {row.execution_time_ms != null && (
          <PaneKV k="Latency" v={`${row.execution_time_ms}ms`}
            vColor={row.execution_time_ms > 30000 ? "var(--red-ink)" : undefined} />
        )}
        {row.error_message && (
          <PaneKV k="Error" v={String(row.error_message).slice(0,80)} vColor="var(--red-ink)" />
        )}
      </PaneSection>
      <PaneSection label={flags.length > 0 ? `Risk Flags (${flags.length})` : "Risk Flags"}>
        {flags.length === 0 ? (
          <div style={{ fontSize:10, color:"var(--ink-4)" }}>None — event will not enter governance pipeline</div>
        ) : (
          <div style={{ display:"flex", flexWrap:"wrap", gap:4, paddingTop:2 }}>
            {flags.map((f, i) => {
              const fc = _FLAG_COLOR[f] || { bg:"var(--surface-2)", ink:"var(--ink-2)" };
              return (
                <span key={i} style={{ padding:"2px 6px", borderRadius:4, background:fc.bg, color:fc.ink,
                  fontSize:9, fontWeight:700, fontFamily:"'Geist Mono',monospace" }}>{f}</span>
              );
            })}
          </div>
        )}
      </PaneSection>
    </div>
  );
}

function SilverDetail({ row, adj }) {
  if (!row) return null;
  if (!adj) return (
    <div style={{ padding:"10px 12px", overflowY:"auto", flex:1 }}>
      <div style={{ fontSize:10, color:"var(--ink-3)", marginBottom:6 }}>Not yet processed through UBO™ pipeline.</div>
      <div style={{ fontSize:9.5, color:"var(--ink-4)", lineHeight:1.6 }}>
        {(row.risk_flags||[]).length > 0
          ? "This event has risk flags and is queued for conformation. Click ▶ PROCESS QUEUE or wait for the 30s auto-cycle."
          : "No risk flags — this event will not enter the governance pipeline."}
      </div>
    </div>
  );
  const violations = adj.policy_violations || [];
  const ss = adj.source_system || "MCP_PROXY";
  const schemaConform = {
    SAP:"SAP-CDHDR-v1-conform", GITHUB:"GitHub-Webhook-v3-conform",
    SAILPOINT:"SailPoint-IDN-v3-conform", MCP_PROXY:"MCP-Telemetry-v1-conform",
    SYSTEM_TELEMETRY:"System-Telemetry-v1-conform",
  };
  return (
    <div style={{ padding:"10px 12px", overflowY:"auto", flex:1 }}>
      <PaneSection label="Conformation">
        <PaneKV k="Handler"       v={`${ss}SilverConformer`} />
        <PaneKV k="Schema"        v={schemaConform[ss] || "generic-conform"} />
        <PaneKV k="Resource"      v={adj.target_tool || row.target_tool || "—"} />
        <PaneKV k="Resource Type" v="mcp_tool" />
        <PaneKV k="Action"        v="tools/call" />
        <PaneKV k="Outcome"       v={row.status || "unknown"} />
        <PaneKV k="Stage Out"     v="SILVER" vColor="var(--ink-2)" />
      </PaneSection>
      <PaneSection label={`Policy-as-Code · ${violations.length} violation${violations.length !== 1 ? "s" : ""}`}>
        {violations.length === 0 ? (
          <div style={{ fontSize:10, color:"var(--green-ink)", display:"flex", alignItems:"center", gap:4 }}>
            <span>✓</span> All policies passed
          </div>
        ) : (
          violations.map((v, i) => (
            <div key={i} style={{ fontSize:9.5, color:"var(--red-ink)", padding:"4px 0",
              borderBottom:"1px solid var(--line)", lineHeight:1.5 }}>{v}</div>
          ))
        )}
      </PaneSection>
      {adj.conflict_flags?.length > 0 && (
        <PaneSection label="Conflict Flags">
          {adj.conflict_flags.map((f, i) => (
            <div key={i} style={{ fontSize:9.5, color:"var(--amber-ink)", padding:"2px 0" }}>{f}</div>
          ))}
        </PaneSection>
      )}
    </div>
  );
}

function GoldDetail({ row, adj }) {
  if (!row) return null;
  if (!adj) return (
    <div style={{ padding:"10px 12px", overflowY:"auto", flex:1 }}>
      <div style={{ fontSize:10, color:"var(--ink-3)" }}>Not yet scored.</div>
    </div>
  );
  const violations  = adj.policy_violations || [];
  const flags       = adj.risk_flags || [];
  const evType      = _flagToEventType(flags, row.status, row.origin);
  const baseW       = _MCP_BASE_WEIGHT[evType] ?? 0.35;
  const violPenalty  = violations.reduce((s, v) => s + _violSeverityWeight(v), 0);
  const actorType    = row.origin === "system" ? "HUMAN" : "SERVICE";
  const actorPenalty = actorType === "SERVICE" ? 0.08 : 0.0;
  const reconScore   = Math.min(1.0, baseW + violPenalty + actorPenalty);
  const ts      = adj.risk_tier || "LOW";
  const tStyle  = _UBO_TIER_STYLE[ts] || _UBO_TIER_STYLE.LOW;
  const vStyle  = _UBO_VERDICT_STYLE[adj.final_verdict || "CLEAR"] || _UBO_VERDICT_STYLE.CLEAR;
  const score   = adj.risk_score;
  const scoreColor = ts === "CRITICAL" ? "var(--red-ink)" : ts === "HIGH" ? "var(--amber-ink)"
    : ts === "MEDIUM" ? "var(--blue-ink)" : "var(--green-ink)";
  return (
    <div style={{ padding:"10px 12px", overflowY:"auto", flex:1 }}>
      <div style={{ textAlign:"center", padding:"6px 0 12px", borderBottom:"1px solid var(--line)", marginBottom:10 }}>
        <div style={{ fontSize:38, fontWeight:700, fontFamily:"'Geist Mono',monospace",
          color:scoreColor, lineHeight:1 }}>
          {score != null ? score.toFixed(3) : "—"}
        </div>
        <div style={{ display:"flex", justifyContent:"center", gap:6, marginTop:6 }}>
          <span style={{ fontSize:10, fontWeight:700, padding:"2px 8px", borderRadius:4,
            background:tStyle.bg, color:tStyle.ink }}>{ts}</span>
          <span style={{ fontSize:10, fontWeight:700, padding:"2px 8px", borderRadius:4,
            background:vStyle.bg, color:vStyle.ink }}>{adj.final_verdict || "—"}</span>
        </div>
        <div style={{ display:"flex", justifyContent:"center", marginTop:6 }}>
          <ProvenanceChip verdict={adj.final_verdict}
            confidence={adj.ensemble_confidence != null ? `${(adj.ensemble_confidence * 100).toFixed(0)}%` : null} />
        </div>
      </div>
      <PaneSection label="Score Breakdown">
        <PaneKV k={`Base (${evType})`} v={baseW.toFixed(2)} vColor="var(--ink-2)" />
        <PaneKV k="Violation penalty"   v={`+${violPenalty.toFixed(2)}`}
          vColor={violPenalty > 0 ? "var(--red-ink)" : "var(--ink-4)"} />
        <PaneKV k={`Actor (${actorType})`} v={`+${actorPenalty.toFixed(2)}`} vColor={actorPenalty > 0 ? "var(--amber-ink,#b45309)" : "var(--ink-4)"} />
        <PaneKV k="Cascade bonus"       v="+0.00" vColor="var(--ink-4)" />
        <div style={{ display:"flex", justifyContent:"space-between", padding:"4px 0",
          borderTop:"1px solid var(--line)", marginTop:2 }}>
          <span style={{ fontSize:10, fontWeight:600, color:"var(--ink)" }}>Reconstructed</span>
          <span style={{ fontSize:10, fontWeight:600, fontFamily:"'Geist Mono',monospace",
            color:"var(--ink)" }}>≈ {reconScore.toFixed(3)}</span>
        </div>
      </PaneSection>
      <PaneSection label="Adjudication">
        <PaneKV k="Confidence" v={adj.ensemble_confidence != null
          ? `${(adj.ensemble_confidence*100).toFixed(0)}%` : "—"} />
        <PaneKV k="Stage Out"  v="GOLD → COUNCIL" vColor="var(--amber-ink,#b45309)" />
        {adj.requires_human_review && (
          <PaneKV k="Human Review" v="⚠ REQUIRED" vColor="var(--red-ink)" />
        )}
        {adj.adjudicated_at && (
          <PaneKV k="Adjudicated" v={new Date(adj.adjudicated_at).toLocaleTimeString("en-US",
            {hour:"2-digit",minute:"2-digit",second:"2-digit"})} />
        )}
      </PaneSection>
    </div>
  );
}

// mcp_telemetry.id and system_telemetry.id are independent sequences and can
// collide, so every raw row and adjudication is addressed by an origin-qualified
// key rather than the bare numeric id.
const _rowKey = r => `${r.origin || "mcp"}:${r.id}`;
const _adjKey = a => `${(a.source_system === "SYSTEM_TELEMETRY") ? "system" : "mcp"}:${a.telemetry_id ?? a.system_telemetry_id}`;

function RawFeedTab({ rows, adjudicated, loading, isPaused }) {
  const LiveBadge = window.LiveBadge;
  const [selectedKey, setSelectedKey] = useState(null);

  const adjByKey = useMemo(() => {
    const m = new Map();
    (adjudicated || []).forEach(a => {
      if (a.telemetry_id != null || a.system_telemetry_id != null) m.set(_adjKey(a), a);
    });
    return m;
  }, [adjudicated]);

  useEffect(() => {
    if (rows.length > 0 && selectedKey === null) setSelectedKey(_rowKey(rows[0]));
  }, [rows]);

  const selectedRaw = useMemo(
    () => rows.find(r => _rowKey(r) === selectedKey) ?? null,
    [rows, selectedKey],
  );
  const selectedAdj = useMemo(
    () => (selectedKey != null ? adjByKey.get(selectedKey) ?? null : null),
    [adjByKey, selectedKey],
  );

  if (loading && rows.length === 0) return (
    <div style={{padding:"32px 18px",textAlign:"center",color:"var(--ink-3)",fontSize:12}}>
      <span className="spin"/> Loading telemetry…
    </div>
  );

  return (
    <div style={{ display:"flex", flexDirection:"column", height:"100%", overflow:"hidden" }}>
      {/* Top bar */}
      <div style={{ padding:"5px 12px", borderBottom:"1px solid var(--line)", display:"flex",
        alignItems:"center", gap:8, flexShrink:0 }}>
        <span style={{ fontSize:9, fontWeight:700, color:"var(--ink-4)", fontFamily:"'Geist Mono',monospace",
          letterSpacing:".06em" }}>BRONZE FEED</span>
        <LiveBadge isPaused={isPaused} compact/>
        <span style={{fontSize:9,color:"var(--ink-4)",marginLeft:"auto"}}>{rows.length} rows</span>
      </div>

      {/* Three-pane pipeline flow */}
      <div style={{ display:"flex", flex:1, overflow:"hidden", minHeight:0 }}>

        {/* ── Bronze column: event list ── */}
        <div style={{ width:"34%", minWidth:190, display:"flex", flexDirection:"column",
          overflow:"hidden", borderRight:"1px solid var(--line)" }}>
          <MedallionPaneHdr tier="BRONZE" subtitle="Raw Ingestion" color={_BRONZE_HDR} />
          {rows.length === 0 ? (
            <div style={{padding:12,color:"var(--ink-4)",fontSize:11}}>
              No telemetry yet. MCP tool calls and system_telemetry events from monitored systems appear here as they arrive.
            </div>
          ) : (
            <div style={{ overflowY:"auto", flex:1 }}>
              {rows.map((r, i) => {
                const rKey     = _rowKey(r);
                const hasFlags = (r.risk_flags || []).length > 0;
                const isAdj    = adjByKey.has(rKey);
                const isSel    = rKey === selectedKey;
                const ts       = r.ts ? new Date(r.ts) : null;
                return (
                  <Clickable key={rKey} onClick={() => setSelectedKey(rKey)}
                    style={{ display:"flex", flexDirection:"column", gap:3, padding:"7px 12px",
                      cursor:"pointer", borderBottom:"1px solid var(--line)",
                      borderLeft: isSel ? "3px solid var(--amber-ink,#b45309)" : "3px solid transparent",
                      background: isSel ? "var(--surface-2)" : "transparent" }}>
                    <div style={{display:"flex",alignItems:"center",gap:5}}>
                      <span style={{fontSize:9,fontFamily:"'Geist Mono',monospace",color:"var(--ink-4)",flexShrink:0}}>
                        {ts ? ts.toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}) : "—"}
                      </span>
                      <span style={{fontSize:8,fontWeight:700,padding:"1px 4px",borderRadius:3,flexShrink:0,
                        background: r.direction==="request" ? "var(--blue-soft,#dbeafe)" : r.direction==="event" ? "var(--amber-soft,#fff8e1)" : "var(--violet-soft,#ede9fe)",
                        color:      r.direction==="request" ? "var(--blue-ink,#1e40af)"  : r.direction==="event" ? "var(--amber-ink,#b45309)" : "var(--violet-ink,#5b21b6)"}}>
                        {(r.direction||"?").toUpperCase()}
                      </span>
                      <span style={{fontSize:10,fontFamily:"'Geist Mono',monospace",flex:1,overflow:"hidden",
                        textOverflow:"ellipsis",whiteSpace:"nowrap",color:"var(--ink-1)"}}>
                        {r.target_tool || r.method || "—"}
                      </span>
                    </div>
                    <div style={{display:"flex",alignItems:"center",gap:3,flexWrap:"wrap"}}>
                      {hasFlags && (r.risk_flags||[]).slice(0,3).map((f, fi) => {
                        const fc = _FLAG_COLOR[f] || {bg:"var(--surface-2)",ink:"var(--ink-2)"};
                        return (
                          <span key={fi} style={{fontSize:8,fontWeight:700,padding:"1px 4px",borderRadius:3,
                            background:fc.bg,color:fc.ink,fontFamily:"'Geist Mono',monospace"}}>{f}</span>
                        );
                      })}
                      {hasFlags && (r.risk_flags||[]).length > 3 && (
                        <span style={{fontSize:8,color:"var(--ink-4)"}}>+{(r.risk_flags||[]).length-3}</span>
                      )}
                      <span style={{fontSize:8,marginLeft:"auto",fontFamily:"'Geist Mono',monospace",
                        color: isAdj ? "var(--green-ink)" : hasFlags ? "var(--amber-ink)" : "var(--ink-4)"}}>
                        {isAdj ? "✓ ADJ" : hasFlags ? "⟳ PEND" : "OK"}
                      </span>
                    </div>
                  </Clickable>
                );
              })}
            </div>
          )}
          {/* Bronze detail for selected event */}
          {selectedRaw && (
            <div style={{ borderTop:"1px solid var(--line)", flex:"0 0 auto", maxHeight:"45%",
              display:"flex", flexDirection:"column", overflow:"hidden" }}>
              <div style={{ fontSize:9, fontWeight:700, color:"var(--ink-4)", letterSpacing:".06em",
                fontFamily:"'Geist Mono',monospace", padding:"5px 12px 2px",
                borderBottom:"1px solid var(--line)" }}>BRONZE DETAIL</div>
              <BronzeDetail row={selectedRaw} />
            </div>
          )}
        </div>

        {/* B→S arrow */}
        <div style={{ display:"flex", alignItems:"center", justifyContent:"center", width:18,
          flexShrink:0, color:"var(--ink-4)", fontSize:13, userSelect:"none",
          background:"var(--surface-1,var(--bg))" }}>→</div>

        {/* ── Silver column: conformation + policy ── */}
        <div style={{ flex:1, minWidth:0, display:"flex", flexDirection:"column", overflow:"hidden",
          borderLeft:"1px solid var(--line)", borderRight:"1px solid var(--line)" }}>
          <MedallionPaneHdr tier="SILVER" subtitle="Conformation + Policy" color={_SILVER_HDR} />
          <SilverDetail row={selectedRaw} adj={selectedAdj} />
        </div>

        {/* S→G arrow */}
        <div style={{ display:"flex", alignItems:"center", justifyContent:"center", width:18,
          flexShrink:0, color:"var(--ink-4)", fontSize:13, userSelect:"none",
          background:"var(--surface-1,var(--bg))" }}>→</div>

        {/* ── Gold column: scoring + adjudication ── */}
        <div style={{ flex:1, minWidth:0, display:"flex", flexDirection:"column", overflow:"hidden",
          borderLeft:"1px solid var(--line)" }}>
          <MedallionPaneHdr tier="GOLD" subtitle="Scoring + Adjudication" color={_GOLD_HDR} />
          <GoldDetail row={selectedRaw} adj={selectedAdj} />
        </div>

      </div>
    </div>
  );
}

// ── Governance Holds tab ──────────────────────────────────────────────────────

function HoldRow({ hold, onResolve }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);

  async function resolve(status) {
    setBusy(true);
    const ok = await onResolve(hold.id, status);
    setBusy(false);
    if (ok) setDone(status);
  }

  const ageS = hold.created_at
    ? Math.round((Date.now() - new Date(hold.created_at)) / 1000)
    : null;

  return (
    <div style={{
      display:"flex", alignItems:"center", gap:10, padding:"10px 14px",
      background:"var(--surface-1)", border:"1.5px solid var(--red-ink,#c0392b)",
      borderRadius:6, marginBottom:8,
    }}>
      <span style={{fontSize:11,fontWeight:700,color:"var(--red-ink)",fontFamily:"'Geist Mono',monospace",flexShrink:0}}>
        ⛔ {hold.target_tool || "—"}
      </span>
      <span style={{fontSize:10,color:"var(--ink-3)",fontFamily:"'Geist Mono',monospace"}}>
        {hold.session_id ? hold.session_id.slice(0,8) + "…" : "—"}
      </span>
      {ageS != null && (
        <span style={{fontSize:10,color:"var(--ink-4)",flexShrink:0}}>{ageS}s ago</span>
      )}
      <div style={{marginLeft:"auto",display:"flex",gap:6}}>
        {done ? (
          <span style={{
            fontSize:10,fontWeight:700,padding:"3px 10px",borderRadius:4,
            background: done === "APPROVED" ? "var(--green-soft)" : "var(--surface-2)",
            color:      done === "APPROVED" ? "var(--green-ink)"  : "var(--ink-3)",
          }}>{done === "APPROVED" ? "✓ APPROVED" : "✕ DENIED"}</span>
        ) : (<>
          <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => resolve("APPROVED")}>
            {busy ? <span className="spin"/> : "✓ Approve"}
          </button>
          <button className="btn btn-sm" disabled={busy} onClick={() => resolve("DENIED")}>
            {busy ? <span className="spin"/> : "✕ Deny"}
          </button>
        </>)}
      </div>
    </div>
  );
}

function HoldsTab({ holds, onResolve }) {
  if (holds.length === 0) return (
    <Empty>
      No pending governance holds. Holds appear when a blocking-tier tool
      (shell, execute, drop, truncate, exec_sql…) is called and the operator
      must approve or deny before the request is forwarded.
    </Empty>
  );
  return (<>
    <div className="bb-section-sep">
      <span style={{color:"var(--red-ink)"}}>⛔ PENDING EXECUTION HOLDS</span>
      <span>{holds.length} AWAITING DECISION</span>
    </div>
    <div style={{padding:"0 18px 18px"}}>
      {holds.map((h, i) => (
        <HoldRow key={h.id ?? i} hold={h} onResolve={onResolve} />
      ))}
      <div style={{fontSize:10,color:"var(--ink-4)",marginTop:8,lineHeight:1.6}}>
        Holds time out after 30 s by default (PROXY_HOLD_TIMEOUT_S). The proxy forwards
        on timeout but logs a warning. Configure PROXY_BLOCKING_TOOLS to add or remove tools
        from the blocking list.
      </div>
    </div>
  </>);
}

// ── Coverage Report tab ───────────────────────────────────────────────────────

function CoverageSuppressRow({ row, onSuppress, onDone }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!reason.trim()) return;
    setBusy(true);
    const ok = await onSuppress({
      server_name: row.server_name,
      target_tool: row.target_tool,
      reason: reason.trim(),
      created_by: "operator",
    });
    setBusy(false);
    if (ok) onDone();
  }

  if (!open) {
    return (
      <button className="btn btn-sm" style={{fontSize:9.5,padding:"2px 8px"}} onClick={() => setOpen(true)}>
        Suppress…
      </button>
    );
  }
  return (
    <div style={{display:"flex",gap:4,alignItems:"center"}}>
      <input
        className="code-input mono" style={{fontSize:10,width:150}}
        placeholder="reason (required)"
        value={reason} onChange={e => setReason(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter") submit(); if (e.key === "Escape") setOpen(false); }}
        autoFocus
      />
      <button className="btn btn-sm btn-acc" style={{fontSize:9.5,padding:"2px 8px"}} disabled={busy || !reason.trim()} onClick={submit}>
        {busy ? "…" : "✓"}
      </button>
      <button className="btn btn-sm" style={{fontSize:9.5,padding:"2px 8px"}} onClick={() => setOpen(false)}>✕</button>
    </div>
  );
}

// Fetched lazily on expand (not prefetched for every row) — most coverage
// reports have far more rows than a reviewer will ever click into.
function CoverageDetailPanel({ serverName, targetTool, kind }) {
  const [detail, setDetail] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const base = _uboBase();
    const params = new URLSearchParams({ server_name: serverName || "", target_tool: targetTool || "", kind });
    fetch(`${base}/observability/coverage/detail?${params.toString()}`, { credentials: "include", headers: _cemAuthHdr() })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => { if (!cancelled) setDetail(d); })
      .catch(e => { if (!cancelled) setErr(e.message); });
    return () => { cancelled = true; };
  }, [serverName, targetTool, kind]);

  if (err) return <div style={{fontSize:11,color:"var(--red-ink)"}}>Failed to load detail: {err}</div>;
  if (!detail) return <div style={{fontSize:11,color:"var(--ink-3)"}}><span className="spin"/> Loading detail…</div>;

  return (
    <div style={{display:"flex",flexDirection:"column",gap:10}}>
      <div>
        <div className="cem-section-lbl">Proposed resolution</div>
        <div className="rca-box">{detail.proposed_resolution}</div>
      </div>
      {detail.rule_references.length > 0 && (
        <div style={{fontSize:10.5,color:"var(--ink-3)"}}>
          Referenced in: {detail.rule_references.join(", ")}
        </div>
      )}
      <div>
        <div className="cem-section-lbl">Recent calls ({detail.recent_samples.length})</div>
        {detail.recent_samples.length === 0 ? (
          <div style={{fontSize:10.5,color:"var(--ink-3)"}}>No sample calls available.</div>
        ) : (
          <div style={{display:"flex",flexDirection:"column",gap:4}}>
            {detail.recent_samples.map((s, i) => (
              <div key={i} style={{
                display:"flex", alignItems:"baseline", gap:10, fontSize:10.5,
                padding:"4px 8px", borderRadius:4, background:"var(--surface-1)", border:"1px solid var(--line)",
              }}>
                <span className="mono" style={{color:"var(--ink-3)",flexShrink:0}}>
                  {s.ts ? new Date(s.ts).toLocaleString("en-US",{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "—"}
                </span>
                {s.status && <span style={{flexShrink:0}}>{s.status}</span>}
                {s.severity && <span style={{flexShrink:0}}>{s.severity}</span>}
                {s.actor && <span style={{color:"var(--ink-3)",flexShrink:0}}>actor: {s.actor}</span>}
                {s.resource && <span style={{color:"var(--ink-2)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{s.resource}</span>}
                {s.error_message && <span style={{color:"var(--red-ink)"}}>{s.error_message}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function CoverageTab({ coverage, loading, onSuppress }) {
  const [justSuppressed, setJustSuppressed] = useState(new Set());
  const [expandedRows, setExpandedRows] = useState(new Set());

  if (loading && coverage.length === 0) return (
    <div style={{padding:"32px 18px",textAlign:"center",color:"var(--ink-3)",fontSize:12}}>
      <span className="spin"/> Loading coverage…
    </div>
  );
  const visible = coverage.filter((r, i) => !justSuppressed.has(`${r.server_name}::${r.target_tool}`));
  const blindSpots = visible.filter(r => (r.flag_rate || 0) === 0).length;
  const mcpCount = visible.filter(r => r.kind === "mcp").length;
  const systemCount = visible.filter(r => r.kind === "system").length;
  return (<>
    <div className="bb-section-sep">
      <span>TOOL COVERAGE REPORT · {mcpCount} MCP · {systemCount} SYSTEM</span>
      <span>{visible.length} TOOLS · {blindSpots > 0 ? <span style={{color:"var(--amber-ink)"}}>{blindSpots} BLIND SPOTS</span> : "ALL COVERED"}</span>
    </div>
    {visible.length === 0 ? (
      <Empty>No tool calls recorded yet. Coverage data appears once MCP tool calls or system_telemetry events (GitHub, SAP, Saviynt, any registered non-MCP system) flow through governance.</Empty>
    ) : (
      <div style={{padding:"0 18px 18px",overflowX:"auto"}}>
        <table className="ubo-lat-table">
          <thead>
            <tr>
              <th>Source</th><th>Server</th><th>Tool / Action</th><th>Calls</th>
              <th>Flagged</th><th>Flag %</th><th>Status</th><th></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r, i) => {
              const isBlind = (r.flag_rate || 0) === 0;
              const rowKey = `${r.server_name}::${r.target_tool}`;
              const isExpanded = expandedRows.has(rowKey);
              const toggleExpanded = () => setExpandedRows(prev => {
                const next = new Set(prev);
                next.has(rowKey) ? next.delete(rowKey) : next.add(rowKey);
                return next;
              });
              return (
              <React.Fragment key={i}>
                <tr>
                  <td>
                    <span style={{
                      fontSize:9,fontWeight:600,padding:"1px 6px",borderRadius:999,
                      background: r.kind === "mcp" ? "var(--surface-2)" : "var(--acc-soft)",
                      color: r.kind === "mcp" ? "var(--ink-3)" : "var(--acc-ink)",
                    }}>
                      {r.kind === "mcp" ? "MCP" : "SYSTEM"}
                    </span>
                  </td>
                  <td className="mono">{r.server_name || "—"}</td>
                  <td className="mono">{r.target_tool || "—"}</td>
                  <td>{r.call_count}</td>
                  <td>{r.flagged_count}</td>
                  <td className={isBlind ? "ubo-lat-warn" : ""}>
                    {r.flag_rate != null ? `${r.flag_rate}%` : "—"}
                  </td>
                  <td>
                    {isBlind
                      ? <span onClick={toggleExpanded} style={{cursor:"pointer",color:"var(--amber-ink)",fontSize:10,fontWeight:700,fontFamily:"'Geist Mono',monospace"}}>
                          ⚠ BLIND SPOT {isExpanded ? "▲" : "▼"}
                        </span>
                      : <span style={{color:"var(--green-ink)",fontSize:10,fontWeight:700,fontFamily:"'Geist Mono',monospace"}}>✓ COVERED</span>
                    }
                  </td>
                  <td>
                    {isBlind && onSuppress && (
                      <CoverageSuppressRow row={r} onSuppress={onSuppress}
                        onDone={() => setJustSuppressed(prev => new Set(prev).add(rowKey))} />
                    )}
                  </td>
                </tr>
                {isBlind && isExpanded && (
                  <tr>
                    <td colSpan={8} style={{background:"var(--surface-1)",padding:"10px 14px"}}>
                      <CoverageDetailPanel serverName={r.server_name} targetTool={r.target_tool} kind={r.kind} />
                    </td>
                  </tr>
                )}
              </React.Fragment>
              );
            })}
          </tbody>
        </table>
        <div style={{fontSize:10,color:"var(--ink-4)",marginTop:10,lineHeight:1.6}}>
          <strong>Blind spot</strong> = this tool/action has been called but no governance rule has ever flagged it —
          it may be genuinely low-risk, or it may mean no detection rule covers it at all. Click <strong>Suppress…</strong>
          to record a reviewed, documented decision that it's safe to ignore (adds it to the Suppressions tab with a
          reason, and auto-clears future calls without adjudicating them). If it's actually a gap, ask your platform
          team to add a detection rule for it.
        </div>
      </div>
    )}
  </>);
}

// ── Session Timeline tab ──────────────────────────────────────────────────────

function TimelineRow({ row }) {
  const isReq     = row.direction === "request";
  const hasFlags  = (row.risk_flags || []).length > 0;
  const ts        = row.ts ? new Date(row.ts) : null;
  const tier      = row.risk_tier;
  const tierStyle = tier ? (_UBO_TIER_STYLE[tier] || _UBO_TIER_STYLE.LOW) : null;
  return (
    <div style={{
      display:"flex", alignItems:"center", gap:6, padding:"5px 0",
      borderBottom:"1px solid var(--line)", fontSize:10,
    }}>
      <span style={{fontSize:9,color:"var(--ink-4)",fontFamily:"'Geist Mono',monospace",flexShrink:0,width:60}}>
        {ts ? ts.toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}) : "—"}
      </span>
      <span style={{fontSize:8,fontWeight:700,padding:"1px 5px",borderRadius:3,flexShrink:0,
        background: isReq ? "var(--blue-soft)" : "var(--violet-soft,#ede9fe)",
        color:      isReq ? "var(--blue-ink)"  : "var(--violet-ink,#5b21b6)"}}>
        {isReq ? "→ REQ" : "← RES"}
      </span>
      <span style={{fontFamily:"'Geist Mono',monospace",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:"var(--ink-1)"}}>
        {row.target_tool || row.method || "—"}
      </span>
      {row.execution_time_ms != null && (
        <span style={{fontSize:9,color:"var(--ink-4)",flexShrink:0}}>{row.execution_time_ms}ms</span>
      )}
      {row.status && (
        <span style={{fontSize:9,fontWeight:700,color: row.status === "error" ? "var(--red-ink)" : "var(--green-ink)",flexShrink:0}}>
          {row.status.toUpperCase()}
        </span>
      )}
      {hasFlags && (row.risk_flags||[]).slice(0,2).map((f, fi) => {
        const fc = _FLAG_COLOR[f] || {bg:"var(--surface-2)",ink:"var(--ink-2)"};
        return (
          <span key={fi} style={{fontSize:8,fontWeight:700,padding:"1px 4px",borderRadius:3,flexShrink:0,
            background:fc.bg,color:fc.ink,fontFamily:"'Geist Mono',monospace"}}>{f}</span>
        );
      })}
      {hasFlags && (row.risk_flags||[]).length > 2 && (
        <span style={{fontSize:8,color:"var(--ink-4)",flexShrink:0}}>+{(row.risk_flags||[]).length-2}</span>
      )}
      {tierStyle && (
        <span style={{fontSize:8,fontWeight:700,padding:"1px 5px",borderRadius:3,flexShrink:0,
          background:tierStyle.bg,color:tierStyle.ink}}>{tier}</span>
      )}
      {row.final_verdict && (
        <span style={{fontSize:8,fontWeight:700,padding:"1px 5px",borderRadius:3,flexShrink:0,
          ...((_UBO_VERDICT_STYLE[row.final_verdict]||_UBO_VERDICT_STYLE.CLEAR))}}>{row.final_verdict}</span>
      )}
    </div>
  );
}

function TimelineTab({ adjudicated, loading }) {
  const [sessionId, setSessionId] = useState(null);
  const [timeline,  setTimeline]  = useState([]);
  const [tlLoading, setTlLoading] = useState(false);

  // Unique sessions from adjudicated data, most recent first. allSessions is
  // kept alongside the displayed slice so the header can say "10 of 43"
  // instead of implying 10 is the total — sessions.length used to BE the
  // post-slice count, silently understating real volume once there were
  // more than 10 sessions in the loaded window.
  const allSessions = useMemo(() => {
    const seen = new Set();
    const out  = [];
    for (const r of adjudicated) {
      if (r.session_id && !seen.has(r.session_id)) {
        seen.add(r.session_id);
        out.push(r);
      }
    }
    return out;
  }, [adjudicated]);
  const sessions = useMemo(() => allSessions.slice(0, 10), [allSessions]);

  async function loadTimeline(sid) {
    setSessionId(sid);
    setTlLoading(true);
    try {
      const res = await fetch(`${_uboBase()}/observability/session/${sid}/timeline`);
      if (res.ok) { const d = await res.json(); setTimeline(d.rows || []); }
    } catch (_) {}
    setTlLoading(false);
  }

  return (<>
    <div className="bb-section-sep">
      <span>SESSION TIMELINE</span>
      <span>
        {allSessions.length > sessions.length
          ? `SHOWING ${sessions.length} OF ${allSessions.length} SESSIONS`
          : `${sessions.length} SESSIONS WITH ADJUDICATIONS`}
      </span>
    </div>
    <div style={{padding:"0 18px 8px"}}>
      {sessions.length === 0 ? (
        <div style={{fontSize:11,color:"var(--ink-4)",padding:"8px 0"}}>
          No sessions with adjudicated calls yet. Sessions appear here once the UBO™ pipeline processes flagged telemetry.
        </div>
      ) : (
        <div className="cem-toolbar">
          {sessions.map(r => (
            <button key={r.session_id}
              className={"cem-filter" + (sessionId === r.session_id ? " active" : "")}
              onClick={() => loadTimeline(r.session_id)}>
              {r.session_id.slice(0,8)}…{r.server_name ? ` [${r.server_name}]` : ""}
            </button>
          ))}
        </div>
      )}
    </div>
    {sessionId && (
      tlLoading ? (
        <div style={{padding:"32px 18px",textAlign:"center",color:"var(--ink-3)",fontSize:12}}>
          <span className="spin"/> Loading timeline…
        </div>
      ) : timeline.length === 0 ? (
        <Empty>No telemetry rows found for this session.</Empty>
      ) : (
        <div style={{padding:"0 18px 18px"}}>
          <div style={{fontSize:9,fontWeight:700,color:"var(--ink-4)",fontFamily:"'Geist Mono',monospace",
            letterSpacing:".06em",marginBottom:6}}>
            SESSION {sessionId.slice(0,8).toUpperCase()} — {timeline.length} EVENTS
          </div>
          {timeline.map((row, i) => <TimelineRow key={i} row={row} />)}
        </div>
      )
    )}
  </>);
}

// ── Suppressions tab ──────────────────────────────────────────────────────────

function SuppressionsTab({ suppressions, onAdd, onDelete }) {
  const [showForm,  setShowForm]  = useState(false);
  const [tool,      setTool]      = useState("");
  const [server,    setServer]    = useState("");
  const [argsHash,  setArgsHash]  = useState("");
  const [reason,    setReason]    = useState("");
  const [busy,      setBusy]      = useState(false);

  async function handleAdd(e) {
    e.preventDefault();
    setBusy(true);
    await onAdd({
      target_tool:    tool.trim()     || undefined,
      server_name:    server.trim()   || undefined,
      tool_args_hash: argsHash.trim() || undefined,
      reason:         reason.trim()   || "Operator suppression",
    });
    setBusy(false);
    setShowForm(false);
    setTool(""); setServer(""); setArgsHash(""); setReason("");
  }

  const active = suppressions.filter(s => s.active);
  return (<>
    <div className="bb-section-sep">
      <span>SUPPRESSION ALLOWLIST</span>
      <span>{active.length} ACTIVE RULES</span>
    </div>
    <div style={{padding:"0 18px 18px"}}>
      <div style={{marginBottom:10}}>
        <button className="btn btn-sm btn-primary" onClick={() => setShowForm(s => !s)}>
          {showForm ? "Cancel" : "+ Add Rule"}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleAdd} style={{
          background:"var(--surface-1)",border:"1px solid var(--line)",borderRadius:6,
          padding:"12px 14px",marginBottom:12,display:"flex",flexDirection:"column",gap:8,
        }}>
          <div style={{fontSize:11,fontWeight:700,color:"var(--ink-2)",marginBottom:2}}>New Suppression Rule</div>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
            {[
              [tool, setTool, "Tool name (e.g. get_company)"],
              [server, setServer, "Server name (e.g. edgar)"],
            ].map(([val, set, ph], i) => (
              <input key={i} value={val} onChange={e => set(e.target.value)}
                placeholder={ph} style={{fontSize:11,padding:"5px 8px",borderRadius:4,
                  border:"1px solid var(--line)",background:"var(--surface-2)",color:"var(--ink-1)"}}/>
            ))}
          </div>
          <input value={argsHash} onChange={e => setArgsHash(e.target.value)}
            placeholder="Args hash (SHA-256, optional — leave blank to match any args)"
            style={{fontSize:11,padding:"5px 8px",borderRadius:4,border:"1px solid var(--line)",
              background:"var(--surface-2)",color:"var(--ink-1)",fontFamily:"'Geist Mono',monospace"}}/>
          <input value={reason} onChange={e => setReason(e.target.value)}
            placeholder="Reason (e.g. read-only tool, reviewed and approved)"
            style={{fontSize:11,padding:"5px 8px",borderRadius:4,border:"1px solid var(--line)",
              background:"var(--surface-2)",color:"var(--ink-1)"}}/>
          <div style={{fontSize:10,color:"var(--ink-4)"}}>
            Leave tool/server blank to match <em>any</em>. At least one field must be filled.
          </div>
          <div style={{display:"flex",gap:6}}>
            <button type="submit" className="btn btn-sm btn-primary" disabled={busy || (!tool && !server && !argsHash)}>
              {busy ? <><span className="spin"/> Saving…</> : "Save Rule"}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setShowForm(false)}>Cancel</button>
          </div>
        </form>
      )}

      {active.length === 0 ? (
        <div style={{fontSize:11,color:"var(--ink-4)",padding:"8px 0"}}>
          No active suppression rules. Add a rule to auto-clear matching flagged calls without running the UBO™ pipeline.
        </div>
      ) : (
        <table className="ubo-lat-table">
          <thead>
            <tr>
              <th>Server</th><th>Tool</th><th>Args Hash</th><th>Reason</th><th>Created</th><th></th>
            </tr>
          </thead>
          <tbody>
            {active.map((s, i) => (
              <tr key={s.id ?? i}>
                <td className="mono">{s.server_name || <span style={{color:"var(--ink-4)"}}>any</span>}</td>
                <td className="mono">{s.target_tool || <span style={{color:"var(--ink-4)"}}>any</span>}</td>
                <td className="mono" style={{fontSize:9,maxWidth:120,overflow:"hidden",textOverflow:"ellipsis"}}>
                  {s.tool_args_hash ? s.tool_args_hash.slice(0,12) + "…" : <span style={{color:"var(--ink-4)"}}>any</span>}
                </td>
                <td style={{maxWidth:200,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontSize:10}}>
                  {s.reason || "—"}
                </td>
                <td style={{fontSize:9,color:"var(--ink-4)"}}>
                  {s.created_at ? new Date(s.created_at).toLocaleDateString() : "—"}
                </td>
                <td>
                  <button className="btn btn-sm" style={{fontSize:10,padding:"2px 8px"}}
                    onClick={() => onDelete(s.id)}>✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  </>);
}

// Pulse keyframe injected once for the live indicator dot
if (typeof document !== "undefined" && !document.getElementById("ubo-pulse-style")) {
  const s = document.createElement("style");
  s.id = "ubo-pulse-style";
  s.textContent = `@keyframes ubo-pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
    .ubo-adj-new { border-left: 3px solid var(--green-ink,#166534) !important; }`;
  document.head.appendChild(s);
}

Object.assign(window, { CEMPanel, UBOGovPanel, TIERS, notifMsgFor });
