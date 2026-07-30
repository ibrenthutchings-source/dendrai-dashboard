#!/usr/bin/env python3
"""
Risk Register Review API
Endpoints for internal risk register management, external framework ingestion,
and risk-to-control mapping with optional AI-powered recommendations.

Routes:
    GET  /risk-register/controls               Master control library
    POST /risk-register/controls/recommend     AI-powered control recommendations
    POST /risk-register/framework-search       Search external framework risk catalogs
    GET  /risk-register/reviews                List review sessions
    POST /risk-register/reviews                Create a new review session
    GET  /risk-register/reviews/{review_id}    Get review with risk states
    PUT  /risk-register/reviews/{review_id}/risks  Bulk-upsert risk states
    POST /risk-register/reviews/{review_id}/complete  Mark review complete
    POST /risk-register/reviews/{review_id}/generate-cac  Generate CaC from this review's risk<->control mappings
    POST /risk-register/convert-to-code        Convert reviewed risks to YAML
"""

import io
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

import db
from pac_endpoints import _controls_to_rego
from auth_endpoints import require_screen_permission

logger = logging.getLogger(__name__)

# Router-level: primary home is the "Risk & Control Ledger" screen (nav id
# "rrreview") — see auth_endpoints.require_screen_permission's docstring.
# Note: risk-register data also renders inside other screens (Risk Radar,
# Risk Sankey/graph views); if an admin ever restricts "rrreview" for a
# role, watch for those cross-screen views losing data too.
router = APIRouter(prefix="/risk-register", tags=["risk-register"],
                    dependencies=[Depends(require_screen_permission("rrreview"))])


# ─────────────────────────────────────────────────────────────────────────────
# Default control library (seeds controls_library table on first startup)
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_CONTROLS: List[Dict[str, Any]] = [
    # Financial controls
    {"ref": "FC-01", "framework": "Internal", "name": "Revenue Recognition Controls",      "category": "Financial",       "domain": "Finance",    "description": "Controls over revenue recognition timing to prevent misstatement"},
    {"ref": "FC-02", "framework": "Internal", "name": "Financial Close Reconciliation",     "category": "Financial",       "domain": "Finance",    "description": "Period-end reconciliation procedures for material accounts"},
    {"ref": "FC-03", "framework": "SOC 2",    "name": "Segregation of Financial Duties",   "category": "Financial",       "domain": "Finance",    "description": "Segregation of duties for payment and approval workflows"},
    {"ref": "FC-04", "framework": "Internal", "name": "Fraud Risk Assessment",              "category": "Financial",       "domain": "Finance",    "description": "Annual fraud risk assessment aligned to Beneish M-Score indicators"},
    # Access & identity
    {"ref": "AC-01", "framework": "Internal",        "name": "Access Control Policy",       "category": "Access Control",  "domain": "IT",         "description": "Documented access control policy reviewed annually"},
    {"ref": "AC-02", "framework": "NIST SP 800-53",  "name": "Account Management",          "category": "Access Control",  "domain": "IT",         "description": "Lifecycle management of user accounts including provisioning and de-provisioning"},
    {"ref": "AC-03", "framework": "NIST SP 800-53",  "name": "Access Enforcement",          "category": "Access Control",  "domain": "IT",         "description": "Enforce approved authorisations for logical access"},
    {"ref": "AC-04", "framework": "CIS Controls",    "name": "Privileged Access Management","category": "Access Control",  "domain": "IT",         "description": "Inventory and control of privileged accounts with MFA enforcement"},
    {"ref": "AC-05", "framework": "SOC 2",           "name": "Logical Access Review",       "category": "Access Control",  "domain": "IT",         "description": "Quarterly review of logical access rights for in-scope systems"},
    # Security controls
    {"ref": "SC-01", "framework": "ISO/IEC 27001",  "name": "Information Security Policy",  "category": "Security",        "domain": "IT",         "description": "Board-approved information security policy with annual review cycle"},
    {"ref": "SC-02", "framework": "CIS Controls",   "name": "Data Protection & Encryption","category": "Security",        "domain": "IT",         "description": "Encryption of data at rest and in transit for sensitive information"},
    {"ref": "SC-03", "framework": "NIST SP 800-53", "name": "Incident Response Plan",       "category": "Security",        "domain": "IT",         "description": "Documented and tested incident response procedures"},
    {"ref": "SC-04", "framework": "ISO/IEC 27001",  "name": "Vulnerability Management",     "category": "Security",        "domain": "IT",         "description": "Regular vulnerability scanning and patch management program"},
    {"ref": "SC-05", "framework": "SOC 2",          "name": "Change Management Controls",   "category": "Security",        "domain": "IT",         "description": "Formal change management process for production systems"},
    # Risk management
    {"ref": "RM-01", "framework": "Internal",       "name": "Risk Assessment Process",      "category": "Risk Management", "domain": "Operational","description": "Documented enterprise risk identification and assessment process"},
    {"ref": "RM-02", "framework": "ISO/IEC 27001",  "name": "Risk Treatment Plan",          "category": "Risk Management", "domain": "Operational","description": "Documented risk treatment decisions with assigned owners and deadlines"},
    {"ref": "RM-03", "framework": "Internal",       "name": "Risk Appetite Framework",      "category": "Risk Management", "domain": "Operational","description": "Board-approved risk appetite statement with quantitative thresholds"},
    {"ref": "RM-04", "framework": "COSO ERM",       "name": "Emerging Risk Monitoring",     "category": "Risk Management", "domain": "Operational","description": "Quarterly horizon-scanning process for emerging and macro risks"},
    # Operational controls
    {"ref": "OP-01", "framework": "Internal",       "name": "Business Continuity Plan",     "category": "Operational",     "domain": "Operational","description": "Tested business continuity and disaster recovery procedures"},
    {"ref": "OP-02", "framework": "ISO/IEC 27001",  "name": "Supplier Risk Management",     "category": "Operational",     "domain": "Operational","description": "Third-party risk assessment and ongoing monitoring program"},
    {"ref": "OP-03", "framework": "Internal",       "name": "Key Person Dependencies",      "category": "Operational",     "domain": "HR",         "description": "Identification and mitigation of key person dependency risks"},
    # Compliance
    {"ref": "CM-01", "framework": "SOC 2",          "name": "Compliance Monitoring Program","category": "Compliance",      "domain": "Legal",      "description": "Ongoing monitoring of regulatory requirements and compliance status"},
    {"ref": "CM-02", "framework": "Internal",       "name": "Regulatory Change Management", "category": "Compliance",      "domain": "Legal",      "description": "Process for tracking and responding to regulatory changes"},
    {"ref": "CM-03", "framework": "SOC 2",          "name": "Privacy Controls",             "category": "Compliance",      "domain": "Legal",      "description": "Data privacy controls aligned to applicable regulations (GDPR, CCPA)"},
    # Vendor & supply chain
    {"ref": "VM-01", "framework": "CIS Controls",   "name": "Vendor Security Assessment",   "category": "Vendor",          "domain": "Operational","description": "Security assessments for critical and high-risk vendors"},
    {"ref": "VM-02", "framework": "Internal",       "name": "Supply Chain Resilience",      "category": "Vendor",          "domain": "Operational","description": "Supplier diversification and concentration risk monitoring"},
    # HR
    {"ref": "HR-01", "framework": "Internal",       "name": "Security Awareness Training",  "category": "HR",              "domain": "HR",         "description": "Annual mandatory security awareness training for all employees"},
    {"ref": "HR-02", "framework": "Internal",       "name": "Background Screening",         "category": "HR",              "domain": "HR",         "description": "Pre-employment background screening for sensitive roles"},
    # ISO/IEC 42001 AI management system
    {"ref": "AI-01", "framework": "ISO/IEC 42001", "name": "AI System Impact Assessment",       "category": "AI Governance",   "domain": "Technology", "description": "Structured assessment of AI system impacts on people, processes, and society"},
    {"ref": "AI-02", "framework": "ISO/IEC 42001", "name": "AI Lifecycle Management",           "category": "AI Governance",   "domain": "Technology", "description": "Governance controls across the full AI system development and deployment lifecycle"},
    {"ref": "AI-03", "framework": "ISO/IEC 42001", "name": "AI Training Data Governance",       "category": "AI Governance",   "domain": "Technology", "description": "Controls ensuring training data quality, provenance, and bias mitigation"},
    {"ref": "AI-04", "framework": "ISO/IEC 42001", "name": "AI Transparency & Explainability",  "category": "AI Governance",   "domain": "Technology", "description": "Mechanisms to explain AI outputs and decisions to relevant stakeholders"},
    {"ref": "AI-05", "framework": "ISO/IEC 42001", "name": "Third-Party AI Tool Assessment",    "category": "AI Governance",   "domain": "Technology", "description": "Due diligence and ongoing monitoring for externally-sourced AI services"},
    {"ref": "AI-06", "framework": "ISO/IEC 42001", "name": "Human Oversight of AI Systems",     "category": "AI Governance",   "domain": "Technology", "description": "Defined human review points and override mechanisms for AI-assisted decisions"},
    # NIST AI RMF (Govern / Map / Measure / Manage) — distinct from ISO/IEC 42001 above:
    # 42001 is a certifiable AI management-system standard, NIST AI RMF is the dominant
    # US voluntary framework AI-governance committees report against. Both belong in the
    # library since GRC and AI-governance buyers ask for different ones.
    {"ref": "AI-07", "framework": "NIST AI RMF", "name": "AI System Inventory & Risk Tiering", "category": "Map",     "domain": "Technology", "description": "Maintained register of AI systems and use cases with assigned risk tier, data sensitivity, and owner"},
    {"ref": "AI-08", "framework": "NIST AI RMF", "name": "AI Governance Accountability",       "category": "Govern",   "domain": "Technology", "description": "Documented AI risk policies, roles, and executive accountability for AI risk tolerance decisions"},
    {"ref": "AI-09", "framework": "NIST AI RMF", "name": "AI Performance & Bias Measurement",  "category": "Measure",  "domain": "Technology", "description": "Testing of AI system performance, fairness, and drift against validated baselines prior to and during deployment"},
    {"ref": "AI-10", "framework": "NIST AI RMF", "name": "AI Incident Response & Human Override","category": "Manage", "domain": "Technology", "description": "Defined incident response procedures and human-in-the-loop override mechanisms for AI system failures"},
]

