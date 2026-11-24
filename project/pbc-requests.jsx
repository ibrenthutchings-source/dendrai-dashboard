/* ============================================================
   PBC Requests — Request-as-Code for document requests.

   Backed by pbc_request_endpoints.py (/pbc-requests):
     PUT  /requirements          declare what evidence a control needs
     POST /generate              build an engagement's request list
     GET  /  and  /summary       requests (with derived "overdue") and the status board
     POST /{id}/receive          link logged evidence; the server runs the checks
     POST /{id}/review           accept or reject (reviewer must not be the submitter)

   Evidence itself is logged on the PBC Evidence Log (/evidence-quality); here a
   request is answered by linking one of those items. The server enforces every rule
   (reviewer independence, a note on rejection, state changes); the buttons below only
   hide actions that cannot work.
   ============================================================ */

const _PR = "/api/pbc-requests";

async function _prJson(path, opts = {}) {
  const res = await fetch(_PR + path, {
    credentials: "include", ...opts,
    headers: { ...(opts.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}
const _prSend = (method, body) => ({ method, body: JSON.stringify(body) });

// ---------- pure helpers ----------

const PR_STATUS_META = {
  requested: { bg: "var(--surface-2)", fg: "var(--ink-3)", label: "Requested" },
  received:  { bg: "var(--amber-soft)", fg: "var(--amber-ink)", label: "Awaiting review" },
  accepted:  { bg: "var(--green-soft)", fg: "var(--green-ink)", label: "Accepted" },
  rejected:  { bg: "var(--red-soft)", fg: "var(--red-ink)", label: "Rejected" },
  overdue:   { bg: "var(--red-soft)", fg: "var(--red-ink)", label: "Overdue" },
};

/** Which actions make sense for a request, given who is looking. The server still decides. */
function prActions(request, username) {
  const s = request.status;
  const me = (username || "").toLowerCase();
  const submitter = (request.submitted_by || "").toLowerCase();
  return {
    canReceive: s === "requested" || s === "rejected",
    canReview: s === "received" && !(me && submitter && me === submitter),
    reviewBlockedReason: s === "received" && me && submitter && me === submitter
      ? "You submitted this evidence, so someone else must review it." : null,
  };
}

/** Distinct engagements, newest-looking first (alphabetical descending is good enough for FY labels). */
function prEngagements(requests) {
  return [...new Set(requests.map(r => r.engagement))].sort().reverse();
}

function prDueLabel(dueDate, today = new Date()) {
  const d = new Date(String(dueDate).slice(0, 10) + "T00:00:00");
  const t = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const days = Math.round((d - t) / 86400000);
  if (days === 0) return "due today";
  return days > 0 ? `due in ${days}d` : `${-days}d late`;
}

// ---------- UI ----------

const _prInput = { fontSize: 12, padding: "5px 8px", border: "1px solid var(--line)", borderRadius: 5, background: "var(--surface)", color: "var(--ink)", width: "100%", boxSizing: "border-box" };
const _prLabel = { fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600, marginBottom: 3, display: "block" };
const _prCard = { border: "1px solid var(--line)", borderRadius: 8, padding: 14, background: "var(--surface)", marginBottom: 12 };
const _prBtn = (primary) => ({
  fontSize: 12, padding: "5px 12px", borderRadius: 5, cursor: "pointer", fontWeight: 600,
  border: primary ? "1px solid var(--ink)" : "1px solid var(--line)",
  background: primary ? "var(--ink)" : "var(--surface)", color: primary ? "var(--surface)" : "var(--ink)",
});
const _PrField = ({ label, children, style }) => <div style={style}><label style={_prLabel}>{label}</label>{children}</div>;

function PrBadge({ status }) {
  const m = PR_STATUS_META[status] || PR_STATUS_META.requested;
  return <span style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 7px", borderRadius: 999, background: m.bg, color: m.fg, whiteSpace: "nowrap", textTransform: "uppercase" }}>{m.label}</span>;
}

function PrFinding({ f }) {
  const hi = f.severity === "HIGH";
  return <span title={f.message} style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 6px", borderRadius: 999, marginRight: 4,
    background: hi ? "var(--red-soft)" : "var(--amber-soft)", color: hi ? "var(--red-ink)" : "var(--amber-ink)" }}>{f.code.replace(/_/g, " ")}</span>;
}

function PrBoard({ summary }) {
  if (!summary) return null;
  const cells = [["requested", "Requested"], ["overdue", "Overdue"], ["received", "Awaiting review"], ["rejected", "Rejected"], ["accepted", "Accepted"]];
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
      {cells.map(([k, label]) => (
        <div key={k} style={{ ..._prCard, marginBottom: 0, padding: "10px 16px", minWidth: 110 }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: k === "overdue" && summary[k] ? "var(--red-ink)" : "var(--ink)" }}>{summary[k] ?? 0}</div>
          <div style={{ fontSize: 10.5, color: "var(--ink-4)", textTransform: "uppercase" }}>{label}</div>
        </div>
      ))}
      <div style={{ ..._prCard, marginBottom: 0, padding: "10px 16px", minWidth: 110 }}>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{summary.percent_accepted ?? 0}%</div>
        <div style={{ fontSize: 10.5, color: "var(--ink-4)", textTransform: "uppercase" }}>Complete</div>
      </div>
    </div>
  );
}

