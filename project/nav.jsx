/* ============================================================
   Left navigation rail — menu only, routes the main canvas.
   Three sections: Configuration · Execution · Governance Intelligence
   ============================================================ */

const NAV_SECTIONS = [
  {
    label: "Configuration",
    items: [
      { id: "config", icon: "gear", l: "Setup" },
    ],
  },
  {
    label: "Execution",
    items: [
      // Risk Register, Risk Flow, and Forecasts now live in the
      // right-hand Live Register rail on the Pipeline screen (post-run).
      { id: "pipeline", icon: "flow",      l: "Pipeline" },
      { id: "controls", icon: "alert",     l: "Controls Monitor", countKey: "controls", pulseKey: "controlsPulse" },
      { id: "maps",     icon: "check",     l: "MAPs", countKey: "maps" },
      { id: "notifs",   icon: "bolt",      l: "Notifications", countKey: "notifs" },
      { id: "scope",    icon: "grid",      l: "Audit Scope" },
      { id: "riskcode",   icon: "spark",     l: "Risk-as-Code" },
      { id: "frameworks", icon: "code",     l: "Risks as Code (Frameworks)" },
      { id: "policycode", icon: "shield",  l: "Policy-as-Code" },
      { id: "scenarios",  icon: "trend",   l: "Grey Swan Scenarios" },
      { id: "sox",        icon: "grid",    l: "SOX Scope" },
    ],
  },
  {
    label: "Governance Intelligence",
    items: [
      { id: "gov", govTab: "overview",  icon: "compass", l: "Overview" },
      { id: "gov", govTab: "board",     icon: "user",    l: "Board & Audit Committee" },
      { id: "gov", govTab: "comp",      icon: "doc",     l: "Exec Compensation" },
      { id: "gov", govTab: "proposals", icon: "list",    l: "Shareholder Proposals" },
      { id: "gov", govTab: "peers",     icon: "table",   l: "Peer Benchmarking" },
    ],
  },
];

// Mini gear icon (not in the shared Icon set) — falls back gracefully.
function NavIcon({ name, size = 14 }) {
  if (name === "gear") {
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" fill="none"
        stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="8" cy="8" r="2.2"/>
        <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6 13 13M13 3l-1.4 1.4M4.4 11.6 3 13"/>
      </svg>
    );
  }
  return <Icon name={name} size={size}/>;
}

function LeftNav({ activeScreen, activeGovTab, onNavigate, counts = {} }) {
  function isActive(item) {
    if (item.id !== activeScreen) return false;
    if (item.govTab) return item.govTab === activeGovTab;
    return true;
  }

  return (
    <nav className="lnav" data-screen-label="Navigation">
      <div className="lnav-brand">
        <div className="lnav-logo">D</div>
        <div className="lnav-brand-name">Dendrai</div>
      </div>

      <div className="lnav-scroll">
        {NAV_SECTIONS.map(section => (
          <div className="lnav-section" key={section.label}>
            <div className="lnav-section-label">{section.label}</div>
            {section.items.map(item => {
              const active = isActive(item);
              const count = item.countKey ? counts[item.countKey] : 0;
              const pulse = item.pulseKey ? counts[item.pulseKey] : false;
              return (
                <button
                  key={item.l}
                  className={"lnav-item" + (active ? " active" : "")}
                  onClick={() => onNavigate(item.id, item.govTab)}>
                  <span className="lnav-item-icon"><NavIcon name={item.icon} size={14}/></span>
                  <span className="lnav-item-label">{item.l}</span>
                  {count > 0 && <span className="lnav-count">{count}</span>}
                  {pulse && <span className="lnav-pulse" />}
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </nav>
  );
}

Object.assign(window, { LeftNav, NAV_SECTIONS });
