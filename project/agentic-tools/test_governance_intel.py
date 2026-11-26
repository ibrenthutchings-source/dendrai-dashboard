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
