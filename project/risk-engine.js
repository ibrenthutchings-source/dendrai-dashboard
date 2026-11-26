/* ============================================================
   Dendrai Risk Engine — dynamic profile builder
   Derives risk profiles from EDGAR financials + 8-K / proxy /
   comment-letter evidence + FRED + RSS. Every entity is scored
   against one universal taxonomy from its OWN evidence — there
   are no industry templates or baseline scores. A category with
   no evidence is "not assessed", never given a default.
   ============================================================ */

window.RISK_ENGINE = (function () {
  'use strict';

  // ── Utilities ──────────────────────────────────────────────
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const pct   = (v, d=1) => v != null ? (v * 100).toFixed(d) + '%' : 'n/a';
  const fmt   = (v, d=0) => v != null ? v.toFixed(d) : 'n/a';
  // Thresholds on 0-25 scale (impact × likelihood)
  const RAG_RED = 15, RAG_AMBER = 9;   // single definition; Stage 2's trace text reads these
  const ragOf = s => s >= RAG_RED ? 'R' : s >= RAG_AMBER ? 'A' : 'G';
  const ceOf  = (s, base) => {
    const d = s - base;
    if (d > 5.0)  return 'WEAK';
    if (d > 1.25) return 'ADEQUATE';
    return 'STRONG';
  };
  const velOf = (score, base) => {
    const d = score - base;
    if (d > 3.75) return 3;
    if (d > 1.75) return 2;
    if (d > 0.25) return 1;
    if (d < -2.0) return -1;
    return 0;
  };
  const histOf = (score, base, n = 6) => {
    const step = (score - base) / Math.max(1, n - 1);
    return Array.from({ length: n }, (_, i) =>
      +clamp(base + step * i, 1.0, 25.0).toFixed(1));
  };
  // tier(val, fallback, [pred, delta], ...) — picks FIRST matching
  function tier(val, fallback, ...tiers) {
    if (val == null) return fallback;
    for (const [pred, delta] of tiers) {
      if (pred(val)) return delta;
    }
    return fallback;
  }

  // ── Impact by category (0-5 scale) ────────────────────────
  const CATEGORY_IMPACT = {
    'Revenue':             4,
    'Operational':         3,
    'Financial Reporting': 4,
    'Supply':              4,
    'Cybersecurity':       4,
    'Trade Compliance':    5,
    'ESG':                 2,
    'Compliance':          3,
    'Legal':               3,
    'Strategic':           3,
    // Added with the evidence-scored taxonomy; mirrors risk_rating_engine.py.
    'Liquidity':           5,
    'Governance':          3,
    'Audit Oversight':     4,
  };

  // ── SIC → Industry ─────────────────────────────────────────
  // Synchronous lookup (required — buildProfile() is called synchronously).
  // The backend exposes GET /industry/from-sic as the canonical source.
  // This table must stay in sync with _classify_sic() in api_server.py.
  function sic2industry(sic) {
    const n = parseInt(sic, 10);
    if (!isFinite(n)) return 'Generic';
    if (n === 3674 || (n >= 3672 && n <= 3679) || n === 3559 || n === 3577) return 'Semiconductors';
    if (n === 3711 || n === 3714 || n === 3716 || n === 3519) return 'Automotive OEM';
    if (n >= 7370 && n <= 7379) return 'Software & Cloud';
    if (n >= 6020 && n <= 6199) return 'Financial Services';
    if ((n >= 2830 && n <= 2836) || (n >= 8010 && n <= 8099)) return 'Healthcare & Pharma';
    if (n >= 5200 && n <= 5999) return 'Retail & Consumer';
    if ((n >= 1300 && n <= 1382) || n === 2911) return 'Energy & Resources';
    if (n >= 4911 && n <= 4939) return 'Utilities';
    if (n >= 2000 && n <= 3999) return 'Industrial & Manufacturing';
    return 'Generic';
  }

  // ── Industry label → canonical taxonomy key ────────────────
  // cfg.industry (the Setup screen's dropdown, and TICKER_META's autofill)
  // uses a more granular label set than the risk/forecast templates below
  // are keyed by. Without this, any manually-selected or autofilled industry
  // label fails every TEMPLATES/GREY_SWAN_EVENTS/FRED_BY_INDUSTRY/
  // INDUSTRY_KPI_DEFS/ANALYST_CONSENSUS_DB/_GEO_DEFAULTS/_SEG_DEFAULTS lookup
  // and silently falls back to 'Generic' — sic2industry()'s output already
  // matches this taxonomy, only the dropdown labels need mapping.
  const INDUSTRY_ALIASES = {
    'Analog Semiconductors':      'Semiconductors',
    'Digital Semiconductors':     'Semiconductors',
    'Semiconductor Equipment':    'Semiconductors',
    'Memory Semiconductors':      'Semiconductors',
    'Industrial / Manufacturing': 'Industrial & Manufacturing',
    'Energy / Utilities':         'Energy & Resources',
    'Retail':                     'Retail & Consumer',
  };
  function normalizeIndustry(industry) {
    return INDUSTRY_ALIASES[industry] || industry;
  }

  // ── Ratio Computation ──────────────────────────────────────
  function computeRatios(fin) {
    if (!fin) return {};
    const getV = f => f?.latestAnnual?.val ?? null;
    const getP = f => {
      const s = (f?.series ?? [])
        .filter(x => x.form === '10-K' && x.fp === 'FY')
        .sort((a, b) => b.end > a.end ? 1 : -1);
      return s[1]?.val ?? null;
    };
    const div  = (n, d) => (n != null && d && d !== 0) ? n / d : null;
    const yoy  = (c, p) => (c != null && p && p !== 0) ? (c - p) / p : null;

    const rev    = getV(fin.revenue);
    const revP   = getP(fin.revenue);
    const cogs   = getV(fin.cogs);
    const rd     = getV(fin.rd);
    const sga    = getV(fin.sga);
    const assets = getV(fin.assets);
    const assetsP= getP(fin.assets);
    const cash   = getV(fin.cash);
    const ar     = getV(fin.ar);
    const arP    = getP(fin.ar);
    const ni     = getV(fin.netIncome);
    const cfo    = getV(fin.cfo);
    const capex  = getV(fin.capex);
    const currentAssets      = getV(fin.currentAssets);
    const currentLiabilities = getV(fin.currentLiabilities);
    const liabilities        = getV(fin.liabilities);
    const stockholdersEquity = getV(fin.stockholdersEquity);
    const retainedEarnings   = getV(fin.retainedEarnings);
    const operatingIncome    = getV(fin.operatingIncome);

    const revGrowth    = yoy(rev, revP);
    const grossMargin  = (rev && cogs) ? (rev - cogs) / rev
                       : (fin.grossMarginPct != null ? fin.grossMarginPct / 100 : null);
    const rdIntensity  = div(rd, rev);
    const sgaIntensity = div(sga, rev);
    const niMargin     = div(ni, rev);
    const assetGrowth  = yoy(assets, assetsP);
    const cashRatio    = div(cash, assets);
    const fcf          = (cfo != null && capex != null) ? cfo - capex : cfo;
    const fcfMargin    = div(fcf, rev);
    const tata         = (ni != null && cfo != null && assets) ? (ni - cfo) / assets : null;
    const dsri         = (ar && rev && arP && revP) ? (ar / rev) / (arP / revP) : null;
    const sgi          = (rev && revP) ? rev / revP : null;
    const cogsP        = getP(fin.cogs);
    const grossMarginPrev = (revP && cogsP) ? (revP - cogsP) / revP : null;

    // Simplified Beneish M-score (5 of 8 variables; missing require prior-yr COGS)
    const mscore = (() => {
      const d = dsri ?? 1.0;
      const t = tata ?? 0.0;
      const s = sgi ?? 1.0;
      return -4.84 + 0.920 * d + 0.528 * 1.0 + 0.892 * s + 4.679 * t;
    })();

    // Altman Z''-Score (general/non-manufacturer variant, book equity in
    // place of market equity — this app has no stock-price data source).
    // Missing sub-terms held at neutral 0, same resilience as mscore above.
    const workingCapital = (currentAssets != null && currentLiabilities != null) ? currentAssets - currentLiabilities : null;
    const zscore = (() => {
      const x1 = div(workingCapital, assets) ?? 0.0;
      const x2 = div(retainedEarnings, assets) ?? 0.0;
      const x3 = div(operatingIncome, assets) ?? 0.0;
      const x4 = div(stockholdersEquity, liabilities) ?? 0.0;
      return 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4;
    })();

    const wcToAssets = div(workingCapital, assets);
    const equityToLiabilities = div(stockholdersEquity, liabilities);
    return { rev, revP, revGrowth, grossMargin, grossMarginPrev, rdIntensity, sgaIntensity,
             wcToAssets, equityToLiabilities, mscoreVal: mscore, zscoreVal: zscore,
             niMargin, assetGrowth, cashRatio, fcf, fcfMargin, tata, dsri, sgi,
             mscore, zscore, workingCapital, liabilities, stockholdersEquity,
             cash, assets, ar, ni, cfo, capex };
  }

  // ── Universal risk taxonomy (replaces the per-industry templates) ──────
  // Every entity is assessed against the SAME categories; a category's score
  // is derived only from evidence about THAT entity. No industry baseline,
  // no hand-set base score. Mirrors agentic-tools/risk_taxonomy.py (the
  // authoritative scorer when the MCP server is reachable) so Live-JS and
  // Mock mode score identically.
  //
  //   likelihood = clamp(1.0 + Σ evidence points, 0.5, 5.0)
  //   score      = impact(category) × likelihood           (0-25; R≥15, A≥9)
  //
  // A category with NO evidence input is "not assessed": it is withheld from
  // profile.risks (so RAG counts, appetite, audit scope and every score-
  // reading screen only ever see real scores) and listed in
  // profile.notAssessed so Gate 1 / reports can show the coverage gap.
  const BASE_LIKELIHOOD = 1.0;
  const ev = (source, label, points, o = {}) =>
    ({ source, label, points: +(+points).toFixed(2), trend: !!o.trend, observed: o.observed ?? null });
  const _gt = (v, tiers, label, o = {}) => {
    if (v == null || Number.isNaN(v)) return null;
    const d = o.digits ?? 3;
    for (const [thr, pts] of [...tiers].sort((a, b) => b[0] - a[0]))
      if (v > thr) return ev(o.source || 'XBRL', `${label} ${v.toFixed(d)} > ${thr}`, pts, { trend: o.trend, observed: v });
    return ev(o.source || 'XBRL', `${label} ${v.toFixed(d)} within normal range`, 0, { trend: o.trend, observed: v });
  };
  const _lt = (v, tiers, label, o = {}) => {
    if (v == null || Number.isNaN(v)) return null;
    const d = o.digits ?? 3;
    for (const [thr, pts] of [...tiers].sort((a, b) => a[0] - b[0]))
      if (v < thr) return ev(o.source || 'XBRL', `${label} ${v.toFixed(d)} < ${thr}`, pts, { trend: o.trend, observed: v });
    return ev(o.source || 'XBRL', `${label} ${v.toFixed(d)} within normal range`, 0, { trend: o.trend, observed: v });
  };
  const _ok = a => a.filter(Boolean);

  // Ratio → evidence rules (same thresholds as risk_taxonomy.py). `r` is the
  // camelCase ratios object computeRatios() / the MCP mapper produce.
  const RATIO_RULES = {
    'TX-FRQ': r => _ok([
      _gt(r.tata, [[0.10, 2.0], [0.05, 1.2], [0.02, 0.5]], 'Total accruals / assets'),
      _gt(r.dsri, [[1.20, 1.0], [1.05, 0.4]], 'Receivables-to-sales index (DSRI)', { trend: true, digits: 2 }),
      r.mscoreVal != null ? _gt(r.mscoreVal, [[-1.78, 1.5], [-2.22, 0.7]], 'Beneish M-Score', { source: 'Beneish', digits: 2 }) : null,
    ]),
    'TX-LIQ': r => _ok([
      _lt(r.cashRatio, [[0.05, 1.5], [0.10, 0.8]], 'Cash / assets'),
      _lt(r.fcfMargin, [[-0.10, 1.5], [0.0, 0.7]], 'FCF margin'),
      r.zscoreVal != null ? _lt(r.zscoreVal, [[1.1, 2.0], [2.6, 0.8]], "Altman Z''-Score", { source: 'Altman', digits: 2 }) : null,
      _lt(r.wcToAssets, [[0.0, 0.5]], 'Working capital / assets'),
    ]),
    'TX-PRF': r => _ok([
      (r.grossMargin != null && r.grossMarginPrev != null)
        ? _lt(r.grossMargin - r.grossMarginPrev, [[-0.03, 1.2], [-0.01, 0.5]], 'Gross-margin change YoY', { trend: true }) : null,
      _lt(r.niMargin, [[0.0, 1.2], [0.03, 0.4]], 'Net margin'),
    ]),
    'TX-REV': r => _ok([
      _lt(r.revGrowth, [[-0.10, 2.0], [-0.05, 1.2], [0.0, 0.6]], 'Revenue growth YoY', { trend: true }),
      _gt(r.sgi, [[1.5, 0.6]], 'Sales growth index (SGI)', { trend: true, digits: 2 }),
    ]),
    'TX-CAP': r => _ok([
      _lt(r.equityToLiabilities, [[0.1, 1.5], [0.3, 0.8]], 'Equity / liabilities'),
      _gt(r.lvgi, [[1.2, 0.8]], 'Leverage index (LVGI)', { trend: true, digits: 2 }),
    ]),
  };

  // Presentation + audit-response text per category. Narrative text is built
  // from the evidence, never from industry boilerplate.
  const CATALOG = [
    { id: 'TX-FRQ', name: 'Financial Reporting Quality', category: 'Financial Reporting',
      obj: 'Test accrual quality, revenue-recognition cut-off and the drivers behind any restatement, impairment or non-reliance signal',
      controls: ['FRQ-101 Period-end close and reconciliation review', 'FRQ-105 Revenue recognition cut-off testing', 'FRQ-110 Journal-entry authorization and override monitoring'],
      mapFinding: 'Reporting-quality indicators (accruals, receivables, M-Score) or filings signal elevated misstatement risk',
      mapAction: 'Targeted substantive testing of accruals and revenue; management-override review; remediation tracked to Audit Committee',
      mapOwner: 'Chief Financial Officer / Controller', mapSuccessCriteria: 'Indicators back within normal range or each driver explained and evidenced; no unremediated deficiencies', reductionPct: 12 },
    { id: 'TX-LIQ', name: 'Liquidity & Going Concern', category: 'Liquidity',
      obj: 'Assess liquidity runway, covenant headroom and the going-concern basis of preparation',
      controls: ['LIQ-201 13-week cash forecast review', 'LIQ-205 Covenant compliance monitoring', 'LIQ-210 Going-concern assessment memo'],
      mapFinding: 'Liquidity / solvency indicators or events indicate elevated going-concern risk',
      mapAction: 'Refresh cash-flow forecast and covenant model; document management going-concern assessment; agree contingency funding plan',
      mapOwner: 'Chief Financial Officer / Treasurer', mapSuccessCriteria: 'Forecast shows adequate runway under downside case; covenant headroom documented; assessment reviewed by Audit Committee', reductionPct: 15 },
    { id: 'TX-PRF', name: 'Profitability & Margin Pressure', category: 'Financial Reporting',
      obj: 'Analyse margin drivers (price, mix, cost, inventory) and the reliability of cost allocations and reserves',
      controls: ['PRF-301 Margin bridge review', 'PRF-305 Inventory and cost-allocation testing'],
      mapFinding: 'Margin trend or profitability is below expectation without a documented driver analysis',
      mapAction: 'Quarterly margin-bridge reviewed by IA; reserve and cost-allocation methodology validated',
      mapOwner: 'Chief Financial Officer', mapSuccessCriteria: 'Margin drivers documented each quarter; reserve methodology validated', reductionPct: 10 },
    { id: 'TX-REV', name: 'Revenue Growth & Demand', category: 'Revenue',
      obj: 'Review revenue trend, concentration and the sustainability of reported growth or decline',
      controls: ['REV-401 Revenue trend and concentration analysis', 'REV-405 Bookings / backlog reconciliation'],
      mapFinding: 'Revenue trend deviates materially from prior year without a documented demand analysis',
      mapAction: 'Quarterly demand and concentration review; reconcile bookings to recognised revenue',
      mapOwner: 'Chief Revenue Officer / CFO', mapSuccessCriteria: 'Trend explained and reflected in forecast; concentration within policy', reductionPct: 10 },
    { id: 'TX-CAP', name: 'Capital Structure & Financial Obligations', category: 'Financial Reporting',
      obj: 'Review leverage, new obligations, off-balance-sheet arrangements and covenant exposure',
      controls: ['CAP-501 Debt and covenant register', 'CAP-505 Off-balance-sheet obligation register', 'CAP-510 Equity issuance log'],
      mapFinding: 'Leverage or obligation events are not tracked against covenant headroom in the risk register',
      mapAction: 'Trigger GRC review on 8-K Items 1.01 / 2.03 / 2.04 / 3.02; quarterly obligation-vs-covenant review',
      mapOwner: 'Treasurer / Chief Legal Officer', mapSuccessCriteria: 'Obligation register complete; covenant headroom reported quarterly', reductionPct: 12 },
    { id: 'TX-CYB', name: 'Cybersecurity & Data Protection', category: 'Cybersecurity',
      obj: 'Assess cyber incident response, materiality-determination process and Item 106 / 1C disclosure readiness',
      controls: ['CYB-601 Incident response and escalation', 'CYB-605 Materiality determination procedure', 'CYB-610 Third-party access review'],
      mapFinding: 'Cyber incident or threat signal not linked to a documented materiality and disclosure decision',
      mapAction: 'Document 8-K Item 1.05 decision path; tabletop the incident-to-disclosure workflow; review board cyber oversight',
      mapOwner: 'Chief Information Security Officer / General Counsel', mapSuccessCriteria: 'Materiality procedure tested; disclosure decision log maintained', reductionPct: 12 },
    { id: 'TX-GOV', name: 'Board & Executive Governance', category: 'Governance',
      obj: 'Review board and executive turnover, tenure / refreshment, succession coverage and committee continuity',
      controls: ['GOV-701 Director and officer change log', 'GOV-705 Succession plan coverage', 'GOV-710 Board refreshment and evaluation'],
      mapFinding: 'Leadership change or board tenure profile is not tracked as a governance risk with documented succession and refreshment',
      mapAction: 'Link 8-K Item 5.02 events to the register; document C-suite and committee-chair succession; adopt / confirm a board refreshment policy',
      mapOwner: 'General Counsel / Board Chair', mapSuccessCriteria: 'Succession documented for all C-suite roles; refreshment policy reviewed annually; KRI reported at each Audit Committee', reductionPct: 8 },
    { id: 'TX-AUD', name: 'Audit Oversight & Internal Control', category: 'Audit Oversight',
      obj: 'Assess auditor change circumstances, auditor tenure and partner rotation, ICFR conclusions and Audit Committee oversight',
      controls: ['AUD-801 Auditor independence and tenure monitoring', 'AUD-805 ICFR deficiency evaluation', 'AUD-810 Audit Committee pre-approval of services'],
      mapFinding: 'Auditor change, ICFR signal or auditor-tenure indicator needs documented Audit Committee evaluation',
      mapAction: 'Audit Committee to document the auditor-change rationale, review any Item 304 matters, and confirm partner-rotation compliance (5-yr lead / 7-yr other)',
      mapOwner: 'Audit Committee Chair / Chief Accounting Officer', mapSuccessCriteria: 'Rationale and rotation compliance minuted; no open ICFR deficiencies without remediation owner', reductionPct: 10 },
    { id: 'TX-REG', name: 'Regulatory, Legal & Enforcement', category: 'Compliance',
      obj: 'Review SEC comment-letter status, regulatory notices, listing compliance and legal contingencies',
      controls: ['REG-901 SEC comment-letter tracker', 'REG-905 Regulatory notice triage', 'REG-910 Legal contingency review'],
      mapFinding: 'Open or repeated regulator comments / notices are not tracked to resolution',
      mapAction: 'Track each comment through closure; assess whether prior-period disclosure needs amendment; report status to Audit Committee',
      mapOwner: 'General Counsel / Chief Compliance Officer', mapSuccessCriteria: 'All comment letters closed or on a documented plan; no unassessed regulatory notices', reductionPct: 10 },
    { id: 'TX-OPS', name: 'Operations & Supply Chain', category: 'Operational',
      obj: 'Review operational disruption, restructuring execution and supply-chain dependency',
      controls: ['OPS-1001 Supplier dependency register', 'OPS-1005 Restructuring cost and liability review'],
      mapFinding: 'Operational disruption or restructuring signal lacks a documented dependency and cost review',
      mapAction: 'Map critical suppliers and single points of failure; validate restructuring estimates',
      mapOwner: 'Chief Operating Officer', mapSuccessCriteria: 'Dependency register complete; restructuring liabilities validated', reductionPct: 10 },
    { id: 'TX-STR', name: 'Strategic Transactions & Concentration', category: 'Strategic',
      obj: 'Review acquisition / disposition accounting, integration risk and revenue or segment concentration',
      controls: ['STR-1101 Transaction accounting review', 'STR-1105 Concentration analysis'],
      mapFinding: 'Material transactions or concentration are not reflected in the risk register',
      mapAction: 'Post-transaction accounting review; annual concentration analysis by customer, product and geography',
      mapOwner: 'Chief Strategy Officer / CFO', mapSuccessCriteria: 'Purchase accounting validated; concentration analysis in annual IA report', reductionPct: 10 },
    { id: 'TX-MAC', name: 'Macro & Market Conditions', category: 'Strategic',
      obj: 'Assess exposure to the macro indicators currently moving against the entity and the adequacy of scenario planning',
      controls: ['MAC-1201 Macro indicator monitoring', 'MAC-1205 Scenario and stress-test refresh'],
      mapFinding: 'Adverse macro indicators are not reflected in scenario planning',
      mapAction: 'Refresh scenarios with current leading indicators; set indicator-based triggers',
      mapOwner: 'Chief Financial Officer', mapSuccessCriteria: 'Scenarios refreshed quarterly with indicator triggers', reductionPct: 8 },
    { id: 'TX-ESG', name: 'ESG & Climate Disclosure', category: 'ESG',
      obj: 'Review climate / sustainability disclosure accuracy and substantiation of public claims',
      controls: ['ESG-1301 Disclosure controls for sustainability data', 'ESG-1305 Claim substantiation review'],
      mapFinding: 'Sustainability disclosures lack documented data controls or substantiation',
      mapAction: 'Establish disclosure controls over ESG metrics; review public claims for substantiation',
      mapOwner: 'Chief Sustainability Officer / General Counsel', mapSuccessCriteria: 'Metrics under disclosure controls; claims substantiated', reductionPct: 8 },
    { id: 'TX-TRD', name: 'Trade & Sanctions Compliance', category: 'Trade Compliance',
      obj: 'Assess export-control, sanctions and restricted-party screening effectiveness',
      controls: ['TRD-1401 Restricted-party screening', 'TRD-1405 Export classification review'],
      mapFinding: 'Trade / sanctions signal is not matched to screening and classification controls',
      mapAction: 'Validate screening coverage and export classification; consider voluntary self-disclosure where warranted',
      mapOwner: 'Chief Compliance Officer', mapSuccessCriteria: 'Screening coverage validated; classification current', reductionPct: 10 },
  ];
  const CATALOG_BY_ID = Object.fromEntries(CATALOG.map(c => [c.id, c]));

  // Narrative built from the evidence the score rests on.
  function narrativeFromEvidence(name, items, score) {
    const top = [...items].filter(i => i.points > 0).sort((a, b) => b.points - a.points).slice(0, 3);
    if (!top.length) return `${name}: ${items.length} data point(s) reviewed, none adverse (score ${score}/25).`;
    return `${name} — score ${score}/25 driven by: ${top.map(i => `${i.label} [${i.source}]`).join('; ')}.`;
  }

  // Score one catalog category from its evidence list. Returns a risk row,
  // or null when there is no evidence at all ("not assessed").
  function scoreRow(cat, evidence) {
    if (!evidence || !evidence.length) return null;
    const impact = CATEGORY_IMPACT[cat.category] || 3;
    const total = evidence.reduce((s, e) => s + e.points, 0);
    const structural = evidence.filter(e => !e.trend).reduce((s, e) => s + e.points, 0);
    const lik = clamp(BASE_LIKELIHOOD + total, 0.5, 5.0);
    let score = +clamp(impact * lik, 0, 25).toFixed(1);
    // An evidence item may carry floor:'R' (8-K Item 1.03 bankruptcy).
    if (evidence.some(e => e.floor === 'R') && score < RAG_RED) score = RAG_RED;
    const base25 = +clamp(impact * clamp(BASE_LIKELIHOOD + structural, 0.5, 5.0), 0, 25).toFixed(1);
    return {
      id: cat.id, taxonomy_id: cat.id, name: cat.name, category: cat.category,
      impact, likelihood: +lik.toFixed(1), score, rag: ragOf(score),
      velocity: velOf(score, base25), ce: ceOf(score, base25), base: base25,
      inherent: +clamp(score + 2.5, score, 25.0).toFixed(1), residual: score,
      peer: score > base25 + 2.5 ? 'above' : score < base25 - 1.25 ? 'below' : 'in-line',
      hist: histOf(score, base25),
      assessed: true, evidence,
      narrative: narrativeFromEvidence(cat.name, evidence, score),
    };
  }

  // Ratio evidence for every category (JS mirror of risk_taxonomy._RATIO_RULES).
  function ratioEvidence(ratios) {
    const out = {};
    for (const [id, rule] of Object.entries(RATIO_RULES)) out[id] = ratios && Object.keys(ratios).length ? rule(ratios) : [];
    return out;
  }

  // Combine evidence maps ({txId: [items]}) and score the whole taxonomy.
  // Returns { risks (assessed only), notAssessed: [{id,name,category,reason}] }.
  function scoreTaxonomy(evidenceMaps) {
    const merged = {};
    for (const m of evidenceMaps) for (const [k, v] of Object.entries(m || {})) (merged[k] = merged[k] || []).push(...v);
    const risks = [], notAssessed = [];
    for (const cat of CATALOG) {
      const row = scoreRow(cat, merged[cat.id]);
      if (row) risks.push(row);
      else notAssessed.push({ id: cat.id, name: cat.name, category: cat.category,
        reason: 'No evidence available for this category in the current data sources' });
    }
    return { risks, notAssessed };
  }

  // ── Build Risk Array from ratios (Live-JS / Mock mode) ──────
  function buildRisks(ratios, extraEvidenceMaps = []) {
    return scoreTaxonomy([ratioEvidence(ratios), ...extraEvidenceMaps]);
  }

  // ── Build Objectives ────────────────────────────────────────
  function buildObjectives(risks, industry) {
    const sorted = [...risks].sort((a, b) => b.score - a.score).slice(0, 6);
    return sorted.map((r, i) => {
      // A segment/geography-specific risk (segment_risk_tool.py — id like
      // "SGG01C") has no matching template; there's no per-segment
      // objective template to borrow one from either (see
      // mapSegmentRisks' docstring in mcp-data.js for why). Falling back to
      // tmpl[0] here used to silently borrow an unrelated template's
      // objective text and controls list — worse than building the
      // objective from the risk's own name/narrative directly.
      const t = r.segment_name ? null : CATALOG_BY_ID[r.id];
      const priority = r.score >= 17 ? 'P1' : r.score >= 11 ? 'P2' : 'P3';
      return {
        id: `O-${String(i + 1).padStart(2, '0')}`,
        priority,
        objective: t?.obj || `Audit ${r.name}`,
        evidence_basis: (r.evidence || []).filter(e => e.points > 0).slice(0, 3).map(e => e.label),
        linked_risk: r.id,
        sprint: priority === 'P1' ? 1 : priority === 'P2' ? 2 : 3,
        hours: priority === 'P1' ? 120 : priority === 'P2' ? 80 : 40,
        controls: t?.controls || [],
        rationale: `${r.name} score ${r.score}/25 (${r.rag}), velocity ${r.velocity > 0 ? '+' : ''}${r.velocity}. ${(r.narrative || '').slice(0, 120)}…`,
        segment_type: r.segment_type || null,
        segment_name: r.segment_name || null,
      };
    });
  }

  // ── Build MAPs ───────────────────────────────────────────────
  function buildMAPs(risks, objectives) {
    return objectives.map((o, i) => {
      const r = risks.find(x => x.id === o.linked_risk);
      if (!r) return null;
      const tmpl = CATALOG_BY_ID[r.id];
      return {
        id: `MAP-${String(i + 1).padStart(2, '0')}`,
        finding: tmpl?.mapFinding || `${r.name} — control gap identified`,
        condition: r.narrative,
        root_cause: `Root cause analysis required — risk score ${r.score}/25 with velocity ${r.velocity > 0 ? '+' : ''}${r.velocity}.`,
        risk_impact: r.rag,
        linked_risk: r.id,
        action: tmpl?.mapAction || `Implement controls to reduce ${r.name} risk to target level`,
        owner: tmpl?.mapOwner || 'Chief Risk Officer',
        due_date: (() => { const now = new Date(); const curQ = Math.ceil((now.getMonth()+1)/3); const off = 1 + (r.rag === 'R' ? 0 : 1) + i; const tq = curQ + off; const dq = ((tq - 1) % 4) + 1; const dy = now.getFullYear() + Math.floor((tq - 1) / 4); return `${dy}-Q${dq}`; })(),
        completion_pct: Math.max(0, 30 - i * 5),
        success_criteria: tmpl?.mapSuccessCriteria || `${r.name} risk score reduced to target level; controls validated by IA`,
        reduction_pct: tmpl?.reductionPct || 12,
      };
    }).filter(Boolean);
  }

  // ── Build Scenarios ─────────────────────────────────────────
  function buildScenarios(risks, ratios, ticker, industry) {
    const topRed = risks.filter(r => r.rag === 'R').map(r => r.id);
    const topAmb = risks.filter(r => r.rag === 'A').map(r => r.id).slice(0, 2);
    const revStr  = ratios.revGrowth != null ? `${(ratios.revGrowth * 100).toFixed(1)}%` : 'n/a';
    const gmStr   = ratios.grossMargin != null ? `${(ratios.grossMargin * 100).toFixed(1)}%` : 'n/a';
    // revenue_at_risk_m: revenue not realized vs. prior year if scenario materialises (millions).
    // Each value is derived from the scenario's own revenue_impact_pct for consistency.
    // Only a negative impact (contraction) produces a positive at-risk figure; upside returns 0.
    const revM = ratios.rev != null ? ratios.rev / 1e6 : null;
    const bearRiskM  = revM != null ? Math.round(revM * 0.18)                                                          : null;
    const baseImpact = ratios.revGrowth ?? -0.03;
    const baseRiskM  = revM != null ? Math.max(0, Math.round(revM * -baseImpact))                                      : null;
    const bullImpact = ratios.revGrowth != null ? (ratios.revGrowth + 0.08) : 0.05;
    const bullRiskM  = revM != null ? Math.max(0, Math.round(revM * -bullImpact))                                      : 0;
    return [
      { id:'bear', name:`Bear — Dual risk materialisation: ${risks[0]?.name?.split('—')[0].trim() || 'Primary Risk'} + macro stress`,
        description: `${topRed.length > 0 ? `Red risks ${topRed.join(', ')} materialise simultaneously` : 'Top two amber risks elevate to red'}. Revenue contracts 15–25%, gross margin compressed ${ratios.grossMargin != null ? `from ${gmStr} to below ${(Math.max(0,ratios.grossMargin-0.12)*100).toFixed(1)}%` : 'materially'}. FCF turns negative; covenant headroom at risk.`,
        probability:'MEDIUM', revenue_impact_pct:-18, gross_margin_impact_bps:-380,
        revenue_at_risk_m: bearRiskM,
        runway_days:30, liquidity:'CONSTRAINED', kris_red: topRed.slice(0,3),
        recovery:'PROLONGED_5Q_PLUS', audit_focus:['Reserve/accrual adequacy','Covenant compliance','Going-concern disclosure'],
        vs_peers:`${ticker} most exposed relative to sector on ${risks[0]?.name?.split('—')[0] || 'primary risk category'}.`,
        assumptions:{ 'Rev Δ':'-18%', 'Margin Δ':'-380bps', 'Macro':'contractionary', 'Liquidity':'constrained' } },
      { id:'base', name:`Base — Managed risk profile; key controls hold`,
        description: `Risk scores stabilise with current controls effective. Revenue growth ${revStr}, gross margin near ${gmStr}. Primary risks remain elevated but within appetite. MAP implementation proceeds on schedule.`,
        probability:'HIGH', revenue_impact_pct: ratios.revGrowth!=null?Math.round(ratios.revGrowth*100):-3,
        gross_margin_impact_bps:-50, revenue_at_risk_m: baseRiskM,
        runway_days:60, liquidity:'SUFFICIENT', kris_red: topRed.slice(0,1),
        recovery:'MODERATE_3_4Q', audit_focus:['Top-3 risk remediation','MAP completion vs. due dates','Velocity trend monitoring'],
        vs_peers:`${ticker} in line with sector; focused execution on P1 MAPs required.`,
        assumptions:{ 'Rev Δ': revStr, 'Margin Δ':'−50bps', 'Controls':'hold', 'MAP':'on-track' } },
      { id:'bull', name:`Bull — Risk reduction + favourable macro`,
        description: `MAP implementation ahead of schedule; primary risks step down one RAG level. Revenue recovers ${ratios.revGrowth!=null&&ratios.revGrowth<0?'to flat/modest growth':'accelerates'}, gross margin expands. All covenant ratios comfortable.`,
        probability:'LOW', revenue_impact_pct: ratios.revGrowth!=null?Math.round((ratios.revGrowth+0.08)*100):5,
        gross_margin_impact_bps:150, revenue_at_risk_m: bullRiskM,
        runway_days:90, liquidity:'COMFORTABLE', kris_red:[],
        recovery:'NONE', audit_focus:['Control effectiveness validation','Revenue recognition on accelerated bookings'],
        vs_peers:`${ticker} outperforms sector on risk-adjusted basis if MAP programme executes.`,
        assumptions:{ 'Rev Δ':'+5–8% above base', 'MAP':'ahead of schedule', 'Macro':'expansionary', 'Liquidity':'comfortable' } },
    ];
  }

  // ── Grey Swan exogenous event library ──────────────────────
  // These are EXOGENOUS tail events — plausible but not yet in the risk register.
  // Each industry gets at least one event. The anchor risk from the register is used
  // only to provide context for *how* the external event propagates internally.
  const GREY_SWAN_EVENTS = {
    'Semiconductors': [
      { name: 'Taiwan Strait escalation — single-source fab supply cutoff',
        description: 'A credible military escalation in the Taiwan Strait triggers a halt to TSMC and downstream single-source fab shipments. Initial disruption appears manageable; within 30 days backlog exhaustion and inventory depletion accelerate the cascade to systemic supply failure — well beyond what current R-04 (Supply Chain) models.',
        catalysts: [
          'PLA military exercises within 40nm of Taiwan extend beyond scheduled window',
          'US Department of Commerce issues emergency export restriction on fab equipment',
          'Lead-time quotes from alternative fabs extend beyond 40 weeks — no viable substitute',
          'Single-source component registry review confirms >60% of critical BOM at risk',
        ] },
      { name: 'Sweeping US–China export controls — revenue cliff beyond current R-07 scope',
        description: 'Sweeping new BIS export controls targeting both advanced and legacy semiconductors extend far beyond current R-07 (Export Controls & Trade Compliance) scope, rendering a material portion of China-destined revenue unlicensable within 90 days.',
        catalysts: [
          'BIS Entity List expanded to include additional Chinese fabs and system integrators',
          'De minimis threshold for foreign-produced items reduced from 25% to 10%',
          'Customers preemptively cancel orders pending clarity on new ECCN classifications',
          'Legal opinion confirms >30% of China-directed revenue requires new EAR licences',
        ] },
    ],
    'Automotive OEM': [
      { name: 'Rapid EV demand collapse — stranded capacity and impairment cascade',
        description: 'Consumer EV adoption rates fall sharply below forecast as range anxiety, charging infrastructure gaps, and insurance cost spikes converge. EV-dedicated manufacturing lines face severe under-utilisation within 90 days, triggering impairment charges and stranded asset write-downs not captured in Bear scenario.',
        catalysts: [
          'Industry EV SAAR falls >30% below consensus for two consecutive months',
          'EV lease residual values drop sharply, tightening captive finance underwriting',
          'Federal EV tax credit eligibility narrowed, reducing effective consumer subsidy',
          'Peer OEMs announce EV programme deferrals, validating structural demand headwind',
        ] },
    ],
    'Software & Cloud': [
      { name: 'Hyperscaler outage or emergency AI-product ban — platform concentration shock',
        description: 'A prolonged multi-region cloud provider outage or an emergency regulatory ban on AI-dependent product features triggers immediate customer churn and SLA breach claims, cascading into reputational and legal escalation that falls entirely outside the current risk register scope.',
        catalysts: [
          'Cloud provider SLA breach triggers contractual penalty clauses across enterprise base',
          'FTC or EU regulator issues emergency order against core AI feature set',
          'Enterprise customers invoke force-majeure to exit multi-year contracts',
          'Cyber incident at primary SaaS delivery layer triggers SOC 2 scope restatement',
        ] },
    ],
    'Financial Services': [
      { name: 'Credit contagion event — correlated portfolio deterioration beyond stress models',
        description: 'A correlated credit shock (commercial real estate collapse, regional bank contagion, or sovereign downgrade) triggers simultaneous CECL reserve increases, delinquency spikes, and deposit outflows that exceed modelled stress scenarios by a factor the current register does not capture.',
        catalysts: [
          'Fed emergency intervention signals systemic stress beyond prior DFAST bounds',
          'Correlated delinquency spike observed across multiple loan categories simultaneously',
          'Rating agency places key counterparty on negative credit watch',
          'Regulatory accelerated examination triggered by systemic early-warning alert',
        ] },
    ],
    'Healthcare & Pharma': [
      { name: 'FDA enforcement action or clinical hold — pipeline NPV collapse',
        description: 'An unexpected FDA Complete Response Letter or clinical hold on a lead programme triggers an immediate pipeline NPV collapse, revenue guidance withdrawal, and accelerated patent cliff exposure not yet reflected in any scenario.',
        catalysts: [
          'FDA issues CRL for flagship NDA citing manufacturing or clinical data deficiency',
          'Clinical hold placed on Phase 3 programme due to unexpected safety signal',
          'Generic manufacturer launches at-risk on anchor product pending appeal',
          'CMS announces IRA price negotiation for top-revenue product ahead of schedule',
        ] },
    ],
    'Generic': [
      { name: 'Regulatory enforcement shock — consent decree or criminal referral',
        description: 'An unexpected regulatory enforcement action (consent decree, cease-and-desist, or criminal referral) requires immediate operational changes, causing revenue disruption and legal cost escalation well beyond modelled compliance risk and outside the current risk register scope.',
        catalysts: [
          'Regulatory agency issues public notice of formal investigation',
          'Board notified of subpoena or document preservation order',
          'Key customer suspends orders pending regulatory outcome',
          'External legal counsel escalates matter to potential criminal referral',
        ] },
    ],
  };
  GREY_SWAN_EVENTS['Industrial & Manufacturing'] = GREY_SWAN_EVENTS['Generic'];
  GREY_SWAN_EVENTS['Retail & Consumer']           = GREY_SWAN_EVENTS['Generic'];
  GREY_SWAN_EVENTS['Energy & Resources']          = GREY_SWAN_EVENTS['Generic'];
  GREY_SWAN_EVENTS['Utilities']                   = GREY_SWAN_EVENTS['Generic'];

  // ── Build Grey Swan ─────────────────────────────────────────
  function buildGreySwan(risks, ratios, ticker, industry) {
    // Anchor = highest-velocity risk from the register the exogenous event would amplify.
    // Velocity ≥ 3 risks are the primary auto-escalation candidates per loop recommendations.
    const byVel = [...risks].sort((a, b) => (b.velocity - a.velocity) || (b.score - a.score));
    const anchor = byVel.find(r => r.velocity >= 3) || byVel.find(r => r.velocity >= 2) ||
                   byVel[0] || { id:'R-01', name:'Primary Risk', score:5.0, rag:'A', velocity:0 };
    // Co-activation partner: a second register risk the grey swan cascade would hit
    const partner = risks.find(r => r.id !== anchor.id && r.rag === 'R') ||
                    risks.find(r => r.id !== anchor.id && r.velocity >= 2) ||
                    risks.find(r => r.id !== anchor.id) || anchor;

    // Select industry-specific exogenous event; rotate by anchor id character to vary between tickers
    const events = GREY_SWAN_EVENTS[industry] || GREY_SWAN_EVENTS['Generic'];
    const event  = events[anchor.id.charCodeAt(anchor.id.length - 1) % events.length];

    // Revenue impact: grey swan exceeds Bear scenario (-18%) by design.
    // T+30: ~10% | T+60: ~22% | T+90: ~35% of annual revenue
    const annRevM = Number.isFinite(ratios.rev) ? ratios.rev / 1e6 : 2000;
    const imp30   = Math.round(annRevM * 0.10);
    const imp60   = Math.round(annRevM * 0.22);
    const imp90   = Math.round(annRevM * 0.35);
    const bearImp = Math.round(annRevM * 0.18); // for "exceeds Bear by $X" messaging

    // Risk score arc: the exogenous event starts GREEN (not in the register = unrecognised).
    // It MUST reach RED by T+90. Fixed arc keeps the progression consistent regardless of
    // what the anchor risk's current score happens to be.
    const ragAt  = s => s >= 15 ? 'R' : s >= 9 ? 'A' : 'G';
    const gsStart = 4.5;  // unrecognised exogenous event — GREEN
    const gs30    = 9.5;  // signal detected — crosses into AMBER
    const gs60    = 13.5; // cascade active — high AMBER
    const gsEnd   = 18.0; // systemic — solidly RED

    const anchorVelStr = anchor.velocity > 0 ? `+${anchor.velocity}` : `${anchor.velocity}`;

    return {
      id: 'grey-swan-gs',
      name: event.name,
      risk_id: anchor.id, risk_name: anchor.name,
      starting_rag: 'G', starting_score: gsStart,
      ending_rag: 'R', ending_score: gsEnd,
      peak_impact_m: imp90,
      revenue_impact_pct: -35,
      probability: 'LOW · plausible',
      headline: `${event.name.split('—')[0].trim()} amplifies ${anchor.name?.split('—')[0].trim()} (score ${anchor.score}/25, velocity ${anchorVelStr}) to systemic disruption`,
      description: event.description,
      catalysts: event.catalysts,
      impacts_at_max: [
        `Revenue at risk at T+90: ~$${imp90}M (≈35% of annual revenue — exceeds Bear scenario by ~$${imp90 - bearImp}M)`,
        `${anchor.name?.split('—')[0].trim()} score amplifies from ${anchor.score}/25 to ~${Math.min(25, anchor.score + 7).toFixed(1)}/25 under exogenous pressure`,
        `${partner.name?.split('—')[0].trim()} co-activates as cascade propagates — dual-risk crystallisation`,
        'External crisis management, regulatory engagement, and investor communication required simultaneously',
      ],
      early_warnings: [
        `${anchor.name?.split('—')[0].trim()} velocity crosses 2.5 — grey swan auto-escalation threshold triggered`,
        `External signals for "${event.name.split('—')[0].trim()}" increase in frequency across news and regulatory feeds`,
        `Related KRI breaches 80% of IA escalation threshold within 21 days`,
        `MAP completion for linked remediation falls >30 days behind due date`,
      ],
      mitigations: [
        `Stress-test ${anchor.name?.split('—')[0].trim()} under grey swan bounds (−35% revenue) before next cycle`,
        `Commission bespoke exogenous scenario build for "${event.name.split('—')[0].trim()}"`,
        `Set velocity threshold trigger at 2.5 to auto-escalate grey swan candidates to monthly review`,
        `Engage specialist risk advisor immediately if any catalyst signal is observed`,
      ],
      timeline: [
        { t:'T+0',  label:'Unrecognised',    score:gsStart, rag:ragAt(gsStart), likelihood:0.05, impact_$m:0,
          impact:`${event.name.split('—')[0].trim()} — exogenous event not yet in risk register`,
          signals:[`Score ${gsStart}/25`,'Not modelled in current scenarios',`Anchor ${anchor.id} vel ${anchorVelStr}`],
          action:'Assign grey swan owner; begin external signal monitoring' },
        { t:'T+30', label:'Signal detected',  score:gs30,    rag:ragAt(gs30),   likelihood:0.12, impact_$m:imp30,
          impact:'First catalyst confirmed; revenue at risk reaches $' + imp30 + 'M (~10% of annual); KRI breach at 80% threshold',
          signals:[`Score ${gs30}/25`,'First catalyst observed','KRI escalation flag raised'],
          action:'Emergency scenario review; AC notified; MAP acceleration ordered' },
        { t:'T+60', label:'Cascade active',   score:gs60,    rag:ragAt(gs60),   likelihood:0.22, impact_$m:imp60,
          impact:`${partner.name?.split('—')[0].trim()} co-activates; $${imp60}M at risk (~22% of annual); dual-risk escalation underway`,
          signals:[`Score ${gs60}/25`,'Dual-risk active','Revenue guidance under review'],
          action:'External specialist engaged; Board briefed; crisis protocol activated' },
        { t:'T+90', label:'Systemic',         score:gsEnd,   rag:ragAt(gsEnd),  likelihood:0.30, impact_$m:imp90,
          impact:`Full crystallisation — $${imp90}M revenue at risk (35% of annual), exceeding Bear scenario by ~$${imp90 - bearImp}M`,
          signals:[`Score ${gsEnd}/25`,'Material finding','Crisis management active'],
          action:'Structured remediation plan; investor communication; regulatory engagement' },
      ],
    };
  }

  // ── Build Reverse Stress Test ────────────────────────────────
  // Unlike Bear/Base/Bull/Grey Swan (which project forward from an event),
  // this starts from an unacceptable outcome and solves backward: how much
  // headroom exists today, and which register risks would need to jointly
  // crystallise to consume it.
  function buildReverseStressTest(risks, ratios, ticker, industry) {
    const BREACH_FCF_MARGIN   = -0.12;  // FCF margin treated as a going-concern/covenant-style trigger
    const REV_SENSITIVITY     = 0.004;  // ~0.4pt FCF-margin decline per 1pt revenue decline (operating leverage proxy)
    const MARGIN_SENSITIVITY  = 0.007;  // ~0.7pt FCF-margin decline per 100bps gross-margin compression

    const currentFcfMargin = ratios.fcfMargin ?? -0.02;
    const alreadyBreached  = currentFcfMargin <= BREACH_FCF_MARGIN;
    const gap = Math.max(0, currentFcfMargin - BREACH_FCF_MARGIN); // FCF-margin points of headroom remaining

    // Two isolated paths to breach — report whichever needs the smaller shock,
    // since that is the more urgent vector to monitor.
    const revOnlyPct    = +(gap / REV_SENSITIVITY).toFixed(1);
    const marginOnlyBps = Math.round((gap / MARGIN_SENSITIVITY) * 100);
    const primaryVector = revOnlyPct <= marginOnlyBps / 100 ? 'revenue' : 'margin';

    const byScore = arr => [...arr].sort((a, b) => b.score - a.score);
    const revDrivers    = risks.filter(r => ['Revenue', 'Supply', 'Trade Compliance'].includes(r.category));
    const marginDrivers = risks.filter(r => ['Financial Reporting', 'Operational', 'Compliance'].includes(r.category));
    const pool = primaryVector === 'revenue' ? revDrivers : marginDrivers;
    const contributors = (pool.length ? byScore(pool) : byScore(risks)).slice(0, 3);

    const scoreSum = contributors.reduce((s, r) => s + r.score, 0) || 1;
    const contributingRisks = contributors.map(r => ({
      id: r.id, name: r.name.split('—')[0].trim(), rag: r.rag, score: r.score,
      share: +((r.score / scoreSum) * 100).toFixed(0),
    }));

    return {
      id: 'reverse-stress',
      breakpoint: {
        label: 'Going-concern / covenant-style trigger',
        definition: `FCF margin falls to ${(BREACH_FCF_MARGIN * 100).toFixed(0)}% or below`,
      },
      already_breached: alreadyBreached,
      current_fcf_margin_pct: +(currentFcfMargin * 100).toFixed(1),
      headroom_pts: +(gap * 100).toFixed(1),
      primary_vector: primaryVector,
      required_shock: {
        revenue_decline_pts: revOnlyPct,
        margin_compression_bps: marginOnlyBps,
      },
      narrative: alreadyBreached
        ? `${ticker} is already at or beyond the going-concern trigger on current FCF margin (${(currentFcfMargin * 100).toFixed(1)}%) — this is a live finding, not a forward scenario.`
        : `From today's FCF margin of ${(currentFcfMargin * 100).toFixed(1)}%, ${ticker} has ${(gap * 100).toFixed(1)}pts of headroom before crossing the going-concern trigger. That headroom is consumed by either a further ${revOnlyPct}pt revenue decline alone, or ${marginOnlyBps}bps of additional gross-margin compression alone — whichever arrives first is the vector to monitor.`,
      contributing_risks: contributingRisks,
      monitoring_kris: contributors.map(r => `${r.id} velocity ≥ +2 (currently ${r.velocity > 0 ? '+' : ''}${r.velocity})`),
      audit_focus: [
        'Covenant headroom recalculation using latest trailing-twelve-month FCF',
        'Going-concern disclosure adequacy if headroom is less than two quarters at current burn trend',
        primaryVector === 'revenue'
          ? 'Revenue recognition and backlog quality — primary breach vector'
          : 'Accrual and reserve adequacy — primary breach vector',
      ],
    };
  }

  // ── Value at Risk / Conditional Value at Risk ────────────────
  // Reuses the same Monte Carlo revenue engine that drives the forecast
  // confidence band (_monteCarloRevenue below), but reports the tail of the
  // simulated distribution instead of just the 10th/90th percentile per quarter.
  function buildVarCvar(ratios, forecasts, ticker) {
    const hist = forecasts?.revenue?.history || [];
    const lastV = hist.length ? hist[hist.length - 1].v : (Number.isFinite(ratios.rev) ? ratios.rev / 1e6 : 1000);
    const trend = ratios.revGrowth ?? 0;
    const qGrowth = Math.pow(1 + trend, 0.25) - 1;
    const volatility = _qoqVolatility(hist);
    const horizonQ = 4;
    const nSims = 2000; // higher than the forecast band's 500 sims for a stabler tail estimate
    const sim = _monteCarloRevenue(lastV, qGrowth, volatility, horizonQ, nSims);
    const sorted = [...sim.finalVals].sort((a, b) => a - b);
    const pct = p => sorted[clamp(Math.round(p * (sorted.length - 1)), 0, sorted.length - 1)];

    const p5 = pct(0.05), p50 = pct(0.50), p95 = pct(0.95), p1 = pct(0.01);
    const tail95 = sorted.slice(0, Math.max(1, Math.round(sorted.length * 0.05)));
    const tail99 = sorted.slice(0, Math.max(1, Math.round(sorted.length * 0.01)));
    const cvar95 = lastV - (tail95.reduce((a, b) => a + b, 0) / tail95.length);
    const cvar99 = lastV - (tail99.reduce((a, b) => a + b, 0) / tail99.length);

    const nBins = 22;
    const min = sorted[0], max = sorted[sorted.length - 1];
    const binW = (max - min) / nBins || 1;
    const histogram = Array.from({ length: nBins }, (_, i) => ({ mid: Math.round(min + (i + 0.5) * binW), count: 0 }));
    sim.finalVals.forEach(v => {
      let idx = Math.floor((v - min) / binW);
      idx = clamp(idx, 0, nBins - 1);
      histogram[idx].count++;
    });

    return {
      id: 'var-cvar',
      ticker,
      horizon_quarters: horizonQ,
      n_sims: nSims,
      volatility_pct: +(volatility * 100).toFixed(1),
      base_revenue_m: Math.round(lastV),
      p5_m: Math.round(p5), p50_m: Math.round(p50), p95_m: Math.round(p95),
      var_95_m: Math.round(lastV - p5),
      var_99_m: Math.round(lastV - p1),
      cvar_95_m: Math.round(cvar95),
      cvar_99_m: Math.round(cvar99),
      prob_decline: sim.probDecline,
      histogram,
      assumptions: [
        `${nSims.toLocaleString()} Monte Carlo simulations over a ${horizonQ}-quarter horizon, seeded from the same volatility model used in the Revenue Forecast confidence band`,
        `Quarterly volatility of ${(volatility * 100).toFixed(1)}% derived from trailing revenue history (6% fallback when fewer than 3 quarters of history exist)`,
        `Growth path compounds the latest annual trend (${(trend * 100).toFixed(1)}%) into a constant quarterly rate — no mean reversion or seasonality is applied`,
        'VaR/CVaR are expressed as a $ revenue decline from the current run-rate over the horizon, not a P&L, EPS, or cash impact',
        'Simulated returns are drawn i.i.d. from a normal distribution (Box-Muller transform) — real revenue shocks are typically fatter-tailed and autocorrelated, so these figures understate true tail risk',
      ],
    };
  }

  // ── Sensitivity / Tornado Analysis ───────────────────────────
  // Shocks one financial-ratio input at a time (others held constant) and
  // rebuilds the risk register with buildRisks() to measure how much the total
  // portfolio score moves — reuses the real scoring logic rather than an
  // approximation, so results stay consistent with the live risk register.
  function buildSensitivity(ratios, ticker, industry) {
    const baseRisks = buildRisks(ratios).risks;
    const baselineScore = +baseRisks.reduce((s, r) => s + r.score, 0).toFixed(1);

    const pct1 = v => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(0)}pt`;
    const factors = [
      { key: 'revGrowth',   label: 'Revenue Growth',           down: -0.10, up: 0.10, fmt: pct1 },
      { key: 'grossMargin', label: 'Gross Margin',             down: -0.05, up: 0.05, fmt: pct1 },
      { key: 'fcfMargin',   label: 'FCF Margin',                down: -0.05, up: 0.05, fmt: pct1 },
      { key: 'mscore',      label: 'Earnings Quality (M-Score)', down: -0.5, up: 0.5, fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(2)}` },
      { key: 'cashRatio',   label: 'Cash Ratio',                down: -0.05, up: 0.05, fmt: pct1 },
      { key: 'assetGrowth', label: 'Asset Growth',              down: -0.05, up: 0.05, fmt: pct1 },
    ];

    const rows = factors.map(f => {
      const base = ratios[f.key] ?? 0;
      const downRatios = { ...ratios, [f.key]: base + f.down };
      const upRatios   = { ...ratios, [f.key]: base + f.up };
      const downScore = buildRisks(downRatios).risks.reduce((s, r) => s + r.score, 0);
      const upScore   = buildRisks(upRatios).risks.reduce((s, r) => s + r.score, 0);
      const downDelta = +(downScore - baselineScore).toFixed(1);
      const upDelta   = +(upScore - baselineScore).toFixed(1);
      return {
        key: f.key, label: f.label,
        down_shock_label: f.fmt(f.down), up_shock_label: f.fmt(f.up),
        down_delta: downDelta, up_delta: upDelta,
        swing: +(Math.abs(downDelta) + Math.abs(upDelta)).toFixed(1),
        low: Math.min(downDelta, upDelta, 0), high: Math.max(downDelta, upDelta, 0),
      };
    }).sort((a, b) => b.swing - a.swing);

    return {
      id: 'sensitivity',
      baseline_score: baselineScore,
      rows,
      assumptions: [
        'Impact = change in total portfolio risk score (sum of all register risk scores, each 0-25) when a single input is shocked in isolation, all other inputs held at current levels',
        'Shock magnitudes are fixed, illustrative ranges (e.g. ±10pts revenue growth, ±5pts margin) representing a plausible one-notch macro/operational move — not statistically derived confidence intervals',
        'Factors are shocked one at a time and do not capture correlation between inputs — see the Multi-Factor Stress Test tab for combined shocks',
        'Ranked by total swing (|downside delta| + |upside delta|); the top row is what the current risk score is most sensitive to',
      ],
    };
  }

  // ── Multi-Factor Stress Test ─────────────────────────────────
  // Extends the Reverse Stress Test's single-vector breakpoint logic to
  // simultaneous revenue + margin shocks, reusing its sensitivity coefficients
  // so results are directly comparable to that card.
  function buildMultiFactorStress(risks, ratios, ticker, industry) {
    const BREACH_FCF_MARGIN  = -0.12; // mirrors buildReverseStressTest's going-concern trigger
    const REV_SENSITIVITY    = 0.004; // mirrors buildReverseStressTest
    const MARGIN_SENSITIVITY = 0.007; // mirrors buildReverseStressTest

    const baseFcfMargin = ratios.fcfMargin ?? -0.02;
    const baseRevM = Number.isFinite(ratios.rev) ? ratios.rev / 1e6 : 1000;

    const factorScenarios = [
      { id: 'revenue_only',      label: 'Revenue Shock Only',            rev_decline_pts: 15, margin_compression_bps: 0 },
      { id: 'margin_only',       label: 'Margin Shock Only',             rev_decline_pts: 0,  margin_compression_bps: 300 },
      { id: 'combined_moderate', label: 'Combined — Moderate',           rev_decline_pts: 12, margin_compression_bps: 200 },
      { id: 'combined_severe',   label: 'Combined — Severe (CCAR-style)', rev_decline_pts: 22, margin_compression_bps: 450 },
    ];

    const rows = factorScenarios.map(s => {
      const fcfMarginImpact = -(s.rev_decline_pts * REV_SENSITIVITY) - ((s.margin_compression_bps / 100) * MARGIN_SENSITIVITY);
      const stressedFcfMargin = +(baseFcfMargin + fcfMarginImpact).toFixed(4);
      return {
        ...s,
        stressed_fcf_margin_pct: +(stressedFcfMargin * 100).toFixed(1),
        revenue_at_risk_m: Math.round(baseRevM * (s.rev_decline_pts / 100)),
        breaches_going_concern: stressedFcfMargin <= BREACH_FCF_MARGIN,
        headroom_remaining_pts: +((stressedFcfMargin - BREACH_FCF_MARGIN) * 100).toFixed(1),
      };
    });

    const byScore = arr => [...arr].sort((a, b) => b.score - a.score);
    const contributingRisks = byScore(risks).slice(0, 4).map(r => ({
      id: r.id, name: r.name.split('—')[0].trim(), rag: r.rag, score: r.score,
    }));

    return {
      id: 'multi-factor-stress',
      base_fcf_margin_pct: +(baseFcfMargin * 100).toFixed(1),
      breach_threshold_pct: +(BREACH_FCF_MARGIN * 100).toFixed(1),
      rows,
      contributing_risks: contributingRisks,
      assumptions: [
        'Combined scenarios apply a revenue decline and a gross-margin compression simultaneously, reusing the Reverse Stress Test\'s sensitivity coefficients (≈0.4pt FCF-margin decline per 1pt revenue decline; ≈0.7pt per 100bps margin compression) so results are directly comparable to that card',
        'The four shock magnitudes are fixed illustrative levels, not derived from a formal correlation matrix between revenue and margin — this is a stylised combined-shock view, not a calibrated joint distribution',
        `Going-concern / covenant-style trigger held at the same ${(BREACH_FCF_MARGIN * 100).toFixed(0)}% FCF margin threshold used in the Reverse Stress Test`,
        'Single-pass shock only — does not model second-order effects (e.g. a covenant breach triggering a credit downgrade that triggers further margin pressure)',
      ],
    };
  }

  // ── Liquidity / Covenant Runway ──────────────────────────────
  // Projects cash forward under three FCF-margin paths and reports the first
  // quarter each path depletes cash or crosses the going-concern trigger.
  function buildLiquidityRunway(ratios, ticker) {
    const BREACH_FCF_MARGIN = -0.12; // mirrors buildReverseStressTest's going-concern trigger
    const cashM = Number.isFinite(ratios.cash) ? ratios.cash / 1e6
      : (Number.isFinite(ratios.assets) ? (ratios.assets / 1e6) * 0.15 : 200);
    const revM  = Number.isFinite(ratios.rev) ? ratios.rev / 1e6 : 1000;
    const qRevM = revM / 4;
    const baseFcfMargin = ratios.fcfMargin ?? -0.02;
    const horizonQ = 8;

    function project(fcfMarginAt) {
      let cash = cashM;
      const points = [];
      for (let i = 0; i < horizonQ; i++) {
        const m = fcfMarginAt(i);
        cash += qRevM * m;
        points.push({ q: i + 1, cash_m: Math.round(cash), fcf_margin_pct: +(m * 100).toFixed(1) });
      }
      const depletionIdx = points.findIndex(p => p.cash_m <= 0);
      const breachIdx = points.findIndex(p => p.fcf_margin_pct / 100 <= BREACH_FCF_MARGIN);
      return { points, cash_depletion_quarter: depletionIdx === -1 ? null : depletionIdx + 1, covenant_breach_quarter: breachIdx === -1 ? null : breachIdx + 1 };
    }

    const basePath   = project(() => baseFcfMargin);
    const stressPath = project(i => baseFcfMargin - 0.02 * Math.min(i + 1, 4));
    const severePath = project(i => baseFcfMargin - 0.04 * Math.min(i + 1, 4));

    return {
      id: 'liquidity-runway',
      starting_cash_m: Math.round(cashM),
      base_fcf_margin_pct: +(baseFcfMargin * 100).toFixed(1),
      breach_threshold_pct: +(BREACH_FCF_MARGIN * 100).toFixed(1),
      horizon_quarters: horizonQ,
      scenarios: [
        { id: 'base',   label: 'Base — Current Trend',            ...basePath },
        { id: 'stress', label: 'Stress — Gradual Deterioration',  ...stressPath },
        { id: 'severe', label: 'Severe — Sharp Deterioration',    ...severePath },
      ],
      assumptions: [
        `Starting cash of $${Math.round(cashM)}M projected forward using quarterly FCF at the current run-rate (Base) and two stepped-deterioration paths (Stress: -2pts of FCF margin per quarter for 4 quarters then flat; Severe: -4pts per quarter for 4 quarters then flat)`,
        'Cash depletion quarter = first quarter the projected cash balance reaches zero; no revolver capacity, asset sales, or financing actions are modeled',
        `Covenant-style breach quarter uses the same ${(BREACH_FCF_MARGIN * 100).toFixed(0)}% FCF margin trigger as the Reverse Stress Test`,
        'Revenue is held flat at the current quarterly run-rate for this projection — see the VaR/CVaR tab for a revenue-distribution view',
      ],
    };
  }

  // ── Composite Early-Warning Indicator ────────────────────────
  // Blends four signals already computed elsewhere in the platform (risk
  // velocity, revenue momentum, Beneish earnings quality, macro/FRED direction)
  // into a single 0-100 systemic-stress score.
  function buildEarlyWarningIndicator(risks, ratios, forecasts, industry) {
    const totalScoreWeight = risks.reduce((s, r) => s + r.score, 0) || 1;
    const velocityWeighted = risks.reduce((s, r) => s + (r.velocity * r.score), 0) / totalScoreWeight; // ~ -1..3
    const velocityScore = clamp(((velocityWeighted + 1) / 4) * 100, 0, 100);

    const sentScore = forecasts?.sentiment?.score ?? 0; // -25..25, positive = improving
    const momentumScore = clamp(50 - (sentScore / 25) * 50, 0, 100);

    const mscore = ratios.mscore ?? -2.5;
    const band = mscore > -1.78 ? 'ELEVATED' : mscore > -2.22 ? 'GRAY ZONE' : 'NORMAL';
    const earningsQualityScore = band === 'ELEVATED' ? 85 : band === 'GRAY ZONE' ? 50 : 15;

    const fred = FRED_BY_INDUSTRY[industry] || FRED_BY_INDUSTRY['Generic'];
    const macroScore = fred.length ? (fred.filter(f => f.dir === 'CONTRACTIONARY').length / fred.length) * 100 : 0;

    const WEIGHTS = { velocity: 0.30, momentum: 0.25, earnings: 0.25, macro: 0.20 };
    const composite = +(velocityScore * WEIGHTS.velocity + momentumScore * WEIGHTS.momentum
      + earningsQualityScore * WEIGHTS.earnings + macroScore * WEIGHTS.macro).toFixed(1);
    const level = composite >= 65 ? 'RED' : composite >= 40 ? 'AMBER' : 'GREEN';

    // Synthetic trend: rescales each register risk's own historical score
    // trajectory (already computed by buildRisks -> hist) onto the 0-100 band,
    // anchored to the actual composite on the most recent point.
    const histLen = risks[0]?.hist?.length || 6;
    const trend = Array.from({ length: histLen }, (_, i) => {
      const avgHist = risks.reduce((s, r) => s + (r.hist?.[i] ?? r.score), 0) / (risks.length || 1);
      return +clamp((avgHist / 25) * 100, 0, 100).toFixed(1);
    });
    trend[trend.length - 1] = composite;

    return {
      id: 'early-warning-indicator',
      composite_score: composite,
      level,
      components: [
        { key: 'velocity', label: 'Risk Velocity',      score: +velocityScore.toFixed(1), weight: WEIGHTS.velocity, detail: `Score-weighted average velocity across ${risks.length} register risks` },
        { key: 'momentum', label: 'Revenue Momentum',   score: +momentumScore.toFixed(1), weight: WEIGHTS.momentum, detail: `QoQ momentum ${sentScore >= 0 ? '+' : ''}${sentScore} (${forecasts?.sentiment?.trend || 'STABLE'})` },
        { key: 'earnings', label: 'Earnings Quality',   score: +earningsQualityScore.toFixed(1), weight: WEIGHTS.earnings, detail: `Beneish M-score ${mscore.toFixed(2)} — ${band}` },
        { key: 'macro',    label: 'Macro Backdrop',     score: +macroScore.toFixed(1), weight: WEIGHTS.macro, detail: `${fred.filter(f => f.dir === 'CONTRACTIONARY').length} of ${fred.length} tracked FRED indicators reading contractionary` },
      ],
      trend,
      assumptions: [
        'Composite blends four inputs computed elsewhere in the platform: risk velocity (30%), revenue momentum (25%), Beneish earnings-quality band (25%), and macro/FRED direction (20%) — weights are illustrative, not statistically fitted',
        'Scored 0-100 where higher = more stressed; banded GREEN < 40, AMBER 40-64, RED ≥ 65',
        'Trend line approximates history by rescaling each register risk\'s own historical score trajectory — a directional proxy, not a recomputed historical composite',
        'A monitoring signal, not a standalone forecast — pair with the other Scenario Analysis tabs for magnitude and impact detail',
      ],
    };
  }

  // ── Historical analog library ────────────────────────────────
  // Real macro shocks with approximate realised deltas, replayed against the
  // current entity's ratios via this industry's FRED correlation sensitivities
  // (FRED_BY_INDUSTRY, defined below) rather than synthetic assumptions.
  const HISTORICAL_ANALOGS_LIB = [
    { id: 'gfc-2008', name: '2008 Global Financial Crisis', period: 'Q4 2008 – Q2 2009',
      macro_severity: 1.00,
      realized: { gdp_pts: -4.3, credit_spread_bps: 400, unemployment_pts: 5.0 },
      parallel: 'Credit contraction and demand collapse across nearly all sectors simultaneously.' },
    { id: 'covid-2020', name: '2020 COVID-19 Demand Shock', period: 'Q1 2020 – Q3 2020',
      macro_severity: 0.85,
      realized: { gdp_pts: -9.0, credit_spread_bps: 300, unemployment_pts: 11.0 },
      parallel: 'Sudden demand/supply-chain shock followed by a sharp policy-driven recovery.' },
    { id: 'rate-2022', name: '2022 Rate-Shock & Inflation Surge', period: 'Q1 2022 – Q4 2022',
      macro_severity: 0.55,
      realized: { gdp_pts: -0.6, credit_spread_bps: 150, unemployment_pts: 0.5, fed_funds_pts: 4.25 },
      parallel: 'Rapid rate repricing compresses valuations and raises financing costs without a demand collapse.' },
  ];

  // ── Build Historical Analogs ─────────────────────────────────
  function buildHistoricalAnalogs(ratios, ticker, industry) {
    const fredRows = FRED_BY_INDUSTRY[industry] || FRED_BY_INDUSTRY['Generic'];
    const avgAbsCorr = fredRows.reduce((s, f) => s + Math.abs(f.r), 0) / fredRows.length;
    const annRevM = Number.isFinite(ratios.rev) ? ratios.rev / 1e6 : 2000;

    return HISTORICAL_ANALOGS_LIB.map(h => {
      // Replay impact: historical severity × this industry's average macro sensitivity,
      // scaled so a GFC-grade shock at a typical ~0.65 avg correlation lands near -20%
      // revenue — consistent with (but distinct from) the Bear scenario's -18%.
      const revenueImpactPct = -Math.round(h.macro_severity * avgAbsCorr * 30);
      const revenueAtRiskM   = Math.round(annRevM * Math.abs(revenueImpactPct) / 100);
      return {
        id: h.id, name: h.name, period: h.period,
        revenue_impact_pct: revenueImpactPct,
        revenue_at_risk_m: revenueAtRiskM,
        realized_deltas: h.realized,
        parallel: h.parallel,
        sensitivity_basis: `${industry} avg |correlation| ${avgAbsCorr.toFixed(2)} across ${fredRows.length} tracked FRED series`,
        probability: h.macro_severity >= 0.9 ? 'LOW · historically rare'
                   : h.macro_severity >= 0.65 ? 'LOW-MEDIUM · has recurred'
                   : 'MEDIUM · cyclical',
      };
    });
  }

  // ── Build AI/Agent Governance Scenario ───────────────────────
  // Unlike Grey Swan (industry-specific exogenous event), this cascade shape is
  // cross-industry: any enterprise running AI agents against production tools
  // faces it. Anchored on the entity's own Cybersecurity/Operational/Compliance
  // risk with the highest score.
  function buildGovernanceScenario(risks, ratios, ticker, industry) {
    const candidates = risks.filter(r => ['Cybersecurity', 'Operational', 'Compliance'].includes(r.category));
    const byScore = [...(candidates.length ? candidates : risks)].sort((a, b) => b.score - a.score);
    const anchor = byScore[0] || { id: 'R-01', name: 'Primary Risk', score: 5.0, rag: 'A', velocity: 0 };

    const annRevM = Number.isFinite(ratios.rev) ? ratios.rev / 1e6 : 2000;
    const imp30 = Math.round(annRevM * 0.02);
    const imp60 = Math.round(annRevM * 0.06);
    const imp90 = Math.round(annRevM * 0.12);

    const ragAt = s => s >= 15 ? 'R' : s >= 9 ? 'A' : 'G';
    const gsStart = 3.5, gs30 = 8.0, gs60 = 12.5, gsEnd = 17.0;
    const anchorName = anchor.name?.split('—')[0].trim() || anchor.name;

    return {
      id: 'ai-governance',
      kind: 'AI GOVERNANCE',
      name: 'Ungoverned AI-agent tool access — cascading governance failure',
      risk_id: anchor.id, risk_name: anchor.name,
      starting_rag: 'G', starting_score: gsStart,
      ending_rag: 'R', ending_score: gsEnd,
      peak_impact_m: imp90,
      revenue_impact_pct: -12,
      probability: 'MEDIUM · rising with agent adoption',
      headline: `Unreviewed AI-agent tool calls escalate past ${anchorName} (score ${anchor.score}/25) into unauthorised production actions`,
      description: 'An AI agent (internal copilot, MCP-connected tool, or third-party integration) is granted broad tool access without governance holds. A sensitive or destructive tool call executes without human review, and by the time telemetry surfaces the pattern, multiple systems have already been touched.',
      catalysts: [
        'Agent or MCP integration is granted a new tool scope without a governance hold configured',
        'Prompt injection or over-broad instructions cause the agent to invoke a sensitive/destructive tool',
        'Bypass-keyword or suppression pattern (e.g. skip-ci, force, override) appears in agent-issued calls',
        'Governance hold queue backlog grows faster than human reviewers can clear it, and timeouts start auto-forwarding calls',
      ],
      impacts_at_max: [
        `Revenue/remediation exposure at T+90: ~$${imp90}M — incident response, customer notification, and control remediation costs`,
        `${anchorName} score amplifies from ${anchor.score}/25 to ~${Math.min(25, anchor.score + 7).toFixed(1)}/25`,
        'Regulatory/customer disclosure obligations triggered if the unauthorised action touched customer or financial data',
        'Board and audit committee briefing required; AI governance program credibility questioned',
      ],
      early_warnings: [
        'Sensitive-tool call rate crosses baseline by >2x week-over-week (per Dendrai UBO™ Governance Brain telemetry)',
        'Pending governance-hold queue depth exceeds reviewer throughput for >48 hours',
        'Repeated bypass-keyword flags from the same agent/session without escalation',
        'New MCP server or agent integration goes live without a documented governance-tier assignment',
      ],
      mitigations: [
        'Enforce pre-execution holds on all sensitive/destructive tools by default, not opt-in',
        'Set a maximum hold-queue SLA with automatic escalation rather than silent timeout-forwarding',
        'Require governance-tier classification before any new MCP server or agent integration is enabled',
        'Run a quarterly tabletop exercise simulating an unauthorised agent tool-call cascade',
      ],
      timeline: [
        { t: 'T+0', label: 'Unreviewed access granted', score: gsStart, rag: ragAt(gsStart), likelihood: 0.08, impact_$m: 0,
          impact: 'New agent/tool scope enabled without governance-tier review',
          signals: [`Score ${gsStart}/25`, 'No hold configured for new tool scope'],
          action: 'Assign governance owner; classify tool scope before go-live' },
        { t: 'T+30', label: 'Anomalous call pattern', score: gs30, rag: ragAt(gs30), likelihood: 0.15, impact_$m: imp30,
          impact: `First bypass/sensitive-tool flag fires; exposure reaches $${imp30}M if unactioned`,
          signals: ['Bypass-keyword flag observed', 'Sensitive-tool call rate elevated'],
          action: 'Governance hold triggered; incident triage opened' },
        { t: 'T+60', label: 'Hold backlog builds', score: gs60, rag: ragAt(gs60), likelihood: 0.20, impact_$m: imp60,
          impact: `Reviewer backlog exceeds SLA; timeout auto-forwarding begins; $${imp60}M at risk`,
          signals: ['Hold queue depth > reviewer throughput', 'Repeated flags from same session'],
          action: 'Escalate to security/audit leadership; freeze new tool scopes pending review' },
        { t: 'T+90', label: 'Unauthorised action executed', score: gsEnd, rag: ragAt(gsEnd), likelihood: 0.25, impact_$m: imp90,
          impact: `Unauthorised production action confirmed; $${imp90}M exposure; disclosure assessment required`,
          signals: ['Confirmed unauthorised write/delete action', 'Customer or financial data potentially touched'],
          action: 'Activate incident response; assess disclosure obligations; board briefing' },
      ],
    };
  }

  // ── Build Personas ──────────────────────────────────────────
  function buildPersonas(risks, ticker) {
    const red   = risks.filter(r => r.rag === 'R').map(r => r.id);
    const top3  = [...risks].sort((a,b)=>b.score-a.score).slice(0,3);
    // Sum of dollarExposureM (see allocateRiskDollarExposure) over a risk
    // subset. Returns null if no risk in the subset has an exposure figure,
    // so callers can fall back to un-quantified language rather than show "$0M".
    const exposureSum = subset => {
      const vals = subset.map(r => r.dollarExposureM).filter(v => v != null);
      return vals.length ? Math.round(vals.reduce((a, b) => a + b, 0)) : null;
    };
    const totalExposure = exposureSum(risks);
    const finRisks = risks.filter(r=>r.category==='Financial Reporting'||r.category==='Revenue');
    const finExposure = exposureSum(finRisks);
    const opsRisks = risks.filter(r=>r.category==='Operational'||r.category==='Supply');
    const opsExposure = exposureSum(opsRisks);
    return {
      'Chief Audit Executive': {
        sections: top3.map(r => `${r.id} ${r.name.split('—')[0].trim()}`),
        summary: `${red.length > 0 ? `${red.length} RED risk${red.length>1?'s':''} require immediate remediation.` : 'No RED risks; amber risk management is the priority.'} Top priority: ${top3[0]?.name.split('—')[0].trim() || 'primary risk'} — highest score and velocity.${totalExposure != null ? ` Modeled revenue exposure across the register: $${totalExposure}M (Bear-case allocation).` : ''}`,
        headline: `Audit impact score derived from risk velocity and score distribution across ${risks.length} risks.`,
      },
      'Chief Financial Officer': {
        sections: finRisks.slice(0,3).map(r => `${r.id} ${r.name.split('—')[0].trim()}`),
        summary: `Financial reporting risks: ${risks.filter(r=>r.category==='Financial Reporting').length} identified. ${risks.find(r=>r.category==='Financial Reporting'&&r.rag==='R')?'At least one financial reporting risk at RED — potential disclosure implications.':'Financial reporting risks within amber range; accruals and estimate review required.'}${finExposure != null ? ` Modeled revenue exposure: $${finExposure}M.` : ''}`,
        headline: `M-score and accruals ratio are the primary financial reporting early-warning signals for this entity.`,
      },
      'Chief Operating Officer': {
        sections: opsRisks.slice(0,3).map(r => `${r.id} ${r.name.split('—')[0].trim()}`),
        summary: `Operational risks: ${opsRisks.length} identified. Priority: KRI integration with GRC to ensure velocity signals are captured before risks breach amber thresholds.${opsExposure != null ? ` Modeled revenue exposure: $${opsExposure}M.` : ''}`,
        headline: `Operational risk KPIs should feed GRC risk register triggers; current coverage gap is the primary operational audit finding.`,
      },
    };
  }

  // ── FRED series metadata by industry ────────────────────────
  const FRED_BY_INDUSTRY = {
    'Semiconductors':           [{ id:'MANEMP',     name:'ISM Mfg PMI',             r:0.76, lead:1, dir:'CONTRACTIONARY', reading:48.3 },{ id:'CAPUTLG3311A2S', name:'Mfg Capacity Util.', r:0.72, lead:1, dir:'CONTRACTIONARY', reading:76.4 },{ id:'UMCSENT', name:'U-Mich Consumer Sent.', r:0.68, lead:3, dir:'NEUTRAL', reading:64.7 },{ id:'DTWEXBGS', name:'USD Broad Index', r:-0.62, lead:2, dir:'NEUTRAL', reading:121.4 },{ id:'DFF', name:'Fed Funds Rate', r:-0.55, lead:3, dir:'CONTRACTIONARY', reading:4.5 }],
    'Automotive OEM':           [{ id:'UMCSENT',    name:'Consumer Sentiment',       r:0.84, lead:2, dir:'CONTRACTIONARY', reading:64.7 },{ id:'TOTALSA', name:'Light Vehicle SAAR', r:0.91, lead:1, dir:'CONTRACTIONARY', reading:15.4 },{ id:'DFF', name:'Fed Funds Rate', r:-0.71, lead:3, dir:'CONTRACTIONARY', reading:4.5 },{ id:'WTISPLC', name:'WTI Crude ($/bbl)', r:-0.52, lead:2, dir:'NEUTRAL', reading:72.1 },{ id:'MANEMP', name:'ISM Mfg PMI', r:0.74, lead:1, dir:'CONTRACTIONARY', reading:48.3 }],
    'Software & Cloud':         [{ id:'PAYEMS',     name:'Nonfarm Payrolls',         r:0.72, lead:2, dir:'NEUTRAL', reading:159.2 },{ id:'UMCSENT', name:'Consumer Sentiment', r:0.65, lead:2, dir:'CONTRACTIONARY', reading:64.7 },{ id:'DFF', name:'Fed Funds Rate', r:-0.58, lead:3, dir:'CONTRACTIONARY', reading:4.5 },{ id:'GDPC1', name:'Real GDP Growth', r:0.61, lead:2, dir:'NEUTRAL', reading:2.4 },{ id:'RSXFS', name:'Retail Sales ex-Food', r:0.48, lead:1, dir:'NEUTRAL', reading:0.3 }],
    'Financial Services':       [{ id:'DFF',        name:'Fed Funds Rate',           r:0.78, lead:1, dir:'CONTRACTIONARY', reading:4.5 },{ id:'T10Y2Y', name:'Yield Curve (10Y-2Y)', r:0.71, lead:2, dir:'NEUTRAL', reading:-0.3 },{ id:'DPCCRV1Q225SBIS', name:'CC Delinquency Rate', r:-0.65, lead:2, dir:'NEUTRAL', reading:3.2 },{ id:'UMCSENT', name:'Consumer Sentiment', r:0.60, lead:3, dir:'CONTRACTIONARY', reading:64.7 },{ id:'PAYEMS', name:'Nonfarm Payrolls', r:0.55, lead:2, dir:'NEUTRAL', reading:159.2 }],
    'Healthcare & Pharma':      [{ id:'CPIMED',     name:'Medical CPI',             r:-0.62, lead:2, dir:'NEUTRAL', reading:3.4 },{ id:'PAYEMS', name:'Nonfarm Payrolls', r:0.54, lead:2, dir:'NEUTRAL', reading:159.2 },{ id:'GDPC1', name:'Real GDP Growth', r:0.50, lead:2, dir:'NEUTRAL', reading:2.4 },{ id:'DFF', name:'Fed Funds Rate', r:-0.48, lead:3, dir:'CONTRACTIONARY', reading:4.5 },{ id:'UMCSENT', name:'Consumer Sentiment', r:0.42, lead:3, dir:'CONTRACTIONARY', reading:64.7 }],
    'Generic':                  [{ id:'UMCSENT',    name:'Consumer Sentiment',       r:0.68, lead:2, dir:'CONTRACTIONARY', reading:64.7 },{ id:'DFF', name:'Fed Funds Rate', r:-0.55, lead:3, dir:'CONTRACTIONARY', reading:4.5 },{ id:'GDPC1', name:'Real GDP Growth', r:0.62, lead:2, dir:'NEUTRAL', reading:2.4 },{ id:'PAYEMS', name:'Nonfarm Payrolls', r:0.58, lead:2, dir:'NEUTRAL', reading:159.2 },{ id:'PCEPI', name:'PCE Inflation', r:-0.45, lead:1, dir:'NEUTRAL', reading:2.6 }],
  };
  FRED_BY_INDUSTRY['Industrial & Manufacturing'] = FRED_BY_INDUSTRY['Generic'];
  FRED_BY_INDUSTRY['Retail & Consumer']           = FRED_BY_INDUSTRY['Generic'];
  FRED_BY_INDUSTRY['Energy & Resources']          = FRED_BY_INDUSTRY['Generic'];
  FRED_BY_INDUSTRY['Utilities']                   = FRED_BY_INDUSTRY['Generic'];

  // ── Build derived signals from ratios ───────────────────────
  function buildSignals(ratios, ticker, industry) {
    const sigs = [];
    if (ratios.revGrowth != null) {
      const dir = ratios.revGrowth < -0.10 ? 'contractionary' : ratios.revGrowth > 0.10 ? 'expansionary' : 'neutral';
      sigs.push({ src:'EDGAR 10-K', label:`${ticker} revenue ${(ratios.revGrowth*100).toFixed(1)}% YoY — ${dir}`, delta:dir, velocity: ratios.revGrowth<-0.10?3:ratios.revGrowth<0?2:1, cat:'Filing' });
    }
    if (ratios.grossMargin != null) {
      const flag = ratios.grossMargin < 0.30 ? 'margin compression risk' : ratios.grossMargin > 0.65 ? 'healthy margins' : 'margin within range';
      sigs.push({ src:'EDGAR 10-K', label:`Gross margin ${(ratios.grossMargin*100).toFixed(1)}% — ${flag}`, delta:'financial quality', velocity:ratios.grossMargin<0.25?3:1, cat:'Filing' });
    }
    if (ratios.mscore != null) {
      const band = ratios.mscore > -1.78 ? 'ELEVATED' : ratios.mscore > -2.22 ? 'GRAY ZONE' : 'NORMAL';
      sigs.push({ src:'EDGAR 10-K', label:`M-score ${ratios.mscore.toFixed(2)} — ${band} (${ratios.mscore>-1.78?'likely manipulator threshold exceeded':ratios.mscore>-2.22?'gray zone':'within normal range'})`, delta:'financial reporting', velocity:ratios.mscore>-1.78?3:ratios.mscore>-2.22?2:0, cat:'Filing' });
    }
    if (ratios.zscore != null) {
      const band = ratios.zscore <= 1.1 ? 'DISTRESS' : ratios.zscore <= 2.6 ? 'GRAY ZONE' : 'SAFE';
      sigs.push({ src:'EDGAR 10-K', label:`Altman Z''-score ${ratios.zscore.toFixed(2)} — ${band} (${ratios.zscore<=1.1?'distress zone, going-concern risk elevated':ratios.zscore<=2.6?'gray zone':'within safe range'})`, delta:'liquidity', velocity:ratios.zscore<=1.1?3:ratios.zscore<=2.6?2:0, cat:'Filing' });
    }
    if (ratios.fcfMargin != null) {
      const flag = ratios.fcfMargin < -0.05 ? 'negative FCF — liquidity flag' : ratios.fcfMargin > 0.15 ? 'strong FCF generation' : 'modest FCF';
      sigs.push({ src:'EDGAR 10-K', label:`FCF margin ${(ratios.fcfMargin*100).toFixed(1)}% — ${flag}`, delta:ratios.fcfMargin<0?'contractionary':'neutral', velocity:ratios.fcfMargin<-0.05?3:1, cat:'Filing' });
    }
    if (ratios.rdIntensity != null && (industry === 'Semiconductors' || industry === 'Software & Cloud' || industry === 'Healthcare & Pharma')) {
      sigs.push({ src:'EDGAR 10-K', label:`R&D intensity ${(ratios.rdIntensity*100).toFixed(1)}% of revenue — ${ratios.rdIntensity<0.10?'below sector peer range':ratios.rdIntensity>0.20?'above-average investment':'within peer range'}`, delta:'technology', velocity:ratios.rdIntensity<0.08?2:1, cat:'Filing' });
    }
    const fred = (FRED_BY_INDUSTRY[industry] || FRED_BY_INDUSTRY['Generic']).slice(0, 3);
    fred.forEach(f => {
      sigs.push({ src:'FRED Macro', label:`${f.name}: ${f.reading} — ${f.dir.toLowerCase()}`, delta:f.dir.toLowerCase(), velocity:f.dir==='CONTRACTIONARY'?2:1, cat:'Macro' });
    });
    sigs.push({ src:'Industry RSS', label:`${industry} sector news monitoring active — velocity signals generated from live RSS feeds`, delta:'neutral', velocity:1, cat:'RSS' });
    sigs.push({ src:'Internal KRI', label:`${ticker} key risk indicators: ${ratios.revGrowth!=null?`revenue trend ${(ratios.revGrowth*100).toFixed(1)}%`:'revenue trend n/a'}, ${ratios.grossMargin!=null?`gross margin ${(ratios.grossMargin*100).toFixed(1)}%`:'gross margin n/a'}`, delta:'neutral', velocity:1, cat:'KRI' });
    return sigs;
  }

  // Convert an EDGAR end-date string ("2024-09-28") to "Q3-24"
  function edgarDateToQLabel(end) {
    if (!end) return null;
    const [y, m] = end.slice(0, 7).split('-').map(Number);
    return `Q${Math.ceil(m / 3)}-${String(y).slice(2)}`;
  }

  // Generate 8 chronological synthetic quarters ending at a given (year, quarter)
  function syntheticQuarters(endYY, endQ) {
    const qs = [];
    let q = endQ, y = endYY;
    for (let i = 7; i >= 0; i--) {
      qs[i] = `Q${q}-${String(y).slice(-2)}`;
      q--; if (q < 1) { q = 4; y--; }
    }
    return qs;
  }

  // Compute dynamic quarter boundaries so historical/forecast labels are never stale.
  // Returns the last *completed* fiscal quarter (Q2-2026 is still open on 2026-06-24,
  // so last completed = Q1-2026) plus 4 future forecast quarter labels.
  function _quarterBoundaries() {
    const now = new Date();
    const year = now.getFullYear();
    const curQ = Math.ceil((now.getMonth() + 1) / 3);
    let lastY = year, lastQ = curQ - 1;
    if (lastQ < 1) { lastQ = 4; lastY--; }
    const fcLabels = [];
    let fqY = year, fqQ = curQ;
    for (let i = 0; i < 4; i++) {
      fcLabels.push(`Q${fqQ}-${String(fqY).slice(-2)}`);
      fqQ++; if (fqQ > 4) { fqQ = 1; fqY++; }
    }
    return { lastY, lastQ, fcLabels, defaultLatestQ: `Q${lastQ}-${String(lastY).slice(-2)}` };
  }

  // ── Monte Carlo revenue forecast bands ───────────────────────
  // Historical QoQ volatility drives N simulated paths over the forecast horizon;
  // lo/hi become the 10th/90th percentile of simulated outcomes per quarter,
  // replacing a fixed ±8% band with a distribution anchored to how volatile
  // this company's revenue has actually been.
  function _qoqVolatility(quarters) {
    if (quarters.length < 3) return 0.06; // fallback: 6% QoQ stdev
    const rets = [];
    for (let i = 1; i < quarters.length; i++) {
      const prev = quarters[i - 1].v, curr = quarters[i].v;
      if (prev > 0) rets.push((curr - prev) / prev);
    }
    if (rets.length < 2) return 0.06;
    const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
    const variance = rets.reduce((a, b) => a + (b - mean) ** 2, 0) / (rets.length - 1);
    return Math.sqrt(variance) || 0.06;
  }
  function _boxMuller() {
    let u = 0, v = 0;
    while (u === 0) u = Math.random();
    while (v === 0) v = Math.random();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }
  function _monteCarloRevenue(lastV, qGrowth, volatility, horizonQ, nSims = 500) {
    const paths = Array.from({ length: nSims }, () => {
      let v = lastV;
      const path = [];
      for (let i = 0; i < horizonQ; i++) {
        v = v * (1 + qGrowth + volatility * _boxMuller());
        path.push(Math.max(0, v));
      }
      return path;
    });
    const pct = (arr, p) => {
      const sorted = [...arr].sort((a, b) => a - b);
      const idx = clamp(Math.round(p * (sorted.length - 1)), 0, sorted.length - 1);
      return sorted[idx];
    };
    const byQuarter = Array.from({ length: horizonQ }, (_, i) => {
      const vals = paths.map(p => p[i]);
      return { p10: pct(vals, 0.10), p50: pct(vals, 0.50), p90: pct(vals, 0.90) };
    });
    const finalVals = paths.map(p => p[horizonQ - 1]);
    const probDecline = finalVals.filter(v => v < lastV).length / nSims;
    return { byQuarter, probDecline, volatility, nSims, finalVals, lastV };
  }

  // ── Build Forecasts from EDGAR quarterly series ─────────────
  // Turn a raw SEC companyfacts .series array (form/fp/start/end/val/filed)
  // into a deduplicated, duration-guarded, Q4-synthesized quarterly series.
  //
  // Two independent EDGAR data-quality problems, both fixed here:
  //  1. fp alone doesn't reliably identify a standalone 3-month period — a
  //     10-Q reports BOTH the 3-month figure AND a year-to-date cumulative
  //     figure (6-month at Q2, 9-month at Q3) side by side, and both can
  //     share the same fp (e.g. fp='Q3' on both) AND the same period `end`
  //     date (only `start` differs: Jul1 vs Jan1). Filtering on fp alone
  //     lets the YTD figure through, and since dedup-by-end just keeps
  //     whichever was filed more recently, a chart can end up plotting the
  //     cumulative 9-month total as if it were Q3 alone — a value ~3x too
  //     large that looks like "adding to the total over the entire year."
  //     Fixed with a real duration check (60-110 days) whenever `start` is
  //     present (SEC companyfacts always includes it).
  //  2. Q4 is never reported as its own standalone fact at all — 10-Qs only
  //     ever cover Q1-Q3; Q4's contribution is only visible baked into the
  //     10-K's full-year total. Left alone, every 4th point in the chart is
  //     a gap. Synthesized here as FY (10-K annual) minus the 3 preceding
  //     standalone quarters, guarded to a plausible ~9-12 month span so a
  //     sparse-history gap can't accidentally bridge unrelated quarters.
  function deriveQuarterlyWithQ4(series) {
    if (!Array.isArray(series)) return [];
    const isStandaloneQ = x => /^Q[1-4]$/.test(x.fp);
    const isThreeMonths = x => {
      if (!x.start || !x.end) return true; // no duration info to check — trust fp alone
      const days = (new Date(x.end) - new Date(x.start)) / 86400000;
      return days >= 60 && days <= 110;
    };
    const byEnd = {};
    series
      .filter(x => x.form === '10-Q' && isStandaloneQ(x) && isThreeMonths(x))
      .forEach(x => {
        if (!x.end) return;
        if (!(x.end in byEnd) || (x.filed || '') > (byEnd[x.end].filed || ''))
          byEnd[x.end] = x;
      });

    const annual = series.filter(x => x.form === '10-K' && x.fp === 'FY' && x.val != null && x.end);
    let sortedEnds = Object.keys(byEnd).sort();
    annual.forEach(a => {
      if (a.end in byEnd) return;
      const preceding = sortedEnds.filter(e => e < a.end).slice(-3);
      if (preceding.length !== 3) return;
      const spanDays = (new Date(a.end) - new Date(preceding[0])) / 86400000;
      if (spanDays < 250 || spanDays > 400) return;
      const q4Val = a.val - preceding.reduce((sum, e) => sum + byEnd[e].val, 0);
      byEnd[a.end] = { end: a.end, val: q4Val, filed: a.filed || '', fp: 'Q4', form: '10-Q', synthesized: true };
      sortedEnds = Object.keys(byEnd).sort();
    });

    return Object.values(byEnd).sort((a, b) => a.end > b.end ? 1 : -1);
  }

  // Collapse rows that landed on the same calendar-quarter label back to one
  // per label, keeping the last (most complete/most recently filed) value at
  // that label's original chronological position. Needed because
  // edgarDateToQLabel buckets by calendar month, not by the company's actual
  // fiscal quarter — a 52/53-week fiscal year (or a synthesized Q4 landing
  // right at a quarter boundary) can produce two distinct `end` dates that
  // both round to e.g. "Q4-25". Left alone, that duplicate label ends up as
  // a React list `key` (chart x-axis, momentum table) and triggers a
  // duplicate-key warning plus a collapsed/misrendered row.
  function _dedupeByQ(arr) {
    const byLabel = new Map();
    arr.forEach(x => byLabel.set(x.q, x));
    return Array.from(byLabel.values());
  }

  function buildForecasts(ratios, ticker, industry, fin) {
    const { lastY, lastQ, fcLabels, defaultLatestQ } = _quarterBoundaries();

    const histQuarters = [];
    const revQuarterly = deriveQuarterlyWithQ4(fin?.revenue?.series);
    if (revQuarterly.length) {
      revQuarterly
        .slice(-12)
        .forEach(q => {
          const label = edgarDateToQLabel(q.end) || q.fp;
          histQuarters.push({ q: label, v: +(q.val / 1e6).toFixed(0) });
        });
      histQuarters.splice(0, histQuarters.length, ..._dedupeByQ(histQuarters));
    }
    // If EDGAR gave fewer than 4 quarters, clear partials and synthesise a full 8-quarter series
    if (histQuarters.length < 4) {
      histQuarters.length = 0; // discard any partial EDGAR entries to avoid mixed series
      const annualM = Number.isFinite(ratios.rev) ? ratios.rev / 1e6 : 1000;
      const qBase   = annualM / 4;
      const qp      = Number.isFinite(ratios.revP) ? ratios.revP / 1e6 / 4 : qBase;
      const labels  = syntheticQuarters(lastY, lastQ);
      for (let i = 0; i < 8; i++) {
        const frac = i / 7;
        histQuarters.push({ q: labels[i], v: +(qp + (qBase - qp) * frac).toFixed(0) });
      }
    }
    const lastV   = histQuarters.length ? histQuarters[histQuarters.length - 1].v : 1000;
    const trend   = ratios.revGrowth ?? 0;
    const qGrowth = Math.pow(1 + trend, 0.25) - 1;
    const revVolatility = _qoqVolatility(histQuarters);
    const revMonteCarlo = _monteCarloRevenue(lastV, qGrowth, revVolatility, fcLabels.length);
    const fcastQ  = fcLabels.map((q, i) => {
      const base = +(lastV * Math.pow(1 + qGrowth, i + 1)).toFixed(0);
      const mcQ  = revMonteCarlo.byQuarter[i];
      return { q, base, lo: +mcQ.p10.toFixed(0), hi: +mcQ.p90.toFixed(0) };
    });

    const histMargins = [];
    if (fin?.cogs?.series && fin?.revenue?.series) {
      const revQ  = deriveQuarterlyWithQ4(fin.revenue.series);
      const cogsQ = deriveQuarterlyWithQ4(fin.cogs.series);
      const revMap = {};
      revQ.forEach(x => { revMap[x.end] = x.val; });
      cogsQ
        .filter(c => revMap[c.end] != null)
        .slice(-12)
        .forEach(c => {
          const rv = revMap[c.end];
          const gm = rv > 0 ? (1 - c.val / rv) * 100 : null;
          if (gm != null && gm > 0 && gm < 100)
            histMargins.push({ q: edgarDateToQLabel(c.end) || c.end.slice(0, 7), v: +gm.toFixed(1) });
        });
      histMargins.splice(0, histMargins.length, ..._dedupeByQ(histMargins));
    }
    // Fallback: use GrossProfit series directly if COGS-based matching gave < 4 quarters
    if (histMargins.length < 4 && fin?.grossProfit?.series && fin?.revenue?.series) {
      histMargins.length = 0;
      const revQ2 = deriveQuarterlyWithQ4(fin.revenue.series);
      const gpQ   = deriveQuarterlyWithQ4(fin.grossProfit.series);
      const revMap2 = {};
      revQ2.forEach(x => { revMap2[x.end] = x.val; });
      gpQ
        .filter(p => revMap2[p.end] != null)
        .slice(-12)
        .forEach(p => {
          const rv = revMap2[p.end];
          const gm = rv > 0 ? (p.val / rv) * 100 : null;
          if (gm != null && gm > 0 && gm < 100)
            histMargins.push({ q: edgarDateToQLabel(p.end) || p.end.slice(0, 7), v: +gm.toFixed(1) });
        });
      histMargins.splice(0, histMargins.length, ..._dedupeByQ(histMargins));
    }
    if (histMargins.length < 4) {
      // Clear any partial EDGAR entries to avoid a mixed EDGAR+synthetic series
      histMargins.length = 0;
      const gm = Number.isFinite(ratios.grossMargin) ? ratios.grossMargin * 100 : 40;
      const labels = syntheticQuarters(lastY, lastQ);
      for (let i = 0; i < 8; i++)
        histMargins.push({ q: labels[i], v: +(gm - (7 - i) * 0.1).toFixed(1) });
    }
    const rawGM  = histMargins[histMargins.length - 1]?.v;
    const lastGM = Number.isFinite(rawGM) ? rawGM : 40;
    const fcastGM = fcLabels.map((q, i) => ({
      q,
      base: +(lastGM + i * 0.2).toFixed(1),
      lo:   +(lastGM + i * 0.2 - 2).toFixed(1),
      hi:   +(lastGM + i * 0.2 + 2.5).toFixed(1),
    }));

    // ── Rolling 8-quarter sentiment — QoQ revenue momentum proxy
    const sentQuarterly = [];
    if (histQuarters.length >= 2) {
      // Use up to 8 QoQ pairs from the available history
      const src = histQuarters.slice(-9);
      for (let i = 1; i < src.length; i++) {
        const prev = src[i - 1].v, curr = src[i].v;
        const qoq  = prev > 0 ? (curr - prev) / prev : 0;
        const score = Math.max(-25, Math.min(25, Math.round(qoq * 100)));
        const hedge = +(Math.max(0.05, 0.26 - i * 0.025)).toFixed(2);
        sentQuarterly.push({ q: src[i].q, score, hedge });
      }
    } else {
      // Pure synthetic — 8 quarters chronological ending at last completed quarter
      const annG   = ratios.revGrowth ?? 0;
      const qG     = Math.pow(1 + annG, 0.25) - 1;
      const labels = syntheticQuarters(lastY, lastQ);
      for (let i = 0; i < 8; i++) {
        const score = Math.max(-25, Math.min(25, Math.round(qG * 100) + (i < 4 ? -5 : 3)));
        sentQuarterly.push({ q: labels[i], score, hedge: +(Math.max(0.05, 0.26 - i * 0.025)).toFixed(2) });
      }
    }
    const latestSent = sentQuarterly[sentQuarterly.length - 1]?.score ?? 0;
    const sentTrend  = latestSent > 5 ? 'IMPROVING' : latestSent < -5 ? 'DETERIORATING' : 'STABLE';
    const hedgeLast  = sentQuarterly[sentQuarterly.length - 1]?.hedge ?? 0.10;
    const hedgePrev  = sentQuarterly[sentQuarterly.length - 3]?.hedge ?? hedgeLast;
    const hedgeTrend = hedgeLast < hedgePrev ? `↓ to ${(hedgeLast * 100).toFixed(0)}%` : `↑ to ${(hedgeLast * 100).toFixed(0)}%`;

    // ── Synthetic analyst KPI series (overridden in app.jsx with MCP real data) ─
    const annualM = Number.isFinite(ratios.rev) ? ratios.rev / 1e6 : 1000;
    const qRevM   = annualM / 4;
    const labels8 = syntheticQuarters(lastY, lastQ);

    const _synth = (base8fn, trend8 = 0, digits = 0) => {
      const hist = labels8.map((q, i) => ({ q, v: +base8fn(i).toFixed(digits) }));
      const lastV8 = hist[hist.length - 1].v;
      return {
        history:  hist,
        forecast: fcLabels.map((q, i) => {
          const base = +(lastV8 + trend8 * (i + 1)).toFixed(digits);
          const pad  = Math.abs(lastV8) * 0.1 || 0.1;
          return { q, base, lo: +(base - pad).toFixed(digits), hi: +(base + pad).toFixed(digits) };
        }),
      };
    };

    // Net Income: 12% net margin, slight ramp
    const niBase = i => qRevM * 0.12 * (0.88 + i * 0.03);
    const niTrend = qRevM * 0.12 * 0.03;
    const synthNI = _synth(niBase, niTrend, 0);

    // EPS: NI ($M) / ~435M shares = $/share
    const epsBase = i => niBase(i) / 435;
    const synthEPS = _synth(epsBase, niTrend / 435, 2);

    // FCF: 16% FCF margin
    const fcfBase = i => qRevM * 0.16 * (0.85 + i * 0.025);
    const synthFCF = _synth(fcfBase, qRevM * 0.16 * 0.025, 0);

    // EBITDA: 28% EBITDA margin
    const ebitdaBase = i => qRevM * 0.28 * (0.90 + i * 0.02);
    const synthEBITDA = _synth(ebitdaBase, qRevM * 0.28 * 0.02, 0);

    // Operating Margin: 15%
    const omLast = 15.0;
    const synthOM = {
      history: labels8.map((q, i) => ({ q, v: +(omLast * (0.88 + i * 0.018)).toFixed(1) })),
      forecast: fcLabels.map((q, i) => ({ q, base: +(omLast + 0.1 * (i + 1)).toFixed(1), lo: +(omLast - 2).toFixed(1), hi: +(omLast + 2.5).toFixed(1) })),
    };

    const fred = FRED_BY_INDUSTRY[industry] || FRED_BY_INDUSTRY['Generic'];
    return {
      revenue:   { history: histQuarters, forecast: fcastQ, monteCarlo: {
        probDecline: revMonteCarlo.probDecline,
        volatilityPct: +(revMonteCarlo.volatility * 100).toFixed(1),
        nSims: revMonteCarlo.nSims,
      } },
      margin:    { history: histMargins,  forecast: fcastGM },
      eps:       synthEPS,
      netIncome: synthNI,
      fcf:       synthFCF,
      ebitda:    synthEBITDA,
      opMargin:  synthOM,
      fred,
      sentiment: {
        score: latestSent,
        trend: sentTrend,
        hedge_ratio_trend: hedgeTrend,
        latest_quarter: sentQuarterly[sentQuarterly.length - 1]?.q ?? defaultLatestQ,
        quarterly: sentQuarterly,
      },
      mscore: {
        m: ratios.mscore ?? -2.5,
        band: ratios.mscore!=null ? (ratios.mscore>-1.78?'ELEVATED':ratios.mscore>-2.22?'GRAY ZONE':'NORMAL') : 'NORMAL',
        key_driver: ratios.dsri!=null&&ratios.dsri>1.15 ? 'DSRI (receivables quality)' : ratios.tata!=null&&ratios.tata>0.04 ? 'TATA (accrual quality)' : 'SGI (sales growth)',
        thresholds: { red:-1.78, amber:-2.22 },
        defaulted: ['GMI','AQI','DEPI','SGAI','LVGI'],
        vars: { DSRI: ratios.dsri??1.0, GMI:1.0, AQI:1.0, SGI:ratios.sgi??1.0, DEPI:1.0, SGAI:1.0, LVGI:1.0, TATA:ratios.tata??0.0 },
      },
      zscore: {
        z: ratios.zscore ?? 1.8,
        band: ratios.zscore!=null ? (ratios.zscore<=1.1?'DISTRESS':ratios.zscore<=2.6?'GRAY ZONE':'SAFE') : 'GRAY ZONE',
        key_driver: ratios.workingCapital!=null&&ratios.assets&&(ratios.workingCapital/ratios.assets)<0 ? 'X1 (working capital deficit)' : ratios.stockholdersEquity!=null&&ratios.stockholdersEquity<0 ? 'X4 (negative book equity)' : 'X3 (operating profitability)',
        thresholds: { distress:1.1, grey:2.6 },
      },
    };
  }

  // ── Build Entity ─────────────────────────────────────────────
  function buildEntity(ticker, fin, industry) {
    return {
      name: fin?.entity || ticker,
      ticker: ticker.toUpperCase(),
      industry,
      focus: industry,
      period: (() => { const y = new Date().getFullYear(); return `Q1 ${y-4} — Q4 ${y-2}`; })(),
      periodBegin: (() => { const now = new Date(); const q = Math.ceil((now.getMonth()+1)/3); return `Q${q} ${now.getFullYear()}`; })(),
      periodEnd: (() => { const y = new Date().getFullYear(); return `Q4 ${y}`; })(),
      auditor: 'Independent Auditor',
      auditor_opinion: 'Unqualified',
      fy_close: '2024-12-31',
      peers: [],
    };
  }

  // ── Build Closure & Loop ─────────────────────────────────────
  function buildClosure(risks) {
    const red = risks.filter(r=>r.rag==='R').length;
    const grn = risks.filter(r=>r.rag==='G').length;
    return { risks_closed:grn, risks_reduced:risks.length-red-grn, risks_unchanged:red,
             projected_total_risk_reduction_pct:Math.round(15+grn*3), evidence_artifacts:40+risks.length*2,
             rerun_recommended:risks.filter(r=>r.velocity>=3).map(r=>r.id) };
  }
  function buildLoop(risks) {
    const impact = +(12.5 + risks.filter(r=>r.rag==='R').length*1.0 + risks.filter(r=>r.velocity>=2).length*0.375).toFixed(1);
    return { loop_health:impact>=17.5?'R':impact>=12.5?'A':'G', audit_impact_score:Math.min(25,impact),
             risk_reduction_pct:Math.round(15+risks.filter(r=>r.velocity<0).length*3),
             maps_open:risks.length, risks_closed:risks.filter(r=>r.rag==='G').length,
             next_trigger_days:30, next_cycle_focus:`Re-test ${risks.filter(r=>r.velocity>=3).map(r=>r.name.split('—')[0].trim()).join('; ') || 'top velocity risks'}`,
             lessons_learned:[`${risks[0]?.name.split('—')[0].trim()} — control environment requires GRC integration to surface velocity signals earlier.`,`Review frequency for velocity-3 risks should be monthly, not quarterly.`],
             velocity_threshold_recommendation:2.5 };
  }

  // ── Risk Flow metadata ─────────────────────────────────────
  const CATEGORY_IMPACTS = {
    'Revenue':             ['Revenue Recognition', 'Sales Operations', 'Customer Relationships', 'Investor Relations'],
    'Operational':         ['Product Development', 'Manufacturing', 'Supply Chain', 'Operations'],
    'Financial Reporting': ['Finance', 'Accounting', 'External Reporting', 'Investor Relations'],
    'Supply':              ['Supply Chain', 'Procurement', 'Manufacturing', 'Operations'],
    'Cybersecurity':       ['Information Technology', 'Legal & Compliance', 'Customer Trust', 'Operations'],
    'Trade Compliance':    ['Legal & Compliance', 'Sales Operations', 'Finance', 'Government Relations'],
    'ESG':                 ['Corporate Governance', 'Investor Relations', 'Operations', 'Legal & Compliance'],
    'Compliance':          ['Legal & Compliance', 'Government Relations', 'Finance', 'Operations'],
    'Legal':               ['Legal & Compliance', 'Finance', 'Operations', 'Customer Relationships'],
    'Liquidity':           ['Finance', 'Treasury', 'Investor Relations', 'Operations'],
    'Governance':          ['Corporate Governance', 'Investor Relations', 'Legal & Compliance', 'Operations'],
    'Audit Oversight':     ['External Reporting', 'Corporate Governance', 'Finance', 'Investor Relations'],
  };
  const DEFAULT_IMPACTS = ['Operations', 'Finance', 'Legal & Compliance', 'Customer Relationships'];

  function buildRiskFlow(risks, industry, maps) {
    const cadenceFor = vel => {
      if (vel >= 3) return ['T+7d',  'T+21d', 'T+45d', 'T+75d'];
      if (vel >= 2) return ['T+14d', 'T+45d', 'T+75d'];
      if (vel >= 1) return ['T+21d', 'T+60d', 'T+90d'];
      if (vel < 0)  return ['T+60d', 'T+90d'];
      return ['T+45d', 'T+90d'];
    };
    const flow = {};
    risks.forEach(r => {
      const t = CATALOG_BY_ID[r.id];
      flow[r.id] = {
        impacts:  CATEGORY_IMPACTS[r.category] || DEFAULT_IMPACTS,
        controls: (t?.controls || []).map(name => ({ name, ce: r.ce })),
        audits:   (maps || []).filter(m => m.linked_risk === r.id).map(m => m.id),
        cadence:  cadenceFor(r.velocity ?? 0),
      };
    });
    return flow;
  }

  // ── Recompute everything derived from the risk set ───────────
  // buildProfile() derives these once, from whatever risks existed at that
  // moment. Once evidence from 8-Ks / proxy / comment letters / signals has
  // been scored, the final risk set differs, so each dependent artefact is
  // rebuilt from it — otherwise objectives, MAPs, scenarios, the grey-swan
  // cascade, risk flow, personas and the loop view silently describe an
  // earlier register than the one on screen.
  function refreshForRisks(profile, risks, ticker, industryLabel) {
    const industry = normalizeIndustry(industryLabel || profile?.entity?.focus || 'Generic');
    const ratios = profile.ratios || {};
    const objectives = buildObjectives(risks, industry);
    const maps = buildMAPs(risks, objectives);
    const scenarios = buildScenarios(risks, ratios, ticker, industry);
    allocateRiskDollarExposure(risks, scenarios);
    return {
      ...profile, risks, objectives, maps, scenarios,
      closure: buildClosure(risks), loop: buildLoop(risks),
      riskFlow: buildRiskFlow(risks, industry, maps),
      personas: buildPersonas(risks, ticker),
      greySwan: buildGreySwan(risks, ratios, ticker, industry),
      reverseStress: buildReverseStressTest(risks, ratios, ticker, industry),
      governanceScenario: buildGovernanceScenario(risks, ratios, ticker, industry),
      multiFactorStress: buildMultiFactorStress(risks, ratios, ticker, industry),
      earlyWarning: buildEarlyWarningIndicator(risks, ratios, profile.forecasts, industry),
    };
  }

  // ── Main: buildProfile ───────────────────────────────────────
  // ── Per-risk dollar exposure ────────────────────────────────
  // Allocates the Bear scenario's modeled revenue-at-risk (revenue_at_risk_m,
  // already computed in buildScenarios) across individual risks, weighted by
  // each risk's current score and velocity. This exists so Board/CFO persona
  // briefs (and the risk register itself) can cite a defensible $ figure per
  // risk instead of an AI inventing one — allocations sum exactly to the
  // Bear scenario's own total, so it's a proportional breakdown of an
  // existing model output, not an independent estimate.
  function allocateRiskDollarExposure(risks, scenarios) {
    const bear = (scenarios || []).find(s => s.id === 'bear');
    const total = bear?.revenue_at_risk_m;
    if (!risks?.length) return risks;
    if (total == null) {
      risks.forEach(r => { r.dollarExposureM = null; });
      return risks;
    }
    // velocity ranges -1..3 (see velOf) — modulates the score-based weight by
    // roughly -10% to +30% so a fast-worsening risk carries a larger share of
    // the allocation without letting velocity alone dominate over score.
    const weight = r => Math.max(r.score || 0.5, 0.5) * (1 + (r.velocity || 0) * 0.10);
    const weights = risks.map(weight);
    const totalWeight = weights.reduce((a, b) => a + b, 0);
    risks.forEach((r, i) => {
      r.dollarExposureM = totalWeight > 0 ? Math.round(total * (weights[i] / totalWeight)) : 0;
    });
    return risks;
  }

  // ── FAIR override application (post-hoc, optional) ──────────────────────
  // buildMAPs()'s reduction_pct is a template constant — a reasonable
  // industry-pattern default per risk type, same status as every other
  // TEMPLATES-sourced field. Once a MAP has a real FAIR control-ROI run
  // (fair_tool.control_roi's ale_reduction_pct — see project/agentic-tools/
  // fair_endpoints.py POST /fair/control-roi, run from the Risk
  // Quantification screen), that real, computed number should win over the
  // template guess.
  //
  // Deliberately a separate, optional, PURE post-processing step rather
  // than an async FAIR fetch threaded into buildMAPs/buildProfile:
  // buildProfile's entire pipeline is synchronous by design (it runs
  // against already-fetched financial data in one pass), and a FAIR run is
  // a deliberate, on-demand action a reviewer takes, not something that
  // should block or silently re-run a MAP's construction. Call this after
  // buildProfile() once you have FAIR data, same as allocateRiskDollarExposure
  // is applied as a second pass over buildScenarios' output today.
  //
  // fairByRisk: { [linked_risk_id]: { reduction_pct } } — look these up via
  // MCP.fairAleSummary()/MCP.fairControlRoi() (resource_type "risk",
  // resource_ref matching a MAP's linked_risk) and pass in whatever's been
  // computed so far; a risk with no entry keeps its template default
  // untouched, and the function no-ops entirely when nothing was found.
  function applyFairOverridesToMaps(maps, fairByRisk) {
    if (!fairByRisk || !Object.keys(fairByRisk).length) return maps;
    return maps.map(m => {
      const o = fairByRisk[m.linked_risk];
      if (!o || o.reduction_pct == null) return m;
      return { ...m, reduction_pct: o.reduction_pct, reduction_pct_source: 'fair' };
    });
  }

  function buildProfile(ticker, fin, sic, industryHint) {
    const industryLabel = industryHint || sic2industry(sic) || 'Generic';
    const industry = normalizeIndustry(industryLabel);
    const ratios   = computeRatios(fin);
    const { risks, notAssessed } = buildRisks(ratios);
    const objectives = buildObjectives(risks, industry);
    const maps     = buildMAPs(risks, objectives);
    const scenarios  = buildScenarios(risks, ratios, ticker, industry);
    allocateRiskDollarExposure(risks, scenarios);
    const greySwan   = buildGreySwan(risks, ratios, ticker, industry);
    const reverseStress      = buildReverseStressTest(risks, ratios, ticker, industry);
    const historicalAnalogs  = buildHistoricalAnalogs(ratios, ticker, industry);
    const governanceScenario = buildGovernanceScenario(risks, ratios, ticker, industry);
    const personas   = buildPersonas(risks, ticker);
    const forecasts  = buildForecasts(ratios, ticker, industry, fin);
    const signals    = buildSignals(ratios, ticker, industry);
    const closure    = buildClosure(risks);
    const loop       = buildLoop(risks);
    const entity     = buildEntity(ticker, fin, industryLabel);
    const eventTemplates = window.MOCK?.eventTemplates || [];
    const varCvar          = buildVarCvar(ratios, forecasts, ticker);
    const sensitivity       = buildSensitivity(ratios, ticker, industry);
    const multiFactorStress = buildMultiFactorStress(risks, ratios, ticker, industry);
    const liquidityRunway   = buildLiquidityRunway(ratios, ticker);
    const earlyWarning      = buildEarlyWarningIndicator(risks, ratios, forecasts, industry);
    return { entity, signals, risks, notAssessed, objectives, maps, closure, loop,
             eventTemplates, forecasts, scenarios, greySwan, ratios,
             reverseStress, historicalAnalogs, governanceScenario,
             riskFlow: buildRiskFlow(risks, industry, maps),
             personas, fred:forecasts.fred,
             varCvar, sensitivity, multiFactorStress, liquidityRunway, earlyWarning };
  }

  return { buildProfile, buildLoop, computeRatios, sic2industry, normalizeIndustry, quarterBoundaries: _quarterBoundaries,
           buildVarCvar, buildSensitivity, buildMultiFactorStress, buildLiquidityRunway, buildEarlyWarningIndicator,
           buildGreySwan, buildScenarios, allocateRiskDollarExposure, applyFairOverridesToMaps,
           buildObjectives, buildMAPs,
           // evidence-scored taxonomy
           CATALOG, CATALOG_BY_ID, scoreRow, scoreTaxonomy, ratioEvidence, buildRisks, ev, narrativeFromEvidence,
           BASE_LIKELIHOOD, CATEGORY_IMPACT, ragOf, RAG_RED, RAG_AMBER, refreshForRisks };

})();
