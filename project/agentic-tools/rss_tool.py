#!/usr/bin/env python3
"""
RSS Industry News Tool

Finds the top 3 RSS news feeds for a company's industry (via EDGAR SIC code),
fetches articles from the past year, and saves results to rss_industry.json.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(__file__))
from edgar_tool import get_company_info

# ── RSS Feed Catalog ────────────────────────────────────────────────────────────
# Industry category -> ordered list of feed descriptors (best sources first)

RSS_CATALOG: dict[str, list[dict]] = {
    "Agriculture": [
        {"name": "AgWeb", "url": "https://www.agweb.com/rss/news", "description": "Farm and agriculture news"},
        {"name": "Farm Progress", "url": "https://www.farmprogress.com/rss.xml", "description": "Farm management, crops, livestock"},
        {"name": "Western Farm Press", "url": "https://www.westernfarmpress.com/rss.xml", "description": "Western US agriculture news"},
        {"name": "Reuters Agriculture", "url": "https://feeds.reuters.com/reuters/agricultureNews", "description": "Global agriculture news from Reuters"},
    ],
    "Mining": [
        {"name": "Mining.com", "url": "https://www.mining.com/feed/", "description": "Global mining industry news"},
        {"name": "Mining Weekly", "url": "https://www.miningweekly.com/rss/index", "description": "Mining news and analysis"},
        {"name": "Kitco Mining", "url": "https://www.kitco.com/rss/mining.rss", "description": "Mining and precious metals news"},
        {"name": "Reuters Mining", "url": "https://feeds.reuters.com/reuters/miningNews", "description": "Global mining news from Reuters"},
    ],
    "Oil & Gas": [
        {"name": "OilPrice.com", "url": "https://oilprice.com/rss/main", "description": "Oil and gas market news"},
        {"name": "Rigzone", "url": "https://www.rigzone.com/news/rss/rigzone_latest.aspx", "description": "Oil field and offshore news"},
        {"name": "Reuters Energy", "url": "https://feeds.reuters.com/reuters/energyNews", "description": "Energy sector news from Reuters"},
        {"name": "Oil & Gas Journal", "url": "https://www.ogj.com/rss/ogj/all.xml", "description": "Petroleum industry technical news"},
    ],
    "Construction": [
        {"name": "Construction Dive", "url": "https://www.constructiondive.com/feeds/news/", "description": "Construction industry news and analysis"},
        {"name": "ENR", "url": "https://www.enr.com/rss/all", "description": "Engineering News-Record – design and construction"},
        {"name": "BD+C", "url": "https://www.bdcnetwork.com/rss.xml", "description": "Building design and construction news"},
    ],
    "Food & Beverage": [
        {"name": "Food Dive", "url": "https://www.fooddive.com/feeds/news/", "description": "Food and beverage industry news"},
        {"name": "Food Business News", "url": "https://www.foodbusinessnews.net/rss/news", "description": "Food processing industry news"},
        {"name": "Supermarket News", "url": "https://www.supermarketnews.com/rss/news", "description": "Grocery and supermarket industry"},
        {"name": "Food Navigator USA", "url": "https://www.foodnavigator-usa.com/Info/RSS-Feeds", "description": "US food and nutrition news"},
    ],
    "Chemicals": [
        {"name": "C&EN", "url": "https://cen.acs.org/rss/news.xml", "description": "ACS chemical science and business news"},
        {"name": "ICIS News", "url": "https://www.icis.com/explore/resources/news/rss/", "description": "Chemical market news"},
        {"name": "Chemical Processing", "url": "https://www.chemicalprocessing.com/rss.xml", "description": "Chemical plant operations news"},
    ],
    "Pharmaceuticals": [
        {"name": "STAT News", "url": "https://www.statnews.com/feed/", "description": "Health, medicine, and biotech news"},
        {"name": "FiercePharma", "url": "https://www.fiercepharma.com/rss/xml", "description": "Pharmaceutical industry news"},
        {"name": "BioPharma Dive", "url": "https://www.biopharmadive.com/feeds/news/", "description": "Biopharma industry news"},
        {"name": "Endpoints News", "url": "https://endpts.com/feed/", "description": "Drug development and biotech news"},
    ],
    "Technology - Hardware": [
        {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index", "description": "Technology news and in-depth analysis"},
        {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "description": "Consumer technology and culture"},
        {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "description": "Technology industry and startup news"},
        {"name": "IEEE Spectrum", "url": "https://spectrum.ieee.org/rss", "description": "Technology for engineers"},
    ],
    "Technology - Software": [
        {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "description": "Technology industry and startup news"},
        {"name": "VentureBeat", "url": "https://venturebeat.com/feed/", "description": "Tech for business leaders and enterprise"},
        {"name": "ZDNet", "url": "https://www.zdnet.com/news/rss.xml", "description": "Enterprise technology and software news"},
        {"name": "InfoWorld", "url": "https://www.infoworld.com/index.rss", "description": "Enterprise software and cloud news"},
    ],
    "Semiconductors": [
        {"name": "EE Times", "url": "https://www.eetimes.com/rss_simple.asp", "description": "Electronics engineering and semiconductor news"},
        {"name": "Electronic Design", "url": "https://www.electronicdesign.com/rss.xml", "description": "Electronic design and chip industry news"},
        {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index", "description": "Technology with deep semiconductor coverage"},
        {"name": "AnandTech", "url": "https://www.anandtech.com/rss/", "description": "Semiconductor and hardware analysis"},
    ],
    "Aerospace & Defense": [
        {"name": "Defense News", "url": "https://www.defensenews.com/rss/news/", "description": "Defense industry news"},
        {"name": "Breaking Defense", "url": "https://breakingdefense.com/feed/", "description": "Defense technology and policy news"},
        {"name": "Aviation Week", "url": "https://aviationweek.com/rss/content", "description": "Aerospace and defense industry news"},
        {"name": "FlightGlobal", "url": "https://www.flightglobal.com/FlightRSS.xml", "description": "Aviation industry news"},
    ],
    "Automotive": [
        {"name": "Automotive News", "url": "https://www.autonews.com/rss/headlines", "description": "Auto industry business news"},
        {"name": "Electrek", "url": "https://electrek.co/feed/", "description": "Electric vehicles and clean energy news"},
        {"name": "WardsAuto", "url": "https://www.wardsauto.com/rss.xml", "description": "Automotive industry data and news"},
        {"name": "Car and Driver News", "url": "https://www.caranddriver.com/rss/all.xml/", "description": "Automotive news and reviews"},
    ],
    "Transportation & Logistics": [
        {"name": "FreightWaves", "url": "https://www.freightwaves.com/news/feed", "description": "Freight and logistics industry news"},
        {"name": "Transport Topics", "url": "https://www.ttnews.com/rss.xml", "description": "Trucking and freight transportation"},
        {"name": "Supply Chain Dive", "url": "https://www.supplychaindive.com/feeds/news/", "description": "Supply chain industry news"},
        {"name": "DC Velocity", "url": "https://www.dcvelocity.com/rss.xml", "description": "Distribution and supply chain news"},
    ],
    "Airlines": [
        {"name": "Aviation Week", "url": "https://aviationweek.com/rss/content", "description": "Aerospace and aviation industry news"},
        {"name": "FlightGlobal", "url": "https://www.flightglobal.com/FlightRSS.xml", "description": "Aviation industry news"},
        {"name": "The Air Current", "url": "https://theaircurrent.com/feed/", "description": "Aviation business and policy news"},
        {"name": "Simple Flying", "url": "https://simpleflying.com/feed/", "description": "Airline and aviation news"},
    ],
    "Utilities": [
        {"name": "Utility Dive", "url": "https://www.utilitydive.com/feeds/news/", "description": "Electric and gas utility industry news"},
        {"name": "Power Magazine", "url": "https://www.powermag.com/feed/", "description": "Electric power generation news"},
        {"name": "Renewables Now", "url": "https://renewablesnow.com/rss/news/", "description": "Renewable energy industry news"},
        {"name": "E&E News", "url": "https://www.eenews.net/rss/all", "description": "Energy and environment policy news"},
    ],
    "Retail": [
        {"name": "Retail Dive", "url": "https://www.retaildive.com/feeds/news/", "description": "Retail industry news and analysis"},
        {"name": "Chain Store Age", "url": "https://chainstoreage.com/rss.xml", "description": "Retail real estate and operations"},
        {"name": "Retail TouchPoints", "url": "https://www.retailtouchpoints.com/component/obrss/latest-news.feed", "description": "Omnichannel retail news"},
        {"name": "Supermarket News", "url": "https://www.supermarketnews.com/rss/news", "description": "Grocery retail industry news"},
    ],
    "Finance & Banking": [
        {"name": "American Banker", "url": "https://www.americanbanker.com/feed", "description": "Banking and financial services news"},
        {"name": "Finance Magnates", "url": "https://www.financemagnates.com/feed/", "description": "Financial industry news"},
        {"name": "Reuters Finance", "url": "https://feeds.reuters.com/reuters/financialsNews", "description": "Financial news from Reuters"},
        {"name": "CFO Magazine", "url": "https://www.cfo.com/rss", "description": "CFO and financial management news"},
    ],
    "Insurance": [
        {"name": "Insurance Journal", "url": "https://www.insurancejournal.com/rss/", "description": "Property casualty insurance news"},
        {"name": "PropertyCasualty360", "url": "https://www.propertycasualty360.com/feed", "description": "P&C insurance industry news"},
        {"name": "Insurance Business", "url": "https://www.insurancebusinessmag.com/us/rss/all-news", "description": "Insurance industry news"},
    ],
    "Real Estate": [
        {"name": "Bisnow", "url": "https://www.bisnow.com/feed", "description": "Commercial real estate news"},
        {"name": "The Real Deal", "url": "https://therealdeal.com/feed/", "description": "Real estate industry news"},
        {"name": "Commercial Observer", "url": "https://commercialobserver.com/feed/", "description": "Commercial real estate finance news"},
    ],
    "Healthcare Services": [
        {"name": "Healthcare Dive", "url": "https://www.healthcaredive.com/feeds/news/", "description": "Healthcare industry news and analysis"},
        {"name": "MedCity News", "url": "https://medcitynews.com/feed/", "description": "Healthcare innovation news"},
        {"name": "Modern Healthcare", "url": "https://www.modernhealthcare.com/rss/news", "description": "Healthcare business and policy news"},
        {"name": "Health Affairs", "url": "https://www.healthaffairs.org/rss/current.xml", "description": "Health policy research and news"},
    ],
    "Media & Entertainment": [
        {"name": "Variety", "url": "https://variety.com/feed/", "description": "Entertainment industry news"},
        {"name": "Deadline", "url": "https://deadline.com/feed/", "description": "Film, TV, and entertainment news"},
        {"name": "Hollywood Reporter", "url": "https://www.hollywoodreporter.com/feed/", "description": "Entertainment business news"},
        {"name": "The Wrap", "url": "https://www.thewrap.com/feed/", "description": "Entertainment and media industry news"},
    ],
    "Hospitality & Restaurants": [
        {"name": "Restaurant Business", "url": "https://www.restaurantbusinessonline.com/feed", "description": "Restaurant industry news"},
        {"name": "Nation's Restaurant News", "url": "https://www.nrn.com/rss.xml", "description": "Foodservice industry news"},
        {"name": "Hotel News Now", "url": "https://www.hotelnewsnow.com/feed", "description": "Hotel industry news"},
        {"name": "QSR Magazine", "url": "https://www.qsrmagazine.com/rss.xml", "description": "Quick service restaurant news"},
    ],
    "Telecommunications": [
        {"name": "FierceWireless", "url": "https://www.fiercewireless.com/rss/xml", "description": "Wireless telecom industry news"},
        {"name": "Light Reading", "url": "https://www.lightreading.com/rss.xml", "description": "Telecom and networking news"},
        {"name": "RCR Wireless", "url": "https://www.rcrwireless.com/feed", "description": "Wireless communications news"},
    ],
    "General Business": [
        {"name": "Reuters Business", "url": "https://feeds.reuters.com/reuters/businessNews", "description": "Business news from Reuters"},
        {"name": "CNBC Business", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "description": "Business news from CNBC"},
        {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines", "description": "Market and business headlines"},
        {"name": "Business Insider", "url": "https://www.businessinsider.com/rss", "description": "Business and finance news"},
    ],
}

# ── SIC Code to Industry Category Mapping ──────────────────────────────────────
# Entries are processed in order; first match wins. Most-specific ranges first.

_SIC_RANGES: list[tuple[int, int, str, list[str]]] = [
    # Semiconductors
    (3674, 3674, "Semiconductors", ["Technology - Hardware"]),
    # Pharmaceuticals / Biotech
    (2830, 2836, "Pharmaceuticals", ["Healthcare Services"]),
    # Oil & gas extraction (crude, natural gas, field services)
    (1311, 1311, "Oil & Gas", ["Mining"]),
    (1381, 1389, "Oil & Gas", ["Mining"]),
    (1300, 1399, "Oil & Gas", ["Mining"]),
    # Petroleum refining
    (2911, 2911, "Oil & Gas", ["Chemicals"]),
    (2900, 2999, "Oil & Gas", ["Chemicals"]),
    # Airlines
    (4512, 4522, "Airlines", ["Transportation & Logistics"]),
    (4510, 4522, "Airlines", ["Transportation & Logistics"]),
    # Telecom
    (4813, 4813, "Telecommunications", ["Technology - Software"]),
    (4812, 4812, "Telecommunications", ["Technology - Software"]),
    (4810, 4813, "Telecommunications", ["Technology - Software"]),
    # Broadcast / cable TV → Media
    (4833, 4833, "Media & Entertainment", ["Telecommunications"]),
    (4841, 4841, "Media & Entertainment", ["Telecommunications"]),
    # Software / data processing
    (7372, 7379, "Technology - Software", ["Technology - Hardware"]),
    (7370, 7379, "Technology - Software", ["Technology - Hardware"]),
    # Computer hardware / electronic computers
    (3571, 3579, "Technology - Hardware", ["Technology - Software"]),
    (3570, 3579, "Technology - Hardware", ["Technology - Software"]),
    # Electronic components (including chips beyond 3674)
    (3670, 3679, "Technology - Hardware", ["Semiconductors"]),
    (3670, 3699, "Technology - Hardware", ["Semiconductors"]),
    # Aerospace & Defense
    (3761, 3769, "Aerospace & Defense", ["Technology - Hardware"]),
    (3720, 3769, "Aerospace & Defense", ["Transportation & Logistics"]),
    # Automotive
    (3711, 3716, "Automotive", ["Transportation & Logistics"]),
    (3710, 3799, "Automotive", ["General Business"]),
    # Trucking
    (4210, 4299, "Transportation & Logistics", ["General Business"]),
    # Rail
    (4011, 4013, "Transportation & Logistics", ["General Business"]),
    (4000, 4099, "Transportation & Logistics", ["General Business"]),
    # Water and pipeline transport
    (4400, 4499, "Transportation & Logistics", ["General Business"]),
    # Other air transportation (non-airlines)
    (4522, 4581, "Transportation & Logistics", ["Airlines"]),
    # Other transportation
    (4100, 4899, "Transportation & Logistics", ["General Business"]),
    # Electric utilities
    (4911, 4941, "Utilities", ["General Business"]),
    (4900, 4999, "Utilities", ["General Business"]),
    # Restaurants / eating places
    (5812, 5812, "Hospitality & Restaurants", ["Retail"]),
    (5800, 5812, "Hospitality & Restaurants", ["Retail"]),
    # Hotels / lodging
    (7011, 7011, "Hospitality & Restaurants", ["Real Estate"]),
    (7000, 7099, "Hospitality & Restaurants", ["Real Estate"]),
    # Motion pictures
    (7810, 7819, "Media & Entertainment", ["General Business"]),
    # Entertainment / amusement parks / sports
    (7920, 7929, "Media & Entertainment", ["General Business"]),
    # Publishing / newspapers / TV (SIC 2700s, 4830s)
    (2710, 2799, "Media & Entertainment", ["General Business"]),
    (4830, 4899, "Media & Entertainment", ["Telecommunications"]),
    # Finance – commercial banks
    (6020, 6029, "Finance & Banking", ["General Business"]),
    (6000, 6099, "Finance & Banking", ["General Business"]),
    # Finance – credit, mortgage, securities, investment
    (6100, 6399, "Finance & Banking", ["General Business"]),
    # Insurance
    (6311, 6399, "Insurance", ["Finance & Banking"]),
    (6400, 6499, "Insurance", ["Finance & Banking"]),
    # Real estate
    (6500, 6552, "Real Estate", ["Finance & Banking"]),
    # Holding companies / investment offices
    (6700, 6799, "Finance & Banking", ["General Business"]),
    # Healthcare services
    (8011, 8099, "Healthcare Services", ["Pharmaceuticals"]),
    (8000, 8099, "Healthcare Services", ["Pharmaceuticals"]),
    # Chemicals (excluding pharma, already handled above)
    (2810, 2829, "Chemicals", ["Pharmaceuticals"]),
    (2800, 2829, "Chemicals", ["Pharmaceuticals"]),
    # Food & Beverage
    (2080, 2099, "Food & Beverage", ["Retail"]),
    (2000, 2099, "Food & Beverage", ["Retail"]),
    # Construction
    (1500, 1799, "Construction", ["General Business"]),
    # Mining (excluding oil & gas, handled above)
    (1000, 1299, "Mining", ["General Business"]),
    (1400, 1499, "Mining", ["General Business"]),  # Non-metallic minerals
    # Agriculture
    (100, 999, "Agriculture", ["General Business"]),
    # Retail trade
    (5200, 5999, "Retail", ["General Business"]),
    # Wholesale trade
    (5000, 5199, "General Business", ["Retail"]),
]


def get_sic_category(sic_code: int, sic_description: str) -> tuple[str, list[str]]:
    """Return (primary_category, fallback_categories) for a SIC code."""
    desc = sic_description.lower()

    # Keyword-based overrides for high-confidence mapping that crosses SIC boundaries
    if any(k in desc for k in ["semiconductor", "integrated circuit", "microchip", "wafer fab"]):
        return "Semiconductors", ["Technology - Hardware"]
    if any(k in desc for k in ["pharmaceutical", "biotech", "biolog", "vaccine"]) or (
        "drug" in desc and "store" not in desc and "retail" not in desc
    ):
        return "Pharmaceuticals", ["Healthcare Services"]
    if any(k in desc for k in ["airline", "air transport", "aircraft operat"]):
        return "Airlines", ["Transportation & Logistics"]
    if any(k in desc for k in ["telecom", "telephone", "wireless carrier", "cellular network", "broadband provider"]):
        return "Telecommunications", ["Technology - Software"]
    if any(k in desc for k in ["prepackaged software", "computer program", "saas", "cloud computing"]):
        return "Technology - Software", ["Technology - Hardware"]
    if any(k in desc for k in ["electronic computer", "computer storage", "computer peripheral"]):
        return "Technology - Hardware", ["Technology - Software"]

    for start, end, category, fallbacks in _SIC_RANGES:
        if start <= sic_code <= end:
            return category, fallbacks

    return "General Business", []


def get_industry_feeds(sic_code: str, sic_description: str, count: int = 3) -> tuple[list[dict], str]:
    """
    Return (feeds, industry_category) for the given SIC code.
    Pulls up to `count` feeds, trying the primary category then fallbacks.
    """
    try:
        sic_int = int(sic_code)
    except (ValueError, TypeError):
        sic_int = 0

    primary, fallbacks = get_sic_category(sic_int, sic_description)

    selected: list[dict] = []
    seen_urls: set[str] = set()

    for cat in [primary] + fallbacks + ["General Business"]:
        for feed in RSS_CATALOG.get(cat, []):
            if feed["url"] not in seen_urls and len(selected) < count:
                selected.append({**feed, "category": cat})
                seen_urls.add(feed["url"])

    return selected[:count], primary


# ── Feed Fetching ───────────────────────────────────────────────────────────────

_REQUEST_HEADERS = {
    "User-Agent": "RSSIndustryBot/1.0 (industry news research tool)",
    "Accept": "application/rss+xml, application/atom+xml, text/xml, */*",
}


def _fetch_raw(url: str, timeout: int = 20) -> bytes | None:
    """Fetch raw feed bytes, returning None on any network/HTTP failure."""
    try:
        resp = requests.get(url, timeout=timeout, headers=_REQUEST_HEADERS)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


def _parse_entry_date(entry: object) -> datetime | None:
    """Extract a UTC-aware datetime from a feedparser entry."""
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, field, None) or entry.get(field) if isinstance(entry, dict) else None
        if val is None and hasattr(entry, field):
            val = getattr(entry, field)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _strip_html(text: str) -> str:
    """Strip HTML tags from text, collapsing whitespace."""
    if not text:
        return ""
    try:
        return BeautifulSoup(text, "lxml").get_text(separator=" ", strip=True)
    except Exception:
        return text


def fetch_feed_articles(feed: dict, cutoff: datetime) -> dict:
    """
    Fetch all articles from an RSS/Atom feed published on or after `cutoff`.
    Returns an augmented feed dict with `articles`, `article_count`, and `status`.
    """
    raw = _fetch_raw(feed["url"])
    if raw is None:
        return {
            **feed,
            "status": "error",
            "error": f"Could not fetch feed URL: {feed['url']}",
            "article_count": 0,
            "articles": [],
        }

    parsed = feedparser.parse(raw)

    if parsed.get("bozo") and not parsed.entries:
        exc = str(parsed.get("bozo_exception", "unknown parse error"))
        return {
            **feed,
            "status": "error",
            "error": f"Invalid feed: {exc}",
            "article_count": 0,
            "articles": [],
        }

    articles: list[dict] = []
    for entry in parsed.entries:
        pub_date = _parse_entry_date(entry)
        # Skip articles older than cutoff; include undated articles (can't exclude them)
        if pub_date and pub_date < cutoff:
            continue

        raw_summary = (
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or ""
        )
        summary = _strip_html(raw_summary)[:2000]

        article: dict = {
            "title": (getattr(entry, "title", "") or "").strip(),
            "url": getattr(entry, "link", "") or "",
            "published": pub_date.isoformat() if pub_date else None,
            "author": getattr(entry, "author", "") or "",
            "summary": summary,
        }

        tags = [t.get("term", "") for t in getattr(entry, "tags", []) if t.get("term")]
        if tags:
            article["tags"] = tags

        articles.append(article)

    feed_title = (parsed.feed.get("title") or feed["name"]).strip()

    return {
        **feed,
        "status": "success",
        "feed_title": feed_title,
        "article_count": len(articles),
        "articles": articles,
    }


# ── Main Orchestration ──────────────────────────────────────────────────────────

def run_rss_analysis(ticker: str, output_path: Path) -> dict:
    """
    Full pipeline: look up the ticker's SIC, select the top 3 industry RSS feeds,
    download articles from the past year, and save results to output_path.
    Returns the result dict.
    """
    meta, _ = get_company_info(ticker)

    sic = meta.get("sic", "")
    sic_description = meta.get("sic_description", "")

    feeds_list, industry_category = get_industry_feeds(sic, sic_description, count=3)

    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=365)

    feed_results: list[dict] = []
    for rank, feed in enumerate(feeds_list, start=1):
        result = fetch_feed_articles(feed, cutoff)
        result["rank"] = rank
        feed_results.append(result)
        if rank < len(feeds_list):
            time.sleep(1)  # polite delay between feed requests

    total_articles = sum(f.get("article_count", 0) for f in feed_results)
    errors = [f["error"] for f in feed_results if f.get("status") == "error"]

    output = {
        "generated_at": now.isoformat(),
        "ticker": ticker.upper(),
        "company_name": meta["company_name"],
        "cik": meta.get("cik_plain", meta.get("cik", "")),
        "sic": sic,
        "sic_description": sic_description,
        "industry_category": industry_category,
        "analysis_period": {
            "start": cutoff.strftime("%Y-%m-%d"),
            "end": now.strftime("%Y-%m-%d"),
        },
        "feeds": feed_results,
        "total_articles": total_articles,
        "errors": errors,
    }

    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    return output
