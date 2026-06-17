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
    <div data-screen-label="Control Event Monitor" className="bb-panel">
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

Object.assign(window, { CEMPanel, TIERS, notifMsgFor });
