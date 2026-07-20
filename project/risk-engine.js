/* ============================================================
   Dendrai Risk Engine — dynamic profile builder
   Derives risk profiles from EDGAR financials + FRED + RSS
   for any publicly traded company. Falls back to industry
   template when EDGAR data is unavailable.
   ============================================================ */

window.RISK_ENGINE = (function () {
  'use strict';

  // ── Utilities ──────────────────────────────────────────────
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const pct   = (v, d=1) => v != null ? (v * 100).toFixed(d) + '%' : 'n/a';
  const fmt   = (v, d=0) => v != null ? v.toFixed(d) : 'n/a';
  // Thresholds on 0-25 scale (impact × likelihood)
  const ragOf = s => s >= 15 ? 'R' : s >= 9 ? 'A' : 'G';
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

    return { rev, revP, revGrowth, grossMargin, rdIntensity, sgaIntensity,
             niMargin, assetGrowth, cashRatio, fcf, fcfMargin, tata, dsri, sgi,
             mscore, zscore, workingCapital, liabilities, stockholdersEquity,
             cash, assets, ar, ni, cfo, capex };
  }

  // ── Industry Risk Templates ─────────────────────────────────
  // Each template: { id, name, category, base, delta(r), ceBase,
  //   narrative(r,ticker), obj, controls[], mapFinding, mapAction,
  //   mapOwner, mapSuccessCriteria, reductionPct }
  const TEMPLATES = {

    'Semiconductors': [
      { id:'R-01', name:'Revenue Concentration — Customer & End-Market', category:'Revenue', base:5.5, ceBase:'ADEQUATE',
        delta: r => tier(r.revGrowth, 0, [v=>v<-0.20,2.5],[v=>v<-0.05,1.0],[v=>v>0.20,-0.8]) + tier(r.dsri,0,[v=>v>1.20,0.5]),
        narrative: (r,t) => `Revenue ${pct(r.revGrowth)} YoY (${fmt(r.rev?r.rev/1e6:null,0)}M). ${r.revGrowth<0?'Declining top line signals end-market softening or customer concentration risk.':'Revenue growing; concentration disclosure warranted.'} DSRI ${fmt(r.dsri,2)} (>1.20 signals AR growing faster than revenue).`,
        obj:'Audit customer concentration disclosures and top-account contract terms for revenue recognition risk',
        controls:['CUS-101 Top-10 customer concentration KRI','CUS-102 Contract term review','CUS-105 Distributor inventory attestation'],
        mapFinding:'Customer concentration disclosures lack quantitative thresholds vs. peer set',
        mapAction:'Implement >50% top-3 concentration trigger in GRC; quarterly AC reporting',
        mapOwner:'Chief Revenue Officer', mapSuccessCriteria:'Concentration KRI live in GRC; threshold breach auto-escalated to AC', reductionPct:18 },

      { id:'R-02', name:'R&D Execution & Technology Leadership', category:'Operational', base:5.0, ceBase:'ADEQUATE',
        delta: r => (r.rdIntensity == null ? 2.5 : tier(r.rdIntensity,0,[v=>v<0.05,2.5],[v=>v<0.10,1.2],[v=>v>0.22,-0.5])) + tier(r.revGrowth,0,[v=>v<-0.10,0.7]),
        narrative: (r,t) => `R&D intensity ${r.rdIntensity != null ? pct(r.rdIntensity) + ' of revenue' : 'not observable (XBRL untagged — monitoring blind spot)'}. ${r.rdIntensity==null?'Unable to verify R&D investment adequacy from EDGAR; untagged spend creates a monitoring gap against ADI, TXN, STMicro peer set.':r.rdIntensity<0.10?'Below-peer investment level creates technology leadership risk versus ADI, TXN peer set.':'R&D intensity at or above peer range.'} Revenue trend ${pct(r.revGrowth)}. SiC competition from STMicro, Wolfspeed, Infineon intensifying.`,
        obj:'Evaluate R&D milestone tracking, competitive benchmarking, and NPI schedule vs. approved roadmap',
        controls:['RD-201 Product roadmap milestone tracking','RD-205 Competitive benchmark analysis','RD-210 Patent filing KPI'],
        mapFinding:'R&D milestone system not linked to GRC risk register; benchmarking vs. peers ad hoc',
        mapAction:'Link R&D program gates to risk register; quarterly innovation KRI including NPI schedule and patent output',
        mapOwner:'Chief Technology Officer', mapSuccessCriteria:'Quarterly R&D KRI dashboard live; milestone slippages auto-escalated', reductionPct:15 },

      { id:'R-03', name:'Financial Reporting — Accruals & Revenue Recognition', category:'Financial Reporting', base:4.5, ceBase:'ADEQUATE',
        delta: r => tier(r.mscore,0,[v=>v>-1.78,3.5],[v=>v>-2.22,1.5]) + tier(r.tata,0,[v=>v>0.06,1.0],[v=>v>0.03,0.5]) + tier(r.dsri,0,[v=>v>1.20,0.7]),
        narrative: (r,t) => `M-score ${fmt(r.mscore,2)} (likely-manipulator threshold -1.78, gray zone -2.22). Accruals ratio ${fmt(r.tata,3)}. DSRI ${fmt(r.dsri,2)}. ${r.mscore>-1.78?'M-score in elevated range — revenue recognition and accruals warrant detailed IA review.':r.mscore>-2.22?'Gray zone — accruals monitoring recommended.':'Financial reporting indicators within normal range.'}`,
        obj:'Review accruals quality, revenue recognition cutoff controls, and AR ageing analysis',
        controls:['FRP-301 Revenue recognition policy review','FRP-305 AR ageing quarterly review','FRP-310 Accruals ratio monitoring'],
        mapFinding:'AR ageing not reviewed by IA on quarterly cadence; M-score not in GRC financial risk dashboard',
        mapAction:'Add quarterly IA AR ageing review (flag >90d); include M-score and accruals ratio in GRC dashboard',
        mapOwner:'Chief Accounting Officer', mapSuccessCriteria:'Quarterly AR review completed; M-score dashboarded and threshold-triggered', reductionPct:22 },

      { id:'R-04', name:'Supply Chain — Single-Source & Geopolitical', category:'Supply', base:5.5, ceBase:'ADEQUATE',
        delta: r => 0.5 + tier(r.revGrowth,0,[v=>v<-0.15,0.8]),
        narrative: (r,t) => `Single-source supplier concentration is not directly observable from EDGAR financials — requires IA supply chain registry review. Revenue trend ${pct(r.revGrowth)} ${r.revGrowth!=null&&r.revGrowth<-0.10?'may reflect supply disruption impact.':'provides no direct supply chain signal.'}`,
        obj:'Map single-source critical components and validate buffer stock and escalation protocols',
        controls:['SC-401 Single-source component registry','SC-405 Buffer stock protocol','SC-410 Geopolitical risk rating'],
        mapFinding:'Single-source registry not maintained in GRC; escalation trigger thresholds undocumented',
        mapAction:'Migrate single-source register to GRC; define escalation triggers (lead-time >10w); assign risk owners',
        mapOwner:'Chief Procurement Officer', mapSuccessCriteria:'All single-source components in GRC register; triggers tested via tabletop', reductionPct:14 },

      { id:'R-05', name:'Gross Margin Compression', category:'Financial Reporting', base:4.5, ceBase:'ADEQUATE',
        delta: r => tier(r.grossMargin,0,[v=>v<0.30,3.0],[v=>v<0.45,1.5],[v=>v>0.65,-0.8]) + tier(r.niMargin,0,[v=>v<0,1.0]),
        narrative: (r,t) => `Gross margin ${pct(r.grossMargin)}. ${r.grossMargin!=null&&r.grossMargin<0.45?'Below sector average for analog/mixed-signal — pricing pressure or unfavourable end-market mix.':'Gross margin within sector norms.'} Net income margin ${pct(r.niMargin)}.`,
        obj:'Analyse gross margin variance bridge and pricing power by end-market segment',
        controls:['GM-501 Margin variance bridge','GM-505 Pricing approval governance','GM-510 Segment margin reporting'],
        mapFinding:'Margin variance bridge not produced for IA; segment pricing approval governance not documented',
        mapAction:'Require quarterly margin bridge by segment to IA; document pricing approval process for OEM vs. distribution channels',
        mapOwner:'CFO', mapSuccessCriteria:'Quarterly margin bridge produced; pricing approval policy published and IA-reviewed', reductionPct:17 },

      { id:'R-06', name:'Cybersecurity — IP & Fab Process Protection', category:'Cybersecurity', base:5.5, ceBase:'ADEQUATE',
        delta: r => 0,
        narrative: (r,t) => `IP and fabrication process protection are critical for semiconductor companies. IP theft, insider threats, and nation-state espionage are primary threat vectors. Annual IA assessment of insider threat detection and fab access management is required.`,
        obj:'Review IP classification controls, insider threat detection program, and fab/design centre access management',
        controls:['ITGC-601 IP classification and DLP','ITGC-605 Insider threat detection','ITGC-610 Fab physical and logical access'],
        mapFinding:'Insider threat detection program scope not formally reviewed by IA; IP classification coverage undocumented',
        mapAction:'Annual IA review of insider threat detection scope; fab access management testing; IP classification inventory',
        mapOwner:'CISO', mapSuccessCriteria:'Annual IA review completed; insider threat gaps remediated; IP classification >95% coverage', reductionPct:12 },

      { id:'R-07', name:'Export Controls & Trade Compliance', category:'Trade Compliance', base:6.0, ceBase:'ADEQUATE',
        calibrationFlag: 'Register vs. quant divergence — widest gap of any item. Verify specific entity-level enforcement action or BIS finding before retaining elevated register score.',
        delta: r => 0.5,
        narrative: (r,t) => `Export control compliance (EAR/ITAR/CCL) is a material risk in the current regulatory environment. U.S. entity list additions and China-related export restrictions create ongoing compliance obligations requiring annual IA testing. ⚠ Calibration: quant model scores this Green (sector median) — register elevation requires verification of entity-specific enforcement exposure.`,
        obj:'Test export license procedures, restricted-party screening, and BIS Entity List compliance controls',
        controls:['EXP-701 Export license review','EXP-705 Restricted-party screening','EXP-710 ECCN self-classification review'],
        mapFinding:'Export license procedures not independently tested by IA in current period; ECCN classification review scope unclear',
        mapAction:'Annual IA walkthrough of export licensing; test ECCN classification for top-20 SKUs; validate restricted-party screening coverage',
        mapOwner:'Chief Compliance Officer', mapSuccessCriteria:'Annual IA export controls test completed; ECCN classification reviewed; no uncleared exceptions', reductionPct:15 },

      { id:'R-08', name:'Capital Expenditure & Capacity Utilisation', category:'Operational', base:5.0, ceBase:'ADEQUATE',
        delta: r => tier(r.fcfMargin,0,[v=>v<-0.10,2.5],[v=>v<0,1.0],[v=>v>0.15,-0.5]) + tier(r.cashRatio,0,[v=>v<0.10,1.0]),
        narrative: (r,t) => `FCF margin ${pct(r.fcfMargin)} (CFO minus CapEx / Revenue). ${r.fcfMargin!=null&&r.fcfMargin<0?'Negative FCF indicates capital-intensive expansion — CapEx governance and capacity utilisation monitoring critical.':'Positive FCF; CapEx governance review remains standard.'} Cash/assets ${pct(r.cashRatio)}.`,
        obj:'Evaluate CapEx approval governance and capacity utilisation reporting vs. approved investment plan',
        controls:['CAPEX-801 CapEx approval gate','CAPEX-805 Capacity utilisation dashboard','CAPEX-810 ROI tracking on completed projects'],
        mapFinding:'CapEx variance vs. approved plan not formally reported to IA; capacity utilisation KPI absent from GRC',
        mapAction:'Add CapEx variance and utilisation to quarterly IA reporting; validate ROI tracking for projects completed >12 months ago',
        mapOwner:'COO / Chief Manufacturing Officer', mapSuccessCriteria:'Quarterly CapEx KRI live in GRC; ROI tracking covers 100% of completed projects', reductionPct:10 },

      { id:'R-09', name:'Macro Demand Cyclicality', category:'Revenue', base:6.5, ceBase:'ADEQUATE',
        delta: r => tier(r.revGrowth,0,[v=>v<-0.20,3.0],[v=>v<-0.10,2.0],[v=>v<0,0.8],[v=>v>0.10,-0.5]) + tier(r.sgi,0,[v=>v!=null&&v<0.80,0.5]),
        narrative: (r,t) => `Revenue growth ${pct(r.revGrowth)} YoY — ${r.revGrowth!=null&&r.revGrowth<-0.15?`severe cyclical contraction (>${Math.abs(r.revGrowth*100).toFixed(0)}% decline). Bear scenario projects further -18% revenue shock with gross margin compression.`:r.revGrowth!=null&&r.revGrowth<0?'Negative YoY — cyclical softening in progress; monitor end-market demand indicators.':'Revenue trend positive; standard cyclicality monitoring applies.'} SiC/power semiconductor sector is highly cyclical (lead times, inventory digestion). SGI ${fmt(r.sgi,2)}.`,
        obj:'Evaluate revenue scenario modelling, cycle-adjusted forecasting, and contingency planning for >15% demand shocks',
        controls:['MAC-901 Revenue scenario analysis','MAC-905 Demand cyclicality KRI','MAC-910 Bear-case contingency trigger'],
        mapFinding:'No formal demand-cycle contingency plan documented; bear scenario (−18% revenue) not modelled in approved operating plan',
        mapAction:'Develop cycle-adjusted revenue forecast model; define bear-case trigger thresholds and contingency responses (CapEx flex, opex reductions)',
        mapOwner:'CFO / Chief Revenue Officer', mapSuccessCriteria:'Cycle-adjusted scenario model approved by AC; bear-case contingency plan tested via tabletop', reductionPct:14 },

      { id:'R-10', name:'New Financial Obligations & Off-Balance-Sheet Exposure', category:'Financial Reporting', base:5.5, ceBase:'ADEQUATE',
        delta: r => tier(r.fcfMargin,0,[v=>v<0,1.5],[v=>v<0.05,0.8],[v=>v>0.12,-0.3]) + tier(r.cashRatio,0,[v=>v<0.10,0.8],[v=>v<0.15,0.4]),
        narrative: (r,t) => `8-K Items 1.01, 2.03, and 3.02 filings indicate material new agreements, direct financial obligations, and unregistered equity transactions requiring GRC coverage. FCF margin ${pct(r.fcfMargin)}, cash/assets ${pct(r.cashRatio)}. ${r.cashRatio!=null&&r.cashRatio<0.12?'Thin cash buffer amplifies risk of new leverage obligations.':'Liquidity adequate but new obligation monitoring is essential.'}`,
        obj:'Review material agreements (Item 1.01/2.03 8-Ks), direct financial obligations, and unregistered equity issuances for off-balance-sheet exposure',
        controls:['OBS-1001 Material agreement 8-K review','OBS-1005 Off-balance-sheet obligation register','OBS-1010 Unregistered equity issuance log'],
        mapFinding:'Three material agreement 8-Ks in 6 weeks not assessed for risk register impact; Item 3.02 unregistered equity issuance not tracked in GRC',
        mapAction:'Implement automated 8-K Item 1.01/2.03/3.02 trigger in GRC; quarterly IA review of new financial obligations vs. covenant headroom',
        mapOwner:'Chief Legal Officer / CFO', mapSuccessCriteria:'8-K trigger automation live in GRC; quarterly obligation review on IA schedule; no unreviewed material agreements >30d', reductionPct:12 },

      { id:'R-11', name:'Executive & Board Governance Stability', category:'Compliance', base:6.5, ceBase:'ADEQUATE',
        delta: r => 0,
        narrative: (r,t) => `Three 8-K Item 5.02 director/officer departure or election events filed within 7 months — a compressed pattern that is a recognised audit signal for governance instability. Item 5.07 (shareholder vote) co-filed in one instance, indicating concurrent board-level structural changes. EDGAR filings do not provide direct financial ratios for governance risk; monitor through 8-K event frequency and succession plan documentation.`,
        obj:'Review director and officer change patterns, C-suite succession plan coverage, and board committee continuity following recent 5.02 event cluster',
        controls:['GOV-1101 Director/officer change log','GOV-1105 Succession plan coverage','GOV-1110 Board committee continuity'],
        mapFinding:'Three Item 5.02 8-K filings in 7 months not tracked as governance risk; no GRC event linking board changes to the risk register',
        mapAction:'Add 8-K Item 5.02 trigger to GRC event monitor; document succession plan for C-suite and board committee chairs; quarterly governance stability KRI',
        mapOwner:'General Counsel / Board Chair', mapSuccessCriteria:'5.02 trigger live in GRC; succession plan documented for all C-suite roles; governance KRI reviewed at each AC meeting', reductionPct:8 },
    ],

    'Automotive OEM': [
      { id:'R-01', name:'EV Transition — Cost, Execution & Demand', category:'Operational', base:6.0, ceBase:'WEAK',
        delta: r => tier(r.grossMargin,0,[v=>v<0.08,2.5],[v=>v<0.12,1.5],[v=>v>0.18,-0.5]) + tier(r.fcfMargin,0,[v=>v<-0.05,1.0]),
        narrative: (r,t) => `Gross margin ${pct(r.grossMargin)} (sector range 8–18%). ${r.grossMargin!=null&&r.grossMargin<0.12?'Thin margin consistent with EV program losses compressing blended margins.':'Gross margin within sector range.'} FCF ${pct(r.fcfMargin)}.`,
        obj:'Audit EV program cost-per-vehicle tracking and milestone governance vs. approved business case',
        controls:['EV-101 Program cost gate','EV-102 Production attainment KPI','EV-105 Battery JV milestone review'],
        mapFinding:'EV program cost-per-vehicle variance not captured in IA GRC dashboard; EV and ICE cost reporting use separate data feeds',
        mapAction:'Integrate EV cost tracker into GRC; flag cost-per-vehicle variance >10%; quarterly AC reporting',
        mapOwner:'VP EV Program / VP Internal Audit', mapSuccessCriteria:'EV cost KRI in GRC; auto-flag on >10% variance; AC receives monthly bridge', reductionPct:22 },

      { id:'R-02', name:'Captive Finance — Reserve Adequacy & Credit Quality', category:'Financial Reporting', base:5.5, ceBase:'ADEQUATE',
        delta: r => tier(r.mscore,0,[v=>v>-1.78,2.0],[v=>v>-2.22,1.0]) + tier(r.dsri,0,[v=>v>1.15,1.0]),
        narrative: (r,t) => `M-score ${fmt(r.mscore,2)}, DSRI ${fmt(r.dsri,2)}. Captive finance reserve adequacy requires direct review of loss reserve vintage calibration against current rate environment — EDGAR ratios provide subsidiary-level early warnings only.`,
        obj:'Review captive finance loss reserve model, delinquency trend, and macro overlay adequacy',
        controls:['FC-201 Reserve methodology review','FC-205 Delinquency vintage analysis','FC-210 NCO trend monitoring'],
        mapFinding:'Reserve model uses pre-rate-hike vintage data; macro overlay procedure not formally documented',
        mapAction:'Recalibrate model with current vintages; implement annual macro-overlay review; stress-test at +150bps',
        mapOwner:'CFO — Financial Services Subsidiary', mapSuccessCriteria:'Updated model approved by AC; reserve recalculated; stress scenario disclosed in 10-Q', reductionPct:20 },

      { id:'R-03', name:'Supply Chain — Semiconductor & Critical Component', category:'Supply', base:5.5, ceBase:'ADEQUATE',
        delta: r => 0.5,
        narrative: (r,t) => `Semiconductor and critical component supply chain concentration is a material operational risk. Single-source components, geographic concentration, and JIT inventory practices create vulnerability to disruptions.`,
        obj:'Map semiconductor single-source concentration and validate escalation protocols and buffer stock levels',
        controls:['SC-301 Single-source semiconductor registry','SC-305 Buffer stock protocol','SC-310 Supplier financial health monitoring'],
        mapFinding:'Single-source escalation protocol not formalized; procurement register not linked to IA risk register',
        mapAction:'Formalize escalation triggers (supply warning, lead-time >12w); link procurement register to IA; tabletop exercise',
        mapOwner:'Chief Procurement Officer', mapSuccessCriteria:'All single-source components registered; triggers tested; IA register linked to procurement', reductionPct:16 },

      { id:'R-04', name:'Labor Cost — CBA Absorption & Productivity', category:'Operational', base:5.0, ceBase:'ADEQUATE',
        delta: r => tier(r.sgaIntensity,0,[v=>v>0.15,1.0],[v=>v>0.12,0.5]),
        narrative: (r,t) => `SGA intensity ${pct(r.sgaIntensity)} of revenue. ${r.sgaIntensity!=null&&r.sgaIntensity>0.12?'Elevated overhead suggests labor cost management and CBA absorption warrants review.':'Overhead within sector range.'} CBA cost absorption and productivity offsets are the key audit focus.`,
        obj:'Audit CBA cost absorption model and productivity offset milestone tracking',
        controls:['HR-401 CBA cost absorption model','HR-405 Productivity offset KPI','HR-410 Headcount vs. plan tracking'],
        mapFinding:'Productivity offset tracking not reported to IA on quarterly cadence; CBA assumptions not independently reviewed',
        mapAction:'Add productivity KRI to quarterly IA report; independently review CBA cost model assumptions annually',
        mapOwner:'Chief HR Officer', mapSuccessCriteria:'Quarterly productivity KRI in IA dashboard; annual CBA model review completed', reductionPct:12 },

      { id:'R-05', name:'Product Recall & Safety Liability', category:'Legal', base:5.5, ceBase:'ADEQUATE',
        delta: r => 0.5,
        narrative: (r,t) => `Product recall liability is a material risk driven by NHTSA investigations, defect discovery cycles, and supplier component failures. Average OEM recall cost has increased with connected vehicle complexity.`,
        obj:'Review recall accrual methodology, NHTSA investigation monitoring, and legal-letter procedures',
        controls:['RECALL-501 NHTSA investigation tracker','RECALL-505 Recall accrual methodology','RECALL-510 Legal letter procedure'],
        mapFinding:'NHTSA tracker not linked to IA register; recall accrual relies on pre-investigation estimates',
        mapAction:'Link NHTSA tracker to IA register; validate recall accrual at investigation opening vs. industry loss factors',
        mapOwner:'General Counsel', mapSuccessCriteria:'NHTSA tracker integrated with IA GRC; accrual validated at each investigation opening', reductionPct:14 },

      { id:'R-06', name:'Gross Margin — Revenue Mix & Incentive Spend', category:'Financial Reporting', base:5.0, ceBase:'ADEQUATE',
        delta: r => tier(r.grossMargin,0,[v=>v<0.08,2.5],[v=>v<0.12,1.2]) + tier(r.revGrowth,0,[v=>v<-0.05,0.8]),
        narrative: (r,t) => `Gross margin ${pct(r.grossMargin)}, revenue ${pct(r.revGrowth)} YoY. ${r.grossMargin!=null&&r.grossMargin<0.10?'<10% gross margin signals EV/mix headwinds or aggressive incentive spend.':'Gross margin within sector range.'} Incentive spend management is the key near-term margin risk.`,
        obj:'Analyse revenue mix, vehicle incentive spend, and gross margin variance bridge by segment',
        controls:['GM-601 Incentive spend approval','GM-605 Segment margin reporting','GM-610 Mix-adjusted margin bridge'],
        mapFinding:'Incentive spend not broken out in IA margin analysis; mix-adjusted margin bridge not produced for IA',
        mapAction:'Include incentive and mix bridge in quarterly margin reporting; require ICE vs. EV segment margin split',
        mapOwner:'CFO', mapSuccessCriteria:'Quarterly margin bridge with incentive and mix split delivered to IA; AC pack updated', reductionPct:15 },

      { id:'R-07', name:'Connected Vehicle Cybersecurity', category:'Cybersecurity', base:5.5, ceBase:'ADEQUATE',
        delta: r => 0.5,
        narrative: (r,t) => `Connected vehicle attack surface expands with each model year. OTA firmware signing, V2X protocol security, and telematics platform access controls are highest-priority IA areas.`,
        obj:'Audit OTA firmware signing controls, telematics API security, and connected vehicle penetration test findings',
        controls:['ITGC-701 OTA key management','ITGC-705 Telematics API security','ITGC-710 V2X threat model'],
        mapFinding:'OTA key rotation schedule undocumented; penetration test against connected vehicle stack overdue',
        mapAction:'Document OTA key rotation policy (90-day max); complete annual penetration test; NIST AISIC adoption',
        mapOwner:'CISO — Connected Vehicle', mapSuccessCriteria:'OTA policy documented; annual pentest completed; NIST AISIC framework adopted', reductionPct:13 },

      { id:'R-08', name:'ESG — Regulatory & ICE Phase-Out', category:'ESG', base:4.5, ceBase:'ADEQUATE',
        delta: r => 0.5,
        narrative: (r,t) => `ICE phase-out mandates (EU 2035, CA ZEV) create stranded asset risk in ICE manufacturing and R&D. Transition plan milestone tracking and stranded asset quantification require IA oversight.`,
        obj:'Validate ICE transition plan milestones, stranded asset estimates, and Scope 1/2 emissions methodology',
        controls:['ESG-801 ICE transition milestones','ESG-805 Stranded asset model','ESG-810 Scope 1/2 verification'],
        mapFinding:'ICE stranded asset estimates not independently validated; transition milestones not in GRC',
        mapAction:'IA validation of stranded asset model; add transition milestones to GRC; Scope 1/2 methodology review',
        mapOwner:'Chief Sustainability Officer', mapSuccessCriteria:'Stranded asset model validated; milestones in GRC; Scope 1/2 third-party verified', reductionPct:10 },
    ],

    'Software & Cloud': [
      { id:'R-01', name:'Revenue Recognition — ASC 606 / IFRS 15', category:'Financial Reporting', base:5.0, ceBase:'ADEQUATE',
        delta: r => tier(r.mscore,0,[v=>v>-1.78,3.0],[v=>v>-2.22,1.5]) + tier(r.dsri,0,[v=>v>1.20,1.0]) + tier(r.assetGrowth,0,[v=>v!=null&&r.revGrowth!=null&&v>r.revGrowth+0.15,0.8]),
        narrative: (r,t) => `M-score ${fmt(r.mscore,2)}, DSRI ${fmt(r.dsri,2)}. ${r.mscore>-1.78?'Elevated M-score — multi-element arrangement revenue recognition and SSP allocation warrant detailed review.':r.mscore>-2.22?'Gray zone — revenue cutoff monitoring recommended.':'Revenue recognition indicators within normal range.'} Asset growth ${pct(r.assetGrowth)} vs. revenue growth ${pct(r.revGrowth)}.`,
        obj:'Audit revenue recognition policy for multi-element arrangements, SSP allocation, and contract modifications',
        controls:['REV-101 SSP allocation methodology','REV-105 Contract modification controls','REV-110 Revenue cutoff testing'],
        mapFinding:'Multi-element SSP allocation not reviewed by IA in current cycle; contract modification controls not documented',
        mapAction:'Annual IA walkthrough of SSP methodology; test high-value contract recognition; document modification controls',
        mapOwner:'Chief Accounting Officer', mapSuccessCriteria:'SSP methodology reviewed; top-20 contracts tested; modification policy published', reductionPct:20 },

      { id:'R-02', name:'Customer Retention — Churn & Net Revenue Retention', category:'Revenue', base:5.5, ceBase:'ADEQUATE',
        delta: r => tier(r.revGrowth,0,[v=>v<-0.10,2.5],[v=>v<0,1.5],[v=>v>0.20,-0.8]),
        narrative: (r,t) => `Revenue growth ${pct(r.revGrowth)} YoY. ${r.revGrowth!=null&&r.revGrowth<0?'Declining revenue suggests churn exceeds new bookings — NRR likely below 100%.':r.revGrowth<0.10?'Modest growth may mask elevated churn offset by new logos.':'Revenue growth consistent with healthy NRR.'} NRR should be tracked in GRC as a primary KRI.`,
        obj:'Evaluate NRR and churn KPIs against board-approved thresholds and early-warning triggers',
        controls:['CX-201 NRR / churn dashboard','CX-205 Customer health scoring','CX-210 Renewal pipeline tracking'],
        mapFinding:'NRR tracking not integrated with GRC risk register; no IA-defined churn breach threshold',
        mapAction:'Define churn threshold trigger in GRC; add NRR to quarterly IA KRI dashboard; alert on >3pp QoQ deterioration',
        mapOwner:'Chief Customer Officer', mapSuccessCriteria:'NRR in GRC KRI dashboard; threshold trigger defined; quarterly review completed', reductionPct:18 },

      { id:'R-03', name:'Cybersecurity — Data Protection & Cloud Infrastructure', category:'Cybersecurity', base:5.5, ceBase:'ADEQUATE',
        delta: r => 0,
        narrative: (r,t) => `Data protection and cloud infrastructure security are existential risks for SaaS/cloud companies. SOC 2 Type II scope, sub-processor security, and incident response plan effectiveness are the primary IA focus areas.`,
        obj:'Review SOC 2 Type II coverage gaps, sub-processor security, and incident response plan effectiveness',
        controls:['ITGC-301 SOC 2 scope review','ITGC-305 Sub-processor security','ITGC-310 Incident response plan'],
        mapFinding:'SOC 2 Type II scope excludes critical sub-processors; incident response not tested by IA in current year',
        mapAction:'Extend SOC 2 scope to cover all in-scope sub-processors; annual IA tabletop incident response exercise',
        mapOwner:'CISO', mapSuccessCriteria:'SOC 2 scope extended; sub-processors covered; tabletop exercise completed and findings remediated', reductionPct:16 },

      { id:'R-04', name:'AI / Regulatory & Bias Risk', category:'Compliance', base:5.0, ceBase:'ADEQUATE',
        delta: r => 0.5,
        narrative: (r,t) => `Regulatory risk from AI governance (EU AI Act, FTC, state laws) and algorithmic bias in product decisions is an emerging but material risk for technology companies. Governance frameworks and bias testing documentation are the key IA requirements.`,
        obj:'Assess AI governance framework maturity and bias testing protocols for in-product and internal AI models',
        controls:['AI-401 AI governance policy','AI-405 Bias testing documentation','AI-410 AI model inventory'],
        mapFinding:'AI governance policy not published; bias testing undocumented for in-product models',
        mapAction:'Implement AI governance policy; document bias testing methodology and cadence; maintain AI model inventory',
        mapOwner:'Chief AI Officer / Chief Privacy Officer', mapSuccessCriteria:'Policy published; bias testing documented for all Tier-1 models; model inventory maintained', reductionPct:14 },

      { id:'R-05', name:'Competitive Disruption & Technology Obsolescence', category:'Operational', base:5.0, ceBase:'ADEQUATE',
        delta: r => tier(r.rdIntensity,0,[v=>v<0.10,1.5],[v=>v<0.15,0.5]) + tier(r.revGrowth,0,[v=>v<0,1.0]),
        narrative: (r,t) => `R&D intensity ${pct(r.rdIntensity)}. ${r.rdIntensity!=null&&r.rdIntensity<0.15?'Below-peer R&D investment in a high-velocity technology sector increases disruption risk.':'R&D investment at or above peer range.'} Revenue trend ${pct(r.revGrowth)}.`,
        obj:'Benchmark R&D investment intensity and product release cadence vs. sector peers',
        controls:['STRAT-501 Competitive landscape assessment','STRAT-505 Product release cadence KPI','STRAT-510 R&D ROI tracking'],
        mapFinding:'Competitive landscape not formally incorporated into risk register; product roadmap benchmarking ad hoc',
        mapAction:'Annual competitive disruption assessment incorporated into risk register update; R&D intensity benchmarking vs. 5 peers',
        mapOwner:'Chief Product Officer', mapSuccessCriteria:'Annual competitive assessment completed; R&D intensity benchmark in GRC; AC review completed', reductionPct:12 },

      { id:'R-06', name:'Gross Margin & Operating Leverage', category:'Financial Reporting', base:4.5, ceBase:'ADEQUATE',
        delta: r => tier(r.grossMargin,0,[v=>v<0.55,2.0],[v=>v<0.65,0.8],[v=>v>0.80,-0.5]) + tier(r.niMargin,0,[v=>v<0,1.0]),
        narrative: (r,t) => `Gross margin ${pct(r.grossMargin)} (software sector typically >60%). ${r.grossMargin!=null&&r.grossMargin<0.65?'Below-sector gross margin may indicate infrastructure cost inefficiency or services mix dilution.':'Gross margin consistent with software sector norms.'} Net income margin ${pct(r.niMargin)}.`,
        obj:'Analyse cloud infrastructure cost efficiency, G&A operating leverage, and gross margin bridge by segment',
        controls:['GM-601 Cloud cost optimisation review','GM-605 G&A operating leverage','GM-610 Segment margin reporting'],
        mapFinding:'Cloud cost optimisation savings not independently validated by IA; G&A leverage not tracked in GRC',
        mapAction:'Add cloud cost efficiency KPI to quarterly IA dashboard; validate optimisation savings with engineering finance',
        mapOwner:'CFO / CTO', mapSuccessCriteria:'Cloud cost KPI in GRC; savings validated; G&A leverage tracked quarterly', reductionPct:14 },

      { id:'R-07', name:'M&A Integration Risk', category:'Operational', base:4.5, ceBase:'ADEQUATE',
        delta: r => tier(r.assetGrowth,0,[v=>v>0.30,1.5],[v=>v>0.15,0.8]) + tier(r.dsri,0,[v=>v>1.20,0.7]),
        narrative: (r,t) => `Asset growth ${pct(r.assetGrowth)} YoY. ${r.assetGrowth!=null&&r.assetGrowth>0.15?'Elevated asset growth vs. revenue growth may indicate acquisition activity — integration controls and goodwill impairment testing warrant IA review.':'Asset growth within organic range.'}`,
        obj:'Assess M&A integration control framework, purchase price allocation, and synergy realisation tracking',
        controls:['MA-701 Integration milestone tracking','MA-705 PPA methodology review','MA-710 Synergy KPI tracking'],
        mapFinding:'Integration milestones not tracked in IA register; synergy estimates not independently validated',
        mapAction:'Add M&A integration KRIs to risk register; IA review of synergy model assumptions; PPA methodology walkthrough',
        mapOwner:'Chief Strategy Officer / Chief Accounting Officer', mapSuccessCriteria:'Integration KRIs in GRC; synergy model reviewed; PPA walkthrough completed', reductionPct:15 },

      { id:'R-08', name:'Talent — Engineering & Technical Retention', category:'Operational', base:4.5, ceBase:'ADEQUATE',
        delta: r => tier(r.sgaIntensity,0,[v=>v>0.40,1.0],[v=>v>0.30,0.4]),
        narrative: (r,t) => `SGA intensity ${pct(r.sgaIntensity)}. ${r.sgaIntensity!=null&&r.sgaIntensity>0.35?'High SGA relative to revenue may reflect elevated talent acquisition and retention costs.':'SGA within expected range for growth-stage software.'} Engineering attrition tracking is a required IA KRI.`,
        obj:'Review engineering attrition KPIs, compensation benchmarking, and succession plan coverage',
        controls:['TALENT-801 Engineering attrition KPI','TALENT-805 Compensation benchmarking','TALENT-810 Succession coverage'],
        mapFinding:'Engineering attrition not reported to IA; succession plan coverage for senior technical roles undocumented',
        mapAction:'Add attrition KRI to quarterly IA report; assess succession coverage for Tier-1 technical roles',
        mapOwner:'Chief People Officer', mapSuccessCriteria:'Attrition KRI in IA dashboard; succession coverage documented for top-20 technical roles', reductionPct:10 },
    ],

    'Financial Services': [
      { id:'R-01', name:'Credit Quality — Delinquency & Reserve Adequacy', category:'Financial Reporting', base:5.5, ceBase:'ADEQUATE',
        delta: r => tier(r.mscore,0,[v=>v>-1.78,2.5],[v=>v>-2.22,1.2]) + tier(r.dsri,0,[v=>v>1.15,1.0]) + tier(r.tata,0,[v=>v>0.05,0.8]),
        narrative: (r,t) => `M-score ${fmt(r.mscore,2)}, DSRI ${fmt(r.dsri,2)}, TATA ${fmt(r.tata,3)}. ${r.mscore>-1.78?'Elevated financial reporting signals — reserve adequacy and CECL model calibration warrant priority review.':r.mscore>-2.22?'Gray zone — reserve model vintage and macro overlay require validation.':'Reporting signals within normal range.'}`,
        obj:'Audit loan loss reserve CECL model — vintage calibration, delinquency trend, and macro overlay adequacy',
        controls:['FCQ-101 CECL model methodology review','FCQ-105 Delinquency vintage analysis','FCQ-110 NCO trend monitoring'],
        mapFinding:'CECL model uses pre-rate-environment vintage data; macro overlay procedure not formally documented',
        mapAction:'Recalibrate model to include current vintages; implement annual macro overlay; stress-test at +150bps',
        mapOwner:'Chief Risk Officer', mapSuccessCriteria:'Updated CECL model approved by AC; reserve recalculated; stress scenario disclosed', reductionPct:22 },

      { id:'R-02', name:'Interest Rate Risk — NIM Sensitivity', category:'Financial Reporting', base:5.5, ceBase:'ADEQUATE',
        delta: r => tier(r.niMargin,0,[v=>v<0.05,2.0],[v=>v<0.10,1.0]),
        narrative: (r,t) => `Net income margin ${pct(r.niMargin)}. NIM sensitivity to rate changes is a primary financial risk. Stress testing NIM at ±200bps scenarios and disclosing asset/liability repricing gaps are key audit requirements.`,
        obj:'Stress-test NIM sensitivity to ±200bps rate scenarios and review asset/liability repricing gap disclosure',
        controls:['IR-201 NIM stress model','IR-205 A/L repricing gap report','IR-210 ALCO governance'],
        mapFinding:'NIM stress scenario at +200bps not stress-tested; repricing gap disclosure limited',
        mapAction:'Produce formal NIM stress test at +200bps and -100bps; include in quarterly IA reporting and board disclosure',
        mapOwner:'Chief Treasury Officer', mapSuccessCriteria:'NIM stress model documented; ±200bps scenario in quarterly IA pack; disclosure updated', reductionPct:18 },

      { id:'R-03', name:'Liquidity — Funding Concentration', category:'Financial Reporting', base:5.5, ceBase:'ADEQUATE',
        delta: r => tier(r.cashRatio,0,[v=>v<0.05,2.5],[v=>v<0.10,1.0],[v=>v>0.20,-0.5]),
        narrative: (r,t) => `Cash/assets ratio ${pct(r.cashRatio)}. ${r.cashRatio!=null&&r.cashRatio<0.10?'Low cash ratio warrants liquidity stress testing and deposit concentration review.':'Liquidity position within normal range.'} 30-day outflow stress scenario and contingency funding plan are primary IA requirements.`,
        obj:'Review deposit concentration, contingent liquidity sources, and 30-day stress outflow adequacy',
        controls:['LIQ-301 Deposit concentration mapping','LIQ-305 30-day stress outflow test','LIQ-310 Contingency funding plan'],
        mapFinding:'Top-20 deposit concentration not mapped in IA register; 30-day outflow stress not IA-reviewed',
        mapAction:'Map top-20 deposit concentration; stress-test 30-day outflow vs. liquidity buffer; validate contingency plan',
        mapOwner:'Chief Treasury Officer', mapSuccessCriteria:'Concentration mapped; stress test completed; contingency plan validated by IA', reductionPct:16 },

      { id:'R-04', name:'Regulatory Capital — CET1 & Stress Test', category:'Compliance', base:5.0, ceBase:'ADEQUATE',
        delta: r => 0.5,
        narrative: (r,t) => `Regulatory capital adequacy (CET1 ratio, stress capital buffer, DFAST/CCAR compliance) is a core IA responsibility for banks and financial institutions. Annual IA attestation of DFAST submission is required.`,
        obj:'Test CET1 ratio adequacy, stress capital buffer vs. DFAST, and review regulatory capital disclosure controls',
        controls:['CAP-401 CET1 ratio monitoring','CAP-405 DFAST submission review','CAP-410 Stress capital buffer adequacy'],
        mapFinding:'DFAST submission not IA-attested; CET1 stress scenario below DFAST requirements not tested',
        mapAction:'Annual IA attestation of DFAST submission; CET1 threshold review vs. regulatory minimum + buffer',
        mapOwner:'Chief Compliance Officer', mapSuccessCriteria:'IA attestation completed; CET1 threshold review documented; AC review of results', reductionPct:14 },

      { id:'R-05', name:'Operational Risk — Fraud & AML', category:'Operational', base:5.5, ceBase:'ADEQUATE',
        delta: r => 0.5,
        narrative: (r,t) => `AML transaction monitoring effectiveness and alert disposition quality are key operational risks. Regulatory enforcement environment (BSA, FATF) has heightened expectations for real-time monitoring and escalation.`,
        obj:'Evaluate AML transaction monitoring effectiveness, alert disposition SLAs, and SAR filing completeness',
        controls:['AML-501 TM tuning review','AML-505 Alert disposition SLA','AML-510 SAR filing completeness'],
        mapFinding:'AML alert disposition SLA breaches not escalated to IA in real time; TM tuning documented but not IA-tested',
        mapAction:'Integrate AML SLA breach alerts into IA dashboard; quarterly IA review of TM model tuning decisions',
        mapOwner:'Chief AML Officer', mapSuccessCriteria:'AML breaches in IA dashboard; quarterly TM tuning review completed; SLA compliance >98%', reductionPct:20 },

      { id:'R-06', name:'Cybersecurity — Core Systems & Customer Data', category:'Cybersecurity', base:6.0, ceBase:'ADEQUATE',
        delta: r => 0,
        narrative: (r,t) => `Core banking system access controls, privileged user management, and customer data protection are critical cybersecurity IA priorities. Privileged access reviews overdue >90 days create regulatory and reputational exposure.`,
        obj:'Test core banking access controls, privileged user management, and customer data security controls',
        controls:['ITGC-601 Privileged access review','ITGC-605 Core banking access controls','ITGC-610 Customer data encryption'],
        mapFinding:'Privileged user access reviews overdue by >90 days across core banking; customer data encryption policy not IA-reviewed',
        mapAction:'Implement quarterly privileged access reviews; add to IA annual plan; validate customer data encryption coverage',
        mapOwner:'CISO', mapSuccessCriteria:'Quarterly access reviews on schedule; no reviews overdue >30d; encryption coverage 100%', reductionPct:15 },

      { id:'R-07', name:'Model Risk — CECL & Pricing Model Validation', category:'Financial Reporting', base:5.0, ceBase:'ADEQUATE',
        delta: r => tier(r.mscore,0,[v=>v>-1.78,1.5],[v=>v>-2.22,0.8]),
        narrative: (r,t) => `Model risk encompasses CECL reserve models, fair-value pricing models, and risk models. Independent model validation cadence and model risk governance framework are core IA requirements.`,
        obj:'Review model validation independence, CECL model risk governance, and fair-value methodology documentation',
        controls:['MR-701 Independent model validation','MR-705 Model inventory completeness','MR-710 Fair-value methodology review'],
        mapFinding:'CECL model not independently validated in >12 months; model inventory completeness unverified',
        mapAction:'Commission independent CECL model validation; verify model inventory covers all material models',
        mapOwner:'Chief Risk Officer / Chief Model Risk Officer', mapSuccessCriteria:'CECL validation completed; model inventory verified; validation cadence documented', reductionPct:16 },

      { id:'R-08', name:'Market Risk — Trading & Derivatives Valuation', category:'Financial Reporting', base:4.5, ceBase:'ADEQUATE',
        delta: r => tier(r.niMargin,0,[v=>v<0,1.5]),
        narrative: (r,t) => `Derivatives fair-value methodology and VaR backtesting results are market risk IA requirements. VaR model exceedances and Level 3 fair value transfers are early warning signals for measurement risk.`,
        obj:'Review derivatives fair-value methodology, VaR model backtesting, and Level 3 fair value transfer disclosures',
        controls:['MKT-801 VaR backtesting review','MKT-805 Fair-value methodology','MKT-810 Level 3 transfer disclosure'],
        mapFinding:'VaR backtesting results not presented to IA in current year; Level 3 transfer disclosures not IA-reviewed',
        mapAction:'Include VaR backtesting in quarterly IA reporting; validate fair-value methodology for top-20 derivative positions',
        mapOwner:'Chief Market Risk Officer', mapSuccessCriteria:'VaR results in quarterly IA pack; fair-value methodology documented; Level 3 disclosures reviewed', reductionPct:12 },
    ],

    'Healthcare & Pharma': [
      { id:'R-01', name:'Pipeline — Clinical Trial & FDA Approval Risk', category:'Operational', base:6.0, ceBase:'ADEQUATE',
        delta: r => tier(r.rdIntensity,0,[v=>v<0.10,2.0],[v=>v<0.18,0.8],[v=>v>0.25,-0.5]),
        narrative: (r,t) => `R&D intensity ${pct(r.rdIntensity)} (pharma sector typically 15–25%). ${r.rdIntensity!=null&&r.rdIntensity<0.18?'Below-average R&D investment relative to revenue creates pipeline adequacy risk.':'R&D investment within sector range.'} Clinical milestone tracking integration with GRC is the key IA requirement.`,
        obj:'Audit clinical trial milestone tracking, approval probability assumptions, and portfolio NPV estimates',
        controls:['RD-101 Clinical milestone tracking','RD-105 Approval probability methodology','RD-110 Portfolio NPV review'],
        mapFinding:'Clinical trial milestone system not linked to GRC risk register; approval probability assumptions not IA-reviewed',
        mapAction:'Link clinical milestone system to GRC; quarterly review of Phase 2/3 milestones; validate approval probability methodology',
        mapOwner:'Chief Medical Officer', mapSuccessCriteria:'GRC integration live; quarterly milestone review completed; probability methodology documented', reductionPct:20 },

      { id:'R-02', name:'Pricing Pressure — Gross-to-Net & IRA Impact', category:'Financial Reporting', base:5.5, ceBase:'ADEQUATE',
        delta: r => tier(r.grossMargin,0,[v=>v<0.50,2.0],[v=>v<0.65,1.0],[v=>v>0.80,-0.5]),
        narrative: (r,t) => `Gross margin ${pct(r.grossMargin)} (pharma typically 65–85%). ${r.grossMargin!=null&&r.grossMargin<0.65?'Below-sector gross margin may reflect unfavourable gross-to-net adjustments or price negotiation impact.':'Gross margin within sector range.'} IRA price negotiation exposure requires scenario analysis.`,
        obj:'Analyse gross-to-net adjustments, IRA price negotiation exposure, and rebate liability accrual adequacy',
        controls:['PRICE-201 Gross-to-net methodology','PRICE-205 Rebate liability accrual','PRICE-210 IRA exposure scenario'],
        mapFinding:'Gross-to-net methodology not reviewed by IA in current year; IRA price negotiation scenario analysis absent',
        mapAction:'Annual IA review of gross-to-net model; IRA exposure scenario at -25% and -40% price; rebate accrual validation',
        mapOwner:'CFO / Head of Managed Care', mapSuccessCriteria:'GTN model reviewed; IRA scenario in AC pack; rebate accrual validated', reductionPct:18 },

      { id:'R-03', name:'IP Expiry — Patent Cliff & Generic Entry', category:'Revenue', base:5.5, ceBase:'ADEQUATE',
        delta: r => tier(r.revGrowth,0,[v=>v<-0.10,2.5],[v=>v<0,1.0]),
        narrative: (r,t) => `Revenue ${pct(r.revGrowth)} YoY. ${r.revGrowth!=null&&r.revGrowth<0?'Declining revenue may reflect patent expiry and generic competition impact.':'Revenue trend suggests patent cliff not yet materializing.'} Patent expiry schedule and revenue-at-risk quantification are required GRC inputs.`,
        obj:'Map patent expiry schedule, generic entry timeline, and revenue-at-risk estimates for top-5 products',
        controls:['IP-301 Patent expiry register','IP-305 Generic entry monitoring','IP-310 Revenue-at-risk quantification'],
        mapFinding:'Patent expiry schedule not maintained in GRC; revenue-at-risk from generic entry unquantified',
        mapAction:'Maintain patent cliff register in GRC; quantify revenue-at-risk annually; monitor generic filing activity',
        mapOwner:'Chief Strategy Officer / Head of IP', mapSuccessCriteria:'Patent register in GRC; revenue-at-risk quantified for all products with <5yr patent life', reductionPct:16 },

      { id:'R-04', name:'Manufacturing Quality — GMP & Regulatory Compliance', category:'Compliance', base:5.5, ceBase:'ADEQUATE',
        delta: r => 0.5,
        narrative: (r,t) => `GMP compliance and FDA/EMA regulatory quality are existential risks for pharmaceutical companies. Prior warning letters, consent decrees, and manufacturing site remediation are primary IA focus areas.`,
        obj:'Evaluate GMP compliance status, FDA warning letter remediation, and quality system effectiveness',
        controls:['QA-401 GMP compliance monitoring','QA-405 FDA remediation milestones','QA-410 Quality management system review'],
        mapFinding:'FDA remediation milestones not tracked in IA risk register; quality system review not on IA annual plan',
        mapAction:'Add FDA milestones to IA register; annual IA review of quality management system; GMP compliance KRI',
        mapOwner:'Chief Quality Officer', mapSuccessCriteria:'FDA milestones in GRC; annual QMS review completed; no open Warning Letter items >180 days', reductionPct:20 },

      { id:'R-05', name:'R&D Productivity — Investment vs. Pipeline Value', category:'Operational', base:5.0, ceBase:'ADEQUATE',
        delta: r => tier(r.rdIntensity,0,[v=>v<0.10,2.0],[v=>v<0.18,0.8]) + tier(r.fcfMargin,0,[v=>v<0,1.0]),
        narrative: (r,t) => `R&D intensity ${pct(r.rdIntensity)}, FCF margin ${pct(r.fcfMargin)}. ${r.rdIntensity!=null&&r.rdIntensity<0.15?'Below-peer R&D investment combined with pipeline adequacy concern.':'R&D intensity within sector range.'} Pipeline NPV vs. R&D investment linkage is the primary IA requirement.`,
        obj:'Benchmark R&D intensity vs. pipeline NPV and NME output rate relative to peer set',
        controls:['RD-501 Pipeline NPV methodology','RD-505 NME output rate KPI','RD-510 R&D investment vs. return bridge'],
        mapFinding:'R&D investment vs. pipeline NPV linkage not reviewed by IA; NME output rate not benchmarked vs. peers',
        mapAction:'Commission annual R&D productivity review with pipeline NPV bridge; NME output benchmarking vs. 5 peers',
        mapOwner:'Chief Scientific Officer', mapSuccessCriteria:'Annual R&D productivity review completed; NPV bridge in AC pack; NME benchmarking documented', reductionPct:14 },

      { id:'R-06', name:'Litigation — Product Liability & False Claims Act', category:'Legal', base:5.5, ceBase:'ADEQUATE',
        delta: r => tier(r.mscore,0,[v=>v>-1.78,1.5],[v=>v>-2.22,0.8]),
        narrative: (r,t) => `Product liability litigation and False Claims Act exposure are material contingent liabilities. Litigation reserve adequacy and completeness of legal-letter procedures are primary IA requirements.`,
        obj:'Review litigation reserve adequacy, False Claims Act exposure assessment, and legal-letter procedure completeness',
        controls:['LEGAL-601 Litigation reserve methodology','LEGAL-605 FCA exposure assessment','LEGAL-610 Legal letter procedure'],
        mapFinding:'Litigation reserve methodology not validated by IA in current cycle; FCA exposure assessment not completed',
        mapAction:'Annual IA review of litigation reserve; FCA exposure quantification; legal letter procedure completeness test',
        mapOwner:'General Counsel', mapSuccessCriteria:'Reserve methodology reviewed; FCA exposure quantified; legal letter completeness >95%', reductionPct:16 },

      { id:'R-07', name:'Cybersecurity — PHI & Medical Device Security', category:'Cybersecurity', base:5.5, ceBase:'ADEQUATE',
        delta: r => 0,
        narrative: (r,t) => `PHI data classification, HIPAA/GDPR compliance, and connected medical device vulnerability management are critical cybersecurity IA requirements. Medical device security has become a primary FDA concern.`,
        obj:'Assess PHI data classification controls, HIPAA compliance effectiveness, and medical device vulnerability management',
        controls:['ITGC-701 PHI classification and access','ITGC-705 HIPAA compliance program','ITGC-710 Medical device vulnerability management'],
        mapFinding:'Medical device vulnerability management not formally IA-reviewed; PHI access controls not tested in current year',
        mapAction:'Annual IA review of medical device security program; PHI access controls testing; HIPAA compliance assessment',
        mapOwner:'CISO / Chief Privacy Officer', mapSuccessCriteria:'Medical device review completed; PHI access controls tested; HIPAA assessment documented', reductionPct:14 },

      { id:'R-08', name:'ESG — Emissions & Access to Medicines', category:'ESG', base:4.5, ceBase:'ADEQUATE',
        delta: r => 0.5,
        narrative: (r,t) => `Scope 1/2/3 emissions methodology and access-to-medicines program disclosures are material ESG IA requirements. Investor and regulatory scrutiny of sustainability disclosures continues to increase.`,
        obj:'Validate Scope 1/2/3 emissions methodology and access-to-medicines program disclosure accuracy',
        controls:['ESG-801 Scope 1/2 verification','ESG-805 Scope 3 methodology','ESG-810 Access program metrics'],
        mapFinding:'Scope 3 methodology not independently verified; access program metrics not reviewed by IA',
        mapAction:'Commission third-party Scope 3 verification; IA review of access program metrics and disclosure accuracy',
        mapOwner:'Chief Sustainability Officer', mapSuccessCriteria:'Scope 3 verified by third party; access program metrics IA-reviewed; disclosures accurate', reductionPct:10 },
    ],

    'Generic': [
      { id:'R-01', name:'Financial Reporting Quality', category:'Financial Reporting', base:4.5, ceBase:'ADEQUATE',
        delta: r => tier(r.mscore,0,[v=>v>-1.78,3.5],[v=>v>-2.22,1.5]) + tier(r.tata,0,[v=>v>0.06,1.0]) + tier(r.dsri,0,[v=>v>1.20,0.8]),
        narrative: (r,t) => `M-score ${fmt(r.mscore,2)} (threshold -1.78 likely manipulator, -2.22 gray zone). Accruals ratio ${fmt(r.tata,3)}. DSRI ${fmt(r.dsri,2)}. ${r.mscore>-1.78?'Elevated M-score warrants detailed revenue recognition and accruals review.':r.mscore>-2.22?'Gray zone — accruals and AR ageing monitoring recommended.':'Financial reporting indicators within normal range.'}`,
        obj:'Review financial reporting controls, accruals quality, and key accounting estimate methodology',
        controls:['FRP-101 Accruals quality monitoring','FRP-105 Revenue recognition policy','FRP-110 Key estimate methodology'],
        mapFinding:'Accruals quality indicators not in GRC financial risk dashboard; key estimate methodology review not on IA plan',
        mapAction:'Add M-score and TATA to quarterly GRC dashboard; key estimate methodology review on IA annual plan',
        mapOwner:'Chief Accounting Officer', mapSuccessCriteria:'M-score dashboarded; key estimate review completed; threshold alerts configured', reductionPct:18 },

      { id:'R-02', name:'Revenue Growth & Concentration Risk', category:'Revenue', base:5.0, ceBase:'ADEQUATE',
        delta: r => tier(r.revGrowth,0,[v=>v<-0.15,2.5],[v=>v<0,1.0],[v=>v>0.20,-0.8]),
        narrative: (r,t) => `Revenue growth ${pct(r.revGrowth)} YoY (${fmt(r.rev?r.rev/1e6:null,0)}M latest). ${r.revGrowth!=null&&r.revGrowth<0?'Declining revenue warrants concentration analysis — customer, product, and geographic diversification review required.':r.revGrowth>0.15?'Strong growth; revenue recognition controls should match pace of growth.':'Revenue growth moderate; standard concentration review applies.'}`,
        obj:'Analyse revenue segment mix, customer concentration, and geographic diversification',
        controls:['REV-201 Revenue concentration KRI','REV-205 Segment reporting review','REV-210 Customer diversification analysis'],
        mapFinding:'Revenue concentration by customer not systematically tracked in GRC; segment reporting not IA-reviewed annually',
        mapAction:'Implement concentration KRI in GRC; annual IA review of top-10 customer and segment revenue composition',
        mapOwner:'Chief Revenue Officer', mapSuccessCriteria:'Concentration KRI live in GRC; top-10 customer analysis in annual IA report', reductionPct:15 },

      { id:'R-03', name:'Profitability — Margin Compression', category:'Financial Reporting', base:4.5, ceBase:'ADEQUATE',
        delta: r => tier(r.grossMargin,0,[v=>v<0.20,2.5],[v=>v<0.35,1.2],[v=>v>0.60,-0.5]) + tier(r.niMargin,0,[v=>v<-0.05,1.5],[v=>v<0,0.8]),
        narrative: (r,t) => `Gross margin ${pct(r.grossMargin)}, net income margin ${pct(r.niMargin)}. ${r.grossMargin!=null&&r.grossMargin<0.30?'Thin margins leave limited buffer for cost absorption — operating leverage and cost management controls are a priority.':'Margin profile adequate at current levels.'} FCF margin ${pct(r.fcfMargin)}.`,
        obj:'Audit gross margin variance bridge, operating cost efficiency, and pricing governance',
        controls:['MARGIN-301 Gross margin bridge','MARGIN-305 Operating cost efficiency KPI','MARGIN-310 Pricing governance'],
        mapFinding:'Gross margin variance bridge not produced for IA; pricing governance policy not documented',
        mapAction:'Require quarterly margin bridge to IA; document pricing approval governance; add operating leverage to GRC',
        mapOwner:'CFO', mapSuccessCriteria:'Quarterly margin bridge delivered; pricing policy published; operating leverage KPI in GRC', reductionPct:14 },

      { id:'R-04', name:'Liquidity & Cash Flow Adequacy', category:'Financial Reporting', base:5.0, ceBase:'ADEQUATE',
        delta: r => tier(r.fcfMargin,0,[v=>v<-0.10,3.0],[v=>v<0,1.5],[v=>v>0.15,-0.5]) + tier(r.cashRatio,0,[v=>v<0.05,2.0],[v=>v<0.10,1.0]) + tier(r.zscore,0,[v=>v<=1.1,2.5],[v=>v<=2.6,1.0]),
        narrative: (r,t) => `FCF margin ${pct(r.fcfMargin)}, cash/assets ${pct(r.cashRatio)}, Altman Z''-score ${fmt(r.zscore,2)} (distress ≤1.1, grey ≤2.6). ${r.zscore!=null&&r.zscore<=1.1?'Z\'\'-score in distress zone — going-concern assessment and covenant headroom require immediate IA review.':r.fcfMargin!=null&&r.fcfMargin<0?'Negative FCF — liquidity runway and covenant headroom require immediate IA review.':r.cashRatio!=null&&r.cashRatio<0.08?'Low cash ratio warrants liquidity stress testing.':'Liquidity and cash generation within acceptable range.'}`,
        obj:'Stress-test FCF adequacy at -20% and -40% revenue scenarios and review covenant compliance headroom',
        controls:['LIQ-401 FCF stress model','LIQ-405 Covenant headroom monitoring','LIQ-410 Liquidity buffer adequacy'],
        mapFinding:'FCF stress test not reviewed by IA; covenant headroom not formally reported to IA on quarterly cadence',
        mapAction:'Annual IA FCF stress test at -20% and -40% scenarios; covenant headroom in quarterly IA dashboard',
        mapOwner:'CFO / Treasurer', mapSuccessCriteria:'FCF stress test completed; covenant headroom tracked quarterly; IA notified at <20% headroom', reductionPct:16 },

      { id:'R-05', name:'Cybersecurity & Data Protection', category:'Cybersecurity', base:5.5, ceBase:'ADEQUATE',
        delta: r => 0,
        narrative: (r,t) => `Cybersecurity framework maturity and key control effectiveness (access management, network segmentation, incident response) are standard IA requirements. Annual maturity assessment against NIST CSF or ISO 27001 is the minimum standard.`,
        obj:'Assess cybersecurity framework maturity, key control effectiveness, and incident response preparedness',
        controls:['CYBER-501 NIST CSF / ISO 27001 assessment','CYBER-505 Privileged access review','CYBER-510 Incident response plan test'],
        mapFinding:'Cybersecurity maturity assessment not conducted by IA in current year; incident response not tabletop-tested',
        mapAction:'Annual IA cybersecurity maturity assessment; prioritise top-3 control gaps; tabletop incident response exercise',
        mapOwner:'CISO', mapSuccessCriteria:'Maturity assessment completed; top-3 gaps remediated; tabletop exercise annual cadence established', reductionPct:15 },

      { id:'R-06', name:'Regulatory & Compliance Risk', category:'Compliance', base:5.0, ceBase:'ADEQUATE',
        delta: r => 0.5,
        narrative: (r,t) => `Regulatory compliance risk encompasses industry-specific requirements (SEC, CFPB, EPA, OSHA) and cross-cutting obligations (GDPR, CCPA, AML). A regulatory change monitoring register in GRC is the foundational control.`,
        obj:'Review compliance program effectiveness and regulatory change monitoring process',
        controls:['COMP-601 Regulatory change register','COMP-605 Compliance program effectiveness review','COMP-610 Consent order / settlement monitoring'],
        mapFinding:'Regulatory change monitoring not formally tracked in GRC; compliance program effectiveness not IA-reviewed',
        mapAction:'Implement regulatory change register in GRC; quarterly IA compliance review; consent order tracking',
        mapOwner:'Chief Compliance Officer', mapSuccessCriteria:'Regulatory register live in GRC; quarterly compliance review on schedule; no untracked consent orders', reductionPct:13 },

      { id:'R-07', name:'Operational Execution Risk', category:'Operational', base:4.5, ceBase:'ADEQUATE',
        delta: r => tier(r.sgaIntensity,0,[v=>v>0.30,1.0]) + tier(r.assetGrowth,0,[v=>v>0.25,0.8]),
        narrative: (r,t) => `SGA intensity ${pct(r.sgaIntensity)}, asset growth ${pct(r.assetGrowth)} YoY. ${r.sgaIntensity!=null&&r.sgaIntensity>0.25?'Elevated overhead vs. peers suggests operating inefficiency or rapid scaling ahead of revenue.':'Operating cost structure within sector range.'} Key operating KPIs should be linked to GRC risk triggers.`,
        obj:'Evaluate key operating KPIs, process controls for critical operations, and operating leverage metrics',
        controls:['OPS-701 Key KPI dashboard','OPS-705 Critical process controls review','OPS-710 Operating leverage monitoring'],
        mapFinding:'Key operating KPIs not linked to GRC risk register triggers; critical process controls not on IA annual plan',
        mapAction:'Define KPI-to-risk trigger linkage in GRC; add critical process review to IA annual plan',
        mapOwner:'COO', mapSuccessCriteria:'KPIs linked to GRC triggers; critical process review completed; operating leverage tracked', reductionPct:12 },

      { id:'R-08', name:'Strategic Risk — Concentration & Diversification', category:'Strategic', base:5.0, ceBase:'ADEQUATE',
        delta: r => tier(r.revGrowth,0,[v=>v<-0.10,1.5]) + tier(r.assetGrowth,0,[v=>v!=null&&r.revGrowth!=null&&v>r.revGrowth+0.20,1.0]),
        narrative: (r,t) => `Revenue growth ${pct(r.revGrowth)}, asset growth ${pct(r.assetGrowth)}. Strategic risk encompasses customer, product, geographic, and channel concentration. Annual IA review of strategic plan execution and concentration metrics is the baseline requirement.`,
        obj:'Map revenue and asset concentration by customer, product, and geography; review strategic plan execution',
        controls:['STRAT-801 Revenue concentration analysis','STRAT-805 Strategic plan execution KPI','STRAT-810 M&A integration monitoring'],
        mapFinding:'Revenue concentration analysis not produced for IA in current year; strategic plan execution KPIs not in GRC',
        mapAction:'Annual IA review of concentration; strategic execution KPIs in GRC with threshold alerts',
        mapOwner:'Chief Strategy Officer', mapSuccessCriteria:'Concentration analysis in annual IA report; strategic KPIs in GRC; threshold alerts configured', reductionPct:14 },
    ],
  };

  // ── Fallback aliases for unlisted industries ────────────────
  TEMPLATES['Industrial & Manufacturing'] = TEMPLATES['Generic'];
  TEMPLATES['Retail & Consumer']           = TEMPLATES['Generic'];
  TEMPLATES['Energy & Resources']          = TEMPLATES['Generic'];
  TEMPLATES['Healthcare & Pharma']         = TEMPLATES['Healthcare & Pharma'] || TEMPLATES['Generic'];
  TEMPLATES['Utilities']                   = TEMPLATES['Generic'];

  // ── Build Risk Array from template + ratios ─────────────────
  // score = impact (0-5) × likelihood (0-5) → 0-25 scale
  function buildRisks(industry, ratios, ticker) {
    const tmpl = TEMPLATES[industry] || TEMPLATES['Generic'];
    return tmpl.map(t => {
      const delta      = t.delta(ratios);
      const rawScore   = clamp(t.base + delta, 1.5, 9.5);   // 0-10 intermediate
      const likelihood = clamp(rawScore / 2, 0.5, 5.0);     // 0-5 scale
      const impact     = CATEGORY_IMPACT[t.category] || 3;  // 0-5 scale
      const score      = +(impact * likelihood).toFixed(1);  // 0-25 scale
      const base25     = impact * (t.base / 2);              // base on 0-25 scale
      const ce         = ceOf(score, base25);
      const vel        = velOf(score, base25);
      return {
        id:       t.id,
        name:     t.name,
        category: t.category,
        impact,
        likelihood: +likelihood.toFixed(1),
        score,
        rag:      ragOf(score),
        velocity: vel,
        ce,
        inherent: +clamp(score + 2.5, score, 25.0).toFixed(1),
        residual: score,
        peer:     score > base25 + 2.5 ? 'above' : score < base25 - 1.25 ? 'below' : 'in-line',
        hist:     histOf(score, base25),
        narrative: t.narrative(ratios, ticker),
      };
    });
  }

  // ── Build Objectives ────────────────────────────────────────
  function buildObjectives(risks, industry) {
    const tmpl = TEMPLATES[industry] || TEMPLATES['Generic'];
    const sorted = [...risks].sort((a, b) => b.score - a.score).slice(0, 6);
    return sorted.map((r, i) => {
      const t = tmpl.find(x => x.id === r.id) || tmpl[0];
      const priority = r.score >= 17 ? 'P1' : r.score >= 11 ? 'P2' : 'P3';
      return {
        id: `O-${String(i + 1).padStart(2, '0')}`,
        priority,
        objective: t.obj || `Audit ${r.name}`,
        linked_risk: r.id,
        sprint: priority === 'P1' ? 1 : priority === 'P2' ? 2 : 3,
        hours: priority === 'P1' ? 120 : priority === 'P2' ? 80 : 40,
        controls: t.controls || [],
        rationale: `${r.name} score ${r.score}/25 (${r.rag}), velocity ${r.velocity > 0 ? '+' : ''}${r.velocity}. ${r.narrative.slice(0, 120)}…`,
      };
    });
  }

  // ── Build MAPs ───────────────────────────────────────────────
  function buildMAPs(risks, objectives) {
    const industry = null; // industry not needed — we use risk templates directly
    return objectives.map((o, i) => {
      const r = risks.find(x => x.id === o.linked_risk);
      if (!r) return null;
      const tmpl = Object.values(TEMPLATES).flat().find(t => t.id === r.id);
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
    const baseRisks = buildRisks(industry, ratios, ticker);
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
      const downScore = buildRisks(industry, downRatios, ticker).reduce((s, r) => s + r.score, 0);
      const upScore   = buildRisks(industry, upRatios, ticker).reduce((s, r) => s + r.score, 0);
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
    return {
      'Chief Audit Executive': {
        sections: top3.map(r => `${r.id} ${r.name.split('—')[0].trim()}`),
        summary: `${red.length > 0 ? `${red.length} RED risk${red.length>1?'s':''} require immediate remediation.` : 'No RED risks; amber risk management is the priority.'} Top priority: ${top3[0]?.name.split('—')[0].trim() || 'primary risk'} — highest score and velocity.`,
        headline: `Audit impact score derived from risk velocity and score distribution across ${risks.length} risks.`,
      },
      'Chief Financial Officer': {
        sections: risks.filter(r=>r.category==='Financial Reporting'||r.category==='Revenue').slice(0,3).map(r=>`${r.id} ${r.name.split('—')[0].trim()}`),
        summary: `Financial reporting risks: ${risks.filter(r=>r.category==='Financial Reporting').length} identified. ${risks.find(r=>r.category==='Financial Reporting'&&r.rag==='R')?'At least one financial reporting risk at RED — potential disclosure implications.':'Financial reporting risks within amber range; accruals and estimate review required.'}`,
        headline: `M-score and accruals ratio are the primary financial reporting early-warning signals for this entity.`,
      },
      'Chief Operating Officer': {
        sections: risks.filter(r=>r.category==='Operational'||r.category==='Supply').slice(0,3).map(r=>`${r.id} ${r.name.split('—')[0].trim()}`),
        summary: `Operational risks: ${risks.filter(r=>r.category==='Operational'||r.category==='Supply').length} identified. Priority: KRI integration with GRC to ensure velocity signals are captured before risks breach amber thresholds.`,
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
  function buildForecasts(ratios, ticker, industry, fin) {
    const { lastY, lastQ, fcLabels, defaultLatestQ } = _quarterBoundaries();
    // Shared helper — keeps only standalone quarterly periods (Q1/Q2/Q3/Q4),
    // excluding YTD cumulative entries (H1, 9M, etc.) that some companies tag
    // with fp values outside the Q1-Q4 set in EDGAR XBRL.
    const isStandaloneQ = x => /^Q[1-4]$/.test(x.fp);

    const histQuarters = [];
    if (fin?.revenue?.series) {
      // Deduplicate by end date before sorting: for any two entries with the same
      // period-end (standalone Q vs YTD), prefer the more recently filed one.
      const revByEnd = {};
      fin.revenue.series
        .filter(x => x.form === '10-Q' && isStandaloneQ(x))
        .forEach(x => {
          if (!x.end) return;
          if (!(x.end in revByEnd) || (x.filed || '') > (revByEnd[x.end].filed || ''))
            revByEnd[x.end] = x;
        });
      Object.values(revByEnd)
        .sort((a, b) => a.end > b.end ? 1 : -1)
        .slice(-12)
        .forEach(q => {
          const label = edgarDateToQLabel(q.end) || q.fp;
          histQuarters.push({ q: label, v: +(q.val / 1e6).toFixed(0) });
        });
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
      const revMap = {};
      fin.revenue.series
        .filter(x => x.form === '10-Q' && isStandaloneQ(x))
        .forEach(x => { if (x.end) revMap[x.end] = x.val; });
      fin.cogs.series
        .filter(x => x.form === '10-Q' && isStandaloneQ(x) && revMap[x.end])
        .sort((a, b) => a.end > b.end ? 1 : -1)
        .slice(-12)
        .forEach(c => {
          const rv = revMap[c.end];
          const gm = rv > 0 ? (1 - c.val / rv) * 100 : null;
          if (gm != null && gm > 0 && gm < 100)
            histMargins.push({ q: edgarDateToQLabel(c.end) || c.end.slice(0, 7), v: +gm.toFixed(1) });
        });
    }
    // Fallback: use GrossProfit series directly if COGS-based matching gave < 4 quarters
    if (histMargins.length < 4 && fin?.grossProfit?.series && fin?.revenue?.series) {
      histMargins.length = 0;
      const revMap2 = {};
      fin.revenue.series
        .filter(x => x.form === '10-Q' && isStandaloneQ(x))
        .forEach(x => { if (x.end) revMap2[x.end] = x.val; });
      fin.grossProfit.series
        .filter(x => x.form === '10-Q' && isStandaloneQ(x) && revMap2[x.end])
        .sort((a, b) => a.end > b.end ? 1 : -1)
        .slice(-12)
        .forEach(p => {
          const rv = revMap2[p.end];
          const gm = rv > 0 ? (p.val / rv) * 100 : null;
          if (gm != null && gm > 0 && gm < 100)
            histMargins.push({ q: edgarDateToQLabel(p.end) || p.end.slice(0, 7), v: +gm.toFixed(1) });
        });
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
    const tmpl = TEMPLATES[industry] || TEMPLATES['Generic'];
    const flow = {};
    risks.forEach(r => {
      const t = tmpl.find(x => x.id === r.id);
      flow[r.id] = {
        impacts:  CATEGORY_IMPACTS[r.category] || DEFAULT_IMPACTS,
        controls: (t?.controls || []).map(name => ({ name, ce: r.ce })),
        audits:   (maps || []).filter(m => m.linked_risk === r.id).map(m => m.id),
        cadence:  cadenceFor(r.velocity ?? 0),
      };
    });
    return flow;
  }

  // ── Main: buildProfile ───────────────────────────────────────
  function buildProfile(ticker, fin, sic, industryHint) {
    const industryLabel = industryHint || sic2industry(sic) || 'Generic';
    const industry = normalizeIndustry(industryLabel);
    const ratios   = computeRatios(fin);
    const risks    = buildRisks(industry, ratios, ticker);
    const objectives = buildObjectives(risks, industry);
    const maps     = buildMAPs(risks, objectives);
    const scenarios  = buildScenarios(risks, ratios, ticker, industry);
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
    return { entity, signals, risks, objectives, maps, closure, loop,
             eventTemplates, forecasts, scenarios, greySwan, ratios,
             reverseStress, historicalAnalogs, governanceScenario,
             riskFlow: buildRiskFlow(risks, industry, maps),
             personas, fred:forecasts.fred,
             varCvar, sensitivity, multiFactorStress, liquidityRunway, earlyWarning };
  }

  return { buildProfile, buildLoop, computeRatios, sic2industry, normalizeIndustry, quarterBoundaries: _quarterBoundaries,
           buildVarCvar, buildSensitivity, buildMultiFactorStress, buildLiquidityRunway, buildEarlyWarningIndicator };

})();
