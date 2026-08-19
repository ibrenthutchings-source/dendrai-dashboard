#!/usr/bin/env python3
"""
DevOps Monitoring: SARIF/SAST Evidence Ingestion.

db.py's observability.evidence_records table, and its full hash-chained,
HMAC-signed CRUD (insert_evidence_record / list_evidence_records /
get_evidence_record / verify_evidence_chain), have existed since this
platform's SOC 2 evidence-chain work — but nothing ever called
insert_evidence_record outside its own test file, and EVIDENCE_SIGNING_KEY
was documented in .env.example yet read nowhere in the codebase. This file
is the missing ingestion endpoint the README already described.

POST /evidence/webhook
    Receives a SARIF 2.1.0 report (any tool that emits SARIF — CodeQL,
    Semgrep, Trivy, Bandit, ESLint, Snyk, SonarQube, Checkmarx, ...) and
    writes one immutable, tamper-evident row per finding. A run with zero
    results still writes one summary PASS row, so "the scan ran and came
    back clean" is itself evidence, not silence.

    HIGH/CRITICAL findings additionally raise a system_telemetry event
    (mcp_governance._ingest_system_event) so they flow through the normal
    adjudication pipeline and surface in Continuous Monitoring / the HITL
    inbox, not just the Evidence Inspector — an Evidence Inspector row alone
    is too easy for an auditor to miss.

Auth: Authorization: Bearer <ingest_api_key> — the same per-system Monitored
Systems mechanism POST /observability/telemetry/ingest already uses (see
mcp_governance._get_system_by_api_key). Signing: EVIDENCE_SIGNING_KEY must be
set — this endpoint 503s rather than signing with an empty key (see
db._evidence_signing_key's docstring).

Router prefix: /evidence

    POST /evidence/webhook                 Ingest one SARIF report
    GET  /evidence/records                 Filtered list (repository/severity/commit_sha)
    GET  /evidence/records/{id}            Full record, including raw_sarif
    GET  /evidence/records/{id}/verify     Re-verify one record's HMAC signature
    GET  /evidence/chain/verify            Walk the tamper-evidence hash chain
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import db
import mcp_governance
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.evidence")

router = APIRouter(prefix="/evidence", tags=["DevOps Monitoring: Evidence"])

# No dedicated Evidence Inspector nav item exists yet — gated on Infrastructure
# Monitoring's screen id, the closest existing real nav path for this kind of
# compliance/pipeline-evidence data (same "no nav yet, reuse the nearest
# permission bucket" reasoning vendor_risk_endpoints.py documents for itself).
_SCREEN_ID = "infrastructuremonitoring"

_SARIF_LEVEL_SEVERITY = {"error": "HIGH", "warning": "MEDIUM", "note": "LOW", "none": "INFO"}
_CWE_RE = re.compile(r"^CWE-\d+$", re.IGNORECASE)
_CVE_RE = re.compile(r"^CVE-\d{4}-\d+$", re.IGNORECASE)


class EvidenceWebhookRequest(BaseModel):
    repository: str
    commit_sha: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    source: str = "other"          # github_actions | gitlab_ci | snyk | sonarqube | checkmarx | other
    author: Optional[str] = None
    approver: Optional[str] = None
    sarif: dict[str, Any]


# ── SARIF parsing (pure, no DB/auth — testable in isolation) ────────────────

def _rules_by_id(run: dict) -> dict:
    rules = ((run.get("tool") or {}).get("driver") or {}).get("rules") or []
    return {r.get("id"): r for r in rules if r.get("id")}


def _severity_for(rule: Optional[dict], result: dict) -> str:
    """Prefers a numeric security-severity score (CodeQL/many SARIF
    producers carry this in properties) over the coarse error/warning/note
    level, since two 'error'-level findings can be wildly different risk."""
    props = result.get("properties") or {}
    score = props.get("security-severity")
    if score is None and rule:
        score = (rule.get("properties") or {}).get("security-severity")
    if score is not None:
        try:
            score = float(score)
            if score >= 9.0:
                return "CRITICAL"
            if score >= 7.0:
                return "HIGH"
            if score >= 4.0:
                return "MEDIUM"
            return "LOW"
        except (TypeError, ValueError):
            pass
    level = result.get("level") or ((rule or {}).get("defaultConfiguration") or {}).get("level")
    return _SARIF_LEVEL_SEVERITY.get(level, "INFO")


def _cwe_cve_for(rule: Optional[dict]) -> tuple[Optional[str], Optional[str]]:
    tags = ((rule or {}).get("properties") or {}).get("tags") or []
    cwe = next((t.upper() for t in tags if _CWE_RE.match(t)), None)
    cve = next((t.upper() for t in tags if _CVE_RE.match(t)), None)
    return cwe, cve


def _location_for(result: dict) -> tuple[Optional[str], Optional[int], Optional[str]]:
    locs = result.get("locations") or []
    if not locs:
        return None, None, None
    phys = (locs[0] or {}).get("physicalLocation") or {}
    file_path = (phys.get("artifactLocation") or {}).get("uri")
    region = phys.get("region") or {}
    line_number = region.get("startLine")
    snippet = (region.get("snippet") or {}).get("text")
    return file_path, line_number, snippet


def _build_record(req: EvidenceWebhookRequest, rule_id, severity, cwe, cve,
                   file_path, line_number, snippet, scan_status: str) -> dict:
    fingerprint_src = f"{req.repository}|{file_path or ''}|{rule_id or ''}|{snippet or ''}"
    fingerprint = hashlib.sha256(fingerprint_src.encode("utf-8")).hexdigest()
    return {
        "repository": req.repository, "commit_sha": req.commit_sha, "pipeline_run_id": req.pipeline_run_id,
        "source": req.source, "rule_id": rule_id, "severity": severity, "cwe": cwe, "cve": cve,
        "file_path": file_path, "line_number": line_number, "line_snippet": snippet,
        "fingerprint": fingerprint, "author": req.author, "approver": req.approver, "scan_status": scan_status,
    }


def _insert_from_record(record: dict, raw_sarif: Optional[dict]) -> Optional[int]:
    record_json_str = json.dumps(record, sort_keys=True, default=str)
    signature = db.sign_evidence_record(record_json_str)
    return db.insert_evidence_record(
        repository=record["repository"], commit_sha=record["commit_sha"], pipeline_run_id=record["pipeline_run_id"],
        source=record["source"], rule_id=record["rule_id"], severity=record["severity"],
        cwe=record["cwe"], cve=record["cve"], file_path=record["file_path"],
        line_number=record["line_number"], line_snippet=record["line_snippet"],
        fingerprint=record["fingerprint"], author=record["author"], approver=record["approver"],
        scan_status=record["scan_status"], raw_sarif=raw_sarif, record_json=record, signature=signature,
    )


async def _escalate_finding(req: EvidenceWebhookRequest, rule_id, severity, file_path, line_number, system: dict) -> None:
    """HIGH/CRITICAL SARIF findings additionally flow through the normal
    adjudication pipeline, same reasoning ai_governance_endpoints.py raises
    an oversight-missing finding immediately rather than leaving it as a
    register-only attestation. Never raises — an escalation failure must not
    fail the evidence ingestion that already succeeded."""
    try:
        flags = mcp_governance._detect_system_flags({
            "action": "sarif_finding", "resource": f"{req.repository}:{file_path or rule_id}",
            "severity": severity, "event_type": "sarif_finding",
            "payload": {"sarif_finding": True},
        })
        await asyncio.to_thread(
            mcp_governance._ingest_system_event,
            system["server_name"], system["server_type"], "sarif_finding",
            f"sarif:{req.repository}:{req.commit_sha}:{rule_id}:{line_number}",
            req.author, "sast_scan", req.repository, severity, flags,
            {"sarif_finding": True, "rule_id": rule_id, "file_path": file_path, "line_number": line_number,
             "commit_sha": req.commit_sha, "pipeline_run_id": req.pipeline_run_id},
            None,
        )
    except Exception as exc:
        logger.warning("evidence: failed to escalate finding rule=%s repo=%s: %s", rule_id, req.repository, exc)


@router.post("/webhook")
async def evidence_webhook(req: EvidenceWebhookRequest, request: Request):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        db._evidence_signing_key()
    except db.EvidenceSigningKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <ingest_api_key>")
    api_key = auth_header[len("Bearer "):].strip()
    system = await asyncio.to_thread(mcp_governance._get_system_by_api_key, api_key)
    if not system:
        raise HTTPException(status_code=401, detail="Invalid ingest API key")

    runs = (req.sarif or {}).get("runs") or []
    if not runs:
        raise HTTPException(status_code=422, detail="sarif.runs is empty or missing")

    inserted = 0
    escalated = 0
    for run in runs:
        rules = _rules_by_id(run)
        results = run.get("results") or []

        if not results:
            record = _build_record(req, None, "INFO", None, None, None, None, None, "PASS")
            if await asyncio.to_thread(_insert_from_record, record, None) is not None:
                inserted += 1
            continue

        for result in results:
            rule = rules.get(result.get("ruleId"))
            severity = _severity_for(rule, result)
            cwe, cve = _cwe_cve_for(rule)
            file_path, line_number, snippet = _location_for(result)
            rule_id = result.get("ruleId")
            record = _build_record(req, rule_id, severity, cwe, cve, file_path, line_number, snippet, "FAIL")
            record_id = await asyncio.to_thread(_insert_from_record, record, result)
            if record_id is None:
                continue  # duplicate (fingerprint, commit_sha) — same finding re-ingested, not an error
            inserted += 1
            if severity in ("HIGH", "CRITICAL"):
                await _escalate_finding(req, rule_id, severity, file_path, line_number, system)
                escalated += 1

    return {"received": True, "records_inserted": inserted, "escalated_to_adjudication": escalated}


@router.get("/records")
def list_evidence(
    repository: Optional[str] = None, severity: Optional[str] = None,
    commit_sha: Optional[str] = None, limit: int = 100,
    current_user: dict = Depends(require_screen_permission(_SCREEN_ID)),
):
    if not db.is_available():
        return {"records": []}
    return {"records": db.list_evidence_records(
        repository=repository, severity=severity, commit_sha=commit_sha, limit=limit,
    )}


@router.get("/records/{record_id}")
def get_evidence(record_id: int, current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    rec = db.get_evidence_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Evidence record not found")
    return rec


@router.get("/records/{record_id}/verify")
def verify_evidence(record_id: int, current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    """Recomputes the HMAC over the stored record_json and compares it
    against the stored signature — proves this ONE row's content wasn't
    altered. Does not check chain continuity; see GET /evidence/chain/verify
    for whether a row was deleted or reordered."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    rec = db.get_evidence_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Evidence record not found")
    try:
        expected = db.sign_evidence_record(json.dumps(rec["record_json"], sort_keys=True, default=str))
    except db.EvidenceSigningKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    valid = hmac.compare_digest(expected, rec["signature"])
    return {"id": record_id, "valid": valid}


@router.get("/chain/verify")
def verify_chain(limit: Optional[int] = None, current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db.verify_evidence_chain(limit=limit)
