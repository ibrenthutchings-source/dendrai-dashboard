/* ============================================================
   Per-Objective Scope Approval Review (HITL Gate 2)
   - Lists every audit objective with Approve / Adjust controls
   - Approve as-is is final immediately (nothing changed, nothing to check).
   - Adjustments route to the preparer's manager (real org-chart reporting
     line, see auth.users.manager_id) for a second review, via the Approval
     Inbox screen — not a fixed fictional signoff chain.
   - Reuses .rar styles; adds .sar column overrides
   ============================================================ */

function priColor(p) {
  return p === "P1" ? "var(--red-ink)" : p === "P2" ? "var(--amber-ink)" : "var(--green-ink)";
}
function priSoft(p) {
  return p === "P1" ? "var(--red-soft)" : p === "P2" ? "var(--amber-soft)" : "var(--green-soft)";
}

const CE_LABEL = { STRONG: "Strong", ADEQUATE: "Adequate", WEAK: "Weak", NONE: "None" };
const CE_COLOR = { STRONG: "var(--green-ink)", ADEQUATE: "var(--acc-ink)", WEAK: "var(--amber-ink)", NONE: "var(--red-ink)" };

function defaultRiskReduction(priority, ce) {
  const base = { P1: 20, P2: 15, P3: 10 }[priority] ?? 15;
  const adj = { NONE: 8, WEAK: 4, ADEQUATE: 0, STRONG: -3 }[ce] ?? 0;
  return Math.min(50, Math.max(5, base + adj));
}

// Status values per objective approval:
//   'pending'          — awaiting preparer disposition
//   'approved'         — accepted as scoped; final, no review needed
//   'submitted'        — adjusted with rationale; routed to manager for review
//   'manager_approved' — manager reviewed and approved the adjustment; final
//   'rejected'         — manager sent it back; preparer must revise

