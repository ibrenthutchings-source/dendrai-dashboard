#!/usr/bin/env python3
"""
SAP HANA Audit Log Connector

Pulls audit trail events from SAP HANA's native audit log (SYS.AUDIT_LOG),
queried directly via SQL (HANA's audit trail is not exposed as a REST API —
unlike the other connectors in this framework, this one is a database
client, not an HTTP client).

Required per-connector config (set via the app UI — Dendrai UBO Configuration
screen — not env vars):
  base_url       HANA host, e.g. "myhana.example.com"
  extra_config:  {"port": 30015}  (optional, defaults to HANA's default tenant/instance 00)
  credentials:   {"username": ..., "password": ...}

Connector adapter interface: pull_events(), test_connection().
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from hdbcli import dbapi
    _HAS_HDBCLI = True
except ImportError:
    _HAS_HDBCLI = False


def _connect(base_url: Optional[str], credentials: dict, extra_config: dict):
    if not _HAS_HDBCLI:
        raise ImportError("hdbcli library required: pip install hdbcli")
    if not base_url:
        raise ValueError("SAP HANA host (base_url) is required")
    port = int((extra_config or {}).get("port") or 30015)
    return dbapi.connect(
        address=base_url,
        port=port,
        user=credentials.get("username"),
        password=credentials.get("password"),
        connecttimeout=10000,
    )


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since: Optional[datetime]) -> list[dict]:
    """Query SYS.AUDIT_LOG for entries since `since`, normalized to the
    uniform connector event shape (event_id, event_type, actor, action,
    resource, severity, raw_payload) that connector_poller.py hands to
    mcp_governance._detect_system_flags/_ingest_system_event."""
    conn = _connect(base_url, credentials, extra_config)
    try:
        cur = conn.cursor()
        since_str = (since or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            SELECT TIMESTAMP, HOST, PORT, USER_NAME, ACTION_TYPE, OBJECT_NAME,
                   AUDIT_POLICY_NAME, EVENT_STATUS, CLIENT_IP, CONNECTION_ID
            FROM SYS.AUDIT_LOG
            WHERE TIMESTAMP > ?
            ORDER BY TIMESTAMP
            LIMIT 500
            """,
            (since_str,),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()

    events = []
    for r in rows:
        event_id = f"{r.get('CONNECTION_ID', '')}:{r.get('TIMESTAMP', '')}:{r.get('ACTION_TYPE', '')}"
        events.append({
            "event_id":    event_id,
            "event_type":  r.get("ACTION_TYPE") or "hana_audit_event",
            "actor":       r.get("USER_NAME") or "",
            "action":      r.get("ACTION_TYPE") or "",
            "resource":    r.get("OBJECT_NAME") or "",
            "severity":    "CRITICAL" if (r.get("EVENT_STATUS") or "").upper() == "FAILED" else "INFO",
            "raw_payload": {k: str(v) for k, v in r.items()},
        })
    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    """Verify connectivity with HANA's standard SELECT 1 FROM DUMMY check."""
    try:
        conn = _connect(base_url, credentials, extra_config)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM DUMMY")
            cur.fetchone()
        finally:
            conn.close()
        return True, "Connected to SAP HANA — SELECT 1 FROM DUMMY succeeded"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return _HAS_HDBCLI and bool(base_url)
