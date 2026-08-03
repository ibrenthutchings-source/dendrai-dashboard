#!/usr/bin/env python3
"""
Unit tests for the DevOps Monitoring category (SCM Integrity Auditor +
SARIF Evidence Ingestion). Pure-function tests only — no DB, no network,
no OPA binary required (the Rego test exercises pac_endpoints'
heuristic-evaluator fallback, which every environment has).

    pytest test_devops_monitoring.py -v
"""

from __future__ import annotations

import hashlib
import hmac

import evidence_endpoints
import mcp_governance
import pac_endpoints
import scm_connectors


# ── scm_connectors: normalization ─────────────────────────────────────────────

def test_normalize_github_compliance_fully_compliant():
    protection = {
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {
            "required_approving_review_count": 2,
            "dismiss_stale_reviews": True,
        },
        "required_status_checks": {"contexts": ["ci/codeql", "ci/unit-tests"]},
    }
    codeowners = "* @security-team\n.github/workflows/** @platform-team\n"
    c = scm_connectors.normalize_github_compliance(protection, codeowners)
    assert c["enforce_admins"] is True
    assert c["required_approving_review_count"] == 2
    assert c["dismiss_stale_reviews"] is True
    assert c["has_required_sast_check"] is True
    assert c["has_required_test_check"] is True
    assert c["codeowners_present"] is True
    assert c["codeowners_covers_workflows"] is True


def test_normalize_github_compliance_admin_bypass_critical():
    protection = {
        "enforce_admins": {"enabled": False},
        "required_pull_request_reviews": {"required_approving_review_count": 1, "dismiss_stale_reviews": True},
        "required_status_checks": {"contexts": ["ci/test"]},
    }
    c = scm_connectors.normalize_github_compliance(protection, "* @team\n")
    assert c["enforce_admins"] is False


def test_normalize_github_compliance_no_protection_is_non_compliant():
    # GitHub returns 404 for an unprotected branch; scm_connectors.fetch_*
    # returns {} for that case, which must fail every check closed.
    c = scm_connectors.normalize_github_compliance({}, None)
    assert c["enforce_admins"] is False
    assert c["required_approving_review_count"] == 0
    assert c["dismiss_stale_reviews"] is False
    assert c["codeowners_present"] is False


def test_codeowners_covers_workflows_true_and_false():
    assert scm_connectors._codeowners_covers_workflows("*.py @x\n.github/workflows/* @platform\n") is True
    assert scm_connectors._codeowners_covers_workflows("*.py @x\n") is False
    assert scm_connectors._codeowners_covers_workflows(None) is False
    assert scm_connectors._codeowners_covers_workflows("* @everyone\n") is True


def test_normalize_gitlab_compliance_maintainer_bypass():
    protected_branch = {
        "push_access_levels": [{"access_level": 40}],  # Maintainer can push directly
        "allow_force_push": False,
        "code_owner_approval_required": True,
    }
    approval_rules = [{"approvals_required": 2}]
    c = scm_connectors.normalize_gitlab_compliance(protected_branch, approval_rules, "* @team\n")
    assert c["enforce_admins"] is False  # maintainer bypass == enforce_admins false
    assert c["required_approving_review_count"] == 2
    assert c["dismiss_stale_reviews"] is True


def test_normalize_gitlab_compliance_no_bypass():
    protected_branch = {"push_access_levels": [], "allow_force_push": False}
    c = scm_connectors.normalize_gitlab_compliance(protected_branch, [], None)
    assert c["enforce_admins"] is True
    assert c["required_approving_review_count"] == 0
    assert c["codeowners_present"] is False


# ── evidence_endpoints: SARIF parsing ──────────────────────────────────────────

_SARIF_FIXTURE = {
    "runs": [
        {
            "tool": {"driver": {"rules": [
                {"id": "py/sql-injection", "properties": {"tags": ["external/cwe/cwe-089"], "security-severity": "9.8"}},
                {"id": "py/weak-crypto", "properties": {"tags": ["external/cwe/cwe-327"]}},
            ]}},
            "results": [
                {
                    "ruleId": "py/sql-injection",
                    "level": "error",
                    "message": {"text": "SQL injection via unsanitized input"},
                    "locations": [{"physicalLocation": {
                        "artifactLocation": {"uri": "app/db.py"},
                        "region": {"startLine": 42, "snippet": {"text": "cur.execute(f\"SELECT * FROM x WHERE id={user_id}\")"}},
                    }}],
                },
                {
                    "ruleId": "py/weak-crypto",
                    "level": "warning",
                    "message": {"text": "Use of MD5 for password hashing (see CVE-2004-2761)"},
                    "locations": [{"physicalLocation": {
                        "artifactLocation": {"uri": "app/auth.py"},
                        "region": {"startLine": 10},
                    }}],
                },
            ],
        }
    ]
}


def test_parse_sarif_extracts_all_findings():
    findings = evidence_endpoints.parse_sarif(_SARIF_FIXTURE)
    assert len(findings) == 2


def test_parse_sarif_security_severity_maps_to_cvss_bucket():
    findings = evidence_endpoints.parse_sarif(_SARIF_FIXTURE)
    sql_finding = next(f for f in findings if f["rule_id"] == "py/sql-injection")
    assert sql_finding["severity"] == "CRITICAL"  # security-severity 9.8 >= 9.0
    assert sql_finding["cwe"] == "CWE-89"
    assert sql_finding["file_path"] == "app/db.py"
    assert sql_finding["line_number"] == 42
    assert "user_id" in sql_finding["line_snippet"]


