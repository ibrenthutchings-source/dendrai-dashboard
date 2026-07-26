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

// Fallback before GET /api/pac/processes resolves — processes are DB-backed
// now (sync_github can auto-register new ones), not a fixed 5. "all" is a
// synthetic option this screen adds itself, not a real process id.
const PAC_PROCESSES_FALLBACK = [
  { id: "all",             label: "All processes" },
  { id: "itgc",            label: "ITGC" },
  { id: "order_to_cash",   label: "Order-to-Cash" },
  { id: "procure_to_pay",  label: "Procure-to-Pay" },
  { id: "receive_to_ship", label: "Receive-to-Ship" },
  { id: "record_to_report",label: "Record-to-Report" },
];

// Connector type metadata — drives the dynamic credential form in
// ConnectorForm below. One entry per adapter registered in
// connector_poller.py's _ADAPTERS.
const CONNECTOR_TYPES = [
  { id: "oracle_fusion", label: "Oracle Fusion ERP",
    baseUrlPlaceholder: "https://mycompany.fa.us6.oraclecloud.com",
    credentialFields: [
      { key: "username", label: "Username", type: "text" },
      { key: "password", label: "Password", type: "password" },
      { key: "client_id", label: "OAuth Client ID (optional — overrides username/password)", type: "text" },
      { key: "client_secret", label: "OAuth Client Secret", type: "password" },
    ],
    extraFields: [] },
  { id: "sap_hana", label: "SAP HANA",
    baseUrlPlaceholder: "myhana.example.com",
    credentialFields: [
      { key: "username", label: "Username", type: "text" },
      { key: "password", label: "Password", type: "password" },
    ],
    extraFields: [ { key: "port", label: "Port", type: "text", placeholder: "30015" } ] },
  { id: "sailpoint", label: "SailPoint Identity Security Cloud",
    baseUrlPlaceholder: "https://mycompany.api.identitynow.com",
    credentialFields: [
      { key: "client_id", label: "Client ID", type: "text" },
      { key: "client_secret", label: "Client Secret", type: "password" },
    ],
    extraFields: [] },
  { id: "dynamics365", label: "Microsoft Dynamics 365",
    baseUrlPlaceholder: "https://myorg.crm.dynamics.com",
    credentialFields: [
      { key: "client_id", label: "Client ID", type: "text" },
      { key: "client_secret", label: "Client Secret", type: "password" },
    ],
    extraFields: [ { key: "tenant_id", label: "Azure AD Tenant ID", type: "text" } ] },
  { id: "netsuite", label: "NetSuite",
    baseUrlPlaceholder: "https://ACCOUNTID.suitetalk.api.netsuite.com",
    credentialFields: [
      { key: "consumer_key", label: "Consumer Key", type: "text" },
      { key: "consumer_secret", label: "Consumer Secret", type: "password" },
      { key: "token_id", label: "Token ID", type: "text" },
      { key: "token_secret", label: "Token Secret", type: "password" },
    ],
    extraFields: [ { key: "account_id", label: "Account ID", type: "text", placeholder: "1234567 or 1234567_SB1" } ] },
  // DevOps Monitoring: branch-protection/CODEOWNERS auditing (scm_audit_endpoints.py,
  // github_scm_tool.py/gitlab_scm_tool.py). Registered here like every other connector;
  // results and on-demand audits surface on the DevOps Monitoring screen.
  { id: "github_scm", label: "GitHub (SCM Audit)",
    baseUrlPlaceholder: "https://api.github.com (leave blank for github.com)",
    baseUrlOptional: true,
    credentialFields: [
      { key: "token", label: "Personal Access Token", type: "password" },
    ],
    extraFields: [
      { key: "repo_full_name", label: "Repository (owner/repo)", type: "text", placeholder: "my-org/my-repo" },
      { key: "branch", label: "Branch", type: "text", placeholder: "main" },
    ] },
  { id: "gitlab_scm", label: "GitLab (SCM Audit)",
    baseUrlPlaceholder: "https://gitlab.com/api/v4 (leave blank for gitlab.com)",
    baseUrlOptional: true,
    credentialFields: [
      { key: "token", label: "Personal/Project Access Token", type: "password" },
    ],
    extraFields: [
      { key: "project_ref", label: "Project (namespace/project or numeric ID)", type: "text", placeholder: "my-group/my-project" },
      { key: "branch", label: "Branch", type: "text", placeholder: "main" },
    ] },
  // DevOps Monitoring: ITSM/Jira-ServiceNow SLA Bridge (itsm_endpoints.py,
  // itsm_jira_tool.py/itsm_servicenow_tool.py). Credentials here are used both
  // to open real tickets (POST /itsm/tickets) and to poll ticket status back.
  { id: "itsm_jira", label: "Jira (ITSM SLA Bridge)",
    baseUrlPlaceholder: "https://mycompany.atlassian.net",
    credentialFields: [
      { key: "email", label: "Account Email", type: "text" },
      { key: "api_token", label: "API Token", type: "password" },
    ],
    extraFields: [
      { key: "project_key", label: "Project Key", type: "text", placeholder: "SEC" },
    ] },
  { id: "itsm_servicenow", label: "ServiceNow (ITSM SLA Bridge)",
    baseUrlPlaceholder: "https://mycompany.service-now.com",
    credentialFields: [
      { key: "username", label: "Username", type: "text" },
      { key: "password", label: "Password", type: "password" },
    ],
    extraFields: [] },
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
            {form.server_type === UBO_MCP_TYPE ? "Server tag * — must match the connector's configured name" : "System identifier *"}
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
            ServiceNow, Workday, Entra ID, GitHub, an AI agent framework (LangChain, OpenAI function calling,
            a custom agent loop), or any other custom system. Each receives a unique ingest API key and is
            adjudicated by the same review pipeline as MCP tool calls. MCP servers use the telemetry proxy
            instead.
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
        Activity counts combine MCP proxy events with events pushed directly by other systems using their
        ingest API key.
      </div>
    </section>
  );
}

