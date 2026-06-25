#!/usr/bin/env python3
"""
Dendrai RSS Ingest Service

Server-side fetch + grading of the compliance/regulatory RSS feeds registered
in rss-engine.js. Mirrors the JS grading algorithm so scores are consistent
whether the dashboard calls the Vite proxy or the MCP server.

Key advantages over the browser-side path:
  - Persistent TTL cache — warm signals on every pipeline run
  - Deduplication across sessions via content hash
  - Full article body available for future NLP grading
  - Production-ready (no Vite dev-server dependency)

Grading algorithm:
  relevance = min(1, keyword_hits / 3)          -- domain vocab density
  severity  = min(1, 0.3 + Σ urgency_word * 0.15)
  velocity  = round(relevance * severity * 5 * feed_weight)  → [0, 5]
"""

import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Optional

import feedparser
import requests

# ── Feed Registry (mirrors rss-engine.js FEEDS) ───────────────────────────────
FEEDS: list[dict] = [
    {
        "id": "bis",
        "name": "BIS Export Controls",
        "url": "https://www.bis.doc.gov/index.php/2013-01-17-21-19-53/2013-01-17-21-20-34.xml",
        "domains": ["Trade Compliance"],
        "risks": ["R-02"],
        "weight": 1.5,
    },
    {
        "id": "cisa",
        "name": "CISA ICS Advisories",
        "url": "https://www.cisa.gov/cybersecurity-advisories/ics-advisories.xml",
        "domains": ["Cybersecurity"],
        "risks": ["R-04"],
        "weight": 1.3,
    },
    {
        "id": "sec",
        "name": "SEC EDGAR Peer Filings",
        # URL is the fallback used only when no ticker is supplied.
        "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=10-K&dateb=&owner=include&count=8&output=atom",
        "type": "edgar-peers",
        "domains": ["Financial Reporting"],
        "risks": ["R-01", "R-05"],
        "weight": 1.0,
    },
    {
        "id": "fed",
        "name": "Federal Reserve Press",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "domains": ["Macro"],
        "risks": ["R-09"],
        "weight": 0.9,
    },
    {
        "id": "epa",
        "name": "EPA Climate Enforcement",
        "url": "https://www.epa.gov/newsreleases/search/rss",
        "domains": ["ESG"],
        "risks": ["R-07"],
        "weight": 0.8,
    },
]

FEEDS_BY_ID: dict[str, dict] = {f["id"]: f for f in FEEDS}

# ── Domain keyword vocabularies (mirrors rss-engine.js DOMAIN_VOCAB) ──────────
DOMAIN_VOCAB: dict[str, list[str]] = {
    "Trade Compliance": [
        "export control", "eccn", "bis", "entity list", "end-user", "license",
        "deemed export", "sanction", "ofac", "itar", "ear", "china", "huawei",
        "advanced node", "chip", "semiconductor export", "tariff", "customs",
        "restricted party", "denied party", "foreign direct product",
    ],
    "Financial Reporting": [
        "revenue recognition", "asc 606", "restatement", "write-down", "write-off",
        "goodwill impairment", "inventory reserve", "channel stuffing", "bill-and-hold",
        "10-k", "10-q", "earnings", "guidance", "sec comment letter", "going concern",
        "internal control", "sox", "material weakness", "accounts receivable",
    ],
    "Cybersecurity": [
        "cisa", "vulnerability", "cve", "patch", "zero-day", "ransomware", "breach",
        "ics", "scada", "ot security", "supply chain attack", "privileged access",
        "authentication", "mfa", "endpoint", "threat actor", "apt",
    ],
    "Macro": [
        "pmi", "gdp", "recession", "manufacturing", "semiconductor demand",
        "inventory correction", "destocking", "lead time", "capacity utilization",
        "fed funds", "interest rate", "inflation", "consumer sentiment", "capex",
    ],
    "Operational": [
        "fab", "wafer", "yield", "capacity", "sic", "silicon carbide",
        "auto demand", "ev", "electric vehicle", "production ramp",
        "supply disruption", "shortage", "inventory write-down",
    ],
    "ESG": [
        "climate", "esg", "water stress", "sec disclosure", "scope 3",
        "carbon", "emission", "sustainability", "drought", "physical risk",
        "transition risk", "tcfd", "arizona", "fab water",
    ],
    "Supply": [
        "conflict minerals", "rmap", "dodd-frank", "cobalt", "tantalum",
        "tin", "tungsten", "smelter", "supply chain due diligence",
        "xinjiang", "forced labor",
    ],
    "Legal": [
        "patent", "litigation", "lawsuit", "ip", "intellectual property",
        "injunction", "royalty", "cross-license", "settlement", "verdict",
    ],
}

