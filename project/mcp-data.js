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
  const AI_TIMEOUT_MS = 300_000; // 5 min — adaptive-thinking agent runs can be long

  async function _post(path, body, timeoutMs = TIMEOUT_MS) {
    const res = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(`MCP ${path}: ${res.status} — ${detail}`);
    }
    return res.json();
  }

  function _postAi(path, body) { return _post(path, body, AI_TIMEOUT_MS); }

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
      use_db:           opts.useDb           || false,
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
      return +Math.max(1, Math.min(25, base + (score - base) * frac)).toFixed(1);
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

      // MCP server returns scores on 0-10 scale; scale to 0-25
      const score = mr.score    != null ? +(mr.score * 2.5).toFixed(1) : tr.score;
      const base  = mr.base_score != null ? +(mr.base_score * 2.5).toFixed(1) : tr.base;
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

  function _domainsToRiskIds(domains, risks) {
    // Use the backend-loaded DOMAIN_RISK_CATS from RSS_ENGINE (single source of truth).
    const mapping = window.RSS_ENGINE?.DOMAIN_RISK_CATS || {};
    const affected = [];
    for (const domain of (domains || [])) {
      const fragments = mapping[domain] || [];
      for (const r of risks) {
        const searchable = ((r.category || '') + ' ' + (r.name || '')).toLowerCase();
        if (fragments.some(f => searchable.includes(f)) && !affected.includes(r.id)) {
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
          label:        sig.title || '',
          feedName:     sig.feed_name || sig.feedName || feed.feed || 'RSS',
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
      const title = ind.name || ind.series_id || '';
      return {
        id:      `MCP-FRED-${String(i + 1).padStart(3, '0')}`,
        src:     'FRED Macro',
        title,
        label:   title,
        snippet: `${title} · ${ind.optimal_lag_quarters}Q lead · r=${r.toFixed(2)}`,
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

  // ── Governance Intelligence — load from DB without a live pipeline run ──────
  // Both return null on 404 (nothing saved yet for this ticker) instead of throwing,
  // so callers can treat "no saved data" as a normal, non-error case.

  async function _getSavedOrNull(path) {
    const res = await fetch(BASE + path, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (res.status === 404) return null;
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(`MCP ${path}: ${res.status} — ${detail}`);
    }
    return res.json();
  }

  async function fetchSavedProxyData(ticker) {
    return _getSavedOrNull(`/edgar/proxy/${encodeURIComponent(ticker)}`);
  }

  async function fetchSavedPeerBenchmarks(ticker) {
    return _getSavedOrNull(`/edgar/peers/${encodeURIComponent(ticker)}`);
  }

  async function fetchSavedAuditScope(ticker) {
    return _getSavedOrNull(`/audit-scope/${encodeURIComponent(ticker)}`);
  }

  // ── Approval workflow (real 2-stage preparer -> manager HITL review) ────────
  // Session-cookie authenticated (proxied at /approvals/, not /api/mcp/) —
  // identity is always resolved server-side from the login session, never
  // sent by the client. See approvals_endpoints.py.

  async function prepareApprovalTask({ runId, gateType, itemRef, itemLabel, disposition, adjustments, rationale, aiSuggested }) {
    const res = await fetch('/approvals/prepare', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        run_id: runId, gate_type: gateType, item_ref: itemRef, item_label: itemLabel,
        disposition, adjustments, rationale, ai_suggested: aiSuggested || null,
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // { saved, task }
  }

  // ── Item 1A enrichment ──────────────────────────────────────────────────────

  // Category keywords used to match filing paragraphs to risk register entries.
  // Loaded from /api/mcp/scoring/config on startup; hardcoded object is the fallback.
  const _RISK_KW_DEFAULT = {
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
  let _RISK_KW = null;
  fetch('/api/mcp/scoring/config', { signal: AbortSignal.timeout(5000) })
    .then(r => r.ok ? r.json() : null)
    .then(cfg => { if (cfg?.risk_kw) _RISK_KW = cfg.risk_kw; })
    .catch(() => {});
  function _getRiskKw() { return _RISK_KW || _RISK_KW_DEFAULT; }

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
      const kwSets = Object.entries(_getRiskKw()).filter(([k]) => {
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

  // ── AI-augmented endpoints (recommendations #1–#4) ───────────────────────────
  // All return 503 if ANTHROPIC_API_KEY is not set on the bridge; callers should
  // treat a thrown error as "AI unavailable" and fall back to the manual flow.

  /** True when the bridge has a configured language model. */
  async function aiEnabled() {
    try { return !!(await _get('/health')).ai_enabled; } catch { return false; }
  }

  /** #2 — Per-risk HITL Gate 1 dispositions. Returns { recommendations: [...] }. */
  function aiGate1Recommend(ticker, risks, context = {}, runId = null) {
    return _postAi('/ai/gate1/recommend', { ticker, run_id: runId, risks, context });
  }

  /** #2 — Per-objective HITL Gate 2 scope drafts. Returns { recommendations: [...] }. */
  function aiGate2Recommend(ticker, objectives, risks, runId = null) {
    return _postAi('/ai/gate2/recommend', { ticker, run_id: runId, objectives, risks });
  }

  /** #2b — AI-drafted approve/reject recommendation for a manager reviewing a
   *  submitted Approval Inbox item. Returns { recommendation, confidence, reasoning }. */
  function aiApprovalRecommend(taskId) {
    return _postAi('/ai/approval/recommend', { task_id: taskId });
  }

  /** #3 — Item 1A / proxy narrative analysis. Returns emerging_risks, yoy_changes, summary. */
  function aiNarrative(ticker, runId = null, opts = {}) {
    return _postAi('/ai/narrative-analysis', {
      ticker, run_id: runId,
      max_filings: opts.maxFilings || 1,
      include_proxy: opts.includeProxy !== false,
    });
  }

  /** #4 — Role-tailored persona brief (CAE / CFO / COO). */
  function aiPersonaBrief(ticker, persona, risks, loopStats = {}, runId = null) {
    return _postAi('/ai/persona-brief', { ticker, run_id: runId, persona, risks, loop_stats: loopStats });
  }

  /** #4 — Full markdown audit report. Returns { markdown }. */
  function aiAuditReport(ticker, { risks = [], objectives = [], maps = [], loop = {} } = {}, runId = null) {
    return _postAi('/ai/audit-report', { ticker, run_id: runId, risks, objectives, maps, loop });
  }

  /** #1 — Tool-use investigation agent. Returns { final_text, tool_calls, iterations }. */
  function agentInvestigate(ticker, focus = '', runId = null) {
    return _postAi('/agent/investigate', { ticker, run_id: runId, focus });
  }

  /**
   * #1b — Streaming investigation agent via SSE.
   * Calls onEvent(event) for each SSE event:
   *   { type: "tool_call",   tool, input, iteration }
   *   { type: "tool_result", tool, result_preview, is_error, iteration }
   *   { type: "done",        final_text, iterations, stopped }
   *   { type: "error",       message }
   * Returns a Promise that resolves to the final { final_text, tool_calls, iterations } object.
   */
  async function agentInvestigateStream(ticker, focus = '', runId = null, onEvent = () => {}) {
    const res = await fetch(BASE + '/agent/investigate/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, run_id: runId, focus }),
      signal: AbortSignal.timeout(AI_TIMEOUT_MS),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(`MCP /agent/investigate/stream: ${res.status} — ${detail}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    const toolCalls = [];
    let finalEvent = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop(); // keep incomplete line
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const event = JSON.parse(line.slice(6));
          onEvent(event);
          if (event.type === 'tool_call') toolCalls.push({ tool: event.tool, input: event.input });
          if (event.type === 'done') finalEvent = event;
        } catch {}
      }
    }
    if (!finalEvent) throw new Error('Stream ended without a done event');
    return { final_text: finalEvent.final_text, tool_calls: toolCalls,
             iterations: finalEvent.iterations, stopped: finalEvent.stopped };
  }

  /** Read back persisted AI analyses for a run. */
  function fetchAiAnalyses(runId, kind = '') {
    return _get(`/history/runs/${runId}/ai-analyses${kind ? `?kind=${encodeURIComponent(kind)}` : ''}`);
  }

  /** Aggregate token usage and estimated cost for all AI calls in a run. */
  function fetchRunTokenCost(runId) {
    return _get(`/history/runs/${runId}/token-cost`);
  }

  /** #4b — Loop calibration AI assist (Gate 3). */
  function aiLoopCalibrate(ticker, { loopStats = {}, risksFinal = [], risksInitial = [], hitlOverrideRate = 0, lessonsLearned = [] } = {}, runId = null) {
    return _postAi('/ai/loop-calibrate', {
      ticker, run_id: runId,
      loop_stats: loopStats,
      risks_final: risksFinal,
      risks_initial: risksInitial,
      hitl_override_rate: hitlOverrideRate,
      lessons_learned: lessonsLearned,
    });
  }

  /** Provision (or reuse) a Managed Agent + scheduled Deployment for a ticker. */
  function agentScheduleProvision(ticker, cron = '0 8 * * 1', mcpUrl = '') {
    return _postAi('/agent/schedule', { ticker, cron, mcp_url: mcpUrl });
  }

  /** Trigger an immediate run of the existing scheduled deployment. */
  function agentScheduleRunNow(ticker) {
    return _postAi('/agent/schedule/run-now', { ticker });
  }

  /** List recent scheduled runs for a ticker's deployment. */
  function agentScheduleStatus(ticker) {
    return _get(`/agent/schedule/status/${encodeURIComponent(ticker)}`);
  }

  // ── Compliance RSS ingest (server-side cached) ───────────────────────────────

  /**
   * Fetch and grade the compliance/regulatory RSS feeds via the MCP server.
   * Results are cached server-side (default 30-min TTL) so this is fast on
   * repeat calls within a session.
   *
   * Returns the same shape as RSS_ENGINE.ingestAll() on the frontend:
   *   { fetched_at, feeds: [{feed, articles, fetchStatus, cached}], total_articles, ... }
   *
   * @param {string[]} feedIds      Feed IDs to ingest (empty = all registered feeds)
   * @param {object}   opts
   *   forceRefresh  bool   — bypass cache (default false)
   *   ttlMinutes    int    — cache TTL in minutes (default 30)
   *   ticker        string — active ticker; enables peer-aware EDGAR feed
   */
  async function ingestRssFeeds(feedIds = [], opts = {}) {
    return _post('/rss/ingest', {
      feed_ids:      feedIds,
      force_refresh: opts.forceRefresh  || false,
      ttl_minutes:   opts.ttlMinutes    || 30,
      ticker:        opts.ticker        || null,
    });
  }

  /**
   * Return per-feed cache health for all registered compliance RSS feeds.
   * Shows last_fetched, article_count, fetch_status for each feed.
   */
  async function fetchRssFeedStatus() {
    return _get('/rss/feeds/status');
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
    fetchSavedProxyData,
    fetchSavedPeerBenchmarks,
    fetchSavedAuditScope,
    prepareApprovalTask,
    enrichRisksFromFactors,
    map8kToCemEvents,
    // AI-augmented (#1–#4b)
    aiEnabled,
    aiGate1Recommend,
    aiGate2Recommend,
    aiApprovalRecommend,
    aiNarrative,
    aiPersonaBrief,
    aiAuditReport,
    aiLoopCalibrate,
    agentInvestigate,
    agentInvestigateStream,
    fetchAiAnalyses,
    fetchRunTokenCost,
    // Scheduled Managed Agent (#2b)
    agentScheduleProvision,
    agentScheduleRunNow,
    agentScheduleStatus,
    // Compliance RSS ingest
    ingestRssFeeds,
    fetchRssFeedStatus,
  };
})();
