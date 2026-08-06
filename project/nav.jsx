/* ============================================================
   Left navigation rail — menu only, routes the main canvas.
   Six sections, mirroring the actual workflow this platform
   automates: Risk Assessment -> Automation -> Tracking ->
   Infrastructure Monitoring -> Board -> Setup. See help.jsx for
   the same framing rendered as a clickable overview (HelpScreen).
   ============================================================ */

const NAV_SECTIONS = [
  {
    label: "Risk Intelligence",
    items: [
      // Risk Register, Risk Flow, and Forecasts now live in the
      // right-hand Live Register rail on the Pipeline screen (post-run).
      { id: "help",       icon: "compass", l: "Intelligenza Workflow" },
      { id: "pipeline",  icon: "flow",  l: "Assess Risk" },
      { id: "posturetrend", icon: "trend", l: "Risk Posture" },
      { id: "sox",        icon: "grid",    l: "SOX Scoping"},
    ],
  },
   {
    label: "Scenario Intelligence",
    items: [
      { id: "scenarios",  icon: "alert",  l: "Grey Swan Scenarios" },
      { id: "scenarioanalysis", icon: "compass", l: "Scenario Sandbox" },
      ],
  },
  {
    label: "Automation",
    items: [
      { id: "riskcode",   icon: "doc",     l: "Risk-as-Code Editor" },
      { id: "policycode", icon: "shield",  l: "Policy-as-Code Engine" },
      { id: "coverage",   icon: "check",   l: "Coverage Gap Analysis" },
      { id: "aiinventory", icon: "list", l: "AI System Ledger" },
    ],
  },
  {
    label: "Monitoring Intelligence",
    items: [
      { id: "approvals", icon: "check",    l: "Approval Inbox", countKey: "approvals", pulseKey: "approvalsPulse" },
      { id: "notifs",   icon: "bolt",      l: "Notifications", countKey: "notifs", pulseKey: "notifsPulse" },
      { id: "continuousmonitoring", icon: "compass", l: "Continuous Watch" },
      { id: "controls",  icon: "alert",    l: "Controls Monitor", countKey: "controls", pulseKey: "controlsPulse" },
      { id: "scope",    icon: "grid",      l: "Audit Plan" },
      { id: "rrreview",   icon: "list",     l: "Risk & Control Ledger" },
      //{ id: "maps",     icon: "check",     l: "MAPs", countKey: "maps" },
      { id: "ubogov",   icon: "shield",    l: "Telemetry Detail" },
     
    ],
  },
  //{
  //  label: "Infrastructure Monitoring",
  //  items: [
  //    { id: "infrastructuremonitoring", icon: "shield", l: "Infrastructure Monitoring" },
  //  ],
  //},
  {
    label: "Board",
    items: [
      { id: "gov", govTab: "overview",  icon: "compass", l: "Boardroom Pulse" },
      { id: "gov", govTab: "board",     icon: "user",    l: "Board & Audit Committee" },
      { id: "gov", govTab: "comp",      icon: "doc",     l: "Pay & Performance" },
      { id: "gov", govTab: "proposals", icon: "list",    l: "Shareholder Proposals" },
      { id: "gov", govTab: "peers",     icon: "table",   l: "Peer Lens" },
    ],
  },
  {
    label: "Setup",
    items: [
      { id: "config",     icon: "gear",   l: "Mission Control" },
      { id: "uboconfig",  icon: "shield", l: "Dendrai UBO™ Configuration" },
      { id: "tokenusage", icon: "table",  l: "Usage Meter" },
      { id: "modelhealth", icon: "trend", l: "Model Vitals" },
      { id: "userconfig", icon: "user",   l: "Team & Access", adminOnly: true },
    ],
  }
];

