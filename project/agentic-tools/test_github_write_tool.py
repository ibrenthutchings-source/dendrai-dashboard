#!/usr/bin/env python3
"""
Unit tests for github_write_tool.py — the first write-capable connector
primitive in this codebase. HTTP is mocked throughout (no real GitHub calls);
these tests verify the request shape/sequence and error handling, same
reasoning as test_github_webhook_listener.py verifying HMAC/DB behavior
without a real webhook delivery.

    pytest test_github_write_tool.py -v
"""
from __future__ import annotations

import github_write_tool as gw


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeRequests:
    """Records every call in order; responses queued per (method, url substring)."""
    def __init__(self):
        self.calls = []
        self._get_responses = {}
        self._post_responses = {}

    def queue_get(self, url_substr, response):
        self._get_responses[url_substr] = response

    def queue_post(self, url_substr, response):
        self._post_responses[url_substr] = response

    def get(self, url, headers=None, timeout=None, **kw):
        self.calls.append(("GET", url))
        for substr, resp in self._get_responses.items():
            if substr in url:
                return resp
        raise AssertionError(f"No queued GET response for {url}")

    def post(self, url, headers=None, json=None, timeout=None, **kw):
        self.calls.append(("POST", url, json))
        for substr, resp in self._post_responses.items():
            if substr in url:
                return resp
        raise AssertionError(f"No queued POST response for {url}")


def _configure(monkeypatch, fake, token="ghp_test", repo="acme/infra"):
    monkeypatch.setattr(gw, "requests", fake)
    monkeypatch.setattr(gw, "_HAS_REQUESTS", True)
    monkeypatch.setenv("GITHUB_WRITE_TOKEN", token)
    if repo is not None:
        monkeypatch.setenv("GITHUB_REMEDIATION_REPO", repo)
    else:
        monkeypatch.delenv("GITHUB_REMEDIATION_REPO", raising=False)


# ── create_issue ─────────────────────────────────────────────────────────────

def test_create_issue_success(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake)
    fake.queue_post("/repos/acme/infra/issues",
                     _FakeResponse({"number": 42, "html_url": "https://github.com/acme/infra/issues/42",
                                    "id": 999, "state": "open"}))

    result = gw.create_issue("SoD conflict on JE-1234", "Preparer and approver were the same person.",
                              labels=["dendrai-remediation"])

    assert result == {"number": 42, "url": "https://github.com/acme/infra/issues/42", "id": 999, "state": "open"}
    method, url, body = fake.calls[0]
    assert method == "POST" and url.endswith("/repos/acme/infra/issues")
    assert body == {"title": "SoD conflict on JE-1234",
                     "body": "Preparer and approver were the same person.",
                     "labels": ["dendrai-remediation"]}


def test_create_issue_no_token_configured(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake, token="")
    result = gw.create_issue("title", "body")
    assert "error" in result
    assert fake.calls == []


def test_create_issue_no_repo_configured(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake, repo=None)
    result = gw.create_issue("title", "body")
    assert "error" in result
    assert fake.calls == []


def test_create_issue_explicit_repo_overrides_default(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake, repo="acme/default-repo")
    fake.queue_post("/repos/acme/other-repo/issues",
                     _FakeResponse({"number": 1, "html_url": "https://github.com/acme/other-repo/issues/1",
                                    "id": 1, "state": "open"}))
    result = gw.create_issue("title", "body", repo="acme/other-repo")
    assert result["number"] == 1
    assert fake.calls[0][1] == f"{gw._API}/repos/acme/other-repo/issues"


def test_create_issue_http_error_returns_error_dict_not_raise(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake)
    fake.queue_post("/repos/acme/infra/issues", _FakeResponse({"message": "Bad credentials"}, status_code=401))
    result = gw.create_issue("title", "body")
    assert "error" in result


# ── create_pull_request ───────────────────────────────────────────────────────

def test_create_pull_request_full_git_data_api_sequence(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake)
    fake.queue_get("/git/ref/heads/main", _FakeResponse({"object": {"sha": "base-sha"}}))
    fake.queue_get("/git/commits/base-sha", _FakeResponse({"tree": {"sha": "base-tree-sha"}}))
    fake.queue_post("/git/blobs", _FakeResponse({"sha": "blob-sha"}))
    fake.queue_post("/git/trees", _FakeResponse({"sha": "new-tree-sha"}))
    fake.queue_post("/git/commits", _FakeResponse({"sha": "new-commit-sha"}))
    fake.queue_post("/git/refs", _FakeResponse({"ref": "refs/heads/remediation/abc123"}))
    fake.queue_post("/pulls", _FakeResponse({"number": 7, "html_url": "https://github.com/acme/infra/pull/7", "id": 555}))

    result = gw.create_pull_request(
        "Add compensating control note", "See finding JE-5678.",
        files={"remediations/JE-5678.md": "# Compensating control\n"},
        new_branch="remediation/abc123",
    )

    assert result == {"number": 7, "url": "https://github.com/acme/infra/pull/7", "id": 555, "branch": "remediation/abc123"}
    methods = [c[0] for c in fake.calls]
    assert methods == ["GET", "GET", "POST", "POST", "POST", "POST", "POST"]
    # tree commit references the blob it just created, and the ref points at the new commit.
    tree_call = next(c for c in fake.calls if c[0] == "POST" and "/git/trees" in c[1])
    assert tree_call[2]["tree"][0]["sha"] == "blob-sha"
    ref_call = next(c for c in fake.calls if c[0] == "POST" and "/git/refs" in c[1])
    assert ref_call[2]["sha"] == "new-commit-sha"


