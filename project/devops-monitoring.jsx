/* ============================================================
   DevOps Monitoring — SCM Integrity Auditor + SARIF Evidence Ingestion.

   Repos are registered as poll connectors on the Dendrai UBO Configuration
   screen (connector_type 'github_scm'/'gitlab_scm' — see ubo-config.jsx's
   CONNECTOR_TYPES) exactly like Oracle Fusion/SAP HANA/etc., so this screen
   doesn't duplicate that form. It shows what's unique to this category: the
   Branch Integrity Matrix (audit findings), on-demand "run now" actions, and
   the Evidence Inspector for ingested SARIF findings + their proof chain.

   Data: GET /api/scm-audit/repositories, GET /api/scm-audit/results,
         POST /api/scm-audit/repositories/{id}/run, POST /api/scm-audit/run-all,
         GET /api/scm-audit/pipeline-security/results,
         POST /api/scm-audit/repositories/{id}/run-pipeline-security,
         GET /api/scm-audit/secret-scan/results,
         POST /api/scm-audit/repositories/{id}/run-secret-scan,
         GET /api/evidence/records, GET /api/evidence/records/{id}/verify.
   ============================================================ */

function _devopsBase() {
  return window.MCP_API_BASE ? window.MCP_API_BASE.replace(/\/mcp$/, "") : "/api";
}

const DM_STATUS_TONE = {
  COMPLIANT: "good",
  WEAKNESS: "warn",
  NON_COMPLIANT: "bad",
};

function DmStatusPill({ status }) {
  const Pill = window.Pill;
  if (!status) return <Pill tone="neutral">UNKNOWN</Pill>;
  return <Pill tone={DM_STATUS_TONE[status] || "neutral"}>{status.replace("_", " ")}</Pill>;
}

const DM_SEVERITY_TONE = {
  CRITICAL: "bad", HIGH: "bad", MEDIUM: "warn", LOW: "neutral", INFO: "neutral",
};

function DmSeverityPill({ severity }) {
  const Pill = window.Pill;
  return <Pill tone={DM_SEVERITY_TONE[severity] || "neutral"}>{severity || "—"}</Pill>;
}

const DM_WAIVER_STATUS_TONE = { ACTIVE: "good", EXPIRED: "warn", REVOKED: "bad" };

function DmWaiverStatusPill({ status }) {
  const Pill = window.Pill;
  return <Pill tone={DM_WAIVER_STATUS_TONE[status] || "neutral"}>{status || "—"}</Pill>;
}

const DM_TICKET_STATUS_TONE = {
  open: "neutral", in_progress: "warn", resolved: "good", closed: "good", cancelled: "neutral",
};

function DmTicketStatusPill({ status }) {
  const Pill = window.Pill;
  return <Pill tone={DM_TICKET_STATUS_TONE[status] || "neutral"}>{(status || "—").replace("_", " ")}</Pill>;
}

