#!/usr/bin/env python3
"""
Oracle Fusion Controls MCP Server

Exposes Oracle Fusion Cloud control data as MCP tools usable by
Claude Code and Claude Desktop.

── Setup ─────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "oracle-fusion": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/oracle_fusion_mcp_server.py"],
          "env": {
            "ORACLE_FUSION_HOST":     "https://mycompany.fa.us6.oraclecloud.com",
            "ORACLE_FUSION_USERNAME": "svc_dendrai",
            "ORACLE_FUSION_PASSWORD": "..."
          }
        }
      }
    }

Claude Code — add to .claude/settings.json in your project:

    {
      "mcpServers": {
        "oracle-fusion": {
          "command": "python",
          "args": ["/absolute/path/to/agentic-tools/oracle_fusion_mcp_server.py"]
        }
      }
    }

── Available tools ───────────────────────────────────────────────────────────

    fusion_control_library    All active controls from the RMCS control library
    fusion_control_results    Control test results / evidence of effectiveness
    fusion_control_issues     Open control deficiencies and remediation plans
    fusion_user_roles         User-to-role assignments (access controls)
    fusion_sod_violations     Segregation-of-duties policy violations
    fusion_audit_events       Transaction audit trail from FSCM modules
    fusion_control_summary    Aggregated control health overview (recommended first call)

── Required environment variables ────────────────────────────────────────────

    ORACLE_FUSION_HOST        https://mycompany.fa.us6.oraclecloud.com
    ORACLE_FUSION_USERNAME    service account username
    ORACLE_FUSION_PASSWORD    service account password

    Optional — OAuth 2.0 Client Credentials (recommended for production):
    ORACLE_FUSION_CLIENT_ID
    ORACLE_FUSION_CLIENT_SECRET
"""

import json
import os
import sys

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from mcp_guards import audit_log, check_rate_limit, validate_enum
from oracle_fusion_tool import (
    OracleFusionClient,
    get_audit_events,
    get_control_issues,
    get_control_library,
    get_control_results,
    get_control_summary,
    get_sod_violations,
    get_user_roles,
    is_configured,
)

mcp = FastMCP("oracle-fusion")

_NOT_CONFIGURED = (
    "Oracle Fusion is not configured. "
    "Set ORACLE_FUSION_HOST, ORACLE_FUSION_USERNAME, and ORACLE_FUSION_PASSWORD "
    "environment variables."
)


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def fusion_control_summary() -> str:
    """
    Return an aggregated control health overview for Oracle Fusion.

    Combines the control library (effectiveness ratings), open issues
    (deficiencies), and SOD violations into a single executive dashboard.

    Includes risk_signals compatible with the Dendrai risk register schema,
    an overall RAG status (R/A/G), and a 0–25 control risk score.

    This is the recommended first call — it gives you the full picture before
    drilling into individual categories with the other tools.
    """
    if not is_configured():
        return _NOT_CONFIGURED
    try:
        check_rate_limit("fusion_control_summary")
        audit_log("fusion_control_summary")
        return json.dumps(get_control_summary(), indent=2)
    except Exception as exc:
        return f"Error fetching control summary: {exc}"


@mcp.tool()
def fusion_control_library(
    control_type: str = "",
    category: str = "",
    status: str = "Active",
) -> str:
    """
    Return the Oracle Risk Management Cloud control library.

    Lists all control definitions including type (Preventive / Detective /
    Corrective), frequency, owner, effectiveness rating, and last test date.

    Args:
        control_type: Preventive | Detective | Corrective (empty = all types)
        category:     control category string, e.g. "Financial Reporting" (empty = all)
        status:       Active | Inactive | All (default Active)
    """
    if not is_configured():
        return _NOT_CONFIGURED
    try:
        check_rate_limit("fusion_control_library")
        control_type = validate_enum(control_type, {"Preventive", "Detective", "Corrective"}, "control_type", default="")
        status = validate_enum(status, {"Active", "Inactive", "All"}, "status", default="Active")
        audit_log("fusion_control_library", control_type=control_type, status=status)
        return json.dumps(
            get_control_library(control_type=control_type, category=category, status=status),
            indent=2,
        )
    except Exception as exc:
        return f"Error fetching control library: {exc}"


@mcp.tool()
def fusion_control_results(
    date_from: str = "",
    date_to: str = "",
    effectiveness: str = "",
) -> str:
    """
    Return Oracle Fusion control test results (operating effectiveness evidence).

    Each result includes the control tested, testing date, tester, conclusion,
    effectiveness rating, and number of exceptions noted.

    Useful for pulling evidence to support internal audit workpapers.

    Args:
        date_from:     ISO date YYYY-MM-DD — results on or after this date (empty = no limit)
        date_to:       ISO date YYYY-MM-DD — results up to this date (empty = today)
        effectiveness: Effective | Partially Effective | Ineffective (empty = all)
    """
    if not is_configured():
        return _NOT_CONFIGURED
    try:
        check_rate_limit("fusion_control_results")
        effectiveness = validate_enum(effectiveness, {"Effective", "Partially Effective", "Ineffective"}, "effectiveness", default="")
        audit_log("fusion_control_results", date_from=date_from, date_to=date_to, effectiveness=effectiveness)
        return json.dumps(
            get_control_results(date_from=date_from, date_to=date_to, effectiveness=effectiveness),
            indent=2,
        )
    except Exception as exc:
        return f"Error fetching control results: {exc}"


