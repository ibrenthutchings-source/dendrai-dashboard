#!/usr/bin/env python3
"""
GitHub Webhook Listener

Receives GitHub webhook events and feeds them through the Dendrai UBO Governance Brain.

POST /github/webhook
    Verifies X-Hub-Signature-256 HMAC, runs the payload through the full
    Bronze→Silver→Gold→Council pipeline, and writes the adjudication result
    to observability.adjudicated_tool_calls (source_system='GITHUB').

    The result appears immediately in the Controls Monitor UBO panel alongside
    MCP telemetry events.

Setup (one-time):
    1.  Set GITHUB_WEBHOOK_SECRET in your .env to any string you choose.
    2.  In the GitHub repo: Settings → Webhooks → Add webhook
            Payload URL:  https://<your-host>/github/webhook
            Content type: application/json
            Secret:       <same string as GITHUB_WEBHOOK_SECRET>
            Events:       Let me select individual events →
                          ☑ Branch or tag creation
                          ☑ Branch protection rules
                          ☑ Dependabot alerts
                          ☑ Pull request reviews
                          ☑ Pushes
                          ☑ Secret scanning alerts
    3.  If running locally, expose the port with: ngrok http 8001
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sys
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("ubo.github")

# ── UBO pipeline (optional — degrades to log-only if UBO not importable) ──────

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_HAS_UBO = False
try:
    from UBO.pipeline.bronze import BronzeIngestionLayer
    from UBO.pipeline.silver import SilverConformationLayer
    from UBO.pipeline.gold import GoldAggregationLayer
    from UBO.council.orchestrator import CouncilOrchestrator
    from UBO.models.uro import SourceSystem as UBOSourceSystem
    _HAS_UBO = True
    logger.info("UBO Governance Brain loaded for GitHub webhook processing")
except ImportError as exc:
    logger.warning("UBO not importable — GitHub webhook events logged only: %s", exc)

import db
import mcp_governance  # reuse _evaluate_pac_policy / _llm_council_opinion — same council-voice
                        # treatment as the mcp_telemetry/system_telemetry adjudication path

# ── Configuration ──────────────────────────────────────────────────────────────

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
_COUNCIL_TIERS = {"CRITICAL", "HIGH", "MEDIUM"}

# ── Lazy pipeline (one shared set, initialised on first webhook) ───────────────

_bronze:  Any = None
_silver:  Any = None
_gold:    Any = None
_council: Any = None


def _get_pipeline():
    global _bronze, _silver, _gold, _council
    if not _HAS_UBO:
        return None, None, None, None
    if _bronze is None:
        _bronze  = BronzeIngestionLayer()
        _silver  = SilverConformationLayer()
        _gold    = GoldAggregationLayer()
        _council = CouncilOrchestrator(only_for_tiers=_COUNCIL_TIERS)
    return _bronze, _silver, _gold, _council


# ── HMAC-256 verification ──────────────────────────────────────────────────────

def _verify_signature(body: bytes, sig_header: str | None) -> bool:
    if not WEBHOOK_SECRET:
        logger.error(
            "GITHUB_WEBHOOK_SECRET not set — rejecting webhook. "
            "Set GITHUB_WEBHOOK_SECRET in your environment to enable webhook delivery."
        )
        return False
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    mac = hmac.new(WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, sig_header)


# ── DB write ───────────────────────────────────────────────────────────────────

def _write_adjudication(uro: Any, repo_full_name: str, gh_event: str, source_system: str = "GITHUB") -> None:
    """
    Write adjudication result to observability.adjudicated_tool_calls.

    Same council-voice treatment as mcp_governance._write_adjudication (the
    mcp_telemetry/system_telemetry path): an optional LLM 4th opinion for
    cases flagged for human review, and a real Rego/OPA PaC policy check —
    GitHub events previously skipped both and never wrote council_votes at
    all, so this was a second, thinner adjudication path than everything
    else feeding this table.

    source_system defaults to 'GITHUB' for the real webhook path below;
    scm_audit_endpoints.py passes 'GITLAB' for its on-demand GitLab audits,
    which reuse this same writer rather than duplicating it.
    """
    if not db.is_available():
        return
    adj = uro.adjudication
    council_votes_list = [
        {
            "agent_name":    e.agent_name,
            "verdict":       e.verdict.value,
            "confidence":    float(e.confidence),
            "risk_delta":    float(e.risk_delta),
            "reasoning":     e.reasoning,
            "evidence":      dict(e.evidence),
            "evaluation_ms": e.evaluation_ms,
        }
        for e in (adj.evaluations if adj else [])
    ]
    if adj and adj.requires_human_review:
        llm_eval = mcp_governance._llm_council_opinion(uro, adj)
        if llm_eval:
            council_votes_list.append(llm_eval)

    pac_violations: list[str] = []
    pac_result = mcp_governance._evaluate_pac_policy(uro)
    if pac_result:
        pac_violations = [
            r.get("control_id") or f"PAC-{pac_result['process'].upper()}: {r.get('rule', 'unknown_rule')}"
            for r in pac_result["rules_fired"]
        ]
        council_votes_list.append({
            "agent_name": "Policy-as-Code (Rego)",
            "verdict": "ESCALATE" if pac_violations else "PROCEED",
            "confidence": 1.0 if pac_result["engine"] == "opa" else 0.6,
            "risk_delta": 0.0,
            "reasoning": (
                f"{len(pac_result['rules_fired'])} deny rule(s) fired against the "
                f"{pac_result['process']} policy module ({pac_result['engine']})."
            ),
            "evidence": {"process": pac_result["process"], "engine": pac_result["engine"],
                         "rules_fired": [r.get("rule") for r in pac_result["rules_fired"]]},
            "evaluation_ms": None,
        })

    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observability.adjudicated_tool_calls (
                        telemetry_id, session_id,
                        target_tool, server_name, risk_flags, execution_time_ms,
                        uro_id, risk_score, risk_tier,
                        final_verdict, ensemble_confidence,
                        requires_human_review, conflict_flags,
                        policy_violations, adjudicator_reasoning,
                        council_votes,
                        source_system
                    ) VALUES (
                        NULL, %s,
                        %s, %s, %s, NULL,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb,
                        %s
                    )
                    """,
                    (
                        str(uuid.uuid4()),
                        gh_event,
                        repo_full_name,
                        [],                   # no proxy-style risk_flags for GitHub events
                        uro.id,
                        float(uro.risk_score) if uro.risk_score is not None else None,
                        uro.risk_tier,
                        adj.final_verdict.value if adj else None,
                        float(adj.ensemble_confidence) if adj else None,
                        adj.requires_human_review if adj else False,
                        [f.value for f in adj.conflict_flags] if adj else [],
                        list(uro.silver_policy_violations) + pac_violations,
                        adj.conflict_reasoning[:1000] if adj and adj.conflict_reasoning else None,
                        json.dumps(council_votes_list),
                        source_system,
                    ),
                )
            conn.commit()
    except Exception as exc:
        logger.warning("_write_adjudication error (repo=%s event=%s): %s", repo_full_name, gh_event, exc)
        try:
            conn.rollback()
        except Exception:
            pass


