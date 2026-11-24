#!/usr/bin/env python3
"""
External-auditor role: deny-by-default policy, both enforcement layers, assignment
scoping and the read-only portal API. No database: stores are in-memory fakes.

    pytest test_external_auditor.py -v
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import auth_endpoints
import external_auditor as ea
import external_endpoints as ep
import reporting_catalog as rc
import reporting_definitions as rd

sign = lambda t: hmac.new(b"k", t.encode(), hashlib.sha256).hexdigest()  # noqa: E731


# ── policy ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/auth/me", "/auth/logout", "/auth/change-password", "/auth/touch",
                                  "/external/engagements", "/external/runs/5/xlsx", "/external/runs/5/pdf", "/external/runs/5/pptx", "/external/admin/access"])
def test_portal_and_session_paths_are_reachable(path):
    assert ea.path_allowed(path)


@pytest.mark.parametrize("path", [
    "/", "/reporting/query", "/reporting/definitions/", "/pbc-requests/", "/evidence-quality/items",
    "/auth/users", "/auth/admin/users", "/auth/admin/roles", "/auth/users/me/manager", "/approvals/inbox",
    "/risk-register/reviews", "/observability/audit-log", "/externalx", "/external", "/auth/me/../admin",
    "/external/../reporting/query", "/external//x", "/external/%2e%2e/x", "\\external\\x", "external/engagements", "",
])
def test_everything_else_is_refused(path):
    assert not ea.path_allowed(path)


def test_non_strings_are_refused():
    assert not ea.path_allowed(None) and not ea.path_allowed(5)


def test_only_the_exact_role_is_external():
    assert ea.is_external("external_auditor")
    for r in ("admin", "user", "External_Auditor", "external_auditor ", None, ""):
        assert not ea.is_external(r)


# ── layer 2: get_current_user reads the role from the database ──────────────

@pytest.fixture
def session(monkeypatch):
    audits = []
    state = {"user": {"id": 7, "username": "aud", "role": "external_auditor", "is_active": True}}
    monkeypatch.setattr(auth_endpoints, "_resolve_session", lambda c: (7, "jti", None))
    monkeypatch.setattr(auth_endpoints.auth_db, "get_user_by_id", lambda i: dict(state["user"]))
    monkeypatch.setattr(auth_endpoints.db, "insert_audit_log_entry", lambda *a, **k: audits.append({"args": a, **k}) or 1)
    return type("S", (), {"state": state, "audits": audits})


class Req:
    def __init__(self, path, method="GET"):
        self.method = method
        self.url = type("U", (), {"path": path})()
        self.client = None
        self.headers = {}


def current(path):
    return auth_endpoints.get_current_user(Req(path), "cookie")


def test_external_user_is_refused_outside_the_portal_and_it_is_audited(session):
    with pytest.raises(HTTPException) as ei:
        current("/reporting/definitions/")
    assert ei.value.status_code == 403
    a = session.audits[-1]
    assert a["args"][:2] == ("auth", "external_access_denied") and a["outcome"] == "denied"
    assert a["detail"]["reason"] == "path_not_allowed" and a["detail"]["path"] == "/reporting/definitions/"


def test_external_user_passes_on_portal_and_session_paths(session):
    assert current("/external/engagements")["username"] == "aud"
    assert current("/auth/me")["username"] == "aud"


def test_role_is_taken_from_the_database_not_the_token(session):
    """The token may still say admin (issued before a demotion); the DB role decides."""
    session.state["user"]["role"] = "external_auditor"
    with pytest.raises(HTTPException) as ei:
        current("/auth/admin/users")
    assert ei.value.status_code == 403


def test_other_roles_are_unaffected(session):
    for role in ("admin", "user"):
        session.state["user"]["role"] = role
        assert current("/reporting/definitions/")["role"] == role


# ── layer 1: the real middleware, through the real app ──────────────────────

@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("api_server")
    import api_server
    from fastapi.testclient import TestClient
    audits = []
    monkeypatch.setattr(api_server.auth_endpoints, "decode_jwt",
                        lambda c: {"role": c, "username": "aud", "jti": "j"} if c in ("external_auditor", "admin") else None)
    monkeypatch.setattr(api_server.auth_db, "validate_session_reason", lambda jti: (7, None))
    monkeypatch.setattr(api_server.db, "insert_audit_log_entry", lambda *a, **k: audits.append({"args": a, **k}) or 1)
    c = TestClient(api_server.app, raise_server_exceptions=False)   # no lifespan: no DB needed
    c.audits = audits
    return c


def test_middleware_refuses_external_tokens_before_any_route_runs(client):
    client.cookies.set("dendrai_session", "external_auditor")
    for path in ("/reporting/datasets", "/pbc-requests/", "/approvals/inbox", "/evidence-quality/items",
                 "/risk-register/reviews", "/openapi-nope", "/reporting/definitions/runs/1/xlsx"):
        r = client.get(path)
        assert r.status_code == 403, path
        assert r.json()["detail"] == "This account can only use the audit portal", path
    a = client.audits[-1]
    assert a["args"][:2] == ("auth", "external_access_denied") and a["outcome"] == "denied"
    assert a["detail"]["gate"] == "middleware"


def test_middleware_lets_external_tokens_through_to_the_portal(client):
    client.cookies.set("dendrai_session", "external_auditor")
    r = client.get("/external/engagements")
    assert r.status_code != 403 or r.json().get("detail") != "This account can only use the audit portal"


def test_middleware_does_not_restrict_other_roles(client):
    client.cookies.set("dendrai_session", "admin")
    r = client.get("/reporting/datasets")
    assert r.json().get("detail") != "This account can only use the audit portal"


# ── portal API ──────────────────────────────────────────────────────────────

AUD = {"id": 7, "username": "aud", "role": "external_auditor", "is_active": True}
OTHER = {"id": 8, "username": "other", "role": "external_auditor", "is_active": True}
ADMIN = {"id": 1, "username": "root", "role": "admin", "is_active": True}
INTERNAL = {"id": 2, "username": "alice", "role": "user", "is_active": True}

NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


def make_run(engagement="FY26", restricted=False, run_id=1):
    defn = rd.validate_definition({
        "title": "T", "objective": "o", "procedure": "p", "conclusion": "c",
        "blocks": [{"id": "list", "type": "table", "query": {"dataset": "controls", "columns": ["control_ref"]}}]})
    ran = rd.run_blocks(defn, {}, False, lambda cq: [{"control_ref": "AC-01"}], NOW)
    rec = rd.build_run("ctl", 1, defn, {}, ran, NOW, "bob", None, engagement, sign)
    rec.update(id=run_id, restricted=restricted, created_at="t")
    return rec


@pytest.fixture
def api(monkeypatch):
    access = {("FY26", 7)}
    audits, runs = [], {1: make_run(), 2: make_run("FY25", run_id=2), 3: make_run("FY26", True, 3),
                        4: make_run(None, run_id=4)}
    requests = {10: {"id": 10, "engagement": "FY26", "control_id": "AC-01", "status": "received", "due_date": "2030-01-01",
                     "evidence_id": 100, "requirement_snapshot": {"title": "t"}},
                11: {"id": 11, "engagement": "FY25", "control_id": "AC-02", "status": "requested", "due_date": "2030-01-01",
                     "evidence_id": 101},
                12: {"id": 12, "engagement": "FY26", "control_id": "AC-03", "status": "requested", "due_date": "2030-01-01",
                     "evidence_id": None}}
    evidence = {100: {"id": 100, "control_id": "AC-01", "title": "Review", "source_url": "https://x", "created_by": "alice",
                      "content_check": {"secret": "internal LLM note"}, "quality_flags": []},
                101: {"id": 101, "control_id": "AC-02", "title": "Other"}}
    users = {7: AUD, 8: OTHER, 2: INTERNAL, 5: {"id": 5, "username": "gone", "role": "external_auditor", "is_active": False}}
    d = ep.db
    monkeypatch.setattr(d, "is_available", lambda: True)
    monkeypatch.setattr(d, "user_has_engagement_access", lambda uid, e: (e, uid) in access)
    monkeypatch.setattr(d, "list_engagements_for_user",
                        lambda uid: [{"engagement": e, "requests_total": 2, "requests_accepted": 1} for e, u in access if u == uid])
    monkeypatch.setattr(d, "list_pbc_requests", lambda engagement=None, **k: [r for r in requests.values() if r["engagement"] == engagement])
    monkeypatch.setattr(d, "get_pbc_request", lambda i: requests.get(i))
    monkeypatch.setattr(d, "get_pbc_evidence", lambda i: evidence.get(i))
    monkeypatch.setattr(d, "list_report_runs", lambda definition_key=None, engagement=None, limit=100:
                        [{k: v for k, v in r.items() if k not in ("payload_json", "rows_json")} for r in runs.values() if r.get("engagement") == engagement])
    monkeypatch.setattr(d, "get_report_run", lambda i: dict(runs[i]) if i in runs else None)
    monkeypatch.setattr(d, "sign_evidence_record", sign)
    monkeypatch.setattr(d, "insert_audit_log_entry", lambda *a, **k: audits.append({"args": a, **k}) or 1)
    monkeypatch.setattr(ep.auth_db, "get_user_by_id", lambda i: users.get(i))
    monkeypatch.setattr(d, "grant_engagement_access", lambda e, uid, un, by: (e, uid) not in access and not access.add((e, uid)))
    monkeypatch.setattr(d, "revoke_engagement_access", lambda e, uid: ((e, uid) in access) and (access.discard((e, uid)) or True))
    monkeypatch.setattr(d, "list_engagement_auditors", lambda e: [{"user_id": u} for en, u in access if en == e])
    return type("Api", (), {"access": access, "audits": audits, "runs": runs})


def denied(api):
    return [a for a in api.audits if a["outcome"] == "denied"]


def test_only_external_accounts_can_use_the_portal(api):
    for u in (ADMIN, INTERNAL):
        with pytest.raises(HTTPException) as ei:
            ep._auditor(u)
        assert ei.value.status_code == 403
    assert ep._auditor(AUD) is AUD


def test_engagement_list_is_only_my_assignments_and_logged(api):
    out = ep.my_engagements(AUD)
    assert [e["engagement"] for e in out["engagements"]] == ["FY26"]
    assert ep.my_engagements(OTHER)["engagements"] == []
    assert api.audits[0]["args"][:2] == ("external", "external_view")


def test_requests_for_an_assigned_engagement(api):
    out = ep.requests_for("FY26", AUD)
    assert {r["id"] for r in out["requests"]} == {10, 12} and out["summary"]["total"] == 2
    assert out["requests"][0]["display_status"] in ("received", "requested")


def test_unassigned_engagement_is_404_and_logged_as_denied(api):
    with pytest.raises(HTTPException) as ei:
        ep.requests_for("FY25", AUD)
    assert ei.value.status_code == 404 and ei.value.detail == "Engagement not found"
    assert denied(api)[-1]["detail"]["reason"] == "engagement_not_assigned"
    # an engagement that does not exist looks identical
    with pytest.raises(HTTPException) as ei2:
        ep.requests_for("NOPE", AUD)
    assert ei2.value.detail == ei.value.detail


def test_blank_engagement_is_refused(api):
    with pytest.raises(HTTPException):
        ep.requests_for("  ", AUD)


def test_evidence_only_through_a_request_in_an_assigned_engagement(api):
    out = ep.request_evidence(10, AUD)
    assert out["evidence"]["title"] == "Review"
    assert "content_check" not in out["evidence"], "internal model notes are not part of the portal"
    for rid in (11, 999):
        with pytest.raises(HTTPException) as ei:
            ep.request_evidence(rid, AUD)
        assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        ep.request_evidence(12, AUD)             # assigned, but nothing linked yet
    assert ei.value.status_code == 404 and "linked" in ei.value.detail


def test_runs_are_listed_only_for_assigned_engagements_and_never_restricted(api):
    out = ep.runs_for("FY26", AUD)
    assert [r["id"] for r in out["runs"]] == [1]            # run 3 is restricted
    with pytest.raises(HTTPException):
        ep.runs_for("FY25", AUD)


@pytest.mark.parametrize("run_id", [2, 3, 4, 99])
def test_runs_outside_scope_are_404_for_every_action(api, run_id):
    for call in (ep.get_run, ep.verify_run, ep.run_html, ep.run_xlsx, ep.run_pdf, ep.run_pptx):
        with pytest.raises(HTTPException) as ei:
            call(run_id, AUD)
        assert ei.value.status_code == 404
    assert denied(api)


def test_assigned_run_can_be_read_verified_rendered_and_downloaded(api):
    got = ep.get_run(1, AUD)
    assert got["rows"]["list"] == [{"control_ref": "AC-01"}] and "payload_json" not in got
    assert ep.verify_run(1, AUD)["intact"] is True
    assert "AC-01" in ep.run_html(1, AUD).body.decode()
    x = ep.run_xlsx(1, AUD)
    assert x.body[:2] == b"PK" and "ctl-run1.xlsx" in x.headers["content-disposition"]
    assert [a["args"][1] for a in api.audits][-1] == "external_download"
    assert ep.run_pdf(1, AUD).body[:5] == b"%PDF-" and api.audits[-1]["detail"]["format"] == "pdf"
    assert ep.run_pptx(1, AUD).body[:2] == b"PK" and api.audits[-1]["detail"]["format"] == "pptx"


def test_tampered_run_fails_verification_and_is_logged_as_failure(api):
    api.runs[1]["rows_json"] = api.runs[1]["rows_json"].replace("AC-01", "FORGED")
    r = ep.verify_run(1, AUD)
    assert r["intact"] is False
    assert api.audits[-1]["outcome"] == "failure" and api.audits[-1]["detail"]["resource"] == "verify"


def test_another_auditor_cannot_use_my_assignment(api):
    for call, arg in ((ep.get_run, 1), (ep.request_evidence, 10)):
        with pytest.raises(HTTPException) as ei:
            call(arg, OTHER)
        assert ei.value.status_code == 404


def test_the_portal_has_no_write_endpoints_for_auditors():
    methods = {(r.path, m) for r in ep.router.routes for m in r.methods if r.path.startswith("/external/") and "admin" not in r.path}
    assert {m for _, m in methods} == {"GET"}


# ── admin assignment ────────────────────────────────────────────────────────

def test_grant_requires_an_active_external_account_and_a_real_engagement(api):
    ep.db.list_pbc_requests = lambda engagement=None, limit=100, **k: [{"id": 1}] if engagement in ("FY26", "FY27") else []
    ep.db.list_report_runs = lambda **k: []
    for uid, status in ((2, 422), (5, 422), (404, 404)):
        with pytest.raises(HTTPException) as ei:
            ep.grant_access(ep.GrantIn(engagement="FY26", user_id=uid), ADMIN)
        assert ei.value.status_code == status
    with pytest.raises(HTTPException) as ei:
        ep.grant_access(ep.GrantIn(engagement="TYPO", user_id=8), ADMIN)
    assert ei.value.status_code == 404 and "spelling" in ei.value.detail
    assert api.audits[-1]["outcome"] == "failure"
    out = ep.grant_access(ep.GrantIn(engagement=" FY27 ", user_id=8), ADMIN)
    assert out["newly_granted"] is True and ("FY27", 8) in api.access
    assert api.audits[-1]["args"][1] == "external_engagement_granted" and api.audits[-1]["outcome"] == "success"
    assert ep.grant_access(ep.GrantIn(engagement="FY27", user_id=8), ADMIN)["newly_granted"] is False


def test_revoke_removes_access_immediately(api):
    assert ep.requests_for("FY26", AUD)
    assert ep.revoke_access("FY26", 7, ADMIN) == {"ok": True}
    with pytest.raises(HTTPException):
        ep.requests_for("FY26", AUD)
    with pytest.raises(HTTPException) as ei:
        ep.revoke_access("FY26", 7, ADMIN)
    assert ei.value.status_code == 404


def test_admin_endpoints_require_admin():
    deps = {d.call for r in ep.router.routes if r.path.startswith("/external/admin") for d in r.dependant.dependencies}
    assert ep.require_admin in deps
