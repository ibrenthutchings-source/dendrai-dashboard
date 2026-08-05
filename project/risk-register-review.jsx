/* ============================================================
   Risk Register Review — Phases 2, 3, 4
   Internal register management + external framework ingestion +
   unified risk-to-control mapping, all in one screen.
   ============================================================ */
import { RiskGraphViz } from "./risk-graph-viz.jsx";
import { RiskSankey }   from "./risk-sankey.jsx";

// ─────────────────────────────────────────────────────────────────────────────
// Reference data — seeded from hardcoded defaults, overwritten from DB on mount
// ─────────────────────────────────────────────────────────────────────────────

// Safety valve for the "pick an existing control" pickers (ControlsPanel and
// RiskFrameworkMatrix both use it) — NOT a UX limit. Both containers already
// scroll, so this only exists to cap DOM node count if the library ever
// grows pathologically large; it must stay far above any realistic control
// count. A too-small cap here silently hides whole frameworks: with the
// library sorted alphabetically by ref, every control whose ref sorts past
// the cap (previously 12-20) never rendered in the un-searched picker at
// all — which is exactly what happened to a later-imported framework like
// "SOX 404" once the library grew past ~35 entries.
const _CONTROL_PICKER_CAP = 250;

let MASTER_CONTROLS = [
  { ref:"FC-01", framework:"Internal",       name:"Revenue Recognition Controls",     category:"Financial",      domain:"Finance",    desc:"Controls over revenue recognition timing to prevent misstatement" },
  { ref:"FC-02", framework:"Internal",       name:"Financial Close Reconciliation",    category:"Financial",      domain:"Finance",    desc:"Period-end reconciliation procedures for material accounts" },
  { ref:"FC-03", framework:"SOC 2",          name:"Segregation of Financial Duties",   category:"Financial",      domain:"Finance",    desc:"Segregation of duties for payment and approval workflows" },
  { ref:"FC-04", framework:"Internal",       name:"Fraud Risk Assessment",             category:"Financial",      domain:"Finance",    desc:"Annual fraud risk assessment aligned to Beneish M-Score indicators" },
  { ref:"AC-01", framework:"Internal",       name:"Access Control Policy",             category:"Access Control", domain:"IT",         desc:"Documented access control policy reviewed annually" },
  { ref:"AC-02", framework:"NIST SP 800-53", name:"Account Management",                category:"Access Control", domain:"IT",         desc:"Lifecycle management of user accounts including provisioning" },
  { ref:"AC-03", framework:"NIST SP 800-53", name:"Access Enforcement",                category:"Access Control", domain:"IT",         desc:"Enforce approved authorisations for logical access" },
  { ref:"AC-04", framework:"CIS Controls",   name:"Privileged Access Management",      category:"Access Control", domain:"IT",         desc:"Inventory and control of privileged accounts with MFA enforcement" },
  { ref:"AC-05", framework:"SOC 2",          name:"Logical Access Review",             category:"Access Control", domain:"IT",         desc:"Quarterly review of logical access rights for in-scope systems" },
  { ref:"SC-01", framework:"ISO/IEC 27001",  name:"Information Security Policy",       category:"Security",       domain:"IT",         desc:"Board-approved information security policy with annual review cycle" },
  { ref:"SC-02", framework:"CIS Controls",   name:"Data Protection & Encryption",      category:"Security",       domain:"IT",         desc:"Encryption of data at rest and in transit for sensitive information" },
  { ref:"SC-03", framework:"NIST SP 800-53", name:"Incident Response Plan",            category:"Security",       domain:"IT",         desc:"Documented and tested incident response procedures" },
  { ref:"SC-04", framework:"ISO/IEC 27001",  name:"Vulnerability Management",          category:"Security",       domain:"IT",         desc:"Regular vulnerability scanning and patch management program" },
  { ref:"SC-05", framework:"SOC 2",          name:"Change Management Controls",        category:"Security",       domain:"IT",         desc:"Formal change management process for production systems" },
  { ref:"RM-01", framework:"Internal",       name:"Risk Assessment Process",           category:"Risk Mgmt",      domain:"Operational",desc:"Documented enterprise risk identification and assessment process" },
  { ref:"RM-02", framework:"ISO/IEC 27001",  name:"Risk Treatment Plan",               category:"Risk Mgmt",      domain:"Operational",desc:"Documented risk treatment decisions with assigned owners and deadlines" },
  { ref:"RM-03", framework:"Internal",       name:"Risk Appetite Framework",           category:"Risk Mgmt",      domain:"Operational",desc:"Board-approved risk appetite statement with quantitative thresholds" },
  { ref:"RM-04", framework:"COSO ERM",       name:"Emerging Risk Monitoring",          category:"Risk Mgmt",      domain:"Operational",desc:"Quarterly horizon-scanning process for emerging and macro risks" },
  { ref:"OP-01", framework:"Internal",       name:"Business Continuity Plan",          category:"Operational",    domain:"Operational",desc:"Tested business continuity and disaster recovery procedures" },
  { ref:"OP-02", framework:"ISO/IEC 27001",  name:"Supplier Risk Management",          category:"Operational",    domain:"Operational",desc:"Third-party risk assessment and ongoing monitoring program" },
  { ref:"OP-03", framework:"Internal",       name:"Key Person Dependencies",           category:"Operational",    domain:"HR",         desc:"Identification and mitigation of key person dependency risks" },
  { ref:"CM-01", framework:"SOC 2",          name:"Compliance Monitoring Program",     category:"Compliance",     domain:"Legal",      desc:"Ongoing monitoring of regulatory requirements and compliance status" },
  { ref:"CM-02", framework:"Internal",       name:"Regulatory Change Management",      category:"Compliance",     domain:"Legal",      desc:"Process for tracking and responding to regulatory changes" },
  { ref:"CM-03", framework:"SOC 2",          name:"Privacy Controls",                  category:"Compliance",     domain:"Legal",      desc:"Data privacy controls aligned to applicable regulations (GDPR, CCPA)" },
  { ref:"VM-01", framework:"CIS Controls",   name:"Vendor Security Assessment",        category:"Vendor",         domain:"Operational",desc:"Security assessments for critical and high-risk vendors" },
  { ref:"VM-02", framework:"Internal",       name:"Supply Chain Resilience",           category:"Vendor",         domain:"Operational",desc:"Supplier diversification and concentration risk monitoring" },
  { ref:"HR-01", framework:"Internal",       name:"Security Awareness Training",       category:"HR",             domain:"HR",         desc:"Annual mandatory security awareness training for all employees" },
  { ref:"HR-02", framework:"Internal",       name:"Background Screening",              category:"HR",             domain:"HR",         desc:"Pre-employment background screening for sensitive roles" },
  // ISO/IEC 42001 AI management system controls
  { ref:"AI-01", framework:"ISO/IEC 42001", name:"AI System Impact Assessment",        category:"AI Governance",  domain:"Technology", desc:"Structured assessment of AI system impacts on people, processes, and society" },
  { ref:"AI-02", framework:"ISO/IEC 42001", name:"AI Lifecycle Management",            category:"AI Governance",  domain:"Technology", desc:"Governance controls across the full AI system development and deployment lifecycle" },
  { ref:"AI-03", framework:"ISO/IEC 42001", name:"AI Training Data Governance",        category:"AI Governance",  domain:"Technology", desc:"Controls ensuring training data quality, provenance, and bias mitigation" },
  { ref:"AI-04", framework:"ISO/IEC 42001", name:"AI Transparency & Explainability",   category:"AI Governance",  domain:"Technology", desc:"Mechanisms to explain AI outputs and decisions to relevant stakeholders" },
  { ref:"AI-05", framework:"ISO/IEC 42001", name:"Third-Party AI Tool Assessment",     category:"AI Governance",  domain:"Technology", desc:"Due diligence and ongoing monitoring for externally-sourced AI services" },
  { ref:"AI-06", framework:"ISO/IEC 42001", name:"Human Oversight of AI Systems",      category:"AI Governance",  domain:"Technology", desc:"Defined human review points and override mechanisms for AI-assisted decisions" },
];
let CTRL_BY_REF = Object.fromEntries(MASTER_CONTROLS.map(c => [c.ref, c]));

// Defaults used while the DB load is in flight (and as fallback when DB is unavailable)
const _DEFAULT_PRESET_FRAMEWORKS = ["NIST SP 800-53", "ISO/IEC 27001", "ISO/IEC 42001", "CIS Controls", "SOC 2"];
const _DEFAULT_MATRIX_FRAMEWORKS = ["ISO/IEC 27001", "ISO/IEC 42001", "SOC 2", "NIST SP 800-53", "CIS Controls", "COSO ERM"];

// Kept as aliases so module-level utility functions that reference them still work
// before the async load completes (they read through the let binding).
let PRESET_FRAMEWORKS  = _DEFAULT_PRESET_FRAMEWORKS;
let MATRIX_FRAMEWORKS  = _DEFAULT_MATRIX_FRAMEWORKS;

async function _loadControlsFromApi() {
  // Every failure branch here used to be silent: a non-ok response or a
  // thrown fetch left MASTER_CONTROLS on its ~30-entry hardcoded fallback
  // with no visible sign anything was wrong — every control from every
  // imported register (the whole point of this fetch) would simply be
  // missing from every picker in the app, and there would be nothing to look
  // at to find out why. Logging each branch turns "the dropdown is
  // inexplicably missing controls" into something the browser console
  // answers directly.
  try {
    const res = await fetch("/api/risk-register/controls");
    if (!res.ok) {
      console.warn(`[risk-register] GET /controls failed (HTTP ${res.status}) — ` +
        `showing the built-in ~30-control fallback only; imported registers' controls will not appear.`);
      return;
    }
    const data = await res.json();
    const controls = data.controls || [];
    if (!controls.length) {
      console.warn("[risk-register] GET /controls returned zero controls — showing the built-in fallback.");
      return;
    }
    MASTER_CONTROLS.length = 0;
    for (const c of controls) MASTER_CONTROLS.push(c);
    CTRL_BY_REF = Object.fromEntries(MASTER_CONTROLS.map(c => [c.ref, c]));
  } catch (e) {
    console.warn("[risk-register] Could not load the control library from the server — " +
      "showing the built-in fallback only:", e);
  }
}

// MASTER_CONTROLS is mutated in place (length=0 + push above), so this window
// reference stays live/in-sync for other screens (e.g. HITL Gate 2) that need
// the shared control library without importing this module directly.
Object.assign(window, { MASTER_CONTROLS, loadControlsFromApi: _loadControlsFromApi });

// PaC controls_catalog — the shared control_id vocabulary used to link a
// register control to the real (or manual) control it corresponds to.
let PAC_CATALOG_CONTROLS = [];

async function _loadPacCatalogFromApi() {
  try {
    const res = await fetch("/api/pac/controls/coverage");
    if (!res.ok) return;
    const data = await res.json();
    PAC_CATALOG_CONTROLS = data.controls || [];
  } catch (_) {}
}

async function _loadMatrixConfigFromApi() {
  try {
    const res = await fetch("/api/risk-register/matrix-config");
    if (!res.ok) return null;
    return await res.json();
  } catch (_) { return null; }
}

const FW_MOCK_RISKS = {
  "NIST SP 800-53": [
    { id:"NIST-AC-2",  name:"Inadequate account lifecycle management exposes systems to unauthorised access",   category:"Access Control",   source_framework:"NIST SP 800-53", control_family:"AC" },
    { id:"NIST-CM-6",  name:"Misconfigured system settings create exploitable security gaps",                   category:"Configuration",    source_framework:"NIST SP 800-53", control_family:"CM" },
    { id:"NIST-IR-4",  name:"Insufficient incident handling capability delays breach containment",              category:"Incident Response",source_framework:"NIST SP 800-53", control_family:"IR" },
    { id:"NIST-RA-3",  name:"Ad-hoc risk assessments miss systemic vulnerabilities in critical systems",        category:"Risk Assessment",  source_framework:"NIST SP 800-53", control_family:"RA" },
    { id:"NIST-SI-7",  name:"Lack of software integrity verification enables supply-chain compromise",          category:"System Integrity", source_framework:"NIST SP 800-53", control_family:"SI" },
    { id:"NIST-AU-9",  name:"Audit log tampering risk undermines forensic investigation capability",            category:"Audit",            source_framework:"NIST SP 800-53", control_family:"AU" },
  ],
  "ISO/IEC 27001": [
    { id:"ISO-A.9.1",  name:"Poorly defined access control policies allow privilege escalation",                category:"Access Control",   source_framework:"ISO/IEC 27001", control_family:"A.9" },
    { id:"ISO-A.12.1", name:"Unmanaged operational change increases risk of service disruption",                category:"Operations",       source_framework:"ISO/IEC 27001", control_family:"A.12" },
    { id:"ISO-A.15.1", name:"Unvetted supplier relationships introduce unmanaged third-party risk",            category:"Supplier",         source_framework:"ISO/IEC 27001", control_family:"A.15" },
    { id:"ISO-A.16.1", name:"Slow information security incident response amplifies regulatory exposure",        category:"Incident",         source_framework:"ISO/IEC 27001", control_family:"A.16" },
    { id:"ISO-A.17.1", name:"Untested continuity plans fail during actual disruption events",                  category:"Continuity",       source_framework:"ISO/IEC 27001", control_family:"A.17" },
    { id:"ISO-A.18.1", name:"Regulatory compliance gaps create penalty and reputational risk",                 category:"Compliance",       source_framework:"ISO/IEC 27001", control_family:"A.18" },
  ],
  "CIS Controls": [
    { id:"CIS-1.1",    name:"Unmanaged hardware assets create invisible attack surface",                        category:"Asset Management", source_framework:"CIS Controls", control_family:"CIS 1" },
    { id:"CIS-5.1",    name:"Uncontrolled administrative accounts expose critical infrastructure",              category:"Access Control",   source_framework:"CIS Controls", control_family:"CIS 5" },
    { id:"CIS-6.1",    name:"Insufficient access control management enables lateral movement post-breach",      category:"Access Control",   source_framework:"CIS Controls", control_family:"CIS 6" },
    { id:"CIS-13.1",   name:"Inadequate network monitoring delays detection of anomalous activity",             category:"Monitoring",       source_framework:"CIS Controls", control_family:"CIS 13" },
    { id:"CIS-16.1",   name:"Insecure application development practices introduce exploitable vulnerabilities", category:"AppSec",           source_framework:"CIS Controls", control_family:"CIS 16" },
  ],
  "SOC 2": [
    { id:"SOC-CC1.1",  name:"Weak control environment culture enables management override",                     category:"Governance",       source_framework:"SOC 2", control_family:"CC1" },
    { id:"SOC-CC6.1",  name:"Insufficient logical access controls expose sensitive customer data",              category:"Access Control",   source_framework:"SOC 2", control_family:"CC6" },
    { id:"SOC-CC7.1",  name:"Undetected system operations anomalies lead to prolonged service failure",        category:"Operations",       source_framework:"SOC 2", control_family:"CC7" },
    { id:"SOC-CC8.1",  name:"Uncontrolled software changes introduce defects into production systems",         category:"Change Management",source_framework:"SOC 2", control_family:"CC8" },
    { id:"SOC-CC9.1",  name:"Unmitigated vendor concentration risk triggers availability commitments breach",   category:"Vendor",           source_framework:"SOC 2", control_family:"CC9" },
  ],
  "ISO/IEC 42001": [
    { id:"AI42-A.5.1", name:"Unassessed AI system impacts create unforeseen ethical and operational harms",    category:"AI Impact Assessment", source_framework:"ISO/IEC 42001", control_family:"A.5" },
    { id:"AI42-A.6.1", name:"Unmanaged AI lifecycle changes introduce regressions in model safety and performance", category:"AI Lifecycle",    source_framework:"ISO/IEC 42001", control_family:"A.6" },
    { id:"AI42-A.7.1", name:"Poor training data quality produces biased or inaccurate AI outputs at scale",   category:"AI Data Governance",   source_framework:"ISO/IEC 42001", control_family:"A.7" },
    { id:"AI42-A.8.1", name:"Inadequate AI transparency undermines stakeholder trust and regulatory acceptance",category:"AI Transparency",      source_framework:"ISO/IEC 42001", control_family:"A.8" },
    { id:"AI42-A.9.1", name:"Unvetted third-party AI tools introduce unmanaged model and data supply-chain risk", category:"Third-Party AI",   source_framework:"ISO/IEC 42001", control_family:"A.9" },
    { id:"AI42-A.10.1",name:"Insufficient human oversight enables unchecked AI decision-making in high-stakes contexts", category:"Human Oversight", source_framework:"ISO/IEC 42001", control_family:"A.10" },
  ],
};

const AUTO_MAP_RULES = [
  { kws:["revenue","recognition","accounting","financial","margin","fraud","restat"],           refs:["FC-01","FC-02","FC-03","FC-04"] },
  { kws:["cyber","security","breach","data","unauthori","hack","phishing"],                    refs:["SC-01","SC-02","SC-03","SC-04","AC-02","AC-05"] },
  { kws:["access","identity","privilege","authentication","authoris","logical"],               refs:["AC-01","AC-02","AC-03","AC-04","AC-05"] },
  { kws:["operational","process","continuity","disaster","recovery","bcp"],                    refs:["RM-01","OP-01"] },
  { kws:["compliance","regulatory","legal","penalty","gdpr","ccpa","sox"],                     refs:["CM-01","CM-02","CM-03"] },
  { kws:["vendor","supplier","third","supply","outsourc"],                                     refs:["VM-01","VM-02","OP-02"] },
  { kws:["talent","people","key","retention","staff","hiring"],                                refs:["HR-01","HR-02","OP-03"] },
  { kws:["macro","market","interest","credit","inflation","rate","currency"],                   refs:["RM-02","RM-03","RM-04"] },
  { kws:["change","configuration","deployment","release","patch"],                             refs:["SC-05","CM-02"] },
  { kws:["incident","response","detection","monitoring","log"],                                refs:["SC-03","SC-04"] },
  { kws:["ai ","artificial intelligence","machine learning","llm","generative","algorithm","model bias","explainab","oversight of ai","training data"], refs:["AI-01","AI-02","AI-03","AI-04","AI-06"] },
  { kws:["third.party ai","ai vendor","ai tool","ai service","ai supply"],                   refs:["AI-05","VM-01"] },
];

function autoMapControls(name, category) {
  const text = (name + " " + (category || "")).toLowerCase();
  const refs = [];
  for (const rule of AUTO_MAP_RULES) {
    if (rule.kws.some(kw => text.includes(kw))) {
      for (const r of rule.refs) { if (!refs.includes(r)) refs.push(r); }
    }
  }
  if (!refs.length) refs.push("RM-01");
  return refs.slice(0, 5);
}

// ─────────────────────────────────────────────────────────────────────────────
// State initialisation helpers
// ─────────────────────────────────────────────────────────────────────────────

function initRiskStates(risks) {
  const states = {};
  (risks || []).forEach((r, idx) => {
    // Fallback to a positional key when a risk lacks a stable id/risk_ref
    // (e.g. rows persisted before predictive_analytics_tool.py assigned one) —
    // otherwise every such risk collapses onto the same key and silently
    // shares state with every other id-less risk.
    const key = r.id || r.risk_ref || `idx-${idx}`;
    const wording = r.current_wording || r.name || "";
    states[key] = {
      included: true,
      wording,
      originalWording: wording,
      reason: "",
    };
  });
  return states;
}

function initControlStates(risks) {
  const states = {};
  (risks || []).forEach((r, idx) => {
    const key = r.id || r.risk_ref || `idx-${idx}`;
    // Use backend-provided auto_controls when present (framework-discovery risks carry these)
    const kwRefs = (r.auto_controls && r.auto_controls.length)
      ? r.auto_controls.filter(ref => CTRL_BY_REF[ref])
      : autoMapControls(r.name, r.category);
    // For framework-specific risks, also include every MASTER_CONTROLS entry for that framework
    const fw = r.source_framework;
    const fwRefs = (fw && fw !== "Internal Risk Register")
      ? MASTER_CONTROLS.filter(c => c.framework === fw).map(c => c.ref)
      : [];
    const autoRefs = [...new Set([...kwRefs, ...fwRefs])].slice(0, 6);
    states[key] = { autoMapped: autoRefs, manual: [], generateCode: new Set() };
  });
  return states;
}

