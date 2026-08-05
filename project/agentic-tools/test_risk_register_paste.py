#!/usr/bin/env python3
"""
Tests for the Risk & Control Framework register import — the pasted-text path
and the Excel-engine error handling.

Both exist because of the same real failure. A user uploading an .xlsx got:

    Could not parse file: `Import openpyxl` failed. Use pip or conda to
    install the openpyxl package.

pandas raises ImportError from inside read_excel when openpyxl is missing, and
the endpoint's blanket `except Exception` turned a SERVER dependency problem
into a 400 that blamed the user's spreadsheet. The paste path then removes the
dependency from the critical path entirely: a register copied out of Excel
needs no file and no Excel engine.

    pytest test_risk_register_paste.py -v
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi import HTTPException

import risk_register_endpoints as rr


# ── Excel engine errors are the server's fault, not the user's ──────────────

def test_missing_openpyxl_reports_503_not_a_parse_error():
    class FakePd:
        @staticmethod
        def read_excel(*a, **k):
            raise ImportError("Missing optional dependency 'openpyxl'")

    with pytest.raises(HTTPException) as exc:
        rr._parse_tabular(FakePd, b"anything", "xlsx")
    # 503, because the file is fine and retrying won't help until the server
    # is fixed — a 400 tells the user to go edit a spreadsheet that isn't broken.
    assert exc.value.status_code == 503
    assert "openpyxl" in exc.value.detail
    assert ".csv" in exc.value.detail          # offers a workaround they can act on


def test_csv_upload_needs_no_excel_engine():
    df = rr._parse_tabular(pd, b"id,name\nR-1,Data breach\n", "csv")
    assert list(df.columns) == ["id", "name"]


def test_csv_upload_strips_the_utf8_bom():
    """Excel's 'CSV UTF-8' export writes a BOM; without utf-8-sig the first
    column header becomes '﻿id' and stops matching the id column."""
    df = rr._parse_tabular(pd, "﻿id,name\nR-1,Breach\n".encode("utf-8"), "csv")
    assert list(df.columns)[0] == "id"


# ── Pasted tables ───────────────────────────────────────────────────────────

def test_paste_reads_a_tab_separated_excel_copy():
    """Copying cells out of Excel or Sheets puts TSV on the clipboard."""
    text = "Risk ID\tRisk Name\tCategory\tScore\tRAG\nR-001\tSupplier fraud\tFinancial\t8.2\tRed"
    risks = rr._normalize_register(rr._parse_pasted_table(pd, text))[0]
    assert len(risks) == 1
    assert risks[0]["id"] == "R-001"
    assert risks[0]["name"] == "Supplier fraud"
    assert risks[0]["category"] == "Financial"
    assert risks[0]["score"] == 8.2
    assert risks[0]["rag"] == "Red"


def test_paste_reads_comma_separated_text():
    risks = rr._normalize_register(rr._parse_pasted_table(pd, "id,name\nR-1,Data breach"))[0]
    assert risks[0]["name"] == "Data breach"


def test_tabs_win_when_a_description_contains_a_comma():
    """A comma inside a risk description is far more likely than a stray tab,
    so the separator is chosen from the header rather than by counting."""
    text = "Risk ID\tName\nR-9\tLoss of data, including PII"
    risks = rr._normalize_register(rr._parse_pasted_table(pd, text))[0]
    assert risks[0]["name"] == "Loss of data, including PII"


def test_paste_accepts_the_same_column_spellings_as_upload():
    """The two routes must agree — data that imports as a file but not as a
    paste would be inexplicable to someone moving it between two boxes."""
    risks, _ = rr._normalize_register(rr._parse_pasted_table(
        pd, "ref,risk_statement,domain,residual_score,rating\nX-1,Fraud,Finance,7,Amber"))
    assert risks[0]["id"] == "X-1"
    assert risks[0]["name"] == "Fraud"
    assert risks[0]["category"] == "Finance"
    assert risks[0]["score"] == 7.0
    assert risks[0]["rag"] == "Amber"


def test_paste_tolerates_leading_and_trailing_blank_lines():
    risks = rr._normalize_register(rr._parse_pasted_table(pd, "\n\nid,name\nR-1,Breach\n\n"))[0]
    assert len(risks) == 1


def test_empty_paste_is_refused_with_guidance():
    with pytest.raises(HTTPException) as exc:
        rr._parse_pasted_table(pd, "   \n  ")
    assert exc.value.status_code == 422
    assert "header row" in exc.value.detail


def test_header_only_paste_is_refused():
    """Pasting just the header row is a common mis-select; silently importing
    zero risks would look like the feature was broken."""
    with pytest.raises(HTTPException) as exc:
        rr._parse_pasted_table(pd, "id,name,category")
    assert exc.value.status_code == 422
    assert "no data rows" in exc.value.detail


def test_missing_name_column_names_the_accepted_spellings():
    with pytest.raises(HTTPException) as exc:
        rr._normalize_register(rr._parse_pasted_table(pd, "id,score\nR-1,5"))[0]
    assert exc.value.status_code == 400
    assert "Risk Statement" in exc.value.detail


def test_rows_with_no_name_are_dropped_not_imported_blank():
    """Trailing empty rows are near-universal in a spreadsheet selection."""
    risks = rr._normalize_register(rr._parse_pasted_table(pd, "id,name\nR-1,Breach\nR-2,\nR-3,   "))[0]
    assert [r["name"] for r in risks] == ["Breach"]


def test_ids_are_generated_when_the_register_has_no_id_column():
    risks = rr._normalize_register(rr._parse_pasted_table(pd, "name\nFirst risk\nSecond risk"))[0]
    assert [r["id"] for r in risks] == ["UPL-001", "UPL-002"]


def test_a_non_numeric_score_is_dropped_rather_than_failing_the_import():
    """Registers routinely carry 'High'/'N/A' in a score column; one bad cell
    must not cost the user the whole import."""
    risks = rr._normalize_register(rr._parse_pasted_table(pd, "name,score\nRisk A,High\nRisk B,6.5"))[0]
    assert risks[0]["score"] is None
    assert risks[1]["score"] == 6.5


def test_defaults_are_applied_for_absent_optional_columns():
    risks = rr._normalize_register(rr._parse_pasted_table(pd, "name\nSome risk"))[0]
    assert risks[0]["category"] == "General"
    assert risks[0]["source_framework"] == "Uploaded Register"
    assert risks[0]["rag"] is None


# ── Registers with no score/RAG, combined ID+description, and controls ──────
# Modelled on a real SOX 404 control matrix, which broke the importer outright:
# every header ('Domain / Process', 'Risk ID & Description', 'Control ID &
# Description') failed exact matching, so a well-formed file was rejected with
# "Could not find a name column".

SOX_REGISTER = (
    "Framework\tDomain / Process\tRisk ID & Description\tControl ID & Description\n"
    "SOX 404\tAccess to Programs & Data\t"
    "SOX-IT-01: Unauthorized user access permits fraudulent financial edits.\t"
    "SOX-IT-01: System access requires formal manager approval; terminated users are disabled within 24 hours.\n"
    "SOX 404\tProgram Change Management\t"
    "R-IT-04: Untested or unapproved code causes reporting errors.\t"
    "SOX-IT-04: Production releases require ticket authorization, peer code review and CAB approval.\n"
)


def _sox():
    return rr._normalize_register(rr._parse_pasted_table(pd, SOX_REGISTER))


def test_header_normalisation_collapses_punctuation():
    """'Risk ID & Description' kept its '&' under the old space-only rule and
    therefore matched nothing."""
    assert rr._norm_header("Risk ID & Description") == "risk_id_description"
    assert rr._norm_header("Domain / Process") == "domain_process"
    assert rr._norm_header("  Score (1-25)  ") == "score_1_25"


def test_sox_style_register_imports_at_all():
    risks, _ = _sox()
    assert len(risks) == 2


def test_combined_id_and_description_cell_is_split():
    """The reference must become the id, not stay buried in the risk text."""
    risks, _ = _sox()
    assert risks[0]["id"] == "SOX-IT-01"
    assert risks[0]["name"] == "Unauthorized user access permits fraudulent financial edits."
    assert "SOX-IT-01" not in risks[0]["name"]


def test_a_register_with_no_score_or_rag_imports_cleanly():
    """Plenty of real registers carry no rating at all. Absent must mean
    absent — not zero, and not a failed import."""
    risks, _ = _sox()
    assert all(r["score"] is None and r["rag"] is None for r in risks)


def test_domain_process_column_becomes_the_category():
    risks, _ = _sox()
    assert risks[0]["category"] == "Access to Programs & Data"
    assert risks[1]["category"] == "Program Change Management"


def test_framework_column_is_carried_through():
    risks, _ = _sox()
    assert all(r["source_framework"] == "SOX 404" for r in risks)


def test_controls_named_by_the_register_are_extracted():
    _, controls = _sox()
    refs = {c["ref"] for c in controls}
    assert refs == {"SOX-IT-01", "SOX-IT-04"}
    ctrl = next(c for c in controls if c["ref"] == "SOX-IT-01")
    assert ctrl["description"].startswith("System access requires formal manager approval")
    assert ctrl["framework"] == "SOX 404"


def test_register_controls_beat_keyword_guessing():
    """The author already asserted which control addresses which risk —
    inferring one from keywords would override a human's explicit statement."""
    risks, _ = _sox()
    assert risks[0]["auto_controls"] == ["SOX-IT-01"]
    # Even when the row's risk id and control id disagree (real registers do
    # this), the control the register named is the one that gets attached.
    assert risks[1]["id"] == "R-IT-04"
    assert risks[1]["auto_controls"] == ["SOX-IT-04"]


