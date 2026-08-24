/* ============================================================
   Exception Management — Continuous Control Monitoring triage.

   Ported from devriskops-ccm (a standalone Streamlit + FastAPI + Airflow
   service committed to this repo but never wired into the React dashboard)
   into this app's own DB/auth/connector infrastructure. Development
   environment only — see deploy_env.py; the backend 404s this entire
   feature outside Development, and nav.jsx hides its nav entry the same way.

   "Exceptions" are connector events (real adapters and the synthetic
   transaction simulator alike) whose anomaly/uncertainty score crosses a
   review threshold as they're ingested (connector_poller.py's scoring
   hook, exception_tool.py) — a second, model-scored lens on the same
   traffic Policy-as-Code's rule-based violations already watch on
   Continuous Watch, not a separate stream.

   Three tabs:
     - Triage Queue    — resolve events awaiting human review
     - Model Analytics — resolution mix + recent-decision history
     - Feature Drift    — PSI on each system's score distribution over time,
                           reusing Model Vitals' drift-incident backend/UI
                           pattern (metric_kind="exception")

   Data: window.MCP.exceptions* (mcp-data.js) -> /exceptions/* (exceptions_endpoints.py),
   plus /model-health/drift-incidents + /model-health/baseline-reset (reused as-is).
   ============================================================ */

const _EXC_RESOLUTION_LABELS = [
  { value: "TRUE_CONTROL_FAILURE", label: "True Control Failure", tone: "bad",
    what: "The control genuinely failed to do its job — a real finding, not noise. Requires notes." },
  { value: "BENIGN_OPERATIONAL_NOISE", label: "Benign Operational Noise", tone: "good",
    what: "Flagged, but on inspection this is normal business activity the scoring model was too sensitive to." },
  { value: "APPROVED_CARVE_OUT", label: "Approved Carve-Out", tone: "neutral",
    what: "Outside normal parameters, but already covered by a documented, approved exception. Requires notes." },
  { value: "DATA_PIPELINE_ERROR", label: "Data Pipeline Error", tone: "warn",
    what: "The event itself is bad data (a connector glitch, a malformed record) — not a real control signal either way." },
];
const _EXC_NOTES_REQUIRED = new Set(["TRUE_CONTROL_FAILURE", "APPROVED_CARVE_OUT"]);
const _EXC_LABEL_META = Object.fromEntries(_EXC_RESOLUTION_LABELS.map(l => [l.value, l]));
const _EXC_LABEL_COLOR = {
  TRUE_CONTROL_FAILURE: "var(--red-ink)", BENIGN_OPERATIONAL_NOISE: "var(--green-ink)",
  APPROVED_CARVE_OUT: "var(--ink-3)", DATA_PIPELINE_ERROR: "var(--amber-ink)",
};

// R/A/G — same vocabulary management_action_plans.risk_rating / risk_scores.rag_status
// use elsewhere in this app. A genuinely separate signal from anomaly/uncertainty
// (see exception_tool.py's module docstring) — combines severity with the
// producing connector's own risk_tier, so it's shown as its own pill, not folded
// into the score bars above.
const _RISK_RATING_META = {
  R: { label: "R — Urgent", bg: "var(--red-soft)", ink: "var(--red-ink)" },
  A: { label: "A — Moderate", bg: "var(--amber-soft)", ink: "var(--amber-ink)" },
  G: { label: "G — Low", bg: "var(--green-soft)", ink: "var(--green-ink)" },
};

function RiskRatingPill({ rating }) {
  const meta = _RISK_RATING_META[rating];
  if (!meta) return <span style={{ fontSize: 9.5, color: "var(--ink-4)" }}>Unrated</span>;
  return (
    <span className="mono" style={{
      fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999,
      background: meta.bg, color: meta.ink, whiteSpace: "nowrap",
    }}>
      {meta.label}
    </span>
  );
}

function _excScoreTone(score) {
  if (score >= 0.70) return "bad";
  if (score >= 0.40) return "warn";
  return "good";
}
function _excToneColor(tone) {
  return tone === "bad" ? "var(--red-ink)" : tone === "warn" ? "var(--amber-ink)" : "var(--green-ink)";
}

// "exception_<system_source>_<anomaly_score|uncertainty_score>" -> [system, metric label]
function _excParseMetricKey(key) {
  const m = /^exception_(.+)_(anomaly_score|uncertainty_score)$/.exec(key || "");
  if (!m) return [key || "—", ""];
  return [m[1], m[2] === "anomaly_score" ? "Anomaly score" : "Uncertainty score"];
}

