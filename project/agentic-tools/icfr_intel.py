#!/usr/bin/env python3
"""
10-K Item 9A (Controls and Procedures) and going-concern language -> taxonomy
evidence. Deterministic regex over the filing text; no LLM.

Every 10-K must state whether disclosure controls and internal control over
financial reporting (ICFR) were effective, whether a material weakness exists,
and — for accelerated filers — whether the auditor attested. That makes this
the one internal-control evidence source available for EVERY filer, so Audit
Oversight and Financial Reporting no longer depend on the proxy parse or an
8-K happening.

Scoring (likelihood points, see risk_taxonomy):

  ICFR concluded NOT effective            TX-AUD +2.0   TX-FRQ +1.5
  Disclosure controls NOT effective       TX-AUD +1.0   TX-FRQ +0.5
  Material weakness identified (and ICFR
    not already scored as ineffective)    TX-AUD +1.0
  Previously reported weakness remediated TX-AUD +0.5   (recent control failure)
  Change in ICFR during the quarter       TX-AUD +0.3
  No auditor attestation (exempt filer)   TX-AUD +0.2   (no independent check)
  ICFR concluded effective                0.0 on TX-AUD and TX-FRQ — an assessed,
                                          benign result, not "no view"

  Going concern: substantial doubt exists TX-LIQ +2.5   TX-FRQ +0.5
                 raised, then alleviated  TX-LIQ +0.8
Hypothetical language ("could raise substantial doubt") and negations ("no
substantial doubt") are not evidence of doubt.

A missing / unparseable Item 9A yields NO evidence (it is reported as not
found), never a clean bill of health.
"""
from __future__ import annotations

import re
from typing import Optional

import risk_taxonomy as rt

SOURCE = "10-K Item 9A"


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").replace("’", "'").replace("“", '"').replace("”", '"'))


def extract_item_9a(text: str) -> str:
    """Longest 'Item 9A. Controls and Procedures' candidate up to Item 9B (the
    first match is normally the table of contents)."""
    if not text:
        return ""
    starts = [m.start() for m in re.finditer(r"item\s*9a(?:\(t\))?[\.\s:—\-–]*controls\s+and\s+procedures", text, flags=re.I)]
    stop = re.compile(r"item\s*9b[\.\s:—\-–]|item\s*9c[\.\s:—\-–]|item\s*10[\.\s:—\-–]*directors", re.I)
    best = ""
    for st in starts:
        m = stop.search(text, st + 200)
        sec = text[st: m.start() if m else min(len(text), st + 60_000)]
        if len(sec) > len(best):
            best = sec
    return best[:80_000]


def _verdict(sentence: str) -> Optional[bool]:
    s = sentence.lower()
    if re.search(r"ineffective|\bnot\s+(?:been\s+)?effective|did not maintain effective|were not designed or operating effectively", s):
        return False
    if re.search(r"(?:was|were|is|are)\s+effective|maintained,? in all material respects,? effective", s):
        return True
    return None


def _conclusion(t: str, subject: str) -> Optional[bool]:
    """True = effective, False = not effective, None = not stated. Sentences that
    announce a conclusion ("concluded", "determined", "based on") win over generic
    descriptions of management's responsibilities."""
    sents = [s for s in re.split(r"(?<=[.;])\s+", t) if re.search(subject, s, re.I)]
    for group in ([s for s in sents if re.search(r"conclu|determin|based on", s, re.I)], sents):
        for s in group:
            v = _verdict(s)
            if v is not None:
                return v
    return None


_MW_NEG = re.compile(r"\bno\s+material weakness|not (?:identified|identify|aware of)[^.]{0,40}material weakness|"
                     r"did not (?:identify|find|detect)[^.]{0,40}material weakness|without (?:any )?material weakness|"
                     r"no (?:significant deficiencies or )?material weaknesses", re.I)
_MW_REMEDIATED = re.compile(r"remediat[^.]{0,200}material weakness|material weakness[^.]{0,200}(?:was|were|has been|have been) remediated", re.I)


def parse_9a(section: str) -> dict:
    t = _norm(section)
    dc = _conclusion(t, r"disclosure controls and procedures")
    icfr = _conclusion(t, r"internal control over financial reporting")
    mw_mentions = bool(re.search(r"material weakness", t, re.I))
    mw_negated = bool(_MW_NEG.search(t))
    remediated = bool(_MW_REMEDIATED.search(t))
    # A weakness described only as remediated, under an "effective" ICFR conclusion, is history, not an open weakness.
    mw = mw_mentions and not mw_negated and not (remediated and icfr is not False)
    if re.search(r"attestation report[^.]{0,200}(?:not required|exempt|not subject)|"
                 r"(?:not required|exempt|not subject|permanently exempt)[^.]{0,200}attestation|"
                 r"non-?accelerated filer|emerging growth company[^.]{0,120}attestation|smaller reporting company[^.]{0,120}attestation", t, re.I):
        attestation = "exempt"
    elif re.search(r"(?:independent )?registered public accounting firm[^.]{0,160}(?:issued|has issued|audited|attestation report)", t, re.I) \
            or re.search(r"attestation report of (?:the )?(?:our )?(?:independent )?registered", t, re.I):
        attestation = "attested"
    else:
        attestation = "unknown"
    no_changes = re.search(r"(?:there were )?no (?:material )?changes? in (?:our )?internal control", t, re.I) or \
        re.search(r"did not (?:experience|have|identify) any (?:material )?changes? in (?:our )?internal control", t, re.I)
    changes = bool(re.search(r"changes? in (?:our )?internal control over financial reporting", t, re.I)) and not no_changes
    return {"disclosure_controls_effective": dc, "icfr_effective": icfr, "material_weakness": mw,
            "weakness_remediated": remediated and not mw, "attestation": attestation, "icfr_changes": changes}


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.;])\s+", _norm(text)) if "substantial doubt" in s.lower()]


