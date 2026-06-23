"""Sage's eyes — the SEARCH + FETCH seam (free, mostly no-key).

The engine never talks to a search API directly; it calls the helpers here. To
swap the WEB backend you change ONE place: the `WEB_BACKEND` constant below.
Everything is wrapped defensively — a flaky or rate-limited source returns an
empty result and logs a note, it never crashes a research run.

Sources, all free:
- web  : DuckDuckGo via the `ddgs` library  — NO API KEY. (default)
- wiki : Wikipedia REST API                  — NO API KEY. encyclopedic baseline.
- news : GDELT 2.0 Doc API                    — NO API KEY. current events.
         (GDELT throttles to ~1 request / 5s; we self-throttle + retry, then
          degrade gracefully.)
- fetch: requests + a dependency-free stdlib HTML-to-text extractor.

Optional paid upgrades behind the SAME web seam (set WEB_BACKEND + the key):
- "tavily" : high-quality search built for LLMs. Free tier; needs TAVILY_API_KEY.
- "brave"  : Brave Search API. Free tier; needs BRAVE_API_KEY.

Each result is a plain dict:  {"url", "title", "snippet", "source_type"}.
"""
from __future__ import annotations

import os
import time
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

# ======================================================================
# THE ONE SWITCH — which web-search backend to use
# ======================================================================
WEB_BACKEND = os.environ.get("FLUX_SEARCH", "ddgs").strip().lower()

USER_AGENT = "production-coach/0.1 (educational research tool)"
_HTTP_TIMEOUT = 20


def _log(quiet: bool, msg: str) -> None:
    if not quiet:
        print(msg)


# ----------------------------------------------------------------------
# WEB SEARCH — pluggable backend (default: ddgs / DuckDuckGo, no key)
# ----------------------------------------------------------------------
def _web_ddgs(query: str, max_results: int) -> list[dict]:
    from ddgs import DDGS  # lazy import so other backends don't require the lib
    out = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            url = r.get("href") or r.get("url") or ""
            if not url:
                continue
            out.append({
                "url": url,
                "title": r.get("title", "").strip(),
                "snippet": (r.get("body") or r.get("snippet") or "").strip(),
                "source_type": "web",
            })
    return out


