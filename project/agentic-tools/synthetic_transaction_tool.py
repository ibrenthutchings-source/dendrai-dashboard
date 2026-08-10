#!/usr/bin/env python3
"""
Synthetic Transaction Simulator — a poll-connector adapter.

Fabricates realistic, case-based transaction activity for systems/processes
this deployment has no live credentials for, so Continuous Watch (the Case
Flow Graph, Process Mining tabs, and the adjudication feed underneath both)
has real, continuously-arriving, monitorable data end-to-end without an
actual Oracle Fusion/SailPoint/SAP/Dynamics/ServiceNow tenant on the other
end. Registered as connector_type "synthetic_transaction" in
connector_poller.py's _ADAPTERS — see db.seed_synthetic_connectors() for the
eleven (system, process) poll_connectors rows it's meant to back.

Each connector's extra_config carries {"process": <id>, "system_label": <str>}.
pull_events() looks up that process id and fabricates 1-3 new complete
lifecycle cases (or standalone events, for processes that aren't naturally
multi-step) per tick. Steps are backdated with realistic inter-step gaps via
each event's "created_at" — connector_poller.py forwards that through to
mcp_governance._ingest_system_event(), so a case's steps land with their
intended day-apart timestamps instead of all appearing to happen in the same
instant they were polled (see connector_poller.py's _poll_one for the plumbing).

Order to Cash / Procure to Pay / Receive to Ship reuse the exact case
templates and payload builders generate_o2c_p2p_synthetic_log.py already
uses for its one-shot backfill — same event_type/flag shape, so these three
still exercise the real PaC Rego packages for those processes (pac_endpoints.py),
just continuously via a poll connector instead of a manual one-shot run.

The other eight processes (Hire to Retire, IAM, Record to Report, Fixed
Assets, Vendor Management, Payroll, Inventory Master, Customer Master File)
have no written PaC rule package yet — see pac_endpoints.py's process list.
They still flow through the real Bronze/Silver/Gold adjudication pipeline and
are fully visible/case-graphable in the Case Flow Graph and Process Mining
tabs; they just won't score process-specific policy_violations, since there's
no Rego package for them to match against. That's a deliberate scope line,
not an oversight — see the "monitoring-only for the gap processes" decision
this module implements.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from generate_o2c_p2p_synthetic_log import _O2C_CASE, _P2P_CASE, _INVENTORY_CASE

_ACTORS = [
    "jsmith@acme-corp.com", "mgarcia@acme-corp.com", "rpatel@acme-corp.com",
    "lchen@acme-corp.com", "kwilliams@acme-corp.com", "atanaka@acme-corp.com",
]

_VIOLATION_RATE = 0.12
_CASES_PER_TICK = (1, 3)  # min, max new cases/standalone events fabricated per poll

# process_mining_tool.PROCESS_TEMPLATES ids these reuse verbatim — kept as a
# separate import-free constant (not importing process_mining_tool here) so
# this adapter has no dependency beyond the synthetic generator it borrows from.
_REUSED_TEMPLATES = {
    "order_to_cash": _O2C_CASE,
    "procure_to_pay": _P2P_CASE,
    "receive_to_ship": _INVENTORY_CASE,
}


@dataclass
class SimStep:
    label: str                 # process_step
    event_type: str
    gap_days: tuple             # (min, max) days from the PREVIOUS step; ignored for step 0
    build: Callable              # (rng, rid, violating) -> payload detail dict
    # When this step is the one deliberately built violating, use THIS
    # event_type instead — for the couple of steps that line up with a real,
    # already-written PaC finding type (mcp_governance._SOURCE_EVENT_TO_PAC_PROCESS),
    # so the violating case actually exercises that existing Rego rule
    # instead of just landing as a generic HIGH-severity event.
    violating_event_type: Optional[str] = None


@dataclass
class ProcessDef:
    resource_prefix: str
    steps: list                 # list[SimStep]
    standalone: bool = False    # True: each poll draws ONE step kind as its own one-step case,
                                 # rather than walking the full list as one linked lifecycle


def _rid(prefix: str, rng: random.Random) -> str:
    return f"{prefix}-{rng.randint(1000, 9999)}"


def _case_id(rng: random.Random) -> str:
    return f"{rng.getrandbits(40):010x}"


def _detail(rng: random.Random, rid: str, violating: bool, actor_field: str = "actor",
            amount_field: Optional[str] = None, amount_range: tuple = (1000, 90000),
            extra: Optional[dict] = None) -> dict:
    d = {
        "reference": rid,
        actor_field: rng.choice(_ACTORS),
        "properly_approved": not violating,
        "approver": None if violating else rng.choice(_ACTORS),
    }
    if amount_field:
        d[amount_field] = rng.randint(*amount_range)
    if extra:
        d.update(extra)
    return d


# ── Oracle Fusion — Hire to Retire ───────────────────────────────────────────
_HIRE_TO_RETIRE = ProcessDef("REQ", [
    SimStep("Requisition Approved", "HCM_REQUISITION_EVENT", (0, 0),
            lambda rng, rid, v: _detail(rng, rid, v, "hiring_manager")),
    SimStep("Offer Accepted", "HCM_OFFER_EVENT", (5, 21),
            lambda rng, rid, v: _detail(rng, rid, v, "candidate", "offer_amount", (60000, 220000))),
    SimStep("Onboarding Completed", "HCM_ONBOARDING_EVENT", (1, 14),
            lambda rng, rid, v: _detail(rng, rid, v, "new_hire", extra={"background_check_cleared": not v})),
    SimStep("Pay Rate Change", "HCM_PAY_RATE_EVENT", (60, 400),
            lambda rng, rid, v: _detail(rng, rid, v, "employee", "new_annual_pay", (60000, 220000),
                                         extra={"pct_change": round(random.uniform(30, 60), 1) if v else round(random.uniform(2, 8), 1)}),
            violating_event_type="UNAUTHORIZED_PAY_RATE_CHANGE"),
    SimStep("Termination Processed", "HCM_TERMINATION_EVENT", (90, 900),
            lambda rng, rid, v: _detail(rng, rid, v, "employee", extra={"access_revoked": not v}),
            violating_event_type="TERMINATED_EMPLOYEE_ACCESS_RETAINED"),
])

# ── SailPoint — IAM ───────────────────────────────────────────────────────────
_IAM = ProcessDef("ACC", [
    SimStep("Access Requested", "IAM_ACCESS_REQUEST_EVENT", (0, 0),
            lambda rng, rid, v: _detail(rng, rid, v, "identity",
                                         extra={"entitlement": rng.choice(["AP_INVOICE_ENTRY", "PO_BUYER", "PRIV_DB_ADMIN", "FIN_CLOSE_APPROVER"])})),
    SimStep("Access Approved", "IAM_ACCESS_APPROVAL_EVENT", (0, 3),
            lambda rng, rid, v: _detail(rng, rid, v, "identity",
                                         extra={"sod_conflict_detected": v})),
    SimStep("Access Provisioned", "IAM_ACCESS_PROVISIONED_EVENT", (0, 2),
            lambda rng, rid, v: _detail(rng, rid, v, "identity")),
    SimStep("Access Certified", "IAM_ACCESS_CERTIFICATION_EVENT", (60, 180),
            lambda rng, rid, v: _detail(rng, rid, v, "certifier")),
    SimStep("Access Revoked", "IAM_ACCESS_REVOKED_EVENT", (30, 365),
            lambda rng, rid, v: _detail(rng, rid, v, "identity")),
])

# ── SAP HANA — Record to Report ──────────────────────────────────────────────
_RECORD_TO_REPORT = ProcessDef("JE", [
    SimStep("Journal Entry Posted", "R2R_JOURNAL_ENTRY_EVENT", (0, 0),
            lambda rng, rid, v: _detail(rng, rid, v, "preparer", "je_amount", (5000, 2000000))),
    SimStep("Account Reconciled", "R2R_RECONCILIATION_EVENT", (3, 20),
            lambda rng, rid, v: _detail(rng, rid, v, "reconciler", extra={"variance_identified": v})),
    SimStep("Period Closed", "R2R_PERIOD_CLOSE_EVENT", (1, 10),
            lambda rng, rid, v: _detail(rng, rid, v, "controller")),
    SimStep("Financial Statement Published", "R2R_FS_PUBLISH_EVENT", (1, 5),
            lambda rng, rid, v: _detail(rng, rid, v, "cfo_office")),
])

# ── SAP HANA — Fixed Assets ──────────────────────────────────────────────────
_FIXED_ASSETS = ProcessDef("FA", [
    SimStep("Asset Acquired", "FA_ACQUISITION_EVENT", (0, 0),
            lambda rng, rid, v: _detail(rng, rid, v, "requestor", "acquisition_cost", (5000, 500000))),
    SimStep("Asset Capitalized", "FA_CAPITALIZATION_EVENT", (2, 15),
            lambda rng, rid, v: _detail(rng, rid, v, "accountant", extra={"capitalization_threshold_met": not v})),
    SimStep("Depreciation Posted", "FA_DEPRECIATION_EVENT", (30, 90),
            lambda rng, rid, v: _detail(rng, rid, v, "accountant")),
    SimStep("Asset Disposed", "FA_DISPOSAL_EVENT", (180, 1800),
            lambda rng, rid, v: _detail(rng, rid, v, "requestor", extra={"disposal_approved": not v})),
])

# ── SAP HANA — Vendor Management ─────────────────────────────────────────────
_VENDOR_MANAGEMENT = ProcessDef("VEN", [
    SimStep("Vendor Onboarded", "VM_ONBOARDING_EVENT", (0, 0),
            lambda rng, rid, v: _detail(rng, rid, v, "sourcing_owner", extra={"due_diligence_completed": not v})),
    SimStep("Vendor Risk Assessed", "VM_RISK_ASSESSMENT_EVENT", (5, 30),
            lambda rng, rid, v: _detail(rng, rid, v, "risk_analyst")),
    SimStep("Vendor Contract Renewed", "VM_CONTRACT_RENEWAL_EVENT", (180, 400),
            lambda rng, rid, v: _detail(rng, rid, v, "procurement_owner", "contract_value", (10000, 3000000))),
    SimStep("Vendor Offboarded", "VM_OFFBOARDING_EVENT", (60, 700),
            lambda rng, rid, v: _detail(rng, rid, v, "procurement_owner")),
])

# ── SAP HANA — Payroll ────────────────────────────────────────────────────────
_PAYROLL = ProcessDef("TS", [
    SimStep("Time Entry Submitted", "PAYROLL_TIME_ENTRY_EVENT", (0, 0),
            lambda rng, rid, v: _detail(rng, rid, v, "employee", "hours", (20, 90))),
    SimStep("Time Approved", "PAYROLL_TIME_APPROVAL_EVENT", (0, 2),
            lambda rng, rid, v: _detail(rng, rid, v, "manager")),
    SimStep("Payroll Calculated", "PAYROLL_CALCULATION_EVENT", (1, 3),
            lambda rng, rid, v: _detail(rng, rid, v, "payroll_admin", "gross_pay", (1500, 12000))),
    SimStep("Payroll Disbursed", "PAYROLL_DISBURSEMENT_EVENT", (0, 2),
            lambda rng, rid, v: _detail(rng, rid, v, "treasury")),
])

# ── Dynamics — Inventory Master ──────────────────────────────────────────────
_INVENTORY_MASTER = ProcessDef("SKU", [
    SimStep("Item Master Created", "INV_MASTER_CREATE_EVENT", (0, 0),
            lambda rng, rid, v: _detail(rng, rid, v, "planner")),
    SimStep("Standard Cost Updated", "INV_STD_COST_UPDATE_EVENT", (10, 120),
            lambda rng, rid, v: _detail(rng, rid, v, "planner", "new_standard_cost", (5, 5000))),
    SimStep("Item Master Deactivated", "INV_MASTER_DEACTIVATE_EVENT", (200, 1200),
            lambda rng, rid, v: _detail(rng, rid, v, "planner")),
])

# ── ServiceNow — Customer Master File (standalone: master-file edits aren't a
#    natural multi-step lifecycle, same reasoning as the existing generator's
#    Customer/Vendor Master Change kinds) ─────────────────────────────────────
_CUSTOMER_MASTER_FILE = ProcessDef("CUST", [
    SimStep("Customer Record Created", "CUST_MASTER_CREATE_EVENT", (0, 0),
            lambda rng, rid, v: _detail(rng, rid, v, "cs_rep")),
    SimStep("Customer Record Updated", "CUST_MASTER_UPDATE_EVENT", (0, 0),
            lambda rng, rid, v: _detail(rng, rid, v, "cs_rep",
                                         extra={"field_changed": rng.choice(["billing_address", "payment_terms", "credit_limit", "tax_id"])})),
    SimStep("Customer Record Merged", "CUST_MASTER_MERGE_EVENT", (0, 0),
            lambda rng, rid, v: _detail(rng, rid, v, "data_steward")),
    SimStep("Customer Record Deactivated", "CUST_MASTER_DEACTIVATE_EVENT", (0, 0),
            lambda rng, rid, v: _detail(rng, rid, v, "cs_rep")),
], standalone=True)

_PROCESS_DEFS = {
    "hire_to_retire":      _HIRE_TO_RETIRE,
    "iam":                 _IAM,
    "record_to_report":    _RECORD_TO_REPORT,
    "fixed_assets":        _FIXED_ASSETS,
    "vendor_management":   _VENDOR_MANAGEMENT,
    "payroll":             _PAYROLL,
    "inventory_master":    _INVENTORY_MASTER,
    "customer_master_file": _CUSTOMER_MASTER_FILE,
}


def _event(event_type: str, actor: str, action: str, resource: str, severity: str,
           payload: dict, when: datetime) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "actor": actor,
        "action": action,
        "resource": resource,
        "severity": severity,
        "raw_payload": payload,
        "created_at": when,
    }


def _build_own_case(pdef: ProcessDef, rng: random.Random, now: datetime, process_id: str) -> list:
    """Walk pdef.steps as one linked lifecycle sharing a case_id — mirrors
    generate_o2c_p2p_synthetic_log.py's _build_case, adapted to this module's
    SimStep.build(rng, rid, violating) signature instead of a TxnKind's
    separate build_clean/build_violating pair."""
    case_id = _case_id(rng)
    violates = rng.random() < _VIOLATION_RATE
    violate_idx = rng.randrange(len(pdef.steps)) if violates else -1
    when = now - timedelta(days=sum(s.gap_days[1] for s in pdef.steps) * rng.uniform(0.3, 1.0))
    events = []
    for i, step in enumerate(pdef.steps):
        if i > 0:
            when = min(when + timedelta(days=rng.uniform(*step.gap_days)), now)
        rid = _rid(pdef.resource_prefix, rng)
        violating = i == violate_idx
        detail = step.build(rng, rid, violating)
        detail["case_id"] = case_id
        detail["process_step"] = step.label
        detail["process"] = process_id
        event_type = (step.violating_event_type if violating and step.violating_event_type else step.event_type)
        events.append(_event(
            event_type, detail.get("actor") or rng.choice(_ACTORS),
            f"{step.label.lower().replace(' ', '_')}_{'violation' if violating else 'clean'}",
            detail.get("reference") or case_id, "HIGH" if violating else "INFO", detail, when,
        ))
    return events


def _build_own_standalone(pdef: ProcessDef, rng: random.Random, now: datetime, process_id: str) -> dict:
    step = rng.choice(pdef.steps)
    case_id = _case_id(rng)
    violating = rng.random() < _VIOLATION_RATE
    rid = _rid(pdef.resource_prefix, rng)
    detail = step.build(rng, rid, violating)
    detail["case_id"] = case_id
    detail["process_step"] = step.label
    detail["process"] = process_id
    when = now - timedelta(hours=rng.uniform(0, 72))
    event_type = (step.violating_event_type if violating and step.violating_event_type else step.event_type)
    return _event(
        event_type, detail.get("actor") or rng.choice(_ACTORS),
        f"{step.label.lower().replace(' ', '_')}_{'violation' if violating else 'clean'}",
        detail.get("reference") or case_id, "HIGH" if violating else "INFO", detail, when,
    )


def _build_reused_case(steps: list, rng: random.Random, now: datetime, process_id: str) -> list:
    """Same shape as _build_own_case, but for the imported O2C/P2P/Inventory
    TxnKind templates, whose kind objects carry build_clean/build_violating
    instead of SimStep's single build(rng, rid, violating)."""
    case_id = _case_id(rng)
    violates = rng.random() < _VIOLATION_RATE
    violate_idx = rng.randrange(len(steps)) if violates else -1
    max_span = sum(gap[1] for _, _, gap in steps)
    when = now - timedelta(days=max_span * rng.uniform(0.3, 1.0))
    events = []
    for i, (kind, label, gap_days) in enumerate(steps):
        if i > 0:
            when = min(when + timedelta(days=rng.uniform(*gap_days)), now)
        rid = _rid(kind.resource_prefix, rng)
        violating = i == violate_idx
        detail = kind.build_violating(rng, rid) if violating else kind.build_clean(rng, rid)
        detail["case_id"] = case_id
        detail["process_step"] = label
        detail["process"] = process_id
        events.append(_event(
            kind.event_type, rng.choice(_ACTORS), f"{kind.name}_{'violation' if violating else 'clean'}",
            rid, "HIGH" if violating else "INFO", detail, when,
        ))
    return events


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list:
    """Fabricate 1-3 new synthetic transactions for this connector's assigned
    process. `base_url`/`credentials`/`since` are accepted for interface
    parity with every real adapter but unused — there's nothing to connect
    to or resume from; every tick just generates fresh activity."""
    process_id = (extra_config or {}).get("process")
    if not process_id:
        return []
    now = datetime.now(timezone.utc)
    rng = random.Random()
    n = rng.randint(*_CASES_PER_TICK)
    events: list = []

    if process_id in _REUSED_TEMPLATES:
        for _ in range(n):
            events.extend(_build_reused_case(_REUSED_TEMPLATES[process_id], rng, now, process_id))
        return events

    pdef = _PROCESS_DEFS.get(process_id)
    if pdef is None:
        return []
    for _ in range(n):
        if pdef.standalone:
            events.append(_build_own_standalone(pdef, rng, now, process_id))
        else:
            events.extend(_build_own_case(pdef, rng, now, process_id))
    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict):
    """Always succeeds — there is no external system to actually reach."""
    process_id = (extra_config or {}).get("process", "unknown")
    label = (extra_config or {}).get("system_label", "Synthetic")
    ok = process_id in _PROCESS_DEFS or process_id in _REUSED_TEMPLATES
    return ok, (f"{label} simulator ready for process '{process_id}'." if ok
                else f"Unknown simulated process '{process_id}'.")
