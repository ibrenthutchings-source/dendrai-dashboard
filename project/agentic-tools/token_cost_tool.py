#!/usr/bin/env python3
"""
Token Cost Tool — Core Logic

Estimates and tracks token usage costs for Claude API calls.
Senses and adapts to the model being used via a catalog of current pricing.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple


# ── Model Pricing Catalog ──────────────────────────────────────────────────────
# Prices in USD per 1,000,000 tokens (MTok).
# Cache write: 1.25× input price (5-min TTL) | 2.0× input price (1-hour TTL).
# Cache read:  0.10× input price.
# Source: https://platform.claude.com/docs/en/pricing.md (cached 2026-06-04)

MODEL_PRICING: dict[str, dict] = {
    "claude-fable-5": {
        "input_per_mtok": 10.00,
        "output_per_mtok": 50.00,
        "cache_write_per_mtok": 12.50,
        "cache_read_per_mtok": 1.00,
        "context_window": 1_000_000,
        "max_output_tokens": 128_000,
        "description": "Claude Fable 5 — most capable widely released model",
    },
    "claude-mythos-5": {
        "input_per_mtok": 10.00,
        "output_per_mtok": 50.00,
        "cache_write_per_mtok": 12.50,
        "cache_read_per_mtok": 1.00,
        "context_window": 1_000_000,
        "max_output_tokens": 128_000,
        "description": "Claude Mythos 5 (Project Glasswing only)",
    },
    "claude-opus-4-8": {
        "input_per_mtok": 5.00,
        "output_per_mtok": 25.00,
        "cache_write_per_mtok": 6.25,
        "cache_read_per_mtok": 0.50,
        "context_window": 1_000_000,
        "max_output_tokens": 128_000,
        "description": "Claude Opus 4.8 — most capable Opus-tier model",
    },
    "claude-opus-4-7": {
        "input_per_mtok": 5.00,
        "output_per_mtok": 25.00,
        "cache_write_per_mtok": 6.25,
        "cache_read_per_mtok": 0.50,
        "context_window": 1_000_000,
        "max_output_tokens": 128_000,
        "description": "Claude Opus 4.7",
    },
    "claude-opus-4-6": {
        "input_per_mtok": 5.00,
        "output_per_mtok": 25.00,
        "cache_write_per_mtok": 6.25,
        "cache_read_per_mtok": 0.50,
        "context_window": 1_000_000,
        "max_output_tokens": 128_000,
        "description": "Claude Opus 4.6",
    },
    "claude-opus-4-5": {
        "input_per_mtok": 5.00,
        "output_per_mtok": 25.00,
        "cache_write_per_mtok": 6.25,
        "cache_read_per_mtok": 0.50,
        "context_window": 200_000,
        "max_output_tokens": 32_000,
        "description": "Claude Opus 4.5 (legacy)",
    },
    "claude-sonnet-4-6": {
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
        "cache_write_per_mtok": 3.75,
        "cache_read_per_mtok": 0.30,
        "context_window": 1_000_000,
        "max_output_tokens": 64_000,
        "description": "Claude Sonnet 4.6 — balanced speed and intelligence",
    },
    "claude-sonnet-4-5": {
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
        "cache_write_per_mtok": 3.75,
        "cache_read_per_mtok": 0.30,
        "context_window": 200_000,
        "max_output_tokens": 64_000,
        "description": "Claude Sonnet 4.5 (legacy)",
    },
    "claude-haiku-4-5": {
        "input_per_mtok": 1.00,
        "output_per_mtok": 5.00,
        "cache_write_per_mtok": 1.25,
        "cache_read_per_mtok": 0.10,
        "context_window": 200_000,
        "max_output_tokens": 64_000,
        "description": "Claude Haiku 4.5 — fastest and most cost-effective",
    },
}

# Date-suffixed model aliases → canonical ID
_DATE_ALIASES: dict[str, str] = {
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "claude-opus-4-5-20251101": "claude-opus-4-5",
    "claude-sonnet-4-5-20250929": "claude-sonnet-4-5",
    "claude-opus-4-20250514": "claude-opus-4-6",
    "claude-sonnet-4-20250514": "claude-sonnet-4-6",
}

# Ordered keyword fragments → canonical ID (most-specific first)
_KEYWORD_MAP: list[tuple[str, str]] = [
    ("fable-5", "claude-fable-5"),
    ("fable", "claude-fable-5"),
    ("mythos-5", "claude-mythos-5"),
    ("mythos", "claude-mythos-5"),
    ("opus-4-8", "claude-opus-4-8"),
    ("opus-4-7", "claude-opus-4-7"),
    ("opus-4-6", "claude-opus-4-6"),
    ("opus-4-5", "claude-opus-4-5"),
    ("sonnet-4-6", "claude-sonnet-4-6"),
    ("sonnet-4-5", "claude-sonnet-4-5"),
    ("haiku-4-5", "claude-haiku-4-5"),
    # Generic family shorthand → latest in family
    ("opus", "claude-opus-4-8"),
    ("sonnet", "claude-sonnet-4-6"),
    ("haiku", "claude-haiku-4-5"),
]


class CostBreakdown(NamedTuple):
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    cache_read_cost_usd: float
    cache_write_cost_usd: float
    total_cost_usd: float


def normalize_model(model_str: str) -> str:
    """
    Normalize a model string (partial, aliased, or date-suffixed) to a
    canonical ID present in MODEL_PRICING. Falls back to the input string
    when no match is found, allowing the caller to surface a clear error.
    """
    s = model_str.strip().lower()

    if s in MODEL_PRICING:
        return s

    if s in _DATE_ALIASES:
        return _DATE_ALIASES[s]

    for keyword, canonical in _KEYWORD_MAP:
        if keyword in s:
            return canonical

    return model_str


def get_pricing(model_str: str) -> dict | None:
    """Return pricing dict for model_str, or None if model is unknown."""
    return MODEL_PRICING.get(normalize_model(model_str))


def calculate_cost(
    model_str: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> CostBreakdown:
    """Calculate USD cost from token counts. Raises ValueError for unknown models."""
    canonical = normalize_model(model_str)
    pricing = MODEL_PRICING.get(canonical)

    if pricing is None:
        raise ValueError(
            f"Unknown model: {model_str!r}. "
            f"Supported: {', '.join(sorted(MODEL_PRICING))}"
        )

    inp = input_tokens * pricing["input_per_mtok"] / 1_000_000
    out = output_tokens * pricing["output_per_mtok"] / 1_000_000
    cr = cache_read_tokens * pricing["cache_read_per_mtok"] / 1_000_000
    cw = cache_write_tokens * pricing["cache_write_per_mtok"] / 1_000_000

    return CostBreakdown(
        model=canonical,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        input_cost_usd=inp,
        output_cost_usd=out,
        cache_read_cost_usd=cr,
        cache_write_cost_usd=cw,
        total_cost_usd=inp + out + cr + cw,
    )


def estimate_tokens_local(text: str) -> int:
    """
    Rough local estimate: ~4 characters ≈ 1 token.
    Accurate to within ±20–30% for English prose.
    """
    return max(1, len(text) // 4)


def count_tokens_api(
    text: str,
    model_str: str,
    api_key: str | None = None,
    system_prompt: str | None = None,
) -> tuple[int, str]:
    """
    Count tokens using the Anthropic count_tokens API.
    Returns (count, method) where method is "api" or "estimate".
    Falls back to local estimation when the API is unavailable.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return estimate_tokens_local(text), "estimate"

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        canonical = normalize_model(model_str)

        kwargs: dict = {
            "model": canonical,
            "messages": [{"role": "user", "content": text}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        result = client.messages.count_tokens(**kwargs)
        return result.input_tokens, "api"
    except Exception:
        return estimate_tokens_local(text), "estimate"


# ── Session Persistence ────────────────────────────────────────────────────────

def _load_state(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return {"sessions": {}}


def _save_state(state: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _ensure_session(state: dict, session: str) -> dict:
    if session not in state["sessions"]:
        now = datetime.now(timezone.utc).isoformat()
        state["sessions"][session] = {
            "created_at": now,
            "updated_at": now,
            "calls": [],
            "totals": {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.0,
            },
        }
    return state["sessions"][session]


def track_usage(
    model_str: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    label: str = "",
    session: str = "default",
    data_file: Path = Path("token_costs.json"),
) -> dict:
    """
    Record actual API token usage and persist to data_file.
    Returns a dict with per-call cost and running session totals.
    """
    bd = calculate_cost(
        model_str, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
    )

    state = _load_state(data_file)
    sess = _ensure_session(state, session)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": bd.model,
        "label": label,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "cost_usd": round(bd.total_cost_usd, 8),
    }
    sess["calls"].append(entry)

    t = sess["totals"]
    t["calls"] += 1
    t["input_tokens"] += input_tokens
    t["output_tokens"] += output_tokens
    t["cache_read_tokens"] += cache_read_tokens
    t["cache_write_tokens"] += cache_write_tokens
    t["cost_usd"] = round(t["cost_usd"] + bd.total_cost_usd, 8)
    sess["updated_at"] = datetime.now(timezone.utc).isoformat()

    _save_state(state, data_file)

    return {"call": entry, "session_totals": dict(t), "session": session}


def get_session_summary(
    session: str = "default",
    data_file: Path = Path("token_costs.json"),
) -> dict:
    """Return session totals and the most recent 10 calls."""
    state = _load_state(data_file)
    if session not in state["sessions"]:
        return {
            "error": f"Session {session!r} not found",
            "sessions": list(state["sessions"]),
        }

    sess = state["sessions"][session]
    return {
        "session": session,
        "created_at": sess.get("created_at", ""),
        "updated_at": sess.get("updated_at", ""),
        "totals": sess["totals"],
        "recent_calls": sess["calls"][-10:],
        "total_calls": len(sess["calls"]),
    }


def list_sessions(data_file: Path = Path("token_costs.json")) -> list[str]:
    """Return all session names in the data file."""
    return list(_load_state(data_file)["sessions"].keys())


def reset_session(
    session: str = "default",
    data_file: Path = Path("token_costs.json"),
) -> bool:
    """Delete a session. Returns True if it existed."""
    state = _load_state(data_file)
    if session in state["sessions"]:
        del state["sessions"][session]
        _save_state(state, data_file)
        return True
    return False
