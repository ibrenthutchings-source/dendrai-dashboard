/* ============================================================
   Dendrai Risk Loop — main App
   Orchestrates: run-loop animation, HITL gates, CEM, modals,
   data mode (mock/live), tweaks.
   ============================================================ */

const DEFAULT_TWEAKS = /*EDITMODE-BEGIN*/{
  "accent": "emerald",
  "density": "comfortable",
  "runSpeed": 1.0,
  "autoExpand": true,
  "persona": "Internal Audit"
} /*EDITMODE-END*/;

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
    ticker: MOCK.entity.ticker,
    industry: MOCK.entity.industry,
    focus: MOCK.entity.focus
  });
  const [signalSet, setSignalSet] = useState(new Set(["edgar", "peers", "industry", "internal"]));
  const [velocity, setVelocity] = useState(3);
  const [hitl, setHitl] = useState({ risk: true, scope: true, map: false });

  // ---- Data connection config (localStorage-persisted) ----
  const [dataConfig, setDataConfig] = useState(() => {
    try {
      const raw = localStorage.getItem('dendrai_data_config');
      return raw ? JSON.parse(raw) : { fredApiKey: '', tickers: [], fredSeriesIds: [] };
    } catch { return { fredApiKey: '', tickers: [], fredSeriesIds: [] }; }
  });
  const [configModalOpen, setConfigModalOpen] = useState(false);

  // ---- Live mode + EDGAR/FRED data ----
  const [liveMode, setLiveMode] = useState(false);
  const [liveStatus, setLiveStatus] = useState("");
  const [livefacts, setLivefacts] = useState(null);
  const [fredLive, setFredLive] = useState(null);
  const [rawEdgarFacts, setRawEdgarFacts] = useState(null);    // { ticker: facts }
  const [fredApiResults, setFredApiResults] = useState(null);  // { seriesId: { observations } }

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
  const [activeMainTab, setActiveMainTab] = useState("pipe"); // pipe | cem | fcst | scen
  const [activeRailTab, setActiveRailTab] = useState("rr"); // rr | hm | map | loop | notif | fcst | pers
  const [activeQuarter, setActiveQuarter] = useState("Now");
  const [selectedRiskId, setSelectedRiskId] = useState(null);
  const [selectedPersona, setSelectedPersona] = useState(tweaks.persona);

  // Keep persona pick in sync with tweaks
  useEffect(() => {setSelectedPersona(tweaks.persona);}, [tweaks.persona]);

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

  // ---- Logging helper ----
  const log = useCallback((msg) => {
    setLoopLog((prev) => [...prev, { ts: new Date().toISOString(), msg }]);
  }, []);

  // ---- HITL gates ----
  const showGate = (n) => new Promise((res) => {
    gateResRef.current[n] = res;
    setStageState((prev) => ({ ...prev, [`s${n + 1}`]: "waiting" }));
    setGateState((prev) => ({ ...prev, [`g${n}`]: "pending" }));
  });
  const approveGate = (n) => {
    log(`HITL Gate ${n}: APPROVED`);
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

  // ---- Live data fetch helpers ----
  async function tryLiveFetch(overrideConfig) {
    const cfg_data = overrideConfig || dataConfig;
    const allTickers = [cfg.ticker, ...(cfg_data.tickers || [])].filter(Boolean);
    const seriesIds  = cfg_data.fredSeriesIds?.length
      ? cfg_data.fredSeriesIds
      : ['IPG3344S', 'CAPUTLG3311A2S', 'INDPRO', 'FEDFUNDS', 'T10Y2Y', 'TOTALSA'];

    // Fetch EDGAR for target + peers
    setLiveStatus(`Fetching EDGAR for ${allTickers.join(', ')}…`);
    try {
      const factsMap = await LIVE.fetchEdgarMultiple(allTickers, msg => setLiveStatus(msg));
      setRawEdgarFacts(factsMap);
      const targetFacts = factsMap[cfg.ticker];
      if (targetFacts && !targetFacts.error) {
        const extracted = LIVE.extractFinancials(targetFacts);
        setLivefacts(extracted);
        setLiveStatus(`EDGAR OK · ${extracted.entity} · ${allTickers.length} tickers`);
        log(`EDGAR live fetch OK · ${allTickers.join(', ')}`);
      } else {
        setLiveStatus(`EDGAR fetch failed for ${cfg.ticker} · falling back to mock`);
        log(`EDGAR live fetch failed: ${targetFacts?.error || 'unknown'}`);
      }
    } catch (e) {
      setLivefacts(null);
      setRawEdgarFacts(null);
      setLiveStatus(`EDGAR fetch failed: ${e.message}`);
      log(`EDGAR live fetch failed: ${e.message}`);
    }

    // Fetch FRED (live API if key set, else bundled snapshot)
    if (cfg_data.fredApiKey) {
      try {
        setLiveStatus(prev => prev + ' · fetching FRED…');
        const fredRes = await LIVE.fetchFredMultiple(cfg_data.fredApiKey, seriesIds, '2015-01-01',
          msg => setLiveStatus(msg));
        setFredApiResults(fredRes);
        setFredLive(null);
        log(`FRED API loaded · ${Object.keys(fredRes).length} series`);
      } catch (e) {
        setFredApiResults(null);
        log(`FRED API failed: ${e.message}`);
      }
    } else {
      try {
        const fred = await LIVE.loadFred();
        setFredLive(fred.series);
        setFredApiResults(null);
        log(`FRED snapshot loaded · ${Object.keys(fred.series).length} series`);
      } catch (e) {
        setFredLive(null);
        log(`FRED snapshot failed: ${e.message}`);
      }
    }
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
    setLoopLog([]);
    setStageState({});
    setOutput({});
    setGateState({ g1: null, g2: null });
    setEvents([]);
    setNotifLog([]);
    setOpenStages(new Set(["s1"]));
    setSelectedRiskId(null);
    log("Loop started");

    if (liveMode) {
      await tryLiveFetch();
    }

    // STAGE 1 — Signal Intake
    const sigsList = MOCK.signals.filter((s) =>
    s.src === "EDGAR 10-K" && signalSet.has("edgar") ||
    s.src === "Peer 10-K" && signalSet.has("peers") ||
    s.src === "Industry RSS" && signalSet.has("industry") ||
    s.src === "Internal KRI" && signalSet.has("internal") ||
    s.src === "FRED Macro" && signalSet.has("fred") ||
    s.src === "Incident" && signalSet.has("incidents")
    );
    await runStage("s1", { signals: sigsList, sourceCount: signalSet.size }, 1200);

    // STAGE 2 — Risk assessment
    await runStage("s2", { risks: MOCK.risks }, 1500);
    setActiveRailTab("rr");

    // GATE 1 — Risk assessment
    if (hitl.risk) {
      const gres = await showGate(1);
      log(gres.ok ? "Gate 1 passed" : `Gate 1 overridden: ${gres.reason}`);
    }
    setStageState((prev) => ({ ...prev, s3: "idle" }));

    // STAGE 3 — Audit scope
    await runStage("s3", { objectives: MOCK.objectives }, 1500);

    // GATE 2 — Audit scope
    if (hitl.scope) {
      const gres = await showGate(2);
      log(gres.ok ? "Gate 2 passed" : `Gate 2 overridden: ${gres.reason}`);
    }
    setStageState((prev) => ({ ...prev, s4: "idle" }));

    // STAGE 4 — MAPs
    await runStage("s4", { maps: MOCK.maps }, 1400);
    setActiveRailTab("map");

    // STAGE 5 — Closure
    await runStage("s5", { closure: MOCK.closure }, 1200);

    // STAGE 6 — Loop calibration
    await runStage("s6", { loop: MOCK.loop }, 1200);
    setActiveRailTab("loop");

    log("Loop complete");
    setRunning(false);
    setHasRun(true);

    // Fire a synthetic CEM event so the Control Monitor tab has content too.
    setTimeout(() => fireSyntheticEvent(2), 1000 / speed);
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
    setRawEdgarFacts(null);
    setFredApiResults(null);
    setLiveStatus("");
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
    const risksCur = output.s2?.risks || MOCK.risks;
    const top3 = [...risksCur].sort((a, b) => b.score - a.score).slice(0, 3).map((r) => r.name);
    const sigsList = output.s1?.signals || [];
    return {
      entity: `${MOCK.entity.name} (${cfg.ticker})`,
      ts: new Date().toISOString(),
      cfg: {
        industry: cfg.industry,
        focus: cfg.focus,
        sigs: [...signalSet]
      },
      signals: { count: sigsList.length, highVel: sigsList.filter((s) => s.velocity >= 3).length },
      risks: risksCur,
      top3,
      riskAppetite: risksCur.some((r) => r.velocity >= velocity && r.rag === "R") ? "BREACHED" : "WITHIN APPETITE",
      objectives: output.s3?.objectives || [],
      maps: output.s4?.maps || [],
      closure: output.s5?.closure || {},
      loop: output.s6?.loop || {},
      log: loopLog,
      assumptions: [
      "Quarterly score projections use a velocity-dampened linear model (15% per quarter).",
      "Control-effectiveness multipliers: NONE=1.20×, WEAK=1.10×, ADEQUATE=0.95×, STRONG=0.80×.",
      "Likelihood proxy from control effectiveness: NONE=9, WEAK=7, ADEQUATE=5, STRONG=3.",
      "Impact proxy uses the inherent_score field from risk assessment.",
      "Heatmap bubble size = sqrt(impact × likelihood) × 4.6 px radius.",
      "Risk appetite benchmark: score ≥ 7.5 = RED, 5.0–7.4 = AMBER, < 5.0 = GREEN.",
      "All projections assume no material change in control environment beyond velocity trend.",
      `Peer benchmark data sourced against ${cfg.industry}.`],

      obstacles: [
      ...risksCur.filter((r) => (r.velocity || 0) >= 3).map((r) => `High-velocity risk detected: ${r.name} (velocity=${r.velocity}) → escalated audit scope`)]

    };
  }, [hasRun, output, loopLog, signalSet, cfg, velocity]);

  // ---- Sub-tab counts ----
  const mainTabs = [
  { id: "pipe", l: "Pipeline" },
  { id: "cem", l: "Control Monitor", count: events.length, pulse: unreadCEM > 0 },
  { id: "fcst", l: "Forecasts" },
  { id: "scen", l: "Scenarios" }];


  // ---- RENDER ----
  return (
    <div className="app">
      <Header
        cfg={cfg}
        liveMode={liveMode} livefacts={livefacts}
        running={running} hasRun={hasRun} />
      

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
            window.postMessage({type: '__activate_edit_mode'}, '*');
          }}
          onOpenDataConfig={() => setConfigModalOpen(true)}
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

          <div className={"panel" + (activeMainTab === "pipe" ? " active" : "")}>
            <div className="panel-head">
              <div>
                <div className="kicker">Risk → Audit closed loop</div>
                <div className="panel-title mt-8">Six-stage continuous governance chain</div>
                <div className="panel-sub">Each stage feeds structured output to the next. HITL gates pause for human review. Toggle gates in the sidebar.</div>
              </div>
              {hasRun &&
              <div className="mono" style={{ display: "flex", gap: 12, alignItems: "center", color: "var(--ink-3)", fontSize: 11 }}>
                  <span><b style={{ color: "var(--ink)", fontWeight: 500 }}>{output.s2?.risks.length || 0}</b> risks</span>
                  <span><b style={{ color: "var(--ink)", fontWeight: 500 }}>{output.s3?.objectives.length || 0}</b> objectives</span>
                  <span><b style={{ color: "var(--ink)", fontWeight: 500 }}>{output.s4?.maps.length || 0}</b> MAPs</span>
                </div>
              }
            </div>
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
              livefacts={livefacts} />
            
          </div>

          <div className={"panel" + (activeMainTab === "cem" ? " active" : "")}>
            <CEMPanel
              events={events} setEvents={setEvents}
              filter={cemFilter} setFilter={setCemFilter}
              expanded={cemExpanded} setExpanded={setCemExpanded}
              onAckNotif={ackNotif}
              onInject={() => fireSyntheticEvent(1)} />
            
          </div>

          <div className={"panel" + (activeMainTab === "fcst" ? " active" : "")}>
            <ForecastsPanel
              data={hasRun ? MOCK.forecasts : null}
              liveMode={liveMode}
              fredSeries={fredLive}
              rawEdgarFacts={rawEdgarFacts}
              fredApiResults={fredApiResults}
              cfg={cfg}
              onOpenDataConfig={() => setConfigModalOpen(true)} />

          </div>

          <div className={"panel" + (activeMainTab === "scen" ? " active" : "")}>
            <ScenariosPanel scenarios={hasRun ? MOCK.scenarios : null} greySwan={hasRun ? MOCK.greySwan : null} />
          </div>
        </main>

        <Rail
          activeTab={activeRailTab} setActiveTab={setActiveRailTab}
          output={output}
          risks={output.s2?.risks || (hasRun ? MOCK.risks : null)}
          maps={output.s4?.maps || null}
          loop={output.s6?.loop || null}
          notifLog={notifLog}
          forecasts={hasRun ? MOCK.forecasts : null}
          activeQuarter={activeQuarter} setActiveQuarter={setActiveQuarter}
          selectedRiskId={selectedRiskId} setSelectedRiskId={setSelectedRiskId}
          selectedPersona={selectedPersona} setSelectedPersona={setSelectedPersona}
          personas={hasRun ? MOCK.personas : null}
          scenarios={hasRun ? MOCK.scenarios : null} />
        
      </div>

      <ReportModal open={reportOpen} onClose={() => setReportOpen(false)} payload={reportPayload} />
      <OverrideModal open={overrideOpen} gateNum={overrideGateNum} onClose={() => setOverrideOpen(false)} onConfirm={confirmOverride} />
      <DataConfigModal
        open={configModalOpen}
        onClose={() => setConfigModalOpen(false)}
        dataConfig={dataConfig}
        setDataConfig={setDataConfig}
        cfg={cfg}
        onFetchNow={(config) => { if (liveMode) tryLiveFetch(config); }} />

      <DendraiTweaks tweaks={tweaks} setTweak={setTweak}
        hitl={hitl} setHitl={setHitl}
        velocity={velocity} setVelocity={setVelocity} />
    </div>);

}

// ---- Header ----
function Header({ cfg, liveMode, livefacts, running, hasRun }) {
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
        <span style={{ fontSize: 11.5 }}>{livefacts?.entity || MOCK.entity.name}</span>
        <span className="hdr-ctx-pill">{cfg.focus}</span>
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

// ---- Mount ----
const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);