// ── Poll-Based Connectors ────────────────────────────────────────────────────
// The inverse of Monitored Systems above: those are push-model (the external
// system authenticates to us); these are pull-model (we authenticate to
// them, so we hold — encrypted — their credentials). Configured entirely
// here, no env vars; connector_poller.py's background loop polls whichever
// of these are active on their own poll_interval_s.

const CONNECTOR_BLANK = {
  connector_type: "oracle_fusion",
  display_name: "",
  base_url: "",
  poll_interval_s: 1800,
  credentials: {},
  extra_config: {},
};

function ConnectorForm({ initial, onSave, onCancel, saving }) {
  const [form, setForm] = useState(initial
    ? { ...initial, credentials: {}, extra_config: initial.extra_config || {} }
    : CONNECTOR_BLANK);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const setCred = (k, v) => setForm(f => ({ ...f, credentials: { ...f.credentials, [k]: v } }));
  const setExtra = (k, v) => setForm(f => ({ ...f, extra_config: { ...f.extra_config, [k]: v } }));

  const typeInfo = CONNECTOR_TYPES.find(t => t.id === form.connector_type) || CONNECTOR_TYPES[0];
  const isEdit = !!initial?.id;
  const valid = form.display_name.trim() && (typeInfo.baseUrlOptional || form.base_url.trim()) &&
    (isEdit || typeInfo.credentialFields.some(f => (form.credentials[f.key] || "").trim()));

  function handleSave() {
    if (!valid) return;
    const hasAnyCred = Object.values(form.credentials).some(v => (v || "").trim());
    onSave({
      connector_type: form.connector_type,
      display_name: form.display_name.trim(),
      base_url: form.base_url.trim() || null,
      auth_type: form.connector_type, // one auth scheme per connector type in this framework
      poll_interval_s: Number(form.poll_interval_s) || 1800,
      extra_config: form.extra_config,
      // Omit credentials entirely on edit if the user left them blank —
      // update_poll_connector keeps the existing encrypted value in that case.
      ...(hasAnyCred ? { credentials: form.credentials } : {}),
    });
  }

  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--acc)", borderRadius: 8, padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--acc-ink)", marginBottom: 2 }}>
        {isEdit ? "Edit connector" : "Add connector"}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">System type</label>
          <select className="input" value={form.connector_type}
            onChange={e => set("connector_type", e.target.value)}>
            {CONNECTOR_TYPES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">Display name *</label>
          <input className="input" value={form.display_name}
            onChange={e => set("display_name", e.target.value)}
            placeholder="e.g. Oracle Fusion Production" />
        </div>
      </div>

      <div className="field" style={{ marginBottom: 0 }}>
        <label className="field-label">Base URL / host{typeInfo.baseUrlOptional ? "" : " *"}</label>
        <input className="input" value={form.base_url}
          onChange={e => set("base_url", e.target.value)}
          placeholder={typeInfo.baseUrlPlaceholder}
          style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }} />
      </div>

      {typeInfo.extraFields.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: `repeat(${typeInfo.extraFields.length}, 1fr)`, gap: 10 }}>
          {typeInfo.extraFields.map(f => (
            <div className="field" key={f.key} style={{ marginBottom: 0 }}>
              <label className="field-label">{f.label}</label>
              <input className="input" value={form.extra_config[f.key] || ""}
                onChange={e => setExtra(f.key, e.target.value)}
                placeholder={f.placeholder || ""}
                style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }} />
            </div>
          ))}
        </div>
      )}

      <div className="field" style={{ marginBottom: 0 }}>
        <label className="field-label">
          Credentials {isEdit && <span style={{ fontWeight: 400 }}>— leave blank to keep the existing ones (never shown once saved)</span>}
        </label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          {typeInfo.credentialFields.map(f => (
            <input key={f.key} className="input" type={f.type} value={form.credentials[f.key] || ""}
              onChange={e => setCred(f.key, e.target.value)}
              placeholder={isEdit ? "•••• (unchanged)" : f.label}
              style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }} />
          ))}
        </div>
      </div>

      <div className="field" style={{ marginBottom: 0, maxWidth: 220 }}>
        <label className="field-label">Poll interval (seconds)</label>
        <input className="input" type="number" min={60} value={form.poll_interval_s}
          onChange={e => set("poll_interval_s", e.target.value)}
          style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }} />
      </div>

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn btn-sm btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn btn-sm btn-primary" onClick={handleSave} disabled={!valid || saving}>
          {saving ? "Saving…" : "Save connector"}
        </button>
      </div>
    </div>
  );
}

