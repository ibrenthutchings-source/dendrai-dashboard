/* ============================================================
   Pipeline — 6 stages with HITL gates and animated run
   Stage statuses: idle / running / waiting / done
   Driven by parent (App): receives stageState, gateState, output.
   ============================================================ */

import { RiskApprovalReview } from "./risk-approval.jsx";
import { ScopeApprovalReview } from "./audit-scope-review.jsx";

const STAGES = [
  { id: "s1", name: "Signal Intake",                       desc: "10-K · peer filings · industry RSS · SEC 8-K · internal KRIs" },
  { id: "s2", name: "Risk Assessment + Velocity",          desc: "Continuous scoring · velocity delta · RAG matrix" },
  { id: "s3", name: "Audit Scope Generator",               desc: "Risk-linked audit plan · fiscal-quarter-ready workplan" },
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
                    riskApprovals, onApproveRisk, onApproveAllRisks, onAddRisk,
                    scopeApprovals, onApproveObjective, onOpenAdjustObjective, onApproveAllObjectives, onAddObjective,
                    manualAudits = [], onAddAudit, onRemoveAudit,
                    narrativeResult, onNarrativeResult, forecasts, ticker: pipelineTicker = "",
                    liveMode = false, fredSeries = null, industry = "",
                    enabledFeedIds = [], onRssSignalsReady = null,
                    flowMeta = null, onOpenMainFlow = null,
                    risks = [], companyName = "", peerData = null,
                    ratios = {}, events = [] }) {
  const threshold = APPETITE_THRESHOLDS[appetiteLevel] ?? 7.5;
  const s1Extra = {
    rssRunProgress,
    rssFeeds: rssFeeds || [],
    ticker: pipelineTicker || output.s1?.ticker || "",
    narrativeResult,
    onNarrativeResult,
    forecasts,
    enabledFeedIds,
    onRssSignalsReady,
    onAddObjective,
    peerData,
  };
  const s2Extra = {
    liveRssSignals, rssLastUpdated, rssRefreshing,
    appetiteLevel, appetiteThreshold: threshold,
    perRiskAppetite: perRiskAppetite || {},
    setPerRiskAppetite,
    allSignals: allSignals || [],
    onOpenAdjustRisk,
    onRerunFromS3,
    liveMode, livefacts, fredSeries, industry,
    ticker: pipelineTicker || "",
  };
  const s3Extra = {
    manualAudits,
    onAddAudit,
    onRemoveAudit,
    risks: output.s2?.risks || [],
  };
  const s5Extra = {
    flowMeta,
    risks: output.s2?.risks || [],
    maps: output.s4?.maps || [],
    onOpenMain: onOpenMainFlow,
  };
  return (
    <div className="pipeline">
      {STAGES.map((s, i) => {
        const status = stageState[s.id] || "idle";
        const isDone = status === "done";
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
              s5Extra={s.id === "s5" ? s5Extra : null}
            />

            {/* ── Stage-linked panels (always visible when stage is done) ── */}
            {isDone && s.id === "s1" && window.RSSPanel && (
              <PipelinePanel label="RSS Signals">
                <RSSPanel enabledFeedIds={enabledFeedIds} onSignalsReady={onRssSignalsReady} risks={risks} ticker={pipelineTicker || ""} companyName={companyName}/>
              </PipelinePanel>
            )}
            {isDone && s.id === "s2" && forecasts && (() => {
              const GeoSegKPI = window.GeoSegmentKPISection;
              return (
                <PipelinePanel label="Forecasts">
                  <ForecastChartsInline forecasts={forecasts} livefacts={livefacts}/>
                  {GeoSegKPI && (
                    <GeoSegKPI
                      data={forecasts}
                      industry={industry}
                      ticker={pipelineTicker || ""}
                      livefacts={livefacts}
                    />
                  )}
                </PipelinePanel>
              );
            })()}
            {isDone && s.id === "s4" && window.MapsTab && (output.s4?.maps?.length > 0) && (
              <PipelinePanel label="Management Action Plans">
                <MapsTab maps={output.s4.maps}/>
              </PipelinePanel>
            )}
            {isDone && s.id === "s2" && flowMeta && (output.s2?.risks?.length > 0) && (
              <PipelinePanel label="Risk Flow">
                <SankeyInline
                  risks={output.s2.risks}
                  maps={output.s4?.maps || []}
                  flowMeta={flowMeta}
                  objectives={output.s3?.objectives || []}
                  onOpenMain={onOpenMainFlow}
                />
              </PipelinePanel>
            )}

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
                    onSubmit={() => onApprove(1)}
                    onOverrideGate={() => onOverride(1)}
                    onAddRisk={onAddRisk}
                  />
                ) : gateNum === 2 && gateState.g2 === "pending" ? (
                  <ScopeApprovalReview
                    objectives={output.s3?.objectives || []}
                    approvals={scopeApprovals}
                    risks={output.s2?.risks || []}
                    onApproveObjective={onApproveObjective}
                    onOpenAdjust={onOpenAdjustObjective}
                    onApproveAll={onApproveAllObjectives}
                    onSubmit={() => onApprove(2)}
                    onOverrideGate={() => onOverride(2)}
                    onAddObjective={onAddObjective}
                  />
                ) : (
                  <>
                    <HITLGate
                      num={gateNum}
                      state={gateState[`g${gateNum}`]}
                      onApprove={() => onApprove(gateNum)}
                      onOverride={() => onOverride(gateNum)}
                    />
                    {gateNum === 2 && (gateState.g2 === "approved" || gateState.g2 === "overridden") && window.CoverageGapPanel && (
                      <PipelinePanel label="Coverage Gap Analysis">
                        <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:12, gap:12}}>
                          <span style={{fontSize:11, color:"var(--ink-3)"}}>
                            Cross-check scope against risk register — address gaps before Stage 4 or revise the audit scope.
                          </span>
                          {onRerunFromS3 && (
                            <button className="btn btn-sm" style={{fontSize:10, padding:"3px 10px", flexShrink:0}} onClick={onRerunFromS3}>
                              <Icon name="reset" size={10}/> Revise Stage 3 — Audit Scope
                            </button>
                          )}
                        </div>
                        <CoverageGapPanel
                          risks={output.s2?.risks || []}
                          objectives={output.s3?.objectives || []}
                          rssSignals={liveRssSignals}
                          events={events}
                          ratios={ratios}
                          industry={industry}
                          ticker={pipelineTicker}
                        />
                      </PipelinePanel>
                    )}
                  </>
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

function Stage({ stage, status, isOpen, onToggle, output, signals, livefacts, s1Extra, s2Extra, s3Extra, s5Extra, forecasts }) {
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
        <div style={{padding: "8px 16px 10px 16px", borderTop: "1px solid var(--line)"}}>
          <div className="mono" style={{fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 6}}>COMPLETED STEPS</div>
          <ExpandableSubSteps steps={subSteps}/>
        </div>
      )}
      {isOpen && (
        <div className="stage-body open">
          <StageBody id={stage.id} status={status} output={output} signals={signals} livefacts={livefacts} s1Extra={s1Extra} s2Extra={s2Extra} s3Extra={s3Extra} s5Extra={s5Extra} forecasts={forecasts}/>
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

// Runs backtest on history, passes metrics to ForecastChart so caption always appears
function FCWithMetrics({ history, forecast, unit, color, decimals }) {
  const [metrics, setMetrics] = React.useState(null);
  React.useEffect(() => {
    if (!history?.length || !window.BACKTESTING || !window.FORECASTING) return;
    try {
      const bt = window.BACKTESTING.backtestAll(history.map(h => h.v));
      setMetrics(bt?.results?.ensemble ?? null);
    } catch (e) {}
  }, [history]);
  const FC = window.ForecastChart;
  if (!FC) return null;
  return React.createElement(FC, { history, forecast, unit, color, decimals, chartMetrics: metrics });
}

function ForecastChartsInline({ forecasts, livefacts }) {
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    if (!window.ForecastChart) {
      const id = setTimeout(() => setTick(t => t + 1), 300);
      return () => clearTimeout(id);
    }
  }, [tick]);

  const FC = window.ForecastChart;
  if (!forecasts) return <div style={{fontSize:11, color:"var(--ink-4)", padding:"12px 0"}}>No forecast data — run the loop first.</div>;
  if (!FC) return <div style={{fontSize:11, color:"var(--ink-4)", padding:"12px 0"}}>Loading chart engine…</div>;
  const rev = forecasts.revenue;
  const mg  = forecasts.margin;
  const lastRev  = rev?.history?.slice(-1)[0]?.v;
  const fcRev    = rev?.forecast?.slice(-1)[0]?.base;
  const lastMg   = mg?.history?.slice(-1)[0]?.v;
  const fcMg     = mg?.forecast?.slice(-1)[0]?.base;
  const revDelta = lastRev && fcRev ? ((fcRev - lastRev) / lastRev * 100) : null;
  const mgDelta  = lastMg != null && fcMg != null ? (fcMg - lastMg) * 100 : null;
  return (
    <div style={{display:"flex", flexDirection:"column", gap:18}}>
      {rev?.history?.length > 0 && (
        <div>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"baseline", marginBottom:6}}>
            <div style={{fontSize:11, fontWeight:600, color:"var(--ink-2)"}}>Revenue Growth Risk ($M)</div>
            {revDelta != null && (
              <div style={{fontSize:10, fontFamily:"var(--mono)", color: revDelta >= 0 ? "var(--green-ink)" : "var(--red-ink)"}}>
                {revDelta >= 0 ? "▲" : "▼"}{Math.abs(revDelta).toFixed(1)}% · 4Q forecast ${fcRev?.toFixed(2)}M
              </div>
            )}
          </div>
          <FCWithMetrics history={rev.history.slice(-16)} forecast={rev.forecast} unit="$M" color="var(--acc)"/>
        </div>
      )}
      {mg?.history?.length > 0 && (
        <div>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"baseline", marginBottom:6}}>
            <div style={{fontSize:11, fontWeight:600, color:"var(--ink-2)"}}>Margin Compression Risk (%)</div>
            {mgDelta != null && (
              <div style={{fontSize:10, fontFamily:"var(--mono)", color: mgDelta >= 0 ? "var(--green-ink)" : "var(--red-ink)"}}>
                {mgDelta >= 0 ? "▲" : "▼"}{Math.abs(mgDelta).toFixed(0)}bps · 4Q forecast {fcMg?.toFixed(2)}%
              </div>
            )}
          </div>
          <FCWithMetrics history={mg.history.slice(-16)} forecast={mg.forecast} unit="%" color="var(--violet)"/>
        </div>
      )}
      {livefacts && (
        <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)"}}>
          Source: EDGAR XBRL — {livefacts.entity} · {livefacts.cik}
        </div>
      )}
    </div>
  );
}

