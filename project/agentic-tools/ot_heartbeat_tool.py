#!/usr/bin/env python3
"""
Generic OT/SCADA device heartbeat poll-connector adapter
(observability.poll_connectors, connector_type='ot_heartbeat').

Technology Risk Pipeline: OT/SCADA keep-alive monitoring. Deliberately thin —
this checks a plain HTTP keep-alive/health endpoint per device, not a real
SCADA fieldbus protocol (Modbus/OPC-UA/DNP3). Full protocol support is out of
scope until a specific OT vendor/system is named (see the Multi-Domain
Continuous Risk Pipeline plan's Roadmap section); most OT gateways/historians
do expose a simple HTTP health check even when the field devices behind them
speak a real industrial protocol, which is what this polls.

Same pull_events()/test_connection() poll-connector contract as the other
adapters — one event per configured device per poll tick, so `since` is
unused (a device either answered this tick or it didn't).

Required per-connector config (set via the app UI, not env vars):
  extra_config: {
    "devices": "pump-01=http://10.0.1.5/health,valve-12=http://10.0.1.9/health"
      (comma-separated name=url pairs),
    "timeout_s": "5"  (optional, default 5)
  }
  credentials: {} (none needed — OT gateway health endpoints are typically
    unauthenticated on an isolated OT network segment; if a target requires
    auth, put it in the URL as HTTP basic auth, e.g. http://user:pass@host/health)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

logger = logging.getLogger(__name__)


def _require_requests() -> None:
    if not _HAS_REQUESTS:
        raise ImportError("requests library required: pip install requests")


def _parse_devices(devices_str: str) -> list[tuple[str, str]]:
    devices = []
    for pair in (devices_str or "").split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, url = pair.split("=", 1)
        if name.strip() and url.strip():
            devices.append((name.strip(), url.strip()))
    return devices


def _check_device(name: str, url: str, timeout_s: float) -> dict:
    _require_requests()
    try:
        resp = requests.get(url, timeout=timeout_s)
        alive = resp.ok
        detail = {"status_code": resp.status_code}
    except Exception as exc:
        alive = False
        detail = {"error": f"{type(exc).__name__}: {exc}"}
    return {"name": name, "url": url, "alive": alive, "detail": detail}


def _audit_once(credentials: dict, extra_config: dict) -> list[dict]:
    extra_config = extra_config or {}
    devices = _parse_devices(extra_config.get("devices") or "")
    if not devices:
        raise ValueError("extra_config.devices is required (comma-separated name=url pairs)")
    timeout_s = float(extra_config.get("timeout_s") or 5)
    return [_check_device(name, url, timeout_s) for name, url in devices]


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict,
                 since) -> list[dict]:
    """One heartbeat-check event per configured device per poll tick — a
    missed heartbeat on this tick is the finding itself, not a state
    accumulated across ticks (same point-in-time idiom as the other
    Infrastructure/Technology connectors)."""
    checks = _audit_once(credentials, extra_config)
    today = datetime.now(timezone.utc).date().isoformat()
    events = []
    for c in checks:
        severity = "INFO" if c["alive"] else "HIGH"
        events.append({
            "event_id":    f"ot-heartbeat:{c['name']}:{today}",
            "event_type":  "infrastructure_finding",
            "actor":       "ot_heartbeat_tool",
            "action":      "ot_heartbeat_check",
            "resource":    c["name"],
            "severity":    severity,
            "raw_payload": {
                "infrastructure_finding": not c["alive"],
                "check_id": "ot-heartbeat-v1",
                "infra_compliance": {"alive": c["alive"], **c["detail"]},
            },
        })
    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    try:
        checks = _audit_once(credentials, extra_config)
        alive = sum(1 for c in checks if c["alive"])
        return True, f"Checked {len(checks)} device(s) — {alive} responding"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return _HAS_REQUESTS
