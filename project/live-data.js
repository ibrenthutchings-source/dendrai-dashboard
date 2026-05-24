/* ============================================================
   Dendrai Risk Loop — live data helpers
   - EDGAR: direct fetch from data.sec.gov (CORS-friendly)
   - FRED:  API fetch (api.stlouisfed.org) + bundled snapshot fallback
   ============================================================ */

window.LIVE = (function () {

  const TICKER_CIK = {
    ON:    "0001097864",
    TXN:   "0000097476",
    STM:   "0001114448",
    MCHP:  "0000827054",
    AVGO:  "0001730168",
    NVDA:  "0001045810",
    INTC:  "0000050863",
    AMD:   "0000002488",
    QCOM:  "0000804328",
    NXPI:  "0001413447",
  };

  // Curated FRED series relevant to semiconductor/industrial companies
  const FRED_SERIES_OPTIONS = [
    { id: 'IPG3344S',        name: 'Semi Production Index',      cat: 'Industry' },
    { id: 'CAPUTLG3311A2S',  name: 'Mfg Capacity Utilization',   cat: 'Industry' },
    { id: 'INDPRO',          name: 'Industrial Production',       cat: 'Macro'    },
    { id: 'MANEMP',          name: 'Manufacturing Employment',    cat: 'Labor'    },
    { id: 'UMCSENT',         name: 'Consumer Sentiment (UMich)',  cat: 'Consumer' },
    { id: 'DTWEXBGS',        name: 'USD Broad Dollar Index',      cat: 'FX'       },
    { id: 'TOTALSA',         name: 'Total Vehicle Sales',         cat: 'Auto'     },
    { id: 'PCU33443344',     name: 'PPI: Semi Manufacturing',     cat: 'Industry' },
    { id: 'GDP',             name: 'Gross Domestic Product',      cat: 'Macro'    },
    { id: 'FEDFUNDS',        name: 'Federal Funds Rate',          cat: 'Rates'    },
    { id: 'T10Y2Y',          name: '10Y-2Y Yield Spread',         cat: 'Rates'    },
    { id: 'DAUPSA',          name: 'New Housing Units Sold',      cat: 'Housing'  },
  ];

  // ── EDGAR ─────────────────────────────────────────────────

  async function fetchEdgarFacts(ticker) {
    const cik = TICKER_CIK[ticker.toUpperCase()];
    if (!cik) throw new Error("Ticker not in CIK map: " + ticker);
    const res = await fetch(`https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`, {
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) throw new Error(`EDGAR ${res.status}: ${res.statusText}`);
    return res.json();
  }

  // Fetch EDGAR for multiple tickers; returns { ticker: facts } map
  async function fetchEdgarMultiple(tickers, onProgress) {
    const results = {};
    for (const ticker of tickers) {
      try {
        onProgress?.(`Fetching EDGAR for ${ticker}…`);
        results[ticker] = await fetchEdgarFacts(ticker);
        await new Promise(r => setTimeout(r, 300)); // gentle rate-limit
      } catch (e) {
        results[ticker] = { error: e.message };
      }
    }
    return results;
  }

  // Annual headline financials (existing behaviour, for header + Stage 1)
  function extractFinancials(facts) {
    const out = {
      entity: facts.entityName || '',
      cik: facts.cik,
      revenue:   pickConceptAnnual(facts, ['Revenues','RevenueFromContractWithCustomerExcludingAssessedTax','SalesRevenueNet'], 'USD'),
      netIncome: pickConceptAnnual(facts, ['NetIncomeLoss'], 'USD'),
      cash:      pickConceptAnnual(facts, ['CashAndCashEquivalentsAtCarryingValue'], 'USD'),
      assets:    pickConceptAnnual(facts, ['Assets'], 'USD'),
      ar:        pickConceptAnnual(facts, ['AccountsReceivableNetCurrent'], 'USD'),
      inventory: pickConceptAnnual(facts, ['InventoryNet'], 'USD'),
      cfo:       pickConceptAnnual(facts, ['NetCashProvidedByUsedInOperatingActivities'], 'USD'),
      capex:     pickConceptAnnual(facts, ['PaymentsToAcquirePropertyPlantAndEquipment'], 'USD'),
      rd:        pickConceptAnnual(facts, ['ResearchAndDevelopmentExpense'], 'USD'),
      sga:       pickConceptAnnual(facts, ['SellingGeneralAndAdministrativeExpense'], 'USD'),
      cogs:      pickConceptAnnual(facts, ['CostOfRevenue','CostOfGoodsAndServicesSold'], 'USD'),
    };
    const ttmRev = out.revenue?.latestAnnual;
    out.grossMarginPct = ttmRev && out.cogs?.latestAnnual
      ? (1 - out.cogs.latestAnnual.val / ttmRev.val) * 100 : null;
    return out;
  }

  // Quarterly KPI series for forecasting — returns arrays of numbers aligned by end-date
  function extractQuarterlyKPIs(facts) {
    if (!facts?.facts?.['us-gaap']) return null;

    function getQtrSeries(names, unit = 'USD') {
      const ns = facts.facts['us-gaap'];
      for (const name of names) {
        const entries = ns[name]?.units?.[unit];
        if (!entries) continue;
        // Quarterly entries: 10-Q (fp=Q1/Q2/Q3) + derived annual quarters from 10-K
        const qtrs = entries
          .filter(x => (x.form === '10-Q' || x.form === '10-K') && x.val > 0 && x.end)
          .sort((a, b) => a.end < b.end ? -1 : 1);
        if (qtrs.length >= 6) {
          // Deduplicate by end-date (keep latest filing per period)
          const seen = new Map();
          for (const q of qtrs) seen.set(q.end, q);
          const deduped = [...seen.values()].sort((a, b) => a.end < b.end ? -1 : 1);
          return deduped.map(q => ({ date: q.end, val: q.val / 1e6 })); // → $M
        }
      }
      return null;
    }

    const revenue    = getQtrSeries(['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet']);
    const cogs       = getQtrSeries(['CostOfRevenue', 'CostOfGoodsAndServicesSold']);
    const netIncome  = getQtrSeries(['NetIncomeLoss']);
    const inventory  = getQtrSeries(['InventoryNet']);
    const ar         = getQtrSeries(['AccountsReceivableNetCurrent']);

    // Derive gross margin %
    let grossMargin = null;
    if (revenue && cogs) {
      const revMap = new Map(revenue.map(r => [r.date, r.val]));
      const cogsMap = new Map(cogs.map(c => [c.date, c.val]));
      const dates = [...revMap.keys()].filter(d => cogsMap.has(d));
      if (dates.length >= 6) {
        grossMargin = dates.map(d => ({
          date: d,
          val: ((revMap.get(d) - cogsMap.get(d)) / revMap.get(d)) * 100,
        }));
      }
    }

    return { revenue, grossMargin, netIncome, inventory, ar };
  }

  // KPI label map for display
  const KPI_LABELS = {
    revenue:     'Revenue ($M)',
    grossMargin: 'Gross Margin (%)',
    netIncome:   'Net Income ($M)',
    inventory:   'Inventory ($M)',
    ar:          'Accounts Receivable ($M)',
  };

  function pickConceptAnnual(facts, names, unit) {
    if (!facts.facts?.['us-gaap']) return null;
    const ns = facts.facts['us-gaap'];
    for (const n of names) {
      if (ns[n]?.units?.[unit]) {
        const series = ns[n].units[unit];
        const annual = series.filter(x => x.form === '10-K' && x.fp === 'FY').sort((a, b) => a.end < b.end ? 1 : -1);
        const latest = annual[0] || series[series.length - 1];
        return {
          concept: n,
          latestAnnual: latest ? { val: latest.val, end: latest.end, accn: latest.accn } : null,
          series: series.slice(-24),
        };
      }
    }
    return null;
  }

  // ── FRED API ──────────────────────────────────────────────

  // Fetch one series; frequency: 'q' (quarterly), 'm' (monthly), 'a' (annual)
  async function fetchFredSeries(apiKey, seriesId, startDate = '2015-01-01', frequency = 'q') {
    if (!apiKey) throw new Error('FRED API key required');
    const url = `https://api.stlouisfed.org/fred/series/observations`
      + `?series_id=${encodeURIComponent(seriesId)}`
      + `&api_key=${encodeURIComponent(apiKey)}`
      + `&observation_start=${startDate}`
      + `&frequency=${frequency}`
      + `&file_type=json`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`FRED ${seriesId}: ${res.status} ${res.statusText}`);
    const json = await res.json();
    if (json.error_message) throw new Error(`FRED: ${json.error_message}`);
    // Parse observations: filter out '.' (missing) values
    const observations = (json.observations || [])
      .filter(o => o.value !== '.')
      .map(o => ({ date: o.date, value: parseFloat(o.value) }));
    return { id: seriesId, observations };
  }

  // Fetch multiple FRED series; returns { seriesId: { observations } }
  async function fetchFredMultiple(apiKey, seriesIds, startDate = '2015-01-01', onProgress) {
    const results = {};
    for (const id of seriesIds) {
      try {
        onProgress?.(`Fetching FRED ${id}…`);
        results[id] = await fetchFredSeries(apiKey, id, startDate);
        await new Promise(r => setTimeout(r, 150)); // gentle rate-limit
      } catch (e) {
        results[id] = { id, error: e.message, observations: [] };
      }
    }
    return results;
  }

  // Align FRED observations to match an EDGAR quarterly date array
  // Returns { seriesId: number[] } where indices match the edgarDates array
  function alignFredToEdgar(fredResults, edgarDates) {
    const aligned = {};
    for (const [id, series] of Object.entries(fredResults)) {
      if (!series.observations?.length) continue;
      // Build a map from quarter-start → value
      const obsMap = new Map(series.observations.map(o => [o.date.slice(0, 7), o.value])); // YYYY-MM
      aligned[id] = edgarDates.map(d => {
        // Match by same quarter: EDGAR end-date → same quarter of FRED
        const endYYYYMM = d.slice(0, 7);
        const endYear = parseInt(d.slice(0, 4));
        const endMonth = parseInt(d.slice(5, 7));
        // Find the FRED observation whose quarter contains this month
        const qMonth = Math.floor((endMonth - 1) / 3) * 3 + 1;
        const qKey = `${endYear}-${String(qMonth).padStart(2, '0')}`;
        return obsMap.get(qKey) ?? obsMap.get(endYYYYMM) ?? null;
      }).map(v => v ?? null);
    }
    // Remove series where fewer than half of values are non-null
    for (const id of Object.keys(aligned)) {
      const valid = aligned[id].filter(v => v !== null).length;
      if (valid < edgarDates.length / 2) delete aligned[id];
    }
    return aligned;
  }

  // Validate a FRED API key with a lightweight metadata call
  async function validateFredKey(apiKey) {
    try {
      const res = await fetch(
        `https://api.stlouisfed.org/fred/series?series_id=IPG3344S&api_key=${encodeURIComponent(apiKey)}&file_type=json`
      );
      const json = await res.json();
      if (json.error_message) return { ok: false, error: json.error_message };
      return { ok: true, seriesTitle: json.seriess?.[0]?.title || 'OK' };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  // ── FRED bundled snapshot (fallback when no API key) ──────

  let fredCache = null;
  async function loadFred() {
    if (fredCache) return fredCache;
    const res = await fetch('data/fred_data.json');
    if (!res.ok) throw new Error('FRED snapshot missing');
    fredCache = await res.json();
    return fredCache;
  }

  function corr(xs, ys) {
    const n = Math.min(xs.length, ys.length);
    if (n < 4) return null;
    const sx = xs.slice(-n), sy = ys.slice(-n);
    const mx = sx.reduce((a, b) => a + b, 0) / n;
    const my = sy.reduce((a, b) => a + b, 0) / n;
    let num = 0, dx = 0, dy = 0;
    for (let i = 0; i < n; i++) {
      const a = sx[i] - mx, b = sy[i] - my;
      num += a * b; dx += a * a; dy += b * b;
    }
    const d = Math.sqrt(dx * dy);
    return d === 0 ? null : num / d;
  }

  return {
    TICKER_CIK, FRED_SERIES_OPTIONS,
    fetchEdgarFacts, fetchEdgarMultiple,
    extractFinancials, extractQuarterlyKPIs, KPI_LABELS,
    fetchFredSeries, fetchFredMultiple, alignFredToEdgar, validateFredKey,
    loadFred, corr,
  };
})();
