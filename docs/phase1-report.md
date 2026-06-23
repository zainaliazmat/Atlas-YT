# Phase 1 — Evaluation Foundation: Build Report

*Lead engineer / orchestrator report. Scope was deliberately bounded to PHASE 1:
establish the standard (a measurable rubric) and prove ONE minimal improvement
loop end-to-end. No coaching split, no research/self-study layer — those are
Phase 2+ and are recommended at the end, in the order the design docs require.*

Date: 2026-06-22 · Branch: `main` · All work additive (new packages
`atlas/eval/` and `atlas/rubric/`; **zero edits to the spine, contracts, gates,
loader, or any sibling engine**).

---

## 1. What was built (at a glance)

| Package | Module | Role |
|---|---|---|
| `atlas/rubric/` | `rubric.json` | **CEO-owned, frozen** standard: 6 weighted global dims, a hard floor `F`, **42 per-property bands**, judged-reference pool, CEO-anchor stub. |
| | `__init__.py` | Deeply-immutable loader. **No write path** (privilege asymmetry, structural). |
| `atlas/eval/` | `types.py` | `Measurement` + `EvalContext` (lazy, tolerant artifact/media access) + ffprobe/ffmpeg helpers. |
| | `analyzers/audio.py` | LUFS, true-peak, ducking depth, VO SNR, SFX-on-#FFD000-beat, render loudness/peak (ffmpeg `ebur128` + soundfile). |
| | `analyzers/video.py` | Motion energy (cv2 frame-diff @4fps), cut rhythm, AV-sync, layout integrity, auto-gate first-pass, runtime. |
| | `analyzers/text.py` | 27 structural props across script / style / storyboard / assets / narration (+ caption-sync). Reuses `source_engine.relevance`. |
| | `judged.py` | **Ensembled** pairwise-vs-reference judge (hook_strength, overall_polish). Variance-tracked, CEO-anchor wired, injectable seam. |
| | `tracking.py` | Append-only, crash-safe `(run_id, change_id, artifact, prop)` store + **noise-floor** function. |
| | `rollup.py` | Comparator gating + compound secondary conditions → banded rows → weighted dimensions + hard floor → scorecard. |
| | `inspector.py` | The read-only orchestrator: run analyzers → scorecard → (optional) persist + track. CLI: `python -m eval.inspector <dir>`. |
| | `validation.py` | Eval-of-the-eval: every band must pass known-good / fail known-bad; reference-fit + CEO-confirm scaffolds. |
| | `diagnose.py` | Credit assignment: a shortfall → ONE owning stage; coordination + decomposition-gap + floor escalation. |
| | `loop.py` | One bounded improvement loop with the **write boundary** enforced in code. |

**Tests:** 81 new tests added (`atlas/tests/test_eval_*.py`), all green; the
pre-existing suite stays green (see §6).

---

## 2. The plan, the subagent decomposition actually used, and the parallelism map

**Method:** read ground truth first (the two design docs + `PROJECT_CONTEXT.md`
+ the real code: `pipeline.py`, `contracts/`, `source_engine.py`, the llm seam,
and the completed gold-fixture project), then build the shared foundation
single-threaded, then fan out independent analyzers to subagents, then integrate
and demonstrate on the main thread.

**Dependency-mapped waves (concurrency cap 5):**

```
Foundation (main thread, sequential — everything depends on it)
  atlas/rubric/  +  atlas/eval/types.py
        │
        ▼
Wave 1 — 5 PARALLEL subagents, each its OWN module + test file (no file collisions)
  A audio.py     B video.py     C text.py     E judged.py     G tracking.py
        │              │             │             │              │
        └──────────────┴─────┬───────┴─────────────┴──────────────┘
                             ▼
Wave 2 (main thread, needs Wave-1 interfaces)
  D rollup.py + inspector.py   →  real end-to-end scorecard
  F validation.py              →  instrument soundness
        │
        ▼
Wave 3 (main thread, capstone)
  H diagnose.py + loop.py      →  inspect→diagnose→propose→re-measure→accept/reject
        │
        ▼
Live-QA (real runs): gold-fixture scorecard · judged noise-floor K=5 · real loop
```

