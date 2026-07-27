/* ============================================================
   Scenario Analysis — quantitative risk tooling
   Five tabs: VaR/CVaR, Sensitivity/Tornado, Multi-Factor Stress Test,
   Liquidity/Covenant Runway, Composite Early-Warning Indicator.
   Each tab pairs an interactive Recharts visualization with an
   assumptions/methodology block. Data is computed client-side in
   risk-engine.js (buildVarCvar, buildSensitivity, buildMultiFactorStress,
   buildLiquidityRunway, buildEarlyWarningIndicator) and passed in as props.
   ============================================================ */

import {
  BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer,
  Cell,
} from 'recharts';

const SCEN_TABS = [
  { id: "var",         l: "VaR / CVaR" },
  { id: "sensitivity", l: "Sensitivity / Tornado" },
  { id: "stress",      l: "Multi-Factor Stress Test" },
  { id: "liquidity",   l: "Liquidity / Covenant Runway" },
  { id: "ewi",         l: "Early-Warning Indicator" },
];

function saFmtM(v) {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1000) return `$${(v / 1000).toFixed(1)}B`;
  return `$${Math.round(v)}M`;
}

function SaTooltip({ active, payload, label, formatter }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: 'var(--bg)', border: '1px solid var(--line-strong)', borderRadius: 6, padding: '6px 10px', fontSize: 11, fontFamily: 'Geist Mono, monospace', boxShadow: '0 2px 8px rgba(0,0,0,0.12)' }}>
      {label != null && <div style={{ marginBottom: 3, color: 'var(--ink-3)' }}>{label}</div>}
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color || 'var(--ink)' }}>
          {p.name ? `${p.name}: ` : ''}{formatter ? formatter(p.value) : p.value}
        </div>
      ))}
    </div>
  );
}

// ── Plain-English metric guides — meaning / impact / next steps per tab ────
// Static reference content (not derived from `data`), shown between the
// chart and the ASSUMPTIONS & METHODOLOGY block: chart → what it means →
// how it's computed.

const _VAR_GUIDE = [
  { metric: "VaR 95%", meaning: "The revenue decline you should expect not to exceed in 95% of simulated outcomes — there's roughly a 1-in-20 chance the actual decline is worse than this.",
    impact: "Sizes the \"reasonably possible\" downside for budgeting and covenant stress testing.",
    action: "Compare against the cash starting position on the Liquidity tab — if VaR 95 regularly exceeds available cash headroom, that's a structural liquidity gap, not just a tail-risk curiosity." },
  { metric: "CVaR 95% (Expected Shortfall)", meaning: "The average decline across just the worst 5% of simulated outcomes — \"if things do go bad, how bad on average.\"",
    impact: "CVaR is always worse than VaR at the same confidence level; the size of the gap between them shows how severe the tail is, not just how likely.",
    action: "A CVaR far worse than VaR means the downside is fat-tailed — prioritize downside protection (hedging, diversification, covenant headroom) over routine monitoring." },
  { metric: "VaR 99% / CVaR 99%", meaning: "The same two measures at the more extreme 1-in-100 tail — the truly rare, severe scenarios.",
    impact: "Relevant for board-level risk appetite and going-concern discussions, not day-to-day forecasting.",
    action: "Feed into enterprise risk appetite statements and severe-scenario contingency planning rather than quarterly management reporting." },
  { metric: "Prob. Decline", meaning: "The share of all simulated paths that end below today's revenue — how likely any decline is, independent of how severe.",
    impact: "A high probability of decline even with a modest VaR points to a structurally weak growth outlook, not just tail risk.",
    action: "If this is elevated (well above ~30-40%), revisit the underlying growth assumptions feeding the forecast, not just the tail-risk hedges." },
];

const _SENS_GUIDE = [
  { metric: "Baseline portfolio score", meaning: "The current total risk score across the whole register — the starting point every shock in this tab is measured against.",
    impact: "Doesn't change per row; it's the anchor for every Δ Score in the table.",
    action: "Track this number across pipeline reruns over time — the tornado only shows sensitivity around this point, not whether the point itself is improving or worsening." },
  { metric: "Downside / Upside shock (Δ Score)", meaning: "How much the total risk score would move if one financial ratio alone moved unfavorably or favorably by a fixed amount, holding everything else constant.",
    impact: "Factors with a large downside delta are where the portfolio is most fragile — a small realistic move in that one ratio meaningfully worsens overall risk.",
    action: "Prioritize monitoring and controls on the top 1-2 factors by delta size — these are the highest-leverage risk drivers, not necessarily the highest-scored individual risks today." },
  { metric: "Swing", meaning: "The combined sensitivity range for a factor (|downside| + |upside|) — one number ranking how much that factor matters overall, in either direction.",
    impact: "Determines the tornado chart's ordering; the factor at the top is the single most consequential input to the risk score.",
    action: "Concentrate scenario-planning effort on the highest-swing factors; factors with near-zero swing are safe to deprioritize." },
];

const _STRESS_GUIDE = [
  { metric: "Rev. Decline / Margin Compression", meaning: "The combined revenue and margin shocks assumed for that named scenario (Base/Stress/Severe) — inputs, not outputs, describing how bad the scenario is.",
    impact: "Defines what \"stress\" and \"severe\" concretely mean for this company's numbers.",
    action: "Sanity-check these shock sizes against real historical downturns for the industry (e.g. 2008, 2020) to confirm the scenarios are realistically calibrated, not arbitrary." },
  { metric: "Revenue at Risk", meaning: "The dollar amount of revenue exposed under that scenario.",
    impact: "Translates an abstract percentage shock into a concrete figure finance and the board can act on.",
    action: "Compare against the planning/performance materiality thresholds on SOX Scope to judge whether this scenario alone would be a reportable, audit-relevant event." },
  { metric: "Stressed FCF Margin / Headroom", meaning: "The free-cash-flow margin after the shock, and how much room remains before the going-concern trigger.",
    impact: "Headroom is the single most important number on this tab — the buffer between \"stressed but survivable\" and \"breach.\"",
    action: "For any scenario with headroom under roughly 2-3 points, treat it as an active watch item and model management levers (cost cuts, capex deferral, financing) that could restore headroom before it becomes plausible." },
  { metric: "Status (BREACH / OK)", meaning: "Whether the stressed FCF margin falls below the going-concern trigger threshold.",
    impact: "A BREACH is a binary, board-relevant flag — it means this scenario, if realized, would raise going-concern questions from auditors.",
    action: "Route any BREACH scenario directly into the Risk Register as a tracked risk with an owner and mitigation plan, rather than leaving it in this table." },
  { metric: "Top contributing risks", meaning: "The highest-scored register risks most associated with this scenario's underlying theme.",
    impact: "Connects the abstract stress number back to concrete, already-tracked risks.",
    action: "Cross-check these risk IDs in the Risks & Controls Register to confirm mitigations are weighted toward the scenario most likely to actually occur." },
];

