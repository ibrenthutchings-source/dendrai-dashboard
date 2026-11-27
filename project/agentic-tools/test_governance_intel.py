"""DEF 14A KRIs, auditor tenure, and SEC comment-letter evidence."""
from datetime import date

import governance_intel as g

TODAY = date(2026, 11, 26)

BOARD = """
Jane Doe, age 66. Director since 2008. ... John Roe, age 71, Director Since: 2011 ...
Amy Poe, age 58. has served as a director since March 2014 ... Sam Lee, age 63, Director since 2016.
Rita Fox, age 59, Director since 2023. The Board has a mandatory retirement age of 75.
"""


def test_tenure_and_age_parsing():
    t = g.parse_director_tenures(BOARD, TODAY)
    assert sorted(t) == [3, 10, 12, 15, 18]
    assert sorted(g.parse_director_ages(BOARD)) == [58, 59, 63, 66, 71]
    assert g.detect_refreshment_policy(BOARD)["mandatory_retirement_age"]


def test_low_parse_does_not_produce_governance_evidence():
    kris = g.compute_kris({"board_of_directors": "Jane Doe, Director since 2010."}, TODAY)
    assert kris["confidence"] == "low"
    assert "TX-GOV" not in g.kris_to_evidence(kris)


def test_stale_board_without_policy_scores_higher_than_refreshed_board_with_policy():
    old = "\n".join(f"Dir{i}, age 70, Director since {2002 + i}." for i in range(6))
    fresh = "\n".join(f"Dir{i}, age 55, Director since {2021 + (i % 4)}. Mandatory retirement age 72." for i in range(6))
    pts = lambda txt: sum(e["points"] for e in g.kris_to_evidence(g.compute_kris({"board_of_directors": txt}, TODAY))["TX-GOV"])
    assert pts(old) > pts(fresh)
    assert pts(fresh) == 0


def test_auditor_tenure_never_claims_partner_rotation_compliance():
    k = g.compute_kris({"board_of_directors": BOARD,
                        "audit_matters": "Ernst & Young LLP has served as our independent registered public accounting firm since 1994."}, TODAY)
    assert k["auditor"] == {"since": 1994, "years": 32}
    ev = g.kris_to_evidence(k)["TX-AUD"][0]
    assert ev["points"] > 0 and "not verifiable" in ev["label"]


def _letters(*pairs):
    return [{"form": f, "date": d, "accession_number": f"A{i}"} for i, (f, d) in enumerate(pairs)]


def test_comment_letter_activity_scoring():
    quiet = g.comment_letters_to_evidence(_letters(("UPLOAD", "2023-01-10")), today=TODAY)
    assert quiet["TX-REG"][0]["points"] == 0.0
    multi = g.comment_letters_to_evidence(_letters(("UPLOAD", "2026-10-20"), ("CORRESP", "2026-09-30"), ("UPLOAD", "2026-08-15")), today=TODAY)
    assert abs(sum(e["points"] for e in multi["TX-REG"]) - (0.6 + 0.6 + 0.4)) < 1e-9
    assert g.comment_letters_to_evidence([], today=TODAY) == {}


def test_comment_topics_route_to_categories():
    topics = g.classify_comment_text("Please revise your non-GAAP measures and describe your internal control over financial reporting; also cybersecurity Item 106.")
    assert {t["tx"] for t in topics} >= {"TX-FRQ", "TX-AUD", "TX-CYB"}
    ev = g.comment_letters_to_evidence(_letters(("UPLOAD", "2026-10-20")), {"A0": topics}, TODAY)
    assert "TX-FRQ" in ev and "TX-AUD" in ev


def test_list_comment_letters_filters_forms_and_sorts():
    sub = {"filings": {"recent": {
        "form": ["10-K", "UPLOAD", "CORRESP", "UPLOAD"],
        "filingDate": ["2026-02-01", "2026-03-01", "2026-03-20", "2020-01-01"],
        "accessionNumber": ["a", "b", "c", "d"], "primaryDocument": ["", "", "", ""]}}}
    out = g.list_comment_letters(sub, months=36)
    assert [l["accession_number"] for l in out] == ["c", "b"]


def test_collect_extra_evidence_survives_every_failure():
    def boom(_):
        raise RuntimeError("edgar down")
    assert g.collect_extra_evidence("X", {}, get_submissions=boom, get_cached_kris=boom) == {}


# ── Board rotation KRIs, policy values, say-on-pay (proxy) ─────────────────
def test_policy_age_is_not_read_as_a_director_age():
    assert g.parse_director_ages("Mandatory retirement age 72. No director may stand after reaching age 75.") == []
    assert g.parse_director_ages("Jane Roe, age 61, Director since 2015") == [61]


def test_rotation_kris_flag_a_board_with_no_new_director():
    stale = "\n".join(f"Dir{i}, age 66, Director since {2008 + i}." for i in range(6))   # newest = 2013 -> 13 yrs
    k = g.compute_kris({"board_of_directors": stale}, TODAY)
    assert k["years_since_last_addition"] == 13 and k["share_joined_5y"] == 0.0
    labels = [(e["label"], e["points"]) for e in g.kris_to_evidence(k)["TX-GOV"]]
    assert any("Board rotation" in l and p == 0.6 for l, p in labels)


def test_policy_values_and_independence_and_say_on_pay():
    txt = ("Our mandatory retirement age is 72. Directors are subject to a term limit of 15 years. "
           "We have a classified board. 6 of our 11 directors are independent. "
           "At the 2026 annual meeting our say-on-pay proposal received approximately 62% support.")
    pv = g.parse_policy_values(txt)
    assert pv["retirement_age"] == 72 and pv["term_limit_years"] == 15 and pv["classified_board"]
    assert pv["independence"] == {"independent": 6, "total": 11, "share": 0.55}
    assert g.parse_say_on_pay_proxy(txt) == 0.62
    ev = g.kris_to_evidence({"n_tenure_parsed": 0, "say_on_pay_prior": 0.62})["TX-GOV"]
    assert ev[0]["points"] == 0.5 and "62%" in ev[0]["label"]    # 1.0 pts tier, halved (prior-year, proxy)


def test_over_retirement_age_directors_are_counted():
    txt = "\n".join(f"Dir{i}, age {70 + i}, Director since {2010 + i}. Mandatory retirement age 72." for i in range(5))
    assert g.compute_kris({"board_of_directors": txt}, TODAY)["over_retirement_age"] == 3
