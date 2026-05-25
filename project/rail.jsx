/* ============================================================
   Right rail — multi-tab live register
   tabs: risks · heatmap · maps · loop · notifs · forecast · persona
   ============================================================ */

const RAIL_TABS = [
  { id: "rr",     l: "Risks" },
  { id: "hm",     l: "Heatmap" },
  { id: "map",    l: "MAPs" },
  { id: "loop",   l: "Loop" },
  { id: "notif",  l: "Notifs" },
  { id: "flow",   l: "Flow" },
  { id: "pers",   l: "Persona" },
];

function Rail({
  activeTab, setActiveTab,
  output, risks, maps, loop, notifLog, forecasts, scenarios,
  activeQuarter, setActiveQuarter,
  selectedRiskId, setSelectedRiskId,
  selectedPersona, setSelectedPersona,
  personas,
}) {
  return (
    <aside className="rsb" data-screen-label="Live register rail">
      <div className="rsb-head">
        <div className="rsb-title">
          <div className="t">Live Register</div>
          <span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{risks?.length || 0} risks · {maps?.length || 0} MAPs</span>
        </div>
        <div className="rtab-bar">
          {RAIL_TABS.map(t => (
            <button key={t.id} className={"rtab" + (activeTab === t.id ? " active" : "")} onClick={() => setActiveTab(t.id)}>{t.l}</button>
          ))}
        </div>
      </div>
      <div className="rbody">
        {activeTab === "rr"    && <RiskTable    risks={risks} selectedId={selectedRiskId} onSelect={setSelectedRiskId}/>}
        {activeTab === "hm"    && <HeatmapTab   risks={risks} activeQ={activeQuarter} setActiveQ={setActiveQuarter} selectedId={selectedRiskId} onSelect={setSelectedRiskId}/>}
        {activeTab === "map"   && <MapsTab      maps={maps}/>}
        {activeTab === "loop"  && <LoopTab      loop={loop}/>}
        {activeTab === "notif" && <NotifTab     log={notifLog}/>}
        {activeTab === "flow"  && <FlowTab       risks={risks} maps={maps}/>}
        {activeTab === "pers"  && <PersonaTab   personas={personas} selected={selectedPersona} setSelected={setSelectedPersona}/>}
      </div>
    </aside>
  );
}

