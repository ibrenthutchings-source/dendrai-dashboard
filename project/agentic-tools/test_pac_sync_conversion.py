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
