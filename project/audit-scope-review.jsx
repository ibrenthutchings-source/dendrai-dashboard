/* ============================================================
   Per-Objective Scope Approval Review (HITL Gate 2)
   - Lists every audit objective with Approve / Adjust controls
   - Adjusted objectives require sequential sign-off:
       1. CAE (Sarah Lin)       — Chief Audit Executive
       2. CFO (Marcus Reed)     — Chief Financial Officer
       3. Audit Committee       — J. Vance, Chair
   - Reuses .rar styles; adds .sar column overrides
   ============================================================ */

function priColor(p) {
  return p === "P1" ? "var(--red-ink)" : p === "P2" ? "var(--amber-ink)" : "var(--green-ink)";
}
function priSoft(p) {
  return p === "P1" ? "var(--red-soft)" : p === "P2" ? "var(--amber-soft)" : "var(--green-soft)";
}

function ScopeApprovalReview({
  objectives,
  approvals,
  onApproveObjective,
  onOpenAdjust,
  onApproveAll,
  onSignoff,
  onSubmit,
  onOverrideGate,
}) {
  if (!objectives || objectives.length === 0) return null;

  const total = objectives.length;
  const decided = objectives.filter(o => {
    const a = approvals[o.id];
    return a && (a.status === "approved" || a.status === "signed");
  }).length;
  const adjustedCount = objectives.filter(o =>
    approvals[o.id]?.status === "adjusted" || approvals[o.id]?.status === "signed"
  ).length;
  const pendingSig = objectives.filter(o => approvals[o.id]?.status === "adjusted").length;
  const allResolved = decided === total;

  return (
    <div className="rar sar" data-screen-label="HITL · Scope approval">
      <div className="rar-head">
        <div className="rar-head-l">
          <div className="rar-pill sar-pill">
            <span className="dot"/>HITL · GATE 2
          </div>
          <div className="rar-title">Audit Scope · Per-Objective Review</div>
          <div className="rar-sub">
            Approve each objective as-is, or adjust priority / hours / sprint with rationale.
            Adjustments are routed for sign-off:
            <span className="rar-sub-chain">CAE → CFO → Audit Committee</span>.
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
              {adjustedCount > 0 && <span className="mono muted">· {adjustedCount} adjusted</span>}
              {pendingSig > 0 && (
                <span className="mono" style={{color:"var(--amber-ink)"}}>· {pendingSig} awaiting sign-off</span>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="rar-table-wrap">
        <div className="rar-thead sar-thead">
          <div className="rar-th sar-th-pri">Pri</div>
          <div className="rar-th sar-th-obj">Objective</div>
          <div className="rar-th sar-th-risk">Risk</div>
          <div className="rar-th sar-th-num">Hrs</div>
          <div className="rar-th sar-th-num">Sprint</div>
          <div className="rar-th sar-th-action">Disposition</div>
        </div>
        <div className="rar-tbody">
          {objectives.map(o => (
            <ObjectiveRow
              key={o.id}
              obj={o}
              approval={approvals[o.id] || { status: "pending" }}
              onApprove={() => onApproveObjective(o.id)}
              onAdjust={() => onOpenAdjust(o.id)}
              onSignoff={(role) => onSignoff(o.id, role)}
            />
          ))}
        </div>
      </div>

      <div className="rar-foot">
        <button className="btn btn-sm" onClick={onOverrideGate}>
          <Icon name="alert" size={11}/> Override entire gate
        </button>
        <div className="rar-foot-spacer"/>
        <button className="btn btn-sm" onClick={onApproveAll} disabled={allResolved}>
          Approve all remaining ({total - decided - pendingSig})
        </button>
        <button className="btn btn-sm btn-primary" disabled={!allResolved} onClick={onSubmit}>
          <Icon name="check" size={11}/> Confirm Audit Scope
        </button>
      </div>
    </div>
  );
}

function ObjectiveRow({ obj, approval, onApprove, onAdjust, onSignoff }) {
  const status = approval.status || "pending";
  const adj = approval.adjustments || null;
  const effPri = adj?.priority ?? obj.priority;
  const effHours = adj?.hours ?? obj.hours;
  const effSprint = adj?.sprint ?? obj.sprint;
  const isAdjusted = status === "adjusted" || status === "signed";

  return (
    <div className={`rar-row sar-row rar-row-${status}`}>
      <div className="rar-td sar-td-pri">
        <span className="sar-pri-chip" style={{color: priColor(effPri), background: priSoft(effPri)}}>
          {effPri}
        </span>
        {isAdjusted && effPri !== obj.priority && (
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
        <span className="sar-risk-chip mono">{obj.linked_risk}</span>
      </div>

      <div className="rar-td sar-td-num">
        <span className="mono" style={{fontWeight:500,color:"var(--ink)"}}>{effHours}h</span>
        {isAdjusted && effHours !== obj.hours && (
          <div className="rar-was mono">was {obj.hours}h</div>
        )}
      </div>

      <div className="rar-td sar-td-num">
        <span className="mono" style={{color:"var(--ink-2)"}}>S{effSprint}</span>
        {isAdjusted && effSprint !== obj.sprint && (
          <div className="rar-was mono">was S{obj.sprint}</div>
        )}
      </div>

      <div className="rar-td rar-td-action">
        {status === "pending" && (
          <div className="rar-actions">
            <button className="btn btn-sm rar-btn-approve" onClick={onApprove}>
              <Icon name="check" size={10}/> Approve
            </button>
            <button className="btn btn-sm" onClick={onAdjust}>Adjust</button>
          </div>
        )}
        {status === "approved" && (
          <div className="rar-disposition rar-disposition-approved">
            <Icon name="check" size={11}/><span>Approved as scoped</span>
          </div>
        )}
        {status === "adjusted" && (
          <div className="rar-disposition rar-disposition-adjusted">
            <Icon name="alert" size={11}/><span>Awaiting sign-off</span>
          </div>
        )}
        {status === "signed" && (
          <div className="rar-disposition rar-disposition-signed">
            <Icon name="check" size={11}/><span>Adjustment signed</span>
          </div>
        )}
      </div>

      {isAdjusted && (
        <div className="rar-row-detail">
          <div className="rar-detail-rationale">
            <div className="rar-detail-label mono">
              RATIONALE — {approval.adjustedBy || "Auditor"} · {approval.adjustedAt
                ? new Date(approval.adjustedAt).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit"})
                : "—"}
            </div>
            <div className="rar-detail-text">{approval.rationale}</div>
          </div>
          <div className="rar-signoff-chain">
            {SIGNOFFS.map((s, i) => {
              const sig = approval.signoffs?.[s.id];
              const isSigned = !!sig?.signedAt;
              const prevSigned = i === 0 || approval.signoffs?.[SIGNOFFS[i-1].id]?.signedAt;
              const canSign = !isSigned && prevSigned;
              return (
                <div key={s.id} className={`rar-sig ${isSigned ? "rar-sig-signed" : canSign ? "rar-sig-active" : "rar-sig-blocked"}`}>
                  <div className="rar-sig-num mono">{i+1}</div>
                  <div className="rar-sig-body">
                    <div className="rar-sig-role">{s.role}</div>
                    <div className="rar-sig-who mono">{isSigned ? sig.who : s.who}</div>
                  </div>
                  {isSigned ? (
                    <div className="rar-sig-stamp">
                      <Icon name="check" size={11}/>
                      <span className="mono">{new Date(sig.signedAt).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit"})}</span>
                    </div>
                  ) : canSign ? (
                    <button className="btn btn-sm rar-sig-btn" onClick={() => onSignoff(s.id)}>
                      Sign as {s.role}
                    </button>
                  ) : (
                    <span className="rar-sig-pending mono">Blocked</span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function AdjustObjectiveModal({ open, obj, onClose, onSubmit }) {
  const [priority, setPriority] = useState(obj?.priority || "P2");
  const [sprint, setSprint] = useState(obj?.sprint ?? 1);
  const [hours, setHours] = useState(obj?.hours ?? 40);
  const [rationale, setRationale] = useState("");

  useEffect(() => {
    if (open && obj) {
      setPriority(obj.priority);
      setSprint(obj.sprint);
      setHours(obj.hours);
      setRationale("");
    }
  }, [open, obj?.id]);

  if (!open || !obj) return null;

  const changed = priority !== obj.priority || sprint !== obj.sprint || hours !== obj.hours;
  const valid = changed && rationale.trim().length >= 30;

  return (
    <div className="modal open" onClick={(e) => { if (e.target.classList.contains("modal")) onClose(); }}>
      <div className="modal-box" style={{width: 620}}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Adjust Objective · {obj.id}</div>
            <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", marginTop: 3, maxWidth: 480, lineHeight: 1.4}}>
              {obj.objective}
            </div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
        </div>
        <div className="modal-body">
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
              <label className="ar-label">Sprint</label>
              <div className="ar-ce-row">
                {[1,2,3].map(v => (
                  <button key={v} className={`ar-ce-opt ${sprint === v ? "active" : ""}`}
                    onClick={() => setSprint(v)}>S{v}</button>
                ))}
              </div>
              <div className="ar-orig mono">AI scoped: S{obj.sprint}</div>
            </div>

            <div className="ar-field">
              <label className="ar-label">Hours <span className="mono ar-val">{hours}h</span></label>
              <input type="range" min="8" max="200" step="4" value={hours}
                onChange={e => setHours(parseInt(e.target.value))} className="ar-slider"/>
              <div className="ar-orig mono">AI estimated: {obj.hours}h</div>
            </div>
          </div>

          <div className="ar-field" style={{marginBottom: 14}}>
            <label className="ar-label">Controls in scope</label>
            <div style={{display:"flex",flexWrap:"wrap",gap:5}}>
              {(obj.controls || []).map((c,i) => (
                <span key={i} className="mono"
                  style={{fontSize:10,padding:"2px 7px",border:"1px solid var(--line)",borderRadius:4,color:"var(--ink-2)"}}>
                  {c}
                </span>
              ))}
            </div>
          </div>

          <div className="ar-rationale">
            <label className="ar-label">
              Rationale
              <span className="muted"> · captured verbatim, routed to CAE / CFO / Audit Committee</span>
            </label>
            <textarea className="fi-ta" value={rationale} onChange={e => setRationale(e.target.value)}
              placeholder="Describe the basis for this adjustment — resource constraints, risk prioritisation rationale, scheduling dependencies, or control coverage changes considered. Minimum 30 characters."
              style={{minHeight: 80}}/>
            <div className="ar-rationale-meta mono">
              <span style={{color: rationale.trim().length >= 30 ? "var(--green-ink)" : "var(--ink-3)"}}>
                {rationale.trim().length} / 30 chars
              </span>
              {!changed && <span className="muted">· no changes made yet</span>}
            </div>
          </div>

          <div className="ar-signoff-preview">
            <div className="rar-detail-label mono">SIGN-OFF ROUTING ON SUBMIT</div>
            <div className="ar-chain">
              {SIGNOFFS.map((s, i) => (
                <React.Fragment key={s.id}>
                  <div className="ar-chain-node">
                    <div className="ar-chain-num mono">{i+1}</div>
                    <div>
                      <div className="ar-chain-role">{s.role}</div>
                      <div className="ar-chain-who mono">{s.who}</div>
                    </div>
                  </div>
                  {i < SIGNOFFS.length - 1 && <div className="ar-chain-arrow">→</div>}
                </React.Fragment>
              ))}
            </div>
          </div>
        </div>
        <div className="modal-foot">
          <span className="muted mono" style={{fontSize: 11}}>
            {changed ? "Submitting routes for 3-step sign-off" : "Adjust at least one field to continue"}
          </span>
          <div style={{display:"flex",gap:6}}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!valid}
              onClick={() => onSubmit({ priority, sprint, hours, rationale: rationale.trim() })}>
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