function ExcScoreBar({ label, value }) {
  const color = _excToneColor(_excScoreTone(value));
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10 }}>
      <span style={{ color: "var(--ink-4)", width: 74, flexShrink: 0 }}>{label}</span>
      <div style={{ flex: 1, height: 5, borderRadius: 3, background: "var(--surface-2)", overflow: "hidden" }}>
        <div style={{ width: `${Math.round(value * 100)}%`, height: "100%", background: color }} />
      </div>
      <span className="mono" style={{ fontWeight: 700, color, width: 34, textAlign: "right" }}>{value.toFixed(2)}</span>
    </div>
  );
}

function ExcFeatureChips({ features }) {
  const entries = Object.entries(features || {});
  if (!entries.length) return <span style={{ fontSize: 10, color: "var(--ink-4)" }}>No numeric fields captured on this event.</span>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
      {entries.map(([k, v]) => (
        <span key={k} className="mono" style={{
          fontSize: 9.5, padding: "2px 7px", borderRadius: 999, background: "var(--surface-2)",
          color: "var(--ink-2)", border: "1px solid var(--line)",
        }}>{k}: {typeof v === "boolean" ? String(v) : v}</span>
      ))}
    </div>
  );
}

// ── Triage Queue ─────────────────────────────────────────────────────────────

function TriageForm({ onSubmit, submitting }) {
  const [label, setLabel] = React.useState(null);
  const [notes, setNotes] = React.useState("");
  const needsNotes = label && _EXC_NOTES_REQUIRED.has(label);
  const canSubmit = label && (!needsNotes || notes.trim().length > 0) && !submitting;

  return (
    <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
        {_EXC_RESOLUTION_LABELS.map(l => (
          <button key={l.value} type="button" onClick={() => setLabel(l.value)} title={l.what}
            style={{
              fontSize: 10.5, padding: "5px 10px", borderRadius: 5, cursor: "pointer",
              border: l.value === label ? "1px solid var(--acc,#2563eb)" : "1px solid var(--line)",
              background: l.value === label ? "var(--acc,#2563eb)" : "transparent",
              color: l.value === label ? "#fff" : "var(--ink-2)",
              fontWeight: l.value === label ? 600 : 400,
            }}>
            {l.label}
          </button>
        ))}
      </div>
      {label && <div style={{ fontSize: 10.5, color: "var(--ink-3)", marginBottom: 8 }}>{_EXC_LABEL_META[label].what}</div>}
      {needsNotes && (
        <textarea className="code-input" rows={2} placeholder="Justification notes (required for this resolution)…"
          value={notes} onChange={e => setNotes(e.target.value)}
          style={{ width: "100%", fontSize: 11, marginBottom: 8, resize: "vertical" }} />
      )}
      <button className="btn btn-acc btn-sm" disabled={!canSubmit} onClick={() => onSubmit(label, notes)}>
        {submitting ? "Submitting…" : "Resolve exception"}
      </button>
    </div>
  );
}

// Raw payload values are already human-readable JSON scalars/short strings
// (see synthetic_transaction_tool.py's SimStep builders and every real
// connector adapter's pull_events) — same flat key:value chip treatment as
// ExcFeatureChips, just a second, separate block so "what the model saw"
// (point_in_time_features) and "what the event actually said" (raw_payload)
// stay visually distinct instead of merged into one undifferentiated list.
function ExcPayloadChips({ payload }) {
  const entries = Object.entries(payload || {});
  if (!entries.length) return <span style={{ fontSize: 10, color: "var(--ink-4)" }}>No payload captured on this event.</span>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
      {entries.map(([k, v]) => (
        <span key={k} className="mono" style={{
          fontSize: 9.5, padding: "2px 7px", borderRadius: 999, background: "var(--surface-2)",
          color: "var(--ink-2)", border: "1px solid var(--line)",
        }}>{k}: {typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}</span>
      ))}
    </div>
  );
}