**Subagents actually dispatched:** 5 (Wave 1), each with isolated context, each
owning exactly one analyzer module + its test file, each told to code to the
`analyze(ctx) -> list[Measurement]` seam, run its tests against the gold fixture,
and return only a distilled report (module surface + real measured values + test
counts + risks). The main thread synthesized those returns and built Waves 2–3
itself (they integrate all modules, so parallelism would only create contention).
This kept the main thread free of subagent transcripts.

**Why this split:** the eval spec's natural seams are by *signal modality* (audio
waveform / video pixels / structured JSON / LLM-judged) and by *infrastructure*
(tracking). Those have no shared state, so they parallelize cleanly; the roll-up,
inspector, and loop are inherently integrative and serial.

---

## 3. Notable autonomous decisions

1. **No edits to `pipeline.py` for "Inspector hooks."** The hard constraint
   ("do not modify stage order / gates / spine") outranks a literal reading of
   "hooks fire in the pipeline." The Inspector is a standalone read-only pass
   over a project dir (`python -m eval.inspector <dir>`), proven on real pipeline
   output (the gold fixture *is* real pipeline output) and on a live smoke run
   (§5). This is strictly additive and risk-free to the spine.
2. **No new pip dependencies.** Used only what the venv already has: ffmpeg
   (`ebur128`), `cv2`, `numpy`, `soundfile`, `jsonschema`, `claude_agent_sdk`.
   Avoided `pyloudnorm`/`scipy`/`PIL` (absent) — loudness via ffmpeg `ebur128`,
   image dims via `cv2`. Keeps the shared env untouched for sibling agents.
3. **Privilege asymmetry made structural, not conventional.** The rubric loader
   returns deeply-immutable `MappingProxyType`/tuples and exposes **no**
   save/write/dump. The loop's `apply_soft_change()` *physically refuses* any
   path under `rubric/`, `contracts/`, or the spine files. `can_write_rubric()`
   is a self-check asserting both facts; it is unit-tested.
4. **`overall_polish` is a textual-digest proxy in Phase 1.** A text LLM can't
   watch an mp4, so the holistic judge compares a deterministic digest of the
   finished project (title + per-scene on-screen text/points + palette +
   signature beat) against reference digests. Clearly labelled a proxy; replacing
   it with true video-vs-video comparison is a Phase-2 item.
5. **Bands are placeholders, by design.** Numbers come from
   `rubric-decomposition.md` as starting points; the *methods, ownership,
   comparators, and roll-up structure* are the stable part. Deriving real bands
   from reference videos (Vera) is Phase-1-of-the-path step 1 and is explicitly
   out of this build's scope — the rubric is wired to receive them.
6. **Loop target = `script:info_density` (Marlow), the affordable real target.**
   It's a soft-tier, single-owner failure measurable WITHOUT a render or TTS, so
   the real loop costs one LLM script-generation per iteration. (Narration/visual
   targets would cost a full TTS/render to re-measure.)
7. **Accept-path proven by unit test; reject-path proven live.** On real runs the
   loop *correctly rejected* every proposal (the placeholder band is tight for
   this content) and reverted — the more important safety property. The accept
   branch is deterministically covered by `test_run_loop_accept_with_fake_engine`.
   (No CEO consult was required; none of these decisions touched a hard
   constraint or incurred non-trivial spend.)

---

## 4. The rubric → scorecard model (how measurement attributes to an owner)

- **Analyzers MEASURE, the rubric DECIDES.** Each analyzer returns raw
  `Measurement`s and never reads a threshold; `rollup.gate()` applies the
  CEO-owned band. This is the same separation as the pipeline's "engines emit
  dicts, the spine validates."
