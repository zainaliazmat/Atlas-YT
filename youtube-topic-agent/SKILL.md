# SKILL.md — Viral Topic Research Method

This is the method you follow on every run. Work the steps in order.

## Goal
Given a niche, return the ~10 best video topics to make next — ranked, each with
title options, an angle, a thumbnail concept, and the data signal behind it.

## The signals (in priority order)

### 1. Outliers — the #1 signal
A video whose views vastly exceed *what its channel normally gets* proves the
*topic* pulled, not the creator's existing audience.

- **Preferred metric — `median_outlier = video_views / channel_median_recent_views`.**
  Comparing a video to its own channel's median recent views is the sharpest
  outlier signal: it measures how far *this* video broke from *this* channel's
  baseline. The baseline excludes videos newer than 14 days (too unmatured to
  trust), Shorts, and the candidate itself. This is what `--deep` runs compute.
- **Fallback metric — `subs_ratio = video_views / channel_subscribers`.** A faster,
  rougher proxy used when the median baseline can't be built (a channel with fewer
  than 3 usable recent uploads, or if the extra calls fail). The default fast run
  uses this for everything; `--deep` uses it only as a per-video fallback.
- Scale (applies to whichever ratio is in play): 5+ is notable, 20+ a strong
  breakout, 100+ a smash.
- When you have it, **rank and reason on `median_outlier`**; cite `subs_ratio`
  only as supporting context. Both numbers are provided per video for transparency.
- These are gold: demand is already proven, and you can make a better version.

### 2. Velocity — what's hot now
- Metric: `views_per_day = video_views / days_since_published`.
- High velocity on a recent video = the topic is alive right now.
- Prefer videos from the last 90 days. Discount anything older than a year.

### 3. Packaging patterns
- Look across the top performers' titles. Name the patterns you see: curiosity gaps,
  numbers, stakes, transformation arcs, contrarian angles, "I tried X for N days."
- The pattern is reusable even when the exact topic isn't.

### 4. Demand validation
- The autocomplete suggestions for the niche are real queries people type.
  Topics that echo them have built-in search demand.
- When provided, **Google Trends** adds two free (best-effort) signals: *rising*
  related searches (breakout angles worth covering early) and a *trend direction*
  for the niche (rising / flat / falling). A topic on a rising trend is timely;
  favour it and say so. Treat Trends as a bonus — it's sometimes "unknown"
  (rate-limited/unavailable), in which case rely on the YouTube signals alone.

### 5. Gaps
- A topic with strong outliers but few good *recent* videos = an open lane.
  Flag these as your highest-opportunity picks.

## How to score a topic
Combine: outlier strength (most weight) × velocity × demand match × freshness.
A topic backed by a real outlier beats a topic that merely "sounds good."

## What to output (per topic)
- **titles** — 2–3 options, written in proven packaging styles.
- **angle** — the specific take, in one line.
- **thumbnail** — one-sentence concept.
- **why** — the data signal: cite the outlier ratio (prefer `median_outlier`) /
  velocity / search match / rising-trend you saw.
- **confidence** — "strong signal" or "worth testing".

## Rules
- Never invent numbers. Only cite signals present in the data you were given.
- Rank by signal strength, not by what sounds exciting.
- If the niche looks saturated (few outliers, low velocity), say so honestly and
  suggest adjacent angles instead of forcing weak picks.