# ── Router ─────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/github", tags=["GitHub Webhook"])


@router.post("/webhook")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
):
    """
    Receive a GitHub webhook event and run it through the Dendrai UBO Governance Brain.

    Returns the adjudication result synchronously so GitHub's webhook dashboard
    shows the risk tier and verdict for every delivery.
    """
    body = await request.body()

    if not _verify_signature(body, x_hub_signature_256):
        logger.warning("GitHub webhook rejected: invalid signature (delivery=%s)", x_github_delivery)
        raise HTTPException(status_code=403, detail="Invalid X-Hub-Signature-256")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Request body is not valid JSON")

    gh_event      = x_github_event or payload.get("event_type", "push")
    repo          = payload.get("repository", {})
    repo_full_name = repo.get("full_name", "unknown/unknown")

    logger.info(
        "GitHub webhook received: event=%s repo=%s delivery=%s",
        gh_event, repo_full_name, x_github_delivery,
    )

    # Inject the event type header into the payload dict so GitHubBronzeHandler
    # can read it from raw_event["X-GitHub-Event"] (same as live webhook headers)
    payload["X-GitHub-Event"] = gh_event

    bronze, silver, gold, council = _get_pipeline()
    if bronze is None:
        logger.info("UBO unavailable — GitHub event logged without adjudication")
        return {"received": True, "adjudicated": False, "reason": "UBO pipeline not available"}

    try:
        uro = await bronze.ingest(payload, UBOSourceSystem.GITHUB)
        uro = await silver.conform(uro)
        uro = await gold.score(uro)
        uro = await council.evaluate(uro)

        # Non-blocking DB write
        asyncio.create_task(asyncio.to_thread(_write_adjudication, uro, repo_full_name, gh_event))

        tier    = uro.risk_tier
        verdict = uro.adjudication.final_verdict.value if uro.adjudication else None
        review  = uro.adjudication.requires_human_review if uro.adjudication else False

        logger.info(
            "GitHub event adjudicated: repo=%s event=%s tier=%s verdict=%s human_review=%s",
            repo_full_name, gh_event, tier, verdict, review,
        )

        return {
            "received":            True,
            "adjudicated":         True,
            "uro_id":              uro.id,
            "event":               gh_event,
            "repo":                repo_full_name,
            "risk_tier":           tier,
            "risk_score":          float(uro.risk_score) if uro.risk_score is not None else None,
            "verdict":             verdict,
            "requires_human_review": review,
            "policy_violations":   list(uro.silver_policy_violations),
        }

    except Exception as exc:
        logger.warning("GitHub webhook pipeline error (repo=%s event=%s): %s", repo_full_name, gh_event, exc)
        return JSONResponse(
            {"received": True, "adjudicated": False, "error": str(exc)},
            status_code=500,
        )
