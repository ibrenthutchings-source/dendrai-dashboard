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
  // DevOps Monitoring: a preparer submits POST /approvals/prepare with this
  // gate_type to request a documented, time-boxed exception for a flagged
  // branch-protection weakness — same generic approval_tasks workflow, no
  // schema change (gate_type is free-text, per approvals_endpoints.py).
  devops_scm_exception: "DevOps Monitoring · SCM Exception",
  // Continuous Monitoring's Walkthrough tab: an AI-drafted narrative,
  // grounded in real Case Flow/Variants/CaC/PaC evidence, submitted for
  // the same preparer -> manager sign-off as every other gate here — see
  // continuous-monitoring-viz.jsx's WalkthroughNarrativeView. run_id is
  // always NULL (not tied to a risk_loop_run — see approval_tasks.run_id's
  // DROP NOT NULL migration in db.py), so the ticker/run line below is
  // skipped for it, same as devops_scm_exception.
  walkthrough_narrative: "Continuous Monitoring · Walkthrough Narrative",
  // Closed-loop remediation (remediation_endpoints.py): the only gate_types
  // whose approval fires a real external write (github_write_tool.py) —
  // "Approve" here doesn't just record a decision, it opens the GitHub
  // issue/PR. remediation_github has no file target (just a tracked
  // ticket); remediation_github_pr commits real file changes the reviewer
  // named explicitly — see the diff preview below.
  remediation_github: "Closed-Loop Remediation · GitHub Issue",
  remediation_github_pr: "Closed-Loop Remediation · GitHub PR",
};

const _REMEDIATION_STATUS_LABEL = {
  submitted: "Awaiting your approval", manager_approved: "Approved — executed", approved: "Approved — executed",
  rejected: "Rejected — no action taken", pending: "Pending",
};

function RemediationActivityRow({ task, onRetry, retrying }) {
  const result = task.execution_result;
  const failed = result && result.error;
  const succeeded = result && result.url;
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "12px 14px", marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 220 }}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 4 }}>
            {_REMEDIATION_STATUS_LABEL[task.status] || task.status}
          </div>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ink)" }}>{task.item_label || task.item_ref}</div>
          <div style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 3 }}>
            Proposed by <b style={{ color: "var(--ink-2)" }}>{task.prepared_by_name}</b>
            {task.reviewed_by_name && <> · reviewed by <b style={{ color: "var(--ink-2)" }}>{task.reviewed_by_name}</b></>}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          {succeeded && (
            <a className="mono" href={result.url} target="_blank" rel="noreferrer"
              style={{ fontSize: 11, color: "var(--green-ink)", fontWeight: 700 }}>
              {task.gate_type === "remediation_github_pr" ? "PR" : "Issue"} #{result.number} opened →
            </a>
          )}
          {failed && (
            <>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 6, maxWidth: 260 }}>
                Execution failed: {result.error}
              </div>
              <button className="btn btn-sm" disabled={retrying} onClick={() => onRetry(task.id)}>
                {retrying ? "Retrying…" : "Retry"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function RemediationActivitySection() {
  const [tasks, setTasks] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [retryingId, setRetryingId] = React.useState(null);

  const load = React.useCallback(() => {
    setLoading(true);
    return fetch("/approvals/remediations", { credentials: "include" })
      .then(res => res.ok ? res.json() : { tasks: [] })
      .then(d => setTasks(d.tasks || []))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => { load(); }, [load]);

  async function handleRetry(taskId) {
    setRetryingId(taskId);
    try {
      await fetch(`/approvals/remediations/${taskId}/retry`, { method: "POST", credentials: "include" });
      await load();
    } finally {
      setRetryingId(null);
    }
  }

  if (loading && !tasks.length) return null;
  if (!tasks.length) return null;

  return (
    <div style={{ marginTop: 24 }}>
      <div className="mono" style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: "0.06em", color: "var(--ink-4)", marginBottom: 8 }}>
        CLOSED-LOOP REMEDIATION · {tasks.length} RECENT
      </div>
      {tasks.map(t => <RemediationActivityRow key={t.id} task={t} onRetry={handleRetry} retrying={retryingId === t.id} />)}
    </div>
  );
}

