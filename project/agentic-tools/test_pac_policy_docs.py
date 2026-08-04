#!/usr/bin/env python3
"""
Unit tests for pac_policy_docs.py — plain-language policy intake and the
human-in-the-loop gate between a generated draft and a live Rego module.

Two things are worth guarding here, and they are the reason this module exists:

  1. Text extraction is the only place a policy document can be silently
     corrupted. A .docx whose tags weren't stripped, or a scanned PDF stored as
     an empty string, would both "succeed" and then be converted into
     confidently wrong Rego. Those cases must fail loudly at upload.

  2. The HITL gate must not be bypassable. The decision endpoint is the only
     code path that writes to pac_policy_modules, so the tests below pin its
     preconditions: a reviewer name is mandatory, an already-decided conversion
     can't be re-decided, and syntactically invalid Rego can never publish.

    pytest test_pac_policy_docs.py -v
"""

from __future__ import annotations

import io
import zipfile
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import pac_policy_docs as ppd


# ── Text extraction ─────────────────────────────────────────────────────────

def test_extract_text_reads_markdown():
    assert ppd._extract_text("policy.md", b"# Access Control\n\nNo shared accounts.") \
        == "# Access Control\n\nNo shared accounts."


def test_extract_text_accepts_extensionless_file():
    # A file dragged out of a wiki export often has no extension at all;
    # treating that as "unsupported" would reject perfectly readable prose.
    assert ppd._extract_text("POLICY", b"No shared accounts.") == "No shared accounts."


def test_extract_text_rejects_unsupported_type_and_names_the_alternatives():
    with pytest.raises(ValueError) as exc:
        ppd._extract_text("policy.exe", b"\x00\x01")
    msg = str(exc.value)
    assert ".exe" in msg and "docx" in msg and "pdf" in msg


def test_extract_text_rejects_legacy_doc_with_actionable_advice():
    with pytest.raises(ValueError, match="save as"):
        ppd._extract_text("policy.doc", b"\xd0\xcf\x11\xe0")


def _docx(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body>{body}</w:body></w:document>")
    return buf.getvalue()


def test_extract_docx_keeps_paragraph_breaks_and_drops_markup():
    text = ppd._extract_docx_text(_docx("Segregation of duties", "Reviewed quarterly"))
    assert text == "Segregation of duties\nReviewed quarterly"
    assert "<w:" not in text


def test_extract_docx_decodes_xml_entities():
    # "&amp;" reaching the conversion prompt as literal "&amp;" is the kind of
    # corruption that survives all the way into a control description.
    assert ppd._extract_docx_text(_docx("Approvals &amp; exceptions")) == "Approvals & exceptions"


def test_extract_docx_rejects_a_zip_that_is_not_a_word_document():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "not a docx")
    with pytest.raises(ValueError, match="not a Word document"):
        ppd._extract_docx_text(buf.getvalue())


# ── Draft analysis ──────────────────────────────────────────────────────────

_VALID_REGO = """package controls.oracle_fusion.itgc

deny_access_event[msg] if {
    not input.event.approved_by
    msg := sprintf("ITGC-AC-01: user '%v' provisioned without approval", [input.event.username])
}
"""


def test_analyze_reports_valid_rego_with_its_control_ids():
    result = ppd._analyze(_VALID_REGO)
    assert result["syntax_valid"] is True
    assert result["control_ids"] == ["ITGC-AC-01"]
    assert result["rule_coverage"] == {"total": 1, "with_control_id": 1}


def test_analyze_flags_rego_with_no_package_declaration():
    result = ppd._analyze('deny_a[msg] if { msg := sprintf("ITGC-AC-01: x", []) }')
    assert result["syntax_valid"] is False
    assert result["syntax_errors"]


def test_control_ids_are_deduped_in_source_order():
    rego = (
        'msg := sprintf("P2P-01: a", [])\n'
        'msg := sprintf("P2P-02: b", [])\n'
        'msg := sprintf("P2P-01: c", [])\n'
    )
    assert ppd._control_ids(rego) == ["P2P-01", "P2P-02"]


def test_control_ids_ignores_prose_that_merely_contains_a_colon():
    assert ppd._control_ids('msg := sprintf("the user was denied: no approval", [])') == []


def test_title_from_filename_is_human_readable():
    assert ppd._title_from_filename("access_control-policy.md") == "Access Control Policy"
    assert ppd._title_from_filename("") == "Policy"


def test_next_version_minor_bumps_the_live_module(monkeypatch):
    monkeypatch.setattr(ppd.db, "is_available", lambda: True)
    monkeypatch.setattr(ppd.db, "get_latest_pac_module", lambda p: {"version": "2.7"})
    assert ppd._next_version("itgc") == "2.8"


