# Phase 2 — Step 1: Band-Calibration Proposal (reference-derived)

> **Status: PROPOSAL for CEO approval. Nothing applied.** This is produced by the read-only proposer `atlas/eval/calibrate.py`. Applying any band is a CEO-owned human edit to `atlas/rubric/rubric.json` (flip `placeholder:false` on the calibrated band). The improver has no write path to the rubric — calibrate writes only `atlas/eval/rubric.proposal.json` + this report.

- Proposal version: `phase2-step1/1.0`
- Generated against rubric: `0.1.0-phase1-placeholder`
- References measured: **8** (in `/home/zain-ali/Documents/YT-AGENTS/ReferanceVideos`)

## Method (apples-to-apples)

Every media band below was produced by running the **existing eval analyzers** (`eval.analyzers.audio` + `video`) directly on each reference mp4 — so the proposed values are in the **same units as the scoring instrument** (ebur128 LUFS/dBTP, @4fps |Δluma|, ffprobe seconds). Vera's `reference_rubric` is shown only as a cross-check (she uses `loudnorm` + scene-threshold cut detection — different units).

## The reference set & the two target formats

You produce **two** formats: ~60–90s (current testing) and ~5–8 min (future). Quality properties that are *rates/levels* (loudness, peak, motion energy) are **format-independent** and are learned from the whole set. Length-dependent bands (runtime, total duration, scene count) depend on the target format, so they are **not** collapsed into one band — a short and a long profile are proposed instead.

| Reference | Duration (s) | Profile |
|---|--:|---|
| 61383987.mp4 | 57.0 | short (≤120s) |
| YTDown_YouTube_Introducing-Google-Spark_Media_9Mi5vpAFgu0_00 | 347.1 | long (120–480s) |
| DeepSeek Just Solved AI's Billion Dollar Problem [mG4SmhWyeF | 349.6 | long (120–480s) |
| YTDown_YouTube_Best-Pomodoro-Timer-Apps-EVER_Media_Hz6d7du0d | 583.4 | over-long (>480s) — style ref, length out of target |
| YTDown_YouTube_Nvidias-New-Mini-Datacenter-Pays-You-Eve_Medi | 645.2 | over-long (>480s) — style ref, length out of target |
| YTDown_YouTube_AI-Was-Supposed-to-Replace-Workers-It-s-_Medi | 1125.1 | over-long (>480s) — style ref, length out of target |
| YTDown_YouTube_Google-Flow-Tutorial-How-To-Use-Google-F_Medi | 1406.2 | over-long (>480s) — style ref, length out of target |
| YTDown_YouTube_New-robot-waifus-GLM-5-2-craze-AI-spas-n_Medi | 2114.3 | over-long (>480s) — style ref, length out of target |

## Media bands — what the references actually say

Headline finding: this reference set does **not** yield tight, directly-adoptable media bands. Loudness/peak are **delivery standards** the noisy raw downloads can't improve on (keep them); motion is a **different design space** than our restrained motion-graphics (a CEO call, not an obvious win). The high-leverage calibration is the structural/editorial bands below, which need the CEO interview.

### Delivery standards (loudness, true-peak) → KEEP

| Band | Current (keep) | Per-reference values | Finding |
|---|---|---|---|
| `render:final_loudness` | min=-15.0 max=-13.0 | -9.8, -17.4, -18.1, -24.3, -24.8, -18.5, -17.1, -17.2 | DELIVERY STANDARD (-14 LUFS broadcast target). References span -24.8…-9.8 LUFS (spread 15.0 > 4.0) — raw downloads carry each creator's upload mastering, so there is NO shared loudness DNA to learn. KEEP the standard. Our own output: [-22.0, -21.8, -21.7, -21.9]. |
| `audiomix:integrated_loudness` | min=-15.0 max=-13.0 | -9.8, -17.4, -18.1, -24.3, -24.8, -18.5, -17.1, -17.2 | DELIVERY STANDARD (-14 LUFS broadcast target). References span -24.8…-9.8 LUFS (spread 15.0 > 4.0) — raw downloads carry each creator's upload mastering, so there is NO shared loudness DNA to learn. KEEP the standard. Our own output: [-22.0, -21.8, -21.7, -21.9]. |
| `render:final_peak` | max=-1.0 | 0.8, -5.0, 0.2, -1.2, -4.5, 0.1, 0.2, 0.3 | DELIVERY STANDARD (no-clip ceiling ≤ -1.0 dBTP). 5/8 references actually CLIP (true-peak > -1.0) — raw-upload mastering artifacts, not a target. A ceiling may never be loosened past the invariant → KEEP current. Our own output: [0.3, -0.5, -2.6, -0.2]. |
| `audiomix:true_peak` | max=-1.0 | 0.8, -5.0, 0.2, -1.2, -4.5, 0.1, 0.2, 0.3 | DELIVERY STANDARD (no-clip ceiling ≤ -1.0 dBTP). 5/8 references actually CLIP (true-peak > -1.0) — raw-upload mastering artifacts, not a target. A ceiling may never be loosened past the invariant → KEEP current. Our own output: [0.3, -0.5, -2.6, -0.2]. |

> **Keep the standard — but note OUR OWN output misses it too.** Our renders measure [-22.0, -21.8, -21.7, -21.9] LUFS (target −14±1): we mix **~7–8 LUFS too quiet**, and 3 of our renders CLIP (true-peak [0.3, -0.5, -0.2] > −1.0 dBTP). That is a concrete, objective target for the Step-2 improvement loop — independent of the noisy references.

### Aesthetic rates (motion) → PROPOSED, but cross-checked vs our own output

