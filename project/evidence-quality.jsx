/* ============================================================
   PBC / Workpaper Evidence Quality — log a piece of collected
   evidence against a control and see the deterministic quality
   checks (stale, unsigned, period mismatch) plus one advisory
   LLM content-plausibility check, computed server-side.

   Backed by evidence_quality_endpoints.py:
     POST /evidence-quality/items   log evidence, returns computed flags
     GET  /evidence-quality/items   filtered list
     GET  /evidence-quality/items/{id}
   ============================================================ */

const _EQ_SEVERITY_META = {
  HIGH:   { bg: "var(--red-soft)",   fg: "var(--red-ink)" },
  MEDIUM: { bg: "var(--amber-soft)", fg: "var(--amber-ink)" },
  LOW:    { bg: "var(--surface-2)",  fg: "var(--ink-3)" },
};

function EqFlagBadge({ flag }) {
  const meta = _EQ_SEVERITY_META[flag.severity] || _EQ_SEVERITY_META.LOW;
  return (
    <span title={flag.message} style={{
      fontSize: 9.5, fontWeight: 700, padding: "2px 7px", borderRadius: 999,
      background: meta.bg, color: meta.fg, whiteSpace: "nowrap",
    }}>
      {flag.code.replace(/_/g, " ")}
    </span>
  );
}

