# Use HyperFrames at 100% — master adoption plan (production-grade video)

**Date:** 2026-06-25 · **Status:** Plan (research done, build later) · **Owner:** CEO + Atlas
**Supersedes/reshapes:** the motion-stack spec (`2026-06-23-motion-stack-d3-gsap-lottie.md`) and the
diagram-generator spec (`2026-06-24-diagram-generator.md`) — both are substantially reducible (see §6).

> Based on a deep read of the vendored HyperFrames skill suite in `.agents/skills/` (9.7M: engine,
> CLI, registry, animation, creative, motion-graphics, media, remotion-migration) by 5 parallel
> researchers, cross-checked against Mason (`composition_engine.py` + `hf_tools.py`). Memory:
> `hyperframes-full-capability`. Nothing here is built yet.

---

## 1. The finding
We use HyperFrames as **a headless-Chrome HTML→MP4 renderer + a lint/validate/inspect gate** — almost
nothing else. HyperFrames is actually a full production toolkit we are ~90% ignoring:

- **A registry of ~97 blocks + 6 components** (`npx hyperframes add <name>`): `data-chart` (animated
  bar+line), `flowchart`/`flowchart-vertical`, maps (`us-map`/`world-map`/`*-bubble/hex/flow`),
  18 `caption-*` skins, 9 code-animation blocks, social overlays (`yt-lower-third`, `x-post`…),
  `logo-outro`, `apple-money-count`/`stat-motion`, **14 WebGL shader transitions**, VFX/Liquid-Glass.
- **A 7-runtime animation system** (GSAP default + **native Lottie**, Three.js, Anime, CSS, WAAPI,
  TypeGPU) with a documented library of atomic motion **rules** + multi-phase **blueprints**.
- **A motion-graphics IR pipeline** (`shot-plan.json` → director→builder→finalize) that already
  produces charts, conceptual diagrams (`flowchart`), maps, kinetic type, stat callouts, logo reveals,
  lower-thirds, and `asset-fusion` (a real photo's geometry becomes the chart).
- **A complete media stack:** `hyperframes tts` (HeyGen via our key, **word-level timestamps**, +
  ElevenLabs + local Kokoro), `transcribe` (Whisper), `bgm` (Google Lyria / local MusicGen),
  deterministic captions, `remove-background` (transparent webm).
- **Parametrized compositions** (`data-composition-variables` + `--variables`) — the natural backbone
  for an automated data→render pipeline — and `render --docker` for byte-identical reproducible output.

**The meta-finding:** both roadmap specs spend their "VERIFY FIRST" budget asking whether HyperFrames
can do things it **demonstrably already does** (a Lottie player → yes, native; GSAP depth → a whole
rules/blueprints library; data charts → the `data-chart` block). We have been reinventing
`hyperframes-animation`, `hyperframes-registry`, and `hyperframes-media` inside Mason.

## 2. The strategic principle (load-bearing — decide this first)
**Mason STAYS the deterministic, LLM-free, byte-stable compositor.** That is our edge and it is
*stricter* than HyperFrames' per-render guarantee (we forbid render-time fetch, `Math.random`,
`Date.now`, SMIL; we are byte-stable across runs; we gate + pause/resume). HyperFrames' own
`faceless-explainer` recipe uses **agentic per-scene HTML authoring** — we deliberately do NOT want
that (it breaks reproducibility/gating).

So: **mine HyperFrames' capabilities as DATA / TECHNIQUE and port them through Mason's gate. Adoption =
"port the technique + inline local deps + seed any randomness," NOT "drop the block in verbatim."**
(Registry blocks fetch a CDN GSAP and can carry non-deterministic defaults — never drop-in raw.)

The one exception, where adoption IS direct: **local-asset capabilities** (TTS/BGM/Lottie-JSON) whose
output is a frozen local file — those carry zero determinism risk.

## 3. The hard constraint (the vetting rule)
Every adopted block/technique must pass Mason's `scan_determinism`:
- **Inline the LOCAL `gsap.min.js`** (`graphic-overlays/assets/vendor/gsap.min.js` is a vendored copy) —
  no CDN fetch.
- **Seed every `Math.random`** with `mulberry32(seed)` (seed from `hash(shot_id)`).
- **No render-time fetch, no SMIL `<animate>`, no `Date.now`/`performance.now`/`rAF`** for visual state;
  finite repeats only (`Math.floor(dur/cycle)-1`).
- Audio/Lottie/media must be a **direct child of the composition root** (HF silent-blank trap).

## 4. Adoption tiers (by determinism risk × value)

