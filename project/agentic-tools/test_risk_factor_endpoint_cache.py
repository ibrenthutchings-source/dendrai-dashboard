"""/edgar/risk-factor-changes: second call for the same filings must not re-download or re-parse."""
import api_server as a


def test_result_is_cached_per_accession_pair(monkeypatch):
    calls = {"fetch": 0}
    monkeypatch.setattr(a, "get_company_info", lambda t: ({"cik": "1"}, {}))
    monkeypatch.setattr(a, "parse_filings", lambda sub, forms: {"10-K": [
        {"date": "2026-02-01", "accession_number": "A-NEW"}, {"date": "2025-02-01", "accession_number": "A-OLD"}]})

    def fake_fetch(cik, f):
        calls["fetch"] += 1
        return ""
    monkeypatch.setattr(a, "fetch_filing_text", fake_fetch)
    a._RF_CACHE.clear()
    req = a.TickerRequest(ticker="ZZZ")
    first = a.edgar_risk_factor_changes(req)
    n = calls["fetch"]
    second = a.edgar_risk_factor_changes(req)
    assert n == 2 and calls["fetch"] == n          # no further downloads
    assert second.get("cached") is True and "cached" not in first


def test_rss_helper_never_raises():
    v = a._rss_mb()
    assert v is None or v > 0
    a._release_memory()
