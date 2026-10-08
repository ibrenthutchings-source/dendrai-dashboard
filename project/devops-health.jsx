/* ============================================================
   DevOps Health — DORA-style change-management metrics, trended.

   db.compute_dora_metrics already computed deployment frequency,
   change-failure rate, and MTTR (honest-null when a window has no
   data — see its own docstring), but the only place it was ever shown
   was a single-window tile buried in the SOC 2 compliance scorecard
   (code-screens.jsx, CC8.1 evidence). This screen trends the same
   three metrics over several trailing windows so a Head of Operations
   can see whether they're improving or worsening, not just today's
   number — see db.compute_dora_trend.

   Data: GET /api/mcp/evidence/dora-metrics/trend
         (window.MCP.doraMetricsTrend).
   ============================================================ */

const _DORA_METRICS = [
  { key: "deployment_frequency_per_day", label: "Deployment frequency", unit: "/day", fmt: v => v.toFixed(2), higherIsBetter: true },
  { key: "change_failure_rate", label: "Change failure rate", unit: "%", fmt: v => (v * 100).toFixed(0), higherIsBetter: false },
  { key: "mttr_hours", label: "Mean time to restore", unit: "h", fmt: v => v.toFixed(1), higherIsBetter: false },
];

function DoraTrendRow({ metric, periods }) {
  const vals = periods.map(p => p[metric.key]);
  const defined = vals.filter(v => v != null);
  const max = Math.max(1e-9, ...defined);
  const latest = vals[vals.length - 1];
  const prior = vals.length > 1 ? vals[vals.length - 2] : null;
  const delta = latest != null && prior != null ? latest - prior : null;
  const improved = delta != null && (metric.higherIsBetter ? delta > 0 : delta < 0);
  const worsened = delta != null && (metric.higherIsBetter ? delta < 0 : delta > 0);

  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 8 }}>
        <div style={{ fontSize: 11.5, fontWeight: 600 }}>{metric.label}</div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontSize: 18, fontWeight: 700, fontFamily: "var(--mono)" }}>
            {latest == null ? "—" : `${metric.fmt(latest)}${metric.unit}`}
          </span>
          {delta != null && (
            <span style={{ fontSize: 10.5, fontWeight: 700, color: improved ? "var(--green-ink)" : worsened ? "var(--red-ink)" : "var(--ink-3)" }}>
              {improved ? "▼ improved" : worsened ? "▲ worsened" : "flat"} vs. prior period
            </span>
          )}
        </div>
      </div>
      <div style={{ display: "flex", gap: 3, alignItems: "flex-end", height: 48 }}>
        {periods.map((p, i) => {
          const v = p[metric.key];
          const h = v == null ? 2 : Math.max(3, (v / max) * 48);
          return (
            <div key={i} title={v == null ? "no data" : `${metric.fmt(v)}${metric.unit}`}
              style={{
                flex: 1, height: h, borderRadius: 2,
                background: v == null ? "var(--line)" : i === periods.length - 1 ? "var(--acc)" : "var(--surface-3)",
              }} />
          );
        })}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "var(--ink-4)", marginTop: 2 }}>
        <span>{periods.length} periods ago</span>
        <span>latest</span>
      </div>
    </div>
  );
}

function DevOpsHealthScreen({ onNavigate } = {}) {
  const { useState, useEffect } = React;
  const [periodDays, setPeriodDays] = useState(7);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    window.MCP.doraMetricsTrend(periodDays, 8)
      .then(d => { setData(d.periods || []); setError(null); })
      .catch(e => setError(e.message || String(e)));
  }, [periodDays]);

  const anyData = (data || []).some(p => p.deployment_count > 0);

  return (
    <div style={{ padding: "0 20px 32px", maxWidth: 900 }}>
      <div className="panel-head" style={{ paddingLeft: 0, paddingRight: 0 }}>
        <div>
          <div className="kicker">Operations Intelligence</div>
          <div className="panel-title mt-8">DevOps Health</div>
          <div className="panel-sub">
            Deployment frequency, change-failure rate, and mean time to restore — real DORA proxies from CI
            attestations and ITSM tickets (see db.compute_dora_metrics), trended across periods instead of a
            single today's-number tile. Lead Time for Changes is not shown — this platform has no
            commit-to-deploy timestamp to compute it honestly.
          </div>
        </div>
        <select className="input" style={{ width: 150 }} value={periodDays} onChange={e => setPeriodDays(Number(e.target.value))}>
          <option value={1}>Daily periods</option>
          <option value={7}>Weekly periods</option>
          <option value={30}>Monthly periods</option>
        </select>
      </div>

      {error && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 10 }}>{error}</div>}
      {!data ? (
        <div style={{ padding: 20, color: "var(--ink-3)", fontSize: 11.5 }}>Loading…</div>
      ) : !anyData ? (
        <Empty>No CI pipeline attestations in any of these periods yet — this screen populates once POST /evidence/attestation is in use.</Empty>
      ) : (
        <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "16px 18px" }}>
          {_DORA_METRICS.map(m => <DoraTrendRow key={m.key} metric={m} periods={data} />)}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { DevOpsHealthScreen });
