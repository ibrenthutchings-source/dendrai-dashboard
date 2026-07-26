#!/usr/bin/env python3
"""
Framework crosswalk metadata — SOC 2 / NIST SP 800-53 / ISO 27001 / COSO ERM
references for policy-enforced controls, powering the Executive Compliance
Scorecard (`GET /pac/compliance-scorecard`).

Curated, hand-reviewed mappings ONLY — never LLM-generated or auto-inferred.
This is the exact same guardrail commit 2b98f45 ("Retire Framework Sync —
superseded by Risk & Controls Register's RaC+CaC") established for RaC/CaC:
that prior pattern generated RaC from unreviewed live output and CaC from a
general library with no risk linkage, and was retired for it. A control_id
with no entry here simply isn't scored against any framework yet — an
honest gap, not one papered over with a fabricated mapping.

These are a reasonable STARTING crosswalk based on each control's plain-
language function (branch-protection admin bypass maps to logical-access
criteria, SARIF SLA maps to vulnerability-monitoring criteria, etc.) — they
are not a substitute for review and sign-off by a compliance professional
before being relied on for an actual SOC 2 / ISO audit. Edit this dict
directly to correct or extend a mapping; there is deliberately no "AI-assist"
button on this data, unlike the Rego editor's draft-assist feature.
"""

from __future__ import annotations

FRAMEWORK_MAPPINGS: dict[str, dict] = {
    # ── DevOps Monitoring: SCM branch-protection ──────────────────────────
    "DEVOPS-001": {  # Admin bypass (enforce_admins)
        "soc2_criteria": ["CC6.1", "CC6.6"],
        "nist_800_53": ["AC-3", "AC-6"],
        "iso_27001": ["A.9.2.3"],
        "coso_component": "Control Activities",
    },
    "DEVOPS-002": {  # Minimum approving reviews
        "soc2_criteria": ["CC8.1"],
        "nist_800_53": ["CM-3"],
        "iso_27001": ["A.14.2.2"],
        "coso_component": "Control Activities",
    },
    "DEVOPS-003": {  # Stale review dismissal
        "soc2_criteria": ["CC8.1"],
        "nist_800_53": ["CM-3"],
        "iso_27001": ["A.14.2.2"],
        "coso_component": "Control Activities",
    },
    "DEVOPS-004": {  # Required SAST/test status checks
        "soc2_criteria": ["CC7.1"],
        "nist_800_53": ["RA-5", "SA-11"],
        "iso_27001": ["A.14.2.8"],
        "coso_component": "Monitoring Activities",
    },
    "DEVOPS-005": {  # No CODEOWNERS
        "soc2_criteria": ["CC6.1"],
        "nist_800_53": ["AC-3"],
        "iso_27001": ["A.9.2.3"],
        "coso_component": "Control Activities",
    },
    "DEVOPS-006": {  # CODEOWNERS doesn't cover CI workflows
        "soc2_criteria": ["CC8.1"],
        "nist_800_53": ["CM-3"],
        "iso_27001": ["A.14.2.2"],
        "coso_component": "Control Activities",
    },
    # ── DevOps Monitoring: SARIF/SAST evidence SLA ────────────────────────
    "DEVOPS-007": {  # CRITICAL SARIF finding
        "soc2_criteria": ["CC7.2"],
        "nist_800_53": ["RA-5", "SI-2"],
        "iso_27001": ["A.12.6.1"],
        "coso_component": "Monitoring Activities",
    },
    "DEVOPS-008": {  # HIGH SARIF finding
        "soc2_criteria": ["CC7.2"],
        "nist_800_53": ["RA-5", "SI-2"],
        "iso_27001": ["A.12.6.1"],
        "coso_component": "Monitoring Activities",
    },
    # ── DevOps Monitoring: ITSM SLA Bridge ─────────────────────────────────
    "DEVOPS-009": {  # ITSM ticket SLA breach
        "soc2_criteria": ["CC7.2", "CC7.3"],
        "nist_800_53": ["IR-4", "SI-2"],
        "iso_27001": ["A.16.1.5"],
        "coso_component": "Monitoring Activities",
    },
    # ── Infrastructure Monitoring: Postgres CIS ────────────────────────────
    "INFRA-001": {  # SSL not enforced
        "soc2_criteria": ["CC6.1", "CC6.7"],
        "nist_800_53": ["SC-8"],
        "iso_27001": ["A.10.1.1", "A.13.1.1"],
        "coso_component": "Control Activities",
    },
    "INFRA-002": {  # Weak password encryption
        "soc2_criteria": ["CC6.1"],
        "nist_800_53": ["IA-5"],
        "iso_27001": ["A.9.4.3"],
        "coso_component": "Control Activities",
    },
    "INFRA-003": {  # Superuser sprawl
        "soc2_criteria": ["CC6.3"],
        "nist_800_53": ["AC-6", "AC-2"],
        "iso_27001": ["A.9.2.3"],
        "coso_component": "Control Activities",
    },
    "INFRA-004": {  # Unencrypted active connections
        "soc2_criteria": ["CC6.7"],
        "nist_800_53": ["SC-8"],
        "iso_27001": ["A.10.1.1", "A.13.1.1"],
        "coso_component": "Control Activities",
    },
    "INFRA-005": {  # Connection logging disabled
        "soc2_criteria": ["CC7.2"],
        "nist_800_53": ["AU-2", "AU-3"],
        "iso_27001": ["A.12.4.1"],
        "coso_component": "Monitoring Activities",
    },
    # ── Infrastructure Monitoring: Railway platform/deployment drift ──────
    "INFRA-006": {  # Unexpected public domain exposure
        "soc2_criteria": ["CC6.6"],
        "nist_800_53": ["CM-7", "SC-7"],
        "iso_27001": ["A.13.1.1", "A.13.1.3"],
        "coso_component": "Control Activities",
    },
    "INFRA-007": {  # Deployment image digest mismatch
        "soc2_criteria": ["CC8.1"],
        "nist_800_53": ["CM-3", "SR-4"],
        "iso_27001": ["A.14.2.2", "A.14.2.4"],
        "coso_component": "Control Activities",
    },
}


def get_mapping(control_id: str) -> dict | None:
    return FRAMEWORK_MAPPINGS.get(control_id)
