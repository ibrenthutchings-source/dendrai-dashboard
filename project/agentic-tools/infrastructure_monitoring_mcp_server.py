#!/usr/bin/env python3
"""
Infrastructure Monitoring MCP Server

Exposes continuous IaaS/OS/DB configuration auditing as MCP tools —
Postgres CIS-style hardening checks today (postgres_cis_tool.py); Railway
platform/deployment drift (railway_iaas_tool.py, P2a) lands as additional
tools in this same server once built, not a separate one — same PaC process
("infrastructure_monitoring"), same event type (INFRASTRUCTURE_FINDING).

Findings aren't listed via a dedicated table here — they ride the generic
observability.system_telemetry -> adjudicated_tool_calls path every other
poll-connector uses (see connector_poller.py's module docstring), so they
already surface in Continuous Monitoring / Controls Monitor for free. This
server's tools are for running an audit on demand and inspecting the
registered targets, not a parallel findings store.

── Setup ─────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "infrastructure-monitoring": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/infrastructure_monitoring_mcp_server.py"]
        }
      }
    }

── Available tools ───────────────────────────────────────────────────────────

    iaas_list_targets       Registered Postgres CIS audit connectors (no credentials)
    iaas_run_postgres_audit Run a Postgres CIS-style hardening audit now (write-guarded)
    iaas_run_railway_audit  Run a Railway platform/deployment drift audit now (write-guarded)

── Environment variables ─────────────────────────────────────────────────────

    DATABASE_URL          PostgreSQL connection string (required for persistence)
    MCP_READ_ONLY         Set to "true" to block iaas_run_postgres_audit
    MCP_RATE_LIMIT_PER_MIN  Override per-tool rate limit (default 30)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from mcp_guards import audit_log, cap_output, check_rate_limit, check_read_only
import db
import postgres_cis_tool
import railway_iaas_tool

mcp = FastMCP("infrastructure-monitoring")

_IAAS_CONNECTOR_TYPES = ("postgres_cis", "railway_iaas")


@mcp.tool()
def iaas_list_targets() -> str:
    """
    List every target registered for continuous infrastructure auditing —
    Postgres instances (CIS-style hardening) and Railway environments
    (platform/deployment drift). Credentials are never included — only
    display metadata.
    """
    try:
        check_rate_limit("iaas_list_targets")
        if not db.is_available():
            return json.dumps({"targets": [], "note": "Database not configured"}, indent=2)
        rows = [c for c in db.list_poll_connectors() if c["connector_type"] in _IAAS_CONNECTOR_TYPES]
        out = [{
            "id": c["id"], "connector_type": c["connector_type"], "display_name": c["display_name"],
            "resource_label": (c.get("extra_config") or {}).get("resource_label")
                or (c.get("extra_config") or {}).get("environment_id"),
            "active": c["active"], "last_poll_at": c["last_poll_at"],
            "last_poll_status": c["last_poll_status"],
        } for c in rows]
        return cap_output(json.dumps({"targets": out}, indent=2, default=str))
    except Exception as exc:
        return f"Error listing targets: {exc}"


@mcp.tool()
def iaas_run_postgres_audit(connector_id: int) -> str:
    """
    Run a Postgres CIS-style hardening audit now for one registered target,
    adjudicated through the full Bronze->Silver->Gold->Council pipeline and
    the infrastructure_monitoring PaC policy. Blocked when MCP_READ_ONLY=true
    (this writes an adjudication row).

    Args:
        connector_id: The registry id from iaas_list_targets.
    """
    try:
        check_read_only("iaas_run_postgres_audit")
        check_rate_limit("iaas_run_postgres_audit")
        if not db.is_available():
            return json.dumps({"note": "Database not configured"}, indent=2)

        audit_log("iaas_run_postgres_audit", connector_id=connector_id)

        connector = db.get_poll_connector(connector_id, include_credentials=True)
        if not connector or connector["connector_type"] != "postgres_cis":
            return f"Error: target {connector_id} not found"

        events = postgres_cis_tool.pull_events(
            connector.get("base_url"), connector.get("credentials") or {},
            connector.get("extra_config") or {}, None,
        )
        db.record_poll_result(connector_id, "ok", None)
        return cap_output(json.dumps({"events": events}, indent=2, default=str))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        try:
            db.record_poll_result(connector_id, "error", str(exc))
        except Exception:
            pass
        return f"Error running audit: {exc}"


@mcp.tool()
def iaas_run_railway_audit(connector_id: int) -> str:
    """
    Run a Railway platform/deployment drift audit now for one registered
    environment — checks every service instance for an unexpected public
    domain and a deployment whose image digest doesn't match any known
    pipeline attestation, adjudicated through the full
    Bronze->Silver->Gold->Council pipeline and the infrastructure_monitoring
    PaC policy. Blocked when MCP_READ_ONLY=true (this writes an adjudication
    row).

    Args:
        connector_id: The registry id from iaas_list_targets.
    """
    try:
        check_read_only("iaas_run_railway_audit")
        check_rate_limit("iaas_run_railway_audit")
        if not db.is_available():
            return json.dumps({"note": "Database not configured"}, indent=2)

        audit_log("iaas_run_railway_audit", connector_id=connector_id)

        connector = db.get_poll_connector(connector_id, include_credentials=True)
        if not connector or connector["connector_type"] != "railway_iaas":
            return f"Error: target {connector_id} not found"

        events = railway_iaas_tool.pull_events(
            connector.get("base_url"), connector.get("credentials") or {},
            connector.get("extra_config") or {}, None,
        )
        db.record_poll_result(connector_id, "ok", None)
        return cap_output(json.dumps({"events": events}, indent=2, default=str))
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        try:
            db.record_poll_result(connector_id, "error", str(exc))
        except Exception:
            pass
        return f"Error running audit: {exc}"


if __name__ == "__main__":
    mcp.run()
