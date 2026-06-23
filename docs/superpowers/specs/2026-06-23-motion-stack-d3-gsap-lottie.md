# Motion Stack Upgrade — d3 + deeper GSAP + Lottie (+ "Loop 🎞️" generator) — planning spec, NOT yet built

**Date:** 2026-06-23 · **Status:** Scoped & queued (build later) · **Owner:** CEO + Atlas
**Roadmap ID:** **#9** (pipeline-engine upgrade track — **independent of the Control Room UI slices**;
adds ONE new off-pipeline agent). **Generator persona name is provisional** — "Loop 🎞️" unless the
CEO renames it.

> Faithful capture of the CEO's brief so a future session has COMPLETE context. Nothing here is
> implemented yet. The CEO delegated **the phasing and the split** to the builder — §6 below is a
> PROPOSED phase plan to be finalized (and re-reported) at build time after the §3 verification spike.
> This track is independent of the Control Room slices and can run as its own thread whenever the CEO
> schedules it.

---

## 1. Goal
Bring **d3.js** (data-driven SVG charts), **deeper GSAP** (richer timeline motion), and **Lottie**
(vector motion-graphic assets) into HyperFrames video generation, so **Iris can DESIGN with them and
Mason can RENDER them** — without breaking the determinism wall, the closed-vocabulary discipline, or
the 10-stage spine.

## 2. The three layers enter DIFFERENTLY (do NOT treat them as one thing)
- **GSAP = the TIMELINE SUBSTRATE Mason already targets** ("HTML + paused GSAP timeline + data-*").
  NOT a new dependency — using MORE of GSAP's capability. Lives in Mason's engine + Iris's **EFFECTS**
  vocabulary. Add new motion as **NEW CLOSED-SET effect tokens**, not arbitrary script.
- **d3 = the `data-chart` layout's renderer.** A `data-chart` LAYOUT + a native chart render already
  exist; d3 **UPGRADES that one layout** (real data-driven SVG: bar/line/area/etc.). NOT a general
  video capability — scope it to the chart path, with **chart types as a closed set**.
- **Lottie = pre-authored vector MOTION-GRAPHIC ASSETS** (icons, loaders, illustrative motion). The
  genuinely new ASSET-SHAPED thing, with a **source/clear/store-local/reference** lifecycle. Gets the
  source+generate fleet treatment (§4).

## 3. VERIFY FIRST (gates the whole design — report findings before building)
Knowledge may be stale; these gate the design:
1. **HyperFrames + Lottie** — does the renderer support a Lottie player in its HTML render context,
   and how (which player, what JSON it ingests)? Read `.agents/skills/` HyperFrames docs (`hyperframes`,
   `-animation`, `-media`, `-cli`) and the composition engine. If not native, determine the
   **local-player approach** (bundled `lottie-web` rendering a LOCAL json) — **never a render-time
   fetch**. (Cross-check the `hyperframes-verified-surface` memory for the render CLI's known
   behavior.)
2. **GSAP licensing** for this use — HyperFrames already runs on a paused GSAP timeline; confirm we're
   **extending existing usage**, not adding a new licensed dep improperly.
3. **Current d3 version/approach** already used in Mason's "native data-chart render" — **extend
   that**, don't bolt on a parallel charting path.
State findings before building; if any blocks a piece, say so and adapt.

## 4. The Lottie SOURCING-AND-GENERATION split (CEO-decided — implement exactly)
Two responsibilities, divided by job:
1. **SOURCING + LICENSING → MAGPIE** (`asset-sourcer/source_engine.py`). Add a **Lottie asset TYPE**
   to the EXISTING asset pipeline: search the web (e.g. LottieFiles + other sources) for a Lottie
   fitting the storyboard beat, run it through Magpie's **EXISTING LICENSE TRUTH TABLE** (only the
   accept-list — CC0/PDM/PD/CC-BY/CC-BY-SA + complete attribution + a LOCAL file — reaches `cleared`;
   ambiguous/forbidden → flagged placeholder), and **DOWNLOAD it LOCAL**. Reuse Magpie's existing
   ranking/clearance/local-file discipline — **do NOT reinvent licensing**. URIs are ALWAYS local.
2. **GENERATION-ON-MISS → a NEW standalone OFF-PIPELINE agent** ("**Loop 🎞️**", rename if the CEO
   chose). Builds a `.lottie`/Lottie-JSON **FROM SCRATCH, LOCAL**, invoked **ONLY when Magpie finds
   nothing clearable/relevant** (the honest scoping of "generate only if nothing CLEARABLE online").
   Magpie (or the pipeline seam) delegates to it as the **FALLBACK**. Same fleet skeleton as the other
   siblings (`*_engine.py` pure+injectable, `run.py`, `chat.py`, `llm.py`, memory, `SKILL.md`, `soul/`,
   `tests/`). **One registry entry + one adapter + one contract. OFF-PIPELINE — NOT a new stage;
   `STAGES` and the 10-stage line are UNCHANGED.** Its soul = motion-graphics taste (what motion
   serves this beat, kept simple/legible/on-brand). Its engine = generate Lottie JSON deterministically
   via an injected seam; offline-testable.

