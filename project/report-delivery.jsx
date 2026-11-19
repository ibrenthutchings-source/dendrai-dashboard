/* ============================================================
   Send to... — push a Loop Report or Audit Evidence Pack to an
   external webhook / API destination. Shared by ReportModal
   (report.jsx) and EvidencePackModal (evidence-pack.jsx) — both
   render <SendReportModal artifactType=".." payload={..} .../> from
   their own footer button rather than duplicating this flow twice.

   Backed by report_delivery_endpoints.py:
     GET/POST   /api/report-delivery/destinations
     PUT/DELETE /api/report-delivery/destinations/{id}
     POST       /api/report-delivery/destinations/{id}/test
     POST       /api/report-delivery/send
     GET        /api/report-delivery/deliveries
   ============================================================ */

const RD_AUTH_TYPES = [
  { value: "none",    label: "None" },
  { value: "bearer",  label: "Bearer token" },
  { value: "api_key", label: "API key header" },
  { value: "basic",   label: "Basic auth" },
  { value: "hmac",    label: "HMAC signature (shared secret)" },
];

const RD_PAYLOAD_FORMATS = [
  { value: "raw",     label: "Raw JSON (any system)" },
  { value: "slack",   label: "Slack-compatible" },
  { value: "msteams", label: "MS Teams-compatible" },
];

function _rdEmptyCreds() {
  return { token: "", header_name: "X-API-Key", api_key: "", username: "", password: "", secret: "" };
}

function _rdCredsForAuth(authType, creds) {
  if (authType === "none") return undefined;
  if (authType === "bearer")  return { token: creds.token };
  if (authType === "api_key") return { header_name: creds.header_name || "X-API-Key", api_key: creds.api_key };
  if (authType === "basic")   return { username: creds.username, password: creds.password };
  if (authType === "hmac")    return { secret: creds.secret };
  return undefined;
}