function TriageQueueRow({ row, onResolved, onNavigate }) {
  const [expanded, setExpanded] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [remediation, setRemediation] = React.useState(null);
  const [remediating, setRemediating] = React.useState(false);
  const [remediationError, setRemediationError] = React.useState(null);
  const [showPrForm, setShowPrForm] = React.useState(false);
  const [prFilePath, setPrFilePath] = React.useState("");

  async function handleSubmit(label, notes) {
    setSubmitting(true);
    setError(null);
    try {
      await window.MCP.submitExceptionTriage(row.event_id, label, notes);
      onResolved(row.event_id);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function handlePropose() {
    setRemediating(true);
    setRemediationError(null);
    try {
      const { task } = await window.MCP.proposeRemediation(row.event_id);
      setRemediation(task);
    } catch (e) {
      setRemediationError(e.message || String(e));
    } finally {
      setRemediating(false);
    }
  }

  async function handleProposePr() {
    if (!prFilePath.trim()) {
      setRemediationError("Enter the repo file path this finding maps to.");
      return;
    }
    setRemediating(true);
    setRemediationError(null);
    try {
      const { task } = await window.MCP.proposeRemediationPr(row.event_id, prFilePath.trim());
      setRemediation(task);
      setShowPrForm(false);
    } catch (e) {
      setRemediationError(e.message || String(e));
    } finally {
      setRemediating(false);
    }
  }

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "10px 12px", marginBottom: 8, background: "var(--surface)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, cursor: "pointer" }}
        onClick={() => setExpanded(e => !e)}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 11.5, fontWeight: 600 }}>
            {row.control_id} <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>· {row.system_source}{row.process ? ` · ${row.process}` : ""}</span>
          </div>
          <div style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 2 }}>
            {row.action ? <span style={{ color: "var(--ink-3)" }}>{row.action}{row.actor ? ` · ${row.actor}` : ""} · </span> : null}
            {row.event_timestamp ? new Date(row.event_timestamp).toLocaleString() : "—"}
            {row.assigned_owner ? <span> · owner: {row.assigned_owner}</span> : null}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 150, flexShrink: 0, alignItems: "flex-end" }}>
          <RiskRatingPill rating={row.risk_rating} />
          <ExcScoreBar label="Anomaly" value={row.anomaly_score} />
          <ExcScoreBar label="Uncertainty" value={row.uncertainty_score} />
        </div>
      </div>
      {expanded && (
        <>
          <div style={{ marginTop: 10, display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
            <div className="kicker" style={{ fontSize: 9.5 }}>What changed</div>
            <div style={{ display: "flex", gap: 6 }}>
              {row.system_telemetry_id && onNavigate && (
                <button type="button" className="btn btn-sm"
                  onClick={() => onNavigate("ubogov", { cemTab: "adjudications", cemFilter: { source: row.system_source } })}>
                  View in Telemetry Detail →
                </button>
              )}
              {!remediation && (
                <button type="button" className="btn btn-sm" disabled={remediating} onClick={handlePropose}
                  title="Draft a GitHub issue for this finding and submit it for manager approval">
                  {remediating ? "Drafting…" : "Propose remediation"}
                </button>
              )}
              {!remediation && !showPrForm && (
                <button type="button" className="btn btn-sm" disabled={remediating} onClick={() => setShowPrForm(true)}
                  title="Draft a real GitHub pull request fixing a specific repo file for this finding">
                  Propose PR fix
                </button>
              )}
            </div>
          </div>
          {showPrForm && !remediation && (
            <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 6, marginBottom: 4 }} onClick={e => e.stopPropagation()}>
              <input type="text" placeholder="e.g. .github/workflows/ci.yml" value={prFilePath}
                onChange={e => setPrFilePath(e.target.value)}
                style={{ fontSize: 11, padding: "4px 7px", border: "1px solid var(--line)", borderRadius: 4, minWidth: 240, flex: 1 }} />
              <button type="button" className="btn btn-sm" disabled={remediating} onClick={handleProposePr}>
                {remediating ? "Drafting…" : "Draft PR"}
              </button>
              <button type="button" className="btn btn-sm btn-ghost" disabled={remediating} onClick={() => setShowPrForm(false)}>
                Cancel
              </button>
            </div>
          )}
          <div style={{ fontSize: 11, color: "var(--ink)", marginTop: 4, marginBottom: 8 }}>
            {row.event_type ? <span className="mono" style={{ fontSize: 10 }}>{row.event_type}</span> : <span style={{ color: "var(--ink-4)" }}>Event type not captured</span>}
            {row.actor && <span style={{ color: "var(--ink-3)" }}> — performed by {row.actor}</span>}
          </div>
          <ExcPayloadChips payload={row.raw_payload} />
          <div className="kicker" style={{ fontSize: 9.5, marginTop: 10, marginBottom: 4 }}>Model features at scoring time</div>
          <ExcFeatureChips features={row.point_in_time_features} />
          {remediationError && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 8 }}>{remediationError}</div>}
          {remediation && (
            <div className="mono" style={{ fontSize: 10.5, color: "var(--acc-ink)", marginTop: 8 }}>
              {remediation.gate_type === "remediation_github_pr" ? "GitHub PR fix" : "Remediation"} proposed — {remediation.status === "submitted" ? "awaiting manager approval" : `status: ${remediation.status}`} in the Approval Inbox.
            </div>
          )}
          {error && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 8 }}>{error}</div>}
          <TriageForm onSubmit={handleSubmit} submitting={submitting} />
        </>
      )}
    </div>
  );
}

