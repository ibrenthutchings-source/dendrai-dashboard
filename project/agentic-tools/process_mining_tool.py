#!/usr/bin/env python3
"""
Process mining — variant analysis, conformance checking, cycle-time/
bottleneck stats, and rework detection over case-tracked adjudications.

The gap this closes: observability.adjudicated_tool_calls.case_id/
process_step (see that column's comment in db.py, and
generate_o2c_p2p_synthetic_log.py's module docstring) were added
specifically to make a REAL directly-follows graph possible — "step A
immediately preceded step B within the same tracked transaction" — and
continuous-monitoring-viz.jsx's CaseFlowGraph already renders that graph.
What was never built on top of the case_id/process_step columns is the rest
of process mining: which sequence of steps is actually the common path
(variant analysis), which cases deviate from the expected
Procure-to-Pay/Order-to-Cash/Receive-to-Ship lifecycle and how (conformance
checking), where time actually gets spent (cycle-time/bottleneck stats), and
which cases looped back through a step they'd already completed (rework —
often the fingerprint of a control catching something and sending it back).

Deliberately pure — no DB, no HTTP. Takes the same event shape
db.get_recent_adjudications_for_domain_summary / GET /observability/events
already produce: {"id", "adjudicated_at", "final_verdict", "risk_tier",
"source_system", "target_tool", "server_name", "requires_human_review",
"policy_violations", "case_id", "process_step"}. process_mining_endpoints.py
owns fetching those rows; this module owns interpreting them — same split
db._build_control_flow_map / db.get_control_flow_map already established.

    from process_mining_tool import build_cases, variant_analysis, conformance_summary
    cases = build_cases(events)
    variants = variant_analysis(cases)
    conformance = conformance_summary(cases)
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# ── Canonical process templates ──────────────────────────────────────────────
# Mirrors generate_o2c_p2p_synthetic_log.py's _P2P_CASE/_O2C_CASE/
# _INVENTORY_CASE process_step labels (the only real producer of case_id/
# process_step today) and pac_endpoints.py's PaC process ids
# (procure_to_pay/order_to_cash/receive_to_ship) so a variant or conformance
# result can deep-link straight into the Policy-as-Code screen for that
# process. Keep these three lists in sync with both of those files if either
# ever changes step naming — same cross-file-taxonomy discipline
# pol_domain_mappings.py documents for its own py/jsx mirror.
PROCESS_TEMPLATES: dict[str, dict[str, Any]] = {
    # Expanded from the original 3-step skeleton (PO -> Invoice -> Payment)
    # to the activities a real P2P walkthrough actually names: a requisition
    # gets approved before a PO ever exists, goods/services have to arrive
    # before a 3-way match is possible, and a payment run is approved before
    # it's released — see generate_o2c_p2p_synthetic_log.py's _P2P_CASE for
    # the matching producer-side steps.
    "procure_to_pay": {
        "label": "Procure to Pay",
        "steps": ["Purchase Requisition Approved", "Purchase Order Created", "Goods Receipt Confirmed",
                  "Invoice Matched", "Payment Approved", "Payment Released"],
    },
    # Expanded from Sales Order -> Billing -> Cash to include the credit
    # check that gates booking and the shipment that has to happen before an
    # order can be billed — see generate_o2c_p2p_synthetic_log.py's _O2C_CASE.
    "order_to_cash": {
        "label": "Order to Cash",
        "steps": ["Sales Order Booked", "Credit Approved", "Sales Order Shipped",
                  "Invoice Billed", "Cash Applied"],
    },
    # Expanded with the inspection/pick/pack activities between receipt and
    # putaway/shipment every warehouse walkthrough actually describes — see
    # generate_o2c_p2p_synthetic_log.py's _INVENTORY_CASE.
    "receive_to_ship": {
        "label": "Receive to Ship",
        "steps": ["Goods Received", "Quality Inspection Passed", "Putaway Confirmed",
                  "Pick Confirmed", "Pack Confirmed", "Goods Shipped"],
    },
    # The eight below mirror synthetic_transaction_tool.py's own ProcessDef
    # step labels (its poll-connector simulator for systems this deployment
    # has no live credentials for — Oracle Fusion HCM, SailPoint, SAP HANA,
    # Dynamics, ServiceNow) — same "keep step naming in sync with the real
    # producer" discipline as the three above. None of these have a written
    # PaC Rego package yet (see pac_endpoints.py's process list), so unlike
    # the three above they won't deep-link into Policy-as-Code — they're
    # still fully case-graphable and analyzable here regardless.
    #
    # Every step label across all 11 templates below must stay distinct —
    # pol_domain_mappings.py's _build_step_to_process() derives its
    # NOT_REVIEWED-event domain lookup straight from this dict and silently
    # keeps the FIRST template that claims a given label on a collision.
    "hire_to_retire": {
        "label": "Hire to Retire",
        "steps": ["Requisition Created", "Requisition Approved", "Offer Extended", "Offer Accepted",
                  "Background Check Completed", "Onboarding Completed", "Pay Rate Change",
                  "Termination Processed", "Final Pay Issued"],
    },
    "iam": {
        "label": "Identity & Access Management",
        "steps": ["Access Requested", "Access Request Reviewed", "Access Approved", "Access Provisioned",
                  "Access Certified", "Access Revoked"],
    },
    # Reordered: flux (variance) analysis is a close-control step performed
    # BEFORE the period is closed and statements are published, not after —
    # reviewing variance against statements that already went out doesn't
    # match how a real close walkthrough is run.
    "record_to_report": {
        "label": "Record to Report",
        "steps": ["Journal Entry Posted", "Journal Entry Approved", "Account Reconciled",
                  "Flux Analysis Reviewed", "Period Closed", "Financial Statement Published"],
    },
    "fixed_assets": {
        "label": "Fixed Assets",
        "steps": ["Asset Acquired", "Asset Tagged", "Asset Capitalized", "Depreciation Posted",
                  "Impairment Reviewed", "Asset Disposed"],
    },
    # Reordered, not just extended: risk assessment/due diligence happens
    # BEFORE a vendor is onboarded in a real procurement walkthrough, not
    # after — the original order here had it backwards. Performance review
    # also has to precede a contract renewal decision, not follow it — a
    # renewal isn't informed by a review that hasn't happened yet.
    "vendor_management": {
        "label": "Vendor Management",
        "steps": ["Vendor Risk Assessed", "Vendor Onboarded", "Contract Signed",
                  "Vendor Performance Reviewed", "Vendor Contract Renewed", "Vendor Offboarded"],
    },
    "payroll": {
        "label": "Payroll",
        "steps": ["Time Entry Submitted", "Time Approved", "Payroll Calculated",
                  "Payroll Approved", "Deductions Withheld", "Expense Reimbursement Approved",
                  "Payroll Disbursed", "Tax Authorities Paid", "Insurance Carriers Paid",
                  "Payslip Issued"],
    },
    "inventory_master": {
        "label": "Inventory Master",
        "steps": ["Item Master Created", "Item Master Approved", "Standard Cost Updated", "Item Master Deactivated"],
    },
    # Deliberately unchanged — a master-data create/update/merge/deactivate
    # set is already the realistic exhaustive lifecycle for a standalone
    # (non-sequential) record type; padding it would just be filler.
    "customer_master_file": {
        "label": "Customer Master File",
        "steps": ["Customer Record Created", "Customer Record Updated",
                  "Customer Record Merged", "Customer Record Deactivated"],
    },
}

_UNKNOWN_STEP = "Unknown step"


def _step_label(event: dict) -> str:
    return event.get("process_step") or _UNKNOWN_STEP


def _parse_ts(value) -> Optional[datetime]:
    """adjudicated_at arrives as a native datetime from psycopg2 in
    production, but tests and any JSON round-trip pass ISO strings — accept
    both rather than forcing every caller to normalize first."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Case assembly ─────────────────────────────────────────────────────────────


