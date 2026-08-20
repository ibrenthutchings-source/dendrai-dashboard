#!/usr/bin/env python3
"""
Infrastructure Monitoring — pure config-introspection queries and
normalization. Mirrors scm_connectors.py's shape: no DB, no FastAPI, just
"go read the real system's current configuration and return a plain dict."

Scope note (from the negative-testing/continuous-monitoring analysis this
was built from): Railway is a PaaS — no SSH, no OS agent, immutable
containers. Classic OS-level CIS benchmarks (sshd config, auditd, file
perms) are neither auditable nor applicable here, so this module doesn't
attempt them. What IS auditable and meaningful:
  - Postgres: SQL-queryable config/roles/connection state (this file).
  - Railway platform/deployment drift: covered separately by
    railway_iaas_tool.py (P2a) via the Railway GraphQL API, not this file —
    a fundamentally different query surface (HTTP API vs. SQL connection).
"""

from __future__ import annotations

from typing import Optional

import version_baselines

try:
    import psycopg2
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


def _require_psycopg2():
    if not _HAS_PSYCOPG2:
        raise ImportError("psycopg2 library required")


def _require_requests():
    if not _HAS_REQUESTS:
        raise ImportError("requests library required: pip install requests")


def fetch_postgres_config(dsn: str, timeout: int = 10) -> dict:
    """
    Connect read-only to a Postgres instance and pull the handful of
    SQL-queryable facts CIS-style hardening checks care about. Every query
    here is a plain SELECT against system catalogs/settings — no writes, no
    superuser requirement beyond what's needed to read pg_roles/pg_stat_ssl
    (a role with pg_read_all_settings/pg_monitor is sufficient; a full
    superuser credential is NOT required and shouldn't be used for this).
    """
    _require_psycopg2()
    conn = psycopg2.connect(dsn, connect_timeout=timeout)
    try:
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()

        # Infra Vulnerability & Currency Posture, Phase 1: the one real
        # version-currency signal available from a bare DSN. `server_version`
        # is the human-readable string ("15.4 (Debian 15.4-1)"); server_version_num
        # is the stable numeric encoding (e.g. 150004) Postgres itself
        # guarantees comparable across versions — kept alongside the string
        # since normalize_postgres_compliance's currency check parses the
        # string, but the numeric form is the more robust field for anything
        # that needs a real comparison later.
        cur.execute("SHOW server_version")
        server_version = cur.fetchone()[0]
        cur.execute("SHOW server_version_num")
        server_version_num = cur.fetchone()[0]

        cur.execute("SHOW ssl")
        ssl_setting = cur.fetchone()[0]

        cur.execute("SHOW password_encryption")
        password_encryption = cur.fetchone()[0]

        cur.execute("SHOW log_connections")
        log_connections_setting = cur.fetchone()[0]

        cur.execute("SHOW row_security")
        row_security_setting = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM pg_roles WHERE rolsuper AND rolcanlogin")
        superuser_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM pg_roles WHERE rolcanlogin AND rolvaliduntil IS NULL AND rolsuper")
        superuser_no_expiry_count = cur.fetchone()[0]

        # pg_stat_ssl only lists currently-active backends — a point-in-time
        # sample, not a historical guarantee, but exactly the "is anything
        # connected in plaintext right now" signal a poll-based audit needs.
        try:
            cur.execute("SELECT COUNT(*) FROM pg_stat_ssl s JOIN pg_stat_activity a USING (pid) "
                        "WHERE NOT s.ssl AND a.backend_type = 'client backend'")
            unencrypted_connection_count = cur.fetchone()[0]
        except Exception:
            unencrypted_connection_count = None  # pg_stat_ssl may be restricted

        cur.execute("SELECT extname FROM pg_extension ORDER BY extname")
        extensions = [r[0] for r in cur.fetchall()]

        cur.close()
        return {
            "server_version": server_version,
            "server_version_num": server_version_num,
            "ssl_setting": ssl_setting,
            "password_encryption": password_encryption,
            "log_connections_setting": log_connections_setting,
            "row_security_setting": row_security_setting,
            "superuser_count": superuser_count,
            "superuser_no_expiry_count": superuser_no_expiry_count,
            "unencrypted_connection_count": unencrypted_connection_count,
            "extensions": extensions,
        }
    finally:
        conn.close()


def normalize_postgres_compliance(raw: dict) -> dict:
    """Same normalize_*_compliance idiom as scm_connectors.py — booleans and
    counts a Rego module can reference directly as input.event.<field>.
    version_current/latest_known_version come from version_baselines.py, NOT
    OSV.dev — OSV has no PostgreSQL/generic-DB-engine ecosystem to enrich a
    bare version string against (see that module's docstring). Both are
    None when the version doesn't match anything in the curated baseline
    table — an honest "don't know" rather than a guessed answer."""
    version_current, latest_known_version = version_baselines.check_currency(
        "postgresql", raw.get("server_version") or "",
    )
    return {
        "server_version": raw.get("server_version"),
        "server_version_num": raw.get("server_version_num"),
        "version_current": version_current,
        "latest_known_version": latest_known_version,
        "ssl_enabled": str(raw.get("ssl_setting", "")).lower() == "on",
        "password_encryption": raw.get("password_encryption"),
        "log_connections": str(raw.get("log_connections_setting", "")).lower() == "on",
        "row_security_enabled": str(raw.get("row_security_setting", "")).lower() == "on",
        "superuser_count": int(raw.get("superuser_count") or 0),
        "superuser_no_expiry_count": int(raw.get("superuser_no_expiry_count") or 0),
        "unencrypted_connection_count": raw.get("unencrypted_connection_count"),
        "extension_count": len(raw.get("extensions") or []),
        "extensions": raw.get("extensions") or [],
    }


