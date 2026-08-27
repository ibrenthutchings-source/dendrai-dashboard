#!/usr/bin/env python3
"""
Curated seed content for the concept layer (db.py's concepts/concept_relations
tables, applied by db.seed_ontology()).

Same guardrail as framework_mappings.py: curated, hand-reviewed mappings ONLY
— never LLM-generated or auto-inferred. A concept or relation with no entry
here simply doesn't exist yet; that is an honest gap, not something papered
over with a guess. Edit this file directly to correct or extend the
vocabulary; there is deliberately no "AI-assist" button on this data.

This is a PROJECTION of vocabularies that already exist as hardcoded literals
elsewhere — it does not replace them. Through Stage 3 of the ontology plan,
those literals stay authoritative:
    - risk_category    <- project/risk-engine.js's CATEGORY_IMPACT (10 keys)
    - enterprise_domain <- risk_register_endpoints.py's _keyword_domain() (9 domains)
Nothing existing reads from the concepts tables yet. seed_ontology() is safe
to re-run: it always overwrites with the current content of this file, so an
edit here takes effect on next restart with no manual migration.

Reviewed and approved 2026-08-26 (first slice: risk_category + enterprise_domain
only, per the ontology plan's "smallest shippable slice"). COSO ERM/IC-IF, the
SOC2/NIST/ISO crosswalk, and the SOX category reconciliation below are the
follow-on slice, reviewed and approved 2026-08-27. PaC process and
SCF-imported schemes remain future work, added the same way.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Seeded in this order — concepts in an earlier scheme are available as
# broader-concept parents for concepts in a later one. Within coso_erm, the
# 5 components must precede their 20 principles (principles use
# broader_scheme="coso_erm" pointing back at their own component).
SEED_ORDER: List[str] = [
    "risk_category", "enterprise_domain", "coso_erm", "coso_icif",
    "soc2", "nist_800_53", "iso_27001", "sox_risk_category",
]

# ─────────────────────────────────────────────────────────────────────────────
# risk_category — the 10 categories the risk loop actually emits.
# Verbatim from project/risk-engine.js:46 CATEGORY_IMPACT's keys. Definitions
# are new (that file has no descriptive text, only an impact-weight int per
# category) — written to describe what each category is actually used for.
# ─────────────────────────────────────────────────────────────────────────────

_RISK_CATEGORY_CONCEPTS: List[Dict[str, Any]] = [
    {"pref_label": "Revenue", "alt_labels": ["Revenue Recognition", "Revenue Risk"],
     "definition": "Risk to the timing, amount, or recognition of revenue."},
    {"pref_label": "Operational", "alt_labels": ["Operations Risk", "Execution Risk"],
     "definition": "Risk to day-to-day business execution not otherwise covered by a "
                    "more specific category — process failure, execution risk, capacity."},
    {"pref_label": "Financial Reporting", "alt_labels": ["Financial Statement Risk", "Accounting Risk"],
     "definition": "Risk of material misstatement in financial reports or the "
                    "controls that produce them."},
    {"pref_label": "Supply", "alt_labels": ["Supply Chain Risk", "Vendor Risk", "Supplier Risk"],
     "definition": "Risk arising from suppliers, vendors, or the supply chain — "
                    "concentration, disruption, or dependency."},
    {"pref_label": "Cybersecurity", "alt_labels": ["Cyber Risk", "Information Security Risk"],
     "definition": "Risk of unauthorized access, breach, or compromise of systems or data."},
    {"pref_label": "Trade Compliance", "alt_labels": ["Export Control Risk", "Sanctions Risk"],
     "definition": "Risk of violating export controls, sanctions, or cross-border "
                    "trade regulation."},
    {"pref_label": "ESG", "alt_labels": ["Environmental Risk", "Social Risk", "Governance Risk (ESG)"],
     "definition": "Environmental, social, and governance risk — predominantly "
                    "disclosure- and regulatory-driven in this app's scoring."},
    {"pref_label": "Compliance", "alt_labels": ["Regulatory Compliance Risk"],
     "definition": "Risk of failing to meet an applicable regulatory or internal "
                    "standard, other than trade/export-specific compliance."},
    {"pref_label": "Legal", "alt_labels": ["Litigation Risk", "Legal Liability Risk"],
     "definition": "Risk of litigation, legal liability, or contractual exposure."},
    {"pref_label": "Strategic", "alt_labels": ["Strategic Risk"],
     "definition": "Risk to the achievement of the entity's business objectives or strategy."},
]

# ─────────────────────────────────────────────────────────────────────────────
# enterprise_domain — the 9 domains risk_register_endpoints.py's
# _keyword_domain() assigns by keyword match (verbatim labels from that
# function). The function's own fallback (`return category or "Enterprise
# Risk"`) is NOT seeded as a 10th domain here — it is a fallback label, not a
# defined domain, and forcing it into the vocabulary would misrepresent an
# absence-of-match as a real concept.
# ─────────────────────────────────────────────────────────────────────────────

_ENTERPRISE_DOMAIN_CONCEPTS: List[Dict[str, Any]] = [
    {"pref_label": "Identity & Access Management",
     "alt_labels": ["IAM", "Access Control"],
     "definition": "Controls over who can access systems and data, and what they can do — "
                    "authentication, authorization, provisioning, privilege management."},
    {"pref_label": "Financial Reporting & Controls",
     "alt_labels": ["Financial Controls"],
     "definition": "Controls over the accuracy and integrity of financial reporting."},
    {"pref_label": "Cyber Security & Data Protection",
     "alt_labels": ["Cybersecurity Domain", "Data Protection"],
     "definition": "Protection of systems and data from breach, unauthorized disclosure, or attack."},
    {"pref_label": "Third-Party & Vendor Risk",
     "alt_labels": ["Vendor Management", "Supplier Risk Domain"],
     "definition": "Risk introduced by external vendors, suppliers, or outsourced service providers."},
    {"pref_label": "Operational Resilience",
     "alt_labels": ["Business Continuity", "Disaster Recovery"],
     "definition": "Ability to continue or recover critical operations through disruption."},
    {"pref_label": "Regulatory & Compliance",
     "alt_labels": ["Regulatory Risk Domain"],
     "definition": "Risk of failing to meet legal or regulatory obligations, including "
                    "privacy law (GDPR/CCPA) and SOX."},
    {"pref_label": "Technology & Change Management",
     "alt_labels": ["Change Management", "IT Change Risk"],
     "definition": "Risk introduced by changes to systems, configuration, or deployed software."},
    {"pref_label": "People & Organisational Risk",
     "alt_labels": ["Workforce Risk", "Key Person Risk"],
     "definition": "Risk related to staffing, retention, succession, or organizational capacity."},
    {"pref_label": "Market & Economic Risk",
     "alt_labels": ["Macroeconomic Risk"],
     "definition": "Risk driven by market conditions — interest rates, credit, inflation, currency."},
]

# ─────────────────────────────────────────────────────────────────────────────
# coso_erm — COSO ERM 2017's 5 components + 20 principles, verbatim from
# risks_as_code.py's ERM_PRINCIPLES (component/number/label already curated
# and approved there for the Risk Coverage Cube's evidence view). Components
# are seeded first (no broader_id); each principle's broader_scheme/
# broader_pref_label points back at its own component. notation carries the
# principle number ('P1'..'P20') so it survives independent of label text.
# ─────────────────────────────────────────────────────────────────────────────

_COSO_ERM_COMPONENTS: List[Dict[str, Any]] = [
    {"pref_label": "Governance & Culture",
     "definition": "Board risk oversight, operating structures, desired culture, core values, "
                    "and the people who carry them out — the foundation the rest of ERM sits on."},
    {"pref_label": "Strategy & Objective-Setting",
     "definition": "How business context, risk appetite, alternative strategies, and business "
                    "objectives are analyzed and set."},
    {"pref_label": "Performance",
     "definition": "Identifying, assessing severity of, prioritizing, and responding to risks "
                    "that affect the achievement of strategy and business objectives."},
    {"pref_label": "Review & Revision",
     "definition": "Assessing substantial change, reviewing risk and performance, and pursuing "
                    "improvement in the ERM approach itself."},
    {"pref_label": "Information, Communication & Reporting",
     "definition": "Leveraging information and technology, communicating risk information, and "
                    "reporting on risk, culture, and performance across the entity."},
]

_COSO_ERM_PRINCIPLES: List[Dict[str, Any]] = [
    {"pref_label": "Exercises Board Risk Oversight", "notation": "P1",
     "broader_scheme": "coso_erm", "broader_pref_label": "Governance & Culture",
     "definition": "The board provides oversight of strategy and carries out governance responsibilities."},
    {"pref_label": "Establishes Operating Structures", "notation": "P2",
     "broader_scheme": "coso_erm", "broader_pref_label": "Governance & Culture",
     "definition": "The entity establishes operating structures in pursuit of strategy and business objectives."},
    {"pref_label": "Defines Desired Culture", "notation": "P3",
     "broader_scheme": "coso_erm", "broader_pref_label": "Governance & Culture",
     "definition": "The entity defines the desired behaviors that characterize its desired culture."},
    {"pref_label": "Demonstrates Commitment to Core Values", "notation": "P4",
     "broader_scheme": "coso_erm", "broader_pref_label": "Governance & Culture",
     "definition": "The entity demonstrates a commitment to its core values."},
    {"pref_label": "Attracts, Develops, and Retains Individuals", "notation": "P5",
     "broader_scheme": "coso_erm", "broader_pref_label": "Governance & Culture",
     "definition": "The entity is committed to building human capital aligned to strategy and business objectives."},
    {"pref_label": "Analyzes Business Context", "notation": "P6",
     "broader_scheme": "coso_erm", "broader_pref_label": "Strategy & Objective-Setting",
     "definition": "The entity considers potential effects of business context on risk profile."},
    {"pref_label": "Defines Risk Appetite", "notation": "P7",
     "broader_scheme": "coso_erm", "broader_pref_label": "Strategy & Objective-Setting",
     "definition": "The entity defines risk appetite in the context of creating, preserving, and realizing value."},
    {"pref_label": "Evaluates Alternative Strategies", "notation": "P8",
     "broader_scheme": "coso_erm", "broader_pref_label": "Strategy & Objective-Setting",
     "definition": "The entity evaluates alternative strategies and potential impact on risk profile."},
    {"pref_label": "Formulates Business Objectives", "notation": "P9",
     "broader_scheme": "coso_erm", "broader_pref_label": "Strategy & Objective-Setting",
     "definition": "The entity considers risk while establishing business objectives at various levels."},
    {"pref_label": "Identifies Risk", "notation": "P10",
     "broader_scheme": "coso_erm", "broader_pref_label": "Performance",
     "definition": "The entity identifies risk that impacts the performance of strategy and business objectives."},
    {"pref_label": "Assesses Severity of Risk", "notation": "P11",
     "broader_scheme": "coso_erm", "broader_pref_label": "Performance",
     "definition": "The entity assesses the severity of risk."},
    {"pref_label": "Prioritizes Risks", "notation": "P12",
     "broader_scheme": "coso_erm", "broader_pref_label": "Performance",
     "definition": "The entity prioritizes risks as a basis for selecting responses to risks."},
    {"pref_label": "Implements Risk Responses", "notation": "P13",
     "broader_scheme": "coso_erm", "broader_pref_label": "Performance",
     "definition": "The entity identifies and selects risk responses."},
    {"pref_label": "Develops Portfolio View", "notation": "P14",
     "broader_scheme": "coso_erm", "broader_pref_label": "Performance",
     "definition": "The entity develops and evaluates a portfolio view of risk."},
    {"pref_label": "Assesses Substantial Change", "notation": "P15",
     "broader_scheme": "coso_erm", "broader_pref_label": "Review & Revision",
     "definition": "The entity identifies and assesses changes that may substantially affect strategy and business objectives."},
    {"pref_label": "Reviews Risk and Performance", "notation": "P16",
     "broader_scheme": "coso_erm", "broader_pref_label": "Review & Revision",
     "definition": "The entity reviews entity performance and considers risk."},
    {"pref_label": "Pursues Improvement in ERM", "notation": "P17",
     "broader_scheme": "coso_erm", "broader_pref_label": "Review & Revision",
     "definition": "The entity pursues improvement of enterprise risk management."},
    {"pref_label": "Leverages Information and Technology", "notation": "P18",
     "broader_scheme": "coso_erm", "broader_pref_label": "Information, Communication & Reporting",
     "definition": "The entity leverages the entity's information and technology systems to support ERM."},
    {"pref_label": "Communicates Risk Information", "notation": "P19",
     "broader_scheme": "coso_erm", "broader_pref_label": "Information, Communication & Reporting",
     "definition": "The entity uses communication channels to support ERM."},
    {"pref_label": "Reports on Risk, Culture, and Performance", "notation": "P20",
     "broader_scheme": "coso_erm", "broader_pref_label": "Information, Communication & Reporting",
     "definition": "The entity reports on risk, culture, and performance at multiple levels and across the entity."},
]

# ─────────────────────────────────────────────────────────────────────────────
# coso_icif — COSO IC-IF 2013's 5 components, verbatim from risks_as_code.py's
# ICIF_COMPONENTS. 'Unmapped' is deliberately NOT seeded here — same reasoning
# as enterprise_domain's excluded fallback label above: it is the absence of a
# mapped control, not a real component.
# ─────────────────────────────────────────────────────────────────────────────

_COSO_ICIF_CONCEPTS: List[Dict[str, Any]] = [
    {"pref_label": "Control Environment",
     "definition": "The set of standards, processes, and structures providing the basis for "
                    "carrying out internal control across the organization."},
    {"pref_label": "Risk Assessment",
     "definition": "The entity's dynamic and iterative process for identifying and assessing "
                    "risks to the achievement of its objectives."},
    {"pref_label": "Control Activities",
     "definition": "The actions established through policies and procedures that help ensure "
                    "management's directives to mitigate risk are carried out."},
    {"pref_label": "Information & Communication",
     "definition": "The information needed to carry out internal control responsibilities, and "
                    "the communication of that information both internally and externally."},
    {"pref_label": "Monitoring Activities",
     "definition": "Ongoing and/or separate evaluations to ascertain whether the components of "
                    "internal control are present and functioning."},
]

# ─────────────────────────────────────────────────────────────────────────────
# soc2 / nist_800_53 / iso_27001 — the criteria codes actually referenced by
# framework_mappings.py's FRAMEWORK_MAPPINGS (the 8 curated INFRA-* controls).
# notation = pref_label = the code itself; these frameworks are cited by code,
# not by a house label. Definitions are this slice's own curation — the
# criteria text is publicly published (AICPA Trust Services Criteria, NIST SP
# 800-53 Rev 5, ISO/IEC 27001:2013 Annex A) but summarizing it here is new
# work, not a copy of framework_mappings.py (which never carried descriptive
# text, only codes). Flagged for a compliance-literate review pass before
# being relied on for an actual audit — same caveat framework_mappings.py's
# own docstring already carries for these exact 8 controls.
# ─────────────────────────────────────────────────────────────────────────────

_SOC2_CONCEPTS: List[Dict[str, Any]] = [
    {"pref_label": "CC6.1", "notation": "CC6.1",
     "definition": "The entity implements logical access security software, infrastructure, "
                    "and architectures over protected information assets."},
    {"pref_label": "CC6.3", "notation": "CC6.3",
     "definition": "The entity authorizes, modifies, or removes access to protected information "
                    "assets based on roles/responsibilities, and reviews it periodically."},
    {"pref_label": "CC6.6", "notation": "CC6.6",
     "definition": "The entity implements logical access security measures to protect against "
                    "threats from sources outside its system boundaries."},
    {"pref_label": "CC6.7", "notation": "CC6.7",
     "definition": "The entity restricts the transmission, movement, and removal of information "
                    "to authorized users/processes and protects it during transmission."},
    {"pref_label": "CC7.2", "notation": "CC7.2",
     "definition": "The entity monitors system components and the operation of controls to "
                    "detect anomalies indicative of malicious acts, natural disasters, or errors."},
    {"pref_label": "CC8.1", "notation": "CC8.1",
     "definition": "The entity authorizes, designs, develops, configures, documents, tests, "
                    "approves, and implements changes to infrastructure, data, software, and procedures."},
]

_NIST_800_53_CONCEPTS: List[Dict[str, Any]] = [
    {"pref_label": "AC-2", "notation": "AC-2",
     "definition": "Account Management — the organization manages information system accounts, "
                    "including establishment, activation, modification, review, and removal."},
    {"pref_label": "AC-6", "notation": "AC-6",
     "definition": "Least Privilege — the organization employs the principle of least privilege, "
                    "allowing only authorized accesses necessary to accomplish assigned tasks."},
    {"pref_label": "IA-5", "notation": "IA-5",
     "definition": "Authenticator Management — the organization manages information system "
                    "authenticators (passwords, tokens, keys), including issuance and revocation."},
    {"pref_label": "SC-7", "notation": "SC-7",
     "definition": "Boundary Protection — the information system monitors and controls "
                    "communications at external and key internal boundaries."},
    {"pref_label": "SC-8", "notation": "SC-8",
     "definition": "Transmission Confidentiality and Integrity — the information system protects "
                    "the confidentiality and integrity of transmitted information."},
    {"pref_label": "AU-2", "notation": "AU-2",
     "definition": "Event Logging — the organization identifies the types of events the system "
                    "is capable of logging."},
    {"pref_label": "AU-3", "notation": "AU-3",
     "definition": "Content of Audit Records — the system generates audit records containing "
                    "information establishing what, when, where, and who was involved in an event."},
    {"pref_label": "CM-3", "notation": "CM-3",
     "definition": "Configuration Change Control — the organization determines, documents, and "
                    "controls changes to the system's baseline configuration."},
    {"pref_label": "CM-7", "notation": "CM-7",
     "definition": "Least Functionality — the organization configures the system to provide only "
                    "essential capabilities, restricting unnecessary functions, ports, and services."},
    {"pref_label": "SR-4", "notation": "SR-4",
     "definition": "Provenance — the organization documents, monitors, and maintains provenance "
                    "data for systems, system components, and associated data."},
]

_ISO_27001_CONCEPTS: List[Dict[str, Any]] = [
    {"pref_label": "A.9.2.3", "notation": "A.9.2.3",
     "definition": "The allocation and use of privileged access rights shall be restricted and controlled."},
    {"pref_label": "A.9.2.4", "notation": "A.9.2.4",
     "definition": "The allocation of secret authentication information shall be controlled "
                    "through a formal management process."},
    {"pref_label": "A.9.4.3", "notation": "A.9.4.3",
     "definition": "Password management systems shall be interactive and ensure quality passwords."},
    {"pref_label": "A.10.1.1", "notation": "A.10.1.1",
     "definition": "A policy on the use of cryptographic controls shall be developed and "
                    "implemented to protect information."},
    {"pref_label": "A.12.4.1", "notation": "A.12.4.1",
     "definition": "Event logs recording user activities, exceptions, faults, and information "
                    "security events shall be produced, kept, and regularly reviewed."},
    {"pref_label": "A.13.1.1", "notation": "A.13.1.1",
     "definition": "Networks shall be managed and controlled to protect information in systems and applications."},
    {"pref_label": "A.13.1.3", "notation": "A.13.1.3",
     "definition": "Groups of information services, users, and information systems shall be "
                    "segregated on networks."},
    {"pref_label": "A.14.2.2", "notation": "A.14.2.2",
     "definition": "Changes to systems within the development lifecycle shall be controlled by "
                    "the use of formal change control procedures."},
    {"pref_label": "A.14.2.4", "notation": "A.14.2.4",
     "definition": "Restrictions on changes to software packages shall be discouraged, limited "
                    "to necessary changes, and strictly controlled."},
]

# ─────────────────────────────────────────────────────────────────────────────
# sox_risk_category — sox_scoping_tool.py's own 5-term risk_categories
# vocabulary (verbatim from its scoping data), kept as its OWN scheme rather
# than merged into risk_category — see the STRM reconciliation below for why.
# ─────────────────────────────────────────────────────────────────────────────

_SOX_RISK_CATEGORY_CONCEPTS: List[Dict[str, Any]] = [
    {"pref_label": "Financial",
     "definition": "sox_scoping_tool.py's broadest category — used for financial statement "
                    "line items and processes generally, spanning what risk_category splits "
                    "into Financial Reporting and Revenue."},
    {"pref_label": "Macro",
     "definition": "Macroeconomic conditions (rates, credit, inflation, currency) as a SOX "
                    "scoping factor — used the same way risk-engine.js/risks_as_code.py treat "
                    "'Macro' as a score MODIFIER, not a risk category in its own right."},
    {"pref_label": "Regulatory",
     "definition": "Regulatory/legal exposure as a SOX scoping factor — spans what "
                    "risk_category splits into Compliance, Trade Compliance, and Legal."},
    {"pref_label": "Operational",
     "definition": "Day-to-day process/operational risk as a SOX scoping factor."},
    {"pref_label": "Strategic",
     "definition": "Strategic risk as a SOX scoping factor."},
]

SEED_CONCEPTS: Dict[str, List[Dict[str, Any]]] = {
    "risk_category": _RISK_CATEGORY_CONCEPTS,
    "enterprise_domain": _ENTERPRISE_DOMAIN_CONCEPTS,
    "coso_erm": _COSO_ERM_COMPONENTS + _COSO_ERM_PRINCIPLES,
    "coso_icif": _COSO_ICIF_CONCEPTS,
    "soc2": _SOC2_CONCEPTS,
    "nist_800_53": _NIST_800_53_CONCEPTS,
    "iso_27001": _ISO_27001_CONCEPTS,
    "sox_risk_category": _SOX_RISK_CATEGORY_CONCEPTS,
}

# ─────────────────────────────────────────────────────────────────────────────
# Curated STRM relations — enterprise_domain -> risk_category.
#
# Not exhaustive by design: only pairs with a real, checked correspondence are
# asserted. A domain/category pair with no row here has NOT been reviewed —
# that is different from `no_relationship`, which asserts a checked negative
# (see e.g. the SOX "Macro" reconciliation, a separate follow-on slice, which
# DOES need no_relationship rows because that reconciliation was explicitly
# worked through). ESG has no domain relation in this slice: none of the 9
# domains' keyword lists cover environmental/social/governance terms, so
# asserting one here would be a guess, not a finding.
# ─────────────────────────────────────────────────────────────────────────────

SEED_RELATIONS: List[Dict[str, Any]] = [
    {"from_scheme": "enterprise_domain", "from_pref_label": "Identity & Access Management",
     "to_scheme": "risk_category", "to_pref_label": "Cybersecurity",
     "strm_type": "intersects_with", "strength": 0.700,
     "rationale": "IAM is part of an org's cyber posture but Cybersecurity also covers "
                  "breach/malware/encryption risk outside access control."},
    {"from_scheme": "enterprise_domain", "from_pref_label": "Financial Reporting & Controls",
     "to_scheme": "risk_category", "to_pref_label": "Financial Reporting",
     "strm_type": "equal", "strength": 0.950,
     "rationale": "Same conceptual scope: accuracy/integrity of financial reporting and its controls."},
    {"from_scheme": "enterprise_domain", "from_pref_label": "Cyber Security & Data Protection",
     "to_scheme": "risk_category", "to_pref_label": "Cybersecurity",
     "strm_type": "equal", "strength": 0.950,
     "rationale": "Same conceptual scope: unauthorized access/breach/compromise of systems or data."},
    {"from_scheme": "enterprise_domain", "from_pref_label": "Third-Party & Vendor Risk",
     "to_scheme": "risk_category", "to_pref_label": "Supply",
     "strm_type": "equal", "strength": 0.900,
     "rationale": "_keyword_domain's own keyword list (vendor/supplier/third-party/supply/outsourc) "
                  "matches Supply's scope directly."},
    {"from_scheme": "enterprise_domain", "from_pref_label": "Operational Resilience",
     "to_scheme": "risk_category", "to_pref_label": "Operational",
     "strm_type": "subset_of", "strength": 0.750,
     "rationale": "Continuity/disaster-recovery is one form of operational risk; Operational "
                  "is broader (process failure, execution risk generally)."},
    {"from_scheme": "enterprise_domain", "from_pref_label": "Regulatory & Compliance",
     "to_scheme": "risk_category", "to_pref_label": "Compliance",
     "strm_type": "intersects_with", "strength": 0.800,
     "rationale": "Domain spans privacy law (GDPR/CCPA), SOX, and general regulatory penalty; "
                  "overlaps Compliance but also Trade Compliance and Legal below."},
    {"from_scheme": "enterprise_domain", "from_pref_label": "Regulatory & Compliance",
     "to_scheme": "risk_category", "to_pref_label": "Trade Compliance",
     "strm_type": "intersects_with", "strength": 0.500,
     "rationale": "Export-control/sanctions matters can surface under this domain's keyword match "
                  "('regulatory') without being export-specific."},
    {"from_scheme": "enterprise_domain", "from_pref_label": "Regulatory & Compliance",
     "to_scheme": "risk_category", "to_pref_label": "Legal",
     "strm_type": "intersects_with", "strength": 0.600,
     "rationale": "Domain's keyword list includes 'legal' and 'penalty', overlapping Legal's "
                  "litigation/liability scope without being identical to it."},
    {"from_scheme": "enterprise_domain", "from_pref_label": "Technology & Change Management",
     "to_scheme": "risk_category", "to_pref_label": "Operational",
     "strm_type": "subset_of", "strength": 0.700,
     "rationale": "No dedicated 'Technology' risk category exists; change/deployment/tech risk "
                  "is a form of operational risk in this app's 10-category vocabulary."},
    {"from_scheme": "enterprise_domain", "from_pref_label": "People & Organisational Risk",
     "to_scheme": "risk_category", "to_pref_label": "Operational",
     "strm_type": "subset_of", "strength": 0.700,
     "rationale": "No dedicated 'People' risk category exists; staffing/retention/key-person "
                  "risk is a form of operational risk in this app's 10-category vocabulary."},
    {"from_scheme": "enterprise_domain", "from_pref_label": "Market & Economic Risk",
     "to_scheme": "risk_category", "to_pref_label": "Strategic",
     "strm_type": "intersects_with", "strength": 0.500,
     "rationale": "Macro conditions can drive strategic risk, but risk-engine.js/risks_as_code.py "
                  "deliberately treat 'Macro' as a score MODIFIER, not a risk category in its own "
                  "right — so this is a partial overlap, never `equal` or `subset_of`."},
]

# ─────────────────────────────────────────────────────────────────────────────
# The SOC2 <-> NIST 800-53 <-> ISO 27001 crosswalk — the first the app has
# ever had. One PRIMARY code per framework per control, not a full cross-
# product of every code FRAMEWORK_MAPPINGS lists for that control (several
# controls carry 2 codes on one side) — asserting every combination would
# overstate the claim (e.g. INFRA-001's CC6.1 is about access broadly, not
# specifically encryption-in-transit; forcing it into an encryption-specific
# crosswalk would misrepresent it). Secondary codes (AC-2, AU-3, CM-7, SR-4,
# A.13.1.3, A.9.2.4) get no relation in this slice — an honest gap, not a
# guess, same policy this file states above. INFRA-001/004 and INFRA-002/008
# share the same primary triple; asserting it twice is harmless (upsert).
# ─────────────────────────────────────────────────────────────────────────────

SOC2_NIST_ISO_RELATIONS: List[Dict[str, Any]] = [
    # INFRA-001 / INFRA-004 — SSL/encryption-in-transit
    {"from_scheme": "soc2", "from_pref_label": "CC6.7", "to_scheme": "nist_800_53", "to_pref_label": "SC-8",
     "strm_type": "intersects_with", "strength": 0.800,
     "rationale": "Both restrict/protect transmission of information; CC6.7 also covers movement/removal, SC-8 is transmission-specific (INFRA-001/004)."},
    {"from_scheme": "soc2", "from_pref_label": "CC6.7", "to_scheme": "iso_27001", "to_pref_label": "A.10.1.1",
     "strm_type": "intersects_with", "strength": 0.700,
     "rationale": "CC6.7's transmission restriction and A.10.1.1's cryptographic-controls policy both address protecting data in transit, from different angles (control vs. policy) (INFRA-001/004)."},
    {"from_scheme": "nist_800_53", "from_pref_label": "SC-8", "to_scheme": "iso_27001", "to_pref_label": "A.10.1.1",
     "strm_type": "intersects_with", "strength": 0.750,
     "rationale": "SC-8's transmission confidentiality/integrity and A.10.1.1's cryptographic-controls policy overlap substantially for TLS-style controls (INFRA-001/004)."},
    # INFRA-002 / INFRA-008 — password/authenticator management
    {"from_scheme": "soc2", "from_pref_label": "CC6.1", "to_scheme": "nist_800_53", "to_pref_label": "IA-5",
     "strm_type": "intersects_with", "strength": 0.600,
     "rationale": "CC6.1 is broad logical-access security; IA-5 is authenticator management specifically — a subset of what CC6.1 covers (INFRA-002/008)."},
    {"from_scheme": "nist_800_53", "from_pref_label": "IA-5", "to_scheme": "iso_27001", "to_pref_label": "A.9.4.3",
     "strm_type": "intersects_with", "strength": 0.850,
     "rationale": "Both are specifically about authenticator/password quality and management — the closest correspondence in this crosswalk (INFRA-002/008)."},
    {"from_scheme": "soc2", "from_pref_label": "CC6.1", "to_scheme": "iso_27001", "to_pref_label": "A.9.4.3",
     "strm_type": "intersects_with", "strength": 0.500,
     "rationale": "CC6.1's broad access-security scope includes password quality, but A.9.4.3 is narrower/password-specific (INFRA-002/008)."},
    # INFRA-003 — privileged/superuser access
    {"from_scheme": "soc2", "from_pref_label": "CC6.3", "to_scheme": "nist_800_53", "to_pref_label": "AC-6",
     "strm_type": "intersects_with", "strength": 0.850,
     "rationale": "Both restrict/review privileged access based on role and necessity (INFRA-003)."},
    {"from_scheme": "nist_800_53", "from_pref_label": "AC-6", "to_scheme": "iso_27001", "to_pref_label": "A.9.2.3",
     "strm_type": "intersects_with", "strength": 0.850,
     "rationale": "Least privilege (AC-6) and restriction/control of privileged access rights (A.9.2.3) describe the same control intent (INFRA-003)."},
    {"from_scheme": "soc2", "from_pref_label": "CC6.3", "to_scheme": "iso_27001", "to_pref_label": "A.9.2.3",
     "strm_type": "intersects_with", "strength": 0.800,
     "rationale": "Both restrict and periodically review privileged/elevated access (INFRA-003)."},
    # INFRA-005 — connection/activity logging
    {"from_scheme": "soc2", "from_pref_label": "CC7.2", "to_scheme": "nist_800_53", "to_pref_label": "AU-2",
     "strm_type": "intersects_with", "strength": 0.650,
     "rationale": "CC7.2 is anomaly-detection monitoring generally; AU-2 is the specific practice of identifying loggable event types that feeds it (INFRA-005)."},
    {"from_scheme": "nist_800_53", "from_pref_label": "AU-2", "to_scheme": "iso_27001", "to_pref_label": "A.12.4.1",
     "strm_type": "intersects_with", "strength": 0.800,
     "rationale": "Both concern producing and reviewing event/activity logs (INFRA-005)."},
    {"from_scheme": "soc2", "from_pref_label": "CC7.2", "to_scheme": "iso_27001", "to_pref_label": "A.12.4.1",
     "strm_type": "intersects_with", "strength": 0.700,
     "rationale": "CC7.2's anomaly monitoring and A.12.4.1's event logging/review serve the same detective-control purpose (INFRA-005)."},
    # INFRA-006 — network/boundary exposure
    {"from_scheme": "soc2", "from_pref_label": "CC6.6", "to_scheme": "nist_800_53", "to_pref_label": "SC-7",
     "strm_type": "intersects_with", "strength": 0.800,
     "rationale": "Both protect against threats/exposure from outside the system's boundary (INFRA-006)."},
    {"from_scheme": "nist_800_53", "from_pref_label": "SC-7", "to_scheme": "iso_27001", "to_pref_label": "A.13.1.1",
     "strm_type": "intersects_with", "strength": 0.750,
     "rationale": "Boundary protection (SC-7) and network management/control (A.13.1.1) describe closely related network-security intent (INFRA-006)."},
    {"from_scheme": "soc2", "from_pref_label": "CC6.6", "to_scheme": "iso_27001", "to_pref_label": "A.13.1.1",
     "strm_type": "intersects_with", "strength": 0.700,
     "rationale": "Both address controlling exposure of systems/networks to outside threats (INFRA-006)."},
    # INFRA-007 — change management
    {"from_scheme": "soc2", "from_pref_label": "CC8.1", "to_scheme": "nist_800_53", "to_pref_label": "CM-3",
     "strm_type": "intersects_with", "strength": 0.800,
     "rationale": "Both require documented, tested, approved change control before implementation (INFRA-007)."},
    {"from_scheme": "nist_800_53", "from_pref_label": "CM-3", "to_scheme": "iso_27001", "to_pref_label": "A.14.2.2",
     "strm_type": "intersects_with", "strength": 0.800,
     "rationale": "Configuration change control (CM-3) and formal development-lifecycle change control (A.14.2.2) describe the same control intent (INFRA-007)."},
    {"from_scheme": "soc2", "from_pref_label": "CC8.1", "to_scheme": "iso_27001", "to_pref_label": "A.14.2.2",
     "strm_type": "intersects_with", "strength": 0.750,
     "rationale": "Both require formal, documented change control procedures before a change is implemented (INFRA-007)."},
]

# ─────────────────────────────────────────────────────────────────────────────
# sox_risk_category <-> risk_category reconciliation — closes the "two
# unreconciled category vocabularies" defect from the original ontology
# review. sox_scoping_tool.py itself is NOT edited; its 5 terms become their
# own scheme, related here.
# ─────────────────────────────────────────────────────────────────────────────

SOX_RECONCILIATION_RELATIONS: List[Dict[str, Any]] = [
    {"from_scheme": "sox_risk_category", "from_pref_label": "Financial",
     "to_scheme": "risk_category", "to_pref_label": "Financial Reporting",
     "strm_type": "intersects_with", "strength": 0.700,
     "rationale": "SOX 'Financial' spans both financial-statement accuracy and revenue-recognition risk — "
                  "it is not identical to the narrower Financial Reporting category alone."},
    {"from_scheme": "sox_risk_category", "from_pref_label": "Financial",
     "to_scheme": "risk_category", "to_pref_label": "Revenue",
     "strm_type": "intersects_with", "strength": 0.600,
     "rationale": "SOX 'Financial' also covers revenue-recognition-driven line items (e.g. deferred_revenue), "
                  "overlapping Revenue without being identical to it."},
    {"from_scheme": "sox_risk_category", "from_pref_label": "Regulatory",
     "to_scheme": "risk_category", "to_pref_label": "Compliance",
     "strm_type": "superset_of", "strength": 0.750,
     "rationale": "SOX 'Regulatory' is a single broad scoping factor covering everything the "
                  "10-category vocabulary splits into Compliance, Trade Compliance, and Legal."},
    {"from_scheme": "sox_risk_category", "from_pref_label": "Regulatory",
     "to_scheme": "risk_category", "to_pref_label": "Trade Compliance",
     "strm_type": "superset_of", "strength": 0.700,
     "rationale": "Same broad-vs-narrow relationship as Regulatory->Compliance above, applied to export/sanctions risk."},
    {"from_scheme": "sox_risk_category", "from_pref_label": "Regulatory",
     "to_scheme": "risk_category", "to_pref_label": "Legal",
     "strm_type": "superset_of", "strength": 0.700,
     "rationale": "Same broad-vs-narrow relationship as Regulatory->Compliance above, applied to litigation/legal risk."},
    {"from_scheme": "sox_risk_category", "from_pref_label": "Operational",
     "to_scheme": "risk_category", "to_pref_label": "Operational",
     "strm_type": "equal", "strength": 0.900,
     "rationale": "Same conceptual scope: day-to-day process/execution risk."},
    {"from_scheme": "sox_risk_category", "from_pref_label": "Strategic",
     "to_scheme": "risk_category", "to_pref_label": "Strategic",
     "strm_type": "equal", "strength": 0.900,
     "rationale": "Same conceptual scope: risk to achievement of business objectives/strategy."},
]

# SOX 'Macro' is asserted `no_relationship` against every risk_category — a
# checked negative, not an unreviewed gap. Generated (not hand-typed 10 times)
# because the claim and citation are identical for all 10; see
# risks_as_code.py's treatment of Macro as a score MODIFIER, never a category.
SOX_RECONCILIATION_RELATIONS += [
    {"from_scheme": "sox_risk_category", "from_pref_label": "Macro",
     "to_scheme": "risk_category", "to_pref_label": category,
     "strm_type": "no_relationship", "strength": 0.000,
     "rationale": "SOX 'Macro' is a scoping factor for macroeconomic conditions; "
                  "risk-engine.js/risks_as_code.py deliberately treat 'Macro' as a score "
                  "MODIFIER applied across categories, never a risk category in its own right — "
                  "checked and confirmed no direct correspondence to any of the 10 categories."}
    for category in (
        "Revenue", "Operational", "Financial Reporting", "Supply", "Cybersecurity",
        "Trade Compliance", "ESG", "Compliance", "Legal", "Strategic",
    )
]

SEED_RELATIONS += SOC2_NIST_ISO_RELATIONS + SOX_RECONCILIATION_RELATIONS