const _LIQ_GUIDE = [
  { metric: "Per-scenario runway (Q# or \"Clear\")", meaning: "The quarter in which cash would run out or a covenant would be breached under that FCF-margin path — or \"Clear\" if neither happens within the horizon.",
    impact: "A hard, dated exposure rather than a soft risk score — \"Q3\" means a real cash or covenant event by that quarter if the scenario plays out.",
    action: "For any scenario showing a specific quarter, treasury should have a financing or refinancing conversation in motion well before that quarter arrives, not after." },
  { metric: "Covenant breach vs. cash depletion (\"*\")", meaning: "The asterisk marks a covenant threshold breach (a contractual trigger with lenders) as distinct from actually running out of cash — different severities.",
    impact: "A covenant breach is typically a technical default that's renegotiable; cash depletion is an operational crisis with no ability to pay obligations.",
    action: "For covenant-breach scenarios, engage lenders proactively for a waiver or amendment before breach; for cash-depletion scenarios, prioritize immediate liquidity actions — credit facility draw, asset sales, or a capital raise." },
  { metric: "Starting cash / base FCF margin", meaning: "The actual cash position and current FCF margin the projections start from.",
    impact: "Sanity-checks whether the runway numbers are proportionate — a thin starting cash balance makes even a mild stress scenario dangerous.",
    action: "If starting cash looks thin relative to the burn rate in the Stress/Severe paths, build cash reserves now, during the current benign period, rather than waiting for a downturn to start." },
];

const _EWI_GUIDE = [
  { metric: "Composite Score / Level", meaning: "A single blended 0-100 score combining risk velocity, revenue momentum, earnings quality, and macro backdrop into one early-warning read, banded into GREEN / AMBER / RED.",
    impact: "The \"check this first\" number — RED means several independent signals are flashing at once, which is a materially different situation than any single bad metric in isolation.",
    action: "Treat a move from GREEN/AMBER into RED as a trigger for an off-cycle risk review — don't wait for the next scheduled pipeline run, since it signals the underlying picture has changed, not just one input." },
  { metric: "Historical Trend", meaning: "How the composite score has moved over recent periods, with reference lines at the AMBER (40) and RED (65) thresholds.",
    impact: "A steadily rising trend is itself a warning even while still technically GREEN — trajectory matters as much as the current level.",
    action: "If the trend is climbing within GREEN, investigate which component is driving the climb now, rather than waiting for a threshold crossing to act." },
  { metric: "Component Breakdown", meaning: "The four weighted inputs behind the composite score — Risk Velocity (30%), Revenue Momentum (25%), Earnings Quality (25%), Macro Backdrop (20%) — each with its own 0-100 sub-score.",
    impact: "Identifies which underlying driver is responsible for the overall read; two companies can land on the same composite score for very different reasons.",
    action: "Focus the next risk review on whichever component scores highest individually — that's the actual root cause, and mitigations should target it specifically rather than treating the composite as one monolithic problem." },
];

function MetricGuide({ items }) {
  if (!items?.length) return null;
  return (
    <div style={{ marginTop: 18, padding: "14px 16px", background: "var(--surface)", borderRadius: 8, border: "1px solid var(--line)" }}>
      <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 10 }}>WHAT THIS MEANS</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {items.map((it, i) => (
          <div key={i} style={{ paddingBottom: i < items.length - 1 ? 12 : 0, borderBottom: i < items.length - 1 ? "1px solid var(--line)" : "none" }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)", marginBottom: 3 }}>{it.metric}</div>
            <div style={{ fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.55, marginBottom: 5 }}>{it.meaning}</div>
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 220 }}>
                <span className="mono" style={{ fontSize: 9, color: "var(--ink-4)", fontWeight: 600, letterSpacing: "0.03em" }}>IMPACT </span>
                <span style={{ fontSize: 11, color: "var(--ink-3)", lineHeight: 1.5 }}>{it.impact}</span>
              </div>
              <div style={{ flex: 1, minWidth: 220 }}>
                <span className="mono" style={{ fontSize: 9, color: "var(--acc-ink)", fontWeight: 600, letterSpacing: "0.03em" }}>NEXT STEPS </span>
                <span style={{ fontSize: 11, color: "var(--ink-3)", lineHeight: 1.5 }}>{it.action}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AssumptionsBlock({ items }) {
  if (!items?.length) return null;
  return (
    <div style={{ marginTop: 18, padding: "12px 14px", background: "var(--surface-2, var(--surface))", borderRadius: 8, border: "1px solid var(--line)" }}>
      <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 8 }}>ASSUMPTIONS &amp; METHODOLOGY</div>
      <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6 }}>
        {items.map((a, i) => <li key={i} style={{ fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.55 }}>{a}</li>)}
      </ul>
    </div>
  );
}

function StatTile({ label, value, sub, color }) {
  return (
    <div style={{ flex: 1, minWidth: 108 }}>
      <div className="mono" style={{ fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 3 }}>{label.toUpperCase()}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: color || "var(--ink)", fontFamily: "var(--mono)" }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function TabCard({ children }) {
  return <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "18px 20px" }}>{children}</div>;
}

