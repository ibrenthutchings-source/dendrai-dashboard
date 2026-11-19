#!/usr/bin/env python3
"""
TLS certificate expiry poll-connector adapter
(observability.poll_connectors, connector_type='tls_cert').

Infrastructure Vulnerability & Currency Posture, Phase 1: the cheapest real
"networking" coverage available — a public TLS handshake needs no
credentials at all, unlike every other infra connector in this codebase.
Same pull_events()/test_connection()/is_configured() contract as
ot_heartbeat_tool.py, which this deliberately mirrors (comma-separated
name=host:port pairs in extra_config, one check per endpoint per poll tick).

Two consumers of _audit_once(), same "one real check, two views" split
postgres_cis_tool.py/iaas_connectors.py already establish between config
data and system_telemetry events:
  - pull_events() below -> observability.system_telemetry, for the existing
    Infrastructure Posture matrix (GET /infra-monitoring/results).
  - infra_asset_sweep.py calls _audit_once() directly to upsert
    observability.infra_assets rows (asset_type='certificate') and mark them
    assessed — the asset-registry half of Phase 1.

Required per-connector config (set via the app UI, not env vars):
  extra_config: {
    "endpoints": "api=api.example.com:443,vpn=vpn.example.com:443"
      (comma-separated name=host:port pairs),
    "warn_days": "30"  (optional, default 30 — days-to-expiry that trips HIGH)
  }
  credentials: {} (none needed — reads only the certificate a server
    presents during the TLS handshake, the same thing any browser sees)
"""

from __future__ import annotations

import logging
import socket
import ssl
from datetime import datetime, timezone
from typing import Optional

try:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False

logger = logging.getLogger(__name__)

_DEFAULT_WARN_DAYS = 30
_DEFAULT_TIMEOUT_S = 8


def _require_cryptography() -> None:
    if not _HAS_CRYPTOGRAPHY:
        raise ImportError("cryptography library required: pip install cryptography")


def _parse_endpoints(endpoints_str: str) -> list[tuple[str, str, int]]:
    """Returns (name, host, port) triples. Malformed entries (no '=', no
    ':port', a non-numeric port) are skipped rather than raising — one typo
    in a long endpoint list shouldn't take down every other check."""
    endpoints = []
    for pair in (endpoints_str or "").split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, hostport = pair.split("=", 1)
        name, hostport = name.strip(), hostport.strip()
        if not name or ":" not in hostport:
            continue
        host, _, port_str = hostport.rpartition(":")
        if not host or not port_str.isdigit():
            continue
        endpoints.append((name, host, int(port_str)))
    return endpoints


def _check_endpoint(name: str, host: str, port: int, warn_days: int, timeout_s: float) -> dict:
    """Connects, reads the leaf certificate's real expiry via a genuine TLS
    handshake (not just an OS trust-store check — a customer's internal CA
    is validated the same way a browser would need to), and classifies it.
    Any connection/parse failure is reported as its own state
    (reachable=False) rather than silently treated as "no cert" — an
    unreachable endpoint and a valid-forever endpoint must never look alike."""
    _require_cryptography()
    result = {
        "name": name, "host": host, "port": port, "reachable": False,
        "not_after": None, "days_to_expiry": None, "subject": None, "issuer": None, "error": None,
    }
    try:
        ctx = ssl.create_default_context()
        # Certificate CONTENT (expiry, subject, issuer) is read regardless of
        # trust-chain validity — an expired or self-signed cert is exactly
        # the finding this check exists to surface, not something to hide by
        # failing the handshake outright.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls_sock:
                der = tls_sock.getpeercert(binary_form=True)
        cert = x509.load_der_x509_certificate(der, default_backend())
        not_after = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after.replace(tzinfo=timezone.utc)
        days_to_expiry = (not_after - datetime.now(timezone.utc)).days
        result.update({
            "reachable": True,
            "not_after": not_after.isoformat(),
            "days_to_expiry": days_to_expiry,
            "subject": cert.subject.rfc4514_string(),
            "issuer": cert.issuer.rfc4514_string(),
        })
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _severity_for(check: dict, warn_days: int) -> str:
    if not check["reachable"]:
        return "MEDIUM"  # can't assess — a real gap, but not itself proof of an expired cert
    days = check["days_to_expiry"]
    if days is None:
        return "MEDIUM"
    if days < 0:
        return "CRITICAL"
    if days <= warn_days:
        return "HIGH"
    return "INFO"


def _audit_once(credentials: dict, extra_config: dict) -> dict:
    """Returns {"warn_days": int, "checks": [...]} — checks is the list
    _check_endpoint() produces, one per configured endpoint."""
    extra_config = extra_config or {}
    endpoints = _parse_endpoints(extra_config.get("endpoints") or "")
    if not endpoints:
        raise ValueError("extra_config.endpoints is required (comma-separated name=host:port pairs)")
    warn_days = int(extra_config.get("warn_days") or _DEFAULT_WARN_DAYS)
    checks = [_check_endpoint(name, host, port, warn_days, _DEFAULT_TIMEOUT_S) for name, host, port in endpoints]
    return {"warn_days": warn_days, "checks": checks}


def pull_events(base_url: Optional[str], credentials: dict, extra_config: dict, since) -> list[dict]:
    """One certificate-expiry event per configured endpoint per poll tick —
    day-scoped event_id for dedup, same idiom as postgres_cis_tool.py."""
    audit = _audit_once(credentials, extra_config)
    today = datetime.now(timezone.utc).date().isoformat()
    events = []
    for c in audit["checks"]:
        severity = _severity_for(c, audit["warn_days"])
        events.append({
            "event_id":    f"tls-cert:{c['name']}:{today}",
            "event_type":  "infrastructure_finding",
            "actor":       "tls_cert_tool",
            "action":      "cert_expiry_check",
            "resource":    c["name"],
            "severity":    severity,
            "raw_payload": {
                "infrastructure_finding": severity in ("HIGH", "CRITICAL", "MEDIUM"),
                "check_id": "tls-cert-v1",
                "infra_compliance": {
                    "cert_reachable": c["reachable"],
                    "cert_not_after": c["not_after"],
                    "cert_days_to_expiry": c["days_to_expiry"],
                    "cert_common_name": c["subject"],
                    "cert_issuer": c["issuer"],
                    "cert_error": c["error"],
                },
            },
        })
    return events


def test_connection(base_url: Optional[str], credentials: dict, extra_config: dict) -> tuple[bool, str]:
    try:
        audit = _audit_once(credentials, extra_config)
        reachable = sum(1 for c in audit["checks"] if c["reachable"])
        return True, f"Checked {len(audit['checks'])} endpoint(s) — {reachable} reachable"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def is_configured(base_url: Optional[str] = None) -> bool:
    return _HAS_CRYPTOGRAPHY
