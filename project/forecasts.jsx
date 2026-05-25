/* ============================================================
   Forecasts panel — revenue / margin + M-Score + FRED correlates
   ============================================================ */

function ForecastsPanel({ data, liveMode, fredSeries }) {
  if (!data) return <Empty>Run the loop to populate forecasts, or click Run Loop in the sidebar.</Empty>;

  const lastHistRev = data.revenue.history[data.revenue.history.length - 1].v;
  const lastFcRev = data.revenue.forecast[data.revenue.forecast.length - 1].base;
  const revDeltaPct = ((lastFcRev - lastHistRev) / lastHistRev) * 100;

  const lastHistMg = data.margin.history[data.margin.history.length - 1].v;
  const lastFcMg = data.margin.forecast[data.margin.forecast.length - 1].base;
  const mgDelta = (lastFcMg - lastHistMg) * 100; // bps

  return (
    <div data-screen-label="Forecasts">
      <div className="panel-head">
        <div>
          <div className="kicker">Financial intelligence + forecasting</div>
          <div className="panel-title mt-8">EDGAR XBRL + FRED macro · ARIMA ensemble</div>
          <div className="panel-sub">Auto-runs after the loop completes. Confidence band is the ensemble's 80% interval. {liveMode ? "Live FRED snapshot bundled with the prototype." : "Mock data — switch to Live in the sidebar to pull EDGAR XBRL for this ticker."}</div>
        </div>
      </div>

      <div className="fcst-row">
        <div className="fcst-card">
          <div className="head">
            <div>
              <div className="ttl">Revenue · TTM</div>
              <div className="sub">Quarterly $M · 8 history + 4 forecast</div>
            </div>
            <div style={{textAlign: "right"}}>
              <div className="big-num">${lastFcRev.toFixed(0)}M</div>
              <div className={`delta ${revDeltaPct >= 0 ? "up" : "dn"}`}>
                {revDeltaPct >= 0 ? "▲" : "▼"} {Math.abs(revDeltaPct).toFixed(1)}% vs latest
              </div>
            </div>
          </div>
          <ForecastChart history={data.revenue.history.slice(-8)} forecast={data.revenue.forecast} unit="$M" color="var(--acc)"/>
        </div>

        <div className="fcst-card">
          <div className="head">
            <div>
              <div className="ttl">Gross margin</div>
              <div className="sub">Quarterly % · 8 history + 4 forecast</div>
            </div>
            <div style={{textAlign: "right"}}>
              <div className="big-num">{lastFcMg.toFixed(1)}%</div>
              <div className={`delta ${mgDelta >= 0 ? "up" : "dn"}`}>
                {mgDelta >= 0 ? "▲" : "▼"} {Math.abs(mgDelta).toFixed(0)} bps vs latest
              </div>
            </div>
          </div>
          <ForecastChart history={data.margin.history.slice(-8)} forecast={data.margin.forecast} unit="%" color="var(--violet)"/>
        </div>
      </div>

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
              <div className="sub">{liveMode ? "Live FRED snapshot · bundled JSON · Q1 2021 → Q1 2026" : "Pre-computed correlation against quarterly revenue"}</div>
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
        <div className="head">
          <div>
            <div className="ttl">Earnings call sentiment trend</div>
            <div className="sub">NLP sentiment + hedge ratio over last 6 quarters</div>
          </div>
          <div style={{textAlign: "right"}}>
            <div className="big-num">{data.sentiment.score}</div>
            <div className="delta dn">DETERIORATING · hedge ratio {data.sentiment.hedge_ratio_trend}</div>
          </div>
        </div>
        <div style={{display:"flex", alignItems:"flex-end", gap: 4, height: 60, padding: "8px 0", marginTop: 6}}>
          {[12, 6, -2, -8, -14, -18].map((v, i) => {
            const h = Math.abs(v) / 20 * 50 + 4;
            const negative = v < 0;
            return (
              <div key={i} style={{flex: 1, display:"flex", flexDirection:"column", alignItems:"center", gap: 4}}>
                <div style={{width: "70%", height: h, background: negative ? "var(--red)" : "var(--green)", opacity: 0.85, borderRadius: 3}}/>
                <div className="mono" style={{fontSize: 9, color: "var(--ink-3)"}}>Q{i+1}-24</div>
              </div>
            );
          })}
        </div>
        <div className="sent-commentary">
          <div className="sent-comm-row">
            <div className="sent-comm-cell">
              <div className="sent-comm-lbl">What changed</div>
              <div className="sent-comm-v">Net sentiment dropped <b style={{fontWeight: 500, color: "var(--red-ink)"}}>30 points</b> over 6 quarters (Q1: +12 → Q6: −18). Inflection at Q3 coincides with the BIS October rule extension and first signs of channel destock.</div>
            </div>
            <div className="sent-comm-cell">
              <div className="sent-comm-lbl">Hedge ratio signal</div>
              <div className="sent-comm-v">Hedge-word ratio is up <b style={{fontWeight: 500}}>{data.sentiment.hedge_ratio_trend}</b> over 4Q. Management is leaning on <span className="mono" style={{fontSize: 11, color: "var(--ink-2)"}}>"visibility limited"</span>, <span className="mono" style={{fontSize: 11, color: "var(--ink-2)"}}>"subject to macro"</span>, <span className="mono" style={{fontSize: 11, color: "var(--ink-2)"}}>"timing uncertainty"</span> — historically a 2-quarter leading indicator of guide-down.</div>
            </div>
          </div>
          <div className="sent-comm-row">
            <div className="sent-comm-cell">
              <div className="sent-comm-lbl">Cross-correlation</div>
              <div className="sent-comm-v">Sentiment trend is tracking M-Score deterioration (corr = <span className="mono">+0.74</span>) and DSO drift (<span className="mono">+0.68</span>). Three independent signals pointing the same direction — not a single-driver story.</div>
            </div>
            <div className="sent-comm-cell">
              <div className="sent-comm-lbl">Audit implication</div>
              <div className="sent-comm-v">Pull <b style={{fontWeight: 500}}>R-01 Revenue Recognition</b> and <b style={{fontWeight: 500}}>R-02 Export Controls</b> forward in Q1 sample plan. Add forensic walk-through on Q4 cut-off entries. Pre-align with external audit on management-letter language.</div>
            </div>
          </div>
        </div>
      </div>
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
            <span className="fred-name" style={{fontSize: 11}}>{s.description.split(":")[0]}</span>
            <span className="fred-r">{latest?.value?.toFixed?.(2) ?? "—"}</span>
            <span className={`fred-dir ${dir}`}>{delta == null ? "—" : `${delta > 0 ? "+" : ""}${delta.toFixed(2)}%`}</span>
          </div>
        );
      })}
    </div>
  );
}

window.ForecastsPanel = ForecastsPanel;
