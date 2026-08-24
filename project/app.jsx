/* ============================================================
   Dendrai Intelligenza — main App
   Orchestrates: run-loop animation, HITL gates, CEM, modals,
   data mode (mock/live), tweaks.
   ============================================================ */

import { lazyGlobal } from './src/lazy-screen.js';

// Screens reachable only through the activeScreen switch below are
// code-split: each chunk is fetched on first navigation, not at startup.
// (code-screens.jsx backs "policycode" and "riskcode";
// sox-scope.jsx needs sox-hitl.jsx loaded alongside it since it renders
// SoxGate1Review/SoxGate2Review/SoxGateBanner unguarded. cem.jsx and
// risk-register-review.jsx are NOT split, despite backing their own
// screens too — see the note in src/main.jsx for why.)
const ConfigScreenLazy = lazyGlobal(() => import('./config-screen.jsx'), 'ConfigScreen');
const UboConfigScreenLazy = lazyGlobal(() => import('./ubo-config.jsx'), 'UboConfigScreen');
const UserConfigScreenLazy = lazyGlobal(() => import('./user-config.jsx'), 'UserConfigScreen');
const TokenUsageScreenLazy = lazyGlobal(() => import('./token-usage.jsx'), 'TokenUsageScreen');
const ModelHealthScreenLazy = lazyGlobal(() => import('./model-health.jsx'), 'ModelHealthScreen');
const ContinuousMonitoringScreenLazy = lazyGlobal(() => import('./continuous-monitoring.jsx'), 'ContinuousMonitoringScreen');
const RiskQuantificationScreenLazy = lazyGlobal(() => import('./fair-quantification.jsx'), 'RiskQuantificationScreen');
const ExceptionsScreenLazy = lazyGlobal(() => import('./exceptions.jsx'), 'ExceptionsScreen');
const InfrastructureMonitoringScreenLazy = lazyGlobal(() => import('./infrastructure-monitoring.jsx'), 'InfrastructureMonitoringScreen');
const AiInventoryScreenLazy = lazyGlobal(() => import('./ai-inventory.jsx'), 'AiInventoryScreen');
const AiGovernanceScreenLazy = lazyGlobal(() => import('./ai-governance.jsx'), 'AiGovernanceScreen');
const EvidenceQualityScreenLazy = lazyGlobal(() => import('./evidence-quality.jsx'), 'EvidenceQualityScreen');
const FlowPanelLazy = lazyGlobal(() => import('./flow.jsx'), 'FlowPanel');
const AuditScopeScreenLazy = lazyGlobal(() => import('./audit-scope.jsx'), 'AuditScopeScreen');
const ApprovalInboxScreenLazy = lazyGlobal(() => import('./approval-inbox.jsx'), 'ApprovalInboxScreen');
const RiskAsCodeScreenLazy = lazyGlobal(() => import('./code-screens.jsx'), 'RiskAsCodeScreen');
const PolicyAsCodeScreenLazy = lazyGlobal(() => import('./code-screens.jsx'), 'PolicyAsCodeScreen');
const RegulatoryChangeScreenLazy = lazyGlobal(() => import('./regulatory-change.jsx'), 'RegulatoryChangeScreen');
const ScenariosPanelLazy = lazyGlobal(() => import('./scenarios.jsx'), 'ScenariosPanel');
const ScenarioAnalysisScreenLazy = lazyGlobal(() => import('./scenario-analysis.jsx'), 'ScenarioAnalysisScreen');
const SoxScopePanelLazy = lazyGlobal(() => Promise.all([import('./sox-scope.jsx'), import('./sox-hitl.jsx')]), 'SoxScopePanel');
const GovernanceViewLazy = lazyGlobal(() => import('./governance.jsx'), 'GovernanceView');
const PostureTrendScreenLazy = lazyGlobal(() => import('./posture-trend.jsx'), 'PostureTrendPanel');
const HelpScreenLazy = lazyGlobal(() => import('./help.jsx'), 'HelpScreen');

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
  "persona": "Chief Audit Executive",
  "colorScheme": "system",
  "digestFrequency": "off"
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

