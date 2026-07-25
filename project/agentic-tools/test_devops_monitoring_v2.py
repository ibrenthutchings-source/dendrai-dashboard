#!/usr/bin/env python3
"""
Unit tests for the Drift Engine, Risk Waiver Hub, and provenance/attestation
(OIDC/SLSA/Cosign/SBOM) additions to DevOps Monitoring. Pure-function tests
only — no DB, no network, no cosign binary required.

    pytest test_devops_monitoring_v2.py -v
"""

from __future__ import annotations

import attestation
import scm_connectors


# ── scm_connectors.diff_compliance (drift engine) ─────────────────────────────

def test_diff_compliance_no_change():
    baseline = {"enforce_admins": True, "required_approving_review_count": 2}
    current = {"enforce_admins": True, "required_approving_review_count": 2}
    assert scm_connectors.diff_compliance(baseline, current) == []


def test_diff_compliance_detects_admin_bypass_regression():
    baseline = {"enforce_admins": True, "required_approving_review_count": 2,
                "dismiss_stale_reviews": True, "has_required_sast_check": True,
                "has_required_test_check": True, "codeowners_present": True,
                "codeowners_covers_workflows": True}
    current = {**baseline, "enforce_admins": False}
    diffs = scm_connectors.diff_compliance(baseline, current)
    assert len(diffs) == 1
    assert diffs[0]["control_name"] == "enforce_admins"
    assert diffs[0]["direction"] == "regressed"
    assert diffs[0]["expected_state"] == {"enforce_admins": True}
    assert diffs[0]["actual_state"] == {"enforce_admins": False}


def test_diff_compliance_detects_restoration_as_improved():
    # The "2am override" pattern: admin disabled it (baseline already reflects
    # the disabled state from the last audit), then restored it (current).
    baseline = {"enforce_admins": False}
    current = {"enforce_admins": True}
    diffs = scm_connectors.diff_compliance(baseline, current)
    assert diffs[0]["direction"] == "improved"


def test_diff_compliance_review_count_decrease_is_regression():
    baseline = {"required_approving_review_count": 2}
    current = {"required_approving_review_count": 1}
    diffs = scm_connectors.diff_compliance(baseline, current)
    assert len(diffs) == 1
    assert diffs[0]["control_name"] == "required_approving_review_count"
    assert diffs[0]["direction"] == "regressed"


def test_diff_compliance_review_count_increase_is_improvement():
    baseline = {"required_approving_review_count": 1}
    current = {"required_approving_review_count": 2}
    diffs = scm_connectors.diff_compliance(baseline, current)
    assert diffs[0]["direction"] == "improved"


def test_diff_compliance_multiple_controls_changed():
    baseline = {"enforce_admins": True, "codeowners_present": True, "dismiss_stale_reviews": True}
    current = {"enforce_admins": False, "codeowners_present": False, "dismiss_stale_reviews": True}
    diffs = scm_connectors.diff_compliance(baseline, current)
    changed = {d["control_name"] for d in diffs}
    assert changed == {"enforce_admins", "codeowners_present"}
    assert all(d["direction"] == "regressed" for d in diffs)


def test_diff_compliance_missing_keys_default_falsy():
    # baseline/current dicts from real audits always have every key, but the
    # function should not crash on a sparse dict (e.g. a hand-built fixture).
    diffs = scm_connectors.diff_compliance({}, {"enforce_admins": True})
    assert diffs and diffs[0]["direction"] == "improved"


# ── attestation.hash_env_vars ──────────────────────────────────────────────────

def test_hash_env_vars_deterministic_and_order_independent():
    h1 = attestation.hash_env_vars({"SKIP_TESTS": "false", "CI": "true"})
    h2 = attestation.hash_env_vars({"CI": "true", "SKIP_TESTS": "false"})
    assert h1 == h2
    assert len(h1) == 64


def test_hash_env_vars_changes_when_a_flag_is_injected():
    baseline = attestation.hash_env_vars({"CI": "true", "SKIP_TESTS": "false"})
    tampered = attestation.hash_env_vars({"CI": "true", "SKIP_TESTS": "true"})
    assert baseline != tampered


def test_hash_env_vars_never_leaks_raw_values():
    # The hash itself must not contain the plaintext secret/flag value.
    h = attestation.hash_env_vars({"DISABLE_SAST": "1", "API_TOKEN": "super-secret-value"})
    assert "super-secret-value" not in h
    assert "DISABLE_SAST" not in h


