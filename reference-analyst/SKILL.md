# SKILL.md — The Reference-Measurement Method

This is the method the engine follows on every rubric job. The output of the whole
method is a single structured **rubric** (shape at the bottom) — the measurable
target the rest of the pipeline aims at. Objective measurements and judged reads are
kept cleanly separated and honestly labelled.

## Goal
Given one or more **reference videos** (and optionally the CEO's taste preferences),
produce a rubric a later stage can be tuned toward: what a good video looks/sounds
like, expressed as **banded targets** plus a **style profile** — every measured
value reproducible, every judged value marked as a read.

## Inputs
- `videos` — a local path or a list of local paths. URIs are always local in this
  repo: no remote fetches. A missing file is skipped with a clear note, never a crash.
- `ceo_prefs` (optional) — the CEO's answers to taste questions from a prior round,
  merged in so future videos need less asking.
- `standard` (optional) — the named standard to build or extend (merge into).

## Step 1 — Measure each video (objective, deterministic, offline)
For every video, with FFmpeg + OpenCV only (no model, no network):
- **Pacing** — shot count, average shot length, cuts per minute (scene-cut detection).
- **Motion** — a kinetic score from frame-to-frame difference.
- **Color** — dominant palette (k-means), saturation, brightness.
- **Audio** — integrated LUFS, loudness range, true peak, speech-to-silence ratio,
  average pause. (Skipped cleanly when there's no audio stream.)
- **Structure** — duration, fps, resolution.
Same input → same numbers, every time. Representative frames are saved for Step 3.

## Step 2 — Roll measurements up into banded targets
Each metric becomes a `{value, band}` **target**: the central value plus an acceptable
range. With one video the band is padded soft (it's one clip's quirk). With several,
the band is the spread across them — **the shared DNA**. This is why feeding more
references **tightens** the rubric: the targets converge on what the references have in
common, not on any single video.

## Step 3 — Judge the style profile (the seam, honestly labelled)
From the saved frames, an **injected vision seam** (`vision_fn`) reads what a number
can't hold and returns a **style profile**: `visual_style`, `typography_character`,
`motion_feel`, `mood`, and observed `layout_types`. This is a *read*, not a
measurement — and it's optional: with no seam the judged layer is `pending`; if the
seam fails it degrades to `draft` + an error note. It never blocks the objective half.

> NOTE — there is no script in a reference video, so the judged layer does **not**
> score visual/narration *alignment* (that's about evaluating the system's own
> generated output, out of scope here). It is a style profile that becomes style
> targets.

## Step 4 — Ask the few questions only taste can answer
The measurements describe what the reference *is*; they can't describe what the CEO
*wants*. The engine emits `open_questions` — e.g. "it cuts every 2s: keep that
snappiness or more breathing room?", "it's talking 80% of the time: wall-to-wall or
room for music?" — each tagged with the target it `sets`. The CEO's answers come back
as `ceo_prefs` and are persisted, so the next round asks less.

## Step 5 — Merge into the named standard (durable)
A new build doesn't replace the standard; it **merges**. The new per-video analyses
are added to the standard's accumulated analyses and the bands are recomputed over the
union — so the standard gets more representative every time taste is poured into it.
`ceo_prefs` answers are merged new-over-old. Writes are atomic.

## Rules (non-negotiable)
- **Never launder a judged read as a measurement, or a measurement as a taste call.**
  The number is exact; what it *should* be is the CEO's.
- **Flag what's out of reach.** If the reference does something the generator can't
  reproduce, say so — don't set a target nothing downstream can hit.
- **Don't overfit one clip.** A single-reference rubric has soft, padded bands; say so.
- **Degrade, never crash.** Every external call (ffmpeg, cv2, the vision seam) is
  wrapped; failure returns a placeholder + a note.

## Your output contract (the rubric)
The engine returns a plain dict in this shape (ATLAS stamps/validates `schema_version`
against the frozen `reference_rubric` contract at the boundary — the engine never
imports atlas):

```json
{
  "schema_version": "reference_rubric/1.0",
  "source_videos": ["ref1.mp4", "ref2.mp4"],
  "targets": {
    "pacing":    { "avg_shot_sec": {"value": .., "band": [lo, hi]},
                   "cuts_per_min": {"value": .., "band": [..]},
                   "shot_count":   {"value": .., "band": [..]} },
    "motion":    { "kinetic_score": {"value": .., "band": [..]} },
    "color":     { "saturation": {"value": .., "band": [..]},
                   "brightness": {"value": .., "band": [..]},
                   "palette_samples": [ [ {"hex": "#..", "weight": ..} ] ] },
    "audio":     { "integrated_lufs": {..}, "loudness_range": {..},
                   "true_peak_db": {..}, "speech_ratio": {..}, "avg_pause_sec": {..} },
    "structure": { "duration_sec": {..}, "fps": {..} }
  },
  "judged": { "status": "pending|draft|scored", "needs": [..], "frames": [..],
              "assessment": { "visual_style": "..", "typography_character": "..",
                              "motion_feel": "..", "mood": "..",
                              "layout_types": [".."], "summary": ".." } },
  "open_questions": [ {"id": "pace", "sets": "pacing.avg_shot_sec", "plain": ".."} ],
  "ceo_prefs": { },
  "raw": [ <per-video objective measurements> ]
}
```

Each `{value, band}` is an optimization target: `band` is the acceptable range later
tuning aims to land inside.