| Band | Conf | Current | Proposed | n | Per-reference values |
|---|---|---|---|--:|---|
| `compose:motion_energy` | low | min=1.5 max=40.0 | min=3.328 max=14.689 value=8.191 | 7 | 10.008, 4.275, 9.38, 4.456, 5.695, 13.742, 9.78 |

- **`compose:motion_energy`** — 7/8 references @4fps |Δluma| in instrument units (1 undecodable, e.g. AV1). CAVEAT: references are stock-footage / screen-recording / long-form — a HIGHER-motion design space than our restrained motion-graphics. Our own output motion: [0.928, 1.692, 1.163, 1.077]; 4/4 of our renders fall OUTSIDE the proposed band [3.33, 14.69] (ours: [0.928, 1.692, 1.163, 1.077]) — adopting it would force us toward reference-level motion (a CEO aesthetic decision, not an obvious win).

**Our own output baseline** (same analyzers, instrument units):

| Project | motion_energy | final_loudness | final_peak | runtime (s) |
|---|--:|--:|--:|--:|
| coffee-vs-tea-which-actually-gives-you-better- | 0.928 | -22.0 | 0.3 | 98.495 |
| gpt-4o-vs-claude-vs-gemini-vs-deepseek-compari | 1.692 | -21.8 | -0.5 | 72.533 |
| how-noise-cancelling-headphones-actually-work- | 1.163 | -21.7 | -2.6 | 73.066 |
| the-first-job-ai-will-destroy-jensen-huang-pre | 1.077 | -21.9 | -0.2 | 90.667 |

## Length-dependent bands — short/long profiles (NOT collapsed)

- **`render:final_runtime`** — short `[60, 90]` · long `[300, 480]`. keep SHORT band active for current testing; add LONG profile when 5–8 min testing begins. _Length is a target-format choice, not a reference artifact. References split: 1 short(≤120s), 2 long(120–480s), 5 over-long(>480s, style refs whose LENGTH is out of target). Do not learn a single runtime band from a mixed-length set._
- **`script:runtime_fit`** — short `[60, 90]` · long `[300, 480]`. keep SHORT band active for current testing; add LONG profile when 5–8 min testing begins. _Length is a target-format choice, not a reference artifact. References split: 1 short(≤120s), 2 long(120–480s), 5 over-long(>480s, style refs whose LENGTH is out of target). Do not learn a single runtime band from a mixed-length set._
- **`narration:total_duration_fit`** — short `[60, 90]` · long `[300, 480]`. keep SHORT band active for current testing; add LONG profile when 5–8 min testing begins. _Length is a target-format choice, not a reference artifact. References split: 1 short(≤120s), 2 long(120–480s), 5 over-long(>480s, style refs whose LENGTH is out of target). Do not learn a single runtime band from a mixed-length set._

## NOT calibrated from references (needs the CEO visual interview)

You cannot read a script, storyboard, or asset manifest back out of a finished mp4, so these bands are **not** proposed from references. They need the CEO visual interview + the completed-project distribution as a prior (the cross-cutting workstream in the Phase-2 plan §4).

```
assets:min_resolution
assets:placeholder_rate
audiomix:ducking_depth
audiomix:sfx_on_beat
audiomix:vo_intelligibility
compose:cut_rhythm
narration:pause_structure
narration:scene_timing_fit
narration:speech_cadence
narration:total_duration_fit
script:claim_support_ratio
script:cta_quality
script:hook_strength
script:info_density
script:narrative_arc
script:on_screen_text_density
script:one_point_adherence
script:runtime_fit
script:scene_count
script:words_per_scene
storyboard:layout_variety
storyboard:shot_specificity
storyboard:signature_beat_placement
storyboard:transition_character
style:motion_budget_sane
style:palette_distance
style:type_in_system
```

## Vera cross-check

- Vera uses ffmpeg loudnorm + scene-threshold cut detection (DIFFERENT units than the eval instrument) — cross-check only.
- avg shot sec (Vera): `{'value': 4.39, 'band': [3.731, 5.048]}`
- integrated LUFS via loudnorm (Vera): `{'value': -9.94, 'band': [-11.431, -8.449]}` — compare to the ebur128 `final_loudness` proposed above (different filters).
- saturation `{'value': 0.171, 'band': [0.145, 0.197]}` · brightness `{'value': 0.401, 'band': [0.341, 0.461]}` (palette centroid available for a future `style:palette_distance` band).

## Re-validation

**`validate_instrument()`** on the current rubric: see the run output — all 42 bands must still discriminate known-good from known-bad (the proposal changes nothing until a human applies it).

**`report_reference_fit()`** over the references (current bands): the references miss several placeholder media bands — that miss is the calibration signal. Current placeholder-band failures across references:

- `render:final_loudness` — fails on 8/8 references (current placeholder band)
- `render:final_runtime` — fails on 8/8 references (current placeholder band)
- `audiomix:integrated_loudness` — fails on 8/8 references (current placeholder band)
- `render:final_peak` — fails on 5/8 references (current placeholder band)
- `audiomix:true_peak` — fails on 5/8 references (current placeholder band)

Sanity check — the proposed bands admit the references they were derived from:

- `compose:motion_energy` — would pass 7/7 references under the proposed band

## What a human does next

1. Review the proposed media bands + the long-format runtime profile.
2. Apply approved bands by editing `atlas/rubric/rubric.json` (set the new min/max, flip `placeholder:false`).
3. Re-run `validate_instrument()` (must stay all-pass) and `report_reference_fit()` (references should now pass the calibrated media bands).
4. Begin the CEO visual interview for the structural/editorial bands above.