# Map default refs for quick lookup (used in fallback and AI prompts built at import time)
_CONTROL_MAP = {c["ref"]: c for c in _DEFAULT_CONTROLS}


def _get_controls_live() -> List[Dict[str, Any]]:
    """Return controls from DB if available, otherwise fall back to defaults."""
    if db.is_available():
        db_controls = db.get_controls_library()
        if db_controls:
            return db_controls
    return _DEFAULT_CONTROLS


def _get_control_map_live() -> Dict[str, Dict[str, Any]]:
    return {c["ref"]: c for c in _get_controls_live()}

# ─────────────────────────────────────────────────────────────────────────────
# Cached system prompts for direct AI calls
# Built once at import time so the stable content qualifies for prompt caching.
# ─────────────────────────────────────────────────────────────────────────────

def _build_controls_system() -> str:
    controls = _get_controls_live()
    return (
        "You are a GRC expert. For the risk provided by the user, recommend the 3-5 most "
        "relevant controls from the library below. Return ONLY a JSON array of control refs "
        "(e.g. [\"AC-02\",\"SC-01\"]).\n\n"
        "Available controls:\n"
        + "\n".join(f"{c['ref']}: {c['name']}" for c in controls)
    )

_CONTROLS_SYSTEM = _build_controls_system()

_FRAMEWORK_SYSTEM = (
    "You are a GRC expert. Generate a realistic risk catalog for the framework or standard "
    "specified in the user message.\n\n"
    "Return a JSON array of 6 to 8 risks. Each risk must have these exact keys:\n"
    '  "id"            — short reference code unique within this framework (e.g. "SOX-404-01")\n'
    '  "name"          — concise risk statement: one sentence under 120 chars, written as\n'
    '                    "Inadequate X [causes / enables / leads to] Y" or similar causal form\n'
    '  "category"      — the risk domain relevant to this framework (e.g. "Financial Reporting")\n'
    '  "control_family"— the specific control area or section reference within the framework\n\n'
    "Return ONLY valid JSON — no markdown fences, no commentary.\n"
    'Example: [{"id":"FW-01","name":"Inadequate controls over X allow Y","category":"Z","control_family":"Section 1"}]'
)

_DOMAIN_SYSTEM = (
    "You are a GRC expert. For each risk provided, assign it to the most appropriate "
    "enterprise risk domain using a concise 3-6 word common area name.\n\n"
    "Preferred domain names (use these where they fit, create a new one only if none applies):\n"
    "  'Identity & Access Management', 'Financial Reporting & Controls',\n"
    "  'Cyber Security & Data Protection', 'Third-Party & Vendor Risk',\n"
    "  'Operational Resilience', 'Regulatory & Compliance',\n"
    "  'Technology & Change Management', 'People & Organisational Risk',\n"
    "  'Market & Economic Risk'.\n\n"
    "Return ONLY a JSON object mapping ref → domain name. No markdown, no commentary.\n"
    'Example: {"RISK-01": "Identity & Access Management", "RISK-02": "Financial Reporting & Controls"}'
)

