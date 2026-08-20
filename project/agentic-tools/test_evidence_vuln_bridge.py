#!/usr/bin/env python3
"""
Unit tests for evidence_endpoints._bridge_to_vuln_register — the SARIF ->
infra_vulnerabilities projection (Infrastructure Vulnerability & Currency
Posture, Phase 2). Pure async function, db.upsert_infra_vulnerability
monkeypatched at the boundary; no DB, no HTTP.

    pytest test_evidence_vuln_bridge.py -v
"""

from __future__ import annotations

import asyncio

import db
import evidence_endpoints as ee


def _record(**overrides) -> dict:
    base = {
        "repository": "org/repo", "commit_sha": "abc123", "pipeline_run_id": "run-1",
        "source": "other", "rule_id": "container-scan/CVE-2021-1234", "severity": "HIGH",
        "cwe": None, "cve": None, "file_path": None, "line_number": None, "line_snippet": None,
        "fingerprint": "deadbeef" * 8, "author": None, "approver": None, "scan_status": "FAIL",
    }
    base.update(overrides)
    return base


def test_bridge_skips_findings_without_a_cve(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "upsert_infra_vulnerability", lambda *a, **kw: calls.append(a))
    asyncio.run(ee._bridge_to_vuln_register(_record(cve=None), 101))
    assert calls == []


def test_bridge_upserts_when_cve_present(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "upsert_infra_vulnerability", lambda *a, **kw: calls.append(a))
    record = _record(cve="CVE-2021-1234", severity="CRITICAL")

    asyncio.run(ee._bridge_to_vuln_register(record, 101))

    assert len(calls) == 1
    args = calls[0]
    assert args[0] == "CVE-2021-1234"     # vuln_id
    assert args[1] is None                 # asset_id — SARIF findings aren't tied to a tracked asset
    assert args[3] == "scanner"            # source
    assert args[4] == record["fingerprint"]  # source_ref — dedup key for the asset_id-less path
    assert args[5] == "CRITICAL"           # severity


def test_bridge_never_raises_on_db_failure(monkeypatch):
    def _raise(*a, **kw):
        raise RuntimeError("simulated DB hiccup")
    monkeypatch.setattr(db, "upsert_infra_vulnerability", _raise)
    # must not raise — evidence ingestion already succeeded by the time this runs
    asyncio.run(ee._bridge_to_vuln_register(_record(cve="CVE-2021-1234"), 101))
