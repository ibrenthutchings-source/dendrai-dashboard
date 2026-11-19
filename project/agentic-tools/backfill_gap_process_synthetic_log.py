#!/usr/bin/env python3
"""
One-shot historical backfill for the 8 (system, process) pairs that
synthetic_transaction_tool.py's own ProcessDefs cover — Hire to Retire, IAM,
Record to Report, Fixed Assets, Vendor Management, Payroll, Inventory
Master, Customer Master File. generate_o2c_p2p_synthetic_log.py already owns
a one-shot backfill for the other three (Procure to Pay/Order to Cash/
Receive to Ship) — this script is its counterpart for the rest of the
catalog, not a replacement.

The gap this closes: _seed_synthetic_connectors() (api_server.py) wires all
11 processes into a live 300s-interval poll connector, and each tick's
pull_events() fabricates just 1-3 cases — real, but far too slow to give
Operational Efficiency's bottleneck/rework/variant/SLA findings anything to
show right after a fresh deploy. This reuses the exact same case builders
the live connector calls (_build_own_case/_build_own_standalone in
synthetic_transaction_tool.py) to generate a realistic window of history in
one run, backdated across --days, and pushes it through the identical
mcp_governance ingestion path (_detect_system_flags + _ingest_system_event)
generate_o2c_p2p_synthetic_log.py already uses — so the running
mcp_governance poller adjudicates it exactly like any other synthetic (or
real) event on its next cycle, with the real case_id/process_step pair
process_mining_tool.py needs intact.

(display_name, system_label) below must stay in sync with
api_server.py's _SYNTHETIC_CONNECTORS_DEFAULT — that's the one place a
stable server_name actually matters, since it's how Continuous Watch's
Source System breakdown and this script's rows end up attributed to the
same connector identity instead of a second, orphaned one.

    python backfill_gap_process_synthetic_log.py --dry-run
    python backfill_gap_process_synthetic_log.py --cases-per-process 40 --days 30
"""
from __future__ import annotations

import argparse
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

load_dotenv()

# Same local-run rewrite reset_admin_password.py uses, and for the same
# reason: `railway run` injects this environment's real DATABASE_URL, but it
# points at a *.railway.internal host only reachable from inside Railway's
# network — rewrite just the host:port to that database's public TCP proxy
# endpoint before anything imports db.py. Keep this dict in sync with
# reset_admin_password.py's _INTERNAL_TO_PROXY if either ever changes.
_INTERNAL_TO_PROXY = {
    "postgres-sandbox.railway.internal": "trolley.proxy.rlwy.net:26897",
    "postgres-uat.railway.internal": "altaria.proxy.rlwy.net:35832",
    "postgres-development.railway.internal": "altaria.proxy.rlwy.net:30786",
    "postgres-production.railway.internal": "trolley.proxy.rlwy.net:26763",
}


def _rewrite_for_local_run(url: str) -> str:
    parts = urlsplit(url)
    proxy_hostport = _INTERNAL_TO_PROXY.get(parts.hostname or "")
    if not proxy_hostport:
        return url
    userinfo = parts.netloc.split("@", 1)[0] if "@" in parts.netloc else ""
    new_netloc = f"{userinfo}@{proxy_hostport}" if userinfo else proxy_hostport
    return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))


_url = os.environ.get("DATABASE_URL", "")
if _url:
    os.environ["DATABASE_URL"] = _rewrite_for_local_run(_url)

import db
import mcp_governance as mg
from synthetic_transaction_tool import _PROCESS_DEFS, _build_own_case, _build_own_standalone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_GAP_CONNECTORS = {
    "hire_to_retire":       ("Oracle Fusion — Hire to Retire",            "Oracle Fusion"),
    "iam":                  ("SailPoint — Identity & Access Management",  "SailPoint"),
    "record_to_report":     ("SAP HANA — Record to Report",               "SAP HANA"),
    "fixed_assets":         ("SAP HANA — Fixed Assets",                   "SAP HANA"),
    "vendor_management":    ("SAP HANA — Vendor Management",              "SAP HANA"),
    "payroll":              ("SAP HANA — Payroll",                        "SAP HANA"),
    "inventory_master":     ("Dynamics 365 — Inventory Master",           "Dynamics 365"),
    "customer_master_file": ("ServiceNow — Customer Master File",         "ServiceNow"),
}


def generate(cases_per_process: int, days: int, seed: "int | None" = None) -> list[dict]:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    events: list[dict] = []
    for process_id, pdef in _PROCESS_DEFS.items():
        for _ in range(cases_per_process):
            # Each case gets its own backdated anchor spread across the
            # trailing window, rather than all cases anchoring to the actual
            # current moment — same role --days plays in
            # generate_o2c_p2p_synthetic_log.py's generate().
            anchor = now - timedelta(seconds=rng.randint(0, max(days, 1) * 86400))
            if pdef.standalone:
                events.append(_build_own_standalone(pdef, rng, anchor, process_id))
            else:
                events.extend(_build_own_case(pdef, rng, anchor, process_id))
    events.sort(key=lambda e: e["created_at"])
    return events


def push(events: list[dict]) -> dict:
    ingested, skipped_duplicate, by_process = 0, 0, {}
    for e in events:
        process = e["raw_payload"].get("process", "unknown")
        display_name, system_label = _GAP_CONNECTORS.get(process, (process, process))
        server_name = f"synthetic_transaction:{display_name}"[:128]
        flags = mg._detect_system_flags({
            "action": e["action"], "resource": e["resource"], "severity": e["severity"],
            "event_type": e["event_type"], "payload": e["raw_payload"],
        })
        row_id = mg._ingest_system_event(
            server_name, system_label, e["event_type"], e["event_id"],
            e["actor"], e["action"], e["resource"], e["severity"], flags,
            e["raw_payload"], None, created_at=e["created_at"],
        )
        by_process[process] = by_process.get(process, 0) + 1
        if row_id is not None:
            ingested += 1
        else:
            skipped_duplicate += 1
    return {"ingested": ingested, "skipped_duplicate": skipped_duplicate, "by_process": by_process}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases-per-process", type=int, default=35,
                         help="Cases (or standalone events) to generate for EACH of the 8 processes")
    parser.add_argument("--days", type=int, default=30, help="Spread each case's start over this many days back from now")
    parser.add_argument("--seed", type=int, default=None, help="Random seed, for reproducible runs")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be generated; don't touch the database")
    args = parser.parse_args()

    events = generate(args.cases_per_process, args.days, seed=args.seed)

    if args.dry_run:
        for e in events[:20]:
            p = e["raw_payload"]
            print(f"{e['created_at'].isoformat()}  {p['process']:<22}  {p['case_id']}  {p['process_step']:<28}  {e['event_type']}")
        if len(events) > 20:
            print(f"... and {len(events) - 20} more")
        n_cases = len({e["raw_payload"]["case_id"] for e in events})
        n_violating = sum(1 for e in events if e["severity"] == "HIGH")
        print(f"\n{len(events)} records generated across {n_cases} cases/standalone events over 8 processes, "
              f"{n_violating} violating ({n_violating/len(events):.0%}). Re-run without --dry-run to insert.")
        return 0

    if not db.init_db():
        logger.error("DATABASE_URL not configured or unreachable — nothing to insert into.")
        return 1

    result = push(events)
    logger.info("Ingested %d record(s), %d skipped as duplicate. By process: %s",
                result["ingested"], result["skipped_duplicate"], result["by_process"])
    print(f"Ingested {result['ingested']}/{len(events)} record(s) into observability.system_telemetry. "
          f"The running mcp_governance poller will adjudicate them on its next cycle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
