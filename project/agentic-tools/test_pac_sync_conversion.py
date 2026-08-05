#!/usr/bin/env python3
"""
Regression tests for the GitHub-sync Markdown->Rego path in pac_endpoints.py.

Every case here is one a real sync actually hit. A repo of framework policy
documents (ISO 27001 controls, an EU AI Act crosswalk, a segregation-of-duties
standard) imported NOTHING, and the reasons shown in the UI all blamed the
generated Rego:

    "converted Rego failed validation: Unbalanced braces"
    "converted Rego failed validation: Missing 'package' declaration;
     No deny_*[msg] rules found"

Neither was the real cause. Adaptive thinking spends the same max_tokens budget
as the visible answer, so those large documents exhausted the budget and came
back either cut off mid-rule (-> unbalanced braces) or completely empty (-> both
of the other two errors at once). The tests below pin the handling of each.

    pytest test_pac_sync_conversion.py -v
"""

from __future__ import annotations

import pytest

import pac_endpoints as pe


# ── _strip_code_fence ───────────────────────────────────────────────────────

def test_strip_fence_extracts_a_single_block():
    assert pe._strip_code_fence("```rego\npackage x\n```") == "package x"


def test_strip_fence_ignores_a_preamble_before_the_fence():
    assert pe._strip_code_fence("Here you go:\n\n```rego\npackage x\n```") == "package x"


def test_strip_fence_keeps_every_block_not_just_the_first():
    """Long control catalogs come back as one fenced block per control family.
    Keeping only the first silently discarded most of the module."""
    out = pe._strip_code_fence("```rego\npackage x\n```\n\ntext\n\n```rego\ndeny_a[msg] if { true }\n```")
    assert "package x" in out
    assert "deny_a[msg]" in out


def test_strip_fence_recovers_the_body_of_an_unclosed_fence():
    """An unclosed fence is what truncation looks like. Returning the raw text
    left a literal '```rego' line in the Rego, so the failure reported as a
    bogus syntax error rather than the truncation it was."""
    out = pe._strip_code_fence("```rego\npackage x\n\ndeny_a[msg] if {\n    input.y")
    assert out.startswith("package x")
    assert "```" not in out


def test_strip_fence_passes_through_unfenced_rego():
    assert pe._strip_code_fence("package x\n") == "package x"


# ── _convert_markdown_to_rego truncation handling ───────────────────────────

_GOOD = 'package controls.oracle_fusion.itgc\n\ndeny_a[msg] if {\n    input.x\n    msg := sprintf("ITGC-01: x", [])\n}\n'


def test_conversion_returns_text_on_a_normal_stop(monkeypatch):
    monkeypatch.setattr(pe.claude_client, "complete_text_meta",
                        lambda *a, **k: (_GOOD, "end_turn"))
    assert pe._convert_markdown_to_rego("itgc", "p.md", "policy") == _GOOD


def test_truncation_retries_at_lower_effort_before_giving_up(monkeypatch):
    """Dropping effort shifts budget from thinking to output — the right trade
    for a mechanical translation, and it rescues the common case."""
    calls: list = []

    def _fake(system, user, **k):
        calls.append(k["effort"])
        return (_GOOD, "end_turn") if k["effort"] == "low" else ("truncated…", "max_tokens")

    monkeypatch.setattr(pe.claude_client, "complete_text_meta", _fake)
    assert pe._convert_markdown_to_rego("itgc", "p.md", "policy") == _GOOD
    assert calls == ["high", "low"]


def test_persistent_truncation_reports_the_real_cause_not_a_syntax_error(monkeypatch):
    """This is the bug: the old code returned the fragment, which then failed
    brace-balance checking and told the user their Rego had unbalanced braces."""
    monkeypatch.setattr(pe.claude_client, "complete_text_meta",
                        lambda *a, **k: ("package x\n\ndeny_a[msg] if {", "max_tokens"))
    with pytest.raises(ValueError) as exc:
        pe._convert_markdown_to_rego("itgc", "iso-27001-controls.md", "policy")
    msg = str(exc.value)
    assert "too large to convert in one pass" in msg
    assert "iso-27001-controls.md" in msg      # names the offending file
    assert "Split" in msg                       # and what to do about it
    assert "brace" not in msg.lower()