def build_cases(events: list[dict]) -> dict[str, list[dict]]:
    """Group events by case_id, each case's events sorted oldest-first by
    adjudicated_at. Events with no case_id (the overwhelming majority of
    ordinary MCP tool-call adjudications — see the case_id column comment)
    are excluded; process mining only has something to say about
    case-tracked transactions."""
    by_case: dict[str, list[dict]] = {}
    for e in events:
        cid = e.get("case_id")
        if not cid:
            continue
        by_case.setdefault(cid, []).append(e)
    for cid, evs in by_case.items():
        evs.sort(key=lambda e: _parse_ts(e.get("adjudicated_at")) or datetime.min.replace(tzinfo=None))
    return by_case


def classify_case_process(step_labels: list[str]) -> Optional[str]:
    """Which PROCESS_TEMPLATES id this case's step set best matches, by
    Jaccard overlap of step SETS (order handled separately by
    conformance checking). Returns None — "untemplated" — for a case whose
    steps share nothing with any known template (e.g. one of
    generate_o2c_p2p_synthetic_log.py's five standalone kinds: Revenue
    Recognized, Customer Master Change, AR Aging Review, Vendor Master
    Change, SoD Check — none of those are naturally multi-step lifecycles,
    so scoring them against a 3-step template would be a false conformance
    verdict, not process mining)."""
    step_set = set(step_labels)
    if not step_set:
        return None
    best_id, best_score = None, 0.0
    for proc_id, tmpl in PROCESS_TEMPLATES.items():
        tmpl_set = set(tmpl["steps"])
        overlap = step_set & tmpl_set
        if not overlap:
            continue
        jaccard = len(overlap) / len(step_set | tmpl_set)
        if jaccard > best_score:
            best_id, best_score = proc_id, jaccard
    return best_id


