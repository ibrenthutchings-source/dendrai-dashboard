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

  const [evRepository, setEvRepository] = React.useState("");
  const [evSeverity, setEvSeverity] = React.useState("");
  const [evRecords, setEvRecords] = React.useState([]);
  const [evLoading, setEvLoading] = React.useState(false);
  const [verifyResults, setVerifyResults] = React.useState({});

  const [driftEvents, setDriftEvents] = React.useState([]);
  const [driftOpenOnly, setDriftOpenOnly] = React.useState(false);
  const [waivers, setWaivers] = React.useState([]);
  const [waiverStatus, setWaiverStatus] = React.useState("");
  const [attestations, setAttestations] = React.useState([]);

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

  function verifyRecord(id) {
    fetch(`${_devopsBase()}/evidence/records/${id}/verify`, { credentials: "include" })
      .then(r => r.json())
      .then(d => setVerifyResults(prev => ({ ...prev, [id]: d.valid })))
      .catch(() => setVerifyResults(prev => ({ ...prev, [id]: false })));
  }

  return (
    <div className="scope-screen" data-screen-label="DevOps Monitoring">
      <div className="panel-head">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div className="kicker">DevOps Monitoring · SCM Integrity & Evidence</div>
            <div className="panel-title mt-8">DevOps Monitoring</div>
            <div className="panel-sub">
              Branch-protection/CODEOWNERS compliance for registered GitHub & GitLab
              repositories, and the SARIF/SAST evidence log feeding their severity SLAs.
              Findings are adjudicated through the same Bronze→Silver→Gold→Council pipeline
              and devops_monitoring Policy-as-Code module as every other governed system.
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

        {loading && !repos.length ? <Empty>Loading…</Empty> : !repos.length ? (
          <Empty>
            No repositories registered yet. Register one via "+ Register repository" —
            it adds a GitHub/GitLab connector on the Dendrai UBO Configuration screen.
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
            </div>
          }
        >
          Evidence Inspector
        </SectionLabel>

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
    </div>
  );
}

Object.assign(window, { DevopsMonitoringScreen });
