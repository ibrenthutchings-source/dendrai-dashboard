/* ============================================================
   Per-Risk Approval Review (HITL Gate 1)
   - Lists every risk inline with Approve / Adjust controls
   - Adjusted risks require sequential sign-off:
       1. CAE (Sarah Lin)        — Chief Audit Executive
       2. CFO (Marcus Reed)      — Chief Financial Officer
       3. Audit Committee        — J. Vance, Chair
   - Rationale captured verbatim into the audit trail
   ============================================================ */

const SIGNOFFS = [
  { id: "cae", role: "CAE",              who: "Sarah Lin",      title: "Chief Audit Executive" },
  { id: "cfo", role: "CFO",              who: "Marcus Reed",    title: "Chief Financial Officer" },
  { id: "ac",  role: "Audit Committee",  who: "J. Vance",       title: "Audit Committee, Chair"  },
];

// Status values per risk approval:
//   'pending'    — awaiting auditor disposition
//   'approved'   — AI score accepted as-is
//   'adjusted'   — values changed; signoffs are routing through CAE → CFO → AC
//   'signed'     — fully signed off (all three signatures captured)

function deltaLabel(orig, next, key, fmt = (v) => v) {
  if (orig[key] === next[key]) return null;
  return `${fmt(orig[key])} → ${fmt(next[key])}`;
}

function RiskApprovalReview({
  risks,
  approvals,
  onApproveRisk,
  onOpenAdjust,
  onApproveAll,
  onSignoff,
  onSubmit,
  onOverrideGate,
}) {
  if (!risks || risks.length === 0) return null;
  const total = risks.length;
  const decided = risks.filter(r => {
    const a = approvals[r.id];
    return a && (a.status === "approved" || a.status === "signed");
  }).length;
  const adjustedCount = risks.filter(r => approvals[r.id]?.status === "adjusted" || approvals[r.id]?.status === "signed").length;
  const pendingSig = risks.filter(r => approvals[r.id]?.status === "adjusted").length;
  const allResolved = decided === total;

  return (
    <div className="rar" data-screen-label="HITL · Risk approval">
      <div className="rar-head">
        <div className="rar-head-l">
          <div className="rar-pill">
            <span className="dot"/>HITL · GATE 1
          </div>
          <div className="rar-title">Risk Assessment · Per-Risk Review</div>
          <div className="rar-sub">
            Approve each risk as-is, or adjust scoring with rationale. Adjustments are routed for sign-off:
            <span className="rar-sub-chain">CAE → CFO → Audit Committee</span>.
          </div>
        </div>
        <div className="rar-head-r">
          <div className="rar-prog">
            <div className="rar-prog-track"><div className="rar-prog-fill" style={{width: `${(decided/total)*100}%`}}/></div>
            <div className="rar-prog-meta">
              <span className="mono"><b style={{color:"var(--ink)",fontWeight:500}}>{decided}</b> / {total} resolved</span>
              {adjustedCount > 0 && <span className="mono muted">· {adjustedCount} adjusted</span>}
              {pendingSig > 0 && <span className="mono" style={{color:"var(--amber-ink)"}}>· {pendingSig} awaiting sign-off</span>}
            </div>
          </div>
        </div>
      </div>

      <div className="rar-table-wrap">
        <div className="rar-thead">
          <div className="rar-th rar-th-rag">RAG</div>
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
              onApprove={() => onApproveRisk(r.id)}
              onAdjust={() => onOpenAdjust(r.id)}
              onSignoff={(role) => onSignoff(r.id, role)}
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
          <Icon name="check" size={11}/> Confirm Risk Assessment
        </button>
      </div>
    </div>
  );
}

