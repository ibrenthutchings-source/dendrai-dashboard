/* ============================================================
   Governance Intelligence
   • GovernancePane  — bottom navigation slideout (bar + nav strip)
   • GovernanceView  — main-pane content (all tabs live here)
   ============================================================ */
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ComposedChart, Area, ReferenceLine, Brush,
} from 'recharts';

const GOV_TABS = [
  { id: "overview",  l: "Overview" },
  { id: "board",     l: "Board & Audit Committee" },
  { id: "comp",      l: "Exec Compensation" },
  { id: "proposals", l: "Shareholder Proposals" },
  { id: "peers",     l: "Peer Benchmarking" },
];

// ── Section text renderer ────────────────────────────────────────────────────
function ProxySection({ text }) {
  if (!text) return <div className="gov-empty">No data extracted from filing.</div>;

  const bulletPat = /^([•·▪\-\*]|\d+[\.\)])\s+/;

  // Prefer paragraph-level splits (double newline) — each paragraph is a
  // coherent unit of legal text. Sentence-splitting breaks on abbreviations.
  let chunks = text.split(/\n{2,}/).map(c => c.replace(/\n/g, " ").trim()).filter(c => c.length > 30);

  // Fall back to single-newline lines if the text has no paragraph structure
  if (chunks.length <= 1) {
    chunks = text.split(/\n/).map(c => c.trim()).filter(c => c.length > 20);
  }

  // If lines already carry explicit bullet markers, merge continuation lines
  const hasBullets = chunks.some(c => bulletPat.test(c));
  let items;
  if (hasBullets) {
    items = [];
    let cur = null;
    for (const chunk of chunks) {
      if (bulletPat.test(chunk)) {
        if (cur !== null) items.push(cur.trim());
        cur = chunk.replace(bulletPat, "");
      } else if (cur !== null) {
        cur += " " + chunk;
      } else if (chunk.length > 30) {
        items.push(chunk);
      }
    }
    if (cur !== null) items.push(cur.trim());
  } else {
    items = chunks;
  }

  return (
    <ul className="gov-bullet-list">
      {items.slice(0, 10).map((item, i) => (
        <li key={i} className="gov-bullet-item">{item}</li>
      ))}
    </ul>
  );
}

// ── Peer benchmarking time series chart ─────────────────────────────────────
const _PEER_LINE_COLORS = ['var(--violet)', '#e8a838', '#4aad52', '#e05c5c', '#5bc4c4', '#9c6ade', '#3d8bd4', '#c77dff', '#57cc99', '#f4a261'];

const _PEER_METRICS = [
  { id: "gross_margin",   label: "Gross Margin" },
  { id: "rd_intensity",   label: "R&D Intensity" },
  { id: "revenue_growth", label: "Revenue Growth" },
];

