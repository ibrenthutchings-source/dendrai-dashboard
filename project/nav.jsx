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
      { id: "pipeline", icon: "flow",      l: "Risk Radar" },
      { id: "scenarios",  icon: "trend",   l: "Grey Swan Scenarios" },
      { id: "scenarioanalysis", icon: "compass", l: "Scenario Sandbox" },
      { id: "sox",        icon: "grid",    l: "SOX Control Pulse"}
     // { id: "coverage",   icon: "check",   l: "Coverage Gap Analysis" }
    ],
  },
  {
    label: "Audit & Compliance Tracking",
    items: [
      // Risk Register, Risk Flow, and Forecasts now live in the
      // right-hand Live Register rail on the Pipeline screen (post-run).
      { id: "continuousmonitoring", icon: "compass", l: "Continuous Watch" },
      { id: "aiinventory", icon: "list", l: "AI System Ledger" },
      { id: "scope",    icon: "grid",      l: "Scope Builder" },
      { id: "rrreview",   icon: "list",     l: "Risk & Control Ledger" },
      { id: "policycode", icon: "shield",  l: "Policy-as-Code Engine" },
      { id: "approvals", icon: "check",    l: "Approval Inbox", countKey: "approvals", pulseKey: "approvalsPulse" },
      //{ id: "controls", icon: "alert",     l: "Controls Monitor", countKey: "controls", pulseKey: "controlsPulse" },
      { id: "ubogov",   icon: "shield",    l: "Control Tower" }
      //{ id: "maps",     icon: "check",     l: "MAPs", countKey: "maps" },
      //{ id: "notifs",   icon: "bolt",      l: "Notifications", countKey: "notifs" }
    ],
  },
  {
    label: "Board Intelligence",
    items: [
      { id: "gov", govTab: "overview",  icon: "compass", l: "Boardroom Pulse" },
      { id: "gov", govTab: "board",     icon: "user",    l: "Board & Audit Committee" },
      { id: "gov", govTab: "comp",      icon: "doc",     l: "Pay & Performance" },
      { id: "gov", govTab: "proposals", icon: "list",    l: "Shareholder Proposals" },
      { id: "gov", govTab: "peers",     icon: "table",   l: "Peer Lens" },
    ],
  },
  {
    label: "Configuration",
    items: [
      { id: "config",     icon: "gear",   l: "Mission Control" },
      { id: "uboconfig",  icon: "shield", l: "Dendrai UBO™ Configuration" },
      { id: "tokenusage", icon: "table",  l: "Usage Meter" },
      { id: "modelhealth", icon: "trend", l: "Model Vitals" },
      { id: "userconfig", icon: "user",   l: "Team & Access", adminOnly: true },
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

function LeftNav({ activeScreen, activeGovTab, onNavigate, counts = {}, isAdmin = false, screenPerms = null }) {
  const [collapsed, setCollapsed] = React.useState({});
  const DendraiMark = window.DendraiMark;
  const DendraiWordmark = window.DendraiWordmark;

  function isActive(item) {
    if (item.id !== activeScreen) return false;
    if (item.govTab) return item.govTab === activeGovTab;
    return true;
  }

  // Admins always see everything; adminOnly items are hidden from everyone
  // else regardless of the screen-permission matrix. Otherwise a missing
  // entry in screenPerms means "allowed" — see auth.screen_permissions.
  function isVisible(item) {
    if (item.adminOnly) return isAdmin;
    if (isAdmin || !screenPerms) return true;
    const p = screenPerms[item.id];
    return !p || p.can_read !== false;
  }

  function toggleSection(label) {
    setCollapsed(prev => ({ ...prev, [label]: !prev[label] }));
  }

  return (
    <nav className="lnav" data-screen-label="Navigation">
      <div className="lnav-brand">
        <div className="lnav-logo"><DendraiMark size={17}/></div>
        <div className="lnav-brand-name"><DendraiWordmark size={13.5}/></div>
      </div>

      <div className="lnav-scroll">
        {NAV_SECTIONS.filter(section => section.items.some(isVisible)).map(section => {
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
              {!isCollapsed && section.items.filter(isVisible).map(item => {
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
