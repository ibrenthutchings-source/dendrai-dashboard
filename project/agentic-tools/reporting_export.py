"""
XLSX export of a stored report run (pure module: bytes in, bytes out).

Built from the signed run record, never from a fresh query, so the workbook shows
exactly what was signed. Sheets:
  Summary        report, version, run id, as-of, run by, parameters, narrative
                 (objective, procedure, conclusion), overall hashes
  <one per data block>   column labels and the stored rows
  Reperformance  per block: dataset and version, source tables, the exact SQL,
                 bound parameters, row count, truncated flag and result SHA-256

The Reperformance sheet is what lets an independent reviewer re-run each block and
compare the hash. Every cell is written as an explicit string or number: text
starting with = + - @ would otherwise be evaluated by Excel as a formula (CSV/formula
injection from data or definitions).
"""
from __future__ import annotations

import io
import json
import re
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

_BAD_SHEET = re.compile(r"[\[\]\:\*\?\/\\]")


def _put(ws, row: int, col: int, value: Any, bold: bool = False):
    if isinstance(value, bool):
        value = "TRUE" if value else "FALSE"
    elif isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True)
    cell = ws.cell(row=row, column=col)
    if isinstance(value, (int, float)):
        cell.value = value
    elif isinstance(value, (datetime, date)):
        cell.value = value.isoformat()
        cell.data_type = "s"
    elif value is None:
        cell.value = None
    else:
        cell.value = str(value)
        cell.data_type = "s"          # never a formula
    if bold:
        cell.font = Font(bold=True)
    return cell


def _sheet_name(base: str, used: set) -> str:
    name = _BAD_SHEET.sub("_", base).strip("'")[:31] or "Sheet"
    candidate, n = name, 2
    while candidate.lower() in used:
        suffix = f" ({n})"
        candidate, n = name[:31 - len(suffix)] + suffix, n + 1
    used.add(candidate.lower())
    return candidate


def build_xlsx(run_id: int, payload: dict, rows_by_block: dict) -> bytes:
    d = payload["definition"]
    defn = d["definition"]
    wb = Workbook()
    used: set = set()

    s = wb.active
    s.title = _sheet_name("Summary", used)
    r = 1
    _put(s, r, 1, defn["title"], bold=True)
    s.cell(row=r, column=1).font = Font(bold=True, size=14)
    r += 2
    facts = [("Report", f"{d['key']} v{d['version']}"), ("Run", run_id), ("As of", payload["as_of"]),
             ("Run by", payload["created_by"]), ("Engagement", payload.get("engagement")),
             ("Note", payload.get("note")),
             ("Result SHA-256", payload["result_sha256"]), ("Definition SHA-256", d["sha256"])]
    for k, v in facts:
        _put(s, r, 1, k, bold=True)
        _put(s, r, 2, v)
        r += 1
    r += 1
    _put(s, r, 1, "Parameters", bold=True)
    r += 1
    for k, v in payload["parameters"].items():
        _put(s, r, 1, k)
        _put(s, r, 2, v)
        r += 1
    for label, key in (("Objective", "objective"), ("Procedure", "procedure"), ("Conclusion", "conclusion")):
        if defn.get(key):
            r += 1
            _put(s, r, 1, label, bold=True)
            c = _put(s, r, 2, defn[key])
            c.alignment = Alignment(wrap_text=True, vertical="top")
    s.column_dimensions["A"].width = 22
    s.column_dimensions["B"].width = 90

    # Payload keys are stored sorted, so take block order from the definition.
    data_blocks = [b["id"] for b in defn["blocks"] if b["type"] != "text"]
    titles = {b["id"]: (b.get("title") or b["id"]) for b in defn["blocks"] if b["type"] != "text"}
    for bid in data_blocks:
        ws = wb.create_sheet(_sheet_name(titles.get(bid, bid), used))
        cols = payload["columns"][bid]
        for ci, c in enumerate(cols, 1):
            _put(ws, 1, ci, c["label"], bold=True)
            ws.column_dimensions[ws.cell(row=1, column=ci).column_letter].width = max(12, min(40, len(c["label"]) + 4))
        for ri, row in enumerate(rows_by_block.get(bid, []), 2):
            for ci, c in enumerate(cols, 1):
                _put(ws, ri, ci, row.get(c["name"]))
        ws.freeze_panes = "A2"

    rp = wb.create_sheet(_sheet_name("Reperformance", used))
    heads = ["Block", "Dataset", "Dataset version", "Source tables", "SQL", "Parameters",
             "Rows", "Truncated", "Result SHA-256"]
    for ci, h in enumerate(heads, 1):
        _put(rp, 1, ci, h, bold=True)
    for ri, bid in enumerate(data_blocks, 2):
        m = payload["manifests"][bid]
        vals = [bid, m["dataset"]["key"], m["dataset"]["version"], ", ".join(m["dataset"]["source_tables"]),
                m["sql"], m["params"], m["row_count"], m["truncated"], m["result_sha256"]]
        for ci, v in enumerate(vals, 1):
            c = _put(rp, ri, ci, v)
            if ci in (5, 6):
                c.alignment = Alignment(wrap_text=True, vertical="top")
    rp.column_dimensions["E"].width = 80
    rp.column_dimensions["F"].width = 30
    rp.column_dimensions["I"].width = 66

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
