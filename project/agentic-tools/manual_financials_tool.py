#!/usr/bin/env python3
"""
Manual Financial Statement Ingestion — Dendrai Intelligenza

Lets a user supply financial data the risk pipeline can't get from SEC EDGAR:
  - a private company with no CIK/XBRL filings at all
  - finer-grained monthly detail to supplement a public company's quarterly filings

All input adapters (Excel/CSV today; PDF and API connectors phased in later,
see IngestionAdapter below) converge on one common shape — a "line item":
    {raw_label, metric, confidence, period_start, period_end, value, granularity,
     segment_type, segment_name}
`metric` is mapped onto the same internal taxonomy edgar_tool.XBRL_METRICS
already uses (Revenue, NetIncome, TotalAssets, ...) so the ratio/Beneish/
Altman models in predictive_analytics_tool.py need zero changes to consume
manually-entered data — see db.get_manual_financials / build_company_xbrl.

`segment_type` ('geography' | 'business_segment') and `segment_name` are
optional and both None on an ordinary consolidated line item. When present,
commit_line_items() routes the row to db.upsert_sox_segment (the same
sox_financial_segments table edgar_segments.py populates from filed XBRL,
tagged source='filed') instead of the consolidated xbrl_metric_series path,
tagged source='uploaded' — so private companies and non-filers can populate
the Risk Coverage Cube's operating-unit axis the same way public filers do.

Two spreadsheet layouts are auto-detected:
  1. Template      — columns: metric | period_start | period_end | value | [granularity]
                      Deterministic; for annual/quarterly entry or private-company financials.
  2. Trial balance — first column is a line-item label, remaining columns are
                      month-end dates (e.g. "Jan-2024", "2024-02-29"); melted into monthly rows.
                      For filling in interim months during a quarter.

Everything parse_* returns is unpersisted — callers show it to the user for
review/edit (metric can be corrected/assigned for "unmapped" rows), then
commit_line_items() persists the reviewed set.
"""

from __future__ import annotations

import calendar
import difflib
import io
import re
from datetime import date
from typing import Optional

from edgar_tool import XBRL_METRICS

# ─────────────────────────────────────────────────────────────────────────────
# Metric taxonomy + synonym matching
# ─────────────────────────────────────────────────────────────────────────────

METRIC_SYNONYMS: dict[str, list[str]] = {
    "Revenue": ["revenue", "revenues", "total revenue", "net revenue", "net sales",
                "sales", "total sales", "net service revenue"],
    "GrossProfit": ["gross profit", "gross margin"],
    "OperatingIncome": ["operating income", "income from operations", "operating profit"],
    "NetIncome": ["net income", "net earnings", "net profit", "net income loss"],
    "EPS_Basic": ["eps basic", "basic eps", "earnings per share basic", "basic earnings per share"],
    "EPS_Diluted": ["eps diluted", "diluted eps", "earnings per share diluted", "diluted earnings per share"],
    "TotalAssets": ["total assets"],
    "CurrentAssets": ["current assets", "total current assets"],
    "CurrentLiabilities": ["current liabilities", "total current liabilities"],
    "TotalLiabilities": ["total liabilities"],
    "StockholdersEquity": ["stockholders equity", "shareholders equity", "total equity", "owners equity"],
    "RetainedEarnings": ["retained earnings", "accumulated deficit"],
    "Cash": ["cash", "cash and cash equivalents", "cash and equivalents"],
    "LongTermDebt": ["long term debt", "notes payable long term"],
    "OperatingCashFlow": ["operating cash flow", "cash from operations",
                           "net cash provided by operating activities",
                           "net cash from operating activities"],
    "CapEx": ["capital expenditures", "capex", "purchases of property and equipment",
              "purchases of property plant and equipment"],
    "Depreciation": ["depreciation", "depreciation and amortization", "d and a"],
    "SharesOutstanding": ["shares outstanding", "common shares outstanding"],
    "Dividends": ["dividends paid", "dividends"],
    "ResearchAndDevelopment": ["r and d", "research and development", "research and development expense"],
    "IncomeTaxExpense": ["income tax expense", "provision for income taxes", "taxes"],
    "InterestExpense": ["interest expense"],
    "Inventory": ["inventory", "inventories"],
    "AccountsReceivable": ["accounts receivable", "receivables", "trade receivables"],
}

