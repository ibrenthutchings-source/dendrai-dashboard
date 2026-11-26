"""Evidence-scored taxonomy: scoring, Not-assessed, floors, and shape."""
import risk_rating_engine as rre
import risk_taxonomy as rt
from predictive_analytics_tool import compute_risk_scores


def _by_id(res):
    return {r["id"]: r for r in res["risks"]}


def test_every_taxonomy_category_resolves_to_a_real_impact():
    for t in rt.TAXONOMY:
        assert rre.canonical_category(t["category"]) == t["category"], t
    assert len(rt.TAXONOMY_IDS) == len(set(rt.TAXONOMY_IDS))


def test_no_evidence_means_not_assessed_not_a_default_score():
    res = rt.score_taxonomy({})
    assert res["assessed_count"] == 0
    assert res["rag_summary"] == {"R": 0, "A": 0, "G": 0}
    for r in res["risks"]:
        assert r["assessed"] is False and r["score"] is None and r["rag_status"] is None


def test_benign_evidence_is_assessed_and_low():
    res = _by_id(rt.score_taxonomy({"tata": 0.0, "cash_ratio": 0.3, "net_margin": 0.2, "revenue_growth": 0.1}))
    assert res["TX-FRQ"]["assessed"] and res["TX-FRQ"]["rag_status"] == "G"
    assert res["TX-LIQ"]["assessed"] and res["TX-LIQ"]["score"] < 9
    assert res["TX-GOV"]["assessed"] is False


def test_stress_pushes_liquidity_to_red_with_evidence_trail():
    res = _by_id(rt.score_taxonomy({"cash_ratio": 0.02, "fcf_margin": -0.2},
                                   zscore={"z_score": 0.5}))
    liq = res["TX-LIQ"]
    assert liq["rag_status"] == "R" and liq["score"] >= 15
    assert len(liq["evidence"]) >= 3 and all("label" in e and "points" in e for e in liq["evidence"])


def test_industry_label_does_not_change_scores():
    ratios = {"tata": 0.08, "revenue_growth": -0.07}
    a = compute_risk_scores(ratios, "Semiconductors")
    b = compute_risk_scores(ratios, "Healthcare & Pharma")
    assert [r["score"] for r in a["risks"]] == [r["score"] for r in b["risks"]]
    assert a["industry"] == "Semiconductors"


def test_trend_evidence_drives_velocity_and_base_excludes_it():
    res = _by_id(rt.score_taxonomy({"revenue_growth": -0.12}))
    rev = res["TX-REV"]
    assert rev["base_score"] < rev["score"] and rev["velocity"] >= 1


def test_floor_forces_red_even_when_points_are_low():
    ev = rt.ev("SEC 8-K", "test", 0.1, trend=True)
    ev["floor"] = "R"
    res = _by_id(rt.score_taxonomy({}, extra_evidence={"TX-LIQ": [ev]}))
    assert res["TX-LIQ"]["score"] == 15.0 and res["TX-LIQ"]["rag_status"] == "R"


def test_grey_swan_ignores_not_assessed_risks():
    from predictive_analytics_tool import compute_grey_swan
    res = compute_risk_scores({"revenue_growth": -0.2})
    out = compute_grey_swan(res, 1e8)
    assert "error" not in out
    assert compute_grey_swan(compute_risk_scores({}), 1e8).get("error")
