#!/usr/bin/env python3
"""
SARIF & Evidence Ingestion Engine — DevOps Monitoring category.

Router prefix: /evidence

    POST /evidence/webhook            Ingest a SARIF payload from CI/SAST tooling
    GET  /evidence/records            Filtered list (repository, severity, commit_sha)
    GET  /evidence/records/{id}/verify  Recompute the HMAC signature, report {valid}
    GET  /evidence/chain/verify        Verify the tamper-evidence hash chain across all records
    GET  /evidence/dora-metrics        Deployment frequency / change failure rate / MTTR (SOC 2 CC8.1)
    POST /evidence/attestation        Ingest pipeline provenance (OIDC/SLSA/Cosign/SBOM)
    GET  /evidence/attestations       Filtered list (commit_sha)
    GET  /evidence/attestations/{id}  Full attestation, including SLSA/Cosign/SBOM detail

Setup (one-time, per CI system/SAST tool):
    1. Register the system in the Dendrai UBO Configuration screen (the same
       "monitored systems" registry Saviynt/SAP/etc. use) — this issues a
       per-system ingest_api_key. Each SAST tool/pipeline gets its own
       revocable key instead of one shared webhook secret.
    2. POST SARIF results to /evidence/webhook with:
           Authorization: Bearer <ingest_api_key>
       Body: {
         "repository": "org/repo", "commit_sha": "...", "pipeline_run_id": "...",
         "source": "github_actions" | "gitlab_ci" | "snyk" | "sonarqube" | "checkmarx" | "other",
         "author": "...", "approver": "...", "scan_status": "PASS" | "FAIL",
         "sarif": { ... OASIS SARIF 2.1.0 payload ... }
       }

Each SARIF result becomes one immutable observability.evidence_records row,
fingerprinted (SHA256 of repository+file_path+rule_id+line_snippet, deduped
against (fingerprint, commit_sha) so re-ingesting the same scan of the same
commit is a no-op) and HMAC-signed (EVIDENCE_SIGNING_KEY) so the record can be
proven untampered later via /evidence/records/{id}/verify.

HIGH/CRITICAL findings are additionally mirrored into observability.system_telemetry
(mcp_governance._ingest_system_event, tagged with the sast_finding risk flag),
which is what actually pushes them through the real Bronze->Silver->Gold->Council
adjudication pipeline and the devops_monitoring PaC policy — see
UBO/pipeline/bronze.py's SystemTelemetryBronzeHandler and
mcp_governance._SOURCE_EVENT_TO_PAC_PROCESS. LOW/MEDIUM/INFO findings are still
recorded as evidence (the immutable log is append-only regardless of severity)
but are not pushed through adjudication — nothing to escalate.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import attestation
import db
import dora_metrics
import mcp_governance
import scm_audit_endpoints
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.evidence")
router = APIRouter(prefix="/evidence", tags=["Evidence Ingestion"])

# Screen gating applied per-route, not at the router level: /evidence/webhook
# and /evidence/attestation are both external-system Bearer-key-authenticated
# ingestion endpoints (see api_server.py's _AUTH_EXEMPT for /evidence/webhook;
# /attestation shares the same auth model per its own docstring) with no user
# session to check a screen permission against.
_SCREEN_ID = "devopsmonitoring"

SIGNING_KEY = os.environ.get("EVIDENCE_SIGNING_KEY", "")

_SEVERITY_ORDER = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")
_LEVEL_TO_SEVERITY = {"error": "HIGH", "warning": "MEDIUM", "note": "LOW", "none": "INFO"}
_CWE_RE = re.compile(r"cwe-(\d+)", re.IGNORECASE)
_CVE_RE = re.compile(r"CVE-\d{4}-\d+", re.IGNORECASE)


# ── SARIF parsing (OASIS SARIF 2.1.0) ─────────────────────────────────────────

def _cvss_to_severity(cvss: float) -> str:
    """Same thresholds as the spec's SLA table (Critical>=9.0, High 7.0-8.9)."""
    if cvss >= 9.0:
        return "CRITICAL"
    if cvss >= 7.0:
        return "HIGH"
    if cvss >= 4.0:
        return "MEDIUM"
    return "LOW"


