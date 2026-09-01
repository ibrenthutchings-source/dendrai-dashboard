"""
AI Governance — AI system register CRUD + behavioural audit.

Auditor-maintained register of the audited company's OWN AI system usage
(distinct from observability.mcp_telemetry / ai-inventory.jsx, which only
inventories this platform's own MCP tool calls — see
db.py's ai_system_registry DDL comment for the full "why a register, not a
connector" rationale).

Saving a system that requires human oversight but has none defined raises an
AI_HUMAN_OVERSIGHT_MISSING finding immediately (a static configuration gap,
not something that decays with time — ai_governance_sweep.py separately
handles the time-based half, assessment expiry).

**Behavioural audit** (`POST /ai-governance/behavioral-audit`) closes the gap
the register alone cannot: `human_oversight_defined` records that a review
step EXISTS, and AI-09 sits in the risk register with no instrument behind it
at all. Feeding a batch of a registered system's own logs through
`UBO.behavioral` produces evidence about whether that oversight actually
functions (AI-06) and whether the system's decisions show disparate impact
(AI-09). A registered system can be attested as governed and still fail both.

The audit itself is fully deterministic — no LLM, so a result that contradicts
a human attestation is reproducible on demand. The optional narrative pass
(`.../narrative`) is a separate, human-gated call that only rewrites already-
computed findings; it never produces the finding itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import claude_client
import db
import mcp_governance
from auth_endpoints import require_screen_permission

logger = logging.getLogger(__name__)

# ── UBO behavioural analyzers (optional — register CRUD works without them) ──
# Same two-candidate path probe as mcp_governance.py: UBO sits one level up in
# the Docker image (/app/UBO) and two levels up in local dev (repo root).
# Hardcoding either one breaks the other environment.
_here = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    os.path.normpath(os.path.join(_here, "..")),        # Docker: /app
    os.path.normpath(os.path.join(_here, "..", "..")),  # local dev: repo root
):
    if os.path.isdir(os.path.join(_candidate, "UBO")) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)
        break

_HAS_BEHAVIORAL = False
try:
    from UBO.behavioral import run_behavioral_audit
    _HAS_BEHAVIORAL = True
except ImportError as exc:
    logger.warning("UBO.behavioral not importable — behavioural audit disabled: %s", exc)

router = APIRouter(prefix="/ai-governance", tags=["AI Governance"])

# Same rationale as vendor_risk_endpoints.py's _SCREEN_ID: no dedicated nav
# item exists yet, but the screen-permission matrix is generic and already
# configurable for this screen_id via the admin roles/screen-permissions API.
_SCREEN_ID = "ai_governance"


class AiSystemRequest(BaseModel):
    system_name: str
    vendor: Optional[str] = None
    business_owner: Optional[str] = None
    risk_tier: str = "MEDIUM"
    requires_human_oversight: bool = False
    human_oversight_defined: bool = False
    last_assessment_date: Optional[str] = None   # ISO date string
    assessment_expires_at: Optional[str] = None  # ISO date string


@router.get("")
async def list_ai_systems(high_risk_only: bool = False,
                           current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"systems": db.list_ai_systems(high_risk_only=high_risk_only)}


@router.put("")
async def upsert_ai_system(req: AiSystemRequest,
                            current_user: dict = Depends(require_screen_permission(_SCREEN_ID, edit=True))):
    """Create or update an AI system's governance profile. Also the
    mechanism for clearing an EXPIRED assessment status by recording a fresh
    assessment date."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    system_id = db.upsert_ai_system(
        system_name=req.system_name, vendor=req.vendor, business_owner=req.business_owner,
        risk_tier=req.risk_tier, requires_human_oversight=req.requires_human_oversight,
        human_oversight_defined=req.human_oversight_defined,
        last_assessment_date=req.last_assessment_date, assessment_expires_at=req.assessment_expires_at,
    )
    if not system_id:
        raise HTTPException(status_code=500, detail="Failed to save AI system profile")

    # Registering the system IS the acceptance of any matching passively-
    # detected candidate — no separate accept action needed (see
    # GET/POST /ai-governance/shadow-candidates below). Best-effort:
    # db.resolve_ai_shadow_candidate_by_name never raises.
    db.resolve_ai_shadow_candidate_by_name(req.system_name, system_id, current_user.get("username"))

    if req.requires_human_oversight and not req.human_oversight_defined:
        try:
            flags = mcp_governance._detect_system_flags({
                "action": "ai_human_oversight_missing", "resource": req.system_name,
                "severity": "HIGH", "event_type": "ai_human_oversight_missing",
                "payload": {"ai_human_oversight_missing": True},
            })
            mcp_governance._ingest_system_event(
                "ai-governance-endpoints", "ai_governance", "ai_human_oversight_missing",
                f"oversight-missing:{system_id}:{req.system_name}",
                current_user.get("username"), "human_oversight_check", req.system_name,
                "HIGH", flags,
                {
                    "ai_human_oversight_missing": True,
                    "ai_governance_detail": {
                        "system_name": req.system_name, "vendor": req.vendor,
                        "risk_tier": req.risk_tier,
                    },
                },
                None,
            )
        except Exception as exc:
            logger.warning("ai_governance_endpoints: failed to raise oversight-missing finding for %s: %s", req.system_name, exc)

    return {"id": system_id}


