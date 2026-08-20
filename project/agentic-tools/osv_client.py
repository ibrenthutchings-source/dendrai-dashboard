#!/usr/bin/env python3
"""
OSV.dev client — Infrastructure Vulnerability & Currency Posture, Phase 2.

Free, unauthenticated, no API key: https://osv.dev/docs/#tag/api. Two calls
per unique vulnerability, not one, because OSV's batch endpoint deliberately
returns minimal records (id + modified only) to keep batch responses small —
full details (summary, severity, affected ranges) require a follow-up
GET /v1/vulns/{id} per distinct id. query_batch() does both steps and
de-duplicates the hydration calls so a CVE shared by many packages (common
for transitive dependencies) is only fetched once per invocation.

Caching lives one layer up, in db.osv_cache (keyed on exact
(ecosystem, package_name, version)) — see get_osv_cache_entry/
put_osv_cache_entry. This module itself does not cache; vulnerability_sweep.py
checks the DB cache before calling query_batch() at all, so a fully-cached
sweep tick makes zero OSV requests.

Fails soft everywhere: a network error, timeout, or malformed response
returns an empty result for the affected package(s) rather than raising —
callers (vulnerability_sweep.py) must treat "OSV had nothing to say" and
"OSV was unreachable" as the SAME non-event (leave last_assessed_at alone),
never as "package is clean". Silently returning [] for an unreachable OSV
would be a false negative; this module keeps that call by never returning a
result for a package it couldn't actually reach.
"""

from __future__ import annotations

import logging
from typing import Optional

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

logger = logging.getLogger(__name__)

_QUERYBATCH_URL = "https://api.osv.dev/v1/querybatch"
_VULN_URL = "https://api.osv.dev/v1/vulns/{id}"
_TIMEOUT_S = 15
_BATCH_CHUNK = 100  # OSV's documented batch limit is 1000; kept well under it


def _require_requests():
    if not _HAS_REQUESTS:
        raise ImportError("requests library required: pip install requests")


def _chunk(items: list, size: int) -> list:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _fetch_vuln_details(vuln_id: str) -> Optional[dict]:
    try:
        resp = requests.get(_VULN_URL.format(id=vuln_id), timeout=_TIMEOUT_S)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("osv_client: failed to fetch details for %s: %s", vuln_id, exc)
        return None


def query_batch(packages: list[tuple[str, str, str]]) -> dict[tuple[str, str, str], list[dict]]:
    """packages: list of (ecosystem, package_name, version) triples — the
    ones NOT already served by db.osv_cache. Returns {triple: [full OSV vuln
    dict, ...]} — a triple present in the dict (even with an empty list)
    means OSV was successfully queried for it; a triple ABSENT from the dict
    means the query failed and the caller must not treat that as "no
    vulnerabilities" (see module docstring)."""
    _require_requests()
    if not packages:
        return {}

    result: dict[tuple[str, str, str], list[dict]] = {}
    details_cache: dict[str, dict] = {}

    for chunk in _chunk(packages, _BATCH_CHUNK):
        try:
            resp = requests.post(
                _QUERYBATCH_URL,
                json={"queries": [
                    {"package": {"name": name, "ecosystem": ecosystem}, "version": version}
                    for ecosystem, name, version in chunk
                ]},
                timeout=_TIMEOUT_S,
            )
            resp.raise_for_status()
            batch_results = resp.json().get("results", [])
        except Exception as exc:
            logger.warning("osv_client: querybatch failed for a chunk of %d package(s): %s", len(chunk), exc)
            continue  # this chunk's triples stay absent from result — an honest "couldn't check", not "clean"

        for (ecosystem, name, version), minimal in zip(chunk, batch_results):
            ids = [v["id"] for v in (minimal.get("vulns") or []) if v.get("id")]
            full_records = []
            for vuln_id in ids:
                if vuln_id not in details_cache:
                    details = _fetch_vuln_details(vuln_id)
                    if details is None:
                        continue  # this one CVE's hydration failed — skip it, don't drop the whole package result
                    details_cache[vuln_id] = details
                full_records.append(details_cache[vuln_id])
            result[(ecosystem, name, version)] = full_records

    return result


_SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


def normalize_severity(osv_vuln: dict) -> tuple[str, Optional[float]]:
    """Extracts (severity_tier, cvss_score) from an OSV vuln record's
    severity[] array (CVSS_V3/CVSS_V2 vector strings) or, failing that,
    database_specific.severity (a plain string some sources set, e.g.
    GHSA). Defaults to MEDIUM, never CRITICAL/HIGH, when OSV reports a CVE
    exists but gives no severity signal at all — an unscored CVE is a real
    gap worth surfacing, but guessing it's the worst tier would be its own
    kind of false claim."""
    for entry in osv_vuln.get("severity") or []:
        score_str = entry.get("score", "")
        cvss = _parse_cvss_score(score_str)
        if cvss is not None:
            return _tier_for_cvss(cvss), cvss

    db_specific = (osv_vuln.get("database_specific") or {}).get("severity")
    if isinstance(db_specific, str) and db_specific.upper() in _SEVERITY_ORDER:
        return db_specific.upper(), None

    return "MEDIUM", None


def _parse_cvss_score(vector_or_score: str) -> Optional[float]:
    """CVSS entries are either a bare numeric string (some CVSS_V2 rows) or a
    full vector string ("CVSS:3.1/AV:N/.../S:U/C:H/...") with no standalone
    score component — computing the real CVSS formula from a vector is out
    of scope here, so a vector string returns None (unknown) rather than a
    fabricated number. Only a plain numeric score is parsed."""
    try:
        return float(vector_or_score)
    except (TypeError, ValueError):
        return None


def _tier_for_cvss(score: float) -> str:
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    return "LOW"


def extract_fixed_version(osv_vuln: dict, ecosystem: str) -> Optional[str]:
    """Best-effort fixed version from affected[].ranges[].events[] where
    ecosystem matches — OSV's affected ranges can list multiple ecosystems
    per vuln (rare) and multiple ranges per ecosystem; returns the first
    'fixed' event found, or None if the vuln has no known fix yet (that is
    itself a valid, meaningful answer — not every CVE has a patch)."""
    for affected in osv_vuln.get("affected") or []:
        pkg_ecosystem = (affected.get("package") or {}).get("ecosystem")
        if pkg_ecosystem != ecosystem:
            continue
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                if event.get("fixed"):
                    return event["fixed"]
    return None


def is_configured() -> bool:
    return _HAS_REQUESTS
