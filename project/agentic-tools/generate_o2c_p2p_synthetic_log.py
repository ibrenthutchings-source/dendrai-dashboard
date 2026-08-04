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

A --violation-rate fraction of each transaction kind is deliberately built
to breach exactly the one rule that kind's baseline otherwise satisfies —
same clean/violating shape as pac_negative_tests.py's corpus fixtures for
these two processes, just randomized and volumed up.

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
from dataclasses import dataclass, field
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


TXN_KINDS: list[TxnKind] = [
    TxnKind("revenue", "REVENUE_RECOGNITION_EVENT", "revenue_recognition_event", "SO", _revenue_clean, _revenue_violating),
    TxnKind("sales_order", "SALES_ORDER_CREDIT_EVENT", "sales_order_credit_event", "SO", _sales_order_clean, _sales_order_violating),
    TxnKind("billing", "BILLING_EVENT", "billing_event", "INV", _billing_clean, _billing_violating),
    TxnKind("cash", "CASH_APPLICATION_EVENT", "cash_application_event", "CR", _cash_clean, _cash_violating),
    TxnKind("customer_master", "CUSTOMER_MASTER_CHANGE", "customer_master_change", "CUST", _customer_master_clean, _customer_master_violating),
    TxnKind("ar_aging", "AR_AGING_EVENT", "ar_aging_event", "AR", _ar_aging_clean, _ar_aging_violating),
    TxnKind("purchase_order", "PURCHASE_ORDER_EVENT", "purchase_order_event", "PO", _po_clean, _po_violating),
    TxnKind("invoice", "INVOICE_MATCH_EVENT", "invoice_match_event", "INV", _invoice_clean, _invoice_violating),
    TxnKind("vendor_master", "VENDOR_MASTER_CHANGE", "vendor_master_change", "VEND", _vendor_master_clean, _vendor_master_violating),
    TxnKind("payment", "PAYMENT_RUN_EVENT", "payment_run_event", "PAY", _payment_clean, _payment_violating),
    TxnKind("sod", "PROCUREMENT_SOD_CONFLICT", "procurement_sod_conflict", "USR", _sod_clean, _sod_violating),
]


def _build_record(kind: TxnKind, rng: random.Random, violating: bool, when: datetime) -> dict:
    rid = _rid(kind.resource_prefix, rng)
    detail = kind.build_violating(rng, rid) if violating else kind.build_clean(rng, rid)
    payload = {kind.flag: True, "erp_transaction_detail": detail}
    return {
        "server_name": _SERVER_NAME,
        "system_type": _SYSTEM_TYPE,
        "event_type": kind.event_type,
        "event_id": str(uuid.uuid4()),
        "actor": rng.choice(_ACTORS),
        "action": f"{kind.name}_{'violation' if violating else 'clean'}",
        "resource": rid,
        "severity": "HIGH" if violating else "INFO",
        "payload": payload,
        "created_at": when,
    }


def generate(count: int, violation_rate: float, days: int, seed: int | None = None) -> list[dict]:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    records = []
    for _ in range(count):
        kind = rng.choice(TXN_KINDS)
        violating = rng.random() < violation_rate
        when = now - timedelta(seconds=rng.randint(0, max(days, 1) * 86400))
        records.append(_build_record(kind, rng, violating, when))
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
    parser.add_argument("--count", type=int, default=200, help="Total records to generate (split across 11 transaction kinds)")
    parser.add_argument("--violation-rate", type=float, default=0.15, help="Fraction of records deliberately built to breach a rule")
    parser.add_argument("--days", type=int, default=30, help="Spread timestamps over this many days back from now")
    parser.add_argument("--seed", type=int, default=None, help="Random seed, for reproducible runs")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be generated; don't touch the database")
    args = parser.parse_args()

    records = generate(args.count, args.violation_rate, args.days, seed=args.seed)

    if args.dry_run:
        for r in records[:20]:
            print(f"{r['created_at'].isoformat()}  {r['event_type']:<28}  {r['action']:<28}  {r['resource']}")
        if len(records) > 20:
            print(f"... and {len(records) - 20} more")
        n_violating = sum(1 for r in records if r["severity"] == "HIGH")
        print(f"\n{len(records)} records generated, {n_violating} violating ({n_violating/len(records):.0%}). "
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
