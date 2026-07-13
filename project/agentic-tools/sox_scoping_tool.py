#!/usr/bin/env python3
"""
SOX Scope Generator — Dendrai Intelligenza

Aligned with PCAOB AS2201 (ICFR) and AS2315 (materiality / tolerable misstatement).

Consumes per pipeline run:
  - Predictive forecast (revenue, margin) from Stage 1
  - Stage 2 risk scores (RAG, velocity, category)
  - Financial ratios from EDGAR XBRL
  - Optional geography / segment financials
  - Extensible system registry (ERP, consolidation, sub-ledgers, etc.)

Produces:
  - Planning materiality, performance materiality, trivial threshold
  - Significant accounts with in-scope decisions and risk linkage
  - Process coverage (P1/P2) mapped from Stage 2 RAG categories
  - System-level scoping decisions (in / out / review)
  - Segment / geography coverage when data is available
  - SHA-256 input hash for automatic rescoping detection
"""

import hashlib
import json
import math
from datetime import datetime
from typing import Optional


# ── SOX Process Catalogue ──────────────────────────────────────────────────────
# always_in: True = required regardless of risk score per AS2201
SOX_PROCESSES = [
    {
        "id": "order_to_cash",
        "name": "Order-to-Cash",
        "risk_categories": ["Strategic", "Financial"],
        "risk_keywords": ["revenue", "customer", "churn", "concentration", "deferred"],
        "always_in": True,
        "description": "Revenue recognition, AR, billing, collections, returns",
        "linked_accounts": ["revenue", "accounts_receivable", "deferred_revenue"],
    },
    {
        "id": "procure_to_pay",
        "name": "Procure-to-Pay",
        "risk_categories": ["Operational"],
        "risk_keywords": ["supply chain", "procurement", "vendor", "raw material", "inventory"],
        "always_in": False,
        "description": "AP, vendor master, 3-way match, payments, expense accruals",
        "linked_accounts": ["accounts_payable", "inventory", "accrued_liabilities"],
    },
    {
        "id": "financial_close",
        "name": "Financial Close & Consolidation",
        "risk_categories": ["Financial"],
        "risk_keywords": ["financial reporting", "reporting quality", "close", "consolidation"],
        "always_in": True,
        "description": "Period-end close, JE controls, account reconciliations, intercompany",
        "linked_accounts": ["all"],
    },
    {
        "id": "itgc",
        "name": "IT General Controls (ITGC)",
        "risk_categories": ["Operational"],
        "risk_keywords": ["cybersecurity", "data breach", "cloud", "infrastructure", "it"],
        "always_in": True,
        "description": "Access controls, change management, operations, SDLC (AS2201 §B24)",
        "linked_accounts": [],
    },
    {
        "id": "treasury",
        "name": "Treasury & Cash Management",
        "risk_categories": ["Financial", "Macro"],
        "risk_keywords": ["liquidity", "macro", "interest rate", "credit", "financing", "capex"],
        "always_in": False,
        "description": "Cash positioning, investments, debt covenants, derivatives, bank recs",
        "linked_accounts": ["cash", "long_term_debt", "derivatives", "interest_expense"],
    },
    {
        "id": "payroll_hr",
        "name": "Payroll & Human Resources",
        "risk_categories": ["Operational"],
        "risk_keywords": ["talent", "labour", "union", "headcount", "compensation"],
        "always_in": False,
        "description": "Payroll processing, employee master, benefits, equity compensation",
        "linked_accounts": ["employee_compensation", "accrued_payroll"],
    },
    {
        "id": "tax_provision",
        "name": "Tax Provision",
        "risk_categories": ["Regulatory", "Financial"],
        "risk_keywords": ["tax", "regulatory", "aml", "compliance", "transfer pricing"],
        "always_in": False,
        "description": "Income tax provision, deferred taxes, transfer pricing, tax reserves",
        "linked_accounts": ["income_tax_provision", "deferred_tax"],
    },
    {
        "id": "inventory_cost",
        "name": "Inventory & Cost Accounting",
        "risk_categories": ["Operational", "Financial"],
        "risk_keywords": ["inventory", "supply chain", "raw material", "cost", "margin", "cogs"],
        "always_in": False,
        "description": "Inventory valuation, COGS, standard costing, obsolescence reserves",
        "linked_accounts": ["inventory", "cogs"],
    },
    {
        "id": "fixed_assets",
        "name": "Fixed Assets & Capital Expenditure",
        "risk_categories": ["Financial", "Operational"],
        "risk_keywords": ["capex", "asset", "depreciation", "property", "plant", "equipment"],
        "always_in": False,
        "description": "PPE additions/disposals, depreciation, impairment, leases (ASC 842)",
        "linked_accounts": ["property_plant_equipment", "capex", "depreciation"],
    },
    {
        "id": "equity_goodwill",
        "name": "Equity, Goodwill & Intangibles",
        "risk_categories": ["Strategic", "Financial"],
        "risk_keywords": ["goodwill", "intangible", "acquisition", "ip", "patent", "impairment"],
        "always_in": False,
        "description": "Equity rollforward, goodwill impairment, intangible amortisation, M&A",
        "linked_accounts": ["goodwill", "intangibles", "equity"],
    },
    {
        "id": "segment_reporting",
        "name": "Segment & Geographic Reporting",
        "risk_categories": ["Strategic", "Financial"],
        "risk_keywords": ["geographic", "concentration", "segment", "geographic concentration"],
        "always_in": False,
        "description": "Operating segment identification, inter-segment eliminations, geo-allocations",
        "linked_accounts": ["segment_revenue", "segment_income"],
    },
]

