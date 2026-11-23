#!/usr/bin/env python3
"""
Report-as-Code: definition validation, parameters, versioning/approval, signed
runs, verification, rerun and rendering. No database (an in-memory store fakes it).

    pytest test_report_definitions.py -v
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import reporting_catalog as rc
import reporting_definition_endpoints as ep
import reporting_definitions as rd

KEY = b"k"
sign = lambda t: hmac.new(KEY, t.encode(), hashlib.sha256).hexdigest()  # noqa: E731
NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)

BASE = {
    "title": "Controls by framework",
    "objective": "Count controls.", "procedure": "Query the control library.", "conclusion": "Counts agree.",
    "parameters": [{"name": "fw", "type": "string", "label": "Framework", "default": "SOX"}],
    "blocks": [
        {"id": "intro", "type": "text", "content": "Hello <b>world</b>"},
        {"id": "total", "type": "kpi", "title": "Total", "value_column": "n",
         "query": {"dataset": "controls", "aggregates": [{"fn": "count", "column": "*", "as": "n"}],
                   "filters": [{"column": "framework", "op": "eq", "value": {"$param": "fw"}}]}},
        {"id": "bycat", "type": "chart", "title": "By category", "chart": {"kind": "bar", "x": "category", "y": ["n"]},
         "query": {"dataset": "controls", "columns": ["category"], "group_by": ["category"],
                   "aggregates": [{"fn": "count", "column": "*", "as": "n"}]}},
        {"id": "list", "type": "table", "title": "Controls",
         "query": {"dataset": "controls", "columns": ["control_ref", "name"],
                   "filters": [{"column": "framework", "op": "eq", "value": {"$param": "fw"}}]}},
    ],
}


def d(**over):
    x = copy.deepcopy(BASE)
    x.update(over)
    return x


def fake_execute(cq):
    cols = [c["name"] for c in cq.output_columns]
    if cols == ["n"]:
        return [{"n": 12}]
    if cols == ["category", "n"]:
        return [{"category": "Access", "n": 5}, {"category": "Change", "n": 7}]
    return [{"control_ref": "AC-01", "name": "<script>x</script>"}]


# ── validation ──────────────────────────────────────────────────────────────

def test_valid_definition_normalises():
    v = rd.validate_definition(BASE)
    assert v["definition_version"] == 1 and [b["id"] for b in v["blocks"]] == ["intro", "total", "bycat", "list"]
    assert rd.definition_hash(v) == rd.definition_hash(rd.validate_definition(copy.deepcopy(BASE)))


@pytest.mark.parametrize("mutate,msg", [
    (lambda x: x.update(title=""), "title"),
    (lambda x: x.update(blocks=[]), "at least one block"),
    (lambda x: x["blocks"].append(dict(x["blocks"][0])), "duplicate block"),
    (lambda x: x["blocks"][0].update(type="script"), "type must be"),
    (lambda x: x["blocks"][3]["query"].update(columns=["nope"]), "unknown column"),
    (lambda x: x["blocks"][3]["query"].update(dataset="audit_log"), "restricted"),
    (lambda x: x["blocks"][1].update(value_column="zzz"), "value_column"),
    (lambda x: x["blocks"][2]["chart"].update(kind="pie"), "chart.kind"),
    (lambda x: x["blocks"][2]["chart"].update(y=["category"]), "number column"),
    (lambda x: x["blocks"][2]["chart"].update(x="zzz"), "chart.x"),
    (lambda x: x["blocks"][3]["query"]["filters"][0].update(value={"$param": "ghost"}), "undeclared"),
    (lambda x: x["parameters"].append({"name": "fw"}), "duplicate parameter"),
    (lambda x: x["parameters"][0].update(type="blob"), "type must be"),
    (lambda x: x["blocks"][0].update(id="Bad Id"), "invalid block id"),
])
def test_invalid_definitions_are_rejected_with_a_reason(mutate, msg):
    x = copy.deepcopy(BASE)
    mutate(x)
    with pytest.raises(rd.DefinitionError) as ei:
        rd.validate_definition(x)
    assert msg in str(ei.value)


def test_admin_can_define_reports_on_restricted_datasets_and_they_are_flagged():
    x = d(blocks=[{"id": "a", "type": "table", "query": {"dataset": "audit_log", "columns": ["action"]}}])
    v = rd.validate_definition(x, is_admin=True)
    assert rd.is_restricted(v) is True
    assert rd.is_restricted(rd.validate_definition(BASE)) is False


def test_missing_narrative_is_reported():
    v = rd.validate_definition(d(procedure="", conclusion=""))
    assert rd.missing_narrative(v) == ["procedure", "conclusion"]
    assert rd.missing_narrative(rd.validate_definition(BASE)) == []


def test_check_key():
    assert rd.check_key(" My-Report_1 ") == "my-report_1"
    for bad in ("", "1abc", "runs", "a b", "x" * 65):
        with pytest.raises(rd.DefinitionError):
            rd.check_key(bad)


# ── parameters ──────────────────────────────────────────────────────────────

def test_parameters_default_override_and_coercion():
    v = rd.validate_definition(d(parameters=[
        {"name": "fw", "type": "string", "default": "SOX"},
        {"name": "n", "type": "number", "required": False},
        {"name": "since", "type": "date"}]))
    assert rd.resolve_params(v, {"since": "2026-01-31T10:00"}) == {"fw": "SOX", "n": None, "since": "2026-01-31"}
    assert rd.resolve_params(v, {"fw": "ISO", "since": "2026-01-01", "n": "3"})["n"] == 3


def test_parameter_errors():
    v = rd.validate_definition(d(parameters=[{"name": "fw", "type": "string"}]))
    with pytest.raises(rd.DefinitionError, match="required"):
        rd.resolve_params(v, {})
    with pytest.raises(rd.DefinitionError, match="unknown"):
        rd.resolve_params(v, {"fw": "x", "evil": "1"})
    v2 = rd.validate_definition(d(parameters=[{"name": "since", "type": "date", "default": "2026-01-01"}],
                                  blocks=[BASE["blocks"][0]]))
    with pytest.raises(rd.DefinitionError, match="valid date"):
        rd.resolve_params(v2, {"since": "yesterday"})


def test_param_value_is_bound_never_inlined():
    v = rd.validate_definition(BASE)
    cq = rd.compile_block(v["blocks"][3], {"fw": "SOX'; DROP TABLE controls_library;--"}, False)
    assert "DROP TABLE" not in cq.sql and "SOX'; DROP TABLE controls_library;--" in cq.params


# ── runs ────────────────────────────────────────────────────────────────────

def make_run(defn=None, params=None, execute=fake_execute):
    v = rd.validate_definition(defn or BASE)
    p = rd.resolve_params(v, params)
    ran = rd.run_blocks(v, p, False, execute, NOW)
    return v, p, ran, rd.build_run("ctl", 2, v, p, ran, NOW, "alice", "note", "FY26", sign)


def test_run_record_verifies_and_embeds_everything_needed_to_reperform():
    v, p, ran, run = make_run()
    assert rd.verify_run(run, sign)["intact"] is True
    payload = json.loads(run["payload_json"])
    assert payload["definition"]["definition"] == v and payload["definition"]["sha256"] == rd.definition_hash(v)
    assert payload["parameters"] == {"fw": "SOX"} and payload["as_of"] == NOW.isoformat()
    assert set(payload["manifests"]) == {"total", "bycat", "list"}
    assert payload["manifests"]["total"]["params"] == ["SOX", ] + [payload["manifests"]["total"]["params"][-1]]


@pytest.mark.parametrize("tamper,field", [
    (lambda r: r.update(rows_json=r["rows_json"].replace("AC-01", "AC-99")), "result rows"),
    (lambda r: r.update(payload_json=r["payload_json"].replace('"created_by":"alice"', '"created_by":"eve"')), "signature"),
])
def test_tampering_is_detected(tamper, field):
    *_, run = make_run()
    tamper(run)
    r = rd.verify_run(run, sign)
    assert r["intact"] is False and field in r["detail"]


def test_attacker_rewriting_rows_and_all_hashes_still_fails_signature():
    *_, run = make_run()
    rows = json.loads(run["rows_json"])
    rows["list"] = [{"control_ref": "EVIL", "name": "x"}]
    run["rows_json"] = rd.canonical(rows)
    run["result_sha256"] = hashlib.sha256(run["rows_json"].encode()).hexdigest()
    payload = json.loads(run["payload_json"])
    payload["result_sha256"] = run["result_sha256"]
    run["payload_json"] = rd.canonical(payload)
    r = rd.verify_run(run, sign)
    assert r["intact"] is False and r["signature_valid"] is False and r["blocks_match"] is False


def test_embedded_definition_edit_is_detected():
    *_, run = make_run()
    payload = json.loads(run["payload_json"])
    payload["definition"]["definition"]["title"] = "Different"
    run["payload_json"] = rd.canonical(payload)
    r = rd.verify_run(run, sign)
    assert r["definition_matches"] is False and r["intact"] is False


def test_one_failing_block_fails_the_whole_run():
    def boom(cq):
        if [c["name"] for c in cq.output_columns] == ["category", "n"]:
            raise RuntimeError("db down")
        return fake_execute(cq)
    with pytest.raises(RuntimeError):
        make_run(execute=boom)


def test_kpi_must_return_exactly_one_row():
    with pytest.raises(rd.DefinitionError, match="exactly one row"):
        make_run(execute=lambda cq: [{"n": 1}, {"n": 2}])


def test_rerun_comparison_per_block():
    *_, run = make_run()
    payload = json.loads(run["payload_json"])
    v = payload["definition"]["definition"]
    same = rd.run_blocks(v, payload["parameters"], False, fake_execute, NOW)
    assert rd.compare_rerun(payload, same["manifests"])["reproduced"] is True

    def moved(cq):
        r = fake_execute(cq)
        return [{**r[0], "n": 99}] + r[1:] if "n" in r[0] and len(r) == 1 else r
    out = rd.compare_rerun(payload, rd.run_blocks(v, payload["parameters"], False, moved, NOW)["manifests"])
    assert out["reproduced"] is False and out["blocks"]["total"]["reason"].startswith("source data")
    assert out["blocks"]["list"]["reproduced"] is True


# ── rendering ───────────────────────────────────────────────────────────────

def test_render_escapes_user_content_and_shows_narrative_and_meta():
    v, p, ran, _ = make_run()
    html = rd.render_html(v, p, ran["results"], {"Run": 7, "Note": "<img src=x onerror=1>"})
    assert "<script>x</script>" not in html and "&lt;script&gt;x&lt;/script&gt;" in html
    assert "Hello &lt;b&gt;world&lt;/b&gt;" in html and "<img src=x" not in html
    assert "Objective" in html and "Conclusion" in html and "Counts agree." in html
    assert "<svg" in html and "<rect" in html and ">12<" in html and "fw = SOX" in html


def test_line_chart_and_empty_chart_render():
    assert "<polyline" in rd._svg_chart("line", "x", ["y"], [{"x": "a", "y": 1}, {"x": "b", "y": 3}])
    assert "No data" in rd._svg_chart("bar", "x", ["y"], [])


# ── API ─────────────────────────────────────────────────────────────────────

USER = {"username": "alice", "role": "user"}
AUTHOR = {"username": "bob", "role": "admin"}
APPROVER = {"username": "carol", "role": "admin"}


@pytest.fixture
def api(monkeypatch):
    versions, runs, audits = [], [], []

    def ins_ver(key, title, dj, sha, by, restricted=False):
        v = {"id": len(versions) + 1, "definition_key": key,
             "version": 1 + max([x["version"] for x in versions if x["definition_key"] == key], default=0),
             "title": title, "definition_json": dj, "definition_sha256": sha, "status": "draft",
             "restricted": restricted, "created_by": by, "created_at": "t", "approved_by": None, "approved_at": None}
        versions.append(v)
        return {k: val for k, val in v.items() if k != "definition_json"}

    def get_ver(key, version=None, approved_only=False):
        c = [v for v in versions if v["definition_key"] == key and (version is None or v["version"] == version)
             and (not approved_only or v["status"] == "approved")]
        return dict(max(c, key=lambda v: v["version"])) if c else None

    def set_status(key, version, status, actor):
        for v in versions:
            if v["definition_key"] == key and v["version"] == version:
                if status == "approved" and (v["status"] != "draft" or v["created_by"] == actor):
                    return None
                if status == "retired" and v["status"] == "retired":
                    return None
                v["status"] = status
                if status == "approved":
                    v["approved_by"] = actor
                return {k: val for k, val in v.items() if k != "definition_json"}
        return None

    def ins_run(r):
        row = {**r, "id": len(runs) + 1, "created_at": "t"}
        runs.append(row)
        return {k: v for k, v in row.items() if k not in ("payload_json", "rows_json")}

    m = ep.db
    monkeypatch.setattr(m, "is_available", lambda: True)
    monkeypatch.setattr(m, "insert_report_definition_version", ins_ver)
    monkeypatch.setattr(m, "get_report_definition_version", get_ver)
    monkeypatch.setattr(m, "list_report_definition_versions",
                        lambda key: [{k: v for k, v in x.items() if k != "definition_json"}
                                     for x in versions if x["definition_key"] == key])
    monkeypatch.setattr(m, "set_report_definition_status", set_status)
    monkeypatch.setattr(m, "insert_report_run", ins_run)
    monkeypatch.setattr(m, "get_report_run", lambda i: dict(runs[i - 1]) if 0 < i <= len(runs) else None)
    monkeypatch.setattr(m, "list_report_runs",
                        lambda definition_key=None, engagement=None, limit=100:
                        [{k: v for k, v in r.items() if k not in ("payload_json", "rows_json")} for r in runs])
    monkeypatch.setattr(m, "sign_evidence_record", sign)
    monkeypatch.setattr(m, "insert_audit_log_entry", lambda *a, **k: audits.append({"args": a, **k}) or 1)
    monkeypatch.setattr(ep.re_, "execute", fake_execute)
    return type("Api", (), {"versions": versions, "runs": runs, "audits": audits})


def save(user=AUTHOR, key="ctl", defn=None):
    return ep.save_definition(key, ep.SaveIn(definition=defn or BASE), user)


def approved(api):
    save()
    return ep.approve("ctl", 1, APPROVER)


def test_save_creates_incrementing_immutable_draft_versions(api):
    a, b = save(), save(defn=d(title="Second"))
    assert (a["version"], b["version"]) == (1, 2) and a["status"] == "draft" and a["missing_narrative"] == []
    assert api.audits[-1]["outcome"] == "success" and api.audits[-1]["target"] == "ctl@2"
    assert ep.get_version("ctl", 1, AUTHOR)["definition"]["title"] == "Controls by framework"
    assert [v["version"] for v in ep.history("ctl", AUTHOR)["versions"]] == [1, 2]


def test_invalid_definition_is_422_and_audited(api):
    with pytest.raises(HTTPException) as ei:
        save(defn=d(blocks=[]))
    assert ei.value.status_code == 422 and api.audits[-1]["outcome"] == "failure"
    assert not api.versions


def test_approval_needs_admin_a_different_person_and_a_narrative(api):
    save()
    with pytest.raises(HTTPException) as ei:
        ep.approve("ctl", 1, USER)
    assert ei.value.status_code == 403 and api.audits[-1]["outcome"] == "denied"
    with pytest.raises(HTTPException) as ei:
        ep.approve("ctl", 1, AUTHOR)
    assert ei.value.status_code == 403 and api.audits[-1]["detail"]["reason"] == "not_independent"
    assert api.versions[0]["status"] == "draft"
    save(defn=d(objective="", conclusion=""))
    with pytest.raises(HTTPException) as ei:
        ep.approve("ctl", 2, APPROVER)
    assert ei.value.status_code == 422 and "objective" in ei.value.detail
    assert ep.approve("ctl", 1, APPROVER)["status"] == "approved"
    with pytest.raises(HTTPException) as ei:
        ep.approve("ctl", 1, APPROVER)
    assert ei.value.status_code == 409


def test_draft_cannot_be_run_only_previewed(api):
    save()
    with pytest.raises(HTTPException) as ei:
        ep.run("ctl", ep.RunIn(), AUTHOR)
    assert ei.value.status_code == 404 and api.audits[-1]["detail"]["reason"] == "no_approved_version"
    out = ep.preview("ctl", ep.RunIn(), AUTHOR)
    assert out["status"] == "draft" and "not a record" in out["html"] and not api.runs


def test_run_stores_signed_record_and_verifies(api):
    approved(api)
    out = ep.run("ctl", ep.RunIn(parameters={"fw": "ISO"}, note="Q3", engagement="FY26"), AUTHOR)
    assert out["id"] == 1 and out["parameters"] == {"fw": "ISO"} and "<svg" in out["html"]
    assert api.audits[-1]["outcome"] == "success" and api.audits[-1]["target"] == "1"
    assert ep.verify_run(1, AUTHOR)["intact"] is True
    stored = ep.get_run(1, AUTHOR)
    assert stored["payload"]["definition"]["version"] == 1 and stored["rows"]["total"] == [{"n": 12}]
    assert [r["id"] for r in ep.list_runs(None, None, AUTHOR)["runs"]] == [1]


def test_stored_run_renders_from_stored_rows_not_a_requery(api, monkeypatch):
    approved(api)
    ep.run("ctl", ep.RunIn(), AUTHOR)
    monkeypatch.setattr(ep.re_, "execute", lambda cq: (_ for _ in ()).throw(AssertionError("must not query")))
    html = ep.run_html(1, AUTHOR).body.decode()
    assert "AC-01" in html and "Result SHA-256" in html and "ctl v1" in html


def test_verify_endpoint_flags_tampered_rows_and_audits_failure(api):
    approved(api)
    ep.run("ctl", ep.RunIn(), AUTHOR)
    api.runs[0]["rows_json"] = api.runs[0]["rows_json"].replace("AC-01", "FORGED")
    r = ep.verify_run(1, AUTHOR)
    assert r["intact"] is False and api.audits[-1]["outcome"] == "failure"


def test_bad_parameters_are_422_and_audited_and_store_nothing(api):
    approved(api)
    with pytest.raises(HTTPException) as ei:
        ep.run("ctl", ep.RunIn(parameters={"nope": 1}), AUTHOR)
    assert ei.value.status_code == 422 and not api.runs
    assert api.audits[-1]["detail"]["reason"] == "invalid_parameters"


def test_execution_error_hides_details_and_stores_nothing(api, monkeypatch):
    approved(api)
    monkeypatch.setattr(ep.re_, "execute", lambda cq: (_ for _ in ()).throw(RuntimeError("secret_table")))
    with pytest.raises(HTTPException) as ei:
        ep.run("ctl", ep.RunIn(), AUTHOR)
    assert ei.value.status_code == 500 and "secret_table" not in ei.value.detail and not api.runs


def test_rerun_reports_reproduced_then_moved_data(api, monkeypatch):
    approved(api)
    ep.run("ctl", ep.RunIn(), AUTHOR)
    assert ep.rerun(1, AUTHOR)["reproduced"] is True
    monkeypatch.setattr(ep.re_, "execute", lambda cq: [{"n": 1}] if [c["name"] for c in cq.output_columns] == ["n"]
                        else fake_execute(cq))
    r = ep.rerun(1, AUTHOR)
    assert r["reproduced"] is False and r["blocks"]["total"]["reproduced"] is False


def test_restricted_reports_are_invisible_to_non_admins(api):
    x = d(blocks=[{"id": "a", "type": "table", "query": {"dataset": "audit_log", "columns": ["action"]}}])
    save(defn=x)
    assert api.versions[0]["restricted"] is True
    ep.approve("ctl", 1, APPROVER)
    ep.run("ctl", ep.RunIn(), AUTHOR)
    for call in (lambda: ep.get_version("ctl", 1, USER), lambda: ep.history("ctl", USER),
                 lambda: ep.preview("ctl", ep.RunIn(), USER), lambda: ep.get_run(1, USER),
                 lambda: ep.verify_run(1, USER), lambda: ep.run_html(1, USER), lambda: ep.rerun(1, USER)):
        with pytest.raises(HTTPException) as ei:
            call()
        assert ei.value.status_code == 404
    with pytest.raises(HTTPException):
        ep.run("ctl", ep.RunIn(), USER)
    assert ep.list_runs(None, None, USER)["runs"] == [] and len(ep.list_runs(None, None, AUTHOR)["runs"]) == 1


def test_non_admin_cannot_author_on_restricted_dataset(api):
    x = d(blocks=[{"id": "a", "type": "table", "query": {"dataset": "audit_log", "columns": ["action"]}}])
    with pytest.raises(HTTPException) as ei:
        save(user=USER, defn=x)
    assert ei.value.status_code == 422 and api.audits[-1]["outcome"] == "denied"


def test_retire_blocks_running(api):
    approved(api)
    assert ep.retire("ctl", 1, AUTHOR)["status"] == "retired"
    with pytest.raises(HTTPException):
        ep.run("ctl", ep.RunIn(), AUTHOR)
    with pytest.raises(HTTPException) as ei:
        ep.retire("ctl", 1, USER)
    assert ei.value.status_code == 403


def test_missing_signing_key_is_503_and_stores_nothing(api, monkeypatch):
    approved(api)
    def nokey(t):
        raise ep.db.EvidenceSigningKeyMissing("EVIDENCE_SIGNING_KEY is not set")
    monkeypatch.setattr(ep.db, "sign_evidence_record", nokey)
    with pytest.raises(HTTPException) as ei:
        ep.run("ctl", ep.RunIn(), AUTHOR)
    assert ei.value.status_code == 503 and not api.runs