_SCORE_SYSTEM = (
    "You are a risk quantification expert using a 5×5 risk matrix (likelihood 1–5 × impact 1–5). "
    "Score each risk on a residual basis (1–25 scale). "
    "Bands: score ≥ 15 = red, 9–14 = amber, < 9 = green.\n\n"
    "Return ONLY a JSON array. Each element must have exactly:\n"
    '  "id"    — the risk id exactly as given\n'
    '  "score" — numeric float, e.g. 12.0\n'
    '  "rag"   — one of "red", "amber", "green"\n\n'
    "Return ONLY valid JSON — no markdown fences, no commentary."
)


# ─────────────────────────────────────────────────────────────────────────────
# Default framework risk catalogs (seeds framework_risk_catalogs on first startup)
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_FRAMEWORK_CATALOGS: Dict[str, List[Dict[str, Any]]] = {
    "NIST SP 800-53": [
        {"id": "NIST-AC-2",  "name": "Inadequate account lifecycle management exposes systems to unauthorised access", "category": "Access Control",  "source_framework": "NIST SP 800-53", "control_family": "AC", "score": None, "rag": None},
        {"id": "NIST-CM-6",  "name": "Misconfigured system settings create exploitable security gaps",                 "category": "Configuration",   "source_framework": "NIST SP 800-53", "control_family": "CM", "score": None, "rag": None},
        {"id": "NIST-IR-4",  "name": "Insufficient incident handling capability delays breach containment",            "category": "Incident Response","source_framework": "NIST SP 800-53", "control_family": "IR", "score": None, "rag": None},
        {"id": "NIST-RA-3",  "name": "Ad-hoc risk assessments miss systemic vulnerabilities in critical systems",     "category": "Risk Assessment",  "source_framework": "NIST SP 800-53", "control_family": "RA", "score": None, "rag": None},
        {"id": "NIST-SI-7",  "name": "Lack of software integrity verification enables supply-chain compromise",        "category": "System Integrity", "source_framework": "NIST SP 800-53", "control_family": "SI", "score": None, "rag": None},
        {"id": "NIST-AU-9",  "name": "Audit log tampering risk undermines forensic investigation capability",          "category": "Audit",            "source_framework": "NIST SP 800-53", "control_family": "AU", "score": None, "rag": None},
    ],
    "ISO/IEC 27001": [
        {"id": "ISO-A.9.1",  "name": "Poorly defined access control policies allow privilege escalation",             "category": "Access Control",   "source_framework": "ISO/IEC 27001", "control_family": "A.9",  "score": None, "rag": None},
        {"id": "ISO-A.12.1", "name": "Unmanaged operational change increases risk of service disruption",             "category": "Operations",       "source_framework": "ISO/IEC 27001", "control_family": "A.12", "score": None, "rag": None},
        {"id": "ISO-A.15.1", "name": "Unvetted supplier relationships introduce unmanaged third-party risk",          "category": "Supplier",         "source_framework": "ISO/IEC 27001", "control_family": "A.15", "score": None, "rag": None},
        {"id": "ISO-A.16.1", "name": "Slow information security incident response amplifies regulatory exposure",      "category": "Incident",         "source_framework": "ISO/IEC 27001", "control_family": "A.16", "score": None, "rag": None},
        {"id": "ISO-A.17.1", "name": "Untested continuity plans fail during actual disruption events",                "category": "Continuity",       "source_framework": "ISO/IEC 27001", "control_family": "A.17", "score": None, "rag": None},
        {"id": "ISO-A.18.1", "name": "Regulatory compliance gaps create penalty and reputational risk",               "category": "Compliance",       "source_framework": "ISO/IEC 27001", "control_family": "A.18", "score": None, "rag": None},
    ],
    "CIS Controls": [
        {"id": "CIS-1.1",   "name": "Unmanaged hardware assets create invisible attack surface",                      "category": "Asset Management", "source_framework": "CIS Controls", "control_family": "CIS 1", "score": None, "rag": None},
        {"id": "CIS-5.1",   "name": "Uncontrolled administrative accounts expose critical infrastructure",            "category": "Access Control",   "source_framework": "CIS Controls", "control_family": "CIS 5", "score": None, "rag": None},
        {"id": "CIS-6.1",   "name": "Insufficient access control management enables lateral movement post-breach",    "category": "Access Control",   "source_framework": "CIS Controls", "control_family": "CIS 6", "score": None, "rag": None},
        {"id": "CIS-13.1",  "name": "Inadequate network monitoring delays detection of anomalous activity",           "category": "Monitoring",       "source_framework": "CIS Controls", "control_family": "CIS 13","score": None, "rag": None},
        {"id": "CIS-16.1",  "name": "Insecure application development practices introduce exploitable vulnerabilities","category": "AppSec",           "source_framework": "CIS Controls", "control_family": "CIS 16","score": None, "rag": None},
    ],
    "SOC 2": [
        {"id": "SOC-CC1.1", "name": "Weak control environment culture enables management override",                   "category": "Governance",       "source_framework": "SOC 2", "control_family": "CC1", "score": None, "rag": None},
        {"id": "SOC-CC6.1", "name": "Insufficient logical access controls expose sensitive customer data",            "category": "Access Control",   "source_framework": "SOC 2", "control_family": "CC6", "score": None, "rag": None},
        {"id": "SOC-CC7.1", "name": "Undetected system operations anomalies lead to prolonged service failure",      "category": "Operations",       "source_framework": "SOC 2", "control_family": "CC7", "score": None, "rag": None},
        {"id": "SOC-CC8.1", "name": "Uncontrolled software changes introduce defects into production systems",       "category": "Change Management","source_framework": "SOC 2", "control_family": "CC8", "score": None, "rag": None},
        {"id": "SOC-CC9.1", "name": "Unmitigated vendor concentration risk triggers availability commitments breach","category": "Vendor",           "source_framework": "SOC 2", "control_family": "CC9", "score": None, "rag": None},
    ],
    "ISO/IEC 42001": [
        {"id": "AI42-A.5.1",  "name": "Unassessed AI system impacts create unforeseen ethical and operational harms",         "category": "AI Impact Assessment", "source_framework": "ISO/IEC 42001", "control_family": "A.5",  "score": None, "rag": None},
        {"id": "AI42-A.6.1",  "name": "Unmanaged AI lifecycle changes introduce regressions in model safety and performance", "category": "AI Lifecycle",          "source_framework": "ISO/IEC 42001", "control_family": "A.6",  "score": None, "rag": None},
        {"id": "AI42-A.7.1",  "name": "Poor training data quality produces biased or inaccurate AI outputs at scale",        "category": "AI Data Governance",    "source_framework": "ISO/IEC 42001", "control_family": "A.7",  "score": None, "rag": None},
        {"id": "AI42-A.8.1",  "name": "Inadequate AI transparency undermines stakeholder trust and regulatory acceptance",    "category": "AI Transparency",       "source_framework": "ISO/IEC 42001", "control_family": "A.8",  "score": None, "rag": None},
        {"id": "AI42-A.9.1",  "name": "Unvetted third-party AI tools introduce unmanaged model and data supply-chain risk",  "category": "Third-Party AI",        "source_framework": "ISO/IEC 42001", "control_family": "A.9",  "score": None, "rag": None},
        {"id": "AI42-A.10.1", "name": "Insufficient human oversight enables unchecked AI decision-making in high-stakes contexts", "category": "Human Oversight", "source_framework": "ISO/IEC 42001", "control_family": "A.10", "score": None, "rag": None},
    ],
    # NIST AI RMF's four functions — GOVERN, MAP, MEASURE, MANAGE — the crosswalk an
    # AI-governance committee reporting against the US framework (rather than, or
    # alongside, ISO/IEC 42001) expects to see. Two risk statements per function.
    "NIST AI RMF": [
        {"id": "NISTAI-GV-1", "name": "Absence of AI governance policy and accountability structures leaves AI risk decisions undocumented and unowned", "category": "AI Governance",      "source_framework": "NIST AI RMF", "control_family": "GOVERN",  "score": None, "rag": None},
        {"id": "NISTAI-GV-2", "name": "Undefined AI risk tolerance allows high-risk model deployments to proceed without executive sign-off",              "category": "AI Risk Tolerance",  "source_framework": "NIST AI RMF", "control_family": "GOVERN",  "score": None, "rag": None},
        {"id": "NISTAI-MP-1", "name": "Unmaintained inventory of AI systems and use cases leaves organization-wide AI risk exposure unmeasured",           "category": "AI Inventory",       "source_framework": "NIST AI RMF", "control_family": "MAP",     "score": None, "rag": None},
        {"id": "NISTAI-MP-2", "name": "Unassessed context and intended use of an AI system produces controls mismatched to its actual risk",              "category": "Context & Use",      "source_framework": "NIST AI RMF", "control_family": "MAP",     "score": None, "rag": None},
        {"id": "NISTAI-MS-1", "name": "Untested AI system performance and fairness metrics allow degraded or biased outputs to reach production",         "category": "AI Testing & Metrics","source_framework": "NIST AI RMF", "control_family": "MEASURE", "score": None, "rag": None},
        {"id": "NISTAI-MS-2", "name": "Absence of ongoing drift monitoring lets AI system behavior silently diverge from its validated baseline",          "category": "AI Monitoring",      "source_framework": "NIST AI RMF", "control_family": "MEASURE", "score": None, "rag": None},
        {"id": "NISTAI-MG-1", "name": "Undefined incident response procedures for AI failures delay containment and stakeholder notification",            "category": "AI Incident Response","source_framework": "NIST AI RMF", "control_family": "MANAGE",  "score": None, "rag": None},
        {"id": "NISTAI-MG-2", "name": "Missing human-in-the-loop controls for high-risk AI decisions allow unchecked automated actions to proceed",        "category": "Human Oversight",    "source_framework": "NIST AI RMF", "control_family": "MANAGE",  "score": None, "rag": None},
    ],
}

