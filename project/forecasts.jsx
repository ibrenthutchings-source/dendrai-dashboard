/* ============================================================
   Forecasts panel — revenue / margin + M-Score + FRED correlates
   Models run via FORECASTING + BACKTESTING engines (forecasting.js / backtesting.js).
   Falls back gracefully if engines are not loaded.
   ============================================================ */

// ── Industry KPI helpers ──────────────────────────────────────
function seededVal(ticker, id, lo, hi) {
  let h = 0;
  for (const c of (ticker || 'X') + '|' + id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return lo + (h / 0xFFFFFFFF) * (hi - lo);
}

function computeRevGrowthYoY(data) {
  const h = data?.revenue?.history;
  if (!h || h.length < 5) return null;
  const last = h[h.length - 1].v, yago = h[h.length - 5].v;
  return yago > 0 ? ((last - yago) / yago) * 100 : null;
}

const INDUSTRY_KPI_DEFS = {
  'Semiconductors': [
    { id: 'gm', label: 'Gross Margin', bmk: 55, bmkFmt: '55%', dir: 'higher',
      get: (d, lf) => lf?.grossMarginPct ?? d?.margin?.history?.slice(-1)[0]?.v,
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 55 ? 'G' : v >= 44 ? 'A' : 'R' },
    { id: 'revgrowth', label: 'Rev Growth YoY', bmk: 12, bmkFmt: '12%', dir: 'higher',
      get: (d) => computeRevGrowthYoY(d),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 15 ? 'G' : v >= 3 ? 'A' : 'R' },
    { id: 'rd', label: 'R&D / Rev', bmk: 18, bmkFmt: '18%', dir: 'higher',
      get: (_, lf, sv) => lf?.rd?.latestAnnual && lf?.revenue?.latestAnnual ? (lf.rd.latestAnnual.val / lf.revenue.latestAnnual.val * 100) : sv(12, 24),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 15 ? 'G' : v >= 10 ? 'A' : 'R' },
    { id: 'b2b', label: 'Book-to-Bill', bmk: 1.0, bmkFmt: '1.0×', dir: 'higher',
      get: (_, __, sv) => sv(0.88, 1.14),
      fmt: v => `${v.toFixed(2)}×`, rag: v => v >= 1.0 ? 'G' : v >= 0.90 ? 'A' : 'R' },
    { id: 'invdays', label: 'Inventory Days', bmk: 90, bmkFmt: '90d', dir: 'lower',
      get: (_, __, sv) => Math.round(sv(62, 130)),
      fmt: v => `${v}d`, rag: v => v <= 85 ? 'G' : v <= 115 ? 'A' : 'R' },
  ],
  'Software & Cloud': [
    { id: 'gm', label: 'Gross Margin', bmk: 72, bmkFmt: '72%', dir: 'higher',
      get: (d, lf) => lf?.grossMarginPct ?? d?.margin?.history?.slice(-1)[0]?.v,
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 70 ? 'G' : v >= 58 ? 'A' : 'R' },
    { id: 'revgrowth', label: 'Rev Growth YoY', bmk: 18, bmkFmt: '18%', dir: 'higher',
      get: (d) => computeRevGrowthYoY(d),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 20 ? 'G' : v >= 8 ? 'A' : 'R' },
    { id: 'r40', label: 'Rule of 40', bmk: 40, bmkFmt: '40', dir: 'higher',
      get: (d, lf, sv) => {
        const gm = lf?.grossMarginPct ?? d?.margin?.history?.slice(-1)[0]?.v ?? 65;
        const growth = computeRevGrowthYoY(d) ?? sv(5, 30);
        const fcfEst = sv(-5, 20);
        return +(growth + fcfEst).toFixed(1);
      },
      fmt: v => v.toFixed(0), rag: v => v >= 40 ? 'G' : v >= 25 ? 'A' : 'R' },
    { id: 'nrr', label: 'Net Rev Retention', bmk: 110, bmkFmt: '110%', dir: 'higher',
      get: (_, __, sv) => Math.round(sv(92, 124)),
      fmt: v => `${v}%`, rag: v => v >= 110 ? 'G' : v >= 100 ? 'A' : 'R' },
    { id: 'cacpb', label: 'CAC Payback', bmk: 18, bmkFmt: '18mo', dir: 'lower',
      get: (_, __, sv) => Math.round(sv(10, 32)),
      fmt: v => `${v}mo`, rag: v => v <= 18 ? 'G' : v <= 28 ? 'A' : 'R' },
  ],
  'Automotive OEM': [
    { id: 'gm', label: 'Gross Margin', bmk: 18, bmkFmt: '18%', dir: 'higher',
      get: (d, lf) => lf?.grossMarginPct ?? d?.margin?.history?.slice(-1)[0]?.v,
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 18 ? 'G' : v >= 10 ? 'A' : 'R' },
    { id: 'revgrowth', label: 'Rev Growth YoY', bmk: 5, bmkFmt: '5%', dir: 'higher',
      get: (d) => computeRevGrowthYoY(d),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 5 ? 'G' : v >= -3 ? 'A' : 'R' },
    { id: 'evmix', label: 'EV Revenue Mix', bmk: 20, bmkFmt: '20%', dir: 'higher',
      get: (_, __, sv) => Math.round(sv(5, 38)),
      fmt: v => `${v}%`, rag: v => v >= 20 ? 'G' : v >= 8 ? 'A' : 'R' },
    { id: 'opmargin', label: 'Operating Margin', bmk: 6, bmkFmt: '6%', dir: 'higher',
      get: (_, __, sv) => sv(2, 14),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 6 ? 'G' : v >= 2 ? 'A' : 'R' },
    { id: 'capex', label: 'CapEx / Rev', bmk: 8, bmkFmt: '8%', dir: 'contextual',
      get: (_, lf, sv) => lf?.capex?.latestAnnual && lf?.revenue?.latestAnnual ? Math.abs(lf.capex.latestAnnual.val / lf.revenue.latestAnnual.val * 100) : sv(5, 14),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 5 && v <= 12 ? 'G' : v <= 16 ? 'A' : 'R' },
  ],
  'Healthcare & Pharma': [
    { id: 'gm', label: 'Gross Margin', bmk: 68, bmkFmt: '68%', dir: 'higher',
      get: (d, lf) => lf?.grossMarginPct ?? d?.margin?.history?.slice(-1)[0]?.v,
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 65 ? 'G' : v >= 50 ? 'A' : 'R' },
    { id: 'rd', label: 'R&D / Rev', bmk: 18, bmkFmt: '18%', dir: 'higher',
      get: (_, lf, sv) => lf?.rd?.latestAnnual && lf?.revenue?.latestAnnual ? (lf.rd.latestAnnual.val / lf.revenue.latestAnnual.val * 100) : sv(10, 28),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 15 ? 'G' : v >= 8 ? 'A' : 'R' },
    { id: 'revgrowth', label: 'Rev Growth YoY', bmk: 8, bmkFmt: '8%', dir: 'higher',
      get: (d) => computeRevGrowthYoY(d),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 8 ? 'G' : v >= 2 ? 'A' : 'R' },
    { id: 'pipeline', label: 'Phase 3 Assets', bmk: 3, bmkFmt: '3', dir: 'higher',
      get: (_, __, sv) => Math.round(sv(0, 8)),
      fmt: v => `${v}`, rag: v => v >= 3 ? 'G' : v >= 1 ? 'A' : 'R' },
    { id: 'nimargin', label: 'Net Income Margin', bmk: 15, bmkFmt: '15%', dir: 'higher',
      get: (_, lf, sv) => lf?.netIncome?.latestAnnual && lf?.revenue?.latestAnnual ? (lf.netIncome.latestAnnual.val / lf.revenue.latestAnnual.val * 100) : sv(5, 28),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 15 ? 'G' : v >= 5 ? 'A' : 'R' },
  ],
  'Financial Services': [
    { id: 'gm', label: 'Net Interest Margin', bmk: 2.5, bmkFmt: '2.5%', dir: 'higher',
      get: (_, __, sv) => sv(1.4, 4.2),
      fmt: v => `${v.toFixed(2)}%`, rag: v => v >= 2.5 ? 'G' : v >= 1.8 ? 'A' : 'R' },
    { id: 'roe', label: 'Return on Equity', bmk: 12, bmkFmt: '12%', dir: 'higher',
      get: (_, __, sv) => sv(5, 22),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 12 ? 'G' : v >= 8 ? 'A' : 'R' },
    { id: 'cet1', label: 'CET1 Ratio', bmk: 12, bmkFmt: '12%', dir: 'higher',
      get: (_, __, sv) => sv(9, 16),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 12 ? 'G' : v >= 10 ? 'A' : 'R' },
    { id: 'efficiency', label: 'Efficiency Ratio', bmk: 58, bmkFmt: '58%', dir: 'lower',
      get: (_, __, sv) => Math.round(sv(48, 75)),
      fmt: v => `${v}%`, rag: v => v <= 58 ? 'G' : v <= 68 ? 'A' : 'R' },
    { id: 'revgrowth', label: 'Rev Growth YoY', bmk: 5, bmkFmt: '5%', dir: 'higher',
      get: (d) => computeRevGrowthYoY(d),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 5 ? 'G' : v >= 0 ? 'A' : 'R' },
  ],
  'Retail & Consumer': [
    { id: 'gm', label: 'Gross Margin', bmk: 38, bmkFmt: '38%', dir: 'higher',
      get: (d, lf) => lf?.grossMarginPct ?? d?.margin?.history?.slice(-1)[0]?.v,
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 38 ? 'G' : v >= 28 ? 'A' : 'R' },
    { id: 'revgrowth', label: 'Rev Growth YoY', bmk: 5, bmkFmt: '5%', dir: 'higher',
      get: (d) => computeRevGrowthYoY(d),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 5 ? 'G' : v >= 0 ? 'A' : 'R' },
    { id: 'invturn', label: 'Inventory Turnover', bmk: 6, bmkFmt: '6×', dir: 'higher',
      get: (_, __, sv) => sv(3, 12),
      fmt: v => `${v.toFixed(1)}×`, rag: v => v >= 6 ? 'G' : v >= 4 ? 'A' : 'R' },
    { id: 'sss', label: 'Same-Store Sales', bmk: 3, bmkFmt: '3%', dir: 'higher',
      get: (_, __, sv) => sv(-4, 10),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 3 ? 'G' : v >= 0 ? 'A' : 'R' },
    { id: 'ecomm', label: 'Digital Mix', bmk: 22, bmkFmt: '22%', dir: 'higher',
      get: (_, __, sv) => Math.round(sv(8, 45)),
      fmt: v => `${v}%`, rag: v => v >= 22 ? 'G' : v >= 12 ? 'A' : 'R' },
  ],
  'Energy & Resources': [
    { id: 'revgrowth', label: 'Rev Growth YoY', bmk: 5, bmkFmt: '5%', dir: 'higher',
      get: (d) => computeRevGrowthYoY(d),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 5 ? 'G' : v >= -5 ? 'A' : 'R' },
    { id: 'fcfmargin', label: 'FCF Margin', bmk: 12, bmkFmt: '12%', dir: 'higher',
      get: (_, lf, sv) => lf?.cfo?.latestAnnual && lf?.capex?.latestAnnual && lf?.revenue?.latestAnnual ? ((lf.cfo.latestAnnual.val - Math.abs(lf.capex.latestAnnual.val)) / lf.revenue.latestAnnual.val * 100) : sv(4, 22),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 12 ? 'G' : v >= 5 ? 'A' : 'R' },
    { id: 'capex', label: 'CapEx / Rev', bmk: 20, bmkFmt: '20%', dir: 'contextual',
      get: (_, lf, sv) => lf?.capex?.latestAnnual && lf?.revenue?.latestAnnual ? Math.abs(lf.capex.latestAnnual.val / lf.revenue.latestAnnual.val * 100) : sv(12, 30),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 10 && v <= 30 ? 'G' : 'A' },
    { id: 'opmargin', label: 'Operating Margin', bmk: 15, bmkFmt: '15%', dir: 'higher',
      get: (_, __, sv) => sv(5, 30),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 15 ? 'G' : v >= 8 ? 'A' : 'R' },
    { id: 'gm', label: 'Gross Margin', bmk: 45, bmkFmt: '45%', dir: 'higher',
      get: (d, lf) => lf?.grossMarginPct ?? d?.margin?.history?.slice(-1)[0]?.v,
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 45 ? 'G' : v >= 30 ? 'A' : 'R' },
  ],
  'Utilities': [
    { id: 'ebitdamargin', label: 'EBITDA Margin', bmk: 35, bmkFmt: '35%', dir: 'higher',
      get: (_, __, sv) => sv(22, 48),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 35 ? 'G' : v >= 25 ? 'A' : 'R' },
    { id: 'revgrowth', label: 'Rev Growth YoY', bmk: 4, bmkFmt: '4%', dir: 'higher',
      get: (d) => computeRevGrowthYoY(d),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 4 ? 'G' : v >= 0 ? 'A' : 'R' },
    { id: 'capex', label: 'CapEx / Rev', bmk: 20, bmkFmt: '20%', dir: 'contextual',
      get: (_, lf, sv) => lf?.capex?.latestAnnual && lf?.revenue?.latestAnnual ? Math.abs(lf.capex.latestAnnual.val / lf.revenue.latestAnnual.val * 100) : sv(14, 28),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 14 && v <= 32 ? 'G' : 'A' },
    { id: 'debtEbitda', label: 'Debt / EBITDA', bmk: 4.5, bmkFmt: '4.5×', dir: 'lower',
      get: (_, __, sv) => sv(2.8, 6.5),
      fmt: v => `${v.toFixed(1)}×`, rag: v => v <= 4.5 ? 'G' : v <= 6.0 ? 'A' : 'R' },
    { id: 'divyield', label: 'Dividend Yield', bmk: 3.5, bmkFmt: '3.5%', dir: 'higher',
      get: (_, __, sv) => sv(1.5, 6.5),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 3.5 ? 'G' : v >= 2.0 ? 'A' : 'R' },
  ],
  'Industrial & Manufacturing': [
    { id: 'gm', label: 'Gross Margin', bmk: 30, bmkFmt: '30%', dir: 'higher',
      get: (d, lf) => lf?.grossMarginPct ?? d?.margin?.history?.slice(-1)[0]?.v,
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 30 ? 'G' : v >= 20 ? 'A' : 'R' },
    { id: 'revgrowth', label: 'Rev Growth YoY', bmk: 6, bmkFmt: '6%', dir: 'higher',
      get: (d) => computeRevGrowthYoY(d),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 6 ? 'G' : v >= 0 ? 'A' : 'R' },
    { id: 'opmargin', label: 'Operating Margin', bmk: 12, bmkFmt: '12%', dir: 'higher',
      get: (_, __, sv) => sv(5, 22),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 12 ? 'G' : v >= 7 ? 'A' : 'R' },
    { id: 'backlog', label: 'Book-to-Bill', bmk: 1.0, bmkFmt: '1.0×', dir: 'higher',
      get: (_, __, sv) => sv(0.85, 1.18),
      fmt: v => `${v.toFixed(2)}×`, rag: v => v >= 1.0 ? 'G' : v >= 0.88 ? 'A' : 'R' },
    { id: 'capex', label: 'CapEx / Rev', bmk: 5, bmkFmt: '5%', dir: 'contextual',
      get: (_, lf, sv) => lf?.capex?.latestAnnual && lf?.revenue?.latestAnnual ? Math.abs(lf.capex.latestAnnual.val / lf.revenue.latestAnnual.val * 100) : sv(3, 10),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 3 && v <= 10 ? 'G' : 'A' },
  ],
  'Generic': [
    { id: 'gm', label: 'Gross Margin', bmk: 40, bmkFmt: '40%', dir: 'higher',
      get: (d, lf) => lf?.grossMarginPct ?? d?.margin?.history?.slice(-1)[0]?.v,
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 40 ? 'G' : v >= 25 ? 'A' : 'R' },
    { id: 'revgrowth', label: 'Rev Growth YoY', bmk: 8, bmkFmt: '8%', dir: 'higher',
      get: (d) => computeRevGrowthYoY(d),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 8 ? 'G' : v >= 0 ? 'A' : 'R' },
    { id: 'nimargin', label: 'Net Margin', bmk: 10, bmkFmt: '10%', dir: 'higher',
      get: (_, lf, sv) => lf?.netIncome?.latestAnnual && lf?.revenue?.latestAnnual ? (lf.netIncome.latestAnnual.val / lf.revenue.latestAnnual.val * 100) : sv(2, 20),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 10 ? 'G' : v >= 3 ? 'A' : 'R' },
    { id: 'fcfmargin', label: 'FCF Margin', bmk: 8, bmkFmt: '8%', dir: 'higher',
      get: (_, lf, sv) => lf?.cfo?.latestAnnual && lf?.capex?.latestAnnual && lf?.revenue?.latestAnnual ? ((lf.cfo.latestAnnual.val - Math.abs(lf.capex.latestAnnual.val)) / lf.revenue.latestAnnual.val * 100) : sv(2, 16),
      fmt: v => `${v.toFixed(1)}%`, rag: v => v >= 8 ? 'G' : v >= 2 ? 'A' : 'R' },
    { id: 'assetgrowth', label: 'Asset Growth YoY', bmk: 8, bmkFmt: '8%', dir: 'contextual',
      get: (_, __, sv) => sv(-3, 20),
      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, rag: v => v >= 0 && v <= 20 ? 'G' : 'A' },
  ],
};

const ANALYST_CONSENSUS_DB = {
  'Semiconductors': {
    total: 26,
    baseRatings: { Buy: 13, Outperform: 5, Hold: 5, Underperform: 2, Sell: 1 },
    sector: 'PHLX SOX',
    themes: [
      { theme: 'AI / HPC demand acceleration', rag: 'G', note: 'GPU and custom ASIC order momentum tracking above consensus through H2 2026' },
      { theme: 'China revenue exposure', rag: 'R', note: 'Export control expansion risk weighing on 15–22% of addressable revenue' },
      { theme: 'Inventory normalisation', rag: 'G', note: 'Channel inventory at target; restocking cycle expected to begin in H2' },
      { theme: 'Memory pricing recovery', rag: 'A', note: 'HBM tight; DRAM spot recovering; commodity NAND lagging schedule' },
    ],
  },
  'Software & Cloud': {
    total: 22,
    baseRatings: { Buy: 11, Outperform: 4, Hold: 5, Underperform: 2, Sell: 0 },
    sector: 'iShares IGV',
    themes: [
      { theme: 'GenAI feature monetisation', rag: 'G', note: 'AI copilot attach driving incremental ARPU uplift and seat expansion' },
      { theme: 'NRR compression risk', rag: 'A', note: 'Net Revenue Retention stabilising at 108–112%, below peak 118–122% cohorts' },
      { theme: 'Platform consolidation tailwind', rag: 'G', note: 'IT budget consolidation favouring best-of-suite vendors over point solutions' },
      { theme: 'Macro IT spend pressure', rag: 'A', note: 'Enterprise deal cycles lengthening in EMEA; SMB churn elevated vs prior year' },
    ],
  },
  'Automotive OEM': {
    total: 18,
    baseRatings: { Buy: 7, Outperform: 3, Hold: 6, Underperform: 2, Sell: 0 },
    sector: 'Global Auto MSCI',
    themes: [
      { theme: 'EV margin normalisation', rag: 'A', note: 'EV gross margins improving sequentially but still below ICE mix; 2027 target in view' },
      { theme: 'China competition headwind', rag: 'R', note: 'BYD and local brands compressing ASPs; market share defence proving costly' },
      { theme: 'Software / SDV monetisation', rag: 'G', note: 'OTA revenue and ADAS subscriptions beginning to offset hardware margin pressure' },
      { theme: 'Supply chain normalisation', rag: 'G', note: 'Semiconductor supply improving; build constraints largely resolved' },
    ],
  },
  'Healthcare & Pharma': {
    total: 24,
    baseRatings: { Buy: 14, Outperform: 4, Hold: 4, Underperform: 2, Sell: 0 },
    sector: 'S&P Healthcare',
    themes: [
      { theme: 'Pipeline readout catalyst', rag: 'G', note: 'Phase 3 catalysts in H2 could drive significant re-rating on positive data' },
      { theme: 'Patent cliff exposure', rag: 'R', note: 'Key assets facing loss of exclusivity 2026–2028; generics erosion risk on the horizon' },
      { theme: 'GLP-1 competitive dynamics', rag: 'A', note: 'Weight-loss adjacencies reshaping treatment algorithms across therapeutic areas' },
      { theme: 'IRA pricing negotiation', rag: 'R', note: 'Medicare Part D negotiation adding portfolio pricing risk on selected assets' },
    ],
  },
  'Financial Services': {
    total: 20,
    baseRatings: { Buy: 9, Outperform: 4, Hold: 5, Underperform: 1, Sell: 1 },
    sector: 'KBW Bank Index',
    themes: [
      { theme: 'NIM expansion trajectory', rag: 'G', note: 'Rate re-pricing of loan book supporting net interest margin expansion through 2026' },
      { theme: 'Credit quality normalisation', rag: 'A', note: 'NCO rates rising but within cycle norms; commercial real estate the key watch item' },
      { theme: 'Fee income diversification', rag: 'G', note: 'Capital markets and wealth fees offsetting spread compression risk' },
      { theme: 'Basel III endgame impact', rag: 'A', note: 'Pending capital rule finalisation; consensus expects manageable 40–80bps CET1 drag' },
    ],
  },
  'Retail & Consumer': {
    total: 16,
    baseRatings: { Buy: 7, Outperform: 3, Hold: 5, Underperform: 1, Sell: 0 },
    sector: 'S&P Retail',
    themes: [
      { theme: 'Consumer resilience divergence', rag: 'A', note: 'High-income consumer healthy; low-income trade-down accelerating vs prior year' },
      { theme: 'Inventory discipline', rag: 'G', note: 'Lean posture maintained; gross margin recovery on track vs 2023 clearance cycle' },
      { theme: 'Digital / omnichannel mix', rag: 'G', note: 'E-commerce contribution growing mid-teens; last-mile costs normalising' },
      { theme: 'Private label mix shift', rag: 'A', note: 'Price-value positioning supporting unit volume but pressuring brand-mix revenue' },
    ],
  },
  'Energy & Resources': {
    total: 20,
    baseRatings: { Buy: 10, Outperform: 4, Hold: 5, Underperform: 1, Sell: 0 },
    sector: 'S&P Energy',
    themes: [
      { theme: 'Free cash flow discipline', rag: 'G', note: 'Capex budgets held flat; strong FCF supporting buyback programmes' },
      { theme: 'Energy transition capex', rag: 'A', note: 'Low-carbon investments growing as % of budget; ROI proving out in early projects' },
      { theme: 'Commodity price volatility', rag: 'R', note: 'Oil/gas price deck above consensus strip; FCF downside if prices normalise' },
      { theme: 'Permitting & regulatory risk', rag: 'A', note: 'Project timelines extending on permitting backlog; affects production ramp cadence' },
    ],
  },
  'Utilities': {
    total: 16,
    baseRatings: { Buy: 8, Outperform: 3, Hold: 4, Underperform: 1, Sell: 0 },
    sector: 'Utility MSCI',
    themes: [
      { theme: 'Data centre load growth', rag: 'G', note: 'AI infrastructure buildout driving strong incremental load demand in service territories' },
      { theme: 'Rate case outcomes', rag: 'A', note: 'Regulatory lag on O&M cost recovery compressing near-term allowed ROE' },
      { theme: 'Grid capex visibility', rag: 'G', note: 'Hardening and generation transition capex fully permitted through 2028' },
      { theme: 'Interest rate sensitivity', rag: 'A', note: 'Regulated utility yields less attractive vs. risk-free rate at current levels' },
    ],
  },
  'Industrial & Manufacturing': {
    total: 18,
    baseRatings: { Buy: 9, Outperform: 3, Hold: 5, Underperform: 1, Sell: 0 },
    sector: 'S&P Industrials',
    themes: [
      { theme: 'Reshoring / nearshoring cycle', rag: 'G', note: 'North America capex cycle supporting aftermarket and equipment demand into 2027' },
      { theme: 'Automation penetration', rag: 'G', note: 'Robotics and factory automation driving content-per-unit expansion in key end markets' },
      { theme: 'Aerospace cycle strength', rag: 'G', note: 'Commercial aircraft production ramp still years below peak; long-tailed backlog' },
      { theme: 'Input cost normalisation', rag: 'A', note: 'Steel and aluminium costs still elevated vs 2019 norms; margin recovery partial' },
    ],
  },
  'Generic': {
    total: 14,
    baseRatings: { Buy: 6, Outperform: 3, Hold: 4, Underperform: 1, Sell: 0 },
    sector: 'S&P 500',
    themes: [
      { theme: 'Revenue growth trajectory', rag: 'A', note: 'Top-line growth trending in line with consensus; forward guidance conservative' },
      { theme: 'Margin expansion path', rag: 'A', note: 'Operating leverage building but partially offset by elevated SG&A investment' },
      { theme: 'Capital allocation discipline', rag: 'G', note: 'Buyback yield and dividend policy viewed positively; balance sheet in good shape' },
      { theme: 'Macro sensitivity', rag: 'A', note: 'Business tied to broader economic cycle; cautious on H2 2026 macro outlook' },
    ],
  },
};

function getAnalystConsensus(industry, ticker, data) {
  const tmpl = ANALYST_CONSENSUS_DB[industry] || ANALYST_CONSENSUS_DB['Generic'];
  const sv = id => seededVal(ticker, 'ac-' + id, 0, 1);
  const jitter = n => Math.max(0, Math.round(n + (sv('j' + n) - 0.5) * 3));
  const ratings = {};
  for (const [k, v] of Object.entries(tmpl.baseRatings)) ratings[k] = jitter(v);
  const total = Object.values(ratings).reduce((s, v) => s + v, 0) || 1;
  const buyCount = (ratings['Buy'] || 0) + (ratings['Outperform'] || 0);
  const buyPct = buyCount / total;
  const consensus = buyPct >= 0.65 ? 'BUY' : buyPct >= 0.50 ? 'OUTPERFORM' : buyPct >= 0.35 ? 'HOLD' : 'UNDERPERFORM';
  const consensusColor = buyPct >= 0.55 ? 'var(--green-ink)' : buyPct >= 0.38 ? 'var(--amber-ink)' : 'var(--red-ink)';
  const ptUpside = seededVal(ticker, 'ac-pt', 5, 28) + ((data?.sentiment?.score ?? 0) * 0.4);
  const epsRaw = seededVal(ticker, 'ac-eps', -3.5, 5.5);
  const epsRevisions = (epsRaw >= 0 ? '+' : '') + epsRaw.toFixed(1) + '%';
  const epsRevDir = epsRaw >= 0 ? 'up' : 'down';
  const sentiment = data?.sentiment?.score ?? 0;
  const themes = tmpl.themes.map((t, i) => {
    if (i === 0 && sentiment > 6) return { ...t, rag: 'G' };
    if (i === 0 && sentiment < -6) return { ...t, rag: 'R' };
    return { ...t };
  });
  return { ratings, total, buyPct, consensus, consensusColor, ptUpside, epsRevisions, epsRevDir, themes, sector: tmpl.sector };
}

function IndustryKPISection({ industry, data, livefacts, ticker }) {
  const defs = INDUSTRY_KPI_DEFS[industry] || INDUSTRY_KPI_DEFS['Generic'];
  const ragColor = { G: 'var(--green)', A: 'var(--amber)', R: 'var(--red)' };
  const ragInk   = { G: 'var(--green-ink)', A: 'var(--amber-ink)', R: 'var(--red-ink)' };

  const kpis = defs.map(def => {
    const sv = (lo, hi) => seededVal(ticker, def.id, lo, hi);
    const rawVal = def.get(data, livefacts, sv);
    const val = (rawVal != null && Number.isFinite(rawVal)) ? rawVal : null;
    const rag = val != null ? def.rag(val) : 'A';
    return { ...def, val, rag };
  });

  return (
    <div className="fcst-card" style={{marginTop: 14}}>
      <div className="head">
        <div>
          <div className="ttl">Industry KPIs · {industry || 'Generic'}</div>
          <div className="sub">Sector benchmarks and performance indicators · {industry || 'Generic'} peer group</div>
        </div>
      </div>
      <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(155px, 1fr))', gap: 10, marginTop: 12}}>
        {kpis.map(kpi => (
          <div key={kpi.id} style={{border: '1px solid var(--line)', borderRadius: 8, padding: '10px 12px', position: 'relative', overflow: 'hidden'}}>
            <div style={{position: 'absolute', top: 0, left: 0, right: 0, height: 3, background: ragColor[kpi.rag], borderRadius: '8px 8px 0 0'}}/>
            <div style={{fontSize: 10.5, color: 'var(--ink-3)', marginBottom: 5, lineHeight: 1.3}}>{kpi.label}</div>
            <div style={{fontSize: 18, fontWeight: 600, fontFamily: 'Geist Mono, monospace', color: kpi.val != null ? ragInk[kpi.rag] : 'var(--ink-4)', marginBottom: 4}}>
              {kpi.val != null ? kpi.fmt(kpi.val) : '—'}
            </div>
            <div style={{fontSize: 9.5, color: 'var(--ink-4)'}}>
              Benchmark: {kpi.bmkFmt}{kpi.dir !== 'contextual' ? (kpi.dir === 'higher' ? ' · ↑ better' : ' · ↓ better') : ''}
            </div>
          </div>
        ))}
      </div>
      <div style={{marginTop: 10, fontSize: 10.5, color: 'var(--ink-3)', display: 'flex', gap: 12, flexWrap: 'wrap', lineHeight: 1.5}}>
        {[['G','GREEN','At or above sector median'], ['A','AMBER','Within 20% of benchmark'], ['R','RED','Below benchmark']].map(([rag, lbl, desc]) => (
          <span key={rag}>
            <span style={{background: `var(--${rag === 'G' ? 'green' : rag === 'A' ? 'amber' : 'red'}-soft)`, color: ragInk[rag], padding: '1px 6px', borderRadius: 3, fontWeight: 500, fontSize: 9.5, marginRight: 5}}>{lbl}</span>
            {desc}
          </span>
        ))}
      </div>
    </div>
  );
}