# Significant-account catalogue with inherent risk category linkage
SOX_ACCOUNTS = [
    {"id": "revenue",                  "name": "Revenue",                          "always_scope": True,  "risk_categories": ["Financial", "Strategic"]},
    {"id": "accounts_receivable",      "name": "Accounts Receivable",              "always_scope": False, "risk_categories": ["Financial"]},
    {"id": "inventory",                "name": "Inventory",                        "always_scope": False, "risk_categories": ["Operational"]},
    {"id": "property_plant_equipment", "name": "Property, Plant & Equipment",      "always_scope": False, "risk_categories": ["Operational", "Financial"]},
    {"id": "goodwill",                 "name": "Goodwill & Intangibles",           "always_scope": False, "risk_categories": ["Strategic", "Financial"]},
    {"id": "long_term_debt",           "name": "Long-Term Debt & Financing",       "always_scope": False, "risk_categories": ["Financial", "Macro"]},
    {"id": "income_tax_provision",     "name": "Income Tax Provision",             "always_scope": False, "risk_categories": ["Regulatory", "Financial"]},
    {"id": "employee_compensation",    "name": "Employee Compensation & Benefits", "always_scope": False, "risk_categories": ["Operational"]},
    {"id": "cogs",                     "name": "Cost of Goods Sold",               "always_scope": False, "risk_categories": ["Operational", "Financial"]},
    {"id": "accrued_liabilities",      "name": "Accrued Liabilities",              "always_scope": False, "risk_categories": ["Financial"]},
    {"id": "cash",                     "name": "Cash & Equivalents",               "always_scope": False, "risk_categories": ["Financial"]},
    {"id": "deferred_revenue",         "name": "Deferred Revenue",                 "always_scope": False, "risk_categories": ["Financial", "Strategic"]},
]

# Default system-type → linked SOX processes
SYSTEM_TYPE_PROCESSES = {
    "erp":         ["financial_close", "procure_to_pay", "order_to_cash", "inventory_cost", "fixed_assets"],
    "consolidation": ["financial_close", "segment_reporting"],
    "reporting":   ["financial_close", "segment_reporting", "tax_provision"],
    "epm":         ["financial_close", "segment_reporting"],
    "treasury":    ["treasury"],
    "hr_payroll":  ["payroll_hr"],
    "tax":         ["tax_provision"],
    "sub_ledger":  ["procure_to_pay", "inventory_cost", "fixed_assets"],
    "crm":         ["order_to_cash"],
    "billing":     ["order_to_cash"],
    "custom":      [],
}