// ---------- RISKS ----------
function RiskTable({ risks, selectedId, onSelect }) {
  if (!risks?.length) return <Empty>Risks populate after Stage 2.</Empty>;
  return (
    <>
      <SectionLabel right={<span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>{risks.length} total</span>}>Risk Register</SectionLabel>
      <table className="rtable">
        <thead>
          <tr>
            <th style={{width: 8}}></th>
            <th>Risk</th>
            <th style={{width: 38}}>Now</th>
            <th style={{width: 36}}>Vel</th>
            <th style={{width: 70}}>Trend</th>
            <th style={{width: 70}}>Control</th>
          </tr>
        </thead>
        <tbody>
          {risks.map(r => {
            const isSel = selectedId === r.id;
            return (
              <tr key={r.id} onClick={() => onSelect(isSel ? null : r.id)} style={isSel ? {background: "var(--acc-soft)"} : null}>
                <td><span className={`rag-dot ${r.rag}`}/></td>
                <td className="risk-name">
                  <b>{r.name}</b>
                  <div className="cat">{r.id} · {r.category}</div>
                </td>
                <td><span className="mono" style={{color: scoreColorInk(r.score), fontWeight: 500}}>{fmt2(r.score)}</span></td>
                <td><VelocityPill v={r.velocity}/></td>
                <td><Sparkline data={r.hist} w={62} h={16} color={scoreColor(r.score)}/></td>
                <td><span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{r.ce}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {selectedId && (() => {
        const r = risks.find(x => x.id === selectedId);
        if (!r) return null;
        const qs = projectQuarters(r);
        return (
          <div className="mt-16" style={{background:"var(--surface-2)", border:"1px solid var(--line)", borderRadius: 10, padding: 14}}>
            <div style={{display:"flex", alignItems:"flex-start", justifyContent:"space-between", marginBottom: 8}}>
              <div>
                <div style={{fontSize: 12.5, fontWeight: 500}}>{r.name}</div>
                <div style={{fontSize: 10.5, color: "var(--ink-3)", marginTop: 2}}>{r.id} · {r.category} · {r.ce}</div>
              </div>
              <button className="btn btn-sm btn-ghost" onClick={() => onSelect(null)}><Icon name="x" size={11}/></button>
            </div>
            <div style={{fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.55, marginBottom: 10}}>{r.narrative}</div>
            <div className="sec-lbl" style={{marginBottom: 6}}>4-Quarter Projection</div>
            <div style={{display:"flex", gap: 4}}>
              {["Now", "Q1", "Q2", "Q3", "Q4"].map((q, i) => {
                const sc = i === 0 ? r.score : qs[i-1];
                return (
                  <div key={q} style={{flex: 1, background: "var(--surface)", borderRadius: 6, padding: "6px 4px", border: "1px solid var(--line)", textAlign:"center"}}>
                    <div className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{q}</div>
                    <div className="mono" style={{fontSize: 14, fontWeight: 500, color: scoreColorInk(sc), marginTop: 2}}>{fmt2(sc)}</div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}
    </>
  );
}

// ---------- HEATMAP ----------
function HeatmapTab({ risks, activeQ, setActiveQ, selectedId, onSelect }) {
  if (!risks?.length) return <Empty>Heatmap populates after Stage 2.</Empty>;
  return (
    <>
      <SectionLabel>Impact × Likelihood</SectionLabel>
      <div style={{fontSize: 11, color: "var(--ink-3)", marginBottom: 10}}>
        Click any bubble for velocity detail. Dashed circles show projected Q4 position.
      </div>
      <div className="qsel">
        {["Now", "Q1", "Q2", "Q3", "Q4"].map(q => (
          <button key={q} className={"qbtn" + (activeQ === q ? " active" : "")} onClick={() => setActiveQ(q)}>{q}</button>
        ))}
      </div>
      <div className="heat-wrap">
        <Heatmap risks={risks} activeQ={activeQ} selectedId={selectedId} onSelect={onSelect}/>
      </div>
      <div className="heat-legend">
        <span className="lg"><span className="rag-dot R"/> High (≥7.5)</span>
        <span className="lg"><span className="rag-dot A"/> Medium</span>
        <span className="lg"><span className="rag-dot G"/> Low</span>
      </div>
      {selectedId && (() => {
        const r = risks.find(x => x.id === selectedId);
        if (!r) return null;
        const qs = projectQuarters(r);
        const delta = qs[3] - r.score;
        return (
          <div className="mt-12" style={{background:"var(--surface-2)", border:"1px solid var(--line)", borderRadius: 10, padding: 12}}>
            <div style={{display:"flex", alignItems:"flex-start", justifyContent:"space-between"}}>
              <div>
                <div style={{fontSize: 12, fontWeight: 500}}>{r.name}</div>
                <div style={{fontSize: 10.5, color: "var(--ink-3)"}}>{r.id} · {r.category}</div>
              </div>
              <button className="btn btn-sm btn-ghost" onClick={() => onSelect(null)}><Icon name="x" size={11}/></button>
            </div>
            <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap: 6, marginTop: 8}}>
              <div className="scen-m"><div className="l">Velocity</div><div className="v" style={{color: r.velocity > 0 ? "var(--red-ink)" : r.velocity < 0 ? "var(--green-ink)" : "var(--ink-3)"}}>{r.velocity > 0 ? "+" : ""}{r.velocity}</div></div>
              <div className="scen-m"><div className="l">Q4 Δ</div><div className="v" style={{color: delta > 0.1 ? "var(--red-ink)" : delta < -0.1 ? "var(--green-ink)" : "var(--ink-3)"}}>{delta >= 0 ? "+" : ""}{fmt2(delta)}</div></div>
            </div>
          </div>
        );
      })()}
    </>
  );
}

// ---------- MAPs ----------
function MapsTab({ maps }) {
  if (!maps?.length) return <Empty>MAPs populate after Stage 4.</Empty>;
  return (
    <>
      <SectionLabel right={<span className="mono" style={{fontSize: 10, color:"var(--ink-3)"}}>{maps.length} total</span>}>Management Action Plans</SectionLabel>
      {maps.map(m => {
        const p = m.completion_pct || 0;
        const sc = p === 100 ? "done" : p > 0 ? "prog" : "open";
        const lbl = p === 100 ? "CLOSED" : p > 0 ? "IN PROG" : "OPEN";
        return (
          <div className="map-card" key={m.id}>
            <div className="top">
              <div style={{flex: 1, minWidth: 0}}>
                <div className="title">{m.finding}</div>
                <div className="meta-row">
                  <span>{m.owner}</span>
                  <span>· Due {m.due_date}</span>
                  <span>· {m.linked_risk}</span>
                </div>
              </div>
              <span className={`map-status ${sc}`}>{lbl}</span>
            </div>
            <div className="action">{m.action}</div>
            <div className="pbar"><div className={p < 60 ? "amber" : ""} style={{width: `${p}%`}}/></div>
            <div className="foot">
              <span>{m.id}</span>
              <span>{p}% complete · −{m.reduction_pct}% risk</span>
            </div>
          </div>
        );
      })}
    </>
  );
}

// ---------- LOOP ----------
function LoopTab({ loop }) {
  if (!loop || !loop.risk_reduction_pct) return <Empty>Loop calibration populates after Stage 6.</Empty>;
  return (
    <>
      <SectionLabel>Loop Calibration</SectionLabel>
      <div className="loop-grid">
        <div className="loop-stat">
          <div className="v" style={{color: "var(--green-ink)"}}>{loop.risk_reduction_pct}%</div>
          <div className="l">Risk reduction</div>
        </div>
        <div className="loop-stat">
          <div className="v" style={{color: "var(--amber-ink)"}}>{loop.maps_open}</div>
          <div className="l">MAPs open</div>
        </div>
        <div className="loop-stat">
          <div className="v">{loop.risks_closed}</div>
          <div className="l">Risks closed</div>
        </div>
        <div className="loop-stat">
          <div className="v">{loop.next_trigger_days}d</div>
          <div className="l">Next cycle</div>
        </div>
      </div>
      <div className="sec-lbl">Next-cycle focus</div>
      <div style={{fontSize: 12, color: "var(--ink-2)", lineHeight: 1.55, marginTop: 6, marginBottom: 14}}>{loop.next_cycle_focus}</div>
      <div className="sec-lbl">Lessons learned</div>
      <div className="mt-8">
        {(loop.lessons_learned || []).map((l, i) => (
          <div key={i} style={{display:"flex", gap: 8, padding: "6px 0", fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.5, borderBottom: "1px dashed var(--line)"}}>
            <span className="mono" style={{color: "var(--ink-3)", flexShrink: 0}}>L{i+1}</span>
            <span>{l}</span>
          </div>
        ))}
      </div>
    </>
  );
}

// ---------- NOTIFS ----------
function NotifTab({ log }) {
  if (!log?.length) return <Empty>No notifications yet. Fire a control event in the Control Event Monitor tab to populate this log.</Empty>;
  return (
    <>
      <SectionLabel right={<span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{log.length} sent</span>}>Notification Log</SectionLabel>
      {log.slice(0, 30).map((n, i) => (
        <div key={i} className="notif">
          <div className={"avatar " + (n.status === "ack" ? "ack" : "sent")}>{n.status === "ack" ? <Icon name="check" size={11}/> : "!"}</div>
          <div className="body">
            <div className="ttl">{n.tier}</div>
            <div className="msg">{n.msg}</div>
            <div className="ts">{n.status === "ack" ? "ACKNOWLEDGED" : "SENT"} · {new Date(n.sentAt).toLocaleTimeString("en-US", {hour:"2-digit",minute:"2-digit"})}</div>
          </div>
        </div>
      ))}
    </>
  );
}

// ---------- RISK FLOW (sankey) ----------
function FlowTab({ risks, maps = [] }) {
  if (!risks?.length) return <Empty>Flow populates after Stage 2. Shows how risks connect through severity, control maturity, and mitigation activities.</Empty>;

  // Derive mitigation status breakdown for the narrative
  const mapByRisk = {};
  (maps || []).forEach(m => {
    if (!m.linked_risk) return;
    const cur = mapByRisk[m.linked_risk];
    if (!cur || (m.completion_pct || 0) > (cur.completion_pct || 0)) mapByRisk[m.linked_risk] = m;
  });
  const mitCounts = { Open: 0, "In Progress": 0, Closed: 0, "No MAP": 0 };
  risks.forEach(r => {
    const m = mapByRisk[r.id];
    if (!m)                                mitCounts["No MAP"]++;
    else if ((m.completion_pct || 0) >= 100) mitCounts.Closed++;
    else if ((m.completion_pct || 0) > 0)   mitCounts["In Progress"]++;
    else                                     mitCounts.Open++;
  });

  return (
    <>
      <SectionLabel right={<span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{risks.length} risks · {maps.length} MAPs</span>}>
        Risk-to-Mitigation Flow
      </SectionLabel>
      <div style={{fontSize: 11, color: "var(--ink-3)", marginBottom: 8, lineHeight: 1.5}}>
        Traces each risk through Severity → Control Maturity → Mitigation. Hover any node to highlight connected paths and see the risks within.
      </div>
      <div style={{background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 10, padding: "6px 8px 4px"}}>
        <RiskFlowSankey risks={risks} maps={maps}/>
      </div>
      <div className="mt-12" style={{background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 10, padding: 12}}>
        <div className="sec-lbl" style={{marginBottom: 8}}>Mitigation coverage</div>
        <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6}}>
          {[
            { k: "Open",        color: "var(--red-ink)" },
            { k: "In Progress", color: "var(--amber-ink)" },
            { k: "Closed",      color: "var(--green-ink)" },
            { k: "No MAP",      color: "var(--ink-3)" },
          ].map(({ k, color }) => (
            <div key={k} className="scen-m">
              <div className="l">{k}</div>
              <div className="v" style={{ color }}>{mitCounts[k]}</div>
            </div>
          ))}
        </div>
        {mitCounts["No MAP"] > 0 && (
          <div style={{fontSize: 11, color: "var(--ink-2)", lineHeight: 1.5, marginTop: 8}}>
            <b style={{fontWeight: 500, color: "var(--amber-ink)"}}>{mitCounts["No MAP"]}</b> risk{mitCounts["No MAP"] !== 1 ? "s have" : " has"} no linked Mitigation Action Plan — consider raising MAPs to close the gap.
          </div>
        )}
      </div>
    </>
  );
}

// ---------- PERSONA ----------
function PersonaTab({ personas, selected, setSelected }) {
  if (!personas) return <Empty>Persona reports populate after the loop completes.</Empty>;
  const names = Object.keys(personas);
  const cur = personas[selected];
  return (
    <>
      <SectionLabel>Persona Report</SectionLabel>
      <div className="persona-pick">
        {names.map(n => (
          <button key={n} className={"pp" + (selected === n ? " active" : "")} onClick={() => setSelected(n)}>{n}</button>
        ))}
      </div>
      <div className="persona-card">
        <div className="kicker" style={{marginBottom: 6}}>Headline</div>
        <div className="persona-headline">{cur.headline}</div>
        <div className="persona-summary">{cur.summary}</div>
      </div>
      <div className="persona-card">
        <div className="kicker" style={{marginBottom: 6}}>Report sections</div>
        <ul className="scen-list" style={{fontSize: 11.5}}>
          {cur.sections.map((s, i) => <li key={i}>{s}</li>)}
        </ul>
      </div>
    </>
  );
}

Object.assign(window, { Rail, RAIL_TABS });
