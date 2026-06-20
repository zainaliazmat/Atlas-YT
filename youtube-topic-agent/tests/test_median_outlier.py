"""Offline proof of the median-outlier logic — NO network, NO API keys.

Run (from the project folder):  python tests/test_median_outlier.py

Scenario: a channel whose normal videos pull ~10k views, but one hit 200k.
The median-views baseline should be ~10k, so median_outlier ≈ 20. Crucially, a
2-day-old video must be EXCLUDED from the baseline (too new to have matured —
counting it would fake the baseline) and Shorts must be excluded too.
"""
import datetime as dt
import pathlib
import sys

# tests/ lives one level below the project code; put the project on the path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import youtube


def _vid(vid, views, days_old, seconds=600):
    """A videos.list-shaped item with statistics + snippet + contentDetails."""
    published = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_old)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    mins, secs = divmod(seconds, 60)
    return {
        "id": vid,
        "snippet": {"title": vid, "channelId": "chan", "channelTitle": "Normal Channel",
                    "publishedAt": published},
        "statistics": {"viewCount": str(views)},
        "contentDetails": {"duration": f"PT{mins}M{secs}S"},
    }


def main():
    # The channel's recent uploads: five mature ~10k long-form videos ...
    baseline_pool = [
        _vid("n1", 9_000, 40), _vid("n2", 10_000, 55), _vid("n3", 11_000, 70),
        _vid("n4", 9_500, 90), _vid("n5", 10_500, 120),
        # ... a 2-DAY-OLD viral spike that must be EXCLUDED (too new to count) ...
        _vid("toonew", 500_000, 2),
        # ... and a Short that must be EXCLUDED (40s) ...
        _vid("short1", 80_000, 30, seconds=40),
        # ... plus the candidate breakout itself, which must be EXCLUDED.
        _vid("breakout", 200_000, 25),
    ]

    median = youtube.compute_median(baseline_pool, exclude_ids={"breakout"})
    print(f"median recent views (baseline): {median:,.0f}")

    assert median is not None, "five mature long-form videos should yield a median"
    assert 9_000 <= median <= 11_000, (
        f"baseline should be ~10k (2-day-old spike + Short + candidate excluded), "
        f"got {median}")

    # Now score the breakout against that baseline.
    breakout = _vid("breakout", 200_000, 25)
    subs = {"chan": 50_000}          # subs ratio would be only 200000/50000 = 4
    medians = {"chan": median}
    scored = youtube.score_videos([breakout], subs, medians)
    v = scored[0]
    print(f"median_outlier x{v['median_outlier']}  (subs_ratio x{v['subs_ratio']}, "
          f"ranked-on outlier_ratio x{v['outlier_ratio']})")

    assert 18 <= v["median_outlier"] <= 22, (
        f"median_outlier should be ≈20 (200k / ~10k), got {v['median_outlier']}")
    assert v["outlier_ratio"] == v["median_outlier"], \
        "the median signal must be the PRIMARY ranked signal when available"
    assert v["subs_ratio"] == 4.0, "subs_ratio should still be kept for transparency"

    # Fallback: too few usable baseline videos -> None -> caller uses subs ratio.
    thin = youtube.compute_median([_vid("a", 10_000, 30), _vid("b", 10_000, 30)],
                                  exclude_ids=set())
    assert thin is None, "fewer than 3 usable videos must fall back (return None)"
    fb = youtube.score_videos([breakout], subs, {"chan": None})[0]
    assert fb["median_outlier"] is None and fb["outlier_ratio"] == fb["subs_ratio"], \
        "with no median, ranking must fall back to subs_ratio"

    print("\n✅ PASS — median_outlier ≈ 20; 2-day-old video, Short, and candidate "
          "excluded from baseline; fewer-than-3 falls back to subs ratio.")


if __name__ == "__main__":
    main()
