/* ============================================================
   Assess Risk · Run History
   Lets a user pick any past risk-loop run for the ticker and see what
   it produced: scored risks, Beneish M-Score (manipulation) with its 8
   indices, and Altman Z'' (solvency) with its 4 inputs.
   Data: GET /api/history/runs/{ticker} (list) and
         GET /api/history/runs/{ticker}/{run_id} (detail).
   ============================================================ */

const _BEN_IDX = [
  { k: "dsri", label: "DSRI", name: "Days Sales in Receivables", coef: 0.920,  flag: "revenue inflation",    red: 1 },
  { k: "gmi",  label: "GMI",  name: "Gross Margin",              coef: 0.528,  flag: "margin deterioration", red: 1 },
  { k: "aqi",  label: "AQI",  name: "Asset Quality",             coef: 0.404,  flag: "expense capitalisation", red: 1 },
  { k: "sgi",  label: "SGI",  name: "Sales Growth",              coef: 0.892,  flag: "growth pressure",      red: 1 },
  { k: "depi", label: "DEPI", name: "Depreciation",              coef: 0.115,  flag: "slower depreciation",  red: 1 },
  { k: "sgai", label: "SGAI", name: "SG&A Expense",              coef: -0.172, flag: "SG&A inefficiency",    red: 1 },
  { k: "tata", label: "TATA", name: "Total Accruals to Total Assets", coef: 4.679, flag: "earnings exceed cash flow", red: 0 },
  { k: "lvgi", label: "LVGI", name: "Leverage",                  coef: -0.327, flag: "rising leverage",      red: 1 },
];

const _ZIN = [
  { k: "x1", label: "X1", name: "Working Capital / Total Assets" },
  { k: "x2", label: "X2", name: "Retained Earnings / Total Assets" },
  { k: "x3", label: "X3", name: "EBIT / Total Assets" },
  { k: "x4", label: "X4", name: "Book Equity / Total Liabilities" },
];

function _ragColor(rag) {
  const r = String(rag || "").charAt(0).toUpperCase();
  return r === "R" ? "var(--red-ink)" : r === "A" ? "var(--amber-ink)" : r === "G" ? "var(--green-ink)" : "var(--ink-3)";
}

