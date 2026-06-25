# Thumbnail Artist — "Glint 🎯" (planning spec, NOT yet built)

**Date:** 2026-06-23 · **Status:** Scoped & queued (build later) · **Owner:** CEO + Atlas
**Roadmap ID:** **#8** (off-pipeline agent; lands with / as a dependency of **#6 Herald** — it feeds
the T3 publish-confirm modal). **Persona name is provisional** — "Glint 🎯" unless the CEO renames it.

> This document is a faithful capture of the CEO's brief so a future session has COMPLETE context.
> Nothing here is implemented yet. The next *build* after Slice 3 is still **Slice 4 (#4 Settings)**;
> Glint is built when #6 Herald's packaging/T3 work lands (the agent itself can be built standalone
> earlier — it only needs a finished project's artifacts — with the Herald wiring finished at #6).

---

## 1. Goal & one-line identity
A standalone, **OFF-PIPELINE** thumbnail artist that generates a SET of high-CTR YouTube thumbnail
**candidates** (default N=3, meaningfully distinct), which **Herald delegates to at packaging time**
and the CEO picks exactly one from in the **T3 publish-confirm modal**. **NO Canva, no external image
API.** Thumbnails are **HTML+CSS stills screenshotted by headless Chrome** (reusing Mason's Chrome
approach), at exactly **1280×720**.

**Glint's SOUL is high-CTR thumbnail JUDGMENT; the renderer is just hands.**

## 2. Where it fits (dependencies & sequence)
- **Off-pipeline, additive:** one `AgentEntry` in `registry.py` + one adapter in `atlas/adapters/` +
  one new contract `atlas/contracts/thumbnail_set.schema.json`. Surfaces through the registry with
  **zero** orchestrator/pipeline change. **NOT a stage** — `STAGES` and the 10-stage line are
  UNCHANGED. Copy the registration shape of the existing off-pipeline entries
  (`reference_analyst` registry.py:271 / `editorial_coach`:308 / `production_coach`:329) and their
  adapters (`atlas/adapters/reference_analyst.py` etc. — engine never imports Atlas; Atlas stamps
  `schema_version` + validates at the seam).
- **Coupled to #6 Herald:** Herald's `package` step delegates to Glint to obtain the candidates; they
  surface in the **T3** modal where the CEO selects one. Selection is a **human T3 decision**; Glint
  only produces the set. The thumbnail flow does **not** auto-publish and **does not satisfy any
  gate** — it feeds the T3 modal (consistent with spec §4 T3 / §8: the LLM plane never satisfies a
  guarantee).
- **Inputs require a near-finished project:** topic/working_title + `script.json` (hook/message) +
  `style_guide.json` (brand) + a local focal source. So Glint runs at packaging time, after render.

## 3. Read these first (when building — match conventions exactly)
- `atlas/registry.py` + an off-pipeline adapter (`reference_analyst.py` / `editorial_coach.py` /
  `production_coach.py`) — the off-pipeline registration shape.
- `atlas/adapters/base.py` (Adapter ABC: `run_job` + `ask`) and `atlas/adapters/loader.py` (the
  in-process isolated-import invariant — Glint MUST respect it).
- `composition-engineer/composition_engine.py` + `hf_tools.py` — Mason's headless-Chrome render path
  and inline-brand-logo logic (`BRAND_CHIPS` / Lobe Icons).
- `art-director/art_engine.py` + `atlas/contracts/style_guide.schema.json` — Iris's style tokens the
  thumbnail must honor.
- `asset-sourcer/source_engine.py` — Magpie's license-clean local-asset path (focal images).
- `atlas/contracts/__init__.py` + a couple of `*.schema.json` — the contract pattern.
- One full sibling skeleton (e.g. `reference-analyst/`) — `SKILL.md` + `soul/` + `run.py` + `chat.py`
  + `llm.py` + `tests/` layout.

## 4. What to build
A new sibling project `thumbnail-artist/` following the fleet skeleton EXACTLY: `*_engine.py` (pure +
injectable), `run.py` (CLI), `chat.py` (REPL), `llm.py` (provider seam),
`chat_state.py`/`compaction.py` (memory), `SKILL.md` (job contract), `soul/` ({SOUL.md, STYLE.md,
examples/}), `tests/`. PLUS its off-pipeline registration + adapter + contract inside `atlas/`.

## 5. Architectural constraints (non-negotiable)
1. **Off-pipeline, additive** — one registry entry + one adapter + one contract; no spine/stage/gate/
   10-stage-contract/registry-of-7 change beyond the single off-pipeline entry.
2. **Engine pure + injectable** (fleet rule) — seams passed as ARGS so tests run offline/deterministic:
   - `chat_fn` → the LLM seam (the JUDGMENT: proposes candidate concepts).
   - `render_fn(html, out_path, width, height)` → HTML→PNG screenshot seam (real headless Chrome in
     prod; a stub that writes a placeholder PNG in tests).
   - a **focal-image source seam** → resolves a LOCAL, license-clean hero image.
   Engine **never imports Atlas**; Atlas stamps `schema_version` + validates at the adapter seam.
3. **A thumbnail is a STILL, not a HyperFrames timeline** — screenshot a standalone HTML+CSS page at
   exactly **1280×720**; do NOT wedge a still through HyperFrames' paused-GSAP video timeline.
4. **Determinism wall** (same as Mason) — no render-time network; focal image + fonts + logos
   LOCAL/inlined; no `Math.random`/`Date.now`/nondeterminism in the HTML; a given spec renders a
   byte-stable PNG.
5. **Focal image is LOCAL + license-clean** — sourced ONLY from (a) Magpie's cleared assets in the
   project's `asset_manifest`, (b) Mason's inline brand logos (Lobe Icons — ideal for AI-comparison
   videos), or (c) a frame extracted from the finished `video.mp4` via ffmpeg. **Never a remote
   fetch.**
6. **On-brand via Iris** — read `style_guide.json` and honor palette (incl. the **#FFD000**
   signature), typography, brand color so the thumbnail matches the video.
7. **Loader invariant** — no lazy import of a colliding bare name at call time, no mutable
   module-level globals two concurrent belt videos could stomp (Glint runs on the multi-video belt).
8. **Output is LOCAL** — write candidates to `projects/<slug>/thumbnails/candidate_N.png` + a
   `thumbnail_set.json` manifest, mirroring how assets/audio are stored.

## 6. Engine logic
**Inputs:** topic/working_title + `script.json` (hook/message) + `style_guide.json` (brand) +
available focal sources (asset_manifest / brand logos / video frame).
**Flow:**
1. **JUDGMENT (`chat_fn`):** propose **N=3 DISTINCT** thumbnail CONCEPTS that differ meaningfully
   (different focal treatment / framing / punch-text angle), each grounded in the CTR principles (§7)
   and **Vera's reference standard if present**. Output a **structured concept spec** per candidate —
   **closed-vocab where it touches Iris's vocabulary** (unknown tokens are an ERROR, never silently
   dropped — match the art/composition stages' closed-set discipline).
2. **RESOLVE** a local focal image per concept via the focal-image seam (license-clean).
3. **COMPOSE** each concept as a standalone HTML+CSS page (1280×720) using the `style_guide` tokens.
4. **RENDER** each via `render_fn` → a local 1280×720 PNG (YouTube spec: 16:9, <2 MB, RGB — **verify
   current YouTube thumbnail requirements at build time**; 1280×720 / <2 MB / JPG·PNG·GIF / RGB has
   been stable but confirm).
5. **VALIDATE** the `thumbnail_set` against its contract; return the candidate set (specs + local PNG
   paths + a short design rationale per candidate).
**Graceful degradation** (fleet rule): a missing input/source yields a clean **placeholder candidate
+ a note**, never a crash.

## 7. The SOUL — high-CTR thumbnail JUDGMENT (encode as craft in SOUL.md/STYLE.md)
- ONE clear focal subject; instant visual hierarchy; readable at small size.
- TEXT IS MINIMAL — punch text **~3 words max**, huge, high-contrast; never a sentence.
- Strong contrast + saturation; face/emotion when a subject exists; a **curiosity/tension gap** the
  title doesn't already give away.
- Framed to stand out **AGAINST competitor thumbnails** in the niche, not in a vacuum.
- On-brand with Iris's `style_guide` (palette, typography, the **#FFD000** signature) — channel
  consistency is itself a CTR asset.
- Glint **specifies and renders**; it owns the thumbnail but never edits other agents' artifacts, the
  rubric, or pass/fail.

## 8. Herald delegation (packaging time, off-spine)
- **JobSpec** e.g. `thumbnail_generate_candidates(topic/slug)` → returns the candidate set.
- Herald's **`package`** step delegates to Glint (delegate-to-sibling pattern) to obtain the 3
  candidates; they surface in the **T3** publish-confirm modal where the CEO selects exactly one.
  Realizes the master spec's "thumbnail-select via Iris contract." Selection is a human T3 decision;
  Glint only produces the set. Does NOT auto-publish and does NOT satisfy any gate.

## 9. Contract (new, additive)
`atlas/contracts/thumbnail_set.schema.json` (Draft 2020-12, `additionalProperties: true`, requires
`schema_version`): a **candidate array**, each candidate `{candidate_id, concept_spec, png_uri
(local), focal_source {type: cleared_asset|brand_logo|video_frame, ref}, design_rationale,
dimensions}`, plus set-level metadata `{slug, count, style_guide_ref}`. Wire into
`contracts/__init__.py` `validate()` / `version_for()` like the others. **Off-pipeline artifact, NOT
a 10-stage-pipeline artifact.**

## 10. Self-improvement hook (leave the SEAM; do NOT build rubric integration now)
- Echo's diagnosis map already routes **low CTR → weak title/thumbnail**. Later that route can target
  **Glint's soul** as a soft-tier coaching addendum (markdown only, via the existing
  `WriteBoundaryError`). Keep the soul coachable (soft-tier markdown); do NOT wire eval/rubric
  integration in this build.
