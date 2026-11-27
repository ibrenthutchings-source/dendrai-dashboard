"""compute_rss_signals: industry feeds must surface, but only relevant stories carry weight."""
import predictive_analytics_tool as p


class _Parsed(dict):
    pass


def _feed(entries):
    return lambda url: _Parsed({"entries": entries})


E = lambda t, s="": {"title": t, "summary": s, "published": "2026-11-01"}

ENTRIES = [
    E("Federal Reserve issues enforcement action against regional bank", "penalty fine violation enforcement"),
    E("Semiconductor export controls tighten as new restrictions announced", "sanction penalty violation export controls on chips"),
    E("NVIDIA discloses cybersecurity incident and data breach", "ransomware attack unauthorized access breach"),
    E("Federal Reserve raises interest rates as inflation persists", "rate hike inflation recession risk"),
]


def _run(monkeypatch, **kw):
    monkeypatch.setattr(p, "_HAS_FEEDPARSER", True)
    monkeypatch.setattr(p, "RSS_SIGNAL_FEEDS", [{"name": "Test feed", "url": "x", "weight": 1.0, "domains": ["Regulatory"]}])
    monkeypatch.setattr(p, "feedparser", type("F", (), {"parse": staticmethod(_feed(ENTRIES))}), raising=False)
    return p.compute_rss_signals("NVDA", "NVIDIA CORP", sic_description="Semiconductors & Related Devices", **kw)


def test_company_terms_drop_legal_suffixes():
    assert p._company_terms("NVDA", "NVIDIA CORP") == ["nvda", "nvidia"]
    assert "corp" not in p._company_terms("ACME", "Acme Holdings Corporation Inc")


def test_industry_stems_from_sic_description():
    assert "semico" in p._industry_terms("Semiconductors & Related Devices")
    assert p._industry_relevant("New semiconductor tariffs", p._industry_terms("Semiconductors & Related Devices"))
    assert not p._industry_relevant("Bank enforcement action", p._industry_terms("Semiconductors & Related Devices"))


def test_signals_surface_with_relevance_and_unrelated_bank_story_is_ambient(monkeypatch):
    res = _run(monkeypatch)["feed_results"][0]
    by_title = {s["title"]: s for s in res["signals"]}
    assert len(res["signals"]) == 4                       # nothing is silently dropped
    bank = by_title["Federal Reserve issues enforcement action against regional bank"]
    assert bank["relevance_to_entity"] == "ambient" and bank["velocity"] == 0
    chips = by_title["Semiconductor export controls tighten as new restrictions announced"]
    assert chips["relevance_to_entity"] == "industry" and chips["velocity"] > 0
    own = by_title["NVIDIA discloses cybersecurity incident and data breach"]
    assert own["company_mentioned"] is True and own["relevance_to_entity"] == "company"
    macro = by_title["Federal Reserve raises interest rates as inflation persists"]
    assert macro["relevance_to_entity"] == "macro"


def test_strongest_relevant_stories_sort_first(monkeypatch):
    sigs = _run(monkeypatch)["feed_results"][0]["signals"]
    assert sigs[-1]["velocity"] == 0 and sigs[0]["velocity"] >= sigs[1]["velocity"] >= sigs[2]["velocity"]