def test_create_pull_request_requires_at_least_one_file(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake)
    result = gw.create_pull_request("title", "body", files={})
    assert "error" in result
    assert fake.calls == []


def test_create_pull_request_generates_branch_name_when_not_given(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake)
    fake.queue_get("/git/ref/heads/main", _FakeResponse({"object": {"sha": "base-sha"}}))
    fake.queue_get("/git/commits/base-sha", _FakeResponse({"tree": {"sha": "base-tree-sha"}}))
    fake.queue_post("/git/blobs", _FakeResponse({"sha": "blob-sha"}))
    fake.queue_post("/git/trees", _FakeResponse({"sha": "new-tree-sha"}))
    fake.queue_post("/git/commits", _FakeResponse({"sha": "new-commit-sha"}))
    fake.queue_post("/git/refs", _FakeResponse({"ref": "refs/heads/x"}))
    fake.queue_post("/pulls", _FakeResponse({"number": 1, "html_url": "https://x", "id": 1}))

    result = gw.create_pull_request("title", "body", files={"a.txt": "hi"})
    assert result["branch"].startswith("remediation/")


# ── get_file_content ─────────────────────────────────────────────────────────

def _b64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_get_file_content_success_decodes_base64(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake)
    fake.queue_get("/repos/acme/infra/contents/ci.yml",
                    _FakeResponse({"encoding": "base64", "content": _b64("name: CI\n"), "sha": "file-sha"}))

    result = gw.get_file_content("ci.yml")

    assert result == {"content": "name: CI\n", "sha": "file-sha"}
    method, url = fake.calls[0]
    assert method == "GET" and url == f"{gw._API}/repos/acme/infra/contents/ci.yml"


def test_get_file_content_no_token_configured(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake, token="")
    result = gw.get_file_content("ci.yml")
    assert "error" in result
    assert fake.calls == []


def test_get_file_content_no_repo_configured(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake, repo=None)
    result = gw.get_file_content("ci.yml")
    assert "error" in result
    assert fake.calls == []


def test_get_file_content_rejects_binary_non_utf8(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake)
    import base64
    binary_b64 = base64.b64encode(b"\xff\xfe\x00\x01").decode("ascii")
    fake.queue_get("/repos/acme/infra/contents/logo.png",
                    _FakeResponse({"encoding": "base64", "content": binary_b64, "sha": "x"}))
    result = gw.get_file_content("logo.png")
    assert "error" in result
    assert "utf-8" in result["error"].lower()


def test_get_file_content_unexpected_shape_when_not_a_single_file(monkeypatch):
    """A directory listing (or any response with no base64 content) must not
    be treated as file text — same defensive shape check as the rest of this
    module's HTTP handling."""
    fake = _FakeRequests()
    _configure(monkeypatch, fake)
    fake.queue_get("/repos/acme/infra/contents/somedir",
                    _FakeResponse([{"name": "a.txt"}, {"name": "b.txt"}]))
    result = gw.get_file_content("somedir")
    assert "error" in result


def test_get_file_content_http_error_returns_error_dict_not_raise(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake)
    fake.queue_get("/repos/acme/infra/contents/missing.txt", _FakeResponse({"message": "Not Found"}, status_code=404))
    result = gw.get_file_content("missing.txt")
    assert "error" in result


# ── test_connection ────────────────────────────────────────────────────────────

def test_test_connection_success_when_push_access(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake)
    fake.queue_get("/repos/acme/infra", _FakeResponse({"permissions": {"push": True, "pull": True}}))
    ok, msg = gw.test_connection()
    assert ok is True
    assert "push access" in msg.lower()


def test_test_connection_fails_when_read_only_token(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake)
    fake.queue_get("/repos/acme/infra", _FakeResponse({"permissions": {"push": False, "pull": True}}))
    ok, msg = gw.test_connection()
    assert ok is False
    assert "lacks push access" in msg


def test_test_connection_not_configured(monkeypatch):
    fake = _FakeRequests()
    _configure(monkeypatch, fake, token="")
    ok, msg = gw.test_connection()
    assert ok is False
