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
      )}
    </div>
  );
}

Object.assign(window, { ModelHealthScreen });