function PrRequestRow({ r, username, onChanged }) {
  const [mode, setMode] = React.useState(null);       // "receive" | "review"
  const [evidence, setEvidence] = React.useState(null);
  const [evidenceId, setEvidenceId] = React.useState("");
  const [note, setNote] = React.useState("");
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const a = prActions(r, username);

  const openReceive = async () => {
    setMode("receive"); setErr(null);
    try {
      const d = await window.MCP.listEvidenceQuality(r.control_id, false);
      setEvidence(d.items || []);
    } catch (e) { setErr(e.message); }
  };
  const act = async (fn) => {
    setBusy(true); setErr(null);
    try { await fn(); setMode(null); setNote(""); setEvidenceId(""); onChanged(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const receive = () => act(() => _prJson(`/${r.id}/receive`, _prSend("POST", { evidence_id: Number(evidenceId) })));
  const review = (decision) => act(() => _prJson(`/${r.id}/review`, _prSend("POST", { decision, note: note || null })));

  return (
    <div style={{ padding: "10px 0", borderTop: "1px solid var(--line)" }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ width: 90, fontWeight: 600, fontSize: 12 }}>{r.control_id}</div>
        <div style={{ flex: 1, minWidth: 200, fontSize: 12.5 }}>
          {r.title}
          <div style={{ fontSize: 10.5, color: "var(--ink-4)" }}>
            {r.engagement} · {String(r.period_start).slice(0, 10)} to {String(r.period_end).slice(0, 10)} · owner {r.owner || "unassigned"}
          </div>
        </div>
        <div style={{ fontSize: 11, color: r.display_status === "overdue" ? "var(--red-ink)" : "var(--ink-3)", width: 110 }}>
          {String(r.due_date).slice(0, 10)}<br />{r.status === "accepted" ? "" : prDueLabel(r.due_date)}
        </div>
        <PrBadge status={r.display_status} />
        {a.canReceive && <button style={_prBtn(false)} onClick={openReceive}>Link evidence</button>}
        {r.status === "received" && <button style={_prBtn(true)} disabled={!a.canReview} title={a.reviewBlockedReason || ""} onClick={() => setMode("review")}>Review</button>}
      </div>

      {(r.checks && r.checks.length > 0) && (
        <div style={{ marginTop: 6 }}>{r.checks.map((f, i) => <PrFinding key={i} f={f} />)}
          <span style={{ fontSize: 10.5, color: "var(--ink-4)" }}> automatic checks (v{r.checks_version})</span></div>
      )}
      {(r.status === "received" || r.status === "accepted" || r.status === "rejected") && r.submitted_by && (
        <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 4 }}>
          Evidence #{r.evidence_id} linked by {r.submitted_by}
          {r.reviewed_by ? ` · ${r.status} by ${r.reviewed_by}` : ""}{r.review_note ? ` · "${r.review_note}"` : ""}
        </div>
      )}
      {r.status === "received" && r.checks && r.checks.length === 0 && (
        <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 4 }}>No automatic check fired. That is not proof the evidence is good; review it.</div>
      )}

      {mode === "receive" && (
        <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "end" }}>
          <_PrField label={`Evidence logged for ${r.control_id}`} style={{ flex: 1 }}>
            <select style={_prInput} value={evidenceId} onChange={e => setEvidenceId(e.target.value)}>
              <option value="">{evidence === null ? "Loading…" : evidence.length ? "choose an item…" : "none logged yet (log it on the PBC Evidence Log first)"}</option>
              {(evidence || []).map(e => <option key={e.id} value={e.id}>#{e.id} · {e.title} · {e.period_start ? `${String(e.period_start).slice(0, 10)} to ${String(e.period_end).slice(0, 10)}` : "no period"}</option>)}
            </select>
          </_PrField>
          <button style={_prBtn(true)} disabled={!evidenceId || busy} onClick={receive}>Submit</button>
          <button style={_prBtn(false)} onClick={() => setMode(null)}>Cancel</button>
        </div>
      )}
      {mode === "review" && (
        <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "end" }}>
          <_PrField label="Note (required to reject)" style={{ flex: 1 }}>
            <input style={_prInput} value={note} onChange={e => setNote(e.target.value)} placeholder="Why accepted or rejected" />
          </_PrField>
          <button style={_prBtn(true)} disabled={busy} onClick={() => review("accepted")}>Accept</button>
          <button style={_prBtn(false)} disabled={busy || !note.trim()} onClick={() => review("rejected")}>Reject</button>
          <button style={_prBtn(false)} onClick={() => setMode(null)}>Cancel</button>
        </div>
      )}
      {err && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)", marginTop: 6 }}>{err}</div>}
    </div>
  );
}

