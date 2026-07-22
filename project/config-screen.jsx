/* ============================================================
   Configuration / Setup screen
   Consolidates everything that used to live in the left sidebar +
   the run-config tweaks + the recurring schedule. Persisted by App
   (localStorage in the prototype; AuditConfig table in production).
   ============================================================ */

const CFG_SIGNAL_OPTS = [
  { id: "edgar",     name: "10-K / EDGAR",  sub: "SEC filings" },
  { id: "peers",     name: "Peer 10-Ks",    sub: "Benchmarking" },
  { id: "industry",  name: "Industry RSS",  sub: "Threat feeds" },
  { id: "internal",  name: "Internal KRIs", sub: "Control data" },
  { id: "fred",      name: "FRED Macro",    sub: "Economic" },
  { id: "incidents", name: "Incidents",     sub: "Near-misses" },
];

function ConfigCard({ title, sub, right, children }) {
  return (
    <section className="cfg-card">
      <div className="cfg-card-head">
        <div>
          <div className="cfg-card-title">{title}</div>
          {sub && <div className="cfg-card-sub">{sub}</div>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

function AiChatConfigCard({ aiChatCfg, setAiChatCfg }) {
  const [geminiKey, setGeminiKey] = useState(() => localStorage.getItem("dendrai_gemini_api_key") || "");
  const [showKey, setShowKey] = useState(false);

  const saveGeminiKey = (val) => {
    setGeminiKey(val);
    if (val.trim()) {
      localStorage.setItem("dendrai_gemini_api_key", val.trim());
    } else {
      localStorage.removeItem("dendrai_gemini_api_key");
    }
  };

  const setProvider = (p) => {
    const label = p === "gemini" ? "Ask Gemini" : "Ask Claude";
    setAiChatCfg({ ...aiChatCfg, provider: p, buttonLabel: label });
  };

  return (
    <ConfigCard
      title="AI Chat Assistant"
      sub="Configure the chat button label, AI provider, and API keys for the slide-out chat panel."
    >
      {/* Provider selector */}
      <div className="field">
        <label className="field-label">AI Provider</label>
        <div style={{ display: "flex", gap: 6 }}>
          {[
            { id: "claude", label: "Claude", note: "Tool access · server key" },
            { id: "gemini", label: "Gemini", note: "Conversational · client key" },
          ].map(p => (
            <button
              key={p.id}
              type="button"
              className={"hitl-toggle" + (aiChatCfg.provider === p.id ? " on" : "")}
              style={{ flex: 1, flexDirection: "column", alignItems: "flex-start", gap: 2, padding: "6px 10px" }}
              onClick={() => setProvider(p.id)}
            >
              <span style={{ fontWeight: 600, fontSize: 12 }}>{p.label}</span>
              <span style={{ fontSize: 10, opacity: 0.7, fontWeight: 400 }}>{p.note}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Button label */}
      <div className="field">
        <label className="field-label">Button label</label>
        <input
          className="input"
          type="text"
          maxLength={32}
          placeholder="Ask Claude"
          value={aiChatCfg.buttonLabel || ""}
          onChange={e => setAiChatCfg({ ...aiChatCfg, buttonLabel: e.target.value })}
        />
      </div>

      {/* Gemini API key (only when Gemini is selected) */}
      {aiChatCfg.provider === "gemini" && (
        <div className="field" style={{ marginBottom: 0 }}>
          <label className="field-label">Gemini API key</label>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              className="input"
              type={showKey ? "text" : "password"}
              placeholder="AIza…"
              value={geminiKey}
              onChange={e => saveGeminiKey(e.target.value)}
              style={{ flex: 1, fontFamily: geminiKey ? "var(--mono, monospace)" : "inherit", fontSize: 12 }}
            />
            <button type="button" className="btn btn-sm btn-ghost"
              onClick={() => setShowKey(v => !v)} title={showKey ? "Hide" : "Show"}>
              {showKey ? "Hide" : "Show"}
            </button>
          </div>
          <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 4 }}>
            Stored in browser localStorage. Get a key at aistudio.google.com.
          </div>
        </div>
      )}

      {/* Claude key note */}
      {aiChatCfg.provider === "claude" && (
        <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)", lineHeight: 1.5 }}>
          Claude uses the server-side ANTHROPIC_API_KEY from project/agentic-tools/.env.
          Start api_server.py to enable tool access.
        </div>
      )}
    </ConfigCard>
  );
}

function ConfigScreen({
  cfg, setCfg, signalSet, setSignalSet,
  velocity, setVelocity, hitl, setHitl,
  liveMode, setLiveMode, mcpMode, setMcpMode, useDb, setUseDb, liveStatus,
  lastSaved, rssEnabledFeeds, setRssEnabledFeeds,
  aiChatCfg, setAiChatCfg,
  colorScheme, setColorScheme,
  accent, setAccent,
}) {
  const focusList = Array.isArray(cfg.focus) ? cfg.focus : [cfg.focus].filter(Boolean);

  const toggleSig = (id) => {
    const next = new Set(signalSet);
    next.has(id) ? next.delete(id) : next.add(id);
    setSignalSet(next);
  };

  return (
    <div className="cfg-screen" data-screen-label="Configuration">
      <div className="panel-head">
        <div>
          <div className="kicker">Configuration</div>
          <div className="panel-title mt-8">Mission Control</div>
          <div className="panel-sub">Entity, signal sources, and run configuration. Changes save automatically. Recurring runs are provisioned from the Loop tab after your first run.</div>
        </div>
        <div className="cfg-saved mono">
          <span className="live-dot on" /> {lastSaved ? `Saved ${new Date(lastSaved).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit"})}` : "Autosave on"}
        </div>
      </div>

      <div className="cfg-grid">
        {/* ---- Entity ---- */}
        <ConfigCard title="Entity" sub="Company, industry, and audit period under review.">
          <div className="field">
            <label className="field-label">Company / Ticker</label>
            <input className="input" type="text" placeholder="e.g. ON, TXN, NVDA, KR, F"
              value={cfg.ticker}
              onChange={e => {
                const raw = e.target.value;
                const lookup = findTickerMeta(raw);
                setCfg({ ...cfg, ticker: raw, ...(lookup ? { industry: lookup.meta.industry, company: lookup.meta.name } : {}) });
              }}
              onBlur={async e => {
                const raw = e.target.value.trim();
                const lookup = findTickerMeta(raw);
                if (lookup) { setCfg(prev => ({ ...prev, ticker: lookup.ticker, company: lookup.meta.name, industry: lookup.meta.industry })); return; }
                const secMeta = await resolveIndustryFromSec(raw);
                if (secMeta) { setCfg(prev => ({ ...prev, ticker: secMeta.ticker, company: secMeta.name || prev.company, industry: secMeta.industry || prev.industry })); return; }
                if (raw) setCfg(prev => ({ ...prev, ticker: raw.toUpperCase() }));
              }} />
            {(() => {
              const meta = TICKER_META[cfg.ticker?.toUpperCase?.()];
              return meta ? <div className="mono" style={{fontSize:10,color:"var(--ink-3)",marginTop:3}}>{meta.name}</div> : null;
            })()}
          </div>
          <div className="field">
            <label className="field-label">Industry</label>
            <select className="input" value={cfg.industry} onChange={e => setCfg({...cfg, industry: e.target.value})}>
              <option>Analog Semiconductors</option>
              <option>Digital Semiconductors</option>
              <option>Semiconductor Equipment</option>
              <option>Memory Semiconductors</option>
              <option>Industrial / Manufacturing</option>
              <option>Energy / Utilities</option>
              <option>Retail</option>
            </select>
          </div>
          <div className="field" style={{marginBottom:0}}>
            <label className="field-label">Audit Period</label>
            <div style={{display:"flex", gap:6, alignItems:"center"}}>
              <select className="input" style={{flex:1}} value={cfg.periodBegin || FISCAL_QUARTERS[4].value}
                onChange={e => setCfg({...cfg, periodBegin: e.target.value})}>
                {FISCAL_QUARTERS.map(q => <option key={q.value} value={q.value}>{q.label}</option>)}
              </select>
              <span className="mono" style={{fontSize:10,color:"var(--ink-3)",flexShrink:0}}>→</span>
              <select className="input" style={{flex:1}} value={cfg.periodEnd || FISCAL_QUARTERS[7].value}
                onChange={e => setCfg({...cfg, periodEnd: e.target.value})}>
                {FISCAL_QUARTERS.map(q => <option key={q.value} value={q.value}>{q.label}</option>)}
              </select>
            </div>
          </div>
        </ConfigCard>

        {/* ---- Audit Universe Focus ---- */}
        <ConfigCard title="Audit Universe Focus" sub="Risk domains in scope for this entity."
          right={
            <button type="button" className="cfg-link"
              onClick={() => {
                const all = focusList.length === FOCUS_OPTS.length;
                setCfg({...cfg, focus: all ? [FOCUS_OPTS[0]] : [...FOCUS_OPTS]});
              }}>
              {focusList.length === FOCUS_OPTS.length ? "Clear" : "Select all"} ({focusList.length}/{FOCUS_OPTS.length})
            </button>
          }>
          <div className="focus-grid">
            {FOCUS_OPTS.map(f => {
              const on = focusList.includes(f);
              return (
                <button key={f} type="button" className={"focus-chip" + (on ? " on" : "")}
                  onClick={() => {
                    const next = on ? focusList.filter(x => x !== f) : [...focusList, f];
                    setCfg({...cfg, focus: next.length ? next : [f]});
                  }}>
                  <span className="focus-chip-check" aria-hidden="true">{on ? <Icon name="check" size={9}/> : null}</span>
                  <span className="focus-chip-lbl">{f}</span>
                </button>
              );
            })}
          </div>
        </ConfigCard>

        {/* ---- Signal Sources ---- */}
        <ConfigCard title="Signal Sources" sub="Feeds ingested at Stage 1 of the loop."
          right={<span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>{signalSet.size}/6</span>}>
          <div className="sig-grid">
            {CFG_SIGNAL_OPTS.map(s => (
              <button key={s.id} className={"sig" + (signalSet.has(s.id) ? " on" : "")} onClick={() => toggleSig(s.id)}>
                <div className="sig-name">{s.name}</div>
                <div className="sig-sub">{s.sub}</div>
              </button>
            ))}
          </div>
        </ConfigCard>

        {/* ---- RSS Feed Sources (shown when Industry RSS is enabled) ---- */}
        {signalSet.has("industry") && (
          <ConfigCard title="RSS Feed Sources"
            sub="Live feeds polled during signal ingestion. All fetches are live — no simulated fallback."
            right={
              <span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>
                {(rssEnabledFeeds || []).length}/{RSS_ENGINE.FEEDS.length} enabled
              </span>
            }>
            <div className="sig-grid">
              {RSS_ENGINE.FEEDS.map(feed => {
                const on = (rssEnabledFeeds || []).includes(feed.id);
                return (
                  <button key={feed.id}
                    className={"sig" + (on ? " on" : "")}
                    onClick={() => {
                      const current = rssEnabledFeeds || RSS_ENGINE.FEEDS.map(f => f.id);
                      const next = on
                        ? current.filter(id => id !== feed.id)
                        : [...current, feed.id];
                      // keep at least one feed enabled
                      if (next.length > 0) setRssEnabledFeeds(next);
                    }}>
                    <div className="sig-name">{feed.name}</div>
                    <div className="sig-sub">{feed.domains.join(" · ")}</div>
                  </button>
                );
              })}
            </div>
            <div className="mono" style={{fontSize:10, color:"var(--ink-3)", marginTop:6, lineHeight:1.5}}>
              Fetched via /api/rss-proxy · failed feeds show as FAILED (no fallback data)
            </div>
          </ConfigCard>
        )}

        {/* ---- Run Configuration ---- */}
        <ConfigCard title="Run Configuration" sub="Velocity threshold, human-in-the-loop gates, and risk appetite.">
          <div className="field">
            <label className="field-label">
              <span>Velocity threshold</span>
              <span className="mono" style={{fontSize:11, color:"var(--acc-ink)"}}>{Number(velocity).toFixed(1)}</span>
            </label>
            <input type="range" min="0" max="5" step="0.5" value={velocity}
              onChange={e => setVelocity(parseFloat(e.target.value))} style={{width:"100%"}} />
          </div>

          <div className="field">
            <label className="field-label">Review Gates</label>
            <div className="hitl-toggle-row">
              {[
                { k: "risk",  l: "Risk assessment" },
                { k: "scope", l: "Audit scope" },
                { k: "map",   l: "MAP generation" },
              ].map(g => (
                <button key={g.k} type="button"
                  className={"hitl-toggle" + (hitl[g.k] ? " on" : "")}
                  onClick={() => setHitl({ ...hitl, [g.k]: !hitl[g.k] })}>
                  <span className="hitl-toggle-dot">{hitl[g.k] ? <Icon name="check" size={9}/> : null}</span>
                  {g.l}
                </button>
              ))}
            </div>
          </div>

          <div className="field" style={{marginBottom:0}}>
            <label className="field-label">Risk Appetite</label>
            <div className="appetite-sel">
              {[
                {lvl:"GREEN", sub:"Conservative · ≤ GREEN"},
                {lvl:"AMBER", sub:"Moderate · ≤ AMBER"},
                {lvl:"RED",   sub:"Permissive · ≤ RED"},
              ].map(({lvl, sub}) => {
                const active = (cfg.appetiteLevel || "AMBER") === lvl;
                const colors = { GREEN:"var(--green-ink)", AMBER:"var(--amber-ink)", RED:"var(--red-ink)" };
                const softs  = { GREEN:"var(--green-soft)", AMBER:"var(--amber-soft)", RED:"var(--red-soft)" };
                return (
                  <button key={lvl} className={"appetite-btn" + (active ? " active" : "")}
                    style={active ? {background:softs[lvl], color:colors[lvl], borderColor:colors[lvl]} : {}}
                    onClick={() => setCfg({...cfg, appetiteLevel: lvl})}>
                    <div style={{fontWeight:600}}>{lvl}</div>
                    <div style={{fontSize:9, opacity:0.8}}>{sub}</div>
                  </button>
                );
              })}
            </div>
          </div>
        </ConfigCard>

        {/* ---- Data Mode ---- */}
        <ConfigCard title="Data Mode" sub="Source for entity financials and signals."
          right={
            <span className="mono" style={{fontSize:10, color: mcpMode ? "var(--acc-ink)" : liveMode ? "var(--green-ink)" : "var(--ink-3)"}}>
              <span className={`live-dot ${liveMode || mcpMode ? "on" : ""}`} style={{display:"inline-block", marginRight:4, verticalAlign:1}}/>
              {mcpMode ? "MCP" : liveMode ? "LIVE" : "MOCK"}
            </span>
          }>
          <div style={{display:"flex", gap:6}}>
            <button className={`btn btn-sm ${!liveMode && !mcpMode ? "btn-primary" : ""}`} style={{flex:1}}
              onClick={() => { setLiveMode(false); setMcpMode(false); }}>Mock</button>
            <button className={`btn btn-sm ${liveMode && !mcpMode ? "btn-primary" : ""}`} style={{flex:1}}
              onClick={() => { setLiveMode(true); setMcpMode(false); }}><Icon name="wifi" size={11}/> Live</button>
            <button className={`btn btn-sm ${mcpMode ? "btn-primary" : ""}`} style={{flex:1}}
              onClick={() => { setMcpMode(true); setLiveMode(false); }} title="Use Python MCP servers (api_server.py must be running)">
              <Icon name="gear" size={11}/> MCP</button>
          </div>
          {mcpMode && (
            <div style={{marginTop:8}}>
              <div style={{fontSize:10, color:"var(--ink-4)", marginBottom:4}}>Data source when MCP is active</div>
              <div style={{display:"flex", gap:6}}>
                <button className={`btn btn-sm ${!useDb ? "btn-primary" : ""}`} style={{flex:1}}
                  onClick={() => setUseDb(false)}
                  title="Fetch fresh data from EDGAR / FRED / APIs">
                  <Icon name="wifi" size={11}/> Live
                </button>
                <button className={`btn btn-sm ${useDb ? "btn-primary" : ""}`} style={{flex:1}}
                  onClick={() => setUseDb(true)}
                  title="Use data already cached in PostgreSQL — faster, no external calls">
                  <Icon name="database" size={11}/> DB Cache
                </button>
              </div>
            </div>
          )}
          <div style={{marginTop:8, fontSize:10.5, color:"var(--ink-3)", lineHeight:1.5}}>
            {mcpMode
              ? (useDb
                  ? "Using PostgreSQL cache — no external API calls."
                  : (liveStatus || "Fetching live from EDGAR / FRED via MCP servers."))
              : liveMode ? (liveStatus || "EDGAR via data.sec.gov · FRED snapshot bundled")
              : "Mock dataset — no external calls."}
          </div>
        </ConfigCard>

        {/* ---- Appearance ---- */}
        <ConfigCard title="Appearance" sub="Color scheme and accent for the dashboard — saved to your account and follows you across browsers and machines.">
          <div className="field">
            <label className="field-label">Color scheme</label>
            <div style={{ display: "flex", gap: 6 }}>
              {[
                { value: "light",  label: "Light" },
                { value: "dark",   label: "Dark" },
                { value: "system", label: "System" },
              ].map(opt => (
                <button key={opt.value}
                  className={`btn btn-sm${colorScheme === opt.value ? " btn-primary" : ""}`}
                  style={{ flex: 1 }}
                  onClick={() => setColorScheme(opt.value)}>
                  {opt.label}
                </button>
              ))}
            </div>
            <div style={{ marginTop: 6, fontSize: 10, color: "var(--ink-3)" }}>
              {colorScheme === "system"
                ? "Follows your OS dark/light setting automatically."
                : colorScheme === "dark" ? "Always uses dark theme."
                : "Always uses light theme."}
            </div>
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label className="field-label">Accent color</label>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {[
                { value: "emerald", label: "Emerald" },
                { value: "indigo",  label: "Indigo" },
                { value: "slate",   label: "Slate" },
                { value: "forest",  label: "Dendrai Forest Green" },
              ].map(opt => (
                <button key={opt.value}
                  className={`btn btn-sm${accent === opt.value ? " btn-primary" : ""}`}
                  style={{ flex: "1 1 auto" }}
                  onClick={() => setAccent(opt.value)}>
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        </ConfigCard>

        {/* ---- AI Chat Assistant ---- */}
        <AiChatConfigCard aiChatCfg={aiChatCfg} setAiChatCfg={setAiChatCfg} />

        {/* ---- Investigation Agent (#1) ---- */}
        <InvestigationCard ticker={cfg.ticker} />
      </div>
    </div>
  );
}

// #1 — Tool-use investigation agent. Uses SSE streaming endpoint so each tool
// call result appears live in the UI rather than waiting for the full response.
function InvestigationCard({ ticker }) {
  const [focus, setFocus] = useState("");
  const [state, setState] = useState({ loading: false, error: null, result: null });
  const [trace, setTrace] = useState([]); // live tool-call events
  const aiAvailable = typeof window !== "undefined" && (window.MCP?.agentInvestigateStream || window.MCP?.agentInvestigate);
  const hasStream = typeof window !== "undefined" && window.MCP?.agentInvestigateStream;

  async function run() {
    if (!aiAvailable || !ticker) return;
    setState({ loading: true, error: null, result: null });
    setTrace([]);
    try {
      let res;
      if (hasStream) {
        res = await window.MCP.agentInvestigateStream(ticker, focus, null, (event) => {
          if (event.type === "tool_call") {
            setTrace(prev => [...prev, { kind: "call", tool: event.tool, iteration: event.iteration }]);
          } else if (event.type === "tool_result") {
            setTrace(prev => [...prev, { kind: "result", tool: event.tool, preview: event.result_preview, isError: event.is_error, iteration: event.iteration }]);
          }
        });
      } else {
        res = await window.MCP.agentInvestigate(ticker, focus, null);
      }
      setState({ loading: false, error: null, result: res });
    } catch (e) {
      setState({ loading: false, error: e.message || "AI unavailable", result: null });
    }
  }

  const TOOL_LABELS = {
    get_financials:    "Fetching EDGAR financials",
    get_risk_factors:  "Reading 10-K risk factors",
    get_8k_events:     "Scanning 8-K events",
    get_peers:         "Benchmarking peers",
    get_industry_news: "Ingesting industry RSS",
    run_quant_models:  "Running quant models",
  };

  return (
    <ConfigCard title="Investigation Agent" sub="Claude investigates the company like an auditor — pulls filings, peers, and quant models, then writes a memo.">
      {!aiAvailable ? (
        <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", lineHeight: 1.5}}>
          Requires the Python bridge with ANTHROPIC_API_KEY set. Start api_server.py and reload.
        </div>
      ) : (
        <>
          <input className="fi-input" value={focus} onChange={e => setFocus(e.target.value)}
            placeholder={`Optional focus (e.g. "margin trend", "8-K events") for ${ticker || "entity"}`}
            style={{width: "100%", marginBottom: 8, fontSize: 12}}/>
          <button className="btn btn-sm" style={{width: "100%"}} onClick={run} disabled={state.loading || !ticker}>
            <Icon name="spark" size={11}/> {state.loading ? "Investigating…" : "Run investigation"}
          </button>

          {/* Live thinking trace (streaming) */}
          {state.loading && trace.length > 0 && (
            <div style={{marginTop: 8, background:"var(--surface-2)", border:"1px solid var(--line)",
              borderRadius: 6, padding:"8px 10px", maxHeight: 180, overflowY: "auto"}}>
              <div className="mono" style={{fontSize:9.5, color:"var(--ink-4)", marginBottom:5, letterSpacing:"0.06em"}}>AGENT TRACE</div>
              {trace.map((t, i) => (
                <div key={i} style={{display:"flex", gap:6, padding:"3px 0", fontSize:10.5,
                  color: t.kind === "result" && t.isError ? "var(--red-ink)" : t.kind === "result" ? "var(--green-ink)" : "var(--acc-ink)"}}>
                  <span className="mono" style={{flexShrink:0, fontSize:9.5, opacity:0.7}}>i{t.iteration}</span>
                  <span className="mono" style={{flexShrink:0}}>{t.kind === "call" ? "→" : "←"}</span>
                  <span>{t.kind === "call" ? (TOOL_LABELS[t.tool] || t.tool) : t.tool + (t.isError ? " error" : " ✓")}</span>
                </div>
              ))}
              {state.loading && <div style={{display:"flex", gap:6, marginTop:4}}><span className="spin" style={{width:10,height:10,borderWidth:1.5}}/><span style={{fontSize:10.5, color:"var(--ink-3)"}}>thinking…</span></div>}
            </div>
          )}

          {state.error && (
            <div className="mono" style={{fontSize: 10.5, color: "var(--red-ink)", marginTop: 8}}>
              {state.error}
            </div>
          )}
          {state.result && (
            <div style={{marginTop: 10}}>
              <div className="mono" style={{fontSize: 10, color: "var(--ink-3)", marginBottom: 6}}>
                {state.result.iterations} iterations · {(state.result.tool_calls || []).length} tool calls
                {(state.result.tool_calls || []).length > 0 && (
                  <span> · {[...new Set((state.result.tool_calls || []).map(t => t.tool))].join(", ")}</span>
                )}
              </div>
              <div style={{whiteSpace: "pre-wrap", fontSize: 12, color: "var(--ink-2)", lineHeight: 1.65,
                background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 6, padding: "12px 14px",
                maxHeight: 360, overflowY: "auto"}}>
                {state.result.final_text}
              </div>
            </div>
          )}
        </>
      )}
    </ConfigCard>
  );
}

Object.assign(window, { ConfigScreen });
