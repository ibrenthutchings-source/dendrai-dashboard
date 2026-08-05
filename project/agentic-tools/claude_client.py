#!/usr/bin/env python3
"""
Shared Claude API client for the Dendrai Intelligenza agentic layer.

This is the single place where the app actually calls a language model. Every
AI-augmented endpoint (HITL gate recommendations, narrative analysis, persona
briefs, the tool-use investigation agent) goes through here so that model
selection, adaptive thinking, prompt caching, structured-output handling,
error handling, and token-cost accounting are consistent.

Activation:
    Set ANTHROPIC_API_KEY in the environment (or project/agentic-tools/.env).
    Without it, is_available() returns False and callers degrade gracefully.

Model policy:
    Defaults to claude-sonnet-4-6. Escalate to opus only when truly necessary.
    Override per call via `model`/`effort`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

try:
    import anthropic
    _HAS_SDK = True
except ImportError:  # pragma: no cover
    _HAS_SDK = False

# Configured default; escalate to Sonnet or Opus per call as needed via `model=`.
MODEL = os.environ.get("DENDRAI_CLAUDE_MODEL", "claude-sonnet-4-6")

# Retried against once, and only once, if MODEL comes back 404 NotFoundError
# (Anthropic's signal for a retired/unrecognized model id) — see _create_message.
# Deliberately a different snapshot than MODEL's own default so a stale
# DENDRAI_CLAUDE_MODEL degrades to "slower to update" rather than "every
# AI-augmented endpoint breaks at once." Override independently in case this
# one is eventually retired too.
FALLBACK_MODEL = os.environ.get("DENDRAI_CLAUDE_FALLBACK_MODEL", "claude-sonnet-4-5")

_client: Optional["anthropic.Anthropic"] = None
_checked = False

# Visibility for GET /admin/model-config — set the moment a retired-model
# fallback actually fires, so the config screen can surface it rather than
# this only showing up as a line in the server log.
_fallback_state: dict = {"active": False, "from_model": None, "at": None, "count": 0}

# Optional token-cost accounting — wired to the existing token_cost_tool + db.
try:
    import token_cost_tool
    _HAS_COST = True
except Exception:  # pragma: no cover
    _HAS_COST = False


# ─────────────────────────────────────────────────────────────────────────────
# Client lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def get_client() -> Optional["anthropic.Anthropic"]:
    """Return a cached Anthropic client, or None when unavailable."""
    global _client, _checked
    if _checked:
        return _client
    _checked = True
    if not _HAS_SDK:
        logger.info("anthropic SDK not installed — AI features disabled")
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("ANTHROPIC_API_KEY not set — AI features disabled")
        return None
    try:
        _client = anthropic.Anthropic()
    except Exception as exc:  # pragma: no cover
        logger.error("Anthropic client init failed: %s", exc)
        _client = None
    return _client


def is_available() -> bool:
    """True when a model call can actually be made."""
    return get_client() is not None


def get_model_status() -> dict:
    """Snapshot for GET /admin/model-config: what's configured, and whether the
    retired-model fallback has actually fired since this process started."""
    return {
        "configured_model": MODEL,
        "fallback_model": FALLBACK_MODEL,
        "client_available": is_available(),
        "fallback_active": _fallback_state["active"],
        "fallback_from_model": _fallback_state["from_model"],
        "fallback_last_at": _fallback_state["at"],
        "fallback_trigger_count": _fallback_state["count"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _system_blocks(system: str) -> list:
    """Cache the (stable) system prompt prefix so repeated calls read the cache."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _create_message(client: "anthropic.Anthropic", **kwargs) -> Any:
    """client.messages.create wrapper that fails soft on a retired model.

    Anthropic signals a retired/unrecognized model id with a 404
    NotFoundError — a distinct, identifiable error class, unlike auth or
    billing failures (which must propagate immediately; retrying those wastes
    a paid call for nothing). On that specific error, log loudly and retry
    once against FALLBACK_MODEL, so a stale DENDRAI_CLAUDE_MODEL degrades to
    "one log line and a slightly different model" instead of every
    AI-augmented endpoint in the app erroring simultaneously until someone
    notices and edits the environment.
    """
    model = kwargs.get("model")
    try:
        return client.messages.create(**kwargs)
    except anthropic.NotFoundError:
        if model == FALLBACK_MODEL:
            raise  # the fallback itself is retired too — nothing left to try
        logger.error(
            "Model '%s' was rejected as not found (likely retired by Anthropic) — "
            "retrying this call against fallback model '%s'. Update "
            "DENDRAI_CLAUDE_MODEL to a current snapshot.",
            model, FALLBACK_MODEL,
        )
        _fallback_state["active"] = True
        _fallback_state["from_model"] = model
        _fallback_state["at"] = time.time()
        _fallback_state["count"] += 1
        kwargs["model"] = FALLBACK_MODEL
        return client.messages.create(**kwargs)