# ── attestation.validate_slsa_provenance ──────────────────────────────────────

def test_slsa_missing_required_fields():
    result = attestation.validate_slsa_provenance({"predicateType": "https://slsa.dev/provenance/v1"})
    assert result["valid"] is False
    assert result["level"] == 0
    assert "subject" in result["missing_fields"]


def test_slsa_level_1_digest_only():
    statement = {
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [{"name": "app.bin", "digest": {"sha256": "abc123"}}],
        "predicate": {},
    }
    result = attestation.validate_slsa_provenance(statement)
    assert result["valid"] is True
    assert result["level"] == 1


def test_slsa_level_2_with_builder_identity():
    statement = {
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [{"name": "app.bin", "digest": {"sha256": "abc123"}}],
        "predicate": {"builder": {"id": "https://github.com/actions/runner"}, "buildType": "github-actions"},
    }
    result = attestation.validate_slsa_provenance(statement)
    assert result["level"] == 2


def test_slsa_level_3_with_materials():
    statement = {
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [{"name": "app.bin", "digest": {"sha256": "abc123"}}],
        "predicate": {
            "builder": {"id": "https://github.com/actions/runner"},
            "buildType": "github-actions",
            "materials": [{"uri": "git+https://github.com/org/repo", "digest": {"sha1": "deadbeef"}}],
        },
    }
    result = attestation.validate_slsa_provenance(statement)
    assert result["level"] == 3


def test_slsa_subject_without_digest_caps_at_level_0():
    statement = {
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [{"name": "app.bin"}],  # no digest
        "predicate": {"builder": {"id": "x"}, "buildType": "y"},
    }
    result = attestation.validate_slsa_provenance(statement)
    assert result["level"] == 0


# ── attestation.verify_cosign_bundle ───────────────────────────────────────────

def test_cosign_bundle_missing_signature_material():
    result = attestation.verify_cosign_bundle({"someOtherField": 1})
    assert result["verified"] == "false"


def test_cosign_bundle_legacy_shape_reports_unknown_without_binary():
    # This test environment has no cosign binary — the function must degrade
    # to "unknown", never fabricate "true".
    result = attestation.verify_cosign_bundle({
        "base64Signature": "c2lnbmF0dXJl", "cert": "Y2VydA==", "rekorBundle": {"logIndex": 12345},
    })
    assert result["verified"] in ("unknown", "false")
    assert result["has_rekor_entry"] is True


def test_cosign_bundle_new_shape_detected():
    result = attestation.verify_cosign_bundle({
        "verificationMaterial": {"tlogEntries": [{"logIndex": 1}]},
        "messageSignature": {"signature": "..."},
    })
    assert result["has_rekor_entry"] is True


# ── attestation.parse_sbom ─────────────────────────────────────────────────────

def test_parse_sbom_cyclonedx_flags_copyleft():
    doc = {
        "components": [
            {"name": "libfoo", "version": "1.0", "licenses": [{"license": {"id": "MIT"}}]},
            {"name": "libgpl", "version": "2.0", "licenses": [{"license": {"id": "GPL-3.0-only"}}]},
        ]
    }
    result = attestation.parse_sbom(doc, "cyclonedx")
    assert result["component_count"] == 2
    assert result["license_risk"] is True
    assert len(result["copyleft_components"]) == 1
    assert result["copyleft_components"][0]["name"] == "libgpl"


def test_parse_sbom_spdx_no_copyleft():
    doc = {
        "packages": [
            {"name": "libbar", "versionInfo": "3.1", "licenseConcluded": "Apache-2.0"},
            {"name": "libbaz", "versionInfo": "1.2", "licenseConcluded": "NOASSERTION"},
        ]
    }
    result = attestation.parse_sbom(doc, "spdx")
    assert result["component_count"] == 2
    assert result["license_risk"] is False


def test_parse_sbom_unsupported_format():
    result = attestation.parse_sbom({}, "not-a-real-format")
    assert result["components"] == []
    assert "error" in result


def test_parse_sbom_mpl_and_lgpl_are_copyleft():
    assert attestation._is_copyleft("MPL-2.0") is True
    assert attestation._is_copyleft("LGPL-2.1-only") is True
    assert attestation._is_copyleft("BSD-3-Clause") is False
    assert attestation._is_copyleft(None) is False