// One row per (control_id, system_source) recurring pattern instead of one
// per event — the curation lever for a queue that's grown out of hand.
// getFlatRows() is a memoized fetch-once accessor into the full flat pending
// list (owned by TriageQueueTab) so expanding N groups doesn't mean N
// separate network round trips.
function GroupedQueueRow({ group, onResolved, onNavigate, getFlatRows }) {
  const [expanded, setExpanded] = React.useState(false);
  const [members, setMembers] = React.useState(null);
  const [membersLoading, setMembersLoading] = React.useState(false);
  const [bulkLabel, setBulkLabel] = React.useState(null);
  const [bulkNotes, setBulkNotes] = React.useState("");
  const [bulkSubmitting, setBulkSubmitting] = React.useState(false);
  const [bulkError, setBulkError] = React.useState(null);
  const [bulkDone, setBulkDone] = React.useState(false);

  async function toggleExpand() {
    const next = !expanded;
    setExpanded(next);
    if (next && members === null) {
      setMembersLoading(true);
      try {
        const flat = await getFlatRows();
        setMembers(flat.filter(r => r.control_id === group.control_id && r.system_source === group.system_source));
      } finally {
        setMembersLoading(false);
      }
    }
  }

  function handleMemberResolved(eventId) {
    setMembers(ms => (ms || []).filter(r => r.event_id !== eventId));
    onResolved && onResolved(eventId, group);
  }

  const needsNotes = bulkLabel && _EXC_NOTES_REQUIRED.has(bulkLabel);
  const canBulkSubmit = bulkLabel && (!needsNotes || bulkNotes.trim().length > 0) && !bulkSubmitting;

  async function handleBulkSubmit() {
    setBulkSubmitting(true);
    setBulkError(null);
    try {
      await window.MCP.exceptionsBulkTriage(group.control_id, group.system_source, bulkLabel, bulkNotes);
      setBulkDone(true);
      onResolved && onResolved(null, group);
    } catch (e) {
      setBulkError(e.message || String(e));
    } finally {
      setBulkSubmitting(false);
    }
  }

  if (bulkDone) return null;

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "10px 12px", marginBottom: 8, background: "var(--surface)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, cursor: "pointer" }}
        onClick={toggleExpand}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 11.5, fontWeight: 600, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            {group.control_id}
            <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>· {group.system_source}</span>
            <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, padding: "1px 7px", borderRadius: 999, background: "var(--surface-2)", color: "var(--ink-2)" }}>
              ×{group.occurrence_count}
            </span>
            {group.has_open_map && (
              <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, padding: "1px 7px", borderRadius: 999, background: "var(--acc-soft)", color: "var(--acc-ink)" }}
                onClick={e => { e.stopPropagation(); onNavigate && onNavigate("continuousmonitoring"); }}
                title="Already tracked by a Management Action Plan">
                Tracked by {group.map_ref}
              </span>
            )}
          </div>
          <div style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 2 }}>
            {group.owner ? `owner: ${group.owner} · ` : ""}
            first seen {group.first_seen_at ? new Date(group.first_seen_at).toLocaleDateString() : "—"}
            {" · "}last seen {group.last_seen_at ? new Date(group.last_seen_at).toLocaleString() : "—"}
          </div>
        </div>
        <RiskRatingPill rating={group.worst_risk_rating} />
      </div>

      {expanded && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
          {group.occurrence_count > 1 && (
            <div style={{ marginBottom: 10, padding: "8px 10px", background: "var(--surface-2)", borderRadius: 5 }}>
              <div className="kicker" style={{ fontSize: 9.5, marginBottom: 6 }}>
                Resolve all {group.occurrence_count} as one decision
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                {_EXC_RESOLUTION_LABELS.map(l => (
                  <button key={l.value} type="button" onClick={() => setBulkLabel(l.value)} title={l.what}
                    style={{
                      fontSize: 10.5, padding: "5px 10px", borderRadius: 5, cursor: "pointer",
                      border: l.value === bulkLabel ? "1px solid var(--acc,#2563eb)" : "1px solid var(--line)",
                      background: l.value === bulkLabel ? "var(--acc,#2563eb)" : "transparent",
                      color: l.value === bulkLabel ? "#fff" : "var(--ink-2)",
                      fontWeight: l.value === bulkLabel ? 600 : 400,
                    }}>
                    {l.label}
                  </button>
                ))}
              </div>
              {needsNotes && (
                <textarea className="code-input" rows={2} placeholder="Justification notes (required for this resolution)…"
                  value={bulkNotes} onChange={e => setBulkNotes(e.target.value)}
                  style={{ width: "100%", fontSize: 11, marginBottom: 8, resize: "vertical" }} />
              )}
              {bulkError && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 8 }}>{bulkError}</div>}
              <button className="btn btn-acc btn-sm" disabled={!canBulkSubmit} onClick={handleBulkSubmit}>
                {bulkSubmitting ? "Resolving…" : `Resolve all ${group.occurrence_count} as ${bulkLabel ? _EXC_LABEL_META[bulkLabel].label : "…"}`}
              </button>
            </div>
          )}
          <div className="kicker" style={{ fontSize: 9.5, marginBottom: 6 }}>Individual events</div>
          {membersLoading ? <Empty>Loading…</Empty> : (members || []).map(row => (
            <TriageQueueRow key={row.event_id} row={row} onResolved={handleMemberResolved} onNavigate={onNavigate} />
          ))}
        </div>
      )}
    </div>
  );
}