function SankeyInline({ risks, maps, flowMeta, objectives, onOpenMain }) {
  const [selId, setSelId] = React.useState(null);
  const [hovId, setHovId] = React.useState(null);
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    if (!window.RiskFlowSankey) {
      const id = setTimeout(() => setTick(t => t + 1), 300);
      return () => clearTimeout(id);
    }
  }, [tick]);

  const RS = window.RiskFlowSankey;
  if (!RS) return <div style={{fontSize:11, color:"var(--ink-4)", padding:"12px 0"}}>Loading chart engine…</div>;
  if (!risks?.length || !flowMeta) return <div style={{fontSize:11, color:"var(--ink-4)", padding:"12px 0"}}>Flow data populates after Stage 2 and Risk Flow generation.</div>;
  return (
    <div>
      <RS
        risks={risks}
        maps={maps || []}
        flowMeta={flowMeta}
        objectives={objectives || []}
        selectedId={selId}
        onSelect={setSelId}
        hoverId={hovId}
        onHover={setHovId}
      />
      {onOpenMain && (
        <div style={{display:"flex", justifyContent:"flex-end", marginTop:10}}>
          <button className="cfg-link" onClick={onOpenMain} type="button" style={{fontSize:11}}>
            Full risk flow view <Icon name="chev-r" size={10}/>
          </button>
        </div>
      )}
    </div>
  );
}