def test_next_version_handles_a_module_with_no_parseable_version(monkeypatch):
    monkeypatch.setattr(ppd.db, "is_available", lambda: True)
    monkeypatch.setattr(ppd.db, "get_latest_pac_module", lambda p: {"version": "draft"})
    assert ppd._next_version("itgc") == "1.1"


# ── The HITL gate ───────────────────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch):
    """App with the screen-permission gate stubbed out and the DB reported as
    available, so these tests exercise the endpoint logic rather than auth."""
    app = FastAPI()
    app.include_router(ppd.router)
    app.dependency_overrides[ppd.router.dependencies[0].dependency] = lambda: {"username": "tester"}
    monkeypatch.setattr(ppd.db, "is_available", lambda: True)
    return TestClient(app)


def _conversion(**over) -> dict:
    base = {
        "id": 1, "document_id": 10, "process": "itgc",
        "generated_rego": _VALID_REGO, "draft_rego": _VALID_REGO,
        "status": "pending_review", "syntax_valid": True,
        "reviewer": None, "control_ids": ["ITGC-AC-01"], "syntax_errors": [],
    }
    base.update(over)
    return base


def test_decision_requires_a_reviewer(client, monkeypatch):
    monkeypatch.setattr(ppd.db, "get_pac_policy_conversion", lambda cid: _conversion())
    r = client.post("/pac/conversions/1/decision", json={"decision": "approve", "reviewer": "  "})
    assert r.status_code == 422
    assert "human-in-the-loop" in r.json()["detail"]


def test_decision_rejects_an_unknown_verb(client, monkeypatch):
    monkeypatch.setattr(ppd.db, "get_pac_policy_conversion", lambda cid: _conversion())
    r = client.post("/pac/conversions/1/decision", json={"decision": "publish", "reviewer": "Dana"})
    assert r.status_code == 422


def test_decision_is_not_repeatable_once_closed(client, monkeypatch):
    monkeypatch.setattr(ppd.db, "get_pac_policy_conversion",
                        lambda cid: _conversion(status="approved", reviewer="Ari"))
    r = client.post("/pac/conversions/1/decision", json={"decision": "reject", "reviewer": "Dana"})
    assert r.status_code == 409
    assert "Ari" in r.json()["detail"]


def test_approving_invalid_rego_never_publishes(client, monkeypatch):
    """The one guarantee that matters: no path publishes Rego OPA can't parse."""
    published: list = []
    monkeypatch.setattr(ppd.db, "get_pac_policy_conversion",
                        lambda cid: _conversion(draft_rego="deny_a[msg] if {", syntax_valid=False))
    monkeypatch.setattr(ppd.db, "save_pac_module", lambda *a, **k: published.append(a) or 1)

    r = client.post("/pac/conversions/1/decision", json={"decision": "approve", "reviewer": "Dana"})
    assert r.status_code == 422
    assert published == []


def test_approving_publishes_the_reviewed_draft_not_the_model_output(client, monkeypatch):
    """The reviewer's edits are what goes live — publishing generated_rego
    instead would silently discard the entire point of the review step."""
    edited = _VALID_REGO.replace("ITGC-AC-01", "ITGC-AC-99")
    saved: dict = {}

    def _save(process, module_name, rego, version, source_format="rego"):
        saved.update(process=process, rego=rego, version=version, source_format=source_format)
        return 77

    monkeypatch.setattr(ppd.db, "get_pac_policy_conversion",
                        lambda cid: _conversion(draft_rego=edited))
    monkeypatch.setattr(ppd.db, "get_latest_pac_module", lambda p: {"version": "1.4"})
    monkeypatch.setattr(ppd.db, "save_pac_module", _save)
    monkeypatch.setattr(ppd.db, "record_pac_conversion_decision", lambda *a, **k: True)
    monkeypatch.setattr(ppd.db, "set_pac_policy_document_status", lambda *a: True)

    r = client.post("/pac/conversions/1/decision",
                    json={"decision": "approve", "reviewer": "Dana", "reviewer_role": "Control Owner"})
    assert r.status_code == 200
    body = r.json()
    assert body["published_module_id"] == 77
    assert body["published_version"] == "1.5"
    assert body["document_status"] == "published"
    assert saved["rego"] == edited
    assert saved["source_format"] == "llm_converted"