// Which nav section + item does the current screen map to? Board tabs all
// share id "gov", so activeGovTab disambiguates them. Returns nulls when a
// screen has no nav entry (shouldn't happen for reachable screens).
function findNavLocation(activeScreen, activeGovTab) {
  for (const section of NAV_SECTIONS) {
    for (const item of section.items) {
      if (item.id !== activeScreen) continue;
      if (item.govTab && item.govTab !== activeGovTab) continue;
      return { sectionLabel: section.label, itemLabel: item.l };
    }
  }
  return { sectionLabel: null, itemLabel: null };
}

// The five risk-to-audit workflow stages (Setup is configuration, not a
// workflow stage — see help.jsx). Kept as an explicit list rather than
// derived so a future Setup rename can't silently light up a stepper cell.
const WORKFLOW_STAGE_LABELS = ["Risk Assessment", "Automation", "Tracking", "Infrastructure Monitoring", "Board"];

// Compact "you are here" strip for the top of the main canvas: the four
// workflow stages as a clickable stepper, plus a Stage › Screen breadcrumb.
// Always visible regardless of nav expand/collapse state. Screens outside
// the four stages (Setup) resolve to a breadcrumb with no stage highlighted.
function WorkflowStrip({ activeScreen, activeGovTab, onNavigate }) {
  const { sectionLabel, itemLabel } = findNavLocation(activeScreen, activeGovTab);
  const activeStageIdx = WORKFLOW_STAGE_LABELS.indexOf(sectionLabel);

  function goToStage(label) {
    const section = NAV_SECTIONS.find(s => s.label === label);
    const first = section && section.items[0];
    if (first) onNavigate(first.id, first.govTab);
  }

  return (
    <div className="wf-strip" data-screen-label="Workflow position">
      <div className="wf-steps">
        {WORKFLOW_STAGE_LABELS.map((label, i) => {
          const cls = "wf-step"
            + (i === activeStageIdx ? " active" : "")
            + (activeStageIdx >= 0 && i < activeStageIdx ? " past" : "");
          return (
            <React.Fragment key={label}>
              <button type="button" className={cls} onClick={() => goToStage(label)} title={label}>
                <span className="wf-step-dot" />
                <span className="wf-step-label">{label}</span>
              </button>
              {i < WORKFLOW_STAGE_LABELS.length - 1 && (
                <span className="wf-step-sep"><Icon name="chev-r" size={10}/></span>
              )}
            </React.Fragment>
          );
        })}
      </div>
      <div className="wf-crumb">
        <button type="button" className="wf-crumb-stage"
          onClick={() => sectionLabel && goToStage(sectionLabel)}>
          {sectionLabel || "—"}
        </button>
        <Icon name="chev-r" size={9}/>
        <span className="wf-crumb-screen">{itemLabel || activeScreen}</span>
      </div>
    </div>
  );
}

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
  // Sections start collapsed except the one holding the active screen, so
  // the "you are here" highlight is always visible without hunting for it.
  const [collapsed, setCollapsed] = React.useState(() => {
    const active = findNavLocation(activeScreen, activeGovTab).sectionLabel;
    return Object.fromEntries(NAV_SECTIONS.map(s => [s.label, s.label !== active]));
  });
  // Reveal the active section on navigation; leave the user's manual
  // expand/collapse of every other section untouched.
  React.useEffect(() => {
    const active = findNavLocation(activeScreen, activeGovTab).sectionLabel;
    if (active) setCollapsed(prev => ({ ...prev, [active]: false }));
  }, [activeScreen, activeGovTab]);
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
      <button
        type="button"
        className="lnav-brand"
        onClick={() => onNavigate("help")}
        style={{ background: "none", border: "none", cursor: "pointer", width: "100%" }}
        title="Back to Intelligenza Workflow"
      >
        <div className="lnav-logo"><DendraiMark size={17}/></div>
        <div className="lnav-brand-name"><DendraiWordmark size={13.5}/></div>
      </button>

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

Object.assign(window, { LeftNav, NAV_SECTIONS, WorkflowStrip });
