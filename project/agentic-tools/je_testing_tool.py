#!/usr/bin/env python3
"""
Journal Entry Testing — classic JE anomaly rules over normalized GL data.

The gap this closes: pac_endpoints.py's record_to_report Rego package
(package controls.oracle_fusion.record_to_report) already encodes real audit
JE-testing logic in prose — manual JEs over $10K need approval, preparer and
approver can't be the same person, weekend postings need an authorization
code — but nothing in this codebase ever constructs the input.journal.*
payload those rules match on, so they've never fired against anything real
(see pac_negative_tests.py's module docstring, which says as much). This
module makes that vocabulary real: oracle_fusion_tool.get_journal_entries /
netsuite_tool.get_journal_entries / sap_hana_tool.get_journal_entries /
dynamics365_tool.get_journal_entries all return the shared shape below, and
je_testing_sweep.py is the scheduled job that pulls it and runs these rules.

Deliberately pure — no DB, no HTTP, no dependency on the OPA/Rego evaluator
mcp_governance.py drives — same discipline as process_mining_tool.py (which
this module deliberately mirrors: PROCESS_TEMPLATES["record_to_report"]
there names the same four-step JE lifecycle these rules test). Native Python
rather than routing each JE through /pac/evaluate: that endpoint's
input.journal.* shape was never wired to a real producer, and there is no
value in adding OPA process-invocation latency per JE just to re-derive
comparisons this module can make directly. Rule IDs below carry an
"R2R-P001"-style cross-reference in their docstring specifically so an
auditor reading a JE-* finding can trace it back to the approved Rego
control text it operationalizes.

Journal entry shape (what every connector's get_journal_entries() returns,
one dict per `journal_entries` list item):
    je_id, amount, currency, account, gl_account_desc, description,
    preparer, approver, posted_at, period_close_date, source_system

    from je_testing_tool import run_je_tests
    findings = run_je_tests(jes)
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Optional

# Manual JE >$10K requires an approver — pac_endpoints.py:677
# (deny_journal_event, R2R-P001, "$10K threshold").
MANUAL_JE_THRESHOLD = 10_000.0

# No connector exposes a "type == top_side" / "cfo_approved" flag (real ERP
# top-side/consolidating-entry workflow state isn't part of the generic GL
# journals resource each connector reads) — an honest gap, not a fabricated
# field. A large manual entry with no approver at all is the closest signal
# available, so this threshold is a proxy for "should have had CFO-level
# sign-off," not a literal read of pac_endpoints.py's cfo_approved field.
TOP_SIDE_PROXY_THRESHOLD = 500_000.0

# A dollar amount this round, on a real transaction, is unusual enough to be
# worth a look — estimates/manual adjustments cluster here, genuine
# transaction amounts almost never do.
ROUND_DOLLAR_STEP = 1_000.0

# "Rare account" / "seldom-used description" only mean something against a
# large-enough population — on a handful of entries almost everything looks
# rare. These two gates keep the frequency rules from firing noise on small
# batches (a single connector's hourly pull can easily be this small).
_MIN_POPULATION_FOR_RARITY = 20
_RARE_ACCOUNT_MAX_COUNT = 1
_RARE_DESCRIPTION_MAX_COUNT = 1
_RARE_DESCRIPTION_MIN_AMOUNT = 5_000.0

# Velocity spike needs enough distinct posting days per preparer to establish
# a baseline mean/stdev — same "don't flag on insufficient history" gate
# process_mining_tool.py's cycle-time stats use.
_MIN_DAYS_FOR_VELOCITY_BASELINE = 3
_VELOCITY_Z_THRESHOLD = 2.0

# UTC hour window treated as "business hours" — postings outside it (or on a
# weekend) are flagged the same way pac_endpoints.py's posted_on_weekend
# check does, extended to catch after-hours weekday postings too.
_BUSINESS_HOUR_START = 6
_BUSINESS_HOUR_END = 20


def _parse_ts(value) -> Optional[datetime]:
    """posted_at arrives as a native datetime from some connectors, an ISO
    string from others (SuiteQL/HANA return strings) — accept both rather
    than forcing every caller to normalize first. Same reasoning as
    process_mining_tool.py's _parse_ts."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _finding(je: dict, rule_id: str, rule_label: str, severity: str, detail: str) -> dict:
    return {
        "je_id": je.get("je_id"),
        "rule_id": rule_id,
        "rule_label": rule_label,
        "severity": severity,
        "detail": detail,
        "source_system": je.get("source_system"),
        "account": je.get("account"),
        "gl_account_desc": je.get("gl_account_desc"),
        "amount": je.get("amount"),
        "currency": je.get("currency"),
        "description": je.get("description"),
        "preparer": je.get("preparer"),
        "approver": je.get("approver"),
        "posted_at": je.get("posted_at"),
    }


