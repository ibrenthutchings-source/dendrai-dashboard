/* ============================================================
   SOX Risk Assessment — HITL Gates
   Mirrors the Enterprise Risk pipeline's Gate 1 (per-risk approval)
   and Gate 2 (per-objective scope approval):
     Gate S1 — Materiality basis + per-account significant-account scope
     Gate S2 — Per-process SOX coverage level (P1 / P2 / Out)
   Approve as-is is final immediately. Adjustments route to the preparer's
   manager (real org-chart reporting line, auth.users.manager_id) for a
   second review via the Approval Inbox screen — not a fixed fictional
   signoff chain.
   Reuses the .rar/.sar shared review-table styling.
   ============================================================ */

function sxFmtM(v) {
  if (v == null) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

// Status values per item approval (materiality / account / process):
//   'pending'          — awaiting preparer disposition
//   'approved'         — accepted as computed; final, no review needed
//   'submitted'        — adjusted with rationale; routed to manager for review
//   'manager_approved' — manager reviewed and approved the adjustment; final
//   'rejected'         — manager sent it back; preparer must revise

function Disposition({ status }) {
  if (status === "approved") return <div className="rar-disposition rar-disposition-approved"><Icon name="check" size={11}/><span>Approved</span></div>;
  if (status === "submitted") return <div className="rar-disposition rar-disposition-adjusted"><Icon name="alert" size={11}/><span>Awaiting review</span></div>;
  if (status === "manager_approved") return <div className="rar-disposition rar-disposition-signed"><Icon name="check" size={11}/><span>Approved</span></div>;
  if (status === "rejected") return <div className="rar-disposition" style={{color: "var(--red-ink)"}}><Icon name="alert" size={11}/><span>Rejected</span></div>;
  return null;
}

function ReviewStatusNote({ approval }) {
  const status = approval.status;
  if (status === "submitted") {
    return <div style={{fontSize: 11, color: "var(--amber-ink)"}}>Awaiting review from {approval.managerName || "your manager"}.</div>;
  }
  if (status === "manager_approved") {
    return <div style={{fontSize: 11, color: "var(--green-ink)"}}>Approved by {approval.reviewerName || "manager"}.</div>;
  }
  if (status === "rejected") {
    return <div style={{fontSize: 11, color: "var(--red-ink)"}}>Rejected by {approval.reviewerName || "manager"}{approval.reviewComment ? `: "${approval.reviewComment}"` : "."}</div>;
  }
  return null;
}

// ── Gate S1 — Materiality basis ──────────────────────────────────────────────

function MaterialityApprovalCard({ scope, approval, onApprove, onAdjust }) {
  const status = approval.status || "pending";
  const wasAdjusted = ["submitted", "manager_approved", "rejected"].includes(status);
  return (
    <div style={{background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "14px 16px", marginBottom: 14}}>
      <div style={{display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap"}}>
        <div>
          <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 4}}>MATERIALITY BASIS · {scope.fiscal_year}</div>
          <div style={{fontSize: 13, fontWeight: 600, color: "var(--ink)"}}>{scope.materiality_basis}</div>
          <div style={{display: "flex", gap: 18, marginTop: 8, flexWrap: "wrap"}}>
            <div>
              <div className="mono" style={{fontSize: 9, color: "var(--ink-4)"}}>PLANNING</div>
              <div style={{fontSize: 14, fontWeight: 700, fontFamily: "var(--mono)"}}>{sxFmtM(scope.planning_materiality)}</div>
            </div>
            <div>
              <div className="mono" style={{fontSize: 9, color: "var(--ink-4)"}}>PERFORMANCE</div>
              <div style={{fontSize: 14, fontWeight: 700, fontFamily: "var(--mono)", color: "var(--amber-ink)"}}>{sxFmtM(scope.performance_materiality)}</div>
            </div>
            <div>
              <div className="mono" style={{fontSize: 9, color: "var(--ink-4)"}}>TRIVIAL</div>
              <div style={{fontSize: 14, fontWeight: 700, fontFamily: "var(--mono)", color: "var(--ink-3)"}}>{sxFmtM(scope.trivial_threshold)}</div>
            </div>
          </div>
        </div>
        <div>
          {status === "pending" && (
            <div style={{display: "flex", gap: 6}}>
              <button className="btn btn-sm rar-btn-approve" onClick={onApprove}><Icon name="check" size={10}/> Approve basis</button>
              <button className="btn btn-sm" onClick={onAdjust}><Icon name="edit" size={10}/> Adjust</button>
            </div>
          )}
          {status === "rejected" && (
            <button className="btn btn-sm" onClick={onAdjust}><Icon name="edit" size={10}/> Revise</button>
          )}
          <Disposition status={status}/>
        </div>
      </div>
      {wasAdjusted && (
        <div className="rar-row-detail">
          <div className="rar-detail-rationale">
            <div className="rar-detail-label mono">RATIONALE — {approval.adjustedBy || "Auditor"}</div>
            <div className="rar-detail-text">{approval.rationale}</div>
          </div>
          <div style={{display: "flex", flexDirection: "column", gap: 6, borderLeft: "1px solid var(--line)", paddingLeft: 16}}>
            <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em"}}>REVIEW STATUS</div>
            <ReviewStatusNote approval={approval}/>
          </div>
        </div>
      )}
    </div>
  );
}

function AdjustMaterialityModal({ open, scope, ticker, onClose, onSubmit }) {
  const [materialityPct, setMaterialityPct] = React.useState(5.0);
  const [performancePct, setPerformancePct] = React.useState(75.0);
  const [rationale, setRationale] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [err, setErr] = React.useState(null);

  React.useEffect(() => {
    if (open) { setMaterialityPct(5.0); setPerformancePct(75.0); setRationale(""); setErr(null); }
  }, [open]);

  if (!open) return null;
  const valid = rationale.trim().length >= 30;

  async function handleSubmit() {
    setSaving(true); setErr(null);
    try {
      const res = await fetch(`/api/mcp/sox/config/${encodeURIComponent(ticker)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fiscal_year: scope.fiscal_year, materiality_basis: "pretax_income",
          materiality_pct: materialityPct, performance_mat_pct: performancePct,
          scope_note: rationale,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      await onSubmit({ materiality_pct: materialityPct, performance_mat_pct: performancePct, rationale });
    } catch (e) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal open">
      <div className="modal-box" style={{width: 520}}>
        <div className="modal-head">
          <div className="modal-title">Adjust Materiality Basis</div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
        </div>
        <div className="modal-body">
          <div style={{display: "flex", gap: 12, marginBottom: 12}}>
            <div style={{flex: 1}}>
              <label className="ar-label">Planning materiality % (of pre-tax income)</label>
              <input type="number" step="0.1" min="0.1" max="20" value={materialityPct}
                onChange={e => setMaterialityPct(parseFloat(e.target.value) || 0)}
                style={{width: "100%", fontSize: 12, padding: "6px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}/>
            </div>
            <div style={{flex: 1}}>
              <label className="ar-label">Performance materiality % (of planning)</label>
              <input type="number" step="1" min="10" max="100" value={performancePct}
                onChange={e => setPerformancePct(parseFloat(e.target.value) || 0)}
                style={{width: "100%", fontSize: 12, padding: "6px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}/>
            </div>
          </div>
          <div className="rar-sub" style={{marginBottom: 10}}>
            Current computed basis: {scope.materiality_basis} — Planning {sxFmtM(scope.planning_materiality)}, Performance {sxFmtM(scope.performance_materiality)}.
            Saved immediately to the SOX config for {(ticker || "").toUpperCase()} {scope.fiscal_year}; takes effect on the next Rescope.
          </div>
          <label className="ar-label">
            Rationale <span className="muted">· captured verbatim into audit trail, routed to your manager for review</span>
          </label>
          <textarea className="fi-ta" value={rationale} onChange={e => setRationale(e.target.value)}
            placeholder="Describe the basis for this materiality adjustment. Minimum 30 characters."
            style={{minHeight: 90}}/>
          {err && <div className="mono" style={{fontSize: 10.5, color: "var(--red-ink)", marginTop: 6}}>{err}</div>}
        </div>
        <div className="modal-foot">
          <span className="muted" style={{fontSize: 11}}>{rationale.length} chars</span>
          <div style={{display: "flex", gap: 6}}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!valid || saving} onClick={handleSubmit}>{saving ? "Saving…" : "Submit Adjustment"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Gate S1 — Per-account review ─────────────────────────────────────────────

function AccountApprovalRow({ acc, approval, onApprove, onAdjust }) {
  const status = approval.status || "pending";
  const adj = approval.adjustments || null;
  const effInScope = adj ? adj.in_scope : acc.in_scope;
  const effPriority = adj ? adj.priority : acc.priority;
  const wasAdjusted = ["submitted", "manager_approved", "rejected"].includes(status);
  const priColor = effPriority === "P1" ? "var(--red-ink)" : effPriority === "P2" ? "var(--amber-ink)" : "var(--ink-4)";
  const priSoft  = effPriority === "P1" ? "var(--red-soft)" : effPriority === "P2" ? "var(--amber-soft)" : "var(--surface-2, var(--surface))";

  return (
    <div className={`rar-row rar-row-${status}`}>
      <div className="rar-td" style={{flexDirection: "column", alignItems: "flex-start", gap: 2, overflow: "visible"}}>
        <div style={{display: "flex", alignItems: "center", gap: 6}}>
          <span style={{width: 6, height: 6, borderRadius: "50%", background: effInScope ? "var(--green-ink)" : "var(--ink-4)", flexShrink: 0}}/>
          <span style={{fontSize: 12.5, fontWeight: 500, color: "var(--ink)"}}>{acc.account_name}</span>
          {wasAdjusted && effInScope !== acc.in_scope && (
            <span className="rar-was mono">was {acc.in_scope ? "in scope" : "out of scope"}</span>
          )}
        </div>
        <div style={{fontSize: 10.5, color: "var(--ink-3)", lineHeight: 1.4}}>{acc.rationale}</div>
      </div>
      <div className="rar-td" style={{justifyContent: "flex-end"}}>
        <span className="mono" style={{fontSize: 11, color: "var(--ink-3)"}}>{sxFmtM(acc.balance_estimate)}</span>
      </div>
      <div className="rar-td" style={{justifyContent: "center"}}>
        {effInScope
          ? <span className="mono" style={{fontSize: 10, padding: "2px 7px", borderRadius: 999, background: priSoft, color: priColor}}>{effPriority || "P2"}</span>
          : <span className="mono" style={{fontSize: 10, color: "var(--ink-4)"}}>OUT</span>}
      </div>
      <div className="rar-td rar-td-action">
        {status === "pending" && (
          <div className="rar-actions">
            <button className="btn btn-sm rar-btn-approve" onClick={onApprove}><Icon name="check" size={10}/> Approve</button>
            <button className="btn btn-sm" onClick={onAdjust}><Icon name="edit" size={10}/> Adjust</button>
          </div>
        )}
        {status === "rejected" && (
          <div className="rar-actions">
            <Disposition status={status}/>
            <button className="btn btn-sm" onClick={onAdjust}><Icon name="edit" size={10}/> Revise</button>
          </div>
        )}
        {status !== "pending" && status !== "rejected" && <Disposition status={status}/>}
      </div>
      {wasAdjusted && (
        <div className="rar-row-detail">
          <div className="rar-detail-rationale">
            <div className="rar-detail-label mono">RATIONALE — {approval.adjustedBy || "Auditor"}</div>
            <div className="rar-detail-text">{approval.rationale}</div>
          </div>
          <div style={{display: "flex", flexDirection: "column", gap: 6, borderLeft: "1px solid var(--line)", paddingLeft: 16}}>
            <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em"}}>REVIEW STATUS</div>
            <ReviewStatusNote approval={approval}/>
          </div>
        </div>
      )}
    </div>
  );
}

function AdjustAccountModal({ open, acc, ticker, onClose, onSubmit }) {
  const [inScope, setInScope] = React.useState(true);
  const [priority, setPriority] = React.useState("P2");
  const [rationale, setRationale] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [err, setErr] = React.useState(null);

  React.useEffect(() => {
    if (open && acc) { setInScope(acc.in_scope); setPriority(acc.priority || "P2"); setRationale(""); setErr(null); }
  }, [open, acc?.account_id]);

  if (!open || !acc) return null;
  const valid = rationale.trim().length >= 30;

  async function handleSubmit() {
    setSaving(true); setErr(null);
    try {
      const res = await fetch(`/api/mcp/sox/accounts/${encodeURIComponent(ticker)}/${acc.account_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          geography: acc.geography || [], segments: acc.segments || [], notes: acc.notes || null,
          manual_in_scope: inScope, manual_priority: inScope ? priority : null,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      await onSubmit({ in_scope: inScope, priority: inScope ? priority : null, rationale });
    } catch (e) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal open">
      <div className="modal-box" style={{width: 520}}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Adjust Account · {acc.account_name}</div>
            <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", marginTop: 3}}>
              Computed: {acc.in_scope ? `In scope (${acc.priority})` : "Out of scope"}
            </div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
        </div>
        <div className="modal-body">
          <div style={{display: "flex", gap: 8, marginBottom: 12}}>
            <button className={`btn btn-sm ${inScope ? "btn-primary" : ""}`} onClick={() => setInScope(true)}>Force in-scope</button>
            <button className={`btn btn-sm ${!inScope ? "btn-primary" : ""}`} onClick={() => setInScope(false)}>Force out-of-scope</button>
          </div>
          {inScope && (
            <div style={{display: "flex", gap: 8, marginBottom: 12}}>
              {["P1", "P2"].map(p => (
                <button key={p} className={`btn btn-sm ${priority === p ? "btn-primary" : ""}`} onClick={() => setPriority(p)}>{p}</button>
              ))}
            </div>
          )}
          <label className="ar-label">
            Rationale <span className="muted">· captured verbatim into audit trail, routed to your manager for review</span>
          </label>
          <textarea className="fi-ta" value={rationale} onChange={e => setRationale(e.target.value)}
            placeholder="Describe the basis for this override. Minimum 30 characters."
            style={{minHeight: 90}}/>
          {err && <div className="mono" style={{fontSize: 10.5, color: "var(--red-ink)", marginTop: 6}}>{err}</div>}
        </div>
        <div className="modal-foot">
          <span className="muted" style={{fontSize: 11}}>{rationale.length} chars</span>
          <div style={{display: "flex", gap: 6}}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!valid || saving} onClick={handleSubmit}>{saving ? "Saving…" : "Submit Adjustment"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Gate S1 container ────────────────────────────────────────────────────────

function SoxGate1Review({
  scope, accounts, materialityApproval, accountApprovals,
  onApproveMateriality, onAdjustMateriality,
  onApproveAccount, onAdjustAccount, onApproveAllAccounts,
  onSubmit, onOverrideGate,
}) {
  const total = accounts.length;
  const decided = accounts.filter(a => {
    const s = accountApprovals[a.account_id]?.status;
    return s === "approved" || s === "submitted" || s === "manager_approved";
  }).length;
  const submittedCount = accounts.filter(a => ["submitted", "manager_approved", "rejected"].includes(accountApprovals[a.account_id]?.status)).length;
  const pendingReview = accounts.filter(a => accountApprovals[a.account_id]?.status === "submitted").length;
  const matDone = materialityApproval.status === "approved" || materialityApproval.status === "manager_approved";
  const matPendingReview = materialityApproval.status === "submitted";
  const allResolved = matDone && decided === total;

  return (
    <div className="rar sxa" data-screen-label="HITL · SOX Gate S1">
      <div className="rar-head">
        <div className="rar-head-l">
          <div className="rar-pill"><span className="dot"/>HUMAN REVIEW · SOX GATE S1</div>
          <div className="rar-title">SOX Scope · Materiality &amp; Significant Accounts</div>
          <div className="rar-sub">
            Approve the materiality basis and each significant account as computed, or adjust with rationale.
            Adjustments route to your manager for review.
          </div>
        </div>
        <div className="rar-head-r">
          <div className="rar-prog">
            <div className="rar-prog-track"><div className="rar-prog-fill" style={{width: `${((decided + (matDone ? 1 : 0)) / (total + 1)) * 100}%`}}/></div>
            <div className="rar-prog-meta">
              <span className="mono"><b style={{color: "var(--ink)", fontWeight: 500}}>{decided}</b> / {total} accounts resolved</span>
              {(submittedCount > 0 || matPendingReview) && <span className="mono muted">· {submittedCount + (matPendingReview ? 1 : 0)} adjusted</span>}
              {(pendingReview > 0 || matPendingReview) && <span className="mono" style={{color: "var(--amber-ink)"}}>· {pendingReview + (matPendingReview ? 1 : 0)} awaiting manager</span>}
            </div>
          </div>
        </div>
      </div>

      <MaterialityApprovalCard scope={scope} approval={materialityApproval}
        onApprove={onApproveMateriality} onAdjust={onAdjustMateriality}/>

      <div className="rar-table-wrap">
        <div className="rar-thead">
          <div className="rar-th">Account</div>
          <div className="rar-th" style={{textAlign: "right"}}>Balance</div>
          <div className="rar-th" style={{textAlign: "center"}}>Priority</div>
          <div className="rar-th">Disposition</div>
        </div>
        <div className="rar-tbody">
          {accounts.map(acc => (
            <AccountApprovalRow key={acc.account_id} acc={acc}
              approval={accountApprovals[acc.account_id] || { status: "pending" }}
              onApprove={() => onApproveAccount(acc.account_id)}
              onAdjust={() => onAdjustAccount(acc.account_id)}/>
          ))}
        </div>
      </div>

      <div className="rar-foot">
        <button className="btn btn-sm" onClick={onOverrideGate}><Icon name="alert" size={11}/> Override entire gate</button>
        <div className="rar-foot-spacer"/>
        <button className="btn btn-sm" onClick={onApproveAllAccounts} disabled={decided === total}>
          Approve all remaining accounts ({total - decided})
        </button>
        <button className="btn btn-sm btn-primary" disabled={!allResolved} onClick={onSubmit}>
          <Icon name="check" size={11}/> Confirm Gate S1
        </button>
      </div>
    </div>
  );
}

// ── Gate S2 — Per-process coverage review ────────────────────────────────────

function COV_INK(level) {
  return level === "P1" ? "var(--red-ink)" : level === "P2" ? "var(--amber-ink)" : "var(--ink-4)";
}
function COV_SOFT(level) {
  return level === "P1" ? "var(--red-soft)" : level === "P2" ? "var(--amber-soft)" : "var(--surface-2, var(--surface))";
}

function ProcessApprovalRow({ proc, approval, onApprove, onAdjust }) {
  const status = approval.status || "pending";
  const adj = approval.adjustments || null;
  const effLevel = adj ? adj.coverage_level : proc.coverage_level;
  const wasAdjusted = ["submitted", "manager_approved", "rejected"].includes(status);

  return (
    <div className={`rar-row rar-row-${status}`}>
      <div className="rar-td" style={{justifyContent: "center"}}>
        <span className="mono" style={{fontSize: 10, padding: "2px 7px", borderRadius: 999, background: COV_SOFT(effLevel), color: COV_INK(effLevel)}}>
          {effLevel}
        </span>
      </div>
      <div className="rar-td" style={{flexDirection: "column", alignItems: "flex-start", gap: 2, overflow: "visible"}}>
        <div style={{display: "flex", alignItems: "center", gap: 6}}>
          <span style={{fontSize: 12.5, fontWeight: 500, color: "var(--ink)"}}>{proc.process_name}</span>
          {proc.always_in && <span className="mono" style={{fontSize: 9, color: "var(--acc-ink, var(--ink-3))"}}>REQUIRED</span>}
          {wasAdjusted && effLevel !== proc.coverage_level && (
            <span className="rar-was mono">was {proc.coverage_level}</span>
          )}
        </div>
        <div style={{fontSize: 10.5, color: "var(--ink-3)", lineHeight: 1.4}}>{proc.rationale}</div>
      </div>
      <div className="rar-td rar-td-action">
        {status === "pending" && (
          <div className="rar-actions">
            <button className="btn btn-sm rar-btn-approve" onClick={onApprove}><Icon name="check" size={10}/> Approve</button>
            <button className="btn btn-sm" onClick={onAdjust}><Icon name="edit" size={10}/> Adjust</button>
          </div>
        )}
        {status === "rejected" && (
          <div className="rar-actions">
            <Disposition status={status}/>
            <button className="btn btn-sm" onClick={onAdjust}><Icon name="edit" size={10}/> Revise</button>
          </div>
        )}
        {status !== "pending" && status !== "rejected" && <Disposition status={status}/>}
      </div>
      {wasAdjusted && (
        <div className="rar-row-detail">
          <div className="rar-detail-rationale">
            <div className="rar-detail-label mono">RATIONALE — {approval.adjustedBy || "Auditor"}</div>
            <div className="rar-detail-text">{approval.rationale}</div>
          </div>
          <div style={{display: "flex", flexDirection: "column", gap: 6, borderLeft: "1px solid var(--line)", paddingLeft: 16}}>
            <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em"}}>REVIEW STATUS</div>
            <ReviewStatusNote approval={approval}/>
          </div>
        </div>
      )}
    </div>
  );
}

function AdjustProcessModal({ open, proc, ticker, onClose, onSubmit }) {
  const [level, setLevel] = React.useState("P2");
  const [rationale, setRationale] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [err, setErr] = React.useState(null);

  React.useEffect(() => {
    if (open && proc) { setLevel(proc.coverage_level || "P2"); setRationale(""); setErr(null); }
  }, [open, proc?.process_id]);

  if (!open || !proc) return null;
  const valid = rationale.trim().length >= 30;

  async function handleSubmit() {
    setSaving(true); setErr(null);
    try {
      const res = await fetch(`/api/mcp/sox/processes/${encodeURIComponent(ticker)}/${proc.process_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          geography: proc.geography || [], segments: proc.segments || [], notes: proc.notes || null,
          manual_coverage_level: level,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      await onSubmit({ coverage_level: level, rationale });
    } catch (e) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal open">
      <div className="modal-box" style={{width: 520}}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Adjust Process · {proc.process_name}</div>
            <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", marginTop: 3}}>Computed: {proc.coverage_level}</div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
        </div>
        <div className="modal-body">
          <div style={{display: "flex", gap: 8, marginBottom: 12}}>
            {["P1", "P2", "Out"].map(l => (
              <button key={l} className={`btn btn-sm ${level === l ? "btn-primary" : ""}`} onClick={() => setLevel(l)}>{l}</button>
            ))}
          </div>
          <label className="ar-label">
            Rationale <span className="muted">· captured verbatim into audit trail, routed to your manager for review</span>
          </label>
          <textarea className="fi-ta" value={rationale} onChange={e => setRationale(e.target.value)}
            placeholder="Describe the basis for this override. Minimum 30 characters."
            style={{minHeight: 90}}/>
          {err && <div className="mono" style={{fontSize: 10.5, color: "var(--red-ink)", marginTop: 6}}>{err}</div>}
        </div>
        <div className="modal-foot">
          <span className="muted" style={{fontSize: 11}}>{rationale.length} chars</span>
          <div style={{display: "flex", gap: 6}}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!valid || saving} onClick={handleSubmit}>{saving ? "Saving…" : "Submit Adjustment"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function SoxGate2Review({
  processes, processApprovals,
  onApproveProcess, onAdjustProcess, onApproveAllProcesses,
  onSubmit, onOverrideGate,
}) {
  const total = processes.length;
  const decided = processes.filter(p => {
    const s = processApprovals[p.process_id]?.status;
    return s === "approved" || s === "submitted" || s === "manager_approved";
  }).length;
  const submittedCount = processes.filter(p => ["submitted", "manager_approved", "rejected"].includes(processApprovals[p.process_id]?.status)).length;
  const pendingReview = processes.filter(p => processApprovals[p.process_id]?.status === "submitted").length;
  const allResolved = decided === total;

  return (
    <div className="rar sxp" data-screen-label="HITL · SOX Gate S2">
      <div className="rar-head">
        <div className="rar-head-l">
          <div className="rar-pill sar-pill"><span className="dot"/>HUMAN REVIEW · SOX GATE S2</div>
          <div className="rar-title">SOX Scope · Process Coverage</div>
          <div className="rar-sub">
            Approve each process's coverage level as computed, or adjust with rationale.
            Adjustments route to your manager for review.
          </div>
        </div>
        <div className="rar-head-r">
          <div className="rar-prog">
            <div className="rar-prog-track"><div className="rar-prog-fill" style={{width: `${(decided / total) * 100}%`}}/></div>
            <div className="rar-prog-meta">
              <span className="mono"><b style={{color: "var(--ink)", fontWeight: 500}}>{decided}</b> / {total} resolved</span>
              {submittedCount > 0 && <span className="mono muted">· {submittedCount} adjusted</span>}
              {pendingReview > 0 && <span className="mono" style={{color: "var(--amber-ink)"}}>· {pendingReview} awaiting manager</span>}
            </div>
          </div>
        </div>
      </div>

      <div className="rar-table-wrap">
        <div className="rar-thead">
          <div className="rar-th" style={{textAlign: "center"}}>Coverage</div>
          <div className="rar-th">Process</div>
          <div className="rar-th">Disposition</div>
        </div>
        <div className="rar-tbody">
          {processes.map(proc => (
            <ProcessApprovalRow key={proc.process_id} proc={proc}
              approval={processApprovals[proc.process_id] || { status: "pending" }}
              onApprove={() => onApproveProcess(proc.process_id)}
              onAdjust={() => onAdjustProcess(proc.process_id)}/>
          ))}
        </div>
      </div>

      <div className="rar-foot">
        <button className="btn btn-sm" onClick={onOverrideGate}><Icon name="alert" size={11}/> Override entire gate</button>
        <div className="rar-foot-spacer"/>
        <button className="btn btn-sm" onClick={onApproveAllProcesses} disabled={decided === total}>
          Approve all remaining ({total - decided})
        </button>
        <button className="btn btn-sm btn-primary" disabled={!allResolved} onClick={onSubmit}>
          <Icon name="check" size={11}/> Confirm Gate S2
        </button>
      </div>
    </div>
  );
}

// ── Gate status banner (shown on tabs once a gate has closed) ────────────────

function SoxGateBanner({ label, state }) {
  if (state !== "approved" && state !== "overridden") return null;
  const isOverridden = state === "overridden";
  return (
    <div style={{display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", marginBottom: 10, borderRadius: 6,
      background: isOverridden ? "var(--amber-soft)" : "var(--green-soft)",
      color: isOverridden ? "var(--amber-ink)" : "var(--green-ink)", fontSize: 11}}>
      <Icon name={isOverridden ? "alert" : "check"} size={12}/>
      <span>{label} {isOverridden ? "overridden" : "approved"}</span>
    </div>
  );
}

Object.assign(window, {
  sxFmtM,
  SoxGate1Review, SoxGate2Review, SoxGateBanner,
  AdjustMaterialityModal, AdjustAccountModal, AdjustProcessModal,
});
