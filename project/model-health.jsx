/* ============================================================
   Model Health — forecast backtest accuracy trend + statistical
   drift detection (Population Stability Index) on financial ratios
   and FRED macro regime indicators. On-demand analytics (computed
   live on each load), not a background alerting system — there's no
   in-repo job scheduler yet. Not hardcoded admin-only — participates
   in the normal auth.screen_permissions matrix like Token Usage.

   Data comes from GET /api/mcp/model-health/summary.
   ============================================================ */

const _MH_FLAG_STYLE = {
  stable:             { bg: "var(--green-soft)", ink: "var(--green-ink)", label: "Stable" },
  watch:               { bg: "var(--amber-soft)", ink: "var(--amber-ink)", label: "Watch" },
  drift:               { bg: "var(--red-soft)",   ink: "var(--red-ink)",   label: "Drift" },
  insufficient_data:   { bg: "var(--surface-2)",  ink: "var(--ink-4)",     label: "Insufficient data" },
};

const _MH_RATIO_LABELS = {
  revenue_growth: "Revenue Growth", gross_margin: "Gross Margin", net_margin: "Net Margin",
  fcf_margin: "FCF Margin", rd_intensity: "R&D Intensity", sga_intensity: "SG&A Intensity",
  asset_growth: "Asset Growth", cash_ratio: "Cash Ratio",
};

function MHFlagBadge({ flag }) {
  const s = _MH_FLAG_STYLE[flag] || _MH_FLAG_STYLE.insufficient_data;
  return (
    <span className="mono" style={{
      fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999,
      background: s.bg, color: s.ink, whiteSpace: "nowrap",
    }}>
      {s.label}
    </span>
  );
}

function MHDriftRow({ label, psi, flag, nBaseline, nCurrent }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "8px 0", borderBottom: "1px solid var(--line)", gap: 12,
    }}>
      <div>
        <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink)" }}>{label}</div>
        <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 2 }}>
          {psi != null ? `PSI ${psi.toFixed(3)}` : "PSI —"} · baseline n={nBaseline} · current n={nCurrent}
        </div>
      </div>
      <MHFlagBadge flag={flag} />
    </div>
  );
}