def case_variant_signature(step_labels: list[str]) -> str:
    """The literal ordered sequence of steps a case took, as a single
    string — the grouping key for variant_analysis. Two cases share a
    variant iff they took the exact same steps in the exact same order,
    including repeats (a rework loop is itself a distinct variant, not
    collapsed into the "normal" one)."""
    return " > ".join(step_labels) if step_labels else "(no steps)"


def _case_verdict_rollup(events: list[dict]) -> dict:
    has_violation = any(e.get("policy_violations") for e in events)
    worst_verdict = "CLEAR"
    for e in events:
        v = e.get("final_verdict") or e.get("verdict")
        if v == "ESCALATE":
            worst_verdict = "ESCALATE"
            break
        if v == "MONITOR" and worst_verdict != "ESCALATE":
            worst_verdict = "MONITOR"
    return {"has_violation": has_violation, "worst_verdict": worst_verdict}


def summarize_case(case_id: str, events: list[dict]) -> dict:
    """One case's full picture: steps taken, matched process (if any),
    variant signature, duration, rework, and conformance against its
    matched template. This is the unit every other function in this module
    aggregates over."""
    step_labels = [_step_label(e) for e in events]
    process = classify_case_process(step_labels)
    timestamps = [_parse_ts(e.get("adjudicated_at")) for e in events]
    timestamps = [t for t in timestamps if t is not None]
    started_at = min(timestamps) if timestamps else None
    ended_at = max(timestamps) if timestamps else None
    duration_hours = (
        (ended_at - started_at).total_seconds() / 3600.0
        if started_at and ended_at and ended_at >= started_at
        else None
    )
    seen = set()
    repeated_steps = sorted({s for s in step_labels if s in seen or seen.add(s)})
    rollup = _case_verdict_rollup(events)

    return {
        "case_id": case_id,
        "steps": step_labels,
        "step_count": len(step_labels),
        "process": process,
        "process_label": PROCESS_TEMPLATES[process]["label"] if process else None,
        "variant": case_variant_signature(step_labels),
        "started_at": started_at.isoformat() if started_at else None,
        "ended_at": ended_at.isoformat() if ended_at else None,
        "duration_hours": round(duration_hours, 2) if duration_hours is not None else None,
        "has_rework": bool(repeated_steps),
        "repeated_steps": repeated_steps,
        "has_violation": rollup["has_violation"],
        "worst_verdict": rollup["worst_verdict"],
        "conformance": conformance_check_case(step_labels, process),
    }


