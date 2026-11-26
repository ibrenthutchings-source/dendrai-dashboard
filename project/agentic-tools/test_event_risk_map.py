"""8-K routing: mapped / review / ignored, 1.03 and 4.01 policy, 5.02 roles."""
from datetime import date

import event_risk_map as m
import risk_taxonomy as rt

TODAY = date(2026, 11, 26)


def _filing(items, d="2026-11-01", acc="0001"):
    return {"items": items, "date": d, "accession_number": acc, "item_descriptions": {}}


def _route(items, details=None, d="2026-11-01"):
    return m.route_filing(_filing(items, d), details, today=TODAY)


def test_every_known_8k_item_is_in_exactly_one_state():
    import edgar_tool
    for item in edgar_tool._8K_ITEMS:
        states = [item in m.ITEM_ROUTES or item in m.DETAIL_ITEMS, item in m.IGNORED_ITEMS, item in m.REVIEW_ITEMS]
        assert sum(states) == 1, f"{item} must be mapped, ignored or review - got {states}"


def test_every_material_item_the_backend_classifies_is_routed():
    import edgar_tool
    for item in edgar_tool._MATERIAL_8K_ITEMS:
        assert item in m.ITEM_ROUTES or item in m.DETAIL_ITEMS, item


def test_all_routes_point_at_real_taxonomy_ids():
    for item, routes in m.ITEM_ROUTES.items():
        for tx, pts, why in routes:
            assert tx in rt.TAXONOMY_IDS and pts > 0 and why


def test_ignored_and_review_items_are_not_scored_but_recorded():
    r = _route("7.01, 9.01")
    assert r["status"] == "ignored" and not r["routes"] and len(r["ignored"]) == 2
    r = _route("8.01")
    assert r["status"] == "review" and r["review"][0]["item"] == "8.01"


def test_bankruptcy_floors_liquidity_red_and_forces_gate_signoff():
    det = {"1.03": m.extract_1_03_details("filed voluntary petitions under Chapter 11")}
    r = _route("1.03", det)
    assert "forces_gate_signoff" in r["flags"] and r["severity"] == "P1"
    agg = m.aggregate_evidence([{"risk_routing": r, "date": "2026-11-01", "accession_number": "B", "items": "1.03"}], TODAY)
    assert {g["flag"] for g in agg["gate_flags"]} >= {"forces_gate_signoff", "bankruptcy"}
    res = {x["id"]: x for x in rt.score_taxonomy({}, extra_evidence=agg["evidence"])["risks"]}
    assert res["TX-LIQ"]["rag_status"] == "R" and res["TX-LIQ"]["score"] >= 15
    assert any("Chapter 11" in e["label"] for e in res["TX-LIQ"]["evidence"])


def test_auditor_change_routine_vs_adverse():
    routine = m.extract_4_01_details(
        'The audit committee approved the dismissal of X following a competitive process. There were no '
        'disagreements and no "reportable events". The reports did not contain an adverse opinion or a disclaimer of opinion.')
    assert routine["routine"] and not routine["p1_reasons"]
    r = _route("4.01", {"4.01": routine})
    assert r["severity"] == "P3" and "auditor_change_routine" in r["flags"]

    for text, reason in [
        ("X resigned as auditor.", "resigned"),
        ("There were disagreements with X regarding revenue recognition.", "disagreement"),
        ("The Company was advised of a reportable event: a material weakness.", "reportable_event"),
        ("The report included a going concern explanatory paragraph.", "going_concern_opinion"),
    ]:
        d = m.extract_4_01_details(text)
        assert reason in d["p1_reasons"], (text, d)
        rr = _route("4.01", {"4.01": d})
        assert rr["severity"] == "P1" and "auditor_change_p1" in rr["flags"]
        txs = {x["tx"] for x in rr["routes"]}
        assert {"TX-AUD", "TX-FRQ"} <= txs


def test_dismissal_without_audit_committee_approval_is_p1():
    d = m.extract_4_01_details("The Company dismissed X as its auditor. There were no disagreements.")
    assert "dismissal_without_audit_committee_approval" in d["p1_reasons"]


def test_auditor_change_without_body_is_flagged_for_review_not_silently_p3():
    r = _route("4.01", None)
    assert "needs_review" in r["flags"]


def test_5_02_routes_by_role_and_nature():
    cfo = m.extract_5_02_details("Item 5.02(b) The Chief Financial Officer resigned effective immediately.")
    txs = {x["tx"] for x in _route("5.02", {"5.02": cfo})["routes"]}
    assert {"TX-GOV", "TX-FRQ", "TX-AUD"} <= txs

    ceo = m.extract_5_02_details("The Chief Executive Officer will step down.")
    assert any(x["tx"] == "TX-GOV" and x["points"] >= 1.0 for x in _route("5.02", {"5.02": ceo})["routes"])

    dis = m.extract_5_02_details("Director resigned due to a disagreement with the Company regarding strategy.")
    r = _route("5.02", {"5.02": dis})
    assert "disagreement_departure" in r["flags"] and any(x["tx"] == "TX-REG" for x in r["routes"])

    comp = m.extract_5_02_details("The Compensation Committee approved a new employment agreement and equity award.")
    assert [x["tx"] for x in _route("5.02", {"5.02": comp})["routes"]] == ["TX-GOV"]


def test_5_02_never_touches_a_category_that_does_not_exist():
    for d in (None, m.extract_5_02_details("director appointed"), m.extract_5_02_details("")):
        for x in _route("5.02", {"5.02": d} if d else None)["routes"]:
            assert x["tx"] in rt.TAXONOMY_IDS


def test_recency_weights_old_events_down_and_drops_very_old():
    assert m.recency_weight("2026-11-01", TODAY) == 1.0
    assert m.recency_weight("2026-06-01", TODAY) == 0.75
    assert m.recency_weight("2026-01-15", TODAY) == 0.5
    assert m.recency_weight("2025-03-01", TODAY) == 0.2
    assert m.recency_weight("2023-01-01", TODAY) == 0.0
    agg = m.aggregate_evidence([{"risk_routing": _route("4.02", None, "2023-01-01"),
                                 "date": "2023-01-01", "accession_number": "O", "items": "4.02"}], TODAY)
    assert not agg["evidence"] and agg["unrouted"][0]["status"] == "stale"


def test_clustered_5_02_filings_add_a_governance_pattern_item():
    cfo = m.extract_5_02_details("The Chief Financial Officer resigned.")
    evs = [{"risk_routing": _route("5.02", {"5.02": cfo}, d), "date": d, "accession_number": a, "items": "5.02"}
           for d, a in [("2026-10-01", "1"), ("2026-08-15", "2"), ("2026-07-01", "3")]]
    agg = m.aggregate_evidence(evs, TODAY)
    assert any("compressed turnover" in e["label"] for e in agg["evidence"]["TX-GOV"])


def test_per_category_event_points_are_capped():
    r = _route("1.03, 2.04, 4.02, 2.06")
    agg = m.aggregate_evidence([{"risk_routing": r, "date": "2026-11-20", "accession_number": "Z", "items": "x"}] * 3, TODAY)
    for tx, items in agg["evidence"].items():
        assert sum(i["points"] for i in items if i["source"] == "SEC 8-K" and "compressed" not in i["label"]) <= m.MAX_EVENT_POINTS_PER_CATEGORY + 1e-6
