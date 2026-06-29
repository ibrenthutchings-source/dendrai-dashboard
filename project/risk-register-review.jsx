/* ============================================================
   Risk Register Review — Phases 2, 3, 4
   Internal register management + external framework ingestion +
   unified risk-to-control mapping, all in one screen.
   ============================================================ */

// ─────────────────────────────────────────────────────────────────────────────
// Static data: control library + framework catalogs
// ─────────────────────────────────────────────────────────────────────────────

const MASTER_CONTROLS = [
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
const CTRL_BY_REF = Object.fromEntries(MASTER_CONTROLS.map(c => [c.ref, c]));

const PRESET_FRAMEWORKS = [
  "NIST SP 800-53",
  "ISO/IEC 27001",
  "ISO/IEC 42001",
  "CIS Controls",
  "SOC 2",
];

const MATRIX_FRAMEWORKS = ["ISO/IEC 27001", "ISO/IEC 42001", "SOC 2", "NIST SP 800-53", "CIS Controls", "COSO ERM"];

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
  for (const r of (risks || [])) {
    const key = r.id || r.risk_ref;
    const wording = r.current_wording || r.name || "";
    states[key] = {
      included: true,
      wording,
      originalWording: wording,
      reason: "",
    };
  }
  return states;
}

function initControlStates(risks) {
  const states = {};
  for (const r of (risks || [])) {
    const key = r.id || r.risk_ref;
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
  }
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
  return (
    <div style={{ display:"flex", alignItems:"center", gap:4, padding:"2px 6px", borderRadius:4, background:"var(--surface-2,#f5f5f5)", border:"1px solid var(--line,#e0e0e0)", fontSize:10 }}>
      <span className="mono" style={{ fontWeight:600, color:"var(--ink-2,#555)" }}>{ctrlRef}</span>
      {ctrl && <span style={{ color:"var(--ink-3,#888)" }}>{ctrl.name}</span>}
      {isAuto && <span style={{ fontSize:9, color:"var(--ink-3,#888)", fontStyle:"italic" }}>auto</span>}
      <button
        title={generateCode ? "Remove from code generation" : "Include in code generation"}
        onClick={() => onToggleGenerate(ctrlRef)}
        style={{ marginLeft:2, fontSize:9, padding:"0 3px", borderRadius:3, border:"1px solid var(--line,#e0e0e0)", background: generateCode ? "var(--acc,#2563eb)" : "transparent", color: generateCode ? "#fff" : "var(--ink-3,#888)", cursor:"pointer", lineHeight:"14px" }}
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

function ControlsPanel({ riskKey, riskName, riskCategory, ctrlState, onAddManual, onRemove, onToggleGenerate, onGetAiRecs, aiRecsLoading }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerSearch, setPickerSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [newCtrl, setNewCtrl] = useState({ ref: "", name: "", framework: "", desc: "" });
  const [createErr, setCreateErr] = useState("");

  function handleCreateControl() {
    const ref = newCtrl.ref.trim().toUpperCase();
    if (!ref) { setCreateErr("Control reference is required."); return; }
    if (CTRL_BY_REF[ref]) { setCreateErr(`${ref} already exists in the control library.`); return; }
    if (!/^[A-Za-z]/.test(ref)) { setCreateErr("Reference must start with a letter."); return; }
    if (!newCtrl.name.trim()) { setCreateErr("Control name is required."); return; }
    const ctrl = {
      ref,
      framework: newCtrl.framework.trim() || "Custom",
      name: newCtrl.name.trim(),
      category: "Custom",
      domain: "Custom",
      desc: newCtrl.desc.trim(),
    };
    MASTER_CONTROLS.push(ctrl);
    CTRL_BY_REF[ref] = ctrl;
    onAddManual(riskKey, ref);
    setCreateOpen(false);
    setNewCtrl({ ref: "", name: "", framework: "", desc: "" });
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
            onClick={() => { setCreateOpen(p => !p); setPickerOpen(false); setCreateErr(""); }}
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
            {filteredLibrary.slice(0,20).map(c => (
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
          </div>
        </div>
      )}

      {/* New control creation form */}
      {createOpen && (
        <div style={{ marginTop:8, padding:10, background:"var(--surface,#fff)", border:"1px solid var(--acc,#2563eb)", borderRadius:6, display:"flex", flexDirection:"column", gap:7 }}>
          <div style={{ fontSize:10, fontWeight:700, color:"var(--ink,#111)" }}>Create new control</div>
          <div style={{ display:"flex", gap:6 }}>
            <div style={{ flex:"0 0 86px" }}>
              <label style={{ fontSize:9, fontWeight:600, color:"var(--ink-2,#555)", display:"block", marginBottom:2 }}>Ref *</label>
              <input
                className="dendrai-input"
                placeholder="e.g. AC-06"
                value={newCtrl.ref}
                onChange={e => setNewCtrl(p => ({ ...p, ref: e.target.value }))}
                style={{ fontSize:10, padding:"3px 6px", width:"100%", boxSizing:"border-box" }}
                autoFocus
              />
            </div>
            <div style={{ flex:1 }}>
              <label style={{ fontSize:9, fontWeight:600, color:"var(--ink-2,#555)", display:"block", marginBottom:2 }}>Framework</label>
              <input
                className="dendrai-input"
                placeholder="e.g. NIST SP 800-53"
                value={newCtrl.framework}
                onChange={e => setNewCtrl(p => ({ ...p, framework: e.target.value }))}
                style={{ fontSize:10, padding:"3px 6px", width:"100%", boxSizing:"border-box" }}
              />
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
  const needsReason = (!riskState.included || wordingChanged) && !riskState.reason.trim();
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
        <span style={{ fontWeight:600, fontSize:12 }}>Risk Register Review — Code Output</span>
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

function RiskFrameworkMatrix({ risks, riskStates, ctrlStates, onWordingChange, onAddManualControl, onRemoveControl, onResetCtrl, onSaveRow, savingRows, savedAt }) {
  // Row-level wording edit state
  const [editingRows, setEditingRows] = useState(new Set());
  const [rowDrafts, setRowDrafts]     = useState({});
  // Per-cell expand state: "riskKey:fw"
  const [expandedCells, setExpandedCells] = useState(new Set());
  const [savingCells, setSavingCells]     = useState(new Set());
  const [fwPicker, setFwPicker]       = useState(null); // { key, fw }
  const [ctrlSearch, setCtrlSearch]   = useState("");
  const [domainNames, setDomainNames] = useState({});
  const [domainsLoading, setDomainsLoading] = useState(false);

  function inferDomain(risk) {
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
    const baseline = {};
    for (const r of risks) { baseline[r.id || r.risk_ref] = inferDomain(r); }
    setDomainNames(baseline);
    (async () => {
      setDomainsLoading(true);
      try {
        const res = await fetch("/api/risk-register/categorize-domains", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            risks: risks.map(r => ({
              ref: r.id || r.risk_ref,
              name: r.name || r.current_wording || "",
              category: r.category || "",
            })),
          }),
        });
        if (res.ok) {
          const data = await res.json();
          if (data.domains) setDomainNames(prev => ({ ...prev, ...data.domains }));
        }
      } catch (_) {}
      setDomainsLoading(false);
    })();
  }, [risks?.length]);

  // Determine which external frameworks to show as columns:
  // 1. Any framework referenced by an assigned control
  // 2. Any source_framework on a risk that isn't the internal register
  const usedFws = new Set();
  for (const cs of Object.values(ctrlStates)) {
    for (const ref of [...(cs.autoMapped || []), ...(cs.manual || [])]) {
      const fw = CTRL_BY_REF[ref]?.framework;
      if (fw && fw !== "Internal") usedFws.add(fw);
    }
  }
  for (const r of (risks || [])) {
    const fw = r.source_framework;
    if (fw && fw !== "Internal Risk Register" && fw !== "Internal") usedFws.add(fw);
  }
  // Preserve standard order for known frameworks; append any extras alphabetically
  const _internalFws = new Set(["Internal", "Internal Risk Register"]);
  const fwCols = [
    ...MATRIX_FRAMEWORKS.filter(fw => usedFws.has(fw)),
    ...[...usedFws].filter(fw => !MATRIX_FRAMEWORKS.includes(fw) && !_internalFws.has(fw)).sort(),
  ];

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

  async function saveRow(key) {
    const state   = riskStates[key] || {};
    const cs      = ctrlStates[key] || { autoMapped: [], manual: [] };
    const allRefs = [...(cs.autoMapped || []), ...(cs.manual || [])];
    await onSaveRow(key, state, allRefs);
    setEditingRows(prev => { const next = new Set(prev); next.delete(key); return next; });
  }

  // ── Cell expand helpers ──────────────────────────────────────────────────

  function toggleCell(key, fw) {
    const id = `${key}:${fw}`;
    setExpandedCells(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
    // Close any open picker when collapsing or switching
    if (fwPicker && !(fwPicker.key === key && fwPicker.fw === fw)) {
      setFwPicker(null);
      setCtrlSearch("");
    }
  }

  async function saveCellControls(key, fw) {
    const cellId  = `${key}:${fw}`;
    setSavingCells(prev => new Set([...prev, cellId]));
    const state   = riskStates[key] || {};
    const cs      = ctrlStates[key] || { autoMapped: [], manual: [] };
    const allRefs = [...(cs.autoMapped || []), ...(cs.manual || [])];
    await onSaveRow(key, state, allRefs);
    setSavingCells(prev => { const next = new Set(prev); next.delete(cellId); return next; });
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

  return (
    <div>
      {savedAt && (
        <div style={{ fontSize: 10, color: "var(--green,#2a7)", padding: "4px 0 10px", display: "flex", alignItems: "center", gap: 4 }}>
          <span>✓</span> Saved at {savedAt}
        </div>
      )}

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead>
            <tr>
              <th style={{ ...thStyle, width: 160, minWidth: 140 }}>
                Core Domains &amp; Risks
                {domainsLoading && (
                  <span style={{ fontSize: 8, fontWeight: 400, color: "var(--ink-3,#aaa)", marginLeft: 5 }}>generating…</span>
                )}
              </th>
              <th style={{ ...thStyle, minWidth: 200, width: "26%" }}>Enterprise Risks</th>
              {fwCols.map(fw => (
                <th key={fw} style={{ ...thStyle, minWidth: 200 }}>{fw}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(risks || []).map((r, idx) => {
              const key      = r.id || r.risk_ref;
              const state    = riskStates[key] || { wording: r.current_wording || r.name || "", included: true };
              const cs       = ctrlStates[key] || { autoMapped: autoMapControls(r.name, r.category), manual: [], generateCode: new Set() };
              const allRefs  = [...(cs.autoMapped || []), ...(cs.manual || [])];
              const isEditing = editingRows.has(key);
              const isSaving  = savingRows.has(key);
              const domain    = domainNames[key] || inferDomain(r);
              const prevRisk  = idx > 0 ? risks[idx - 1] : null;
              const prevDomain = prevRisk ? (domainNames[prevRisk.id || prevRisk.risk_ref] || inferDomain(prevRisk)) : null;
              const isGroupStart = domain !== prevDomain;

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
                      <div style={{ fontWeight: 700, fontSize: 10, color: "var(--acc,#2563eb)", lineHeight: 1.4 }}>
                        {domain}
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
                          <button className="btn btn-sm btn-acc" onClick={() => saveRow(key)} disabled={isSaving} style={{ fontSize: 9, padding: "2px 9px" }}>
                            {isSaving ? "Saving…" : "Save"}
                          </button>
                          <button className="btn btn-sm" onClick={() => cancelEdit(key)} disabled={isSaving} style={{ fontSize: 9, padding: "2px 9px" }}>
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

                  {/* Framework columns — independently expandable */}
                  {fwCols.map(fw => {
                    const fwRefs     = allRefs.filter(ref => CTRL_BY_REF[ref]?.framework === fw);
                    const cellId     = `${key}:${fw}`;
                    const isExpanded = expandedCells.has(cellId);
                    const isSavingCell = savingCells.has(cellId);
                    const pickerOpen = fwPicker?.key === key && fwPicker?.fw === fw;
                    const addable    = MASTER_CONTROLS.filter(c =>
                      c.framework === fw &&
                      !allRefs.includes(c.ref) &&
                      (ctrlSearch === "" ||
                        c.name.toLowerCase().includes(ctrlSearch.toLowerCase()) ||
                        c.ref.toLowerCase().includes(ctrlSearch.toLowerCase()))
                    );

                    return (
                      <td key={fw} style={{ ...tdStyle, padding: 0 }}>
                        {/* Compact header — always visible, click to expand */}
                        <button
                          onClick={() => toggleCell(key, fw)}
                          style={{
                            width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
                            padding: "10px 10px", border: "none", cursor: "pointer", textAlign: "left",
                            background: isExpanded ? "var(--acc-bg,#eff6ff)" : "transparent",
                            borderBottom: isExpanded ? "1px solid var(--line,#dce8fd)" : "none",
                            transition: "background 0.1s",
                          }}
                        >
                          <span style={{ fontSize: 10, flex: 1, minWidth: 0 }}>
                            {fwRefs.length > 0 ? (
                              <>
                                <span style={{ fontWeight: 600, color: "var(--acc,#2563eb)" }}>
                                  {fwRefs.slice(0, 2).join(", ")}
                                </span>
                                {fwRefs.length > 2 && (
                                  <span style={{ color: "var(--ink-3,#888)" }}> +{fwRefs.length - 2} more</span>
                                )}
                              </>
                            ) : (
                              <span style={{ color: "var(--ink-3,#bbb)" }}>No controls</span>
                            )}
                          </span>
                          <span style={{ fontSize: 9, color: "var(--acc,#2563eb)", flexShrink: 0, marginLeft: 6, fontWeight: 600 }}>
                            {isExpanded ? "▲" : "▼"}
                          </span>
                        </button>

                        {/* Expanded panel */}
                        {isExpanded && (
                          <div style={{ padding: "10px 10px 12px" }}>
                            {/* Control list */}
                            {fwRefs.length > 0 ? (
                              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 10 }}>
                                {fwRefs.map(ref => {
                                  const ctrl = CTRL_BY_REF[ref];
                                  return (
                                    <div key={ref} style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
                                      <div style={{ flex: 1 }}>
                                        <div style={{ fontWeight: 700, fontSize: 10, color: "var(--acc,#2563eb)", marginBottom: 2 }}>
                                          {ref}: {ctrl?.name}
                                        </div>
                                        {ctrl?.desc && (
                                          <div style={{ fontSize: 10, color: "var(--ink-2,#555)", lineHeight: 1.45 }}>
                                            {ctrl.desc}
                                          </div>
                                        )}
                                      </div>
                                      <button
                                        title="Remove control"
                                        onClick={() => onRemoveControl(key, ref, cs.autoMapped.includes(ref))}
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
                              <div style={{ fontSize: 10, color: "var(--ink-3,#888)", fontStyle: "italic", marginBottom: 10 }}>
                                No controls assigned for this framework.
                              </div>
                            )}

                            {/* Add picker */}
                            <button
                              onClick={() => { setFwPicker(pickerOpen ? null : { key, fw }); setCtrlSearch(""); }}
                              style={{
                                fontSize: 9, padding: "2px 8px", borderRadius: 3, cursor: "pointer",
                                border: "1px dashed var(--acc,#2563eb)", background: "transparent",
                                color: "var(--acc,#2563eb)", marginBottom: 4,
                              }}
                            >+ Add control</button>

                            {pickerOpen && (
                              <div style={{
                                marginBottom: 6, padding: 6, background: "var(--surface,#fff)",
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
                                  {addable.slice(0, 12).map(c => (
                                    <button
                                      key={c.ref}
                                      onClick={() => { onAddManualControl(key, c.ref); setFwPicker(null); setCtrlSearch(""); }}
                                      style={{
                                        display: "flex", gap: 5, padding: "4px 4px", border: "none",
                                        background: "transparent", cursor: "pointer", textAlign: "left", fontSize: 9, borderRadius: 3,
                                      }}
                                      onMouseEnter={e => e.currentTarget.style.background = "var(--surface-2,#f5f5f5)"}
                                      onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                                    >
                                      <span className="mono" style={{ fontWeight: 600, color: "var(--acc,#2563eb)", minWidth: 40 }}>{c.ref}</span>
                                      <span style={{ color: "var(--ink,#111)" }}>{c.name}</span>
                                    </button>
                                  ))}
                                  {addable.length === 0 && (
                                    <span style={{ fontSize: 9, color: "var(--ink-3,#888)", padding: "3px 4px" }}>No more controls in this framework</span>
                                  )}
                                </div>
                              </div>
                            )}

                            {/* Per-cell save */}
                            <button
                              className="btn btn-sm btn-acc"
                              onClick={() => saveCellControls(key, fw)}
                              disabled={isSavingCell}
                              style={{ fontSize: 9, padding: "2px 10px", width: "100%", marginTop: 2 }}
                            >
                              {isSavingCell ? "Saving…" : "Save"}
                            </button>
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

  // ── Discovery state ──────────────────────────────────────────────────────
  const [fwSearch, setFwSearch]           = useState("");
  const [selectedFws, setSelectedFws]     = useState([]);
  const [searching, setSearching]         = useState(false);
  const [discoveredRisks, setDiscovered]  = useState([]);
  const [discRiskStates, setDiscStates]   = useState({});
  const [discCtrlStates, setDiscCtrlStates] = useState({});
  const [discCollapsed, setDiscCollapsed] = useState({});
  const [discExpandedCtrl, setDiscExpandedCtrl] = useState(new Set());

  // ── Upload Register state ─────────────────────────────────────────────
  const [uploadedRisks, setUploaded]           = useState([]);
  const [uploadRiskStates, setUploadStates]    = useState({});
  const [uploadCtrlStates, setUploadCtrlStates] = useState({});
  const [uploadCollapsed, setUploadCollapsed]  = useState({});
  const [uploadExpandedCtrl, setUploadExpandedCtrl] = useState(new Set());
  const [uploadLoading, setUploadLoading]      = useState(false);
  const [uploadErr, setUploadErr]              = useState(null);
  const [uploadFilename, setUploadFilename]    = useState(null);

  // ── Matrix view ───────────────────────────────────────────────────────────
  const [matrixView, setMatrixView]   = useState(false);
  const [savingRows, setSavingRows]   = useState(new Set());
  const [refreshing, setRefreshing]   = useState(false);

  // ── Output ───────────────────────────────────────────────────────────────
  const [outputYaml, setOutputYaml] = useState(null);
  const [converting, setConverting] = useState(false);
  const [convertErr, setConvertErr] = useState(null);
  const [validationMsg, setValidationMsg] = useState(null);

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
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/risk-register/framework-catalogs");
        if (!res.ok) return;
        const data = await res.json();
        const catalogs = data.catalogs || [];
        if (!catalogs.length) return;
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
        }
      } catch (_) {}
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
      if ((!s.included || wordingChanged) && !s.reason.trim()) {
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

  // ── File upload ────────────────────────────────────────────────────────────

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
      const found = data.risks || [];
      setUploaded(found);
      setUploadStates(initRiskStates(found));
      setUploadCtrlStates(initControlStates(found));
      setUploadCollapsed({});
      setUploadFilename(file.name);
    } catch (err) {
      setUploadErr(err.message || "Upload failed");
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

  async function handleSaveAllWording() {
    const modified = Object.entries(riskStates).filter(([, s]) => s.wording !== s.originalWording);
    if (!modified.length) return;
    setSavingRows(new Set(modified.map(([k]) => k)));
    try {
      await fetch("/api/risk-register/reviews", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          run_id: effectiveRunId || null,
          review_type: "internal",
          framework: "Internal Risk Register",
          risk_states: modified.map(([key, s]) => ({
            risk_ref: key,
            original_wording: s.originalWording || s.wording,
            current_wording: s.wording,
            included: s.included !== false,
            reason_for_change: s.reason || null,
            controls_assigned: [],
          })),
        }),
      });
      if (effectiveRunId) {
        await fetch("/api/risk-register/apply-wording", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            run_id: effectiveRunId,
            risks: modified.map(([key, s]) => ({ risk_ref: key, current_wording: s.wording })),
          }),
        });
      }
      setRiskStates(prev => {
        const next = { ...prev };
        for (const [key, s] of modified) next[key] = { ...next[key], originalWording: s.wording };
        return next;
      });
      setSavedAt(new Date().toLocaleTimeString([], { hour:"2-digit", minute:"2-digit" }));
      const url = effectiveRunId
        ? `/api/risk-register/risks/${effectiveRunId}`
        : ticker ? `/api/risk-register/risks/latest/${encodeURIComponent(ticker)}` : null;
      if (url) {
        const res = await fetch(url);
        if (res.ok) { const d = await res.json(); if (d.risks?.length) setRefreshedRisks(d.risks); }
      }
    } catch (_) {}
    setSavingRows(new Set());
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
    const framework   = isInternal
      ? "Internal Risk Register"
      : isUpload ? (uploadFilename || "Uploaded Register")
      : (selectedFws[0] || fwSearch || "External");

    const missing = validateStates(states);
    if (missing.length) {
      setValidationMsg(`${missing.length} risk${missing.length>1?"s":""} need a reason before converting: ${missing.join(", ")}`);
      return;
    }
    setValidationMsg(null);
    setConverting(true);
    setConvertErr(null);

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

  function renderSummaryBanner(sourceRisks, states) {
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
        {missing > 0 && (
          <span style={{ color:"var(--red,#e53)", fontWeight:600 }}>
            ⚠ {missing} reason{missing>1?"s":""} required before converting
          </span>
        )}
        {missing === 0 && total > 0 && (
          <span style={{ color:"var(--green,#2a7)", fontWeight:600 }}>✓ Ready to convert</span>
        )}
      </div>
    );
  }

  // ── Main render ───────────────────────────────────────────────────────────

  // Merge internal + discovered risks for the Framework Matrix so that imported
  // external frameworks appear as both rows and columns.
  const allMatrixRisks       = [...(effectiveRisks || []), ...discoveredRisks];
  const allMatrixRiskStates  = { ...discRiskStates,  ...riskStates  };
  const allMatrixCtrlStates  = { ...discCtrlStates,  ...ctrlStates  };
  const isDiscKey = key => discRiskStates[key] !== undefined && riskStates[key] === undefined;
  const matrixWordingChange  = (key, val)           => isDiscKey(key) ? discHandlers.wordingChange(key, val) : intHandlers.wordingChange(key, val);
  const matrixAddManual      = (key, ref)           => isDiscKey(key) ? discCtrl.addManual(key, ref)        : intCtrl.addManual(key, ref);
  const matrixRemove         = (key, ref, isAuto)   => isDiscKey(key) ? discCtrl.remove(key, ref, isAuto)   : intCtrl.remove(key, ref, isAuto);
  const matrixReset          = (key, auto, manual)  => isDiscKey(key) ? discCtrl.reset(key, auto, manual)   : intCtrl.reset(key, auto, manual);

  return (
    <div className="code-screen" data-screen-label="Risk Register Review" style={{ position:"relative" }}>
      {/* Header */}
      <div className="panel-head">
        <div>
          <div className="kicker">Execution · Risk Register Review</div>
          <div className="panel-title mt-8">Risk Register Review</div>
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

      {/* Tab content — scrollable, no bottom padding needed since action bar is outside */}
      <div style={{ flex:1, overflowY:"auto" }}>

        {/* ── Internal Register tab ── */}
        {activeTab === "internal" && (
          <div>
            {!effectiveRisks?.length ? (
              <Empty style={{ padding:48 }}>
                Run the pipeline to Stage 2 to load the internal risk register, then return here to review and curate before converting to code.
              </Empty>
            ) : (
              <>
                {renderSummaryBanner(effectiveRisks, riskStates)}

                {/* View toggle */}
                <div style={{ display:"flex", gap:6, marginBottom:12, paddingBottom:10, borderBottom:"1px solid var(--line,#eee)" }}>
                  {[["detail","Detail"],["matrix","Framework Matrix"]].map(([v, label]) => {
                    const active = matrixView ? v === "matrix" : v === "detail";
                    return (
                      <button
                        key={v}
                        onClick={() => setMatrixView(v === "matrix")}
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

                {matrixView ? (
                  <RiskFrameworkMatrix
                    risks={allMatrixRisks}
                    riskStates={allMatrixRiskStates}
                    ctrlStates={allMatrixCtrlStates}
                    onWordingChange={matrixWordingChange}
                    onAddManualControl={matrixAddManual}
                    onRemoveControl={matrixRemove}
                    onResetCtrl={matrixReset}
                    onSaveRow={handleSaveRowWording}
                    savingRows={savingRows}
                    savedAt={savedAt}
                  />
                ) : (
                  renderRiskList(effectiveRisks, riskStates, ctrlStates, collapsedGroups, setCollapsed, expandedCtrl, setExpandedCtrl, intHandlers, intCtrl, "internal")
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
                {PRESET_FRAMEWORKS.map(fw => {
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
                {renderSummaryBanner(discoveredRisks, discRiskStates)}
                {renderRiskList(
                  discoveredRisks, discRiskStates, discCtrlStates,
                  discCollapsed, setDiscCollapsed,
                  discExpandedCtrl, setDiscExpandedCtrl,
                  discHandlers, discCtrl, "external"
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
                  Supports .xlsx, .xls, .csv · Columns: ID, Name, Category, Score, RAG, Framework
                </span>
                <input
                  id="risk-upload-input"
                  type="file"
                  accept=".csv,.xlsx,.xls"
                  style={{ display:"none" }}
                  onChange={e => { const f = e.target.files?.[0]; if (f) handleFileUpload(f); e.target.value = ""; }}
                />
              </label>

              {uploadLoading && (
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
                Upload an Excel or CSV file above to import risks into the review and convert-to-code workflow.
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
      {activeTab === "internal"  && renderActionBar(effectiveRisks, riskStates, "internal")}
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

Object.assign(window, { RiskRegisterReviewScreen });
