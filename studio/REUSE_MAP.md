# REUSE_MAP.md — what studio/ REUSES vs REBUILDS vs RETIRES

The v2 production path (`studio/`) is a clean spine, not a clean slate. Most of
the hard, proven engineering in this repo is **reused by wrapping** — studio
imports the sibling engines through an isolated loader and never forks their
logic. What gets **retired** is the *spec-passing creative middle* (the closed-
vocab contracts and the persona machinery), because that is exactly what
`GOLDEN_REFERENCE.md` identifies as the source of flat output. Retired code
stays in `atlas/` untouched; studio simply doesn't call it.

Legend: **REUSE** = wrap the existing code, don't fork · **REUSE (PATTERN)** =
copy the design, re-target it · **REBUILD** = new code in studio/ · **RETIRE** =
leave in atlas/, not on the production path.

---

## REUSE — wrap, don't fork

### 1. Sage research + factcheck engine → REUSE
- **Source:** `topic-researcher/researcher.py` (`run()` = decompose→gather→classify→route→assemble, `find_thematic_anchor()`), `topic-researcher/search.py` (pluggable web/wiki/news backend), `topic-researcher/factcheck.py` (pass-2: `resolve_source_ref()`, `iter_claims()`, VERIFIED/FLAGGED/UNVERIFIABLE).
- **Studio seam:** `studio.pipeline` `research` + `factcheck` stages call these via the same isolated-engine loader pattern atlas uses.
- **Rule:** wrap, don't fork. Studio owns the contract stamp at the boundary; the engine never imports studio.

### 2. The "block can't be approved away" gate → REUSE
- **Source:** `atlas/pipeline.py::_factcheck_gate` — on verdict `block`, sets `status = blocked_at_factcheck` and routes back to the writer; approving on resume only **resets the stage to pending so Sage re-runs** (a fresh `block` still blocks). The block is structurally un-dismissable.
- **Studio seam:** `studio.pipeline._factcheck_gate` reproduces these *semantics* by wrapping the atlas gate behavior. This is the one gate that approval cannot clear.
- **Contrast:** the `final` gate is a normal human pause and *is* clearable by approval (`studio.pipeline._final_gate`).

### 3. HyperFrames CLI wrappers → REUSE
- **Source:** `composition-engineer/hf_tools.py` — `run_lint()`, `run_validate()`, `run_inspect()`, `run_gate()` (lint→validate→inspect, short-circuit + transient-Chrome retry via `_is_transient`/`_retrying`), `run_render()`, `assemble_final()` (+ shader-transition splice / graceful concat fallback). All error-contained, timeout-bounded, never raise; parse first JSON object out of stdout.
- **Studio seam:** `studio.hf` is a thin re-export/adapter over these.
- **One deliberate change:** version pin moves **0.6.115 → 0.7.10**, centralized in `studio.HYPERFRAMES_VERSION` / `studio.hf`. Bumping is a one-line change; both compose and audio wrappers agree.

### 4. Kokoro VO flow + HF audio wrappers → REUSE
- **Source:** `audio-designer/hf_audio.py` — `tts()` (Kokoro 24kHz mono), `concat_wavs()` (lossless concat-demuxer), `transcribe()` (optional whisper.cpp word-level), `build_mix_recipe()` (PURE filtergraph: VO authoritative, bed hard-ducked via sidechaincompress, one SFX accent, final loudnorm **−14 LUFS**) + `run_mix()`. And `audio-designer/audio_engine.py` — `record_narration()` (concurrent per-scene synthesis + transcript) and `mix_audio()` (license-cleared bed sourcing, signature SFX, clearance invariant).
- **Studio seam:** `studio.vo.record_vo` / `studio.vo.mix` wrap these.
- **License policy reused too:** `audio_engine.normalize_license/classify/build_attribution` become the shared clearance rules for `studio.library` (visual + audio assets cleared identically).

### 5. Video analyzers → REUSE, but now IN-LOOP per video
- **Source:** `atlas/eval/analyzers/video.py::analyze()` — 6 measurers: `_measure_motion_energy` (frame-diff delta-luma @~4fps, downscaled), `_measure_cut_rhythm` (median scene duration, flags outside 1.5–12s), `_measure_av_sync` (fraction of scene boundaries within 0.25s of narration), `_measure_layout_integrity`, `_measure_auto_gate_first_pass`, `_measure_final_runtime`.
- **Current execution model:** **post-hoc only** — runs after the full render, reads completed artifacts, scores.
- **Studio change:** `studio.review.run_analyzers` runs the SAME analyzers **in-loop on the DRAFT render**, so motion energy / cut rhythm / AV-sync / layout integrity become *gates that drive revision*, not after-the-fact scores. This is the GOLDEN_REFERENCE.md anti-pattern fix #3 ("looking at frames") wired into the loop.