def list_case_summaries(cases: dict[str, list[dict]], process: Optional[str] = None) -> list[dict]:
    """One summarize_case() per case, newest-started first. `process`
    filters to cases classified into that PROCESS_TEMPLATES id (pass None
    for every case, including untemplated ones)."""
    out = [summarize_case(cid, evs) for cid, evs in cases.items()]
    if process:
        out = [c for c in out if c["process"] == process]
    out.sort(key=lambda c: c["started_at"] or "", reverse=True)
    return out


# ── Conformance checking ──────────────────────────────────────────────────────


def conformance_check_case(step_labels: list[str], process: Optional[str]) -> dict:
    """Compare one case's actual step sequence against its matched
    template. A case with no matched process (`process is None`) is
    reported unscored rather than force-fit against an arbitrary template —
    same "honest gap, not papered over" rule framework_mappings.py's module
    docstring states for its own unmapped controls.

    conforming == True iff every template step is present exactly once, in
    the template's order, with no steps outside the template."""
    if not process:
        return {"scored": False, "process": None, "conforming": None, "reason": "no matching process template"}

    template_steps = PROCESS_TEMPLATES[process]["steps"]
    template_set = set(template_steps)
    case_set = set(step_labels)

    missing_steps = [s for s in template_steps if s not in case_set]
    extra_steps = [s for s in step_labels if s not in template_set]

    seen = set()
    repeated_steps = sorted({s for s in step_labels if s in seen or seen.add(s)})

    # Relative order of the steps this case shares with the template, first
    # occurrence only — compared against the template's own order over that
    # same subset. A skip doesn't count as out-of-order (that's
    # missing_steps' job); this only catches "present, but in the wrong place".
    seen_common: list[str] = []
    seen_set: set[str] = set()
    for s in step_labels:
        if s in template_set and s not in seen_set:
            seen_common.append(s)
            seen_set.add(s)
    expected_order = [s for s in template_steps if s in seen_set]
    out_of_order = seen_common != expected_order

    conforming = not missing_steps and not extra_steps and not repeated_steps and not out_of_order

    return {
        "scored": True,
        "process": process,
        "process_label": PROCESS_TEMPLATES[process]["label"],
        "conforming": conforming,
        "missing_steps": missing_steps,
        "extra_steps": extra_steps,
        "repeated_steps": repeated_steps,
        "out_of_order": out_of_order,
    }


def conformance_summary(cases: dict[str, list[dict]], process: Optional[str] = None) -> dict:
    """Aggregate conformance across every case whose steps matched a
    template (or just `process` when given): conformance rate, a breakdown
    of which deviation types actually occurred, and the specific
    non-conforming cases (for a reviewer to open and inspect)."""
    summaries = [c for c in list_case_summaries(cases, process) if c["conformance"]["scored"]]
    total = len(summaries)
    conforming = sum(1 for c in summaries if c["conformance"]["conforming"])
    breakdown = {"missing_step": 0, "extra_step": 0, "repeated_step": 0, "out_of_order": 0}
    deviating_cases = []
    for c in summaries:
        conf = c["conformance"]
        if conf["conforming"]:
            continue
        if conf["missing_steps"]:
            breakdown["missing_step"] += 1
        if conf["extra_steps"]:
            breakdown["extra_step"] += 1
        if conf["repeated_steps"]:
            breakdown["repeated_step"] += 1
        if conf["out_of_order"]:
            breakdown["out_of_order"] += 1
        deviating_cases.append({
            "case_id": c["case_id"], "process": c["process"], "process_label": c["process_label"],
            "variant": c["variant"], "started_at": c["started_at"], "has_violation": c["has_violation"],
            "missing_steps": conf["missing_steps"], "extra_steps": conf["extra_steps"],
            "repeated_steps": conf["repeated_steps"], "out_of_order": conf["out_of_order"],
        })
    return {
        "process": process,
        "scored_cases": total,
        "conforming_cases": conforming,
        "conformance_rate": round(conforming / total, 4) if total else None,
        "deviation_breakdown": breakdown,
        "deviating_cases": deviating_cases,
    }


# ── Variant analysis ──────────────────────────────────────────────────────────


