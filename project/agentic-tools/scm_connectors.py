#!/usr/bin/env python3
"""
GitHub & GitLab branch-protection / CODEOWNERS connectors.

Pure REST I/O + normalization — no DB, no FastAPI. Shared by
github_scm_tool.py / gitlab_scm_tool.py (poll-connector adapters, scheduled
via connector_poller.py) and scm_audit_endpoints.py's on-demand "run now"
endpoint, so both paths evaluate compliance identically.

GitHub:  GET /repos/{owner}/{repo}/branches/{branch}/protection
         GET /repos/{owner}/{repo}/contents/{path}          (CODEOWNERS)
GitLab:  GET /projects/:id/protected_branches
         GET /projects/:id/approval_rules
         GET /projects/:id/repository/files/{path}/raw      (CODEOWNERS)

normalize_*_compliance() map each provider's raw shape onto one common
vocabulary — {enforce_admins, required_approving_review_count,
dismiss_stale_reviews, required_status_checks, has_required_sast_check,
has_required_test_check, codeowners_present, codeowners_covers_workflows} —
which is exactly the field set the "devops_monitoring" PaC Rego module
(pac_endpoints.py's _REGO_DEFAULTS["devops_monitoring"]) and the Silver
conformation rules for BRANCH_PROTECTION_BYPASSED expect under
conformed_payload.risk_indicators.

GitLab has no direct equivalent of GitHub's enforce_admins/required-review-count
fields — its access model is role-based (push_access_levels/merge_access_levels)
and review counts live in a separate approval_rules API. normalize_gitlab_compliance
is a best-effort approximation, documented inline; treat GitLab NON_COMPLIANT
findings as directional, not as precise as the GitHub path.
"""

from __future__ import annotations

import base64
import logging
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

_GITHUB_CODEOWNERS_PATHS = ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"]
_GITLAB_CODEOWNERS_PATHS = ["CODEOWNERS", ".gitlab/CODEOWNERS", "docs/CODEOWNERS"]

_SAST_KEYWORDS = ("sast", "codeql", "snyk", "sonar", "checkmarx", "semgrep", "security")
_TEST_KEYWORDS = ("test", "unit", "ci", "build")


class ConnectorError(RuntimeError):
    """Any non-2xx response or transport failure. Callers store the message
    as the audit result's `error` field rather than letting it propagate —
    one unreachable repo can't be allowed to break a run-all batch."""


def _require_requests() -> None:
    if not _HAS_REQUESTS:
        raise ConnectorError("requests library required: pip install requests")


# ── GitHub ────────────────────────────────────────────────────────────────────

def fetch_github_branch_protection(repo_full_name: str, branch: str, token: str,
                                    base_url: str = "https://api.github.com") -> dict:
    """GET /repos/{owner}/{repo}/branches/{branch}/protection.

    Returns {} (not an error) when the branch has no protection configured at
    all — GitHub answers that specific case with 404, and an empty dict makes
    every downstream check fail closed (NON_COMPLIANT), which is correct."""
    _require_requests()
    url = f"{base_url.rstrip('/')}/repos/{repo_full_name}/branches/{quote(branch, safe='')}/protection"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=20,
    )
    if resp.status_code == 404:
        return {}
    if not resp.ok:
        raise ConnectorError(f"GitHub branch protection fetch failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def fetch_github_codeowners(repo_full_name: str, token: str,
                             base_url: str = "https://api.github.com") -> Optional[str]:
    """Returns CODEOWNERS file content, or None if absent at every conventional path."""
    _require_requests()
    for path in _GITHUB_CODEOWNERS_PATHS:
        url = f"{base_url.rstrip('/')}/repos/{repo_full_name}/contents/{path}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=20,
        )
        if resp.status_code == 404:
            continue
        if not resp.ok:
            raise ConnectorError(f"GitHub CODEOWNERS fetch failed ({resp.status_code}): {resp.text[:300]}")
        content = (resp.json() or {}).get("content", "")
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            return content or None
    return None


# ── GitLab ────────────────────────────────────────────────────────────────────

def fetch_gitlab_protected_branches(project_ref: str, branch: str, token: str,
                                     base_url: str = "https://gitlab.com/api/v4") -> dict:
    """GET /projects/:id/protected_branches, filtered to `branch`. project_ref
    may be a numeric project ID or a namespace/project path (URL-encoded here)."""
    _require_requests()
    url = f"{base_url.rstrip('/')}/projects/{quote(project_ref, safe='')}/protected_branches"
    resp = requests.get(url, headers={"PRIVATE-TOKEN": token}, timeout=20)
    if not resp.ok:
        raise ConnectorError(f"GitLab protected branches fetch failed ({resp.status_code}): {resp.text[:300]}")
    for item in resp.json() or []:
        if item.get("name") == branch:
            return item
    return {}


