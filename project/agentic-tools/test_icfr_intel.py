"""Item 9A (ICFR / disclosure controls / attestation) and going-concern parsing."""
import icfr_intel as i
import risk_taxonomy as rt

TOC = "Table of Contents\n\nItem 9A. Controls and Procedures 88\n\nItem 9B. Other Information 90\n\n"

EFFECTIVE = (
    "Item 9A. Controls and Procedures\n\nEvaluation of disclosure controls and procedures. Based on that evaluation, our CEO and CFO "
    "concluded that our disclosure controls and procedures were effective as of year end.\n\n"
    "Management's report. Management is responsible for establishing and maintaining adequate internal control over financial reporting. "
    "Based on this assessment, management concluded that our internal control over financial reporting was effective. "
    "The attestation report of our independent registered public accounting firm on ICFR is included below. "
    "We identified no material weakness. There were no changes in our internal control over financial reporting during the quarter.\n\n"
    "Item 9B. Other Information\n\nNone.")

INEFFECTIVE = (
    "Item 9A. Controls and Procedures\n\nBased on that evaluation, our CEO and CFO concluded that our disclosure controls and procedures "
    "were not effective as of year end. Management concluded that our internal control over financial reporting was not effective "
    "because of a material weakness related to revenue recognition. During the quarter there were changes in our internal control "
    "over financial reporting as we began remediation.\n\nItem 9B. Other Information\n\nNone.")

EXEMPT = (
    "Item 9A. Controls and Procedures\n\nBased on this assessment, management concluded that our internal control over financial reporting was effective. "
    "This annual report does not include an attestation report of our registered public accounting firm because we are a "
    "non-accelerated filer and are not required to include one.\n\nItem 9B. Other Information")


def _doc(sec):
    return TOC + "Item 1. Business " + "We make things. " * 40 + "\n\n" + sec


def test_extraction_skips_the_table_of_contents():
    assert "concluded" in i.extract_item_9a(_doc(EFFECTIVE)) and len(i.extract_item_9a(_doc(EFFECTIVE))) > 500


def test_effective_controls_are_assessed_at_zero_points():
    out = i.analyze(_doc(EFFECTIVE), "2026-02-20")
    assert out["item_9a"]["icfr_effective"] is True and out["item_9a"]["disclosure_controls_effective"] is True
    assert out["item_9a"]["material_weakness"] is False and out["item_9a"]["attestation"] == "attested"
    assert sum(e["points"] for evs in out["evidence"].values() for e in evs) == 0.0
    assert set(out["evidence"]) == {"TX-AUD", "TX-FRQ"} and out["flags"] == []


def test_ineffective_controls_with_material_weakness_score_and_flag():
    out = i.analyze(_doc(INEFFECTIVE), "2026-02-20")
    a = out["item_9a"]
    assert a["icfr_effective"] is False and a["disclosure_controls_effective"] is False and a["material_weakness"] is True
    aud = sum(e["points"] for e in out["evidence"]["TX-AUD"])
    assert aud == 2.0 + 1.0 + 0.3       # ICFR + disclosure controls + change; weakness not double-counted
    assert sum(e["points"] for e in out["evidence"]["TX-FRQ"]) == 2.0
    assert {"icfr_not_effective", "disclosure_controls_not_effective"} <= set(out["flags"])


def test_exempt_filer_gets_small_points_not_a_failure():
    out = i.analyze(_doc(EXEMPT), "2026-02-20")
    assert out["item_9a"]["attestation"] == "exempt" and out["item_9a"]["icfr_effective"] is True
    assert [e["points"] for e in out["evidence"]["TX-AUD"] if "attestation" in e["label"].lower()] == [0.2]


def test_remediated_weakness_and_negated_weakness():
    rem = "Item 9A. Controls and Procedures\n\nManagement concluded that internal control over financial reporting was effective. We remediated the material weakness reported last year.\n\nItem 9B."
    out = i.analyze(_doc(rem))
    assert out["item_9a"]["weakness_remediated"] is True
    assert any("remediated" in e["label"] for e in out["evidence"]["TX-AUD"])
    assert i.parse_9a("We did not identify any material weakness.")["material_weakness"] is False


def test_missing_item_9a_yields_no_control_evidence():
    out = i.analyze("Item 1. Business. " + "We make things. " * 50)
    assert out["item_9a"] == {"found": False} and out["evidence"] == {}


def test_going_concern_states():
    doubt = "Our recurring losses raise substantial doubt about our ability to continue as a going concern."
    assert i.going_concern(doubt)["status"] == "substantial_doubt"
    assert i.going_concern("We have concluded that substantial doubt exists about our ability to continue.")["status"] == "substantial_doubt"
    alle = "Conditions raised substantial doubt, which management's plans have alleviated."
    assert i.going_concern(alle)["status"] == "alleviated"
    for benign in ("There is no substantial doubt about our ability to continue as a going concern.",
                   "Failure to raise capital could raise substantial doubt about our ability to continue as a going concern.",
                   "If we cannot refinance there may be substantial doubt about our going concern status.",
                   "The audit report did not raise substantial doubt."):
        assert i.going_concern(benign)["status"] == "none", benign


def test_going_concern_evidence_and_flag():
    out = i.analyze("Item 1. " + "x " * 50 + "Our losses raise substantial doubt about our ability to continue as a going concern.")
    assert out["evidence"]["TX-LIQ"][0]["points"] == 2.5 and "going_concern" in out["flags"]


def test_evidence_targets_real_taxonomy_ids():
    for d in (EFFECTIVE, INEFFECTIVE, EXEMPT):
        for tx in i.analyze(_doc(d))["evidence"]:
            assert tx in rt.TAXONOMY_IDS


def test_deterministic():
    assert i.analyze(_doc(INEFFECTIVE), "2026-02-20") == i.analyze(_doc(INEFFECTIVE), "2026-02-20")
