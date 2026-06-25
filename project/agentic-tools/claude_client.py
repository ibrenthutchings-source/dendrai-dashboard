#!/usr/bin/env python3
"""
Shared Claude API client for the Dendrai Risk Loop agentic layer.

This is the single place where the app actually calls a language model. Every
AI-augmented endpoint (HITL gate recommendations, narrative analysis, persona
briefs, the tool-use investigation agent) goes through here so that model
selection, adaptive thinking, prompt caching, structured-output handling,
error handling, and token-cost accounting are consistent.

Activation:
    Set ANTHROPIC_API_KEY in the environment (or project/agentic-tools/.env).
    Without it, is_available() returns False and callers degrade gracefully.

Model policy:
    Defaults to claude-opus-4-8 with adaptive thinking — audit reasoning is
    multi-step and correctness-sensitive. Override per call via `model`/`effort`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

try:
    import anthropic
    _HAS_SDK = True
except ImportError:  # pragma: no cover
    _HAS_SDK = False

# Audit reasoning is correctness-sensitive: default to the most capable Opus tier.
MODEL = os.environ.get("DENDRAI_CLAUDE_MODEL", "claude-opus-4-8")

_client: Optional["anthropic.Anthropic"] = None
_checked = False

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


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _system_blocks(system: str) -> list:
    """Cache the (stable) system prompt prefix so repeated calls read the cache."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


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


def _record_cost(message: Any, label: str, model: str) -> None:
    """Best-effort token-cost accounting into the token_usage_* tables."""
    if not _HAS_COST:
        return
    try:
        import db
        usage = getattr(message, "usage", None)
        if not usage or not db.is_available():
            return
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
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
                },
                {},  # running totals are recomputed elsewhere; per-call row is enough
            )
    except Exception as exc:  # never let accounting break a request
        logger.debug("cost accounting skipped: %s", exc)


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
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        system=_system_blocks(system),
        messages=[{"role": "user", "content": user}],
    )

    def _call(extra: dict) -> Any:
        kwargs = dict(base)
        oc = dict(kwargs["output_config"])
        oc.update(extra)
        kwargs["output_config"] = oc
        return client.messages.create(**kwargs)

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
        message = client.messages.create(**guided)

    _record_cost(message, label, model)
    return _extract_json(_text_of(message))


def complete_text(
    system: str,
    user: str,
    *,
    label: str = "text",
    effort: str = "high",
    max_tokens: int = 8000,
    model: Optional[str] = None,
) -> str:
    """Run a single free-form completion and return the response text."""
    client = get_client()
    if client is None:
        raise RuntimeError("Claude client unavailable (set ANTHROPIC_API_KEY)")
    model = model or MODEL
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        system=_system_blocks(system),
        messages=[{"role": "user", "content": user}],
    )
    _record_cost(message, label, model)
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
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            system=_system_blocks(system),
            tools=cached_tools,
            messages=messages,
        )
        _record_cost(message, f"{label}:{iterations}", model)

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
            message = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                system=_system_blocks(system),
                tools=cached_tools,
                messages=messages,
            )
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return
        _record_cost(message, f"{label}:{iterations}", model)

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