function TriageQueueTab({ onResolved, onNavigate }) {
  const [grouped, setGrouped] = React.useState(true);
  const [rows, setRows] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [riskRating, setRiskRating] = React.useState("");
  const [owner, setOwner] = React.useState("");
  const flatCacheRef = React.useRef(null);

  const load = React.useCallback(() => {
    setLoading(true);
    flatCacheRef.current = null;
    return window.MCP.exceptionsPending({
      limit: grouped ? 200 : 100, group: grouped,
      riskRating: riskRating || undefined, owner: owner || undefined,
    })
      .then(d => { setRows(d.rows || []); setError(null); })
      .catch(e => setError(e.message || String(e)))
      .finally(() => setLoading(false));
  }, [grouped, riskRating, owner]);

  React.useEffect(() => { load(); }, [load]);

  const getFlatRows = React.useCallback(async () => {
    if (flatCacheRef.current) return flatCacheRef.current;
    const d = await window.MCP.exceptionsPending({ limit: 1000, riskRating: riskRating || undefined, owner: owner || undefined });
    flatCacheRef.current = d.rows || [];
    return flatCacheRef.current;
  }, [riskRating, owner]);

  function handleResolved(eventId) {
    setRows(rs => rs.filter(r => r.event_id !== eventId));
    onResolved && onResolved();
  }

  function handleGroupResolved(eventId, group) {
    if (eventId === null) {
      // bulk-resolved — the whole group disappears from the grouped list
      setRows(rs => rs.filter(r => !(r.control_id === group.control_id && r.system_source === group.system_source)));
    }
    onResolved && onResolved();
  }

  const owners = React.useMemo(() => {
    const seen = new Set();
    rows.forEach(r => { if (r.owner || r.assigned_owner) seen.add(r.owner || r.assigned_owner); });
    return [...seen].sort();
  }, [rows]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
        <div className="kicker">
          {loading ? "Loading…" : grouped
            ? `${rows.length} recurring pattern(s) awaiting review`
            : `${rows.length} event(s) awaiting review`}
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <label style={{ fontSize: 10.5, display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={grouped} onChange={e => setGrouped(e.target.checked)} />
            Group by control/system
          </label>
          <select className="code-input" style={{ fontSize: 10.5, padding: "3px 6px" }}
            value={riskRating} onChange={e => setRiskRating(e.target.value)}>
            <option value="">All risk ratings</option>
            <option value="R">R — Urgent</option>
            <option value="A">A — Moderate</option>
            <option value="G">G — Low</option>
          </select>
          {owners.length > 0 && (
            <select className="code-input" style={{ fontSize: 10.5, padding: "3px 6px" }}
              value={owner} onChange={e => setOwner(e.target.value)}>
              <option value="">All owners</option>
              {owners.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          )}
          <button className="btn btn-sm" onClick={load}>Refresh</button>
        </div>
      </div>

      {loading && !rows.length ? <Empty>Loading pending exceptions…</Empty> : error ? (
        <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)" }}>{error}</div>
      ) : !rows.length ? (
        <Empty icon="✓">
          No exceptions awaiting review right now. New ones appear here as connector events cross the
          anomaly/uncertainty threshold — check back after Continuous Watch has ingested some activity.
        </Empty>
      ) : grouped ? (
        rows.map(group => (
          <GroupedQueueRow key={`${group.control_id}::${group.system_source}`} group={group}
            onResolved={handleGroupResolved} onNavigate={onNavigate} getFlatRows={getFlatRows} />
        ))
      ) : (
        rows.map(row => <TriageQueueRow key={row.event_id} row={row} onResolved={handleResolved} onNavigate={onNavigate} />)
      )}
    </div>
  );
}

// ── Model Analytics ───────────────────────────────────────────────────────────

function ResolutionMixBars({ mix }) {
  const entries = _EXC_RESOLUTION_LABELS.map(l => [l.value, mix[l.value] || 0]);
  const total = entries.reduce((s, [, n]) => s + n, 0);
  if (!total) return <Empty>No resolved exceptions yet.</Empty>;
  return (
    <div>
      {entries.map(([value, n]) => (
        <div key={value} style={{ marginBottom: 8 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, marginBottom: 3 }}>
            <span>{_EXC_LABEL_META[value].label}</span>
            <span className="mono" style={{ color: "var(--ink-4)" }}>{n} ({Math.round(n / total * 100)}%)</span>
          </div>
          <div style={{ height: 6, borderRadius: 3, background: "var(--surface-2)", overflow: "hidden" }}>
            <div style={{ width: `${(n / total) * 100}%`, height: "100%", background: _EXC_LABEL_COLOR[value] }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function HistoryTable({ rows }) {
  if (!rows.length) return <Empty>No resolved exceptions yet.</Empty>;
  return (
    <div>
      {rows.map(r => (
        <div key={r.triage_id} style={{ display: "flex", justifyContent: "space-between", padding: "7px 0", borderBottom: "1px solid var(--line)", gap: 10, fontSize: 10.5 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600 }}>{r.control_id} <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>· {r.system_source}</span></div>
            <div style={{ color: "var(--ink-4)", fontSize: 9.5, marginTop: 1 }}>
              {r.auditor} · {r.reviewed_at ? new Date(r.reviewed_at).toLocaleString() : "—"}
            </div>
          </div>
          <span className="mono" style={{ fontWeight: 700, color: _EXC_LABEL_COLOR[r.resolution_label], whiteSpace: "nowrap" }}>
            {_EXC_LABEL_META[r.resolution_label]?.label || r.resolution_label}
          </span>
        </div>
      ))}
    </div>
  );
}

function ModelAnalyticsTab() {
  const [summary, setSummary] = React.useState(null);
  const [history, setHistory] = React.useState([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    Promise.all([window.MCP.exceptionsSummary(), window.MCP.exceptionsHistory(100)])
      .then(([s, h]) => { setSummary(s); setHistory(h.rows || []); })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Empty>Loading…</Empty>;

  const bySystem = Object.entries(summary?.pending_by_system || {});
  const byOwner = Object.entries(summary?.pending_by_owner || {});
  const byRiskRating = ["R", "A", "G"].map(r => [r, (summary?.pending_by_risk_rating || {})[r] || 0]);

  return (
    <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
      <div style={{ flex: 1, minWidth: 280 }}>
        <div className="kicker" style={{ marginBottom: 8 }}>Resolution mix (all-time)</div>
        <ResolutionMixBars mix={summary?.resolution_mix || {}} />

        <div className="kicker" style={{ marginTop: 20, marginBottom: 8 }}>Pending, by risk rating</div>
        {byRiskRating.map(([rating, n]) => (
          <div key={rating} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, padding: "4px 0" }}>
            <RiskRatingPill rating={rating} /><span className="mono" style={{ fontWeight: 700 }}>{n}</span>
          </div>
        ))}

        <div className="kicker" style={{ marginTop: 20, marginBottom: 8 }}>Pending, by owner</div>
        {byOwner.length
          ? byOwner.map(([own, n]) => (
              <div key={own} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, padding: "4px 0" }}>
                <span>{own}</span><span className="mono" style={{ fontWeight: 700 }}>{n}</span>
              </div>
            ))
          : <Empty>Nothing pending.</Empty>}

        <div className="kicker" style={{ marginTop: 20, marginBottom: 8 }}>Pending, by system</div>
        {bySystem.length
          ? bySystem.map(([sys, n]) => (
              <div key={sys} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, padding: "4px 0" }}>
                <span>{sys}</span><span className="mono" style={{ fontWeight: 700 }}>{n}</span>
              </div>
            ))
          : <Empty>Nothing pending.</Empty>}
      </div>
      <div style={{ flex: 2, minWidth: 340 }}>
        <div className="kicker" style={{ marginBottom: 8 }}>Recent decisions</div>
        <HistoryTable rows={history} />
      </div>
    </div>
  );
}

// ── Feature Drift ──────────────────────────────────────────────────────────────
// Reuses Model Vitals' drift-incident backend + PSI math wholesale
// (metric_kind="exception") — see exceptions_endpoints.compute_exception_drift
// and api_server.py's /model-health/drift-incidents, /model-health/baseline-reset.

const _EXC_FLAG_STYLE = {
  stable: { bg: "var(--green-soft)", ink: "var(--green-ink)", label: "Stable" },
  watch: { bg: "var(--amber-soft)", ink: "var(--amber-ink)", label: "Watch" },
  drift: { bg: "var(--red-soft)", ink: "var(--red-ink)", label: "Drift" },
  insufficient_data: { bg: "var(--surface-2)", ink: "var(--ink-4)", label: "Insufficient data" },
};

function ExcFlagBadge({ flag }) {
  const s = _EXC_FLAG_STYLE[flag] || _EXC_FLAG_STYLE.insufficient_data;
  return (
    <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: s.bg, color: s.ink, whiteSpace: "nowrap" }}>
      {s.label}
    </span>
  );
}

function DriftLiveRow({ row }) {
  const metricLabel = row.metric === "anomaly_score" ? "Anomaly score" : "Uncertainty score";
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--line)", gap: 12 }}>
      <div>
        <div style={{ fontSize: 11.5, fontWeight: 600 }}>{row.system_source} <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>· {metricLabel}</span></div>
        <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 2 }}>
          {row.psi != null ? `PSI ${row.psi.toFixed(3)}` : "PSI —"} · baseline n={row.n_baseline} · current n={row.n_current}
        </div>
      </div>
      <ExcFlagBadge flag={row.flag} />
    </div>
  );
}

