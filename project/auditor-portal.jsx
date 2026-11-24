/* ============================================================
   Audit Portal — what an external auditor sees instead of the app.

   Rendered by AuthProvider (auth.jsx) for accounts with the external_auditor role;
   the main application is never loaded for them. Read-only: look at the requests
   and evidence for the engagements an admin assigned, and verify / view / download
   the signed report runs. The server enforces all of it (deny-by-default role,
   assignment scoping, audit logging); this screen only presents it.

   Backed by external_endpoints.py (/external).
   ============================================================ */

const _AP = "/api/external";

async function _apFetch(path, opts = {}) {
  const res = await fetch(_AP + path, { credentials: "include", ...opts });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res;
}
const _apJson = (path) => _apFetch(path).then(r => r.json());

const _apCard = { border: "1px solid var(--line, #ddd)", borderRadius: 8, padding: 14, background: "var(--surface, #fff)", marginBottom: 12 };
const _apBtn = (primary) => ({
  fontSize: 12, padding: "5px 12px", borderRadius: 5, cursor: "pointer", fontWeight: 600,
  border: primary ? "1px solid var(--ink, #111)" : "1px solid var(--line, #ddd)",
  background: primary ? "var(--ink, #111)" : "var(--surface, #fff)", color: primary ? "var(--surface, #fff)" : "var(--ink, #111)",
});

const AP_STATUS = {
  requested: "Requested", received: "Received, awaiting review", accepted: "Accepted", rejected: "Rejected", overdue: "Overdue",
};

