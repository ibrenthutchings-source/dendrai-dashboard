#!/usr/bin/env python3
"""
Integration test: PostgreSQL <-> UI persistence path.

Exercises the exact db.py functions that api_server.py invokes when the React UI
runs the risk loop — INSERT, UPDATE, and UPSERT — then verifies round-trip reads
and cleans up the test rows.

    DATABASE_URL=postgresql://... python integration_test.py

Uses a sentinel ticker (ZZIT) so it never touches real data, and deletes every
row it creates (children first, FK-safe) on the way out.
"""

from __future__ import annotations

import os
import sys

import db
import mcp_governance

TICKER = "ZZIT"
PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results = []


def check(name: str, cond: bool, detail: str = ""):
    results.append(cond)
    mark = PASS if cond else FAIL
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def cleanup(run_id, company_id):
    """Delete test rows children-first (FKs have no ON DELETE CASCADE)."""
    from db import _conn
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM observability.adjudicated_tool_calls WHERE uro_id = %s", ("ZZIT-URO-1",))
            if run_id:
                cur.execute("SELECT id FROM hitl_sessions WHERE run_id=%s", (run_id,))
                sess = [r[0] for r in cur.fetchall()]
                for sid in sess:
                    cur.execute("DELETE FROM risk_approval_signoffs WHERE approval_id IN "
                                "(SELECT id FROM risk_approvals WHERE session_id=%s)", (sid,))
                    cur.execute("DELETE FROM risk_approvals WHERE session_id=%s", (sid,))
                cur.execute("DELETE FROM hitl_sessions WHERE run_id=%s", (run_id,))
                for t in ("ai_analyses", "risk_scores", "financial_ratios",
                          "beneish_mscores"):
                    cur.execute(f"DELETE FROM {t} WHERE run_id=%s", (run_id,))
                cur.execute("DELETE FROM risk_loop_runs WHERE id=%s", (run_id,))
            cur.execute("DELETE FROM fred_observations WHERE series_id IN "
                        "(SELECT id FROM fred_series WHERE series_id=%s)", ("ZZIT_TEST",))
            cur.execute("DELETE FROM fred_series WHERE series_id=%s", ("ZZIT_TEST",))
            if company_id:
                cur.execute("DELETE FROM companies WHERE id=%s", (company_id,))


