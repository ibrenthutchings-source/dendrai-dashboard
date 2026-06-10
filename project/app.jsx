/* ============================================================
   Dendrai Risk Loop — main App
   Orchestrates: run-loop animation, HITL gates, CEM, modals,
   data mode (mock/live), tweaks.
   ============================================================ */

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  render() {
    if (this.state.err) {
      return (
        <div style={{padding: 40, fontFamily: "system-ui, sans-serif", color: "var(--ink, #111)"}}>
          <div style={{fontSize: 13, fontWeight: 600, marginBottom: 8}}>Something went wrong rendering this section.</div>
          <div style={{fontSize: 11, color: "var(--ink-3, #888)", marginBottom: 16, fontFamily: "monospace"}}>
            {this.state.err?.message || "Unknown error"}
          </div>
          <button style={{fontSize: 11, padding: "5px 14px", borderRadius: 6, border: "1px solid var(--line, #ddd)", cursor: "pointer", background: "var(--surface, #fff)"}}
            onClick={() => this.setState({ err: null })}>
            Dismiss and retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const DEFAULT_TWEAKS = /*EDITMODE-BEGIN*/{
  "accent": "emerald",
  "density": "comfortable",
  "runSpeed": 1.0,
  "autoExpand": true,
  "persona": "Internal Audit"
} /*EDITMODE-END*/;

const APPETITE_THRESHOLDS = { GREEN: 5.0, AMBER: 7.5, RED: 9.5 };

function App() {
  // ---- Tweaks ----
  const [tweaks, setTweak] = useTweaks(DEFAULT_TWEAKS);

  // Apply accent + density at body level
  useEffect(() => {
    document.body.dataset.accent = tweaks.accent;
    document.body.dataset.density = tweaks.density;
  }, [tweaks.accent, tweaks.density]);

  // ---- Sidebar config ----
  const [cfg, setCfg] = useState({
    ticker: "ON",
    industry: "Semiconductors",
    focus: ["Revenue Recognition"],
    periodBegin: "Q1 2025",
    periodEnd: "Q4 2025",
    appetiteLevel: "AMBER",
  });
  const [signalSet, setSignalSet] = useState(new Set(["edgar", "peers", "industry", "internal", "fred", "incidents"]));
  const [velocity, setVelocity] = useState(3);
  const [hitl, setHitl] = useState({ risk: true, scope: true, map: false });

  // ---- Live mode + EDGAR/FRED data ----
  const [liveMode, setLiveMode] = useState(false);
  const [liveStatus, setLiveStatus] = useState("");
  const [livefacts, setLivefacts] = useState(null);
  const [fredLive, setFredLive] = useState(null);

  // ---- Pipeline run state ----
  const [running, setRunning] = useState(false);
  const [hasRun, setHasRun] = useState(false);
  const [stageState, setStageState] = useState({}); // id -> idle/running/done/waiting
  const [gateState, setGateState] = useState({ g1: null, g2: null }); // null / "pending" / "approved" / "overridden"
  const [output, setOutput] = useState({}); // per-stage payload
  const [loopLog, setLoopLog] = useState([]);
  const [openStages, setOpenStages] = useState(new Set(["s1"]));

  // Pending gate promise resolvers (so the run sequence can await user action)
  const gateResRef = useRef({});

  // ---- Tabs ----
  const [activeMainTab, setActiveMainTab] = useState("pipe"); // pipe | cem | flow
  const [activePipeTab, setActivePipeTab] = useState("stages"); // stages | rss | fcst | scen
  const [activeRailTab, setActiveRailTab] = useState("rr"); // rr | hm | map | loop | notif | fcst | pers
  const [activeQuarter, setActiveQuarter] = useState("Now");
  const [selectedRiskId, setSelectedRiskId] = useState(null);
  const [selectedPersona, setSelectedPersona] = useState(tweaks.persona);

  // Keep persona pick in sync with tweaks
  useEffect(() => {setSelectedPersona(tweaks.persona);}, [tweaks.persona]);

  // ---- RSS ingestion signals ----
  const [rssSignals, setRssSignals] = useState([]);
  const [rssLastUpdated, setRssLastUpdated] = useState(null);
  const [rssRefreshing, setRssRefreshing] = useState(false);
  const [perRiskAppetite, setPerRiskAppetite] = useState({});

  // Periodic RSS refresh while pipeline is running
  useEffect(() => {
    if (!running) return;
    const doRefresh = async () => {
      if (!signalSet.has("industry")) return;
      setRssRefreshing(true);
      try {
        const ingestResult = await RSS_ENGINE.ingestAll({ simulate: !liveMode });
        const freshSigs = RSS_ENGINE.toSignals(ingestResult);
        setRssSignals(freshSigs);
        setRssLastUpdated(Date.now());
      } catch(e) { /* silent */ }
      setRssRefreshing(false);
    };
    const interval = setInterval(doRefresh, 30000);
    return () => clearInterval(interval);
  }, [running, liveMode]);

  // ---- CEM state ----
  const [events, setEvents] = useState([]);
  const [cemFilter, setCemFilter] = useState("all");
  const [cemExpanded, setCemExpanded] = useState(new Set());
  const [notifLog, setNotifLog] = useState([]);
  const [unreadCEM, setUnreadCEM] = useState(0);

  // ---- Modals ----
  const [reportOpen, setReportOpen] = useState(false);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overrideGateNum, setOverrideGateNum] = useState(null);

  // ---- Per-risk approval state (Gate 1) ----
  // { [riskId]: { status: 'pending'|'approved'|'adjusted'|'signed', adjustments?, rationale?, signoffs? } }
  const [riskApprovals, setRiskApprovals] = useState({});
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [adjustingRiskId, setAdjustingRiskId] = useState(null);

  // ---- Per-objective scope approval state (Gate 2) ----
  // { [objId]: { status: 'pending'|'approved'|'adjusted'|'signed', adjustments?, rationale?, signoffs? } }
  const [scopeApprovals, setScopeApprovals] = useState({});
  const [adjustObjOpen, setAdjustObjOpen] = useState(false);
  const [adjustingObjId, setAdjustingObjId] = useState(null);

  const auditorName = (selectedPersona && selectedPersona !== "Internal Audit") ? selectedPersona : "Internal Audit";

  // ---- Logging helper ----
  const log = useCallback((msg) => {
    setLoopLog((prev) => [...prev, { ts: new Date().toISOString(), msg }]);
  }, []);

  // ---- Manual audit plan entries ----
  const [manualAudits, setManualAudits] = useState([]);
  const addManualAudit = useCallback((audit) => {
    setManualAudits(prev => [...prev, audit]);
    log(`Manual audit added: ${audit.title} · ${audit.when} · linked to ${audit.riskId}`);
  }, [log]);
  const removeManualAudit = useCallback((id) => {
    setManualAudits(prev => prev.filter(a => a.id !== id));
  }, []);

  // ---- Company profile — built from EDGAR + FRED + RISK_ENGINE during run ----
  const [profile, setProfile] = useState(() => RISK_ENGINE.buildProfile("ON", null, "3674", "Semiconductors"));
  const profileRef = useRef(profile);
  useEffect(() => { profileRef.current = profile; }, [profile]);

  // ---- HITL gates ----
  const showGate = (n) => new Promise((res) => {
    gateResRef.current[n] = res;
    setGateState((prev) => ({ ...prev, [`g${n}`]: "pending" }));
    if (n === 1) {
      const risksNow = (output.s2?.risks) || profileRef.current?.risks || [];
      const init = {};
      risksNow.forEach(r => { init[r.id] = { status: "pending" }; });
      setRiskApprovals(init);
      setPerRiskAppetite(prev => {
        const next = {};
        risksNow.forEach(r => { next[r.id] = prev[r.id] || cfg.appetiteLevel || "AMBER"; });
        return { ...prev, ...next };
      });
    }
    if (n === 2) {
      const objsNow = (output.s3?.objectives) || profileRef.current?.objectives || [];
      const init = {};
      objsNow.forEach(o => { init[o.id] = { status: "pending" }; });
      setScopeApprovals(init);
    }
  });

  // ---- Per-risk HITL handlers ----
  const approveRisk = useCallback((riskId) => {
    setRiskApprovals(prev => ({ ...prev, [riskId]: { ...(prev[riskId]||{}), status: "approved" } }));
    log(`Risk ${riskId}: APPROVED as scored by ${auditorName}`);
  }, [auditorName, log]);

  const openAdjustRisk = useCallback((riskId) => {
    setAdjustingRiskId(riskId);
    setAdjustOpen(true);
  }, []);

  const submitAdjustment = useCallback((payload) => {
    const id = adjustingRiskId;
    if (!id) return;
    setRiskApprovals(prev => ({
      ...prev,
      [id]: {
        status: "adjusted",
        adjustments: { rag: payload.rag, score: payload.score, velocity: payload.velocity, ce: payload.ce },
        rationale: payload.rationale,
        adjustedBy: auditorName,
        adjustedAt: Date.now(),
        signoffs: {}
      }
    }));
    log(`Risk ${id}: ADJUSTED by ${auditorName} — routed to CAE / CFO / Audit Committee`);
    setAdjustOpen(false);
    setAdjustingRiskId(null);
  }, [adjustingRiskId, auditorName, log]);

  const signoffRisk = useCallback((riskId, role) => {
    const SIG_MAP = { cae: "Sarah Lin (CAE)", cfo: "Marcus Reed (CFO)", ac: "J. Vance (Audit Committee)" };
    setRiskApprovals(prev => {
      const cur = prev[riskId];
      if (!cur) return prev;
      const newSigs = { ...(cur.signoffs||{}), [role]: { who: SIG_MAP[role], signedAt: Date.now() } };
      const allSigned = !!newSigs.cae && !!newSigs.cfo && !!newSigs.ac;
      return { ...prev, [riskId]: { ...cur, signoffs: newSigs, status: allSigned ? "signed" : "adjusted" } };
    });
    log(`Risk ${riskId}: ${role.toUpperCase()} sign-off captured (${SIG_MAP[role]})`);
  }, [log]);

  const approveAllRemainingRisks = useCallback(() => {
    setRiskApprovals(prev => {
      const next = { ...prev };
      Object.keys(next).forEach(id => {
        if (next[id].status === "pending") next[id] = { ...next[id], status: "approved" };
      });
      return next;
    });
    log(`Bulk-approve: all remaining pending risks accepted by ${auditorName}`);
  }, [auditorName, log]);

  // ---- Per-objective HITL handlers (Gate 2) ----
  const approveObjective = useCallback((objId) => {
    setScopeApprovals(prev => ({ ...prev, [objId]: { ...(prev[objId]||{}), status: "approved" } }));
    log(`Objective ${objId}: APPROVED as scoped by ${auditorName}`);
  }, [auditorName, log]);

  const openAdjustObjective = useCallback((objId) => {
    setAdjustingObjId(objId);
    setAdjustObjOpen(true);
  }, []);

  const submitObjAdjustment = useCallback((payload) => {
    const id = adjustingObjId;
    if (!id) return;
    setScopeApprovals(prev => ({
      ...prev,
      [id]: {
        status: "adjusted",
        adjustments: { priority: payload.priority, sprint: payload.sprint, hours: payload.hours },
        rationale: payload.rationale,
        adjustedBy: auditorName,
        adjustedAt: Date.now(),
        signoffs: {}
      }
    }));
    log(`Objective ${id}: ADJUSTED by ${auditorName} — routed to CAE / CFO / Audit Committee`);
    setAdjustObjOpen(false);
    setAdjustingObjId(null);
  }, [adjustingObjId, auditorName, log]);

  const signoffObjective = useCallback((objId, role) => {
    const SIG_MAP = { cae: "Sarah Lin (CAE)", cfo: "Marcus Reed (CFO)", ac: "J. Vance (Audit Committee)" };
    setScopeApprovals(prev => {
      const cur = prev[objId];
      if (!cur) return prev;
      const newSigs = { ...(cur.signoffs||{}), [role]: { who: SIG_MAP[role], signedAt: Date.now() } };
      const allSigned = !!newSigs.cae && !!newSigs.cfo && !!newSigs.ac;
      return { ...prev, [objId]: { ...cur, signoffs: newSigs, status: allSigned ? "signed" : "adjusted" } };
    });
    log(`Objective ${objId}: ${role.toUpperCase()} sign-off captured (${SIG_MAP[role]})`);
  }, [log]);

  const approveAllRemainingObjectives = useCallback(() => {
    setScopeApprovals(prev => {
      const next = { ...prev };
      Object.keys(next).forEach(id => {
        if (!next[id] || next[id].status === "pending") next[id] = { status: "approved" };
      });
      return next;
    });
    log(`Bulk-approve: all remaining objectives accepted by ${auditorName}`);
  }, [auditorName, log]);

  const addObjective = useCallback(() => {
    const newId = `OBJ-${String((output.s3?.objectives?.length || 0) + 1).padStart(2, "0")}`;
    const newObj = {
      id: newId,
      objective: "New audit objective — click Adjust to define scope",
      priority: "P2",
      linked_risk: output.s2?.risks?.[0]?.id || "R-01",
      controls: [],
      hours: 40,
      sprint: 1,
    };
    setOutput(prev => ({
      ...prev,
      s3: { ...(prev.s3 || {}), objectives: [...(prev.s3?.objectives || []), newObj] },
    }));
    setScopeApprovals(prev => ({ ...prev, [newId]: { status: "pending" } }));
    log(`Added new objective ${newId}`);
  }, [output.s3?.objectives?.length, output.s2?.risks, log]);

  const approveGate = (n) => {
    // For Gate 1, commit per-risk adjustments back into output.s2.risks
    if (n === 1) {
      setOutput(prev => {
        const orig = prev.s2?.risks || [];
        const merged = orig.map(r => {
          const a = riskApprovals[r.id];
          if (a && (a.status === "adjusted" || a.status === "signed") && a.adjustments) {
            return { ...r, ...a.adjustments };
          }
          return r;
        });
        return { ...prev, s2: { ...(prev.s2||{}), risks: merged } };
      });
      const adjusted = Object.values(riskApprovals).filter(a => a.status === "adjusted" || a.status === "signed").length;
      log(`HITL Gate ${n}: CONFIRMED — ${Object.values(riskApprovals).filter(a=>a.status==="approved").length} accepted, ${adjusted} adjusted with sign-off`);
    } else if (n === 2) {
      setOutput(prev => {
        const orig = prev.s3?.objectives || [];
        const merged = orig.map(o => {
          const a = scopeApprovals[o.id];
          if (a && (a.status === "adjusted" || a.status === "signed") && a.adjustments) {
            return { ...o, ...a.adjustments };
          }
          return o;
        });
        return { ...prev, s3: { ...(prev.s3||{}), objectives: merged } };
      });
      const adjObjs = Object.values(scopeApprovals).filter(a => a.status === "adjusted" || a.status === "signed").length;
      log(`HITL Gate 2: CONFIRMED — ${Object.values(scopeApprovals).filter(a=>a.status==="approved").length} objectives accepted, ${adjObjs} adjusted with sign-off`);
    } else {
      log(`HITL Gate ${n}: APPROVED`);
    }
    setGateState((prev) => ({ ...prev, [`g${n}`]: "approved" }));
    const res = gateResRef.current[n];
    if (res) {res({ ok: true });delete gateResRef.current[n];}
  };
  const requestOverride = (n) => {
    setOverrideGateNum(n);
    setOverrideOpen(true);
  };
  const confirmOverride = (reason) => {
    const n = overrideGateNum;
    log(`HITL Gate ${n}: OVERRIDDEN — ${reason}`);
    setGateState((prev) => ({ ...prev, [`g${n}`]: "overridden" }));
    setOverrideOpen(false);
    const res = gateResRef.current[n];
    if (res) {res({ ok: false, reason });delete gateResRef.current[n];}
  };

  // ---- Signal-adjusted risk scoring (Stage 2) ----
  function adjustRiskScores(baseRisks, allSigs, rssSigs) {
    return baseRisks.map(r => {
      // FRED contractionary signals lift macro-category risks
      const fredContr = allSigs.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").length;
      const macroAdj = r.category.toLowerCase().includes("macro") ? fredContr * 0.08 : 0;

      // RSS signals directly linked to this risk
      const rssLinked = rssSigs.filter(s => (s.affectedRisks || []).includes(r.id));
      const rssAdj = rssLinked.reduce((sum, s) => sum + (s.velocity || 0) * 0.08, 0);

      // High-velocity industry signals add minor pressure to all risks
      const highVelIndustry = allSigs.filter(s => s.src === "Industry RSS" && s.velocity >= 3).length;
      const industryAdj = Math.min(0.2, highVelIndustry * 0.05);

      const adjScore = Math.min(10, parseFloat((r.score + macroAdj + rssAdj + industryAdj).toFixed(1)));
      const adjVelocity = rssLinked.length > 0
        ? Math.max(r.velocity, Math.max(...rssLinked.map(s => s.velocity || 0)))
        : r.velocity;
      const adjRag = adjScore >= 7.5 ? "R" : adjScore >= 5.0 ? "A" : "G";
      return { ...r, score: adjScore, velocity: adjVelocity, rag: adjRag };
    });
  }

  // ---- Run loop ----
  const speed = tweaks.runSpeed || 1;
  const t = (ms) => new Promise((res) => setTimeout(res, ms / speed));

  async function runStage(id, payload, durationMs = 1400) {
    setStageState((prev) => ({ ...prev, [id]: "running" }));
    if (tweaks.autoExpand) {
      setOpenStages((prev) => new Set([...prev, id]));
    }
    log(`Stage ${id.toUpperCase()} starting`);
    await t(durationMs);
    setOutput((prev) => ({ ...prev, [id]: payload }));
    setStageState((prev) => ({ ...prev, [id]: "done" }));
    log(`Stage ${id.toUpperCase()} complete`);
  }

  async function runLoop() {
    setRunning(true);
    setHasRun(false);
    setLoopLog([]);
    setStageState({});
    setOutput({});
    setGateState({ g1: null, g2: null });
    setEvents([]);
    setNotifLog([]);
    setOpenStages(new Set(["s1"]));
    setSelectedRiskId(null);
    setRiskApprovals({});
    log("Loop started");
    try { await _runLoopBody(); } catch (err) {
      log(`Run error: ${err?.message || err}`);
      setRunning(false);
    }
  }

  async function _runLoopBody() {

    // --- Auto-ingest RSS (live fetch when liveMode, else simulate) ---
    let currentRssSignals = [...rssSignals];
    if (signalSet.has("industry")) {
      try {
        log(liveMode ? "Fetching live RSS signals…" : "Generating simulated RSS signals…");
        const ingestResult = await RSS_ENGINE.ingestAll({ simulate: !liveMode });
        const freshSigs = RSS_ENGINE.toSignals(ingestResult);
        currentRssSignals = freshSigs;
        setRssSignals(freshSigs);
        setRssLastUpdated(Date.now());
        log(`RSS: ${freshSigs.length} signals graded`);
      } catch(e) {
        log(`RSS ingest: ${e.message || "using prior signals"}`);
      }
    }

    // --- Always fetch FRED ---
    if (signalSet.has("fred")) {
      try {
        log("Fetching FRED snapshot…");
        const fred = await LIVE.loadFred();
        setFredLive(fred.series);
        log(`FRED: ${Object.keys(fred.series || {}).length} series loaded`);
      } catch(e) {
        log(`FRED snapshot: ${e.message || "using mock"}`);
      }
    }

    // --- EDGAR fetch (always attempted; required for risk profile) ---
    let edgarFin = null;
    let edgarSic = null;
    {
      setLiveStatus("Fetching EDGAR companyfacts…");
      try {
        const facts = await LIVE.fetchEdgarFacts(cfg.ticker);
        const extracted = LIVE.extractFinancials(facts);
        edgarFin = extracted;
        edgarSic = facts?.sic ?? null;
        setLivefacts(extracted);
        setLiveStatus(`EDGAR OK · ${extracted.entity} · CIK ${extracted.cik}`);
        log(`EDGAR: ${cfg.ticker}`);
      } catch(e) {
        edgarFin = null;
        setLivefacts(null);
        setLiveStatus(`EDGAR unavailable: ${e.message} · industry template`);
        log(`EDGAR unavailable: ${e.message}`);
      }
    }

    // --- Build company risk profile from EDGAR + industry template ---
    {
      const edgarProfile = LIVE.fetchEdgarProfile ? null : null; // profile from submissions
      const industry = cfg.industry || RISK_ENGINE.sic2industry(edgarSic);
      const builtProfile = RISK_ENGINE.buildProfile(cfg.ticker, edgarFin, edgarSic, industry);
      profileRef.current = builtProfile;
      setProfile(builtProfile);
      log(`Profile: ${builtProfile.entity.name} · ${industry} · ${builtProfile.risks.length} risks derived`);
    }

    // STAGE 1 — Signal Intake
    const mockSigs = profileRef.current.signals.filter((s) =>
      s.src === "EDGAR 10-K" && signalSet.has("edgar") ||
      s.src === "Peer 10-K" && signalSet.has("peers") ||
      s.src === "Industry RSS" && signalSet.has("industry") ||
      s.src === "Internal KRI" && signalSet.has("internal") ||
      s.src === "FRED Macro" && signalSet.has("fred") ||
      s.src === "Incident" && signalSet.has("incidents")
    );
    const rssSigsFiltered = signalSet.has("industry") ? currentRssSignals : [];
    const sigsList = [...mockSigs, ...rssSigsFiltered];
    await runStage("s1", { signals: sigsList, sourceCount: signalSet.size }, 1200);

    // STAGE 2 — Risk assessment with signal-adjusted scoring
    const adjustedRisks = adjustRiskScores(profileRef.current.risks, sigsList, currentRssSignals);
    const threshold = APPETITE_THRESHOLDS[cfg.appetiteLevel] ?? 7.5;
    const breachingIds = adjustedRisks.filter(r => r.score >= threshold).map(r => r.id);
    const riskAppetiteResult = {
      threshold,
      level: cfg.appetiteLevel || "AMBER",
      breaching: breachingIds,
      status: breachingIds.length > 0 ? "BREACHED" : "WITHIN APPETITE",
    };
    await runStage("s2", { risks: adjustedRisks, riskAppetite: riskAppetiteResult }, 1500);
    setActiveRailTab("rr");

    // GATE 1 — Risk assessment
    if (hitl.risk) {
      setStageState((prev) => ({ ...prev, s3: "waiting", s4: "waiting", s5: "waiting", s6: "waiting" }));
      const gres = await showGate(1);
      log(gres.ok ? "Gate 1 passed" : `Gate 1 overridden: ${gres.reason}`);
    }
    setStageState((prev) => ({ ...prev, s3: "idle" }));

    // STAGE 3 — Audit scope
    await runStage("s3", { objectives: profileRef.current.objectives }, 1500);

    // GATE 2 — Audit scope
    if (hitl.scope) {
      setStageState((prev) => ({ ...prev, s4: "waiting", s5: "waiting", s6: "waiting" }));
      const gres = await showGate(2);
      log(gres.ok ? "Gate 2 passed" : `Gate 2 overridden: ${gres.reason}`);
    }
    setStageState((prev) => ({ ...prev, s4: "idle" }));

    // STAGE 4 — MAPs
    await runStage("s4", { maps: profileRef.current.maps }, 1400);
    setActiveRailTab("map");

    // STAGE 5 — Closure
    await runStage("s5", { closure: profileRef.current.closure }, 1200);

    // STAGE 6 — Loop calibration
    await runStage("s6", { loop: profileRef.current.loop }, 1200);
    setActiveRailTab("loop");

    log("Loop complete");
    setRunning(false);
    setHasRun(true);

    // Fire a synthetic CEM event so the Control Monitor tab has content too.
    setTimeout(() => fireSyntheticEvent(2), 1000 / speed);
  }

  async function rerunFromS3() {
    if (running) return;
    setRunning(true);
    try {
      log("Re-run triggered from Stage 3");
      setStageState(prev => ({ ...prev, s3: "idle", s4: "idle", s5: "idle", s6: "idle" }));
      setOutput(prev => { const n = {...prev}; delete n.s3; delete n.s4; delete n.s5; delete n.s6; return n; });
      setGateState(prev => ({ ...prev, g2: null }));
      await t(300);
      await runStage("s3", { objectives: profileRef.current.objectives }, 1500);
      if (hitl.scope) {
        setStageState(prev => ({ ...prev, s4: "waiting", s5: "waiting", s6: "waiting" }));
        const gres = await showGate(2);
        log(gres.ok ? "Gate 2 passed" : `Gate 2 overridden: ${gres.reason}`);
      }
      setStageState(prev => ({ ...prev, s4: "idle" }));
      await runStage("s4", { maps: profileRef.current.maps }, 1400);
      setActiveRailTab("map");
      await runStage("s5", { closure: profileRef.current.closure }, 1200);
      await runStage("s6", { loop: profileRef.current.loop }, 1200);
      setActiveRailTab("loop");
      log("Re-run from Stage 3 complete");
      setHasRun(true);
    } catch (err) {
      log(`Re-run error: ${err?.message || err}`);
    }
    setRunning(false);
  }

  function resetAll() {
    setStageState({});
    setOutput({});
    setGateState({ g1: null, g2: null });
    setEvents([]);
    setNotifLog([]);
    setLoopLog([]);
    setHasRun(false);
    setOpenStages(new Set(["s1"]));
    setLivefacts(null);
    setFredLive(null);
    setLiveStatus("");
    setRiskApprovals({});
    setPerRiskAppetite({});
    setAdjustOpen(false);
    setAdjustingRiskId(null);
    setManualAudits([]);
  }

  // ---- CEM event firing ----
  function fireSyntheticEvent(count = 1) {
    for (let i = 0; i < count; i++) {
      const tpl = MOCK.eventTemplates[Math.floor(Math.random() * MOCK.eventTemplates.length)];
      const ev = {
        ...tpl,
        id: `CEV-${Date.now().toString(36).toUpperCase()}-${i}`,
        ts: Date.now() - i * 7000,
        notifs: [],
        rcLoading: true,
        rc: null
      };
      setEvents((prev) => [ev, ...prev]);
      setCemExpanded((prev) => new Set([...prev, ev.id]));
      // Schedule notification cascade
      TIERS.forEach((tier) => {
        if (!tier.sevs.includes(ev.severity)) return;
        setTimeout(() => {
          const msg = notifMsgFor(tier, ev);
          const sentAt = Date.now();
          setEvents((prev) => prev.map((e) => e.id === ev.id ?
          { ...e, notifs: [...e.notifs, { tid: tier.id, tier: tier.label, msg, sentAt, status: "sent", ackAt: null }] } :
          e));
          setNotifLog((prev) => [{ tier: tier.label, control: ev.control, msg, status: "sent", sentAt }, ...prev]);
        }, tier.delay / speed);
      });
      // Schedule RC reveal
      setTimeout(() => {
        setEvents((prev) => prev.map((e) => e.id === ev.id ? { ...e, rcLoading: false, rc: tpl.rc } : e));
      }, 2200 / speed);
    }
    if (activeMainTab !== "cem") setUnreadCEM((u) => u + count);
  }

  function ackNotif(eventId, tierId) {
    setEvents((prev) => prev.map((e) => {
      if (e.id !== eventId) return e;
      return {
        ...e,
        notifs: e.notifs.map((n) =>
        n.tid === tierId && n.status !== "ack" ?
        { ...n, status: "ack", ackAt: Date.now() } :
        n
        )
      };
    }));
    setNotifLog((prev) => prev.map((n) =>
    n.control === events.find((e) => e.id === eventId)?.control && !n.ackAt ?
    { ...n, status: "ack", ackAt: Date.now() } :
    n
    ));
  }

  // ---- Report payload ----
  const reportPayload = useMemo(() => {
    if (!hasRun) return null;
    const risksCur = output.s2?.risks || profile.risks;
    const top3 = [...risksCur].sort((a, b) => b.score - a.score).slice(0, 3).map((r) => r.name);
    const sigsList = output.s1?.signals || [];
    const fredContrCount = sigsList.filter(s => s.src === "FRED Macro" && s.delta === "contractionary").length;
    const rssHighVelCount = sigsList.filter(s => s.src === "Industry RSS" && (s.velocity || 0) >= 3).length;
    const rssLinkedCount = sigsList.filter(s => s.src === "Industry RSS" && (s.affectedRisks?.length || 0) > 0).length;
    const riskAppetiteResult = output.s2?.riskAppetite || (() => {
      const level = cfg.appetiteLevel || "AMBER"; const t = APPETITE_THRESHOLDS[level] ?? 7.5;
      const b = risksCur.filter(r => r.score >= t).map(r => r.id);
      return { threshold: t, level, breaching: b, status: b.length > 0 ? "BREACHED" : "WITHIN APPETITE" };
    })();
    const adjRiskCount = Object.values(riskApprovals).filter(a => a.status === "adjusted" || a.status === "signed").length;
    const adjObjCount  = Object.values(scopeApprovals).filter(a => a.status === "adjusted" || a.status === "signed").length;

    return {
      entity: `${profile.entity.name} (${cfg.ticker})`,
      ts: new Date().toISOString(),
      cfg: {
        industry: cfg.industry,
        focus: Array.isArray(cfg.focus) ? cfg.focus : [cfg.focus].filter(Boolean),
        sigs: [...signalSet]
      },
      signals: { count: sigsList.length, highVel: sigsList.filter((s) => s.velocity >= 3).length },
      risks: risksCur,
      baseRisks: profile.risks,
      top3,
      riskAppetite: riskAppetiteResult,
      objectives: output.s3?.objectives || [],
      maps: output.s4?.maps || [],
      closure: output.s5?.closure || {},
      loop: output.s6?.loop || {},
      scenarios: output.s7?.scenarios || profile.scenarios,
      greySwan: output.s7?.greySwan || profile.greySwan,
      personas: output.s7?.personas || profile.personas,
      log: loopLog,
      // Methodology data
      fredSeries: profile.fred || [],
      fredContrCount,
      rssHighVelCount,
      rssLinkedCount,
      liveMode,
      // HITL adjustments
      riskApprovals,
      scopeApprovals,
      assumptions: [
        `Quarterly score projections use a velocity-dampened linear model: base + (velocity × CE_mult × 0.4 × 0.85^(q−1)), capped at 10.0.`,
        `Control-effectiveness multipliers applied to velocity contribution: NONE=1.20×, WEAK=1.10×, ADEQUATE=0.95×, STRONG=0.80×.`,
        `FRED macro adjustment: each contractionary FRED signal adds +0.08 to macro-category risk scores (${fredContrCount} contractionary signal${fredContrCount !== 1 ? "s" : ""} in this run).`,
        `RSS signal adjustment: linked industry signals add (signal velocity × 0.08) to directly linked risks; risk velocity set to max of base velocity or linked signal velocity.`,
        `High-velocity industry signal adjustment: +0.05 per signal with velocity ≥ 3, capped at +0.20, applied uniformly to all risks (${rssHighVelCount} qualifying signal${rssHighVelCount !== 1 ? "s" : ""} in this run).`,
        `Macro ensemble forecasts use ARIMA + Prophet + Random Forest with FRED series as exogenous features; ensemble weights update iteratively by inverse MAPE from backtesting.`,
        `Random Forest features: lags 1–4, rolling mean and std, time index, quarter dummies, and current FRED series values.`,
        `Likelihood proxy from control effectiveness: NONE→9, WEAK→7, ADEQUATE→5, STRONG→3. Impact proxy from inherent_score field.`,
        `Risk appetite threshold: score ≥ 7.5 = RED, 5.0–7.4 = AMBER, < 5.0 = GREEN (configured: ${cfg.appetiteLevel || "AMBER"}).`,
        liveMode
          ? `Live mode active — EDGAR companyfacts fetched directly from data.sec.gov; FRED loaded from bundled snapshot.`
          : `Live mode inactive — all financial signals derived from mock dataset; EDGAR companyfacts not fetched.`,
        `Peer benchmark data sourced against ${cfg.industry}.`,
      ],
      obstacles: [
        ...risksCur.filter(r => (r.velocity || 0) >= 3).map(r =>
          `High-velocity risk: ${r.name} (${r.id}, v+${r.velocity}, ${r.rag}) — downstream audit scope expanded.`),
        ...(riskAppetiteResult?.status === "BREACHED"
          ? [`Risk appetite BREACHED: ${riskAppetiteResult.breaching?.length || 0} risk(s) exceed the ${cfg.appetiteLevel} threshold (≥${riskAppetiteResult.threshold}). HITL Gate 1 triggered for mandatory review.`]
          : []),
        ...(!liveMode
          ? ["Live data mode disabled — EDGAR companyfacts unavailable; all EDGAR-sourced signals derived from mock register."]
          : []),
        ...(adjRiskCount > 0
          ? [`${adjRiskCount} risk${adjRiskCount !== 1 ? "s" : ""} adjusted through HITL Gate 1 — auditor-revised scores and RAG ratings applied to final register.`]
          : []),
        ...(adjObjCount > 0
          ? [`${adjObjCount} audit objective${adjObjCount !== 1 ? "s" : ""} adjusted through HITL Gate 2 — revised priorities and sprint allocations reflected in plan.`]
          : []),
      ],
    };
  }, [hasRun, output, loopLog, signalSet, cfg, velocity, liveMode, riskApprovals, scopeApprovals, profile]);

  // ---- Tab definitions ----
  const mainTabs = [
    { id: "pipe", l: "Pipeline" },
    { id: "cem",  l: "Controls Monitor", count: events.length, pulse: unreadCEM > 0 },
    { id: "flow", l: "Risk Flow" },
  ];
  const pipeTabs = [
    { id: "stages", l: "Pipeline" },
    { id: "rss",    l: "RSS Signals" },
    { id: "fcst",   l: "Forecasts" },
    { id: "scen",   l: "Scenarios" },
  ];


  // ---- RENDER ----
  return (
    <div className="app">
      <ErrorBoundary>
      <Header
        cfg={cfg}
        liveMode={liveMode} livefacts={livefacts}
        running={running} hasRun={hasRun}
        entityName={profile.entity.name} />
      

      <div className="app-body">
        <Sidebar
          cfg={cfg} setCfg={setCfg}
          signalSet={signalSet} setSignalSet={setSignalSet}
          velocity={velocity}
          hitl={hitl}
          running={running} hasRun={hasRun}
          onRun={runLoop} onReset={resetAll}
          onOpenReport={() => setReportOpen(true)}
          onOpenPersona={() => {setActiveRailTab("pers");}}
          onOpenConfig={() => {
            // Surface the tweaks panel — same affordance as the toolbar toggle.
            window.postMessage({type: '__activate_edit_mode'}, '*');
          }}
          liveMode={liveMode} setLiveMode={setLiveMode}
          liveStatus={liveStatus} />
        

        <main className="main" data-screen-label="Main canvas">
          <div className="main-tabs">
            {mainTabs.map((t) =>
            <button key={t.id}
            className={"tab" + (activeMainTab === t.id ? " active" : "")}
            onClick={() => {setActiveMainTab(t.id);if (t.id === "cem") setUnreadCEM(0);}}>
                {t.l}
                {t.count > 0 && <span className="count">{t.count}</span>}
                {t.pulse && <span className="pulse" />}
              </button>
            )}
          </div>

          {/* ---- Pipeline panel (with sub-tabs) ---- */}
          <div className={"panel" + (activeMainTab === "pipe" ? " active" : "")}>
            <div className="panel-head">
              <div>
                <div className="kicker">Risk → Audit closed loop</div>
                <div className="panel-title mt-8">Six-stage continuous governance chain</div>
                <div className="panel-sub">Each stage feeds structured output to the next. HITL gates pause for human review.</div>
              </div>
              {hasRun &&
                <div className="mono" style={{ display: "flex", gap: 12, alignItems: "center", color: "var(--ink-3)", fontSize: 11 }}>
                  {output.s2?.riskAppetite?.status === "BREACHED" && (
                    <span style={{color:"var(--red-ink)", fontWeight:500}}>
                      Appetite <RAGChip rag={output.s2.riskAppetite.level?.charAt(0)}>{output.s2.riskAppetite.level}</RAGChip> BREACHED · {output.s2.riskAppetite.breaching?.length ?? 0} risk{(output.s2.riskAppetite.breaching?.length ?? 0) !== 1 ? "s" : ""} exceed tolerance
                    </span>
                  )}
                  <span><b style={{ color: "var(--ink)", fontWeight: 500 }}>{output.s2?.risks?.length || 0}</b> risks</span>
                  <span><b style={{ color: "var(--ink)", fontWeight: 500 }}>{output.s3?.objectives?.length || 0}</b> objectives</span>
                  <span><b style={{ color: "var(--ink)", fontWeight: 500 }}>{output.s4?.maps?.length || 0}</b> MAPs</span>
                </div>
              }
            </div>

            {/* Sub-tabs */}
            <div className="pipe-sub-tabs">
              {pipeTabs.map(t => (
                <button key={t.id}
                  className={"pipe-sub-tab" + (activePipeTab === t.id ? " active" : "")}
                  onClick={() => setActivePipeTab(t.id)}>
                  {t.l}
                </button>
              ))}
            </div>

            {activePipeTab === "stages" && (
              <Pipeline
                stageState={stageState}
                output={output}
                openStages={openStages}
                setOpenStages={setOpenStages}
                hitl={hitl}
                gateState={gateState}
                onApprove={approveGate}
                onOverride={requestOverride}
                signals={output.s1?.signals || []}
                livefacts={livefacts}
                liveRssSignals={rssSignals}
                rssLastUpdated={rssLastUpdated}
                rssRefreshing={rssRefreshing}
                appetiteLevel={cfg.appetiteLevel || "AMBER"}
                appetiteThreshold={APPETITE_THRESHOLDS[cfg.appetiteLevel] ?? 7.5}
                perRiskAppetite={perRiskAppetite}
                setPerRiskAppetite={setPerRiskAppetite}
                allSignals={output.s1?.signals || []}
                onRerunFromS3={rerunFromS3}
                onOpenAdjustRisk={openAdjustRisk}
                riskApprovals={riskApprovals}
                onApproveRisk={approveRisk}
                onApproveAllRisks={approveAllRemainingRisks}
                onSignoffRisk={signoffRisk}
                scopeApprovals={scopeApprovals}
                onApproveObjective={approveObjective}
                onOpenAdjustObjective={openAdjustObjective}
                onApproveAllObjectives={approveAllRemainingObjectives}
                onSignoffObjective={signoffObjective}
                onAddObjective={addObjective}
                manualAudits={manualAudits}
                onAddAudit={addManualAudit}
                onRemoveAudit={removeManualAudit} />
            )}
            {activePipeTab === "rss" && (
              <RSSPanel
                liveMode={liveMode}
                onSignalsReady={(sigs) => {
                  setRssSignals(sigs);
                  log(`RSS ingestion complete — ${sigs.length} velocity signals graded`);
                }} />
            )}
            {activePipeTab === "fcst" && (
              <ForecastsPanel
                data={hasRun ? profile.forecasts : null}
                liveMode={liveMode}
                livefacts={livefacts}
                fredSeries={fredLive}
                rssSignals={rssSignals}
                industry={hasRun ? profile.entity?.industry : cfg.industry}
                ticker={cfg.ticker} />
            )}
            {activePipeTab === "scen" && (
              <ScenariosPanel scenarios={hasRun ? profile.scenarios : null} greySwan={hasRun ? profile.greySwan : null} />
            )}
          </div>

          {/* ---- Controls Monitor ---- */}
          <div className={"panel" + (activeMainTab === "cem" ? " active" : "")}>
            <CEMPanel
              events={events} setEvents={setEvents}
              filter={cemFilter} setFilter={setCemFilter}
              expanded={cemExpanded} setExpanded={setCemExpanded}
              onAckNotif={ackNotif}
              onInject={() => fireSyntheticEvent(1)} />
          </div>

          {/* ---- Risk Flow ---- */}
          <div className={"panel" + (activeMainTab === "flow" ? " active" : "")}>
            <FlowPanel
              risks={output.s2?.risks || (hasRun ? profile.risks : null)}
              maps={output.s4?.maps || (hasRun ? profile.maps : null)}
              flowMeta={hasRun ? profile.riskFlow : null}
              selectedId={selectedRiskId} setSelectedId={setSelectedRiskId}
              liveMode={liveMode}
              rssSignals={rssSignals}
              fredData={profile.forecasts?.fred}
              appetiteThreshold={APPETITE_THRESHOLDS[cfg.appetiteLevel] ?? 7.5} />
          </div>
        </main>

        <Rail
          activeTab={activeRailTab} setActiveTab={setActiveRailTab}
          output={output}
          risks={output.s2?.risks || (hasRun ? profile.risks : null)}
          maps={output.s4?.maps || null}
          loop={output.s6?.loop || null}
          notifLog={notifLog}
          forecasts={hasRun ? profile.forecasts : null}
          flowMeta={hasRun ? profile.riskFlow : null}
          activeQuarter={activeQuarter} setActiveQuarter={setActiveQuarter}
          selectedRiskId={selectedRiskId} setSelectedRiskId={setSelectedRiskId}
          selectedPersona={selectedPersona} setSelectedPersona={setSelectedPersona}
          personas={hasRun ? profile.personas : null}
          scenarios={hasRun ? profile.scenarios : null}
          onOpenMainFlow={() => setActiveMainTab("flow")}
          periodBegin={cfg.periodBegin}
          periodEnd={cfg.periodEnd} />
        
      </div>

      <ReportModal open={reportOpen} onClose={() => setReportOpen(false)} payload={reportPayload} />
      <OverrideModal open={overrideOpen} gateNum={overrideGateNum} onClose={() => setOverrideOpen(false)} onConfirm={confirmOverride} />
      <AdjustRiskModal
        open={adjustOpen}
        risk={(output.s2?.risks || []).find(r => r.id === adjustingRiskId)}
        onClose={() => { setAdjustOpen(false); setAdjustingRiskId(null); }}
        onSubmit={submitAdjustment} />
      <AdjustObjectiveModal
        open={adjustObjOpen}
        obj={(output.s3?.objectives || profile.objectives || []).find(o => o.id === adjustingObjId)}
        onClose={() => { setAdjustObjOpen(false); setAdjustingObjId(null); }}
        onSubmit={submitObjAdjustment} />

      <DendraiTweaks tweaks={tweaks} setTweak={setTweak}
        hitl={hitl} setHitl={setHitl}
        velocity={velocity} setVelocity={setVelocity} />
      </ErrorBoundary>
    </div>);

}

// ---- Header ----
function Header({ cfg, liveMode, livefacts, running, hasRun, entityName }) {
  return (
    <header className="hdr">
      <div className="hdr-brand">
        <div className="hdr-logo">D</div>
        <div className="hdr-name">Dendrai <span>Risk Loop</span></div>
      </div>
      <div className="hdr-sep" />
      <div className="hdr-ctx">
        <span className="hdr-ctx-tkr">{cfg.ticker}</span>
        <span className="muted">·</span>
        <span style={{ fontSize: 11.5 }}>{livefacts?.entity || cfg.company || entityName}</span>
        {(() => {
          const focusList = Array.isArray(cfg.focus) ? cfg.focus : [cfg.focus].filter(Boolean);
          if (!focusList.length) return null;
          if (focusList.length === 1) return <span className="hdr-ctx-pill">{focusList[0]}</span>;
          return (
            <span className="hdr-ctx-pill" title={focusList.join(" · ")}>
              {focusList[0]} <span className="muted">· +{focusList.length - 1}</span>
            </span>
          );
        })()}
      </div>
      <div className="hdr-spacer" />
      <div className="hdr-meta">
        <div className="item">
          <span className={"live-dot" + (running || hasRun ? " on" : "")} />
          <span>{running ? "Running" : hasRun ? "Idle · last run live" : "Ready"}</span>
        </div>
        <div className="item">
          <Icon name={liveMode ? "wifi" : "satellite"} size={12} className="muted" />
          <span className="val">{liveMode ? "LIVE" : "MOCK"}</span>
        </div>
        <div className="item">
          <span className="muted">Cycle</span>
          <span className="val">28d</span>
        </div>
      </div>
    </header>);

}

// Expose Header to window for JSX usage
window.Header = Header;

export default App;