**Explicit fallback chain:** storyboard names a motion-graphic need → Magpie searches + license-clears
+ downloads local → **if nothing clearable, delegate to Loop** → local `.lottie`. Either way the
result is a **LOCAL, CLEARED** motion-graphic asset Mason renders.

## 5. Hard constraints (non-negotiable across all phases)
1. **Determinism wall** (Mason's rule, now also Lottie's): NO render-time network. Every Lottie
   (sourced OR generated), every font, every d3 dataset is **LOCAL/inlined** at render time. No
   `Math.random`/`Date.now` in rendered HTML. A given storyboard renders **byte-stable** output.
   Lottie players load a LOCAL json only.
2. **Closed-vocabulary discipline is LOAD-BEARING** — do NOT open the floodgates. LAYOUTS/TEXTURES/
   EFFECTS/TRANSITIONS are CLOSED SETS where unknown tokens are an ERROR. Extend DELIBERATELY: add
   new **GSAP-backed EFFECT tokens**, a **`lottie` SHOT KIND**, and a **closed set of d3 CHART TYPES**
   — each a named, validated token. Keep "unknown token = error." The win is a WIDER vocabulary, not
   NO vocabulary. (Vocabulary lives in PROJECT_CONTEXT §7 — extend it there + in the contracts.)
3. **Frozen-but-extensible contracts** — extend `style_guide`/`storyboard`/`asset_manifest` by ADDING
   optional fields under a **BUMPED `schema_version`** (`additionalProperties:true`). Old readers keep
   working. Add a new local-Lottie asset shape (or a Lottie asset type in `asset_manifest`) + the d3
   chart-spec shape. Validate at the seam; nothing crashes on bad data.
4. **Engines pure + injectable, NEVER import Atlas** — Iris/Mason/Magpie/Loop take seams as args
   (`chat_fn`, `render_fn`, search/fetch client, `generate_fn`). Tests run offline/deterministic.
5. **Iris specifies, Mason builds** (existing rule) — Iris adds the new tokens/specs to
   `style_guide` + `storyboard` (incl. WHERE a Lottie/d3 chart/GSAP effect goes and the data for a
   chart); Iris writes **NO HTML**. Mason renders. **Loop BUILDS Lottie JSON but does not place it in
   the video** — Iris references, Mason renders.
6. **Mason's auto-gate still applies** — new motion must pass the existing per-scene self-scan (no
   network/fetch, no SMIL filters, no late `gsap.set`) + lint + validate + inspect. A scene whose
   Lottie/d3/GSAP fails the gate **BLOCKS the stage** as today. **Extend the auto-gate** if the new
   motion introduces new failure modes (e.g. a Lottie that fetches, an unbounded animation).
7. **Loader invariant** for Loop — no lazy import of a colliding bare name at call time, no mutable
   module-level globals two concurrent belt videos could stomp.
8. **Graceful degradation** — a missing/failed Lottie or chart dataset yields a clean placeholder +
   a note, never a crash (fleet rule).
9. **Signature beats preserved** — Iris's one **#FFD000** beat and the **master-bridge** audio rule
   are untouched; the **motion budget** still governs (the #FFD000 beat is never trimmed). New motion
   lives WITHIN the motion budget, not around it.

## 6. PROPOSED phase plan (builder owns this — finalize & re-report at build time)
The CEO delegated phasing. Proposed ordering, lowest-risk first, each phase independently landable +
tested:
- **Phase M0 — Verification spike (gating).** Resolve §3 (HyperFrames Lottie player + how; GSAP
  licensing; existing d3 version/path). Output: a short findings note that confirms or reshapes M1–M4.
  No production code beyond a throwaway probe.
- **Phase M1 — Deeper GSAP as new EFFECT tokens.** Lowest risk (no new dep). Add a small, named set of
  GSAP-backed EFFECT tokens to Iris's closed EFFECTS vocabulary + `style_guide`/`storyboard` (bumped
  schema); Mason renders them; extend the auto-gate for any new failure mode. Unknown token = error.
- **Phase M2 — d3 data-chart upgrade.** Scope strictly to the `data-chart` LAYOUT. Add a **closed set
  of d3 chart types** + a **chart-spec shape** (data + type + axes/labels) in the contracts; Iris
  emits the spec (incl. the data); Mason renders deterministic SVG; missing dataset → placeholder.