- **Comparators:** `range / gte / lte / eq / eq_true / info`, plus a small,
  explicit registry of **compound secondary conditions** taken verbatim from the
  spec (e.g. `layout_variety` also requires no single layout > 60%;
  `transition_character` also requires ≥1 match-cut).
- **Roll-up:** each global dimension scores = fraction of its gated contributors
  that pass; quality_score = weight-normalized sum over present dimensions. The
  **floor `F` is hard** — any hard floor failure ⇒ `BLOCKED_BY_FLOOR` (a quality
  score is still computed for diagnostics but the verdict is the block,
  consistent with "a fact-check `block` can't be averaged away").
- **Decomposition-gap detector:** if every gated local passes but the holistic
  anchor fails, the scorecard flags that the rubric is missing a term — a
  CEO-owned signal, never an auto-fix.

---

## 5. Live-QA evidence (real runs, not inspection)

### 5.1 Real end-to-end scorecard on the gold fixture (objective)

`python -m eval.inspector projects/gpt-4o-...-comparison--…67a3 --no-track`:

```
OVERALL: BLOCKED_BY_FLOOR   quality_score: 0.660
gated 37  passed 25  failed 12  ungated 3  errors 3
floor: FAIL ['assets:clearance_rate']
dimensions:
  G1 Pacing & rhythm    score 0.80  (8/10 gated, w=0.2)
  G2 Editorial quality  score 0.80  (4/5 gated,  w=0.25)
  G3 Visual craft       score 0.75  (6/8 gated,  w=0.2)
  G4 Asset relevance    score 0.67  (2/3 gated,  w=0.15)
  G5 Audio quality      score 0.00  (0/3 gated,  w=0.15)
  G6 AV coherence       score 1.00  (3/3 gated,  w=0.05)
```

This is the instrument working: a real verdict from real artifacts. The floor
block is a **legitimate catch** — 1 of 13 assets is a `placeholder`, so
`clearance_rate` = 0.923 ≠ 1.0. The failing properties are a faithful mix of
*placeholder-band* misses and *real* findings:

| Property | Value | Band | Read |
|---|---|---|---|
| audiomix:integrated_loudness | −21.8 LUFS | −14 ±1 | real: the fixture was mixed quiet |
| audiomix:true_peak | −0.6 dBTP | ≤ −1.0 | real (marginal) |
| audiomix:vo_intelligibility | −1.7 dB | ≥ 15 | fixture's bed is a placeholder ⇒ SNR not representative |
| script:info_density | 7.16 /min | 1.5–4.0 | placeholder band likely too tight |
| storyboard:layout_variety | 0.96 | 0.45–0.85 | placeholder band; 6 distinct layouts is *good*, suggests recalibrating up |
| **assets:clearance_rate** | **0.923** | **=1.0 (hard)** | **real legal-floor catch** |
| **assets:relevance_score** | **0.139** | **≥0.50** | **real — the known Issue-#2 asset weakness** |
| assets:min_resolution | 717 px | ≥720 | real (1px under — a strict floor working) |
| narration:speech_cadence | 186 wpm | 140–165 | real: the TTS is fast |
| narration:scene_timing_fit | 4.4 s | ≤3.0 | real: scene-6 narration overruns its plan |

3 graceful "ungated/error" rows (correct behavior): `ducking_depth` (no VO gaps
in this back-to-back mix), `palette_distance` (info-only, no reference centroid
yet), `caption_sync` (no whisper word-timing present).

### 5.2 Per-analyzer real values (sanity checks all pass)

- **#FFD000 discipline:** `effect_discipline = 1.0` (exactly one), `signature_present
  = #FFD000`, `signature_beat_placement` valid (scene 8, not first/last). ✓
- **Motion energy:** mean |Δluma| = 1.69, variance 93.85 across 272 sampled
  frames @4fps. **Cut rhythm** median 6.06 s, correctly flags scene 6 (12.4 s >
  12 s). **AV-sync** = 1.0 (visual boundaries match narration to ~1e-14 s). ✓