# Default matrix / preset framework config (seeds app_config on first startup)
_DEFAULT_MATRIX_FRAMEWORKS = ["ISO/IEC 27001", "ISO/IEC 42001", "NIST AI RMF", "SOC 2", "NIST SP 800-53", "CIS Controls", "COSO ERM"]
_DEFAULT_PRESET_FRAMEWORKS  = ["NIST SP 800-53", "ISO/IEC 27001", "ISO/IEC 42001", "NIST AI RMF", "CIS Controls", "SOC 2"]


def seed_static_data() -> None:
    """Idempotent: seed controls_library, framework_risk_catalogs, and app_config from defaults."""
    if not db.is_available():
        return
    seeded = db.seed_controls_library(_DEFAULT_CONTROLS)
    if seeded:
        logger.info("Seeded %d default controls into controls_library", seeded)

    for fw_name, risks in _DEFAULT_FRAMEWORK_CATALOGS.items():
        db.save_framework_catalog(fw_name, risks)
    logger.info("Seeded %d default framework catalogs into framework_risk_catalogs", len(_DEFAULT_FRAMEWORK_CATALOGS))

    if db.get_app_config("matrix_frameworks") is None:
        db.set_app_config("matrix_frameworks", _DEFAULT_MATRIX_FRAMEWORKS)
    if db.get_app_config("preset_frameworks") is None:
        db.set_app_config("preset_frameworks", _DEFAULT_PRESET_FRAMEWORKS)

# Keyword → control refs for auto-mapping
_AUTO_MAP_RULES = [
    (["revenue", "recognition", "accounting", "financial", "margin", "fraud", "restat"],
     ["FC-01", "FC-02", "FC-03", "FC-04"]),
    (["cyber", "security", "breach", "data", "unauthori", "hack", "phishing"],
     ["SC-01", "SC-02", "SC-03", "SC-04", "AC-02", "AC-05"]),
    (["access", "identity", "privilege", "authentication", "authoris", "logical"],
     ["AC-01", "AC-02", "AC-03", "AC-04", "AC-05"]),
    (["operational", "process", "continuity", "disaster", "recovery", "bcp"],
     ["RM-01", "OP-01"]),
    (["compliance", "regulatory", "legal", "penalty", "gdpr", "ccpa", "sox"],
     ["CM-01", "CM-02", "CM-03"]),
    (["vendor", "supplier", "third.party", "supply.chain", "outsourc"],
     ["VM-01", "VM-02", "OP-02"]),
    (["talent", "people", "key.person", "retention", "staff", "hiring"],
     ["HR-01", "HR-02", "OP-03"]),
    (["macro", "market", "interest", "credit", "inflation", "rate", "currency"],
     ["RM-02", "RM-03", "RM-04"]),
    (["change", "configuration", "deployment", "release", "patch"],
     ["SC-05", "CM-02"]),
    (["incident", "response", "detection", "monitoring", "log"],
     ["SC-03", "SC-04"]),
    (["artificial intelligence", "machine learning", "llm", "generative", "algorithm", "model bias", "explainab", "oversight of ai", "training data", "ai system", "ai risk"],
     ["AI-01", "AI-02", "AI-03", "AI-04", "AI-06", "AI-07", "AI-08", "AI-09", "AI-10"]),
    (["third-party ai", "ai vendor", "ai tool", "ai service", "ai supply"],
     ["AI-05", "VM-01"]),
    (["ai drift", "ai incident", "ai governance", "ai inventory", "human-in-the-loop", "human in the loop"],
     ["AI-07", "AI-09", "AI-10"]),
]


