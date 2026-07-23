/* ============================================================
   Intelligenza Workflow — a clickable overview of the platform's
   actual workflow: Risk Assessment -> Automation -> Tracking ->
   Board -> Setup. Mirrors nav.jsx's five sections exactly (same
   ids/labels) so this stays a map of the real nav, not a separate
   narrative that can drift out of sync with it. This is also the
   default landing screen (see app.jsx's activeScreen initial state).
   ============================================================ */

const WORKFLOW_STAGES = [
  {
    id: "assessment", label: "Risk Assessment", icon: "flow",
    desc: "Run the six-stage risk loop for a ticker, monitor grey-swan escalation scenarios, and track how posture has actually changed run over run.",
    items: [
      { screen: "pipeline", label: "Risk Radar", desc: "Run the six-stage AI risk loop for a ticker and monitor live results." },
      { screen: "posturetrend", label: "Posture Trend", desc: "See how overall risk posture has shifted run over run." },
      { screen: "scenarios", label: "Grey Swan Scenarios", desc: "Model Bear/Base/Bull/Grey Swan futures and escalation cascades." },
      { screen: "scenarioanalysis", label: "Scenario Sandbox", desc: "Quantitative what-if stress testing against custom macro shocks." },
      { screen: "sox", label: "SOX Control Pulse", desc: "Track ICFR control testing status and SOX scope." },
    ],
  },
  {
    id: "automation", label: "Automation", icon: "code",
    desc: "Turn risk and control language into machine-enforceable code — Risk-as-Code, an OPA-backed Policy-as-Code engine, and a coverage view of what's actually automated vs. manual.",
    items: [
      { screen: "riskcode", label: "Risk-as-Code Editor", desc: "Author and version risk logic as machine-readable YAML." },
      { screen: "frameworks", label: "Framework Sync", desc: "Map risks and controls onto compliance frameworks like COSO." },
      { screen: "policycode", label: "Policy-as-Code Engine", desc: "Write and evaluate OPA/Rego policies against live controls." },
      { screen: "coverage", label: "Coverage Gap Analysis", desc: "See what's automated vs. still manual across the program." },
    ],
  },
  {
    id: "tracking", label: "Tracking", icon: "list",
    desc: "The day-to-day audit execution layer — scope, the risk & control ledger, approval sign-offs, management action plans, and live control-event monitoring.",
    items: [
      { screen: "continuousmonitoring", label: "Continuous Watch", desc: "Live command-center feed of control events as they fire." },
      { screen: "controls", label: "Controls Monitor", desc: "Track control health, alerts, and status in real time." },
      { screen: "aiinventory", label: "AI System Ledger", desc: "Inventory AI systems in use and their risk classification." },
      { screen: "scope", label: "Scope Builder", desc: "Define risk-linked audit objectives by fiscal quarter." },
      { screen: "rrreview", label: "Risk & Control Ledger", desc: "Review risks alongside the controls mapped to address them." },
      { screen: "maps", label: "MAPs", desc: "Track management action plans, owners, and due dates." },
      { screen: "approvals", label: "Approval Inbox", desc: "Sign off on Gate 1/Gate 2 approval tasks in your queue." },
      { screen: "ubogov", label: "Control Tower", desc: "Oversight dashboard for UBO and governance controls." },
      { screen: "notifs", label: "Notifications", desc: "Posture digests plus the tiered stakeholder alert cascade." },
    ],
  },
  {
    id: "board", label: "Board", icon: "user",
    desc: "Roll everything up for the board and audit committee — governance posture, pay & performance, shareholder proposals, and peer comparison.",
    items: [
      { screen: "gov", govTab: "overview", label: "Boardroom Pulse", desc: "Executive summary of governance posture for the board." },
      { screen: "gov", govTab: "board", label: "Board & Audit Committee", desc: "Composition, independence, and activity of the board and committee." },
      { screen: "gov", govTab: "comp", label: "Pay & Performance", desc: "Executive compensation measured against company performance." },
      { screen: "gov", govTab: "proposals", label: "Shareholder Proposals", desc: "Track shareholder proposals and voting outcomes." },
      { screen: "gov", govTab: "peers", label: "Peer Lens", desc: "Compare governance posture against peer companies." },
    ],
  },
  {
    id: "setup", label: "Setup", icon: "gear",
    desc: "Configure the entity being assessed, manage team access, and watch the platform's own model health and usage.",
    items: [
      { screen: "config", label: "Mission Control", desc: "Configure the entity, signal sources, and run the pipeline." },
      { screen: "uboconfig", label: "Dendrai UBO™ Configuration", desc: "Configure ultimate beneficial ownership tracking settings." },
      { screen: "tokenusage", label: "Usage Meter", desc: "Track AI token usage and cost by feature." },
      { screen: "modelhealth", label: "Model Vitals", desc: "Monitor AI drift, acceptance rates, and model health." },
      { screen: "userconfig", label: "Team & Access", desc: "Manage users, roles, and per-screen permissions." },
    ],
  },
];

function HelpScreen({ onNavigate }) {
  const [activeStage, setActiveStage] = useState("assessment");
  const stage = WORKFLOW_STAGES.find(s => s.id === activeStage);

  return (
    <>
      <div className="panel-head">
        <div>
          <div className="kicker">Setup</div>
          <div className="panel-title mt-8">Intelligenza Workflow</div>
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