- **Audio:** integrated −21.8 LUFS, true-peak −0.6 dBTP via `ebur128`;
  **sfx_on_beat = 0.0 s** (page-turn at 47.852 s lands exactly on scene-8's
  start). ✓
- **Relevance reuse:** `source_engine.relevance` imported and used (engine
  method, not the fallback). Score 0.139 — low because asset filenames are short
  slugs and the engine deliberately discounts single-token overlaps (Issue-#2
  mitigation); a metadata proxy, flagged for band calibration.

### 5.3 Judged noise floor — K=5 real runs (subscription LLM, no API key)

```
script:hook_strength : mean 1.000  std 0.000   values [1,1,1,1,1]
render:overall_polish: mean 0.560  std 0.233   values [0.4,0.6,0.4,0.4,1.0]
```

**This is the single most important number for the whole self-improvement
program.** `hook_strength` is rock-solid (the gold hook beats the placeholder
pool every time). `overall_polish` has a **high noise floor (σ≈0.23)** — so a
future "improvement" on the judged polish metric must clear ≈2σ ≈ **0.47** to be
distinguishable from LLM jitter. Without this measurement, two LLMs nodding at
each other would look like progress. (Within-run ensemble variance ≈0.24,
consistent with the cross-run spread.)

### 5.4 The minimal improvement loop — real runs

`inspect → diagnose → propose → re-measure (real Marlow engine) → accept/reject`:

```
DIAGNOSE  primary target: script:info_density  (Marlow, value 7.16, band [1.5,4.0])

rule addendum,  1 iter : 7.16 → 7.68  passes=False  → REJECT, soft change reverted
LLM coach,      iter 1 : 7.16 → 8.31  passes=False  → REJECT
LLM coach,      iter 2 : 7.16 → 6.23  passes=False  regressions=['script:words_per_scene'] → REJECT
RUBRIC WRITE BLOCKED: True   (every run)
```