def _auto_map_controls(risk_name: str, risk_category: str) -> List[str]:
    """Return up to 5 best-matching control refs based on keyword matching."""
    combined = (risk_name + " " + (risk_category or "")).lower()
    matched: List[str] = []
    for keywords, control_refs in _AUTO_MAP_RULES:
        if any(kw in combined for kw in keywords):
            for ref in control_refs:
                if ref not in matched:
                    matched.append(ref)
    if not matched:
        matched = ["RM-01"]
    return matched[:5]


# ─────────────────────────────────────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────────────────────────────────────

class RiskStateUpdate(BaseModel):
    risk_ref: str
    original_wording: Optional[str] = None
    current_wording: Optional[str] = None
    included: bool = True
    reason_for_change: Optional[str] = None
    controls_assigned: List[Dict[str, Any]] = []


class ReviewCreateRequest(BaseModel):
    run_id: Optional[int] = None
    review_type: str = "internal"
    framework: Optional[str] = None
    risk_states: List[RiskStateUpdate] = []


class FrameworkSearchRequest(BaseModel):
    query: Optional[str] = None
    frameworks: List[str] = []


class ControlRecommendRequest(BaseModel):
    risk_wording: str
    risk_category: Optional[str] = None
    risk_ref: Optional[str] = None


class ConvertToCodeRequest(BaseModel):
    risks: List[Dict[str, Any]] = []
    review_type: str = "internal"
    framework: Optional[str] = None
    include_controls: bool = True
    review_id: Optional[int] = None


class ApplyWordingRequest(BaseModel):
    run_id: int
    risks: List[Dict[str, Any]] = []  # [{risk_ref, current_wording}, ...]


class CategorizeDomainRequest(BaseModel):
    risks: List[Dict[str, Any]] = []  # [{ref, name, category}, ...]
    run_id: Optional[int] = None      # when set, domains are persisted to risk_scores.assigned_domain


class ScoreFrameworkRisksRequest(BaseModel):
    risks: List[Dict[str, Any]] = []  # [{id, name, category, source_framework}]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

class ControlCreateRequest(BaseModel):
    ref: str
    name: str
    framework: str = "Custom"
    category: str = "Custom"
    domain: str = "Custom"
    description: str = ""


class MatrixConfigUpdate(BaseModel):
    matrix_frameworks: Optional[List[str]] = None
    preset_frameworks: Optional[List[str]] = None


@router.get("/controls")
async def get_controls():
    """Return the control library — served from DB when available, defaults otherwise."""
    controls = _get_controls_live()
    return {"controls": controls, "count": len(controls)}


@router.post("/controls")
async def create_control(req: ControlCreateRequest):
    """Add a new control to the library and persist it to DB."""
    ref = req.ref.strip().upper()
    live_map = _get_control_map_live()
    if ref in live_map:
        from fastapi import status
        return {"saved": False, "detail": f"{ref} already exists in the control library"}
    ctrl = {
        "ref": ref, "framework": req.framework, "name": req.name,
        "category": req.category, "domain": req.domain, "description": req.description,
    }
    if db.is_available():
        db.upsert_control(ctrl)
    # Also update the module-level fallback map so AI prompt stays current within this process
    _CONTROL_MAP[ref] = ctrl
    _DEFAULT_CONTROLS.append(ctrl)
    return {"saved": True, "ref": ref, "control": ctrl}


@router.get("/matrix-config")
async def get_matrix_config():
    """Return MATRIX_FRAMEWORKS and PRESET_FRAMEWORKS (from DB or defaults)."""
    matrix  = db.get_app_config("matrix_frameworks",  _DEFAULT_MATRIX_FRAMEWORKS)  if db.is_available() else _DEFAULT_MATRIX_FRAMEWORKS
    preset  = db.get_app_config("preset_frameworks",  _DEFAULT_PRESET_FRAMEWORKS)   if db.is_available() else _DEFAULT_PRESET_FRAMEWORKS
    return {"matrix_frameworks": matrix, "preset_frameworks": preset}


@router.put("/matrix-config")
async def update_matrix_config(req: MatrixConfigUpdate):
    """Persist updated MATRIX_FRAMEWORKS or PRESET_FRAMEWORKS to DB."""
    saved = {}
    if req.matrix_frameworks is not None:
        if db.is_available():
            db.set_app_config("matrix_frameworks", req.matrix_frameworks)
        saved["matrix_frameworks"] = req.matrix_frameworks
    if req.preset_frameworks is not None:
        if db.is_available():
            db.set_app_config("preset_frameworks", req.preset_frameworks)
        saved["preset_frameworks"] = req.preset_frameworks
    return {"saved": True, **saved}


@router.post("/controls/recommend")
async def recommend_controls(req: ControlRecommendRequest):
    """
    Recommend controls for a risk. Attempts Claude API if available,
    falls back to keyword-based auto-mapping.
    """
    live_map = _get_control_map_live()
    auto_refs = _auto_map_controls(req.risk_wording, req.risk_category or "")
    auto_controls = [live_map[r] for r in auto_refs if r in live_map]

    # Attempt AI-augmented recommendations if ANTHROPIC_API_KEY is set
    ai_summary = None
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            system_text = _build_controls_system()
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=256,
                system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"Risk: {req.risk_wording}\nCategory: {req.risk_category or 'Unknown'}"}],
            )
            import json, re
            raw = msg.content[0].text.strip()
            match = re.search(r'\[.*?\]', raw, re.DOTALL)
            if match:
                ai_refs = json.loads(match.group())
                ai_controls = [live_map[r] for r in ai_refs if r in live_map]
                if ai_controls:
                    ai_summary = f"Recommended by Claude based on risk wording analysis"
                    return {
                        "controls": ai_controls,
                        "source": "ai",
                        "summary": ai_summary,
                        "all_refs": ai_refs,
                    }
    except Exception as exc:
        logger.warning("AI control recommendation failed, falling back to keyword match: %s", exc)

    return {
        "controls": auto_controls,
        "source": "keyword",
        "summary": f"Auto-mapped {len(auto_controls)} controls from keyword analysis",
        "all_refs": auto_refs,
    }


async def _generate_framework_risks_ai(framework: str) -> List[Dict[str, Any]]:
    """Call Claude to generate a realistic risk catalog for an arbitrary framework name."""
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return []
        import anthropic
        import json
        import re
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=[{"type": "text", "text": _FRAMEWORK_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f'Framework: "{framework}"'}],
        )
        raw = msg.content[0].text.strip()
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if not match:
            return []
        risks = json.loads(match.group())
        return [
            {
                "id": r.get("id", f"{framework[:6].upper().replace(' ','-')}-{i+1:02d}"),
                "name": r.get("name", ""),
                "category": r.get("category", "General"),
                "source_framework": framework,
                "control_family": r.get("control_family", framework),
                "score": None,
                "rag": None,
            }
            for i, r in enumerate(risks)
            if r.get("name")
        ]
    except Exception as exc:
        logger.warning("AI framework catalog generation failed for '%s': %s", framework, exc)
        return []


