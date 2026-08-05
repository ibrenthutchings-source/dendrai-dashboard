#!/usr/bin/env python3
"""
Unit tests for claude_client.py's retired-model fallback (_create_message).

The failure this exists to prevent: DENDRAI_CLAUDE_MODEL points at a snapshot
Anthropic has since retired, and every AI-augmented endpoint in the app starts
throwing 404s simultaneously until someone notices and edits the environment.
_create_message catches that specific error and retries once against
FALLBACK_MODEL; these tests exercise that retry in isolation, with a fake
client so no real API call is made.

    pytest test_claude_client.py -v
"""

from __future__ import annotations

import httpx
import pytest

import anthropic
import claude_client as cc


def _not_found(msg="model: not-a-real-model") -> anthropic.NotFoundError:
    resp = httpx.Response(404, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return anthropic.NotFoundError(msg, response=resp, body=None)


def _auth_error(msg="invalid x-api-key") -> anthropic.AuthenticationError:
    resp = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return anthropic.AuthenticationError(msg, response=resp, body=None)


class _FakeClient:
    """Records every model it was asked to create a message for."""
    def __init__(self, fail_models):
        self.fail_models = set(fail_models)
        self.calls = []

    class _messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            model = kwargs["model"]
            self._outer.calls.append(model)
            if model in self._outer.fail_models:
                raise _not_found(f"model: {model}")
            return {"model": model, "ok": True}

    @property
    def messages(self):
        return self._messages(self)


@pytest.fixture(autouse=True)
def _reset_fallback_state():
    """_fallback_state is module-level and mutated by the code under test —
    reset it before and after each test so tests don't leak into each other."""
    cc._fallback_state.update({"active": False, "from_model": None, "at": None, "count": 0})
    yield
    cc._fallback_state.update({"active": False, "from_model": None, "at": None, "count": 0})


def test_create_message_succeeds_normally_without_touching_fallback():
    client = _FakeClient(fail_models=set())
    result = cc._create_message(client, model="claude-sonnet-4-6", max_tokens=10)
    assert result == {"model": "claude-sonnet-4-6", "ok": True}
    assert client.calls == ["claude-sonnet-4-6"]
    assert cc._fallback_state["active"] is False


def test_create_message_retries_once_against_fallback_on_not_found():
    client = _FakeClient(fail_models={"claude-retired-snapshot"})
    result = cc._create_message(client, model="claude-retired-snapshot", max_tokens=10)
    assert result == {"model": cc.FALLBACK_MODEL, "ok": True}
    assert client.calls == ["claude-retired-snapshot", cc.FALLBACK_MODEL]


def test_create_message_updates_fallback_state_on_trigger():
    client = _FakeClient(fail_models={"claude-retired-snapshot"})
    cc._create_message(client, model="claude-retired-snapshot", max_tokens=10)
    assert cc._fallback_state["active"] is True
    assert cc._fallback_state["from_model"] == "claude-retired-snapshot"
    assert cc._fallback_state["count"] == 1
    assert cc._fallback_state["at"] is not None


def test_create_message_raises_when_fallback_is_also_retired():
    client = _FakeClient(fail_models={"claude-retired-snapshot", cc.FALLBACK_MODEL})
    with pytest.raises(anthropic.NotFoundError):
        cc._create_message(client, model="claude-retired-snapshot", max_tokens=10)
    # Only one retry attempted — no infinite loop when the fallback is also dead.
    assert client.calls == ["claude-retired-snapshot", cc.FALLBACK_MODEL]


def test_create_message_does_not_retry_non_not_found_errors():
    """Auth/billing failures must propagate immediately — retrying wastes a
    paid call and the fallback model won't fix a bad API key anyway."""
    class _AuthFailClient(_FakeClient):
        class _messages(_FakeClient._messages):
            def create(self, **kwargs):
                self._outer.calls.append(kwargs["model"])
                raise _auth_error()

    client = _AuthFailClient(fail_models=set())
    with pytest.raises(anthropic.AuthenticationError):
        cc._create_message(client, model="claude-sonnet-4-6", max_tokens=10)
    assert client.calls == ["claude-sonnet-4-6"]  # no retry attempt


def test_get_model_status_shape():
    status = cc.get_model_status()
    assert set(status.keys()) == {
        "configured_model", "fallback_model", "client_available",
        "fallback_active", "fallback_from_model", "fallback_last_at",
        "fallback_trigger_count",
    }
    assert status["configured_model"] == cc.MODEL
    assert status["fallback_model"] == cc.FALLBACK_MODEL
    assert status["fallback_active"] is False


def test_get_model_status_reflects_a_fallback_trigger():
    client = _FakeClient(fail_models={"claude-retired-snapshot"})
    cc._create_message(client, model="claude-retired-snapshot", max_tokens=10)
    status = cc.get_model_status()
    assert status["fallback_active"] is True
    assert status["fallback_from_model"] == "claude-retired-snapshot"
    assert status["fallback_trigger_count"] == 1
