#!/usr/bin/env python3
"""
One-time (but safely re-runnable) backfill: categorize every risk_scores row
still missing assigned_domain, by calling the real /categorize-domains
endpoint logic per run_id — same keyword-fallback + Claude path, same
db.bulk_save_risk_domains persistence, exactly as a live screen visit would
trigger. Not a reimplementation: this exercises the production code path so
the backfill and the ongoing per-visit categorization can never drift apart.

Idempotent: only selects rows where assigned_domain IS NULL, so re-running
after a partial failure just picks up wherever it left off.

Usage:
    python backfill_risk_domains.py            # do it
    python backfill_risk_domains.py --dry-run  # show what would be sent, no writes
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

import db
import risk_register_endpoints as rre


async def main(dry_run: bool) -> None:
    if not db.init_db():
        sys.exit("Database not available (DATABASE_URL not set or unreachable)")

    def _fetch_pending():
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT run_id, risk_ref, risk_name, category
                    FROM risk_scores
                    WHERE assigned_domain IS NULL
                    ORDER BY run_id
                    """
                )
                return cur.fetchall()

    rows = db._run(_fetch_pending, default=[]) or []
    if not rows:
        print("Nothing to backfill — every risk_scores row already has assigned_domain.")
        return

    by_run: dict[int, list[dict]] = {}
    for run_id, ref, name, category in rows:
        by_run.setdefault(run_id, []).append({"ref": ref or "", "name": name or "", "category": category or ""})

    print(f"{len(rows)} rows across {len(by_run)} runs need categorization.")
    if dry_run:
        for run_id, risks in list(by_run.items())[:3]:
            print(f"  run {run_id}: {len(risks)} risks, e.g. {risks[0]['name']!r}")
        print("(dry run — no writes)")
        return

    total_persisted = 0
    for i, (run_id, risks) in enumerate(by_run.items(), 1):
        req = rre.CategorizeDomainRequest(risks=risks, run_id=run_id)
        result = await rre.categorize_domains(req)
        total_persisted += len(result.get("domains", {}))
        if i % 10 == 0 or i == len(by_run):
            print(f"  [{i}/{len(by_run)}] run {run_id}: {len(risks)} risks categorized")

    remaining = db._run(_fetch_pending, default=[]) or []
    print(f"Done. {len(rows) - len(remaining)} rows now have assigned_domain; {len(remaining)} still missing.")
    if remaining:
        sample_names = {r[2] for r in remaining[:10]}
        print(f"  Still-missing sample risk_names: {sample_names}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
