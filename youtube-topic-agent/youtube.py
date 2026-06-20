"""YouTube data gathering and scoring — all free.

Uses the YouTube Data API v3 (free quota: ~10,000 units/day) plus the free,
unofficial YouTube search-autocomplete endpoint. No paid services.

Two outlier signals (see SKILL.md):
  • subs_ratio    = video_views / channel_subscribers      (fast, rough proxy)
  • median_outlier = video_views / channel's median recent views   (sharper)
The median signal is the PRIMARY one when available; it needs the extra
channel-baseline calls enabled by `--deep` (see gather(deep=True)) and falls
back to subs_ratio per-video whenever the baseline can't be built.
"""
import datetime as dt
import pathlib
import statistics
import time

import requests

YT = "https://www.googleapis.com/youtube/v3"

# Channel-baseline cache: median recent views per channel, so repeat runs don't
# re-fetch the same channels. Refreshed when entries get older than 7 days.
CHANNEL_CACHE = pathlib.Path(__file__).parent / "channel_cache.json"
CACHE_TTL_DAYS = 7
BASELINE_MIN_VIDEOS = 3   # need at least this many usable uploads to trust a median
BASELINE_MAX_AGE_DAYS = 14  # videos newer than this haven't matured -> excluded
SHORTS_MAX_SECONDS = 60   # treat uploads <= this as Shorts -> excluded from baseline


def get_suggestions(seed: str):
    """Free YouTube search autocomplete — these are real queries people type."""
    try:
        r = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "ds": "yt", "q": seed},
            timeout=10,
        )
        return r.json()[1]
    except Exception:
        return [seed]


def search_videos(api_key, query, max_results=15, days=90):
    """search.list — costs 100 quota units per call. Returns a list of video IDs."""
    after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    r = requests.get(
        f"{YT}/search",
        params={
            "key": api_key, "part": "snippet", "q": query, "type": "video",
            "order": "viewCount", "maxResults": max_results,
            "publishedAfter": after, "relevanceLanguage": "en",
        },
        timeout=15,
    )
    r.raise_for_status()
    return [it["id"]["videoId"] for it in r.json().get("items", [])]


def get_videos(api_key, video_ids, parts="snippet,statistics"):
    """videos.list — 1 unit per call, batched by 50. Returns stats + snippet.

    `parts` is overridable so the baseline builder can also request
    contentDetails (for duration -> Shorts filtering) without changing the
    default fast-path behaviour.
    """
    out = []
    for i in range(0, len(video_ids), 50):
        r = requests.get(
            f"{YT}/videos",
            params={"key": api_key, "part": parts,
                    "id": ",".join(video_ids[i:i + 50])},
            timeout=15,
        )
        r.raise_for_status()
        out.extend(r.json().get("items", []))
    return out


def get_subs(api_key, channel_ids):
    """channels.list — 1 unit per call. Returns {channel_id: subscriber_count}."""
    subs, ids = {}, list(set(channel_ids))
    for i in range(0, len(ids), 50):
        r = requests.get(
            f"{YT}/channels",
            params={"key": api_key, "part": "statistics",
                    "id": ",".join(ids[i:i + 50])},
            timeout=15,
        )
        r.raise_for_status()
        for it in r.json().get("items", []):
            stats = it.get("statistics", {})
            # Channels can hide their sub count; mark those as 0 (handled in scoring).
            if stats.get("hiddenSubscriberCount"):
                subs[it["id"]] = 0
            else:
                subs[it["id"]] = int(stats.get("subscriberCount", 0))
    return subs


def get_channels(api_key, channel_ids):
    """channels.list with part=statistics,contentDetails — 1 unit per call, batched 50.

    Folds the subscriber count AND the uploads-playlist id into a SINGLE call
    (vs. get_subs, which fetches only stats). Returns:
        {channel_id: {"subs": int, "uploads": "<uploads playlist id or None>"}}
    The uploads playlist is the gateway to a channel's recent videos, which we
    use to compute its median-views baseline.
    """
    info, ids = {}, list(set(channel_ids))
    for i in range(0, len(ids), 50):
        r = requests.get(
            f"{YT}/channels",
            params={"key": api_key, "part": "statistics,contentDetails",
                    "id": ",".join(ids[i:i + 50])},
            timeout=15,
        )
        r.raise_for_status()
        for it in r.json().get("items", []):
            stats = it.get("statistics", {})
            subs = 0 if stats.get("hiddenSubscriberCount") else int(
                stats.get("subscriberCount", 0))
            uploads = (it.get("contentDetails", {})
                       .get("relatedPlaylists", {})
                       .get("uploads"))
            info[it["id"]] = {"subs": subs, "uploads": uploads}
    return info


