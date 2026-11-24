"""
PDF and PowerPoint export of a stored report run (pure module: bytes in, bytes out).

Like the Excel export, both are built from the signed run record, never from a
fresh query, so they show exactly what was signed. They are *renderings*: the
signed record in the platform (and its Verify action) is the evidence, and both
documents say so on their face.

PDF  is the full document an auditor can file: title, objective and procedure,
     parameters, every block with all its stored rows, the conclusion, and an
     appendix ("Reperformance") giving per block the dataset and version, source
     tables, the exact SQL, bound parameters, row count, truncated flag and result
     SHA-256. Page footers carry the run id and result hash.
PPTX is for presenting: a title slide, a narrative slide, one slide per block
     (native, editable charts; tables show the first rows only and say so), and a
     closing slide with the hashes. Each data slide's speaker notes carry that
     block's full reperformance details, so nothing needed to redo the work is lost.

Text safety: PDF paragraphs are XML-escaped (reportlab parses markup), and
PowerPoint text is set as plain runs. Nothing from the data or definition is ever
interpreted as markup or as a formula.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any, Optional
from xml.sax.saxutils import escape

PDF_CELL_CAP = 1500          # characters shown per table cell; the Excel export is untruncated
PPTX_TABLE_ROWS = 15
PPTX_TABLE_COLS = 8
PPTX_TEXT_CHUNK = 1100       # characters of narrative per slide
NOT_THE_RECORD = ("This document is a rendering of a signed report run. The signed record in the platform, "
                  "and its Verify action, is the evidence; compare the result SHA-256 below to that record.")


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def _num(v: Any) -> Optional[float]:
    """A chart value, or None when the cell is not a number (never a silent zero)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _blocks(payload: dict) -> list:
    return payload["definition"]["definition"]["blocks"]


def _facts(run_id: int, payload: dict) -> list:
    d = payload["definition"]
    return [("Report", f"{d['key']} v{d['version']}"), ("Run", run_id), ("As of", payload["as_of"]),
            ("Run by", payload["created_by"]), ("Engagement", payload.get("engagement")),
            ("Note", payload.get("note")), ("Result SHA-256", payload["result_sha256"]),
            ("Definition SHA-256", d["sha256"])]


def _reperformance(payload: dict, bid: str) -> dict:
    m = payload["manifests"][bid]
    return {"dataset": m["dataset"]["key"], "dataset_version": m["dataset"]["version"],
            "source_tables": ", ".join(m["dataset"]["source_tables"]), "sql": m["sql"],
            "params": m["params"], "rows": m["row_count"], "truncated": m["truncated"],
            "sha256": m["result_sha256"]}


# ── PDF ─────────────────────────────────────────────────────────────────────

