#!/usr/bin/env python3
"""
Unit tests for the Pipeline-as-Code (GitHub Actions workflow) security
auditor: pipeline_security_connectors.py's static-analysis/normalization/
severity logic, and the devops_monitoring Rego module's DEVOPS-010..013
rules (via pac_endpoints' heuristic evaluator — every environment has it).
No DB, no network.

    pytest test_pipeline_security.py -v
"""

from __future__ import annotations

import pac_endpoints
import pipeline_security_connectors as psc


# ── analyze_workflow ─────────────────────────────────────────────────────────

_SAFE_WORKFLOW = """\
name: CI
on:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@8f4b7f84864484a7bf31766abe9204da3cbe65b3
      - uses: actions/setup-node@60edb5dd545a775178f52524783378180af0d1f0
"""

_WRITE_ALL_WORKFLOW = """\
name: CI
on: push
permissions: write-all
jobs:
  build:
    steps:
      - uses: actions/checkout@v4
"""

_NO_PERMISSIONS_WORKFLOW = """\
name: CI
on: push
jobs:
  build:
    steps:
      - uses: actions/checkout@v4
"""

_RISKY_PR_TARGET_WORKFLOW = """\
name: CI
on:
  pull_request_target:
    branches: [main]
permissions:
  contents: read
jobs:
  build:
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
"""

_SAFE_PR_TARGET_WORKFLOW = """\
name: CI
on:
  pull_request_target:
    branches: [main]
permissions:
  contents: read
jobs:
  build:
    steps:
      - uses: actions/checkout@8f4b7f84864484a7bf31766abe9204da3cbe65b3
"""


def test_analyze_workflow_fully_hardened():
    a = psc.analyze_workflow(_SAFE_WORKFLOW)
    assert a["has_permissions_block"] is True
    assert a["permissions_write_all"] is False
    assert a["has_pull_request_target"] is False
    assert a["has_risky_pull_request_target"] is False
    assert a["unpinned_actions"] == []


def test_analyze_workflow_write_all_permissions():
    a = psc.analyze_workflow(_WRITE_ALL_WORKFLOW)
    assert a["permissions_write_all"] is True


def test_analyze_workflow_missing_permissions_block():
    a = psc.analyze_workflow(_NO_PERMISSIONS_WORKFLOW)
    assert a["has_permissions_block"] is False


def test_analyze_workflow_unpinned_action_detected():
    a = psc.analyze_workflow(_NO_PERMISSIONS_WORKFLOW)
    assert "actions/checkout@v4" in a["unpinned_actions"]


def test_analyze_workflow_risky_pull_request_target():
    a = psc.analyze_workflow(_RISKY_PR_TARGET_WORKFLOW)
    assert a["has_pull_request_target"] is True
    assert a["has_risky_pull_request_target"] is True


def test_analyze_workflow_safe_pull_request_target_pinned_checkout():
    """pull_request_target alone isn't risky — only combined with a
    PR-head checkout. A SHA-pinned checkout with no `ref:` override is safe."""
    a = psc.analyze_workflow(_SAFE_PR_TARGET_WORKFLOW)
    assert a["has_pull_request_target"] is True
    assert a["has_risky_pull_request_target"] is False


def test_analyze_workflow_handles_malformed_yaml_without_raising():
    a = psc.analyze_workflow("not: valid: yaml: [[[")
    assert "parse_error" in a
    assert a["has_risky_pull_request_target"] is False


# ── normalize_pipeline_compliance ────────────────────────────────────────────

def test_normalize_pipeline_compliance_aggregates_across_files():
    analyzed = [psc.analyze_workflow(_SAFE_WORKFLOW), psc.analyze_workflow(_NO_PERMISSIONS_WORKFLOW)]
    c = psc.normalize_pipeline_compliance(analyzed)
    assert c["total_workflows"] == 2
    assert c["workflows_without_permissions"] == 1
    assert c["unpinned_action_count"] == 1


def test_normalize_pipeline_compliance_empty_input():
    c = psc.normalize_pipeline_compliance([])
    assert c["total_workflows"] == 0
    assert c["has_write_all_permissions"] is False
    assert c["has_risky_pull_request_target"] is False


# ── evaluate_pipeline_severity ───────────────────────────────────────────────

def test_evaluate_pipeline_severity_risky_pr_target_is_critical():
    c = psc.normalize_pipeline_compliance([psc.analyze_workflow(_RISKY_PR_TARGET_WORKFLOW)])
    assert psc.evaluate_pipeline_severity(c) == "CRITICAL"


def test_evaluate_pipeline_severity_write_all_is_high():
    c = psc.normalize_pipeline_compliance([psc.analyze_workflow(_WRITE_ALL_WORKFLOW)])
    assert psc.evaluate_pipeline_severity(c) == "HIGH"


def test_evaluate_pipeline_severity_unpinned_action_is_high():
    c = psc.normalize_pipeline_compliance([psc.analyze_workflow(_NO_PERMISSIONS_WORKFLOW)])
    assert psc.evaluate_pipeline_severity(c) == "HIGH"


def test_evaluate_pipeline_severity_fully_hardened_is_info():
    c = psc.normalize_pipeline_compliance([psc.analyze_workflow(_SAFE_WORKFLOW)])
    assert psc.evaluate_pipeline_severity(c) == "INFO"


# ── devops_monitoring Rego: DEVOPS-010..013 (heuristic evaluator) ───────────

def _rego() -> str:
    return pac_endpoints._REGO_DEFAULTS["devops_monitoring"]


def _compliant_event(**overrides) -> dict:
    base = {
        "type": "PIPELINE_MISCONFIGURATION", "resource": "my-org/my-repo",
        "has_write_all_permissions": False, "workflows_without_permissions": 0,
        "unpinned_action_count": 0, "has_risky_pull_request_target": False,
    }
    base.update(overrides)
    return base


def test_devops_rego_fires_on_write_all_permissions():
    result = pac_endpoints.evaluate_policy_event(_rego(), {"event": _compliant_event(has_write_all_permissions=True)})
    fired_rules = {r["rule"] for r in result["rules_fired"]}
    assert "deny_pipeline_security" in fired_rules


def test_devops_rego_fires_on_missing_permissions_block():
    result = pac_endpoints.evaluate_policy_event(_rego(), {"event": _compliant_event(workflows_without_permissions=2)})
    assert result["rules_fired"]


def test_devops_rego_fires_on_unpinned_actions():
    result = pac_endpoints.evaluate_policy_event(_rego(), {"event": _compliant_event(unpinned_action_count=3)})
    assert result["rules_fired"]


def test_devops_rego_fires_on_risky_pull_request_target():
    result = pac_endpoints.evaluate_policy_event(_rego(), {"event": _compliant_event(has_risky_pull_request_target=True)})
    control_ids = {r.get("control_id") for r in result["rules_fired"]}
    assert "DEVOPS-013" in control_ids


def test_devops_rego_silent_when_pipeline_fully_compliant():
    result = pac_endpoints.evaluate_policy_event(_rego(), {"event": _compliant_event()})
    # Only the pipeline-security fields are populated here — branch-protection/
    # SARIF/SLA rules key on different input.event.type values and shouldn't fire.
    fired_rules = {r["rule"] for r in result["rules_fired"]}
    assert "deny_pipeline_security" not in fired_rules