// ---- Discover repositories — token-driven picker over POST /discover,
// bulk-registers the selected ones via POST /repositories/bulk. Covers all
// three SCM providers (github_scm.py's request already accepts a per-provider
// base_url override for self-hosted GitHub Enterprise/GitLab/Bitbucket Server). ----
function DiscoverReposModal({ onClose, onRegistered }) {
  const Empty = window.Empty;
  const [provider, setProvider] = React.useState("github");
  const [baseUrl, setBaseUrl] = React.useState("");
  const [token, setToken] = React.useState("");
  const [fetching, setFetching] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [repos, setRepos] = React.useState(null);
  const [capped, setCapped] = React.useState(false);
  const [selected, setSelected] = React.useState({});
  const [registering, setRegistering] = React.useState(false);
  const [registerError, setRegisterError] = React.useState(null);

  function discover() {
    if (!token.trim()) { setError("Token is required"); return; }
    setFetching(true); setError(null); setRepos(null);
    fetch(`${_devopsBase()}/scm-audit/discover`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, token, base_url: baseUrl || undefined }),
    })
      .then(r => r.json().then(d => r.ok ? d : Promise.reject(new Error(d.detail || `HTTP ${r.status}`))))
      .then(d => { setRepos(d.repositories || []); setCapped(!!d.capped); setSelected({}); })
      .catch(e => setError(e.message || "Discovery failed"))
      .finally(() => setFetching(false));
  }

  function toggle(repoRef) {
    setSelected(s => ({ ...s, [repoRef]: !s[repoRef] }));
  }

  function registerSelected() {
    const picked = (repos || []).filter(r => selected[r.repo_ref]);
    if (!picked.length) return;
    setRegistering(true); setRegisterError(null);
    fetch(`${_devopsBase()}/scm-audit/repositories/bulk`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider, token, base_url: baseUrl || undefined,
        repos: picked.map(r => ({ repo_ref: r.repo_ref, branch: r.default_branch || "main" })),
      }),
    })
      .then(r => r.json())
      .then(d => {
        if (d.failed && d.failed.length) {
          setRegisterError(`${d.registered.length} registered, ${d.failed.length} failed: ${d.failed.map(f => f.repo_ref).join(", ")}`);
        } else {
          onRegistered();
          onClose();
        }
      })
      .catch(e => setRegisterError(e.message || "Registration failed"))
      .finally(() => setRegistering(false));
  }

  const selectedCount = Object.values(selected).filter(Boolean).length;

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}
      onClick={onClose}>
      <div style={{ background: "var(--surface)", borderRadius: 10, padding: 20, width: 560, maxHeight: "80vh", overflowY: "auto", border: "1px solid var(--line)" }}
        onClick={e => e.stopPropagation()}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Discover repositories</div>

        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          <select className="input input-sm" value={provider} onChange={e => { setProvider(e.target.value); setRepos(null); }} style={{ flex: 1 }}>
            <option value="github">GitHub</option>
            <option value="gitlab">GitLab</option>
            <option value="bitbucket">Bitbucket</option>
          </select>
          <input className="input input-sm" placeholder="Base URL (optional — self-hosted)"
            value={baseUrl} onChange={e => setBaseUrl(e.target.value)} style={{ flex: 2 }} />
        </div>
        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          <input className="input input-sm" type="password" placeholder="Access token"
            value={token} onChange={e => setToken(e.target.value)} style={{ flex: 1 }} />
          <button type="button" className="btn btn-sm" onClick={discover} disabled={fetching}>
            {fetching ? "Fetching…" : "Fetch repositories"}
          </button>
        </div>

        {error && <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 8 }}>{error}</div>}

        {repos && (
          <>
            {capped && (
              <div className="panel-sub" style={{ marginBottom: 8 }}>
                Showing the first {repos.length} repositories — this token can see more; narrow by provider/workspace if the one you want isn't listed.
              </div>
            )}
            {!repos.length ? (
              <Empty>No repositories visible to this token.</Empty>
            ) : (
              <div style={{ border: "1px solid var(--line)", borderRadius: 6, maxHeight: 260, overflowY: "auto", marginBottom: 12 }}>
                {repos.map(r => (
                  <label key={r.repo_ref} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", borderBottom: "1px solid var(--line)", cursor: "pointer", fontSize: 11.5 }}>
                    <input type="checkbox" checked={!!selected[r.repo_ref]} onChange={() => toggle(r.repo_ref)} />
                    <span style={{ fontFamily: "var(--mono)", flex: 1 }}>{r.repo_ref}</span>
                    {r.private === false && <span style={{ fontSize: 10, color: "var(--ink-4)" }}>public</span>}
                  </label>
                ))}
              </div>
            )}
          </>
        )}

        {registerError && <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 8 }}>{registerError}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button type="button" className="btn btn-sm" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-sm btn-primary" onClick={registerSelected}
            disabled={!selectedCount || registering}>
            {registering ? "Registering…" : `Register ${selectedCount || ""} selected`}
          </button>
        </div>
      </div>
    </div>
  );
}