// UBO Governance Brain's ConflictFlag enum (UBO/models/risk_intelligence.py) —
// WHY the Council escalated, distinct from risk_flags (WHAT the Bronze-layer
// detection rules noticed in the raw event) and from policy_violations
// (WHICH specific PaC/Silver rule fired). All three answer a different half
// of "what caused this" and none was shown here before.
const CONFLICT_FLAG_LABEL = {
  AGENT_DIVERGENCE:        "Agents disagreed past threshold",
  LOW_CONFIDENCE:          "Low ensemble confidence",
  MISSING_EVALUATIONS:     "One or more agents returned no evaluation",
  ANOMALOUS_RISK_DELTA:    "Anomalous risk-score swing",
  POLICY_VIOLATION:        "Policy-as-Code deny rule fired",
  LLM_ESCALATION_OVERRIDE: "LLM 4th-opinion override",
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

// remediation_github_pr proposals carry a unified diff under the
// underscore-prefixed "_diff" adjustment key (see remediation_endpoints.py's
// propose_pr_remediation) — hidden from AdjustmentSummary's generic chip
// list on purpose, shown here instead as an actual reviewable diff, since a
// manager approving a real file-change PR needs to see the change itself,
// not just a title/body like the issue flow shows.
function PrDiffPreview({ diff, filePath }) {
  if (!diff) return null;
  return (
    <div style={{ marginTop: 10 }}>
      <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 4 }}>
        PROPOSED DIFF{filePath ? ` — ${filePath}` : ""}
      </div>
      <pre className="mono" style={{
        fontSize: 10.5, lineHeight: 1.5, margin: 0, padding: "10px 12px", borderRadius: 6,
        background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)",
        maxHeight: 320, overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word",
      }}>
        {diff}
      </pre>
    </div>
  );
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

