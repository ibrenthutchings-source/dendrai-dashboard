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
    POST /pac/cac/generate                Generate Controls-as-Code Rego from controls library
    GET  /pac/cac/latest                  Get the latest CaC artifact
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pac", tags=["pac"])

# ─────────────────────────────────────────────────────────────────────────────
# Supported processes
# ─────────────────────────────────────────────────────────────────────────────

VALID_PROCESSES = {
    "itgc",
    "order_to_cash",
    "procure_to_pay",
    "receive_to_ship",
    "record_to_report",
}

_PROCESS_LABELS = {
    "itgc":            "IT General Controls",
    "order_to_cash":   "Order to Cash",
    "procure_to_pay":  "Procure to Pay",
    "receive_to_ship": "Receive to Ship",
    "record_to_report": "Record to Report",
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
# Oracle Fusion Revenue Management module
deny_revenue_event[msg] if {
    input.transaction.type == "revenue_recognition"
    not input.transaction.performance_obligation_satisfied
    msg := sprintf("OTC-P001: Revenue $%v recognized for order '%v' before performance obligation satisfied — ASC 606 violation", [
        input.transaction.amount,
        input.transaction.order_number
    ])
}

deny_revenue_event[msg] if {
    input.contract.value > 1000000
    not input.contract.reviewed_by_legal
    msg := sprintf("OTC-P001: High-value contract '%v' ($%v) recognized without legal review in Oracle Fusion Revenue Management", [
        input.contract.id,
        input.contract.value
    ])
}

deny_revenue_event[msg] if {
    input.transaction.type == "variable_consideration"
    not input.transaction.constrained_estimate_documented
    msg := sprintf("OTC-P001: Variable consideration for contract '%v' lacks constrained estimate documentation (ASC 606-10-32)", [input.transaction.contract_id])
}

# ── P-OTC-002: Credit Management ─────────────────────────────────────────────
# Oracle Fusion Order Management: Credit check integration
deny_order_event[msg] if {
    input.sales_order.status == "booked"
    input.customer.credit_limit > 0
    input.sales_order.total > input.customer.credit_limit
    not input.sales_order.credit_override_approved_by
    msg := sprintf("OTC-P002: Sales order '%v' ($%v) exceeds customer credit limit ($%v) — override approval required in Oracle Fusion", [
        input.sales_order.order_number,
        input.sales_order.total,
        input.customer.credit_limit
    ])
}

# ── P-OTC-003: Billing Accuracy ───────────────────────────────────────────────
# Oracle Fusion AR: AutoInvoice and billing controls
deny_billing_event[msg] if {
    input.invoice.type == "manual"
    not input.invoice.approved_by
    input.invoice.amount > 10000
    msg := sprintf("OTC-P003: Manual invoice '%v' for $%v requires manager approval in Oracle Fusion AR (>$10K threshold)", [
        input.invoice.invoice_number,
        input.invoice.amount
    ])
}

deny_billing_event[msg] if {
    input.invoice.billing_date > input.invoice.shipment_date
    input.invoice.days_billed_before_shipment > 30
    msg := sprintf("OTC-P003: Invoice '%v' billed %v days before shipment — premature revenue risk in Oracle Fusion AR", [
        input.invoice.invoice_number,
        input.invoice.days_billed_before_shipment
    ])
}

# ── P-OTC-004: Cash Application ───────────────────────────────────────────────
# Oracle Fusion AR: Cash receipts and unapplied cash monitoring
deny_cash_event[msg] if {
    input.cash_receipt.unapplied_days > 30
    msg := sprintf("OTC-P004: Cash receipt '%v' ($%v) unapplied for %v days — Oracle Fusion AR SLA breach", [
        input.cash_receipt.receipt_number,
        input.cash_receipt.amount,
        input.cash_receipt.unapplied_days
    ])
}

# ── P-OTC-005: Customer Master Data Integrity ─────────────────────────────────
deny_customer_event[msg] if {
    input.event.type == "customer_master_change"
    input.event.field in ["bank_account", "payment_terms", "billing_address", "tax_id"]
    not input.event.dual_approved
    msg := sprintf("OTC-P005: Customer master change to '%v' for customer '%v' requires dual approval in Oracle Fusion Customer Model", [
        input.event.field,
        input.event.customer_name
    ])
}

# ── P-OTC-006: Accounts Receivable Aging ─────────────────────────────────────
deny_ar_event[msg] if {
    input.ar_balance.days_outstanding > 90
    input.ar_balance.amount > 50000
    not input.ar_balance.collection_action_documented
    msg := sprintf("OTC-P006: AR balance $%v for customer '%v' is %v days outstanding without documented collection action in Oracle Fusion", [
        input.ar_balance.amount,
        input.ar_balance.customer_name,
        input.ar_balance.days_outstanding
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
# Oracle Fusion Procurement: Approval Management Engine (AME)
deny_po_event[msg] if {
    input.purchase_order.total > 50000
    not input.purchase_order.vp_approved
    msg := sprintf("P2P-P001: PO '%v' for $%v requires VP approval in Oracle Fusion Procurement (>$50K threshold)", [
        input.purchase_order.po_number,
        input.purchase_order.total
    ])
}

deny_po_event[msg] if {
    input.purchase_order.total > 250000
    not input.purchase_order.cfo_approved
    msg := sprintf("P2P-P001: PO '%v' for $%v requires CFO approval in Oracle Fusion Procurement (>$250K threshold)", [
        input.purchase_order.po_number,
        input.purchase_order.total
    ])
}

deny_po_event[msg] if {
    input.purchase_order.type == "blanket"
    not input.purchase_order.annual_review_completed
    msg := sprintf("P2P-P001: Blanket PO '%v' lacks annual review documentation in Oracle Fusion Procurement", [input.purchase_order.po_number])
}

# ── P-P2P-002: Three-Way Match (PO / GR / Invoice) ───────────────────────────
# Oracle Fusion Payables: Automated invoice matching
deny_invoice_event[msg] if {
    input.invoice.matching_type == "3_way"
    abs(input.invoice.amount - input.purchase_order.amount) > input.purchase_order.amount * 0.05
    msg := sprintf("P2P-P002: Three-way match variance for invoice '%v' exceeds 5%% tolerance — Oracle Fusion AP hold applied, manual review required", [
        input.invoice.invoice_number
    ])
}

deny_invoice_event[msg] if {
    input.invoice.amount > 10000
    not input.goods_receipt.confirmed
    msg := sprintf("P2P-P002: Invoice '%v' ($%v) processed without confirmed goods receipt in Oracle Fusion — three-way match incomplete", [
        input.invoice.invoice_number,
        input.invoice.amount
    ])
}

# ── P-P2P-003: Vendor Master Data ────────────────────────────────────────────
# Oracle Fusion Supplier Model: Dual-control for sensitive field changes
deny_vendor_event[msg] if {
    input.event.type == "vendor_master_change"
    input.event.field in ["bank_account_number", "bank_routing_number", "payment_method", "tax_id"]
    not input.event.dual_approved
    msg := sprintf("P2P-P003: Vendor bank detail change to '%v' for supplier '%v' requires dual approval — Oracle Fusion Supplier Model control", [
        input.event.field,
        input.event.vendor_name
    ])
}

deny_vendor_event[msg] if {
    input.event.type == "new_vendor_activation"
    not input.event.due_diligence_completed
    msg := sprintf("P2P-P003: New supplier '%v' activated in Oracle Fusion without completed due diligence checklist", [input.event.vendor_name])
}

# ── P-P2P-004: Duplicate Invoice Detection ────────────────────────────────────
deny_invoice_event[msg] if {
    input.invoice.duplicate_score > 0.85
    not input.invoice.duplicate_override_reason
    msg := sprintf("P2P-P004: Potential duplicate invoice '%v' (score: %v) in Oracle Fusion AP — manual review required before payment", [
        input.invoice.invoice_number,
        input.invoice.duplicate_score
    ])
}

# ── P-P2P-005: Payment Run Authorization ─────────────────────────────────────
deny_payment_event[msg] if {
    input.payment_batch.total > 100000
    not input.payment_batch.treasury_approved
    msg := sprintf("P2P-P005: Oracle Fusion payment batch '%v' ($%v) requires Treasury approval before release (>$100K threshold)", [
        input.payment_batch.batch_name,
        input.payment_batch.total
    ])
}

deny_payment_event[msg] if {
    input.payment.type == "wire_transfer"
    not input.payment.two_factor_confirmed
    msg := sprintf("P2P-P005: Wire transfer '%v' ($%v) requires two-factor confirmation in Oracle Fusion Payables", [
        input.payment.payment_id,
        input.payment.amount
    ])
}

# ── P-P2P-006: Segregation of Duties ─────────────────────────────────────────
deny_sod_event[msg] if {
    "AP_INVOICE_ENTRY" in input.user.oracle_roles
    "AP_PAYMENT_APPROVAL" in input.user.oracle_roles
    msg := sprintf("P2P-P006: SoD violation — Oracle Fusion user '%v' holds conflicting AP Invoice Entry and Payment Approval roles", [
        input.user.username
    ])
}

deny_sod_event[msg] if {
    "PO_BUYER" in input.user.oracle_roles
    "AP_INVOICE_APPROVAL" in input.user.oracle_roles
    msg := sprintf("P2P-P006: SoD violation — Oracle Fusion user '%v' can both create POs and approve invoices (P2P cycle conflict)", [
        input.user.username
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
            lines.append(f'control_active["{ref}"] := {{')
            lines.append(f'    "name":        "{name}",')
            lines.append(f'    "framework":   "{fw}",')
            lines.append(f'    "category":    "{cat}",')
            lines.append(f'    "domain":      "{dom}",')
            lines.append(f'    "description": "{desc}",')
            lines.append(f'    "frequency":   "Quarterly",')
            lines.append(f'    "owner":       "Control Owner",')
            lines.append(f'    "test_criteria": [')
            lines.append(f'        "Design effectiveness tested annually",')
            lines.append(f'        "Operating effectiveness tested quarterly",')
            lines.append(f'        "Exceptions documented and remediated within 30 days"')
            lines.append(f'    ]')
            lines.append("}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Module endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/modules")
async def list_modules():
    """Return latest module for every saved process, falling back to defaults for unsaved ones."""
    saved = {m["process"]: m for m in db.list_pac_modules()} if db.is_available() else {}
    result = []
    for proc in sorted(VALID_PROCESSES):
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
    """Return the latest versioned Rego module for a process (with approvals)."""
    if process not in VALID_PROCESSES:
        raise HTTPException(status_code=400, detail=f"Unknown process '{process}'. Valid: {sorted(VALID_PROCESSES)}")

    if db.is_available():
        mod = db.get_latest_pac_module(process)
        if mod:
            return mod

    # Fall back to built-in default
    return {
        "id": None,
        "process": process,
        "module_name": f"controls.oracle_fusion.{process}",
        "rego_content": _REGO_DEFAULTS.get(process, f"package controls.oracle_fusion.{process}\n"),
        "version": "1.0",
        "last_revised_at": None,
        "created_at": None,
        "approvals": [],
        "is_default": True,
    }


@router.put("/modules/{process}")
async def save_module(process: str, req: SaveModuleRequest):
    """Save a new version of a Rego module for a process."""
    if process not in VALID_PROCESSES:
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
    if process not in VALID_PROCESSES:
        raise HTTPException(status_code=400, detail=f"Unknown process '{process}'")

    if not db.is_available():
        return {"process": process, "history": [], "note": "Database not configured"}

    return {"process": process, "history": db.get_pac_module_history(process)}


@router.post("/modules/{process}/approve")
async def approve_module(process: str, req: ApproveModuleRequest):
    """Add an approver sign-off for a module version."""
    if process not in VALID_PROCESSES:
        raise HTTPException(status_code=400, detail=f"Unknown process '{process}'")

    if not req.approver.strip():
        raise HTTPException(status_code=422, detail="approver name is required")

    if not db.is_available():
        return {"saved": False, "note": "Database not configured"}

    approval_id = db.save_pac_approval(req.module_id, req.approver.strip(), req.role)
    if not approval_id:
        raise HTTPException(status_code=500, detail="Failed to save approval")

    return {"saved": True, "approval_id": approval_id, "approver": req.approver, "role": req.role}


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


# ─────────────────────────────────────────────────────────────────────────────
# Default Rego getter (for frontend bootstrap without a DB hit)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/defaults/{process}")
async def get_default_rego(process: str):
    """Return the built-in Rego default for a process (no DB required)."""
    if process not in VALID_PROCESSES:
        raise HTTPException(status_code=400, detail=f"Unknown process '{process}'")
    return {
        "process": process,
        "rego_content": _REGO_DEFAULTS.get(process, ""),
        "is_default": True,
    }