def _thinking_kwargs(model: str, effort: str) -> dict:
    """Return thinking/output_config kwargs only for models that support adaptive thinking.

    Haiku (4.5 and earlier) does not support thinking — passing it returns a 400.
    Sonnet (4.x), Opus (4.x), and Fable (5) do support it.
    """
    if any(name in model for name in ("sonnet", "opus", "fable")):
        return {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
    return {}


def _text_of(message: Any) -> str:
    """Concatenate text blocks from a Messages response."""
    out = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            out.append(block.text)
    return "".join(out)


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> Any:
    """Best-effort JSON extraction from a model response."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_FENCE.search(text)
    if m:
        return json.loads(m.group(1))
    # Fall back to the first balanced {...} / [...] span.
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start >= 0:
        depth, opener = 0, text[start]
        closer = "}" if opener == "{" else "]"
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("no JSON object found in model response")


def record_usage(in_tok: int, out_tok: int, cache_read: int, cache_write: int,
                  label: str, model: str, caller: Optional[dict] = None) -> None:
    """
    Best-effort token-cost accounting into the token_usage_* tables, from
    already-extracted usage numbers. Public so callers that don't go through
    complete_json/complete_text/run_tool_loop — e.g. chat_endpoint.py, which
    accumulates usage across a manual streaming tool-use loop — can still
    record what they spent.

    `caller`, when given (the authenticated session user — {"id", "username"}),
    attributes the call for the Token Usage screen; omitted/unknown callers
    are recorded with user_id/username left NULL ("Unknown" in the UI).
    """
    if not _HAS_COST:
        return
    try:
        import db
        if not db.is_available():
            return
        cost_usd = None
        try:
            cost_usd = token_cost_tool.calculate_cost(
                model, in_tok, out_tok, cache_read, cache_write,
            ).total_cost_usd
        except Exception:
            pass  # unknown model in pricing catalog — record tokens without cost
        session_id = db.upsert_token_session("dendrai-agentic")
        if session_id:
            from datetime import datetime, timezone
            db.save_token_call(
                session_id,
                {
                    "timestamp": datetime.now(timezone.utc),
                    "model": model, "label": label,
                    "input_tokens": in_tok, "output_tokens": out_tok,
                    "cache_read_tokens": cache_read, "cache_write_tokens": cache_write,
                    "cost_usd": cost_usd,
                    "user_id": (caller or {}).get("id"),
                    "username": (caller or {}).get("username"),
                },
                {},  # running totals are recomputed elsewhere; per-call row is enough
            )
    except Exception as exc:  # never let accounting break a request
        logger.debug("cost accounting skipped: %s", exc)


def _record_cost(message: Any, label: str, model: str, caller: Optional[dict] = None) -> None:
    """Extracts usage from an SDK message object, then defers to record_usage().

    Prefers message.model (what the API actually served) over the requested
    `model` — the two diverge when _create_message silently retried against
    FALLBACK_MODEL, and cost/token accounting should reflect what really ran.
    """
    usage = getattr(message, "usage", None)
    if not usage:
        return
    actual_model = getattr(message, "model", None) or model
    record_usage(
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
        getattr(usage, "cache_read_input_tokens", 0) or 0,
        getattr(usage, "cache_creation_input_tokens", 0) or 0,
        label, actual_model, caller,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public: structured + free-form completions
# ─────────────────────────────────────────────────────────────────────────────

def complete_json(
    system: str,
    user: str,
    schema: Optional[dict] = None,
    *,
    label: str = "json",
    effort: str = "high",
    max_tokens: int = 8000,
    model: Optional[str] = None,
    caller: Optional[dict] = None,
) -> dict:
    """
    Run a single structured-output completion and return parsed JSON.

    Uses adaptive thinking + prompt caching. When `schema` is provided we ask the
    API to constrain the output to it; if the running SDK/model rejects the
    constraint we fall back to instructing JSON in the prompt and parsing it out.
    """
    client = get_client()
    if client is None:
        raise RuntimeError("Claude client unavailable (set ANTHROPIC_API_KEY)")
    model = model or MODEL

    base = dict(
        model=model,
        max_tokens=max_tokens,
        **_thinking_kwargs(model, effort),
        system=_system_blocks(system),
        messages=[{"role": "user", "content": user}],
    )

    def _call(extra: dict) -> Any:
        kwargs = dict(base)
        oc = dict(kwargs["output_config"])
        oc.update(extra)
        kwargs["output_config"] = oc
        return _create_message(client, **kwargs)

    message = None
    if schema is not None:
        try:
            message = _call({"format": {"type": "json_schema", "schema": schema}})
        except Exception as exc:
            # Re-raise billing / auth errors immediately — retrying wastes a credit call.
            exc_str = str(exc)
            if "credit balance" in exc_str or "insufficient_quota" in exc_str or "invalid_api_key" in exc_str:
                raise
            logger.info("structured-output constraint unavailable (%s); using prompt-guided JSON", exc)
    if message is None:
        guided = dict(base)
        guided["messages"] = [{
            "role": "user",
            "content": user + "\n\nRespond with a single valid JSON object only. No prose, no code fences.",
        }]
        message = _create_message(client, **guided)

    _record_cost(message, label, model, caller)
    return _extract_json(_text_of(message))


def complete_text(
    system: str,
    user: str,
    *,
    label: str = "text",
    effort: str = "high",
    max_tokens: int = 8000,
    model: Optional[str] = None,
    caller: Optional[dict] = None,
) -> str:
    """Run a single free-form completion and return the response text."""
    client = get_client()
    if client is None:
        raise RuntimeError("Claude client unavailable (set ANTHROPIC_API_KEY)")
    model = model or MODEL
    message = _create_message(
        client,
        model=model,
        max_tokens=max_tokens,
        **_thinking_kwargs(model, effort),
        system=_system_blocks(system),
        messages=[{"role": "user", "content": user}],
    )
    _record_cost(message, label, model, caller)
    return _text_of(message)


# ─────────────────────────────────────────────────────────────────────────────
# Public: manual tool-use loop (the investigation agent)
# ─────────────────────────────────────────────────────────────────────────────

def run_tool_loop(
    system: str,
    user: str,
    tools: list[dict],
    tool_impls: dict[str, Callable[[dict], Any]],
    *,
    label: str = "agent",
    effort: str = "high",
    max_tokens: int = 8000,
    max_iterations: int = 12,
    model: Optional[str] = None,
    caller: Optional[dict] = None,
) -> dict:
    """
    Drive a manual agentic loop. Claude decides which of `tools` to call; we
    execute the matching `tool_impls` and feed results back until it stops.

    Returns: {"final_text", "tool_calls": [...], "iterations", "stopped"}.
    `tools` schemas are stable and cached alongside the system prompt.
    """
    client = get_client()
    if client is None:
        raise RuntimeError("Claude client unavailable (set ANTHROPIC_API_KEY)")
    model = model or MODEL

    # Cache the (stable) tool list by marking the last tool definition.
    cached_tools = [dict(t) for t in tools]
    if cached_tools:
        cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}

    messages: list[dict] = [{"role": "user", "content": user}]
    tool_calls: list[dict] = []
    iterations = 0
    stopped = "max_iterations"

    while iterations < max_iterations:
        iterations += 1
        message = _create_message(
            client,
            model=model,
            max_tokens=max_tokens,
            **_thinking_kwargs(model, effort),
            system=_system_blocks(system),
            tools=cached_tools,
            messages=messages,
        )
        _record_cost(message, f"{label}:{iterations}", model, caller)

        if message.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": message.content})
            continue
        if message.stop_reason != "tool_use":
            stopped = message.stop_reason or "end_turn"
            messages.append({"role": "assistant", "content": message.content})
            break

        messages.append({"role": "assistant", "content": message.content})
        results = []
        for block in message.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            name, tool_input = block.name, block.input or {}
            impl = tool_impls.get(name)
            try:
                if impl is None:
                    raise KeyError(f"unknown tool {name}")
                output = impl(tool_input)
                is_error = False
            except Exception as exc:
                output = {"error": str(exc)}
                is_error = True
            tool_calls.append({"tool": name, "input": tool_input, "is_error": is_error})
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(output, default=str)[:60_000],
                "is_error": is_error,
            })
        # Cache the conversation history up to this point so the next iteration
        # does not re-pay for the full transcript prefix.
        if results:
            results[-1] = {**results[-1], "cache_control": {"type": "ephemeral"}}
        messages.append({"role": "user", "content": results})

    # Recover the final assistant text from the transcript.
    final_text = ""
    for m in reversed(messages):
        if m["role"] == "assistant":
            content = m["content"]
            if isinstance(content, list):
                final_text = "".join(
                    getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text"
                )
            elif isinstance(content, str):
                final_text = content
            if final_text:
                break

    return {
        "final_text": final_text,
        "tool_calls": tool_calls,
        "iterations": iterations,
        "stopped": stopped,
    }


def run_tool_loop_streaming(
    system: str,
    user: str,
    tools: list[dict],
    tool_impls: dict[str, Callable[[dict], Any]],
    *,
    label: str = "agent",
    effort: str = "high",
    max_tokens: int = 8000,
    max_iterations: int = 12,
    model: Optional[str] = None,
    caller: Optional[dict] = None,
):
    """
    Like run_tool_loop but yields SSE-style JSON events as the agent progresses.

    Yield types:
      {"type": "tool_call",   "tool": name, "input": {...}, "iteration": n}
      {"type": "tool_result", "tool": name, "result_preview": str, "is_error": bool, "iteration": n}
      {"type": "done",        "final_text": str, "iterations": n, "stopped": str}
      {"type": "error",       "message": str}
    """
    import json as _json
    client = get_client()
    if client is None:
        yield {"type": "error", "message": "Claude client unavailable (set ANTHROPIC_API_KEY)"}
        return
    model = model or MODEL

    cached_tools = [dict(t) for t in tools]
    if cached_tools:
        cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}

    messages: list[dict] = [{"role": "user", "content": user}]
    tool_calls: list[dict] = []
    iterations = 0
    stopped = "max_iterations"

    while iterations < max_iterations:
        iterations += 1
        try:
            message = _create_message(
                client,
                model=model,
                max_tokens=max_tokens,
                **_thinking_kwargs(model, effort),
                system=_system_blocks(system),
                tools=cached_tools,
                messages=messages,
            )
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return
        _record_cost(message, f"{label}:{iterations}", model, caller)

        if message.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": message.content})
            continue
        if message.stop_reason != "tool_use":
            stopped = message.stop_reason or "end_turn"
            messages.append({"role": "assistant", "content": message.content})
            break

        messages.append({"role": "assistant", "content": message.content})
        results = []
        for block in message.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            name, tool_input = block.name, block.input or {}
            yield {"type": "tool_call", "tool": name, "input": tool_input, "iteration": iterations}
            impl = tool_impls.get(name)
            try:
                if impl is None:
                    raise KeyError(f"unknown tool {name}")
                output = impl(tool_input)
                is_error = False
            except Exception as exc:
                output = {"error": str(exc)}
                is_error = True
            tool_calls.append({"tool": name, "input": tool_input, "is_error": is_error})
            preview = _json.dumps(output, default=str)[:300]
            yield {"type": "tool_result", "tool": name, "result_preview": preview,
                   "is_error": is_error, "iteration": iterations}
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": _json.dumps(output, default=str)[:60_000],
                "is_error": is_error,
            })
        # Cache the conversation history up to this point so the next iteration
        # does not re-pay for the full transcript prefix.
        if results:
            results[-1] = {**results[-1], "cache_control": {"type": "ephemeral"}}
        messages.append({"role": "user", "content": results})

    # Recover final text
    final_text = ""
    for m in reversed(messages):
        if m["role"] == "assistant":
            content = m["content"]
            if isinstance(content, list):
                final_text = "".join(
                    getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text"
                )
            elif isinstance(content, str):
                final_text = content
            if final_text:
                break

    yield {"type": "done", "final_text": final_text, "iterations": iterations, "stopped": stopped}
