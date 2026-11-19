#!/usr/bin/env python3
"""
Policy-as-Code approval/evaluation drift detector.

The gap this exists to surface: mcp_governance._evaluate_pac_policy (and
pac_negative_sweep.py's own _rego_for_process, which deliberately mirrors it)
reads whatever db.get_latest_pac_module(process) returns — the most
recently SAVED module row — as what every real production event is
adjudicated against. pac_policy_modules has no status/approved column at
all; approval is tracked only by a row existing in pac_policy_approvals,
and get_latest_pac_module never joins against it. That means saving a new
draft via PUT /pac/modules/{process} makes it live in production the
instant it's saved — before any approval. POST /modules/{process}/approve's
negative-testing gate runs strictly AFTER a save, and is advisory even
then (see its own docstring): it can warn, but the draft was already live
regardless of what it finds.

This module does not change that behavior — making evaluation gated on
approval is a real product/behavior decision, not something to slip in
silently as part of a monitoring feature. It makes the gap visible instead:
for each process, compare the content hash of what's actually live
(get_latest_pac_module) against the content hash of the most recently
APPROVED version (get_latest_approved_pac_module). A mismatch is real,
actionable drift — an unapproved or since-edited module is currently
adjudicating real events for that process.
"""

from __future__ import annotations

import hashlib

import db
import pac_endpoints


def _content_hash(rego_content: str) -> str:
    return hashlib.sha256((rego_content or "").encode("utf-8")).hexdigest()


def check_process_drift(process: str) -> dict:
    """
    Returns {"process", "live_module_id", "live_version", "live_hash",
    "approved_module_id", "approved_version", "approved_hash", "drifted",
    "reason"}.

    drifted=True means the module currently being evaluated in production
    for this process is NOT the most recently approved version — either it
    has never been approved at all, or it was approved and then edited
    again without re-approval.
    """
    live = db.get_latest_pac_module(process) if db.is_available() else None
    if live is None:
        # No saved module at all for this process — the built-in default
        # Rego is what's live, and defaults ship as reviewed code, not a
        # draft, so there's nothing to drift from.
        return {
            "process": process, "live_module_id": None, "live_version": None,
            "live_hash": _content_hash(pac_endpoints._REGO_DEFAULTS.get(process, "")),
            "approved_module_id": None, "approved_version": None, "approved_hash": None,
            "drifted": False,
            "reason": "no saved module — evaluating the built-in default Rego",
        }

    live_hash = _content_hash(live["rego_content"])
    approved = db.get_latest_approved_pac_module(process)

    if approved is None:
        return {
            "process": process, "live_module_id": live["id"], "live_version": live["version"],
            "live_hash": live_hash,
            "approved_module_id": None, "approved_version": None, "approved_hash": None,
            "drifted": True,
            "reason": "the live module has never received any approval sign-off",
        }

    approved_hash = _content_hash(approved["rego_content"])
    drifted = approved_hash != live_hash
    return {
        "process": process, "live_module_id": live["id"], "live_version": live["version"],
        "live_hash": live_hash,
        "approved_module_id": approved["id"], "approved_version": approved["version"],
        "approved_hash": approved_hash,
        "drifted": drifted,
        "reason": (
            "the live module was edited/replaced after its last approval — "
            "the new content has never itself been approved"
        ) if drifted else "the live module matches its most recently approved version",
    }


def check_all_processes() -> dict:
    """Runs check_process_drift for every known PaC process. Returns
    {"processes": {process: result, ...}, "any_drifted": bool}."""
    processes = set(pac_endpoints._REGO_DEFAULTS.keys())
    if db.is_available():
        processes |= pac_endpoints._valid_processes()
    results = {p: check_process_drift(p) for p in sorted(processes)}
    return {"processes": results, "any_drifted": any(r["drifted"] for r in results.values())}
