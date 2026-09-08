#!/usr/bin/env python3
"""
Unit tests for report_delivery_endpoints.py's pure functions: the SSRF
host guard, payload shaping per payload_format, and the auth-header/HMAC
signing helpers. No DB or network required.

    pytest test_report_delivery_endpoints.py -v
"""
from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi import HTTPException

import report_delivery_endpoints as rd


# ── _validate_destination_url (SSRF guard) ──────────────────────────────────

def test_validate_destination_url_allows_public_https():
    rd._validate_destination_url("https://hooks.example.com/webhooks/dendrai")  # no raise


@pytest.mark.parametrize("url", [
    "http://localhost/webhook",
    "http://127.0.0.1:8080/hook",
    "http://10.0.0.5/hook",
    "http://172.16.0.1/hook",
    "http://192.168.1.1/hook",
    "http://169.254.169.254/latest/meta-data",  # cloud metadata endpoint
    "http://0.0.0.0/hook",
    "http://[::1]/hook",
])
def test_validate_destination_url_blocks_private_hosts(url):
    with pytest.raises(HTTPException) as exc:
        rd._validate_destination_url(url)
    assert exc.value.status_code == 422


def test_validate_destination_url_blocks_non_http_scheme():
    with pytest.raises(HTTPException) as exc:
        rd._validate_destination_url("ftp://example.com/hook")
    assert exc.value.status_code == 422


def test_validate_destination_url_allows_172_31_but_blocks_172_16_to_31_range():
    # 172.16.0.0/12 is the full private range (172.16.x - 172.31.x)
    with pytest.raises(HTTPException):
        rd._validate_destination_url("http://172.31.255.1/hook")
    # 172.32.x is outside RFC-1918 and should NOT be blocked
    rd._validate_destination_url("http://172.32.0.1/hook")  # no raise


# ── _validate_credentials ────────────────────────────────────────────────────

def test_validate_credentials_none_requires_nothing():
    rd._validate_credentials("none", None)  # no raise


def test_validate_credentials_bearer_requires_token():
    with pytest.raises(HTTPException):
        rd._validate_credentials("bearer", {})
    rd._validate_credentials("bearer", {"token": "abc"})  # no raise


def test_validate_credentials_basic_requires_username_and_password():
    with pytest.raises(HTTPException):
        rd._validate_credentials("basic", {"username": "u"})
    rd._validate_credentials("basic", {"username": "u", "password": "p"})  # no raise


def test_validate_credentials_hmac_requires_secret():
    with pytest.raises(HTTPException):
        rd._validate_credentials("hmac", {})
    rd._validate_credentials("hmac", {"secret": "s3cr3t"})  # no raise


# ── _validate_artifact_types ─────────────────────────────────────────────────

def test_validate_artifact_types_rejects_unknown_values():
    with pytest.raises(HTTPException):
        rd._validate_artifact_types(["loop_report", "bogus"])


def test_validate_artifact_types_rejects_empty_list():
    with pytest.raises(HTTPException):
        rd._validate_artifact_types([])


def test_validate_artifact_types_accepts_known_values():
    rd._validate_artifact_types(["loop_report", "evidence_pack"])  # no raise


# ── _shape_payload ────────────────────────────────────────────────────────────

def test_shape_payload_raw_passes_through_untouched():
    payload = {"entity": "Acme Corp", "risks": [{"id": "R1"}]}
    assert rd._shape_payload("raw", "loop_report", payload) is payload


def test_shape_payload_slack_wraps_as_slack_attachment():
    payload = {"entity": "Acme Corp", "risks": [{"id": "R1"}], "riskAppetite": {"status": "BREACHED"}}
    shaped = rd._shape_payload("slack", "loop_report", payload)
    assert "text" in shaped and "attachments" in shaped
    assert "Acme Corp" in shaped["text"]


def test_shape_payload_msteams_wraps_as_message_card():
    payload = {"run": {"company_name": "Acme Corp"}, "risk_scores": [1, 2], "approval_tasks": [1]}
    shaped = rd._shape_payload("msteams", "evidence_pack", payload)
    assert shaped["@type"] == "MessageCard"
    assert "Acme Corp" in shaped["title"] + shaped["text"] or "Audit Evidence Pack" in shaped["title"]


# ── _build_auth_headers ───────────────────────────────────────────────────────

def test_build_auth_headers_bearer():
    headers = rd._build_auth_headers("bearer", {"token": "tok123"}, b"{}")
    assert headers == {"Authorization": "Bearer tok123"}


def test_build_auth_headers_api_key_default_header_name():
    headers = rd._build_auth_headers("api_key", {"api_key": "key123"}, b"{}")
    assert headers == {"X-API-Key": "key123"}


def test_build_auth_headers_api_key_custom_header_name():
    headers = rd._build_auth_headers("api_key", {"api_key": "key123", "header_name": "X-Custom-Auth"}, b"{}")
    assert headers == {"X-Custom-Auth": "key123"}


def test_build_auth_headers_hmac_signs_body():
    body = b'{"hello":"world"}'
    secret = "s3cr3t"
    headers = rd._build_auth_headers("hmac", {"secret": secret}, body)
    expected_sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert headers == {"X-Dendrai-Signature-256": f"sha256={expected_sig}"}


def test_build_auth_headers_none_returns_empty():
    assert rd._build_auth_headers("none", None, b"{}") == {}


# ── _basic_auth ───────────────────────────────────────────────────────────────

def test_basic_auth_returns_tuple_for_basic():
    assert rd._basic_auth("basic", {"username": "u", "password": "p"}) == ("u", "p")


def test_basic_auth_returns_none_for_other_types():
    assert rd._basic_auth("bearer", {"token": "t"}) is None
    assert rd._basic_auth("none", None) is None