# ── FY Projections ─────────────────────────────────────────────────────────────

def compute_fy_projections(
    forecast: dict,
    ratios: dict,
    fiscal_year: Optional[str] = None,
) -> dict:
    """
    Derive projected FY financials from the pipeline's ensemble forecast + EDGAR ratios.

    forecast: output of compute_ensemble_forecast() keyed on "Revenue":
        {"forecasts": [{"horizon":1,"point":...}, ...], "metric": "Revenue", ...}
    ratios: output of compute_financial_ratios().
    """
    if not fiscal_year:
        fiscal_year = f"FY{datetime.utcnow().year}"

    # Sum 4-quarter point forecasts → projected annual revenue
    fc_pts = forecast.get("forecasts", [])
    if fc_pts:
        revenue_fy = sum(f["point"] for f in fc_pts if f.get("point") is not None)
        revenue_fy_low  = sum(f.get("ci_lower", f["point"]) for f in fc_pts if f.get("point") is not None)
        revenue_fy_high = sum(f.get("ci_upper", f["point"]) for f in fc_pts if f.get("point") is not None)
    else:
        # Fall back to latest annual from ratios
        revenue_fy      = ratios.get("revenue_now") or 0
        revenue_fy_low  = revenue_fy
        revenue_fy_high = revenue_fy

    gm_pct  = ratios.get("gross_margin") or 0.0
    sga_pct = ratios.get("sga_intensity") or 0.0
    rd_pct  = ratios.get("rd_intensity") or 0.0
    nm_pct  = ratios.get("net_margin") or 0.0

    gross_profit_fy     = revenue_fy * gm_pct if revenue_fy and gm_pct else None
    sga_fy              = revenue_fy * sga_pct if revenue_fy and sga_pct else None
    rd_fy               = revenue_fy * rd_pct  if revenue_fy and rd_pct  else None

    # Pre-tax income estimate: gross profit − SGA − R&D (simplified, ignores D&A, other)
    if gross_profit_fy is not None and sga_fy is not None:
        pretax_income_est = gross_profit_fy - sga_fy - (rd_fy or 0)
    elif nm_pct and revenue_fy:
        # fallback: use net margin as proxy for pre-tax margin (conservative)
        pretax_income_est = revenue_fy * nm_pct * 1.25  # 25% gross-up for taxes
    else:
        pretax_income_est = None

    return {
        "fiscal_year":         fiscal_year,
        "revenue_fy":          revenue_fy,
        "revenue_fy_low":      revenue_fy_low,
        "revenue_fy_high":     revenue_fy_high,
        "gross_profit_fy":     gross_profit_fy,
        "sga_fy":              sga_fy,
        "rd_fy":               rd_fy,
        "pretax_income_est":   pretax_income_est,
        "gross_margin_pct":    gm_pct,
        "assets_now":          ratios.get("assets_now"),
        "cash_now":            ratios.get("cash_now"),
        "net_income_now":      ratios.get("net_income_now"),
        "forecast_quarters":   len(fc_pts),
    }


# ── Materiality ────────────────────────────────────────────────────────────────