_GC_NEG = re.compile(r"\bno substantial doubt|not (?:raise|raised|result in|give rise to)[^.]{0,40}substantial doubt|"
                     r"(?:do|does|did|have|has) not[^.]{0,60}substantial doubt|absence of substantial doubt|"
                     r"substantial doubt (?:does|did|is|was) not", re.I)
_GC_HYP = re.compile(r"\b(?:could|may|might|would|if|whether|potential(?:ly)?)\b[^.]{0,80}substantial doubt|"
                     r"substantial doubt[^.]{0,40}\b(?:could|may|might)\b", re.I)
_GC_ALLEV = re.compile(r"alleviat", re.I)
_GC_POS = re.compile(r"(?:raise|raises|raised|raising|there is|there was|there exists|exists|existed|concluded that|determined that)"
                     r"[^.]{0,80}substantial doubt|substantial doubt[^.]{0,80}(?:exists|existed|remains|about (?:our|the company's) ability)", re.I)


def going_concern(text: str) -> dict:
    """status: substantial_doubt | alleviated | none."""
    positive = alleviated = False
    for s in _sentences(text):
        if _GC_NEG.search(s) or _GC_HYP.search(s):
            continue
        if _GC_POS.search(s):
            if _GC_ALLEV.search(s):
                alleviated = True
            else:
                positive = True
    # "management's plans alleviate the substantial doubt" often sits in its own sentence
    if positive is False and alleviated is False:
        for s in _sentences(text):
            if _GC_ALLEV.search(s) and not _GC_NEG.search(s) and not _GC_HYP.search(s):
                alleviated = True
    return {"status": "substantial_doubt" if positive else "alleviated" if alleviated else "none"}


def analyze(text: str, filing_date: str = "", accession: str = "") -> dict:
    out: dict = {"item_9a": {"found": False}, "going_concern": {"status": "none"}, "evidence": {}, "flags": []}
    ev = out["evidence"]
    add = lambda tx, label, pts, obs=None: ev.setdefault(tx, []).append(
        rt.ev(SOURCE, label, pts, observed={"accession": accession, "value": obs}))

    sec = extract_item_9a(text)
    if sec:
        p = parse_9a(sec)
        out["item_9a"] = {"found": True, **p, "chars": len(sec)}
        icfr_bad = p["icfr_effective"] is False
        if icfr_bad:
            add("TX-AUD", "ICFR concluded NOT effective", 2.0, False)
            add("TX-FRQ", "ICFR concluded NOT effective — reported numbers rest on weak controls", 1.5, False)
            out["flags"].append("icfr_not_effective")
        elif p["icfr_effective"] is True and not p["material_weakness"]:
            add("TX-AUD", "ICFR concluded effective; no material weakness reported", 0.0, True)
            add("TX-FRQ", "ICFR concluded effective", 0.0, True)
        if p["disclosure_controls_effective"] is False:
            add("TX-AUD", "Disclosure controls and procedures concluded NOT effective", 1.0, False)
            add("TX-FRQ", "Disclosure controls and procedures concluded NOT effective", 0.5, False)
            out["flags"].append("disclosure_controls_not_effective")
        if p["material_weakness"] and not icfr_bad:
            add("TX-AUD", "Material weakness identified", 1.0, True)
            out["flags"].append("material_weakness")
        if p["weakness_remediated"]:
            add("TX-AUD", "A previously reported material weakness was remediated (recent control failure)", 0.5, True)
        if p["icfr_changes"]:
            add("TX-AUD", "Change in internal control over financial reporting disclosed for the period", 0.3, True)
        if p["attestation"] == "exempt":
            add("TX-AUD", "No auditor attestation of ICFR (exempt filer) — no independent check", 0.2, "exempt")
        elif p["attestation"] == "attested":
            add("TX-AUD", "Auditor attestation of ICFR present", 0.0, "attested")

    gc = going_concern(text)
    out["going_concern"] = gc
    if gc["status"] == "substantial_doubt":
        add("TX-LIQ", "10-K states substantial doubt about the ability to continue as a going concern", 2.5, "substantial_doubt")
        add("TX-FRQ", "Going-concern uncertainty affects the basis of preparation", 0.5, "substantial_doubt")
        out["flags"].append("going_concern")
    elif gc["status"] == "alleviated":
        add("TX-LIQ", "Substantial doubt about going concern was raised and alleviated by management's plans", 0.8, "alleviated")
        out["flags"].append("going_concern_alleviated")
    return out
