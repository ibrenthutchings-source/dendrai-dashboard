#!/usr/bin/env python3
"""
Integration tests for the outbound alert-dispatch side of "catch and
report" — the mechanisms that turn a caught control-state signal into an
actual outbound notification, as opposed to a row that just sits in a table
until someone happens to look:

1. mcp_governance._post_webhook_alert / _dispatch_alert — the Slack-compatible
   webhook every ESCALATE verdict, corporate-event alert, and Model Health
   drift incident dispatches through (README.md / .env.example's
   MCP_ALERT_WEBHOOK_URL). No prior test coverage existed for either
   function — every existing test that touches "alert" is about scoring
   severity, not about whether a notification actually goes out.

2. api_server._check_model_health_drift_once — the periodic watcher that
   turns a PSI drift signal into a persisted, deduplicated incident AND an
   alert. Proven here: a real drift opens exactly one incident (not a
   duplicate on the next tick while it's still open) and dispatches exactly
   one webhook call per newly-opened incident.

    pytest test_alert_dispatch.py -v
"""

from __future__ import annotations

import json

import mcp_governance as mg


# ── _post_webhook_alert ──────────────────────────────────────────────────────

def test_post_webhook_alert_no_op_when_url_unset(monkeypatch):
    monkeypatch.setattr(mg, "_ALERT_WEBHOOK_URL", "")
    def _should_not_be_called(*a, **kw):
        raise AssertionError("urlopen must not be called when MCP_ALERT_WEBHOOK_URL is unset")
    monkeypatch.setattr(mg.urllib.request, "urlopen", _should_not_be_called)
    mg._post_webhook_alert("test", [{"title": "x", "value": "y"}])  # must not raise


def test_post_webhook_alert_posts_correct_slack_shaped_payload(monkeypatch):
    monkeypatch.setattr(mg, "_ALERT_WEBHOOK_URL", "https://hooks.slack.com/services/T00/B00/XYZ")
    captured = {}

    class _FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data)
        captured["timeout"] = timeout
        return _FakeResponse()
    monkeypatch.setattr(mg.urllib.request, "urlopen", _fake_urlopen)

    mg._post_webhook_alert(
        "\U0001f6a8 test alert",
        [{"title": "Tool", "value": "delete_prod_data", "short": True}],
        color="#c0392b",
    )

    assert captured["url"] == "https://hooks.slack.com/services/T00/B00/XYZ"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["body"]["text"] == "\U0001f6a8 test alert"
    assert captured["body"]["attachments"][0]["color"] == "#c0392b"
    assert captured["body"]["attachments"][0]["fields"][0]["value"] == "delete_prod_data"
    assert captured["timeout"] == 5


def test_post_webhook_alert_network_failure_is_swallowed_not_raised(monkeypatch):
    """A Slack outage must not take down the caller (the governance poller,
    the drift watcher, ...) — this is explicitly a best-effort notification,
    not a step whose failure should abort adjudication or drift-incident
    creation."""
    monkeypatch.setattr(mg, "_ALERT_WEBHOOK_URL", "https://hooks.slack.com/services/T00/B00/XYZ")
    def _raise(*a, **kw):
        raise TimeoutError("slack.com unreachable")
    monkeypatch.setattr(mg.urllib.request, "urlopen", _raise)
    mg._post_webhook_alert("test", [])  # must not raise


# ── _dispatch_alert — builds the ESCALATE-specific field shape ─────────────

def test_dispatch_alert_builds_correct_fields_and_delegates_to_post(monkeypatch):
    captured = {}
    def _fake_post(text, fields, *, color="#c0392b"):
        captured.update(text=text, fields=fields, color=color)
    monkeypatch.setattr(mg, "_post_webhook_alert", _fake_post)

    mg._dispatch_alert(
        tool_name="update_vendor_bank_details",
        session_id="12345678-abcd-ef00-0000-000000000000",
        risk_tier="CRITICAL",
        risk_score=0.97,
        verdict="ESCALATE",
        reasoning="Policy-as-Code veto: VM-DENY-001 fired.",
    )

    assert "update_vendor_bank_details" in captured["text"]
    field_map = {f["title"]: f["value"] for f in captured["fields"]}
    assert field_map["Tool"] == "update_vendor_bank_details"
    assert field_map["Risk Tier"] == "CRITICAL"
    assert field_map["Risk Score"] == "0.970"
    assert field_map["Verdict"] == "ESCALATE"
    assert field_map["Session"] == "12345678" + "…"
    assert "VM-DENY-001" in field_map["Reasoning"]


def test_dispatch_alert_truncates_long_reasoning_to_300_chars(monkeypatch):
    captured = {}
    monkeypatch.setattr(mg, "_post_webhook_alert", lambda text, fields, **kw: captured.update(fields=fields))
    mg._dispatch_alert(
        tool_name="x", session_id="s" * 40, risk_tier="HIGH", risk_score=0.7,
        verdict="ESCALATE", reasoning="A" * 500,
    )
    field_map = {f["title"]: f["value"] for f in captured["fields"]}
    assert len(field_map["Reasoning"]) == 300


# ── api_server._check_model_health_drift_once ────────────────────────────────