// ── Auth fields sub-form — shared between the destination editor and the
// ad-hoc one-off send form. ────────────────────────────────────────────────
function RdAuthFields({ authType, onAuthTypeChange, creds, onCredsChange }) {
  return (
    <>
      <div style={{ marginBottom: 8 }}>
        <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Authentication</label>
        <select className="fi-input" value={authType} onChange={e => onAuthTypeChange(e.target.value)} style={{ width: "100%", fontSize: 12 }}>
          {RD_AUTH_TYPES.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
        </select>
      </div>
      {authType === "bearer" && (
        <div style={{ marginBottom: 8 }}>
          <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Token</label>
          <input type="password" className="fi-input" style={{ width: "100%", fontSize: 12 }} value={creds.token}
            onChange={e => onCredsChange({ ...creds, token: e.target.value })} placeholder="Bearer token" />
        </div>
      )}
      {authType === "api_key" && (
        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Header name</label>
            <input className="fi-input" style={{ width: "100%", fontSize: 12 }} value={creds.header_name}
              onChange={e => onCredsChange({ ...creds, header_name: e.target.value })} placeholder="X-API-Key" />
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Key</label>
            <input type="password" className="fi-input" style={{ width: "100%", fontSize: 12 }} value={creds.api_key}
              onChange={e => onCredsChange({ ...creds, api_key: e.target.value })} placeholder="API key" />
          </div>
        </div>
      )}
      {authType === "basic" && (
        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Username</label>
            <input className="fi-input" style={{ width: "100%", fontSize: 12 }} value={creds.username}
              onChange={e => onCredsChange({ ...creds, username: e.target.value })} />
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Password</label>
            <input type="password" className="fi-input" style={{ width: "100%", fontSize: 12 }} value={creds.password}
              onChange={e => onCredsChange({ ...creds, password: e.target.value })} />
          </div>
        </div>
      )}
      {authType === "hmac" && (
        <div style={{ marginBottom: 8 }}>
          <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>
            Shared secret <span className="mono" style={{ fontWeight: 400, color: "var(--ink-4)" }}>· signs the body as X-Dendrai-Signature-256</span>
          </label>
          <input type="password" className="fi-input" style={{ width: "100%", fontSize: 12 }} value={creds.secret}
            onChange={e => onCredsChange({ ...creds, secret: e.target.value })} />
        </div>
      )}
    </>
  );
}

// ── Add/edit destination form ───────────────────────────────────────────────
function RdDestinationForm({ initial, onSaved, onCancel }) {
  const [displayName, setDisplayName] = useState(initial?.display_name || "");
  const [url, setUrl] = useState(initial?.url || "");
  const [httpMethod, setHttpMethod] = useState(initial?.http_method || "POST");
  const [authType, setAuthType] = useState(initial?.auth_type || "none");
  const [creds, setCreds] = useState(_rdEmptyCreds());
  const [payloadFormat, setPayloadFormat] = useState(initial?.payload_format || "raw");
  const [artifactTypes, setArtifactTypes] = useState(initial?.artifact_types || ["loop_report", "evidence_pack"]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  function toggleArtifactType(t) {
    setArtifactTypes(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t]);
  }

  async function save() {
    if (!displayName.trim() || !url.trim() || artifactTypes.length === 0) {
      setErr("Name, URL, and at least one artifact type are required."); return;
    }
    setSaving(true); setErr(null);
    const body = {
      display_name: displayName.trim(), url: url.trim(), http_method: httpMethod,
      auth_type: authType, credentials: _rdCredsForAuth(authType, creds),
      payload_format: payloadFormat, artifact_types: artifactTypes,
    };
    try {
      const res = await fetch(initial ? `/api/report-delivery/destinations/${initial.id}` : `/api/report-delivery/destinations`, {
        method: initial ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`); }
      onSaved();
    } catch (e) {
      setErr(e.message || "Failed to save destination");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 14, background: "var(--surface-2)" }}>
      {err && <div style={{ fontSize: 11, color: "var(--red-ink, #b93333)", marginBottom: 8 }}>{err}</div>}
      <div style={{ marginBottom: 8 }}>
        <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Name</label>
        <input className="fi-input" style={{ width: "100%", fontSize: 12 }} value={displayName}
          onChange={e => setDisplayName(e.target.value)} placeholder="e.g. ServiceNow GRC intake" />
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
        <div style={{ flex: 3 }}>
          <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>URL</label>
          <input className="fi-input mono" style={{ width: "100%", fontSize: 11.5 }} value={url}
            onChange={e => setUrl(e.target.value)} placeholder="https://example.com/webhooks/dendrai" />
        </div>
        <div style={{ flex: 1 }}>
          <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Method</label>
          <select className="fi-input" style={{ width: "100%", fontSize: 12 }} value={httpMethod} onChange={e => setHttpMethod(e.target.value)}>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
          </select>
        </div>
      </div>
      <div style={{ marginBottom: 8 }}>
        <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Send from</label>
        <div style={{ display: "flex", gap: 12 }}>
          <label style={{ fontSize: 11.5, display: "flex", alignItems: "center", gap: 5 }}>
            <input type="checkbox" checked={artifactTypes.includes("loop_report")} onChange={() => toggleArtifactType("loop_report")} /> Loop Report
          </label>
          <label style={{ fontSize: 11.5, display: "flex", alignItems: "center", gap: 5 }}>
            <input type="checkbox" checked={artifactTypes.includes("evidence_pack")} onChange={() => toggleArtifactType("evidence_pack")} /> Evidence Pack
          </label>
        </div>
      </div>
      <div style={{ marginBottom: 8 }}>
        <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Payload format</label>
        <select className="fi-input" style={{ width: "100%", fontSize: 12 }} value={payloadFormat} onChange={e => setPayloadFormat(e.target.value)}>
          {RD_PAYLOAD_FORMATS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
      </div>
      <RdAuthFields authType={authType} onAuthTypeChange={setAuthType} creds={creds} onCredsChange={setCreds} />
      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end", marginTop: 10 }}>
        <button className="btn btn-sm" onClick={onCancel} disabled={saving}>Cancel</button>
        <button className="btn btn-sm btn-primary" onClick={save} disabled={saving}>
          {saving ? "Saving…" : initial ? "Save changes" : "Add destination"}
        </button>
      </div>
    </div>
  );
}

// ── Main "Send to..." modal ─────────────────────────────────────────────────
function SendReportModal({ open, onClose, artifactType, payload, runId, ticker }) {
  const [destinations, setDestinations] = useState([]);
  const [loadingDests, setLoadingDests] = useState(false);
  const [mode, setMode] = useState("pick");   // pick | add | edit | adhoc
  const [editingDest, setEditingDest] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [sendState, setSendState] = useState({}); // { [id]: {loading, status, error} }

  // Ad-hoc (one-off, nothing saved) form state
  const [adhocUrl, setAdhocUrl] = useState("");
  const [adhocMethod, setAdhocMethod] = useState("POST");
  const [adhocAuthType, setAdhocAuthType] = useState("none");
  const [adhocCreds, setAdhocCreds] = useState(_rdEmptyCreds());
  const [adhocFormat, setAdhocFormat] = useState("raw");

  function loadDestinations() {
    setLoadingDests(true);
    fetch("/api/report-delivery/destinations")
      .then(r => r.json())
      .then(d => setDestinations((d.destinations || []).filter(x => x.active)))
      .catch(() => setDestinations([]))
      .finally(() => setLoadingDests(false));
  }

  useEffect(() => { if (open) { loadDestinations(); setMode("pick"); setSendState({}); } }, [open]);

  if (!open) return null;

  const eligible = destinations.filter(d => (d.artifact_types || []).includes(artifactType));

  async function sendTo(destId) {
    setSendState(prev => ({ ...prev, [destId]: { loading: true } }));
    try {
      const res = await fetch("/api/report-delivery/send", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ artifact_type: artifactType, payload, destination_id: destId, run_id: runId || null, ticker: ticker || null }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || `HTTP ${res.status}`);
      setSendState(prev => ({ ...prev, [destId]: { loading: false, status: d.status, error: d.error } }));
      window.showToast?.(
        d.status === "ok" ? "Sent successfully." : `Send failed — ${d.error || "unknown error"}`,
        { tone: d.status === "ok" ? "good" : "bad" },
      );
    } catch (e) {
      setSendState(prev => ({ ...prev, [destId]: { loading: false, status: "error", error: e.message } }));
      window.showToast?.(`Send failed — ${e.message}`, { tone: "bad" });
    }
  }

  async function sendAdhoc() {
    if (!adhocUrl.trim()) { window.showToast?.("URL is required.", { tone: "warn" }); return; }
    setSendState(prev => ({ ...prev, adhoc: { loading: true } }));
    try {
      const res = await fetch("/api/report-delivery/send", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          artifact_type: artifactType, payload, url: adhocUrl.trim(), http_method: adhocMethod,
          auth_type: adhocAuthType, credentials: _rdCredsForAuth(adhocAuthType, adhocCreds),
          payload_format: adhocFormat, run_id: runId || null, ticker: ticker || null,
        }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || `HTTP ${res.status}`);
      setSendState(prev => ({ ...prev, adhoc: { loading: false, status: d.status, error: d.error } }));
      window.showToast?.(
        d.status === "ok" ? "Sent successfully." : `Send failed — ${d.error || "unknown error"}`,
        { tone: d.status === "ok" ? "good" : "bad" },
      );
    } catch (e) {
      setSendState(prev => ({ ...prev, adhoc: { loading: false, status: "error", error: e.message } }));
      window.showToast?.(`Send failed — ${e.message}`, { tone: "bad" });
    }
  }

  async function deleteDest(id) {
    if (!window.confirm("Remove this destination?")) return;
    await fetch(`/api/report-delivery/destinations/${id}`, { method: "DELETE" }).catch(() => {});
    loadDestinations();
  }

  const artifactLabel = artifactType === "loop_report" ? "Loop Report" : "Audit Evidence Pack";

  return (
    <Modal open={open} onClose={onClose} title={`Send ${artifactLabel}`} width={560}
      titleSub="Push this artifact to an external webhook or API endpoint">
      {mode === "pick" && (
        <>
          {loadingDests ? (
            <div style={{ padding: "20px 0", textAlign: "center", color: "var(--ink-3)", fontSize: 12 }}>Loading destinations…</div>
          ) : eligible.length === 0 ? (
            <div style={{ fontSize: 11.5, color: "var(--ink-4)", fontStyle: "italic", padding: "8px 0" }}>
              No saved destinations accept {artifactLabel} yet.
            </div>
          ) : (
            <div style={{ display: "grid", gap: 8, marginBottom: 12 }}>
              {eligible.map(d => {
                const s = sendState[d.id] || {};
                return (
                  <div key={d.id} style={{
                    display: "flex", alignItems: "center", gap: 8, padding: "9px 12px",
                    border: "1px solid var(--line)", borderRadius: 7, background: "var(--surface-2)",
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12.5, fontWeight: 500 }}>{d.display_name}</div>
                      <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {d.http_method} {d.url}
                      </div>
                      {d.last_sent_at && (
                        <div style={{ fontSize: 10, color: d.last_status === "ok" ? "var(--green-ink)" : "var(--red-ink, #b93333)" }}>
                          Last sent {new Date(d.last_sent_at).toLocaleString()} · {d.last_status}
                        </div>
                      )}
                    </div>
                    <button className="btn btn-sm" onClick={() => { setEditingDest(d); setMode("edit"); }}>
                      <Icon name="edit" size={11}/>
                    </button>
                    <button className="btn btn-sm" onClick={() => deleteDest(d.id)}>
                      <Icon name="x" size={11}/>
                    </button>
                    <button className="btn btn-sm btn-primary" onClick={() => sendTo(d.id)} disabled={s.loading}>
                      {s.loading ? "Sending…" : s.status === "ok" ? "Sent ✓" : "Send"}
                    </button>
                  </div>
                );
              })}
            </div>
          )}
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn btn-sm" onClick={() => setMode("add")}><Icon name="plus" size={11}/> Add destination</button>
            <button className="btn btn-sm" onClick={() => setMode("adhoc")}>One-off send to a URL</button>
          </div>
        </>
      )}

      {mode === "add" && (
        <RdDestinationForm onCancel={() => setMode("pick")} onSaved={() => { setMode("pick"); loadDestinations(); }} />
      )}
      {mode === "edit" && editingDest && (
        <RdDestinationForm initial={editingDest} onCancel={() => setMode("pick")}
          onSaved={() => { setMode("pick"); loadDestinations(); }} />
      )}

      {mode === "adhoc" && (
        <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 14, background: "var(--surface-2)" }}>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 10 }}>
            Sends once — nothing is saved as a reusable destination.
          </div>
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <div style={{ flex: 3 }}>
              <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>URL</label>
              <input className="fi-input mono" style={{ width: "100%", fontSize: 11.5 }} value={adhocUrl}
                onChange={e => setAdhocUrl(e.target.value)} placeholder="https://example.com/webhooks/dendrai" />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Method</label>
              <select className="fi-input" style={{ width: "100%", fontSize: 12 }} value={adhocMethod} onChange={e => setAdhocMethod(e.target.value)}>
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
              </select>
            </div>
          </div>
          <div style={{ marginBottom: 8 }}>
            <label style={{ fontSize: 10.5, color: "var(--ink-3)", fontWeight: 600, display: "block", marginBottom: 3 }}>Payload format</label>
            <select className="fi-input" style={{ width: "100%", fontSize: 12 }} value={adhocFormat} onChange={e => setAdhocFormat(e.target.value)}>
              {RD_PAYLOAD_FORMATS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
            </select>
          </div>
          <RdAuthFields authType={adhocAuthType} onAuthTypeChange={setAdhocAuthType} creds={adhocCreds} onCredsChange={setAdhocCreds} />
          <div style={{ display: "flex", gap: 6, justifyContent: "flex-end", marginTop: 10 }}>
            <button className="btn btn-sm" onClick={() => setMode("pick")}>Back</button>
            <button className="btn btn-sm btn-primary" onClick={sendAdhoc} disabled={sendState.adhoc?.loading}>
              {sendState.adhoc?.loading ? "Sending…" : "Send"}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

Object.assign(window, { SendReportModal });
