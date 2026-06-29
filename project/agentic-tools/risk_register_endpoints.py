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
    POST /risk-register/convert-to-code        Convert reviewed risks to YAML
"""

import io
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/risk-register", tags=["risk-register"])


# ─────────────────────────────────────────────────────────────────────────────
# Master control library (canonical reference set — mirrors risk-register-review.jsx)
# ─────────────────────────────────────────────────────────────────────────────

CONTROLS_LIBRARY: List[Dict[str, Any]] = [
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
]

# Map control refs to quick-lookup dict
_CONTROL_MAP = {c["ref"]: c for c in CONTROLS_LIBRARY}

# ─────────────────────────────────────────────────────────────────────────────
# Cached system prompts for direct AI calls
# Built once at import time so the stable content qualifies for prompt caching.
# ─────────────────────────────────────────────────────────────────────────────

_CONTROLS_SYSTEM = (
    "You are a GRC expert. For the risk provided by the user, recommend the 3-5 most "
    "relevant controls from the library below. Return ONLY a JSON array of control refs "
    "(e.g. [\"AC-02\",\"SC-01\"]).\n\n"
    "Available controls:\n"
    + "\n".join(f"{c['ref']}: {c['name']}" for c in CONTROLS_LIBRARY)
)

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


# ─────────────────────────────────────────────────────────────────────────────
# Mock external framework risk catalogs
# ─────────────────────────────────────────────────────────────────────────────

_FRAMEWORK_CATALOGS: Dict[str, List[Dict[str, Any]]] = {
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
}

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


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/controls")
async def get_controls():
    """Return the master control library."""
    return {"controls": CONTROLS_LIBRARY, "count": len(CONTROLS_LIBRARY)}


@router.post("/controls/recommend")
async def recommend_controls(req: ControlRecommendRequest):
    """
    Recommend controls for a risk. Attempts Claude API if available,
    falls back to keyword-based auto-mapping.
    """
    auto_refs = _auto_map_controls(req.risk_wording, req.risk_category or "")
    auto_controls = [_CONTROL_MAP[r] for r in auto_refs if r in _CONTROL_MAP]

    # Attempt AI-augmented recommendations if ANTHROPIC_API_KEY is set
    ai_summary = None
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=256,
                system=[{"type": "text", "text": _CONTROLS_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"Risk: {req.risk_wording}\nCategory: {req.risk_category or 'Unknown'}"}],
            )
            import json, re
            raw = msg.content[0].text.strip()
            match = re.search(r'\[.*?\]', raw, re.DOTALL)
            if match:
                ai_refs = json.loads(match.group())
                ai_controls = [_CONTROL_MAP[r] for r in ai_refs if r in _CONTROL_MAP]
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
        catalog = _FRAMEWORK_CATALOGS.get(fw_name)
        if catalog:
            results.extend(catalog)
        else:
            # Unknown framework — ask Claude to generate a realistic catalog
            ai_risks = await _generate_framework_risks_ai(fw_name)
            results.extend(ai_risks)

    # Free-text query with no explicit frameworks: search preset catalogs by name/content
    if req.query and not req.frameworks:
        q = req.query.lower()
        for fw_name, catalog in _FRAMEWORK_CATALOGS.items():
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

    return {"risks": unique, "count": len(unique)}


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

    return {"domains": domains}


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
                    ctrl_info = _CONTROL_MAP.get(ctrl_ref)
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
