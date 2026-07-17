/* ============================================================
   Right rail — multi-tab live register (Pipeline screen, post-run)
   tabs: risks · heatmap · scenarios · loop
   RSS Signals, Forecasts, MAPs, and Risk Flow now live inside
   their respective pipeline stages (S1, S2, S4, S5).
   ============================================================ */

const RAIL_TABS = [
  { id: "rr",     l: "Risks" },
  { id: "hm",     l: "Heatmap" },
  { id: "loop",   l: "Loop" },
];

function Rail({
  activeTab, setActiveTab,
  output, risks, maps, loop, notifLog, forecasts, scenarios, greySwan, flowMeta,
  activeQuarter, setActiveQuarter,
  selectedRiskId, setSelectedRiskId,
  selectedPersona, setSelectedPersona,
  personas, onOpenMainFlow,
  periodBegin, periodEnd,
  // Risk Flow + Forecasts context (panels moved in from the old nav screens)
  objectives, gate2Reductions, appetiteThreshold,
  liveMode, livefacts, fredSeries, rssSignals, industry, ticker, loopStats, runId,
}) {
  return (
    <aside className="rsb" data-screen-label="Live register rail">
      <div className="rsb-head">
        <div className="rsb-title">
          <div className="t">Live Register</div>
          <span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{risks?.length || 0} risks · {maps?.length || 0} MAPs</span>
          {(periodBegin || periodEnd) && (
            <span className="mono" style={{fontSize: 10, color: "var(--ink-3)", marginTop: 2}}>
              {periodBegin || "—"} → {periodEnd || "—"}
            </span>
          )}
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
        {activeTab === "loop"  && <LoopTab      loop={loop} ticker={ticker} risks={risks} loopStats={loopStats} runId={runId}/>}
      </div>
    </aside>
  );
}