def variant_analysis(cases: dict[str, list[dict]], process: Optional[str] = None) -> list[dict]:
    """Every distinct step sequence observed, most frequent first — the
    process-mining question "what actually happens, and how often does
    reality deviate from the one path everyone assumes." The single most
    frequent variant for a process that has a matched template and equals
    that template's exact order is flagged is_canonical=True; the plain
    most-frequent variant (whatever it is) is separately flagged
    is_happy_path=True — the two coincide for a well-behaved process and
    diverge for one where the "normal" path has quietly drifted from the
    documented one."""
    summaries = list_case_summaries(cases, process)
    by_variant: dict[str, dict] = {}
    for c in summaries:
        v = by_variant.setdefault(c["variant"], {
            "variant": c["variant"], "steps": c["steps"], "process": c["process"],
            "process_label": c["process_label"], "case_count": 0, "violation_count": 0,
            "durations": [], "case_ids": [],
        })
        v["case_count"] += 1
        if c["has_violation"]:
            v["violation_count"] += 1
        if c["duration_hours"] is not None:
            v["durations"].append(c["duration_hours"])
        v["case_ids"].append(c["case_id"])

    total_cases = len(summaries)
    out = []
    for v in by_variant.values():
        durations = v.pop("durations")
        case_ids = v.pop("case_ids")
        out.append({
            **v,
            "pct_of_cases": round(v["case_count"] / total_cases, 4) if total_cases else 0.0,
            "violation_rate": round(v["violation_count"] / v["case_count"], 4) if v["case_count"] else 0.0,
            "avg_duration_hours": round(statistics.fmean(durations), 2) if durations else None,
            "sample_case_ids": case_ids[:5],
        })
    out.sort(key=lambda v: v["case_count"], reverse=True)

    if out:
        out[0]["is_happy_path"] = True
        for v in out[1:]:
            v["is_happy_path"] = False
        for v in out:
            tmpl = PROCESS_TEMPLATES.get(v["process"])
            v["is_canonical"] = bool(tmpl) and v["steps"] == tmpl["steps"]
    return out


# ── Cycle-time / bottleneck analysis ──────────────────────────────────────────


def cycle_time_stats(cases: dict[str, list[dict]], process: Optional[str] = None) -> dict:
    """Per-edge (step A -> step B, consecutive within one case) duration
    stats and overall case duration stats. The edge with the highest mean
    duration is the bottleneck — where time actually accumulates in the
    lifecycle, as opposed to the DFG's edge width, which shows volume, not
    speed."""
    summaries = [c for c in list_case_summaries(cases, process) if c["step_count"] >= 1]
    edge_durations: dict[tuple[str, str], list[float]] = {}
    for cid, events in cases.items():
        if process and classify_case_process([_step_label(e) for e in events]) != process:
            continue
        ordered = sorted(events, key=lambda e: _parse_ts(e.get("adjudicated_at")) or datetime.min.replace(tzinfo=None))
        for a, b in zip(ordered, ordered[1:]):
            ta, tb = _parse_ts(a.get("adjudicated_at")), _parse_ts(b.get("adjudicated_at"))
            if ta is None or tb is None or tb < ta:
                continue
            hours = (tb - ta).total_seconds() / 3600.0
            key = (_step_label(a), _step_label(b))
            edge_durations.setdefault(key, []).append(hours)

    edges = []
    for (src, tgt), durations in edge_durations.items():
        durations_sorted = sorted(durations)
        edges.append({
            "source": src, "target": tgt, "count": len(durations),
            "avg_hours": round(statistics.fmean(durations_sorted), 2),
            "median_hours": round(statistics.median(durations_sorted), 2),
            "p90_hours": round(_percentile(durations_sorted, 0.90), 2),
        })
    edges.sort(key=lambda e: e["avg_hours"], reverse=True)

    case_durations = [c["duration_hours"] for c in summaries if c["duration_hours"] is not None]
    case_duration_stats = None
    if case_durations:
        cd_sorted = sorted(case_durations)
        case_duration_stats = {
            "count": len(cd_sorted),
            "mean_hours": round(statistics.fmean(cd_sorted), 2),
            "median_hours": round(statistics.median(cd_sorted), 2),
            "p90_hours": round(_percentile(cd_sorted, 0.90), 2),
        }

    return {
        "process": process,
        "edges": edges,
        "bottleneck": edges[0] if edges else None,
        "case_duration": case_duration_stats,
    }


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


