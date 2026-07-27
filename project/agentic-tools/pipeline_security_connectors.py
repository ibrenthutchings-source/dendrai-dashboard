#!/usr/bin/env python3
"""
GitHub Actions workflow-as-code security auditor — Pipeline-as-Code security,
DevOps Monitoring category.

Pure YAML analysis + REST fetch, no DB, no FastAPI — same shape as
scm_connectors.py (branch protection) and iaas_connectors.py (Postgres/Railway),
reused by both the scheduled poll-connector path (github_scm_tool.py, which
runs this alongside its branch-protection check for the same registered repo)
and scm_audit_endpoints.py's on-demand "run now" path.

Checks three classes of CI/CD pipeline weakness a branch-protection audit
can't see (the pipeline DEFINITION itself, not what it enforces):
  - GITHUB_TOKEN permissions: missing an explicit `permissions:` block (scope
    then depends on the repo/org default setting, which can silently change)
    or an explicit `write-all` grant.
  - Unpinned third-party actions: `uses: owner/action@v4` (a mutable tag) vs.
    `uses: owner/action@<40-hex-sha>` (immutable) — a compromised upstream tag
    changes what every consumer runs with no change on this side.
  - pull_request_target + untrusted checkout: the actions/checkout injection
    pattern behind several real supply-chain incidents — pull_request_target
    grants write-scoped secrets to a workflow run, and checking out the PR
    head lets a fork PR's commits execute inside that context.

This is static analysis of workflow YAML text only — it does not evaluate
what a third-party action actually does at runtime.
"""

from __future__ import annotations

import base64
import logging
import re

logger = logging.getLogger(__name__)

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


class ConnectorError(RuntimeError):
    """Mirrors scm_connectors.ConnectorError — one unreachable/malformed
    workflow file can't be allowed to break a run-all batch."""


def _require_requests() -> None:
    if not _HAS_REQUESTS:
        raise ConnectorError("requests library required: pip install requests")


def _require_yaml() -> None:
    if not _HAS_YAML:
        raise ConnectorError("pyyaml library required: pip install pyyaml")


_SHA_PIN_RE = re.compile(r"@[0-9a-f]{40}$")


def fetch_github_workflow_files(repo_full_name: str, token: str,
                                 base_url: str = "https://api.github.com") -> list[dict]:
    """List + fetch every YAML file under .github/workflows/. Returns []
    (not an error) when the directory doesn't exist — a repo with no
    workflows has nothing to audit here, not a failed audit."""
    _require_requests()
    url = f"{base_url.rstrip('/')}/repos/{repo_full_name}/contents/.github/workflows"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=20,
    )
    if resp.status_code == 404:
        return []
    if not resp.ok:
        raise ConnectorError(f"GitHub workflow listing fetch failed ({resp.status_code}): {resp.text[:300]}")
    entries = resp.json() or []

    files: list[dict] = []
    for entry in entries:
        name = entry.get("name", "")
        if entry.get("type") != "file" or not (name.endswith(".yml") or name.endswith(".yaml")):
            continue
        file_resp = requests.get(
            entry["url"],
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=20,
        )
        if not file_resp.ok:
            logger.warning("Workflow file fetch failed for %s: %s", name, file_resp.status_code)
            continue
        content_b64 = (file_resp.json() or {}).get("content", "")
        try:
            content = base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception:
            continue
        files.append({"path": f".github/workflows/{name}", "content": content})
    return files


def _permissions_is_write_all(permissions) -> bool:
    if isinstance(permissions, str):
        return permissions.strip().lower() == "write-all"
    if isinstance(permissions, dict):
        return any(str(v).lower() == "write" for v in permissions.values())
    return False


def _triggers_pull_request_target(on_value) -> bool:
    if isinstance(on_value, str):
        return on_value == "pull_request_target"
    if isinstance(on_value, list):
        return "pull_request_target" in on_value
    if isinstance(on_value, dict):
        return "pull_request_target" in on_value
    return False


