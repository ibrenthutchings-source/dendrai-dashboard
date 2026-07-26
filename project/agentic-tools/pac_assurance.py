#!/usr/bin/env python3
"""
PaC negative-testing orchestration — ties pac_contracts.py (layer 1: can this
rule fire) and pac_negative_tests.py (layer 2: does it actually) together
into one call, and persists the result as assurance metadata/audit evidence.

This is the one function both the module-approval gate (pac_endpoints.py's
POST /modules/{process}/approve, wired in the same change that adds this
file) and the periodic full-evaluation sweep (P1a, a scheduled loop mirroring
risk_waiver_sweep.py/itsm_sla_sweep.py) call — so "test at edit time" and
"test on a schedule" can never quietly diverge in what they check.
"""

from __future__ import annotations

from typing import Optional

import db
import pac_contracts
import pac_negative_tests


def evaluate_and_record(process: str, rego_content: str, *, module_id: Optional[int] = None,
                         triggered_by: str = "manual", triggered_by_user: Optional[str] = None) -> dict:
    """
    Run both negative-testing layers against `rego_content` for `process`,
    persist the result to observability.pac_test_runs (audit evidence), and
    update controls_catalog.last_verified_at/last_test_passed for every
    control_id a must-fire fixture exercised — which is what makes a control
    stop counting as "unverified" (db.list_unverified_controls).

    triggered_by: 'manual' | 'approval_gate' | 'scheduled_sweep' — recorded,
    not just used for logging, since an auditor asking "was this actually
    tested before it was approved, or only in the nightly sweep afterward"
    needs the distinction.
    """
    contract_result = pac_contracts.check_module_contract(process, rego_content)
    corpus_result = pac_negative_tests.run_corpus(process, rego_content)

    if db.is_available():
        db.insert_pac_test_run(
            process, module_id, triggered_by, triggered_by_user,
            contract_result["ok"], contract_result["findings"],
            corpus_result["total"], corpus_result["passed"], corpus_result["failed"],
            corpus_result["results"],
        )
        for r in corpus_result["results"]:
            if r["expect"] == "fire" and r["expected_control_id"]:
                db.update_control_verification(r["expected_control_id"], r["passed"])
        # Cheap (one small indexed query per pac_rego control) — refresh
        # last_fired_at from real adjudication history at the same cadence
        # we're already touching the DB for this process's controls.
        db.refresh_control_fire_stats(
            [c["control_id"] for c in db.list_controls(process=process, source="pac_rego")]
        )

    return {
        "process": process,
        "contract": contract_result,
        "corpus": corpus_result,
        # A module can't be considered assured if it's dead-by-construction
        # (contract fails) even when the corpus that would prove it happens
        # to have no registered fixtures for this process yet.
        "ok": contract_result["ok"] and corpus_result["ok"] is not False,
    }


def assurance_summary(process: Optional[str] = None, stale_days: int = 30) -> dict:
    """Rolled-up "what's actually proven to work" view for the DevOps
    Monitoring / Policy-as-Code UI: every policy-enforced control, whether
    it's fired in real production recently, whether a negative-control test
    currently proves it, and the unverified subset (neither)."""
    if not db.is_available():
        return {"controls": [], "unverified": [], "total": 0, "unverified_count": 0}
    controls = db.list_controls(process=process, source="pac_rego")
    unverified = db.list_unverified_controls(process=process, stale_days=stale_days)
    return {
        "controls": controls,
        "unverified": unverified,
        "total": len(controls),
        "unverified_count": len(unverified),
    }