_ALL_METRICS = list(XBRL_METRICS.keys())

_LABEL_TO_METRIC: dict[str, str] = {}
for _m in _ALL_METRICS:
    _LABEL_TO_METRIC[_m.lower()] = _m
    for _syn in METRIC_SYNONYMS.get(_m, []):
        _LABEL_TO_METRIC[_syn] = _m


def _normalize_label(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9&\s]", " ", str(s).lower())).strip()


def map_label_to_metric(label: str) -> tuple[Optional[str], str]:
    """Map a free-text line-item label onto the internal metric taxonomy.
    Returns (metric_name_or_None, confidence) where confidence is
    "exact" | "fuzzy" | "unmapped". Unmapped rows are still returned (not
    dropped) so the review UI can let a user map them by hand."""
    norm = _normalize_label(label)
    if norm in _LABEL_TO_METRIC:
        return _LABEL_TO_METRIC[norm], "exact"
    match = difflib.get_close_matches(norm, _LABEL_TO_METRIC.keys(), n=1, cutoff=0.75)
    if match:
        return _LABEL_TO_METRIC[match[0]], "fuzzy"
    return None, "unmapped"


# ─────────────────────────────────────────────────────────────────────────────
# Period helpers
# ─────────────────────────────────────────────────────────────────────────────

# Matches predictive_analytics_tool.py's _annual_pts/_quarterly_pts form
# allow-lists, which are extended with these exact tags (see that file).
# Distinct tags per granularity — not one shared "MANUAL" tag — so an annual
# manual entry can never accidentally satisfy the quarterly-only filter (or
# vice versa) purely because form-string membership is the primary gate there.
_MANUAL_FORM = {"annual": "MANUAL-A", "quarterly": "MANUAL-Q", "monthly": "MANUAL-M"}

# Metrics sox_financial_segments can carry, and the column each maps to —
# mirrors the actuals edgar_segments.py extracts from filed XBRL, so an
# uploaded segment breakdown lands in the same shape as a filed one.
_SEGMENT_METRIC_FIELD = {
    "Revenue": "revenue",
    "GrossProfit": "gross_profit",
    "OperatingIncome": "operating_income",
    "NetIncome": "net_income",
    "TotalAssets": "assets",
}
_SEGMENT_TYPES = {"geography", "business_segment"}


def _infer_granularity(start: Optional[date], end: Optional[date]) -> str:
    if not start or not end:
        return "annual"
    days = (end - start).days
    if days <= 40:
        return "monthly"
    if days <= 110:
        return "quarterly"
    return "annual"


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _coerce_date(v) -> Optional[date]:
    import pandas as pd
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        ts = pd.to_datetime(v)
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Excel / CSV
# ─────────────────────────────────────────────────────────────────────────────

