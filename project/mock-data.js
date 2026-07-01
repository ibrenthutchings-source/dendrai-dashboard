/* ============================================================
   Dendrai Risk Loop — CEM event templates (company-agnostic)
   Company-specific risk profiles are now generated dynamically
   by RISK_ENGINE.buildProfile() in risk-engine.js.
   ============================================================ */

window.MOCK = (function () {

  const _defaultEventTemplates = [
    { control: "Revenue Recognition — Contract Review Gate",   area: "Revenue",            risk: "Revenue overstatement",        severity: "P1", exposure: "$12–18M",            category: "Financial Reporting",
      rc: "Most likely root cause: distributor attestation workflow regression after Q4 platform release. Containment: block billing on un-attested distributor contracts pending manual review. Systemic fix: re-platform RC-402 on contract lifecycle tool with mandatory gate." },
    { control: "Export License Validation — ECCN Check",       area: "Trade Compliance",   risk: "Export violation",             severity: "P1", exposure: "Regulatory",         category: "Trade Compliance",
      rc: "Likely root cause: self-classified ECCN on new SKU shipped to Greater China without engineering review. Containment: hold shipments to affected end-users; flag for trade-counsel screen. Systemic fix: mandate engineering ECCN sign-off in product launch workflow." },
    { control: "Segregation of Duties — AP Approval",          area: "Accounts Payable",   risk: "Fraudulent disbursement",      severity: "P2", exposure: "$2–5M",              category: "Fraud Risk",
      rc: "Likely root cause: temporary delegation during Q4 close granted both initiate and approve. Containment: revoke delegation; reverse last 30 days of dual-approved entries for review. Systemic fix: SoD matrix enforcement at workflow layer, not via policy alone." },
    { control: "Inventory Count Reconciliation",               area: "Supply Chain",       risk: "Inventory misstatement",       severity: "P2", exposure: "$4–8M",              category: "Operations",
      rc: "Likely root cause: cycle count cadence skipped during line conversion. Containment: full count of WIP; reconcile against ERP. Systemic fix: automated reminders + escalation for skipped counts." },
    { control: "Access Provisioning — Privileged Accounts",    area: "IT General Controls",risk: "Unauthorized access",          severity: "P2", exposure: "Data breach",        category: "Cybersecurity",
      rc: "Likely root cause: org-chart change orphaned 147 user re-certifications. Containment: emergency re-cert via skip-level approvers. Systemic fix: auto-detect org deltas and re-route pending certifications." },
    { control: "Management Override Exception Log",            area: "Financial Reporting",risk: "Management override",          severity: "P1", exposure: "Material misstatement", category: "Financial Reporting",
      rc: "Likely root cause: Q4 accrual posted with verbal CFO approval, no documented business case. Containment: require contemporaneous business case for >$1M overrides. Systemic fix: workflow-enforced documentation + AC visibility." },
    { control: "Third-Party Vendor SOC 2 Review",              area: "Vendor Management",  risk: "Supply chain exposure",        severity: "P3", exposure: "Reputational",       category: "Third-Party Risk",
      rc: "Likely root cause: SOC 2 Type II reports not refreshed for tier-1 vendors. Containment: request current attestations. Systemic fix: vendor-portal auto-renewal cadence." },
    { control: "Journal Entry Authorization — Month-End",      area: "Accounting",         risk: "Unauthorized JE manipulation", severity: "P2", exposure: "$1–3M",             category: "Financial Reporting",
      rc: "Likely root cause: JE approver pool included terminated employee for 8 days. Containment: void affected JEs; re-route. Systemic fix: HRIS-to-ERP role-revocation real-time sync." },
  ];

  // Live eventTemplates — starts with defaults, replaced by DB data when available
  let eventTemplates = _defaultEventTemplates.slice();

  async function load() {
    try {
      const res = await fetch("/api/cem-templates");
      if (!res.ok) return;
      const data = await res.json();
      const templates = data.templates || [];
      if (!templates.length) return;
      // Map DB shape → UI shape and replace array in-place
      eventTemplates.length = 0;
      for (const t of templates) {
        eventTemplates.push({
          control:  t.control,
          area:     t.area,
          risk:     t.risk_label,
          severity: t.severity,
          exposure: t.exposure || "",
          category: t.category || "",
          rc:       t.rc_narrative || "",
        });
      }
    } catch (_) {}
  }

  // Kick off load immediately; consumers that need fresh data should await load()
  load();

  return { eventTemplates, load };

})();