function EqLogForm({ onLogged }) {
  const [form, setForm] = React.useState({
    control_id: "", title: "", description: "", source_url: "",
    period_start: "", period_end: "", collected_date: "",
    has_signature: false, requires_signature: false,
    max_age_days: 90, control_description: "",
  });
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [lastResult, setLastResult] = React.useState(null);

  function set(field, value) { setForm(f => ({ ...f, [field]: value })); }

  async function submit() {
    if (!form.control_id.trim() || !form.title.trim()) {
      setError("Control ID and title are required.");
      return;
    }
    setSaving(true); setError(null); setLastResult(null);
    try {
      const payload = {
        control_id: form.control_id.trim(), title: form.title.trim(),
        description: form.description.trim() || null,
        source_url: form.source_url.trim() || null,
        period_start: form.period_start || null, period_end: form.period_end || null,
        collected_date: form.collected_date || null,
        has_signature: form.has_signature, requires_signature: form.requires_signature,
        max_age_days: Number(form.max_age_days) || 90,
        control_description: form.control_description.trim() || null,
      };
      const item = await window.MCP.logEvidence(payload);
      setLastResult(item);
      onLogged(item);
      setForm(f => ({ ...f, title: "", description: "", source_url: "", collected_date: "" }));
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  const inputStyle = {
    width: "100%", fontSize: 12, padding: "6px 8px",
    border: "1px solid var(--line)", borderRadius: 5, background: "var(--surface)", color: "var(--ink)",
  };
  const labelStyle = { fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600, marginBottom: 4, display: "block" };

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 16, background: "var(--surface)", marginBottom: 20 }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12 }}>Log evidence</div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <label style={labelStyle}>Control ID *</label>
          <input style={inputStyle} value={form.control_id} onChange={e => set("control_id", e.target.value)} placeholder="e.g. SOX-AP-04" />
        </div>
        <div>
          <label style={labelStyle}>Title *</label>
          <input style={inputStyle} value={form.title} onChange={e => set("title", e.target.value)} placeholder="e.g. Q2 access review sign-off" />
        </div>
        <div style={{ gridColumn: "1 / -1" }}>
          <label style={labelStyle}>Description <span style={{ textTransform: "none" }}>(what the evidence shows)</span></label>
          <textarea style={{ ...inputStyle, height: 50 }} value={form.description} onChange={e => set("description", e.target.value)}
            placeholder="e.g. Screenshot of the quarterly user access review approved by the control owner" />
        </div>
        <div>
          <label style={labelStyle}>Source URL</label>
          <input style={inputStyle} value={form.source_url} onChange={e => set("source_url", e.target.value)} placeholder="Link to the file/screenshot" />
        </div>
        <div>
          <label style={labelStyle}>Collected date</label>
          <input type="date" style={inputStyle} value={form.collected_date} onChange={e => set("collected_date", e.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Period start</label>
          <input type="date" style={inputStyle} value={form.period_start} onChange={e => set("period_start", e.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Period end</label>
          <input type="date" style={inputStyle} value={form.period_end} onChange={e => set("period_end", e.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Max age (days)</label>
          <input type="number" min="1" style={inputStyle} value={form.max_age_days} onChange={e => set("max_age_days", e.target.value)} />
        </div>
        <div style={{ gridColumn: "1 / -1" }}>
          <label style={labelStyle}>Control description <span style={{ textTransform: "none" }}>(optional — enables the AI plausibility check)</span></label>
          <input style={inputStyle} value={form.control_description} onChange={e => set("control_description", e.target.value)}
            placeholder="e.g. Quarterly user access review with control-owner sign-off" />
        </div>
      </div>

      <div style={{ display: "flex", gap: 16, marginTop: 14 }}>
        <label style={{ fontSize: 11.5, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input type="checkbox" checked={form.has_signature} onChange={e => set("has_signature", e.target.checked)} />
          Evidence is signed/approved
        </label>
        <label style={{ fontSize: 11.5, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input type="checkbox" checked={form.requires_signature} onChange={e => set("requires_signature", e.target.checked)} />
          Control requires a signature
        </label>
      </div>

      {error && <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginTop: 10 }}>{error}</div>}

      <div style={{ marginTop: 14 }}>
        <button className="btn btn-sm" disabled={saving} onClick={submit}>
          {saving ? "Logging…" : "Log evidence"}
        </button>
      </div>

      {lastResult && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
          <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginBottom: 6 }}>Result</div>
          {lastResult.quality_flags?.length ? (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {lastResult.quality_flags.map((f, i) => <EqFlagBadge key={i} flag={f} />)}
            </div>
          ) : (
            <div style={{ fontSize: 11.5, color: "var(--green-ink)" }}>No quality issues flagged.</div>
          )}
          {lastResult.content_check && (
            <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6 }}>
              AI plausibility: <b style={{ color: "var(--ink-2)" }}>{lastResult.content_check.verdict}</b>
              {lastResult.content_check.reason && <> — {lastResult.content_check.reason}</>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function EqItemRow({ item }) {
  const [expanded, setExpanded] = React.useState(false);
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "10px 12px", marginBottom: 8, background: "var(--surface)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, cursor: "pointer" }}
        onClick={() => setExpanded(e => !e)}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 600 }}>{item.title}</div>
          <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 2 }}>
            {item.control_id}{item.collected_date ? ` · collected ${item.collected_date.slice(0, 10)}` : ""}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end", maxWidth: 260 }}>
          {item.quality_flags?.length
            ? item.quality_flags.map((f, i) => <EqFlagBadge key={i} flag={f} />)
            : <span style={{ fontSize: 10, color: "var(--green-ink)" }}>Clean</span>}
        </div>
      </div>
      {expanded && (
        <div style={{ marginTop: 8, fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.5 }}>
          {item.description && <div style={{ marginBottom: 6 }}>{item.description}</div>}
          {item.source_url && (
            <div style={{ marginBottom: 6 }}>
              <a href={item.source_url} target="_blank" rel="noreferrer" style={{ color: "var(--acc-ink)" }}>{item.source_url}</a>
            </div>
          )}
          {(item.period_start || item.period_end) && (
            <div style={{ color: "var(--ink-4)", fontSize: 10.5 }}>
              Period: {item.period_start?.slice(0, 10) || "—"} to {item.period_end?.slice(0, 10) || "—"}
            </div>
          )}
          {item.quality_flags?.length > 0 && (
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              {item.quality_flags.map((f, i) => <li key={i} style={{ fontSize: 11, color: "var(--ink-3)" }}>{f.message}</li>)}
            </ul>
          )}
          {item.content_check && (
            <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6 }}>
              AI plausibility: <b>{item.content_check.verdict}</b>{item.content_check.reason && <> — {item.content_check.reason}</>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function EvidenceQualityScreen() {
  const [items, setItems] = React.useState(null);
  const [controlFilter, setControlFilter] = React.useState("");
  const [flaggedOnly, setFlaggedOnly] = React.useState(false);
  const [error, setError] = React.useState(null);

  const load = React.useCallback(() => {
    return window.MCP.listEvidenceQuality(controlFilter.trim() || null, flaggedOnly)
      .then(d => { setItems(d.items || []); setError(null); })
      .catch(e => setError(e.message || String(e)));
  }, [controlFilter, flaggedOnly]);

  React.useEffect(() => { load(); }, [load]);

  const rows = items || [];
  const flaggedCount = rows.filter(r => r.quality_flags?.length).length;

  return (
    <div className="scope-screen" data-screen-label="PBC Evidence Log">
      <div className="panel-head">
        <div>
          <div className="kicker">Audit &amp; Compliance · PBC Evidence Log</div>
          <div className="panel-title mt-8">PBC Evidence Log</div>
          <div className="panel-sub">
            Log evidence collected for a control test and see quality issues immediately —
            stale, unsigned, or collected outside the period it's supposed to support.
          </div>
        </div>
      </div>

      <EqLogForm onLogged={() => load()} />

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
        <input value={controlFilter} onChange={e => setControlFilter(e.target.value)}
          placeholder="Filter by control ID…"
          style={{ fontSize: 12, padding: "5px 8px", border: "1px solid var(--line)", borderRadius: 5, background: "var(--surface)", color: "var(--ink)", minWidth: 200 }} />
        <label style={{ fontSize: 11.5, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input type="checkbox" checked={flaggedOnly} onChange={e => setFlaggedOnly(e.target.checked)} />
          Flagged only
        </label>
        {items && (
          <span style={{ fontSize: 10.5, color: "var(--ink-4)" }}>
            {rows.length} item{rows.length === 1 ? "" : "s"}{flaggedCount > 0 ? ` · ${flaggedCount} with issues` : ""}
          </span>
        )}
      </div>

      {error && <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>}

      {items === null ? <Empty>Loading…</Empty> : !rows.length ? (
        <Empty icon="🗂️">No evidence logged yet — use the form above to log the first item.</Empty>
      ) : (
        rows.map(item => <EqItemRow key={item.id} item={item} />)
      )}
    </div>
  );
}

Object.assign(window, { EvidenceQualityScreen });