def fetch_gitlab_approval_rules(project_ref: str, token: str,
                                 base_url: str = "https://gitlab.com/api/v4") -> list:
    """GET /projects/:id/approval_rules — merge-request approval counts live
    here, not on the protected-branch resource. Requires a paid tier on
    self-managed GitLab; returns [] (not an error) on 403/404 so it degrades
    to 'no required approvals detected' rather than failing the whole audit."""
    _require_requests()
    url = f"{base_url.rstrip('/')}/projects/{quote(project_ref, safe='')}/approval_rules"
    resp = requests.get(url, headers={"PRIVATE-TOKEN": token}, timeout=20)
    if resp.status_code in (403, 404):
        return []
    if not resp.ok:
        raise ConnectorError(f"GitLab approval rules fetch failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json() or []


def fetch_gitlab_codeowners(project_ref: str, branch: str, token: str,
                             base_url: str = "https://gitlab.com/api/v4") -> Optional[str]:
    _require_requests()
    for path in _GITLAB_CODEOWNERS_PATHS:
        url = (f"{base_url.rstrip('/')}/projects/{quote(project_ref, safe='')}/repository/files/"
               f"{quote(path, safe='')}/raw")
        resp = requests.get(url, headers={"PRIVATE-TOKEN": token}, params={"ref": branch}, timeout=20)
        if resp.status_code == 404:
            continue
        if not resp.ok:
            raise ConnectorError(f"GitLab CODEOWNERS fetch failed ({resp.status_code}): {resp.text[:300]}")
        return resp.text or None
    return None


# ── Normalization (pure, no I/O) ───────────────────────────────────────────────

def _has_check_matching(status_checks: list, keywords: tuple) -> bool:
    return any(any(kw in str(c).lower() for kw in keywords) for c in status_checks)


def _codeowners_covers_workflows(codeowners_text: Optional[str]) -> bool:
    if not codeowners_text:
        return False
    for line in codeowners_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern = line.split()[0]
        if (".github/workflows" in pattern or ".gitlab-ci.yml" in pattern
                or pattern in ("*", "/*", "**", "/**")):
            return True
    return False


def normalize_github_compliance(protection: dict, codeowners_text: Optional[str]) -> dict:
    required_reviews = protection.get("required_pull_request_reviews") or {}
    required_checks = ((protection.get("required_status_checks") or {}).get("contexts")) or []
    enforce_admins = bool((protection.get("enforce_admins") or {}).get("enabled", False))
    return {
        "enforce_admins": enforce_admins,
        "required_approving_review_count": required_reviews.get("required_approving_review_count", 0),
        "dismiss_stale_reviews": bool(required_reviews.get("dismiss_stale_reviews", False)),
        "required_status_checks": required_checks,
        "has_required_sast_check": _has_check_matching(required_checks, _SAST_KEYWORDS),
        "has_required_test_check": _has_check_matching(required_checks, _TEST_KEYWORDS),
        "codeowners_present": bool(codeowners_text),
        "codeowners_covers_workflows": _codeowners_covers_workflows(codeowners_text),
    }


# Controls tracked for drift detection, and which direction is a regression.
# bool controls: "improved" means flipping to True; "worse" means flipping to False.
# int controls (review count): improved means increasing, worse means decreasing.
_BOOL_CONTROLS_GOOD_WHEN_TRUE = (
    "enforce_admins", "dismiss_stale_reviews", "has_required_sast_check",
    "has_required_test_check", "codeowners_present", "codeowners_covers_workflows",
)
_INT_CONTROLS = ("required_approving_review_count",)


def diff_compliance(baseline: dict, current: dict) -> list:
    """Compare two compliance dicts (same shape as normalize_*_compliance's
    return value) and report every control that changed, tagged 'regressed'
    or 'improved'. Pure/no I/O — db.record_scm_audit_snapshot persists the
    result; this function just decides what counts as a change and which
    direction it went.

    Returns [{control_name, expected_state, actual_state, direction}, ...].
    `expected_state`/`actual_state` are single-key dicts ({control: value})
    rather than bare values, so scm_drift_events rows stay self-describing."""
    changes: list = []
    for control in _BOOL_CONTROLS_GOOD_WHEN_TRUE:
        before, after = bool(baseline.get(control)), bool(current.get(control))
        if before == after:
            continue
        changes.append({
            "control_name": control,
            "expected_state": {control: before},
            "actual_state": {control: after},
            "direction": "improved" if after else "regressed",
        })
    for control in _INT_CONTROLS:
        before, after = int(baseline.get(control) or 0), int(current.get(control) or 0)
        if before == after:
            continue
        changes.append({
            "control_name": control,
            "expected_state": {control: before},
            "actual_state": {control: after},
            "direction": "improved" if after > before else "regressed",
        })
    return changes


def normalize_gitlab_compliance(protected_branch: dict, approval_rules: list,
                                 codeowners_text: Optional[str]) -> dict:
    """Best-effort mapping onto the GitHub-shaped vocabulary — see module
    docstring. GitLab's role-based push_access_levels/allow_force_push stand
    in for enforce_admins; approval_rules' sum of approvals_required stands
    in for required_approving_review_count. GitLab has no equivalent of
    GitHub's named required-status-checks list, so has_required_sast_check /
    has_required_test_check are always False here (never a false positive)."""
    push_levels = protected_branch.get("push_access_levels") or []
    # access_level 40 = Maintainer, 60 = Admin (GitLab's role enum) — either
    # being allowed to push directly bypasses branch-protection review, same
    # risk as GitHub's enforce_admins=false.
    allows_bypass_push = any((lvl.get("access_level") or 0) >= 40 for lvl in push_levels)
    allow_force_push = bool(protected_branch.get("allow_force_push", False))

    total_required_approvals = sum(int(r.get("approvals_required") or 0) for r in approval_rules)

    return {
        "enforce_admins": not (allows_bypass_push or allow_force_push),
        "required_approving_review_count": total_required_approvals,
        "dismiss_stale_reviews": bool(protected_branch.get("code_owner_approval_required", False)),
        "required_status_checks": [],
        "has_required_sast_check": False,
        "has_required_test_check": False,
        "codeowners_present": bool(codeowners_text),
        "codeowners_covers_workflows": _codeowners_covers_workflows(codeowners_text),
    }
