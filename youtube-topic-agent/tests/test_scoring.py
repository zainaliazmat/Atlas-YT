"""Offline proof that the outlier logic works — NO network, NO API keys.

Run (from the project folder):  python tests/test_scoring.py

It builds two fake videos and checks that a small channel's breakout video
(huge views relative to its tiny subscriber base) ranks ABOVE a mega-channel's
flop (lots of subscribers, mediocre views). That ranking is the whole point of
the agent: outlier_ratio = views / subscribers is the #1 signal.
"""
import datetime as dt
import pathlib
import sys

# This test lives in tests/, one level below the project code. Put the project
# folder on the import path so `import youtube` finds youtube.py beside agent.py.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import youtube


def _fake_video(vid, title, channel_id, channel_title, views, days_old):
    """Build a video dict shaped like the YouTube videos.list API response."""
    published = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_old)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": vid,
        "snippet": {
            "title": title,
            "channelId": channel_id,
            "channelTitle": channel_title,
            "publishedAt": published,
        },
        "statistics": {"viewCount": str(views)},
    }


def main():
    # The breakout: a 2,000-subscriber channel whose video pulled 400,000 views.
    # outlier_ratio = 400000 / 2000 = 200  -> a "smash" by the method's scale.
    breakout = _fake_video(
        "vid_breakout", "I quit my job to do this", "chan_small",
        "Tiny Creator", views=400_000, days_old=20,
    )
    # The flop: a 5,000,000-subscriber channel whose video only got 250,000 views.
    # outlier_ratio = 250000 / 5000000 = 0.05  -> underperformed its own audience.
    flop = _fake_video(
        "vid_flop", "Big channel phones it in", "chan_mega",
        "Mega Channel", views=250_000, days_old=10,
    )
    # A noise video below the 5,000-view floor — should be filtered out entirely.
    noise = _fake_video(
        "vid_noise", "Nobody watched this", "chan_small",
        "Tiny Creator", views=300, days_old=5,
    )

    subs = {"chan_small": 2_000, "chan_mega": 5_000_000}
    ranked = youtube.score_videos([flop, breakout, noise], subs)

    print("Ranked result (best first):")
    for v in ranked:
        print(f"  outlier x{v['outlier_ratio']:>6}  | {v['views']:>9,} views "
              f"| {v['subs']:>9,} subs | {v['title']}")

    # --- Assertions: this is the actual test ---
    assert len(ranked) == 2, "the sub-5,000-view noise video should be filtered out"
    assert ranked[0]["title"] == "I quit my job to do this", \
        "the small-channel breakout must rank FIRST"
    assert ranked[0]["outlier_ratio"] > ranked[1]["outlier_ratio"], \
        "the breakout's outlier ratio must beat the mega-channel flop"

    print("\n✅ PASS — small-channel breakout (x{}) ranked above mega-channel flop (x{}); "
          "noise filtered.".format(ranked[0]["outlier_ratio"], ranked[1]["outlier_ratio"]))


if __name__ == "__main__":
    main()
