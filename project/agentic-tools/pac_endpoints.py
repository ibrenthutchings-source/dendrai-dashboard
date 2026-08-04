#!/usr/bin/env python3
"""
Policy-as-Code & Controls-as-Code API
Endpoints for Rego policy module management, version history,
multi-approver sign-off, external hook configuration (GitHub / Confluence),
and Controls-as-Code generation from the controls library.

Router prefix: /pac

    GET  /pac/modules                     List all process modules (latest version)
    GET  /pac/modules/{process}           Get latest Rego module for a process
    PUT  /pac/modules/{process}           Save / version-bump a module
    GET  /pac/modules/{process}/history   Version history
    POST /pac/modules/{process}/approve   Add approver sign-off
    GET  /pac/hooks                       Get all external hook configs
    PUT  /pac/hooks/{hook_type}           Save / update a hook config
    POST /pac/hooks/github/sync           Pull .rego files from the configured repo path and import them
    POST /pac/cac/generate                Generate Controls-as-Code Rego from controls library
    GET  /pac/cac/latest                  Get the latest CaC artifact

    -- Negative testing (pac_contracts.py / pac_negative_tests.py / pac_assurance.py) --
    POST /pac/negative-tests/run/{process}      Run schema-contract + must-fire/must-not-fire corpus
    GET  /pac/negative-tests/history/{process}  Past test runs (audit evidence)
    GET  /pac/assurance                         Which controls are proven working vs. unverified
    GET  /pac/compliance-scorecard               Framework coverage (SOC 2/NIST/ISO/COSO) — mapped vs. verified
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import claude_client
import db
import pac_assurance
import pac_contracts
from auth_endpoints import require_screen_permission

logger = logging.getLogger(__name__)
# Router-level dependency: every /pac/* route requires at least read access
# to the Policy-as-Code Engine screen (nav id "policycode") — closes the gap
# where the screen-permission matrix only ever hid the nav item on the
# frontend without the backend re-checking it (see auth_endpoints.require_
# screen_permission's docstring). No route here is an external-system
# webhook, so uniform router-level gating is safe.
router = APIRouter(prefix="/pac", tags=["pac"], dependencies=[Depends(require_screen_permission("policycode"))])

# ─────────────────────────────────────────────────────────────────────────────
# Supported processes
# ─────────────────────────────────────────────────────────────────────────────

# The 5 processes below are the built-in defaults, seeded into the
# `pac_processes` table on startup (api_server.py) via db.seed_builtin_pac_processes().
# VALID_PROCESSES / _valid_processes() is now DB-backed so a process
# discovered in a synced GitHub repo (sync_github's auto-register, below)
# becomes valid immediately without a code change — this constant stays only
# as the offline/DB-unavailable fallback.
_BUILTIN_PROCESS_IDS = {
    "itgc",
    "order_to_cash",
    "procure_to_pay",
    "receive_to_ship",
    "record_to_report",
    "devops_monitoring",
    "infrastructure_monitoring",
    "hire_to_retire",
    "trade_compliance",
}


def _valid_processes() -> set[str]:
    """Known PaC process ids — DB-backed so new ones (manually added or
    discovered via sync_github) are valid immediately. Falls back to the 5
    built-in ids if the DB is unavailable."""
    if db.is_available():
        rows = db.list_pac_processes()
        if rows:
            return {r["id"] for r in rows}
    return set(_BUILTIN_PROCESS_IDS)


_PROCESS_LABELS = {
    "itgc":            "IT General Controls",
    "order_to_cash":   "Order to Cash",
    "procure_to_pay":  "Procure to Pay",
    "receive_to_ship": "Receive to Ship",
    "record_to_report": "Record to Report",
    "devops_monitoring": "DevOps Monitoring",
    "infrastructure_monitoring": "Infrastructure Monitoring",
    "hire_to_retire": "Hire to Retire",
    "trade_compliance": "Trade Compliance",
}

# Assigned round-robin to processes auto-registered by sync_github (a folder
# discovered in a synced repo that doesn't match any known process) — the 5
# built-in processes keep their own hand-picked colors from db.py's seed.
_AUTO_PROCESS_COLORS = ["#8b5cf6", "#14b8a6", "#f97316", "#3b82f6", "#ec4899", "#84cc16"]

# Control-ID prefix each process's deny rules embed in their sprintf message
# (e.g. "ITGC-AC-01: ...", "R2S-P001: ..."). Used to steer the Markdown->Rego
# LLM conversion so generated modules stay consistent with hand-authored ones.
_PROCESS_ID_PREFIX = {
    "itgc":            "ITGC",
    "order_to_cash":   "OTC",
    "procure_to_pay":  "P2P",
    "receive_to_ship": "R2S",
    "record_to_report": "R2R",
    "devops_monitoring": "DEVOPS",
    "infrastructure_monitoring": "INFRA",
    "hire_to_retire": "H2R",
    "trade_compliance": "TC",
}

# ─────────────────────────────────────────────────────────────────────────────
# Synthetic Rego defaults — Oracle Fusion ERP examples
# ─────────────────────────────────────────────────────────────────────────────

_REGO_DEFAULTS: Dict[str, str] = {

"itgc": """\
# Oracle Fusion ERP — IT General Controls (ITGCs)
# Package:  controls.oracle_fusion.itgc
# Process:  IT General Controls
# Version:  1.0
# Approved by: CISO, VP Engineering
# Last Revised: 2026-07-03
# Description: Preventive and detective controls over Oracle Fusion Cloud
#   infrastructure, access lifecycle, change management, backup, and monitoring.

package controls.oracle_fusion.itgc

import future.keywords.in
import future.keywords.if

# ── AC-01: User Access Provisioning ──────────────────────────────────────────
# Oracle Fusion Identity Cloud Service (IDCS) / OCI IAM
deny_access_event[msg] if {
    input.event.type == "user_provisioning"
    not input.event.approved_by
    msg := sprintf("ITGC-AC-01: Oracle Fusion user '%v' provisioned without IDCS approval workflow", [input.event.username])
}

deny_access_event[msg] if {
    input.event.type == "privileged_role_grant"
    not input.event.dual_approval
    msg := sprintf("ITGC-AC-01: Privileged role '%v' granted to '%v' without dual approval in Oracle Fusion", [input.event.role_name, input.event.username])
}

# ── AC-02: User Deprovisioning ────────────────────────────────────────────────
deny_access_event[msg] if {
    input.event.type == "user_deprovision"
    not input.event.hr_termination_confirmed
    msg := sprintf("ITGC-AC-02: Oracle Fusion user '%v' deprovisioned without confirmed HR termination record", [input.event.username])
}

deny_access_event[msg] if {
    input.event.type == "user_termination"
    input.event.deprovision_lag_hours > 24
    msg := sprintf("ITGC-AC-02: Oracle Fusion access for terminated user '%v' not revoked within 24 hours (lag: %vh)", [input.event.username, input.event.deprovision_lag_hours])
}

# ── AC-05: Logical Access Review ──────────────────────────────────────────────
deny_access_review[msg] if {
    input.access_review.last_review_days > 90
    msg := sprintf("ITGC-AC-05: Oracle Fusion access review overdue by %v days for role '%v' — quarterly review SLA breached", [
        input.access_review.last_review_days - 90,
        input.access_review.role_name
    ])
}

# ── SC-05: Change Management ──────────────────────────────────────────────────
# Oracle Fusion: Changes tracked via Oracle Change Management module
deny_change_event[msg] if {
    input.event.type == "production_change"
    not input.event.change_ticket
    msg := sprintf("ITGC-SC-05: Production change to Oracle Fusion object '%v' lacks approved change ticket", [input.event.object_name])
}

deny_change_event[msg] if {
    input.event.type == "production_change"
    input.event.tested_in_uat != true
    msg := sprintf("ITGC-SC-05: Oracle Fusion change '%v' deployed to production without UAT sign-off", [input.event.object_name])
}

deny_change_event[msg] if {
    input.event.type == "emergency_change"
    not input.event.post_implementation_review_scheduled
    msg := sprintf("ITGC-SC-05: Emergency change '%v' in Oracle Fusion lacks scheduled post-implementation review", [input.event.change_id])
}

# ── SC-04: Patch Management ───────────────────────────────────────────────────
deny_patch_event[msg] if {
    input.environment.patch_lag_days > 90
    msg := sprintf("ITGC-SC-04: Oracle Fusion patch lag %v days exceeds 90-day policy SLA", [input.environment.patch_lag_days])
}

# ── Backup and Recovery ───────────────────────────────────────────────────────
deny_backup_event[msg] if {
    input.backup.type == "daily"
    not input.backup.completed_successfully
    msg := sprintf("ITGC-BC-01: Oracle Fusion daily backup for environment '%v' failed on %v", [input.backup.environment, input.backup.date])
}

deny_backup_event[msg] if {
    input.backup.rto_test_hours > 4
    msg := sprintf("ITGC-BC-02: Oracle Fusion RTO test of %vh exceeds 4-hour recovery objective — BCP update required", [input.backup.rto_test_hours])
}

# ── Logging and Monitoring ────────────────────────────────────────────────────
deny_monitoring_event[msg] if {
    input.monitoring.audit_log_retention_days < 365
    msg := sprintf("ITGC-MO-01: Oracle Fusion audit log retention %v days falls below 365-day regulatory requirement", [input.monitoring.audit_log_retention_days])
}

deny_monitoring_event[msg] if {
    input.monitoring.failed_login_attempts > 5
    not input.monitoring.alert_triggered
    msg := sprintf("ITGC-MO-02: Oracle Fusion user '%v' has %v failed login attempts with no security alert triggered", [input.monitoring.username, input.monitoring.failed_login_attempts])
}

# ── AI Governance (ISO/IEC 42001 AI-05/AI-06) ─────────────────────────────────
# Findings ride the generic input.event.* shape (system_telemetry ->
# mcp_governance._evaluate_pac_policy) — ai_governance_sweep.py's assessment-
# expiry sweep and ai_governance_endpoints.py's inline human-oversight check.

# ── ITGC-AI-05: Third-Party AI Tool Assessment ────────────────────────────────
deny_ai_governance[msg] if {
    input.event.type == "AI_ASSESSMENT_OVERDUE"
    msg := sprintf("ITGC-AI-05: AI system '%v' (%v risk) third-party assessment expired on %v", [
        input.event.system_name,
        input.event.risk_tier,
        input.event.assessment_expires_at,
    ])
}

# ── ITGC-AI-06: Human Oversight of AI Systems ─────────────────────────────────
deny_ai_governance[msg] if {
    input.event.type == "AI_HUMAN_OVERSIGHT_MISSING"
    msg := sprintf("ITGC-AI-06: AI system '%v' requires human oversight but has no defined human review point on file", [
        input.event.system_name,
    ])
}
""",

"order_to_cash": """\
# Oracle Fusion ERP — Order to Cash (O2C)
# Package:  controls.oracle_fusion.order_to_cash
# Process:  Order to Cash
# Version:  1.0
# Approved by: Controller, VP Revenue, CFO
# Last Revised: 2026-07-03
# Description: Controls over the complete O2C cycle in Oracle Fusion:
#   Quote → Order → Fulfillment → Invoice → Cash → Revenue Recognition.

package controls.oracle_fusion.order_to_cash

import future.keywords.in
import future.keywords.if

# ── P-OTC-001: Revenue Recognition (ASC 606 / IFRS 15) ───────────────────────
# Oracle Fusion Revenue Management module. Fields ride the generic
# input.event.* shape (system_telemetry -> mcp_governance._evaluate_pac_policy
# -> UBO/pipeline/silver.py's erp_transaction_detail spread), flat and
# txn_/contract_-prefixed, matching every other process's convention rather
# than a nested input.transaction/.contract root the pipeline never
# constructs — see pac_contracts.py's module docstring for why nested roots
# are dead by construction.
deny_revenue_event[msg] if {
    input.event.txn_type == "revenue_recognition"
    not input.event.txn_performance_obligation_satisfied
    msg := sprintf("OTC-P001: Revenue $%v recognized for order '%v' before performance obligation satisfied — ASC 606 violation", [
        input.event.txn_amount,
        input.event.txn_order_number
    ])
}

