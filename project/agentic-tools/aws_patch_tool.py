#!/usr/bin/env python3
"""
AWS SSM Patch Manager poll-connector adapter
(observability.poll_connectors, connector_type='aws_patch').

Infrastructure Vulnerability & Currency Posture, Phase 3: the highest-value
Phase 3 item per the plan — real OS patch compliance (installed / missing /
failed counts) for every EC2 instance running the SSM agent, via
ssm.describe_instance_patch_states. This is what "has patching been applied"
actually means for a fleet, as opposed to aws_iaas_tool.py's config-drift
checks (public buckets, open ports) which are a different question entirely.

Same credential shape and session helper as aws_iaas_tool.py (role_arn
preferred, access key pair as fallback) — this module intentionally
duplicates _session_from_credentials rather than importing it from
aws_iaas_tool, since the two connectors are registered and configured
independently (a customer may grant read access to one service's API but
not the other) and duplicating ~15 lines is cheaper than coupling two
otherwise-unrelated connector modules together.

Required per-connector config (set via the app UI, not env vars):
  credentials: EITHER {"role_arn": "..."} OR {"access_key_id": ..., "secret_access_key": ...}
  extra_config: {
    "regions": "us-east-1,us-west-2"  (comma-separated, defaults to us-east-1)
  }

IAM policy should be read-only: ssm:DescribeInstancePatchStates,
ssm:DescribeInstanceInformation.
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


def _require_boto3() -> None:
    if not _HAS_BOTO3:
        raise ImportError("boto3 library required: pip install boto3")


def _session_from_credentials(credentials: dict):
    _require_boto3()
    credentials = credentials or {}
    role_arn = credentials.get("role_arn")
    if role_arn:
        sts = boto3.client("sts")
        resp = sts.assume_role(RoleArn=role_arn, RoleSessionName="dendrai-patch-audit")
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


def _audit_region(session, region: str) -> list[dict]:
    """One row per managed instance with SSM-visible patch state. Instances
    with no SSM agent (not registered with Systems Manager) simply don't
    appear here — that's a real coverage gap, not something this check can
    paper over; the asset registry's last_assessed_at stays NULL for them
    since nothing calls mark_infra_asset_assessed for an instance that was
    never returned."""
    ssm = session.client("ssm", region_name=region)
    rows = []
    for page in ssm.get_paginator("describe_instance_patch_states").paginate():
        for state in page.get("InstancePatchStates", []):
            rows.append({
                "instance_id": state["InstanceId"],
                "region": region,
                "os": state.get("OperatingSystem"),
                "installed_count": state.get("InstalledCount", 0),
                "missing_count": state.get("MissingCount", 0),
                "failed_count": state.get("FailedCount", 0),
                "not_applicable_count": state.get("NotApplicableCount", 0),
                "patch_group": state.get("PatchGroup"),
                "last_scan_at": state.get("OperationEndTime").isoformat() if state.get("OperationEndTime") else None,
            })
    return rows


def _audit_once(credentials: dict, extra_config: dict) -> list[dict]:
    extra_config = extra_config or {}
    session = _session_from_credentials(credentials)
    regions = [r.strip() for r in (extra_config.get("regions") or "us-east-1").split(",") if r.strip()]
    rows = []
    for region in regions:
        rows += _audit_region(session, region)
    return rows


def _severity_for(row: dict) -> str:
    """CRITICAL for any failed patch install (an attempted fix that didn't
    take — worse than simply not-yet-patched); HIGH for missing patches;
    INFO for a fully patched instance."""
    if row["failed_count"] > 0:
        return "CRITICAL"
    if row["missing_count"] > 0:
        return "HIGH"
    return "INFO"


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict, since) -> list[dict]:
    """One patch-compliance event per managed instance per poll tick — same
    day-scoped event_id idiom as the other config-posture connectors."""
    rows = _audit_once(credentials, extra_config)
    today = datetime.now(timezone.utc).date().isoformat()
    events = []
    for r in rows:
        severity = _severity_for(r)
        events.append({
            "event_id":    f"aws-patch:{r['instance_id']}:{today}",
            "event_type":  "infrastructure_finding",
            "actor":       "aws_patch_tool",
            "action":      "os_patch_compliance",
            "resource":    f"ec2:{r['instance_id']}:{r['region']}",
            "severity":    severity,
            "raw_payload": {
                "infrastructure_finding": severity in ("HIGH", "CRITICAL"),
                "check_id": "aws-patch-v1",
                "infra_compliance": r,
            },
        })
    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    try:
        rows = _audit_once(credentials, extra_config)
        missing = sum(1 for r in rows if r["missing_count"] > 0)
        return True, f"Audited {len(rows)} managed instance(s) — {missing} with missing patches"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return _HAS_BOTO3
