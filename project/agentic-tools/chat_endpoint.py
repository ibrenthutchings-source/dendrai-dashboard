#!/usr/bin/env python3
"""
AI Chat endpoint for the Dendrai dashboard.

Provides a streaming conversational interface with live MCP tool access.
Claude uses the full tool-use loop (EDGAR, FRED, RSS, quant models).
Gemini uses a conversational mode with dashboard context injected as system context.

Router prefix: /ai (registered via app.include_router)
    POST /ai/chat/stream   Server-Sent Events streaming chat
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import claude_client

logger = logging.getLogger(__name__)
router = APIRouter()

_MODEL_CHAT = "claude-haiku-4-5-20251001"


# ── Request model ──────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []
    ticker: Optional[str] = None
    industry: Optional[str] = None
    provider: str = "claude"            # "claude" | "gemini"
    gemini_api_key: Optional[str] = None
    risks: List[Dict[str, Any]] = []    # current risk register (for context)
    loop_stats: Dict[str, Any] = {}


# ── System prompt ──────────────────────────────────────────────────────────────

_BASE_SYSTEM = """You are an AI assistant embedded in the Dendrai Risk Loop — an AI-powered \
audit and risk governance platform. You help users understand financial data, risk factors, \
macro indicators, audit findings, and governance insights.

You have access to live data tools:
- get_financials: SEC EDGAR XBRL financial summary (revenue, margins, R&D, cash)
- get_risk_factors: Item 1A risk factors from recent 10-K filings
- get_8k_events: Recent material 8-K events (restatements, impairments, exec changes)
- get_peers: Industry peer benchmarking by SIC code
- get_industry_news: Graded RSS industry signals
- run_quant_models: Full analytics suite (Beneish M-score, ratios, risk scores, scenarios)

