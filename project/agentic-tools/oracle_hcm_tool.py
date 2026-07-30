#!/usr/bin/env python3
"""
Oracle Fusion HCM Tool — Hire-to-Retire continuous audit

Same Oracle Fusion Cloud tenant/credentials as oracle_fusion_tool.py (ERP/RMCS
controls), just the HCM REST API family instead of FSCM/RMCS — see that
module's docstring for the auth model. Kept as a separate file rather than
folded into oracle_fusion_tool.py because it targets a materially different
domain (payroll/workforce) with its own checks, matching how sap_hana_tool.py
and dynamics365_tool.py are separate files despite all being "ERP connectors".

Checks (each produces 0+ poll-connector events, one per finding):
  - Ghost employees:        active payroll run entry for a terminated worker
  - Unauthorized pay changes: pay-rate change beyond threshold, no 2nd approver
  - Access retention:       "Revoke System Access" offboarding task not
                             completed within the expected window post-termination

Required environment variables (standalone/env-var mode only — UI-configured
connectors pass base_url/credentials explicitly, see pull_events() below):
  ORACLE_FUSION_HOST      e.g. https://mycompany.fa.us6.oraclecloud.com
  ORACLE_FUSION_USERNAME  service account (ignored when using OAuth)
  ORACLE_FUSION_PASSWORD  service account password (ignored when using OAuth)

Optional:
  ORACLE_FUSION_CLIENT_ID / ORACLE_FUSION_CLIENT_SECRET  OAuth 2.0 instead of Basic
  ORACLE_HCM_API_VERSION                                  defaults to 11.13.18.05
  ORACLE_HCM_PAY_RATE_THRESHOLD_PCT                       defaults to 25.0
  ORACLE_HCM_ACCESS_REVOKE_WINDOW_DAYS                    defaults to 7
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from oracle_fusion_tool import OracleFusionClient

logger = logging.getLogger(__name__)

_API_VERSION = os.environ.get("ORACLE_HCM_API_VERSION", "11.13.18.05")
_PAY_RATE_THRESHOLD_PCT = float(os.environ.get("ORACLE_HCM_PAY_RATE_THRESHOLD_PCT", "25.0"))
_ACCESS_REVOKE_WINDOW_DAYS = int(os.environ.get("ORACLE_HCM_ACCESS_REVOKE_WINDOW_DAYS", "7"))


def _hcm_url(client: OracleFusionClient, path: str) -> str:
    return f"{client.host}/hcmRestApi/resources/{_API_VERSION}/{path.lstrip('/')}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_between(earlier_iso: str, later_iso: Optional[str] = None) -> Optional[int]:
    """Whole days from earlier_iso to later_iso (default: now). None on bad input."""
    try:
        earlier = datetime.fromisoformat(earlier_iso.replace("Z", "+00:00"))
        later = (
            datetime.fromisoformat(later_iso.replace("Z", "+00:00"))
            if later_iso else datetime.now(timezone.utc)
        )
        if earlier.tzinfo is None:
            earlier = earlier.replace(tzinfo=timezone.utc)
        if later.tzinfo is None:
            later = later.replace(tzinfo=timezone.utc)
        return (later - earlier).days
    except (ValueError, AttributeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Ghost employees
# ─────────────────────────────────────────────────────────────────────────────

def check_ghost_employees(
    since_iso: Optional[str] = None,
    max_items: int = 500,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """Cross-reference recent payroll run entries against terminated workers.
    A payroll entry for a worker whose termination date precedes the pay
    period end date is a ghost-employee pattern."""
    c = client or OracleFusionClient()

    try:
        terminated = c._get_all(
            _hcm_url(c, "workers"),
            params={"q": "terminationDate is not null", "fields": "PersonId,PersonNumber,DisplayName,TerminationDate"},
            max_items=max_items,
        )
        term_by_id = {
            str(w.get("PersonId") or w.get("personId")): w
            for w in terminated if w.get("TerminationDate") or w.get("terminationDate")
        }
        if not term_by_id:
            return {"findings": [], "count": 0}

        params: dict[str, Any] = {}
        if since_iso:
            params["q"] = f"PayPeriodEndDate>='{since_iso[:10]}'"
        pay_runs = c._get_all(_hcm_url(c, "payrollRunResults"), params=params, max_items=max_items)
    except Exception as exc:
        return {"error": str(exc), "findings": [], "count": 0}

    findings = []
    for run in pay_runs:
        person_id = str(run.get("PersonId") or run.get("personId") or "")
        worker = term_by_id.get(person_id)
        if not worker:
            continue
        term_date = worker.get("TerminationDate") or worker.get("terminationDate")
        period_end = run.get("PayPeriodEndDate") or run.get("payPeriodEndDate")
        if term_date and period_end and period_end > term_date:
            findings.append({
                "person_id":       person_id,
                "person_number":   worker.get("PersonNumber") or worker.get("personNumber"),
                "display_name":    worker.get("DisplayName") or worker.get("displayName"),
                "termination_date": term_date,
                "pay_period_end":  period_end,
                "run_id":          run.get("PayrollRunResultId") or run.get("payrollRunResultId"),
            })

    return {"source": "Oracle Fusion HCM — Payroll vs. Termination Cross-Check",
            "fetched_at": _now(), "count": len(findings), "findings": findings}


# ─────────────────────────────────────────────────────────────────────────────
# Unauthorized pay-rate changes
# ─────────────────────────────────────────────────────────────────────────────

def check_pay_rate_changes(
    since_iso: Optional[str] = None,
    threshold_pct: float = _PAY_RATE_THRESHOLD_PCT,
    max_items: int = 500,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """Fetch salary/compensation change history and flag changes beyond
    threshold_pct that lack a second approver."""
    c = client or OracleFusionClient()

    params: dict[str, Any] = {}
    if since_iso:
        params["q"] = f"EffectiveDate>='{since_iso[:10]}'"
    try:
        changes = c._get_all(_hcm_url(c, "salaryHistory"), params=params, max_items=max_items)
    except Exception as exc:
        return {"error": str(exc), "findings": [], "count": 0}

    findings = []
    for ch in changes:
        prior = ch.get("PriorSalaryAmount") or ch.get("priorSalaryAmount")
        new   = ch.get("SalaryAmount") or ch.get("salaryAmount")
        if not prior or not new or prior <= 0:
            continue
        pct = round((new - prior) / prior * 100, 2)
        approvers = ch.get("Approvers") or ch.get("approvers") or []
        if abs(pct) >= threshold_pct and len(approvers) < 2:
            findings.append({
                "person_id":     ch.get("PersonId") or ch.get("personId"),
                "person_number": ch.get("PersonNumber") or ch.get("personNumber"),
                "display_name":  ch.get("DisplayName") or ch.get("displayName"),
                "prior_pay_rate": prior,
                "new_pay_rate":   new,
                "pay_rate_change_pct": pct,
                "approver_count": len(approvers),
                "effective_date": ch.get("EffectiveDate") or ch.get("effectiveDate"),
            })

    return {"source": "Oracle Fusion HCM — Salary Change History",
            "fetched_at": _now(), "threshold_pct": threshold_pct,
            "count": len(findings), "findings": findings}


# ─────────────────────────────────────────────────────────────────────────────
# Terminated-employee access retention
# ─────────────────────────────────────────────────────────────────────────────

def check_access_retention(
    window_days: int = _ACCESS_REVOKE_WINDOW_DAYS,
    max_items: int = 500,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """Fetch the offboarding checklist's "Revoke System Access" task per
    recently-terminated worker and flag ones still incomplete past window_days."""
    c = client or OracleFusionClient()

    try:
        tasks = c._get_all(
            _hcm_url(c, "offboardingChecklistTasks"),
            params={"q": "TaskName='Revoke System Access'"},
            max_items=max_items,
        )
    except Exception as exc:
        return {"error": str(exc), "findings": [], "count": 0}

    findings = []
    for t in tasks:
        if t.get("Completed") or t.get("completed"):
            continue
        term_date = t.get("TerminationDate") or t.get("terminationDate")
        if not term_date:
            continue
        days = _days_between(term_date)
        if days is not None and days > window_days:
            findings.append({
                "person_id":        t.get("PersonId") or t.get("personId"),
                "person_number":    t.get("PersonNumber") or t.get("personNumber"),
                "display_name":     t.get("DisplayName") or t.get("displayName"),
                "termination_date": term_date,
                "days_since_termination": days,
            })

    return {"source": "Oracle Fusion HCM — Offboarding Checklist",
            "fetched_at": _now(), "window_days": window_days,
            "count": len(findings), "findings": findings}


# ─────────────────────────────────────────────────────────────────────────────
# connector_poller adapter interface
# ─────────────────────────────────────────────────────────────────────────────
# Same uniform shape every adapter must produce — see oracle_fusion_tool.py's
# pull_events() docstring for the full contract explanation.

def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    client = OracleFusionClient(
        host=base_url,
        username=credentials.get("username"),
        password=credentials.get("password"),
        client_id=credentials.get("client_id"),
        client_secret=credentials.get("client_secret"),
    )
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else None
    today = datetime.now(timezone.utc).date().isoformat()
    events: list[dict] = []

    ghosts = check_ghost_employees(since_iso=since_iso, client=client)
    if ghosts.get("error"):
        raise RuntimeError(ghosts["error"])
    for f in ghosts["findings"]:
        events.append({
            "event_id":   f"ghost:{f['person_id']}:{f['run_id']}",
            "event_type": "ghost_employee_suspected",
            "actor":      "oracle_hcm_tool",
            "action":     "payroll_run_vs_termination_check",
            "resource":   f"worker/{f['person_number'] or f['person_id']}",
            "severity":   "HIGH",
            "raw_payload": {
                "ghost_employee_suspected": True,
                "payroll_detail": {
                    "employee_id": f["person_number"] or f["person_id"],
                    "termination_date": f["termination_date"],
                    "pay_period_end": f["pay_period_end"],
                },
            },
        })

    pay_changes = check_pay_rate_changes(since_iso=since_iso, client=client)
    if pay_changes.get("error"):
        raise RuntimeError(pay_changes["error"])
    for f in pay_changes["findings"]:
        events.append({
            "event_id":   f"payrate:{f['person_id']}:{f['effective_date']}",
            "event_type": "unauthorized_pay_rate_change",
            "actor":      "oracle_hcm_tool",
            "action":     "salary_change_audit",
            "resource":   f"worker/{f['person_number'] or f['person_id']}",
            "severity":   "HIGH",
            "raw_payload": {
                "unauthorized_pay_rate_change": True,
                "payroll_detail": {
                    "employee_id": f["person_number"] or f["person_id"],
                    "prior_pay_rate": f["prior_pay_rate"],
                    "new_pay_rate": f["new_pay_rate"],
                    "pay_rate_change_pct": f["pay_rate_change_pct"],
                    "second_approver": f["approver_count"] >= 2,
                },
            },
        })

    access = check_access_retention(client=client)
    if access.get("error"):
        raise RuntimeError(access["error"])
    for f in access["findings"]:
        events.append({
            "event_id":   f"access-retained:{f['person_id']}:{today}",
            "event_type": "terminated_employee_access_retained",
            "actor":      "oracle_hcm_tool",
            "action":     "offboarding_checklist_audit",
            "resource":   f"worker/{f['person_number'] or f['person_id']}",
            "severity":   "MEDIUM",
            "raw_payload": {
                "terminated_employee_access_retained": True,
                "payroll_detail": {
                    "employee_id": f["person_number"] or f["person_id"],
                    "termination_date": f["termination_date"],
                    "days_since_termination": f["days_since_termination"],
                },
            },
        })

    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity/credentials with a minimal real call."""
    try:
        client = OracleFusionClient(
            host=base_url,
            username=credentials.get("username"),
            password=credentials.get("password"),
            client_id=credentials.get("client_id"),
            client_secret=credentials.get("client_secret"),
        )
        result = client._get_all(_hcm_url(client, "workers"), params={}, max_items=1)
        return True, f"Connected — {len(result)} worker record(s) visible in test window"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