def test_request_changes_leaves_the_draft_open_and_publishes_nothing(client, monkeypatch):
    recorded: dict = {}
    published: list = []
    monkeypatch.setattr(ppd.db, "get_pac_policy_conversion", lambda cid: _conversion())
    monkeypatch.setattr(ppd.db, "save_pac_module", lambda *a, **k: published.append(a) or 1)
    monkeypatch.setattr(ppd.db, "record_pac_conversion_decision",
                        lambda cid, status, reviewer, **k: recorded.update(status=status) or True)
    monkeypatch.setattr(ppd.db, "set_pac_policy_document_status",
                        lambda doc_id, s: recorded.update(doc_status=s) or True)

    r = client.post("/pac/conversions/1/decision",
                    json={"decision": "request_changes", "reviewer": "Dana", "notes": "AC-02 is missing"})
    assert r.status_code == 200
    assert recorded["status"] == "changes_requested"
    assert recorded["doc_status"] == "in_review"
    assert published == []


def test_rejecting_a_re_conversion_does_not_unpublish_the_live_module(client, monkeypatch):
    """A document with an already-published earlier draft stays 'published'
    when a later re-conversion is rejected — rejecting a draft is not an
    instruction to retract live policy."""
    statuses: list = []
    monkeypatch.setattr(ppd.db, "get_pac_policy_conversion", lambda cid: _conversion(id=2))
    monkeypatch.setattr(ppd.db, "get_pac_policy_document", lambda doc_id, **k: {
        "conversions": [{"id": 1, "published_module_id": 55}, {"id": 2, "published_module_id": None}],
    })
    monkeypatch.setattr(ppd.db, "record_pac_conversion_decision", lambda *a, **k: True)
    monkeypatch.setattr(ppd.db, "set_pac_policy_document_status",
                        lambda doc_id, s: statuses.append(s) or True)

    r = client.post("/pac/conversions/2/decision",
                    json={"decision": "reject", "reviewer": "Dana", "notes": "worse than the live one"})
    assert r.status_code == 200
    assert statuses == ["published"]


def test_rejecting_a_never_published_document_returns_it_to_the_intake_queue(client, monkeypatch):
    statuses: list = []
    monkeypatch.setattr(ppd.db, "get_pac_policy_conversion", lambda cid: _conversion())
    monkeypatch.setattr(ppd.db, "get_pac_policy_document", lambda doc_id, **k: {
        "conversions": [{"id": 1, "published_module_id": None}],
    })
    monkeypatch.setattr(ppd.db, "record_pac_conversion_decision", lambda *a, **k: True)
    monkeypatch.setattr(ppd.db, "set_pac_policy_document_status",
                        lambda doc_id, s: statuses.append(s) or True)

    r = client.post("/pac/conversions/1/decision",
                    json={"decision": "reject", "reviewer": "Dana", "notes": "hallucinated controls"})
    assert r.status_code == 200
    assert statuses == ["uploaded"]


def test_draft_edits_are_refused_once_the_conversion_is_closed(client, monkeypatch):
    monkeypatch.setattr(ppd.db, "get_pac_policy_conversion", lambda cid: _conversion(status="approved"))
    # The DB guard is the real enforcement; it reports "no row updated".
    monkeypatch.setattr(ppd.db, "update_pac_policy_conversion_draft", lambda *a, **k: False)
    r = client.put("/pac/conversions/1/draft", json={"rego_content": _VALID_REGO})
    assert r.status_code == 409


def test_upload_rejects_an_unknown_process_before_reading_the_file(client, monkeypatch):
    monkeypatch.setattr(ppd.pac_endpoints, "_valid_processes", lambda: {"itgc"})
    r = client.post("/pac/policy-docs", json={"process": "not_a_process", "title": "T", "text": "x"})
    assert r.status_code == 400
    assert "not_a_process" in r.json()["detail"]


def test_paste_stores_the_text_verbatim(client, monkeypatch):
    """The source of record must be byte-identical to what was submitted —
    any normalisation here would break the "this is what we wrote" claim."""
    text = "  No shared accounts.\n\n\tExceptions need CFO approval.  "
    captured: dict = {}
    monkeypatch.setattr(ppd.pac_endpoints, "_valid_processes", lambda: {"itgc"})
    monkeypatch.setattr(ppd.db, "find_pac_policy_document_by_hash", lambda p, h: None)
    monkeypatch.setattr(ppd.db, "save_pac_policy_document",
                        lambda process, title, doc_text, **k: captured.update(text=doc_text) or 5)

    r = client.post("/pac/policy-docs", json={"process": "itgc", "title": "SoD", "text": text})
    assert r.status_code == 200
    assert captured["text"] == text
    assert r.json()["document_id"] == 5