def _extract_cwe(text: str) -> Optional[str]:
    m = _CWE_RE.search(text or "")
    # CodeQL/SARIF tags commonly zero-pad ("cwe-089"); normalize to the
    # conventional unpadded CWE id ("CWE-89") via int() round-trip.
    return f"CWE-{int(m.group(1))}" if m else None


def _extract_cve(text: str) -> Optional[str]:
    m = _CVE_RE.search(text or "")
    return m.group(0).upper() if m else None


def parse_sarif(payload: dict) -> list[dict]:
    """Extract one dict per SARIF result: {rule_id, severity, cwe, cve,
    file_path, line_number, line_snippet}. Tolerant of missing optional
    fields — SARIF producers vary widely in how much they populate."""
    findings: list[dict] = []
    for run in payload.get("runs") or []:
        rules_by_id: dict[str, dict] = {
            r.get("id"): r for r in ((run.get("tool") or {}).get("driver") or {}).get("rules") or []
            if r.get("id")
        }
        for result in run.get("results") or []:
            rule_id = result.get("ruleId") or "unknown-rule"
            rule = rules_by_id.get(rule_id, {})
            rule_props = rule.get("properties") or {}
            result_props = result.get("properties") or {}

            security_severity = result_props.get("security-severity") or rule_props.get("security-severity")
            if security_severity is not None:
                try:
                    severity = _cvss_to_severity(float(security_severity))
                except (TypeError, ValueError):
                    severity = _LEVEL_TO_SEVERITY.get(result.get("level", ""), "MEDIUM")
            else:
                severity = _LEVEL_TO_SEVERITY.get(result.get("level", ""), "MEDIUM")

            tags = " ".join(rule_props.get("tags") or [])
            message_text = (result.get("message") or {}).get("text", "")
            cwe = _extract_cwe(tags) or _extract_cwe(rule_id) or _extract_cwe(message_text)
            cve = _extract_cve(rule_id) or _extract_cve(message_text)

            location = (result.get("locations") or [{}])[0]
            phys = location.get("physicalLocation") or {}
            artifact = phys.get("artifactLocation") or {}
            region = phys.get("region") or {}
            file_path = artifact.get("uri")
            line_number = region.get("startLine")
            line_snippet = ((region.get("snippet") or {}).get("text") or message_text or "")[:2000]

            findings.append({
                "rule_id":      rule_id,
                "severity":     severity,
                "cwe":          cwe,
                "cve":          cve,
                "file_path":    file_path,
                "line_number":  line_number,
                "line_snippet": line_snippet,
            })
    return findings


# ── Fingerprinting + signing ──────────────────────────────────────────────────

