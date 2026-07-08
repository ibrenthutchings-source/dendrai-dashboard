/* ============================================================
   Approval Inbox — real manager review queue.
   Lists HITL gate adjustments (Enterprise Risk Gate 1/2, SOX Gate S1/S2)
   currently routed to the logged-in user for review, per the real
   preparer -> manager workflow (see approvals_endpoints.py).
   ============================================================ */

const GATE_TYPE_LABEL = {
  risk: "Enterprise Risk · Gate 1",
  objective: "Enterprise Risk · Gate 2",
  sox_materiality: "SOX · Gate S1 (Materiality)",
  sox_account: "SOX · Gate S1 (Account)",
  sox_process: "SOX · Gate S2 (Process)",
};

const ADJUSTMENT_FIELD_LABEL = {
  rag: "RAG", score: "Score", velocity: "Velocity", ce: "Control Effectiveness",
  name: "Name", category: "Category",
  objective: "Objective", priority: "Priority", sprint: "Fiscal Quarter", hours: "Hours",
  linked_risks: "Linked Risks", controls: "Controls", residualRiskReduction: "Risk Reduction (pts)",
  in_scope: "In Scope", coverage_level: "Coverage Level",
  materiality_pct: "Materiality %", performance_mat_pct: "Performance Materiality %",
};

function formatAdjustmentValue(key, v) {
  if (v == null) return "—";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return String(v);
}