- **Phase M3 — Lottie sourcing via Magpie + Mason render.** Add a **Lottie asset type** to
  `asset_manifest` (bumped schema) + a `lottie` **SHOT KIND**; Magpie sources → license truth table →
  local; Mason loads a LOCAL json via the verified player; extend the auto-gate (no-fetch, bounded).
- **Phase M4 — "Loop 🎞️" generator agent (off-pipeline) as the Magpie-miss fallback.** New sibling
  project + one registry entry + one adapter + one contract; Magpie/seam delegates to Loop only when
  nothing clearable was found; Loop returns a LOCAL `.lottie`.

(Reasonable to fold M3+M4 into one Lottie phase, or to land M1/M2 in parallel since they're
independent — the builder decides after M0 and reports.)

## 7. Tests (pytest, pure unit, offline/no-network; extend `atlas/tests/` + each project's `tests/`)
- **Iris** emits new EFFECT tokens / `lottie` shot kind / d3 chart-spec into `style_guide`+`storyboard`;
  unknown tokens RAISE (closed-set), valid ones validate against the bumped contracts.
- **Mason** renders each new motion type from a storyboard with INJECTED render/asset seams; asserts
  NO render-time network and NO nondeterministic tokens in composed HTML; Lottie loads a LOCAL json.
- **Magpie** sources a Lottie, clears it via the license truth table (accept-list → cleared+local;
  ambiguous → flagged placeholder), and NEVER emits a non-local/uncleared Lottie uri.
- **Fallback chain:** Magpie search yields nothing clearable → Loop is delegated to and returns a
  LOCAL Lottie; Magpie yields a clearable hit → Loop is NOT called.
- **Loop:** engine with injected `generate_fn` produces a valid local Lottie; registry surfaces it
  (roster + tools) with NO orchestrator change; off-pipeline entry doesn't alter `STAGES`.
- **d3:** chart-spec → deterministic SVG; closed chart-type set enforced; missing dataset →
  placeholder.
- **Mason auto-gate:** a Lottie that would fetch, or an unbounded/nondeterministic animation, FAILS
  the gate and BLOCKS the stage.
- **Loader-invariant** concurrency test for Loop.
- *(Optional, OUT of the offline suite)* one real-render integration test producing actual frames with
  a Lottie + a d3 chart + a GSAP effect.

## 8. Definition of done (end state, per the final phase plan)
Iris can specify and Mason can render d3 data-charts, richer GSAP motion (as new closed-set effect
tokens), and local Lottie assets; Magpie sources+license-clears+localizes Lotties via its existing
truth table; **Loop** builds Lotties only on a Magpie miss; contracts extended frozen-but-extensibly;
the closed vocabulary intact (unknown = error); determinism wall and auto-gate intact; the 10-stage
spine and `STAGES` UNCHANGED (one new off-pipeline agent only); pytest green incl. determinism +
license + fallback-chain + auto-gate + closed-set negative tests.

## 9. DO NOT
- No fetch of any Lottie/chart data at render time; never emit a non-local or uncleared asset uri.
- Don't break the closed-vocabulary rule (no arbitrary motion/script as a token); unknown = error.
- No pipeline stage; don't touch `STAGES`/spine/gate logic/10-stage flow beyond the one new
  off-pipeline generator agent.
- Don't let Iris write HTML or let any engine import Atlas / read Settings globally.
- Don't reinvent licensing — Lottie sourcing reuses Magpie's existing truth table.
- Don't let new motion escape the motion budget or trim the #FFD000 signature beat.

## 10. Read these first (when building)
PROJECT_CONTEXT.md (esp. §6 domain mechanisms + §7 closed vocabulary) + the master design spec, end
to end. Then `.agents/skills/` HyperFrames docs; `composition-engineer/composition_engine.py` +
`hf_tools.py` (Mason's render + auto-gate + native chart); `art-director/art_engine.py` +
`atlas/contracts/style_guide.schema.json` + `storyboard.schema.json` (Iris's vocabulary);
`asset-sourcer/source_engine.py` + `atlas/contracts/asset_manifest.schema.json` (Magpie's truth
table); the off-pipeline registration shape (`registry.py` + `adapters/reference_analyst.py`). Memory:
`hyperframes-verified-surface`, `issue-2-irrelevant-footage`.

## 11. Report-back checklist (when built)
Chosen PHASE PLAN + split (and why) · what the §3 verification found (HyperFrames-Lottie / GSAP
licensing / d3) and how it shaped the design · the new vocabulary tokens added · the generator
agent's persona/name · any deviations from this spec and why.