def compute_materiality(
    projections: dict,
    materiality_pct: float = 5.0,
    performance_mat_pct: float = 75.0,
) -> dict:
    """
    Compute AS2315-aligned materiality thresholds.

    Primary basis: materiality_pct % of |pre-tax income|.
    Revenue floor: 0.5% of projected FY revenue (if income-based is too small).
    Performance materiality: performance_mat_pct % of planning materiality.
    Trivial: 5% of planning materiality.
    """
    rev = projections.get("revenue_fy") or 0
    pti = projections.get("pretax_income_est")

    # Income-based
    income_based = abs(pti) * (materiality_pct / 100.0) if pti else None

    # Revenue-based floor
    revenue_floor = rev * 0.005 if rev else None  # 0.5% of revenue

    # Asset-based cross-check (0.5% of total assets)
    assets = projections.get("assets_now")
    asset_check = assets * 0.005 if assets else None

    # Choose basis
    if income_based and income_based > 0:
        planning = income_based
        basis_note = f"{materiality_pct}% × projected pre-tax income estimate"
        # Apply revenue floor only if income-based is unreasonably small
        if revenue_floor and planning < revenue_floor * 0.25:
            planning = revenue_floor
            basis_note = "0.5% × projected FY revenue (income-based too small)"
    elif revenue_floor:
        planning = revenue_floor
        basis_note = "0.5% × projected FY revenue (pre-tax income unavailable)"
    else:
        planning = None
        basis_note = "Insufficient financial data — manual materiality required"

    if planning is None:
        return {
            "planning_materiality": None,
            "performance_materiality": None,
            "trivial_threshold": None,
            "basis": basis_note,
            "income_based": None,
            "revenue_floor": revenue_floor,
            "asset_check": asset_check,
        }

    performance = planning * (performance_mat_pct / 100.0)
    trivial     = planning * 0.05

    return {
        "planning_materiality":    round(planning),
        "performance_materiality": round(performance),
        "trivial_threshold":       round(trivial),
        "basis":                   basis_note,
        "income_based":            round(income_based) if income_based else None,
        "revenue_floor":           round(revenue_floor) if revenue_floor else None,
        "asset_check":             round(asset_check)   if asset_check  else None,
    }


# ── Account Scoping ────────────────────────────────────────────────────────────

def _estimate_account_balance(account_id: str, projections: dict, ratios: dict) -> Optional[float]:
    """Rough balance estimate for an account from projected financials."""
    rev = projections.get("revenue_fy") or 0
    gm  = projections.get("gross_profit_fy")
    assets = projections.get("assets_now")

    estimates = {
        "revenue":                  rev,
        "accounts_receivable":      rev * 0.12 if rev else None,   # ~45 DSO
        "deferred_revenue":         rev * 0.06 if rev else None,
        "cogs":                     (rev - gm) if (rev and gm) else (rev * 0.60 if rev else None),
        "inventory":                rev * 0.15 if rev else None,
        "accrued_liabilities":      rev * 0.08 if rev else None,
        "employee_compensation":    rev * (ratios.get("sga_intensity") or 0.18) * 0.55 if rev else None,
        "income_tax_provision":     abs(projections.get("pretax_income_est") or 0) * 0.22,
        "property_plant_equipment": assets * 0.30 if assets else (rev * 0.35 if rev else None),
        "goodwill":                 assets * 0.25 if assets else None,
        "long_term_debt":           assets * 0.20 if assets else None,
        "cash":                     projections.get("cash_now"),
    }
    return estimates.get(account_id)


