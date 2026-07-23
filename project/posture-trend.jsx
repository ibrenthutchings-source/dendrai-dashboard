/* ============================================================
   Posture Trend modal — quarter/run-over-run risk-score deltas
   for a ticker. Backward-looking (actual completed runs), distinct
   from forecasting.js's forward-looking quarter-ahead projections.
   Modal chrome modeled on EvidencePackModal (evidence-pack.jsx).
   ============================================================ */
import {
  ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from 'recharts';

function EmptyNote({ text }) {
  return <div style={{ fontSize: 11.5, color: "var(--ink-4)", fontStyle: "italic" }}>{text}</div>;
}

function PostureTrendModal({ open, onClose, ticker }) {
  const [state, setState] = useState({ loading: false, error: null, runs: null });

  useEffect(() => {
    if (!open || !ticker) return;
    setState({ loading: true, error: null, runs: null });
    fetch(`/api/history/runs/${encodeURIComponent(ticker)}/posture-trend?limit=20`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(data => setState({ loading: false, error: null, runs: data.runs || [] }))
      .catch(e => setState({ loading: false, error: e.message || "Request failed", runs: null }));
  }, [open, ticker]);

  if (!open) return null;

  const runs = state.runs || [];
  // Client-side deltas — the endpoint returns raw oldest-first snapshots only.
  const rows = runs.map((r, i) => ({
    ...r,
    dateLabel: r.run_at ? new Date(r.run_at).toLocaleDateString() : "—",
    delta: i > 0 && r.avg_score != null && runs[i - 1].avg_score != null
      ? r.avg_score - runs[i - 1].avg_score : null,
  }));

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
    <div className="modal open" onClick={(e) => { if (e.target.classList.contains("modal")) onClose(); }}>
      <div className="modal-box" style={{ width: 780 }}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Posture Trend</div>
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 3 }}>
              {ticker || "—"} · completed runs, oldest → newest
            </div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
        </div>

        <div className="modal-body">
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
                  <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
                    <XAxis dataKey="dateLabel" tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
                      tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
                    <YAxis domain={[0, 10]} tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
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
                  <thead><tr><th>Run Date</th><th>Avg Score</th><th>Δ vs. prior</th><th>RAG (R/A/G)</th><th>Risks</th></tr></thead>
                  <tbody>
                    {rows.map(r => (
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

        <div className="modal-foot">
          <span className="mono muted" style={{ fontSize: 11 }}>
            {rows.length > 0 ? `${rows.length} completed run${rows.length === 1 ? "" : "s"}` : ""}
          </span>
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn btn-sm" onClick={downloadJson} disabled={!rows.length}><Icon name="download" size={11}/> Download JSON</button>
            <button className="btn btn-sm btn-primary" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { PostureTrendModal });
