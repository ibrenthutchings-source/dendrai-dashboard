#!/usr/bin/env python3
"""
SCM Integrity Auditor API — DevOps Monitoring category.

Router prefix: /scm-audit

    POST   /scm-audit/repositories               Register a repo for auditing
    GET    /scm-audit/repositories                List registered repos (tokens never returned)
    DELETE /scm-audit/repositories/{id}           Remove a repo
    POST   /scm-audit/repositories/{id}/run       Run an audit now, synchronously
    POST   /scm-audit/run-all                     Audit every active repo (best-effort per-repo)
    GET    /scm-audit/results                     Latest on-demand/webhook audit per repo
    GET    /scm-audit/results/history?resource=.. Audit history for one repo
    GET    /scm-audit/drift                       Drift/time-series log (control flips, either direction)
    GET    /scm-audit/waivers                     List Risk Waivers (ACTIVE/EXPIRED/REVOKED)
    POST   /scm-audit/waivers/{id}/revoke          Manually revoke an ACTIVE waiver early

Repos are stored as observability.poll_connectors rows (connector_type
'github_scm'|'gitlab_scm') — the same encrypted-credential (db.encrypt_credentials)
and risk_tier/data_sensitivity/system_owner metadata path Oracle Fusion/SAP HANA/
etc. already use, so a registered repo shows up in the existing AI Inventory
screen for free. connector_poller.py schedules the periodic re-audit via
github_scm_tool.py/gitlab_scm_tool.py; "run now" here instead runs the exact
webhook-shaped Bronze->Silver->Gold->Council pipeline github_endpoints.py uses
for real GitHub events, so an on-demand audit is adjudicated with the same
fidelity a live webhook would get (and is written through the same
github_endpoints._write_adjudication, generalized with a source_system param
for the GITLAB case).

GET /scm-audit/results reads observability.adjudicated_tool_calls (source_system
GITHUB/GITLAB, target_tool the synthesized event name) — i.e. real-time webhook
and on-demand "run now" results. The separate scheduled poll-connector path
(github_scm_tool.py/gitlab_scm_tool.py) writes to system_telemetry instead and
already surfaces through the existing Continuous Monitoring / AI Inventory
screens; it is not duplicated into this endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import github_endpoints
import pipeline_security_connectors
import scm_connectors
import secret_scanner_connectors
from mcp_guards import validate_external_url

logger = logging.getLogger("ubo.scm_audit")

_HAS_UBO = False
try:
    from UBO.pipeline.bronze import BronzeIngestionLayer
    from UBO.pipeline.silver import SilverConformationLayer
    from UBO.pipeline.gold import GoldAggregationLayer
    from UBO.council.orchestrator import CouncilOrchestrator
    from UBO.models.uro import SourceSystem as UBOSourceSystem
    _HAS_UBO = True
    logger.info("UBO Governance Brain loaded for SCM audit processing")
except ImportError as exc:
    logger.warning("UBO not importable — SCM audits will be logged only: %s", exc)

router = APIRouter(prefix="/scm-audit", tags=["SCM Integrity Auditor"])

_COUNCIL_TIERS = {"CRITICAL", "HIGH", "MEDIUM"}
_CONNECTOR_TYPES = ("github_scm", "gitlab_scm", "bitbucket_scm")

_bronze: Any = None
_silver: Any = None
_gold: Any = None
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


_DEFAULT_BASE_URL = {
    "github": "https://api.github.com",
    "gitlab": "https://gitlab.com/api/v4",
    "bitbucket": "https://api.bitbucket.org/2.0",
}


# ── Request models ─────────────────────────────────────────────────────────────

class RegisterRepoRequest(BaseModel):
    provider: str            # "github" | "gitlab" | "bitbucket"
    display_name: str
    repo_ref: str             # "owner/repo" (GitHub/Bitbucket) or "namespace/project"/numeric id (GitLab)
    branch: str = "main"
    base_url: Optional[str] = None
    token: str
    risk_tier: Optional[str] = None
    data_sensitivity: Optional[str] = None
    system_owner: Optional[str] = None
    poll_interval_s: int = 1800


class DiscoverRequest(BaseModel):
    provider: str
    token: str
    base_url: Optional[str] = None


class BulkRegisterItem(BaseModel):
    repo_ref: str
    display_name: Optional[str] = None
    branch: str = "main"


class BulkRegisterRequest(BaseModel):
    provider: str
    token: str
    base_url: Optional[str] = None
    repos: list[BulkRegisterItem]
    risk_tier: Optional[str] = None
    data_sensitivity: Optional[str] = None
    system_owner: Optional[str] = None
    poll_interval_s: int = 1800


# ── Registry (thin wrapper over poll_connectors) ──────────────────────────────

def _register_one(provider: str, display_name: str, repo_ref: str, branch: str,
                   base_url: Optional[str], token: str, poll_interval_s: int,
                   risk_tier: Optional[str], data_sensitivity: Optional[str],
                   system_owner: Optional[str]) -> int:
    """Shared validation + poll_connectors write behind both the single
    /repositories POST and the bulk /repositories/bulk POST (the discovery
    picker's "register selected" action) — one repo, one connector row."""
    if provider not in ("github", "gitlab", "bitbucket"):
        raise HTTPException(status_code=422, detail="provider must be 'github', 'gitlab', or 'bitbucket'")
    if not repo_ref.strip():
        raise HTTPException(status_code=422, detail="repo_ref is required")
    if not token.strip():
        raise HTTPException(status_code=422, detail="token is required")

    resolved_base_url = base_url or _DEFAULT_BASE_URL[provider]
    try:
        validate_external_url(resolved_base_url, field="base_url")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    extra_key = "repo_full_name" if provider in ("github", "bitbucket") else "project_ref"
    connector_id = db.create_poll_connector(
        connector_type=f"{provider}_scm",
        display_name=display_name,
        base_url=resolved_base_url,
        auth_type="token",
        credentials={"token": token},
        extra_config={extra_key: repo_ref, "branch": branch},
        poll_interval_s=poll_interval_s,
        risk_tier=risk_tier,
        data_sensitivity=data_sensitivity,
        system_owner=system_owner,
    )
    if not connector_id:
        raise HTTPException(status_code=500, detail="Failed to register repository")
    return connector_id


@router.post("/repositories")
async def register_repository(req: RegisterRepoRequest):
    connector_id = _register_one(
        req.provider, req.display_name, req.repo_ref, req.branch, req.base_url, req.token,
        req.poll_interval_s, req.risk_tier, req.data_sensitivity, req.system_owner,
    )
    return {"id": connector_id, "provider": req.provider, "repo_ref": req.repo_ref, "branch": req.branch}


@router.post("/discover")
async def discover_repositories(req: DiscoverRequest):
    """List every repository the given token can see for provider — the
    auto-discover picker's data source. Read-only against the provider's
    API; nothing is stored until the user picks repos and calls /repositories/bulk."""
    if req.provider not in ("github", "gitlab", "bitbucket"):
        raise HTTPException(status_code=422, detail="provider must be 'github', 'gitlab', or 'bitbucket'")
    if not req.token.strip():
        raise HTTPException(status_code=422, detail="token is required")
    base_url = req.base_url or _DEFAULT_BASE_URL[req.provider]
    try:
        validate_external_url(base_url, field="base_url")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    lister = {"github": scm_connectors.list_github_repos, "gitlab": scm_connectors.list_gitlab_projects,
              "bitbucket": scm_connectors.list_bitbucket_repos}[req.provider]
    try:
        return await asyncio.to_thread(lister, req.token, base_url)
    except scm_connectors.ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/repositories/bulk")
async def bulk_register_repositories(req: BulkRegisterRequest):
    """Register every repo the caller selected from the discovery picker.
    Best-effort per-repo, same isolation shape as /run-all — one bad repo_ref
    can't block the rest of the batch from registering."""
    registered, failed = [], []
    for item in req.repos:
        try:
            connector_id = _register_one(
                req.provider, item.display_name or item.repo_ref, item.repo_ref, item.branch,
                req.base_url, req.token, req.poll_interval_s,
                req.risk_tier, req.data_sensitivity, req.system_owner,
            )
            registered.append({"id": connector_id, "repo_ref": item.repo_ref})
        except HTTPException as exc:
            failed.append({"repo_ref": item.repo_ref, "error": exc.detail})
    return {"registered": registered, "failed": failed}


def _repo_ref_of(connector: dict) -> str:
    extra = connector.get("extra_config") or {}
    return extra.get("repo_full_name") or extra.get("project_ref") or ""


@router.get("/repositories")
async def list_repositories():
    if not db.is_available():
        return {"repositories": []}
    rows = [c for c in db.list_poll_connectors() if c["connector_type"] in _CONNECTOR_TYPES]
    out = []
    for c in rows:
        out.append({
            "id":               c["id"],
            "provider":         c["connector_type"].removesuffix("_scm"),
            "display_name":     c["display_name"],
            "repo_ref":         _repo_ref_of(c),
            "branch":           (c.get("extra_config") or {}).get("branch"),
            "base_url":         c["base_url"],
            "active":           c["active"],
            "last_poll_at":     c["last_poll_at"],
            "last_poll_status": c["last_poll_status"],
            "risk_tier":        c.get("risk_tier"),
            "data_sensitivity": c.get("data_sensitivity"),
            "system_owner":     c.get("system_owner"),
        })
    return {"repositories": out}


@router.delete("/repositories/{repository_id}")
async def delete_repository(repository_id: int):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    connector = db.get_poll_connector(repository_id)
    if not connector or connector["connector_type"] not in _CONNECTOR_TYPES:
        raise HTTPException(status_code=404, detail="Repository not found")
    db.delete_poll_connector(repository_id)
    return {"deleted": True}


# ── Compliance evaluation (shared by GitHub/GitLab paths) ─────────────────────

def _compliance_status(compliance: dict) -> str:
    """COMPLIANT / WEAKNESS / NON_COMPLIANT per the spec. enforce_admins=false
    is an automatic NON_COMPLIANT (mirrors the devops_monitoring Rego's
    DEVOPS-001 CRITICAL rule); any other single failing check is a WEAKNESS.
    GitLab has no equivalent of GitHub's required-status-checks list (see
    scm_connectors.normalize_gitlab_compliance), so those two checks default
    to "satisfied" (True) rather than penalizing every GitLab repo for a
    check GitLab doesn't expose the same way."""
    if compliance.get("enforce_admins") is False:
        return "NON_COMPLIANT"
    weaknesses = [
        compliance.get("required_approving_review_count", 0) < 1,
        not compliance.get("dismiss_stale_reviews", False),
        not compliance.get("has_required_sast_check", True),
        not compliance.get("has_required_test_check", True),
        not compliance.get("codeowners_present", False),
        compliance.get("codeowners_present", False) and not compliance.get("codeowners_covers_workflows", False),
    ]
    return "WEAKNESS" if any(weaknesses) else "COMPLIANT"


async def _adjudicate(raw_event: dict, ubo_source_system, gh_event: str, resource: str,
                       branch: str, provider: str, compliance: dict) -> dict:
    # Drift & Time-Series: diff against the last-recorded state for this repo
    # BEFORE anything else, so a short-lived "2am override" (disable a control,
    # merge, restore it) leaves a resolved scm_drift_events row even if this
    # particular audit finds the repo compliant again.
    drift_events = await asyncio.to_thread(
        db.record_scm_audit_snapshot, f"{resource}@{branch}", compliance)

    result = {
        "provider":           provider,
        "resource":           resource,
        "branch":             branch,
        "compliance_status":  _compliance_status(compliance),
        "compliance":         compliance,
        "drift_events":       drift_events,
        "adjudicated":        False,
    }
    bronze, silver, gold, council = _get_pipeline()
    if bronze is None or ubo_source_system is None:
        result["reason"] = "UBO pipeline not available — compliance computed, not adjudicated"
        return result

    try:
        uro = await bronze.ingest(raw_event, ubo_source_system)
        uro = await silver.conform(uro)
        uro = await gold.score(uro)
        uro = await council.evaluate(uro)

        asyncio.create_task(asyncio.to_thread(
            github_endpoints._write_adjudication, uro, resource, gh_event, provider.upper(),
        ))

        result.update({
            "adjudicated":            True,
            "uro_id":                 uro.id,
            "risk_tier":              uro.risk_tier,
            "risk_score":             float(uro.risk_score) if uro.risk_score is not None else None,
            "verdict":                uro.adjudication.final_verdict.value if uro.adjudication else None,
            "requires_human_review":  uro.adjudication.requires_human_review if uro.adjudication else False,
            "policy_violations":      list(uro.silver_policy_violations),
        })
    except Exception as exc:
        logger.warning("SCM audit adjudication error (resource=%s): %s", resource, exc)
        result["adjudication_error"] = str(exc)
    return result


async def _run_github(connector: dict) -> dict:
    creds = connector.get("credentials") or {}
    token = creds.get("token")
    extra = connector.get("extra_config") or {}
    repo_full_name = extra.get("repo_full_name")
    branch = extra.get("branch") or "main"
    base_url = connector.get("base_url") or _DEFAULT_BASE_URL["github"]
    if not repo_full_name or not token:
        raise HTTPException(status_code=422, detail="Repository is missing repo_full_name or token")

    protection = await asyncio.to_thread(
        scm_connectors.fetch_github_branch_protection, repo_full_name, branch, token, base_url)
    codeowners = await asyncio.to_thread(
        scm_connectors.fetch_github_codeowners, repo_full_name, token, base_url)
    compliance = scm_connectors.normalize_github_compliance(protection, codeowners)

    raw_event = {
        "X-GitHub-Event": "branch_protection_rule",
        "repository": {"full_name": repo_full_name, "id": connector["id"], "visibility": "private"},
        "sender": {"login": "scm-audit-engine", "site_admin": False},
        "organization": {"login": repo_full_name.split("/")[0] if "/" in repo_full_name else ""},
        "compliance": compliance,
        "raw_protection": protection,
    }
    return await _adjudicate(
        raw_event,
        UBOSourceSystem.GITHUB if _HAS_UBO else None,
        "branch_protection_rule", repo_full_name, branch, "github", compliance,
    )


async def _adjudicate_pipeline_security(raw_event: dict, ubo_source_system, resource: str,
                                         compliance: dict, severity: str) -> dict:
    """Same shape as _adjudicate() above, minus the branch-protection-specific
    compliance_status tri-state and drift tracking (pipeline-security has
    neither — every field it reports is directly Rego-actionable)."""
    result = {
        "provider":   "github",
        "resource":   resource,
        "severity":   severity,
        "compliance": compliance,
        "adjudicated": False,
    }
    bronze, silver, gold, council = _get_pipeline()
    if bronze is None or ubo_source_system is None:
        result["reason"] = "UBO pipeline not available — compliance computed, not adjudicated"
        return result

    try:
        uro = await bronze.ingest(raw_event, ubo_source_system)
        uro = await silver.conform(uro)
        uro = await gold.score(uro)
        uro = await council.evaluate(uro)

        asyncio.create_task(asyncio.to_thread(
            github_endpoints._write_adjudication, uro, resource, "workflow_security_audit", "GITHUB",
        ))

        result.update({
            "adjudicated":            True,
            "uro_id":                 uro.id,
            "risk_tier":              uro.risk_tier,
            "risk_score":             float(uro.risk_score) if uro.risk_score is not None else None,
            "verdict":                uro.adjudication.final_verdict.value if uro.adjudication else None,
            "requires_human_review":  uro.adjudication.requires_human_review if uro.adjudication else False,
            "policy_violations":      list(uro.silver_policy_violations),
        })
    except Exception as exc:
        logger.warning("Pipeline security adjudication error (resource=%s): %s", resource, exc)
        result["adjudication_error"] = str(exc)
    return result


async def _run_github_pipeline_security(connector: dict) -> dict:
    """GitHub Actions workflow-as-code security audit for a repo already
    registered for branch-protection auditing (github_scm connector) — reuses
    the same token, since it's the identical credential. GitHub-only."""
    creds = connector.get("credentials") or {}
    token = creds.get("token")
    extra = connector.get("extra_config") or {}
    repo_full_name = extra.get("repo_full_name")
    base_url = connector.get("base_url") or _DEFAULT_BASE_URL["github"]
    if not repo_full_name or not token:
        raise HTTPException(status_code=422, detail="Repository is missing repo_full_name or token")

    workflow_files = await asyncio.to_thread(
        pipeline_security_connectors.fetch_github_workflow_files, repo_full_name, token, base_url)
    analyzed = [pipeline_security_connectors.analyze_workflow(f["content"]) for f in workflow_files]
    compliance = pipeline_security_connectors.normalize_pipeline_compliance(analyzed)
    severity = pipeline_security_connectors.evaluate_pipeline_severity(compliance)

    raw_event = {
        "X-GitHub-Event": "workflow_security_audit",
        "repository": {"full_name": repo_full_name, "id": connector["id"], "visibility": "private"},
        "sender": {"login": "scm-audit-engine", "site_admin": False},
        "organization": {"login": repo_full_name.split("/")[0] if "/" in repo_full_name else ""},
        "compliance": compliance,
    }
    return await _adjudicate_pipeline_security(
        raw_event, UBOSourceSystem.GITHUB if _HAS_UBO else None, repo_full_name, compliance, severity,
    )


async def _adjudicate_secret_scan(raw_event: dict, ubo_source_system, resource: str, findings: list) -> dict:
    """Same shape as _adjudicate_pipeline_security() above. Only called when
    findings is non-empty — a clean scan never reaches here, so there is no
    'compliant' branch to fabricate: the honest report for a clean repo is
    simply {"scanned": True, "finding_count": 0}, computed entirely in
    _run_github_secret_scan() before this function is ever invoked."""
    result = {
        "provider":      "github",
        "resource":      resource,
        "scanned":       True,
        "finding_count": len(findings),
        "findings":      findings,
        "adjudicated":   False,
    }
    bronze, silver, gold, council = _get_pipeline()
    if bronze is None or ubo_source_system is None:
        result["reason"] = "UBO pipeline not available — findings computed, not adjudicated"
        return result

    try:
        uro = await bronze.ingest(raw_event, ubo_source_system)
        uro = await silver.conform(uro)
        uro = await gold.score(uro)
        uro = await council.evaluate(uro)

        asyncio.create_task(asyncio.to_thread(
            github_endpoints._write_adjudication, uro, resource, "gitleaks_scan", "GITHUB",
        ))

        result.update({
            "adjudicated":            True,
            "uro_id":                 uro.id,
            "risk_tier":              uro.risk_tier,
            "risk_score":             float(uro.risk_score) if uro.risk_score is not None else None,
            "verdict":                uro.adjudication.final_verdict.value if uro.adjudication else None,
            "requires_human_review":  uro.adjudication.requires_human_review if uro.adjudication else False,
            "policy_violations":      list(uro.silver_policy_violations),
        })
    except Exception as exc:
        logger.warning("Secret scan adjudication error (resource=%s): %s", resource, exc)
        result["adjudication_error"] = str(exc)
    return result


async def _run_github_secret_scan(connector: dict) -> dict:
    """Real gitleaks scan of an already-registered github_scm repo's full
    git history — see secret_scanner_connectors.py. Heavier than the
    branch-protection/pipeline-security checks (a full clone, not just a few
    REST calls), so this is on-demand only, not part of the scheduled poll
    tick. GitHub-only."""
    creds = connector.get("credentials") or {}
    token = creds.get("token")
    extra = connector.get("extra_config") or {}
    repo_full_name = extra.get("repo_full_name")
    base_url = connector.get("base_url") or _DEFAULT_BASE_URL["github"]
    if not repo_full_name or not token:
        raise HTTPException(status_code=422, detail="Repository is missing repo_full_name or token")

    if not secret_scanner_connectors.is_gitleaks_available():
        return {"provider": "github", "resource": repo_full_name, "scanned": False,
                "reason": "gitleaks binary not available in this environment"}

    try:
        findings = await asyncio.to_thread(
            secret_scanner_connectors.scan_repo_for_secrets, repo_full_name, token, base_url)
    except secret_scanner_connectors.ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if not findings:
        return {"provider": "github", "resource": repo_full_name, "scanned": True,
                "finding_count": 0, "adjudicated": False}

    raw_event = {
        "X-GitHub-Event": "gitleaks_scan",
        "repository": {"full_name": repo_full_name, "id": connector["id"], "visibility": "private"},
        "sender": {"login": "secret-scanner-engine", "site_admin": False},
        "organization": {"login": repo_full_name.split("/")[0] if "/" in repo_full_name else ""},
        "alert": {"secret_type": findings[0]["rule_id"]},
        "secret_findings": findings,
    }
    return await _adjudicate_secret_scan(
        raw_event, UBOSourceSystem.GITHUB if _HAS_UBO else None, repo_full_name, findings,
    )


async def _run_gitlab(connector: dict) -> dict:
    creds = connector.get("credentials") or {}
    token = creds.get("token")
    extra = connector.get("extra_config") or {}
    project_ref = extra.get("project_ref")
    branch = extra.get("branch") or "main"
    base_url = connector.get("base_url") or _DEFAULT_BASE_URL["gitlab"]
    if not project_ref or not token:
        raise HTTPException(status_code=422, detail="Repository is missing project_ref or token")

    protected_branch = await asyncio.to_thread(
        scm_connectors.fetch_gitlab_protected_branches, project_ref, branch, token, base_url)
    approval_rules = await asyncio.to_thread(
        scm_connectors.fetch_gitlab_approval_rules, project_ref, token, base_url)
    codeowners = await asyncio.to_thread(
        scm_connectors.fetch_gitlab_codeowners, project_ref, branch, token, base_url)
    compliance = scm_connectors.normalize_gitlab_compliance(protected_branch, approval_rules, codeowners)

    raw_event = {
        "X-Gitlab-Event": "protected_branch_audit",
        "project": {"path_with_namespace": project_ref, "id": connector["id"], "visibility": "private"},
        "user": {"username": "scm-audit-engine"},
        "compliance": compliance,
        "raw_protected_branch": protected_branch,
    }
    return await _adjudicate(
        raw_event,
        UBOSourceSystem.GITLAB if _HAS_UBO else None,
        "protected_branch_audit", project_ref, branch, "gitlab", compliance,
    )


async def _run_bitbucket(connector: dict) -> dict:
    creds = connector.get("credentials") or {}
    token = creds.get("token")
    extra = connector.get("extra_config") or {}
    repo_full_name = extra.get("repo_full_name")
    branch = extra.get("branch") or "main"
    base_url = connector.get("base_url") or _DEFAULT_BASE_URL["bitbucket"]
    if not repo_full_name or not token:
        raise HTTPException(status_code=422, detail="Repository is missing repo_full_name or token")

    restrictions = await asyncio.to_thread(
        scm_connectors.fetch_bitbucket_branch_restrictions, repo_full_name, branch, token, base_url)
    codeowners = await asyncio.to_thread(
        scm_connectors.fetch_bitbucket_codeowners, repo_full_name, branch, token, base_url)
    compliance = scm_connectors.normalize_bitbucket_compliance(restrictions, codeowners)

    raw_event = {
        "event_type": "branch_restriction_audit",
        "repository": {"full_name": repo_full_name, "uuid": connector["id"], "is_private": True},
        "actor": {"username": "scm-audit-engine"},
        "compliance": compliance,
        "raw_restrictions": restrictions,
    }
    return await _adjudicate(
        raw_event,
        UBOSourceSystem.BITBUCKET if _HAS_UBO else None,
        "branch_restriction_audit", repo_full_name, branch, "bitbucket", compliance,
    )


_RUNNERS = {"github_scm": _run_github, "gitlab_scm": _run_gitlab, "bitbucket_scm": _run_bitbucket}


@router.post("/repositories/{repository_id}/run")
async def run_repository_audit(repository_id: int):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    connector = db.get_poll_connector(repository_id, include_credentials=True)
    if not connector or connector["connector_type"] not in _CONNECTOR_TYPES:
        raise HTTPException(status_code=404, detail="Repository not found")

    result = await _RUNNERS[connector["connector_type"]](connector)

    db.record_poll_result(repository_id, "error" if result.get("adjudication_error") else "ok",
                           result.get("adjudication_error"))
    return result


@router.post("/repositories/{repository_id}/run-pipeline-security")
async def run_repository_pipeline_security_audit(repository_id: int):
    """GitHub Actions workflow-as-code security audit (permissions, unpinned
    actions, risky pull_request_target) for an already-registered github_scm
    repository — see pipeline_security_connectors.py. GitHub-only; GitLab CI
    has a different workflow shape and isn't covered."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    connector = db.get_poll_connector(repository_id, include_credentials=True)
    if not connector or connector["connector_type"] != "github_scm":
        raise HTTPException(status_code=404,
                             detail="Repository not found, or not a GitHub repository (pipeline security auditing is GitHub-only)")

    result = await _run_github_pipeline_security(connector)
    db.record_poll_result(repository_id, "error" if result.get("adjudication_error") else "ok",
                           result.get("adjudication_error"))
    return result


@router.post("/repositories/{repository_id}/run-secret-scan")
async def run_repository_secret_scan(repository_id: int):
    """Real gitleaks scan of an already-registered GitHub repository's full
    git history — see secret_scanner_connectors.py. This clones the repo, so
    it can take noticeably longer than the other on-demand audits for large
    repos/histories; GitHub-only."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    connector = db.get_poll_connector(repository_id, include_credentials=True)
    if not connector or connector["connector_type"] != "github_scm":
        raise HTTPException(status_code=404,
                             detail="Repository not found, or not a GitHub repository (secret scanning is GitHub-only)")

    result = await _run_github_secret_scan(connector)
    db.record_poll_result(repository_id, "error" if result.get("adjudication_error") else "ok",
                           result.get("adjudication_error"))
    return result


@router.get("/secret-scan/results")
async def list_secret_scan_results(limit: int = 50):
    """Latest on-demand gitleaks scan per repo that actually found
    something — a clean scan is never adjudicated (see
    _adjudicate_secret_scan's docstring), so it never appears here either."""
    if not db.is_available():
        return {"results": []}
    return {"results": db.fetch_secret_scan_results(limit=limit)}


@router.get("/pipeline-security/results")
async def list_pipeline_security_results(limit: int = 50):
    """Latest on-demand/scheduled pipeline-security audit per repo — the
    Pipeline Security section's feed."""
    if not db.is_available():
        return {"results": []}
    return {"results": db.fetch_pipeline_security_results(limit=limit)}


@router.post("/run-all")
async def run_all_audits():
    """Best-effort batch — one failing repo can't sink the others, same
    isolation shape as connector_poller._poll_due_connectors()."""
    if not db.is_available():
        return {"results": []}
    connectors = [c for c in db.list_poll_connectors() if c["connector_type"] in _CONNECTOR_TYPES and c["active"]]
    results = []
    for c in connectors:
        try:
            full = db.get_poll_connector(c["id"], include_credentials=True)
            result = await _RUNNERS[c["connector_type"]](full)
            db.record_poll_result(c["id"], "error" if result.get("adjudication_error") else "ok",
                                   result.get("adjudication_error"))
        except Exception as exc:
            logger.warning("run-all: repository %s failed: %s", c["id"], exc)
            result = {"id": c["id"], "error": str(exc)}
            db.record_poll_result(c["id"], "error", str(exc))
        result["id"] = c["id"]
        results.append(result)
    return {"results": results}


# ── Results ────────────────────────────────────────────────────────────────────

@router.get("/results")
async def list_results(limit: int = 50):
    """Latest on-demand/webhook audit per repo (source_system GITHUB/GITLAB,
    target_tool the synthesized audit event name) — the Branch Integrity
    Matrix feed."""
    if not db.is_available():
        return {"results": []}
    rows = db.fetch_scm_audit_results(limit=limit)
    return {"results": rows}


@router.get("/results/history")
async def result_history(resource: str, limit: int = 50):
    if not db.is_available():
        return {"history": []}
    return {"history": db.fetch_scm_audit_results(resource=resource, limit=limit)}


@router.get("/drift")
async def list_drift(resource: Optional[str] = None, open_only: bool = False, limit: int = 100):
    """Drift & Time-Series log — every control that flipped between two
    consecutive audits of the same repo, in either direction. A row with
    resolved_at set shortly after detected_at is exactly the "2am override"
    pattern: briefly non-compliant, then restored before anyone would have
    noticed from a single point-in-time check."""
    if not db.is_available():
        return {"events": []}
    return {"events": db.list_scm_drift_events(resource=resource, open_only=open_only, limit=limit)}


@router.get("/waivers")
async def list_waivers(status: Optional[str] = None, limit: int = 100):
    """Risk Waivers (observability.risk_waivers) — documented, time-boxed
    exceptions created when a manager approves a devops_scm_exception HITL
    request (see approvals_endpoints._create_waiver_from_task)."""
    if not db.is_available():
        return {"waivers": []}
    return {"waivers": db.list_risk_waivers(status=status, limit=limit)}


@router.post("/waivers/{waiver_id}/revoke")
async def revoke_waiver(waiver_id: int):
    """Manually revoke an ACTIVE waiver before its natural expiry (e.g. the
    compensating control turned out not to hold) — the finding goes back to
    failing immediately rather than waiting for risk_waiver_sweep's hourly tick."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    ok = db.revoke_risk_waiver(waiver_id)
    if not ok:
        raise HTTPException(status_code=404, detail="No ACTIVE waiver with that id")
    return {"revoked": True}