function RiskRow({ risk, approval, onApprove, onAdjust, onSignoff }) {
  const status = approval.status || "pending";
  const adj = approval.adjustments || null;
  const effRag = adj?.rag ?? risk.rag;
  const effScore = adj?.score ?? risk.score;
  const effVel = adj?.velocity ?? risk.velocity;
  const effCe = adj?.ce ?? risk.ce;

  const isAdjusted = status === "adjusted" || status === "signed";

  return (
    <div className={`rar-row rar-row-${status}`}>
      <div className="rar-td rar-td-rag">
        <RAGChip rag={effRag}>{effRag}</RAGChip>
        {isAdjusted && effRag !== risk.rag && (
          <div className="rar-was mono">was <RAGChip rag={risk.rag}>{risk.rag}</RAGChip></div>
        )}
      </div>

      <div className="rar-td rar-td-name">
        <div className="rar-rname">{risk.name}</div>
        <div className="rar-rmeta mono">
          <span>{risk.id}</span>
          <span>·</span>
          <span>{risk.category}</span>
        </div>
      </div>

      <div className="rar-td rar-td-score">
        <span className="mono" style={{color: scoreColorInk(effScore), fontWeight: 500}}>{fmt2(effScore)}</span>
        {isAdjusted && effScore !== risk.score && (
          <span className="rar-was mono">was {fmt2(risk.score)}</span>
        )}
      </div>

      <div className="rar-td rar-td-vel">
        <VelocityPill v={effVel}/>
        {isAdjusted && effVel !== risk.velocity && (
          <span className="rar-was mono">was {risk.velocity > 0 ? "+" : ""}{risk.velocity}</span>
        )}
      </div>

      <div className="rar-td rar-td-ce">
        <span className="mono" style={{fontSize: 10.5}}>{effCe}</span>
        {isAdjusted && effCe !== risk.ce && (
          <span className="rar-was mono">was {risk.ce}</span>
        )}
      </div>

      <div className="rar-td rar-td-action">
        {status === "pending" && (
          <div className="rar-actions">
            <button className="btn btn-sm rar-btn-approve" onClick={onApprove}>
              <Icon name="check" size={10}/> Approve
            </button>
            <button className="btn btn-sm" onClick={onAdjust}>
              <Icon name="edit" size={10}/> Adjust
            </button>
          </div>
        )}
        {status === "approved" && (
          <div className="rar-disposition rar-disposition-approved">
            <Icon name="check" size={11}/>
            <span>Approved as scored</span>
          </div>
        )}
        {status === "adjusted" && (
          <div className="rar-disposition rar-disposition-adjusted">
            <Icon name="alert" size={11}/>
            <span>Awaiting sign-off</span>
          </div>
        )}
        {status === "signed" && (
          <div className="rar-disposition rar-disposition-signed">
            <Icon name="check" size={11}/>
            <span>Adjustment signed</span>
          </div>
        )}
      </div>

      {isAdjusted && (
        <div className="rar-row-detail">
          <div className="rar-detail-rationale">
            <div className="rar-detail-label mono">RATIONALE — {approval.adjustedBy || "Auditor"} · {approval.adjustedAt ? new Date(approval.adjustedAt).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit"}) : "—"}</div>
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

// ----------- Adjust Risk Modal -----------
function AdjustRiskModal({ open, risk, onClose, onSubmit }) {
  const [rag, setRag] = useState(risk?.rag || "A");
  const [score, setScore] = useState(risk?.score ?? 5);
  const [velocity, setVelocity] = useState(risk?.velocity ?? 0);
  const [ce, setCe] = useState(risk?.ce || "ADEQUATE");
  const [rationale, setRationale] = useState("");

  useEffect(() => {
    if (open && risk) {
      setRag(risk.rag);
      setScore(risk.score);
      setVelocity(risk.velocity);
      setCe(risk.ce);
      setRationale("");
    }
  }, [open, risk?.id]);

  if (!open || !risk) return null;

  const changed = rag !== risk.rag || score !== risk.score || velocity !== risk.velocity || ce !== risk.ce;
  const valid = changed && rationale.trim().length >= 30;

  return (
    <div className="modal open" onClick={(e) => { if (e.target.classList.contains("modal")) onClose(); }}>
      <div className="modal-box" style={{width: 640}}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Adjust Risk · {risk.id}</div>
            <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", marginTop: 3}}>
              {risk.name} · {risk.category}
            </div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
        </div>
        <div className="modal-body">
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
              <input type="range" min="0" max="10" step="0.1" value={score}
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
              Rationale <span className="muted">· captured verbatim into audit trail, sent to CAE, CFO, Audit Committee</span>
            </label>
            <textarea className="fi-ta" value={rationale}
              onChange={e => setRationale(e.target.value)}
              placeholder="Describe the basis for this adjustment. Include evidence reviewed, peer benchmarks consulted, and any control deficiencies considered. Minimum 30 characters."
              style={{minHeight: 90}}/>
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
          <div style={{display: "flex", gap: 6}}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!valid}
              onClick={() => onSubmit({ rag, score, velocity, ce, rationale: rationale.trim() })}>
              Submit Adjustment
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { RiskApprovalReview, AdjustRiskModal, SIGNOFFS });