deny_revenue_event[msg] if {
    input.event.contract_value > 1000000
    not input.event.contract_reviewed_by_legal
    msg := sprintf("OTC-P001: High-value contract '%v' ($%v) recognized without legal review in Oracle Fusion Revenue Management", [
        input.event.contract_id,
        input.event.contract_value
    ])
}

deny_revenue_event[msg] if {
    input.event.txn_type == "variable_consideration"
    not input.event.txn_constrained_estimate_documented
    msg := sprintf("OTC-P001: Variable consideration for contract '%v' lacks constrained estimate documentation (ASC 606-10-32)", [input.event.txn_contract_id])
}

# ── P-OTC-002: Credit Management ─────────────────────────────────────────────
# Oracle Fusion Order Management: Credit check integration
deny_order_event[msg] if {
    input.event.so_status == "booked"
    input.event.customer_credit_limit > 0
    input.event.so_total > input.event.customer_credit_limit
    not input.event.so_credit_override_approved_by
    msg := sprintf("OTC-P002: Sales order '%v' ($%v) exceeds customer credit limit ($%v) — override approval required in Oracle Fusion", [
        input.event.so_order_number,
        input.event.so_total,
        input.event.customer_credit_limit
    ])
}

# ── P-OTC-003: Billing Accuracy ───────────────────────────────────────────────
# Oracle Fusion AR: AutoInvoice and billing controls
deny_billing_event[msg] if {
    input.event.inv_type == "manual"
    not input.event.inv_approved_by
    input.event.inv_amount > 10000
    msg := sprintf("OTC-P003: Manual invoice '%v' for $%v requires manager approval in Oracle Fusion AR (>$10K threshold)", [
        input.event.inv_number,
        input.event.inv_amount
    ])
}

deny_billing_event[msg] if {
    input.event.inv_billing_date > input.event.inv_shipment_date
    input.event.inv_days_billed_before_shipment > 30
    msg := sprintf("OTC-P003: Invoice '%v' billed %v days before shipment — premature revenue risk in Oracle Fusion AR", [
        input.event.inv_number,
        input.event.inv_days_billed_before_shipment
    ])
}

# ── P-OTC-004: Cash Application ───────────────────────────────────────────────
# Oracle Fusion AR: Cash receipts and unapplied cash monitoring
deny_cash_event[msg] if {
    input.event.cash_unapplied_days > 30
    msg := sprintf("OTC-P004: Cash receipt '%v' ($%v) unapplied for %v days — Oracle Fusion AR SLA breach", [
        input.event.cash_receipt_number,
        input.event.cash_amount,
        input.event.cash_unapplied_days
    ])
}

# ── P-OTC-005: Customer Master Data Integrity ─────────────────────────────────
deny_customer_event[msg] if {
    # NOTE: real EventType enum value is "CUSTOMER_MASTER_CHANGE" (uppercase)
    # — same dead-rule-by-literal-case bug shape as the vendor-master rule
    # below (P-P2P-003)/pac_contracts.py's module docstring.
    input.event.type == "CUSTOMER_MASTER_CHANGE"
    input.event.field in ["bank_account", "payment_terms", "billing_address", "tax_id"]
    not input.event.dual_approved
    msg := sprintf("OTC-P005: Customer master change to '%v' for customer '%v' requires dual approval in Oracle Fusion Customer Model", [
        input.event.field,
        input.event.customer_name
    ])
}

# ── P-OTC-006: Accounts Receivable Aging ─────────────────────────────────────
deny_ar_event[msg] if {
    input.event.ar_days_outstanding > 90
    input.event.ar_amount > 50000
    not input.event.ar_collection_action_documented
    msg := sprintf("OTC-P006: AR balance $%v for customer '%v' is %v days outstanding without documented collection action in Oracle Fusion", [
        input.event.ar_amount,
        input.event.ar_customer_name,
        input.event.ar_days_outstanding
    ])
}
""",

"procure_to_pay": """\
# Oracle Fusion ERP — Procure to Pay (P2P)
# Package:  controls.oracle_fusion.procure_to_pay
# Process:  Procure to Pay
# Version:  1.0
# Approved by: CFO, VP Procurement, Controller
# Last Revised: 2026-07-03
# Description: Controls over the full P2P cycle in Oracle Fusion:
#   Requisition → PO → Goods Receipt → Invoice → Three-Way Match → Payment.

package controls.oracle_fusion.procure_to_pay

import future.keywords.in
import future.keywords.if

# ── P-P2P-001: Purchase Order Approval Thresholds ────────────────────────────
# Oracle Fusion Procurement: Approval Management Engine (AME). Fields ride
# the generic input.event.* shape, flat and po_/inv_-prefixed — same
# reasoning as order_to_cash's P-OTC-001 above.
deny_po_event[msg] if {
    input.event.po_total > 50000
    not input.event.po_vp_approved
    msg := sprintf("P2P-P001: PO '%v' for $%v requires VP approval in Oracle Fusion Procurement (>$50K threshold)", [
        input.event.po_number,
        input.event.po_total
    ])
}

deny_po_event[msg] if {
    input.event.po_total > 250000
    not input.event.po_cfo_approved
    msg := sprintf("P2P-P001: PO '%v' for $%v requires CFO approval in Oracle Fusion Procurement (>$250K threshold)", [
        input.event.po_number,
        input.event.po_total
    ])
}

deny_po_event[msg] if {
    input.event.po_type == "blanket"
    not input.event.po_annual_review_completed
    msg := sprintf("P2P-P001: Blanket PO '%v' lacks annual review documentation in Oracle Fusion Procurement", [input.event.po_number])
}

# ── P-P2P-002: Three-Way Match (PO / GR / Invoice) ───────────────────────────
# Oracle Fusion Payables: Automated invoice matching. po_total doubles as the
# PO's amount for the match-variance comparison below (the original draft
# used a separate purchase_order.amount field for this same real-world
# quantity — standardized on one field name rather than porting that
# inconsistency into the flat shape).
deny_invoice_event[msg] if {
    input.event.inv_matching_type == "3_way"
    abs(input.event.inv_amount - input.event.po_total) > input.event.po_total * 0.05
    msg := sprintf("P2P-P002: Three-way match variance for invoice '%v' exceeds 5%% tolerance — Oracle Fusion AP hold applied, manual review required", [
        input.event.inv_number
    ])
}

deny_invoice_event[msg] if {
    input.event.inv_amount > 10000
    not input.event.goods_receipt_confirmed
    msg := sprintf("P2P-P002: Invoice '%v' ($%v) processed without confirmed goods receipt in Oracle Fusion — three-way match incomplete", [
        input.event.inv_number,
        input.event.inv_amount
    ])
}

# ── P-P2P-003: Vendor Master Data ────────────────────────────────────────────
# Oracle Fusion Supplier Model: Dual-control for sensitive field changes
deny_vendor_event[msg] if {
    # NOTE: real EventType enum value is "VENDOR_MASTER_CHANGE" (uppercase) —
    # fixed from the original lowercase "vendor_master_change" literal, the
    # same dead-rule bug shape pac_contracts.py exists to catch (see its
    # module docstring re: devops_monitoring's branch_protection_rule bug).
    input.event.type == "VENDOR_MASTER_CHANGE"
    input.event.field in ["bank_account_number", "bank_routing_number", "payment_method", "tax_id"]
    not input.event.dual_approved
    msg := sprintf("P2P-P003: Vendor bank detail change to '%v' for supplier '%v' requires dual approval — Oracle Fusion Supplier Model control", [
        input.event.field,
        input.event.vendor_name
    ])
}

# NOTE: "new_vendor_activation" has no corresponding EventType today (no
# producer emits it) — left as documented, pre-existing debt rather than
# invented a new EventType speculatively; see check_module_contract's
# invalid_event_types output for this module.
deny_vendor_event[msg] if {
    input.event.type == "new_vendor_activation"
    not input.event.due_diligence_completed
    msg := sprintf("P2P-P003: New supplier '%v' activated in Oracle Fusion without completed due diligence checklist", [input.event.vendor_name])
}

# ── P-P2P-004: Duplicate Invoice Detection ────────────────────────────────────
deny_invoice_event[msg] if {
    input.event.inv_duplicate_score > 0.85
    not input.event.inv_duplicate_override_reason
    msg := sprintf("P2P-P004: Potential duplicate invoice '%v' (score: %v) in Oracle Fusion AP — manual review required before payment", [
        input.event.inv_number,
        input.event.inv_duplicate_score
    ])
}

# ── P-P2P-005: Payment Run Authorization ─────────────────────────────────────
deny_payment_event[msg] if {
    input.event.pay_batch_total > 100000
    not input.event.pay_batch_treasury_approved
    msg := sprintf("P2P-P005: Oracle Fusion payment batch '%v' ($%v) requires Treasury approval before release (>$100K threshold)", [
        input.event.pay_batch_name,
        input.event.pay_batch_total
    ])
}

deny_payment_event[msg] if {
    input.event.pay_type == "wire_transfer"
    not input.event.pay_two_factor_confirmed
    msg := sprintf("P2P-P005: Wire transfer '%v' ($%v) requires two-factor confirmation in Oracle Fusion Payables", [
        input.event.pay_id,
        input.event.pay_amount
    ])
}

# ── P-P2P-006: Segregation of Duties ─────────────────────────────────────────
deny_sod_event[msg] if {
    "AP_INVOICE_ENTRY" in input.event.user_oracle_roles
    "AP_PAYMENT_APPROVAL" in input.event.user_oracle_roles
    msg := sprintf("P2P-P006: SoD violation — Oracle Fusion user '%v' holds conflicting AP Invoice Entry and Payment Approval roles", [
        input.event.user_username
    ])
}

deny_sod_event[msg] if {
    "PO_BUYER" in input.event.user_oracle_roles
    "AP_INVOICE_APPROVAL" in input.event.user_oracle_roles
    msg := sprintf("P2P-P006: SoD violation — Oracle Fusion user '%v' can both create POs and approve invoices (P2P cycle conflict)", [
        input.event.user_username
    ])
}

# ── Continuous Third-Party/Vendor Risk ────────────────────────────────────────
# Findings ride the generic input.event.* shape (system_telemetry ->
# mcp_governance._evaluate_pac_policy) — vendor_risk_sweep.py (SOC 2 expiry)
# and oracle_fusion_tool.py's spend-concentration check.

# ── P-VEN-001: Vendor SOC 2 Report Expired ────────────────────────────────────
deny_vendor_risk[msg] if {
    input.event.type == "VENDOR_SOC2_EXPIRED"
    msg := sprintf("P-VEN-001: vendor '%v' SOC 2 report expired on %v — no current attestation on file", [
        input.event.vendor_name,
        input.event.soc2_expires_at,
    ])
}

# ── P-VEN-002: Vendor Spend Concentration Breach ──────────────────────────────
deny_vendor_risk[msg] if {
    input.event.type == "VENDOR_CONCENTRATION_BREACH"
    msg := sprintf("P-VEN-002: vendor '%v' accounts for %v%% of trailing %v-day P2P spend — exceeds the %v%% concentration threshold", [
        input.event.vendor_name,
        input.event.concentration_pct,
        input.event.window_days,
        input.event.threshold_pct,
    ])
}
""",

"receive_to_ship": """\
# Oracle Fusion ERP — Receive to Ship (R2S)
# Package:  controls.oracle_fusion.receive_to_ship
# Process:  Receive to Ship
# Version:  1.0
# Approved by: VP Operations, Controller, CFO
# Last Revised: 2026-07-03
# Description: Controls over inbound receipts, inventory management,
#   outbound shipping, and returns processing in Oracle Fusion SCM.

package controls.oracle_fusion.receive_to_ship

import future.keywords.in
import future.keywords.if

# ── P-R2S-001: Goods Receipt Accuracy ────────────────────────────────────────
# Oracle Fusion Inventory Management: Receiving module
deny_receipt_event[msg] if {
    input.goods_receipt.type == "physical"
    abs(input.goods_receipt.quantity_received - input.purchase_order.quantity_ordered) > input.purchase_order.quantity_ordered * 0.02
    msg := sprintf("R2S-P001: Goods receipt '%v' quantity variance >2%% vs PO '%v' — Oracle Fusion receiving exception raised", [
        input.goods_receipt.receipt_number,
        input.purchase_order.po_number
    ])
}

