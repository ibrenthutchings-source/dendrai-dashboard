#!/usr/bin/env python3
"""
RSS Industry News MCP Server

Finds the top 3 RSS news feeds for a company's SIC industry, downloads
articles from the past 12 months, and saves results to rss_industry.json.

── Setup ────────────────────────────────────────────────────────────────────────

Claude Desktop — add to ~/.claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "rss-news": {
          "command": "python",
          "args": ["/absolute/path/to/rss_mcp_server.py"]
        }
      }
    }

Claude Code — add to .claude/settings.json in your project:

    {
      "mcpServers": {
        "rss-news": {
          "command": "python",
          "args": ["/absolute/path/to/rss_mcp_server.py"]
        }
      }
    }

── Available tools ───────────────────────────────────────────────────────────────
    rss_industry_news     Find top 3 RSS feeds for a company's industry and download articles
    rss_load_results      Load and summarize a previously saved rss_industry.json
    rss_list_feeds        List all feeds in the catalog, grouped by industry category
"""

import json
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(__file__))
from rss_tool import RSS_CATALOG, get_industry_feeds, get_sic_category, run_rss_analysis

mcp = FastMCP("rss-news")


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def rss_industry_news(
    ticker: str,
    output_file: str = "rss_industry.json",
) -> str:
    """
    Find the top 3 RSS news feeds for a public company's industry and download
    all articles published in the past 12 months.

    Uses the company's EDGAR SIC code to identify the industry, selects the most
    relevant feeds from a curated catalog covering 25+ industry categories
    (Technology, Healthcare, Finance, Retail, Energy, Automotive, and more), then
    fetches every article available in each feed from the last year.

    Full results — feed metadata, article titles, URLs, publication dates, authors,
    and summaries — are saved to the output JSON file for downstream analysis.

    Note: RSS feeds typically retain the most recent 20–100 articles. Coverage of
    the full 12-month window depends on each feed's own retention policy.

    Args:
        ticker:      NYSE/NASDAQ ticker symbol (e.g. AAPL, MSFT, UAL)
        output_file: Path for the output JSON file (default "rss_industry.json")
    """
    try:
        result = run_rss_analysis(ticker=ticker, output_path=Path(output_file))
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error running RSS analysis: {type(e).__name__}: {e}"

    lines = [
        "RSS Industry News Analysis",
        f"  Company:   {result['company_name']} ({result['ticker']})",
        f"  SIC:       {result['sic']} — {result['sic_description']}",
        f"  Industry:  {result['industry_category']}",
        f"  Period:    {result['analysis_period']['start']} → {result['analysis_period']['end']}",
        "",
        "── Top 3 RSS Feeds ───────────────────────────────────────────────────────",
    ]

    for feed in result["feeds"]:
        icon = "✓" if feed.get("status") == "success" else "✗"
        lines.append(
            f"  {feed['rank']}. {icon} {feed.get('feed_title', feed['name']):<32} "
            f"{feed['article_count']:>4} articles"
        )
        lines.append(f"       {feed['url']}")
        if feed.get("status") == "error":
            lines.append(f"       Error: {feed.get('error', 'unknown')}")
        elif feed.get("articles"):
            dates = [a["published"] for a in feed["articles"] if a.get("published")]
            if dates:
                lines.append(
                    f"       Date range: {min(dates)[:10]} → {max(dates)[:10]}"
                )

    lines.append("")
    lines.append(f"  Total articles saved: {result['total_articles']:,}")

    if result["errors"]:
        lines.append(f"  Warnings: {len(result['errors'])} feed(s) failed to fetch")

    lines.append(f"\n✓ Full data saved to: {output_file}")
    lines.append(
        "  JSON contains article titles, URLs, publication dates, authors, and summaries."
    )

    return "\n".join(lines)


@mcp.tool()
def rss_load_results(file_path: str = "rss_industry.json") -> str:
    """
    Load and summarize a previously saved rss_industry.json analysis file.
    Shows metadata, per-feed article counts, date ranges, and sample headlines —
    without re-fetching any feeds.

    Args:
        file_path: Path to the saved JSON file (default "rss_industry.json")
    """
    p = Path(file_path)
    if not p.exists():
        return (
            f"File not found: {file_path}\n\n"
            "Run rss_industry_news first to generate the analysis."
        )

    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as e:
        return f"Error: {file_path} is not valid JSON — {e}"
    except Exception as e:
        return f"Error reading {file_path}: {e}"

    period = data.get("analysis_period", {})
    lines = [
        f"Saved RSS Analysis: {data.get('company_name', '?')} ({data.get('ticker', '?')})",
        f"  Generated:    {data.get('generated_at', 'unknown')}",
        f"  SIC:          {data.get('sic', '?')} — {data.get('sic_description', '?')}",
        f"  Industry:     {data.get('industry_category', '?')}",
        f"  Period:       {period.get('start', '?')} → {period.get('end', '?')}",
        f"  Total:        {data.get('total_articles', 0):,} articles across {len(data.get('feeds', []))} feeds",
        "",
        "── Feed Breakdown ────────────────────────────────────────────────────────",
    ]

    for feed in data.get("feeds", []):
        icon = "✓" if feed.get("status") == "success" else "✗"
        lines.append(
            f"\n  {feed.get('rank', '?')}. {icon} {feed.get('feed_title', feed.get('name', '?'))}"
        )
        lines.append(f"       URL:         {feed.get('url', '')}")
        lines.append(f"       Description: {feed.get('description', '')}")
        lines.append(f"       Articles:    {feed.get('article_count', 0)}")

        articles = feed.get("articles", [])
        if articles:
            dates = [a["published"] for a in articles if a.get("published")]
            if dates:
                lines.append(f"       Date range:  {min(dates)[:10]} → {max(dates)[:10]}")
            lines.append("       Sample headlines:")
            for art in articles[:5]:
                pub = (art.get("published") or "")[:10]
                title = (art.get("title") or "")[:90]
                lines.append(f"         [{pub}] {title}")
            if len(articles) > 5:
                lines.append(f"         … and {len(articles) - 5} more articles")
        elif feed.get("status") == "error":
            lines.append(f"       Error: {feed.get('error', 'unknown')}")

    errors = data.get("errors", [])
    if errors:
        lines.append(f"\n  Warnings: {len(errors)} feed(s) reported errors during fetch")

    return "\n".join(lines)


@mcp.tool()
def rss_list_feeds() -> str:
    """
    List all RSS feeds in the curated catalog, grouped by industry category.
    Shows the feed name, URL, and description for every entry.

    Use this to explore which industries are covered and which specific sources
    are used before running rss_industry_news on a ticker.
    """
    total = sum(len(v) for v in RSS_CATALOG.values())
    lines = [
        f"RSS Feed Catalog  ({total} feeds across {len(RSS_CATALOG)} industry categories)",
        "=" * 72,
    ]

    for category in sorted(RSS_CATALOG.keys()):
        feeds = RSS_CATALOG[category]
        lines.append(f"\n── {category} ({len(feeds)} feeds) ──")
        for i, feed in enumerate(feeds, 1):
            lines.append(f"  {i}. {feed['name']}")
            lines.append(f"     URL:  {feed['url']}")
            lines.append(f"     Desc: {feed['description']}")

    return "\n".join(lines)


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