function AdjustmentSummary({ adjustments }) {
  if (!adjustments) return null;
  const entries = Object.entries(adjustments).filter(([k, v]) => !k.startsWith("_") && v !== undefined);
  if (!entries.length) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
      {entries.map(([k, v]) => (
        <span key={k} className="mono" style={{ fontSize: 10, padding: "3px 8px", borderRadius: 4, background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", color: "var(--ink-2)" }}>
          {ADJUSTMENT_FIELD_LABEL[k] || k}: <b style={{ color: "var(--ink)" }}>{formatAdjustmentValue(k, v)}</b>
        </span>
      ))}
    </div>
  );
}

function InboxItem({ item, onDecide }) {
  const [expanded, setExpanded] = React.useState(false);
  const [comment, setComment] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);
  const [aiState, setAiState] = React.useState({ loading: false, error: null, reco: null });

  async function decide(decision) {
    setBusy(true); setErr(null);
    try {
      await onDecide(item.id, decision, comment.trim() || null);
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  }

  // #2b — draft a review recommendation the manager accepts or overrides;
  // never auto-decides, only pre-fills the comment for them to edit.
  const aiAvailable = typeof window !== "undefined" && window.MCP?.aiApprovalRecommend;
  async function runAiSuggest() {
    if (!aiAvailable) return;
    setAiState({ loading: true, error: null, reco: null });
    try {
      const reco = await window.MCP.aiApprovalRecommend(item.id);
      setComment(`[AI suggestion, ${reco.confidence} confidence] ${reco.reasoning || ""}`.trim());
      setAiState({ loading: false, error: null, reco });
    } catch (e) {
      setAiState({ loading: false, error: e.message || "AI unavailable", reco: null });
    }
  }

  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 240 }}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 4 }}>
            {GATE_TYPE_LABEL[item.gate_type] || item.gate_type} · {item.ticker} · Run #{item.run_id}
          </div>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>{item.item_label || item.item_ref}</div>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}>
            Submitted by <b style={{ color: "var(--ink-2)" }}>{item.prepared_by_name}</b>
            {item.prepared_at && <> · {new Date(item.prepared_at).toLocaleString()}</>}
          </div>
        </div>
        <button className="btn btn-sm" onClick={() => setExpanded(e => !e)}>
          {expanded ? "Collapse" : "Review"}
        </button>
      </div>

      {expanded && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
          {item.rationale && (
            <div style={{ marginBottom: 8 }}>
              <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 4 }}>PREPARER RATIONALE</div>
              <div style={{ fontSize: 12, color: "var(--ink-2)", lineHeight: 1.55 }}>{item.rationale}</div>
            </div>
          )}
          <AdjustmentSummary adjustments={item.adjustments} />
          {item.ai_suggested && (
            <div className="mono" style={{ fontSize: 10, marginTop: 8, color: item.ai_accepted ? "var(--green-ink)" : "var(--amber-ink)" }}>
              <Icon name="spark" size={10} />{" "}
              {item.ai_accepted
                ? "Preparer kept the AI's suggested values as-is"
                : "Preparer adjusted the AI's suggestion before submitting"}
            </div>
          )}

          <div style={{ marginTop: 14 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
              <label className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em" }}>
                REVIEW COMMENT <span style={{ textTransform: "none", letterSpacing: 0 }}>(optional for approve, recommended for reject)</span>
              </label>
              {aiAvailable && (
                <button className="btn btn-sm" onClick={runAiSuggest} disabled={aiState.loading}
                  title="Draft a review recommendation with Claude — accept or override as needed">
                  <Icon name="spark" size={11} /> {aiState.loading ? "Analyzing…" : "Suggest with AI"}
                </button>
              )}
            </div>
            {aiState.error && (
              <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 6 }}>
                AI suggestion unavailable: {aiState.error}
              </div>
            )}
            {aiState.reco && (
              <div className="mono" style={{ fontSize: 10.5, color: "var(--acc-ink)", marginBottom: 6 }}>
                AI recommends: <b>{aiState.reco.recommendation}</b> ({aiState.reco.confidence} confidence) — comment pre-filled below, edit freely.
              </div>
            )}
            <textarea className="fi-ta" value={comment} onChange={e => setComment(e.target.value)}
              placeholder="Add context for the audit trail…" style={{ minHeight: 60, width: "100%", boxSizing: "border-box" }} />
          </div>

          {err && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 8 }}>{err}</div>}

          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button className="btn btn-sm approve" disabled={busy} onClick={() => decide("approved")}>
              <Icon name="check" size={11} /> Approve
            </button>
            <button className="btn btn-sm" disabled={busy} onClick={() => decide("rejected")}
              style={{ color: "var(--red-ink)" }}>
              <Icon name="x" size={11} /> Reject
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ApprovalInboxScreen() {
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);

  const reload = React.useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const res = await fetch("/approvals/inbox", { credentials: "include" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setItems(data.items || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { reload(); }, [reload]);

  async function handleDecide(taskId, decision, comment) {
    const res = await fetch("/approvals/review", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId, decision, comment }),
    });
    if (!res.ok) throw new Error(await res.text());
    setItems(prev => prev.filter(i => i.id !== taskId));
  }

  return (
    <div className="scope-screen" data-screen-label="Approval Inbox">
      <div className="panel-head">
        <div>
          <div className="kicker">Governance · My Queue</div>
          <div className="panel-title mt-8">Approval Inbox</div>
          <div className="panel-sub">
            HITL gate adjustments from Enterprise Risk and SOX Risk Assessment awaiting your review as manager.
            Plain approvals-as-scored don't appear here — only overrides that need a second set of eyes.
          </div>
        </div>
        <button className="btn btn-sm" onClick={reload} disabled={loading}>
          <Icon name="reset" size={11} /> Refresh
        </button>
      </div>

      {error && (
        <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", background: "var(--red-soft)", padding: "6px 10px", borderRadius: 4, marginBottom: 12 }}>{error}</div>
      )}

      {loading ? (
        <div style={{ padding: "24px 0", textAlign: "center", color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>
      ) : items.length === 0 ? (
        <Empty>Nothing awaiting your review right now.</Empty>
      ) : (
        <div>
          {items.map(item => <InboxItem key={item.id} item={item} onDecide={handleDecide} />)}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { ApprovalInboxScreen });