# ─────────────────────────────────────────────────────────────────────────────
# Passive shadow-AI detection — candidates surfaced by
# mcp_governance._extract_ai_tool_name (an AI-vendor/tool keyword match in
# some connector event's payload, e.g. an IAM entitlement literally named
# "OPENAI_ENTERPRISE_ACCESS"). Never auto-promoted into the register above —
# a human either registers the system (which auto-resolves the matching
# candidate, see upsert_ai_system's PUT handler) or dismisses it here.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/shadow-candidates")
async def list_shadow_candidates(status: str = "pending",
                                  current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"candidates": db.list_ai_shadow_candidates(status=status)}


@router.post("/shadow-candidates/{candidate_id}/dismiss")
async def dismiss_shadow_candidate(candidate_id: int,
                                    current_user: dict = Depends(require_screen_permission(_SCREEN_ID, edit=True))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    ok = db.dismiss_ai_shadow_candidate(candidate_id, current_user.get("username"))
    if not ok:
        raise HTTPException(status_code=404, detail="Candidate not found or already resolved")
    return {"dismissed": True}


# ─────────────────────────────────────────────────────────────────────────────
# Behavioural audit — does the attested oversight actually work?
# ─────────────────────────────────────────────────────────────────────────────

class BehavioralAuditRequest(BaseModel):
    """A batch of one registered AI system's own operating logs.

    `events` is intentionally loosely typed: each analyzer selects the event
    shapes it understands by `event_type` and ignores the rest, so a caller
    can post a mixed export without pre-splitting it.

        {"event_type": "human_review", "decision": "approved"|"rejected",
         "seconds_to_decide": 1.4}
        {"event_type": "ai_decision", "subject_group": "...",
         "outcome": "favourable"|"adverse"}
    """

    system_name: str
    events: list[dict[str, Any]] = Field(default_factory=list)


def _severity_for(verdict: str) -> str:
    return {"ESCALATE": "HIGH", "MONITOR": "MEDIUM"}.get(verdict, "LOW")


@router.post("/behavioral-audit")
async def behavioral_audit(
    req: BehavioralAuditRequest,
    current_user: dict = Depends(require_screen_permission(_SCREEN_ID, edit=True)),
):
    """Run the deterministic behavioural analyzers over a log batch.

    Requires the system to already be on the register: this audit's whole
    purpose is to test an attestation against evidence, and there is no
    attestation to test for an unregistered system. Auditing a system nobody
    has claimed responsibility for would produce a finding with no owner.

    A non-CLEAR verdict is re-ingested as a system_telemetry event so it
    surfaces in Continuous Monitoring / the HITL inbox on the same path as
    every other finding — the same pattern ai_governance_sweep.py uses for
    assessment expiry, rather than a bespoke notification channel.
    """
    if not _HAS_BEHAVIORAL:
        raise HTTPException(status_code=503, detail="Behavioural analyzers unavailable (UBO package not loaded)")
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    if not req.events:
        raise HTTPException(status_code=400, detail="No events supplied")

    registered = {s["system_name"] for s in db.list_ai_systems()}
    if req.system_name not in registered:
        raise HTTPException(
            status_code=404,
            detail=f"'{req.system_name}' is not on the AI system register. Add it before auditing it.",
        )

    report = run_behavioral_audit(req.system_name, req.events)

    if report["requires_human_review"]:
        try:
            verdict = report["overall_verdict"]
            severity = _severity_for(verdict)
            flags = mcp_governance._detect_system_flags({
                "action": "ai_behavioral_audit", "resource": req.system_name,
                "severity": severity, "event_type": "ai_behavioral_audit",
                "payload": {"ai_behavioral_audit": True},
            })
            mcp_governance._ingest_system_event(
                "ai-governance-endpoints", "ai_governance", "ai_behavioral_audit",
                # Hash the evidence, not a timestamp: re-running an unchanged
                # batch should be recognisably the same finding rather than a
                # fresh one each time the auditor re-runs it.
                f"behavioral-audit:{req.system_name}:"
                f"{hashlib.sha256(json.dumps(report['evaluations'], sort_keys=True).encode()).hexdigest()[:16]}",
                current_user.get("username"), "behavioral_audit", req.system_name,
                severity, flags,
                {
                    "ai_behavioral_audit": True,
                    "ai_governance_detail": {
                        "system_name": req.system_name,
                        "overall_verdict": verdict,
                        "events_examined": report["events_examined"],
                        "evaluations": report["evaluations"],
                    },
                },
                None,
            )
        except Exception as exc:
            # Same non-fatal treatment as the oversight-missing finding above:
            # the auditor still gets their result even if ingestion fails.
            logger.warning(
                "ai_governance_endpoints: failed to raise behavioural-audit finding for %s: %s",
                req.system_name, exc,
            )

    return report


class BehavioralNarrativeRequest(BaseModel):
    system_name: str
    report: dict[str, Any]


_NARRATIVE_SYSTEM = """You are writing the narrative section of an AI governance audit finding.

You are given a COMPLETED, deterministic analysis. Every number in it was computed by
statistical code, not by you. Your job is to explain what those already-computed findings
mean for a non-technical audit committee reader.

Hard rules:
- Never compute, estimate, restate-as-different, or invent any number. Cite only figures
  present in the input.
- Never change a verdict or soften a conclusion the analysis reached.
- If the analysis returned INSUFFICIENT_DATA, say plainly that the control could not be
  evidenced. Do not present that as a pass. "We could not test this" and "this passed"
  are different findings, and conflating them is the failure mode this whole audit exists
  to prevent.
- Write for an audit committee: what was tested, what was found, what it means for
  control reliance, what should happen next."""

_NARRATIVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "control_reliance_impact": {"type": "string"},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "summary", "control_reliance_impact", "recommended_actions"],
}

