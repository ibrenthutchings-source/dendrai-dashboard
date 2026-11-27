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
    assert [x["tx"] for x in out2["removed"]] == ["TX-TRD"] and out2["evidence"] == {}


def test_hypothetical_to_realised_wording_is_escalated():
    h = FACTORS[1]
    pri = _tenk(FACTORS)
    cur = _tenk(FACTORS, {h: BODY + "We experienced a cybersecurity incident during the year that was contained. "})
    out = r.analyze(cur, pri, "2026-11-01", "2025-11-01", today=TODAY)
    ch = [c for c in out["changes"] if c["change"] == "escalated"]
    assert len(ch) == 1 and ch[0]["tx"] == "TX-CYB" and ch[0]["points"] == r.PTS_ESCALATED
    ev = out["evidence"]["TX-CYB"][0]
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
    assert round(sum(e["points"] for e in fresh["evidence"]["TX-CYB"]), 2) == r.MAX_POINTS_PER_CATEGORY
    old = r.analyze(_tenk(FACTORS + [many[-1]]), _tenk(FACTORS), "2025-02-01", "2024-02-01", today=TODAY)
    assert old["evidence"]["TX-CYB"][0]["points"] == round(r.PTS_NEW * 0.2, 2)     # ~21 months old -> 0.2 band


def test_guards_prior_missing_low_parse_and_restructure():
    assert r.analyze(_tenk(FACTORS), "", "2026-11-01", today=TODAY)["status"] == "no_prior"
    few = r.analyze(_tenk(FACTORS[:3]), _tenk(FACTORS), "2026-11-01", "2025-11-01", today=TODAY)
    assert few["status"] == "low_confidence" and few["evidence"] == {}
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
    assert out["status"] == "structure_changed" and out["evidence"] == {}


def test_unclassifiable_new_factor_is_listed_not_scored():
    cur = FACTORS + ["Zebra migration patterns could adversely affect the herd we may be unable to follow."]
    out = r.analyze(_tenk(cur), _tenk(FACTORS), "2026-11-01", "2025-11-01", today=TODAY)
    assert out["evidence"] == {} and len(out["unclassified"]) == 1


def test_analysis_is_deterministic():
    cur, pri = _tenk(FACTORS + ["A ransomware attack could disrupt production and expose data."]), _tenk(FACTORS)
    a = r.analyze(cur, pri, "2026-11-01", "2025-11-01", today=TODAY)
    b = r.analyze(cur, pri, "2026-11-01", "2025-11-01", today=TODAY)
    assert a == b
