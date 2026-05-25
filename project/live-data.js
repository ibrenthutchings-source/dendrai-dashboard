/* ============================================================
   Dendrai Risk Loop — live data helpers
   - EDGAR: direct fetch from data.sec.gov (CORS-friendly)
   - FRED: bundled JSON snapshot (Q1 2021 → Q1 2026)
   ============================================================ */

window.LIVE = (function () {

  // ---- Common tickers → CIK (zero-padded to 10 digits) ----
  // Add more as needed; ticker→CIK comes from SEC EDGAR's
  // company_tickers.json. We hardcode the most-relevant analog/
  // semi peers so the demo works offline-friendly for those.
  const TICKER_CIK = {
    ON:    "0001097864",   // onsemi
    TXN:   "0000097476",   // Texas Instruments
    STM:   "0001114448",   // STMicroelectronics
    MCHP:  "0000827054",   // Microchip Technology
    AVGO:  "0001730168",   // Broadcom
    NVDA:  "0001045810",   // NVIDIA
    INTC:  "0000050863",   // Intel
    AMD:   "0000002488",   // AMD
    QCOM:  "0000804328",   // Qualcomm
    NXPI:  "0001413447",   // NXP Semiconductors
  };

  async function fetchEdgarFacts(ticker) {
    const cik = TICKER_CIK[ticker.toUpperCase()];
    if (!cik) throw new Error("Ticker not in CIK map: " + ticker);
    const url = `https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`;
    const res = await fetch(url, {
      headers: { "Accept": "application/json" },
    });
    if (!res.ok) throw new Error(`EDGAR ${res.status}: ${res.statusText}`);
    return res.json();
  }

  // Extract a usable set of headline financials from companyfacts JSON
  function extractFinancials(facts) {
    const out = {
      entity: facts.entityName || "",
      cik: facts.cik,
      revenue: pickConcept(facts, [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
      ], "USD"),
      netIncome: pickConcept(facts, ["NetIncomeLoss"], "USD"),
      cash: pickConcept(facts, ["CashAndCashEquivalentsAtCarryingValue"], "USD"),
      assets: pickConcept(facts, ["Assets"], "USD"),
      ar: pickConcept(facts, ["AccountsReceivableNetCurrent"], "USD"),
      inventory: pickConcept(facts, ["InventoryNet"], "USD"),
      cfo: pickConcept(facts, ["NetCashProvidedByUsedInOperatingActivities"], "USD"),
      capex: pickConcept(facts, ["PaymentsToAcquirePropertyPlantAndEquipment"], "USD"),
      rd: pickConcept(facts, ["ResearchAndDevelopmentExpense"], "USD"),
      sga: pickConcept(facts, ["SellingGeneralAndAdministrativeExpense"], "USD"),
      cogs: pickConcept(facts, ["CostOfRevenue", "CostOfGoodsAndServicesSold"], "USD"),
    };
    // Derive headline ratios from latest annual fact for each
    const ttmRev = out.revenue && out.revenue.latestAnnual;
    const grossMargin =
      ttmRev && out.cogs && out.cogs.latestAnnual
        ? (1 - out.cogs.latestAnnual.val / ttmRev.val) * 100
        : null;
    out.grossMarginPct = grossMargin;
    return out;
  }

  function pickConcept(facts, names, unit) {
    if (!facts.facts || !facts.facts["us-gaap"]) return null;
    const ns = facts.facts["us-gaap"];
    for (const n of names) {
      if (ns[n] && ns[n].units && ns[n].units[unit]) {
        const series = ns[n].units[unit];
        const annual = series
          .filter((x) => x.form === "10-K" && x.fp === "FY")
          .sort((a, b) => (a.end < b.end ? 1 : -1));
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

  // ---- FRED — bundled snapshot ----
  let fredCache = null;
  async function loadFred() {
    if (fredCache) return fredCache;
    const res = await fetch("data/fred_data.json");
    if (!res.ok) throw new Error("FRED snapshot missing");
    fredCache = await res.json();
    return fredCache;
  }

  // Compute simple correlation between two equal-length arrays
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

  return { TICKER_CIK, fetchEdgarFacts, extractFinancials, loadFred, corr };
})();