function PrGenerateForm({ onDone }) {
  const [f, setF] = React.useState({ engagement: "", period_start: "", period_end: "", due_date: "", control_ids: "" });
  const [msg, setMsg] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const set = (k, v) => setF(x => ({ ...x, [k]: v }));
  const go = async () => {
    setErr(null); setMsg(null);
    try {
      const ids = f.control_ids.split(",").map(s => s.trim()).filter(Boolean);
      const r = await _prJson("/generate", _prSend("POST", { engagement: f.engagement.trim(), period_start: f.period_start, period_end: f.period_end, due_date: f.due_date, control_ids: ids.length ? ids : null }));
      setMsg(`${r.created} request(s) created, ${r.existing} already existed.`);
      onDone(f.engagement.trim());
    } catch (e) { setErr(e.message); }
  };
  return (
    <div style={_prCard}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>Generate an engagement's request list</div>
      <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 10 }}>One request per active evidence requirement. Running it again only adds what is missing.</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
        <_PrField label="Engagement"><input style={_prInput} value={f.engagement} onChange={e => set("engagement", e.target.value)} placeholder="e.g. FY26 SOX" /></_PrField>
        <_PrField label="Period start"><input style={_prInput} type="date" value={f.period_start} onChange={e => set("period_start", e.target.value)} /></_PrField>
        <_PrField label="Period end"><input style={_prInput} type="date" value={f.period_end} onChange={e => set("period_end", e.target.value)} /></_PrField>
        <_PrField label="Due date"><input style={_prInput} type="date" value={f.due_date} onChange={e => set("due_date", e.target.value)} /></_PrField>
        <_PrField label="Only these controls (comma-separated, optional)" style={{ gridColumn: "1 / -1" }}>
          <input style={_prInput} value={f.control_ids} onChange={e => set("control_ids", e.target.value)} placeholder="blank = every control that has a requirement" />
        </_PrField>
      </div>
      <div style={{ marginTop: 10, display: "flex", gap: 10, alignItems: "center" }}>
        <button style={_prBtn(true)} onClick={go}>Generate</button>
        {msg && <span style={{ fontSize: 11.5, color: "var(--green-ink)" }}>{msg}</span>}
      </div>
      {err && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)", marginTop: 6 }}>{err}</div>}
    </div>
  );
}

