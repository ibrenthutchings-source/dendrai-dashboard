/* ============================================================
   AI System Inventory — the register a NIST AI RMF "Map" function
   or an EU AI Act system inventory expects: every AI-adjacent system
   Dendrai governs (push-based monitored_systems + pull-based
   poll_connectors), each tagged with a risk tier, data sensitivity,
   and named owner. Classification is edited inline here and saved
   via the dedicated .../classification endpoints (partial update —
   never touches a system's other configuration).

   Data comes from GET /api/mcp/observability/ai-inventory.
   ============================================================ */

function _aiInvBase() {
  return (window.MCP_API_BASE || "/api/mcp") + "/observability";
}

const _RISK_TIER_META = {
  critical: { label: "Critical", bg: "var(--red-soft)",   fg: "var(--red-ink)"   },
  high:     { label: "High",     bg: "var(--amber-soft)", fg: "var(--amber-ink)" },
  medium:   { label: "Medium",   bg: "var(--surface-2)",  fg: "var(--ink-2)"     },
  low:      { label: "Low",      bg: "var(--green-soft)", fg: "var(--green-ink)" },
};

const _SENSITIVITY_LABELS = {
  pii: "PII", financial: "Financial", confidential: "Confidential",
  internal: "Internal", public: "Public",
};

function _invAgeLabel(ts) {
  if (!ts) return "never";
  const ms = Date.now() - new Date(ts).getTime();
  if (ms < 60000) return `${Math.round(ms / 1000)}s ago`;
  if (ms < 3600000) return `${Math.round(ms / 60000)}m ago`;
  if (ms < 86400000) return `${Math.round(ms / 3600000)}h ago`;
  return `${Math.round(ms / 86400000)}d ago`;
}

function InvTile({ label, value, sub, tone = "neutral" }) {
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

function InvRow({ row, onSave, savingKey }) {
  const [riskTier, setRiskTier] = React.useState(row.risk_tier || "");
  const [sensitivity, setSensitivity] = React.useState(row.data_sensitivity || "");
  const [owner, setOwner] = React.useState(row.system_owner || "");
  const rowKey = `${row.kind}:${row.id}`;
  const saving = savingKey === rowKey;

  React.useEffect(() => {
    setRiskTier(row.risk_tier || "");
    setSensitivity(row.data_sensitivity || "");
    setOwner(row.system_owner || "");
  }, [row.risk_tier, row.data_sensitivity, row.system_owner]);

  function commit(next) {
    onSave(row, { risk_tier: riskTier, data_sensitivity: sensitivity, system_owner: owner, ...next });
  }

  const tierMeta = _RISK_TIER_META[row.risk_tier] || null;

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "1.5fr 0.7fr 0.9fr 1fr 1fr 1.2fr 1fr",
      alignItems: "center", gap: 10, padding: "9px 12px",
      borderBottom: "1px solid var(--line)", fontSize: 12,
      opacity: row.active ? 1 : 0.55,
    }}>
      <div>
        <div style={{ fontWeight: 600 }}>{row.display_name}</div>
        <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 1 }}>{row.type}</div>
      </div>

      <div>
        <span style={{
          fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 999,
          background: row.kind === "push" ? "var(--surface-2)" : "var(--acc-soft)",
          color: row.kind === "push" ? "var(--ink-3)" : "var(--acc-ink)",
        }}>
          {row.kind === "push" ? "Push" : "Poll"}
        </span>
      </div>

      <div style={{ fontSize: 10.5, color: row.active ? "var(--green-ink)" : "var(--ink-4)" }}>
        {row.active ? "Active" : "Inactive"}
        <div style={{ color: "var(--ink-4)", marginTop: 1 }}>{_invAgeLabel(row.last_activity)}</div>
      </div>

      <div>
        <select className="code-input mono" style={{ fontSize: 11, width: "100%" }}
          value={riskTier}
          onChange={e => { setRiskTier(e.target.value); commit({ risk_tier: e.target.value }); }}>
          <option value="">— Not tiered —</option>
          {Object.entries(_RISK_TIER_META).map(([k, m]) => (
            <option key={k} value={k}>{m.label}</option>
          ))}
        </select>
        {tierMeta && (
          <span style={{
            display: "inline-block", marginTop: 4, fontSize: 9.5, fontWeight: 700,
            padding: "1px 7px", borderRadius: 999, background: tierMeta.bg, color: tierMeta.fg,
          }}>
            {tierMeta.label}
          </span>
        )}
      </div>

      <div>
        <select className="code-input mono" style={{ fontSize: 11, width: "100%" }}
          value={sensitivity}
          onChange={e => { setSensitivity(e.target.value); commit({ data_sensitivity: e.target.value }); }}>
          <option value="">— Unclassified —</option>
          {Object.entries(_SENSITIVITY_LABELS).map(([k, l]) => (
            <option key={k} value={k}>{l}</option>
          ))}
        </select>
      </div>

      <div>
        <input className="code-input" style={{ fontSize: 11.5, width: "100%" }}
          value={owner} placeholder="Unassigned"
          onChange={e => setOwner(e.target.value)}
          onBlur={() => commit({ system_owner: owner })} />
      </div>

      <div style={{ fontSize: 10, color: "var(--ink-4)", textAlign: "right" }}>
        {saving ? "Saving…" : (row.kind === "push" ? `${row.total_calls ?? 0} calls` : "polled")}
      </div>
    </div>
  );
}