@mcp.tool()
def fusion_control_issues(
    status: str = "Open",
    severity: str = "",
    date_from: str = "",
) -> str:
    """
    Return open control deficiencies and their remediation plans from Oracle RMCS.

    Each issue includes severity (Critical / High / Medium / Low), RAG status,
    root cause, remediation plan, owner, and due date.

    Args:
        status:    Open | Closed | All (default Open)
        severity:  Critical | High | Medium | Low (empty = all severities)
        date_from: ISO date YYYY-MM-DD — issues raised on or after this date
    """
    if not is_configured():
        return _NOT_CONFIGURED
    try:
        check_rate_limit("fusion_control_issues")
        status = validate_enum(status, {"Open", "Closed", "All"}, "status", default="Open")
        severity = validate_enum(severity, {"Critical", "High", "Medium", "Low"}, "severity", default="")
        audit_log("fusion_control_issues", status=status, severity=severity)
        return json.dumps(
            get_control_issues(status=status, severity=severity, date_from=date_from),
            indent=2,
        )
    except Exception as exc:
        return f"Error fetching control issues: {exc}"


@mcp.tool()
def fusion_user_roles(
    username: str = "",
    role_name: str = "",
) -> str:
    """
    Return user-to-role assignments from Oracle Fusion (access control listing).

    Queries the SCIM 2.0 API.

    - Pass username to see all roles assigned to a specific user.
    - Pass role_name to see all users who hold that role.
    - Pass neither to return a broad listing of all user-role pairs.

    Useful for access control reviews, user access certifications, and
    privilege escalation checks.

    Args:
        username:  Oracle Fusion username or partial email (empty = all users)
        role_name: role display name filter (empty = all roles)
    """
    if not is_configured():
        return _NOT_CONFIGURED
    try:
        check_rate_limit("fusion_user_roles")
        audit_log("fusion_user_roles", username=username or "(all)", role_name=role_name or "(all)")
        return json.dumps(
            get_user_roles(username=username, role_name=role_name),
            indent=2,
        )
    except Exception as exc:
        return f"Error fetching user roles: {exc}"


@mcp.tool()
def fusion_sod_violations(
    status: str = "Open",
    risk_level: str = "",
) -> str:
    """
    Return segregation-of-duties (SOD) policy violations from Oracle RMCS.

    Each violation identifies the user, the conflicting role pair, the SOD
    policy breached, risk level, and any mitigating control in place.

    Args:
        status:     Open | Resolved | All (default Open)
        risk_level: High | Medium | Low (empty = all risk levels)
    """
    if not is_configured():
        return _NOT_CONFIGURED
    try:
        check_rate_limit("fusion_sod_violations")
        status = validate_enum(status, {"Open", "Resolved", "All"}, "status", default="Open")
        risk_level = validate_enum(risk_level, {"High", "Medium", "Low"}, "risk_level", default="")
        audit_log("fusion_sod_violations", status=status, risk_level=risk_level)
        return json.dumps(
            get_sod_violations(status=status, risk_level=risk_level),
            indent=2,
        )
    except Exception as exc:
        return f"Error fetching SOD violations: {exc}"


@mcp.tool()
def fusion_audit_events(
    module: str = "",
    date_from: str = "",
    date_to: str = "",
    event_type: str = "",
    username: str = "",
) -> str:
    """
    Return transaction audit trail events from Oracle Fusion FSCM modules.

    Useful for control testing (confirming a control fired) or anomaly detection
    (finding activity that should have been prevented or approved).

    Module codes:
      FIN_AP  Accounts Payable     FIN_AR  Accounts Receivable
      FIN_GL  General Ledger       FIN_FA  Fixed Assets
      PRC     Procurement          HCM     Human Capital Management

    Args:
        module:     Fusion module code (empty = all modules)
        date_from:  ISO datetime YYYY-MM-DDTHH:MM:SS (empty = last 30 days)
        date_to:    ISO datetime YYYY-MM-DDTHH:MM:SS (empty = now)
        event_type: Create | Update | Delete (empty = all event types)
        username:   filter by the user who performed the action
    """
    if not is_configured():
        return _NOT_CONFIGURED
    try:
        check_rate_limit("fusion_audit_events")
        module = validate_enum(module, {"FIN_AP", "FIN_AR", "FIN_GL", "FIN_FA", "PRC", "HCM"}, "module", default="")
        event_type = validate_enum(event_type, {"Create", "Update", "Delete"}, "event_type", default="")
        import re as _re
        if username and not _re.match(r'^[\w@.\-]{1,128}$', username):
            return "Error: username contains invalid characters"
        audit_log("fusion_audit_events", module=module, event_type=event_type, username=username or "(all)")
        return json.dumps(
            get_audit_events(
                module=module,
                date_from=date_from,
                date_to=date_to,
                event_type=event_type,
                username=username,
            ),
            indent=2,
        )
    except Exception as exc:
        return f"Error fetching audit events: {exc}"


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
