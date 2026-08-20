#!/usr/bin/env python3
"""
Unit tests for aws_patch_tool.py — AWS SSM Patch Manager connector. boto3
sessions/clients/paginators are all monkeypatched at the module boundary —
no real AWS calls.

    pytest test_aws_patch_tool.py -v
"""

from __future__ import annotations

import pytest

import aws_patch_tool


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kw):
        return iter(self._pages)


class _FakeSSMClient:
    def __init__(self, states):
        self._states = states

    def get_paginator(self, name):
        assert name == "describe_instance_patch_states"
        return _FakePaginator([{"InstancePatchStates": self._states}])


class _FakeSession:
    def __init__(self, states):
        self._states = states

    def client(self, service, region_name=None):
        assert service == "ssm"
        return _FakeSSMClient(self._states)


def _state(**over):
    base = {
        "InstanceId": "i-0abc123", "OperatingSystem": "AMAZON_LINUX_2",
        "InstalledCount": 40, "MissingCount": 0, "FailedCount": 0,
        "NotApplicableCount": 2, "PatchGroup": "prod",
    }
    base.update(over)
    return base


# ── _audit_once / _audit_region ──────────────────────────────────────────────

def test_audit_once_requires_credentials(monkeypatch):
    monkeypatch.setattr(aws_patch_tool, "_HAS_BOTO3", True)
    with pytest.raises(ValueError):
        aws_patch_tool._audit_once({}, {})


def test_audit_once_flattens_instance_rows(monkeypatch):
    monkeypatch.setattr(aws_patch_tool, "_session_from_credentials",
                         lambda credentials: _FakeSession([_state()]))
    rows = aws_patch_tool._audit_once({"role_arn": "arn:aws:iam::123:role/x"}, {"regions": "us-east-1"})
    assert len(rows) == 1
    assert rows[0]["instance_id"] == "i-0abc123"
    assert rows[0]["missing_count"] == 0


def test_audit_once_covers_multiple_regions(monkeypatch):
    monkeypatch.setattr(aws_patch_tool, "_session_from_credentials",
                         lambda credentials: _FakeSession([_state()]))
    rows = aws_patch_tool._audit_once({"role_arn": "x"}, {"regions": "us-east-1,us-west-2"})
    assert len(rows) == 2
    assert {r["region"] for r in rows} == {"us-east-1", "us-west-2"}


# ── _severity_for ─────────────────────────────────────────────────────────────

def test_severity_failed_patches_is_critical():
    assert aws_patch_tool._severity_for({"failed_count": 1, "missing_count": 0}) == "CRITICAL"


def test_severity_missing_patches_is_high():
    assert aws_patch_tool._severity_for({"failed_count": 0, "missing_count": 3}) == "HIGH"


def test_severity_fully_patched_is_info():
    assert aws_patch_tool._severity_for({"failed_count": 0, "missing_count": 0}) == "INFO"


# ── pull_events / test_connection ────────────────────────────────────────────

def test_pull_events_missing_patches_flags_finding(monkeypatch):
    monkeypatch.setattr(aws_patch_tool, "_session_from_credentials",
                         lambda credentials: _FakeSession([_state(MissingCount=5)]))
    events = aws_patch_tool.pull_events(None, {"role_arn": "x"}, {}, None)
    assert len(events) == 1
    assert events[0]["severity"] == "HIGH"
    assert events[0]["raw_payload"]["infrastructure_finding"] is True
    assert events[0]["raw_payload"]["infra_compliance"]["missing_count"] == 5


def test_pull_events_fully_patched_not_flagged(monkeypatch):
    monkeypatch.setattr(aws_patch_tool, "_session_from_credentials",
                         lambda credentials: _FakeSession([_state()]))
    events = aws_patch_tool.pull_events(None, {"role_arn": "x"}, {}, None)
    assert events[0]["severity"] == "INFO"
    assert events[0]["raw_payload"]["infrastructure_finding"] is False


def test_connection_reports_missing_count(monkeypatch):
    monkeypatch.setattr(aws_patch_tool, "_session_from_credentials",
                         lambda credentials: _FakeSession([_state(MissingCount=2), _state(InstanceId="i-2")]))
    ok, msg = aws_patch_tool.test_connection(None, {"role_arn": "x"}, {})
    assert ok is True
    assert "2 managed instance" in msg
    assert "1 with missing patches" in msg


def test_connection_failure_reports_exception(monkeypatch):
    def _raise(credentials):
        raise ValueError("bad creds")
    monkeypatch.setattr(aws_patch_tool, "_session_from_credentials", _raise)
    ok, msg = aws_patch_tool.test_connection(None, {}, {})
    assert ok is False
    assert "ValueError" in msg


def test_is_configured_reflects_boto3_availability():
    assert aws_patch_tool.is_configured() == aws_patch_tool._HAS_BOTO3