### 6. Pairwise-vs-reference LLM judging → REUSE inside the vision review
- **Source:** `atlas/eval/judged.py::judge_pairwise()` — ensembled (DEFAULT_N=5), seeded A/B-order-randomized, abstention-aware, never raises; `analyze()` judges `hook_strength` + `overall_polish` via an injectable `chat_fn`; `_build_polish_digest()` builds the candidate text.
- **Studio seam:** `studio.review.judge_against_references` calls `judge_pairwise` **inside the review loop** to compare each draft against the golden reference(s), alongside a sampled-frame vision pass. Same anti-order-bias machinery; now in-loop and reference-anchored.

### 7. Registry/adapter PATTERN → REUSE the pattern, for PACKS and CHANNELS
- **Source:** `atlas/registry.py` (`AgentEntry`, `JobSpec`, `REGISTRY`, `build_adapters()`, `get_entry()`) + `atlas/adapters/base.py` (`Adapter` ABC: lazy `engine()` via `loader.load_engine` with isolated sys.path/sys.modules, `run_job()`, `ask()`).
- **Studio reuse:** the *declarative-entry + lazy-isolated-loader* design is re-applied:
  - **Packs** — `studio.packs` `PackEntry`/registry (id, display, dir, loader) ↔ `DesignPack` (the analog of an adapter-loaded engine).
  - **Channels** — `studio.config` channel registry (per-channel runtime band, default pack, voice, brand).
  - The isolated-loader trick is also how `studio.vo` / research stages pull sibling engines without import collisions.
- **Not reused:** the *agent* registry contents (Scout/Sage/Marlow/Iris/… as personas with `ask_<name>` tools) — studio has no persona surface.

---

## REBUILD — new in studio/

- **`studio.compose` (Phase 3) — the Composer.** Authors ONE deterministic, paused-GSAP, seekable `index.html` against a Design Pack + VO-lock re-timer. This is the genuinely new core and the direct replacement for "Mason renders a closed enum from a passed storyboard spec."
- **`studio.packs` (Phase 1) — Design Packs.** Curated look+type+texture+motion+audio systems the Composer authors against (replaces generated style/storyboard/mood-board specs).
- **`studio.library` (Phase 2) — Asset Library + manifest.** Local-first, license-cleared, procedural-preferred asset resolution (narrows the live web-sourcing approach).
- **`studio.vo` re-timer bridge (Phase 4) — `retimer_windows()`.** Converts real VO timing into the `OS/OD/NS/ND` arrays the Composer's re-timer needs (the VO-lock mechanism from GOLDEN_REFERENCE.md §2). The TTS/mix underneath is REUSE; the re-timer wiring is new.
- **`studio.review` loop (Phase 5).** The render→look→revise orchestration that turns the reused analyzers + judge into an in-loop gate with concrete revise instructions.
- **`studio.pipeline` spine + `studio.run` CLI + `studio.config`.** Lean orchestration over the leaner stage set.

---

## RETIRE — leave in atlas/, off the production path

These remain in `atlas/` (untouched) but are **not called** by studio. They are
the spec-passing creative middle and persona machinery that
`GOLDEN_REFERENCE.md`'s "Anti-Patterns We Are Fixing" targets:

- **The closed-vocab intermediate contracts** and their stages:
  `treatment` (Iris), `narrative_intent` (Iris), `motion_mood_board` (Iris),
  `style` (Iris), `storyboard` (Iris) — `atlas/pipeline.py` STAGES + `atlas/contracts/`.
  → Replaced by: the Composer authoring directly against a Design Pack.
- **Mason-renders-from-an-enum.** The storyboard→effect-enum→render handoff in the
  composition stage. → Replaced by: bespoke per-scene GSAP authored to content
  (GOLDEN_REFERENCE.md §6), on one shared timeline (anti-pattern #2).
- **The persona surface.** The agent roundtable and the Quill/Flux coaches
  (`scriptwriter/roundtable.py`, `editorial-coach/`, `production-coach/`, the
  `ask_<name>` persona tools). → Replaced by: the in-loop vision review
  (analyzers + reference judge), which improves the artifact by *looking at it*
  rather than by relaying critiques between personas.

**Why retire rather than wrap:** these stages communicate by serializing a spec
and handing it to the next agent, so no stage ever sees the others' realities in
one artifact — the exact failure mode in GOLDEN_REFERENCE.md anti-pattern #2.
studio collapses that middle into one Composer + one timeline + one review loop.

---

## Import-cleanliness rule (applies to every studio module)

`python -c "import studio"` must stay clean and side-effect free. Therefore:
sibling engines (topic-researcher, audio-designer, composition-engineer, atlas
eval) are imported **lazily inside functions**, never at module scope; `studio`
itself imports only stdlib at import time. The reuse seams above are wired at
call time through the isolated loader, exactly as atlas's adapters do.
