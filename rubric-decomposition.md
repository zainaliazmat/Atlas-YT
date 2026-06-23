# Per-Artifact Rubric Decomposition — YT-AGENTS Self-Improvement

> **What this is.** The single "is this video good?" rubric, broken into a *local rubric per
> artifact* so that (a) every quality shortfall attributes to exactly one owning stage — the
> credit-assignment problem — (b) most measurement is **deterministic code in the spine**, not
> LLM judgment, and (c) problems can be caught at *plan* stages before you spend a render.
>
> **What this is not.** The numbers below are **PLACEHOLDER bands**. Deriving the real bands from
> reference videos is step 1 of your path — this spec defines *what* to measure and *where*; the
> references set the *values*. **Bands and weights are CEO-owned.** The improver reads this file;
> it may never write it. (Same asymmetry as your guardrails — this is the success criterion.)

---

## 1. The composition model

Three layers, top to bottom:

1. **Global dimensions** — the handful of things the CEO actually cares about ("does it feel
   master-class").
2. **Per-artifact local properties** — where each global dimension is actually *measured and
   attributed*. This is the working layer.
3. **One holistic judged check** — measured only on the finished `video.mp4`, because some things
   ("overall polish") only exist on the whole.

Two distinctions run through every property:

- **Objective vs Judged.**
  - *Objective* = a deterministic analyzer (ffmpeg, frame-diff, token-overlap) with a hard numeric
    band. Ungameable, cheap, test-retest stable. **Lives in the deterministic spine.**
  - *Judged* = pairwise-vs-reference, scored by an **ensembled** LLM (e.g. N=5, track variance),
    **periodically re-anchored to CEO labels**. Noisy and expensive. **Lives in the LLM plane.**
  - **Rule:** maximize objective coverage. Use a judged property *only* where no deterministic
    proxy exists. (This is the fix for "two LLMs agreeing with each other" — the judged set is
    small, ensembled, and CEO-calibrated, not the backbone.)

- **Plan-proxy vs Realized-authoritative.** Some properties are *planned* in an early artifact
  (cheap, predictive) and *realized* in a later one (authoritative). The realized measure **decides**;
  the plan proxy lets you **fail fast** before the render spend. (Example: motion energy is
  *intended* by `style_guide` + `storyboard` and *realized* on rendered frames.)

**Roll-up.** Each global dimension = a weighted combination of its contributing local properties,
with the holistic judged score as a **sanity anchor**. If every local passes but the holistic
fails, that is not a contradiction — it means your decomposition is **missing a property**. Treat
that as a diagnostic that the rubric itself needs a new term.

---

## 2. Global dimensions (CEO-owned weights)

| # | Dimension | Captures | Primary contributing stages | Weight* |
|---|-----------|----------|------------------------------|---------|
| G1 | **Pacing & rhythm** | cut rhythm, scene length, speech cadence, pauses | Marlow, Cadence, Mason | 0.20 |
| G2 | **Editorial quality** | hook, one-point clarity, arc, density, CTA | Marlow | 0.25 |
| G3 | **Visual craft** | palette, layout variety, effect discipline, type | Iris, Mason | 0.20 |
| G4 | **Asset relevance** | visuals match narration; quality; placeholder rate | Magpie, Iris | 0.15 |
| G5 | **Audio quality** | loudness, VO/music balance, intelligibility, SFX | Cadence | 0.15 |
| G6 | **AV coherence** | visual boundaries align to narration; transition feel | Mason, Cadence | 0.05 |
| **F** | **Technical integrity** | renders clean, no overflow, deterministic, caption sync | Mason | **gate** |

\* Weights are an example starting point and belong to the CEO. **F is a pass/fail floor, not a
weighted term** — a render that fails it has no quality score (consistent with how a fact-check
`block` can't be averaged away).

---

## 3. Per-artifact local rubrics

### 3.1 `script.json` — Marlow (stage: script)

| Property | Type | How measured | Target / band (PLACEHOLDER) | Rolls up to |
|---|---|---|---|---|
| `hook_strength` | **Judged** | pairwise vs pool of reference hooks, ensembled N=5; CEO-anchored each cycle | preference rate ≥ 0.55 | G2 |
| `scene_count` | Objective | `total_scenes` | 8–14 | G1 |
| `runtime_fit` | Objective | `est_runtime_sec` | 60–90 s | G1 |
| `words_per_scene` | Objective | word count per `narration`; mean + variance | mean 18–32; flag any scene >45 or <8 | G1 |
| `one_point_adherence` | Objective + Judged | exactly one `point` per scene (struct) + narration stays on point (judged) | struct: 100%; judged ≥ 0.8 | G2 |
| `claim_support_ratio` | Objective | fraction of `claims[]` with a `source_ref` | ≥ 0.9 | G2 / F |
| `info_density` | Objective + Judged | claims-per-minute (obj) + "new-concept rate vs ref" (judged) | obj: 1.5–4.0/min | G2 |
| `narrative_arc` | Objective + Judged | `hook` + `cta` present (obj) + arc lands vs ref (judged) | obj: both present | G2 |
| `cta_quality` | Objective + Judged | `cta` present (obj) + judged strength | obj: present | G2 |
| `on_screen_text_density` | Objective | chars per `on_screen_text` | ≤ 45 chars; flag longer | G3 |

### 3.2 `style_guide.json` — Iris (stage: style)  *(plan-stage proxies)*

| Property | Type | How measured | Target / band | Rolls up to |
|---|---|---|---|---|
| `palette_distance` | Objective | ΔE between declared palette and reference-palette centroid | within reference band | G3 (proxy) |
| `signature_present` | Objective | `signature_highlight == #FFD000` present | true (hard) | G3 |
| `type_in_system` | Objective | declared fonts ∈ allowed set | 100% | G3 |
| `motion_budget_sane` | Objective | declared budget within bounds | in range | G1/G3 (proxy) |

> These are cheap **predictors**; the authoritative visual measures are on the render (§3.6).

### 3.3 `storyboard.json` — Iris (stage: storyboard)

| Property | Type | How measured | Target / band | Rolls up to |
|---|---|---|---|---|
| `layout_variety` | Objective | normalized Shannon entropy of layout distribution | 0.45–0.85 **and** no single layout >60% of scenes | G3 |
| `effect_discipline` | Objective | count of `highlighter-FFD000` across video; effects-per-scene vs budget | **exactly 1** FFD000 (hard); per-scene ≤ budget | G3 |
| `transition_character` | Objective | distribution of `transition` kinds | hard `cut` ≤ 85%; `match-cut` ≥ 1 | G1/G6 |
| `shot_specificity` | Objective | fraction of `shots[]` with a concrete `asset_ref`/`content` | ≥ 0.8 | G4 (proxy) |
| `signature_beat_placement` | Objective | `signature_beat` present and not scene 1 or last | true | G3 |

### 3.4 `asset_manifest.json` — Magpie (stage: assets)

| Property | Type | How measured | Target / band | Rolls up to |
|---|---|---|---|---|
| `relevance_score` | Objective | **your existing** normalized subject-token fraction | ≥ `RELEVANCE_WEAK` (0.50); < `RELEVANCE_FLOOR` (0.20) → placeholder | G4 |
| `placeholder_rate` | Objective | fraction of assets with `status: placeholder` | ≤ 0.15 | G4/G3 |
| `clearance_rate` | Objective | fraction `cleared` of assets that should clear | 1.0 (legal floor) | F |
| `min_resolution` | Objective | min pixel dims of downloaded raster assets | ≥ 1280×720 | G3 |

> Anchor `relevance_score` and the two thresholds on the machinery you already shipped in
> `source_engine.py` — this row is mostly *surfacing existing signal* into the rubric.

### 3.5 `narration.transcript.json` + `narration.wav` — Cadence (stage: narration)

| Property | Type | How measured | Target / band | Rolls up to |
|---|---|---|---|---|
| `speech_cadence` | Objective | words ÷ `total_duration_sec` (overall + per-scene) | 140–165 wpm; per-scene flag >185 or <110 | G1 |
| `pause_structure` | Objective | gaps between `segments[]`; mean inter-scene pause | 0.15–1.2 s; flag outside | G1 |
| `scene_timing_fit` | Objective | per-scene narration duration vs storyboard intent | |Δ| within tolerance | G1/G6 |
| `total_duration_fit` | Objective | `total_duration_sec` vs target | 60–90 s | G1 |

### 3.6 `composition_manifest.json` + scene HTML — Mason (stage: compose)  *(realized-authoritative visual)*

| Property | Type | How measured | Target / band | Rolls up to |
|---|---|---|---|---|
| `auto_gate_first_pass` | Objective | scenes passing self-scan+lint+validate+inspect on first try ÷ total | required 1.0 to ship; track first-pass rate as health | F |
| `motion_energy` | Objective | mean |Δluma| between frames sampled @4fps; mean **and** variance | mean + modulation within reference band | G3/G1 |
| `cut_rhythm` | Objective | shot-duration distribution from manifest timings; median + IQR | flag shots <1.5 s or >12 s; median in ref band | G1 |
| `layout_integrity` | Objective | overflow/clipping from `inspect` | zero overflow | F |
| `av_sync` | Objective | per-scene |visual boundary − narration segment boundary| | ≤ 0.25 s for ≥ 90% of scenes | G6 |

### 3.7 `master.wav` + `audio_manifest.json` — Cadence (stage: audiomix)

| Property | Type | How measured | Target / band | Rolls up to |
|---|---|---|---|---|
| `integrated_loudness` | Objective | ffmpeg `ebur128` integrated LUFS on `master_uri` | −14 LUFS ± 1.0 | G5 |
| `true_peak` | Objective | ffmpeg true-peak | ≤ −1.0 dBTP | G5/F |
| `ducking_depth` | Objective | bed-level reduction during VO (master-bridge sidechain) | 12–18 dB | G5 |
| `vo_intelligibility` | Objective | VO-to-bed SNR during VO segments | ≥ 15 dB | G5 |
| `sfx_on_beat` | Objective | signature SFX onset vs the cut into the #FFD000 scene | within ±0.1 s | G6/G3 |

### 3.8 `video.mp4` — holistic (stage: render)

| Property | Type | How measured | Target / band | Rolls up to |
|---|---|---|---|---|
| `overall_polish` | **Judged** | pairwise vs reference *videos*, ensembled; **CEO-anchored every cycle** | preference rate ≥ 0.5 | sanity anchor |
| `final_loudness` / `final_peak` / `final_runtime` | Objective | recompute end-to-end as backstops | match §3.7 / G1 bands | F |
| `caption_sync` | Objective | if whisper word-timing present: caption vs audio drift | ≤ 0.2 s | F |

### 3.9 `factcheck_report.json` — *not a quality term*

Correctness is a **gate**, not a weighted dimension. A `block` verdict routes upstream and re-blocks
until fixed — it must never be averaged into a quality score or "approved away." Keep it exactly
where it is (`_factcheck_gate`); the rubric only scores the *editorial* qualities of `script.json`
(§3.1), never the factual verdict.

---

## 4. How the Diagnostician uses this (credit assignment)

The decomposition exists so a global shortfall points to **one owner**:

- **Clean case.** G1 (Pacing) is below band → inspect its contributors → `speech_cadence` is out of
  band but `scene_count`, `cut_rhythm`, `words_per_scene` are all fine → owner = **Cadence**. The
  Editorial coach is *not* involved; the fix targets Cadence's prompt/persona. One owner, one fix.

- **Multi-stage case.** G1 low *and* both `words_per_scene` (Marlow) **and** `speech_cadence`
  (Cadence) are out of band → this is a genuinely shared shortfall. Flag it for **coordination**
  rather than letting two coaches optimize against each other (the content/craft boundary rule). Fix
  one, re-measure, then the other — never both blind in the same iteration.

- **Decomposition-gap case.** Every local passes but `overall_polish` (§3.8) fails → the rubric is
  missing a term. Escalate to the **CEO** to name the missing property, then add a row here. (This
  is the rubric improving — a CEO-owned change, never the improver's.)

---

## 5. Where each piece lives (two-plane placement)

- **Objective analyzers → the deterministic spine.** Add a read-only `Inspector` computation set
  that runs *alongside* contract validation — same plane as your gates, additive, no LLM. Most of
  this spec is code.
- **Judged assessment + fix proposal → the LLM plane.** Only `hook_strength`, the judged halves of
  density/arc/one-point, and `overall_polish` touch an LLM — all ensembled and CEO-anchored.
- **The rubric file → a frozen, CEO-owned contract.** Mirror your `contracts/` pattern with a new
  `rubric/` of versioned, CEO-owned targets (`global_weights`, per-artifact `bands`, the judged-pool
  references). The improver imports it; it has **no write path** to it. This is the privilege
  asymmetry made structural.

---

## 6. Boundaries (kept honest)

- **Bands are placeholders** — derive them from references (step 1). The *methods* above are the
  durable part; the *numbers* are tunable.
- **Plan proxies predict, realized measures decide** — never gate a render on a plan-stage proxy
  alone; use it only to fail fast.
- **Out of reach (parked, matching your design doc):** true semantic "does this specific visual
  depict this specific sentence" beyond boundary alignment; asset provenance/recipe. `av_sync`
  (§3.6) is the cheap structural stand-in, not the semantic check.
- **This decomposition is itself testable** — which is the next piece.

---

## 7. How the other two pieces build on this

- **Rubric-validation spec (the eval-of-the-eval):** for every band above, assert
  *references-pass / known-bad-fails / CEO-confirms-a-sample*, and re-run it whenever a band changes.
  Without this, you optimize toward an unvalidated instrument.
- **Eval-tracking schema:** one row per `(run_id, artifact, property)` → `measured_value`, `band`,
  `pass/fail`, `objective|judged`, plus the `change_id` that produced the run. That table is what
  makes improvement **auditable** and lets you **measure the noise floor** (run the held-out set K
  times, read the variance) before trusting any delta.

---

*Numbers marked PLACEHOLDER are to be replaced by reference-derived bands in step 1. Methods,
ownership, types, and roll-up structure are the parts intended to be stable.*