function MHBacktestTable({ trend }) {
  const byModel = React.useMemo(() => {
    const m = new Map();
    for (const r of trend || []) {
      if (!m.has(r.model)) m.set(r.model, []);
      m.get(r.model).push(r); // already ordered oldest-first by the backend
    }
    return [...m.entries()].map(([model, rows]) => {
      const first = rows[0], last = rows[rows.length - 1];
      const mapeDelta = (first?.mape != null && last?.mape != null) ? last.mape - first.mape : null;
      return {
        model, runs: rows.length,
        latestMape: last?.mape ?? null,
        mapeDelta,
        latestWeight: last?.calibrated_weight ?? null,
      };
    }).sort((a, b) => (b.latestMape ?? 0) - (a.latestMape ?? 0));
  }, [trend]);

  if (!byModel.length) return <Empty>No backtest history yet — run the pipeline a few times to populate this.</Empty>;

  return (
    <div>
      {byModel.map(m => (
        <div key={m.model} style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "8px 0", borderBottom: "1px solid var(--line)", gap: 12,
        }}>
          <div>
            <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink)" }}>{m.model}</div>
            <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 2 }}>
              {m.runs} run{m.runs !== 1 ? "s" : ""} in window
              {m.latestWeight != null && ` · ensemble weight ${(m.latestWeight * 100).toFixed(0)}%`}
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div className="mono" style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>
              {m.latestMape != null ? `${m.latestMape.toFixed(2)}% MAPE` : "—"}
            </div>
            {m.mapeDelta != null && (
              <div className="mono" style={{ fontSize: 9.5, color: m.mapeDelta > 0 ? "var(--red-ink)" : "var(--green-ink)" }}>
                {m.mapeDelta > 0 ? "▲" : "▼"} {Math.abs(m.mapeDelta).toFixed(2)}pp vs. window start
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

const _MH_INCIDENT_STATUS_STYLE = {
  open:         { bg: "var(--red-soft)",   ink: "var(--red-ink)",   label: "Open" },
  acknowledged: { bg: "var(--amber-soft)", ink: "var(--amber-ink)", label: "Acknowledged" },
  resolved:     { bg: "var(--green-soft)", ink: "var(--green-ink)", label: "Resolved" },
};

function DriftIncidentRow({ incident, onUpdate, saving }) {
  const [owner, setOwner] = React.useState(incident.owner || "");
  const [notes, setNotes] = React.useState(incident.notes || "");
  const st = _MH_INCIDENT_STATUS_STYLE[incident.status] || _MH_INCIDENT_STATUS_STYLE.open;

  React.useEffect(() => { setOwner(incident.owner || ""); }, [incident.owner]);
  React.useEffect(() => { setNotes(incident.notes || ""); }, [incident.notes]);

  return (
    <div style={{
      border: "1px solid var(--line)", borderRadius: 6, padding: "10px 12px", marginBottom: 8,
      background: "var(--surface)",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="mono" style={{
            fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999,
            background: st.bg, color: st.ink,
          }}>{st.label}</span>
          <span style={{ fontSize: 11.5, fontWeight: 600 }}>{incident.metric_key}</span>
          <span style={{ fontSize: 9.5, color: "var(--ink-4)" }}>
            {incident.metric_kind === "ratio" ? "Financial ratio" : "FRED macro series"}
          </span>
        </div>
        <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)" }}>
          {incident.psi != null ? `PSI ${incident.psi.toFixed(3)}` : ""} · detected {new Date(incident.detected_at).toLocaleDateString()}
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 8, marginBottom: 8 }}>
        <input className="code-input" style={{ fontSize: 11 }} placeholder="Owner (unassigned)"
          value={owner} onChange={e => setOwner(e.target.value)}
          onBlur={() => owner !== (incident.owner || "") && onUpdate(incident.id, { owner })} />
        <input className="code-input" style={{ fontSize: 11 }} placeholder="Notes"
          value={notes} onChange={e => setNotes(e.target.value)}
          onBlur={() => notes !== (incident.notes || "") && onUpdate(incident.id, { notes })} />
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        {incident.status !== "acknowledged" && incident.status !== "resolved" && (
          <button className="btn btn-sm" disabled={saving} onClick={() => onUpdate(incident.id, { status: "acknowledged" })}>Acknowledge</button>
        )}
        {incident.status !== "resolved" && (
          <button className="btn btn-sm btn-acc" disabled={saving} onClick={() => onUpdate(incident.id, { status: "resolved" })}>Resolve</button>
        )}
        {incident.status === "resolved" && (
          <button className="btn btn-sm" disabled={saving} onClick={() => onUpdate(incident.id, { status: "open" })}>Reopen</button>
        )}
      </div>
    </div>
  );
}

function DriftIncidentsPanel() {
  const [incidents, setIncidents] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [savingId, setSavingId] = React.useState(null);
  const [showResolved, setShowResolved] = React.useState(false);

  const load = React.useCallback(() => {
    return fetch("/api/mcp/model-health/drift-incidents", { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setIncidents(d.rows || []); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => { load(); }, [load]);

  async function handleUpdate(id, fields) {
    setSavingId(id);
    setIncidents(rows => rows.map(r => r.id === id ? { ...r, ...fields } : r));
    try {
      await fetch(`/api/mcp/model-health/drift-incidents/${id}`, {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields),
      });
    } catch (_) {}
    await load();
    setSavingId(null);
  }

  const visible = showResolved ? incidents : incidents.filter(i => i.status !== "resolved");
  const openCount = incidents.filter(i => i.status === "open").length;

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <div className="kicker">
          Drift Incidents{openCount > 0 && (
            <span className="mono" style={{ marginLeft: 8, fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: "var(--red-soft)", color: "var(--red-ink)" }}>
              {openCount} OPEN
            </span>
          )}
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10.5, color: "var(--ink-3)", cursor: "pointer" }}>
          <input type="checkbox" checked={showResolved} onChange={e => setShowResolved(e.target.checked)} />
          Show resolved
        </label>
      </div>
      <div style={{ fontSize: 10, color: "var(--ink-4)", marginBottom: 10 }}>
        Every drift detection the background watch (every few hours) finds becomes a tracked incident here —
        assign an owner, acknowledge, and resolve, so there's a governed record of what happened, not just a
        webhook ping. Re-alerting only stops once an incident is resolved.
      </div>
      {loading ? <Empty>Loading…</Empty> : !visible.length ? (
        <Empty>{incidents.length ? "No open incidents — everything's resolved." : "No drift ever detected."}</Empty>
      ) : (
        visible.map(inc => (
          <DriftIncidentRow key={inc.id} incident={inc} onUpdate={handleUpdate} saving={savingId === inc.id} />
        ))
      )}
    </div>
  );
}

function AgentCalibrationBar({ label, pct, sample }) {
  const tone = pct == null ? "var(--ink-4)" : pct >= 0.7 ? "var(--green-ink)" : pct >= 0.4 ? "var(--amber-ink)" : "var(--red-ink)";
  const barBg = pct == null ? "var(--surface-2)" : pct >= 0.7 ? "var(--green-soft)" : pct >= 0.4 ? "var(--amber-soft)" : "var(--red-soft)";
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 3 }}>
        <span style={{ fontWeight: 600 }}>{label}</span>
        <span className="mono" style={{ color: tone, fontWeight: 700 }}>
          {pct == null ? "no data yet" : `${(pct * 100).toFixed(0)}%`}
          {sample != null && <span style={{ color: "var(--ink-4)", fontWeight: 400 }}> (n={sample})</span>}
        </span>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: "var(--surface-2)", overflow: "hidden" }}>
        {pct != null && <div style={{ height: "100%", width: `${Math.round(pct * 100)}%`, background: barBg, borderLeft: `2px solid ${tone}` }} />}
      </div>
    </div>
  );
}

