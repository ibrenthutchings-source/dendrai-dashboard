"""
pytest port of integration_test.py's live-Postgres round-trip checks.

Exercises the exact db.py functions api_server.py invokes when the React UI
runs the risk loop — INSERT, UPDATE, UPSERT — plus the read-back helpers
that power run history and the Evidence Pack (Feature 1). Requires a real
DATABASE_URL (CI provides one via a postgres service container); skipped
entirely otherwise so `pytest` still runs cleanly in a plain local checkout.

Uses a sentinel ticker (ZZIT) so it never touches real data, and deletes
every row it creates (children first, FK-safe) after the module's tests run.
integration_test.py itself is left in place as a standalone manual script
(`DATABASE_URL=... python integration_test.py`) for ad-hoc live-DB checks —
this file is the CI-integrated equivalent.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import mcp_governance

TICKER = "ZZIT"

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — DB integration tests require a live Postgres",
)


def _cleanup(run_id, company_id):
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
                for t in ("ai_analyses", "risk_scores", "financial_ratios", "beneish_mscores"):
                    cur.execute(f"DELETE FROM {t} WHERE run_id=%s", (run_id,))
                cur.execute("DELETE FROM risk_loop_runs WHERE id=%s", (run_id,))
            cur.execute("DELETE FROM fred_observations WHERE series_id IN "
                        "(SELECT id FROM fred_series WHERE series_id=%s)", ("ZZIT_TEST",))
            cur.execute("DELETE FROM fred_series WHERE series_id=%s", ("ZZIT_TEST",))
            if company_id:
                cur.execute("DELETE FROM companies WHERE id=%s", (company_id,))


@pytest.fixture(scope="module")
def loop_ctx():
    assert db.init_db(), "db.init_db() failed — check DATABASE_URL"
    from db import _conn

    company_id = db.upsert_company({
        "ticker": TICKER, "company_name": "ZZ Integration Test Co",
        "cik": "CIK0001234567", "sic": "3674", "sic_description": "Semiconductors",
    })
    run_id = db.create_risk_loop_run(company_id, {
        "ticker": TICKER, "industry": "Semiconductors", "data_mode": "mcp",
        "forecast_metric": "Revenue", "forecast_horizon": 4, "persona": "CAE",
    })
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
    ai_id = db.save_ai_analysis(
        "gate1_recommendation",
        {"recommendations": [{"risk_ref": "R2", "recommendation": "adjust",
                              "suggested_score": 8.5, "rationale": "Weak controls + velocity 3."}]},
        run_id=run_id, ticker=TICKER, model="claude-sonnet-4-6", effort="high",
        summary="1 risk disposition", input_tokens=1200, output_tokens=300, cost_usd=0.0135,
    )
    db.complete_risk_loop_run(run_id)
    db.save_fred_series_and_observations({"ZZIT_TEST": {
        "name": "ZZ Test Series", "category": "Test", "units": "Index",
        "observations": [{"date": "2025-12-31", "value": 100.0}],
    }})
    db.save_fred_series_and_observations({"ZZIT_TEST": {
        "name": "ZZ Test Series", "category": "Test", "units": "Index",
        "observations": [{"date": "2025-12-31", "value": 250.0}],  # same key, new value
    }})
    db.save_risk_approvals(run_id, {
        "R2": {"status": "adjusted", "risk_name": "Cybersecurity (IP)",
               "adjustments": {"rag": "R", "score": 8.5, "velocity": 3, "ce": "WEAK"},
               "rationale": "Adjusted per AI suggestion and weak control evidence.",
               "adjustedBy": "Sarah Lin",
               "signoffs": {"cae": {"who": "Sarah Lin"}}},
    }, persona="CAE")
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

    yield {"company_id": company_id, "run_id": run_id, "ai_id": ai_id}

    _cleanup(run_id, company_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM companies WHERE ticker=%s", (TICKER,))
            left = cur.fetchone()[0]
    assert left == 0, f"cleanup left {left} company row(s) for {TICKER}"


def test_upsert_company_insert(loop_ctx):
    assert loop_ctx["company_id"]


def test_upsert_company_updates_in_place():
    # Re-upsert with the same ticker but a changed name — must keep the same
    # id (ON CONFLICT (ticker) DO UPDATE), not insert a duplicate row.
    first_id = db.upsert_company({
        "ticker": TICKER, "company_name": "ZZ Integration Test Co",
        "cik": "CIK0001234567", "sic": "3674", "sic_description": "Semiconductors",
    })
    second_id = db.upsert_company({
        "ticker": TICKER, "company_name": "ZZ Integration Test Co — RENAMED",
        "cik": "CIK0001234567", "sic": "3674", "sic_description": "Semiconductors",
    })
    from db import _conn
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT company_name FROM companies WHERE id=%s", (first_id,))
            name_now = cur.fetchone()[0]
    assert second_id == first_id
    assert name_now.endswith("RENAMED")


def test_risk_loop_run_created(loop_ctx):
    assert loop_ctx["run_id"]


def test_risk_scores_inserted(loop_ctx):
    from db import _conn
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM risk_scores WHERE run_id=%s", (loop_ctx["run_id"],))
            n = cur.fetchone()[0]
    assert n == 2


def test_ai_analysis_inserted(loop_ctx):
    assert loop_ctx["ai_id"]


def test_risk_loop_run_completed(loop_ctx):
    from db import _conn
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT completed, completed_at FROM risk_loop_runs WHERE id=%s", (loop_ctx["run_id"],))
            completed, completed_at = cur.fetchone()
    assert completed is True
    assert completed_at is not None


def test_fred_observation_upsert_no_duplicate(loop_ctx):
    from db import _conn
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT o.value FROM fred_observations o
                           JOIN fred_series s ON s.id=o.series_id
                           WHERE s.series_id='ZZIT_TEST' AND o.quarter_end='2025-12-31'""")
            rows = cur.fetchall()
    assert len(rows) == 1
    assert float(rows[0][0]) == 250.0


