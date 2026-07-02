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

function CEMPanel({ events, setEvents, filter, setFilter, expanded, setExpanded, onAckNotif, onInject }) {
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
          <button className="btn btn-sm" onClick={onInject}><Icon name="bolt" size={12}/> INJECT EVENT</button>
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
  return (
    <div className={`cem-event ${ev.severity}`}>
      <div className="cem-head" onClick={onToggle}>
        <span className="sev">{ev.severity}</span>
        <span className="name">{ev.control}</span>
        {ev.source === "8-K Filing" && (
          <span style={{fontSize:10, padding:"1px 6px", borderRadius:4, background:"var(--blue-soft)", color:"var(--blue-ink)", fontWeight:600, letterSpacing:".04em", flexShrink:0}}>
            8-K {ev.filingDate || ""}
          </span>
        )}
        <span className="ts mono">{new Date(ev.ts).toLocaleTimeString("en-US", {hour:"2-digit",minute:"2-digit",second:"2-digit"})}</span>
        <Icon name={expanded ? "chev-u" : "chev-d"} size={14} className="muted"/>
      </div>
      {expanded && (
        <div className="cem-body">
          <div className="cem-meta">
            <CEMMeta l="Area"     v={ev.area}/>
            <CEMMeta l="Category" v={ev.category}/>
            <CEMMeta l="Risk"     v={ev.risk}/>
            <CEMMeta l="Exposure" v={ev.exposure}/>
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

// ── UBO Governance Brain panel ────────────────────────────────────────────────

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

function UBOGovPanel() {
  const [adjudicated, setAdjudicated] = useState([]);
  const [humanReview, setHumanReview] = useState([]);
  const [latency,     setLatency]     = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [triggering,  setTriggering]  = useState(false);
  const [filter,      setFilter]      = useState("all");
  const [expanded,    setExpanded]    = useState(new Set());
  const [lastRefresh, setLastRefresh] = useState(null);
  const [fetchErr,    setFetchErr]    = useState(null);
  const [tab,         setTab]         = useState("adjudications");

  async function refresh() {
    const base = window.MCP_API_BASE || "http://127.0.0.1:8001";
    try {
      const [adjRes, hrRes, latRes] = await Promise.all([
        fetch(`${base}/observability/telemetry/adjudicated?limit=100`),
        fetch(`${base}/observability/telemetry/human-review`),
        fetch(`${base}/observability/telemetry/summary`),
      ]);
      if (adjRes.ok) { const d = await adjRes.json(); setAdjudicated(d.rows || []); }
      if (hrRes.ok)  { const d = await hrRes.json();  setHumanReview(d.rows || []); }
      if (latRes.ok) { const d = await latRes.json(); setLatency(d.rows || []); }
      setFetchErr(null);
      setLastRefresh(new Date());
    } catch (e) {
      setFetchErr(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, []);

  async function triggerProcess() {
    setTriggering(true);
    try {
      const base = window.MCP_API_BASE || "http://127.0.0.1:8001";
      await fetch(`${base}/observability/telemetry/process`, { method: "POST" });
      await refresh();
    } finally {
      setTriggering(false);
    }
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
    if (filter === "all")    return true;
    if (filter === "review") return r.requires_human_review;
    if (filter === "GITHUB" || filter === "MCP_PROXY") return (r.source_system || "MCP_PROXY") === filter;
    return r.risk_tier === filter;
  });

  return (
    <div data-screen-label="UBO Governance Brain" className="bb-panel" style={{height:"calc(100% + 40px)", overflow:"hidden"}}>
      <BBTermHeader
        section="UBO GOVERNANCE BRAIN"
        title="Medallion Pipeline · MCP Telemetry Adjudication"
        status={`${counts.total} ADJUDICATED  ·  ${counts.review} NEEDS HUMAN REVIEW  ·  BRONZE → SILVER → GOLD → COUNCIL`}
        actions={
          <div style={{display:"flex",gap:8,alignItems:"center"}}>
            {lastRefresh && (
              <span style={{fontSize:10,color:"var(--ink-3)",fontFamily:"'Geist Mono',monospace"}}>
                REFRESHED {lastRefresh.toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false})}
              </span>
            )}
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
      </div>

      {fetchErr && (
        <div style={{margin:"8px 18px",padding:"8px 12px",background:"var(--red-soft)",borderRadius:6,fontSize:11,color:"var(--red-ink)"}}>
          ⚠ API unavailable: {fetchErr} — ensure api_server.py is running
        </div>
      )}

      {/* ── Tab switcher ─────────────────────────────────────────────────── */}
      <div style={{padding:"0 18px"}}>
        <div className="cem-toolbar">
          <button className={"cem-filter" + (tab === "adjudications" ? " active" : "")} onClick={() => setTab("adjudications")}>Adjudications</button>
          <button className={"cem-filter" + (tab === "council" ? " active" : "")} onClick={() => setTab("council")}>Council Activity</button>
        </div>
      </div>

      <div className="cem-event-list">
        {tab === "adjudications" && (<>
          {humanReview.length > 0 && (
            <>
              <div className="bb-section-sep">
                <span style={{color:"var(--red-ink)"}}>⚠ HUMAN REVIEW QUEUE</span>
                <span>{humanReview.length} REQUIRING ATTENTION</span>
              </div>
              <div style={{padding:"0 18px 10px"}}>
                {humanReview.slice(0, 5).map((r, i) => (
                  <UBOReviewRow key={i} row={r} />
                ))}
                {humanReview.length > 5 && (
                  <div style={{fontSize:11,color:"var(--ink-3)",padding:"4px 0"}}>
                    + {humanReview.length - 5} more — set filter to "Needs Review" to see all
                  </div>
                )}
              </div>
            </>
          )}

          <div style={{padding:"0 18px"}}>
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
              ].map(f => (
                <button key={f.id} className={"cem-filter" + (filter === f.id ? " active" : "")} onClick={() => setFilter(f.id)}>
                  {f.l}{f.id === "review" && counts.review > 0 ? ` (${counts.review})` : ""}
                </button>
              ))}
            </div>
          </div>

          <div className="bb-section-sep">
            <span>ADJUDICATION LOG</span>
            <span>{filtered.length} EVENTS SHOWN</span>
          </div>

          {loading ? (
            <div style={{padding:"32px 18px",textAlign:"center",color:"var(--ink-3)",fontSize:12}}>
              <span className="spin"/> Loading UBO governance data…
            </div>
          ) : filtered.length === 0 ? (
            <Empty>
              {counts.total === 0
                ? "No adjudications yet. Click \"▶ PROCESS QUEUE\" to run the UBO pipeline against flagged MCP telemetry, or wait for the 30-second polling cycle."
                : "No events match this filter."}
            </Empty>
          ) : (
            <div style={{padding:"0 18px 18px"}}>
              {filtered.map((r, i) => (
                <UBOAdjRow
                  key={i}
                  row={r}
                  expanded={expanded.has(i)}
                  onToggle={() => {
                    const next = new Set(expanded);
                    next.has(i) ? next.delete(i) : next.add(i);
                    setExpanded(next);
                  }}
                />
              ))}
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
      </div>
    </div>
  );
}

function UBOReviewRow({ row }) {
  const ts = _UBO_TIER_STYLE[row.risk_tier] || _UBO_TIER_STYLE.LOW;
  return (
    <div className="ubo-review-row">
      <span className="ubo-tier-badge" style={{background:ts.bg,color:ts.ink}}>{row.risk_tier || "—"}</span>
      <span className="mono" style={{fontSize:11,flex:1,minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
        {row.target_tool || "unknown"}
      </span>
      <span style={{fontSize:10,color:"var(--ink-3)",flexShrink:0}}>{row.server_name}</span>
      <span className="mono" style={{fontSize:11,color:"var(--red-ink)",fontWeight:600,flexShrink:0}}>
        {row.risk_score != null ? row.risk_score.toFixed(3) : "—"}
      </span>
    </div>
  );
}

function UBOAdjRow({ row, expanded, onToggle }) {
  const tier    = row.risk_tier    || "LOW";
  const verdict = row.final_verdict || "CLEAR";
  const ts  = _UBO_TIER_STYLE[tier]       || _UBO_TIER_STYLE.LOW;
  const vs  = _UBO_VERDICT_STYLE[verdict] || _UBO_VERDICT_STYLE.CLEAR;
  const violations = row.policy_violations || [];
  const flags      = row.risk_flags        || [];
  const conflicts  = row.conflict_flags    || [];

  return (
    <div className={`ubo-adj-row${row.requires_human_review ? " needs-review" : ""}`}>
      <div className="ubo-adj-head" onClick={onToggle}>
        <span className="ubo-tier-badge"    style={{background:ts.bg, color:ts.ink}}>{tier}</span>
        <span className="ubo-verdict-badge" style={{background:vs.bg, color:vs.ink}}>{verdict}</span>
        {(row.source_system || "MCP_PROXY") === "GITHUB" && (
          <span style={{fontSize:9,fontWeight:700,padding:"2px 6px",borderRadius:4,background:"#1a1a2e",color:"#58a6ff",fontFamily:"Geist Mono,monospace",flexShrink:0,letterSpacing:".06em"}}>GH</span>
        )}
        <span className="mono ubo-tool-name">{row.target_tool || "unknown"}</span>
        <span style={{fontSize:10,color:"var(--ink-3)",flexShrink:0}}>{row.server_name}</span>
        {row.requires_human_review && <span className="ubo-review-flag">⚠ REVIEW</span>}
        <span className="mono" style={{fontSize:11,fontWeight:600,flexShrink:0}}>
          {row.risk_score != null ? row.risk_score.toFixed(3) : "—"}
        </span>
        <span className="mono ubo-ts">
          {row.adjudicated_at ? new Date(row.adjudicated_at).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit"}) : ""}
        </span>
        <Icon name={expanded ? "chev-u" : "chev-d"} size={13} className="muted"/>
      </div>
      {expanded && (
        <div className="ubo-adj-body">
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
};

function UBOCouncilRow({ row, expanded, onToggle }) {
  const tier    = row.risk_tier     || "LOW";
  const verdict = row.final_verdict || "CLEAR";
  const ts  = _UBO_TIER_STYLE[tier]       || _UBO_TIER_STYLE.LOW;
  const vs  = _UBO_VERDICT_STYLE[verdict] || _UBO_VERDICT_STYLE.CLEAR;
  const votes = row.council_votes || [];

  return (
    <div className="ubo-adj-row">
      <div className="ubo-adj-head" onClick={onToggle}>
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
      </div>
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

Object.assign(window, { CEMPanel, UBOGovPanel, TIERS, notifMsgFor });