### Tier A — Local-asset capabilities → ADOPT DIRECTLY (zero determinism risk)
- **Audio stage (Cadence — the ONE remaining pipeline stub). [CORRECTED by §7.1 verify]** Our v0.7.5
  install:
  - **TTS = `hyperframes tts` (Kokoro-82M local, works NOW, no API key, offline).** 12 voices
    (af_heart, am_michael, bf_emma…), multilingual (en/es/fr/hi/it/pt/ja/zh). The CLI is **Kokoro-only**
    — there is **no HeyGen/ElevenLabs option in our build**, and we hold **no HeyGen/ElevenLabs key**.
    Premium HeyGen voices = optional upgrade gated on getting a key (then `auth login` + `cloud`, or the
    `heygen-tts.mjs` script).
  - **Word timestamps for captions:** Kokoro doesn't emit them → chain **`hyperframes transcribe
    narration.wav --model small.en`** (Whisper, present) to get `[{id,text,start,end}]`.
  - **BGM: `hyperframes bgm` does NOT exist in v0.7.5** (no Lyria/MusicGen subcommand). Use **Pixabay
    music API (we hold `PIXABAY_API_KEY`) → download royalty-free track → ffmpeg mix/duck**, or another
    royalty-free source. `hyperframes beats` (present) detects beats in a music track for audio-reactive sync.
  - Audio attaches as `<audio data-start data-duration data-track-index>` (direct child of root).
  **We still own:** per-scene narration orchestration + voice policy; **mixing + ducking + loudness
  normalization to our LUFS target** (HF supports only static `data-volume`; ramps baked with ffmpeg
  `afade`) — calibration found our output ~7–8 LUFS too quiet.
- **Lottie → native player.** `window.__hfLottie.push(anim)` with `lottie-web`/`dotLottie`, `autoplay:false`,
  local JSON under `assets/`, seek-safe. **This answers the motion-stack's gating question (it has a
  native Lottie player) and deletes the "build a local player" work.** Magpie adds a Lottie asset type +
  license-clears + localizes; Mason emits the push. The `lottieExperiments` programmatic builder is the
  generator if we ever need Loop.
- **Captions timing.** Feed Mason the `[{id,text,start,end}]` word array from TTS `--words` so burned
  caption timing matches delivery exactly; optionally mine the 18 `caption-*` skins.

### Tier B — Ported techniques → PORT into Mason's deterministic emitter, then gate
- **Charts.** Port the `data-chart` block's types (bar/line + add pie/ring/race) and staggered reveal
  into Mason's build-time SVG emitter. Collapses motion-stack **M2** (the "d3 chart types" set ≈ this).
- **Conceptual diagrams.** Use the `flowchart` block (SVG connectors + nodes) + `hyperframes-animation`
  draw-on rules as the render library; keep ONLY the LLM `DiagramPlan` (English→structure — HF has no
  planner). Collapses most of the diagram-generator spec's from-scratch technique library.
- **Motion/effects.** Source effect tokens from `hyperframes-animation/rules/` + `blueprints/`
  (dozens of seek-safe GSAP recipes) instead of hand-writing them. Collapses motion-stack **M1**
  ("deeper GSAP"). Keep Iris's closed-vocabulary wrapper; populate it from HF's catalog.

### Tier C — Assembly-stage & architecture → PILOT / CONSIDER
- **Shader transitions** (14 WebGL: whip-pan, sdf-iris, glitch, domain-warp…) on signature beats via an
  assembly seam, instead of only FFmpeg `xfade`. A distinct production-value tier.
- **Parametrized compositions** (`--variables`/`--variables-file`, per-instance `data-variable-values`)
  as an automated data→render backbone — a real architectural option for how Mason templates render.
- **Quality upgrades to mine:** the 8 designer-grounded **visual styles**, the **prompt-expansion**
  stage (thin brief → fully art-directed shot list), `snapshot --at` QA (catches blank-media/mount-id
  traps lint/validate/inspect miss), `fitTextFontSize`/`pretext` (deterministic text-fit), and the
  camera/cinematography + audio-reactive rules.

## 5. The 5 highest-leverage moves (do in this order)
1. **Wire `hyperframes tts`+`transcribe`+`bgm` into Cadence** — the only stage we have *nothing* for,
   lowest risk (local files), biggest visible jump (videos get a voice + music). Proven in-env (Kokoro).
2. **Adopt the native Lottie player; delete the "build a player" work** from the motion-stack spec.
3. **Replace the hand-rolled bar chart with the `data-chart` capability** (line/pie/animated/labels).
4. **Source effect tokens from `hyperframes-animation/rules/`+`blueprints/`** (this IS motion-stack M1).
5. **Pilot shader transitions on signature beats** (assembly seam).

## 6. Verdict on the existing roadmap
- **Motion-stack spec — ~60–70% reducible.** Lottie player = solved (native, Tier A). d3 charts ≈ the
  `data-chart` block (Tier B). "Deeper GSAP" = the animation rules library (Tier B). The "Loop 🎞️"
  generator is mostly assembly (the `lottieExperiments` builder exists). 4 phases → ~1.5 of real work,
  **no new render capability to build.**
- **Diagram-generator spec — ~40% reducible.** Keep the LLM `DiagramPlan`; render via `flowchart` +
  animation draw-on rules, not a from-scratch technique library. Still gated on the unresolved
  frequency need (D9). The §3.5 "animated flat SVG composed by Mason" direction is right — just source
  the technique from HF instead of porting the `claude experiments` by hand where they overlap.

## 7. Verify-first (gates the build — knowledge may be version-stale)
The skill docs may describe a fuller/newer surface than our installed `npx hyperframes` (memory says
~v0.6.115/v0.7.5). Before building, run against OUR install: `hyperframes info`, `hyperframes catalog
--type block`, `hyperframes catalog --type component`, `hyperframes --help`, and probe `tts`/`bgm`/
`add` once. Confirm: the registry list + `add` work; `tts` reaches HeyGen via our key (or use
`heygen-tts.mjs`); the native Lottie adapter (`window.__hfLottie`) exists in our version. Report
findings; reshape Tiers if anything is absent.

## 7.1. VERIFY-FIRST RESULTS (2026-06-25, against our install)
- **Version v0.7.5**, Node v22.18.0, ffmpeg 6.1.1 — all good. Installed at `~/.npm/_npx/.../hyperframes`.
- **Command surface confirmed:** `init, add, capture, catalog, preview, present, publish, render, lint,
  beats, inspect, snapshot, info, compositions, docs, benchmark, browser, doctor, upgrade, cloud,
  lambda, cloudrun, skills, transcribe, tts, remove-background, auth`.
- ✅ **Native Lottie adapter present** (`__hf*` runtime globals incl. lottie handling in
  `dist/hyperframe.runtime.iife.js`). Motion-stack "build a player" stays DELETED.
- ✅ **TTS works (Kokoro local, no key)**; ⚠️ **Kokoro-only** in our build; **no HeyGen/ElevenLabs key** present.
- ❌ **`bgm` NOT in v0.7.5** (skill docs ran ahead). Use Pixabay music + ffmpeg. `beats` IS present.
- ⚠️ **`catalog`/`add` require network to the registry CDN — every fetch FAILED/timed out in this
  environment.** So registry blocks can't be pulled here right now. This is fine for our model anyway:
  the determinism wall already forbids render-time fetch, so we **`add` blocks ONCE at dev time (where
  network works), vendor them into the repo, port + vet, then Mason emits the local technique.** OPEN:
  confirm whether the registry CDN is reachable from the CEO's normal machine (it may be a sandbox-egress
  limit here) — needed before Tier-B block adoption.
- **Keys we DO hold (.env):** GEMINI, DEEPSEEK, YOUTUBE, SMITHSONIAN, PEXELS, PIXABAY, TAVILY. **No**
  HeyGen, ElevenLabs.

## 8. Build order
0. **Verify-first spike (§7)** + port ONE `data-chart` and ONE `flowchart` into Mason with local GSAP +
   seeded randomness; confirm they pass `scan_determinism` + `run_gate`. Run `tts` end-to-end on one script.
1. **Audio stage (Cadence)** — Tier A move #1.
2. **Lottie player adoption + Magpie Lottie sourcing** — Tier A move #2 (revise motion-stack M3, delete M-player).
3. **Charts: `data-chart` port** — Tier B move #3 (collapse motion-stack M2).
4. **Diagram render via `flowchart` + draw-on rules** — revise diagram-generator spec down.
5. **Effect-token mining from `hyperframes-animation`** — Tier B move #4 (collapse motion-stack M1).
6. **Shader-transition pilot** on signature beats — Tier C move #5.

## 9. DO NOT
- Don't adopt HyperFrames' **agentic per-scene authoring** (`faceless-explainer` worker model) — it
  breaks our reproducibility/gating/pause-resume. We want its capabilities as DATA, not its freedom.
- Don't drop registry blocks in **verbatim** — they fetch CDN GSAP + may be non-deterministic; port+vet.
- Don't keep building the motion-stack/diagram specs as written before applying §6 reductions.

## 10. Read first (when building)
`.agents/skills/`: `hyperframes-cli/SKILL.md`, `hyperframes-registry/` (add/discovery/wiring),
`hyperframes-media/` (tts/bgm/captions + `scripts/heygen-tts.mjs`), `remotion-to-hyperframes/references/
lottie.md` (the native Lottie integration), `motion-graphics/` (`shot-plan-ir.md`, `categories/charts`,
`categories/maps`, `builder-contract.md`), `hyperframes-animation/` (rules + blueprints),
`hyperframes-core/references/determinism-rules.md` + `variables-and-media.md`. Our side:
`composition-engineer/composition_engine.py`, `hf_tools.py`. Memory: `hyperframes-verified-surface`,
`hyperframes-full-capability`, `svg-lottie-experiments`.
