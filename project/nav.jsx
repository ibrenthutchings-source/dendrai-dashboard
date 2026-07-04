/* ============================================================
   Left navigation rail — menu only, routes the main canvas.
   Three sections: Configuration · Execution · Governance Intelligence
   ============================================================ */

const NAV_SECTIONS = [
  {
    label: "Risk Intelligence",
    items: [
      // Risk Register, Risk Flow, and Forecasts now live in the
      // right-hand Live Register rail on the Pipeline screen (post-run).
      { id: "pipeline", icon: "flow",      l: "Assess Enterprise Risk" },
      { id: "scenarios",  icon: "trend",   l: "Grey Swan Scenarios" },
      { id: "sox",        icon: "grid",    l: "SOX Risk Assessment"},
      { id: "rrreview",   icon: "list",     l: "Risks & Controls Register" }
     // { id: "coverage",   icon: "check",   l: "Coverage Gap Analysis" }
    ],
  },
  {
    label: "Audit & Compliance Tracking",
    items: [
      // Risk Register, Risk Flow, and Forecasts now live in the
      // right-hand Live Register rail on the Pipeline screen (post-run).
      { id: "controls", icon: "alert",     l: "Controls Monitor", countKey: "controls", pulseKey: "controlsPulse" },
      { id: "ubogov",   icon: "shield",    l: "UBO Governance Brain" },
      { id: "maps",     icon: "check",     l: "MAPs", countKey: "maps" },
      { id: "notifs",   icon: "bolt",      l: "Notifications", countKey: "notifs" },
      { id: "scope",    icon: "grid",      l: "Audit Scope" },
      { id: "policycode", icon: "shield",  l: "Policy-as-Code" }
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
  {
    label: "Configuration",
    items: [
      { id: "config",    icon: "gear",   l: "Setup" },
      { id: "uboconfig", icon: "shield", l: "UBO Configuration" },
    ],
  }
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
  const [collapsed, setCollapsed] = React.useState({});

  function isActive(item) {
    if (item.id !== activeScreen) return false;
    if (item.govTab) return item.govTab === activeGovTab;
    return true;
  }

  function toggleSection(label) {
    setCollapsed(prev => ({ ...prev, [label]: !prev[label] }));
  }

  return (
    <nav className="lnav" data-screen-label="Navigation">
      <div className="lnav-brand">
        <div className="lnav-logo">D</div>
        <div className="lnav-brand-name">Dendrai</div>
      </div>

      <div className="lnav-scroll">
        {NAV_SECTIONS.map(section => {
          const isCollapsed = !!collapsed[section.label];
          return (
            <div className="lnav-section" key={section.label}>
              <button
                className={"lnav-section-label" + (isCollapsed ? " collapsed" : "")}
                onClick={() => toggleSection(section.label)}
                type="button"
                aria-expanded={!isCollapsed}>
                <span className="lnav-section-label-text">{section.label}</span>
                <svg className="lnav-chevron" width="10" height="10" viewBox="0 0 10 10"
                  fill="none" stroke="currentColor" strokeWidth="1.5"
                  strokeLinecap="round" strokeLinejoin="round">
                  <path d="M2 4l3 3 3-3"/>
                </svg>
              </button>
              {!isCollapsed && section.items.map(item => {
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
          );
        })}
      </div>
    </nav>
  );
}

Object.assign(window, { LeftNav, NAV_SECTIONS });