import api_server


def test_drift_check_no_op_without_database(monkeypatch):
    monkeypatch.setattr(api_server.db, "is_available", lambda: False)
    assert api_server._check_model_health_drift_once() == []


def test_drift_check_opens_incident_and_dispatches_alert_on_real_drift(monkeypatch):
    monkeypatch.setattr(api_server.db, "is_available", lambda: True)
    monkeypatch.setattr(api_server.db, "get_baseline_resets", lambda: {})
    monkeypatch.setattr(api_server.db, "get_financial_ratios_history", lambda: [])
    monkeypatch.setattr(api_server.db, "get_ai_acceptance_history", lambda: [])
    monkeypatch.setenv("MODEL_HEALTH_AUTO_REOPTIMIZE", "false")

    import drift_tool
    monkeypatch.setattr(drift_tool, "compute_ratio_drift", lambda rows, baseline_resets=None: [
        {"ratio": "gross_margin", "flag": "drift", "psi": 0.31, "n_baseline": 40, "n_current": 10},
    ])
    monkeypatch.setattr(drift_tool, "compute_fred_regime_drift", lambda key, baseline_resets=None: [])
    monkeypatch.setattr(drift_tool, "compute_ai_acceptance_drift", lambda rows, baseline_resets=None: [])

    monkeypatch.setattr(api_server.db, "get_open_drift_incident", lambda metric_key: None)
    created = []
    monkeypatch.setattr(api_server.db, "create_drift_incident",
                         lambda metric_key, metric_kind, psi, n_baseline, n_current, detail=None:
                         created.append((metric_key, metric_kind, psi)) or 501)

    alerts = []
    monkeypatch.setattr(mg, "_post_webhook_alert", lambda text, fields, **kw: alerts.append((text, fields)))
    monkeypatch.setattr(api_server, "_HAS_MCP_GOVERNANCE", True)
    monkeypatch.setattr(api_server, "mcp_governance", mg)

    result = api_server._check_model_health_drift_once()

    assert len(result) == 1
    assert result[0]["metric"] == "gross_margin"
    assert result[0]["incident_id"] == 501
    assert created == [("gross_margin", "ratio", 0.31)]
    assert len(alerts) == 1
    assert "gross_margin" in alerts[0][0]


def test_drift_check_does_not_reopen_an_already_open_incident(monkeypatch):
    """The dedup claim: 'already a tracked, unresolved incident for this
    metric' must suppress a second alert/incident on the next tick while the
    first is still open — otherwise a persistent drift condition would spam
    an incident (and a Slack message) every single check cycle."""
    monkeypatch.setattr(api_server.db, "is_available", lambda: True)
    monkeypatch.setattr(api_server.db, "get_baseline_resets", lambda: {})
    monkeypatch.setattr(api_server.db, "get_financial_ratios_history", lambda: [])
    monkeypatch.setattr(api_server.db, "get_ai_acceptance_history", lambda: [])

    import drift_tool
    monkeypatch.setattr(drift_tool, "compute_ratio_drift", lambda rows, baseline_resets=None: [
        {"ratio": "gross_margin", "flag": "drift", "psi": 0.31, "n_baseline": 40, "n_current": 10},
    ])
    monkeypatch.setattr(drift_tool, "compute_fred_regime_drift", lambda key, baseline_resets=None: [])
    monkeypatch.setattr(drift_tool, "compute_ai_acceptance_drift", lambda rows, baseline_resets=None: [])

    monkeypatch.setattr(api_server.db, "get_open_drift_incident", lambda metric_key: {"id": 999, "status": "open"})
    created = []
    monkeypatch.setattr(api_server.db, "create_drift_incident", lambda *a, **kw: created.append(a) or 999)
    alerts = []
    monkeypatch.setattr(mg, "_post_webhook_alert", lambda *a, **kw: alerts.append(a))
    monkeypatch.setattr(api_server, "mcp_governance", mg)

    result = api_server._check_model_health_drift_once()

    assert result == []
    assert created == []
    assert alerts == []


def test_drift_check_stable_metrics_open_nothing(monkeypatch):
    monkeypatch.setattr(api_server.db, "is_available", lambda: True)
    monkeypatch.setattr(api_server.db, "get_baseline_resets", lambda: {})
    monkeypatch.setattr(api_server.db, "get_financial_ratios_history", lambda: [])
    monkeypatch.setattr(api_server.db, "get_ai_acceptance_history", lambda: [])

    import drift_tool
    monkeypatch.setattr(drift_tool, "compute_ratio_drift", lambda rows, baseline_resets=None: [
        {"ratio": "gross_margin", "flag": "stable", "psi": 0.02, "n_baseline": 40, "n_current": 10},
    ])
    monkeypatch.setattr(drift_tool, "compute_fred_regime_drift", lambda key, baseline_resets=None: [])
    monkeypatch.setattr(drift_tool, "compute_ai_acceptance_drift", lambda rows, baseline_resets=None: [])

    created = []
    monkeypatch.setattr(api_server.db, "create_drift_incident", lambda *a, **kw: created.append(a) or 1)

    result = api_server._check_model_health_drift_once()

    assert result == []
    assert created == []