def compute_fingerprint(repository: str, file_path: Optional[str], rule_id: str,
                         line_snippet: Optional[str]) -> str:
    """SHA256(repository|file_path|rule_id|line_snippet) — deterministic so the
    same finding re-ingested from a repeated scan hashes identically."""
    basis = "|".join([repository or "", file_path or "", rule_id or "", line_snippet or ""])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def sign_record(record_json: dict) -> str:
    if not SIGNING_KEY:
        logger.warning("EVIDENCE_SIGNING_KEY not set — signing with an empty key (not secure for production)")
    canonical = json.dumps(record_json, sort_keys=True, separators=(",", ":"))
    return hmac.new(SIGNING_KEY.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


# ── Webhook ────────────────────────────────────────────────────────────────────

class EvidenceWebhookBody(BaseModel):
    repository: str
    commit_sha: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    source: str = "other"
    author: Optional[str] = None
    approver: Optional[str] = None
    scan_status: str = "FAIL"
    sarif: dict


@router.post("/webhook")
async def evidence_webhook(request: Request, body: EvidenceWebhookBody):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <ingest_api_key>")
    api_key = auth_header[len("Bearer "):].strip()

    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    system = await asyncio.to_thread(mcp_governance._get_system_by_api_key, api_key)
    if not system:
        raise HTTPException(status_code=401, detail="Invalid ingest API key")

    findings = parse_sarif(body.sarif)
    ingested, skipped_duplicate, escalated = 0, 0, 0

    for f in findings:
        fingerprint = compute_fingerprint(body.repository, f["file_path"], f["rule_id"], f["line_snippet"])
        record_json = {
            "repository":      body.repository,
            "commit_sha":       body.commit_sha,
            "pipeline_run_id":  body.pipeline_run_id,
            "source":           body.source,
            "rule_id":          f["rule_id"],
            "severity":         f["severity"],
            "cwe":              f["cwe"],
            "cve":              f["cve"],
            "file_path":        f["file_path"],
            "line_number":      f["line_number"],
            "fingerprint":      fingerprint,
            "author":           body.author,
            "approver":         body.approver,
            "scan_status":      body.scan_status,
        }
        signature = sign_record(record_json)

        record_id = await asyncio.to_thread(
            db.insert_evidence_record,
            body.repository, body.commit_sha, body.pipeline_run_id, body.source,
            f["rule_id"], f["severity"], f["cwe"], f["cve"],
            f["file_path"], f["line_number"], f["line_snippet"],
            fingerprint, body.author, body.approver,
            body.scan_status, body.sarif, record_json, signature,
        )
        if record_id is None:
            skipped_duplicate += 1
            continue
        ingested += 1

        if f["severity"] in ("HIGH", "CRITICAL"):
            flags = mcp_governance._detect_system_flags({
                "action": "sast_finding", "resource": f["file_path"] or body.repository,
                "severity": f["severity"], "event_type": "sast_finding",
                "payload": {"sast_finding": True},
            })
            await asyncio.to_thread(
                mcp_governance._ingest_system_event,
                system["server_name"], system.get("server_type") or "sast",
                "sast_finding", f"{fingerprint}:{body.commit_sha or ''}",
                body.author, "sast_finding", f["file_path"] or body.repository,
                f["severity"], flags,
                {**f, "repository": body.repository, "commit_sha": body.commit_sha}, None,
            )
            escalated += 1

    return {
        "received":               True,
        "findings_count":         len(findings),
        "ingested_count":         ingested,
        "skipped_duplicate_count": skipped_duplicate,
        "escalated_count":        escalated,
    }


# ── Pipeline provenance / attestation ──────────────────────────────────────────

class AttestationBody(BaseModel):
    commit_sha: str
    repository: Optional[str] = None          # "owner/repo" — Technology Risk Pipeline's
                                               # deploy-gate-bypass check needs this to look
                                               # up the matching github_scm connector's token;
                                               # omit if the pipeline system isn't GitHub-backed.
    pipeline_run_id: Optional[str] = None
    oidc_actor: Optional[str] = None          # OIDC token's job_workflow_ref/actor claim
    oidc_claims: Optional[dict] = None        # full decoded claim set, for forensic reconstruction
    slsa_provenance: Optional[dict] = None    # in-toto/SLSA provenance statement
    env_vars: Optional[dict] = None           # raw values — hashed server-side, NEVER persisted raw
    runner_type: Optional[str] = None         # github-hosted | self-hosted | gitlab-shared | gitlab-self-managed | other
    runner_id: Optional[str] = None           # runner IP/AMI ID/instance id
    container_image_sha: Optional[str] = None
    cosign_bundle: Optional[dict] = None       # Sigstore/Cosign signature + Rekor bundle
    sbom_format: Optional[str] = None          # cyclonedx | spdx
    sbom: Optional[dict] = None


@router.post("/attestation")
async def ingest_attestation(request: Request, body: AttestationBody):
    """
    Ingest one pipeline run's provenance/attestation metadata: who/what ran it
    (OIDC claims), what was built and how (SLSA provenance), whether the
    runtime environment matched expectations (env_vars_hash — detects an
    injected SKIP_TESTS=true/DISABLE_SAST=1 without ever storing the raw
    values), what actually ran it (runner metadata), whether the artifact is
    signed (Cosign/Sigstore bundle — structurally validated, see
    attestation.verify_cosign_bundle for what "verified" does and doesn't
    mean here), and its dependency manifest (SBOM, with copyleft-license
    flagging). Same Bearer-key auth as /evidence/webhook — one call per
    pipeline run, independent of how many SARIF findings that run produced.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <ingest_api_key>")
    api_key = auth_header[len("Bearer "):].strip()

    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    system = await asyncio.to_thread(mcp_governance._get_system_by_api_key, api_key)
    if not system:
        raise HTTPException(status_code=401, detail="Invalid ingest API key")

    slsa_result = attestation.validate_slsa_provenance(body.slsa_provenance) if body.slsa_provenance else None
    env_vars_hash = attestation.hash_env_vars(body.env_vars) if body.env_vars else None
    cosign_result = attestation.verify_cosign_bundle(body.cosign_bundle) if body.cosign_bundle else None
    sbom_result = attestation.parse_sbom(body.sbom, body.sbom_format) if body.sbom else None

    attestation_id = await asyncio.to_thread(
        db.insert_pipeline_attestation,
        body.commit_sha, body.pipeline_run_id, body.oidc_actor, body.oidc_claims,
        body.slsa_provenance, (slsa_result["level"] if slsa_result else None),
        env_vars_hash, body.runner_type, body.runner_id, body.container_image_sha,
        body.cosign_bundle, (cosign_result["verified"] if cosign_result else None),
        body.sbom_format, body.sbom, (sbom_result["license_risk"] if sbom_result else False),
    )

    deploy_gate = await _check_deploy_gate_for_attestation(body.repository, body.commit_sha)

    return {
        "id": attestation_id,
        "env_vars_hash": env_vars_hash,
        "slsa": slsa_result,
        "cosign": cosign_result,
        "sbom": sbom_result,
        "deploy_gate": deploy_gate,
    }


async def _check_deploy_gate_for_attestation(repository: Optional[str], commit_sha: str) -> Optional[dict]:
    """Technology Risk Pipeline: if this attestation named a repository and
    that repo is also registered as a github_scm connector (Branch Integrity
    & Evidence / Dendrai UBO Configuration), check the deployed commit's PR
    approval and adjudicate it (scm_audit_endpoints.check_deploy_gate,
    POL-GH-005). Returns None (not an error) when there's no repository name
    or no matching connector — this check is opportunistic, not required for
    attestation ingestion to succeed."""
    if not repository or not db.is_available():
        return None
    connectors = [c for c in db.list_poll_connectors()
                  if c["connector_type"] == "github_scm"
                  and (c.get("extra_config") or {}).get("repo_full_name") == repository]
    if not connectors:
        return None
    connector = db.get_poll_connector(connectors[0]["id"], include_credentials=True)
    token = (connector.get("credentials") or {}).get("token") if connector else None
    if not token:
        return None
    try:
        return await scm_audit_endpoints.check_deploy_gate(
            repository, commit_sha, token, connector.get("base_url") or "https://api.github.com")
    except Exception as exc:
        logger.warning("Deploy-gate check failed for %s@%s: %s", repository, commit_sha, exc)
        return {"error": str(exc)}


@router.get("/attestations")
async def list_attestations(commit_sha: Optional[str] = None, limit: int = 50,
                             current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        return {"attestations": []}
    return {"attestations": db.list_pipeline_attestations(commit_sha=commit_sha, limit=limit)}


@router.get("/attestations/{attestation_id}")
async def get_attestation(attestation_id: int, current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    record = db.get_pipeline_attestation(attestation_id)
    if not record:
        raise HTTPException(status_code=404, detail="Attestation not found")
    return record


# ── Read endpoints ─────────────────────────────────────────────────────────────

@router.get("/records")
async def list_records(repository: Optional[str] = None, severity: Optional[str] = None,
                        commit_sha: Optional[str] = None, limit: int = 100,
                        current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        return {"records": []}
    return {"records": db.list_evidence_records(
        repository=repository, severity=severity, commit_sha=commit_sha, limit=limit)}


@router.get("/records/{record_id}/verify")
async def verify_record(record_id: int, current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    record = db.get_evidence_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Evidence record not found")
    recomputed = sign_record(record["record_json"])
    return {
        "id": record_id,
        "valid": hmac.compare_digest(recomputed, record["signature"]),
        "fingerprint": record["fingerprint"],
    }


@router.get("/chain/verify")
async def verify_chain(limit: Optional[int] = None, current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    """Tamper-evidence chain verification — proves trail completeness (no
    row deleted or reordered), which the per-record HMAC above cannot: that
    check only proves a row's own content wasn't altered. See
    db.verify_evidence_chain's docstring for what 'valid' does and doesn't
    mean, particularly when checked==0."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    return db.verify_evidence_chain(limit=limit)


@router.get("/dora-metrics")
async def get_dora_metrics(window_days: int = 30, current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    """Deployment frequency / change failure rate / MTTR — real operational
    evidence for SOC 2 CC8.1, computed from pipeline_attestations and
    itsm_tickets. See dora_metrics.py's module docstring for the exact
    proxies used and why Lead Time for Changes isn't included."""
    return dora_metrics.compute_dora_metrics(window_days=window_days)