def test_empty_completion_reports_the_real_cause(monkeypatch):
    """An empty response produced BOTH 'Missing package declaration' AND
    'No deny_*[msg] rules found' — the exact pair seen in the field."""
    monkeypatch.setattr(pe.claude_client, "complete_text_meta",
                        lambda *a, **k: ("   \n  ", "end_turn"))
    with pytest.raises(ValueError, match="returned no text"):
        pe._convert_markdown_to_rego("itgc", "segregation-of-duties.md", "policy")


def test_empty_completion_error_names_the_file(monkeypatch):
    monkeypatch.setattr(pe.claude_client, "complete_text_meta", lambda *a, **k: ("", "end_turn"))
    with pytest.raises(ValueError, match="segregation-of-duties.md"):
        pe._convert_markdown_to_rego("itgc", "segregation-of-duties.md", "policy")


def test_conversion_asks_for_a_budget_large_enough_for_a_control_catalog(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(pe.claude_client, "complete_text_meta",
                        lambda *a, **k: seen.update(k) or (_GOOD, "end_turn"))
    pe._convert_markdown_to_rego("itgc", "p.md", "policy")
    assert seen["max_tokens"] >= 32000


# ── _combine_rego_sections ──────────────────────────────────────────────────

def test_single_section_is_left_exactly_as_is():
    section = "# ─── a.rego ───\n\npackage controls.oracle_fusion.itgc\n\ndeny_a[msg] if { true }"
    assert pe._combine_rego_sections("itgc", [section]) == section


def test_combining_two_files_emits_exactly_one_package_declaration():
    """Rego allows one package declaration per module. Concatenating N files
    verbatim produced N of them, so any process backed by more than one file
    was saved as a module opa check rejects — silently, because only the
    individual pieces were validated, never the combined result."""
    a = "package controls.oracle_fusion.itgc\n\nimport future.keywords.if\n\ndeny_a[msg] if { input.a }"
    b = "package controls.oracle_fusion.itgc\n\nimport future.keywords.if\n\ndeny_b[msg] if { input.b }"
    out = pe._combine_rego_sections("itgc", [a, b])
    assert out.count("package ") == 1
    assert out.startswith("package controls.oracle_fusion.itgc")


def test_combining_dedupes_imports_but_keeps_the_union():
    a = "package p\n\nimport future.keywords.if\n\ndeny_a[msg] if { input.a }"
    b = "package p\n\nimport future.keywords.if\nimport future.keywords.in\n\ndeny_b[msg] if { input.b }"
    out = pe._combine_rego_sections("itgc", [a, b])
    assert out.count("import future.keywords.if") == 1
    assert out.count("import future.keywords.in") == 1


def test_combining_keeps_every_rule_and_its_provenance_comment():
    a = "# ─── policies/a.md ───\npackage p\n\ndeny_a[msg] if { input.a }"
    b = "# ─── policies/b.md ───\npackage p\n\ndeny_b[msg] if { input.b }"
    out = pe._combine_rego_sections("itgc", [a, b])
    assert "deny_a[msg]" in out and "deny_b[msg]" in out
    assert "policies/a.md" in out and "policies/b.md" in out


def test_combined_output_passes_the_syntax_validator():
    """The end-to-end point: what sync saves must actually validate."""
    a = "package controls.oracle_fusion.itgc\n\nimport future.keywords.if\n\ndeny_a[msg] if {\n    input.a\n    msg := sprintf(\"ITGC-01: a\", [])\n}"
    b = "package controls.oracle_fusion.itgc\n\nimport future.keywords.if\n\ndeny_b[msg] if {\n    input.b\n    msg := sprintf(\"ITGC-02: b\", [])\n}"
    ok, errors = pe._validate_rego_syntax(pe._combine_rego_sections("itgc", [a, b]))
    assert ok, errors


# ── Failed conversions land in the HITL review queue ────────────────────────
# Sync used to report a failure and discard both the prose and the draft, so a
# repo whose files all failed left nothing behind to fix — the real ISO 27001 /
# EU AI Act repo that prompted this produced five skips and zero artifacts.

def _db(monkeypatch, *, existing_doc=None, conversions=()):
    """Stub the DB layer, recording what would have been written."""
    calls: dict = {"docs": [], "conversions": [], "statuses": []}
    monkeypatch.setattr(pe.db, "is_available", lambda: True)
    monkeypatch.setattr(pe.db, "find_pac_policy_document_by_hash", lambda p, h: existing_doc)
    monkeypatch.setattr(pe.db, "save_pac_policy_document",
                        lambda process, title, text, **k: calls["docs"].append((process, title, text, k)) or 7)
    monkeypatch.setattr(pe.db, "get_pac_policy_document",
                        lambda doc_id, **k: {"id": doc_id, "conversions": list(conversions)})
    monkeypatch.setattr(pe.db, "save_pac_policy_conversion",
                        lambda doc_id, process, rego, **k: calls["conversions"].append((doc_id, rego, k)) or 99)
    monkeypatch.setattr(pe.db, "set_pac_policy_document_status",
                        lambda doc_id, s: calls["statuses"].append(s) or True)
    return calls


def test_failed_conversion_stores_the_prose_and_the_rejected_draft(monkeypatch):
    calls = _db(monkeypatch)
    out = pe._queue_failed_conversion(
        "itgc", "policies/iso-27001-controls.md", "The org shall review access quarterly.",
        "deny_a[msg] if {", ["Unbalanced braces"],
    )
    assert out == {"document_id": 7, "conversion_id": 99}

    process, title, text, kw = calls["docs"][0]
    assert text == "The org shall review access quarterly."   # prose kept verbatim
    assert kw["filename"] == "policies/iso-27001-controls.md"  # full path for provenance
    assert kw["source"] == "github"
    assert title == "iso-27001-controls.md"

    doc_id, rego, kw = calls["conversions"][0]
    assert rego == "deny_a[msg] if {"        # the rejected draft, kept for repair
    assert kw["syntax_valid"] is False
    assert kw["syntax_errors"] == ["Unbalanced braces"]
    assert calls["statuses"] == ["in_review"]


def test_requeueing_an_unchanged_file_reuses_its_document(monkeypatch):
    """Re-syncing an unchanged repo must not accumulate a new document per run."""
    calls = _db(monkeypatch, existing_doc={"id": 3, "title": "x", "status": "in_review"})
    out = pe._queue_failed_conversion("itgc", "a.md", "same text", "bad rego", ["e"])
    assert out["document_id"] == 3
    assert calls["docs"] == []               # no second document created


def test_requeueing_does_not_stack_duplicate_drafts_on_an_open_review(monkeypatch):
    """If a draft is already waiting for a decision, re-syncing points at it
    rather than burying the reviewer in identical copies."""
    calls = _db(monkeypatch,
                existing_doc={"id": 3, "title": "x", "status": "in_review"},
                conversions=[{"id": 42, "status": "pending_review"}])
    out = pe._queue_failed_conversion("itgc", "a.md", "same text", "bad rego", ["e"])
    assert out == {"document_id": 3, "conversion_id": 42}
    assert calls["conversions"] == []        # nothing new written


def test_a_resolved_document_can_be_requeued_after_a_later_failure(monkeypatch):
    """A closed (approved/rejected) conversion must not block a fresh attempt —
    otherwise a document fixed once could never be re-reported."""
    calls = _db(monkeypatch,
                existing_doc={"id": 3, "title": "x", "status": "published"},
                conversions=[{"id": 42, "status": "approved"}])
    out = pe._queue_failed_conversion("itgc", "a.md", "same text", "bad rego", ["e"])
    assert out["conversion_id"] == 99
    assert len(calls["conversions"]) == 1


def test_queueing_is_skipped_entirely_without_a_database(monkeypatch):
    monkeypatch.setattr(pe.db, "is_available", lambda: False)
    assert pe._queue_failed_conversion("itgc", "a.md", "text", "draft", ["e"]) is None


def test_a_queueing_failure_never_breaks_the_sync(monkeypatch):
    """The file is already reported as skipped; a bookkeeping error on top of
    that must not turn the whole sync into a 500."""
    monkeypatch.setattr(pe.db, "is_available", lambda: True)
    monkeypatch.setattr(pe.db, "find_pac_policy_document_by_hash",
                        lambda p, h: (_ for _ in ()).throw(RuntimeError("db down")))
    assert pe._queue_failed_conversion("itgc", "a.md", "text", "draft", ["e"]) is None


# ── extract_control_ids_from_rego (shared by both paths) ────────────────────

def test_control_ids_shared_helper_matches_the_review_path():
    import pac_policy_docs as ppd
    rego = 'msg := sprintf("P2P-01: a", [])\nmsg := sprintf("P2P-02: b", [])\nmsg := sprintf("P2P-01: c", [])'
    assert pe.extract_control_ids_from_rego(rego) == ["P2P-01", "P2P-02"] == ppd._control_ids(rego)
