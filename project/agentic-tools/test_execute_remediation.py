#!/usr/bin/env python3
"""
Unit tests for approvals_endpoints._execute_remediation — the function that
fires the actual GitHub write once a remediation_github (issue) or
remediation_github_pr (real file-change PR) task is approved. db.py and
github_write_tool.py are monkeypatched throughout (no real DB or GitHub
calls) — these tests verify the branch is chosen correctly by gate_type,
that create_pull_request receives the right files/base_branch, and that a
failure is persisted rather than swallowed, for both gate types.

    pytest test_execute_remediation.py -v
"""
from __future__ import annotations

import approvals_endpoints as ap


class _FakeDb:
    def __init__(self):
        self.execution_results = []
        self.triage_calls = []

    def set_approval_task_execution_result(self, task_id, result):
        self.execution_results.append((task_id, result))

    def submit_exception_triage(self, event_id, auditor, label, notes):
        self.triage_calls.append((event_id, auditor, label, notes))


def _issue_task(**adj_overrides) -> dict:
    adjustments = {"title": "Fix it", "body": "Details.", "repo": "acme/infra",
                   "source_event_id": 42, "control_id": "SCM-01"}
    adjustments.update(adj_overrides)
    return {"id": 1, "gate_type": "remediation_github", "item_ref": "42", "adjustments": adjustments}


def _pr_task(**adj_overrides) -> dict:
    adjustments = {
        "title": "Add required status check", "body": "Details.", "repo": "acme/infra",
        "base_branch": "main", "file_path": "ci.yml", "source_event_id": 42, "control_id": "SCM-01",
        "_files": {"ci.yml": "name: CI\non: [push]\n"},
    }
    adjustments.update(adj_overrides)
    return {"id": 2, "gate_type": "remediation_github_pr", "item_ref": "42", "adjustments": adjustments}


def test_execute_remediation_issue_calls_create_issue(monkeypatch):
    fake_db = _FakeDb()
    calls = {}

    def _fake_create_issue(title, body, repo=None, labels=None):
        calls["create_issue"] = (title, body, repo, labels)
        return {"number": 5, "url": "https://github.com/acme/infra/issues/5", "id": 5, "state": "open"}

    def _unexpected_pr_call(*a, **kw):
        raise AssertionError("must not call create_pull_request for an issue task")

    monkeypatch.setattr(ap, "db", fake_db)
    monkeypatch.setattr(ap.github_write_tool, "create_issue", _fake_create_issue)
    monkeypatch.setattr(ap.github_write_tool, "create_pull_request", _unexpected_pr_call)
    monkeypatch.setattr(ap, "mcp_guards", type("M", (), {"audit_log": staticmethod(lambda *a, **kw: None)}))

    ap._execute_remediation(_issue_task())

    assert "create_issue" in calls
    title, body, repo, labels = calls["create_issue"]
    assert title == "Fix it" and repo == "acme/infra"
    assert fake_db.execution_results == [(1, {"number": 5, "url": "https://github.com/acme/infra/issues/5", "id": 5, "state": "open"})]
    assert fake_db.triage_calls[0][0] == 42
    assert "GitHub issue opened" in fake_db.triage_calls[0][3]


def test_execute_remediation_pr_calls_create_pull_request_with_files_and_base_branch(monkeypatch):
    fake_db = _FakeDb()
    calls = {}

    def _fake_create_pr(title, body, files, repo=None, base_branch="main"):
        calls["create_pull_request"] = (title, body, files, repo, base_branch)
        return {"number": 9, "url": "https://github.com/acme/infra/pull/9", "id": 9, "branch": "remediation/x"}

    def _unexpected_issue_call(*a, **kw):
        raise AssertionError("must not call create_issue for a PR task")

    monkeypatch.setattr(ap, "db", fake_db)
    monkeypatch.setattr(ap.github_write_tool, "create_issue", _unexpected_issue_call)
    monkeypatch.setattr(ap.github_write_tool, "create_pull_request", _fake_create_pr)
    monkeypatch.setattr(ap, "mcp_guards", type("M", (), {"audit_log": staticmethod(lambda *a, **kw: None)}))

    ap._execute_remediation(_pr_task())

    assert "create_pull_request" in calls
    title, body, files, repo, base_branch = calls["create_pull_request"]
    assert title == "Add required status check"
    assert files == {"ci.yml": "name: CI\non: [push]\n"}
    assert repo == "acme/infra" and base_branch == "main"
    assert fake_db.execution_results == [(2, {"number": 9, "url": "https://github.com/acme/infra/pull/9", "id": 9, "branch": "remediation/x"})]
    assert "GitHub PR opened" in fake_db.triage_calls[0][3]


def test_execute_remediation_pr_failure_is_persisted_not_swallowed(monkeypatch):
    fake_db = _FakeDb()
    monkeypatch.setattr(ap, "db", fake_db)
    monkeypatch.setattr(ap.github_write_tool, "create_pull_request",
                         lambda *a, **kw: {"error": "422 Client Error: branch already exists"})
    monkeypatch.setattr(ap, "mcp_guards", type("M", (), {"audit_log": staticmethod(lambda *a, **kw: None)}))

    ap._execute_remediation(_pr_task())

    assert fake_db.execution_results[0][1] == {"error": "422 Client Error: branch already exists"}
    assert fake_db.triage_calls == []  # source finding stays open on failure — no auto-resolve


def test_execute_remediation_pr_exception_is_caught_and_recorded(monkeypatch):
    fake_db = _FakeDb()
    monkeypatch.setattr(ap, "db", fake_db)

    def _raise(*a, **kw):
        raise RuntimeError("network timeout")
    monkeypatch.setattr(ap.github_write_tool, "create_pull_request", _raise)
    monkeypatch.setattr(ap, "mcp_guards", type("M", (), {"audit_log": staticmethod(lambda *a, **kw: None)}))

    ap._execute_remediation(_pr_task())

    assert fake_db.execution_results[0][1] == {"error": "network timeout"}