def build_pdf(run_id: int, payload: dict, rows_by_block: dict) -> bytes:
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    defn = payload["definition"]["definition"]
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=10, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
    cellst = ParagraphStyle("cell", parent=body, fontSize=7.5, leading=9)
    headst = ParagraphStyle("head", parent=cellst, fontName="Helvetica-Bold")
    mono = ParagraphStyle("mono", parent=body, fontName="Courier", fontSize=7, leading=8.5)
    kpi = ParagraphStyle("kpi", parent=body, fontSize=28, leading=32, fontName="Helvetica-Bold")
    P = lambda t, st=body: Paragraph(escape(_text(t)).replace("\n", "<br/>"), st)  # noqa: E731

    page = landscape(A4)
    width = page[0] - 30 * mm
    story: list = [Paragraph(escape(defn["title"]), ss["Title"])]
    if defn.get("description"):
        story.append(P(defn["description"]))
    story.append(Spacer(1, 4))
    facts = Table([[P(k, headst), P(v, cellst)] for k, v in _facts(run_id, payload) if v not in (None, "")],
                  colWidths=[35 * mm, width - 35 * mm])
    facts.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    story += [facts, Spacer(1, 6), P(NOT_THE_RECORD, small), Spacer(1, 8)]
    for label, key in (("Objective", "objective"), ("Procedure", "procedure")):
        if defn.get(key):
            story += [Paragraph(label, ss["Heading2"]), P(defn[key])]
    if payload["parameters"]:
        story.append(P("Parameters: " + ", ".join(f"{k} = {_text(v)}" for k, v in payload["parameters"].items()), small))

    def chart(b, rows):
        ch = b["chart"]
        ys = ch["y"]
        data = [[_num(r.get(y)) for r in rows] for y in ys]
        if not rows or any(v is None for s in data for v in s):
            return P("(chart not drawn: no rows, or a value is not a number; see the table in the Excel export)", small)
        d = Drawing(width, 200)
        c = VerticalBarChart() if ch["kind"] == "bar" else HorizontalLineChart()
        c.x, c.y, c.width, c.height = 40, 30, width - 60, 150
        c.data = data
        c.categoryAxis.categoryNames = [_text(r.get(ch["x"]))[:18] for r in rows]
        c.categoryAxis.labels.fontSize = 6
        c.categoryAxis.labels.angle = 30 if len(rows) > 6 else 0
        c.categoryAxis.labels.boxAnchor = "ne" if len(rows) > 6 else "n"
        c.valueAxis.labels.fontSize = 7
        palette = [colors.HexColor(h) for h in ("#2a6f97", "#c97b2b", "#4c956c", "#8e5572", "#6c757d")]
        for i in range(len(ys)):
            if ch["kind"] == "bar":
                c.bars[i].fillColor = palette[i % len(palette)]
            else:
                c.lines[i].strokeColor = palette[i % len(palette)]
                c.lines[i].strokeWidth = 1.6
        d.add(c)
        return d

    cols_by = payload["columns"]
    for b in _blocks(payload):
        if b["type"] == "text":
            story += [P(p) for p in b["content"].split("\n\n")]
            continue
        bid = b["id"]
        rows = rows_by_block.get(bid, [])
        cols = cols_by[bid]
        head = [Paragraph(escape(b.get("title") or bid), ss["Heading2"])]
        if b["type"] == "kpi":
            story += head + [Paragraph(escape(_text(rows[0].get(b["value_column"])) if rows else "—"), kpi)]
        elif b["type"] == "chart":
            story += [KeepTogether(head + [chart(b, rows)])]
        else:
            story += head
            if not rows:
                story.append(P("No rows.", small))
                continue
            clip = lambda v: (_text(v)[:PDF_CELL_CAP] + "…") if len(_text(v)) > PDF_CELL_CAP else v  # noqa: E731
            data = [[P(c["label"], headst) for c in cols]] + [[P(clip(r.get(c["name"])), cellst) for c in cols] for r in rows]
            weights = [max(6, min(40, max(len(c["label"]), *(len(_text(r.get(c["name"]))) for r in rows[:50])))) for c in cols]
            tot = float(sum(weights))
            t = Table(data, colWidths=[width * w / tot for w in weights], repeatRows=1)
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                                   ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.append(t)
    if defn.get("conclusion"):
        story += [Paragraph("Conclusion", ss["Heading2"]), P(defn["conclusion"])]

    story += [PageBreak(), Paragraph("Appendix: Reperformance", ss["Heading1"]),
              P("For each block: the dataset and version, the exact SQL, the bound parameters and the SHA-256 of the "
                "canonical result. An independent reviewer can re-run the SQL with these parameters and compare the hash.", small)]
    for b in _blocks(payload):
        if b["type"] == "text":
            continue
        rp = _reperformance(payload, b["id"])
        rows_ = [("Block", b.get("title") or b["id"]), ("Dataset", f"{rp['dataset']} (version {rp['dataset_version']})"),
                 ("Source tables", rp["source_tables"]), ("Rows", rp["rows"]), ("Truncated", rp["truncated"]),
                 ("Parameters", rp["params"]), ("Result SHA-256", rp["sha256"])]
        t = Table([[P(k, headst), P(v, cellst)] for k, v in rows_], colWidths=[32 * mm, width - 32 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
        story += [Spacer(1, 8), KeepTogether([t, Spacer(1, 3), P("SQL", headst)]), P(rp["sql"], mono)]

    foot = f"Run {run_id} · {payload['definition']['key']} v{payload['definition']['version']} · result sha256 {payload['result_sha256'][:16]}…"

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(15 * mm, 8 * mm, foot)
        canvas.drawRightString(page[0] - 15 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=page, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                      bottomMargin=16 * mm, title=defn["title"], author=payload["created_by"],
                      subject=f"Signed report run {run_id}").build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# ── PPTX ────────────────────────────────────────────────────────────────────

def build_pptx(run_id: int, payload: dict, rows_by_block: dict) -> bytes:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.util import Emu, Inches, Pt

    defn = payload["definition"]["definition"]
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    W = prs.slide_width

    def slide(title: str, notes: Optional[str] = None):
        s = prs.slides.add_slide(blank)
        tb = s.shapes.add_textbox(Inches(0.5), Inches(0.3), W - Inches(1), Inches(0.9))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.text = title[:140]
        tf.paragraphs[0].runs[0].font.size = Pt(28)
        tf.paragraphs[0].runs[0].font.bold = True
        foot = s.shapes.add_textbox(Inches(0.5), Inches(7.05), W - Inches(1), Inches(0.3))
        foot.text_frame.text = f"Run {run_id} · result sha256 {payload['result_sha256'][:16]}…"
        foot.text_frame.paragraphs[0].runs[0].font.size = Pt(9)
        if notes:
            s.notes_slide.notes_text_frame.text = notes
        return s

    def para_box(s, text: str, top=1.3, size=16, height=5.5):
        tb = s.shapes.add_textbox(Inches(0.5), Inches(top), W - Inches(1), Inches(height))
        tf = tb.text_frame
        tf.word_wrap = True
        first = True
        for chunk in text.split("\n"):
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            r = p.add_run()
            r.text = chunk
            r.font.size = Pt(size)
        return tb

    def notes_for(bid: str) -> str:
        rp = _reperformance(payload, bid)
        return ("REPERFORMANCE\n" + NOT_THE_RECORD + f"\nDataset: {rp['dataset']} (version {rp['dataset_version']})"
                f"\nSource tables: {rp['source_tables']}\nRows: {rp['rows']}  Truncated: {rp['truncated']}"
                f"\nParameters: {_text(rp['params'])}\nResult SHA-256: {rp['sha256']}\nSQL:\n{rp['sql']}")

    # title
    s = slide(defn["title"])
    para_box(s, "\n".join(f"{k}: {_text(v)}" for k, v in _facts(run_id, payload)[:6] if v not in (None, "")), top=1.5, size=16)
    para_box(s, NOT_THE_RECORD, top=5.6, size=11, height=1.2)

    # narrative
    for label, key in (("Objective", "objective"), ("Procedure", "procedure")):
        if defn.get(key):
            text = defn[key]
            parts = [text[i:i + PPTX_TEXT_CHUNK] for i in range(0, len(text), PPTX_TEXT_CHUNK)] or [""]
            for n, part in enumerate(parts, 1):
                para_box(slide(label + (f" ({n}/{len(parts)})" if len(parts) > 1 else "")), part)
    if payload["parameters"]:
        para_box(slide("Parameters"), "\n".join(f"{k} = {_text(v)}" for k, v in payload["parameters"].items()))

    for b in _blocks(payload):
        if b["type"] == "text":
            text = b["content"]
            parts = [text[i:i + PPTX_TEXT_CHUNK] for i in range(0, len(text), PPTX_TEXT_CHUNK)] or [""]
            for part in parts:
                para_box(slide(b.get("title") or "Notes"), part)
            continue
        bid = b["id"]
        rows, cols = rows_by_block.get(bid, []), payload["columns"][bid]
        s = slide(b.get("title") or bid, notes_for(bid))
        if b["type"] == "kpi":
            v = _text(rows[0].get(b["value_column"])) if rows else "—"
            tb = para_box(s, v, top=2.5, size=72, height=2)
            tb.text_frame.paragraphs[0].runs[0].font.bold = True
        elif b["type"] == "chart":
            ch = b["chart"]
            data = CategoryChartData()
            data.categories = [_text(r.get(ch["x"])) for r in rows]
            lab = {c["name"]: c["label"] for c in cols}
            for y in ch["y"]:
                data.add_series(lab.get(y, y), [_num(r.get(y)) for r in rows])   # None stays a gap
            kind = XL_CHART_TYPE.COLUMN_CLUSTERED if ch["kind"] == "bar" else XL_CHART_TYPE.LINE_MARKERS
            gf = s.shapes.add_chart(kind, Inches(0.5), Inches(1.3), W - Inches(1), Inches(5.6), data)
            gf.chart.has_legend = len(ch["y"]) > 1
            if gf.chart.has_legend:
                gf.chart.legend.position = XL_LEGEND_POSITION.BOTTOM
                gf.chart.legend.include_in_layout = False
        else:
            shown_cols = cols[:PPTX_TABLE_COLS]
            shown = rows[:PPTX_TABLE_ROWS]
            if not shown:
                para_box(s, "No rows.")
                continue
            tbl = s.shapes.add_table(len(shown) + 1, len(shown_cols), Inches(0.5), Inches(1.3),
                                     W - Inches(1), Inches(0.35) * (len(shown) + 1)).table
            for ci, c in enumerate(shown_cols):
                tbl.cell(0, ci).text = c["label"]
            for ri, r in enumerate(shown, 1):
                for ci, c in enumerate(shown_cols):
                    tbl.cell(ri, ci).text = _text(r.get(c["name"]))[:120]
            for row in tbl.rows:
                for cell in row.cells:
                    for p in cell.text_frame.paragraphs:
                        for run in p.runs:
                            run.font.size = Pt(11)
            caveats = []
            if len(rows) > len(shown):
                caveats.append(f"Showing the first {len(shown)} of {len(rows)} rows.")
            if len(cols) > len(shown_cols):
                caveats.append(f"Showing the first {len(shown_cols)} of {len(cols)} columns.")
            if caveats:
                para_box(s, " ".join(caveats) + " The full data is in the PDF and Excel exports.", top=6.5, size=11, height=0.5)

    if defn.get("conclusion"):
        text = defn["conclusion"]
        parts = [text[i:i + PPTX_TEXT_CHUNK] for i in range(0, len(text), PPTX_TEXT_CHUNK)] or [""]
        for n, part in enumerate(parts, 1):
            para_box(slide("Conclusion" + (f" ({n}/{len(parts)})" if len(parts) > 1 else "")), part)

    lines = [f"{k}: {_text(v)}" for k, v in _facts(run_id, payload)[6:]]
    for b in _blocks(payload):
        if b["type"] != "text":
            lines.append(f"{b.get('title') or b['id']}: {payload['manifests'][b['id']]['result_sha256']}")
    para_box(slide("Integrity: hashes", NOT_THE_RECORD), "\n".join(lines) + "\n\nFull SQL and parameters for each block are in "
             "that slide's speaker notes, and in the PDF and Excel exports.", size=12)

    buf = io.BytesIO()
    prs.core_properties.title = defn["title"]
    prs.core_properties.author = payload["created_by"]
    prs.save(buf)
    return buf.getvalue()