SEVERITY_WORDS: dict[str, float] = {
    "critical": 3.0, "urgent": 2.5, "mandatory": 2.0, "violation": 2.5,
    "penalty": 2.5, "enforcement": 2.5, "warning": 1.5, "significant": 1.5,
    "material": 1.5, "expanded": 1.2, "escalated": 1.5, "immediate": 2.0,
    "ban": 2.5, "sanction": 2.5, "restricted": 1.5, "updated": 0.8,
    "revised": 0.7, "announced": 0.5, "proposed": 0.6, "monitoring": 0.6,
    "review": 0.5,
}

# ── Grading functions ─────────────────────────────────────────────────────────

def _tokenize(text: str) -> str:
    return re.sub(r"[^\w\s-]", " ", (text or "").lower())


def score_relevance(text: str, domains: list[str]) -> float:
    t = _tokenize(text)
    hits = 0
    for domain in domains:
        for kw in DOMAIN_VOCAB.get(domain, []):
            if kw in t:
                hits += 1
    return min(1.0, hits / 3)


def score_severity(text: str) -> float:
    t = _tokenize(text)
    score = 0.3
    for word, weight in SEVERITY_WORDS.items():
        if word in t:
            score += weight * 0.15
    return min(1.0, score)


def velocity_from_scores(relevance: float, severity: float, feed_weight: float) -> int:
    raw = relevance * severity * 5 * feed_weight
    return int(round(max(0.0, min(5.0, raw))))


def _article_id(feed_id: str, title: str) -> str:
    h = hashlib.md5(title.encode("utf-8", errors="ignore")).hexdigest()[:6]
    return f"{feed_id}-{h}"


def grade_article(entry: dict, feed: dict) -> dict:
    title = (entry.get("title") or "").strip()
    description = (entry.get("summary") or entry.get("description") or "").strip()
    text = f"{title} {description}"

    relevance = score_relevance(text, feed["domains"])
    severity = score_severity(text)
    velocity = velocity_from_scores(relevance, severity, feed["weight"])
    rag = "R" if velocity >= 3 else "A" if velocity >= 2 else "G"

    pub_date = entry.get("published") or entry.get("updated") or datetime.now(timezone.utc).isoformat()

    return {
        "id": _article_id(feed["id"], title or pub_date),
        "feedId": feed["id"],
        "feedName": feed["name"],
        "title": title or "(No title)",
        "url": entry.get("link") or None,
        "pubDate": pub_date,
        "relevance": round(relevance, 2),
        "severity": round(severity, 2),
        "velocity": velocity,
        "rag": rag,
        "affectedRisks": list(feed["risks"]),
        "domains": list(feed["domains"]),
        "src": "Industry RSS",
        "delta": f"v={'+' if velocity >= 0 else ''}{velocity}",
        "label": title or "(No title)",
        "cat": "RSS",
        "gradedAt": datetime.now(timezone.utc).isoformat(),
    }


# ── Feed fetching (uses feedparser — already a project dependency) ─────────────

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DendraiBot/1.0; +https://dendrai.ai)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


def _fetch_raw(url: str, timeout: int = 12) -> Optional[bytes]:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


def _fetch_peer_ciks(ticker: str) -> list[dict]:
    """Return up to 5 peer companies for ticker via edgar_tool / peer_intel."""
    try:
        from edgar_tool import get_company_info, fetch_sic_peers
        import peer_intel as _pi

        meta, sub = get_company_info(ticker)
        sic = meta.get("sic", "")

        peers: list = []
        try:
            named = _pi.extract_competitor_names(ticker, meta, sub)
            if named:
                peers = _pi.resolve_names_to_edgar(
                    named, exclude_cik=meta.get("cik_plain", "")
                )
        except Exception:
            pass

        if not peers and sic:
            peers = fetch_sic_peers(sic, max_peers=10)

        return peers[:5]
    except Exception:
        return []


def fetch_and_grade_peer_filings(ticker: str, feed: dict) -> dict:
    """Fetch recent filings for each peer company and grade as a single feed result."""
    peers = _fetch_peer_ciks(ticker)
    if not peers:
        return fetch_and_grade(feed)

    graded = []
    for peer in peers:
        cik = (peer.get("cik_plain") or peer.get("cik") or "").lstrip("0")
        if not cik:
            continue
        peer_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=3&output=atom"
        )
        raw = _fetch_raw(peer_url)
        if raw is None:
            continue
        parsed = feedparser.parse(raw)
        label = peer.get("ticker") or peer.get("company_name") or ""
        for entry in parsed.entries[:3]:
            title = (getattr(entry, "title", "") or "").strip()
            article = {
                "title": f"[{label}] {title}" if label else title,
                "summary": getattr(entry, "summary", "") or "",
                "description": getattr(entry, "description", "") or "",
                "published": _parse_date(entry),
                "link": getattr(entry, "link", "") or "",
            }
            graded.append(grade_article(article, feed))

    if not graded:
        return fetch_and_grade(feed)

    return {"feed": feed, "articles": graded, "fetchStatus": "ok"}