function ApRequest({ r }) {
  const [ev, setEv] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const snap = r.requirement_snapshot || {};
  const load = async () => {
    setErr(null);
    try { setEv((await _apJson(`/requests/${r.id}/evidence`)).evidence); } catch (e) { setErr(e.message); }
  };
  return (
    <div style={{ padding: "10px 0", borderTop: "1px solid var(--line, #ddd)", fontSize: 12.5 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <b style={{ width: 90 }}>{r.control_id}</b>
        <span style={{ flex: 1, minWidth: 200 }}>{r.title}</span>
        <span style={{ fontSize: 11, color: r.display_status === "overdue" ? "#b00020" : "#666" }}>due {String(r.due_date).slice(0, 10)}</span>
        <span style={{ fontWeight: 700, fontSize: 11 }}>{AP_STATUS[r.display_status] || r.display_status}</span>
        {r.evidence_id && !ev && <button style={_apBtn(false)} onClick={load}>Show evidence</button>}
      </div>
      <div style={{ fontSize: 11, color: "#666", marginTop: 3 }}>
        Period {String(r.period_start).slice(0, 10)} to {String(r.period_end).slice(0, 10)} · owner {r.owner || "unassigned"}
        {snap.requires_signature ? " · signature required" : ""}{snap.max_age_days != null ? ` · evidence no older than ${snap.max_age_days} days` : ""}
        {snap.acceptance_notes ? ` · acceptance: ${snap.acceptance_notes}` : ""}
      </div>
      {r.checks && r.checks.length > 0 && (
        <div style={{ marginTop: 4, fontSize: 11 }}>
          {r.checks.map((f, i) => <div key={i} style={{ color: f.severity === "HIGH" ? "#b00020" : "#8a5a00" }}>{f.code.replace(/_/g, " ")}: {f.message}</div>)}
        </div>
      )}
      {r.reviewed_by && (
        <div style={{ fontSize: 11, color: "#666", marginTop: 3 }}>
          {r.status} by {r.reviewed_by}{r.review_note ? `: "${r.review_note}"` : ""} · evidence submitted by {r.submitted_by}
        </div>
      )}
      {err && <div style={{ color: "#b00020", fontSize: 11.5, marginTop: 4 }}>{err}</div>}
      {ev && (
        <div style={{ marginTop: 6, padding: 8, background: "var(--surface-2, #f6f6f6)", borderRadius: 6, fontSize: 11.5 }}>
          <div><b>{ev.title}</b> (evidence #{ev.id})</div>
          {ev.description && <div>{ev.description}</div>}
          <div style={{ color: "#555" }}>
            Collected {ev.collected_date ? String(ev.collected_date).slice(0, 10) : "date not recorded"} · covers {ev.period_start ? `${String(ev.period_start).slice(0, 10)} to ${String(ev.period_end).slice(0, 10)}` : "no period recorded"} ·
            {" "}{ev.has_signature ? "signed" : "not signed"} · logged by {ev.created_by}
          </div>
          {ev.source_url && <div style={{ color: "#555", wordBreak: "break-all" }}>Source: {ev.source_url}</div>}
          {ev.quality_flags && ev.quality_flags.length > 0 && ev.quality_flags.map((f, i) => <div key={i} style={{ color: "#8a5a00" }}>{String(f.code || "").replace(/_/g, " ")}: {f.message}</div>)}
        </div>
      )}
    </div>
  );
}

function ApRuns({ engagement }) {
  const [runs, setRuns] = React.useState(null);
  const [checks, setChecks] = React.useState({});
  const [html, setHtml] = React.useState(null);
  const [err, setErr] = React.useState(null);
  React.useEffect(() => {
    setRuns(null);
    _apJson(`/runs?engagement=${encodeURIComponent(engagement)}`).then(d => setRuns(d.runs || [])).catch(e => setErr(e.message));
  }, [engagement]);
  const verify = async (id) => {
    try { const r = await _apJson(`/runs/${id}/verify`); setChecks(c => ({ ...c, [id]: r })); }
    catch (e) { setChecks(c => ({ ...c, [id]: { error: e.message } })); }
  };
  const view = async (id) => { try { setHtml(await (await _apFetch(`/runs/${id}/html`)).text()); } catch (e) { setErr(e.message); } };
  const download = async (id, key, ext = "xlsx") => {
    try {
      const url = URL.createObjectURL(await (await _apFetch(`/runs/${id}/${ext}`)).blob());
      const a = document.createElement("a"); a.href = url; a.download = `${key || "report"}-run${id}.${ext}`; a.click(); URL.revokeObjectURL(url);
    } catch (e) { setErr(e.message); }
  };
  return (
    <div style={_apCard}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Signed report runs</div>
      <div style={{ fontSize: 11, color: "#666", marginBottom: 8 }}>
        Each run stores the report definition, parameters, the exact queries and the result rows, and is signed. Verify checks that nothing changed
        after signing; the Excel file includes a Reperformance sheet with the SQL, parameters and hashes needed to redo each block.
      </div>
      {err && <div style={{ color: "#b00020", fontSize: 11.5 }}>{err}</div>}
      {runs === null ? "Loading…" : !runs.length ? <div style={{ fontSize: 12, color: "#666" }}>No report runs have been shared for this engagement.</div> : runs.map(r => {
        const v = checks[r.id];
        return (
          <div key={r.id} style={{ padding: "6px 0", borderTop: "1px solid var(--line, #ddd)", fontSize: 12 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <b>Run #{r.id}</b> · {r.definition_key} v{r.definition_version}
              <span style={{ color: "#666", flex: 1 }}>{new Date(r.created_at).toLocaleString()} · by {r.created_by}{r.note ? ` · ${r.note}` : ""}</span>
              <button style={_apBtn(false)} onClick={() => verify(r.id)}>Verify</button>
              <button style={_apBtn(false)} onClick={() => view(r.id)}>View</button>
              <button style={_apBtn(false)} onClick={() => download(r.id, r.definition_key, "xlsx")}>Excel</button>
              <button style={_apBtn(false)} onClick={() => download(r.id, r.definition_key, "pdf")}>PDF</button>
              <button style={_apBtn(false)} onClick={() => download(r.id, r.definition_key, "pptx")}>PowerPoint</button>
            </div>
            {v && <div style={{ fontSize: 11.5, marginTop: 4, color: v.error || !v.intact ? "#b00020" : "#1b7a3a" }}>
              {v.error ? v.error : v.intact ? "Verified: signature, row hashes and report definition all match." : `Does NOT verify: ${v.detail}`}</div>}
          </div>
        );
      })}
      {html && (
        <div style={{ marginTop: 10 }}>
          <button style={_apBtn(false)} onClick={() => setHtml(null)}>Close</button>
          <iframe title="Report run" sandbox="" srcDoc={html} style={{ width: "100%", height: 560, border: "1px solid #ddd", borderRadius: 6, background: "#fff", marginTop: 6 }} />
        </div>
      )}
    </div>
  );
}

function ApEngagement({ engagement }) {
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [filter, setFilter] = React.useState("");
  React.useEffect(() => {
    setData(null); setErr(null);
    _apJson(`/requests?engagement=${encodeURIComponent(engagement)}`).then(setData).catch(e => setErr(e.message));
  }, [engagement]);
  const shown = (data?.requests || []).filter(r => !filter || r.display_status === filter);
  const s = data?.summary;
  return (
    <div>
      {err && <div style={{ color: "#b00020", fontSize: 12, marginBottom: 10 }}>{err}</div>}
      {s && (
        <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap", fontSize: 12 }}>
          {[["total", "Total"], ["accepted", "Accepted"], ["received", "Awaiting review"], ["requested", "Requested"], ["rejected", "Rejected"], ["overdue", "Overdue"]].map(([k, l]) =>
            <div key={k} style={{ ..._apCard, marginBottom: 0, padding: "8px 14px" }}><div style={{ fontSize: 20, fontWeight: 700 }}>{s[k] ?? 0}</div><div style={{ fontSize: 10.5, color: "#666" }}>{l}</div></div>)}
        </div>
      )}
      <div style={_apCard}>
        <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 6 }}>
          <div style={{ fontWeight: 700, fontSize: 13, flex: 1 }}>Document requests</div>
          <select value={filter} onChange={e => setFilter(e.target.value)} style={{ fontSize: 12, padding: "4px 8px" }}>
            <option value="">Any status</option>{Object.entries(AP_STATUS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </div>
        {data === null && !err ? "Loading…" : !shown.length ? <div style={{ fontSize: 12, color: "#666" }}>No requests.</div> : shown.map(r => <ApRequest key={r.id} r={r} />)}
      </div>
      <ApRuns engagement={engagement} />
    </div>
  );
}

function AuditorPortal() {
  const auth = window.useAuth ? window.useAuth() : null;
  const [engagements, setEngagements] = React.useState(null);
  const [current, setCurrent] = React.useState("");
  const [err, setErr] = React.useState(null);
  React.useEffect(() => {
    _apJson("/engagements").then(d => {
      setEngagements(d.engagements || []);
      if ((d.engagements || []).length) setCurrent(d.engagements[0].engagement);
    }).catch(e => setErr(e.message));
  }, []);
  return (
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: "20px 16px", fontFamily: "system-ui, sans-serif", color: "var(--ink, #111)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 11, color: "#666", textTransform: "uppercase", letterSpacing: "0.05em" }}>Dendrai Intelligenza · Audit portal</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>Engagement materials</div>
        </div>
        <span style={{ fontSize: 12, color: "#666" }}>{auth?.user?.display_name || auth?.user?.username}</span>
        <button style={_apBtn(false)} onClick={() => auth && auth.logout()}>Sign out</button>
      </div>
      <div style={{ fontSize: 12, color: "#666", marginBottom: 14 }}>
        You can see the engagements assigned to you. This portal is read-only, and your views and downloads are logged.
      </div>
      {err && <div style={{ color: "#b00020", fontSize: 12, marginBottom: 10 }}>{err}</div>}
      {engagements === null && !err ? "Loading…" : engagements && !engagements.length ? (
        <div style={_apCard}>No engagements have been assigned to your account yet. Ask the person who invited you.</div>
      ) : engagements && (
        <>
          <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
            {engagements.map(e => (
              <button key={e.engagement} style={_apBtn(current === e.engagement)} onClick={() => setCurrent(e.engagement)}>
                {e.engagement} <span style={{ opacity: 0.7, fontWeight: 400 }}>· {e.requests_accepted}/{e.requests_total} accepted</span>
              </button>
            ))}
          </div>
          {current && <ApEngagement engagement={current} key={current} />}
        </>
      )}
    </div>
  );
}

Object.assign(window, { AuditorPortal });
