#!/usr/bin/env python3
"""
Signed, immutable report snapshots. No database: the store is a dict, and the
signing function is a real HMAC with a test key.

    pytest test_report_snapshots.py -v
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import HTTPException

import reporting_catalog as rc
import reporting_endpoints as re_
import reporting_snapshot as rs

KEY = b"test-key"


def sign(text: str) -> str:
    return hmac.new(KEY, text.encode(), hashlib.sha256).hexdigest()


def make(rows=None, note=None, engagement=None, spec=None):
    rows = rows if rows is not None else [{"control_ref": "AC-01"}, {"control_ref": "AC-02"}]
    cq = rc.compile_query(spec or {"dataset": "controls", "columns": ["control_ref"], "limit": 10})
    rows_json = rc.canonical_rows_json(rows)
    manifest = rc.build_manifest(cq, rows)
    return rs.build_snapshot(manifest, rows_json, "alice", note, engagement, sign=sign), manifest


# ── pure module ─────────────────────────────────────────────────────────────

def test_fresh_snapshot_verifies():
    snap, manifest = make()
    assert rs.verify(snap, sign) == {"rows_match_hash": True, "signature_valid": True, "intact": True, "detail": "ok"}
    assert snap["result_sha256"] == manifest["result_sha256"] and snap["row_count"] == 2


def test_altered_rows_are_detected():
    snap, _ = make()
    snap["rows_json"] = snap["rows_json"].replace("AC-02", "AC-99")
    r = rs.verify(snap, sign)
    assert r["intact"] is False and r["rows_match_hash"] is False and r["signature_valid"] is True
    assert "rows were altered" in r["detail"]


def test_altered_payload_is_detected():
    snap, _ = make()
    snap["payload_json"] = snap["payload_json"].replace('"created_by":"alice"', '"created_by":"bob"')
    r = rs.verify(snap, sign)
    assert r["signature_valid"] is False and r["rows_match_hash"] is True and r["intact"] is False


def test_rows_and_hash_replaced_together_still_fail_the_signature():
    """An attacker who rewrites the rows and the stored hash but cannot sign."""
    snap, _ = make()
    snap["rows_json"] = rc.canonical_rows_json([{"control_ref": "EVIL"}])
    snap["result_sha256"] = hashlib.sha256(snap["rows_json"].encode()).hexdigest()
    r = rs.verify(snap, sign)
    assert r["intact"] is False and r["rows_match_hash"] is False   # payload still declares the old hash


def test_wrong_key_fails_signature():
    snap, _ = make()
    other = lambda t: hmac.new(b"other", t.encode(), hashlib.sha256).hexdigest()  # noqa: E731
    assert rs.verify(snap, other)["signature_valid"] is False


def test_unreadable_payload_reports_not_intact_without_raising():
    snap, _ = make()
    snap["payload_json"] = "{not json"
    assert rs.verify(snap, sign)["intact"] is False


def test_rows_must_match_manifest_hash_at_build_time():
    cq = rc.compile_query({"dataset": "controls", "columns": ["control_ref"]})
    manifest = rc.build_manifest(cq, [{"control_ref": "A"}])
    with pytest.raises(rs.SnapshotError):
        rs.build_snapshot(manifest, rc.canonical_rows_json([{"control_ref": "B"}]), "alice", sign=sign)


def test_requires_signer_and_limits_note():
    cq = rc.compile_query({"dataset": "controls", "columns": ["control_ref"]})
    manifest = rc.build_manifest(cq, [])
    with pytest.raises(rs.SnapshotError):
        rs.build_snapshot(manifest, "[]", "alice")
    with pytest.raises(rs.SnapshotError):
        rs.build_snapshot(manifest, "[]", "alice", note="x" * 2001, sign=sign)


def test_payload_records_who_why_and_how():
    snap, manifest = make(note="  Q3 SOX walkthrough ", engagement="FY26-SOX")
    p = json.loads(snap["payload_json"])
    assert p["created_by"] == "alice" and p["note"] == "Q3 SOX walkthrough" and p["engagement"] == "FY26-SOX"
    assert p["manifest"]["sql"] == manifest["sql"] and p["manifest"]["as_of"] == manifest["as_of"]
    assert p["snapshot_version"] == 1


def test_numbers_and_dates_verify_after_text_storage():
    from datetime import datetime, timezone
    from decimal import Decimal
    rows = [{"n": Decimal("1234567.10"), "t": datetime(2026, 1, 1, tzinfo=timezone.utc), "z": None}]
    snap, _ = make(rows=rows)
    stored = dict(snap)               # text columns are stored and returned verbatim
    assert rs.verify(stored, sign)["intact"] is True


def test_rerun_comparison_distinguishes_unchanged_data_changed_data_and_changed_definition():
    snap, manifest = make()
    payload = json.loads(snap["payload_json"])
    same = rs.rerun_matches(payload, manifest["sql"], manifest["result_sha256"])
    assert same["reproduced"] is True
    moved = rs.rerun_matches(payload, manifest["sql"], "f" * 64)
    assert moved["data_changed"] is True and moved["sql_changed"] is False and not moved["reproduced"]
    redefined = rs.rerun_matches(payload, "SELECT 1", manifest["result_sha256"])
    assert redefined["sql_changed"] is True and "definition" in redefined["explanation"]


# ── API ─────────────────────────────────────────────────────────────────────

USER = {"username": "alice", "role": "user"}
ADMIN = {"username": "root", "role": "admin"}


@pytest.fixture
def api(monkeypatch):
    store, audits = {}, []

    def insert(rec):
        sid = len(store) + 1
        store[sid] = {**rec, "id": sid, "created_at": "2026-11-23T00:00:00+00:00"}
        return {k: store[sid][k] for k in ("id", "dataset_key", "engagement", "note", "result_sha256",
                                           "row_count", "signature", "created_by", "created_at")}

    monkeypatch.setattr(re_.db, "insert_report_snapshot", insert)
    monkeypatch.setattr(re_.db, "get_report_snapshot", lambda i: dict(store[i]) if i in store else None)
    monkeypatch.setattr(re_.db, "list_report_snapshots",
                        lambda dataset_key=None, engagement=None, limit=100:
                        [{k: v for k, v in s.items() if k not in ("payload_json", "rows_json")}
                         for s in store.values() if not dataset_key or s["dataset_key"] == dataset_key])
    monkeypatch.setattr(re_.db, "sign_evidence_record", sign)
    monkeypatch.setattr(re_.db, "insert_audit_log_entry", lambda *a, **k: audits.append({"args": a, **k}) or 1)
    monkeypatch.setattr(re_.db, "is_available", lambda: True)
    monkeypatch.setattr(re_, "execute", lambda cq: [{"control_ref": "AC-01"}])
    return type("Api", (), {"store": store, "audits": audits})


def create(user=USER, spec=None, **kw):
    return re_.create_snapshot(re_.SnapshotRequest(spec=spec or {"dataset": "controls", "columns": ["control_ref"]},
                                                   **kw), user)


def test_create_stores_signed_snapshot_and_audits(api):
    out = create(note="walkthrough", engagement="FY26")
    assert out["id"] == 1 and out["rows"] == [{"control_ref": "AC-01"}]
    assert rs.verify(api.store[1], sign)["intact"] is True
    a = api.audits[-1]
    assert a["args"][:2] == ("reporting", "report_snapshot_created")
    assert a["outcome"] == "success" and a["target"] == "1"
    assert a["detail"]["result_sha256"] == out["result_sha256"]


def test_get_returns_payload_and_rows_not_raw_text(api):
    create()
    got = re_.get_snapshot(1, USER)
    assert got["rows"] == [{"control_ref": "AC-01"}] and got["payload"]["manifest"]["dataset"]["key"] == "controls"
    assert "payload_json" not in got and "rows_json" not in got


def test_verify_endpoint_reports_tampering_and_audits_it_as_failure(api):
    create()
    assert re_.verify_snapshot(1, USER)["intact"] is True and api.audits[-1]["outcome"] == "success"
    api.store[1]["rows_json"] = '[{"control_ref":"FORGED"}]'
    r = re_.verify_snapshot(1, USER)
    assert r["intact"] is False and r["rows_match_hash"] is False
    assert api.audits[-1]["outcome"] == "failure" and api.audits[-1]["detail"]["rows_match_hash"] is False


def test_restricted_dataset_snapshots_are_admin_only(api):
    spec = {"dataset": "audit_log", "columns": ["action"]}
    with pytest.raises(HTTPException) as ei:
        create(USER, spec)
    assert ei.value.status_code == 422 and api.audits[-1]["outcome"] == "denied"
    create(ADMIN, spec)
    with pytest.raises(HTTPException) as ei:
        re_.get_snapshot(1, USER)
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException):
        re_.verify_snapshot(1, USER)
    assert re_.list_snapshots(None, None, USER)["snapshots"] == []
    assert len(re_.list_snapshots(None, None, ADMIN)["snapshots"]) == 1
    assert re_.get_snapshot(1, ADMIN)["id"] == 1


def test_unknown_snapshot_is_404(api):
    with pytest.raises(HTTPException) as ei:
        re_.get_snapshot(99, USER)
    assert ei.value.status_code == 404


def test_missing_signing_key_is_503_and_nothing_is_stored(api, monkeypatch):
    def nokey(text):
        raise re_.db.EvidenceSigningKeyMissing("EVIDENCE_SIGNING_KEY is not set")
    monkeypatch.setattr(re_.db, "sign_evidence_record", nokey)
    with pytest.raises(HTTPException) as ei:
        create()
    assert ei.value.status_code == 503 and not api.store
    assert api.audits[-1]["outcome"] == "failure" and api.audits[-1]["detail"]["reason"] == "signing_key_missing"


def test_execution_error_stores_nothing(api, monkeypatch):
    def boom(cq):
        raise RuntimeError("secret")
    monkeypatch.setattr(re_, "execute", boom)
    with pytest.raises(HTTPException) as ei:
        create()
    assert ei.value.status_code == 500 and "secret" not in ei.value.detail and not api.store


def test_rerun_reproduces_then_detects_moved_data(api, monkeypatch):
    create()
    assert re_.rerun_snapshot(1, USER)["reproduced"] is True
    monkeypatch.setattr(re_, "execute", lambda cq: [{"control_ref": "AC-01"}, {"control_ref": "AC-03"}])
    r = re_.rerun_snapshot(1, USER)
    assert r["data_changed"] is True and r["reproduced"] is False
