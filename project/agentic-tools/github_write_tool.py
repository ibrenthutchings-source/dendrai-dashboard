#!/usr/bin/env python3
"""
GitHub write-back tool — the first write-capable connector primitive in this
codebase. Every adapter connector_poller.py's _ADAPTERS registers is strictly
pull_events()/test_connection() (confirmed by a full audit before this was
built: sailpoint_tool.py, oracle_fusion_tool.py, and every other connector
expose only reads). This module is the exception, deliberately scoped to the
lowest-blast-radius external system available: GitHub, not a live identity
provider — opening an issue or PR is fully reversible and touches no access
grant, unlike a future SailPoint entitlement-revoke primitive would.

Used by the closed-loop remediation flow (approvals_endpoints.py's
remediation_github / remediation_github_pr gate types): once a manager
approves a proposed fix, _execute_remediation fires the actual GitHub write —
create_issue for a proposal with no file target (remediation_endpoints.py's
POST /remediation/propose/{event_id}, needs no fabricated code change, just
the finding's own facts written up as a tracked ticket), or
create_pull_request for one with real file changes ready
(POST /remediation/propose-pr/{event_id}, full Git Data API flow: blob ->
tree -> commit -> ref -> PR). Since there's no principled way to
auto-detect which file an arbitrary business-exception finding (a SoD
conflict, a round-dollar journal entry) maps to, the PR path requires the
reviewer to name the file explicitly — get_file_content then reads it,
Claude drafts the fix, and get_file_content's failure to find a real file at
that path is exactly what stops a bogus PR from ever being proposed.

Configuration mirrors github_endpoints.py's existing env-var style — that
file is the one other GitHub integration in this codebase, and it already
configures itself via env vars rather than the poll_connectors table (which
exists for the enumerated poll-based read connectors, not a write-only
target with no events to poll):
    GITHUB_WRITE_TOKEN        Personal access token with repo (push) scope
    GITHUB_REMEDIATION_REPO   default "owner/repo" target, e.g. "acme-corp/infra"
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

_API = "https://api.github.com"


def _token() -> str:
    return os.environ.get("GITHUB_WRITE_TOKEN", "")


def _default_repo() -> str:
    return os.environ.get("GITHUB_REMEDIATION_REPO", "")


def is_configured() -> bool:
    return _HAS_REQUESTS and bool(_token())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def create_issue(title: str, body: str, repo: Optional[str] = None,
                  labels: Optional[list[str]] = None, timeout: int = 20) -> dict:
    """Open a tracked GitHub issue documenting a remediation finding —
    always the safe, concrete action: no code change to invent, just the
    finding's own facts and suggested fix written up for a human to action.
    Returns {"number", "url", "id", "state"} on success or {"error": "..."}."""
    if not _HAS_REQUESTS:
        return {"error": "requests library required: pip install requests"}
    if not _token():
        return {"error": "GITHUB_WRITE_TOKEN is not configured"}
    target_repo = repo or _default_repo()
    if not target_repo:
        return {"error": "No target repo configured (set GITHUB_REMEDIATION_REPO, or pass repo=)"}
    try:
        resp = requests.post(
            f"{_API}/repos/{target_repo}/issues",
            headers=_headers(), json={"title": title, "body": body, "labels": labels or []},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"number": data["number"], "url": data["html_url"], "id": data["id"], "state": data["state"]}
    except Exception as exc:
        logger.warning("github_write_tool.create_issue failed for %s: %s", target_repo, exc)
        return {"error": str(exc)}


def create_pull_request(title: str, body: str, files: dict[str, str], repo: Optional[str] = None,
                         base_branch: str = "main", new_branch: Optional[str] = None,
                         timeout: int = 20) -> dict:
    """Commit `files` ({path: full new content}) to a fresh branch off
    base_branch and open a PR against it, via the Git Data API (create blob
    per file -> tree -> commit -> ref -> PR). The caller supplies real file
    contents — this function fabricates no diff of its own. A complete,
    independently-tested primitive; not called by today's remediation-
    proposal flow (see module docstring) but available for a future
    connector-specific fixer. Returns {"number", "url", "id", "branch"} on
    success or {"error": "..."}."""
    if not _HAS_REQUESTS:
        return {"error": "requests library required: pip install requests"}
    if not _token():
        return {"error": "GITHUB_WRITE_TOKEN is not configured"}
    target_repo = repo or _default_repo()
    if not target_repo:
        return {"error": "No target repo configured (set GITHUB_REMEDIATION_REPO, or pass repo=)"}
    if not files:
        return {"error": "files must contain at least one path:content entry"}

    branch = new_branch or f"remediation/{uuid.uuid4().hex[:10]}"
    h = _headers()
    try:
        base_ref = requests.get(f"{_API}/repos/{target_repo}/git/ref/heads/{base_branch}", headers=h, timeout=timeout)
        base_ref.raise_for_status()
        base_sha = base_ref.json()["object"]["sha"]

        base_commit = requests.get(f"{_API}/repos/{target_repo}/git/commits/{base_sha}", headers=h, timeout=timeout)
        base_commit.raise_for_status()
        base_tree_sha = base_commit.json()["tree"]["sha"]

        tree_items = []
        for path, content in files.items():
            blob = requests.post(f"{_API}/repos/{target_repo}/git/blobs", headers=h,
                                  json={"content": content, "encoding": "utf-8"}, timeout=timeout)
            blob.raise_for_status()
            tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob.json()["sha"]})

        tree = requests.post(f"{_API}/repos/{target_repo}/git/trees", headers=h,
                              json={"base_tree": base_tree_sha, "tree": tree_items}, timeout=timeout)
        tree.raise_for_status()
        new_tree_sha = tree.json()["sha"]

        commit = requests.post(f"{_API}/repos/{target_repo}/git/commits", headers=h,
                                json={"message": title, "tree": new_tree_sha, "parents": [base_sha]}, timeout=timeout)
        commit.raise_for_status()
        new_commit_sha = commit.json()["sha"]

        ref = requests.post(f"{_API}/repos/{target_repo}/git/refs", headers=h,
                             json={"ref": f"refs/heads/{branch}", "sha": new_commit_sha}, timeout=timeout)
        ref.raise_for_status()

        pr = requests.post(f"{_API}/repos/{target_repo}/pulls", headers=h,
                            json={"title": title, "body": body, "head": branch, "base": base_branch}, timeout=timeout)
        pr.raise_for_status()
        data = pr.json()
        return {"number": data["number"], "url": data["html_url"], "id": data["id"], "branch": branch}
    except Exception as exc:
        logger.warning("github_write_tool.create_pull_request failed for %s: %s", target_repo, exc)
        return {"error": str(exc)}


def get_file_content(path: str, repo: Optional[str] = None, ref: str = "main", timeout: int = 15) -> dict:
    """Fetch one file's current content via the Contents API — the starting
    point for a PR-based remediation (create_pull_request commits full new
    file contents, so a caller needs the current content to base an edit on;
    unlike create_issue, there is no principled way to propose a file fix
    without first reading the file). Returns {"content": str, "sha": str} on
    success, decoded from the API's base64 encoding, or {"error": "..."} —
    including when the decoded bytes aren't valid UTF-8 text, since a binary
    file has no line-level fix to propose."""
    if not _HAS_REQUESTS:
        return {"error": "requests library required: pip install requests"}
    if not _token():
        return {"error": "GITHUB_WRITE_TOKEN is not configured"}
    target_repo = repo or _default_repo()
    if not target_repo:
        return {"error": "No target repo configured (set GITHUB_REMEDIATION_REPO, or pass repo=)"}
    try:
        resp = requests.get(
            f"{_API}/repos/{target_repo}/contents/{path}",
            headers=_headers(), params={"ref": ref}, timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("encoding") != "base64" or "content" not in data:
            return {"error": f"Unexpected response shape for {path} (not a single file?)"}
        import base64
        raw = base64.b64decode(data["content"])
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {"error": f"{path} is not valid UTF-8 text — cannot propose a text-based fix"}
        return {"content": text, "sha": data["sha"]}
    except Exception as exc:
        logger.warning("github_write_tool.get_file_content failed for %s/%s: %s", target_repo, path, exc)
        return {"error": str(exc)}


def test_connection(repo: Optional[str] = None, timeout: int = 15) -> tuple[bool, str]:
    """Verify the configured token can read AND push to the target repo —
    a read-only token would let every remediation silently fail at
    create_issue/create_pull_request time instead of at setup time."""
    if not is_configured():
        return False, "GITHUB_WRITE_TOKEN is not configured"
    target_repo = repo or _default_repo()
    if not target_repo:
        return False, "No target repo configured (set GITHUB_REMEDIATION_REPO)"
    if not _HAS_REQUESTS:
        return False, "requests library required: pip install requests"
    try:
        resp = requests.get(f"{_API}/repos/{target_repo}", headers=_headers(), timeout=timeout)
        resp.raise_for_status()
        perms = resp.json().get("permissions", {})
        if not perms.get("push"):
            return False, f"Token can read {target_repo} but lacks push access"
        return True, f"Connected — push access confirmed on {target_repo}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