deny_receipt_event[msg] if {
    input.goods_receipt.type == "blind"
    not input.goods_receipt.count_verified_by_second_person
    msg := sprintf("R2S-P001: Blind receipt '%v' in Oracle Fusion requires second-person count verification for items >$5K", [
        input.goods_receipt.receipt_number
    ])
}

# ── P-R2S-002: Inventory Valuation ───────────────────────────────────────────
# Oracle Fusion Cost Management: Inventory costing
deny_inventory_event[msg] if {
    input.inventory.negative_balance == true
    msg := sprintf("R2S-P002: Negative inventory balance detected for item '%v' in Oracle Fusion Cost Management — investigate before period close", [
        input.inventory.item_number
    ])
}

deny_inventory_event[msg] if {
    input.inventory.cycle_count_variance_pct > 5
    not input.inventory.variance_investigation_completed
    msg := sprintf("R2S-P002: Inventory cycle count variance %v%% for '%v' exceeds 5%% threshold in Oracle Fusion — investigation required", [
        input.inventory.cycle_count_variance_pct,
        input.inventory.item_description
    ])
}

deny_inventory_event[msg] if {
    input.inventory.standard_cost_update_lag_days > 180
    msg := sprintf("R2S-P002: Standard cost for item '%v' not updated in %v days — Oracle Fusion Cost Management review required", [
        input.inventory.item_number,
        input.inventory.standard_cost_update_lag_days
    ])
}

# ── P-R2S-003: Shipping Authorization ────────────────────────────────────────
# Oracle Fusion Order Management: Ship Confirm and shipping controls
deny_shipping_event[msg] if {
    input.shipment.type == "outbound"
    not input.sales_order.credit_checked
    msg := sprintf("R2S-P003: Outbound shipment '%v' released in Oracle Fusion Order Management without credit check clearance", [
        input.shipment.shipment_number
    ])
}

deny_shipping_event[msg] if {
    input.shipment.hazardous_materials == true
    not input.shipment.hazmat_compliance_confirmed
    msg := sprintf("R2S-P003: Hazardous material shipment '%v' processed in Oracle Fusion without DOT/IATA compliance confirmation", [
        input.shipment.shipment_number
    ])
}

deny_shipping_event[msg] if {
    input.shipment.value > 500000
    not input.shipment.export_license_verified
    msg := sprintf("R2S-P003: High-value shipment '%v' ($%v) released without export license verification in Oracle Fusion", [
        input.shipment.shipment_number,
        input.shipment.value
    ])
}

# ── P-R2S-004: Returns Processing (RMA) ──────────────────────────────────────
deny_returns_event[msg] if {
    input.return_order.type == "customer_return"
    input.return_order.credit_amount > 5000
    not input.return_order.rma_approved_by
    msg := sprintf("R2S-P004: RMA '%v' for $%v requires manager approval in Oracle Fusion Order Management (>$5K threshold)", [
        input.return_order.rma_number,
        input.return_order.credit_amount
    ])
}

# ── P-R2S-005: Intercompany Transfers ─────────────────────────────────────────
deny_intercompany_event[msg] if {
    input.transfer.type == "intercompany"
    not input.transfer.transfer_pricing_validated
    msg := sprintf("R2S-P005: Intercompany inventory transfer '%v' in Oracle Fusion lacks transfer pricing documentation (IRC §482)", [
        input.transfer.transaction_number
    ])
}

# ── P-R2S-006: Consignment Inventory ─────────────────────────────────────────
deny_consignment_event[msg] if {
    input.inventory.type == "consignment"
    input.inventory.unreconciled_days > 30
    msg := sprintf("R2S-P006: Consignment inventory from supplier '%v' unreconciled for %v days in Oracle Fusion — reconciliation overdue", [
        input.inventory.supplier_name,
        input.inventory.unreconciled_days
    ])
}
""",

"record_to_report": """\
# Oracle Fusion ERP — Record to Report (R2R)
# Package:  controls.oracle_fusion.record_to_report
# Process:  Record to Report
# Version:  1.0
# Approved by: CFO, Controller, External Auditors
# Last Revised: 2026-07-03
# Description: Controls over the full R2R cycle in Oracle Fusion GL:
#   Journal Entry → Account Reconciliation → Period Close → Financial Reporting.

package controls.oracle_fusion.record_to_report

import future.keywords.in
import future.keywords.if

# ── P-R2R-001: Journal Entry Authorization ────────────────────────────────────
# Oracle Fusion General Ledger: Journal approval workflow
deny_journal_event[msg] if {
    input.journal.amount > 10000
    not input.journal.approved_by
    msg := sprintf("R2R-P001: Manual journal entry '%v' for $%v requires approval in Oracle Fusion GL (>$10K threshold)", [
        input.journal.journal_name,
        input.journal.amount
    ])
}

deny_journal_event[msg] if {
    input.journal.type == "manual"
    input.journal.posted_by == input.journal.created_by
    msg := sprintf("R2R-P001: SoD violation — Oracle Fusion GL journal '%v' prepared and posted by the same user '%v'", [
        input.journal.journal_name,
        input.journal.created_by
    ])
}

deny_journal_event[msg] if {
    input.journal.posted_on_weekend == true
    not input.journal.weekend_authorization_code
    msg := sprintf("R2R-P001: Weekend journal entry '%v' in Oracle Fusion GL posted without weekend authorization code", [
        input.journal.journal_name
    ])
}

deny_journal_event[msg] if {
    input.journal.type == "top_side"
    not input.journal.cfo_approved
    msg := sprintf("R2R-P001: Top-side journal entry '%v' for $%v requires CFO approval in Oracle Fusion GL", [
        input.journal.journal_name,
        input.journal.amount
    ])
}

# ── P-R2R-002: Account Reconciliation ─────────────────────────────────────────
# Oracle Fusion Account Reconciliation (ARCS)
deny_recon_event[msg] if {
    input.account_recon.status != "reconciled"
    input.account_recon.period_close_date_passed == true
    msg := sprintf("R2R-P002: Account '%v' not reconciled by period close in Oracle Fusion ARCS — open item requires escalation", [
        input.account_recon.account_name
    ])
}

deny_recon_event[msg] if {
    input.bank_recon.unreconciled_amount > 10000
    input.bank_recon.unreconciled_business_days > 5
    msg := sprintf("R2R-P002: Bank account '%v' has $%v unreconciled for >5 business days in Oracle Fusion Cash Management", [
        input.bank_recon.bank_account,
        input.bank_recon.unreconciled_amount
    ])
}

# ── P-R2R-003: Period Close Procedures ────────────────────────────────────────
deny_close_event[msg] if {
    input.period.status == "closed"
    not input.period.controller_sign_off
    msg := sprintf("R2R-P003: Period '%v' closed in Oracle Fusion without Controller sign-off on close checklist", [input.period.period_name])
}

deny_close_event[msg] if {
    input.period.status == "closed"
    not input.period.cfo_sign_off
    input.period.type == "year_end"
    msg := sprintf("R2R-P003: Year-end period '%v' closed in Oracle Fusion without CFO sign-off", [input.period.period_name])
}

deny_close_event[msg] if {
    input.period.close_completed_after_deadline == true
    msg := sprintf("R2R-P003: Oracle Fusion period close for '%v' completed after scheduled deadline — variance report required", [
        input.period.period_name
    ])
}

# ── P-R2R-004: Subledger to GL Reconciliation ─────────────────────────────────
deny_subledger_event[msg] if {
    abs(input.subledger_recon.gl_balance - input.subledger_recon.subledger_balance) > input.subledger_recon.materiality_threshold
    msg := sprintf("R2R-P004: GL to subledger variance $%v exceeds materiality for account '%v' in Oracle Fusion — must be resolved before close", [
        abs(input.subledger_recon.gl_balance - input.subledger_recon.subledger_balance),
        input.subledger_recon.account_name
    ])
}

# ── P-R2R-005: Trial Balance and Out-of-Balance Detection ─────────────────────
deny_tb_event[msg] if {
    input.trial_balance.out_of_balance_amount != 0
    msg := sprintf("R2R-P005: Oracle Fusion trial balance is out of balance by $%v — period close is blocked", [
        input.trial_balance.out_of_balance_amount
    ])
}

# ── P-R2R-006: Intercompany Eliminations ──────────────────────────────────────
deny_ic_event[msg] if {
    input.intercompany.uneliminated_balance > 0
    not input.intercompany.elimination_approved
    msg := sprintf("R2R-P006: Intercompany balance of $%v not eliminated in Oracle Fusion Financial Consolidation Hub", [
        input.intercompany.uneliminated_balance
    ])
}

# ── P-R2R-007: Financial Statement Disclosure ─────────────────────────────────
deny_disclosure_event[msg] if {
    input.disclosure.type == "segment_reporting"
    not input.disclosure.reconciled_to_consolidated
    msg := sprintf("R2R-P007: Segment disclosure '%v' not reconciled to consolidated financials in Oracle Fusion — ASC 280 compliance gap", [
        input.disclosure.segment_name
    ])
}

# ── Financial Risk Pipeline ────────────────────────────────────────────────────
# Findings ride the generic input.event.* shape (system_telemetry ->
# mcp_governance._evaluate_pac_policy), same convention as devops_monitoring/
# infrastructure_monitoring below — distinct from the input.journal.*/
# input.account_recon.*/etc. shapes P-R2R-001..007 above use, which are
# evaluated directly by oracle_fusion_endpoints.py against live GL data.

# ── P-FIN-001: Manual Journal Entry Velocity Spike ────────────────────────────
deny_financial_risk[msg] if {
    input.event.type == "JE_VELOCITY_ANOMALY"
    input.event.anomaly == true
    msg := sprintf("P-FIN-001: manual journal-entry velocity on '%v' is %vσ above baseline (%v/day vs. baseline %v/day)", [
        input.event.resource,
        input.event.z_score,
        input.event.recent_daily_rate,
        input.event.baseline_daily_mean,
    ])
}

# ── P-FIN-002: Liquidity Shift ─────────────────────────────────────────────────
deny_financial_risk[msg] if {
    input.event.type == "LIQUIDITY_SHIFT"
    input.event.shift_detected == true
    msg := sprintf("P-FIN-002: liquidity shift on '%v' — QoQ ratio delta %vσ below historical norm", [
        input.event.resource,
        input.event.worst_z_score,
    ])
}

# ── P-FIN-003: Inventory/Sales Divergence (Toxic Bloat) ───────────────────────
deny_financial_risk[msg] if {
    input.event.type == "INVENTORY_DIVERGENCE"
    input.event.divergence_detected == true
    msg := sprintf("P-FIN-003: inventory/sales divergence on '%v' — ratio delta %vσ above historical norm (toxic bloat)", [
        input.event.resource,
        input.event.z_score,
    ])
}

# ── Treasury & Cash Management ─────────────────────────────────────────────────
# Findings ride the same generic input.event.* shape as the Financial Risk
# Pipeline section above — oracle_fusion_tool.py's treasury checks.

# ── R2R-TREAS-001: Wire Transfer Single Approval ──────────────────────────────
deny_treasury[msg] if {
    input.event.type == "WIRE_TRANSFER_SINGLE_APPROVAL"
    msg := sprintf("R2R-TREAS-001: wire transfer '%v' for %v %v was processed with only %v approver — dual-approval control bypassed", [
        input.event.payment_id,
        input.event.currency,
        input.event.amount,
        input.event.approver_count,
    ])
}

# ── R2R-TREAS-002: Bank Reconciliation Overdue ────────────────────────────────
deny_treasury[msg] if {
    input.event.type == "BANK_RECON_OVERDUE"
    input.event.days_overdue > 0
    msg := sprintf("R2R-TREAS-002: bank account '%v' reconciliation is %v day(s) overdue — last reconciled %v", [
        input.event.bank_account,
        input.event.days_overdue,
        input.event.last_reconciled_date,
    ])
}