function PipelinePanel({ label, children }) {
  const [open, setOpen] = React.useState(true);
  return (
    <div className="stage" style={{marginTop: 0, borderStyle: "dashed", opacity: 0.97}}>
      <div className="stage-head" onClick={() => setOpen(o => !o)} style={{cursor: "pointer"}}>
        <div className="stage-num" style={{background: "var(--acc-soft)", color: "var(--acc-ink)", border: "1px solid var(--acc-ink, var(--line))", fontSize: 9, letterSpacing: "0.04em", fontFamily: "var(--mono)", minWidth: 32, padding: "0 6px", display: "flex", alignItems: "center", justifyContent: "center"}}>
          {label.toUpperCase().slice(0, 4)}
        </div>
        <div className="stage-meta">
          <div className="stage-name">{label}</div>
        </div>
        <Icon name={open ? "chev-u" : "chev-d"} size={14} className="muted"/>
      </div>
      {open && <div className="stage-body open">{children}</div>}
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

// ------ Rich sub-step builder — returns { label, detail, children[] } objects ------
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
    const narrRisks = s1Extra?.narrativeResult?.emerging_risks || [];
    return [
      bySrc["EDGAR 10-K"] > 0 ? {
        label: "EDGAR 10-K loaded",
        detail: livefacts
          ? `${livefacts.entity} · CIK ${livefacts.cik} · ${bySrc["EDGAR 10-K"]} financial signals extracted`
          : `${bySrc["EDGAR 10-K"]} financial signals from annual filing`,
        children: livefacts ? [
          livefacts.revenue?.latestAnnual && `Revenue (FY): ${fmt$M(livefacts.revenue.latestAnnual.val)} as of ${livefacts.revenue.latestAnnual.end}`,
          livefacts.netIncome?.latestAnnual && `Net Income: ${fmt$M(livefacts.netIncome.latestAnnual.val)}`,
          livefacts.grossMarginPct != null && `Gross Margin: ${livefacts.grossMarginPct.toFixed(1)}%`,
          livefacts.cash?.latestAnnual && `Cash & Equiv: ${fmt$M(livefacts.cash.latestAnnual.val)}`,
        ].filter(Boolean) : undefined,
      } : null,
      livefacts ? {
        label: "Financial data mapped to risk pipeline",
        detail: "Revenue, net income, gross margin, and cash metrics incorporated as risk score inputs",
      } : null,
      bySrc["Peer 10-K"] > 0 ? {
        label: `Peer 10-K parsed`,
        detail: `${bySrc["Peer 10-K"]} comparative signal${bySrc["Peer 10-K"] !== 1 ? "s" : ""} across industry peers`,
      } : null,
      rssSigs.length > 0 ? {
        label: `Industry RSS feeds ingested`,
        detail: `${rssSigs.length} article${rssSigs.length !== 1 ? "s" : ""} velocity-graded across ${Object.keys(rssByFeed).length} feed${Object.keys(rssByFeed).length !== 1 ? "s" : ""}`,
        children: Object.entries(rssByFeed).map(([feed, count]) => `${feed} — ${count} article${count !== 1 ? "s" : ""}`),
      } : null,
      fredTotal > 0 ? {
        label: `FRED macro indicators ingested`,
        detail: `${fredTotal} economic series · ${fredContr} contractionary signal${fredContr !== 1 ? "s" : ""} flagged for risk adjustment`,
        children: fredContr > 0 ? [`${fredContr} contractionary signal${fredContr !== 1 ? "s" : ""} → +${(fredContr * 0.08).toFixed(2)} score lift on macro-category risks`] : undefined,
      } : null,
      bySrc["Internal KRI"] > 0 ? {
        label: `Internal KRIs assessed`,
        detail: `${bySrc["Internal KRI"]} key risk indicator${bySrc["Internal KRI"] !== 1 ? "s" : ""} assessed against control thresholds`,
      } : null,
      bySrc["Incident"] > 0 ? {
        label: `Incident log reviewed`,
        detail: `${bySrc["Incident"]} recent incident${bySrc["Incident"] !== 1 ? "s" : ""} evaluated for risk linkage`,
      } : null,
      bySrc["SEC 8-K"] > 0 ? {
        label: `SEC 8-K material events ingested`,
        detail: `${bySrc["SEC 8-K"]} filing${bySrc["SEC 8-K"] !== 1 ? "s" : ""} mapped to risk pipeline — severity amplifiers: P1 +0.5 · P2 +0.25 · P3 +0.10 per category match`,
        children: signals.filter(s => s.src === "SEC 8-K").slice(0, 6).map(s => `${s.severity || "—"} · ${s.label}`),
      } : null,
      total > 0 ? {
        label: `${total} signals velocity-graded`,
        detail: `Scored on 1–3 scale: ${high} high · ${med} medium · ${total - high - med} standard`,
        children: [
          high > 0 ? `${high} high-velocity (v3) — flagged for systemic lift (+${Math.min(0.20, high * 0.05).toFixed(2)} applied to all risks)` : null,
          med > 0  ? `${med} medium-velocity (v2)` : null,
          (total - high - med) > 0 ? `${total - high - med} standard-velocity (v1)` : null,
        ].filter(Boolean),
      } : null,
      s1Extra?.narrativeResult ? {
        label: `AI narrative analysis (Item 1A)`,
        detail: `${narrRisks.length} emerging risk${narrRisks.length !== 1 ? "s" : ""} · ${(s1Extra.narrativeResult.yoy_changes || []).length} language shift${(s1Extra.narrativeResult.yoy_changes || []).length !== 1 ? "s" : ""} detected`,
        children: narrRisks.slice(0, 4).map(r => `${r.severity?.toUpperCase() || "—"} · ${r.title}`),
      } : null,
    ].filter(Boolean);
  }

  if (stageId === "s2") {
    const risks    = output?.risks || [];
    const counts   = risks.reduce((acc, r) => { acc[r.rag] = (acc[r.rag] || 0) + 1; return acc; }, {});
    const appetite = output?.riskAppetite;
    const allSigs  = s2Extra?.allSignals || [];
    const liveRss  = s2Extra?.liveRssSignals || [];
    const fredContr  = allSigs.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").length;
    const rssLinked  = liveRss.filter(s => (s.affectedRisks || []).length > 0);
    const highVel    = risks.filter(r => r.velocity >= 3);
    return [
      {
        label: `${risks.length} risks loaded from industry template`,
        detail: `Inherent scores and control environment assigned — base register ready for signal overlay`,
        children: risks.slice(0, 5).map(r => `${r.id} · ${r.name} — inherent ${r.inherent?.toFixed(1) ?? "—"}`),
      },
      fredContr > 0 ? {
        label: `FRED macro adjustments applied`,
        detail: `${fredContr} contractionary indicator${fredContr !== 1 ? "s" : ""} → +0.08 per signal to macro-category risk scores`,
        children: allSigs.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").slice(0, 4).map(s => `${s.label || s.src} — contractionary (+0.08)`),
      } : null,
      rssLinked.length > 0 ? {
        label: `RSS signal adjustments applied`,
        detail: `${rssLinked.length} linked signal${rssLinked.length !== 1 ? "s" : ""} — velocity × 0.08 added to directly affected risks`,
        children: rssLinked.slice(0, 4).map(s => `${s.feedName || "RSS"} · ${(s.title || "").slice(0, 60)} → +${((s.velocity || 0) * 0.08).toFixed(2)}`),
      } : null,
      (() => {
        const eightK = allSigs.filter(s => s.src === "SEC 8-K");
        return eightK.length > 0 ? {
          label: `SEC 8-K adjustments applied`,
          detail: `${eightK.length} material filing${eightK.length !== 1 ? "s" : ""} — P1 +0.5 · P2 +0.25 · P3 +0.10 per category-matched risk (capped +1.5)`,
          children: eightK.slice(0, 5).map(s => `${s.severity} · ${s.label}`),
        } : null;
      })(),
      {
        label: "Control effectiveness (CE) scored per risk",
        detail: "STRONG / ADEQUATE / WEAK / NONE ratings applied — residual score adjusted per control environment",
        children: ["STRONG: −0.7 · CE_mult 0.80", "ADEQUATE: −0.3 · CE_mult 0.95", "WEAK: +0.1 · CE_mult 1.10", "NONE: +0.4 · CE_mult 1.20"],
      },
      {
        label: `RAG matrix computed`,
        detail: `${counts.R || 0} RED · ${counts.A || 0} AMBER · ${counts.G || 0} GREEN — thresholds: RED ≥ 15, AMBER ≥ 9, GREEN < 9`,
        children: [
          counts.R > 0 ? `${counts.R} RED risk${counts.R !== 1 ? "s" : ""} (score ≥ 15) — priority audit targets` : null,
          counts.A > 0 ? `${counts.A} AMBER risk${counts.A !== 1 ? "s" : ""} (score 9–14) — monitored` : null,
          counts.G > 0 ? `${counts.G} GREEN risk${counts.G !== 1 ? "s" : ""} (score < 9) — within tolerance` : null,
        ].filter(Boolean),
      },
      highVel.length > 0 ? {
        label: `${highVel.length} high-velocity risk${highVel.length !== 1 ? "s" : ""} flagged for escalation`,
        detail: "Velocity-3 risks receive +0.05 systemic lift and are prioritised for P1 audit objectives in Stage 3",
        children: highVel.slice(0, 5).map(r => `${r.name} — score ${r.score?.toFixed(1)} · v${r.velocity}`),
      } : null,
      appetite ? {
        label: appetite.breaching?.length > 0
          ? `Risk appetite breached — ${appetite.breaching.length} risk${appetite.breaching.length !== 1 ? "s" : ""} exceed ${appetite.level} threshold`
          : `All risks within ${appetite.level} appetite tolerance`,
        detail: appetite.breaching?.length > 0
          ? `Gate 1 human review triggered — required before Stage 3 scoping`
          : `No appetite breaches detected at ${s2Extra?.appetiteLevel || appetite.level} threshold`,
        children: (appetite.breaching || []).slice(0, 5).map(id => {
          const r = risks.find(x => x.id === id);
          return r ? `${r.name} — score ${r.score?.toFixed(1)}` : id;
        }),
      } : null,
    ].filter(Boolean);
  }

  if (stageId === "s3") {
    const objs     = output?.objectives || [];
    const p1       = objs.filter(o => o.priority === "P1");
    const p2       = objs.filter(o => o.priority === "P2");
    const hrs      = objs.reduce((a, o) => a + (o.hours || 0), 0);
    const manual   = s3Extra?.manualAudits || [];
    const risks    = s3Extra?.risks || [];
    const redAmber = risks.filter(r => r.rag === "R" || r.rag === "A");
    return [
      {
        label: `${redAmber.length} RED/AMBER risk${redAmber.length !== 1 ? "s" : ""} mapped to audit objectives`,
        detail: "HIGH-priority risks from Stage 2 drove objective generation",
        children: redAmber.slice(0, 5).map(r => `${r.rag === "R" ? "RED" : "AMBER"} · ${r.name} — score ${r.score?.toFixed(1)}`),
      },
      objs.length > 0 ? {
        label: `${objs.length} audit objective${objs.length !== 1 ? "s" : ""} generated`,
        detail: "Each objective linked to specific risk IDs and control gaps",
        children: objs.slice(0, 5).map(o => `${o.priority} · ${o.objective?.slice(0, 70)} (${o.hours}h)`),
      } : null,
      p1.length > 0 ? {
        label: `${p1.length} P1 priority objective${p1.length !== 1 ? "s" : ""}`,
        detail: "Immediate remediation — targeting RED risks and appetite breaches",
        children: p1.slice(0, 4).map(o => `${o.objective?.slice(0, 70)} · ${o.hours}h`),
      } : null,
      p2.length > 0 ? {
        label: `${p2.length} P2 priority objective${p2.length !== 1 ? "s" : ""}`,
        detail: "Planned review — covering residual AMBER risk exposure",
        children: p2.slice(0, 4).map(o => `${o.objective?.slice(0, 70)} · ${o.hours}h`),
      } : null,
      hrs > 0 ? {
        label: `${hrs} total audit hours estimated`,
        detail: "Allocated proportionally to risk score and velocity",
      } : null,
      {
        label: "Fiscal-quarter-ready workplan built",
        detail: "Objectives structured for fiscal-quarter execution with defined scope, linked risks, and control coverage",
      },
      manual.length > 0 ? {
        label: `${manual.length} manual audit${manual.length !== 1 ? "s" : ""} incorporated`,
        detail: "Planned audits added to scope alongside AI-generated objectives",
        children: manual.map(a => `${a.title} — ${a.when} · linked ${a.riskId} · −${a.reduction}% risk`),
      } : null,
    ].filter(Boolean);
  }

  if (stageId === "s4") {
    const maps    = output?.maps || [];
    const avgRed  = maps.reduce((a, m) => a + (m.reduction_pct || 0), 0);
    const owners  = [...new Set(maps.map(m => m.owner).filter(Boolean))];
    const redMaps = maps.filter(m => m.risk_impact === "R");
    return [
      {
        label: `${maps.length} finding${maps.length !== 1 ? "s" : ""} linked to risks`,
        detail: `${redMaps.length} RED-impact finding${redMaps.length !== 1 ? "s" : ""} — each tied to a specific risk and control gap`,
        children: maps.slice(0, 5).map(m => `${m.id} · ${(m.finding || "").slice(0, 65)} — ${m.risk_impact || "—"}`),
      },
      {
        label: "Root cause analysis completed per finding",
        detail: "Contributing factors identified — systemic, process, and control gaps classified",
      },
      owners.length > 0 ? {
        label: `${owners.length} control owner${owners.length !== 1 ? "s" : ""} assigned`,
        detail: "Accountable owners allocated across all action plans",
        children: owners.slice(0, 6).map(o => `${o}`),
      } : null,
      {
        label: "Due dates and milestones set",
        detail: "Timelines calibrated to risk velocity — high-velocity items fast-tracked",
        children: maps.slice(0, 4).map(m => `${m.id} — due ${m.due_date || "TBD"} · ${m.completion_pct || 0}% complete`),
      },
      {
        label: "Success criteria defined",
        detail: "Measurable closure conditions specified per MAP — quantified risk reduction targets set",
      },
      avgRed > 0 ? {
        label: `${avgRed}% total risk reduction projected`,
        detail: `Avg ${Math.round(avgRed / Math.max(1, maps.length))}% per finding — feeds Stage 5 closure scoring`,
        children: maps.slice(0, 5).map(m => `${m.id} · ${(m.finding || "").slice(0, 55)} — −${m.reduction_pct || 0}%`),
      } : null,
    ].filter(Boolean);
  }

  if (stageId === "s5") {
    const c       = output?.closure || {};
    const reruns  = c.rerun_recommended || [];
    return [
      c.evidence_artifacts > 0 ? {
        label: `${c.evidence_artifacts} evidence artifact${c.evidence_artifacts !== 1 ? "s" : ""} reviewed`,
        detail: "Evidence files evaluated against MAP success criteria",
      } : null,
      c.risks_closed > 0 ? {
        label: `${c.risks_closed} risk${c.risks_closed !== 1 ? "s" : ""} fully closed`,
        detail: "Evidence confirms sustained control effectiveness — removed from active register",
      } : null,
      c.risks_reduced > 0 ? {
        label: `${c.risks_reduced} risk${c.risks_reduced !== 1 ? "s" : ""} partially mitigated`,
        detail: "Partial evidence — residual exposure documented and carried to next cycle",
      } : null,
      c.risks_unchanged > 0 ? {
        label: `${c.risks_unchanged} risk${c.risks_unchanged !== 1 ? "s" : ""} unchanged — escalated`,
        detail: "Root causes persist or evidence insufficient — priority treatment in Stage 6 recalibration",
      } : null,
      (c.projected_total_risk_reduction_pct || 0) > 0 ? {
        label: `${c.projected_total_risk_reduction_pct}% projected portfolio risk reduction`,
        detail: `Aggregated across ${(c.risks_closed || 0) + (c.risks_reduced || 0)} closed/mitigated risks — fed to Stage 6 loop health score`,
      } : null,
      reruns.length > 0 ? {
        label: `${reruns.length} risk${reruns.length !== 1 ? "s" : ""} queued for next-cycle re-test`,
        detail: "Flagged for re-assessment in the next audit loop",
        children: reruns.map(id => `${id} — re-assess in next cycle`),
      } : null,
    ].filter(Boolean);
  }

  if (stageId === "s6") {
    const l       = output?.loop || {};
    const lessons = l.lessons_learned || [];
    return [
      l.loop_health ? {
        label: `Loop health: ${l.loop_health}`,
        detail: `Audit impact ${l.audit_impact_score || "—"}/25 — derived from RAG distribution, velocity trend, and MAP completion`,
      } : null,
      {
        label: "Risk velocity weights recalibrated",
        detail: "Signal weights and velocity decay factors updated from this cycle's observed outcomes",
      },
      lessons.length > 0 ? {
        label: `${lessons.length} lesson${lessons.length !== 1 ? "s" : ""} documented`,
        detail: "Process improvement insights captured for next audit cycle",
        children: lessons.slice(0, 5).map(s => s),
      } : null,
      l.maps_open > 0 ? {
        label: `${l.maps_open} open MAP${l.maps_open !== 1 ? "s" : ""} carried forward`,
        detail: "Tracked into the next risk cycle",
      } : null,
      l.next_trigger_days > 0 ? {
        label: `Next cycle in ${l.next_trigger_days} days`,
        detail: l.next_cycle_focus ? `Focus: ${l.next_cycle_focus}` : "Full re-run scheduled",
      } : null,
      {
        label: "Updated risk register fed back to Stage 1",
        detail: "Recalibrated scores and lessons re-enter the loop as baseline inputs",
      },
    ].filter(Boolean);
  }

  return [];
}