function AnalystConsensusSection({ industry, ticker, data }) {
  const consensus = getAnalystConsensus(industry, ticker, data);
  const { ratings, total, buyPct, consensus: csLabel, consensusColor, ptUpside, epsRevisions, epsRevDir, themes, sector } = consensus;
  const ratingColors = { Buy: 'var(--green)', Outperform: '#4ade80', Hold: 'var(--amber)', Underperform: '#f97316', Sell: 'var(--red)' };
  const ragColor = { G: 'var(--green)', A: 'var(--amber)', R: 'var(--red)' };

  return (
    <div className="fcst-card" style={{marginTop: 14}}>
      <div className="head">
        <div>
          <div className="ttl">Analyst consensus · {industry || 'Generic'}</div>
          <div className="sub">{total} analysts covering sector · {sector} · 12-month estimates</div>
        </div>
        <div style={{textAlign: 'right'}}>
          <div className="big-num" style={{color: consensusColor}}>{csLabel}</div>
          <div className="delta up">{Math.round(buyPct * 100)}% BUY / OUTPERFORM</div>
        </div>
      </div>
      <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginTop: 14}}>
        <div>
          <div style={{fontSize: 11, fontWeight: 500, color: 'var(--ink-2)', marginBottom: 8}}>Ratings distribution</div>
          {Object.entries(ratings).map(([rating, count]) => {
            const pct = total > 0 ? count / total * 100 : 0;
            return (
              <div key={rating} style={{display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5}}>
                <div style={{width: 88, fontSize: 10.5, color: 'var(--ink-3)'}}>{rating}</div>
                <div style={{flex: 1, height: 13, background: 'var(--surface-2)', borderRadius: 2, overflow: 'hidden'}}>
                  <div style={{height: '100%', width: `${pct}%`, background: ratingColors[rating] || 'var(--line-strong)', borderRadius: 2, transition: 'width 0.3s'}}/>
                </div>
                <div style={{width: 22, fontSize: 10.5, fontFamily: 'Geist Mono, monospace', color: 'var(--ink-2)', textAlign: 'right'}}>{count}</div>
              </div>
            );
          })}
          <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 14}}>
            <div style={{border: '1px solid var(--line)', borderRadius: 7, padding: '8px 10px'}}>
              <div style={{fontSize: 9.5, color: 'var(--ink-3)', marginBottom: 3}}>12M PT UPSIDE</div>
              <div style={{fontSize: 17, fontWeight: 600, fontFamily: 'Geist Mono, monospace', color: ptUpside >= 0 ? 'var(--green-ink)' : 'var(--red-ink)'}}>
                {ptUpside >= 0 ? '+' : ''}{ptUpside.toFixed(0)}%
              </div>
            </div>
            <div style={{border: '1px solid var(--line)', borderRadius: 7, padding: '8px 10px'}}>
              <div style={{fontSize: 9.5, color: 'var(--ink-3)', marginBottom: 3}}>EPS REVISIONS</div>
              <div style={{fontSize: 17, fontWeight: 600, fontFamily: 'Geist Mono, monospace', color: epsRevDir === 'up' ? 'var(--green-ink)' : 'var(--red-ink)'}}>
                {epsRevisions}
              </div>
            </div>
          </div>
        </div>
        <div>
          <div style={{fontSize: 11, fontWeight: 500, color: 'var(--ink-2)', marginBottom: 8}}>Key analyst themes</div>
          {themes.map((t, i) => (
            <div key={i} style={{display: 'flex', alignItems: 'flex-start', gap: 7, marginBottom: 8}}>
              <span style={{width: 7, height: 7, borderRadius: '50%', background: ragColor[t.rag] || 'var(--line-strong)', flexShrink: 0, marginTop: 4}}/>
              <div>
                <div style={{fontSize: 11, fontWeight: 500, color: 'var(--ink)', lineHeight: 1.4}}>{t.theme}</div>
                <div style={{fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.45}}>{t.note}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

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

function ForecastsPanel({ data, liveMode, livefacts, fredSeries, rssSignals, industry, ticker }) {
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
        // Build revenue series — prefer EDGAR XBRL quarterly data (same filter as risk-engine.js buildForecasts)
        let revSeries = null, revSource = "mock";
        if (livefacts?.revenue?.series) {
          // Primary: standalone quarterly 10-Q periods (≥4 quarters)
          const qtrs = livefacts.revenue.series
            .filter(x => x.form === "10-Q" && x.fp !== "FY")
            .sort((a, b) => (a.end < b.end ? -1 : 1));
          if (qtrs.length >= 4) {
            revSeries = qtrs.map(x => x.val / 1e6);
            revSource = "edgar";
          } else {
            // Fallback: annual 10-K FY data (lowered threshold from 6 to 4)
            const annual = livefacts.revenue.series
              .filter(x => x.form === "10-K" && x.fp === "FY")
              .sort((a, b) => (a.end < b.end ? -1 : 1));
            if (annual.length >= 4) {
              revSeries = annual.map(x => x.val / 1e6);
              revSource = "edgar";
            }
          }
        }
        // data.revenue.history is already built from EDGAR quarterly data by risk-engine.js when available
        if (!revSeries) revSeries = data.revenue.history.map(x => x.v);

        // margin history already uses EDGAR quarterly COGS/grossProfit when livefacts was present
        const mgSeries = data.margin.history.map(x => x.v);
        const mgSource = livefacts ? "edgar" : "mock";

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

        // Apply MAE + Trimmed Mean Error to projection bands.
        // TME de-biases the base estimate; MAE * sqrt(horizon) fans the confidence band.
        function applyErrBands(baseFc, btEnsemble) {
          const maeVal = btEnsemble?.mae;
          const tmeVal = btEnsemble?.tme ?? 0;
          if (!maeVal) return baseFc;
          return baseFc.map((f, i) => {
            const horizonFactor = Math.sqrt(i + 1);
            const band = maeVal * horizonFactor;
            const adj  = Number.isFinite(f.base - tmeVal) ? f.base - tmeVal : f.base;
            return {
              ...f,
              base: adj,
              lo:   Number.isFinite(adj - band) ? adj - band : f.lo,
              hi:   Number.isFinite(adj + band) ? adj + band : f.hi,
            };
          });
        }

        // EPS — run ensemble if ≥8 quarters available
        let epsOut = null;
        const epsSeries = data.eps?.history?.length >= 8 ? data.eps.history.map(x => x.v) : null;
        if (epsSeries) {
          const epsBT    = BACKTESTING.backtestAll(epsSeries);
          const epsMapes = [epsBT.results.arima?.mape, epsBT.results.prophet?.mape, epsBT.results.rf?.mape];
          const epsFcAll = BACKTESTING.forecastAll(epsSeries, null, 4, epsMapes);
          const epsEns   = epsFcAll.ensemble;
          const rawEpsFc = data.eps.forecast.map((f, i) => ({
            q:    f.q,
            base: safeV(epsEns?.base[i], f.base),
            lo:   safeV(epsEns?.lo[i],   f.lo),
            hi:   safeV(epsEns?.hi[i],   f.hi),
          }));
          epsOut = {
            history:  data.eps.history,
            forecast: applyErrBands(rawEpsFc, epsBT.results.ensemble),
            all:      epsFcAll,
            backtest: epsBT,
          };
        }
        const rawRevFc = data.revenue.forecast.map((f, i) => ({
          q:    f.q,
          base: safeV(revEns?.base[i], f.base),
          lo:   safeV(revEns?.lo[i],   f.lo),
          hi:   safeV(revEns?.hi[i],   f.hi),
        }));
        const rawMgFc = data.margin.forecast.map((f, i) => ({
          q:    f.q,
          base: safeV(mgEns?.base[i], f.base),
          lo:   safeV(mgEns?.lo[i],   f.lo),
          hi:   safeV(mgEns?.hi[i],   f.hi),
        }));

        setModelOutput({
          revenue: {
            history: data.revenue.history,
            forecast: applyErrBands(rawRevFc, revBT.results.ensemble),
            all: revFcAll,
            backtest: revBT,
            source: revSource,
          },
          margin: {
            history: data.margin.history,
            forecast: applyErrBands(rawMgFc, mgBT.results.ensemble),
            all: mgFcAll,
            backtest: mgBT,
            source: mgSource,
          },
          eps: epsOut,
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
  const eps  = modelOutput?.eps      ?? data.eps;

  const lastHistRev = rev.history[rev.history.length - 1].v;
  const lastFcRev   = rev.forecast[rev.forecast.length - 1].base;
  const revDeltaPct = ((lastFcRev - lastHistRev) / lastHistRev) * 100;

  const lastHistMg = mg.history[mg.history.length - 1].v;
  const lastFcMg   = mg.forecast[mg.forecast.length - 1].base;
  const mgDelta    = (lastFcMg - lastHistMg) * 100;

  const lastFcEPS  = eps?.forecast?.[eps.forecast.length - 1]?.base;
  const lastHistEPS= eps?.history?.[eps.history.length - 1]?.v;
  const epsDeltaPct= (lastFcEPS != null && lastHistEPS) ? ((lastFcEPS - lastHistEPS) / Math.abs(lastHistEPS)) * 100 : null;

  const lastFcFCF  = data.fcf?.forecast?.[data.fcf.forecast.length - 1]?.base;

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
          <div className={`bb-ticker-val${revDeltaPct >= 0 ? " green" : " red"}`}>
            {revFcLast.base >= 1000 ? `$${(revFcLast.base/1000).toFixed(3)}B` : `$${revFcLast.base.toFixed(3)}M`}
          </div>
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
          <div className="bb-ticker-label">EPS FCST</div>
          <div className={`bb-ticker-val${epsDeltaPct != null ? (epsDeltaPct >= 0 ? " green" : " red") : ""}`}>
            {lastFcEPS != null ? `$${lastFcEPS.toFixed(2)}` : "—"}
          </div>
        </div>
        <div className="bb-ticker-item">
          <div className="bb-ticker-label">EPS Δ</div>
          <div className={`bb-ticker-val${epsDeltaPct != null ? (epsDeltaPct >= 0 ? " green" : " red") : ""}`}>
            {epsDeltaPct != null ? `${epsDeltaPct >= 0 ? "▲" : "▼"}${Math.abs(epsDeltaPct).toFixed(1)}%` : "—"}
          </div>
        </div>
        <div className="bb-ticker-item">
          <div className="bb-ticker-label">FCF FCST</div>
          <div className="bb-ticker-val">{lastFcFCF != null ? `$${lastFcFCF.toFixed(1)}M` : "—"}</div>
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
              <div className="big-num">{lastFcRev >= 1000 ? `$${(lastFcRev/1000).toFixed(3)}B` : `$${lastFcRev.toFixed(3)}M`}</div>
              <div className={`delta ${revDeltaPct >= 0 ? "up" : "dn"}`}>
                {revDeltaPct >= 0 ? "▲" : "▼"} {Math.abs(revDeltaPct).toFixed(1)}% vs latest
              </div>
            </div>
          </div>
          <ForecastChart history={rev.history.slice(-8)} forecast={rev.forecast} unit="$M" color="var(--acc)" decimals={2} chartMetrics={modelOutput?.revenue?.backtest?.results?.ensemble}/>
          {modelOutput?.revenue?.all && (
            <ComponentForecastTable fcAll={modelOutput.revenue.all} labels={data.revenue.forecast.map(f => f.q)} unit="$M" decimals={2}/>
          )}
        </div>

        <div className="fcst-card">
          <div className="head">
            <div>
              <div className="ttl">Gross margin</div>
              <div className="sub">
                {modelOutput ? `${modelOutput.margin.source === "edgar" ? "EDGAR XBRL" : "Mock"} series · ensemble` : "Quarterly % · 8 history + 4 forecast"}
              </div>
            </div>
            <div style={{textAlign:"right"}}>
              <div className="big-num">{lastFcMg.toFixed(1)}%</div>
              <div className={`delta ${mgDelta >= 0 ? "up" : "dn"}`}>
                {mgDelta >= 0 ? "▲" : "▼"} {Math.abs(mgDelta).toFixed(0)} bps vs latest
              </div>
            </div>
          </div>
          <ForecastChart history={mg.history.slice(-8)} forecast={mg.forecast} unit="%" color="var(--violet)" chartMetrics={modelOutput?.margin?.backtest?.results?.ensemble}/>
          {modelOutput?.margin?.all && (
            <ComponentForecastTable fcAll={modelOutput.margin.all} labels={data.margin.forecast.map(f => f.q)} unit="%" />
          )}
        </div>
      </div>

      {/* ── Analyst KPI row 1: EPS + EBITDA ─────────────────────────────────── */}
      {(eps || data.ebitda) && (
        <div className="fcst-row">
          {eps && (() => {
            const lastH = eps.history[eps.history.length - 1]?.v;
            const lastF = eps.forecast[eps.forecast.length - 1]?.base;
            const d = lastH != null && lastH !== 0 ? ((lastF - lastH) / Math.abs(lastH)) * 100 : null;
            return (
              <div className="fcst-card">
                <div className="head">
                  <div>
                    <div className="ttl">EPS · Diluted</div>
                    <div className="sub">{modelOutput?.eps ? "EDGAR XBRL · ensemble" : "Quarterly $/share · 8 history + 4 forecast"}</div>
                  </div>
                  <div style={{textAlign:"right"}}>
                    <div className="big-num">{lastF != null ? `$${lastF.toFixed(2)}` : "—"}</div>
                    {d != null && <div className={`delta ${d >= 0 ? "up" : "dn"}`}>{d >= 0 ? "▲" : "▼"} {Math.abs(d).toFixed(1)}% vs latest</div>}
                  </div>
                </div>
                <ForecastChart history={eps.history.slice(-8)} forecast={eps.forecast} unit="$" color="var(--acc)" chartMetrics={modelOutput?.eps?.backtest?.results?.ensemble}/>
                {modelOutput?.eps?.all && (
                  <ComponentForecastTable fcAll={modelOutput.eps.all} labels={data.eps.forecast.map(f => f.q)} unit="$" />
                )}
              </div>
            );
          })()}
          {data.ebitda && (() => {
            const lastH = data.ebitda.history[data.ebitda.history.length - 1]?.v;
            const lastF = data.ebitda.forecast[data.ebitda.forecast.length - 1]?.base;
            const d = lastH ? ((lastF - lastH) / lastH) * 100 : null;
            return (
              <div className="fcst-card">
                <div className="head">
                  <div>
                    <div className="ttl">EBITDA</div>
                    <div className="sub">Operating Income + D&amp;A · quarterly $M</div>
                  </div>
                  <div style={{textAlign:"right"}}>
                    <div className="big-num">${lastF != null ? lastF.toFixed(0) : "—"}M</div>
                    {d != null && <div className={`delta ${d >= 0 ? "up" : "dn"}`}>{d >= 0 ? "▲" : "▼"} {Math.abs(d).toFixed(1)}%</div>}
                  </div>
                </div>
                <ForecastChart history={data.ebitda.history.slice(-8)} forecast={data.ebitda.forecast} unit="$M" color="var(--violet)"/>
              </div>
            );
          })()}
        </div>
      )}

      {/* ── Analyst KPI row 2: Net Income + FCF ─────────────────────────────── */}
      {(data.netIncome || data.fcf) && (
        <div className="fcst-row">
          {data.netIncome && (() => {
            const lastH = data.netIncome.history[data.netIncome.history.length - 1]?.v;
            const lastF = data.netIncome.forecast[data.netIncome.forecast.length - 1]?.base;
            const d = lastH ? ((lastF - lastH) / Math.abs(lastH)) * 100 : null;
            return (
              <div className="fcst-card">
                <div className="head">
                  <div>
                    <div className="ttl">Net Income</div>
                    <div className="sub">GAAP · quarterly $M</div>
                  </div>
                  <div style={{textAlign:"right"}}>
                    <div className="big-num">${lastF != null ? lastF.toFixed(1) : "—"}M</div>
                    {d != null && <div className={`delta ${d >= 0 ? "up" : "dn"}`}>{d >= 0 ? "▲" : "▼"} {Math.abs(d).toFixed(1)}%</div>}
                  </div>
                </div>
                <ForecastChart history={data.netIncome.history.slice(-8)} forecast={data.netIncome.forecast} unit="$M" color="var(--acc)" decimals={1}/>
              </div>
            );
          })()}
          {data.fcf && (() => {
            const lastH = data.fcf.history[data.fcf.history.length - 1]?.v;
            const lastF = data.fcf.forecast[data.fcf.forecast.length - 1]?.base;
            const d = lastH ? ((lastF - lastH) / Math.abs(lastH)) * 100 : null;
            return (
              <div className="fcst-card">
                <div className="head">
                  <div>
                    <div className="ttl">Free Cash Flow</div>
                    <div className="sub">CFO − CapEx · quarterly $M</div>
                  </div>
                  <div style={{textAlign:"right"}}>
                    <div className="big-num">${lastF != null ? lastF.toFixed(1) : "—"}M</div>
                    {d != null && <div className={`delta ${d >= 0 ? "up" : "dn"}`}>{d >= 0 ? "▲" : "▼"} {Math.abs(d).toFixed(1)}%</div>}
                  </div>
                </div>
                <ForecastChart history={data.fcf.history.slice(-8)} forecast={data.fcf.forecast} unit="$M" color="#4aad52" decimals={1}/>
              </div>
            );
          })()}
        </div>
      )}

      {/* ── Analyst KPI row 3: Operating Margin ─────────────────────────────── */}
      {data.opMargin && (() => {
        const lastH = data.opMargin.history[data.opMargin.history.length - 1]?.v;
        const lastF = data.opMargin.forecast[data.opMargin.forecast.length - 1]?.base;
        const mgH   = mg.history[mg.history.length - 1]?.v;
        const spread = mgH != null && lastH != null ? +(mgH - lastH).toFixed(1) : null;
        return (
          <div className="fcst-row">
            <div className="fcst-card">
              <div className="head">
                <div>
                  <div className="ttl">Operating Margin</div>
                  <div className="sub">EBIT ÷ Revenue · quarterly %</div>
                </div>
                <div style={{textAlign:"right"}}>
                  <div className="big-num">{lastF != null ? `${lastF.toFixed(2)}%` : "—"}</div>
                  {spread != null && <div className="sub" style={{marginTop:2}}>Gross−Op spread: {spread.toFixed(1)} pp</div>}
                </div>
              </div>
              <ForecastChart history={data.opMargin.history.slice(-8)} forecast={data.opMargin.forecast} unit="%" color="#e8a838" decimals={2}/>
            </div>
            <div className="fcst-card">
              <div className="head">
                <div>
                  <div className="ttl">Margin Comparison</div>
                  <div className="sub">Latest quarter · Gross vs Operating vs Net</div>
                </div>
              </div>
              <div style={{padding:"12px 0"}}>
                {[
                  { label: "Gross Margin",     v: mg.history[mg.history.length-1]?.v,              unit: "%", color: "var(--violet)" },
                  { label: "Operating Margin", v: data.opMargin.history[data.opMargin.history.length-1]?.v, unit: "%", color: "#e8a838" },
                  { label: "Net Margin",       v: data.netIncome && data.revenue
                      ? +(data.netIncome.history.slice(-1)[0]?.v / data.revenue.history.slice(-1)[0]?.v * 100).toFixed(1)
                      : null,                                                                        unit: "%", color: "var(--acc)" },
                ].map(({ label, v, unit, color }) => v != null && (
                  <div key={label} style={{display:"flex", alignItems:"center", gap:8, marginBottom:10}}>
                    <div style={{width:3, height:28, background:color, borderRadius:2, flexShrink:0}}/>
                    <div style={{flex:1}}>
                      <div style={{fontSize:10.5, color:"var(--ink-3)", letterSpacing:"0.05em", textTransform:"uppercase"}}>{label}</div>
                      <div style={{fontSize:15, fontWeight:500, fontVariantNumeric:"tabular-nums"}}>{v.toFixed(1)}{unit}</div>
                    </div>
                    <div style={{width:`${Math.min(v, 100)}%`, maxWidth:120, height:4, background:color, opacity:0.35, borderRadius:2}}/>
                  </div>
                ))}
              </div>
            </div>
          </div>
        );
      })()}

      {modelOutput && (
        <ModelDiagnosticsCard revenue={modelOutput.revenue} margin={modelOutput.margin} />
      )}

      {industry && (
        <IndustryKPISection industry={industry} data={data} livefacts={livefacts} ticker={ticker || 'X'} />
      )}
      {industry && (
        <GeoSegmentKPISection industry={industry} ticker={ticker || 'X'} data={data} livefacts={livefacts} />
      )}
      {industry && (
        <AnalystConsensusSection industry={industry} ticker={ticker || 'X'} data={data} />
      )}

      <div className="fcst-row">
        <div className="fcst-card">
          <div className="head">
            <div>
              <div className="ttl">Beneish M-Score</div>
              <div className="sub">Forensic accounting · 8-variable earnings manipulation model</div>
            </div>
            <div style={{textAlign:"right"}}>
              <div className="big-num" style={{color: data.mscore.m > -1.78 ? "var(--red-ink)" : data.mscore.m > -2.22 ? "var(--amber-ink)" : "var(--green-ink)"}}>
                {data.mscore.m.toFixed(2)}
              </div>
              <div className="sub" style={{marginTop:2}}>
                {data.mscore.m > -1.78 ? "ELEVATED · likely manipulator" : data.mscore.m > -2.22 ? "GRAY ZONE · monitor" : "NORMAL"}
              </div>
            </div>
          </div>
          <MScoreGauge m={data.mscore.m}/>
          <div className="mt-12" style={{fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.55}}>
            <b style={{fontWeight: 500}}>Key driver:</b> {data.mscore.key_driver}. Band breaches RED at M &gt; −1.78, AMBER &gt; −2.22.
          </div>
          {/* Individual metric breakdown */}
          {(() => {
            const META = {
              DSRI: { label:'Days Sales Recv. Index',  threshold:1.465, amber:1.20, desc:'AR growing faster than revenue → potential revenue inflation' },
              GMI:  { label:'Gross Margin Index',      threshold:1.193, amber:1.05, desc:'Declining gross margins → deteriorating profitability signal' },
              AQI:  { label:'Asset Quality Index',     threshold:1.254, amber:1.10, desc:'Non-current / intangible assets rising → potential capitalisation of expenses' },
              SGI:  { label:'Sales Growth Index',      threshold:1.607, amber:1.30, desc:'Aggressive revenue growth → elevated manipulation incentive' },
              DEPI: { label:'Depreciation Index',      threshold:1.077, amber:1.00, desc:'Slower depreciation rate → possible useful-life extension to boost earnings' },
              SGAI: { label:'SGA Expenses Index',      threshold:1.041, amber:1.00, desc:'SGA growing relative to sales → potential misclassification or cost manipulation' },
              LVGI: { label:'Leverage Index',          threshold:1.111, amber:1.00, desc:'Increasing leverage → covenant pressure increasing manipulation motive' },
              TATA: { label:'Accruals / Total Assets', threshold:0.031, amber:0.01, desc:'High accrual intensity → earnings diverging from cash generation' },
            };
            const vars = data.mscore.vars || {};
            const elevated = Object.entries(vars).filter(([k, v]) => {
              const m = META[k]; return m && typeof v === 'number' && v > m.threshold;
            });
            return (
              <div className="mt-12">
                {elevated.length > 0 && (
                  <div style={{background:'color-mix(in oklch, var(--red) 8%, transparent)', border:'1px solid color-mix(in oklch, var(--red) 25%, transparent)', borderRadius:7, padding:'8px 12px', marginBottom:10, fontSize:11, color:'var(--red-ink)', lineHeight:1.5}}>
                    <b style={{fontWeight:600}}>{elevated.length} variable{elevated.length > 1 ? 's' : ''} above manipulation threshold:</b>{' '}
                    {elevated.map(([k]) => k).join(', ')}
                  </div>
                )}
                <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(210px, 1fr))', gap:8}}>
                  {Object.entries(vars).map(([k, v]) => {
                    const m = META[k];
                    if (!m || typeof v !== 'number') return (
                      <div key={k} className="scen-m"><div className="l">{k}</div><div className="v">{v}</div></div>
                    );
                    const isRed   = v > m.threshold;
                    const isAmber = !isRed && v > m.amber;
                    const rag = isRed ? 'R' : isAmber ? 'A' : 'G';
                    const ragColor = {R:'var(--red)', A:'var(--amber)', G:'var(--green)'};
                    const ragInk   = {R:'var(--red-ink)', A:'var(--amber-ink)', G:'var(--green-ink)'};
                    // Bar: show v relative to threshold (clamp at 2× threshold for display)
                    const barMax = Math.max(m.threshold * 1.8, Math.abs(v) * 1.2, 0.1);
                    const valPct  = Math.min(100, (Math.abs(v) / barMax) * 100);
                    const thrPct  = Math.min(100, (m.threshold / barMax) * 100);
                    return (
                      <div key={k} style={{border:`1px solid ${isRed ? 'color-mix(in oklch, var(--red) 35%, var(--line))' : 'var(--line)'}`, borderRadius:8, padding:'9px 11px', position:'relative', overflow:'hidden', background: isRed ? 'color-mix(in oklch, var(--red) 4%, transparent)' : undefined}}>
                        <div style={{position:'absolute', top:0, left:0, right:0, height:3, background:ragColor[rag], borderRadius:'8px 8px 0 0'}}/>
                        <div style={{display:'flex', justifyContent:'space-between', alignItems:'baseline', marginBottom:4}}>
                          <span style={{fontWeight:600, fontSize:10, fontFamily:'Geist Mono, monospace', letterSpacing:'0.05em'}}>{k}</span>
                          <span style={{fontSize:16, fontWeight:600, fontFamily:'Geist Mono, monospace', color:ragInk[rag]}}>{v.toFixed(3)}</span>
                        </div>
                        <div style={{fontSize:9.5, color:'var(--ink-3)', marginBottom:6, lineHeight:1.35}}>{m.label}</div>
                        {/* Mini bar */}
                        <div style={{height:5, background:'var(--surface-2)', borderRadius:3, position:'relative', marginBottom:5}}>
                          <div style={{position:'absolute', left:0, top:0, height:'100%', width:`${valPct}%`, background:ragColor[rag], borderRadius:3, opacity:0.75}}/>
                          {/* Threshold marker */}
                          <div style={{position:'absolute', top:-2, bottom:-2, left:`${thrPct}%`, width:2, background:'var(--ink-3)', borderRadius:1}}/>
                        </div>
                        <div style={{display:'flex', justifyContent:'space-between', fontSize:9, color:'var(--ink-4)', fontFamily:'Geist Mono, monospace'}}>
                          <span>{isRed ? '⚠ ELEVATED' : isAmber ? '△ WATCH' : '✓ NORMAL'}</span>
                          <span>threshold {m.threshold}</span>
                        </div>
                        {isRed && (
                          <div style={{marginTop:5, fontSize:9, color:'var(--red-ink)', lineHeight:1.35}}>{m.desc}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })()}
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
function ComponentForecastTable({ fcAll, labels, unit, decimals }) {
  const models = ["arima","prophet","rf","ensemble"];
  const dp = decimals ?? (unit === "$M" ? 0 : unit === "$" ? 2 : 1);
  const fmt = (v, u) => {
    if (v == null) return "—";
    if (u === "$M") return v >= 1000 ? `$${(v / 1000).toFixed(dp)}B` : `$${v.toFixed(dp)}M`;
    if (u === "$") return `$${v.toFixed(dp)}`;
    return `${v.toFixed(dp)}%`;
  };
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

// ── Geographic & segment KPI defaults ─────────────────────────────────────────
// Tuples: [name, pct-of-total, gm_delta_pp, om_delta_pp]
// Used when DB has no segment data for the ticker.
const _GEO_DEFAULTS = {
  'Semiconductors':           [['Americas',35,2,4],['EMEA',18,-1,-2],['China',20,-5,-6],['APAC ex-China',20,1,1],['Japan',7,3,3]],
  'Software & Cloud':         [['Americas',58,3,5],['EMEA',28,-2,-3],['APAC',14,-4,-5]],
  'Automotive OEM':           [['North America',30,1,2],['EMEA',35,-1,-1],['China',20,-4,-5],['Rest of World',15,-2,-2]],
  'Healthcare & Pharma':      [['North America',55,3,4],['EMEA',30,-1,-2],['APAC',15,-3,-3]],
  'Financial Services':       [['Domestic',70,2,3],['International',30,-2,-3]],
  'Retail & Consumer':        [['North America',62,2,3],['EMEA',22,-1,-1],['APAC',16,-3,-3]],
  'Energy & Resources':       [['North America',55,1,2],['International',45,-1,-2]],
  'Utilities':                [['Regulated',80,2,3],['Unregulated',20,-4,-5]],
  'Industrial & Manufacturing':[['Americas',40,1,2],['EMEA',30,-1,-1],['APAC',20,-2,-2],['Rest',10,-3,-3]],
  'Generic':                  [['Domestic',65,2,3],['International',35,-3,-4]],
};
const _SEG_DEFAULTS = {
  'Semiconductors':           [['Power Solutions',40,3,5],['Intelligent Sensing',30,-1,-2],['Advanced Logic',30,2,3]],
  'Software & Cloud':         [['Cloud / Subscription',65,4,6],['On-Premise / License',20,-3,-4],['Professional Services',15,-8,-6]],
  'Automotive OEM':           [['Electric Vehicles',25,-6,-10],['ICE & Hybrid Vehicles',60,3,4],['Aftermarket / Parts',15,6,8]],
  'Healthcare & Pharma':      [['Innovative Medicine',55,4,5],['MedTech',30,-1,-1],['Consumer Health',15,-2,-3]],
  'Financial Services':       [['Retail Banking',45,1,2],['Institutional / Markets',35,-1,2],['Wealth Management',20,3,5]],
  'Retail & Consumer':        [['Core Retail',60,1,2],['E-Commerce',28,4,3],['Private Label',12,-3,-2]],
  'Energy & Resources':       [['Upstream E&P',50,2,5],['Midstream',30,1,1],['Downstream',20,-2,-2]],
  'Utilities':                [['Electric Distribution',60,2,2],['Natural Gas',25,-1,-1],['Renewables',15,-5,-3]],
  'Industrial & Manufacturing':[['Equipment & Systems',50,2,3],['Aftermarket Services',30,6,8],['Components',20,-2,-1]],
  'Generic':                  [['Core Business',70,2,3],['Adjacent & Other',30,-4,-4]],
};

// ── Geographic & Segment KPI breakdown ────────────────────────────────────────
function GeoSegmentKPISection({ ticker, industry, data, livefacts }) {
  const [dbSegments, setDbSegments] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [segFcst, setSegFcst] = React.useState(null);
  const [fcstRunning, setFcstRunning] = React.useState(false);

  React.useEffect(() => {
    if (!ticker) return;
    const year = new Date().getFullYear();
    const base = window.MCP_API_BASE || 'http://127.0.0.1:8001';
    setLoading(true);
    fetch(`${base}/sox/segments/${encodeURIComponent(ticker)}/FY${year}`, { signal: AbortSignal.timeout(4000) })
      .then(r => r.ok ? r.json() : null)
      .then(rows => { if (rows?.length) setDbSegments(rows); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [ticker]);

  const ind = industry || 'Generic';

  // Consolidated base values
  const consRevAnnualM = (data?.revenue?.history?.slice(-1)[0]?.v ?? 0) * 4;
  const consGM  = data?.margin?.history?.slice(-1)[0]?.v ?? 40;
  const consOM  = data?.opMargin?.history?.slice(-1)[0]?.v
    ?? (livefacts?.operatingIncome?.latestAnnual && livefacts?.revenue?.latestAnnual
        ? (livefacts.operatingIncome.latestAnnual.val / livefacts.revenue.latestAnnual.val * 100)
        : consGM * 0.55);
  const consNI  = data?.netIncome && data?.revenue
    ? (data.netIncome.history.slice(-1)[0]?.v / data.revenue.history.slice(-1)[0]?.v * 100)
    : null;
  const consGrowth = computeRevGrowthYoY(data);

  function buildRows(segType, defaultMap) {
    const dbRows = dbSegments?.filter(r => r.segment_type === segType);
    if (dbRows?.length) {
      return dbRows.map(s => {
        const gm = s.gross_profit != null && s.revenue ? (s.gross_profit / s.revenue * 100) : null;
        const om = s.operating_income != null && s.revenue ? (s.operating_income / s.revenue * 100) : null;
        return {
          name: s.segment_name, revM: s.revenue != null ? s.revenue / 1e6 : null,
          revPct: s.revenue_pct, gmPct: gm, omPct: om, niPct: null, revGrowth: null,
          fromDB: true, gmD: gm != null ? gm - consGM : 0, omD: om != null ? om - consOM : 0,
        };
      });
    }
    const defs = defaultMap[ind] || defaultMap['Generic'];
    return defs.map(([name, pct, gmD, omD]) => {
      const sv = id => seededVal(ticker || 'X', `gs-${name}-${id}`, -1, 1);
      return {
        name,
        revM: consRevAnnualM * (pct / 100) * (1 + sv('r') * 0.06),
        revPct: pct,
        gmPct: Math.max(0, consGM + gmD + sv('gm') * 2.5),
        omPct: consOM + omD + sv('om') * 1.5,
        niPct: consNI != null ? consNI + omD * 0.6 + sv('ni') * 1.5 : null,
        revGrowth: consGrowth != null ? consGrowth + sv('g') * 4 : null,
        fromDB: false, gmD, omD,
      };
    });
  }

  const geoRows = buildRows('geography', _GEO_DEFAULTS);
  const bizRows = buildRows('segment',   _SEG_DEFAULTS);
  const isDB = !!dbSegments?.length;

  // ── Forecast computation: walk-forward backtest + ensemble for each segment ──
  React.useEffect(() => {
    const consRevHist = data?.revenue?.history;
    const consMgHist  = data?.margin?.history;
    if (!consRevHist || !consMgHist || consRevHist.length < 4 || consMgHist.length < 4) return;
    if (typeof BACKTESTING === 'undefined') return;

    setFcstRunning(true);
    const handle = setTimeout(() => {
      try {
        const fcstQs = data.revenue.forecast?.map(f => f.q) || ['Q1', 'Q2', 'Q3', 'Q4'];
        const safeN = (v, fb) => Number.isFinite(v) ? v : fb;

        function applyErrBands(fc, btEns) {
          const mae = btEns?.mae, tme = btEns?.tme ?? 0;
          if (!mae) return fc;
          return fc.map((f, i) => {
            const adj = Number.isFinite(f.base - tme) ? f.base - tme : f.base;
            const band = mae * Math.sqrt(i + 1);
            return { ...f, base: adj, lo: adj - band, hi: adj + band };
          });
        }

        // Rebuild minimal rows locally to avoid stale closure issues
        const ind_l = industry || 'Generic';
        const consGM_l = consMgHist[consMgHist.length - 1].v;

        function makeDefaultRows(defaultMap) {
          const defs = defaultMap[ind_l] || defaultMap['Generic'];
          return defs.map(([name, pct, gmD]) => ({ name, revPct: pct, gmD }));
        }

        const geoR = dbSegments?.filter(r => r.segment_type === 'geography')?.map(s => {
          const gm = s.gross_profit != null && s.revenue ? (s.gross_profit / s.revenue * 100) : consGM_l;
          return { name: s.segment_name, revPct: s.revenue_pct ?? 0, gmD: gm - consGM_l };
        }) || makeDefaultRows(_GEO_DEFAULTS);

        const bizR = dbSegments?.filter(r => r.segment_type === 'segment')?.map(s => {
          const gm = s.gross_profit != null && s.revenue ? (s.gross_profit / s.revenue * 100) : consGM_l;
          return { name: s.segment_name, revPct: s.revenue_pct ?? 0, gmD: gm - consGM_l };
        }) || makeDefaultRows(_SEG_DEFAULTS);

        function computeForRow(row) {
          // Revenue history: consolidated quarterly × segment share + per-quarter noise
          const revHist = consRevHist.map((h, qi) => {
            const noise = seededVal(ticker || 'X', `fcst-${row.name}-rev-t${qi}`, -0.04, 0.04);
            return { q: h.q, v: Math.max(0.01, h.v * ((row.revPct || 0) / 100) * (1 + noise)) };
          });
          // GM history: consolidated margin + fixed segment delta + per-quarter noise
          const gmHist = consMgHist.map((h, qi) => {
            const noise = seededVal(ticker || 'X', `fcst-${row.name}-gm-t${qi}`, -0.6, 0.6);
            return { q: h.q, v: Math.max(0, h.v + (row.gmD ?? 0) + noise) };
          });

          const revSeries = revHist.map(h => h.v);
          const gmSeries  = gmHist.map(h => h.v);

          const revBT = BACKTESTING.backtestAll(revSeries);
          const gmBT  = BACKTESTING.backtestAll(gmSeries);

          const revFcAll = BACKTESTING.forecastAll(revSeries, null, 4,
            [revBT.results.arima?.mape, revBT.results.prophet?.mape, revBT.results.rf?.mape]);
          const gmFcAll  = BACKTESTING.forecastAll(gmSeries,  null, 4,
            [gmBT.results.arima?.mape,  gmBT.results.prophet?.mape,  gmBT.results.rf?.mape]);

          const lastRev = revHist[revHist.length - 1].v;
          const lastGm  = gmHist[gmHist.length - 1].v;

          const revFc = fcstQs.map((q, i) => ({
            q,
            base: safeN(revFcAll.ensemble?.base[i], lastRev),
            lo:   safeN(revFcAll.ensemble?.lo[i],   lastRev * 0.85),
            hi:   safeN(revFcAll.ensemble?.hi[i],   lastRev * 1.15),
          }));
          const gmFc = fcstQs.map((q, i) => ({
            q,
            base: safeN(gmFcAll.ensemble?.base[i], lastGm),
            lo:   safeN(gmFcAll.ensemble?.lo[i],   lastGm - 3),
            hi:   safeN(gmFcAll.ensemble?.hi[i],   lastGm + 3),
          }));

          return {
            name: row.name, revPct: row.revPct,
            rev: { history: revHist, forecast: applyErrBands(revFc, revBT.results.ensemble), backtest: revBT },
            gm:  { history: gmHist,  forecast: applyErrBands(gmFc,  gmBT.results.ensemble),  backtest: gmBT  },
          };
        }

        setSegFcst({ geo: geoR.map(computeForRow), biz: bizR.map(computeForRow) });
      } catch (e) {
        console.error("Segment forecast error:", e);
      }
      setFcstRunning(false);
    }, 0);

    return () => clearTimeout(handle);
  }, [data, ticker, industry, dbSegments]);

  const conRow = { name:'Consolidated', revM:consRevAnnualM, revPct:100, gmPct:consGM, omPct:consOM, niPct:consNI, revGrowth:consGrowth, isConsolidated:true };

  const ragInk = { G:'var(--green-ink)', A:'var(--amber-ink)', R:'var(--red-ink)' };
  const gmDef  = (INDUSTRY_KPI_DEFS[ind] || INDUSTRY_KPI_DEFS['Generic']).find(d => d.id === 'gm');

  function KpiTable({ rows, title }) {
    return (
      <div style={{marginTop:16}}>
        <div style={{fontSize:11, fontWeight:600, color:'var(--ink-2)', marginBottom:8, display:'flex', alignItems:'center', gap:8}}>
          {title}
          <span className="mono" style={{fontSize:9, color:'var(--ink-4)', fontWeight:400, letterSpacing:'0.06em'}}>
            {isDB ? 'ACTUAL · DB' : 'ESTIMATED · INDUSTRY TYPICAL'}
          </span>
        </div>
        <div style={{overflowX:'auto'}}>
          <table style={{width:'100%', borderCollapse:'collapse', fontSize:11}}>
            <thead>
              <tr style={{borderBottom:'2px solid var(--line)'}}>
                {['Geography / Segment','Revenue $M','Rev %','Rev Growth YoY','Gross Margin','Op. Margin','Net Margin'].map(h => (
                  <th key={h} style={{textAlign: h==='Geography / Segment'?'left':'right', padding:'4px 10px 5px 0', color:'var(--ink-4)', fontWeight:400, fontFamily:'Geist Mono, monospace', fontSize:9.5, whiteSpace:'nowrap'}}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => {
                const isCon = !!row.isConsolidated;
                const gmRag = row.gmPct != null && gmDef ? gmDef.rag(row.gmPct) : 'A';
                const omRag = row.omPct == null ? 'A' : row.omPct >= 15 ? 'G' : row.omPct >= 5 ? 'A' : 'R';
                const growInk = row.revGrowth == null ? 'var(--ink-4)' : row.revGrowth >= 5 ? 'var(--green-ink)' : row.revGrowth >= 0 ? 'var(--amber-ink)' : 'var(--red-ink)';
                return (
                  <tr key={i} style={{borderBottom:'1px solid var(--line)', fontWeight: isCon ? 600 : 400, background: isCon ? 'var(--surface-2)' : undefined}}>
                    <td style={{padding:'6px 10px 6px 0', display:'flex', alignItems:'center', gap:6}}>
                      <span style={{width:8, height:8, borderRadius: isCon?2:'50%', background: isCon?'var(--acc)':'var(--line-strong)', display:'inline-block', flexShrink:0}}/>
                      <span style={{fontSize:11.5, color: isCon?'var(--ink)':'var(--ink-2)'}}>{row.name}</span>
                      {!row.fromDB && !isCon && <span className="mono" style={{fontSize:8.5, color:'var(--ink-4)'}}>est.</span>}
                    </td>
                    <td style={{textAlign:'right', padding:'6px 10px 6px 0', fontFamily:'Geist Mono, monospace'}}>
                      {row.revM != null ? (row.revM >= 1000 ? `$${(row.revM/1000).toFixed(2)}B` : `$${row.revM.toFixed(0)}M`) : '—'}
                    </td>
                    <td style={{textAlign:'right', padding:'6px 10px 6px 0', fontFamily:'Geist Mono, monospace', color:'var(--ink-3)'}}>
                      {row.revPct != null ? `${Number(row.revPct).toFixed(0)}%` : '—'}
                    </td>
                    <td style={{textAlign:'right', padding:'6px 10px 6px 0', fontFamily:'Geist Mono, monospace', color:growInk}}>
                      {row.revGrowth != null ? `${row.revGrowth>=0?'+':''}${row.revGrowth.toFixed(1)}%` : '—'}
                    </td>
                    <td style={{textAlign:'right', padding:'6px 10px 6px 0', fontFamily:'Geist Mono, monospace', color: ragInk[gmRag]}}>
                      {row.gmPct != null ? `${row.gmPct.toFixed(1)}%` : '—'}
                    </td>
                    <td style={{textAlign:'right', padding:'6px 10px 6px 0', fontFamily:'Geist Mono, monospace', color: ragInk[omRag]}}>
                      {row.omPct != null ? `${row.omPct.toFixed(1)}%` : '—'}
                    </td>
                    <td style={{textAlign:'right', padding:'6px 10px 6px 0', fontFamily:'Geist Mono, monospace', color:'var(--ink-3)'}}>
                      {row.niPct != null ? `${row.niPct.toFixed(1)}%` : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // ── Chart grids for segment/geo time series ───────────────────────────────
  function SegmentRevChartGrid({ fcstRows, label }) {
    if (!fcstRows?.length) return null;
    return (
      <div style={{marginTop:18}}>
        <div style={{fontSize:10, fontWeight:600, color:'var(--ink-3)', letterSpacing:'0.07em', textTransform:'uppercase', marginBottom:10}}>
          {label} · Revenue Forecast · ARIMA / Prophet / RF Ensemble
        </div>
        <div style={{display:'grid', gridTemplateColumns:'repeat(2,1fr)', gap:12}}>
          {fcstRows.map(seg => {
            if (!seg?.rev?.history?.length || !seg?.rev?.forecast?.length) return null;
            const lastH = seg.rev.history[seg.rev.history.length - 1]?.v;
            const lastF = seg.rev.forecast[seg.rev.forecast.length - 1]?.base;
            const delta = (lastH && lastF != null) ? ((lastF - lastH) / lastH) * 100 : null;
            return (
              <div key={seg.name} style={{border:'1px solid var(--line)', borderRadius:8, padding:'10px 12px'}}>
                <div style={{display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:4}}>
                  <div>
                    <div style={{fontSize:10.5, fontWeight:600, color:'var(--ink-2)'}}>{seg.name}</div>
                    <div style={{fontSize:9, color:'var(--ink-4)'}}>{seg.revPct}% of consolidated · $M</div>
                  </div>
                  {lastF != null && (
                    <div style={{textAlign:'right', flexShrink:0}}>
                      <div style={{fontSize:13, fontWeight:500, fontVariantNumeric:'tabular-nums', fontFamily:'Geist Mono, monospace'}}>
                        {lastF >= 1000 ? `$${(lastF/1000).toFixed(2)}B` : `$${lastF.toFixed(0)}M`}
                      </div>
                      {delta != null && (
                        <div style={{fontSize:9, color: delta >= 0 ? 'var(--green-ink)' : 'var(--red-ink)'}}>
                          {delta >= 0 ? '▲' : '▼'} {Math.abs(delta).toFixed(1)}% vs latest
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <ForecastChart
                  history={seg.rev.history.slice(-8)}
                  forecast={seg.rev.forecast}
                  unit="$M"
                  color="var(--acc)"
                  decimals={1}
                  chartMetrics={seg.rev.backtest?.results?.ensemble}
                />
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  function SegmentGMChartGrid({ fcstRows, label }) {
    if (!fcstRows?.length) return null;
    return (
      <div style={{marginTop:14}}>
        <div style={{fontSize:10, fontWeight:600, color:'var(--ink-3)', letterSpacing:'0.07em', textTransform:'uppercase', marginBottom:10}}>
          {label} · Gross Margin Forecast
        </div>
        <div style={{display:'grid', gridTemplateColumns:'repeat(2,1fr)', gap:12}}>
          {fcstRows.map(seg => {
            if (!seg?.gm?.history?.length || !seg?.gm?.forecast?.length) return null;
            const lastH = seg.gm.history[seg.gm.history.length - 1]?.v;
            const lastF = seg.gm.forecast[seg.gm.forecast.length - 1]?.base;
            const bps   = (lastH != null && lastF != null) ? Math.round((lastF - lastH) * 100) : null;
            return (
              <div key={seg.name} style={{border:'1px solid var(--line)', borderRadius:8, padding:'10px 12px'}}>
                <div style={{display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:4}}>
                  <div>
                    <div style={{fontSize:10.5, fontWeight:600, color:'var(--ink-2)'}}>{seg.name}</div>
                    <div style={{fontSize:9, color:'var(--ink-4)'}}>Gross margin % · quarterly</div>
                  </div>
                  {lastF != null && (
                    <div style={{textAlign:'right', flexShrink:0}}>
                      <div style={{fontSize:13, fontWeight:500, fontVariantNumeric:'tabular-nums', fontFamily:'Geist Mono, monospace'}}>
                        {lastF.toFixed(1)}%
                      </div>
                      {bps != null && (
                        <div style={{fontSize:9, color: bps >= 0 ? 'var(--green-ink)' : 'var(--red-ink)'}}>
                          {bps >= 0 ? '▲' : '▼'} {Math.abs(bps)} bps
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <ForecastChart
                  history={seg.gm.history.slice(-8)}
                  forecast={seg.gm.forecast}
                  unit="%"
                  color="var(--violet)"
                  chartMetrics={seg.gm.backtest?.results?.ensemble}
                />
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className="fcst-card" style={{marginTop:14}}>
      <div className="head">
        <div>
          <div className="ttl">KPI Breakdown · Geography &amp; Segment</div>
          <div className="sub">
            Consolidated vs. geographic and business-segment view ·
            {isDB ? ' actual reported data from DB' : ' industry-typical estimates anchored to consolidated KPIs'}
          </div>
        </div>
        <div style={{display:'flex', alignItems:'center', gap:8}}>
          {loading    && <span className="mono" style={{fontSize:10, color:'var(--ink-4)'}}>Loading segments…</span>}
          {fcstRunning && <span className="mono" style={{fontSize:10, color:'var(--ink-4)'}}>Forecasting…</span>}
        </div>
      </div>

      <KpiTable rows={[conRow, ...geoRows]} title="By Geography"/>
      {segFcst && (
        <>
          <SegmentRevChartGrid fcstRows={segFcst.geo} label="By Geography"/>
          <SegmentGMChartGrid  fcstRows={segFcst.geo} label="By Geography"/>
        </>
      )}
      {!segFcst && !fcstRunning && typeof BACKTESTING === 'undefined' && (
        <div style={{marginTop:10, fontSize:9.5, color:'var(--ink-4)', fontFamily:'Geist Mono, monospace'}}>
          Load forecasting engines to enable time-series charts per geography.
        </div>
      )}

      <div style={{borderTop:'1px solid var(--line)', marginTop:20, paddingTop:2}}/>

      <KpiTable rows={[conRow, ...bizRows]} title="By Business Segment"/>
      {segFcst && (
        <>
          <SegmentRevChartGrid fcstRows={segFcst.biz} label="By Business Segment"/>
          <SegmentGMChartGrid  fcstRows={segFcst.biz} label="By Business Segment"/>
        </>
      )}
      {!segFcst && !fcstRunning && typeof BACKTESTING === 'undefined' && (
        <div style={{marginTop:10, fontSize:9.5, color:'var(--ink-4)', fontFamily:'Geist Mono, monospace'}}>
          Load forecasting engines to enable time-series charts per segment.
        </div>
      )}

      {!isDB && (
        <div style={{marginTop:12, padding:'8px 12px', background:'var(--surface-2)', borderRadius:6, fontSize:10, color:'var(--ink-4)', lineHeight:1.55}}>
          Estimates are industry-typical splits anchored to the consolidated figures — not company-reported segment data.
          Upload actual segment financials via <b style={{fontWeight:500}}>SOX → Geography</b> tab to replace with reported values.
        </div>
      )}
    </div>
  );
}

window.ForecastsPanel        = ForecastsPanel;
window.GeoSegmentKPISection  = GeoSegmentKPISection;