Always ground answers in data. When asked about a specific company or metric, use the tools \
to fetch current information rather than relying on memory. Be concise, cite specific figures, \
and use a professional audit/finance voice. For multi-step analyses, think step-by-step but \
keep prose tight."""


def _build_system(ticker: str, industry: str, risks: list, loop_stats: dict) -> str:
    parts = [_BASE_SYSTEM]
    if ticker:
        line = f"\nCurrent entity: {ticker.upper()}"
        if industry:
            line += f" — {industry}"
        parts.append(line)
    if risks:
        summary = [
            {"id": r.get("id"), "name": r.get("name"),
             "score": r.get("score"), "rag": r.get("rag"), "velocity": r.get("velocity")}
            for r in risks[:12]
        ]
        parts.append(f"\nActive risk register:\n{json.dumps(summary, indent=2)}")
    if loop_stats:
        parts.append(f"\nLoop stats: {json.dumps(loop_stats, default=str)[:800]}")
    return "\n".join(parts)


# ── Claude streaming tool-use loop ────────────────────────────────────────────

def _claude_stream(messages: list, system: str, tools: list, impls: dict):
    """Generator yielding SSE-payload dicts for a Claude chat turn with tool use."""
    client = claude_client.get_client()
    if client is None:
        yield {"type": "error", "message": "Claude unavailable — set ANTHROPIC_API_KEY in project/agentic-tools/.env"}
        return

    # Mark last tool definition for prompt caching
    cached_tools = [dict(t) for t in tools]
    if cached_tools:
        cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}

    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    accumulated_text = ""
    max_iterations = 8

    for iteration in range(1, max_iterations + 1):
        tool_uses_this_turn: list = []
        stop_reason = "end_turn"

        try:
            with client.messages.stream(
                model=_MODEL_CHAT,
                max_tokens=4096,
                system=system_blocks,
                tools=cached_tools,
                messages=messages,
            ) as stream:
                for event in stream:
                    etype = getattr(event, "type", None)
                    if etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if getattr(delta, "type", None) == "text_delta":
                            chunk = getattr(delta, "text", "") or ""
                            if chunk:
                                accumulated_text += chunk
                                yield {"type": "text_delta", "delta": chunk}

                final_msg = stream.get_final_message()
                stop_reason = final_msg.stop_reason or "end_turn"
                tool_uses_this_turn = [
                    b for b in final_msg.content
                    if getattr(b, "type", None) == "tool_use"
                ]

        except Exception as exc:
            yield {"type": "error", "message": str(exc)[:600]}
            return

        if stop_reason != "tool_use":
            break

        # Append assistant turn and execute tools
        messages.append({"role": "assistant", "content": final_msg.content})
        results = []
        for tb in tool_uses_this_turn:
            yield {"type": "tool_call", "tool": tb.name, "iteration": iteration}
            impl = impls.get(tb.name)
            try:
                output = impl(tb.input or {}) if impl else {"error": f"unknown tool {tb.name}"}
                is_error = False
            except Exception as exc:
                output = {"error": str(exc)[:400]}
                is_error = True
            preview = str(output)
            if len(preview) > 200:
                preview = preview[:200] + "…"
            yield {"type": "tool_result", "tool": tb.name, "is_error": is_error,
                   "result_preview": preview, "iteration": iteration}
            results.append({
                "type": "tool_result",
                "tool_use_id": tb.id,
                "content": json.dumps(output, default=str)[:10_000],
            })
        messages.append({"role": "user", "content": results})

    yield {"type": "done", "final_text": accumulated_text}


# ── Gemini conversational mode ────────────────────────────────────────────────

def _gemini_stream(messages: list, system: str, api_key: str):
    """Generator yielding SSE-payload dicts for a Gemini chat turn."""
    # Try new google-genai SDK first, then fall back to google-generativeai.
    last_user = messages[-1]["content"] if messages else ""
    history_msgs = messages[:-1]

    try:
        from google import genai as _g  # type: ignore
        client = _g.Client(api_key=api_key)
        # Build history for multi-turn
        contents = []
        for m in history_msgs:
            contents.append({"role": "model" if m["role"] == "assistant" else "user",
                              "parts": [{"text": m["content"]}]})
        contents.append({"role": "user", "parts": [{"text": system + "\n\n" + last_user}]})
        accumulated = ""
        for chunk in client.models.generate_content_stream(
            model="gemini-2.0-flash",
            contents=contents,
        ):
            text = getattr(chunk, "text", "") or ""
            if text:
                accumulated += text
                yield {"type": "text_delta", "delta": text}
        yield {"type": "done", "final_text": accumulated}
        return
    except ImportError:
        pass
    except Exception as exc:
        yield {"type": "error", "message": f"Gemini error: {exc}"}
        return

    # Fallback: older google-generativeai package
    try:
        import google.generativeai as genai  # type: ignore
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash-exp", system_instruction=system)
        chat_history = []
        for m in history_msgs:
            chat_history.append({
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [m["content"]],
            })
        chat = model.start_chat(history=chat_history)
        response = chat.send_message(last_user, stream=True)
        accumulated = ""
        for chunk in response:
            text = getattr(chunk, "text", "") or ""
            if text:
                accumulated += text
                yield {"type": "text_delta", "delta": text}
        yield {"type": "done", "final_text": accumulated}
    except ImportError:
        yield {
            "type": "error",
            "message": (
                "Google GenAI package not installed on the server. "
                "Run: pip install google-generativeai  in project/agentic-tools/, then restart api_server.py."
            ),
        }
    except Exception as exc:
        yield {"type": "error", "message": f"Gemini error: {exc}"}


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/ai/chat/stream")
def chat_stream(req: ChatRequest):
    """
    Streaming chat endpoint.

    Emits Server-Sent Events:
      data: {"type": "text_delta", "delta": "..."}
      data: {"type": "tool_call", "tool": "...", "iteration": N}
      data: {"type": "tool_result", "tool": "...", "is_error": bool, "result_preview": "...", "iteration": N}
      data: {"type": "done", "final_text": "..."}
      data: {"type": "error", "message": "..."}
    """
    import agent_tools

    # Build messages list from history + new message
    messages: list = []
    for h in req.history:
        if h.role in ("user", "assistant"):
            messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": req.message})

    system = _build_system(
        ticker=req.ticker or "",
        industry=req.industry or "",
        risks=req.risks,
        loop_stats=req.loop_stats,
    )

    _headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

    if req.provider == "gemini":
        if not req.gemini_api_key:
            def _no_key():
                yield (
                    'data: {"type":"error","message":"Gemini API key required. '
                    'Add it in Setup → AI Chat Assistant."}\n\n'
                )
            return StreamingResponse(_no_key(), media_type="text/event-stream", headers=_headers)

        def _gem():
            for evt in _gemini_stream(messages, system, req.gemini_api_key):
                yield f"data: {json.dumps(evt, default=str)}\n\n"

        return StreamingResponse(_gem(), media_type="text/event-stream", headers=_headers)

    # Claude (default)
    if not claude_client.is_available():
        def _no_claude():
            yield (
                'data: {"type":"error","message":"Claude unavailable — set ANTHROPIC_API_KEY '
                'in project/agentic-tools/.env, then restart api_server.py."}\n\n'
            )
        return StreamingResponse(_no_claude(), media_type="text/event-stream", headers=_headers)

    def _cl():
        for evt in _claude_stream(messages, system, agent_tools.TOOLS, agent_tools.IMPLS):
            yield f"data: {json.dumps(evt, default=str)}\n\n"

    return StreamingResponse(_cl(), media_type="text/event-stream", headers=_headers)