function TabHead({ kicker, title, sub }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div className="kicker">{kicker}</div>
      <div style={{ fontSize: 16, fontWeight: 700, color: "var(--ink)", marginTop: 2 }}>{title}</div>
      {sub && <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

// ── Tab 1: VaR / CVaR ──────────────────────────────────────────────────────

function VarCvarTab({ data }) {
  if (!data) return <Empty>Run the pipeline to generate the revenue distribution.</Empty>;
  const chartData = data.histogram.map(b => ({ x: b.mid, count: b.count }));
  const tailCutoff = data.base_revenue_m - data.var_95_m;

  return (
    <TabCard>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 14, marginBottom: 4 }}>
        <TabHead kicker="Tail Risk" title={`Revenue Value-at-Risk · ${data.horizon_quarters}Q horizon`}
          sub={`${data.n_sims.toLocaleString()} simulated paths from a base of ${saFmtM(data.base_revenue_m)} · ${data.volatility_pct}% quarterly volatility`} />
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
          <StatTile label="VaR 95%" value={saFmtM(data.var_95_m)} color="var(--amber-ink)" sub="5% worst-case decline" />
          <StatTile label="CVaR 95%" value={saFmtM(data.cvar_95_m)} color="var(--red-ink)" sub="avg. of worst 5%" />
          <StatTile label="VaR 99%" value={saFmtM(data.var_99_m)} color="var(--amber-ink)" />
          <StatTile label="CVaR 99%" value={saFmtM(data.cvar_99_m)} color="var(--red-ink)" />
          <StatTile label="Prob. Decline" value={`${Math.round(data.prob_decline * 100)}%`} />
        </div>
      </div>
      <ResponsiveContainer width="100%" height={230}>
        <BarChart data={chartData} margin={{ top: 12, right: 16, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
          <XAxis dataKey="x" tickFormatter={saFmtM} tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }} tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
          <YAxis tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }} tickLine={false} axisLine={false} width={30} />
          <Tooltip content={<SaTooltip formatter={v => `${v} of ${data.n_sims} sims`} />} cursor={{ fill: 'var(--surface-2)' }} />
          <Bar dataKey="count" radius={[3, 3, 0, 0]} isAnimationActive={true}>
            {chartData.map((d, i) => (
              <Cell key={i} fill={d.x <= tailCutoff ? "var(--red)" : "var(--acc)"} fillOpacity={0.75} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div style={{ display: "flex", gap: 16, marginTop: 8, fontSize: 10.5, color: "var(--ink-3)" }}>
        <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "var(--red)", marginRight: 5, opacity: 0.75 }} />Worst 5% of outcomes (VaR 95 tail)</span>
        <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "var(--acc)", marginRight: 5, opacity: 0.75 }} />Simulated outcome distribution</span>
      </div>
      <MetricGuide items={_VAR_GUIDE} />
      <AssumptionsBlock items={data.assumptions} />
    </TabCard>
  );
}

// ── Tab 2: Sensitivity / Tornado ───────────────────────────────────────────

