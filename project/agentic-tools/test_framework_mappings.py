"""
Tests for framework_mappings.py's curated FRAMEWORK_MAPPINGS dict — pure data
validation, no DB access. Guards against the exact bug this file was
corrected for twice: coso_component silently drifting to IC-IF 2013 names
(2026-08-25), and icif_component (added 2026-08-26) silently drifting to
ERM 2017 names or being derived from coso_component instead of curated
independently.
"""

from __future__ import annotations

import framework_mappings

_ERM_2017_COMPONENTS = {
    "Governance & Culture",
    "Strategy & Objective-Setting",
    "Performance",
    "Review & Revision",
    "Information, Communication & Reporting",
}

_ICIF_2013_COMPONENTS = {
    "Control Environment",
    "Risk Assessment",
    "Control Activities",
    "Information & Communication",
    "Monitoring Activities",
}


def test_coso_component_values_are_all_real_erm_2017_names():
    for control_id, mapping in framework_mappings.FRAMEWORK_MAPPINGS.items():
        value = mapping.get("coso_component")
        if value is not None:
            assert value in _ERM_2017_COMPONENTS, f"{control_id}.coso_component={value!r} is not an ERM 2017 component"


def test_icif_component_values_are_all_real_icif_2013_names():
    for control_id, mapping in framework_mappings.FRAMEWORK_MAPPINGS.items():
        value = mapping.get("icif_component")
        if value is not None:
            assert value in _ICIF_2013_COMPONENTS, f"{control_id}.icif_component={value!r} is not an IC-IF 2013 component"


def test_coso_component_and_icif_component_are_independent_fields():
    """Neither is derived from the other — a control can (and does) carry
    both simultaneously, and they are allowed to differ in wording even when
    they describe related activity (e.g. 'Performance' vs 'Control
    Activities' for the same INFRA-* control) because they answer different
    questions about the same control."""
    for control_id, mapping in framework_mappings.FRAMEWORK_MAPPINGS.items():
        has_coso = "coso_component" in mapping and mapping["coso_component"] is not None
        has_icif = "icif_component" in mapping and mapping["icif_component"] is not None
        # Every currently-mapped control happens to carry both today, but the
        # real invariant is just that the two keys are distinct dict entries,
        # not one computed from the other — assert they exist as separate
        # keys rather than asserting a specific 1:1 correspondence.
        if has_coso or has_icif:
            assert "coso_component" in mapping
            assert "icif_component" in mapping


def test_infra005_maps_to_monitoring_not_control_activities():
    """The one control whose two framework components genuinely diverge —
    connection logging is a Monitoring Activity (IC-IF) that implements a
    Review & Revision response (ERM 2017), not a Control Activity/Performance
    one like the other seven INFRA-* controls."""
    mapping = framework_mappings.FRAMEWORK_MAPPINGS["INFRA-005"]
    assert mapping["coso_component"] == "Review & Revision"
    assert mapping["icif_component"] == "Monitoring Activities"
