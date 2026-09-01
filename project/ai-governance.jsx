/* ============================================================
   AI Governance — the auditor-maintained AI system register
   (observability.ai_system_registry) plus the behavioural audit
   that tests each register entry against evidence.

   The register answers "is human oversight DEFINED for this system"
   (AI-06) and "has its assessment EXPIRED" (AI-05) — both attestations
   a human ticks. The behavioural audit answers the question an
   attestation cannot: does that oversight actually WORK, and do the
   system's decisions show disparate impact (AI-09)?

   A system can be attested as fully governed here and still fail both,
   which is the entire reason this screen shows them side by side:
   the Attested column and the Evidence column are allowed to disagree,
   and when they do, that disagreement is the finding.

   Backed by ai_governance_endpoints.py:
     GET  /ai-governance                       register list
     PUT  /ai-governance                       upsert a system
     POST /ai-governance/behavioral-audit      deterministic analyzers
     POST /ai-governance/behavioral-audit/narrative   gated LLM summary
   ============================================================ */

function _aiGovBase() {
  return window.MCP_API_BASE || "/api/mcp";
}

// INSUFFICIENT_DATA deliberately does NOT reuse the CLEAR styling. "We could
// not evidence this control" and "this control passed" are different audit
// outcomes, and rendering them alike is exactly how an untested control gets
// mistaken for a working one.
const _VERDICT_META = {
  ESCALATE:          { label: "Escalate",          bg: "var(--red-soft)",   fg: "var(--red-ink)",   tone: "bad"  },
  MONITOR:           { label: "Monitor",           bg: "var(--amber-soft)", fg: "var(--amber-ink)", tone: "warn" },
  CLEAR:             { label: "Clear",             bg: "var(--green-soft)", fg: "var(--green-ink)", tone: "good" },
  INSUFFICIENT_DATA: { label: "Not evidenced",     bg: "var(--surface-2)",  fg: "var(--ink-3)",     tone: "neutral" },
};

const _VERDICT_ORDER = ["ESCALATE", "MONITOR", "INSUFFICIENT_DATA", "CLEAR"];

const _SAMPLE_CSV = `event_type,decision,seconds_to_decide,subject_group,outcome
human_review,approved,0.4,,
human_review,approved,0.6,,
human_review,approved,0.3,,
human_review,rejected,45,,
ai_decision,,,Region A,adverse
ai_decision,,,Region A,favourable
ai_decision,,,Region B,favourable`;

function VerdictPill({ verdict, size = "sm" }) {
  const meta = _VERDICT_META[verdict] || _VERDICT_META.INSUFFICIENT_DATA;
  return (
    <span style={{
      fontSize: size === "lg" ? 12 : 9.5, fontWeight: 700, padding: size === "lg" ? "4px 12px" : "1px 7px",
      borderRadius: 999, background: meta.bg, color: meta.fg, whiteSpace: "nowrap",
    }}>
      {meta.label}
    </span>
  );
}

