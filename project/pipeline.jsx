/* ============================================================
   Pipeline — 6 stages with HITL gates and animated run
   Stage statuses: idle / running / waiting / done
   Driven by parent (App): receives stageState, gateState, output.
   ============================================================ */

import { RiskApprovalReview } from "./risk-approval.jsx";
import { ScopeApprovalReview } from "./audit-scope-review.jsx";

const STAGES = [
  { id: "s1", name: "Signal Intake",                       desc: "10-K · peer filings · industry RSS · internal KRIs" },
  { id: "s2", name: "Risk Assessment + Velocity",          desc: "Continuous scoring · velocity delta · RAG matrix" },
  { id: "s3", name: "Audit Scope Generator",               desc: "Risk-linked audit plan · sprint-ready workplan" },
  { id: "s4", name: "Findings → Management Action Plans",  desc: "Root cause · owner · due date · success criteria" },
  { id: "s5", name: "Closure Evidence + Risk Reduction",   desc: "Quantified risk reduction · MAP completion" },
  { id: "s6", name: "Loop Calibration + Re-feed",          desc: "Updated register · velocity recalibration · lessons" },
];

const APPETITE_THRESHOLDS = { GREEN: 5.0, AMBER: 7.5, RED: 9.5 };

// Insert HITL gates after stages 2 and 3
function Pipeline({ stageState, output, openStages, setOpenStages, hitl, gateState, onApprove, onOverride, signals, livefacts,
                    liveRssSignals, rssLastUpdated, rssRefreshing,
                    appetiteLevel = "AMBER", appetiteThreshold,
                    perRiskAppetite, setPerRiskAppetite, allSignals, onRerunFromS3, onOpenAdjustRisk,
                    riskApprovals, onApproveRisk, onApproveAllRisks, onSignoffRisk,
                    scopeApprovals, onApproveObjective, onOpenAdjustObjective, onApproveAllObjectives, onSignoffObjective, onAddObjective,
                    manualAudits = [], onAddAudit, onRemoveAudit }) {
  const threshold = APPETITE_THRESHOLDS[appetiteLevel] ?? 7.5;
  const s2Extra = {
    liveRssSignals, rssLastUpdated, rssRefreshing,
    appetiteLevel, appetiteThreshold: threshold,
    perRiskAppetite: perRiskAppetite || {},
    setPerRiskAppetite,
    allSignals: allSignals || [],
    onOpenAdjustRisk,
    onRerunFromS3,
  };
  const s3Extra = {
    manualAudits,
    onAddAudit,
    onRemoveAudit,
    risks: output.s2?.risks || [],
  };
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
              s3Extra={s.id === "s3" ? s3Extra : null}
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
                    appetiteLevel={appetiteLevel}
                    appetiteThreshold={threshold}
                    perRiskAppetite={perRiskAppetite || {}}
                    onSetPerRiskAppetite={setPerRiskAppetite}
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
                    risks={output.s2?.risks || []}
                    onApproveObjective={onApproveObjective}
                    onOpenAdjust={onOpenAdjustObjective}
                    onApproveAll={onApproveAllObjectives}
                    onSignoff={onSignoffObjective}
                    onSubmit={() => onApprove(2)}
                    onOverrideGate={() => onOverride(2)}
                    onAddObjective={onAddObjective}
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

function Stage({ stage, status, isOpen, onToggle, output, signals, livefacts, s2Extra, s3Extra }) {
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
          <StageBody id={stage.id} status={status} output={output} signals={signals} livefacts={livefacts} s2Extra={s2Extra} s3Extra={s3Extra}/>
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
function StageBody({ id, status, output, signals, livefacts, s2Extra, s3Extra }) {
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
  if (id === "s3") return <S3Body output={output} {...(s3Extra || {})}/>;

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

function S2Body({ output, liveRssSignals = [], rssLastUpdated = null, rssRefreshing = false,
                  appetiteLevel = "AMBER", appetiteThreshold,
                  perRiskAppetite = {}, setPerRiskAppetite,
                  allSignals = [], onOpenAdjustRisk, onRerunFromS3 }) {
  const [expandedSigs, setExpandedSigs] = React.useState(new Set());
  const risks = output?.risks || [];
  const appetite = output?.riskAppetite;
  const APPETITE_THRESHOLDS = { GREEN: 5.0, AMBER: 7.5, RED: 9.5 };
  const overallThreshold = appetiteThreshold ?? APPETITE_THRESHOLDS[appetiteLevel] ?? 7.5;
  const counts = risks.reduce((acc, r) => { acc[r.rag] = (acc[r.rag] || 0) + 1; return acc; }, {});

  // Group live RSS signals by source for the status bar
  const rssByFeed = {};
  (liveRssSignals || []).forEach(s => {
    const key = s.feedName || s.feedId || "RSS";
    rssByFeed[key] = (rssByFeed[key] || 0) + 1;
  });
  const secsAgo = rssLastUpdated ? Math.round((Date.now() - rssLastUpdated) / 1000) : null;

  const fredContr = allSignals.filter(s => s.src === "FRED Macro" && s.delta === "contractionary");
  const highVelIndustry = liveRssSignals.filter(s => s.velocity >= 3).length;
  const industryAdj = Math.min(0.2, highVelIndustry * 0.05);

  const toggleSig = (id) => setExpandedSigs(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

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

      {/* Live RSS status bar */}
      <div className="s2-rss-bar">
        <span className="s2-rss-label">
          {rssRefreshing
            ? <><span className="spin" style={{width:10,height:10,borderWidth:1.5}}/> Ingesting…</>
            : <><span className="s2-rss-dot live"/>RSS live</>}
        </span>
        {liveRssSignals.length > 0 && (
          <span className="mono" style={{fontSize:10, color:"var(--ink-2)"}}>
            {liveRssSignals.length} signals
            {Object.entries(rssByFeed).map(([k,n]) => (
              <span key={k} style={{marginLeft:6, color:"var(--ink-3)"}}>{k}: {n}</span>
            ))}
          </span>
        )}
        <span className="mono" style={{fontSize:10, color:"var(--ink-4)", marginLeft:"auto"}}>
          {secsAgo != null ? `updated ${secsAgo < 60 ? `${secsAgo}s` : `${Math.round(secsAgo/60)}m`} ago · refreshes every 30s` : "not yet ingested"}
        </span>
      </div>

      <div className="stage-detail">
        <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:8}}>
          <h5 style={{margin:0}}>Risk scoring · signal evidence · tolerance</h5>
          <div style={{display:"flex", gap:8, alignItems:"center"}}>
            <span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>
              Overall: <span style={{color:`var(--${appetiteLevel.toLowerCase()}-ink)`, fontWeight:600}}>{appetiteLevel}</span> appetite
            </span>
            {onRerunFromS3 && (
              <button className="btn btn-sm" style={{fontSize:10, padding:"3px 10px"}} onClick={onRerunFromS3}>
                <Icon name="reset" size={10}/> Rerun Stages 3–6
              </button>
            )}
          </div>
        </div>

        <div className="s2-ctrl-list">
          {[...risks].sort((a,b) => b.score - a.score).map(r => {
            const perLevel = perRiskAppetite[r.id] || appetiteLevel;
            const perThreshold = APPETITE_THRESHOLDS[perLevel] ?? overallThreshold;
            const breachesAppetite = r.score >= perThreshold;

            // Signal evidence for this risk
            const rssLinked = liveRssSignals.filter(s => (s.affectedRisks || []).includes(r.id));
            const isMacro = (r.category || "").toLowerCase().includes("macro");
            const fredAdj = isMacro ? fredContr.length * 0.08 : 0;
            const rssAdj = rssLinked.reduce((sum,s) => sum + (s.velocity||0)*0.08, 0);
            const totalAdj = rssAdj + fredAdj + (industryAdj > 0 && rssLinked.length === 0 ? industryAdj : 0);
            const hasSigs = rssLinked.length > 0 || fredAdj > 0 || (highVelIndustry > 0 && industryAdj > 0);
            const sigOpen = expandedSigs.has(r.id);

            const controls = (MOCK.riskFlow?.[r.id]?.controls) || [];

            return (
              <div key={r.id} className={"s2-ctrl-risk" + (breachesAppetite ? " breach" : "")}>
                <div className="s2-ctrl-risk-head">
                  {/* Row 1: RAG dot + score + name + velocity */}
                  <div className="s2-ctrl-risk-title">
                    <span className={`rag-dot ${r.rag}`} style={{flexShrink:0}}/>
                    <span className="mono s2-ctrl-score" style={{color: scoreColorInk(r.score)}}>{fmt2(r.score)}</span>
                    <span className="s2-ctrl-risk-name">{r.name}</span>
                    <VelocityPill v={r.velocity}/>
                  </div>
                  {/* Row 2: appetite selector + breach flag + adjust */}
                  <div className="s2-ctrl-risk-actions">
                    <select className="s2-appetite-sel"
                      value={perLevel}
                      onChange={e => setPerRiskAppetite && setPerRiskAppetite(prev => ({...prev, [r.id]: e.target.value}))}
                      onClick={e => e.stopPropagation()}
                      style={{color: perLevel === "GREEN" ? "var(--green-ink)" : perLevel === "AMBER" ? "var(--amber-ink)" : "var(--red-ink)"}}>
                      <option value="GREEN">APT: G</option>
                      <option value="AMBER">APT: A</option>
                      <option value="RED">APT: R</option>
                    </select>
                    {breachesAppetite && (
                      <span className="mono" style={{fontSize:9, color:"var(--red-ink)", letterSpacing:"0.04em"}}>BREACH</span>
                    )}
                    {onOpenAdjustRisk && (
                      <button className="btn btn-sm" style={{padding:"2px 8px", fontSize:10}}
                        onClick={e => { e.stopPropagation(); onOpenAdjustRisk(r.id); }}>
                        Adjust
                      </button>
                    )}
                  </div>
                </div>

                {/* Signal evidence */}
                {hasSigs && (
                  <div className="s2-sig-ev">
                    <button className="s2-sig-ev-toggle" onClick={() => toggleSig(r.id)}>
                      {sigOpen ? "▲" : "▼"} {rssLinked.length + (fredAdj > 0 ? 1 : 0) + (industryAdj > 0 && rssLinked.length === 0 ? 1 : 0)} signal{(rssLinked.length + (fredAdj > 0 ? 1 : 0)) !== 1 ? "s" : ""} driving score
                      {totalAdj > 0.01 && (
                        <span className="s2-sig-ev-delta">+{totalAdj.toFixed(2)} adjustment</span>
                      )}
                    </button>
                    {sigOpen && (
                      <div className="s2-sig-ev-body">
                        {rssLinked.map((s,i) => (
                          <div key={i} className="s2-sig-ev-item">
                            <span className="s2-sig-ev-src">{s.feedName || "RSS"}</span>
                            <span style={{flex:1, fontSize:10.5, color:"var(--ink-2)"}}>{(s.title||"").slice(0,72)}{s.title?.length > 72 ? "…" : ""}</span>
                            <VelocityPill v={s.velocity}/>
                            <span className="s2-sig-ev-adj">+{(s.velocity*0.08).toFixed(2)}</span>
                          </div>
                        ))}
                        {fredAdj > 0 && (
                          <div className="s2-sig-ev-item">
                            <span className="s2-sig-ev-src">FRED Macro</span>
                            <span style={{flex:1, fontSize:10.5, color:"var(--ink-2)"}}>{fredContr.length} contractionary indicator{fredContr.length !== 1 ? "s" : ""}</span>
                            <span className="s2-sig-ev-adj">+{fredAdj.toFixed(2)}</span>
                          </div>
                        )}
                        {industryAdj > 0 && rssLinked.length === 0 && (
                          <div className="s2-sig-ev-item">
                            <span className="s2-sig-ev-src">Industry</span>
                            <span style={{flex:1, fontSize:10.5, color:"var(--ink-2)"}}>{highVelIndustry} high-velocity industry signal{highVelIndustry !== 1 ? "s" : ""}</span>
                            <span className="s2-sig-ev-adj">+{industryAdj.toFixed(2)}</span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Per-control tolerance */}
                {controls.length > 0 && (
                  <div className="s2-ctrl-detail">
                    {controls.map((ctrl,ci) => {
                      const adj = parseFloat((r.score + (CE_ADJ[ctrl.ce]||0)).toFixed(1));
                      const withinTol = adj < perThreshold;
                      return (
                        <div key={ci} className="s2-ctrl-row">
                          <span className={"s2-ctrl-dot " + (withinTol ? "ok" : "out")}/>
                          <span style={{flex:1, fontSize:11, color:"var(--ink-2)"}}>{ctrl.name}</span>
                          <span className="mono" style={{fontSize:10, color:"var(--ink-3)", marginRight:6}}>{ctrl.ce}</span>
                          <span className="mono" style={{fontSize:10, fontWeight:500, color: withinTol ? "var(--green-ink)" : "var(--red-ink)", minWidth:28, textAlign:"right"}}>
                            {adj.toFixed(1)}
                          </span>
                          <span className="mono" style={{fontSize:9, color: withinTol ? "var(--green-ink)" : "var(--red-ink)", marginLeft:4}}>
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

function S3Body({ output, manualAudits = [], onAddAudit, onRemoveAudit, risks = [] }) {
  const objs = output?.objectives || [];
  const p1 = objs.filter(o => o.priority === "P1").length;
  const p2 = objs.filter(o => o.priority === "P2").length;
  const totalHrs = objs.reduce((a, o) => a + (o.hours || 0), 0);
  const [modalOpen, setModalOpen] = React.useState(false);

  return (
    <div className="stage-body-grid">
      <div className="stage-stat-row">
        <Stat l="AI objectives" v={objs.length}/>
        <Stat l="P1 priority" v={p1} mono color="var(--red-ink)"/>
        <Stat l="P2 priority" v={p2} mono color="var(--amber-ink)"/>
        <Stat l="Effort (hrs)" v={totalHrs} mono/>
        <Stat l="Planned audits" v={manualAudits.length} mono color={manualAudits.length > 0 ? "var(--acc-ink)" : undefined}/>
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

      <div className="stage-detail">
        <div className="s3-audit-header">
          <h5 style={{margin: 0}}>
            Individual audits
            {manualAudits.length > 0 && <span className="mono muted" style={{fontWeight: 400, marginLeft: 6}}>({manualAudits.length})</span>}
          </h5>
          {onAddAudit && (
            <button className="btn btn-sm" onClick={() => setModalOpen(true)}>
              <Icon name="plus" size={10}/> Add Audit
            </button>
          )}
        </div>

        {manualAudits.length === 0 ? (
          <div className="s3-audit-empty">
            No individual audits planned. Use <strong>Add Audit</strong> to schedule targeted risk-reduction activities linked to specific risks.
          </div>
        ) : (
          <div className="s3-audit-list">
            {manualAudits.map(a => {
              const risk = risks.find(r => r.id === a.riskId);
              const ragColors = { R: "var(--red-ink)", A: "var(--amber-ink)", G: "var(--green-ink)" };
              return (
                <div key={a.id} className="s3-audit-item">
                  <div className="s3-audit-item-head">
                    <div className="s3-audit-item-title">{a.title}</div>
                    {onRemoveAudit && (
                      <button className="btn btn-sm btn-ghost s3-audit-remove" onClick={() => onRemoveAudit(a.id)}>
                        <Icon name="x" size={10}/>
                      </button>
                    )}
                  </div>
                  <div className="s3-audit-item-meta">
                    <span className="tag mono">{a.when}</span>
                    <span className="mono muted">Linked: {a.riskId}</span>
                    {risk && <span className="mono" style={{color: ragColors[risk.rag]}}>{fmt2(risk.score)} → {fmt2(a.residualScore)}</span>}
                    <span className="mono" style={{color: "var(--green-ink)"}}>−{a.reduction}% risk</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {modalOpen && onAddAudit && (
        <AddAuditModal
          risks={risks}
          onClose={() => setModalOpen(false)}
          onSubmit={(audit) => { onAddAudit(audit); setModalOpen(false); }}
        />
      )}
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

// ---- Add Audit Modal ----
const AUDIT_QUARTERS = ["Q3 2026","Q4 2026","Q1 2027","Q2 2027","Q3 2027","Q4 2027"];

function AddAuditModal({ risks, onClose, onSubmit }) {
  const [riskId, setRiskId] = React.useState("");
  const [when, setWhen] = React.useState("Q3 2026");
  const [title, setTitle] = React.useState("");
  const [reduction, setReduction] = React.useState(20);

  const selectedRisk = risks.find(r => r.id === riskId);
  const baseScore = selectedRisk?.score ?? 0;
  const residualScore = parseFloat(Math.max(0, baseScore * (1 - reduction / 100)).toFixed(1));

  const valid = riskId && title.trim().length >= 5;

  return (
    <div className="modal open" onClick={(e) => { if (e.target.classList.contains("modal")) onClose(); }}>
      <div className="modal-box" style={{width: 580}}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Add Individual Audit</div>
            <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", marginTop: 3}}>
              Schedule a targeted audit linked to a specific risk with anticipated reduction
            </div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
        </div>

        <div className="modal-body">
          <div className="ar-grid" style={{gridTemplateColumns: "1fr 1fr"}}>
            <div className="ar-field" style={{gridColumn: "1 / -1"}}>
              <label className="ar-label">Linked Risk</label>
              <select value={riskId} onChange={e => setRiskId(e.target.value)} className="s3-risk-sel">
                <option value="">— Select a risk —</option>
                {[...risks].sort((a,b) => b.score - a.score).map(r => (
                  <option key={r.id} value={r.id}>
                    {r.id} · {r.name} · score {fmt2(r.score)} ({r.rag})
                  </option>
                ))}
              </select>
            </div>

            <div className="ar-field" style={{gridColumn: "1 / -1"}}>
              <label className="ar-label">Audit Objective</label>
              <input type="text" value={title} onChange={e => setTitle(e.target.value)}
                className="fi-input"
                placeholder="e.g. Revenue recognition testing — controls walkthrough"/>
            </div>

            <div className="ar-field">
              <label className="ar-label">Scheduled Period</label>
              <div className="ar-ce-row" style={{flexWrap: "wrap", gap: 4}}>
                {AUDIT_QUARTERS.map(q => (
                  <button key={q} className={`ar-ce-opt ${when === q ? "active" : ""}`}
                    onClick={() => setWhen(q)}>{q}</button>
                ))}
              </div>
            </div>

            <div className="ar-field">
              <label className="ar-label">
                Anticipated Risk Reduction <span className="mono ar-val">{reduction}%</span>
              </label>
              <input type="range" min="5" max="80" step="5" value={reduction}
                onChange={e => setReduction(parseInt(e.target.value))} className="ar-slider"/>
              <div className="ar-orig mono">Applied to selected risk's current score</div>
            </div>
          </div>

          {selectedRisk && (
            <div className="s3-residual-preview">
              <div className="s3-res-label mono">Residual Risk Projection</div>
              <div className="s3-res-row">
                <div className="s3-res-item">
                  <div className="l">Current Score</div>
                  <div className="v mono" style={{color: scoreColorInk(baseScore)}}>{fmt2(baseScore)}</div>
                </div>
                <div className="s3-res-arrow">→</div>
                <div className="s3-res-item">
                  <div className="l">Residual Score</div>
                  <div className="v mono" style={{color: scoreColorInk(residualScore)}}>{fmt2(residualScore)}</div>
                </div>
                <div className="s3-res-item">
                  <div className="l">Score Reduction</div>
                  <div className="v mono" style={{color: "var(--green-ink)"}}>−{fmt2(baseScore - residualScore)}</div>
                </div>
                <div className="s3-res-item">
                  <div className="l">Control</div>
                  <div className="v mono">{selectedRisk.ce}</div>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="modal-foot">
          <span className="muted mono" style={{fontSize: 11}}>
            {!riskId ? "Select a risk to continue" : !valid ? "Audit objective must be at least 5 characters" : "Ready to add to plan"}
          </span>
          <div style={{display: "flex", gap: 6}}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!valid}
              onClick={() => onSubmit({
                id: `MA-${Date.now().toString(36).toUpperCase()}`,
                riskId,
                riskName: selectedRisk?.name || riskId,
                when,
                title: title.trim(),
                reduction,
                baseScore,
                residualScore,
              })}>
              <Icon name="plus" size={10}/> Add to Plan
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Pipeline, STAGES });