def scope_accounts(
    materiality: dict,
    projections: dict,
    ratios: dict,
    risk_scores: dict,
    segments: Optional[list] = None,
    account_overrides: Optional[dict] = None,
) -> list:
    """
    Decide which accounts are in scope.

    Rules (AS2201 §26):
    1. Revenue recognition — always in scope.
    2. Account balance > performance materiality → quantitatively significant.
    3. Account balance > trivial but < performance materiality AND linked to
       RED or AMBER risk → qualitatively significant (risk-based in-scope).
    4. Elevated-risk accounts get lower effective threshold (75% of perf mat).

    account_overrides: optional {account_id: {geography, segments, notes,
    manual_in_scope, manual_priority}} — user-supplied detail/overrides
    (see db.get_sox_account_details). manual_in_scope, when not None,
    replaces the computed in-scope decision.
    """
    account_overrides = account_overrides or {}
    pm   = materiality.get("performance_materiality")
    trivial = materiality.get("trivial_threshold")
    risks = risk_scores.get("risks", [])

    # Build category → max RAG colour lookup
    cat_rag: dict[str, str] = {}
    for r in risks:
        cat = r.get("category", "")
        rag = r.get("rag_status", "Green")
        rag_rank = {"Red": 2, "Amber": 1, "Green": 0}
        if cat not in cat_rag or rag_rank.get(rag, 0) > rag_rank.get(cat_rag[cat], 0):
            cat_rag[cat] = rag

    # Build keyword → risk name lookup for rationale text
    risk_by_keyword: dict[str, list] = {}
    for r in risks:
        for kw in (r.get("name", "") + " " + r.get("category", "")).lower().split():
            risk_by_keyword.setdefault(kw, []).append(r)

    result = []
    for acc in SOX_ACCOUNTS:
        bal = _estimate_account_balance(acc["id"], projections, ratios)

        # Risk linkage for this account
        linked_risk_cats = acc["risk_categories"]
        worst_rag = max(
            (cat_rag.get(c, "Green") for c in linked_risk_cats),
            key=lambda r: {"Red": 2, "Amber": 1, "Green": 0}.get(r, 0),
            default="Green",
        )
        linked_risks = [r["name"] for r in risks if r.get("category") in linked_risk_cats]

        # Effective threshold: lower for risk-elevated accounts
        threshold_mult = 0.75 if worst_rag in ("Red", "Amber") else 1.0
        effective_threshold = (pm * threshold_mult) if pm else None

        # Scope decision
        if acc["always_scope"]:
            in_scope = True
            priority = "P1"
            rationale = "Required per AS2201 §26 (revenue recognition always in scope)"
        elif effective_threshold and bal and bal >= effective_threshold:
            in_scope = True
            priority = "P1" if bal >= (pm or 0) else "P2"
            rationale = (
                f"Balance estimate ${bal:,.0f} ≥ "
                f"{'effective threshold' if threshold_mult < 1 else 'performance materiality'} "
                f"${effective_threshold:,.0f}"
                + (f" — elevated by {worst_rag} risk ({', '.join(linked_risks[:2])})" if linked_risks and worst_rag != "Green" else "")
            )
        elif bal and trivial and bal < trivial:
            in_scope = False
            priority = None
            rationale = f"Below trivial threshold ${trivial:,.0f} — clearly inconsequential"
        elif worst_rag == "Red":
            # Qualitative: Red risk forces in-scope even without quantitative trigger
            in_scope = True
            priority = "P2"
            rationale = f"Qualitative — RED risk linkage ({', '.join(linked_risks[:2])})"
        else:
            in_scope = False
            priority = None
            rationale = "Below performance materiality; no elevated risk linkage"

        override = account_overrides.get(acc["id"])
        manual_override = False
        if override and override.get("manual_in_scope") is not None:
            manual_override = True
            in_scope = override["manual_in_scope"]
            priority = override.get("manual_priority") or (priority if in_scope else None) or ("P2" if in_scope else None)
            rationale = f"Manually overridden by user — {rationale}"
        elif override and override.get("manual_priority") and in_scope:
            priority = override["manual_priority"]

        result.append({
            "account_id":      acc["id"],
            "account_name":    acc["name"],
            "balance_estimate": round(bal) if bal else None,
            "in_scope":        in_scope,
            "priority":        priority,
            "rag_linkage":     worst_rag,
            "linked_risks":    linked_risks[:4],
            "rationale":       rationale,
            "geography":       (override or {}).get("geography") or [],
            "segments":        (override or {}).get("segments") or [],
            "notes":           (override or {}).get("notes"),
            "manual_override": manual_override,
        })

    return result


# ── Process Scoping ────────────────────────────────────────────────────────────