def fetch_and_grade(feed: dict) -> dict:
    """Fetch a single feed via feedparser, grade all entries, return result dict."""
    raw = _fetch_raw(feed["url"])
    if raw is None:
        return {"feed": feed, "articles": [], "fetchStatus": "failed"}

    parsed = feedparser.parse(raw)
    if parsed.get("bozo") and not parsed.entries:
        return {"feed": feed, "articles": [], "fetchStatus": "failed"}

    graded = []
    for entry in parsed.entries[:8]:
        article = {
            "title":       getattr(entry, "title",   "") or "",
            "summary":     getattr(entry, "summary",  "") or "",
            "description": getattr(entry, "description", "") or "",
            "published":   _parse_date(entry),
            "link":        getattr(entry, "link",    "") or "",
        }
        graded.append(grade_article(article, feed))

    return {"feed": feed, "articles": graded, "fetchStatus": "ok"}


def _parse_date(entry) -> str:
    for field in ("published_parsed", "updated_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


# ── In-memory TTL cache ───────────────────────────────────────────────────────
# Keyed by feed_id. Persists for the lifetime of the api_server.py process.
# DB persistence for cross-restart history is a future enhancement.

_cache: dict[str, dict] = {}  # feed_id → {"result": dict, "fetched_at": float}


def _get_cached(feed_id: str, ttl_seconds: int) -> Optional[dict]:
    entry = _cache.get(feed_id)
    if entry and (time.monotonic() - entry["fetched_at"]) < ttl_seconds:
        return entry["result"]
    return None


def _set_cached(feed_id: str, result: dict) -> None:
    _cache[feed_id] = {"result": result, "fetched_at": time.monotonic()}


def _cache_age_seconds(feed_id: str) -> Optional[float]:
    entry = _cache.get(feed_id)
    return time.monotonic() - entry["fetched_at"] if entry else None


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_feeds(
    feed_ids: Optional[list[str]] = None,
    force_refresh: bool = False,
    ttl_minutes: int = 30,
    ticker: Optional[str] = None,
) -> dict:
    """
    Fetch and grade the requested feeds. Returns results shaped identically to
    RSS_ENGINE.ingestAll() on the frontend so the same mapping code applies.

    Args:
        feed_ids:       Feed IDs to process (default: all registered feeds).
        force_refresh:  Bypass cache and re-fetch even within TTL.
        ttl_minutes:    Cache TTL in minutes (default: 30).
        ticker:         Active ticker; enables peer-aware EDGAR filing fetch.
    """
    ttl_seconds = ttl_minutes * 60
    ticker_key = ticker.upper() if ticker else ""

    feeds = (
        [FEEDS_BY_ID[fid] for fid in feed_ids if fid in FEEDS_BY_ID]
        if feed_ids
        else FEEDS
    )

    results = []
    for feed in feeds:
        # Peer-aware feeds are keyed by ticker so each company gets its own cache slot.
        cache_key = f"{feed['id']}:{ticker_key}" if (feed.get("type") == "edgar-peers" and ticker_key) else feed["id"]
        cached = None if force_refresh else _get_cached(cache_key, ttl_seconds)
        if cached is not None:
            results.append({**cached, "cached": True})
        else:
            if feed.get("type") == "edgar-peers" and ticker:
                result = fetch_and_grade_peer_filings(ticker, feed)
            else:
                result = fetch_and_grade(feed)
            _set_cached(cache_key, result)
            results.append({**result, "cached": False})

    total_articles = sum(len(r["articles"]) for r in results)
    total_signals = sum(
        sum(1 for a in r["articles"] if a["velocity"] > 0) for r in results
    )

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "feeds": results,
        "total_articles": total_articles,
        "total_signals": total_signals,
        "live_feeds": sum(1 for r in results if r["fetchStatus"] == "ok"),
        "failed_feeds": sum(1 for r in results if r["fetchStatus"] == "failed"),
    }


def get_feed_status() -> list[dict]:
    """Return cache + health status for every registered feed."""
    status = []
    for feed in FEEDS:
        age = _cache_age_seconds(feed["id"])
        cached_entry = _cache.get(feed["id"])
        cached_result = cached_entry["result"] if cached_entry else None
        status.append({
            "id": feed["id"],
            "name": feed["name"],
            "url": feed["url"],
            "domains": feed["domains"],
            "last_fetched": (
                datetime.fromtimestamp(
                    time.time() - (time.monotonic() - cached_entry["fetched_at"]),
                    timezone.utc,
                ).isoformat()
                if cached_entry else None
            ),
            "age_seconds": round(age) if age is not None else None,
            "fetch_status": cached_result["fetchStatus"] if cached_result else "not_fetched",
            "article_count": len(cached_result["articles"]) if cached_result else 0,
            "signal_count": (
                sum(1 for a in cached_result["articles"] if a["velocity"] > 0)
                if cached_result else 0
            ),
        })
    return status
