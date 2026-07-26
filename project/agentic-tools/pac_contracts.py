#!/usr/bin/env python3
"""
Policy-as-Code event-schema contracts — negative testing, layer 1.

The failure this exists to prevent: a Rego rule that references a field or
compares against an event-type literal that the real pipeline never produces.
Such a rule is syntactically valid, passes `opa check`, evaluates without
error, and *silently never fires*. A policy that can never fire is
indistinguishable from a policy that found nothing wrong — both report zero
denials. This module makes that difference visible.

This is not hypothetical: the devops_monitoring module shipped with seven
rules keyed on `input.event.type == "branch_protection_rule"`, while
mcp_governance._evaluate_pac_policy sends the URO's EventType enum *value*
("BRANCH_PROTECTION_BYPASSED"). Every one of those rules was dead in
production. The unit tests passed because their fixtures encoded the same
wrong assumption — which is exactly why a contract check has to be derived
from the *producer* side, not from test fixtures.

Running this checker against every shipped built-in module found something
larger: _evaluate_pac_policy's input document has exactly one top-level key,
`event` (see below). itgc, order_to_cash, procure_to_pay, receive_to_ship,
and record_to_report all reference OTHER top-level roots too —
`input.journal.*`, `input.invoice.*`, `input.purchase_order.*`,
`input.bank_recon.*`, `input.access_review.*`, and more. None of those roots
are ever constructed by the automated adjudication pipeline; only
POST /pac/evaluate's manual "test this policy" sandbox lets a human supply
an input document shaped that way. So every rule keyed on a non-`event` root
is, today, a template with no live producer — not a bug in the same sense as
the branch_protection_rule literal, but the same underlying risk: a policy
that reads as enforcing something is not actually connected to anything.
That distinction (dead-by-typo vs. dead-by-no-producer-yet) is preserved in
check_module_contract's output via `unproducible_roots`.

── What the input document actually looks like ───────────────────────────────

mcp_governance._evaluate_pac_policy builds:

    {"event": {
        "type":          <EventType enum value>,
        "resource":      conformed_payload.resource_id,
        "resource_type": conformed_payload.resource_type,
        "action":        conformed_payload.action,
        "outcome":       conformed_payload.outcome,
        **conformed_payload.risk_indicators,      # source-system specific
    }}

So the legal field set is the five base fields plus whatever
UBO/pipeline/silver.py's conformer for that source system puts in
risk_indicators. PROCESS_CONTRACTS below declares that per PaC process.

── Why the contract is declared, not inferred ────────────────────────────────

Several conformers splat a dict whose keys aren't statically knowable from
the Python source (`**(raw.get("compliance") or {})`). Rather than parse
Python and guess, the allowed set is declared here and kept honest two ways:
  1. test_pac_contracts.py asserts the declared set matches what the real
     producers (scm_connectors.normalize_*_compliance, the *_tool.py poll
     adapters) actually emit — so the declaration can't drift into fiction.
  2. check_observed_fields() diffs the declaration against fields seen in
     real adjudicated events, catching producer changes the declaration
     hasn't caught up with.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Optional

# ── UBO import (same bootstrap as mcp_governance.py) ──────────────────────────
_here = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    os.path.normpath(os.path.join(_here, "..")),        # Docker: /app
    os.path.normpath(os.path.join(_here, "..", "..")),  # local dev: repo root
):
    if os.path.isdir(os.path.join(_candidate, "UBO")) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)
        break

try:
    from UBO.models.uro import EventType
    VALID_EVENT_TYPES = {e.value for e in EventType}
except ImportError:  # pragma: no cover — UBO always present in the real image
    VALID_EVENT_TYPES = set()


# ── The five fields every event carries, from _evaluate_pac_policy ────────────
BASE_EVENT_FIELDS = frozenset({"type", "resource", "resource_type", "action", "outcome"})

# Fields the SCM branch-protection audit contributes, via
# scm_connectors.normalize_github_compliance / normalize_gitlab_compliance.
_SCM_COMPLIANCE_FIELDS = frozenset({
    "enforce_admins",
    "required_approving_review_count",
    "dismiss_stale_reviews",
    "required_status_checks",
    "has_required_sast_check",
    "has_required_test_check",
    "codeowners_present",
    "codeowners_covers_workflows",
})

# Fields _conform_system_telemetry always sets.
_SYSTEM_TELEMETRY_FIELDS = frozenset({
    "risk_flags", "flag_count", "severity", "server_name", "system_type",
    "event_id", "narrative", "rule_id", "cwe",
    "external_system", "external_ticket_key", "finding_hash", "sla_due_at",
})

# Fields _conform_github / _conform_gitlab always set.
_GIT_FIELDS = frozenset({
    "ref", "forced", "cvss_score", "secret_type", "commits_count", "is_admin",
})

_MCP_FIELDS = frozenset({
    "risk_flags", "flag_count", "execution_time_ms", "server_name", "session_id",
    "message_id", "tool_args_hash", "error_message", "payload_hash", "narrative",
})

_SAP_FIELDS = frozenset({
    "amount", "currency", "cost_center", "company_code", "approver", "actor_groups",
})

_SAILPOINT_FIELDS = frozenset({
    "role_count", "last_login_days", "access_request_id", "entitlements", "certification_id",
})


# ── Per-process contract ──────────────────────────────────────────────────────
# allowed_fields: every input.event.<field> a module for this process may
#                 reference (base fields are added automatically).
# allowed_event_types: the EventType values that actually route to this
#                 process, per mcp_governance's _SOURCE_EVENT_TO_PAC_PROCESS /
#                 _SOURCE_SYSTEM_TO_PAC_PROCESS. A rule comparing
#                 input.event.type to anything outside this set can never fire.
#                 None means "not constrained" (the process receives whatever
#                 its source systems emit, which is most of the enum).

PROCESS_CONTRACTS: dict[str, dict] = {
    "devops_monitoring": {
        "allowed_fields": _SCM_COMPLIANCE_FIELDS | _SYSTEM_TELEMETRY_FIELDS | _GIT_FIELDS,
        "allowed_event_types": {
            "BRANCH_PROTECTION_BYPASSED",
            "CODE_REVIEW_BYPASSED",
            "SAST_FINDING",
            "SLA_BREACH",
        },
    },
    "itgc": {
        "allowed_fields": _GIT_FIELDS | _SYSTEM_TELEMETRY_FIELDS | _MCP_FIELDS | _SAILPOINT_FIELDS,
        "allowed_event_types": None,
    },
    "record_to_report": {"allowed_fields": _SAP_FIELDS, "allowed_event_types": None},
    "procure_to_pay":   {"allowed_fields": _SAP_FIELDS, "allowed_event_types": None},
    "order_to_cash":    {"allowed_fields": _SAP_FIELDS, "allowed_event_types": None},
    "receive_to_ship":  {"allowed_fields": _SAP_FIELDS, "allowed_event_types": None},
}


# ── Rego static analysis ──────────────────────────────────────────────────────

_INPUT_ROOT_RE = re.compile(r"\binput\.([A-Za-z_][A-Za-z0-9_]*)\.")
_INPUT_REF_RE = re.compile(r"\binput\.event\.([A-Za-z_][A-Za-z0-9_]*)")
# `input.event.type == "..."` and the reversed `"..." == input.event.type`
_TYPE_LITERAL_RE = re.compile(
    r'input\.event\.type\s*==\s*"([^"]+)"|"([^"]+)"\s*==\s*input\.event\.type'
)
_RULE_NAME_RE = re.compile(r"^\s*((?:deny|allow)\w*)\s*(?:\[|contains|:=|=|if|\{)", re.MULTILINE)

# The one and only top-level key _evaluate_pac_policy ever constructs. Any
# other root is unreachable by the automated pipeline — see module docstring.
_PRODUCED_ROOT = "event"


def extract_input_roots(rego_content: str) -> set[str]:
    """Every distinct top-level `input.<root>.` referenced in the module."""
    return set(_INPUT_ROOT_RE.findall(rego_content or ""))


def extract_input_event_refs(rego_content: str) -> set[str]:
    """Every distinct `input.event.<field>` referenced anywhere in the module."""
    return set(_INPUT_REF_RE.findall(rego_content or ""))


def extract_event_type_literals(rego_content: str) -> set[str]:
    """Every string literal compared for equality against input.event.type."""
    out: set[str] = set()
    for a, b in _TYPE_LITERAL_RE.findall(rego_content or ""):
        out.add(a or b)
    return out


def extract_rule_names(rego_content: str) -> set[str]:
    """Every deny*/allow* rule head defined in the module."""
    return set(_RULE_NAME_RE.findall(rego_content or ""))


def check_module_contract(process: str, rego_content: str) -> dict:
    """
    Validate one Rego module against its process's declared event contract.

    Returns:
        {
          "process": str,
          "ok": bool,
          "unproducible_roots": [...],      # top-level input.<root> that's
                                             # never anything but "event"
          "referenced_fields": [...],
          "unknown_fields": [...],          # referenced but never produced
          "referenced_event_types": [...],
          "invalid_event_types": [...],     # not a real EventType at all
          "unroutable_event_types": [...],  # real, but never routed to this process
          "findings": [human-readable strings],
        }

    unproducible_roots, unknown_fields, and invalid/unroutable event types
    are all *dead policy* signals: the rule referencing them cannot fire
    through the automated adjudication pipeline.
    """
    roots = extract_input_roots(rego_content)
    unproducible_roots = sorted(roots - {_PRODUCED_ROOT})

    contract = PROCESS_CONTRACTS.get(process)
    referenced = extract_input_event_refs(rego_content)
    type_literals = extract_event_type_literals(rego_content)

    findings: list[str] = []
    for root in unproducible_roots:
        findings.append(
            f"input.{root}.* is referenced but the automated pipeline never constructs "
            f"an input document with a top-level '{root}' key (only 'event') — any rule "
            f"keyed on it can only ever be exercised via POST /pac/evaluate's manual test "
            f"sandbox, never by real production adjudication"
        )

    if contract is None:
        findings.append(f"No declared field/event-type contract for process '{process}' — that part of the check was skipped")
        return {
            "process": process,
            "ok": not findings,
            "unproducible_roots": unproducible_roots,
            "referenced_fields": sorted(referenced),
            "unknown_fields": [],
            "referenced_event_types": sorted(type_literals),
            "invalid_event_types": [],
            "unroutable_event_types": [],
            "findings": findings,
        }

    allowed = set(contract["allowed_fields"]) | set(BASE_EVENT_FIELDS)
    unknown = sorted(referenced - allowed)

    # A literal that isn't an EventType value at all is unambiguously dead.
    invalid_types = sorted(t for t in type_literals if VALID_EVENT_TYPES and t not in VALID_EVENT_TYPES)
    # A real EventType that never routes to this process is also dead, but the
    # distinction matters for the fix (typo vs. wrong routing table).
    allowed_types = contract.get("allowed_event_types")
    unroutable = sorted(
        t for t in type_literals
        if allowed_types is not None and t not in allowed_types and t not in invalid_types
    )

    for field in unknown:
        findings.append(
            f"input.event.{field} is never produced for process '{process}' — "
            f"any rule requiring it can never fire"
        )
    for t in invalid_types:
        findings.append(
            f'input.event.type == "{t}" is not a valid EventType value — '
            f"this rule is dead (did you mean an EventType enum value?)"
        )
    for t in unroutable:
        findings.append(
            f'input.event.type == "{t}" is a valid EventType but never routes to '
            f"process '{process}' — this rule is dead in production"
        )

    return {
        "process": process,
        "ok": not findings,
        "unproducible_roots": unproducible_roots,
        "referenced_fields": sorted(referenced),
        "unknown_fields": unknown,
        "referenced_event_types": sorted(type_literals),
        "invalid_event_types": invalid_types,
        "unroutable_event_types": unroutable,
        "findings": findings,
    }


def check_observed_fields(process: str, observed_fields: set) -> dict:
    """
    Second honesty check, from the other direction: compare the declared
    contract against fields actually seen on real events for this process.

    `undeclared` means a producer started emitting something the contract
    doesn't know about (policy authors can't safely reference it yet).
    `never_observed` is informational — a declared field no real event has
    carried, which may just mean that code path hasn't run recently.
    """
    contract = PROCESS_CONTRACTS.get(process)
    if contract is None:
        return {"process": process, "undeclared": [], "never_observed": [], "ok": True}
    allowed = set(contract["allowed_fields"]) | set(BASE_EVENT_FIELDS)
    undeclared = sorted(set(observed_fields) - allowed)
    return {
        "process": process,
        "undeclared": undeclared,
        "never_observed": sorted(allowed - set(observed_fields)),
        "ok": not undeclared,
    }