def main():
    if not os.environ.get("DATABASE_URL"):
        sys.exit("Set DATABASE_URL first.")
    if not db.init_db():
        sys.exit("db.init_db() failed — check DATABASE_URL.")

    print(f"\nIntegration test against live PostgreSQL (ticker={TICKER})\n")
    run_id = company_id = None
    try:
        # ── 1. INSERT (upsert_company, first time) ───────────────────────────
        company_id = db.upsert_company({
            "ticker": TICKER, "company_name": "ZZ Integration Test Co",
            "cik": "CIK0001234567", "sic": "3674", "sic_description": "Semiconductors",
        })
        check("INSERT company via upsert_company", bool(company_id), f"company_id={company_id}")

        # ── 2. UPSERT (upsert_company, ON CONFLICT (ticker) DO UPDATE) ────────
        company_id2 = db.upsert_company({
            "ticker": TICKER, "company_name": "ZZ Integration Test Co — RENAMED",
            "cik": "CIK0001234567", "sic": "3674", "sic_description": "Semiconductors",
        })
        from db import _conn
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT company_name FROM companies WHERE id=%s", (company_id,))
                name_now = cur.fetchone()[0]
        check("UPSERT company keeps same id", company_id2 == company_id,
              f"{company_id} == {company_id2}")
        check("UPSERT updated company_name in place", name_now.endswith("RENAMED"),
              f"name now: {name_now!r}")

        # ── 3. INSERT (create_risk_loop_run — mirrors /predictive persist) ───
        run_id = db.create_risk_loop_run(company_id, {
            "ticker": TICKER, "industry": "Semiconductors", "data_mode": "mcp",
            "forecast_metric": "Revenue", "forecast_horizon": 4, "persona": "CAE",
        })
        check("INSERT risk_loop_run", bool(run_id), f"run_id={run_id}")

        # ── 4. INSERT child rows (risk scores, ratios, M-score) ──────────────
        db.save_risk_scores(run_id, [
            {"risk_ref": "R1", "name": "Revenue Concentration", "category": "Revenue",
             "base_score": 6.0, "score": 7.2, "rag_status": "Amber", "velocity": 2,
             "control_env": "ADEQUATE"},
            {"risk_ref": "R2", "name": "Cybersecurity (IP)", "category": "Cybersecurity",
             "base_score": 5.5, "score": 8.1, "rag_status": "Red", "velocity": 3,
             "control_env": "WEAK"},
        ])
        db.save_financial_ratios(run_id, {"revenue_now": 8200.0, "gross_margin": 0.46,
                                          "rd_intensity": 0.18, "fcf_margin": 0.12})
        db.save_beneish_mscore(run_id, {"m_score": -2.31, "interpretation": "Unlikely manipulator",
                                        "rag_status": "Green", "inputs": {"dsri": 1.0}})
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM risk_scores WHERE run_id=%s", (run_id,))
                n_risks = cur.fetchone()[0]
        check("INSERT risk_scores (2 rows)", n_risks == 2, f"{n_risks} rows")

        # ── 5. INSERT ai_analyses (new table from this work) ─────────────────
        ai_id = db.save_ai_analysis(
            "gate1_recommendation",
            {"recommendations": [{"risk_ref": "R2", "recommendation": "adjust",
                                  "suggested_score": 8.5, "rationale": "Weak controls + velocity 3."}]},
            run_id=run_id, ticker=TICKER, model="claude-opus-4-8", effort="high",
            summary="1 risk disposition", input_tokens=1200, output_tokens=300, cost_usd=0.0135,
        )
        check("INSERT ai_analyses row", bool(ai_id), f"ai_id={ai_id}")

        # ── 6. UPDATE (complete_risk_loop_run sets completed=TRUE) ───────────
        db.complete_risk_loop_run(run_id)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT completed, completed_at FROM risk_loop_runs WHERE id=%s", (run_id,))
                completed, completed_at = cur.fetchone()
        check("UPDATE risk_loop_run completed flag", completed is True and completed_at is not None,
              f"completed={completed}")

        # ── 7. UPSERT (fred observations ON CONFLICT DO UPDATE value) ────────
        db.save_fred_series_and_observations({"ZZIT_TEST": {
            "name": "ZZ Test Series", "category": "Test", "units": "Index",
            "observations": [{"date": "2025-12-31", "value": 100.0}],
        }})
        db.save_fred_series_and_observations({"ZZIT_TEST": {
            "name": "ZZ Test Series", "category": "Test", "units": "Index",
            "observations": [{"date": "2025-12-31", "value": 250.0}],  # same key, new value
        }})
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT o.value FROM fred_observations o
                               JOIN fred_series s ON s.id=o.series_id
                               WHERE s.series_id='ZZIT_TEST' AND o.quarter_end='2025-12-31'""")
                rows = cur.fetchall()
        check("UPSERT fred observation updated value (no dup)",
              len(rows) == 1 and float(rows[0][0]) == 250.0,
              f"{len(rows)} row(s), value={rows[0][0] if rows else None}")

        # ── 8. HITL path (mirrors /loop/hitl/risk-approvals from app.jsx) ────
        db.save_risk_approvals(run_id, {
            "R2": {"status": "adjusted", "risk_name": "Cybersecurity (IP)",
                   "adjustments": {"rag": "R", "score": 8.5, "velocity": 3, "ce": "WEAK"},
                   "rationale": "Adjusted per AI suggestion and weak control evidence.",
                   "adjustedBy": "Sarah Lin",
                   "signoffs": {"cae": {"who": "Sarah Lin"}}},
        }, persona="CAE")
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT ra.status, ra.adjusted_score FROM risk_approvals ra
                               JOIN hitl_sessions h ON h.id=ra.session_id
                               WHERE h.run_id=%s AND ra.risk_ref='R2'""", (run_id,))
                arow = cur.fetchone()
        check("INSERT HITL risk approval (Gate 1)", bool(arow) and arow[0] == "adjusted",
              f"status={arow[0] if arow else None}, score={arow[1] if arow else None}")

        # ── 9. READ-BACK via query helpers (what the UI history calls) ───────
        hist = db.get_run_history(TICKER, limit=5)
        check("READ get_run_history returns the run",
              any(h["run_id"] == run_id for h in hist), f"{len(hist)} run(s)")
        detail = db.get_run_detail(run_id)
        check("READ get_run_detail returns 2 risks + M-score",
              detail and len(detail["risk_scores"]) == 2 and detail["beneish_mscore"] is not None,
              f"{len(detail['risk_scores']) if detail else 0} risks")
        ai_rows = db.get_ai_analyses(run_id, kind="gate1_recommendation")
        check("READ get_ai_analyses round-trips JSON content",
              len(ai_rows) == 1 and ai_rows[0]["content"]["recommendations"][0]["risk_ref"] == "R2",
              f"{len(ai_rows)} analysis row(s)")

        # ── 10. Evidence Pack building blocks (new getters + adjudication join) ──
        meta = db.get_run_meta_for_evidence_pack(run_id)
        check("READ get_run_meta_for_evidence_pack", meta is not None and meta["ticker"] == TICKER,
              f"meta={meta}")
        check("READ get_audit_objectives_for_run (empty, none saved)",
              db.get_audit_objectives_for_run(run_id) == [], "expected []")
        check("READ get_loop_log_for_run (empty, none saved)",
              db.get_loop_log_for_run(run_id) == [], "expected []")

        # Insert one adjudicated_tool_calls row directly (no db.py helper exists —
        # writes normally go through mcp_governance's adjudication pipeline, out
        # of scope here) with run_id left NULL but adjudicated_at inside the
        # run's window, to exercise the best-effort time-window fallback join.
        import uuid
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.adjudicated_tool_calls
                        (session_id, target_tool, uro_id, final_verdict, risk_tier, adjudicated_at)
                    VALUES (%s, 'test_tool', 'ZZIT-URO-1', 'ALLOW', 'LOW', NOW())
                    """,
                    (str(uuid.uuid4()),),
                )
        adjudications = mcp_governance.fetch_adjudications_for_run(
            run_id, meta["run_at"], meta.get("completed_at"),
        )
        check("READ fetch_adjudications_for_run finds the time-window match",
              any(a["target_tool"] == "test_tool" and a["linked_via"] == "time_window_estimate"
                  for a in adjudications),
              f"{len(adjudications)} row(s)")

    finally:
        print("\n  cleaning up test rows…")
        try:
            cleanup(run_id, company_id)
            # verify cleanup
            from db import _conn
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM companies WHERE ticker=%s", (TICKER,))
                    left = cur.fetchone()[0]
            check("CLEANUP removed all test rows", left == 0, f"{left} company rows remain")
        except Exception as exc:
            check("CLEANUP", False, str(exc))

    total, passed = len(results), sum(results)
    print(f"\n{'='*50}\nRESULT: {passed}/{total} checks passed\n{'='*50}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
