/* ============================================================
   Forecasts panel — live multi-model forecasting + backtesting
   ARIMA · Prophet · Random Forest · Ensemble + FRED leading indicators
   ============================================================ */

// ── Model forecast multi-line chart ──────────────────────────

function ModelForecastChart({ kpiSeries, forecasts, histOnly }) {
  if (!kpiSeries?.length) return null;

  const W = 600, H = 200;
  const pad = { l: 52, r: 12, t: 14, b: 28 };
  const iW = W - pad.l - pad.r;
  const iH = H - pad.t - pad.b;

  const histVals = kpiSeries.map(k => k.val);
  const histLen  = histVals.length;

  const fcSteps = histOnly ? 0 : (forecasts?.ensemble?.base?.length || 4);

  // Future quarter dates from last historical date
  const lastDate = kpiSeries[histLen - 1].date;
  const forecastDates = Array.from({ length: fcSteps }, (_, i) => {
    const year = parseInt(lastDate.slice(0, 4));
    const month = parseInt(lastDate.slice(5, 7));
    const fm = month + (i + 1) * 3;
    const fy = year + Math.floor((fm - 1) / 12);
    const am = ((fm - 1) % 12) + 1;
    return `${fy}-${String(am).padStart(2, '0')}-${lastDate.slice(8)}`;
  });

  const allDates = [...kpiSeries.map(k => k.date), ...forecastDates];
  const N = allDates.length;

  // Collect all values for Y range
  const fcVals = histOnly ? [] : Object.values(forecasts || {}).flatMap(f => f?.base || []);
  const hiVals = !histOnly && forecasts?.ensemble?.hi ? forecasts.ensemble.hi : [];
  const loVals = !histOnly && forecasts?.ensemble?.lo ? forecasts.ensemble.lo : [];
  const allVals = [...histVals, ...fcVals, ...hiVals, ...loVals].filter(v => v != null && isFinite(v));
  if (!allVals.length) return null;

  const minY = Math.min(...allVals) * 0.94;
  const maxY = Math.max(...allVals) * 1.06;
  const rangeY = maxY - minY || 1;

  const xS = i  => pad.l + (N > 1 ? (i / (N - 1)) * iW : iW / 2);
  const yS = v  => pad.t + (1 - (v - minY) / rangeY) * iH;

  function pts(vals, startIdx) {
    return vals.map((v, i) => {
      if (v == null || !isFinite(v)) return null;
      return `${xS(startIdx + i).toFixed(1)},${yS(v).toFixed(1)}`;
    }).filter(Boolean).join(' ');
  }

  function polyline(vals, startIdx, color, sw, dash) {
    const d = vals.map((v, i) => {
      if (v == null || !isFinite(v)) return null;
      return `${i === 0 ? 'M' : 'L'}${xS(startIdx + i).toFixed(1)} ${yS(v).toFixed(1)}`;
    }).filter(Boolean).join(' ');
    return <path key={color + startIdx} d={d} fill="none" stroke={color} strokeWidth={sw} strokeDasharray={dash || undefined} />;
  }

  // CI band for ensemble
  const ciPoints = (!histOnly && forecasts?.ensemble?.hi && forecasts?.ensemble?.lo)
    ? [
        ...forecasts.ensemble.hi.map((v, i) => `${xS(histLen + i).toFixed(1)},${yS(v).toFixed(1)}`),
        ...[...forecasts.ensemble.lo].reverse().map((v, i) => {
          const ri = forecasts.ensemble.lo.length - 1 - i;
          return `${xS(histLen + ri).toFixed(1)},${yS(forecasts.ensemble.lo[ri]).toFixed(1)}`;
        }),
      ].join(' ')
    : null;

  // Y axis ticks
  const nTicks = 4;
  const yTicks = Array.from({ length: nTicks + 1 }, (_, i) => minY + (rangeY * i) / nTicks);

  // X axis labels: show every other, prefer quarterly
  const xLabels = allDates.filter((_, i) => i % Math.max(1, Math.floor(N / 6)) === 0);

  const COLORS = { arima: 'var(--acc)', prophet: 'var(--violet)', rf: 'var(--amber)', ensemble: 'var(--ink)' };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H }} preserveAspectRatio="none">
      {/* Grid */}
      {yTicks.map((v, i) => (
        <g key={i}>
          <line x1={pad.l} x2={W - pad.r} y1={yS(v).toFixed(1)} y2={yS(v).toFixed(1)}
            stroke="var(--line)" strokeWidth={0.5} />
          <text x={pad.l - 4} y={yS(v) + 3} textAnchor="end" fontSize={8} fill="var(--ink-3)">
            {v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(1)}
          </text>
        </g>
      ))}

      {/* CI band */}
      {ciPoints && (
        <polygon points={ciPoints} fill="var(--ink)" fillOpacity={0.07} />
      )}

      {/* Forecast/history separator */}
      {!histOnly && fcSteps > 0 && (
        <line x1={xS(histLen - 1).toFixed(1)} x2={xS(histLen - 1).toFixed(1)}
          y1={pad.t} y2={H - pad.b}
          stroke="var(--line-strong)" strokeDasharray="4,3" strokeWidth={1} />
      )}

      {/* Historical line */}
      {polyline(histVals, 0, 'var(--ink-3)', 1.5, null)}

      {/* Historical dots */}
      {histVals.map((v, i) => (
        <circle key={i} cx={xS(i)} cy={yS(v)} r={2} fill="var(--ink-3)" />
      ))}

      {/* Forecast lines — connect from last hist point */}
      {!histOnly && BACKTESTING.MODEL_DEFS.map(def => {
        const fc = forecasts?.[def.key];
        if (!fc?.base?.length) return null;
        const connect = [histVals[histLen - 1], ...fc.base];
        return polyline(
          connect, histLen - 1,
          COLORS[def.key] || def.color,
          def.key === 'ensemble' ? 2.5 : 1.5,
          def.key === 'ensemble' ? null : '5,3'
        );
      })}

      {/* X axis labels */}
      {allDates.map((d, i) => {
        if (i % Math.max(1, Math.floor(N / 6)) !== 0) return null;
        return (
          <text key={i} x={xS(i)} y={H - 8} textAnchor="middle" fontSize={8} fill="var(--ink-3)">
            {d.slice(0, 7)}
          </text>
        );
      })}
    </svg>
  );
}

