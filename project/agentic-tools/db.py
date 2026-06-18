#!/usr/bin/env python3
"""
PostgreSQL persistence layer for Dendrai tool outputs.

Reads DATABASE_URL from the environment (or .env via dotenv).
All tool results are stored in a single `tool_results` table using JSONB
so the complex nested structures from each tool require no schema migration.

Usage:
    Set DATABASE_URL=postgresql://user:pass@host:5432/dbname in .env

    from db import init_db, save_result, get_results, is_available
    init_db()                                          # call once at startup
    save_result("risk_loop", "AAPL", "Apple Inc.", result_dict)
    rows = get_results("risk_loop", ticker="AAPL", limit=5)
"""

import json
import logging
import os
from typing import Optional

try:
    import psycopg2
    from psycopg2 import pool as pg_pool
    from psycopg2.extras import Json
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

logger = logging.getLogger(__name__)

_pool: Optional["pg_pool.ThreadedConnectionPool"] = None

_DDL = """
CREATE TABLE IF NOT EXISTS tool_results (
    id           SERIAL PRIMARY KEY,
    tool_name    VARCHAR(64)  NOT NULL,
    ticker       VARCHAR(16),
    company_name VARCHAR(255),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    data         JSONB        NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_results_tool_ticker
    ON tool_results (tool_name, ticker);
CREATE INDEX IF NOT EXISTS idx_tool_results_created_at
    ON tool_results (created_at DESC);
"""


def init_db() -> bool:
    """
    Initialize the thread-safe connection pool and create tables.
    Call once at application startup.
    Returns True when the database is ready, False if unconfigured or unavailable.
    """
    global _pool

    if not _HAS_PSYCOPG2:
        logger.warning("psycopg2 not installed — database persistence disabled. "
                       "Run: pip install psycopg2-binary")
        return False

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        logger.info("DATABASE_URL not set — database persistence disabled")
        return False

    try:
        _pool = pg_pool.ThreadedConnectionPool(1, 10, dsn=url)
        conn = _pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(_DDL)
            conn.commit()
        finally:
            _pool.putconn(conn)
        logger.info("PostgreSQL database initialized")
        return True
    except Exception as exc:
        logger.error("Database init failed: %s", exc)
        _pool = None
        return False


def is_available() -> bool:
    """Return True when a live connection pool is configured."""
    return _pool is not None


def save_result(
    tool_name: str,
    ticker: str,
    company_name: str,
    data: dict,
) -> Optional[int]:
    """
    Persist a tool result to the database.
    Returns the new row id, or None if the database is unavailable.
    Failures are logged but never raised so callers don't break.
    """
    if _pool is None:
        return None
    try:
        conn = _pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tool_results (tool_name, ticker, company_name, data)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (tool_name, ticker.upper(), company_name, Json(data)),
                )
                row_id: int = cur.fetchone()[0]
            conn.commit()
            logger.debug("Saved %s result for %s → id=%d", tool_name, ticker, row_id)
            return row_id
        finally:
            _pool.putconn(conn)
    except Exception as exc:
        logger.error("save_result failed (%s / %s): %s", tool_name, ticker, exc)
        return None


def get_results(
    tool_name: str,
    ticker: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """
    Retrieve recent results for a given tool, optionally filtered by ticker.
    Returns an empty list if the database is unavailable.
    """
    if _pool is None:
        return []
    try:
        conn = _pool.getconn()
        try:
            with conn.cursor() as cur:
                if ticker:
                    cur.execute(
                        """
                        SELECT id, tool_name, ticker, company_name, created_at, data
                        FROM tool_results
                        WHERE tool_name = %s AND ticker = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (tool_name, ticker.upper(), limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, tool_name, ticker, company_name, created_at, data
                        FROM tool_results
                        WHERE tool_name = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (tool_name, limit),
                    )
                rows = cur.fetchall()
        finally:
            _pool.putconn(conn)

        return [
            {
                "id": r[0],
                "tool_name": r[1],
                "ticker": r[2],
                "company_name": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
                "data": r[5],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("get_results failed (%s / %s): %s", tool_name, ticker, exc)
        return []


def list_tickers(tool_name: str) -> list[str]:
    """Return distinct tickers that have stored results for the given tool."""
    if _pool is None:
        return []
    try:
        conn = _pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ticker
                    FROM tool_results
                    WHERE tool_name = %s AND ticker IS NOT NULL
                    ORDER BY ticker
                    """,
                    (tool_name,),
                )
                return [r[0] for r in cur.fetchall()]
        finally:
            _pool.putconn(conn)
    except Exception as exc:
        logger.error("list_tickers failed (%s): %s", tool_name, exc)
        return []