def parse_spreadsheet(content: bytes, filename: str) -> dict:
    """Parse an uploaded .xlsx/.xls/.csv into normalized line items for review.
    Auto-detects the template layout (metric + period_end + value columns) vs.
    a wide trial-balance layout (line-item rows x month-end date columns)."""
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError("pandas not installed — run: pip install pandas openpyxl")

    suffix = (filename or "upload").rsplit(".", 1)[-1].lower()
    if suffix in ("xlsx", "xls"):
        df = pd.read_excel(io.BytesIO(content), engine="openpyxl")
    elif suffix == "csv":
        df = pd.read_csv(io.StringIO(content.decode("utf-8-sig")))
    else:
        raise ValueError(f"Unsupported file type '.{suffix}' — upload a .xlsx, .xls, or .csv file")

    orig_columns = list(df.columns)
    norm_columns = [str(c).strip().lower().replace(" ", "_") for c in orig_columns]
    df.columns = norm_columns

    def _col(*candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    metric_col   = _col("metric", "line_item", "account", "gl_account", "label", "item")
    start_col    = _col("period_start", "start", "start_date")
    end_col      = _col("period_end", "end", "end_date", "period", "date", "as_of")
    value_col    = _col("value", "amount")
    gran_col     = _col("granularity", "period_type", "frequency")
    seg_type_col = _col("segment_type", "segment_axis", "dimension")
    seg_name_col = _col("segment_name", "segment", "operating_unit", "geography", "business_segment")

    if not metric_col:
        raise ValueError(
            "Could not find a metric/line-item column. Expected a 'Metric' or "
            "'Line Item' column, plus either a 'Value' + 'Period End' column pair "
            "(one row per period) or one column per month-end date (trial balance layout)."
        )

    if value_col and end_col:
        line_items = _parse_template(df, metric_col, start_col, end_col, value_col, gran_col,
                                      seg_type_col, seg_name_col)
        fmt = "template"
    else:
        line_items = _parse_trial_balance(df, metric_col, orig_columns, norm_columns,
                                           seg_type_col, seg_name_col)
        fmt = "trial_balance"

    unmapped = sorted({li["raw_label"] for li in line_items if li["metric"] is None})
    return {
        "line_items": line_items,
        "format_detected": fmt,
        "unmapped_labels": unmapped,
        "filename": filename or "upload",
    }


def _segment_dims(row, seg_type_col, seg_name_col) -> tuple[Optional[str], Optional[str]]:
    """Read the optional segment dimension off a row. A column literally named
    'geography' or 'business_segment' implies its own segment_type even
    without a separate segment_type column; otherwise segment_type defaults
    to 'business_segment' (geography names are usually self-evident, but
    ambiguous free text isn't, so an explicit segment_type column is honored
    first when present)."""
    if not seg_name_col:
        return None, None
    name = str(row.get(seg_name_col, "")).strip()
    if not name or name.lower() in ("nan", "none", ""):
        return None, None
    if seg_type_col:
        seg_type = str(row.get(seg_type_col, "")).strip().lower()
    elif seg_name_col in _SEGMENT_TYPES:
        seg_type = seg_name_col
    else:
        seg_type = ""
    if seg_type not in _SEGMENT_TYPES:
        seg_type = "business_segment"
    return seg_type, name


def _parse_template(df, metric_col, start_col, end_col, value_col, gran_col,
                     seg_type_col=None, seg_name_col=None) -> list[dict]:
    items = []
    for _, row in df.iterrows():
        raw_label = str(row.get(metric_col, "")).strip()
        if not raw_label or raw_label.lower() in ("nan", "none", ""):
            continue
        end = _coerce_date(row.get(end_col))
        if not end:
            continue
        start = _coerce_date(row.get(start_col)) if start_col else None
        try:
            value = float(row.get(value_col))
        except (TypeError, ValueError):
            continue
        granularity = str(row.get(gran_col, "")).strip().lower() if gran_col else ""
        if granularity not in ("annual", "quarterly", "monthly"):
            granularity = _infer_granularity(start, end)
        metric, confidence = map_label_to_metric(raw_label)
        segment_type, segment_name = _segment_dims(row, seg_type_col, seg_name_col)
        items.append({
            "raw_label": raw_label, "metric": metric, "confidence": confidence,
            "period_start": start.isoformat() if start else None,
            "period_end": end.isoformat(), "value": value, "granularity": granularity,
            "segment_type": segment_type, "segment_name": segment_name,
        })
    return items


def _parse_trial_balance(df, metric_col, orig_columns, norm_columns,
                          seg_type_col=None, seg_name_col=None) -> list[dict]:
    """Wide layout: one row per line item, one column per month-end date."""
    date_cols = []
    for orig, norm in zip(orig_columns, norm_columns):
        if norm == metric_col or norm in (seg_type_col, seg_name_col):
            continue
        d = _coerce_date(orig)
        if d:
            date_cols.append((norm, d))

    items = []
    for _, row in df.iterrows():
        raw_label = str(row.get(metric_col, "")).strip()
        if not raw_label or raw_label.lower() in ("nan", "none", ""):
            continue
        metric, confidence = map_label_to_metric(raw_label)
        segment_type, segment_name = _segment_dims(row, seg_type_col, seg_name_col)
        for col, end in date_cols:
            try:
                value = float(row.get(col))
            except (TypeError, ValueError):
                continue
            items.append({
                "raw_label": raw_label, "metric": metric, "confidence": confidence,
                "period_start": _month_start(end).isoformat(),
                "period_end": _month_end(end).isoformat(),
                "value": value, "granularity": "monthly",
                "segment_type": segment_type, "segment_name": segment_name,
            })
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — PDF (financial-statement text/table extraction + LLM mapping)
# ─────────────────────────────────────────────────────────────────────────────

_PDF_MAPPING_SCHEMA = {
    "type": "object",
    "properties": {
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "raw_label":    {"type": "string"},
                    "metric":       {"type": ["string", "null"]},
                    "period_start": {"type": ["string", "null"]},
                    "period_end":   {"type": "string"},
                    "value":        {"type": "number"},
                    "granularity":  {"type": "string", "enum": ["annual", "quarterly", "monthly"]},
                },
                "required": ["raw_label", "period_end", "value", "granularity"],
            },
        },
    },
    "required": ["line_items"],
}


