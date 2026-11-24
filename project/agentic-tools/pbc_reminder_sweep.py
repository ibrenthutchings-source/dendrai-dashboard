#!/usr/bin/env python3
"""
PBC request reminders and escalation.

Periodically looks at requests still waiting on evidence and posts a reminder to
the alert webhook (MCP_ALERT_WEBHOOK_URL, Slack-compatible, the same channel the
rest of the platform alerts on) when one comes due soon, is due today, is overdue,
or has been overdue a week (escalation). The schedule and the "only the current
kind" rule are in pbc_request_tool.reminder_kind.

  * Idempotent: each (request, kind) is claimed in observability.pbc_request_reminders
    before it is sent, so a restart or a second instance cannot send it twice.
  * Does nothing, and claims nothing, when no webhook is configured: otherwise
    reminders would be marked sent without anyone being told.
  * Reminders are grouped into one message per kind. A failed webhook call is
    logged by mcp_governance and the reminder is not retried (it was claimed);
    the Requests screen and /pbc-requests/summary still show everything overdue.
  * Single-tenant only for now: with TENANT_MODE=multi it sends nothing, because the
    webhook is one global URL and a reminder contains a tenant's engagement and control
    names. The Requests screen still shows overdue items there.
  * Each pass that sends anything writes one audit-log entry (category "pbc").

Shape copied from risk_waiver_sweep.py: hourly loop, errors caught, never exits except
on cancellation.
"""
from __future__ import annotations

import asyncio
import logging

import db
import mcp_governance
import pbc_request_tool as prt

logger = logging.getLogger(__name__)

_TICK_S = 3600
_MAX_LINES = 25
_HEADINGS = {
    "due_soon": ("PBC requests due soon", "#f39c12"),
    "due_today": ("PBC requests due today", "#e67e22"),
    "overdue": ("PBC requests OVERDUE", "#c0392b"),
    "escalation": ("PBC requests ESCALATED: overdue a week or more", "#8e1b10"),
}


def _post(kind: str, items: list) -> None:
    title, color = _HEADINGS[kind]
    fields = [{"title": f"{r['engagement']} / {r['control_id']}",
               "value": f"{r['title']} (due {r['due_date']}, owner {r.get('owner') or 'unassigned'})"}
              for r in items[:_MAX_LINES]]
    if len(items) > _MAX_LINES:
        fields.append({"title": "...", "value": f"and {len(items) - _MAX_LINES} more"})
    mcp_governance._post_webhook_alert(f"{title}: {len(items)}", fields, color=color)


def _sweep_sync() -> dict:
    if not mcp_governance._ALERT_WEBHOOK_URL:
        return {}
    if db._current_tenant.get() is not None:
        # Multi-tenant: the only webhook is one global URL, and a reminder names a
        # tenant's engagements and controls. Posting it there would send one tenant's
        # data to a shared channel. Needs per-tenant webhooks first.
        return {}
    by_kind: dict = {}
    for r in db.list_unanswered_pbc_requests():
        kind = prt.reminder_kind(r["status"], r["due_date"], set(r.get("reminders_sent") or []))
        if kind and db.claim_pbc_reminder(r["id"], kind):
            by_kind.setdefault(kind, []).append(r)
    for kind in prt.REMINDER_KINDS:
        if by_kind.get(kind):
            _post(kind, by_kind[kind])
    counts = {k: len(v) for k, v in by_kind.items()}
    if counts:
        try:
            db.insert_audit_log_entry("pbc", "pbc_reminders_sent", actor="pbc-reminder-sweep", target=None,
                                      detail=counts, outcome="success")
        except Exception as exc:
            logger.warning("pbc_reminder_sweep: audit write failed: %s", exc)
    return counts


async def sweep_once() -> int:
    """One pass. Returns the number of reminders sent (exposed for tests and multi-tenant ticks)."""
    counts = await asyncio.to_thread(_sweep_sync)
    total = sum(counts.values())
    if total:
        logger.info("pbc_reminder_sweep: sent %s", counts)
    return total


async def start_sweep() -> None:
    logger.info("PBC reminder sweep started (tick=%.0fs)", _TICK_S)
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("PBC reminder sweep stopped")
            break
        except Exception as exc:
            logger.warning("pbc_reminder_sweep tick error: %s", exc)