# ── R2R-TREAS-003: FX Hedge Documentation Missing ─────────────────────────────
deny_treasury[msg] if {
    input.event.type == "FX_HEDGE_DOCUMENTATION_MISSING"
    msg := sprintf("R2R-TREAS-003: FX hedge '%v' on %v (notional %v) has no completed hedge-accounting documentation on file", [
        input.event.hedge_id,
        input.event.currency_pair,
        input.event.notional_amount,
    ])
}
""",

"devops_monitoring": """\
# DevOps Monitoring — SCM Integrity + SARIF/SAST Evidence + Pipeline Security
# Package:  controls.devops.monitoring
# Process:  DevOps Monitoring
# Version:  1.1
# Approved by: CISO, VP Engineering
# Last Revised: 2026-07-26
# Description: Branch-protection/CODEOWNERS compliance for GitHub & GitLab
#   repositories, severity-based SLA triggers for ingested SARIF/SAST
#   findings, and GitHub Actions workflow-as-code security (token
#   permissions, unpinned actions, pull_request_target). Evaluated against
#   the synthesized events scm_audit_endpoints.py, evidence_endpoints.py,
#   and pipeline_security_connectors.py produce — see their module docstrings.

package controls.devops.monitoring

import future.keywords.in
import future.keywords.if

# ── DEVOPS-001: Admin Bypass (CRITICAL) ──────────────────────────────────────
deny_branch_protection[msg] if {
    input.event.type == "BRANCH_PROTECTION_BYPASSED"
    input.event.enforce_admins == false
    msg := sprintf("DEVOPS-001: Branch protection on '%v' does not enforce rules for administrators — admins can bypass required checks (CRITICAL)", [input.event.resource])
}

# ── DEVOPS-002: Minimum Approving Reviews ────────────────────────────────────
deny_branch_protection[msg] if {
    input.event.type == "BRANCH_PROTECTION_BYPASSED"
    input.event.required_approving_review_count < 1
    msg := sprintf("DEVOPS-002: Branch '%v' requires zero approving reviews before merge", [input.event.resource])
}

# ── DEVOPS-003: Stale Review Dismissal ───────────────────────────────────────
deny_branch_protection[msg] if {
    input.event.type == "BRANCH_PROTECTION_BYPASSED"
    input.event.dismiss_stale_reviews == false
    msg := sprintf("DEVOPS-003: Branch '%v' does not dismiss stale reviews when new commits are pushed", [input.event.resource])
}

# ── DEVOPS-004: Required Security/Test Status Checks ─────────────────────────
deny_branch_protection[msg] if {
    input.event.type == "BRANCH_PROTECTION_BYPASSED"
    input.event.has_required_sast_check == false
    msg := sprintf("DEVOPS-004: Branch '%v' has no required SAST/security status check", [input.event.resource])
}

deny_branch_protection[msg] if {
    input.event.type == "BRANCH_PROTECTION_BYPASSED"
    input.event.has_required_test_check == false
    msg := sprintf("DEVOPS-004: Branch '%v' has no required unit-test status check", [input.event.resource])
}

# ── DEVOPS-005/006: CODEOWNERS Coverage ──────────────────────────────────────
deny_branch_protection[msg] if {
    input.event.type == "BRANCH_PROTECTION_BYPASSED"
    not input.event.codeowners_present
    msg := sprintf("DEVOPS-005: Repository for '%v' has no CODEOWNERS file", [input.event.resource])
}

deny_branch_protection[msg] if {
    input.event.type == "BRANCH_PROTECTION_BYPASSED"
    input.event.codeowners_present == true
    input.event.codeowners_covers_workflows == false
    msg := sprintf("DEVOPS-006: CODEOWNERS for '%v' does not cover the CI/workflow definition path", [input.event.resource])
}

# ── DEVOPS-007/008: SARIF Evidence Severity SLA ──────────────────────────────
# Critical = 7-day resolution target, High = 30-day (spec thresholds).
deny_evidence_finding[msg] if {
    input.event.severity == "CRITICAL"
    msg := sprintf("DEVOPS-007: CRITICAL SARIF finding '%v' on '%v' — 7-day remediation SLA applies", [input.event.rule_id, input.event.resource])
}

deny_evidence_finding[msg] if {
    input.event.severity == "HIGH"
    msg := sprintf("DEVOPS-008: HIGH SARIF finding '%v' on '%v' — 30-day remediation SLA applies", [input.event.rule_id, input.event.resource])
}

# ── DEVOPS-009: ITSM Ticket SLA Breach ───────────────────────────────────────
deny_sla_breach[msg] if {
    input.event.type == "SLA_BREACH"
    msg := sprintf("DEVOPS-009: ITSM ticket '%v' (%v) for finding '%v' breached its remediation SLA (due %v)", [input.event.external_ticket_key, input.event.external_system, input.event.finding_hash, input.event.sla_due_at])
}

# ── DEVOPS-010: Workflow Permissions Least-Privilege ─────────────────────────
deny_pipeline_security[msg] if {
    input.event.type == "PIPELINE_MISCONFIGURATION"
    input.event.has_write_all_permissions == true
    msg := sprintf("DEVOPS-010: A workflow in '%v' grants write-all GITHUB_TOKEN permissions — scope to only what each job needs", [input.event.resource])
}

# ── DEVOPS-011: Missing Explicit Permissions Block ───────────────────────────
deny_pipeline_security[msg] if {
    input.event.type == "PIPELINE_MISCONFIGURATION"
    input.event.workflows_without_permissions > 0
    msg := sprintf("DEVOPS-011: %v workflow(s) in '%v' have no explicit permissions block — token scope depends on a repo/org default that can silently change", [input.event.workflows_without_permissions, input.event.resource])
}

# ── DEVOPS-012: Unpinned Third-Party Actions ─────────────────────────────────
deny_pipeline_security[msg] if {
    input.event.type == "PIPELINE_MISCONFIGURATION"
    input.event.unpinned_action_count > 0
    msg := sprintf("DEVOPS-012: %v action reference(s) in '%v' are pinned to a mutable tag/branch rather than a commit SHA — a compromised upstream tag changes what runs with no change on this side", [input.event.unpinned_action_count, input.event.resource])
}

# ── DEVOPS-013: Risky pull_request_target + Untrusted Checkout (CRITICAL) ────
deny_pipeline_security[msg] if {
    input.event.type == "PIPELINE_MISCONFIGURATION"
    input.event.has_risky_pull_request_target == true
    msg := sprintf("DEVOPS-013: A workflow in '%v' triggers on pull_request_target and checks out the PR head — a fork PR can execute arbitrary code with write-scoped secrets (CRITICAL)", [input.event.resource])
}
""",

"infrastructure_monitoring": """\
# Infrastructure Monitoring — Continuous IaaS/OS/DB Configuration Audit
# Package:  controls.infrastructure.monitoring
# Process:  Infrastructure Monitoring
# Version:  1.1
# Approved by: CISO, VP Engineering
# Last Revised: 2026-07-27
# Description: Postgres CIS-style hardening checks (postgres_cis_tool.py),
#   Railway platform/deployment drift (railway_iaas_tool.py), and
#   Intelligenza's own connector-credential rotation hygiene
#   (connector_hygiene.py). Evaluated against
#   iaas_connectors.normalize_postgres_compliance()'s output, spread
#   into input.event.* the same way scm_audit_endpoints.py's "compliance"
#   sub-dict is for the devops_monitoring module.

package controls.infrastructure.monitoring

import future.keywords.in
import future.keywords.if

# ── INFRA-001: TLS/SSL Enforcement ───────────────────────────────────────────
deny_db_config[msg] if {
    input.event.type == "INFRASTRUCTURE_FINDING"
    input.event.ssl_enabled == false
    msg := sprintf("INFRA-001: Postgres instance '%v' does not enforce SSL — connections can be made in plaintext (CRITICAL)", [input.event.resource])
}

# ── INFRA-002: Password Encryption Scheme ────────────────────────────────────
deny_db_config[msg] if {
    input.event.type == "INFRASTRUCTURE_FINDING"
    input.event.password_encryption != "scram-sha-256"
    msg := sprintf("INFRA-002: Postgres instance '%v' uses '%v' password encryption instead of scram-sha-256", [input.event.resource, input.event.password_encryption])
}

# ── INFRA-003: Superuser Sprawl ──────────────────────────────────────────────
deny_db_config[msg] if {
    input.event.type == "INFRASTRUCTURE_FINDING"
    input.event.superuser_count > 2
    msg := sprintf("INFRA-003: Postgres instance '%v' has %v superuser roles — excess superusers widen the blast radius of a single compromised credential", [input.event.resource, input.event.superuser_count])
}

# ── INFRA-004: Unencrypted Active Connections ────────────────────────────────
deny_db_config[msg] if {
    input.event.type == "INFRASTRUCTURE_FINDING"
    input.event.unencrypted_connection_count > 0
    msg := sprintf("INFRA-004: Postgres instance '%v' has %v active connection(s) not using SSL", [input.event.resource, input.event.unencrypted_connection_count])
}

# ── INFRA-005: Connection/Disconnection Audit Logging ────────────────────────
deny_db_config[msg] if {
    input.event.type == "INFRASTRUCTURE_FINDING"
    input.event.log_connections == false
    msg := sprintf("INFRA-005: Postgres instance '%v' does not log connections — a compromised credential's access can't be reconstructed after the fact", [input.event.resource])
}

# ── INFRA-006: Public Network Exposure (Railway) ─────────────────────────────
deny_railway_config[msg] if {
    input.event.type == "INFRASTRUCTURE_FINDING"
    input.event.unexpected_public_domain == true
    msg := sprintf("INFRA-006: Service '%v' has a public domain that isn't in the approved baseline — verify this exposure was intentional", [input.event.resource])
}

# ── INFRA-007: Deployment Provenance Mismatch (Railway) ──────────────────────
deny_railway_config[msg] if {
    input.event.type == "INFRASTRUCTURE_FINDING"
    input.event.image_digest_mismatch == true
    msg := sprintf("INFRA-007: Running deployment for '%v' does not match the last approved image digest — verify what's actually deployed", [input.event.resource])
}

# ── INFRA-008: Connector Credential Rotation Staleness ───────────────────────
# Dogfooded on Intelligenza's own observability.poll_connectors credential
# store (connector_hygiene.py) — the one Infrastructure Monitoring check
# with no external system to poll; the system being checked is Intelligenza
# itself.
deny_connector_hygiene[msg] if {
    input.event.type == "INFRASTRUCTURE_FINDING"
    input.event.stale_connector_count > 0
    msg := sprintf("INFRA-008: %v stored connector credential(s) have not been rotated in over the staleness threshold (oldest: %v days) — see the Connector Hygiene panel for which ones", [input.event.stale_connector_count, input.event.oldest_credential_age_days])
}
""",

"hire_to_retire": """\
# Hire-to-Retire — Payroll/HR Continuous Audit
# Package:  controls.hire_to_retire
# Process:  Hire to Retire
# Version:  1.0
# Approved by: CHRO, Controller
# Last Revised: 2026-07-30
# Description: Oracle Fusion HCM payroll findings (oracle_hcm_tool.py) —
#   ghost-employee detection, unauthorized pay-rate changes, and
#   terminated-employee access retention. Evaluated against
#   UBO/pipeline/silver.py's "payroll_detail" spread, the same pattern
#   the Financial Risk Pipeline's "financial_compliance" spread uses.
#   Payroll SoD conflicts route through the existing SOD_VIOLATION event
#   type/itgc-style check, not through this module.

package controls.hire_to_retire

import future.keywords.in
import future.keywords.if

# ── H2R-001: Ghost Employee ──────────────────────────────────────────────────
deny_payroll[msg] if {
    input.event.type == "GHOST_EMPLOYEE_SUSPECTED"
    msg := sprintf("H2R-001: Active payroll run for employee '%v' who was terminated on %v (pay period ending %v) — ghost-employee pattern", [
        input.event.employee_id, input.event.termination_date, input.event.pay_period_end
    ])
}

