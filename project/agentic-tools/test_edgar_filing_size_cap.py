"""The /edgar/proxy OOM guard (2026-09-10): DEF 14A / 10-K primary documents
run 5-20 MB of HTML and BeautifulSoup+lxml build a tree 10-30x that size, so
an unbounded parse in a request worker OOM-kills the process. fetch_filing_text
now reads a capped prefix and _strip_html truncates defensively.
"""
import edgar_tool


def test_strip_html_truncates_oversized_input():
    huge = "<html><body><p>Executive Compensation</p>" + ("x" * (edgar_tool._MAX_FILING_BYTES + 50_000)) + "</body></html>"
    out = edgar_tool._strip_html(huge)
    assert len(out) <= edgar_tool._MAX_FILING_BYTES
    assert "Executive Compensation" in out  # front of the doc is preserved


def test_strip_html_leaves_normal_filings_untouched():
    body = "<p>Board of Directors</p><div>Say-on-Pay advisory vote</div>"
    assert edgar_tool._strip_html(body) == "Board of Directors\n\nSay-on-Pay advisory vote"


def test_get_text_capped_stops_at_max_bytes(monkeypatch):
    class _FakeResp:
        encoding = "utf-8"
        apparent_encoding = "utf-8"
        def raise_for_status(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_content(self, chunk_size=65536):
            # 20 MB in 1 MB chunks — far past the cap
            for _ in range(20):
                yield b"a" * 1_000_000

    monkeypatch.setattr(edgar_tool, "_sleep", lambda: None)
    monkeypatch.setattr(edgar_tool.requests, "get", lambda *a, **k: _FakeResp())

    text = edgar_tool._get_text_capped("https://example.com/big.htm")
    assert text is not None
    # stopped near the cap, not after downloading all 20 MB
    assert edgar_tool._MAX_FILING_BYTES <= len(text) < edgar_tool._MAX_FILING_BYTES + 1_000_000


def test_get_text_capped_returns_none_on_error(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("dns")
    monkeypatch.setattr(edgar_tool, "_sleep", lambda: None)
    monkeypatch.setattr(edgar_tool.requests, "get", _boom)
    assert edgar_tool._get_text_capped("https://example.com/x.htm") is None
