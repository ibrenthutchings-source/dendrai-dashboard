/* ============================================================
   Audit Evidence Pack modal — one-click, timestamped, defensible
   evidence bundle for a specific pipeline run. Modeled directly on
   ReportModal (report.jsx): same modal/rep-* classes, same
   window.print() export mechanism, same Row/RepSubhead helpers.
   ============================================================ */

function EvidencePackModal({ open, onClose, runId, ticker }) {
  const [state, setState] = useState({ loading: false, error: null, data: null });

  useEffect(() => {
    if (!open || !runId) return;
    setState({ loading: true, error: null, data: null });
    fetch(`/api/evidence-pack/${runId}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(data => setState({ loading: false, error: null, data }))
      .catch(e => setState({ loading: false, error: e.message || "Request failed", data: null }));
  }, [open, runId]);

  if (!open) return null;

  function downloadJson() {
    if (!state.data) return;
    const blob = new Blob([JSON.stringify(state.data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `dendrai_evidence_pack_${ticker || "run"}_${runId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const d = state.data;

  return (
    <div className="modal open" onClick={(e) => { if (e.target.classList.contains("modal")) onClose(); }}>
      <div className="modal-box" style={{ width: 920 }}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Audit Evidence Pack</div>
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 3 }}>
              {ticker ? `${ticker} · ` : ""}Run {runId}
            </div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
        </div>

        <div className="modal-body">
          {state.loading && (
            <div style={{ padding: "40px 0", textAlign: "center", color: "var(--ink-3)", fontSize: 12 }}>
              Assembling evidence pack…
            </div>
          )}

          {state.error && (
            <div style={{
              margin: "10px 0", padding: "10px 14px", borderRadius: 6,
              background: "var(--red-soft, #fff0f0)", border: "1px solid var(--red, #e05252)",
              fontSize: 11.5, color: "var(--red-ink, #b93333)",
            }}>
              Failed to load evidence pack — {state.error}
            </div>
          )}

          {d && (
            <>
              <div className="rep-h1">{d.run?.company_name || d.run?.ticker}</div>
              <div className="rep-h1-sub">
                {d.run?.ticker} · Run {d.run?.run_id} · {d.run?.run_at ? new Date(d.run.run_at).toLocaleString() : "—"}
                {" · "}Generated {d.generated_at ? new Date(d.generated_at).toLocaleString() : "—"} by {d.generated_by || "—"}
              </div>

              {/* ── Caveats — always visible, never hidden ────────── */}
              {d.caveats?.length > 0 && (
                <div style={{
                  margin: "10px 0", padding: "10px 14px", borderRadius: 6,
                  background: "var(--amber-soft, #fff8e6)", border: "1px solid var(--amber, #e8a838)",
                }}>
                  <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--amber-ink, #92600a)", marginBottom: 6 }}>
                    Known limitations in this pack — read before relying on it
                  </div>
                  {d.caveats.map((c, i) => (
                    <div key={i} style={{ fontSize: 11, color: "var(--ink-2)", lineHeight: 1.55, marginBottom: 4 }}>
                      <b style={{ textTransform: "capitalize" }}>{c.section.replace(/_/g, " ")}:</b> {c.note}
                    </div>
                  ))}
                </div>
              )}

              {/* ── Run summary ─────────────────────────────────── */}
              <div className="rep-section">
                <h3>Run Summary</h3>
                <Row k="Industry" v={d.run?.industry || "—"}/>
                <Row k="Persona" v={d.run?.persona || "—"}/>
                <Row k="Data Mode" v={d.run?.data_mode || "—"}/>
                <Row k="Period" v={`${d.run?.period_begin || "—"} → ${d.run?.period_end || "—"}`}/>
                <Row k="Appetite Level" v={d.run?.appetite_level || "—"}/>
                <Row k="Completed" v={d.run?.completed ? `Yes · ${d.run?.completed_at ? new Date(d.run.completed_at).toLocaleString() : ""}` : "No"}/>
              </div>

              {/* ── Risk Scores ──────────────────────────────────── */}
              <div className="rep-section">
                <h3>Risk Scores <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>· {d.risk_scores?.length || 0}</span></h3>
                {!d.risk_scores?.length ? <EmptyNote text="No risk scores recorded for this run."/> : (
                  <table className="rep-table">
                    <thead><tr><th>Risk</th><th>RAG</th><th>Score</th><th>Velocity</th></tr></thead>
                    <tbody>
                      {d.risk_scores.map(r => (
                        <tr key={r.risk_ref}>
                          <td><b style={{ fontWeight: 500 }}>{r.name}</b><div className="muted" style={{ fontSize: 10 }}>{r.risk_ref} · {r.category}</div></td>
                          <td><RAGChip rag={r.rag}>{r.rag}</RAGChip></td>
                          <td className="mono">{r.score != null ? r.score.toFixed(1) : "—"}</td>
                          <td className="mono">{r.velocity != null ? `v${r.velocity >= 0 ? "+" : ""}${r.velocity}` : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {/* ── Audit Objectives ─────────────────────────────── */}
              <div className="rep-section">
                <h3>Audit Objectives <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>· {d.audit_objectives?.length || 0}</span></h3>
                {!d.audit_objectives?.length ? <EmptyNote text="No audit objectives recorded for this run."/> : d.audit_objectives.map(o => (
                  <div key={o.id} className="rep-finding" style={{ marginBottom: 8 }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
                      <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>{o.priority}</span>
                      <b style={{ fontWeight: 500 }}>{o.objective}</b>
                    </div>
                    <Row k="Linked Risk" v={o.linked_risk || (o.linked_risks || []).join(", ") || "—"}/>
                    <Row k="Controls" v={(o.controls || []).join(", ") || "—"}/>
                  </div>
                ))}
              </div>

              {/* ── Sign-offs (approval_tasks — the real, current workflow) ── */}
              <div className="rep-section">
                <h3>Approval Sign-offs <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>· {d.approval_tasks?.length || 0}</span></h3>
                {!d.approval_tasks?.length ? <EmptyNote text="No approval tasks recorded for this run."/> : (
                  <table className="rep-table">
                    <thead><tr><th>Gate</th><th>Item</th><th>Disposition</th><th>Prepared by</th><th>Reviewed by</th><th>Status</th></tr></thead>
                    <tbody>
                      {d.approval_tasks.map(t => (
                        <tr key={t.id}>
                          <td className="mono" style={{ fontSize: 10 }}>{t.gate_type}</td>
                          <td>{t.item_label || t.item_ref}</td>
                          <td>{t.disposition || "—"}</td>
                          <td style={{ fontSize: 11 }}>{t.prepared_by_name || "—"}</td>
                          <td style={{ fontSize: 11 }}>{t.reviewed_by_name || "—"}</td>
                          <td className="mono" style={{ fontSize: 10 }}>{t.status}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {/* ── Risks-as-Code artifacts ──────────────────────── */}
              <div className="rep-section">
                <h3>Risks-as-Code Artifacts</h3>
                {!Object.keys(d.risks_as_code_artifacts || {}).length ? <EmptyNote text="No RaC artifacts generated for this run."/> : (
                  <div style={{ display: "grid", gap: 10 }}>
                    {Object.entries(d.risks_as_code_artifacts).map(([fw, artifact]) => (
                      <div key={fw}>
                        <RepSubhead>{fw.toUpperCase()} <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>· generated {artifact.generated_at ? new Date(artifact.generated_at).toLocaleString() : "—"}</span></RepSubhead>
                        <pre className="mono" style={{ fontSize: 10.5, background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 6, padding: 10, maxHeight: 200, overflow: "auto", whiteSpace: "pre-wrap" }}>{artifact.content}</pre>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* ── Risk -> Control mappings ─────────────────────── */}
              <div className="rep-section">
                <h3>Risk → Control Mappings <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>· {d.controls_by_risk?.length || 0}</span></h3>
                {!d.controls_by_risk?.length ? <EmptyNote text="No Risk Register Review pass has been run for this run."/> : (
                  <table className="rep-table">
                    <thead><tr><th>Risk</th><th>Controls Assigned</th></tr></thead>
                    <tbody>
                      {d.controls_by_risk.map((c, i) => (
                        <tr key={i}>
                          <td className="mono" style={{ fontSize: 10 }}>{c.risk_ref}</td>
                          <td style={{ fontSize: 11 }}>{(c.controls_assigned || []).map(x => x.ref || x).join(", ") || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {/* ── Adjudications ─────────────────────────────────── */}
              <div className="rep-section">
                <h3>MCP/Tool Adjudications <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>· {d.adjudications_meta?.total || 0} ({d.adjudications_meta?.time_window_estimate_count || 0} estimated)</span></h3>
                {!d.adjudications?.length ? <EmptyNote text="No adjudications found in this run's window."/> : (
                  <table className="rep-table">
                    <thead><tr><th>Tool</th><th>Verdict</th><th>Tier</th><th>Linked via</th><th>When</th></tr></thead>
                    <tbody>
                      {d.adjudications.map(a => (
                        <tr key={a.id}>
                          <td className="mono" style={{ fontSize: 10 }}>{a.target_tool}</td>
                          <td>{a.final_verdict || "—"}</td>
                          <td>{a.risk_tier || "—"}</td>
                          <td>
                            <span className="mono" style={{
                              fontSize: 9.5, padding: "1px 6px", borderRadius: 4,
                              background: a.linked_via === "run_id" ? "var(--green-soft)" : "var(--amber-soft)",
                              color: a.linked_via === "run_id" ? "var(--green-ink)" : "var(--amber-ink)",
                            }}>
                              {a.linked_via === "run_id" ? "verified" : "estimated"}
                            </span>
                          </td>
                          <td className="mono" style={{ fontSize: 10 }}>{a.adjudicated_at ? new Date(a.adjudicated_at).toLocaleString() : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {/* ── Controls-as-Code (latest, global — caveat above) ── */}
              {d.controls_as_code_latest && (
                <div className="rep-section">
                  <h3>Controls-as-Code <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>· latest as of {d.controls_as_code_latest.generated_at ? new Date(d.controls_as_code_latest.generated_at).toLocaleString() : "—"}</span></h3>
                  <pre className="mono" style={{ fontSize: 10.5, background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 6, padding: 10, maxHeight: 200, overflow: "auto", whiteSpace: "pre-wrap" }}>{d.controls_as_code_latest.content_rego}</pre>
                </div>
              )}

              {/* ── Policy-as-Code modules (current state — caveat above) ── */}
              <div className="rep-section">
                <h3>Policy-as-Code Modules <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>· {d.policy_as_code_modules?.length || 0} processes</span></h3>
                {!d.policy_as_code_modules?.length ? <EmptyNote text="No policy modules saved."/> : (
                  <div style={{ display: "grid", gap: 10 }}>
                    {d.policy_as_code_modules.map(m => (
                      <div key={m.id}>
                        <RepSubhead>{m.process} <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>· v{m.version}</span></RepSubhead>
                        <pre className="mono" style={{ fontSize: 10.5, background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 6, padding: 10, maxHeight: 160, overflow: "auto", whiteSpace: "pre-wrap" }}>{m.rego_content}</pre>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* ── AI Narrative (if generated for this run) ────────── */}
              {d.audit_report && (
                <div className="rep-section">
                  <h3>AI Narrative Report <span className="mono" style={{ fontSize: 10, color: "var(--acc-ink)", fontWeight: 400 }}>· Claude-generated</span></h3>
                  <div style={{
                    whiteSpace: "pre-wrap", fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.7,
                    background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 8, padding: "14px 16px",
                  }}>
                    {d.audit_report.content}
                  </div>
                </div>
              )}

              {/* ── Execution log ────────────────────────────────── */}
              <div className="rep-section">
                <h3>Execution Log <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>· {d.loop_log?.length || 0} entries</span></h3>
                {!d.loop_log?.length ? <EmptyNote text="No log entries recorded for this run."/> : (
                  <div className="mono rep-audit-trail" style={{ fontSize: 11, background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 8, padding: 12, maxHeight: 220, overflowY: "auto" }}>
                    {d.loop_log.map((e, i) => (
                      <div key={i} style={{ display: "grid", gridTemplateColumns: "140px 1fr", gap: 8, padding: "2px 0" }}>
                        <span style={{ color: "var(--ink-3)" }}>{e.logged_at ? new Date(e.logged_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"}</span>
                        <span style={{ color: "var(--ink-2)" }}>{e.message}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        <div className="modal-foot">
          <span className="mono muted" style={{ fontSize: 11 }}>
            {d ? `${d.risk_scores?.length || 0} risks · ${d.approval_tasks?.length || 0} sign-offs · ${d.adjudications_meta?.total || 0} adjudications` : ""}
          </span>
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn btn-sm" onClick={downloadJson} disabled={!d}><Icon name="download" size={11}/> Download JSON</button>
            <button className="btn btn-sm" onClick={() => window.print()} disabled={!d}><Icon name="download" size={11}/> Print / PDF</button>
            <button className="btn btn-sm btn-primary" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function EmptyNote({ text }) {
  return <div style={{ fontSize: 11.5, color: "var(--ink-4)", fontStyle: "italic" }}>{text}</div>;
}

Object.assign(window, { EvidencePackModal });
