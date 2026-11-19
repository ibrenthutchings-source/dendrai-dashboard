#!/usr/bin/env python3
"""
Unit tests for Infrastructure Monitoring (P1b): iaas_connectors.py's
normalization/severity logic and the infrastructure_monitoring Rego module
(via pac_endpoints' heuristic evaluator — every environment has it). No DB,
no network, no real Postgres connection.

    pytest test_infrastructure_monitoring.py -v
"""

from __future__ import annotations

import iaas_connectors
import pac_endpoints


# ── iaas_connectors.normalize_postgres_compliance ───────────────────────────

def test_normalize_postgres_compliance_fully_hardened():
    raw = {
        "ssl_setting": "on", "password_encryption": "scram-sha-256",
        "log_connections_setting": "on", "row_security_setting": "on",
        "superuser_count": 1, "superuser_no_expiry_count": 0,
        "unencrypted_connection_count": 0, "extensions": ["pgcrypto"],
    }
    c = iaas_connectors.normalize_postgres_compliance(raw)
    assert c["ssl_enabled"] is True
    assert c["password_encryption"] == "scram-sha-256"
    assert c["log_connections"] is True
    assert c["superuser_count"] == 1
    assert c["extension_count"] == 1


def test_normalize_postgres_compliance_ssl_off():
    raw = {"ssl_setting": "off", "password_encryption": "scram-sha-256",
           "log_connections_setting": "on", "row_security_setting": "on",
           "superuser_count": 1, "unencrypted_connection_count": 0, "extensions": []}
    c = iaas_connectors.normalize_postgres_compliance(raw)
    assert c["ssl_enabled"] is False


def test_normalize_postgres_compliance_handles_none_gracefully():
    c = iaas_connectors.normalize_postgres_compliance({})
    assert c["ssl_enabled"] is False
    assert c["superuser_count"] == 0
    assert c["extensions"] == []


# ── iaas_connectors.evaluate_severity ────────────────────────────────────────

def test_evaluate_severity_ssl_disabled_is_critical():
    c = iaas_connectors.normalize_postgres_compliance({"ssl_setting": "off"})
    assert iaas_connectors.evaluate_severity(c) == "CRITICAL"


def test_evaluate_severity_unencrypted_live_connection_is_critical():
    raw = {"ssl_setting": "on", "password_encryption": "scram-sha-256",
           "log_connections_setting": "on", "unencrypted_connection_count": 3}
    c = iaas_connectors.normalize_postgres_compliance(raw)
    assert iaas_connectors.evaluate_severity(c) == "CRITICAL"


def test_evaluate_severity_weak_password_hashing_is_high():
    raw = {"ssl_setting": "on", "password_encryption": "md5",
           "log_connections_setting": "on", "unencrypted_connection_count": 0, "superuser_count": 1}
    c = iaas_connectors.normalize_postgres_compliance(raw)
    assert iaas_connectors.evaluate_severity(c) == "HIGH"


def test_evaluate_severity_superuser_sprawl_is_high():
    raw = {"ssl_setting": "on", "password_encryption": "scram-sha-256",
           "log_connections_setting": "on", "unencrypted_connection_count": 0, "superuser_count": 5}
    c = iaas_connectors.normalize_postgres_compliance(raw)
    assert iaas_connectors.evaluate_severity(c) == "HIGH"


def test_evaluate_severity_fully_hardened_is_info():
    raw = {"ssl_setting": "on", "password_encryption": "scram-sha-256",
           "log_connections_setting": "on", "unencrypted_connection_count": 0, "superuser_count": 1}
    c = iaas_connectors.normalize_postgres_compliance(raw)
    assert iaas_connectors.evaluate_severity(c) == "INFO"


# ── infrastructure_monitoring Rego (heuristic evaluator) ────────────────────

def _rego() -> str:
    return pac_endpoints._REGO_DEFAULTS["infrastructure_monitoring"]


_COMPLIANT_EVENT = {
    "type": "INFRASTRUCTURE_FINDING", "resource": "primary-db",
    "ssl_enabled": True, "password_encryption": "scram-sha-256",
    "log_connections": True, "superuser_count": 1, "unencrypted_connection_count": 0,
}


def test_infra_rego_fires_on_ssl_disabled():
    input_event = {"event": {**_COMPLIANT_EVENT, "ssl_enabled": False}}
    result = pac_endpoints.evaluate_policy_event(_rego(), input_event)
    fired_rules = {r["rule"] for r in result["rules_fired"]}
    assert "deny_db_config" in fired_rules


def test_infra_rego_silent_when_fully_compliant():
    result = pac_endpoints.evaluate_policy_event(_rego(), {"event": _COMPLIANT_EVENT})
    assert result["rules_fired"] == []


