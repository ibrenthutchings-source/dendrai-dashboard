#!/usr/bin/env python3
"""
Trivy SARIF compatibility regression test (P2b) — DevOps Monitoring's
Evidence Ingestion Engine (evidence_endpoints.py's POST /evidence/webhook)
accepts SARIF from any scanner via a free-text `source` field; this locks in
that Trivy's real output actually parses correctly, rather than assuming it
based on the SARIF spec alone.

TRIVY_SARIF_SAMPLE below is a trimmed, VERBATIM two-finding excerpt from a
real `trivy fs --format sarif` run (Trivy v0.72.0) against an npm
package-lock.json with real (if since-patched) CVEs — not a hand-built
approximation of what Trivy's output might look like. Two real findings
were kept deliberately because they exercise different extraction paths:
  - GHSA-frvp-7c67-39w9: a GitHub Security Advisory ID, not a CVE. Confirms
    _extract_cve() correctly returns None rather than a false match — GHSA
    and CVE are different identifier namespaces, and only one is a CVE.
  - CVE-2026-13676: ruleId IS the CVE string itself (typical for Trivy,
    unlike CodeQL where ruleId is a scanner rule name and the CVE lives in
    the message text) — confirms extraction from ruleId works.

Also documents a real, non-obvious characteristic: Trivy's dependency-
vulnerability SARIF output never tags a CWE (no `cwe-\\d+` anywhere in tags,
ruleId, or message) — cwe is always None for Trivy findings. This isn't a
parser bug; Trivy is CVE/GHSA-centric, not CWE-centric, unlike CodeQL.
Evidence Inspector users should expect a blank CWE column for Trivy rows.

    pytest test_trivy_sarif.py -v
"""

from __future__ import annotations

import evidence_endpoints

TRIVY_SARIF_SAMPLE = {
    "version": "2.1.0",
    "runs": [{
        "tool": {"driver": {"name": "Trivy", "version": "0.72.0", "rules": [
            {
                "id": "GHSA-frvp-7c67-39w9",
                "name": "LanguageSpecificPackageVulnerability",
                "shortDescription": {"text": "Node.js Adapter for Hono: Path traversal in `serve-static` on Windows via encoded backslash (`%5C`)"},
                "fullDescription": {"text": "On Windows hosts, an encoded backslash (%5C) in the request path decodes to \\, which the Windows path resolver treats as a separator..."},
                "defaultConfiguration": {"level": "warning"},
                "helpUri": "https://github.com/advisories/GHSA-frvp-7c67-39w9",
                "properties": {
                    "cvssv3_baseScore": 5.9,
                    "cvssv3_vector": "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N",
                    "precision": "very-high",
                    "security-severity": "5.9",
                    "tags": ["vulnerability", "security", "MEDIUM"],
                },
            },
            {
                "id": "CVE-2026-13676",
                "name": "LanguageSpecificPackageVulnerability",
                "shortDescription": {"text": "fast-uri: Security policy bypass due to improper Unicode hostname canonicalization"},
                "fullDescription": {"text": "fast-uri versions 2.3.1 through 3.1.2 and 4.0.0 fail to canonicalize Unicode (IDN) hostnames for HTTP-family URLs..."},
                "defaultConfiguration": {"level": "error"},
                "helpUri": "https://avd.aquasec.com/nvd/cve-2026-13676",
                "properties": {
                    "cvssv3_baseScore": 7.5,
                    "cvssv3_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:H/A:N",
                    "precision": "very-high",
                    "security-severity": "7.5",
                    "tags": ["vulnerability", "security", "HIGH"],
                },
            },
        ]}},
        "results": [
            {
                "ruleId": "GHSA-frvp-7c67-39w9", "ruleIndex": 0, "level": "warning",
                "message": {"text": "Package: @hono/node-server\nInstalled Version: 1.19.14\nVulnerability GHSA-frvp-7c67-39w9\nSeverity: MEDIUM\nFixed Version: 2.0.5"},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": "opa-mcp-server/package-lock.json", "uriBaseId": "ROOTPATH"},
                    "region": {"startLine": 696, "startColumn": 1, "endLine": 707, "endColumn": 1},
                }, "message": {"text": "opa-mcp-server/package-lock.json: @hono/node-server@1.19.14"}}],
            },
            {
                "ruleId": "CVE-2026-13676", "ruleIndex": 1, "level": "error",
                "message": {"text": "Package: fast-uri\nInstalled Version: 3.1.2\nVulnerability CVE-2026-13676\nSeverity: HIGH\nFixed Version: 4.0.1, 3.1.3, 2.4.2"},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": "opa-mcp-server/package-lock.json", "uriBaseId": "ROOTPATH"},
                    "region": {"startLine": 2482, "startColumn": 1, "endLine": 2497, "endColumn": 1},
                }, "message": {"text": "opa-mcp-server/package-lock.json: fast-uri@3.1.2"}}],
            },
        ],
    }],
}


