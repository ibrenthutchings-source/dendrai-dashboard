#!/usr/bin/env python3
"""
Synthetic Order-to-Cash / Procure-to-Pay transaction log generator.

Produces realistic O2C/P2P transactions shaped to actually exercise the real
`controls.oracle_fusion.order_to_cash`/`procure_to_pay` Rego packages
(pac_endpoints.py) via the real producer wiring added alongside this script
(UBO/models/uro.py's new EventTypes, mcp_governance.py's
_detect_system_flags/_SOURCE_EVENT_TO_PAC_PROCESS, UBO/pipeline/
bronze.py's _FLAG_EVENT_MAP, UBO/pipeline/silver.py's erp_transaction_detail
spread). Each record is inserted via the same mcp_governance._ingest_system_
event() call every sweep/connector already uses — the already-running
mcp_governance.start_polling() loop picks them up and adjudicates them for
real: real Bronze/Silver/Gold/Council/PaC, real policy_violations, real
adjudicated_tool_calls rows.

Three of the fourteen transaction kinds are generated as LINKED CASES rather
than independent records: Procure-to-Pay (Purchase Order -> Invoice ->
Payment), Order-to-Cash (Sales Order -> Billing -> Cash Application), and
Inventory Cycle (Goods Received -> Putaway Confirmed -> Goods Shipped), each
sharing one case_id across its steps with realistic increasing timestamps.
That case_id/process_step pair (see mcp_governance._write_adjudication and
its adjudicated_tool_calls.case_id/process_step columns) is what makes a REAL
directly-follows graph possible — "step A immediately preceded step B within
the same transaction" — as opposed to the categorical Domain/Tier/Verdict/
Rule breakdown every adjudication already supports regardless of case
membership. The other five kinds (revenue, customer/vendor master changes,
AR aging, SoD conflicts) aren't naturally multi-step lifecycles, so each
stays a one-step case of its own — no less real, just nothing to sequence.

A --violation-rate fraction of each case is deliberately built to breach
exactly one rule at exactly one step (never every step at once — that's not
how a real control failure looks) — same clean/violating shape as
pac_negative_tests.py's corpus fixtures for these two processes, just
randomized and volumed up.

Writes to whatever DATABASE_URL is active. Use --dry-run first.

    python generate_o2c_p2p_synthetic_log.py --dry-run --count 20
    python generate_o2c_p2p_synthetic_log.py --count 200 --violation-rate 0.15 --days 30
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from dotenv import load_dotenv

load_dotenv()

import db
import mcp_governance as mg

logger = logging.getLogger("generate_o2c_p2p_synthetic_log")

_ACTORS = [
    "jsmith@acme-corp.com", "mgarcia@acme-corp.com", "rpatel@acme-corp.com",
    "lchen@acme-corp.com", "kwilliams@acme-corp.com", "atanaka@acme-corp.com",
]
_CUSTOMERS = ["Northwind Traders", "Contoso Ltd", "Fabrikam Inc", "Globex Corp", "Initech LLC"]
_VENDORS = ["Acme Supplies", "Wayne Industrial", "Stark Components", "Umbrella Logistics", "Hooli Materials"]

_SERVER_NAME = "synthetic-o2c-p2p-generator"
_SYSTEM_TYPE = "oracle_fusion"


@dataclass
class TxnKind:
    name: str
    event_type: str
    flag: str
    resource_prefix: str
    build_clean: Callable[[random.Random, str], dict]
    build_violating: Callable[[random.Random, str], dict]


def _rid(prefix: str, rng: random.Random) -> str:
    return f"{prefix}-{rng.randint(1000, 9999)}"


def _case_id(rng: random.Random) -> str:
    # Derived from the seeded rng (not uuid.uuid4(), which isn't
    # seed-reproducible) — everything else generated for a given --seed is
    # reproducible, and case_id living inside each record's compared payload
    # is no exception.
    return f"{rng.getrandbits(40):010x}"


# ── Order-to-Cash builders ───────────────────────────────────────────────────

def _revenue_clean(rng, rid):
    return {
        "txn_type": "revenue_recognition", "txn_performance_obligation_satisfied": True,
        "txn_amount": rng.randint(20000, 400000), "txn_order_number": rid,
        "contract_value": rng.randint(50000, 900000), "contract_reviewed_by_legal": True,
        "contract_id": f"CTR-{rng.randint(100,999)}",
        "txn_constrained_estimate_documented": True, "txn_contract_id": f"CTR-{rng.randint(100,999)}",
    }


def _revenue_violating(rng, rid):
    d = _revenue_clean(rng, rid)
    d["txn_performance_obligation_satisfied"] = False
    return d


def _sales_order_clean(rng, rid):
    limit = rng.randint(50000, 200000)
    return {
        "so_status": "booked", "customer_credit_limit": limit,
        "so_total": rng.randint(1000, limit - 1000),
        "so_credit_override_approved_by": None, "so_order_number": rid,
    }


def _sales_order_violating(rng, rid):
    limit = rng.randint(50000, 200000)
    return {
        "so_status": "booked", "customer_credit_limit": limit,
        "so_total": limit + rng.randint(10000, 80000),
        "so_credit_override_approved_by": None, "so_order_number": rid,
    }


def _billing_clean(rng, rid):
    return {
        "inv_type": "manual", "inv_approved_by": rng.choice(_ACTORS), "inv_amount": rng.randint(500, 9500),
        "inv_number": rid, "inv_billing_date": "2026-06-01", "inv_shipment_date": "2026-05-28",
        "inv_days_billed_before_shipment": 0,
    }


def _billing_violating(rng, rid):
    return {
        "inv_type": "manual", "inv_approved_by": None, "inv_amount": rng.randint(11000, 60000),
        "inv_number": rid, "inv_billing_date": "2026-06-01", "inv_shipment_date": "2026-05-28",
        "inv_days_billed_before_shipment": 0,
    }


def _cash_clean(rng, rid):
    return {"cash_unapplied_days": rng.randint(0, 25), "cash_receipt_number": rid, "cash_amount": rng.randint(1000, 50000)}


def _cash_violating(rng, rid):
    return {"cash_unapplied_days": rng.randint(31, 90), "cash_receipt_number": rid, "cash_amount": rng.randint(1000, 50000)}


def _customer_master_clean(rng, rid):
    return {"field": "bank_account", "dual_approved": True, "customer_name": rng.choice(_CUSTOMERS)}


def _customer_master_violating(rng, rid):
    return {"field": rng.choice(["bank_account", "tax_id", "payment_terms"]), "dual_approved": False,
            "customer_name": rng.choice(_CUSTOMERS)}


def _ar_aging_clean(rng, rid):
    return {"ar_days_outstanding": rng.randint(0, 60), "ar_amount": rng.randint(1000, 200000),
            "ar_customer_name": rng.choice(_CUSTOMERS), "ar_collection_action_documented": True}


def _ar_aging_violating(rng, rid):
    return {"ar_days_outstanding": rng.randint(91, 200), "ar_amount": rng.randint(51000, 250000),
            "ar_customer_name": rng.choice(_CUSTOMERS), "ar_collection_action_documented": False}


# ── Procure-to-Pay builders ──────────────────────────────────────────────────

def _po_clean(rng, rid):
    return {"po_total": rng.randint(500, 45000), "po_vp_approved": True, "po_number": rid,
            "po_cfo_approved": True, "po_type": "standard", "po_annual_review_completed": True}


def _po_violating(rng, rid):
    return {"po_total": rng.randint(55000, 240000), "po_vp_approved": False, "po_number": rid,
            "po_cfo_approved": False, "po_type": "standard", "po_annual_review_completed": True}


def _invoice_clean(rng, rid):
    amount = rng.randint(1000, 40000)
    return {"inv_matching_type": "3_way", "inv_amount": amount, "po_total": amount, "inv_number": rid,
            "goods_receipt_confirmed": True, "inv_duplicate_score": round(rng.uniform(0.0, 0.4), 2),
            "inv_duplicate_override_reason": None}


def _invoice_violating(rng, rid):
    """Alternates between the two distinct controls this rule-group covers
    (P2P-P002 variance, P2P-P004 duplicate) so both get real coverage."""
    po_total = rng.randint(5000, 40000)
    if rng.random() < 0.5:
        return {"inv_matching_type": "3_way", "inv_amount": int(po_total * rng.uniform(1.15, 1.6)),
                "po_total": po_total, "inv_number": rid, "goods_receipt_confirmed": True,
                "inv_duplicate_score": 0.1, "inv_duplicate_override_reason": None}
    return {"inv_matching_type": "3_way", "inv_amount": po_total, "po_total": po_total, "inv_number": rid,
            "goods_receipt_confirmed": True, "inv_duplicate_score": round(rng.uniform(0.86, 0.99), 2),
            "inv_duplicate_override_reason": None}


def _vendor_master_clean(rng, rid):
    return {"field": "bank_account_number", "dual_approved": True, "vendor_name": rng.choice(_VENDORS)}


def _vendor_master_violating(rng, rid):
    return {"field": rng.choice(["bank_account_number", "bank_routing_number", "payment_method"]),
            "dual_approved": False, "vendor_name": rng.choice(_VENDORS)}


def _payment_clean(rng, rid):
    return {"pay_batch_total": rng.randint(1000, 90000), "pay_batch_treasury_approved": True,
            "pay_batch_name": rid, "pay_type": "ach", "pay_two_factor_confirmed": True,
            "pay_id": rid, "pay_amount": rng.randint(1000, 90000)}


def _payment_violating(rng, rid):
    return {"pay_batch_total": rng.randint(110000, 500000), "pay_batch_treasury_approved": False,
            "pay_batch_name": rid, "pay_type": "wire_transfer", "pay_two_factor_confirmed": False,
            "pay_id": rid, "pay_amount": rng.randint(110000, 500000)}


def _sod_clean(rng, rid):
    return {"user_oracle_roles": [rng.choice(["AP_INVOICE_ENTRY", "PO_BUYER", "AP_PAYMENT_APPROVAL"])],
            "user_username": rng.choice(_ACTORS)}


def _sod_violating(rng, rid):
    conflict = rng.choice([
        ["AP_INVOICE_ENTRY", "AP_PAYMENT_APPROVAL"],
        ["PO_BUYER", "AP_INVOICE_APPROVAL"],
    ])
    return {"user_oracle_roles": conflict, "user_username": rng.choice(_ACTORS)}


# ── Inventory Cycle builders (Receive -> Putaway -> Ship) ───────────────────

def _goods_receipt_clean(rng, rid):
    qty_ordered = rng.randint(50, 2000)
    return {"grn_po_number": _rid("PO", rng), "grn_qty_ordered": qty_ordered,
            "grn_qty_received": qty_ordered, "grn_quality_inspection_passed": True,
            "grn_received_by": rng.choice(_ACTORS)}


def _goods_receipt_violating(rng, rid):
    qty_ordered = rng.randint(50, 2000)
    return {"grn_po_number": _rid("PO", rng), "grn_qty_ordered": qty_ordered,
            "grn_qty_received": qty_ordered + rng.randint(int(qty_ordered * 0.25), qty_ordered),
            "grn_quality_inspection_passed": False, "grn_received_by": rng.choice(_ACTORS)}


def _putaway_clean(rng, rid):
    return {"putaway_location": f"WH-{rng.randint(1,6)}-{rng.randint(10,99)}",
            "putaway_qty": rng.randint(50, 2000), "putaway_variance_pct": 0.0,
            "putaway_cycle_count_matched": True}


def _putaway_violating(rng, rid):
    return {"putaway_location": f"WH-{rng.randint(1,6)}-{rng.randint(10,99)}",
            "putaway_qty": rng.randint(50, 2000), "putaway_variance_pct": round(rng.uniform(8.0, 30.0), 1),
            "putaway_cycle_count_matched": False}


def _shipment_clean(rng, rid):
    return {"shipment_so_number": _rid("SO", rng), "shipment_qty": rng.randint(10, 1800),
            "shipment_carrier": rng.choice(["FedEx Freight", "UPS", "DHL Supply Chain", "XPO Logistics"]),
            "shipment_matches_sales_order": True}


def _shipment_violating(rng, rid):
    return {"shipment_so_number": None, "shipment_qty": rng.randint(10, 1800),
            "shipment_carrier": rng.choice(["FedEx Freight", "UPS", "DHL Supply Chain", "XPO Logistics"]),
            "shipment_matches_sales_order": False}


REVENUE_KIND        = TxnKind("revenue", "REVENUE_RECOGNITION_EVENT", "revenue_recognition_event", "SO", _revenue_clean, _revenue_violating)
SALES_ORDER_KIND     = TxnKind("sales_order", "SALES_ORDER_CREDIT_EVENT", "sales_order_credit_event", "SO", _sales_order_clean, _sales_order_violating)
BILLING_KIND         = TxnKind("billing", "BILLING_EVENT", "billing_event", "INV", _billing_clean, _billing_violating)
CASH_KIND            = TxnKind("cash", "CASH_APPLICATION_EVENT", "cash_application_event", "CR", _cash_clean, _cash_violating)
CUSTOMER_MASTER_KIND = TxnKind("customer_master", "CUSTOMER_MASTER_CHANGE", "customer_master_change", "CUST", _customer_master_clean, _customer_master_violating)
AR_AGING_KIND        = TxnKind("ar_aging", "AR_AGING_EVENT", "ar_aging_event", "AR", _ar_aging_clean, _ar_aging_violating)
PURCHASE_ORDER_KIND  = TxnKind("purchase_order", "PURCHASE_ORDER_EVENT", "purchase_order_event", "PO", _po_clean, _po_violating)
INVOICE_KIND         = TxnKind("invoice", "INVOICE_MATCH_EVENT", "invoice_match_event", "INV", _invoice_clean, _invoice_violating)
VENDOR_MASTER_KIND   = TxnKind("vendor_master", "VENDOR_MASTER_CHANGE", "vendor_master_change", "VEND", _vendor_master_clean, _vendor_master_violating)
PAYMENT_KIND         = TxnKind("payment", "PAYMENT_RUN_EVENT", "payment_run_event", "PAY", _payment_clean, _payment_violating)
SOD_KIND             = TxnKind("sod", "PROCUREMENT_SOD_CONFLICT", "procurement_sod_conflict", "USR", _sod_clean, _sod_violating)
GOODS_RECEIPT_KIND   = TxnKind("goods_receipt", "GOODS_RECEIPT_EVENT", "goods_receipt_event", "GRN", _goods_receipt_clean, _goods_receipt_violating)
PUTAWAY_KIND         = TxnKind("putaway", "INVENTORY_PUTAWAY_EVENT", "inventory_putaway_event", "PUT", _putaway_clean, _putaway_violating)
SHIPMENT_KIND        = TxnKind("shipment", "GOODS_SHIPMENT_EVENT", "goods_shipment_event", "SHP", _shipment_clean, _shipment_violating)

# Standalone kinds: not naturally a multi-step lifecycle, so each instance is
# its own one-step case — still tagged with a case_id/process_step (every
# adjudication gets one), just nothing to sequence it against.
_STANDALONE = [
    (REVENUE_KIND, "Revenue Recognized"),
    (CUSTOMER_MASTER_KIND, "Customer Master Change"),
    (AR_AGING_KIND, "AR Aging Review"),
    (VENDOR_MASTER_KIND, "Vendor Master Change"),
    (SOD_KIND, "SoD Check"),
]

# Linked cases: (kind, process_step label, (min_days, max_days) gap from the
# PREVIOUS step — the first step's gap is ignored). Real lifecycles don't
# take the same 2 seconds a script would default to, so each step lands
# realistically days after the one before it.
_P2P_CASE = [
    (PURCHASE_ORDER_KIND, "Purchase Order Created", (0, 0)),
    (INVOICE_KIND, "Invoice Matched", (2, 10)),
    (PAYMENT_KIND, "Payment Released", (3, 14)),
]
_O2C_CASE = [
    (SALES_ORDER_KIND, "Sales Order Booked", (0, 0)),
    (BILLING_KIND, "Invoice Billed", (1, 5)),
    (CASH_KIND, "Cash Applied", (5, 30)),
]
_INVENTORY_CASE = [
    (GOODS_RECEIPT_KIND, "Goods Received", (0, 0)),
    (PUTAWAY_KIND, "Putaway Confirmed", (0, 2)),
    (SHIPMENT_KIND, "Goods Shipped", (2, 21)),
]

# Weighting: cases (3 records) count more toward --count than standalone
# events (1 record) per "unit" drawn, so mix the two pools by drawing case
# vs. standalone with a probability tuned so the OUTPUT record count roughly
# matches the requested --count regardless of the mix.
_CASE_TEMPLATES = [("procure_to_pay", _P2P_CASE), ("order_to_cash", _O2C_CASE), ("inventory_cycle", _INVENTORY_CASE)]


def _build_step_record(kind: TxnKind, detail: dict, case_id: str, process_step: str,
                        violating: bool, actor: str, when: datetime) -> dict:
    payload = {kind.flag: True, "erp_transaction_detail": detail,
               "case_id": case_id, "process_step": process_step}
    return {
        "server_name": _SERVER_NAME,
        "system_type": _SYSTEM_TYPE,
        "event_type": kind.event_type,
        "event_id": str(uuid.uuid4()),
        "actor": actor,
        "action": f"{kind.name}_{'violation' if violating else 'clean'}",
        "resource": detail.get("po_number") or detail.get("so_order_number") or detail.get("inv_number")
                    or detail.get("pay_id") or detail.get("cash_receipt_number")
                    or detail.get("grn_po_number") or detail.get("shipment_so_number") or case_id,
        "severity": "HIGH" if violating else "INFO",
        "payload": payload,
        "created_at": when,
    }


def _build_case(steps: list[tuple[TxnKind, str, tuple[float, float]]], rng: random.Random,
                 case_violates: bool, base_when: datetime, now: datetime) -> list[dict]:
    case_id = _case_id(rng)
    actor = rng.choice(_ACTORS)
    violate_idx = rng.randrange(len(steps)) if case_violates else -1
    when = base_when
    records: list[dict] = []
    prior_amount: float | None = None

    for i, (kind, process_step, gap_days) in enumerate(steps):
        if i > 0:
            # Clamp to `now` — a later step's realistic gap (up to 30 days
            # for O2C's cash-application step) can otherwise push it into
            # the future when the case started close to `now` or the
            # --days window is smaller than the lifecycle's own length.
            # Clamping (never un-clamping) keeps timestamps non-decreasing.
            when = min(when + timedelta(days=rng.uniform(*gap_days)), now)
        rid = _rid(kind.resource_prefix, rng)
        violating = i == violate_idx
        detail = kind.build_violating(rng, rid) if violating else kind.build_clean(rng, rid)
        # Keep the amount coherent across a CLEAN step following a prior
        # step — a real invoice matches its PO, a real cash receipt matches
        # its invoice. Skip this for the deliberately-violating step, whose
        # builder already constructed a specific, meaningful mismatch that
        # an amount override would just erase.
        if not violating and prior_amount is not None:
            for amount_field in ("po_total", "inv_amount", "cash_amount"):
                if amount_field in detail:
                    detail[amount_field] = prior_amount
        for amount_field in ("po_total", "so_total", "inv_amount", "cash_amount", "pay_amount"):
            if amount_field in detail:
                prior_amount = detail[amount_field]
        records.append(_build_step_record(kind, detail, case_id, process_step, violating, actor, when))
    return records


def _build_standalone(kind: TxnKind, process_step: str, rng: random.Random,
                       violating: bool, when: datetime) -> dict:
    case_id = _case_id(rng)
    rid = _rid(kind.resource_prefix, rng)
    detail = kind.build_violating(rng, rid) if violating else kind.build_clean(rng, rid)
    return _build_step_record(kind, detail, case_id, process_step, violating, rng.choice(_ACTORS), when)


def generate(count: int, violation_rate: float, days: int, seed: int | None = None) -> list[dict]:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    records: list[dict] = []

    while len(records) < count:
        # Draw a case template about 2/5 of the time (cases produce 3
        # records each, so this keeps the case:standalone record ratio
        # realistic rather than letting 3-record cases dominate the log).
        base_when = now - timedelta(seconds=rng.randint(0, max(days, 1) * 86400))
        if rng.random() < 0.4:
            _, steps = rng.choice(_CASE_TEMPLATES)
            case_violates = rng.random() < violation_rate
            records.extend(_build_case(steps, rng, case_violates, base_when, now))
        else:
            kind, process_step = rng.choice(_STANDALONE)
            violating = rng.random() < violation_rate
            records.append(_build_standalone(kind, process_step, rng, violating, base_when))

    records = records[:count] if len(records) > count else records
    records.sort(key=lambda r: r["created_at"])
    return records


def push(records: list[dict]) -> dict:
    """Insert every record via the real mcp_governance ingestion path —
    _detect_system_flags + _ingest_system_event, same two calls every real
    sweep/connector already uses. Returns a small summary."""
    ingested, skipped_duplicate, by_kind = 0, 0, {}
    for r in records:
        flags = mg._detect_system_flags({
            "action": r["action"], "resource": r["resource"], "severity": r["severity"],
            "event_type": r["event_type"], "payload": r["payload"],
        })
        row_id = mg._ingest_system_event(
            r["server_name"], r["system_type"], r["event_type"], r["event_id"],
            r["actor"], r["action"], r["resource"], r["severity"], flags,
            r["payload"], None, created_at=r["created_at"],
        )
        by_kind[r["action"]] = by_kind.get(r["action"], 0) + 1
        if row_id is not None:
            ingested += 1
        else:
            skipped_duplicate += 1
    return {"ingested": ingested, "skipped_duplicate": skipped_duplicate, "by_action": by_kind}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=200, help="Total records to generate (cases contribute 3 each, standalone events 1)")
    parser.add_argument("--violation-rate", type=float, default=0.15, help="Fraction of cases/standalone events deliberately built to breach a rule (at exactly one step for a case)")
    parser.add_argument("--days", type=int, default=30, help="Spread each case/event's start over this many days back from now")
    parser.add_argument("--seed", type=int, default=None, help="Random seed, for reproducible runs")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be generated; don't touch the database")
    args = parser.parse_args()

    records = generate(args.count, args.violation_rate, args.days, seed=args.seed)

    if args.dry_run:
        for r in records[:20]:
            p = r["payload"]
            print(f"{r['created_at'].isoformat()}  {p['case_id']}  {p['process_step']:<24}  {r['event_type']:<28}  {r['resource']}")
        if len(records) > 20:
            print(f"... and {len(records) - 20} more")
        n_violating = sum(1 for r in records if r["severity"] == "HIGH")
        n_cases = len({r["payload"]["case_id"] for r in records})
        print(f"\n{len(records)} records generated across {n_cases} cases, {n_violating} violating ({n_violating/len(records):.0%}). "
              f"Re-run without --dry-run to insert.")
        return 0

    if not db.init_db():
        logger.error("DATABASE_URL not configured or unreachable — nothing to insert into.")
        return 1

    result = push(records)
    logger.info("Ingested %d record(s), %d skipped as duplicate. By action: %s",
                result["ingested"], result["skipped_duplicate"], result["by_action"])
    print(f"Ingested {result['ingested']}/{len(records)} record(s) into observability.system_telemetry. "
          f"The running mcp_governance poller will adjudicate them on its next cycle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