@router.post("/framework-search")
async def search_frameworks(req: FrameworkSearchRequest):
    """
    Search external framework risk catalogs.
    - Preset frameworks (NIST, ISO, CIS, SOC 2) are served from the local catalog.
    - Unknown framework names are sent to Claude to generate a realistic risk catalog.
    - Free-text query with no framework list searches across all preset catalogs.
    """
    results: List[Dict[str, Any]] = []

    for fw_name in req.frameworks:
        catalog = _DEFAULT_FRAMEWORK_CATALOGS.get(fw_name)
        if catalog:
            results.extend(catalog)
        else:
            # Unknown framework — ask Claude to generate a realistic catalog
            ai_risks = await _generate_framework_risks_ai(fw_name)
            results.extend(ai_risks)

    # Free-text query with no explicit frameworks: search preset catalogs by name/content
    if req.query and not req.frameworks:
        q = req.query.lower()
        for fw_name, catalog in _DEFAULT_FRAMEWORK_CATALOGS.items():
            if q in fw_name.lower():
                results.extend(catalog)
            else:
                results.extend([r for r in catalog if q in r["name"].lower()])

    # Deduplicate by id
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for r in results:
        if r["id"] not in seen:
            seen.add(r["id"])
            r = dict(r)
            r["auto_controls"] = _auto_map_controls(r["name"], r.get("category", ""))
            unique.append(r)

    # Persist each framework's risks to the catalog table for cross-session recall
    if unique and db.is_available():
        fw_groups: Dict[str, List] = {}
        for r in unique:
            fw = r.get("source_framework", "")
            if fw:
                fw_groups.setdefault(fw, []).append(r)
        for fw_name, fw_risks in fw_groups.items():
            db.save_framework_catalog(fw_name, fw_risks)

    return {"risks": unique, "count": len(unique)}


@router.get("/framework-catalogs")
async def get_framework_catalogs():
    """Return all framework risk catalogs previously saved to the database."""
    return {"catalogs": db.list_framework_catalogs()}


@router.post("/score-framework-risks")
async def score_framework_risks(req: ScoreFrameworkRisksRequest):
    """
    Score unrated framework risks via AI (5×5 risk matrix, 1–25 scale).
    Falls back to a keyword heuristic if no API key is configured.
    Persists updated scores back into framework_risk_catalogs for cross-session recall.
    """
    if not req.risks:
        return {"scores": {}}

    scores: Dict[str, Dict[str, Any]] = {}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    ai_ok = False
    if api_key:
        try:
            import anthropic
            import json
            import re
            client = anthropic.Anthropic(api_key=api_key)
            risk_list = "\n".join(
                f'{i+1}. id="{r["id"]}" | {r.get("source_framework", "")} — {r.get("name", "")} [{r.get("category", "")}]'
                for i, r in enumerate(req.risks)
            )
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=[{"type": "text", "text": _SCORE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"Score these risks:\n{risk_list}"}],
            )
            raw = msg.content[0].text.strip()
            match = re.search(r'\[.*?\]', raw, re.DOTALL)
            if match:
                for item in json.loads(match.group()):
                    rid = item.get("id")
                    sc  = item.get("score")
                    rag = str(item.get("rag", "")).lower()
                    if rid and sc is not None:
                        sc = float(sc)
                        if rag not in ("red", "amber", "green"):
                            rag = "red" if sc >= 15 else "amber" if sc >= 9 else "green"
                        scores[rid] = {"score": round(sc, 1), "rag": rag}
                ai_ok = bool(scores)
        except Exception as exc:
            logger.warning("AI risk scoring failed, using heuristic: %s", exc)

    # Fill any gaps (AI missed some risks, or AI was unavailable) with a keyword heuristic.
    _HIGH_KW = {"cyber", "breach", "security", "access", "compliance", "incident",
                "fraud", "oversight", "supply chain", "vendor", "ai ", "artificial intelligence"}
    for r in req.risks:
        rid = r.get("id")
        if not rid or rid in scores:   # skip if already scored by AI
            continue
        text = ((r.get("category") or "") + " " + (r.get("name") or "")).lower()
        sc   = 12.0 if any(k in text for k in _HIGH_KW) else 9.0
        scores[rid] = {"score": sc, "rag": "amber"}

    # Persist: merge new scores into existing catalogs and re-save each framework
    if scores and db.is_available():
        existing_cats = db.list_framework_catalogs()
        existing_map  = {cat["framework"]: list(cat.get("risks") or []) for cat in existing_cats}
        fw_groups: Dict[str, List] = {}
        for r in req.risks:
            fw = r.get("source_framework", "")
            if fw:
                fw_groups.setdefault(fw, []).append(r)
        for fw_name, batch in fw_groups.items():
            score_patch = {r["id"]: scores.get(r["id"]) for r in batch}
            existing    = existing_map.get(fw_name, [])
            merged      = []
            seen_ids: set = set()
            for er in existing:
                eid = er["id"]
                seen_ids.add(eid)
                patch = score_patch.get(eid)
                merged.append({**er, **patch} if patch else er)
            for r in batch:
                if r["id"] not in seen_ids:
                    merged.append({**r, **(scores.get(r["id"]) or {})})
            db.save_framework_catalog(fw_name, merged)

    return {"scores": scores}


@router.get("/reviews")
async def list_reviews(run_id: Optional[int] = None):
    """List recent review sessions."""
    return {"reviews": db.list_risk_register_reviews(run_id=run_id)}


@router.post("/reviews")
async def create_review(req: ReviewCreateRequest):
    """Create a new review session, optionally seeding initial risk states."""
    if not db.is_available():
        return {"review_id": None, "saved": False, "detail": "Database not connected"}
    review_id = db.create_risk_register_review(
        run_id=req.run_id,
        review_type=req.review_type,
        framework=req.framework,
    )
    if review_id and req.risk_states:
        db.save_review_risk_states(
            review_id,
            [s.model_dump() for s in req.risk_states],
        )
    return {"review_id": review_id, "saved": review_id is not None}


@router.get("/reviews/{review_id}")
async def get_review(review_id: int):
    """Return a review session with all its risk states."""
    states = db.get_review_risk_states(review_id)
    return {"review_id": review_id, "risk_states": states}


@router.put("/reviews/{review_id}/risks")
async def update_review_risks(review_id: int, states: List[RiskStateUpdate]):
    """Bulk-upsert risk states for a review session."""
    db.save_review_risk_states(review_id, [s.model_dump() for s in states])
    return {"saved": True, "count": len(states)}


@router.post("/reviews/{review_id}/complete")
async def complete_review(review_id: int):
    """Mark a review session as completed."""
    db.complete_risk_register_review(review_id)
    return {"completed": True}


class GenerateCacFromReviewRequest(BaseModel):
    ticker: Optional[str] = None
    run_id: Optional[int] = None


