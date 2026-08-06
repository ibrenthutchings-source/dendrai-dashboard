/* ============================================================
   Intelligenza Workflow — a clickable overview of the platform's
   actual workflow: Risk Intelligence -> Scenario Intelligence ->
   Automation Intelligence -> Monitoring Intelligence -> Board
   Intelligence -> Setup. Mirrors nav.jsx's NAV_SECTIONS exactly
   (same screen ids, same labels) so this stays a map of the real
   nav, not a separate narrative that can drift out of sync with
   it — Setup is excluded here the same way nav.jsx's WorkflowStrip
   excludes it (configuration, not a workflow stage), and
   Infrastructure Monitoring is dropped because NAV_SECTIONS
   currently has that section commented out. This is also the
   default landing screen (see app.jsx's activeScreen initial state).
   ============================================================ */

const WORKFLOW_STAGES = [
  {
    id: "risk", label: "Risk Intelligence", icon: "flow",
    desc: "Run the six-stage risk loop for a ticker, track posture run over run, scope SOX ICFR, and build the audit plan the risks justify.",
    items: [
      { screen: "pipeline", label: "Assess Risk", desc: "The predictive-risk engine — a six-stage AI loop turns live EDGAR filings into scored, forecasted risks with Beneish M-Score and Altman Z''-Score distress flags." },
      { screen: "aiinventory", label: "AI System Ledger", desc: "The EU AI Act-style register — every AI system in use, classified by risk tier and mapped to its governance obligations." },
      { screen: "posturetrend", label: "Risk Posture", desc: "Point-in-time risk snapshots charted run over run, so you can see whether the entity is genuinely improving or quietly deteriorating." },
      { screen: "sox", label: "SOX Scoping", desc: "AI-scoped ICFR — materiality and risk drive which accounts and systems fall in scope, not a static checklist." },
      { screen: "scope", label: "Audit Plan", desc: "Risk-linked audit objectives organized by fiscal quarter, so the plan is traceable back to the risks that justified it." },
    ],
  },
  {
    id: "scenario", label: "Scenario Intelligence", icon: "alert",
    desc: "Stress-test the risk picture — grey-swan escalation cascades and a quantitative what-if sandbox for custom macro shocks.",
    items: [
      { screen: "scenarios", label: "Grey Swan Scenarios", desc: "Beyond Bear/Base/Bull — models the foreseeable low-probability cascade where one contained risk escalates across a quarter, plus reverse-stress breakpoints and historical analogs." },
      { screen: "scenarioanalysis", label: "Scenario Sandbox", desc: "Quantitative what-if lab — apply custom macro shocks and watch risk scores and audit priorities re-solve in real time." },
    ],
  },
  {
    id: "automation", label: "Automation Intelligence", icon: "code",
    desc: "Turn risk and control language into machine-enforceable code — Risk-as-Code, an OPA-backed Policy-as-Code engine, and a coverage view of what's actually automated vs. manual.",
    items: [
      { screen: "riskcode", label: "Risk-as-Code Editor", desc: "Version-controlled risk definitions as OSCAL/COSO YAML — risk logic becomes a diffable, auditable artifact instead of a spreadsheet." },
      { screen: "policycode", label: "Policy-as-Code Engine", desc: "Author policies in OPA/Rego and evaluate them against live control data — enforceable, testable governance rather than PDF policy. Upload the plain-language policy you already wrote and the engine drafts the Rego for it, but a human must review the draft against the source text and approve it before it becomes a live module." },
      { screen: "coverage", label: "Coverage Gap Analysis", desc: "The automated-vs-manual heatmap — surfaces exactly which risks still lack a control, a test, or a quant model behind them." },
    ],
  },
  {
    id: "monitoring", label: "Monitoring Intelligence", icon: "list",
    desc: "The day-to-day audit execution layer — the risk & control ledger, approval sign-offs, and live control-event monitoring.",
    items: [
      { screen: "approvals", label: "Approval Inbox", desc: "The two-stage preparer→manager sign-off queue — human-in-the-loop gates every AI-suggested disposition before it lands." },
      { screen: "notifs", label: "Notifications", desc: "Scheduled posture digests plus the graduated stakeholder alert cascade driven by control-event severity." },
      { screen: "continuousmonitoring", label: "Continuous Watch", desc: "Always-on command center — 8-K and control events stream in and trigger the tiered stakeholder cascade the moment they fire." },
      { screen: "controls", label: "Controls Monitor", desc: "Live control-health board — flags failing and degrading controls in real time instead of at period-end testing." },
      { screen: "rrreview", label: "Risk & Control Ledger", desc: "The living risk register with its control mappings — Save All generates reviewed Risk-as-Code and Controls-as-Code together, with the relationship between them embedded in both artifacts." },
      { screen: "ubogov", label: "Telemetry Detail", desc: "Ultimate-beneficial-ownership oversight — surfaces hidden ownership and control chains behind the entity." },
    ],
  },
  {
    id: "board", label: "Board Intelligence", icon: "user",
    desc: "Roll everything up for the board and audit committee — governance posture, pay & performance, shareholder proposals, and peer comparison.",
    items: [
      { screen: "gov", govTab: "overview", label: "Boardroom Pulse", desc: "One-glance governance posture built from live SEC DEF 14A proxy data — board-ready, not hand-assembled." },
      { screen: "gov", govTab: "board", label: "Board & Audit Committee", desc: "Board composition, independence, and committee activity pulled straight from the proxy statement." },
      { screen: "gov", govTab: "comp", label: "Pay & Performance", desc: "Executive pay tested against actual company performance — flags misalignment between compensation and results." },
      { screen: "gov", govTab: "proposals", label: "Shareholder Proposals", desc: "Shareholder proposals and voting outcomes tracked as an early signal of investor pressure." },
      { screen: "gov", govTab: "peers", label: "Peer Lens", desc: "Benchmarks governance posture against SIC-matched peers — is this entity an outlier or the norm?" },
    ],
  },
];

