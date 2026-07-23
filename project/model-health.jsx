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

const _MH_GATE_LABELS = {
  risk: "Risk Gate (Gate 1)", objective: "Objective Gate (Gate 2)",
  sox_materiality: "SOX Materiality Gate", sox_account: "SOX Account Gate", sox_process: "SOX Process Gate",
};

const _MH_METRIC_KIND_LABEL = {
  ratio: "Financial ratio", fred_series: "FRED macro series", ai_acceptance: "AI acceptance rate",
};

// Correction actions a reviewer can log against a drift incident — the
// structured "what was actually done" record MODEL_CARD.md's "Recommended
// Next Steps" asked for, distinct from `status`. Choosing one auto-resolves
// the incident (see DriftIncidentRow) since logging a correction is itself
// the decision that the incident is handled.
const _MH_CORRECTION_ACTIONS = [
  { value: "rebaselined", label: "Rebaselined" },
  { value: "recalibrated", label: "Recalibrated" },
  { value: "escalated_for_review", label: "Escalated for review" },
  { value: "false_positive", label: "False positive" },
  { value: "no_action_needed", label: "No action needed" },
];
const _MH_CORRECTION_LABEL = Object.fromEntries(_MH_CORRECTION_ACTIONS.map(a => [a.value, a.label]));

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

// Baseline-vs-current bucket overlay — PSI alone says THAT a distribution
// shifted; this shows WHERE. Bars: outlined = baseline share, filled =
// current share, same bucket edges the PSI computation itself used.
function MHDistributionSparkline({ histogram, flag }) {
  if (!histogram) return null;
  const { baseline_pct, current_pct } = histogram;
  const W = 130, H = 40, gap = 1.5;
  const n = baseline_pct.length;
  const barW = (W - gap * (n - 1)) / n;
  const maxPct = Math.max(0.01, ...baseline_pct, ...current_pct);
  const color = flag === "drift" ? "var(--red)" : flag === "watch" ? "var(--amber)" : "var(--green)";

  return (
    <svg width={W} height={H} style={{ flexShrink: 0 }}>
      {baseline_pct.map((bp, i) => {
        const x = i * (barW + gap);
        const cp = current_pct[i] || 0;
        const bh = (bp / maxPct) * (H - 4);
        const ch = (cp / maxPct) * (H - 4);
        return (
          <g key={i}>
            <rect x={x} y={H - bh} width={barW} height={bh} fill="none" stroke="var(--ink-4)" strokeWidth={1} opacity={0.6} />
            <rect x={x} y={H - ch} width={barW} height={ch} fill={color} opacity={0.55} />
          </g>
        );
      })}
    </svg>
  );
}

