"""XBRL tag selection (freshest wins) and liquid-asset / capex ratios."""
import edgar_tool as e
import predictive_analytics_tool as p


class _Resp:
    def __init__(self, data): self._d = data
    def json(self): return self._d


def _facts(**tags):
    gaap = {}
    for tag, pts in tags.items():
        gaap[tag] = {"label": tag, "description": "", "units": {"USD": [
            {"end": end, "val": v, "form": "10-K", "fp": "FY", "filed": "2026-03-01", "start": f"{int(end[:4]) - 1}{end[4:]}"}
            for end, v in pts]}}
    return {"facts": {"us-gaap": gaap}}


def test_freshest_tag_wins_over_an_abandoned_legacy_tag(monkeypatch):
    facts = _facts(PaymentsToAcquirePropertyPlantAndEquipment=[("2023-12-31", 1.0)],
                   PaymentsToAcquireProductiveAssets=[("2025-12-31", 6.0), ("2024-12-31", 5.0)])
    monkeypatch.setattr(e, "_get_safe", lambda url: _Resp(facts))
    out = e.fetch_xbrl_facts("1")
    assert out["CapEx"]["tag"] == "PaymentsToAcquireProductiveAssets"
    assert out["CapEx"]["data_points"][0]["end"] == "2025-12-31"


def test_tie_goes_to_the_earlier_tag(monkeypatch):
    facts = _facts(CashAndCashEquivalentsAtCarryingValue=[("2025-12-31", 10.0)],
                   CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents=[("2025-12-31", 11.0)])
    monkeypatch.setattr(e, "_get_safe", lambda url: _Resp(facts))
    assert e.fetch_xbrl_facts("1")["Cash"]["tag"] == "CashAndCashEquivalentsAtCarryingValue"


def _metric(tag, *pts):
    return {"tag": tag, "data_points": [{"end": en, "val": v, "form": "10-K", "fp": "FY", "filed": "2026-03-01"} for en, v in pts]}


def _xbrl(**extra):
    base = {"Revenue": _metric("Revenues", ("2025-12-31", 1000.0), ("2024-12-31", 800.0)),
            "TotalAssets": _metric("Assets", ("2025-12-31", 1000.0), ("2024-12-31", 900.0)),
            "Cash": _metric("Cash", ("2025-12-31", 100.0), ("2024-12-31", 90.0)),
            "OperatingCashFlow": _metric("CFO", ("2025-12-31", 300.0)),
            "CapEx": _metric("Capex", ("2025-12-31", 60.0))}
    base.update(extra)
    return base


def test_cash_ratio_includes_same_period_securities_and_fcf_uses_fresh_capex():
    fr = p.compute_financial_ratios(_xbrl(ShortTermInvestments=_metric("STI", ("2025-12-31", 400.0))))
    assert fr["cash_ratio"] == 0.5 and fr["liquid_now"] == 500.0 and fr["cash_now"] == 100.0
    assert fr["fcf_margin"] == 0.24            # (300 - 60) / 1000


def test_stale_securities_balance_is_never_added():
    fr = p.compute_financial_ratios(_xbrl(ShortTermInvestments=_metric("STI", ("2023-12-31", 400.0))))
    assert fr["cash_ratio"] == 0.1


def test_combined_cash_and_securities_tag_is_used_without_double_counting():
    fr = p.compute_financial_ratios(_xbrl(ShortTermInvestments=_metric("STI", ("2025-12-31", 400.0)),
                                          CashAndShortTermInvestments=_metric("CSTI", ("2025-12-31", 450.0))))
    assert fr["liquid_now"] == 450.0