function AiInventoryScreen({ onNavigate } = {}) {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [savingKey, setSavingKey] = React.useState(null);
  const [filterTier, setFilterTier] = React.useState("all");

  const load = React.useCallback(() => {
    return fetch(`${_aiInvBase()}/ai-inventory`, { credentials: "include" })
      .then(res => {
        if (!res.ok) throw new Error(`Failed to load AI inventory (${res.status})`);
        return res.json();
      })
      .then(d => { setData(d); setError(null); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => { load(); }, [load]);

  async function handleSave(row, fields) {
    const rowKey = `${row.kind}:${row.id}`;
    setSavingKey(rowKey);
    // Optimistic local update so the tiles/filter react immediately.
    setData(d => d && ({
      ...d,
      rows: d.rows.map(r => (r.kind === row.kind && r.id === row.id) ? { ...r, ...fields } : r),
    }));
    const path = row.kind === "push"
      ? `${_aiInvBase()}/systems/${row.id}/classification`
      : `${_aiInvBase()}/connectors/${row.id}/classification`;
    try {
      await fetch(path, {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields),
      });
    } catch (_) {
      // Non-fatal — a background refresh (or the next edit) will reconcile.
    }
    setSavingKey(null);
  }

  const rows = data?.rows || [];
  const untiered = data?.untiered_count ?? rows.filter(r => !r.risk_tier).length;
  const criticalCount = rows.filter(r => r.risk_tier === "critical").length;
  const highCount = rows.filter(r => r.risk_tier === "high").length;
  const visibleRows = filterTier === "all" ? rows : rows.filter(r => r.risk_tier === filterTier);

  return (
    <div className="scope-screen" data-screen-label="AI System Inventory">
      <div className="panel-head">
        <div className="kicker">Audit &amp; Compliance · AI System Inventory</div>
        <div className="panel-title mt-8">AI System Inventory</div>
        <div className="panel-sub">
          Every system the Dendrai UBO™ Governance Brain watches or polls, in one register — the
          starting artifact a NIST AI RMF "Map" review or an EU AI Act system inventory both ask for:
          what exists, what it touches, how risky it is, who owns it.
        </div>
      </div>

      {error && (
        <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>
      )}

      {loading && !data ? <Empty>Loading…</Empty> : (
        <>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 20 }}>
            <InvTile label="Systems in register" value={rows.length} sub="Push + poll, active and inactive" />
            <InvTile label="Untiered" value={untiered}
              tone={untiered > 0 ? "warn" : "good"}
              sub={untiered > 0 ? "Needs a risk tier assigned" : "Fully classified"} />
            <InvTile label="Critical tier" value={criticalCount} tone={criticalCount > 0 ? "bad" : "neutral"} />
            <InvTile label="High tier" value={highCount} tone={highCount > 0 ? "warn" : "neutral"} />
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <span style={{ fontSize: 11, color: "var(--ink-3)" }}>Filter:</span>
            {["all", "critical", "high", "medium", "low"].map(t => (
              <button key={t}
                className={"btn btn-sm" + (filterTier === t ? " btn-acc" : "")}
                style={{ fontSize: 10.5, padding: "3px 9px" }}
                onClick={() => setFilterTier(t)}>
                {t === "all" ? "All" : _RISK_TIER_META[t].label}
              </button>
            ))}
          </div>

          {!visibleRows.length ? (
            <Empty icon="🗂️">
              {rows.length === 0
                ? "No systems registered yet — add one in Dendrai UBO™ Configuration."
                : "No systems at this risk tier."}
            </Empty>
          ) : (
            <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
              <div style={{
                display: "grid", gridTemplateColumns: "1.5fr 0.7fr 0.9fr 1fr 1fr 1.2fr 1fr",
                gap: 10, padding: "6px 12px", fontSize: 10, color: "var(--ink-4)",
                letterSpacing: "0.05em", textTransform: "uppercase", fontWeight: 600,
                borderBottom: "1px solid var(--line)",
              }}>
                <div>System</div><div>Model</div><div>Status</div>
                <div>Risk tier</div><div>Data sensitivity</div><div>Owner</div><div/>
              </div>
              {visibleRows.map(row => (
                <InvRow key={`${row.kind}:${row.id}`} row={row} onSave={handleSave} savingKey={savingKey} />
              ))}
            </div>
          )}

          <p style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 10 }}>
            Risk tier and data sensitivity are editable directly in this table and save immediately.
            To add or remove a system entirely, use{" "}
            <a href="#" onClick={e => { e.preventDefault(); onNavigate && onNavigate("uboconfig"); }}>
              Dendrai UBO™ Configuration
            </a>.
          </p>
        </>
      )}
    </div>
  );
}

Object.assign(window, { AiInventoryScreen });
