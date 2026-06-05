/* ============================================================
   Loop Report modal + Override (HITL) modal
   ============================================================ */

function ReportModal({ open, onClose, payload }) {
  if (!open || !payload) return null;
  const { entity, ts, cfg, signals, risks, top3, riskAppetite, objectives, maps, closure, loop, scenarios, greySwan, personas, assumptions, obstacles, log } = payload;
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
                <RAGChip rag={
                  (riskAppetite?.status || riskAppetite) === "BREACHED" ? "R" : "G"
                }>{riskAppetite?.status || riskAppetite}</RAGChip>
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
            <Row k="Loop Health"      v={<><RAGChip rag={loop.loop_health || "A"}>{loop.loop_health}</RAGChip> <span className="muted mono" style={{marginLeft: 8}}>impact {loop.audit_impact_score}/10</span></>}/>
          </div>

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
                <Row k="Sprint / Hours"   v={`${o.sprint} / ${o.hours}h`}/>
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
                      <div className="mono" style={{fontSize: 11, color: "var(--ink-3)"}}>{s.runway_qtrs} quarters runway</div>
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
            {assumptions.map((a, i) => (
              <div key={i} style={{display:"flex", gap: 10, padding: "5px 0", fontSize: 12, color: "var(--ink-2)", lineHeight: 1.55}}>
                <span className="mono" style={{color: "var(--ink-3)", flexShrink: 0, width: 18}}>{i+1}.</span>
                <span>{a}</span>
              </div>
            ))}
          </div>

          <div className="rep-section">
            <h3>Obstacles & Flags</h3>
            {obstacles.length === 0
              ? <div style={{fontSize: 12, color: "var(--green-ink)"}}>No obstacles. All stages completed within expected parameters.</div>
              : obstacles.map((o, i) => (
                <div key={i} className="rep-finding A" style={{marginBottom: 6}}>{o}</div>
              ))}
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
          <span className="mono muted" style={{fontSize: 11}}>{loop.audit_impact_score ? `Audit impact ${loop.audit_impact_score}/10` : ""} · {risks.length} risks · {maps.length} MAPs</span>
          <div style={{display: "flex", gap: 6}}>
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
