/* ============================================================
   Dendrai Risk Loop — Python MCP server client
   Calls the FastAPI bridge (api_server.py) via /api/mcp/ proxy.

   Start the bridge before running the Vite dev server:
     python project/agentic-tools/api_server.py

   Exposes window.MCP with:
     checkHealth()              — confirm bridge is reachable
     fetchFullAnalysis()        — all 10 analytics models in one call
     mergeRiskScores()          — overlay MCP scores onto template risks
     mapRssSignals()            — convert MCP RSS feed results → Loop signals
     mapFredSignals()           — convert FRED correlations → Loop signals
   ============================================================ */

window.MCP = (function () {
  'use strict';

  const BASE = '/api/mcp';
  const TIMEOUT_MS = 120_000; // 2 min — full analysis can take 30-90s

  async function _post(path, body) {
    const res = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(`MCP ${path}: ${res.status} — ${detail}`);
    }
    return res.json();
  }

  async function _get(path) {
    const res = await fetch(BASE + path, { signal: AbortSignal.timeout(5000) });
    return res.json();
  }

  // ── Health ──────────────────────────────────────────────────────────────────

  async function checkHealth() {
    try {
      const r = await _get('/health');
      return r.status === 'ok';
    } catch {
      return false;
    }
  }

  // ── Full analysis ───────────────────────────────────────────────────────────

  /**
   * Run all 10 predictive analytics models via the Python MCP server.
   * @param {string} ticker  NYSE/NASDAQ ticker
   * @param {object} opts
   *   industry, fredApiKey, forecastHorizon, forecastMetric,
   *   includeRss (bool), includeFred (bool)
   * @returns {Promise<object>} Full analysis result
   */
  async function fetchFullAnalysis(ticker, opts = {}) {
    return _post('/predictive/full-analysis', {
      ticker,
      industry:         opts.industry        || '',
      fred_api_key:     opts.fredApiKey      || '',
      forecast_horizon: opts.forecastHorizon || 4,
      forecast_metric:  opts.forecastMetric  || 'Revenue',
      include_rss:      opts.includeRss      !== false,
      include_fred:     opts.includeFred     !== false,
    });
  }

  // ── Risk score mapping ──────────────────────────────────────────────────────

  // "Red" | "Amber" | "Green"  →  "R" | "A" | "G"
  function _ragChar(ragStatus) {
    if (ragStatus === 'Red')   return 'R';
    if (ragStatus === 'Amber') return 'A';
    return 'G';
  }

  // Build a 6-point history arc from base → score
  function _mkHist(base, score) {
    return Array.from({ length: 6 }, (_, k) => {
      const frac = k / 5;
      return +Math.max(1, Math.min(10, base + (score - base) * frac)).toFixed(1);
    });
  }

  /**
   * Merge MCP-computed risk scores onto template risks from RISK_ENGINE.
   * Template risks supply narrative fields (obj, controls, mapFinding, …);
   * MCP supplies data-driven scores, RAG, velocity, and control environment.
   *
   * Matching is done by index first, then by category similarity as fallback.
   *
   * @param {object[]} templateRisks   Risks from RISK_ENGINE.buildProfile()
   * @param {object}   mcpRiskScores   risk_scores section of full analysis result
   * @returns {object[]} Merged risk array
   */
  function mergeRiskScores(templateRisks, mcpRiskScores) {
    const mcpList = mcpRiskScores?.risks || [];
    if (!mcpList.length) return templateRisks;

    return templateRisks.map((tr, i) => {
      const byIndex = mcpList[i];
      const byCategory = mcpList.find(mr =>
        mr.category && tr.category &&
        mr.category.toLowerCase() === tr.category.toLowerCase()
      );
      const mr = byIndex || byCategory;
      if (!mr) return tr;

      const score = mr.score    ?? tr.score;
      const base  = mr.base_score ?? tr.base;
      return {
        ...tr,
        score,
        base,
        rag:           _ragChar(mr.rag_status) || tr.rag,
        velocity:      mr.velocity  ?? tr.velocity,
        ce:            mr.control_env || tr.ce,
        peerBenchmark: mr.peer_benchmark ?? tr.peerBenchmark,
        hist:          _mkHist(base, score),
      };
    });
  }

  // ── Signal mapping ──────────────────────────────────────────────────────────

  // Domain vocabulary → risk category fragments used to find affected risks
  const _DOMAIN_CATS = {
    'Financial Reporting': ['Financial', 'Revenue', 'Reporting'],
    'Cybersecurity':       ['Cybersecurity', 'IT'],
    'Trade Compliance':    ['Compliance', 'Trade', 'Legal'],
    'Supply Chain':        ['Supply', 'Operations'],
    'Macro':               ['Macro', 'Operational'],
    'Regulatory':          ['Compliance', 'Legal', 'ESG'],
    'Environmental':       ['ESG'],
    'Competitive':         ['Operational'],
  };

  function _domainsToRiskIds(domains, risks) {
    const affected = [];
    for (const domain of (domains || [])) {
      const fragments = _DOMAIN_CATS[domain] || [];
      for (const r of risks) {
        const cat = r.category || '';
        if (fragments.some(f => cat.includes(f)) && !affected.includes(r.id)) {
          affected.push(r.id);
        }
      }
    }
    return affected.slice(0, 3);
  }

  function _velocityToDelta(v) {
    if (v >= 2)  return 'negative';
    if (v <= -1) return 'positive';
    return 'neutral';
  }

  /**
   * Convert MCP rss_signals section into Risk Loop signal objects.
   *
   * @param {object}   mcpResult      Full analysis result
   * @param {object[]} templateRisks  Template risks (with ids) for affectedRisks mapping
   * @returns {object[]} Signal array
   */
  function mapRssSignals(mcpResult, templateRisks = []) {
    const feedResults = mcpResult?.rss_signals?.feed_results || [];
    const signals = [];
    let idx = 0;

    for (const feed of feedResults) {
      if (!Array.isArray(feed.signals)) continue;
      const feedDomains = feed.domains || [];

      for (const sig of feed.signals) {
        idx++;
        const v = sig.velocity ?? 0;
        const allDomains = [...feedDomains, ...(sig.domains_triggered || [])];
        signals.push({
          id:           `MCP-RSS-${String(idx).padStart(3, '0')}`,
          src:          'Industry RSS',
          title:        sig.title || '',
          snippet:      sig.title || '',
          velocity:     v,
          delta:        _velocityToDelta(v),
          rag:          _ragChar(sig.rag_status || 'Green'),
          affectedRisks: _domainsToRiskIds(allDomains, templateRisks),
          published:    sig.date || '',
        });
      }
    }
    return signals;
  }

  /**
   * Convert MCP macro_leading_indicators section into FRED-type signals.
   *
   * @param {object} mcpResult  Full analysis result
   * @returns {object[]} Signal array
   */
  function mapFredSignals(mcpResult) {
    const indicators = mcpResult?.macro_leading_indicators?.indicators || [];
    return indicators.map((ind, i) => {
      const r = ind.pearson_r ?? 0;
      return {
        id:      `MCP-FRED-${String(i + 1).padStart(3, '0')}`,
        src:     'FRED Macro',
        title:   ind.name || ind.series_id || '',
        snippet: `${ind.name} · ${ind.optimal_lag_quarters}Q lead · r=${r.toFixed(2)}`,
        velocity: Math.abs(r) > 0.9 ? 3 : 2,
        delta:   r < 0 ? 'contractionary' : 'expansionary',
        affectedRisks: [],
      };
    });
  }

  // ── New data fetchers ───────────────────────────────────────────────────────

  async function fetchRiskFactors(ticker) {
    return _post('/edgar/risk-factors', { ticker, max_filings: 2 });
  }

  async function fetch8kEvents(ticker) {
    return _post('/edgar/8k-events', { ticker });
  }

  async function fetchProxyData(ticker) {
    return _post('/edgar/proxy', { ticker, max_filings: 2 });
  }

  async function fetchPeerBenchmarks(ticker) {
    return _post('/edgar/peers', { ticker });
  }

  // ── Item 1A enrichment ──────────────────────────────────────────────────────

  // Category keywords used to match filing paragraphs to risk register entries.
  const _RISK_KW = {
    'Financial Reporting': ['revenue recognition','accrual','restatement','gaap','icfr','material weakness','audit','financial statement'],
    'Supply Chain':        ['supply chain','supplier','component','inventory','procurement','lead time','single source'],
    'Cybersecurity':       ['cyber','information security','data breach','ransomware','it system','unauthorized access'],
    'Trade Compliance':    ['export control','tariff','sanction','trade restriction','itar','ear','embargo'],
    'Macro':               ['macro','interest rate','inflation','recession','economic','gdp','monetary'],
    'Operational':         ['operational','execution','talent','retention','key personnel','workforce'],
    'Regulatory':          ['regulatory','compliance','enforcement','epa','fda','sec','legal','litigation'],
    'ESG':                 ['esg','climate','emission','sustainability','environmental'],
    'R&D':                 ['research','r&d','technology','innovation','sic','silicon carbide','product development'],
    'Revenue':             ['revenue','customer concentration','end market','automotive','industrial','pricing'],
    'Gross Margin':        ['gross margin','margin','pricing pressure','average selling price','asp','competition'],
    'CapEx':               ['capital expenditure','capex','capacity','fab','manufacturing','facility'],
  };

  /**
   * Match Item 1A filing paragraphs to template risks and attach the most
   * relevant snippet as `filingSnippet` on each risk.
   */
  function enrichRisksFromFactors(risks, factorsResult) {
    const filings = factorsResult?.filings || [];
    if (!filings.length) return risks;

    // Collect all paragraphs across filings (newest first)
    const paragraphs = [];
    for (const f of filings) {
      const text = f.risk_factors || '';
      const paras = text.split(/\n{2,}/).filter(p => p.trim().length > 80);
      for (const p of paras) {
        paragraphs.push({ text: p.trim(), date: f.filing_date });
      }
    }

    return risks.map(r => {
      // Build keyword list from risk category + name
      const cat = (r.category || '').toLowerCase();
      const name = (r.name || '').toLowerCase();
      const kwSets = Object.entries(_RISK_KW).filter(([k]) => {
        const kl = k.toLowerCase();
        return cat.includes(kl) || name.includes(kl) ||
               kl.split(/\s+/).some(w => cat.includes(w) || name.includes(w));
      });
      const kws = kwSets.flatMap(([, v]) => v);
      if (!kws.length) return r;

      // Score each paragraph by keyword hit density
      let best = null, bestScore = 0;
      for (const para of paragraphs) {
        const lc = para.text.toLowerCase();
        const hits = kws.filter(k => lc.includes(k)).length;
        if (hits > bestScore) { bestScore = hits; best = para; }
      }

      if (!best || bestScore === 0) return r;
      const snippet = best.text.slice(0, 300) + (best.text.length > 300 ? '…' : '');
      return { ...r, filingSnippet: snippet, filingDate: best.date };
    });
  }

  // ── 8-K → CEM event mapping ─────────────────────────────────────────────────

  const _8K_ITEM_MAP = {
    '1.05': { sev: 'P1', cat: 'Cybersecurity',       area: 'IT / Security',        risk: 'Unauthorized access / data breach',       ctrl: 'Material Cybersecurity Incident (8-K 1.05)' },
    '4.02': { sev: 'P1', cat: 'Financial Reporting', area: 'Financial Reporting',  risk: 'Material misstatement / restatement',     ctrl: 'Non-Reliance on Financial Statements (8-K 4.02)' },
    '2.06': { sev: 'P1', cat: 'Financial Reporting', area: 'Accounting',           risk: 'Asset impairment / write-down',           ctrl: 'Material Impairment (8-K 2.06)' },
    '3.01': { sev: 'P1', cat: 'Financial Reporting', area: 'Capital Markets',      risk: 'Delisting / listing failure',             ctrl: 'Notice of Delisting (8-K 3.01)' },
    '2.05': { sev: 'P2', cat: 'Operations',          area: 'Operations',           risk: 'Restructuring / cost escalation',         ctrl: 'Exit or Disposal Activity (8-K 2.05)' },
    '5.02': { sev: 'P2', cat: 'Operations',          area: 'Executive Management', risk: 'Key personnel / succession risk',         ctrl: 'Director / Officer Departure (8-K 5.02)' },
    '2.01': { sev: 'P2', cat: 'Operations',          area: 'Strategy',             risk: 'M&A integration / concentration risk',    ctrl: 'Acquisition or Disposition (8-K 2.01)' },
    '2.03': { sev: 'P2', cat: 'Financial Reporting', area: 'Treasury',             risk: 'Liquidity / covenant risk',               ctrl: 'New Financial Obligation (8-K 2.03)' },
    '1.01': { sev: 'P3', cat: 'Regulatory',          area: 'Legal',                risk: 'Contractual / commitment risk',           ctrl: 'Material Agreement (8-K 1.01)' },
    '7.01': { sev: 'P3', cat: 'Regulatory',          area: 'Regulatory',           risk: 'Reg FD / disclosure risk',                ctrl: 'Regulation FD Disclosure (8-K 7.01)' },
  };

  const _8K_RC = {
    '1.05': 'SEC-mandated disclosure of material cybersecurity incident. Immediate containment, forensic investigation, and regulatory notification required.',
    '4.02': 'Non-reliance determination requires restatement process, audit committee oversight, and SEC disclosure. Review ICFR and management override controls.',
    '2.06': 'Material impairment signals asset quality deterioration. Validate valuation assumptions and reassess carrying values for remaining assets.',
    '2.05': 'Restructuring cost recognition. Validate restructuring estimates, liabilities, and disclosure adequacy. Monitor for GAAP adherence.',
    '5.02': 'Key executive departure. Assess succession planning adequacy, knowledge transfer risk, and impact on control environment.',
    '2.01': 'Material acquisition or disposal. Assess integration risk, purchase price allocation, and valuation methodology.',
    '2.03': 'New material financial obligation. Assess covenant compliance, liquidity impact, and disclosure completeness.',
    '3.01': 'Listing deficiency notice. Assess going-concern implications and capital structure alternatives.',
    '1.01': 'Material agreement executed. Review contractual obligations, commitments, and off-balance-sheet exposures.',
    '7.01': 'Regulation FD disclosure. Validate selective disclosure controls and investor relations procedures.',
  };

  /**
   * Convert an `/edgar/8k-events` result into CEM event objects ready for setEvents().
   */
  function map8kToCemEvents(eightKResult) {
    const events = eightKResult?.events || [];
    const cemEvents = [];

    for (const ev of events) {
      const items = Object.keys(ev.item_descriptions || {});
      if (!items.length) continue;

      // Prioritise the highest-severity item on the filing
      let best = null;
      for (const item of items) {
        const map = _8K_ITEM_MAP[item];
        if (!map) continue;
        if (!best || map.sev < best.sev) best = { item, ...map };
      }
      if (!best) continue;

      const desc = ev.item_descriptions?.[best.item] || best.ctrl;
      const exposure = best.sev === 'P1' ? 'Material — disclose to board' : 'Significant — management action required';

      cemEvents.push({
        id:       `8K-${ev.accession_number?.replace(/[^A-Z0-9]/gi, '') || Date.now().toString(36).toUpperCase()}`,
        ts:       new Date(ev.date + 'T12:00:00Z').getTime(),
        source:   '8-K Filing',
        control:  best.ctrl,
        area:     best.area,
        category: best.cat,
        risk:     best.risk,
        severity: best.sev,
        exposure,
        rc:       `${_8K_RC[best.item] || desc} Filing date: ${ev.date}.`,
        rcLoading: false,
        notifs:   [],
        filingDate: ev.date,
        items:    items,
      });
    }

    // Newest events first, cap at 20 to avoid flooding CEM
    return cemEvents.sort((a, b) => b.ts - a.ts).slice(0, 20);
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  return {
    checkHealth,
    fetchFullAnalysis,
    mergeRiskScores,
    mapRssSignals,
    mapFredSignals,
    fetchRiskFactors,
    fetch8kEvents,
    fetchProxyData,
    fetchPeerBenchmarks,
    enrichRisksFromFactors,
    map8kToCemEvents,
  };
})();
