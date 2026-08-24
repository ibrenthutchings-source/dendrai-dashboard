/* ============================================================
   Per-Risk Approval Review (HITL Gate 1)
   - Lists every risk inline with Approve / Adjust controls
   - Approve as-is is final immediately (nothing changed, nothing to check).
   - Adjustments route to the preparer's manager (real org-chart reporting
     line, see auth.users.manager_id) for a second review, via the Approval
     Inbox screen — not a fixed fictional signoff chain.
   - Rationale captured verbatim into the audit trail
   ============================================================ */

function deltaLabel(orig, next, key, fmt = (v) => v) {
  if (orig[key] === next[key]) return null;
  return `${fmt(orig[key])} → ${fmt(next[key])}`;
}

const RAR_CE_ADJ = { STRONG: -0.7, ADEQUATE: -0.3, WEAK: 0.1, NONE: 0.4 };
const RAR_APPETITE_THRESHOLDS = { GREEN: 5.0, AMBER: 7.5, RED: 9.5 };

// Status values per risk approval:
//   'pending'          — awaiting preparer disposition
//   'approved'         — accepted as scored; final, no review needed
//   'submitted'        — adjusted with rationale; routed to manager for review
//   'manager_approved' — manager reviewed and approved the adjustment; final
//   'rejected'         — manager sent it back; preparer must revise

function RiskApprovalReview({
  risks,
  approvals,
  appetiteLevel = "AMBER",
  appetiteThreshold,
  perRiskAppetite = {},
  onSetPerRiskAppetite,
  onApproveRisk,
  onOpenAdjust,
  onApproveAll,
  onSubmit,
  onOverrideGate,
  onAddRisk,
}) {
  if (!risks || risks.length === 0) return null;
  const [expandedId, setExpandedId] = React.useState(null);
  const total = risks.length;
  const decided = risks.filter(r => {
    const s = approvals[r.id]?.status;
    return s === "approved" || s === "submitted" || s === "manager_approved";
  }).length;
  const submittedCount = risks.filter(r => ["submitted", "manager_approved", "rejected"].includes(approvals[r.id]?.status)).length;
  const pendingReview = risks.filter(r => approvals[r.id]?.status === "submitted").length;
  const rejectedCount = risks.filter(r => approvals[r.id]?.status === "rejected").length;
  const allResolved = decided === total;

  return (
    <div className="rar" data-screen-label="HITL · Risk approval">
      <div className="rar-head">
        <div className="rar-head-l">
          <div className="rar-pill">
            <span className="dot"/>HUMAN REVIEW · GATE 1
          </div>
          <div className="rar-title">Risk Assessment · Per-Risk Review</div>
          <div className="rar-sub">
            Approve each risk as-is, or adjust scoring with rationale. Adjustments route to your manager for review.
          </div>
        </div>
        <div className="rar-head-r">
          <div className="rar-prog">
            <div className="rar-prog-track"><div className="rar-prog-fill" style={{width: `${(decided/total)*100}%`}}/></div>
            <div className="rar-prog-meta">
              <span className="mono"><b style={{color:"var(--ink)",fontWeight:500}}>{decided}</b> / {total} resolved</span>
              {submittedCount > 0 && <span className="mono muted">· {submittedCount} adjusted</span>}
              {pendingReview > 0 && <span className="mono" style={{color:"var(--amber-ink)"}}>· {pendingReview} awaiting manager</span>}
              {rejectedCount > 0 && <span className="mono" style={{color:"var(--red-ink)"}}>· {rejectedCount} rejected</span>}
            </div>
          </div>
        </div>
      </div>

      <div className="rar-table-wrap">
        <div className="rar-thead">
          <div className="rar-th rar-th-name">Risk</div>
          <div className="rar-th rar-th-score">Score</div>
          <div className="rar-th rar-th-vel">Velocity</div>
          <div className="rar-th rar-th-ce">Control</div>
          <div className="rar-th rar-th-action">Disposition</div>
        </div>
        <div className="rar-tbody">
          {risks.map(r => (
            <RiskRow
              key={r.id}
              risk={r}
              approval={approvals[r.id] || { status: "pending" }}
              appetiteLevel={appetiteLevel}
              perRiskLevel={perRiskAppetite[r.id] || appetiteLevel}
              onSetPerRiskLevel={(lvl) => onSetPerRiskAppetite && onSetPerRiskAppetite(prev => ({...prev, [r.id]: lvl}))}
              onApprove={() => onApproveRisk(r.id)}
              onAdjust={() => onOpenAdjust(r.id)}
              expanded={expandedId === r.id}
              onToggle={() => setExpandedId(prev => prev === r.id ? null : r.id)}
            />
          ))}
        </div>
      </div>

      <div className="rar-foot">
        <button className="btn btn-sm" onClick={onOverrideGate}>
          <Icon name="alert" size={11}/> Override entire gate
        </button>
        {onAddRisk && (
          <button className="btn btn-sm" onClick={onAddRisk}>
            <Icon name="plus" size={11}/> Add Risk
          </button>
        )}
        <div className="rar-foot-spacer"/>
        <button className="btn btn-sm" onClick={onApproveAll} disabled={allResolved}>
          Approve all remaining ({total - decided})
        </button>
        <button className="btn btn-sm btn-primary" disabled={!allResolved} onClick={onSubmit}>
          <Icon name="check" size={11}/> Confirm Risk Assessment
        </button>
      </div>
    </div>
  );
}

