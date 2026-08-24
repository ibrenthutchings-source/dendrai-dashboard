/* ============================================================
   Infrastructure Monitoring — Postgres CIS hardening + Railway
   platform/deployment drift + connector-credential rotation hygiene.

   Postgres/Railway targets are registered as poll connectors on the Dendrai
   UBO Configuration screen (connector_type 'postgres_cis'/'railway_iaas') —
   this screen doesn't duplicate that form, it shows the Infrastructure Posture
   matrix (INFRA-001..007) and on-demand "run now" actions, plus Connector
   Hygiene (INFRA-008), a live check with no registration of its own — it
   audits Intelligenza's own credential store.

   Data: GET /api/infra-monitoring/connectors, GET /api/infra-monitoring/results,
         POST /api/infra-monitoring/connectors/{id}/run,
         GET /api/infra-monitoring/connector-hygiene.
   ============================================================ */

function _infraBase() {
  return window.MCP_API_BASE ? window.MCP_API_BASE.replace(/\/mcp$/, "") : "/api";
}

const IM_SEVERITY_TONE = {
  CRITICAL: "bad", HIGH: "bad", MEDIUM: "warn", LOW: "neutral", INFO: "good",
};

function ImSeverityPill({ severity }) {
  const Pill = window.Pill;
  return <Pill tone={IM_SEVERITY_TONE[severity] || "neutral"}>{severity || "—"}</Pill>;
}

const IM_TH_STYLE = { padding: "8px 12px", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" };
const IM_TD_STYLE = { padding: "8px 12px" };

/* ---- Vulnerability & Currency Posture tabs (Development environment only) ----
   GET/POST /api/infra-posture/* — see infra_posture_endpoints.py. Every count
   here is coverage-bounded: "no known-open findings from connected sources",
   never "no vulnerabilities exist" — the summary strip always shows assets
   assessed vs. total alongside the open-finding counts for exactly that reason. */

function ImCoverageNote({ summary }) {
  if (!summary) return null;
  const { assets_total = 0, assets_assessed = 0 } = summary;
  const pct = assets_total ? Math.round((assets_assessed / assets_total) * 100) : 0;
  return (
    <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)", marginBottom: 10 }}>
      {assets_assessed} of {assets_total} tracked assets assessed ({pct}%) — counts below reflect only
      what's been checked from connected sources, not a guarantee nothing else exists.
    </div>
  );
}