function PrRequirements() {
  const [reqs, setReqs] = React.useState(null);
  const [f, setF] = React.useState({ control_id: "", requirement_key: "", title: "", description: "", artifact_type: "", source_system: "manual", frequency: "once", requires_signature: false, max_age_days: 90, owner: "", acceptance_notes: "" });
  const [err, setErr] = React.useState(null);
  const set = (k, v) => setF(x => ({ ...x, [k]: v }));
  const load = React.useCallback(() => _prJson("/requirements").then(d => setReqs(d.requirements || [])).catch(e => setErr(e.message)), []);
  React.useEffect(() => { load(); }, [load]);
  const save = async () => {
    setErr(null);
    try {
      await _prJson("/requirements", _prSend("PUT", { ...f, max_age_days: Number(f.max_age_days) || 0 }));
      setF(x => ({ ...x, requirement_key: "", title: "", description: "", acceptance_notes: "" }));
      load();
    } catch (e) { setErr(e.message); }
  };
  return (
    <div>
      <div style={_prCard}>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Declare what evidence a control needs</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 2fr", gap: 8 }}>
          <_PrField label="Control ID *"><input style={_prInput} value={f.control_id} onChange={e => set("control_id", e.target.value)} placeholder="e.g. AC-01" /></_PrField>
          <_PrField label="Requirement key *"><input style={_prInput} value={f.requirement_key} onChange={e => set("requirement_key", e.target.value)} placeholder="e.g. access-review" /></_PrField>
          <_PrField label="Title *"><input style={_prInput} value={f.title} onChange={e => set("title", e.target.value)} placeholder="e.g. Quarterly user access review sign-off" /></_PrField>
          <_PrField label="Owner"><input style={_prInput} value={f.owner} onChange={e => set("owner", e.target.value)} /></_PrField>
          <_PrField label="Source system"><input style={_prInput} value={f.source_system} onChange={e => set("source_system", e.target.value)} /></_PrField>
          <_PrField label="Artifact type"><input style={_prInput} value={f.artifact_type} onChange={e => set("artifact_type", e.target.value)} placeholder="e.g. screenshot, report, ticket" /></_PrField>
          <_PrField label="Frequency">
            <select style={_prInput} value={f.frequency} onChange={e => set("frequency", e.target.value)}>{["once", "monthly", "quarterly", "annual"].map(x => <option key={x}>{x}</option>)}</select>
          </_PrField>
          <_PrField label="Max evidence age (days)"><input style={_prInput} type="number" min="0" value={f.max_age_days} onChange={e => set("max_age_days", e.target.value)} /></_PrField>
          <label style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center", alignSelf: "end", paddingBottom: 6 }}>
            <input type="checkbox" checked={f.requires_signature} onChange={e => set("requires_signature", e.target.checked)} /> Must be signed
          </label>
          <_PrField label="Acceptance notes (what a reviewer should look for)" style={{ gridColumn: "1 / -1" }}>
            <input style={_prInput} value={f.acceptance_notes} onChange={e => set("acceptance_notes", e.target.value)} />
          </_PrField>
        </div>
        <div style={{ marginTop: 10 }}><button style={_prBtn(true)} onClick={save}>Save requirement</button></div>
        {err && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)", marginTop: 6 }}>{err}</div>}
      </div>
      <div style={_prCard}>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Declared requirements</div>
        {reqs === null ? "Loading…" : !reqs.length ? <div style={{ fontSize: 12, color: "var(--ink-3)" }}>None yet.</div> : reqs.map(r => (
          <div key={r.id} style={{ padding: "6px 0", borderTop: "1px solid var(--line)", fontSize: 12 }}>
            <b>{r.control_id}</b> / {r.requirement_key} · {r.title}
            <span style={{ color: "var(--ink-4)" }}> · {r.frequency}{r.requires_signature ? " · signed" : ""} · max age {r.max_age_days}d · {r.source_system}{r.owner ? ` · ${r.owner}` : ""}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Admin: which external auditors may see which engagement. Accounts themselves are
// created in User Management with the "external_auditor" role; this only assigns scope.
function PrAuditorAccess({ engagements }) {
  const [engagement, setEngagement] = React.useState("");
  const [auditors, setAuditors] = React.useState([]);
  const [users, setUsers] = React.useState([]);
  const [userId, setUserId] = React.useState("");
  const [err, setErr] = React.useState(null);
  const [msg, setMsg] = React.useState(null);

  const ext = async (path, opts = {}) => {
    const res = await fetch("/api/external" + path, { credentials: "include", ...opts, headers: opts.body ? { "Content-Type": "application/json" } : {} });
    if (!res.ok) { let d = res.statusText; try { const j = await res.json(); d = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {} throw new Error(d); }
    return res.json();
  };
  const loadAuditors = React.useCallback(async (eng) => {
    if (!eng) { setAuditors([]); return; }
    try { setAuditors((await ext(`/admin/access?engagement=${encodeURIComponent(eng)}`)).auditors || []); setErr(null); }
    catch (e) { setErr(e.message); }
  }, []);
  React.useEffect(() => {
    fetch("/auth/admin/users", { credentials: "include" }).then(r => r.ok ? r.json() : { users: [] })
      .then(d => setUsers((d.users || []).filter(u => u.role === "external_auditor" && u.is_active))).catch(() => {});
  }, []);
  React.useEffect(() => { loadAuditors(engagement); }, [engagement, loadAuditors]);

  const grant = async () => {
    setErr(null); setMsg(null);
    try {
      const r = await ext("/admin/access", _prSend("PUT", { engagement, user_id: Number(userId) }));
      setMsg(r.newly_granted ? `${r.username} can now see ${engagement}.` : `${r.username} already had access.`);
      setUserId(""); loadAuditors(engagement);
    } catch (e) { setErr(e.message); }
  };
  const revoke = async (uid) => {
    setErr(null); setMsg(null);
    try { await ext(`/admin/access?engagement=${encodeURIComponent(engagement)}&user_id=${uid}`, { method: "DELETE" }); loadAuditors(engagement); }
    catch (e) { setErr(e.message); }
  };
  const assignedIds = new Set(auditors.map(a => a.user_id));
  return (
    <div style={_prCard}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>External auditor access</div>
      <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 10 }}>
        External auditors see only the engagements assigned here, read-only, in a separate audit portal. Create their account in User
        Management with the <b>external_auditor</b> role, then assign it. Removing access takes effect on their next request.
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap" }}>
        <_PrField label="Engagement" style={{ width: 240 }}>
          <select style={_prInput} value={engagement} onChange={e => setEngagement(e.target.value)}>
            <option value="">choose…</option>{engagements.map(e => <option key={e}>{e}</option>)}
          </select>
        </_PrField>
        <_PrField label="Auditor account" style={{ width: 260 }}>
          <select style={_prInput} value={userId} onChange={e => setUserId(e.target.value)} disabled={!engagement}>
            <option value="">{users.length ? "choose…" : "no active external_auditor accounts"}</option>
            {users.filter(u => !assignedIds.has(u.id)).map(u => <option key={u.id} value={u.id}>{u.display_name || u.username} ({u.username})</option>)}
          </select>
        </_PrField>
        <button style={_prBtn(true)} disabled={!engagement || !userId} onClick={grant}>Assign</button>
      </div>
      {msg && <div style={{ fontSize: 11.5, color: "var(--green-ink)", marginTop: 8 }}>{msg}</div>}
      {err && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)", marginTop: 8 }}>{err}</div>}
      {engagement && (
        <div style={{ marginTop: 12 }}>
          {!auditors.length ? <div style={{ fontSize: 12, color: "var(--ink-3)" }}>No external auditors assigned to {engagement}.</div> : auditors.map(a => (
            <div key={a.user_id} style={{ display: "flex", gap: 10, alignItems: "center", padding: "6px 0", borderTop: "1px solid var(--line)", fontSize: 12 }}>
              <b style={{ flex: 1 }}>{a.username}</b>
              <span style={{ color: "var(--ink-4)" }}>assigned by {a.granted_by}</span>
              <button style={_prBtn(false)} onClick={() => revoke(a.user_id)}>Remove access</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PbcRequestsScreen() {
  const auth = window.useAuth ? window.useAuth() : null;
  const username = auth?.user?.username;
  const [tab, setTab] = React.useState("requests");
  const [requests, setRequests] = React.useState(null);
  const [summary, setSummary] = React.useState(null);
  const [engagement, setEngagement] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [error, setError] = React.useState(null);

  const load = React.useCallback(async (eng) => {
    try {
      const q = (eng ?? engagement) ? `?engagement=${encodeURIComponent(eng ?? engagement)}` : "";
      const [list, sum] = await Promise.all([_prJson("/" + q), _prJson("/summary" + q)]);
      setRequests(list.requests || []); setSummary(sum); setError(null);
    } catch (e) { setError(e.message); }
  }, [engagement]);
  React.useEffect(() => { load(); }, [load]);

  const all = requests || [];
  const [engagements, setEngagements] = React.useState([]);
  React.useEffect(() => {
    // The engagement picker must list every engagement, not just the filtered one.
    _prJson("/").then(d => setEngagements(prEngagements(d.requests || []))).catch(() => {});
  }, [requests]);
  const shown = all.filter(r => !status || r.display_status === status);

  return (
    <div className="scope-screen" data-screen-label="PBC Requests">
      <div className="panel-head">
        <div>
          <div className="kicker">Audit &amp; Compliance · PBC Requests</div>
          <div className="panel-title mt-8">PBC Requests</div>
          <div className="panel-sub">
            Each control declares the evidence it needs, and an engagement's request list is generated from that. Link evidence
            from the PBC Evidence Log to answer a request; automatic checks run against the requirement as it was when the
            request was raised, and a different person accepts or rejects.
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        {[["requests", "Requests"], ["generate", "Generate"], ["requirements", "Requirements"],
          ...(auth?.user?.role === "admin" ? [["auditors", "Auditor access"]] : [])].map(([k, l]) => (
          <button key={k} style={_prBtn(tab === k)} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {error && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>}

      {tab === "requirements" && <PrRequirements />}
      {tab === "auditors" && <PrAuditorAccess engagements={engagements} />}
      {tab === "generate" && <PrGenerateForm onDone={(eng) => { setEngagement(eng); setTab("requests"); load(eng); }} />}
      {tab === "requests" && (
        <>
          <div style={{ display: "flex", gap: 10, marginBottom: 12, alignItems: "center" }}>
            <select style={{ ..._prInput, width: 220 }} value={engagement} onChange={e => setEngagement(e.target.value)}>
              <option value="">All engagements</option>{engagements.map(e => <option key={e}>{e}</option>)}
            </select>
            <select style={{ ..._prInput, width: 160 }} value={status} onChange={e => setStatus(e.target.value)}>
              <option value="">Any status</option>
              {["requested", "overdue", "received", "rejected", "accepted"].map(s => <option key={s} value={s}>{PR_STATUS_META[s].label}</option>)}
            </select>
            <span style={{ fontSize: 11, color: "var(--ink-4)" }}>{shown.length} request{shown.length === 1 ? "" : "s"}</span>
          </div>
          <PrBoard summary={summary} />
          <div style={_prCard}>
            {requests === null ? "Loading…" : !shown.length
              ? <div style={{ fontSize: 12, color: "var(--ink-3)" }}>No requests. Declare requirements, then generate an engagement's list.</div>
              : shown.map(r => <PrRequestRow key={r.id} r={r} username={username} onChanged={() => load()} />)}
          </div>
        </>
      )}
    </div>
  );
}

Object.assign(window, { PbcRequestsScreen, prActions, prEngagements, prDueLabel });
