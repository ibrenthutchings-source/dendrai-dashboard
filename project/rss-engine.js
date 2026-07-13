/* ============================================================
   Dendrai Intelligenza — RSS Ingestion + Grading Engine
   Fetch → parse → keyword-relevance grade → velocity delta

   Fetch path:
     Live fetch via /api/rss-proxy (Vite dev-server middleware
     that forwards the request server-side, bypassing CORS).
     If a feed is unreachable the result is marked "failed"
     and no articles are returned — there is no simulation fallback.

   Grading:
     - Relevance  0→1  (keyword density against risk domain vocab)
     - Severity   0→1  (urgency language detection)
     - Velocity   rounded integer, calibrated to [-2, +5]
   ============================================================ */

window.RSS_ENGINE = (function () {

  // ── Feed registry ─────────────────────────────────────────
  const FEEDS = [
    {
      id: "bis",
      name: "BIS Export Controls",
      url: "https://www.bis.doc.gov/index.php/2013-01-17-21-19-53/2013-01-17-21-20-34.xml",
      domains: ["Trade Compliance"],
      risks: ["R-02"],
      weight: 1.5,
      icon: "shield",
    },
    {
      id: "cisa",
      name: "CISA ICS Advisories",
      url: "https://www.cisa.gov/cybersecurity-advisories/ics-advisories.xml",
      domains: ["Cybersecurity"],
      risks: ["R-04"],
      weight: 1.3,
      icon: "wifi",
      companyGated: true,  // Only surface advisories that mention the company or its products
    },
    {
      id: "sec",
      name: "SEC EDGAR Peer Filings",
      // URL is resolved dynamically from /edgar/peers for the active ticker.
      // The fallback URL is used only when no ticker is available.
      url: "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=10-K&dateb=&owner=include&count=8&output=atom",
      type: "edgar-peers",
      domains: ["Financial Reporting"],
      risks: ["R-01", "R-05"],
      weight: 1.0,
      icon: "doc",
    },
    {
      id: "fed",
      name: "Federal Reserve Press",
      url: "https://www.federalreserve.gov/feeds/press_all.xml",
      domains: ["Macro"],
      risks: ["R-09"],
      weight: 0.9,
      icon: "trend",
    },
    {
      id: "epa",
      name: "EPA Climate Enforcement",
      url: "https://www.federalregister.gov/agencies/environmental-protection-agency.rss",
      domains: ["ESG"],
      risks: ["R-07"],
      weight: 0.8,
      icon: "compass",
    },
  ];

  // ── Severity vocabulary ───────────────────────────────────
  const SEVERITY_WORDS = {
    critical:      3.0,
    urgent:        2.5,
    mandatory:     2.0,
    violation:     2.5,
    penalty:       2.5,
    enforcement:   2.5,
    warning:       1.5,
    significant:   1.5,
    material:      1.5,
    expanded:      1.2,
    escalated:     1.5,
    immediate:     2.0,
    ban:           2.5,
    sanction:      2.5,
    restricted:    1.5,
    updated:       0.8,
    revised:       0.7,
    announced:     0.5,
    proposed:      0.6,
    monitoring:    0.6,
    review:        0.5,
  };

  // ── Domain keyword vocabularies ───────────────────────────
  const DOMAIN_VOCAB = {
    "Trade Compliance": [
      "export control", "eccn", "bis", "entity list", "end-user", "license",
      "deemed export", "sanction", "ofac", "itar", "ear", "china", "huawei",
      "advanced node", "chip", "semiconductor export", "tariff", "customs",
      "restricted party", "denied party", "foreign direct product",
    ],
    "Financial Reporting": [
      "revenue recognition", "asc 606", "restatement", "write-down", "write-off",
      "goodwill impairment", "inventory reserve", "channel stuffing", "bill-and-hold",
      "10-k", "10-q", "earnings", "guidance", "sec comment letter", "going concern",
      "internal control", "sox", "material weakness", "restatement", "accounts receivable",
    ],
    "Cybersecurity": [
      "cisa", "vulnerability", "cve", "patch", "zero-day", "ransomware", "breach",
      "ics", "scada", "ot security", "supply chain attack", "privileged access",
      "authentication", "mfa", "endpoint", "threat actor", "apt",
    ],
    "Macro": [
      "pmi", "gdp", "recession", "manufacturing", "semiconductor demand",
      "inventory correction", "destocking", "lead time", "capacity utilization",
      "fed funds", "interest rate", "inflation", "consumer sentiment", "capex",
    ],
    "Operational": [
      "fab", "wafer", "yield", "capacity", "sic", "silicon carbide",
      "auto demand", "ev", "electric vehicle", "production ramp",
      "supply disruption", "shortage", "inventory write-down",
    ],
    "ESG": [
      "climate", "esg", "water stress", "sec disclosure", "scope 3",
      "carbon", "emission", "sustainability", "drought", "physical risk",
      "transition risk", "tcfd", "arizona", "fab water",
    ],
    "Supply": [
      "conflict minerals", "rmap", "dodd-frank", "cobalt", "tantalum",
      "tin", "tungsten", "smelter", "supply chain due diligence",
      "xinjiang", "forced labor",
    ],
    "Legal": [
      "patent", "litigation", "lawsuit", "ip", "intellectual property",
      "injunction", "royalty", "cross-license", "settlement", "verdict",
    ],
  };

  // ── Domain → risk category matching ──────────────────────
  // Maps RSS feed domain labels to substrings searched in risk.category + risk.name.
  // When the live risk register is passed to ingestAll(), this replaces the hardcoded
  // per-feed risk IDs so assignment reflects the actual company's risk template.
  const DOMAIN_RISK_CATS = {
    'Financial Reporting': ['financial', 'revenue', 'reporting', 'accounting'],
    'Cybersecurity':       ['cybersecurity', 'it ', 'technology', 'digital', 'information'],
    'Trade Compliance':    ['compliance', 'trade', 'legal', 'regulatory'],
    'Supply Chain':        ['supply', 'operations', 'procurement', 'sourcing'],
    'Macro':               ['macro', 'economic', 'market', 'operational'],
    'Regulatory':          ['compliance', 'legal', 'regulatory', 'esg'],
    'Environmental':       ['esg', 'environmental', 'climate', 'sustainability'],
    'ESG':                 ['esg', 'environmental', 'climate', 'sustainability'],
    'Competitive':         ['competitive', 'market', 'commercial', 'operational'],
    'Operational':         ['operational', 'operations'],
  };

  function resolveDomainRisks(domains, risks) {
    if (!risks?.length) return [];
    const matched = [];
    for (const domain of (domains || [])) {
      const fragments = DOMAIN_RISK_CATS[domain] || [];
      for (const r of risks) {
        const searchable = ((r.category || '') + ' ' + (r.name || '')).toLowerCase();
        if (fragments.some(f => searchable.includes(f)) && !matched.includes(r.id)) {
          matched.push(r.id);
        }
      }
    }
    return matched.slice(0, 3);
  }

  // ── Core grading functions ────────────────────────────────

  function tokenize(text) {
    return (text || "").toLowerCase().replace(/[^\w\s-]/g, " ");
  }

  function scoreRelevance(text, domains) {
    const t = tokenize(text);
    let hits = 0;
    for (const domain of domains) {
      const vocab = DOMAIN_VOCAB[domain] || [];
      for (const kw of vocab) {
        if (t.includes(kw)) hits += 1;
      }
    }
    return Math.min(1, hits / 3); // 3 hits = full relevance
  }

  function scoreSeverity(text) {
    const t = tokenize(text);
    let score = 0.3; // baseline
    for (const [word, w] of Object.entries(SEVERITY_WORDS)) {
      if (t.includes(word)) score += w * 0.15;
    }
    return Math.min(1, score);
  }

  function velocityFromScores(relevance, severity, feedWeight) {
    const raw = relevance * severity * 5 * feedWeight;
    return Math.round(Math.max(0, Math.min(5, raw)));
  }

  function gradeArticle(article, feed, risks = [], companyName = "") {
    const text = `${article.title || ""} ${article.description || article.summary || ""}`;
    const relevance = scoreRelevance(text, feed.domains);
    const severity  = article.severity_hint != null
      ? Math.min(1, article.severity_hint / 3)
      : scoreSeverity(text);
    let velocity    = velocityFromScores(relevance, severity, feed.weight);

    // Company-gated feeds: suppress articles that don't mention the company.
    // CISA ICS advisories cover all vendors — only surface ones relevant to this company.
    if (feed.companyGated && companyName) {
      const t = tokenize(text);
      const STOP = new Set(['corp', 'inc', 'ltd', 'llc', 'group', 'holdings']);
      const terms = companyName.toLowerCase().split(/\W+/).filter(w => w.length >= 4 && !STOP.has(w));
      if (terms.length && !terms.some(term => t.includes(term))) velocity = 0;
    }

    const rag = velocity >= 3 ? "R" : velocity >= 2 ? "A" : "G";

    // Prefer dynamic domain→risk matching when the live register is available.
    // Fall back to the hardcoded per-feed risk IDs so older call sites still work.
    const dynamicRisks = risks.length ? resolveDomainRisks(feed.domains, risks) : null;

    return {
      id:            `${feed.id}-${Math.random().toString(36).slice(2,8)}`,
      feedId:        feed.id,
      feedName:      feed.name,
      title:         article.title || "(No title)",
      url:           article.link || article.url || null,
      pubDate:       article.pubDate || article.published || new Date().toISOString(),
      relevance:     parseFloat(relevance.toFixed(2)),
      severity:      parseFloat(severity.toFixed(2)),
      velocity,
      rag,
      affectedRisks: dynamicRisks?.length ? dynamicRisks : (article.risks || feed.risks),
      domains:       feed.domains,
      src:           "Industry RSS",
      delta:         `v=${velocity >= 0 ? "+" : ""}${velocity}`,
      label:         article.title || "(No title)",
      cat:           "RSS",
      gradedAt:      new Date().toISOString(),
    };
  }

  // ── Parse RSS 2.0 or Atom 1.0 XML ───────────────────────
  function parseXmlFeed(xmlText) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(xmlText, "application/xml");
    if (doc.querySelector("parsererror")) return null;
    const root = doc.documentElement;
    // Atom feed (root element is <feed>)
    if (root.localName === "feed") {
      return Array.from(doc.querySelectorAll("entry")).slice(0, 8).map(e => ({
        title:       e.querySelector("title")?.textContent?.trim() || "",
        description: e.querySelector("summary")?.textContent?.trim() || e.querySelector("content")?.textContent?.trim() || "",
        pubDate:     e.querySelector("published")?.textContent || e.querySelector("updated")?.textContent || "",
        link:        e.querySelector("link[rel='alternate']")?.getAttribute("href") || e.querySelector("link")?.getAttribute("href") || "",
      }));
    }
    // RSS 2.0
    return Array.from(doc.querySelectorAll("item")).slice(0, 8).map(item => ({
      title:       item.querySelector("title")?.textContent?.trim() || "",
      description: item.querySelector("description")?.textContent?.trim() || "",
      pubDate:     item.querySelector("pubDate")?.textContent || "",
      link:        item.querySelector("link")?.textContent?.trim() || "",
    }));
  }

  // ── Fetch feed XML via local dev-server proxy ─────────────
  // Routes through /api/rss-proxy (vite.config.js) so the request is made
  // server-side, bypassing browser CORS restrictions.
  async function fetchFeedUrl(url) {
    try {
      const res = await fetch(`/api/rss-proxy?url=${encodeURIComponent(url)}`, {
        signal: AbortSignal.timeout(10000),
      });
      if (!res.ok) return null;
      const text = await res.text();
      return parseXmlFeed(text);
    } catch {
      return null;
    }
  }

  async function fetchFeed(feed) {
    if (!feed.url) return null;
    return fetchFeedUrl(feed.url);
  }

  // ── EDGAR peer filing fetcher ─────────────────────────────
  // Calls /edgar/peers for the active ticker, then fetches each peer's
  // EDGAR atom feed so articles reflect real industry competitors.
  async function fetchEdgarPeerFilings(ticker) {
    if (!ticker) return null;
    try {
      const peersRes = await fetch("/api/mcp/edgar/peers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker }),
        signal: AbortSignal.timeout(20000),
      });
      if (!peersRes.ok) return null;
      const data = await peersRes.json();
      const peers = (data.peers || []).slice(0, 5);
      if (!peers.length) return null;

      const allArticles = [];
      for (const peer of peers) {
        const cik = peer.cik_plain || (peer.cik || "").replace(/^0+/, "");
        if (!cik) continue;
        const feedUrl =
          `https://www.sec.gov/cgi-bin/browse-edgar` +
          `?action=getcompany&CIK=${cik}&type=&dateb=&owner=include&count=3&output=atom`;
        const articles = await fetchFeedUrl(feedUrl);
        if (articles) {
          const label = peer.ticker || peer.company_name || "";
          for (const a of articles) {
            allArticles.push({ ...a, title: label ? `[${label}] ${a.title}` : a.title });
          }
        }
      }
      return allArticles.length ? allArticles : null;
    } catch {
      return null;
    }
  }

  // ── Backend config loader ─────────────────────────────────
  // Loads FEEDS, DOMAIN_VOCAB, SEVERITY_WORDS and DOMAIN_RISK_CATS from the
  // Python backend on startup. Falls back to hardcoded values if unavailable.
  // ingestAll() awaits this so the first run always uses backend data.
  let _initDone = false;
  const _initPromise = (async () => {
    try {
      const [feedsRes, cfgRes] = await Promise.all([
        fetch('/api/mcp/rss/feeds',      { signal: AbortSignal.timeout(5000) }).catch(() => null),
        fetch('/api/mcp/scoring/config', { signal: AbortSignal.timeout(5000) }).catch(() => null),
      ]);
      if (feedsRes?.ok) {
        const data = await feedsRes.json();
        if (data.feeds?.length) FEEDS.splice(0, FEEDS.length, ...data.feeds);
      }
      if (cfgRes?.ok) {
        const cfg = await cfgRes.json();
        if (cfg.domain_vocab)     Object.assign(DOMAIN_VOCAB,     cfg.domain_vocab);
        if (cfg.severity_words)   Object.assign(SEVERITY_WORDS,   cfg.severity_words);
        if (cfg.domain_risk_cats) Object.assign(DOMAIN_RISK_CATS, cfg.domain_risk_cats);
      }
    } finally {
      _initDone = true;
    }
  })();

  // ── Main ingestion run ────────────────────────────────────
  // opts.enabledFeedIds — array of feed IDs to include; defaults to all
  // opts.ticker         — active ticker; used to resolve EDGAR peer feeds
  // opts.risks          — live risk register; enables dynamic domain→risk assignment
  // opts.onProgress(msg, feedId, done) — called per feed
  async function ingestAll(opts = {}) {
    if (!_initDone) await _initPromise;
    const { onProgress, enabledFeedIds, ticker, risks = [], companyName = "" } = opts;
    const feeds = enabledFeedIds
      ? FEEDS.filter(f => enabledFeedIds.includes(f.id))
      : FEEDS;
    const results = [];

    for (const feed of feeds) {
      onProgress?.(`Fetching ${feed.name}…`, feed.id, false);
      let rawArticles;
      if (feed.type === "edgar-peers" && ticker) {
        rawArticles = await fetchEdgarPeerFilings(ticker);
        // Fallback to generic EDGAR feed when backend is unavailable
        if (!rawArticles) rawArticles = await fetchFeed(feed);
      } else {
        rawArticles = await fetchFeed(feed);
      }
      const fetchStatus = rawArticles ? "ok" : "failed";
      const graded = (rawArticles || []).map(a => gradeArticle(a, feed, risks, companyName));
      results.push({ feed, articles: graded, fetchStatus });
      onProgress?.(`${feed.name} fetched`, feed.id, true);
    }

    onProgress?.("Grading complete.", null, false);
    return results;
  }

  // ── Convert graded results to signal format ───────────────
  function toSignals(ingestResults) {
    return ingestResults.flatMap(({ articles }) =>
      articles.filter(a => a.velocity > 0)
    );
  }

  return {
    FEEDS,
    DOMAIN_VOCAB,
    DOMAIN_RISK_CATS,
    gradeArticle,
    resolveDomainRisks,
    scoreRelevance,
    scoreSeverity,
    velocityFromScores,
    ingestAll,
    toSignals,
  };
})();