def parse_pdf(content: bytes, filename: str) -> dict:
    """Extract line items from an uploaded financial-statement PDF. Pulls
    text/tables with pdfplumber, then uses an LLM pass (claude_client) to map
    extracted labels onto the internal metric taxonomy and periods — a PDF has
    no machine-readable column semantics the way a spreadsheet does, so
    deterministic parsing alone can't reliably tell "Total Revenue" apart from
    a subtotal or a prior-year comparison column. Without a configured LLM,
    returns no line items but still surfaces the extracted text so a human can
    transcribe it into the spreadsheet template instead."""
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber not installed — run: pip install pdfplumber")

    table_chunks: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                rows = ["\t".join(c or "" for c in r) for r in table if any(r)]
                if rows:
                    table_chunks.append("\n".join(rows))

    raw_text = "\n\n".join(table_chunks)
    if not raw_text.strip():
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            raw_text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    if not raw_text.strip():
        raise ValueError("Could not extract any text or tables from this PDF")

    try:
        import claude_client
        llm_ready = claude_client.is_available()
    except ImportError:
        claude_client = None
        llm_ready = False

    line_items = _llm_map_pdf_text(raw_text, claude_client) if llm_ready else []
    unmapped = sorted({li["raw_label"] for li in line_items if li["metric"] is None})
    return {
        "line_items": line_items,
        "format_detected": "pdf",
        "unmapped_labels": unmapped,
        "filename": filename or "upload",
        "llm_used": llm_ready,
        "raw_text_preview": raw_text[:2000],
    }


def _llm_map_pdf_text(raw_text: str, claude_client) -> list[dict]:
    system = (
        "You extract line items from financial statement text and map each one onto "
        "a fixed internal metric taxonomy. Valid metric names: " + ", ".join(_ALL_METRICS) + ". "
        "If a line item does not clearly correspond to one of these, set metric to null "
        "rather than guessing. Dates must be ISO 8601 (YYYY-MM-DD). granularity is 'annual' "
        "for a full fiscal year, 'quarterly' for a 3-month period, 'monthly' for a 1-month period."
    )
    user = f"Extract every financial line item with its reporting period and value from this statement:\n\n{raw_text[:12000]}"
    result = claude_client.complete_json(
        system, user, schema=_PDF_MAPPING_SCHEMA,
        label="manual_financials_pdf_extract", effort="medium",
    )
    items = []
    for li in result.get("line_items", []):
        metric = li.get("metric")
        if metric not in _ALL_METRICS:
            metric = None
        items.append({
            "raw_label": li.get("raw_label", ""),
            "metric": metric,
            "confidence": "llm" if metric else "unmapped",
            "period_start": li.get("period_start"),
            "period_end": li.get("period_end"),
            "value": li.get("value"),
            "granularity": li.get("granularity") or "annual",
        })
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — API connectors (interface only; no provider wired up yet)
# ─────────────────────────────────────────────────────────────────────────────

class IngestionAdapter:
    """Common interface every ingestion source implements: produce the same
    line-item shape parse_spreadsheet/parse_pdf already return, so a future
    accounting-software connector (QuickBooks, Xero, ...) is a drop-in third
    adapter behind the same upload -> review -> commit flow. No provider is
    wired up yet — provider selection and auth are a separate piece of work."""

    name: str = "unknown"

    def parse(self, **kwargs) -> dict:
        """Return {"line_items": [...], "format_detected": str, "unmapped_labels": [...]}."""
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# Commit — persist reviewed line items
# ─────────────────────────────────────────────────────────────────────────────

