#!/usr/bin/env python3
"""
DORA-style change-management metrics — real operational evidence for SOC 2
CC8.1 (change management), not just a policy-mapping claim.

Two of DORA's four keys, computed from data this platform already ingests:
  - Deployment Frequency: observability.pipeline_attestations row count per
    day — each attestation is one CI pipeline run reporting its provenance
    (evidence_endpoints.py's POST /evidence/attestation), a real proxy for
    "how often are we shipping," not a self-reported number.
  - Change Failure Rate: observability.itsm_tickets opened per pipeline
    attestation in the same window — the fraction of pipeline runs serious
    enough to escalate into a real Jira/ServiceNow incident.
  - Mean Time to Restore (MTTR): mean hours from itsm_tickets.created_at to
    its resolved_at (set only on the actual resolved/closed transition —
    see db.update_itsm_ticket_status — not the generic updated_at, which
    bumps on any field change and would overstate/understate restore time).

DORA's fourth metric, Lead Time for Changes (commit -> deploy elapsed
time), is deliberately NOT computed here: this schema captures pipeline
attestation ingestion time, not a commit-authored-at timestamp anywhere, so
computing it would mean fabricating a number from data that doesn't exist.
Three honest metrics beat four with one invented — the same principle
behind every "never fabricate a finding" decision elsewhere in this
platform (Cosign verification, Railway image-digest-mismatch, the
Compliance Scorecard's mapped-vs-verified split).

All three reported metrics use None (not 0) when their denominator is
empty — see db._aggregate_dora_metrics's docstring for exactly which case
maps to which None.
"""

from __future__ import annotations

import db


def compute_dora_metrics(window_days: int = 30) -> dict:
    return db.compute_dora_metrics(window_days=window_days)
