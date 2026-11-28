"""Item 1A year-over-year diff: extraction, classification, change scoring, guards."""
from datetime import date

import risk_factor_intel as r
import risk_taxonomy as rt

TODAY = date(2026, 11, 26)
BODY = ("This is a long explanation of the risk that goes on for several sentences so that the splitter sees real body "
        "text rather than another heading. It discusses how the matter could affect operations, results and the "
        "price of our securities, and what management does about it. ")

FACTORS = [
    "We depend on a limited number of suppliers for key components and a disruption could harm our business.",
    "A cybersecurity incident could disrupt our operations and expose confidential data.",
    "Our substantial indebtedness could limit our flexibility and our ability to refinance.",
    "We may be unable to attract and retain key personnel and senior management.",
    "Changes in tariffs and trade restrictions could adversely affect our costs.",
    "Weak demand for our products could reduce our revenue.",
    "We are subject to extensive regulation and litigation that could be costly.",
    "Our failure to maintain effective internal control over financial reporting could harm investors.",
    "Competition from larger competitors could reduce our market share.",
    "Fluctuations in foreign currency exchange rates could adversely affect our results.",
]


def T(out):
    """Year-over-year (trend) evidence only — the standing baselines are tested separately."""
    return {tx: [e for e in evs if e["trend"]] for tx, evs in out["evidence"].items() if any(e["trend"] for e in evs)}


def _tenk(headings, bodies=None, toc=True):
    bodies = bodies or {}
    parts = ["Annual Report", "Table of Contents"]
    if toc:
        parts += ["Item 1A. Risk Factors 12", "Item 1B. Unresolved Staff Comments 30", "Item 2. Properties 31"]
    parts += ["PART I", "Item 1. Business", "We make things. " * 30,
              "Item 1A. Risk Factors", "Summary of risk factors", "Risks Related to Our Business"]
    for h in headings:
        parts += [h, bodies.get(h, BODY * 2)]
    parts += ["Item 1B. Unresolved Staff Comments", "None.", "Item 2. Properties"]
    return "\n\n".join(parts)


def test_extraction_skips_the_table_of_contents():
    sec = r.extract_item_1a(_tenk(FACTORS))
    assert "cybersecurity incident" in sec and len(sec) > 2000


def test_splitter_finds_each_factor_and_ignores_boilerplate():
    f = r.split_risk_factors(r.extract_item_1a(_tenk(FACTORS)))
    assert [x["heading"] for x in f] == FACTORS


def test_classification_is_rule_ordered_and_unknown_is_none():
    assert r.classify("A cybersecurity incident could disrupt our operations")["tx"] == "TX-CYB"
    assert r.classify("Changes in tariffs could affect costs")["tx"] == "TX-TRD"
    assert r.classify("Our substantial indebtedness could limit flexibility")["tx"] == "TX-CAP"
    assert r.classify("Something entirely unrelated could happen to the zebra herd") is None
    assert {tx for _, _, tx in r.TOPIC_RULES} <= set(rt.TAXONOMY_IDS)


def test_new_factor_is_scored_against_its_category_and_removed_is_not():
    cur = FACTORS + ["A ransomware attack on our systems could disrupt production and expose data."]
    pri = [h for h in FACTORS if "tariffs" not in h]
    out = r.analyze(_tenk(cur), _tenk(pri), "2026-11-01", "2025-11-01", today=TODAY)
    assert out["status"] == "ok"
    new = {c["tx"]: c for c in out["changes"] if c["change"] == "new"}
    assert set(new) == {"TX-TRD", "TX-CYB"} and all(c["points"] == r.PTS_NEW for c in new.values())
    assert out["removed"] == []
    out2 = r.analyze(_tenk(pri), _tenk(FACTORS), "2026-11-01", "2025-11-01", today=TODAY)
    assert [x["tx"] for x in out2["removed"]] == ["TX-TRD"] and T(out2) == {}