def _web_tavily(query: str, max_results: int) -> list[dict]:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY missing (WEB_BACKEND='tavily').")
    r = requests.post("https://api.tavily.com/search",
                      json={"api_key": key, "query": query,
                            "max_results": max_results},
                      timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return [{"url": x.get("url", ""), "title": x.get("title", "").strip(),
             "snippet": (x.get("content") or "").strip(), "source_type": "web"}
            for x in r.json().get("results", []) if x.get("url")]


def _web_brave(query: str, max_results: int) -> list[dict]:
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        raise RuntimeError("BRAVE_API_KEY missing (WEB_BACKEND='brave').")
    r = requests.get("https://api.search.brave.com/res/v1/web/search",
                     params={"q": query, "count": max_results},
                     headers={"X-Subscription-Token": key,
                              "Accept": "application/json"},
                     timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    results = r.json().get("web", {}).get("results", [])
    return [{"url": x.get("url", ""), "title": x.get("title", "").strip(),
             "snippet": (x.get("description") or "").strip(), "source_type": "web"}
            for x in results if x.get("url")]


_WEB_BACKENDS = {"ddgs": _web_ddgs, "tavily": _web_tavily, "brave": _web_brave}


def web_search(query: str, max_results: int = 5, *, quiet: bool = True) -> list[dict]:
    """Web search via the selected WEB_BACKEND. Degrades to [] on any failure."""
    fn = _WEB_BACKENDS.get(WEB_BACKEND)
    if fn is None:
        _log(quiet, f"  · (unknown WEB_BACKEND {WEB_BACKEND!r}; skipping web)")
        return []
    try:
        return fn(query, max_results)
    except Exception as exc:  # noqa: BLE001 — a search source must never crash a run
        _log(quiet, f"  · (web search failed for {query!r}: {exc})")
        return []


# ----------------------------------------------------------------------
# WIKIPEDIA — encyclopedic baseline (no key)
# ----------------------------------------------------------------------
def wiki_search(query: str, max_results: int = 3, *, quiet: bool = True) -> list[dict]:
    """Search Wikipedia and return article hits with short extracts. [] on failure."""
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query,
                    "format": "json", "srlimit": max_results},
            headers={"User-Agent": USER_AGENT}, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        hits = r.json().get("query", {}).get("search", [])
    except Exception as exc:  # noqa: BLE001
        _log(quiet, f"  · (wikipedia search failed for {query!r}: {exc})")
        return []

    out = []
    for h in hits:
        title = h.get("title", "")
        # strip the HTML <span> markup Wikipedia puts in snippets
        snippet = _extract_text(h.get("snippet", ""))
        url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
        out.append({"url": url, "title": title, "snippet": snippet,
                    "source_type": "wikipedia"})
    return out


# ----------------------------------------------------------------------
# NEWS — GDELT current events (no key, but throttled ~1 req / 5s)
# ----------------------------------------------------------------------
_GDELT_MIN_INTERVAL = 5.2  # GDELT 429s if you exceed ~1 request every 5 seconds
_gdelt_last_call = 0.0


def news_search(query: str, max_results: int = 5, timespan: str = "1month",
                *, quiet: bool = True) -> list[dict]:
    """Recent news via GDELT. Self-throttles + one retry on 429; [] on failure."""
    global _gdelt_last_call
    for attempt in range(2):
        wait = _GDELT_MIN_INTERVAL - (time.time() - _gdelt_last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            _gdelt_last_call = time.time()
            r = requests.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={"query": query, "mode": "artlist", "maxrecords": max_results,
                        "timespan": timespan, "format": "json", "sort": "datedesc"},
                headers={"User-Agent": USER_AGENT}, timeout=_HTTP_TIMEOUT)
            if r.status_code == 429:
                _log(quiet, "  · (news rate-limited; backing off)")
                time.sleep(_GDELT_MIN_INTERVAL)
                continue
            r.raise_for_status()
            if "json" not in r.headers.get("content-type", ""):
                return []  # GDELT returns plain-text errors for odd queries
            arts = r.json().get("articles", [])
            return [{"url": a.get("url", ""), "title": (a.get("title") or "").strip(),
                     "snippet": "", "source_type": "news",
                     "date": a.get("seendate", "")}
                    for a in arts if a.get("url")]
        except Exception as exc:  # noqa: BLE001
            _log(quiet, f"  · (news search failed for {query!r}: {exc})")
            return []
    return []


# ----------------------------------------------------------------------
# PAGE FETCH + extraction (requests + stdlib HTML-to-text)
# ----------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    """Collect visible text, dropping script/style/nav noise. Stdlib only."""
    _SKIP = {"script", "style", "noscript", "head", "nav", "footer", "header",
             "form", "svg"}

    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            t = data.strip()
            if t:
                self._chunks.append(t)

    def text(self) -> str:
        return " ".join(self._chunks)


def _extract_text(html: str) -> str:
    try:
        p = _TextExtractor()
        p.feed(html)
        return " ".join(p.text().split())
    except Exception:  # noqa: BLE001 — malformed HTML must not crash extraction
        return ""


def fetch_text(url: str, max_chars: int = 2500, *, quiet: bool = True) -> str:
    """Fetch a page and return readable text (truncated). "" on any failure.

    Upgrade path: for cleaner main-content extraction, `pip install trafilatura`
    and replace `_extract_text(...)` with `trafilatura.extract(html)`.
    """
    if not url:
        return ""
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT},
                         timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        if "html" not in r.headers.get("content-type", "text/html"):
            return ""
        text = _extract_text(r.text)
        return text[:max_chars]
    except Exception as exc:  # noqa: BLE001
        _log(quiet, f"  · (couldn't fetch {url}: {exc})")
        return ""


# ----------------------------------------------------------------------
# Source credibility — a transparent domain heuristic (not a verdict)
# ----------------------------------------------------------------------
_HIGH_CRED_TLDS = (".gov", ".edu", ".mil", ".int")
_KNOWN_AUTHORITATIVE = {
    "wikipedia.org": "Encyclopedic baseline — good for orientation, cross-check primaries.",
    "nasa.gov": "Primary — government space agency.",
    "nih.gov": "Primary — government health/research agency.",
    "who.int": "Primary — international health authority.",
    "nature.com": "Peer-reviewed scientific journal.",
    "science.org": "Peer-reviewed scientific journal.",
    "reuters.com": "Established wire service — generally reliable reporting.",
    "apnews.com": "Established wire service — generally reliable reporting.",
    "bbc.com": "Established news outlet.",
    "bbc.co.uk": "Established news outlet.",
}
_LOW_CRED_HINTS = ("reddit.com", "quora.com", "medium.com", "blogspot.",
                   "wordpress.com", "facebook.com", "x.com", "twitter.com",
                   "youtube.com", "tiktok.com", "pinterest.")


def credibility_note(url: str) -> str:
    """A one-line, transparent credibility hint for a URL. Heuristic, not gospel."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return "Unknown source."
    for domain, note in _KNOWN_AUTHORITATIVE.items():
        if host == domain or host.endswith("." + domain):
            return note
    if any(host.endswith(tld) for tld in _HIGH_CRED_TLDS):
        return "Government/academic domain — typically authoritative (primary)."
    if any(hint in host for hint in _LOW_CRED_HINTS):
        return "User-generated / social — treat as a lead, verify against a primary."
    return "General web source — credibility unverified; corroborate independently."