# ── Per-entry rules ───────────────────────────────────────────────────────────

def round_dollar(je: dict) -> Optional[dict]:
    amount = je.get("amount") or 0
    if amount > 0 and amount % ROUND_DOLLAR_STEP == 0:
        return _finding(
            je, "JE-ROUND-DOLLAR", "Round-dollar amount", "MEDIUM",
            f"${amount:,.0f} is an exact multiple of ${ROUND_DOLLAR_STEP:,.0f} — "
            f"characteristic of an estimate or manual adjustment rather than a transaction.",
        )
    return None


def after_hours_or_weekend(je: dict) -> Optional[dict]:
    ts = _parse_ts(je.get("posted_at"))
    if ts is None:
        return None
    if ts.weekday() >= 5:
        return _finding(
            je, "JE-WEEKEND-POSTING", "Weekend posting", "HIGH",
            f"Posted on {ts.strftime('%A')} ({ts.date().isoformat()}) — "
            f"R2R-P001 requires a weekend authorization code for this.",
        )
    if not (_BUSINESS_HOUR_START <= ts.hour < _BUSINESS_HOUR_END):
        return _finding(
            je, "JE-AFTER-HOURS", "After-hours posting", "MEDIUM",
            f"Posted at {ts.strftime('%H:%M UTC')}, outside the "
            f"{_BUSINESS_HOUR_START:02d}:00-{_BUSINESS_HOUR_END:02d}:00 UTC business window.",
        )
    return None


def preparer_equals_approver(je: dict) -> Optional[dict]:
    preparer, approver = je.get("preparer"), je.get("approver")
    if preparer and approver and preparer == approver:
        return _finding(
            je, "JE-SOD-PREPARER-APPROVER", "Preparer/approver conflict", "CRITICAL",
            f"'{preparer}' both prepared and approved this entry — "
            f"R2R-P001 segregation-of-duties violation.",
        )
    return None


def manual_je_over_threshold_unapproved(je: dict, threshold: float = MANUAL_JE_THRESHOLD) -> Optional[dict]:
    amount = je.get("amount") or 0
    if amount > threshold and not je.get("approver"):
        return _finding(
            je, "JE-THRESHOLD-UNAPPROVED", "Manual entry over threshold, unapproved", "HIGH",
            f"${amount:,.0f} exceeds the ${threshold:,.0f} threshold — "
            f"R2R-P001 requires an approver above this amount.",
        )
    return None


def top_side_unapproved(je: dict, threshold: float = TOP_SIDE_PROXY_THRESHOLD) -> Optional[dict]:
    amount = je.get("amount") or 0
    if amount > threshold and not je.get("approver"):
        return _finding(
            je, "JE-TOPSIDE-UNAPPROVED", "Large entry lacking CFO-level approval", "CRITICAL",
            f"${amount:,.0f} exceeds ${threshold:,.0f} with no approver on file — "
            f"proxy for R2R-P001's top-side/CFO-approval requirement (no connector "
            f"exposes an explicit entry-type or CFO-approval flag).",
        )
    return None


_PER_ENTRY_RULES = (
    round_dollar,
    after_hours_or_weekend,
    preparer_equals_approver,
    manual_je_over_threshold_unapproved,
    top_side_unapproved,
)


# ── Population-level rules ────────────────────────────────────────────────────

