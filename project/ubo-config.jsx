/* ============================================================
   Dendrai UBO Configuration screen
   Manage systems monitored by the Dendrai UBO Governance Brain and
   Policy-as-Code source repositories.
   ============================================================ */

const UBO_SERVER_TYPES = [
  { id: "saviynt",              label: "Saviynt IGA" },
  { id: "oracle-fusion",        label: "Oracle Fusion ERP" },
  { id: "sap",                  label: "SAP" },
  { id: "servicenow",           label: "ServiceNow" },
  { id: "workday",              label: "Workday" },
  { id: "entra",                label: "Microsoft Entra ID" },
  { id: "github",               label: "GitHub" },
  { id: "mcp",                  label: "MCP Server" },
  { id: "custom",               label: "Custom / Generic" },
];

const UBO_MCP_TYPE = "mcp";

const UBO_GOVERNANCE_TIERS = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];

const UBO_TIER_COLORS = {
  CRITICAL: { bg: "var(--red-soft)",    ink: "var(--red-ink)" },
  HIGH:     { bg: "var(--amber-soft)",  ink: "var(--amber-ink)" },
  MEDIUM:   { bg: "var(--blue-soft)",   ink: "var(--blue-ink)" },
  LOW:      { bg: "var(--surface-2)",   ink: "var(--ink-3)" },
};

const PAC_PROVIDERS = [
  { id: "github",     label: "GitHub" },
  { id: "gitlab",     label: "GitLab" },
  { id: "bitbucket",  label: "Bitbucket" },
  { id: "azure-devops", label: "Azure DevOps" },
  { id: "custom",     label: "Custom Git" },
];

const PAC_PROCESSES = [
  { id: "all",             label: "All processes" },
  { id: "itgc",            label: "ITGC" },
  { id: "order_to_cash",   label: "Order-to-Cash" },
  { id: "procure_to_pay",  label: "Procure-to-Pay" },
  { id: "receive_to_ship", label: "Receive-to-Ship" },
  { id: "record_to_report",label: "Record-to-Report" },
];

function _uboConfigBase() {
  return (window.MCP_API_BASE || "/api/mcp") + "/observability";
}

// ── Shared sub-components ──────────────────────────────────────────────────────

function TierChip({ tier }) {
  const c = UBO_TIER_COLORS[tier] || UBO_TIER_COLORS.LOW;
  return (
    <span style={{
      display: "inline-block",
      padding: "1px 6px",
      borderRadius: 4,
      fontSize: 10,
      fontWeight: 600,
      fontFamily: "var(--mono, monospace)",
      letterSpacing: "0.04em",
      background: c.bg,
      color: c.ink,
    }}>
      {tier}
    </span>
  );
}

function StatusDot({ active }) {
  return (
    <span style={{
      display: "inline-block",
      width: 7,
      height: 7,
      borderRadius: "50%",
      background: active ? "var(--green)" : "var(--line-strong)",
      flexShrink: 0,
    }} title={active ? "Active" : "Inactive"} />
  );
}

function EmptyState({ icon, title, sub }) {
  return (
    <div style={{
      textAlign: "center",
      padding: "28px 16px",
      color: "var(--ink-3)",
    }}>
      <div style={{ fontSize: 22, marginBottom: 8, opacity: 0.5 }}>{icon}</div>
      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink-2)", marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 11, lineHeight: 1.5 }}>{sub}</div>
    </div>
  );
}

// ── API key display (copy-to-clipboard with reveal toggle) ───────────────────

