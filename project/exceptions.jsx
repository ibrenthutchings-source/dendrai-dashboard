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

function TriageQueueRow({ row, onResolved }) {
  const [expanded, setExpanded] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState(null);

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

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "10px 12px", marginBottom: 8, background: "var(--surface)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, cursor: "pointer" }}
        onClick={() => setExpanded(e => !e)}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 11.5, fontWeight: 600 }}>
            {row.control_id} <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>· {row.system_source}{row.process ? ` · ${row.process}` : ""}</span>
          </div>
          <div style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 2 }}>
            {row.event_timestamp ? new Date(row.event_timestamp).toLocaleString() : "—"}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 150, flexShrink: 0 }}>
          <ExcScoreBar label="Anomaly" value={row.anomaly_score} />
          <ExcScoreBar label="Uncertainty" value={row.uncertainty_score} />
        </div>
      </div>
      {expanded && (
        <>
          <div style={{ marginTop: 10 }}>
            <ExcFeatureChips features={row.point_in_time_features} />
          </div>
          {error && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 8 }}>{error}</div>}
          <TriageForm onSubmit={handleSubmit} submitting={submitting} />
        </>
      )}
    </div>
  );
}

function TriageQueueTab({ onResolved }) {
  const [rows, setRows] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);

  const load = React.useCallback(() => {
    setLoading(true);
    return window.MCP.exceptionsPending({ limit: 100 })
      .then(d => { setRows(d.rows || []); setError(null); })
      .catch(e => setError(e.message || String(e)))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => { load(); }, [load]);

  function handleResolved(eventId) {
    setRows(rs => rs.filter(r => r.event_id !== eventId));
    onResolved && onResolved();
  }

  if (loading && !rows.length) return <Empty>Loading pending exceptions…</Empty>;
  if (error) return <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)" }}>{error}</div>;
  if (!rows.length) {
    return (
      <Empty icon="✓">
        No exceptions awaiting review right now. New ones appear here as connector events cross the
        anomaly/uncertainty threshold — check back after Continuous Watch has ingested some activity.
      </Empty>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div className="kicker">{rows.length} awaiting review — highest uncertainty first</div>
        <button className="btn btn-sm" onClick={load}>Refresh</button>
      </div>
      {rows.map(row => <TriageQueueRow key={row.event_id} row={row} onResolved={handleResolved} />)}
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

  return (
    <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
      <div style={{ flex: 1, minWidth: 280 }}>
        <div className="kicker" style={{ marginBottom: 8 }}>Resolution mix (all-time)</div>
        <ResolutionMixBars mix={summary?.resolution_mix || {}} />
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
        <div className="kicker">Development · Continuous Control Monitoring</div>
        <div className="panel-title mt-8">Exception Management</div>
        <div className="panel-sub">
          Connector events — from real systems and the synthetic transaction simulator alike — are scored for
          anomaly and uncertainty as they arrive. Anything that crosses the review threshold lands here for a human
          auditor to resolve, as a second, model-scored lens alongside Policy-as-Code's rule-based violations on
          Continuous Watch.
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

      {tab === "triage" && <TriageQueueTab onResolved={bumpSummary} />}
      {tab === "analytics" && <ModelAnalyticsTab />}
      {tab === "drift" && <FeatureDriftTab />}
    </div>
  );
}

Object.assign(window, { ExceptionsScreen });