function ConnectorRow({ conn, onEdit, onDelete, onToggle, onTest, testState }) {
  const typeInfo = CONNECTOR_TYPES.find(t => t.id === conn.connector_type);
  const age = conn.last_poll_at
    ? (() => {
        const ms = Date.now() - new Date(conn.last_poll_at).getTime();
        if (ms < 60000) return `${Math.round(ms / 1000)}s ago`;
        if (ms < 3600000) return `${Math.round(ms / 60000)}m ago`;
        if (ms < 86400000) return `${Math.round(ms / 3600000)}h ago`;
        return `${Math.round(ms / 86400000)}d ago`;
      })()
    : "never polled";

  return (
    <div style={{
      display: "grid", gridTemplateColumns: "20px 1fr 130px 130px 150px",
      alignItems: "start", gap: 10, padding: "10px 12px",
      borderBottom: "1px solid var(--line)", fontSize: 12, opacity: conn.active ? 1 : 0.55,
    }}>
      <div style={{ paddingTop: 2 }}><StatusDot active={conn.active} /></div>
      <div>
        <div style={{ fontWeight: 600 }}>{conn.display_name}</div>
        <div style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 1 }}>
          <span style={{ fontFamily: "var(--mono, monospace)", background: "var(--acc-soft)", color: "var(--acc-ink)", padding: "1px 5px", borderRadius: 3, marginRight: 5 }}>
            {typeInfo?.label || conn.connector_type}
          </span>
          {conn.base_url}
        </div>
      </div>
      <div style={{ fontSize: 10, color: "var(--ink-4)", fontFamily: "var(--mono, monospace)", paddingTop: 2 }}>
        <div>every {Math.round((conn.poll_interval_s || 1800) / 60)}m</div>
        <div style={{ color: conn.last_poll_status === "error" ? "var(--red-ink)" : "var(--ink-4)" }}>{age}</div>
      </div>
      <div style={{ fontSize: 10, paddingTop: 2 }}>
        {testState?.testing ? (
          <span className="spin" style={{ display: "inline-block", width: 11, height: 11, borderWidth: 2 }} />
        ) : testState?.result ? (
          <span style={{ color: testState.result.ok ? "var(--green-ink)" : "var(--red-ink)" }} title={testState.result.message}>
            {testState.result.ok ? "✓ " : "✗ "}{(testState.result.message || "").slice(0, 40)}
          </span>
        ) : conn.last_poll_status === "error" ? (
          <span style={{ color: "var(--red-ink)" }} title={conn.last_poll_error}>Last poll failed</span>
        ) : conn.last_poll_status === "ok" ? (
          <span style={{ color: "var(--green-ink)" }}>Last poll OK</span>
        ) : (
          <span style={{ color: "var(--ink-4)" }}>—</span>
        )}
      </div>
      <div style={{ display: "flex", gap: 5, justifyContent: "flex-end", paddingTop: 2, flexWrap: "wrap" }}>
        <button className="btn btn-sm btn-ghost" onClick={() => onTest(conn)} disabled={testState?.testing}
          style={{ padding: "3px 7px", fontSize: 10 }}>
          Test
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => onToggle(conn)}
          title={conn.active ? "Deactivate" : "Activate"} style={{ padding: "3px 7px", fontSize: 10 }}>
          {conn.active ? "Off" : "On"}
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => onEdit(conn)} style={{ padding: "3px 7px" }}>
          <Icon name="edit" size={11}/>
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => onDelete(conn.id)} style={{ padding: "3px 7px", color: "var(--red-ink)" }}>
          <Icon name="x" size={11}/>
        </button>
      </div>
    </div>
  );
}