@router.post("/reviews/{review_id}/generate-cac")
async def generate_cac_from_review(review_id: int, req: GenerateCacFromReviewRequest):
    """Generate a Controls-as-Code Rego artifact from the controls actually
    assigned to risks in this review (risk_control_mappings) — the
    auditor-curated relationship, not the whole general control library.
    Each control's Rego block embeds its linked_risks, so the risk<->control
    mapping lives in the artifact itself, not only in a side table. Mirrors
    cac_generate's persistence (controls_as_code_artifacts + embedding), but
    scoped to this review and grounded in real assignments instead of an
    arbitrary controls list the caller has to assemble by hand."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not connected")

    mappings = db.get_risk_control_mappings(review_id=review_id)
    if not mappings:
        return {"generated": False, "reason": "no_controls_assigned", "artifact_id": None}

    linked_risks_by_ctrl: Dict[str, List[str]] = {}
    for m in mappings:
        linked_risks_by_ctrl.setdefault(m["control_ref"], []).append(m["risk_ref"])

    control_map = _get_control_map_live()
    controls = []
    new_to_catalog = []
    for ctrl_ref, risk_refs in linked_risks_by_ctrl.items():
        base = control_map.get(ctrl_ref)
        if base is None:
            base = {
                "ref": ctrl_ref, "name": ctrl_ref, "framework": "Internal — Register-assigned",
                "category": "Uncategorised", "domain": "",
                "description": "Ad-hoc control assigned in the Risk & Controls Register; not yet in the control library.",
            }
            new_to_catalog.append(base)
        controls.append({**base, "ref": ctrl_ref, "linked_risks": sorted(set(risk_refs))})

    content_rego = _controls_to_rego(controls, req.ticker)
    artifact_id = db.save_controls_as_code_artifact(content_rego, req.ticker, req.run_id)

    if artifact_id:
        try:
            db.save_embedding(
                source_table="controls_as_code_artifacts", source_id=artifact_id,
                content_type=db.EMBT_CAC, text=content_rego[:8000],
            )
        except Exception:
            pass  # embedding is non-fatal, same as cac_generate
        # Self-register ad-hoc controls into the canonical catalog, same pattern
        # cac_from_pac already uses, so they show up in the library going forward.
        for c in new_to_catalog:
            db.upsert_catalog_control(c["ref"], c["name"], c.get("description"), source="register")

    return {
        "generated": artifact_id is not None,
        "artifact_id": artifact_id,
        "control_count": len(controls),
        "linked_risk_count": len(mappings),
    }


@router.post("/apply-wording")
async def apply_wording(req: ApplyWordingRequest):
    """Persist reviewed risk wording back to risk_scores for a pipeline run."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not connected — set DATABASE_URL to persist wording changes")
    rows_updated = db.apply_review_wording(req.run_id, req.risks)
    return {"applied": True, "count": len(req.risks), "rows_updated": rows_updated}


@router.post("/categorize-domains")
async def categorize_domains(req: CategorizeDomainRequest):
    """
    Assign each risk to a common enterprise risk domain name.
    Uses Claude when ANTHROPIC_API_KEY is available; falls back to keyword heuristics.
    """
    def _keyword_domain(name: str, category: str) -> str:
        text = (name + " " + (category or "")).lower()
        if any(w in text for w in ["access", "identity", "privilege", "authentication", "authoris", "account", "logical"]):
            return "Identity & Access Management"
        if any(w in text for w in ["revenue", "financial", "accounting", "fraud", "margin", "restat"]):
            return "Financial Reporting & Controls"
        if any(w in text for w in ["cyber", "security", "breach", "hack", "phishing", "encrypt", "vulnerab", "incident"]):
            return "Cyber Security & Data Protection"
        if any(w in text for w in ["vendor", "supplier", "third-party", "supply", "outsourc"]):
            return "Third-Party & Vendor Risk"
        if any(w in text for w in ["continuity", "disaster", "recovery", "bcp", "resilience", "availability"]):
            return "Operational Resilience"
        if any(w in text for w in ["compliance", "regulatory", "legal", "penalty", "gdpr", "ccpa", "sox", "privacy"]):
            return "Regulatory & Compliance"
        if any(w in text for w in ["change", "configuration", "deployment", "release", "patch", "software", "technolog"]):
            return "Technology & Change Management"
        if any(w in text for w in ["people", "talent", "staff", "retention", "key person", "hiring", "workforce"]):
            return "People & Organisational Risk"
        if any(w in text for w in ["market", "macro", "interest", "credit", "inflation", "currency", "economic"]):
            return "Market & Economic Risk"
        return category or "Enterprise Risk"

    domains = {
        r.get("ref", ""): _keyword_domain(r.get("name", ""), r.get("category", ""))
        for r in req.risks
        if r.get("ref")
    }

    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key and req.risks:
            import anthropic
            import json
            import re
            client = anthropic.Anthropic(api_key=api_key)
            risk_lines = "\n".join(
                f'{r.get("ref", "")}: {r.get("name", "")} (category: {r.get("category", "")})'
                for r in req.risks
            )
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=[{"type": "text", "text": _DOMAIN_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"Risks:\n{risk_lines}"}],
            )
            raw = msg.content[0].text.strip()
            match = re.search(r'\{.*?\}', raw, re.DOTALL)
            if match:
                domains.update(json.loads(match.group()))
    except Exception as exc:
        logger.warning("AI domain categorization failed, using keyword fallback: %s", exc)

    # Persist domain assignments back to risk_scores when a run_id is available
    if req.run_id and db.is_available():
        try:
            db.bulk_save_risk_domains(req.run_id, domains)
        except Exception as exc:
            logger.warning("Domain persistence failed (non-fatal): %s", exc)

    return {"domains": domains}


@router.get("/graph/{ticker}")
async def get_risk_graph(ticker: str):
    """Return the risk relationship graph (nodes + edges) for a ticker.

    Nodes are drawn from the most recent risk_scores row per risk_ref.
    Edges come from the risk_relationships table (computed after each pipeline run).
    When the DB is unavailable the response is empty rather than an error.
    """
    if not db.is_available():
        return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0, "ticker": ticker.upper()}
    company = db._run(lambda: _resolve_company_id_or_none(ticker))
    if not company:
        return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0, "ticker": ticker.upper()}
    graph = db.get_risk_graph(company)
    graph["ticker"] = ticker.upper()
    return graph


@router.get("/graph/{ticker}/run/{run_id}")
async def get_risk_graph_for_run(ticker: str, run_id: int):
    """Return the risk relationship graph for a specific pipeline run."""
    if not db.is_available():
        return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0}
    company = db._run(lambda: _resolve_company_id_or_none(ticker))
    if not company:
        return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0}
    graph = db.get_risk_graph(company, run_id=run_id)
    graph["ticker"] = ticker.upper()
    graph["run_id"] = run_id
    return graph