def test_parse_sarif_falls_back_to_level_and_extracts_cve_from_message():
    findings = evidence_endpoints.parse_sarif(_SARIF_FIXTURE)
    crypto_finding = next(f for f in findings if f["rule_id"] == "py/weak-crypto")
    assert crypto_finding["severity"] == "MEDIUM"  # level=="warning", no security-severity
    assert crypto_finding["cwe"] == "CWE-327"
    assert crypto_finding["cve"] == "CVE-2004-2761"


def test_parse_sarif_empty_payload():
    assert evidence_endpoints.parse_sarif({}) == []
    assert evidence_endpoints.parse_sarif({"runs": []}) == []


# ── evidence_endpoints: fingerprint + signing ─────────────────────────────────

def test_fingerprint_is_deterministic():
    fp1 = evidence_endpoints.compute_fingerprint("org/repo", "app/db.py", "py/sql-injection", "cur.execute(x)")
    fp2 = evidence_endpoints.compute_fingerprint("org/repo", "app/db.py", "py/sql-injection", "cur.execute(x)")
    assert fp1 == fp2
    assert len(fp1) == 64  # hex SHA256


def test_fingerprint_changes_with_line_snippet():
    fp1 = evidence_endpoints.compute_fingerprint("org/repo", "app/db.py", "py/sql-injection", "line A")
    fp2 = evidence_endpoints.compute_fingerprint("org/repo", "app/db.py", "py/sql-injection", "line B")
    assert fp1 != fp2


def test_fingerprint_matches_manual_sha256():
    expected = hashlib.sha256(b"org/repo|app/db.py|py/sql-injection|snippet").hexdigest()
    assert evidence_endpoints.compute_fingerprint("org/repo", "app/db.py", "py/sql-injection", "snippet") == expected


def test_sign_record_round_trip_and_tamper_detection(monkeypatch):
    monkeypatch.setattr(evidence_endpoints, "SIGNING_KEY", "test-signing-key")
    record = {"repository": "org/repo", "rule_id": "py/sql-injection", "severity": "CRITICAL"}
    sig = evidence_endpoints.sign_record(record)
    # Same record (even reconstructed fresh, key order irrelevant — sign_record sorts) -> same signature.
    same_record_diff_order = {"severity": "CRITICAL", "rule_id": "py/sql-injection", "repository": "org/repo"}
    assert evidence_endpoints.sign_record(same_record_diff_order) == sig
    # Tampering with any field must change the signature.
    tampered = {**record, "severity": "LOW"}
    assert evidence_endpoints.sign_record(tampered) != sig
    assert hmac.compare_digest(sig, sig)


# ── PaC: devops_monitoring Rego module (heuristic evaluator, no OPA needed) ────

def _rego():
    return pac_endpoints._REGO_DEFAULTS["devops_monitoring"]


def test_devops_monitoring_rego_registered_as_default():
    assert "devops_monitoring" in pac_endpoints._REGO_DEFAULTS
    assert "package controls.devops.monitoring" in _rego()


def test_devops_monitoring_rego_fires_on_admin_bypass():
    input_event = {"event": {
        "type": "BRANCH_PROTECTION_BYPASSED", "resource": "org/repo@main",
        "enforce_admins": False, "required_approving_review_count": 2,
        "dismiss_stale_reviews": True,
    }}
    result = pac_endpoints.evaluate_policy_event(_rego(), input_event)
    fired_rules = {r["rule"] for r in result["rules_fired"]}
    assert "deny_branch_protection" in fired_rules


def test_devops_monitoring_rego_silent_when_compliant():
    input_event = {"event": {
        "type": "BRANCH_PROTECTION_BYPASSED", "resource": "org/repo@main",
        "enforce_admins": True, "required_approving_review_count": 2,
        "dismiss_stale_reviews": True, "has_required_sast_check": True,
        "has_required_test_check": True, "codeowners_present": True,
        "codeowners_covers_workflows": True,
    }}
    result = pac_endpoints.evaluate_policy_event(_rego(), input_event)
    assert result["rules_fired"] == []


def test_devops_monitoring_rego_fires_on_critical_evidence_severity():
    input_event = {"event": {"severity": "CRITICAL", "rule_id": "py/sql-injection", "resource": "app/db.py"}}
    result = pac_endpoints.evaluate_policy_event(_rego(), input_event)
    fired_rules = {r["rule"] for r in result["rules_fired"]}
    assert "deny_evidence_finding" in fired_rules


# ── mcp_governance: SSRF guard on poll-connector base_url ──────────────────────
# The generic /observability/connectors form (not scm_audit_endpoints.py's
# unused register_repository) is the actual UI-facing registration path for
# github_scm/gitlab_scm connectors, so the guard must live in
# _validate_connector_base_url, wired into both create_connector and
# update_connector.

def test_connector_base_url_guard_rejects_private_ip_for_github_scm():
    try:
        mcp_governance._validate_connector_base_url("github_scm", "https://169.254.169.254/latest/meta-data")
        assert False, "expected HTTPException for a link-local target"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400


def test_connector_base_url_guard_rejects_http_scheme_for_gitlab_scm():
    try:
        mcp_governance._validate_connector_base_url("gitlab_scm", "http://gitlab.example.com/api/v4")
        assert False, "expected HTTPException for a non-https scheme"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400


def test_connector_base_url_guard_allows_blank_base_url():
    # github_scm/gitlab_scm may omit base_url entirely (adapter defaults to
    # the public api.github.com / gitlab.com host) — must not raise.
    mcp_governance._validate_connector_base_url("github_scm", None)
    mcp_governance._validate_connector_base_url("github_scm", "")


def test_connector_base_url_guard_skips_unguarded_connector_types():
    # Oracle Fusion/SAP HANA/etc. connectors legitimately point at
    # private/on-prem addresses — the guard must not touch them.
    mcp_governance._validate_connector_base_url("oracle_fusion", "https://10.0.5.20/api")
