#!/usr/bin/env python3
"""
Governance & regulator intelligence -> risk-taxonomy evidence.

Three sources, all turned into the same evidence items risk_taxonomy scores:

  1. DEF 14A board KRIs      director tenure / age distribution, refreshment
                             policy, auditor tenure         -> TX-GOV, TX-AUD
  2. SEC staff comment       UPLOAD (staff) / CORRESP (company) letters from
     letters                 the EDGAR submissions index    -> TX-REG (+ topic
                             categories when the body is available)
  3. (8-K items live in event_risk_map.py)

What the rules actually require (context for the thresholds below — these are
disclosure / investor-policy signals, NOT legal limits):

  * No SEC / NYSE / Nasdaq rule mandates director term limits or rotation.
    Reg S-K Item 401 requires term-of-office and experience disclosure;
    proxy advisers and institutions flag long tenure (commonly ~9-10+ years)
    and the UK Code presumes independence is lost after 9. Refreshment and
    mandatory-retirement policies are voluntary, so the KRI is whether the
    board's tenure profile is stale AND whether any refreshment policy exists.
  * Audit PARTNER rotation IS mandatory (SOX §203 / Reg S-X Rule 2-01(c)(6):
    lead and concurring partner after 5 years, 5-year time-out; other
    significant partners after 7, 2-year time-out). Audit FIRM rotation is
    not required by the SEC. The proxy rarely names the partner, so this
    module can only report firm tenure; it never claims partner compliance.

Parsing the proxy is best-effort regex over free text. Every KRI therefore
carries `n_parsed` and a `confidence` so a low-parse result is shown as such
and never silently treated as a clean bill of health.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Callable, Optional

import risk_taxonomy as rt

# ── thresholds (documented above; investor-policy, not legal) ──────────────
TENURE_LONG_YEARS = 10
TENURE_VERY_LONG_YEARS = 15
AVG_TENURE_ELEVATED = 9.0
AVG_TENURE_HIGH = 12.0
SHARE_LONG_ELEVATED = 0.5
MIN_PARSED_FOR_ASSESSMENT = 3
AUDITOR_TENURE_LONG = 20


def _year(v) -> Optional[int]:
    try:
        y = int(v)
        return y if 1950 <= y <= date.today().year else None
    except (TypeError, ValueError):
        return None


def parse_director_tenures(text: str, today: Optional[date] = None) -> list[int]:
    """Years of board service, one per director we could identify.

    Recognises the common proxy phrasings:
      "Director since 2012" / "Director Since: 2012" / "Director since March 2012"
      "has served as a director since 2012" / "served on our Board since 2012"
    """
    today = today or date.today()
    t = re.sub(r"\s+", " ", text or "")
    years: list[int] = []
    pats = [
        r"director\s+since\s*[:\-–]?\s*(?:[A-Za-z]+\s+(?:\d{1,2},\s*)?)?((?:19|20)\d{2})",
        r"(?:served|serving|has served|have served)\s+(?:as\s+(?:a\s+)?(?:member of our\s+board|director)|on\s+(?:our|the)\s+board(?:\s+of\s+directors)?)[^.]{0,40}?since\s+(?:[A-Za-z]+\s+(?:\d{1,2},\s*)?)?((?:19|20)\d{2})",
        r"joined\s+(?:our|the)\s+board[^.]{0,30}?(?:in|since)\s+(?:[A-Za-z]+\s+(?:\d{1,2},\s*)?)?((?:19|20)\d{2})",
    ]
    seen_spans: list[tuple[int, int, int]] = []   # (start, end, year)
    for pat in pats:
        for m in re.finditer(pat, t, flags=re.I):
            y = _year(m.group(1))
            # Two phrasings of the SAME bio ("Director since 2008 ... has served
            # since 2008") sit close together with the same year; two different
            # directors with a coincident start year are far enough apart.
            if y and not any(abs(m.start() - a) < 60 and yr == y for a, _, yr in seen_spans):
                years.append(today.year - y)
                seen_spans.append((m.start(), m.end(), y))
    return years


def parse_director_ages(text: str) -> list[int]:
    t = re.sub(r"\s+", " ", text or "")
    ages = []
    for m in re.finditer(r"(?:\bage\b\s*[:\-–]?\s*|,\s*age\s+)(\d{2})\b", t, flags=re.I):
        a = int(m.group(1))
        if 30 <= a <= 95:
            ages.append(a)
    return ages


def detect_refreshment_policy(text: str) -> dict:
    t = (text or "").lower()
    return {
        "mandatory_retirement_age": bool(re.search(r"mandatory retirement|retirement age|retire (from the board )?(at|upon reaching) (the )?age", t)),
        "term_limit": bool(re.search(r"term limits?|maximum (term|tenure|years of service)|tenure limit", t)),
        "board_refreshment": bool(re.search(r"board (refreshment|renewal|succession)|refresh(ing)? the board", t)),
        "annual_board_evaluation": bool(re.search(r"(annual|yearly) (board|self|performance)[ -]?(evaluation|assessment)", t)),
    }


def parse_auditor_tenure(text: str, today: Optional[date] = None) -> Optional[dict]:
    """Firm tenure from "has served as the Company's auditor since 1994"-style text."""
    today = today or date.today()
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(
        r"(?:has\s+(?:served|audited|been)|have\s+served|served)\s+(?:as\s+)?(?:the\s+)?(?:company'?s?\s+|our\s+)?"
        r"(?:independent\s+registered\s+public\s+accounting\s+firm|auditors?|independent\s+auditors?)[^.]{0,80}?"
        r"since\s+(?:[A-Za-z]+\s+)?((?:19|20)\d{2})", t, flags=re.I)
    if not m:
        m = re.search(r"(?:auditor|independent registered public accounting firm)[^.]{0,60}since\s+((?:19|20)\d{2})", t, flags=re.I)
    if not m:
        return None
    y = _year(m.group(1))
    return {"since": y, "years": today.year - y} if y else None


def compute_kris(sections: dict, today: Optional[date] = None) -> dict:
    """KRIs from the extracted DEF 14A sections ({name: text})."""
    board = (sections or {}).get("board_of_directors", "") or ""
    blob = " ".join(str(v) for v in (sections or {}).values())
    tenures = parse_director_tenures(board or blob, today)
    ages = parse_director_ages(board or blob)
    n = len(tenures)
    kris: dict = {
        "n_tenure_parsed": n,
        "n_age_parsed": len(ages),
        "confidence": "none" if n == 0 else "low" if n < 5 else "medium" if n < 8 else "high",
        "policies": detect_refreshment_policy(blob),
        "auditor": parse_auditor_tenure(blob, today),
    }
    if n:
        kris.update({
            "avg_tenure_years": round(sum(tenures) / n, 1),
            "max_tenure_years": max(tenures),
            "share_over_10y": round(sum(1 for y in tenures if y >= TENURE_LONG_YEARS) / n, 2),
            "share_over_15y": round(sum(1 for y in tenures if y >= TENURE_VERY_LONG_YEARS) / n, 2),
        })
    if ages:
        kris["avg_age"] = round(sum(ages) / len(ages), 1)
        kris["max_age"] = max(ages)
    return kris


def kris_to_evidence(kris: Optional[dict]) -> dict:
    """KRIs -> {taxonomy_id: [evidence]}. Below MIN_PARSED_FOR_ASSESSMENT
    directors parsed, no governance evidence is produced (so the category
    stays "Not assessed" unless other evidence exists) — a low-parse result
    is not allowed to masquerade as a clean tenure profile."""
    out: dict[str, list] = {}
    if not kris:
        return out
    n = kris.get("n_tenure_parsed", 0)
    if n >= MIN_PARSED_FOR_ASSESSMENT:
        conf = f"n={n}, {kris.get('confidence')} confidence"
        pol = kris.get("policies", {})
        has_policy = pol.get("mandatory_retirement_age") or pol.get("term_limit") or pol.get("board_refreshment")
        items = []
        avg = kris["avg_tenure_years"]
        pts = 0.8 if avg >= AVG_TENURE_HIGH else 0.4 if avg >= AVG_TENURE_ELEVATED else 0.0
        items.append(rt.ev("DEF 14A", f"Average director tenure {avg} yrs ({conf})", pts, observed=avg))
        share = kris["share_over_10y"]
        items.append(rt.ev("DEF 14A", f"{round(share*100)}% of directors serving ≥{TENURE_LONG_YEARS} yrs ({conf})",
                           0.6 if share > SHARE_LONG_ELEVATED else 0.0, observed=share))
        mx = kris["max_tenure_years"]
        items.append(rt.ev("DEF 14A", f"Longest-serving director {mx} yrs",
                           0.4 if mx >= 20 else 0.0, observed=mx))
        stale = avg >= AVG_TENURE_ELEVATED
        items.append(rt.ev("DEF 14A",
                           "Refreshment policy disclosed (retirement age / term limit / refreshment)" if has_policy
                           else ("No board-refreshment, retirement-age or term-limit policy found alongside an aged tenure profile" if stale
                                 else "No refreshment policy found (tenure profile not stale)"),
                           0.0 if has_policy else (0.4 if stale else 0.0), observed=bool(has_policy)))
        out["TX-GOV"] = items
    aud = kris.get("auditor")
    if aud and aud.get("years") is not None:
        yrs = aud["years"]
        out.setdefault("TX-AUD", []).append(rt.ev(
            "DEF 14A", f"Audit firm tenure ~{yrs} yrs (since {aud['since']}); partner rotation (5-yr lead / 7-yr other, SOX §203) not verifiable from the proxy",
            0.4 if yrs >= AUDITOR_TENURE_LONG else 0.0, observed=yrs))
    return out


# ── SEC staff comment letters ─────────────────────────────────────────────

COMMENT_TOPICS: list[tuple[str, str, str]] = [
    # (label, regex, taxonomy id)
    ("Non-GAAP measures / key metrics", r"non-?gaap|regulation g|item 10\(e\)|key (performance )?(indicators|metrics)|adjusted ebitda", "TX-FRQ"),
    ("Revenue recognition", r"revenue recognition|asc 606|performance obligation|principal versus agent", "TX-FRQ"),
    ("Segment reporting", r"segment (reporting|information)|asc 280|operating segments?|chief operating decision maker", "TX-FRQ"),
    ("Internal control / disclosure controls", r"internal control over financial reporting|disclosure controls|item 9a|material weakness", "TX-AUD"),
    ("Cybersecurity disclosure", r"cybersecurity|item 106|item 1c|data breach|cyber incident", "TX-CYB"),
    ("Climate / ESG disclosure", r"climate|greenhouse|sustainab|esg\b", "TX-ESG"),
    ("Related party / governance / compensation", r"related[- ]party|executive compensation|clawback|director independence|item 407", "TX-GOV"),
    ("Going concern / liquidity", r"going concern|liquidity and capital resources|substantial doubt", "TX-LIQ"),
    ("Risk-factor language", r"risk factors?|hypothetical|item 1a", "TX-REG"),
    ("Legal proceedings / contingencies", r"legal proceedings|loss contingenc|asc 450|item 103", "TX-REG"),
    ("Trade / sanctions exposure", r"sanction|ofac|export control|china|variable interest entit", "TX-TRD"),
]


def classify_comment_text(text: str) -> list[dict]:
    t = (text or "").lower()
    seen, out = set(), []
    for label, pat, tx in COMMENT_TOPICS:
        if re.search(pat, t) and label not in seen:
            seen.add(label)
            out.append({"topic": label, "tx": tx})
    return out


def _days_ago(d: str, today: date) -> Optional[int]:
    try:
        y, m, dd = (int(x) for x in d[:10].split("-"))
        return (today - date(y, m, dd)).days
    except Exception:
        return None


def comment_letters_to_evidence(letters: list[dict], topics_by_accession: Optional[dict] = None,
                                today: Optional[date] = None) -> dict:
    """letters: [{form: 'UPLOAD'|'CORRESP', date, accession_number}] newest first.

    UPLOAD = SEC staff letter; CORRESP = company response. Scoring is on the
    review's activity, deliberately not on any single letter's wording:
      - staff letter in the last 12 months                +0.6
      - 2+ staff letters in the last 12 months (multi-round)  +0.6 more
      - latest document is an unanswered staff letter      +0.4 (open comment)
    Topic categories (when bodies were available) add +0.3 each to the
    matching category, capped at 3 topics.
    """
    today = today or date.today()
    out: dict[str, list] = {}
    staff = [l for l in letters if l.get("form") == "UPLOAD"]
    recent_staff = [l for l in staff if (_days_ago(l.get("date", ""), today) or 10**6) <= 365]
    if not letters:
        return out  # nothing in the index is not evidence either way (could be pre-index window)
    pts = 0.0
    if recent_staff:
        pts = 0.6
        label = f"{len(recent_staff)} SEC staff comment letter(s) in the last 12 months (latest {recent_staff[0]['date']})"
        items = [rt.ev("SEC comment letters", label, pts, trend=True, observed=len(recent_staff))]
        if len(recent_staff) >= 2:
            items.append(rt.ev("SEC comment letters", "Multiple rounds of staff comments — review not closed after first response",
                               0.6, trend=True, observed=len(recent_staff)))
        latest = letters[0]
        if latest.get("form") == "UPLOAD" and (_days_ago(latest.get("date", ""), today) or 0) <= 180:
            items.append(rt.ev("SEC comment letters", f"Latest document is an unanswered staff letter ({latest['date']}) — comment possibly still open",
                               0.4, trend=True, observed=latest.get("date")))
        out["TX-REG"] = items
    else:
        out["TX-REG"] = [rt.ev("SEC comment letters",
                               f"No staff comment letters in the last 12 months ({len(staff)} older letter(s) on file)",
                               0.0, trend=True, observed=0)]
    for acc, topics in (topics_by_accession or {}).items():
        for t in topics[:3]:
            out.setdefault(t["tx"], []).append(rt.ev(
                "SEC comment letters", f"Staff comment topic: {t['topic']} (accession {acc})", 0.3, trend=True,
                observed={"accession": acc, "topic": t["topic"]}))
    return out


def merge_evidence(*dicts: dict) -> dict:
    merged: dict[str, list] = {}
    for d in dicts:
        for k, v in (d or {}).items():
            merged.setdefault(k, []).extend(v)
    return merged


# ── Best-effort collection for run_full_analysis ───────────────────────────

def collect_extra_evidence(ticker: str, company_meta: dict,
                           get_submissions: Optional[Callable] = None,
                           get_cached_kris: Optional[Callable] = None,
                           today: Optional[date] = None) -> dict:
    """Evidence available WITHOUT any heavy download: comment-letter activity
    from the submissions index and cached DEF 14A KRIs. (/edgar/proxy is
    deliberately not called here — it has a history of OOM-killing the
    process; its KRIs reach the register via the loop's own proxy fetch.)"""
    out: dict = {}
    try:
        if get_submissions is None:
            import edgar_tool
            get_submissions = lambda tk: edgar_tool.get_company_info(tk)[1]
        sub = get_submissions(ticker)
        out = merge_evidence(out, comment_letters_to_evidence(list_comment_letters(sub), today=today))
    except Exception:
        pass
    try:
        if get_cached_kris is None:
            import db
            company_id = (company_meta or {}).get("company_id")
            get_cached_kris = (lambda _t: db.get_latest_proxy_kris(company_id)) if (company_id and db.is_available()) else None
        if get_cached_kris:
            out = merge_evidence(out, kris_to_evidence(get_cached_kris(ticker)))
    except Exception:
        pass
    return out


def list_comment_letters(submissions: dict, months: int = 36) -> list[dict]:
    """UPLOAD / CORRESP entries from an EDGAR submissions index, newest first."""
    recent = (submissions or {}).get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    cutoff = date.today().toordinal() - int(months * 30.4)
    out = []
    for i, f in enumerate(forms):
        if f not in ("UPLOAD", "CORRESP"):
            continue
        d = dates[i] if i < len(dates) else ""
        try:
            y, m, dd = (int(x) for x in d[:10].split("-"))
            if date(y, m, dd).toordinal() < cutoff:
                continue
        except Exception:
            continue
        out.append({"form": f, "date": d,
                    "accession_number": accs[i] if i < len(accs) else "",
                    "primary_document": docs[i] if i < len(docs) else ""})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out
