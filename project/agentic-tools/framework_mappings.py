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

coso_component values were originally "Control Activities" / "Monitoring
Activities" — IC-IF 2013 component names, not COSO ERM 2017's five (the same
naming error risks_as_code.py's _COSO_PRINCIPLES had before it was corrected;
see that module's comment for the full rationale). Corrected 2026-08-25,
reviewed and approved: "Control Activities" -> "Performance" (a technical
control enforcing a policy is implementing a risk response — ERM 2017
principle 13, which sits in the Performance component); "Monitoring
Activities" -> "Review & Revision" (ongoing monitoring/logging is principle
16, "Reviews Risk and Performance").

icif_component (added 2026-08-26, reviewed and approved): a control's ERM
2017 component and its IC-IF 2013 component are two independent attributes of
the same control, not two names for one fact — a technical control can be
"implementing a risk response" (ERM Performance) AND simultaneously be an
IC-IF "Control Activity" (the policy/procedure that carries out a directive)
at the same time. Restoring icif_component is recovering the pre-2026-08-25
values recorded above, not inferring anything new: "Control Activities" for
every control previously tagged that way, "Monitoring Activities" for
INFRA-005. Neither field is derived from the other, and neither is ever
overwritten by the other — see risk_coverage_cube.py's build_icif_cube (uses
icif_component) vs. its ERM evidence view (uses coso_component).

Only these 8 INFRA-* controls carry either field today — every other
control_id in controls_catalog is an honest gap in BOTH frameworks, not
backfilled with a guess, per this file's own policy above.
"""

from __future__ import annotations

FRAMEWORK_MAPPINGS: dict[str, dict] = {
    # ── Infrastructure Monitoring: Postgres CIS ────────────────────────────
    "INFRA-001": {  # SSL not enforced
        "soc2_criteria": ["CC6.1", "CC6.7"],
        "nist_800_53": ["SC-8"],
        "iso_27001": ["A.10.1.1", "A.13.1.1"],
        "coso_component": "Performance",
        "icif_component": "Control Activities",
    },
    "INFRA-002": {  # Weak password encryption
        "soc2_criteria": ["CC6.1"],
        "nist_800_53": ["IA-5"],
        "iso_27001": ["A.9.4.3"],
        "coso_component": "Performance",
        "icif_component": "Control Activities",
    },
    "INFRA-003": {  # Superuser sprawl
        "soc2_criteria": ["CC6.3"],
        "nist_800_53": ["AC-6", "AC-2"],
        "iso_27001": ["A.9.2.3"],
        "coso_component": "Performance",
        "icif_component": "Control Activities",
    },
    "INFRA-004": {  # Unencrypted active connections
        "soc2_criteria": ["CC6.7"],
        "nist_800_53": ["SC-8"],
        "iso_27001": ["A.10.1.1", "A.13.1.1"],
        "coso_component": "Performance",
        "icif_component": "Control Activities",
    },
    "INFRA-005": {  # Connection logging disabled
        "soc2_criteria": ["CC7.2"],
        "nist_800_53": ["AU-2", "AU-3"],
        "iso_27001": ["A.12.4.1"],
        "coso_component": "Review & Revision",
        "icif_component": "Monitoring Activities",
    },
    # ── Infrastructure Monitoring: Railway platform/deployment drift ──────
    "INFRA-006": {  # Unexpected public domain exposure
        "soc2_criteria": ["CC6.6"],
        "nist_800_53": ["CM-7", "SC-7"],
        "iso_27001": ["A.13.1.1", "A.13.1.3"],
        "coso_component": "Performance",
        "icif_component": "Control Activities",
    },
    "INFRA-007": {  # Deployment image digest mismatch
        "soc2_criteria": ["CC8.1"],
        "nist_800_53": ["CM-3", "SR-4"],
        "iso_27001": ["A.14.2.2", "A.14.2.4"],
        "coso_component": "Performance",
        "icif_component": "Control Activities",
    },
    # ── Infrastructure Monitoring: connector credential hygiene ────────────
    "INFRA-008": {  # Stale connector credential rotation
        "soc2_criteria": ["CC6.1", "CC6.3"],
        "nist_800_53": ["IA-5", "AC-2"],
        "iso_27001": ["A.9.2.4", "A.9.4.3"],
        "coso_component": "Performance",
        "icif_component": "Control Activities",
    },
}


def get_mapping(control_id: str) -> dict | None:
    return FRAMEWORK_MAPPINGS.get(control_id)
