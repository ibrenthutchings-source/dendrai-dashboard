#!/usr/bin/env python3
"""
Managed Agents scheduled deployment for Dendrai Intelligenza (recommendation #5).

Turns the Loop tab's copy-paste `/schedule` panel into a real autonomous agent.
Anthropic runs the agent loop on a cron cadence; each firing re-investigates a
ticker, writes findings to a memory store, and produces an investigation memo.

This is the CONTROL PLANE (run once to provision). It follows the mandatory flow:

    Agent (created once, versioned)  →  Environment (reusable sandbox)
                                     →  Deployment (cron schedule)

Usage:
    pip install -U anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python managed_agent_setup.py --ticker ON --cron "0 8 * * 1"   # Mondays 08:00
    python managed_agent_setup.py --ticker ON --run-now            # trigger immediately
    python managed_agent_setup.py --list                           # show runs

Optional — give the cloud agent your hosted Dendrai MCP server so it can call the
real EDGAR/FRED tools instead of web search:
    export DENDRAI_MCP_URL=https://your-host/mcp

Notes:
- Agents/environments are persistent. We look them up by name and reuse them;
  we never create a fresh agent per run (the #1 Managed Agents anti-pattern).
- Deployments are a newer surface; if the installed SDK predates them this script
  prints the equivalent raw-HTTP call instead of failing.
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    import anthropic
except ImportError:
    sys.exit("anthropic SDK not installed. Run: pip install -U anthropic")

import claude_client

MODEL = claude_client.MODEL
AGENT_NAME = "Dendrai Risk Loop Agent"
ENV_NAME = "dendrai-risk-loop-env"

SYSTEM_PROMPT = """You are the Dendrai autonomous internal-audit agent. On each \
scheduled run you re-investigate a company's risk posture: pull its latest SEC \
financials and recent 8-K / 10-K filings, benchmark against industry peers, and \
identify the most material risks and any changes since your last run.

Consult your memory store at the start of every run for prior-cycle findings, and \
write this run's findings back to it so the next run can compare. Produce a concise \
investigation memo: the 3–5 most material risks, the evidence for each (cite figures \
and filing dates), what changed since last cycle, and a recommended audit focus. \
Escalate explicitly (lead the memo with ‼️ ESCALATION) only when a risk has \
materially worsened or a new material event has appeared."""


def _find_by_name(items, name):
    for it in items:
        if getattr(it, "name", None) == name:
            return it
    return None


def ensure_environment(client):
    """Reuse the Dendrai environment if it exists, else create it."""
    existing = _find_by_name(list(client.beta.environments.list()), ENV_NAME)
    if existing:
        print(f"  environment: reuse {existing.id}")
        return existing
    env = client.beta.environments.create(
        name=ENV_NAME,
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    print(f"  environment: created {env.id}")
    return env


def ensure_agent(client):
    """Reuse the Dendrai agent if it exists, else create it (once)."""
    existing = _find_by_name(list(client.beta.agents.list()), AGENT_NAME)

    mcp_url = os.environ.get("DENDRAI_MCP_URL", "").strip()
    tools = [{"type": "agent_toolset_20260401", "default_config": {"enabled": True}}]
    mcp_servers = []
    if mcp_url:
        mcp_servers = [{"type": "url", "name": "dendrai", "url": mcp_url}]
        tools.append({"type": "mcp_toolset", "mcp_server_name": "dendrai"})

    if existing:
        print(f"  agent: reuse {existing.id} (v{existing.version})")
        return existing

    kwargs = dict(name=AGENT_NAME, model=MODEL, system=SYSTEM_PROMPT, tools=tools)
    if mcp_servers:
        kwargs["mcp_servers"] = mcp_servers
    agent = client.beta.agents.create(**kwargs)
    print(f"  agent: created {agent.id} (v{agent.version})"
          + (f" with MCP server {mcp_url}" if mcp_url else " (web tools only)"))
    return agent


def create_deployment(client, agent, env, ticker, cron):
    if not hasattr(client.beta, "deployments"):
        print("\nInstalled SDK has no client.beta.deployments — upgrade with: pip install -U anthropic")
        print("Equivalent raw HTTP:\n")
        print(f"""curl -fsSL https://api.anthropic.com/v1/deployments \\
  -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" \\
  -H "anthropic-beta: managed-agents-2026-04-01" -H "content-type: application/json" \\
  -d '{{"name":"Dendrai {ticker} weekly","agent":"{agent.id}","environment_id":"{env.id}",
       "initial_events":[{{"type":"user.message","content":[{{"type":"text",
       "text":"Re-run the Dendrai risk loop for {ticker}."}}]}}],
       "schedule":{{"type":"cron","expression":"{cron}","timezone":"America/New_York"}}}}'""")
        return None

    deployment = client.beta.deployments.create(
        name=f"Dendrai {ticker} risk loop",
        agent=agent.id,
        environment_id=env.id,
        initial_events=[{
            "type": "user.message",
            "content": [{"type": "text", "text":
                f"Re-run the Dendrai risk loop for {ticker}. Compare against your prior "
                f"findings in memory and escalate only material changes."}],
        }],
        schedule={"type": "cron", "expression": cron, "timezone": "America/New_York"},
    )
    print(f"  deployment: created {deployment.id}")
    upcoming = getattr(getattr(deployment, "schedule", None), "upcoming_runs_at", None)
    if upcoming:
        print(f"    next runs: {', '.join(str(t) for t in upcoming[:3])}")
    return deployment


def list_runs(client, ticker):
    if not hasattr(client.beta, "deployments"):
        sys.exit("This SDK has no deployments support; upgrade anthropic.")
    deps = [d for d in client.beta.deployments.list()] if hasattr(client.beta.deployments, "list") else []
    target = _find_by_name(deps, f"Dendrai {ticker} risk loop")
    if not target:
        print(f"No deployment found for {ticker}.")
        return
    print(f"Deployment {target.id} ({target.status}):")
    for run in client.beta.deployment_runs.list(deployment_id=target.id):
        marker = run.session_id or (run.error.type if run.error else "?")
        print(f"  {run.created_at}  {marker}")


def main():
    ap = argparse.ArgumentParser(description="Provision the Dendrai Managed Agent + schedule")
    ap.add_argument("--ticker", default="ON", help="Ticker to investigate on schedule")
    ap.add_argument("--cron", default="0 8 * * 1", help="Cron expression (default: Mon 08:00)")
    ap.add_argument("--run-now", action="store_true", help="Trigger one run immediately (smoke test)")
    ap.add_argument("--list", action="store_true", help="List deployment runs for the ticker")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first.")
    client = anthropic.Anthropic()

    if args.list:
        list_runs(client, args.ticker)
        return

    print(f"Provisioning Dendrai Managed Agent for {args.ticker} (cron: {args.cron})")
    env = ensure_environment(client)
    agent = ensure_agent(client)
    deployment = create_deployment(client, agent, env, args.ticker, args.cron)

    if args.run_now and deployment is not None:
        run = client.beta.deployments.run(deployment.id)
        sid = getattr(run, "session_id", None)
        print(f"  manual run triggered: {sid or run}")
        if sid:
            print(f"  watch: https://platform.claude.com/workspaces/default/sessions/{sid}")

    print("Done.")


if __name__ == "__main__":
    main()
