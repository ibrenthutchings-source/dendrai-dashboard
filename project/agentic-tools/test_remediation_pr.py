#!/usr/bin/env python3
"""
Unit tests for remediation_endpoints._draft_pr_fix — the parsing/validation
logic behind the closed-loop remediation PR path (POST
/remediation/propose-pr/{event_id}). claude_client.complete_text_meta is
monkeypatched throughout (no real LLM call) — these tests verify the
TITLE:/BODY:/FILE: parsing, the size cap, and the "no actual change
proposed" guard, since unlike _draft_issue there is deliberately no safe
templated fallback here: a bad parse must return None, never a fabricated
"fix".

    pytest test_remediation_pr.py -v
"""
from __future__ import annotations

import remediation_endpoints as re_mod


def _event(**overrides) -> dict:
    base = {
        "id": 1, "control_id": "SCM-01", "system_source": "github",
        "event_type": "BRANCH_PROTECTION_MISSING", "raw_payload": {"repo": "acme/infra"},
    }
    base.update(overrides)
    return base


def test_draft_pr_fix_parses_title_body_file(monkeypatch):
    llm_output = (
        "TITLE: Require status checks on main\n"
        "BODY:\n"
        "Branch protection was missing a required status check.\n"
        "Added the CI check as required.\n"
        "FILE:\n"
        "name: CI\non: [push]\n"
    )
    monkeypatch.setattr(re_mod.claude_client, "complete_text_meta", lambda *a, **kw: (llm_output, "end_turn"))

    result = re_mod._draft_pr_fix(_event(), "ci.yml", "name: CI\n")

    assert result is not None
    title, body, new_content = result
    assert title == "Require status checks on main"
    assert "required status check" in body
    assert new_content == "name: CI\non: [push]\n"


def test_draft_pr_fix_returns_none_when_llm_call_fails(monkeypatch):
    def _raise(*a, **kw):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(re_mod.claude_client, "complete_text_meta", _raise)

    assert re_mod._draft_pr_fix(_event(), "ci.yml", "name: CI\n") is None


def test_draft_pr_fix_returns_none_on_malformed_output_missing_sections(monkeypatch):
    monkeypatch.setattr(re_mod.claude_client, "complete_text_meta", lambda *a, **kw: ("not the expected format", "end_turn"))
    assert re_mod._draft_pr_fix(_event(), "ci.yml", "name: CI\n") is None


def test_draft_pr_fix_returns_none_when_no_actual_change_proposed(monkeypatch):
    """A 'fix' that reproduces the file verbatim isn't a fix — must not
    silently open a no-op PR."""
    current = "name: CI\n"
    llm_output = f"TITLE: No change needed\nBODY:\nFile already correct.\nFILE:\n{current}"
    monkeypatch.setattr(re_mod.claude_client, "complete_text_meta", lambda *a, **kw: (llm_output, "end_turn"))

    assert re_mod._draft_pr_fix(_event(), "ci.yml", current) is None


def test_draft_pr_fix_returns_none_for_oversized_file(monkeypatch):
    """Never even calls the LLM for a file past the size cap — a huge
    full-file rewrite is unreliable for both the model and a human reviewer."""
    called = []
    monkeypatch.setattr(re_mod.claude_client, "complete_text_meta",
                         lambda *a, **kw: called.append(1) or ("TITLE: x\nBODY:\ny\nFILE:\nz", "end_turn"))

    huge_content = "x" * (re_mod._MAX_PR_FILE_CHARS + 1)
    result = re_mod._draft_pr_fix(_event(), "big.txt", huge_content)

    assert result is None
    assert called == []  # short-circuited before the LLM call


def test_draft_pr_fix_title_is_truncated_to_200_chars(monkeypatch):
    long_title = "T" * 300
    llm_output = f"TITLE: {long_title}\nBODY:\nsome body\nFILE:\nnew content\n"
    monkeypatch.setattr(re_mod.claude_client, "complete_text_meta", lambda *a, **kw: (llm_output, "end_turn"))

    result = re_mod._draft_pr_fix(_event(), "ci.yml", "old content\n")
    assert result is not None
    title, _body, _new = result
    assert len(title) == 200


def test_draft_pr_fix_returns_none_for_empty_file_section(monkeypatch):
    llm_output = "TITLE: A fix\nBODY:\nsome body\nFILE:\n"
    monkeypatch.setattr(re_mod.claude_client, "complete_text_meta", lambda *a, **kw: (llm_output, "end_turn"))
    assert re_mod._draft_pr_fix(_event(), "ci.yml", "old content\n") is None
