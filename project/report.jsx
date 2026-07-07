/* ============================================================
   Loop Report modal + Override (HITL) modal
   ============================================================ */

function ReportModal({ open, onClose, payload }) {
  // Hooks must run unconditionally — declare before the early return.
  const [aiReport, setAiReport] = React.useState({ loading: false, error: null, markdown: null });
  React.useEffect(() => { setAiReport({ loading: false, error: null, markdown: null }); }, [payload?.ts]);

  if (!open || !payload) return null;
  const {
    entity, ticker, runId, ts, cfg, signals, risks, baseRisks, top3, riskAppetite,
    objectives, maps, closure, loop, scenarios, greySwan, personas,
    fredSeries, fredContrCount, rssHighVelCount, rssLinkedCount, liveMode,
    riskApprovals, scopeApprovals,
    assumptions, obstacles, log,
    stageState = {}, stageOutput = {},
  } = payload;

  const adjRisks = risks.filter(r => riskApprovals?.[r.id]?.adjustments && riskApprovals?.[r.id]?.status !== "pending");
  const adjObjs  = objectives.filter(o => scopeApprovals?.[o.id]?.adjustments && scopeApprovals?.[o.id]?.status !== "pending");

  // #4 — Generate a board-ready narrative audit report with Claude.
  const aiAvailable = typeof window !== "undefined" && window.MCP?.aiAuditReport;
  async function generateAiReport() {
    if (!aiAvailable) return;
    setAiReport({ loading: true, error: null, markdown: null });
    try {
      const res = await window.MCP.aiAuditReport(
        ticker || entity,
        { risks, objectives, maps, loop }, runId || null,
      );
      setAiReport({ loading: false, error: null, markdown: res?.markdown || "" });
    } catch (e) {
      const raw = e.message || "";
      const isBilling = raw.includes("402") || raw.includes("credit") || raw.includes("Credits");
      const friendly = isBilling
        ? "BILLING: Anthropic API credits exhausted — add credits at console.anthropic.com/settings/billing"
        : raw || "AI unavailable";
      setAiReport({ loading: false, error: friendly, markdown: null });
    }
  }

  return (
    <div className="modal open" onClick={(e) => { if (e.target.classList.contains("modal")) onClose(); }}>
      <div className="modal-box" style={{width: 920}}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Loop Report</div>
            <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", marginTop: 3}}>Generated {new Date(ts).toLocaleString()}</div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
        </div>
        <div className="modal-body">
          <div className="rep-h1">{entity}</div>
          <div className="rep-h1-sub">{cfg.industry} · {(Array.isArray(cfg.focus) ? cfg.focus : [cfg.focus]).join(" · ")} · {new Date(ts).toLocaleDateString()}</div>

          {/* ── AI Narrative (#4) ────────────────────────────── */}
          {aiReport.error && (() => {
            const isBilling = aiReport.error.startsWith("BILLING:");
            const msg = isBilling ? aiReport.error.slice("BILLING: ".length) : aiReport.error;
            return (
              <div style={{
                margin: "10px 0", padding: "10px 14px", borderRadius: 6,
                background: isBilling ? "var(--amber-soft, #fff8e6)" : "var(--red-soft, #fff0f0)",
                border: `1px solid ${isBilling ? "var(--amber, #e8a838)" : "var(--red, #e05252)"}`,
                display: "flex", alignItems: "flex-start", gap: 10,
              }}>
                <span style={{fontSize: 16, lineHeight: 1}}>{isBilling ? "⚠" : "✕"}</span>
                <div>
                  <div style={{fontSize: 11.5, fontWeight: 600,
                    color: isBilling ? "var(--amber-ink, #92600a)" : "var(--red-ink, #b93333)"}}>
                    {isBilling ? "API Credits Required" : "AI Report Unavailable"}
                  </div>
                  <div style={{fontSize: 11, color: "var(--ink-2)", marginTop: 3}}>{msg}</div>
                </div>
              </div>
            );
          })()}
          {aiReport.markdown && (
            <div className="rep-section">
              <h3>AI Narrative Report <span className="mono" style={{fontSize: 10, color: "var(--acc-ink)", fontWeight: 400}}>· Claude-generated</span></h3>
              <div style={{whiteSpace: "pre-wrap", fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.7,
                background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 8, padding: "14px 16px"}}>
                {aiReport.markdown}
              </div>
            </div>
          )}

          {/* ── Executive Summary ────────────────────────────── */}
          <div className="rep-section">
            <h3>Executive Summary</h3>
            <Row k="Entity"           v={entity}/>
            <Row k="Industry"         v={cfg.industry}/>
            <Row k="Audit Focus"      v={(Array.isArray(cfg.focus) ? cfg.focus : [cfg.focus]).join(", ")}/>
            <Row k="Signal Sources"   v={cfg.sigs.join(", ")}/>
            <Row k="Signals Ingested" v={`${signals.count} total · ${signals.highVel} high velocity`}/>
            <Row k="Risks Identified" v={`${risks.length}`}/>
            <Row k="Top 3 Risks"      v={top3.join(", ")}/>
            <Row k="Risk Appetite" v={
              <span style={{display:"flex", alignItems:"center", gap:10, flexWrap:"wrap"}}>
                <RAGChip rag={(riskAppetite?.status || riskAppetite) === "BREACHED" ? "R" : "G"}>{riskAppetite?.status || riskAppetite}</RAGChip>
                {riskAppetite?.threshold != null && (
                  <span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>
                    threshold ≥ {riskAppetite.threshold} · {riskAppetite.breaching?.length || 0} risk{(riskAppetite.breaching?.length || 0) !== 1 ? "s" : ""} breach{(riskAppetite.breaching?.length || 0) !== 1 ? "" : ""}
                    {riskAppetite.breaching?.length > 0 && ` (${riskAppetite.breaching.join(", ")})`}
                  </span>
                )}
              </span>
            }/>
            <Row k="Audit Objectives" v={`${objectives.length} · ${objectives.filter(o=>o.priority==="P1").length} P1`}/>
            <Row k="MAPs"             v={`${maps.length} generated · ${loop.maps_open || 0} open`}/>
            <Row k="Projected Reduction" v={`${closure.projected_total_risk_reduction_pct || 0}%`}/>
            <Row k="Loop Health"      v={<><RAGChip rag={loop.loop_health || "A"}>{loop.loop_health}</RAGChip> <span className="muted mono" style={{marginLeft: 8}}>impact {loop.audit_impact_score}/25</span></>}/>
            <Row k="Data Mode"        v={liveMode ? "Live (EDGAR + FRED)" : "Mock / Simulated"}/>
          </div>

          {/* ── Pipeline Execution ───────────────────────────── */}
          <div className="rep-section">
            <h3>Pipeline Execution</h3>
            <PipelineStagesReport stageState={stageState} stageOutput={stageOutput}/>
          </div>

          {/* ── Methodology ──────────────────────────────────── */}
          <div className="rep-section">
            <h3>Methodology</h3>

            <RepSubhead>Forecast Development</RepSubhead>
            <div style={{fontSize:12, color:"var(--ink-2)", lineHeight:1.65, marginBottom:10}}>
              Individual risk score projections use a <b>velocity-dampened linear model</b> applied per risk:
              <span className="mono" style={{display:"block", background:"var(--surface-2)", border:"1px solid var(--line)", borderRadius:6, padding:"6px 10px", margin:"7px 0", fontSize:11}}>
                Q<em>n</em> score = base + (velocity × CE_mult × 1.0 × 0.85^(n−1)), capped [1, 25]
              </span>
              The 0.85 decay factor reduces velocity impact by 15% each quarter, preventing indefinite trend extrapolation.
              Control-effectiveness multipliers modulate velocity: <b>NONE ×1.20, WEAK ×1.10, ADEQUATE ×0.95, STRONG ×0.80</b>.
            </div>
            <div style={{fontSize:12, color:"var(--ink-2)", lineHeight:1.65, marginBottom:4}}>
              Macro-level forecasting uses a <b>three-model ensemble</b> — ARIMA, Prophet, and Random Forest — with FRED macro series
              as exogenous features. Ensemble weights update iteratively by inverse mean absolute percentage error (MAPE) from backtesting,
              so the best-performing model on recent history receives the highest weight. Random Forest features include lags 1–4,
              rolling mean and standard deviation, time index, quarter dummies, and current FRED indicator readings.
            </div>

            <RepSubhead style={{marginTop:14}}>Risk Velocity Calculation</RepSubhead>
            <div style={{fontSize:12, color:"var(--ink-2)", lineHeight:1.65, marginBottom:8}}>
              Velocity is a composite integer in [−2, +5] representing the expected quarter-on-quarter score change.
              The signal-adjustment process adds three components to each risk's base velocity:
            </div>
            <table className="rep-table" style={{marginBottom:10}}>
              <thead><tr><th>Component</th><th>Formula</th><th>Scope</th><th>This run</th></tr></thead>
              <tbody>
                <tr>
                  <td><b>FRED macro</b></td>
                  <td className="mono">+0.20 per contractionary signal</td>
                  <td>Macro-category risks only</td>
                  <td className="mono">{fredContrCount} signal{fredContrCount !== 1 ? "s" : ""} → +{(fredContrCount * 0.08).toFixed(2)} max</td>
                </tr>
                <tr>
                  <td><b>RSS-linked</b></td>
                  <td className="mono">signal_velocity × 0.20 per link</td>
                  <td>Directly linked risks; velocity = max(base, signal)</td>
                  <td className="mono">{rssLinkedCount} linked signal{rssLinkedCount !== 1 ? "s" : ""}</td>
                </tr>
                <tr>
                  <td><b>Industry pressure</b></td>
                  <td className="mono">+0.125 per signal ≥ v3, cap +0.50</td>
                  <td>All risks</td>
                  <td className="mono">{rssHighVelCount} high-vel signal{rssHighVelCount !== 1 ? "s" : ""} → +{Math.min(0.50, rssHighVelCount * 0.125).toFixed(2)}</td>
                </tr>
              </tbody>
            </table>
            <div style={{fontSize:12, color:"var(--ink-2)", lineHeight:1.65}}>
              Score adjustments are summed and capped at 25.0. RAG is recalculated post-adjustment:
              ≥15 → <b style={{color:"var(--red-ink)"}}>RED</b>, 9–14 → <b style={{color:"var(--amber-ink)"}}>AMBER</b>, &lt;9 → <b style={{color:"var(--green-ink)"}}>GREEN</b>.
            </div>
          </div>

          {/* ── Macro Indicators ─────────────────────────────── */}
          {fredSeries?.length > 0 && (
            <div className="rep-section">
              <h3>Macro Indicators</h3>
              <div style={{fontSize:12, color:"var(--ink-3)", marginBottom:10, lineHeight:1.55}}>
                FRED series used as exogenous features in ensemble forecasting and as velocity-adjustment signals.
                Pearson correlations (r) computed against company revenue over available history.
                Lead lag indicates how many quarters ahead the indicator moves relative to company KPIs.
              </div>
              <table className="rep-table">
                <thead>
                  <tr><th>Series ID</th><th>Indicator</th><th>Correlation (r)</th><th>Lead</th><th>Signal</th><th>Current Reading</th></tr>
                </thead>
                <tbody>
                  {fredSeries.map(f => {
                    const strength = Math.abs(f.r) >= 0.75 ? "var(--emerald-ink)" : Math.abs(f.r) >= 0.60 ? "var(--amber-ink)" : "var(--ink-3)";
                    return (
                      <tr key={f.id}>
                        <td className="mono" style={{fontSize:10}}>{f.id}</td>
                        <td>{f.name}</td>
                        <td className="mono" style={{color: strength, fontWeight: Math.abs(f.r) >= 0.75 ? 600 : 400}}>
                          {f.r > 0 ? "+" : ""}{f.r.toFixed(2)}
                        </td>
                        <td className="mono">{f.lead}Q</td>
                        <td>
                          <span style={{fontSize:11, fontWeight:500, color: f.dir === "CONTRACTIONARY" ? "var(--red-ink)" : f.dir === "NEUTRAL" ? "var(--ink-3)" : "var(--green-ink)"}}>
                            {f.dir}
                          </span>
                        </td>
                        <td className="mono" style={{color: f.dir === "CONTRACTIONARY" ? "var(--red-ink)" : "var(--ink-2)"}}>{f.reading}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <div className="mono" style={{fontSize:10, color:"var(--ink-4)", marginTop:6}}>
                r ≥ 0.75 = strong (green) · r 0.60–0.74 = moderate (amber) · r &lt; 0.60 = weak (grey)
              </div>
            </div>
          )}

          {/* ── Changes to Risks & Audit Plan ────────────────── */}
          {(adjRisks.length > 0 || adjObjs.length > 0) && (
            <div className="rep-section">
              <h3>Changes to Risks &amp; Audit Plan</h3>
              <div style={{fontSize:12, color:"var(--ink-3)", marginBottom:12, lineHeight:1.55}}>
                The following items were modified by auditors through HITL gate review.
                All changes are captured verbatim in the audit trail below.
              </div>

              {adjRisks.length > 0 && (
                <>
                  <RepSubhead>Risk Register Adjustments · Gate 1</RepSubhead>
                  {adjRisks.map(r => {
                    const a = riskApprovals[r.id];
                    const orig = baseRisks?.find(b => b.id === r.id);
                    return (
                      <div key={r.id} className="rep-finding A" style={{marginBottom:8}}>
                        <div style={{display:"flex", gap:8, alignItems:"center", marginBottom:6, flexWrap:"wrap"}}>
                          <b style={{fontWeight:500}}>{r.name}</b>
                          <span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>{r.id}</span>
                        </div>
                        {a?.adjustments && (
                          <div style={{display:"grid", gap:3}}>
                            {a.adjustments.score != null && orig && (
                              <Row k="Score" v={<span className="mono">{orig.score?.toFixed(1)} → <b style={{color:"var(--amber-ink)"}}>{a.adjustments.score?.toFixed(1)}</b></span>}/>
                            )}
                            {a.adjustments.rag && orig && (
                              <Row k="RAG" v={<span>{orig.rag} → <RAGChip rag={a.adjustments.rag}>{a.adjustments.rag}</RAGChip></span>}/>
                            )}
                            {a.adjustments.velocity != null && orig && (
                              <Row k="Velocity" v={<span className="mono">v{orig.velocity >= 0 ? "+" : ""}{orig.velocity} → <b>v{a.adjustments.velocity >= 0 ? "+" : ""}{a.adjustments.velocity}</b></span>}/>
                            )}
                            {a.adjustments.ce && orig && (
                              <Row k="Control Eff." v={`${orig.ce} → ${a.adjustments.ce}`}/>
                            )}
                          </div>
                        )}
                        {a?.rationale && <Row k="Auditor rationale" v={a.rationale}/>}
                      </div>
                    );
                  })}
                </>
              )}

              {adjObjs.length > 0 && (
                <>
                  <RepSubhead style={{marginTop: adjRisks.length > 0 ? 14 : 0}}>Audit Plan Adjustments · Gate 2</RepSubhead>
                  {adjObjs.map(o => {
                    const a = scopeApprovals[o.id];
                    return (
                      <div key={o.id} className="rep-finding A" style={{marginBottom:8}}>
                        <div style={{display:"flex", gap:8, alignItems:"center", marginBottom:6, flexWrap:"wrap"}}>
                          <b style={{fontWeight:500}}>{o.objective}</b>
                          <span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>{o.id}</span>
                        </div>
                        {a?.adjustments && (
                          <div style={{display:"grid", gap:3}}>
                            {a.adjustments.priority && <Row k="Priority" v={`${o.priority} → ${a.adjustments.priority}`}/>}
                            {a.adjustments.sprint   && <Row k="Fiscal Quarter"   v={`Q${o.sprint} → Q${a.adjustments.sprint}`}/>}
                            {a.adjustments.hours != null && <Row k="Hours" v={<span className="mono">{o.hours}h → <b>{a.adjustments.hours}h</b></span>}/>}
                          </div>
                        )}
                        {a?.rationale && <Row k="Auditor rationale" v={a.rationale}/>}
                      </div>
                    );
                  })}
                </>
              )}
            </div>
          )}

          <div className="rep-section">
            <h3>Risk Register · 4-Quarter Projections</h3>
            <table className="rep-table">
              <thead>
                <tr><th>Risk</th><th>RAG</th><th>Now</th><th>Vel</th><th>Q1</th><th>Q2</th><th>Q3</th><th>Q4</th><th>Control</th></tr>
              </thead>
              <tbody>
                {risks.map(r => {
                  const qs = projectQuarters(r);
                  return (
                    <tr key={r.id}>
                      <td><b style={{fontWeight: 500}}>{r.name}</b><div className="muted" style={{fontSize: 10}}>{r.id} · {r.category}</div></td>
                      <td><RAGChip rag={r.rag}>{r.rag}</RAGChip></td>
                      <td className="mono" style={{color: scoreColorInk(r.score)}}>{fmt2(r.score)}</td>
                      <td><VelocityPill v={r.velocity}/></td>
                      {qs.map((q, i) => (
                        <td key={i} className="mono" style={{color: scoreColorInk(q)}}>{fmt2(q)}</td>
                      ))}
                      <td className="mono muted" style={{fontSize: 10}}>{r.ce}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="rep-section">
            <h3>Audit Objectives</h3>
            {objectives.map(o => (
              <div key={o.id} className={`rep-finding ${o.priority === "P1" ? "R" : o.priority === "P2" ? "A" : "G"}`}>
                <div style={{display:"flex", alignItems:"center", gap: 8, marginBottom: 6}}>
                  <span className="mono" style={{fontSize: 10, padding: "2px 7px", borderRadius: 4, background: o.priority === "P1" ? "var(--red-soft)" : o.priority === "P2" ? "var(--amber-soft)" : "var(--green-soft)", color: o.priority === "P1" ? "var(--red-ink)" : o.priority === "P2" ? "var(--amber-ink)" : "var(--green-ink)"}}>{o.priority}</span>
                  <b style={{fontWeight: 500, fontSize: 13}}>{o.objective}</b>
                </div>
                <Row k="Linked Risk"      v={o.linked_risk}/>
                <Row k="Fiscal Qtr / Hours" v={`Q${o.sprint} / ${o.hours}h`}/>
                <Row k="Controls to Test" v={o.controls.join(", ")}/>
                <Row k="Rationale"        v={o.rationale}/>
              </div>
            ))}
          </div>

          <div className="rep-section">
            <h3>Management Action Plans</h3>
            {maps.map(m => (
              <div key={m.id} className={`rep-finding ${m.risk_impact}`}>
                <div style={{display:"flex", alignItems:"center", gap: 8, marginBottom: 6}}>
                  <RAGChip rag={m.risk_impact}>{m.risk_impact}</RAGChip>
                  <b style={{fontWeight: 500, fontSize: 13}}>{m.finding}</b>
                </div>
                <Row k="Condition"      v={m.condition}/>
                <Row k="Root Cause"     v={m.root_cause}/>
                <Row k="Action"         v={m.action}/>
                <Row k="Owner / Due"    v={`${m.owner} · ${m.due_date}`}/>
                <Row k="Success Criteria" v={m.success_criteria}/>
                <Row k="Expected Reduction" v={`${m.reduction_pct}%`}/>
                <Row k="Progress"       v={`${m.completion_pct}%`}/>
              </div>
            ))}
          </div>

          {scenarios?.length > 0 && (
            <div className="rep-section">
              <h3>Scenario Outlook</h3>
              <div style={{display: "grid", gap: 12}}>
                {scenarios.map((s, idx) => (
                  <div key={s.id} className="rep-finding" style={{padding: 14, borderRadius: 10, border: "1px solid var(--line)"}}>
                    <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom: 8}}>
                      <div>
                        <div style={{fontWeight: 700, fontSize: 13}}>{s.name}</div>
                        <div className="mono" style={{fontSize: 11, color: "var(--ink-3)"}}>{s.probability} probability · Revenue impact {s.revenue_impact_pct > 0 ? `+${s.revenue_impact_pct}%` : `${s.revenue_impact_pct}%`}</div>
                      </div>
                      <div className="mono" style={{fontSize: 11, color: "var(--ink-3)"}}>{s.runway_days} days runway</div>
                    </div>
                    <div style={{fontSize: 12, color: "var(--ink-2)", marginBottom: 8}}>{s.description}</div>
                    <Row k="Audit focus" v={s.audit_focus?.join(", ") || "—"}/>
                    <Row k="Key assumptions" v={Object.entries(s.assumptions || {}).map(([k,v]) => `${k}: ${v}`).join(" · ") || "—"}/>
                  </div>
                ))}
              </div>
            </div>
          )}

          {greySwan && (
            <div className="rep-section">
              <h3>Grey Swan Risk</h3>
              <div style={{display: "grid", gap: 14}}>
                <div style={{background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 10, padding: 14}}>
                  <div style={{display:"flex", justifyContent:"space-between", gap: 12, flexWrap:"wrap"}}>
                    <div>
                      <div style={{fontWeight:700, fontSize:14, marginBottom: 6}}>{greySwan.headline}</div>
                      <div style={{fontSize: 12, color: "var(--ink-2)", lineHeight: 1.5}}>{greySwan.description}</div>
                    </div>
                    <div style={{display:"grid", gap:6, minWidth: 160}}>
                      <span className="mono" style={{fontSize: 11, color: "var(--ink-3)"}}>Probability</span>
                      <div style={{padding: "8px 10px", borderRadius: 8, background: "var(--surface-1)", border: "1px solid var(--line)", fontWeight: 700}}>{greySwan.probability}</div>
                      <span className="mono" style={{fontSize: 11, color: "var(--ink-3)"}}>Scenario path</span>
                      <div style={{padding: "8px 10px", borderRadius: 8, background: "var(--surface-1)", border: "1px solid var(--line)", fontWeight: 700}}>{greySwan.starting_rag} → {greySwan.ending_rag}</div>
                    </div>
                  </div>
                </div>

                {greySwan.timeline?.length > 0 && (
                  <div style={{overflowX: "auto"}}>
                    <table className="rep-table" style={{minWidth: 760}}>
                      <thead>
                        <tr><th>Stage</th><th>Score / RAG</th><th>Impact</th><th>Signals</th><th>Action</th></tr>
                      </thead>
                      <tbody>
                        {greySwan.timeline.map((t, i) => (
                          <tr key={i}>
                            <td><b>{t.label}</b><div className="mono" style={{fontSize:10,color:"var(--ink-3)"}}>{t.t}</div></td>
                            <td className="mono" style={{color: scoreColorInk(t.score)}}>{t.score.toFixed(1)} · {t.rag}</td>
                            <td>{t.impact}</td>
                            <td style={{fontSize:11, color:"var(--ink-2)"}}>{Array.isArray(t.signals) ? t.signals.join(" · ") : t.signals}</td>
                            <td style={{fontSize:11, color:"var(--ink-2)"}}>{t.action}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {(greySwan.early_warnings?.length > 0 || greySwan.mitigations?.length > 0) && (
                  <div style={{display:"grid", gap:12}}>
                    {greySwan.early_warnings?.length > 0 && (
                      <div>
                        <div style={{fontWeight:700, marginBottom:6}}>Early Warning Signals</div>
                        <ul style={{margin:0, paddingLeft:18, color:"var(--ink-2)", fontSize:12, lineHeight:1.6}}>
                          {greySwan.early_warnings.map((item, idx) => <li key={idx}>{item}</li>)}
                        </ul>
                      </div>
                    )}
                    {greySwan.mitigations?.length > 0 && (
                      <div>
                        <div style={{fontWeight:700, marginBottom:6}}>Recommended Mitigations</div>
                        <ul style={{margin:0, paddingLeft:18, color:"var(--ink-2)", fontSize:12, lineHeight:1.6}}>
                          {greySwan.mitigations.map((item, idx) => <li key={idx}>{item}</li>)}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {personas && Object.keys(personas).length > 0 && (
            <div className="rep-section">
              <h3>Stakeholder Highlights</h3>
              <div style={{display: "grid", gap: 12}}>
                {Object.entries(personas).map(([role, details]) => (
                  <div key={role} className="rep-finding" style={{padding: 14, borderRadius: 10, border: "1px solid var(--line)"}}>
                    <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom: 8, flexWrap:"wrap"}}>
                      <div>
                        <div style={{fontWeight:700, fontSize:13}}>{role}</div>
                        <div className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{details.sections?.join(" · ")}</div>
                      </div>
                    </div>
                    <div style={{fontSize: 12, color: "var(--ink-2)", marginBottom: 8}}>{details.summary}</div>
                    <div style={{fontSize: 11, color: "var(--ink-3)", fontWeight: 700}}>{details.headline}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="rep-section">
            <h3>Analytical Assumptions</h3>
            <div style={{fontSize:12, color:"var(--ink-3)", marginBottom:10, lineHeight:1.55}}>
              The following assumptions governed scoring, projection, and signal-adjustment computations in this loop run.
            </div>
            {assumptions.map((a, i) => (
              <div key={i} style={{display:"flex", gap:10, padding:"6px 0", fontSize:12, color:"var(--ink-2)", lineHeight:1.6, borderBottom: i < assumptions.length - 1 ? "1px solid var(--line)" : "none"}}>
                <span className="mono" style={{color:"var(--ink-4)", flexShrink:0, width:20, paddingTop:1}}>{i+1}.</span>
                <span>{a}</span>
              </div>
            ))}
          </div>

          <div className="rep-section">
            <h3>Obstacles &amp; Flags</h3>
            {obstacles.length === 0
              ? <div style={{fontSize:12, color:"var(--green-ink)", display:"flex", alignItems:"center", gap:6}}>
                  <Icon name="check" size={13}/> No obstacles. All stages completed within expected parameters.
                </div>
              : obstacles.map((o, i) => (
                <div key={i} className="rep-finding A" style={{marginBottom:6, display:"flex", gap:8, alignItems:"flex-start"}}>
                  <Icon name="alert" size={12} style={{flexShrink:0, marginTop:2}}/>
                  <span style={{fontSize:12, lineHeight:1.55}}>{o}</span>
                </div>
              ))
            }
          </div>

          <div className="rep-section">
            <h3>Audit Trail</h3>
            <div className="mono rep-audit-trail" style={{fontSize: 11, background: "var(--surface-2)", border:"1px solid var(--line)", borderRadius: 8, padding: 12, maxHeight: 220, overflowY: "auto"}}>
              {log.map((e, i) => (
                <div key={i} style={{display:"grid", gridTemplateColumns: "82px 1fr", gap: 8, padding: "2px 0"}}>
                  <span style={{color: "var(--ink-3)"}}>{new Date(e.ts).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit"})}</span>
                  <span style={{color: "var(--ink-2)"}}>{e.msg}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="modal-foot">
          <span className="mono muted" style={{fontSize: 11}}>{loop.audit_impact_score ? `Audit impact ${loop.audit_impact_score}/25` : ""} · {risks.length} risks · {maps.length} MAPs</span>
          <div style={{display: "flex", gap: 6}}>
            {aiAvailable && (
              <button className="btn btn-sm" onClick={generateAiReport} disabled={aiReport.loading}
                title="Generate a board-ready narrative report with Claude">
                <Icon name="spark" size={11}/> {aiReport.loading ? "Generating…" : aiReport.markdown ? "Regenerate AI report" : "Generate AI report"}
              </button>
            )}
            <button className="btn btn-sm" onClick={() => window.print()}><Icon name="download" size={11}/> Print / PDF</button>
            <button className="btn btn-sm btn-primary" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div className="rep-row">
      <span className="rep-key">{k}</span>
      <span className="rep-val">{v}</span>
    </div>
  );
}

function RepSubhead({ children, style }) {
  return (
    <div style={{fontWeight:600, fontSize:12, color:"var(--ink)", margin:"12px 0 7px", ...style}}>
      {children}
    </div>
  );
}

// ------ Pipeline stages report section ------
const STAGE_META = [
  { id: "s1", name: "Signal Intake",                      desc: "10-K · peer filings · industry RSS · internal KRIs" },
  { id: "s2", name: "Risk Assessment + Velocity",         desc: "Continuous scoring · velocity delta · RAG matrix" },
  { id: "s3", name: "Audit Scope Generator",              desc: "Risk-linked audit plan · fiscal-quarter-ready workplan" },
  { id: "s4", name: "Findings → Management Action Plans", desc: "Root cause · owner · due date · success criteria" },
  { id: "s5", name: "Closure Evidence + Risk Reduction",  desc: "Quantified risk reduction · MAP completion" },
  { id: "s6", name: "Loop Calibration + Re-feed",         desc: "Updated register · velocity recalibration · lessons" },
];

function stageSummary(id, out) {
  if (!out) return null;
  if (id === "s1") {
    const sigs = out.signals || [];
    const high = sigs.filter(s => s.velocity >= 3).length;
    const bySrc = {};
    sigs.forEach(s => { bySrc[s.src] = (bySrc[s.src] || 0) + 1; });
    return { metrics: [
      { l: "Signals ingested", v: sigs.length },
      { l: "High velocity", v: high },
      { l: "Sources", v: out.sourceCount || Object.keys(bySrc).length },
    ], tasks: Object.entries(bySrc).map(([src, n]) => `${src}: ${n} signal${n !== 1 ? "s" : ""}`) };
  }
  if (id === "s2") {
    const risks = out.risks || [];
    const counts = risks.reduce((a, r) => { a[r.rag] = (a[r.rag] || 0) + 1; return a; }, {});
    return { metrics: [
      { l: "Risks scored", v: risks.length },
      { l: "RED", v: counts.R || 0 },
      { l: "AMBER", v: counts.A || 0 },
      { l: "GREEN", v: counts.G || 0 },
    ], tasks: risks.map(r => `${r.id} · ${r.name} — score ${r.score?.toFixed(1) ?? "—"} · ${r.rag} · v${r.velocity >= 0 ? "+" : ""}${r.velocity} · ${r.ce}`) };
  }
  if (id === "s3") {
    const objs = out.objectives || [];
    const p1 = objs.filter(o => o.priority === "P1").length;
    return { metrics: [
      { l: "Objectives", v: objs.length },
      { l: "P1", v: p1 },
      { l: "Total hours", v: objs.reduce((a, o) => a + (o.hours || 0), 0) + "h" },
    ], tasks: objs.map(o => `${o.id} [${o.priority}] · ${o.objective} — Q${o.sprint} / ${o.hours}h`) };
  }
  if (id === "s4") {
    const maps = out.maps || [];
    return { metrics: [
      { l: "MAPs generated", v: maps.length },
      { l: "Avg reduction", v: maps.length ? Math.round(maps.reduce((a, m) => a + (m.reduction_pct || 0), 0) / maps.length) + "%" : "—" },
    ], tasks: maps.map(m => `${m.id} · ${m.finding} — owner: ${m.owner} · due: ${m.due_date} · −${m.reduction_pct}%`) };
  }
  if (id === "s5") {
    const c = out.closure || {};
    return { metrics: [
      { l: "Risks closed", v: c.risks_closed || 0 },
      { l: "Risks reduced", v: c.risks_reduced || 0 },
      { l: "Projected reduction", v: (c.projected_total_risk_reduction_pct || 0) + "%" },
    ], tasks: [] };
  }
  if (id === "s6") {
    const l = out.loop || {};
    return { metrics: [
      { l: "Loop health", v: l.loop_health || "—" },
      { l: "Audit impact", v: l.audit_impact_score != null ? l.audit_impact_score + "/25" : "—" },
      { l: "Recommendations", v: (l.recommendations || []).length },
    ], tasks: (l.recommendations || []).map((r, i) => `${i + 1}. ${r}`) };
  }
  return null;
}

function PipelineStagesReport({ stageState, stageOutput }) {
  return (
    <div style={{display:"flex", flexDirection:"column", gap:12}}>
      {STAGE_META.map((stage, i) => {
        const status = stageState[stage.id] || "idle";
        const out = stageOutput[stage.id];
        const summary = status === "done" ? stageSummary(stage.id, out) : null;
        const trace = out?.trace;
        const statusColor = status === "done" ? "var(--green-ink)" : status === "running" ? "var(--acc-ink)" : status === "waiting" ? "var(--amber-ink)" : "var(--ink-4)";
        return (
          <div key={stage.id} style={{border:"1px solid var(--line)", borderRadius:8, overflow:"hidden"}}>
            <div style={{display:"flex", alignItems:"center", gap:10, padding:"10px 14px", background:"var(--surface-2)"}}>
              <span className="mono" style={{fontSize:10, fontWeight:700, color:"var(--surface)", background: status === "done" ? "var(--acc)" : "var(--ink-4)", borderRadius:4, padding:"2px 7px", minWidth:18, textAlign:"center"}}>{i+1}</span>
              <div style={{flex:1}}>
                <div style={{fontSize:12.5, fontWeight:600, color:"var(--ink)"}}>{stage.name}</div>
                <div style={{fontSize:10.5, color:"var(--ink-3)"}}>{stage.desc}</div>
              </div>
              <span className="mono" style={{fontSize:10, fontWeight:600, color:statusColor, textTransform:"uppercase"}}>{status}</span>
            </div>
            {summary && (
              <div style={{padding:"10px 14px"}}>
                <div style={{display:"flex", gap:18, marginBottom: summary.tasks.length ? 10 : 0, flexWrap:"wrap"}}>
                  {summary.metrics.map((m, mi) => (
                    <div key={mi} style={{fontSize:11}}>
                      <span style={{color:"var(--ink-4)", marginRight:5}}>{m.l}:</span>
                      <span className="mono" style={{fontWeight:600, color:"var(--ink)"}}>{m.v}</span>
                    </div>
                  ))}
                </div>
                {summary.tasks.length > 0 && (
                  <div style={{display:"flex", flexDirection:"column", gap:3}}>
                    {summary.tasks.map((t, ti) => (
                      <div key={ti} style={{display:"flex", gap:8, fontSize:11, color:"var(--ink-2)", padding:"3px 0", borderTop: ti > 0 ? "1px solid var(--line)" : "none"}}>
                        <span style={{color:"var(--green-ink)", flexShrink:0}}>✓</span>
                        <span>{t}</span>
                      </div>
                    ))}
                  </div>
                )}
                {trace?.decisions?.length > 0 && (
                  <div style={{marginTop:10}}>
                    <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)", letterSpacing:"0.06em", marginBottom:5}}>STAGE REASONING</div>
                    <div style={{display:"flex", flexDirection:"column", gap:3}}>
                      {trace.decisions.map((d, di) => (
                        <div key={di} style={{fontSize:11, color:"var(--ink-3)", padding:"3px 0", borderTop: di > 0 ? "1px solid var(--line)" : "none"}}>{d}</div>
                      ))}
                    </div>
                  </div>
                )}
                {trace?.conclusion && (
                  <div style={{marginTop:8, fontSize:11, color:"var(--ink-2)", fontStyle:"italic"}}>{trace.conclusion}</div>
                )}
              </div>
            )}
            {status === "idle" && (
              <div style={{padding:"8px 14px", fontSize:11, color:"var(--ink-4)"}}>Stage did not run in this loop.</div>
            )}
            {status === "waiting" && (
              <div style={{padding:"8px 14px", fontSize:11, color:"var(--amber-ink)"}}>Awaiting HITL gate approval.</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ------ Override modal (HITL gate override w/ rationale capture) ------
function OverrideModal({ open, gateNum, onClose, onConfirm }) {
  const [reason, setReason] = useState("");
  useEffect(() => { if (open) setReason(""); }, [open]);
  if (!open) return null;
  return (
    <div className="modal open" onClick={(e) => { if (e.target.classList.contains("modal")) onClose(); }}>
      <div className="modal-box sm">
        <div className="modal-head">
          <div className="modal-title">Override Gate {gateNum} · Add Rationale</div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
        </div>
        <div className="modal-body">
          <div style={{fontSize: 12, color: "var(--ink-3)", marginBottom: 8, lineHeight: 1.5}}>
            Required for audit trail. The rationale is captured verbatim into the Loop Report.
          </div>
          <textarea className="fi-ta" value={reason} onChange={e => setReason(e.target.value)} placeholder="Describe the basis for your override decision…"/>
        </div>
        <div className="modal-foot">
          <span className="muted" style={{fontSize: 11}}>{reason.length} chars</span>
          <div style={{display: "flex", gap: 6}}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!reason.trim()} onClick={() => onConfirm(reason.trim())}>Confirm Override</button>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ReportModal, OverrideModal });
