/* ============================================================
   Dendrai Intelligenza — Disclosure Risk Layer
   The risk that a risk is MISSED or MISREPORTED.

   The register scores the risks themselves; this layer scores the
   company's exposure to failing to identify or accurately report one
   to a regulator. Pure logic, no fetching — CoverageGapPanel calls it.

   Three failure modes per obligation domain:
     missed      a signal fired (RSS / 8-K) but no register risk covers
                 the domain
     misaligned  the internal rating disagrees with the company's own
                 Item 1A language (rated Green but disclosed as a risk
                 factor; rated Red with no filing evidence)
     stale       the assessment is older than STALE_DAYS (only evaluated
                 when a risk carries assessedAt — not captured yet, so
                 reported as "undated" rather than guessed)

   exposure = severity × failureLikelihood × consequence
   ============================================================ */

window.DISCLOSURE_RISK = (function () {

  const STALE_DAYS = 90;
  const AUDIT_STALE_DAYS = 180;   // behavioural audits are heavier to run than reassessments

  // ── Reporting obligations by domain ──────────────────────────
  // consequence: 1 low · 2 enforcement/fine · 3 mandatory disclosure
  // with a hard clock or personal certification exposure.
  const OBLIGATIONS = {
    cyber: {
      label: "Cybersecurity",
      consequence: 3,
      regulators: ["SEC", "CISA (CIRCIA)", "State AGs"],
      duties: "8-K Item 1.05 within 4 business days of materiality determination; 10-K Item 1C (risk management & governance); state breach-notification laws",
      categories: ["cyber", "information security", "data protection"],
      signalDomains: ["Cybersecurity"],
      filingKeywords: ["cyber", "data breach", "ransomware", "unauthorized access"],
    },
    financial: {
      label: "Financial Reporting",
      consequence: 3,
      regulators: ["SEC", "PCAOB"],
      duties: "10-K/10-Q accuracy, SOX 302/404 certification, 8-K Item 4.02 non-reliance within 4 business days",
      categories: ["financial", "revenue", "reporting", "accounting"],
      signalDomains: ["Financial Reporting"],
      filingKeywords: ["material weakness", "restatement", "icfr", "revenue recognition"],
    },
    esg: {
      label: "ESG / Climate",
      consequence: 2,
      regulators: ["SEC", "EU (CSRD/ESRS)", "California (SB 253/261)"],
      duties: "Climate-related risk disclosure (Reg S-K / ISSB / ESRS), emissions reporting, avoidance of greenwashing",
      categories: ["esg", "environmental", "climate", "sustainability"],
      signalDomains: ["ESG", "Environmental"],
      filingKeywords: ["climate", "emission", "sustainability", "environmental"],
    },
    ai: {
      label: "AI",
      consequence: 2,
      regulators: ["SEC", "FTC", "EU AI Office", "State AI laws"],
      duties: "Accurate AI capability claims (AI-washing), EU AI Act high-risk obligations, Item 1A AI risk factors, bias/consumer-protection exposure",
      categories: ["ai ", "artificial", "algorithm", "machine learning", "bias"],
      signalDomains: ["AI"],
      filingKeywords: ["artificial intelligence", "machine learning", "algorithm", "generative"],
    },
    privacy: {
      label: "Privacy / Data Protection",
      consequence: 2,
      regulators: ["EU DPAs (GDPR)", "State AGs (CCPA etc.)", "FTC", "HHS (HIPAA)"],
      duties: "GDPR 72-hour breach notification, state privacy enforcement, HIPAA breach notification",
      categories: ["privacy", "data protection", "phi"],
      signalDomains: ["Privacy"],
      filingKeywords: ["privacy", "gdpr", "ccpa", "personal data"],
    },
    trade: {
      label: "Trade Compliance",
      consequence: 2,
      regulators: ["BIS", "OFAC"],
      duties: "Export-control voluntary self-disclosure, sanctions reporting",
      categories: ["trade", "export"],
      signalDomains: ["Trade Compliance"],
      filingKeywords: ["export control", "sanction", "tariff", "embargo"],
    },
  };

  const SEVERITY = { R: 3, A: 2, G: 1 };
  const LIKELIHOOD = { missed: 1.0, misaligned: 0.6, stale: 0.4 };

  function _text(r) {
    return `${r.category || ""} ${r.name || ""}`.toLowerCase() + " ";
  }

  function riskDomains(risk) {
    const t = _text(risk);
    return Object.keys(OBLIGATIONS).filter(k =>
      OBLIGATIONS[k].categories.some(c => t.includes(c)));
  }

  // A risk name can hit several domains ("Cybersecurity & Data Protection"
  // → cyber + privacy); that is intentional — one risk can carry two duties.
  function _covering(risks, key) {
    return risks.filter(r => riskDomains(r).includes(key));
  }

  function _signalDomains(sig) {
    if (Array.isArray(sig.domains)) return sig.domains;
    return sig.cat ? [sig.cat] : [];
  }

  /**
   * @param {object} p
   * @param {Array}  p.risks         active register
   * @param {Array}  p.rssSignals    graded RSS signals (gradeArticle output)
   * @param {Array}  p.events        CEM events parsed from 8-Ks
   * @param {object} p.aiInventory   GET /observability/ai-inventory body, or null
   * @param {number} [p.now]         ms epoch, injectable for tests
   */
  function assess({ risks = [], rssSignals = [], events = [], aiInventory = null, aiGovernance = null, now = Date.now() }) {
    const findings = [];

    // ── Missed: signals in a domain with no covering risk ─────
    for (const [key, ob] of Object.entries(OBLIGATIONS)) {
      const covering = _covering(risks, key);
      if (covering.length) continue;

      const rssHits = rssSignals.filter(s =>
        _signalDomains(s).some(d => ob.signalDomains.includes(d)));
      const eightKHits = events.filter(e => {
        const c = String(e.cat || e.category || "").toLowerCase();
        return ob.categories.some(cat => c.includes(cat.trim()));
      });
      const hits = rssHits.length + eightKHits.length;

      if (hits > 0) {
        findings.push({
          kind: "missed", domain: key, severityRag: "R",
          evidence: `${hits} signal${hits !== 1 ? "s" : ""} (${rssHits.length} RSS, ${eightKHits.length} 8-K) but no ${ob.label} risk in the register`,
        });
      }
    }

    // ── AI: inventory says AI exists, register says nothing ───
    const aiRows = aiInventory?.rows || [];
    if (aiRows.length) {
      const aiCovered = _covering(risks, "ai").length > 0;
      if (!aiCovered) {
        findings.push({
          kind: "missed", domain: "ai", severityRag: "R",
          evidence: `${aiRows.length} AI system${aiRows.length !== 1 ? "s" : ""} in the inventory but no AI risk in the register`,
        });
      }
      const untiered = aiInventory.untiered_count ?? aiRows.filter(r => !r.risk_tier).length;
      if (untiered > 0) {
        findings.push({
          kind: "missed", domain: "ai", severityRag: "A",
          evidence: `${untiered} of ${aiRows.length} AI systems are untiered — their risk cannot be reported`,
        });
      }
    }

    // ── AI governance register: unevidenced or lapsed controls ─
    // An attested control that is unassessed, expired, or has no defined
    // oversight is a misreporting risk: the company would be asserting
    // governance it cannot evidence. HIGH-tier systems weigh as Red.
    for (const s of aiGovernance || []) {
      const sev = String(s.risk_tier || "").toUpperCase() === "HIGH" ? "R" : "A";
      const name = s.system_name;
      if (s.requires_human_oversight && !s.human_oversight_defined) {
        findings.push({
          kind: "misaligned", domain: "ai", severityRag: sev,
          evidence: `${name}: human oversight required but not defined — any oversight claim in disclosures is unsupported`,
        });
      }
      // Behavioural audit: tests whether the attested oversight actually
      // works. Only systems that claim oversight need this evidence.
      if (s.requires_human_oversight && s.human_oversight_defined) {
        const auditAge = s.last_audit_at ? (now - new Date(s.last_audit_at).getTime()) / 86400000 : null;
        if (!s.last_audit_verdict) {
          findings.push({
            kind: "misaligned", domain: "ai", severityRag: sev,
            evidence: `${name}: oversight is attested but has never been behaviourally tested`,
          });
        } else if (s.last_audit_verdict === "ESCALATE" || s.last_audit_verdict === "MONITOR") {
          findings.push({
            kind: "misaligned", domain: "ai",
            severityRag: s.last_audit_verdict === "ESCALATE" ? "R" : "A",
            evidence: `${name}: behavioural audit returned ${s.last_audit_verdict} (${s.last_audit_events ?? "?"} events) — oversight claim is contradicted by evidence`,
          });
        } else if (s.last_audit_verdict === "INSUFFICIENT_DATA") {
          findings.push({
            kind: "misaligned", domain: "ai", severityRag: "A",
            evidence: `${name}: behavioural audit could not test oversight (insufficient data) — control is unevidenced, not passed`,
          });
        } else if (auditAge != null && auditAge > AUDIT_STALE_DAYS) {
          findings.push({
            kind: "stale", domain: "ai", severityRag: sev,
            evidence: `${name}: last clean behavioural audit was ${Math.round(auditAge)} days ago (limit ${AUDIT_STALE_DAYS})`,
          });
        }
      }

      const expired = s.status === "EXPIRED" ||
        (s.assessment_expires_at && new Date(s.assessment_expires_at).getTime() < now);
      if (!s.last_assessment_date) {
        findings.push({
          kind: "stale", domain: "ai", severityRag: sev,
          evidence: `${name}: never assessed`,
        });
      } else if (expired) {
        findings.push({
          kind: "stale", domain: "ai", severityRag: sev,
          evidence: `${name}: assessment expired ${String(s.assessment_expires_at || "").slice(0, 10)}`,
        });
      }
    }

    // ── Misaligned + stale, per register risk ─────────────────
    for (const r of risks) {
      const domains = riskDomains(r);
      if (!domains.length) continue;
      const rag = r.rag || "G";
      const snippet = r.filingSnippet || null;

      for (const key of domains) {
        if (rag === "G" && snippet) {
          findings.push({
            kind: "misaligned", domain: key, riskId: r.id, severityRag: "A",
            evidence: `${r.id} rated Green internally, but the company's own Item 1A discusses it (${r.filingDate || "date n/a"})`,
          });
        } else if (rag === "R" && r.filingSnippet === undefined) {
          // undefined = enrichment never ran; null/"" = ran and found nothing.
          continue;
        } else if (rag === "R" && !snippet) {
          findings.push({
            kind: "misaligned", domain: key, riskId: r.id, severityRag: "R",
            evidence: `${r.id} rated Red internally with no matching Item 1A language — external disclosure may understate it`,
          });
        }
      }

      if (r.assessedAt) {
        const ageDays = (now - new Date(r.assessedAt).getTime()) / 86400000;
        if (ageDays > STALE_DAYS) {
          for (const key of domains) {
            findings.push({
              kind: "stale", domain: key, riskId: r.id, severityRag: rag,
              evidence: `${r.id} last assessed ${Math.round(ageDays)} days ago (limit ${STALE_DAYS})`,
            });
          }
        }
      }
    }

    // ── Exposure score ────────────────────────────────────────
    for (const f of findings) {
      f.exposure = +(SEVERITY[f.severityRag] * LIKELIHOOD[f.kind] * OBLIGATIONS[f.domain].consequence).toFixed(1);
      f.obligation = OBLIGATIONS[f.domain];
    }
    findings.sort((a, b) => b.exposure - a.exposure);

    const count = kind => findings.filter(f => f.kind === kind).length;
    const undated = risks.filter(r => riskDomains(r).length && !r.assessedAt).length;

    return {
      findings,
      missed: count("missed"),
      misaligned: count("misaligned"),
      stale: count("stale"),
      undated,                       // obligation-bearing risks with no assessedAt — staleness unverifiable
      totalExposure: +findings.reduce((s, f) => s + f.exposure, 0).toFixed(1),
      aiConfidence: _aiConfidence(aiInventory, aiGovernance, now),
    };
  }

  // AI risk is hard to score, so say how much the number can be trusted.
  // Three evidence inputs: classification coverage (inventory), assessment
  // currency, and behavioural-audit results (governance register). Confidence
  // is the weakest of them. "high" needs every oversight-claiming system to
  // hold a recent CLEAR audit; INSUFFICIENT_DATA does not count as a pass.
  const _LEVELS = ["unknown", "very low", "low", "medium", "high"];

  function _aiConfidence(inv, gov, now) {
    const rows = inv?.rows || [];
    if (!inv) return { level: "unknown", note: "AI inventory unavailable — AI exposure cannot be assessed" };
    if (!rows.length) return { level: "unknown", note: "No AI systems inventoried; shadow AI cannot be ruled out" };

    const tiered = rows.filter(r => r.risk_tier).length / rows.length;
    let level = tiered >= 0.9 ? 4 : tiered >= 0.5 ? 2 : 1;
    const notes = [`${Math.round(tiered * 100)}% of systems tiered`];

    if (gov == null) {
      level = Math.min(level, 2);
      notes.push("governance register unavailable");
    } else if (!gov.length) {
      level = Math.min(level, 1);
      notes.push("no systems on the governance register");
    } else {
      const current = gov.filter(s => s.last_assessment_date && s.status !== "EXPIRED" &&
        !(s.assessment_expires_at && new Date(s.assessment_expires_at).getTime() < now)).length / gov.length;
      notes.push(`${Math.round(current * 100)}% of governed systems have a current assessment`);
      if (current < 0.5) level = Math.min(level, 1);
      else if (current < 0.9) level = Math.min(level, 2);

      const overseen = gov.filter(s => s.requires_human_oversight);
      if (overseen.length) {
        const proven = overseen.filter(s => s.last_audit_verdict === "CLEAR" && s.last_audit_at &&
          (now - new Date(s.last_audit_at).getTime()) / 86400000 <= AUDIT_STALE_DAYS).length / overseen.length;
        notes.push(`${Math.round(proven * 100)}% of oversight-claiming systems have a recent clean behavioural audit`);
        if (proven < 0.5) level = Math.min(level, 2);
        else if (proven < 1) level = Math.min(level, 3);
      } else {
        notes.push("no systems claim human oversight, so none was behaviourally tested");
        level = Math.min(level, 3);
      }
    }
    return { level: _LEVELS[level], note: notes.join("; ") };
  }

  return { OBLIGATIONS, STALE_DAYS, riskDomains, assess };
})();
