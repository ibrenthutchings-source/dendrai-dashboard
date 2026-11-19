#!/usr/bin/env python3
"""
Endpoint-level tests for approvals_endpoints.py's admin-override path.

Regression coverage for a real dead end: an approval_tasks item routed to
a manager_id whose account nobody actually uses (a stale/misconfigured org
chart, or a small-team deployment with no real second reviewer) sat
'submitted' forever — reachable by no one, since /approvals/review 403'd
anyone but the exact assigned manager_id, and /approvals/inbox only ever
showed items assigned to the caller. Fixed by letting an admin decide ANY
submitted item (recorded as an explicit override in review_comment, never
a forged identity) and see every pending item org-wide via
?all_pending=true.

    pytest test_approvals_endpoints.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import approvals_endpoints as ae
import auth_endpoints


def _client_as(user):
    app = FastAPI()
    app.include_router(ae.router)
    app.dependency_overrides[auth_endpoints.get_current_user] = lambda: user
    return TestClient(app)


_MANAGER = {"id": 10, "username": "manager", "display_name": "Real Manager", "role": "user"}
_ADMIN = {"id": 1, "username": "admin", "display_name": "Administrator", "role": "admin"}
_RANDOM_USER = {"id": 99, "username": "someone", "display_name": "Someone Else", "role": "user"}

_TASK = {
    "id": 5, "run_id": 42, "gate_type": "risk", "item_ref": "R-01", "item_label": "Revenue Recognition",
    "manager_id": 10, "manager_name": "Real Manager", "status": "submitted",
    "disposition": "adjusted", "adjustments": {"score": 8}, "rationale": "widened window",
    "prepared_by_name": "Preparer", "execution_result": None,
}


class TestReviewItemAuthorization:
    def test_assigned_manager_can_review_without_an_override_note(self, monkeypatch):
        monkeypatch.setattr(ae.db, "is_available", lambda: True)
        monkeypatch.setattr(ae.db, "get_approval_task", lambda task_id: dict(_TASK))
        captured = {}
        def _fake_review(**kw):
            captured.update(kw)
            return {"id": 5, "run_id": 42, "gate_type": "risk", "item_ref": "R-01",
                    "status": "manager_approved", "disposition": "adjusted", "adjustments": {"score": 8}}
        monkeypatch.setattr(ae.db, "review_approval_task", lambda **kw: _fake_review(**kw))
        client = _client_as(_MANAGER)

        r = client.post("/approvals/review", json={"task_id": 5, "decision": "approved", "comment": "looks right"})

        assert r.status_code == 200
        assert captured["comment"] == "looks right"  # not rewritten — this IS the assigned manager

    def test_non_manager_non_admin_is_rejected(self, monkeypatch):
        monkeypatch.setattr(ae.db, "is_available", lambda: True)
        monkeypatch.setattr(ae.db, "get_approval_task", lambda task_id: dict(_TASK))
        client = _client_as(_RANDOM_USER)

        r = client.post("/approvals/review", json={"task_id": 5, "decision": "approved"})

        assert r.status_code == 403

    def test_admin_can_override_and_the_comment_records_it(self, monkeypatch):
        monkeypatch.setattr(ae.db, "is_available", lambda: True)
        monkeypatch.setattr(ae.db, "get_approval_task", lambda task_id: dict(_TASK))
        captured = {}
        def _fake_review(**kw):
            captured.update(kw)
            return {"id": 5, "run_id": 42, "gate_type": "risk", "item_ref": "R-01",
                    "status": "manager_approved", "disposition": "adjusted", "adjustments": {"score": 8}}
        monkeypatch.setattr(ae.db, "review_approval_task", lambda **kw: _fake_review(**kw))
        client = _client_as(_ADMIN)

        r = client.post("/approvals/review", json={"task_id": 5, "decision": "approved", "comment": "clearing a stale queue"})

        assert r.status_code == 200
        assert "Admin override" in captured["comment"]
        assert "Real Manager" in captured["comment"]
        assert "clearing a stale queue" in captured["comment"]
        assert captured["reviewer_id"] == _ADMIN["id"]  # recorded as the real reviewer, never impersonating the manager

    def test_admin_override_with_no_comment_still_records_the_override(self, monkeypatch):
        monkeypatch.setattr(ae.db, "is_available", lambda: True)
        monkeypatch.setattr(ae.db, "get_approval_task", lambda task_id: dict(_TASK))
        captured = {}
        def _fake_review(**kw):
            captured.update(kw)
            return {"id": 5, "status": "rejected"}
        monkeypatch.setattr(ae.db, "review_approval_task", lambda **kw: _fake_review(**kw))
        client = _client_as(_ADMIN)

        r = client.post("/approvals/review", json={"task_id": 5, "decision": "rejected"})

        assert r.status_code == 200
        assert "Admin override" in captured["comment"]

    def test_not_awaiting_review_returns_409_even_for_admin(self, monkeypatch):
        monkeypatch.setattr(ae.db, "is_available", lambda: True)
        monkeypatch.setattr(ae.db, "get_approval_task", lambda task_id: {**_TASK, "status": "manager_approved"})
        client = _client_as(_ADMIN)

        r = client.post("/approvals/review", json={"task_id": 5, "decision": "approved"})

        assert r.status_code == 409


class TestGetInbox:
    def test_all_pending_true_ignored_for_non_admin(self, monkeypatch):
        monkeypatch.setattr(ae.db, "is_available", lambda: True)
        captured = {}
        def _fake_inbox(manager_id, all_pending=False):
            captured.update(manager_id=manager_id, all_pending=all_pending)
            return []
        monkeypatch.setattr(ae.db, "get_approval_inbox", _fake_inbox)
        client = _client_as(_MANAGER)

        r = client.get("/approvals/inbox", params={"all_pending": "true"})

        assert r.status_code == 200
        assert captured == {"manager_id": _MANAGER["id"], "all_pending": False}

    def test_all_pending_true_honored_for_admin(self, monkeypatch):
        monkeypatch.setattr(ae.db, "is_available", lambda: True)
        captured = {}
        def _fake_inbox(manager_id, all_pending=False):
            captured.update(manager_id=manager_id, all_pending=all_pending)
            return []
        monkeypatch.setattr(ae.db, "get_approval_inbox", _fake_inbox)
        client = _client_as(_ADMIN)

        r = client.get("/approvals/inbox", params={"all_pending": "true"})

        assert r.status_code == 200
        assert captured == {"manager_id": _ADMIN["id"], "all_pending": True}

    def test_default_stays_personal_inbox_for_admin_too(self, monkeypatch):
        monkeypatch.setattr(ae.db, "is_available", lambda: True)
        captured = {}
        def _fake_inbox(manager_id, all_pending=False):
            captured.update(manager_id=manager_id, all_pending=all_pending)
            return []
        monkeypatch.setattr(ae.db, "get_approval_inbox", _fake_inbox)
        client = _client_as(_ADMIN)

        r = client.get("/approvals/inbox")

        assert r.status_code == 200
        assert captured["all_pending"] is False