def get_uploads(api_key, uploads_playlist, max_results=20):
    """playlistItems.list — 1 unit per call. Returns recent video IDs (newest first)."""
    r = requests.get(
        f"{YT}/playlistItems",
        params={"key": api_key, "part": "contentDetails",
                "playlistId": uploads_playlist, "maxResults": max_results},
        timeout=15,
    )
    r.raise_for_status()
    return [it["contentDetails"]["videoId"] for it in r.json().get("items", [])]


def _parse_duration(iso: str) -> float:
    """Parse an ISO-8601 duration (e.g. 'PT1H2M30s', 'PT45S') to seconds.

    YouTube reports video length in this format under contentDetails.duration.
    Returns 0.0 if it can't be parsed (so an unparseable value won't be treated
    as a Short and silently dropped from the baseline)."""
    import re
    m = re.fullmatch(
        r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0.0
    days, hours, mins, secs = (int(g or 0) for g in m.groups())
    return days * 86400 + hours * 3600 + mins * 60 + secs


def _days_since(published_at: str) -> float:
    pub = dt.datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)
    return max((now - pub).total_seconds() / 86400, 1.0)


def compute_median(videos, exclude_ids=(), exclude_shorts=True,
                   min_videos=BASELINE_MIN_VIDEOS):
    """Median view count over a channel's recent uploads — the baseline.

    PURE / offline-testable: takes already-fetched videos.list items and applies
    the baseline rules, so the median math can be tested without any network:
      • EXCLUDE the candidate video(s) themselves (`exclude_ids`).
      • EXCLUDE videos newer than 14 days — too new to have matured; counting
        them would fake outliers (a fresh viral hit would inflate the baseline).
      • EXCLUDE Shorts (duration <= 60s) so the baseline reflects long-form.
    Returns the median, or None if fewer than `min_videos` usable videos remain
    (too thin to trust — caller should fall back to the subs ratio).
    """
    exclude_ids = set(exclude_ids)
    usable = []
    for v in videos:
        if v.get("id") in exclude_ids:
            continue
        try:
            views = int(v["statistics"].get("viewCount", 0))
        except (KeyError, ValueError):
            continue
        if _days_since(v["snippet"]["publishedAt"]) < BASELINE_MAX_AGE_DAYS:
            continue
        if exclude_shorts:
            dur = _parse_duration(v.get("contentDetails", {}).get("duration", ""))
            if 0 < dur <= SHORTS_MAX_SECONDS:
                continue
        usable.append(views)
    if len(usable) < min_videos:
        return None
    return statistics.median(usable)


def _cache_fresh(entry):
    """True if a channel_cache.json entry is present and younger than the TTL."""
    return (isinstance(entry, dict) and "ts" in entry
            and (time.time() - entry["ts"]) < CACHE_TTL_DAYS * 86400)


def build_medians(api_key, videos, channel_info, cache_path=CHANNEL_CACHE,
                  exclude_shorts=True, log=print):
    """Build {channel_id: median_recent_views or None} for every candidate channel.

    Cache-backed: fresh entries in channel_cache.json (median + timestamp) are
    reused; stale/missing ones are fetched and written back. Each channel is
    wrapped in its own try/except so one bad channel can't sink the run — it just
    falls back to None (and the caller uses the subs ratio for that video).
    `channel_info` is the get_channels() result (subs + uploads playlist id).
    """
    # chat_state has the corruption-tolerant JSON helpers; import lazily so
    # youtube.py stays importable on its own (e.g. in the offline median test).
    import chat_state
    cache = chat_state.load_json(cache_path, {})

    # Exclude every candidate video from its own channel's baseline.
    candidate_ids = {v["id"] for v in videos}
    channel_ids = {v["snippet"]["channelId"] for v in videos}

    medians, fetched = {}, 0
    for cid in channel_ids:
        entry = cache.get(cid)
        if _cache_fresh(entry):
            medians[cid] = entry.get("median")
            continue
        try:
            uploads = (channel_info.get(cid) or {}).get("uploads")
            if not uploads:
                raise ValueError("no uploads playlist")
            ids = get_uploads(api_key, uploads, max_results=20)
            recent = get_videos(api_key, ids,
                                parts="statistics,snippet,contentDetails")
            med = compute_median(recent, exclude_ids=candidate_ids,
                                 exclude_shorts=exclude_shorts)
            medians[cid] = med
            cache[cid] = {"median": med, "ts": time.time()}
            fetched += 1
        except Exception as e:
            log(f"  ! baseline failed for channel {cid} ({e}); using subs ratio")
            medians[cid] = None  # signal fallback for this channel's videos

    if fetched:
        try:
            chat_state.atomic_write_json(cache_path, cache)
        except Exception as e:
            log(f"  ! could not write {cache_path.name}: {e}")
    return medians


