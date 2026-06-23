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
                    liveRssSignals, rssLastUpdated, rssRefreshing, rssRunProgress, rssFeeds,
                    appetiteLevel = "AMBER", appetiteThreshold,
                    perRiskAppetite, setPerRiskAppetite, allSignals, onRerunFromS3, onOpenAdjustRisk,
                    riskApprovals, onApproveRisk, onApproveAllRisks, onSignoffRisk,
                    scopeApprovals, onApproveObjective, onOpenAdjustObjective, onApproveAllObjectives, onSignoffObjective, onAddObjective,
                    manualAudits = [], onAddAudit, onRemoveAudit,
                    narrativeResult, onNarrativeResult, forecasts, ticker: pipelineTicker = "" }) {
  const threshold = APPETITE_THRESHOLDS[appetiteLevel] ?? 7.5;
  const s1Extra = {
    rssRunProgress,
    rssFeeds: rssFeeds || [],
    ticker: pipelineTicker || output.s1?.ticker || "",
    narrativeResult,
    onNarrativeResult,
    forecasts,
  };
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
              forecasts={forecasts}
              s1Extra={s.id === "s1" ? s1Extra : null}
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

function Stage({ stage, status, isOpen, onToggle, output, signals, livefacts, s1Extra, s2Extra, s3Extra, forecasts }) {
  const statusCls = status === "running" ? "running" : status === "done" ? "done" : "";
  const pill =
    status === "running" ? <span className="stage-pill run"><span className="dot"/>RUNNING</span> :
    status === "done"    ? <span className="stage-pill done"><span className="dot"/>COMPLETE</span> :
    status === "waiting" ? <span className="stage-pill wait"><span className="dot"/>AWAITING GATE</span> :
                           <span className="stage-pill"><span className="dot"/>IDLE</span>;
  const num = stage.id.replace("s", "");
  const subSteps = status === "done"
    ? buildSubSteps(stage.id, output, signals, livefacts, s1Extra, s2Extra, s3Extra)
    : [];
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
      {subSteps.length > 0 && (
        <div style={{
          padding: "8px 16px 10px 16px",
          borderTop: "1px solid var(--line)",
          display: "flex", flexDirection: "column", gap: 4,
        }}>
          <div className="mono" style={{fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 2}}>COMPLETED STEPS</div>
          <div style={{display: "flex", flexWrap: "wrap", gap: "4px 20px"}}>
            {subSteps.map((step, i) => (
              <div key={i} style={{display: "flex", alignItems: "baseline", gap: 5, fontSize: 11, color: "var(--ink-2)"}}>
                <span style={{color: "var(--green-ink)", fontSize: 10, flexShrink: 0}}>✓</span>
                <span>{step}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {isOpen && (
        <div className="stage-body">
          <StageBody id={stage.id} status={status} output={output} signals={signals} livefacts={livefacts} s1Extra={s1Extra} s2Extra={s2Extra} s3Extra={s3Extra} forecasts={forecasts}/>
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

// ------ Compact sub-step builder (always-visible on completed stage cards) ------
function buildSubSteps(stageId, output, signals = [], livefacts, s1Extra, s2Extra, s3Extra) {
  if (!output) return [];

  if (stageId === "s1") {
    const bySrc = {};
    signals.forEach(s => { bySrc[s.src] = (bySrc[s.src] || 0) + 1; });
    const rssSigs = signals.filter(s => s.src === "Industry RSS");
    const rssByFeed = {};
    rssSigs.forEach(s => { const k = s.feedName || "RSS"; rssByFeed[k] = (rssByFeed[k] || 0) + 1; });
    const fredTotal = bySrc["FRED Macro"] || 0;
    const fredContr = signals.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").length;
    const total = signals.length;
    const high = signals.filter(s => s.velocity >= 3).length;
    const med  = signals.filter(s => s.velocity === 2).length;
    return [
      bySrc["EDGAR 10-K"] > 0 && `EDGAR 10-K loaded${livefacts ? ` · ${livefacts.entity} (CIK ${livefacts.cik})` : ` · ${bySrc["EDGAR 10-K"]} signals`}`,
      livefacts            && "Financial metrics mapped to risk score inputs",
      bySrc["Peer 10-K"] > 0 && `Peer 10-K parsed · ${bySrc["Peer 10-K"]} comparative signal${bySrc["Peer 10-K"] !== 1 ? "s" : ""}`,
      rssSigs.length > 0   && `Industry RSS · ${Object.keys(rssByFeed).length} feed${Object.keys(rssByFeed).length !== 1 ? "s" : ""} · ${rssSigs.length} article${rssSigs.length !== 1 ? "s" : ""} ingested`,
      fredTotal > 0        && `FRED macro · ${fredTotal} series · ${fredContr} contractionary signal${fredContr !== 1 ? "s" : ""} flagged`,
      bySrc["Internal KRI"] > 0 && `Internal KRIs · ${bySrc["Internal KRI"]} indicator${bySrc["Internal KRI"] !== 1 ? "s" : ""} assessed`,
      bySrc["Incident"] > 0     && `Incident log · ${bySrc["Incident"]} recent event${bySrc["Incident"] !== 1 ? "s" : ""} reviewed`,
      total > 0            && `${total} signals velocity-graded · ${high} high · ${med} medium · ${total - high - med} standard`,
      s1Extra?.narrativeResult && `AI narrative (Item 1A) · ${(s1Extra.narrativeResult.emerging_risks || []).length} emerging risk${(s1Extra.narrativeResult.emerging_risks || []).length !== 1 ? "s" : ""} detected`,
    ].filter(Boolean);
  }

  if (stageId === "s2") {
    const risks   = output?.risks || [];
    const counts  = risks.reduce((acc, r) => { acc[r.rag] = (acc[r.rag] || 0) + 1; return acc; }, {});
    const appetite = output?.riskAppetite;
    const allSigs  = s2Extra?.allSignals || [];
    const liveRss  = s2Extra?.liveRssSignals || [];
    const fredContr  = allSigs.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").length;
    const rssLinked  = liveRss.filter(s => (s.affectedRisks || []).length > 0).length;
    const highVel    = risks.filter(r => r.velocity >= 3).length;
    return [
      `${risks.length} risks loaded from industry template`,
      fredContr > 0   && `FRED macro adjustments applied · ${fredContr} contractionary signal${fredContr !== 1 ? "s" : ""}`,
      rssLinked > 0   && `RSS signal adjustments applied · ${rssLinked} linked signal${rssLinked !== 1 ? "s" : ""}`,
      "Control effectiveness (CE) scored per risk",
      `RAG matrix computed · ${counts.R || 0} RED · ${counts.A || 0} AMBER · ${counts.G || 0} GREEN`,
      highVel > 0     && `${highVel} high-velocity risk${highVel !== 1 ? "s" : ""} flagged for escalation`,
      appetite && (appetite.breaching?.length > 0
        ? `Appetite breached · ${appetite.breaching.length} risk${appetite.breaching.length !== 1 ? "s" : ""} exceed ${appetite.level} threshold`
        : `All risks within ${appetite.level} appetite tolerance`),
    ].filter(Boolean);
  }

  if (stageId === "s3") {
    const objs   = output?.objectives || [];
    const p1     = objs.filter(o => o.priority === "P1").length;
    const p2     = objs.filter(o => o.priority === "P2").length;
    const hrs    = objs.reduce((a, o) => a + (o.hours || 0), 0);
    const manual = s3Extra?.manualAudits || [];
    const risks  = s3Extra?.risks || [];
    const redAmber = risks.filter(r => r.rag === "R" || r.rag === "A").length;
    return [
      `${redAmber} RED/AMBER risk${redAmber !== 1 ? "s" : ""} mapped to audit objectives`,
      `${objs.length} audit objective${objs.length !== 1 ? "s" : ""} generated`,
      p1 > 0 && `${p1} P1 (immediate) · ${p2} P2 (planned) priority objectives`,
      hrs > 0 && `${hrs} total audit hours estimated across all objectives`,
      "Sprint-ready workplan built with linked risk IDs and control coverage",
      manual.length > 0 && `${manual.length} manual audit${manual.length !== 1 ? "s" : ""} incorporated into scope`,
    ].filter(Boolean);
  }

  if (stageId === "s4") {
    const maps   = output?.maps || [];
    const avgRed = maps.reduce((a, m) => a + (m.reduction_pct || 0), 0);
    const owners = new Set(maps.map(m => m.owner).filter(Boolean)).size;
    const redMaps = maps.filter(m => m.risk_impact === "R").length;
    return [
      `${maps.length} finding${maps.length !== 1 ? "s" : ""} linked to risks · ${redMaps} RED impact`,
      "Root cause analysis completed per finding",
      owners > 0 && `${owners} control owner${owners !== 1 ? "s" : ""} assigned`,
      "Due dates and milestones set — high-velocity items fast-tracked",
      "Success criteria defined with measurable closure conditions",
      avgRed > 0 && `${avgRed}% total risk reduction projected across all MAPs`,
    ].filter(Boolean);
  }

  if (stageId === "s5") {
    const c = output?.closure || {};
    const rerun = (c.rerun_recommended || []).length;
    return [
      c.evidence_artifacts > 0 && `${c.evidence_artifacts} evidence artifact${c.evidence_artifacts !== 1 ? "s" : ""} reviewed against MAP success criteria`,
      c.risks_closed > 0   && `${c.risks_closed} risk${c.risks_closed !== 1 ? "s" : ""} fully closed`,
      c.risks_reduced > 0  && `${c.risks_reduced} risk${c.risks_reduced !== 1 ? "s" : ""} partially mitigated`,
      c.risks_unchanged > 0 && `${c.risks_unchanged} risk${c.risks_unchanged !== 1 ? "s" : ""} unchanged — escalated for next cycle`,
      (c.projected_total_risk_reduction_pct || 0) > 0 && `${c.projected_total_risk_reduction_pct}% projected portfolio risk reduction`,
      rerun > 0 && `${rerun} risk${rerun !== 1 ? "s" : ""} queued for next-cycle re-test`,
    ].filter(Boolean);
  }

  if (stageId === "s6") {
    const l       = output?.loop || {};
    const lessons = (l.lessons_learned || []).length;
    return [
      l.loop_health && `Loop health: ${l.loop_health} · audit impact ${l.audit_impact_score || "—"}/25`,
      "Risk velocity weights recalibrated from this cycle's outcomes",
      lessons > 0  && `${lessons} lesson${lessons !== 1 ? "s" : ""} documented`,
      l.maps_open > 0 && `${l.maps_open} open MAP${l.maps_open !== 1 ? "s" : ""} carried forward`,
      l.next_trigger_days > 0 && `Next cycle in ${l.next_trigger_days} days${l.next_cycle_focus ? ` · focus: ${l.next_cycle_focus}` : ""}`,
      "Updated risk register and lessons fed back to Stage 1",
    ].filter(Boolean);
  }

  return [];
}

// ------ Stage body content ------
function StageBody({ id, status, output, signals, livefacts, s1Extra, s2Extra, s3Extra }) {
  const trace = output?.trace;
  if (status === "idle") {
    return <Empty>Awaiting run — toggle signal sources in the sidebar and press Run Loop.</Empty>;
  }
  if (status === "waiting") {
    return <Empty>Awaiting gate approval — review and confirm the previous stage before this one runs.</Empty>;
  }
  if (status === "running") {
    if (id === "s1" && s1Extra?.rssRunProgress) {
      return <S1RunningBody rssRunProgress={s1Extra.rssRunProgress} rssFeeds={s1Extra.rssFeeds || []}/>;
    }
    return (
      <div className="stage-detail">
        <span className="spin"/> Stage running… synthesizing structured output.
      </div>
    );
  }
  if (id === "s1") return <><S1Body output={output} signals={signals} livefacts={livefacts} ticker={s1Extra?.ticker || ""} narrativeResult={s1Extra?.narrativeResult} onNarrativeResult={s1Extra?.onNarrativeResult}/><StageTrace trace={trace}/></>;
  if (id === "s2") return <><S2Body output={output} {...(s2Extra || {})}/><StageTrace trace={trace}/></>;
  if (id === "s3") return <><S3Body output={output} {...(s3Extra || {})}/><StageTrace trace={trace}/></>;

  if (id === "s4") return <><S4Body output={output}/><StageTrace trace={trace}/></>;
  if (id === "s5") return <><S5Body output={output}/><StageTrace trace={trace}/></>;
  if (id === "s6") return <><S6Body output={output}/><StageTrace trace={trace}/></>;
  return null;
}

function StageTrace({ trace }) {
  if (!trace) return null;
  const renderList = (label, items) => {
    if (!items?.length) return null;
    return (
      <div className="stage-trace-block">
        <div className="stage-trace-label">{label}</div>
        <ul>
          {items.map((item, idx) => <li key={idx}>{item}</li>)}
        </ul>
      </div>
    );
  };

  return (
    <div className="stage-trace">
      <div className="stage-trace-title">Stage reasoning</div>
      {renderList("Assumptions", trace.assumptions)}
      {renderList("Decisions", trace.decisions)}
      {renderList("Obstacles", trace.obstacles)}
      {trace.conclusion ? <div className="stage-trace-summary"><strong>Conclusion:</strong> {trace.conclusion}</div> : null}
    </div>
  );
}

function SubStepList({ steps }) {
  if (!steps?.length) return null;
  return (
    <div style={{
      background: "var(--surface-2, var(--surface))",
      border: "1px solid var(--line)",
      borderRadius: 6,
      padding: "10px 14px",
      marginBottom: 10,
    }}>
      <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 8}}>PROCESS STEPS</div>
      {steps.map((step, i) => (
        <div key={i} style={{
          display: "flex", alignItems: "flex-start", gap: 8,
          paddingTop: i > 0 ? 7 : 0, marginTop: i > 0 ? 7 : 0,
          borderTop: i > 0 ? "1px solid var(--line)" : "none",
        }}>
          <span style={{color: "var(--green-ink)", fontSize: 11, flexShrink: 0, lineHeight: "16px"}}>✓</span>
          <div style={{flex: 1, minWidth: 0}}>
            <div style={{fontSize: 11, fontWeight: 500, color: "var(--ink)", lineHeight: 1.4}}>{step.label}</div>
            {step.detail && <div style={{fontSize: 10.5, color: "var(--ink-3)", marginTop: 1}}>{step.detail}</div>}
            {step.children?.length > 0 && (
              <div style={{marginTop: 5, paddingLeft: 10, borderLeft: "2px solid var(--line)", display: "flex", flexDirection: "column", gap: 2}}>
                {step.children.map((child, ci) => (
                  <div key={ci} style={{fontSize: 10, color: "var(--ink-2)", display: "flex", gap: 5}}>
                    <span style={{color: "var(--ink-4)", flexShrink: 0}}>└</span>
                    <span>{child}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function S1RunningBody({ rssRunProgress, rssFeeds }) {
  return (
    <div className="stage-detail">
      <div style={{display:"flex", alignItems:"center", gap:8, marginBottom:10}}>
        <span className="spin"/>
        <span style={{fontSize:11.5, color:"var(--ink-2)"}}>Stage 1 running — {rssRunProgress.msg}</span>
      </div>
      {rssFeeds.length > 0 && (
        <>
          <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)", marginBottom:5, letterSpacing:"0.06em"}}>RSS FEED INGESTION</div>
          <div style={{display:"flex", gap:5, flexWrap:"wrap"}}>
            {rssFeeds.map(f => {
              const done   = rssRunProgress.feedsDone.includes(f.id);
              const active = !done && rssRunProgress.msg.includes(f.name);
              return (
                <span key={f.id} className="mono" style={{
                  fontSize: 10, padding: "3px 8px", borderRadius: 4,
                  border: "1px solid var(--line)",
                  background: done   ? "var(--green-soft)"
                             : active ? "var(--amber-soft)"
                             : "var(--surface-2, var(--surface))",
                  color: done   ? "var(--green-ink)"
                       : active ? "var(--amber-ink)"
                       : "var(--ink-4)",
                  transition: "background 0.2s, color 0.2s",
                }}>
                  {f.name}{done ? " ✓" : active ? " ⟳" : ""}
                </span>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function S1Body({ output, signals, livefacts, ticker: tickerProp = "", narrativeResult, onNarrativeResult }) {
  const total = signals.length;
  const high = signals.filter(s => s.velocity >= 3).length;
  const med = signals.filter(s => s.velocity === 2).length;

  // ---- sub-steps ----
  const bySrc = {};
  signals.forEach(s => { bySrc[s.src] = (bySrc[s.src] || 0) + 1; });
  const rssSigs = signals.filter(s => s.src === "Industry RSS");
  const rssByFeed = {};
  rssSigs.forEach(s => { const k = s.feedName || "RSS"; rssByFeed[k] = (rssByFeed[k] || 0) + 1; });
  const fredTotal = bySrc["FRED Macro"] || 0;
  const fredContrS1 = signals.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").length;
  const s1Steps = [
    bySrc["EDGAR 10-K"] > 0 ? {
      label: "EDGAR 10-K filing loaded",
      detail: livefacts
        ? `${livefacts.entity} · CIK ${livefacts.cik} · ${bySrc["EDGAR 10-K"]} financial signal${bySrc["EDGAR 10-K"] !== 1 ? "s" : ""} extracted`
        : `${bySrc["EDGAR 10-K"]} financial signal${bySrc["EDGAR 10-K"] !== 1 ? "s" : ""} from annual filing`,
    } : null,
    livefacts ? {
      label: "Financial data mapped to risk pipeline",
      detail: "Revenue, net income, gross margin, and cash metrics incorporated as risk score inputs",
    } : null,
    bySrc["Peer 10-K"] > 0 ? {
      label: "Peer 10-K filings parsed",
      detail: `${bySrc["Peer 10-K"]} comparative signal${bySrc["Peer 10-K"] !== 1 ? "s" : ""} across industry peers`,
    } : null,
    rssSigs.length > 0 ? {
      label: "Industry RSS feeds ingested",
      detail: `${rssSigs.length} article${rssSigs.length !== 1 ? "s" : ""} velocity-graded across ${Object.keys(rssByFeed).length} feed${Object.keys(rssByFeed).length !== 1 ? "s" : ""}`,
      children: Object.entries(rssByFeed).map(([feed, count]) => `${feed} — ${count} article${count !== 1 ? "s" : ""}`),
    } : null,
    fredTotal > 0 ? {
      label: "FRED macro indicators ingested",
      detail: `${fredTotal} economic series · ${fredContrS1} contractionary signal${fredContrS1 !== 1 ? "s" : ""} flagged for risk adjustment`,
    } : null,
    bySrc["Internal KRI"] > 0 ? {
      label: "Internal KRI data reviewed",
      detail: `${bySrc["Internal KRI"]} key risk indicator${bySrc["Internal KRI"] !== 1 ? "s" : ""} assessed against control thresholds`,
    } : null,
    bySrc["Incident"] > 0 ? {
      label: "Incident log reviewed",
      detail: `${bySrc["Incident"]} recent incident${bySrc["Incident"] !== 1 ? "s" : ""} evaluated for risk linkage`,
    } : null,
    total > 0 ? {
      label: "Signal velocity graded",
      detail: `${high} high (v3) · ${med} medium (v2) · ${total - high - med} standard (v1) — scored on 1–3 scale`,
    } : null,
    narrativeResult ? {
      label: "AI narrative analysis applied",
      detail: `${(narrativeResult.emerging_risks || []).length} emerging risk${(narrativeResult.emerging_risks || []).length !== 1 ? "s" : ""} · ${(narrativeResult.yoy_changes || []).length} language shift${(narrativeResult.yoy_changes || []).length !== 1 ? "s" : ""} detected from Item 1A`,
    } : null,
  ].filter(Boolean);
  // ----

  const [narrLoading, setNarrLoading] = React.useState(false);
  const [narrError, setNarrError] = React.useState(null);
  const aiAvailable = typeof window !== "undefined" && window.MCP?.aiNarrative;
  const ticker = tickerProp || output?.ticker || livefacts?.entity || "";

  async function runNarrative() {
    if (!aiAvailable || !ticker) return;
    setNarrLoading(true);
    setNarrError(null);
    try {
      const res = await window.MCP.aiNarrative(ticker, null, { maxFilings: 2, includeProxy: true });
      onNarrativeResult?.(res);
    } catch (e) {
      setNarrError(e.message || "AI unavailable");
    } finally {
      setNarrLoading(false);
    }
  }

  const narrRisks = narrativeResult?.emerging_risks || [];
  const narrChanges = narrativeResult?.yoy_changes || [];

  return (
    <div className="stage-body-grid">
      <SubStepList steps={s1Steps}/>
      <div className="stage-stat-row">
        <Stat l="Signals ingested" v={total}/>
        <Stat l="High velocity" v={high} mono color="var(--red-ink)"/>
        <Stat l="Medium velocity" v={med} mono color="var(--amber-ink)"/>
        <Stat l="Sources" v={output?.sourceCount || 4}/>
        {narrRisks.length > 0 && <Stat l="Narrative risks" v={narrRisks.length} mono color="var(--acc-ink)"/>}
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

      {/* How signals feed into Stage 2 scoring */}
      {total > 0 && (
        <div className="stage-detail">
          <h5>How these signals adjust risk scores in Stage 2</h5>
          <div style={{display:"flex", flexDirection:"column", gap:6, marginTop:4}}>
            {signals.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").length > 0 && (
              <div style={{display:"flex", alignItems:"baseline", gap:8, fontSize:11}}>
                <span className="tag mono" style={{background:"var(--amber-soft)", color:"var(--amber-ink)", flexShrink:0}}>FRED</span>
                <span style={{color:"var(--ink-2)", flex:1}}>
                  {signals.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").length} contractionary signal{signals.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").length !== 1 ? "s" : ""}
                  {" "}→ <span className="mono">+{(signals.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").length * 0.08).toFixed(2)}</span> applied to macro-category risks
                </span>
              </div>
            )}
            {signals.filter(s => s.src === "Industry RSS").length > 0 && (
              <div style={{display:"flex", alignItems:"baseline", gap:8, fontSize:11}}>
                <span className="tag mono" style={{background:"var(--acc-soft,var(--surface))", color:"var(--acc-ink,var(--ink-2))", flexShrink:0}}>RSS</span>
                <span style={{color:"var(--ink-2)", flex:1}}>
                  Linked signals → <span className="mono">velocity × 0.08</span> added per directly linked risk · max velocity propagated
                </span>
              </div>
            )}
            {signals.filter(s => s.velocity >= 3).length > 0 && (
              <div style={{display:"flex", alignItems:"baseline", gap:8, fontSize:11}}>
                <span className="tag mono" style={{background:"var(--red-soft)", color:"var(--red-ink)", flexShrink:0}}>HIGH-VEL</span>
                <span style={{color:"var(--ink-2)", flex:1}}>
                  {signals.filter(s => s.velocity >= 3).length} high-velocity signal{signals.filter(s => s.velocity >= 3).length !== 1 ? "s" : ""}
                  {" "}→ systemic lift <span className="mono">+{Math.min(0.20, signals.filter(s => s.velocity >= 3).length * 0.05).toFixed(2)}</span> (capped at 0.20) applied to all risks
                </span>
              </div>
            )}
            <div style={{marginTop:2, padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
              <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>Stage 2 residual formula: </span>
              <span className="mono" style={{fontSize:10, color:"var(--ink-2)"}}>score = inherent + FRED_adj + RSS_adj + industry_adj − CE_discount</span>
            </div>
          </div>
        </div>
      )}

      {/* AI Narrative Analysis */}
      {aiAvailable && (
        <div className="stage-detail">
          <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom: narrRisks.length ? 10 : 0}}>
            <h5 style={{margin:0}}>Item 1A · AI narrative signals</h5>
            <button className="btn btn-sm" onClick={runNarrative} disabled={narrLoading || !ticker}>
              <Icon name="spark" size={10}/> {narrLoading ? "Extracting…" : narrativeResult ? "Re-extract" : "Extract narrative"}
            </button>
          </div>
          {narrError && (
            <div className="mono" style={{fontSize:10.5, color:"var(--red-ink)", marginTop:6}}>{narrError}</div>
          )}
          {narrativeResult?.summary && (
            <div style={{fontSize:11.5, color:"var(--ink-2)", lineHeight:1.55, marginTop:8, marginBottom:8,
              background:"var(--surface)", border:"1px solid var(--line)", borderRadius:6, padding:"8px 12px"}}>
              {narr.result.summary}
            </div>
          )}
          {narrRisks.length > 0 && (
            <ul>
              {narrRisks.map((r, i) => (
                <li key={i}>
                  <span className={`tag mono ${r.severity === "high" ? "sev-high" : r.severity === "medium" ? "sev-med" : ""}`}
                    style={{background: r.severity === "high" ? "var(--red-soft)" : r.severity === "medium" ? "var(--amber-soft)" : undefined,
                            color:      r.severity === "high" ? "var(--red-ink)"  : r.severity === "medium" ? "var(--amber-ink)"  : undefined}}>
                    {r.severity?.toUpperCase()}
                  </span>
                  <span style={{flex:1}}>{r.title}</span>
                  <span className="mono muted" style={{fontSize:9.5}}>{r.category}</span>
                </li>
              ))}
            </ul>
          )}
          {narrChanges.length > 0 && (
            <>
              <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)", letterSpacing:"0.06em", margin:"8px 0 4px"}}>YoY LANGUAGE SHIFTS</div>
              <ul>
                {narrChanges.map((c, i) => (
                  <li key={i}>
                    <span className="tag mono" style={{
                      background: c.direction === "expanded" ? "var(--red-soft)"   : c.direction === "new" ? "var(--amber-soft)" : "var(--surface-2)",
                      color:      c.direction === "expanded" ? "var(--red-ink)"    : c.direction === "new" ? "var(--amber-ink)"  : "var(--ink-3)",
                    }}>{c.direction}</span>
                    <span style={{flex:1, fontSize:11}}>{c.change}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}

const CE_ADJ  = { STRONG: -0.7, ADEQUATE: -0.3, WEAK: 0.1, NONE: 0.4 };
const CE_MULT = { STRONG: 0.80, ADEQUATE: 0.95, WEAK: 1.10, NONE: 1.20 };

function forecastRisk(risk, quarters = 4) {
  const mult = CE_MULT[risk.ce] ?? 1.0;
  return Array.from({ length: quarters }, (_, i) =>
    +Math.min(25, Math.max(0, risk.score + risk.velocity * mult * Math.pow(0.85, i))).toFixed(1)
  );
}

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

  // ---- sub-steps ----
  const fredContrS2 = allSignals.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").length;
  const rssLinkedCount = liveRssSignals.filter(s => (s.affectedRisks || []).length > 0).length;
  const highVelRisks = risks.filter(r => r.velocity >= 3).length;
  const s2Steps = [
    {
      label: "Base risk register loaded",
      detail: `${risks.length} risk${risks.length !== 1 ? "s" : ""} from industry template — inherent scores and control environment assigned`,
    },
    fredContrS2 > 0 ? {
      label: "FRED macro adjustments applied",
      detail: `${fredContrS2} contractionary indicator${fredContrS2 !== 1 ? "s" : ""} → +0.08 per signal to macro-category risk scores`,
    } : null,
    liveRssSignals.length > 0 ? {
      label: "Industry RSS signal adjustments applied",
      detail: `${rssLinkedCount} linked signal${rssLinkedCount !== 1 ? "s" : ""} · velocity × 0.08 added to directly affected risks`,
    } : null,
    {
      label: "Control effectiveness (CE) scored",
      detail: "STRONG / ADEQUATE / WEAK / NONE ratings applied — residual score adjusted per control environment",
    },
    {
      label: "RAG matrix computed",
      detail: `${counts.R || 0} RED · ${counts.A || 0} AMBER · ${counts.G || 0} GREEN — thresholds: RED ≥ 15, AMBER ≥ 9, GREEN < 9`,
    },
    {
      label: "Velocity deltas calculated",
      detail: `${highVelRisks} high-velocity risk${highVelRisks !== 1 ? "s" : ""} (v3) identified — RSS signal velocity overlaid on base velocity`,
    },
    {
      label: "Risk appetite breach check",
      detail: appetite?.breaching?.length > 0
        ? `${appetite.breaching.length} risk${appetite.breaching.length !== 1 ? "s" : ""} exceed ${appetiteLevel} threshold (≥${overallThreshold}) — HITL Gate 1 triggered`
        : `All risks within ${appetiteLevel} tolerance (threshold ≥${overallThreshold})`,
    },
  ].filter(Boolean);
  // ----

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
      <SubStepList steps={s2Steps}/>
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

      {/* Scoring methodology */}
      <div className="stage-detail">
        <h5>Scoring methodology</h5>
        <div style={{display:"flex", flexDirection:"column", gap:5, fontSize:11, color:"var(--ink-2)"}}>
          <div style={{padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>Residual score: </span>
            <span className="mono" style={{fontSize:10, color:"var(--ink-2)"}}>inherent + signal_adjustments + CE_adj</span>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)", marginLeft:12}}>CE adj: STRONG −0.7 · ADEQUATE −0.3 · WEAK +0.1 · NONE +0.4</span>
          </div>
          <div style={{padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>RAG thresholds: </span>
            <span className="mono" style={{fontSize:10, color:"var(--red-ink)"}}>RED ≥ 15</span>
            <span className="mono" style={{fontSize:10, color:"var(--amber-ink)", marginLeft:10}}>AMBER ≥ 9</span>
            <span className="mono" style={{fontSize:10, color:"var(--green-ink)", marginLeft:10}}>GREEN &lt; 9</span>
          </div>
        </div>
      </div>

      {/* Quarterly forecast for top risks */}
      {risks.length > 0 && (
        <div className="stage-detail">
          <h5>Quarterly forecast · velocity-dampened model</h5>
          <div style={{padding:"5px 10px 6px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)", marginBottom:8}}>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>Q_n = score + (velocity × CE_mult × 0.85^(n−1)) · capped at 25 · CE_mult: STRONG 0.80 · ADEQUATE 0.95 · WEAK 1.10 · NONE 1.20</span>
          </div>
          <div style={{overflowX:"auto"}}>
            <table style={{width:"100%", borderCollapse:"collapse", fontSize:11}}>
              <thead>
                <tr style={{borderBottom:"1px solid var(--line)"}}>
                  <th style={{textAlign:"left", padding:"4px 6px", color:"var(--ink-4)", fontSize:9.5, fontWeight:500, letterSpacing:"0.05em", fontFamily:"var(--mono)"}}>RISK</th>
                  <th style={{textAlign:"left", padding:"4px 6px", color:"var(--ink-4)", fontSize:9.5, fontWeight:500, letterSpacing:"0.05em", fontFamily:"var(--mono)"}}>CE</th>
                  <th style={{textAlign:"right", padding:"4px 6px", color:"var(--ink-4)", fontSize:9.5, fontWeight:500, letterSpacing:"0.05em", fontFamily:"var(--mono)"}}>NOW</th>
                  <th style={{textAlign:"right", padding:"4px 6px", color:"var(--ink-4)", fontSize:9.5, fontWeight:500, letterSpacing:"0.05em", fontFamily:"var(--mono)"}}>Q+1</th>
                  <th style={{textAlign:"right", padding:"4px 6px", color:"var(--ink-4)", fontSize:9.5, fontWeight:500, letterSpacing:"0.05em", fontFamily:"var(--mono)"}}>Q+2</th>
                  <th style={{textAlign:"right", padding:"4px 6px", color:"var(--ink-4)", fontSize:9.5, fontWeight:500, letterSpacing:"0.05em", fontFamily:"var(--mono)"}}>Q+3</th>
                  <th style={{textAlign:"right", padding:"4px 6px", color:"var(--ink-4)", fontSize:9.5, fontWeight:500, letterSpacing:"0.05em", fontFamily:"var(--mono)"}}>Q+4</th>
                  <th style={{textAlign:"right", padding:"4px 6px", color:"var(--ink-4)", fontSize:9.5, fontWeight:500, letterSpacing:"0.05em", fontFamily:"var(--mono)"}}>TREND</th>
                </tr>
              </thead>
              <tbody>
                {[...risks].sort((a,b) => b.score - a.score).slice(0,6).map(r => {
                  const qs = forecastRisk(r, 4);
                  const q4Rag = qs[3] >= 15 ? "R" : qs[3] >= 9 ? "A" : "G";
                  const ragInk = { R:"var(--red-ink)", A:"var(--amber-ink)", G:"var(--green-ink)" };
                  const rising = qs[3] > r.score + 0.2;
                  const falling = qs[3] < r.score - 0.2;
                  return (
                    <tr key={r.id} style={{borderBottom:"1px solid var(--line)", background: r.rag === "R" ? "color-mix(in oklch, var(--red-soft) 40%, transparent)" : "transparent"}}>
                      <td style={{padding:"5px 6px"}}>
                        <span className={`rag-dot ${r.rag}`} style={{marginRight:5}}/>
                        <span style={{fontSize:10.5, color:"var(--ink-2)", fontWeight:500}}>{r.name}</span>
                      </td>
                      <td style={{padding:"5px 6px"}}>
                        <span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>{r.ce}</span>
                      </td>
                      <td style={{padding:"5px 6px", textAlign:"right"}}>
                        <span className="mono" style={{fontSize:10.5, fontWeight:600, color:scoreColorInk(r.score)}}>{r.score.toFixed(1)}</span>
                      </td>
                      {qs.map((q,qi) => (
                        <td key={qi} style={{padding:"5px 6px", textAlign:"right"}}>
                          <span className="mono" style={{fontSize:10.5, color:scoreColorInk(q)}}>{q.toFixed(1)}</span>
                        </td>
                      ))}
                      <td style={{padding:"5px 6px", textAlign:"right"}}>
                        <span className="mono" style={{fontSize:11, color: rising ? "var(--red-ink)" : falling ? "var(--green-ink)" : "var(--ink-4)"}}>
                          {rising ? "↑" : falling ? "↓" : "→"}
                          {" "}
                          <span style={{color:ragInk[q4Rag], fontSize:9}}>{q4Rag}</span>
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)", marginTop:6}}>
            Forecast used by Stage 3 to prioritize sprint allocation · high-trajectory risks earn P1 objectives regardless of current RAG
          </div>
        </div>
      )}
    </div>
  );
}

function S3Body({ output, manualAudits = [], onAddAudit, onRemoveAudit, risks = [] }) {
  const objs = output?.objectives || [];
  const p1 = objs.filter(o => o.priority === "P1").length;
  const p2 = objs.filter(o => o.priority === "P2").length;
  const totalHrs = objs.reduce((a, o) => a + (o.hours || 0), 0);
  const [modalOpen, setModalOpen] = React.useState(false);

  // ---- sub-steps ----
  const redAmberRisks = risks.filter(r => r.rag === "R" || r.rag === "A").length;
  const s3Steps = [
    {
      label: "High-priority risks mapped to audit objectives",
      detail: `${redAmberRisks} RED/AMBER risk${redAmberRisks !== 1 ? "s" : ""} from Stage 2 drove objective generation`,
    },
    objs.length > 0 ? {
      label: "Audit objectives generated",
      detail: `${objs.length} objective${objs.length !== 1 ? "s" : ""} created — each linked to specific risk IDs and control gaps`,
    } : null,
    p1 > 0 ? {
      label: "P1 priority assigned",
      detail: `${p1} immediate-remediation objective${p1 !== 1 ? "s" : ""} — targeting RED risks and appetite breaches`,
    } : null,
    p2 > 0 ? {
      label: "P2 priority assigned",
      detail: `${p2} planned-review objective${p2 !== 1 ? "s" : ""} — covering residual AMBER risk exposure`,
    } : null,
    totalHrs > 0 ? {
      label: "Effort hours estimated",
      detail: `${totalHrs} total audit hours — allocated proportionally to risk score and velocity`,
    } : null,
    {
      label: "Sprint-ready workplan built",
      detail: "Objectives structured for sprint execution with defined scope, linked risks, and expected control coverage",
    },
    manualAudits.length > 0 ? {
      label: "Manual audits incorporated",
      detail: `${manualAudits.length} planned audit${manualAudits.length !== 1 ? "s" : ""} added to scope`,
      children: manualAudits.map(a => `${a.title} — ${a.when} · linked ${a.riskId} · −${a.reduction}% risk`),
    } : null,
  ].filter(Boolean);
  // ----

  return (
    <div className="stage-body-grid">
      <SubStepList steps={s3Steps}/>
      <div className="stage-stat-row">
        <Stat l="AI objectives" v={objs.length}/>
        <Stat l="P1 priority" v={p1} mono color="var(--red-ink)"/>
        <Stat l="P2 priority" v={p2} mono color="var(--amber-ink)"/>
        <Stat l="Effort (hrs)" v={totalHrs} mono/>
        <Stat l="Planned audits" v={manualAudits.length} mono color={manualAudits.length > 0 ? "var(--acc-ink)" : undefined}/>
      </div>

      {/* Scoping derivation logic */}
      <div className="stage-detail">
        <h5>How risks drive scope and hours</h5>
        <div style={{display:"flex", flexDirection:"column", gap:5, fontSize:11}}>
          <div style={{padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>Priority rules: </span>
            <span className="mono" style={{fontSize:10, color:"var(--red-ink)"}}>RED ≥ 15 → P1</span>
            <span className="mono" style={{fontSize:10, color:"var(--ink-3)", margin:"0 6px"}}>·</span>
            <span className="mono" style={{fontSize:10, color:"var(--amber-ink)"}}>AMBER 9–14 or velocity ≥ 3 → P1</span>
            <span className="mono" style={{fontSize:10, color:"var(--ink-3)", margin:"0 6px"}}>·</span>
            <span className="mono" style={{fontSize:10, color:"var(--green-ink)"}}>remaining AMBER → P2</span>
          </div>
          <div style={{padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>Hours formula: </span>
            <span className="mono" style={{fontSize:10, color:"var(--ink-2)"}}>P1 base 80h · P2 base 40h · scaled proportionally by residual risk score</span>
          </div>
          {risks.length > 0 && (
            <div style={{marginTop:2}}>
              {[...risks].filter(r => r.rag === "R" || r.rag === "A").sort((a,b) => b.score - a.score).slice(0,5).map(r => {
                const linked = objs.filter(o => o.linked_risk === r.id || (o.linked_risks||[]).includes(r.id));
                return (
                  <div key={r.id} style={{display:"flex", alignItems:"center", gap:6, padding:"3px 0", borderBottom:"1px solid var(--line)"}}>
                    <span className={`rag-dot ${r.rag}`}/>
                    <span className="mono" style={{fontSize:10, color:"var(--ink-3)", minWidth:48}}>{r.id}</span>
                    <span style={{flex:1, fontSize:10.5, color:"var(--ink-2)"}}>{r.name}</span>
                    <span className="mono" style={{fontSize:10, color:scoreColorInk(r.score), minWidth:28}}>{r.score.toFixed(1)}</span>
                    <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>→ {linked.length} obj{linked.length !== 1 ? "s" : ""}</span>
                    <span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>{linked.reduce((a,o)=>a+(o.hours||0),0)}h</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
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

  // ---- sub-steps ----
  const redMaps = maps.filter(m => m.risk_impact === "R").length;
  const amberMaps = maps.filter(m => m.risk_impact === "A").length;
  const uniqueOwners = new Set(maps.map(m => m.owner).filter(Boolean)).size;
  const avgCompletion = Math.round(maps.reduce((a,m) => a + (m.completion_pct || 0), 0) / Math.max(1, maps.length));
  const avgPerMap = maps.length > 0 ? Math.round(avgRed / maps.length) : 0;
  const s4Steps = [
    {
      label: "Audit findings linked to risks",
      detail: `${maps.length} finding${maps.length !== 1 ? "s" : ""} generated — ${redMaps} RED, ${amberMaps} AMBER impact`,
    },
    {
      label: "Root cause analysis completed",
      detail: "Contributing factors identified per finding — systemic, process, and control gaps classified",
    },
    uniqueOwners > 0 ? {
      label: "Control owners assigned",
      detail: `${uniqueOwners} distinct owner${uniqueOwners !== 1 ? "s" : ""} accountable across ${maps.length} action plan${maps.length !== 1 ? "s" : ""}`,
    } : null,
    {
      label: "Due dates and milestones set",
      detail: "Timelines calibrated to risk velocity and priority — high-velocity items fast-tracked",
    },
    {
      label: "Success criteria defined",
      detail: "Measurable closure conditions specified per MAP — quantified risk reduction targets set",
    },
    avgRed > 0 ? {
      label: "Risk reduction projected",
      detail: `${avgRed}% total expected reduction across all MAPs · avg ${avgPerMap}% per finding`,
    } : null,
    avgCompletion > 0 ? {
      label: "Completion baseline established",
      detail: `${avgCompletion}% average MAP completion at time of run`,
    } : null,
  ].filter(Boolean);
  // ----

  return (
    <div className="stage-body-grid">
      <SubStepList steps={s4Steps}/>
      <div className="stage-stat-row">
        <Stat l="MAPs generated" v={maps.length}/>
        <Stat l="High-impact (R)" v={maps.filter(m => m.risk_impact === "R").length} mono color="var(--red-ink)"/>
        <Stat l="Total reduction" v={`${avgRed}%`} mono/>
        <Stat l="Avg completion" v={`${Math.round(maps.reduce((a,m)=>a+(m.completion_pct||0),0) / Math.max(1,maps.length))}%`} mono/>
      </div>
      {/* Risk reduction math */}
      {maps.length > 0 && (
        <div className="stage-detail">
          <h5>Projected risk reduction per MAP</h5>
          <div style={{padding:"5px 10px 6px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)", marginBottom:8}}>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>Residual = current_score × (1 − reduction_pct / 100) · aggregated across all MAPs for portfolio reduction</span>
          </div>
          <div style={{display:"flex", flexDirection:"column", gap:3}}>
            {maps.slice(0, 6).map(m => {
              const pct = m.reduction_pct || 0;
              const barW = Math.round(pct * 1.25);
              return (
                <div key={m.id} style={{display:"flex", alignItems:"center", gap:8, padding:"4px 0", borderBottom:"1px solid var(--line)"}}>
                  <span className="mono" style={{fontSize:9.5, color:"var(--ink-4)", minWidth:52}}>{m.id}</span>
                  <RAGChip rag={m.risk_impact}>{m.risk_impact}</RAGChip>
                  <span style={{flex:1, fontSize:10.5, color:"var(--ink-2)", minWidth:0, overflow:"hidden", whiteSpace:"nowrap", textOverflow:"ellipsis"}}>{m.finding}</span>
                  <div style={{display:"flex", alignItems:"center", gap:5, flexShrink:0}}>
                    <div style={{width:70, height:5, background:"var(--line)", borderRadius:3, overflow:"hidden"}}>
                      <div style={{width:`${barW}%`, height:"100%", background:"var(--green-ink)", borderRadius:3}}/>
                    </div>
                    <span className="mono" style={{fontSize:10, color:"var(--green-ink)", minWidth:30, textAlign:"right"}}>−{pct}%</span>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)", marginTop:6}}>
            These projections feed Stage 5 closure scoring · owners and due dates set by velocity — high-velocity items fast-tracked
          </div>
        </div>
      )}

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

  // ---- sub-steps ----
  const rerunList = c.rerun_recommended || [];
  const s5Steps = [
    c.evidence_artifacts > 0 ? {
      label: "Closure evidence artifacts reviewed",
      detail: `${c.evidence_artifacts} evidence file${c.evidence_artifacts !== 1 ? "s" : ""} evaluated against MAP success criteria`,
    } : null,
    c.risks_closed > 0 ? {
      label: "Risk closures validated",
      detail: `${c.risks_closed} risk${c.risks_closed !== 1 ? "s" : ""} fully closed — evidence confirms sustained control effectiveness`,
    } : null,
    c.risks_reduced > 0 ? {
      label: "Partial risk reductions confirmed",
      detail: `${c.risks_reduced} risk${c.risks_reduced !== 1 ? "s" : ""} partially mitigated — residual exposure documented in register`,
    } : null,
    c.risks_unchanged > 0 ? {
      label: "Residual risks identified",
      detail: `${c.risks_unchanged} risk${c.risks_unchanged !== 1 ? "s" : ""} unchanged — root causes persist, escalated for next cycle`,
    } : null,
    (c.projected_total_risk_reduction_pct || 0) > 0 ? {
      label: "Portfolio risk reduction quantified",
      detail: `${c.projected_total_risk_reduction_pct}% projected total reduction — aggregated across all closed and partially mitigated risks`,
    } : null,
    rerunList.length > 0 ? {
      label: "Re-test schedule set",
      detail: `${rerunList.length} risk${rerunList.length !== 1 ? "s" : ""} queued for next-cycle re-assessment`,
      children: rerunList.map(id => id),
    } : null,
  ].filter(Boolean);
  // ----

  return (
    <div className="stage-body-grid">
      <SubStepList steps={s5Steps}/>
      <div className="stage-stat-row">
        <Stat l="Risks closed" v={c.risks_closed || 0} mono color="var(--green-ink)"/>
        <Stat l="Risks reduced" v={c.risks_reduced || 0} mono color="var(--acc-ink)"/>
        <Stat l="Risks unchanged" v={c.risks_unchanged || 0} mono color="var(--ink-3)"/>
        <Stat l="Projected reduction" v={`${c.projected_total_risk_reduction_pct || 0}%`} mono/>
      </div>
      {/* Closure validation criteria */}
      <div className="stage-detail">
        <h5>Closure validation logic</h5>
        <div style={{display:"flex", flexDirection:"column", gap:5, fontSize:11}}>
          <div style={{padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>CLOSED: </span>
            <span style={{fontSize:10.5, color:"var(--ink-2)"}}>Evidence confirms success criteria met and control effectiveness sustained — risk removed from active register</span>
          </div>
          <div style={{padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
            <span className="mono" style={{fontSize:10, color:"var(--amber-ink)"}}>REDUCED: </span>
            <span style={{fontSize:10.5, color:"var(--ink-2)"}}>Partial evidence — some success criteria met, residual exposure documented and carried to next cycle</span>
          </div>
          <div style={{padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
            <span className="mono" style={{fontSize:10, color:"var(--red-ink)"}}>UNCHANGED: </span>
            <span style={{fontSize:10.5, color:"var(--ink-2)"}}>Root causes persist or evidence insufficient — escalated for priority treatment in Stage 6 recalibration</span>
          </div>
          {(c.projected_total_risk_reduction_pct || 0) > 0 && (
            <div style={{padding:"6px 10px", background:"var(--green-soft)", borderRadius:5, border:"1px solid color-mix(in oklch, var(--green-ink) 30%, var(--line))"}}>
              <span className="mono" style={{fontSize:10, color:"var(--green-ink)"}}>Portfolio reduction = </span>
              <span className="mono" style={{fontSize:10, color:"var(--green-ink)", fontWeight:600}}>{c.projected_total_risk_reduction_pct}%</span>
              <span style={{fontSize:10, color:"var(--green-ink)", marginLeft:6}}>aggregated across {(c.risks_closed||0) + (c.risks_reduced||0)} closed/mitigated risks — fed to Stage 6 loop health score</span>
            </div>
          )}
        </div>
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

  // ---- sub-steps ----
  const lessons = l.lessons_learned || [];
  const s6Steps = [
    l.loop_health ? {
      label: "Loop health scored",
      detail: `${l.loop_health} — audit impact ${l.audit_impact_score || "—"}/25 · derived from RAG distribution, velocity trend, and MAP completion`,
    } : null,
    {
      label: "Risk velocity recalibrated",
      detail: "Signal weights and velocity decay factors updated based on this cycle's observed outcomes",
    },
    lessons.length > 0 ? {
      label: "Lessons learned captured",
      detail: `${lessons.length} lesson${lessons.length !== 1 ? "s" : ""} documented for process improvement`,
      children: lessons.map(s => s),
    } : null,
    l.maps_open > 0 ? {
      label: "Open MAPs carried forward",
      detail: `${l.maps_open} action plan${l.maps_open !== 1 ? "s" : ""} remain open — tracked into the next risk cycle`,
    } : null,
    l.next_trigger_days > 0 ? {
      label: "Next cycle trigger scheduled",
      detail: `Re-run in ${l.next_trigger_days} days${l.next_cycle_focus ? ` · next focus: ${l.next_cycle_focus}` : ""}`,
    } : null,
    {
      label: "Updated risk register fed back to loop",
      detail: "Post-MAP scores, closures, recalibrated velocity, and lessons ready for Stage 1 re-ingest",
    },
  ].filter(Boolean);
  // ----

  return (
    <div className="stage-body-grid">
      <SubStepList steps={s6Steps}/>
      <div className="stage-stat-row">
        <Stat l="Loop health" v={l.loop_health || "—"} mono color="var(--amber-ink)"/>
        <Stat l="Audit impact" v={`${l.audit_impact_score || "—"}/25`} mono/>
        <Stat l="MAPs open" v={l.maps_open || 0} mono/>
        <Stat l="Next cycle" v={`${l.next_trigger_days || 0}d`} mono/>
      </div>
      {/* Loop health scoring criteria */}
      <div className="stage-detail">
        <h5>Loop health · scoring criteria</h5>
        <div style={{display:"flex", flexDirection:"column", gap:5, fontSize:11}}>
          <div style={{padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>Audit impact score (0–25): </span>
            <span style={{fontSize:10.5, color:"var(--ink-2)"}}>RAG distribution (10pts) + velocity trend (7pts) + MAP completion rate (8pts)</span>
          </div>
          <div style={{display:"flex", gap:6}}>
            {[["STRONG", "≥ 20/25", "var(--green-ink)", "var(--green-soft)"], ["ADEQUATE", "12–19", "var(--amber-ink)", "var(--amber-soft)"], ["WEAK", "< 12/25", "var(--red-ink)", "var(--red-soft)"]].map(([label, range, ink, bg]) => (
              <div key={label} style={{flex:1, padding:"6px 10px", background:bg, borderRadius:5, border:`1px solid color-mix(in oklch, ${ink} 25%, var(--line))`}}>
                <div className="mono" style={{fontSize:9.5, color:ink, fontWeight:600}}>{label}</div>
                <div style={{fontSize:10.5, color:ink, marginTop:2}}>{range}</div>
              </div>
            ))}
          </div>
          <div style={{padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>Signal weights recalibrated: </span>
            <span style={{fontSize:10.5, color:"var(--ink-2)"}}>velocity decay factors and CE multipliers updated from observed outcomes — fed back to Stage 1 on next run</span>
          </div>
          {l.next_trigger_days > 0 && (
            <div style={{padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
              <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>Next cycle trigger: </span>
              <span className="mono" style={{fontSize:10, color:"var(--ink-2)", fontWeight:600}}>{l.next_trigger_days} days</span>
              {l.next_cycle_focus && <span style={{fontSize:10.5, color:"var(--ink-2)", marginLeft:8}}>· focus: {l.next_cycle_focus}</span>}
            </div>
          )}
        </div>
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