# ── H2R-002: Unauthorized Pay Rate Change ────────────────────────────────────
deny_payroll[msg] if {
    input.event.type == "UNAUTHORIZED_PAY_RATE_CHANGE"
    input.event.second_approver == false
    msg := sprintf("H2R-002: Pay-rate change of %v%% for employee '%v' ($%v -> $%v) has no second approver on file", [
        input.event.pay_rate_change_pct, input.event.employee_id, input.event.prior_pay_rate, input.event.new_pay_rate
    ])
}

# ── H2R-003: Terminated Employee Access Retained ─────────────────────────────
deny_payroll[msg] if {
    input.event.type == "TERMINATED_EMPLOYEE_ACCESS_RETAINED"
    input.event.days_since_termination > 7
    msg := sprintf("H2R-003: Employee '%v' still has active system access %v days after termination on %v", [
        input.event.employee_id, input.event.days_since_termination, input.event.termination_date
    ])
}
""",

"trade_compliance": """\
# Export Control / Trade Compliance — Restricted-Party Screening
# Package:  controls.trade_compliance
# Process:  Trade Compliance
# Version:  1.0
# Approved by: General Counsel, VP Trade Compliance
# Last Revised: 2026-07-30
# Description: denied_party_screening_tool.py's Consolidated Screening List
#   (OFAC SDN + BIS Entity List + other U.S. government restricted-party
#   lists) screen against active vendor and customer master data. Replaces
#   the prior RSS-keyword-tagging "export control" proxy signal with a real
#   screening control.

package controls.trade_compliance

import future.keywords.in
import future.keywords.if

