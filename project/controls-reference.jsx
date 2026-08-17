/* ============================================================
   Shared control-library reference data.

   Single source of truth for the ~34-row seed control list and the
   "load real controls from the DB" fetch — previously three independent
   copies (risk-register-review.jsx, risk-graph-viz.jsx, risk-sankey.jsx),
   each its own hardcoded array plus its own fetch-and-merge effect, free
   to silently drift out of sync with each other.

   Canonical field shape matches risk-register-review.jsx's original
   MASTER_CONTROLS ({ref, framework, name, category, domain, desc}) since
   that's the shape already exposed on window.MASTER_CONTROLS for other
   screens (audit-scope-review.jsx) to read directly, and the shape
   GET /api/risk-register/controls already returns server-side. Consumers
   with their own internal field-naming convention (risk-graph-viz.jsx's
   fw/cat/domain, risk-sankey.jsx's fw/cat/dom) map these fields to their
   own shape at the point of use rather than this module dictating a
   naming convention to every consumer.
   ============================================================ */

export const DEFAULT_CONTROLS = [
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

// Framework color/short-label maps — previously two independent copies
// (risk-graph-viz.jsx's FW_COLOR/FW_SHORT, risk-sankey.jsx's FW_COLOR),
// already agreeing on color for every framework they both listed but each
// missing one the other had (risk-graph-viz.jsx had no COSO ERM; neither
// had every DEFAULT_CONTROLS framework value). One canonical set, covering
// every framework value that actually appears above, same consolidation
// rationale as DEFAULT_CONTROLS itself.
export const FRAMEWORK_COLOR = {
  "Internal":       "#94a3b8",
  "SOC 2":          "#a855f7",
  "NIST SP 800-53": "#3b82f6",
  "CIS Controls":   "#f59e0b",
  "ISO/IEC 27001":  "#22c55e",
  "ISO/IEC 42001":  "#ec4899",
  "COSO ERM":       "#f97316",
};
export const FRAMEWORK_COLOR_FALLBACK = "#64748b";

export const FRAMEWORK_SHORT_LABEL = {
  "Internal":       "Internal",
  "SOC 2":          "SOC 2",
  "NIST SP 800-53": "NIST",
  "CIS Controls":   "CIS",
  "ISO/IEC 27001":  "ISO 27001",
  "ISO/IEC 42001":  "ISO 42001",
  "COSO ERM":       "COSO ERM",
};

/**
 * Fetches the live control library from the DB (GET /api/risk-register/controls)
 * and returns it verbatim ({ref, framework, name, category, domain, ...}, same
 * shape risk_register_endpoints.py already returns). Returns null on any
 * failure (network error, non-OK response, empty result) rather than
 * throwing, so every caller can fall back to DEFAULT_CONTROLS uniformly
 * without its own try/catch. Every failure branch logs — a silent failure
 * here means the app quietly falls back to the ~34-control seed list with
 * no sign anything was wrong, which is exactly the bug this consolidation
 * was written to stop happening independently in three different files.
 */
export async function fetchControlsFromApi() {
  try {
    const res = await fetch("/api/risk-register/controls");
    if (!res.ok) {
      console.warn(`[controls-reference] GET /controls failed (HTTP ${res.status}) — ` +
        `showing the built-in ~34-control fallback only; imported registers' controls will not appear.`);
      return null;
    }
    const data = await res.json();
    const controls = data.controls || [];
    if (!controls.length) {
      console.warn("[controls-reference] GET /controls returned zero controls — showing the built-in fallback.");
      return null;
    }
    return controls;
  } catch (e) {
    console.warn("[controls-reference] Could not load the control library from the server — " +
      "showing the built-in fallback only:", e);
    return null;
  }
}