- Thumbnail-specific **quality bands** would touch the **CEO-owned/frozen rubric** → a future
  CEO-interview item (consistent with the rest of the eval system). **Note, don't build:** when
  thumbnails enter the loop, `coach_for_stage` needs a routing decision (punch-text = editorial/Quill;
  visual craft = production/Flux) — **flag it, don't decide it here.**

## 11. Tests (pytest, pure unit, offline/no-network — extend `atlas/tests/` + the project's `tests/`)
- Engine + injected fake `chat_fn` + fake `render_fn` → exactly **N=3** candidates; each has a valid
  concept spec, a local PNG path, a license-clean `focal_source`, and PASSES the contract.
- **Closed-vocab discipline:** an unknown style token is an ERROR, not silently dropped.
- **Determinism:** same inputs → identical spec/output; assert no render-time network and no
  nondeterministic tokens in the composed HTML.
- **Focal sourcing:** covers all three sources; never emits a non-local/uncleared focal uri.
- **Registry:** Glint surfaces (roster + generated tools) with NO orchestrator change; the
  off-pipeline entry doesn't alter `STAGES` or the 10-stage pipeline.
- **Adapter:** `run_job` returns a compact digest; `ask` returns in-character via the `llm` seam.
- **Herald delegation:** Herald's `package` calls the thumbnail job and receives the candidate set.
- **Loader invariant** respected (no colliding lazy import / mutable global) under a concurrent-jobs
  test.
