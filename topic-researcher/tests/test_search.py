"""Offline proof for the search/fetch seam — NO network.

Run:  python tests/test_search.py   (or: pytest tests/test_search.py)

The network backends are MOCKED. We assert the seam's CONTRACT:
  - a failing web backend degrades to [] (never raises into a run)
  - credibility_note classifies gov/edu/social/general correctly
  - the stdlib HTML extractor strips script/style and returns visible text
  - news_search degrades to [] on a non-JSON / error response

HONEST NOTE: whether DuckDuckGo/Wikipedia/GDELT return GOOD results live is a
manual/integration check, not unit-tested here.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import search  # noqa: E402


def test_web_search_degrades_on_failure(monkeypatch):
    def boom(q, n):
        raise RuntimeError("network down")
    monkeypatch.setattr(search, "_WEB_BACKENDS", {"ddgs": boom})
    monkeypatch.setattr(search, "WEB_BACKEND", "ddgs")
    assert search.web_search("anything", quiet=True) == []


def test_web_search_unknown_backend_is_empty(monkeypatch):
    monkeypatch.setattr(search, "WEB_BACKEND", "does-not-exist")
    assert search.web_search("anything", quiet=True) == []


def test_credibility_note():
    assert "primary" in search.credibility_note("https://www.nasa.gov/x").lower()
    assert "government" in search.credibility_note("https://cdc.gov/data").lower() \
        or "primary" in search.credibility_note("https://cdc.gov/data").lower()
    assert "social" in search.credibility_note("https://reddit.com/r/x").lower() \
        or "lead" in search.credibility_note("https://reddit.com/r/x").lower()
    assert "encyclopedic" in search.credibility_note(
        "https://en.wikipedia.org/wiki/Q").lower()
    assert search.credibility_note("") == "Unknown source."


def test_html_extractor_strips_noise():
    html = ("<html><head><style>.x{color:red}</style></head>"
            "<body><script>evil()</script><p>Hello   world</p>"
            "<nav>menu junk</nav></body></html>")
    text = search._extract_text(html)
    assert "Hello world" in text
    assert "evil" not in text and "menu junk" not in text and "color:red" not in text


def test_news_search_non_json_is_empty(monkeypatch):
    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/plain"}
        text = "Please limit requests"
        def raise_for_status(self): pass
    monkeypatch.setattr(search, "_gdelt_last_call", 0.0)
    monkeypatch.setattr(search.requests, "get", lambda *a, **k: FakeResp())
    assert search.news_search("x", quiet=True) == []


def test_fetch_text_empty_url():
    assert search.fetch_text("", quiet=True) == ""


if __name__ == "__main__":
    import types

    class _MP:
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, val in reversed(self._undo):
                setattr(obj, name, val)
            self._undo.clear()

    passed = 0
    for fn_name, fn in sorted(globals().items()):
        if not (fn_name.startswith("test_") and isinstance(fn, types.FunctionType)):
            continue
        mp = _MP()
        try:
            if fn.__code__.co_argcount and "monkeypatch" in fn.__code__.co_varnames:
                fn(mp)
            else:
                fn()
            print(f"  ok  {fn_name}")
            passed += 1
        finally:
            mp.undo()
    print(f"\n{passed} tests passed.")