def score_videos(videos, subs, medians=None):
    """Attach outlier signals + views_per_day, then rank (outliers first).

    `medians` (optional, from build_medians) keys channel_id -> median recent
    views. When given and usable for a video's channel, the PRIMARY signal is
    median_outlier = views / median; otherwise we fall back to the old
    subs_ratio = views / subs (per video). Both numbers are kept on each result
    for transparency. `outlier_ratio` is the best-available signal (median if we
    have it, else subs) and is what we rank by — so callers and the old
    fast-path test that read `outlier_ratio` keep working unchanged.

    With medians=None this is byte-for-byte the original behaviour.
    """
    medians = medians or {}
    scored = []
    for v in videos:
        try:
            views = int(v["statistics"].get("viewCount", 0))
        except (KeyError, ValueError):
            continue
        if views < 5000:          # drop noise
            continue
        ch = v["snippet"]["channelId"]
        s = subs.get(ch, 0)
        med = medians.get(ch)

        # subs_ratio needs a real sub count; median_outlier needs a real median.
        subs_ratio = round(views / s, 1) if s > 0 else None
        median_outlier = round(views / med, 1) if med and med > 0 else None

        # No usable signal at all (hidden subs AND no median) -> can't rank it.
        if subs_ratio is None and median_outlier is None:
            continue

        # PRIMARY: median when we have it, else the subs proxy.
        outlier_ratio = median_outlier if median_outlier is not None else subs_ratio

        days = _days_since(v["snippet"]["publishedAt"])
        scored.append({
            "title": v["snippet"]["title"],
            "channel": v["snippet"]["channelTitle"],
            "views": views,
            "subs": s,
            "outlier_ratio": outlier_ratio,        # best available (ranked on)
            "median_outlier": median_outlier,      # primary signal, or None
            "subs_ratio": subs_ratio,              # secondary signal, or None
            "baseline_views": int(med) if med else None,  # the median used
            "views_per_day": int(views / days),
            "days_old": int(days),
            "url": f"https://youtu.be/{v['id']}",
        })
    scored.sort(key=lambda x: (x["outlier_ratio"], x["views_per_day"]), reverse=True)
    return scored


def gather(api_key, queries, per_query=15, days=90, top=30, deep=False, log=print):
    """Full free data pipeline: search -> stats -> subs -> score. Returns top videos.

    deep=False (default): fast path — channel subs only, subs_ratio outliers.
        Unchanged from the original; cheap (~600 quota units/run).
    deep=True: also builds each channel's median-recent-views baseline (the
        sharper signal). Costs a few extra units per channel (cached for 7 days).
        Fully guarded: if the baseline machinery fails for any reason, it logs and
        falls back to the exact fast-path result, so --deep can never break a run.
    """
    ids = []
    for q in queries:
        try:
            ids += search_videos(api_key, q, per_query, days)
        except Exception as e:
            log(f"  ! search failed for '{q}': {e}")
    ids = list(dict.fromkeys(ids))     # dedupe, keep order
    if not ids:
        return []
    videos = get_videos(api_key, ids)
    channel_ids = [v["snippet"]["channelId"] for v in videos]

    if deep:
        try:
            # One channels.list call gets BOTH subs and the uploads playlist.
            info = get_channels(api_key, channel_ids)
            subs = {cid: i["subs"] for cid, i in info.items()}
            medians = build_medians(api_key, videos, info, log=log)
            usable = sum(1 for m in medians.values() if m)
            log(f"  · built median baselines for {usable}/{len(medians)} channels")
            return score_videos(videos, subs, medians)[:top]
        except Exception as e:
            log(f"  ! deep baseline pass failed ({e}); falling back to subs ratio")
            # fall through to the fast path below

    subs = get_subs(api_key, channel_ids)
    return score_videos(videos, subs)[:top]
