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

function Sidebar({
  cfg, setCfg, signalSet, setSignalSet,
  velocity,
  hitl,
  running, hasRun, onRun, onReset, onOpenReport, onOpenPersona, onOpenConfig,
  liveMode, setLiveMode, liveStatus,
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
          <select className="input" value={cfg.ticker} onChange={e => setCfg({...cfg, ticker: e.target.value})}>
            <option value="ON">onsemi (ON)</option>
            <option value="TXN">Texas Instruments (TXN)</option>
            <option value="STM">STMicroelectronics (STM)</option>
            <option value="MCHP">Microchip Technology (MCHP)</option>
            <option value="NXPI">NXP Semiconductors (NXPI)</option>
            <option value="AVGO">Broadcom (AVGO)</option>
            <option value="NVDA">NVIDIA (NVDA)</option>
            <option value="INTC">Intel (INTC)</option>
            <option value="AMD">AMD</option>
            <option value="QCOM">Qualcomm (QCOM)</option>
          </select>
        </div>
        <div className="field">
          <label className="field-label">Industry</label>
          <select className="input" value={cfg.industry} onChange={e => setCfg({...cfg, industry: e.target.value})}>
            <option>Analog Semiconductors</option>
            <option>Digital Semiconductors</option>
            <option>Industrial / Manufacturing</option>
            <option>Energy / Utilities</option>
          </select>
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
          <span className="mono" style={{fontSize:10, color: liveMode ? "var(--green-ink)" : "var(--ink-3)"}}>
            <span className={`live-dot ${liveMode ? "on" : ""}`} style={{display:"inline-block", marginRight: 4, verticalAlign: 1}}/>
            {liveMode ? "LIVE" : "MOCK"}
          </span>
        }>Data Mode</SectionLabel>
        <div style={{display:"flex", gap: 6}}>
          <button className={`btn btn-sm ${!liveMode ? "btn-primary" : ""}`} style={{flex:1}} onClick={() => setLiveMode(false)}>Mock</button>
          <button className={`btn btn-sm ${liveMode ? "btn-primary" : ""}`} style={{flex:1}} onClick={() => setLiveMode(true)}>
            <Icon name="wifi" size={11}/> Live
          </button>
        </div>
        {liveMode && (
          <div style={{marginTop: 8, fontSize: 10.5, color: "var(--ink-3)", lineHeight: 1.5}}>
            {liveStatus || "EDGAR via data.sec.gov · FRED snapshot bundled"}
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
            <span className="cfg-lab">HITL gates</span>
            <span className="cfg-val mono">
              <span className={hitl.risk ? "on" : "off"} title="Risk assessment">R</span>
              <span className={hitl.scope ? "on" : "off"} title="Audit scope">S</span>
              <span className={hitl.map ? "on" : "off"} title="MAP generation">M</span>
            </span>
          </div>
        </div>
        <div className="cfg-hint">
          Velocity threshold and HITL gates configure how the loop escalates and where it pauses. Edit in the Tweaks panel — opens with the gear above.
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

window.Sidebar = Sidebar;