// ------ Expandable completed-step renderer ------
function ExpandableSubSteps({ steps }) {
  const [expanded, setExpanded] = React.useState(new Set());
  const toggle = (i) => setExpanded(prev => {
    const next = new Set(prev);
    next.has(i) ? next.delete(i) : next.add(i);
    return next;
  });
  return (
    <div style={{display:"flex", flexDirection:"column", gap:0}}>
      {steps.map((step, i) => {
        const hasMore = step.detail || (step.children?.length > 0);
        const isExp   = expanded.has(i);
        return (
          <div key={i}>
            <div
              onClick={hasMore ? () => toggle(i) : undefined}
              style={{
                display:"flex", alignItems:"baseline", gap:6, fontSize:11,
                color:"var(--ink-2)", padding:"3px 0",
                cursor: hasMore ? "pointer" : "default",
                borderBottom: isExp ? "none" : "1px solid transparent",
              }}
            >
              <span style={{color:"var(--green-ink)", fontSize:10, flexShrink:0, lineHeight:"16px"}}>✓</span>
              <span style={{flex:1, fontWeight: 500}}>{step.label}</span>
              {hasMore && (
                <span style={{color:"var(--ink-4)", fontSize:9, flexShrink:0, marginLeft:4}}>
                  {isExp ? "▲" : "▼"}
                </span>
              )}
            </div>
            {isExp && hasMore && (
              <div style={{
                paddingLeft:16, paddingBottom:6, paddingTop:2,
                display:"flex", flexDirection:"column", gap:3,
                borderLeft:"2px solid var(--green-soft,var(--line))",
                marginLeft:4, marginBottom:2,
              }}>
                {step.detail && (
                  <div style={{fontSize:10.5, color:"var(--ink-3)", lineHeight:1.45}}>{step.detail}</div>
                )}
                {step.children?.map((c, ci) => (
                  <div key={ci} style={{display:"flex", alignItems:"baseline", gap:5, fontSize:10, color:"var(--ink-3)"}}>
                    <span style={{color:"var(--green-ink)", flexShrink:0}}>└</span>
                    <span>{c}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ------ Stage body content ------
function StageBody({ id, status, output, signals, livefacts, s1Extra, s2Extra, s3Extra, s5Extra, forecasts }) {
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
  if (id === "s1") return <><S1Body output={output} signals={signals} livefacts={livefacts} ticker={s1Extra?.ticker || ""} narrativeResult={s1Extra?.narrativeResult} onNarrativeResult={s1Extra?.onNarrativeResult} forecasts={forecasts ?? s1Extra?.forecasts} enabledFeedIds={s1Extra?.enabledFeedIds || []} onRssSignalsReady={s1Extra?.onRssSignalsReady} peerData={s1Extra?.peerData} onAddObjective={s1Extra?.onAddObjective}/><StageTrace trace={trace}/></>;
  if (id === "s2") return <><S2Body output={output} {...(s2Extra || {})} forecasts={forecasts}/><StageTrace trace={trace}/></>;
  if (id === "s3") return <><S3Body output={output} {...(s3Extra || {})}/><StageTrace trace={trace}/></>;

  if (id === "s4") return <><S4Body output={output}/><StageTrace trace={trace}/></>;
  if (id === "s5") return <><S5Body output={output} flowMeta={s5Extra?.flowMeta} risks={s5Extra?.risks} maps={s5Extra?.maps}/><StageTrace trace={trace}/></>;
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

function S1Body({ output, signals, livefacts, ticker: tickerProp = "", narrativeResult, onNarrativeResult, forecasts, enabledFeedIds = [], onRssSignalsReady = null, peerData = null, onAddObjective = null }) {
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
  const eightKSigsS1 = signals.filter(s => s.src === "SEC 8-K");
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
            {eightKSigsS1.length > 0 && (
              <div style={{display:"flex", alignItems:"baseline", gap:8, fontSize:11}}>
                <span className="tag mono" style={{background:"var(--orange-soft,var(--red-soft))", color:"var(--orange-ink,var(--red-ink))", flexShrink:0}}>8-K</span>
                <span style={{color:"var(--ink-2)", flex:1}}>
                  {eightKSigsS1.length} SEC 8-K filing{eightKSigsS1.length !== 1 ? "s" : ""}
                  {" "}→ <span className="mono">+0.5 P1 · +0.25 P2 · +0.10 P3</span> per category-matched risk (cap +1.5)
                  {" · "}{eightKSigsS1.filter(s => s.severity === "P1").length > 0 && <span style={{color:"var(--red-ink)"}}>{eightKSigsS1.filter(s => s.severity === "P1").length} P1</span>}
                  {eightKSigsS1.filter(s => s.severity === "P2").length > 0 && <span style={{color:"var(--amber-ink)", marginLeft:4}}>{eightKSigsS1.filter(s => s.severity === "P2").length} P2</span>}
                  {eightKSigsS1.filter(s => s.severity === "P3").length > 0 && <span style={{color:"var(--ink-3)", marginLeft:4}}>{eightKSigsS1.filter(s => s.severity === "P3").length} P3</span>}
                </span>
              </div>
            )}
            <div style={{marginTop:2, padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
              <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>Stage 2 residual formula: </span>
              <span className="mono" style={{fontSize:10, color:"var(--ink-2)"}}>score = inherent + macro adj. + industry-signal adj. + 8-K adj. + industry adj. − control-effectiveness discount</span>
            </div>
          </div>
        </div>
      )}

      {/* Beneish M-Score gauge */}
      {forecasts?.mscore != null && (() => {
        const MSG = window.MScoreGauge;
        const ms = forecasts.mscore;
        if (!MSG) return null;
        return (
          <div className="stage-detail">
            <h5>Earnings Manipulation Risk — is revenue recognition or accruals quality deteriorating? (pairs with Z''-Score below)</h5>
            <MSG m={ms.m} peers={peerData?.peers}/>
            <div style={{display:"flex", flexDirection:"column", gap:4, marginTop:8, fontSize:11, color:"var(--ink-2)"}}>
              <div style={{display:"flex", gap:10}}>
                <span className="mono" style={{color:"var(--ink-4)"}}>M = {ms.m?.toFixed(2)}</span>
                <span className="mono" style={{
                  padding:"1px 7px", borderRadius:4, fontSize:10,
                  background: ms.m > -1.78 ? "var(--red-soft)" : ms.m > -2.22 ? "var(--amber-soft)" : "var(--green-soft)",
                  color:      ms.m > -1.78 ? "var(--red-ink)"  : ms.m > -2.22 ? "var(--amber-ink)"  : "var(--green-ink)",
                }}>{ms.band || (ms.m > -1.78 ? "ELEVATED" : ms.m > -2.22 ? "GRAY ZONE" : "NORMAL")}</span>
              </div>
              <div style={{display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:"4px 10px", marginTop:4, padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
                <span className="mono" style={{fontSize:9.5, color:"var(--red-ink)"}}>≥ −1.78 · ELEVATED</span>
                <span className="mono" style={{fontSize:9.5, color:"var(--amber-ink)", textAlign:"center"}}>−2.22 to −1.78 · GRAY ZONE</span>
                <span className="mono" style={{fontSize:9.5, color:"var(--green-ink)", textAlign:"right"}}>≤ −2.22 · NORMAL</span>
              </div>
              <div style={{fontSize:10.5, color:"var(--ink-3)", marginTop:2}}>
                8-variable model: DSRI · GMI · AQI · SGI · DEPI · SGAI · TATA · LVGI · computed from EDGAR 10-K.
              </div>
            </div>
            {(() => {
              const tone = ms.m > -1.78 ? "red" : ms.m > -2.22 ? "amber" : "green";
              const material = tone !== "green";
              return (
                <AuditorTakeaway
                  tone={tone}
                  actionLabel={material ? "Add to scope" : undefined}
                  onAction={material && onAddObjective ? () => onAddObjective(
                    `Review revenue recognition and accruals quality — Beneish M-Score (${ms.m.toFixed(2)}) is ${tone === "red" ? "above the likely-manipulator threshold (-1.78)" : "in the gray zone"}.`
                  ) : undefined}
                >
                  {ms.m > -1.78 ? "Score exceeds the likely-manipulator threshold — accruals and revenue recognition warrant IA review this cycle."
                    : ms.m > -2.22 ? "Gray zone — worth a lighter-touch accruals monitoring pass, not necessarily a full scope item."
                    : "Within normal range — no elevated financial-reporting risk detected from this model."}
                </AuditorTakeaway>
              );
            })()}
          </div>
        );
      })()}

      {/* Altman Z''-Score gauge */}
      {forecasts?.zscore != null && (() => {
        const ZSG = window.ZScoreGauge;
        const zs = forecasts.zscore;
        if (!ZSG) return null;
        return (
          <div className="stage-detail">
            <h5>Solvency Risk — is the balance sheet strong enough to avoid distress? (pairs with M-Score above)</h5>
            <ZSG z={zs.z} peers={peerData?.peers}/>
            <div style={{display:"flex", flexDirection:"column", gap:4, marginTop:8, fontSize:11, color:"var(--ink-2)"}}>
              <div style={{display:"flex", gap:10}}>
                <span className="mono" style={{color:"var(--ink-4)"}}>Z'' = {zs.z?.toFixed(2)}</span>
                <span className="mono" style={{
                  padding:"1px 7px", borderRadius:4, fontSize:10,
                  background: zs.z <= 1.1 ? "var(--red-soft)" : zs.z <= 2.6 ? "var(--amber-soft)" : "var(--green-soft)",
                  color:      zs.z <= 1.1 ? "var(--red-ink)"  : zs.z <= 2.6 ? "var(--amber-ink)"  : "var(--green-ink)",
                }}>{zs.band || (zs.z <= 1.1 ? "DISTRESS" : zs.z <= 2.6 ? "GRAY ZONE" : "SAFE")}</span>
              </div>
              <div style={{display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:"4px 10px", marginTop:4, padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
                <span className="mono" style={{fontSize:9.5, color:"var(--red-ink)"}}>≤ 1.10 · DISTRESS</span>
                <span className="mono" style={{fontSize:9.5, color:"var(--amber-ink)", textAlign:"center"}}>1.10 to 2.60 · GRAY ZONE</span>
                <span className="mono" style={{fontSize:9.5, color:"var(--green-ink)", textAlign:"right"}}>&gt; 2.60 · SAFE</span>
              </div>
              <div style={{fontSize:10.5, color:"var(--ink-3)", marginTop:2}}>
                General/non-manufacturer variant (book equity, no market-cap dependency): working capital, retained earnings, and EBIT relative to total assets, plus book equity to total liabilities — computed from EDGAR 10-K.
              </div>
            </div>
            {(() => {
              const tone = zs.z <= 1.1 ? "red" : zs.z <= 2.6 ? "amber" : "green";
              const material = tone !== "green";
              return (
                <AuditorTakeaway
                  tone={tone}
                  actionLabel={material ? "Add to scope" : undefined}
                  onAction={material && onAddObjective ? () => onAddObjective(
                    `Assess going-concern risk and covenant headroom — Altman Z''-Score (${zs.z.toFixed(2)}) is ${tone === "red" ? "in the distress zone (≤1.10)" : "in the gray zone"}.`
                  ) : undefined}
                >
                  {zs.z <= 1.1 ? "Distress zone — going-concern assessment and covenant headroom warrant IA review this cycle."
                    : zs.z <= 2.6 ? "Gray zone — worth a liquidity/solvency monitoring pass, not necessarily a full scope item."
                    : "Within safe range — no elevated solvency risk detected from this model."}
                </AuditorTakeaway>
              );
            })()}
          </div>
        );
      })()}

      {/* Financial Risk Pipeline — JE velocity / liquidity shift / inventory divergence */}
      {forecasts?.financialRiskPipeline && (() => {
        const frp = forecasts.financialRiskPipeline;
        const cards = [
          { key: "je_velocity", label: "Manual JE Velocity", data: frp.je_velocity, flag: "anomaly",
            detail: d => `z = ${d.z_score}σ (${d.recent_daily_rate}/day vs. baseline ${d.baseline_daily_mean}/day)` },
          { key: "liquidity_shift", label: "Liquidity Shift", data: frp.liquidity_shift, flag: "shift_detected",
            detail: d => `worst QoQ z = ${d.worst_z_score}σ` },
          { key: "inventory_divergence", label: "Inventory/Sales Divergence", data: frp.inventory_divergence, flag: "divergence_detected",
            detail: d => `QoQ ratio z = ${d.z_score}σ` },
        ].filter(c => c.data && c.data.interpretation !== "insufficient_data" && c.data.interpretation !== "insufficient_baseline");

        if (!cards.length) return null;

        return (
          <div className="stage-detail">
            <h5>Financial Risk Pipeline — journal-entry velocity, liquidity, and inventory signals beyond the point-in-time Z/M-Score snapshot above</h5>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 8, marginTop: 8 }}>
              {cards.map(c => {
                const flagged = !!c.data[c.flag];
                const tone = flagged ? (c.data.rag_status === "Red" ? "red" : "amber") : "green";
                return (
                  <div key={c.key} style={{
                    padding: "8px 10px", borderRadius: 6, border: "1px solid var(--line)",
                    background: tone === "red" ? "var(--red-soft)" : tone === "amber" ? "var(--amber-soft)" : "var(--surface-2,var(--surface))",
                  }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: tone === "red" ? "var(--red-ink)" : tone === "amber" ? "var(--amber-ink)" : "var(--ink-2)" }}>
                      {c.label}
                    </div>
                    <div style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 2 }}>
                      {flagged ? "Flagged — " : "Normal — "}{c.detail(c.data)}
                    </div>
                  </div>
                );
              })}
            </div>
            {cards.some(c => c.data[c.flag]) && onAddObjective && (
              <AuditorTakeaway
                tone="amber"
                actionLabel="Add to scope"
                onAction={() => onAddObjective(
                  `Review ${cards.filter(c => c.data[c.flag]).map(c => c.label.toLowerCase()).join(", ")} — Financial Risk Pipeline flagged a statistically significant deviation from historical baseline.`
                )}
              >
                One or more Financial Risk Pipeline checks flagged a deviation beyond the company's own historical noise — worth a targeted review this cycle.
              </AuditorTakeaway>
            )}
          </div>
        );
      })()}

      {/* Revenue forecast chart */}
      {forecasts?.revenue?.history?.length > 0 && forecasts?.revenue?.forecast?.length > 0 && (() => {
        const FC = window.ForecastChart;
        if (!FC) return null;
        return (
          <div className="stage-detail">
            <h5>Revenue Growth Risk — is growth decelerating, reversing, or masking concentration?</h5>
            <div style={{fontSize:10.5, color:"var(--ink-3)", marginBottom:8}}>
              Quarterly revenue trend (EDGAR 10-K + 10-Q) with 4-quarter AI forecast. Positive/negative revenue momentum feeds velocity adjustments in Stage 2 risk scores.
            </div>
            <FCWithMetrics history={forecasts.revenue.history} forecast={forecasts.revenue.forecast} unit="$M" decimals={2}/>
            {forecasts.revenue.monteCarlo && (
              <div style={{fontSize:10.5, color:"var(--ink-3)", marginTop:6, display:"flex", gap:14, flexWrap:"wrap"}}>
                <span>Monte Carlo · {forecasts.revenue.monteCarlo.nSims} sims</span>
                <span>QoQ volatility <span style={{color:"var(--ink-2)"}}>{forecasts.revenue.monteCarlo.volatilityPct}%</span></span>
                <span>P(revenue decline by Q4) <span style={{color: forecasts.revenue.monteCarlo.probDecline > 0.4 ? "var(--red-ink)" : "var(--ink-2)"}}>{(forecasts.revenue.monteCarlo.probDecline * 100).toFixed(0)}%</span></span>
                <span>Bands = 10th/90th percentile of simulated paths, not a fixed ±%</span>
              </div>
            )}
            {forecasts.revenue.monteCarlo && (() => {
              const pd = forecasts.revenue.monteCarlo.probDecline;
              const material = pd > 0.4; // same threshold already used to color the span above red
              return (
                <AuditorTakeaway
                  tone={material ? "red" : "green"}
                  actionLabel={material ? "Add to scope" : undefined}
                  onAction={material && onAddObjective ? () => onAddObjective(
                    `Assess revenue concentration and growth durability — Monte Carlo model shows a ${(pd * 100).toFixed(0)}% probability of Q4 revenue decline.`
                  ) : undefined}
                >
                  {material
                    ? `Elevated downside risk — ${(pd * 100).toFixed(0)}% simulated probability of a Q4 revenue decline warrants a concentration/durability review this cycle.`
                    : "Downside probability within normal range — no elevated revenue risk from this forecast."}
                </AuditorTakeaway>
              );
            })()}
          </div>
        );
      })()}

      {/* Gross margin forecast chart */}
      {forecasts?.margin?.history?.length > 0 && forecasts?.margin?.forecast?.length > 0 && (() => {
        const FC = window.ForecastChart;
        if (!FC) return null;
        return (
          <div className="stage-detail">
            <h5>Margin Compression Risk — cost pressure and earnings-quality flag (pairs with M-Score above)</h5>
            <div style={{fontSize:10.5, color:"var(--ink-3)", marginBottom:8}}>
              Margin trend from EDGAR COGS data. Compression below 10% flags Beneish GMI risk and raises the inherent score on financial-reporting risks.
            </div>
            <FCWithMetrics history={forecasts.margin.history} forecast={forecasts.margin.forecast} unit="%" color="var(--amber)"/>
            {(() => {
              const lastF = forecasts.margin.forecast.slice(-1)[0]?.base;
              if (lastF == null) return null;
              const material = lastF < 10; // same 10% GMI-risk threshold stated above
              return (
                <AuditorTakeaway
                  tone={material ? "amber" : "green"}
                  actionLabel={material ? "Add to scope" : undefined}
                  onAction={material && onAddObjective ? () => onAddObjective(
                    `Review cost structure and margin trend — gross margin is forecast to compress to ${lastF.toFixed(1)}%, below the 10% Beneish GMI-risk threshold.`
                  ) : undefined}
                >
                  {material
                    ? `Forecast margin (${lastF.toFixed(1)}%) is below the 10% threshold that flags Beneish GMI risk — worth a cost-structure review this cycle.`
                    : "Forecast margin stays above the GMI-risk threshold — no elevated financial-reporting risk from this signal."}
                </AuditorTakeaway>
              );
            })()}
          </div>
        );
      })()}

      {/* EPS forecast chart */}
      {forecasts?.eps?.history?.length > 0 && forecasts?.eps?.forecast?.length > 0 && (() => {
        const FC = window.ForecastChart;
        if (!FC) return null;
        const lastH = forecasts.eps.history.slice(-1)[0]?.v;
        const lastF = forecasts.eps.forecast.slice(-1)[0]?.base;
        return (
          <div className="stage-detail">
            <h5>Earnings Trend Risk — compression signals reporting and liquidity stress</h5>
            <div style={{fontSize:10.5, color:"var(--ink-3)", marginBottom:8}}>
              Earnings per share trend. Forecast: ${lastF?.toFixed(2)} · 4Q out.
              Persistent EPS compression raises financial-reporting and liquidity risk scores.
            </div>
            <FCWithMetrics history={forecasts.eps.history.slice(-16)} forecast={forecasts.eps.forecast} unit="$" color="var(--acc)"/>
          </div>
        );
      })()}

      {/* Operating Margin forecast */}
      {forecasts?.opMargin?.history?.length > 0 && forecasts?.opMargin?.forecast?.length > 0 && (() => {
        const FC = window.ForecastChart;
        if (!FC) return null;
        const lastF = forecasts.opMargin.forecast.slice(-1)[0]?.base;
        return (
          <div className="stage-detail">
            <h5>Operating Efficiency Risk — contraction signals cost or competitive pressure</h5>
            <div style={{fontSize:10.5, color:"var(--ink-3)", marginBottom:8}}>
              EBIT ÷ Revenue. Forecast: {lastF?.toFixed(2)}%. Margin contraction feeds Stage 2 operational-risk velocity adjustments.
            </div>
            <FCWithMetrics history={forecasts.opMargin.history.slice(-16)} forecast={forecasts.opMargin.forecast} unit="%" color="#e8a838"/>
          </div>
        );
      })()}

      {/* EBITDA forecast */}
      {forecasts?.ebitda?.history?.length > 0 && forecasts?.ebitda?.forecast?.length > 0 && (() => {
        const FC = window.ForecastChart;
        if (!FC) return null;
        const lastF = forecasts.ebitda.forecast.slice(-1)[0]?.base;
        return (
          <div className="stage-detail">
            <h5>Debt-Covenant Risk — leverage service capacity (pairs with Free Cash Flow below)</h5>
            <div style={{fontSize:10.5, color:"var(--ink-3)", marginBottom:8}}>
              Operating Income + D&A. Forecast: ${lastF?.toFixed(0)}M. Used as a proxy for operating cash generation in debt-covenant risk scoring.
            </div>
            <FCWithMetrics history={forecasts.ebitda.history.slice(-16)} forecast={forecasts.ebitda.forecast} unit="$M" color="var(--violet)"/>
          </div>
        );
      })()}

      {/* Net Income forecast */}
      {forecasts?.netIncome?.history?.length > 0 && forecasts?.netIncome?.forecast?.length > 0 && (() => {
        const FC = window.ForecastChart;
        if (!FC) return null;
        const lastF = forecasts.netIncome.forecast.slice(-1)[0]?.base;
        return (
          <div className="stage-detail">
            <h5>Profitability Risk — net-loss quarters signal liquidity stress (compare with FCF below)</h5>
            <div style={{fontSize:10.5, color:"var(--ink-3)", marginBottom:8}}>
              GAAP bottom line. Forecast: ${lastF?.toFixed(0)}M. Net loss quarters trigger inherent score uplift on liquidity and financial-reporting risks.
            </div>
            <FC history={forecasts.netIncome.history.slice(-16)} forecast={forecasts.netIncome.forecast} unit="$M" color="var(--acc)"/>
          </div>
        );
      })()}

      {/* Free Cash Flow forecast */}
      {forecasts?.fcf?.history?.length > 0 && forecasts?.fcf?.forecast?.length > 0 && (() => {
        const FC = window.ForecastChart;
        if (!FC) return null;
        const lastF = forecasts.fcf.forecast.slice(-1)[0]?.base;
        return (
          <div className="stage-detail">
            <h5>Liquidity Risk — negative cash-flow streaks signal cash-runway stress (compare with Net Income above)</h5>
            <div style={{fontSize:10.5, color:"var(--ink-3)", marginBottom:8}}>
              CFO − CapEx. Forecast: ${lastF?.toFixed(0)}M. Negative FCF for two or more consecutive quarters escalates liquidity risk to HIGH.
            </div>
            <FC history={forecasts.fcf.history.slice(-16)} forecast={forecasts.fcf.forecast} unit="$M" color="#4aad52"/>
          </div>
        );
      })()}

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
              {narrativeResult.summary}
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
                  allSignals = [], onOpenAdjustRisk, onRerunFromS3, forecasts,
                  liveMode = false, livefacts = null, fredSeries = null, industry = "", ticker = "" }) {
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
  const eightKSigsS2 = allSignals.filter(s => s.src === "SEC 8-K");

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
            const eightKLinked = eightKSigsS2.filter(s => {
              const rCat = (r.category || "").toLowerCase();
              const sCat = (s.category || "").toLowerCase();
              return sCat && (rCat.includes(sCat) || sCat.includes(rCat));
            });
            const eightKAdj = Math.min(1.5, eightKLinked.reduce(
              (sum, s) => sum + (s.severity === "P1" ? 0.5 : s.severity === "P2" ? 0.25 : 0.1), 0
            ));
            const totalAdj = rssAdj + fredAdj + eightKAdj + (industryAdj > 0 && rssLinked.length === 0 ? industryAdj : 0);
            const hasSigs = rssLinked.length > 0 || fredAdj > 0 || eightKAdj > 0 || (highVelIndustry > 0 && industryAdj > 0);
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
                      {sigOpen ? "▲" : "▼"} {rssLinked.length + (fredAdj > 0 ? 1 : 0) + eightKLinked.length + (industryAdj > 0 && rssLinked.length === 0 ? 1 : 0)} signal{(rssLinked.length + (fredAdj > 0 ? 1 : 0) + eightKLinked.length) !== 1 ? "s" : ""} driving score
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
                        {eightKLinked.map((s,i) => (
                          <div key={`8k-${i}`} className="s2-sig-ev-item">
                            <span className="s2-sig-ev-src" style={{color:"var(--orange-ink,var(--red-ink))"}}>SEC 8-K</span>
                            <span style={{flex:1, fontSize:10.5, color:"var(--ink-2)"}}>{s.label}</span>
                            <span className="mono" style={{fontSize:9, padding:"1px 5px", borderRadius:3,
                              background: s.severity === "P1" ? "var(--red-soft)" : s.severity === "P2" ? "var(--amber-soft)" : "var(--surface-2,var(--surface))",
                              color:      s.severity === "P1" ? "var(--red-ink)"  : s.severity === "P2" ? "var(--amber-ink)"  : "var(--ink-3)",
                              marginRight: 4}}>{s.severity}</span>
                            <span className="s2-sig-ev-adj">+{(s.severity === "P1" ? 0.5 : s.severity === "P2" ? 0.25 : 0.1).toFixed(2)}</span>
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
            <span className="mono" style={{fontSize:10, color:"var(--ink-2)"}}>inherent + macro adj. + industry-signal adj. + 8-K adj. + industry adj. − control-effectiveness discount</span>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)", marginLeft:12}}>8K_adj: P1 +0.5 · P2 +0.25 · P3 +0.10 per category match (cap +1.5)</span>
          </div>
          <div style={{padding:"6px 10px", background:"var(--surface-2,var(--surface))", borderRadius:5, border:"1px solid var(--line)"}}>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>CE discount: </span>
            <span className="mono" style={{fontSize:10, color:"var(--ink-4)"}}>STRONG −0.7 · ADEQUATE −0.3 · WEAK +0.1 · NONE +0.4</span>
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
                  <th title="Control Effectiveness" style={{textAlign:"left", padding:"4px 6px", color:"var(--ink-4)", fontSize:9.5, fontWeight:500, letterSpacing:"0.05em", fontFamily:"var(--mono)"}}>CE</th>
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
            Forecast used by Stage 3 to prioritize fiscal-quarter allocation · high-trajectory risks earn P1 objectives regardless of current RAG
          </div>
        </div>
      )}

      {/* Top-risk 4-quarter projection — anchored at current score, no synthetic history */}
      {risks.length > 0 && (() => {
        const FC = window.ForecastChart;
        if (!FC) return null;
        const topRisk = [...risks].sort((a, b) => b.score - a.score)[0];
        if (!topRisk) return null;
        const qs = forecastRisk(topRisk, 4);
        // Same per-risk-override-over-overall-appetite precedence used by the
        // control list above (line ~1317) — the tolerance line should match
        // whichever threshold this specific risk is actually held to.
        const topLevel = perRiskAppetite[topRisk.id] || appetiteLevel;
        const topThreshold = APPETITE_THRESHOLDS[topLevel] ?? overallThreshold;
        // Dynamic quarter labels from today's date — avoids hardcoded year drift
        const now = new Date();
        const curQ = Math.ceil((now.getMonth() + 1) / 3);
        const curY = now.getFullYear();
        const qLabel = offset => {
          const q = ((curQ - 1 + offset) % 4) + 1;
          const y = curY + Math.floor((curQ - 1 + offset) / 4);
          return `Q${q}-${String(y).slice(2)}`;
        };
        const hist  = [{ q: "Now", v: topRisk.score }];
        const fcast = [1, 2, 3, 4].map((o, i) => ({
          q:    qLabel(o),
          base: qs[i],
          lo:   +Math.max(0, qs[i] - 1.5).toFixed(1),
          hi:   +Math.min(25, qs[i] + 1.5).toFixed(1),
        }));
        return (
          <div className="stage-detail">
            <h5>{topRisk.name} — on track to breach risk tolerance?</h5>
            <div style={{fontSize:10.5, color:"var(--ink-3)", marginBottom:8}}>
              Current score {topRisk.score.toFixed(1)} ({topRisk.rag}) with 4-quarter velocity-dampened projection. Dashed line = forecast used by Stage 3 to prioritise audit objectives. Confidence band ±1.5 pts. Red threshold = risk tolerance ({topLevel}, {topThreshold.toFixed(1)}).
            </div>
            <FC
              history={hist}
              forecast={fcast}
              unit="score"
              color={topRisk.rag === "R" ? "var(--red)" : topRisk.rag === "A" ? "var(--amber)" : "var(--green)"}
              referenceValue={topThreshold}
              referenceLabel="Risk tolerance"
            />
          </div>
        );
      })()}

      {/* Revenue momentum table — how QoQ revenue change feeds risk velocity */}
      {forecasts?.revenue?.history?.length > 1 && (() => {
        const recent = forecasts.revenue.history.slice(-5);
        const rows = recent.slice(1).map((d, i) => {
          const prev = recent[i];
          const qoq = prev.v ? ((d.v - prev.v) / Math.abs(prev.v)) * 100 : null;
          return { q: d.q, v: d.v, qoq };
        }).filter(r => r.qoq != null);
        if (!rows.length) return null;
        const avgQoQ = rows.reduce((s, r) => s + r.qoq, 0) / rows.length;
        const trend  = avgQoQ > 1 ? "positive" : avgQoQ < -1 ? "negative" : "flat";
        const trendColor = trend === "positive" ? "var(--green-ink)" : trend === "negative" ? "var(--red-ink)" : "var(--ink-3)";
        return (
          <div className="stage-detail">
            <h5>Revenue momentum · velocity feed</h5>
            <div style={{fontSize:10.5, color:"var(--ink-3)", marginBottom:8}}>
              QoQ revenue change from Stage 1 EDGAR data. Contraction amplifies velocity on financial-reporting and supply-chain risks; growth suppresses it.
            </div>
            <table style={{width:"100%", fontSize:10.5, borderCollapse:"collapse"}}>
              <thead>
                <tr>
                  {["QUARTER","REVENUE","QoQ %","VELOCITY FEED"].map((h, hi) => (
                    <th key={h} className="mono" style={{
                      textAlign: hi === 0 ? "left" : "right",
                      color:"var(--ink-3)", fontWeight:500, padding:"2px 4px",
                      fontSize:9, letterSpacing:"0.05em", whiteSpace:"nowrap",
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map(r => {
                  const rising = r.qoq > 1, falling = r.qoq < -1;
                  const c = rising ? "var(--green-ink)" : falling ? "var(--red-ink)" : "var(--ink-3)";
                  return (
                    <tr key={r.q} style={{borderTop:"1px solid var(--line)"}}>
                      <td className="mono" style={{padding:"4px 4px", color:"var(--ink-3)", fontSize:10}}>{r.q}</td>
                      <td className="mono" style={{textAlign:"right", padding:"4px 4px", color:"var(--ink)", fontSize:10}}>
                        {r.v >= 1000 ? `$${(r.v / 1000).toFixed(1)}B` : `$${r.v.toFixed(0)}M`}
                      </td>
                      <td className="mono" style={{textAlign:"right", padding:"4px 4px", color:c, fontSize:10}}>
                        {rising ? "▲" : falling ? "▼" : "→"} {Math.abs(r.qoq).toFixed(1)}%
                      </td>
                      <td className="mono" style={{textAlign:"right", padding:"4px 4px", fontSize:10, color:c, whiteSpace:"nowrap"}}>
                        {rising ? "↓ suppresses" : falling ? "↑ amplifies" : "→ neutral"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div style={{marginTop:8, fontSize:10, color:"var(--ink-3)"}}>
              Avg QoQ <span style={{color:trendColor, fontWeight:500}}>
                {avgQoQ >= 0 ? "+" : ""}{avgQoQ.toFixed(1)}%
              </span> — {trend === "positive" ? "velocity suppressed" : trend === "negative" ? "velocity amplified" : "neutral impact"}
            </div>
          </div>
        );
      })()}

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
        <h5>Fiscal-quarter-ready objectives</h5>
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

  const redMaps = maps.filter(m => m.risk_impact === "R").length;
  const avgCompletion = Math.round(maps.reduce((a,m) => a + (m.completion_pct || 0), 0) / Math.max(1, maps.length));

  return (
    <div className="stage-body-grid">
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

function S5Body({ output, flowMeta = null, risks = [], maps = [], onOpenMain = null }) {
  const c = output?.closure || {};

  return (
    <div className="stage-body-grid">
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

  const lessons = l.lessons_learned || [];

  return (
    <div className="stage-body-grid">
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
            <span style={{fontSize:10.5, color:"var(--ink-2)"}}>velocity decay factors and control-effectiveness multipliers updated from observed outcomes — fed back to Stage 1 on next run</span>
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