function ScopeApprovalReview({
  objectives,
  approvals,
  risks = [],
  onApproveObjective,
  onOpenAdjust,
  onApproveAll,
  onSubmit,
  onOverrideGate,
  onAddObjective,
}) {
  if (!objectives || objectives.length === 0) return null;

  const [riskReductions, setRiskReductions] = React.useState(() => {
    const map = {};
    objectives.forEach(o => {
      const linkedRisk = risks.find(r => r.id === o.linked_risk);
      map[o.id] = defaultRiskReduction(o.priority, linkedRisk?.ce || "ADEQUATE");
    });
    return map;
  });

  const total = objectives.length;
  const decided = objectives.filter(o => {
    const s = approvals[o.id]?.status;
    return s === "approved" || s === "submitted" || s === "manager_approved";
  }).length;
  const submittedCount = objectives.filter(o => ["submitted", "manager_approved", "rejected"].includes(approvals[o.id]?.status)).length;
  const pendingReview = objectives.filter(o => approvals[o.id]?.status === "submitted").length;
  const rejectedCount = objectives.filter(o => approvals[o.id]?.status === "rejected").length;
  const allResolved = decided === total;

  const totalHours = objectives.reduce((sum, o) => {
    return sum + (approvals[o.id]?.adjustments?.hours ?? o.hours ?? 0);
  }, 0);

  return (
    <div className="rar sar" data-screen-label="HITL · Scope approval">
      <div className="rar-head">
        <div className="rar-head-l">
          <div className="rar-pill sar-pill">
            <span className="dot"/>HITL · GATE 2
          </div>
          <div className="rar-title">Audit Scope · Per-Objective Review</div>
          <div className="rar-sub">
            Approve each objective as-is, or adjust priority / hours / fiscal quarter with rationale.
            Adjustments route to your manager for review.
          </div>
        </div>
        <div className="rar-head-r">
          <div className="rar-prog">
            <div className="rar-prog-track">
              <div className="rar-prog-fill" style={{width: `${(decided/total)*100}%`}}/>
            </div>
            <div className="rar-prog-meta">
              <span className="mono">
                <b style={{color:"var(--ink)",fontWeight:500}}>{decided}</b> / {total} resolved
              </span>
              {submittedCount > 0 && <span className="mono muted">· {submittedCount} adjusted</span>}
              {pendingReview > 0 && (
                <span className="mono" style={{color:"var(--amber-ink)"}}>· {pendingReview} awaiting manager</span>
              )}
              {rejectedCount > 0 && <span className="mono" style={{color:"var(--red-ink)"}}>· {rejectedCount} rejected</span>}
            </div>
          </div>
        </div>
      </div>

      <div className="rar-table-wrap">
        <div className="rar-thead sar-thead">
          <div className="rar-th sar-th-pri">Pri</div>
          <div className="rar-th sar-th-obj">Objective</div>
          <div className="rar-th sar-th-risk">Risk</div>
          <div className="rar-th sar-th-ctrl">Control</div>
          <div className="rar-th sar-th-rr">Risk Red.</div>
          <div className="rar-th sar-th-num">Hrs</div>
          <div className="rar-th sar-th-num" title="Fiscal Quarter">FQ</div>
          <div className="rar-th sar-th-action">Disposition</div>
        </div>
        <div className="rar-tbody">
          {objectives.map(o => {
            const CE_RANK = { NONE: 0, WEAK: 1, ADEQUATE: 2, STRONG: 3 };
            const linkedIds = o.linked_risks?.length ? o.linked_risks : o.linked_risk ? [o.linked_risk] : [];
            const linkedCEs = linkedIds.map(id => risks.find(r => r.id === id)?.ce || "ADEQUATE");
            const worstCE = linkedCEs.reduce((worst, ce) =>
              (CE_RANK[ce] ?? 2) < (CE_RANK[worst] ?? 2) ? ce : worst
            , "ADEQUATE");
            return (
              <ObjectiveRow
                key={o.id}
                obj={o}
                approval={approvals[o.id] || { status: "pending" }}
                linkedRiskCE={worstCE}
                riskReduction={riskReductions[o.id] ?? 15}
                onSetRiskReduction={val => setRiskReductions(prev => ({ ...prev, [o.id]: val }))}
                onApprove={() => onApproveObjective(o.id)}
                onAdjust={() => onOpenAdjust(o.id)}
              />
            );
          })}
        </div>
      </div>

      <div className="sar-footer-meta">
        <span className="mono" style={{fontSize: 11, color: "var(--ink-3)"}}>
          Total hours: <b style={{color: "var(--ink)", fontWeight: 500}}>{totalHours}h</b>
        </span>
      </div>

      <div className="rar-foot">
        <button className="btn btn-sm" onClick={onOverrideGate}>
          <Icon name="alert" size={11}/> Override entire gate
        </button>
        {onAddObjective && (
          <button className="btn btn-sm" onClick={onAddObjective}>
            <Icon name="plus" size={11}/> Add Objective
          </button>
        )}
        <div className="rar-foot-spacer"/>
        <button className="btn btn-sm" onClick={onApproveAll} disabled={allResolved}>
          Approve all remaining ({total - decided})
        </button>
        <button className="btn btn-sm btn-primary" disabled={!allResolved} onClick={onSubmit}>
          <Icon name="check" size={11}/> Confirm Audit Scope
        </button>
      </div>
    </div>
  );
}

