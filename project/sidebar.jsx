/* ============================================================
   Sidebar (LEFT) — entity, signals, velocity, HITL toggles, Run
   ============================================================ */

const FOCUS_OPTS = [
  "Revenue Recognition",
  "Supply Chain and Procurement",
  "IT General Controls",
  "Financial Reporting",
  "Trade Compliance / Export",
  "ESG / Climate Risk",
  "M&A Integration",
  "Cybersecurity",
];

const TICKER_META = {
  ON:   { name: "onsemi",                   industry: "Analog Semiconductors" },
  TXN:  { name: "Texas Instruments",        industry: "Analog Semiconductors" },
  STM:  { name: "STMicroelectronics",       industry: "Analog Semiconductors" },
  MCHP: { name: "Microchip Technology",     industry: "Analog Semiconductors" },
  NXPI: { name: "NXP Semiconductors",       industry: "Analog Semiconductors" },
  ADI:  { name: "Analog Devices",           industry: "Analog Semiconductors" },
  SWKS: { name: "Skyworks Solutions",       industry: "Analog Semiconductors" },
  QRVO: { name: "Qorvo",                    industry: "Analog Semiconductors" },
  MPWR: { name: "Monolithic Power Systems", industry: "Analog Semiconductors" },
  WOLF: { name: "Wolfspeed",                industry: "Analog Semiconductors" },
  AVGO: { name: "Broadcom",                 industry: "Digital Semiconductors" },
  NVDA: { name: "NVIDIA",                   industry: "Digital Semiconductors" },
  INTC: { name: "Intel",                    industry: "Digital Semiconductors" },
  AMD:  { name: "Advanced Micro Devices",   industry: "Digital Semiconductors" },
  QCOM: { name: "Qualcomm",                 industry: "Digital Semiconductors" },
  MRVL: { name: "Marvell Technology",       industry: "Digital Semiconductors" },
  AMAT: { name: "Applied Materials",        industry: "Semiconductor Equipment" },
  KLAC: { name: "KLA Corporation",          industry: "Semiconductor Equipment" },
  LRCX: { name: "Lam Research",             industry: "Semiconductor Equipment" },
  ASML: { name: "ASML Holding",             industry: "Semiconductor Equipment" },
  AMKR: { name: "AMKOR Technology",         industry: "Semiconductor Equipment" },
  ONTO: { name: "Onto Innovation",           industry: "Semiconductor Equipment" },
  TER:  { name: "Teradyne",                 industry: "Semiconductor Equipment" },
  ENTG: { name: "Entegris",                 industry: "Semiconductor Equipment" },
  MU:   { name: "Micron Technology",        industry: "Memory Semiconductors" },
  WDC:  { name: "Western Digital",          industry: "Memory Semiconductors" },
  SKX:  { name: "SK Hynix",                 industry: "Memory Semiconductors" },
  KR:   { name: "Kroger",                   industry: "Retail" },
  F:    { name: "Ford Motor Company",       industry: "Industrial / Manufacturing" },
};

function findTickerMeta(input) {
  const normalized = input?.toUpperCase?.().trim();
  if (!normalized) return null;
  const direct = TICKER_META[normalized];
  if (direct) return { ticker: normalized, meta: direct };

  const exact = Object.entries(TICKER_META).find(([, value]) => value.name.toUpperCase() === normalized);
  if (exact) return { ticker: exact[0], meta: exact[1] };

  const fuzzy = Object.entries(TICKER_META).find(([, value]) =>
    value.name.toUpperCase().includes(normalized) || normalized.includes(value.name.toUpperCase())
  );
  if (fuzzy) return { ticker: fuzzy[0], meta: fuzzy[1] };

  return null;
}

async function resolveIndustryFromSec(raw) {
  if (!window.LIVE?.fetchEdgarProfile) return null;
  try {
    const profile = await window.LIVE.fetchEdgarProfile(raw);
    return profile;
  } catch (e) {
    return null;
  }
}

function quarterDateRange(year, q) {
  const startMonth = (q - 1) * 3;
  const start = new Date(Date.UTC(year, startMonth, 1));
  const end = new Date(Date.UTC(year, startMonth + 3, 0));
  const fmt = d => d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  return `${fmt(start)} – ${fmt(end)}`;
}

// Preceding fiscal year, current fiscal year, and two future fiscal years —
// recomputed from today's date so the list never goes stale.
function genFiscalQuarters() {
  const currentYear = new Date().getFullYear();
  const years = [currentYear - 1, currentYear, currentYear + 1, currentYear + 2];
  const qtrs = [];
  for (const yr of years) {
    for (let q = 1; q <= 4; q++) {
      qtrs.push({ value: `Q${q} ${yr}`, label: `Q${q} ${yr} (${quarterDateRange(yr, q)})` });
    }
  }
  return qtrs;
}
const FISCAL_QUARTERS = genFiscalQuarters();