function _fmtWhen(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

function RunHistoryPanel({ ticker }) {
  const [list, setList] = useState({ loading: false, error: null, runs: [] });
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState({ loading: false, error: null, data: null });

  useEffect(() => {
    if (!ticker) return;
    setList({ loading: true, error: null, runs: [] });
    setSelected(null);
    fetch(`/api/history/runs/${encodeURIComponent(ticker)}?limit=50`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(d => {
        const runs = d.runs || [];
        setList({ loading: false, error: null, runs });
        if (runs.length) setSelected(runs[0].run_id);
      })
      .catch(e => setList({ loading: false, error: e.message || "Request failed", runs: [] }));
  }, [ticker]);

  useEffect(() => {
    if (!ticker || selected == null) { setDetail({ loading: false, error: null, data: null }); return; }
    let cancelled = false;
    setDetail({ loading: true, error: null, data: null });
    fetch(`/api/history/runs/${encodeURIComponent(ticker)}/${selected}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(d => { if (!cancelled) setDetail({ loading: false, error: null, data: d }); })
      .catch(e => { if (!cancelled) setDetail({ loading: false, error: e.message || "Request failed", data: null }); });
    return () => { cancelled = true; };
  }, [ticker, selected]);

  if (!ticker) return <div className="stage-detail muted">Run an assessment for a ticker to build history.</div>;
  if (list.loading) return <div className="stage-detail muted">Loading run history…</div>;
  if (list.error) return <div className="stage-detail" style={{ color: "var(--red-ink)" }}>Could not load run history: {list.error}</div>;
  if (!list.runs.length) return <div className="stage-detail muted">No saved runs for {ticker} yet. Completed risk-loop runs appear here.</div>;

  const d = detail.data;
  const MSG = window.MScoreGauge, ZSG = window.ZScoreGauge;
  const ms = d?.beneish_mscore, zs = d?.altman_zscore;
  const defaulted = new Set(ms?.defaulted || []);
  const risks = [...(d?.risk_scores || [])].sort((a, b) => (b.score ?? -1) - (a.score ?? -1));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="stage-detail">
        <h5>Past risk-loop runs · {ticker}</h5>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
          {list.runs.map((r, i) => {
            const active = r.run_id === selected;
            return (
              <button key={r.run_id} onClick={() => setSelected(r.run_id)} className="mono"
                title={`Run #${r.run_id}${r.completed ? "" : " (not completed)"}`}
                style={{
                  fontSize: 10.5, padding: "4px 9px", borderRadius: 5, cursor: "pointer",
                  border: `1px solid ${active ? "var(--ink)" : "var(--line)"}`,
                  background: active ? "var(--ink)" : "var(--surface-2, var(--surface))",
                  color: active ? "var(--surface)" : "var(--ink-2)",
                }}>
                {_fmtWhen(r.run_at)}{i === 0 ? " · latest" : ""} · {r.risk_count ?? 0} risks{r.completed ? "" : " · incomplete"}
              </button>
            );
          })}
        </div>
      </div>

      {detail.loading && <div className="stage-detail muted">Loading run #{selected}…</div>}
      {detail.error && <div className="stage-detail" style={{ color: "var(--red-ink)" }}>Could not load run: {detail.error}</div>}

      {d && (
        <>
          <div className="stage-detail">
            <h5>Run #{d.run_id} · {d.company_name || d.ticker} · {_fmtWhen(d.run_at)}</h5>
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 4 }}>
              {[d.industry, d.appetite_level && `appetite ${d.appetite_level}`,
                d.period_begin && d.period_end && `${d.period_begin} → ${d.period_end}`].filter(Boolean).join(" · ") || "—"}
            </div>
          </div>

          <div className="stage-detail">
            <h5>Scored risks ({risks.length})</h5>
            {risks.length === 0 ? <div className="muted" style={{ fontSize: 11 }}>No risks were stored for this run.</div> : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, marginTop: 6 }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "var(--ink-3)", borderBottom: "1px solid var(--line)" }}>
                    <th style={{ padding: "3px 6px" }}>Ref</th>
                    <th style={{ padding: "3px 6px" }}>Risk</th>
                    <th style={{ padding: "3px 6px", textAlign: "right" }}>Score</th>
                    <th style={{ padding: "3px 6px" }}>RAG</th>
                    <th style={{ padding: "3px 6px", textAlign: "right" }}>Velocity</th>
                  </tr>
                </thead>
                <tbody>
                  {risks.map(r => (
                    <tr key={r.risk_ref} style={{ borderBottom: "1px solid var(--line)" }}>
                      <td className="mono" style={{ padding: "3px 6px", color: "var(--ink-3)" }}>{r.risk_ref}</td>
                      <td style={{ padding: "3px 6px" }}>{r.risk_name}</td>
                      <td className="mono" style={{ padding: "3px 6px", textAlign: "right" }}>{r.score != null ? r.score.toFixed(1) : "—"}</td>
                      <td className="mono" style={{ padding: "3px 6px", color: _ragColor(r.rag_status) }}>{r.rag_status || "—"}</td>
                      <td className="mono" style={{ padding: "3px 6px", textAlign: "right" }}>{r.velocity ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="stage-detail">
            <h5>Earnings Manipulation Risk · Beneish M-Score</h5>
            {ms?.m_score != null && MSG ? (
              <>
                <MSG m={ms.m_score} peers={[]} />
                <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-2)", marginTop: 6, lineHeight: 1.7 }}>
                  M = −4.84 {_BEN_IDX.map(x => `${x.coef < 0 ? "−" : "+"} ${Math.abs(x.coef).toFixed(3)}·${x.label}`).join(" ")}
                </div>
                <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 6, fontSize: 10.5 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--ink-3)", borderBottom: "1px solid var(--line)" }}>
                      <th style={{ padding: "3px 6px" }}>Index</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>Value</th>
                      <th style={{ padding: "3px 6px" }}>Red flag when</th>
                    </tr>
                  </thead>
                  <tbody>
                    {_BEN_IDX.map(x => {
                      const v = ms.inputs?.[x.k];
                      const has = typeof v === "number";
                      const dflt = defaulted.has(x.label);
                      const isRed = has && !dflt && v > x.red;
                      return (
                        <tr key={x.k} style={{ borderBottom: "1px solid var(--line)" }}>
                          <td style={{ padding: "3px 6px" }}>{x.label} <span style={{ color: "var(--ink-3)" }}>({x.name})</span></td>
                          <td className="mono" style={{ padding: "3px 6px", textAlign: "right", color: isRed ? "var(--red-ink)" : "var(--ink-2)", fontWeight: isRed ? 700 : 400 }}>
                            {has ? v.toFixed(x.k === "tata" ? 3 : 2) : "—"}{dflt ? "*" : ""}
                          </td>
                          <td className="mono" style={{ padding: "3px 6px", color: "var(--ink-3)" }}>&gt; {x.red} ({x.flag})</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {defaulted.size > 0 && (
                  <div style={{ fontSize: 9.5, color: "var(--ink-3)", marginTop: 3 }}>
                    * Not computed from filings for this run; neutral default (1.0) used.
                  </div>
                )}
              </>
            ) : <div className="muted" style={{ fontSize: 11 }}>No M-Score was stored for this run.</div>}
          </div>

          <div className="stage-detail">
            <h5>Solvency Risk · Altman Z''-Score</h5>
            {zs?.z_score != null && ZSG ? (
              <>
                <ZSG z={zs.z_score} peers={[]} />
                <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 6, fontSize: 10.5 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--ink-3)", borderBottom: "1px solid var(--line)" }}>
                      <th style={{ padding: "3px 6px" }}>Input</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {_ZIN.map(x => (
                      <tr key={x.k} style={{ borderBottom: "1px solid var(--line)" }}>
                        <td style={{ padding: "3px 6px" }}>{x.label} <span style={{ color: "var(--ink-3)" }}>({x.name})</span></td>
                        <td className="mono" style={{ padding: "3px 6px", textAlign: "right" }}>
                          {typeof zs.inputs?.[x.k] === "number" ? zs.inputs[x.k].toFixed(3) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            ) : <div className="muted" style={{ fontSize: 11 }}>No Z''-Score was stored for this run.</div>}
          </div>
        </>
      )}
    </div>
  );
}

Object.assign(window, { RunHistoryPanel });
export { RunHistoryPanel };
