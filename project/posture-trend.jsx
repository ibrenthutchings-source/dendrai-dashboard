/* ============================================================
   Posture Trend — quarter/run-over-run risk-score deltas for a
   ticker. Backward-looking (actual completed runs), distinct from
   forecasting.js's forward-looking quarter-ahead projections.
   Standalone nav screen (not a modal) — the data is scoped to the
   ticker, not the current session's active run, so it's useful
   even before the pipeline has been run this session.
   ============================================================ */
import {
  ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from 'recharts';

function EmptyNote({ text }) {
  return <div style={{ fontSize: 11.5, color: "var(--ink-4)", fontStyle: "italic" }}>{text}</div>;
}

// Reverse-chronological by default (newest run first) — toggled per-column
// by clicking a header. Sorting is display-only: the underlying `rows` array
// stays oldest->newest for the chart and for the delta-vs-prior calc, which
// depends on chronological order.
function _sortRows(rows, sort) {
  const dir = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    let av = a[sort.key], bv = b[sort.key];
    if (sort.key === "run_at") {
      av = av ? new Date(av).getTime() : null;
      bv = bv ? new Date(bv).getTime() : null;
    }
    if (av == null && bv == null) return 0;
    if (av == null) return 1;  // nulls sort last regardless of direction
    if (bv == null) return -1;
    if (av < bv) return -1 * dir;
    if (av > bv) return 1 * dir;
    return 0;
  });
}

