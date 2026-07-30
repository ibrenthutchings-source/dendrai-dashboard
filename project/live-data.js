/* ============================================================
   Dendrai Intelligenza — live data helpers
   - EDGAR: direct fetch from data.sec.gov (CORS-friendly)
   - FRED: bundled JSON snapshot (Q1 2021 → Q1 2026)
   ============================================================ */

window.LIVE = (function () {

  // ---- Common tickers → CIK (zero-padded to 10 digits) ----
  // Add more as needed; ticker→CIK comes from SEC EDGAR's
  // company_tickers.json. We hardcode a broad analog/semi peer set
  // so live mode works even if the SEC dynamic lookup is rate-limited.
  const TICKER_CIK = {
    // Analog / Mixed-Signal
    ON:    "0001097864",   // onsemi
    TXN:   "0000097476",   // Texas Instruments
    STM:   "0001114448",   // STMicroelectronics
    MCHP:  "0000827054",   // Microchip Technology
    NXPI:  "0001413447",   // NXP Semiconductors
    ADI:   "0000006951",   // Analog Devices
    SWKS:  "0000004127",   // Skyworks Solutions
    QRVO:  "0001604778",   // Qorvo
    MPWR:  "0001280452",   // Monolithic Power Systems
    WOLF:  "0000895419",   // Wolfspeed
    // Digital
    AVGO:  "0001730168",   // Broadcom
    NVDA:  "0001045810",   // NVIDIA
    INTC:  "0000050863",   // Intel
    AMD:   "0000002488",   // AMD
    QCOM:  "0000804328",   // Qualcomm
    MRVL:  "0001058057",   // Marvell Technology
    // Semiconductor Equipment / Packaging
    AMAT:  "0000003153",   // Applied Materials
    KLAC:  "0000319201",   // KLA Corporation
    LRCX:  "0000707549",   // Lam Research
    ASML:  "0000937966",   // ASML Holding
    AMKR:  "0001047127",   // AMKOR Technology
    ONTO:  "0000074260",   // Onto Innovation
    TER:   "0000097216",   // Teradyne
    ENTG:  "0001101302",   // Entegris
    // Memory
    MU:    "0000723125",   // Micron Technology
    WDC:   "0000106040",   // Western Digital
    // Automotive
    F:     "0000037996",   // Ford Motor Company
  };

  async function fetchEdgarFacts(ticker) {
    let cik = TICKER_CIK[ticker.toUpperCase()];

    // Dynamic CIK lookup for unknown tickers
    if (!cik) {
      try {
        const tcRes = await fetch("/api/sec/files/company_tickers.json", {
          signal: AbortSignal.timeout(8000),
        });
        if (tcRes.ok) {
          const tc = await tcRes.json();
          const entry = Object.values(tc).find(e => e.ticker?.toUpperCase() === ticker.toUpperCase());
          if (entry) cik = String(entry.cik_str).padStart(10, "0");
        }
      } catch {}
      if (!cik) throw new Error(`Ticker not found in CIK map or SEC lookup: ${ticker}`);
    }

    const url = `/api/edgar/api/xbrl/companyfacts/CIK${cik}.json`;

    async function attempt() {
      const res = await fetch(url, {
        headers: { "Accept": "application/json" },
        signal: AbortSignal.timeout(20000),
      });
      if (res.status === 403) throw new Error("SEC EDGAR returned 403 — rate limited. Wait 30s and retry.");
      if (res.status === 429) throw new Error("SEC EDGAR rate limit (429). Wait 60s and retry.");
      if (!res.ok) throw new Error(`EDGAR ${res.status}: ${res.statusText}`);
      return res.json();
    }

    try {
      return await attempt();
    } catch (e) {
      if (e.name === "TimeoutError" || e.name === "AbortError") {
        // One retry after 2s
        await new Promise(r => setTimeout(r, 2000));
        return await attempt();
      }
      throw e;
    }
  }

  async function lookupTickerAndCik(value) {
    const raw = (value || "").trim();
    if (!raw) return null;

    const key = raw.toUpperCase();
    if (TICKER_CIK[key]) {
      return { ticker: key, cik: TICKER_CIK[key] };
    }

    try {
      const res = await fetch("/api/sec/files/company_tickers.json", {
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) return null;
      const list = await res.json();
      const exactTicker = Object.values(list).find((entry) => entry.ticker?.toUpperCase() === key);
      if (exactTicker) {
        return { ticker: exactTicker.ticker.toUpperCase(), cik: String(exactTicker.cik_str).padStart(10, "0") };
      }

      const exactName = Object.values(list).find((entry) => entry.title?.toUpperCase() === key);
      if (exactName) {
        return { ticker: exactName.ticker.toUpperCase(), cik: String(exactName.cik_str).padStart(10, "0") };
      }

      const fuzzy = Object.values(list).find((entry) => {
        const title = entry.title?.toUpperCase() || "";
        return title.includes(key) || key.includes((entry.ticker || "").toUpperCase());
      });
      if (fuzzy) {
        return { ticker: fuzzy.ticker.toUpperCase(), cik: String(fuzzy.cik_str).padStart(10, "0") };
      }
    } catch (e) {
      // ignore SEC lookup failures here; fallback will happen elsewhere
    }

    return null;
  }

  async function fetchEdgarProfile(value) {
    const lookup = await lookupTickerAndCik(value);
    if (!lookup) throw new Error(`Unable to resolve ticker or company for '${value}'`);

    const profileUrl = `/api/edgar/submissions/CIK${lookup.cik}.json`;
    const res = await fetch(profileUrl, {
      headers: { "Accept": "application/json" },
      signal: AbortSignal.timeout(20000),
    });
    if (res.status === 403) throw new Error("SEC EDGAR returned 403 — rate limited. Wait 30s and retry.");
    if (!res.ok) throw new Error(`SEC profile ${res.status}: ${res.statusText}`);
    const profile = await res.json();

    return {
      ticker: lookup.ticker,
      cik: lookup.cik,
      name: profile.name || profile.entityName || "",
      sic: profile.sic || null,
      sicDescription: profile.sicDescription || null,
      industry: mapSicToIndustry(profile.sic, profile.sicDescription),
    };
  }

  function mapSicToIndustry(sic, sicDescription) {
    const code = parseInt(sic, 10);
    if (!Number.isFinite(code)) {
      if (sicDescription?.toLowerCase().includes("retail")) return "Retail";
      if (sicDescription?.toLowerCase().includes("utility")) return "Energy / Utilities";
      return null;
    }
    if (code >= 5200 && code < 6000) return "Retail";
    if (code >= 1500 && code < 1800) return "Energy / Utilities";
    if (code >= 2000 && code < 4000) return "Industrial / Manufacturing";
    if (code >= 6000 && code < 7000) return "Industrial / Manufacturing";
    if (sicDescription?.toLowerCase().includes("semiconductor")) return "Digital Semiconductors";
    return sicDescription || null;
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
      cogs: pickConcept(facts, ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"], "USD"),
      grossProfit: pickConcept(facts, ["GrossProfit"], "USD"),
      currentAssets: pickConcept(facts, ["AssetsCurrent"], "USD"),
      currentLiabilities: pickConcept(facts, ["LiabilitiesCurrent"], "USD"),
      liabilities: pickConcept(facts, ["Liabilities"], "USD"),
      stockholdersEquity: pickConcept(facts, ["StockholdersEquity", "StockholdersEquityAttributableToParent"], "USD"),
      retainedEarnings: pickConcept(facts, ["RetainedEarningsAccumulatedDeficit"], "USD"),
      operatingIncome: pickConcept(facts, ["OperatingIncomeLoss"], "USD"),
    };
    // Derive headline ratios from latest annual fact for each
    const ttmRev = out.revenue && out.revenue.latestAnnual;
    const grossMargin =
      ttmRev && out.cogs?.latestAnnual
        ? (1 - out.cogs.latestAnnual.val / ttmRev.val) * 100
        : ttmRev && out.grossProfit?.latestAnnual
        ? (out.grossProfit.latestAnnual.val / ttmRev.val) * 100
        : null;
    out.grossMarginPct = grossMargin;
    return out;
  }

  function pickConcept(facts, names, unit) {
    if (!facts.facts || !facts.facts["us-gaap"]) return null;
    const ns = facts.facts["us-gaap"];
    // Filers often carry multiple tag aliases for the same line item (e.g. the
    // legacy "Revenues" tag alongside "RevenueFromContractWithCustomer..." after
    // adopting ASC 606) — the old tag's last data point can be years stale even
    // though it still technically "exists" in companyfacts. Evaluate every alias
    // and keep whichever one's latest point is most recent, rather than taking
    // the first alias found — otherwise a stale tag silently wins just because
    // it's earlier in the alias list.
    let best = null;
    for (const n of names) {
      if (!(ns[n] && ns[n].units && ns[n].units[unit])) continue;
      const series = ns[n].units[unit];
      const annual = series
        .filter((x) => x.form === "10-K" && x.fp === "FY")
        .sort((a, b) => (a.end < b.end ? 1 : -1));
      const latest = annual[0] || series[series.length - 1];
      if (!latest) continue;
      if (!best || latest.end > best.latest.end) {
        best = { concept: n, latest, series };
      }
    }
    if (!best) return null;
    return {
      concept: best.concept,
      latestAnnual: { val: best.latest.val, end: best.latest.end, accn: best.latest.accn },
      series: best.series.slice(-24),
    };
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

  return { TICKER_CIK, fetchEdgarFacts, fetchEdgarProfile, extractFinancials, loadFred, corr };
})();