# ── Rework ─────────────────────────────────────────────────────────────────────


def rework_summary(cases: dict[str, list[dict]], process: Optional[str] = None) -> dict:
    """Cases that revisited a step they'd already completed — often the
    fingerprint of a control catching something (an invoice bounced back
    for correction, a putaway redone after a count mismatch) and sending
    the case backward rather than forward. Distinct from conformance's
    repeated_steps list (which flags it per-case as a deviation type); this
    aggregates the rate and lists the affected cases directly."""
    summaries = list_case_summaries(cases, process)
    reworked = [c for c in summaries if c["has_rework"]]
    return {
        "process": process,
        "total_cases": len(summaries),
        "reworked_cases": len(reworked),
        "rework_rate": round(len(reworked) / len(summaries), 4) if summaries else None,
        "cases": [
            {"case_id": c["case_id"], "process": c["process"], "repeated_steps": c["repeated_steps"],
             "variant": c["variant"], "started_at": c["started_at"]}
            for c in reworked
        ],
    }


# ── Value stream map (process cycle efficiency) ────────────────────────────────
#
# Textbook VSM splits every station's time into touch time (value-add work)
# vs. wait time (queueing) using a start-of-work and end-of-work timestamp per
# station. This platform has neither — adjudicated_at is a single instant per
# step (when it was logged), so there is no honest way to say how long the
# step's own work took, only how long elapsed between consecutive steps (the
# same edges cycle_time_stats already computes). Rather than fabricate a
# touch/wait split with no data behind it, this reclassifies those same real
# edges by whether they advance the case along its template's documented
# order (canonical — the forward, expected handoff) or not (deviation —
# rework/out-of-order/off-template, i.e. where elapsed time bought no forward
# progress). Process Cycle Efficiency = canonical hours / total hours is the
# same ratio VSM uses for value-add %, computed from a labeling rule rather
# than a measured station duration, and is reported as such.


def _is_canonical_edge(src: str, tgt: str, process: Optional[str]) -> bool:
    """True iff (src, tgt) is a consecutive pair in process's template order
    — the one forward handoff this edge is "supposed" to be, per the
    documented lifecycle. False for every rework loop, skip, or off-template
    step pair, and always False when the case has no matched template
    (nothing to be canonical against)."""
    if not process:
        return False
    steps = PROCESS_TEMPLATES[process]["steps"]
    try:
        return steps.index(tgt) == steps.index(src) + 1
    except ValueError:
        return False


def value_stream_map(cases: dict[str, list[dict]], process: Optional[str] = None) -> dict:
    """Per-case split of real inter-step elapsed time into canonical
    (forward, template-order) vs. deviation (rework/skip/off-template) hours,
    aggregated into a process cycle efficiency ratio and a ranked ledger of
    the deviation edges actually costing time — the "waste" side of the
    map. Unscored (process_cycle_efficiency=None) when there's no case with
    at least one timed edge, same honest-null convention cycle_time_stats
    uses for case_duration."""
    summaries = {c["case_id"]: c for c in list_case_summaries(cases, process)}
    canonical_hours_by_case: list[float] = []
    deviation_hours_by_case: list[float] = []
    deviation_edges: dict[tuple[str, str], list[float]] = {}
    canonical_edges: dict[tuple[str, str], list[float]] = {}

    for cid, events in cases.items():
        if cid not in summaries:
            continue
        case_process = summaries[cid]["process"]
        if process and case_process != process:
            continue
        ordered = sorted(events, key=lambda e: _parse_ts(e.get("adjudicated_at")) or datetime.min.replace(tzinfo=None))
        canon_h, dev_h, any_timed = 0.0, 0.0, False
        for a, b in zip(ordered, ordered[1:]):
            ta, tb = _parse_ts(a.get("adjudicated_at")), _parse_ts(b.get("adjudicated_at"))
            if ta is None or tb is None or tb < ta:
                continue
            hours = (tb - ta).total_seconds() / 3600.0
            key = (_step_label(a), _step_label(b))
            any_timed = True
            if _is_canonical_edge(key[0], key[1], case_process):
                canon_h += hours
                canonical_edges.setdefault(key, []).append(hours)
            else:
                dev_h += hours
                deviation_edges.setdefault(key, []).append(hours)
        if any_timed:
            canonical_hours_by_case.append(canon_h)
            deviation_hours_by_case.append(dev_h)

    def _edge_rows(edges: dict[tuple[str, str], list[float]]) -> list[dict]:
        rows = []
        for (src, tgt), hrs in edges.items():
            rows.append({
                "source": src, "target": tgt, "count": len(hrs),
                "total_hours": round(sum(hrs), 2),
                "avg_hours": round(statistics.fmean(hrs), 2),
            })
        rows.sort(key=lambda r: r["total_hours"], reverse=True)
        return rows

    total_canon = sum(canonical_hours_by_case)
    total_dev = sum(deviation_hours_by_case)
    total = total_canon + total_dev
    n = len(canonical_hours_by_case)

    return {
        "process": process,
        "scored_cases": n,
        "process_cycle_efficiency": round(total_canon / total, 4) if total > 0 else None,
        "avg_value_add_hours": round(total_canon / n, 2) if n else None,
        "avg_deviation_hours": round(total_dev / n, 2) if n else None,
        "canonical_edges": _edge_rows(canonical_edges),
        "deviation_edges": _edge_rows(deviation_edges),
    }


