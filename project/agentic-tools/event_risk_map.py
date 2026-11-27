#!/usr/bin/env python3
"""
SEC Form 8-K  ->  risk taxonomy routing.

Single source of truth for which 8-K items move which risk categories, by how
much, and why. Pure and deterministic (regex on the filing text, no LLM), so
every routing decision is testable and can be shown to a HITL reviewer.

Every item lands in exactly one of three states:

    mapped   routed to one or more taxonomy categories with evidence points
    review   applicability depends on content we could not determine
             (e.g. 8.01 "Other Events") — surfaced to the reviewer, NOT scored
    ignored  routine / administrative / not applicable to this entity class,
             with the reason recorded (never silently dropped)

Evidence points are LIKELIHOOD points (see risk_taxonomy): a category's
likelihood is 1.0 + the sum of its evidence, impact comes from the category.

Item 4.01 (auditor change) and 1.03 (bankruptcy) are handled per the agreed
policy:
  * 1.03 -> Liquidity & Going Concern (+ Financial Reporting, Capital
    Structure, Strategic); Liquidity is FLOORED at Red and the filing forces
    an explicit reviewer sign-off at Gate 1.
  * 4.01 -> Audit Oversight & ICFR. P1 when the filing discloses a
    resignation / declined re-election, a disagreement, a reportable event,
    an adverse / disclaimer / going-concern audit report, or a dismissal NOT
    described as an audit-committee-approved selection. A dismissal that the
    audit committee approved with no such flags is a routine rotation (P3).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

import risk_taxonomy as rt

# ── Recency ─────────────────────────────────────────────────────────────
# (max age in days, weight). Older than the last band contributes nothing.
RECENCY_BANDS = [(90, 1.0), (180, 0.75), (365, 0.5), (730, 0.2)]
MAX_EVENT_POINTS_PER_CATEGORY = 4.0   # cap so a burst of filings can't saturate a category
P1_POINTS, P2_POINTS = 2.0, 0.8       # route points >= P1 -> "P1", >= P2 -> "P2", else "P3"


def recency_weight(filing_date: str, today: Optional[date] = None) -> float:
    today = today or date.today()
    try:
        d = datetime.strptime((filing_date or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0.5  # unknown date: count at half weight rather than silently drop
    age = (today - d).days
    for limit, w in RECENCY_BANDS:
        if age <= limit:
            return w
    return 0.0


def severity_of(points: float) -> str:
    return "P1" if points >= P1_POINTS else "P2" if points >= P2_POINTS else "P3"


# ── Static item routes ──────────────────────────────────────────────────
# item -> list of (taxonomy_id, points, rationale)
ITEM_ROUTES: dict[str, list[tuple[str, float, str]]] = {
    "1.01": [("TX-STR", 0.3, "New material agreement: commitments / early signal of a transaction")],
    "1.02": [("TX-STR", 0.6, "Termination of a material agreement"),
             ("TX-OPS", 0.3, "Counterparty relationship lost — check operational dependency")],
    "1.03": [("TX-LIQ", 4.0, "Bankruptcy / receivership — solvency event"),
             ("TX-CAP", 2.5, "Debt obligations in default or being restructured"),
             ("TX-FRQ", 1.5, "Going-concern basis of accounting no longer assured"),
             ("TX-STR", 1.0, "Control of the enterprise passes to a court process")],
    "1.05": [("TX-CYB", 2.5, "Material cybersecurity incident disclosed"),
             ("TX-REG", 0.8, "SEC / state-law notification and disclosure obligations triggered")],
    "2.01": [("TX-STR", 1.0, "Completed acquisition or disposition"),
             ("TX-FRQ", 0.4, "Purchase-accounting / deconsolidation estimates"),
             ("TX-CAP", 0.3, "Financing or capital-structure effect of the transaction")],
    "2.03": [("TX-CAP", 0.8, "New direct financial obligation / off-balance-sheet arrangement"),
             ("TX-LIQ", 0.4, "Covenant and repayment burden")],
    "2.04": [("TX-LIQ", 2.5, "Obligation accelerated or increased by a triggering event"),
             ("TX-CAP", 2.0, "Default / acceleration of a direct financial obligation")],
    "2.05": [("TX-OPS", 1.0, "Exit or disposal activity (restructuring)"),
             ("TX-PRF", 0.4, "Restructuring charges")],
    "2.06": [("TX-FRQ", 1.2, "Material impairment — valuation and estimate risk"),
             ("TX-PRF", 0.8, "Asset-quality deterioration")],
    "3.01": [("TX-REG", 1.5, "Listing-standard deficiency notice"),
             ("TX-LIQ", 1.0, "Going-concern / capital-access implications"),
             ("TX-CAP", 0.8, "Capital-structure alternatives constrained")],
    "3.02": [("TX-CAP", 0.6, "Unregistered equity issuance (dilution / financing need)"),
             ("TX-LIQ", 0.3, "Indicates external funding reliance")],
    "3.03": [("TX-GOV", 0.8, "Security-holder rights materially modified"),
             ("TX-CAP", 0.6, "Capital-structure change")],
    "4.02": [("TX-FRQ", 3.0, "Non-reliance on previously issued financial statements (restatement)"),
             ("TX-AUD", 2.0, "Implies an ICFR failure and management-override question"),
             ("TX-REG", 1.0, "Heightened SEC enforcement / comment exposure")],
    "5.01": [("TX-GOV", 1.5, "Change in control of the registrant"),
             ("TX-STR", 1.0, "Strategic direction may change"),
             ("TX-CAP", 0.5, "Financing arrangements may be triggered")],
    "5.03": [("TX-GOV", 0.4, "Articles / bylaws amended — review for shareholder-rights impact")],
    "5.05": [("TX-GOV", 0.5, "Code of ethics amended or waived"),
             ("TX-REG", 0.3, "Ethics-code disclosure obligations")],
    "5.06": [("TX-STR", 0.5, "Change in shell-company status")],
    "5.08": [("TX-GOV", 0.8, "Shareholder director nomination window / possible proxy contest")],
}

# Items that are intentionally NOT scored, with the recorded reason.
IGNORED_ITEMS: dict[str, str] = {
    "1.04": "Mine-safety reporting applies only to mine operators; no category impact otherwise",
    "2.02": "Earnings release — results already captured from XBRL; the release itself is not a risk event",
    "5.04": "Employee-benefit-plan blackout notice — routine",
    "6.01": "ABS-issuer informational item — not applicable to operating companies",
    "6.02": "ABS-issuer servicer change — not applicable to operating companies",
    "6.03": "ABS-issuer credit enhancement — not applicable to operating companies",
    "6.04": "ABS-issuer distribution failure — not applicable to operating companies",
    "6.05": "ABS-issuer securities-act updates — not applicable to operating companies",
    "7.01": "Regulation FD disclosure — informational; no risk signal by itself",
    "9.01": "Financial statements and exhibits — administrative",
}

# Items whose meaning depends on content: shown to the reviewer, not scored.
REVIEW_ITEMS: dict[str, str] = {
    "8.01": "Other Events — content varies (litigation, regulatory, voluntary); reviewer to confirm applicability",
}

# Items with a dedicated content parser below.
DETAIL_ITEMS = {"1.03", "4.01", "5.02", "5.07"}

# Say-on-pay support -> TX-GOV likelihood points. Thresholds are investor-policy
# signals, not legal limits: the advisory vote is non-binding, ISS expects board
# responsiveness below 70% support, Glass Lewis below 80%, and <50% is a failed vote.
SAY_ON_PAY_FAIL, SAY_ON_PAY_LOW, SAY_ON_PAY_WATCH = 0.50, 0.70, 0.80


def say_on_pay_points(support: float) -> float:
    return 1.5 if support < SAY_ON_PAY_FAIL else 1.0 if support < SAY_ON_PAY_LOW else 0.5 if support < SAY_ON_PAY_WATCH else 0.0


# ── Deterministic content parsers ──────────────────────────────────────

def _norm(text: str) -> str:
    t = (text or "").lower()
    return re.sub(r"\s+", " ", t.replace("“", '"').replace("”", '"').replace("’", "'"))


def extract_1_03_details(text: str) -> dict:
    t = _norm(text)
    chapter = None
    m = re.search(r"chapter\s+(7|11|15|13|12)\b", t)
    if m:
        chapter = m.group(1)
    return {
        "chapter": chapter,
        "receivership": bool(re.search(r"receiver(ship)?\b", t)),
        "liquidation": bool(re.search(r"liquidat", t)) or chapter == "7",
    }


def extract_4_01_details(text: str) -> dict:
    t = _norm(text)
    dismissed = bool(re.search(r"\b(dismiss(ed|al)|terminat(ed|ion) of (the )?(engagement|appointment|relationship))", t))
    resigned = bool(re.search(
        r"\b(resign(ed|ation)|declined to stand for re-?election|will not stand for re-?election|"
        r"decline[sd]? to (be )?re-?engag|informed the company that it (would|will) not)", t))
    no_disagree = bool(re.search(r"\bno\s+(such\s+)?disagreements?\b|\bnot\s+(any\s+)?disagreements?\b|\bwithout\s+disagreement", t))
    disagreement = bool(re.search(r"\bdisagreements?\b", t)) and not no_disagree
    no_reportable = bool(re.search(r"\bno\s+(such\s+)?\"?reportable\s+events?", t))
    reportable = bool(re.search(r"reportable\s+events?", t)) and not no_reportable
    clean_opinion = bool(re.search(r"did not contain an adverse opinion or (a )?disclaimer", t))
    adverse = (bool(re.search(r"adverse opinion|disclaimer of opinion", t)) and not clean_opinion)
    going_concern = bool(re.search(r"going concern", t))
    ac_approved = bool(re.search(
        r"audit committee[^.]{0,160}(approved|recommended|selected|engaged|appointed|decided)|"
        r"(competitive|request for proposals?|rfp|selection process)", t))
    flags = {
        "dismissed": dismissed, "resigned": resigned, "disagreement": disagreement,
        "reportable_event": reportable, "adverse_or_disclaimer": adverse,
        "going_concern_opinion": going_concern, "audit_committee_approved": ac_approved,
    }
    p1_reasons = [k for k in ("resigned", "disagreement", "reportable_event",
                              "adverse_or_disclaimer", "going_concern_opinion") if flags[k]]
    if dismissed and not ac_approved and not p1_reasons:
        p1_reasons.append("dismissal_without_audit_committee_approval")
    flags["p1_reasons"] = p1_reasons
    flags["routine"] = bool(dismissed and ac_approved and not p1_reasons)
    return flags


_ROLE_PATTERNS = {
    "ceo": r"chief executive officer|\bceo\b|principal executive officer",
    "cfo": r"chief financial officer|\bcfo\b|principal financial officer",
    "cao": r"chief accounting officer|principal accounting officer|\bcontroller\b",
    "audit_committee": r"audit committee",
    "chair": r"chair(man|woman|person)? of the board|board chair|lead independent director",
    "director": r"\bdirector\b|board of directors",
}
_DEPART = r"resign|retire|terminat|departure|step(s|ping)? down|stepped down|removed|not stand for re-?election|decline[sd]? to stand|leave the company|will leave"
_APPOINT = r"appointed|elected|named|will serve as|has been selected|promoted"


def extract_5_02_details(text: str) -> dict:
    t = _norm(text)
    paragraphs = sorted(set(re.findall(r"5\.02\s*\(\s*([a-f])\s*\)", t)))
    roles = {k: bool(re.search(p, t)) for k, p in _ROLE_PATTERNS.items()}
    departure = bool(re.search(_DEPART, t))
    appointment = bool(re.search(_APPOINT, t))
    disagreement = bool(re.search(r"disagreement", t)) and not re.search(r"\bno\s+(such\s+)?disagreements?\b|not\s+(the\s+result\s+of|due to|because of)\s+(any\s+)?disagreement", t)
    comp_only = bool(re.search(r"compensat|employment agreement|retention (award|bonus)|equity award", t)) and not departure and not appointment
    return {
        "paragraphs": paragraphs, "roles": roles,
        "departure": departure, "appointment": appointment,
        "disagreement": disagreement, "compensation_only": comp_only,
    }


_NUM = r"\b\d{1,3}(?:,\d{3})+\b|\b\d{4,}\b"


def extract_5_07_details(text: str) -> dict:
    """Item 5.07 vote results -> say-on-pay support and failed-proposal signals.

    Support = For / (For + Against), the figure proxy advisers use (abstentions
    and broker non-votes excluded). Falls back to a stated percentage."""
    t = _norm(text)
    sop_pat = (r"say[- ]on[- ]pay|advisory (?:vote|resolution|basis)[^.]{0,120}?(?:compensation|named executive officers)"
               r"|compensation of (?:the |our )?named executive officers")
    support = for_v = against_v = None
    has_sop = False
    for m in re.finditer(sop_pat, t):
        before = t[max(0, m.start() - 90):m.start()]
        window = t[m.end(): m.end() + 500]
        if re.search(r"frequency|every (one|two|three) years?|one year|three years", before + window[:120]):
            continue  # the frequency vote, not the approval vote
        has_sop = True
        nums = [int(x.replace(",", "")) for x in re.findall(_NUM, window)]
        if len(nums) >= 2 and nums[0] + nums[1] > 0:
            for_v, against_v = nums[0], nums[1]
            support = for_v / (for_v + against_v)
            break
        pct = re.search(r"(\d{2,3}(?:\.\d+)?)\s*%", window[:300])
        if pct and 0 < float(pct.group(1)) <= 100:
            support = float(pct.group(1)) / 100
            break
    failed = bool(re.search(
        r"(?:was|were) not (?:approved|adopted|ratified)|failed to (?:receive|obtain|pass)|"
        r"did not receive (?:the required|a majority|sufficient)|was (?:rejected|defeated)", t))
    return {
        "has_say_on_pay": has_sop,
        "say_on_pay_support": None if support is None else round(support, 3),
        "say_on_pay_for": for_v, "say_on_pay_against": against_v,
        "proposal_failed": failed,
    }


# ── Routing ────────────────────────────────────────────────────────────

def _routes_5_07(details: Optional[dict]) -> tuple[str, list, list[str], str]:
    """-> (state, routes, flags, reason); state in mapped | review | ignored."""
    if not details:
        return "review", [], [], "Shareholder-vote results — filing text unavailable; say-on-pay support not evaluated"
    routes: list[tuple[str, float, str]] = []
    flags: list[str] = []
    sup = details.get("say_on_pay_support")
    if sup is not None:
        pts = say_on_pay_points(sup)
        routes.append(("TX-GOV", pts,
                       f"Say-on-pay support {sup:.0%} (fail <50%; ISS responsiveness <70%; Glass Lewis <80%)"))
        if sup < SAY_ON_PAY_FAIL:
            flags.append("say_on_pay_failed")
        elif sup < SAY_ON_PAY_LOW:
            flags.append("say_on_pay_low")
    if details.get("proposal_failed"):
        routes.append(("TX-GOV", 1.0, "A proposal or director election failed / did not receive the required vote"))
        flags.append("vote_failed")
    if routes:
        return "mapped", routes, flags, ""
    if details.get("has_say_on_pay"):
        return "review", [], [], "Say-on-pay vote present but the tallies could not be parsed — reviewer to read the filing"
    return "ignored", [], [], "Shareholder-vote results with no say-on-pay vote and no failed proposal — routine"


def _routes_for_item(item: str, details: Optional[dict]) -> tuple[list[tuple[str, float, str]], list[str]]:
    """Return (routes, flags) for one item, honouring parsed details."""
    flags: list[str] = []
    if item == "4.01":
        if not details:
            return ([("TX-AUD", 1.0, "Auditor change — filing content unavailable; reviewer to classify")], ["needs_review"])
        if details["p1_reasons"]:
            why = ", ".join(r.replace("_", " ") for r in details["p1_reasons"])
            routes = [("TX-AUD", 3.0, f"Auditor change with adverse indicator(s): {why}"),
                      ("TX-FRQ", 1.2, "Auditor change raises ICFR / reported-number reliability questions")]
            if details["disagreement"] or details["reportable_event"]:
                routes.append(("TX-REG", 0.6, "Item 304 disagreement / reportable event — SEC attention likely"))
            if details["going_concern_opinion"]:
                routes.append(("TX-LIQ", 1.0, "Prior audit report referenced a going-concern uncertainty"))
            flags.append("auditor_change_p1")
            return routes, flags
        if details["routine"]:
            return [("TX-AUD", 0.5, "Routine auditor rotation (audit-committee approved, no disagreements / reportable events)")], ["auditor_change_routine"]
        return [("TX-AUD", 1.0, "Auditor change — nature unclear from filing; reviewer to classify")], ["needs_review"]
    if item == "5.02":
        if not details:
            return ([("TX-GOV", 0.6, "Director / officer change — filing content unavailable; reviewer to classify")], ["needs_review"])
        r = details["roles"]
        routes: list[tuple[str, float, str]] = []
        if details["departure"]:
            if r["ceo"]:
                routes.append(("TX-GOV", 1.2, "CEO / principal executive officer departure"))
            if r["cfo"] or r["cao"]:
                routes.append(("TX-FRQ", 0.8, "CFO / accounting-officer departure — financial-reporting continuity"))
                routes.append(("TX-AUD", 0.8, "Departure of an ICFR-owning officer"))
                routes.append(("TX-GOV", 0.6, "Senior officer departure"))
            if r["audit_committee"]:
                routes.append(("TX-AUD", 0.5, "Audit-committee membership change"))
            if details["disagreement"]:
                routes.append(("TX-GOV", 2.0, "Departure attributed to a disagreement with the company"))
                routes.append(("TX-REG", 0.5, "Item 5.02(a) disagreement letter may be filed with the SEC"))
                flags.append("disagreement_departure")
            if r.get("chair"):
                routes.append(("TX-GOV", 1.0, "Board chair / lead independent director departure"))
            if not routes:
                routes.append(("TX-GOV", 0.6, "Director departure (board rotation)" if r.get("director") else "Director / officer departure"))
        elif details["appointment"] and not details["compensation_only"] and r.get("director") and not (r["ceo"] or r["cfo"] or r["cao"]):
            routes.append(("TX-GOV", 0.0, "Board refreshment — new director appointed"))
        elif details["appointment"] and not details["compensation_only"]:
            routes.append(("TX-GOV", 0.3, "Leadership transition (new appointment)"))
            if r["cfo"] or r["cao"]:
                routes.append(("TX-FRQ", 0.3, "New CFO / accounting officer — transition risk to reporting"))
        elif details["compensation_only"]:
            routes.append(("TX-GOV", 0.2, "Compensatory arrangement change for a named officer"))
        else:
            routes.append(("TX-GOV", 0.4, "Director / officer change"))
        return routes, flags
    if item == "1.03":
        base = list(ITEM_ROUTES["1.03"])
        flags += ["bankruptcy", "forces_gate_signoff", "floor_liquidity_red"]
        if details and details.get("chapter"):
            base[0] = ("TX-LIQ", 4.0, f"Bankruptcy — Chapter {details['chapter']} filing")
        return base, flags
    return list(ITEM_ROUTES.get(item, [])), flags


def route_filing(filing: dict, texts_details: Optional[dict] = None,
                 today: Optional[date] = None) -> dict:
    """Route one annotated 8-K filing.

    filing: an edgar_tool.annotate_8k() dict (needs `items` / `item_descriptions`,
            `date`, `accession_number`).
    texts_details: {item: parsed-details dict} for DETAIL_ITEMS, when the
            filing body was available.
    Returns {status, routes, ignored, review, flags, severity, weight}.
    """
    texts_details = texts_details or {}
    raw_items = filing.get("items", "") or ""
    items = [i.strip() for i in re.split(r"[,\s]+", raw_items) if i.strip()]
    if not items:
        items = list((filing.get("item_descriptions") or {}).keys())
    weight = recency_weight(filing.get("date", ""), today)

    routes: list[dict] = []
    ignored: list[dict] = []
    review: list[dict] = []
    flags: list[str] = []
    for item in items:
        if item in IGNORED_ITEMS:
            ignored.append({"item": item, "reason": IGNORED_ITEMS[item]})
            continue
        if item in REVIEW_ITEMS:
            review.append({"item": item, "reason": REVIEW_ITEMS[item]})
            continue
        if item == "5.07":
            state, item_routes, item_flags, why = _routes_5_07(texts_details.get(item))
            if state == "ignored":
                ignored.append({"item": item, "reason": why})
                continue
            if state == "review":
                review.append({"item": item, "reason": why})
                continue
        else:
            item_routes, item_flags = _routes_for_item(item, texts_details.get(item))
        if not item_routes:
            review.append({"item": item, "reason": "Item code has no routing rule — reviewer to confirm applicability"})
            continue
        flags += item_flags
        for tx, pts, why in item_routes:
            routes.append({"item": item, "tx": tx, "points": pts, "rationale": why})

    if routes:
        status = "mapped"
    elif review:
        status = "review"
    else:
        status = "ignored"
    sev = severity_of(max((r["points"] for r in routes), default=0.0)) if routes else None
    board_change = None
    d502 = texts_details.get("5.02")
    if d502 and "5.02" in items and d502["roles"].get("director") and not any(d502["roles"].get(k) for k in ("ceo", "cfo", "cao")):
        board_change = "departure" if d502["departure"] else "appointment" if d502["appointment"] else None
    return {
        "status": status, "routes": routes, "ignored": ignored, "review": review,
        "flags": sorted(set(flags)), "severity": sev, "weight": weight,
        "board_change": board_change,
    }


def aggregate_evidence(events: list[dict], today: Optional[date] = None) -> dict:
    """Fold routed events into taxonomy evidence.

    events: each carrying `risk_routing` (route_filing output) plus
            `date` / `accession_number`.
    Returns {
      "evidence": {tx: [evidence items]},
      "gate_flags": [{flag, accession, date, item_labels}],
      "unrouted": [{accession, date, status, reasons}],
    }
    """
    evidence: dict[str, list[dict]] = {}
    gate_flags: list[dict] = []
    unrouted: list[dict] = []
    per_cat_total: dict[str, float] = {}
    gov_5_02_dates: list[str] = []
    rotation = {"departures_12m": 0, "departures_24m": 0, "appointments_12m": 0, "appointments_24m": 0}
    say_on_pay: Optional[dict] = None

    for e in events:
        rr = e.get("risk_routing") or {}
        acc, d = e.get("accession_number", ""), e.get("date", "")
        w = rr.get("weight", recency_weight(d, today))
        if rr.get("status") != "mapped":
            reasons = [x["reason"] for x in (rr.get("ignored") or []) + (rr.get("review") or [])]
            unrouted.append({"accession": acc, "date": d, "status": rr.get("status", "unrouted"),
                             "items": e.get("items", ""), "reasons": reasons})
            continue
        if w <= 0:
            unrouted.append({"accession": acc, "date": d, "status": "stale",
                             "items": e.get("items", ""), "reasons": ["Older than 24 months — no longer counted"]})
            continue
        bc = rr.get("board_change")
        if bc:
            kind = "departures" if bc == "departure" else "appointments"
            rotation[f"{kind}_24m"] += 1
            if w >= 0.5:
                rotation[f"{kind}_12m"] += 1
        for f in rr.get("flags", []):
            if f in ("forces_gate_signoff", "auditor_change_p1", "disagreement_departure", "bankruptcy",
                     "say_on_pay_failed", "say_on_pay_low", "vote_failed"):
                gate_flags.append({"flag": f, "accession": acc, "date": d, "items": e.get("items", "")})
        for r in rr["routes"]:
            if r["item"] == "5.02":
                gov_5_02_dates.append(d)
            if r["item"] == "5.07" and r["rationale"].startswith("Say-on-pay") and say_on_pay is None:
                say_on_pay = {"date": d, "accession": acc, "rationale": r["rationale"]}
            pts = round(r["points"] * w, 2)
            cap_left = MAX_EVENT_POINTS_PER_CATEGORY - per_cat_total.get(r["tx"], 0.0)
            pts = max(0.0, min(pts, cap_left))
            per_cat_total[r["tx"]] = per_cat_total.get(r["tx"], 0.0) + pts
            item = rt.ev("SEC 8-K", f"Item {r['item']} ({d}): {r['rationale']}", pts, trend=True,
                         observed={"accession": acc, "date": d, "item": r["item"], "weight": w})
            if "floor_liquidity_red" in rr.get("flags", []) and r["tx"] == "TX-LIQ":
                item["floor"] = "R"
            evidence.setdefault(r["tx"], []).append(item)

    # Clustering: a compressed run of 5.02 filings is itself a governance signal.
    recent = sorted(d for d in gov_5_02_dates if recency_weight(d, today) >= 0.5)
    if len(recent) >= 3:
        evidence.setdefault("TX-GOV", []).append(rt.ev(
            "SEC 8-K", f"{len(recent)} Item 5.02 filings within 12 months — compressed turnover pattern",
            0.8, trend=True, observed={"count": len(recent)}))
    if rotation["departures_12m"] >= 3:
        evidence.setdefault("TX-GOV", []).append(rt.ev(
            "SEC 8-K", f"{rotation['departures_12m']} director departures within 12 months — board instability",
            0.6, trend=True, observed=dict(rotation)))
    return {"evidence": evidence, "gate_flags": gate_flags, "unrouted": unrouted,
            "board_rotation": rotation, "say_on_pay": say_on_pay}