def test_control_column_is_not_mistaken_for_the_risk_description():
    """'Control ID & Description' contains 'description' and would otherwise
    happily answer to the risk-description matcher."""
    risks, _ = _sox()
    assert risks[0]["name"].startswith("Unauthorized user access")


def test_registers_without_a_control_column_still_get_keyword_mapping():
    risks, controls = rr._normalize_register(rr._parse_pasted_table(
        pd, "name,category\nUnauthorized access to systems,IT"))
    assert controls == []
    assert risks[0]["auto_controls"]           # fell back to inference


def test_a_dedicated_id_column_wins_over_an_embedded_one():
    risks, _ = rr._normalize_register(rr._parse_pasted_table(
        pd, "Ref,Risk Description\nX-99,SOX-IT-01: something bad"))
    assert risks[0]["id"] == "X-99"
    assert risks[0]["name"] == "something bad"


def test_an_ordinary_sentence_is_not_parsed_as_an_id():
    """'Risk: the system may fail' opens like an ID but isn't one — the
    pattern requires a hyphenated/dotted reference."""
    risks, _ = rr._normalize_register(rr._parse_pasted_table(
        pd, "name\nRisk: the system may fail"))
    assert risks[0]["id"] == "UPL-001"
    assert risks[0]["name"] == "Risk: the system may fail"


def test_split_id_prefix_accepts_the_reference_shapes_registers_use():
    for raw, ref in [
        ("SOX-IT-01: text", "SOX-IT-01"),
        ("R-IT-04: text", "R-IT-04"),
        ("AC.1.2: text", "AC.1.2"),
        ("CC6_1: text", "CC6_1"),
    ]:
        assert rr._split_id_prefix(raw)[0] == ref, raw
    # No separator group -> not a reference.
    assert rr._split_id_prefix("Note: something")[0] is None
    assert rr._split_id_prefix("plain text")[0] is None


def test_missing_column_error_lists_what_was_actually_found():
    """A rejection has to say what the file DID contain, or the user is
    guessing at which header to rename."""
    with pytest.raises(HTTPException) as exc:
        rr._normalize_register(rr._parse_pasted_table(pd, "framework,owner\nSOX,Alice"))
    assert "framework" in exc.value.detail and "owner" in exc.value.detail