function ObjectiveRow({ obj, approval, linkedRiskCE, riskReduction, onSetRiskReduction, onApprove, onAdjust }) {
  const status = approval.status || "pending";
  const adj = approval.adjustments || null;
  const effPri = adj?.priority ?? obj.priority;
  const effHours = adj?.hours ?? obj.hours;
  const effSprint = adj?.sprint ?? obj.sprint;
  const wasAdjusted = ["submitted", "manager_approved", "rejected"].includes(status);
  const [sliderOpen, setSliderOpen] = React.useState(false);

  return (
    <div className={`rar-row sar-row rar-row-${status}`}>
      <div className="rar-td sar-td-pri">
        <span className="sar-pri-chip" style={{color: priColor(effPri), background: priSoft(effPri)}}>
          {effPri}
        </span>
        {wasAdjusted && effPri !== obj.priority && (
          <div className="rar-was mono">was {obj.priority}</div>
        )}
      </div>

      <div className="rar-td rar-td-name">
        <div className="rar-rname">{obj.objective}</div>
        <div className="rar-rmeta mono">
          <span>{obj.id}</span>
          <span>·</span>
          <span>{(obj.controls || []).length} control{(obj.controls || []).length !== 1 ? "s" : ""}</span>
        </div>
      </div>

      <div className="rar-td sar-td-risk">
        {(() => {
          const ids = obj.linked_risks?.length ? obj.linked_risks : obj.linked_risk ? [obj.linked_risk] : [];
          return ids.length === 0 ? <span className="mono" style={{color:"var(--ink-4)"}}>—</span>
            : ids.slice(0, 2).map((id, i) => (
              <span key={i} className="sar-risk-chip mono" style={{marginBottom: 1}}>{id}</span>
            )).concat(ids.length > 2
              ? [<span key="more" className="mono" style={{fontSize:9.5, color:"var(--ink-3)"}}>+{ids.length-2}</span>]
              : []);
        })()}
      </div>

      <div className="rar-td sar-td-ctrl">
        <span className="mono" style={{fontSize: 10.5, color: CE_COLOR[linkedRiskCE] || "var(--ink-2)", fontWeight: 500}}>
          {CE_LABEL[linkedRiskCE] || linkedRiskCE}
        </span>
      </div>

      <div className="rar-td sar-td-rr">
        {sliderOpen ? (
          <div className="sar-rr-slider-wrap">
            <span className="mono" style={{fontSize: 10, color: "var(--ink)", fontWeight: 500, minWidth: 28}}>{riskReduction}%</span>
            <input type="range" min="5" max="50" step="5" value={riskReduction}
              onChange={e => onSetRiskReduction(parseInt(e.target.value))}
              className="ar-slider sar-rr-slider"/>
            <button className="sar-rr-close" onClick={() => setSliderOpen(false)}>✕</button>
          </div>
        ) : (
          <button className="sar-rr-badge" onClick={() => setSliderOpen(true)}
            title="Click to adjust expected risk reduction">
            {riskReduction}%
          </button>
        )}
      </div>

      <div className="rar-td sar-td-num">
        <span className="mono" style={{fontWeight:500,color:"var(--ink)"}}>{effHours}h</span>
        {wasAdjusted && effHours !== obj.hours && (
          <div className="rar-was mono">was {obj.hours}h</div>
        )}
      </div>

      <div className="rar-td sar-td-num">
        <span className="mono" style={{color:"var(--ink-2)"}} title="Fiscal Quarter">Q{effSprint}</span>
        {wasAdjusted && effSprint !== obj.sprint && (
          <div className="rar-was mono">was Q{obj.sprint}</div>
        )}
      </div>

      <div className="rar-td rar-td-action">
        {status === "pending" && (
          obj._isNew ? (
            <div className="rar-actions">
              <button className="btn btn-sm" onClick={onAdjust}>
                <Icon name="edit" size={10}/> Define scope
              </button>
              <div className="mono" style={{fontSize: 9, color: "var(--amber-ink)"}}>New — must be defined</div>
            </div>
          ) : (
            <div className="rar-actions">
              <button className="btn btn-sm rar-btn-approve" onClick={onApprove}>
                <Icon name="check" size={10}/> Approve
              </button>
              <button className="btn btn-sm" onClick={onAdjust}>Adjust</button>
            </div>
          )
        )}
        {status === "approved" && (
          <div className="rar-disposition rar-disposition-approved">
            <Icon name="check" size={11}/><span>Approved as scoped</span>
          </div>
        )}
        {status === "submitted" && (
          <div className="rar-disposition rar-disposition-adjusted">
            <Icon name="alert" size={11}/><span>Awaiting {approval.managerName || "manager"} review</span>
          </div>
        )}
        {status === "manager_approved" && (
          <div className="rar-disposition rar-disposition-signed">
            <Icon name="check" size={11}/><span>Approved by {approval.reviewerName || "manager"}</span>
          </div>
        )}
        {status === "rejected" && (
          <div className="rar-actions">
            <div className="rar-disposition" style={{color: "var(--red-ink)"}}>
              <Icon name="alert" size={11}/><span>Rejected — revise</span>
            </div>
            <button className="btn btn-sm" onClick={onAdjust}>
              <Icon name="edit" size={10}/> Revise
            </button>
          </div>
        )}
      </div>

      {wasAdjusted && (
        <div className="rar-row-detail">
          <div className="rar-detail-rationale">
            <div className="rar-detail-label mono">
              RATIONALE — {approval.adjustedBy || "Auditor"} · {approval.adjustedAt
                ? new Date(approval.adjustedAt).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit"})
                : "—"}
            </div>
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
    </div>
  );
}

function AdjustObjectiveModal({ open, obj, risks = [], ticker, runId, onClose, onSubmit }) {
  const [objectiveText, setObjectiveText] = useState(obj?.objective || "");
  const [priority, setPriority] = useState(obj?.priority || "P2");
  const [sprint, setSprint] = useState(obj?.sprint ?? 1);
  const [hours, setHours] = useState(String(obj?.hours ?? 40));
  const [rationale, setRationale] = useState("");
  const [linkedRiskIds, setLinkedRiskIds] = useState(() => {
    if (obj?.linked_risks?.length) return obj.linked_risks;
    if (obj?.linked_risk) return [obj.linked_risk];
    return [];
  });
  const [controlRefs, setControlRefs] = useState(() => obj?.controls || []);
  const [residual, setResidual] = useState(String(obj?.residualRiskReduction ?? 0));
  const [aiState, setAiState] = useState({ loading: false, error: null, reco: null });
  const [ctrlPickerOpen, setCtrlPickerOpen] = useState(false);
  const [ctrlSearch, setCtrlSearch] = useState("");
  const [ctrlCreateOpen, setCtrlCreateOpen] = useState(false);
  const [newCtrl, setNewCtrl] = useState({ ref: "", name: "", framework: "", desc: "" });
  const [ctrlCreateErr, setCtrlCreateErr] = useState("");

  useEffect(() => {
    if (open && obj) {
      setObjectiveText(obj.objective || "");
      setPriority(obj.priority);
      setSprint(obj.sprint);
      setHours(String(obj.hours ?? 40));
      setRationale("");
      setLinkedRiskIds(obj.linked_risks?.length ? obj.linked_risks : obj.linked_risk ? [obj.linked_risk] : []);
      setControlRefs(obj.controls || []);
      setResidual(String(obj.residualRiskReduction ?? 0));
      setAiState({ loading: false, error: null, reco: null });
      setCtrlPickerOpen(false);
      setCtrlCreateOpen(false);
      setCtrlSearch("");
    }
  }, [open, obj?.id]);

  // #2 — AI-assisted HITL Gate 2: draft a scope the planner accepts or overrides.
  // Prefills the unambiguous fields (priority/sprint/hours/linked risks); the AI's
  // residual-reduction reasoning goes into the rationale to avoid a unit mismatch
  // (endpoint returns a %, the slider below is in score points).
  const aiAvailable = typeof window !== "undefined" && window.MCP?.aiGate2Recommend;
  async function runAiSuggest() {
    if (!aiAvailable || !obj) return;
    setAiState({ loading: true, error: null, reco: null });
    try {
      const res = await window.MCP.aiGate2Recommend(ticker || "", [obj], risks, runId || null);
      const reco = (res?.recommendations || [])[0];
      if (!reco) throw new Error("no recommendation returned");
      if (reco.suggested_priority) setPriority(reco.suggested_priority);
      if (typeof reco.suggested_sprint === "number") setSprint(reco.suggested_sprint);
      if (typeof reco.suggested_hours === "number") setHours(String(reco.suggested_hours));
      if (Array.isArray(reco.suggested_linked_risks) && reco.suggested_linked_risks.length) {
        const valid = reco.suggested_linked_risks.filter(id => risks.some(r => r.id === id));
        if (valid.length) setLinkedRiskIds(valid);
      }
      const pct = reco.suggested_residual_reduction;
      setRationale(
        `[AI suggestion] ${reco.rationale || ""}`.trim()
        + (typeof pct === "number" ? ` Expected residual-risk reduction ≈ ${pct}%.` : "")
      );
      setAiState({ loading: false, error: null, reco });
    } catch (e) {
      setAiState({ loading: false, error: e.message || "AI unavailable", reco: null });
    }
  }

  if (!open || !obj) return null;

  const sortedRisks = [...risks].sort((a, b) => b.score - a.score);
  const hoursNum = parseInt(hours, 10);
  const hoursValid = !isNaN(hoursNum) && hoursNum >= 1 && hoursNum <= 9999;

  const toggleRisk = (id) => {
    setLinkedRiskIds(prev => prev.includes(id) ? prev.filter(r => r !== id) : [...prev, id]);
  };

  const ragInk = { R: "var(--red-ink)", A: "var(--amber-ink)", G: "var(--green-ink)" };
  const primaryRisk = sortedRisks.find(r => linkedRiskIds.includes(r.id));
  const maxResidual = primaryRisk ? primaryRisk.score : 5;

  const residualNum = parseFloat(residual);
  const residualValid = residual.trim() === "" || (!isNaN(residualNum) && residualNum >= 0 && residualNum <= maxResidual);
  const residualReduction = residualValid && residual.trim() !== "" ? residualNum : 0;
  const residualScore = primaryRisk
    ? Math.max(0, parseFloat((primaryRisk.score - residualReduction).toFixed(1)))
    : null;
  const residualRag = residualScore == null ? null : residualScore >= 15 ? "R" : residualScore >= 9 ? "A" : "G";

  const addControl = (ref) => setControlRefs(prev => prev.includes(ref) ? prev : [...prev, ref]);
  const removeControl = (ref) => setControlRefs(prev => prev.filter(r => r !== ref));

  const CTRL_BY_REF = Object.fromEntries((window.MASTER_CONTROLS || []).map(c => [c.ref, c]));
  const addableControls = (window.MASTER_CONTROLS || []).filter(c =>
    !controlRefs.includes(c.ref) &&
    (ctrlSearch === "" || c.name.toLowerCase().includes(ctrlSearch.toLowerCase()) || c.ref.toLowerCase().includes(ctrlSearch.toLowerCase()))
  );

  async function handleCreateControl() {
    const ref = newCtrl.ref.trim().toUpperCase();
    if (!ref) { setCtrlCreateErr("Control reference is required."); return; }
    if (CTRL_BY_REF[ref]) { setCtrlCreateErr(`${ref} already exists in the control library.`); return; }
    if (!newCtrl.name.trim()) { setCtrlCreateErr("Control name is required."); return; }
    const ctrl = {
      ref, framework: newCtrl.framework.trim() || "Custom", name: newCtrl.name.trim(),
      category: "Custom", domain: "Custom", description: newCtrl.desc.trim(), desc: newCtrl.desc.trim(),
    };
    try {
      await fetch("/api/risk-register/controls", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ctrl),
      });
    } catch (_) {}
    if (window.MASTER_CONTROLS) window.MASTER_CONTROLS.push(ctrl);
    addControl(ref);
    setCtrlCreateOpen(false);
    setNewCtrl({ ref: "", name: "", framework: "", desc: "" });
    setCtrlCreateErr("");
  }

  const origLinkedRiskIds = obj.linked_risks?.length ? obj.linked_risks
    : obj.linked_risk ? [obj.linked_risk] : [];
  const origResidual = obj.residualRiskReduction ?? 0;
  const origControls = obj.controls || [];

  const riskIdsChanged = JSON.stringify([...linkedRiskIds].sort()) !== JSON.stringify([...origLinkedRiskIds].sort());
  const controlsChanged = JSON.stringify([...controlRefs].sort()) !== JSON.stringify([...origControls].sort());
  const objectiveChanged = objectiveText.trim() !== (obj.objective || "").trim();
  // Tracked for the "was X" diff indicators, not required to submit — a written
  // rationale on its own is sufficient grounds for Adjust (matches Gate 1).
  const changed = priority !== obj.priority || sprint !== obj.sprint
    || (hoursValid && hoursNum !== obj.hours)
    || riskIdsChanged
    || controlsChanged
    || objectiveChanged
    || residualReduction !== origResidual;
  const valid = rationale.trim().length >= 30 && hoursValid && residualValid && objectiveText.trim().length > 0;

  // Normalized to the same keys as the submitted adjustments, so the backend
  // can tell whether the preparer kept the AI's suggestion or overrode it —
  // see approval_tasks.ai_suggested / ai_accepted.
  const aiSuggestedFields = aiState.reco ? {
    ...(aiState.reco.suggested_priority != null            ? { priority: aiState.reco.suggested_priority } : {}),
    ...(typeof aiState.reco.suggested_sprint === "number"   ? { sprint: aiState.reco.suggested_sprint } : {}),
    ...(typeof aiState.reco.suggested_hours === "number"    ? { hours: aiState.reco.suggested_hours } : {}),
    ...(Array.isArray(aiState.reco.suggested_linked_risks)  ? { linked_risks: aiState.reco.suggested_linked_risks } : {}),
    ...(typeof aiState.reco.suggested_residual_reduction === "number" ? { residualRiskReduction: aiState.reco.suggested_residual_reduction } : {}),
  } : null;

  return (
    <div className="modal open" onClick={(e) => { if (e.target.classList.contains("modal")) onClose(); }}>
      <div className="modal-box" style={{width: 640}}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Adjust Objective · {obj.id}</div>
            <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", marginTop: 3, maxWidth: 500, lineHeight: 1.4}}>
              {obj.objective}
            </div>
          </div>
          <div style={{display: "flex", alignItems: "center", gap: 6}}>
            {aiAvailable && (
              <button className="btn btn-sm" onClick={runAiSuggest} disabled={aiState.loading}
                title="Draft a scope with Claude — review and override as needed">
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
          <div className="mono" style={{padding: "4px 16px", fontSize: 10.5, color: "var(--acc-ink)"}}>
            AI recommends: <b>{aiState.reco.recommendation}</b> — fields pre-filled below, edit freely.
          </div>
        )}
        <div className="modal-body">
          <div className="ar-field" style={{marginBottom: 14}}>
            <label className="ar-label">Objective</label>
            <textarea value={objectiveText} onChange={e => setObjectiveText(e.target.value)}
              className="fi-ta" rows={2} placeholder="Describe the audit objective…"
              style={{minHeight: 50, width: "100%", boxSizing: "border-box", fontFamily: "inherit"}}/>
            {obj._isNew ? (
              <div className="ar-orig mono">New objective — name it and set scope below</div>
            ) : (
              <div className="ar-orig mono">Original: {obj.objective}</div>
            )}
          </div>

          <div className="ar-grid" style={{gridTemplateColumns:"1fr 1fr 1fr"}}>
            <div className="ar-field">
              <label className="ar-label">Priority</label>
              <div className="ar-ce-row">
                {["P1","P2","P3"].map(v => (
                  <button key={v} className={`ar-ce-opt ${priority === v ? "active" : ""}`}
                    onClick={() => setPriority(v)}>{v}</button>
                ))}
              </div>
              <div className="ar-orig mono">AI scoped: {obj.priority}</div>
            </div>

            <div className="ar-field">
              <label className="ar-label">Fiscal Quarter</label>
              <div className="ar-ce-row">
                {[1,2,3,4].map(v => (
                  <button key={v} className={`ar-ce-opt ${sprint === v ? "active" : ""}`}
                    onClick={() => setSprint(v)}>Q{v}</button>
                ))}
              </div>
              <div className="ar-orig mono">AI scoped: Q{obj.sprint}</div>
            </div>

            <div className="ar-field">
              <label className="ar-label">Hours</label>
              <input type="number" min="1" max="9999" value={hours}
                onChange={e => setHours(e.target.value)}
                className="fi-input"
                style={{fontFamily: "Geist Mono, monospace", fontSize: 13}}/>
              <div className="ar-orig mono">
                AI estimated: {obj.hours}h
                {!hoursValid && hours !== "" && <span style={{color:"var(--red-ink)", marginLeft:6}}>invalid</span>}
              </div>
            </div>
          </div>

          {/* Linked Key Risks — multi-select */}
          <div className="ar-field" style={{marginBottom: 14}}>
            <label className="ar-label">
              Linked Key Risks
              <span className="muted" style={{marginLeft: 6}}>· select one or more</span>
              {linkedRiskIds.length > 0 && (
                <span className="mono" style={{marginLeft: 6, color: "var(--acc-ink)", fontSize: 10.5}}>
                  {linkedRiskIds.length} selected
                </span>
              )}
            </label>
            <div className="ar-risk-list">
              {sortedRisks.length === 0 ? (
                <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", padding: "7px 10px"}}>
                  No risks available — run the pipeline first
                </div>
              ) : sortedRisks.map(r => {
                const checked = linkedRiskIds.includes(r.id);
                return (
                  <label key={r.id} className={`ar-risk-item${checked ? " selected" : ""}`}>
                    <input type="checkbox" checked={checked} onChange={() => toggleRisk(r.id)}/>
                    <span className="mono" style={{color: ragInk[r.rag], fontSize: 10, fontWeight: 600, minWidth: 34}}>
                      {r.id}
                    </span>
                    <span style={{flex: 1, fontSize: 11, color: "var(--ink)"}}>{r.name}</span>
                    <span className="mono" style={{fontSize: 10, color: ragInk[r.rag], marginLeft: 4}}>
                      {(r.score ?? 0).toFixed(1)}
                    </span>
                    <span className={`rag-dot ${r.rag}`} style={{flexShrink: 0}}/>
                  </label>
                );
              })}
            </div>
            {origLinkedRiskIds.length > 0 && (
              <div className="ar-orig mono">Original: {origLinkedRiskIds.join(", ")}</div>
            )}
          </div>

          {/* Project Residual Risk */}
          <div className="ar-field" style={{marginBottom: 14}}>
            <label className="ar-label">
              Project Risk Reduction
              <span className="muted" style={{marginLeft: 6}}>
                · max {maxResidual.toFixed(1)} pts (cannot exceed the linked risk's score)
              </span>
            </label>
            <input type="number" min="0" max={maxResidual} step="0.1" value={residual}
              onChange={e => setResidual(e.target.value)}
              className="fi-input" style={{fontFamily: "Geist Mono, monospace", fontSize: 13}}/>
            <div className="ar-orig mono">
              AI scoped: {origResidual.toFixed(1)} pts
              {!residualValid && residual !== "" && <span style={{color:"var(--red-ink)", marginLeft:6}}>must be between 0 and {maxResidual.toFixed(1)}</span>}
            </div>
            {primaryRisk ? (
              <div className="ar-residual-preview">
                <span className="mono" style={{color: "var(--ink-3)"}}>
                  {primaryRisk.id} base:
                  <span style={{color: ragInk[primaryRisk.rag], fontWeight: 600, marginLeft: 4}}>
                    {primaryRisk.score.toFixed(1)}
                  </span>
                </span>
                <span className="mono" style={{color: "var(--ink-3)"}}>→</span>
                <span className="mono">
                  Residual:
                  <span style={{color: ragInk[residualRag], fontWeight: 700, marginLeft: 4}}>
                    {residualScore.toFixed(1)}
                  </span>
                </span>
                {residualReduction > 0 && (
                  <span className="mono" style={{color: "var(--green-ink)", marginLeft: "auto"}}>
                    −{residualReduction.toFixed(1)} pts applied
                  </span>
                )}
              </div>
            ) : (
              <div className="ar-orig mono">Select a linked risk above to see residual projection</div>
            )}
          </div>

          <div className="ar-field" style={{marginBottom: 14}}>
            <label className="ar-label">
              Controls in scope
              <span className="muted" style={{marginLeft: 6}}>· choose which controls this audit will test</span>
            </label>
            <div style={{display:"flex",flexWrap:"wrap",gap:5, marginBottom: 6}}>
              {controlRefs.map(ref => {
                const ctrl = CTRL_BY_REF[ref];
                return (
                  <span key={ref} className="mono" style={{display:"flex", alignItems:"center", gap:4, fontSize:10,padding:"2px 4px 2px 7px",border:"1px solid var(--line)",borderRadius:4,color:"var(--ink-2)", background:"var(--surface-2, var(--surface))"}}>
                    {ref}{ctrl && <span style={{color:"var(--ink-3)"}}>· {ctrl.name}</span>}
                    <button type="button" onClick={() => removeControl(ref)}
                      style={{border:"none", background:"transparent", cursor:"pointer", color:"var(--ink-3)", fontSize:11, lineHeight:1, padding:"0 2px"}}>×</button>
                  </span>
                );
              })}
              {controlRefs.length === 0 && (
                <span className="mono" style={{fontSize:10.5,color:"var(--ink-3)"}}>None assigned</span>
              )}
            </div>
            <div style={{display:"flex", gap:6}}>
              <button type="button" className="btn btn-sm" style={{fontSize:10, padding:"2px 8px"}}
                onClick={() => { setCtrlPickerOpen(p => !p); setCtrlCreateOpen(false); }}>+ Add</button>
              <button type="button" className="btn btn-sm" style={{fontSize:10, padding:"2px 8px"}}
                onClick={() => { setCtrlCreateOpen(p => !p); setCtrlPickerOpen(false); setCtrlCreateErr(""); }}
                title="Create a brand-new control with a new reference number">+ New</button>
            </div>
            {ctrlPickerOpen && (
              <div style={{marginTop:6, padding:8, background:"var(--surface-2, var(--surface))", border:"1px solid var(--line)", borderRadius:6, maxHeight:180, display:"flex", flexDirection:"column", gap:6}}>
                <input value={ctrlSearch} onChange={e => setCtrlSearch(e.target.value)}
                  placeholder="Search controls…" className="fi-input" style={{fontSize:10.5, padding:"3px 7px"}} autoFocus/>
                <div style={{overflowY:"auto", maxHeight:130, display:"flex", flexDirection:"column", gap:2}}>
                  {addableControls.slice(0,20).map(c => (
                    <button key={c.ref} type="button"
                      onClick={() => { addControl(c.ref); setCtrlPickerOpen(false); setCtrlSearch(""); }}
                      style={{display:"flex", gap:6, padding:"4px 6px", border:"none", background:"transparent", cursor:"pointer", textAlign:"left", fontSize:10, borderRadius:3}}>
                      <span className="mono" style={{fontWeight:600, color:"var(--acc-ink, var(--ink))", minWidth:46}}>{c.ref}</span>
                      <span style={{color:"var(--ink)"}}>{c.name}</span>
                      <span style={{marginLeft:"auto", fontSize:9, color:"var(--ink-3)"}}>{c.category}</span>
                    </button>
                  ))}
                  {addableControls.length === 0 && <span className="mono" style={{fontSize:9.5, color:"var(--ink-3)", padding:4}}>No matches</span>}
                </div>
              </div>
            )}
            {ctrlCreateOpen && (
              <div style={{marginTop:6, padding:10, background:"var(--surface-2, var(--surface))", border:"1px solid var(--acc-ink, var(--line))", borderRadius:6, display:"flex", flexDirection:"column", gap:7}}>
                <div style={{display:"flex", gap:6}}>
                  <input placeholder="Ref * e.g. AC-06" value={newCtrl.ref} onChange={e => setNewCtrl(p => ({...p, ref: e.target.value}))}
                    className="fi-input" style={{flex:"0 0 100px", fontSize:10.5, padding:"3px 6px"}}/>
                  <input placeholder="Framework" value={newCtrl.framework} onChange={e => setNewCtrl(p => ({...p, framework: e.target.value}))}
                    className="fi-input" style={{flex:1, fontSize:10.5, padding:"3px 6px"}}/>
                </div>
                <input placeholder="Control name *" value={newCtrl.name} onChange={e => setNewCtrl(p => ({...p, name: e.target.value}))}
                  className="fi-input" style={{fontSize:10.5, padding:"3px 6px"}}/>
                <textarea placeholder="Description" value={newCtrl.desc} onChange={e => setNewCtrl(p => ({...p, desc: e.target.value}))}
                  rows={2} className="fi-input" style={{fontSize:10.5, padding:"3px 6px", resize:"vertical", fontFamily:"inherit"}}/>
                {ctrlCreateErr && <div className="mono" style={{fontSize:9.5, color:"var(--red-ink)"}}>{ctrlCreateErr}</div>}
                <div style={{display:"flex", gap:6}}>
                  <button type="button" className="btn btn-sm btn-primary" style={{fontSize:10, padding:"2px 9px"}} onClick={handleCreateControl}>Create &amp; Assign</button>
                  <button type="button" className="btn btn-sm" style={{fontSize:10, padding:"2px 9px"}} onClick={() => { setCtrlCreateOpen(false); setCtrlCreateErr(""); }}>Cancel</button>
                </div>
              </div>
            )}
          </div>

          <div className="ar-rationale">
            <label className="ar-label">
              Rationale
              <span className="muted"> · captured verbatim, routed to your manager for review</span>
            </label>
            <textarea className="fi-ta" value={rationale} onChange={e => setRationale(e.target.value)}
              placeholder="Describe the basis for this adjustment — resource constraints, risk prioritisation rationale, scheduling dependencies, or control coverage changes considered. Minimum 30 characters."
              style={{minHeight: 80}}/>
            <div className="ar-rationale-meta mono">
              <span style={{color: rationale.trim().length >= 30 ? "var(--green-ink)" : "var(--ink-3)"}}>
                {rationale.trim().length} / 30 chars
              </span>
              {!changed && rationale.trim().length >= 30 && <span className="muted">· reaffirming as scoped, with rationale on file</span>}
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
          <div style={{display:"flex",gap:6}}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!valid}
              onClick={() => onSubmit({
                objective: objectiveText.trim(),
                priority,
                sprint,
                hours: hoursValid ? hoursNum : obj.hours,
                linked_risks: linkedRiskIds,
                controls: controlRefs,
                residualRiskReduction: residualReduction,
                rationale: rationale.trim(),
                ai_suggested: aiSuggestedFields,
              })}>
              Submit Adjustment
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ScopeApprovalReview, AdjustObjectiveModal });
export { ScopeApprovalReview, AdjustObjectiveModal };
