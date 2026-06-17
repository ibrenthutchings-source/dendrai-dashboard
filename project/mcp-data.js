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

  // ── Public API ──────────────────────────────────────────────────────────────

  return {
    checkHealth,
    fetchFullAnalysis,
    mergeRiskScores,
    mapRssSignals,
    mapFredSignals,
  };
})();