function RiskRow({ risk, approval, appetiteLevel = "AMBER", perRiskLevel = "AMBER", onSetPerRiskLevel, onApprove, onAdjust, expanded, onToggle }) {
  const r = risk;
  const status = approval.status || "pending";
  const adj = approval.adjustments || null;
  const effRag = adj?.rag ?? risk.rag;
  const effScore = adj?.score ?? risk.score;
  const effVel = adj?.velocity ?? risk.velocity;
  const effCe = adj?.ce ?? risk.ce;

  const wasAdjusted = ["submitted", "manager_approved", "rejected"].includes(status);
  const controls = MOCK.riskFlow?.[risk.id]?.controls || [];
  const threshold = RAR_APPETITE_THRESHOLDS[perRiskLevel] ?? 7.5;
  const breachesAppetite = effScore >= threshold;

  return (
    <Clickable className={`rar-row rar-row-${status}`} onClick={onToggle} style={{cursor: "pointer", userSelect: "none"}}>
      <div className="rar-td rar-td-name">
        <div className="rar-name-head">
          <RAGChip rag={effRag}>{effRag}</RAGChip>
          {wasAdjusted && effRag !== risk.rag && (
            <span className="rar-was mono">was <RAGChip rag={risk.rag}>{risk.rag}</RAGChip></span>
          )}
          <div className="rar-rname">{risk.name}</div>
          <span style={{marginLeft: "auto", fontSize: 10, color: "var(--ink-3)", flexShrink: 0, paddingLeft: 6}}>{expanded ? "▲" : "▼"}</span>
        </div>
        <div className="rar-rmeta mono">
          <span>{risk.id}</span>
          <span>·</span>
          <span>{risk.category}</span>
          {controls.length > 0 && (
            <span style={{color: "var(--ink-3)", fontSize: 10}}>{controls.length} controls</span>
          )}
        </div>
      </div>

      <div className="rar-td rar-td-score">
        <div className="rar-score-row">
          <span className="mono" style={{color: scoreColorInk(effScore), fontWeight: 500}}>{fmt2(effScore)}</span>
          {wasAdjusted && effScore !== risk.score && (
            <span className="rar-was mono">was {fmt2(risk.score)}</span>
          )}
        </div>
        <div style={{display:"flex", alignItems:"center", gap:4}}>
          <span className={"rar-tol-badge " + (breachesAppetite ? "breach" : "ok")}>
            {breachesAppetite ? "BREACH" : "OK"}
          </span>
          <div className="rar-appetite-btns">
            {["G","A","R"].map(ch => {
              const lvlMap = {"G":"GREEN","A":"AMBER","R":"RED"};
              const lvl = lvlMap[ch];
              const isActive = perRiskLevel === lvl;
              const colors = {G:"var(--green-ink)",A:"var(--amber-ink)",R:"var(--red-ink)"};
              const softs = {G:"var(--green-soft)",A:"var(--amber-soft)",R:"var(--red-soft)"};
              return (
                <button key={ch}
                  className={"rar-apt-btn" + (isActive ? " active" : "")}
                  style={isActive ? {background:softs[ch], color:colors[ch], borderColor:colors[ch]} : {}}
                  onClick={(e) => { e.stopPropagation(); onSetPerRiskLevel && onSetPerRiskLevel(lvl); }}
                  title={`Set ${r.id} appetite to ${lvl}`}>
                  {ch}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="rar-td rar-td-vel">
        <VelocityPill v={effVel}/>
        {wasAdjusted && effVel !== risk.velocity && (
          <span className="rar-was mono">was {risk.velocity > 0 ? "+" : ""}{risk.velocity}</span>
        )}
      </div>

      <div className="rar-td rar-td-ce">
        <span className="mono" style={{fontSize: 10.5}}>{effCe}</span>
        {wasAdjusted && effCe !== risk.ce && (
          <span className="rar-was mono">was {risk.ce}</span>
        )}
      </div>

      <div className="rar-td rar-td-action">
        {status === "pending" && (
          risk._isNew ? (
            <div className="rar-actions">
              <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); onAdjust(); }}>
                <Icon name="edit" size={10}/> Assess risk
              </button>
              <div className="mono" style={{fontSize: 9, color: "var(--amber-ink)"}}>New — must be assessed</div>
            </div>
          ) : (
            <div className="rar-actions">
              <button className="btn btn-sm rar-btn-approve" onClick={(e) => { e.stopPropagation(); onApprove(); }}>
                <Icon name="check" size={10}/> Approve
              </button>
              <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); onAdjust(); }}>
                <Icon name="edit" size={10}/> Adjust
              </button>
            </div>
          )
        )}
        {status === "approved" && (
          <div className="rar-disposition rar-disposition-approved">
            <Icon name="check" size={11}/>
            <span>Approved as scored</span>
          </div>
        )}
        {status === "submitted" && (
          <div className="rar-disposition rar-disposition-adjusted">
            <Icon name="alert" size={11}/>
            <span>Awaiting {approval.managerName || "manager"} review</span>
          </div>
        )}
        {status === "manager_approved" && (
          <div className="rar-disposition rar-disposition-signed">
            <Icon name="check" size={11}/>
            <span>Approved by {approval.reviewerName || "manager"}</span>
          </div>
        )}
        {status === "rejected" && (
          <div className="rar-actions">
            <div className="rar-disposition" style={{color: "var(--red-ink)"}}>
              <Icon name="alert" size={11}/>
              <span>Rejected — revise</span>
            </div>
            <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); onAdjust(); }}>
              <Icon name="edit" size={10}/> Revise
            </button>
          </div>
        )}
      </div>

      {expanded && (
        <>
          {controls.length > 0 && (
            <div className="rar-ctrl-detail-row">
              <div className="rar-ctrl-detail-head mono">
                Control tolerance · threshold ≥ {threshold}
              </div>
              {controls.map((ctrl, ci) => {
                const adj = parseFloat((effScore + (RAR_CE_ADJ[ctrl.ce] || 0)).toFixed(1));
                const withinTol = adj < threshold;
                return (
                  <div key={ci} className="rar-ctrl-item">
                    <span className={"s2-ctrl-dot " + (withinTol ? "ok" : "out")}/>
                    <span style={{flex:1, fontSize:11, color:"var(--ink-2)"}}>{ctrl.name}</span>
                    <span className="mono" style={{fontSize:10, color:"var(--ink-3)", marginRight:8}}>{ctrl.ce}</span>
                    <span className="mono" style={{fontSize:10, fontWeight:500, color: withinTol ? "var(--green-ink)" : "var(--red-ink)"}}>
                      {adj.toFixed(1)} {withinTol ? "OK" : "BREACH"}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          {wasAdjusted && (
            <div className="rar-row-detail">
              <div className="rar-detail-rationale">
                <div className="rar-detail-label mono">RATIONALE — {approval.adjustedBy || "Auditor"} · {approval.adjustedAt ? new Date(approval.adjustedAt).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit"}) : "—"}</div>
                <div className="rar-detail-text">{approval.rationale}</div>
              </div>
              <div style={{display: "flex", flexDirection: "column", gap: 6, borderLeft: "1px solid var(--line)", paddingLeft: 16}}>
                <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em"}}>REVIEW STATUS</div>
                {status === "submitted" && (
                  <div style={{fontSize: 11.5, color: "var(--amber-ink)"}}>Awaiting review from {approval.managerName || "your manager"}.</div>
                )}
                {status === "manager_approved" && (
                  <div style={{fontSize: 11.5, color: "var(--green-ink)"}}>
                    Approved by {approval.reviewerName || "manager"}{approval.reviewedAt ? ` at ${new Date(approval.reviewedAt).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit"})}` : ""}.
                  </div>
                )}
                {status === "rejected" && (
                  <div style={{fontSize: 11.5, color: "var(--red-ink)"}}>
                    Rejected by {approval.reviewerName || "manager"}{approval.reviewComment ? `: "${approval.reviewComment}"` : "."}
                  </div>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </Clickable>
  );
}

// ----------- Adjust Risk Modal -----------
function AdjustRiskModal({ open, risk, risks = [], ticker, runId, narrativeResult, onClose, onSubmit }) {
  const [name, setName] = useState(risk?.name || "");
  const [category, setCategory] = useState(risk?.category || "");
  const [rag, setRag] = useState(risk?.rag || "A");
  const [score, setScore] = useState(risk?.score ?? 5);
  const [velocity, setVelocity] = useState(risk?.velocity ?? 0);
  const [ce, setCe] = useState(risk?.ce || "ADEQUATE");
  const [rationale, setRationale] = useState("");
  const [aiState, setAiState] = useState({ loading: false, error: null, reco: null });

  useEffect(() => {
    if (open && risk) {
      setName(risk.name || "");
      setCategory(risk.category || "");
      setRag(risk.rag);
      setScore(risk.score);
      setVelocity(risk.velocity);
      setCe(risk.ce);
      setRationale("");
      setAiState({ loading: false, error: null, reco: null });
    }
  }, [open, risk?.id]);

  // #2 — AI-assisted HITL: draft a disposition the auditor accepts or overrides.
  const aiAvailable = typeof window !== "undefined" && window.MCP?.aiGate1Recommend;
  async function runAiSuggest() {
    if (!aiAvailable || !risk) return;
    setAiState({ loading: true, error: null, reco: null });
    try {
      const gate1Context = narrativeResult
        ? { emerging_risks: narrativeResult.emerging_risks, yoy_changes: narrativeResult.yoy_changes, narrative_summary: narrativeResult.summary }
        : {};
      const res = await window.MCP.aiGate1Recommend(ticker || "", [risk], gate1Context, runId || null);
      const reco = (res?.recommendations || [])[0];
      if (!reco) throw new Error("no recommendation returned");
      setRag(reco.suggested_rag ?? rag);
      setScore(reco.suggested_score ?? score);
      setVelocity(reco.suggested_velocity ?? velocity);
      setCe(reco.suggested_ce ?? ce);
      setRationale(
        `[AI suggestion · ${reco.confidence || "?"} confidence] ${reco.rationale || ""}`.trim()
      );
      setAiState({ loading: false, error: null, reco });
    } catch (e) {
      setAiState({ loading: false, error: e.message || "AI unavailable", reco: null });
    }
  }

  useEscapeToClose(open, onClose);

  if (!open || !risk) return null;

  const frameworkRisks = window.FW_MOCK_RISKS || {};
  const fwRiskByName = {};
  Object.values(frameworkRisks).flat().forEach(r => { fwRiskByName[r.name] = r; });

  const categoryOptions = [...new Set([
    risk.category,
    ...risks.map(r => r.category),
    ...Object.values(frameworkRisks).flat().map(r => r.category),
  ].filter(Boolean))].sort();

  function handleNameSelect(newName) {
    setName(newName);
    const match = fwRiskByName[newName];
    if (match) setCategory(match.category);
  }

  const nameValid = name.trim().length > 0;
  // Tracked for the "was X" diff indicators below, not required to submit — a
  // written rationale on its own is sufficient grounds for Adjust (an auditor may
  // want to formally document/reaffirm a risk's disposition without moving its
  // score, distinct from a quick "Approve as scored").
  const changed = rag !== risk.rag || score !== risk.score || velocity !== risk.velocity || ce !== risk.ce
    || name.trim() !== (risk.name || "") || category.trim() !== (risk.category || "")
    || !!aiState.reco;
  const valid = rationale.trim().length >= 30 && nameValid;

  // Normalized to the same keys as the submitted adjustments, so the backend
  // can tell whether the preparer kept the AI's suggestion or overrode it —
  // see approval_tasks.ai_suggested / ai_accepted.
  const aiSuggestedFields = aiState.reco ? {
    ...(aiState.reco.suggested_rag != null      ? { rag: aiState.reco.suggested_rag } : {}),
    ...(aiState.reco.suggested_score != null    ? { score: aiState.reco.suggested_score } : {}),
    ...(aiState.reco.suggested_velocity != null ? { velocity: aiState.reco.suggested_velocity } : {}),
    ...(aiState.reco.suggested_ce != null       ? { ce: aiState.reco.suggested_ce } : {}),
  } : null;

  return (
    <div className="modal open">
      <div className="modal-box" style={{width: 640}}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Adjust Risk · {risk.id}</div>
            <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", marginTop: 3}}>
              {risk.name} · {risk.category}
            </div>
          </div>
          <div style={{display: "flex", alignItems: "center", gap: 6}}>
            {aiAvailable && (
              <button className="btn btn-sm" onClick={runAiSuggest} disabled={aiState.loading}
                title="Draft a disposition with Claude — review and override as needed">
                <Icon name="spark" size={11}/> {aiState.loading ? "Analyzing…" : "Suggest with AI"}
              </button>
            )}
            <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
          </div>
        </div>
        {aiState.error && (
          <div className="mono" style={{padding: "4px 16px", fontSize: 10.5, color: "var(--red-ink)"}}>
            AI suggestion unavailable: {aiState.error}
          </div>
        )}
        {aiState.reco && (
          <div className="mono" style={{padding: "4px 16px", fontSize: 10.5, color: "var(--acc-ink)", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap"}}>
            <span>AI recommends: <b>{aiState.reco.recommendation}</b> — fields pre-filled below, edit freely.</span>
            <ProvenanceChip verdict={aiState.reco.recommendation} confidence={aiState.reco.confidence} />
          </div>
        )}
        <div className="modal-body">
          <div className="ar-field" style={{marginBottom: 14, display: "flex", gap: 12}}>
            <div style={{flex: 2}}>
              <label className="ar-label">Risk Name</label>
              <select value={name} onChange={e => handleNameSelect(e.target.value)} className="fi-input">
                <optgroup label="Current">
                  <option value={risk.name}>{risk.name}</option>
                </optgroup>
                {Object.entries(frameworkRisks).map(([fw, list]) => (
                  <optgroup key={fw} label={fw}>
                    {list.map(r => <option key={r.id} value={r.name}>{r.name}</option>)}
                  </optgroup>
                ))}
              </select>
              {risk._isNew ? (
                <div className="ar-orig mono">New risk — pick a name and set a real score below</div>
              ) : (
                <div className="ar-orig mono">Original: {risk.name}</div>
              )}
            </div>
            <div style={{flex: 1}}>
              <label className="ar-label">Category</label>
              <select value={category} onChange={e => setCategory(e.target.value)} className="fi-input">
                {categoryOptions.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>

          <div className="ar-grid">
            <div className="ar-field">
              <label className="ar-label">RAG Band</label>
              <div className="ar-rag-row">
                {["R","A","G"].map(v => (
                  <button key={v}
                    className={`ar-rag-opt ar-rag-opt-${v} ${rag === v ? "active" : ""}`}
                    onClick={() => setRag(v)}>
                    <RAGChip rag={v}>{v === "R" ? "Red" : v === "A" ? "Amber" : "Green"}</RAGChip>
                  </button>
                ))}
              </div>
              <div className="ar-orig mono">AI scored: <RAGChip rag={risk.rag}>{risk.rag}</RAGChip></div>
            </div>

            <div className="ar-field">
              <label className="ar-label">Score <span className="mono ar-val">{fmt2(score)}</span></label>
              <input type="range" min="0" max="25" step="0.1" value={score}
                onChange={e => setScore(parseFloat(e.target.value))} className="ar-slider"/>
              <div className="ar-orig mono">AI scored: {fmt2(risk.score)}</div>
            </div>

            <div className="ar-field">
              <label className="ar-label">Velocity <span className="mono ar-val">{velocity > 0 ? "+" : ""}{velocity}</span></label>
              <div className="ar-vel-row">
                {[-2,-1,0,1,2,3].map(v => (
                  <button key={v}
                    className={`ar-vel-opt ${velocity === v ? "active" : ""}`}
                    onClick={() => setVelocity(v)}>
                    {v > 0 ? "+" : ""}{v}
                  </button>
                ))}
              </div>
              <div className="ar-orig mono">AI scored: {risk.velocity > 0 ? "+" : ""}{risk.velocity}</div>
            </div>

            <div className="ar-field">
              <label className="ar-label">Control Effectiveness</label>
              <div className="ar-ce-row">
                {["NONE","WEAK","ADEQUATE","STRONG"].map(v => (
                  <button key={v}
                    className={`ar-ce-opt ${ce === v ? "active" : ""}`}
                    onClick={() => setCe(v)}>
                    {v}
                  </button>
                ))}
              </div>
              <div className="ar-orig mono">AI assessed: {risk.ce}</div>
            </div>
          </div>

          <div className="ar-rationale">
            <label className="ar-label">
              Rationale <span className="muted">· captured verbatim into audit trail, routed to your manager for review</span>
            </label>
            <textarea className="fi-ta" value={rationale}
              onChange={e => setRationale(e.target.value)}
              placeholder="Describe the basis for this adjustment. Include evidence reviewed, peer benchmarks consulted, and any control deficiencies considered. Minimum 30 characters."
              style={{minHeight: 90}}/>
            <div className="ar-rationale-meta mono">
              <span style={{color: rationale.trim().length >= 30 ? "var(--green-ink)" : "var(--ink-3)"}}>
                {rationale.trim().length} / 30 chars
              </span>
              {!changed && rationale.trim().length >= 30 && <span className="muted">· reaffirming as scored, with rationale on file</span>}
            </div>
          </div>

          <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", padding: "8px 10px", background: "var(--surface-2, var(--surface))", borderRadius: 6, border: "1px solid var(--line)"}}>
            This adjustment will be submitted to your manager for review. If you have no manager configured (set one from the header user menu), it is auto-approved so the workflow still completes.
          </div>
        </div>
        <div className="modal-foot">
          <span className="muted mono" style={{fontSize: 11}}>
            {valid ? "Submitting routes to your manager for review" : "Write a rationale (min 30 characters) to continue"}
          </span>
          <div style={{display: "flex", gap: 6}}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!valid}
              onClick={() => onSubmit({ name: name.trim(), category: category.trim(), rag, score, velocity, ce, rationale: rationale.trim(), ai_suggested: aiSuggestedFields })}>
              Submit Adjustment
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { RiskApprovalReview, AdjustRiskModal });
export { RiskApprovalReview, AdjustRiskModal };