function VulnerabilitiesTab({ base }) {
  const Empty = window.Empty;
  const [summary, setSummary] = React.useState(null);
  const [vulns, setVulns] = React.useState([]);
  const [statusFilter, setStatusFilter] = React.useState("open");
  const [loading, setLoading] = React.useState(true);
  const [busyId, setBusyId] = React.useState(null);

  const load = React.useCallback(() => {
    setLoading(true);
    Promise.all([
      fetch(`${base}/infra-posture/summary`, { credentials: "include" }).then(r => r.json()),
      fetch(`${base}/infra-posture/vulnerabilities?status=${encodeURIComponent(statusFilter)}`, { credentials: "include" }).then(r => r.json()),
    ])
      .then(([s, v]) => { setSummary(s); setVulns(v.vulnerabilities || []); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [statusFilter]);

  React.useEffect(() => { load(); }, [load]);

  function disposition(vulnId, status) {
    const reason = window.prompt(status === "accepted_risk" ? "Reason for accepting this risk (required):" : "Reason this is a false positive (required):");
    if (!reason || !reason.trim()) return;
    setBusyId(vulnId);
    fetch(`${base}/infra-posture/vulnerabilities/${vulnId}/disposition`, {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, reason: reason.trim() }),
    })
      .then(r => r.json())
      .then(() => load())
      .catch(() => {})
      .finally(() => setBusyId(null));
  }

  const sevCounts = summary?.open_by_severity || {};

  return (
    <div>
      <ImCoverageNote summary={summary} />
      <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
        {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map(sev => (
          <div key={sev} style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "8px 14px", minWidth: 90 }}>
            <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{sev}</div>
            <div className="mono" style={{ fontSize: 18, fontWeight: 700 }}>{sevCounts[sev] || 0}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        {["open", "remediated", "accepted_risk", "false_positive"].map(s => (
          <button key={s} type="button" className="btn btn-sm" onClick={() => setStatusFilter(s)}
            style={statusFilter === s ? { fontWeight: 700, borderColor: "var(--ink-1)" } : {}}>
            {s.replace("_", " ")}
          </button>
        ))}
      </div>

      {loading && !vulns.length ? <Empty>Loading…</Empty> : !vulns.length ? (
        <Empty>No {statusFilter.replace("_", " ")} vulnerabilities on record.</Empty>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                {["Vuln ID", "Severity", "Title", "Fixed Version", "Source", "First Detected", statusFilter === "open" ? "Disposition" : ""].map(h => (
                  <th key={h} style={IM_TH_STYLE}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {vulns.map(v => (
                <tr key={v.id} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ ...IM_TD_STYLE, fontFamily: "var(--mono)" }}>{v.vuln_id}</td>
                  <td style={IM_TD_STYLE}><ImSeverityPill severity={v.severity} /></td>
                  <td style={{ ...IM_TD_STYLE, color: "var(--ink-3)" }}>{v.title || "—"}</td>
                  <td style={{ ...IM_TD_STYLE, fontFamily: "var(--mono)" }}>{v.fixed_version || "—"}</td>
                  <td style={IM_TD_STYLE}>{v.source}</td>
                  <td style={{ ...IM_TD_STYLE, color: "var(--ink-3)" }}>{v.first_detected_at ? new Date(v.first_detected_at).toLocaleDateString() : "—"}</td>
                  {statusFilter === "open" && (
                    <td style={{ ...IM_TD_STYLE, textAlign: "right", whiteSpace: "nowrap" }}>
                      <button type="button" className="btn btn-sm" disabled={busyId === v.id} onClick={() => disposition(v.id, "accepted_risk")}>Accept risk</button>{" "}
                      <button type="button" className="btn btn-sm" disabled={busyId === v.id} onClick={() => disposition(v.id, "false_positive")}>False positive</button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function AssetInventoryTab({ base }) {
  const Empty = window.Empty;
  const [assets, setAssets] = React.useState([]);
  const [unassessedOnly, setUnassessedOnly] = React.useState(false);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    setLoading(true);
    fetch(`${base}/infra-posture/assets?unassessed_only=${unassessedOnly}`, { credentials: "include" })
      .then(r => r.json()).then(d => setAssets(d.assets || [])).catch(() => {}).finally(() => setLoading(false));
  }, [unassessedOnly]);

  return (
    <div>
      <div style={{ marginBottom: 10 }}>
        <label style={{ fontSize: 11.5, display: "flex", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={unassessedOnly} onChange={e => setUnassessedOnly(e.target.checked)} />
          Show only assets never assessed
        </label>
      </div>
      {loading && !assets.length ? <Empty>Loading…</Empty> : !assets.length ? (
        <Empty>No assets tracked yet — the daily infra asset sweep populates this from registered connectors.</Empty>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                {["Name", "Type", "Software", "Expires", "Last Assessed"].map(h => (
                  <th key={h} style={IM_TH_STYLE}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {assets.map(a => (
                <tr key={a.id} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ ...IM_TD_STYLE, fontFamily: "var(--mono)" }}>{a.name}</td>
                  <td style={IM_TD_STYLE}>{a.asset_type}</td>
                  <td style={{ ...IM_TD_STYLE, color: "var(--ink-3)" }}>{a.software_name ? `${a.software_name} ${a.software_version || ""}` : "—"}</td>
                  <td style={{ ...IM_TD_STYLE, color: "var(--ink-3)" }}>{a.expires_at ? new Date(a.expires_at).toLocaleDateString() : "—"}</td>
                  <td style={IM_TD_STYLE}>
                    {a.last_assessed_at
                      ? <span style={{ color: "var(--ink-3)" }}>{new Date(a.last_assessed_at).toLocaleString()}</span>
                      : <span style={{ color: "var(--red-ink, #b91c1c)", fontWeight: 600 }}>never assessed</span>}
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

function ExpiryTab({ base }) {
  const Empty = window.Empty;
  const [assets, setAssets] = React.useState([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    setLoading(true);
    fetch(`${base}/infra-posture/assets?limit=2000`, { credentials: "include" })
      .then(r => r.json())
      .then(d => setAssets((d.assets || []).filter(a => a.expires_at)))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const now = Date.now();
  const rows = [...assets].sort((a, b) => new Date(a.expires_at) - new Date(b.expires_at));

  return (
    <div>
      <div className="panel-sub" style={{ marginBottom: 8 }}>
        Every tracked credential and certificate with a known expiry date — connectors' own
        credential expiry plus certificates discovered by the tls_cert connector.
      </div>
      {loading && !rows.length ? <Empty>Loading…</Empty> : !rows.length ? (
        <Empty>Nothing with a known expiry date is tracked yet.</Empty>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                {["Name", "Type", "Expires", "Status"].map(h => (
                  <th key={h} style={IM_TH_STYLE}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(a => {
                const expires = new Date(a.expires_at);
                const daysLeft = Math.round((expires.getTime() - now) / 86400000);
                const tone = daysLeft < 0 ? "bad" : daysLeft <= 30 ? "warn" : "good";
                const Pill = window.Pill;
                return (
                  <tr key={a.id} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ ...IM_TD_STYLE, fontFamily: "var(--mono)" }}>{a.name}</td>
                    <td style={IM_TD_STYLE}>{a.asset_type}</td>
                    <td style={{ ...IM_TD_STYLE, color: "var(--ink-3)" }}>{expires.toLocaleDateString()}</td>
                    <td style={IM_TD_STYLE}>
                      <Pill tone={tone}>{daysLeft < 0 ? `expired ${-daysLeft}d ago` : `${daysLeft}d left`}</Pill>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const IM_TABS = [
  { id: "posture", label: "Posture" },
  { id: "vulnerabilities", label: "Vulnerabilities" },
  { id: "assets", label: "Asset Inventory" },
  { id: "expiry", label: "Expiry" },
];

function InfrastructureMonitoringScreen({ onNavigate, isDevEnv = false } = {}) {
  const Empty = window.Empty;
  const LiveBadge = window.LiveBadge;
  const SectionLabel = window.SectionLabel;

  const [connectors, setConnectors] = React.useState([]);
  const [results, setResults] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [lastRefresh, setLastRefresh] = React.useState(null);
  const [isPaused, setIsPaused] = React.useState(false);
  const [runningId, setRunningId] = React.useState(null);

  const [hygiene, setHygiene] = React.useState(null);
  const [hygieneLoading, setHygieneLoading] = React.useState(true);

  const [activeTab, setActiveTab] = React.useState("posture");

  const load = React.useCallback(() => {
    setLoading(true);
    Promise.all([
      fetch(`${_infraBase()}/infra-monitoring/connectors`, { credentials: "include" }).then(r => r.json()),
      fetch(`${_infraBase()}/infra-monitoring/results`, { credentials: "include" }).then(r => r.json()),
    ])
      .then(([c, r]) => {
        setConnectors(c.connectors || []);
        setResults(r.results || []);
        setError(null);
        setLastRefresh(new Date());
      })
      .catch(e => setError(e.message || "Failed to load infrastructure monitoring data"))
      .finally(() => setLoading(false));
  }, []);

  const loadHygiene = React.useCallback(() => {
    setHygieneLoading(true);
    return fetch(`${_infraBase()}/infra-monitoring/connector-hygiene`, { credentials: "include" })
      .then(r => r.json())
      .then(d => setHygiene(d))
      .catch(e => setHygiene({ compliance: { stale_connectors: [] }, error: e.message }))
      .finally(() => setHygieneLoading(false));
  }, []);

  React.useEffect(() => { load(); }, [load]);
  React.useEffect(() => { loadHygiene(); }, [loadHygiene]);

  React.useEffect(() => {
    if (isPaused) return;
    const id = setInterval(() => { load(); loadHygiene(); }, 15000);
    return () => clearInterval(id);
  }, [isPaused, load, loadHygiene]);

  function runConnector(connectorId) {
    setRunningId(connectorId);
    fetch(`${_infraBase()}/infra-monitoring/connectors/${connectorId}/run`, {
      method: "POST", credentials: "include",
    })
      .then(r => r.json())
      .then(() => load())
      .catch(e => setError(e.message || "Run failed"))
      .finally(() => setRunningId(null));
  }

  // One row per (connector, resource) — a Railway connector can cover several
  // services, each with its own latest result; Postgres connectors have one.
  const resultsByConnector = React.useMemo(() => {
    const m = {};
    for (const r of results) {
      const key = r.server_name || "";
      (m[key] = m[key] || []).push(r);
    }
    return m;
  }, [results]);

  const staleConnectors = hygiene?.compliance?.stale_connectors || [];

  return (
    <div className="scope-screen" data-screen-label="Infrastructure Monitoring">
      <div className="panel-head">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div className="kicker">Infrastructure Monitoring · Config Posture &amp; Credential Hygiene</div>
            <div className="panel-title mt-8">Infrastructure Monitoring</div>
            <div className="panel-sub">
              Postgres CIS-style hardening checks, Railway platform/deployment drift, and AWS
              cloud configuration drift + IAM lease duration for registered infrastructure
              targets, plus Intelligenza's own connector-credential rotation hygiene. Findings
              feed the infrastructure_monitoring Policy-as-Code module the same way DevOps
              Monitoring's findings feed devops_monitoring.
            </div>
          </div>
          {LiveBadge && (
            <LiveBadge lastRefresh={lastRefresh} isPaused={isPaused}
              onToggle={() => setIsPaused(p => !p)} intervalLabel="15s" />
          )}
        </div>
      </div>

      {error && (
        <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>
      )}

      {isDevEnv && (
        <div style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: "1px solid var(--line)" }}>
          {IM_TABS.map(t => (
            <button key={t.id} type="button" onClick={() => setActiveTab(t.id)}
              className="btn btn-sm"
              style={{
                borderRadius: "6px 6px 0 0", borderBottom: "none", marginBottom: -1,
                fontWeight: activeTab === t.id ? 700 : 400,
                background: activeTab === t.id ? "var(--panel-bg, var(--card))" : "transparent",
              }}>
              {t.label}
            </button>
          ))}
        </div>
      )}

      {activeTab === "vulnerabilities" && <VulnerabilitiesTab base={_infraBase()} />}
      {activeTab === "assets" && <AssetInventoryTab base={_infraBase()} />}
      {activeTab === "expiry" && <ExpiryTab base={_infraBase()} />}

      {activeTab === "posture" && (<>
      {/* ---- Infrastructure Posture ---- */}
      <div style={{ marginBottom: 24 }}>
        <SectionLabel
          right={
            <button type="button" className="btn btn-sm" onClick={() => onNavigate && onNavigate("uboconfig")}>
              + Register target
            </button>
          }
        >
          Infrastructure Posture
        </SectionLabel>

        {loading && !connectors.length ? <Empty>Loading…</Empty> : !connectors.length ? (
          <Empty>
            No Postgres or Railway targets registered yet. Register one via "+ Register target" —
            it adds a postgres_cis/railway_iaas connector on the Dendrai UBO Configuration screen.
          </Empty>
        ) : (
          connectors.map(c => {
            const serverName = `${c.connector_type}:${c.display_name}`;
            const rows = resultsByConnector[serverName] || [];
            return (
              <div key={c.id} style={{ marginBottom: 14 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>
                    {c.display_name}
                    <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)", marginLeft: 8 }}>
                      {{ postgres_cis: "Postgres CIS", railway_iaas: "Railway platform", aws_iaas: "AWS", aws_patch: "AWS SSM Patch", aws_inspector: "AWS Inspector", ot_heartbeat: "OT/SCADA heartbeat", tls_cert: "TLS certificate" }[c.connector_type] || c.connector_type}
                    </span>
                  </div>
                  <button type="button" className="btn btn-sm" onClick={() => runConnector(c.id)} disabled={runningId === c.id}>
                    {runningId === c.id ? "Running…" : "Run now"}
                  </button>
                </div>
                <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                        {["Resource", "Severity", "Key Findings", "Last Checked"].map(h => (
                          <th key={h} style={{ padding: "8px 12px", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {!rows.length ? (
                        <tr><td colSpan={4} style={{ padding: "8px 12px", color: "var(--ink-4)" }}>not yet audited</td></tr>
                      ) : rows.map(r => {
                        const compliance = r.raw_payload?.infra_compliance || {};
                        const notes = [];
                        if (c.connector_type === "postgres_cis") {
                          if (compliance.ssl_enabled === false) notes.push("SSL disabled");
                          if (compliance.password_encryption && compliance.password_encryption !== "scram-sha-256") notes.push(`weak password hashing (${compliance.password_encryption})`);
                          if ((compliance.superuser_count || 0) > 2) notes.push(`${compliance.superuser_count} superusers`);
                          if (compliance.log_connections === false) notes.push("connection logging off");
                        } else if (c.connector_type === "railway_iaas") {
                          if (compliance.unexpected_public_domain) notes.push("unapproved public domain");
                          if (compliance.image_digest_mismatch) notes.push("image digest mismatch");
                        } else if (c.connector_type === "aws_iaas") {
                          // AWS multiplexes several distinct checks (S3/security group/
                          // encryption/IAM) into the same resource matrix — r.action names
                          // which one this row is.
                          if (r.action === "s3_public_access" && compliance.is_public) notes.push("bucket publicly accessible");
                          if (r.action === "security_group_open_ingress" && (compliance.open_sensitive_ports || []).length) notes.push(`open ports: ${compliance.open_sensitive_ports.join(", ")}`);
                          if ((r.action === "unencrypted_volume" || r.action === "unencrypted_rds") && compliance.encrypted === false) notes.push("unencrypted at rest");
                          if (r.action === "iam_excessive_session" && compliance.max_session_duration_hours > 12) notes.push(`${compliance.max_session_duration_hours}h max session`);
                        } else if (c.connector_type === "ot_heartbeat") {
                          if (compliance.alive === false) notes.push(compliance.error || "no response");
                        } else if (c.connector_type === "aws_patch") {
                          if (compliance.failed_count > 0) notes.push(`${compliance.failed_count} failed install(s)`);
                          if (compliance.missing_count > 0) notes.push(`${compliance.missing_count} missing patch(es)`);
                        } else if (c.connector_type === "aws_inspector") {
                          notes.push(`${compliance.vuln_id || "finding"}${compliance.package_name ? ` in ${compliance.package_name}` : ""}`);
                        } else if (c.connector_type === "tls_cert") {
                          if (compliance.cert_reachable === false) notes.push(compliance.cert_error || "unreachable");
                          else if (typeof compliance.cert_days_to_expiry === "number") notes.push(`${compliance.cert_days_to_expiry}d to expiry`);
                        }
                        return (
                          <tr key={r.id} style={{ borderBottom: "1px solid var(--line)" }}>
                            <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{r.resource || "—"}</td>
                            <td style={{ padding: "8px 12px" }}><ImSeverityPill severity={r.severity} /></td>
                            <td style={{ padding: "8px 12px", color: "var(--ink-3)" }}>{notes.length ? notes.join(", ") : "—"}</td>
                            <td style={{ padding: "8px 12px", color: "var(--ink-3)" }}>
                              {r.created_at ? new Date(r.created_at).toLocaleString() : "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* ---- Connector Hygiene ---- */}
      <div>
        <SectionLabel>Connector Hygiene</SectionLabel>
        <div className="panel-sub" style={{ marginBottom: 8 }}>
          Credential rotation age across every registered observability connector (Oracle Fusion,
          SAP HANA, GitHub/GitLab, Postgres, Railway, …) — Intelligenza's own INFRA-008 check,
          live rather than paged from history.
        </div>

        {hygieneLoading && !hygiene ? <Empty>Loading…</Empty> : !staleConnectors.length ? (
          <Empty>No stale connector credentials — every registered connector has rotated within the threshold.</Empty>
        ) : (
          <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                  {["Connector", "Type", "Credential Age", ""].map(h => (
                    <th key={h} style={{ padding: "8px 12px", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {staleConnectors.map(sc => (
                  <tr key={sc.id} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{sc.display_name}</td>
                    <td style={{ padding: "8px 12px" }}>{sc.connector_type}</td>
                    <td style={{ padding: "8px 12px" }}>{sc.credential_age_days} days</td>
                    <td style={{ padding: "8px 12px", textAlign: "right" }}>
                      <button type="button" className="btn btn-sm" onClick={() => onNavigate && onNavigate("uboconfig")}>
                        Rotate credential
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      </>)}
    </div>
  );
}

Object.assign(window, { InfrastructureMonitoringScreen });