// Guards the Pipeline/Assess Risk autosave effects below (dendrai.config,
// dendrai.lastLoop:<ticker>) against "Converting circular structure to JSON"
// crashes. Those payloads carry large, largely-external object graphs
// (profile, output, narrativeResult) built up over a run; if any field ever
// ends up holding a live DOM/Window reference (a chart lib's internal ref, a
// stray event, etc.) instead of plain data, a raw JSON.stringify throws and
// takes the whole autosave — and the screen using it — down with it. This
// drops any BOM object (Window/Document/Node) and de-dupes an already-seen
// object (breaking a genuine reference cycle) instead of throwing, so a
// stray non-serializable reference degrades to a dropped field, not a crash.
// Does not fix the root cause — see the call sites' comments — just keeps
// autosave from being fatal while that's tracked down.
function _safeStringify(value) {
  const seen = new WeakSet();
  try {
    return JSON.stringify(value, (key, v) => {
      if (v !== null && typeof v === "object") {
        if ((typeof Window !== "undefined" && v instanceof Window) ||
            (typeof Node !== "undefined" && v instanceof Node)) {
          return undefined;
        }
        if (seen.has(v)) return undefined;
        seen.add(v);
      }
      return v;
    });
  } catch (e) {
    console.warn("_safeStringify: falling back to {} — payload still not serializable:", e);
    return "{}";
  }
}

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

  // ---- Background-prefetch lazy screen chunks ----
  // Every screen reachable through nav.jsx/the workflow stepper other than
  // the ones eagerly bundled in src/main.jsx is code-split (React.lazy, see
  // src/lazy-screen.js) — the first navigation to any of them pays for a
  // fresh network fetch + parse of its chunk before it can render. That's
  // the "lag on first open" this warms away. Dynamic import() is idempotent
  // and cached per specifier by the browser/Vite, so calling it here ahead
  // of time just primes that cache; the same lazyGlobal() call the actual
  // screen navigation triggers later resolves instantly from it. Fires once
  // after first paint via requestIdleCallback, one chunk per idle slot, so
  // it never competes with the landing screen's own data fetches or with
  // interaction responsiveness — falls back to a short setTimeout chain on
  // browsers without requestIdleCallback (Safari).
  useEffect(() => {
    const loaders = [
      () => import('./config-screen.jsx'),
      () => import('./ubo-config.jsx'),
      () => import('./user-config.jsx'),
      () => import('./token-usage.jsx'),
      () => import('./model-health.jsx'),
      () => import('./continuous-monitoring.jsx'),
      () => import('./infrastructure-monitoring.jsx'),
      () => import('./ai-inventory.jsx'),
      () => import('./ai-governance.jsx'),
      () => import('./evidence-quality.jsx'),
      () => import('./flow.jsx'),
      () => import('./audit-scope.jsx'),
      () => import('./approval-inbox.jsx'),
      () => import('./code-screens.jsx'), // backs both RiskAsCode and PolicyAsCode
      () => import('./scenarios.jsx'),
      () => import('./scenario-analysis.jsx'),
      () => Promise.all([import('./sox-scope.jsx'), import('./sox-hitl.jsx')]),
      () => import('./governance.jsx'),
      () => import('./posture-trend.jsx'),
      () => import('./help.jsx'),
    ];
    let cancelled = false;
    let i = 0;
    const schedule = window.requestIdleCallback
      ? (fn) => window.requestIdleCallback(fn, { timeout: 2000 })
      : (fn) => setTimeout(fn, 300);
    function prefetchNext() {
      if (cancelled || i >= loaders.length) return;
      const loader = loaders[i++];
      // A failed prefetch (offline, flaky connection) just means the real
      // navigation fetches it fresh later — never surface this to the user.
      loader().catch(() => {});
      schedule(prefetchNext);
    }
    schedule(prefetchNext);
    return () => { cancelled = true; };
  }, []);

  // ---- Sidebar config ----
  const [cfg, setCfg] = useState({
    ticker: "ON",
    industry: "Semiconductors",
    focus: ["Revenue Recognition"],
    periodBegin: `Q1 ${new Date().getFullYear()}`,
    periodEnd: `Q4 ${new Date().getFullYear()}`,
    appetiteLevel: "AMBER",
  });
  const [signalSet, setSignalSet] = useState(new Set(["edgar", "peers", "industry", "internal", "fred", "incidents"]));
  const [velocity, setVelocity] = useState(3);
  const [hitl, setHitl] = useState({ risk: true, scope: true, map: false });
  const [rssEnabledFeeds, setRssEnabledFeeds] = useState(() => RSS_ENGINE.FEEDS.map(f => f.id));

  // ---- AI Chat (declared here so it's before the persistence effects that include it) ----
  const [aiChatCfg, setAiChatCfg] = useState({ provider: "claude", buttonLabel: "Ask Claude" });
  const [chatOpen, setChatOpen] = useState(false);
  const [chatSeedQuestion, setChatSeedQuestion] = useState(null);

  // ---- Config persistence (DB primary, localStorage fallback) ----
  const [lastSaved, setLastSaved] = useState(null);
  const cfgLoadedRef = useRef(false);
  // Toast only on the transition into a failing streak, not once per
  // keystroke-triggered autosave while the backend stays down.
  const configSaveFailingRef = useRef(false);
  const loopSaveFailingRef = useRef(false);
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
    try { localStorage.setItem("dendrai.config", _safeStringify(payload)); } catch {}
    setLastSaved(savedAt);
    fetch("/api/mcp/config/pipeline", {
      method: "PUT",
      headers: _authHeaders(),
      body: _safeStringify(payload),
    }).then(res => {
      if (res.ok) { configSaveFailingRef.current = false; return; }
      throw new Error(`HTTP ${res.status}`);
    }).catch(() => {
      // Still saved to localStorage above, so no data is lost — this is
      // purely "your config isn't syncing to the server right now."
      if (!configSaveFailingRef.current) {
        configSaveFailingRef.current = true;
        window.showToast?.("Couldn't sync your configuration to the server — saved locally, will retry.", { tone: "warn" });
      }
    });
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
  // Overview Hub focus: null = hub overview, else a node id ("s1".."s6" |
  // "g1" | "g2"). Auto-follows the active stage while a run is in progress
  // (runStage below) unless the user manually navigated away — hubPinnedRef
  // tracks that without triggering a re-render. A pending gate always wins
  // focus regardless of pinned, since showGate() awaits a promise the user
  // must resolve before the run can continue — the screen can never be
  // showing something else while that's true.
  const [hubFocus, setHubFocus] = useState(null);
  const hubPinnedRef = useRef(false);
  const goToHub = useCallback(() => { hubPinnedRef.current = running; setHubFocus(null); }, [running]);
  const goToNode = useCallback((id) => { hubPinnedRef.current = running; setHubFocus(id); }, [running]);

  // Pending gate promise resolvers (so the run sequence can await user action)
  const gateResRef = useRef({});
  const runIdRef = useRef(null);
  const loopLogRef = useRef([]);
  const manualAuditsRef = useRef([]);

  // Auto-generated Risk-as-Code YAML after each loop completion
  const [autoCodeYaml, setAutoCodeYaml] = useState(null);

  // ---- Tabs ----
  // Default landing screen is the Intelligenza Workflow guide; config|pipeline|
  // register|controls|flow|maps|notifs|scope|riskcode|policycode|gov
  //
  // Restored from localStorage so a refresh keeps you where you were. It used
  // to reset to "help" unconditionally, which made refreshing to check whether
  // something had actually saved genuinely misleading: you'd land back on the
  // workflow guide, navigate somewhere, and be unsure what you were looking
  // at. Validated against the nav so a stale or renamed id can't leave the
  // app rendering an empty shell.
  const [activeScreen, setActiveScreen] = useState(() => {
    try {
      const saved = window.localStorage.getItem("dendrai.activeScreen");
      const valid = (window.NAV_SECTIONS || [])
        .flatMap(s => (s.items || []).map(i => i.id));
      return saved && (valid.length === 0 || valid.includes(saved)) ? saved : "help";
    } catch {
      return "help";   // private mode / storage disabled
    }
  });
  useEffect(() => {
    try { window.localStorage.setItem("dendrai.activeScreen", activeScreen); } catch {}
  }, [activeScreen]);

  // Session-only screen-visit tally, for the anticipatory help nudge (see
  // HelpNudge below) — watches activeScreen directly rather than instrumenting
  // every individual onNavigate callsite (nav rail, WorkflowStrip,
  // navigateToScreen, NextActionRail all change activeScreen independently),
  // so no navigation path can silently go untracked.
  const [screenVisitCounts, setScreenVisitCounts] = useState({});
  useEffect(() => {
    setScreenVisitCounts(prev => ({ ...prev, [activeScreen]: (prev[activeScreen] || 0) + 1 }));
  }, [activeScreen]);
  // ---- Command palette (Cmd/Ctrl+K) ----
  const [paletteOpen, setPaletteOpen] = useState(false);
  useEffect(() => {
    function onKeyDown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(o => !o);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);
  // Same navigation side-effects LeftNav's onNavigate triggers (clear the
  // per-screen deep-link seeds so a stale filter/tab from a previous
  // navigation doesn't leak into an unrelated screen) — shared so the
  // command palette lands exactly like a normal nav click, not a shortcut
  // with different behavior.
  const handleLeftNavigate = useCallback((screen, govTab) => {
    setActiveScreen(screen);
    if (govTab) setActiveGovTab(govTab);
    if (screen === "controls") setUnreadCEM(0);
    setCemInitialTab(null);
    setCemInitialFilter(null);
    setPacInitialProcess(null);
  }, []);

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
        const ingestResult = await RSS_ENGINE.ingestAll({
          enabledFeedIds: rssEnabledFeeds, ticker: cfg.ticker,
          companyName: profileRef.current?.entity?.name || cfg.ticker,
        });
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
  // Approval Inbox badge — combines gate items routed to this user, the
  // broadcast UBO™ telemetry human-review queue, and the AI narrative
  // review queue (persona_brief/audit_report awaiting their pre-delivery
  // check — MODEL_CARD.md known limitation #3), all visible to everyone,
  // first reviewer resolves each for all. Polls independently of which
  // screen is open, unlike Control Tower's own 5s poll which only runs
  // while that screen is mounted — this is the point of a nav badge.
  const [approvalInboxCount, setApprovalInboxCount] = useState(0);
  // Split out from the combined badge above so the workflow strip can show
  // a live "needs you" count per stage (Risk Intelligence = pending gate
  // approvals, Monitoring Intelligence = UBO™ telemetry human-review) —
  // same poll, no new endpoint. aiCount (persona-brief/audit-report review)
  // doesn't map cleanly to one stage, so it stays folded into
  // approvalInboxCount only.
  const [riskGateCount, setRiskGateCount] = useState(0);
  const [monitoringReviewCount, setMonitoringReviewCount] = useState(0);
  useEffect(() => {
    let cancelled = false;
    async function pollApprovals() {
      try {
        const [gateRes, telRes, aiRes] = await Promise.all([
          fetch("/approvals/inbox", { credentials: "include" }),
          fetch(`${window.MCP_API_BASE || "/api/mcp"}/observability/telemetry/human-review`, { credentials: "include" }),
          fetch(`${window.MCP_API_BASE || "/api/mcp"}/ai/review-queue`, { credentials: "include" }),
        ]);
        const gateCount = gateRes.ok ? ((await gateRes.json()).items || []).length : 0;
        const telCount  = telRes.ok  ? ((await telRes.json()).count ?? 0) : 0;
        const aiCount   = aiRes.ok   ? ((await aiRes.json()).count ?? 0) : 0;
        if (!cancelled) {
          setApprovalInboxCount(gateCount + telCount + aiCount);
          setRiskGateCount(gateCount);
          setMonitoringReviewCount(telCount);
        }
      } catch { /* best-effort — badge just stays at its last known value */ }
    }
    pollApprovals();
    const t = setInterval(pollApprovals, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);
  // Scheduled digest notifications (Feature 5) — same lazy-poll shape as the
  // approvals badge above. check-due is a no-op server-side unless the
  // user's digestFrequency preference interval has elapsed and a new
  // completed run exists for the current ticker, so this is cheap to poll.
  const [digests, setDigests] = useState([]);
  const [unreadDigestCount, setUnreadDigestCount] = useState(0);
  useEffect(() => {
    let cancelled = false;
    async function pollDigests() {
      if (!cfg.ticker) return;
      try {
        const res = await fetch("/api/digests/check-due", {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticker: cfg.ticker }),
        });
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;
        setUnreadDigestCount(data.unread_count || 0);
        if (data.generated && data.digest) setDigests(prev => [data.digest, ...prev]);
      } catch { /* best-effort — badge just stays at its last known value */ }
    }
    pollDigests();
    const t = setInterval(pollDigests, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [cfg.ticker]);
  // Full digest history — fetched lazily on opening the Notifications screen,
  // same fetch-on-tab-select pattern as the Coverage tab (code-screens.jsx).
  useEffect(() => {
    if (activeScreen !== "notifs") return;
    fetch("/api/digests", { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) { setDigests(data.digests || []); setUnreadDigestCount(data.unread_count || 0); } })
      .catch(() => {});
  }, [activeScreen]);
  function markDigestRead(id) {
    setDigests(prev => prev.map(d => d.id === id ? { ...d, read_at: new Date().toISOString() } : d));
    setUnreadDigestCount(c => Math.max(0, c - 1));
    fetch(`/api/digests/${id}/read`, { method: "POST", credentials: "include" }).catch(() => {});
  }
  // Deep-link targets for click-through from Continuous Monitoring — only
  // read as each target screen's *initial* tab/process on mount, so a
  // stale value can't stick around: the plain left-nav onNavigate below
  // always clears both, and only navigateToScreen (used by Continuous
  // Monitoring's click-throughs) sets them.
  const [cemInitialTab, setCemInitialTab] = useState(null);
  const [cemInitialFilter, setCemInitialFilter] = useState(null);
  const [pacInitialProcess, setPacInitialProcess] = useState(null);
  const navigateToScreen = useCallback((screen, opts = {}) => {
    if (opts.cemTab) setCemInitialTab(opts.cemTab);
    if (opts.cemFilter) setCemInitialFilter(opts.cemFilter);
    if (opts.pacProcess) setPacInitialProcess(opts.pacProcess);
    if (screen === "ubogov") setUnreadCEM(0);
    setActiveScreen(screen);
  }, []);

  // ---- Governance Intelligence pane ----
  const [govData, setGovData] = useState(null);     // proxy data from DEF 14A
  const [govPeerData, setGovPeerData] = useState(null);
  const [govLoading, setGovLoading] = useState(false);
  const [govFetchError, setGovFetchError] = useState(null);
  // Separate from govFetchError — proxy and peer fetches run independently
  // (Promise.allSettled) and a peer-only failure was previously silent: no
  // error state was ever set for it, so the Peer Benchmarking tab looked
  // identical whether the fetch was never attempted or attempted and failed.
  const [govPeerError, setGovPeerError] = useState(null);
  const [govLastRefresh, setGovLastRefresh] = useState(null);
  const [activeGovTab, setActiveGovTab] = useState("overview");

  // ---- Pipeline screen: peer overlay for the KPI charts + gauges ----
  // The pick list itself is govPeerData.peers (10-K named competitors, falling
  // back to same-SIC peers — see PeerComparePicker in pipeline.jsx); this state
  // is which ones are currently selected (up to MAX_PEER_COMPARE, else the
  // charts get unreadable) and their fetched comparison data. Mirrored in
  // pipeline.jsx's own MAX_PEER_COMPARE for the picker's "at capacity" UI —
  // keep the two in sync if this changes.
  const MAX_PEER_COMPARE = 4;
  const [peerCompareList, setPeerCompareList] = useState([]); // [{ticker, companyName, forecasts, zscore, mscore}]
  const [peerCompareLoading, setPeerCompareLoading] = useState(false);
  const [peerCompareError, setPeerCompareError] = useState(null);

  const addPeerCompare = useCallback(async (rawTicker) => {
    const t = (rawTicker || "").trim().toUpperCase();
    if (!t || peerCompareList.some(p => p.ticker === t)) return;
    if (peerCompareList.length >= MAX_PEER_COMPARE) {
      setPeerCompareError(`Up to ${MAX_PEER_COMPARE} peers at a time — remove one first.`);
      return;
    }
    setPeerCompareLoading(true);
    setPeerCompareError(null);
    try {
      const res = await MCP.fetchFullAnalysis(t, { includeRss: false, includeFred: false });
      const peer = {
        ticker: res.ticker || t,
        companyName: res.company_name || t,
        forecasts: _peerForecastBundle(res),
        zscore: res.altman_zscore?.z_score,
        mscore: res.beneish_mscore?.m_score,
      };
      setPeerCompareList(list => list.some(p => p.ticker === peer.ticker) ? list : [...list, peer]);
    } catch (e) {
      setPeerCompareError(e.message || "Peer fetch failed");
    } finally {
      setPeerCompareLoading(false);
    }
  }, [peerCompareList]);

  const removePeerCompare = useCallback((ticker) => {
    setPeerCompareList(list => list.filter(p => p.ticker !== ticker));
    setPeerCompareError(null);
  }, []);

  const clearPeerCompare = useCallback(() => {
    setPeerCompareList([]);
    setPeerCompareError(null);
  }, []);

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
    // Check ticker match, not just truthiness — cfg.ticker resolves asynchronously
    // (the separate config-restore effect above), so this can fire once with the
    // default ticker before the real saved ticker loads. A pure truthiness guard
    // would then treat that first ticker's data as "already loaded" forever and
    // never re-fetch for the real ticker once cfg.ticker updates (same class of
    // bug fixed for last_loop_state — see that effect's comment).
    const tickerU     = cfg.ticker.toUpperCase();
    const proxyStale  = !govData || govData.ticker !== tickerU;
    const peersStale  = !govPeerData || govPeerData.ticker !== tickerU;
    if (!proxyStale && !peersStale) return;
    setGovLoading(true);
    Promise.allSettled([
      proxyStale ? MCP.fetchSavedProxyData(cfg.ticker) : Promise.resolve(null),
      peersStale ? MCP.fetchSavedPeerBenchmarks(cfg.ticker) : Promise.resolve(null),
    ]).then(([proxyRes, peerRes]) => {
      if (proxyStale && proxyRes.status === "fulfilled" && proxyRes.value) setGovData(proxyRes.value);
      if (peersStale && peerRes.status  === "fulfilled") {
        if (peerRes.value) { setGovPeerData(peerRes.value); setGovPeerError(null); }
      } else if (peersStale && peerRes.status === "rejected") {
        setGovPeerError(peerRes.reason?.message || "Saved peer data fetch failed");
      }
      setGovLastRefresh(new Date());
      setGovLoading(false);
    });
  }, [activeScreen, cfg.ticker, govData, govPeerData]); // eslint-disable-line react-hooks/exhaustive-deps

  // Manual refresh for the Board Intelligence screen's RefreshBadge — re-pulls
  // the saved (DB) proxy/peer data unconditionally, bypassing the staleness
  // guard above (which only fires once per ticker match). A full live EDGAR
  // re-pull already happens whenever the Risk Loop itself is re-run; this is
  // just "did anything change in the DB since I loaded this screen."
  const refreshGovData = useCallback(() => {
    if (!cfg.ticker || govLoading) return;
    setGovLoading(true);
    Promise.allSettled([
      MCP.fetchSavedProxyData(cfg.ticker),
      MCP.fetchSavedPeerBenchmarks(cfg.ticker),
    ]).then(([proxyRes, peerRes]) => {
      if (proxyRes.status === "fulfilled" && proxyRes.value) setGovData(proxyRes.value);
      if (peerRes.status  === "fulfilled") {
        if (peerRes.value) { setGovPeerData(peerRes.value); setGovPeerError(null); }
      } else {
        setGovPeerError(peerRes.reason?.message || "Saved peer data fetch failed");
      }
      setGovLastRefresh(new Date());
      setGovLoading(false);
    });
  }, [cfg.ticker, govLoading]);

  // ---- Modals ----
  const [reportOpen, setReportOpen] = useState(false);
  const [evidencePackOpen, setEvidencePackOpen] = useState(false);
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

  // ---- Company profile — built from EDGAR + FRED + RISK_ENGINE during run ----
  // Declared here (ahead of its usual spot below) because the persistence
  // effect's dependency array references it, and dependency arrays are
  // evaluated synchronously during render — a `const` referenced there
  // before its declaration line throws a TDZ ReferenceError, unlike a plain
  // usage inside an effect *callback* body (which only runs after render).
  const [profile, setProfile] = useState(() => RISK_ENGINE.buildProfile("ON", null, "3674", "Semiconductors"));
  const profileRef = useRef(profile);

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
        const hf = s.hubFocus ?? s.hub_focus;
        if (hf !== undefined)                            setHubFocus(hf);
        // profile holds the actual chart data (forecasts.revenue/margin/eps/...)
        // rendered by pipeline.jsx — without restoring it, stage/gate state comes
        // back as "complete" but the charts silently show the hardcoded mock
        // default profile instead of the last real run's data.
        if (s.profile)                                   setProfile(s.profile);
        // runId is a ref (not state) because it's mutated mid-run without
        // needing a re-render — but that also means it was never part of the
        // restore, so every runId-scoped fetch (SOX scope, approval status,
        // etc.) silently got null on a fresh page load even though the rest
        // of the run looked "restored". Mutating it here is safe before the
        // setters above trigger their re-render — same pattern already used
        // for loopLogRef/manualAuditsRef alongside their setState calls.
        const rid = s.runId || s.run_id;
        if (rid)                                          runIdRef.current = rid;
      };
      // Last-run state is saved per-ticker (see api_server.py's _loop_state_key)
      // so restoring shows the last run for the ticker actually configured,
      // not whichever ticker anyone last ran the loop for. The config-restore
      // effect above sets React state asynchronously, so cfg.ticker isn't
      // reliably readable here yet — resolve the ticker independently instead
      // of racing that effect (this GET is cheap and idempotent).
      let ticker = cfg.ticker;
      try {
        const cfgRes = await fetch("/api/mcp/config/pipeline");
        if (cfgRes.ok) {
          const cfgBody = await cfgRes.json();
          ticker = cfgBody?.cfg?.ticker || ticker;
        }
      } catch {}
      try {
        const res = await fetch(`/api/mcp/loop/last-state?ticker=${encodeURIComponent(ticker)}`);
        if (res.ok) {
          applyLoop(await res.json());
          return;
        }
      } catch {}
      // Fallback to localStorage when API is unavailable
      try {
        const raw = localStorage.getItem(`dendrai.lastLoop:${ticker}`);
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
      hubFocus,
      profile,
      runId: runIdRef.current,
      savedAt: Date.now(),
    };
    // Keyed by the ticker this profile/run actually belongs to (profile is
    // built by RISK_ENGINE.buildProfile, which always stamps entity.ticker) —
    // deliberately NOT cfg.ticker, the live config-field input. cfg.ticker was
    // in this effect's deps until it was found to cause data loss: editing the
    // ticker field after a run (e.g. typing a new ticker to configure the next
    // analysis) changes cfg.ticker on every keystroke without re-running the
    // loop, re-firing this effect each time and overwriting that new, not-yet-
    // run ticker's last-saved state with this stale profile. Confirmed live in
    // the production DB — app_config held last_loop_state:S / :SW / :SWK /
    // :SWKS entries, one per keystroke, each clobbering the previous ticker's
    // real (zscore-bearing) run with someone else's stale data.
    const runTicker = profile?.entity?.ticker || cfg.ticker;
    // Write-through: localStorage for instant offline access, DB for persistence.
    try { localStorage.setItem(`dendrai.lastLoop:${runTicker}`, _safeStringify(payload)); } catch {}
    fetch(`/api/mcp/loop/last-state?ticker=${encodeURIComponent(runTicker)}`, {
      method: "PUT",
      headers: _authHeaders(),
      body: _safeStringify(payload),
    }).then(res => {
      if (res.ok) { loopSaveFailingRef.current = false; return; }
      throw new Error(`HTTP ${res.status}`);
    }).catch(() => {
      if (!loopSaveFailingRef.current) {
        loopSaveFailingRef.current = true;
        window.showToast?.("Couldn't sync this run's state to the server — saved locally, will retry.", { tone: "warn" });
      }
    });
  }, [hasRun, output, profile]);

  const auth = window.useAuth ? window.useAuth() : null;
  const auditorName = auth?.user?.display_name || auth?.user?.username || "Auditor";

  // Development-environment gate for dev-only nav items (currently just
  // Exception Management) — see deploy_env.py. Backend independently 404s
  // the underlying endpoints outside Development, so this is purely a nav
  // visibility concern, not the enforcement boundary.
  const [isDevEnv, setIsDevEnv] = React.useState(false);
  React.useEffect(() => {
    window.MCP.fetchEnvironment().then(e => setIsDevEnv(e.isDev)).catch(() => {});
  }, []);

  // ---- Appearance persistence (accent/density/colorScheme, per-user via
  // auth.users.preferences — follows the account across browsers/machines,
  // not just this one). Hydrate once the user is known, then start saving
  // on change; the ref guard stops the initial DEFAULT_TWEAKS render from
  // overwriting whatever's already saved server-side before hydration runs. ----
  const prefsHydratedRef = useRef(false);
  useEffect(() => {
    if (auth?.user === undefined) return; // still loading session
    if (auth?.user?.preferences && Object.keys(auth.user.preferences).length) {
      setTweak(auth.user.preferences);
    }
    prefsHydratedRef.current = true;
  }, [auth?.user]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!prefsHydratedRef.current || !auth?.user) return;
    fetch("/auth/users/me/preferences", {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        accent: tweaks.accent, density: tweaks.density, colorScheme: tweaks.colorScheme,
        digestFrequency: tweaks.digestFrequency,
      }),
    }).catch(() => {});
  }, [tweaks.accent, tweaks.density, tweaks.colorScheme, tweaks.digestFrequency]); // eslint-disable-line react-hooks/exhaustive-deps

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

  useEffect(() => { profileRef.current = profile; }, [profile]);

  // ---- HITL gates ----
  const showGate = (n) => new Promise((res) => {
    gateResRef.current[n] = res;
    // A pending gate always wins focus, overriding a manual pin — this
    // promise can't resolve until the user acts on it, so the hub can never
    // be showing something else while it's outstanding.
    hubPinnedRef.current = false;
    setHubFocus(`g${n}`);
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
  const submitApprovalTask = useCallback(async (gateType, itemRef, itemLabel, disposition, adjustments, rationale, aiSuggested) => {
    if (!mcpMode || !runIdRef.current) return null;
    try {
      const result = await MCP.prepareApprovalTask({
        runId: runIdRef.current, gateType, itemRef, itemLabel, disposition, adjustments, rationale, aiSuggested,
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
    const task = await submitApprovalTask("risk", id, payload.name || risk?.name, "adjusted", adjustments, payload.rationale, payload.ai_suggested);
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
    const task = await submitApprovalTask("objective", id, payload.objective, "adjusted", adjustments, payload.rationale, payload.ai_suggested);
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

  // prefillText/linkedRiskRefs let a caller (e.g. an Auditor Takeaway "Add to
  // scope" button on the M-Score/Z-Score gauges) seed a real starting point
  // instead of the generic placeholder — the user still reviews/edits via the
  // same AdjustObjectiveModal that opens immediately after either way.
  const addObjective = useCallback((prefillText, linkedRiskRefs = []) => {
    const newId = `OBJ-${String((output.s3?.objectives?.length || 0) + 1).padStart(2, "0")}`;
    const newObj = {
      id: newId,
      objective: prefillText || "New audit objective — click Edit to define scope",
      priority: "P2",
      linked_risks: linkedRiskRefs,
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
      log(`Gate ${n} review: CONFIRMED — ${Object.values(riskApprovals).filter(a=>a.status==="approved").length} accepted, ${adjusted} adjusted and routed for review`);
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
      log(`Gate 2 review: CONFIRMED — ${Object.values(scopeApprovals).filter(a=>a.status==="approved").length} objectives accepted, ${adjObjs} adjusted and routed for review`);
    } else {
      log(`Gate ${n} review: APPROVED`);
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
    log(`Gate ${n} review: OVERRIDDEN — ${reason}`);
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
    if (tweaks.autoExpand && !hubPinnedRef.current) {
      setHubFocus(id);
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
    hubPinnedRef.current = false;
    setHubFocus(tweaks.autoExpand ? "s1" : null);
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

    // Private companies (synthetic PVT-<SLUG> ticker, see PrivateCompanyForm /
    // POST /company/private) have no SEC filings — every EDGAR-only call below
    // (risk factors, 8-K events, proxy, peers, direct XBRL fetch) would just
    // 404/error for one, so skip them rather than let each fail individually.
    // The financial ratios themselves still come through: MCP mode's
    // run_full_analysis branches on this internally (see build_company_xbrl).
    const isPrivateTicker = cfg.isPrivate || /^PVT-/i.test(cfg.ticker || "");

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
          periodBegin:   cfg.periodBegin,
          periodEnd:     cfg.periodEnd,
          persona:       selectedPersona,
          appetiteLevel: cfg.appetiteLevel,
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

        // Override placeholder M-Score/Z''-Score with real values computed
        // server-side from live EDGAR data (buildProfile was called with
        // fin=null above, so ratios.mscore/zscore are neutral placeholders
        // until overridden here).
        const _bm = mcpResult.beneish_mscore;
        if (_bm?.m_score != null) {
          templateProfile.forecasts.mscore = {
            m: _bm.m_score,
            band: _bm.rag_status === "Red" ? "ELEVATED" : _bm.rag_status === "Amber" ? "GRAY ZONE" : "NORMAL",
            key_driver: _bm.inputs?.dsri > 1.15 ? "DSRI (receivables quality)" : _bm.inputs?.tata > 0.04 ? "TATA (accrual quality)" : "SGI (sales growth)",
            thresholds: { red: -1.78, amber: -2.22 },
            vars: { DSRI: _bm.inputs?.dsri ?? 1.0, GMI: _bm.inputs?.gmi ?? 1.0, AQI: 1.0, SGI: _bm.inputs?.sgi ?? 1.0, DEPI: 1.0, SGAI: 1.0, LVGI: 1.0, TATA: _bm.inputs?.tata ?? 0.0 },
          };
        }
        const _az = mcpResult.altman_zscore;
        if (_az?.z_score != null) {
          templateProfile.forecasts.zscore = {
            z: _az.z_score,
            band: _az.rag_status === "Red" ? "DISTRESS" : _az.rag_status === "Amber" ? "GRAY ZONE" : "SAFE",
            key_driver: _az.inputs?.x1 < 0 ? "X1 (working capital deficit)" : _az.inputs?.x4 < 0.3 ? "X4 (negative book equity)" : "X3 (operating profitability)",
            thresholds: { distress: 1.1, grey: 2.6 },
          };
        }
        // Financial Risk Pipeline: JE velocity / liquidity shift / inventory
        // divergence — passed straight through, same shape
        // check_financial_risk_pipeline() returns server-side.
        if (mcpResult.financial_risk_pipeline) {
          templateProfile.forecasts.financialRiskPipeline = mcpResult.financial_risk_pipeline;
        }

        // Same placeholder problem as mscore/zscore above — templateProfile.ratios
        // is {} (buildProfile was called with fin=null), which silently breaks any
        // consumer keyed on it (e.g. Coverage Gap Analysis's quant-model-gap check).
        // Map the backend's snake_case financial_ratios onto risk-engine.js's
        // camelCase field names.
        const _fr = mcpResult.financial_ratios;
        if (_fr) {
          templateProfile.ratios = {
            ...templateProfile.ratios,
            revGrowth:    _fr.revenue_growth,
            grossMargin:  _fr.gross_margin,
            rdIntensity:  _fr.rd_intensity,
            sgaIntensity: _fr.sga_intensity,
            niMargin:     _fr.net_margin,
            assetGrowth:  _fr.asset_growth,
            cashRatio:    _fr.cash_ratio,
            fcfMargin:    _fr.fcf_margin,
            tata:         _fr.tata,
            dsri:         _fr.dsri,
            sgi:          _fr.sgi,
          };
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
        if (signalSet.has("edgar") && !isPrivateTicker) {
          try {
            const factors = await MCP.fetchRiskFactors(cfg.ticker);
            enrichedRisks = MCP.enrichRisksFromFactors(mergedRisks, factors);
            log(`MCP Risk Factors: ${factors.filings?.length || 0} filings parsed, snippets matched`);
          } catch(e) { log(`MCP Risk Factors unavailable: ${e.message}`); }
        }

        // 8-K material events → seed CEM with real events
        if (!isPrivateTicker) try {
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

        // Proxy data + peer benchmarks → Governance pane (fire and forget, non-blocking).
        // Neither applies to a private company — no proxy filings, and SIC-peer
        // benchmarking needs a resolved SIC/CIK the private path doesn't have.
        if (isPrivateTicker) {
          log("Governance/Peers: skipped — no SEC filings for a private company");
        } else
        {
        setGovLoading(true);
        setGovFetchError(null);
        setGovPeerError(null);
        Promise.allSettled([
          MCP.fetchProxyData(cfg.ticker),
          MCP.fetchPeerBenchmarks(cfg.ticker),
        ]).then(([proxyRes, peerRes]) => {
          if (proxyRes.status === "fulfilled") { setGovData(proxyRes.value); setGovFetchError(null); }
          if (peerRes.status  === "fulfilled") { setGovPeerData(peerRes.value); setGovPeerError(null); }
          setGovLastRefresh(new Date());
          if (proxyRes.status === "rejected" && peerRes.status === "rejected") {
            setGovFetchError(proxyRes.reason?.message || "MCP server unreachable — ensure api_server.py is running");
            log(`MCP Governance: server unreachable — ${proxyRes.reason?.message || "connection refused"}`);
          } else if (peerRes.status === "rejected") {
            // Peer fetch alone can fail (10-K competitor extraction + per-peer XBRL
            // enrichment is slow) without tripping the "both failed" branch above —
            // log it AND surface it on the Peer Benchmarking tab itself (govPeerError)
            // — previously this was only ever visible in the loop log, so the tab
            // looked identical whether the fetch was never attempted or attempted
            // and failed, which is what actually reads as "nothing happens."
            const peerErrMsg = peerRes.reason?.message || "unknown error";
            log(`MCP Peers: fetch failed — ${peerErrMsg}`);
            setGovPeerError(peerErrMsg);
          }
          setGovLoading(false);
          if (proxyRes.status === "fulfilled") log(`MCP Governance: proxy data loaded`);
          if (peerRes.status  === "fulfilled") log(`MCP Peers: ${peerRes.value?.peers?.length || 0} peers with data (${peerRes.value?.peer_source || "SIC peers"})`);
        });
        }

        profileRef.current = { ...templateProfile, risks: enrichedRisks };
        setProfile(profileRef.current);
        // Forecasts/ratios/riskFlow are already fully computed at this point —
        // flip hasRun here (not at loop end) so Stage 1 charts and HITL 1
        // (which fires after Stage 2, long before the loop finishes) actually
        // have data to show instead of rendering blank until Stage 6 completes.
        setHasRun(true);
        log(`Profile: ${templateProfile.entity.name} · ${industry} · ${enrichedRisks.length} risks (MCP-scored)`);

      } catch (e) {
        log(`MCP error: ${e.message} · falling back to industry template`);
        setLiveStatus(`MCP unavailable: ${e.message} · industry template`);
        const fallback = RISK_ENGINE.buildProfile(cfg.ticker, null, null, cfg.industry);
        profileRef.current = fallback;
        setProfile(fallback);
        setHasRun(true);
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
            companyName: profileRef.current?.entity?.name || cfg.ticker,
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

      // EDGAR direct fetch — private companies have no CIK/filings to fetch,
      // and Live JS mode has no client-side path to manual financials (those
      // only exist via the Python bridge, see MCP mode above), so it falls
      // back to the industry template same as an EDGAR lookup failure would.
      let edgarFin = null;
      let edgarSic = null;
      if (isPrivateTicker) {
        setLiveStatus("Private company · industry template (switch to MCP mode for manually-uploaded financials)");
        log("EDGAR: skipped — private company has no SEC filings");
      } else {
        setLiveStatus("Fetching EDGAR companyfacts…");
        try {
          const facts = await LIVE.fetchEdgarFacts(cfg.ticker);
          const extracted = LIVE.extractFinancials(facts);
          extracted.ticker = cfg.ticker; // extractFinancials doesn't echo the ticker itself — needed so the header can verify livefacts still belongs to the active ticker
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
        setHasRun(true); // see MCP-mode comment above — data's ready well before loop end
        log(`Profile: ${builtProfile.entity.name} · ${industry} · ${builtProfile.risks.length} risks derived`);
      }

      // 8-K material events via MCP bridge (Live mode — opportunistic)
      if (liveMode && !isPrivateTicker) {
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

        // Peer benchmarks via MCP bridge (Live mode — opportunistic). This is
        // the only source of the peer comparison shown on the M-Score/Z-Score
        // gauges — 10-K named-competitor extraction and SIC-peer resolution
        // are Python-backend-only (no client-side equivalent), so without
        // this call govPeerData only ever gets populated by a prior MCP-mode
        // run's saved DB entry (or stays empty, showing zero peer ticks).
        try {
          const peerBench = await MCP.fetchPeerBenchmarks(cfg.ticker);
          setGovPeerData(peerBench);
          log(`Peers: ${peerBench?.peers?.length || 0} peers with data (${peerBench?.peer_source || "SIC peers"})`);
        } catch(e) { /* peer benchmarks optional in Live mode — requires MCP bridge */ }
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

    // Rebuild Scenario Analysis + Grey Swan from the live signal-adjusted risk
    // register. buildProfile() computes both once, early, from the baseline
    // risk register — before RSS/8-K/FRED Stage 2 adjustments are applied —
    // and nothing ever recomputed them afterward, so they silently went stale
    // relative to what the risk register actually shows by the end of a run
    // (in MCP mode the backend computes a fresh scenario_analysis/grey_swan
    // per call too, but the frontend never even read those fields).
    {
      const _gsIndustry = RISK_ENGINE.normalizeIndustry(profileRef.current.entity?.focus || cfg.industry);
      const _liveRatios = profileRef.current.ratios || {};
      const _freshScenarios = RISK_ENGINE.buildScenarios(adjustedRisks, _liveRatios, cfg.ticker, _gsIndustry);
      // Mutates adjustedRisks in place with dollarExposureM — same array
      // reference used for output.s2.risks (Stage 2's payload below) and
      // thus what the Persona brief's req.risks and the Live Register rail
      // both actually read, so this keeps the per-risk $ figure in sync with
      // post-signal-adjustment scores/velocities, not just the run's baseline.
      RISK_ENGINE.allocateRiskDollarExposure(adjustedRisks, _freshScenarios);
      profileRef.current = {
        ...profileRef.current,
        scenarios: _freshScenarios,
        greySwan:  RISK_ENGINE.buildGreySwan(adjustedRisks, _liveRatios, cfg.ticker, _gsIndustry),
      };
      setProfile(profileRef.current);

      // Re-sync risk_scores with these signal-adjusted values — without this,
      // risk_scores stays frozen at the initial pre-adjustment snapshot from
      // the MCP call above, and anything reading it directly (Posture Trend's
      // RAG counts) shows the wrong distribution once Stage 2 has actually
      // moved scores. Non-blocking (fire-and-forget from the caller's
      // perspective), but retried once and logged on final failure — a
      // silently-swallowed failure here used to leave a run's persisted
      // risk_scores stuck at the initial industry-template placeholder
      // forever, with nothing to show it happened: two runs seconds apart
      // could end up on opposite sides of that coin flip, showing wildly
      // different avg score/RAG/risk counts in Posture Trend for no visible
      // reason.
      if (runIdRef.current) {
        const _syncRunId = runIdRef.current;
        const _postSync = () => fetch(`/api/mcp/risk-scores/${_syncRunId}/sync`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ risks: adjustedRisks }),
        });
        _postSync()
          .then(res => { if (!res.ok) throw new Error(`HTTP ${res.status}`); })
          .catch(() => _postSync().then(res => {
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
          }))
          .catch(err => console.warn(
            `[risk-scores/sync] Failed to sync signal-adjusted risk scores for run ${_syncRunId} — ` +
            `Posture Trend and any other direct reader of risk_scores will show this run's ` +
            `pre-adjustment (initial industry-template) snapshot instead:`, err
          ));
      }
    }

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

      // Auto-generate + persist OSCAL / COSO ERM Risks-as-Code artifacts so
      // they exist as soon as the loop completes, instead of only being
      // generated on-demand by visiting the Frameworks screen.
      fetch('/api/risks-as-code/generate', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          ticker:     cfg.ticker,
          run_id:     runIdRef.current,
          risks:      adjustedRisks,
          objectives: stage3Objectives,
          maps:       stage4Maps,
          ratios:     profileRef.current?.ratios || {},
          signals:    sigsList,
          industry:   cfg.industry,
          period:     cfg.periodEnd,
        }),
      }).catch(() => {});
    }

    setRunning(false);
    setHasRun(true);
    // Every gate the loop hit has resolved by this point (each was awaited),
    // so it's always safe to land back on the overview hub here.
    hubPinnedRef.current = false;
    setHubFocus(null);

    // Fire a synthetic CEM event so the Control Monitor tab has content too.
    setTimeout(() => fireSyntheticEvent(2), 1000 / speed);
  }

  async function rerunFromS3() {
    if (running) return;
    setRunning(true);
    hubPinnedRef.current = false;
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
      hubPinnedRef.current = false;
      setHubFocus(null);
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
    hubPinnedRef.current = false;
    setHubFocus(null);
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
    setGovPeerError(null);
    setAutoCodeYaml(null);
    // Clear the run this profile actually belongs to, not whatever's
    // currently typed in the ticker field — see the persist effect above for
    // why cfg.ticker can't be trusted here either.
    const runTicker = profile?.entity?.ticker || cfg.ticker;
    try { localStorage.removeItem(`dendrai.lastLoop:${runTicker}`); } catch {}
    // Clear the DB row too — otherwise the pre-reset run resurfaces on the
    // next login even though the UI looks freshly reset right now.
    fetch(`/api/mcp/loop/last-state?ticker=${encodeURIComponent(runTicker)}`, {
      method: "DELETE",
      headers: _authHeaders(),
    }).catch(() => {});
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
    const adjRiskCount = Object.values(riskApprovals).filter(a => a.adjustments && a.status !== "pending").length;
    const adjObjCount  = Object.values(scopeApprovals).filter(a => a.adjustments && a.status !== "pending").length;

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
      mcpMode,
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
        mcpMode
          ? `MCP mode active — EDGAR companyfacts and FRED series fetched live via the Python analytics backend.`
          : liveMode
          ? `Live mode active — EDGAR companyfacts fetched directly from data.sec.gov; FRED loaded from bundled snapshot.`
          : `Live mode inactive — all financial signals derived from mock dataset; EDGAR companyfacts not fetched.`,
        `Peer benchmark data sourced against ${cfg.industry}.`,
      ],
      obstacles: [
        ...risksCur.filter(r => (r.velocity || 0) >= 3).map(r =>
          `High-velocity risk: ${r.name} (${r.id}, v+${r.velocity}, ${r.rag}) — downstream audit scope expanded.`),
        ...(riskAppetiteResult?.status === "BREACHED"
          ? [`Risk appetite BREACHED: ${riskAppetiteResult.breaching?.length || 0} risk(s) exceed the ${cfg.appetiteLevel} threshold (≥${riskAppetiteResult.threshold}). Gate 1 mandatory review triggered.`]
          : []),
        ...(!liveMode && !mcpMode
          ? ["Live data mode disabled — EDGAR companyfacts unavailable; all EDGAR-sourced signals derived from mock register."]
          : []),
        ...(adjRiskCount > 0
          ? [`${adjRiskCount} risk${adjRiskCount !== 1 ? "s" : ""} adjusted through Gate 1 review — auditor-revised scores and RAG ratings applied to final register.`]
          : []),
        ...(adjObjCount > 0
          ? [`${adjObjCount} audit objective${adjObjCount !== 1 ? "s" : ""} adjusted through Gate 2 review — revised priorities and sprint allocations reflected in plan.`]
          : []),
      ],
    };
  }, [hasRun, output, loopLog, signalSet, cfg, velocity, liveMode, mcpMode, riskApprovals, scopeApprovals, profile]);

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
        entityTicker={profile.entity.ticker}
        aiChatLabel={aiChatCfg.buttonLabel || "Ask Claude"}
        chatOpen={chatOpen}
        onChatToggle={() => setChatOpen(v => !v)} />


      <div className={"app-body" + (activeScreen === "pipeline" && (hasRun || output.s2?.risks?.length > 0) ? " has-rail" : "")}>
        <LeftNav
          activeScreen={activeScreen}
          activeGovTab={activeGovTab}
          isAdmin={auth?.user?.role === "admin"}
          screenPerms={auth?.user?.screen_permissions}
          isDevEnv={isDevEnv}
          onNavigate={handleLeftNavigate}
          counts={{
            controls: events.length,
            controlsPulse: unreadCEM > 0,
            maps: output.s4?.maps?.length || 0,
            notifs: notifLog.length + unreadDigestCount,
            notifsPulse: (notifLog.length + unreadDigestCount) > 0,
            approvals: approvalInboxCount,
            approvalsPulse: approvalInboxCount > 0,
          }} />

        <main className="main" data-screen-label="Main canvas">
        <WorkflowStrip
          activeScreen={activeScreen}
          activeGovTab={activeGovTab}
          onNavigate={handleLeftNavigate}
          stageCounts={{ "Risk Intelligence": riskGateCount, "Monitoring Intelligence": monitoringReviewCount }} />
        <NextActionRail
          hasRun={hasRun} running={running} gateState={gateState} output={output}
          approvalInboxCount={approvalInboxCount} notifLog={notifLog}
          unreadDigestCount={unreadDigestCount} ticker={cfg.ticker}
          onNavigate={navigateToScreen} />
        {/* Help nudge only competes for attention when there's no actual work
            queued (NextActionRail empty) — an orientation offer shouldn't
            crowd out a pending approval or a breached appetite. */}
        {_computeNextActions({ hasRun, running, gateState, output, approvalInboxCount, notifLog, unreadDigestCount, ticker: cfg.ticker }).length === 0 && (
          <HelpNudge
            activeScreen={activeScreen}
            visitCount={screenVisitCounts[activeScreen] || 0}
            onNavigate={navigateToScreen}
            onAskChat={(question) => { setChatSeedQuestion(question); setChatOpen(true); }} />
        )}
        <React.Suspense fallback={<ScreenLoadingFallback/>}>

          {/* ---- Configuration / Setup ---- */}
          {activeScreen === "config" && (
            <ScreenAccessGate screenId="config">
            <div className="panel active">
              <ConfigScreenLazy
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
                setColorScheme={(v) => setTweak("colorScheme", v)}
                accent={tweaks.accent}
                setAccent={(v) => setTweak("accent", v)} />
            </div>
            </ScreenAccessGate>
          )}

          {/* ---- UBO Configuration ---- */}
          {activeScreen === "uboconfig" && (
            <ScreenAccessGate screenId="uboconfig">
            <div className="panel active">
              <UboConfigScreenLazy />
            </div>
            </ScreenAccessGate>
          )}

          {/* ---- User Configuration (add/change/remove local accounts + per-role screen access) ---- */}
          {activeScreen === "userconfig" && (
            <div className="panel active">
              <UserConfigScreenLazy />
            </div>
          )}

          {/* ---- Token Usage (LLM token/cost by user and by feature) ---- */}
          {activeScreen === "tokenusage" && (
          <ScreenAccessGate screenId="tokenusage">
            <div className="panel active">
              <TokenUsageScreenLazy />
            </div>
          </ScreenAccessGate>
          )}

          {/* ---- Model Health (backtest accuracy trend + PSI drift) ---- */}
          {activeScreen === "modelhealth" && (
          <ScreenAccessGate screenId="modelhealth">
            <div className="panel active">
              <ModelHealthScreenLazy />
            </div>
          </ScreenAccessGate>
          )}

          {/* ---- Continuous Monitoring (command center) ---- */}
          {activeScreen === "continuousmonitoring" && (
          <ScreenAccessGate screenId="continuousmonitoring">
            <div className="panel active">
              <ContinuousMonitoringScreenLazy onNavigate={navigateToScreen} />
            </div>
          </ScreenAccessGate>
          )}

          {/* ---- Risk Quantification (FAIR Monte Carlo loss modeling) ---- */}
          {activeScreen === "riskquant" && (
          <ScreenAccessGate screenId="riskquant">
            <div className="panel active">
              <RiskQuantificationScreenLazy onNavigate={navigateToScreen} />
            </div>
          </ScreenAccessGate>
          )}

          {/* ---- Exception Management (Continuous Control Monitoring triage — Development environment only) ---- */}
          {activeScreen === "exceptions" && isDevEnv && (
          <ScreenAccessGate screenId="exceptions">
            <div className="panel active">
              <ExceptionsScreenLazy onNavigate={navigateToScreen} />
            </div>
          </ScreenAccessGate>
          )}

          {/* ---- Infrastructure Monitoring (Postgres CIS + Railway drift + connector hygiene) ---- */}
          {activeScreen === "infrastructuremonitoring" && (
          <ScreenAccessGate screenId="infrastructuremonitoring">
            <div className="panel active">
              <InfrastructureMonitoringScreenLazy onNavigate={navigateToScreen} isDevEnv={isDevEnv} />
            </div>
          </ScreenAccessGate>
          )}

          {/* ---- AI System Inventory ---- */}
          {activeScreen === "aiinventory" && (
          <ScreenAccessGate screenId="aiinventory">
            <div className="panel active">
              <AiInventoryScreenLazy onNavigate={navigateToScreen} />
            </div>
          </ScreenAccessGate>
          )}

          {/* ---- AI Governance (register + behavioural audit) ---- */}
          {/* Screen id, nav id, and _SCREEN_ID in ai_governance_endpoints.py
              are all "ai_governance" on purpose — see the note in nav.jsx.
              No longer Development-only — ScreenAccessGate + the backend's
              require_screen_permission are the real access boundary, same
              as every other non-dev-only screen. */}
          {activeScreen === "ai_governance" && (
          <ScreenAccessGate screenId="ai_governance">
            <div className="panel active">
              <AiGovernanceScreenLazy />
            </div>
          </ScreenAccessGate>
          )}

          {/* ---- Pipeline (with action bar + sub-tabs) ---- */}
          {activeScreen === "pipeline" && (
          <ScreenAccessGate screenId="pipeline">
          <div className="panel active">
            {/* Action bar — primary verbs live with the pipeline they drive */}
            <div className="pipe-action-bar">
              <button className="btn btn-acc" disabled={running} onClick={runLoop}>
                {running ? <><span className="spin"/> Running loop…</> : <><Icon name="play" size={12}/> Run Loop</>}
              </button>
              <button className="btn" disabled={!hasRun} onClick={() => setReportOpen(true)}><Icon name="doc" size={11}/> Loop Report</button>
              <button className="btn" disabled={!hasRun || !runIdRef.current} onClick={() => setEvidencePackOpen(true)}
                title={hasRun && !runIdRef.current ? "Evidence Pack requires a DB-persisted run (MCP mode)" : ""}>
                <Icon name="shield" size={11}/> Evidence Pack
              </button>
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
                <div className="panel-sub">Each stage feeds structured output to the next, pausing at review gates for human sign-off.</div>
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

            <WhatChangedDigest ticker={cfg.ticker} hasRun={hasRun} />

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
                hubFocus={hubFocus}
                onFocusStage={goToNode}
                onFocusGate={goToNode}
                onGoHub={goToHub}
                stageState={stageState}
                output={output}
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
                forecasts={hasRun ? profile.forecasts : null}
                ticker={cfg.ticker || ""}
                liveMode={liveMode}
                fredSeries={fredLive}
                industry={hasRun ? profile.entity?.industry : cfg.industry}
                enabledFeedIds={rssEnabledFeeds}
                onRssSignalsReady={(sigs) => {
                  setRssSignals(sigs);
                  log(`RSS ingestion complete — ${sigs.length} velocity signals graded`);
                }}
                flowMeta={hasRun ? profile.riskFlow : null}
                onOpenMainFlow={() => setActiveScreen("flow")}
                risks={output.s2?.risks || (hasRun ? profile?.risks : null) || []}
                companyName={hasRun ? (profile?.entity?.name || "") : ""}
                peerData={govPeerData}
                peerCompareList={peerCompareList}
                peerCompareLoading={peerCompareLoading}
                peerCompareError={peerCompareError}
                onAddPeerCompare={addPeerCompare}
                onRemovePeerCompare={removePeerCompare}
                onClearPeerCompare={clearPeerCompare}
                ratios={hasRun ? (profile?.ratios || {}) : {}}
                events={events} />
            )}
          </div>
          </ScreenAccessGate>
          )}

          {/* Risk Register, Risk Flow, Forecasts and Scenarios now live in the
              right-hand Live Register rail (rendered below, post-run). */}

          {/* ---- Controls Monitor ---- */}
          {activeScreen === "controls" && (
          <ScreenAccessGate screenId="controls">
          <div className="panel active">
            <CEMPanel
              events={events} setEvents={setEvents}
              filter={cemFilter} setFilter={setCemFilter}
              expanded={cemExpanded} setExpanded={setCemExpanded}
              onAckNotif={ackNotif}
              onInject={() => fireSyntheticEvent(1)}
              ticker={cfg.ticker} />
          </div>
          </ScreenAccessGate>
          )}

          {/* ---- Dendrai UBO Governance Brain ---- */}
          {activeScreen === "ubogov" && (
          <ScreenAccessGate screenId="ubogov">
          <div className="panel active">
            <UBOGovPanel initialTab={cemInitialTab} initialFilter={cemInitialFilter} />
          </div>
          </ScreenAccessGate>
          )}

          {/* ---- Risk Flow ---- */}
          {activeScreen === "flow" && (
          <div className="panel active">
            <FlowPanelLazy
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
          <ScreenAccessGate screenId="maps">
          <div className="panel active">
            <div className="panel-head">
              <div>
                <div className="kicker">Execution</div>
                <div className="panel-title mt-8">Management Action Plans</div>
                <div className="panel-sub">Findings, owners, due dates, and completion across the register.</div>
              </div>
            </div>
            <RecurringExceptionMaps />
            <MapsTab maps={railMaps}/>
          </div>
          </ScreenAccessGate>
          )}

          {/* ---- Notifications ---- */}
          {activeScreen === "notifs" && (
          <ScreenAccessGate screenId="notifs">
          <div className="panel active">
            <div className="panel-head">
              <div>
                <div className="kicker">Execution</div>
                <div className="panel-title mt-8">Notifications</div>
                <div className="panel-sub">Scheduled posture digests, plus the tiered stakeholder cascade from the Control Event Monitor.</div>
              </div>
            </div>
            <NotifTab log={notifLog} digests={digests} onMarkRead={markDigestRead}
              digestFreq={tweaks.digestFrequency || "off"} onSetDigestFreq={(v) => setTweak("digestFrequency", v)}/>
          </div>
          </ScreenAccessGate>
          )}

          {/* ---- Audit Scope ---- */}
          {activeScreen === "scope" && (
          <ScreenAccessGate screenId="scope">
          <div className="panel active">
            <AuditScopeScreenLazy
              objectives={output.s3?.objectives?.length ? output.s3.objectives
                : savedAuditScope?.objectives?.length ? savedAuditScope.objectives
                : (hasRun ? profile.objectives : [])}
              maps={railMaps}
              risks={railRisks}
              hasRun={hasRun}
              savedRunAt={!output.s3?.objectives?.length ? savedAuditScope?.run_at : null} />
          </div>
          </ScreenAccessGate>
          )}

          {/* ---- PBC Evidence Log ---- */}
          {activeScreen === "evidencequality" && (
          <ScreenAccessGate screenId="evidencequality">
            <div className="panel active">
              <EvidenceQualityScreenLazy />
            </div>
          </ScreenAccessGate>
          )}

          {/* ---- Approval Inbox ---- */}
          {activeScreen === "approvals" && (
          <ScreenAccessGate screenId="approvals">
          <div className="panel active">
            <ApprovalInboxScreenLazy />
          </div>
          </ScreenAccessGate>
          )}

          {/* ---- Coverage Gap Analysis ---- */}
          {activeScreen === "coverage" && (
          <div className="panel active" style={{overflow:"auto"}}>
            <CoverageGapPanel
              risks={output.s2?.risks || (hasRun ? profile.risks : [])}
              objectives={output.s3?.objectives || (hasRun ? profile.objectives : [])}
              rssSignals={rssSignals}
              events={events}
              ratios={hasRun ? (profile.ratios || {}) : {}}
              industry={hasRun ? profile.entity?.industry : cfg.industry}
              ticker={cfg.ticker} />
          </div>
          )}

          {/* ---- Posture Trend ---- */}
          {activeScreen === "posturetrend" && (
          <ScreenAccessGate screenId="posturetrend">
            <PostureTrendScreenLazy ticker={cfg.ticker} />
          </ScreenAccessGate>
          )}

          {/* ---- Help ---- */}
          {activeScreen === "help" && (
          <div className="panel active">
            <HelpScreenLazy isDevEnv={isDevEnv} onNavigate={(screen, govTab) => { setActiveScreen(screen); if (govTab) setActiveGovTab(govTab); }} />
          </div>
          )}

          {/* ---- Risk-as-Code ---- */}
          {activeScreen === "riskcode" && (
          <div className="panel active">
            <RiskAsCodeScreenLazy
              risks={output.s2?.risks || (hasRun ? profile.risks : null)}
              baseRisks={profile.risks} />
          </div>
          )}

          {/* ---- Risk Register Review (Phases 2-4) ---- */}
          {activeScreen === "rrreview" && (
          <ScreenAccessGate screenId="rrreview">
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
          </ScreenAccessGate>
          )}

          {/* ---- Policy-as-Code ---- */}
          {activeScreen === "policycode" && (
          <ScreenAccessGate screenId="policycode">
          <div className="panel active">
            <PolicyAsCodeScreenLazy
              events={events}
              maps={railMaps}
              risks={railRisks}
              appetiteThreshold={APPETITE_THRESHOLDS[cfg.appetiteLevel] ?? 7.5}
              initialProcess={pacInitialProcess} />
          </div>
          </ScreenAccessGate>
          )}

          {/* ---- Regulatory Change Management ---- */}
          {activeScreen === "regchange" && (
          <ScreenAccessGate screenId="regchange">
          <div className="panel active">
            <RegulatoryChangeScreenLazy />
          </div>
          </ScreenAccessGate>
          )}

          {/* ---- Grey Swan Scenarios ---- */}
          {activeScreen === "scenarios" && (
          <ScreenAccessGate screenId="scenarios">
          <div className="panel active">
            <ScenariosPanelLazy
              scenarios={hasRun ? profile.scenarios : null}
              greySwan={hasRun ? profile.greySwan : null}
              reverseStress={hasRun ? profile.reverseStress : null}
              historicalAnalogs={hasRun ? profile.historicalAnalogs : null}
              governanceScenario={hasRun ? profile.governanceScenario : null} />
          </div>
          </ScreenAccessGate>
          )}

          {/* ---- Scenario Analysis (VaR/CVaR, sensitivity, stress, liquidity, EWI) ---- */}
          {activeScreen === "scenarioanalysis" && (
          <ScreenAccessGate screenId="scenarioanalysis">
          <div className="panel active">
            <ScenarioAnalysisScreenLazy
              ticker={cfg.ticker}
              hasRun={hasRun}
              varCvar={hasRun ? profile.varCvar : null}
              sensitivity={hasRun ? profile.sensitivity : null}
              multiFactorStress={hasRun ? profile.multiFactorStress : null}
              liquidityRunway={hasRun ? profile.liquidityRunway : null}
              earlyWarning={hasRun ? profile.earlyWarning : null}
              risks={hasRun ? (profile.risks || []) : []}
              onAddAudit={addManualAudit} />
          </div>
          </ScreenAccessGate>
          )}

          {/* ---- SOX Scope ---- */}
          {activeScreen === "sox" && (
          <ScreenAccessGate screenId="sox">
          <div className="panel active" style={{overflow: "auto"}}>
            <SoxScopePanelLazy
              ticker={cfg.ticker}
              runId={runIdRef.current}
              forecasts={hasRun ? profile.forecasts : null}
              risks={output.s2?.risks || (hasRun ? profile.risks : []) || []}
              ratios={hasRun ? (profile.ratios || {}) : {}}
              hasRun={hasRun} />
          </div>
          </ScreenAccessGate>
          )}

          {/* ---- Governance Intelligence ---- */}
          {activeScreen === "gov" && (
          <ScreenAccessGate screenId="gov">
          <div className="panel gov-panel active">
            <GovernanceViewLazy
              data={govData}
              peerData={govPeerData}
              ticker={cfg.ticker}
              loading={govLoading}
              activeTab={activeGovTab}
              onTabChange={setActiveGovTab}
              govFetchError={govFetchError}
              peerFetchError={govPeerError}
              lastRefresh={govLastRefresh}
              onRefresh={refreshGovData} />
          </div>
          </ScreenAccessGate>
          )}
        </React.Suspense>
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

      {/* ---- AI disclaimer — persistent, present on every screen (outside the
           per-screen activeScreen tree, so no per-screen wiring needed) ---- */}
      <div className="ai-disclaimer">
        <Icon name="spark" size={11} />
        <span>
          Dendrai combines deterministic financial models with AI-generated analysis, recommendations, and drafted content (Claude).
          AI outputs may be inaccurate or incomplete — review before relying on them for decisions.
        </span>
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
      <EvidencePackModal open={evidencePackOpen} onClose={() => setEvidencePackOpen(false)}
        runId={runIdRef.current} ticker={cfg.ticker || ""} />
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
        useDb={useDb} setUseDb={setUseDb}
        seedQuestion={chatSeedQuestion} />
      <ToastHost />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} onNavigate={handleLeftNavigate} />
      </ErrorBoundary>
    </div>);

}

