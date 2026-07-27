#!/usr/bin/env python3
"""
Real gitleaks-based secret scanning — the actual producer for the
SECRET_DETECTED EventType outside a live GitHub Advanced Security
secret_scanning_alert webhook (a paid GitHub feature many orgs, especially
on private repos, don't have enabled). Native Advanced Security alerts
already flow through github_endpoints.py's webhook path (bronze.py's
GitHubBronzeHandler._ACTION_MAP); this module is an independent,
plan-tier-agnostic producer for the same EventType, reusing whatever
repo+token is already registered for branch-protection auditing
(scm_audit_endpoints.py / github_scm_tool.py).

Shells out to a real `gitleaks` binary (verified empirically against
gitleaks v8.28.0's actual JSON report shape — RuleID/Secret/File/StartLine/
Commit/Author/Email/Date/Fingerprint — by running it against a throwaway
git repo during development) against a full clone of the registered repo,
scanning git *history* (gitleaks' default mode, not --no-git) since a secret
committed and later removed is still exposed to anyone who has ever cloned
the repo — a working-tree-only scan would miss exactly that case.

Security note: gitleaks' `Secret`/`Match` report fields contain the actual
plaintext secret value, and the clone URL embeds the repo token as HTTPS
basic-auth userinfo. Neither the secret value nor the token is ever
persisted, logged, or returned to a caller — every finding is redacted to a
fixed placeholder in parse_gitleaks_report() before it leaves this module,
the clone lives only in a TemporaryDirectory that's removed on every exit
path (including exceptions), and clone/scan failures are re-raised as a
plain ConnectorError with no subprocess command/args attached (which would
otherwise leak the token via exc.cmd).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


class ConnectorError(RuntimeError):
    """Mirrors scm_connectors.ConnectorError."""


def _find_gitleaks_binary() -> Optional[str]:
    env_path = os.environ.get("GITLEAKS_BINARY", "").strip()
    if env_path and os.path.isfile(env_path):
        return env_path
    return shutil.which("gitleaks")


def is_gitleaks_available() -> bool:
    return _find_gitleaks_binary() is not None


def _authenticated_clone_url(repo_full_name: str, token: str, base_url: str) -> str:
    """HTTPS basic-auth userinfo clone URL — used for the initial `git
    clone` only. Never written to a persisted git remote config: the clone
    lives in a TemporaryDirectory that's deleted immediately after scanning
    and is never reused, so the embedded token never touches disk beyond
    that single clone invocation's lifetime."""
    host = base_url.replace("https://", "").replace("http://", "").rstrip("/")
    if host in ("api.github.com", ""):
        host = "github.com"
    return f"https://x-access-token:{quote(token, safe='')}@{host}/{repo_full_name}.git"


def scan_repo_for_secrets(repo_full_name: str, token: str, base_url: str = "https://api.github.com",
                           timeout: int = 180) -> list[dict]:
    """Clone the repo's full history and run a real gitleaks scan. Returns
    [] (not an error) when gitleaks isn't installed in this environment —
    callers treat that as 'nothing to report', same as
    pac_endpoints._find_opa_binary's fallback convention: never fabricate a
    finding, but never fabricate a clean bill of health either — callers
    that care should check is_gitleaks_available() separately."""
    gitleaks_bin = _find_gitleaks_binary()
    if not gitleaks_bin:
        logger.info("gitleaks binary not found — secret scanning skipped for %s", repo_full_name)
        return []

    clone_url = _authenticated_clone_url(repo_full_name, token, base_url)
    with tempfile.TemporaryDirectory(prefix="gitleaks-scan-") as tmpdir:
        repo_dir = os.path.join(tmpdir, "repo")
        try:
            subprocess.run(
                ["git", "clone", "--quiet", clone_url, repo_dir],
                check=True, capture_output=True, timeout=timeout,
            )
        except subprocess.CalledProcessError as exc:
            # Deliberately not re-raising exc (or including exc.cmd/exc.args) —
            # both contain clone_url, which embeds the access token.
            raise ConnectorError(f"git clone failed for {repo_full_name} (exit {exc.returncode})")
        except subprocess.TimeoutExpired:
            raise ConnectorError(f"git clone timed out for {repo_full_name}")

        report_path = os.path.join(tmpdir, "report.json")
        try:
            subprocess.run(
                [gitleaks_bin, "detect", "--source", repo_dir, "--report-format", "json",
                 "--report-path", report_path, "--exit-code", "0", "--no-banner"],
                check=True, capture_output=True, timeout=timeout,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace")[:300]
            raise ConnectorError(f"gitleaks scan failed for {repo_full_name}: {stderr}")
        except subprocess.TimeoutExpired:
            raise ConnectorError(f"gitleaks scan timed out for {repo_full_name}")

        if not os.path.isfile(report_path):
            return []
        with open(report_path, "r", encoding="utf-8") as f:
            raw = json.load(f) or []

    return parse_gitleaks_report(raw)


def parse_gitleaks_report(raw_findings: list[dict]) -> list[dict]:
    """Normalize gitleaks' JSON report into a DB/event-safe shape. The
    plaintext secret is redacted here — the earliest point after gitleaks
    itself produces it — to a fixed placeholder carrying no information
    about the real secret's length or content, since either could narrow a
    search space for real credential formats."""
    findings = []
    for f in raw_findings or []:
        findings.append({
            "rule_id":     f.get("RuleID"),
            "description": f.get("Description"),
            "file_path":   f.get("File"),
            "line_number": f.get("StartLine"),
            "commit":      f.get("Commit"),
            "author":      f.get("Author"),
            "date":        f.get("Date"),
            "fingerprint": f.get("Fingerprint"),
            "secret":      "***REDACTED***",
        })
    return findings


def evaluate_secret_scan_severity(findings: list[dict]) -> str:
    """Any real gitleaks finding is a live credential exposure — matches
    POL-GH-001's zero-tolerance treatment of SECRET_DETECTED (automatically
    CRITICAL regardless of confidence), so severity here is binary rather
    than tiered by rule type."""
    return "CRITICAL" if findings else "INFO"
