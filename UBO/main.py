"""
Governance Brain — end-to-end demonstration.

Runs three synthetic events through the full Medallion pipeline and Council of Agents:
  1. A SailPoint privilege escalation (high blast radius, missing approval)
  2. A GitHub secret detection (zero-tolerance, immediate escalation)
  3. A SAP SoD violation (financial controls breach)

Each event is correlated under the same correlation_id to demonstrate
multi-system cascade detection by The Graph Architect.

Usage:
    cd UBO
    python main.py

    # Or from the repo root:
    python -m UBO.main
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

# Configure logging before importing modules so all loggers inherit the config
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ubo.demo")


async def main() -> None:
    from .models.uro import SourceSystem
    from .pipeline.bronze import BronzeIngestionLayer
    from .pipeline.silver import SilverConformationLayer
    from .pipeline.gold import GoldAggregationLayer
    from .council.orchestrator import CouncilOrchestrator

    # ── Build the pipeline layers ──────────────────────────────────────────────
    bronze  = BronzeIngestionLayer()
    silver  = SilverConformationLayer()
    gold    = GoldAggregationLayer()
    council = CouncilOrchestrator(only_for_tiers={"CRITICAL", "HIGH", "MEDIUM"})

    # ── Synthetic events — all tagged with the same correlation_id ─────────────
    # In production: correlation_id comes from SIEM alert / incident ID
    INCIDENT_CORRELATION_ID = "INC-2025-07-001"
    NOW = datetime.now(tz=timezone.utc).isoformat()

    raw_events = [
        # ── Event 1: SailPoint privilege escalation ────────────────────────────
        # Actor 'jdoe' was granted the 'SAP-FI-ADMIN' role without an approval ID.
        # They also hold 30 existing roles (role explosion → SPoF identity).
        (
            SourceSystem.SAILPOINT,
            {
                "timestamp":     NOW,
                "action":        "ROLE_ADDED",
                "org":           "dendrai",
                "pod":           "pod-useast1",
                "requestedFor":  {"id": "jdoe@dendrai.com"},
                "requestedBy":   {"id": "admin-bot"},
                "role":          "SAP-FI-ADMIN",
                "role_count":    30,
                "entitlements":  ["finance-erp:write", "payroll-db:admin", "treasury-system:read"],
                # No requestId → policy violation POL-SP-001
            },
        ),

        # ── Event 2: GitHub secret detected ───────────────────────────────────
        # A PAT (Personal Access Token) was committed to the main branch of
        # the secrets-manager repo. Force-pushed by a service account.
        (
            SourceSystem.GITHUB,
            {
                "X-GitHub-Event": "secret_scanning_alert",
                "created_at":     NOW,
                "sender":         {"login": "ci-bot[bot]", "site_admin": False},
                "repository":     {
                    "id":          987654,
                    "full_name":   "dendrai/secrets-manager",
                    "visibility":  "private",
                },
                "alert": {
                    "secret_type": "github_personal_access_token",
                    "secret":      "ghp_REDACTED",
                },
                "organization":   {"login": "dendrai"},
                "forced":         True,
                "ref":            "refs/heads/main",
                # No cvss_score on a secret_scanning alert — different schema path
            },
        ),

        # ── Event 3: SAP SoD violation ─────────────────────────────────────────
        # The same actor (jdoe) posted a €1.2M journal entry on a Sunday without
        # a weekend authorisation code, and without an approver ID on the record.
        (
            SourceSystem.SAP,
            {
                "timestamp":    NOW,
                "TCODE":        "SOD_VIOLATION",
                "UNAME":        "jdoe@dendrai.com",
                "OBJECT_ID":    "GL-ACCT-1001",
                "OBJECT_TYPE":  "G_L_ACCOUNT",
                "AMOUNT":       1_200_000.0,
                "WAERS":        "EUR",
                "BUKRS":        "DE01",
                "KOSTL":        "CC-FINANCE",
                "BKTXT":        "misc adjustment",   # obfuscation trigger
                "actor_groups": ["gl-posting-clerks"],  # not 'financial-controllers'
                # No APPROVER_ID → policy violation POL-SAP-002
                "conflicting_roles": ["FI-AP-POSTING", "FI-AP-APPROVAL"],
                "sap_landscape": "PRD",
                "env_provider":  "On-Prem",
            },
        ),
    ]

    adjudicated_uros = []

    print("\n" + "=" * 70)
    print("  GOVERNANCE BRAIN — Medallion Pipeline + Council of Agents Demo")
    print("=" * 70 + "\n")

    for i, (source, raw) in enumerate(raw_events, 1):
        print(f"── EVENT {i}: {source.value} ──────────────────────────────────────────")

        # ── Bronze: raw ingestion ──────────────────────────────────────────────
        bronze_uro = await bronze.ingest(raw, source, correlation_id=INCIDENT_CORRELATION_ID)
        print(f"  [BRONZE] URO {bronze_uro.id[:8]}… | event_type={bronze_uro.event_type.value}")

        # ── Silver: conform + policy validation ───────────────────────────────
        silver_uro = await silver.conform(bronze_uro)
        violations = silver_uro.silver_policy_violations
        print(f"  [SILVER] Conformed | {len(violations)} policy violation(s):")
        for v in violations:
            print(f"           ↳ {v}")

        # ── Gold: risk scoring ────────────────────────────────────────────────
        gold_uro = await gold.score(silver_uro)
        print(
            f"  [GOLD]   score={gold_uro.risk_score:.3f} | tier={gold_uro.risk_tier}"
        )

        # ── Council of Agents: parallel evaluation + adjudication ─────────────
        final_uro = await council.evaluate(gold_uro)
        adj = final_uro.adjudication

        print(f"  [COUNCIL] ─────────────────────────────────────────────────────")
        if adj and adj.evaluations:
            for ev in adj.evaluations:
                print(
                    f"    {ev.agent_name:<22} verdict={ev.verdict.value:<20} "
                    f"confidence={ev.confidence:.2f}  delta={ev.risk_delta:+.3f}"
                )
        else:
            print("    (fast-path cleared — below Council tier threshold)")

        if adj:
            print(f"  [ADJUDICATOR] ──────────────────────────────────────────────────")
            print(f"    Final verdict:      {adj.final_verdict.value}")
            print(f"    Adjusted score:     {adj.adjusted_risk_score:.3f}")
            print(f"    Adjusted tier:      {adj.adjusted_risk_tier.value}")
            print(f"    Ensemble conf:      {adj.ensemble_confidence:.2f}")
            print(f"    Human review:       {adj.requires_human_review}")
            if adj.conflict_flags:
                print(f"    Conflict flags:     {[f.value for f in adj.conflict_flags]}")
            if adj.conflict_reasoning:
                print(f"    Conflict reasoning: {adj.conflict_reasoning[:120]}…")

        adjudicated_uros.append(final_uro)
        print()

    # ── Gold aggregation: build the executive dashboard report ─────────────────
    print("── GOLD AGGREGATION REPORT ────────────────────────────────────────────")
    from datetime import timedelta
    window_end   = datetime.now(tz=timezone.utc)
    window_start = window_end - timedelta(hours=24)
    report = await gold.aggregate(adjudicated_uros, window_start, window_end)

    print(f"  Report ID:                {report.report_id[:8]}…")
    print(f"  Window:                   {report.window_start.strftime('%H:%M')} → {report.window_end.strftime('%H:%M UTC')}")
    print(f"  Total events:             {report.total_events}")
    print(f"  Tier breakdown:           CRITICAL={report.critical_count} HIGH={report.high_count} MEDIUM={report.medium_count} LOW={report.low_count}")
    print(f"  Enterprise risk score:    {report.enterprise_risk_score:.4f}")
    print(f"  Cascade probability:      {report.cascading_failure_probability:.4f}")
    print(f"  Risk by source:           {json.dumps(report.risk_by_source, indent=None)}")
    if report.cascade_map:
        print(f"  Cascade root:             {report.cascade_map.system} / {report.cascade_map.resource_id}")
        print(f"  Cascade blast radius:     {report.cascade_map.blast_radius} entities")
        print(f"  Cascade depth-1 nodes:    {len(report.cascade_map.children)}")
    if report.human_review_queue:
        print(f"  Human review queue:       {len(report.human_review_queue)} URO(s)")

    print("\n" + "=" * 70)
    print("  Demo complete. All UROs are at stage=ADJUDICATED.")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
