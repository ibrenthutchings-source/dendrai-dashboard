#!/usr/bin/env python3
"""
One-time cleanup: delete synthetic ("SYNTHETIC" source) JOURNAL_ENTRY
exception rows whose amount is >= $1,000,000.

synthetic_transaction_tool.py's JE amount generator (_RECORD_TO_REPORT's
"Journal Entry Posted" step) used to draw from (5000, 2_000_000) — now capped
to (5000, 999_999), so no NEW synthetic JE can hit $1M. This script removes
rows already in the DB from before that fix.

Targets exception_control_events rows with event_type='JOURNAL_ENTRY' and
system_source='SYNTHETIC' whose point_in_time_features->>'amount' clears the
threshold. exception_model_inferences and exception_auditor_triage rows for
each deleted event are removed automatically (ON DELETE CASCADE) — nothing
else references exception_control_events.id.

Dry-run by default — prints the count and a sample of matching rows, deletes
nothing. Pass --execute to actually delete.

    python cleanup_oversized_synthetic_je.py            # dry run
    python cleanup_oversized_synthetic_je.py --execute  # actually delete
"""
from __future__ import annotations

import argparse

import db

_THRESHOLD = 1_000_000

_SELECT_SQL = """
    SELECT id, event_timestamp, (point_in_time_features->>'amount')::numeric AS amount
    FROM exception_control_events
    WHERE event_type = 'JOURNAL_ENTRY'
      AND system_source = 'SYNTHETIC'
      AND (point_in_time_features->>'amount')::numeric >= %s
    ORDER BY amount DESC
"""

_DELETE_SQL = """
    DELETE FROM exception_control_events
    WHERE event_type = 'JOURNAL_ENTRY'
      AND system_source = 'SYNTHETIC'
      AND (point_in_time_features->>'amount')::numeric >= %s
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="actually delete (default: dry run only)")
    args = parser.parse_args()

    if not db.init_db():
        print("DATABASE_URL not configured / DB unavailable — nothing to do.")
        return

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SELECT_SQL, (_THRESHOLD,))
            rows = cur.fetchall()

    print(f"{len(rows)} synthetic JOURNAL_ENTRY exception row(s) have amount >= ${_THRESHOLD:,}")
    for r in rows[:20]:
        print(f"  id={r[0]}  event_timestamp={r[1]}  amount=${r[2]:,.2f}")
    if len(rows) > 20:
        print(f"  ... and {len(rows) - 20} more")

    if not rows:
        return

    if not args.execute:
        print("\nDry run only — nothing deleted. Re-run with --execute to delete these rows "
              "(cascades to exception_model_inferences / exception_auditor_triage).")
        return

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_DELETE_SQL, (_THRESHOLD,))
            deleted = cur.rowcount

    print(f"\nDeleted {deleted} row(s).")


if __name__ == "__main__":
    main()