const _EXC_INCIDENT_STATUS_STYLE = {
  open: { bg: "var(--red-soft)", ink: "var(--red-ink)", label: "Open" },
  acknowledged: { bg: "var(--amber-soft)", ink: "var(--amber-ink)", label: "Acknowledged" },
  resolved: { bg: "var(--green-soft)", ink: "var(--green-ink)", label: "Resolved" },
};

function ExcIncidentRow({ incident, onUpdate, saving }) {
  const [owner, setOwner] = React.useState(incident.owner || "");
  const [notes, setNotes] = React.useState(incident.notes || "");
  const st = _EXC_INCIDENT_STATUS_STYLE[incident.status] || _EXC_INCIDENT_STATUS_STYLE.open;
  const [system, metric] = _excParseMetricKey(incident.metric_key);

  React.useEffect(() => { setOwner(incident.owner || ""); }, [incident.owner]);
  React.useEffect(() => { setNotes(incident.notes || ""); }, [incident.notes]);

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "10px 12px", marginBottom: 8, background: "var(--surface)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: st.bg, color: st.ink }}>{st.label}</span>
          <span style={{ fontSize: 11.5, fontWeight: 600 }}>{system} <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>· {metric}</span></span>
        </div>
        <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)" }}>
          {incident.psi != null ? `PSI ${incident.psi.toFixed(3)}` : ""} · detected {new Date(incident.detected_at).toLocaleDateString()}
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 8, marginBottom: 8 }}>
        <input className="code-input" style={{ fontSize: 11 }} placeholder="Owner (unassigned)"
          value={owner} onChange={e => setOwner(e.target.value)}
          onBlur={() => owner !== (incident.owner || "") && onUpdate(incident.id, { owner })} />
        <input className="code-input" style={{ fontSize: 11 }} placeholder="Notes"
          value={notes} onChange={e => setNotes(e.target.value)}
          onBlur={() => notes !== (incident.notes || "") && onUpdate(incident.id, { notes })} />
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        {incident.status !== "acknowledged" && incident.status !== "resolved" && (
          <button className="btn btn-sm" disabled={saving} onClick={() => onUpdate(incident.id, { status: "acknowledged" })}>Acknowledge</button>
        )}
        {incident.status !== "resolved" && (
          <button className="btn btn-sm" disabled={saving} onClick={() => onUpdate(incident.id, { status: "resolved" })}>Resolve</button>
        )}
      </div>
    </div>
  );
}