function ApiKeyDisplay({ apiKey, ingestBase }) {
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!apiKey) return <span style={{ fontSize: 10, color: "var(--ink-4)" }}>—</span>;

  const masked = apiKey.slice(0, 8) + "••••••••••••••••••••••••••••";
  const display = revealed ? apiKey : masked;

  function copy(text) {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
        <code style={{
          fontSize: 10,
          fontFamily: "var(--mono, monospace)",
          background: "var(--surface-2)",
          padding: "2px 6px",
          borderRadius: 3,
          color: "var(--ink-2)",
          letterSpacing: "0.03em",
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>{display}</code>
        <button className="btn btn-sm btn-ghost" onClick={() => setRevealed(r => !r)}
          style={{ padding: "2px 6px", fontSize: 10 }} title={revealed ? "Hide" : "Reveal"}>
          {revealed ? "Hide" : "Show"}
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => copy(apiKey)}
          style={{ padding: "2px 6px", fontSize: 10, color: copied ? "var(--green)" : undefined }}>
          {copied ? "✓" : "Copy key"}
        </button>
      </div>
      {ingestBase && (
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <code style={{
            fontSize: 9,
            fontFamily: "var(--mono, monospace)",
            color: "var(--ink-4)",
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={`POST ${ingestBase}/telemetry/ingest`}>
            POST {ingestBase}/telemetry/ingest
          </code>
          <button className="btn btn-sm btn-ghost"
            onClick={() => copy(`POST ${ingestBase}/telemetry/ingest\nAuthorization: Bearer ${apiKey}`)}
            style={{ padding: "2px 6px", fontSize: 10 }}>
            Copy curl
          </button>
        </div>
      )}
    </div>
  );
}

// ── Monitored Systems ──────────────────────────────────────────────────────────

const SYSTEM_BLANK = {
  display_name: "",
  server_name: "",
  server_type: "saviynt",
  description: "",
  active: true,
  governance_tiers: ["CRITICAL", "HIGH", "MEDIUM"],
  blocking_tools: "",
  alert_webhook: "",
};

function SystemForm({ initial, onSave, onCancel, saving }) {
  const [form, setForm] = useState(initial || SYSTEM_BLANK);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const toggleTier = (t) => {
    const cur = form.governance_tiers || [];
    set("governance_tiers", cur.includes(t) ? cur.filter(x => x !== t) : [...cur, t]);
  };

  const valid = form.display_name.trim() && form.server_name.trim();

  function handleSave() {
    if (!valid) return;
    const payload = {
      ...form,
      display_name: form.display_name.trim(),
      server_name: form.server_name.trim().toLowerCase(),
      blocking_tools: form.blocking_tools
        ? form.blocking_tools.split(",").map(s => s.trim()).filter(Boolean)
        : [],
      alert_webhook: form.alert_webhook.trim() || null,
      description: form.description.trim() || null,
    };
    onSave(payload);
  }

  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--acc)",
      borderRadius: 8,
      padding: "16px",
      display: "flex",
      flexDirection: "column",
      gap: 12,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--acc-ink)", marginBottom: 2 }}>
        {initial?.id ? "Edit system" : "Add system"}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">Display name *</label>
          <input className="input" value={form.display_name}
            onChange={e => set("display_name", e.target.value)}
            placeholder="e.g. Saviynt Production" />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">
            {form.server_type === UBO_MCP_TYPE ? "Server tag * — matches proxy --name" : "System identifier *"}
          </label>
          <input className="input" value={form.server_name}
            onChange={e => set("server_name", e.target.value)}
            placeholder={form.server_type === UBO_MCP_TYPE ? "edgar" : "saviynt-prod"}
            style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }} />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">System type</label>
          <select className="input" value={form.server_type} onChange={e => set("server_type", e.target.value)}>
            {UBO_SERVER_TYPES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">Status</label>
          <div style={{ display: "flex", gap: 6, alignItems: "center", paddingTop: 4 }}>
            <button type="button"
              className={"hitl-toggle" + (form.active ? " on" : "")}
              onClick={() => set("active", !form.active)}
              style={{ flex: 1 }}>
              <span className="hitl-toggle-dot">{form.active ? <Icon name="check" size={9}/> : null}</span>
              {form.active ? "Active" : "Inactive"}
            </button>
          </div>
        </div>
      </div>

      <div className="field" style={{ marginBottom: 0 }}>
        <label className="field-label">Governance tiers
          <span style={{ fontWeight: 400 }}> — tiers that trigger full council evaluation</span>
        </label>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {UBO_GOVERNANCE_TIERS.map(t => {
            const on = (form.governance_tiers || []).includes(t);
            const c = UBO_TIER_COLORS[t];
            return (
              <button key={t} type="button"
                className={"hitl-toggle" + (on ? " on" : "")}
                style={on ? { borderColor: c.ink, background: c.bg, color: c.ink } : {}}
                onClick={() => toggleTier(t)}>
                <span className="hitl-toggle-dot"
                  style={on ? { background: c.ink, borderColor: c.ink } : {}}>
                  {on ? <Icon name="check" size={9}/> : null}
                </span>
                {t}
              </button>
            );
          })}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: form.server_type === UBO_MCP_TYPE ? "1fr 1fr" : "1fr", gap: 10 }}>
        {form.server_type === UBO_MCP_TYPE && (
          <div className="field" style={{ marginBottom: 0 }}>
            <label className="field-label">Blocking tools override
              <span style={{ fontWeight: 400 }}> — comma-separated; empty = use global default</span>
            </label>
            <input className="input" value={form.blocking_tools}
              onChange={e => set("blocking_tools", e.target.value)}
              placeholder="shell,exec_sql,drop"
              style={{ fontFamily: "var(--mono, monospace)", fontSize: 11 }} />
          </div>
        )}
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">Alert webhook override
            <span style={{ fontWeight: 400 }}> — empty = use global</span>
          </label>
          <input className="input" value={form.alert_webhook}
            onChange={e => set("alert_webhook", e.target.value)}
            placeholder="https://hooks.slack.com/services/…"
            style={{ fontFamily: "var(--mono, monospace)", fontSize: 11 }} />
        </div>
      </div>

      <div className="field" style={{ marginBottom: 0 }}>
        <label className="field-label">Description (optional)</label>
        <textarea className="input" rows={2} value={form.description}
          onChange={e => set("description", e.target.value)}
          placeholder="Brief note about what this system does"
          style={{ resize: "vertical", fontSize: 12 }} />
      </div>

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn btn-sm btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn btn-sm btn-primary" onClick={handleSave}
          disabled={!valid || saving}>
          {saving ? "Saving…" : "Save system"}
        </button>
      </div>
    </div>
  );
}

