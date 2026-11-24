#!/usr/bin/env python3
"""PDF and PowerPoint export of a stored report run. No database.

    pytest test_report_docs.py -v
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi import HTTPException

import reporting_definition_endpoints as ep
import reporting_export_docs as rxd
import test_report_definitions as t
from test_report_definitions import BASE, d


def payload_rows(defn=None, execute=t.fake_execute):
    v = t.rd.validate_definition(defn or BASE)
    p = t.rd.resolve_params(v, None)
    ran = t.rd.run_blocks(v, p, False, execute, t.NOW)
    run = t.rd.build_run("ctl", 2, v, p, ran, t.NOW, "alice", "note", "FY26", t.sign)
    return json.loads(run["payload_json"]), json.loads(run["rows_json"])


def pdf_text(data: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((pg.extract_text() or "") for pg in pdf.pages)


def deck(data: bytes):
    from pptx import Presentation
    return Presentation(io.BytesIO(data))


def slide_text(s) -> str:
    out = []
    for sh in s.shapes:
        if sh.has_text_frame:
            out.append(sh.text_frame.text)
        if getattr(sh, "has_table", False) and sh.has_table:
            out += [c.text for r in sh.table.rows for c in r.cells]
    return "\n".join(out)


def charts_of(prs):
    return [sh.chart for s in prs.slides for sh in s.shapes if getattr(sh, "has_chart", False) and sh.has_chart]


# ── PDF ─────────────────────────────────────────────────────────────────────

def test_pdf_has_narrative_data_and_reperformance_appendix():
    payload, rows = payload_rows()
    data = rxd.build_pdf(7, payload, rows)
    assert data[:5] == b"%PDF-"
    txt = pdf_text(data)
    for needle in ("Controls by framework", "Count controls.", "Query the control library.", "Counts agree.",
                   "AC-01", "Access", "Appendix: Reperformance", "SELECT", "rendering of a signed report run"):
        assert needle in txt, needle
    flat = txt.replace("\n", "").replace(" ", "")
    for m in payload["manifests"].values():
        assert m["result_sha256"] in flat


def test_pdf_shows_markup_in_data_literally():
    payload, rows = payload_rows()                     # data has <script>, the text block has <b>
    txt = pdf_text(rxd.build_pdf(1, payload, rows))
    assert "<script>x</script>" in txt and "<b>world</b>" in txt


def test_pdf_empty_result_and_ampersands():
    def ex(cq):
        cols = [c["name"] for c in cq.output_columns]
        if cols == ["control_ref", "name"]:
            return []
        return [{"n": 1}] if cols == ["n"] else [{"category": "R&D <x>", "n": 3}]
    payload, rows = payload_rows(execute=ex)
    txt = pdf_text(rxd.build_pdf(1, payload, rows))
    assert "No rows." in txt


def test_pdf_chart_with_non_numeric_value_says_so_instead_of_drawing_zero():
    def ex(cq):
        if [c["name"] for c in cq.output_columns] == ["category", "n"]:
            return [{"category": "A", "n": "n/a"}]
        return t.fake_execute(cq)
    payload, rows = payload_rows(execute=ex)
    assert "chart not drawn" in pdf_text(rxd.build_pdf(1, payload, rows))


def test_pdf_very_long_cell_is_clipped_and_still_builds():
    def ex(cq):
        if [c["name"] for c in cq.output_columns] == ["control_ref", "name"]:
            return [{"control_ref": "X", "name": "w " * 20000}]
        return t.fake_execute(cq)
    payload, rows = payload_rows(execute=ex)
    assert rxd.build_pdf(1, payload, rows)[:5] == b"%PDF-"


def test_pdf_many_rows_paginate_and_keep_all_rows():
    def ex(cq):
        if [c["name"] for c in cq.output_columns] == ["control_ref", "name"]:
            return [{"control_ref": f"C-{i}", "name": f"n{i}"} for i in range(400)]
        return t.fake_execute(cq)
    payload, rows = payload_rows(execute=ex)
    txt = pdf_text(rxd.build_pdf(1, payload, rows))
    assert "C-0" in txt and "C-399" in txt and "Page 3" in txt


# ── PPTX ────────────────────────────────────────────────────────────────────

def test_pptx_has_title_narrative_blocks_and_integrity_slide():
    payload, rows = payload_rows()
    prs = deck(rxd.build_pptx(7, payload, rows))
    texts = [slide_text(s) for s in prs.slides]
    assert "Controls by framework" in texts[0] and "ctl v2" in texts[0]
    joined = "\n".join(texts)
    for needle in ("Count controls.", "Query the control library.", "Counts agree.", "12", "AC-01",
                   "Integrity: hashes", "<b>world</b>", "<script>x</script>"):
        assert needle in joined, needle
    assert payload["manifests"]["list"]["result_sha256"] in texts[-1]


def test_pptx_chart_is_native_with_the_data():
    payload, rows = payload_rows()
    charts = charts_of(deck(rxd.build_pptx(1, payload, rows)))
    assert len(charts) == 1
    plot = charts[0].plots[0]
    assert list(plot.categories) == ["Access", "Change"] and list(plot.series[0].values) == [5.0, 7.0]


def test_pptx_chart_leaves_a_gap_for_a_non_numeric_value():
    def ex(cq):
        if [c["name"] for c in cq.output_columns] == ["category", "n"]:
            return [{"category": "A", "n": "n/a"}, {"category": "B", "n": 4}]
        return t.fake_execute(cq)
    payload, rows = payload_rows(execute=ex)
    ch = charts_of(deck(rxd.build_pptx(1, payload, rows)))[0]
    assert list(ch.plots[0].series[0].values) == [None, 4.0]


def test_pptx_speaker_notes_carry_full_reperformance_detail():
    payload, rows = payload_rows()
    prs = deck(rxd.build_pptx(1, payload, rows))
    m = payload["manifests"]["list"]
    notes = [s.notes_slide.notes_text_frame.text for s in prs.slides if s.has_notes_slide]
    hit = [n for n in notes if m["result_sha256"] in n]
    assert hit and m["sql"] in hit[0] and "REPERFORMANCE" in hit[0]


def test_pptx_table_truncation_is_stated():
    def ex(cq):
        if [c["name"] for c in cq.output_columns] == ["control_ref", "name"]:
            return [{"control_ref": f"C-{i}", "name": "n"} for i in range(40)]
        return t.fake_execute(cq)
    payload, rows = payload_rows(execute=ex)
    joined = "\n".join(slide_text(s) for s in deck(rxd.build_pptx(1, payload, rows)).slides)
    assert f"Showing the first {rxd.PPTX_TABLE_ROWS} of 40 rows" in joined and "C-39" not in joined


def test_pptx_long_narrative_splits_across_slides():
    payload, rows = payload_rows(d(procedure="word " * 700))
    titles = [slide_text(s).split("\n")[0] for s in deck(rxd.build_pptx(1, payload, rows)).slides]
    assert any(x.startswith("Procedure (1/") for x in titles)


# ── endpoints ───────────────────────────────────────────────────────────────

USER = {"username": "alice", "role": "user"}
ADMIN = {"username": "root", "role": "admin"}


@pytest.fixture
def stored(monkeypatch):
    payload, rows = payload_rows()
    run = {"id": 1, "payload_json": json.dumps(payload), "rows_json": json.dumps(rows), "restricted": False,
           "definition_key": "ctl"}
    audits = []
    monkeypatch.setattr(ep.db, "is_available", lambda: True)
    monkeypatch.setattr(ep.db, "get_report_run", lambda i: dict(run) if i == 1 else None)
    monkeypatch.setattr(ep.db, "insert_audit_log_entry", lambda *a, **k: audits.append({"args": a, **k}) or 1)
    return type("S", (), {"audits": audits, "run": run})


def test_endpoints_export_and_audit(stored):
    r = ep.run_pdf(1, USER)
    assert r.body[:5] == b"%PDF-" and r.media_type == "application/pdf"
    assert "ctl-run1.pdf" in r.headers["content-disposition"]
    assert stored.audits[-1]["detail"]["format"] == "pdf" and stored.audits[-1]["outcome"] == "success"
    r = ep.run_pptx(1, USER)
    assert r.body[:2] == b"PK" and "presentationml" in r.media_type and "ctl-run1.pptx" in r.headers["content-disposition"]
    assert stored.audits[-1]["detail"]["format"] == "pptx"
    with pytest.raises(HTTPException) as ei:
        ep.run_pdf(99, USER)
    assert ei.value.status_code == 404


def test_builder_failure_is_audited_and_hides_internals(stored, monkeypatch):
    def boom(*a):
        raise RuntimeError("secret internals")
    monkeypatch.setitem(ep._DOC_FORMATS, "pdf", ("application/pdf", boom))
    with pytest.raises(HTTPException) as ei:
        ep.run_pdf(1, USER)
    assert ei.value.status_code == 500 and "secret" not in ei.value.detail
    assert stored.audits[-1]["outcome"] == "failure"


def test_restricted_run_is_404_for_non_admin(stored):
    stored.run["restricted"] = True
    with pytest.raises(HTTPException) as ei:
        ep.run_pptx(1, USER)
    assert ei.value.status_code == 404
    assert ep.run_pptx(1, ADMIN).body[:2] == b"PK"