// ── Backtest metrics table ────────────────────────────────────

function BacktestTable({ btResults }) {
  if (!btResults) return null;
  const modelKeys = ['arima', 'prophet', 'rf', 'ensemble'];
  const metricKeys = ['mape', 'rmse', 'r2', 'precision', 'recall', 'f1'];
  const higherBetter = new Set(['r2', 'precision', 'recall', 'f1']);

  // Find winner per metric
  const winners = {};
  for (const m of metricKeys) {
    let best = null, bestVal = null;
    for (const k of modelKeys) {
      const v = btResults[k]?.[m];
      if (v == null) continue;
      const better = bestVal === null
        || (higherBetter.has(m) ? v > bestVal : v < bestVal);
      if (better) { best = k; bestVal = v; }
    }
    winners[m] = best;
  }

  const COLORS = { arima: 'var(--acc)', prophet: 'var(--violet)', rf: 'var(--amber)', ensemble: 'var(--ink)' };

  return (
    <div className="bt-scroll">
      <table className="bt-table">
        <thead>
          <tr>
            <th>Model</th>
            <th title="Mean Absolute Percentage Error — lower is better">MAPE</th>
            <th title="Root Mean Squared Error — lower is better">RMSE</th>
            <th title="R-squared — higher is better">R²</th>
            <th title="Directional precision">Precision</th>
            <th title="Directional recall">Recall</th>
            <th title="F1 score — directional accuracy">F1</th>
          </tr>
        </thead>
        <tbody>
          {BACKTESTING.MODEL_DEFS.map(def => {
            const bt = btResults[def.key];
            const fmt = BACKTESTING.formatMetrics(bt);
            return (
              <tr key={def.key}>
                <td>
                  <span className="bt-dot" style={{ background: COLORS[def.key] || def.color }} />
                  {def.name}
                </td>
                {metricKeys.map(m => (
                  <td key={m} className={winners[m] === def.key ? 'bt-winner' : ''}>
                    {fmt[m]}
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

// ── Leading-indicator heatmap ─────────────────────────────────

function LeadingHeatmap({ kpiValues, fredArrays }) {
  if (!fredArrays || !kpiValues || kpiValues.length < 8) return null;

  const matrix = FORECASTING.leadingIndicatorMatrix(kpiValues, fredArrays, 4);
  if (!matrix.ids.length) return (
    <div className="dc-hint" style={{ marginTop: 8 }}>No FRED series with sufficient correlation found.</div>
  );

  function heatBg(r) {
    if (r == null) return 'var(--surface-2)';
    const abs = Math.min(Math.abs(r), 1);
    if (abs < 0.15) return 'var(--surface-2)';
    const pct = Math.round(abs * 65);
    return r > 0
      ? `color-mix(in oklch, var(--green) ${pct}%, var(--surface-2))`
      : `color-mix(in oklch, var(--red) ${pct}%, var(--surface-2))`;
  }
  function heatInk(r) {
    if (r == null || Math.abs(r) < 0.15) return 'var(--ink-3)';
    return Math.abs(r) > 0.5 ? 'var(--ink)' : 'var(--ink-2)';
  }

  const seriesMap = {};
  for (const s of LIVE.FRED_SERIES_OPTIONS) seriesMap[s.id] = s.name;

  return (
    <div className="li-wrap">
      <div className="li-grid" style={{ gridTemplateColumns: `1fr repeat(${matrix.lags.length}, 48px)` }}>
        <div className="li-hdr-cell" />
        {matrix.lags.map(l => (
          <div key={l} className="li-hdr-cell" style={{ textAlign: 'center' }}>+{l}Q</div>
        ))}
        {matrix.ids.map(id => (
          <React.Fragment key={id}>
            <div className="li-label" title={id}>{seriesMap[id] || id}</div>
            {matrix.lags.map(l => {
              const r = matrix.matrix[id]?.[l];
              return (
                <div key={l} className="li-cell" style={{ background: heatBg(r), color: heatInk(r) }}>
                  {r != null ? r.toFixed(2) : '—'}
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
      <div className="li-legend">
        <span style={{ color: 'var(--red-ink)' }}>■</span> Negative lead &nbsp;
        <span style={{ color: 'var(--green-ink)' }}>■</span> Positive lead &nbsp;·&nbsp; Blank = |r| &lt; 0.15
      </div>
    </div>
  );
}

// ── Live forecasting section ──────────────────────────────────

const KPI_KEYS   = ['revenue', 'grossMargin', 'netIncome', 'inventory', 'ar'];
const KPI_LABELS = LIVE.KPI_LABELS;

function LiveForecastSection({ rawEdgarFacts, fredApiResults, cfg }) {
  const [selectedKPI, setKPI]   = useState('revenue');
  const [running, setRunning]   = useState(false);
  const [results, setResults]   = useState(null);   // {kpi, kpis, bt, forecasts, fredArrays}
  const [error, setError]       = useState(null);

  const targetFacts = rawEdgarFacts?.[cfg?.ticker];
  const edgarError = targetFacts?.error || null;

  async function runModels() {
    setRunning(true);
    setError(null);
    try {
      if (edgarError) throw new Error(`EDGAR fetch failed for ${cfg?.ticker}: ${edgarError}`);
      const kpis = LIVE.extractQuarterlyKPIs(targetFacts);
      if (!kpis) throw new Error(`No quarterly XBRL data found for ${cfg?.ticker}. Try a different ticker or check EDGAR availability.`);

      const kpiSeries = kpis[selectedKPI];
      if (!kpiSeries || kpiSeries.length < 10)
        throw new Error(`Insufficient data for ${selectedKPI} (need ≥ 10 quarters, got ${kpiSeries?.length || 0}).`);

      const values = kpiSeries.map(k => k.val);
      const dates  = kpiSeries.map(k => k.date);

      // Align FRED to EDGAR dates if we have FRED data
      const fredArrays = (fredApiResults && Object.keys(fredApiResults).length)
        ? LIVE.alignFredToEdgar(fredApiResults, dates)
        : null;

      // Run backtest (this is the slow part)
      const { results: btResults, mapes } = BACKTESTING.backtestAll(values, fredArrays, 4);

      // Forecast forward 4 quarters
      const forecasts = BACKTESTING.forecastAll(values, fredArrays, 4, mapes);

      setResults({ kpi: selectedKPI, kpis, kpiSeries, values, btResults, forecasts, fredArrays });
    } catch (e) {
      setError(e.message);
    }
    setRunning(false);
  }

  // Re-run when KPI tab changes if we already have results
  useEffect(() => {
    if (results && results.kpi !== selectedKPI) {
      runModels();
    }
  }, [selectedKPI]);

  if (!targetFacts || edgarError) {
    return (
      <div className="live-fc-empty">
        <div className="empty">
          <div className="icon">⟳</div>
          {edgarError
            ? <>EDGAR fetch failed for <b>{cfg?.ticker}</b>: {edgarError}. Check your connection or try a different ticker.</>
            : <>No EDGAR data loaded. Run the loop in Live mode — or click <b>Configure</b> → <b>Save &amp; Fetch Now</b>.</>
          }
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* KPI tabs */}
      <div className="kpi-tabs">
        {KPI_KEYS.map(k => (
          <button key={k} className={'kpi-tab' + (selectedKPI === k ? ' active' : '')} onClick={() => setKPI(k)}>
            {KPI_LABELS[k] || k}
          </button>
        ))}
        <button
          className={'btn btn-sm btn-primary' + (running ? ' disabled' : '')}
          style={{ marginLeft: 'auto' }}
          onClick={runModels}
          disabled={running}
        >
          {running ? <><span className="spin" />Running…</> : '▶ Run Models'}
        </button>
      </div>

      {error && (
        <div style={{ padding: '10px 14px', background: 'var(--red-soft)', color: 'var(--red-ink)',
          borderRadius: 8, fontSize: 12, marginBottom: 12 }}>
          {error}
        </div>
      )}

      {!results && !running && (
        <div className="live-fc-hint">
          Click <b>Run Models</b> to fit ARIMA, Prophet, Random Forest, and Ensemble to live EDGAR data.
          Includes walk-forward backtesting and FRED leading-indicator analysis.
        </div>
      )}

      {results && (
        <>
          {/* Model comparison chart */}
          <div className="fcst-card" style={{ marginBottom: 12 }}>
            <div className="head">
              <div>
                <div className="ttl">{KPI_LABELS[results.kpi]} · model comparison</div>
                <div className="sub">{results.kpiSeries.length} quarters history · 4Q forecast · dashed = individual models</div>
              </div>
            </div>
            <div className="mc-legend">
              {BACKTESTING.MODEL_DEFS.map(def => {
                const COLORS = { arima:'var(--acc)', prophet:'var(--violet)', rf:'var(--amber)', ensemble:'var(--ink)' };
                return (
                  <span key={def.key} className="mc-legend-item">
                    <span className="mc-dot" style={{ background: COLORS[def.key] || def.color }} />
                    {def.name}
                  </span>
                );
              })}
              <span className="mc-legend-item">
                <span className="mc-dot" style={{ background: 'var(--ink)', opacity: 0.15, width: 18, borderRadius: 2 }} />
                Ensemble 80% CI
              </span>
            </div>
            <ModelForecastChart
              kpiSeries={results.kpiSeries}
              forecasts={results.forecasts}
              histOnly={false}
            />
          </div>

          {/* Backtest metrics */}
          <div className="fcst-card" style={{ marginBottom: 12 }}>
            <div className="head">
              <div>
                <div className="ttl">Backtest metrics · walk-forward validation</div>
                <div className="sub">Expanding window · 1-step ahead · 4 hold-out quarters · winner highlighted</div>
              </div>
            </div>
            <BacktestTable btResults={results.btResults} />
          </div>

          {/* Leading indicators */}
          {results.fredArrays && Object.keys(results.fredArrays).length > 0 && (
            <div className="fcst-card" style={{ marginBottom: 12 }}>
              <div className="head">
                <div>
                  <div className="ttl">FRED leading indicators · cross-correlation by lag</div>
                  <div className="sub">Pearson r of FRED series lagged 1–4 quarters against {KPI_LABELS[results.kpi]}</div>
                </div>
              </div>
              <LeadingHeatmap kpiValues={results.values} fredArrays={results.fredArrays} />
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Mock chart helpers (kept for mock mode) ──────────────────

function LiveFREDList({ series }) {
  if (!series) return null;
  return (
    <div>
      {Object.entries(series).map(([id, s]) => {
        const obs = s.observations || [];
        const latest = obs[obs.length - 1];
        const prev   = obs[obs.length - 2];
        const delta  = latest && prev ? ((latest.value - prev.value) / prev.value) * 100 : null;
        const dir    = delta == null ? 'neutral' : delta > 0.5 ? 'expand' : delta < -0.5 ? 'contract' : 'neutral';
        return (
          <div className="fred-row" key={id}>
            <span className="fred-id">{id}</span>
            <span className="fred-name" style={{ fontSize: 11 }}>
              {(s.description || '').split(':')[0] || id}
            </span>
            <span className="fred-r">{latest?.value?.toFixed?.(2) ?? '—'}</span>
            <span className={`fred-dir ${dir}`}>
              {delta == null ? '—' : `${delta > 0 ? '+' : ''}${delta.toFixed(2)}%`}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────

function ForecastsPanel({ data, liveMode, fredSeries, rawEdgarFacts, fredApiResults, cfg, onOpenDataConfig }) {

  // Live mode with real EDGAR data — show live forecasting UI
  const hasLiveData = liveMode && rawEdgarFacts && Object.keys(rawEdgarFacts).length > 0;

  return (
    <div data-screen-label="Forecasts">
      <div className="panel-head">
        <div>
          <div className="kicker">Financial intelligence + forecasting</div>
          <div className="panel-title mt-8">
            {hasLiveData
              ? `EDGAR live · ${cfg?.ticker} · ARIMA · Prophet · RF · Ensemble`
              : 'EDGAR XBRL + FRED macro · ARIMA ensemble'}
          </div>
          <div className="panel-sub">
            {hasLiveData
              ? 'Live EDGAR quarterly KPIs. Click Run Models to fit all four models, run walk-forward backtesting, and compute FRED leading indicators.'
              : liveMode
                ? 'Live mode active — run the loop or click Save & Fetch Now in Data Connection to load EDGAR data.'
                : 'Mock data — switch to Live in the sidebar to pull EDGAR XBRL for this ticker.'}
          </div>
        </div>
        {liveMode && (
          <button className="btn btn-sm" style={{ flexShrink: 0, alignSelf: 'flex-start' }}
            onClick={onOpenDataConfig}>
            <Icon name="wifi" size={11} /> Configure
          </button>
        )}
      </div>

      {/* ── Live forecasting section ── */}
      {liveMode && (
        <div style={{ marginBottom: 16 }}>
          <LiveForecastSection
            rawEdgarFacts={rawEdgarFacts}
            fredApiResults={fredApiResults}
            cfg={cfg}
          />
        </div>
      )}

      {/* ── Mock / static sections (always shown when data available) ── */}
      {data && (
        <>
          <div className="fcst-row">
            <div className="fcst-card">
              <div className="head">
                <div>
                  <div className="ttl">Revenue · TTM {liveMode ? '(mock)' : ''}</div>
                  <div className="sub">Quarterly $M · 8 history + 4 forecast</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div className="big-num">${data.revenue.forecast[data.revenue.forecast.length - 1].base.toFixed(0)}M</div>
                  <div className={`delta ${((data.revenue.forecast[data.revenue.forecast.length-1].base - data.revenue.history[data.revenue.history.length-1].v) / data.revenue.history[data.revenue.history.length-1].v * 100) >= 0 ? 'up' : 'dn'}`}>
                    {((data.revenue.forecast[data.revenue.forecast.length-1].base - data.revenue.history[data.revenue.history.length-1].v) / data.revenue.history[data.revenue.history.length-1].v * 100) >= 0 ? '▲' : '▼'}&nbsp;
                    {Math.abs((data.revenue.forecast[data.revenue.forecast.length-1].base - data.revenue.history[data.revenue.history.length-1].v) / data.revenue.history[data.revenue.history.length-1].v * 100).toFixed(1)}% vs latest
                  </div>
                </div>
              </div>
              <ForecastChart history={data.revenue.history.slice(-8)} forecast={data.revenue.forecast} unit="$M" color="var(--acc)" />
            </div>

            <div className="fcst-card">
              <div className="head">
                <div>
                  <div className="ttl">Gross margin {liveMode ? '(mock)' : ''}</div>
                  <div className="sub">Quarterly % · 8 history + 4 forecast</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div className="big-num">{data.margin.forecast[data.margin.forecast.length-1].base.toFixed(1)}%</div>
                  <div className={`delta ${(data.margin.forecast[data.margin.forecast.length-1].base - data.margin.history[data.margin.history.length-1].v) * 100 >= 0 ? 'up' : 'dn'}`}>
                    {(data.margin.forecast[data.margin.forecast.length-1].base - data.margin.history[data.margin.history.length-1].v) * 100 >= 0 ? '▲' : '▼'}&nbsp;
                    {Math.abs((data.margin.forecast[data.margin.forecast.length-1].base - data.margin.history[data.margin.history.length-1].v) * 100).toFixed(0)} bps
                  </div>
                </div>
              </div>
              <ForecastChart history={data.margin.history.slice(-8)} forecast={data.margin.forecast} unit="%" color="var(--violet)" />
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
              <MScoreGauge m={data.mscore.m} />
              <div className="mt-12" style={{ fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.55 }}>
                <b style={{ fontWeight: 500 }}>Key driver:</b> {data.mscore.key_driver}. Band breaches RED at M &gt; −1.78.
              </div>
              <div className="mt-12" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 6 }}>
                {Object.entries(data.mscore.vars).map(([k, v]) => (
                  <div key={k} className="scen-m">
                    <div className="l">{k}</div>
                    <div className="v">{typeof v === 'number' ? v.toFixed(2) : v}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="fcst-card">
              <div className="head">
                <div>
                  <div className="ttl">FRED macro correlates</div>
                  <div className="sub">
                    {liveMode && fredSeries
                      ? 'Live FRED · latest observations'
                      : 'Pre-computed correlation against quarterly revenue'}
                  </div>
                </div>
              </div>
              {liveMode && fredSeries
                ? <LiveFREDList series={fredSeries} />
                : (
                  <div>
                    {data.fred.map(s => (
                      <div className="fred-row" key={s.id}>
                        <span className="fred-id">{s.id}</span>
                        <span className="fred-name">{s.name}</span>
                        <span className="fred-r" style={{ color: Math.abs(s.r) >= 0.75 ? 'var(--ink)' : 'var(--ink-3)' }}>
                          r={s.r >= 0 ? '+' : ''}{s.r.toFixed(2)}
                        </span>
                        <span className={`fred-dir ${s.dir}`}>{s.dir.slice(0, 5)}</span>
                      </div>
                    ))}
                  </div>
                )}
              <div className="mt-12" style={{ fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.5 }}>
                Strongest leading indicators: Philadelphia Fed Semi Index (lead 2Q, r=0.82); Mfg Capacity Util. (lead 1Q, r=0.78).
                Macro signal currently <b style={{ color: 'var(--red-ink)' }}>CONTRACTIONARY</b>.
              </div>
            </div>
          </div>

          <div className="fcst-card">
            <div className="head">
              <div>
                <div className="ttl">Earnings call sentiment trend</div>
                <div className="sub">NLP sentiment + hedge ratio over last 6 quarters</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div className="big-num">{data.sentiment.score}</div>
                <div className="delta dn">DETERIORATING · hedge ratio {data.sentiment.hedge_ratio_trend}</div>
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 60, padding: '8px 0', marginTop: 6 }}>
              {[12, 6, -2, -8, -14, -18].map((v, i) => {
                const h = Math.abs(v) / 20 * 50 + 4;
                return (
                  <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                    <div style={{ width: '70%', height: h, background: v < 0 ? 'var(--red)' : 'var(--green)', opacity: 0.85, borderRadius: 3 }} />
                    <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>Q{i + 1}-24</div>
                  </div>
                );
              })}
            </div>
            <div className="sent-commentary">
              <div className="sent-comm-row">
                <div className="sent-comm-cell">
                  <div className="sent-comm-lbl">What changed</div>
                  <div className="sent-comm-v">Net sentiment dropped <b style={{ fontWeight: 500, color: 'var(--red-ink)' }}>30 points</b> over 6 quarters. Inflection at Q3 coincides with the BIS October rule extension and first signs of channel destock.</div>
                </div>
                <div className="sent-comm-cell">
                  <div className="sent-comm-lbl">Hedge ratio signal</div>
                  <div className="sent-comm-v">Hedge-word ratio up <b style={{ fontWeight: 500 }}>{data.sentiment.hedge_ratio_trend}</b> over 4Q — historically a 2-quarter leading indicator of guide-down.</div>
                </div>
              </div>
              <div className="sent-comm-row">
                <div className="sent-comm-cell">
                  <div className="sent-comm-lbl">Cross-correlation</div>
                  <div className="sent-comm-v">Sentiment tracking M-Score deterioration (corr = <span className="mono">+0.74</span>) and DSO drift (<span className="mono">+0.68</span>).</div>
                </div>
                <div className="sent-comm-cell">
                  <div className="sent-comm-lbl">Audit implication</div>
                  <div className="sent-comm-v">Pull <b style={{ fontWeight: 500 }}>R-01 Revenue Recognition</b> and <b style={{ fontWeight: 500 }}>R-02 Export Controls</b> forward in Q1 sample plan.</div>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {!data && !hasLiveData && (
        <Empty>Run the loop to populate forecasts, or click Run Loop in the sidebar.</Empty>
      )}
    </div>
  );
}

window.ForecastsPanel = ForecastsPanel;