// Linear-interpolation percentile (same convention as numpy/Excel's default) —
// good enough for a peer set of ~5-15 companies, no need for a stats library.
function _percentile(sorted, p) {
  if (!sorted.length) return null;
  if (sorted.length === 1) return sorted[0];
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

// Same statistical forecasting engine (ARIMA/Prophet-like/Random Forest
// ensemble, forecasting.js) the Pipeline's KPI ForecastChart runs — reused
// here rather than a second forecasting method, so "the company is
// projected to..." means the same thing on every screen. Annual ratio
// series are short (2-6 points); below 3 points the ensemble has nothing
// to fit against, so that series is left unforecast rather than showing a
// number the model made up from one or two points.
const _PEER_FORECAST_STEPS = 2;

function _forecastValues(values) {
  const F = window.FORECASTING;
  if (!F || !values || values.length < 3) return null;
  try {
    return F.predictEnsemble(F.fitEnsemble(values), _PEER_FORECAST_STEPS);
  } catch {
    return null;
  }
}

// Annual cadence assumed (this data is built from 10-K/20-F annual points —
// see api_server.py's _build_ratio_history) — advances the year, keeps
// month/day, so a non-calendar fiscal year-end still lands on the same date.
function _addYearsIso(isoDate, years) {
  const d = new Date(isoDate);
  if (isNaN(d.getTime())) return null;
  d.setUTCFullYear(d.getUTCFullYear() + years);
  return d.toISOString().slice(0, 10);
}

function PeerTimeSeriesChart({ peers, subjectHistory, ticker }) {
  // governance.jsx has no build-time import of components.jsx (cross-file
  // access here is always via window — see GovernanceView's RefreshBadge
  // below for the established pattern); a bare <AuditorTakeaway> JSX tag
  // would throw ReferenceError the same way evidence-pack.jsx's Row did.
  const AuditorTakeaway = window.AuditorTakeaway;
  // Same zoom/pan infrastructure Assess Risk's KPI charts use (charts.jsx,
  // loaded eagerly in src/main.jsx before this lazy-loaded screen can ever
  // render, so these are always present by the time this component mounts).
  // No ChartZoomProvider ancestor here — this screen has only the one chart,
  // so useChartZoom's documented no-provider fallback (independent local
  // zoom state, not synced/persisted) is exactly the right fit, unlike
  // Assess Risk's many-charts-in-one-provider case.
  const useChartZoom = window.useChartZoom;
  const useMountedAfterPaint = window.useMountedAfterPaint;
  const ZoomControls = window.ZoomControls;
  const [metric, setMetric] = useState("gross_margin");
  // Individual peer lines start hidden — with up to 15 peers, N overlapping
  // lines reads as spaghetti, not signal. The percentile band (below) answers
  // "moving with peers or breaking away" at a glance; any single peer can
  // still be toggled on via the legend for a direct one-to-one comparison.
  const [hidden, setHidden] = useState(() => new Set((peers || []).map((p, i) => p.company_name || p.ticker || `Peer ${i + 1}`)));

  const series = useMemo(() => {
    const list = [];
    if (subjectHistory?.length) {
      list.push({
        key: "__subject__", name: `${ticker?.toUpperCase() || "Company"} (You)`,
        color: "var(--acc)", strokeWidth: 2.4, history: subjectHistory,
      });
    }
    (peers || []).forEach((p, i) => {
      if (p.history?.length) {
        list.push({
          key: p.ticker || `peer-${i}`, name: p.company_name || p.ticker || `Peer ${i + 1}`,
          color: _PEER_LINE_COLORS[i % _PEER_LINE_COLORS.length], strokeWidth: 1.6, history: p.history,
        });
      }
    });
    return list;
  }, [peers, subjectHistory, ticker]);

  const peerSeries = useMemo(() => series.filter(s => s.key !== "__subject__"), [series]);

  // Rules of Hooks: useChartZoom/useMountedAfterPaint below must be called
  // unconditionally on every render, so the "nothing to chart yet" bail-out
  // moves to AFTER them (see below) rather than sitting here as it used to —
  // every computation between here and that bail-out is plain data-shaping
  // (no hooks) and already degrades to empty results on an empty `series`,
  // so nothing here needs series.length to be checked first.
  const allPeriods = Array.from(new Set(series.flatMap(s => s.history.map(h => h.period)))).sort();
  const data = allPeriods.map(period => {
    const row = { period };
    series.forEach(s => {
      const pt = s.history.find(h => h.period === period);
      row[s.key] = pt ? pt[metric] : null;
    });
    const peerVals = peerSeries
      .map(s => row[s.key])
      .filter(v => v != null && Number.isFinite(v))
      .sort((a, b) => a - b);
    if (peerVals.length >= 2) {
      row.peerRange = [_percentile(peerVals, 0.25), _percentile(peerVals, 0.75)];
      row.peerMedian = _percentile(peerVals, 0.5);
    }
    return row;
  });

  // Latest-period subject-vs-peer-median comparison, for the takeaway strip.
  // Deliberately neutral/informational — for these three metrics, "above" or
  // "below" median isn't reliably good or bad (e.g. revenue growth can also
  // signal Beneish SGI risk), so this states the fact, not a verdict.
  const latestWithMedian = [...data].reverse().find(r => r.peerMedian != null && r["__subject__"] != null);

  const fmtV = v => Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";

  // ---- Forecast, same ensemble engine + visual language as the Pipeline's
  // KPI ForecastChart (charts.jsx) — a dashed continuation of each series'
  // own line, a shaded band on the subject and on the peer-median aggregate,
  // and a "FORECAST →" divider at the last actual period. One series' own
  // history is what's forecast (own trend), not an interpolation against
  // the others — same semantics as ComparableChart/MultiSeriesForecastChart
  // already use for the Risk Loop's own peer-compare charts.
  const forecastsByKey = {};
  series.forEach(s => {
    const vals = s.history.filter(h => Number.isFinite(h[metric])).map(h => h[metric]);
    forecastsByKey[s.key] = _forecastValues(vals);
  });
  const lastPeriod = allPeriods[allPeriods.length - 1];
  const forecastPeriods = [];
  for (let i = 1; i <= _PEER_FORECAST_STEPS; i++) {
    const p = _addYearsIso(lastPeriod, i);
    if (p) forecastPeriods.push(p);
  }
  const hasForecast = forecastPeriods.length > 0 && series.some(s => forecastsByKey[s.key]);

  if (hasForecast) {
    const lastIdx = data.length - 1;
    // Seed every series' forecast at the last actual point so the dashed
    // line/band picks up with no gap — same anchoring trick ForecastChart
    // uses for its own history->forecast join.
    series.forEach(s => {
      if (!forecastsByKey[s.key]) return;
      const lastVal = data[lastIdx][s.key];
      if (Number.isFinite(lastVal)) {
        data[lastIdx][`${s.key}_base`] = lastVal;
        if (s.key === "__subject__") data[lastIdx][`${s.key}_band`] = [lastVal, lastVal];
      }
    });
    if (data[lastIdx].peerMedian != null) {
      data[lastIdx].peerMedianFc = data[lastIdx].peerMedian;
      data[lastIdx].peerRangeFc = data[lastIdx].peerRange;
    }

    forecastPeriods.forEach((period, i) => {
      const row = { period };
      series.forEach(s => {
        const fc = forecastsByKey[s.key];
        if (!fc) return;
        row[`${s.key}_base`] = fc.base[i];
        if (s.key === "__subject__") row[`${s.key}_band`] = [fc.lo[i], fc.hi[i]];
      });
      const peerFcVals = peerSeries
        .map(s => forecastsByKey[s.key]?.base?.[i])
        .filter(v => v != null && Number.isFinite(v))
        .sort((a, b) => a - b);
      if (peerFcVals.length >= 2) {
        row.peerRangeFc = [_percentile(peerFcVals, 0.25), _percentile(peerFcVals, 0.75)];
        row.peerMedianFc = _percentile(peerFcVals, 0.5);
      }
      data.push(row);
    });
  }

  // `data` is now final (the hasForecast block above may have pushed
  // forecast rows onto it via mutation) — its length is what
  // ComposedChart/Brush below actually render, so it's the right
  // dataLength for the zoom hook. Reading data.length any earlier (before
  // those pushes) would silently clip the default, unzoomed Brush window
  // to exclude the forecast rows even before a user ever zooms.
  const zoom = useChartZoom(data.length);
  const mounted = useMountedAfterPaint();
  // Same threshold as Assess Risk's KPI charts (charts.jsx's ZOOM_MIN_POINTS)
  // — not worth showing zoom controls on a handful of points.
  const showZoom = data.length >= (window.ZOOM_MIN_POINTS ?? 9);
  // Recharts can't resolve a categorical ReferenceLine's x={lastPeriod}
  // against a domain the Brush has scrolled/zoomed past — same NaN-
  // coordinate hazard ForecastChart's own splitVisible guards against.
  // Only meaningful once zoom can actually move the window (showZoom).
  const lastPeriodIndex = allPeriods.length - 1;
  const dividerVisible = !showZoom || (zoom.startIndex <= lastPeriodIndex && lastPeriodIndex <= zoom.endIndex);

  if (!series.length) return null;

  function ChartTooltip({ active, payload, label }) {
    if (!active || !payload?.length) return null;
    const row = payload[0]?.payload;
    const isFc = forecastPeriods.includes(label);
    const vals = series
      .map(s => {
        const actual = payload.find(p => p.dataKey === s.key)?.value;
        const fc = payload.find(p => p.dataKey === `${s.key}_base`)?.value;
        return { name: s.name, color: s.color, value: actual != null ? actual : fc, isFc: actual == null && fc != null };
      })
      .filter(v => v.value != null && !hidden.has(v.name));
    const medVal = row?.peerMedian != null ? row.peerMedian : row?.peerMedianFc;
    if (medVal != null) {
      vals.push({ name: `Peer median (n=${peerSeries.length})`, color: "var(--ink-3)", value: medVal, isFc: row?.peerMedian == null });
    }
    if (!vals.length) return null;
    return (
      <div style={{
        background: 'var(--bg)', border: '1px solid var(--line-strong)', borderRadius: 6,
        padding: '6px 10px', fontSize: 11, fontFamily: 'Geist Mono, monospace',
        boxShadow: '0 2px 8px rgba(0,0,0,0.12)', pointerEvents: 'none', maxHeight: 240, overflowY: 'auto',
      }}>
        <div style={{ color: 'var(--ink-3)', fontSize: 9, marginBottom: 4 }}>{label}{isFc ? ' · FORECAST' : ''}</div>
        {vals.map(v => (
          <div key={v.name} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: v.color, flexShrink: 0, opacity: v.isFc ? 0.6 : 1 }}/>
            <span style={{ color: 'var(--ink-2)', flex: 1, fontSize: 10, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontStyle: v.isFc ? 'italic' : 'normal' }}>{v.name}</span>
            <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{fmtV(v.value)}</span>
          </div>
        ))}
      </div>
    );
  }

  function toggle(name) {
    setHidden(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  }

  return (
    <div className="gov-peer-chart">
      <div className="gov-picker">
        {_PEER_METRICS.map(m => (
          <button key={m.id}
            className={"gov-pick-btn" + (metric === m.id ? " active" : "")}
            onClick={() => setMetric(m.id)}>
            {m.label}
          </button>
        ))}
      </div>
      {showZoom && ZoomControls && <ZoomControls zoom={zoom} color="var(--acc)"/>}
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" strokeOpacity={0.6} vertical={false}/>
          <XAxis dataKey="period"
            tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
            tickLine={false} axisLine={{ stroke: 'var(--line)' }}/>
          <YAxis tickFormatter={fmtV}
            tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
            tickLine={false} axisLine={false} width={48}/>
          <Tooltip content={<ChartTooltip/>} cursor={{ stroke: 'var(--line-strong)', strokeWidth: 1, strokeDasharray: '2 2' }}/>
          {/* Peer 25th-75th percentile band — drawn first so lines render on top. */}
          <Area dataKey="peerRange" fill="var(--ink-4)" fillOpacity={0.12} stroke="none"
            connectNulls isAnimationActive={false} legendType="none"/>
          <Line type="monotone" dataKey="peerMedian" stroke="var(--ink-3)" strokeWidth={1.4}
            strokeDasharray="3 3" dot={false} activeDot={{ r: 3, fill: "var(--ink-3)", strokeWidth: 0 }}
            connectNulls isAnimationActive={false} legendType="none"/>
          {series.map(s => (
            <Line key={s.key} type="monotone" dataKey={s.key}
              stroke={s.color} strokeWidth={s.strokeWidth}
              dot={{ r: 2, fill: s.color, strokeWidth: 0 }}
              activeDot={{ r: 4, fill: s.color, strokeWidth: 0 }}
              hide={hidden.has(s.name)}
              connectNulls
              isAnimationActive={false}
              legendType="none"/>
          ))}

          {hasForecast && (
            <>
              {/* Peer-median forecast band — same peer-aggregate concept as
                  peerRange above, projected forward. */}
              <Area dataKey="peerRangeFc" fill="var(--ink-4)" fillOpacity={0.12} stroke="none"
                connectNulls isAnimationActive={false} legendType="none"/>
              <Line type="monotone" dataKey="peerMedianFc" stroke="var(--ink-3)" strokeWidth={1.2}
                strokeDasharray="2 2" opacity={0.7} dot={false} activeDot={{ r: 3, fill: "var(--ink-3)", strokeWidth: 0 }}
                connectNulls isAnimationActive={false} legendType="none"/>
              {/* Subject's own confidence band — the only individual series
                  banded, same as ForecastChart (peer lines get a dashed
                  continuation only, no band, matching how their actuals
                  already carry no band either — only the aggregate does). */}
              <Area dataKey="__subject___band" fill="var(--acc)" fillOpacity={0.18} stroke="none"
                connectNulls isAnimationActive={false} legendType="none"/>
              {series.map(s => forecastsByKey[s.key] && (
                <Line key={`${s.key}-fc`} type="monotone" dataKey={`${s.key}_base`}
                  stroke={s.color} strokeWidth={s.strokeWidth}
                  strokeDasharray="5 4" opacity={0.85}
                  dot={{ r: 3, fill: 'var(--bg)', stroke: s.color, strokeWidth: 1.6 }}
                  activeDot={{ r: 5, fill: s.color, strokeWidth: 0 }}
                  hide={hidden.has(s.name)}
                  connectNulls
                  isAnimationActive={false}
                  legendType="none"/>
              ))}
              {dividerVisible && (
                <ReferenceLine x={lastPeriod} stroke="var(--line-strong)" strokeDasharray="3 3" strokeWidth={0.8}
                  label={{ value: 'FORECAST →', position: 'insideTopRight', fontSize: 8.5, fontFamily: 'Geist Mono, monospace', fill: 'var(--ink-3)', dy: -4 }}/>
              )}
            </>
          )}

          {/* Zoom/pan — same mechanism as Assess Risk's KPI charts
              (charts.jsx's ForecastChart): drag either handle, or use the
              +/− controls above. Gated on `mounted` (useMountedAfterPaint)
              to avoid Recharts' transient NaN traveller coordinates against
              an unmeasured ResponsiveContainer on the very first paint. */}
          {showZoom && mounted && (
            <Brush dataKey="period" height={20} travellerWidth={8}
              startIndex={zoom.startIndex} endIndex={zoom.endIndex} onChange={zoom.onBrushChange}
              stroke="var(--acc)" fill="var(--surface-2, var(--surface))"
              tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}/>
          )}
        </ComposedChart>
      </ResponsiveContainer>
      <div className="gov-peer-legend">
        <span className="gov-peer-legend-item" style={{ cursor: "default" }} title="25th-75th percentile of peers with data this period">
          <span className="gov-peer-legend-swatch" style={{background: "var(--ink-4)", opacity: 0.4}}/>
          Peer range (p25–p75)
        </span>
        <span className="gov-peer-legend-item" style={{ cursor: "default" }}>
          <span className="gov-peer-legend-swatch" style={{background: "var(--ink-3)"}}/>
          Peer median
        </span>
        {series.map(s => (
          <button key={s.key}
            className={"gov-peer-legend-item" + (hidden.has(s.name) ? " off" : "")}
            onClick={() => toggle(s.name)}
            title={hidden.has(s.name) ? "Click to show" : "Click to hide"}>
            <span className="gov-peer-legend-swatch" style={{background: s.color}}/>
            {s.name}
          </button>
        ))}
      </div>
      {hasForecast && (
        <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', padding: '4px 2px 0', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ display: 'inline-block', width: 14, borderBottom: '2px solid var(--ink-3)' }} /> Actual
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ display: 'inline-block', width: 14, borderBottom: '2px dashed var(--ink-3)' }} /> Forecast — {_PEER_FORECAST_STEPS}-year ensemble (ARIMA / Prophet-like / Random Forest), same engine as the Pipeline's KPI forecasts
          </span>
        </div>
      )}
      {latestWithMedian && AuditorTakeaway && (() => {
        const subj = latestWithMedian["__subject__"];
        const med = latestWithMedian.peerMedian;
        const diffPts = (subj - med) * 100;
        const metricLabel = _PEER_METRICS.find(m => m.id === metric)?.label || metric;
        return (
          <AuditorTakeaway tone="info">
            {metricLabel} ({latestWithMedian.period}): {fmtV(subj)} for {ticker?.toUpperCase() || "this company"} vs.{" "}
            {fmtV(med)} peer median ({peerSeries.length} peers) — {Math.abs(diffPts) < 0.1 ? "in line with peers" : `${diffPts > 0 ? diffPts.toFixed(1) + " pts above" : Math.abs(diffPts).toFixed(1) + " pts below"} the peer median`}.
          </AuditorTakeaway>
        );
      })()}
    </div>
  );
}

