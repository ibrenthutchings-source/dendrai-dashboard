#!/usr/bin/env python3
"""
Software currency check for products OSV.dev cannot enrich.

osv_client.py answers "does this package/version have a known CVE" for
anything with a real OSV ecosystem (PyPI, npm, Go, Debian:12, ...). It has
no ecosystem for a bare database-engine version pulled straight off a DSN —
"PostgreSQL 15.4" is not a package in any package manager OSV indexes, and
there is no principled way to guess a customer's host distro (which IS what
Debian/Alpine CVEs are actually keyed on) from a connection string alone.
Faking an ecosystem match here would produce false "no known CVEs" claims
that look like real enrichment but aren't.

This module answers a narrower, honest question instead: is the installed
version still a maintained/current one, per a small hand-maintained table —
a CURRENCY check, not a CVE check, and it is never represented as the
latter. Returns (None, None) for anything not in BASELINES rather than
guessing, the same "an honest gap, not a guess" discipline
pol_domain_mappings.py documents for its own curated mapping.

BASELINES needs periodic manual review (like framework_mappings.py and
pol_domain_mappings.py) — it is deliberately NOT auto-fetched or
LLM-generated, so a wrong entry is a reviewable typo, not a silent model
hallucination baked into a compliance signal.
"""

from __future__ import annotations

import re
from typing import Optional

# product -> {major_version: latest_known_patch_version}. Keyed on the
# LEADING numeric component(s) an admin would recognize as "the major
# version" for that product, since that's what determines whether a version
# is "current for its line" vs. simply "old" — Postgres 15.8 is current,
# Postgres 12.x is not, regardless of patch level.
BASELINES: dict[str, dict[str, str]] = {
    "postgresql": {"12": "12.20", "13": "13.16", "14": "14.13", "15": "15.8", "16": "16.4", "17": "17.0"},
    "openssl": {"1.1": "1.1.1w", "3.0": "3.0.15", "3.1": "3.1.7", "3.2": "3.2.3", "3.3": "3.3.2"},
}

_LEADING_VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?")


def _major_key(product: str, version: str) -> Optional[str]:
    """Extracts the major-version key BASELINES is keyed on. Postgres uses a
    single leading integer (12, 13, ... 17 — the major-version-only scheme
    adopted from Postgres 10 onward); openssl uses major.minor (1.1, 3.0,
    3.1, ...). Anything that doesn't parse returns None rather than guessing."""
    m = _LEADING_VERSION_RE.match((version or "").strip())
    if not m:
        return None
    major, minor = m.group(1), m.group(2)
    if product == "postgresql":
        return major
    if minor is not None:
        return f"{major}.{minor}"
    return major


def _version_tuple(v: str) -> tuple:
    parts = re.findall(r"\d+", v or "")
    return tuple(int(p) for p in parts)


def check_currency(product: str, version: str) -> tuple[Optional[bool], Optional[str]]:
    """Returns (is_current, latest_known_version). is_current is None (not
    False) when the product/version isn't in BASELINES — an unrecognized
    product must never render as "out of date" just because this table
    hasn't been taught about it yet. is_current is True when the installed
    version is at or above the latest known patch for its major line, or
    when its major line isn't in BASELINES at all (nothing to compare
    against, so no basis to call it stale) but IS a plausible version string —
    that distinction matters less than the None case, which is "no product
    match at all"."""
    baselines = BASELINES.get((product or "").strip().lower())
    if not baselines:
        return None, None
    key = _major_key(product.strip().lower(), version)
    if key is None or key not in baselines:
        return None, None
    latest = baselines[key]
    installed_t, latest_t = _version_tuple(version), _version_tuple(latest)
    return installed_t >= latest_t, latest