function AgentCalibrationPanel() {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    fetch("/api/mcp/observability/agent-calibration", { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setData(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const agents = data?.agents || [];

  return (
    <div style={{ marginBottom: 24 }}>
      <div className="kicker" style={{ marginBottom: 4 }}>Agent Calibration</div>
      <div style={{ fontSize: 10, color: "var(--ink-4)", marginBottom: 12 }}>
        Of the cases each Council member (or the Policy-as-Code check, or the LLM 4th opinion) voted
        ESCALATE on, what fraction did a human reviewer actually confirm? This is measured from real
        human-review outcomes, not author-chosen confidence formulas — it only fills in as review volume
        accumulates.
      </div>
      {loading ? <Empty>Loading…</Empty> : !agents.length ? (
        <Empty>No reviewed adjudications yet — calibration fills in once human reviewers confirm or override AI verdicts in Controls Monitor.</Empty>
      ) : (
        <div style={{ maxWidth: 480 }}>
          {agents.map(a => (
            <AgentCalibrationBar key={a.agent_name}
              label={`${a.agent_name} — ESCALATE confirmation rate`}
              pct={a.escalate_confirmation_rate} sample={a.escalate_calls} />
          ))}
          <div style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 2 }}>
            Based on {data.reviewed_count} human-reviewed adjudication{data.reviewed_count === 1 ? "" : "s"}.
          </div>
        </div>
      )}
    </div>
  );
}

function ModelHealthScreen() {
  const RefreshBadge = window.RefreshBadge;
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [lastRefresh, setLastRefresh] = React.useState(null);

  const load = React.useCallback(() => {
    setLoading(true);
    setError(null);
    return fetch("/api/mcp/model-health/summary", { credentials: "include" })
      .then(res => {
        if (!res.ok) throw new Error(`Failed to load model health (${res.status})`);
        return res.json();
      })
      .then(d => { setData(d); setLastRefresh(new Date()); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => { load(); }, [load]);

  const ratioDrift = data?.ratio_drift || [];
  const fredDrift = data?.fred_drift || [];

  return (
    <div className="scope-screen" data-screen-label="Model Health">
      <div className="panel-head">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div className="kicker">Governance · Configuration</div>
            <div className="panel-title mt-8">Model Health</div>
            <div className="panel-sub">
              Forecast backtest accuracy over recent runs, and statistical drift (Population Stability Index) on the financial
              ratios feeding risk scoring and on broad FRED macro regime indicators. A background watch (every few hours)
              alerts on newly-detected drift — this on-demand view recomputes live on every load/refresh, independent of that.
            </div>
          </div>
          <RefreshBadge lastRefresh={lastRefresh} onRefresh={load} loading={loading} />
        </div>
      </div>

      {error && (
        <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>
      )}

      {loading ? <Empty>Loading…</Empty> : (
        <>
        <DriftIncidentsPanel />
        <AgentCalibrationPanel />
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 340 }}>
            <div className="kicker" style={{ marginBottom: 8 }}>Backtest Accuracy Trend</div>
            <MHBacktestTable trend={data?.backtest_trend} />
          </div>

          <div style={{ flex: 1, minWidth: 340 }}>
            <div className="kicker" style={{ marginBottom: 8 }}>
              Financial Ratio Drift (PSI)
            </div>
            <div style={{ fontSize: 10, color: "var(--ink-4)", marginBottom: 8 }}>
              Cross-sectional across all tickers/runs — tests whether the population of companies being
              analyzed has shifted from what the risk-scoring templates were calibrated against.
            </div>
            {!ratioDrift.length ? <Empty>No ratio history yet.</Empty> : (
              ratioDrift.map(r => (
                <MHDriftRow key={r.ratio} label={_MH_RATIO_LABELS[r.ratio] || r.ratio}
                  psi={r.psi} flag={r.flag} nBaseline={r.n_baseline} nCurrent={r.n_current} />
              ))
            )}

            <div className="kicker" style={{ marginTop: 20, marginBottom: 8 }}>
              FRED Macro Regime Drift (PSI)
            </div>
            {!data?.fred_configured ? (
              <Empty>FRED_API_KEY not configured — set it in project/agentic-tools/.env to enable macro regime drift detection.</Empty>
            ) : !fredDrift.length ? (
              <Empty>No FRED drift data returned.</Empty>
            ) : (
              fredDrift.map(f => (
                <MHDriftRow key={f.series_id} label={f.name}
                  psi={f.psi} flag={f.flag} nBaseline={f.n_baseline} nCurrent={f.n_current} />
              ))
            )}
          </div>
        </div>
        </>
      )}
    </div>
  );
}

Object.assign(window, { ModelHealthScreen });
