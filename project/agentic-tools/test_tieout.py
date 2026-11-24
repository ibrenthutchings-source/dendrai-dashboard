#!/usr/bin/env python3
"""
Tick-and-tie engine and API. No database: stores are dicts, signing is a real HMAC.

    pytest test_tieout.py -v
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import HTTPException

import reporting_endpoints as re_
import tieout as to
import tieout_endpoints as te

KEY = b"test-key"


def sign(text: str) -> str:
    return hmac.new(KEY, text.encode(), hashlib.sha256).hexdigest()


def spec(**over):
    s = {"left": {"label": "GL", "key": "ref", "amount": "amt"},
         "right": {"label": "Subledger", "key": "ref", "amount": "amt"},
         "objective": "Confirm the GL balance agrees to the subledger.",
         "procedure": "Match by reference; compare amounts; apply the stated tolerance."}
    s.update(over)
    return to.validate_spec(s)


L = [{"ref": "A", "amt": "100.00"}, {"ref": "B", "amt": "50"}, {"ref": "C", "amt": "10.004"},
     {"ref": "D", "amt": "7"}, {"ref": "E", "amt": 1}]
R = [{"ref": "A", "amt": 100}, {"ref": "B", "amt": "49"}, {"ref": "C", "amt": "10"},
     {"ref": "F", "amt": "3"}, {"ref": "E", "amt": "1.00"}]


def marks(result):
    return {i["key"]: i["mark"] for i in result["items"]}


# ── spec validation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["objective", "procedure"])
def test_narrative_is_required(field):
    with pytest.raises(to.TieoutError, match=field):
        spec(**{field: "  "})


def test_spec_rejects_bad_shapes():
    for bad in (None, [], {"left": 1}):
        with pytest.raises(to.TieoutError):
            to.validate_spec(bad)
    with pytest.raises(to.TieoutError, match="left.key"):
        spec(left={"label": "x", "amount": "a"})
    with pytest.raises(to.TieoutError, match="negative"):
        spec(tolerance={"abs": "-1"})
    with pytest.raises(to.TieoutError, match="only contain"):
        spec(tolerance={"abs": "1", "bogus": "2"})
    with pytest.raises(to.TieoutError, match="not a number"):
        spec(tolerance={"pct": "ten"})


# ── marks ───────────────────────────────────────────────────────────────────

def test_exact_ties_and_exceptions_with_no_tolerance():
    r = to.compute(spec(), L, R)
    assert marks(r) == {"A": "T", "B": "D", "C": "D", "D": "L", "E": "T", "F": "X"}
    assert r["summary"]["counts"] == {"T": 2, "R": 0, "D": 2, "L": 1, "X": 1}
    assert r["summary"]["exceptions"] == 4 and r["summary"]["outcome"] == "exceptions"


def test_absolute_tolerance_marks_small_differences_R():
    r = to.compute(spec(tolerance={"abs": "0.01"}), L, R)
    assert marks(r)["C"] == "R" and marks(r)["B"] == "D"
    c = next(i for i in r["items"] if i["key"] == "C")
    assert c["difference"] == "0.004"


def test_percentage_tolerance_is_of_the_left_amount():
    # B: 50 vs 49, diff 1 = 2% of 50
    assert marks(to.compute(spec(tolerance={"pct": "2"}), L, R))["B"] == "R"
    assert marks(to.compute(spec(tolerance={"pct": "1.9"}), L, R))["B"] == "D"


def test_tolerance_boundary_is_inclusive():
    r = to.compute(spec(tolerance={"abs": "1"}), L, R)
    assert marks(r)["B"] == "R"


def test_negative_difference_uses_absolute_value():
    r = to.compute(spec(tolerance={"abs": "1"}), [{"ref": "A", "amt": "49"}], [{"ref": "A", "amt": "50"}])
    assert marks(r) == {"A": "R"}


def test_duplicate_keys_are_summed_and_line_counts_recorded():
    r = to.compute(spec(), [{"ref": "A", "amt": "60"}, {"ref": "A", "amt": "40"}], [{"ref": "A", "amt": "100"}])
    it = r["items"][0]
    assert it["mark"] == "T" and it["left_lines"] == 2 and it["right_lines"] == 1


def test_all_tied_outcome():
    r = to.compute(spec(), [{"ref": "A", "amt": "1"}], [{"ref": "A", "amt": "1.0"}])
    assert r["summary"]["outcome"] == "tied" and r["summary"]["exceptions"] == 0


def test_totals_and_net_difference_are_exact_decimals():
    r = to.compute(spec(), [{"ref": "A", "amt": "0.1"}, {"ref": "B", "amt": "0.2"}], [{"ref": "A", "amt": "0.3"}])
    s = r["summary"]
    assert s["left_total"] == "0.3" and s["right_total"] == "0.3" and s["net_difference"] == "0"


def test_output_is_ordered_and_deterministic():
    a = to.compute(spec(), L, R)
    b = to.compute(spec(), list(reversed(L)), list(reversed(R)))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@pytest.mark.parametrize("bad_rows,msg", [
    ([{"amt": "1"}], "no 'ref' column"),
    ([{"ref": "A"}], "no 'amt' column"),
    ([{"ref": " ", "amt": "1"}], "empty key"),
    ([{"ref": "A", "amt": "abc"}], "not a number"),
    ([{"ref": "A", "amt": None}], "not a number"),
    ([{"ref": "A", "amt": True}], "not a number"),
    ([{"ref": "A", "amt": "NaN"}], "finite"),
    (["x"], "no 'ref' column"),
])
def test_bad_rows_are_errors_never_silent_zeros(bad_rows, msg):
    with pytest.raises(to.TieoutError, match=msg):
        to.compute(spec(), bad_rows, [])


def test_row_cap():
    with pytest.raises(to.TieoutError, match="more than"):
        to.compute(spec(), [{"ref": str(i), "amt": "1"} for i in range(to.MAX_ROWS + 1)], [])


def test_thousands_separators_are_accepted():
    r = to.compute(spec(), [{"ref": "A", "amt": "1,234.50"}], [{"ref": "A", "amt": "1234.5"}])
    assert marks(r) == {"A": "T"}


def test_legend_covers_every_mark_and_tolerance_statement_is_plain():
    assert set(LEGEND := to.LEGEND) == {"T", "R", "D", "L", "X"} and all(LEGEND.values())
    assert to.tolerance_text({"abs": "0", "pct": "0"}).startswith("No tolerance")
    t = to.tolerance_text({"abs": "0.01", "pct": "0.5"})
    assert "0.01" in t and "0.5%" in t and " or " in t


# ── record, signing, verification ───────────────────────────────────────────

def record(inline=True, conclusion="Two exceptions to follow up.", **kw):
    sp = spec(**kw)
    res = to.compute(sp, L, R)
    srcs = [to.describe_source("left", "inline", "GL", json.dumps(L)),
            to.describe_source("right", "inline", "Sub", json.dumps(R))]
    return to.build_record(sp, srcs, res, conclusion, "alice", "2026-11-24T00:00:00+00:00", "FY26", None,
                           {"left": L, "right": R} if inline else {}, sign=sign)


def test_record_contains_everything_needed_to_reperform():
    p = json.loads(record()["payload_json"])
    assert p["legend"] == to.LEGEND and p["legend_version"] == 1
    assert p["spec"]["objective"] and p["spec"]["procedure"] and p["conclusion"]
    assert "tolerance" in p["tolerance_statement"].lower()
    assert p["inline_rows"]["left"] == L and p["summary"]["counts"]["T"] == 2


def test_conclusion_is_required():
    with pytest.raises(to.TieoutError, match="conclusion"):
        record(conclusion="   ")


def test_fresh_record_verifies_and_recomputes():
    v = to.verify(record(), sign)
    assert v["intact"] and v["recomputed_match"] is True and v["detail"] == "ok"


def test_altered_mark_is_detected_by_hash():
    rec = record()
    rec["items_json"] = rec["items_json"].replace('"mark":"D"', '"mark":"T"', 1)
    v = to.verify(rec, sign)
    assert v["intact"] is False and v["items_match_hash"] is False and "items were altered" in v["detail"]


def test_altered_mark_with_rehash_still_fails_signature():
    rec = record()
    rec["items_json"] = rec["items_json"].replace('"mark":"D"', '"mark":"T"', 1)
    rec["result_sha256"] = hashlib.sha256(rec["items_json"].encode()).hexdigest()
    v = to.verify(rec, sign)
    assert v["intact"] is False and v["items_match_hash"] is False


def test_altered_conclusion_is_detected():
    rec = record()
    rec["payload_json"] = rec["payload_json"].replace("Two exceptions", "No exceptions")
    v = to.verify(rec, sign)
    assert v["signature_valid"] is False and v["intact"] is False


def test_marks_that_do_not_follow_from_signed_inputs_are_caught():
    """Items and hash forged AND payload re-signed by someone holding the key: the
    arithmetic check still notices the marks are wrong."""
    rec = record()
    forged = json.loads(rec["items_json"])
    forged[1]["mark"] = "T"
    items_json = to._canon(forged)
    p = json.loads(rec["payload_json"])
    p["items_sha256"] = hashlib.sha256(items_json.encode()).hexdigest()
    payload_json = to._canon(p)
    rec2 = {**rec, "items_json": items_json, "payload_json": payload_json,
            "result_sha256": p["items_sha256"], "signature": sign(payload_json)}
    v = to.verify(rec2, sign)
    assert v["signature_valid"] and v["items_match_hash"] and v["recomputed_match"] is False
    assert v["intact"] is False and "do not follow" in v["detail"]


def test_wrong_key_fails():
    other = lambda t: hmac.new(b"x", t.encode(), hashlib.sha256).hexdigest()  # noqa: E731
    assert to.verify(record(), other)["signature_valid"] is False


def test_unreadable_payload_reports_not_raises():
    rec = record()
    rec["payload_json"] = "{"
    assert to.verify(rec, sign)["intact"] is False


# ── API ─────────────────────────────────────────────────────────────────────

USER = {"username": "alice", "role": "user"}
ADMIN = {"username": "root", "role": "admin"}


@pytest.fixture
def api(monkeypatch):
    snaps, ties, audits = {}, {}, []
    state = {"rows": [{"ref": "A", "amt": "100"}]}

    def ins_snap(rec):
        sid = len(snaps) + 1
        snaps[sid] = {**rec, "id": sid, "created_at": "t"}
        return {k: snaps[sid][k] for k in ("id", "dataset_key", "engagement", "note", "result_sha256",
                                           "row_count", "signature", "created_by", "created_at")}

    def ins_tie(rec):
        tid = len(ties) + 1
        ties[tid] = {**rec, "id": tid, "created_at": "t"}
        return {k: ties[tid][k] for k in ("id", "engagement", "note", "outcome", "restricted",
                                          "result_sha256", "signature", "created_by", "created_at")}

    d = re_.db
    monkeypatch.setattr(d, "insert_report_snapshot", ins_snap)
    monkeypatch.setattr(d, "get_report_snapshot", lambda i: dict(snaps[i]) if i in snaps else None)
    monkeypatch.setattr(d, "insert_report_tieout", ins_tie)
    monkeypatch.setattr(d, "get_report_tieout", lambda i: dict(ties[i]) if i in ties else None)
    monkeypatch.setattr(d, "list_report_tieouts",
                        lambda engagement=None, limit=100:
                        [{k: v for k, v in t.items() if k not in ("payload_json", "items_json")}
                         for t in ties.values()])
    monkeypatch.setattr(d, "sign_evidence_record", sign)
    monkeypatch.setattr(d, "insert_audit_log_entry", lambda *a, **k: audits.append({"args": a, **k}) or 1)
    monkeypatch.setattr(d, "is_available", lambda: True)
    monkeypatch.setattr(re_, "execute", lambda cq: state["rows"])
    return type("Api", (), {"snaps": snaps, "ties": ties, "audits": audits, "state": state})


def snap(api, rows, user=USER, dataset="controls"):
    api.state["rows"] = rows
    col = "action" if dataset == "audit_log" else "control_ref"
    return re_.create_snapshot(re_.SnapshotRequest(spec={"dataset": dataset, "columns": [col]}), user)["id"]


def req(left, right, **kw):
    base = dict(spec={"left": {"label": "GL", "key": "ref", "amount": "amt"},
                      "right": {"label": "Sub", "key": "ref", "amount": "amt"},
                      "objective": "Agree GL to subledger.", "procedure": "Match by ref, compare amounts."},
                left_source=left, right_source=right, conclusion="Reviewed; no exceptions.")
    base.update(kw)
    return te.TieoutRequest(**base)


def inline(rows, label="Bank statement"):
    return {"type": "inline", "label": label, "rows": rows}


def test_inline_tie_stores_signed_record_and_audits(api):
    out = te.create(req(inline([{"ref": "A", "amt": "5"}], "GL extract"), inline([{"ref": "A", "amt": "5"}])), USER)
    assert out["outcome"] == "tied" and out["summary"]["counts"]["T"] == 1
    assert to.verify(api.ties[1], sign)["intact"] is True
    a = api.audits[-1]
    assert a["args"][:2] == ("reporting", "tieout_created") and a["outcome"] == "success" and a["target"] == "1"


def test_inline_source_must_say_where_it_came_from(api):
    with pytest.raises(HTTPException) as ei:
        te.create(req({"type": "inline", "rows": [{"ref": "A", "amt": "1"}]}, inline([{"ref": "A", "amt": "1"}])), USER)
    assert ei.value.status_code == 422 and "label" in ei.value.detail and not api.ties
    assert api.audits[-1]["outcome"] == "failure"


def test_snapshot_sources_are_hashed_and_recorded(api):
    a = snap(api, [{"ref": "A", "amt": "100"}, {"ref": "B", "amt": "5"}])
    b = snap(api, [{"ref": "A", "amt": "100"}, {"ref": "B", "amt": "6"}])
    out = te.create(req({"type": "snapshot", "id": a}, {"type": "snapshot", "id": b}), USER)
    assert out["outcome"] == "exceptions"
    p = json.loads(api.ties[1]["payload_json"])
    left = p["sources"][0]
    assert left["type"] == "snapshot" and left["snapshot_id"] == a
    assert left["rows_sha256"] == hashlib.sha256(api.snaps[a]["rows_json"].encode()).hexdigest()
    assert p["inline_rows"] == {}


def test_tampered_snapshot_cannot_be_a_source(api):
    a = snap(api, [{"ref": "A", "amt": "1"}])
    api.snaps[a]["rows_json"] = '[{"ref":"A","amt":"999"}]'
    with pytest.raises(HTTPException) as ei:
        te.create(req({"type": "snapshot", "id": a}, inline([{"ref": "A", "amt": "1"}])), USER)
    assert ei.value.status_code == 422 and "does not verify" in ei.value.detail and not api.ties


def test_unknown_snapshot_is_404(api):
    with pytest.raises(HTTPException) as ei:
        te.create(req({"type": "snapshot", "id": 42}, inline([{"ref": "A", "amt": "1"}])), USER)
    assert ei.value.status_code == 404


def test_bad_source_type_and_shape(api):
    for bad in ({"type": "sql"}, {"type": "snapshot", "id": "1"}, {"type": "snapshot", "id": True}, {}):
        with pytest.raises(HTTPException) as ei:
            te.create(req(bad, inline([{"ref": "A", "amt": "1"}])), USER)
        assert ei.value.status_code == 422


def test_non_object_source_is_rejected():
    with pytest.raises(to.TieoutError, match="must be an object"):
        te._resolve("left", "x", USER)


def test_verify_endpoint_full_chain_and_snapshot_tampering_after_the_fact(api):
    a = snap(api, [{"ref": "A", "amt": "1"}])
    b = snap(api, [{"ref": "A", "amt": "1"}])
    te.create(req({"type": "snapshot", "id": a}, {"type": "snapshot", "id": b}), USER)
    r = te.verify_one(1, USER)
    assert r["intact"] is True and r["sources_ok"] is True and api.audits[-1]["outcome"] == "success"
    api.snaps[a]["rows_json"] = '[{"ref":"A","amt":"2"}]'          # a source later altered
    r = te.verify_one(1, USER)
    assert r["intact"] is False and r["sources_ok"] is False and api.audits[-1]["outcome"] == "failure"


def test_verify_endpoint_detects_forged_items(api):
    te.create(req(inline([{"ref": "A", "amt": "1"}]), inline([{"ref": "A", "amt": "2"}])), USER)
    assert api.ties[1]["outcome"] == "exceptions"
    api.ties[1]["items_json"] = api.ties[1]["items_json"].replace('"mark":"D"', '"mark":"T"')
    r = te.verify_one(1, USER)
    assert r["intact"] is False and r["items_match_hash"] is False


def test_restricted_dataset_tieouts_are_admin_only(api):
    a = snap(api, [{"ref": "A", "amt": "1"}], ADMIN, dataset="audit_log")
    te.create(req({"type": "snapshot", "id": a}, inline([{"ref": "A", "amt": "1"}])), ADMIN)
    assert api.ties[1]["restricted"] is True
    with pytest.raises(HTTPException) as ei:
        te.get_one(1, USER)
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException):
        te.verify_one(1, USER)
    assert te.list_all(None, USER)["tieouts"] == [] and len(te.list_all(None, ADMIN)["tieouts"]) == 1
    # and a non-admin cannot use the restricted snapshot as a source in the first place
    with pytest.raises(HTTPException) as ei:
        te.create(req({"type": "snapshot", "id": a}, inline([{"ref": "A", "amt": "1"}])), USER)
    assert ei.value.status_code == 404


def test_get_returns_parsed_payload_and_items(api):
    te.create(req(inline([{"ref": "A", "amt": "1"}]), inline([{"ref": "B", "amt": "1"}])), USER)
    got = te.get_one(1, USER)
    assert {i["mark"] for i in got["items"]} == {"L", "X"} and got["payload"]["legend"] == to.LEGEND
    assert "payload_json" not in got and "items_json" not in got


def test_missing_conclusion_and_signing_key(api, monkeypatch):
    with pytest.raises(HTTPException) as ei:
        te.create(req(inline([{"ref": "A", "amt": "1"}]), inline([{"ref": "A", "amt": "1"}]), conclusion=" "), USER)
    assert ei.value.status_code == 422 and "conclusion" in ei.value.detail

    def nokey(text):
        raise re_.db.EvidenceSigningKeyMissing("no key")
    monkeypatch.setattr(re_.db, "sign_evidence_record", nokey)
    with pytest.raises(HTTPException) as ei:
        te.create(req(inline([{"ref": "A", "amt": "1"}]), inline([{"ref": "A", "amt": "1"}])), USER)
    assert ei.value.status_code == 503 and not api.ties
    assert api.audits[-1]["detail"]["reason"] == "signing_key_missing"


def test_legend_endpoint():
    assert te.legend(USER)["legend"] == to.LEGEND


def test_router_is_gated_by_reporting_screen_and_legend_route_precedes_id():
    paths = [r.path for r in te.router.routes]
    assert paths.index("/reporting/tieouts/legend") < paths.index("/reporting/tieouts/{tieout_id}")
    assert te.router.dependencies


def test_external_auditors_cannot_reach_tieouts():
    import external_auditor
    assert external_auditor.path_allowed("/reporting/tieouts/1") is False