def scope_processes(risk_scores: dict, account_scope: list, process_overrides: Optional[dict] = None) -> list:
    """
    Assign coverage level to each SOX process based on risk scores and account scope.

    P1 = mandatory or driven by RED risk / always-in account
    P2 = elevated (AMBER risk or quantitatively significant account linked)
    Out = no risk trigger and no material account linkage

    process_overrides: optional {process_id: {geography, segments, notes,
    manual_coverage_level, estimated_exposure}} — user-supplied detail/
    overrides (see db.get_sox_process_details). manual_coverage_level, when
    set, replaces the computed coverage decision. estimated_exposure is a
    manually-entered $ figure (e.g. annual transaction volume/spend) — unlike
    accounts, processes have no balance in the underlying financial data, so
    there's no algorithmic equivalent to balance_estimate here.
    """
    process_overrides = process_overrides or {}
    risks = risk_scores.get("risks", [])
    in_scope_accs = {a["account_id"] for a in account_scope if a["in_scope"]}

    # Risk name/category → RAG lookup
    cat_rag: dict[str, str] = {}
    name_rag: dict[str, str] = {}
    for r in risks:
        cat = r.get("category", "")
        name = r.get("name", "").lower()
        rag  = r.get("rag_status", "Green")
        rag_rank = {"Red": 2, "Amber": 1, "Green": 0}
        if rag_rank.get(rag, 0) > rag_rank.get(cat_rag.get(cat, "Green"), 0):
            cat_rag[cat] = rag
        name_rag[name] = rag

    result = []
    for proc in SOX_PROCESSES:
        # Always-in processes
        if proc["always_in"]:
            coverage = "P1"
            rationale = "Mandatory per AS2201" + (" (ITGC §B24)" if proc["id"] == "itgc" else " (core ICFR process)")
            linked_risk_names = []
        else:
            # Check risk category linkage
            linked_rag = [cat_rag.get(c, "Green") for c in proc["risk_categories"]]
            worst_cat_rag = max(linked_rag, key=lambda r: {"Red": 2, "Amber": 1, "Green": 0}.get(r, 0), default="Green")

            # Check keyword match
            kw_matched_risks = []
            for r in risks:
                rname_lower = r.get("name", "").lower()
                if any(kw in rname_lower for kw in proc["risk_keywords"]):
                    kw_matched_risks.append(r)
            worst_kw_rag = max(
                (r.get("rag_status", "Green") for r in kw_matched_risks),
                key=lambda rg: {"Red": 2, "Amber": 1, "Green": 0}.get(rg, 0),
                default="Green",
            )
            worst_rag = max([worst_cat_rag, worst_kw_rag],
                            key=lambda rg: {"Red": 2, "Amber": 1, "Green": 0}.get(rg, 0))

            # Check if any linked account is in scope
            acc_trigger = any(a in in_scope_accs for a in proc["linked_accounts"] if a != "all")

            if worst_rag == "Red":
                coverage = "P1"
                rationale = f"RED risk linked — {', '.join(r['name'] for r in kw_matched_risks[:2] if r.get('rag_status') == 'Red')}"
            elif worst_rag == "Amber" or acc_trigger:
                coverage = "P2"
                rationale = (
                    f"AMBER risk linkage" if worst_rag == "Amber" else ""
                ) + (
                    " + material account in scope" if acc_trigger else ""
                )
                rationale = rationale.strip(" +") or "Material account in scope"
            else:
                coverage = "Out"
                rationale = "No elevated risk trigger; no material account linkage"

            linked_risk_names = [r["name"] for r in kw_matched_risks]

        override = process_overrides.get(proc["id"])
        manual_override = False
        if override and override.get("manual_coverage_level"):
            manual_override = True
            coverage = override["manual_coverage_level"]
            rationale = f"Manually overridden by user — {rationale}"

        result.append({
            "process_id":    proc["id"],
            "process_name":  proc["name"],
            "coverage_level": coverage,
            "always_in":     proc["always_in"],
            "linked_risks":  linked_risk_names[:4],
            "rationale":     rationale,
            "description":   proc["description"],
            "geography":     (override or {}).get("geography") or [],
            "segments":      (override or {}).get("segments") or [],
            "notes":         (override or {}).get("notes"),
            "estimated_exposure": (override or {}).get("estimated_exposure"),
            "manual_override": manual_override,
        })

    return result


# ── System Scoping ─────────────────────────────────────────────────────────────

