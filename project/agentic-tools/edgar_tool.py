#!/usr/bin/env python3
"""
EDGAR Financial Data Tool

Fetches CIK/SIC, 10-K/10-Q/8-K/DEF 14A filings from SEC EDGAR for a public
company and writes financials.json, proxy.json, and risks.json.

Usage:
    python edgar_tool.py AAPL
    python edgar_tool.py MSFT --output-dir ./output --max-filings 3
"""

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import warnings

import requests

try:
    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    _BS4 = True
except ImportError:
    BeautifulSoup: Any = None
    _BS4 = False

# ── Constants ─────────────────────────────────────────────────────────────────

EDGAR_BASE = "https://www.sec.gov"
DATA_BASE  = "https://data.sec.gov"

HEADERS = {
    "User-Agent": "EDGARFinancialTool/1.0 (research@example.com)",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json, text/html, */*",
}

# Five years back from today
CUTOFF = datetime.now(timezone.utc) - timedelta(days=5 * 365)

# XBRL tags to harvest (metric_name → possible tag names, tried in order)
XBRL_METRICS = {
    "Revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "GrossProfit": ["GrossProfit"],
    "OperatingIncome": ["OperatingIncomeLoss"],
    "NetIncome": ["NetIncomeLoss"],
    "EPS_Basic": ["EarningsPerShareBasic"],
    "EPS_Diluted": ["EarningsPerShareDiluted"],
    "TotalAssets": ["Assets"],
    "CurrentAssets": ["AssetsCurrent"],
    "CurrentLiabilities": ["LiabilitiesCurrent"],
    "TotalLiabilities": ["Liabilities"],
    "StockholdersEquity": ["StockholdersEquity", "StockholdersEquityAttributableToParent"],
    "RetainedEarnings": ["RetainedEarningsAccumulatedDeficit"],
    "Cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "LongTermDebt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "OperatingCashFlow": ["NetCashProvidedByUsedInOperatingActivities"],
    "CapEx": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "Depreciation": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
    "SharesOutstanding": ["CommonStockSharesOutstanding"],
    "Dividends": ["DividendsCommonStockCash", "PaymentsOfDividendsCommonStock"],
    "ResearchAndDevelopment": ["ResearchAndDevelopmentExpense"],
    "IncomeTaxExpense": ["IncomeTaxExpenseBenefit"],
    "InterestExpense": ["InterestExpense"],
    "Inventory": ["InventoryNet"],
    "AccountsReceivable": ["AccountsReceivableNetCurrent"],
}

# ── Utilities ─────────────────────────────────────────────────────────────────

def _sleep():
    """Respect SEC EDGAR's 10 req/s guidance."""
    time.sleep(0.12)


def _get(url: str, **kw) -> requests.Response:
    _sleep()
    resp = requests.get(url, headers=HEADERS, timeout=30, **kw)
    resp.raise_for_status()
    return resp


def _get_safe(url: str, **kw) -> Optional[requests.Response]:
    try:
        return _get(url, **kw)
    except Exception:
        return None


def _strip_html(raw: str) -> str:
    """Return plain text from an HTML string, with paragraph structure preserved.

    get_text(separator="\n") inserts a newline at *every* tag boundary — DEF
    14A/10-K filings wrap running prose in many inline <span>/<b>/<font> tags
    for formatting, so a single flowing paragraph fragmented into a dozen
    inline tags came out as a dozen separate one-line "paragraphs" downstream
    (each looking like a truncated, disconnected bullet on screen). Only
    block-level tags get a paragraph break; everything else is joined with
    spaces so sentences stay whole.
    """
    if _BS4:
        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style", "table"]):
            tag.decompose()
        for tag in soup.find_all(["p", "div", "tr", "li", "br",
                                   "h1", "h2", "h3", "h4", "h5", "h6"]):
            tag.append("\n\n")
        text = soup.get_text(separator=" ")
    else:
        text = re.sub(r"<[^>]+>", " ", raw)
        text = html.unescape(text)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(ln.strip() for ln in text.split("\n")).strip()


def _extract_section(
    text: str,
    start_patterns: list[str],
    stop_patterns: list[str],
    max_chars: int = 40_000,
) -> str:
    """Find the first matching start pattern and return up to the first stop."""
    tl = text.lower()
    start = None
    for pat in start_patterns:
        m = re.search(pat, tl, re.IGNORECASE)
        if m:
            start = m.start()
            break
    if start is None:
        return ""

    end = len(text)
    search_region = tl[start + 80:]
    for pat in stop_patterns:
        m = re.search(pat, search_region, re.IGNORECASE)
        if m:
            candidate = start + 80 + m.start()
            end = min(end, candidate)

    snippet = text[start:end]
    return snippet[:max_chars]


# ── EDGAR lookup ───────────────────────────────────────────────────────────────

def get_company_info(ticker: str) -> tuple[dict, dict]:
    """
    Return (company_metadata, raw_submissions).
    Raises ValueError if ticker is not found.
    """
    print(f"  [1/6] Resolving CIK for {ticker.upper()} …")
    resp = _get(f"{EDGAR_BASE}/files/company_tickers.json")
    ticker_map = resp.json()

    ticker_up = ticker.upper()
    cik_int = None
    name_hint = ""
    for entry in ticker_map.values():
        if entry.get("ticker", "").upper() == ticker_up:
            cik_int = entry["cik_str"]
            name_hint = entry.get("title", "")
            break

    if cik_int is None:
        raise ValueError(
            f"Ticker '{ticker}' not found in EDGAR company_tickers.json.\n"
            "Check spelling or try the company's legal name."
        )

    cik_padded = str(cik_int).zfill(10)

    print(f"  [2/6] Fetching submissions for CIK {cik_padded} …")
    sub = _get(f"{DATA_BASE}/submissions/CIK{cik_padded}.json").json()

    meta = {
        "ticker": ticker_up,
        "cik": cik_padded,
        "cik_plain": str(cik_int),
        "company_name": sub.get("name", name_hint),
        "sic": sub.get("sic", ""),
        "sic_description": sub.get("sicDescription", ""),
        "entity_type": sub.get("entityType", ""),
        "state_of_incorporation": sub.get("stateOfIncorporation", ""),
        "fiscal_year_end": sub.get("fiscalYearEnd", ""),
        "ein": sub.get("ein", ""),
        "phone": sub.get("phone", ""),
        "business_address": sub.get("addresses", {}).get("business", {}),
        "exchanges": sub.get("exchanges", []),
        "former_names": sub.get("formerNames", []),
    }

    # Merge any additional filing pages (companies with >1000 filings have
    # continuation references in sub["filings"]["files"])
    _merge_extra_filings(sub, cik_padded)

    return meta, sub


def _merge_extra_filings(sub: dict, cik_padded: str):
    """Append older filings from pagination links into sub['filings']['recent']."""
    extra_files = sub.get("filings", {}).get("files", [])
    recent = sub["filings"]["recent"]

    for file_ref in extra_files:
        fname = file_ref.get("name", "")
        if not fname:
            continue
        url = f"{DATA_BASE}/submissions/{fname}"
        r = _get_safe(url)
        if r is None:
            continue
        data = r.json()
        for key, vals in data.items():
            if isinstance(vals, list) and key in recent:
                recent[key].extend(vals)


def parse_filings(sub: dict, form_types: set[str]) -> dict[str, list[dict]]:
    """Return {form_type: [filing_dict, …]} filtered to the last 5 years."""
    recent = sub.get("filings", {}).get("recent", {})
    forms  = recent.get("form", [])
    dates  = recent.get("filingDate", [])
    accnos = recent.get("accessionNumber", [])
    docs   = recent.get("primaryDocument", [])
    items  = recent.get("items", [])  # present on some filings (8-K items)
    descs  = recent.get("primaryDocDescription", [])

    cutoff_str = CUTOFF.strftime("%Y-%m-%d")
    result: dict[str, list[dict]] = {ft: [] for ft in form_types}

    for i, form in enumerate(forms):
        if form not in form_types:
            continue
        date = dates[i] if i < len(dates) else ""
        if date < cutoff_str:
            continue
        result[form].append({
            "form": form,
            "date": date,
            "accession_number": accnos[i] if i < len(accnos) else "",
            "primary_document": docs[i] if i < len(docs) else "",
            "description": descs[i] if i < len(descs) else "",
            "items": items[i] if i < len(items) else "",
        })

    for ft in result:
        result[ft].sort(key=lambda x: x["date"], reverse=True)

    return result


# ── Document fetching ──────────────────────────────────────────────────────────

def _filing_index(cik: str, accession_number: str) -> list[dict]:
    """Return the document list from a filing's JSON index.

    EDGAR serves the machine-readable directory listing at plain
    `index.json` in the filing's own accession folder — `{accession-with-
    dashes}-index.json` (what this used to build) 404s; that dashed-name
    convention only exists for the *human* `-index.htm` page. This was
    previously a silent no-op for every caller: fetch_filing_text()'s
    _best_document() falls through to the already-known primary_document
    when items is empty, which happens to be right most of the time, so the
    bug never surfaced — until a caller needed a *different* file in the
    same directory (edgar_segments.py, locating the XBRL instance doc),
    where there's no equivalent fallback.
    """
    cik_int = int(cik)
    acc_clean = accession_number.replace("-", "")
    url = f"{EDGAR_BASE}/Archives/edgar/data/{cik_int}/{acc_clean}/index.json"
    r = _get_safe(url)
    if r is None:
        return []
    data = r.json()
    return data.get("directory", {}).get("item", [])


def _best_document(items: list[dict], primary_doc: str) -> str:
    """
    Pick the best HTML/text document from a filing index.
    Prefers the primary document; falls back to first .htm file.
    """
    names = [it.get("name", "") for it in items]
    # Exact match first
    if primary_doc in names:
        return primary_doc
    # Case-insensitive
    for name in names:
        if name.lower() == primary_doc.lower():
            return name
    # First .htm document
    for name in names:
        if name.lower().endswith((".htm", ".html")):
            return name
    return primary_doc


def fetch_filing_text(cik: str, filing: dict) -> str:
    """Download a filing and return its plain text (empty string on failure)."""
    acc = filing["accession_number"]
    primary = filing["primary_document"]
    if not acc or not primary:
        return ""

    items = _filing_index(cik, acc)
    doc_name = _best_document(items, primary)

    cik_int = int(cik)
    acc_clean = acc.replace("-", "")
    url = f"{EDGAR_BASE}/Archives/edgar/data/{cik_int}/{acc_clean}/{doc_name}"

    r = _get_safe(url)
    if r is None:
        return ""

    ct = r.headers.get("Content-Type", "")
    raw = r.text
    if "html" in ct or raw.lstrip().startswith("<") or doc_name.lower().endswith((".htm", ".html")):
        return _strip_html(raw)
    return raw


# ── XBRL financial data ────────────────────────────────────────────────────────

def fetch_xbrl_facts(cik: str) -> dict:
    """
    Pull structured financial metrics from the XBRL company facts endpoint.
    Returns a dict keyed by metric name.

    Zero-pads cik to SEC's required 10 digits regardless of what the caller
    passed in — get_company_info() already returns a padded CIK for a fresh
    live lookup, but a CIK read back from companies.cik after a DB round
    trip is not guaranteed to still have its leading zeros (confirmed: this
    silently broke Board Intelligence's peer-benchmarking subject-history
    line, which reads the saved CIK via db.get_sic_peers() — the API 404s on
    an unpadded CIK, _get_safe swallows that into a plain None, and this
    function's own `if r is None: return {}` makes the failure indistinguishable
    from "no data," never raising or logging anything).
    """
    cik = str(cik).strip().zfill(10)
    url = f"{DATA_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
    r = _get_safe(url)
    if r is None:
        return {}

    facts    = r.json()
    us_gaap  = facts.get("facts", {}).get("us-gaap", {})
    cutoff_s = CUTOFF.strftime("%Y-%m-%d")
    result   = {}

    for metric, tags in XBRL_METRICS.items():
        for tag in tags:
            if tag not in us_gaap:
                continue
            concept = us_gaap[tag]
            units   = concept.get("units", {})
            # Try USD, then shares, then USD/shares
            unit_vals = units.get("USD") or units.get("shares") or units.get("USD/shares") or []

            if not unit_vals:
                continue

            filtered = [
                e for e in unit_vals
                if e.get("filed", "0") >= cutoff_s
                and e.get("form") in {"10-K", "10-Q", "20-F", "10-K/A", "10-Q/A"}
            ]
            if not filtered:
                continue

            filtered.sort(key=lambda e: e.get("end", ""), reverse=True)

            result[metric] = {
                "tag": tag,
                "label": concept.get("label", tag),
                "description": concept.get("description", "")[:300],
                "unit": "USD" if "USD" in units else ("shares" if "shares" in units else ""),
                "data_points": filtered[:48],   # enough for 5yr standalone+cumulative mix
            }
            break   # found a working tag for this metric

    return result


# ── Section extractors ─────────────────────────────────────────────────────────

def extract_risk_factors(text: str) -> str:
    return _extract_section(
        text,
        start_patterns=[
            r"item\s+1a[\.\s:—]+risk\s+factors",
            r"risk\s+factors",
        ],
        stop_patterns=[
            r"item\s+1b[\.\s:—]+",
            r"item\s+2[\.\s:—]+",
            r"unresolved\s+staff\s+comments",
            r"properties",
        ],
        max_chars=50_000,
    )


def extract_proxy_sections(text: str) -> dict:
    sections = {}

    sections["executive_compensation"] = _extract_section(
        text,
        start_patterns=[
            r"compensation\s+discussion\s+and\s+analysis",
            r"executive\s+compensation",
            r"named\s+executive\s+officer",
        ],
        stop_patterns=[
            r"director\s+compensation",
            r"security\s+ownership",
            r"audit\s+committee\s+report",
            r"certain\s+relationships",
        ],
        max_chars=30_000,
    )

    sections["board_of_directors"] = _extract_section(
        text,
        start_patterns=[
            r"proposal\s+no\.\s*1[\s:—]+election",
            r"election\s+of\s+directors",
            r"director\s+nominees",
            r"board\s+of\s+directors",
        ],
        stop_patterns=[
            r"proposal\s+no\.\s*2",
            r"executive\s+compensation",
            r"audit\s+committee",
        ],
        max_chars=20_000,
    )

    sections["say_on_pay"] = _extract_section(
        text,
        start_patterns=[
            r"say.on.pay",
            r"advisory\s+vote.*compensation",
            r"proposal.*advisory.*compensation",
        ],
        stop_patterns=[
            r"proposal\s+no\.\s*[34]",
            r"ratification",
            r"director\s+compensation",
        ],
        max_chars=10_000,
    )

    sections["shareholder_proposals"] = _extract_section(
        text,
        start_patterns=[
            r"stockholder\s+proposal",
            r"shareholder\s+proposal",
        ],
        stop_patterns=[
            r"additional\s+information",
            r"other\s+matters",
            r"signature",
            r"general\s+information",
        ],
        max_chars=20_000,
    )

    return {k: v for k, v in sections.items() if v.strip()}


# ── 8-K item descriptions ──────────────────────────────────────────────────────

_8K_ITEMS = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.04": "Mine Safety – Reporting of Shutdowns and Patterns of Violations",
    "1.05": "Material Cybersecurity Incidents",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate or Increase a Direct Financial Obligation",
    "2.05": "Cost Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy Continued Listing Rules",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure/Election of Directors or Principal Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "5.04": "Temporary Suspension of Trading Under Registrant's Employee Benefit Plans",
    "5.05": "Amendments to the Registrant's Code of Ethics",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "5.08": "Shareholder Director Nominations",
    "6.01": "ABS Informational and Computational Material",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}


def annotate_8k(filing: dict) -> dict:
    """Add human-readable item descriptions to an 8-K filing dict."""
    raw_items = filing.get("items", "")
    item_list = [i.strip() for i in re.split(r"[,\s]+", raw_items) if i.strip()]
    filing["item_descriptions"] = {
        item: _8K_ITEMS.get(item, "Unknown item") for item in item_list
    }
    return filing


# Item codes worth the cost of fetching + classifying the actual filing body —
# these are the ones that can represent a genuine change in risk exposure
# (M&A, divestiture/exit, distress, control, or accounting-integrity events),
# as opposed to routine/procedural items (Reg FD disclosures, exhibit lists,
# shareholder-vote mechanics) that are common, low-signal, and not worth an
# LLM call every time. This is a judgment call, not an SEC-defined category —
# revisit if a real customer's risk model needs a wider or narrower net.
_MATERIAL_8K_ITEMS = {
    "1.01",  # Entry into a Material Definitive Agreement — often the FIRST signal
             # of an announced-but-not-yet-closed acquisition/divestiture
    "1.03",  # Bankruptcy or Receivership
    "1.05",  # Material Cybersecurity Incidents
    "2.01",  # Completion of Acquisition or Disposition of Assets
    "2.05",  # Costs Associated with Exit or Disposal Activities — e.g. closing/selling a facility
    "2.06",  # Material Impairments
    "4.01",  # Change in Registrant's Certifying Accountant
    "4.02",  # Non-Reliance on Previously Issued Financial Statements (restatement)
    "5.01",  # Changes in Control of Registrant
}


def has_material_item(filing: dict) -> bool:
    """True if this 8-K's item codes intersect _MATERIAL_8K_ITEMS."""
    raw_items = filing.get("items", "")
    item_list = {i.strip() for i in re.split(r"[,\s]+", raw_items) if i.strip()}
    return bool(item_list & _MATERIAL_8K_ITEMS)


_8K_CLASSIFY_SYSTEM = """You are a risk analyst reading a single SEC Form 8-K filing. \
Extract exactly what the filing states — do not infer or speculate beyond the text. \
If the filing does not actually describe a corporate action (acquisition, divestiture, \
restructuring, bankruptcy, impairment, accounting restatement, or change of control), \
set is_corporate_action to false and leave the other fields null. Numbers/dates must be \
copied verbatim from the text, not estimated.

If is_corporate_action is true, also write suggested_risk_note: one or two plain-English \
sentences telling an internal auditor what to actually go check in the risk register or \
scenario models as a result — not a restatement of the summary. Be specific to what changed \
(e.g. "This closes the previously-flagged pending acquisition — confirm integration risk and \
purchase-price/goodwill exposure are reflected" or "This divestiture removes a segment — the \
liquidity/covenant runway model's revenue base should be re-checked"). This is a suggestion for \
a human to act on, not an instruction you're executing — never claim the register was updated."""

_8K_CLASSIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "is_corporate_action": {"type": "boolean"},
        "action_type": {
            "type": "string",
            "enum": ["acquisition", "divestiture", "restructuring", "bankruptcy",
                     "impairment", "auditor_change", "restatement", "change_of_control",
                     "cybersecurity_incident", "other", "none"],
        },
        "counterparty": {"type": ["string", "null"]},
        "assets_or_business_description": {"type": ["string", "null"]},
        "consideration": {"type": ["string", "null"]},
        "expected_close_or_effective_date": {"type": ["string", "null"]},
        "rationale": {"type": ["string", "null"]},
        "summary": {"type": "string"},
        "suggested_risk_note": {"type": ["string", "null"]},
    },
    "required": ["is_corporate_action", "action_type", "summary"],
}


def classify_8k_event(item_descriptions: dict, filing_text: str) -> Optional[dict]:
    """
    LLM-based structured extraction of a material 8-K's actual content —
    counterparty, consideration, what was acquired/sold, stated rationale.
    Returns None on any failure (missing API key, empty text, malformed
    response) rather than raising — this must never break the ingestion run,
    matching every other best-effort path in this codebase.
    """
    if not filing_text or not filing_text.strip():
        return None
    try:
        import claude_client
    except ImportError:
        return None
    if not claude_client.is_available():
        return None
    try:
        items_line = "; ".join(f"Item {k}: {v}" for k, v in (item_descriptions or {}).items())
        user = (
            f"Filed items: {items_line}\n\n"
            f"Filing text (truncated to 20,000 chars):\n{filing_text[:20_000]}"
        )
        result = claude_client.complete_json(
            _8K_CLASSIFY_SYSTEM, user, _8K_CLASSIFY_SCHEMA,
            label="edgar_8k_classify", effort="medium", max_tokens=1000,
        )
        return result
    except Exception:
        return None


# ── SIC peer lookup ────────────────────────────────────────────────────────────

def _cik_to_ticker_map() -> dict[str, str]:
    """Return {cik_str: ticker} from EDGAR company_tickers.json."""
    resp = _get_safe(f"{EDGAR_BASE}/files/company_tickers.json")
    if resp is None:
        return {}
    return {str(v["cik_str"]): v["ticker"] for v in resp.json().values()}


def fetch_sic_peers(sic: str, max_peers: int = 20) -> list[dict]:
    """
    Return companies sharing the given SIC code by scraping EDGAR's browse page.
    Each entry: {cik, cik_plain, company_name, state, sic, ticker}.
    """
    url = (
        f"{EDGAR_BASE}/cgi-bin/browse-edgar"
        f"?action=getcompany&SIC={sic}&type=10-K"
        f"&dateb=&owner=include&count={min(max_peers + 10, 100)}&search_text="
    )
    r = _get_safe(url)
    if r is None:
        return []

    peers = []
    if _BS4:
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table", class_="tableFile2")
        if table:
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                cik_link = cells[0].find("a")
                if not cik_link:
                    continue
                cik_int = re.sub(r"\D", "", cik_link.get_text(strip=True))
                name = cells[1].get_text(strip=True)
                state = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                peers.append({
                    "cik": cik_int.zfill(10),
                    "cik_plain": cik_int,
                    "company_name": name,
                    "state": state,
                })
    else:
        for m in re.finditer(
            r'CIK=(\d+)[^"]*">\s*\1\s*</a>.*?<a[^>]+>([^<]+)</a>.*?<td[^>]*>([^<]*)</td>',
            r.text, re.DOTALL,
        ):
            peers.append({
                "cik": m.group(1).zfill(10),
                "cik_plain": m.group(1),
                "company_name": m.group(2).strip(),
                "state": m.group(3).strip(),
            })

    ticker_map = _cik_to_ticker_map()
    for p in peers:
        p["ticker"] = ticker_map.get(p["cik_plain"], "")
        p["sic"] = sic

    return peers[:max_peers]


def summarize_xbrl_annual(xbrl: dict) -> dict:
    """
    Collapse full XBRL time-series to the single most-recent annual (10-K) value
    per metric. Returns {metric: {value, period_end, unit}}.
    """
    out = {}
    for metric, data in xbrl.items():
        annual = [
            p for p in data.get("data_points", [])
            if p.get("form") in {"10-K", "20-F", "10-K/A"}
        ]
        if not annual:
            continue
        best = max(annual, key=lambda p: p.get("end", ""))
        out[metric] = {
            "value": best.get("val"),
            "period_end": best.get("end"),
            "unit": data.get("unit"),
        }
    return out


# ── Main orchestration ─────────────────────────────────────────────────────────

def run(ticker: str, output_dir: Path, max_filings: int):
    # 1. Company lookup
    meta, sub = get_company_info(ticker)
    cik = meta["cik"]
    print(
        f"       {meta['company_name']}  "
        f"CIK={meta['cik_plain']}  "
        f"SIC={meta['sic']} ({meta['sic_description']})"
    )

    # 2. Parse all relevant filings
    print("  [3/6] Cataloguing filings (10-K, 10-Q, 8-K, DEF 14A) …")
    filing_map = parse_filings(sub, {"10-K", "10-Q", "8-K", "DEF 14A"})
    for ft, lst in filing_map.items():
        print(f"         {ft}: {len(lst)} filings in range")

    # 3. XBRL financial facts
    print("  [4/6] Fetching XBRL company facts …")
    xbrl = fetch_xbrl_facts(cik)
    print(f"         Extracted {len(xbrl)} financial metric series")

    # 4. Risk factors (from latest 10-Ks)
    print("  [5/6] Extracting risk factors from 10-K filings …")
    risk_entries = []
    for filing in filing_map["10-K"][:max_filings]:
        print(f"         10-K {filing['date']} …", end="", flush=True)
        text = fetch_filing_text(cik, filing)
        risks = extract_risk_factors(text) if text else ""
        entry = {
            "filing_date": filing["date"],
            "accession_number": filing["accession_number"],
            "edgar_url": (
                f"{EDGAR_BASE}/Archives/edgar/data/{int(cik)}"
                f"/{filing['accession_number'].replace('-','')}"
                f"/{filing['primary_document']}"
            ),
            "risk_factors": risks,
            "word_count": len(risks.split()) if risks else 0,
        }
        risk_entries.append(entry)
        print(f" {entry['word_count']:,} words extracted")

    # 5. Proxy data (from latest DEF 14As)
    print("  [6/6] Extracting proxy data from DEF 14A filings …")
    proxy_entries = []
    for filing in filing_map["DEF 14A"][:max_filings]:
        print(f"         DEF 14A {filing['date']} …", end="", flush=True)
        text = fetch_filing_text(cik, filing)
        sections = extract_proxy_sections(text) if text else {}
        entry = {
            "filing_date": filing["date"],
            "accession_number": filing["accession_number"],
            "edgar_url": (
                f"{EDGAR_BASE}/Archives/edgar/data/{int(cik)}"
                f"/{filing['accession_number'].replace('-','')}"
                f"/{filing['primary_document']}"
            ),
            "sections": sections,
        }
        proxy_entries.append(entry)
        print(f" {len(sections)} sections extracted")

    # ── Build output documents ────────────────────────────────────────────────

    now_iso = datetime.now(timezone.utc).isoformat()

    financials = {
        "generated_at": now_iso,
        "period_from": CUTOFF.strftime("%Y-%m-%d"),
        "period_to": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "company": meta,
        "filings_index": {
            "10-K":    [{"date": f["date"], "accession_number": f["accession_number"]} for f in filing_map["10-K"]],
            "10-Q":    [{"date": f["date"], "accession_number": f["accession_number"]} for f in filing_map["10-Q"]],
            "8-K":     [{"date": f["date"], "accession_number": f["accession_number"], "items": f["items"]} for f in filing_map["8-K"]],
            "DEF 14A": [{"date": f["date"], "accession_number": f["accession_number"]} for f in filing_map["DEF 14A"]],
        },
        "current_reports_8k": [annotate_8k(dict(f)) for f in filing_map["8-K"]],
        "xbrl_metrics": xbrl,
    }

    proxy = {
        "generated_at": now_iso,
        "company": meta["company_name"],
        "ticker": ticker.upper(),
        "cik": meta["cik_plain"],
        "proxy_statements": proxy_entries,
    }

    risks = {
        "generated_at": now_iso,
        "company": meta["company_name"],
        "ticker": ticker.upper(),
        "cik": meta["cik_plain"],
        "risk_filings": risk_entries,
    }

    # ── Write files ───────────────────────────────────────────────────────────

    output_dir.mkdir(parents=True, exist_ok=True)

    _write_json(output_dir / "financials.json", financials)
    _write_json(output_dir / "proxy.json", proxy)
    _write_json(output_dir / "risks.json", risks)

    print("\n  Output files written:")
    for name in ("financials.json", "proxy.json", "risks.json"):
        p = output_dir / name
        size_kb = p.stat().st_size / 1024
        print(f"    {p}  ({size_kb:.1f} KB)")


def _write_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
    print(f"  ✓ {path.name}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch SEC EDGAR data for a public company.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("ticker", help="NYSE/NASDAQ ticker symbol, e.g. AAPL")
    parser.add_argument(
        "--output-dir", "-o", default=".",
        help="Directory for output JSON files (default: current directory)",
    )
    parser.add_argument(
        "--max-filings", "-n", type=int, default=5,
        help="Max 10-K and DEF 14A filings to download for text extraction (default: 5)",
    )
    args = parser.parse_args()

    try:
        run(
            ticker=args.ticker,
            output_dir=Path(args.output_dir),
            max_filings=args.max_filings,
        )
        print("\nDone.")
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