// ---------- RISKS ----------
function RiskTable({ risks, selectedId, onSelect }) {
  if (!risks?.length) return <Empty>Risks populate after Stage 2.</Empty>;
  // Shared scale across every row's trend sparkline, so a flat line reads
  // as genuinely flat and slope is comparable risk-to-risk instead of each
  // sparkline auto-scaling to its own (possibly tiny) range.
  const allHist = risks.flatMap(r => r.hist || []);
  const histMin = allHist.length ? Math.min(...allHist) : 0;
  const histMax = allHist.length ? Math.max(...allHist) : 1;
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
                <td><Sparkline data={r.hist} w={62} h={16} color={scoreColor(r.score)} min={histMin} max={histMax}/></td>
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
            {r.filingSnippet && (
              <div style={{background:"var(--blue-soft)", border:"1px solid var(--line)", borderRadius:7, padding:"8px 10px", marginBottom:10}}>
                <div style={{fontSize:10, fontWeight:600, letterSpacing:".05em", textTransform:"uppercase", color:"var(--blue-ink)", marginBottom:4}}>
                  10-K Item 1A · {r.filingDate || "Filing"}
                </div>
                <div style={{fontSize:11, color:"var(--ink-2)", lineHeight:1.6}}>{r.filingSnippet}</div>
              </div>
            )}
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
const LOOP_CADENCES = [
  { id: "monthly",   label: "Monthly",   cron: "0 8 1 * *",       desc: "1st of each month" },
  { id: "weekly",    label: "Weekly",    cron: "0 8 * * 1",       desc: "Every Monday 8am" },
  { id: "quarterly", label: "Quarterly", cron: "0 8 1 1,4,7,10 *", desc: "Jan / Apr / Jul / Oct" },
];

function LoopTab({ loop, ticker = "", risks = [], loopStats = {}, runId = null }) {
  const [schedOpen, setSchedOpen] = useState(false);
  const [cadence, setCadence]     = useState("monthly");
  const [copied, setCopied]       = useState(false);
  const [schedState, setSchedState] = useState({ loading: false, error: null, result: null });
  const [runNowState, setRunNowState] = useState({ loading: false, error: null, result: null });
  const [calibState, setCalibState] = useState({ loading: false, error: null, result: null });
  const [costData, setCostData]     = useState(null);
  const aiAvailableCalib = typeof window !== "undefined" && window.MCP?.aiLoopCalibrate;
  const canFetchCost = typeof window !== "undefined" && window.MCP?.fetchRunTokenCost;

  // Fetch session cost whenever runId becomes available
  React.useEffect(() => {
    if (!runId || !canFetchCost) return;
    window.MCP.fetchRunTokenCost(runId)
      .then(d => setCostData(d))
      .catch(() => {});
  }, [runId]);

  if (!loop || !loop.risk_reduction_pct) return <Empty>Loop calibration populates after Stage 6.</Empty>;

  const sel = LOOP_CADENCES.find(c => c.id === cadence);
  const focusText = loop.next_cycle_focus || "Re-run Dendrai risk loop, re-score all risks, flag velocity-3 breaches and RAG changes, post summary.";
  const schedCmd  = `/schedule "${focusText}" --cron "${sel.cron}"`;
  const mcpAvailable = typeof window !== "undefined" && window.MCP?.agentScheduleProvision;

  function copyCmd() {
    navigator.clipboard?.writeText(schedCmd).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  async function runCalibrate() {
    if (!aiAvailableCalib || !ticker) return;
    setCalibState({ loading: true, error: null, result: null });
    try {
      const overrideRate = loop.hitl_override_count != null && loop.hitl_total_count
        ? loop.hitl_override_count / loop.hitl_total_count : 0;
      const res = await window.MCP.aiLoopCalibrate(ticker, {
        loopStats: { ...loopStats, ...loop },
        risksFinal: risks,
        risksInitial: [],
        hitlOverrideRate: overrideRate,
        lessonsLearned: loop.lessons_learned || [],
      }, runId);
      setCalibState({ loading: false, error: null, result: res });
    } catch (e) {
      setCalibState({ loading: false, error: e.message || "AI unavailable", result: null });
    }
  }

  async function provisionAgent() {
    if (!mcpAvailable || !ticker) return;
    setSchedState({ loading: true, error: null, result: null });
    try {
      const res = await window.MCP.agentScheduleProvision(ticker, sel.cron);
      setSchedState({ loading: false, error: null, result: res });
    } catch (e) {
      setSchedState({ loading: false, error: e.message || "Provisioning failed", result: null });
    }
  }

  async function runNow() {
    if (!mcpAvailable || !ticker) return;
    setRunNowState({ loading: true, error: null, result: null });
    try {
      const res = await window.MCP.agentScheduleRunNow(ticker);
      setRunNowState({ loading: false, error: null, result: res });
    } catch (e) {
      setRunNowState({ loading: false, error: e.message || "Run trigger failed", result: null });
    }
  }

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

      {/* ── Loop Calibration AI Assist (Gate 3) ── */}
      {aiAvailableCalib && (
        <div style={{marginTop: 18, borderTop: "1px solid var(--line)", paddingTop: 14}}>
          <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom: calibState.result ? 12 : 0}}>
            <div className="sec-lbl" style={{marginBottom: 0}}>AI Calibration Assist</div>
            <button className="btn btn-sm" onClick={runCalibrate} disabled={calibState.loading || !ticker}>
              <Icon name="spark" size={10}/>
              {calibState.loading ? "Calibrating…" : calibState.result ? "Regenerate" : "Calibrate with AI"}
            </button>
          </div>
          {calibState.error && (
            <div className="mono" style={{fontSize:10.5, color:"var(--red-ink)", marginTop:6}}>{calibState.error}</div>
          )}
          {calibState.result && (
            <div style={{marginTop: 8}}>
              {calibState.result.summary && (
                <div style={{fontSize:11.5, color:"var(--ink-2)", lineHeight:1.55,
                  background:"var(--surface)", border:"1px solid var(--line)", borderRadius:6,
                  padding:"8px 12px", marginBottom:10}}>
                  {calibState.result.summary}
                </div>
              )}
              <div style={{display:"flex", gap:6, marginBottom:10, alignItems:"center"}}>
                <span className="mono" style={{fontSize:9.5, color:"var(--ink-4)"}}>RECOMMENDED FREQUENCY</span>
                <span className="mono" style={{fontSize:11, fontWeight:600, color:"var(--acc-ink)"}}>
                  {calibState.result.recommended_frequency?.toUpperCase()}
                </span>
              </div>
              {(calibState.result.next_cycle_focus_risks || []).length > 0 && (
                <>
                  <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)", marginBottom:4, letterSpacing:"0.06em"}}>NEXT CYCLE FOCUS</div>
                  {calibState.result.next_cycle_focus_risks.map((r, i) => (
                    <div key={i} style={{display:"flex", gap:8, padding:"5px 0", fontSize:11, color:"var(--ink-2)", borderBottom:"1px dashed var(--line)"}}>
                      <span className="mono" style={{color:"var(--acc-ink)", flexShrink:0}}>{r.risk_ref}</span>
                      <span>{r.reason}</span>
                    </div>
                  ))}
                </>
              )}
              {(calibState.result.tune_for_next_cycle || []).length > 0 && (
                <div style={{marginTop:10}}>
                  <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)", marginBottom:4, letterSpacing:"0.06em"}}>TUNE FOR NEXT CYCLE</div>
                  {calibState.result.tune_for_next_cycle.map((t, i) => (
                    <div key={i} style={{padding:"5px 0", borderBottom:"1px dashed var(--line)"}}>
                      <div style={{fontSize:11, fontWeight:600, color:"var(--ink-1)"}}>{t.area}</div>
                      <div style={{fontSize:11, color:"var(--ink-2)", marginTop:2}}>{t.recommendation}</div>
                    </div>
                  ))}
                </div>
              )}
              {(calibState.result.drift_indicators || []).length > 0 && (
                <div style={{marginTop:10}}>
                  <div className="mono" style={{fontSize:9.5, color:"var(--amber-ink)", marginBottom:4, letterSpacing:"0.06em"}}>DRIFT INDICATORS</div>
                  {calibState.result.drift_indicators.map((d, i) => (
                    <div key={i} style={{fontSize:11, color:"var(--amber-ink)", padding:"3px 0"}}>{d}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Session AI Cost ── */}
      {costData && (costData.total_cost_usd > 0 || costData.total_input_tokens > 0) && (
        <div style={{marginTop: 18, borderTop: "1px solid var(--line)", paddingTop: 14}}>
          <div className="sec-lbl">Session AI Cost</div>
          <div style={{display:"flex", gap:8, marginTop:8, marginBottom: costData.by_kind?.length ? 10 : 0, flexWrap:"wrap"}}>
            <div style={{background:"var(--surface-2)", border:"1px solid var(--line)", borderRadius:6, padding:"6px 10px", flex:1}}>
              <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)"}}>TOTAL COST</div>
              <div className="mono" style={{fontSize:13, fontWeight:600, color:"var(--acc-ink)", marginTop:2}}>
                ${costData.total_cost_usd < 0.01
                  ? costData.total_cost_usd.toFixed(5)
                  : costData.total_cost_usd.toFixed(4)}
              </div>
            </div>
            <div style={{background:"var(--surface-2)", border:"1px solid var(--line)", borderRadius:6, padding:"6px 10px", flex:1}}>
              <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)"}}>TOKENS IN / OUT</div>
              <div className="mono" style={{fontSize:11, fontWeight:600, color:"var(--ink-1)", marginTop:2}}>
                {(costData.total_input_tokens || 0).toLocaleString()} / {(costData.total_output_tokens || 0).toLocaleString()}
              </div>
            </div>
          </div>
          {(costData.by_kind || []).length > 0 && (
            <div>
              {costData.by_kind.map((k, i) => (
                <div key={i} style={{display:"flex", gap:6, padding:"4px 0", fontSize:10.5,
                  borderBottom:"1px dashed var(--line)", alignItems:"center"}}>
                  <span className="mono" style={{flex:1, color:"var(--ink-2)"}}>{k.kind}</span>
                  <span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>{k.calls} call{k.calls !== 1 ? "s" : ""}</span>
                  <span className="mono" style={{fontSize:10, color:"var(--ink-3)", minWidth:60, textAlign:"right"}}>
                    {k.cost_usd > 0 ? `$${k.cost_usd.toFixed(4)}` : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Schedule panel ── */}
      <div style={{marginTop: 18, borderTop: "1px solid var(--line)", paddingTop: 14}}>
        <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom: schedOpen ? 12 : 0}}>
          <div className="sec-lbl" style={{marginBottom: 0}}>Recurring Schedule</div>
          <button className={"btn btn-sm" + (schedOpen ? "" : " btn-ghost")} onClick={() => setSchedOpen(o => !o)}>
            <Icon name="bolt" size={11}/> {schedOpen ? "Close" : "Set up"}
          </button>
        </div>

        {schedOpen && (
          <div style={{background:"var(--surface-2)", border:"1px solid var(--line)", borderRadius: 9, padding: 12}}>

            {/* cadence selector */}
            <div style={{display:"flex", gap: 5, marginBottom: 10}}>
              {LOOP_CADENCES.map(c => (
                <button key={c.id}
                  className={"btn btn-sm" + (cadence === c.id ? "" : " btn-ghost")}
                  style={{flex: 1, fontSize: 10.5}}
                  onClick={() => setCadence(c.id)}>
                  {c.label}
                </button>
              ))}
            </div>

            <div className="mono" style={{fontSize: 10, color:"var(--ink-3)", marginBottom: 10}}>
              {sel.desc} · cron <span style={{color:"var(--acc-ink)"}}>{sel.cron}</span>
            </div>

            {/* focus preview */}
            <div style={{fontSize: 10.5, color:"var(--ink-2)", padding:"7px 9px",
              background:"var(--surface)", border:"1px solid var(--line)", borderRadius: 6,
              lineHeight: 1.55, marginBottom: 10}}>
              <span style={{fontSize: 10, color:"var(--ink-3)", display:"block", marginBottom: 3}}>FOCUS (auto-filled from loop output)</span>
              {focusText}
            </div>

            {/* Provision via MCP bridge (primary) or copy CLI command (fallback) */}
            {mcpAvailable ? (
              <>
                <div style={{display:"flex", gap: 6, marginBottom: 8}}>
                  <button className="btn btn-sm btn-primary" style={{flex: 1}}
                    onClick={provisionAgent} disabled={schedState.loading || !ticker}>
                    <Icon name="bolt" size={11}/>
                    {schedState.loading ? "Provisioning…" : schedState.result ? "Re-provision" : "Provision agent"}
                  </button>
                  {schedState.result && (
                    <button className="btn btn-sm" style={{flex: 1}}
                      onClick={runNow} disabled={runNowState.loading}>
                      <Icon name="spark" size={11}/>
                      {runNowState.loading ? "Triggering…" : "Run now"}
                    </button>
                  )}
                </div>
                {schedState.error && (
                  <div className="mono" style={{fontSize:10.5, color:"var(--red-ink)", marginBottom:8}}>{schedState.error}</div>
                )}
                {schedState.result && (
                  <div className="mono" style={{fontSize:10, color:"var(--green-ink)", marginBottom:8, lineHeight:1.55}}>
                    {schedState.result.status === "ok"
                      ? `Agent provisioned · deployment ${schedState.result.deployment_id?.slice(0, 12)}…`
                      : schedState.result.message || "Provisioned"}
                  </div>
                )}
                {runNowState.result && (
                  <div className="mono" style={{fontSize:10, color:"var(--acc-ink)", marginBottom:8}}>
                    Run triggered · session {runNowState.result.session_id?.slice(0, 16)}…
                  </div>
                )}
                {runNowState.error && (
                  <div className="mono" style={{fontSize:10.5, color:"var(--red-ink)", marginBottom:8}}>{runNowState.error}</div>
                )}
                <div style={{borderTop:"1px dashed var(--line)", paddingTop:8, marginTop:4}}>
                  <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)", marginBottom:6}}>OR PASTE INTO CLAUDE CODE TERMINAL</div>
                  <div className="mono" style={{fontSize: 9.5, color:"var(--ink-3)",
                    padding:"6px 9px", background:"var(--surface)", border:"1px solid var(--line)",
                    borderRadius: 6, wordBreak:"break-all", lineHeight: 1.65, marginBottom: 8}}>
                    {schedCmd}
                  </div>
                  <button className="btn btn-sm btn-ghost" style={{width:"100%"}} onClick={copyCmd}>
                    <Icon name={copied ? "check" : "download"} size={11}/>
                    {copied ? "Copied" : "Copy /schedule command"}
                  </button>
                </div>
              </>
            ) : (
              <>
                {/* generated command */}
                <div className="mono" style={{fontSize: 9.5, color:"var(--ink-3)",
                  padding:"6px 9px", background:"var(--surface)", border:"1px solid var(--line)",
                  borderRadius: 6, wordBreak:"break-all", lineHeight: 1.65, marginBottom: 10}}>
                  {schedCmd}
                </div>
                <button className="btn btn-sm" style={{width:"100%"}} onClick={copyCmd}>
                  <Icon name={copied ? "check" : "download"} size={11}/>
                  {copied ? "Copied to clipboard" : "Copy /schedule command"}
                </button>
                <div style={{fontSize: 10.5, color:"var(--ink-3)", marginTop: 10, lineHeight: 1.55}}>
                  Paste into the Claude Code terminal to register a recurring cloud agent that re-runs the loop automatically.
                </div>
              </>
            )}
          </div>
        )}
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

// ---------- RISK FLOW MINI ----------
function FlowMiniTab({ risks, maps, flowMeta, selectedId, onSelect, onOpenMain }) {
  if (!risks?.length || !flowMeta) return <Empty>Flow populates after Stage 2. The full chart lives in the Risk Flow main tab.</Empty>;

  const top = [...risks].sort((a,b) => b.score - a.score).slice(0, 6);
  const ragSoft = { R: "var(--red-soft)", A: "var(--amber-soft)", G: "var(--green-soft)" };
  const ragInk  = { R: "var(--red-ink)",  A: "var(--amber-ink)",  G: "var(--green-ink)" };
  const ragCol  = { R: "var(--red)",      A: "var(--amber)",      G: "var(--green)" };

  function audCounts(rid) {
    const meta = flowMeta[rid];
    const linked = (maps || []).filter(m => m.linked_risk === rid);
    const planned = Math.max(0, (meta?.audits?.length || 0) - linked.length);
    const open = linked.filter(m => (m.completion_pct || 0) < 100).length;
    const closed = linked.filter(m => (m.completion_pct || 0) >= 100).length;
    return { open, planned, closed };
  }

  return (
    <>
      <SectionLabel right={
        <button className="cfg-link" onClick={onOpenMain} type="button">
          Full view <Icon name="chev-r" size={10}/>
        </button>
      }>Risk Flow</SectionLabel>
      <div style={{fontSize: 11, color: "var(--ink-3)", marginBottom: 10, lineHeight: 1.5}}>
        Top risks and where they fan out. Click for full sankey + cadence in the main panel.
      </div>
      <div className="flow-mini-list">
        {top.map(r => {
          const meta = flowMeta[r.id];
          if (!meta) return null;
          const counts = audCounts(r.id);
          const isSel = selectedId === r.id;
          return (
            <div key={r.id} className={"flow-mini-card" + (isSel ? " active" : "")}
              onClick={() => onSelect(isSel ? null : r.id)}>
              <div className="flow-mini-head">
                <span className="rag-dot" style={{background: ragCol[r.rag]}}/>
                <div className="flow-mini-name">{r.name}</div>
                <span className="mono" style={{fontSize: 10, color: ragInk[r.rag]}}>{r.score.toFixed(1)}</span>
              </div>
              <div className="flow-mini-impact">
                {(meta.impacts || []).slice(0, 3).map(im => <span key={im} className="scen-pill">{im}</span>)}
                {(meta.impacts || []).length > 3 && <span className="scen-pill" style={{opacity: 0.7}}>+{(meta.impacts || []).length - 3}</span>}
              </div>
              <div className="flow-mini-foot">
                <span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>
                  {(meta.controls || []).length} control{(meta.controls || []).length === 1 ? "" : "s"} · {meta.cadence?.length || 0} checkpoint{(meta.cadence?.length || 0) === 1 ? "" : "s"}
                </span>
                <span className="flow-mini-aud">
                  {counts.open  > 0 && <span className="chip-pill" style={{background: "var(--amber-soft)", color: "var(--amber-ink)"}}>{counts.open} in flight</span>}
                  {counts.planned > 0 && <span className="chip-pill" style={{background: "var(--acc-soft)", color: "var(--acc-ink)"}}>{counts.planned} on plan</span>}
                  {counts.closed > 0 && <span className="chip-pill" style={{background: "var(--green-soft)", color: "var(--green-ink)"}}>{counts.closed} closed</span>}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

// ---------- PERSONA ----------
function PersonaTab({ personas, selected, setSelected, ticker, risks = [], loopStats = {}, runId }) {
  if (!personas) return <Empty>Persona reports populate after the loop completes.</Empty>;
  const names = Object.keys(personas);
  const cur = personas[selected];

  // #4 — AI-generated persona briefs replace the template when requested.
  const [aiBriefs, setAiBriefs] = useState({});   // { [personaName]: brief }
  const [ai, setAi] = useState({ loading: false, error: null });
  const aiAvailable = typeof window !== "undefined" && window.MCP?.aiPersonaBrief;
  const aiBrief = aiBriefs[selected];

  async function regenerate() {
    if (!aiAvailable) return;
    setAi({ loading: true, error: null });
    try {
      const res = await window.MCP.aiPersonaBrief(ticker || "", selected, risks, loopStats, runId || null);
      setAiBriefs(prev => ({ ...prev, [selected]: res }));
      setAi({ loading: false, error: null });
    } catch (e) {
      setAi({ loading: false, error: e.message || "AI unavailable" });
    }
  }

  return (
    <>
      <SectionLabel right={aiAvailable ? (
        <button className="btn btn-sm" onClick={regenerate} disabled={ai.loading}
          title="Generate a role-tailored brief with Claude">
          <Icon name="spark" size={10}/> {ai.loading ? "Generating…" : aiBrief ? "Regenerate" : "Generate with AI"}
        </button>
      ) : null}>Persona Report</SectionLabel>
      <div className="persona-pick">
        {names.map(n => (
          <button key={n} className={"pp" + (selected === n ? " active" : "")} onClick={() => setSelected(n)}>{n}</button>
        ))}
      </div>
      {ai.error && (
        <div className="mono" style={{fontSize: 10.5, color: "var(--red-ink)", margin: "4px 0"}}>
          AI brief unavailable: {ai.error}
        </div>
      )}

      {aiBrief ? (
        <>
          <div className="persona-card">
            <div className="kicker" style={{marginBottom: 6, color: "var(--acc-ink)"}}>Headline · AI-generated</div>
            <div className="persona-headline">{aiBrief.headline}</div>
          </div>
          {(aiBrief.sections || []).map((s, i) => (
            <div className="persona-card" key={i}>
              <div className="kicker" style={{marginBottom: 6}}>{s.title}</div>
              <div className="persona-summary">{s.body}</div>
            </div>
          ))}
          {(aiBrief.callouts || []).length > 0 && (
            <div className="persona-card">
              <div className="kicker" style={{marginBottom: 6}}>Callouts</div>
              <ul className="scen-list" style={{fontSize: 11.5}}>
                {aiBrief.callouts.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </div>
          )}
        </>
      ) : (
        <>
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
      )}
    </>
  );
}

// ---------- SCHEDULE BUILDER (extracted so Config screen can reuse it) ----------
function ScheduleBuilder({ focusText: focusTextProp }) {
  const [cadence, setCadence] = useState("monthly");
  const [copied, setCopied]   = useState(false);

  const sel = LOOP_CADENCES.find(c => c.id === cadence);
  const focusText = focusTextProp || "Re-run Dendrai risk loop, re-score all risks, flag velocity-3 breaches and RAG changes, post summary.";
  const schedCmd  = `/schedule "${focusText}" --cron "${sel.cron}"`;

  function copyCmd() {
    navigator.clipboard?.writeText(schedCmd).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div style={{background:"var(--surface-2)", border:"1px solid var(--line)", borderRadius: 9, padding: 12}}>
      <div style={{display:"flex", gap: 5, marginBottom: 10}}>
        {LOOP_CADENCES.map(c => (
          <button key={c.id}
            className={"btn btn-sm" + (cadence === c.id ? "" : " btn-ghost")}
            style={{flex: 1, fontSize: 10.5}}
            onClick={() => setCadence(c.id)}>
            {c.label}
          </button>
        ))}
      </div>
      <div className="mono" style={{fontSize: 10, color:"var(--ink-3)", marginBottom: 10}}>
        {sel.desc} · cron <span style={{color:"var(--acc-ink)"}}>{sel.cron}</span>
      </div>
      <div style={{fontSize: 10.5, color:"var(--ink-2)", padding:"7px 9px",
        background:"var(--surface)", border:"1px solid var(--line)", borderRadius: 6,
        lineHeight: 1.55, marginBottom: 10}}>
        <span style={{fontSize: 10, color:"var(--ink-3)", display:"block", marginBottom: 3}}>FOCUS (auto-filled from loop output)</span>
        {focusText}
      </div>
      <div className="mono" style={{fontSize: 9.5, color:"var(--ink-3)",
        padding:"6px 9px", background:"var(--surface)", border:"1px solid var(--line)",
        borderRadius: 6, wordBreak:"break-all", lineHeight: 1.65, marginBottom: 10}}>
        {schedCmd}
      </div>
      <button className="btn btn-sm" style={{width:"100%"}} onClick={copyCmd}>
        <Icon name={copied ? "check" : "download"} size={11}/>
        {copied ? "Copied to clipboard" : "Copy /schedule command"}
      </button>
      <div style={{fontSize: 10.5, color:"var(--ink-3)", marginTop: 10, lineHeight: 1.55}}>
        Paste into the Claude Code terminal to register a recurring cloud agent that re-runs the loop automatically.
      </div>
    </div>
  );
}

Object.assign(window, {
  Rail, RAIL_TABS,
  RiskTable, HeatmapTab, MapsTab, LoopTab, NotifTab, FlowMiniTab, PersonaTab,
  ScheduleBuilder,
});