// Group risks by source_framework, then by category for internal ones
function groupRisks(risks) {
  const groups = {};
  for (const r of (risks || [])) {
    const fw = r.source_framework || r.category || "Internal Risk Register";
    if (!groups[fw]) groups[fw] = [];
    groups[fw].push(r);
  }
  return groups;
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

function RagDot({ rag }) {
  if (!rag) return null;
  return <span className={"rag-dot " + rag} />;
}

function ScoreBadge({ score }) {
  if (score == null) return null;
  const val = Number(score);
  const color = val >= 15 ? "var(--red,#e53)" : val >= 9 ? "var(--amber,#f80)" : "var(--green,#2a7)";
  return (
    <span className="mono" style={{ fontSize: 10, fontWeight: 700, color, minWidth: 28, textAlign: "right" }}>
      {val.toFixed(1)}
    </span>
  );
}

function ControlPill({ ctrlRef, onRemove, generateCode, onToggleGenerate, isAuto }) {
  const ctrl = CTRL_BY_REF[ctrlRef];
  const [editing, setEditing]   = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftDesc, setDraftDesc] = useState("");
  const [saving, setSaving]     = useState(false);
  const [err, setErr]           = useState("");

  function startEdit() {
    setDraftName(ctrl?.name || "");
    setDraftDesc(ctrl?.description || ctrl?.desc || "");
    setErr("");
    setEditing(true);
  }

  async function saveEdit() {
    const name = draftName.trim();
    if (!name) { setErr("Name is required."); return; }
    setSaving(true);
    try {
      const res = await fetch(`/api/risk-register/controls/${encodeURIComponent(ctrlRef)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description: draftDesc.trim() }),
      });
      const data = await res.json().catch(() => null);
      if (data && data.saved === false) { setErr(data.detail || "Could not save control wording."); setSaving(false); return; }
      // CTRL_BY_REF/MASTER_CONTROLS entries are shared object references —
      // mutate in place so every other reader picks up the change on its
      // next render, same pattern handleCreateControl already relies on.
      if (ctrl) { ctrl.name = name; ctrl.description = draftDesc.trim(); ctrl.desc = draftDesc.trim(); }
      setEditing(false);
    } catch (_) {
      setErr("Could not save — check your connection.");
    }
    setSaving(false);
  }

  if (editing) {
    return (
      <div style={{ display:"flex", flexDirection:"column", gap:4, padding:"6px 8px", borderRadius:4, background:"var(--surface,#fff)", border:"1px solid var(--acc,#2563eb)", fontSize:10, minWidth:220 }}>
        <span className="mono" style={{ fontWeight:600, color:"var(--ink-2,#555)" }}>{ctrlRef}</span>
        <input
          className="dendrai-input"
          value={draftName}
          onChange={e => setDraftName(e.target.value)}
          placeholder="Control name…"
          style={{ fontSize:10, padding:"3px 6px" }}
          autoFocus
        />
        <textarea
          className="dendrai-input"
          value={draftDesc}
          onChange={e => setDraftDesc(e.target.value)}
          placeholder="Control description…"
          rows={2}
          style={{ fontSize:10, padding:"3px 6px", resize:"vertical", fontFamily:"inherit" }}
        />
        {err && <div style={{ fontSize:9, color:"var(--red,#e53)" }}>{err}</div>}
        <div style={{ display:"flex", gap:4 }}>
          <button className="btn btn-sm btn-acc" onClick={saveEdit} disabled={saving} style={{ fontSize:9, padding:"2px 9px" }}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button className="btn btn-sm" onClick={() => setEditing(false)} style={{ fontSize:9, padding:"2px 8px" }}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ display:"flex", alignItems:"center", gap:4, padding:"2px 6px", borderRadius:4, background:"var(--surface-2,#f5f5f5)", border:"1px solid var(--line,#e0e0e0)", fontSize:10 }}>
      <span className="mono" style={{ fontWeight:600, color:"var(--ink-2,#555)" }}>{ctrlRef}</span>
      {ctrl && <span style={{ color:"var(--ink-3,#888)" }}>{ctrl.name}</span>}
      {ctrl?.pac_control_id && (
        <span title={`Enforced by PaC control ${ctrl.pac_control_id}`} style={{ fontSize:9, color:"var(--acc,#2563eb)" }}>⚡{ctrl.pac_control_id}</span>
      )}
      {isAuto && <span style={{ fontSize:9, color:"var(--ink-3,#888)", fontStyle:"italic" }}>auto</span>}
      <button
        title="Edit control wording"
        onClick={startEdit}
        style={{ marginLeft:2, fontSize:9, padding:"0 3px", borderRadius:3, border:"1px solid var(--line,#e0e0e0)", background:"transparent", color:"var(--ink-3,#888)", cursor:"pointer", lineHeight:"14px" }}
      >✎</button>
      <button
        title={generateCode ? "Remove from code generation" : "Include in code generation"}
        onClick={() => onToggleGenerate(ctrlRef)}
        style={{ fontSize:9, padding:"0 3px", borderRadius:3, border:"1px solid var(--line,#e0e0e0)", background: generateCode ? "var(--acc,#2563eb)" : "transparent", color: generateCode ? "#fff" : "var(--ink-3,#888)", cursor:"pointer", lineHeight:"14px" }}
      >{generateCode ? "</>" : "</>"}
      </button>
      <button
        title="Remove control"
        onClick={() => onRemove(ctrlRef)}
        style={{ marginLeft:1, fontSize:10, padding:"0 3px", borderRadius:3, border:"none", background:"transparent", color:"var(--ink-3,#888)", cursor:"pointer", lineHeight:"14px" }}
      >×</button>
    </div>
  );
}

// Known framework -> ref-prefix abbreviations. Frameworks not listed here
// (custom/newly-discovered ones) fall back to a derived abbreviation.
const _FRAMEWORK_REF_PREFIXES = {
  "Internal":        "INT",
  "SOC 2":           "SOC2",
  "NIST SP 800-53":  "NIST",
  "CIS Controls":    "CIS",
  "ISO/IEC 27001":   "ISO27K",
  "ISO/IEC 42001":   "ISO42K",
  "COSO ERM":        "COSO",
  "NIST AI RMF":     "NISTAI",
};

function _frameworkRefPrefix(fw) {
  if (_FRAMEWORK_REF_PREFIXES[fw]) return _FRAMEWORK_REF_PREFIXES[fw];
  const cleaned = (fw || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  return cleaned.slice(0, 6) || "CTRL";
}

// Next available "<PREFIX>-NN" ref for a framework, based on the highest
// existing suffix number already used by that prefix in the live control library.
function _nextRefForFramework(fw) {
  const prefix = _frameworkRefPrefix(fw);
  const pattern = new RegExp(`^${prefix}-(\\d+)$`);
  let max = 0;
  for (const c of MASTER_CONTROLS) {
    const m = pattern.exec(c.ref || "");
    if (m) max = Math.max(max, parseInt(m[1], 10));
  }
  return `${prefix}-${String(max + 1).padStart(2, "0")}`;
}

function ControlsPanel({ riskKey, riskName, riskCategory, ctrlState, onAddManual, onRemove, onToggleGenerate, onGetAiRecs, aiRecsLoading }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerSearch, setPickerSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [newCtrl, setNewCtrl] = useState({ ref: "", name: "", framework: "", desc: "", pacControlId: "" });
  const [refIsAuto, setRefIsAuto] = useState(true);
  const [createErr, setCreateErr] = useState("");

  // Frameworks available for a new control — "Internal" first, then every
  // framework currently pinned to the Matrix, in the preset list, or already
  // used by a control in the library. Pulling from all three (not just
  // MATRIX_FRAMEWORKS) means this dropdown stays populated even when the
  // user has removed every pinned Matrix column — those two concepts
  // (which columns are pinned vs. which frameworks controls can target)
  // are related but shouldn't collapse into each other.
  const _knownFws = new Set(MASTER_CONTROLS.map(c => c.framework).filter(Boolean));
  const frameworkOptions = ["Internal", ...new Set(
    [...MATRIX_FRAMEWORKS, ...PRESET_FRAMEWORKS, ..._knownFws].filter(f => f && f !== "Internal")
  )];

  function openCreateForm() {
    const fw = newCtrl.framework || frameworkOptions[0];
    setNewCtrl({ ref: _nextRefForFramework(fw), name: "", framework: fw, desc: "", pacControlId: "" });
    setRefIsAuto(true);
    setCreateOpen(true);
    setPickerOpen(false);
    setCreateErr("");
  }

  function handleFrameworkChange(fw) {
    setNewCtrl(p => ({ ...p, framework: fw, ref: refIsAuto ? _nextRefForFramework(fw) : p.ref }));
  }

  async function handleCreateControl() {
    const ref = newCtrl.ref.trim().toUpperCase();
    if (!ref) { setCreateErr("Control reference is required."); return; }
    if (CTRL_BY_REF[ref]) { setCreateErr(`${ref} already exists in the control library.`); return; }
    if (!/^[A-Za-z]/.test(ref)) { setCreateErr("Reference must start with a letter."); return; }
    if (!newCtrl.name.trim()) { setCreateErr("Control name is required."); return; }
    const pacControlId = newCtrl.pacControlId.trim().toUpperCase() || null;
    const ctrl = {
      ref,
      framework: newCtrl.framework.trim() || "Custom",
      name: newCtrl.name.trim(),
      category: "Custom",
      domain: "Custom",
      description: newCtrl.desc.trim(),
      desc: newCtrl.desc.trim(),
      pac_control_id: pacControlId,
    };
    // Persist to DB
    try {
      const res = await fetch("/api/risk-register/controls", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(ctrl),
      });
      const data = await res.json().catch(() => null);
      if (data && data.saved === false) { setCreateErr(data.detail || "Could not create control."); return; }
    } catch (_) {}
    // Update module-level lookup so other components can use the new control immediately
    MASTER_CONTROLS.push(ctrl);
    CTRL_BY_REF[ref] = ctrl;
    onAddManual(riskKey, ref);
    setCreateOpen(false);
    setNewCtrl({ ref: "", name: "", framework: "", desc: "", pacControlId: "" });
    setRefIsAuto(true);
    setCreateErr("");
  }

  const allAssigned = [...(ctrlState.autoMapped || []), ...(ctrlState.manual || [])];
  const filteredLibrary = MASTER_CONTROLS.filter(c =>
    !allAssigned.includes(c.ref) &&
    (pickerSearch === "" || c.name.toLowerCase().includes(pickerSearch.toLowerCase()) || c.ref.toLowerCase().includes(pickerSearch.toLowerCase()) || c.category.toLowerCase().includes(pickerSearch.toLowerCase()))
  );

  return (
    <div style={{ marginTop:8, padding:"10px 12px", background:"var(--surface-2,#f8f9fa)", borderRadius:6, border:"1px solid var(--line,#eee)" }}>
      <div style={{ display:"flex", alignItems:"center", gap:6, marginBottom:8 }}>
        <span style={{ fontSize:10, fontWeight:600, color:"var(--ink-2,#555)", textTransform:"uppercase", letterSpacing:"0.06em" }}>Controls</span>
        <span style={{ fontSize:9, color:"var(--ink-3,#888)" }}>{allAssigned.length} assigned</span>
        <div style={{ marginLeft:"auto", display:"flex", gap:4 }}>
          <button
            className="btn btn-sm"
            onClick={() => onGetAiRecs(riskKey, riskName, riskCategory)}
            disabled={aiRecsLoading}
            style={{ fontSize:9, padding:"2px 7px" }}
          >
            <Icon name="spark" size={9}/> {aiRecsLoading ? "…" : "AI Recs"}
          </button>
          <button
            className="btn btn-sm"
            onClick={() => { setPickerOpen(p => !p); setCreateOpen(false); }}
            style={{ fontSize:9, padding:"2px 7px" }}
          >
            + Add
          </button>
          <button
            className="btn btn-sm"
            onClick={() => { if (createOpen) { setCreateOpen(false); } else { openCreateForm(); } }}
            style={{ fontSize:9, padding:"2px 7px" }}
            title="Create a brand-new control with a new reference number"
          >
            + New
          </button>
        </div>
      </div>

      {/* Assigned control pills */}
      {allAssigned.length > 0 ? (
        <div style={{ display:"flex", flexWrap:"wrap", gap:4 }}>
          {(ctrlState.autoMapped || []).map(ref => (
            <ControlPill
              key={ref} ctrlRef={ref} isAuto
              generateCode={ctrlState.generateCode.has(ref)}
              onToggleGenerate={r => onToggleGenerate(riskKey, r)}
              onRemove={r => onRemove(riskKey, r, true)}
            />
          ))}
          {(ctrlState.manual || []).map(ref => (
            <ControlPill
              key={ref} ctrlRef={ref}
              generateCode={ctrlState.generateCode.has(ref)}
              onToggleGenerate={r => onToggleGenerate(riskKey, r)}
              onRemove={r => onRemove(riskKey, r, false)}
            />
          ))}
        </div>
      ) : (
        <div style={{ fontSize:10, color:"var(--ink-3,#888)", fontStyle:"italic" }}>No controls assigned — click AI Recs or Add.</div>
      )}

      {/* Control library picker */}
      {pickerOpen && (
        <div style={{ marginTop:8, padding:8, background:"var(--surface,#fff)", border:"1px solid var(--line,#ddd)", borderRadius:6, maxHeight:180, overflow:"hidden", display:"flex", flexDirection:"column", gap:6 }}>
          <input
            className="dendrai-input"
            placeholder="Search controls…"
            value={pickerSearch}
            onChange={e => setPickerSearch(e.target.value)}
            style={{ fontSize:10, padding:"3px 7px" }}
            autoFocus
          />
          <div style={{ overflowY:"auto", display:"flex", flexDirection:"column", gap:2 }}>
            {/* The library is sorted alphabetically by ref, and this list was
                capped at the first 20 regardless of search — with 40+ default
                refs (AC-*, AI-*, CM-*, FC-*, HR-*, OP-*, RM-*, SC-*, VM-*) all
                sorting before anything starting with a later letter, EVERY
                control from a framework like "SOX 404" (or anything else
                imported later) fell past the cutoff and was invisible in the
                unsearched, just-opened picker — not filtered out, just never
                rendered. _CONTROL_PICKER_CAP is a safety valve against a
                pathological library size, not a UX limit; the container
                already scrolls, so raising it costs nothing.  */}
            {filteredLibrary.slice(0, _CONTROL_PICKER_CAP).map(c => (
              <button
                key={c.ref}
                style={{ display:"flex", alignItems:"center", gap:6, padding:"4px 6px", borderRadius:4, border:"none", background:"transparent", cursor:"pointer", textAlign:"left", fontSize:10 }}
                onClick={() => { onAddManual(riskKey, c.ref); setPickerOpen(false); setPickerSearch(""); }}
                onMouseEnter={e => e.currentTarget.style.background="var(--surface-2,#f5f5f5)"}
                onMouseLeave={e => e.currentTarget.style.background="transparent"}
              >
                <span className="mono" style={{ fontWeight:600, minWidth:46, color:"var(--acc,#2563eb)" }}>{c.ref}</span>
                <span style={{ color:"var(--ink,#111)" }}>{c.name}</span>
                <span style={{ marginLeft:"auto", fontSize:9, color:"var(--ink-3,#888)" }}>{c.category}</span>
              </button>
            ))}
            {filteredLibrary.length === 0 && <div style={{ fontSize:10, color:"var(--ink-3,#888)", padding:4 }}>No matches</div>}
            {filteredLibrary.length > _CONTROL_PICKER_CAP && (
              <div style={{ fontSize:9, color:"var(--ink-3,#888)", padding:"2px 4px", fontStyle:"italic" }}>
                Showing {_CONTROL_PICKER_CAP} of {filteredLibrary.length} — type to narrow the search
              </div>
            )}
          </div>
        </div>
      )}

      {/* New control creation form */}
      {createOpen && (
        <div style={{ marginTop:8, padding:10, background:"var(--surface,#fff)", border:"1px solid var(--acc,#2563eb)", borderRadius:6, display:"flex", flexDirection:"column", gap:7 }}>
          <div style={{ fontSize:10, fontWeight:700, color:"var(--ink,#111)" }}>Create new control</div>
          <div style={{ display:"flex", gap:6 }}>
            <div style={{ flex:"0 0 100px" }}>
              <label style={{ fontSize:9, fontWeight:600, color:"var(--ink-2,#555)", display:"block", marginBottom:2 }}>Ref *</label>
              <input
                className="dendrai-input"
                placeholder="e.g. AC-06"
                value={newCtrl.ref}
                onChange={e => { setNewCtrl(p => ({ ...p, ref: e.target.value })); setRefIsAuto(false); }}
                style={{ fontSize:10, padding:"3px 6px", width:"100%", boxSizing:"border-box" }}
                title="Auto-populated from the selected framework's next available number — edit to override"
                autoFocus
              />
            </div>
            <div style={{ flex:1 }}>
              <label style={{ fontSize:9, fontWeight:600, color:"var(--ink-2,#555)", display:"block", marginBottom:2 }}>Framework</label>
              <select
                className="dendrai-input"
                value={newCtrl.framework}
                onChange={e => handleFrameworkChange(e.target.value)}
                style={{ fontSize:10, padding:"3px 6px", width:"100%", boxSizing:"border-box", cursor:"pointer" }}
              >
                {frameworkOptions.map(fw => (
                  <option key={fw} value={fw}>{fw}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label style={{ fontSize:9, fontWeight:600, color:"var(--ink-2,#555)", display:"block", marginBottom:2 }}>Name *</label>
            <input
              className="dendrai-input"
              placeholder="Control name…"
              value={newCtrl.name}
              onChange={e => setNewCtrl(p => ({ ...p, name: e.target.value }))}
              style={{ fontSize:10, padding:"3px 6px", width:"100%", boxSizing:"border-box" }}
            />
          </div>
          <div>
            <label style={{ fontSize:9, fontWeight:600, color:"var(--ink-2,#555)", display:"block", marginBottom:2 }}>Description</label>
            <textarea
              className="dendrai-input"
              placeholder="Brief description of what this control does…"
              value={newCtrl.desc}
              onChange={e => setNewCtrl(p => ({ ...p, desc: e.target.value }))}
              rows={2}
              style={{ fontSize:10, padding:"3px 6px", width:"100%", boxSizing:"border-box", resize:"vertical", fontFamily:"inherit" }}
            />
          </div>
          <div>
            <label style={{ fontSize:9, fontWeight:600, color:"var(--ink-2,#555)", display:"block", marginBottom:2 }}>
              Link to PaC control <span style={{ fontWeight:400, color:"var(--ink-3,#888)" }}>(optional)</span>
            </label>
            <select
              className="dendrai-input"
              value={newCtrl.pacControlId}
              onChange={e => setNewCtrl(p => ({ ...p, pacControlId: e.target.value }))}
              style={{ fontSize:10, padding:"3px 6px", width:"100%", boxSizing:"border-box", cursor:"pointer" }}
              title="Marks this as the register's copy of a real, policy-enforced control"
            >
              <option value="">Not linked</option>
              {PAC_CATALOG_CONTROLS.map(c => (
                <option key={c.control_id} value={c.control_id}>
                  {c.control_id} — {c.name}{c.source === "pac_rego" ? " (enforced)" : ""}
                </option>
              ))}
            </select>
          </div>
          {createErr && (
            <div style={{ fontSize:9, color:"var(--red,#e53)" }}>{createErr}</div>
          )}
          <div style={{ display:"flex", gap:4 }}>
            <button className="btn btn-sm btn-acc" onClick={handleCreateControl} style={{ fontSize:9, padding:"2px 10px" }}>
              Create &amp; Assign
            </button>
            <button className="btn btn-sm" onClick={() => { setCreateOpen(false); setCreateErr(""); }} style={{ fontSize:9, padding:"2px 8px" }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Code generation legend */}
      {allAssigned.length > 0 && (
        <div style={{ marginTop:6, fontSize:9, color:"var(--ink-3,#888)" }}>
          Toggle <span className="mono" style={{ background:"var(--acc,#2563eb)", color:"#fff", borderRadius:2, padding:"0 3px" }}>&lt;/&gt;</span> to flag a control for Control-as-Code generation alongside the Risk-as-Code output.
        </div>
      )}
    </div>
  );
}

function RiskReviewRow({
  risk, riskState, ctrlState,
  onToggleInclude, onWordingChange, onReasonChange,
  onAddManualControl, onRemoveControl, onToggleGenerateControl,
  onGetAiRecs, aiRecsLoading,
  expanded, onToggleExpand,
}) {
  const key = risk.id || risk.risk_ref;
  const wordingChanged = riskState.wording !== riskState.originalWording;
  const needsReason = (!riskState.included || wordingChanged) && !(riskState.reason || "").trim();
  const showReasonField = !riskState.included || wordingChanged;

  return (
    <div
      style={{
        borderBottom:"1px solid var(--line,#eee)",
        padding:"10px 0",
        opacity: riskState.included ? 1 : 0.55,
      }}
    >
      <div style={{ display:"flex", alignItems:"flex-start", gap:8 }}>
        {/* Include toggle */}
        <input
          type="checkbox"
          checked={riskState.included}
          onChange={() => onToggleInclude(key)}
          style={{ marginTop:3, cursor:"pointer", accentColor:"var(--acc,#2563eb)", flexShrink:0 }}
          title={riskState.included ? "Exclude this risk" : "Include this risk"}
        />

        {/* Risk ID badge */}
        <span className="mono" style={{ fontSize:9, fontWeight:700, color:"var(--ink-3,#888)", minWidth:42, paddingTop:2, flexShrink:0 }}>
          {key}
        </span>

        {/* Editable wording */}
        <div style={{ flex:1, display:"flex", flexDirection:"column", gap:4, minWidth:0 }}>
          <div style={{ display:"flex", alignItems:"center", gap:6 }}>
            <textarea
              value={riskState.wording}
              onChange={e => onWordingChange(key, e.target.value)}
              rows={2}
              className="dendrai-input"
              style={{
                flex:1, fontSize:11, lineHeight:1.5, resize:"vertical", padding:"4px 7px",
                border: wordingChanged ? "1px solid var(--amber,#f80)" : "1px solid var(--line,#ddd)",
                borderRadius:4, background:"var(--surface,#fff)", fontFamily:"inherit",
              }}
              disabled={!riskState.included}
            />
            <div style={{ display:"flex", flexDirection:"column", alignItems:"flex-end", gap:3, flexShrink:0 }}>
              <RagDot rag={risk.rag} />
              <ScoreBadge score={risk.score} />
              {risk.category && (
                <span style={{ fontSize:9, color:"var(--ink-3,#888)", maxWidth:70, textAlign:"right", lineHeight:1.2 }}>
                  {risk.category}
                </span>
              )}
            </div>
          </div>

          {/* Wording changed indicator */}
          {wordingChanged && (
            <div style={{ fontSize:9, color:"var(--amber,#c65)", display:"flex", alignItems:"center", gap:3 }}>
              <span>&#9679;</span> Wording modified from original
            </div>
          )}

          {/* Conditional reason field */}
          {showReasonField && (
            <div style={{ display:"flex", flexDirection:"column", gap:3 }}>
              <label style={{ fontSize:9, fontWeight:600, color: needsReason ? "var(--red,#e53)" : "var(--ink-2,#555)", display:"flex", alignItems:"center", gap:4 }}>
                {needsReason && <span title="Required before converting to code">⚠</span>}
                {riskState.included ? "Reason for wording change" : "Reason for exclusion"}
                <span style={{ fontWeight:400, color:"var(--ink-3,#888)" }}>(required)</span>
              </label>
              <textarea
                value={riskState.reason}
                onChange={e => onReasonChange(key, e.target.value)}
                rows={2}
                placeholder={riskState.included ? "Describe why the wording was changed…" : "Describe why this risk is being excluded…"}
                className="dendrai-input"
                style={{
                  fontSize:10, resize:"vertical", padding:"4px 7px",
                  border: needsReason ? "1px solid var(--red,#e53)" : "1px solid var(--line,#ddd)",
                  borderRadius:4, background:"var(--surface,#fff)", fontFamily:"inherit",
                  boxShadow: needsReason ? "0 0 0 2px rgba(229,85,51,0.12)" : "none",
                }}
              />
            </div>
          )}

          {/* Controls toggle */}
          <button
            onClick={() => onToggleExpand(key)}
            style={{
              alignSelf:"flex-start", fontSize:9, padding:"2px 7px", borderRadius:4,
              border:"1px solid var(--line,#ddd)", background:"transparent",
              color:"var(--ink-2,#555)", cursor:"pointer", display:"flex", alignItems:"center", gap:3,
            }}
          >
            <Icon name="check" size={9}/> Controls ({[...(ctrlState.autoMapped||[]),...(ctrlState.manual||[])].length})
            <span style={{ fontSize:8 }}>{expanded ? "▲" : "▼"}</span>
          </button>

          {expanded && (
            <ControlsPanel
              riskKey={key}
              riskName={riskState.wording}
              riskCategory={risk.category}
              ctrlState={ctrlState}
              onAddManual={onAddManualControl}
              onRemove={onRemoveControl}
              onToggleGenerate={onToggleGenerateControl}
              onGetAiRecs={onGetAiRecs}
              aiRecsLoading={aiRecsLoading}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function FrameworkGroupHeader({ framework, risks, riskStates, collapsed, onToggle }) {
  const total = risks.length;
  const excluded = risks.filter(r => riskStates[(r.id||r.risk_ref)]?.included === false).length;
  const changed  = risks.filter(r => {
    const s = riskStates[r.id||r.risk_ref];
    return s && s.wording !== s.originalWording;
  }).length;
  return (
    <div
      onClick={onToggle}
      style={{
        display:"flex", alignItems:"center", gap:8, padding:"8px 0",
        cursor:"pointer", userSelect:"none", borderBottom: collapsed ? "1px solid var(--line,#eee)" : "none",
      }}
    >
      <span style={{ fontSize:10, color:"var(--ink-3,#888)" }}>{collapsed ? "▶" : "▼"}</span>
      <span style={{ fontWeight:600, fontSize:12, color:"var(--ink,#111)" }}>{framework}</span>
      <span style={{ fontSize:10, color:"var(--ink-3,#888)", fontWeight:400 }}>{total} risk{total !== 1 ? "s" : ""}</span>
      {excluded > 0 && <span style={{ fontSize:9, padding:"1px 5px", borderRadius:3, background:"rgba(229,85,51,0.1)", color:"var(--red,#e53)", fontWeight:600 }}>{excluded} excluded</span>}
      {changed  > 0 && <span style={{ fontSize:9, padding:"1px 5px", borderRadius:3, background:"rgba(248,128,0,0.1)", color:"var(--amber,#c65)", fontWeight:600 }}>{changed} modified</span>}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Output panel (Convert to Code result)
// ─────────────────────────────────────────────────────────────────────────────

function OutputPanel({ yaml, onClose, onDownload }) {
  return (
    <div style={{
      position:"fixed", top:0, right:0, bottom:0, width:"min(600px,50vw)",
      background:"var(--surface,#fff)", borderLeft:"1px solid var(--line,#e0e0e0)",
      display:"flex", flexDirection:"column", zIndex:200, boxShadow:"-4px 0 24px rgba(0,0,0,0.08)",
    }}>
      <div style={{ display:"flex", alignItems:"center", gap:8, padding:"12px 16px", borderBottom:"1px solid var(--line,#eee)", flexShrink:0 }}>
        <span style={{ fontWeight:600, fontSize:12 }}>Risk and Controls Register — Code Output</span>
        <div style={{ marginLeft:"auto", display:"flex", gap:6 }}>
          <button className="btn btn-sm" onClick={onDownload}><Icon name="download" size={11}/> Download</button>
          <button className="btn btn-sm" onClick={onClose}>✕ Close</button>
        </div>
      </div>
      <div style={{ padding:"8px 12px", borderBottom:"1px solid var(--line,#eee)", flexShrink:0 }}>
        <span className="mono" style={{ fontSize:9, color:"var(--ink-3,#888)" }}>
          risk-register-review.yaml · {yaml.split("\n").length} lines · {(new Blob([yaml]).size/1024).toFixed(1)} KB
        </span>
      </div>
      <textarea
        className="code-editor mono"
        readOnly
        value={yaml}
        spellCheck={false}
        style={{ flex:1, resize:"none", padding:12, fontSize:10.5, lineHeight:1.6 }}
      />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Framework Matrix view
// ─────────────────────────────────────────────────────────────────────────────

// Sticky, viewport-bottom-pinned horizontal scrollbar for a wide table,
// scroll-synced with the table's own overflow-x region via `targetRef`.
//
// Why this exists instead of just `overflow-x:auto` on the table wrapper:
// a wrapper tall enough to hold every row puts its native horizontal
// scrollbar at the very bottom of the (possibly very long) table — below
// the fold until the page is scrolled all the way down. Bounding the
// wrapper's own height with overflow-y:auto "fixes" that but creates a
// second, nested vertical scrollbar next to the page's real one, which is
// confusing (two scrollbars on the right). This bar instead stays
// perfectly still at the bottom of the actual page scroll region — one
// real vertical scrollbar, one always-reachable horizontal control.
function StickyHScrollBar({ targetRef, contentWidth }) {
  const barRef = useRef(null);
  const syncing = useRef(false);

  useEffect(() => {
    const body = targetRef.current;
    const bar = barRef.current;
    if (!body || !bar) return;
    const onBodyScroll = () => {
      if (syncing.current) { syncing.current = false; return; }
      syncing.current = true;
      bar.scrollLeft = body.scrollLeft;
    };
    body.addEventListener("scroll", onBodyScroll);
    return () => body.removeEventListener("scroll", onBodyScroll);
  }, []);

  function onBarScroll() {
    if (syncing.current) { syncing.current = false; return; }
    if (barRef.current && targetRef.current) {
      syncing.current = true;
      targetRef.current.scrollLeft = barRef.current.scrollLeft;
    }
  }

  return (
    <div
      ref={barRef}
      onScroll={onBarScroll}
      style={{
        overflowX: "auto", overflowY: "hidden", maxWidth: "100%",
        position: "sticky", bottom: 0, height: 14,
        background: "var(--surface,#fff)", borderTop: "1px solid var(--line,#eee)",
      }}
    >
      <div style={{ minWidth: contentWidth, height: 1 }} />
    </div>
  );
}

function RiskFrameworkMatrix({ risks, riskStates, ctrlStates, matrixFrameworks, hiddenFrameworks, onWordingChange, onAddManualControl, onRemoveControl, onResetCtrl, onSaveRow, onRemoveFramework, onPurgeExtraFramework, savingRows, savedAt, runId }) {
  const scrollWrapRef = useRef(null);
  // Per-user, cross-session column order — persisted in the same
  // auth.users.preferences JSONB blob the appearance settings (accent/
  // density/colorScheme) already use, via the existing self-service
  // PUT /auth/users/me/preferences endpoint. No new backend/table needed.
  const auth = window.useAuth ? window.useAuth() : null;
  const [localColOrder, setLocalColOrder] = useState(null); // optimistic override for this render, avoids waiting on auth context refresh
  const [dragFw, setDragFw]         = useState(null);
  const [dragOverFw, setDragOverFw] = useState(null);
  // Row-level wording edit state
  const [editingRows, setEditingRows] = useState(new Set());
  const [rowDrafts, setRowDrafts]     = useState({});
  // Per-cell expand state: "riskKey:fw"
  const [savingCells, setSavingCells]     = useState(new Set());
  const [fwPicker, setFwPicker]       = useState(null); // { key, fw }
  const [ctrlSearch, setCtrlSearch]   = useState("");
  const [domainNames, setDomainNames] = useState({});
  const [domainsLoading, setDomainsLoading] = useState(false);
  // Per-cell "+ New" (create a brand-new control, not pick from the library) —
  // the Detail view's ControlsPanel has this; the matrix cells previously didn't.
  const [fwCreate, setFwCreate]       = useState(null); // { key, fw }
  const [newCtrlDraft, setNewCtrlDraft] = useState({ ref: "", name: "", framework: "", desc: "", pacControlId: "" });
  const [createErr, setCreateErr]     = useState("");

  // ── Spreadsheet-style sort / collapse / filter ─────────────────────────────
  const [sortCol, setSortCol]         = useState(null);   // null (default domain grouping) | "domain" | "risk" | a framework name
  const [sortDir, setSortDir]         = useState("asc");
  const [collapsedDomains, setCollapsedDomains] = useState(new Set());
  const [colFilters, setColFilters]   = useState({});      // { domain, risk, [fw]: text }

  function toggleSort(col) {
    if (sortCol === col) { setSortDir(d => (d === "asc" ? "desc" : "asc")); }
    else { setSortCol(col); setSortDir("asc"); }
  }

  function toggleDomainCollapse(domain) {
    setCollapsedDomains(prev => {
      const next = new Set(prev);
      next.has(domain) ? next.delete(domain) : next.add(domain);
      return next;
    });
  }

  function SortIndicator({ col }) {
    if (sortCol !== col) return <span style={{ fontSize: 8, color: "var(--ink-4,#ccc)" }}>⇅</span>;
    return <span style={{ fontSize: 9, color: "var(--acc,#2563eb)" }}>{sortDir === "asc" ? "▲" : "▼"}</span>;
  }

  const _INTERNAL_FWS = new Set(["Internal", "Internal Risk Register", ""]);

  function inferDomain(risk) {
    // Framework catalog risks use their own category as the domain so they aren't
    // conflated with enterprise risk domains (e.g. CIS "Asset Management" should not
    // land in "Cyber Security & Data Protection").
    const fw = risk.source_framework || "";
    if (fw && !_INTERNAL_FWS.has(fw)) {
      return risk.category || fw;
    }

    const text = ((risk.name || risk.current_wording || "") + " " + (risk.category || "")).toLowerCase();
    if (/\baccess\b|identity|privilege|authentication|authoris|account/.test(text)) return "Identity & Access Management";
    if (/revenue|financial|accounting|fraud|margin|restat/.test(text))              return "Financial Reporting & Controls";
    if (/cyber|security|breach|hack|phishing|encrypt|vulnerab|incident/.test(text)) return "Cyber Security & Data Protection";
    if (/vendor|supplier|third.party|supply.chain|outsourc/.test(text))             return "Third-Party & Vendor Risk";
    if (/continuity|disaster|recovery|\bbcp\b|resilience|availability/.test(text))  return "Operational Resilience";
    if (/compliance|regulatory|legal|penalty|gdpr|ccpa|\bsox\b|privacy/.test(text)) return "Regulatory & Compliance";
    if (/change|configuration|deployment|release|patch|software|technolog/.test(text)) return "Technology & Change Management";
    if (/people|talent|staff|retention|key.person|\bhr\b|hiring|workforce/.test(text)) return "People & Organisational Risk";
    if (/market|macro|interest|credit|inflation|\brate\b|currency|economic/.test(text)) return "Market & Economic Risk";
    return risk.category || "Enterprise Risk";
  }

  useEffect(() => {
    if (!risks?.length) return;
    // Prefer the persisted domain (risk_scores.assigned_domain, returned by the
    // risk-fetch endpoints) over recomputing — only fall back to the client-side
    // keyword guess for risks that have never been categorized. Previously this
    // recomputed (and re-billed the AI call) on every single screen load because
    // nothing read the persisted value back — see risk_scores.assigned_domain.
    const baseline = {};
    for (const r of risks) { baseline[r.id || r.risk_ref] = r.assigned_domain || inferDomain(r); }
    setDomainNames(baseline);

    const uncategorized = risks.filter(r => !r.assigned_domain);
    if (uncategorized.length === 0) return;

    (async () => {
      setDomainsLoading(true);
      try {
        const res = await fetch("/api/risk-register/categorize-domains", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            risks: uncategorized.map(r => ({
              ref: r.id || r.risk_ref,
              name: r.name || r.current_wording || "",
              category: r.category || "",
            })),
            ...(runId ? { run_id: runId } : {}),
          }),
        });
        if (res.ok) {
          const data = await res.json();
          if (data.domains) {
            // Only apply AI domain overrides to enterprise (internal) risks — framework
            // catalog risks keep their category-based domain from inferDomain.
            const enterpriseRefs = new Set(
              uncategorized.filter(r => !r.source_framework || _INTERNAL_FWS.has(r.source_framework))
                   .map(r => r.id || r.risk_ref)
            );
            setDomainNames(prev => {
              const next = { ...prev };
              for (const [ref, domain] of Object.entries(data.domains)) {
                if (enterpriseRefs.has(ref)) next[ref] = domain;
              }
              return next;
            });
          }
        }
      } catch (_) {}
      setDomainsLoading(false);
    })();
  }, [risks?.length]);

  // All matrix framework columns always appear; append any extra frameworks
  // from discovered risks or assigned controls alphabetically after them.
  const _activeMxFws = matrixFrameworks || MATRIX_FRAMEWORKS;
  const _internalFws = new Set(["Internal", "Internal Risk Register"]);
  // Frameworks the user explicitly removed via the × button — controls tagged
  // to them are left in place, so without this the "extra column" detection
  // below (driven purely by live control/risk assignments) would recompute
  // and re-show the column on the very next render or refresh.
  const _hiddenFws = new Set(hiddenFrameworks || []);
  const extraFws = new Set();
  for (const cs of Object.values(ctrlStates)) {
    for (const ref of [...(cs.autoMapped || []), ...(cs.manual || [])]) {
      const fw = CTRL_BY_REF[ref]?.framework;
      if (fw && !_internalFws.has(fw) && !_activeMxFws.includes(fw) && !_hiddenFws.has(fw)) extraFws.add(fw);
    }
  }
  for (const r of (risks || [])) {
    const fw = r.source_framework;
    if (fw && !_internalFws.has(fw) && !_activeMxFws.includes(fw) && !_hiddenFws.has(fw)) extraFws.add(fw);
  }
  const fwCols = [..._activeMxFws, ...[...extraFws].sort()];

  // Frameworks offered when creating a brand-new control from a matrix cell.
  // fwCols alone (pinned columns + columns auto-detected from what's
  // currently visible) still misses a framework that has real, saved
  // controls in the library but happens not to be pinned AND not to be
  // detected as an "extra" column in the CURRENT view — e.g. Enterprise
  // Risks with no assigned control yet from that framework. Union in every
  // framework MASTER_CONTROLS already knows about (same source ControlsPanel's
  // "new control" dropdown already uses) so a real framework is never
  // unselectable here just because nothing on screen right now points to it.
  const _knownCtrlFws = new Set(MASTER_CONTROLS.map(c => c.framework).filter(Boolean));
  const newCtrlFwOptions = ["Internal", ...new Set(
    [...fwCols, ..._knownCtrlFws].filter(f => f && !_internalFws.has(f))
  )];

  // Apply the user's saved drag order, if any: known columns first in their
  // saved positions, then any columns that weren't part of that snapshot yet
  // (new frameworks added since) appended in their natural order — so a stale
  // saved order never hides a column, it just puts unranked ones at the end.
  const persistedColOrder = auth?.user?.preferences?.framework_matrix_column_order;
  const activeColOrder = localColOrder || persistedColOrder;
  const orderedFwCols = activeColOrder?.length
    ? [...activeColOrder.filter(f => fwCols.includes(f)), ...fwCols.filter(f => !activeColOrder.includes(f))]
    : fwCols;

  async function persistColumnOrder(nextOrder) {
    setLocalColOrder(nextOrder);
    if (!auth?.user) return; // not signed in (or auth context unavailable) — session-local only
    auth.setUser?.(prev => prev && ({ ...prev, preferences: { ...(prev.preferences || {}), framework_matrix_column_order: nextOrder } }));
    try {
      await fetch("/auth/users/me/preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ framework_matrix_column_order: nextOrder }),
      });
    } catch (_) {}
  }

  function handleColDrop(targetFw) {
    setDragOverFw(null);
    if (!dragFw || dragFw === targetFw) { setDragFw(null); return; }
    const next = [...orderedFwCols];
    const from = next.indexOf(dragFw);
    const to   = next.indexOf(targetFw);
    if (from === -1 || to === -1) { setDragFw(null); return; }
    next.splice(from, 1);
    next.splice(to, 0, dragFw);
    setDragFw(null);
    persistColumnOrder(next);
  }

  // A control tied to a specific (non-Internal) framework only ever belongs to
  // its own column; Internal/unspecified-framework controls have no single
  // "home" column so they remain visible in every column. Shared by sort,
  // filter, and the per-cell render below so all three stay consistent.
  function computeFwRefs(allRefs, fw) {
    return allRefs.filter(ref => {
      const c = CTRL_BY_REF[ref];
      if (!c) return false;
      return c.framework && !_internalFws.has(c.framework) ? c.framework === fw : true;
    });
  }

  function rowDomain(r, key) { return domainNames[key] || inferDomain(r); }

  function rowMatchesFilters(r, key, cs) {
    const domainFilter = (colFilters.domain || "").trim().toLowerCase();
    if (domainFilter && !rowDomain(r, key).toLowerCase().includes(domainFilter)) return false;

    const riskFilter = (colFilters.risk || "").trim().toLowerCase();
    if (riskFilter) {
      const wording = (riskStates[key]?.wording ?? r.current_wording ?? r.name ?? "");
      const riskText = `${r.category || ""} ${wording}`.toLowerCase();
      if (!riskText.includes(riskFilter)) return false;
    }

    const allRefs = [...(cs.autoMapped || []), ...(cs.manual || [])];
    for (const fw of fwCols) {
      const f = (colFilters[fw] || "").trim().toLowerCase();
      if (!f) continue;
      const fwRefs = computeFwRefs(allRefs, fw);
      if (f === "none" || f === "empty") {
        if (fwRefs.length > 0) return false;
      } else {
        const text = fwRefs.map(ref => `${ref} ${CTRL_BY_REF[ref]?.name || ""}`).join(" ").toLowerCase();
        if (!text.includes(f)) return false;
      }
    }
    return true;
  }

  const anyFilterActive = Object.values(colFilters).some(v => (v || "").trim() !== "");

  // ── Wording row helpers ──────────────────────────────────────────────────

  function startEdit(key) {
    const state = riskStates[key] || {};
    const cs    = ctrlStates[key] || { autoMapped: [], manual: [] };
    setRowDrafts(prev => ({
      ...prev,
      [key]: { wording: state.wording || "", autoMapped: [...(cs.autoMapped || [])], manual: [...(cs.manual || [])] },
    }));
    setEditingRows(prev => new Set([...prev, key]));
  }

  function cancelEdit(key) {
    const draft = rowDrafts[key];
    if (draft) {
      onWordingChange(key, draft.wording);
      onResetCtrl(key, draft.autoMapped, draft.manual);
    }
    setEditingRows(prev => { const next = new Set(prev); next.delete(key); return next; });
  }

  function doneEdit(key) {
    setEditingRows(prev => { const next = new Set(prev); next.delete(key); return next; });
  }

  // ── Cell expand helpers ──────────────────────────────────────────────────

  async function saveCellControls(key, fw) {
    const cellId  = `${key}:${fw}`;
    setSavingCells(prev => new Set([...prev, cellId]));
    const state   = riskStates[key] || {};
    const cs      = ctrlStates[key] || { autoMapped: [], manual: [] };
    const allRefs = [...(cs.autoMapped || []), ...(cs.manual || [])];
    await onSaveRow(key, state, allRefs);
    setSavingCells(prev => { const next = new Set(prev); next.delete(cellId); return next; });
  }

  // ── "+ New" (create a brand-new control) per cell ──────────────────────────

  function openCreateForCell(key, fw) {
    setNewCtrlDraft({ ref: _nextRefForFramework(fw), name: "", framework: fw, desc: "", pacControlId: "" });
    setCreateErr("");
    setFwCreate({ key, fw });
    setFwPicker(null);
  }

  async function handleCreateForCell() {
    const ref = newCtrlDraft.ref.trim().toUpperCase();
    if (!ref) { setCreateErr("Control reference is required."); return; }
    if (CTRL_BY_REF[ref]) { setCreateErr(`${ref} already exists in the control library.`); return; }
    if (!/^[A-Za-z]/.test(ref)) { setCreateErr("Reference must start with a letter."); return; }
    if (!newCtrlDraft.name.trim()) { setCreateErr("Control name is required."); return; }
    const pacControlId = newCtrlDraft.pacControlId.trim().toUpperCase() || null;
    const ctrl = {
      ref, framework: newCtrlDraft.framework || "Custom", name: newCtrlDraft.name.trim(),
      category: "Custom", domain: "Custom",
      description: newCtrlDraft.desc.trim(), desc: newCtrlDraft.desc.trim(),
      pac_control_id: pacControlId,
    };
    try {
      const res = await fetch("/api/risk-register/controls", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(ctrl),
      });
      const data = await res.json().catch(() => null);
      if (data && data.saved === false) { setCreateErr(data.detail || "Could not create control."); return; }
    } catch (_) {}
    MASTER_CONTROLS.push(ctrl);
    CTRL_BY_REF[ref] = ctrl;
    onAddManualControl(fwCreate.key, ref);
    const rState = riskStates[fwCreate.key] || {};
    const rCs    = ctrlStates[fwCreate.key] || { autoMapped: [], manual: [] };
    onSaveRow(fwCreate.key, rState, [...(rCs.autoMapped || []), ...(rCs.manual || []), ref]);
    setFwCreate(null);
    setCreateErr("");
  }

  // ── Styles ───────────────────────────────────────────────────────────────

  const thStyle = {
    padding: "8px 10px", textAlign: "left", fontSize: 10, fontWeight: 700,
    color: "var(--ink-2,#555)", background: "var(--surface-2,#f8f9fa)",
    borderBottom: "2px solid var(--line,#e0e0e0)", borderRight: "1px solid var(--line,#eee)",
    textTransform: "uppercase", letterSpacing: "0.05em", whiteSpace: "nowrap",
  };
  const tdStyle = {
    padding: "10px 10px", verticalAlign: "top",
    borderBottom: "1px solid var(--line,#eee)", borderRight: "1px solid var(--line,#f0f0f0)",
  };
  const matrixMinWidth = `${340 + fwCols.length * 200}px`;
  const allDomains = [...new Set((risks || []).map(r => domainNames[r.id || r.risk_ref] || inferDomain(r)))];

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 4 }}>
          <button
            onClick={() => setCollapsedDomains(new Set(allDomains))}
            title="Collapse all domain groups"
            style={{ fontSize: 9, padding: "2px 8px", borderRadius: 3, cursor: "pointer", border: "1px solid var(--line,#ddd)", background: "transparent", color: "var(--ink-2,#555)" }}
          >Collapse all</button>
          <button
            onClick={() => setCollapsedDomains(new Set())}
            title="Expand all domain groups"
            style={{ fontSize: 9, padding: "2px 8px", borderRadius: 3, cursor: "pointer", border: "1px solid var(--line,#ddd)", background: "transparent", color: "var(--ink-2,#555)" }}
          >Expand all</button>
        </div>
        {sortCol && (
          <span style={{ fontSize: 9, color: "var(--ink-3,#888)" }}>
            Sorted by <b>{sortCol === "domain" ? "Core Domains & Risks" : sortCol === "risk" ? "Enterprise Risks" : sortCol}</b> ({sortDir === "asc" ? "ascending" : "descending"})
            {" · "}
            <button onClick={() => { setSortCol(null); setSortDir("asc"); }} style={{ fontSize:9, padding:0, border:"none", background:"transparent", color:"var(--acc,#2563eb)", cursor:"pointer", textDecoration:"underline" }}>reset sort</button>
          </span>
        )}
        {savedAt && (
          <div style={{ fontSize: 10, color: "var(--green,#2a7)", display: "flex", alignItems: "center", gap: 4 }}>
            <span>✓</span> Saved at {savedAt}
          </div>
        )}
      </div>

      <div ref={scrollWrapRef} style={{ overflowX: "auto", maxWidth: "100%" }}>
        <table style={{ width: "100%", minWidth: matrixMinWidth, borderCollapse: "collapse", fontSize: 11 }}>
          <thead>
            <tr>
              <th style={{ ...thStyle, width: 160, minWidth: 140, cursor: "pointer" }} onClick={() => toggleSort("domain")}>
                <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  <span>Core Domains &amp; Risks</span>
                  <SortIndicator col="domain" />
                </span>
                {domainsLoading && (
                  <span style={{ fontSize: 8, fontWeight: 400, color: "var(--ink-3,#aaa)", marginLeft: 5 }}>generating…</span>
                )}
              </th>
              <th style={{ ...thStyle, minWidth: 200, width: "26%", cursor: "pointer" }} onClick={() => toggleSort("risk")}>
                <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  <span>Enterprise Risks</span>
                  <SortIndicator col="risk" />
                </span>
              </th>
              {orderedFwCols.map(fw => (
                <th
                  key={fw}
                  draggable
                  onDragStart={() => setDragFw(fw)}
                  onDragOver={e => { e.preventDefault(); if (dragOverFw !== fw) setDragOverFw(fw); }}
                  onDragLeave={() => setDragOverFw(prev => (prev === fw ? null : prev))}
                  onDrop={e => { e.preventDefault(); handleColDrop(fw); }}
                  onDragEnd={() => { setDragFw(null); setDragOverFw(null); }}
                  title="Drag to reorder columns — remembered for your account"
                  style={{
                    ...thStyle, minWidth: 200, cursor: "grab",
                    opacity: dragFw === fw ? 0.4 : 1,
                    background: dragOverFw === fw && dragFw && dragFw !== fw ? "var(--acc-soft,rgba(37,99,235,0.12))" : thStyle.background,
                    borderLeft: dragOverFw === fw && dragFw && dragFw !== fw ? "2px solid var(--acc,#2563eb)" : undefined,
                  }}
                >
                  <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
                    <span style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }} onClick={() => toggleSort(fw)}>
                      <span style={{ fontSize: 9, color: "var(--ink-4,#ccc)" }}>⠿</span>
                      <span>{fw}</span>
                      <SortIndicator col={fw} />
                    </span>
                    {onRemoveFramework && _activeMxFws.includes(fw) && (
                      <button
                        title={`Remove ${fw} from the Framework Matrix`}
                        onClick={() => onRemoveFramework(fw)}
                        style={{
                          flexShrink: 0, fontSize: 12, padding: "0 4px", border: "none",
                          background: "transparent", color: "var(--ink-3,#aaa)", cursor: "pointer",
                          lineHeight: "16px", borderRadius: 3, fontWeight: 400, textTransform: "none",
                        }}
                        onMouseEnter={e => { e.currentTarget.style.color = "var(--red,#e53)"; e.currentTarget.style.background = "rgba(229,85,51,0.08)"; }}
                        onMouseLeave={e => { e.currentTarget.style.color = "var(--ink-3,#aaa)"; e.currentTarget.style.background = "transparent"; }}
                      >×</button>
                    )}
                    {onPurgeExtraFramework && !_activeMxFws.includes(fw) && (
                      // Not in the configured matrix at all — this column only exists
                      // because a real control tagged with this framework is still
                      // assigned to a risk (see fwCols/extraFws above). There's no
                      // "config" to remove it from; removing it here means actually
                      // unassigning those controls, so it's a distinct, more consequential
                      // action than onRemoveFramework — handler owns its own confirmation.
                      <button
                        title={`"${fw}" isn't in the configured Framework Matrix — it's shown because a control is still assigned to it. Click to unassign and remove this column.`}
                        onClick={() => onPurgeExtraFramework(fw)}
                        style={{
                          flexShrink: 0, fontSize: 12, padding: "0 4px", border: "none",
                          background: "transparent", color: "var(--ink-3,#aaa)", cursor: "pointer",
                          lineHeight: "16px", borderRadius: 3, fontWeight: 400, textTransform: "none",
                        }}
                        onMouseEnter={e => { e.currentTarget.style.color = "var(--red,#e53)"; e.currentTarget.style.background = "rgba(229,85,51,0.08)"; }}
                        onMouseLeave={e => { e.currentTarget.style.color = "var(--ink-3,#aaa)"; e.currentTarget.style.background = "transparent"; }}
                      >×</button>
                    )}
                  </span>
                </th>
              ))}
            </tr>
            {/* Filter row — one text input per column, spreadsheet-style */}
            <tr>
              <th style={{ ...thStyle, padding: "4px 6px", fontWeight: 400, textTransform: "none" }}>
                <input
                  className="dendrai-input"
                  placeholder="Filter domain…"
                  value={colFilters.domain || ""}
                  onChange={e => setColFilters(prev => ({ ...prev, domain: e.target.value }))}
                  style={{ fontSize: 9, padding: "2px 5px", width: "100%", boxSizing: "border-box" }}
                />
              </th>
              <th style={{ ...thStyle, padding: "4px 6px", fontWeight: 400, textTransform: "none" }}>
                <input
                  className="dendrai-input"
                  placeholder="Filter risks…"
                  value={colFilters.risk || ""}
                  onChange={e => setColFilters(prev => ({ ...prev, risk: e.target.value }))}
                  style={{ fontSize: 9, padding: "2px 5px", width: "100%", boxSizing: "border-box" }}
                />
              </th>
              {orderedFwCols.map(fw => (
                <th key={fw} style={{ ...thStyle, padding: "4px 6px", fontWeight: 400, textTransform: "none" }}>
                  <input
                    className="dendrai-input"
                    placeholder='Filter controls… ("none" = unassigned)'
                    value={colFilters[fw] || ""}
                    onChange={e => setColFilters(prev => ({ ...prev, [fw]: e.target.value }))}
                    style={{ fontSize: 9, padding: "2px 5px", width: "100%", boxSizing: "border-box" }}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {anyFilterActive && (
              <tr>
                <td colSpan={2 + fwCols.length} style={{ padding: "4px 10px", fontSize: 9, color: "var(--ink-3,#888)", background: "var(--surface-2,#f8f9fa)", borderBottom: "1px solid var(--line,#eee)" }}>
                  Filtered — <button onClick={() => setColFilters({})} style={{ fontSize:9, padding:0, border:"none", background:"transparent", color:"var(--acc,#2563eb)", cursor:"pointer", textDecoration:"underline" }}>clear all filters</button>
                </td>
              </tr>
            )}
            {[...(risks || [])].filter((r, idx, arr) => {
              const key = r.id || r.risk_ref || `idx-${idx}`;
              const cs  = ctrlStates[key] || { autoMapped: autoMapControls(r.name, r.category), manual: [] };
              return rowMatchesFilters(r, key, cs);
            }).sort((a, b) => {
              const keyA = a.id || a.risk_ref, keyB = b.id || b.risk_ref;
              if (sortCol && sortCol !== "domain") {
                let cmp;
                if (sortCol === "risk") {
                  cmp = (a.category || a.name || "").localeCompare(b.category || b.name || "");
                } else {
                  const csA = ctrlStates[keyA] || { autoMapped: [], manual: [] };
                  const csB = ctrlStates[keyB] || { autoMapped: [], manual: [] };
                  const allA = [...(csA.autoMapped || []), ...(csA.manual || [])];
                  const allB = [...(csB.autoMapped || []), ...(csB.manual || [])];
                  cmp = computeFwRefs(allA, sortCol).length - computeFwRefs(allB, sortCol).length;
                }
                return sortDir === "asc" ? cmp : -cmp;
              }
              // Default / explicit domain sort: primary Core Domain & Risks, secondary
              // Enterprise Risks, so each domain group is internally ordered too.
              const da = domainNames[a.id || a.risk_ref] || inferDomain(a);
              const db_ = domainNames[b.id || b.risk_ref] || inferDomain(b);
              let domainCmp = da.localeCompare(db_);
              if (domainCmp === 0) domainCmp = (a.category || "Risk").localeCompare(b.category || "Risk");
              return sortDir === "asc" ? domainCmp : -domainCmp;
            }).map((r, idx, arr) => {
              // Fallback for legacy rows persisted before predictive_analytics_tool.py
              // assigned stable risk ids — prevents every id-less risk from colliding
              // onto the same React key and sharing wording/control state.
              const key      = r.id || r.risk_ref || `idx-${idx}`;
              const state    = riskStates[key] || { wording: r.current_wording || r.name || "", included: true };
              const cs       = ctrlStates[key] || { autoMapped: autoMapControls(r.name, r.category), manual: [], generateCode: new Set() };
              const allRefs  = [...(cs.autoMapped || []), ...(cs.manual || [])];
              const isEditing = editingRows.has(key);
              const isSaving  = savingRows.has(key);
              const domain    = domainNames[key] || inferDomain(r);
              const prevRisk  = idx > 0 ? arr[idx - 1] : null;
              const prevDomain = prevRisk ? (domainNames[prevRisk.id || prevRisk.risk_ref] || inferDomain(prevRisk)) : null;
              const isGroupStart = domain !== prevDomain;
              // Collapse only makes sense when rows are actually grouped contiguously
              // by domain — sorting by Enterprise Risks or a framework column breaks
              // that contiguity, so collapse is disabled (and irrelevant) there.
              const isGroupedMode = !sortCol || sortCol === "domain";
              const domainCollapsed = isGroupedMode && collapsedDomains.has(domain);
              if (isGroupedMode && domainCollapsed && !isGroupStart) return null;

              let groupCount = 1;
              if (isGroupStart && isGroupedMode) {
                for (let j = idx + 1; j < arr.length; j++) {
                  const rj = arr[j];
                  const kj = rj.id || rj.risk_ref || `idx-${j}`;
                  if ((domainNames[kj] || inferDomain(rj)) !== domain) break;
                  groupCount++;
                }
              }

              return (
                <tr key={key} style={{ opacity: state.included ? 1 : 0.45, background: isEditing ? "rgba(37,99,235,0.025)" : "transparent" }}>

                  {/* Column 0 — Core domain */}
                  <td style={{
                    ...tdStyle,
                    borderTop: isGroupStart && idx > 0 ? "2px solid var(--line,#d8d8d8)" : undefined,
                    paddingTop: isGroupStart && idx > 0 ? 14 : undefined,
                    verticalAlign: "top",
                    minWidth: 140,
                    maxWidth: 160,
                  }}>
                    {isGroupStart ? (
                      <div
                        onClick={isGroupedMode ? () => toggleDomainCollapse(domain) : undefined}
                        style={{ display: "flex", alignItems: "center", gap: 4, fontWeight: 700, fontSize: 10, color: "var(--acc,#2563eb)", lineHeight: 1.4, cursor: isGroupedMode ? "pointer" : "default" }}
                      >
                        {isGroupedMode && (
                          <span style={{ fontSize: 8, display: "inline-block", transform: domainCollapsed ? "rotate(-90deg)" : "none", transition: "transform .1s" }}>▾</span>
                        )}
                        <span>{domain}</span>
                        {domainCollapsed && (
                          <span style={{ fontSize: 9, fontWeight: 400, color: "var(--ink-3,#888)" }}>({groupCount})</span>
                        )}
                      </div>
                    ) : (
                      <div style={{
                        fontSize: 10, color: "var(--ink-3,#bbb)", lineHeight: 1.4,
                        paddingLeft: 8, borderLeft: "2px solid var(--line,#eee)",
                      }}>
                        {domain}
                      </div>
                    )}
                  </td>

                  {/* Column 1 — Risk domain + wording */}
                  <td style={tdStyle}>
                    <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 5 }}>
                      <span style={{ fontWeight: 700, fontSize: 11, color: "var(--ink,#111)" }}>
                        {r.category || "Risk"}
                      </span>
                      {r.score != null && <ScoreBadge score={r.score} />}
                      {(r.rag_status || r.rag) && <RagDot rag={r.rag_status || r.rag} />}
                    </div>
                    {isEditing ? (
                      <textarea
                        value={state.wording}
                        onChange={e => onWordingChange(key, e.target.value)}
                        rows={3}
                        className="dendrai-input"
                        style={{
                          width: "100%", fontSize: 10, lineHeight: 1.5, resize: "vertical",
                          padding: "4px 7px", boxSizing: "border-box",
                          border: "1px solid var(--acc,#2563eb)",
                          borderRadius: 4, background: "var(--surface,#fff)", fontFamily: "inherit",
                        }}
                        autoFocus
                      />
                    ) : (
                      <div style={{ fontSize: 10, color: "var(--ink-2,#555)", lineHeight: 1.5, fontStyle: "italic" }}>
                        {state.wording || <span style={{ color: "var(--ink-3,#aaa)" }}>No wording</span>}
                      </div>
                    )}
                    <div style={{ marginTop: 6, display: "flex", gap: 4 }}>
                      {isEditing ? (
                        <>
                          <button className="btn btn-sm btn-acc" onClick={() => doneEdit(key)} style={{ fontSize: 9, padding: "2px 9px" }}>
                            Done
                          </button>
                          <button className="btn btn-sm" onClick={() => cancelEdit(key)} style={{ fontSize: 9, padding: "2px 9px" }}>
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button className="btn btn-sm" onClick={() => startEdit(key)} style={{ fontSize: 9, padding: "2px 7px" }} title="Edit risk wording">
                          Edit
                        </button>
                      )}
                    </div>
                  </td>

                  {/* Framework columns — same format as Enterprise Risks */}
                  {orderedFwCols.map(fw => {
                    // A control tied to a specific (non-Internal) framework belongs only
                    // to its own column — no longer repeated across every framework.
                    // Internal/unspecified-framework controls aren't "specific" to any
                    // framework, so they remain visible in every column as before.
                    const fwRefs       = computeFwRefs(allRefs, fw);
                    const cellId       = `${key}:${fw}`;
                    const isSavingCell = savingCells.has(cellId);
                    const pickerOpen   = fwPicker?.key === key && fwPicker?.fw === fw;
                    const createOpen   = fwCreate?.key === key && fwCreate?.fw === fw;
                    // Deliberately NOT framework-restricted: any not-yet-assigned control
                    // can be added from any column's picker. The dedup above (fwRefs) makes
                    // sure a framework-specific control still only ever DISPLAYS in its own
                    // native column — restricting the picker itself just meant a column
                    // whose own framework's controls were all already assigned showed
                    // nothing addable at all, even though plenty of controls existed.
                    const addable      = MASTER_CONTROLS.filter(c =>
                      !allRefs.includes(c.ref) &&
                      (ctrlSearch === "" ||
                        c.name.toLowerCase().includes(ctrlSearch.toLowerCase()) ||
                        c.ref.toLowerCase().includes(ctrlSearch.toLowerCase()) ||
                        (c.framework || "").toLowerCase().includes(ctrlSearch.toLowerCase()))
                    );

                    return (
                      <td key={fw} style={tdStyle}>
                        {/* Risk wording — mirrors Enterprise Risks column */}
                        <div style={{ fontSize: 10, color: "var(--ink-2,#555)", lineHeight: 1.5, fontStyle: "italic", marginBottom: 8, paddingBottom: 6, borderBottom: "1px solid var(--line,#eee)" }}>
                          {state.wording || <span style={{ color: "var(--ink-3,#aaa)" }}>No wording</span>}
                        </div>

                        {/* Control list */}
                        {fwRefs.length > 0 ? (
                          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 6 }}>
                            {fwRefs.map(ref => {
                              const ctrl = CTRL_BY_REF[ref];
                              const isNativeFw = ctrl?.framework === fw;
                              return (
                                <div key={ref} style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
                                  <div style={{ flex: 1 }}>
                                    <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap", marginBottom: 2 }}>
                                      <span style={{ fontWeight: 700, fontSize: 11, color: "var(--ink,#111)" }}>
                                        {ctrl?.name || ref}
                                      </span>
                                      {!isNativeFw && ctrl?.framework && (
                                        <span style={{ fontSize: 8, padding: "1px 4px", borderRadius: 3, background: "var(--surface-2,#f0f0f0)", color: "var(--ink-3,#888)", border: "1px solid var(--line,#e0e0e0)", whiteSpace: "nowrap" }}>
                                          {ctrl.framework}
                                        </span>
                                      )}
                                      {ctrl?.pac_control_id && (
                                        <span title={`Enforced by PaC control ${ctrl.pac_control_id}`} style={{ fontSize: 8, color: "var(--acc,#2563eb)", whiteSpace: "nowrap" }}>
                                          ⚡{ctrl.pac_control_id}
                                        </span>
                                      )}
                                    </div>
                                    {ctrl?.desc && (
                                      <div style={{ fontSize: 10, color: "var(--ink-2,#555)", lineHeight: 1.5, fontStyle: "italic" }}>
                                        {ctrl.desc}
                                      </div>
                                    )}
                                  </div>
                                  <button
                                    title={isNativeFw ? "Remove control" : "Shared control — removing it here removes it from every framework column showing it"}
                                    onClick={() => {
                                      // This control isn't native to this framework column — it's an
                                      // Internal/unspecified-framework control shown here only because
                                      // it has no single "home" column (see fwRefs filter above), so
                                      // there's really one shared assignment behind every copy shown.
                                      // Removing it always affects every column — that's unavoidable
                                      // given the data model — so require an explicit confirmation
                                      // instead of either silently cascading or being unremovable here.
                                      if (!isNativeFw && !window.confirm(
                                        `"${ctrl?.name || ref}" has no single framework — it's shown in every column because it's one shared control assignment. Removing it here removes it from ALL frameworks on this row, not just ${fw}. Continue?`
                                      )) return;
                                      const isAuto = cs.autoMapped.includes(ref);
                                      onRemoveControl(key, ref, isAuto);
                                      // onRemoveControl only updates local state — without an explicit
                                      // save it silently reverts on the next refresh/reload, which is
                                      // exactly the "deleted framework comes back" bug this fixes.
                                      onSaveRow(key, state, allRefs.filter(r => r !== ref));
                                    }}
                                    style={{
                                      flexShrink: 0, fontSize: 13, padding: "0 5px", border: "none",
                                      background: "transparent", color: "var(--ink-3,#aaa)", cursor: "pointer",
                                      lineHeight: "18px", borderRadius: 3,
                                    }}
                                    onMouseEnter={e => { e.currentTarget.style.color = "var(--red,#e53)"; e.currentTarget.style.background = "rgba(229,85,51,0.08)"; }}
                                    onMouseLeave={e => { e.currentTarget.style.color = "var(--ink-3,#aaa)"; e.currentTarget.style.background = "transparent"; }}
                                  >×</button>
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <div style={{ fontSize: 10, color: "var(--ink-3,#aaa)", fontStyle: "italic", marginBottom: 6 }}>
                            No controls assigned
                          </div>
                        )}

                        {/* Actions */}
                        <div style={{ marginTop: 6, display: "flex", gap: 4 }}>
                          <button
                            onClick={() => { setFwPicker(pickerOpen ? null : { key, fw }); setCtrlSearch(""); setFwCreate(null); }}
                            style={{
                              fontSize: 9, padding: "2px 8px", borderRadius: 3, cursor: "pointer",
                              border: "1px dashed var(--acc,#2563eb)", background: "transparent",
                              color: "var(--acc,#2563eb)",
                            }}
                          >+ Add</button>
                          <button
                            onClick={() => { if (createOpen) { setFwCreate(null); } else { openCreateForCell(key, fw); } }}
                            title="Create a brand-new control with a new reference number"
                            style={{
                              fontSize: 9, padding: "2px 8px", borderRadius: 3, cursor: "pointer",
                              border: "1px dashed var(--line,#ccc)", background: "transparent",
                              color: "var(--ink-2,#555)",
                            }}
                          >+ New</button>
                        </div>

                        {/* Control picker */}
                        {pickerOpen && (
                          <div style={{
                            marginTop: 6, padding: 6, background: "var(--surface,#fff)",
                            border: "1px solid var(--line,#ddd)", borderRadius: 6,
                            boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
                            maxHeight: 180, overflow: "hidden", display: "flex", flexDirection: "column", gap: 4,
                            position: "relative", zIndex: 10,
                          }}>
                            <input
                              className="dendrai-input"
                              placeholder="Search controls…"
                              value={ctrlSearch}
                              onChange={e => setCtrlSearch(e.target.value)}
                              style={{ fontSize: 9, padding: "2px 6px" }}
                              autoFocus
                            />
                            <div style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: 1 }}>
                              {/* Same fix as ControlsPanel's picker above: this used
                                  to cap at 12 regardless of search, alphabetically —
                                  so any framework sorting past the ~35th control ref
                                  (everything "SOX*" included) was invisible until you
                                  typed a search term. See _CONTROL_PICKER_CAP. */}
                              {addable.slice(0, _CONTROL_PICKER_CAP).map(c => (
                                <button
                                  key={c.ref}
                                  onClick={() => {
                                    onAddManualControl(key, c.ref);
                                    onSaveRow(key, state, [...allRefs, c.ref]);
                                    setFwPicker(null); setCtrlSearch("");
                                  }}
                                  style={{
                                    display: "flex", gap: 5, padding: "4px 4px", border: "none",
                                    background: "transparent", cursor: "pointer", textAlign: "left", fontSize: 9, borderRadius: 3,
                                  }}
                                  onMouseEnter={e => e.currentTarget.style.background = "var(--surface-2,#f5f5f5)"}
                                  onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                                >
                                  <span className="mono" style={{ fontWeight: 600, color: "var(--acc,#2563eb)", minWidth: 40 }}>{c.ref}</span>
                                  <span style={{ color: "var(--ink,#111)", flex: 1 }}>{c.name}</span>
                                  {c.framework && c.framework !== fw && (
                                    <span style={{ fontSize: 8, padding: "1px 4px", borderRadius: 3, background: "var(--surface-2,#f0f0f0)", color: "var(--ink-3,#888)", border: "1px solid var(--line,#e0e0e0)", whiteSpace: "nowrap", flexShrink: 0 }}>{c.framework}</span>
                                  )}
                                </button>
                              ))}
                              {addable.length === 0 && (
                                <span style={{ fontSize: 9, color: "var(--ink-3,#888)", padding: "3px 4px" }}>All controls already assigned</span>
                              )}
                              {addable.length > _CONTROL_PICKER_CAP && (
                                <span style={{ fontSize: 8.5, color: "var(--ink-3,#888)", padding: "2px 4px", fontStyle: "italic" }}>
                                  Showing {_CONTROL_PICKER_CAP} of {addable.length} — type to narrow the search
                                </span>
                              )}
                            </div>
                          </div>
                        )}

                        {/* Create-new-control form */}
                        {createOpen && (
                          <div style={{
                            marginTop: 6, padding: 8, background: "var(--surface,#fff)",
                            border: "1px solid var(--acc,#2563eb)", borderRadius: 6,
                            display: "flex", flexDirection: "column", gap: 6,
                            position: "relative", zIndex: 10,
                          }}>
                            <div style={{ display: "flex", gap: 6 }}>
                              <div style={{ flex: "0 0 80px" }}>
                                <input
                                  className="dendrai-input"
                                  placeholder="Ref"
                                  value={newCtrlDraft.ref}
                                  onChange={e => setNewCtrlDraft(p => ({ ...p, ref: e.target.value }))}
                                  style={{ fontSize: 9, padding: "3px 6px", width: "100%", boxSizing: "border-box" }}
                                  title="Auto-populated from the selected framework's next available number — edit to override"
                                  autoFocus
                                />
                              </div>
                              <select
                                className="dendrai-input"
                                value={newCtrlDraft.framework}
                                onChange={e => {
                                  const nfw = e.target.value;
                                  setNewCtrlDraft(p => ({ ...p, framework: nfw, ref: _nextRefForFramework(nfw) }));
                                }}
                                style={{ fontSize: 9, padding: "3px 6px", flex: 1, cursor: "pointer" }}
                              >
                                {newCtrlFwOptions.map(f => (
                                  <option key={f} value={f}>{f}</option>
                                ))}
                              </select>
                            </div>
                            <input
                              className="dendrai-input"
                              placeholder="Control name…"
                              value={newCtrlDraft.name}
                              onChange={e => setNewCtrlDraft(p => ({ ...p, name: e.target.value }))}
                              style={{ fontSize: 9, padding: "3px 6px" }}
                            />
                            <select
                              className="dendrai-input"
                              value={newCtrlDraft.pacControlId}
                              onChange={e => setNewCtrlDraft(p => ({ ...p, pacControlId: e.target.value }))}
                              style={{ fontSize: 9, padding: "3px 6px", cursor: "pointer" }}
                              title="Optionally link this to a real, policy-enforced control"
                            >
                              <option value="">Not linked to a PaC control</option>
                              {PAC_CATALOG_CONTROLS.map(c => (
                                <option key={c.control_id} value={c.control_id}>
                                  {c.control_id} — {c.name}{c.source === "pac_rego" ? " (enforced)" : ""}
                                </option>
                              ))}
                            </select>
                            {createErr && (
                              <div style={{ fontSize: 9, color: "var(--red,#e53)" }}>{createErr}</div>
                            )}
                            <div style={{ display: "flex", gap: 4 }}>
                              <button className="btn btn-sm btn-acc" onClick={handleCreateForCell} style={{ fontSize: 9, padding: "2px 10px" }}>
                                Create &amp; Assign
                              </button>
                              <button className="btn btn-sm" onClick={() => { setFwCreate(null); setCreateErr(""); }} style={{ fontSize: 9, padding: "2px 8px" }}>
                                Cancel
                              </button>
                            </div>
                          </div>
                        )}
                      </td>
                    );
                  })}

                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <StickyHScrollBar targetRef={scrollWrapRef} contentWidth={matrixMinWidth} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Control Coverage Matrix
// Rows = controls, Columns = frameworks, cell = ✓ when control belongs to that fw
// ─────────────────────────────────────────────────────────────────────────────

function ControlCoverageMatrix({ ctrlStates }) {
  const [, bump] = useState(0);
  const [linkSaving, setLinkSaving] = useState(null); // ref currently being saved
  const scrollWrapRef = useRef(null);

  async function handleSetPacLink(ref, pacControlId) {
    setLinkSaving(ref);
    try {
      const res = await fetch(`/api/risk-register/controls/${encodeURIComponent(ref)}/pac-link`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pac_control_id: pacControlId || null }),
      });
      const data = await res.json().catch(() => null);
      if (data?.saved !== false) {
        const ctrl = CTRL_BY_REF[ref];
        if (ctrl) ctrl.pac_control_id = pacControlId || null;
        bump(n => n + 1);
      }
    } catch (_) {}
    setLinkSaving(null);
  }

  // Derive the full set of frameworks and controls at render time so we pick up
  // any DB-loaded controls that were appended to MASTER_CONTROLS.
  const allFws = [...new Set(MASTER_CONTROLS.map(c => c.framework || "Internal"))].sort((a, b) => {
    // Pin "Internal" first
    if (a === "Internal") return -1;
    if (b === "Internal") return 1;
    return a.localeCompare(b);
  });

  // Which control refs are actively assigned to at least one risk?
  const assignedRefs = new Set();
  for (const cs of Object.values(ctrlStates || {})) {
    for (const ref of [...(cs.autoMapped || []), ...(cs.manual || [])]) assignedRefs.add(ref);
  }

  // Group controls by category for visual separation
  const byCategory = {};
  for (const c of MASTER_CONTROLS) {
    const cat = c.category || "Other";
    if (!byCategory[cat]) byCategory[cat] = [];
    byCategory[cat].push(c);
  }
  const categories = Object.keys(byCategory).sort();

  const thStyle = {
    padding: "6px 10px", textAlign: "left", fontSize: 10, fontWeight: 700,
    color: "var(--ink-2,#555)", background: "var(--surface-2,#f8f9fa)",
    borderBottom: "2px solid var(--line,#e0e0e0)", borderRight: "1px solid var(--line,#eee)",
    textTransform: "uppercase", letterSpacing: "0.05em", whiteSpace: "nowrap",
  };
  const tdStyle = {
    padding: "6px 10px", verticalAlign: "middle",
    borderBottom: "1px solid var(--line,#eee)", borderRight: "1px solid var(--line,#f0f0f0)",
    fontSize: 11,
  };
  const coverageMinWidth = `${390 + allFws.length * 80}px`;

  return (
    <div>
    <div ref={scrollWrapRef} style={{ overflowX: "auto", maxWidth: "100%" }}>
      <table style={{ width: "100%", minWidth: coverageMinWidth, borderCollapse: "collapse", fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...thStyle, minWidth: 90 }}>Category</th>
            <th style={{ ...thStyle, minWidth: 70 }}>Ref</th>
            <th style={{ ...thStyle, minWidth: 220, width: "30%" }}>Control Name</th>
            {allFws.map(fw => (
              <th key={fw} style={{ ...thStyle, minWidth: 80, textAlign: "center" }}>{fw}</th>
            ))}
            <th style={{ ...thStyle, minWidth: 160 }}>PaC Link</th>
          </tr>
        </thead>
        <tbody>
          {categories.flatMap(cat =>
            byCategory[cat].map((ctrl, idx) => {
              const isActive = assignedRefs.has(ctrl.ref);
              const isGroupStart = idx === 0;
              return (
                <tr
                  key={ctrl.ref}
                  style={{ background: isActive ? "rgba(37,99,235,0.04)" : "transparent" }}
                >
                  {/* Category label — shown only on first row of each group */}
                  {isGroupStart && (
                    <td
                      rowSpan={byCategory[cat].length}
                      style={{
                        ...tdStyle,
                        borderTop: "2px solid var(--line,#d8d8d8)",
                        fontWeight: 700, fontSize: 10,
                        color: "var(--acc,#2563eb)",
                        verticalAlign: "top", paddingTop: 10,
                        minWidth: 80, maxWidth: 100,
                      }}
                    >
                      {cat}
                    </td>
                  )}
                  {/* Ref */}
                  <td style={{ ...tdStyle, borderTop: isGroupStart ? "2px solid var(--line,#d8d8d8)" : undefined, fontWeight: 700, fontFamily: "monospace", color: "var(--acc,#2563eb)", fontSize: 10 }}>
                    {ctrl.ref}
                    {isActive && (
                      <span title="Actively assigned to a risk" style={{ marginLeft: 4, color: "var(--green,#16a34a)", fontSize: 9 }}>●</span>
                    )}
                  </td>
                  {/* Control name */}
                  <td style={{ ...tdStyle, borderTop: isGroupStart ? "2px solid var(--line,#d8d8d8)" : undefined }}>
                    <div style={{ fontWeight: 600, color: "var(--ink,#111)", marginBottom: ctrl.desc ? 2 : 0 }}>
                      {ctrl.name}
                    </div>
                    {ctrl.desc && (
                      <div style={{ fontSize: 9, color: "var(--ink-2,#666)", lineHeight: 1.4 }}>
                        {ctrl.desc}
                      </div>
                    )}
                  </td>
                  {/* Framework checkmarks */}
                  {allFws.map(fw => (
                    <td key={fw} style={{ ...tdStyle, borderTop: isGroupStart ? "2px solid var(--line,#d8d8d8)" : undefined, textAlign: "center" }}>
                      {(ctrl.framework || "Internal") === fw ? (
                        <span style={{ color: "var(--green,#16a34a)", fontWeight: 700, fontSize: 14 }}>✓</span>
                      ) : null}
                    </td>
                  ))}
                  {/* PaC link */}
                  <td style={{ ...tdStyle, borderTop: isGroupStart ? "2px solid var(--line,#d8d8d8)" : undefined }}>
                    <select
                      value={ctrl.pac_control_id || ""}
                      disabled={linkSaving === ctrl.ref}
                      onChange={e => handleSetPacLink(ctrl.ref, e.target.value)}
                      style={{
                        fontSize: 9, padding: "2px 4px", width: "100%", boxSizing: "border-box",
                        borderRadius: 3, border: "1px solid var(--line,#ddd)",
                        background: ctrl.pac_control_id ? "rgba(37,99,235,0.06)" : "var(--surface,#fff)",
                        color: ctrl.pac_control_id ? "var(--acc,#2563eb)" : "var(--ink-3,#888)",
                        cursor: "pointer",
                      }}
                      title="Link this register control to the real PaC control it corresponds to"
                    >
                      <option value="">Not linked</option>
                      {PAC_CATALOG_CONTROLS.map(c => (
                        <option key={c.control_id} value={c.control_id}>
                          {c.control_id}{c.source === "pac_rego" ? " ⚡" : ""}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
    <StickyHScrollBar targetRef={scrollWrapRef} contentWidth={coverageMinWidth} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

// Load the most-recent review session for a run and overlay its controls_assigned
// onto an existing ctrlStates map. Returns merged map, or null on failure/miss.
async function fetchSavedControls(runId, baseCtrlStates) {
  try {
    const revRes = await fetch(`/api/risk-register/reviews?run_id=${runId}`);
    if (!revRes.ok) return null;
    const revData = await revRes.json();
    const reviews = revData.reviews || [];
    if (!reviews.length) return null;
    const statesRes = await fetch(`/api/risk-register/reviews/${reviews[0].id}`);
    if (!statesRes.ok) return null;
    const saved = (await statesRes.json()).risk_states || [];
    if (!saved.length) return null;
    const merged = { ...baseCtrlStates };
    for (const rs of saved) {
      const assigned = rs.controls_assigned || [];
      if (!assigned.length) continue;
      const refs = assigned
        .map(c => (typeof c === "string" ? c : c.ref))
        .filter(ref => ref && CTRL_BY_REF[ref]);
      if (!refs.length) continue;
      const genSet = new Set(
        assigned.filter(c => typeof c === "object" && c.generate_code).map(c => c.ref)
      );
      merged[rs.risk_ref] = { autoMapped: refs, manual: [], generateCode: genSet };
    }
    return merged;
  } catch (_) {
    return null;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Main screen
// ─────────────────────────────────────────────────────────────────────────────

function RiskRegisterReviewScreen({ risks, runId, ticker, onConverted }) {
  const [activeTab, setActiveTab] = useState("internal");

  // ── Internal register state ──────────────────────────────────────────────
  // refreshedRisks: DB-fetched copy; overrides the prop (set on mount or after convert)
  const [refreshedRisks, setRefreshedRisks] = useState(null);
  const effectiveRisks = refreshedRisks || risks;

  // effectiveRunId: resolved from the prop OR from the DB fetch so that
  // apply-wording and onConverted work even when no in-session pipeline ran.
  const [effectiveRunId, setEffectiveRunId] = useState(runId);

  const [riskStates, setRiskStates]     = useState(() => initRiskStates(risks));
  const [ctrlStates, setCtrlStates]     = useState(() => initControlStates(risks));
  const [collapsedGroups, setCollapsed] = useState({});
  const [expandedCtrl, setExpandedCtrl] = useState(new Set());
  const [aiRecsLoading, setAiRecsLoading] = useState(null);
  const [savedAt, setSavedAt]           = useState(null); // timestamp of last DB save
  // matrixCfg: loaded from DB; falls back to module-level defaults while in-flight
  const [matrixCfg, setMatrixCfg] = useState({ matrix: _DEFAULT_MATRIX_FRAMEWORKS, preset: _DEFAULT_PRESET_FRAMEWORKS, hidden: [] });
  // controlsKey: incremented after DB load to force re-renders that read MASTER_CONTROLS
  const [controlsKey, setControlsKey] = useState(0);

  // ── Discovery state ──────────────────────────────────────────────────────
  const [fwSearch, setFwSearch]           = useState("");
  const [selectedFws, setSelectedFws]     = useState([]);
  const [searching, setSearching]         = useState(false);
  const [discoveredRisks, setDiscovered]  = useState([]);
  const [discRiskStates, setDiscStates]   = useState({});
  const [discCtrlStates, setDiscCtrlStates] = useState({});
  const [discCollapsed, setDiscCollapsed] = useState({});
  const [discExpandedCtrl, setDiscExpandedCtrl] = useState(new Set());
  const [discMatrixView, setDiscMatrixView] = useState(true);

  // ── Upload Register state ─────────────────────────────────────────────
  const [uploadedRisks, setUploaded]           = useState([]);
  const [uploadRiskStates, setUploadStates]    = useState({});
  const [uploadCtrlStates, setUploadCtrlStates] = useState({});
  const [uploadCollapsed, setUploadCollapsed]  = useState({});
  const [uploadExpandedCtrl, setUploadExpandedCtrl] = useState(new Set());
  const [uploadLoading, setUploadLoading]      = useState(false);
  const [uploadErr, setUploadErr]              = useState(null);
  const [uploadedControls, setUploadedControls] = useState([]);
  const [catalogSaved, setCatalogSaved]        = useState(null);
  const [pasteMode, setPasteMode]              = useState(false);
  const [pasteText, setPasteText]              = useState("");
  const [uploadFilename, setUploadFilename]    = useState(null);

  // ── Matrix / Detail / Graph view ─────────────────────────────────────────
  const [matrixView, setMatrixView]       = useState(true);
  const [graphView,  setGraphView]        = useState(false);
  const [sankeyView, setSankeyView]       = useState(false);
  const [ctrlMatrixView, setCtrlMatrixView] = useState(false);
  const [detailFw, setDetailFw]       = useState("Enterprise Risks");
  const [savingRows, setSavingRows]   = useState(new Set());
  const [refreshing, setRefreshing]   = useState(false);
  const [assessingAll, setAssessingAll] = useState(false);

  // ── Output ───────────────────────────────────────────────────────────────
  const [outputYaml, setOutputYaml] = useState(null);
  const [converting, setConverting] = useState(false);
  const [convertErr, setConvertErr] = useState(null);
  const [saving,     setSaving]     = useState(false);
  const [validationMsg, setValidationMsg] = useState(null);
  // CaC generated alongside RaC by handleSaveAll — {controlCount, linkedRiskCount} on
  // success, {error} on failure, null before the first Save All.
  const [cacStatus, setCacStatus] = useState(null);

  // On mount: load controls library and matrix config from DB/API
  useEffect(() => {
    (async () => {
      await _loadControlsFromApi();
      await _loadPacCatalogFromApi();
      setControlsKey(k => k + 1);
    })();
    (async () => {
      const cfg = await _loadMatrixConfigFromApi();
      if (cfg?.matrix_frameworks?.length || cfg?.preset_frameworks?.length || cfg?.hidden_frameworks?.length) {
        const next = {
          matrix: cfg.matrix_frameworks || _DEFAULT_MATRIX_FRAMEWORKS,
          preset: cfg.preset_frameworks  || _DEFAULT_PRESET_FRAMEWORKS,
          hidden: cfg.hidden_frameworks  || [],
        };
        setMatrixCfg(next);
        // Keep module-level aliases in sync so utility functions stay consistent
        MATRIX_FRAMEWORKS = next.matrix;
        PRESET_FRAMEWORKS = next.preset;
      }
    })();
  }, []);

  // When the parent passes fresh pipeline risks, reset local state to track them.
  useEffect(() => {
    if (!risks?.length) return;
    setRefreshedRisks(null);
    setRiskStates(initRiskStates(risks));
    setCtrlStates(initControlStates(risks));
    setSavedAt(null);
    setEffectiveRunId(runId);
  }, [risks?.length]);

  // On mount: load previously-saved framework catalogs from the database so that
  // framework columns appear in the matrix without needing a fresh Discovery search.
  // Falls back to the built-in FW_MOCK_RISKS if the DB is unavailable or empty,
  // so the Assess All button always has preset framework risks to score.
  useEffect(() => {
    function seedFromPresets() {
      const localRisks = Object.values(FW_MOCK_RISKS).flat();
      if (localRisks.length) {
        setDiscovered(localRisks);
        setDiscStates(initRiskStates(localRisks));
        setDiscCtrlStates(initControlStates(localRisks));
      }
    }
    (async () => {
      try {
        const res = await fetch("/api/risk-register/framework-catalogs");
        if (!res.ok) { seedFromPresets(); return; }
        const data = await res.json();
        const catalogs = data.catalogs || [];
        if (!catalogs.length) { seedFromPresets(); return; }
        const allRisks = catalogs.flatMap(cat =>
          (cat.risks || []).map(r => ({
            ...r,
            source_framework: r.source_framework || cat.framework,
          }))
        );
        if (allRisks.length) {
          setDiscovered(allRisks);
          setDiscStates(initRiskStates(allRisks));
          setDiscCtrlStates(initControlStates(allRisks));
        } else {
          seedFromPresets();
        }
      } catch (_) { seedFromPresets(); }
    })();
  }, []);

  // On mount: if no in-memory risks were passed in, load from the database so the
  // Internal tab works without a pipeline having been run in this session.
  useEffect(() => {
    if (risks?.length || !ticker) return;
    (async () => {
      try {
        const res = await fetch(`/api/risk-register/risks/latest/${encodeURIComponent(ticker)}`);
        if (!res.ok) return;
        const data = await res.json();
        if (!data?.risks?.length) return;
        setRefreshedRisks(data.risks);
        setRiskStates(initRiskStates(data.risks));
        let cs = initControlStates(data.risks);
        if (data.run_id) {
          const saved = await fetchSavedControls(data.run_id, cs);
          if (saved) cs = saved;
          setEffectiveRunId(data.run_id);
        }
        setCtrlStates(cs);
      } catch (_) {}
    })();
  }, []);

  // ── Validation helpers ────────────────────────────────────────────────────

  function validateStates(states) {
    const missingReasons = [];
    for (const [key, s] of Object.entries(states)) {
      const wordingChanged = s.wording !== s.originalWording;
      if ((!s.included || wordingChanged) && !(s.reason || "").trim()) {
        missingReasons.push(key);
      }
    }
    return missingReasons;
  }

  // ── Risk state mutation helpers ────────────────────────────────────────────

  function makeStateHandlers(setStates) {
    return {
      toggleInclude: (key) => setStates(prev => ({ ...prev, [key]: { ...prev[key], included: !prev[key].included } })),
      wordingChange: (key, val) => setStates(prev => ({ ...prev, [key]: { ...prev[key], wording: val } })),
      reasonChange:  (key, val) => setStates(prev => ({ ...prev, [key]: { ...prev[key], reason: val } })),
    };
  }

  const intHandlers = makeStateHandlers(setRiskStates);
  const discHandlers = makeStateHandlers(setDiscStates);

  // ── Control state mutation helpers ────────────────────────────────────────

  function makeCtrlHandlers(setCtrl) {
    return {
      addManual: (riskKey, ctrlRef) => setCtrl(prev => {
        const s = prev[riskKey] || { autoMapped:[], manual:[], generateCode:new Set() };
        if (s.autoMapped.includes(ctrlRef) || s.manual.includes(ctrlRef)) return prev;
        return { ...prev, [riskKey]: { ...s, manual:[...s.manual, ctrlRef] } };
      }),
      remove: (riskKey, ctrlRef, isAuto) => setCtrl(prev => {
        const s = prev[riskKey];
        if (!s) return prev;
        const next = { ...s };
        if (isAuto) next.autoMapped = next.autoMapped.filter(r => r !== ctrlRef);
        else        next.manual     = next.manual.filter(r => r !== ctrlRef);
        const gen = new Set(next.generateCode);
        gen.delete(ctrlRef);
        next.generateCode = gen;
        return { ...prev, [riskKey]: next };
      }),
      toggleGen: (riskKey, ctrlRef) => setCtrl(prev => {
        const s = prev[riskKey];
        if (!s) return prev;
        const gen = new Set(s.generateCode);
        gen.has(ctrlRef) ? gen.delete(ctrlRef) : gen.add(ctrlRef);
        return { ...prev, [riskKey]: { ...s, generateCode: gen } };
      }),
      reset: (riskKey, autoMapped, manual) => setCtrl(prev => ({
        ...prev,
        [riskKey]: { ...(prev[riskKey] || { generateCode: new Set() }), autoMapped, manual },
      })),
    };
  }

  const intCtrl    = makeCtrlHandlers(setCtrlStates);
  const discCtrl   = makeCtrlHandlers(setDiscCtrlStates);
  const uploadCtrl = makeCtrlHandlers(setUploadCtrlStates);

  const uploadHandlers = makeStateHandlers(setUploadStates);

  // ── AI control recommendations ────────────────────────────────────────────

  // tab: "internal" | "external" | "upload"
  async function getAiRecs(riskKey, riskName, riskCategory, tab) {
    setAiRecsLoading(riskKey);
    try {
      const res = await fetch("/api/risk-register/controls/recommend", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ risk_wording: riskName, risk_category: riskCategory, risk_ref: riskKey }),
      });
      if (res.ok) {
        const data = await res.json();
        const refs = (data.controls || []).map(c => c.ref || c).filter(Boolean);
        const setter = tab === "external" ? setDiscCtrlStates : tab === "upload" ? setUploadCtrlStates : setCtrlStates;
        setter(prev => {
          const s = prev[riskKey] || { autoMapped:[], manual:[], generateCode:new Set() };
          const merged = [...new Set([...s.autoMapped, ...refs])].slice(0, 6);
          return { ...prev, [riskKey]: { ...s, autoMapped: merged } };
        });
      }
    } catch {}
    setAiRecsLoading(null);
  }

  // ── File upload / paste ────────────────────────────────────────────────────
  // Two ways in, one landing place. Pasting exists because the file route has
  // more ways to fail than the data does — an .xlsx needs a server-side Excel
  // engine, and a register living in an email or a wiki table has no file at
  // all — so `_loadRisks` is shared and the review screen can't tell them apart.

  // Controls the register named itself must be registered in the library
  // BEFORE initControlStates runs — it filters auto_controls through
  // CTRL_BY_REF, so a register's own refs (SOX-IT-01, ...) would be dropped on
  // the floor and silently replaced by keyword guesses.
  function _mergeRegisterControls(controls) {
    (controls || []).forEach(c => {
      if (!c?.ref || CTRL_BY_REF[c.ref]) return;
      MASTER_CONTROLS.push(c);
      CTRL_BY_REF[c.ref] = c;
    });
  }

  function _loadRisks(found, label, controls) {
    _mergeRegisterControls(controls);
    setUploadedControls(controls || []);
    setUploaded(found);
    setUploadStates(initRiskStates(found));
    setUploadCtrlStates(initControlStates(found));
    setUploadCollapsed({});
    setUploadFilename(label);
  }

  async function handleFileUpload(file) {
    if (!file) return;
    setUploadLoading(true);
    setUploadErr(null);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch("/api/risk-register/upload", { method: "POST", body: fd });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      _loadRisks(data.risks || [], file.name, data.controls);
    } catch (err) {
      setUploadErr(err.message || "Upload failed");
    }
    setUploadLoading(false);
  }

  async function handlePasteRegister() {
    if (!pasteText.trim()) { setUploadErr("Paste your register's rows first, including the header row."); return; }
    setUploadLoading(true);
    setUploadErr(null);
    try {
      const res = await fetch("/api/risk-register/paste", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: pasteText }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const found = data.risks || [];
      if (!found.length) throw new Error("No risks found — check that a name/description column is present.");
      _loadRisks(found, `pasted (${found.length} row${found.length === 1 ? "" : "s"})`, data.controls);
      setPasteText("");
      setPasteMode(false);
    } catch (err) {
      setUploadErr(err.message || "Paste failed");
    }
    setUploadLoading(false);
  }

  // ── Framework discovery ───────────────────────────────────────────────────

  function toggleFwSelection(fw) {
    setSelectedFws(prev => prev.includes(fw) ? prev.filter(f => f !== fw) : [...prev, fw]);
  }

  function localFallback(fws) {
    // Only serves known preset frameworks; unknown frameworks require the backend AI call.
    const found = [];
    for (const fw of fws) {
      if (FW_MOCK_RISKS[fw]) {
        found.push(...FW_MOCK_RISKS[fw]);
      } else {
        const partialKey = Object.keys(FW_MOCK_RISKS).find(k =>
          k.toLowerCase().includes(fw.toLowerCase()) || fw.toLowerCase().includes(k.toLowerCase())
        );
        if (partialKey) found.push(...FW_MOCK_RISKS[partialKey]);
      }
    }
    return found;
  }

  async function handleSearch() {
    const query = fwSearch.trim();
    const fwsToSearch = selectedFws.length ? selectedFws : query ? [query] : PRESET_FRAMEWORKS;
    setSearching(true);
    setDiscovered([]);
    setConvertErr(null);
    let found = [];
    try {
      const res = await fetch("/api/risk-register/framework-search", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ query: query || null, frameworks: fwsToSearch }),
      });
      if (res.ok) {
        const data = await res.json();
        found = data.risks || [];
      } else {
        found = localFallback(fwsToSearch);
      }
    } catch {
      found = localFallback(fwsToSearch);
    }
    setDiscovered(found);
    setDiscStates(initRiskStates(found));
    setDiscCtrlStates(initControlStates(found));
    setDiscCollapsed({});
    setSearching(false);
  }

  // ── Refresh ───────────────────────────────────────────────────────────────

  async function handleRefresh() {
    setActiveTab("internal");
    const url = effectiveRunId
      ? `/api/risk-register/risks/${effectiveRunId}`
      : ticker ? `/api/risk-register/risks/latest/${encodeURIComponent(ticker)}` : null;
    if (!url) return;
    setRefreshing(true);
    try {
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        const fresh = data.risks || [];
        const runId = effectiveRunId || data.run_id;
        if (fresh.length) {
          setRefreshedRisks(fresh);
          setRiskStates(initRiskStates(fresh));
          let cs = initControlStates(fresh);
          if (runId) {
            const saved = await fetchSavedControls(runId, cs);
            if (saved) cs = saved;
          }
          setCtrlStates(cs);
          if (data.run_id) setEffectiveRunId(data.run_id);
          setSavedAt(null);
        }
      }
    } catch (_) {}
    setRefreshing(false);
  }

  // ── Assess all unrated framework risks ───────────────────────────────────

  async function handleAssessAll() {
    const allRisks = [...(effectiveRisks || []), ...discoveredRisks];
    const unrated  = allRisks.filter(r => r.score == null);
    if (!unrated.length) return;
    setAssessingAll(true);
    try {
      const res = await fetch("/api/risk-register/score-framework-risks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          risks: unrated.map(r => ({
            id: r.id || r.risk_ref,
            name: r.name || r.current_wording || "",
            category: r.category || "",
            source_framework: r.source_framework || "",
          })),
        }),
      });
      if (res.ok) {
        const data = await res.json();
        const scoreMap = data.scores || {};

        // Apply scores immediately to in-memory state so the UI responds at once.
        const applyScores = r => {
          const sid = r.id || r.risk_ref;
          const s = scoreMap[sid];
          return s ? { ...r, score: s.score, rag: s.rag } : r;
        };
        setDiscovered(discoveredRisks.map(applyScores));
        const base = refreshedRisks || effectiveRisks || [];
        if (base.length) setRefreshedRisks(base.map(applyScores));

        // Re-fetch framework catalogs from DB (backend just persisted the scores).
        // Only overwrite discoveredRisks if the DB copy has scored risks so a
        // failed persist doesn't wipe out the in-memory scores applied above.
        try {
          const catRes = await fetch("/api/risk-register/framework-catalogs");
          if (catRes.ok) {
            const catData = await catRes.json();
            const catalogs = catData.catalogs || [];
            const dbRisks = catalogs.flatMap(cat =>
              (cat.risks || []).map(r => ({
                ...r,
                source_framework: r.source_framework || cat.framework,
              }))
            );
            if (dbRisks.some(r => r.score != null)) {
              setDiscovered(dbRisks);
              setDiscStates(initRiskStates(dbRisks));
              setDiscCtrlStates(initControlStates(dbRisks));
            }
          }
        } catch (_) {}

        setSavedAt(new Date().toLocaleTimeString([], { hour:"2-digit", minute:"2-digit" }));
      }
    } catch (_) {}
    setAssessingAll(false);
  }

  // ── Matrix inline save ────────────────────────────────────────────────────

  async function handleSaveRowWording(riskKey, state, ctrlRefs = []) {
    setSavingRows(prev => new Set([...prev, riskKey]));
    try {
      await fetch("/api/risk-register/reviews", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          run_id: effectiveRunId || null,
          review_type: "internal",
          framework: "Internal Risk Register",
          risk_states: [{
            risk_ref: riskKey,
            original_wording: state.originalWording || state.wording,
            current_wording: state.wording,
            included: state.included !== false,
            reason_for_change: state.reason || null,
            controls_assigned: ctrlRefs.map(ref => ({ ref, generate_code: false })),
          }],
        }),
      });
      if (effectiveRunId) {
        await fetch("/api/risk-register/apply-wording", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            run_id: effectiveRunId,
            risks: [{ risk_ref: riskKey, current_wording: state.wording }],
          }),
        });
      }
      setRiskStates(prev => ({ ...prev, [riskKey]: { ...prev[riskKey], originalWording: state.wording } }));
      setSavedAt(new Date().toLocaleTimeString([], { hour:"2-digit", minute:"2-digit" }));
      const url = effectiveRunId
        ? `/api/risk-register/risks/${effectiveRunId}`
        : ticker ? `/api/risk-register/risks/latest/${encodeURIComponent(ticker)}` : null;
      if (url) {
        const res = await fetch(url);
        if (res.ok) { const d = await res.json(); if (d.risks?.length) setRefreshedRisks(d.risks); }
      }
    } catch (_) {}
    setSavingRows(prev => { const next = new Set(prev); next.delete(riskKey); return next; });
  }

  // Discovered/external risks aren't persisted per-row the way the internal
  // register is (no risk_register_reviews row to save into yet) — they're
  // committed as a whole via "Convert to Code" in the Discovery tab's action
  // bar. This just satisfies RiskFrameworkMatrix's onSaveRow contract (used
  // by the per-cell Save button after an inline wording edit) without
  // inventing a network call that has nothing real to persist to yet.
  async function handleSaveDiscoveryRow(riskKey, state) {
    setSavingRows(prev => new Set([...prev, riskKey]));
    setDiscStates(prev => ({ ...prev, [riskKey]: { ...prev[riskKey], originalWording: state.wording } }));
    setSavingRows(prev => { const next = new Set(prev); next.delete(riskKey); return next; });
  }

  // RiskFrameworkMatrix renders internal (pipeline-run) and external/
  // discovered (uploaded register / framework catalog) risks side by side in
  // one table, but only ever had ONE onSaveRow prop, wired unconditionally to
  // handleSaveRowWording — which is internal-only. Every control add/remove
  // on an EXTERNAL row (e.g. an uploaded SOX 404 risk) still funnelled
  // through it, and its cleanup line
  //     setRiskStates(prev => ({ ...prev, [riskKey]: { ...prev[riskKey], originalWording: state.wording } }))
  // wrote into riskStates (the INTERNAL bucket) keyed by the external risk's
  // ref. Since prev[riskKey] didn't exist there, the spread produced an
  // entry containing ONLY originalWording — missing `included` and
  // `wording` entirely. On the next render, allMatrixRiskStates
  // ({ ...discRiskStates, ...riskStates }) spreads riskStates LAST, so that
  // corrupted entry silently shadowed the real (correct) one from
  // discRiskStates. The row then rendered with state.included === undefined
  // — which the opacity check reads as excluded, so the whole row grayed
  // out — and state.wording undefined too, leaving the row's own text gone
  // and nothing left to edit. This router sends external rows to the
  // already-correct handleSaveDiscoveryRow instead, so riskStates is never
  // touched by anything but genuinely internal rows.
  function matrixSaveRow(riskKey, state, ctrlRefs) {
    const isExternalRow = discoveredRisks.some(r => (r.id || r.risk_ref) === riskKey);
    return isExternalRow
      ? handleSaveDiscoveryRow(riskKey, state)
      : handleSaveRowWording(riskKey, state, ctrlRefs);
  }

  // ── Save All — upsert every risk/control/framework to DB + generate Risk-as-Code ──

  async function handleSaveAll() {
    if (!effectiveRisks?.length) return;
    setSaving(true);
    setConvertErr(null);

    const allRisks  = [...(effectiveRisks || []), ...discoveredRisks];
    const allStates = { ...discRiskStates,  ...riskStates  };
    const allCtrl   = { ...discCtrlStates,  ...ctrlStates  };
    const framework = "Internal Risk Register";

    const payload = buildConvertPayload(allRisks, allStates, allCtrl, false);

    // 1. Upsert review session — all risks with wording, include/exclude, and control assignments
    let savedReviewId = null;
    try {
      const res = await fetch("/api/risk-register/reviews", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_id: effectiveRunId || null,
          review_type: "internal",
          framework,
          risk_states: payload.map(r => ({
            risk_ref:         r.id || r.risk_ref || "",
            original_wording: r.name || "",
            current_wording:  r.current_wording || r.name || "",
            included:         r.included !== false,
            reason_for_change: r.reason_for_change || null,
            controls_assigned: r.controls_assigned || [],
          })),
        }),
      });
      if (res.ok) savedReviewId = (await res.json()).review_id || null;
    } catch (_) {}

    // 2. Write approved wording back to risk_scores
    if (effectiveRunId) {
      try {
        await fetch("/api/risk-register/apply-wording", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            run_id: effectiveRunId,
            risks: payload.map(r => ({
              risk_ref:        r.id || r.risk_ref || "",
              current_wording: r.current_wording || r.name || "",
            })),
          }),
        });
      } catch (_) {}
    }

    // 3. Generate Risk-as-Code YAML and persist it alongside the review
    let yaml = "";
    try {
      const res = await fetch("/api/risk-register/convert-to-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          risks: payload,
          review_type: "internal",
          framework,
          include_controls: true,
          review_id: savedReviewId,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      yaml = (await res.json()).yaml || "";
    } catch (_) {
      yaml = buildLocalYaml(payload, framework);
    }
    setOutputYaml(yaml);

    // 3b. Generate Controls-as-Code from this review's actual risk<->control
    // assignments (not the whole control library) — so RaC (above) and CaC
    // are generated together from the same curated review, with the
    // relationship between them captured in risk_control_mappings and
    // embedded directly in the CaC Rego's linked_risks fields.
    if (savedReviewId) {
      try {
        const cacRes = await fetch(`/api/risk-register/reviews/${savedReviewId}/generate-cac`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticker: ticker || null, run_id: effectiveRunId || null }),
        });
        if (cacRes.ok) {
          const cacData = await cacRes.json();
          setCacStatus(cacData.generated
            ? { controlCount: cacData.control_count, linkedRiskCount: cacData.linked_risk_count }
            : { error: cacData.reason === "no_controls_assigned" ? "No controls assigned yet" : "Not generated" });
        } else {
          setCacStatus({ error: `HTTP ${cacRes.status}` });
        }
      } catch (err) {
        setCacStatus({ error: err.message || "CaC generation failed" });
      }
    }

    // 4. Refresh display from DB so wording changes are reflected
    try {
      const url = effectiveRunId
        ? `/api/risk-register/risks/${effectiveRunId}`
        : ticker ? `/api/risk-register/risks/latest/${encodeURIComponent(ticker)}` : null;
      if (url) {
        const res = await fetch(url);
        if (res.ok) {
          const d = await res.json();
          if (d.risks?.length) {
            setRefreshedRisks(d.risks);
            setRiskStates(prev => {
              const next = { ...prev };
              for (const r of d.risks) {
                const key = r.id || r.risk_ref;
                if (next[key]) next[key] = { ...next[key], originalWording: next[key].wording };
              }
              return next;
            });
          }
        }
      }
    } catch (_) {}

    // 5. Land on Framework Matrix view
    setGraphView(false);
    setSankeyView(false);
    setCtrlMatrixView(false);
    setMatrixView(true);
    setActiveTab("internal");
    setSavedAt(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
    setSaving(false);
  }

  // ── Convert to Code ────────────────────────────────────────────────────────

  function buildConvertPayload(sourceRisks, states, ctrlStates, isDiscovery) {
    return (sourceRisks || []).map(r => {
      const key = r.id || r.risk_ref;
      const s = states[key] || { included:true, wording:r.name||"", reason:"" };
      const cs = ctrlStates[key] || { autoMapped:[], manual:[], generateCode:new Set() };
      const allCtrlRefs = [...(cs.autoMapped||[]), ...(cs.manual||[])];
      return {
        ...r,
        included: s.included,
        current_wording: s.wording,
        reason_for_change: s.reason || null,
        controls_assigned: allCtrlRefs.map(ref => ({
          ref,
          generate_code: cs.generateCode.has(ref),
        })),
      };
    });
  }

  // tab: "internal" | "external" | "upload"
  async function handleConvert(tab) {
    const isInternal  = tab === "internal";
    const isExternal  = tab === "external";
    const isUpload    = tab === "upload";
    const sourceRisks = isUpload ? uploadedRisks : isExternal ? discoveredRisks : (effectiveRisks || []);
    const states      = isUpload ? uploadRiskStates : isExternal ? discRiskStates : riskStates;
    const ctrl        = isUpload ? uploadCtrlStates : isExternal ? discCtrlStates : ctrlStates;
    // For an upload, the framework is whatever the REGISTER says (its
    // Framework column — "SOX 404"), not the file it arrived in. Labelling
    // the review session "SOX-matrix.xlsx" or "pasted (14 rows)" filed the
    // whole import under a name nothing else in the app knows about, so it
    // was unfindable afterwards. Falls back to the filename only when the
    // register declared no framework at all.
    const uploadFw = isUpload
      ? (sourceRisks.find(r => r.source_framework && r.source_framework !== "Uploaded Register")
          ?.source_framework || uploadFilename || "Uploaded Register")
      : null;
    const framework   = isInternal
      ? "Internal Risk Register"
      : isUpload ? uploadFw
      : (selectedFws[0] || fwSearch || "External");

    const missing = validateStates(states);
    if (missing.length) {
      setValidationMsg(`${missing.length} risk${missing.length>1?"s":""} need a reason before converting: ${missing.join(", ")}`);
      return;
    }
    setValidationMsg(null);
    setConverting(true);
    setConvertErr(null);
    setCatalogSaved(null);

    const payload = buildConvertPayload(sourceRisks, states, ctrl, !isInternal);

    // 1. Save review session (all tabs) — records wording changes, include/exclude, controls
    let savedReviewId = null;
    try {
      const reviewRes = await fetch("/api/risk-register/reviews", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          run_id: effectiveRunId || null,
          review_type: isInternal ? "internal" : "external",
          framework,
          risk_states: payload.map(r => ({
            risk_ref: r.id || r.risk_ref || "",
            original_wording: r.name || "",
            current_wording: r.current_wording || r.name || "",
            included: r.included !== false,
            reason_for_change: r.reason_for_change || null,
            controls_assigned: r.controls_assigned || [],
          })),
        }),
      });
      if (!reviewRes.ok) {
        const e = await reviewRes.json().catch(() => ({}));
        setConvertErr(`Review session not saved: ${e.detail || `HTTP ${reviewRes.status}`}`);
      } else {
        const reviewData = await reviewRes.json();
        savedReviewId = reviewData.review_id || null;
      }
    } catch (err) {
      setConvertErr(`Review session not saved: ${err.message || "network error"}`);
    }

    // 1b. Persist an uploaded/pasted register into the framework catalogs.
    //     Framework-search already did this for discovered risks; the upload
    //     path never did, so an imported register was reviewed and converted
    //     and then simply wasn't there afterwards — nothing had written it
    //     anywhere the Risk Register screen reads from.
    if (isUpload) {
      try {
        const catRes = await fetch("/api/risk-register/save-catalog", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            risks: payload.filter(r => r.included !== false).map(r => ({
              ...r,
              // Reviewer edits are the wording that should persist.
              name: r.current_wording || r.name,
            })),
            default_framework: uploadFw,
            // Persist the register's own controls too, or their refs resolve
            // to nothing after a page refresh and drop off every risk.
            controls: uploadedControls,
          }),
        });
        if (catRes.ok) {
          const catData = await catRes.json().catch(() => ({}));
          setCatalogSaved(catData.catalogs || []);
          // Re-read the catalogs so the framework tab shows the newly saved
          // register immediately, without needing a reload.
          const listRes = await fetch("/api/risk-register/framework-catalogs");
          if (listRes.ok) {
            const d = await listRes.json();
            const allRisks = (d.catalogs || []).flatMap(cat =>
              (cat.risks || []).map(r => ({
                ...r,
                source_framework: r.source_framework || cat.framework,
              }))
            );
            if (allRisks.length) {
              setDiscovered(allRisks);
              setDiscStates(initRiskStates(allRisks));
              setDiscCtrlStates(initControlStates(allRisks));
            }
          }
        } else {
          const e = await catRes.json().catch(() => ({}));
          setConvertErr(`Register not added to the framework catalog: ${e.detail || `HTTP ${catRes.status}`}`);
        }
      } catch (err) {
        setConvertErr(`Register not added to the framework catalog: ${err.message || "network error"}`);
      }
    }

    // 2. For internal runs with a known run: write wording back to risk_scores,
    //    then re-fetch the updated list so the UI reflects the DB state.
    if (isInternal && effectiveRunId) {
      try {
        const applyRes = await fetch("/api/risk-register/apply-wording", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            run_id: effectiveRunId,
            risks: payload.map(r => ({
              risk_ref: r.id || r.risk_ref || "",
              current_wording: r.current_wording || r.name || "",
            })),
          }),
        });
        if (!applyRes.ok) {
          const e = await applyRes.json().catch(() => ({}));
          setConvertErr(`Wording not saved to register: ${e.detail || `HTTP ${applyRes.status}`}`);
        } else {
          const refreshRes = await fetch(`/api/risk-register/risks/${effectiveRunId}`);
          if (refreshRes.ok) {
            const refreshData = await refreshRes.json();
            const updated = refreshData.risks || [];
            if (updated.length) {
              setRefreshedRisks(updated);
              setRiskStates(prev => {
                const next = { ...prev };
                for (const r of updated) {
                  const key = r.id || r.risk_ref;
                  if (next[key]) next[key] = { ...next[key], originalWording: next[key].wording };
                }
                return next;
              });
              setSavedAt(new Date().toLocaleTimeString([], { hour:"2-digit", minute:"2-digit" }));
            }
          }
        }
      } catch (err) {
        setConvertErr(`Wording not saved to register: ${err.message || "network error"}`);
      }
    } else if (isInternal && !effectiveRunId && savedReviewId) {
      setSavedAt(new Date().toLocaleTimeString([], { hour:"2-digit", minute:"2-digit" }));
    }

    let finalYaml = "";
    try {
      const res = await fetch("/api/risk-register/convert-to-code", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({
          risks: payload,
          review_type: isInternal ? "internal" : "external",
          framework,
          include_controls: true,
          review_id: savedReviewId,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      finalYaml = data.yaml || "";
    } catch (err) {
      finalYaml = buildLocalYaml(payload, framework);
    }
    setOutputYaml(finalYaml);

    // Refresh the main screen with the latest DB state for this run
    if (isInternal && effectiveRunId && onConverted) {
      try {
        const refreshRes = await fetch(`/api/risk-register/risks/${effectiveRunId}`);
        if (refreshRes.ok) {
          const refreshData = await refreshRes.json();
          onConverted(refreshData.risks || []);
        }
      } catch (_) {
        // non-fatal — main screen will still show prior data
      }
    }

    setConverting(false);
  }

  function buildLocalYaml(risks, framework) {
    const now = new Date().toISOString().split("T")[0];
    const included = risks.filter(r => r.included !== false);
    const excluded = risks.filter(r => r.included === false);
    const lines = [
      "# Risk Register Review — Risk-as-Code Output",
      `# Generated: ${now}  ·  ${included.length} included  ·  ${excluded.length} excluded`,
      `# Source: ${framework}`,
      "",
      "risks:",
    ];
    for (const r of included) {
      const wording = (r.current_wording||r.name||"").replace(/"/g,'\\"');
      lines.push(`  - id:               ${r.id||"—"}`);
      lines.push(`    name:             "${wording}"`);
      lines.push(`    category:         ${r.category||"—"}`);
      lines.push(`    source_framework: ${r.source_framework||framework}`);
      lines.push(`    rag:              ${r.rag||"—"}`);
      if (r.score != null) lines.push(`    score:            ${Number(r.score).toFixed(1)}`);
      if (r.reason_for_change) lines.push(`    change_reason:    "${r.reason_for_change.replace(/"/g,'\\"')}"`);
      if ((r.controls_assigned||[]).length) {
        lines.push("    controls:");
        for (const c of r.controls_assigned) {
          const ctrl = CTRL_BY_REF[c.ref];
          lines.push(`      - ref: ${c.ref}`);
          if (ctrl) lines.push(`        name: "${ctrl.name}"`);
          if (c.generate_code) lines.push(`        generate_control_as_code: true`);
        }
      }
      lines.push("");
    }
    if (excluded.length) {
      lines.push("excluded_risks:");
      for (const r of excluded) {
        lines.push(`  - id:     ${r.id||"—"}`);
        lines.push(`    name:   "${(r.current_wording||r.name||"").replace(/"/g,'\\"')}"`);
        lines.push(`    reason: "${(r.reason_for_change||"No reason provided").replace(/"/g,'\\"')}"`);
        lines.push("");
      }
    }
    return lines.join("\n").trimEnd();
  }

  function handleDownload() {
    if (!outputYaml) return;
    const blob = new Blob([outputYaml], { type:"application/x-yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `dendrai_risk-register-review_${new Date().toISOString().split("T")[0]}.yaml`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // ── Render helpers ────────────────────────────────────────────────────────

  function renderRiskList(sourceRisks, states, ctrl, collapsedG, setCollapsedG, expandedC, setExpandedC, handlers, ctrlHandlers, tab) {
    if (!sourceRisks?.length) return null;
    const groups = groupRisks(sourceRisks);

    return Object.entries(groups).map(([fw, fwRisks]) => {
      const isCollapsed = !!collapsedG[fw];
      return (
        <div key={fw} style={{ marginBottom:4 }}>
          <FrameworkGroupHeader
            framework={fw}
            risks={fwRisks}
            riskStates={states}
            collapsed={isCollapsed}
            onToggle={() => setCollapsedG(prev => ({ ...prev, [fw]: !isCollapsed }))}
          />
          {!isCollapsed && fwRisks.map(r => {
            const key = r.id || r.risk_ref;
            const s = states[key] || { included:true, wording:r.name||"", originalWording:r.name||"", reason:"" };
            const cs = ctrl[key] || { autoMapped:autoMapControls(r.name,r.category), manual:[], generateCode:new Set() };
            return (
              <RiskReviewRow
                key={key}
                risk={r}
                riskState={s}
                ctrlState={cs}
                onToggleInclude={handlers.toggleInclude}
                onWordingChange={handlers.wordingChange}
                onReasonChange={handlers.reasonChange}
                onAddManualControl={ctrlHandlers.addManual}
                onRemoveControl={ctrlHandlers.remove}
                onToggleGenerateControl={ctrlHandlers.toggleGen}
                onGetAiRecs={(k, n, c) => getAiRecs(k, n, c, tab)}
                aiRecsLoading={aiRecsLoading === key}
                expanded={expandedC.has(key)}
                onToggleExpand={(k) => setExpandedC(prev => {
                  const next = new Set(prev);
                  next.has(k) ? next.delete(k) : next.add(k);
                  return next;
                })}
              />
            );
          })}
        </div>
      );
    });
  }

  // tab: "internal" | "external" | "upload"
  function renderActionBar(sourceRisks, states, tab) {
    if (!sourceRisks?.length) return null;
    const isInternal = tab === "internal";
    const isUploadTab = tab === "upload";
    const missing = validateStates(states);
    const total = sourceRisks.length;
    const excluded = Object.values(states).filter(s => !s.included).length;
    const modified = Object.values(states).filter(s => s.wording !== s.originalWording).length;

    return (
      <div style={{
        flexShrink:0,
        background:"var(--surface,#fff)", borderTop:"1px solid var(--line,#e0e0e0)",
        padding:"12px 0", display:"flex", alignItems:"center", gap:12,
      }}>
        <div style={{ display:"flex", gap:10, fontSize:11, color:"var(--ink-2,#555)" }}>
          <span><b>{total - excluded}</b> / {total} included</span>
          {modified > 0 && <span style={{ color:"var(--amber,#c65)" }}><b>{modified}</b> wording change{modified>1?"s":""}</span>}
          {excluded > 0 && <span style={{ color:"var(--red,#e53)" }}><b>{excluded}</b> excluded</span>}
        </div>

        {missing.length > 0 && (
          <div style={{ fontSize:10, color:"var(--red,#e53)", display:"flex", alignItems:"center", gap:4 }}>
            <span>⚠</span>
            <span>{missing.length} item{missing.length>1?"s":""} missing a reason</span>
          </div>
        )}

        {validationMsg && (
          <div style={{ fontSize:10, color:"var(--red,#e53)" }}>{validationMsg}</div>
        )}

        {convertErr && (
          <div style={{ fontSize:10, color:"var(--red,#e53)", display:"flex", alignItems:"center", gap:4 }}>
            <span>⚠</span>
            <span>{convertErr}</span>
          </div>
        )}

        {isInternal && savedAt && !convertErr && (
          <div style={{ fontSize:10, color:"var(--green,#2a7)", display:"flex", alignItems:"center", gap:3 }}>
            <span>✓</span> Saved to register at {savedAt}
          </div>
        )}

        {/* The upload tab had NO success feedback of any kind: you clicked
            Convert, YAML appeared, and whether the register had actually been
            filed anywhere was invisible. Report exactly which frameworks were
            written and how many risks each now holds — that is the thing
            you'd otherwise have to query the database to find out. */}
        {isUploadTab && catalogSaved && !convertErr && (
          <div style={{ fontSize:10, color:"var(--green,#2a7)", display:"flex", alignItems:"center", gap:3 }}>
            <span>✓</span> Saved to register:{" "}
            {catalogSaved.map(c => `${c.framework} (${c.total} risk${c.total === 1 ? "" : "s"})`).join(", ")}
          </div>
        )}

        {isInternal && cacStatus && (
          cacStatus.error ? (
            <div style={{ fontSize:10, color:"var(--amber,#c65)", display:"flex", alignItems:"center", gap:3 }}
              title="Controls-as-Code generation">
              <span>⚠</span> CaC: {cacStatus.error}
            </div>
          ) : (
            <div style={{ fontSize:10, color:"var(--green,#2a7)", display:"flex", alignItems:"center", gap:3 }}
              title="Controls-as-Code generated from this review's risk<->control assignments">
              <span>✓</span> CaC: {cacStatus.controlCount} control{cacStatus.controlCount !== 1 ? "s" : ""}
              {" · "}{cacStatus.linkedRiskCount} mapping{cacStatus.linkedRiskCount !== 1 ? "s" : ""}
            </div>
          )
        )}

        <div style={{ marginLeft:"auto" }}>
          <button
            className={"btn btn-sm btn-acc" + (converting?" loading":"")}
            disabled={missing.length > 0 || converting}
            onClick={() => handleConvert(tab)}
            title={missing.length > 0 ? `Add reasons for ${missing.length} item(s) first` : "Generate Risk-as-Code YAML"}
          >
            <Icon name="spark" size={11}/>
            {converting ? " Converting…" : " Convert to Code"}
          </button>
        </div>
      </div>
    );
  }

  // ── Summary banner ────────────────────────────────────────────────────────

  function renderSummaryBanner(sourceRisks, states, hideValidation = false) {
    if (!sourceRisks?.length) return null;
    const groups = groupRisks(sourceRisks);
    const fwCount = Object.keys(groups).length;
    const total = sourceRisks.length;
    const excluded = Object.values(states).filter(s => !s.included).length;
    const missing = validateStates(states).length;
    return (
      <div style={{
        display:"flex", gap:16, padding:"8px 0 12px", fontSize:11,
        color:"var(--ink-2,#555)", borderBottom:"1px solid var(--line,#eee)", marginBottom:8,
        flexWrap:"wrap",
      }}>
        <span><b>{total}</b> risks across <b>{fwCount}</b> group{fwCount !== 1 ? "s" : ""}</span>
        {excluded > 0 && <span style={{ color:"var(--red,#e53)" }}><b>{excluded}</b> excluded</span>}
        {!hideValidation && missing > 0 && (
          <span style={{ color:"var(--red,#e53)", fontWeight:600 }}>
            ⚠ {missing} reason{missing>1?"s":""} required before converting
          </span>
        )}
        {!hideValidation && missing === 0 && total > 0 && (
          <span style={{ color:"var(--green,#2a7)", fontWeight:600 }}>✓ Ready to convert</span>
        )}
      </div>
    );
  }

  // ── Main render ───────────────────────────────────────────────────────────

  // Merge internal + discovered risks for the Framework Matrix so that imported
  // external frameworks appear as both rows and columns.
  const allMatrixRisks       = [...(effectiveRisks || []), ...discoveredRisks];
  const unratedCount         = allMatrixRisks.filter(r => r.score == null).length;
  const allMatrixRiskStates  = { ...discRiskStates,  ...riskStates  };
  const allMatrixCtrlStates  = { ...discCtrlStates,  ...ctrlStates  };
  const isDiscKey = key => discRiskStates[key] !== undefined && riskStates[key] === undefined;
  const matrixWordingChange  = (key, val)           => isDiscKey(key) ? discHandlers.wordingChange(key, val) : intHandlers.wordingChange(key, val);
  const matrixAddManual      = (key, ref)           => isDiscKey(key) ? discCtrl.addManual(key, ref)        : intCtrl.addManual(key, ref);
  const matrixRemove         = (key, ref, isAuto)   => isDiscKey(key) ? discCtrl.remove(key, ref, isAuto)   : intCtrl.remove(key, ref, isAuto);
  const matrixReset          = (key, auto, manual)  => isDiscKey(key) ? discCtrl.reset(key, auto, manual)   : intCtrl.reset(key, auto, manual);

  // Find every risk-row → control-ref relationship for controls tagged to fw
  // (both auto-mapped and manually-added), across the internal register and
  // discovery results.
  function _findFrameworkControlRefs(fw) {
    const affected = [];
    for (const [key, cs] of Object.entries(allMatrixCtrlStates)) {
      for (const ref of (cs.autoMapped || [])) if (CTRL_BY_REF[ref]?.framework === fw) affected.push({ key, ref, isAuto: true });
      for (const ref of (cs.manual || []))     if (CTRL_BY_REF[ref]?.framework === fw) affected.push({ key, ref, isAuto: false });
    }
    return affected;
  }

  // Unassign every control tagged to fw from every risk it's mapped to.
  // Control *definitions* stay in controls_library (they aren't deleted) —
  // only the risk↔control relationship is removed. Grouped by risk so each
  // row is persisted exactly once with its final control list, rather than
  // firing a save per individual ref removed. matrixRemove alone only
  // updates local React state — without the explicit save below it silently
  // reverts on the next refresh, which is exactly the "removed framework
  // comes back" bug this whole flow fixes.
  async function _unassignFrameworkControls(affected) {
    const byKey = {};
    for (const { key, ref } of affected) (byKey[key] ||= []).push(ref);

    for (const [key, refsToRemove] of Object.entries(byKey)) {
      const cs = allMatrixCtrlStates[key] || { autoMapped: [], manual: [] };
      const removeSet = new Set(refsToRemove);
      const newRefs = [...(cs.autoMapped || []), ...(cs.manual || [])].filter(r => !removeSet.has(r));
      for (const ref of refsToRemove) matrixRemove(key, ref, (cs.autoMapped || []).includes(ref));
      if (isDiscKey(key)) {
        await handleSaveDiscoveryRow(key, discRiskStates[key] || {});
      } else {
        await handleSaveRowWording(key, riskStates[key] || {}, newRefs);
      }
    }
  }

  // Persist fw as hidden so the matrix's "extra column" auto-detection
  // (driven purely by live control/risk assignments) can't recompute and
  // re-show the column on the next render or refresh.
  async function _hideFramework(fw) {
    const nextHidden = [...new Set([...(matrixCfg.hidden || []), fw])];
    setMatrixCfg(prev => ({ ...prev, hidden: nextHidden }));
    try {
      await fetch("/api/risk-register/matrix-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hidden_frameworks: nextHidden }),
      });
    } catch (_) {}
  }

  async function handleRemoveFramework(fw) {
    const affected = _findFrameworkControlRefs(fw);
    const riskCount = new Set(affected.map(a => a.key)).size;
    const msg = affected.length
      ? `Remove "${fw}" from the Framework Matrix? This will also unassign ` +
        `${affected.length} control${affected.length !== 1 ? "s" : ""} tagged to it from ` +
        `${riskCount} risk${riskCount !== 1 ? "s" : ""} — the control${affected.length !== 1 ? "s" : ""} ` +
        `will stay in the controls library, just no longer linked to those risks. This cannot be undone from this screen.`
      : `Remove "${fw}" from the Framework Matrix?`;
    if (!window.confirm(msg)) return;

    const nextMatrix = matrixCfg.matrix.filter(f => f !== fw);
    const nextPreset = matrixCfg.preset.filter(f => f !== fw);
    const nextHidden = [...new Set([...(matrixCfg.hidden || []), fw])];
    const next = { matrix: nextMatrix, preset: nextPreset, hidden: nextHidden };
    setMatrixCfg(next);
    MATRIX_FRAMEWORKS = nextMatrix;
    PRESET_FRAMEWORKS = nextPreset;
    try {
      await fetch("/api/risk-register/matrix-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ matrix_frameworks: nextMatrix, preset_frameworks: nextPreset, hidden_frameworks: nextHidden }),
      });
    } catch (_) {}

    if (affected.length) await _unassignFrameworkControls(affected);

    // Re-fetch from the DB so the matrix reflects what actually persisted,
    // not just the optimistic local update — same reasoning as the save
    // calls added to control add/remove: don't just look removed, confirm it.
    await handleRefresh();
  }

  // Removing an "extra" (not-in-config) framework column has no config to
  // edit — it only exists because a real control tagged with that framework
  // is still assigned to a risk somewhere on the register. The only way to
  // actually make the column go away is to unassign those controls, so this
  // is a real, explicit, confirmed data change — not a display toggle.
  async function handlePurgeExtraFramework(fw) {
    const affected = _findFrameworkControlRefs(fw);
    if (affected.length === 0) return;
    const riskCount = new Set(affected.map(a => a.key)).size;
    const ok = window.confirm(
      `"${fw}" isn't part of the configured Framework Matrix — it's only shown because ` +
      `${affected.length} control${affected.length !== 1 ? "s are" : " is"} still assigned to it across ` +
      `${riskCount} risk${riskCount !== 1 ? "s" : ""}. Removing this column will unassign ` +
      `${affected.length === 1 ? "that control" : "those controls"} from every risk shown here — the control` +
      `${affected.length !== 1 ? "s" : ""} will stay in the controls library, just no longer linked to those risks. ` +
      `This cannot be undone from this screen. Continue?`
    );
    if (!ok) return;

    await _unassignFrameworkControls(affected);
    await _hideFramework(fw);

    // Re-fetch from the DB so the matrix reflects what actually persisted
    // across every affected row, not just the optimistic local removals.
    await handleRefresh();
  }

  return (
    <div className="code-screen" data-screen-label="Risk and Controls Register" style={{ position:"relative" }}>
      {/* Header */}
      <div className="panel-head">
        <div>
          <div className="kicker">Execution · Risk and Controls Register</div>
          <div className="panel-title mt-8">Risk and Controls Register</div>
          <div className="panel-sub">
            Validate and curate risks before converting to Risk-as-Code.
            Edit wording, include/exclude items, assign controls, and generate output.
          </div>
        </div>
        <div className="code-actions">
          <button
            className={"btn btn-sm" + (refreshing ? " loading" : "")}
            onClick={handleRefresh}
            disabled={refreshing}
            title="Refresh internal register from database and return to Internal Register tab"
          >
            {refreshing ? "Refreshing…" : "↺ Refresh"}
          </button>
          <button
            className={"btn btn-sm" + (assessingAll ? " loading" : "")}
            onClick={handleAssessAll}
            disabled={assessingAll || unratedCount === 0}
            title={unratedCount > 0
              ? `Score ${unratedCount} unrated risk${unratedCount !== 1 ? "s" : ""} using AI risk matrix (5×5, 1–25 scale)`
              : "All risks are already rated"}
          >
            <Icon name="spark" size={11}/>
            {assessingAll ? " Assessing…" : unratedCount > 0 ? ` Assess All (${unratedCount})` : " Assess All"}
          </button>
          {effectiveRisks?.length > 0 && (
            <button
              className={"btn btn-sm btn-acc" + (saving ? " loading" : "")}
              onClick={handleSaveAll}
              disabled={saving}
              title="Upsert all frameworks, risks, and controls to the database and generate Risk-as-Code"
            >
              {saving ? "Saving…" : "Save"}
            </button>
          )}
          {outputYaml && (
            <button className="btn btn-sm" onClick={() => setOutputYaml(null)}>Hide Output</button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="rac-tabs">
        <button
          className={"rac-tab" + (activeTab === "internal" ? " active" : "")}
          onClick={() => setActiveTab("internal")}
        >
          <span className="rac-tab-badge">INT</span>
          Internal Register
          {effectiveRisks?.length > 0 && <span className="rac-tab-dot"/>}
        </button>
        <button
          className={"rac-tab" + (activeTab === "discovery" ? " active" : "")}
          onClick={() => setActiveTab("discovery")}
        >
          <span className="rac-tab-badge">EXT</span>
          Framework Discovery
          {discoveredRisks.length > 0 && <span className="rac-tab-dot"/>}
        </button>
        <button
          className={"rac-tab" + (activeTab === "upload" ? " active" : "")}
          onClick={() => setActiveTab("upload")}
        >
          <span className="rac-tab-badge">UPL</span>
          Upload Register
          {uploadedRisks.length > 0 && <span className="rac-tab-dot"/>}
        </button>
      </div>

      {/* Tab content — scrollable, no bottom padding needed since action bar is outside.
          overflowX here is a belt-and-suspenders horizontal scrollbar: the wide tables
          (RiskFrameworkMatrix, ControlCoverageMatrix — one column per framework, can
          run wider than the viewport) already wrap themselves in their own
          overflowX:auto div, but this flex column's cross-axis has no width
          constraint of its own, so without this the wide table could push the
          whole screen wider instead of scrolling in place. */}
      <div style={{ flex:1, minWidth:0, overflowY:"auto", overflowX:"auto" }}>

        {/* ── Internal Register tab ── */}
        {activeTab === "internal" && (
          <div>
            {!effectiveRisks?.length ? (
              <Empty style={{ padding:48 }}>
                Run the pipeline to Stage 2 to load the internal risk register, then return here to review and curate before converting to code.
              </Empty>
            ) : (
              <>
                {renderSummaryBanner(effectiveRisks, riskStates, matrixView || graphView || sankeyView || ctrlMatrixView)}

                {/* View toggle */}
                <div style={{ display:"flex", gap:6, marginBottom:12, paddingBottom:10, borderBottom:"1px solid var(--line,#eee)" }}>
                  {[["matrix","Framework Matrix"],["detail","Detail"],["graph","Risk Graph"],["sankey","Control Sankey"],["controls","Controls"]].map(([v, label]) => {
                    const active = v === "graph"    ? graphView
                                 : v === "sankey"   ? sankeyView
                                 : v === "controls" ? ctrlMatrixView
                                 : !graphView && !sankeyView && !ctrlMatrixView && (v === "matrix" ? matrixView : !matrixView);
                    return (
                      <button
                        key={v}
                        onClick={() => {
                          if (v === "graph")       { setGraphView(true); setSankeyView(false); setCtrlMatrixView(false); }
                          else if (v === "sankey") { setSankeyView(true); setGraphView(false); setCtrlMatrixView(false); }
                          else if (v === "controls") { setCtrlMatrixView(true); setGraphView(false); setSankeyView(false); }
                          else { setGraphView(false); setSankeyView(false); setCtrlMatrixView(false); setMatrixView(v === "matrix"); }
                        }}
                        style={{
                          fontSize:10, padding:"3px 10px", borderRadius:4, cursor:"pointer",
                          border: active ? "1px solid var(--acc,#2563eb)" : "1px solid var(--line,#ddd)",
                          background: active ? "var(--acc,#2563eb)" : "transparent",
                          color: active ? "#fff" : "var(--ink-2,#555)", fontWeight: active ? 600 : 400,
                        }}
                      >{label}</button>
                    );
                  })}
                </div>

                {sankeyView ? (
                  <RiskSankey />
                ) : graphView ? (
                  <RiskGraphViz risks={allMatrixRisks} ticker={ticker} runId={runId} ctrlStates={ctrlStates} />
                ) : ctrlMatrixView ? (
                  <ControlCoverageMatrix ctrlStates={allMatrixCtrlStates} />
                ) : matrixView ? (
                  <RiskFrameworkMatrix
                    risks={allMatrixRisks}
                    riskStates={allMatrixRiskStates}
                    ctrlStates={allMatrixCtrlStates}
                    matrixFrameworks={matrixCfg.matrix}
                    hiddenFrameworks={matrixCfg.hidden}
                    onWordingChange={matrixWordingChange}
                    onAddManualControl={matrixAddManual}
                    onRemoveControl={matrixRemove}
                    onResetCtrl={matrixReset}
                    onSaveRow={matrixSaveRow}
                    onRemoveFramework={handleRemoveFramework}
                    onPurgeExtraFramework={handlePurgeExtraFramework}
                    savingRows={savingRows}
                    savedAt={savedAt}
                    runId={effectiveRunId}
                  />
                ) : (
                  (() => {
                    const fwOptions = ["Enterprise Risks",
                      ...Array.from(new Set(discoveredRisks.map(r => r.source_framework).filter(Boolean))).sort()
                    ];
                    const detailRisks = detailFw === "Enterprise Risks"
                      ? effectiveRisks
                      : discoveredRisks.filter(r => r.source_framework === detailFw);
                    const detailStates   = detailFw === "Enterprise Risks" ? riskStates    : discRiskStates;
                    const detailCtrl     = detailFw === "Enterprise Risks" ? ctrlStates    : discCtrlStates;
                    const detailCollapsed= detailFw === "Enterprise Risks" ? collapsedGroups : discCollapsed;
                    const setDetailCollapsed = detailFw === "Enterprise Risks" ? setCollapsed : setDiscCollapsed;
                    const detailExpanded = detailFw === "Enterprise Risks" ? expandedCtrl  : discExpandedCtrl;
                    const setDetailExpanded  = detailFw === "Enterprise Risks" ? setExpandedCtrl : setDiscExpandedCtrl;
                    const detailHandlers = detailFw === "Enterprise Risks" ? intHandlers   : discHandlers;
                    const detailCtrlH    = detailFw === "Enterprise Risks" ? intCtrl       : discCtrl;
                    const detailTab      = detailFw === "Enterprise Risks" ? "internal"    : "external";
                    return (
                      <>
                        <div style={{ display:"flex", alignItems:"center", gap:8, marginBottom:12 }}>
                          <label style={{ fontSize:10, fontWeight:600, color:"var(--ink-2,#555)", whiteSpace:"nowrap" }}>
                            Framework
                          </label>
                          <select
                            value={detailFw}
                            onChange={e => setDetailFw(e.target.value)}
                            style={{
                              fontSize:11, padding:"4px 8px", borderRadius:4,
                              border:"1px solid var(--line,#ddd)", background:"var(--surface,#fff)",
                              color:"var(--ink,#111)", cursor:"pointer", flex:1,
                            }}
                          >
                            {fwOptions.map(fw => (
                              <option key={fw} value={fw}>{fw}</option>
                            ))}
                          </select>
                        </div>
                        {renderRiskList(detailRisks, detailStates, detailCtrl, detailCollapsed, setDetailCollapsed, detailExpanded, setDetailExpanded, detailHandlers, detailCtrlH, detailTab)}
                      </>
                    );
                  })()
                )}
              </>
            )}
          </div>
        )}

        {/* ── Framework Discovery tab ── */}
        {activeTab === "discovery" && (
          <div>
            {/* Search inputs */}
            <div style={{ padding:"12px 0 16px", borderBottom:"1px solid var(--line,#eee)", marginBottom:12 }}>
              <div style={{ fontSize:11, fontWeight:600, color:"var(--ink,#111)", marginBottom:8 }}>
                Ingest an external framework's risk catalog
              </div>

              {/* Preset framework chips */}
              <div style={{ display:"flex", flexWrap:"wrap", gap:6, marginBottom:10 }}>
                {matrixCfg.preset.map(fw => {
                  const sel = selectedFws.includes(fw);
                  return (
                    <button
                      key={fw}
                      onClick={() => toggleFwSelection(fw)}
                      style={{
                        fontSize:10, padding:"3px 10px", borderRadius:12,
                        border: sel ? "1px solid var(--acc,#2563eb)" : "1px solid var(--line,#ddd)",
                        background: sel ? "var(--acc,#2563eb)" : "var(--surface,#fff)",
                        color: sel ? "#fff" : "var(--ink-2,#555)",
                        cursor:"pointer", fontWeight: sel ? 600 : 400,
                        transition:"all 0.1s",
                      }}
                    >
                      {fw}
                    </button>
                  );
                })}
              </div>

              {/* Free-text field */}
              <div style={{ display:"flex", gap:8, alignItems:"center" }}>
                <input
                  className="dendrai-input"
                  placeholder="Or enter a custom framework name…"
                  value={fwSearch}
                  onChange={e => setFwSearch(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && handleSearch()}
                  style={{ flex:1, fontSize:11 }}
                />
                <button
                  className={"btn btn-sm btn-acc" + (searching ? " loading" : "")}
                  onClick={handleSearch}
                  disabled={searching}
                >
                  <Icon name="spark" size={11}/>
                  {searching ? " Searching…" : " Search"}
                </button>
              </div>

              {selectedFws.length === 0 && !fwSearch.trim() && (
                <div style={{ marginTop:6, fontSize:10, color:"var(--ink-3,#888)" }}>
                  Select frameworks above or enter a custom name, then click Search to fetch the risk catalog.
                  When no selection is made, all preset frameworks are searched.
                </div>
              )}
            </div>

            {/* Discovery results */}
            {searching && (
              <div style={{ padding:24, textAlign:"center", color:"var(--ink-3,#888)", fontSize:11 }}>
                Fetching framework risk catalog…
              </div>
            )}

            {!searching && discoveredRisks.length === 0 && (
              <Empty style={{ padding:32 }}>
                {(selectedFws.length > 0 || fwSearch.trim())
                  ? "No risks returned. The backend may need an ANTHROPIC_API_KEY to generate catalogs for custom frameworks."
                  : "Select one or more frameworks above or enter a custom name, then click Search."}
              </Empty>
            )}

            {!searching && discoveredRisks.length > 0 && (
              <>
                {renderSummaryBanner(discoveredRisks, discRiskStates, discMatrixView)}

                {/* View toggle — same as Internal Register's, scoped to this search's
                    results. Matrix defaults on so a search immediately produces a real
                    risk & control matrix for the searched framework(s), not just a list. */}
                <div style={{ display:"flex", gap:6, marginBottom:12, paddingBottom:10, borderBottom:"1px solid var(--line,#eee)" }}>
                  {[["matrix","Risk & Control Matrix"],["detail","Detail"]].map(([v, label]) => (
                    <button
                      key={v}
                      onClick={() => setDiscMatrixView(v === "matrix")}
                      style={{
                        fontSize:10, padding:"3px 10px", borderRadius:4, cursor:"pointer",
                        border: (v === "matrix") === discMatrixView ? "1px solid var(--acc,#2563eb)" : "1px solid var(--line,#ddd)",
                        background: (v === "matrix") === discMatrixView ? "var(--acc,#2563eb)" : "transparent",
                        color: (v === "matrix") === discMatrixView ? "#fff" : "var(--ink-2,#555)",
                        fontWeight: (v === "matrix") === discMatrixView ? 600 : 400,
                      }}
                    >{label}</button>
                  ))}
                </div>

                {discMatrixView ? (
                  <RiskFrameworkMatrix
                    risks={discoveredRisks}
                    riskStates={discRiskStates}
                    ctrlStates={discCtrlStates}
                    matrixFrameworks={[...new Set(discoveredRisks.map(r => r.source_framework).filter(Boolean))].sort()}
                    onWordingChange={discHandlers.wordingChange}
                    onAddManualControl={discCtrl.addManual}
                    onRemoveControl={discCtrl.remove}
                    onResetCtrl={discCtrl.reset}
                    onSaveRow={handleSaveDiscoveryRow}
                    savingRows={savingRows}
                  />
                ) : (
                  renderRiskList(
                    discoveredRisks, discRiskStates, discCtrlStates,
                    discCollapsed, setDiscCollapsed,
                    discExpandedCtrl, setDiscExpandedCtrl,
                    discHandlers, discCtrl, "external"
                  )
                )}
              </>
            )}
          </div>
        )}

        {/* ── Upload Register tab ── */}
        {activeTab === "upload" && (
          <div>
            {/* Drop zone */}
            <div style={{ padding:"12px 0 16px", borderBottom:"1px solid var(--line,#eee)", marginBottom:12 }}>
              <div style={{ fontSize:11, fontWeight:600, color:"var(--ink,#111)", marginBottom:8 }}>
                Import a risk register from Excel or CSV
              </div>
              <label
                htmlFor="risk-upload-input"
                onDragOver={e => { e.preventDefault(); e.currentTarget.style.background = "var(--acc-bg,#e8f0fe)"; }}
                onDragLeave={e => { e.currentTarget.style.background = "transparent"; }}
                onDrop={e => {
                  e.preventDefault();
                  e.currentTarget.style.background = "transparent";
                  const f = e.dataTransfer.files?.[0];
                  if (f) handleFileUpload(f);
                }}
                style={{
                  display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center",
                  gap:8, padding:"28px 20px", borderRadius:8, cursor:"pointer",
                  border:"2px dashed var(--line,#ddd)", background:"transparent",
                  transition:"background 0.15s",
                }}
              >
                <span style={{ fontSize:22, color:"var(--ink-3,#aaa)" }}>↑</span>
                <span style={{ fontSize:11, color:"var(--ink-2,#555)", textAlign:"center" }}>
                  Drop an Excel or CSV file here, or{" "}
                  <span style={{ color:"var(--acc,#2563eb)", textDecoration:"underline" }}>browse</span>
                </span>
                <span style={{ fontSize:10, color:"var(--ink-3,#888)" }}>
                  .xlsx, .xls, .csv · Needs a risk description column; ID, Category, Framework,
                  Control, Score and RAG are all optional
                </span>
                <input
                  id="risk-upload-input"
                  type="file"
                  accept=".csv,.xlsx,.xls"
                  style={{ display:"none" }}
                  onChange={e => { const f = e.target.files?.[0]; if (f) handleFileUpload(f); e.target.value = ""; }}
                />
              </label>

              {/* Paste path — copy rows straight out of Excel/Sheets. Needs no
                  file and no server-side Excel engine, so it also works when
                  the .xlsx route can't. */}
              <div style={{ marginTop:10, textAlign:"center" }}>
                <button
                  onClick={() => { setPasteMode(v => !v); setUploadErr(null); }}
                  style={{
                    fontSize:11, padding:"4px 10px", borderRadius:6, cursor:"pointer",
                    border:"1px solid var(--line,#ddd)", background:"transparent",
                    color:"var(--ink-2,#555)",
                  }}
                >
                  {pasteMode ? "✕ Cancel paste" : "⌘ or paste rows instead"}
                </button>
              </div>

              {pasteMode && (
                <div style={{ marginTop:10, display:"flex", flexDirection:"column", gap:6 }}>
                  <textarea
                    value={pasteText}
                    onChange={e => setPasteText(e.target.value)}
                    onPaste={e => {
                      // Excel/Sheets put a tab-separated table on the clipboard;
                      // let it land as-is — the server sniffs tab vs comma.
                      const t = e.clipboardData?.getData("text/plain");
                      if (t && !pasteText.trim()) { e.preventDefault(); setPasteText(t); }
                    }}
                    spellCheck={false}
                    placeholder={"Paste rows copied from Excel or Google Sheets, including the header row.\n\nRisk ID\tRisk Name\tCategory\tScore\tRAG\nR-001\tSupplier fraud\tFinancial\t8.2\tRed\n\nComma-separated works too."}
                    style={{
                      width:"100%", minHeight:130, resize:"vertical", padding:"8px 10px",
                      fontSize:11, lineHeight:1.6, fontFamily:"var(--mono, monospace)",
                      borderRadius:6, border:"1px solid var(--line,#ddd)",
                      background:"var(--surface, #fff)", color:"var(--ink,#111)",
                    }}
                  />
                  <div style={{ display:"flex", alignItems:"center", gap:8 }}>
                    <button
                      onClick={handlePasteRegister}
                      disabled={uploadLoading || !pasteText.trim()}
                      style={{
                        fontSize:11, padding:"5px 12px", borderRadius:6,
                        cursor: uploadLoading || !pasteText.trim() ? "not-allowed" : "pointer",
                        border:"1px solid var(--acc,#2563eb)",
                        background: uploadLoading || !pasteText.trim() ? "transparent" : "var(--acc,#2563eb)",
                        color: uploadLoading || !pasteText.trim() ? "var(--ink-3,#888)" : "#fff",
                        opacity: uploadLoading || !pasteText.trim() ? 0.6 : 1,
                      }}
                    >
                      {uploadLoading ? "Parsing…" : "Import pasted rows"}
                    </button>
                    {pasteText.trim() && (
                      <span style={{ fontSize:10, color:"var(--ink-3,#888)" }}>
                        {pasteText.trim().split("\n").length - 1} data row
                        {pasteText.trim().split("\n").length - 1 === 1 ? "" : "s"} detected
                      </span>
                    )}
                  </div>
                </div>
              )}

              {uploadLoading && !pasteMode && (
                <div style={{ marginTop:10, fontSize:11, color:"var(--ink-3,#888)", textAlign:"center" }}>
                  Parsing file…
                </div>
              )}
              {uploadErr && (
                <div style={{ marginTop:10, fontSize:11, color:"var(--red,#e53)", padding:"6px 10px", background:"rgba(229,85,51,0.08)", borderRadius:4 }}>
                  ⚠ {uploadErr}
                </div>
              )}
              {uploadFilename && uploadedRisks.length > 0 && (
                <div style={{ marginTop:10, fontSize:10, color:"var(--green,#2a7)", display:"flex", alignItems:"center", gap:4 }}>
                  <span>✓</span> Loaded {uploadedRisks.length} risk{uploadedRisks.length !== 1 ? "s" : ""} from{" "}
                  <span className="mono">{uploadFilename}</span>
                  <button
                    onClick={() => { setUploaded([]); setUploadFilename(null); setUploadErr(null); }}
                    style={{ marginLeft:4, fontSize:10, padding:"0 5px", borderRadius:3, border:"1px solid var(--line,#ddd)", background:"transparent", color:"var(--ink-3,#888)", cursor:"pointer" }}
                  >✕ Clear</button>
                </div>
              )}
            </div>

            {!uploadLoading && uploadedRisks.length === 0 && (
              <Empty style={{ padding:32 }}>
                Upload an Excel or CSV file above — or paste rows straight from your spreadsheet — to import
                risks into the review and convert-to-code workflow.
              </Empty>
            )}
            {!uploadLoading && uploadedRisks.length > 0 && (
              <>
                {renderSummaryBanner(uploadedRisks, uploadRiskStates)}
                {renderRiskList(
                  uploadedRisks, uploadRiskStates, uploadCtrlStates,
                  uploadCollapsed, setUploadCollapsed,
                  uploadExpandedCtrl, setUploadExpandedCtrl,
                  uploadHandlers, uploadCtrl, "upload"
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* Action bar — outside scroll area so it never overlaps list content */}
      {activeTab === "internal" && !matrixView && !graphView && !sankeyView && !ctrlMatrixView && (() => {
        const detailRisks  = detailFw === "Enterprise Risks" ? effectiveRisks : discoveredRisks.filter(r => r.source_framework === detailFw);
        const detailStates = detailFw === "Enterprise Risks" ? riskStates : discRiskStates;
        const detailTab    = detailFw === "Enterprise Risks" ? "internal" : "external";
        return renderActionBar(detailRisks, detailStates, detailTab);
      })()}
      {activeTab === "discovery" && renderActionBar(discoveredRisks, discRiskStates, "external")}
      {activeTab === "upload"    && renderActionBar(uploadedRisks, uploadRiskStates, "upload")}

      {/* Output panel */}
      {outputYaml && (
        <OutputPanel
          yaml={outputYaml}
          onClose={() => setOutputYaml(null)}
          onDownload={handleDownload}
        />
      )}
    </div>
  );
}

Object.assign(window, { RiskRegisterReviewScreen, FW_MOCK_RISKS });