# Controls tracked for drift detection — same idiom as
# mcp_governance._BOOL_CONTROLS_GOOD_WHEN_TRUE / diff_compliance, so a future
# "infra config drift" table can reuse scm_connectors.diff_compliance
# directly against this normalized shape without writing a parallel differ.
BOOL_CONTROLS_GOOD_WHEN_TRUE = ("ssl_enabled", "log_connections", "row_security_enabled")
INT_CONTROLS_GOOD_WHEN_LOW = ("superuser_count", "superuser_no_expiry_count")


# ── Railway platform/deployment drift ─────────────────────────────────────────
# GraphQL endpoint + field names below verified against the real Railway API
# (https://backboard.railway.com/graphql/v2) during development — a request
# without a browser-like User-Agent gets a Cloudflare 403 (error code 1010),
# not an auth failure, so the User-Agent header below is load-bearing, not
# decorative.

_RAILWAY_GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"

_RAILWAY_ENV_QUERY = """
query GetEnv($envId: String!) {
  environment(id: $envId) {
    serviceInstances {
      edges {
        node {
          serviceId
          serviceName
          domains {
            serviceDomains { domain }
            customDomains { domain }
          }
          latestDeployment {
            id
            status
            meta
          }
        }
      }
    }
  }
}
"""


def fetch_railway_environment(api_token: str, environment_id: str, timeout: int = 20) -> list[dict]:
    """One node per service instance in the environment: serviceId,
    serviceName, domains (service + custom), and latestDeployment (id,
    status, meta — meta is a JSON blob including imageDigest)."""
    _require_requests()
    resp = requests.post(
        _RAILWAY_GRAPHQL_URL,
        json={"query": _RAILWAY_ENV_QUERY, "variables": {"envId": environment_id}},
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "User-Agent": "dendrai-infrastructure-monitoring",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Railway GraphQL error: {body['errors']}")
    env = (body.get("data") or {}).get("environment")
    if not env:
        raise RuntimeError(f"Environment '{environment_id}' not found or not accessible with this token")
    return [e["node"] for e in env["serviceInstances"]["edges"]]


def normalize_railway_service_compliance(node: dict, approved_public_service_ids: set,
                                          known_image_digests: Optional[set]) -> dict:
    """
    Per-service compliance dict, same normalize_*_compliance idiom as the
    Postgres/SCM connectors.

    known_image_digests=None (or empty) means "no pipeline attestation data
    has been ingested at all yet" — in that state image_digest_mismatch is
    reported as None (unknown), never True, mirroring attestation.py's
    Cosign-verification principle: never fabricate a negative finding when
    there's genuinely nothing to compare against. Once at least one
    CI pipeline has POSTed an attestation (evidence_endpoints.py's
    /evidence/attestation), a currently-deployed digest that doesn't match
    ANY attested build becomes a real, meaningful finding.
    """
    domains = node.get("domains") or {}
    has_public_domain = bool(domains.get("serviceDomains") or domains.get("customDomains"))
    service_id = node.get("serviceId")
    deployment = node.get("latestDeployment") or {}
    meta = deployment.get("meta") or {}
    image_digest = meta.get("imageDigest")

    unexpected_public_domain = has_public_domain and service_id not in (approved_public_service_ids or set())

    if not known_image_digests or not image_digest:
        image_digest_mismatch = None
    else:
        image_digest_mismatch = image_digest not in known_image_digests

    return {
        "service_id": service_id,
        "service_name": node.get("serviceName"),
        "has_public_domain": has_public_domain,
        "unexpected_public_domain": unexpected_public_domain,
        "image_digest": image_digest,
        "image_digest_mismatch": image_digest_mismatch,
        "deployment_status": deployment.get("status"),
    }


def evaluate_railway_severity(compliance: dict) -> str:
    if compliance.get("unexpected_public_domain"):
        return "HIGH"
    if compliance.get("image_digest_mismatch") is True:
        return "HIGH"
    return "INFO"


def evaluate_severity(compliance: dict) -> str:
    """CRITICAL if SSL isn't enforced or any live connection is unencrypted
    (both mean credentials/data can travel in plaintext right now); HIGH for
    weak password hashing or excess superusers; MEDIUM for a known-outdated
    server version (currency, not itself a live plaintext-exposure risk —
    ranked below the HIGH config gaps, above a clean INFO); INFO otherwise.
    version_current is None (not False) when version_baselines.py has no
    opinion — that must never trip this check, only an explicit False does."""
    if not compliance.get("ssl_enabled"):
        return "CRITICAL"
    if (compliance.get("unencrypted_connection_count") or 0) > 0:
        return "CRITICAL"
    if compliance.get("password_encryption") != "scram-sha-256":
        return "HIGH"
    if compliance.get("superuser_count", 0) > 2:
        return "HIGH"
    if not compliance.get("log_connections"):
        return "HIGH"
    if compliance.get("version_current") is False:
        return "MEDIUM"
    return "INFO"