# ── Pareto analysis ──────────────────────────────────────────────────────────
#
# "Which few causes account for most of the pain" — the vital-few-vs-trivial-
# many ranking, applied here to the deviation edges value_stream_map already
# computes (real elapsed hours, not an invented severity score) rather than
# to opinion-based severity ratings, keeping this consistent with the
# platform's never-fabricate rule.


def pareto_rank(items: list[dict], value_key: str, label_fn) -> list[dict]:
    """Generic 80/20 ranking: sorts `items` by `value_key` descending, adds
    pct_of_total/cumulative_pct, and flags is_vital_few for the smallest
    leading set of items whose cumulative_pct first reaches 80% (inclusive
    of the item that crosses the line — the standard Pareto cutoff). Pure
    utility, reusable for any ranked-cause breakdown, not just process
    mining's own callers."""
    total = sum(i[value_key] for i in items)
    ranked = sorted(items, key=lambda i: i[value_key], reverse=True)
    cumulative = 0.0
    out = []
    crossed = False
    for i in ranked:
        cumulative += i[value_key]
        pct = round(i[value_key] / total, 4) if total else 0.0
        cum_pct = round(cumulative / total, 4) if total else 0.0
        out.append({**i, "label": label_fn(i), "pct_of_total": pct, "cumulative_pct": cum_pct,
                    "is_vital_few": not crossed})
        if cum_pct >= 0.80:
            crossed = True
    return out


def pareto_breakdown(cases: dict[str, list[dict]], process: Optional[str] = None) -> dict:
    """Pareto-ranks this process's deviation edges (from value_stream_map) by
    total hours lost — "which specific step-pair deviations cost the most
    total time" — the direct, data-backed answer to "where should process
    improvement focus first." Reported on total_hours, not just count: one
    rare-but-very-slow deviation can outweigh many quick ones, and ranking by
    count alone would hide that."""
    vsm = value_stream_map(cases, process)
    ranked = pareto_rank(
        vsm["deviation_edges"], "total_hours",
        lambda e: f"{e['source']} → {e['target']}",
    )
    vital_few = [e for e in ranked if e["is_vital_few"]]
    return {
        "process": process,
        "basis": "deviation_edge_total_hours",
        "total_deviation_hours": round(sum(e["total_hours"] for e in ranked), 2),
        "ranked": ranked,
        "vital_few_count": len(vital_few),
        "vital_few_share_of_hours": round(
            sum(e["total_hours"] for e in vital_few) / sum(e["total_hours"] for e in ranked), 4
        ) if ranked else None,
    }


