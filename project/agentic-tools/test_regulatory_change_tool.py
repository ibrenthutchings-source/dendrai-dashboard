#!/usr/bin/env python3
"""
Unit tests for regulatory_change_tool.py — content hashing, material-change
detection, and diffing. Entirely pure, same testability reasoning as
test_je_testing_tool.py / test_process_mining_tool.py.

    pytest test_regulatory_change_tool.py -v
"""
from __future__ import annotations

import regulatory_change_tool as rct


# ── content_hash ─────────────────────────────────────────────────────────────

def test_content_hash_deterministic():
    assert rct.content_hash("hello world") == rct.content_hash("hello world")


def test_content_hash_differs_for_different_text():
    assert rct.content_hash("hello world") != rct.content_hash("hello world!")


def test_content_hash_handles_none_and_empty_the_same():
    assert rct.content_hash(None) == rct.content_hash("")


# ── is_material_change ────────────────────────────────────────────────────────

def test_first_fetch_with_no_prior_text_is_material():
    assert rct.is_material_change(None, "New regulation text here.") is True
    assert rct.is_material_change("", "New regulation text here.") is True


def test_first_fetch_with_no_prior_and_no_new_text_is_not_material():
    assert rct.is_material_change(None, "") is False


def test_identical_text_is_not_material():
    text = "Article 6: high-risk AI systems must undergo conformity assessment."
    assert rct.is_material_change(text, text) is False


def test_whitespace_only_churn_is_not_material():
    old = "Article 6: high-risk AI systems must undergo conformity assessment.\n" * 50
    new = old + "\n"  # trailing newline only
    assert rct.is_material_change(old, new) is False


def test_real_content_change_is_material():
    old = "Article 6: high-risk AI systems must undergo conformity assessment." * 20
    new = "Article 6: high-risk AI systems must undergo a THIRD-PARTY conformity assessment " \
          "performed by a notified body before market placement." * 20
    assert rct.is_material_change(old, new) is True


def test_custom_ratio_threshold_is_respected():
    old = "a" * 1000
    new = "a" * 990 + "b" * 10  # 1% changed
    assert rct.is_material_change(old, new, min_ratio_delta=0.5) is False
    assert rct.is_material_change(old, new, min_ratio_delta=0.005) is True


# ── diff_summary ───────────────────────────────────────────────────────────────

def test_diff_summary_shows_added_and_removed_lines():
    old = "line one\nline two\nline three"
    new = "line one\nline TWO CHANGED\nline three\nline four"
    diff = rct.diff_summary(old, new)
    assert "-line two" in diff
    assert "+line TWO CHANGED" in diff
    assert "+line four" in diff
    assert "line three" not in diff.split("\n")[0]  # unchanged context, not a +/- line at the top


def test_diff_summary_empty_old_text_is_all_additions():
    diff = rct.diff_summary("", "brand new regulation text")
    assert "+brand new regulation text" in diff


def test_diff_summary_identical_text_produces_no_diff_lines():
    text = "unchanged content"
    diff = rct.diff_summary(text, text)
    assert "+" not in diff and "-" not in diff


def test_diff_summary_truncates_oversized_diffs():
    old = ""
    new = "\n".join(f"line {i} " + "x" * 50 for i in range(2000))
    diff = rct.diff_summary(old, new)
    assert len(diff) <= rct._MAX_DIFF_CHARS + len("\n… (diff truncated)")
    assert diff.endswith("(diff truncated)")