def test_hypothetical_to_realised_wording_is_escalated():
    h = FACTORS[1]
    pri = _tenk(FACTORS)
    cur = _tenk(FACTORS, {h: BODY + "We experienced a cybersecurity incident during the year that was contained. "})
    out = r.analyze(cur, pri, "2026-11-01", "2025-11-01", today=TODAY)
    ch = [c for c in out["changes"] if c["change"] == "escalated"]
    assert len(ch) == 1 and ch[0]["tx"] == "TX-CYB" and ch[0]["points"] == r.PTS_ESCALATED
    ev = T(out)["TX-CYB"][0]
    assert ev["points"] == 0.5 and ev["source"] == r.SOURCE and ev["trend"] is True


def test_expanded_disclosure_scores_small_and_unchanged_scores_nothing():
    h = FACTORS[0]
    cur = _tenk(FACTORS, {h: BODY * 6})
    out = r.analyze(cur, _tenk(FACTORS), "2026-11-01", "2025-11-01", today=TODAY)
    assert [c["change"] for c in out["changes"]] == ["expanded"]
    assert r.analyze(_tenk(FACTORS), _tenk(FACTORS), "2026-11-01", "2025-11-01", today=TODAY)["changes"] == []


def test_recency_weighting_and_category_cap():
    many = FACTORS + [f"A new ransomware campaign number {i} could disrupt our systems and expose data." for i in range(8)]
    fresh = r.analyze(_tenk(many), _tenk(FACTORS), "2026-11-01", "2025-11-01", today=TODAY)
    assert round(sum(e["points"] for e in T(fresh)["TX-CYB"]), 2) == r.MAX_POINTS_PER_CATEGORY
    old = r.analyze(_tenk(FACTORS + [many[-1]]), _tenk(FACTORS), "2025-02-01", "2024-02-01", today=TODAY)
    assert T(old)["TX-CYB"][0]["points"] == round(r.PTS_NEW * 0.2, 2)     # ~21 months old -> 0.2 band


def test_guards_prior_missing_low_parse_and_restructure():
    assert r.analyze(_tenk(FACTORS), "", "2026-11-01", today=TODAY)["status"] == "no_prior"
    few = r.analyze(_tenk(FACTORS[:3]), _tenk(FACTORS), "2026-11-01", "2025-11-01", today=TODAY)
    assert few["status"] == "low_confidence" and T(few) == {}
    rewritten = [
        "Quarterly dividend policy could be altered by directors at any time.",
        "Employee stock plans may produce unexpected accounting volatility for shareholders.",
        "Maritime shipping lanes may become congested and delay deliveries to buyers.",
        "Pension obligations could grow faster than projected asset returns.",
        "Warranty claims on products could exceed reserves established by management.",
        "Real estate holdings might lose appraised value during downturns.",
        "Hedging instruments could fail to offset commodity price swings.",
        "Franchise partners may terminate agreements before scheduled expiry.",
        "Insurance coverage might prove inadequate for catastrophic claims.",
        "Intercompany loans could create adverse withholding consequences.",
    ]
    out = r.analyze(_tenk(rewritten), _tenk(FACTORS), "2026-11-01", "2025-11-01", today=TODAY)
    assert out["status"] == "structure_changed" and T(out) == {}


def test_unclassifiable_new_factor_is_listed_not_scored():
    cur = FACTORS + ["Zebra migration patterns could adversely affect the herd we may be unable to follow."]
    out = r.analyze(_tenk(cur), _tenk(FACTORS), "2026-11-01", "2025-11-01", today=TODAY)
    assert T(out) == {} and len(out["unclassified"]) == 1


def test_analysis_is_deterministic():
    cur, pri = _tenk(FACTORS + ["A ransomware attack could disrupt production and expose data."]), _tenk(FACTORS)
    a = r.analyze(cur, pri, "2026-11-01", "2025-11-01", today=TODAY)
    b = r.analyze(cur, pri, "2026-11-01", "2025-11-01", today=TODAY)
    assert a == b


# ── Standing baselines: Item 1A presence and Item 1C ─────────────────────────
C1_FULL = ("Item 1C. Cybersecurity\n\nRisk management and strategy. We maintain an incident response plan and assess third-party "
           "service provider risk. We engage a third-party assessor and run penetration testing.\n\n"
           "Governance. The Audit Committee of our Board of Directors oversees cybersecurity risk. Our Chief Information "
           "Security Officer reports quarterly. We have not identified any material cybersecurity incident.\n\n")


def _with_1c(c1, headings=FACTORS):
    return _tenk(headings).replace("Item 2. Properties", c1 + "Item 2. Properties")


