#!/usr/bin/env python3
"""
UBO policy rule (POL-*) -> Core Domain crosswalk, powering Continuous
Monitoring's domain-grouped views (Heat Grid / Dotted Chart / Sankey).

Curated, hand-reviewed mappings ONLY — same guardrail framework_mappings.py
already established for the SOC 2/NIST/ISO/COSO crosswalk, and for the same
reason: this feeds a screen people use to decide where to look, so a
fabricated or auto-inferred mapping is worse than an honest gap. A rule_id
with no entry here simply isn't domain-grouped yet, not silently miscategorized.

This exists because observability.adjudicated_tool_calls.policy_violations
records UBO/policy/rules.py's POL-* rule IDs (the rules that actually fire in
production), while controls_catalog/PaC's Rego speaks a different, mostly
dormant vocabulary (ITGC-AC-01, INFRA-001, ...) — see pac_contracts.py's
module docstring for why most of that second vocabulary is dead by
construction today. Grouping Continuous Monitoring by Core Domain means
grouping what POL-* rules actually fire, not what PaC controls exist on
paper; hence this separate, POL_-keyed table rather than extending
framework_mappings.py (which is keyed on PaC control_id and answers a
different question — "which audit framework does this map to," not "which
business risk domain").

The nine domains are the same fixed taxonomy risk-register-review.jsx's
inferDomain() falls back to and _DOMAIN_SYSTEM (risk_register_endpoints.py)
asks Claude to categorize into — kept identical so a risk categorized one way
and a monitoring event categorized the other still land in the same bucket
on screen.

Edit this dict directly to correct or extend a mapping — there is
deliberately no "AI-assist" button on this data, matching
framework_mappings.py's own guardrail.
"""

from __future__ import annotations

CORE_DOMAINS = (
    "Identity & Access Management",
    "Financial Reporting & Controls",
    "Cyber Security & Data Protection",
    "Third-Party & Vendor Risk",
    "Operational Resilience",
    "Regulatory & Compliance",
    "Technology & Change Management",
    "People & Organisational Risk",
    "Market & Economic Risk",
)

POL_DOMAIN_MAPPINGS: dict[str, str] = {
    # ── Cross-System Baseline: audit-trail/pipeline data-quality rules ────────
    # Not a business risk domain on their own — bucketed as pipeline/system
    # integrity, the same reasoning as the Technology & Change Management
    # entries below.
    "POL-CORE-001": "Technology & Change Management",  # URO completeness (actor_id)
    "POL-CORE-002": "Technology & Change Management",  # timestamp freshness
    "POL-CORE-003": "Technology & Change Management",  # future timestamp rejection
    "POL-CORE-004": "Technology & Change Management",  # payload integrity

    # ── SAP Financial Controls ────────────────────────────────────────────────
    "POL-SAP-001": "Identity & Access Management",      # SoD violation
    "POL-SAP-002": "Third-Party & Vendor Risk",          # vendor master change, no approver
    "POL-SAP-003": "Financial Reporting & Controls",     # JE weekend anomaly
    "POL-SAP-004": "Financial Reporting & Controls",     # period-end override

    # ── GitHub DevSecOps ──────────────────────────────────────────────────────
    "POL-GH-001": "Cyber Security & Data Protection",    # secret exposure
    "POL-GH-002": "Identity & Access Management",        # main branch force push
    "POL-GH-003": "Cyber Security & Data Protection",    # dependency CVE/CVSS
    "POL-GH-004": "Identity & Access Management",        # branch protection admin bypass
    "POL-GH-005": "Technology & Change Management",      # deploy gate bypass
    "POL-GH-006": "Cyber Security & Data Protection",    # CVE remediation SLA breach

    # ── GitLab / Bitbucket DevSecOps (same shape as GitHub branch-protection) ─
    "POL-GL-001": "Identity & Access Management",        # protected branch admin bypass
    "POL-BB-001": "Identity & Access Management",        # branch restriction admin bypass

    # ── DevOps Monitoring evidence rules (still live in UBO/policy/rules.py
    #    even though the PaC-side devops_monitoring Rego module was retired —
    #    see UBO/pipeline/silver.py's SAST/SLA producers) ──────────────────────
    "POL-DEVOPS-001": "Cyber Security & Data Protection",  # SARIF finding SLA severity floor
    "POL-DEVOPS-002": "Cyber Security & Data Protection",  # ITSM ticket SLA breach
    "POL-DEVOPS-003": "Technology & Change Management",    # GitHub Actions workflow security

    # ── Infrastructure Monitoring ─────────────────────────────────────────────
    "POL-INFRA-001": "Cyber Security & Data Protection",  # config finding severity floor

    # ── Financial Risk Pipeline ────────────────────────────────────────────────
    "POL-FIN-001": "Financial Reporting & Controls",      # manual JE velocity spike
    "POL-FIN-002": "Financial Reporting & Controls",      # liquidity shift
    "POL-FIN-003": "Financial Reporting & Controls",      # inventory/sales divergence

    # ── Hire-to-Retire ─────────────────────────────────────────────────────────
    "POL-HR-001": "People & Organisational Risk",         # ghost employee suspected
    "POL-HR-002": "People & Organisational Risk",         # unauthorized pay rate change
    "POL-HR-003": "People & Organisational Risk",         # terminated employee access retained

    # ── Treasury & Cash Management ────────────────────────────────────────────
    "POL-TREAS-001": "Financial Reporting & Controls",    # wire transfer single approval
    "POL-TREAS-002": "Financial Reporting & Controls",    # bank reconciliation overdue
    "POL-TREAS-003": "Market & Economic Risk",            # FX hedge documentation missing

    # ── Export Control / Trade Compliance ─────────────────────────────────────
    "POL-TC-001": "Regulatory & Compliance",              # restricted-party match

    # ── Continuous Third-Party/Vendor Risk ────────────────────────────────────
    "POL-VEN-001": "Third-Party & Vendor Risk",           # vendor SOC 2 report expired
    "POL-VEN-002": "Third-Party & Vendor Risk",           # vendor spend concentration breach

    # ── AI Governance ──────────────────────────────────────────────────────────
    "POL-AI-001": "Regulatory & Compliance",              # AI assessment overdue
    "POL-AI-002": "Regulatory & Compliance",              # AI human oversight missing

    # ── SailPoint Identity ────────────────────────────────────────────────────
    "POL-SP-001": "Identity & Access Management",         # privilege escalation, no approval
    "POL-SP-002": "Identity & Access Management",         # dormant privileged account
    "POL-SP-003": "Identity & Access Management",         # role explosion

    # ── MCP Proxy Governance (AI agent/tool-call oversight) ───────────────────
    "POL-MCP-001": "Technology & Change Management",      # bypass keyword detection
    "POL-MCP-002": "Cyber Security & Data Protection",    # destructive tool call authorization
    "POL-MCP-003": "Technology & Change Management",      # MCP tool SLA breach
    "POL-MCP-004": "Technology & Change Management",      # MCP tool error investigation
    "POL-MCP-005": "Technology & Change Management",      # compound MCP governance violation

    # ── Generic Enterprise System (system_telemetry ingest) ──────────────────
    "POL-SYS-001": "Identity & Access Management",        # generic SoD violation
    "POL-SYS-002": "Identity & Access Management",        # privileged access, critical severity
    "POL-SYS-003": "Operational Resilience",              # compound generic violation
}