function DevopsMonitoringScreen({ onNavigate } = {}) {
  const Empty = window.Empty;
  const LiveBadge = window.LiveBadge;
  const SectionLabel = window.SectionLabel;

  const [repos, setRepos] = React.useState([]);
  const [results, setResults] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [lastRefresh, setLastRefresh] = React.useState(null);
  const [isPaused, setIsPaused] = React.useState(false);
  const [runningId, setRunningId] = React.useState(null);
  const [runAllBusy, setRunAllBusy] = React.useState(false);
  const [showDiscoverModal, setShowDiscoverModal] = React.useState(false);

  const [pipelineResults, setPipelineResults] = React.useState([]);
  const [pipelineRunningId, setPipelineRunningId] = React.useState(null);

  const [secretScanResults, setSecretScanResults] = React.useState([]);
  const [secretScanRunningId, setSecretScanRunningId] = React.useState(null);
  const [secretScanNote, setSecretScanNote] = React.useState({});

  const [evRepository, setEvRepository] = React.useState("");
  const [evSeverity, setEvSeverity] = React.useState("");
  const [evRecords, setEvRecords] = React.useState([]);
  const [evLoading, setEvLoading] = React.useState(false);
  const [verifyResults, setVerifyResults] = React.useState({});
  const [chainVerifyResult, setChainVerifyResult] = React.useState(null);
  const [chainVerifying, setChainVerifying] = React.useState(false);

  const [driftEvents, setDriftEvents] = React.useState([]);
  const [driftOpenOnly, setDriftOpenOnly] = React.useState(false);
  const [waivers, setWaivers] = React.useState([]);
  const [waiverStatus, setWaiverStatus] = React.useState("");
  const [attestations, setAttestations] = React.useState([]);

  const [itsmTickets, setItsmTickets] = React.useState([]);
  const [itsmStatus, setItsmStatus] = React.useState("");
  const [itsmBreachedOnly, setItsmBreachedOnly] = React.useState(false);
  const [itsmSummary, setItsmSummary] = React.useState({ open: 0, breached: 0, at_risk_24h: 0 });
  const [syncingTicketId, setSyncingTicketId] = React.useState(null);

  const load = React.useCallback(() => {
    return Promise.all([
      fetch(`${_devopsBase()}/scm-audit/repositories`, { credentials: "include" }).then(r => r.json()),
      fetch(`${_devopsBase()}/scm-audit/results`, { credentials: "include" }).then(r => r.json()),
    ])
      .then(([repoRes, resultRes]) => {
        setRepos(repoRes.repositories || []);
        setResults(resultRes.results || []);
        setError(null);
        setLastRefresh(new Date());
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    load();
    if (isPaused) return;
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load, isPaused]);

  const loadPipelineResults = React.useCallback(() => {
    return fetch(`${_devopsBase()}/scm-audit/pipeline-security/results`, { credentials: "include" })
      .then(r => r.json())
      .then(d => setPipelineResults(d.results || []))
      .catch(() => setPipelineResults([]));
  }, []);
  React.useEffect(() => {
    loadPipelineResults();
    if (isPaused) return;
    const id = setInterval(loadPipelineResults, 15000);
    return () => clearInterval(id);
  }, [loadPipelineResults, isPaused]);

  const loadSecretScanResults = React.useCallback(() => {
    return fetch(`${_devopsBase()}/scm-audit/secret-scan/results`, { credentials: "include" })
      .then(r => r.json())
      .then(d => setSecretScanResults(d.results || []))
      .catch(() => setSecretScanResults([]));
  }, []);
  React.useEffect(() => { loadSecretScanResults(); }, [loadSecretScanResults]);

  const loadEvidence = React.useCallback(() => {
    setEvLoading(true);
    const params = new URLSearchParams();
    if (evRepository.trim()) params.set("repository", evRepository.trim());
    if (evSeverity) params.set("severity", evSeverity);
    return fetch(`${_devopsBase()}/evidence/records?${params.toString()}`, { credentials: "include" })
      .then(r => r.json())
      .then(d => setEvRecords(d.records || []))
      .catch(() => setEvRecords([]))
      .finally(() => setEvLoading(false));
  }, [evRepository, evSeverity]);

  React.useEffect(() => { loadEvidence(); }, [loadEvidence]);

  const loadDrift = React.useCallback(() => {
    const params = new URLSearchParams();
    if (driftOpenOnly) params.set("open_only", "true");
    return fetch(`${_devopsBase()}/scm-audit/drift?${params.toString()}`, { credentials: "include" })
      .then(r => r.json()).then(d => setDriftEvents(d.events || [])).catch(() => setDriftEvents([]));
  }, [driftOpenOnly]);
  React.useEffect(() => { loadDrift(); }, [loadDrift]);

  const loadWaivers = React.useCallback(() => {
    const params = new URLSearchParams();
    if (waiverStatus) params.set("status", waiverStatus);
    return fetch(`${_devopsBase()}/scm-audit/waivers?${params.toString()}`, { credentials: "include" })
      .then(r => r.json()).then(d => setWaivers(d.waivers || [])).catch(() => setWaivers([]));
  }, [waiverStatus]);
  React.useEffect(() => { loadWaivers(); }, [loadWaivers]);

  const loadAttestations = React.useCallback(() => {
    return fetch(`${_devopsBase()}/evidence/attestations`, { credentials: "include" })
      .then(r => r.json()).then(d => setAttestations(d.attestations || [])).catch(() => setAttestations([]));
  }, []);
  React.useEffect(() => { loadAttestations(); }, [loadAttestations]);

  const loadItsm = React.useCallback(() => {
    const params = new URLSearchParams();
    if (itsmStatus) params.set("status", itsmStatus);
    if (itsmBreachedOnly) params.set("breached_only", "true");
    return Promise.all([
      fetch(`${_devopsBase()}/itsm/tickets?${params.toString()}`, { credentials: "include" }).then(r => r.json()),
      fetch(`${_devopsBase()}/itsm/sla-summary`, { credentials: "include" }).then(r => r.json()),
    ])
      .then(([ticketRes, summaryRes]) => {
        setItsmTickets(ticketRes.tickets || []);
        setItsmSummary(summaryRes || { open: 0, breached: 0, at_risk_24h: 0 });
      })
      .catch(() => { setItsmTickets([]); });
  }, [itsmStatus, itsmBreachedOnly]);
  React.useEffect(() => { loadItsm(); }, [loadItsm]);

  function syncTicket(id) {
    setSyncingTicketId(id);
    fetch(`${_devopsBase()}/itsm/tickets/${id}/sync`, { method: "POST", credentials: "include" })
      .catch(() => {})
      .finally(() => { setSyncingTicketId(null); loadItsm(); });
  }

  // Join the registry (has ids, for the run action) with the latest audit
  // result per repo (keyed by server_name == repo_ref) into one matrix row.
  const matrixRows = repos.map(repo => {
    const result = results.find(r => r.server_name === repo.repo_ref) || null;
    return { repo, result };
  });

  function runAudit(repositoryId) {
    setRunningId(repositoryId);
    fetch(`${_devopsBase()}/scm-audit/repositories/${repositoryId}/run`, {
      method: "POST", credentials: "include",
    })
      .then(r => r.json())
      .catch(e => ({ adjudication_error: e.message }))
      .finally(() => { setRunningId(null); load(); });
  }

  function runAll() {
    setRunAllBusy(true);
    fetch(`${_devopsBase()}/scm-audit/run-all`, { method: "POST", credentials: "include" })
      .catch(() => {})
      .finally(() => { setRunAllBusy(false); load(); });
  }

  const githubRepos = repos.filter(r => r.provider === "github");
  const pipelineRows = githubRepos.map(repo => ({
    repo, result: pipelineResults.find(r => r.server_name === repo.repo_ref) || null,
  }));

  function runPipelineSecurityAudit(repositoryId) {
    setPipelineRunningId(repositoryId);
    fetch(`${_devopsBase()}/scm-audit/repositories/${repositoryId}/run-pipeline-security`, {
      method: "POST", credentials: "include",
    })
      .then(r => r.json())
      .catch(e => ({ adjudication_error: e.message }))
      .finally(() => { setPipelineRunningId(null); loadPipelineResults(); });
  }

  function runSecretScan(repositoryId) {
    setSecretScanRunningId(repositoryId);
    fetch(`${_devopsBase()}/scm-audit/repositories/${repositoryId}/run-secret-scan`, {
      method: "POST", credentials: "include",
    })
      .then(r => r.json())
      .then(d => setSecretScanNote(prev => ({ ...prev, [repositoryId]: d })))
      .catch(e => setSecretScanNote(prev => ({ ...prev, [repositoryId]: { adjudication_error: e.message } })))
      .finally(() => { setSecretScanRunningId(null); loadSecretScanResults(); });
  }

  function verifyRecord(id) {
    fetch(`${_devopsBase()}/evidence/records/${id}/verify`, { credentials: "include" })
      .then(r => r.json())
      .then(d => setVerifyResults(prev => ({ ...prev, [id]: d.valid })))
      .catch(() => setVerifyResults(prev => ({ ...prev, [id]: false })));
  }

  function verifyChain() {
    setChainVerifying(true);
    fetch(`${_devopsBase()}/evidence/chain/verify`, { credentials: "include" })
      .then(r => r.json())
      .then(d => setChainVerifyResult(d))
      .catch(e => setChainVerifyResult({ valid: false, error: e.message }))
      .finally(() => setChainVerifying(false));
  }

  return (
    <div className="scope-screen" data-screen-label="DevOps Monitoring">
      <div className="panel-head">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div className="kicker">DevOps Monitoring · SCM Integrity & Evidence</div>
            <div className="panel-title mt-8">DevOps Monitoring</div>
            <div className="panel-sub">
              Branch-protection/CODEOWNERS compliance and GitHub Actions workflow-as-code
              security for registered GitHub & GitLab repositories, and the SARIF/SAST
              evidence log feeding their severity SLAs. Findings are adjudicated through the
              same Bronze→Silver→Gold→Council pipeline and devops_monitoring Policy-as-Code
              module as every other governed system.
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

      {/* ---- Branch Integrity Matrix ---- */}
      <div style={{ marginBottom: 24 }}>
        <SectionLabel
          right={
            <div style={{ display: "flex", gap: 8 }}>
              <button type="button" className="btn btn-sm" onClick={() => setShowDiscoverModal(true)}>
                Discover repositories
              </button>
              <button type="button" className="btn btn-sm" onClick={() => onNavigate && onNavigate("uboconfig")}>
                + Register repository
              </button>
              <button type="button" className="btn btn-sm" onClick={runAll} disabled={runAllBusy || !repos.length}>
                {runAllBusy ? "Running…" : "Run all audits"}
              </button>
            </div>
          }
        >
          Branch Integrity Matrix
        </SectionLabel>

        {showDiscoverModal && (
          <DiscoverReposModal onClose={() => setShowDiscoverModal(false)} onRegistered={load} />
        )}

        {loading && !repos.length ? <Empty>Loading…</Empty> : !repos.length ? (
          <Empty>
            No repositories registered yet. Use "Discover repositories" to pick from
            everything a token can see, or "+ Register repository" for one at a time —
            both add a GitHub/GitLab/Bitbucket connector visible on the Dendrai UBO
            Configuration screen too.
          </Empty>
        ) : (
          <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                  {["Repository", "Provider", "Branch", "Compliance", "Risk Tier", "Human Review", "Last Audited", ""].map(h => (
                    <th key={h} style={{ padding: "8px 12px", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {matrixRows.map(({ repo, result }) => (
                  <tr key={repo.id} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{repo.repo_ref}</td>
                    <td style={{ padding: "8px 12px" }}>{repo.provider}</td>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{repo.branch}</td>
                    <td style={{ padding: "8px 12px" }}>
                      {result ? <DmStatusPill status={result.compliance_status} /> : <span style={{ color: "var(--ink-4)" }}>not yet audited</span>}
                    </td>
                    <td style={{ padding: "8px 12px" }}>{result?.risk_tier || "—"}</td>
                    <td style={{ padding: "8px 12px" }}>{result?.requires_human_review ? "Yes" : "No"}</td>
                    <td style={{ padding: "8px 12px", color: "var(--ink-3)" }}>
                      {result?.adjudicated_at ? new Date(result.adjudicated_at).toLocaleString() : "—"}
                    </td>
                    <td style={{ padding: "8px 12px", textAlign: "right" }}>
                      <button type="button" className="btn btn-sm" onClick={() => runAudit(repo.id)} disabled={runningId === repo.id}>
                        {runningId === repo.id ? "Running…" : "Run now"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ---- Pipeline Security ---- */}
      <div style={{ marginBottom: 24 }}>
        <SectionLabel>Pipeline Security</SectionLabel>
        <div className="panel-sub" style={{ marginBottom: 8 }}>
          GitHub Actions workflow-as-code audit — token permissions, unpinned third-party
          actions, and risky pull_request_target triggers — for registered GitHub repositories.
        </div>

        {!githubRepos.length ? (
          <Empty>No GitHub repositories registered yet.</Empty>
        ) : (
          <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                  {["Repository", "Workflows", "Missing Permissions", "Unpinned Actions", "Risky pull_request_target", "Risk Tier", "Last Audited", ""].map(h => (
                    <th key={h} style={{ padding: "8px 12px", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {pipelineRows.map(({ repo, result }) => (
                    <tr key={repo.id} style={{ borderBottom: "1px solid var(--line)" }}>
                      <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{repo.repo_ref}</td>
                      <td style={{ padding: "8px 12px" }}>{result ? (result.compliance?.total_workflows ?? "—") : "—"}</td>
                      <td style={{ padding: "8px 12px" }}>{result ? (result.compliance?.workflows_without_permissions ?? "—") : "—"}</td>
                      <td style={{ padding: "8px 12px" }}>{result ? (result.compliance?.unpinned_action_count ?? "—") : "—"}</td>
                      <td style={{ padding: "8px 12px" }}>
                        {result ? (result.compliance?.has_risky_pull_request_target
                          ? <DmSeverityPill severity="CRITICAL" /> : <DmSeverityPill severity="INFO" />)
                          : <span style={{ color: "var(--ink-4)" }}>not yet audited</span>}
                      </td>
                      <td style={{ padding: "8px 12px" }}>{result?.risk_tier || "—"}</td>
                      <td style={{ padding: "8px 12px", color: "var(--ink-3)" }}>
                        {result?.adjudicated_at ? new Date(result.adjudicated_at).toLocaleString() : "—"}
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "right" }}>
                        <button type="button" className="btn btn-sm" onClick={() => runPipelineSecurityAudit(repo.id)} disabled={pipelineRunningId === repo.id}>
                          {pipelineRunningId === repo.id ? "Running…" : "Run now"}
                        </button>
                      </td>
                    </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ---- Secret Scanning ---- */}
      <div style={{ marginBottom: 24 }}>
        <SectionLabel>Secret Scanning</SectionLabel>
        <div className="panel-sub" style={{ marginBottom: 8 }}>
          Real gitleaks scan of each registered GitHub repository's full git history — the
          producer for SECRET_DETECTED (zero-tolerance, CRITICAL) findings outside a live
          GitHub Advanced Security webhook. Clones the repo, so a run can take longer than
          the other audits above; secret values are never stored or displayed, only
          redacted metadata (rule, file, commit, author).
        </div>

        {!githubRepos.length ? (
          <Empty>No GitHub repositories registered yet.</Empty>
        ) : (
          <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                  {["Repository", "Status", "Risk Tier", "Last Scanned", ""].map(h => (
                    <th key={h} style={{ padding: "8px 12px", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {githubRepos.map(repo => {
                  const result = secretScanResults.find(r => r.server_name === repo.repo_ref) || null;
                  const note = secretScanNote[repo.id];
                  let statusNode;
                  if (note && note.scanned === false) {
                    statusNode = <span style={{ color: "var(--ink-4)" }}>gitleaks unavailable in this environment</span>;
                  } else if (note && note.finding_count === 0) {
                    statusNode = <DmSeverityPill severity="INFO" />;
                  } else if (result || (note && note.finding_count > 0)) {
                    statusNode = <DmSeverityPill severity="CRITICAL" />;
                  } else {
                    statusNode = <span style={{ color: "var(--ink-4)" }}>not yet scanned</span>;
                  }
                  return (
                    <tr key={repo.id} style={{ borderBottom: "1px solid var(--line)" }}>
                      <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{repo.repo_ref}</td>
                      <td style={{ padding: "8px 12px" }}>{statusNode}</td>
                      <td style={{ padding: "8px 12px" }}>{result?.risk_tier || "—"}</td>
                      <td style={{ padding: "8px 12px", color: "var(--ink-3)" }}>
                        {result?.adjudicated_at ? new Date(result.adjudicated_at).toLocaleString() : "—"}
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "right" }}>
                        <button type="button" className="btn btn-sm" onClick={() => runSecretScan(repo.id)} disabled={secretScanRunningId === repo.id}>
                          {secretScanRunningId === repo.id ? "Scanning…" : "Run scan"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ---- Evidence Inspector ---- */}
      <div>
        <SectionLabel
          right={
            <div style={{ display: "flex", gap: 8 }}>
              <input
                className="input input-sm" placeholder="Filter by repository…"
                value={evRepository} onChange={e => setEvRepository(e.target.value)}
                style={{ fontSize: 11, padding: "4px 8px" }}
              />
              <select className="input input-sm" value={evSeverity} onChange={e => setEvSeverity(e.target.value)}
                style={{ fontSize: 11, padding: "4px 8px" }}>
                <option value="">All severities</option>
                {["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"].map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <button type="button" className="btn btn-sm" onClick={verifyChain} disabled={chainVerifying}>
                {chainVerifying ? "Verifying…" : "Verify Chain"}
              </button>
            </div>
          }
        >
          Evidence Inspector
        </SectionLabel>

        {chainVerifyResult && (
          <div className="mono" style={{
            fontSize: 11, marginBottom: 8, padding: "6px 10px", borderRadius: 4,
            background: chainVerifyResult.valid ? "var(--green-bg, rgba(16,185,129,0.1))" : "var(--red-bg, rgba(239,68,68,0.1))",
            color: chainVerifyResult.valid ? "var(--green-ink, #059669)" : "var(--red-ink, #dc2626)",
          }}>
            {chainVerifyResult.error
              ? `Chain verification error: ${chainVerifyResult.error}`
              : chainVerifyResult.checked === 0
              ? "Chain verification: nothing to verify yet (no chained records)"
              : chainVerifyResult.valid
              ? `Chain verification: OK — ${chainVerifyResult.checked} record(s) verified, unbroken`
              : `Chain verification FAILED at record #${chainVerifyResult.break_at_id} — a row may have been altered, deleted, or reordered`}
          </div>
        )}

        {evLoading && !evRecords.length ? <Empty>Loading…</Empty> : !evRecords.length ? (
          <Empty>No SARIF evidence ingested yet — see /evidence/webhook setup docs.</Empty>
        ) : (
          <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                  {["Repository", "Rule", "Severity", "CWE/CVE", "File : Line", "Commit", "Scan", "Ingested", ""].map(h => (
                    <th key={h} style={{ padding: "8px 12px", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {evRecords.map(rec => (
                  <tr key={rec.id} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{rec.repository}</td>
                    <td style={{ padding: "8px 12px" }}>{rec.rule_id}</td>
                    <td style={{ padding: "8px 12px" }}><DmSeverityPill severity={rec.severity} /></td>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{[rec.cwe, rec.cve].filter(Boolean).join(" / ") || "—"}</td>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{rec.file_path ? `${rec.file_path}:${rec.line_number ?? "?"}` : "—"}</td>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)", color: "var(--ink-3)" }}>{(rec.commit_sha || "").slice(0, 8) || "—"}</td>
                    <td style={{ padding: "8px 12px" }}>{rec.scan_status}</td>
                    <td style={{ padding: "8px 12px", color: "var(--ink-3)" }}>{new Date(rec.ingested_at).toLocaleString()}</td>
                    <td style={{ padding: "8px 12px", textAlign: "right" }}>
                      {verifyResults[rec.id] === undefined ? (
                        <button type="button" className="btn btn-sm" onClick={() => verifyRecord(rec.id)}>Verify</button>
                      ) : (
                        <span className="mono" style={{ fontSize: 10, fontWeight: 700, color: verifyResults[rec.id] ? "var(--green-ink)" : "var(--red-ink)" }}>
                          {verifyResults[rec.id] ? "✓ signature valid" : "✗ signature mismatch"}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ---- Drift Timeline ---- */}
      <div style={{ marginTop: 24 }}>
        <SectionLabel
          right={
            <label style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 5 }}>
              <input type="checkbox" checked={driftOpenOnly} onChange={e => setDriftOpenOnly(e.target.checked)} />
              Open only
            </label>
          }
        >
          Drift Timeline
        </SectionLabel>
        <div className="panel-sub" style={{ marginBottom: 8 }}>
          Every branch-protection control that flipped since the last audit, either
          direction. A row resolved shortly after it was detected is a short-lived
          "2am override" — briefly weakened, then restored before a single
          point-in-time check would have caught it.
        </div>
        {!driftEvents.length ? <Empty>No drift detected — nothing has changed between consecutive audits.</Empty> : (
          <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                  {["Repository", "Control", "Direction", "Expected", "Actual", "Detected", "Resolved"].map(h => (
                    <th key={h} style={{ padding: "8px 12px", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {driftEvents.map(ev => (
                  <tr key={ev.id} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{ev.resource}</td>
                    <td style={{ padding: "8px 12px" }}>{ev.control_name}</td>
                    <td style={{ padding: "8px 12px" }}>
                      <window.Pill tone={ev.direction === "regressed" ? "bad" : "good"}>{ev.direction}</window.Pill>
                    </td>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)", fontSize: 10.5 }}>{JSON.stringify(ev.expected_state)}</td>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)", fontSize: 10.5 }}>{JSON.stringify(ev.actual_state)}</td>
                    <td style={{ padding: "8px 12px", color: "var(--ink-3)" }}>{new Date(ev.detected_at).toLocaleString()}</td>
                    <td style={{ padding: "8px 12px", color: "var(--ink-3)" }}>
                      {ev.resolved_at ? new Date(ev.resolved_at).toLocaleString() : <span style={{ color: "var(--red-ink)", fontWeight: 700 }}>OPEN</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ---- Risk Waivers ---- */}
      <div style={{ marginTop: 24 }}>
        <SectionLabel
          right={
            <select className="input input-sm" value={waiverStatus} onChange={e => setWaiverStatus(e.target.value)}
              style={{ fontSize: 11, padding: "4px 8px" }}>
              <option value="">All statuses</option>
              {["ACTIVE", "EXPIRED", "REVOKED"].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          }
        >
          Risk Waivers
        </SectionLabel>
        <div className="panel-sub" style={{ marginBottom: 8 }}>
          Documented, time-boxed exceptions approved through the HITL Approval Inbox
          (gate type "DevOps Monitoring · SCM Exception"). Expired waivers are
          automatically re-opened as failing by the hourly expiry sweep.
        </div>
        {!waivers.length ? <Empty>No waivers on record.</Empty> : (
          <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                  {["Vulnerability", "Reason", "Compensating Control", "Approved By", "Expires", "Status", ""].map(h => (
                    <th key={h} style={{ padding: "8px 12px", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {waivers.map(w => (
                  <tr key={w.id} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{(w.vulnerability_hash || "").slice(0, 12)}…</td>
                    <td style={{ padding: "8px 12px" }}>{w.reason}</td>
                    <td style={{ padding: "8px 12px" }}>{w.compensating_control || "—"}</td>
                    <td style={{ padding: "8px 12px" }}>{w.approved_by}</td>
                    <td style={{ padding: "8px 12px", color: "var(--ink-3)" }}>{new Date(w.expires_at).toLocaleString()}</td>
                    <td style={{ padding: "8px 12px" }}><DmWaiverStatusPill status={w.status} /></td>
                    <td style={{ padding: "8px 12px", textAlign: "right" }}>
                      {w.status === "ACTIVE" && (
                        <button type="button" className="btn btn-sm" onClick={() => {
                          fetch(`${_devopsBase()}/scm-audit/waivers/${w.id}/revoke`, { method: "POST", credentials: "include" })
                            .finally(loadWaivers);
                        }}>Revoke</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ---- Pipeline Attestations ---- */}
      <div style={{ marginTop: 24 }}>
        <SectionLabel>Pipeline Attestations</SectionLabel>
        <div className="panel-sub" style={{ marginBottom: 8 }}>
          Provenance metadata submitted alongside each CI run — OIDC identity, a
          structural SLSA level estimate, an environment-variable hash (detects an
          injected SKIP_TESTS/DISABLE_SAST flag without storing raw values),
          runner metadata, Cosign/Sigstore verification status, and SBOM
          license-risk. See POST /evidence/attestation.
        </div>
        {!attestations.length ? <Empty>No pipeline attestations ingested yet.</Empty> : (
          <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                  {["Commit", "OIDC Actor", "SLSA Level", "Runner", "Cosign", "SBOM", "License Risk", "Ingested"].map(h => (
                    <th key={h} style={{ padding: "8px 12px", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {attestations.map(a => (
                  <tr key={a.id} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{(a.commit_sha || "").slice(0, 8)}</td>
                    <td style={{ padding: "8px 12px" }}>{a.oidc_actor || "—"}</td>
                    <td style={{ padding: "8px 12px" }}>{a.slsa_level != null ? `SLSA L${a.slsa_level}` : "—"}</td>
                    <td style={{ padding: "8px 12px" }}>{a.runner_type || "—"}</td>
                    <td style={{ padding: "8px 12px" }}>
                      <window.Pill tone={a.cosign_verified === "true" ? "good" : a.cosign_verified === "false" ? "bad" : "neutral"}>
                        {a.cosign_verified || "n/a"}
                      </window.Pill>
                    </td>
                    <td style={{ padding: "8px 12px" }}>{a.sbom_format || "—"}</td>
                    <td style={{ padding: "8px 12px" }}>
                      {a.license_risk ? <window.Pill tone="warn">copyleft</window.Pill> : "—"}
                    </td>
                    <td style={{ padding: "8px 12px", color: "var(--ink-3)" }}>{new Date(a.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ---- ITSM Tickets & SLA ---- */}
      <div style={{ marginTop: 24 }}>
        <SectionLabel
          right={
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
                Open {itsmSummary.open} · Breached {itsmSummary.breached} · At risk (24h) {itsmSummary.at_risk_24h}
              </span>
              <select className="input input-sm" value={itsmStatus} onChange={e => setItsmStatus(e.target.value)}
                style={{ fontSize: 11, padding: "4px 8px" }}>
                <option value="">All statuses</option>
                {["open", "in_progress", "resolved", "closed", "cancelled"].map(s => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
              </select>
              <label style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 5 }}>
                <input type="checkbox" checked={itsmBreachedOnly} onChange={e => setItsmBreachedOnly(e.target.checked)} />
                Breached only
              </label>
            </div>
          }
        >
          ITSM Tickets & SLA
        </SectionLabel>
        <div className="panel-sub" style={{ marginBottom: 8 }}>
          Jira/ServiceNow tickets opened against DevOps Monitoring findings, tracked against
          a severity-based remediation SLA independent of the external system — an overdue
          ticket is flagged and its finding re-opened as failing by the hourly breach sweep,
          even if nobody is watching Jira/ServiceNow. Register a connector (System type "Jira
          (ITSM SLA Bridge)" or "ServiceNow (ITSM SLA Bridge)") on the Dendrai UBO Configuration
          screen, then open tickets via POST /itsm/tickets.
        </div>
        {!itsmTickets.length ? <Empty>No ITSM tickets tracked yet.</Empty> : (
          <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left" }}>
                  {["Ticket", "System", "Finding", "Severity", "Status", "SLA Due", "Breached", ""].map(h => (
                    <th key={h} style={{ padding: "8px 12px", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {itsmTickets.map(t => (
                  <tr key={t.id} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{t.external_ticket_key}</td>
                    <td style={{ padding: "8px 12px" }}>{t.external_system}</td>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)" }}>{(t.finding_hash || "").slice(0, 12)}…</td>
                    <td style={{ padding: "8px 12px" }}><DmSeverityPill severity={t.severity} /></td>
                    <td style={{ padding: "8px 12px" }}><DmTicketStatusPill status={t.status} /></td>
                    <td style={{ padding: "8px 12px", color: "var(--ink-3)" }}>{new Date(t.sla_due_at).toLocaleString()}</td>
                    <td style={{ padding: "8px 12px" }}>
                      {t.sla_breached_at
                        ? <span style={{ color: "var(--red-ink)", fontWeight: 700 }}>BREACHED</span>
                        : <span style={{ color: "var(--ink-4)" }}>—</span>}
                    </td>
                    <td style={{ padding: "8px 12px", textAlign: "right" }}>
                      <button type="button" className="btn btn-sm" onClick={() => syncTicket(t.id)} disabled={syncingTicketId === t.id}>
                        {syncingTicketId === t.id ? "Syncing…" : "Sync"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { DevopsMonitoringScreen });