# ── TC-001: Restricted-Party Match ────────────────────────────────────────────
deny_screening[msg] if {
    input.event.type == "EXPORT_CONTROL_MATCH"
    msg := sprintf("TC-001: %v '%v' matched '%v' on the %v list (score %v, entity %v) — zero-tolerance, transact only after compliance clearance", [
        input.event.party_type,
        input.event.party_name,
        input.event.matched_name,
        input.event.list_source,
        input.event.match_score,
        input.event.entity_number,
    ])
}
""",

}


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class SaveModuleRequest(BaseModel):
    module_name: str = ""
    rego_content: str
    version: str = "1.0"


class ApproveModuleRequest(BaseModel):
    module_id: int
    approver: str
    role: Optional[str] = None


class SaveHookRequest(BaseModel):
    config: Dict[str, Any]


class GenerateCaCRequest(BaseModel):
    controls: List[Dict[str, Any]]
    ticker: Optional[str] = None
    run_id: Optional[int] = None


# ─────────────────────────────────────────────────────────────────────────────
# Policy evaluation — real OPA when available, Python heuristic fallback
# ─────────────────────────────────────────────────────────────────────────────
# Shared by POST /pac/evaluate (web UI) and cac_mcp_server.py's cac_evaluate_event
# (Claude chat tool) so both surfaces give identical results.

def _find_opa_binary() -> Optional[str]:
    """Locate a real OPA binary. OPA_BINARY env var takes precedence over PATH."""
    env_path = os.environ.get("OPA_BINARY", "").strip()
    if env_path and os.path.isfile(env_path):
        return env_path
    return shutil.which("opa")


def _opa_version(opa_bin: str) -> str:
    try:
        proc = subprocess.run([opa_bin, "version"], capture_output=True, text=True, timeout=5)
        return proc.stdout.strip().splitlines()[0] if proc.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


# Every deny_* rule across all 5 default Rego modules formats its message as
# msg := sprintf("<ID>: <text>", [...]) — verified 100% consistent across
# all 51 rules this session. This is the one reliable, already-existing
# source of real control IDs; extracting it is what makes PaC/CaC/RaC share
# an actual identifier instead of three disconnected systems.
_CONTROL_ID_RE = re.compile(r"^([A-Z0-9-]+):")


def _extract_control_id(text: Any) -> Optional[str]:
    """Pull the leading '<ID>: ' token off a rendered Rego msg string (or the
    first element if given OPA's set-valued binding for a partial-set rule)."""
    if isinstance(text, (list, set, tuple)):
        text = next(iter(text), None)
    if not isinstance(text, str):
        return None
    m = _CONTROL_ID_RE.match(text.strip())
    return m.group(1) if m else None


def extract_control_ids_from_defaults() -> list[dict]:
    """Scan every default Rego module's own msg templates for embedded
    control IDs — used once at startup to seed controls_catalog. Returns
    [{"control_id", "name", "process"}, ...], deduped by control_id."""
    seen: dict[str, dict] = {}
    for process, rego_content in _REGO_DEFAULTS.items():
        for m in re.finditer(r'msg\s*:=\s*sprintf\("([A-Z0-9-]+):\s*([^"%]*)', rego_content):
            control_id, desc = m.group(1), m.group(2).strip().rstrip(".,")
            if control_id not in seen:
                seen[control_id] = {
                    "control_id": control_id,
                    "name": desc[:120] if desc else control_id,
                    "process": process,
                }
    return list(seen.values())


# Deliberately duplicated from cac_mcp_server._parse_pac_deny_rules (~15 lines)
# rather than imported — cac_mcp_server already does `from pac_endpoints import
# (...)` at module level, so the reverse import would be circular.
_DENY_RULE_RE = re.compile(r'^(deny_\w+)\[msg\]\s+if\s*\{([^}]+)\}', re.MULTILINE)


def _rule_coverage(rego_content: str) -> dict:
    """How many of a module's deny_*[msg] rules have an extractable control
    ID in their message — surfaced on the Rego Editor as a coverage badge so
    a partially-LLM-converted module's gap is visible, not silent."""
    total = with_id = 0
    for m in _DENY_RULE_RE.finditer(rego_content or ""):
        body = m.group(2)
        msg_match = re.search(r'msg\s*:=\s*sprintf\("([^"]+)"', body)
        if not msg_match:
            msg_match = re.search(r'msg\s*:=\s*"([^"]+)"', body)
        total += 1
        if msg_match and _extract_control_id(msg_match.group(1)):
            with_id += 1
    return {"total": total, "with_control_id": with_id}


def _parse_opa_bindings(bindings: dict) -> tuple[list[dict], list[dict]]:
    """
    Pure parsing step, split out of _run_real_opa_eval so it's unit-testable
    against a captured OPA response shape without needing the opa binary
    installed (this repo's local dev/test environment has no OPA on PATH —
    only the Docker image does — so a bug here can otherwise hide behind
    every test silently running the heuristic fallback instead).

    `bindings` is `parsed["result"][0]["expressions"][0]["value"]` from a
    real `opa eval -f json` response: {rule_name: rule_value, ...} for every
    rule under the queried package.
    """
    fired: list[dict] = []
    passed: list[dict] = []
    for key, val in bindings.items():
        if not (key.startswith("deny") or key.startswith("allow")):
            continue
        # A Rego partial-set rule (`deny_x[msg] if { ...; msg := "..." }`)
        # is serialized by `opa eval -f json` as a JSON OBJECT whose keys
        # are the set's members — e.g. {"DEVOPS-001: ...": true} — never
        # a JSON array, even for a single-member set. A dict here looked
        # enough like "the rule's raw value" that _extract_control_id was
        # called on the whole dict instead of each message, so control_id
        # was silently None for every real-OPA evaluation (the heuristic
        # fallback parses Rego source per rule-block and never hit this).
        # Flatten to one finding per individual message, matching that
        # per-rule-block granularity.
        if isinstance(val, dict):
            messages = list(val.keys())
        elif isinstance(val, list):
            messages = val
        elif val in (None, False, {}, [], set()):
            messages = []
        else:
            messages = [val]

        if not messages:
            passed.append({"rule": key, "value": val, "control_id": None})
            continue
        for msg in messages:
            fired.append({"rule": key, "value": msg, "control_id": _extract_control_id(msg)})
    return fired, passed


def _run_real_opa_eval(rego_content: str, input_event: dict) -> dict:
    """
    Run the actual OPA binary against a Rego module and input document.
    Queries every rule under the module's own package, returning which
    deny/allow rules fired vs. stayed silent.

    Raises RuntimeError if OPA isn't installed, the module has no package
    declaration, or the eval subprocess fails — callers should catch this
    and fall back to _heuristic_evaluate().
    """
    opa_bin = _find_opa_binary()
    if not opa_bin:
        raise RuntimeError("OPA binary not found — set OPA_BINARY or install opa on PATH")

    pkg_match = re.search(r"^package\s+([\w.]+)", rego_content, re.MULTILINE)
    if not pkg_match:
        raise RuntimeError("rego_content has no package declaration")
    package = pkg_match.group(1)

    with tempfile.TemporaryDirectory() as tmp:
        rego_path = os.path.join(tmp, "policy.rego")
        input_path = os.path.join(tmp, "input.json")
        with open(rego_path, "w", encoding="utf-8") as f:
            f.write(rego_content)
        with open(input_path, "w", encoding="utf-8") as f:
            json.dump(input_event, f)

        try:
            proc = subprocess.run(
                [opa_bin, "eval", "-f", "json",
                 "-d", rego_path, "-i", input_path,
                 f"data.{package}"],
                capture_output=True, text=True, timeout=10,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("opa eval timed out after 10s")

        if proc.returncode != 0:
            raise RuntimeError(f"opa eval failed: {(proc.stderr or proc.stdout).strip()[:500]}")

        try:
            parsed = json.loads(proc.stdout)
            bindings = parsed["result"][0]["expressions"][0]["value"] or {}
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"could not parse opa eval output: {exc}")

        fired, passed = _parse_opa_bindings(bindings)

        return {
            "evaluation": "opa eval (authoritative)",
            "opa_version": _opa_version(opa_bin),
            "package": package,
            "rules_fired": fired,
            "rules_passed": passed,
        }


def _heuristic_evaluate(rego_content: str, input_event: dict) -> dict:
    """
    Pattern-match deny rule conditions against an input event without OPA.
    Approximation only — used when no OPA binary is available. Checks
    whether the fields referenced in each deny rule are present and satisfy
    the comparison conditions (==, !=, >, <, >=, <=, or a bare `not` check).
    """
    def _flatten(d: dict, prefix: str = "") -> dict:
        flat: dict = {}
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                flat.update(_flatten(v, key))
            else:
                flat[key] = v
        return flat

    flat_input = _flatten(input_event)

    rule_pattern = re.compile(
        r'^(deny_\w+)\[msg\]\s+if\s*\{([^}]+?)\}',
        re.MULTILINE | re.DOTALL,
    )

    fired: list[dict] = []
    passed: list[dict] = []
    skipped: list[dict] = []

    for m in rule_pattern.finditer(rego_content):
        rule_name = m.group(1)
        body = m.group(2)

        conditions = re.findall(
            r'input\.([\w.]+)\s*(==|!=|>|<|>=|<=|!=)\s*([^\n]+)',
            body,
        )
        not_conditions = re.findall(r'not\s+input\.([\w.]+)', body)

        if not conditions and not not_conditions:
            skipped.append({"rule": rule_name, "reason": "No evaluable conditions found"})
            continue

        score = 0
        total = 0
        detail: list[str] = []

        for path, op, val_str in conditions:
            total += 1
            val_str = val_str.strip().strip('"')
            actual = flat_input.get(f"input.{path}", flat_input.get(path))
            if actual is None:
                detail.append(f"input.{path} not in event (condition unknown)")
                continue
            try:
                val_cmp: object = json.loads(val_str)
            except (json.JSONDecodeError, ValueError):
                val_cmp = val_str
            try:
                result = (
                    (op == "==" and actual == val_cmp) or
                    (op == "!=" and actual != val_cmp) or
                    (op == ">" and float(actual) > float(val_cmp)) or  # type: ignore[arg-type]
                    (op == "<" and float(actual) < float(val_cmp)) or  # type: ignore[arg-type]
                    (op == ">=" and float(actual) >= float(val_cmp)) or  # type: ignore[arg-type]
                    (op == "<=" and float(actual) <= float(val_cmp))      # type: ignore[arg-type]
                )
                if result:
                    score += 1
                detail.append(f"input.{path} {op} {val_cmp!r}: {'✓' if result else '✗'} (actual={actual!r})")
            except (TypeError, ValueError):
                detail.append(f"input.{path} {op} {val_cmp!r}: ? (type mismatch)")

        for path in not_conditions:
            total += 1
            actual = flat_input.get(f"input.{path}", flat_input.get(path))
            satisfied = actual is None or actual is False or actual == ""
            if satisfied:
                score += 1
            detail.append(f"not input.{path}: {'✓' if satisfied else '✗'} (actual={actual!r})")

        confidence = round(score / total, 2) if total else 0.0
        # The msg := sprintf("<ID>: ...") line lives in the same {...} block
        # as the conditions, so it's already inside `body` — no second pass
        # over rego_content needed.
        msg_match = re.search(r'msg\s*:=\s*sprintf\("([^"]+)"', body)
        control_id = _extract_control_id(msg_match.group(1)) if msg_match else None
        entry = {"rule": rule_name, "confidence": confidence, "conditions_checked": detail, "control_id": control_id}
        (fired if confidence >= 0.7 else passed).append(entry)

    return {
        "evaluation": "simulation (Python heuristic — not authoritative OPA)",
        "rules_fired": fired,
        "rules_passed": passed,
        "rules_skipped": skipped,
        "summary": {
            "fired_count": len(fired),
            "passed_count": len(passed),
            "skipped_count": len(skipped),
        },
    }


def evaluate_policy_event(rego_content: str, input_event: dict) -> dict:
    """Evaluate a Rego module against a sample input event — real OPA when
    available, labelled heuristic fallback otherwise."""
    try:
        return _run_real_opa_eval(rego_content, input_event)
    except RuntimeError as exc:
        result = _heuristic_evaluate(rego_content, input_event)
        result["opa_unavailable_reason"] = str(exc)
        return result


# ─────────────────────────────────────────────────────────────────────────────
# CaC generation helper
# ─────────────────────────────────────────────────────────────────────────────

def _controls_to_rego(controls: list, ticker: Optional[str] = None) -> str:
    """Generate Rego Controls-as-Code from the controls library."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        "# Controls as Code — Oracle Fusion ERP",
        f"# Entity: {ticker or 'Global'}",
        f"# Generated: {now}  ·  {len(controls)} controls",
        "# Source: Dendrai Controls Library",
        "#",
        "# Usage: OPA policy engine evaluates control_active[ref]",
        "#   to determine whether a control is in scope and testable.",
        "",
        "package controls.library",
        "",
        "import future.keywords.in",
        "",
    ]

    # Group by category
    by_cat: dict = {}
    for c in controls:
        cat = c.get("category") or "Uncategorised"
        by_cat.setdefault(cat, []).append(c)

    for cat, ctrls in sorted(by_cat.items()):
        divider = "─" * max(0, 76 - len(cat))
        lines.append(f"# ── {cat} {divider}")
        lines.append("")
        for c in ctrls:
            ref  = c.get("ref") or c.get("control_ref", "UNKNOWN")
            name = c.get("name", "").replace('"', '\\"')
            fw   = c.get("framework", "Internal").replace('"', '\\"')
            dom  = c.get("domain", "").replace('"', '\\"')
            desc = (c.get("description") or c.get("desc", "")).replace('"', '\\"')[:200]
            # linked_risks: risk_ref list this control was actually assigned to in
            # the Risk & Controls Register (risk_control_mappings), when the caller
            # supplies it — embeds the risk<->control relationship directly in the
            # artifact itself rather than only in a separate join table, so anyone
            # reading the Rego (or diffing it over time) can see what each control
            # is actually for, not just what it technically does.
            linked = c.get("linked_risks") or []
            lines.append(f'control_active["{ref}"] := {{')
            lines.append(f'    "name":        "{name}",')
            lines.append(f'    "framework":   "{fw}",')
            lines.append(f'    "category":    "{cat}",')
            lines.append(f'    "domain":      "{dom}",')
            lines.append(f'    "description": "{desc}",')
            lines.append(f'    "frequency":   "Quarterly",')
            lines.append(f'    "owner":       "Control Owner",')
            if linked:
                risk_list = ", ".join(f'"{r}"' for r in linked)
                lines.append(f'    "linked_risks": [{risk_list}],')
            lines.append(f'    "test_criteria": [')
            lines.append(f'        "Design effectiveness tested annually",')
            lines.append(f'        "Operating effectiveness tested quarterly",')
            lines.append(f'        "Exceptions documented and remediated within 30 days"')
            lines.append(f'    ]')
            lines.append("}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Business process endpoints
# ─────────────────────────────────────────────────────────────────────────────
# Replaces the formerly-hardcoded 5-entry VALID_PROCESSES set with a real,
# UI/API-manageable catalog — see db.pac_processes and _valid_processes()
# above. Both frontend PAC_PROCESSES arrays (code-screens.jsx, ubo-config.jsx)
# fetch from GET /processes instead of hardcoding the list.

class CreateProcessRequest(BaseModel):
    id: str
    label: str
    short_label: str
    control_prefix: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None


@router.get("/processes")
async def list_processes():
    """All known PaC business processes — built-in + manually added +
    GitHub-discovered. Powers both frontend process pickers."""
    if not db.is_available():
        return {"processes": [
            {"id": p, "label": _PROCESS_LABELS.get(p, p), "short_label": p.upper()[:4],
             "control_prefix": _PROCESS_ID_PREFIX.get(p), "color": None, "icon": None,
             "description": None, "is_builtin": True, "source": "builtin"}
            for p in sorted(_BUILTIN_PROCESS_IDS)
        ]}
    return {"processes": db.list_pac_processes()}


@router.post("/processes")
async def create_process(req: CreateProcessRequest):
    """Manually register a new business process (the other path — automatic
    registration from an unmatched synced folder — happens inside sync_github)."""
    key = _norm_process_key(req.id)
    if not key:
        raise HTTPException(status_code=422, detail="id is required")
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    ok = db.create_pac_process(
        key, req.label.strip() or key, req.short_label.strip() or key.upper()[:16],
        control_prefix=req.control_prefix, color=req.color, icon=req.icon,
        description=req.description, is_builtin=False, source="manual",
    )
    if not ok:
        raise HTTPException(status_code=409, detail=f"Process '{key}' already exists")
    return {"created": True, "id": key}


@router.delete("/processes/{process_id}")
async def delete_process(process_id: str):
    """Delete a non-builtin process. Built-in processes (the original 5)
    cannot be removed this way."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")
    ok = db.delete_pac_process(process_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Process not found or is a built-in process (cannot be deleted)")
    return {"deleted": True, "id": process_id}


# ─────────────────────────────────────────────────────────────────────────────
# Controls catalog — the shared control_id vocabulary PaC/CaC/RaC all read
# from (db.list_controls/upsert_catalog_control). "policy-enforced" means
# source == 'pac_rego': the control_id traces to a real deny_*[msg] Rego
# rule OPA can actually evaluate (see _extract_control_id above). 'manual'
# entries are auditor-assigned business controls with no executable rule
# behind them yet.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/controls/coverage")
async def get_controls_coverage():
    """Aggregate policy-enforcement coverage across the whole controls
    catalog — how many of the org's controls actually have an enforceable
    Rego rule behind them, broken down by process. This is a different
    question from a single module's Control-ID Coverage badge (_rule_coverage,
    which measures whether one module's deny rules are well-formed) — this
    is the org-wide "are our controls actually enforced" view."""
    if not db.is_available():
        return {"total": 0, "policy_enforced": 0, "manual_only": 0, "by_process": [], "controls": []}

    controls = db.list_controls()
    by_process: Dict[str, Dict[str, int]] = {}
    policy_enforced = 0
    for c in controls:
        proc = c.get("process") or "unassigned"
        bucket = by_process.setdefault(proc, {"process": proc, "total": 0, "policy_enforced": 0})
        bucket["total"] += 1
        if c.get("source") == "pac_rego":
            bucket["policy_enforced"] += 1
            policy_enforced += 1

    return {
        "total": len(controls),
        "policy_enforced": policy_enforced,
        "manual_only": len(controls) - policy_enforced,
        "by_process": sorted(by_process.values(), key=lambda b: b["process"]),
        "controls": controls,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Module endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/modules")
async def list_modules():
    """Return latest module for every saved process, falling back to defaults for unsaved ones."""
    saved = {m["process"]: m for m in db.list_pac_modules()} if db.is_available() else {}
    result = []
    for proc in sorted(_valid_processes()):
        if proc in saved:
            result.append(saved[proc])
        else:
            result.append({
                "id": None,
                "process": proc,
                "module_name": f"controls.oracle_fusion.{proc}",
                "version": "1.0",
                "last_revised_at": None,
                "created_at": None,
                "approvals": [],
                "is_default": True,
            })
    return {"modules": result}


@router.get("/modules/{process}")
async def get_module(process: str):
    """Return the latest versioned Rego module for a process (with approvals
    and a rule_coverage summary — how many deny rules have an extractable
    control ID vs. not, so a partially-covered module isn't silently opaque)."""
    if process not in _valid_processes():
        raise HTTPException(status_code=400, detail=f"Unknown process '{process}'. Valid: {sorted(_valid_processes())}")

    if db.is_available():
        mod = db.get_latest_pac_module(process)
        if mod:
            mod["rule_coverage"] = _rule_coverage(mod.get("rego_content", ""))
            return mod

    # Fall back to built-in default
    default_content = _REGO_DEFAULTS.get(process, f"package controls.oracle_fusion.{process}\n")
    return {
        "id": None,
        "process": process,
        "module_name": f"controls.oracle_fusion.{process}",
        "rego_content": default_content,
        "version": "1.0",
        "last_revised_at": None,
        "created_at": None,
        "approvals": [],
        "is_default": True,
        "rule_coverage": _rule_coverage(default_content),
    }


@router.put("/modules/{process}")
async def save_module(process: str, req: SaveModuleRequest):
    """Save a new version of a Rego module for a process."""
    if process not in _valid_processes():
        raise HTTPException(status_code=400, detail=f"Unknown process '{process}'")

    if not req.rego_content.strip():
        raise HTTPException(status_code=422, detail="rego_content must not be empty")

    module_name = req.module_name.strip() or f"controls.oracle_fusion.{process}"

    if not db.is_available():
        return {
            "saved": False,
            "note": "Database not configured — content accepted but not persisted",
            "process": process,
            "version": req.version,
        }

    module_id = db.save_pac_module(process, module_name, req.rego_content, req.version)
    if not module_id:
        raise HTTPException(status_code=500, detail="Failed to save module")

    return {
        "saved": True,
        "module_id": module_id,
        "process": process,
        "module_name": module_name,
        "version": req.version,
    }


@router.get("/modules/{process}/history")
async def get_module_history(process: str):
    """Return version history for a process (newest first, last 20 versions)."""
    if process not in _valid_processes():
        raise HTTPException(status_code=400, detail=f"Unknown process '{process}'")

    if not db.is_available():
        return {"process": process, "history": [], "note": "Database not configured"}

    return {"process": process, "history": db.get_pac_module_history(process)}


@router.post("/modules/{process}/approve")
async def approve_module(process: str, req: ApproveModuleRequest):
    """Add an approver sign-off for a module version.

    Also runs the negative-testing gate (schema-contract check + must-fire/
    must-not-fire corpus) against the EXACT version being approved and
    persists the result as audit evidence — but does not (yet) block the
    approval on failure. Advisory rather than blocking on purpose: today
    every built-in process except devops_monitoring fails the contract check
    (see pac_contracts.py's module docstring — no real producer wires their
    input fields yet), so a hard block would make this endpoint unusable for
    5 of 6 processes with no warning. The result is returned in the response
    so the approver sees it before deciding, and it's on record either way.
    """
    if process not in _valid_processes():
        raise HTTPException(status_code=400, detail=f"Unknown process '{process}'")

    if not req.approver.strip():
        raise HTTPException(status_code=422, detail="approver name is required")

    if not db.is_available():
        return {"saved": False, "note": "Database not configured"}

    approval_id = db.save_pac_approval(req.module_id, req.approver.strip(), req.role)
    if not approval_id:
        raise HTTPException(status_code=500, detail="Failed to save approval")

    negative_test_result = None
    module = db.get_pac_module_by_id(req.module_id)
    if module:
        negative_test_result = pac_assurance.evaluate_and_record(
            process, module["rego_content"], module_id=req.module_id,
            triggered_by="approval_gate", triggered_by_user=req.approver.strip(),
        )

    return {
        "saved": True, "approval_id": approval_id, "approver": req.approver, "role": req.role,
        "negative_test_result": negative_test_result,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Negative testing (pac_contracts.py / pac_negative_tests.py / pac_assurance.py)
# ─────────────────────────────────────────────────────────────────────────────

class RunNegativeTestRequest(BaseModel):
    rego_content: Optional[str] = None  # omit to test the currently-saved (or default) module
    triggered_by: str = "manual"
    triggered_by_user: Optional[str] = None


@router.post("/negative-tests/run/{process}")
async def run_negative_tests(process: str, req: RunNegativeTestRequest):
    """Run the schema-contract check and must-fire/must-not-fire corpus
    against a process's Rego — either an explicit rego_content (e.g. the
    Rego Editor's unsaved draft) or, if omitted, whatever is currently live
    (the latest saved module, falling back to the built-in default)."""
    if process not in _valid_processes():
        raise HTTPException(status_code=400, detail=f"Unknown process '{process}'")

    rego_content = req.rego_content
    module_id = None
    if not rego_content:
        saved = db.get_latest_pac_module(process) if db.is_available() else None
        if saved:
            rego_content = saved["rego_content"]
            module_id = saved.get("id")
        else:
            rego_content = _REGO_DEFAULTS.get(process)
    if not rego_content:
        raise HTTPException(status_code=404, detail=f"No Rego content available for process '{process}'")

    return pac_assurance.evaluate_and_record(
        process, rego_content, module_id=module_id,
        triggered_by=req.triggered_by, triggered_by_user=req.triggered_by_user,
    )


@router.get("/negative-tests/history/{process}")
async def get_negative_test_history(process: str, limit: int = 50):
    """Past negative-control test runs for a process — audit evidence that
    a control was actually tested, and when."""
    if not db.is_available():
        return {"process": process, "runs": []}
    return {"process": process, "runs": db.list_pac_test_runs(process=process, limit=limit)}


@router.get("/assurance")
async def get_assurance(process: Optional[str] = None, stale_days: int = 30):
    """Which policy-enforced controls are currently proven working (recent
    real production fire and/or a passing negative-control test) vs.
    unverified (neither) — the silent-rule-detection view."""
    return pac_assurance.assurance_summary(process=process, stale_days=stale_days)


@router.get("/compliance-scorecard")
async def get_compliance_scorecard(framework: str = "soc2", stale_days: int = 30):
    """
    Executive Compliance Scorecard — for one framework
    ('soc2' | 'nist_800_53' | 'iso_27001' | 'coso'), every criterion any
    control_id is mapped to (framework_mappings.py, curated — never
    auto-generated), how many controls map to it, and how many of those are
    actually PROVEN working per the negative-testing assurance metadata
    (last_fired_at/last_test_passed), not just mapped on paper.

    "Mapped" and "verified" are reported separately and never conflated — a
    criterion can be 100% mapped and 0% verified, and that's the honest
    number to show, not a green checkmark a compliance mapping alone
    doesn't earn.
    """
    if not db.is_available():
        return {"framework": framework, "criteria": [], "note": "Database not configured"}
    return db.get_compliance_scorecard(framework, stale_days=stale_days)


@router.get("/control-flow-map")
async def get_control_flow_map(days: int = 30):
    """
    Process-mining view of the adjudication log: a directly-follows-graph
    (source system -> risk tier -> verdict -> fired control) built from
    REAL observability.adjudicated_tool_calls rows over the last `days`
    days, not the static control catalog structure risk-sankey.jsx renders.
    Control nodes carry their SOC 2/NIST/ISO/COSO crosswalk (from
    controls_catalog) so the frontend can show multi-framework mapping on
    hover without a 5th column.
    """
    if not db.is_available():
        return {"nodes": [], "links": [], "note": "Database not configured"}
    return db.get_control_flow_map(days=days)


@router.get("/approval-drift")
async def get_approval_drift(process: Optional[str] = None):
    """
    Compare what's actually being evaluated in production (the latest SAVED
    module for a process) against the latest version that ever received a
    real approval sign-off — see pac_approval_drift.py's module docstring
    for why a save alone is enough to go live, with no approval gate today.
    A mismatch (drifted=True) means an unapproved or since-edited module is
    currently adjudicating real events.

    Args:
        process: A specific process id, or omit to check every known process.
    """
    import pac_approval_drift  # local import: pac_approval_drift imports this module
                                # for _REGO_DEFAULTS/_valid_processes, so importing it
                                # back at module level here would be circular.
    if process:
        return pac_approval_drift.check_process_drift(process)
    return pac_approval_drift.check_all_processes()


# ─────────────────────────────────────────────────────────────────────────────
# External hook endpoints
# ─────────────────────────────────────────────────────────────────────────────

VALID_HOOK_TYPES = {"github", "confluence"}


@router.get("/hooks")
async def get_hooks():
    """Return all saved external hook configs."""
    if not db.is_available():
        return {"hooks": {}, "note": "Database not configured"}
    hooks = db.get_all_pac_hooks()
    # Flatten: return {hook_type: config_dict}
    return {"hooks": {ht: h["config"] for ht, h in hooks.items()}}


@router.put("/hooks/{hook_type}")
async def save_hook(hook_type: str, req: SaveHookRequest):
    """Save or update an external hook config. Persists until changed."""
    if hook_type not in VALID_HOOK_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown hook type '{hook_type}'. Valid: github, confluence")

    if not db.is_available():
        return {"saved": False, "note": "Database not configured"}

    ok = db.upsert_pac_hook(hook_type, req.config)
    return {"saved": ok, "hook_type": hook_type}


def _parse_github_repo(repo_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub HTTPS URL, e.g.
    'https://github.com/org/policies' -> ('org', 'policies')."""
    parts = [p for p in urlparse(repo_url.strip()).path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"Could not parse an owner/repo from repo_url '{repo_url}'")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    return owner, repo


_SYNC_EXTENSIONS = (".rego", ".md", ".txt")


def _norm_process_key(s: str) -> str:
    return s.strip().lower().replace("-", "_").replace(" ", "_")


def _match_process(path: str) -> Optional[str]:
    """Match a repo file path to a known process by checking the filename
    stem, then every ancestor directory name — case-insensitive, hyphens
    and spaces treated as underscores. Handles both a flat layout
    ('procure-to-pay.md' -> procure_to_pay) and a per-process folder layout
    ('ITGC/access-management.md' -> itgc, via the folder name)."""
    parts = path.split("/")
    filename = parts[-1]
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    valid = _valid_processes()
    for candidate in [stem] + parts[:-1]:
        key = _norm_process_key(candidate)
        if key in valid:
            return key
    return None


def _process_key_from_path(path: str) -> str:
    """Derive a candidate process id from a path when _match_process finds no
    existing one — used by sync_github's auto-register path. Uses the
    filename stem, not the containing folder: each policy document
    (fixed-assets.md, payroll.md, ...) is typically its own independently-
    owned, independently-approved policy, even when several are grouped
    under one category folder (business_cycles/, compliance/, security/) —
    folder-level grouping would merge distinct policies with different
    owners into a single Rego module, losing the ability to version and
    sign off on each one separately."""
    parts = path.split("/")
    filename = parts[-1]
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return _norm_process_key(stem)


def _label_from_key(key: str) -> str:
    """'treasury_ops' -> 'Treasury Ops' — a readable default label for an
    auto-registered process until someone edits it."""
    return " ".join(w.capitalize() for w in key.split("_"))


# ─────────────────────────────────────────────────────────────────────────────
# Markdown/prose -> Rego conversion (external-source sync)
# ─────────────────────────────────────────────────────────────────────────────
# GitHub sync pulls .rego/.md/.txt files verbatim. Rego files are Rego already;
# Markdown/text policy documents need converting before they're usable by the
# deny-rule parser (cac_mcp_server._parse_pac_deny_rules), the OPA evaluator
# (evaluate_policy_event below), and the control_id extraction this file
# already does for real Rego (_extract_control_id / extract_control_ids_from_defaults).

def _looks_like_rego(content: str) -> bool:
    """Heuristic: real Rego declares a package and has at least one deny_* rule."""
    c = content.strip()
    return bool(re.search(r'^package\s+[\w.]+', c, re.MULTILINE)) and "deny_" in c


def _strip_code_fence(text: str) -> str:
    """LLM completions often wrap code in ```rego ... ``` even when told not to.
    Searches anywhere in the text, not just an exact whole-string match — a
    leading disclaimer/preamble sentence before the fence (a common deviation
    even under "output only the code" instructions) made the old anchored
    ^...$ regex fail to match at all, silently falling through to validating
    the prose+fence text as-is instead of the actual Rego inside it."""
    t = text.strip()
    m = re.search(r'```(?:rego)?\s*\n(.*?)\n```', t, re.DOTALL)
    return m.group(1).strip() if m else t


def _validate_rego_syntax(rego_content: str) -> tuple[bool, list[str]]:
    """Real `opa check` syntax validation when the OPA binary is available;
    a lightweight structural fallback otherwise. Mirrors the real-OPA vs.
    heuristic split already used by _run_real_opa_eval/_heuristic_evaluate
    for policy *evaluation* — this is the same idea applied to syntax *checking*
    of freshly LLM-generated Rego before it's ever persisted."""
    opa_bin = _find_opa_binary()
    if opa_bin:
        path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".rego", delete=False, encoding="utf-8") as f:
                f.write(rego_content)
                path = f.name
            proc = subprocess.run([opa_bin, "check", path], capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                return True, []
            return False, [(proc.stderr or proc.stdout).strip()[:500]]
        except Exception as exc:
            return False, [f"opa check failed to run: {exc}"]
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    errors: list[str] = []
    content = rego_content.strip()
    if not re.search(r'^package\s+[\w.]+', content, re.MULTILINE):
        errors.append("Missing 'package' declaration")
    if content.count("{") != content.count("}"):
        errors.append("Unbalanced braces")
    if not re.search(r'^deny_\w+\[msg\]', content, re.MULTILINE):
        errors.append("No deny_*[msg] rules found")
    return (len(errors) == 0), errors


def _convert_markdown_to_rego(process: str, source_path: str, text_content: str) -> str:
    """Ask Claude to translate a prose/Markdown policy document into a Rego
    module matching the Dendrai deny_*[msg] / sprintf("<CONTROL_ID>: text", [...])
    convention every other PaC module follows — control_id extraction
    (_extract_control_id, cac_from_pac, mcp_governance adjudication) all
    depend on that exact format, so the prompt spells it out with a real
    worked example rather than describing it abstractly."""
    prefix = _PROCESS_ID_PREFIX.get(process, process.upper())
    example = _REGO_DEFAULTS.get("receive_to_ship", "")[:1400]
    system = (
        "You convert audit/compliance policy documents (Markdown or prose) into "
        "Open Policy Agent Rego modules for Oracle Fusion ERP controls monitoring. "
        "Output ONLY the Rego module text — no explanation, no markdown code fences.\n\n"
        "Required structure (must match exactly — this is machine-parsed downstream):\n\n"
        f"    package controls.oracle_fusion.{process}\n\n"
        "    import future.keywords.in\n"
        "    import future.keywords.if\n\n"
        "    deny_<category>[msg] if {\n"
        "        <one or more conditions on input.* fields inferred from the policy text>\n"
        f'        msg := sprintf("{prefix}-<ID>: <human-readable violation description>", [<interpolated fields>])\n'
        "    }\n\n"
        f"Control IDs must start with '{prefix}-', be unique within the module, and be "
        "the first token in the sprintf message string followed by a colon. Every "
        "distinct control or requirement described in the source document should become "
        "one or more deny_* rules. Infer reasonable input.* field names from the policy's "
        "subject matter. This includes reference/crosswalk-style documents (control "
        "catalogs, framework requirement lists, mappings between standards) that have no "
        "explicit prohibition text — for those, write deny rules that check whether each "
        "listed control/requirement/mapping is present and implemented, e.g. "
        f'deny_missing_control[msg] if {{ not input.controls[\"<id>\"].implemented; '
        f'msg := sprintf("{prefix}-<ID>: <control> is not marked implemented", []) }}. '
        "Every document must produce at least one deny_* rule — never respond with an "
        "explanation of why the document doesn't fit the format. "
        "Follow the style of this real example from another process:\n\n"
        f"{example}"
    )
    user = f"Source file: {source_path}\nProcess: {process}\n\n---\n\n{text_content}"
    return claude_client.complete_text(
        system, user,
        label="pac_markdown_to_rego", effort="high", max_tokens=16000,
    )


@router.post("/hooks/github/sync")
async def sync_github():
    """
    Recursively pull every .rego/.md/.txt file out of the configured repo
    path and import it as a Policy-as-Code module, matching each file to a
    known process by filename or containing folder (e.g. 'itgc.rego' or
    'ITGC/access-management.md' both resolve to process 'itgc'). Multiple
    files matching the same process are concatenated into one module, each
    section marked with its source path. Files that don't match a known
    process are reported back as skipped, not silently dropped.

    Markdown/text policy documents are converted to Rego via Claude before
    saving (matching the deny_*[msg]/sprintf("<ID>: ...") convention every
    other module follows); a file whose conversion fails OPA syntax
    validation is skipped rather than saved broken.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured")

    hook = db.get_pac_hook("github")
    config = (hook or {}).get("config") or {}
    repo_url = (config.get("repo_url") or "").strip()
    if not repo_url:
        raise HTTPException(status_code=400, detail="GitHub hook not configured — save the repo URL first")

    branch = (config.get("branch") or "main").strip()
    path_filter = (config.get("path_filter") or "").strip().strip("/")
    # The UI's field key is 'pat'; accept 'token' too for anything saved via
    # the pac_save_hook MCP tool, which uses that name instead.
    token = (config.get("pat") or config.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="No Personal Access Token saved for the GitHub hook")

    try:
        owner, repo = _parse_github_repo(repo_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return await _sync_github_repo(owner, repo, branch, path_filter, token)


async def _sync_github_repo(
    owner: str, repo: str, branch: str, path_filter: str, token: str,
    process_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Shared GitHub-pull-and-import logic behind both the single legacy GitHub
    hook (/hooks/github/sync) and the multi-repository registry
    (POST /observability/pac-repos/{id}/sync in mcp_governance.py). Identical
    behavior either way; only the source of (owner, repo, branch, path_filter,
    token) differs.

    process_hint: when set (a repo registered against one specific process
    rather than "all"), a file that doesn't match any known process by name
    is filed under process_hint instead of auto-registering a brand-new
    process — lets a repo's "Linked process" field actually mean something.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "dendrai-policy-as-code-sync",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            tr = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}",
                params={"recursive": "1"}, headers=headers,
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach GitHub: {exc}")

        if tr.status_code == 401:
            raise HTTPException(status_code=401, detail="GitHub rejected the token — it may be invalid or expired")
        if tr.status_code == 403:
            raise HTTPException(status_code=403, detail="GitHub denied access (403) — check the token's repo permissions")
        if tr.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Branch '{branch}' not found on {owner}/{repo}")
        if tr.status_code != 200:
            raise HTTPException(status_code=502, detail=f"GitHub returned {tr.status_code}: {tr.text[:300]}")

        tree = tr.json().get("tree", [])
        blobs = [
            item for item in tree
            if item.get("type") == "blob"
            and item["path"].lower().endswith(_SYNC_EXTENSIONS)
            and (not path_filter or item["path"] == path_filter or item["path"].startswith(path_filter + "/"))
        ]

        by_process: Dict[str, List[Dict[str, str]]] = {}
        skipped: List[Dict[str, str]] = []
        newly_registered: set[str] = set()

        for item in blobs:
            process = _match_process(item["path"])
            if not process and process_hint and process_hint in _valid_processes():
                process = process_hint
            if not process:
                # pac_processes.id is VARCHAR(32) — an un-truncated candidate
                # (e.g. a long descriptive filename stem) made create_pac_process
                # fail on every such file with "value too long for type
                # character varying", logged but never surfaced to the sync
                # response, so these files silently piled up as "database
                # insert failed" skips.
                candidate = _process_key_from_path(item["path"])[:32]
                if not candidate or not db.is_available():
                    skipped.append({
                        "name": item["path"],
                        "reason": f"doesn't match a known process ({', '.join(sorted(_valid_processes()))})"
                                  + ("" if db.is_available() else " — database unavailable, cannot auto-register"),
                    })
                    continue
                # No known process matched this path — register a new one from
                # the folder/file name rather than silently dropping the file.
                # Idempotent (ON CONFLICT DO NOTHING) so re-syncing the same
                # repo doesn't error on a process another file already registered.
                # create_pac_process returns False both when the row already
                # exists (fine) and when the insert genuinely failed (not
                # fine) — re-check membership rather than trusting the return
                # value, so a failed insert doesn't silently save an orphaned
                # module under a process id that isn't actually registered.
                if candidate not in newly_registered:
                    db.create_pac_process(
                        candidate, _label_from_key(candidate), candidate.upper()[:16],
                        control_prefix=candidate.upper()[:16],
                        color=_AUTO_PROCESS_COLORS[len(newly_registered) % len(_AUTO_PROCESS_COLORS)],
                        icon="📁", source="github_discovered",
                    )
                    newly_registered.add(candidate)
                if candidate not in _valid_processes():
                    skipped.append({
                        "name": item["path"],
                        "reason": f"could not auto-register process '{candidate}' — database insert failed",
                    })
                    continue
                process = candidate
                logger.info("sync_github: auto-registered new PaC process '%s' from %s", candidate, item["path"])
            try:
                br = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/git/blobs/{item['sha']}",
                    headers=headers,
                )
                br.raise_for_status()
                blob = br.json()
                content = base64.b64decode(blob["content"]).decode("utf-8", errors="replace")
            except Exception as exc:
                skipped.append({"name": item["path"], "reason": f"failed to fetch: {exc}"})
                continue
            by_process.setdefault(process, []).append({"path": item["path"], "content": content})

        # Convert every non-Rego file across all processes concurrently. This
        # used to be a sequential await-per-file loop — one Claude call per
        # Markdown/text policy doc — so a repo with several such files easily
        # pushed the whole request past Railway's edge-gateway timeout, which
        # returns a bare 502 to the browser (nginx's own 600s proxy_read_timeout
        # never even comes into play) long before the request actually
        # finishes server-side. Running the conversions concurrently bounds
        # the added time to roughly the slowest single file instead of the
        # sum of all of them.
        async def _convert_one(process: str, f: Dict[str, str]) -> tuple[str, str]:
            try:
                converted = await asyncio.to_thread(
                    _convert_markdown_to_rego, process, f["path"], f["content"]
                )
            except Exception as exc:
                return "error", f"Markdown→Rego conversion failed: {exc}"
            rego = _strip_code_fence(converted)
            ok, errors = _validate_rego_syntax(rego)
            if not ok:
                # The API response only ever showed the validation errors, never
                # what the model actually returned — made failures like these
                # impossible to diagnose without guessing. Log a preview so the
                # raw completion is visible in server logs on the next failure.
                logger.warning(
                    "pac_markdown_to_rego validation failed for %s (%d chars): %s | preview: %r",
                    f["path"], len(converted), "; ".join(errors), converted[:400],
                )
                return "error", f"converted Rego failed validation: {'; '.join(errors)}"
            return "ok", rego

        to_convert: List[tuple[str, Dict[str, str]]] = []
        for process, files in by_process.items():
            files.sort(key=lambda f: f["path"])
            for f in files:
                if not _looks_like_rego(f["content"]):
                    to_convert.append((process, f))

        conversion_results = (
            await asyncio.gather(*[_convert_one(process, f) for process, f in to_convert])
            if to_convert else []
        )
        converted_map: Dict[str, tuple[str, str]] = {
            f["path"]: result for (process, f), result in zip(to_convert, conversion_results)
        }

        imported: List[Dict[str, Any]] = []
        for process, files in by_process.items():
            sections: List[str] = []
            converted_any = False
            for f in files:
                if _looks_like_rego(f["content"]):
                    sections.append(f"# ─── {f['path']} ───\n\n{f['content']}")
                    continue
                kind, payload = converted_map[f["path"]]
                if kind == "error":
                    skipped.append({"name": f["path"], "reason": payload})
                    continue
                sections.append(f"# ─── {f['path']} (converted from Markdown by Claude) ───\n\n{payload}")
                converted_any = True

            if not sections:
                skipped.append({"name": ", ".join(f["path"] for f in files), "reason": "no file in this process produced valid Rego"})
                continue

            combined = "\n\n".join(sections)
            module_name = f"controls.oracle_fusion.{process}"
            source_format = "llm_converted" if converted_any else "rego"
            module_id = db.save_pac_module(process, module_name, combined, "1.0", source_format=source_format)
            if module_id:
                imported.append({
                    "process": process, "module_name": module_name, "module_id": module_id,
                    "file_count": len(files), "source_format": source_format,
                })
            else:
                skipped.append({"name": ", ".join(f["path"] for f in files), "reason": "database save failed"})

    return {
        "synced": True,
        "repo": f"{owner}/{repo}",
        "branch": branch,
        "path": path_filter or "/",
        "files_found": len(blobs),
        "imported": imported,
        "skipped": skipped,
        "newly_registered": sorted(newly_registered),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Controls-as-Code endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/cac/generate")
async def generate_cac(req: GenerateCaCRequest):
    """Generate a Controls-as-Code Rego artifact from the supplied controls list and persist to DB."""
    if not req.controls:
        raise HTTPException(status_code=422, detail="controls list must not be empty")

    content_rego = _controls_to_rego(req.controls, req.ticker)

    artifact_id: Optional[int] = None
    if db.is_available():
        artifact_id = db.save_controls_as_code_artifact(content_rego, req.ticker, req.run_id)

        # Save embedding so the CaC content is searchable via vector similarity
        if artifact_id:
            try:
                db.save_embedding(
                    source_table="controls_as_code_artifacts",
                    source_id=artifact_id,
                    content_type=db.EMBT_CAC,
                    text=content_rego[:8000],   # truncate for embedding budget
                )
            except Exception as exc:
                logger.debug("CaC embedding skipped (non-fatal): %s", exc)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ticker": req.ticker,
        "control_count": len(req.controls),
        "artifact_id": artifact_id,
        "content_rego": content_rego,
    }


@router.get("/cac/latest")
async def get_latest_cac(ticker: Optional[str] = None):
    """Return the most recent CaC artifact, optionally filtered by ticker."""
    if not db.is_available():
        return {"artifact": None, "note": "Database not configured"}

    artifact = db.get_latest_cac_artifact(ticker)
    return {"artifact": artifact}


class EvaluateRequest(BaseModel):
    rego_content: str
    input_event: Dict[str, Any]


@router.post("/evaluate")
async def evaluate_policy(req: EvaluateRequest):
    """
    Evaluate a Rego module against a sample input event.

    Uses the real OPA binary when found (OPA_BINARY env var or `opa` on
    PATH) for an authoritative result; falls back to a labelled Python
    heuristic pattern-matcher otherwise. Same logic Claude uses via the
    cac_evaluate_event MCP tool, so both surfaces agree.
    """
    if not req.rego_content.strip():
        raise HTTPException(status_code=422, detail="rego_content must not be empty")
    return evaluate_policy_event(req.rego_content, req.input_event)


# ─────────────────────────────────────────────────────────────────────────────
# Default Rego getter (for frontend bootstrap without a DB hit)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/defaults/{process}")
async def get_default_rego(process: str):
    """Return the built-in Rego default for a process (no DB required)."""
    if process not in _valid_processes():
        raise HTTPException(status_code=400, detail=f"Unknown process '{process}'")
    return {
        "process": process,
        "rego_content": _REGO_DEFAULTS.get(process, ""),
        "is_default": True,
    }
