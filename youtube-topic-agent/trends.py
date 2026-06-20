"""Google Trends signals — a free, best-effort BONUS layer (via pytrends).

pytrends is an UNOFFICIAL scraper of Google Trends. It gets rate-limited and the
endpoint changes without notice, so EVERYTHING here is defensive: any failure
(missing package, rate-limit, timeout, shape change) degrades to an empty/"unknown"
result and a short printed note — it must NEVER crash a research run. The agent
still works fully on YouTube signals alone if Trends is unavailable.

Two signals:
  • rising_queries(keyword)  -> related "rising"/breakout searches (more angles)
  • trend_direction(keyword) -> "rising" / "flat" / "falling" / "unknown"

Results are cached in trends_cache.json (refreshed daily) to avoid hammering the
rate limit, with light sleeps between live calls.
"""
import os
import pathlib
import time

import chat_state  # corruption-tolerant JSON load + atomic write

# pytrends is optional. If it's missing or won't import, the whole module
# silently disables itself — callers get empty/"unknown" and the run continues.
try:
    from pytrends.request import TrendReq
    AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when pytrends is absent
    TrendReq = None
    AVAILABLE = False

# Master off-switch. Set ENABLED=False (or env VIRAL_SCOUT_TRENDS=0, see below)
# to skip Trends entirely even when pytrends is installed — used if the endpoint
# turns out to be unusably rate-limited.
ENABLED = AVAILABLE and os.environ.get("VIRAL_SCOUT_TRENDS", "1") != "0"

TRENDS_CACHE = pathlib.Path(__file__).parent / "trends_cache.json"
CACHE_TTL_SECONDS = 24 * 3600   # refresh daily
SLEEP_BETWEEN_CALLS = 1.0       # be gentle with the unofficial endpoint
TIMEFRAME = "today 3-m"         # last ~90 days
MAX_RISING = 8                  # cap how many rising queries we surface


def _client():
    """A configured pytrends client, or None if Trends is unavailable."""
    if not ENABLED:
        return None
    try:
        return TrendReq(hl="en-US", tz=0, timeout=(10, 25))
    except Exception as e:
        print(f"  · trends: client init failed ({e}); skipping Trends")
        return None


def _cache_get(keyword):
    """Return a still-fresh cached blob for `keyword`, or None."""
    cache = chat_state.load_json(TRENDS_CACHE, {})
    entry = cache.get(keyword.lower())
    if isinstance(entry, dict) and (time.time() - entry.get("ts", 0)) < CACHE_TTL_SECONDS:
        return entry
    return None


def _cache_put(keyword, **fields):
    """Merge `fields` into this keyword's cache entry and stamp the time."""
    try:
        cache = chat_state.load_json(TRENDS_CACHE, {})
        entry = cache.get(keyword.lower(), {})
        entry.update(fields)
        entry["ts"] = time.time()
        cache[keyword.lower()] = entry
        chat_state.atomic_write_json(TRENDS_CACHE, cache)
    except Exception:
        pass  # cache is an optimisation; never let it break a run


def rising_queries(keyword):
    """Rising/breakout related searches for `keyword` (extra candidate angles).

    Returns a list of query strings (possibly empty). Cached daily. Any failure
    -> [] plus a short note.
    """
    cached = _cache_get(keyword)
    if cached is not None and "rising" in cached:
        return cached["rising"]
    if not ENABLED:
        return []

    pt = _client()
    if pt is None:
        return []
    try:
        pt.build_payload([keyword], timeframe=TIMEFRAME)
        time.sleep(SLEEP_BETWEEN_CALLS)
        related = pt.related_queries() or {}
        block = related.get(keyword) or {}
        rising_df = block.get("rising")
        out = []
        if rising_df is not None and not rising_df.empty:
            out = [str(q) for q in rising_df["query"].tolist()[:MAX_RISING]]
        _cache_put(keyword, rising=out)
        return out
    except Exception as e:
        print(f"  · trends: rising_queries('{keyword}') unavailable ({e})")
        return []


def trend_direction(keyword):
    """Direction of search interest over ~90 days: rising / flat / falling / unknown.

    Compares the average of the most-recent third of the series to the average of
    the oldest third; a >15% move counts as a trend. Cached daily; failures ->
    "unknown".
    """
    cached = _cache_get(keyword)
    if cached is not None and "direction" in cached:
        return cached["direction"]
    if not ENABLED:
        return "unknown"

    pt = _client()
    if pt is None:
        return "unknown"
    try:
        pt.build_payload([keyword], timeframe=TIMEFRAME)
        time.sleep(SLEEP_BETWEEN_CALLS)
        iot = pt.interest_over_time()
        if iot is None or iot.empty or keyword not in iot:
            _cache_put(keyword, direction="unknown")
            return "unknown"
        series = [v for v in iot[keyword].tolist() if isinstance(v, (int, float))]
        if len(series) < 6:
            _cache_put(keyword, direction="unknown")
            return "unknown"
        third = max(1, len(series) // 3)
        early = sum(series[:third]) / third
        late = sum(series[-third:]) / third
        if early <= 0:
            direction = "rising" if late > 0 else "flat"
        elif late >= early * 1.15:
            direction = "rising"
        elif late <= early * 0.85:
            direction = "falling"
        else:
            direction = "flat"
        _cache_put(keyword, direction=direction)
        return direction
    except Exception as e:
        print(f"  · trends: trend_direction('{keyword}') unavailable ({e})")
        return "unknown"


def status():
    """One-line human summary of whether Trends is on (for diagnostics)."""
    if not AVAILABLE:
        return "Trends: pytrends not installed — running on YouTube signals only."
    if not ENABLED:
        return "Trends: disabled (VIRAL_SCOUT_TRENDS=0)."
    return "Trends: enabled (best-effort; cached daily)."
