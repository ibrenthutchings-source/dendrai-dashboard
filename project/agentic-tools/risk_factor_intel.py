#!/usr/bin/env python3
"""
10-K Item 1A (Risk Factors) year-over-year change -> risk-taxonomy evidence.

Fully deterministic: regex extraction, a fixed keyword table for classification
and a token-overlap diff. No LLM, no randomness, so the same two filings always
produce the same evidence and every point can be traced to a sentence.

Why a DIFF and not a count: every issuer lists dozens of risk factors, so
presence says nothing. What carries information is what CHANGED since the prior
10-K:

    new factor              the issuer added a risk it did not disclose before      +0.3
    escalated wording       a hypothetical ("could", "may") became realised
                            ("has occurred", "we experienced") — the exact
                            language SEC staff comment letters target             +0.5
    expanded                body grew >= 1.5x (more disclosure about a known risk)  +0.2
    removed                 recorded for the reviewer, not scored (a removed
                            factor is not evidence of higher risk)

Points are weighted by the age of the CURRENT 10-K (event_risk_map recency
bands) and capped per category, like the 8-K items.

Honest limits (all surfaced to the reviewer, never silently scored):
  * Headings are recovered from plain text (bold formatting is lost), so the
    splitter is heuristic. Fewer than MIN_FACTORS factors from either filing ->
    status "low_confidence", nothing scored.
  * If fewer than STRUCTURE_MIN_MATCH of the current factors match any prior
    factor the issuer restructured the section -> status "structure_changed",
    nothing scored (a rewrite is not 40 new risks).
  * A factor that matches no topic rule is listed as "not scored".
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

import risk_taxonomy as rt
import event_risk_map as erm

SOURCE = "10-K Item 1A"
MIN_FACTORS = 8
STRUCTURE_MIN_MATCH = 0.4
MATCH_JACCARD = 0.55
EXPANDED_RATIO = 1.5
PTS_NEW, PTS_ESCALATED, PTS_EXPANDED = 0.3, 0.5, 0.2
MAX_POINTS_PER_CATEGORY = 1.5
MAX_SECTION_CHARS = 600_000

# Topic -> taxonomy rules. FIRST match wins, so order is priority (specific
# before general). A factor is classified on its heading plus the start of its body.
TOPIC_RULES: list[tuple[str, str, str]] = [
    ("Cybersecurity & privacy",      r"cyber|ransomware|data breach|security incident|information security|privacy|personal (data|information)|gdpr|unauthorized access", "TX-CYB"),
    ("Going concern & liquidity",    r"going concern|substantial doubt|liquidity|additional (capital|financing|funding)|sufficient (cash|capital)|access to (capital|credit)", "TX-LIQ"),
    ("Internal control & reporting", r"internal control|material weakness|restate|critical accounting|accounting estimates|goodwill|impairment|financial reporting|accounting standards", "TX-FRQ"),
    ("Auditor",                      r"independent registered public accounting|our auditor|external auditor|auditor (independence|rotation)", "TX-AUD"),
    ("Trade & sanctions",            r"tariff|sanction|export control|trade (restriction|war|polic)|customs|embargo|\bofac\b", "TX-TRD"),
    ("Climate & ESG",                r"climate|greenhouse|\besg\b|sustainab|carbon|environmental (law|regulation|liabilit)|severe weather", "TX-ESG"),
    ("Debt & capital structure",     r"indebtedness|substantial debt|leverage|refinanc|credit rating|covenant|interest rate|dilut|convertible", "TX-CAP"),
    ("Legal & regulatory",           r"regulat|litigation|legal proceeding|government investigation|enforcement|antitrust|intellectual property|patent|anti-corruption|fcpa|tax (law|legislation|rate)|compliance with", "TX-REG"),
    ("Governance & key people",      r"key (personnel|employees|executives)|senior management|succession|founder|controlling (stockholder|shareholder)|dual[- ]class|anti-takeover|conflicts? of interest|related[- ]party|board of directors", "TX-GOV"),
    ("Operations & supply chain",    r"supply chain|supplier|manufactur|single[- ]source|sole source|labor|workforce|facilit(y|ies)|inventory|business continuity|natural disaster|pandemic|outage", "TX-OPS"),
    ("Demand & revenue",             r"demand for|decline in demand|pricing pressure|reduce (their )?(spending|purchases)|seasonal|renewal|churn|customer (retention|losses)", "TX-REV"),
    ("Profitability",                r"net loss|history of losses|profitab|margin|cost increases|operating (costs|expenses)", "TX-PRF"),
    ("Strategy & concentration",     r"acquisition|integrat|strategic (transaction|alliance)|joint venture|divestiture|significant customers?|customer concentration|compet(e|ition|itive)|new products?", "TX-STR"),
    ("Macro & markets",              r"economic (condition|downturn|uncertaint)|recession|inflation|foreign currency|exchange rate|market volatility|macroeconomic|geopolitical|stock price|volatil", "TX-MAC"),
]

# Realised-language markers: the risk is described as having happened.
_REALISED = re.compile(
    r"\b(has|have|had) (occurred|materialized|materialised|been (subject|impacted|affected|the target|targeted))\b|"
    r"\bwe (have |had )?(experienced|suffered|were (subject|the target)|identified)\b|"
    r"\b(was|were) (subject to|the target of|affected by|impacted by)\b|"
    r"\bhas (experienced|suffered)\b", re.I)

_RISK_LANG = re.compile(
    r"\b(could|may|might|would|if we|if our|our ability|we are|we have|we may|failure|fail|depend|subject to|adverse|"
    r"risks?|unable|inability|difficult|volatil|loss of|changes? in)\b", re.I)

_STOP = set("the a an of to in and or for on at by with from our we us is are be as that this it its their these those "
            "may could would might will can not no any such if than other more also including related".split())

_BOILERPLATE = re.compile(r"^(table of contents|item\s+1a|risk factors$|summary of risk|the following|you should|"
                          r"in addition,?|these risks|see |for more|for a discussion|page \d+)", re.I)


# ── Extraction ────────────────────────────────────────────────────────────

def extract_item_1a(text: str) -> str:
    """The Item 1A section. The first 'Item 1A. Risk Factors' in a 10-K is usually
    the table of contents, so every candidate start is tried and the one that
    yields the longest section wins."""
    if not text:
        return ""
    starts = [m.start() for m in re.finditer(r"item\s*1a[\.\s:—\-–]*risk\s+factors", text, flags=re.I)]
    stop = re.compile(r"item\s*1b[\.\s:—\-–]|item\s*1c[\.\s:—\-–]|item\s*2[\.\s:—\-–]*properties|unresolved\s+staff\s+comments", re.I)
    best = ""
    for s in starts:
        m = stop.search(text, s + 300)
        sec = text[s: m.start() if m else min(len(text), s + MAX_SECTION_CHARS)]
        if len(sec) > len(best):
            best = sec
    return best[:MAX_SECTION_CHARS]


def _paragraphs(section: str) -> list[str]:
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n{2,}|\n", section or "")]
    return [p for p in paras if p]


def _is_heading(p: str) -> bool:
    if not (30 <= len(p) <= 420) or _BOILERPLATE.search(p) or not p[0].isupper():
        return False
    body = p.rstrip(".")
    if re.search(r"[.!?]\s+[A-Z]", body):          # more than one sentence -> body text
        return False
    return bool(_RISK_LANG.search(p))


def split_risk_factors(section: str) -> list[dict]:
    """[{heading, body, body_chars}] — a heading is a single-sentence paragraph with
    risk language, followed by at least ~250 chars of body before the next heading."""
    paras = _paragraphs(section)
    heads: list[int] = []
    for i, p in enumerate(paras):
        if _is_heading(p):
            # A heading must be followed by body text, not by another heading.
            nxt = paras[i + 1] if i + 1 < len(paras) else ""
            if nxt and not _is_heading(nxt):
                heads.append(i)
    out = []
    for n, i in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(paras)
        body = " ".join(paras[i + 1:end])
        if len(body) >= 250:
            out.append({"heading": paras[i], "body": body, "body_chars": len(body)})
    return out


# ── Classification & matching ─────────────────────────────────────────────

def classify(heading: str, body: str = "") -> Optional[dict]:
    t = f"{heading} {body[:600]}".lower()
    for label, pat, tx in TOPIC_RULES:
        if re.search(pat, t):
            return {"topic": label, "tx": tx}
    return None


def _tokens(s: str) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z]{3,}", (s or "").lower()) if w not in _STOP)


def _jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def match_factors(current: list[dict], prior: list[dict]) -> dict[int, int]:
    """current index -> prior index. Greedy by descending similarity, one-to-one,
    deterministic tie-break on (current idx, prior idx)."""
    cand = []
    pt = [_tokens(p["heading"]) for p in prior]
    for i, c in enumerate(current):
        ct = _tokens(c["heading"])
        for j, t in enumerate(pt):
            s = _jaccard(ct, t)
            if s >= MATCH_JACCARD:
                cand.append((-s, i, j))
    cand.sort()
    used_i, used_j, out = set(), set(), {}
    for _, i, j in cand:
        if i not in used_i and j not in used_j:
            out[i] = j
            used_i.add(i)
            used_j.add(j)
    return out


def _realised_count(s: str) -> int:
    return len(_REALISED.findall(s or ""))


# ── Analysis ──────────────────────────────────────────────────────────────

def analyze(current_text: str, prior_text: str, current_date: str = "",
            prior_date: str = "", today: Optional[date] = None,
            current_acc: str = "", prior_acc: str = "") -> dict:
    """Diff two 10-Ks' Item 1A. `*_text` are the full plain-text filings (or
    already-extracted sections). Returns the full result incl. `evidence`."""
    cur_sec = extract_item_1a(current_text) or current_text or ""
    pri_sec = extract_item_1a(prior_text) or prior_text or ""
    cur, pri = split_risk_factors(cur_sec), split_risk_factors(pri_sec)
    result = {
        "status": "ok", "message": "",
        "current": {"date": current_date, "accession": current_acc, "n_factors": len(cur)},
        "prior": {"date": prior_date, "accession": prior_acc, "n_factors": len(pri)},
        "matched": 0, "changes": [], "removed": [], "unclassified": [], "by_topic": {}, "evidence": {},
    }
    if not pri_sec:
        result.update(status="no_prior", message="Only one 10-K available — no year-over-year Item 1A comparison")
        return result
    if len(cur) < MIN_FACTORS or len(pri) < MIN_FACTORS:
        result.update(status="low_confidence",
                      message=f"Only {len(cur)} / {len(pri)} risk factors could be separated (need ≥{MIN_FACTORS} in both) — nothing scored")
        return result

    pairs = match_factors(cur, pri)
    result["matched"] = len(pairs)
    for c in cur:
        k = classify(c["heading"], c["body"])
        if k:
            result["by_topic"][k["tx"]] = result["by_topic"].get(k["tx"], 0) + 1
    if len(pairs) / len(cur) < STRUCTURE_MIN_MATCH:
        result.update(status="structure_changed",
                      message=f"Only {len(pairs)} of {len(cur)} current factors match the prior 10-K — the section was restructured; diff not scored")
        return result

    weight = erm.recency_weight(current_date, today) if current_date else 0.5
    matched_prior = set(pairs.values())
    for i, c in enumerate(cur):
        k = classify(c["heading"], c["body"])
        if i not in pairs:
            change, pts, why = "new", PTS_NEW, "New risk factor not in the prior 10-K"
        else:
            p = pri[pairs[i]]
            if _realised_count(c["body"]) > _realised_count(p["body"]):
                change, pts, why = "escalated", PTS_ESCALATED, "Wording moved from hypothetical to realised (e.g. 'has occurred' / 'we experienced')"
            elif p["body_chars"] and c["body_chars"] / p["body_chars"] >= EXPANDED_RATIO:
                change, pts, why = "expanded", PTS_EXPANDED, f"Disclosure grew {c['body_chars'] / p['body_chars']:.1f}x versus the prior 10-K"
            else:
                continue
        row = {"heading": c["heading"][:240], "change": change, "rationale": why,
               "tx": k["tx"] if k else None, "topic": k["topic"] if k else None,
               "points": pts if k else 0.0}
        if not k:
            result["unclassified"].append({"heading": row["heading"], "change": change})
        result["changes"].append(row)
    for j, p in enumerate(pri):
        if j not in matched_prior:
            k = classify(p["heading"], p["body"])
            result["removed"].append({"heading": p["heading"][:240], "tx": k["tx"] if k else None,
                                      "topic": k["topic"] if k else None})

    totals: dict[str, float] = {}
    for r in result["changes"]:
        if not r["tx"]:
            continue
        pts = round(r["points"] * weight, 2)
        pts = max(0.0, min(pts, MAX_POINTS_PER_CATEGORY - totals.get(r["tx"], 0.0)))
        totals[r["tx"]] = totals.get(r["tx"], 0.0) + pts
        r["weighted_points"] = pts
        result["evidence"].setdefault(r["tx"], []).append(rt.ev(
            SOURCE, f"{r['change'].capitalize()} risk factor ({current_date}): {r['heading'][:150]}", pts,
            trend=True, observed={"change": r["change"], "topic": r["topic"], "accession": current_acc}))
    return result