def test_hitl_risk_approval_inserted(loop_ctx):
    from db import _conn
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT ra.status, ra.adjusted_score FROM risk_approvals ra
                           JOIN hitl_sessions h ON h.id=ra.session_id
                           WHERE h.run_id=%s AND ra.risk_ref='R2'""", (loop_ctx["run_id"],))
            arow = cur.fetchone()
    assert arow is not None
    assert arow[0] == "adjusted"


def test_get_run_history_returns_the_run(loop_ctx):
    hist = db.get_run_history(TICKER, limit=5)
    assert any(h["run_id"] == loop_ctx["run_id"] for h in hist)


def test_get_run_detail_returns_risks_and_mscore(loop_ctx):
    detail = db.get_run_detail(loop_ctx["run_id"])
    assert detail is not None
    assert len(detail["risk_scores"]) == 2
    assert detail["beneish_mscore"] is not None


def test_get_ai_analyses_roundtrips_json(loop_ctx):
    rows = db.get_ai_analyses(loop_ctx["run_id"], kind="gate1_recommendation")
    assert len(rows) == 1
    assert rows[0]["content"]["recommendations"][0]["risk_ref"] == "R2"


def test_get_run_meta_for_evidence_pack(loop_ctx):
    meta = db.get_run_meta_for_evidence_pack(loop_ctx["run_id"])
    assert meta is not None
    assert meta["ticker"] == TICKER


def test_get_audit_objectives_for_run_empty(loop_ctx):
    assert db.get_audit_objectives_for_run(loop_ctx["run_id"]) == []


def test_get_loop_log_for_run_empty(loop_ctx):
    assert db.get_loop_log_for_run(loop_ctx["run_id"]) == []


def test_fetch_adjudications_for_run_time_window_match(loop_ctx):
    meta = db.get_run_meta_for_evidence_pack(loop_ctx["run_id"])
    adjudications = mcp_governance.fetch_adjudications_for_run(
        loop_ctx["run_id"], meta["run_at"], meta.get("completed_at"),
    )
    assert any(
        a["target_tool"] == "test_tool" and a["linked_via"] == "time_window_estimate"
        for a in adjudications
    )


def test_posture_trend_reflects_the_completed_run(loop_ctx):
    # Feature 4 — completed run must show up with the RAG counts the fixture
    # seeded (1 Amber, 1 Red, 0 Green), guarding the rag_status case-mismatch
    # class of bug (DB stores 'Red'/'Amber'/'Green', not 'R'/'A'/'G').
    trend = db.get_posture_trend(TICKER, limit=5)
    row = next((r for r in trend if r["run_id"] == loop_ctx["run_id"]), None)
    assert row is not None
    assert row["red_count"] == 1
    assert row["amber_count"] == 1
    assert row["green_count"] == 0
