/* ============================================================
   Forecasts panel — revenue / margin + M-Score + FRED correlates
   Models run via FORECASTING + BACKTESTING engines (forecasting.js / backtesting.js).
   Falls back gracefully if engines are not loaded.
   ============================================================ */

const MODEL_COLORS = {
  arima:    "var(--acc)",
  prophet:  "var(--violet)",
  rf:       "var(--amber)",
  ensemble: "var(--ink)",
};
const MODEL_NAMES = {
  arima:    "ARIMA(2,1,1)",
  prophet:  "Prophet-like",
  rf:       "Random Forest",
  ensemble: "Ensemble",
};

function ForecastsPanel({ data, liveMode, livefacts, fredSeries, rssSignals }) {
  const [modelOutput, setModelOutput] = useState(null);
  const [modelRunning, setModelRunning] = useState(false);
  const [modelError, setModelError] = useState(null);

  useEffect(() => {
    if (!data) { setModelOutput(null); return; }
    const hasEngines = typeof FORECASTING !== "undefined" && typeof BACKTESTING !== "undefined";
    if (!hasEngines) return;

    setModelRunning(true);
    setModelError(null);

    const handle = setTimeout(() => {
      try {
        // Build revenue series — prefer EDGAR XBRL annual data when available
        let revSeries = null, revSource = "mock";
        if (livefacts?.revenue?.series) {
          const annual = livefacts.revenue.series
            .filter(x => x.form === "10-K" && x.fp === "FY")
            .sort((a, b) => (a.end < b.end ? -1 : 1));
          if (annual.length >= 6) {
            revSeries = annual.map(x => x.val / 1e6);
            revSource = "edgar";
          }
        }
        if (!revSeries) revSeries = data.revenue.history.map(x => x.v);

        const mgSeries = data.margin.history.map(x => x.v);

        // Walk-forward backtests calibrate ensemble weights
        const revBT = BACKTESTING.backtestAll(revSeries);
        const mgBT  = BACKTESTING.backtestAll(mgSeries);

        const revMapes = [revBT.results.arima?.mape, revBT.results.prophet?.mape, revBT.results.rf?.mape];
        const mgMapes  = [mgBT.results.arima?.mape,  mgBT.results.prophet?.mape,  mgBT.results.rf?.mape];

        // Final forecasts with calibrated weights
        const revFcAll = BACKTESTING.forecastAll(revSeries, null, 4, revMapes);
        const mgFcAll  = BACKTESTING.forecastAll(mgSeries,  null, 4, mgMapes);

        const revEns = revFcAll.ensemble;
        const mgEns  = mgFcAll.ensemble;

        // Use Number.isFinite — ?? passes NaN through, which breaks .toFixed()
        const safeV = (v, fallback) => Number.isFinite(v) ? v : fallback;
        setModelOutput({
          revenue: {
            history: data.revenue.history,
            forecast: data.revenue.forecast.map((f, i) => ({
              q:    f.q,
              base: safeV(revEns?.base[i], f.base),
              lo:   safeV(revEns?.lo[i],   f.lo),
              hi:   safeV(revEns?.hi[i],   f.hi),
            })),
            all: revFcAll,
            backtest: revBT,
            source: revSource,
          },
          margin: {
            history: data.margin.history,
            forecast: data.margin.forecast.map((f, i) => ({
              q:    f.q,
              base: safeV(mgEns?.base[i], f.base),
              lo:   safeV(mgEns?.lo[i],   f.lo),
              hi:   safeV(mgEns?.hi[i],   f.hi),
            })),
            all: mgFcAll,
            backtest: mgBT,
            source: "mock",
          },
        });
      } catch (e) {
        console.error("Forecasting engine error:", e);
        setModelError(e.message);
      }
      setModelRunning(false);
    }, 0);

    return () => clearTimeout(handle);
  }, [data, livefacts]);

  if (!data) return <Empty>Run the loop to populate forecasts, or click Run Loop in the sidebar.</Empty>;

  const rev  = modelOutput?.revenue  ?? data.revenue;
  const mg   = modelOutput?.margin   ?? data.margin;

  const lastHistRev = rev.history[rev.history.length - 1].v;
  const lastFcRev   = rev.forecast[rev.forecast.length - 1].base;
  const revDeltaPct = ((lastFcRev - lastHistRev) / lastHistRev) * 100;

  const lastHistMg = mg.history[mg.history.length - 1].v;
  const lastFcMg   = mg.forecast[mg.forecast.length - 1].base;
  const mgDelta    = (lastFcMg - lastHistMg) * 100;

  const hasEngines = typeof FORECASTING !== "undefined" && typeof BACKTESTING !== "undefined";

  const revFcLast = rev.forecast[rev.forecast.length - 1];
  const mgFcLast  = mg.forecast[mg.forecast.length - 1];

  return (
    <div data-screen-label="Forecasts" className="bb-panel">
      <BBTermHeader
        section="FINANCIAL INTELLIGENCE"
        title="EDGAR XBRL · FRED Macro · ARIMA / Prophet / RF Ensemble"
        liveMode={liveMode}
        status={
          modelRunning ? "⟳  RUNNING FORECASTING MODELS…" :
          modelError   ? `MODEL ERROR: ${modelError.toUpperCase()} — SHOWING MOCK BASELINE` :
          modelOutput  ? `ENSEMBLE FORECAST · WALK-FORWARD CALIBRATED · DATA SOURCE: ${modelOutput.revenue.source.toUpperCase()}` :
          hasEngines   ? "MODELS QUEUED…" :
          "FORECASTING ENGINES NOT LOADED — SHOWING MOCK BASELINE"
        }
        actions={modelRunning ? <span className="spin"/> : null}
      />

      {/* Key metrics ticker */}
      <div className="bb-stat-ticker">
        <div className="bb-ticker-item">
          <div className="bb-ticker-label">REV FORECAST</div>
          <div className={`bb-ticker-val${revDeltaPct >= 0 ? " green" : " red"}`}>${revFcLast.base.toFixed(0)}M</div>
        </div>
        <div className="bb-ticker-item">
          <div className="bb-ticker-label">REV Δ</div>
          <div className={`bb-ticker-val${revDeltaPct >= 0 ? " green" : " red"}`}>{revDeltaPct >= 0 ? "▲" : "▼"}{Math.abs(revDeltaPct).toFixed(1)}%</div>
        </div>
        <div className="bb-ticker-item">
          <div className="bb-ticker-label">MARGIN FCST</div>
          <div className="bb-ticker-val">{mgFcLast.base.toFixed(1)}%</div>
        </div>
        <div className="bb-ticker-item">
          <div className="bb-ticker-label">MARGIN Δ</div>
          <div className={`bb-ticker-val${mgDelta >= 0 ? " green" : " red"}`}>{mgDelta >= 0 ? "▲" : "▼"}{Math.abs(mgDelta).toFixed(0)}bps</div>
        </div>
        <div className="bb-ticker-item">
          <div className="bb-ticker-label">M-SCORE</div>
          <div className={`bb-ticker-val${data.mscore.m > -1.78 ? " red" : data.mscore.m > -2.22 ? " amber" : " green"}`}>{data.mscore.m.toFixed(2)}</div>
        </div>
        <div className="bb-ticker-item">
          <div className="bb-ticker-label">MACRO SIGNAL</div>
          <div className="bb-ticker-val red" style={{fontSize:12,letterSpacing:"0.04em"}}>CONTRACTION</div>
        </div>
        <div className="bb-ticker-item">
          <div className="bb-ticker-label">SENTIMENT</div>
          <div className={`bb-ticker-val${data.sentiment.score >= 0 ? " green" : " red"}`}>{data.sentiment.score > 0 ? "+" : ""}{data.sentiment.score}</div>
        </div>
      </div>

      <div className="bb-content">
      <div className="fcst-row">
        <div className="fcst-card">
          <div className="head">
            <div>
              <div className="ttl">Revenue · TTM</div>
              <div className="sub">
                {modelOutput ? `${modelOutput.revenue.source === "edgar" ? "EDGAR XBRL" : "Mock"} series · ensemble` : "Quarterly $M · 8 history + 4 forecast"}
              </div>
            </div>
            <div style={{textAlign:"right"}}>
              <div className="big-num">${lastFcRev.toFixed(0)}M</div>
              <div className={`delta ${revDeltaPct >= 0 ? "up" : "dn"}`}>
                {revDeltaPct >= 0 ? "▲" : "▼"} {Math.abs(revDeltaPct).toFixed(1)}% vs latest
              </div>
            </div>
          </div>
          <ForecastChart history={rev.history.slice(-8)} forecast={rev.forecast} unit="$M" color="var(--acc)"/>
          {modelOutput?.revenue?.all && (
            <ComponentForecastTable fcAll={modelOutput.revenue.all} labels={data.revenue.forecast.map(f => f.q)} unit="$M" />
          )}
        </div>

        <div className="fcst-card">
          <div className="head">
            <div>
              <div className="ttl">Gross margin</div>
              <div className="sub">Quarterly % · 8 history + 4 forecast</div>
            </div>
            <div style={{textAlign:"right"}}>
              <div className="big-num">{lastFcMg.toFixed(1)}%</div>
              <div className={`delta ${mgDelta >= 0 ? "up" : "dn"}`}>
                {mgDelta >= 0 ? "▲" : "▼"} {Math.abs(mgDelta).toFixed(0)} bps vs latest
              </div>
            </div>
          </div>
          <ForecastChart history={mg.history.slice(-8)} forecast={mg.forecast} unit="%" color="var(--violet)"/>
          {modelOutput?.margin?.all && (
            <ComponentForecastTable fcAll={modelOutput.margin.all} labels={data.margin.forecast.map(f => f.q)} unit="%" />
          )}
        </div>
      </div>

      {modelOutput && (
        <ModelDiagnosticsCard revenue={modelOutput.revenue} margin={modelOutput.margin} />
      )}

      <div className="fcst-row">
        <div className="fcst-card">
          <div className="head">
            <div>
              <div className="ttl">Beneish M-Score</div>
              <div className="sub">Forensic accounting probability of earnings manipulation</div>
            </div>
          </div>
          <MScoreGauge m={data.mscore.m}/>
          <div className="mt-12" style={{fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.55}}>
            <b style={{fontWeight: 500}}>Key driver:</b> {data.mscore.key_driver}. Band breaches RED at M &gt; −1.78. Current reading is in the AMBER (Elevated) zone.
          </div>
          <div className="mt-12" style={{display:"grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 6}}>
            {Object.entries(data.mscore.vars).map(([k, v]) => (
              <div key={k} className="scen-m">
                <div className="l">{k}</div>
                <div className="v">{typeof v === "number" ? v.toFixed(2) : v}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="fcst-card">
          <div className="head">
            <div>
              <div className="ttl">FRED macro correlates</div>
              <div className="sub">{liveMode ? "Live FRED snapshot · Q1 2021 → Q1 2026" : "Pre-computed correlation against quarterly revenue"}</div>
            </div>
          </div>
          {liveMode && fredSeries ? (
            <LiveFREDList series={fredSeries}/>
          ) : (
            <div>
              {data.fred.map(s => (
                <div className="fred-row" key={s.id}>
                  <span className="fred-id">{s.id}</span>
                  <span className="fred-name">{s.name}</span>
                  <span className="fred-r" style={{color: Math.abs(s.r) >= 0.75 ? "var(--ink)" : "var(--ink-3)"}}>
                    r={s.r >= 0 ? "+" : ""}{s.r.toFixed(2)}
                  </span>
                  <span className={`fred-dir ${s.dir}`}>{s.dir.slice(0,5)}</span>
                </div>
              ))}
            </div>
          )}
          <div className="mt-12" style={{fontSize: 11, color: "var(--ink-3)", lineHeight: 1.5}}>
            Strongest leading indicators: Philadelphia Fed Semi Index (lead 2Q, r=0.82); Mfg Capacity Util. (lead 1Q, r=0.78). Macro signal currently <b style={{color: "var(--red-ink)"}}>CONTRACTIONARY</b>.
          </div>
        </div>
      </div>

      <div className="fcst-card">
        {(() => {
          const sq = data.sentiment.quarterly || [];
          const first = sq[0], last = sq[sq.length - 1];
          const peak  = sq.reduce((a, b) => b.score > a.score ? b : a, sq[0] || {score:0,q:''});
          const trough= sq.reduce((a, b) => b.score < a.score ? b : a, sq[0] || {score:0,q:''});
          const swing = last && first ? last.score - first.score : 0;
          const swingAbs = Math.abs(swing);
          const hedgeDir = data.sentiment.hedge_ratio_trend?.startsWith('↓') ? 'declined' : 'increased';
          const hedgePct = data.sentiment.hedge_ratio_trend?.match(/(\d+)%/)?.[1] ?? '–';
          return (
            <>
              <div className="head">
                <div>
                  <div className="ttl">Earnings call sentiment trend</div>
                  <div className="sub">QoQ revenue momentum proxy · {sq.length} quarters · NLP hedge-word ratio</div>
                </div>
                <div style={{textAlign: "right"}}>
                  <div className="big-num">{data.sentiment.score > 0 ? "+" : ""}{data.sentiment.score}</div>
                  <div className={`delta ${data.sentiment.trend === "IMPROVING" ? "up" : "dn"}`}>{data.sentiment.trend} · hedge ratio {data.sentiment.hedge_ratio_trend}</div>
                </div>
              </div>
              {/* SVG sentiment bar chart — zero-baseline, positive up / negative down */}
              {sq.length > 0 ? (() => {
                const W = 480, H = 72, MID = 34, PAD = 6;
                const n = sq.length;
                const barW = Math.max(4, (W - PAD * 2 - (n - 1) * 3) / n);
                const maxAbs = Math.max(1, ...sq.map(d => Math.abs(d.score)));
                const scaleH = (MID - 8) / maxAbs;
                return (
                  <svg viewBox={`0 0 ${W} ${H}`} style={{width:"100%", display:"block", marginTop:8}} xmlns="http://www.w3.org/2000/svg">
                    {/* zero line */}
                    <line x1={PAD} y1={MID} x2={W - PAD} y2={MID} stroke="var(--line-2)" strokeWidth="1"/>
                    {sq.map((d, i) => {
                      const x = PAD + i * (barW + 3);
                      const barH = Math.max(3, Math.abs(d.score) * scaleH);
                      const neg = d.score < 0;
                      const barY = neg ? MID : MID - barH;
                      const fill = neg ? "var(--red)" : "var(--green)";
                      return (
                        <g key={i}>
                          <rect x={x} y={barY} width={barW} height={barH} fill={fill} opacity="0.82" rx="2"/>
                          <text x={x + barW / 2} y={H - 2} textAnchor="middle" fontSize="8" fontFamily="Geist Mono,monospace" fill="var(--ink-3)">{d.q}</text>
                          {Math.abs(d.score) >= 5 && (
                            <text x={x + barW / 2} y={neg ? barY + barH - 3 : barY - 2} textAnchor="middle" fontSize="7.5" fontFamily="Geist Mono,monospace" fill={neg ? "var(--red-ink)" : "var(--green-ink)"}>{d.score > 0 ? '+' : ''}{d.score}</text>
                          )}
                        </g>
                      );
                    })}
                  </svg>
                );
              })() : (
                <div style={{height:72, display:"flex", alignItems:"center", justifyContent:"center", color:"var(--ink-4)", fontSize:11}}>
                  Run loop to populate sentiment history
                </div>
              )}
              <div className="sent-commentary">
                <div className="sent-comm-row">
                  <div className="sent-comm-cell">
                    <div className="sent-comm-lbl">What changed</div>
                    <div className="sent-comm-v">
                      {swing < -3 ? (
                        <>Net sentiment <b style={{fontWeight:500,color:"var(--red-ink)"}}>fell {swingAbs} pts</b> over {sq.length} quarters ({first?.q}: {first?.score > 0 ? '+' : ''}{first?.score} → {last?.q}: {last?.score}). Trough at {trough.q} ({trough.score}). Weak momentum is a leading indicator for revenue risk and heightened hedge-word frequency.</>
                      ) : swing > 3 ? (
                        <>Net sentiment <b style={{fontWeight:500,color:"var(--green)"}}>improved {swingAbs} pts</b> over {sq.length} quarters ({first?.q}: {first?.score > 0 ? '+' : ''}{first?.score} → {last?.q}: {last?.score}). Peak at {peak.q} (+{peak.score}). Positive momentum correlates with revenue beat probability.</>
                      ) : (
                        <>Sentiment broadly stable over {sq.length} quarters ({first?.q} to {last?.q}), range {trough.score} to +{peak.score}. No sustained directional signal — monitor for breakout.</>
                      )}
                    </div>
                  </div>
                  <div className="sent-comm-cell">
                    <div className="sent-comm-lbl">Hedge ratio signal</div>
                    <div className="sent-comm-v">Hedge-word ratio has <b style={{fontWeight:500}}>{hedgeDir}</b> to {hedgePct}% ({data.sentiment.hedge_ratio_trend}). {hedgeDir === 'declined' ? <>Language is shifting toward more definitive forward guidance — a 1–2Q leading indicator of revenue beats.</> : <>Rising hedge language signals management caution; monitor for guidance cuts.</>}</div>
                  </div>
                </div>
                <div className="sent-comm-row">
                  <div className="sent-comm-cell">
                    <div className="sent-comm-lbl">Cross-correlation</div>
                    <div className="sent-comm-v">Sentiment trend is tracking M-Score deterioration (corr = <span className="mono">+0.74</span>) and DSO drift (<span className="mono">+0.68</span>). Three independent signals pointing the same direction — not a single-driver story.</div>
                  </div>
                  <div className="sent-comm-cell">
                    <div className="sent-comm-lbl">Audit implication</div>
                    <div className="sent-comm-v">
                      {data.sentiment.trend === 'DETERIORATING'
                        ? <>Pull forward revenue recognition and accruals audit work. Add forensic walkthrough on most recent quarter cut-off entries. Pre-align with external auditor on management-letter language.</>
                        : <>Monitor for reversal signals. Maintain standard revenue recognition procedures and confirm hedge-word ratio stays below 20%.</>}
                    </div>
                  </div>
                </div>
              </div>
            </>
          );
        })()}
      </div>
      {rssSignals?.length > 0 && (
        <RssSentimentCard signals={rssSignals} />
      )}
      </div>
    </div>
  );
}

function RssSentimentCard({ signals }) {
  // Group by domain, compute average velocity as proxy for sentiment
  const byDomain = {};
  signals.forEach(s => {
    (s.domains || []).forEach(d => {
      if (!byDomain[d]) byDomain[d] = { total: 0, count: 0, high: 0 };
      byDomain[d].total += s.velocity || 0;
      byDomain[d].count += 1;
      if ((s.velocity || 0) >= 3) byDomain[d].high += 1;
    });
  });

  const entries = Object.entries(byDomain).sort((a, b) => b[1].total - a[1].total);
  const overallVel = signals.reduce((s, a) => s + (a.velocity || 0), 0) / (signals.length || 1);

  return (
    <div className="fcst-card" style={{marginTop:14}}>
      <div className="head">
        <div>
          <div className="ttl">RSS signal sentiment</div>
          <div className="sub">Aggregate velocity across {signals.length} graded articles · by risk domain</div>
        </div>
        <div style={{textAlign:"right"}}>
          <div className="big-num" style={{color: overallVel >= 3 ? "var(--red-ink)" : overallVel >= 2 ? "var(--amber-ink)" : "var(--green-ink)"}}>
            +{overallVel.toFixed(1)}
          </div>
          <div className="delta dn">AVG VELOCITY</div>
        </div>
      </div>
      <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(180px, 1fr))", gap:8, marginTop:12}}>
        {entries.map(([domain, data]) => {
          const avg = data.total / data.count;
          const color = avg >= 3 ? "var(--red-ink)" : avg >= 2 ? "var(--amber-ink)" : "var(--green-ink)";
          const barW = Math.min(100, (avg / 5) * 100);
          return (
            <div key={domain} style={{border:"1px solid var(--line)", borderRadius:8, padding:"9px 11px"}}>
              <div style={{fontWeight:500, fontSize:11.5, marginBottom:5}}>{domain}</div>
              <div style={{height:4, background:"var(--surface-2)", borderRadius:2, marginBottom:5}}>
                <div style={{height:"100%", width:`${barW}%`, background:color, borderRadius:2}}/>
              </div>
              <div className="mono" style={{fontSize:10.5, color:"var(--ink-3)"}}>
                avg v=+{avg.toFixed(1)} · {data.count} articles · {data.high} high-vel
              </div>
            </div>
          );
        })}
      </div>
      <div style={{marginTop:10, fontSize:11, color:"var(--ink-3)", lineHeight:1.5}}>
        RSS velocity feeds into residual risk scoring. High-velocity domains elevate projected end-of-period scores. Run ingestion in the RSS Signals tab to refresh.
      </div>
    </div>
  );
}

// ---- Per-model forecast numbers table ----
function ComponentForecastTable({ fcAll, labels, unit }) {
  const models = ["arima","prophet","rf","ensemble"];
  const fmt = (v, u) => v == null ? "—" : u === "$M" ? `$${v.toFixed(0)}M` : `${v.toFixed(1)}%`;
  return (
    <div style={{marginTop: 10, overflowX: "auto"}}>
      <table style={{width:"100%", borderCollapse:"collapse", fontSize:10.5}}>
        <thead>
          <tr style={{borderBottom:"1px solid var(--line)"}}>
            <th style={{textAlign:"left", padding:"3px 8px 3px 0", color:"var(--ink-3)", fontWeight:400, fontFamily:"Geist Mono, monospace"}}>Model</th>
            {labels.map(q => <th key={q} style={{textAlign:"right", padding:"3px 6px", color:"var(--ink-3)", fontWeight:400, fontFamily:"Geist Mono, monospace"}}>{q}</th>)}
          </tr>
        </thead>
        <tbody>
          {models.map(key => {
            const fc = fcAll[key];
            if (!fc) return null;
            const isEns = key === "ensemble";
            return (
              <tr key={key} style={{borderBottom: isEns ? "none" : "1px solid var(--line)", fontWeight: isEns ? 500 : 400}}>
                <td style={{padding:"4px 8px 4px 0", display:"flex", alignItems:"center", gap:5}}>
                  <span style={{width:7,height:7,borderRadius:"50%",background:MODEL_COLORS[key],flexShrink:0,display:"inline-block"}}/>
                  <span style={{color:"var(--ink-2)", fontFamily:"Geist Mono, monospace"}}>{MODEL_NAMES[key]}</span>
                </td>
                {fc.base.map((v, i) => (
                  <td key={i} style={{textAlign:"right", padding:"4px 6px", fontFamily:"Geist Mono, monospace", color: isEns ? MODEL_COLORS[key] : "var(--ink-2)"}}>
                    {fmt(v, unit)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---- Model diagnostics card ----
function ModelDiagnosticsCard({ revenue, margin }) {
  const [tab, setTab] = useState("revenue");
  const data = tab === "revenue" ? revenue : margin;
  const bt = data.backtest?.results;
  const weights = revenue.backtest?.ensembleWeights;

  const fmtMape = v => v == null ? "—" : v.toFixed(2) + "%";
  const fmtRmse = v => v == null ? "—" : v.toFixed(2);
  const fmtR2   = v => v == null ? "—" : v.toFixed(3);
  const mapeColor = v => v == null ? "var(--ink-3)" : v < 5 ? "var(--green-ink)" : v < 15 ? "var(--amber-ink)" : "var(--red-ink)";

  return (
    <div className="fcst-card" style={{marginTop:14}}>
      <div className="head">
        <div>
          <div className="ttl">Model diagnostics · backtesting</div>
          <div className="sub">Walk-forward validation · leave-last-4 hold-out · MAPE / RMSE / R²</div>
        </div>
        <div style={{display:"flex", gap:5}}>
          <button className={`btn btn-sm${tab === "revenue" ? " btn-primary" : ""}`} onClick={() => setTab("revenue")}>Revenue</button>
          <button className={`btn btn-sm${tab === "margin"  ? " btn-primary" : ""}`} onClick={() => setTab("margin")}>Margin</button>
        </div>
      </div>

      <div style={{display:"grid", gridTemplateColumns:"repeat(4, 1fr)", gap:8, marginTop:12}}>
        {["arima","prophet","rf","ensemble"].map(key => {
          const r = bt?.[key];
          return (
            <div key={key} style={{border:"1px solid var(--line)", borderRadius:8, padding:"10px 12px", background: key === "ensemble" ? "var(--surface-2)" : undefined}}>
              <div style={{display:"flex", alignItems:"center", gap:5, marginBottom:8}}>
                <span style={{width:8,height:8,borderRadius:"50%",background:MODEL_COLORS[key],flexShrink:0}}/>
                <span style={{fontWeight:500, fontSize:11.5, color:"var(--ink)"}}>{MODEL_NAMES[key]}</span>
              </div>
              <div style={{display:"grid", gridTemplateColumns:"auto 1fr", gap:"3px 10px", fontSize:11}}>
                <span className="mono" style={{color:"var(--ink-3)"}}>MAPE</span>
                <span className="mono" style={{color: mapeColor(r?.mape)}}>{fmtMape(r?.mape)}</span>
                <span className="mono" style={{color:"var(--ink-3)"}}>RMSE</span>
                <span className="mono" style={{color:"var(--ink-2)"}}>{fmtRmse(r?.rmse)}</span>
                <span className="mono" style={{color:"var(--ink-3)"}}>R²</span>
                <span className="mono" style={{color:"var(--ink-2)"}}>{fmtR2(r?.r2)}</span>
              </div>
            </div>
          );
        })}
      </div>

      {weights && (
        <div style={{marginTop:10, fontSize:11, color:"var(--ink-3)", lineHeight:1.5}}>
          Ensemble weights (calibrated by MAPE):
          {" "}<span className="mono" style={{color:"var(--acc)"}}>ARIMA {(weights[0]*100).toFixed(0)}%</span>
          {" · "}<span className="mono" style={{color:"var(--violet)"}}>Prophet {(weights[1]*100).toFixed(0)}%</span>
          {" · "}<span className="mono" style={{color:"var(--amber)"}}>RF {(weights[2]*100).toFixed(0)}%</span>
          {". "}Revenue source: <span className="mono">{revenue.source.toUpperCase()}</span>.
          Lower MAPE = higher weight. Green &lt; 5% · Amber 5–15% · Red &gt; 15%.
        </div>
      )}
    </div>
  );
}

function LiveFREDList({ series }) {
  return (
    <div>
      {Object.entries(series).map(([id, s]) => {
        const obs = s.observations || [];
        const latest = obs[obs.length - 1];
        const prev = obs[obs.length - 2];
        const delta = latest && prev ? ((latest.value - prev.value) / prev.value) * 100 : null;
        const dir = delta == null ? "NEUTRAL" : delta > 0.5 ? "EXPANSIONARY" : delta < -0.5 ? "CONTRACTIONARY" : "NEUTRAL";
        return (
          <div className="fred-row" key={id}>
            <span className="fred-id">{id}</span>
            <span className="fred-name" style={{fontSize:11}}>{s.description.split(":")[0]}</span>
            <span className="fred-r">{latest?.value?.toFixed?.(2) ?? "—"}</span>
            <span className={`fred-dir ${dir}`}>{delta == null ? "—" : `${delta > 0 ? "+" : ""}${delta.toFixed(2)}%`}</span>
          </div>
        );
      })}
    </div>
  );
}

window.ForecastsPanel = ForecastsPanel;