# PaC process -> Core Domain, coarser fallback for the second vocabulary that
# turns up in the SAME observability.adjudicated_tool_calls.policy_violations
# column: bare control_id strings (e.g. "OTC-P005", "P2P-P005") from Rego
# adjudication, rather than UBO/policy/rules.py's bracketed "[POL-X:SEV] msg"
# format. Coarser than the per-rule table above on purpose — a whole PaC
# process maps to one primary domain, not a per-control judgment call, since
# most of these controls don't fire in practice yet (see pac_contracts.py's
# module docstring on which processes are "dead by construction" today).
PROCESS_DOMAIN_MAPPINGS: dict[str, str] = {
    "itgc": "Identity & Access Management",
    "order_to_cash": "Financial Reporting & Controls",
    "procure_to_pay": "Third-Party & Vendor Risk",
    "receive_to_ship": "Operational Resilience",
    "record_to_report": "Financial Reporting & Controls",
    "infrastructure_monitoring": "Cyber Security & Data Protection",
    "hire_to_retire": "People & Organisational Risk",
    "trade_compliance": "Regulatory & Compliance",
}


def get_domain(rule_id: str) -> str | None:
    """Core Domain for a POL-* rule_id, or None if not (yet) mapped."""
    return POL_DOMAIN_MAPPINGS.get(rule_id)


def domain_for_violations(policy_violations: list[str], control_id_to_process: dict | None = None) -> str | None:
    """
    Best-effort domain for an adjudicated_tool_calls row's policy_violations
    list. Handles both vocabularies actually observed in that column:

      1. UBO/policy/rules.py's bracketed format — "[POL-GH-002:HIGH] Force
         push to protected branch by non-admin actor '...'" — resolved via
         POL_DOMAIN_MAPPINGS, per-rule precision.
      2. PaC Rego's bare control_id format — "OTC-P005", "P2P-P005" — resolved
         via `control_id_to_process` (caller-supplied, typically controls_
         catalog.control_id -> process) then PROCESS_DOMAIN_MAPPINGS, process-
         level precision. Pass None to skip this vocabulary entirely (e.g. a
         caller with no controls_catalog access).

    A row occasionally carries more than one violation, but they're
    overwhelmingly single-rule in practice, and "first wins" beats picking
    arbitrarily. Returns None if nothing resolves — an honest gap, not a
    guess (a control_id absent from controls_catalog, like some legacy
    ITGC-CM* ids seen in production, correctly resolves to None rather than
    a fabricated domain).
    """
    import re
    for v in policy_violations or []:
        m = re.match(r"\[(POL-[A-Z0-9-]+):", v)
        if m:
            domain = POL_DOMAIN_MAPPINGS.get(m.group(1))
            if domain:
                return domain
            continue
        if control_id_to_process and re.match(r"^[A-Z][A-Z0-9-]*$", v or ""):
            process = control_id_to_process.get(v)
            if process:
                domain = PROCESS_DOMAIN_MAPPINGS.get(process)
                if domain:
                    return domain
    return None
