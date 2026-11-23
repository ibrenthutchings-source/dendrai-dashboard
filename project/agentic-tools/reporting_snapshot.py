"""
Signed, immutable report snapshots (pure module: no DB, no HTTP).

A governed query's manifest (reporting_catalog.build_manifest) proves *how* a
result was produced, but the source data keeps changing, so on its own it cannot
let someone reproduce what was shown at the time. A snapshot stores the result
rows themselves with the manifest, and signs the lot:

  payload_json  canonical JSON of the manifest + who/why (this is what is signed)
  rows_json     canonical JSON of the result rows (SHA-256 is inside the payload)
  signature     HMAC-SHA256 of payload_json with the tenant's signing key

Both are stored as exact TEXT, never re-serialised, so verification does not
depend on JSON round-tripping (jsonb would reorder keys and reformat numbers).

verify() answers two separate questions, reported separately so a failure says
what was altered:
  rows_match_hash   the stored rows still hash to the SHA-256 in the payload
  signature_valid   the payload is exactly what the platform signed
Immutability is enforced in the database (a trigger rejects UPDATE/DELETE); the
signature is the second line of defence, and the evidence for a reviewer who
cannot trust the database administrator.

A reviewer reperforming the work needs: the payload (dataset, version, spec, SQL,
parameters, as-of time), the rows, and the hash. rerun_matches() compares a fresh
run to the snapshot to show whether the underlying data has moved since.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Callable, Optional

SNAPSHOT_VERSION = 1
MAX_NOTE = 2000


class SnapshotError(ValueError):
    """Invalid snapshot request. Safe to show to the caller."""


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def build_snapshot(manifest: dict, rows_json: str, created_by: str, note: Optional[str] = None,
                   engagement: Optional[str] = None,
                   sign: Callable[[str], str] = None) -> dict:
    """Assemble the storable record. `rows_json` must be the canonical rows text the
    manifest's result_sha256 was computed over; `sign` maps payload text -> hex signature."""
    if sign is None:
        raise SnapshotError("a signing function is required")
    note = (note or "").strip() or None
    if note and len(note) > MAX_NOTE:
        raise SnapshotError(f"note is limited to {MAX_NOTE} characters")
    engagement = (engagement or "").strip()[:128] or None
    if hashlib.sha256(rows_json.encode("utf-8")).hexdigest() != manifest["result_sha256"]:
        raise SnapshotError("rows do not match the manifest hash")
    payload = {
        "snapshot_version": SNAPSHOT_VERSION,
        "manifest": manifest,
        "created_by": created_by,
        "note": note,
        "engagement": engagement,
    }
    payload_json = _canon(payload)
    return {
        "dataset_key": manifest["dataset"]["key"],
        "engagement": engagement,
        "note": note,
        "payload_json": payload_json,
        "rows_json": rows_json,
        "result_sha256": manifest["result_sha256"],
        "row_count": manifest["row_count"],
        "signature": sign(payload_json),
        "created_by": created_by,
    }


def verify(snapshot: dict, sign: Callable[[str], str]) -> dict:
    """Check a stored snapshot. Never raises on tampering; reports it."""
    payload_json = snapshot["payload_json"]
    rows_hash = hashlib.sha256(snapshot["rows_json"].encode("utf-8")).hexdigest()
    try:
        payload = json.loads(payload_json)
        declared = payload["manifest"]["result_sha256"]
    except (ValueError, KeyError, TypeError):
        return {"rows_match_hash": False, "signature_valid": False, "intact": False,
                "detail": "payload is unreadable"}
    rows_ok = hmac.compare_digest(rows_hash, declared) and hmac.compare_digest(rows_hash, snapshot["result_sha256"])
    sig_ok = hmac.compare_digest(sign(payload_json), snapshot["signature"])
    detail = "ok"
    if not sig_ok and not rows_ok:
        detail = "payload and rows have both been altered, or the signing key has changed"
    elif not sig_ok:
        detail = "payload was altered after signing, or the signing key has changed"
    elif not rows_ok:
        detail = "result rows were altered after signing"
    return {"rows_match_hash": rows_ok, "signature_valid": sig_ok, "intact": rows_ok and sig_ok,
            "detail": detail}


def rerun_matches(snapshot_payload: dict, fresh_sql: str, fresh_rows_hash: str) -> dict:
    """Compare a fresh run of the snapshot's spec with the snapshot.
    sql_changed: the dataset definition produces different SQL now (a new dataset version).
    data_changed: same SQL, different result: the source data moved after as_of."""
    m = snapshot_payload["manifest"]
    sql_changed = fresh_sql != m["sql"]
    data_changed = fresh_rows_hash != m["result_sha256"]
    if sql_changed:
        explanation = "the dataset definition has changed since the snapshot; a rerun is not like for like"
    elif data_changed:
        explanation = "the underlying data has changed since the snapshot's as-of time"
    else:
        explanation = "a fresh run reproduces the snapshot exactly"
    return {"sql_changed": sql_changed, "data_changed": data_changed,
            "reproduced": not sql_changed and not data_changed,
            "snapshot_as_of": m["as_of"], "explanation": explanation}