function PollConnectorsCard() {
  const [connectors, setConnectors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [testStates, setTestStates] = useState({}); // { [id]: { testing, result } }

  async function load() {
    setLoading(true);
    try {
      const res = await fetch(`${_uboConfigBase()}/connectors`);
      if (res.ok) { const d = await res.json(); setConnectors(d.rows || []); }
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
      const url = isEdit ? `${_uboConfigBase()}/connectors/${editingId}` : `${_uboConfigBase()}/connectors`;
      const res = await fetch(url, {
        method: isEdit ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) { setAdding(false); setEditingId(null); await load(); }
      else { const d = await res.json().catch(() => ({})); setError(d.detail || "Save failed."); }
    } catch (e) {
      setError("Save failed — check api_server.py logs.");
    }
    setSaving(false);
  }

  async function handleDelete(id) {
    if (!confirm("Remove this connector? Its stored credentials will be permanently deleted.")) return;
    try {
      await fetch(`${_uboConfigBase()}/connectors/${id}`, { method: "DELETE" });
      await load();
    } catch (_) {}
  }

  async function handleToggle(conn) {
    try {
      await fetch(`${_uboConfigBase()}/connectors/${conn.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: !conn.active }),
      });
      await load();
    } catch (_) {}
  }

  async function handleTest(conn) {
    setTestStates(s => ({ ...s, [conn.id]: { testing: true, result: null } }));
    try {
      const res = await fetch(`${_uboConfigBase()}/connectors/${conn.id}/test`, { method: "POST" });
      const d = await res.json().catch(() => ({ ok: false, message: `HTTP ${res.status}` }));
      setTestStates(s => ({ ...s, [conn.id]: { testing: false, result: d } }));
    } catch (e) {
      setTestStates(s => ({ ...s, [conn.id]: { testing: false, result: { ok: false, message: e.message } } }));
    }
  }

  const editingConn = editingId ? connectors.find(c => c.id === editingId) : null;

  return (
    <section className="cfg-card">
      <div className="cfg-card-head">
        <div>
          <div className="cfg-card-title">Poll-Based Connectors</div>
          <div className="cfg-card-sub">
            Systems the Dendrai UBO™ Governance Brain polls on a schedule rather than receiving pushed
            telemetry from — Oracle Fusion, SAP HANA, SailPoint, Dynamics 365, NetSuite. Credentials are
            encrypted at rest and never shown again once saved.
          </div>
        </div>
        <button className="btn btn-sm" onClick={() => { setAdding(true); setEditingId(null); }}>
          <Icon name="plus" size={11}/> Add connector
        </button>
      </div>

      {error && <div style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 10 }}>{error}</div>}

      {adding && !editingId && (
        <div style={{ marginBottom: 12 }}>
          <ConnectorForm initial={null} onSave={handleSave} onCancel={() => setAdding(false)} saving={saving} />
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: "center", padding: 20 }}>
          <span className="spin" style={{ display: "inline-block", width: 16, height: 16, borderWidth: 2 }}/>
        </div>
      ) : connectors.length === 0 && !adding ? (
        <EmptyState
          icon="🔌"
          title="No connectors configured"
          sub="Add Oracle Fusion, SAP HANA, SailPoint, Dynamics 365, or NetSuite to start pulling audit events into the Dendrai UBO™ Governance Brain on a schedule." />
      ) : (
        <>
          {connectors.length > 0 && (
            <div style={{
              display: "grid", gridTemplateColumns: "20px 1fr 130px 130px 150px", gap: 10,
              padding: "5px 12px 5px", fontSize: 10, color: "var(--ink-4)",
              letterSpacing: "0.05em", textTransform: "uppercase", fontWeight: 600,
            }}>
              <div/><div>Connector</div><div>Poll cadence</div><div>Last result</div><div/>
            </div>
          )}
          <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
            {connectors.map(conn => (
              <React.Fragment key={conn.id}>
                <ConnectorRow
                  conn={conn}
                  onEdit={(c) => { setEditingId(c.id); setAdding(false); }}
                  onDelete={handleDelete}
                  onToggle={handleToggle}
                  onTest={handleTest}
                  testState={testStates[conn.id]} />
                {editingId === conn.id && (
                  <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--line)", background: "var(--surface-2)" }}>
                    <ConnectorForm
                      initial={editingConn}
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
        Polled events are ingested the same way pushed telemetry is (via <code>observability.system_telemetry</code>),
        so they're picked up by the same adjudication pipeline — no separate review flow.
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
  token: "",
};

function RepoForm({ initial, onSave, onCancel, saving, processes }) {
  const [form, setForm] = useState(initial ? { ...initial, token: "" } : REPO_BLANK);
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
      token: form.token.trim(), // blank = keep whatever's already saved (write-only field)
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

      <div className="field" style={{ marginBottom: 0 }}>
        <label className="field-label">Personal Access Token {form.provider !== "github" && "(GitHub only, for now)"}</label>
        <input className="input" type="password" value={form.token}
          onChange={e => set("token", e.target.value)}
          disabled={form.provider !== "github"}
          placeholder={initial?.has_token ? "•••• saved — leave blank to keep" : "ghp_••••••••••••••••"}
          style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }} />
        <div style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 3 }}>
          Required to use "Sync Now" on this repository. Stored encrypted server-side; never displayed again.
        </div>
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
            {(processes || PAC_PROCESSES_FALLBACK).map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
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

function RepoRow({ repo, onEdit, onDelete, onToggle, onSync, syncing, syncResult, processes }) {
  const pColor = PROVIDER_BADGE_COLORS[repo.provider] || PROVIDER_BADGE_COLORS.custom;
  const process = (processes || PAC_PROCESSES_FALLBACK).find(p => p.id === repo.process)?.label || repo.process;
  const shortUrl = repo.repo_url.replace(/^https?:\/\//, "").replace(/\.git$/, "");
  const syncable = repo.provider === "github";

  return (
    <div style={{ borderBottom: "1px solid var(--line)", opacity: repo.active ? 1 : 0.55 }}>
    <div style={{
      display: "grid",
      gridTemplateColumns: "20px 1fr 110px 140px 130px 90px",
      alignItems: "center",
      gap: 10,
      padding: "8px 12px",
      fontSize: 12,
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

    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "0 12px 8px 42px", fontSize: 10, color: "var(--ink-3)",
    }}>
      {syncable ? (
        <>
          <button className="btn btn-sm" disabled={syncing || !repo.has_token}
            onClick={() => onSync(repo)}
            title={repo.has_token ? "Pull the latest policy files from this repo" : "Add a Personal Access Token first (Edit)"}
            style={{ padding: "2px 8px", fontSize: 10 }}>
            {syncing ? "Syncing…" : "Sync Now"}
          </button>
          {!repo.has_token && <span>No token saved — click Edit to add one</span>}
          {repo.has_token && repo.last_sync_status === "ok" && repo.last_synced_at && (
            <span style={{ color: "var(--acc-ink)" }}>✓ Last synced {new Date(repo.last_synced_at).toLocaleString()}</span>
          )}
          {repo.last_sync_status === "error" && (
            <span style={{ color: "var(--red-ink)" }} title={repo.last_sync_error}>✗ Last sync failed</span>
          )}
          {repo.has_token && !repo.last_synced_at && <span>Never synced</span>}
        </>
      ) : (
        <span>Sync isn't supported yet for {PAC_PROVIDERS.find(p => p.id === repo.provider)?.label || repo.provider} — GitHub only for now</span>
      )}
    </div>
    {syncResult && (
      <div className="mono" style={{
        fontSize: 10, lineHeight: 1.6, margin: "0 12px 10px 42px", padding: "6px 9px", borderRadius: 6,
        background: syncResult.error ? "var(--red-soft, rgba(239,68,68,0.08))" : "var(--surface-2, var(--surface))",
        border: "1px solid var(--line)", color: syncResult.error ? "var(--red)" : "var(--ink-2)",
      }}>
        {syncResult.error ? (
          <>Sync failed: {syncResult.error}</>
        ) : (
          <>
            Found {syncResult.files_found} file{syncResult.files_found === 1 ? "" : "s"} in {syncResult.repo}@{syncResult.branch}:{syncResult.path}
            {syncResult.imported?.length > 0 && (
              <div>✓ Imported: {syncResult.imported.map(m => `${m.process}${syncResult.newly_registered?.includes(m.process) ? " (new tab)" : ""} (${m.file_count} file${m.file_count === 1 ? "" : "s"})`).join(", ")}</div>
            )}
            {syncResult.skipped?.length > 0 && (
              <div style={{ color: "var(--amber-ink, #b45309)" }}>Skipped: {syncResult.skipped.map(s => `${s.name} (${s.reason})`).join("; ")}</div>
            )}
          </>
        )}
      </div>
    )}
    </div>
  );
}

function PacReposCard() {
  const [repos, setRepos] = useState([]);
  const [processes, setProcesses] = useState(PAC_PROCESSES_FALLBACK);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [syncingId, setSyncingId] = useState(null);
  const [syncResults, setSyncResults] = useState({}); // { [repoId]: { ...result } | { error } }

  useEffect(() => {
    fetch("/api/pac/processes")
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.processes?.length) {
          setProcesses([{ id: "all", label: "All processes" }, ...d.processes.map(p => ({ id: p.id, label: p.label }))]);
        }
      })
      .catch(() => {});
  }, []);

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
    setError(null);
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
      // POST/PUT /pac-repos always return HTTP 200, even when the DB write
      // itself failed (_create_pac_repo/_update_pac_repo swallow exceptions
      // and return None/False, encoded as {ok:false} in the body) — checking
      // only res.ok made a silent DB failure look identical to success: the
      // form appeared to do nothing and no row ever showed up, with no error
      // surfaced anywhere.
      const d = await res.json().catch(() => null);
      if (res.ok && d?.ok !== false) {
        setAdding(false);
        setEditingId(null);
        await load();
      } else {
        setError(d?.detail || "Save failed — the repository was not persisted. Check api_server.py logs.");
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

  async function handleSync(repo) {
    setSyncingId(repo.id);
    setSyncResults(r => ({ ...r, [repo.id]: null }));
    try {
      const res = await fetch(`${_uboConfigBase()}/pac-repos/${repo.id}/sync`, { method: "POST" });
      const d = await res.json().catch(() => null);
      if (!res.ok) {
        setSyncResults(r => ({ ...r, [repo.id]: { error: d?.detail || `Sync failed (HTTP ${res.status})` } }));
      } else {
        setSyncResults(r => ({ ...r, [repo.id]: d }));
      }
      await load();
    } catch (e) {
      setSyncResults(r => ({ ...r, [repo.id]: { error: e.message || "Network error" } }));
    }
    setSyncingId(null);
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
            saving={saving}
            processes={processes} />
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
                  onToggle={handleToggle}
                  onSync={handleSync}
                  syncing={syncingId === repo.id}
                  syncResult={syncResults[repo.id]}
                  processes={processes} />
                {editingId === repo.id && (
                  <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--line)", background: "var(--surface-2)" }}>
                    <RepoForm
                      initial={editInitial}
                      onSave={handleSave}
                      onCancel={() => setEditingId(null)}
                      saving={saving}
                      processes={processes} />
                  </div>
                )}
              </React.Fragment>
            ))}
          </div>
        </>
      )}

      <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 10, lineHeight: 1.5 }}>
        Click <b>Sync Now</b> on a repository row to pull its .rego/.md/.txt files into the Rego
        Editor — each registered repo syncs independently of the single legacy GitHub hook under
        <b> Policy-as-Code Engine → External Sources</b>.
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
        <PollConnectorsCard />
        <PacReposCard />
      </div>
    </div>
  );
}

Object.assign(window, { UboConfigScreen });
