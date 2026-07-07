/* ============================================================
   Dendrai Intelligenza — main App
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
  "persona": "Internal Audit",
  "colorScheme": "system"
} /*EDITMODE-END*/;

const APPETITE_THRESHOLDS = { GREEN: 12.0, AMBER: 18.0, RED: 23.0 };

// API key sent on all mutating requests. Set VITE_API_KEY at build time (same
// value as DENDRAI_API_KEY on the server) to authenticate write endpoints.
const _API_KEY = import.meta.env.VITE_API_KEY || "";
const _authHeaders = (extra = {}) => ({
  "Content-Type": "application/json",
  ...(_API_KEY ? { "X-API-Key": _API_KEY } : {}),
  ...extra,
});

function App() {
  // ---- Tweaks ----
  const [tweaks, setTweak] = useTweaks(DEFAULT_TWEAKS);

  // Apply accent, density, and theme at body level
  useEffect(() => {
    document.body.dataset.accent = tweaks.accent;
    document.body.dataset.density = tweaks.density;
    const scheme = tweaks.colorScheme || (tweaks.dark ? "dark" : "light");
    if (scheme === "system") {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const apply = () => { document.body.dataset.theme = mq.matches ? "dark" : ""; };
      apply();
      mq.addEventListener("change", apply);
      return () => mq.removeEventListener("change", apply);
    } else {
      document.body.dataset.theme = scheme === "dark" ? "dark" : "";
    }
  }, [tweaks.accent, tweaks.density, tweaks.colorScheme, tweaks.dark]);

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
  const [rssEnabledFeeds, setRssEnabledFeeds] = useState(() => RSS_ENGINE.FEEDS.map(f => f.id));

  // ---- AI Chat (declared here so it's before the persistence effects that include it) ----
  const [aiChatCfg, setAiChatCfg] = useState({ provider: "claude", buttonLabel: "Ask Claude" });
  const [chatOpen, setChatOpen] = useState(false);

  // ---- Config persistence (DB primary, localStorage fallback) ----
  const [lastSaved, setLastSaved] = useState(null);
  const cfgLoadedRef = useRef(false);
  useEffect(() => {
    (async () => {
      const applyConfig = (s) => {
        if (s.cfg) setCfg(c => ({ ...c, ...s.cfg }));
        if (Array.isArray(s.signals)) setSignalSet(new Set(s.signals));
        if (typeof s.velocity === "number") setVelocity(s.velocity);
        if (s.hitl) setHitl(s.hitl);
        const feeds = s.rssEnabledFeeds || s.rss_enabled_feeds;
        if (Array.isArray(feeds)) setRssEnabledFeeds(feeds);
        const chatCfg = s.aiChatCfg || s.ai_chat_cfg;
        if (chatCfg) setAiChatCfg(c => ({ ...c, ...chatCfg }));
        const ts = s.savedAt || s.saved_at;
        if (ts) setLastSaved(ts);
      };
      try {
        const res = await fetch("/api/mcp/config/pipeline");
        if (res.ok) {
          applyConfig(await res.json());
          cfgLoadedRef.current = true;
          return;
        }
      } catch {}
      // Fallback to localStorage when API is unavailable
      try {
        const raw = localStorage.getItem("dendrai.config");
        if (raw) applyConfig(JSON.parse(raw));
      } catch {}
      cfgLoadedRef.current = true;
    })();
  }, []);
  useEffect(() => {
    if (!cfgLoadedRef.current) return;
    const savedAt = Date.now();
    const payload = { cfg, signals: [...signalSet], velocity, hitl, rssEnabledFeeds, aiChatCfg, savedAt };
    // Write-through: localStorage for instant offline access, DB for cross-device persistence
    try { localStorage.setItem("dendrai.config", JSON.stringify(payload)); } catch {}
    setLastSaved(savedAt);
    fetch("/api/mcp/config/pipeline", {
      method: "PUT",
      headers: _authHeaders(),
      body: JSON.stringify(payload),
    }).catch(() => {});
  }, [cfg, signalSet, velocity, hitl, rssEnabledFeeds, aiChatCfg]);

  // ---- Data modes: mock / live (JS) / mcp (Python servers) ----
  const [liveMode, setLiveMode] = useState(false);
  const [mcpMode, setMcpMode] = useState(true);
  // useDb: when mcpMode=true, use PostgreSQL cache instead of fetching live from EDGAR/FRED
  const [useDb, setUseDb] = useState(false);
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
  const runIdRef = useRef(null);
  const loopLogRef = useRef([]);
  const manualAuditsRef = useRef([]);

  // Auto-generated Risk-as-Code YAML after each loop completion
  const [autoCodeYaml, setAutoCodeYaml] = useState(null);

  // ---- Tabs ----
  const [activeScreen, setActiveScreen] = useState("pipeline"); // config|pipeline|register|controls|flow|maps|notifs|scope|riskcode|policycode|gov
  const [activePipeTab, setActivePipeTab] = useState("stages"); // stages | rss (forecasts/scenarios moved to the rail)
  const [activeRailTab, setActiveRailTab] = useState("rr"); // Risk Register sub-tab: rr | hm | loop (also nudged by the run)
  const [personaOpen, setPersonaOpen] = useState(false);
  const [activeQuarter, setActiveQuarter] = useState("Now");
  const [selectedRiskId, setSelectedRiskId] = useState(null);
  const [selectedPersona, setSelectedPersona] = useState(tweaks.persona);

  // Keep persona pick in sync with tweaks
  useEffect(() => {setSelectedPersona(tweaks.persona);}, [tweaks.persona]);

  // ---- RSS ingestion signals ----
  const [rssSignals, setRssSignals] = useState([]);
  const [rssLastUpdated, setRssLastUpdated] = useState(null);
  const [rssRefreshing, setRssRefreshing] = useState(false);
  // { msg: string, feedsDone: string[] } | null — tracks per-feed progress during pipeline run
  const [rssRunProgress, setRssRunProgress] = useState(null);
  const [perRiskAppetite, setPerRiskAppetite] = useState({});

  // ---- Narrative analysis results (from S1 AI extract, fed to Gate 1 context) ----
  const [narrativeResult, setNarrativeResult] = useState(null);

  // Periodic RSS refresh while pipeline is running
  useEffect(() => {
    if (!running) return;
    const doRefresh = async () => {
      if (!signalSet.has("industry")) return;
      setRssRefreshing(true);
      try {
        const ingestResult = await RSS_ENGINE.ingestAll({ enabledFeedIds: rssEnabledFeeds, ticker: cfg.ticker });
        const freshSigs = RSS_ENGINE.toSignals(ingestResult);
        setRssSignals(freshSigs);
        setRssLastUpdated(Date.now());
      } catch(e) { /* silent */ }
      setRssRefreshing(false);
    };
    const interval = setInterval(doRefresh, 6 * 60 * 60 * 1000); // 6 hours
    return () => clearInterval(interval);
  }, [running, liveMode]);

  // ---- CEM state ----
  const [events, setEvents] = useState([]);
  const [cemFilter, setCemFilter] = useState("all");
  const [cemExpanded, setCemExpanded] = useState(new Set());
  const [notifLog, setNotifLog] = useState([]);
  const [unreadCEM, setUnreadCEM] = useState(0);

  // ---- Governance Intelligence pane ----
  const [govData, setGovData] = useState(null);     // proxy data from DEF 14A
  const [govPeerData, setGovPeerData] = useState(null);
  const [govLoading, setGovLoading] = useState(false);
  const [govFetchError, setGovFetchError] = useState(null);
  const [activeGovTab, setActiveGovTab] = useState("overview");

  // ---- Audit Scope: DB-backed fallback when Assess Enterprise Risk hasn't
  // been run in this session ----
  const [savedAuditScope, setSavedAuditScope] = useState(null); // { run_id, run_at, objectives }
  const [savedAuditScopeLoading, setSavedAuditScopeLoading] = useState(false);

  // Load-on-demand: land on Audit Scope with no live run in memory (page
  // reload, or navigating here without rerunning the pipeline this session)
  // — pull the last completed run's saved objectives from the DB instead of
  // showing the generic industry-template mock. Cleared whenever a live run
  // exists so the freshly-computed objectives always take priority.
  useEffect(() => {
    if (activeScreen !== "scope" || savedAuditScopeLoading || !cfg.ticker) return;
    if (output.s3?.objectives?.length || savedAuditScope) return;
    setSavedAuditScopeLoading(true);
    MCP.fetchSavedAuditScope(cfg.ticker)
      .then(saved => { if (saved) setSavedAuditScope(saved); })
      .catch(() => {})
      .finally(() => setSavedAuditScopeLoading(false));
  }, [activeScreen, cfg.ticker, output.s3?.objectives, savedAuditScope]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load-on-demand: if you land on Governance Intelligence, or the Pipeline
  // screen's Beneish M-Score gauge needs peer data for benchmarking, without
  // govData/govPeerData in memory (page reload, navigating here without
  // rerunning the pipeline, or the live peer fetch failed while the proxy
  // fetch succeeded) — pull whichever piece is still missing from the DB for
  // this ticker instead of requiring a fresh live EDGAR fetch. govData and
  // govPeerData are checked independently so one succeeding doesn't block
  // retrying the other.
  useEffect(() => {
    if ((activeScreen !== "gov" && activeScreen !== "pipeline") || govLoading || !cfg.ticker) return;
    if (govData && govPeerData) return;
    const wantProxy = !govData;
    const wantPeers = !govPeerData;
    setGovLoading(true);
    Promise.allSettled([
      wantProxy ? MCP.fetchSavedProxyData(cfg.ticker) : Promise.resolve(null),
      wantPeers ? MCP.fetchSavedPeerBenchmarks(cfg.ticker) : Promise.resolve(null),
    ]).then(([proxyRes, peerRes]) => {
      if (wantProxy && proxyRes.status === "fulfilled" && proxyRes.value) setGovData(proxyRes.value);
      if (wantPeers && peerRes.status  === "fulfilled" && peerRes.value)  setGovPeerData(peerRes.value);
      setGovLoading(false);
    });
  }, [activeScreen, cfg.ticker, govData, govPeerData]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // ---- Last loop persistence — DB primary, localStorage fallback ----
  useEffect(() => {
    (async () => {
      const applyLoop = (s) => {
        if (s.output)                                    setOutput(s.output);
        const ss = s.stageState || s.stage_state;
        if (ss)                                          setStageState(ss);
        const gs = s.gateState || s.gate_state;
        if (gs)                                          setGateState(gs);
        const ll = s.loopLog || s.loop_log;
        if (ll)                                          { setLoopLog(ll); loopLogRef.current = ll; }
        if (s.hasRun || s.has_run)                       setHasRun(true);
        if (s.livefacts)                                 setLivefacts(s.livefacts);
        const pra = s.perRiskAppetite || s.per_risk_appetite;
        if (pra)                                         setPerRiskAppetite(pra);
        const ra = s.riskApprovals || s.risk_approvals;
        if (ra)                                          setRiskApprovals(ra);
        const sa = s.scopeApprovals || s.scope_approvals;
        if (sa)                                          setScopeApprovals(sa);
        const ma = s.manualAudits || s.manual_audits;
        if (ma)                                          { setManualAudits(ma); manualAuditsRef.current = ma; }
        const nr = s.narrativeResult || s.narrative_result;
        if (nr)                                          setNarrativeResult(nr);
        const os = s.openStages || s.open_stages;
        if (os)                                          setOpenStages(new Set(os));
      };
      try {
        const res = await fetch("/api/mcp/loop/last-state");
        if (res.ok) {
          applyLoop(await res.json());
          return;
        }
      } catch {}
      // Fallback to localStorage when API is unavailable
      try {
        const raw = localStorage.getItem("dendrai.lastLoop");
        if (raw) applyLoop(JSON.parse(raw));
      } catch {}
    })();
  }, []);
  useEffect(() => {
    if (!hasRun) return;
    const payload = {
      output,
      stageState,
      gateState,
      loopLog,
      hasRun,
      livefacts,
      perRiskAppetite,
      riskApprovals,
      scopeApprovals,
      manualAudits,
      narrativeResult,
      openStages: [...openStages],
      savedAt: Date.now(),
    };
    // Write-through: localStorage for instant offline access, DB for persistence
    try { localStorage.setItem("dendrai.lastLoop", JSON.stringify(payload)); } catch {}
    fetch("/api/mcp/loop/last-state", {
      method: "PUT",
      headers: _authHeaders(),
      body: JSON.stringify(payload),
    }).catch(() => {});
  }, [hasRun, output]);

  const auth = window.useAuth ? window.useAuth() : null;
  const auditorName = auth?.user?.display_name || auth?.user?.username || "Auditor";

  // ---- Logging helper ----
  const log = useCallback((msg) => {
    const entry = { ts: new Date().toISOString(), msg };
    loopLogRef.current = [...loopLogRef.current, entry];
    setLoopLog(loopLogRef.current);
  }, []);

  // ---- Manual audit plan entries ----
  const [manualAudits, setManualAudits] = useState([]);
  const addManualAudit = useCallback((audit) => {
    setManualAudits(prev => {
      const next = [...prev, audit];
      manualAuditsRef.current = next;
      return next;
    });
    log(`Manual audit added: ${audit.title} · ${audit.when} · linked to ${audit.riskId}`);
  }, [log]);
  const removeManualAudit = useCallback((id) => {
    setManualAudits(prev => {
      const next = prev.filter(a => a.id !== id);
      manualAuditsRef.current = next;
      return next;
    });
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

  // ---- Real approval workflow: submit a preparer disposition, get back the
  // resolved status (submitted-to-manager, or auto-approved if no manager) ----
  const submitApprovalTask = useCallback(async (gateType, itemRef, itemLabel, disposition, adjustments, rationale) => {
    if (!mcpMode || !runIdRef.current) return null;
    try {
      const result = await MCP.prepareApprovalTask({
        runId: runIdRef.current, gateType, itemRef, itemLabel, disposition, adjustments, rationale,
      });
      return result.task;
    } catch (e) {
      log(`Approval submission failed: ${e.message}`);
      return null;
    }
  }, [mcpMode, log]);

  // ---- Per-risk HITL handlers ----
  const approveRisk = useCallback((riskId) => {
    const risk = (output.s2?.risks || []).find(r => r.id === riskId);
    setRiskApprovals(prev => ({ ...prev, [riskId]: { ...(prev[riskId]||{}), status: "approved" } }));
    log(`Risk ${riskId}: APPROVED as scored by ${auditorName}`);
    submitApprovalTask("risk", riskId, risk?.name, "approved", null, null);
  }, [auditorName, log, output.s2?.risks, submitApprovalTask]);

  const openAdjustRisk = useCallback((riskId) => {
    setAdjustingRiskId(riskId);
    setAdjustOpen(true);
  }, []);

  const submitAdjustment = useCallback(async (payload) => {
    const id = adjustingRiskId;
    if (!id) return;
    const risk = (output.s2?.risks || []).find(r => r.id === id);
    const adjustments = { name: payload.name, category: payload.category, rag: payload.rag, score: payload.score, velocity: payload.velocity, ce: payload.ce, _isNew: false };
    const task = await submitApprovalTask("risk", id, payload.name || risk?.name, "adjusted", adjustments, payload.rationale);
    setRiskApprovals(prev => ({
      ...prev,
      [id]: {
        status: task?.status || "submitted",
        adjustments,
        rationale: payload.rationale,
        adjustedBy: auditorName,
        adjustedAt: Date.now(),
        managerName: task?.manager_name || null,
      }
    }));
    log(task?.status === "approved"
      ? `Risk ${id}: ADJUSTED by ${auditorName} — auto-approved (no manager configured)`
      : `Risk ${id}: ADJUSTED by ${auditorName} — routed to ${task?.manager_name || "your manager"} for review`);
    setAdjustOpen(false);
    setAdjustingRiskId(null);
  }, [adjustingRiskId, auditorName, log, output.s2?.risks, submitApprovalTask]);

  const approveAllRemainingRisks = useCallback(() => {
    // Newly-added risks (_isNew) are excluded — they still require individual
    // assessment via Adjust before they can be approved, same as the per-row gate.
    const risksNow = output.s2?.risks || [];
    const newRiskIds = new Set(risksNow.filter(r => r._isNew).map(r => r.id));
    setRiskApprovals(prev => {
      const next = { ...prev };
      Object.keys(next).forEach(id => {
        if (next[id].status === "pending" && !newRiskIds.has(id)) {
          next[id] = { ...next[id], status: "approved" };
          const risk = risksNow.find(r => r.id === id);
          submitApprovalTask("risk", id, risk?.name, "approved", null, null);
        }
      });
      return next;
    });
    log(`Bulk-approve: all remaining pending risks accepted by ${auditorName}`);
  }, [auditorName, log, output.s2?.risks, submitApprovalTask]);

  // ---- Per-objective HITL handlers (Gate 2) ----
  const approveObjective = useCallback((objId) => {
    const obj = (output.s3?.objectives || []).find(o => o.id === objId);
    setScopeApprovals(prev => ({ ...prev, [objId]: { ...(prev[objId]||{}), status: "approved" } }));
    log(`Objective ${objId}: APPROVED as scoped by ${auditorName}`);
    submitApprovalTask("objective", objId, obj?.objective, "approved", null, null);
  }, [auditorName, log, output.s3?.objectives, submitApprovalTask]);

  const openAdjustObjective = useCallback((objId) => {
    setAdjustingObjId(objId);
    setAdjustObjOpen(true);
  }, []);

  const submitObjAdjustment = useCallback(async (payload) => {
    const id = adjustingObjId;
    if (!id) return;
    const adjustments = {
      objective: payload.objective,
      priority: payload.priority,
      sprint: payload.sprint,
      hours: payload.hours,
      linked_risks: payload.linked_risks,
      controls: payload.controls,
      residualRiskReduction: payload.residualRiskReduction,
      _isNew: false,
    };
    const task = await submitApprovalTask("objective", id, payload.objective, "adjusted", adjustments, payload.rationale);
    setScopeApprovals(prev => ({
      ...prev,
      [id]: {
        status: task?.status || "submitted",
        adjustments,
        rationale: payload.rationale,
        adjustedBy: auditorName,
        adjustedAt: Date.now(),
        managerName: task?.manager_name || null,
      }
    }));
    log(task?.status === "approved"
      ? `Objective ${id}: ADJUSTED by ${auditorName} — auto-approved (no manager configured)`
      : `Objective ${id}: ADJUSTED by ${auditorName} — routed to ${task?.manager_name || "your manager"} for review`);
    setAdjustObjOpen(false);
    setAdjustingObjId(null);
  }, [adjustingObjId, auditorName, log, submitApprovalTask]);

  const approveAllRemainingObjectives = useCallback(() => {
    const objsNow = output.s3?.objectives || [];
    setScopeApprovals(prev => {
      const next = { ...prev };
      Object.keys(next).forEach(id => {
        if (!next[id] || next[id].status === "pending") {
          next[id] = { status: "approved" };
          const obj = objsNow.find(o => o.id === id);
          submitApprovalTask("objective", id, obj?.objective, "approved", null, null);
        }
      });
      return next;
    });
    log(`Bulk-approve: all remaining objectives accepted by ${auditorName}`);
  }, [auditorName, log, output.s3?.objectives, submitApprovalTask]);

  const addObjective = useCallback(() => {
    const newId = `OBJ-${String((output.s3?.objectives?.length || 0) + 1).padStart(2, "0")}`;
    const newObj = {
      id: newId,
      objective: "New audit objective — click Edit to define scope",
      priority: "P2",
      linked_risks: [],
      controls: [],
      hours: 40,
      sprint: 1,
      _isNew: true,
    };
    setOutput(prev => ({
      ...prev,
      s3: { ...(prev.s3 || {}), objectives: [...(prev.s3?.objectives || []), newObj] },
    }));
    setScopeApprovals(prev => ({ ...prev, [newId]: { status: "pending" } }));
    log(`Added new objective ${newId}`);
    setAdjustingObjId(newId);
    setAdjustObjOpen(true);
  }, [output.s3?.objectives?.length, log]);

  const addRisk = useCallback(() => {
    const risksNow = output.s2?.risks || [];
    const newId = `R-${String(risksNow.length + 1).padStart(2, "0")}`;
    const newRisk = {
      id: newId,
      name: "New risk — click Assess to score",
      category: "Operational",
      score: 1,
      velocity: 0,
      ce: "ADEQUATE",
      rag: "G",
      _isNew: true,
    };
    setOutput(prev => ({
      ...prev,
      s2: { ...(prev.s2 || {}), risks: [...(prev.s2?.risks || []), newRisk] },
    }));
    setRiskApprovals(prev => ({ ...prev, [newId]: { status: "pending" } }));
    log(`Added new risk ${newId}`);
    setAdjustingRiskId(newId);
    setAdjustOpen(true);
  }, [output.s2?.risks, log]);

  // Each item's disposition is already persisted individually as it's actioned
  // (submitApprovalTask, above) — confirming the gate just unblocks the pipeline
  // and folds any adjustments into output.sN so downstream stages see them.
  // Manager review happens asynchronously afterward via the Approval Inbox and
  // does not block pipeline progression.
  const approveGate = (n) => {
    const isResolved = (a) => a?.status === "approved" || a?.status === "submitted" || a?.status === "manager_approved";
    if (n === 1) {
      setOutput(prev => {
        const orig = prev.s2?.risks || [];
        const merged = orig.map(r => {
          const a = riskApprovals[r.id];
          if (a && isResolved(a) && a.status !== "approved" && a.adjustments) {
            return { ...r, ...a.adjustments };
          }
          return r;
        });
        return { ...prev, s2: { ...(prev.s2||{}), risks: merged } };
      });
      const adjusted = Object.values(riskApprovals).filter(a => a.status !== "approved" && isResolved(a)).length;
      log(`HITL Gate ${n}: CONFIRMED — ${Object.values(riskApprovals).filter(a=>a.status==="approved").length} accepted, ${adjusted} adjusted and routed for review`);
    } else if (n === 2) {
      setOutput(prev => {
        const orig = prev.s3?.objectives || [];
        const merged = orig.map(o => {
          const a = scopeApprovals[o.id];
          if (a && isResolved(a) && a.status !== "approved" && a.adjustments) {
            return { ...o, ...a.adjustments };
          }
          return o;
        });
        return { ...prev, s3: { ...(prev.s3||{}), objectives: merged } };
      });
      const adjObjs = Object.values(scopeApprovals).filter(a => a.status !== "approved" && isResolved(a)).length;
      log(`HITL Gate 2: CONFIRMED — ${Object.values(scopeApprovals).filter(a=>a.status==="approved").length} objectives accepted, ${adjObjs} adjusted and routed for review`);
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
      const macroAdj = r.category.toLowerCase().includes("macro") ? fredContr * 0.20 : 0;

      // RSS signals directly linked to this risk
      const rssLinked = rssSigs.filter(s => (s.affectedRisks || []).includes(r.id));
      const rssAdj = rssLinked.reduce((sum, s) => sum + (s.velocity || 0) * 0.20, 0);

      // High-velocity industry signals add minor pressure to all risks
      const highVelIndustry = allSigs.filter(s => s.src === "Industry RSS" && s.velocity >= 3).length;
      const industryAdj = Math.min(0.5, highVelIndustry * 0.125);

      // SEC 8-K material events amplify risks in matching categories
      const eightKLinked = allSigs.filter(s => {
        if (s.src !== "SEC 8-K") return false;
        const rCat = (r.category || "").toLowerCase();
        const sCat = (s.category || "").toLowerCase();
        return sCat && (rCat.includes(sCat) || sCat.includes(rCat));
      });
      const eightKAdj = Math.min(1.5, eightKLinked.reduce(
        (sum, s) => sum + (s.severity === "P1" ? 0.5 : s.severity === "P2" ? 0.25 : 0.1), 0
      ));

      const adjScore = Math.min(25, parseFloat((r.score + macroAdj + rssAdj + industryAdj + eightKAdj).toFixed(1)));
      const adjVelocity = rssLinked.length > 0
        ? Math.max(r.velocity, Math.max(...rssLinked.map(s => s.velocity || 0)))
        : r.velocity;
      const adjRag = adjScore >= 15 ? "R" : adjScore >= 9 ? "A" : "G";

      // Regenerate hist so the sparkline direction matches the adjusted score trend.
      // r.hist[0] is the original base; the line must end at adjScore.
      const histBase = r.hist?.[0] ?? r.score;
      const histLen  = r.hist?.length ?? 6;
      const histStep = (adjScore - histBase) / Math.max(1, histLen - 1);
      const adjHist  = Array.from({ length: histLen }, (_, i) =>
        +Math.max(1.0, Math.min(25.0, histBase + histStep * i)).toFixed(1)
      );

      // Update CE to reflect the adjusted score's distance from the original base.
      const deltaFromBase = adjScore - histBase;
      const adjCe = deltaFromBase > 5.0 ? "WEAK" : deltaFromBase > 1.25 ? "ADEQUATE" : "STRONG";

      return { ...r, score: adjScore, velocity: adjVelocity, rag: adjRag, hist: adjHist, ce: adjCe };
    });
  }

  // ---- Run loop ----
  const speed = tweaks.runSpeed || 1;
  const t = (ms) => new Promise((res) => setTimeout(res, ms / speed));

  function buildTrace({ assumptions = [], decisions = [], obstacles = [], conclusion = "" }) {
    return { assumptions, decisions, obstacles, conclusion };
  }

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
    runIdRef.current = null;
    loopLogRef.current = [];
    manualAuditsRef.current = [];
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

    // --- Data ingestion: MCP (Python servers) OR Live JS OR Mock ---
    let currentRssSignals = [...rssSignals];
    let _capturedCemEvents = [];

    if (mcpMode) {
      // ── MCP mode: delegate all ingestion to Python predictive analytics server ──
      setLiveStatus("Calling Python MCP servers…");
      log("MCP: Starting full analysis…");
      try {
        const mcpResult = await MCP.fetchFullAnalysis(cfg.ticker, {
          industry:      cfg.industry,
          includeRss:   signalSet.has("industry"),
          includeFred:  signalSet.has("fred"),
          useDb,
        });

        if (mcpResult._db_id) runIdRef.current = mcpResult._db_id;

        const industry = mcpResult.industry || cfg.industry || RISK_ENGINE.sic2industry(mcpResult.sic);
        setLiveStatus(`MCP OK · ${mcpResult.company_name} · ${industry}`);
        log(`MCP: ${mcpResult.ticker} · ${industry} · ${mcpResult.risk_scores?.risks?.length || 0} risks scored`);

        // Build template profile for narrative text (objectives, MAPs, controls)
        const templateProfile = RISK_ENGINE.buildProfile(cfg.ticker, null, mcpResult.sic, industry);

        // Override synthetic forecast history with real EDGAR quarterly series from MCP
        const _mcpForecast = mcpResult.forecast;
        if (_mcpForecast) {
          const _toQL = (d) => { if (!d) return null; const [y,m] = d.slice(0,7).split('-').map(Number); return `Q${Math.ceil(m/3)}-${String(y).slice(-2)}`; };
          const { fcLabels } = RISK_ENGINE.quarterBoundaries();

          const revHist = (_mcpForecast.history || []).slice(-20).map(p => ({
            q: _toQL(p.quarter_end) || p.quarter_end,
            v: +(p.value / 1e6).toFixed(0),
          }));
          if (revHist.length >= 4) {
            templateProfile.forecasts.revenue.history = revHist;
            // Replace simple trend-based forecast with MCP ensemble forecast points
            if (_mcpForecast.forecasts?.length) {
              templateProfile.forecasts.revenue.forecast = _mcpForecast.forecasts.map((f, i) => ({
                q:    fcLabels[i] || `H${i + 1}`,
                base: +(f.point / 1e6).toFixed(0),
                lo:   +(f.ci_lower / 1e6).toFixed(0),
                hi:   +(f.ci_upper / 1e6).toFixed(0),
              }));
            }
          }

          const mgHist = (_mcpForecast.margin_history || []).slice(-20).map(p => ({
            q: _toQL(p.quarter_end) || p.quarter_end,
            v: +p.value.toFixed(1),
          }));
          if (mgHist.length >= 4) {
            templateProfile.forecasts.margin.history = mgHist;
            // Use ensemble forecast from Python if available; fall back to trend extrapolation
            if (_mcpForecast.margin_forecast?.forecasts?.length) {
              templateProfile.forecasts.margin.forecast = _mcpForecast.margin_forecast.forecasts.map((f, i) => ({
                q:    fcLabels[i] || `H${i + 1}`,
                base: +f.point.toFixed(1),
                lo:   +f.ci_lower.toFixed(1),
                hi:   +f.ci_upper.toFixed(1),
              }));
            } else {
              // Trend extrapolation: use average of last 4Q change, not a fixed constant
              const lastMG = mgHist[mgHist.length - 1].v;
              const n = Math.min(4, mgHist.length - 1);
              const step = n > 0 ? (lastMG - mgHist[mgHist.length - 1 - n].v) / n * 0.5 : 0;
              templateProfile.forecasts.margin.forecast = fcLabels.map((q, i) => ({
                q,
                base: +(lastMG + step * (i + 1)).toFixed(1),
                lo:   +(lastMG + step * (i + 1) - 2.5).toFixed(1),
                hi:   +(lastMG + step * (i + 1) + 2.5).toFixed(1),
              }));
            }
          }
        }

        // Override synthetic analyst KPI series with real EDGAR data from MCP
        const _as = mcpResult.analyst_series;
        if (_as) {
          const { fcLabels: fcL } = RISK_ENGINE.quarterBoundaries();
          const _toQL2 = (d) => { if (!d) return null; const [y,m] = d.slice(0,7).split('-').map(Number); return `Q${Math.ceil(m/3)}-${String(y).slice(-2)}`; };
          const _mapQ  = (series, scale, digits) =>
            (series || []).slice(-20).map(p => ({ q: _toQL2(p.quarter_end) || p.quarter_end, v: +(p.value / scale).toFixed(digits) }));
          const _linFc = (hist, labels, digits) => {
            if (!hist?.length) return null;
            const last = hist[hist.length - 1].v;
            const step = hist.length >= 2 ? (last - hist[hist.length - 2].v) * 0.5 : 0;
            return labels.map((q, i) => {
              const b = +(last + step * (i + 1)).toFixed(digits);
              return { q, base: b, lo: +(b - Math.abs(last) * 0.10).toFixed(digits), hi: +(b + Math.abs(last) * 0.10).toFixed(digits) };
            });
          };

          if (_as.eps?.length >= 4) {
            const h = _mapQ(_as.eps, 1, 2);
            templateProfile.forecasts.eps.history  = h;
            templateProfile.forecasts.eps.forecast = _as.eps_forecast?.forecasts?.length
              ? _as.eps_forecast.forecasts.map((f, i) => ({ q: fcL[i] || `H${i+1}`, base: +f.point.toFixed(2), lo: +f.ci_lower.toFixed(2), hi: +f.ci_upper.toFixed(2) }))
              : _linFc(h, fcL, 2) ?? templateProfile.forecasts.eps.forecast;
          }
          if (_as.op_margin?.length >= 4) {
            const h = _mapQ(_as.op_margin, 1, 1);
            templateProfile.forecasts.opMargin.history  = h;
            templateProfile.forecasts.opMargin.forecast = _as.op_margin_forecast?.forecasts?.length
              ? _as.op_margin_forecast.forecasts.map((f, i) => ({ q: fcL[i] || `H${i+1}`, base: +f.point.toFixed(1), lo: +f.ci_lower.toFixed(1), hi: +f.ci_upper.toFixed(1) }))
              : _linFc(h, fcL, 1) ?? templateProfile.forecasts.opMargin.forecast;
          }
          if (_as.net_income?.length >= 4) {
            const h = _mapQ(_as.net_income, 1e6, 0);
            templateProfile.forecasts.netIncome.history  = h;
            templateProfile.forecasts.netIncome.forecast = _linFc(h, fcL, 0) ?? templateProfile.forecasts.netIncome.forecast;
          }
          if (_as.fcf?.length >= 4) {
            const h = _mapQ(_as.fcf, 1e6, 0);
            templateProfile.forecasts.fcf.history  = h;
            templateProfile.forecasts.fcf.forecast = _linFc(h, fcL, 0) ?? templateProfile.forecasts.fcf.forecast;
          }
          if (_as.ebitda?.length >= 4) {
            const h = _mapQ(_as.ebitda, 1e6, 0);
            templateProfile.forecasts.ebitda.history  = h;
            templateProfile.forecasts.ebitda.forecast = _linFc(h, fcL, 0) ?? templateProfile.forecasts.ebitda.forecast;
          }
        }

        // Overlay MCP-computed scores onto template risks
        const mergedRisks = MCP.mergeRiskScores(templateProfile.risks, mcpResult.risk_scores);

        // MCP RSS signals → Loop signal format (industry feeds from predictive analytics)
        if (mcpResult.rss_signals && signalSet.has("industry")) {
          const rssSigs = MCP.mapRssSignals(mcpResult, mergedRisks);
          currentRssSignals = rssSigs;
          log(`MCP RSS industry: ${rssSigs.length} signals`);
        }

        // Compliance RSS (BIS/CISA/SEC/Fed/EPA) — server-side cached, supplement industry signals
        if (signalSet.has("industry") && rssEnabledFeeds.length > 0) {
          try {
            const rssFeeds = RSS_ENGINE.FEEDS.filter(f => rssEnabledFeeds.includes(f.id));
            setRssRunProgress({ msg: "Fetching compliance feeds via MCP…", feedsDone: [] });
            const complianceResult = await MCP.ingestRssFeeds(rssEnabledFeeds, { ticker: cfg.ticker });
            // Mark all feeds that came back ok as done for the progress display
            const doneFeedIds = complianceResult.feeds
              .filter(r => r.fetchStatus === "ok")
              .map(r => r.feed.id);
            setRssRunProgress({ msg: "Compliance feeds complete", feedsDone: doneFeedIds });
            const complianceSigs = complianceResult.feeds.flatMap(r =>
              r.articles.filter(a => a.velocity > 0)
            );
            currentRssSignals = [...currentRssSignals, ...complianceSigs];
            setRssSignals(currentRssSignals);
            setRssLastUpdated(Date.now());
            log(`MCP compliance RSS: ${complianceSigs.length} signals · ${complianceResult.live_feeds} live · ${complianceResult.feeds.filter(r => r.cached).length} cached`);
            setRssRunProgress(null);
          } catch(e) {
            setRssRunProgress(null);
            log(`MCP compliance RSS: ${e.message || "fetch failed"}`);
          }
        }

        // MCP FRED indicators → Loop signal format (appended to RSS)
        if (mcpResult.macro_leading_indicators && signalSet.has("fred")) {
          const fredSigs = MCP.mapFredSignals(mcpResult);
          currentRssSignals = [...currentRssSignals, ...fredSigs];
          setFredLive(mcpResult.macro_leading_indicators);
          log(`MCP FRED: ${fredSigs.length} macro indicators`);
        }

        // Expose financial ratios in the same shape live-data.js uses
        setLivefacts({
          entity: mcpResult.company_name,
          ticker: mcpResult.ticker,
          cik:    mcpResult.cik,
          ...mcpResult.financial_ratios,
        });

        // Item 1A risk factors → enrich risk narratives with filing snippets
        let enrichedRisks = mergedRisks;
        if (signalSet.has("edgar")) {
          try {
            const factors = await MCP.fetchRiskFactors(cfg.ticker);
            enrichedRisks = MCP.enrichRisksFromFactors(mergedRisks, factors);
            log(`MCP Risk Factors: ${factors.filings?.length || 0} filings parsed, snippets matched`);
          } catch(e) { log(`MCP Risk Factors unavailable: ${e.message}`); }
        }

        // 8-K material events → seed CEM with real events
        try {
          const eightK = await MCP.fetch8kEvents(cfg.ticker);
          const cemEvs = MCP.map8kToCemEvents(eightK);
          if (cemEvs.length) {
            _capturedCemEvents = cemEvs;
            setEvents(cemEvs);
            cemEvs.forEach(ev => {
              TIERS.filter(t => t.sevs.includes(ev.severity)).forEach(tier => {
                const msg = notifMsgFor(tier, ev);
                const sentAt = Date.now();
                setEvents(prev => prev.map(e => e.id === ev.id
                  ? { ...e, notifs: [...(e.notifs||[]), { tid: tier.id, tier: tier.label, msg, sentAt, status: "sent", ackAt: null }] }
                  : e));
                setNotifLog(prev => [{ tier: tier.label, control: ev.control, msg, status: "sent", sentAt }, ...prev]);
              });
            });
            log(`MCP 8-K Events: ${cemEvs.length} material events loaded into CEM`);
            setUnreadCEM(u => u + cemEvs.length);
          }
        } catch(e) { log(`MCP 8-K Events unavailable: ${e.message}`); }

        // Proxy data + peer benchmarks → Governance pane (fire and forget, non-blocking)
        setGovLoading(true);
        setGovFetchError(null);
        Promise.allSettled([
          MCP.fetchProxyData(cfg.ticker),
          MCP.fetchPeerBenchmarks(cfg.ticker),
        ]).then(([proxyRes, peerRes]) => {
          if (proxyRes.status === "fulfilled") { setGovData(proxyRes.value); setGovFetchError(null); }
          if (peerRes.status  === "fulfilled") setGovPeerData(peerRes.value);
          if (proxyRes.status === "rejected" && peerRes.status === "rejected") {
            setGovFetchError(proxyRes.reason?.message || "MCP server unreachable — ensure api_server.py is running");
            log(`MCP Governance: server unreachable — ${proxyRes.reason?.message || "connection refused"}`);
          } else if (peerRes.status === "rejected") {
            // Peer fetch alone can fail (10-K competitor extraction + per-peer XBRL
            // enrichment is slow) without tripping the "both failed" branch above —
            // log it separately so a partial failure isn't silent.
            log(`MCP Peers: fetch failed — ${peerRes.reason?.message || "unknown error"}`);
          }
          setGovLoading(false);
          if (proxyRes.status === "fulfilled") log(`MCP Governance: proxy data loaded`);
          if (peerRes.status  === "fulfilled") log(`MCP Peers: ${peerRes.value?.peers?.length || 0} peers with data (${peerRes.value?.peer_source || "SIC peers"})`);
        });

        profileRef.current = { ...templateProfile, risks: enrichedRisks };
        setProfile(profileRef.current);
        log(`Profile: ${templateProfile.entity.name} · ${industry} · ${enrichedRisks.length} risks (MCP-scored)`);

      } catch (e) {
        log(`MCP error: ${e.message} · falling back to industry template`);
        setLiveStatus(`MCP unavailable: ${e.message} · industry template`);
        const fallback = RISK_ENGINE.buildProfile(cfg.ticker, null, null, cfg.industry);
        profileRef.current = fallback;
        setProfile(fallback);
      }

    } else {
      // ── Live JS mode or Mock mode ─────────────────────────────────────────────

      // RSS ingest — live fetch only, no simulation fallback
      if (signalSet.has("industry")) {
        try {
          log("Fetching live RSS signals…");
          const feedsDoneRef = [];
          setRssRunProgress({ msg: "Starting…", feedsDone: [] });
          const ingestResult = await RSS_ENGINE.ingestAll({
            enabledFeedIds: rssEnabledFeeds,
            ticker: cfg.ticker,
            onProgress: (msg, feedId, done) => {
              if (done && feedId) feedsDoneRef.push(feedId);
              setRssRunProgress({ msg, feedsDone: [...feedsDoneRef] });
            },
          });
          setRssRunProgress(null);
          const freshSigs = RSS_ENGINE.toSignals(ingestResult);
          currentRssSignals = freshSigs;
          setRssSignals(freshSigs);
          setRssLastUpdated(Date.now());
          log(`RSS: ${freshSigs.length} signals graded from ${ingestResult.filter(r => r.fetchStatus === "ok").length} live feeds`);
        } catch(e) {
          setRssRunProgress(null);
          log(`RSS ingest: ${e.message || "using prior signals"}`);
        }
      }

      // FRED bundled snapshot
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

      // EDGAR direct fetch
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

      // Build company risk profile from EDGAR + industry template
      {
        const industry = cfg.industry || RISK_ENGINE.sic2industry(edgarSic);
        const builtProfile = RISK_ENGINE.buildProfile(cfg.ticker, edgarFin, edgarSic, industry);
        profileRef.current = builtProfile;
        setProfile(builtProfile);
        log(`Profile: ${builtProfile.entity.name} · ${industry} · ${builtProfile.risks.length} risks derived`);
      }

      // 8-K material events via MCP bridge (Live mode — opportunistic)
      if (liveMode) {
        try {
          const eightK = await MCP.fetch8kEvents(cfg.ticker);
          const cemEvs = MCP.map8kToCemEvents(eightK);
          if (cemEvs.length) {
            _capturedCemEvents = cemEvs;
            setEvents(cemEvs);
            cemEvs.forEach(ev => {
              TIERS.filter(t => t.sevs.includes(ev.severity)).forEach(tier => {
                const msg = notifMsgFor(tier, ev);
                const sentAt = Date.now();
                setEvents(prev => prev.map(e => e.id === ev.id
                  ? { ...e, notifs: [...(e.notifs||[]), { tid: tier.id, tier: tier.label, msg, sentAt, status: "sent", ackAt: null }] }
                  : e));
                setNotifLog(prev => [{ tier: tier.label, control: ev.control, msg, status: "sent", sentAt }, ...prev]);
              });
            });
            log(`8-K Events: ${cemEvs.length} material events loaded into CEM`);
            setUnreadCEM(u => u + cemEvs.length);
          }
        } catch(e) { /* 8-K fetch optional in Live mode — requires MCP bridge */ }
      }
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
    const eightKSigs = _capturedCemEvents.map(ev => ({
      src: "SEC 8-K",
      label: `${ev.control} (${ev.filingDate || "recent"})`,
      velocity: ev.severity === "P1" ? 3 : ev.severity === "P2" ? 2 : 1,
      category: ev.category || "",
      area: ev.area || "",
      severity: ev.severity,
      filingDate: ev.filingDate,
      delta: "adverse",
    }));
    const sigsList = [...mockSigs, ...rssSigsFiltered, ...eightKSigs];
    const stage1Trace = buildTrace({
      assumptions: [
        `Ingest signals from ${signalSet.has("edgar") ? "EDGAR" : "no EDGAR"} / ${signalSet.has("industry") ? "industry RSS" : "no RSS"} / ${signalSet.has("fred") ? "FRED" : "no FRED"} / ${signalSet.has("internal") ? "internal KRIs" : "no internal KRIs"}.`,
        "Signal relevance is mapped to risk templates using industry domain keywords and risk category matching.",
      ],
      decisions: [
        `Collected ${sigsList.length} signals from ${signalSet.size} source(s).`,
        `${currentRssSignals.length} industry RSS signal(s) included in the risk feed.`,
      ],
      obstacles: [
        !liveMode && signalSet.has("edgar") ? "EDGAR live data disabled; using cached or mock financial signals." : null,
      ].filter(Boolean),
      conclusion: `${sigsList.length} signals ingested; ${sigsList.filter(s => s.velocity >= 3).length} high-velocity signal(s) identified.`,
    });
    await runStage("s1", { signals: sigsList, sourceCount: signalSet.size, trace: stage1Trace }, 1200);

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
    const counts = adjustedRisks.reduce((acc, r) => { acc[r.rag] = (acc[r.rag] || 0) + 1; return acc; }, {});
    const stage2Trace = buildTrace({
      assumptions: [
        "FRED contractionary indicators increase macro-category risk scores by +0.08 each.",
        "RSS-linked signals add risk pressure at velocity × 0.08 to directly affected risks.",
        "High-velocity industry RSS adds systemic pressure, capped at +0.20 to all risks.",
        eightKSigs.length > 0
          ? `${eightKSigs.length} SEC 8-K filing${eightKSigs.length !== 1 ? "s" : ""} amplify risk scores in matching categories (+0.5 P1 / +0.25 P2 / +0.10 P3, capped +1.5 per risk).`
          : "SEC 8-K material events amplify risk scores in matching categories when present.",
        "RAG thresholds are RED ≥ 7.5, AMBER ≥ 5.0, GREEN < 5.0.",
      ],
      decisions: [
        `Applied signal-driven adjustments to ${adjustedRisks.length} risks.`,
        `Risk appetite threshold set to ${threshold} (${cfg.appetiteLevel}).`,
      ],
      obstacles: [
        breachingIds.length > 0 ? `Risk appetite breached by ${breachingIds.length} risk(s).` : null,
        sigsList.length === 0 ? "No signals were available; using base risk profile only." : null,
      ].filter(Boolean),
      conclusion: `${counts.R || 0} RED, ${counts.A || 0} AMBER, ${counts.G || 0} GREEN after adjustment.`,
    });
    await runStage("s2", { risks: adjustedRisks, riskAppetite: riskAppetiteResult, trace: stage2Trace }, 1500);
    setActiveRailTab("rr");

    // GATE 1 — Risk assessment
    if (hitl.risk) {
      setStageState((prev) => ({ ...prev, s3: "waiting", s4: "waiting", s5: "waiting", s6: "waiting" }));
      const gres = await showGate(1);
      log(gres.ok ? "Gate 1 passed" : `Gate 1 overridden: ${gres.reason}`);
    }
    setStageState((prev) => ({ ...prev, s3: "idle" }));

    // STAGE 3 — Audit scope
    const stage3Objectives = profileRef.current.objectives || [];
    const stage3Hours = stage3Objectives.reduce((sum, o) => sum + (o.hours || 0), 0);
    const stage3Trace = buildTrace({
      assumptions: [
        "Audit objectives are derived from the risk register and template control gaps.",
        "Priority labels indicate estimated effort and risk mitigation urgency.",
      ],
      decisions: [
        `Generated ${stage3Objectives.length} objectives with ${stage3Objectives.filter(o => o.priority === "P1").length} P1 items.`,
        `Planned ${stage3Hours} total audit hours for the scope package.`,
      ],
      obstacles: [
        stage3Objectives.length === 0 ? "No audit objectives generated; review risk template coverage." : null,
      ].filter(Boolean),
      conclusion: `${stage3Objectives.length} objectives ready for scope review; ${stage3Hours} audit hours planned.`,
    });
    await runStage("s3", { objectives: stage3Objectives, trace: stage3Trace }, 1500);

    // GATE 2 — Audit scope
    if (hitl.scope) {
      setStageState((prev) => ({ ...prev, s4: "waiting", s5: "waiting", s6: "waiting" }));
      const gres = await showGate(2);
      log(gres.ok ? "Gate 2 passed" : `Gate 2 overridden: ${gres.reason}`);
    }
    setStageState((prev) => ({ ...prev, s4: "idle" }));

    // STAGE 4 — MAPs
    const stage4Maps = profileRef.current.maps || [];
    const stage4Trace = buildTrace({
      assumptions: [
        "Management action plans target findings linked to high-risk areas.",
        "Projected reduction percentages are estimated from the underlying risk template model.",
      ],
      decisions: [
        `Generated ${stage4Maps.length} MAPs based on identified objectives and risk controls.`,
      ],
      conclusion: `${stage4Maps.length} action plans created.`,
    });
    await runStage("s4", { maps: stage4Maps, trace: stage4Trace }, 1400);

    // STAGE 5 — Closure
    const stage5Closure = profileRef.current.closure || {};
    const stage5Trace = buildTrace({
      assumptions: [
        "Closure evaluates risk reduction using MAP completion and residual risk counts.",
      ],
      decisions: [
        `Projected ${stage5Closure.projected_total_risk_reduction_pct || 0}% total risk reduction.`,
      ],
      conclusion: `${stage5Closure.risks_closed || 0} risks closed and ${stage5Closure.risks_reduced || 0} reduced.`,
    });
    await runStage("s5", { closure: stage5Closure, trace: stage5Trace }, 1200);

    // STAGE 6 — Loop calibration
    const stage6Loop = RISK_ENGINE.buildLoop(adjustedRisks);
    const stage6Trace = buildTrace({
      assumptions: [
        "Loop health is calibrated from RAG counts, risk velocity, and control environment effectiveness.",
      ],
      decisions: [
        `Set next trigger to ${stage6Loop.next_trigger_days || 0} days and captured ${stage6Loop.lessons_learned?.length || 0} lessons learned.`,
      ],
      conclusion: `Loop health ${stage6Loop.loop_health || "—"}; next focus: ${stage6Loop.next_cycle_focus || "re-run risk loop"}.`,
    });
    await runStage("s6", { loop: stage6Loop, trace: stage6Trace }, 1200);
    setActiveRailTab("loop");

    log("Loop complete");

    // Auto-convert pipeline risks to Risk-as-Code on every loop completion.
    // Baselines wording in DB when runId is available (MCP mode), then generates YAML.
    if (mcpMode) {
      const _autoPayload = adjustedRisks.map(r => ({
        ...r,
        included: true,
        current_wording: r.name,
      }));
      if (runIdRef.current) {
        fetch('/api/risk-register/apply-wording', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            run_id: runIdRef.current,
            risks: _autoPayload.map(r => ({ risk_ref: r.id || r.risk_ref || '', current_wording: r.name || '' })),
          }),
        }).catch(() => {});
      }
      fetch('/api/risk-register/convert-to-code', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          risks: _autoPayload,
          review_type: 'internal',
          framework: 'Internal Risk Register',
          include_controls: true,
        }),
      }).then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.yaml) setAutoCodeYaml(d.yaml); })
        .catch(() => {});
    }

    if (mcpMode && runIdRef.current) {
      fetch('/api/mcp/loop/persist', {
        method: 'POST', headers: _authHeaders(),
        body: JSON.stringify({
          run_id: runIdRef.current,
          loop_log: loopLogRef.current,
          objectives: stage3Objectives,
          cem_events: _capturedCemEvents,
          manual_audits: manualAuditsRef.current,
        }),
      }).catch(() => {});
    }

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
      await runStage("s5", { closure: profileRef.current.closure }, 1200);
      const rerunRisks = output.s2?.risks || profileRef.current.risks || [];
      await runStage("s6", { loop: RISK_ENGINE.buildLoop(rerunRisks) }, 1200);
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
    setNarrativeResult(null);
    runIdRef.current = null;
    loopLogRef.current = [];
    manualAuditsRef.current = [];
    setGovData(null);
    setGovPeerData(null);
    setGovLoading(false);
    setGovFetchError(null);
    setAutoCodeYaml(null);
    try { localStorage.removeItem("dendrai.lastLoop"); } catch {}
  }

  function downloadAutoYaml() {
    if (!autoCodeYaml) return;
    const blob = new Blob([autoCodeYaml], { type: 'application/x-yaml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `dendrai_risk-as-code_${cfg.ticker || 'run'}_${new Date().toISOString().split('T')[0]}.yaml`;
    a.click();
    URL.revokeObjectURL(url);
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
    if (activeScreen !== "controls") setUnreadCEM((u) => u + count);
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
      ticker: cfg.ticker,
      runId: runIdRef.current,
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
      stageState,
      stageOutput: output,
      assumptions: [
        `Quarterly score projections use a velocity-dampened linear model: base + (velocity × CE_mult × 1.0 × 0.85^(q−1)), capped at 25.0.`,
        `Control-effectiveness multipliers applied to velocity contribution: NONE=1.20×, WEAK=1.10×, ADEQUATE=0.95×, STRONG=0.80×.`,
        `FRED macro adjustment: each contractionary FRED signal adds +0.08 to macro-category risk scores (${fredContrCount} contractionary signal${fredContrCount !== 1 ? "s" : ""} in this run).`,
        `RSS signal adjustment: linked industry signals add (signal velocity × 0.08) to directly linked risks; risk velocity set to max of base velocity or linked signal velocity.`,
        `High-velocity industry signal adjustment: +0.05 per signal with velocity ≥ 3, capped at +0.20, applied uniformly to all risks (${rssHighVelCount} qualifying signal${rssHighVelCount !== 1 ? "s" : ""} in this run).`,
        `Macro ensemble forecasts use ARIMA + Prophet + Random Forest with FRED series as exogenous features; ensemble weights update iteratively by inverse MAPE from backtesting.`,
        `Random Forest features: lags 1–4, rolling mean and std, time index, quarter dummies, and current FRED series values.`,
        `Likelihood proxy from control effectiveness: NONE→9, WEAK→7, ADEQUATE→5, STRONG→3. Impact proxy from inherent_score field.`,
        `Risk appetite threshold: score ≥ 15 = RED, 9–14 = AMBER, < 9 = GREEN (configured: ${cfg.appetiteLevel || "AMBER"}).`,
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

  // ---- Gate 2 residual risk reductions (for Sankey) ----
  const gate2Reductions = useMemo(() => {
    const map = {};
    (output.s3?.objectives || []).forEach(o => {
      const reduction = o.residualRiskReduction || 0;
      if (!reduction) return;
      const linkedRisks = o.linked_risks || (o.linked_risk ? [o.linked_risk] : []);
      linkedRisks.forEach(rid => { map[rid] = (map[rid] || 0) + reduction; });
    });
    return map;
  }, [output.s3?.objectives]);

  // ---- Governance tab navigation ----
  const selectGovTab = useCallback((tabId) => {
    setActiveGovTab(tabId);
    setActiveScreen("gov");
  }, []);

  // ---- Pipeline sub-tab definitions ----
  // RSS Signals, Forecasts, and MAPs now live inside their respective pipeline stages.
  const pipeTabs = [
    { id: "stages", l: "Pipeline" },
  ];

  const railRisks = output.s2?.risks || (hasRun ? profile.risks : null);
  const railMaps  = output.s4?.maps || null;

  // ---- RENDER ----
  return (
    <div className="app">
      <ErrorBoundary>
      <Header
        cfg={cfg}
        liveMode={liveMode} mcpMode={mcpMode} livefacts={livefacts}
        running={running} hasRun={hasRun}
        entityName={profile.entity.name}
        aiChatLabel={aiChatCfg.buttonLabel || "Ask Claude"}
        chatOpen={chatOpen}
        onChatToggle={() => setChatOpen(v => !v)} />


      <div className={"app-body" + (activeScreen === "pipeline" && (hasRun || output.s2?.risks?.length > 0) ? " has-rail" : "")}>
        <LeftNav
          activeScreen={activeScreen}
          activeGovTab={activeGovTab}
          onNavigate={(screen, govTab) => {
            setActiveScreen(screen);
            if (govTab) setActiveGovTab(govTab);
            if (screen === "controls") setUnreadCEM(0);
          }}
          counts={{
            controls: events.length,
            controlsPulse: unreadCEM > 0,
            maps: output.s4?.maps?.length || 0,
            notifs: notifLog.length,
          }} />

        <main className="main" data-screen-label="Main canvas">

          {/* ---- Configuration / Setup ---- */}
          {activeScreen === "config" && (
            <div className="panel active">
              <ConfigScreen
                cfg={cfg} setCfg={setCfg}
                signalSet={signalSet} setSignalSet={setSignalSet}
                velocity={velocity} setVelocity={setVelocity}
                hitl={hitl} setHitl={setHitl}
                liveMode={liveMode} setLiveMode={setLiveMode}
                mcpMode={mcpMode} setMcpMode={setMcpMode}
                useDb={useDb} setUseDb={setUseDb}
                liveStatus={liveStatus}
                lastSaved={lastSaved}
                rssEnabledFeeds={rssEnabledFeeds}
                setRssEnabledFeeds={setRssEnabledFeeds}
                aiChatCfg={aiChatCfg}
                setAiChatCfg={setAiChatCfg}
                colorScheme={tweaks.colorScheme || "system"}
                setColorScheme={(v) => setTweak("colorScheme", v)} />
            </div>
          )}

          {/* ---- UBO Configuration ---- */}
          {activeScreen === "uboconfig" && (
            <div className="panel active">
              <UboConfigScreen />
            </div>
          )}

          {/* ---- Pipeline (with action bar + sub-tabs) ---- */}
          {activeScreen === "pipeline" && (
          <div className="panel active">
            {/* Action bar — primary verbs live with the pipeline they drive */}
            <div className="pipe-action-bar">
              <button className="btn btn-acc" disabled={running} onClick={runLoop}>
                {running ? <><span className="spin"/> Running loop…</> : <><Icon name="play" size={12}/> Run Loop</>}
              </button>
              <button className="btn" disabled={!hasRun} onClick={() => setReportOpen(true)}><Icon name="doc" size={11}/> Loop Report</button>
              <button className="btn" disabled={!hasRun} onClick={() => setPersonaOpen(true)}><Icon name="user" size={11}/> Persona</button>
              {autoCodeYaml && (
                <button className="btn btn-acc" onClick={downloadAutoYaml} title="Download auto-generated Risk-as-Code YAML">
                  <Icon name="download" size={11}/> Risk-as-Code
                </button>
              )}
              <div style={{flex:1}} />
              {hasRun && <button className="btn btn-ghost" onClick={resetAll}><Icon name="reset" size={11}/> Reset</button>}
            </div>

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

            {/* Sub-tabs — hidden when only one tab exists */}
            {pipeTabs.length > 1 && (
              <div className="pipe-sub-tabs">
                {pipeTabs.map(t => (
                  <button key={t.id}
                    className={"pipe-sub-tab" + (activePipeTab === t.id ? " active" : "")}
                    onClick={() => setActivePipeTab(t.id)}>
                    {t.l}
                  </button>
                ))}
              </div>
            )}

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
                rssRunProgress={rssRunProgress}
                rssFeeds={RSS_ENGINE.FEEDS.filter(f => rssEnabledFeeds.includes(f.id))}
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
                onAddRisk={addRisk}
                scopeApprovals={scopeApprovals}
                onApproveObjective={approveObjective}
                onOpenAdjustObjective={openAdjustObjective}
                onApproveAllObjectives={approveAllRemainingObjectives}
                onAddObjective={addObjective}
                manualAudits={manualAudits}
                onAddAudit={addManualAudit}
                onRemoveAudit={removeManualAudit}
                narrativeResult={narrativeResult}
                onNarrativeResult={setNarrativeResult}
                forecasts={profile.forecasts}
                ticker={cfg.ticker || ""}
                liveMode={liveMode}
                fredSeries={fredLive}
                industry={hasRun ? profile.entity?.industry : cfg.industry}
                enabledFeedIds={rssEnabledFeeds}
                onRssSignalsReady={(sigs) => {
                  setRssSignals(sigs);
                  log(`RSS ingestion complete — ${sigs.length} velocity signals graded`);
                }}
                flowMeta={profile.riskFlow}
                onOpenMainFlow={() => setActiveScreen("flow")}
                risks={output.s2?.risks || profile?.risks || []}
                companyName={profile?.entity?.name || ""}
                peerData={govPeerData} />
            )}
          </div>
          )}

          {/* Risk Register, Risk Flow, Forecasts and Scenarios now live in the
              right-hand Live Register rail (rendered below, post-run). */}

          {/* ---- Controls Monitor ---- */}
          {activeScreen === "controls" && (
          <div className="panel active">
            <CEMPanel
              events={events} setEvents={setEvents}
              filter={cemFilter} setFilter={setCemFilter}
              expanded={cemExpanded} setExpanded={setCemExpanded}
              onAckNotif={ackNotif}
              onInject={() => fireSyntheticEvent(1)}
              ticker={cfg.ticker} />
          </div>
          )}

          {/* ---- UBO Governance Brain ---- */}
          {activeScreen === "ubogov" && (
          <div className="panel active">
            <UBOGovPanel />
          </div>
          )}

          {/* ---- Risk Flow ---- */}
          {activeScreen === "flow" && (
          <div className="panel active">
            <FlowPanel
              risks={output.s2?.risks || (hasRun ? profile.risks : null)}
              maps={output.s4?.maps || (hasRun ? profile.maps : null)}
              flowMeta={hasRun ? profile.riskFlow : null}
              objectives={output.s3?.objectives || []}
              gate2Reductions={gate2Reductions}
              selectedId={selectedRiskId} setSelectedId={setSelectedRiskId}
              liveMode={liveMode}
              rssSignals={rssSignals}
              fredData={profile.forecasts?.fred}
              appetiteThreshold={APPETITE_THRESHOLDS[cfg.appetiteLevel] ?? 7.5} />
          </div>
          )}

          {/* ---- MAPs ---- */}
          {activeScreen === "maps" && (
          <div className="panel active">
            <div className="panel-head">
              <div>
                <div className="kicker">Execution</div>
                <div className="panel-title mt-8">Management Action Plans</div>
                <div className="panel-sub">Findings, owners, due dates, and completion across the register.</div>
              </div>
            </div>
            <MapsTab maps={railMaps}/>
          </div>
          )}

          {/* ---- Notifications ---- */}
          {activeScreen === "notifs" && (
          <div className="panel active">
            <div className="panel-head">
              <div>
                <div className="kicker">Execution</div>
                <div className="panel-title mt-8">Notifications</div>
                <div className="panel-sub">Tiered stakeholder cascade from the Control Event Monitor.</div>
              </div>
            </div>
            <NotifTab log={notifLog}/>
          </div>
          )}

          {/* ---- Audit Scope ---- */}
          {activeScreen === "scope" && (
          <div className="panel active">
            <AuditScopeScreen
              objectives={output.s3?.objectives?.length ? output.s3.objectives
                : savedAuditScope?.objectives?.length ? savedAuditScope.objectives
                : (hasRun ? profile.objectives : [])}
              maps={railMaps}
              risks={railRisks}
              hasRun={hasRun}
              savedRunAt={!output.s3?.objectives?.length ? savedAuditScope?.run_at : null} />
          </div>
          )}

          {/* ---- Approval Inbox ---- */}
          {activeScreen === "approvals" && (
          <div className="panel active">
            <ApprovalInboxScreen />
          </div>
          )}

          {/* ---- Coverage Gap Analysis ---- */}
          {activeScreen === "coverage" && (
          <div className="panel active" style={{overflow:"auto"}}>
            <CoverageGapPanel
              risks={output.s2?.risks || (hasRun ? profile.risks : [])}
              objectives={output.s3?.objectives || (hasRun ? profile.objectives : [])}
              rssSignals={rssSignals}
              ratios={profile.ratios || {}}
              industry={hasRun ? profile.entity?.industry : cfg.industry}
              ticker={cfg.ticker} />
          </div>
          )}

          {/* ---- Risk-as-Code ---- */}
          {activeScreen === "riskcode" && (
          <div className="panel active">
            <RiskAsCodeScreen
              risks={output.s2?.risks || (hasRun ? profile.risks : null)}
              baseRisks={profile.risks} />
          </div>
          )}

          {/* ---- Risk Register Review (Phases 2-4) ---- */}
          {activeScreen === "rrreview" && (
          <div className="panel active">
            <RiskRegisterReviewScreen
              risks={output.s2?.risks || (hasRun ? profile.risks : null)}
              runId={runIdRef.current}
              ticker={cfg.ticker}
              onConverted={(updatedRisks) => {
                if (updatedRisks?.length) {
                  setOutput(prev => ({
                    ...prev,
                    s2: { ...(prev.s2 || {}), risks: updatedRisks },
                  }));
                }
              }} />
          </div>
          )}

          {/* ---- Risks as Code (Industry Frameworks) ---- */}
          {activeScreen === "frameworks" && (
          <div className="panel active">
            <RisksAsCodeLiveScreen
              risks={output.s2?.risks || (hasRun ? profile.risks : null)}
              objectives={output.s3?.objectives || (hasRun ? profile.objectives : [])}
              maps={output.s4?.maps || (hasRun ? profile.maps : null)}
              signals={output.s1?.signals || []}
              ratios={profile.ratios || {}}
              ticker={cfg.ticker}
              industry={cfg.industry}
              period={cfg.periodEnd}
              runId={runIdRef.current} />
          </div>
          )}

          {/* ---- Policy-as-Code ---- */}
          {activeScreen === "policycode" && (
          <div className="panel active">
            <PolicyAsCodeScreen
              events={events}
              maps={railMaps}
              risks={railRisks}
              appetiteThreshold={APPETITE_THRESHOLDS[cfg.appetiteLevel] ?? 7.5} />
          </div>
          )}

          {/* ---- Grey Swan Scenarios ---- */}
          {activeScreen === "scenarios" && (
          <div className="panel active">
            <ScenariosPanel
              scenarios={profile.scenarios}
              greySwan={profile.greySwan}
              reverseStress={profile.reverseStress}
              historicalAnalogs={profile.historicalAnalogs}
              governanceScenario={profile.governanceScenario} />
          </div>
          )}

          {/* ---- Scenario Analysis (VaR/CVaR, sensitivity, stress, liquidity, EWI) ---- */}
          {activeScreen === "scenarioanalysis" && (
          <div className="panel active">
            <ScenarioAnalysisScreen
              ticker={cfg.ticker}
              hasRun={hasRun}
              varCvar={profile.varCvar}
              sensitivity={profile.sensitivity}
              multiFactorStress={profile.multiFactorStress}
              liquidityRunway={profile.liquidityRunway}
              earlyWarning={profile.earlyWarning} />
          </div>
          )}

          {/* ---- SOX Scope ---- */}
          {activeScreen === "sox" && (
          <div className="panel active" style={{overflow: "auto"}}>
            <SoxScopePanel
              ticker={cfg.ticker}
              runId={runIdRef.current}
              forecasts={profile.forecasts}
              risks={output.s2?.risks || (hasRun ? profile.risks : []) || []}
              ratios={profile.ratios || {}}
              hasRun={hasRun} />
          </div>
          )}

          {/* ---- Governance Intelligence ---- */}
          {activeScreen === "gov" && (
          <div className="panel gov-panel active">
            <GovernanceView
              data={govData}
              peerData={govPeerData}
              ticker={cfg.ticker}
              loading={govLoading}
              activeTab={activeGovTab}
              onTabChange={setActiveGovTab}
              govFetchError={govFetchError} />
          </div>
          )}
        </main>

        {/* ---- Live Register rail — Pipeline screen, visible from Stage 2 onward ---- */}
        {activeScreen === "pipeline" && (hasRun || output.s2?.risks?.length > 0) && (
          <Rail
            activeTab={activeRailTab}
            setActiveTab={setActiveRailTab}
            output={output}
            risks={railRisks}
            maps={railMaps}
            loop={output.s6?.loop || null}
            notifLog={notifLog}
            forecasts={profile.forecasts}
            scenarios={profile.scenarios}
            greySwan={profile.greySwan}
            flowMeta={profile.riskFlow}
            activeQuarter={activeQuarter}
            setActiveQuarter={setActiveQuarter}
            selectedRiskId={selectedRiskId}
            setSelectedRiskId={setSelectedRiskId}
            selectedPersona={selectedPersona}
            setSelectedPersona={setSelectedPersona}
            personas={profile.personas}
            onOpenMainFlow={() => setActiveScreen("flow")}
            periodBegin={cfg.periodBegin}
            periodEnd={cfg.periodEnd}
            objectives={output.s3?.objectives || []}
            gate2Reductions={gate2Reductions}
            appetiteThreshold={APPETITE_THRESHOLDS[cfg.appetiteLevel] ?? 7.5}
            liveMode={liveMode}
            livefacts={livefacts}
            fredSeries={fredLive}
            rssSignals={rssSignals}
            industry={hasRun ? profile.entity?.industry : cfg.industry}
            ticker={cfg.ticker}
            loopStats={output.s6 || output.s6?.loop || {}}
            runId={runIdRef.current} />
        )}

      </div>

      {personaOpen && (
        <div className="pm-overlay" onClick={() => setPersonaOpen(false)}>
          <div className="pm-card" onClick={e => e.stopPropagation()}>
            <div className="pm-head">
              <div className="pm-title">Persona Report</div>
              <button className="btn btn-sm btn-ghost" onClick={() => setPersonaOpen(false)}><Icon name="x" size={12}/></button>
            </div>
            <div className="pm-body">
              <PersonaTab personas={hasRun ? profile.personas : null} selected={selectedPersona} setSelected={setSelectedPersona}
                ticker={cfg.ticker} risks={output.s2?.risks || profile.risks || []}
                loopStats={output.s6 || loop || {}} runId={runIdRef.current}/>
            </div>
          </div>
        </div>
      )}

      <ReportModal open={reportOpen} onClose={() => setReportOpen(false)} payload={reportPayload} />
      <OverrideModal open={overrideOpen} gateNum={overrideGateNum} onClose={() => setOverrideOpen(false)} onConfirm={confirmOverride} />
      <AdjustRiskModal
        open={adjustOpen}
        risk={(output.s2?.risks || []).find(r => r.id === adjustingRiskId)}
        risks={output.s2?.risks || []}
        ticker={cfg.ticker}
        runId={runIdRef.current}
        narrativeResult={narrativeResult}
        onClose={() => { setAdjustOpen(false); setAdjustingRiskId(null); }}
        onSubmit={submitAdjustment} />
      <AdjustObjectiveModal
        open={adjustObjOpen}
        obj={(output.s3?.objectives || profile.objectives || []).find(o => o.id === adjustingObjId)}
        risks={output.s2?.risks || []}
        ticker={cfg.ticker}
        runId={runIdRef.current}
        onClose={() => { setAdjustObjOpen(false); setAdjustingObjId(null); }}
        onSubmit={submitObjAdjustment} />

      <DendraiTweaks tweaks={tweaks} setTweak={setTweak}
        hitl={hitl} setHitl={setHitl}
        velocity={velocity} setVelocity={setVelocity} />

      <AiChatPanel
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        provider={aiChatCfg.provider || "claude"}
        buttonLabel={aiChatCfg.buttonLabel || "Ask Claude"}
        ticker={cfg.ticker}
        industry={cfg.industry}
        output={output}
        useDb={useDb} setUseDb={setUseDb} />
      </ErrorBoundary>
    </div>);

}

// ---- Header ----
function Header({ cfg, liveMode, mcpMode, livefacts, running, hasRun, entityName,
                  aiChatLabel, chatOpen, onChatToggle }) {
  const auth = window.useAuth ? window.useAuth() : null;
  const [userMenuOpen, setUserMenuOpen] = React.useState(false);
  const [orgUsers, setOrgUsers] = React.useState([]);
  const [managerId, setManagerId] = React.useState(auth?.user?.manager_id ?? "");
  const [savingManager, setSavingManager] = React.useState(false);

  React.useEffect(() => {
    setManagerId(auth?.user?.manager_id ?? "");
  }, [auth?.user?.manager_id]);

  React.useEffect(() => {
    if (!userMenuOpen || orgUsers.length) return;
    fetch("/auth/users", { credentials: "include" })
      .then(r => r.ok ? r.json() : { users: [] })
      .then(d => setOrgUsers(d.users || []))
      .catch(() => {});
  }, [userMenuOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  async function saveManager(e) {
    const val = e.target.value;
    setManagerId(val);
    setSavingManager(true);
    try {
      await fetch("/auth/users/me/manager", {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ manager_id: val ? Number(val) : null }),
      });
    } catch (_) {}
    setSavingManager(false);
  }

  return (
    <header className="hdr">
      <div className="hdr-brand">
        <div className="hdr-logo">D</div>
        <div className="hdr-name">Dendrai <span>Intelligenza</span></div>
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
          <Icon name={mcpMode ? "gear" : liveMode ? "wifi" : "satellite"} size={12} className="muted" />
          <span className="val">{mcpMode ? "MCP" : liveMode ? "LIVE" : "MOCK"}</span>
        </div>
        <div className="item">
          <span className="muted">Cycle</span>
          <span className="val">28d</span>
        </div>
      </div>
      <button
        className={"hdr-ai-btn" + (chatOpen ? " active" : "")}
        onClick={onChatToggle}
        title={chatOpen ? "Close AI chat" : "Open AI chat"}
      >
        <Icon name="spark" size={11} />
        {aiChatLabel || "Ask Claude"}
      </button>
      {auth?.user && (
        <div style={{ position: "relative" }}>
          <button
            className="hdr-user-btn"
            onClick={() => setUserMenuOpen(o => !o)}
            title={`Signed in as ${auth.user.username}`}
          >
            <Icon name="user" size={11} />
            <span className="hdr-user-name">{auth.user.username}</span>
            <Icon name={userMenuOpen ? "chev-u" : "chev-d"} size={10} />
          </button>
          {userMenuOpen && (
            <>
              <div onClick={() => setUserMenuOpen(false)}
                style={{ position: "fixed", inset: 0, zIndex: 49 }} />
              <div style={{
                position: "absolute", top: "100%", right: 0, marginTop: 6, zIndex: 50,
                background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8,
                padding: "12px 14px", minWidth: 230, boxShadow: "0 8px 24px rgba(0,0,0,0.14)",
              }}>
                <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginBottom: 10 }}>
                  Signed in as <b style={{ color: "var(--ink)" }}>{auth.user.username}</b>
                </div>
                <label className="mono" style={{ display: "block", fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 4 }}>
                  MY MANAGER
                </label>
                <select value={managerId} onChange={saveManager} disabled={savingManager}
                  className="fi-input" style={{ width: "100%", fontSize: 11.5, marginBottom: 8, boxSizing: "border-box" }}>
                  <option value="">— none set —</option>
                  {orgUsers.filter(u => u.id !== auth.user.id).map(u => (
                    <option key={u.id} value={u.id}>{u.display_name || u.username}</option>
                  ))}
                </select>
                <div style={{ fontSize: 9.5, color: "var(--ink-4)", marginBottom: 12, lineHeight: 1.4 }}>
                  HITL adjustments you submit for Enterprise Risk and SOX gates route to this person for review.
                </div>
                <button className="btn btn-sm" style={{ width: "100%" }} onClick={auth.logout}>
                  <Icon name="logout" size={11} /> Sign out
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </header>);

}

// Expose Header to window for JSX usage
window.Header = Header;

export default App;