function SortableTh({ label, sortKey, sort, onSort }) {
  const active = sort.key === sortKey;
  return (
    <th
      onClick={() => onSort(sortKey)}
      style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
      title={`Sort by ${label}`}
    >
      {label}
      <span className="mono" style={{
        marginLeft: 4, fontSize: 9,
        color: active ? "var(--ink)" : "var(--ink-4)", opacity: active ? 1 : 0.5,
      }}>
        {active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
      </span>
    </th>
  );
}

function PostureTrendPanel({ ticker }) {
  const [state, setState] = useState({ loading: false, error: null, runs: null });
  const [sort, setSort] = useState({ key: "run_at", dir: "desc" });

  useEffect(() => {
    if (!ticker) return;
    setState({ loading: true, error: null, runs: null });
    fetch(`/api/history/runs/${encodeURIComponent(ticker)}/posture-trend?limit=20`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(data => setState({ loading: false, error: null, runs: data.runs || [] }))
      .catch(e => setState({ loading: false, error: e.message || "Request failed", runs: null }));
  }, [ticker]);

  const runs = state.runs || [];
  // Client-side deltas — the endpoint returns raw oldest-first snapshots only.
  const rows = runs.map((r, i) => ({
    ...r,
    dateLabel: r.run_at ? new Date(r.run_at).toLocaleDateString() : "—",
    delta: i > 0 && r.avg_score != null && runs[i - 1].avg_score != null
      ? r.avg_score - runs[i - 1].avg_score : null,
  }));
  // avg_score is NULL for a completed run with zero rows in risk_scores yet
  // (get_posture_trend LEFT JOINs against risk_scores) — a real, valid run,
  // just nothing to plot. Recharts' Line/Area default to connectNulls=false,
  // so leaving these in `data` breaks the trend into disconnected segments
  // and strands the next real point as a floating, unconnected dot. The
  // detail table below still shows every run (including these, as "—");
  // the chart only needs the ones that actually have a score to trend.
  const chartRows = rows.filter(r => r.avg_score != null);

  function toggleSort(key) {
    setSort(prev => prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" });
  }

  const sortedRows = useMemo(() => _sortRows(rows, sort), [rows, sort]);

  function downloadJson() {
    if (!state.runs) return;
    const blob = new Blob([JSON.stringify(state.runs, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `dendrai_posture_trend_${ticker || "ticker"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function ChartTooltip({ active, payload }) {
    if (!active || !payload?.length) return null;
    const pt = payload[0]?.payload ?? {};
    return (
      <div style={{
        background: 'var(--bg)', border: '1px solid var(--line-strong)', borderRadius: 6,
        padding: '6px 10px', fontSize: 11, fontFamily: 'Geist Mono, monospace',
        boxShadow: '0 2px 8px rgba(0,0,0,0.12)', pointerEvents: 'none',
      }}>
        <div style={{ color: 'var(--ink-3)', fontSize: 9, marginBottom: 2 }}>{pt.dateLabel}</div>
        <div style={{ color: 'var(--ink)', fontWeight: 600, fontSize: 13 }}>
          {pt.avg_score != null ? pt.avg_score.toFixed(2) : '—'} avg score
        </div>
        <div style={{ color: 'var(--ink-3)', fontSize: 9 }}>
          {pt.red_count}R · {pt.amber_count}A · {pt.green_count}G · {pt.risk_count} risks
        </div>
      </div>
    );
  }

  return (
    <div className="panel active">
      <div className="panel-head">
        <div>
          <div className="kicker">Risk Assessment</div>
          <div className="panel-title mt-8">Posture Trend</div>
          <div className="panel-sub">
            {ticker ? `${ticker} · completed runs, oldest → newest` : "Set a ticker in Mission Control to see its posture trend."}
          </div>
        </div>
        <button className="btn btn-sm" onClick={downloadJson} disabled={!rows.length}>
          <Icon name="download" size={11}/> Download JSON
        </button>
      </div>

      {state.loading && (
        <div style={{ padding: "40px 0", textAlign: "center", color: "var(--ink-3)", fontSize: 12 }}>
          Loading posture history…
        </div>
      )}

      {state.error && (
        <div style={{
          margin: "10px 0", padding: "10px 14px", borderRadius: 6,
          background: "var(--red-soft, #fff0f0)", border: "1px solid var(--red, #e05252)",
          fontSize: 11.5, color: "var(--red-ink, #b93333)",
        }}>
          Failed to load posture trend — {state.error}
        </div>
      )}

      {state.runs && rows.length === 0 && (
        <EmptyNote text="No completed runs found for this ticker yet. Posture trend needs at least one DB-persisted run (MCP mode)." />
      )}

      {rows.length > 0 && (
        <>
          <div className="rep-section">
            <h3>Average Risk Score <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>· {rows.length} run{rows.length === 1 ? "" : "s"}</span></h3>
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={chartRows} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
                <XAxis dataKey="dateLabel" tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
                  tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
                {/* Risk scores here are on a 0–25 scale (see risk-engine.js's
                    RAG banding: R >= 17.5, A >= 12.5, G below) — a hardcoded
                    [0, 10] domain used to clip/distort any run averaging
                    above 10. `dataMax` tracks whatever scale is actually in
                    use instead of assuming one. */}
                <YAxis domain={[0, 'dataMax']} tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
                  tickLine={false} axisLine={false} width={28} />
                <Tooltip content={<ChartTooltip/>} />
                <Area type="monotone" dataKey="avg_score" stroke="none" fill="var(--acc)" fillOpacity={0.08} />
                <Line type="monotone" dataKey="avg_score" stroke="var(--acc)" strokeWidth={2} dot={{ r: 3 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <div className="rep-section">
            <h3>Run-over-Run Detail</h3>
            <table className="rep-table">
              <thead>
                <tr>
                  <SortableTh label="Run Date" sortKey="run_at" sort={sort} onSort={toggleSort} />
                  <SortableTh label="Avg Score" sortKey="avg_score" sort={sort} onSort={toggleSort} />
                  <SortableTh label="Δ vs. prior" sortKey="delta" sort={sort} onSort={toggleSort} />
                  <SortableTh label="RAG (R/A/G)" sortKey="red_count" sort={sort} onSort={toggleSort} />
                  <SortableTh label="Risks" sortKey="risk_count" sort={sort} onSort={toggleSort} />
                </tr>
              </thead>
              <tbody>
                {sortedRows.map(r => (
                  <tr key={r.run_id}>
                    <td className="mono" style={{ fontSize: 10 }}>{r.dateLabel}</td>
                    <td className="mono">{r.avg_score != null ? r.avg_score.toFixed(2) : "—"}</td>
                    <td className="mono" style={{
                      color: r.delta == null ? "var(--ink-3)" : r.delta > 0 ? "var(--red-ink)" : r.delta < 0 ? "var(--green-ink)" : "var(--ink-3)",
                    }}>
                      {r.delta == null ? "—" : `${r.delta > 0 ? "▲" : r.delta < 0 ? "▼" : "–"} ${Math.abs(r.delta).toFixed(2)}`}
                    </td>
                    <td className="mono" style={{ fontSize: 10 }}>
                      <span style={{ color: "var(--red-ink)" }}>{r.red_count}</span>
                      {" / "}
                      <span style={{ color: "var(--amber-ink)" }}>{r.amber_count}</span>
                      {" / "}
                      <span style={{ color: "var(--green-ink)" }}>{r.green_count}</span>
                    </td>
                    <td className="mono" style={{ fontSize: 10 }}>{r.risk_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 8, lineHeight: 1.5 }}>
              Higher score = higher risk; Δ shown red when posture worsened, green when it improved.
            </div>
          </div>
        </>
      )}
    </div>
  );
}

Object.assign(window, { PostureTrendPanel });
