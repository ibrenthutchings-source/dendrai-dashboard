#!/usr/bin/env python3
"""
Oracle Fusion Controls Tool

Pulls automated control data from Oracle Fusion Cloud via REST API.

Supported data categories:
  - Control library        (Oracle Risk Management Cloud — RMCS)
  - Control test results   (RMCS)
  - Control issues         (RMCS — open deficiencies)
  - User role assignments  (FSCM / SCIM access controls)
  - SOD violations         (RMCS segregation-of-duties policies)
  - Audit trail events     (FSCM audit service)
  - Control health summary (aggregated view across all categories)

Authentication:
  Basic auth (username + password) is used by default.
  Set ORACLE_FUSION_CLIENT_ID + ORACLE_FUSION_CLIENT_SECRET to use
  OAuth 2.0 Client Credentials instead — recommended for production.

Required environment variables:
  ORACLE_FUSION_HOST      e.g. https://mycompany.fa.us6.oraclecloud.com
  ORACLE_FUSION_USERNAME  service account (ignored when using OAuth)
  ORACLE_FUSION_PASSWORD  service account password (ignored when using OAuth)

Optional environment variables:
  ORACLE_FUSION_CLIENT_ID      OAuth 2.0 client ID
  ORACLE_FUSION_CLIENT_SECRET  OAuth 2.0 client secret
  ORACLE_FUSION_API_VERSION    defaults to 11.13.18.05
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

try:
    import requests
    from requests.auth import HTTPBasicAuth
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

_API_VERSION = os.environ.get("ORACLE_FUSION_API_VERSION", "11.13.18.05")

# RAG thresholds for control effectiveness scoring
_EFFECTIVENESS_RAG = {
    "Effective":           "G",
    "Partially Effective": "A",
    "Ineffective":         "R",
    "Not Evaluated":       "A",
}

_ISSUE_SEVERITY_RAG = {
    "Critical":   "R",
    "High":       "R",
    "Medium":     "A",
    "Low":        "G",
    "Informational": "G",
}

# Map Oracle Fusion control categories to Dendrai risk categories
_CONTROL_CATEGORY_MAP = {
    "Financial Reporting":  "Financial Reporting",
    "Compliance":           "Regulatory",
    "Operational":          "Operational",
    "IT General Controls":  "Cybersecurity",
    "Access Management":    "Cybersecurity",
    "Fraud":                "Financial Reporting",
    "Revenue":              "Revenue",
    "Procurement":          "Operational",
    "Payroll":              "Operational",
}


# ─────────────────────────────────────────────────────────────────────────────
# HTTP client
# ─────────────────────────────────────────────────────────────────────────────

class OracleFusionClient:
    """Thin REST client for Oracle Fusion Cloud APIs."""

    def __init__(
        self,
        host: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        timeout: int = 30,
    ):
        if not _HAS_REQUESTS:
            raise ImportError("requests library required: pip install requests")

        self.host = (host or os.environ.get("ORACLE_FUSION_HOST", "")).rstrip("/")
        if not self.host:
            raise ValueError(
                "Oracle Fusion host not configured. "
                "Set ORACLE_FUSION_HOST env var or pass host= parameter."
            )

        self._client_id     = client_id     or os.environ.get("ORACLE_FUSION_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("ORACLE_FUSION_CLIENT_SECRET", "")
        self._username      = username      or os.environ.get("ORACLE_FUSION_USERNAME", "")
        self._password      = password      or os.environ.get("ORACLE_FUSION_PASSWORD", "")
        self._timeout       = timeout
        self._oauth_token: Optional[str] = None

        self._session = requests.Session()
        self._session.headers.update({
            "Accept":       "application/json",
            "Content-Type": "application/json",
        })

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _auth(self) -> HTTPBasicAuth | None:
        """Return Basic auth if OAuth is not configured."""
        if self._client_id:
            return None
        if self._username:
            return HTTPBasicAuth(self._username, self._password)
        return None

    def _ensure_oauth_token(self) -> None:
        if self._oauth_token:
            return
        token_url = f"{self.host}/oauth2/v1/token"
        resp = self._session.post(
            token_url,
            data={
                "grant_type":    "client_credentials",
                "client_id":     self._client_id,
                "client_secret": self._client_secret,
                "scope":         "urn:opc:resource:consumer::all",
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        self._oauth_token = resp.json()["access_token"]
        self._session.headers["Authorization"] = f"Bearer {self._oauth_token}"

    def _request_headers(self) -> dict:
        if self._client_id:
            self._ensure_oauth_token()
            return {}
        return {}

    # ── Pagination helper ─────────────────────────────────────────────────────

    def _get_all(
        self,
        url: str,
        params: Optional[dict] = None,
        max_items: int = 500,
    ) -> list[dict]:
        """GET with automatic Oracle-style offset pagination."""
        params = dict(params or {})
        params.setdefault("limit", min(100, max_items))
        params.setdefault("offset", 0)

        items: list[dict] = []
        while True:
            resp = self._session.get(
                url,
                params=params,
                auth=self._auth(),
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            batch = data.get("items", data if isinstance(data, list) else [])
            items.extend(batch)

            if not data.get("hasMore", False) or len(items) >= max_items:
                break
            params["offset"] = params.get("offset", 0) + len(batch)

        return items[:max_items]

    def _fscm_url(self, path: str) -> str:
        return f"{self.host}/fscmRestApi/resources/{_API_VERSION}/{path.lstrip('/')}"

    def _rmcs_url(self, path: str) -> str:
        return f"{self.host}/rmcsRestApi/resources/{_API_VERSION}/{path.lstrip('/')}"

    def _scim_url(self, path: str) -> str:
        return f"{self.host}/admin/v1/{path.lstrip('/')}"


# ─────────────────────────────────────────────────────────────────────────────
# Control library
# ─────────────────────────────────────────────────────────────────────────────

def get_control_library(
    control_type: str = "",
    category: str = "",
    status: str = "Active",
    max_items: int = 200,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """
    Fetch the Oracle Risk Management Cloud control library.

    Returns all active control definitions, including control type
    (Preventive / Detective / Corrective), frequency, and risk category.

    Args:
        control_type: filter by Preventive / Detective / Corrective (empty = all)
        category:     filter by control category string (empty = all)
        status:       Active | Inactive | All (default Active)
        max_items:    pagination cap
    """
    c = client or OracleFusionClient()

    params: dict[str, Any] = {}
    filters: list[str] = []
    if status and status.lower() != "all":
        filters.append(f"Status='{status}'")
    if control_type:
        filters.append(f"ControlType='{control_type}'")
    if category:
        filters.append(f"Category='{category}'")
    if filters:
        params["q"] = ";".join(filters)

    try:
        items = c._get_all(c._rmcs_url("controls"), params=params, max_items=max_items)
    except Exception as exc:
        return {"error": str(exc), "controls": [], "count": 0}

    normalized = []
    for ctrl in items:
        raw_cat   = ctrl.get("Category", ctrl.get("category", ""))
        raw_eff   = ctrl.get("ControlEffectiveness", ctrl.get("effectiveness", "Not Evaluated"))
        normalized.append({
            "control_id":    ctrl.get("ControlId",    ctrl.get("controlId", "")),
            "name":          ctrl.get("ControlName",  ctrl.get("name", "")),
            "description":   ctrl.get("Description",  ctrl.get("description", "")),
            "control_type":  ctrl.get("ControlType",  ctrl.get("controlType", "")),
            "category":      raw_cat,
            "risk_category": _CONTROL_CATEGORY_MAP.get(raw_cat, "Operational"),
            "frequency":     ctrl.get("Frequency",    ctrl.get("frequency", "")),
            "owner":         ctrl.get("Owner",         ctrl.get("owner", "")),
            "status":        ctrl.get("Status",        ctrl.get("status", "")),
            "effectiveness": raw_eff,
            "rag":           _EFFECTIVENESS_RAG.get(raw_eff, "A"),
            "last_tested":   ctrl.get("LastTestedDate", ctrl.get("lastTestedDate", "")),
            "risk_ids":      ctrl.get("LinkedRisks",   ctrl.get("linkedRisks", [])),
        })

    rag_counts = {"R": 0, "A": 0, "G": 0}
    for ctrl in normalized:
        rag_counts[ctrl["rag"]] = rag_counts.get(ctrl["rag"], 0) + 1

    return {
        "source":    "Oracle Risk Management Cloud — Control Library",
        "fetched_at": _now(),
        "count":     len(normalized),
        "rag_summary": rag_counts,
        "controls":  normalized,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Control test results
# ─────────────────────────────────────────────────────────────────────────────

def get_control_results(
    date_from: str = "",
    date_to: str = "",
    effectiveness: str = "",
    max_items: int = 200,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """
    Fetch control test results (evidence of operating effectiveness).

    Args:
        date_from:     ISO date string YYYY-MM-DD (empty = no lower bound)
        date_to:       ISO date string YYYY-MM-DD (empty = today)
        effectiveness: filter by Effective / Partially Effective / Ineffective
        max_items:     pagination cap
    """
    c = client or OracleFusionClient()

    params: dict[str, Any] = {}
    filters: list[str] = []
    if date_from:
        filters.append(f"TestingDate>='{date_from}'")
    if date_to:
        filters.append(f"TestingDate<='{date_to}'")
    if effectiveness:
        filters.append(f"OperatingEffectiveness='{effectiveness}'")
    if filters:
        params["q"] = ";".join(filters)

    try:
        items = c._get_all(c._rmcs_url("controlResults"), params=params, max_items=max_items)
    except Exception as exc:
        return {"error": str(exc), "results": [], "count": 0}

    normalized = []
    for r in items:
        raw_eff = r.get("OperatingEffectiveness", r.get("effectiveness", "Not Evaluated"))
        normalized.append({
            "result_id":       r.get("ResultId",      r.get("resultId", "")),
            "control_id":      r.get("ControlId",     r.get("controlId", "")),
            "control_name":    r.get("ControlName",   r.get("controlName", "")),
            "testing_date":    r.get("TestingDate",   r.get("testingDate", "")),
            "effectiveness":   raw_eff,
            "rag":             _EFFECTIVENESS_RAG.get(raw_eff, "A"),
            "tester":          r.get("Tester",        r.get("tester", "")),
            "conclusion":      r.get("Conclusion",    r.get("conclusion", "")),
            "exceptions_noted": r.get("ExceptionsNoted", r.get("exceptionsNoted", 0)),
            "sample_size":     r.get("SampleSize",    r.get("sampleSize", 0)),
        })

    pass_count = sum(1 for r in normalized if r["rag"] == "G")
    fail_count = sum(1 for r in normalized if r["rag"] == "R")
    partial_count = sum(1 for r in normalized if r["rag"] == "A")

    return {
        "source":    "Oracle Risk Management Cloud — Control Results",
        "fetched_at": _now(),
        "period":    {"from": date_from, "to": date_to or _today()},
        "count":     len(normalized),
        "summary": {
            "effective":           pass_count,
            "partially_effective": partial_count,
            "ineffective":         fail_count,
            "pass_rate_pct":       round(pass_count / len(normalized) * 100, 1) if normalized else None,
        },
        "results": normalized,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Control issues / deficiencies
# ─────────────────────────────────────────────────────────────────────────────

def get_control_issues(
    status: str = "Open",
    severity: str = "",
    date_from: str = "",
    max_items: int = 200,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """
    Fetch open control deficiencies and remediation plans from Oracle RMCS.

    Args:
        status:    Open | Closed | All (default Open)
        severity:  Critical / High / Medium / Low (empty = all)
        date_from: ISO date string — issues raised on or after this date
        max_items: pagination cap
    """
    c = client or OracleFusionClient()

    params: dict[str, Any] = {}
    filters: list[str] = []
    if status and status.lower() != "all":
        filters.append(f"Status='{status}'")
    if severity:
        filters.append(f"Severity='{severity}'")
    if date_from:
        filters.append(f"RaisedDate>='{date_from}'")
    if filters:
        params["q"] = ";".join(filters)

    try:
        items = c._get_all(c._rmcs_url("issues"), params=params, max_items=max_items)
    except Exception as exc:
        return {"error": str(exc), "issues": [], "count": 0}

    normalized = []
    for issue in items:
        raw_sev = issue.get("Severity", issue.get("severity", "Medium"))
        normalized.append({
            "issue_id":       issue.get("IssueId",       issue.get("issueId", "")),
            "title":          issue.get("IssueName",     issue.get("name", issue.get("title", ""))),
            "description":    issue.get("Description",   issue.get("description", "")),
            "severity":       raw_sev,
            "rag":            _ISSUE_SEVERITY_RAG.get(raw_sev, "A"),
            "status":         issue.get("Status",        issue.get("status", "")),
            "control_id":     issue.get("ControlId",     issue.get("controlId", "")),
            "control_name":   issue.get("ControlName",   issue.get("controlName", "")),
            "owner":          issue.get("AssignedTo",    issue.get("owner", "")),
            "raised_date":    issue.get("RaisedDate",    issue.get("raisedDate", "")),
            "due_date":       issue.get("DueDate",       issue.get("dueDate", "")),
            "remediation":    issue.get("RemediationPlan", issue.get("remediationPlan", "")),
            "root_cause":     issue.get("RootCause",     issue.get("rootCause", "")),
        })

    return {
        "source":    "Oracle Risk Management Cloud — Issues",
        "fetched_at": _now(),
        "filter":    {"status": status, "severity": severity},
        "count":     len(normalized),
        "rag_summary": {
            "R": sum(1 for i in normalized if i["rag"] == "R"),
            "A": sum(1 for i in normalized if i["rag"] == "A"),
            "G": sum(1 for i in normalized if i["rag"] == "G"),
        },
        "issues": normalized,
    }


# ─────────────────────────────────────────────────────────────────────────────
# User role assignments (access controls)
# ─────────────────────────────────────────────────────────────────────────────

def get_user_roles(
    username: str = "",
    role_name: str = "",
    max_items: int = 500,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """
    Fetch user-to-role assignments from Oracle Fusion via SCIM 2.0 API.

    When username is provided, returns roles for that specific user.
    When role_name is provided, returns all users holding that role.
    When neither is provided, returns a summary of all role assignments.

    Args:
        username:  Oracle Fusion username or email (partial match supported)
        role_name: role name filter (partial match supported)
        max_items: pagination cap
    """
    c = client or OracleFusionClient()

    try:
        if username:
            # Single-user lookup via SCIM
            filter_str = f'userName sw "{username}"'
            items = c._get_all(
                c._scim_url("Users"),
                params={"filter": filter_str, "attributes": "userName,displayName,emails,groups"},
                max_items=max_items,
            )
        elif role_name:
            # Role lookup — find group then list members
            filter_str = f'displayName co "{role_name}"'
            items = c._get_all(
                c._scim_url("Groups"),
                params={"filter": filter_str, "attributes": "displayName,members"},
                max_items=max_items,
            )
        else:
            # Broad listing — all users with their groups
            items = c._get_all(
                c._scim_url("Users"),
                params={"attributes": "userName,displayName,groups"},
                max_items=max_items,
            )
    except Exception as exc:
        return {"error": str(exc), "assignments": [], "count": 0}

    assignments = []
    for item in items:
        groups = item.get("groups", item.get("members", []))
        if username or not role_name:
            for g in groups:
                assignments.append({
                    "username":     item.get("userName", item.get("userName", "")),
                    "display_name": item.get("displayName", ""),
                    "email":        next(
                        (e.get("value") for e in item.get("emails", []) if e.get("primary")), ""
                    ),
                    "role":         g.get("display", g.get("value", "")),
                    "role_id":      g.get("value", ""),
                })
        else:
            for m in groups:
                assignments.append({
                    "role":         item.get("displayName", ""),
                    "username":     m.get("display", m.get("value", "")),
                    "member_id":    m.get("value", ""),
                })

    return {
        "source":     "Oracle Fusion SCIM 2.0 — User Role Assignments",
        "fetched_at": _now(),
        "filter":     {"username": username, "role_name": role_name},
        "count":      len(assignments),
        "assignments": assignments,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Segregation of Duties violations
# ─────────────────────────────────────────────────────────────────────────────

def get_sod_violations(
    status: str = "Open",
    risk_level: str = "",
    max_items: int = 200,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """
    Fetch SOD policy violations from Oracle Risk Management Cloud.

    Oracle RMCS stores SOD analysis results as a specialised type of issue.
    Each violation links a user, the conflicting role pair, and the SOD policy.

    Args:
        status:     Open | Resolved | All (default Open)
        risk_level: High / Medium / Low (empty = all)
        max_items:  pagination cap
    """
    c = client or OracleFusionClient()

    params: dict[str, Any] = {}
    filters: list[str] = []
    if status and status.lower() != "all":
        filters.append(f"ViolationStatus='{status}'")
    if risk_level:
        filters.append(f"RiskLevel='{risk_level}'")
    if filters:
        params["q"] = ";".join(filters)

    try:
        items = c._get_all(
            c._rmcs_url("segregationOfDutiesViolations"),
            params=params,
            max_items=max_items,
        )
    except Exception as exc:
        return {"error": str(exc), "violations": [], "count": 0}

    normalized = []
    for v in items:
        raw_level = v.get("RiskLevel", v.get("riskLevel", "Medium"))
        normalized.append({
            "violation_id":  v.get("ViolationId",    v.get("violationId", "")),
            "username":      v.get("UserName",        v.get("userName", "")),
            "user_display":  v.get("UserDisplayName", v.get("userDisplayName", "")),
            "policy_name":   v.get("PolicyName",      v.get("policyName", "")),
            "conflict_roles": v.get("ConflictingRoles", v.get("conflictingRoles", [])),
            "risk_level":    raw_level,
            "rag":           _ISSUE_SEVERITY_RAG.get(raw_level, "A"),
            "status":        v.get("ViolationStatus", v.get("status", "")),
            "detected_date": v.get("DetectedDate",    v.get("detectedDate", "")),
            "mitigating_control": v.get("MitigatingControl", v.get("mitigatingControl", "")),
            "remediation":   v.get("RemediationAction", v.get("remediationAction", "")),
        })

    return {
        "source":    "Oracle Risk Management Cloud — SOD Violations",
        "fetched_at": _now(),
        "filter":    {"status": status, "risk_level": risk_level},
        "count":     len(normalized),
        "rag_summary": {
            "R": sum(1 for v in normalized if v["rag"] == "R"),
            "A": sum(1 for v in normalized if v["rag"] == "A"),
            "G": sum(1 for v in normalized if v["rag"] == "G"),
        },
        "violations": normalized,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Treasury & Cash Management (FSCM Payments / Cash Management modules)
# ─────────────────────────────────────────────────────────────────────────────
# Same Oracle Fusion Cloud tenant/host as the ERP control-library functions
# above — Treasury/Cash Management is part of core FSCM (module codes PAY,
# CE), unlike HCM which is a separate REST API family (see oracle_hcm_tool.py).

_WIRE_APPROVAL_MIN = 2  # dual-approval is the control every outbound wire is expected to carry
_BANK_RECON_SLA_DAYS = 5  # business days after statement date a reconciliation is due


def check_wire_transfer_approvals(
    date_from: str = "",
    min_approvers: int = _WIRE_APPROVAL_MIN,
    max_items: int = 200,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """Fetch recent outbound wire payment process requests and flag any that
    cleared with fewer than min_approvers approvals on file."""
    c = client or OracleFusionClient()

    params: dict[str, Any] = {"q": "PaymentMethod='WIRE'"}
    if date_from:
        params["q"] += f";PaymentDate>='{date_from}'"
    try:
        items = c._get_all(c._fscm_url("paymentProcessRequests"), params=params, max_items=max_items)
    except Exception as exc:
        return {"error": str(exc), "findings": [], "count": 0}

    findings = []
    for p in items:
        approvers = p.get("Approvers", p.get("approvers", []))
        if len(approvers) < min_approvers:
            findings.append({
                "payment_id": p.get("PaymentProcessRequestId", p.get("paymentProcessRequestId", "")),
                "amount":     p.get("PaymentAmount", p.get("paymentAmount", "")),
                "currency":   p.get("Currency", p.get("currency", "")),
                "approver_count": len(approvers),
                "payment_date": p.get("PaymentDate", p.get("paymentDate", "")),
            })

    return {"source": "Oracle Fusion FSCM — Payment Process Requests",
            "fetched_at": _now(), "count": len(findings), "findings": findings}


def check_bank_reconciliation_status(
    sla_days: int = _BANK_RECON_SLA_DAYS,
    max_items: int = 200,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """Fetch bank account reconciliation status and flag accounts overdue
    against sla_days from their last statement date."""
    c = client or OracleFusionClient()

    try:
        items = c._get_all(c._fscm_url("bankStatementReconciliations"), params={}, max_items=max_items)
    except Exception as exc:
        return {"error": str(exc), "findings": [], "count": 0}

    findings = []
    for r in items:
        status = (r.get("ReconciliationStatus") or r.get("reconciliationStatus") or "").upper()
        if status == "RECONCILED":
            continue
        stmt_date = r.get("StatementDate") or r.get("statementDate")
        days_overdue = _days_since(stmt_date, sla_days)
        if days_overdue is not None and days_overdue > 0:
            findings.append({
                "bank_account": r.get("BankAccountName", r.get("bankAccountName", "")),
                "last_reconciled_date": r.get("LastReconciledDate", r.get("lastReconciledDate", stmt_date)),
                "days_overdue": days_overdue,
            })

    return {"source": "Oracle Fusion FSCM — Bank Statement Reconciliations",
            "fetched_at": _now(), "sla_days": sla_days,
            "count": len(findings), "findings": findings}


def check_fx_hedge_documentation(
    max_items: int = 200,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """Fetch open FX hedge/derivative positions and flag ones missing
    completed hedge-accounting documentation (required for ASC 815 hedge
    accounting treatment). Resource path may need adjustment depending on
    which Treasury/Risk Management module configuration a given tenant runs —
    written to the same FSCM REST convention as the checks above."""
    c = client or OracleFusionClient()

    try:
        items = c._get_all(c._fscm_url("treasuryHedgeDesignations"), params={"q": "Status='Open'"}, max_items=max_items)
    except Exception as exc:
        return {"error": str(exc), "findings": [], "count": 0}

    findings = []
    for h in items:
        doc_status = (h.get("DocumentationStatus") or h.get("documentationStatus") or "").upper()
        if doc_status != "COMPLETE":
            findings.append({
                "hedge_id":       h.get("HedgeId", h.get("hedgeId", "")),
                "currency_pair":  h.get("CurrencyPair", h.get("currencyPair", "")),
                "notional_amount": h.get("NotionalAmount", h.get("notionalAmount", "")),
                "documentation_status": doc_status or "MISSING",
            })

    return {"source": "Oracle Fusion FSCM — Treasury Hedge Designations",
            "fetched_at": _now(), "count": len(findings), "findings": findings}


_VENDOR_CONCENTRATION_THRESHOLD_PCT = float(os.environ.get("VENDOR_CONCENTRATION_THRESHOLD_PCT", "25.0"))
_VENDOR_CONCENTRATION_WINDOW_DAYS = int(os.environ.get("VENDOR_CONCENTRATION_WINDOW_DAYS", "90"))


def check_vendor_concentration(
    date_from: str = "",
    threshold_pct: float = _VENDOR_CONCENTRATION_THRESHOLD_PCT,
    window_days: int = _VENDOR_CONCENTRATION_WINDOW_DAYS,
    max_items: int = 500,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """Aggregate trailing-window payment spend by supplier and flag any
    vendor whose share of total spend exceeds threshold_pct — VM-02 (Supply
    Chain Resilience) concentration risk."""
    c = client or OracleFusionClient()

    params: dict[str, Any] = {}
    if date_from:
        params["q"] = f"PaymentDate>='{date_from}'"
    try:
        items = c._get_all(c._fscm_url("paymentProcessRequests"), params=params, max_items=max_items)
    except Exception as exc:
        return {"error": str(exc), "findings": [], "count": 0}

    by_supplier: dict[str, float] = {}
    for p in items:
        supplier = p.get("SupplierName") or p.get("supplierName") or "Unknown"
        amount = p.get("PaymentAmount") or p.get("paymentAmount") or 0
        by_supplier[supplier] = by_supplier.get(supplier, 0) + float(amount)

    total = sum(by_supplier.values())
    findings = []
    if total > 0:
        for supplier, amount in by_supplier.items():
            pct = round(amount / total * 100, 2)
            if pct >= threshold_pct:
                findings.append({
                    "vendor_name": supplier, "amount": amount,
                    "concentration_pct": pct, "total_spend": total,
                })

    return {"source": "Oracle Fusion FSCM — Payment Process Requests (Concentration)",
            "fetched_at": _now(), "threshold_pct": threshold_pct, "window_days": window_days,
            "total_spend": total, "count": len(findings), "findings": findings}


def _days_since(date_str: Optional[str], sla_days: int) -> Optional[int]:
    """Days past sla_days since date_str. None on missing/bad input."""
    if not date_str:
        return None
    try:
        d = datetime.fromisoformat(date_str[:10])
        elapsed = (datetime.now(timezone.utc).date() - d.date()).days
        return elapsed - sla_days
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Audit trail events
# ─────────────────────────────────────────────────────────────────────────────

def get_audit_events(
    module: str = "",
    date_from: str = "",
    date_to: str = "",
    event_type: str = "",
    username: str = "",
    max_items: int = 500,
    client: Optional[OracleFusionClient] = None,
) -> dict:
    """
    Fetch Oracle Fusion audit trail events via the FSCM audit history REST API.

    Useful for control testing: proves that a control fired, or detects
    activity that should have been prevented.

    Args:
        module:     Fusion module code e.g. FIN_AP, FIN_AR, FIN_GL, HCM, PRC
        date_from:  ISO datetime string YYYY-MM-DDTHH:MM:SS (empty = last 30 days)
        date_to:    ISO datetime string (empty = now)
        event_type: e.g. Create, Update, Delete (empty = all)
        username:   filter by the user who performed the action
        max_items:  pagination cap
    """
    c = client or OracleFusionClient()

    params: dict[str, Any] = {}
    filters: list[str] = []
    if module:
        filters.append(f"ProductCode='{module}'")
    if date_from:
        filters.append(f"EventDate>='{date_from}'")
    if date_to:
        filters.append(f"EventDate<='{date_to}'")
    if event_type:
        filters.append(f"EventType='{event_type}'")
    if username:
        filters.append(f"UserName='{username}'")
    if filters:
        params["q"] = ";".join(filters)

    try:
        items = c._get_all(c._fscm_url("auditHistory"), params=params, max_items=max_items)
    except Exception as exc:
        return {"error": str(exc), "events": [], "count": 0}

    normalized = [
        {
            "event_id":     e.get("AuditId",      e.get("auditId",      e.get("eventId", ""))),
            "event_type":   e.get("EventType",     e.get("eventType",    "")),
            "event_date":   e.get("EventDate",     e.get("eventDate",    "")),
            "username":     e.get("UserName",      e.get("userName",     "")),
            "module":       e.get("ProductCode",   e.get("productCode",  "")),
            "object_type":  e.get("ObjectType",    e.get("objectType",   "")),
            "object_id":    e.get("ObjectId",      e.get("objectId",     "")),
            "description":  e.get("Description",   e.get("description",  "")),
            "ip_address":   e.get("IPAddress",     e.get("ipAddress",    "")),
            "before_values": e.get("BeforeValues", e.get("beforeValues", {})),
            "after_values":  e.get("AfterValues",  e.get("afterValues",  {})),
        }
        for e in items
    ]

    by_module: dict[str, int] = {}
    by_event_type: dict[str, int] = {}
    for e in normalized:
        by_module[e["module"]] = by_module.get(e["module"], 0) + 1
        by_event_type[e["event_type"]] = by_event_type.get(e["event_type"], 0) + 1

    return {
        "source":    "Oracle Fusion FSCM — Audit History",
        "fetched_at": _now(),
        "filter":    {"module": module, "date_from": date_from, "date_to": date_to,
                      "event_type": event_type, "username": username},
        "count":     len(normalized),
        "by_module":     by_module,
        "by_event_type": by_event_type,
        "events":    normalized,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Control health summary
# ─────────────────────────────────────────────────────────────────────────────

def get_control_summary(client: Optional[OracleFusionClient] = None) -> dict:
    """
    Aggregated control health overview across all Oracle Fusion control categories.

    Combines control library effectiveness, open issues, and SOD violations
    into a single executive-level dashboard object compatible with the
    Dendrai risk scoring schema.

    Designed to be called without arguments — uses environment variables for credentials.
    """
    c = client or OracleFusionClient()

    library = get_control_library(max_items=500, client=c)
    issues  = get_control_issues(status="Open", max_items=200, client=c)
    sod     = get_sod_violations(status="Open", max_items=200, client=c)

    lib_rag = library.get("rag_summary", {"R": 0, "A": 0, "G": 0})
    iss_rag = issues.get("rag_summary",  {"R": 0, "A": 0, "G": 0})
    sod_rag = sod.get("rag_summary",     {"R": 0, "A": 0, "G": 0})

    total_red   = lib_rag["R"] + iss_rag["R"] + sod_rag["R"]
    total_amber = lib_rag["A"] + iss_rag["A"] + sod_rag["A"]
    total_green = lib_rag["G"] + iss_rag["G"] + sod_rag["G"]
    total       = total_red + total_amber + total_green

    overall_rag = "G"
    if total_red > 0:
        overall_rag = "R"
    elif total_amber > 0:
        overall_rag = "A"

    # Score on 0–25 scale (Impact × Likelihood analogue)
    control_score = round(
        (total_red * 25 + total_amber * 12 + total_green * 0) / max(total, 1), 2
    )

    # Build per-category control health for the risk register
    risk_signals: list[dict] = []
    for ctrl in library.get("controls", []):
        if ctrl["rag"] in ("R", "A"):
            risk_signals.append({
                "src":           "Oracle Fusion",
                "label":         f"Control '{ctrl['name']}' rated {ctrl['effectiveness']}",
                "category":      ctrl["risk_category"],
                "delta":         ctrl["effectiveness"],
                "velocity":      1 if ctrl["rag"] == "A" else 2,
                "affectedRisks": [ctrl["risk_category"]],
            })

    for issue in issues.get("issues", []):
        risk_signals.append({
            "src":           "Oracle Fusion",
            "label":         f"Control issue: {issue['title']} ({issue['severity']})",
            "category":      "Operational",
            "delta":         issue["severity"],
            "velocity":      2 if issue["rag"] == "R" else 1,
            "affectedRisks": ["Operational", "Financial Reporting"],
        })

    for v in sod.get("violations", []):
        risk_signals.append({
            "src":           "Oracle Fusion",
            "label":         f"SOD violation: {v['policy_name']} — {v['user_display'] or v['username']}",
            "category":      "Cybersecurity",
            "delta":         v["risk_level"],
            "velocity":      2 if v["rag"] == "R" else 1,
            "affectedRisks": ["Cybersecurity", "Financial Reporting"],
        })

    # Errors from any sub-call
    errors = [
        s for s in [library.get("error"), issues.get("error"), sod.get("error")] if s
    ]

    return {
        "source":     "Oracle Fusion — Aggregated Control Health",
        "fetched_at": _now(),
        "overall_rag": overall_rag,
        "control_score": control_score,
        "rag_summary": {
            "R": total_red,
            "A": total_amber,
            "G": total_green,
            "total": total,
        },
        "categories": {
            "control_library": {
                "count":       library.get("count", 0),
                "rag_summary": lib_rag,
            },
            "open_issues": {
                "count":       issues.get("count", 0),
                "rag_summary": iss_rag,
            },
            "sod_violations": {
                "count":       sod.get("count", 0),
                "rag_summary": sod_rag,
            },
        },
        "risk_signals": risk_signals,
        "errors":        errors if errors else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def is_configured() -> bool:
    """Return True if ORACLE_FUSION_HOST is set."""
    return bool(os.environ.get("ORACLE_FUSION_HOST", ""))


# ─────────────────────────────────────────────────────────────────────────────
# connector_poller adapter interface
# ─────────────────────────────────────────────────────────────────────────────
# Thin wrappers so this module (which predates the connector framework and is
# also used standalone via /oracle-fusion/* REST endpoints + env-var config)
# can be registered in connector_poller._ADAPTERS. UI-configured connectors
# pass base_url/credentials explicitly rather than relying on
# ORACLE_FUSION_HOST/USERNAME/PASSWORD env vars.
#
# pull_events() returns the UNIFORM shape every adapter must produce —
# {event_id, event_type, actor, action, resource, severity, raw_payload} —
# so connector_poller.py can hand any adapter's output to
# mcp_governance._detect_system_flags/_ingest_system_event without knowing
# each vendor's native field names.

def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    """Pull audit events created since `since`, normalized to the uniform
    connector event shape (see module docstring above). Also runs the
    Treasury & Cash Management checks (wire dual-approval, bank recon SLA,
    FX hedge documentation) every tick — same connector, same tenant, no
    separate poll schedule needed."""
    client = OracleFusionClient(
        host=base_url,
        username=credentials.get("username"),
        password=credentials.get("password"),
        client_id=credentials.get("client_id"),
        client_secret=credentials.get("client_secret"),
    )
    date_from = since.strftime("%Y-%m-%dT%H:%M:%S") if since else ""
    result = get_audit_events(date_from=date_from, max_items=500, client=client)
    if result.get("error"):
        raise RuntimeError(result["error"])
    events = [
        {
            "event_id":    str(e.get("event_id") or ""),
            "event_type":  e.get("event_type") or "audit_event",
            "actor":       e.get("username") or "",
            "action":      e.get("event_type") or "",
            "resource":    f"{e.get('module', '')}/{e.get('object_type', '')}/{e.get('object_id', '')}".strip("/"),
            "severity":    "INFO",
            "raw_payload": e,
        }
        for e in result.get("events", [])
    ]

    min_approvers = int((extra_config or {}).get("wire_min_approvers") or _WIRE_APPROVAL_MIN)
    recon_sla_days = int((extra_config or {}).get("bank_recon_sla_days") or _BANK_RECON_SLA_DAYS)
    today = datetime.now(timezone.utc).date().isoformat()

    wires = check_wire_transfer_approvals(date_from=date_from, min_approvers=min_approvers, client=client)
    if wires.get("error"):
        raise RuntimeError(wires["error"])
    for f in wires["findings"]:
        events.append({
            "event_id":   f"wire:{f['payment_id']}",
            "event_type": "wire_transfer_single_approval",
            "actor":      "oracle_fusion_tool",
            "action":     "payment_approval_audit",
            "resource":   f"payment/{f['payment_id']}",
            "severity":   "HIGH",
            "raw_payload": {
                "wire_transfer_single_approval": True,
                "treasury_detail": {
                    "payment_id": f["payment_id"], "amount": f["amount"], "currency": f["currency"],
                    "approver_count": f["approver_count"],
                },
            },
        })

    recon = check_bank_reconciliation_status(sla_days=recon_sla_days, client=client)
    if recon.get("error"):
        raise RuntimeError(recon["error"])
    for f in recon["findings"]:
        events.append({
            "event_id":   f"bankrecon:{f['bank_account']}:{today}",
            "event_type": "bank_recon_overdue",
            "actor":      "oracle_fusion_tool",
            "action":     "bank_reconciliation_audit",
            "resource":   f"bank_account/{f['bank_account']}",
            "severity":   "HIGH",
            "raw_payload": {
                "bank_recon_overdue": True,
                "treasury_detail": {
                    "bank_account": f["bank_account"], "last_reconciled_date": f["last_reconciled_date"],
                    "days_overdue": f["days_overdue"],
                },
            },
        })

    hedges = check_fx_hedge_documentation(client=client)
    if hedges.get("error"):
        raise RuntimeError(hedges["error"])
    for f in hedges["findings"]:
        events.append({
            "event_id":   f"fxhedge:{f['hedge_id']}:{today}",
            "event_type": "fx_hedge_documentation_missing",
            "actor":      "oracle_fusion_tool",
            "action":     "hedge_documentation_audit",
            "resource":   f"hedge/{f['hedge_id']}",
            "severity":   "MEDIUM",
            "raw_payload": {
                "fx_hedge_documentation_missing": True,
                "treasury_detail": {
                    "hedge_id": f["hedge_id"], "currency_pair": f["currency_pair"],
                    "notional_amount": f["notional_amount"],
                },
            },
        })

    # Continuous Third-Party/Vendor Risk: spend-concentration check rides this
    # same connector/poll schedule — see vendor_risk_sweep.py for the other
    # half (SOC 2 expiry), which is DB-only and doesn't need ERP access.
    concentration_threshold = float((extra_config or {}).get("vendor_concentration_threshold_pct")
                                     or _VENDOR_CONCENTRATION_THRESHOLD_PCT)
    concentration = check_vendor_concentration(date_from=date_from, threshold_pct=concentration_threshold, client=client)
    if concentration.get("error"):
        raise RuntimeError(concentration["error"])
    for f in concentration["findings"]:
        events.append({
            "event_id":   f"vendor-concentration:{f['vendor_name']}:{today}",
            "event_type": "vendor_concentration_breach",
            "actor":      "oracle_fusion_tool",
            "action":     "vendor_spend_concentration_audit",
            "resource":   f"vendor/{f['vendor_name']}",
            "severity":   "MEDIUM",
            "raw_payload": {
                "vendor_concentration_breach": True,
                "vendor_risk_detail": {
                    "vendor_name": f["vendor_name"],
                    "concentration_pct": f["concentration_pct"],
                    "threshold_pct": concentration_threshold,
                    "window_days": _VENDOR_CONCENTRATION_WINDOW_DAYS,
                },
            },
        })

    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity/credentials with a minimal real call, without
    pulling a full event batch."""
    try:
        client = OracleFusionClient(
            host=base_url,
            username=credentials.get("username"),
            password=credentials.get("password"),
            client_id=credentials.get("client_id"),
            client_secret=credentials.get("client_secret"),
        )
        result = get_audit_events(max_items=1, client=client)
        if result.get("error"):
            return False, result["error"]
        return True, f"Connected — {result.get('count', 0)} audit event(s) visible in test window"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