def _checkout_uses_pr_head(steps: list) -> bool:
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses") or "")
        if not uses.startswith("actions/checkout"):
            continue
        ref = str((step.get("with") or {}).get("ref") or "")
        if "pull_request.head" in ref or "head.sha" in ref or "head.ref" in ref:
            return True
    return False


def analyze_workflow(content: str) -> dict:
    """Static analysis of one workflow YAML file's text. Returns a per-file
    finding dict; normalize_pipeline_compliance() aggregates across all of a
    repo's workflow files. A YAML parse error is reported as a (non-crashing)
    finding rather than raised, matching the fail-closed convention used
    elsewhere (e.g. scm_connectors' empty-protection-dict-on-404)."""
    _require_yaml()
    try:
        doc = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        return {"parse_error": str(exc), "has_permissions_block": False,
                "permissions_write_all": False, "has_pull_request_target": False,
                "has_risky_pull_request_target": False, "unpinned_actions": []}
    if not isinstance(doc, dict):
        doc = {}

    # PyYAML (1.1 resolver) parses the bare key `on:` as boolean True —
    # virtually every real workflow file hits this, so check both keys.
    on_value = doc.get("on", doc.get(True))
    top_permissions = doc.get("permissions")
    jobs = doc.get("jobs") or {}
    if not isinstance(jobs, dict):
        jobs = {}

    has_permissions_block = top_permissions is not None or any(
        isinstance(j, dict) and j.get("permissions") is not None for j in jobs.values()
    )
    permissions_write_all = _permissions_is_write_all(top_permissions) or any(
        isinstance(j, dict) and _permissions_is_write_all(j.get("permissions")) for j in jobs.values()
    )

    has_pull_request_target = _triggers_pull_request_target(on_value)
    has_risky_pull_request_target = has_pull_request_target and any(
        isinstance(j, dict) and _checkout_uses_pr_head(j.get("steps") or [])
        for j in jobs.values()
    )

    unpinned_actions: list[str] = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            uses = step.get("uses")
            if not uses or uses.startswith("./") or uses.startswith("docker://"):
                continue
            if not _SHA_PIN_RE.search(uses):
                unpinned_actions.append(uses)

    return {
        "has_permissions_block": has_permissions_block,
        "permissions_write_all": permissions_write_all,
        "has_pull_request_target": has_pull_request_target,
        "has_risky_pull_request_target": has_risky_pull_request_target,
        "unpinned_actions": unpinned_actions,
    }


def normalize_pipeline_compliance(analyzed: list[dict]) -> dict:
    """Aggregate per-file analyze_workflow() results into one repo-level
    compliance dict — same normalize_*_compliance idiom as scm_connectors.py/
    iaas_connectors.py; the shape the devops_monitoring Rego module and
    Silver's POL-DEVOPS-003 rule read as input.event.*."""
    total = len(analyzed)
    workflows_without_permissions = sum(1 for a in analyzed if not a.get("has_permissions_block"))
    has_write_all_permissions = any(a.get("permissions_write_all") for a in analyzed)
    has_risky_pull_request_target = any(a.get("has_risky_pull_request_target") for a in analyzed)
    all_unpinned = [u for a in analyzed for u in (a.get("unpinned_actions") or [])]
    return {
        "total_workflows": total,
        "workflows_without_permissions": workflows_without_permissions,
        "has_write_all_permissions": has_write_all_permissions,
        "has_risky_pull_request_target": has_risky_pull_request_target,
        "unpinned_action_count": len(all_unpinned),
        "unpinned_actions": sorted(set(all_unpinned))[:20],
    }


def evaluate_pipeline_severity(compliance: dict) -> str:
    """CRITICAL: pull_request_target combined with an untrusted PR-head
    checkout (fork PR code execution with write-scoped secrets). HIGH:
    write-all token permissions or any unpinned third-party action. INFO
    otherwise — including the softer 'no explicit permissions block'
    finding, which is a hardening gap rather than an active exposure."""
    if compliance.get("has_risky_pull_request_target"):
        return "CRITICAL"
    if compliance.get("has_write_all_permissions") or compliance.get("unpinned_action_count", 0) > 0:
        return "HIGH"
    return "INFO"