def rare_account_combination(jes: list[dict]) -> list[dict]:
    """Flags entries touching a GL account seen only a handful of times
    across the tested population — a rarely-used account combination is a
    classic JE-testing signal (either a genuinely unusual transaction or a
    posting error). Needs a large-enough population for "rare" to be
    meaningful; see _MIN_POPULATION_FOR_RARITY."""
    if len(jes) < _MIN_POPULATION_FOR_RARITY:
        return []
    counts = Counter(je.get("account") for je in jes if je.get("account"))
    findings = []
    for je in jes:
        account = je.get("account")
        if account and counts[account] <= _RARE_ACCOUNT_MAX_COUNT:
            findings.append(_finding(
                je, "JE-RARE-ACCOUNT", "Rarely-used account combination", "MEDIUM",
                f"Account '{account}' appears only {counts[account]} time(s) across "
                f"{len(jes)} entries tested — an unusual combination worth a second look.",
            ))
    return findings


def unusual_description(jes: list[dict]) -> list[dict]:
    """Flags entries whose description text (normalized) appears nowhere
    else in a large-enough, above-materiality-floor population — a seldom-
    used description is often the clearest signal a manual entry doesn't
    match the account's normal activity."""
    if len(jes) < _MIN_POPULATION_FOR_RARITY:
        return []
    normalized = {
        id(je): (je.get("description") or "").strip().lower()
        for je in jes
    }
    counts = Counter(v for v in normalized.values() if v)
    findings = []
    for je in jes:
        desc = normalized[id(je)]
        amount = je.get("amount") or 0
        if desc and counts[desc] <= _RARE_DESCRIPTION_MAX_COUNT and amount >= _RARE_DESCRIPTION_MIN_AMOUNT:
            findings.append(_finding(
                je, "JE-UNUSUAL-DESCRIPTION", "Seldom-used description", "LOW",
                f"Description '{je.get('description')}' is unique across "
                f"{len(jes)} entries tested and the amount (${amount:,.0f}) "
                f"is above the ${_RARE_DESCRIPTION_MIN_AMOUNT:,.0f} materiality floor.",
            ))
    return findings


def je_velocity_spike(jes: list[dict], z_threshold: float = _VELOCITY_Z_THRESHOLD) -> list[dict]:
    """Flags a preparer's posting day where entry count is z_threshold
    standard deviations above that preparer's own baseline — same
    "recent_daily_rate vs. baseline_daily_mean" signal pac_endpoints.py's
    P-FIN-001 Rego rule already names, computed here directly against real
    per-preparer posting history rather than a payload nothing constructs."""
    by_preparer: dict[str, dict[Any, int]] = defaultdict(lambda: defaultdict(int))
    je_by_preparer_day: dict[tuple, list[dict]] = defaultdict(list)
    for je in jes:
        preparer = je.get("preparer")
        ts = _parse_ts(je.get("posted_at"))
        if not preparer or ts is None:
            continue
        day = ts.date()
        by_preparer[preparer][day] += 1
        je_by_preparer_day[(preparer, day)].append(je)

    findings = []
    for preparer, day_counts in by_preparer.items():
        if len(day_counts) < _MIN_DAYS_FOR_VELOCITY_BASELINE:
            continue
        counts = list(day_counts.values())
        mean = statistics.mean(counts)
        stdev = statistics.pstdev(counts)
        if stdev == 0:
            continue
        for day, count in day_counts.items():
            z = (count - mean) / stdev
            if z > z_threshold:
                sample = je_by_preparer_day[(preparer, day)][0]
                findings.append(_finding(
                    sample, "JE-VELOCITY-SPIKE", "Journal entry velocity spike", "HIGH",
                    f"'{preparer}' posted {count} entries on {day.isoformat()}, "
                    f"{z:.1f}σ above their {mean:.1f}/day baseline "
                    f"(stdev {stdev:.2f} across {len(counts)} days).",
                ))
    return findings


_POPULATION_RULES = (rare_account_combination, unusual_description, je_velocity_spike)


# ── Entry point ────────────────────────────────────────────────────────────────

def run_je_tests(jes: list[dict]) -> list[dict]:
    """Runs every per-entry and population-level rule over `jes` (the
    normalized shape every connector's get_journal_entries() returns) and
    returns one finding dict per rule violation. A single JE can appear in
    multiple findings (e.g. round-dollar AND weekend AND SoD all at once) —
    callers persisting these should key on (je_id, rule_id), not je_id alone."""
    findings: list[dict] = []
    for je in jes:
        for rule in _PER_ENTRY_RULES:
            result = rule(je)
            if result:
                findings.append(result)
    for rule in _POPULATION_RULES:
        findings.extend(rule(jes))
    return findings
