#!/usr/bin/env python3
"""
AWS cloud configuration drift + IAM session-duration poll-connector adapter
(observability.poll_connectors, connector_type='aws_iaas').

Technology Risk Pipeline: cloud infrastructure drift + IAM lease duration.
Scoped to AWS only for Phase 1 of the Multi-Domain Continuous Risk Pipeline
plan — Azure/GCP are deferred (no SDK in requirements.txt, would triple the
surface for no immediate payoff). Same pull_events()/test_connection()
poll-connector contract as postgres_cis_tool.py/railway_iaas_tool.py, one
event per audited resource (not one aggregate event) so the Infrastructure
Monitoring screen's per-resource matrix is meaningful.

Required per-connector config (set via the app UI, not env vars):
  credentials: EITHER
    {"role_arn": "arn:aws:iam::123456789012:role/dendrai-readonly-audit"}
      (recommended — cross-account role via sts:AssumeRole, no long-lived keys)
    OR
    {"access_key_id": "...", "secret_access_key": "...", "session_token": "..."}
      (session_token optional; use only when a role can't be granted)
  extra_config: {
    "regions": "us-east-1,us-west-2"  (comma-separated, defaults to us-east-1),
    "max_session_duration_hours": "12"  (optional, default 12 — IAM lease-duration threshold)
  }

The IAM policy behind either credential path should be read-only:
  s3:ListAllMyBuckets, s3:GetBucketPolicyStatus, s3:GetPublicAccessBlock,
  ec2:DescribeSecurityGroups, ec2:DescribeVolumes,
  rds:DescribeDBInstances, iam:ListRoles
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

try:
    import boto3
    from botocore.exceptions import ClientError
    _HAS_BOTO3 = True
except ImportError:
    _HAS_BOTO3 = False

logger = logging.getLogger(__name__)

# Ports whose exposure to 0.0.0.0/0 is almost never intentional: SSH, RDP,
# and the common database ports (MySQL/Postgres, MSSQL, Redis, MongoDB, Elasticsearch).
_SENSITIVE_PORTS = {22, 3389, 3306, 5432, 1433, 6379, 27017, 9200}


def _require_boto3() -> None:
    if not _HAS_BOTO3:
        raise ImportError("boto3 library required: pip install boto3")


def _session_from_credentials(credentials: dict):
    _require_boto3()
    credentials = credentials or {}
    role_arn = credentials.get("role_arn")
    if role_arn:
        sts = boto3.client("sts")
        resp = sts.assume_role(RoleArn=role_arn, RoleSessionName="dendrai-infra-audit")
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


def _audit_s3(session) -> list[dict]:
    s3 = session.client("s3")
    findings = []
    for bucket in s3.list_buckets().get("Buckets", []):
        name = bucket["Name"]
        is_public = False
        try:
            is_public = bool(s3.get_bucket_policy_status(Bucket=name)["PolicyStatus"]["IsPublic"])
        except ClientError:
            pass  # no bucket policy at all — not public via policy
        blocked = False
        try:
            pab = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
            blocked = all(pab.values())
        except ClientError:
            pass  # no public-access-block config — treated as not blocked
        findings.append({
            "resource": f"s3:{name}", "check": "s3_public_access",
            "violated": is_public and not blocked,
            "detail": {"is_public": is_public, "public_access_blocked": blocked},
        })
    return findings


def _audit_security_groups(session, region: str) -> list[dict]:
    ec2 = session.client("ec2", region_name=region)
    findings = []
    for sg in ec2.describe_security_groups().get("SecurityGroups", []):
        open_ports: set[int] = set()
        for perm in sg.get("IpPermissions", []):
            if not any(r.get("CidrIp") == "0.0.0.0/0" for r in perm.get("IpRanges", [])):
                continue
            from_port, to_port = perm.get("FromPort"), perm.get("ToPort")
            if from_port is None:  # IpProtocol == "-1" (all traffic)
                open_ports |= _SENSITIVE_PORTS
            else:
                open_ports |= {p for p in _SENSITIVE_PORTS if from_port <= p <= (to_port or from_port)}
        findings.append({
            "resource": f"sg:{sg['GroupId']}:{region}", "check": "security_group_open_ingress",
            "violated": bool(open_ports),
            "detail": {"open_sensitive_ports": sorted(open_ports)},
        })
    return findings


def _audit_encryption(session, region: str) -> list[dict]:
    findings = []
    ec2 = session.client("ec2", region_name=region)
    for vol in ec2.describe_volumes().get("Volumes", []):
        findings.append({
            "resource": f"ebs:{vol['VolumeId']}:{region}", "check": "unencrypted_volume",
            "violated": not vol.get("Encrypted", False),
            "detail": {"encrypted": vol.get("Encrypted", False)},
        })
    try:
        rds = session.client("rds", region_name=region)
        for db_instance in rds.describe_db_instances().get("DBInstances", []):
            findings.append({
                "resource": f"rds:{db_instance['DBInstanceIdentifier']}:{region}", "check": "unencrypted_rds",
                "violated": not db_instance.get("StorageEncrypted", False),
                "detail": {"encrypted": db_instance.get("StorageEncrypted", False)},
            })
    except ClientError:
        pass  # no RDS access, or none provisioned in this region
    return findings


def _audit_iam_session_duration(session, max_hours: float) -> list[dict]:
    """IAM lease duration: roles whose MaxSessionDuration exceeds the
    configured threshold (default 12h) can hand out excessively long-lived
    temporary credentials from a single AssumeRole call."""
    iam = session.client("iam")
    findings = []
    for page in iam.get_paginator("list_roles").paginate():
        for role in page.get("Roles", []):
            duration_hours = (role.get("MaxSessionDuration") or 3600) / 3600
            findings.append({
                "resource": f"iam-role:{role['RoleName']}", "check": "iam_excessive_session",
                "violated": duration_hours > max_hours,
                "detail": {"max_session_duration_hours": duration_hours},
            })
    return findings


def _audit_once(credentials: dict, extra_config: dict) -> list[dict]:
    extra_config = extra_config or {}
    session = _session_from_credentials(credentials)
    regions = [r.strip() for r in (extra_config.get("regions") or "us-east-1").split(",") if r.strip()]
    max_hours = float(extra_config.get("max_session_duration_hours") or 12)

    findings = _audit_s3(session)
    findings += _audit_iam_session_duration(session, max_hours)
    for region in regions:
        findings += _audit_security_groups(session, region)
        findings += _audit_encryption(session, region)
    return findings


def _severity_for(finding: dict) -> str:
    """CRITICAL for external exposure right now (public S3, open sensitive
    ports); HIGH for unencrypted storage or excessive IAM session duration."""
    if not finding["violated"]:
        return "INFO"
    if finding["check"] in ("s3_public_access", "security_group_open_ingress"):
        return "CRITICAL"
    return "HIGH"


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since) -> list[dict]:
    """One audit event per resource per poll tick (S3 bucket, security group,
    volume, RDS instance, IAM role) — a point-in-time configuration check
    like postgres_cis_tool.py/railway_iaas_tool.py, so `since` is unused.
    Every resource gets an event regardless of pass/fail (same convention as
    postgres_cis_tool.py) so the Infrastructure Monitoring matrix shows full
    status, not just violations."""
    findings = _audit_once(credentials, extra_config)
    today = datetime.now(timezone.utc).date().isoformat()
    events = []
    for f in findings:
        severity = _severity_for(f)
        events.append({
            "event_id":    f"aws-iaas:{f['resource']}:{today}",
            "event_type":  "infrastructure_finding",
            "actor":       "aws_iaas_tool",
            "action":      f["check"],
            "resource":    f["resource"],
            "severity":    severity,
            "raw_payload": {
                "infrastructure_finding": f["violated"],
                "check_id": "aws-iaas-v1",
                "infra_compliance": f["detail"],
            },
        })
    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity by running one real audit — the same call
    pull_events() needs."""
    try:
        findings = _audit_once(credentials, extra_config)
        violated = sum(1 for f in findings if f["violated"])
        return True, f"Audited {len(findings)} resource(s) — {violated} finding(s)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return _HAS_BOTO3
