#!/usr/bin/env python3
"""
One-time migration: rewrite legacy risk_scores rows onto the canonical 0-25
/ R-A-G scale.

Before this session, predictive_analytics_tool.compute_risk_scores wrote
risk_scores rows on its own 0-10 scale with full-word rag_status ("Red" /
"Amber" / "Green"), while risk-engine.js's sync (POST /sync-risk-scores)
wrote 0-25 / letter ("R"/"A"/"G") rows into the SAME table — see
risk_rating_engine.py's module docstring for the full history. That
producer is now fixed to emit the canonical scale directly, but rows it
already wrote are still on the old scale. Posture Trend, run history, and
Evidence Packs read across runs, so a mix of scales on one chart/table is a
real, visible problem — this script normalizes existing rows in place.

Discriminator: a legacy row's rag_status is a full word (length > 1);
a canonical row's is a single letter. This is exact and requires no
guessing at which producer wrote a given run — LENGTH(rag_status) > 1 can
ONLY be true for "Red"/"Amber"/"Green".

Conversion (mirrors risk_rating_engine.score_from_raw10's own math, applied
retroactively rather than re-derived from ratios that may no longer be on
hand): score *= 2.5, base_score *= 2.5 (both were on the 0-10 scale;
0-25 = impact(0-5) x likelihood(0-5), and the old scale's own 0-10 range
maps onto exactly half of that per side, so a flat x2.5 is the correct,
exact inverse of how those rows were produced), rag_status -> its first
letter uppercased ("Red"->"R", "Amber"->"A", "Green"->"G").

Idempotent: re-running after a successful commit finds zero legacy rows
(rag_status is already single-letter) and does nothing.

Usage:
    python migrate_risk_scores_to_25.py           # dry run — reports only, writes nothing
    python migrate_risk_scores_to_25.py --commit   # actually rewrites the rows

Requires DATABASE_URL (same as every other script in this directory — see
db.py's is_available()).
"""
from __future__ import annotations

import argparse
import sys

import db


def _fetch_legacy_rows(cur) -> list[tuple]:
    cur.execute(
        """
        SELECT id, score, base_score, rag_status
        FROM risk_scores
        WHERE rag_status IS NOT NULL AND length(rag_status) > 1
        ORDER BY id
        """
    )
    return cur.fetchall()


def _letter_for(rag_status: str) -> str:
    return rag_status.strip()[:1].upper()


def run(commit: bool) -> int:
    """Returns the number of legacy rows found (migrated, if commit=True)."""
    if not db.is_available():
        print("DATABASE_URL not configured — nothing to do.", file=sys.stderr)
        return 0

    with db._conn() as conn:
        with conn.cursor() as cur:
            legacy_rows = _fetch_legacy_rows(cur)
            print(f"Found {len(legacy_rows)} legacy (0-10 scale, full-word rag_status) risk_scores row(s).")
            if not legacy_rows:
                return 0

            for row_id, score, base_score, rag_status in legacy_rows[:10]:
                new_score = round(float(score) * 2.5, 2) if score is not None else None
                new_base = round(float(base_score) * 2.5, 1) if base_score is not None else None
                new_rag = _letter_for(rag_status)
                print(f"  id={row_id}: score {score} -> {new_score}, "
                      f"base_score {base_score} -> {new_base}, rag_status {rag_status!r} -> {new_rag!r}")
            if len(legacy_rows) > 10:
                print(f"  ... and {len(legacy_rows) - 10} more")

            if not commit:
                print("\nDry run — no rows were written. Re-run with --commit to apply.")
                return len(legacy_rows)

            for row_id, score, base_score, rag_status in legacy_rows:
                new_score = round(float(score) * 2.5, 2) if score is not None else None
                new_base = round(float(base_score) * 2.5, 1) if base_score is not None else None
                cur.execute(
                    "UPDATE risk_scores SET score = %s, base_score = %s, rag_status = %s WHERE id = %s",
                    (new_score, new_base, _letter_for(rag_status), row_id),
                )
            conn.commit()
            print(f"\nMigrated {len(legacy_rows)} row(s).")
            return len(legacy_rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="Actually write the migration (default: dry run)")
    args = parser.parse_args()
    run(commit=args.commit)
