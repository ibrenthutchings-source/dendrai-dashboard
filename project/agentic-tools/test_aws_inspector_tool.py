#!/usr/bin/env python3
"""
Unit tests for aws_inspector_tool.py — AWS Inspector v2 connector. boto3
sessions/clients/paginators monkeypatched at the module boundary — no real
AWS calls.

    pytest test_aws_inspector_tool.py -v
"""

from __future__ import annotations

import pytest

import aws_inspector_tool


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kw):
        return iter(self._pages)


class _FakeInspectorClient:
    def __init__(self, findings):
        self._findings = findings

    def get_paginator(self, name):
        assert name == "list_findings"
        return _FakePaginator([{"findings": self._findings}])


class _FakeSession:
    def __init__(self, findings):
        self._findings = findings

    def client(self, service, region_name=None):
        assert service == "inspector2"
        return _FakeInspectorClient(self._findings)


def _raw_finding(**over):
    base = {
        "title": "CVE-2021-1234 - openssl",
        "description": "A vulnerability in openssl.",
        "severity": "HIGH",
        "status": "ACTIVE",
        "type": "PACKAGE_VULNERABILITY",
        "packageVulnerabilityDetails": {
            "vulnerabilityId": "CVE-2021-1234",
            "vulnerablePackages": [{"name": "openssl", "version": "1.1.1a", "fixedInVersion": "1.1.1w"}],
            "cvss": [{"baseScore": 7.5, "version": "3.1"}],
        },
        "resources": [{"id": "i-0abc123", "type": "AWS_EC2_INSTANCE"}],
        "firstObservedAt": None,
    }
    base.update(over)
    return base


# ── _normalize_finding ────────────────────────────────────────────────────────

def test_normalize_finding_extracts_expected_fields():
    n = aws_inspector_tool._normalize_finding(_raw_finding(), "us-east-1")
    assert n["vuln_id"] == "CVE-2021-1234"
    assert n["severity"] == "HIGH"
    assert n["cvss_score"] == 7.5
    assert n["package_name"] == "openssl"
    assert n["package_version"] == "1.1.1a"
    assert n["fixed_version"] == "1.1.1w"
    assert n["resource_id"] == "i-0abc123"
    assert n["resource_type"] == "AWS_EC2_INSTANCE"
    assert n["region"] == "us-east-1"


def test_normalize_finding_maps_informational_to_info():
    n = aws_inspector_tool._normalize_finding(_raw_finding(severity="INFORMATIONAL"), "us-east-1")
    assert n["severity"] == "INFO"


def test_normalize_finding_unknown_severity_defaults_to_medium():
    n = aws_inspector_tool._normalize_finding(_raw_finding(severity="SOMETHING_NEW"), "us-east-1")
    assert n["severity"] == "MEDIUM"


def test_normalize_finding_no_packages_or_resources_does_not_crash():
    raw = _raw_finding(packageVulnerabilityDetails={"vulnerabilityId": "CVE-X"}, resources=[])
    n = aws_inspector_tool._normalize_finding(raw, "us-east-1")
    assert n["package_name"] is None
    assert n["resource_id"] is None


def test_normalize_finding_falls_back_to_title_when_no_vulnerability_id():
    raw = _raw_finding(packageVulnerabilityDetails={})
    n = aws_inspector_tool._normalize_finding(raw, "us-east-1")
    assert n["vuln_id"] == raw["title"]


# ── _audit_once ────────────────────────────────────────────────────────────────

def test_audit_once_requires_credentials(monkeypatch):
    monkeypatch.setattr(aws_inspector_tool, "_HAS_BOTO3", True)
    with pytest.raises(ValueError):
        aws_inspector_tool._audit_once({}, {})


def test_audit_once_covers_multiple_regions(monkeypatch):
    monkeypatch.setattr(aws_inspector_tool, "_session_from_credentials",
                         lambda credentials: _FakeSession([_raw_finding()]))
    findings = aws_inspector_tool._audit_once({"role_arn": "x"}, {"regions": "us-east-1,us-west-2"})
    assert len(findings) == 2


# ── pull_events / test_connection ────────────────────────────────────────────

def test_pull_events_shapes_one_event_per_finding(monkeypatch):
    monkeypatch.setattr(aws_inspector_tool, "_session_from_credentials",
                         lambda credentials: _FakeSession([_raw_finding()]))
    events = aws_inspector_tool.pull_events(None, {"role_arn": "x"}, {"regions": "us-east-1"}, None)
    assert len(events) == 1
    assert events[0]["severity"] == "HIGH"
    assert events[0]["raw_payload"]["infrastructure_finding"] is True
    assert events[0]["raw_payload"]["infra_compliance"]["vuln_id"] == "CVE-2021-1234"
    assert "CVE-2021-1234" in events[0]["event_id"]


def test_connection_reports_finding_count(monkeypatch):
    monkeypatch.setattr(aws_inspector_tool, "_session_from_credentials",
                         lambda credentials: _FakeSession([_raw_finding(), _raw_finding()]))
    ok, msg = aws_inspector_tool.test_connection(None, {"role_arn": "x"}, {})
    assert ok is True
    assert "2 active finding" in msg


def test_connection_failure_reports_exception(monkeypatch):
    def _raise(credentials):
        raise ValueError("bad creds")
    monkeypatch.setattr(aws_inspector_tool, "_session_from_credentials", _raise)
    ok, msg = aws_inspector_tool.test_connection(None, {}, {})
    assert ok is False
    assert "ValueError" in msg


def test_is_configured_reflects_boto3_availability():
    assert aws_inspector_tool.is_configured() == aws_inspector_tool._HAS_BOTO3