def scope_systems(systems_registry: list, process_scope: list) -> list:
    """
    Decide each registered system's scope status based on linked processes.

    in_scope  = system supports at least one P1 process, or is high-significance
    review    = system supports only P2 processes or is medium-significance with P2
    out       = system supports only Out-of-scope processes
    """
    proc_level: dict[str, str] = {p["process_id"]: p["coverage_level"] for p in process_scope}

    # Default processes for system types with no explicit linkage
    result = []
    for sys in systems_registry:
        raw_procs = sys.get("linked_processes") or SYSTEM_TYPE_PROCESSES.get(sys.get("system_type", "custom"), [])
        linked_levels = [proc_level.get(p, "Out") for p in raw_procs]

        significance = sys.get("significance", "medium")

        if not linked_levels:
            decision = "review" if significance == "high" else "out"
            rationale = "No process linkages defined — manual review required" if decision == "review" else "No linked in-scope processes"
        else:
            has_p1 = "P1" in linked_levels
            has_p2 = "P2" in linked_levels

            if has_p1 or significance == "high":
                decision = "in_scope"
                p1_procs = [p for p, l in zip(raw_procs, linked_levels) if l == "P1"]
                rationale = f"Supports P1 process(es): {', '.join(p1_procs[:3])}" if p1_procs else "High-significance system — in scope"
            elif has_p2:
                decision = "review"
                p2_procs = [p for p, l in zip(raw_procs, linked_levels) if l == "P2"]
                rationale = f"Supports P2 process(es): {', '.join(p2_procs[:3])} — ITGC review required"
            else:
                decision = "out"
                rationale = "All linked processes out of scope"

        result.append({
            "system_id":       sys.get("id"),
            "system_name":     sys.get("system_name", ""),
            "system_type":     sys.get("system_type", ""),
            "vendor":          sys.get("vendor"),
            "significance":    significance,
            "linked_processes": raw_procs,
            "decision":        decision,
            "rationale":       rationale,
        })

    return result


# ── Segment / Geography Scoping ────────────────────────────────────────────────

def scope_segments(
    segments: list,
    materiality: dict,
    risk_scores: Optional[dict] = None,
) -> list:
    """
    Determine which geographic / business segments fall within SOX scope.

    AS2201 §16 requires coverage of locations that represent significant
    accounts or processes. Thresholds:
      - Segment revenue > performance materiality → in scope
      - Segment represents > 15% of total consolidated revenue → in scope
      - Segment linked to RED risk → qualitative in scope
    """
    if not segments:
        return []

    pm     = materiality.get("performance_materiality")
    risks  = (risk_scores or {}).get("risks", [])

    # Identify geographic/strategic risks
    geo_red = any(
        r.get("rag_status") == "Red" and
        any(kw in r.get("name", "").lower() for kw in ["geographic", "concentration", "geopolitic"])
        for r in risks
    )

    total_rev = sum((s.get("revenue") or 0) for s in segments if s.get("revenue"))
    result = []

    for seg in segments:
        rev     = seg.get("revenue")
        rev_pct = seg.get("revenue_pct") or ((rev / total_rev * 100) if total_rev and rev else None)

        if rev and pm and rev > pm:
            in_scope = True
            rationale = f"Revenue ${rev:,.0f} > performance materiality ${pm:,.0f}"
        elif rev_pct and rev_pct >= 15.0:
            in_scope = True
            rationale = f"{rev_pct:.1f}% of total revenue ≥ 15% AS2201 threshold"
        elif geo_red:
            in_scope = True
            rationale = "Qualitative — geographic concentration RED risk"
        elif rev_pct and rev_pct >= 5.0:
            in_scope = False
            rationale = f"{rev_pct:.1f}% of total revenue — below 15% threshold; monitor"
        else:
            in_scope = False
            rationale = "Immaterial — below quantitative and qualitative thresholds"

        result.append({
            "segment_name":  seg.get("segment_name", ""),
            "segment_type":  seg.get("segment_type", "geography"),
            "revenue":       rev,
            "revenue_pct":   rev_pct,
            "in_scope":      in_scope,
            "rationale":     rationale,
        })

    # Sort: in-scope first, then by revenue descending
    return sorted(result, key=lambda s: (not s["in_scope"], -(s["revenue"] or 0)))


# ── Change-Detection Hash ──────────────────────────────────────────────────────

def compute_input_hash(forecast: dict, risk_scores: dict, ratios: dict, segments: Optional[list] = None) -> str:
    """
    SHA-256 fingerprint of key inputs so a changed hash triggers auto-rescoping.
    """
    key = {
        "fc": [round(f.get("point", 0), 2) for f in forecast.get("forecasts", [])],
        "risks": sorted(
            [(r.get("name", ""), round(r.get("score", 0), 1), r.get("rag_status", ""))
             for r in risk_scores.get("risks", [])],
            key=lambda x: x[0],
        ),
        "rev": round(ratios.get("revenue_now") or 0, 0),
        "gm":  round(ratios.get("gross_margin") or 0, 4),
        "segs": sorted([(s.get("segment_name", ""), s.get("revenue") or 0) for s in (segments or [])]),
    }
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


