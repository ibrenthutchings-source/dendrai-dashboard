"""
Tests for risk_rating_engine.py — the canonical 0-25 / R-A-G scorer every
other risk-scoring path in the platform is being migrated onto.

    pytest test_risk_rating_engine.py -v
"""
from __future__ import annotations

import risk_rating_engine as rre


class TestRagOf:
    def test_exact_red_boundary(self):
        assert rre.rag_of(15.0) == "R"

    def test_just_under_red_is_amber(self):
        assert rre.rag_of(14.9) == "A"

    def test_exact_amber_boundary(self):
        assert rre.rag_of(9.0) == "A"

    def test_just_under_amber_is_green(self):
        assert rre.rag_of(8.9) == "G"

    def test_zero_is_green(self):
        assert rre.rag_of(0) == "G"

    def test_none_is_green_not_an_exception(self):
        assert rre.rag_of(None) == "G"

    def test_max_score_is_red(self):
        assert rre.rag_of(25.0) == "R"


class TestCanonicalCategory:
    def test_direct_category_hit(self):
        assert rre.canonical_category(category="Cybersecurity") == "Cybersecurity"

    def test_alias_resolves(self):
        assert rre.canonical_category(category="Regulatory") == "Compliance"
        assert rre.canonical_category(category="Financial") == "Financial Reporting"
        assert rre.canonical_category(category="Macro") == "Strategic"

    def test_unknown_category_falls_through_to_none(self):
        assert rre.canonical_category(category="Nonsense") is None

    def test_process_maps_to_category(self):
        assert rre.canonical_category(process="record_to_report") == "Financial Reporting"
        assert rre.canonical_category(process="iam") == "Cybersecurity"

    def test_process_is_case_insensitive(self):
        assert rre.canonical_category(process="RECORD_TO_REPORT") == "Financial Reporting"

    def test_unmapped_process_falls_through_to_none(self):
        assert rre.canonical_category(process="some_custom_extra_config_key") is None

    def test_category_wins_over_process_when_both_given(self):
        assert rre.canonical_category(category="Legal", process="iam") == "Legal"

    def test_no_input_is_none(self):
        assert rre.canonical_category() is None


class TestImpactFor:
    def test_known_category(self):
        assert rre.impact_for(category="Trade Compliance") == 5

    def test_unknown_category_uses_default(self):
        assert rre.impact_for(category="Nonsense") == rre.DEFAULT_IMPACT

    def test_unmapped_process_uses_default(self):
        assert rre.impact_for(process=None) == rre.DEFAULT_IMPACT

    def test_esg_is_lowest_impact(self):
        assert rre.impact_for(category="ESG") == 2


class TestScoreFrom:
    def test_shape(self):
        result = rre.score_from(impact=4, likelihood=5.0)
        assert result == {"impact": 4.0, "likelihood": 5.0, "score": 20.0, "rag_status": "R"}

    def test_likelihood_clamped_above_max(self):
        result = rre.score_from(impact=2, likelihood=99)
        assert result["likelihood"] == rre.LIKELIHOOD_MAX

    def test_likelihood_clamped_below_min(self):
        result = rre.score_from(impact=2, likelihood=-5)
        assert result["likelihood"] == rre.LIKELIHOOD_MIN

    def test_score_never_exceeds_scale_max(self):
        result = rre.score_from(impact=5, likelihood=5)
        assert result["score"] <= rre.SCORE_MAX

    def test_rag_status_matches_rag_of_the_computed_score(self):
        for impact in range(1, 6):
            for likelihood10 in range(1, 51):
                r = rre.score_from(impact, likelihood10 / 10)
                assert r["rag_status"] == rre.rag_of(r["score"])


class TestScoreFromRaw10:
    def test_matches_buildrisks_shape(self):
        # raw 10 -> likelihood clamp(10/2, 0.5, 5.0) = 5.0; impact for
        # Financial Reporting = 4 -> score = 20.0 -> Red.
        result = rre.score_from_raw10(10.0, category="Financial Reporting")
        assert result["likelihood"] == 5.0
        assert result["score"] == 20.0
        assert result["rag_status"] == "R"

    def test_low_raw_score_is_green(self):
        result = rre.score_from_raw10(1.0, category="ESG")
        assert result["rag_status"] == "G"


class TestScoreException:
    def test_critical_severity_high_tier_is_red_or_amber_never_unscored(self):
        result = rre.score_exception("CRITICAL", process="record_to_report", connector_risk_tier="high")
        assert result["score"] > 0
        assert result["rag_status"] in ("R", "A", "G")

    def test_severity_drives_likelihood_upward(self):
        low = rre.score_exception("INFO", process="record_to_report")
        high = rre.score_exception("CRITICAL", process="record_to_report")
        assert high["likelihood"] > low["likelihood"]
        assert high["score"] > low["score"]

    def test_connector_tier_is_a_real_independent_modifier(self):
        # Same severity, different tier -> different score. If risk_tier were
        # ignored (folded away or dropped) these would be equal.
        low_tier = rre.score_exception("HIGH", connector_risk_tier="low")
        high_tier = rre.score_exception("HIGH", connector_risk_tier="high")
        assert high_tier["score"] > low_tier["score"]

    def test_unrecognized_severity_does_not_crash(self):
        result = rre.score_exception("SOMETHING_WEIRD")
        assert result["rag_status"] in ("R", "A", "G")

    def test_none_process_falls_back_to_default_impact(self):
        result = rre.score_exception("HIGH", process=None)
        assert result["impact"] == rre.DEFAULT_IMPACT

    def test_rag_status_is_always_a_letter(self):
        for sev in ["CRITICAL", "HIGH", "MEDIUM", "WARN", "LOW", "INFO", "bogus"]:
            result = rre.score_exception(sev)
            assert result["rag_status"] in ("R", "A", "G")


class TestVelocity:
    def test_velocity_of_matches_risk_engine_js_bands(self):
        assert rre.velocity_of(base=10, score=14) == 3    # d=4 > 3.75
        assert rre.velocity_of(base=10, score=12) == 2     # d=2 > 1.75
        assert rre.velocity_of(base=10, score=10.5) == 1   # d=0.5 > 0.25
        assert rre.velocity_of(base=10, score=10.1) == 0   # d=0.1, no band
        assert rre.velocity_of(base=10, score=7.9) == -1   # d=-2.1 < -2.0

    def test_escalation_step_round_trips_into_the_same_velocity(self):
        for level in (1, 2, 3):
            base = 10.0
            step = rre.escalation_step(level)
            assert rre.velocity_of(base=base, score=base + step) == level
