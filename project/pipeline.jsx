/* ============================================================
   Pipeline — 6 stages with HITL gates and animated run
   Stage statuses: idle / running / waiting / done
   Driven by parent (App): receives stageState, gateState, output.
   ============================================================ */

const STAGES = [
  { id: "s1", name: "Signal Intake",                       desc: "10-K · peer filings · industry RSS · internal KRIs" },
  { id: "s2", name: "Risk Assessment + Velocity",          desc: "Continuous scoring · velocity delta · RAG matrix" },
  { id: "s3", name: "Audit Scope Generator",               desc: "Risk-linked audit plan · sprint-ready workplan" },
  { id: "s4", name: "Findings → Management Action Plans",  desc: "Root cause · owner · due date · success criteria" },
  { id: "s5", name: "Closure Evidence + Risk Reduction",   desc: "Quantified risk reduction · MAP completion" },
  { id: "s6", name: "Loop Calibration + Re-feed",          desc: "Updated register · velocity recalibration · lessons" },
];

// Insert HITL gates after stages 2 and 3
function Pipeline({ stageState, output, openStages, setOpenStages, hitl, gateState, onApprove, onOverride, signals, livefacts,
                    liveRssSignals, rssLastUpdated, rssRefreshing, appetiteThreshold,
                    riskApprovals, onApproveRisk, onOpenAdjustRisk, onApproveAllRisks, onSignoffRisk,
                    scopeApprovals, onApproveObjective, onOpenAdjustObjective, onApproveAllObjectives, onSignoffObjective }) {
  const s2Extra = { liveRssSignals, rssLastUpdated, rssRefreshing, appetiteThreshold };
  return (
    <div className="pipeline">
      {STAGES.map((s, i) => {
        const status = stageState[s.id] || "idle";
        const isOpen = openStages.has(s.id);
        const showGate = (i === 1 && hitl.risk && gateState.g1) || (i === 2 && hitl.scope && gateState.g2);
        const gateNum = i === 1 ? 1 : 2;
        return (
          <React.Fragment key={s.id}>
            <Stage
              stage={s}
              status={status}
              isOpen={isOpen}
              onToggle={() => {
                const next = new Set(openStages);
                next.has(s.id) ? next.delete(s.id) : next.add(s.id);
                setOpenStages(next);
              }}
              output={output[s.id]}
              signals={signals}
              livefacts={livefacts}
              s2Extra={s.id === "s2" ? s2Extra : null}
            />
            {i < STAGES.length - 1 && (
              <Connector active={status === "done" || status === "running"}/>
            )}
            {showGate && (
              <>
                {gateNum === 1 && gateState.g1 === "pending" ? (
                  <RiskApprovalReview
                    risks={output.s2?.risks || []}
                    approvals={riskApprovals}
                    appetiteThreshold={appetiteThreshold}
                    onApproveRisk={onApproveRisk}
                    onOpenAdjust={onOpenAdjustRisk}
                    onApproveAll={onApproveAllRisks}
                    onSignoff={onSignoffRisk}
                    onSubmit={() => onApprove(1)}
                    onOverrideGate={() => onOverride(1)}
                  />
                ) : gateNum === 2 && gateState.g2 === "pending" ? (
                  <ScopeApprovalReview
                    objectives={output.s3?.objectives || []}
                    approvals={scopeApprovals}
                    onApproveObjective={onApproveObjective}
                    onOpenAdjust={onOpenAdjustObjective}
                    onApproveAll={onApproveAllObjectives}
                    onSignoff={onSignoffObjective}
                    onSubmit={() => onApprove(2)}
                    onOverrideGate={() => onOverride(2)}
                  />
                ) : (
                  <HITLGate
                    num={gateNum}
                    state={gateState[`g${gateNum}`]}
                    onApprove={() => onApprove(gateNum)}
                    onOverride={() => onOverride(gateNum)}
                  />
                )}
                {i < STAGES.length - 1 && (
                  <Connector active={gateState[`g${gateNum}`] === "approved"}/>
                )}
              </>
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}

function Stage({ stage, status, isOpen, onToggle, output, signals, livefacts, s2Extra }) {
  const statusCls = status === "running" ? "running" : status === "done" ? "done" : "";
  const pill =
    status === "running" ? <span className="stage-pill run"><span className="dot"/>RUNNING</span> :
    status === "done"    ? <span className="stage-pill done"><span className="dot"/>COMPLETE</span> :
    status === "waiting" ? <span className="stage-pill wait"><span className="dot"/>AWAITING GATE</span> :
                           <span className="stage-pill"><span className="dot"/>IDLE</span>;
  const num = stage.id.replace("s", "");
  return (
    <div className={`stage ${statusCls}`} data-screen-label={`Stage ${num}`}>
      <div className="stage-head" onClick={onToggle}>
        <div className="stage-num">{num}</div>
        <div className="stage-meta">
          <div className="stage-name">{stage.name}</div>
          <div className="stage-desc">{stage.desc}</div>
        </div>
        {pill}
        <Icon name={isOpen ? "chev-u" : "chev-d"} size={14} className="muted"/>
      </div>
      {isOpen && (
        <div className="stage-body">
          <StageBody id={stage.id} status={status} output={output} signals={signals} livefacts={livefacts} s2Extra={s2Extra}/>
        </div>
      )}
    </div>
  );
}

function Connector({ active }) {
  return (
    <div className={"conn" + (active ? " on" : "")}>
      <div className="line"/>
    </div>
  );
}

function HITLGate({ num, state, onApprove, onOverride }) {
  const title = num === 1 ? "Human Review · Risk Assessment" : "Human Review · Audit Scope";
  const desc  = num === 1 ? "Validate AI risk scores before scoping audit." : "Confirm scope and resource allocation before fieldwork.";
  if (state === "approved" || state === "overridden") {
    const ink = state === "approved" ? "var(--green-ink)" : "var(--amber-ink)";
    const soft = state === "approved" ? "var(--green-soft)" : "var(--amber-soft)";
    const lbl = state === "approved" ? "APPROVED" : "OVERRIDDEN";
    return (
      <div className="stage" style={{background: soft, borderColor: `color-mix(in oklch, ${ink} 30%, var(--line))`, padding: "10px 18px", display: "flex", alignItems: "center", gap: 12}}>
        <div className="stage-num" style={{background: ink, color: "var(--surface)", border: "none"}}>
          <Icon name={state === "approved" ? "check" : "alert"} size={14}/>
        </div>
        <div className="stage-meta">
          <div className="stage-name" style={{color: ink}}>{title}</div>
          <div className="stage-desc" style={{color: ink, opacity: 0.85}}>{state === "approved" ? "Auditor confirmed AI output. Proceeding." : "Overridden with rationale captured in audit trail."}</div>
        </div>
        <span className="mono" style={{fontSize: 10, padding: "3px 9px", borderRadius: 999, background: `color-mix(in oklch, ${ink} 18%, transparent)`, color: ink, letterSpacing: ".05em"}}>{lbl}</span>
      </div>
    );
  }
  return (
    <div className="hgate">
      <div className="icon"><Icon name="alert" size={15}/></div>
      <div className="meta">
        <div className="t">{title}</div>
        <div className="d">{desc}</div>
      </div>
      <div className="actions">
        <button className="btn btn-sm approve" onClick={onApprove}><Icon name="check" size={11}/> Approve</button>
        <button className="btn btn-sm" onClick={onOverride}>Override</button>
      </div>
    </div>
  );
}

// ------ Stage body content ------
function StageBody({ id, status, output, signals, livefacts, s2Extra }) {
  if (status === "idle") {
    return <Empty>Awaiting run — toggle signal sources in the sidebar and press Run Loop.</Empty>;
  }
  if (status === "waiting") {
    return <Empty>Awaiting gate approval — review and confirm the previous stage before this one runs.</Empty>;
  }
  if (status === "running") {
    return (
      <div className="stage-detail">
        <span className="spin"/> Stage running… synthesizing structured output.
      </div>
    );
  }
  if (id === "s1") return <S1Body output={output} signals={signals} livefacts={livefacts}/>;
  if (id === "s2") return <S2Body output={output} {...(s2Extra || {})}/>;
  if (id === "s3") return <S3Body output={output}/>;
  if (id === "s4") return <S4Body output={output}/>;
  if (id === "s5") return <S5Body output={output}/>;
  if (id === "s6") return <S6Body output={output}/>;
  return null;
}

function S1Body({ output, signals, livefacts }) {
  const total = signals.length;
  const high = signals.filter(s => s.velocity >= 3).length;
  const med = signals.filter(s => s.velocity === 2).length;
  return (
    <div className="stage-body-grid">
      <div className="stage-stat-row">
        <Stat l="Signals ingested" v={total}/>
        <Stat l="High velocity" v={high} mono color="var(--red-ink)"/>
        <Stat l="Medium velocity" v={med} mono color="var(--amber-ink)"/>
        <Stat l="Sources" v={output?.sourceCount || 4}/>
      </div>
      {livefacts && (
        <div className="stage-detail">
          <h5>EDGAR · live extract</h5>
          <ul>
            <li><span className="tag mono">Entity</span> {livefacts.entity}</li>
            <li><span className="tag mono">CIK</span> {livefacts.cik}</li>
            {livefacts.revenue?.latestAnnual && <li><span className="tag mono">Revenue (FY)</span> {fmt$M(livefacts.revenue.latestAnnual.val)} <span className="muted" style={{marginLeft: 6}}>as of {livefacts.revenue.latestAnnual.end}</span></li>}
            {livefacts.netIncome?.latestAnnual && <li><span className="tag mono">Net Income</span> {fmt$M(livefacts.netIncome.latestAnnual.val)}</li>}
            {livefacts.grossMarginPct != null && <li><span className="tag mono">Gross Margin</span> {livefacts.grossMarginPct.toFixed(1)}%</li>}
            {livefacts.cash?.latestAnnual && <li><span className="tag mono">Cash & Equiv.</span> {fmt$M(livefacts.cash.latestAnnual.val)}</li>}
          </ul>
        </div>
      )}
      <div className="stage-detail">
        <h5>Top signals (by velocity)</h5>
        <ul>
          {signals.slice().sort((a,b) => b.velocity - a.velocity).slice(0, 6).map((s, i) => (
            <li key={i}>
              <span className="tag mono">{s.src}</span>
              <span style={{flex: 1}}>{s.label}</span>
              <VelocityPill v={s.velocity}/>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

const CE_ADJ = { STRONG: -0.7, ADEQUATE: -0.3, WEAK: 0.1, NONE: 0.4 };

function S2Body({ output, liveRssSignals = [], rssLastUpdated = null, rssRefreshing = false, appetiteThreshold }) {
  const risks = output?.risks || [];
  const appetite = output?.riskAppetite;
  const threshold = appetiteThreshold ?? appetite?.threshold ?? 7.0;
  const counts = risks.reduce((acc, r) => { acc[r.rag] = (acc[r.rag] || 0) + 1; return acc; }, {});
  const topRisks = [...risks].sort((a, b) => b.score - a.score).slice(0, 6);

  // Group live RSS signals by source
  const rssByFeed = {};
  (liveRssSignals || []).forEach(s => {
    const key = s.feedName || s.feedId || "RSS";
    rssByFeed[key] = (rssByFeed[key] || 0) + 1;
  });
  const secsAgo = rssLastUpdated ? Math.round((Date.now() - rssLastUpdated) / 1000) : null;
  return (
    <div className="stage-body-grid">
      <div className="stage-stat-row">
        <Stat l="Risks identified" v={risks.length}/>
        <Stat l="Red" v={counts.R || 0} mono color="var(--red-ink)"/>
        <Stat l="Amber" v={counts.A || 0} mono color="var(--amber-ink)"/>
        <Stat l="Green" v={counts.G || 0} mono color="var(--green-ink)"/>
        <Stat l="Appetite breach" v={appetite?.breaching?.length || 0} mono
              color={(appetite?.breaching?.length || 0) > 0 ? "var(--red-ink)" : "var(--green-ink)"}/>
      </div>
      {/* Live RSS ingestion status */}
      <div className="s2-rss-bar">
        <span className="s2-rss-label">
          {rssRefreshing
            ? <><span className="spin" style={{width:10,height:10,borderWidth:1.5}}/> Ingesting RSS…</>
            : <><span className="s2-rss-dot live"/>RSS live</>}
        </span>
        {liveRssSignals.length > 0 && (
          <span className="mono" style={{fontSize:10, color:"var(--ink-2)"}}>
            {liveRssSignals.length} signals
            {Object.entries(rssByFeed).map(([k, n]) => (
              <span key={k} style={{marginLeft:6, color:"var(--ink-3)"}}>{k}: {n}</span>
            ))}
          </span>
        )}
        <span className="mono" style={{fontSize:10, color:"var(--ink-4)", marginLeft:"auto"}}>
          {secsAgo != null ? `updated ${secsAgo < 60 ? `${secsAgo}s` : `${Math.round(secsAgo/60)}m`} ago · refreshes every 30s` : "not yet ingested"}
        </span>
      </div>

      <div className="stage-detail">
        <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom: 8}}>
          <h5 style={{margin:0}}>Control tolerance · per-control assessment</h5>
          <span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>appetite threshold ≥ {threshold}</span>
        </div>
        <div className="s2-ctrl-list">
          {topRisks.map(r => {
            const controls = (MOCK.riskFlow?.[r.id]?.controls) || [];
            const breachesAppetite = r.score >= threshold;
            return (
              <div key={r.id} className={"s2-ctrl-risk" + (breachesAppetite ? " breach" : "")}>
                <div className="s2-ctrl-risk-head">
                  <RAGChip rag={r.rag}>{fmt2(r.score)}</RAGChip>
                  <span style={{flex:1, fontWeight:500, fontSize:11.5}}>{r.name}</span>
                  <VelocityPill v={r.velocity}/>
                  {breachesAppetite && <span className="mono" style={{fontSize:9, color:"var(--red-ink)", letterSpacing:"0.05em"}}>APPETITE BREACH</span>}
                </div>
                {controls.length > 0 && (
                  <div className="s2-ctrl-detail">
                    {controls.map((ctrl, ci) => {
                      const adj = parseFloat((r.score + (CE_ADJ[ctrl.ce] || 0)).toFixed(1));
                      const withinTol = adj < threshold;
                      return (
                        <div key={ci} className="s2-ctrl-row">
                          <span className={"s2-ctrl-dot " + (withinTol ? "ok" : "out")}/>
                          <span style={{flex:1, fontSize:11, color:"var(--ink-2)"}}>{ctrl.name}</span>
                          <span className="mono" style={{fontSize:10, color:"var(--ink-3)", marginRight:6}}>{ctrl.ce}</span>
                          <span className="mono" style={{fontSize:10, fontWeight:500, color: withinTol ? "var(--green-ink)" : "var(--red-ink)", minWidth:28, textAlign:"right"}}>
                            {adj.toFixed(1)}
                          </span>
                          <span className="mono" style={{fontSize:9, color: withinTol ? "var(--green-ink)" : "var(--red-ink)", marginLeft:4, minWidth:32}}>
                            {withinTol ? "OK" : "BREACH"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function S3Body({ output }) {
  const objs = output?.objectives || [];
  const p1 = objs.filter(o => o.priority === "P1").length;
  const p2 = objs.filter(o => o.priority === "P2").length;
  const totalHrs = objs.reduce((a, o) => a + (o.hours || 0), 0);
  return (
    <div className="stage-body-grid">
      <div className="stage-stat-row">
        <Stat l="Audit objectives" v={objs.length}/>
        <Stat l="P1 priority" v={p1} mono color="var(--red-ink)"/>
        <Stat l="P2 priority" v={p2} mono color="var(--amber-ink)"/>
        <Stat l="Effort (hrs)" v={totalHrs} mono/>
      </div>
      <div className="stage-detail">
        <h5>Sprint-ready objectives</h5>
        <ul>
          {objs.map(o => (
            <li key={o.id}>
              <span className="tag mono">{o.priority}</span>
              <span style={{flex: 1}}>{o.objective}</span>
              <span className="mono muted" style={{fontSize: 10.5}}>{o.hours}h</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function S4Body({ output }) {
  const maps = output?.maps || [];
  const avgRed = maps.reduce((a, m) => a + (m.reduction_pct || 0), 0);
  return (
    <div className="stage-body-grid">
      <div className="stage-stat-row">
        <Stat l="MAPs generated" v={maps.length}/>
        <Stat l="High-impact (R)" v={maps.filter(m => m.risk_impact === "R").length} mono color="var(--red-ink)"/>
        <Stat l="Total reduction" v={`${avgRed}%`} mono/>
        <Stat l="Avg completion" v={`${Math.round(maps.reduce((a,m)=>a+(m.completion_pct||0),0) / Math.max(1,maps.length))}%`} mono/>
      </div>
      <div className="stage-detail">
        <h5>Action plans</h5>
        <ul>
          {maps.slice(0, 5).map(m => (
            <li key={m.id}>
              <span className="tag mono">{m.id}</span>
              <span style={{flex: 1}}>{m.finding}</span>
              <RAGChip rag={m.risk_impact}>{m.risk_impact}</RAGChip>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function S5Body({ output }) {
  const c = output?.closure || {};
  return (
    <div className="stage-body-grid">
      <div className="stage-stat-row">
        <Stat l="Risks closed" v={c.risks_closed || 0} mono color="var(--green-ink)"/>
        <Stat l="Risks reduced" v={c.risks_reduced || 0} mono color="var(--acc-ink)"/>
        <Stat l="Risks unchanged" v={c.risks_unchanged || 0} mono color="var(--ink-3)"/>
        <Stat l="Projected reduction" v={`${c.projected_total_risk_reduction_pct || 0}%`} mono/>
      </div>
      <div className="stage-detail">
        <h5>Evidence summary</h5>
        <ul>
          <li><span className="tag mono">Artifacts</span> <span style={{flex:1}}>Evidence files attached to MAP packages</span> <span className="mono">{c.evidence_artifacts || 0}</span></li>
          <li><span className="tag mono">Re-run</span> <span style={{flex:1}}>Risks flagged for next-cycle re-test</span> <span className="mono">{(c.rerun_recommended || []).join(", ")}</span></li>
        </ul>
      </div>
    </div>
  );
}

function S6Body({ output }) {
  const l = output?.loop || {};
  return (
    <div className="stage-body-grid">
      <div className="stage-stat-row">
        <Stat l="Loop health" v={l.loop_health || "—"} mono color="var(--amber-ink)"/>
        <Stat l="Audit impact" v={`${l.audit_impact_score || "—"}/10`} mono/>
        <Stat l="MAPs open" v={l.maps_open || 0} mono/>
        <Stat l="Next cycle" v={`${l.next_trigger_days || 0}d`} mono/>
      </div>
      <div className="stage-detail">
        <h5>Lessons learned</h5>
        <ul>
          {(l.lessons_learned || []).map((s, i) => (
            <li key={i}>
              <span className="tag mono">L{i+1}</span>
              <span>{s}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Stat({ l, v, mono, color }) {
  return (
    <div className="stage-stat">
      <div className="l">{l}</div>
      <div className={"v" + (mono ? " mono" : "")} style={color ? { color } : null}>{v}</div>
    </div>
  );
}

Object.assign(window, { Pipeline, STAGES });