def test_single_10k_still_gets_standing_presence_evidence():
    out = r.analyze(_tenk(FACTORS), "", "2026-11-01", today=TODAY)
    assert out["status"] == "no_prior"
    topics = {p["tx"] for p in out["presence"]}
    assert {"TX-CYB", "TX-CAP", "TX-TRD", "TX-OPS", "TX-GOV"} <= topics
    cyb = [e for e in out["evidence"]["TX-CYB"] if e["source"] == r.SOURCE]
    assert len(cyb) == 1 and cyb[0]["points"] == r.PTS_PRESENCE and cyb[0]["trend"] is False


def test_presence_scales_with_factor_count_and_needs_a_parsed_section():
    many = FACTORS + [f"A ransomware attack number {i} could disrupt our systems and expose data." for i in range(3)]
    out = r.analyze(_tenk(many), "", "2026-11-01", today=TODAY)
    assert [p["points"] for p in out["presence"] if p["tx"] == "TX-CYB"] == [r.PTS_PRESENCE_MANY]
    assert r.analyze("No risk factor section here at all. " * 40, "", "2026-11-01", today=TODAY)["presence"] == []


def test_item_1c_fully_described_scores_zero_but_makes_cyber_assessed():
    out = r.analyze(_with_1c(C1_FULL), "", "2026-11-01", today=TODAY)
    c1 = [e for e in out["evidence"]["TX-CYB"] if e["source"] == "10-K Item 1C"]
    assert out["item_1c"]["found"] and out["item_1c"]["checks"]["incident_disclosed"] is False
    assert len(c1) >= 5 and sum(e["points"] for e in c1) == 0.0
    assert out["item_1c"]["checks"]["independent_assessment"] is True


def test_item_1c_gaps_and_disclosed_incident_add_points():
    thin = "Item 1C. Cybersecurity\n\nWe take security seriously. We experienced a ransomware incident in the year.\n\n"
    out = r.analyze(_with_1c(thin), "", "2026-11-01", today=TODAY)
    c1 = {e["label"]: e["points"] for e in out["evidence"]["TX-CYB"] if e["source"] == "10-K Item 1C"}
    assert out["item_1c"]["checks"]["incident_disclosed"] is True
    assert c1["Board / committee oversight of cyber risk: not described"] == 0.4
    assert c1["Named management role (CISO / security leader): not described"] == 0.3
    assert c1["Item 1C states the company has experienced a cybersecurity incident"] == 0.8


def test_missing_1c_only_flagged_when_required():
    new = r.analyze(_tenk(FACTORS), "", "2026-11-01", today=TODAY)
    assert new["item_1c"] == {"found": False, "checks": {}, "chars": 0}
    assert any("No Item 1C" in e["label"] for e in new["evidence"]["TX-CYB"])
    old = r.analyze(_tenk(FACTORS), "", "2022-11-01", today=TODAY)
    assert old["item_1c"] is None and not any(e["source"] == "10-K Item 1C" for e in old["evidence"]["TX-CYB"])


def test_extract_ignores_cross_references_and_toc():
    body = "\n\n".join(
        [f"Failure to manage risk number {i} could adversely affect our business results." + "\n\n" + ("Detail sentence about the risk. " * 12)
         for i in range(10)])
    text = ("Item 1A. Risk Factors 12 Item 1B. Unresolved Staff Comments 30\n\n"          # TOC row
            "Item 1A. Risk Factors\n\n" + body + "\n\nItem 1B. Unresolved Staff Comments\n\nNone.\n\n"
            "Item 1C. Cybersecurity\n\nRefer to \"Item 1A. Risk Factors\" for more.\n\n"
            "Item 7. MD&A\n\n" + ("Revenue grew because of demand. " * 200))
    sec = r.extract_item_1a(text)
    assert "number 3" in sec and "Revenue grew" not in sec
    assert len(r.split_risk_factors(sec)) == 10


def test_group_banners_and_leadins_are_not_factors():
    assert not r._is_heading("Risks Related to Demand, Supply, and Manufacturing")
    assert not r._is_heading("Our success depends on our ability to:")
    assert r._is_heading("Competition could adversely impact our market share and financial results.")