function InboxItem({ item, currentUserId, onDecide }) {
  const isOverride = currentUserId != null && item.manager_id != null && item.manager_id !== currentUserId;
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
            {GATE_TYPE_LABEL[item.gate_type] || item.gate_type}
            {item.run_id != null && <> · {item.ticker} · Run #{item.run_id}</>}
          </div>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>{item.item_label || item.item_ref}</div>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}>
            Submitted by <b style={{ color: "var(--ink-2)" }}>{item.prepared_by_name}</b>
            {item.prepared_at && <> · {new Date(item.prepared_at).toLocaleString()}</>}
          </div>
          {isOverride && (
            <div className="mono" style={{ fontSize: 9.5, color: "var(--amber-ink)", marginTop: 4 }}
              title="You're not the assigned reviewer for this item — deciding it will be recorded as an admin override.">
              <Icon name="alert" size={9} /> Assigned to {item.manager_name || "another manager"} — admin override
            </div>
          )}
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
          {item.gate_type === "remediation_github_pr" && (
            <PrDiffPreview diff={item.adjustments?._diff} filePath={item.adjustments?.file_path} />
          )}
          {item.ai_suggested && (
            <div className="mono" style={{ fontSize: 10, marginTop: 8, color: item.ai_accepted ? "var(--green-ink)" : "var(--amber-ink)", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span><Icon name="spark" size={10} />{" "}
                {item.ai_accepted
                  ? "Preparer kept the AI's suggested values as-is"
                  : "Preparer adjusted the AI's suggestion before submitting"}
              </span>
              <ProvenanceChip verdict={item.ai_accepted ? "accepted" : "adjusted"} />
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
              <div className="mono" style={{ fontSize: 10.5, color: "var(--acc-ink)", marginBottom: 6, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span>AI recommends: <b>{aiState.reco.recommendation}</b> — comment pre-filled below, edit freely.</span>
                <ProvenanceChip verdict={aiState.reco.recommendation} confidence={aiState.reco.confidence} />
              </div>
            )}
            <textarea className="fi-ta" value={comment} onChange={e => setComment(e.target.value)}
              placeholder="Add context for the audit trail…" style={{ minHeight: 60, width: "100%", boxSizing: "border-box" }} />
          </div>

          {err && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 8 }}>{err}</div>}

          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button className="btn btn-sm btn-approve" disabled={busy} onClick={() => decide("approved")}>
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

// UBO™ Governance Brain telemetry rows flagged requires_human_review — a
// second, unrelated review queue that lives in a different table
// (adjudicated_tool_calls, not approval_tasks) and isn't routed to a specific
// manager the way Gate items are: it's broadcast to every user's inbox, and
// whoever reviews it first resolves it for everyone (the review endpoint
// flips requires_human_review on the one shared row, so the next poll drops
// it from every other user's list too — no per-user dismissal bookkeeping
// needed). Mirrors the exact verdict options UBOAdjRow's inline review panel
// uses in Control Tower, since this is the same action surfaced in a second
// place, not a different workflow.
const _UBO_VERDICT_CHOICES = [
  { v: "APPROVE",  l: "✓ Approve AI verdict" },
  { v: "ESCALATE", l: "↑ Escalate" },
  { v: "CLEAR",    l: "○ Override → CLEAR" },
  { v: "MONITOR",  l: "~ Override → MONITOR" },
];

function TelemetryReviewItem({ item, onDecide }) {
  const [expanded, setExpanded] = React.useState(false);
  const [verdict,  setVerdict]  = React.useState("APPROVE");
  const [notes,    setNotes]    = React.useState("");
  const [busy,     setBusy]     = React.useState(false);
  const [err,      setErr]      = React.useState(null);

  async function submit() {
    setBusy(true); setErr(null);
    try {
      await onDecide(item.id, verdict, notes.trim() || null);
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  }

  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 240 }}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 4 }}>
            UBO™ GOVERNANCE BRAIN · TELEMETRY ADJUDICATION · {item.risk_tier || "—"}
          </div>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>{item.target_tool || "unknown tool"}</div>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}>
            {item.server_name && <>{item.server_name} · </>}
            AI verdict <b style={{ color: "var(--ink-2)" }}>{item.final_verdict || "—"}</b>
            {item.risk_score != null && <> · risk score <b style={{ color: "var(--red-ink)" }}>{item.risk_score.toFixed(3)}</b></>}
            {item.adjudicated_at && <> · {new Date(item.adjudicated_at).toLocaleString()}</>}
          </div>
          {(item.policy_violations || []).length > 0 && (
            <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 4, fontWeight: 600 }}>
              ⚠ {item.policy_violations.length} Policy-as-Code violation{item.policy_violations.length !== 1 ? "s" : ""}
              {item.domain ? ` · ${item.domain}` : ""}
            </div>
          )}
        </div>
        <button className="btn btn-sm" onClick={() => setExpanded(e => !e)}>
          {expanded ? "Collapse" : "Review"}
        </button>
      </div>

      {expanded && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
          {(item.conflict_flags || []).length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 4 }}>WHAT CAUSED THE ESCALATION</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {item.conflict_flags.map(f => (
                  <span key={f} className="mono" style={{ fontSize: 10, padding: "3px 8px", borderRadius: 4, background: "var(--red-soft)", border: "1px solid var(--red-ink)", color: "var(--red-ink)" }}>
                    {CONFLICT_FLAG_LABEL[f] || f}
                  </span>
                ))}
              </div>
            </div>
          )}
          {(item.policy_violations || []).length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 4 }}>
                POLICY-AS-CODE VIOLATED{item.domain ? ` · ${item.domain}` : ""}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {item.policy_violations.map((v, i) => (
                  <span key={i} className="mono" style={{ fontSize: 10.5, padding: "3px 8px", borderRadius: 4, background: "var(--surface-2, var(--surface))", border: "1px solid var(--red-ink)", color: "var(--ink)" }}>
                    {v}
                  </span>
                ))}
              </div>
            </div>
          )}
          {item.adjudicator_reasoning && (
            <div style={{ marginBottom: 10 }}>
              <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 4 }}>ADJUDICATOR REASONING</div>
              <div style={{ fontSize: 12, color: "var(--ink-2)", lineHeight: 1.55, fontFamily: "'Geist Mono',monospace" }}>{item.adjudicator_reasoning}</div>
            </div>
          )}
          {(item.council_votes || []).length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 4 }}>AGENT-BY-AGENT VOTES</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {item.council_votes.map((v, i) => (
                  <div key={i} style={{ fontSize: 11, padding: "6px 8px", borderRadius: 4, background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)" }}>
                    <div className="mono" style={{ fontSize: 10, color: "var(--ink-2)", fontWeight: 600 }}>
                      {v.agent_name} — <span style={{ color: v.verdict === "ESCALATE" ? "var(--red-ink)" : "var(--ink-2)" }}>{v.verdict}</span>
                      {v.confidence != null && <span style={{ color: "var(--ink-4)", fontWeight: 400 }}> ({(v.confidence * 100).toFixed(0)}% confidence)</span>}
                    </div>
                    {v.reasoning && <div style={{ color: "var(--ink-3)", marginTop: 2 }}>{v.reasoning}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
          {(item.risk_flags || []).length > 0 && (
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginBottom: 10 }}>
              Risk flags: {item.risk_flags.join(", ")}
            </div>
          )}

          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
            {_UBO_VERDICT_CHOICES.map(({ v, l }) => (
              <label key={v} style={{
                display: "flex", alignItems: "center", gap: 4, fontSize: 11, cursor: "pointer",
                padding: "3px 10px", borderRadius: 4, border: `1.5px solid ${verdict === v ? "var(--acc)" : "var(--line)"}`,
                background: verdict === v ? "var(--acc-soft,#eff6ff)" : "var(--surface-1)",
                color: verdict === v ? "var(--acc)" : "var(--ink-2)", fontWeight: verdict === v ? 700 : 400,
              }}>
                <input type="radio" name={`verdict-${item.id}`} value={v} checked={verdict === v}
                  onChange={() => setVerdict(v)} style={{ display: "none" }} />
                {l}
              </label>
            ))}
          </div>
          <textarea className="fi-ta" value={notes} onChange={e => setNotes(e.target.value)}
            placeholder="Review notes (optional)…" style={{ minHeight: 50, width: "100%", boxSizing: "border-box" }} />

          {err && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 8 }}>{err}</div>}

          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button className="btn btn-sm btn-primary" disabled={busy} onClick={submit}>
              {busy ? <><span className="spin" /> Submitting…</> : "Submit Review"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const AI_NARRATIVE_KIND_LABEL = {
  persona_brief: "Persona Brief",
  audit_report: "Audit Report",
};

// AI narrative review — persona_brief/audit_report are the two AI endpoints
// that reach a user/board with no gate before delivery (MODEL_CARD.md known
// limitation #3); every generation now lands here for review instead of the
// old 20%-sample after-the-fact spot-check. See ai_endpoints.py's
// GET/POST /ai/review-queue.
function AiNarrativeReviewItem({ item, onDecide }) {
  const [expanded, setExpanded] = React.useState(false);
  const [note, setNote] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);

  async function submit() {
    setBusy(true); setErr(null);
    try {
      await onDecide(item.id, note.trim() || null);
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  }

  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 240 }}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 4 }}>
            AI NARRATIVE · {AI_NARRATIVE_KIND_LABEL[item.kind] || item.kind}{item.ticker ? ` · ${item.ticker}` : ""}
          </div>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>
            {item.subject_ref ? `${item.subject_ref} brief` : "Audit report"}
          </div>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}>
            {item.model && <>{item.model} · </>}
            generated {item.created_at ? new Date(item.created_at).toLocaleString() : "—"}
          </div>
          {item.summary && (
            <div style={{ fontSize: 11.5, color: "var(--ink-2)", marginTop: 6, lineHeight: 1.5 }}>{item.summary}</div>
          )}
        </div>
        <button className="btn btn-sm" onClick={() => setExpanded(e => !e)}>
          {expanded ? "Collapse" : "Review"}
        </button>
      </div>

      {expanded && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 6 }}>
            Open the {item.kind === "audit_report" ? "Loop Report" : "Persona"} tab on the source run to read the full generated text before clearing this.
          </div>
          <textarea className="fi-ta" value={note} onChange={e => setNote(e.target.value)}
            placeholder="Review notes (optional)…" style={{ minHeight: 50, width: "100%", boxSizing: "border-box" }} />
          {err && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 8 }}>{err}</div>}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button className="btn btn-sm btn-primary" disabled={busy} onClick={submit}>
              {busy ? <><span className="spin" /> Submitting…</> : "Mark Reviewed"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// Bails a setState out to the exact previous array (reference-equal, so React
// skips the re-render entirely — it only bails on Object.is, not deep
// equality) when the freshly-fetched data is unchanged from what's already
// showing. Without this, every 5s poll tick — even when nobody has touched
// the queue — replaced items/telemetryItems/aiReviewItems with new array
// instances and forced React to re-render and re-diff the whole (often
// 50-100 row) list, which is what shows up as "Forced reflow" console
// violations: a large, otherwise-pointless layout recalculation every poll.
function _sameContent(a, b) {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  return JSON.stringify(a) === JSON.stringify(b);
}

function ApprovalInboxScreen() {
  const LiveBadge = window.LiveBadge;
  const auth = window.useAuth ? window.useAuth() : null;
  const currentUserId = auth?.user?.id;
  const isAdmin = auth?.user?.role === "admin";
  // Admin-only escape hatch for an item whose assigned manager_id belongs
  // to an account nobody actually uses (a real dead end otherwise — see
  // approvals_endpoints.review_item's admin-override branch, which is what
  // actually lets a decision land here): shows every submitted item
  // org-wide, not just ones routed to the logged-in admin specifically.
  const [showAllPending, setShowAllPending] = React.useState(false);
  const [items, setItems] = React.useState([]);
  const [telemetryItems, setTelemetryItems] = React.useState([]);
  const [aiReviewItems, setAiReviewItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [lastRefresh, setLastRefresh] = React.useState(null);
  const [isPaused, setIsPaused] = React.useState(false);
  // Tracks whether we've completed at least one load, distinct from `loading`
  // itself — the 5s background poll below also flips `loading` true/false on
  // every tick, and gating the whole list render on `loading` (see below)
  // collapsed the entire inbox down to a one-line "Loading…" placeholder and
  // back every 5 seconds, which is what made the page look like it kept
  // jumping to the top: the list's height (and the user's scroll position
  // within it) got wiped out on every poll, not just the first one.
  const hasLoadedRef = React.useRef(false);

  const reload = React.useCallback(async () => {
    // Only the very first load shows the "Loading…" placeholder — flipping
    // `loading` true/false on every background poll forced a render even
    // when the fetched data below turns out unchanged.
    if (!hasLoadedRef.current) setLoading(true);
    setError(null);
    try {
      const inboxUrl = showAllPending && isAdmin ? "/approvals/inbox?all_pending=true" : "/approvals/inbox";
      const [gateRes, telRes, aiRes] = await Promise.all([
        fetch(inboxUrl, { credentials: "include" }),
        fetch(`${window.MCP_API_BASE || "/api/mcp"}/observability/telemetry/human-review`, { credentials: "include" }),
        fetch(`${window.MCP_API_BASE || "/api/mcp"}/ai/review-queue`, { credentials: "include" }),
      ]);
      if (!gateRes.ok) {
        const err = new Error(await gateRes.text());
        err.status = gateRes.status;
        throw err;
      }
      const gateData = await gateRes.json();
      setItems(prev => _sameContent(prev, gateData.items || []) ? prev : (gateData.items || []));
      // Telemetry review and AI narrative review are both best-effort — a
      // DB-unavailable backend shouldn't take down the Gate-item half of
      // this screen.
      const telData = telRes.ok ? (await telRes.json()).rows || [] : [];
      setTelemetryItems(prev => _sameContent(prev, telData) ? prev : telData);
      const aiData = aiRes.ok ? (await aiRes.json()).items || [] : [];
      setAiReviewItems(prev => _sameContent(prev, aiData) ? prev : aiData);
      setLastRefresh(new Date());
    } catch (e) {
      setError(e.message);
      // Re-throw so usePolling's 401 counter can see it and, after enough
      // consecutive hits, stop polling and log out instead of retrying a
      // dead session every 5 seconds forever.
      if (e && e.status === 401) throw e;
    } finally {
      setLoading(false);
      hasLoadedRef.current = true;
    }
  }, [showAllPending, isAdmin]);

  window.usePolling(reload, 5000, { paused: isPaused });

  async function handleDecide(taskId, decision, comment) {
    const res = await fetch("/approvals/review", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId, decision, comment }),
    });
    if (!res.ok) throw new Error(await res.text());
    setItems(prev => prev.filter(i => i.id !== taskId));
  }

  // Telemetry and AI-narrative reviews are broadcast to every manager's inbox
  // at once — first to review wins, so a failed decide here is very often
  // "someone else already resolved this," not a real error. There's nowhere
  // for that fact to surface without a toast, and no reason to make the user
  // wait for their next manual refresh to find out.
  async function handleDecideTelemetry(rowId, verdict, notes) {
    const res = await fetch(`${window.MCP_API_BASE || "/api/mcp"}/observability/telemetry/adjudicated/${rowId}/review`, {
      method: "PUT", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ human_verdict: verdict, notes }),
    });
    if (!res.ok) {
      window.showToast?.("This item may have already been resolved by someone else — refreshing the queue.", { tone: "warn" });
      reload();
      throw new Error(await res.text());
    }
    // The backend clears requires_human_review on the shared row — this just
    // drops it from the current view immediately rather than waiting on the
    // next reload; every other user's inbox loses it on their next reload.
    setTelemetryItems(prev => prev.filter(i => i.id !== rowId));
  }

  async function handleDecideAiReview(analysisId, note) {
    const res = await fetch(`${window.MCP_API_BASE || "/api/mcp"}/ai/review-queue/${analysisId}/review`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    });
    if (!res.ok) {
      window.showToast?.("This item may have already been resolved by someone else — refreshing the queue.", { tone: "warn" });
      reload();
      throw new Error(await res.text());
    }
    setAiReviewItems(prev => prev.filter(i => i.id !== analysisId));
  }

  const totalCount = items.length + telemetryItems.length + aiReviewItems.length;

  return (
    <div className="scope-screen" data-screen-label="Approval Inbox">
      <div className="panel-head">
        <div>
          <div className="kicker">Governance · My Queue</div>
          <div className="panel-title mt-8">Approval Inbox</div>
          <div className="panel-sub">
            Gate adjustments from Enterprise Risk and SOX Risk Assessment awaiting your review as manager, plus
            UBO™ Governance Brain telemetry flagged for human review, plus every AI-generated persona brief and
            audit report awaiting its pre-delivery check. Gate items are routed to you specifically; telemetry
            and AI narrative reviews are broadcast to every user — whoever reviews one first resolves it for everyone.
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {isAdmin && (
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--ink-3)", cursor: "pointer" }}
              title="Every submitted gate item org-wide, not just ones routed to you — the admin escape hatch for an item whose assigned manager never logs in to review it.">
              <input type="checkbox" checked={showAllPending} onChange={e => setShowAllPending(e.target.checked)} />
              Show all pending (admin)
            </label>
          )}
          {LiveBadge && (
            <LiveBadge lastRefresh={lastRefresh} isPaused={isPaused}
              onToggle={() => setIsPaused(p => !p)} intervalLabel="5s" />
          )}
        </div>
      </div>

      {error && (
        <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", background: "var(--red-soft)", padding: "6px 10px", borderRadius: 4, marginBottom: 12 }}>{error}</div>
      )}

      {loading && !hasLoadedRef.current ? (
        <div style={{ padding: "24px 0", textAlign: "center", color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>
      ) : totalCount === 0 ? (
        <Empty>Nothing awaiting review right now.</Empty>
      ) : (
        <div>
          {telemetryItems.length > 0 && (
            <>
              <div className="mono" style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: "0.06em", color: "var(--red-ink)", marginBottom: 8 }}>
                ⚠ TELEMETRY REVIEW · {telemetryItems.length} REQUIRING ATTENTION
              </div>
              {telemetryItems.map(item => <TelemetryReviewItem key={`tel-${item.id}`} item={item} onDecide={handleDecideTelemetry} />)}
            </>
          )}
          {items.length > 0 && (
            <>
              {(telemetryItems.length > 0 || aiReviewItems.length > 0 || showAllPending) && (
                <div className="mono" style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: "0.06em", color: "var(--ink-4)", margin: "16px 0 8px" }}>
                  GATE ADJUSTMENTS · {items.length} {showAllPending && isAdmin ? "PENDING ORG-WIDE" : "AWAITING YOUR REVIEW"}
                </div>
              )}
              {items.map(item => <InboxItem key={item.id} item={item} currentUserId={currentUserId} onDecide={handleDecide} />)}
            </>
          )}
          {aiReviewItems.length > 0 && (
            <>
              {(telemetryItems.length > 0 || items.length > 0) && (
                <div className="mono" style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: "0.06em", color: "var(--ink-4)", margin: "16px 0 8px" }}>
                  AI NARRATIVE REVIEW · {aiReviewItems.length} AWAITING PRE-DELIVERY CHECK
                </div>
              )}
              {aiReviewItems.map(item => <AiNarrativeReviewItem key={`ai-${item.id}`} item={item} onDecide={handleDecideAiReview} />)}
            </>
          )}
        </div>
      )}

      <RemediationActivitySection />
    </div>
  );
}

Object.assign(window, { ApprovalInboxScreen });