# ── Full SOX Scoping Orchestrator ──────────────────────────────────────────────

def run_sox_scoping(
    run_id: Optional[int],
    forecast: dict,
    risk_scores: dict,
    ratios: dict,
    systems_registry: Optional[list] = None,
    segments: Optional[list] = None,
    fiscal_year: Optional[str] = None,
    materiality_pct: float = 5.0,
    performance_mat_pct: float = 75.0,
    trigger_reason: Optional[str] = None,
    account_overrides: Optional[dict] = None,
    process_overrides: Optional[dict] = None,
) -> dict:
    """
    Full SOX scoping run.

    Returns structured dict ready for JSON serialisation and DB persistence.
    """
    # 1. FY projections
    projections = compute_fy_projections(forecast, ratios, fiscal_year)

    # 2. Materiality
    mat = compute_materiality(projections, materiality_pct, performance_mat_pct)

    # 3. Account scoping
    accounts = scope_accounts(mat, projections, ratios, risk_scores, segments, account_overrides)

    # 4. Process scoping
    processes = scope_processes(risk_scores, accounts, process_overrides)

    # 5. System scoping
    systems_out = scope_systems(systems_registry or [], processes)

    # 6. Segment coverage
    seg_coverage = scope_segments(segments or [], mat, risk_scores)

    # 7. Summary stats
    accs_in   = sum(1 for a in accounts  if a["in_scope"])
    procs_p1  = sum(1 for p in processes if p["coverage_level"] == "P1")
    procs_p2  = sum(1 for p in processes if p["coverage_level"] == "P2")
    sys_in    = sum(1 for s in systems_out if s["decision"] == "in_scope")
    segs_in   = sum(1 for s in seg_coverage if s["in_scope"])

    # 8. Input hash
    input_hash = compute_input_hash(forecast, risk_scores, ratios, segments)

    return {
        "run_id":                  run_id,
        "fiscal_year":             projections["fiscal_year"],
        "scoped_at":               datetime.utcnow().isoformat() + "Z",
        "trigger_reason":          trigger_reason or "pipeline_run",
        "input_hash":              input_hash,

        # Materiality
        "planning_materiality":    mat["planning_materiality"],
        "performance_materiality": mat["performance_materiality"],
        "trivial_threshold":       mat["trivial_threshold"],
        "materiality_basis":       mat["basis"],

        # Projected financials
        "revenue_forecast_fy":     round(projections["revenue_fy"]) if projections["revenue_fy"] else None,
        "revenue_forecast_low":    round(projections["revenue_fy_low"]) if projections.get("revenue_fy_low") else None,
        "revenue_forecast_high":   round(projections["revenue_fy_high"]) if projections.get("revenue_fy_high") else None,
        "pretax_income_estimate":  round(projections["pretax_income_est"]) if projections["pretax_income_est"] else None,
        "gross_margin_pct":        projections["gross_margin_pct"],

        # Scope outputs
        "accounts_in_scope":       accounts,
        "processes_in_scope":      processes,
        "systems_in_scope":        systems_out,
        "segments_coverage":       seg_coverage,

        # Summary
        "summary": {
            "accounts_in":     accs_in,
            "accounts_total":  len(accounts),
            "processes_p1":    procs_p1,
            "processes_p2":    procs_p2,
            "processes_out":   len(processes) - procs_p1 - procs_p2,
            "systems_in":      sys_in,
            "systems_total":   len(systems_out),
            "segments_in":     segs_in,
            "segments_total":  len(seg_coverage),
            "red_risks":       sum(1 for r in risk_scores.get("risks", []) if r.get("rag_status") == "Red"),
            "amber_risks":     sum(1 for r in risk_scores.get("risks", []) if r.get("rag_status") == "Amber"),
        },
    }