function HelpScreen({ onNavigate }) {
  const [activeStage, setActiveStage] = useState("risk");
  const stage = WORKFLOW_STAGES.find(s => s.id === activeStage);

  return (
    <>
      <div className="panel-head">
        <div>
          <div className="panel-title">Intelligenza Workflow</div>
          <div className="panel-sub">How the platform's screens map onto the actual risk-to-audit workflow. Click a stage, then a screen, to jump straight there.</div>
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "stretch", gap: 4, marginBottom: 20, flexWrap: "wrap" }}>
        {WORKFLOW_STAGES.map((s, i) => (
          <React.Fragment key={s.id}>
            <button
              type="button"
              onClick={() => setActiveStage(s.id)}
              style={{
                flex: "1 1 140px", minWidth: 140, textAlign: "left", cursor: "pointer",
                padding: "12px 14px", borderRadius: 10,
                border: "1px solid " + (activeStage === s.id ? "var(--acc)" : "var(--line)"),
                background: activeStage === s.id ? "var(--acc-soft)" : "var(--surface)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                <Icon name={s.icon} size={13}/>
                <span style={{ fontSize: 12.5, fontWeight: 600, color: activeStage === s.id ? "var(--acc-ink)" : "var(--ink)" }}>{s.label}</span>
              </div>
              <div style={{ fontSize: 10.5, color: "var(--ink-3)" }}>{s.items.length} screen{s.items.length === 1 ? "" : "s"}</div>
            </button>
            {i < WORKFLOW_STAGES.length - 1 && (
              <div style={{ display: "flex", alignItems: "center", color: "var(--ink-4)", flex: "0 0 auto" }}>
                <Icon name="chev-r" size={14}/>
              </div>
            )}
          </React.Fragment>
        ))}
      </div>

      {stage && (
        <div className="rep-section">
          <h3>{stage.label}</h3>
          <div style={{ fontSize: 12, color: "var(--ink-2)", lineHeight: 1.6, marginBottom: 12 }}>{stage.desc}</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 8 }}>
            {stage.items.map(it => (
              <button
                key={it.screen + (it.govTab || "")}
                type="button"
                className="btn btn-sm"
                style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 3, textAlign: "left", padding: "10px 12px", height: "auto" }}
                onClick={() => onNavigate?.(it.screen, it.govTab)}
              >
                <span style={{ display: "flex", width: "100%", justifyContent: "space-between", alignItems: "center", fontWeight: 600 }}>
                  {it.label} <Icon name="chev-r" size={11}/>
                </span>
                {it.desc && <span style={{ fontSize: 10.5, fontWeight: 400, color: "var(--ink-3)", lineHeight: 1.4 }}>{it.desc}</span>}
              </button>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

Object.assign(window, { HelpScreen });