function FeatureDriftTab() {
  const [live, setLive] = React.useState([]);
  const [incidents, setIncidents] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);

  const load = React.useCallback(() => {
    setLoading(true);
    return Promise.all([
      window.MCP.exceptionsDriftSummary(),
      fetch("/api/mcp/model-health/drift-incidents", { credentials: "include" }).then(r => r.json()),
    ]).then(([drift, inc]) => {
      setLive(drift.rows || []);
      setIncidents((inc.rows || []).filter(r => r.metric_kind === "exception"));
    }).finally(() => setLoading(false));
  }, []);

  React.useEffect(() => { load(); }, [load]);

  async function handleUpdate(id, patch) {
    setSaving(true);
    try {
      await fetch(`/api/mcp/model-health/drift-incidents/${id}`, {
        method: "PUT", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      await load();
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <Empty>Loading…</Empty>;

  return (
    <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
      <div style={{ flex: 1, minWidth: 300 }}>
        <div className="kicker" style={{ marginBottom: 8 }}>Live PSI — anomaly / uncertainty score, per system</div>
        {live.length ? live.map(r => <DriftLiveRow key={r.metric_key} row={r} />) : <Empty>No scored exceptions yet.</Empty>}
      </div>
      <div style={{ flex: 1, minWidth: 300 }}>
        <div className="kicker" style={{ marginBottom: 8 }}>Tracked drift incidents</div>
        {incidents.length
          ? incidents.map(inc => <ExcIncidentRow key={inc.id} incident={inc} onUpdate={handleUpdate} saving={saving} />)
          : <Empty>No open drift incidents — one opens automatically once a system's PSI crosses 0.20.</Empty>}
      </div>
    </div>
  );
}

// ── Screen ─────────────────────────────────────────────────────────────────────

function ExceptionsScreen({ onNavigate } = {}) {
  const [tab, setTab] = React.useState("triage");
  const [summary, setSummary] = React.useState(null);
  const [summaryTick, setSummaryTick] = React.useState(0);

  React.useEffect(() => {
    window.MCP.exceptionsSummary().then(setSummary).catch(() => {});
  }, [summaryTick]);

  const bumpSummary = React.useCallback(() => setSummaryTick(t => t + 1), []);
  const resolvedCount = summary ? Object.values(summary.resolution_mix || {}).reduce((a, b) => a + b, 0) : null;

  return (
    <div className="scope-screen" data-screen-label="Exception Management">
      <div className="panel-head">
        <div>
          <div className="kicker">Development · Continuous Control Monitoring</div>
          <div className="panel-title mt-8">Exception Management</div>
          <div className="panel-sub">
            Connector events — from real systems and the synthetic transaction simulator alike — are scored for
            anomaly and uncertainty as they arrive. Anything that crosses the review threshold lands here for a human
            auditor to resolve, as a second, model-scored lens alongside Policy-as-Code's rule-based violations on
            Continuous Watch.
          </div>
        </div>
      </div>

      <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "14px 16px", marginBottom: 22, background: "var(--surface-2)" }}>
        <div className="kicker" style={{ marginBottom: 8 }}>New here? How this screen works</div>
        <ol style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.75 }}>
          <li>
            <strong>Triage Queue</strong> — every connector event gets an <strong>anomaly score</strong> (how unusual
            it looks) and an <strong>uncertainty score</strong> (how close it sits to the review boundary); events
            crossing either threshold land here. Open one, read its captured fields, and resolve it as a
            <strong> True Control Failure</strong> (a real finding), <strong> Benign Operational Noise</strong> (a
            false positive), an <strong> Approved Carve-Out</strong> (already covered by a documented exception), or a
            <strong> Data Pipeline Error</strong> (bad data, not a real signal either way).
          </li>
          <li>
            <strong>Model Analytics</strong> — the resolution mix and recent-decision history across everything
            triaged so far, broken out by source system.
          </li>
          <li>
            <strong>Feature Drift</strong> — Population Stability Index (PSI) on each system's score distribution
            over time. A drift incident opens automatically (same tracked workflow as Model Vitals) when a system's
            scoring behavior shifts materially — worth a look before trusting its recent decisions at face value.
          </li>
        </ol>
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 22 }}>
        <div style={{ flex: "1 1 180px", minWidth: 180, border: "1px solid var(--line)", borderRadius: 8, padding: "12px 14px", background: "var(--surface)" }}>
          <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>Awaiting review</div>
          <div className="mono" style={{ fontSize: 22, fontWeight: 700, color: (summary?.pending_count || 0) > 0 ? "var(--red-ink)" : "var(--ink)", marginTop: 4 }}>
            {summary?.pending_count ?? "…"}
          </div>
        </div>
        <div style={{ flex: "1 1 180px", minWidth: 180, border: "1px solid var(--line)", borderRadius: 8, padding: "12px 14px", background: "var(--surface)" }}>
          <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>Total events scored</div>
          <div className="mono" style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{summary?.total_events ?? "…"}</div>
        </div>
        <div style={{ flex: "1 1 180px", minWidth: 180, border: "1px solid var(--line)", borderRadius: 8, padding: "12px 14px", background: "var(--surface)" }}>
          <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>Resolved so far</div>
          <div className="mono" style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{resolvedCount ?? "…"}</div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
        {[["triage", "Triage Queue"], ["analytics", "Model Analytics"], ["drift", "Feature Drift"]].map(([v, label]) => (
          <button key={v} type="button" onClick={() => setTab(v)}
            style={{
              fontSize: 11, padding: "5px 12px", borderRadius: 5, cursor: "pointer",
              border: v === tab ? "1px solid var(--acc,#2563eb)" : "1px solid var(--line,#ddd)",
              background: v === tab ? "var(--acc,#2563eb)" : "transparent",
              color: v === tab ? "#fff" : "var(--ink-2,#555)",
              fontWeight: v === tab ? 600 : 400,
            }}>
            {label}
          </button>
        ))}
      </div>

      {tab === "triage" && <TriageQueueTab onResolved={bumpSummary} onNavigate={onNavigate} />}
      {tab === "analytics" && <ModelAnalyticsTab />}
      {tab === "drift" && <FeatureDriftTab />}
    </div>
  );
}

Object.assign(window, { ExceptionsScreen });