# This narrative reaches an audit committee, which is exactly the population
# MODEL_CARD.md's persona_brief/audit_report gating exists to protect. Same
# treatment: every generation lands in the AI Narrative Review queue and is
# marked pending until a human clears it.
_REQUIRE_REVIEW_FOR_NARRATIVE = True


@router.post("/behavioral-audit/narrative")
async def behavioral_audit_narrative(
    req: BehavioralNarrativeRequest,
    current_user: dict = Depends(require_screen_permission(_SCREEN_ID, edit=True)),
):
    """Plain-language narrative over an already-computed behavioural audit.

    Advisory only, and deliberately downstream of the analysis: the LLM never
    decides whether a finding exists, only how the finding already produced by
    deterministic code is explained. That split is what lets the audit stay
    reproducible while still being readable by a non-technical reader.
    """
    evaluations = req.report.get("evaluations")
    if not evaluations:
        raise HTTPException(status_code=400, detail="Report contains no evaluations to narrate")

    user = json.dumps({
        "system_name": req.system_name,
        "overall_verdict": req.report.get("overall_verdict"),
        "events_examined": req.report.get("events_examined"),
        "evaluations": evaluations,
    }, indent=2, default=str)

    input_hash = hashlib.sha256((_NARRATIVE_SYSTEM + "\n---\n" + user).encode("utf-8")).hexdigest()[:32]
    cached = db.get_cached_ai_analysis("ai_behavioral_narrative", None, req.system_name, input_hash)
    if cached is not None:
        return {
            **cached["content"],
            "_review": {
                "id": cached["id"], "status": cached["review_status"],
                "reviewed_by_name": cached["reviewed_by_name"], "reviewed_at": cached["reviewed_at"],
            },
        }

    try:
        result = claude_client.complete_json(
            _NARRATIVE_SYSTEM, user, _NARRATIVE_SCHEMA,
            label="ai_behavioral_narrative", effort="medium", max_tokens=2000,
            caller=current_user,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Narrative generation failed: {exc}")

    analysis_id = db.save_ai_analysis(
        "ai_behavioral_narrative", result,
        subject_ref=req.system_name,
        effort="medium",
        summary=str(result.get("headline", ""))[:500],
        sampled_for_review=_REQUIRE_REVIEW_FOR_NARRATIVE,
        input_hash=input_hash,
    )
    return {
        **result,
        "_review": {"id": analysis_id, "status": "pending", "reviewed_by_name": None, "reviewed_at": None},
    }