function SystemRow({ sys, onEdit, onDelete, onToggle }) {
  const tiers = sys.governance_tiers || [];
  const isMcp = sys.server_type === UBO_MCP_TYPE;
  const age = sys.last_seen
    ? (() => {
        const ms = Date.now() - new Date(sys.last_seen).getTime();
        if (ms < 60000) return `${Math.round(ms / 1000)}s ago`;
        if (ms < 3600000) return `${Math.round(ms / 60000)}m ago`;
        if (ms < 86400000) return `${Math.round(ms / 3600000)}h ago`;
        return `${Math.round(ms / 86400000)}d ago`;
      })()
    : "—";

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "20px 1fr 110px 180px 140px 80px",
      alignItems: "start",
      gap: 10,
      padding: "10px 12px",
      borderBottom: "1px solid var(--line)",
      fontSize: 12,
      opacity: sys.active ? 1 : 0.55,
    }}>
      <div style={{ paddingTop: 2 }}><StatusDot active={sys.active} /></div>
      <div>
        <div style={{ fontWeight: 600 }}>{sys.display_name}</div>
        <div style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 1 }}>
          <span style={{
            fontFamily: "var(--mono, monospace)",
            background: "var(--acc-soft)",
            color: "var(--acc-ink)",
            padding: "1px 5px",
            borderRadius: 3,
            marginRight: 5,
          }}>{sys.server_name}</span>
          {UBO_SERVER_TYPES.find(t => t.id === sys.server_type)?.label || sys.server_type}
          {isMcp && <span style={{ marginLeft: 5, opacity: 0.6 }}>· proxy</span>}
        </div>
        {sys.description && (
          <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 2 }}>{sys.description}</div>
        )}
      </div>
      <div style={{ fontSize: 10, color: "var(--ink-4)", fontFamily: "var(--mono, monospace)", paddingTop: 2 }}>
        <div>{sys.total_calls || 0} events</div>
        <div style={{ color: sys.flagged_calls > 0 ? "var(--amber-ink)" : "var(--ink-4)" }}>
          {sys.flagged_calls || 0} flagged
        </div>
        <div>{age}</div>
      </div>
      <div style={{ paddingTop: 2 }}>
        {!isMcp && sys.ingest_api_key
          ? <ApiKeyDisplay apiKey={sys.ingest_api_key} ingestBase={_uboConfigBase()} />
          : <span style={{ fontSize: 10, color: "var(--ink-4)" }}>MCP proxy telemetry</span>
        }
      </div>
      <div style={{ display: "flex", gap: 3, flexWrap: "wrap", paddingTop: 2 }}>
        {tiers.map(t => <TierChip key={t} tier={t} />)}
      </div>
      <div style={{ display: "flex", gap: 5, justifyContent: "flex-end", paddingTop: 2 }}>
        <button className="btn btn-sm btn-ghost" onClick={() => onToggle(sys)}
          title={sys.active ? "Deactivate" : "Activate"}
          style={{ padding: "3px 7px", fontSize: 10 }}>
          {sys.active ? "Off" : "On"}
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => onEdit(sys)}
          style={{ padding: "3px 7px" }}>
          <Icon name="edit" size={11}/>
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => onDelete(sys.id)}
          style={{ padding: "3px 7px", color: "var(--red-ink)" }}>
          <Icon name="x" size={11}/>
        </button>
      </div>
    </div>
  );
}

