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
only, per the ontology plan's "smallest shippable slice"). COSO ERM/IC-IF, SOX,
PaC process, and SCF-imported schemes are follow-on work, added the same way.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Seeded in this order — concepts in an earlier scheme are available as
# broader-concept parents for concepts in a later one. Neither scheme in this
# first slice uses broader_id (both are flat), but the order is fixed now so
# adding a hierarchical scheme later doesn't require reordering existing ones.
SEED_ORDER: List[str] = ["risk_category", "enterprise_domain"]

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

SEED_CONCEPTS: Dict[str, List[Dict[str, Any]]] = {
    "risk_category": _RISK_CATEGORY_CONCEPTS,
    "enterprise_domain": _ENTERPRISE_DOMAIN_CONCEPTS,
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
