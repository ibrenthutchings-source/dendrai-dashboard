#!/usr/bin/env python3
"""
AWS Inspector v2 poll-connector adapter
(observability.poll_connectors, connector_type='aws_inspector').

Infrastructure Vulnerability & Currency Posture, Phase 3: real CVE findings
with CVSS for EC2/ECR/Lambda, via inspector2.list_findings — unlike
version_baselines.py/osv_client.py's version-string matching, Inspector
findings come pre-matched by AWS against the actual installed package
inventory, so they need no separate ecosystem/version-currency inference.

Same credential/session shape as aws_iaas_tool.py/aws_patch_tool.py
(duplicated rather than shared — see aws_patch_tool.py's docstring for why).

Two consumers of _audit_once(), the same "one real check, two views" split
postgres_cis_tool.py/iaas_connectors.py already establish:
  - pull_events() below -> observability.system_telemetry, for the existing
    Infrastructure Posture matrix (GET /infra-monitoring/results).
  - vulnerability_sweep.py calls _audit_once() directly and upserts each
    ACTIVE finding into observability.infra_vulnerabilities with
    source='connector' — Inspector findings need no OSV enrichment, they
    already carry vuln_id/severity/cvss/fixed_version.

Required per-connector config (set via the app UI, not env vars):
  credentials: EITHER {"role_arn": "..."} OR {"access_key_id": ..., "secret_access_key": ...}
  extra_config: { "regions": "us-east-1,us-west-2" (comma-separated, defaults to us-east-1) }

IAM policy should be read-only: inspector2:ListFindings.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

try:
    import boto3
    _HAS_BOTO3 = True
except ImportError:
    _HAS_BOTO3 = False

logger = logging.getLogger(__name__)

_SEVERITY_MAP = {
    "CRITICAL": "CRITICAL", "HIGH": "HIGH", "MEDIUM": "MEDIUM",
    "LOW": "LOW", "INFORMATIONAL": "INFO", "UNTRIAGED": "MEDIUM",
}


def _require_boto3() -> None:
    if not _HAS_BOTO3:
        raise ImportError("boto3 library required: pip install boto3")


def _session_from_credentials(credentials: dict):
    _require_boto3()
    credentials = credentials or {}
    role_arn = credentials.get("role_arn")
    if role_arn:
        sts = boto3.client("sts")
        resp = sts.assume_role(RoleArn=role_arn, RoleSessionName="dendrai-inspector-audit")
        creds = resp["Credentials"]
        return boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
    access_key = credentials.get("access_key_id")
    secret_key = credentials.get("secret_access_key")
    if not access_key or not secret_key:
        raise ValueError("credentials must include either role_arn or access_key_id+secret_access_key")
    return boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=credentials.get("session_token"),
    )


def _normalize_finding(raw: dict, region: str) -> dict:
    """Flattens one Inspector finding into the shape both pull_events() and
    vulnerability_sweep.py's sync need. package_name/package_version/
    fixed_version come from the FIRST vulnerable package Inspector lists —
    a finding can name several (a transitive dependency chain); the first is
    the one Inspector itself leads with in its own console, so this mirrors
    that rather than picking arbitrarily."""
    pkg_details = raw.get("packageVulnerabilityDetails") or {}
    vuln_id = pkg_details.get("vulnerabilityId") or raw.get("title") or "UNKNOWN"
    packages = pkg_details.get("vulnerablePackages") or []
    first_pkg = packages[0] if packages else {}
    cvss_list = pkg_details.get("cvss") or []
    cvss_score = cvss_list[0].get("baseScore") if cvss_list else None
    resources = raw.get("resources") or []
    first_resource = resources[0] if resources else {}
    return {
        "vuln_id": vuln_id,
        "severity": _SEVERITY_MAP.get(raw.get("severity"), "MEDIUM"),
        "cvss_score": cvss_score,
        "title": raw.get("title"),
        "summary": (raw.get("description") or "")[:2000],
        "status": raw.get("status"),  # ACTIVE | CLOSED | SUPPRESSED
        "resource_id": first_resource.get("id"),
        "resource_type": first_resource.get("type"),  # AWS_EC2_INSTANCE | AWS_ECR_CONTAINER_IMAGE | ...
        "package_name": first_pkg.get("name"),
        "package_version": first_pkg.get("version"),
        "fixed_version": first_pkg.get("fixedInVersion"),
        "first_observed_at": raw.get("firstObservedAt").isoformat() if raw.get("firstObservedAt") else None,
        "region": region,
    }


def _audit_region(session, region: str) -> list[dict]:
    inspector = session.client("inspector2", region_name=region)
    findings = []
    for page in inspector.get_paginator("list_findings").paginate(
        filterCriteria={"findingStatus": [{"comparison": "EQUALS", "value": "ACTIVE"}]}
    ):
        for raw in page.get("findings", []):
            findings.append(_normalize_finding(raw, region))
    return findings


def _audit_once(credentials: dict, extra_config: dict) -> list[dict]:
    extra_config = extra_config or {}
    session = _session_from_credentials(credentials)
    regions = [r.strip() for r in (extra_config.get("regions") or "us-east-1").split(",") if r.strip()]
    findings = []
    for region in regions:
        findings += _audit_region(session, region)
    return findings


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict, since) -> list[dict]:
    """One event per active finding per poll tick — day-scoped event_id, same
    idiom as the other config-posture connectors."""
    findings = _audit_once(credentials, extra_config)
    today = datetime.now(timezone.utc).date().isoformat()
    events = []
    for f in findings:
        events.append({
            "event_id":    f"aws-inspector:{f['vuln_id']}:{f['resource_id']}:{today}",
            "event_type":  "infrastructure_finding",
            "actor":       "aws_inspector_tool",
            "action":      "cve_finding",
            "resource":    f"{f['resource_type']}:{f['resource_id']}",
            "severity":    f["severity"],
            "raw_payload": {
                "infrastructure_finding": True,
                "check_id": "aws-inspector-v1",
                "infra_compliance": f,
            },
        })
    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    try:
        findings = _audit_once(credentials, extra_config)
        return True, f"Found {len(findings)} active finding(s)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return _HAS_BOTO3