// ── Peer table ───────────────────────────────────────────────────────────────
function PeerTable({ peers, sic, sic_description, ticker, peerSource, namedCompetitors }) {
  if (!peers?.length) return <div className="gov-empty">No peer data — run in MCP mode to fetch peer intelligence.</div>;
  const fromTenK = peerSource && peerSource.startsWith("10-K");
  return (
    <div>
      <div className="gov-meta-row">
        <span className="gov-meta-label">Source</span>
        <span className="gov-meta-val">{peerSource || "SIC peers"}</span>
        <span className="gov-meta-label" style={{marginLeft: 16}}>SIC</span>
        <span className="gov-meta-val mono">{sic}</span>
        <span className="gov-meta-label" style={{marginLeft: 16}}>Industry</span>
        <span className="gov-meta-val">{sic_description || "—"}</span>
        <span className="gov-meta-label" style={{marginLeft: 16}}>{peers.length} with data</span>
      </div>
      {fromTenK && namedCompetitors?.length > 0 && (
        <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", margin: "0 0 10px", lineHeight: 1.5}}>
          Named in {ticker?.toUpperCase()}'s 10-K: {namedCompetitors.join(" · ")}
          {namedCompetitors.length > peers.length && (
            <span> — {namedCompetitors.length - peers.length} dropped (no EDGAR financial data)</span>
          )}
        </div>
      )}
      <table className="gov-table">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Company</th>
            <th style={{width: 72, textAlign:"right"}}>Gross Margin</th>
            <th style={{width: 72, textAlign:"right"}}>R&D %</th>
            <th style={{width: 72, textAlign:"right"}}>Rev Growth</th>
          </tr>
        </thead>
        <tbody>
          {peers.map((p, i) => {
            const isSelf = p.ticker && p.ticker.toUpperCase() === ticker?.toUpperCase();
            return (
              <tr key={i} style={isSelf ? {background: "var(--acc-soft)", fontWeight: 600} : null}>
                <td className="mono">{p.ticker || "—"}{isSelf ? " ★" : ""}</td>
                <td>{p.company_name}</td>
                <td className="mono" style={{textAlign:"right"}}>{p.gross_margin != null ? `${(p.gross_margin * 100).toFixed(1)}%` : "—"}</td>
                <td className="mono" style={{textAlign:"right"}}>{p.rd_intensity  != null ? `${(p.rd_intensity  * 100).toFixed(1)}%` : "—"}</td>
                <td className="mono" style={{textAlign:"right"}}>{p.revenue_growth != null ? `${(p.revenue_growth * 100).toFixed(1)}%` : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Material-account comparison across peers — the accounts detected as
// material for the SUBJECT's industry template (manufacturing/
// financial_services/saas, see material_accounts_tool.py), scored the same
// way for every peer so they're directly comparable. Populated server-side
// as peers[i].material_accounts by api_server.py's _enrich_peer_financials
// — nothing to fetch here, this is presentational only, same as PeerTable.
function PeerMaterialAccountsTable({ peers, ticker }) {
  const withAccounts = (peers || []).filter(p => p.material_accounts?.length);
  if (!withAccounts.length) return null;

  // Union of metrics across all peers, ordered by how many peers actually
  // have data for each (most-covered first) — a metric only one peer
  // discloses sinks to the bottom rather than crowding out ones every peer
  // reports.
  const byMetric = {};
  withAccounts.forEach(p => {
    p.material_accounts.forEach(a => {
      const bucket = byMetric[a.metric] || (byMetric[a.metric] = { label: a.label || a.metric, count: 0 });
      bucket.count += 1;
    });
  });
  const metrics = Object.entries(byMetric).sort((a, b) => b[1].count - a[1].count).map(([m]) => m);

  return (
    <div>
      <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", margin: "0 0 10px", lineHeight: 1.5}}>
        Accounts the subject's own industry template flags as material (≥5% of revenue or total assets) — every peer
        below is scored on the same line items, not each peer's own largest accounts.
      </div>
      <table className="gov-table">
        <thead>
          <tr>
            <th>Account</th>
            {withAccounts.map((p, i) => (
              <th key={i} style={{width: 90, textAlign: "right"}}>{p.ticker || "—"}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {metrics.map(metric => (
            <tr key={metric}>
              <td>{byMetric[metric].label}</td>
              {withAccounts.map((p, i) => {
                const acc = p.material_accounts.find(a => a.metric === metric);
                return (
                  <td key={i} className="mono" style={{textAlign: "right"}}>
                    {acc?.ratio != null ? `${(acc.ratio * 100).toFixed(1)}%` : "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Filing selector ──────────────────────────────────────────────────────────
function FilingPicker({ filings, selected, onSelect }) {
  if (!filings?.length) return null;
  return (
    <div className="gov-picker">
      {filings.map((f, i) => (
        <button key={i}
          className={"gov-pick-btn" + (selected === i ? " active" : "")}
          onClick={() => onSelect(i)}>
          {f.filing_date}
        </button>
      ))}
    </div>
  );
}

// ── Bottom navigation slideout ────────────────────────────────────────────────
// Shows the persistent bar + a navigation strip when open.
// Clicking a nav item calls onSelectTab(id), which app.jsx wires to switch the
// main pane to the Governance tab with the right sub-section active.
function GovernancePane({ open, onToggle, data, peerData, ticker, loading, activeTab, onSelectTab }) {
  const proxy = data?.proxy_filings || [];

  return (
    <div className={"gov-shell" + (open ? " open" : "")}>

      {/* ── Persistent bar ── */}
      <div className="gov-bar" onClick={onToggle}>
        <div className="gov-bar-left">
          <div className="gov-bar-icon">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <rect x="1" y="1" width="5" height="5" rx="1.2" fill="currentColor" opacity=".7"/>
              <rect x="8" y="1" width="5" height="5" rx="1.2" fill="currentColor" opacity=".7"/>
              <rect x="1" y="8" width="5" height="5" rx="1.2" fill="currentColor"/>
              <rect x="8" y="8" width="5" height="5" rx="1.2" fill="currentColor" opacity=".45"/>
            </svg>
          </div>
          <span className="gov-bar-label">Board Intelligence</span>
          {data && (
            <span className="gov-bar-badge">
              {data.company_name || ticker}
            </span>
          )}
          {loading && <span className="gov-bar-status">Fetching…</span>}
          {!loading && !data && <span className="gov-bar-status muted">Run in MCP mode to load</span>}
        </div>
        <div className="gov-bar-right">
          {data && (
            <span className="gov-bar-meta mono">
              {proxy.length} proxy filing{proxy.length !== 1 ? "s" : ""}
              {peerData ? ` · ${peerData.peers?.length || 0} peers` : ""}
            </span>
          )}
          <svg className={"gov-chevron" + (open ? " up" : "")}
               width="12" height="12" viewBox="0 0 12 12">
            <path d="M2 8L6 4L10 8" stroke="currentColor" strokeWidth="1.6"
                  strokeLinecap="round" strokeLinejoin="round" fill="none"/>
          </svg>
        </div>
      </div>

      {/* ── Navigation strip ── */}
      <div className="gov-pane">
        <div className="gov-nav">
          <span className="gov-nav-hint">Jump to section:</span>
          {GOV_TABS.map(t => (
            <button key={t.id}
              className={"gov-nav-item" + (activeTab === t.id ? " active" : "")}
              onClick={(e) => { e.stopPropagation(); onSelectTab(t.id); }}>
              {t.l}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Main-pane Governance view ─────────────────────────────────────────────────
// Rendered inside a .panel div in app.jsx. Contains all five sub-tab views.
function GovernanceView({ data, peerData, ticker, loading, activeTab, onTabChange, govFetchError, peerFetchError, lastRefresh, onRefresh }) {
  const RefreshBadge = window.RefreshBadge;
  const [filingIdx, setFilingIdx] = useState(0);

  const proxy    = data?.proxy_filings || [];
  const filing   = proxy[filingIdx];
  const sections = filing?.sections || {};

  return (
    <div className="gov-view">
      {/* Header */}
      <div className="panel-head">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div className="kicker">Proxy Data · SEC EDGAR DEF 14A</div>
            <div className="panel-title mt-8">Board Intelligence</div>
            {data
              ? <div className="panel-sub">
                  {data.company_name || ticker} · {proxy.length} proxy filing{proxy.length !== 1 ? "s" : ""}
                  {peerData ? ` · ${peerData.peers?.length || 0} peers` : ""}
                </div>
              : <div className="panel-sub">
                  Board composition, exec compensation, shareholder proposals &amp; peer benchmarks from SEC EDGAR.
                </div>
            }
          </div>
          {onRefresh && <RefreshBadge lastRefresh={lastRefresh} onRefresh={onRefresh} loading={loading} />}
        </div>
      </div>

      {/* Tab bar */}
      <div className="gov-tab-bar gov-view-tab-bar">
        {GOV_TABS.map(t => (
          <button key={t.id}
            className={"gov-tab" + (activeTab === t.id ? " active" : "")}
            onClick={() => onTabChange(t.id)}>
            {t.l}
          </button>
        ))}
        {proxy.length > 1 && (
          <div style={{marginLeft: "auto"}}>
            <FilingPicker filings={proxy} selected={filingIdx} onSelect={setFilingIdx}/>
          </div>
        )}
      </div>

      {/* Splash — no data yet */}
      {!data && !loading && (
        <div className="gov-splash gov-view-splash">
          <div className="gov-splash-icon">
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
              <rect x="3"  y="3"  width="11" height="11" rx="2.5" fill="var(--acc)" opacity=".25"/>
              <rect x="18" y="3"  width="11" height="11" rx="2.5" fill="var(--acc)" opacity=".45"/>
              <rect x="3"  y="18" width="11" height="11" rx="2.5" fill="var(--acc)" opacity=".65"/>
              <rect x="18" y="18" width="11" height="11" rx="2.5" fill="var(--acc)" opacity=".2"/>
            </svg>
          </div>
          <div className="gov-splash-title">Board Intelligence</div>
          <div className="gov-splash-desc">
            {govFetchError
              ? <>MCP server unreachable — start <code style={{fontFamily:"monospace",fontSize:11}}>api_server.py</code> before running in MCP mode.
                  <br/><span style={{color:"var(--red-ink)", marginTop: 4, display:"block"}}>{govFetchError}</span></>
              : "Switch to MCP mode and run the loop to fetch proxy data (DEF 14A), board composition, exec compensation, and peer benchmarks from SEC EDGAR."
            }
          </div>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="gov-splash gov-view-splash">
          <span className="spin" style={{width:20,height:20,borderWidth:2}}/>
          <div className="gov-splash-desc" style={{marginTop: 12}}>Fetching governance data from SEC EDGAR…</div>
        </div>
      )}

      {/* Content */}
      {data && !loading && (
        <div className="gov-view-content">
          {activeTab === "overview" && (
            <div className="gov-content">
              <div className="gov-overview-grid">
                <GovInfoCard title="Company" value={data.company_name || ticker}/>
                <GovInfoCard title="Latest Proxy" value={proxy[0]?.filing_date || "—"}/>
                <GovInfoCard title="Proxy Filings" value={`${proxy.length} in range`}/>
                {peerData && <GovInfoCard title="Peers" value={`${peerData.peers?.length || 0} · ${peerData.peer_source || "SIC"}`}/>}
              </div>
              <div className="gov-section-hd">Key Governance Sections Found</div>
              <div className="gov-section-chips">
                {Object.keys(sections).map(k => (
                  <span key={k} className="gov-chip">{_sectionLabel(k)}</span>
                ))}
                {Object.keys(sections).length === 0 && (
                  <span className="gov-empty">No structured sections extracted from this filing.</span>
                )}
              </div>
              {sections.executive_compensation && (
                <>
                  <div className="gov-section-hd">Compensation Snapshot</div>
                  <ProxySection text={sections.executive_compensation}/>
                </>
              )}
            </div>
          )}

          {activeTab === "board" && (
            <div className="gov-content">
              {sections.audit_committee ? (
                <>
                  <div className="gov-section-hd">Audit Committee</div>
                  <ProxySection text={sections.audit_committee}/>
                </>
              ) : (
                <div className="gov-empty">Audit committee section not extracted from this proxy filing.</div>
              )}
            </div>
          )}

          {activeTab === "comp" && (
            <div className="gov-content">
              {sections.executive_compensation ? (
                <>
                  <div className="gov-section-hd">Compensation Discussion & Analysis (CD&A)</div>
                  <ProxySection text={sections.executive_compensation}/>
                </>
              ) : (
                <div className="gov-empty">Compensation section not extracted from this proxy filing.</div>
              )}
            </div>
          )}

          {activeTab === "proposals" && (
            <div className="gov-content">
              {sections.shareholder_proposals ? (
                <>
                  <div className="gov-section-hd">Shareholder Proposals</div>
                  <ProxySection text={sections.shareholder_proposals}/>
                </>
              ) : (
                <div className="gov-empty">No shareholder proposals extracted from this proxy filing.</div>
              )}
            </div>
          )}

          {activeTab === "peers" && (
            <div className="gov-content">
              {peerFetchError && !peerData?.peers?.length && (
                <div style={{
                  fontSize: 11, color: "var(--red-ink)", background: "var(--red-soft)",
                  padding: "8px 12px", borderRadius: 6, marginBottom: 12, lineHeight: 1.5,
                }}>
                  Peer fetch failed — proxy data loaded fine, but the peer benchmarking request errored or timed out
                  separately (10-K competitor extraction + per-peer XBRL enrichment is the slow part). This is why
                  it may look like nothing happened rather than showing a generic "no data" message.
                  <div className="mono" style={{ marginTop: 4, fontSize: 10.5 }}>{peerFetchError}</div>
                </div>
              )}
              <div className="gov-section-hd">Peer Trend — Gross Margin / R&amp;D Intensity / Revenue Growth</div>
              <PeerTimeSeriesChart
                peers={peerData?.peers}
                subjectHistory={peerData?.subject_history}
                ticker={ticker}/>
              <div className="gov-section-hd" style={{marginTop: 16}}>Latest Snapshot</div>
              <PeerTable
                peers={peerData?.peers}
                sic={peerData?.sic}
                sic_description={peerData?.sic_description}
                peerSource={peerData?.peer_source}
                namedCompetitors={peerData?.named_competitors}
                ticker={ticker}/>
              {(peerData?.peers || []).some(p => p.material_accounts?.length) && (
                <>
                  <div className="gov-section-hd" style={{marginTop: 16}}>Material Accounts</div>
                  <PeerMaterialAccountsTable peers={peerData?.peers} ticker={ticker}/>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function GovInfoCard({ title, value }) {
  return (
    <div className="gov-info-card">
      <div className="gov-info-label">{title}</div>
      <div className="gov-info-val">{value}</div>
    </div>
  );
}

function _sectionLabel(key) {
  return {
    executive_compensation: "Exec Compensation (CD&A)",
    audit_committee:        "Audit Committee",
    shareholder_proposals:  "Shareholder Proposals",
    vote_results:           "Vote Results",
    director_compensation:  "Director Compensation",
  }[key] || key.replace(/_/g, " ");
}

window.GovernancePane = GovernancePane;
window.GovernanceView = GovernanceView;
