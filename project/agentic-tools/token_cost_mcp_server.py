#!/usr/bin/env python3
"""
Token Cost MCP Server

Estimates and tracks Claude API token costs — before and after each API call.
Automatically detects the model being used and applies the correct pricing.

── Prerequisites ───────────────────────────────────────────────────────────────

Optional: set ANTHROPIC_API_KEY for exact token counts via the Anthropic API.
Without it the server falls back to a character-based approximation (~4 chars
per token, accurate to ±20–30% for English prose).

To set it in a .env file (loaded automatically on startup):
    ANTHROPIC_API_KEY=sk-ant-...

── Claude Desktop ─ ~/.claude/claude_desktop_config.json ──────────────────────

    {
      "mcpServers": {
        "token-cost": {
          "command": "python",
          "args": ["/absolute/path/to/token_cost_mcp_server.py"]
        }
      }
    }

── Claude Code ─ .claude/settings.json ────────────────────────────────────────

    {
      "mcpServers": {
        "token-cost": {
          "command": "python",
          "args": ["/absolute/path/to/token_cost_mcp_server.py"]
        }
      }
    }

── Available tools ─────────────────────────────────────────────────────────────
    cost_estimate         Estimate tokens + USD cost before an API call
    cost_count_tokens     Count tokens in text (exact via API or approximation)
    cost_track            Record actual token usage after an API response
    cost_session_summary  Show accumulated costs for a session
    cost_reset_session    Clear a session's accumulated data
    cost_list_models      Show all supported models with per-token pricing
    cost_list_sessions    List all tracked sessions in the data file
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from token_cost_tool import (
    MODEL_PRICING,
    calculate_cost,
    count_tokens_api,
    estimate_tokens_local,
    get_pricing,
    get_session_summary,
    list_sessions,
    normalize_model,
    reset_session,
    track_usage,
)

mcp = FastMCP("token-cost")


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def cost_estimate(
    text: str,
    model: str = "claude-opus-4-8",
    max_output_tokens: int = 4096,
    system_prompt: str = "",
    anthropic_api_key: str = "",
) -> str:
    """
    Estimate the token count and USD cost of an API call BEFORE making it.
    Useful for budgeting and choosing the right model for a given task.

    Token counting uses the Anthropic count_tokens API for exact results when
    ANTHROPIC_API_KEY is set; falls back to a character-based approximation
    (~4 chars per token, ±20–30% for English prose) otherwise.

    Accepts full model IDs (claude-opus-4-8), version aliases (opus-4-8),
    and family shorthand (opus → claude-opus-4-8, sonnet, haiku).

    Args:
        text:              The user message / prompt text to estimate
        model:             Claude model ID or alias (default: claude-opus-4-8)
        max_output_tokens: Expected maximum output size for the output cost estimate
        system_prompt:     Optional system prompt included in the token count
        anthropic_api_key: API key — falls back to ANTHROPIC_API_KEY env var
    """
    canonical = normalize_model(model)
    pricing = get_pricing(model)
    if pricing is None:
        return (
            f"Unknown model: {model!r}\n"
            "Run cost_list_models to see all supported models."
        )

    key = anthropic_api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    input_tokens, method = count_tokens_api(text, canonical, key, system_prompt or None)

    input_bd = calculate_cost(canonical, input_tokens=input_tokens)
    output_bd = calculate_cost(canonical, output_tokens=max_output_tokens)
    total_est = input_bd.input_cost_usd + output_bd.output_cost_usd

    method_detail = (
        "exact count from Anthropic token-counting API"
        if method == "api"
        else "approximation (~4 chars per token)"
    )

    lines = [
        f"Cost Estimate — {canonical}",
        f"  Pricing:      ${pricing['input_per_mtok']:.2f} / ${pricing['output_per_mtok']:.2f} per MTok (in/out)",
        f"  Token method: {method} ({method_detail})",
        "",
        f"  Input tokens:  {input_tokens:>10,}   → ${input_bd.input_cost_usd:.6f}",
        f"  Output tokens: {max_output_tokens:>10,}   → ${output_bd.output_cost_usd:.6f}  (estimated max)",
        f"  ───────────────────────────────────────────",
        f"  Total estimate:              ${total_est:.6f}",
        "",
        f"  Context window:  {pricing['context_window']:,} tokens",
        f"  Max output:      {pricing['max_output_tokens']:,} tokens",
    ]

    if input_tokens > pricing["context_window"]:
        lines.append(
            f"\n  WARNING: Input ({input_tokens:,}) exceeds context window "
            f"({pricing['context_window']:,}) — request will fail."
        )

    lines.extend([
        "",
        "  Prompt caching (when enabled):",
        f"    Cache write:  ${pricing['cache_write_per_mtok']:.2f}/MTok  (1.25× input — 5-min TTL)",
        f"    Cache read:   ${pricing['cache_read_per_mtok']:.2f}/MTok  (0.10× input)",
    ])

    return "\n".join(lines)


@mcp.tool()
def cost_count_tokens(
    text: str,
    model: str = "claude-opus-4-8",
    system_prompt: str = "",
    anthropic_api_key: str = "",
) -> str:
    """
    Count tokens in a text string for a given Claude model.

    Uses the Anthropic token-counting API for exact counts when ANTHROPIC_API_KEY
    is set; falls back to a character-based approximation (~4 chars per token).
    Token counts are model-specific — pass the same model you intend to use.

    Args:
        text:              Text to count tokens for
        model:             Claude model ID or alias
        system_prompt:     Optional system prompt to include in the count
        anthropic_api_key: API key — falls back to ANTHROPIC_API_KEY env var
    """
    canonical = normalize_model(model)
    pricing = get_pricing(canonical)
    key = anthropic_api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")

    tokens, method = count_tokens_api(text, canonical, key, system_prompt or None)
    approx = estimate_tokens_local(text + (system_prompt or ""))

    method_label = "exact" if method == "api" else "approximate"
    lines = [
        f"Token Count — {canonical}",
        f"  Method: {method} ({method_label})",
        f"  Tokens: {tokens:,}",
    ]

    if method == "estimate":
        lines.append("  Tip: set ANTHROPIC_API_KEY for exact counts via the API")
    else:
        diff = tokens - approx
        lines.append(f"  Local estimate would have been: {approx:,}  (diff: {diff:+,})")

    if pricing:
        cost = tokens * pricing["input_per_mtok"] / 1_000_000
        lines.append(f"  Input cost at {canonical}: ${cost:.6f}")

    return "\n".join(lines)


@mcp.tool()
def cost_track(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    label: str = "",
    session: str = "default",
    data_file: str = "token_costs.json",
) -> str:
    """
    Record actual token usage from a Claude API response and accumulate costs.

    Call this immediately after receiving an API response. The token counts
    come from the usage object in the response:
      input_tokens        → response.usage.input_tokens
      output_tokens       → response.usage.output_tokens
      cache_read_tokens   → response.usage.cache_read_input_tokens  (0 if absent)
      cache_write_tokens  → response.usage.cache_creation_input_tokens (0 if absent)

    Costs are persisted across sessions in the data file and can be reviewed
    with cost_session_summary.

    Args:
        model:              Model used — full ID, alias, or shorthand (auto-detected)
        input_tokens:       Uncached input tokens
        output_tokens:      Output tokens generated
        cache_read_tokens:  Tokens served from cache
        cache_write_tokens: Tokens written to cache
        label:              Optional label for this call (e.g. "edgar_lookup")
        session:            Session name for cost grouping (default: "default")
        data_file:          Path to the JSON cost data file (default: token_costs.json)
    """
    try:
        result = track_usage(
            model_str=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            label=label,
            session=session,
            data_file=Path(data_file),
        )
    except ValueError as e:
        return f"Error: {e}"

    call = result["call"]
    totals = result["session_totals"]

    lines = [
        f"Usage Tracked — {call['model']}",
        f"  Label:    {label or '(none)'}",
        f"  Session:  {session}",
        "",
        "  This call:",
        f"    Input tokens:         {call['input_tokens']:>10,}",
        f"    Output tokens:        {call['output_tokens']:>10,}",
    ]

    if call["cache_read_tokens"]:
        lines.append(f"    Cache read tokens:    {call['cache_read_tokens']:>10,}")
    if call["cache_write_tokens"]:
        lines.append(f"    Cache write tokens:   {call['cache_write_tokens']:>10,}")

    lines.extend([
        f"    Cost:                 ${call['cost_usd']:.6f}",
        "",
        f"  Session '{session}' totals:",
        f"    Calls:           {totals['calls']:>6}",
        f"    Input tokens:    {totals['input_tokens']:>12,}",
        f"    Output tokens:   {totals['output_tokens']:>12,}",
        f"    Total cost:      ${totals['cost_usd']:.6f}",
        "",
        f"Saved to {data_file}",
    ])

    return "\n".join(lines)


@mcp.tool()
def cost_session_summary(
    session: str = "default",
    data_file: str = "token_costs.json",
) -> str:
    """
    Show accumulated token usage and USD cost for a session.
    Includes a breakdown of the most recent 10 API calls.

    Args:
        session:   Session name to summarize (default: "default")
        data_file: Path to the JSON cost data file (default: token_costs.json)
    """
    summary = get_session_summary(session=session, data_file=Path(data_file))

    if "error" in summary:
        avail = ", ".join(summary.get("sessions", [])) or "(none)"
        return f"Error: {summary['error']}\n\nAvailable sessions: {avail}"

    totals = summary["totals"]
    lines = [
        f"Session Summary: {session}",
        f"  Created:  {summary['created_at'][:19].replace('T', ' ')} UTC",
        f"  Updated:  {summary['updated_at'][:19].replace('T', ' ')} UTC",
        "",
        "── Totals ────────────────────────────────────────────────────────────────",
        f"  API calls:       {totals['calls']:>8}",
        f"  Input tokens:    {totals['input_tokens']:>12,}",
        f"  Output tokens:   {totals['output_tokens']:>12,}",
    ]

    if totals["cache_read_tokens"]:
        lines.append(f"  Cache read toks: {totals['cache_read_tokens']:>12,}")
    if totals["cache_write_tokens"]:
        lines.append(f"  Cache write toks:{totals['cache_write_tokens']:>12,}")

    lines.extend([
        f"  Total cost:      ${totals['cost_usd']:.6f}",
        "",
        f"── Recent Calls (last {len(summary['recent_calls'])} of {summary['total_calls']}) ──",
    ])

    for call in summary["recent_calls"]:
        ts = call["timestamp"][:19].replace("T", " ")
        label = f" [{call['label']}]" if call.get("label") else ""
        lines.append(
            f"  {ts}  {call['model']:<20}  "
            f"in={call['input_tokens']:>7,}  "
            f"out={call['output_tokens']:>6,}  "
            f"${call['cost_usd']:.6f}{label}"
        )

    return "\n".join(lines)


@mcp.tool()
def cost_reset_session(
    session: str = "default",
    data_file: str = "token_costs.json",
) -> str:
    """
    Clear all accumulated data for a session. Cannot be undone.
    Run cost_session_summary first to review costs before clearing.

    Args:
        session:   Session name to clear (default: "default")
        data_file: Path to the JSON cost data file (default: token_costs.json)
    """
    existed = reset_session(session=session, data_file=Path(data_file))
    if existed:
        return f"Session '{session}' cleared from {data_file}."
    return f"Session '{session}' not found in {data_file} — nothing to clear."


@mcp.tool()
def cost_list_models() -> str:
    """
    List all Claude models in the pricing catalog with per-token costs.
    Shows context window, max output, and cache pricing for each model.
    Useful for comparing cost tradeoffs before choosing a model.
    """
    lines = [
        f"Claude Model Pricing Catalog  ({len(MODEL_PRICING)} models)",
        "=" * 74,
        f"  {'Model':<22}  {'Input/MTok':>11}  {'Output/MTok':>12}  {'Context':>10}",
        "  " + "─" * 70,
    ]

    for model_id, p in MODEL_PRICING.items():
        lines.append(
            f"  {model_id:<22}  ${p['input_per_mtok']:>10.2f}  "
            f"${p['output_per_mtok']:>10.2f}  "
            f"{p['context_window']:>9,}"
        )
        lines.append(f"  {'':22}  {p['description']}")

    lines.extend([
        "",
        "Cache pricing (standard across all models):",
        "  Write (5-min TTL):  1.25× input price",
        "  Write (1-hour TTL): 2.00× input price",
        "  Read:               0.10× input price",
        "",
        "Model alias shortcuts:",
        "  opus    → claude-opus-4-8     (latest Opus)",
        "  sonnet  → claude-sonnet-4-6   (latest Sonnet)",
        "  haiku   → claude-haiku-4-5    (latest Haiku)",
        "  fable   → claude-fable-5      (most capable)",
        "",
        "Prices in USD per 1,000,000 tokens (MTok).",
    ])

    return "\n".join(lines)


@mcp.tool()
def cost_list_sessions(data_file: str = "token_costs.json") -> str:
    """
    List all sessions tracked in the cost data file with total calls and cost.

    Args:
        data_file: Path to the JSON cost data file (default: token_costs.json)
    """
    sessions = list_sessions(data_file=Path(data_file))
    if not sessions:
        return f"No sessions found in {data_file}."

    lines = [f"Sessions in {data_file}:"]
    for s in sessions:
        summary = get_session_summary(session=s, data_file=Path(data_file))
        totals = summary.get("totals", {})
        lines.append(
            f"  {s:<24}  calls={totals.get('calls', 0):>5}  "
            f"cost=${totals.get('cost_usd', 0.0):.6f}"
        )

    return "\n".join(lines)


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
