/* ============================================================
   Dendrai Risk Loop — mock data
   onsemi (ON) + peer set, modeled on the IA Risk Chain Engine
   spec. All numbers plausible; no live API calls.
   ============================================================ */

window.MOCK = (function () {

  // --------------------------------------------------------
  // ENTITY
  // --------------------------------------------------------
  const entity = {
    name: "onsemi",
    ticker: "ON",
    industry: "Analog Semiconductors",
    focus: "Revenue Recognition",
    period: "Q1 2022 — Q4 2024",
    periodBegin: "Q1 2025",
    periodEnd: "Q4 2025",
    auditor: "PwC",
    auditor_opinion: "Unqualified",
    fy_close: "2024-12-31",
    peers: ["STMicroelectronics", "Microchip", "Texas Instruments"],
    distributors: ["Arrow Electronics", "Avnet", "TD SYNNEX"],
    customers: ["General Motors", "Ford", "Bosch"],
  };

  // --------------------------------------------------------
  // STAGE 1 — SIGNAL INTAKE
  // --------------------------------------------------------
  const signals = [
    { src: "EDGAR 10-K",    label: "FY24 10-K filed",                          delta: "+2 new risk factors", velocity: 2, cat: "Filing" },
    { src: "EDGAR 10-K",    label: "Going-concern flag — false",               delta: "stable",              velocity: 0, cat: "Filing" },
    { src: "Peer 10-K",     label: "TXN — auto demand softening guidance",     delta: "+1 sector signal",    velocity: 1, cat: "Peer" },
    { src: "Peer 10-K",     label: "STM — SiC inventory write-down $0.2B",     delta: "spillover risk",      velocity: 2, cat: "Peer" },
    { src: "Industry RSS",  label: "BIS export rule update — advanced nodes",  delta: "+3 articles 7d",      velocity: 3, cat: "Reg" },
    { src: "Industry RSS",  label: "Auto OEM destocking cycle Q1",             delta: "+5 articles 7d",      velocity: 3, cat: "Macro" },
    { src: "Internal KRI",  label: "DSO rising — 73 → 81 days",                delta: "+8 days QoQ",         velocity: 2, cat: "KRI" },
    { src: "Internal KRI",  label: "Distributor channel weeks-on-hand 14.2",   delta: "+2.4 weeks QoQ",      velocity: 3, cat: "KRI" },
    { src: "Internal KRI",  label: "Privileged access reviews — 89% on-time",  delta: "-6 pts QoQ",          velocity: 2, cat: "KRI" },
    { src: "Internal KRI",  label: "Trade screening hits — 3 partial matches", delta: "+1 vs prior qtr",     velocity: 1, cat: "KRI" },
    { src: "FRED Macro",    label: "IPG3344S Semiconductor index -3.2% MoM",   delta: "contractionary",      velocity: 2, cat: "Macro" },
    { src: "FRED Macro",    label: "UMCSENT consumer sentiment -4.1",          delta: "contractionary",      velocity: 1, cat: "Macro" },
  ];

  // --------------------------------------------------------
  // STAGE 2 — RISK REGISTER
  // 10-risk register with velocity, control effectiveness,
  // inherent vs residual, peer benchmark, sparkline history
  // --------------------------------------------------------
  const risks = [
    { id: "R-01", name: "Revenue Recognition — Channel Stuffing",      category: "Financial Reporting", score: 7.8, rag: "R", velocity: 2,  ce: "ADEQUATE", inherent: 8.5, residual: 7.8, peer: "above",  hist: [6.2, 6.4, 6.8, 7.1, 7.5, 7.8], narrative: "DSO trend deteriorating 4Q; distributor channel weeks-on-hand rising while end-market demand softens. ASC 606 sell-in vs sell-through disclosure quality flagged." },
    { id: "R-02", name: "Export Control — China BIS Expansion",        category: "Trade Compliance",     score: 7.2, rag: "R", velocity: 3,  ce: "WEAK",     inherent: 8.0, residual: 7.2, peer: "above",  hist: [5.0, 5.4, 5.8, 6.4, 6.8, 7.2], narrative: "October 2024 BIS rule expanded covered ECCN universe. 32% revenue exposure to Greater China. License turnaround variable." },
    { id: "R-03", name: "Inventory Write-down — SiC / Auto",           category: "Operational",          score: 6.8, rag: "A", velocity: 3,  ce: "ADEQUATE", inherent: 7.5, residual: 6.8, peer: "in-line", hist: [3.8, 4.4, 5.2, 5.9, 6.4, 6.8], narrative: "Auto OEM destocking cycle entering Q3; SiC backlog coverage falling. STM peer reported $0.2B reserve." },
    { id: "R-04", name: "Cyber — Privileged Access Drift",             category: "Cybersecurity",        score: 6.5, rag: "A", velocity: 2,  ce: "ADEQUATE", inherent: 7.8, residual: 6.5, peer: "in-line", hist: [5.8, 5.9, 6.1, 6.2, 6.3, 6.5], narrative: "Quarterly access certification completeness dropped to 89%. SOC2 control 6.2 deviation logged on production systems." },
    { id: "R-05", name: "M-Score Forensic — TATA Driver",              category: "Financial Reporting", score: 6.1, rag: "A", velocity: 1,  ce: "ADEQUATE", inherent: 6.8, residual: 6.1, peer: "above",  hist: [4.8, 5.0, 5.4, 5.7, 5.9, 6.1], narrative: "M-Score -1.62 — ELEVATED band. TATA accrual quality key driver; CFO/NI divergence under review." },
    { id: "R-06", name: "Capex Discipline — 2 Period Under-Spend",     category: "Operational",          score: 5.4, rag: "A", velocity: -1, ce: "STRONG",   inherent: 6.0, residual: 5.4, peer: "in-line", hist: [6.2, 6.0, 5.8, 5.6, 5.5, 5.4], narrative: "Two consecutive periods of guided-vs-actual CapEx under-spend by >20%. Possible underinvestment in capacity." },
    { id: "R-07", name: "Climate — Arizona Fab Water Stress",          category: "ESG",                  score: 5.1, rag: "A", velocity: 1,  ce: "ADEQUATE", inherent: 6.4, residual: 5.1, peer: "below",  hist: [4.2, 4.4, 4.6, 4.8, 4.9, 5.1], narrative: "Aqueduct Extremely High at Pocatello/Mesa fab clusters. SEC climate disclosure compliance partial." },
    { id: "R-08", name: "IP Litigation — Active Patent Cases",         category: "Legal",                score: 4.9, rag: "A", velocity: 0,  ce: "STRONG",   inherent: 5.8, residual: 4.9, peer: "in-line", hist: [5.1, 5.0, 5.0, 4.9, 4.9, 4.9], narrative: "Three active cases; estimated exposure $42M; cross-licensing portfolio adequate." },
    { id: "R-09", name: "Macro — PMI / IPG3344S Correlation",          category: "Macro",                score: 4.6, rag: "G", velocity: 2,  ce: "STRONG",   inherent: 6.0, residual: 4.6, peer: "in-line", hist: [3.2, 3.4, 3.8, 4.1, 4.3, 4.6], narrative: "Manufacturing PMI -5.4 MoM. Semiconductor index correlation r=0.82 against company revenue suggests soft Q1." },
    { id: "R-10", name: "Conflict Minerals — RMAP Coverage",           category: "Supply",               score: 3.8, rag: "G", velocity: 0,  ce: "STRONG",   inherent: 4.8, residual: 3.8, peer: "below",  hist: [3.9, 3.9, 3.8, 3.8, 3.8, 3.8], narrative: "Form SD filed timely; RMAP-certified smelter coverage 96%; no Xinjiang-linked tier-2 exposure detected." },
  ];

  // --------------------------------------------------------
  // STAGE 3 — AUDIT SCOPE / OBJECTIVES
  // --------------------------------------------------------
  const objectives = [
    { id: "O-01", priority: "P1", objective: "Revenue cut-off & channel-stuffing test — Q4 sample expansion",
      linked_risk: "R-01", sprint: 1, hours: 120,
      controls: ["RC-401 Contract review gate", "RC-402 Distributor confirmation", "RC-405 Bill-and-hold attestation"],
      rationale: "Velocity +2 sustained 3Q; DSO + channel WoH both worsening; peer signals corroborate destocking risk." },
    { id: "O-02", priority: "P1", objective: "Export control license file walkthrough — China shipments Q4",
      linked_risk: "R-02", sprint: 1, hours: 80,
      controls: ["TC-101 ECCN classification", "TC-104 End-user screening", "TC-110 BIS license tracker"],
      rationale: "BIS rule change Oct-24; weak control effectiveness on screening hits; 32% China revenue exposure." },
    { id: "O-03", priority: "P2", objective: "Inventory NRV testing — SiC product family, auto end-market",
      linked_risk: "R-03", sprint: 2, hours: 64,
      controls: ["INV-301 NRV write-down workflow", "INV-305 Distributor demand reconciliation"],
      rationale: "Velocity +3; peer write-down precedent; backlog coverage deteriorating." },
    { id: "O-04", priority: "P2", objective: "Privileged access certification — re-perform Q4 review",
      linked_risk: "R-04", sprint: 2, hours: 48,
      controls: ["ITGC-201 PAR completeness", "ITGC-205 SoD enforcement"],
      rationale: "Completeness rate 89% — below 95% threshold for SOX assertion." },
    { id: "O-05", priority: "P3", objective: "CapEx discipline review — guidance vs actual variance",
      linked_risk: "R-06", sprint: 3, hours: 32,
      controls: ["FIN-501 CapEx authorization", "FIN-503 Variance reporting"],
      rationale: "Two-period under-spend; assess whether underinvestment masks capacity risk." },
    { id: "O-06", priority: "P3", objective: "Climate risk disclosure mapping — Arizona fabs",
      linked_risk: "R-07", sprint: 3, hours: 24,
      controls: ["ESG-701 SEC climate compliance"],
      rationale: "Disclosure partial; physical risk material; aligns with 2025 SEC enforcement priorities." },
  ];

  // --------------------------------------------------------
  // STAGE 4 — MANAGEMENT ACTION PLANS
  // --------------------------------------------------------
  const maps = [
    { id: "MAP-01", finding: "Distributor confirmation procedure lacks evidence of sell-through reconciliation",
      condition: "27 of 60 sampled Q4 contracts had no distributor inventory-at-period-end attestation.",
      root_cause: "Procedure RC-402 amended Q2-24 to require attestation but workflow tool not updated.",
      risk_impact: "R", linked_risk: "R-01",
      action: "Re-platform RC-402 onto contract lifecycle tool with mandatory attestation gate before billing.",
      owner: "VP Revenue Operations", due_date: "2025-Q2", completion_pct: 35,
      success_criteria: "100% of distributor contracts have attestation on file; SAS 145 walkthrough green.",
      reduction_pct: 22 },
    { id: "MAP-02", finding: "ECCN classification for 4 product families relies on supplier representation only",
      condition: "Spot-check of 18 shipments to Greater China found 4 SKUs with self-classified ECCN, no engineering review.",
      root_cause: "Q3-24 product launches bypassed Trade Compliance pre-launch review due to time-to-market pressure.",
      risk_impact: "R", linked_risk: "R-02",
      action: "Mandatory engineering ECCN review for all new SKUs; back-classify FY24 launches; BIS voluntary disclosure if needed.",
      owner: "Director Trade Compliance", due_date: "2025-Q1", completion_pct: 60,
      success_criteria: "0 self-classified SKUs in new launches; back-file complete; BIS counsel sign-off.",
      reduction_pct: 28 },
    { id: "MAP-03", finding: "Inventory reserve methodology does not differentiate SiC vs silicon product families",
      condition: "FY24 NRV reserve calculated at consolidated level; SiC inventory ($380M) exposure not isolated.",
      root_cause: "ERP reporting hierarchy designed for legacy product taxonomy.",
      risk_impact: "A", linked_risk: "R-03",
      action: "Implement SiC-isolated reserve calc; recompute Q4 reserve; disclosure update for 10-K.",
      owner: "Corporate Controller", due_date: "2025-Q1", completion_pct: 75,
      success_criteria: "10-K reserve disclosure shows SiC product family separately.",
      reduction_pct: 18 },
    { id: "MAP-04", finding: "Privileged access quarterly certification completeness 89%",
      condition: "11% of privileged accounts (147 users) missed Q4 re-certification window.",
      root_cause: "Manager delegation chain breakdown when org-chart changes occur mid-cycle.",
      risk_impact: "A", linked_risk: "R-04",
      action: "Auto-detect org-chart deltas; re-route pending certifications; escalate to skip-level after 5 days.",
      owner: "CISO", due_date: "2025-Q2", completion_pct: 50,
      success_criteria: ">=98% PAR completeness over 2 consecutive quarters; SOX 404 control rated effective.",
      reduction_pct: 15 },
    { id: "MAP-05", finding: "CapEx variance reporting reaches FP&A weekly but no formal IA visibility",
      condition: "Two consecutive periods of >20% under-spend not surfaced in audit committee pack.",
      root_cause: "CapEx reporting routed through FP&A only; no DataOps feed to audit dashboards.",
      risk_impact: "A", linked_risk: "R-06",
      action: "Add CapEx variance feed to GRC dashboard; auto-flag at ±20%; quarterly AC inclusion.",
      owner: "CFO", due_date: "2025-Q3", completion_pct: 20,
      success_criteria: "AC dashboard shows CapEx variance trend; >20% deviations auto-paged to CAE.",
      reduction_pct: 10 },
    { id: "MAP-06", finding: "Climate disclosure does not map physical risk sites to revenue-bearing product lines",
      condition: "Pocatello and Mesa fabs (Arizona) — water stress Extremely High — not mapped to SiC product family.",
      root_cause: "Sustainability and Investor Relations teams maintain separate site/product maps.",
      risk_impact: "G", linked_risk: "R-07",
      action: "Unified site-to-product-line ledger; refresh 10-K Item 1A and ESG report.",
      owner: "Chief Sustainability Officer", due_date: "2025-Q3", completion_pct: 10,
      success_criteria: "Single source of truth ledger maintained; physical risk disclosed at product-family level.",
      reduction_pct: 8 },
  ];

  // --------------------------------------------------------
  // STAGE 5 — CLOSURE / RISK REDUCTION
  // --------------------------------------------------------
  const closure = {
    risks_closed: 2,
    risks_reduced: 4,
    risks_unchanged: 4,
    projected_total_risk_reduction_pct: 23,
    evidence_artifacts: 47,
    rerun_recommended: ["R-01", "R-02"],
  };

  // --------------------------------------------------------
  // STAGE 6 — LOOP CALIBRATION
  // --------------------------------------------------------
  const loop = {
    loop_health: "A",
    audit_impact_score: 7.4,
    risk_reduction_pct: 23,
    maps_open: 6,
    risks_closed: 2,
    next_trigger_days: 28,
    next_cycle_focus: "Revenue cut-off re-test post-Q1 close; BIS rule change impact assessment; SiC reserve validation.",
    lessons_learned: [
      "Channel-stuffing velocity outpaces DSO threshold — recalibrate to 7-day rolling delta.",
      "BIS rule change signal arrived 11 days late from Industry RSS — add direct .gov feed.",
      "Distributor attestation procedure change missed by GRC change-mgmt — link to platform release pipeline.",
    ],
    velocity_threshold_recommendation: 2.5,
  };

  // --------------------------------------------------------
  // CONTROL EVENT MONITOR — synthetic events
  // --------------------------------------------------------
  const eventTemplates = [
    { control: "Revenue Recognition — Contract Review Gate",   area: "Revenue",            risk: "Revenue overstatement",     severity: "P1", exposure: "$12–18M",            category: "Financial Reporting",
      rc: "Most likely root cause: distributor attestation workflow regression after Q4 platform release. Containment: block billing on un-attested distributor contracts pending manual review. Systemic fix: re-platform RC-402 on contract lifecycle tool with mandatory gate." },
    { control: "Export License Validation — ECCN Check",       area: "Trade Compliance",   risk: "Export violation",          severity: "P1", exposure: "Regulatory",         category: "Trade Compliance",
      rc: "Likely root cause: self-classified ECCN on new SKU shipped to Greater China without engineering review. Containment: hold shipments to affected end-users; flag for trade-counsel screen. Systemic fix: mandate engineering ECCN sign-off in product launch workflow." },
    { control: "Segregation of Duties — AP Approval",          area: "Accounts Payable",   risk: "Fraudulent disbursement",   severity: "P2", exposure: "$2–5M",              category: "Fraud Risk",
      rc: "Likely root cause: temporary delegation during Q4 close granted both initiate and approve. Containment: revoke delegation; reverse last 30 days of dual-approved entries for review. Systemic fix: SoD matrix enforcement at workflow layer, not via policy alone." },
    { control: "Inventory Count Reconciliation",                area: "Supply Chain",       risk: "Inventory misstatement",    severity: "P2", exposure: "$4–8M",              category: "Operations",
      rc: "Likely root cause: cycle count cadence skipped at Mesa fab during line conversion. Containment: full count of SiC WIP; reconcile against ERP. Systemic fix: automated reminders + escalation for skipped counts." },
    { control: "Access Provisioning — Privileged Accounts",     area: "IT General Controls",risk: "Unauthorized access",       severity: "P2", exposure: "Data breach",        category: "Cybersecurity",
      rc: "Likely root cause: org-chart change orphaned 147 user re-certifications. Containment: emergency re-cert via skip-level approvers. Systemic fix: auto-detect org deltas and re-route pending certifications." },
    { control: "Management Override Exception Log",             area: "Financial Reporting",risk: "Management override",        severity: "P1", exposure: "Material misstatement", category: "Financial Reporting",
      rc: "Likely root cause: $4.2M Q4 accrual posted with verbal CFO approval, no documented business case. Containment: require contemporaneous business case for >$1M overrides. Systemic fix: workflow-enforced documentation + AC visibility." },
    { control: "Third-Party Vendor SOC 2 Review",               area: "Vendor Management",  risk: "Supply chain exposure",     severity: "P3", exposure: "Reputational",       category: "Third-Party Risk",
      rc: "Likely root cause: SOC 2 Type II reports not refreshed for 7 tier-1 vendors. Containment: request current attestations. Systemic fix: vendor-portal auto-renewal cadence." },
    { control: "Journal Entry Authorization — Month-End",       area: "Accounting",         risk: "Unauthorized JE manipulation", severity: "P2", exposure: "$1–3M",          category: "Financial Reporting",
      rc: "Likely root cause: JE approver pool included terminated employee for 8 days. Containment: void affected JEs; re-route. Systemic fix: HRIS-to-ERP role-revocation real-time sync." },
  ];

  // --------------------------------------------------------
  // FORECASTS — revenue / margin
  // --------------------------------------------------------
  const forecasts = {
    revenue: {
      history: [ // quarterly $M
        { q: "Q1-22", v: 1945 }, { q: "Q2-22", v: 2086 }, { q: "Q3-22", v: 2192 }, { q: "Q4-22", v: 2104 },
        { q: "Q1-23", v: 1959 }, { q: "Q2-23", v: 2094 }, { q: "Q3-23", v: 2181 }, { q: "Q4-23", v: 2018 },
        { q: "Q1-24", v: 1862 }, { q: "Q2-24", v: 1735 }, { q: "Q3-24", v: 1762 }, { q: "Q4-24", v: 1721 },
      ],
      forecast: [ // base / lo / hi
        { q: "Q1-25", base: 1685, lo: 1602, hi: 1768 },
        { q: "Q2-25", base: 1712, lo: 1604, hi: 1822 },
        { q: "Q3-25", base: 1775, lo: 1635, hi: 1918 },
        { q: "Q4-25", base: 1840, lo: 1670, hi: 2014 },
      ],
    },
    margin: {
      history: [
        { q: "Q1-22", v: 49.1 }, { q: "Q2-22", v: 49.6 }, { q: "Q3-22", v: 49.0 }, { q: "Q4-22", v: 48.0 },
        { q: "Q1-23", v: 47.0 }, { q: "Q2-23", v: 47.4 }, { q: "Q3-23", v: 47.3 }, { q: "Q4-23", v: 46.7 },
        { q: "Q1-24", v: 45.8 }, { q: "Q2-24", v: 45.3 }, { q: "Q3-24", v: 45.5 }, { q: "Q4-24", v: 45.2 },
      ],
      forecast: [
        { q: "Q1-25", base: 44.5, lo: 42.8, hi: 46.2 },
        { q: "Q2-25", base: 44.8, lo: 42.5, hi: 47.1 },
        { q: "Q3-25", base: 45.4, lo: 42.6, hi: 48.2 },
        { q: "Q4-25", base: 46.2, lo: 43.0, hi: 49.4 },
      ],
    },
    fred: [
      { id: "IPG3344S",         name: "Phila Fed Semi Index",  r: 0.82,  lead: 2, dir: "CONTRACTIONARY", reading: -3.2 },
      { id: "CAPUTLG3311A2S",   name: "Mfg Capacity Util.",     r: 0.78,  lead: 1, dir: "CONTRACTIONARY", reading: 76.4 },
      { id: "MANEMP",           name: "ISM Mfg PMI",            r: 0.76,  lead: 1, dir: "CONTRACTIONARY", reading: 48.3 },
      { id: "UMCSENT",          name: "U-Mich Consumer Sent.",  r: 0.71,  lead: 3, dir: "NEUTRAL",         reading: 64.7 },
      { id: "DTWEXBGS",         name: "USD Broad Index",        r: -0.68, lead: 2, dir: "NEUTRAL",         reading: 121.4 },
    ],
    sentiment: {
      score: 6,
      trend: "IMPROVING",
      hedge_ratio_trend: "-19pp over 4Q",
      latest_quarter: "Q1-26",
      quarterly: [
        { q: "Q3-23", score: 12,  hedge: 0.08 },
        { q: "Q4-23", score: 6,   hedge: 0.10 },
        { q: "Q1-24", score: -2,  hedge: 0.12 },
        { q: "Q2-24", score: -8,  hedge: 0.18 },
        { q: "Q3-24", score: -14, hedge: 0.22 },
        { q: "Q4-24", score: -18, hedge: 0.26 },
        { q: "Q1-25", score: -20, hedge: 0.28 },
        { q: "Q2-25", score: -16, hedge: 0.24 },
        { q: "Q3-25", score: -8,  hedge: 0.18 },
        { q: "Q4-25", score: -2,  hedge: 0.12 },
        { q: "Q1-26", score: 6,   hedge: 0.09 },
      ],
    },
    mscore: {
      m: -1.62,
      band: "ELEVATED",
      key_driver: "TATA (accrual quality)",
      thresholds: { red: -1.78, amber: -2.22 },
      vars: {
        DSRI: 1.18, GMI: 1.04, AQI: 1.02, SGI: 0.95, DEPI: 1.01, SGAI: 1.08, LVGI: 1.05, TATA: 0.041,
      },
    },
  };

  // --------------------------------------------------------
  // SCENARIOS — Bear / Base / Bull
  // --------------------------------------------------------
  const scenarios = [
    { id: "bear", name: "Bear — China BIS expansion + auto destock",
      description: "October BIS rule extends to advanced analog; Greater China revenue −22% YoY; auto OEM destock prolongs through Q3.",
      probability: "MEDIUM",
      revenue_impact_pct: -18,
      gross_margin_impact_bps: -350,
      revenue_at_risk_m: 1310,
      runway_qtrs: 6,
      liquidity: "CONSTRAINED",
      kris_red: ["R-01", "R-02", "R-03"],
      recovery: "PROLONGED_5Q_PLUS",
      audit_focus: ["Revenue cut-off Q1–Q2", "BIS license documentation", "Inventory NRV stress"],
      vs_peers: "Most exposed: ON. Least exposed: TXN (diversified end markets)",
      assumptions: {
        china_revenue_delta: "−22%",
        capacity_util: "62%",
        usd_dxy: "+4%",
        pmi: "44.0",
      },
    },
    { id: "base", name: "Base — Soft landing through 1H25",
      description: "Destocking troughs Q1; gradual recovery from Q3; BIS impact contained to existing controls.",
      probability: "HIGH",
      revenue_impact_pct: -4,
      gross_margin_impact_bps: -110,
      revenue_at_risk_m: 290,
      runway_qtrs: 11,
      liquidity: "SUFFICIENT",
      kris_red: ["R-01"],
      recovery: "MODERATE_3_4Q",
      audit_focus: ["Revenue cut-off Q1", "ECCN classification refresh", "Capex variance"],
      vs_peers: "ON in-line with sector trajectory",
      assumptions: {
        china_revenue_delta: "−6%",
        capacity_util: "71%",
        usd_dxy: "0%",
        pmi: "49.0",
      },
    },
    { id: "bull", name: "Bull — AI-driven analog demand snap-back",
      description: "Data-center power management + EV inflection drives backlog recovery from Q2; SiC reserve releases.",
      probability: "LOW",
      revenue_impact_pct: +8,
      gross_margin_impact_bps: +180,
      revenue_at_risk_m: -580,
      runway_qtrs: 14,
      liquidity: "SUFFICIENT",
      kris_red: [],
      recovery: "RAPID_1_2Q",
      audit_focus: ["Capacity adequacy", "Revenue acceleration controls"],
      vs_peers: "ON best-positioned in analog SiC",
      assumptions: {
        china_revenue_delta: "+4%",
        capacity_util: "84%",
        usd_dxy: "−2%",
        pmi: "52.0",
      },
    },
  ];

  // --------------------------------------------------------
  // RISK FLOW — per-risk fan-out: impact areas + controls
  // + linked audit/MAP work, plus the velocity-driven cadence
  // of audit oversight across the next 90 days. Keys match
  // the risk register IDs.
  // --------------------------------------------------------
  const riskFlow = {
    "R-01": {
      impacts: ["Revenue Recognition", "Financial Reporting", "Investor Reporting"],
      controls: [
        { name: "Distributor attestation gate",      ce: "ADEQUATE" },
        { name: "Channel WoH monitor",               ce: "WEAK" },
        { name: "Q1/Q4 revenue cut-off review",      ce: "ADEQUATE" },
      ],
      audits: ["MAP-01", "Q1 sample expansion", "SOX 404 walkthrough"],
      cadence: ["T+14d", "T+30d", "T+60d", "T+75d"],
      summary: "Distributor attestation regression after Q4 platform release; sell-through visibility lagging sell-in.",
    },
    "R-02": {
      impacts: ["Trade Compliance", "Revenue Recognition", "Reputational"],
      controls: [
        { name: "ECCN classification review",        ce: "WEAK" },
        { name: "Restricted-party screening",        ce: "ADEQUATE" },
        { name: "End-user attestation",              ce: "ADEQUATE" },
      ],
      audits: ["MAP-02", "BIS deep-dive audit", "FY24 SKU back-classification"],
      cadence: ["T+7d", "T+21d", "T+45d", "T+70d", "T+85d"],
      summary: "October BIS rule extension expanded ECCN universe; 4 self-classified SKUs already shipped.",
    },
    "R-03": {
      impacts: ["Financial Reporting", "Supply Chain", "Operational"],
      controls: [
        { name: "NRV reserve methodology",           ce: "ADEQUATE" },
        { name: "Cycle counting (Mesa fab)",         ce: "ADEQUATE" },
        { name: "Demand forecasting model",          ce: "WEAK" },
      ],
      audits: ["MAP-03", "Inventory NRV stress test", "10-K reserve disclosure"],
      cadence: ["T+30d", "T+60d", "T+90d"],
      summary: "FY24 NRV reserve consolidated; SiC ($380M) exposure not isolated in ERP hierarchy.",
    },
    "R-04": {
      impacts: ["Cybersecurity", "Financial Reporting", "Revenue Recognition", "Trade Compliance", "ESG"],
      controls: [
        { name: "Privileged access certification",   ce: "ADEQUATE" },
        { name: "Org-chart delta auto-detect",       ce: "WEAK" },
        { name: "Just-in-time credential issuance",  ce: "STRONG" },
      ],
      audits: ["MAP-04", "ITGC reperformance", "Access governance audit", "Penetration test"],
      cadence: ["T+14d", "T+28d", "T+42d", "T+60d", "T+80d"],
      summary: "Privileged access cert at 89%; org-chart deltas mid-cycle orphan re-certifications. High blast radius across financial + operational systems.",
    },
    "R-05": {
      impacts: ["Financial Reporting", "Investor Reporting"],
      controls: [
        { name: "M-Score forensic monitor",          ce: "ADEQUATE" },
        { name: "Forensic walkthrough (quarterly)",  ce: "ADEQUATE" },
      ],
      audits: ["Forensic review", "M-Score model refresh"],
      cadence: ["T+30d", "T+75d"],
      summary: "M-Score elevated (−1.62) driven by TATA component; DSO +8d QoQ, channel WoH +2.4 weeks.",
    },
    "R-06": {
      impacts: ["Operational", "Financial Reporting"],
      controls: [
        { name: "Capex variance review",             ce: "STRONG" },
        { name: "Capacity-plan reconciliation",      ce: "STRONG" },
      ],
      audits: ["Capex utilisation review"],
      cadence: ["T+90d"],
      summary: "Two periods of under-spend vs plan; risk is opportunity-cost rather than control failure.",
    },
    "R-07": {
      impacts: ["ESG", "Operational", "Reputational"],
      controls: [
        { name: "Site water-stress monitoring",      ce: "ADEQUATE" },
        { name: "Drought-contingency plan (AZ)",     ce: "ADEQUATE" },
      ],
      audits: ["ESG water disclosure walkthrough"],
      cadence: ["T+60d"],
      summary: "Arizona fab water-stress index trending up; offset partially by reclaim investments coming online Q3.",
    },
    "R-08": {
      impacts: ["Legal", "Financial Reporting"],
      controls: [
        { name: "Active-litigation tracker",         ce: "STRONG" },
        { name: "Quarterly counsel review",          ce: "STRONG" },
      ],
      audits: ["Legal-letter procedure"],
      cadence: ["T+90d"],
      summary: "Active patent cases stable; no new claims this quarter. Reserve adequate.",
    },
    "R-09": {
      impacts: ["Macro", "Revenue Recognition", "Financial Reporting"],
      controls: [
        { name: "FRED correlate dashboard",          ce: "STRONG" },
        { name: "Peer earnings monitor",             ce: "STRONG" },
      ],
      audits: ["Macro early-warning review"],
      cadence: ["T+45d"],
      summary: "PMI / Phila Fed correlation strong; current reading not at alert threshold but converging.",
    },
    "R-10": {
      impacts: ["Supply Chain", "ESG", "Reputational"],
      controls: [
        { name: "RMAP smelter coverage check",       ce: "STRONG" },
        { name: "Supplier attestation refresh",      ce: "STRONG" },
      ],
      audits: ["Conflict-minerals procedure"],
      cadence: ["T+90d"],
      summary: "RMAP coverage at 96%; remaining suppliers in cure period. No reportable issue currently.",
    },
  };

  // --------------------------------------------------------
  // Grey Swan — a foreseeable cascade where a currently-green
  // risk escalates to high risk over 90 days. Timeline at
  // 0 / 30 / 60 / 90 days with impact + likelihood at each step.
  // --------------------------------------------------------
  const greySwan = {
    id: "grey-swan",
    name: "Grey Swan — Macro / PMI shock cascade",
    risk_id: "R-09",
    risk_name: "Macro — PMI / IPG3344S Correlation",
    starting_rag: "G",
    starting_score: 4.6,
    ending_rag: "R",
    ending_score: 7.9,
    probability: "LOW · plausible",
    headline: "A green-band macro signal becomes a material risk inside one quarter",
    description: "Foreseeable but under-weighted: a coordinated reading collapse in Phila Fed Semi Index + ISM PMI, USD strengthening past 124 DXY, and a customer destock at two top-10 distributors. Each leg is independently within historical tolerances; the joint distribution sits outside it.",
    catalysts: [
      "Phila Fed Semi Index (IPG3344S) prints below −5 for two consecutive months",
      "ISM Manufacturing PMI falls under 47, breaking 18-month support",
      "Top-2 distributor weeks-of-inventory crosses 14w (vs 10w policy ceiling)",
      "USD broad index rallies past 124 (peer hedges roll off in Q2)",
    ],
    impacts_at_max: [
      "Revenue Q1 −9 to −12% vs guide",
      "Channel WoH risks NRV write-down trigger",
      "M-Score TATA driver re-rates AMBER → RED",
      "Three covenant ratios within 50 bps of trip",
    ],
    timeline: [
      {
        t: "T0",   label: "Day 0",   score: 4.6, rag: "G",
        impact: "—",   impact_$m: 0,
        likelihood: 0.08,
        signals: ["Single FRED series weakens by 1σ", "No customer escalations", "All KRIs within band"],
        action: "Monitor; no audit action triggered.",
      },
      {
        t: "T30",  label: "Day 30",  score: 5.7, rag: "A",
        impact: "Watch", impact_$m: 35,
        likelihood: 0.18,
        signals: ["2 FRED series breach 1σ — co-movement detected", "1 distributor flags WoH drift", "Velocity recalculates from +2 → +4"],
        action: "Escalate from continuous-monitor to scoped Phase-2 review. Inform CRO.",
      },
      {
        t: "T60",  label: "Day 60",  score: 6.9, rag: "A",
        impact: "Material", impact_$m: 120,
        likelihood: 0.38,
        signals: ["PMI prints sub-47", "Channel WoH 12.4w at 2 distributors", "USD breaks 122", "Peer (TXN) warns in 8-K"],
        action: "Open MAP. Add revenue cut-off and NRV write-down to audit scope. CFO + Audit Committee brief.",
      },
      {
        t: "T90",  label: "Day 90",  score: 7.9, rag: "R",
        impact: "Severe",   impact_$m: 280,
        likelihood: 0.62,
        signals: ["Joint distribution — 4σ tail event historically", "Distributor returns spike", "M-Score crosses RED at −1.6"],
        action: "Risk appetite breached. Recommend re-forecast + covenant pre-emptive amendment dialogue.",
      },
    ],
    early_warnings: [
      "Two FRED correlates breaching 1σ together — single-series moves are noise, paired moves are signal.",
      "Weeks-of-inventory at top-2 distributors crossing 12w with no offsetting POS uptick.",
      "Peer earnings warnings — even a single 8-K from TXN/STM/MCHP front-runs the cascade by ~30 days.",
    ],
    mitigations: [
      "Hedge USD exposure beyond rolling 2Q at trigger T30.",
      "Pre-stage NRV memo template; CFO + auditor pre-aligned on cut-off treatment by T60.",
      "Lock down channel-stuffing controls and tighten ECCN review concurrently — the two MAPs share evidence.",
    ],
  };

  // --------------------------------------------------------
  // PERSONA REPORT BLURBS (mocked Phase-4 outputs)
  // --------------------------------------------------------
  const personas = {
    "Internal Audit": {
      headline: "Top audit priorities: Revenue cut-off & BIS export controls",
      summary: "M-Score ELEVATED (−1.62, TATA driver). DSO +8 days QoQ, channel WoH +2.4 weeks. BIS rule change Oct-24 expands ECCN universe. Recommend Q1 sample expansion on revenue cut-off and full ECCN classification refresh.",
      sections: ["KRI Matrix", "All variable breakdowns", "Confidence flags"],
    },
    "Board / Audit Committee": {
      headline: "5 risks to watch — 2 require Board decision",
      summary: "Channel-stuffing risk and China BIS exposure are the two material items. Management has MAPs in flight (35% and 60% complete respectively). No going-concern signal. Auditor unqualified.",
      sections: ["Top 5 RAG", "What management must do", "What the Board must decide"],
    },
    "CFO / Treasury": {
      headline: "$290M revenue at risk in Base case; covenant headroom intact",
      summary: "Liquidity sufficient through 11 quarters in Base; Bear case compresses to 6 quarters and constrains liquidity. EBITDA stress −350 bps in Bear.",
      sections: ["P&L impact", "Liquidity stress", "Covenant headroom"],
    },
    "CRO / ERM": {
      headline: "Risk velocity exceeds appetite — 3 risks above threshold",
      summary: "R-01, R-02, R-03 above the velocity-2.5 threshold; control gap flags on RC-402 and TC-110. Risk appetite breach pending Board review.",
      sections: ["Inherent / control / residual", "Velocity indicators", "Appetite breach status"],
    },
  };

  // ── ON profile (default) ─────────────────────────────────
  const profileON = { entity, signals, risks, objectives, maps, closure, loop,
    eventTemplates, forecasts, scenarios, greySwan, riskFlow, personas,
    fred: forecasts.fred };

  // ── Ford Motor Company (F) profile ───────────────────────
  const profileF = (function () {
    const entity = {
      name: "Ford Motor Company",
      ticker: "F",
      industry: "Automotive OEM",
      focus: "EV Program Execution",
      period: "Q1 2022 — Q4 2024",
      periodBegin: "Q1 2025",
      periodEnd: "Q4 2025",
      auditor: "PricewaterhouseCoopers",
      auditor_opinion: "Unqualified",
      fy_close: "2024-12-31",
      peers: ["General Motors", "Stellantis", "Toyota"],
      distributors: ["AutoNation", "Penske Automotive", "Lithia Motors"],
      customers: ["Fleet buyers", "U.S. Government", "Commercial fleet operators"],
    };

    const signals = [
      { src: "EDGAR 10-K",   label: "F FY24 10-K filed — Model e $5.1B operating loss disclosed",    delta: "+2 new risk factors", velocity: 3, cat: "Filing" },
      { src: "EDGAR 10-K",   label: "Ford Credit DQ30+ 3.2% — up 0.4pp vs FY23",                     delta: "credit quality flag",  velocity: 2, cat: "Filing" },
      { src: "Peer 10-K",    label: "GM — EV program restructuring; Cruise write-off $0.9B",          delta: "+1 sector signal",    velocity: 2, cat: "Peer" },
      { src: "Peer 10-K",    label: "Stellantis — labor cost guidance raised 8% post-CBA",            delta: "cost pressure",        velocity: 1, cat: "Peer" },
      { src: "Industry RSS", label: "NHTSA opens transmission recall investigation — F-150",           delta: "+4 articles 7d",      velocity: 3, cat: "Safety", affectedRisks: ["R-06"] },
      { src: "Industry RSS", label: "BEV retail demand softening — inventory days 82 vs 45 target",   delta: "+5 articles 7d",      velocity: 3, cat: "EV",    affectedRisks: ["R-01"] },
      { src: "Internal KRI", label: "Blue Oval City production attainment 87% vs. plan",               delta: "-13 pts vs target",   velocity: 3, cat: "KRI" },
      { src: "Internal KRI", label: "Ford Credit net charge-offs 0.41% — 3Q trend rising",            delta: "+0.12pp QoQ",         velocity: 2, cat: "KRI" },
      { src: "Internal KRI", label: "China JV revenue contribution down 18% YoY",                     delta: "tariff impact",        velocity: 2, cat: "KRI" },
      { src: "FRED Macro",   label: "UMCSENT consumer sentiment 64.7 — vehicle-purchase intent weak", delta: "contractionary",       velocity: 2, cat: "Macro" },
      { src: "FRED Macro",   label: "TOTALSA light vehicle SAAR 15.4M — below 16M threshold",         delta: "contractionary",       velocity: 2, cat: "Macro" },
      { src: "Incident",     label: "F-150 transmission investigation — est. recall exposure $340M",   delta: "legal flag",           velocity: 3, cat: "Safety", affectedRisks: ["R-06"] },
    ];

    const risks = [
      { id: "R-01", name: "EV Program — Cost & Schedule Overrun",        category: "Operational",          score: 7.9, rag: "R", velocity: 3,  ce: "WEAK",     inherent: 9.0, residual: 7.9, peer: "above",   hist: [5.2, 5.8, 6.2, 6.8, 7.4, 7.9], narrative: "Model e operating loss $5.1B FY24; Blue Oval City production attainment 87%; cost-per-vehicle $12K above target. BlueOvalSK JV timeline slipped one quarter." },
      { id: "R-02", name: "Ford Credit — Delinquency & Reserve Adequacy", category: "Financial Reporting", score: 7.3, rag: "R", velocity: 2,  ce: "ADEQUATE", inherent: 8.2, residual: 7.3, peer: "above",   hist: [5.5, 5.8, 6.1, 6.5, 6.9, 7.3], narrative: "DQ30+ 3.2% (+0.4pp YoY); net charge-offs 0.41%, trending toward 3-year high. Reserve methodology relies on pre-2020 vintage loss curves not reflective of rate environment." },
      { id: "R-03", name: "UAW Labor Cost — CBA Absorption",              category: "Operational",          score: 6.9, rag: "A", velocity: 1,  ce: "ADEQUATE", inherent: 7.6, residual: 6.9, peer: "in-line", hist: [5.0, 5.4, 5.7, 6.1, 6.5, 6.9], narrative: "$9.4B incremental CBA cost over 4.5 years; productivity offset running 60% of target. Inflation escalators in years 3-4 may outpace assumed efficiency gains." },
      { id: "R-04", name: "China JV — Tariff & Revenue Exposure",         category: "Trade Compliance",     score: 6.6, rag: "A", velocity: 3,  ce: "WEAK",     inherent: 7.8, residual: 6.6, peer: "above",   hist: [4.8, 5.0, 5.4, 5.9, 6.2, 6.6], narrative: "Changan Ford JV revenue down 18% YoY. 100% tariff on U.S.-built EV imports to China effective Q2. JV intercompany transactions lack quarterly IA visibility." },
      { id: "R-05", name: "Battery Supply — Lithium Price & JV Ramp",     category: "Supply",               score: 6.2, rag: "A", velocity: 2,  ce: "ADEQUATE", inherent: 7.0, residual: 6.2, peer: "in-line", hist: [4.4, 4.7, 5.1, 5.6, 5.9, 6.2], narrative: "Battery-grade lithium spot +38% YoY; LGES BlueOvalSK production timeline slipped Q1. LFP vs NMC architecture decision pending; sunk cost risk $1.4B." },
      { id: "R-06", name: "Product Recall — Safety Defect Liability",     category: "Legal",                score: 5.9, rag: "A", velocity: 2,  ce: "ADEQUATE", inherent: 6.8, residual: 5.9, peer: "in-line", hist: [4.8, 5.0, 5.2, 5.5, 5.7, 5.9], narrative: "NHTSA investigation open on F-150 transmission — estimated exposure $340M. Historical 4-year average recall cost $680M. Accrual methodology relies on pre-NHTSA investigation estimates." },
      { id: "R-07", name: "Cybersecurity — Connected Vehicle Platform",   category: "Cybersecurity",        score: 5.5, rag: "A", velocity: 2,  ce: "ADEQUATE", inherent: 7.0, residual: 5.5, peer: "in-line", hist: [4.2, 4.5, 4.7, 5.0, 5.2, 5.5], narrative: "SYNC 4 and FordPass API surface area expanded with 1.3M new connected vehicles. OTA signing key rotation schedule not formally documented. NIST AISIC framework adopted but penetration test cadence below peer standard." },
      { id: "R-08", name: "Pension / OPEB — Actuarial Risk",              category: "Financial Reporting",  score: 5.1, rag: "A", velocity: -1, ce: "STRONG",   inherent: 6.5, residual: 5.1, peer: "below",   hist: [5.8, 5.7, 5.6, 5.4, 5.3, 5.1], narrative: "$37B gross pension obligation; 98% funded at 5.5% discount rate. Mortality table last updated 2021. Rising rates have improved funded status; risk is mean-reversion on rates." },
      { id: "R-09", name: "ICE Phase-out — Regulatory Transition Risk",   category: "ESG",                  score: 4.8, rag: "A", velocity: 1,  ce: "ADEQUATE", inherent: 6.2, residual: 4.8, peer: "in-line", hist: [3.8, 4.0, 4.2, 4.4, 4.6, 4.8], narrative: "EU 2035 ICE ban; California ZEV 68% by 2030; stranded ICE asset exposure estimated $2.1B. Transition plan published but IA has not independently validated milestones." },
      { id: "R-10", name: "Semiconductor Supply — Single-Source Risk",    category: "Supply",               score: 4.1, rag: "G", velocity: 0,  ce: "STRONG",   inherent: 5.5, residual: 4.1, peer: "below",   hist: [4.4, 4.3, 4.3, 4.2, 4.1, 4.1], narrative: "22 single-source semiconductor components across F-150 and Explorer platforms. Dual-sourcing program 68% complete; buffer stock strategy holds 6-week coverage on critical chips." },
    ];

    const objectives = [
      { id: "O-01", priority: "P1", objective: "EV program unit-economics audit — Blue Oval City cost-per-vehicle vs. milestones",
        linked_risk: "R-01", sprint: 1, hours: 140,
        controls: ["EV-101 Program cost gate", "EV-102 Production attainment reporting", "EV-105 BlueOvalSK JV milestone review"],
        rationale: "Velocity +3 for 3 consecutive quarters; $5.1B operating loss; cost-per-vehicle $12K above target; production attainment 87%." },
      { id: "O-02", priority: "P1", objective: "Ford Credit reserve adequacy — vintage loss curve and macro overlay review",
        linked_risk: "R-02", sprint: 1, hours: 100,
        controls: ["FC-201 Reserve methodology review", "FC-205 DQ vintage analysis", "FC-210 NCO trend dashboard"],
        rationale: "Reserve relies on pre-rate-hike vintage; DQ30+ 3.2% and trending; net charge-offs at 3-year high." },
      { id: "O-03", priority: "P2", objective: "China JV revenue recognition and tariff exposure mapping",
        linked_risk: "R-04", sprint: 2, hours: 80,
        controls: ["TC-301 JV intercompany documentation", "TC-305 Tariff classification review"],
        rationale: "Velocity +3; JV revenue down 18%; intercompany IA visibility gap; 100% EV import tariff in effect." },
      { id: "O-04", priority: "P2", objective: "Connected vehicle cybersecurity — OTA signing controls and API penetration test",
        linked_risk: "R-07", sprint: 2, hours: 60,
        controls: ["ITGC-401 OTA key rotation", "ITGC-405 FordPass API security", "ITGC-410 V2X threat model"],
        rationale: "OTA key rotation undocumented; 1.3M new connected vehicles added surface area; peer OEM breach precedent." },
      { id: "O-05", priority: "P3", objective: "Pension actuarial assumption review — mortality table and discount rate sensitivity",
        linked_risk: "R-08", sprint: 3, hours: 40,
        controls: ["PEN-501 Actuarial assumption sign-off", "PEN-503 Sensitivity disclosure"],
        rationale: "Mortality table 3 cycles out of date; rate sensitivity undisclosed; funded status vulnerable to 100bps rate move." },
      { id: "O-06", priority: "P3", objective: "Semiconductor single-source concentration — escalation protocol and buffer coverage",
        linked_risk: "R-10", sprint: 3, hours: 28,
        controls: ["SC-601 Single-source registry", "SC-605 Buffer stock protocol"],
        rationale: "Dual-sourcing 68% complete; 22 single-source components; escalation protocol not formalized." },
    ];

    const maps = [
      { id: "MAP-01", finding: "EV cost-per-vehicle variance not captured in consolidated IA dashboard",
        condition: "$12K cost-per-vehicle gap vs. target not surfaced in audit committee EV tracker; FP&A and IA use separate data feeds.",
        root_cause: "EV program reporting system launched Q3-24 without IA data integration; GRC platform not connected to program cost ledger.",
        risk_impact: "R", linked_risk: "R-01",
        action: "Integrate Blue Oval City cost tracker into GRC dashboard; auto-flag cost-per-vehicle variance >$5K; include in quarterly AC pack.",
        owner: "VP Internal Audit / VP EV Program", due_date: "2025-Q2", completion_pct: 20,
        success_criteria: "IA dashboard shows weekly cost-per-vehicle actuals vs. target; AC receives monthly variance bridge.",
        reduction_pct: 24 },
      { id: "MAP-02", finding: "Ford Credit loss reserve uses pre-2020 vintage loss curves not calibrated for rate environment",
        condition: "Reserve model trained on 2015–2019 data; DQ and NCO rates have structurally shifted with Fed funds rate at 5.5%.",
        root_cause: "Model refresh cycle is 5-year; no interim override procedure triggered despite material macro shift.",
        risk_impact: "R", linked_risk: "R-02",
        action: "Recalibrate vintage loss model to include 2022–2024 vintages; implement annual macro-overlay review; stress-test reserve at +150bps.",
        owner: "Ford Credit Chief Risk Officer", due_date: "2025-Q1", completion_pct: 55,
        success_criteria: "Updated model approved by Audit Committee; reserve recalculated using refreshed curves; stress scenario disclosed in 10-Q.",
        reduction_pct: 20 },
      { id: "MAP-03", finding: "Changan Ford JV intercompany transactions lack formal IA review cadence",
        condition: "$2.1B intercompany transactions in FY24 reviewed by external audit only; no IA touchpoint between annual visits.",
        root_cause: "JV oversight framework last updated 2018; IA charter does not include quarterly JV monitoring.",
        risk_impact: "A", linked_risk: "R-04",
        action: "Add semi-annual IA review of JV intercompany ledger; update charter to include tariff classification sign-off for China shipments.",
        owner: "Director Internal Audit — International", due_date: "2025-Q3", completion_pct: 10,
        success_criteria: "Two semi-annual JV reviews completed; findings reported to AC; tariff classification sign-off rate 100%.",
        reduction_pct: 16 },
      { id: "MAP-04", finding: "OTA firmware signing key rotation schedule not formally documented or IA-verified",
        condition: "SYNC 4 OTA signing keys have no documented rotation schedule; last rotation was 19 months ago with no IA sign-off.",
        root_cause: "OTA program stood up under engineering control without cybersecurity governance framework integration.",
        risk_impact: "A", linked_risk: "R-07",
        action: "Document OTA key rotation policy (quarterly maximum); integrate with cybersecurity governance; IA to verify next rotation.",
        owner: "CISO — Connected Vehicle", due_date: "2025-Q2", completion_pct: 35,
        success_criteria: "Policy documented and approved; Q2 rotation verified by IA; penetration test against updated OTA stack completed.",
        reduction_pct: 14 },
      { id: "MAP-05", finding: "Pension mortality table 3 actuarial cycles out of date",
        condition: "RP-2014 table in use; RP-2021 released and adopted by 87% of FTSE 500 peer set. Estimated underfunding impact $0.8–1.2B.",
        root_cause: "Actuarial update deferred annually pending internal review — no formal trigger for mandatory refresh.",
        risk_impact: "A", linked_risk: "R-08",
        action: "Adopt RP-2021 mortality table for FY25 valuation; disclose impact; add mandatory 3-year refresh trigger to actuarial policy.",
        owner: "CFO / Treasurer", due_date: "2025-Q4", completion_pct: 5,
        success_criteria: "FY25 pension valuation uses RP-2021; impact disclosed in 10-K; policy updated with refresh trigger.",
        reduction_pct: 10 },
      { id: "MAP-06", finding: "Single-source semiconductor escalation protocol not formalized",
        condition: "22 single-source chips across F-150 and Explorer; escalation threshold, trigger, and owner not documented in supplier risk register.",
        root_cause: "Dual-sourcing program managed by procurement; IA register not linked to procurement system.",
        risk_impact: "G", linked_risk: "R-10",
        action: "Link procurement single-source registry to IA supplier risk register; define escalation triggers (supply warning, lead-time >12w); designate risk owner.",
        owner: "Chief Procurement Officer", due_date: "2025-Q3", completion_pct: 30,
        success_criteria: "All 22 single-source chips in IA register with trigger thresholds; escalation tested via tabletop exercise.",
        reduction_pct: 7 },
    ];

    const closure = {
      risks_closed: 1,
      risks_reduced: 5,
      risks_unchanged: 4,
      projected_total_risk_reduction_pct: 21,
      evidence_artifacts: 52,
      rerun_recommended: ["R-01", "R-02"],
    };

    const loop = {
      loop_health: "A",
      audit_impact_score: 7.1,
      risk_reduction_pct: 21,
      maps_open: 6,
      risks_closed: 1,
      next_trigger_days: 35,
      next_cycle_focus: "EV cost-per-vehicle re-test vs. Q1 actuals; Ford Credit reserve model validation; China JV semi-annual review.",
      lessons_learned: [
        "EV program cost data not in IA reporting path — GRC integration gap identified 2 cycles late.",
        "Ford Credit reserve model staleness not flagged by existing controls — add model-vintage monitoring.",
        "China JV IA gap: annual-only coverage insufficient given tariff environment; semi-annual cadence required.",
      ],
      velocity_threshold_recommendation: 2.5,
    };

    const fred = [
      { id: "UMCSENT",   name: "U-Mich Consumer Sentiment", r: 0.84,  lead: 2, dir: "CONTRACTIONARY", reading: 64.7 },
      { id: "TOTALSA",   name: "Light Vehicle SAAR",         r: 0.91,  lead: 1, dir: "CONTRACTIONARY", reading: 15.4 },
      { id: "MANEMP",    name: "ISM Mfg PMI",                r: 0.74,  lead: 1, dir: "CONTRACTIONARY", reading: 48.3 },
      { id: "DFF",       name: "Federal Funds Rate",         r: -0.71, lead: 3, dir: "NEUTRAL",         reading: 4.50 },
      { id: "WTISPLC",   name: "WTI Crude Oil ($/bbl)",      r: -0.52, lead: 2, dir: "NEUTRAL",         reading: 72.1 },
    ];

    const forecasts = {
      revenue: {
        history: [
          { q: "Q1-22", v: 34476 }, { q: "Q2-22", v: 37937 }, { q: "Q3-22", v: 37240 }, { q: "Q4-22", v: 44216 },
          { q: "Q1-23", v: 41470 }, { q: "Q2-23", v: 44954 }, { q: "Q3-23", v: 43788 }, { q: "Q4-23", v: 43170 },
          { q: "Q1-24", v: 42775 }, { q: "Q2-24", v: 47777 }, { q: "Q3-24", v: 46178 }, { q: "Q4-24", v: 44839 },
        ],
        forecast: [
          { q: "Q1-25", base: 41200, lo: 38900, hi: 43500 },
          { q: "Q2-25", base: 44500, lo: 41200, hi: 47800 },
          { q: "Q3-25", base: 45800, lo: 42100, hi: 49500 },
          { q: "Q4-25", base: 46200, lo: 42600, hi: 49800 },
        ],
      },
      margin: {
        history: [
          { q: "Q1-22", v: 10.2 }, { q: "Q2-22", v: 10.8 }, { q: "Q3-22", v: 9.4 },  { q: "Q4-22", v: 11.6 },
          { q: "Q1-23", v: 11.4 }, { q: "Q2-23", v: 12.1 }, { q: "Q3-23", v: 11.8 }, { q: "Q4-23", v: 10.9 },
          { q: "Q1-24", v: 10.5 }, { q: "Q2-24", v: 11.2 }, { q: "Q3-24", v: 10.8 }, { q: "Q4-24", v: 9.9 },
        ],
        forecast: [
          { q: "Q1-25", base: 9.4,  lo: 7.8,  hi: 11.0 },
          { q: "Q2-25", base: 10.1, lo: 8.2,  hi: 12.0 },
          { q: "Q3-25", base: 10.8, lo: 8.6,  hi: 13.0 },
          { q: "Q4-25", base: 11.2, lo: 8.9,  hi: 13.5 },
        ],
      },
      fred,
      sentiment: {
        score: -8,
        trend: "DETERIORATING",
        hedge_ratio_trend: "+11pp over 4Q",
        latest_quarter: "Q1-25",
        quarterly: [
          { q: "Q3-23", score: 4,   hedge: 0.06 },
          { q: "Q4-23", score: 2,   hedge: 0.07 },
          { q: "Q1-24", score: -2,  hedge: 0.09 },
          { q: "Q2-24", score: -4,  hedge: 0.11 },
          { q: "Q3-24", score: -6,  hedge: 0.13 },
          { q: "Q4-24", score: -8,  hedge: 0.15 },
          { q: "Q1-25", score: -10, hedge: 0.17 },
        ],
      },
      mscore: {
        m: -2.04,
        band: "ELEVATED",
        key_driver: "DSRI (receivables quality)",
        thresholds: { red: -1.78, amber: -2.22 },
        vars: {
          DSRI: 1.24, GMI: 1.09, AQI: 1.03, SGI: 0.98, DEPI: 1.00, SGAI: 1.05, LVGI: 1.08, TATA: 0.028,
        },
      },
    };

    const scenarios = [
      { id: "bear", name: "Bear — EV demand collapse + Ford Credit stress",
        description: "BEV retail demand falls 30% below plan as incentive wars intensify; Ford Credit NCOs breach 0.65%; additional $2.8B EV write-down required.",
        probability: "MEDIUM",
        revenue_impact_pct: -14,
        gross_margin_impact_bps: -420,
        revenue_at_risk_m: 25800,
        runway_qtrs: 5,
        liquidity: "CONSTRAINED",
        kris_red: ["R-01", "R-02", "R-04"],
        recovery: "PROLONGED_5Q_PLUS",
        audit_focus: ["EV cost write-down adequacy", "Ford Credit reserve stress", "Covenant compliance"],
        vs_peers: "Most exposed: Ford. GM better positioned via Cruise separation. Toyota unaffected.",
        assumptions: { bev_demand_delta: "−30%", ford_credit_nco: "0.65%", ev_write_down: "$2.8B", fed_funds: "5.5%" } },
      { id: "base", name: "Base — Managed EV losses; Ford Pro drives profitability",
        description: "Model e losses narrow to $4.5B; Ford Pro commercial division sustains 10%+ EBIT margin; Ford Credit stable.",
        probability: "HIGH",
        revenue_impact_pct: -3,
        gross_margin_impact_bps: -80,
        revenue_at_risk_m: 5200,
        runway_qtrs: 10,
        liquidity: "SUFFICIENT",
        kris_red: ["R-01"],
        recovery: "MODERATE_3_4Q",
        audit_focus: ["EV cost trajectory", "Ford Pro margin sustainability", "Ford Credit DQ trend"],
        vs_peers: "Ford in-line with sector on auto; Ford Pro outperforming GM commercial",
        assumptions: { model_e_loss: "$4.5B", ford_pro_ebit: "10.5%", ford_credit_nco: "0.42%", china_revenue_delta: "−12%" } },
      { id: "bull", name: "Bull — EV cost inflection + Ford Pro acceleration",
        description: "Blue Oval City reaches cost parity ahead of plan; Ford Pro fleet wins accelerate; China JV stabilizes under new EV models.",
        probability: "LOW",
        revenue_impact_pct: 6,
        gross_margin_impact_bps: 180,
        revenue_at_risk_m: 0,
        runway_qtrs: 14,
        liquidity: "COMFORTABLE",
        kris_red: [],
        recovery: "NONE",
        audit_focus: ["EV ramp controls", "Revenue recognition on fleet orders"],
        vs_peers: "Ford outperforms GM and Stellantis on EV margin trajectory",
        assumptions: { model_e_loss: "$3.2B", ford_pro_ebit: "12%", blue_oval_city_attainment: "96%", china_revenue_delta: "0%" } },
    ];

    const greySwan = {
      id: "grey-swan-f",
      name: "Grey Swan — Ford Credit liquidity stress cascade",
      risk_id: "R-02",
      risk_name: "Ford Credit — Delinquency & Reserve Adequacy",
      starting_rag: "A",
      starting_score: 7.3,
      ending_rag: "R",
      ending_score: 9.1,
      probability: "LOW · plausible",
      headline: "Ford Credit reserve shortfall triggers covenant breach and rating downgrade",
      description: "Foreseeable but under-weighted: NCO rate breaches 0.65%, reserve is inadequate by $1.4B, triggers covenant on $6.2B Ford Credit revolver, prompting Moody's to cut Ford Credit to Ba1. Loss of investment-grade status raises Ford's own funding costs by 180bps — a cascade that begins with a consumer credit metric.",
      catalysts: [
        "Net charge-off rate prints 0.65%+ for two consecutive quarters",
        "Reserve model fails SEC comment letter review — restatement risk",
        "Revolver covenant (max NCO 0.70%) approaches breach — lender waiver required",
        "Moody's places Ford Credit on negative watch",
      ],
      impacts_at_max: [
        "Ford Credit funding cost +180bps — $1.1B annual P&L impact",
        "Ford parent credit facility covenants triggered — $3B liquidity at risk",
        "EV program capital raises more expensive — timeline extension risk",
      ],
      early_warnings: [
        "DQ30+ breaches 3.5% for two consecutive months",
        "NCO rate reaches 0.55% — 1σ above 3-year mean",
        "Reserve model sensitivity to +150bps rate scenario shows >$800M gap",
        "Ford Credit unsecured spreads widen 40bps vs. sector",
      ],
      mitigations: [
        "Immediately recalibrate reserve model with 2022–2024 vintage data",
        "Obtain Board pre-approval for reserve top-up before external trigger",
        "Engage proactively with rating agencies on reserve methodology changes",
        "Negotiate covenant headroom with lead lenders before breach threshold reached",
      ],
      timeline: [
        { t: "T+0",  label: "Early signal",    score: 7.3, rag: "A", impact: "DQ30+ 3.2%, NCO rising — reserve model review initiated", signals: ["NCO 0.41%", "DQ30+ 3.2%", "Macro overlay pending"], action: "IA initiates reserve methodology review" },
        { t: "T+30", label: "Stress building", score: 8.1, rag: "R", impact: "NCO 0.54%; reserve gap estimated $0.6B — SEC comment letter risk", signals: ["NCO 0.54%", "DQ30+ 3.7%", "Lender monitoring activated"], action: "Emergency reserve model refresh; Board notified" },
        { t: "T+60", label: "Cascade risk",    score: 8.8, rag: "R", impact: "Covenant breach imminent; Moody's on negative watch", signals: ["NCO 0.62%", "Covenant 94% utilized", "Rating watch negative"], action: "Lender waiver negotiation; capital raise consideration" },
        { t: "T+90", label: "Systemic",        score: 9.1, rag: "R", impact: "Rating downgrade — Ford Credit Ba1; parent funding cost +180bps", signals: ["Moody's Ba1", "Spreads +180bps", "EV capex re-scoped"], action: "Structured reserve cure plan; investor communication" },
      ],
    };

    const riskFlow = {
      "R-01": {
        impacts: ["Operational", "Financial Reporting", "Reputational", "ESG"],
        controls: [
          { name: "EV program cost gate",                ce: "WEAK" },
          { name: "BlueOvalSK JV milestone review",      ce: "WEAK" },
          { name: "Production attainment dashboard",     ce: "ADEQUATE" },
        ],
        audits: ["MAP-01", "EV program cost audit", "JV milestone walkthrough"],
        cadence: ["T+7d", "T+21d", "T+42d", "T+60d", "T+80d"],
        summary: "Velocity +3; $5.1B operating loss; 87% production attainment. Cost-per-vehicle $12K above target. Highest-priority audit item.",
      },
      "R-02": {
        impacts: ["Financial Reporting", "Liquidity", "Credit Rating", "Investor Relations"],
        controls: [
          { name: "Ford Credit reserve methodology",    ce: "WEAK" },
          { name: "DQ vintage analysis",                ce: "ADEQUATE" },
          { name: "NCO trend dashboard",                ce: "ADEQUATE" },
        ],
        audits: ["MAP-02", "Reserve adequacy review", "Stress test walkthrough"],
        cadence: ["T+14d", "T+30d", "T+55d", "T+75d"],
        summary: "Reserve relies on pre-rate-hike vintages. NCO 0.41% and rising. Grey swan cascade path identified.",
      },
      "R-03": {
        impacts: ["Operational", "Financial Reporting"],
        controls: [
          { name: "CBA cost absorption tracking",       ce: "ADEQUATE" },
          { name: "Productivity offset monitoring",     ce: "ADEQUATE" },
        ],
        audits: ["Labor cost variance review", "Productivity offset audit"],
        cadence: ["T+45d", "T+90d"],
        summary: "CBA cost structure locked in; productivity offset at 60% of target. Risk is slow-burn over 4.5 years.",
      },
      "R-04": {
        impacts: ["Trade Compliance", "Financial Reporting", "Revenue Recognition"],
        controls: [
          { name: "JV intercompany documentation",      ce: "WEAK" },
          { name: "Tariff classification review",       ce: "WEAK" },
        ],
        audits: ["MAP-03", "China JV semi-annual review", "Tariff classification walkthrough"],
        cadence: ["T+28d", "T+60d", "T+90d"],
        summary: "JV revenue down 18%; 100% EV import tariff in effect. IA coverage annual-only — gap identified.",
      },
      "R-05": {
        impacts: ["Supply Chain", "Financial Reporting", "ESG"],
        controls: [
          { name: "Battery supply contract review",     ce: "ADEQUATE" },
          { name: "LFP/NMC architecture decision gate", ce: "ADEQUATE" },
        ],
        audits: ["Battery supply audit", "JV commitment review"],
        cadence: ["T+30d", "T+70d"],
        summary: "Lithium +38%; JV slipped one quarter. Architecture decision pending with $1.4B sunk cost.",
      },
      "R-06": {
        impacts: ["Legal", "Financial Reporting", "Reputational"],
        controls: [
          { name: "NHTSA investigation tracker",        ce: "ADEQUATE" },
          { name: "Recall accrual methodology",         ce: "ADEQUATE" },
        ],
        audits: ["MAP-04", "Recall accrual review", "Legal-letter procedure"],
        cadence: ["T+21d", "T+50d", "T+80d"],
        summary: "NHTSA investigation open; $340M estimated exposure. Accrual pre-investigation — may need uplift.",
      },
      "R-07": {
        impacts: ["Cybersecurity", "Reputational", "Legal"],
        controls: [
          { name: "OTA signing key management",         ce: "WEAK" },
          { name: "FordPass API security testing",      ce: "ADEQUATE" },
          { name: "V2X threat model",                   ce: "ADEQUATE" },
        ],
        audits: ["MAP-04", "OTA controls walkthrough", "API penetration test"],
        cadence: ["T+21d", "T+45d", "T+70d"],
        summary: "OTA key rotation undocumented; 1.3M new connected vehicles. Penetration test overdue.",
      },
      "R-08": {
        impacts: ["Financial Reporting", "Investor Relations"],
        controls: [
          { name: "Actuarial assumption review",        ce: "STRONG" },
          { name: "Pension sensitivity disclosure",     ce: "STRONG" },
        ],
        audits: ["MAP-05", "Actuarial table review"],
        cadence: ["T+90d"],
        summary: "Mortality table 3 cycles out of date. Rate sensitivity partially mitigating — risk is mean-reversion.",
      },
      "R-09": {
        impacts: ["ESG", "Reputational", "Operational"],
        controls: [
          { name: "ICE phase-out transition plan",      ce: "ADEQUATE" },
          { name: "Stranded asset mapping",             ce: "ADEQUATE" },
        ],
        audits: ["ESG transition plan walkthrough"],
        cadence: ["T+90d"],
        summary: "EU 2035 / CA ZEV mandates set; transition plan published but IA milestone validation pending.",
      },
      "R-10": {
        impacts: ["Supply Chain", "Operational"],
        controls: [
          { name: "Single-source semiconductor registry", ce: "STRONG" },
          { name: "6-week buffer stock protocol",        ce: "STRONG" },
        ],
        audits: ["MAP-06", "Supplier concentration review"],
        cadence: ["T+90d"],
        summary: "22 single-source chips; dual-sourcing 68% complete; 6-week buffer adequate for current lead times.",
      },
    };

    const personas = {
      "Chief Audit Executive": {
        sections: ["R-01 EV execution", "R-02 Ford Credit", "R-04 China JV"],
        summary: "Three RED risks require immediate remediation. EV program cost visibility gap is the highest-priority IA action — integrate cost tracker into GRC dashboard before Q2 AC meeting.",
        headline: "Audit impact score 7.1/10 — above average; EV and credit risks dominate.",
      },
      "Chief Financial Officer": {
        sections: ["R-02 Ford Credit reserve", "R-08 Pension OPEB", "R-03 CBA cost"],
        summary: "Ford Credit reserve model needs immediate recalibration — pre-rate-hike vintages materially understate expected loss. Pension mortality table refresh required for FY25 valuation.",
        headline: "Two financial reporting risks with potential restatement exposure.",
      },
      "Chief Operating Officer": {
        sections: ["R-01 EV program", "R-03 UAW labor", "R-10 Semi supply"],
        summary: "Blue Oval City production attainment 87% vs. plan — weekly cost-per-vehicle dashboard required. UAW productivity offset tracking needs acceleration; currently 60% of target.",
        headline: "EV ramp and labor cost absorption are the operational priorities for Q1–Q2.",
      },
    };

    return { entity, signals, risks, objectives, maps, closure, loop,
      eventTemplates: profileON.eventTemplates,
      forecasts, scenarios, greySwan, riskFlow, personas, fred };
  })();

  // ── Profile registry ──────────────────────────────────────
  const profiles = { ON: profileON, F: profileF };

  function getProfile(ticker) {
    return profiles[(ticker || "ON").toUpperCase()] || profileON;
  }

  return { entity, signals, risks, objectives, maps, closure, loop, eventTemplates, forecasts, scenarios, greySwan, riskFlow, personas, fred: forecasts.fred, getProfile };
})();