function GovTile({ label, value, sub, tone = "neutral" }) {
  const toneColor = {
    neutral: "var(--ink)", good: "var(--green-ink)",
    warn: "var(--amber-ink)", bad: "var(--red-ink)",
  }[tone] || "var(--ink)";
  return (
    <div style={{
      flex: "1 1 160px", minWidth: 160, border: "1px solid var(--line)", borderRadius: 8,
      padding: "12px 14px", background: "var(--surface)",
    }}>
      <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>
        {label}
      </div>
      <div className="mono" style={{ fontSize: 22, fontWeight: 700, color: toneColor, marginTop: 4 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

/* ── CSV parsing ───────────────────────────────────────────────────────────
   An auditor's evidence arrives as an export, not as hand-typed records, so
   paste/upload is the primary input. Header-driven rather than positional so
   a column order change in the source system does not silently misread every
   row into the wrong field. */
function _parseEventsCsv(text) {
  const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  if (!lines.length) return { events: [], errors: ["File is empty."] };

  const header = lines[0].split(",").map(h => h.trim().toLowerCase());
  if (!header.includes("event_type")) {
    return { events: [], errors: ['Missing required "event_type" column in the header row.'] };
  }

  const events = [];
  const errors = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split(",").map(c => c.trim());
    const row = {};
    header.forEach((h, idx) => { if (cells[idx] !== undefined && cells[idx] !== "") row[h] = cells[idx]; });
    if (!row.event_type) { errors.push(`Row ${i + 1}: no event_type — skipped.`); continue; }

    if (row.seconds_to_decide !== undefined) {
      const n = Number(row.seconds_to_decide);
      if (Number.isNaN(n)) {
        errors.push(`Row ${i + 1}: seconds_to_decide "${row.seconds_to_decide}" is not a number — dropped that field.`);
        delete row.seconds_to_decide;
      } else {
        row.seconds_to_decide = n;
      }
    }
    events.push(row);
  }
  return { events, errors };
}

function EvidenceTable({ evidence }) {
  const entries = Object.entries(evidence || {}).filter(([k]) => k !== "by_group");
  const byGroup = evidence?.by_group;
  return (
    <div style={{ marginTop: 10 }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k} style={{ borderBottom: "1px solid var(--line)" }}>
              <td style={{ padding: "4px 8px", color: "var(--ink-4)", width: "45%", verticalAlign: "top" }}>{k}</td>
              <td className="mono" style={{ padding: "4px 8px", color: "var(--ink-2)", wordBreak: "break-word" }}>
                {typeof v === "object" ? JSON.stringify(v) : String(v)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {byGroup && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600, marginBottom: 4 }}>
            Selection rate by group
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
            <thead>
              <tr style={{ color: "var(--ink-4)", fontSize: 9.5, textTransform: "uppercase" }}>
                <th style={{ textAlign: "left", padding: "3px 8px" }}>Group</th>
                <th style={{ textAlign: "right", padding: "3px 8px" }}>Decisions</th>
                <th style={{ textAlign: "right", padding: "3px 8px" }}>Favourable</th>
                <th style={{ textAlign: "right", padding: "3px 8px" }}>Rate</th>
                <th style={{ textAlign: "left", padding: "3px 8px" }}>Assessed</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(byGroup).map(([g, s]) => (
                <tr key={g} style={{ borderBottom: "1px solid var(--line)", opacity: s.assessed ? 1 : 0.55 }}>
                  <td style={{ padding: "3px 8px" }}>{g}</td>
                  <td className="mono" style={{ padding: "3px 8px", textAlign: "right" }}>{s.decisions}</td>
                  <td className="mono" style={{ padding: "3px 8px", textAlign: "right" }}>{s.favourable}</td>
                  <td className="mono" style={{ padding: "3px 8px", textAlign: "right" }}>{(s.selection_rate * 100).toFixed(1)}%</td>
                  <td style={{ padding: "3px 8px", fontSize: 10, color: s.assessed ? "var(--green-ink)" : "var(--ink-4)" }}>
                    {s.assessed ? "yes" : "below minimum"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function EvaluationCard({ evaluation }) {
  const [open, setOpen] = React.useState(false);
  const meta = _VERDICT_META[evaluation.verdict] || _VERDICT_META.INSUFFICIENT_DATA;
  return (
    <div style={{
      border: "1px solid var(--line)", borderLeft: `3px solid ${meta.fg}`,
      borderRadius: 6, padding: "12px 14px", background: "var(--surface)", marginBottom: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <VerdictPill verdict={evaluation.verdict} />
        <span style={{ fontWeight: 600, fontSize: 12.5 }}>{evaluation.agent_name}</span>
        {evaluation.evidence?.control_ref && (
          <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)" }}>
            {evaluation.evidence.control_ref}
          </span>
        )}
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--ink-4)" }} className="mono">
          confidence {(evaluation.confidence * 100).toFixed(0)}%
        </span>
      </div>

      <p style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 8, lineHeight: 1.55 }}>
        {evaluation.reasoning}
      </p>

      <button className="btn btn-sm" style={{ fontSize: 10, padding: "2px 8px", marginTop: 8 }}
        onClick={() => setOpen(o => !o)}>
        {open ? "Hide evidence" : "Show evidence"}
      </button>
      {open && <EvidenceTable evidence={evaluation.evidence} />}
    </div>
  );
}

function BehavioralAuditPanel({ system, onClose, onAudited }) {
  const [csvText, setCsvText] = React.useState("");
  const [parseErrors, setParseErrors] = React.useState([]);
  const [running, setRunning] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [report, setReport] = React.useState(null);
  const [narrative, setNarrative] = React.useState(null);
  const [narrating, setNarrating] = React.useState(false);

  const parsed = React.useMemo(() => (csvText.trim() ? _parseEventsCsv(csvText) : { events: [], errors: [] }), [csvText]);

  function handleFile(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setCsvText(String(reader.result || ""));
    reader.readAsText(file);
  }

  async function runAudit() {
    setError(null); setReport(null); setNarrative(null);
    const { events, errors } = _parseEventsCsv(csvText);
    setParseErrors(errors);
    if (!events.length) { setError("No usable events found in the input."); return; }

    setRunning(true);
    try {
      const res = await fetch(`${_aiGovBase()}/ai-governance/behavioral-audit`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ system_name: system.system_name, events }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `Audit failed (${res.status})`);
      setReport(body);
      onAudited && onAudited(system.system_name, body);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  async function generateNarrative() {
    setNarrating(true);
    try {
      const res = await fetch(`${_aiGovBase()}/ai-governance/behavioral-audit/narrative`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ system_name: system.system_name, report }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `Narrative failed (${res.status})`);
      setNarrative(body);
    } catch (e) {
      setError(e.message);
    } finally {
      setNarrating(false);
    }
  }

  // Worst-first. An auditor reads the top of this list and stops; burying an
  // ESCALATE under a CLEAR because of analyzer registration order would be a
  // presentation bug with audit consequences.
  const ordered = report
    ? [...report.evaluations].sort((a, b) => _VERDICT_ORDER.indexOf(a.verdict) - _VERDICT_ORDER.indexOf(b.verdict))
    : [];

  const attestedButFailing = report
    && system.human_oversight_defined
    && ordered.some(e => e.evidence?.control_ref === "AI-06" && e.verdict === "ESCALATE");

  return (
    <div style={{
      border: "1px solid var(--line)", borderRadius: 8, padding: 16,
      background: "var(--surface)", marginBottom: 20,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>Behavioural audit — {system.system_name}</div>
        <button className="btn btn-sm" style={{ marginLeft: "auto", fontSize: 10.5 }} onClick={onClose}>Close</button>
      </div>
      <div style={{ fontSize: 11, color: "var(--ink-4)", marginBottom: 14 }}>
        Upload this system&apos;s own review and decision logs. The analysis is deterministic —
        the same file always produces the same verdict, so a finding here can be re-run and
        reproduced by anyone reviewing it.
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
        <input type="file" accept=".csv,text/csv" onChange={handleFile} style={{ fontSize: 11 }} />
        <button className="btn btn-sm" style={{ fontSize: 10.5 }} onClick={() => setCsvText(_SAMPLE_CSV)}>
          Load sample
        </button>
        {csvText && (
          <button className="btn btn-sm" style={{ fontSize: 10.5 }} onClick={() => { setCsvText(""); setReport(null); setNarrative(null); }}>
            Clear
          </button>
        )}
      </div>

      <textarea
        className="code-input mono"
        style={{ width: "100%", height: 120, fontSize: 11 }}
        placeholder="Paste CSV here, or use the file picker above."
        value={csvText}
        onChange={e => setCsvText(e.target.value)}
        spellCheck={false}
      />

      <details style={{ marginTop: 6 }}>
        <summary style={{ fontSize: 10.5, color: "var(--ink-4)", cursor: "pointer" }}>
          Expected columns
        </summary>
        <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6, lineHeight: 1.6 }}>
          One row per event, with an <code>event_type</code> column. Other columns are read by
          name, so extra columns are ignored rather than misread.
          <ul style={{ marginTop: 6, paddingLeft: 18 }}>
            <li>
              <code>human_review</code> — needs <code>decision</code>
              (<code>approved</code>/<code>rejected</code>) and <code>seconds_to_decide</code>.
              Without the timing column, oversight cannot be evidenced at all: approval rate on
              its own cannot tell a careful reviewer who agrees apart from one who is not reading.
            </li>
            <li>
              <code>ai_decision</code> — needs <code>subject_group</code> and <code>outcome</code>
              (<code>favourable</code>/<code>adverse</code>).
            </li>
          </ul>
        </div>
      </details>

      {csvText.trim() && (
        <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 6 }}>
          {parsed.events.length} event(s) parsed
          {parsed.errors.length > 0 && ` · ${parsed.errors.length} row issue(s)`}
        </div>
      )}

      <button className="btn btn-acc" style={{ marginTop: 12, fontSize: 12 }}
        disabled={running || !parsed.events.length} onClick={runAudit}>
        {running ? "Running…" : "Run behavioural audit"}
      </button>

      {parseErrors.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 10.5, color: "var(--amber-ink)" }}>
          {parseErrors.slice(0, 5).map((e, i) => <div key={i}>{e}</div>)}
          {parseErrors.length > 5 && <div>…and {parseErrors.length - 5} more.</div>}
        </div>
      )}

      {error && (
        <div className="mono" style={{ marginTop: 10, fontSize: 11, color: "var(--red-ink)" }}>{error}</div>
      )}

      {report && (
        <div style={{ marginTop: 18 }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
            border: "1px solid var(--line)", borderRadius: 6, background: "var(--surface-2)", marginBottom: 14,
          }}>
            <VerdictPill verdict={report.overall_verdict} size="lg" />
            <div style={{ fontSize: 11.5, color: "var(--ink-2)" }}>
              {report.events_examined} events examined · {report.evaluations.length} checks
              {report.requires_human_review && " · requires human review"}
            </div>
          </div>

          {attestedButFailing && (
            <div style={{
              padding: "10px 14px", borderRadius: 6, marginBottom: 14,
              border: "1px solid var(--red-ink)", background: "var(--red-soft)", color: "var(--red-ink)",
              fontSize: 11.5, lineHeight: 1.5,
            }}>
              <strong>Attestation contradicted by evidence.</strong> This system is recorded on the
              register as having human oversight defined (AI-06), but its own review logs show that
              oversight is not functioning as a control. The register entry should not be relied on
              until this is resolved.
            </div>
          )}

          {ordered.map((ev, i) => <EvaluationCard key={i} evaluation={ev} />)}

          <div style={{ marginTop: 12 }}>
            <button className="btn btn-sm" style={{ fontSize: 11 }} disabled={narrating} onClick={generateNarrative}>
              {narrating ? "Generating…" : "Generate audit-committee summary"}
            </button>
            <span style={{ fontSize: 10, color: "var(--ink-4)", marginLeft: 8 }}>
              Optional AI narrative over the findings above. Every generation goes to the AI
              Narrative Review queue before it can be relied on.
            </span>
          </div>

          {narrative && (
            <div style={{
              marginTop: 12, border: "1px solid var(--line)", borderRadius: 6,
              padding: "12px 14px", background: "var(--surface-2)",
            }}>
              {narrative._review && narrative._review.status !== "reviewed" && (
                <div style={{
                  fontSize: 10, fontWeight: 700, color: "var(--amber-ink)",
                  background: "var(--amber-soft)", display: "inline-block",
                  padding: "2px 8px", borderRadius: 999, marginBottom: 8,
                }}>
                  Pending Review
                </div>
              )}
              <div style={{ fontWeight: 700, fontSize: 13 }}>{narrative.headline}</div>
              <p style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 6, lineHeight: 1.55 }}>{narrative.summary}</p>
              {narrative.control_reliance_impact && (
                <p style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 8, lineHeight: 1.55 }}>
                  <strong>Control reliance: </strong>{narrative.control_reliance_impact}
                </p>
              )}
              {Array.isArray(narrative.recommended_actions) && narrative.recommended_actions.length > 0 && (
                <ul style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 8, paddingLeft: 18, lineHeight: 1.6 }}>
                  {narrative.recommended_actions.map((a, i) => <li key={i}>{a}</li>)}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const _RISK_TIERS = ["LOW", "MEDIUM", "HIGH"];

// Keyed by system_name (observability.ai_system_registry's own upsert key —
// see db.upsert_ai_system's ON CONFLICT (system_name)), so editing an
// existing row and changing its name would silently create a SECOND row
// rather than rename the first. Simplest safe behavior: lock the name field
// once editing, same reasoning approval_tasks' item_ref is never mutable
// once a task exists.
function AiSystemForm({ initial, onCancel, onSaved }) {
  const isEdit = !!initial?.id;
  const [form, setForm] = React.useState({
    system_name: initial?.system_name || "",
    vendor: initial?.vendor || "",
    business_owner: initial?.business_owner || "",
    risk_tier: initial?.risk_tier || "MEDIUM",
    requires_human_oversight: initial?.requires_human_oversight || false,
    human_oversight_defined: initial?.human_oversight_defined || false,
    last_assessment_date: initial?.last_assessment_date ? initial.last_assessment_date.slice(0, 10) : "",
    assessment_expires_at: initial?.assessment_expires_at ? initial.assessment_expires_at.slice(0, 10) : "",
  });
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState(null);

  function set(field, value) { setForm(f => ({ ...f, [field]: value })); }

  async function handleSave() {
    if (!form.system_name.trim()) { setError("System name is required."); return; }
    setSaving(true); setError(null);
    try {
      const res = await fetch(`${_aiGovBase()}/ai-governance`, {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          system_name: form.system_name.trim(),
          vendor: form.vendor.trim() || null,
          business_owner: form.business_owner.trim() || null,
          risk_tier: form.risk_tier,
          requires_human_oversight: form.requires_human_oversight,
          human_oversight_defined: form.human_oversight_defined,
          last_assessment_date: form.last_assessment_date || null,
          assessment_expires_at: form.assessment_expires_at || null,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `Save failed (${res.status})`);
      onSaved();
    } catch (e) {
      setError(e.message);
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
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12 }}>
        {isEdit ? `Edit — ${initial.system_name}` : "Register a new AI system"}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <label style={labelStyle}>System name{!isEdit && " *"}</label>
          <input style={inputStyle} value={form.system_name} disabled={isEdit}
            onChange={e => set("system_name", e.target.value)} placeholder="e.g. Vendor Risk Scoring Model" />
        </div>
        <div>
          <label style={labelStyle}>Vendor</label>
          <input style={inputStyle} value={form.vendor} onChange={e => set("vendor", e.target.value)} placeholder="e.g. Internal, OpenAI, Acme AI" />
        </div>
        <div>
          <label style={labelStyle}>Business owner</label>
          <input style={inputStyle} value={form.business_owner} onChange={e => set("business_owner", e.target.value)} placeholder="e.g. Jane Lee, Procurement" />
        </div>
        <div>
          <label style={labelStyle}>Risk tier</label>
          <select style={inputStyle} value={form.risk_tier} onChange={e => set("risk_tier", e.target.value)}>
            {_RISK_TIERS.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div>
          <label style={labelStyle}>Last assessment date</label>
          <input type="date" style={inputStyle} value={form.last_assessment_date} onChange={e => set("last_assessment_date", e.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Assessment expires</label>
          <input type="date" style={inputStyle} value={form.assessment_expires_at} onChange={e => set("assessment_expires_at", e.target.value)} />
        </div>
      </div>

      <div style={{ display: "flex", gap: 16, marginTop: 14, flexWrap: "wrap" }}>
        <label style={{ fontSize: 11.5, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input type="checkbox" checked={form.requires_human_oversight}
            onChange={e => set("requires_human_oversight", e.target.checked)} />
          Requires human oversight (AI-06)
        </label>
        <label style={{ fontSize: 11.5, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input type="checkbox" checked={form.human_oversight_defined}
            onChange={e => set("human_oversight_defined", e.target.checked)} />
          Human oversight defined
        </label>
      </div>
      {form.requires_human_oversight && !form.human_oversight_defined && (
        <div style={{ fontSize: 10.5, color: "var(--amber-ink)", marginTop: 6 }}>
          Saving with oversight required but not defined raises an AI-06 finding immediately — this is
          the intended attestation gap detection, not an error.
        </div>
      )}

      {error && <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginTop: 10 }}>{error}</div>}

      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <button className="btn btn-sm" disabled={saving} onClick={handleSave}>
          {saving ? "Saving…" : isEdit ? "Save changes" : "Register system"}
        </button>
        <button className="btn btn-sm btn-ghost" disabled={saving} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function AiGovernanceScreen() {
  const [systems, setSystems] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [auditing, setAuditing] = React.useState(null);
  const [lastVerdicts, setLastVerdicts] = React.useState({});
  // null = form hidden; {} = registering a new system; {...row} = editing one.
  const [formSystem, setFormSystem] = React.useState(null);
  // Passively-detected candidates (mcp_governance.py's shadow-AI keyword
  // match, e.g. an IAM entitlement named "OPENAI_ENTERPRISE_ACCESS") —
  // never auto-promoted into `systems` above; a human registers or
  // dismisses each one below.
  const [candidates, setCandidates] = React.useState(null);
  const [candidateBusy, setCandidateBusy] = React.useState(null);

  const load = React.useCallback(() => {
    return fetch(`${_aiGovBase()}/ai-governance`, { credentials: "include" })
      .then(res => {
        if (!res.ok) throw new Error(`Failed to load AI system register (${res.status})`);
        return res.json();
      })
      .then(d => { setSystems(d.systems || []); setError(null); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const loadCandidates = React.useCallback(() => {
    return fetch(`${_aiGovBase()}/ai-governance/shadow-candidates`, { credentials: "include" })
      .then(res => (res.ok ? res.json() : { candidates: [] }))
      .then(d => setCandidates(d.candidates || []))
      .catch(() => setCandidates([]));
  }, []);

  React.useEffect(() => { load(); loadCandidates(); }, [load, loadCandidates]);

  async function dismissCandidate(id) {
    setCandidateBusy(id);
    try {
      await fetch(`${_aiGovBase()}/ai-governance/shadow-candidates/${id}/dismiss`, {
        method: "POST", credentials: "include",
      });
      await loadCandidates();
    } finally {
      setCandidateBusy(null);
    }
  }

  const rows = systems || [];
  const pendingCandidates = candidates || [];
  const expired = rows.filter(r => r.status === "EXPIRED").length;
  const oversightGaps = rows.filter(r => r.requires_human_oversight && !r.human_oversight_defined).length;
  const contradicted = Object.values(lastVerdicts).filter(v => v === "ESCALATE").length;

  return (
    <div className="scope-screen" data-screen-label="AI Governance">
      <div className="panel-head">
        <div>
          <div className="kicker">Audit &amp; Compliance · AI Governance</div>
          <div className="panel-title mt-8">AI Governance</div>
          <div className="panel-sub">
            The register of AI systems this company operates, and the evidence behind each one.
            The register records what was <em>attested</em> — that a human review step exists, that
            an assessment is current. The behavioural audit tests those attestations against the
            system&apos;s own logs. Where the two disagree, the disagreement is the finding.
          </div>
        </div>
        {!formSystem && (
          <button className="btn btn-sm" onClick={() => setFormSystem({})}>+ Add system</button>
        )}
      </div>

      {error && (
        <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>
      )}

      {loading && !systems ? <Empty>Loading…</Empty> : (
        <>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 20 }}>
            <GovTile label="Systems registered" value={rows.length} sub="Manual attestation register" />
            <GovTile label="Detected, pending review" value={pendingCandidates.length}
              tone={pendingCandidates.length > 0 ? "warn" : "neutral"}
              sub="Passively detected, not yet registered" />
            <GovTile label="Oversight gaps" value={oversightGaps} tone={oversightGaps > 0 ? "bad" : "good"}
              sub={oversightGaps > 0 ? "Required but not defined (AI-06)" : "All required oversight defined"} />
            <GovTile label="Expired assessments" value={expired} tone={expired > 0 ? "warn" : "good"}
              sub={expired > 0 ? "Control reliance basis lapsed (AI-05)" : "All assessments current"} />
            <GovTile label="Contradicted by evidence" value={contradicted} tone={contradicted > 0 ? "bad" : "neutral"}
              sub="Attested as governed, failed its audit" />
          </div>

          {pendingCandidates.length > 0 && (
            <div style={{ border: "1px solid var(--amber-ink, var(--line))", borderRadius: 6, marginBottom: 20, overflow: "hidden" }}>
              <div style={{ padding: "8px 12px", background: "var(--amber-soft)", fontSize: 11, fontWeight: 600, color: "var(--amber-ink)" }}>
                Detected AI tools — passively observed, not yet registered
              </div>
              <div style={{ padding: "6px 12px 2px", fontSize: 10.5, color: "var(--ink-3)" }}>
                Surfaced from IAM entitlement activity that named an AI vendor/tool (e.g. an access request for
                &quot;OPENAI_ENTERPRISE_ACCESS&quot;) — never auto-registered. Register it to record a real attestation,
                or dismiss it if it's not actually in use.
              </div>
              <div style={{ padding: "4px 12px 10px" }}>
                {pendingCandidates.map(c => (
                  <div key={c.id} style={{
                    display: "flex", alignItems: "center", gap: 10, padding: "8px 0",
                    borderTop: "1px solid var(--line)",
                  }}>
                    <div style={{ flex: "1 1 200px", minWidth: 0 }}>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ink)" }}>{c.detected_name}</div>
                      <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                        {c.source_detail || "—"} · {c.occurrence_count}× · last seen {new Date(c.last_seen_at).toLocaleDateString()}
                        {c.last_actor ? ` · ${c.last_actor}` : ""}
                      </div>
                    </div>
                    <button className="btn btn-sm" disabled={candidateBusy === c.id}
                      onClick={() => setFormSystem({ system_name: c.detected_name, vendor: c.detected_name })}>
                      Register
                    </button>
                    <button className="btn btn-sm" disabled={candidateBusy === c.id}
                      style={{ color: "var(--ink-3)" }}
                      onClick={() => dismissCandidate(c.id)}>
                      Dismiss
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {formSystem && (
            <AiSystemForm
              initial={formSystem}
              onCancel={() => setFormSystem(null)}
              onSaved={() => { setFormSystem(null); load(); loadCandidates(); }}
            />
          )}

          {auditing && (
            <BehavioralAuditPanel
              system={auditing}
              onClose={() => setAuditing(null)}
              onAudited={(name, report) => setLastVerdicts(v => ({ ...v, [name]: report.overall_verdict }))}
            />
          )}

          {!rows.length ? (
            <Empty icon="🗂️">
              No AI systems registered yet. The register itself is a manual attestation — passive
              detection above will surface candidates here as IAM entitlement activity naming an AI
              vendor/tool is observed, but only a human decision (Register or Dismiss) ever creates a
              row. Use &quot;+ Add system&quot; to record one directly in the meantime.
            </Empty>
          ) : (
            <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
              <div style={{
                display: "grid", gridTemplateColumns: "1.6fr 1fr 0.8fr 1fr 1fr 1fr 0.9fr",
                gap: 10, padding: "6px 12px", fontSize: 10, color: "var(--ink-4)",
                letterSpacing: "0.05em", textTransform: "uppercase", fontWeight: 600,
                borderBottom: "1px solid var(--line)",
              }}>
                <div>System</div><div>Vendor</div><div>Tier</div><div>Owner</div>
                <div>Attested oversight</div><div>Evidence</div><div />
              </div>

              {rows.map(row => {
                const gap = row.requires_human_oversight && !row.human_oversight_defined;
                const verdict = lastVerdicts[row.system_name];
                return (
                  <div key={row.id} style={{
                    display: "grid", gridTemplateColumns: "1.6fr 1fr 0.8fr 1fr 1fr 1fr 0.9fr",
                    alignItems: "center", gap: 10, padding: "9px 12px",
                    borderBottom: "1px solid var(--line)", fontSize: 12,
                  }}>
                    <div>
                      <div style={{ fontWeight: 600 }}>{row.system_name}</div>
                      {row.status === "EXPIRED" && (
                        <div style={{ fontSize: 9.5, color: "var(--amber-ink)", marginTop: 1 }}>
                          Assessment expired {row.assessment_expires_at}
                        </div>
                      )}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--ink-3)" }}>{row.vendor || "—"}</div>
                    <div style={{ fontSize: 11 }}>{row.risk_tier}</div>
                    <div style={{ fontSize: 11, color: "var(--ink-3)" }}>{row.business_owner || "Unassigned"}</div>
                    <div style={{ fontSize: 10.5, color: gap ? "var(--red-ink)" : "var(--ink-3)" }}>
                      {!row.requires_human_oversight
                        ? "Not required"
                        : gap ? "Required, not defined" : "Defined"}
                    </div>
                    <div>
                      {verdict
                        ? <VerdictPill verdict={verdict} />
                        : <span style={{ fontSize: 10, color: "var(--ink-4)" }}>Not audited</span>}
                    </div>
                    <div style={{ textAlign: "right", display: "flex", gap: 6, justifyContent: "flex-end" }}>
                      <button className="btn btn-sm" style={{ fontSize: 10.5 }}
                        onClick={() => setFormSystem(row)}>
                        Edit
                      </button>
                      <button className="btn btn-sm" style={{ fontSize: 10.5 }}
                        onClick={() => setAuditing(row)}>
                        Audit
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <p style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 10 }}>
            An audit verdict shown here reflects the batch most recently uploaded in this session.
            Every non-clear result is also ingested as a governed event, so it appears in
            Continuous Watch and the Approval Inbox on the same path as any other finding.
          </p>
        </>
      )}
    </div>
  );
}

Object.assign(window, { AiGovernanceScreen });