- *(Optional, kept OUT of the offline suite)* one real-render integration test asserting a 1280×720
  PNG under 2 MB.

## 12. Definition of done
A fleet-shaped `thumbnail-artist` agent, off-pipeline (one registry entry + one adapter + one
contract, zero spine change), rendering 3 on-brand HTML+Chrome thumbnail candidates from local
license-clean focal images, deterministic and offline-testable, delegated by Herald's `package` step
and surfaced to the CEO in the T3 modal, with the soul encoding the CTR judgment and the eval hook
left as a seam. Pytest green incl. determinism + contract + negative (no remote/uncleared focal, no
spine change) cases.

## 13. DO NOT
- No Canva / external image API; no fetch at render time.
- No pipeline stage; don't touch `STAGES`/spine/gate logic/10-stage contracts/registry-of-7 beyond
  the one off-pipeline entry.
- Engine must not import Atlas or read Settings/`style_guide` globally — pass seams/inputs in.
- Never emit a non-local or non-license-cleared focal image.
- Don't wire thumbnail quality bands into the frozen (CEO-owned) rubric in this build.

## 14. Build-time verifications & open questions
- **Persona name** — confirm "Glint 🎯" or the CEO's chosen name/emoji.
- **YouTube thumbnail spec** — confirm 1280×720 / 16:9 / <2 MB / JPG·PNG·GIF / RGB still current.
- **Focal default for AI-comparison videos** — Lobe Icons brand logos are ideal; confirm the default
  focal selection order (cleared_asset → brand_logo → video_frame) suits the typical niche.
- **N=3** — confirm the candidate count (the brief sets 3; trivially configurable).

## 15. Report-back checklist (when built)
Persona/name used · files added · focal-image sources wired · how Herald delegates · anything the
build-time YouTube-spec check changed · any deviations from this spec and why.
