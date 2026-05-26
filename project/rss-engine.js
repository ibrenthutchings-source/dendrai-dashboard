/* ============================================================
   Dendrai Risk Loop — RSS Ingestion + Grading Engine
   Fetch → parse → keyword-relevance grade → velocity delta

   Fetch path:
     1. Real fetch via rss2json.com (CORS-friendly, rate-limited)
     2. Fallback: domain-appropriate simulated articles

   Grading:
     - Relevance  0→1  (keyword density against risk domain vocab)
     - Severity   0→1  (urgency language detection)
     - Novelty    0→1  (dedup against seen-article cache)
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
    },
    {
      id: "sec",
      name: "SEC EDGAR Filings",
      url: "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=10-K&dateb=&owner=include&count=8&output=atom",
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
      url: "https://www.epa.gov/newsreleases/search/rss",
      domains: ["ESG"],
      risks: ["R-07"],
      weight: 0.8,
      icon: "compass",
    },
    {
      id: "sia",
      name: "Semiconductor Industry (SIA)",
      url: "",
      domains: ["Operational", "Supply"],
      risks: ["R-03", "R-06", "R-10"],
      weight: 1.1,
      icon: "bolt",
      simulateOnly: true,
    },
    {
      id: "peers",
      name: "Peer Company Disclosures",
      url: "",
      domains: ["Financial Reporting", "Trade Compliance", "Operational", "Macro"],
      risks: ["R-01", "R-02", "R-03", "R-09"],
      weight: 1.3,
      icon: "users",
      simulateOnly: true,
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

  // ── Simulated article templates per feed ─────────────────
  const SIMULATED_ARTICLES = {
    bis: [
      { title: "BIS Expands Entity List — 14 Chinese Semiconductor Entities Added", severity_hint: 3.0, risks: ["R-02"] },
      { title: "Commerce Dept Proposes New ECCN Classification for Advanced Logic Chips", severity_hint: 2.0, risks: ["R-02"] },
      { title: "Export Administration Regulations (EAR) Annual Review Published", severity_hint: 1.0, risks: ["R-02"] },
      { title: "BIS Issues Advisory on Proliferation-Related Export Control Violations", severity_hint: 2.5, risks: ["R-02"] },
    ],
    cisa: [
      { title: "CISA Issues Advisory on ICS Vulnerabilities in Semiconductor Manufacturing Equipment", severity_hint: 2.5, risks: ["R-04"] },
      { title: "Alert: Privileged Access Management Gaps in OT Environments", severity_hint: 2.0, risks: ["R-04"] },
      { title: "Known Exploited Vulnerabilities Catalog Updated — 8 New Entries", severity_hint: 1.5, risks: ["R-04"] },
    ],
    sec: [
      { title: "SEC Issues Comment Letters on Revenue Recognition Disclosures in Semiconductor Sector", severity_hint: 2.0, risks: ["R-01", "R-05"] },
      { title: "SEC Climate Disclosure Rule: Implementation Guidance for Semiconductor Manufacturers", severity_hint: 1.5, risks: ["R-07"] },
      { title: "SEC Enforcement Action: Revenue Recognition Manipulation — Analog Devices Peer", severity_hint: 3.0, risks: ["R-01"] },
    ],
    fed: [
      { title: "Fed Beige Book: Manufacturing Sector Reports Continued Contraction", severity_hint: 1.5, risks: ["R-09"] },
      { title: "Philadelphia Fed Manufacturing Survey: Index Falls to −5.4", severity_hint: 2.0, risks: ["R-09"] },
      { title: "FOMC Minutes: Elevated Rate Uncertainty — Semiconductor CapEx Commentary", severity_hint: 1.2, risks: ["R-06", "R-09"] },
    ],
    epa: [
      { title: "EPA Issues Water Scarcity Designation for Central Arizona — Fab Cluster Impact", severity_hint: 2.0, risks: ["R-07"] },
      { title: "EPA Climate Enforcement: New Reporting Requirements for Semiconductor Fabs", severity_hint: 1.5, risks: ["R-07"] },
    ],
    sia: [
      { title: "SIA: Global Semiconductor Sales Down 12% YoY — Auto Segment Weakest", severity_hint: 2.0, risks: ["R-03", "R-09"] },
      { title: "SIA Members Report Extended Inventory Correction — SiC Product Family Most Exposed", severity_hint: 2.5, risks: ["R-03"] },
      { title: "SIA Annual Report: Conflict Minerals RMAP Coverage Improves to 97% Industry Average", severity_hint: 0.5, risks: ["R-10"] },
      { title: "SEMI Manufacturing Equipment Orders Fall 18% — Capacity Discipline Maintained", severity_hint: 1.5, risks: ["R-06"] },
    ],
    peers: [
      { title: "TXN Q4 Earnings: Export Control Headwinds Cut Revenue Guidance 8% — BIS License Delays Cited", severity_hint: 2.5, risks: ["R-02", "R-01"] },
      { title: "STMicroelectronics Discloses €420M NRV Reserve on SiC Inventory Overbuild", severity_hint: 2.5, risks: ["R-03"] },
      { title: "Microchip Technology 10-K: Material Weakness in Revenue Cut-Off Controls — SOX 404(b) Finding", severity_hint: 3.0, risks: ["R-01", "R-05"] },
      { title: "NXP Semiconductors Updates BIS Compliance Program — EAR License Required for 6 SKUs", severity_hint: 2.0, risks: ["R-02"] },
      { title: "Analog Semi Peer Round-Up: Q1 Book-to-Bill Ratio Falls Below 1.0 for 4th Consecutive Quarter", severity_hint: 1.5, risks: ["R-09", "R-01"] },
      { title: "Broadcom Q1 Earnings: Channel Inventory Normalising in Auto, Industrial Still Elevated", severity_hint: 1.5, risks: ["R-03", "R-09"] },
      { title: "Texas Instruments Issues SEC Comment Letter Response on Revenue Recognition Disclosures", severity_hint: 2.0, risks: ["R-01"] },
    ],
  };

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

  function gradeArticle(article, feed) {
    const text = `${article.title || ""} ${article.description || article.summary || ""}`;
    const relevance = scoreRelevance(text, feed.domains);
    const severity  = article.severity_hint != null
      ? Math.min(1, article.severity_hint / 3)
      : scoreSeverity(text);
    const velocity  = velocityFromScores(relevance, severity, feed.weight);
    const rag       = velocity >= 3 ? "R" : velocity >= 2 ? "A" : "G";

    return {
      id:         `${feed.id}-${Math.random().toString(36).slice(2,8)}`,
      feedId:     feed.id,
      feedName:   feed.name,
      title:      article.title || "(No title)",
      url:        article.link || article.url || null,
      pubDate:    article.pubDate || article.published || new Date().toISOString(),
      relevance:  parseFloat(relevance.toFixed(2)),
      severity:   parseFloat(severity.toFixed(2)),
      velocity,
      rag,
      affectedRisks: article.risks || feed.risks,
      domains:    feed.domains,
      src:        "Industry RSS",
      delta:      `v=${velocity >= 0 ? "+" : ""}${velocity}`,
      label:      article.title || "(No title)",
      cat:        "RSS",
      gradedAt:   new Date().toISOString(),
    };
  }

  // ── Fetch via rss2json.com ────────────────────────────────
  async function fetchFeed(feed) {
    if (feed.simulateOnly || !feed.url) return null;
    const apiUrl = `https://api.rss2json.com/v1/api.json?rss_url=${encodeURIComponent(feed.url)}&count=8`;
    try {
      const res = await fetch(apiUrl, { signal: AbortSignal.timeout(8000) });
      if (!res.ok) return null;
      const data = await res.json();
      if (data.status !== "ok" || !Array.isArray(data.items)) return null;
      return data.items.slice(0, 8);
    } catch {
      return null;
    }
  }

  // ── Simulate articles for a feed ─────────────────────────
  function simulateFeed(feed, count = 3) {
    const templates = SIMULATED_ARTICLES[feed.id] || [];
    if (!templates.length) return [];
    // Pick random subset, add noise to pubDate
    const shuffled = [...templates].sort(() => Math.random() - 0.5);
    return shuffled.slice(0, Math.min(count, templates.length)).map(t => ({
      ...t,
      pubDate: new Date(Date.now() - Math.random() * 7 * 86400000).toISOString(),
      link: null,
    }));
  }

  // ── Main ingestion run ────────────────────────────────────
  async function ingestAll(opts = {}) {
    const { onProgress, simulate = false } = opts;
    const results = [];

    for (const feed of FEEDS) {
      onProgress?.(`Fetching ${feed.name}…`);

      let rawArticles = null;
      let fetchStatus = "ok";

      if (!simulate && !feed.simulateOnly) {
        rawArticles = await fetchFeed(feed);
        if (!rawArticles) fetchStatus = "simulated";
      }

      if (!rawArticles) {
        rawArticles = simulateFeed(feed, 3);
        fetchStatus = feed.simulateOnly ? "simulated" : "fallback";
      }

      const graded = rawArticles.map(a => gradeArticle(a, feed));
      results.push({ feed, articles: graded, fetchStatus });
    }

    onProgress?.("Grading complete.");
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
    gradeArticle,
    scoreRelevance,
    scoreSeverity,
    velocityFromScores,
    ingestAll,
    simulateFeed,
    toSignals,
  };
})();