function SensitivityTab({ data }) {
  if (!data) return <Empty>Run the pipeline to generate sensitivity data.</Empty>;
  const chartData = [...data.rows].reverse();
  const maxAbs = Math.max(1, ...data.rows.map(r => Math.max(Math.abs(r.down_delta), Math.abs(r.up_delta))));

  return (
    <TabCard>
      <TabHead kicker="Assumption Sensitivity" title="Portfolio Risk Score Tornado"
        sub={<>Baseline portfolio score: <b style={{ color: "var(--ink)" }}>{data.baseline_score}</b> · each factor shocked independently, others held constant</>} />
      <ResponsiveContainer width="100%" height={Math.max(220, chartData.length * 44)}>
        <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 28, bottom: 4, left: 4 }} barGap={2}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" strokeOpacity={0.6} horizontal={false} />
          <XAxis type="number" domain={[-maxAbs * 1.15, maxAbs * 1.15]} tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }} tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
          <YAxis type="category" dataKey="label" width={150} tick={{ fontSize: 10.5, fill: 'var(--ink-2)' }} tickLine={false} axisLine={false} />
          <Tooltip content={<SaTooltip formatter={v => `${v >= 0 ? '+' : ''}${v} pts`} />} cursor={{ fill: 'var(--surface-2)' }} />
          <ReferenceLine x={0} stroke="var(--ink-3)" />
          <Bar dataKey="down_delta" name="Downside shock" radius={[3, 0, 0, 3]}>
            {chartData.map((d, i) => <Cell key={i} fill="var(--red)" fillOpacity={0.7} />)}
          </Bar>
          <Bar dataKey="up_delta" name="Upside shock" radius={[0, 3, 3, 0]}>
            {chartData.map((d, i) => <Cell key={i} fill="var(--green)" fillOpacity={0.7} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 10.5, color: "var(--ink-3)" }}>
        <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "var(--red)", marginRight: 5, opacity: 0.7 }} />Downside shock impact (shock size per factor in table below)</span>
        <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "var(--green)", marginRight: 5, opacity: 0.7 }} />Upside shock impact</span>
      </div>
      <div style={{ overflowX: "auto", marginTop: 12 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead><tr>{["Factor", "Downside Shock", "Δ Score", "Upside Shock", "Δ Score", "Swing"].map(h => (
            <th key={h} style={{ textAlign: "left", padding: "5px 8px", color: "var(--ink-4)", fontWeight: 400, fontSize: 9.5, borderBottom: "1px solid var(--line)" }}>{h}</th>
          ))}</tr></thead>
          <tbody>
            {data.rows.map(r => (
              <tr key={r.key} style={{ borderBottom: "1px solid var(--line)" }}>
                <td style={{ padding: "6px 8px", fontWeight: 500 }}>{r.label}</td>
                <td className="mono" style={{ padding: "6px 8px", color: "var(--ink-3)" }}>{r.down_shock_label}</td>
                <td className="mono" style={{ padding: "6px 8px", color: r.down_delta < 0 ? "var(--green-ink)" : "var(--red-ink)" }}>{r.down_delta >= 0 ? '+' : ''}{r.down_delta}</td>
                <td className="mono" style={{ padding: "6px 8px", color: "var(--ink-3)" }}>{r.up_shock_label}</td>
                <td className="mono" style={{ padding: "6px 8px", color: r.up_delta < 0 ? "var(--green-ink)" : "var(--red-ink)" }}>{r.up_delta >= 0 ? '+' : ''}{r.up_delta}</td>
                <td className="mono" style={{ padding: "6px 8px", fontWeight: 600 }}>{r.swing}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <MetricGuide items={_SENS_GUIDE} />
      <AssumptionsBlock items={data.assumptions} />
    </TabCard>
  );
}

// ── Tab 3: Multi-Factor Stress Test ────────────────────────────────────────

function MultiFactorStressTab({ data }) {
  if (!data) return <Empty>Run the pipeline to generate stress test data.</Empty>;
  const chartData = data.rows.map(r => ({ label: r.label, fcf_margin: r.stressed_fcf_margin_pct, breach: r.breaches_going_concern }));

  return (
    <TabCard>
      <TabHead kicker="Combined Shock Scenarios" title="Multi-Factor Stress Test"
        sub={`Base FCF margin ${data.base_fcf_margin_pct}% · going-concern trigger at ${data.breach_threshold_pct}%`} />
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 30, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 9.5, fill: 'var(--ink-3)' }} tickLine={false} axisLine={{ stroke: 'var(--line)' }} angle={-14} textAnchor="end" height={56} interval={0} />
          <YAxis tickFormatter={v => `${v}%`} tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }} tickLine={false} axisLine={false} width={42} />
          <Tooltip content={<SaTooltip formatter={v => `${v}%`} />} cursor={{ fill: 'var(--surface-2)' }} />
          <ReferenceLine y={data.breach_threshold_pct} stroke="var(--red)" strokeDasharray="4 3" strokeWidth={1.2}
            label={{ value: 'Going-concern trigger', position: 'insideTopRight', fontSize: 8.5, fill: 'var(--red-ink)', fontFamily: 'Geist Mono, monospace' }} />
          <ReferenceLine y={0} stroke="var(--line-strong)" />
          <Bar dataKey="fcf_margin" radius={[3, 3, 0, 0]}>
            {chartData.map((d, i) => <Cell key={i} fill={d.breach ? "var(--red)" : "var(--acc)"} fillOpacity={0.75} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div style={{ overflowX: "auto", marginTop: 14 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead><tr>{["Scenario", "Rev. Decline", "Margin Compression", "Revenue at Risk", "Stressed FCF Margin", "Headroom", "Status"].map(h => (
            <th key={h} style={{ textAlign: "left", padding: "5px 8px", color: "var(--ink-4)", fontWeight: 400, fontSize: 9.5, borderBottom: "1px solid var(--line)" }}>{h}</th>
          ))}</tr></thead>
          <tbody>
            {data.rows.map(r => (
              <tr key={r.id} style={{ borderBottom: "1px solid var(--line)" }}>
                <td style={{ padding: "6px 8px", fontWeight: 500 }}>{r.label}</td>
                <td className="mono" style={{ padding: "6px 8px" }}>{r.rev_decline_pts}pt</td>
                <td className="mono" style={{ padding: "6px 8px" }}>{r.margin_compression_bps}bps</td>
                <td className="mono" style={{ padding: "6px 8px" }}>${r.revenue_at_risk_m}M</td>
                <td className="mono" style={{ padding: "6px 8px" }}>{r.stressed_fcf_margin_pct}%</td>
                <td className="mono" style={{ padding: "6px 8px" }}>{r.headroom_remaining_pts}pt</td>
                <td style={{ padding: "6px 8px" }}>
                  <span style={{ fontSize: 9.5, padding: "2px 7px", borderRadius: 999, background: r.breaches_going_concern ? "var(--red-soft)" : "var(--green-soft)", color: r.breaches_going_concern ? "var(--red-ink)" : "var(--green-ink)" }}>
                    {r.breaches_going_concern ? "BREACH" : "OK"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.contributing_risks?.length > 0 && (
        <div style={{ marginTop: 12, display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: 10, color: "var(--ink-4)" }}>Top contributing risks:</span>
          {data.contributing_risks.map(r => (
            <span key={r.id} className="mono" style={{ fontSize: 9.5, padding: "2px 7px", borderRadius: 4, background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)" }}>{r.id} · {r.name}</span>
          ))}
        </div>
      )}
      <MetricGuide items={_STRESS_GUIDE} />
      <AssumptionsBlock items={data.assumptions} />
    </TabCard>
  );
}

// ── Tab 4: Liquidity / Covenant Runway ─────────────────────────────────────

function LiquidityRunwayTab({ data }) {
  if (!data) return <Empty>Run the pipeline to generate the liquidity runway.</Empty>;
  const byQ = {};
  data.scenarios.forEach(s => {
    s.points.forEach(p => {
      byQ[p.q] = byQ[p.q] || { q: `Q${p.q}` };
      byQ[p.q][s.id] = p.cash_m;
    });
  });
  const chartData = Object.values(byQ);
  const COLORS = { base: "var(--acc)", stress: "var(--amber)", severe: "var(--red)" };

  return (
    <TabCard>
      <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 14, marginBottom: 4 }}>
        <TabHead kicker="Cash Runway" title={`Liquidity & Covenant Runway · ${data.horizon_quarters}Q`}
          sub={`Starting cash ${saFmtM(data.starting_cash_m)} · base FCF margin ${data.base_fcf_margin_pct}%`} />
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
          {data.scenarios.map(s => {
            const breached = s.cash_depletion_quarter || s.covenant_breach_quarter;
            return (
              <StatTile key={s.id} label={s.label.split('—')[0].trim()}
                value={s.cash_depletion_quarter ? `Q${s.cash_depletion_quarter}` : s.covenant_breach_quarter ? `Q${s.covenant_breach_quarter}*` : "Clear"}
                sub={s.cash_depletion_quarter ? "cash depletion" : s.covenant_breach_quarter ? "*covenant breach" : `${data.horizon_quarters}Q horizon`}
                color={breached ? "var(--red-ink)" : "var(--green-ink)"} />
            );
          })}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={230}>
        <LineChart data={chartData} margin={{ top: 12, right: 16, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
          <XAxis dataKey="q" tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }} tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
          <YAxis tickFormatter={saFmtM} tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }} tickLine={false} axisLine={false} width={48} />
          <Tooltip content={<SaTooltip formatter={saFmtM} />} cursor={{ stroke: 'var(--line-strong)', strokeWidth: 1, strokeDasharray: '2 2' }} />
          <ReferenceLine y={0} stroke="var(--red)" strokeDasharray="4 3"
            label={{ value: 'Cash depleted', position: 'insideBottomRight', fontSize: 8.5, fill: 'var(--red-ink)', fontFamily: 'Geist Mono, monospace' }} />
          {data.scenarios.map(s => (
            <Line key={s.id} type="monotone" dataKey={s.id} name={s.label} stroke={COLORS[s.id] || 'var(--ink)'} strokeWidth={2}
              dot={{ r: 2.5, fill: COLORS[s.id], strokeWidth: 0 }} activeDot={{ r: 5 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <MetricGuide items={_LIQ_GUIDE} />
      <AssumptionsBlock items={data.assumptions} />
    </TabCard>
  );
}

// ── Tab 5: Composite Early-Warning Indicator ───────────────────────────────

function EarlyWarningTab({ data }) {
  if (!data) return <Empty>Run the pipeline to generate the early-warning indicator.</Empty>;
  const levelColor = { RED: "var(--red-ink)", AMBER: "var(--amber-ink)", GREEN: "var(--green-ink)" }[data.level];
  const levelSoft  = { RED: "var(--red-soft)", AMBER: "var(--amber-soft)", GREEN: "var(--green-soft)" }[data.level];
  const trendData = data.trend.map((v, i) => ({ i: i + 1, score: v }));
  const compData = data.components.map(c => ({ label: c.label, score: c.score }));

  return (
    <TabCard>
      <div style={{ display: "flex", gap: 28, flexWrap: "wrap", marginBottom: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minWidth: 160 }}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 6 }}>COMPOSITE SCORE</div>
          <div style={{ fontSize: 44, fontWeight: 800, color: levelColor, fontFamily: "var(--mono)", lineHeight: 1 }}>{data.composite_score}</div>
          <div className="mono" style={{ fontSize: 11, padding: "3px 12px", borderRadius: 999, background: levelSoft, color: levelColor, marginTop: 8, fontWeight: 600, letterSpacing: "0.05em" }}>{data.level}</div>
        </div>
        <div style={{ flex: 1, minWidth: 220 }}>
          <div className="kicker">Historical Trend</div>
          <ResponsiveContainer width="100%" height={100}>
            <LineChart data={trendData} margin={{ top: 10, right: 8, bottom: 0, left: 0 }}>
              <YAxis domain={[0, 100]} hide />
              <XAxis dataKey="i" hide />
              <Tooltip content={<SaTooltip formatter={v => `${v}/100`} />} />
              <ReferenceLine y={65} stroke="var(--red)" strokeOpacity={0.35} strokeDasharray="3 3" />
              <ReferenceLine y={40} stroke="var(--amber)" strokeOpacity={0.35} strokeDasharray="3 3" />
              <Line type="monotone" dataKey="score" stroke={levelColor} strokeWidth={2.5} dot={{ r: 3, fill: levelColor, strokeWidth: 0 }} activeDot={{ r: 5 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="kicker" style={{ marginBottom: 8 }}>Component Breakdown</div>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={compData} layout="vertical" margin={{ top: 4, right: 28, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" strokeOpacity={0.6} horizontal={false} />
          <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }} tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
          <YAxis type="category" dataKey="label" width={130} tick={{ fontSize: 10.5, fill: 'var(--ink-2)' }} tickLine={false} axisLine={false} />
          <Tooltip content={<SaTooltip formatter={v => `${v}/100`} />} cursor={{ fill: 'var(--surface-2)' }} />
          <Bar dataKey="score" radius={[0, 3, 3, 0]}>
            {compData.map((d, i) => <Cell key={i} fill={d.score >= 65 ? "var(--red)" : d.score >= 40 ? "var(--amber)" : "var(--green)"} fillOpacity={0.75} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 10 }}>
        {data.components.map(c => (
          <div key={c.key} style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 10.5, color: "var(--ink-3)" }}>
            <span>{c.label} <span className="mono" style={{ color: "var(--ink-4)" }}>({Math.round(c.weight * 100)}% weight)</span></span>
            <span className="mono" style={{ textAlign: "right" }}>{c.detail}</span>
          </div>
        ))}
      </div>
      <MetricGuide items={_EWI_GUIDE} />
      <AssumptionsBlock items={data.assumptions} />
    </TabCard>
  );
}

// ── Main screen ─────────────────────────────────────────────────────────────

const _CE_STATUS_STYLE = {
  new:        { bg: "var(--red-soft)",   ink: "var(--red-ink)",   label: "New" },
  reviewing:  { bg: "var(--amber-soft)", ink: "var(--amber-ink)", label: "Reviewing" },
  assessed:   { bg: "var(--green-soft)", ink: "var(--green-ink)", label: "Assessed" },
  dismissed:  { bg: "var(--surface-2)",  ink: "var(--ink-4)",     label: "Dismissed" },
};

const _CE_TYPE_LABEL = {
  acquisition: "Acquisition", divestiture: "Divestiture", restructuring: "Restructuring",
  bankruptcy: "Bankruptcy", impairment: "Impairment", auditor_change: "Auditor Change",
  restatement: "Restatement", change_of_control: "Change of Control",
  cybersecurity_incident: "Cybersecurity Incident", other: "Other",
};

const _CE_TYPE_COLOR = {
  acquisition: "var(--green)", divestiture: "var(--blue)", restructuring: "var(--amber)",
  bankruptcy: "var(--red)", impairment: "var(--red)", auditor_change: "var(--amber)",
  restatement: "var(--red)", change_of_control: "var(--acc)",
  cybersecurity_incident: "var(--red)", other: "var(--ink-4)",
};

// Horizontal date-axis timeline — event clustering (multiple material filings
// in a short window) is invisible in a card list but jumps out here. Click a
// dot to expand that event in the list below.
function CorporateEventsTimeline({ events, onSelect }) {
  const dated = events
    .filter(e => e.event_date)
    .map(e => ({ ...e, t: new Date(e.event_date).getTime() }))
    .sort((a, b) => a.t - b.t);

  if (dated.length < 2) return null;

  const W = 900, H = 64, PAD = 16;
  const tMin = dated[0].t, tMax = dated[dated.length - 1].t;
  const tSpan = Math.max(1, tMax - tMin);
  const x = t => PAD + ((t - tMin) / tSpan) * (W - PAD * 2);

  return (
    <div style={{ marginBottom: 10 }}>
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMinYMin meet" style={{ maxWidth: W }}>
        <line x1={PAD} x2={W - PAD} y1={H / 2} y2={H / 2} stroke="var(--line)" strokeWidth={1.5} />
        {dated.map((ev, i) => {
          const cls = ev.classification || {};
          const color = _CE_TYPE_COLOR[cls.action_type] || "var(--ink-4)";
          const isMaterialAction = cls.action_type && cls.action_type !== "none";
          return (
            <g key={ev.id ?? i} transform={`translate(${x(ev.t)},${H / 2})`} style={{ cursor: "pointer" }} onClick={() => onSelect && onSelect(ev)}>
              <line y1={0} y2={i % 2 === 0 ? -16 : 16} stroke={color} strokeWidth={1} opacity={0.5} />
              <circle r={isMaterialAction ? 5 : 3.5} fill={color} opacity={0.85} stroke="var(--surface)" strokeWidth={1.5} />
              <text y={i % 2 === 0 ? -20 : 28} textAnchor="middle" fontSize={8} fill="var(--ink-4)">
                {new Date(ev.t).toLocaleDateString(undefined, { month: "short", year: "2-digit" })}
              </text>
              <title>{`${ev.event_date} — ${_CE_TYPE_LABEL[cls.action_type] || "8-K event"}${cls.summary ? ": " + cls.summary.slice(0, 80) : ""}`}</title>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function CorporateEventRow({ event, onUpdate, saving, expanded, onToggle, highlight, users, risks, onAddAudit }) {
  const [owner, setOwner] = React.useState(event.owner || "");
  const [notes, setNotes] = React.useState(event.notes || "");
  const [auditModalOpen, setAuditModalOpen] = React.useState(false);
  const [auditAdded, setAuditAdded] = React.useState(false);
  const st = _CE_STATUS_STYLE[event.status] || _CE_STATUS_STYLE.new;
  const cls = event.classification || {};

  // The current owner may be a name that isn't (or is no longer) an active
  // user — keep it selectable rather than silently dropping it from the list.
  const ownerOptions = React.useMemo(() => {
    const names = new Set((users || []).map(u => u.display_name || u.username).filter(Boolean));
    if (owner) names.add(owner);
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [users, owner]);

  React.useEffect(() => { setOwner(event.owner || ""); }, [event.owner]);
  React.useEffect(() => { setNotes(event.notes || ""); }, [event.notes]);

  return (
    <div id={`ce-row-${event.id}`} style={{
      border: highlight ? "1px solid var(--acc)" : "1px solid var(--line)", borderRadius: 6, padding: "10px 12px",
      marginBottom: 8, background: highlight ? "var(--acc-soft)" : "var(--surface)", transition: "background .6s, border-color .6s",
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8, cursor: "pointer" }}
        onClick={onToggle}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: st.bg, color: st.ink }}>
            {st.label}
          </span>
          {cls.action_type && cls.action_type !== "none" && (
            <span style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: "var(--acc-soft)", color: "var(--acc-ink)" }}>
              {_CE_TYPE_LABEL[cls.action_type] || cls.action_type}
            </span>
          )}
          <span style={{ fontSize: 11.5, fontWeight: 600 }}>
            {cls.summary ? cls.summary.slice(0, 100) + (cls.summary.length > 100 ? "…" : "") : (Object.values(event.item_descriptions || {}).join(", ") || "8-K event")}
          </span>
        </div>
        <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", flexShrink: 0 }}>{event.event_date}</span>
      </div>

      {expanded && (
        <div style={{ marginTop: 10 }}>
          {cls.summary && <div style={{ fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.5, marginBottom: 8 }}>{cls.summary}</div>}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 10.5, marginBottom: 10 }}>
            {cls.counterparty && <div><b>Counterparty:</b> {cls.counterparty}</div>}
            {cls.consideration && <div><b>Consideration:</b> {cls.consideration}</div>}
            {cls.assets_or_business_description && <div style={{ gridColumn: "1 / -1" }}><b>Assets/business:</b> {cls.assets_or_business_description}</div>}
            {cls.expected_close_or_effective_date && <div><b>Effective date:</b> {cls.expected_close_or_effective_date}</div>}
            {cls.rationale && <div style={{ gridColumn: "1 / -1" }}><b>Stated rationale:</b> {cls.rationale}</div>}
          </div>
          {cls.suggested_risk_note && (
            <div style={{
              fontSize: 10.5, lineHeight: 1.5, padding: "8px 10px", borderRadius: 6, marginBottom: 10,
              background: "var(--acc-soft)", color: "var(--acc-ink)", borderLeft: "3px solid var(--acc)",
            }}>
              <b>Suggested next step:</b> {cls.suggested_risk_note}
            </div>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 8, marginBottom: 8 }}>
            <select className="code-input" style={{ fontSize: 11 }}
              value={owner}
              onChange={e => { setOwner(e.target.value); onUpdate(event.id, { owner: e.target.value }); }}
              onClick={e => e.stopPropagation()}>
              <option value="">Owner (unassigned)</option>
              {ownerOptions.map(name => <option key={name} value={name}>{name}</option>)}
            </select>
            <input className="code-input" style={{ fontSize: 11 }} placeholder="Notes / assessment"
              value={notes} onChange={e => setNotes(e.target.value)}
              onBlur={() => notes !== (event.notes || "") && onUpdate(event.id, { notes })}
              onClick={e => e.stopPropagation()} />
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }} onClick={e => e.stopPropagation()}>
            {event.status !== "reviewing" && event.status !== "assessed" && event.status !== "dismissed" && (
              <button className="btn btn-sm" disabled={saving} onClick={() => onUpdate(event.id, { status: "reviewing" })}>Start Review</button>
            )}
            {event.status !== "assessed" && (
              <button className="btn btn-sm btn-acc" disabled={saving} onClick={() => onUpdate(event.id, { status: "assessed" })}>Mark Assessed</button>
            )}
            {event.status !== "dismissed" && (
              <button className="btn btn-sm" disabled={saving} onClick={() => onUpdate(event.id, { status: "dismissed" })}>Dismiss</button>
            )}
            {onAddAudit && (
              <button className="btn btn-sm" onClick={() => { setAuditModalOpen(true); setAuditAdded(false); }}>
                <Icon name="plus" size={10}/> Add Audit
              </button>
            )}
            {auditAdded && (
              <span className="mono" style={{ fontSize: 10, color: "var(--green-ink)" }}>✓ Added to Individual Audits</span>
            )}
          </div>
        </div>
      )}

      {auditModalOpen && (
        <AddAuditFromEventModal
          event={event}
          risks={risks || []}
          onClose={() => setAuditModalOpen(false)}
          onSubmit={(audit) => {
            onAddAudit(audit);
            setAuditModalOpen(false);
            setAuditAdded(true);
          }}
        />
      )}
    </div>
  );
}

const _CE_AUDIT_QUARTERS = ["Q3 2026", "Q4 2026", "Q1 2027", "Q2 2027", "Q3 2027", "Q4 2027"];

// Same shape/visual language as pipeline.jsx's AddAuditModal (reuses its
// "ar-*"/"s3-*" CSS classes for consistency) but with an optional, not
// required, linked risk — a corporate event is often worth auditing before
// it has an existing scored risk to attach to, unlike the Stage 3 flow this
// otherwise mirrors. Submits into the same manualAudits list (onAddAudit =
// app.jsx's addManualAudit), so it shows up in Stage 3 "Individual audits"
// and rides the same end-of-run persistence as any other manual audit.
function AddAuditFromEventModal({ event, risks, onClose, onSubmit }) {
  const cls = event.classification || {};
  const [title, setTitle] = React.useState(
    cls.summary ? `Assess corporate event: ${cls.summary.slice(0, 80)}` : `Assess corporate event — ${event.event_date}`
  );
  const [riskId, setRiskId] = React.useState("");
  const [when, setWhen] = React.useState(_CE_AUDIT_QUARTERS[0]);
  const [reduction, setReduction] = React.useState(20);

  const selectedRisk = risks.find(r => r.id === riskId);
  const baseScore = selectedRisk?.score ?? 0;
  const residualScore = selectedRisk ? parseFloat(Math.max(0, baseScore * (1 - reduction / 100)).toFixed(1)) : null;
  const valid = title.trim().length >= 5;

  return (
    <div className="modal open" onClick={(e) => { if (e.target.classList.contains("modal")) onClose(); }}>
      <div className="modal-box" style={{ width: 560 }}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Add Audit from Corporate Event</div>
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 3 }}>
              {event.event_date} · {_CE_TYPE_LABEL[cls.action_type] || "8-K event"}
            </div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12}/></button>
        </div>

        <div className="modal-body">
          <div className="ar-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <div className="ar-field" style={{ gridColumn: "1 / -1" }}>
              <label className="ar-label">Audit Objective</label>
              <input type="text" value={title} onChange={e => setTitle(e.target.value)} className="fi-input" />
            </div>

            <div className="ar-field" style={{ gridColumn: "1 / -1" }}>
              <label className="ar-label">Linked Risk <span className="mono muted" style={{ fontWeight: 400 }}>(optional)</span></label>
              <select value={riskId} onChange={e => setRiskId(e.target.value)} className="s3-risk-sel" disabled={!risks.length}>
                <option value="">{risks.length ? "— None —" : "— No scored risks yet (run the pipeline) —"}</option>
                {[...risks].sort((a, b) => b.score - a.score).map(r => (
                  <option key={r.id} value={r.id}>{r.id} · {r.name} · score {fmt2(r.score)} ({r.rag})</option>
                ))}
              </select>
            </div>

            <div className="ar-field">
              <label className="ar-label">Scheduled Period</label>
              <div className="ar-ce-row" style={{ flexWrap: "wrap", gap: 4 }}>
                {_CE_AUDIT_QUARTERS.map(q => (
                  <button key={q} className={`ar-ce-opt ${when === q ? "active" : ""}`} onClick={() => setWhen(q)}>{q}</button>
                ))}
              </div>
            </div>

            {selectedRisk && (
              <div className="ar-field">
                <label className="ar-label">
                  Anticipated Risk Reduction <span className="mono ar-val">{reduction}%</span>
                </label>
                <input type="range" min="5" max="80" step="5" value={reduction}
                  onChange={e => setReduction(parseInt(e.target.value))} className="ar-slider" />
              </div>
            )}
          </div>

          {selectedRisk && (
            <div className="s3-residual-preview">
              <div className="s3-res-label mono">Residual Risk Projection</div>
              <div className="s3-res-row">
                <div className="s3-res-item">
                  <div className="l">Current Score</div>
                  <div className="v mono" style={{ color: scoreColorInk(baseScore) }}>{fmt2(baseScore)}</div>
                </div>
                <div className="s3-res-arrow">→</div>
                <div className="s3-res-item">
                  <div className="l">Residual Score</div>
                  <div className="v mono" style={{ color: scoreColorInk(residualScore) }}>{fmt2(residualScore)}</div>
                </div>
                <div className="s3-res-item">
                  <div className="l">Score Reduction</div>
                  <div className="v mono" style={{ color: "var(--green-ink)" }}>−{fmt2(baseScore - residualScore)}</div>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="modal-foot">
          <span className="muted mono" style={{ fontSize: 11 }}>
            {!valid ? "Audit objective must be at least 5 characters" : "Ready to add to plan"}
          </span>
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!valid}
              onClick={() => onSubmit({
                id: `MA-${Date.now().toString(36).toUpperCase()}`,
                riskId: riskId || null,
                riskName: selectedRisk?.name || null,
                when,
                title: title.trim(),
                reduction: selectedRisk ? reduction : 0,
                baseScore: selectedRisk ? baseScore : null,
                residualScore,
                sourceEventId: event.id,
              })}>
              <Icon name="plus" size={10}/> Add to Plan
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function CorporateEventsPanel({ ticker, risks, onAddAudit }) {
  const [events, setEvents] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [savingId, setSavingId] = React.useState(null);
  const [showClosed, setShowClosed] = React.useState(false);
  const [checking, setChecking] = React.useState(false);
  const [checkResult, setCheckResult] = React.useState(null); // { newCount } | { error }
  const [lastChecked, setLastChecked] = React.useState(null);
  const [expandedIds, setExpandedIds] = React.useState(new Set());
  const [highlightId, setHighlightId] = React.useState(null);
  const [users, setUsers] = React.useState([]);

  // Any authenticated user can list this (see auth_endpoints.py) — powers
  // the Owner dropdown below, same source the account-menu manager picker uses.
  React.useEffect(() => {
    fetch("/auth/users", { credentials: "include" })
      .then(r => r.ok ? r.json() : { users: [] })
      .then(d => setUsers(d.users || []))
      .catch(() => {});
  }, []);

  function selectFromTimeline(ev) {
    setExpandedIds(prev => new Set(prev).add(ev.id));
    setHighlightId(ev.id);
    setTimeout(() => {
      document.getElementById(`ce-row-${ev.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 30);
    setTimeout(() => setHighlightId(null), 2000);
  }

  const load = React.useCallback(() => {
    if (!ticker) { setLoading(false); return Promise.resolve(); }
    return fetch(`/api/mcp/edgar/corporate-events?ticker=${encodeURIComponent(ticker)}`, { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setEvents(d.rows || []); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [ticker]);

  React.useEffect(() => { load(); }, [load]);

  async function handleUpdate(id, fields) {
    setSavingId(id);
    setEvents(rows => rows.map(r => r.id === id ? { ...r, ...fields } : r));
    try {
      await fetch(`/api/mcp/edgar/corporate-events/${id}`, {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields),
      });
    } catch (_) {}
    await load();
    setSavingId(null);
  }

  // Runs the real 8-K fetch + classification directly, regardless of Data
  // Mode (Mock/Live/MCP) — the pipeline only calls this in Live/MCP mode, so
  // this is the way to check for new filings without switching modes and
  // re-running the whole pipeline.
  async function handleCheckNow() {
    if (!ticker) return;
    setChecking(true);
    setCheckResult(null);
    try {
      const res = await fetch("/api/mcp/edgar/8k-events", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker }),
      });
      const d = await res.json().catch(() => null);
      if (!res.ok) {
        setCheckResult({ error: d?.detail || `Check failed (HTTP ${res.status})` });
      } else {
        setCheckResult({ newCount: (d.new_material_events || []).length });
        await load();
      }
    } catch (e) {
      setCheckResult({ error: e.message || "Network error" });
    }
    setLastChecked(new Date());
    setChecking(false);
  }

  if (!ticker) return null;

  const visible = showClosed ? events : events.filter(e => e.status !== "assessed" && e.status !== "dismissed");
  const newCount = events.filter(e => e.status === "new").length;

  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6, flexWrap: "wrap", gap: 8 }}>
        <div className="kicker">
          Corporate Events{newCount > 0 && (
            <span className="mono" style={{ marginLeft: 8, fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: "var(--red-soft)", color: "var(--red-ink)" }}>
              {newCount} NEW
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10.5, color: "var(--ink-3)", cursor: "pointer" }}>
            <input type="checkbox" checked={showClosed} onChange={e => setShowClosed(e.target.checked)} />
            Show assessed/dismissed
          </label>
          <button className="btn btn-sm" disabled={checking} onClick={handleCheckNow}
            title="Fetch and classify this company's 8-K filings right now, independent of Data Mode">
            {checking ? "Checking…" : "Check for new filings"}
          </button>
        </div>
      </div>
      <div style={{ fontSize: 10, color: "var(--ink-4)", marginBottom: 10 }}>
        Material 8-K filings — acquisitions, divestitures, restructuring, impairments, restatements, change of
        control — detected from real SEC filing content, not just item-code labels. These are exactly the kind of
        discrete, event-driven signals the scenario models below don't capture on their own; review each one and
        assess whether it changes the risk picture. Populated automatically when the pipeline runs in Live or MCP
        data mode — use "Check for new filings" to pull the latest without a full pipeline run.
      </div>
      {checkResult && (
        <div className="mono" style={{
          fontSize: 10.5, padding: "6px 10px", borderRadius: 6, marginBottom: 10,
          background: checkResult.error ? "var(--red-soft)" : "var(--surface-2)",
          color: checkResult.error ? "var(--red-ink)" : "var(--ink-2)",
        }}>
          {checkResult.error
            ? `Check failed: ${checkResult.error}`
            : checkResult.newCount > 0
              ? `✓ Found ${checkResult.newCount} new material event${checkResult.newCount === 1 ? "" : "s"}`
              : `✓ Checked — no new material filings since last check${lastChecked ? ` (${lastChecked.toLocaleTimeString()})` : ""}`}
        </div>
      )}
      {loading ? <Empty>Loading…</Empty> : !visible.length ? (
        <Empty>{events.length ? "No open corporate events — everything's been assessed or dismissed." : "No material corporate events detected yet — click \"Check for new filings\" to pull real EDGAR data for this company."}</Empty>
      ) : (
        <>
          <CorporateEventsTimeline events={visible} onSelect={selectFromTimeline} />
          {visible.map(ev => (
            <CorporateEventRow key={ev.id} event={ev} onUpdate={handleUpdate} saving={savingId === ev.id}
              expanded={expandedIds.has(ev.id)} highlight={highlightId === ev.id}
              users={users} risks={risks} onAddAudit={onAddAudit}
              onToggle={() => setExpandedIds(prev => {
                const next = new Set(prev);
                next.has(ev.id) ? next.delete(ev.id) : next.add(ev.id);
                return next;
              })} />
          ))}
        </>
      )}
    </div>
  );
}

function ScenarioAnalysisScreen({ ticker, hasRun, varCvar, sensitivity, multiFactorStress, liquidityRunway, earlyWarning, risks, onAddAudit }) {
  const [activeTab, setActiveTab] = React.useState("var");
  const hasData = !!(varCvar || sensitivity || multiFactorStress || liquidityRunway || earlyWarning);

  return (
    <div className="scope-screen" data-screen-label="Scenario Analysis">
      <div className="panel-head">
        <div>
          <div className="kicker">Risk Intelligence · Quantitative</div>
          <div className="panel-title mt-8">Scenario Analysis</div>
          <div className="panel-sub">
            Tail-risk, sensitivity, combined stress, liquidity runway, and a composite early-warning signal —
            all derived from the live risk register and financial ratios for {ticker ? ticker.toUpperCase() : "the current company"}.
          </div>
        </div>
      </div>

      <CorporateEventsPanel ticker={ticker} risks={risks || []} onAddAudit={onAddAudit} />

      <div className="pipe-sub-tabs" style={{ marginTop: 4 }}>
        {SCEN_TABS.map(t => (
          <button key={t.id} className={"pipe-sub-tab" + (activeTab === t.id ? " active" : "")} onClick={() => setActiveTab(t.id)}>
            {t.l}
          </button>
        ))}
      </div>

      <div style={{ padding: "18px 2px" }}>
        {!hasData ? (
          <Empty>Run the pipeline from Assess Enterprise Risk to populate scenario analysis.</Empty>
        ) : (
          <>
            {activeTab === "var" && <VarCvarTab data={varCvar} />}
            {activeTab === "sensitivity" && <SensitivityTab data={sensitivity} />}
            {activeTab === "stress" && <MultiFactorStressTab data={multiFactorStress} />}
            {activeTab === "liquidity" && <LiquidityRunwayTab data={liquidityRunway} />}
            {activeTab === "ewi" && <EarlyWarningTab data={earlyWarning} />}
          </>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { ScenarioAnalysisScreen });