function Sidebar({
  cfg, setCfg, signalSet, setSignalSet,
  velocity,
  hitl,
  running, hasRun, onRun, onReset, onOpenReport, onOpenPersona, onOpenConfig,
  liveMode, setLiveMode,
  mcpMode, setMcpMode,
  useDb, setUseDb,
  liveStatus,
}) {
  const SIGNAL_OPTS = [
    { id: "edgar",     name: "10-K / EDGAR",     sub: "SEC filings" },
    { id: "peers",     name: "Peer 10-Ks",       sub: "Benchmarking" },
    { id: "industry",  name: "Industry RSS",     sub: "Threat feeds" },
    { id: "internal",  name: "Internal KRIs",    sub: "Control data" },
    { id: "fred",      name: "FRED Macro",       sub: "Economic" },
    { id: "incidents", name: "Incidents",        sub: "Near-misses" },
  ];
  const toggleSig = (id) => {
    const next = new Set(signalSet);
    next.has(id) ? next.delete(id) : next.add(id);
    setSignalSet(next);
  };

  return (
    <aside className="lsb" data-screen-label="Sidebar config">
      {/* Entity */}
      <div className="lsb-section">
        <div className="sec-lbl">Entity</div>
        <div className="field">
          <label className="field-label">Company / Ticker</label>
          <input
            className="input"
            type="text"
            placeholder="e.g. ON, TXN, NVDA, KR, F, FORD"
            value={cfg.ticker}
            onChange={e => {
              const raw = e.target.value;
              const lookup = findTickerMeta(raw);
              setCfg({
                ...cfg,
                ticker: raw,
                ...(lookup ? { industry: lookup.meta.industry, company: lookup.meta.name } : {}),
              });
            }}
            onBlur={async e => {
              const raw = e.target.value.trim();
              const lookup = findTickerMeta(raw);
              if (lookup) {
                setCfg(prev => ({
                  ...prev,
                  ticker: lookup.ticker,
                  company: lookup.meta.name,
                  industry: lookup.meta.industry,
                }));
                return;
              }

              const secMeta = await resolveIndustryFromSec(raw);
              if (secMeta) {
                setCfg(prev => ({
                  ...prev,
                  ticker: secMeta.ticker,
                  company: secMeta.name || prev.company,
                  industry: secMeta.industry || prev.industry,
                }));
                return;
              }

              if (raw) {
                setCfg(prev => ({ ...prev, ticker: raw.toUpperCase() }));
              }
            }}
          />
          {(() => {
            const meta = TICKER_META[cfg.ticker?.toUpperCase?.()];
            return meta ? (
              <div className="mono" style={{fontSize:10,color:"var(--ink-3)",marginTop:3}}>{meta.name}</div>
            ) : null;
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
          <div className="mono" style={{fontSize:10,color:"var(--ink-3)",marginTop:3}}>Beginning → Ending fiscal quarter</div>
        </div>
        <div className="field" style={{marginBottom: 0}}>
          <label className="field-label">
            <span>Audit Universe Focus</span>
            <span className="field-label-meta">
              {(() => {
                const focusList = Array.isArray(cfg.focus) ? cfg.focus : [cfg.focus].filter(Boolean);
                return (
                  <>
                    <span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{focusList.length}/{FOCUS_OPTS.length}</span>
                    <button type="button" className="cfg-link" style={{padding: 0}}
                      onClick={() => {
                        const all = focusList.length === FOCUS_OPTS.length;
                        setCfg({...cfg, focus: all ? [FOCUS_OPTS[0]] : [...FOCUS_OPTS]});
                      }}>
                      {focusList.length === FOCUS_OPTS.length ? "Clear" : "All"}
                    </button>
                  </>
                );
              })()}
            </span>
          </label>
          <div className="focus-grid">
            {FOCUS_OPTS.map(f => {
              const focusList = Array.isArray(cfg.focus) ? cfg.focus : [cfg.focus].filter(Boolean);
              const on = focusList.includes(f);
              return (
                <button key={f} type="button" className={"focus-chip" + (on ? " on" : "")}
                  onClick={() => {
                    const next = on
                      ? focusList.filter(x => x !== f)
                      : [...focusList, f];
                    // Always keep at least one selected
                    setCfg({...cfg, focus: next.length ? next : [f]});
                  }}>
                  <span className="focus-chip-check" aria-hidden="true">
                    {on ? <Icon name="check" size={9}/> : null}
                  </span>
                  <span className="focus-chip-lbl">{f}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Data mode */}
      <div className="lsb-section tight">
        <SectionLabel right={
          <span className="mono" style={{fontSize:10, color: mcpMode ? "var(--accent-ink,#2563eb)" : liveMode ? "var(--green-ink)" : "var(--ink-3)"}}>
            <span className={`live-dot ${liveMode || mcpMode ? "on" : ""}`} style={{display:"inline-block", marginRight: 4, verticalAlign: 1}}/>
            {mcpMode ? "MCP" : liveMode ? "LIVE" : "MOCK"}
          </span>
        }>Data Mode</SectionLabel>
        <div style={{display:"flex", gap: 6}}>
          <button className={`btn btn-sm ${!liveMode && !mcpMode ? "btn-primary" : ""}`} style={{flex:1}}
            onClick={() => { setLiveMode(false); setMcpMode(false); }}>
            Mock
          </button>
          <button className={`btn btn-sm ${liveMode && !mcpMode ? "btn-primary" : ""}`} style={{flex:1}}
            onClick={() => { setLiveMode(true); setMcpMode(false); }}>
            <Icon name="wifi" size={11}/> Live
          </button>
          <button className={`btn btn-sm ${mcpMode ? "btn-primary" : ""}`} style={{flex:1}}
            onClick={() => { setMcpMode(true); setLiveMode(false); }}
            title="Use Python MCP servers (api_server.py must be running)">
            <Icon name="gear" size={11}/> MCP
          </button>
        </div>
        {mcpMode && (
          <div style={{marginTop:6}}>
            <div style={{display:"flex", gap:5}}>
              <button className={`btn btn-sm ${!useDb ? "btn-primary" : ""}`} style={{flex:1}}
                onClick={() => setUseDb?.(false)} title="Fetch fresh data from EDGAR / FRED">
                <Icon name="wifi" size={10}/> Live
              </button>
              <button className={`btn btn-sm ${useDb ? "btn-primary" : ""}`} style={{flex:1}}
                onClick={() => setUseDb?.(true)} title="Use data cached in PostgreSQL">
                <Icon name="database" size={10}/> DB Cache
              </button>
            </div>
          </div>
        )}
        {(liveMode || mcpMode) && (
          <div style={{marginTop: 6, fontSize: 10.5, color: "var(--ink-3)", lineHeight: 1.5}}>
            {mcpMode
              ? (useDb ? "PostgreSQL cache · no external calls" : (liveStatus || "Python MCP servers · api_server.py"))
              : (liveStatus || "EDGAR via data.sec.gov · FRED snapshot bundled")}
          </div>
        )}
      </div>

      {/* Signals */}
      <div className="lsb-section">
        <SectionLabel right={<span className="mono" style={{fontSize:10, color:"var(--ink-3)"}}>{signalSet.size}/6</span>}>Signal Sources</SectionLabel>
        <div className="sig-grid">
          {SIGNAL_OPTS.map(s => (
            <button key={s.id} className={"sig" + (signalSet.has(s.id) ? " on" : "")} onClick={() => toggleSig(s.id)}>
              <div className="sig-name">{s.name}</div>
              <div className="sig-sub">{s.sub}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Run configuration — lives in Tweaks panel */}
      <div className="lsb-section tight">
        <SectionLabel right={
          <button className="cfg-link" onClick={onOpenConfig} type="button">
            Open <Icon name="chev-r" size={10}/>
          </button>
        }>Run Configuration</SectionLabel>
        <div className="cfg-summary">
          <div className="cfg-row">
            <span className="cfg-lab">Velocity threshold</span>
            <span className="cfg-val mono">{velocity.toFixed(1)}</span>
          </div>
          <div className="cfg-row">
            <span className="cfg-lab">Review gates</span>
            <span className="cfg-val mono">
              <span className={hitl.risk ? "on" : "off"} title="Risk assessment">R</span>
              <span className={hitl.scope ? "on" : "off"} title="Audit scope">S</span>
              <span className={hitl.map ? "on" : "off"} title="MAP generation">M</span>
            </span>
          </div>
          <div className="cfg-row" style={{alignItems:"flex-start", flexDirection:"column", gap:4}}>
            <span className="cfg-lab">Risk appetite</span>
            <div className="appetite-sel">
              {[
                {lvl:"GREEN", label:"Conservative", sub:"≤ GREEN"},
                {lvl:"AMBER", label:"Moderate",     sub:"≤ AMBER"},
                {lvl:"RED",   label:"Permissive",   sub:"≤ RED"},
              ].map(({lvl, label, sub}) => {
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
        </div>
        <div className="cfg-hint">
          Velocity threshold, review gates, and risk appetite level configure the loop. Appetite level marks residual risks that breach tolerance on the Sankey. Edit velocity and gates in Tweaks.
        </div>
      </div>

      {/* Run cluster pinned bottom */}
      <div className="run-cluster">
        <button className="btn btn-acc btn-block" disabled={running} onClick={onRun}>
          {running ? <><span className="spin"/> Running loop…</> : <><Icon name="play" size={12}/> Run Loop</>}
        </button>
        {hasRun && (
          <>
            <div className="row">
              <button className="btn btn-sm" onClick={onOpenReport}><Icon name="doc" size={11}/> Loop report</button>
              <button className="btn btn-sm" onClick={onOpenPersona}><Icon name="user" size={11}/> Persona</button>
            </div>
            <div className="row">
              <button className="btn btn-sm" onClick={onReset} style={{flex:1}}><Icon name="reset" size={11}/> Reset</button>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}

Object.assign(window, {
  Sidebar,
  FOCUS_OPTS, TICKER_META, FISCAL_QUARTERS, findTickerMeta, resolveIndustryFromSec,
});