def _resolve_company_id_or_none(ticker: str) -> Optional[int]:
    """Return company_id for ticker, or None if not found. Never raises."""
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM companies WHERE ticker = %s", (ticker.upper(),))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception:
        return None


@router.get("/risks/latest/{ticker}")
async def get_latest_risks(ticker: str):
    """Return risks from the most recent run for a ticker, with latest review wording applied."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not connected")
    result = db.get_latest_risks_for_ticker(ticker)
    return {
        "risks": result["risks"],
        "count": len(result["risks"]),
        "run_id": result["run_id"],
        "ticker": ticker.upper(),
    }


@router.get("/risks/{run_id}")
async def get_risks_for_run(run_id: int):
    """Return current risk_scores for a run, with narrative wording applied."""
    risks = db.get_risk_scores_for_run(run_id)
    return {"risks": risks, "count": len(risks)}


@router.post("/upload")
async def upload_risk_register(file: UploadFile = File(...)):
    """Parse an uploaded Excel (.xlsx/.xls) or CSV risk register and return normalized risks for review.

    Expected columns (case-insensitive, flexible naming):
      ID / Risk ID / Ref, Name / Risk Name / Description, Category / Type / Domain,
      Score / Risk Score, RAG / Status / Rating, Framework / Source Framework
    """
    suffix = (file.filename or "upload").rsplit(".", 1)[-1].lower()
    if suffix not in ("xlsx", "xls", "csv"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '.{suffix}' — upload a .xlsx, .xls, or .csv file",
        )
    try:
        import pandas as pd
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="pandas not installed — run: pip install pandas openpyxl",
        )

    content = await file.read()
    try:
        if suffix in ("xlsx", "xls"):
            df = pd.read_excel(io.BytesIO(content), engine="openpyxl")
        else:
            df = pd.read_csv(io.StringIO(content.decode("utf-8-sig")))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {exc}")

    # Normalise column names for flexible header matching
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    def _col(*candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    id_col    = _col("id", "risk_id", "risk_ref", "ref", "risk_no", "no")
    name_col  = _col("name", "risk_name", "risk_statement", "description", "risk", "title")
    cat_col   = _col("category", "risk_category", "type", "domain", "risk_type")
    score_col = _col("score", "risk_score", "total_score", "residual_score")
    rag_col   = _col("rag", "status", "rating", "rag_status", "color")
    fw_col    = _col("framework", "source_framework", "source", "standard")

    if not name_col:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not find a name column. Expected one of: "
                "Name, Risk Name, Risk Statement, Description, Risk."
            ),
        )

    risks: List[Dict[str, Any]] = []
    for i, row in df.iterrows():
        name = str(row[name_col]).strip() if name_col else ""
        if not name or name.lower() in ("nan", "none", ""):
            continue

        def _safe(col):
            if col is None:
                return None
            v = row.get(col)
            if v is None:
                return None
            s = str(v).strip()
            return None if s.lower() in ("nan", "none", "") else s

        risk_id  = _safe(id_col) or f"UPL-{i + 1:03d}"
        category = _safe(cat_col) or "General"
        rag      = _safe(rag_col)
        fw       = _safe(fw_col) or "Uploaded Register"
        score: Optional[float] = None
        if score_col:
            try:
                score = float(row[score_col])
            except (ValueError, TypeError):
                pass

        risks.append({
            "id": risk_id,
            "name": name,
            "category": category,
            "score": score,
            "rag": rag,
            "source_framework": fw,
            "auto_controls": _auto_map_controls(name, category),
        })

    return {"risks": risks, "count": len(risks), "filename": file.filename or "upload"}


@router.post("/convert-to-code")
async def convert_to_code(req: ConvertToCodeRequest):
    """
    Convert reviewed risks to Risk-as-Code YAML. Only included risks are
    converted; control assignments are embedded when include_controls=True.
    """
    now = date.today().isoformat()
    included = [r for r in req.risks if r.get("included", True)]
    excluded = [r for r in req.risks if not r.get("included", True)]

    lines = [
        "# Risk Register Review — Risk-as-Code Output",
        f"# Generated: {now}  ·  {len(included)} risks included  ·  {len(excluded)} excluded",
        f"# Source: {req.framework or 'Internal Risk Register'}  ·  type: {req.review_type}",
        "",
        "thresholds:",
        "  red:   15.0",
        "  amber:  9.0",
        "",
        "scoring:",
        "  scale: 25    # impact (0-5) × likelihood (0-5)",
        "",
    ]

    if included:
        lines.append("risks:")
        for r in included:
            ref = r.get("id") or r.get("risk_ref") or "—"
            wording = (r.get("current_wording") or r.get("wording") or r.get("name") or "").replace('"', '\\"')
            category = r.get("category") or "—"
            score = r.get("score")
            rag = r.get("rag") or r.get("rag_status") or "—"
            reason = r.get("reason_for_change") or ""
            framework = r.get("source_framework") or req.framework or "Internal"
            controls = r.get("controls_assigned") or []

            lines.append(f"  - id:               {ref}")
            lines.append(f'    name:             "{wording}"')
            lines.append(f"    category:         {category}")
            lines.append(f"    source_framework: {framework}")
            lines.append(f"    rag:              {rag}")
            if score is not None:
                lines.append(f"    score:            {float(score):.1f}   # out of 25")
            if reason:
                lines.append(f'    change_reason:    "{reason}"')
            if req.include_controls and controls:
                lines.append("    controls:")
                for ctrl in controls:
                    ctrl_ref = ctrl if isinstance(ctrl, str) else ctrl.get("ref", "")
                    ctrl_info = _get_control_map_live().get(ctrl_ref)
                    lines.append(f"      - ref: {ctrl_ref}")
                    if ctrl_info:
                        lines.append(f'        name: "{ctrl_info["name"]}"')
                    gen = ctrl.get("generate_code", False) if isinstance(ctrl, dict) else False
                    if gen:
                        lines.append(f"        generate_control_as_code: true")
            lines.append("")

    if excluded:
        lines.append("excluded_risks:")
        for r in excluded:
            ref = r.get("id") or r.get("risk_ref") or "—"
            wording = (r.get("current_wording") or r.get("name") or "").replace('"', '\\"')
            reason = r.get("reason_for_change") or "No reason provided"
            lines.append(f"  - id:     {ref}")
            lines.append(f'    name:   "{wording}"')
            lines.append(f'    reason: "{reason}"')
            lines.append("")

    yaml_str = "\n".join(lines).rstrip()

    if req.review_id and db.is_available():
        db.save_rac_yaml(req.review_id, yaml_str)

    return {
        "yaml": yaml_str,
        "included_count": len(included),
        "excluded_count": len(excluded),
        "framework": req.framework or "Internal",
    }