The loop behaved exactly as a safe optimizer must: it **refused every change
that didn't hit the CEO-owned bar**, it **caught a regression** (iter 2 improved
the target but broke `words_per_scene`, so it was rejected), it **reverted** the
soft-tier addendum on reject, and it was **physically unable to write the
rubric**. The accept branch (target lands in band, no regression ⇒ persist the
soft change) is proven deterministically in `test_run_loop_accept_with_fake_engine`.
That the placeholder band resisted three honest attempts is itself a finding:
the band, not the agent, is the likely problem — and the loop is correctly
*forbidden* from loosening it (that's a CEO-owned recalibration).

### 5.5 Live pipeline smoke

The gold fixture is genuine end-to-end pipeline output (all 10 stages done, both
gates approved, real `video.mp4`/`master.wav`). The Inspector runs cleanly over
it without touching stage order or gate behavior, and the real Marlow engine was
re-invoked live (via its injectable `chat_fn`, subscription brain) during the
loop — confirming the new read-only machinery composes with the real engines and
the real LLM path. A fresh full `produce` (render + TTS) was not run end-to-end
to a new mp4 — it is minutes-long and unnecessary to prove the read-only hooks,
since the fixture already provides authentic artifacts at every stage.

---

## 6. Test results

- **Pre-existing suite:** unchanged and green (137 tests; verified with the new
  packages present — no regressions, one pre-existing benign warning in
  `test_async_containment`).
- **New eval tests (81), all green:**
  - audio 5 · video 9 · text 5 · judged 14 · tracking 9 (Wave 1 = 42)
  - rollup 12 · validation 6 · inspector 6 (Wave 2 = 24)
  - diagnose 5 · loop 16 (Wave 3 = 21) — minus overlap, 81 total new.
- **Eval-of-the-eval:** `validate_instrument()` confirms **all 42 bands
  discriminate** known-good from known-bad.
- Run: `cd atlas && ../venv/bin/python -m pytest tests/ -q`. (The audio/video
  analyzer tests decode full media via ffmpeg/cv2, so the suite takes ~4–5 min;
  everything else is sub-second.)

---

## 7. What works · known gaps · risks

**Works**
- Deterministic objective coverage of all 6 dimensions + the hard floor, proven
  on real media. Objective measurement is pure Python + ffmpeg/cv2; the only LLM
  is the ensembled judge.
- A frozen, CEO-owned rubric with a structural no-write guarantee, validated as
  an instrument, with a measured noise floor.
- An auditable tracking store and a bounded, regression-aware, self-reverting
  improvement loop that cannot edit its own success bar.

**Known gaps**
- **Bands are placeholders** — the gold fixture "fails" several that are likely
  mis-set (e.g. `layout_variety 0.96`, `info_density`). Deriving real bands from
  references (Vera) is the immediate next step; the rubric is wired for it.
- **`overall_polish` is a text proxy**, not true video-vs-video; `ducking_depth`
  and `vo_intelligibility` are limited by the fixture's placeholder bed;
  `caption_sync` needs whisper word-timing (absent here).
- **`relevance_score` is a metadata proxy** (filename/shot-token overlap), not
  pixel-level semantic relevance — calibrate its band to the method.
- **CEO-anchor is stubbed** (the labels file is optional/absent); the re-anchor
  math is wired but not implemented.
- The objective suite is slow (full-media decode); fine for an eval cadence, not
  a hot loop.

**Risks (and the mitigations already in place)**
- *Gaming the measure / deleting guardrails* → structural no-write rubric +
  `can_write_rubric()` self-check + the loop's `apply_soft_change` boundary.
- *Two LLMs agreeing* → judged set is tiny, ensembled, variance-tracked, and
  CEO-anchorable; the measured σ≈0.23 on polish makes the noise explicit.
- *Overfitting the rubric* → held-out evaluation and human spot-checks are a
  Phase-2 must (the tracking store + noise floor are the substrate for it).
- *Runaway cost* → the loop is hard-capped (`max_iters`) and the affordable
  target (script) avoids render/TTS spend.

---

## 8. Recommended Phase 2 plan (in the design-doc order) + readiness

The design mandates: **prove one basic loop → split coaching → add research.**

1. **Calibrate the bands from references (do this FIRST).** Run Vera over the
   reference videos to replace the placeholder bands, then re-run
   `validate_instrument()` and `report_reference_fit()` — the references should
   then *pass*. Optimizing against uncalibrated bands is the exact failure the
   docs warn about. *(Readiness: HIGH — the rubric, validation harness, and
   `reference_rubric` contract already exist.)*
2. **Harden the one loop before trusting it more.** Add a held-out project set
   the loop never optimizes against; require an accepted change to beat the
   measured noise floor (use `tracking.noise_floor`); add human spot-checks at
   accept. Demonstrate a *real accept* on a calibrated band. *(Readiness: HIGH —
   loop, tracking, diagnose are in place; needs a second fixture + the held-out
   gate.)*
3. **Split the coaching into two domain coaches** (Editorial/Content over
   pre-production; Production/Craft over production), mirroring the rubric's
   content/craft division. The Diagnostician already attributes to one owner and
   flags multi-stage coordination — the coordination boundary the split needs.
   *(Readiness: MEDIUM — diagnosis is ready; the coaches are new "employees"
   (one registry entry + one adapter each), and the soft-tier write boundary is
   already enforced.)*
4. **Add the research / self-study dimension** — bounded, and every hypothesis
   tested against the rubric before adoption ("research widens what to try; the
   eval prunes it"). *(Readiness: LOW by intent — build only after step 2 proves
   the optimizer, per the docs.)*

**Overall readiness:** the foundation the rest of the program stands on — a
measurable standard, a validated instrument, a noise floor, and a safe loop that
cannot rewrite its own success bar — is **in place and proven on real data**. The
single highest-leverage next action is band calibration from references (step 1);
everything else builds on it.