// ---- What Changed digest (Change Layer) ----
// "What changed since your last run" — see api_server.py's Change Layer
// section for the materiality rules. Fetches on ticker change and whenever
// a fresh run completes (hasRun flips), so a just-finished run's deltas
// show up immediately without a manual refresh. Renders nothing on a
// ticker's first-ever run (no prior to compare against) or while loading;
// a fetch failure is swallowed the same way govPeerError's siblings are —
// this is a nice-to-have callout, not something worth blocking the screen on.
function WhatChangedDigest({ ticker, hasRun }) {
  const [data, setData] = React.useState(null);
  const [dismissedFor, setDismissedFor] = React.useState(null);

  React.useEffect(() => {
    if (!ticker) { setData(null); return; }
    let cancelled = false;
    MCP.fetchChanges(ticker)
      .then(d => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [ticker, hasRun]);

  if (!data?.has_prior) return null;
  const dismissKey = data.to_run?.run_id;
  if (dismissKey != null && dismissedFor === dismissKey) return null;

  const changes = data.changes || [];
  const CHANGE_ICON = { risk_band: "alert", mscore_band: "flow", zscore_band: "trend" };

  function changeLine(c, i) {
    if (c.type === "risk_band") {
      return (
        <div key={i} className="wcd-row" style={{ "--i": i }}>
          <span className="wcd-row-icon"><Icon name={CHANGE_ICON.risk_band} size={12}/></span>
          <span className="wcd-row-text">
            <b>{c.name}</b> {c.from_band && c.to_band ? "moved" : "shifted"}{" "}
            {c.from_band && c.to_band && (
              <>from <RAGChip rag={c.from_band[0]}>{c.from_band}</RAGChip> to <RAGChip rag={c.to_band[0]}>{c.to_band}</RAGChip></>
            )}
            {" "}<span className="mono muted">({c.from_score?.toFixed(1)} → {c.to_score?.toFixed(1)}, {c.delta > 0 ? "+" : ""}{c.delta?.toFixed(1)})</span>
          </span>
        </div>
      );
    }
    const label = c.type === "mscore_band" ? "Beneish M-Score" : "Altman Z''-Score";
    return (
      <div key={i} className="wcd-row" style={{ "--i": i }}>
        <span className="wcd-row-icon"><Icon name={CHANGE_ICON[c.type]} size={12}/></span>
        <span className="wcd-row-text">
          <b>{label}</b> crossed from <RAGChip rag={c.from_band[0]}>{c.from_band}</RAGChip> to <RAGChip rag={c.to_band[0]}>{c.to_band}</RAGChip>
          {" "}<span className="mono muted">({c.from_value?.toFixed(2)} → {c.to_value?.toFixed(2)})</span>
        </span>
      </div>
    );
  }

  const hasMaterial = changes.length > 0;
  return (
    <div className={"wcd" + (hasMaterial ? " wcd-material" : "")}>
      <div className="wcd-head">
        <Icon name={hasMaterial ? "alert" : "check"} size={13}/>
        <span className="wcd-headline">{data.headline}</span>
        <button type="button" className="wcd-dismiss" onClick={() => setDismissedFor(dismissKey)} title="Dismiss">
          <Icon name="x" size={11}/>
        </button>
      </div>
      {hasMaterial && <div className="wcd-rows">{changes.map(changeLine)}</div>}
    </div>
  );
}

// ---- Peer comparison chart series (Pipeline screen) ----
// Builds the same {history, forecast} shape used for the subject company's
// KPI charts (see the MCP-mode branch of _runLoopBody above), from a peer
// ticker's own /predictive/full-analysis result — so MultiSeriesForecastChart
// can overlay it directly. Deliberately mirrors rather than shares that
// logic: the subject-company path also drives Monte Carlo bands, gate state,
// and DB persistence, none of which a side comparison should touch.
function _peerForecastBundle(mcpResult) {
  const { fcLabels } = RISK_ENGINE.quarterBoundaries();
  const toQL = (d) => { if (!d) return null; const [y, m] = d.slice(0, 7).split('-').map(Number); return `Q${Math.ceil(m / 3)}-${String(y).slice(-2)}`; };
  const mapQ = (series, scale, digits) =>
    (series || []).slice(-20).map(p => ({ q: toQL(p.quarter_end) || p.quarter_end, v: +(p.value / scale).toFixed(digits) }));
  const linFc = (hist, digits) => {
    if (!hist?.length) return [];
    const last = hist[hist.length - 1].v;
    const step = hist.length >= 2 ? (last - hist[hist.length - 2].v) * 0.5 : 0;
    return fcLabels.map((q, i) => ({ q, base: +(last + step * (i + 1)).toFixed(digits) }));
  };
  const ensembleFc = (fc, digits, scale = 1) =>
    fc?.forecasts?.length
      ? fc.forecasts.map((f, i) => ({ q: fcLabels[i] || `H${i + 1}`, base: +(f.point / scale).toFixed(digits) }))
      : null;

  const bundle = {};
  const fc = mcpResult.forecast;
  if (fc?.history?.length) {
    const h = mapQ(fc.history, 1e6, 0);
    if (h.length >= 4) bundle.revenue = { history: h, forecast: ensembleFc(fc, 0, 1e6) ?? linFc(h, 0) };
  }
  if (fc?.margin_history?.length) {
    const h = mapQ(fc.margin_history, 1, 1);
    if (h.length >= 4) bundle.margin = { history: h, forecast: ensembleFc(fc.margin_forecast, 1) ?? linFc(h, 1) };
  }
  const as = mcpResult.analyst_series;
  if (as?.eps?.length >= 4) {
    const h = mapQ(as.eps, 1, 2);
    bundle.eps = { history: h, forecast: ensembleFc(as.eps_forecast, 2) ?? linFc(h, 2) };
  }
  if (as?.op_margin?.length >= 4) {
    const h = mapQ(as.op_margin, 1, 1);
    bundle.opMargin = { history: h, forecast: ensembleFc(as.op_margin_forecast, 1) ?? linFc(h, 1) };
  }
  if (as?.net_income?.length >= 4) {
    const h = mapQ(as.net_income, 1e6, 0);
    bundle.netIncome = { history: h, forecast: linFc(h, 0) };
  }
  if (as?.ebitda?.length >= 4) {
    const h = mapQ(as.ebitda, 1e6, 0);
    bundle.ebitda = { history: h, forecast: linFc(h, 0) };
  }
  if (as?.fcf?.length >= 4) {
    const h = mapQ(as.fcf, 1e6, 0);
    bundle.fcf = { history: h, forecast: linFc(h, 0) };
  }
  return bundle;
}

// ---- Next Best Action rail (quasi-facilitated navigation) ----
// A single always-current "what should I do next" prompt, computed from real
// app state (pending gates, breached appetite, approval-inbox depth, overdue
// MAPs, unread notifications) — not a generic tour. Deliberately has no
// permanent "seen it, never show again" flag: it's state-driven, so it goes
// quiet on its own once there's genuinely nothing to act on, and comes back
// the moment something new needs attention. Dismissing only clears it for
// the current session (resets on reload) since a queue this short doesn't
// warrant a persisted preference.
function _isMapOverdue(m) {
  if (!m || (m.completion_pct ?? 0) >= 100 || !m.due_date) return false;
  const match = /^(\d{4})-Q([1-4])$/.exec(m.due_date);
  if (!match) return false;
  const dueYear = +match[1], dueQ = +match[2];
  const now = new Date();
  const curYear = now.getFullYear(), curQ = Math.ceil((now.getMonth() + 1) / 3);
  return dueYear < curYear || (dueYear === curYear && dueQ < curQ);
}

function _computeNextActions({ hasRun, running, gateState, output, approvalInboxCount, notifLog, unreadDigestCount, ticker }) {
  const actions = [];
  if (!hasRun && !running) {
    actions.push({ id: "run", priority: 0, screen: "pipeline",
      text: `Run the risk loop for ${ticker || "this entity"} to generate your first assessment.`,
      cta: "Go to Risk Radar" });
  }
  if (hasRun && gateState?.g1 === "pending") {
    actions.push({ id: "gate1", priority: 1, screen: "pipeline",
      text: "Gate 1 risk approval is waiting on your review.", cta: "Review Gate 1" });
  }
  if (hasRun && gateState?.g2 === "pending") {
    actions.push({ id: "gate2", priority: 1, screen: "pipeline",
      text: "Gate 2 scope approval is waiting on your review.", cta: "Review Gate 2" });
  }
  if (output?.s2?.riskAppetite?.status === "BREACHED") {
    const n = output.s2.riskAppetite.breaching?.length || 0;
    actions.push({ id: "appetite", priority: 1, screen: "pipeline",
      text: `Risk appetite breached — ${n} risk${n !== 1 ? "s" : ""} exceed tolerance.`, cta: "Review risks" });
  }
  if (approvalInboxCount > 0) {
    actions.push({ id: "approvals", priority: 2, screen: "approvals",
      text: `${approvalInboxCount} item${approvalInboxCount !== 1 ? "s" : ""} in your Approval Inbox need review.`,
      cta: "Open Approval Inbox" });
  }
  const overdueMaps = (output?.s4?.maps || []).filter(_isMapOverdue);
  if (overdueMaps.length > 0) {
    actions.push({ id: "maps", priority: 2, screen: "maps",
      text: `${overdueMaps.length} management action plan${overdueMaps.length !== 1 ? "s are" : " is"} past their due quarter.`,
      cta: "Open MAPs" });
  }
  const unread = (notifLog?.length || 0) + (unreadDigestCount || 0);
  if (unread > 0) {
    actions.push({ id: "notifs", priority: 3, screen: "notifs",
      text: `${unread} unread notification${unread !== 1 ? "s" : ""}.`, cta: "Open Notifications" });
  }
  return actions.sort((a, b) => a.priority - b.priority);
}

function NextActionRail({ hasRun, running, gateState, output, approvalInboxCount, notifLog, unreadDigestCount, ticker, onNavigate }) {
  const [dismissed, setDismissed] = React.useState(false);
  const [expanded, setExpanded] = React.useState(false);

  const actions = _computeNextActions({ hasRun, running, gateState, output, approvalInboxCount, notifLog, unreadDigestCount, ticker });
  // Re-arm the dismissal once the queue changes shape (new action appeared,
  // or the count shifted) — dismissing "review Gate 1" shouldn't also
  // silently swallow a brand-new appetite breach that shows up a minute later.
  const actionsKey = actions.map(a => a.id).join(",");
  const lastKeyRef = React.useRef(actionsKey);
  if (lastKeyRef.current !== actionsKey) {
    lastKeyRef.current = actionsKey;
    if (dismissed) setDismissed(false);
  }

  if (!actions.length || dismissed) return null;
  const top = actions[0];
  const rest = actions.slice(1);

  return (
    <div className="nba">
      <div className="nba-head">
        <span className="nba-icon"><Icon name="compass" size={13}/></span>
        <span className="nba-text">{top.text}</span>
        <button type="button" className="btn btn-sm nba-cta" onClick={() => onNavigate(top.screen)}>
          {top.cta} <Icon name="chev-r" size={10}/>
        </button>
        {rest.length > 0 && (
          <button type="button" className="nba-more" onClick={() => setExpanded(v => !v)}>
            +{rest.length} more <Icon name={expanded ? "chev-u" : "chev-d"} size={10}/>
          </button>
        )}
        <button type="button" className="nba-dismiss" onClick={() => setDismissed(true)} title="Dismiss for this session">
          <Icon name="x" size={11}/>
        </button>
      </div>
      {expanded && rest.length > 0 && (
        <div className="nba-rows">
          {rest.map(a => (
            <div key={a.id} className="nba-row">
              <span className="nba-row-text">{a.text}</span>
              <button type="button" className="btn btn-sm" onClick={() => onNavigate(a.screen)}>{a.cta}</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Anticipatory help nudge ----
// Reads one cheap, fully-client-side activity signal — how many times the
// current screen has been visited this session — and offers (never
// auto-injects) a way to get oriented: jump to that screen's entry in the
// Intelligenza Workflow guide, or ask the AI chat a seeded-but-not-sent
// question about it. Both offers only ever point at data/screens the user
// can already reach through the normal nav — this never fetches anything on
// its own or bypasses ScreenAccessGate, it just suggests a next click.
const _HELP_NUDGE_THRESHOLD = 3;

function HelpNudge({ activeScreen, visitCount, onNavigate, onAskChat }) {
  const [dismissed, setDismissed] = React.useState(() => new Set());

  if (activeScreen === "help" || visitCount < _HELP_NUDGE_THRESHOLD || dismissed.has(activeScreen)) {
    return null;
  }

  let label = activeScreen;
  for (const section of window.NAV_SECTIONS || []) {
    const item = section.items.find(it => it.id === activeScreen);
    if (item) { label = item.l; break; }
  }

  function dismiss() {
    setDismissed(prev => new Set(prev).add(activeScreen));
  }

  return (
    <div className="help-nudge">
      <span className="help-nudge-icon"><Icon name="compass" size={13}/></span>
      <span className="help-nudge-text">You've come back to {label} a few times this session — need a hand?</span>
      <button type="button" className="btn btn-sm" onClick={() => { dismiss(); onNavigate("help"); }}>
        Show workflow guide <Icon name="chev-r" size={10}/>
      </button>
      <button type="button" className="btn btn-sm btn-ghost" onClick={() => { dismiss(); onAskChat(`What does the ${label} screen do and how should I use it?`); }}>
        Ask in chat
      </button>
      <button type="button" className="help-nudge-dismiss" onClick={dismiss} title="Dismiss for this session">
        <Icon name="x" size={11}/>
      </button>
    </div>
  );
}

// ---- Command palette (Cmd/Ctrl+K) ----
// v1 scope: fuzzy-search over the 33 nav screens (window.NAV_SECTIONS) plus
// the shared control library (window.MASTER_CONTROLS — same live reference
// HITL Gate 2 already reads, see risk-register-review.jsx). Deliberately
// does NOT search risks/MAPs across all runs — output.s2?.risks/s4?.maps
// are only populated for the currently-loaded run, not a standing cross-run
// index, so that would need a new backend endpoint rather than reusing
// existing data (see the UX audit's "worth building" writeup for this item).
function CommandPalette({ open, onClose, onNavigate }) {
  const inputRef = React.useRef(null);
  const [query, setQuery] = React.useState("");
  const [activeIdx, setActiveIdx] = React.useState(0);

  React.useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIdx(0);
      const t = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(t);
    }
  }, [open]);
  useEscapeToClose(open, onClose);

  const items = React.useMemo(() => {
    const screens = [];
    (window.NAV_SECTIONS || []).forEach(section => {
      (section.items || []).forEach(item => {
        screens.push({
          key: `screen:${item.id}:${item.govTab || ""}`,
          label: item.l, section: section.label, kind: "Screen",
          go: () => onNavigate(item.id, item.govTab),
        });
      });
    });
    const controls = (window.MASTER_CONTROLS || []).map(c => ({
      key: `control:${c.ref}`,
      label: `${c.ref} — ${c.name}`, section: "Controls", kind: "Control", sub: c.desc,
      go: () => {
        if (navigator.clipboard?.writeText) navigator.clipboard.writeText(c.ref).catch(() => {});
        window.showToast?.(`Copied "${c.ref}" — opening Risk & Control Ledger, search for it there.`, { tone: "neutral" });
        onNavigate("rrreview");
      },
    }));
    return [...screens, ...controls];
  }, [onNavigate]);

  const results = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items.slice(0, 8);
    return items
      .map(it => {
        const hay = `${it.label} ${it.section} ${it.sub || ""}`.toLowerCase();
        const idx = hay.indexOf(q);
        if (idx === -1) return null;
        const score = it.label.toLowerCase().startsWith(q) ? -1 : idx;
        return { it, score };
      })
      .filter(Boolean)
      .sort((a, b) => a.score - b.score)
      .slice(0, 20)
      .map(r => r.it);
  }, [items, query]);

  function select(it) {
    if (!it) return;
    it.go();
    onClose();
  }

  function onKeyDown(e) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActiveIdx(i => Math.min(i + 1, results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActiveIdx(i => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); select(results[activeIdx]); }
  }

  if (!open) return null;
  return (
    <div className="cmdk-overlay" role="presentation" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="cmdk-box" role="dialog" aria-modal="true" aria-label="Command palette">
        <input ref={inputRef} className="cmdk-input" value={query}
          onChange={e => setQuery(e.target.value)} onKeyDown={onKeyDown}
          placeholder="Search screens and controls…" />
        <div className="cmdk-results">
          {results.length === 0 ? (
            <div className="cmdk-empty">No matches.</div>
          ) : results.map((it, i) => (
            <div key={it.key} className={"cmdk-item" + (i === activeIdx ? " active" : "")}
              onMouseEnter={() => setActiveIdx(i)} onClick={() => select(it)}>
              <span className="cmdk-item-label">{it.label}</span>
              <span className="cmdk-item-meta">{it.kind === "Control" ? "Control" : it.section}</span>
            </div>
          ))}
        </div>
        <div className="cmdk-foot">↑↓ navigate · ↵ select · esc close</div>
      </div>
    </div>
  );
}

// ---- Header ----
function Header({ cfg, liveMode, mcpMode, livefacts, running, hasRun, entityName, entityTicker,
                  aiChatLabel, chatOpen, onChatToggle }) {
  const auth = window.useAuth ? window.useAuth() : null;
  const DendraiMark = window.DendraiMark;
  const DendraiWordmark = window.DendraiWordmark;
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
        <div className="hdr-logo"><DendraiMark size={19}/></div>
        <div className="hdr-name"><DendraiWordmark size={14}/> <span className="hdr-subname">Intelligenza™</span></div>
      </div>
      <div className="hdr-sep" />
      <div className="hdr-ctx">
        <span className="hdr-ctx-tkr">{cfg.ticker}</span>
        <span className="muted">·</span>
        {(() => {
          // livefacts and profile.entity are only refreshed when a run
          // completes for the *currently typed* ticker — until then they
          // still hold whatever company was last run. Showing either one
          // next to cfg.ticker without checking its own ticker pairs the
          // new ticker with a stale company name (e.g. "ENTG · International
          // Business Machines Corp" after typing ENTG but before re-running).
          const tkr = (cfg.ticker || "").toUpperCase();
          const factsMatch  = livefacts?.ticker?.toUpperCase() === tkr;
          const entityMatch = entityTicker?.toUpperCase() === tkr;
          const name = factsMatch ? livefacts.entity : entityMatch ? entityName : null;
          return <span style={{ fontSize: 11.5 }}>{name || cfg.company || "—"}</span>;
        })()}
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
              <div style={{ position: "fixed", inset: 0, zIndex: 49 }} />
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
                  Adjustments you submit for Enterprise Risk and SOX gates route to this person for review.
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