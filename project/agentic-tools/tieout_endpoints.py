"""
Tick-and-tie API (see tieout.py).

    POST /reporting/tieouts              tie two sources, store a signed result
    GET  /reporting/tieouts              list (metadata only)
    GET  /reporting/tieouts/legend       the tickmark legend
    GET  /reporting/tieouts/{id}         full record: spec, sources, items, signature
    GET  /reporting/tieouts/{id}/verify  signature, item hash, marks recomputed from the sources

A source is either a stored signed snapshot ({"type":"snapshot","id":N}; its
signature is checked first and a snapshot that does not verify is refused) or rows
supplied directly ({"type":"inline","label":"...","rows":[...]}), which are kept
inside the signed record because nothing else could reproduce them.

Same access rule as the rest of reporting: the "reporting" screen permission, and
a tie-out built on a restricted dataset (e.g. the audit log) is admin-only.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
import reporting_endpoints as re_
import reporting_snapshot as rs
import tieout as to
from auth_endpoints import get_current_user, require_screen_permission

logger = logging.getLogger("ubo.tieout")

router = APIRouter(prefix="/reporting/tieouts", tags=["Reporting"],
                   dependencies=[Depends(require_screen_permission("reporting"))])


class TieoutRequest(BaseModel):
    spec: Dict[str, Any]
    left_source: Dict[str, Any]
    right_source: Dict[str, Any]
    conclusion: str
    engagement: Optional[str] = None
    note: Optional[str] = None


def _audit(user: dict, action: str, target: str, outcome: str, detail: dict) -> None:
    try:
        db.insert_audit_log_entry("reporting", action, actor=user.get("username"), target=target,
                                  detail=detail, outcome=outcome)
    except Exception as exc:
        logger.warning("tieout audit write failed: %s", exc)


def _fail(user: dict, status: int, reason: str, message: str, outcome: str = "failure"):
    _audit(user, "tieout_created", "", outcome, {"reason": reason, "message": message[:200]})
    raise HTTPException(status_code=status, detail=message)


def _resolve(role: str, src: Any, user: dict):
    """-> (rows, source description, inline rows or None, restricted)"""
    if not isinstance(src, dict):
        raise to.TieoutError(f"{role}_source must be an object")
    kind = src.get("type")
    if kind == "snapshot":
        sid = src.get("id")
        if isinstance(sid, bool) or not isinstance(sid, int):
            raise to.TieoutError(f"{role}_source.id must be a snapshot id")
        snap = re_._load_snapshot(sid, user)           # 404 if absent or restricted to admins
        check = rs.verify(snap, sign=db.sign_evidence_record)   # EvidenceSigningKeyMissing propagates to create()
        if not check["intact"]:
            raise to.TieoutError(f"snapshot {sid} does not verify ({check['detail']}); it cannot be used as a source")
        payload = json.loads(snap["payload_json"])
        label = (src.get("label") or f"{payload['manifest']['dataset']['key']} snapshot #{sid}")[:200]
        desc = to.describe_source(role, "snapshot", label, snap["rows_json"], snapshot_id=sid,
                                  snapshot_result_sha256=snap["result_sha256"],
                                  dataset=payload["manifest"]["dataset"]["key"],
                                  as_of=payload["manifest"]["as_of"])
        restricted = not _public_dataset(payload["manifest"]["dataset"]["key"])
        return json.loads(snap["rows_json"]), desc, None, restricted
    if kind == "inline":
        rows = src.get("rows")
        label = str(src.get("label") or "").strip()[:200]
        if not label:
            raise to.TieoutError(f"{role}_source.label is required: say where these figures came from")
        if not isinstance(rows, list) or not rows:
            raise to.TieoutError(f"{role}_source.rows must be a non-empty list")
        if len(rows) > to.MAX_ROWS:
            raise to.TieoutError(f"the {role} source has more than {to.MAX_ROWS} rows")
        rows_json = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
        desc = to.describe_source(role, "inline", label, rows_json,
                                  entered_by=user.get("username", ""),
                                  provenance="supplied by the user; not independently sourced")
        return json.loads(rows_json), desc, json.loads(rows_json), False
    raise to.TieoutError(f"{role}_source.type must be 'snapshot' or 'inline'")


def _public_dataset(key: str) -> bool:
    import reporting_catalog as rc
    try:
        return not rc.get_dataset(key).admin_only
    except rc.QueryError:
        return False


def _visible(rec: dict, user: dict) -> bool:
    return user.get("role") == "admin" or not rec.get("restricted")


def _load(tieout_id: int, user: dict) -> dict:
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not available")
    rec = db.get_report_tieout(tieout_id)
    if not rec or not _visible(rec, user):
        raise HTTPException(status_code=404, detail="Tie-out not found")
    return rec


@router.get("/legend", summary="The tickmark legend")
def legend(user: dict = Depends(get_current_user)):
    return {"legend_version": to.LEGEND_VERSION, "legend": to.LEGEND}


@router.post("", summary="Tie two sources together and keep a signed result")
def create(req: TieoutRequest, user: dict = Depends(get_current_user)):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        spec = to.validate_spec(req.spec)
        left_rows, left_desc, left_inline, left_r = _resolve("left", req.left_source, user)
        right_rows, right_desc, right_inline, right_r = _resolve("right", req.right_source, user)
        result = to.compute(spec, left_rows, right_rows)
        inline = {}
        if left_inline is not None:
            inline["left"] = left_inline
        if right_inline is not None:
            inline["right"] = right_inline
        record = to.build_record(spec, [left_desc, right_desc], result, req.conclusion,
                                 user.get("username", ""), datetime.now(timezone.utc).isoformat(),
                                 req.engagement, req.note, inline, sign=db.sign_evidence_record)
    except to.TieoutError as exc:
        _fail(user, 422, "invalid_tieout", str(exc))
    except db.EvidenceSigningKeyMissing as exc:
        _fail(user, 503, "signing_key_missing", str(exc))
    record["restricted"] = left_r or right_r
    saved = db.insert_report_tieout(record)
    if not saved:
        _fail(user, 500, "db_error", "Failed to store tie-out")
    s = result["summary"]
    _audit(user, "tieout_created", str(saved["id"]), "success",
           {"outcome": s["outcome"], "items": s["items"], "exceptions": s["exceptions"],
            "result_sha256": saved["result_sha256"], "engagement": record["engagement"]})
    return {**saved, "summary": s, "items": result["items"]}


@router.get("", summary="List tie-outs (metadata only)")
def list_all(engagement: Optional[str] = None, user: dict = Depends(get_current_user)):
    if not db.is_available():
        return {"tieouts": []}
    return {"tieouts": [t for t in db.list_report_tieouts(engagement=engagement) if _visible(t, user)]}


@router.get("/{tieout_id}", summary="One tie-out in full")
def get_one(tieout_id: int, user: dict = Depends(get_current_user)):
    rec = _load(tieout_id, user)
    payload = json.loads(rec.pop("payload_json"))
    items = json.loads(rec.pop("items_json"))
    return {**rec, "payload": payload, "items": items}


@router.get("/{tieout_id}/verify", summary="Check signature, item hash and the arithmetic")
def verify_one(tieout_id: int, user: dict = Depends(get_current_user)):
    rec = _load(tieout_id, user)
    try:
        result = to.verify(rec, sign=db.sign_evidence_record)
    except db.EvidenceSigningKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # Snapshot sources: each must still be the exact rows the tie-out used, and the
    # marks must follow from them. (Inline sources were rechecked inside to.verify.)
    sources_ok = True
    if result["intact"]:
        payload = json.loads(rec["payload_json"])
        rows_by_role: Dict[str, List[dict]] = {}
        for src in payload["sources"]:
            if src["type"] != "snapshot":
                rows_by_role[src["role"]] = payload["inline_rows"][src["role"]]
                continue
            snap = db.get_report_snapshot(src["snapshot_id"])
            snap_ok = (snap is not None
                       and rs.verify(snap, sign=db.sign_evidence_record)["intact"]
                       and to.describe_source(src["role"], "snapshot", src["label"], snap["rows_json"])["rows_sha256"]
                       == src["rows_sha256"])
            if not snap_ok:
                sources_ok = False
                break
            rows_by_role[src["role"]] = json.loads(snap["rows_json"])
        if sources_ok:
            again = to.recompute_with(rec, rows_by_role["left"], rows_by_role["right"])
            sources_ok = again["reproduced"]
        if not sources_ok:
            result = {**result, "intact": False, "detail": "a source snapshot is missing or altered, "
                                                           "or the marks do not follow from the sources"}
    result["sources_ok"] = sources_ok
    _audit(user, "tieout_verified", str(tieout_id), "success" if result["intact"] else "failure",
           {"signature_valid": result["signature_valid"], "items_match_hash": result["items_match_hash"],
            "sources_ok": sources_ok})
    return {"id": tieout_id, **result}