function MHDriftRow({ label, psi, flag, nBaseline, nCurrent, histogram }) {
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
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <MHDistributionSparkline histogram={histogram} flag={flag} />
        <MHFlagBadge flag={flag} />
      </div>
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

// Incidents only ever get created at PSI >= 0.20 (the standard "drift"
// threshold) — this sub-bands that range so "how bad" reads as more than a
// bare number. Bands are a heuristic layered on top of the same convention
// documented in drift_tool.py, not a separate standard.
const _MH_MAGNITUDE_STYLE = {
  severe:   { label: "Severe drift",   ink: "var(--red-ink)",   min: 0.50 },
  major:    { label: "Major drift",    ink: "var(--red-ink)",   min: 0.30 },
  moderate: { label: "Moderate drift", ink: "var(--amber-ink)", min: 0.20 },
};

function _mhMagnitude(psi) {
  if (psi == null) return null;
  if (psi >= 0.50) return "severe";
  if (psi >= 0.30) return "major";
  if (psi >= 0.20) return "moderate";
  return "moderate"; // persisted incidents are only ever created at psi >= 0.20
}

// PSI alone says THAT a distribution shifted; this reconstructs roughly
// HOW using the same per-bucket histogram already captured in detail.histogram
// (bucket_distributions() in drift_tool.py) — a weighted mean over bucket
// midpoints, baseline vs current.
function _mhHistogramShift(histogram) {
  if (!histogram?.edges?.length) return null;
  const { edges, baseline_pct, current_pct } = histogram;
  const mids = edges.slice(0, -1).map((e, i) => (e + edges[i + 1]) / 2);
  const wMean = (pcts) => mids.reduce((sum, m, i) => sum + m * (pcts[i] || 0), 0);
  const baselineMean = wMean(baseline_pct || []);
  const currentMean = wMean(current_pct || []);
  return { baselineMean, currentMean, delta: currentMean - baselineMean };
}

// What actually drifted, in the metric's own units — ratio fields are
// fractions (revenue_growth 0.05 = 5%) so render as percentage points;
// FRED series have no histogram (2-bucket regime check, too coarse to
// bin) so fall back to the baseline_mean/current_mean drift_tool.py now
// also captures for exactly this purpose.
function _mhShiftText(incident) {
  const d = incident.detail;
  if (!d) return null;
  if (incident.metric_kind === "ratio") {
    const shift = _mhHistogramShift(d.histogram);
    if (!shift) return null;
    const b = shift.baselineMean * 100, c = shift.currentMean * 100, delta = shift.delta * 100;
    return `Population-average shifted from ${b.toFixed(1)}% (baseline) to ${c.toFixed(1)}% (current) — ${delta > 0 ? "+" : ""}${delta.toFixed(1)}pt.`;
  }
  if (incident.metric_kind === "ai_acceptance") {
    if (d.baseline_acceptance_rate == null || d.current_acceptance_rate == null) return null;
    const b = d.baseline_acceptance_rate * 100, c = d.current_acceptance_rate * 100, delta = c - b;
    return `AI-suggestion acceptance rate moved from ${b.toFixed(0)}% (baseline) to ${c.toFixed(0)}% (current) — ${delta > 0 ? "+" : ""}${delta.toFixed(0)}pt.`;
  }
  if (d.baseline_mean != null && d.current_mean != null) {
    const delta = d.current_mean - d.baseline_mean;
    return `Series average moved from ${d.baseline_mean.toFixed(2)} (baseline) to ${d.current_mean.toFixed(2)} (current) — ${delta > 0 ? "+" : ""}${delta.toFixed(2)}.`;
  }
  return null;
}

function _mhNextSteps(incident) {
  const magnitude = _mhMagnitude(incident.psi);
  if (incident.metric_kind === "ratio") {
    const label = _MH_RATIO_LABELS[incident.metric_key] || incident.metric_key;
    if (magnitude === "severe") {
      return `Treat risk scores that key off ${label} with caution until reviewed — a shift this large usually means the risk-scoring template's calibration no longer matches the population being analyzed. Pull the tickers driving the shift, confirm it's a real market change and not a data-quality issue, then re-baseline the template's ${label} thresholds if it holds up.`;
    }
    if (magnitude === "major") {
      return `Spot-check recent runs where ${label} is a key risk driver — confirm the shift reflects genuine conditions rather than an upstream data issue, and flag it to whoever owns risk-template calibration for this ratio.`;
    }
    return `Monitor on the next drift cycle — if ${label} keeps drifting in the same direction, plan a template re-calibration rather than treating this as a one-off.`;
  }
  if (incident.metric_kind === "ai_acceptance") {
    const label = _MH_GATE_LABELS[incident.detail?.gate_type] || incident.detail?.gate_type || incident.metric_key;
    const dir = (incident.detail?.current_acceptance_rate ?? 0) < (incident.detail?.baseline_acceptance_rate ?? 0)
      ? "being overridden more often" : "being accepted more readily";
    if (magnitude === "severe" || magnitude === "major") {
      return `${label} AI suggestions are ${dir} than their own recent history. Sample a few recent items from the review queue (GET /ai/review-queue) or the Approval Inbox for this gate and check whether the AI's advice quality genuinely changed, or whether the underlying risk population did.`;
    }
    return `Monitor — if the ${label} acceptance rate keeps moving in the same direction, treat it as a signal to review recent AI recommendations for this gate, not just the aggregate number.`;
  }
  const name = incident.detail?.name || incident.metric_key;
  if (magnitude === "severe" || magnitude === "major") {
    return `${name} has moved into a materially different regime. Re-validate FRED-correlated forecasts (EPS, Net Income, EBITDA feature weights) that depend on this series — the historical correlation used to derive those weights may no longer hold.`;
  }
  return `Monitor — if ${name} keeps drifting, re-run backtests once enough post-shift data accumulates to confirm whether FRED-correlated forecast weights need recalibration.`;
}

function DriftIncidentDetail({ incident }) {
  const magnitude = _mhMagnitude(incident.psi);
  const mStyle = _MH_MAGNITUDE_STYLE[magnitude];
  const shiftText = _mhShiftText(incident);
  const histogram = incident.detail?.histogram;
  return (
    <div style={{
      background: "var(--surface-2,var(--surface))", border: "1px solid var(--line)",
      borderRadius: 5, padding: "8px 10px", marginBottom: 8, display: "flex", gap: 10, alignItems: "flex-start",
    }}>
      {histogram && <MHDistributionSparkline histogram={histogram} flag="drift" />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 11, marginBottom: 4 }}>
          <span className="mono" style={{ fontWeight: 700, color: mStyle?.ink }}>{mStyle?.label || "Drift"}</span>
          <span className="mono" style={{ color: "var(--ink-4)" }}> · PSI {incident.psi != null ? incident.psi.toFixed(3) : "—"}</span>
        </div>
        {shiftText && <div style={{ fontSize: 10.5, color: "var(--ink-2)", marginBottom: 6 }}>{shiftText}</div>}
        <div style={{ fontSize: 10.5, color: "var(--ink-2)" }}>
          <span style={{ fontWeight: 600, color: "var(--ink-3)" }}>Recommended next steps: </span>
          {_mhNextSteps(incident)}
        </div>
      </div>
    </div>
  );
}

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
          <span style={{ fontSize: 11.5, fontWeight: 600 }}>
            {incident.metric_kind === "ratio" ? (_MH_RATIO_LABELS[incident.metric_key] || incident.metric_key) : (incident.detail?.name || incident.metric_key)}
          </span>
          <span style={{ fontSize: 9.5, color: "var(--ink-4)" }}>
            {incident.metric_kind === "ratio" ? "Financial ratio" : "FRED macro series"}
          </span>
        </div>
        <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)" }}>
          {incident.psi != null ? `PSI ${incident.psi.toFixed(3)}` : ""} · detected {new Date(incident.detected_at).toLocaleDateString()}
        </span>
      </div>
      <DriftIncidentDetail incident={incident} />
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
                  psi={r.psi} flag={r.flag} nBaseline={r.n_baseline} nCurrent={r.n_current} histogram={r.histogram} />
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