# ── Statistical process control (cycle-time control chart) ─────────────────────
#
# A single z-score outlier check (like CaseFlowGraph's) answers "is this one
# edge unusual right now." This answers the trend question instead — is
# overall case duration drifting out of its normal range over time — using
# the same individuals-control-chart logic (center line ± k * population
# stdev of the period means) as a textbook I-chart, computed over trailing
# periods the same way db.compute_dora_trend buckets deployment metrics, so a
# Head of Operations sees drift, not just a snapshot.


def cycle_time_control_chart(
    cases: dict[str, list[dict]], process: Optional[str] = None,
    period_days: int = 7, periods: int = 8, sigma: float = 2.0,
) -> dict:
    """Case-duration mean per trailing period (most recent last, same
    windowing convention as db.compute_dora_trend), plus a center line and
    UCL/LCL fit across the periods that actually have data. A period's mean
    outside [LCL, UCL] is flagged out_of_control — a signal worth
    investigating, not proof of a problem on its own (a single outlier period
    from genuinely low volume is still flagged, but reported alongside its
    case_count so that's visible). Honest-null when fewer than two periods
    have any cases — a center line and control limits need real variation
    across periods to mean anything, same as the rest of this module declines
    to score against data it doesn't have."""
    summaries = list_case_summaries(cases, process)
    now = datetime.now(timezone.utc)

    period_rows = []
    for i in range(periods - 1, -1, -1):
        start = now - timedelta(days=(i + 1) * period_days)
        end = now - timedelta(days=i * period_days)
        durations = []
        for c in summaries:
            started_at = _parse_ts(c.get("started_at"))
            if started_at is None or c.get("duration_hours") is None:
                continue
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            if start <= started_at < end:
                durations.append(c["duration_hours"])
        period_rows.append({
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "case_count": len(durations),
            "mean_hours": round(statistics.fmean(durations), 2) if durations else None,
        })

    scored_means = [p["mean_hours"] for p in period_rows if p["mean_hours"] is not None]
    center_line = round(statistics.fmean(scored_means), 2) if len(scored_means) >= 2 else None
    stdev = statistics.pstdev(scored_means) if len(scored_means) >= 2 else None
    ucl = round(center_line + sigma * stdev, 2) if center_line is not None else None
    lcl = round(max(0.0, center_line - sigma * stdev), 2) if center_line is not None else None

    for p in period_rows:
        p["out_of_control"] = (
            center_line is not None and p["mean_hours"] is not None
            and (p["mean_hours"] > ucl or p["mean_hours"] < lcl)
        )

    return {
        "process": process, "period_days": period_days, "periods": period_rows,
        "center_line_hours": center_line, "ucl_hours": ucl, "lcl_hours": lcl,
        "sigma": sigma,
        "scored_periods": len(scored_means),
    }


# ── One-shot overview (MCP convenience) ────────────────────────────────────────


def summary(events: list[dict]) -> dict:
    """Everything above, in one call, broken out per known process plus an
    "untemplated" bucket — what process_mining_mcp_server.py's overview tool
    and the Continuous Monitoring 'Process Mining' tab's headline tiles use.
    Individual functions (variant_analysis, conformance_summary,
    cycle_time_stats, rework_summary) remain the finer-grained entry points
    for anything that needs to filter to one process."""
    cases = build_cases(events)
    all_summaries = list_case_summaries(cases)
    by_process: dict[str, int] = {}
    for c in all_summaries:
        key = c["process"] or "untemplated"
        by_process[key] = by_process.get(key, 0) + 1

    processes_out = {}
    for proc_id in PROCESS_TEMPLATES:
        if by_process.get(proc_id, 0) == 0:
            continue
        conf = conformance_summary(cases, proc_id)
        cyc = cycle_time_stats(cases, proc_id)
        rw = rework_summary(cases, proc_id)
        processes_out[proc_id] = {
            "label": PROCESS_TEMPLATES[proc_id]["label"],
            "case_count": by_process[proc_id],
            "conformance_rate": conf["conformance_rate"],
            "rework_rate": rw["rework_rate"],
            "bottleneck": cyc["bottleneck"],
            "avg_case_duration_hours": (cyc["case_duration"] or {}).get("mean_hours"),
        }

    return {
        "total_cases": len(all_summaries),
        "untemplated_cases": by_process.get("untemplated", 0),
        "processes": processes_out,
    }