def commit_line_items(company_id: int, line_items: list[dict]) -> dict:
    """Persist reviewed line items. Ordinary (non-segment) rows go through
    db.upsert_xbrl_series + db.upsert_manual_data_points, grouped by metric so
    each metric gets its own xbrl_metric_series row exactly like the live
    EDGAR write path does. Rows carrying a segment_type/segment_name (an
    operating-unit breakdown the user is supplying by hand, e.g. a private
    company with no XBRL filings) are instead routed to
    db.upsert_sox_segment — the same sox_financial_segments table
    edgar_segments.py populates from filed XBRL — tagged source='uploaded' so
    it's distinguishable from a filed breakdown (source='filed') at the point
    of use. Rows still missing a metric (unmapped in review) are skipped, not
    guessed."""
    import db

    by_metric: dict[str, list[dict]] = {}
    segment_rows: list[dict] = []
    skipped = 0
    for li in line_items:
        metric = li.get("metric")
        if not metric or metric not in XBRL_METRICS:
            skipped += 1
            continue
        if li.get("segment_type") and li.get("segment_name"):
            segment_rows.append(li)
        else:
            by_metric.setdefault(metric, []).append(li)

    saved = 0
    for metric, items in by_metric.items():
        series_id = db.upsert_xbrl_series(company_id, metric, xbrl_tag=None, unit="USD")
        if series_id is None:
            continue
        rows = []
        for li in items:
            granularity = li.get("granularity") or "annual"
            rows.append({
                "period_end":   li.get("period_end"),
                "period_start": li.get("period_start"),
                "value":        li.get("value"),
                "form":         _MANUAL_FORM.get(granularity, "MANUAL-A"),
                "granularity":  granularity,
            })
        db.upsert_manual_data_points(series_id, rows)
        saved += len(rows)

    segments_saved = _commit_segment_rows(company_id, segment_rows)

    return {
        "metrics_saved": len(by_metric), "data_points_saved": saved,
        "skipped_unmapped": skipped, "segments_saved": segments_saved,
    }


def _commit_segment_rows(company_id: int, segment_rows: list[dict]) -> int:
    """Fold segment-tagged line items into sox_financial_segments rows, one
    per (fiscal_year, segment_type, segment_name), combining whichever
    _SEGMENT_METRIC_FIELD metrics were supplied for that slice. revenue_pct is
    the row's share of the segment_type's total revenue *within this upload*
    — a same-type consolidated figure isn't guaranteed to exist here the way
    it does in edgar_segments.py's reconciliation, so this is a mix-share,
    not a reconciled-to-consolidated percentage."""
    import db

    by_key: dict[tuple, dict] = {}
    for li in segment_rows:
        field = _SEGMENT_METRIC_FIELD.get(li["metric"])
        if not field:
            continue
        period_end = li.get("period_end") or ""
        # "FY{year}" — matches the convention every other fiscal_year-keyed
        # lookup in this codebase uses (see edgar_segments.py's fetch_segments
        # for the full rationale); a bare year here would silently never
        # match a SOX-scoping lookup for the same fiscal year.
        fiscal_year = f"FY{period_end[:4]}" if period_end else None
        key = (fiscal_year, li["segment_type"], li["segment_name"])
        by_key.setdefault(key, {
            "fiscal_year": fiscal_year,
            "segment_type": li["segment_type"],
            "segment_name": li["segment_name"],
            "source": "uploaded",
        })[field] = li.get("value")

    totals: dict[tuple, float] = {}
    for (fiscal_year, segment_type, _name), seg in by_key.items():
        rev = seg.get("revenue")
        if rev is not None:
            totals[(fiscal_year, segment_type)] = totals.get((fiscal_year, segment_type), 0.0) + rev

    saved = 0
    for (fiscal_year, segment_type, _name), seg in by_key.items():
        total = totals.get((fiscal_year, segment_type))
        rev = seg.get("revenue")
        if rev is not None and total:
            seg["revenue_pct"] = round(rev / total * 100, 2)
        db.upsert_sox_segment(company_id, None, seg)
        saved += 1
    return saved