def test_uploading_identical_text_is_allowed_but_reported(client, monkeypatch):
    monkeypatch.setattr(ppd.pac_endpoints, "_valid_processes", lambda: {"itgc"})
    monkeypatch.setattr(ppd.db, "find_pac_policy_document_by_hash",
                        lambda p, h: {"id": 3, "title": "SoD", "status": "published"})
    monkeypatch.setattr(ppd.db, "save_pac_policy_document", lambda *a, **k: 9)

    r = client.post("/pac/policy-docs", json={"process": "itgc", "title": "SoD again", "text": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["document_id"] == 9            # still saved
    assert body["duplicate_of"]["id"] == 3     # and flagged


def test_empty_document_is_refused(client, monkeypatch):
    monkeypatch.setattr(ppd.pac_endpoints, "_valid_processes", lambda: {"itgc"})
    r = client.post("/pac/policy-docs", json={"process": "itgc", "title": "T", "text": "   \n  "})
    assert r.status_code == 422


def test_oversized_document_is_refused_with_the_limit_named(client, monkeypatch):
    monkeypatch.setattr(ppd.pac_endpoints, "_valid_processes", lambda: {"itgc"})
    r = client.post("/pac/policy-docs",
                    json={"process": "itgc", "title": "T", "text": "x" * (ppd.MAX_TEXT_CHARS + 1)})
    assert r.status_code == 413
    assert f"{ppd.MAX_TEXT_CHARS:,}" in r.json()["detail"]


def test_convert_saves_the_draft_for_review_rather_than_publishing_it(client, monkeypatch):
    """Conversion must never touch pac_policy_modules — that is the whole
    difference between this path and sync_github's fire-and-forget import."""
    published: list = []
    saved_conversion: dict = {}

    monkeypatch.setattr(ppd.db, "get_pac_policy_document",
                        lambda doc_id, **k: {"id": doc_id, "process": "itgc", "title": "SoD",
                                             "filename": "sod.md", "doc_text": "No shared accounts."})
    monkeypatch.setattr(ppd.db, "set_pac_policy_document_status", lambda *a: True)
    monkeypatch.setattr(ppd.db, "save_pac_module", lambda *a, **k: published.append(a) or 1)
    monkeypatch.setattr(ppd.pac_endpoints, "_convert_markdown_to_rego",
                        lambda process, path, text: f"```rego\n{_VALID_REGO}```")
    monkeypatch.setattr(ppd.db, "save_pac_policy_conversion",
                        lambda doc_id, process, rego, **k: saved_conversion.update(rego=rego, **k) or 42)

    r = client.post("/pac/policy-docs/10/convert", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["conversion_id"] == 42
    assert body["status"] == "pending_review"
    assert published == []
    # The ``` fence the model wraps its output in must be stripped before storage.
    assert saved_conversion["rego"].startswith("package controls.oracle_fusion.itgc")
    assert saved_conversion["syntax_valid"] is True


def test_convert_still_stores_a_draft_that_fails_validation(client, monkeypatch):
    """A near-miss draft a reviewer can fix by hand is far more useful than a
    discarded one — sync_github's silent drop is what made those failures
    impossible to diagnose."""
    saved: dict = {}
    monkeypatch.setattr(ppd.db, "get_pac_policy_document",
                        lambda doc_id, **k: {"id": doc_id, "process": "itgc", "title": "SoD",
                                             "filename": None, "doc_text": "No shared accounts."})
    monkeypatch.setattr(ppd.db, "set_pac_policy_document_status", lambda *a: True)
    monkeypatch.setattr(ppd.pac_endpoints, "_convert_markdown_to_rego",
                        lambda *a: "deny_a[msg] if {")
    monkeypatch.setattr(ppd.db, "save_pac_policy_conversion",
                        lambda doc_id, process, rego, **k: saved.update(k) or 43)

    r = client.post("/pac/policy-docs/10/convert", json={})
    assert r.status_code == 200
    assert r.json()["syntax_valid"] is False
    assert saved["syntax_valid"] is False
    assert saved["syntax_errors"]


def test_convert_truncates_a_pathologically_long_document_and_says_so(client, monkeypatch):
    sent: dict = {}
    monkeypatch.setattr(ppd.db, "get_pac_policy_document",
                        lambda doc_id, **k: {"id": doc_id, "process": "itgc", "title": "Huge",
                                             "filename": None,
                                             "doc_text": "x" * (ppd.CONVERSION_INPUT_CHARS + 5000)})
    monkeypatch.setattr(ppd.db, "set_pac_policy_document_status", lambda *a: True)
    monkeypatch.setattr(ppd.pac_endpoints, "_convert_markdown_to_rego",
                        lambda process, path, text: sent.update(n=len(text)) or _VALID_REGO)
    monkeypatch.setattr(ppd.db, "save_pac_policy_conversion", lambda *a, **k: 44)

    r = client.post("/pac/policy-docs/10/convert", json={})
    assert r.status_code == 200
    assert r.json()["source_truncated"] is True
    assert sent["n"] == ppd.CONVERSION_INPUT_CHARS
