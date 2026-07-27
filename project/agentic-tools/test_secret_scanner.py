#!/usr/bin/env python3
"""
Unit tests for secret_scanner_connectors.py: the real-gitleaks producer for
SECRET_DETECTED events outside a live GitHub Advanced Security webhook.
No network, no real git clone, no dependency on a gitleaks binary being
installed in the test environment — parse_gitleaks_report/severity are pure
functions tested against a fixture shaped exactly like gitleaks v8.28.0's
real JSON report (verified empirically during development, see the module
docstring), and is_gitleaks_available()/scan_repo_for_secrets' "not
installed" path is tested directly rather than skipped.

    pytest test_secret_scanner.py -v
"""

from __future__ import annotations

import secret_scanner_connectors as ssc

# A real gitleaks v8.28.0 JSON report entry, captured while verifying this
# integration against a throwaway git repo — see the module docstring.
_REAL_GITLEAKS_FINDING = {
    "RuleID": "github-pat",
    "Description": "Uncovered a GitHub Personal Access Token, potentially leading to unauthorized repository access and sensitive content exposure.",
    "StartLine": 3,
    "EndLine": 3,
    "StartColumn": 17,
    "EndColumn": 56,
    "Match": "ghp_XnYNyB0fYUSuEQ3MUivQF6QOknatgpwg78FR",
    "Secret": "ghp_XnYNyB0fYUSuEQ3MUivQF6QOknatgpwg78FR",
    "File": "config.py",
    "SymlinkFile": "",
    "Commit": "7d9774186bcd2a66db325f638b5b2e1eda265f91",
    "Entropy": 4.8341837,
    "Author": "test",
    "Email": "test@test.com",
    "Date": "2026-07-27T03:17:09Z",
    "Message": "add real token",
    "Tags": [],
    "Fingerprint": "7d9774186bcd2a66db325f638b5b2e1eda265f91:config.py:github-pat:3",
}


# ── parse_gitleaks_report ────────────────────────────────────────────────────

def test_parse_gitleaks_report_maps_real_fields():
    findings = ssc.parse_gitleaks_report([_REAL_GITLEAKS_FINDING])
    assert len(findings) == 1
    f = findings[0]
    assert f["rule_id"] == "github-pat"
    assert f["file_path"] == "config.py"
    assert f["line_number"] == 3
    assert f["commit"] == "7d9774186bcd2a66db325f638b5b2e1eda265f91"
    assert f["author"] == "test"
    assert f["fingerprint"] == "7d9774186bcd2a66db325f638b5b2e1eda265f91:config.py:github-pat:3"


def test_parse_gitleaks_report_never_leaks_the_secret_value():
    findings = ssc.parse_gitleaks_report([_REAL_GITLEAKS_FINDING])
    f = findings[0]
    assert f["secret"] == "***REDACTED***"
    # No key anywhere in the normalized finding may carry the raw value.
    assert "ghp_XnYNyB0fYUSuEQ3MUivQF6QOknatgpwg78FR" not in str(f)


def test_parse_gitleaks_report_empty_input():
    assert ssc.parse_gitleaks_report([]) == []
    assert ssc.parse_gitleaks_report(None) == []


def test_parse_gitleaks_report_handles_multiple_findings():
    second = dict(_REAL_GITLEAKS_FINDING, RuleID="aws-access-token", StartLine=10,
                  Fingerprint="abc:other.py:aws-access-token:10")
    findings = ssc.parse_gitleaks_report([_REAL_GITLEAKS_FINDING, second])
    assert len(findings) == 2
    assert {f["rule_id"] for f in findings} == {"github-pat", "aws-access-token"}


# ── evaluate_secret_scan_severity ────────────────────────────────────────────

def test_evaluate_secret_scan_severity_critical_when_findings_present():
    findings = ssc.parse_gitleaks_report([_REAL_GITLEAKS_FINDING])
    assert ssc.evaluate_secret_scan_severity(findings) == "CRITICAL"


def test_evaluate_secret_scan_severity_info_when_clean():
    assert ssc.evaluate_secret_scan_severity([]) == "INFO"


# ── _authenticated_clone_url ─────────────────────────────────────────────────

def test_authenticated_clone_url_embeds_token_and_normalizes_api_host():
    url = ssc._authenticated_clone_url("my-org/my-repo", "tok3n", "https://api.github.com")
    assert url == "https://x-access-token:tok3n@github.com/my-org/my-repo.git"


def test_authenticated_clone_url_url_encodes_special_characters_in_token():
    url = ssc._authenticated_clone_url("my-org/my-repo", "tok en/with?special", "https://api.github.com")
    assert "tok en" not in url  # raw space must not appear in a URL
    assert "github.com/my-org/my-repo.git" in url


# ── gitleaks-not-installed path (never fabricate a clean result) ───────────

def test_is_gitleaks_available_false_when_no_binary_configured(monkeypatch):
    monkeypatch.delenv("GITLEAKS_BINARY", raising=False)
    monkeypatch.setattr(ssc.shutil, "which", lambda name: None)
    assert ssc.is_gitleaks_available() is False


def test_scan_repo_for_secrets_returns_empty_list_not_error_when_unavailable(monkeypatch):
    """Mirrors pac_endpoints._find_opa_binary's fallback convention: an
    unavailable tool reports 'nothing to report', not a fabricated finding
    OR a fabricated clean bill of health — callers (scm_audit_endpoints)
    are responsible for surfacing 'scanned: False' distinctly from
    'scanned: True, finding_count: 0'."""
    monkeypatch.delenv("GITLEAKS_BINARY", raising=False)
    monkeypatch.setattr(ssc.shutil, "which", lambda name: None)
    result = ssc.scan_repo_for_secrets("my-org/my-repo", "tok3n")
    assert result == []