def test_infra_rego_registered_as_default():
    assert "infrastructure_monitoring" in pac_endpoints._REGO_DEFAULTS
    assert "package controls.infrastructure.monitoring" in _rego()


# ── iaas_connectors.normalize_railway_service_compliance ────────────────────

def test_railway_compliance_flags_unapproved_public_domain():
    node = {"serviceId": "svc-1", "serviceName": "internal-worker",
            "domains": {"serviceDomains": [{"domain": "internal-worker-uat.up.railway.app"}], "customDomains": []},
            "latestDeployment": {"status": "SUCCESS", "meta": {}}}
    c = iaas_connectors.normalize_railway_service_compliance(node, approved_public_service_ids=set(), known_image_digests=None)
    assert c["has_public_domain"] is True
    assert c["unexpected_public_domain"] is True


def test_railway_compliance_approved_public_domain_not_flagged():
    node = {"serviceId": "svc-1", "serviceName": "public-api",
            "domains": {"serviceDomains": [{"domain": "public-api.up.railway.app"}], "customDomains": []},
            "latestDeployment": {"status": "SUCCESS", "meta": {}}}
    c = iaas_connectors.normalize_railway_service_compliance(node, approved_public_service_ids={"svc-1"}, known_image_digests=None)
    assert c["unexpected_public_domain"] is False


def test_railway_compliance_no_domain_never_flagged():
    node = {"serviceId": "svc-2", "serviceName": "worker",
            "domains": {"serviceDomains": [], "customDomains": []},
            "latestDeployment": {"status": "SUCCESS", "meta": {}}}
    c = iaas_connectors.normalize_railway_service_compliance(node, approved_public_service_ids=set(), known_image_digests=None)
    assert c["has_public_domain"] is False
    assert c["unexpected_public_domain"] is False


def test_railway_compliance_no_attestations_is_unknown_not_mismatch():
    """The core safety property: with zero pipeline attestations on record,
    image_digest_mismatch must be None (unknown), never True — otherwise
    every deployment in a fresh environment would flag as suspicious."""
    node = {"serviceId": "svc-1", "serviceName": "app",
            "domains": {"serviceDomains": [], "customDomains": []},
            "latestDeployment": {"status": "SUCCESS", "meta": {"imageDigest": "sha256:abc"}}}
    c = iaas_connectors.normalize_railway_service_compliance(node, approved_public_service_ids=set(), known_image_digests=set())
    assert c["image_digest_mismatch"] is None


def test_railway_compliance_digest_matches_known_attestation():
    node = {"serviceId": "svc-1", "serviceName": "app",
            "domains": {"serviceDomains": [], "customDomains": []},
            "latestDeployment": {"status": "SUCCESS", "meta": {"imageDigest": "sha256:abc"}}}
    c = iaas_connectors.normalize_railway_service_compliance(node, approved_public_service_ids=set(), known_image_digests={"sha256:abc"})
    assert c["image_digest_mismatch"] is False


def test_railway_compliance_digest_not_in_known_attestations_is_true():
    node = {"serviceId": "svc-1", "serviceName": "app",
            "domains": {"serviceDomains": [], "customDomains": []},
            "latestDeployment": {"status": "SUCCESS", "meta": {"imageDigest": "sha256:unattested"}}}
    c = iaas_connectors.normalize_railway_service_compliance(node, approved_public_service_ids=set(), known_image_digests={"sha256:abc"})
    assert c["image_digest_mismatch"] is True


def test_railway_severity_public_domain_is_high():
    c = {"unexpected_public_domain": True, "image_digest_mismatch": None}
    assert iaas_connectors.evaluate_railway_severity(c) == "HIGH"


def test_railway_severity_digest_mismatch_is_high():
    c = {"unexpected_public_domain": False, "image_digest_mismatch": True}
    assert iaas_connectors.evaluate_railway_severity(c) == "HIGH"


def test_railway_severity_unknown_digest_is_info_not_high():
    c = {"unexpected_public_domain": False, "image_digest_mismatch": None}
    assert iaas_connectors.evaluate_railway_severity(c) == "INFO"


# ── infrastructure_monitoring Rego: Railway rules ───────────────────────────

def test_infra_rego_fires_on_unexpected_public_domain():
    result = pac_endpoints.evaluate_policy_event(_rego(), {"event": {
        "type": "INFRASTRUCTURE_FINDING", "resource": "internal-worker", "unexpected_public_domain": True,
    }})
    fired_rules = {r["rule"] for r in result["rules_fired"]}
    assert "deny_railway_config" in fired_rules


def test_infra_rego_silent_when_digest_mismatch_unknown():
    result = pac_endpoints.evaluate_policy_event(_rego(), {"event": {
        "type": "INFRASTRUCTURE_FINDING", "resource": "app",
        "image_digest_mismatch": None, "unexpected_public_domain": False,
    }})
    assert result["rules_fired"] == []