function MonitoredSystemsCard() {
  const [systems, setSystems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  async function load() {
    setLoading(true);
    try {
      const res = await fetch(`${_uboConfigBase()}/systems`);
      if (res.ok) {
        const d = await res.json();
        setSystems(d.rows || []);
      }
    } catch (e) {
      setError("Could not reach api_server.py — ensure it is running.");
    }
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function handleSave(payload) {
    setSaving(true);
    try {
      const isEdit = !!editingId;
      const url = isEdit
        ? `${_uboConfigBase()}/systems/${editingId}`
        : `${_uboConfigBase()}/systems`;
      const res = await fetch(url, {
        method: isEdit ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        setAdding(false);
        setEditingId(null);
        await load();
      }
    } catch (e) {
      setError("Save failed — check api_server.py logs.");
    }
    setSaving(false);
  }

  async function handleDelete(id) {
    if (!confirm("Remove this system from UBO™ monitoring?")) return;
    try {
      await fetch(`${_uboConfigBase()}/systems/${id}`, { method: "DELETE" });
      await load();
    } catch (_) {}
  }

  async function handleToggle(sys) {
    try {
      await fetch(`${_uboConfigBase()}/systems/${sys.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...sys, active: !sys.active,
          governance_tiers: sys.governance_tiers || [],
          blocking_tools: (sys.blocking_tools || []).join(","),
        }),
      });
      await load();
    } catch (_) {}
  }

  const editingSystem = editingId
    ? systems.find(s => s.id === editingId)
    : null;
  const editInitial = editingSystem
    ? {
        ...editingSystem,
        blocking_tools: (editingSystem.blocking_tools || []).join(", "),
        alert_webhook: editingSystem.alert_webhook || "",
        description: editingSystem.description || "",
      }
    : null;

  return (
    <section className="cfg-card">
      <div className="cfg-card-head">
        <div>
          <div className="cfg-card-title">Monitored Systems</div>
          <div className="cfg-card-sub">
            Any system can send telemetry to the Dendrai UBO™ Governance Brain — Saviynt, SAP, Oracle Fusion,
            ServiceNow, Workday, Entra ID, GitHub, or any custom system. Each non-MCP system receives
            a unique ingest API key for <code style={{fontSize:10}}>POST /observability/telemetry/ingest</code>.
            MCP servers use the telemetry proxy instead.
          </div>
        </div>
        <button className="btn btn-sm" onClick={() => { setAdding(true); setEditingId(null); }}>
          <Icon name="plus" size={11}/> Add system
        </button>
      </div>

      {error && (
        <div style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 10 }}>{error}</div>
      )}

      {adding && !editingId && (
        <div style={{ marginBottom: 12 }}>
          <SystemForm
            initial={null}
            onSave={handleSave}
            onCancel={() => setAdding(false)}
            saving={saving} />
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: "center", padding: 20 }}>
          <span className="spin" style={{ display: "inline-block", width: 16, height: 16, borderWidth: 2 }}/>
        </div>
      ) : systems.length === 0 && !adding ? (
        <EmptyState
          icon="🛡"
          title="No systems registered"
          sub="Add any system — Saviynt, SAP, Oracle Fusion, ServiceNow, Workday, Entra ID, GitHub, or an MCP server — to start receiving Dendrai UBO™ Governance Brain coverage." />
      ) : (
        <>
          {systems.length > 0 && (
            <div style={{
              display: "grid",
              gridTemplateColumns: "20px 1fr 110px 180px 140px 80px",
              gap: 10,
              padding: "5px 12px 5px",
              fontSize: 10,
              color: "var(--ink-4)",
              letterSpacing: "0.05em",
              textTransform: "uppercase",
              fontWeight: 600,
            }}>
              <div/>
              <div>System</div>
              <div>Activity</div>
              <div>Ingest key</div>
              <div>Gov. tiers</div>
              <div/>
            </div>
          )}
          <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
            {systems.map(sys => (
              <React.Fragment key={sys.id}>
                <SystemRow
                  sys={sys}
                  onEdit={(s) => { setEditingId(s.id); setAdding(false); }}
                  onDelete={handleDelete}
                  onToggle={handleToggle} />
                {editingId === sys.id && (
                  <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--line)", background: "var(--surface-2)" }}>
                    <SystemForm
                      initial={editInitial}
                      onSave={handleSave}
                      onCancel={() => setEditingId(null)}
                      saving={saving} />
                  </div>
                )}
              </React.Fragment>
            ))}
          </div>
        </>
      )}

      <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 10, lineHeight: 1.5 }}>
        Activity counts combine <code>observability.mcp_telemetry</code> (MCP proxy) and
        <code> observability.system_telemetry</code> (REST ingest). Non-MCP systems push events to{" "}
        <code>POST /api/mcp/observability/telemetry/ingest</code> using their ingest API key as a Bearer token.
      </div>
    </section>
  );
}

// ── PAC Repositories ───────────────────────────────────────────────────────────

const REPO_BLANK = {
  display_name: "",
  provider: "github",
  repo_url: "",
  branch: "main",
  rego_path: "policies/",
  process: "all",
  description: "",
  active: true,
};

function RepoForm({ initial, onSave, onCancel, saving }) {
  const [form, setForm] = useState(initial || REPO_BLANK);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const valid = form.display_name.trim() && form.repo_url.trim();

  function handleSave() {
    if (!valid) return;
    onSave({
      ...form,
      display_name: form.display_name.trim(),
      repo_url: form.repo_url.trim(),
      branch: form.branch.trim() || "main",
      rego_path: form.rego_path.trim() || "policies/",
      description: form.description.trim() || null,
    });
  }

  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--acc)",
      borderRadius: 8,
      padding: 16,
      display: "flex",
      flexDirection: "column",
      gap: 12,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--acc-ink)", marginBottom: 2 }}>
        {initial?.id ? "Edit repository" : "Add repository"}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">Display name *</label>
          <input className="input" value={form.display_name}
            onChange={e => set("display_name", e.target.value)}
            placeholder="e.g. Acme Corp Policy Repo" />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">Provider</label>
          <select className="input" value={form.provider} onChange={e => set("provider", e.target.value)}>
            {PAC_PROVIDERS.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </div>
      </div>

      <div className="field" style={{ marginBottom: 0 }}>
        <label className="field-label">Repository URL *</label>
        <input className="input" value={form.repo_url}
          onChange={e => set("repo_url", e.target.value)}
          placeholder="https://github.com/org/repo"
          style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">Branch</label>
          <input className="input" value={form.branch}
            onChange={e => set("branch", e.target.value)}
            placeholder="main"
            style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }} />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">Rego path within repo</label>
          <input className="input" value={form.rego_path}
            onChange={e => set("rego_path", e.target.value)}
            placeholder="policies/"
            style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }} />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">Linked process</label>
          <select className="input" value={form.process} onChange={e => set("process", e.target.value)}>
            {PAC_PROCESSES.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 10, alignItems: "end" }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">Description (optional)</label>
          <input className="input" value={form.description}
            onChange={e => set("description", e.target.value)}
            placeholder="Brief note about this repository" />
        </div>
        <div>
          <label className="field-label">Status</label>
          <button type="button"
            className={"hitl-toggle" + (form.active ? " on" : "")}
            onClick={() => set("active", !form.active)}>
            <span className="hitl-toggle-dot">{form.active ? <Icon name="check" size={9}/> : null}</span>
            {form.active ? "Active" : "Inactive"}
          </button>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn btn-sm btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn btn-sm btn-primary" onClick={handleSave}
          disabled={!valid || saving}>
          {saving ? "Saving…" : "Save repository"}
        </button>
      </div>
    </div>
  );
}

const PROVIDER_BADGE_COLORS = {
  github:      { bg: "#f0f6ff", ink: "#1a6cba" },
  gitlab:      { bg: "#fff0e8", ink: "#c04700" },
  bitbucket:   { bg: "#e8f0ff", ink: "#1444b8" },
  "azure-devops": { bg: "#e8f4ff", ink: "#0065a0" },
  custom:      { bg: "var(--surface-2)", ink: "var(--ink-3)" },
};

function RepoRow({ repo, onEdit, onDelete, onToggle }) {
  const pColor = PROVIDER_BADGE_COLORS[repo.provider] || PROVIDER_BADGE_COLORS.custom;
  const process = PAC_PROCESSES.find(p => p.id === repo.process)?.label || repo.process;
  const shortUrl = repo.repo_url.replace(/^https?:\/\//, "").replace(/\.git$/, "");

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "20px 1fr 110px 140px 130px 90px",
      alignItems: "center",
      gap: 10,
      padding: "8px 12px",
      borderBottom: "1px solid var(--line)",
      fontSize: 12,
      opacity: repo.active ? 1 : 0.55,
    }}>
      <StatusDot active={repo.active} />
      <div>
        <div style={{ fontWeight: 600 }}>{repo.display_name}</div>
        <div style={{
          fontSize: 10,
          color: "var(--acc-ink)",
          fontFamily: "var(--mono, monospace)",
          marginTop: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }} title={repo.repo_url}>
          {shortUrl}
        </div>
        {repo.description && (
          <div style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 1 }}>{repo.description}</div>
        )}
      </div>
      <div>
        <span style={{
          display: "inline-block",
          padding: "2px 7px",
          borderRadius: 4,
          fontSize: 10,
          fontWeight: 600,
          background: pColor.bg,
          color: pColor.ink,
        }}>
          {PAC_PROVIDERS.find(p => p.id === repo.provider)?.label || repo.provider}
        </span>
      </div>
      <div style={{ fontSize: 11 }}>
        <span style={{
          fontFamily: "var(--mono, monospace)",
          background: "var(--surface-2)",
          padding: "1px 5px",
          borderRadius: 3,
          fontSize: 10,
        }}>
          {repo.branch}
        </span>
        <span style={{ color: "var(--ink-3)", marginLeft: 4 }}>{repo.rego_path}</span>
      </div>
      <div style={{ fontSize: 11, color: "var(--ink-2)" }}>{process}</div>
      <div style={{ display: "flex", gap: 5, justifyContent: "flex-end" }}>
        <button className="btn btn-sm btn-ghost" onClick={() => onToggle(repo)}
          style={{ padding: "3px 7px", fontSize: 10 }}>
          {repo.active ? "Off" : "On"}
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => onEdit(repo)}
          style={{ padding: "3px 7px" }}>
          <Icon name="edit" size={11}/>
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => onDelete(repo.id)}
          style={{ padding: "3px 7px", color: "var(--red-ink)" }}>
          <Icon name="x" size={11}/>
        </button>
      </div>
    </div>
  );
}

function PacReposCard() {
  const [repos, setRepos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  async function load() {
    setLoading(true);
    try {
      const res = await fetch(`${_uboConfigBase()}/pac-repos`);
      if (res.ok) {
        const d = await res.json();
        setRepos(d.rows || []);
      }
    } catch (e) {
      setError("Could not reach api_server.py — ensure it is running.");
    }
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function handleSave(payload) {
    setSaving(true);
    try {
      const isEdit = !!editingId;
      const url = isEdit
        ? `${_uboConfigBase()}/pac-repos/${editingId}`
        : `${_uboConfigBase()}/pac-repos`;
      const res = await fetch(url, {
        method: isEdit ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        setAdding(false);
        setEditingId(null);
        await load();
      }
    } catch (_) {
      setError("Save failed — check api_server.py logs.");
    }
    setSaving(false);
  }

  async function handleDelete(id) {
    if (!confirm("Remove this repository?")) return;
    try {
      await fetch(`${_uboConfigBase()}/pac-repos/${id}`, { method: "DELETE" });
      await load();
    } catch (_) {}
  }

  async function handleToggle(repo) {
    try {
      await fetch(`${_uboConfigBase()}/pac-repos/${repo.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...repo, active: !repo.active }),
      });
      await load();
    } catch (_) {}
  }

  const editingRepo = editingId ? repos.find(r => r.id === editingId) : null;
  const editInitial = editingRepo
    ? { ...editingRepo, description: editingRepo.description || "" }
    : null;

  return (
    <section className="cfg-card">
      <div className="cfg-card-head">
        <div>
          <div className="cfg-card-title">Policy-as-Code Repositories</div>
          <div className="cfg-card-sub">
            Source repositories where Rego policy modules are maintained.
            These are the authoritative references for the five Oracle Fusion ERP process policies
            (ITGC, O2C, P2P, R2S, R2R).
          </div>
        </div>
        <button className="btn btn-sm" onClick={() => { setAdding(true); setEditingId(null); }}>
          <Icon name="plus" size={11}/> Add repository
        </button>
      </div>

      {error && (
        <div style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 10 }}>{error}</div>
      )}

      {adding && !editingId && (
        <div style={{ marginBottom: 12 }}>
          <RepoForm
            initial={null}
            onSave={handleSave}
            onCancel={() => setAdding(false)}
            saving={saving} />
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: "center", padding: 20 }}>
          <span className="spin" style={{ display: "inline-block", width: 16, height: 16, borderWidth: 2 }}/>
        </div>
      ) : repos.length === 0 && !adding ? (
        <EmptyState
          icon="📁"
          title="No repositories registered"
          sub="Add the Git repositories that contain your Rego policy modules. Supports GitHub, GitLab, Bitbucket, and Azure DevOps." />
      ) : (
        <>
          {repos.length > 0 && (
            <div style={{
              display: "grid",
              gridTemplateColumns: "20px 1fr 110px 140px 130px 90px",
              gap: 10,
              padding: "5px 12px 5px",
              fontSize: 10,
              color: "var(--ink-4)",
              letterSpacing: "0.05em",
              textTransform: "uppercase",
              fontWeight: 600,
            }}>
              <div/><div>Repository</div><div>Provider</div><div>Branch / Path</div><div>Process</div><div/>
            </div>
          )}
          <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
            {repos.map(repo => (
              <React.Fragment key={repo.id}>
                <RepoRow
                  repo={repo}
                  onEdit={(r) => { setEditingId(r.id); setAdding(false); }}
                  onDelete={handleDelete}
                  onToggle={handleToggle} />
                {editingId === repo.id && (
                  <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--line)", background: "var(--surface-2)" }}>
                    <RepoForm
                      initial={editInitial}
                      onSave={handleSave}
                      onCancel={() => setEditingId(null)}
                      saving={saving} />
                  </div>
                )}
              </React.Fragment>
            ))}
          </div>
        </>
      )}

      <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 10, lineHeight: 1.5 }}>
        Repository registration is for tracking and reference. Push-on-save integration uses the
        GitHub hook configured under <b>Policy-as-Code → Hooks</b>.
      </div>
    </section>
  );
}

// ── Main screen ────────────────────────────────────────────────────────────────

function UboConfigScreen() {
  return (
    <div className="cfg-screen" data-screen-label="UBO Configuration">
      <div className="panel-head">
        <div>
          <div className="kicker">Configuration</div>
          <div className="panel-title mt-8">Dendrai UBO™ Configuration</div>
          <div className="panel-sub">
            Register the systems monitored by the Dendrai UBO™ Governance Brain and the Policy-as-Code repositories
            that are the authoritative source of Rego policy modules.
          </div>
        </div>
      </div>

      <div className="cfg-grid" style={{ gridTemplateColumns: "1fr" }}>
        <MonitoredSystemsCard />
        <PacReposCard />
      </div>
    </div>
  );
}

Object.assign(window, { UboConfigScreen });