def test_parse_sarif_handles_real_trivy_output_without_error():
    findings = evidence_endpoints.parse_sarif(TRIVY_SARIF_SAMPLE)
    assert len(findings) == 2


def test_parse_sarif_trivy_cvss_security_severity_maps_correctly():
    findings = {f["rule_id"]: f for f in evidence_endpoints.parse_sarif(TRIVY_SARIF_SAMPLE)}
    assert findings["GHSA-frvp-7c67-39w9"]["severity"] == "MEDIUM"   # CVSS 5.9
    assert findings["CVE-2026-13676"]["severity"] == "HIGH"          # CVSS 7.5


def test_parse_sarif_trivy_cve_ruleid_extracted():
    findings = {f["rule_id"]: f for f in evidence_endpoints.parse_sarif(TRIVY_SARIF_SAMPLE)}
    assert findings["CVE-2026-13676"]["cve"] == "CVE-2026-13676"


def test_parse_sarif_trivy_ghsa_ruleid_is_not_mistaken_for_a_cve():
    """GHSA-frvp-7c67-39w9 is a GitHub Security Advisory ID, not a CVE — the
    two identifier namespaces must not be conflated."""
    findings = {f["rule_id"]: f for f in evidence_endpoints.parse_sarif(TRIVY_SARIF_SAMPLE)}
    assert findings["GHSA-frvp-7c67-39w9"]["cve"] is None


def test_parse_sarif_trivy_never_populates_cwe():
    """Real, non-obvious characteristic: Trivy's dependency-vulnerability
    SARIF has no CWE tags at all (CVE/GHSA-centric, unlike CodeQL) — cwe is
    always None for Trivy findings, not a parser gap."""
    findings = evidence_endpoints.parse_sarif(TRIVY_SARIF_SAMPLE)
    assert all(f["cwe"] is None for f in findings)


def test_parse_sarif_trivy_file_path_and_line_extracted():
    findings = {f["rule_id"]: f for f in evidence_endpoints.parse_sarif(TRIVY_SARIF_SAMPLE)}
    assert findings["CVE-2026-13676"]["file_path"] == "opa-mcp-server/package-lock.json"
    assert findings["CVE-2026-13676"]["line_number"] == 2482


def test_compute_fingerprint_and_sign_record_work_on_trivy_findings():
    """End-to-end: a Trivy finding must fingerprint and HMAC-sign the same
    way any other SARIF source's finding does — no Trivy-specific branch
    needed anywhere in the ingestion path, confirming P2b needs no new code,
    only documentation of what already works."""
    f = evidence_endpoints.parse_sarif(TRIVY_SARIF_SAMPLE)[1]  # CVE-2026-13676
    fingerprint = evidence_endpoints.compute_fingerprint(
        "org/repo", f["file_path"], f["rule_id"], f["line_snippet"])
    assert len(fingerprint) == 64  # SHA-256 hex digest
    record_json = {"repository": "org/repo", "rule_id": f["rule_id"], "severity": f["severity"],
                   "cwe": f["cwe"], "cve": f["cve"], "fingerprint": fingerprint}
    signature = evidence_endpoints.sign_record(record_json)
    assert len(signature) == 64  # HMAC-SHA256 hex digest
