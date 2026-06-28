# GOLDEN_REFERENCE.md — the "Dark Truth Behind the Social Media" technique teardown

**Source:** `reference/dark-truth-social/` (`index.html` ~2377 lines, `DESIGN.md`, `AGENTS.md`).
**Status:** This is the single example of the quality bar we must hit. Everything below is a
**transferable technique** — the mechanism, the exact identifiers/line refs, and how to generalize
it past this one video. Line numbers are into `reference/dark-truth-social/index.html` unless noted.

The format: 1920×1080, deterministic HTML+GSAP composition rendered by HyperFrames frame-by-frame.
Total runtime **85 s** (the `data-duration` on `#root`, line 1257), driven by VO, NOT the 240 s
editorial grid it was authored against. That gap between "authored grid" and "real runtime" is the
heart of technique #2.

The lesson that ties all nine techniques together: **none of this is a reusable effect from a
closed menu.** Every motion beat is bespoke GSAP authored against the actual content of its scene,
on one real timeline, looked at frame by frame. The anti-patterns section at the end names what we
are moving away from.

---

## 1. The single-file, paused-GSAP, seekable, deterministic target

**(a) What it does.** The entire video is ONE self-contained `index.html`: inline `<style>`, inline
markup for all 9 scenes, inline `<script>` with the whole animation program. No build step, no
framework, no external state. A frame at time *t* is a pure function of *t* — the renderer seeks the
timeline to *t*, samples the DOM, screenshots. That is what makes it reproducible and parallelizable.

**(b) The exact mechanism.**
- GSAP loaded from CDN, animation core is one timeline created **paused**: `tlReal = gsap.timeline({ paused: true })` (line 1668) and at the very end published to the global registry the renderer reads: `window.__timelines["dark-truth"] = tlReal` (line 2374, declared `window.__timelines || {}` at 1639).
- The renderer never "plays" — it calls `tl.seek(t)` per frame. So **everything visible must be a deterministic function of timeline position**: no `requestAnimationFrame` loops, no CSS `@keyframes` animations that run on wall-clock, no `transition:` that eases on real time. All motion lives on the GSAP timeline so seeking to *t* fully determines the frame.
- The root carries the composition contract as data attributes: `data-composition-id="dark-truth" data-start="0" data-duration="85" data-width="1920" data-height="1080"` (line 1257).
- Every timed element follows the HyperFrames key rules (AGENTS.md "Key Rules"): `class="clip"` plus `data-start` / `data-duration` / `data-track-index` (e.g. scenes at lines 1289, 1311, 1339…; captions 1574+; audio 1547+). `clip` is how the framework controls per-element visibility windows.
- Decorative nodes that should not participate in the layout audit are tagged `data-layout-ignore` (e.g. the orbit cluster 1341, calendar 1360, cursor 1384, caption layer children). Overlap-by-design nodes get `data-layout-allow-overlap` (line 1869).

**(c) How to generalize.** This is the **target shape for every composition we emit**: one HTML file,
one paused timeline on `window.__timelines[id]`, all motion expressed as GSAP tweens (never wall-clock),
every timed node a `.clip` with the three `data-*` timing attrs, root carrying `data-duration`. If a
technique can't be expressed as "function of timeline position," it doesn't belong — it will flicker or
desync under frame-seek. Treat "seekable + deterministic" as a hard gate, not a style preference.

---

## 2. The VO-lock RE-TIMER proxy — authored "old grid" tweens remapped into the real VO window

This is the single most reusable architectural idea in the file. **It lets you author choreography
against a clean conceptual grid and then conform the whole thing to real narration automatically, so
dead air dies without re-hand-timing a single tween.**

**(a) What it does.** Every per-scene animation was authored against the original 240 s editorial
storyboard (scene 1 at 0–18 s, scene 2 at 18–32 s, …). But the actual VO is much tighter — 85 s total,
with per-scene starts/durations dictated by how long the narrator actually takes. Rather than rewrite
hundreds of tween positions, a proxy intercepts every `.to/.from/.fromTo/.set` call, looks at the
absolute time it was authored at, figures out which *old* scene that belongs to, and **linearly remaps
the position AND scales the tween's duration/stagger/keyframes** into the corresponding *new*
(VO-driven) scene window. Choreography auto-fits; if a scene got shorter, its tweens compress; if a VO
line ran long, its scene's beats stretch to fill — no manual dead air.

**(b) The exact mechanism** (lines 1670–1706, comment block 1670–1676):
- Four parallel arrays, one entry per scene:
  - `OS` — old scene starts `[0,18,32,60,92,128,162,188,220]` (line 1677)
  - `OD` — old scene durations `[18,14,28,32,36,34,26,32,20]` (1678)
  - `NS` — new (VO-driven) starts `[0,5.867,9.259,20.246,31.446,43.627,54.443,67.008,78.315]` (1679)
  - `ND` — new durations `[6.267,3.792,11.387,11.6,12.581,11.216,12.965,11.707,6.544]` (1680)
- `SCALE = ND.map((d,i)=> d/OD[i])` — per-scene time-compression factor (1681).
- `sceneOf(t)` — which old scene an absolute authored time falls in: walk scenes high→low, return first where `t >= OS[n]` (1682–1685).
- `RT(t)` — remap one absolute time: `NS[n] + (t - OS[n]) * SCALE[n]` (1686–1689). I.e. offset within the old scene, scaled, added to the new scene start.
- `scaleVars(v,n)` — multiply a tween-vars object's `duration`, `stagger`, and any `keyframes[].duration` by `SCALE[n]` (1690–1695). Guards against DOM nodes / non-objects (`v.nodeType`).
- `wrap(method)` — returns a function that: reads the **last argument** (GSAP's position parameter), and if it's a number, computes `n = sceneOf(pos)`, scales every preceding vars object via `scaleVars`, replaces the position with `RT(pos)`, then forwards to the real timeline (1696–1704).
- The exported proxy: `const tl = { to: wrap("to"), from: wrap("from"), fromTo: wrap("fromTo"), set: wrap("set") }` (line 1706). **All scene choreography below calls `tl.*` and is silently remapped.**
- **The deliberate exception:** transitions and the ticker are authored on `tlReal` **directly** (real new-timeline times, NOT remapped) — because they live *between* scenes / span the whole runtime, where the old-grid mapping is meaningless. See the transition functions reading `NS[b]` (lines 2309+) and the ticker tween on `tlReal` (2371).

Because everything is one linear remap, an authored beat like `tl.to(slab, {...}, 1.0)` (the S1
count-up, line 1963) or a hero reveal at old time `19.8` (line 1749) just lands at the right place in
the compressed timeline with no per-tween bookkeeping.

**(c) How to generalize.** This is the **"author against intent, conform to reality" pattern** and we
should build it into the engine as a first-class seam:
- Author every scene's motion against a clean nominal grid (round scene boundaries, comfortable beats). Keep the choreography readable.
- Capture the *real* per-scene window from the VO timing (whisper word-level transcript → per-scene start/duration). That produces `NS`/`ND`.
- Run all authored tweens through a position+duration remap proxy keyed by scene. The author never re-times anything when the VO changes; re-running TTS just regenerates `NS`/`ND`.
- **Keep cross-scene/global elements (transitions, tickers, grain) OFF the remap** — they're authored in real time.
- Generalization beyond linear: the remap is currently a single linear scale per scene. For finer VO-lock (e.g. land a word reveal on a specific spoken word) you can swap `RT` for a piecewise map keyed on word timestamps. The architecture — intercept the position arg, remap, forward — is unchanged.

---

## 3. The transition library — overlapping-the-seam tweens on the REAL timeline + explicit boundary map

**(a) What it does.** Between every adjacent pair of scenes there is a designed transition that
**overlaps the cut**, so there is never a static frame sitting between two scenes. Four transition
"verbs" plus a reusable green spray swipe. The choice of verb per boundary is data — an explicit
boundary map — not hardcoded into the scenes.

**(b) The exact mechanism** (lines 2293–2356):
- Authored on `tlReal` directly with **real new-timeline times** (the comment at 2293–2298 is explicit: NOT remapped), each transition reads its boundary start from `NS[b]` so it lands exactly on the incoming scene's start, overlapping the outgoing tail.
- `greenSwipe(t, dur)` (2303–2306): a `--spray` bar (`.tx-swipe`, CSS at 1110–1123) sweeps `xPercent -170 → 270` across the seam with `immediateRender:false`, then a hard `set(...{opacity:0})` "kill" at `t+dur` so it can't bleed into later frames.
- `txPush(b)` (2309–2314): outgoing `#s{b}` slides `xPercent 0→-106`, incoming `#s{b+1}` slides `106→0` over 0.34 s, green swipe covers the seam. Conveyor/news feel.
- `txTile(b)` (2316–2321): outgoing lifts+fades (`yPercent 0→-10, opacity 1→0`), incoming tiles up (`yPercent 12→0, opacity 0→1`), plus swipe.
- `txWhip(b)` (2323–2330): fast short whip-push (`xPercent 0→-34` / `44→0`) into a white `.tx-flash` overlay that pulses opacity 0→1→0 with a hard kill at `t+0.35`.
- `txCut(b, glitch)` (2333–2346): near-instant `.tx-paper` flash (`steps(1)` opacity to 0.92 for one frame, then out + hard kill) masking a hard cut; optional stepped RGB-split shudder on the incoming scene.
- **The explicit boundary map** (lines 2348–2356) — the part that makes it composable:
  ```
  txWhip(1)        // 1→2  whip into the title
  txPush(2)        // 2→3  conveyor push
  txCut(3,false)   // 3→4  hard cut on "no one says out loud"
  txPush(4)        // 4→5  match-cut push into the quote cards
  txTile(5)        // 5→6  cards out, S6 grid tiles in
  txCut(6,true)    // 6→7  cut with a quick RGB-split glitch
  txPush(7)        // 7→8  energy-lift push
  txWhip(8)        // 8→9  whip into the outro
  ```
  Each verb is chosen to match the *content* of the cut (whip into a title reveal; hard cut on a
  rhetorical "no one says out loud"; tile when one card layout replaces another).
- The "hard kill" `set` after every overlay tween is a determinism guard — under frame-seek a tween that fades an overlay to 0 must be followed by an explicit `set(0)` so a seek landing past it can't leave the overlay partially visible.

**(c) How to generalize.** Two reusable ideas:
1. **A small library of transition verbs**, each authored as "overlap the seam by ~0.2–0.4 s, with a hard kill on every overlay." Verbs are parameterized by boundary index and read the real boundary time from the VO map. New verbs slot in the same way.
2. **A boundary map as data** — a per-video list `[verb, boundary, opts]` that the engine fills in by matching cut content to verb semantics. The scenes stay ignorant of their neighbors; the transition layer owns the seams. Critically these live on the real timeline, never the remap proxy. This is the opposite of "pick one transition from an enum and apply it everywhere."

---

## 4. The continuous news TICKER through-line

**(a) What it does.** A thin monospace band scrolls leftward across the **top margin of every scene
for the entire runtime** — one unbroken element that gives the whole piece a "live field report /
news conveyor" identity and visually binds the 9 scenes into one broadcast.

**(b) The exact mechanism.** Markup: a single `.ticker-band > .ticker-track` placed once at root
level (line 1544), OUTSIDE any scene so it survives every cut. CSS at 1140–1189: fixed top band,
`--spray` left edge accent, mono 13px, the track is `display:inline-flex; white-space:nowrap`. The
`ticker()` IIFE (2362–2372) builds the content deterministically from a `labels` array (one per
scene), duplicates the unit string `unit + unit` ("2× for a seamless leftward loop"), and animates the
track `xPercent: 0 → -50` over `duration: 85` (the full runtime) with `ease:"none"` **on `tlReal`**
(not the remap proxy) so it scrolls continuously and never freezes between scenes.

**(c) How to generalize.** A **persistent through-line element** is a cheap, high-impact way to make a
multi-scene video feel authored rather than assembled. Pattern: one element at root scope (outside the
scene containers so transitions don't touch it), content built by duplicating a unit for a seamless
loop, a single whole-runtime linear tween on the real timeline. Reskin per video — a ticker for an
editorial piece, a progress rail, a recurring watermark/wordmark, a timecode. The rule: it lives above
the scenes, spans `data-duration`, and is authored in real time.

---

## 5. "Texture is alive" — grain drift + breathe + scale-pulse for the whole runtime; flickering reg-ticks

**(a) What it does.** Static frames never feel dead because two always-on textures move for the entire
runtime: the procedural grain layer slowly drifts, breathes its opacity, and scale-pulses; and the
registration-tick glyphs in each scene corner flicker on a stepped pattern. This is DESIGN.md motion
principle #7 ("Texture is alive — speckle drifts/breathes subtly so static frames never feel dead").

**(b) The exact mechanism.**
- Grain layer: `.grain` (CSS 121–132) is an oversized `inset:-220px` SVG `feTurbulence` rect at `opacity:0.07; mix-blend-mode:multiply`. Three simultaneous lifelong tweens at lines 1898–1909:
  - drift `x:70, y:-45` over `duration:240, ease:"none"` (1899) — slow translation.
  - opacity breathe `→0.105` over 3.6 s, `yoyo:true, repeat: Math.ceil(240/3.6)` (1900–1904).
  - scale breathe `→1.035` over 5.2 s, yoyo, `repeat: Math.ceil(240/5.2)`, `transformOrigin:"50% 50%"` (1905–1909).
  - (Note these durations are in *old-grid* seconds, run through the `tl` remap proxy, so they conform to the 85 s runtime like everything else.)
- Reg-ticks flicker (1911–1931): for each `.reg-ticks`, a `keyframes` opacity sequence (0.55/0.92/0.66/1.0/0.5/0.85 with irregular sub-frame durations) under `ease:"steps(1)"`, repeated across the runtime, **phase-offset per scene** `phase=(i%6)*0.21` so they don't pulse in unison. Stepped easing = deterministic hard-cut flicker that's bulletproof under frame-seek.

**(c) How to generalize.** **Bake at least one always-on micro-motion that spans the entire runtime.**
The cheapest is a single grain/noise overlay with slow drift + a yoyo opacity/scale breathe. Layer a
second, faster, *stepped* flicker on small decorative marks, phase-offset so they don't sync. Rules
that make it safe and good: keep it subtle (single-digit % opacity, ~3–5% scale), use `ease:"none"`
or `ease:"steps(1)"` (never wall-clock), and let it run under the remap proxy so it conforms to
runtime. The goal is that **pausing on any frame still looks alive.**

---

## 6. Simulated-interaction beats — BESPOKE per-scene GSAP, not a fixed effect set

**(a) What it does.** Each scene contains a little hand-built "the product is happening to you"
interaction that dramatizes that scene's specific argument. They are explicitly **not** a shared
library — each is its own IIFE authored against that scene's DOM and message. The header comment calls
them "SIMULATED-INTERACTION MOTION … All decorative; transforms + color/opacity only; seek-safe"
(lines 845–848, 1936–1939).

**(b) The exact mechanism** — each is a self-contained IIFE on the `tl` (remapped) proxy:
- **S1 count-up + bell** (`s1Interaction`, 1947–2012): a notification badge pops (`back.out(2.6)`), the bell rings via a decaying-swing `keyframes` rotation (Lottie-style, built in GSAP), a number counts `0→4` driving `slab.textContent` through an `onUpdate` with `snap:{v:1}` for integer ticks, and the bell **re-rings at [5.5, 9.5, 13.5]** to fill the hold and reinforce "you'll unlock 96× a day."
- **S3 orbit + calendar-crumble** (`s3Interaction`, 2026–2107): stat counters count up with snap + landing pop; an icon ring is built procedurally (5 SVG glyph nodes placed by trig on a circle, lines 2063–2073) that rotates `360°` over 26 s while inner icons **counter-rotate** `-360°` to stay upright; a 6×6 calendar grid is generated (36 cells, 2085–2089), fills green cell-by-cell (`39.2 + i*0.13`), then **crumbles** — each cell scatters with deterministic pseudo-random offsets derived from its index (`(i*53)%100`, `(i*31)%100`, `(i*47)%160`) and falls/fades, "disintegrating into grain."
- **S4 infinite-feed + fake cursor + slot-reels + SCROLL stack** (`s4Interaction`, 2110–2180): the feed is cloned up to 34 rows (2114–2120, `stripIds` on clones to avoid duplicate `data-hf-id`) and scrolls `y:0→-dist` over 30 s; a fake cursor enters then does **7 periodic flick gestures** (`62.2 + k*3.6`); the refresh spinner spins `360*9` like a reel then idles; glyph-chips are rebuilt into **slot reels** (a `.reel` of 8 random-pool icons + the landing slot, rolled with `back.out(1.05)`, 2142–2155); the "SCROLL" column fills outline→solid **accelerating** (`gap *= 0.72`, 2161–2167) then keeps a staggered vertical wave so the dominant side never freezes; chips periodically "ding" (2176–2179).
- **S6 reel-grid + RGB-split glitch** (`s6Interaction`, 2199–2238): a 96-cell highlight-reel grid scrolls behind (2202–2208); "NOT ENOUGH" glitches via **stepped `set`s** of red/cyan ghost layers (`flick` array of x/y/opacity states, 2219–2229) — the comment notes stepped `set`s are "bulletproof under frame-seek" — then the ghosts converge and the solid word resolves with a final shudder.
- **S8 grayscale-drain** (`s8Interaction`, 2249–2281): wellbeing checklist checks **draw themselves on** in sequence (`strokeDashoffset 1→0` per circle+path, staggered), and the phone mock drains color→grayscale via an `onUpdate` writing `phone.style.filter = "grayscale(g) saturate(1-0.85g)"` over 12 s.
- Plus quieter sustain beats: S2 idle icon bob (2018–2023), S5 ∞-watermark slow turn + card parallax + late re-stamp (2182–2196), S7 "DESIGNED" breathe + strike re-pulse (2240–2246), S9 outro breathe (2283–2288).

**(c) How to generalize.** The principle is **"the motion IS the argument."** S4's scene is *about*
the addictive feed, so the scene literally simulates an infinite feed + slot machine + restless cursor.
S3 is about *time lost*, so a calendar fills and then disintegrates. Do not reach for a generic
"fade-in card" — ask "what is this scene claiming, and what interaction embodies that claim?" then
build it bespoke from primitives (count-ups via `onUpdate`+`snap`, self-drawing strokes via
`strokeDashoffset`, procedural placement via trig, cloned/scrolling lists, stepped glitches). Hard
constraints that keep them seek-safe: **transforms + color/opacity only, deterministic index-derived
pseudo-randomness (never `Math.random`), `onUpdate` for any text/filter mutation, and a continued
low-amplitude motion through any long hold so the frame never freezes.** A closed effect-enum cannot
produce these — they only exist because someone authored to the content.

---

## 7. Procedural / deterministic assets — filters, grain, and "Lottie" built in GSAP/SVG

**(a) What it does.** The signature texture and accent animations are generated **in-engine**, not
sourced as files. The halftone/dither look, the rough spray edge, the grain, and the "Lottie" bell and
checkmarks are all SVG/GSAP. `assets/lottie/` is **empty** (verified) — there are zero `.json` Lottie
files; the bell-ring and check-draw are hand-built.

**(b) The exact mechanism.**
- **`#spray-rough`** filter (1261–1264): `feTurbulence` (fractalNoise, `baseFrequency 0.9`, `seed 7`) → `feDisplacementMap scale=2.5` on SourceGraphic. Applied to hero type (`.lead { filter: url(#spray-rough) }`, line 202) and hand-drawn accents (786) for the rough spray-paint edge. Fixed `seed` = deterministic.
- **`#halftone`** filter (1266–1284): a full 1-bit dither pipeline entirely in SVG primitives — luminance `feColorMatrix` → contrast `feComponentTransfer` → `feTurbulence` noise → composite the noise into the image (`feComposite` arithmetic) → posterize to 1-bit (`feFuncR/G/B type="discrete" tableValues="0 1"`) → recolor inked pixels to `--ink`, paper to transparent (final `feColorMatrix`). Applied to the S2 portrait (`.portrait-img { filter: url(#halftone) }`, line 424). This reproduces the DESIGN.md "1-bit / halftone-dither B&W cutout" treatment with no image-processing step.
- **Grain** (`#grain-noise`, 1532–1536): `feTurbulence` fractalNoise `seed 11` desaturated, painted into the `.grain` rect — the procedural speckle from technique #5.
- **"Lottie" animations built in GSAP/SVG:** the bell is an inline SVG path swung by a GSAP `keyframes` rotation with decay (1977–1992); the checkmarks are inline SVG circle+path drawn on via `strokeDashoffset` (2249–2261); the self-drawing accents/underlines/strikes/signature flourish are `.accent-draw` paths with `pathLength="1"` drawn via `strokeDashoffset 1→0` at a `data-draw` time (1871–1880). The comment at 1976 is explicit: "Lottie-style, built in GSAP/SVG."
- Determinism throughout: every `feTurbulence` has a fixed `seed`; every "random" scatter is derived from element index arithmetic. Nothing samples the clock or RNG.

**(c) How to generalize.** **Prefer procedural, deterministic, in-engine assets over sourced files**
for texture and accent motion:
- Texture treatments (halftone, dither, grain, rough edges, paper) → SVG filter chains with a fixed `seed`. They scale to any resolution, cost nothing to "license," and render byte-stable.
- Icon/accent micro-animations (bells, checks, draws, underlines) → inline SVG + GSAP (`keyframes` for physical swings, `strokeDashoffset` for self-draws). You do not need a Lottie runtime or `.json` files for these; the empty `assets/lottie/` proves the bar is reachable without them.
- Reserve real sourced assets for genuinely photographic content (the one portrait), and even then run it through an in-engine filter so it matches the deterministic look.

---

## 8. The audio model — alternating-track VO, trimmed bed, low SFX, burned-in synced captions

**(a) What it does.** A layered, deterministic audio mix: one VO clip per scene, a continuous music
bed, sparse SFX hits on transition beats, and whisper-synced burned-in captions. The track-index
choices are deliberate to let adjacent VO **overlap during transitions**.

**(b) The exact mechanism** (lines 1546–1635):
- **Per-scene VO on alternating track indices** (1547–1555): `s1.wav … s9.wav`, each `data-start` set to the scene's new VO start (`NS`), and `data-track-index` alternating **9, 10, 9, 10, …**. The alternation matters: because adjacent scenes are on different audio tracks, scene *n*'s VO tail can overlap scene *n+1*'s VO head during the seam without one cutting the other off (mirrors the visual seam overlap in #3).
- **Pre-trimmed music bed** (1558): one `music.mp3`, `data-start=0 data-duration=85`, on its own `track-index:8`. The comment notes it goes "dark → hopeful at the S8 pivot ~67s, sidechain-ducked under VO" — i.e. the duck/mix is baked into the asset, not computed live.
- **Low-volume SFX on their own track** (1561–1570): notification, whooshes, slot-machine payout, glitch, stamp, piano-logo — each `data-track-index:7`, `data-volume` 0.38–0.5, placed exactly on transition/interaction beats (e.g. whoosh at 5.867 = the S1→S2 whip; slot payout at 20.6 = S4 reels; glitch at 49.5 = S6). SFX reinforce the bespoke interactions of #6.
- **Burned-in whisper-synced captions** (1572–1635): a `.vo-cap-layer` of ~60 `.vo-cap clip` divs, each one phrase with its own `data-start`/`data-duration` from the word-level transcript, all on `data-track-index:2`. Styled as a lower-third mono card (CSS 1083–1107), with a `.vo-cap-low` variant (1104–1107) that drops below the S3 calendar so captions never collide with scene content.

**(c) How to generalize.** The reusable audio architecture:
- **One VO clip per scene, alternating track indices**, started at the real VO scene boundaries (`NS`). Alternation is what lets transitions cross-fade VO instead of hard-cutting it.
- **A single bed on its own track** spanning `data-duration`; bake the emotional arc and the VO-duck into the rendered asset rather than relying on live sidechain.
- **SFX as a thin accent track** (own index, low volume) placed on the same beats as the visual transitions/interactions — audio and motion hit together.
- **Captions as `.clip` divs from the word-level transcript**, lower-third, with content-aware position variants so they dodge busy scene regions. Because they're `.clip` elements on the timeline they're as seek-deterministic as everything else.

---

## 9. The determinism rules and the HyperFrames key rules (from AGENTS.md)

These are the non-negotiable constraints that make techniques 1–8 actually render. They come straight
from `AGENTS.md` "Key Rules" and are obeyed everywhere in the file.

**(a) Determinism (AGENTS.md rule 6):** "Only deterministic logic — no `Date.now()`, no
`Math.random()`, no network fetches." Verified throughout: every `feTurbulence` has a fixed `seed`
(7, 11), every scatter/flicker is derived from element index arithmetic (`(i*53)%100`, `(i%6)*0.21`,
etc.), all timing is literal. Why it matters: the renderer seeks to arbitrary frames, possibly out of
order and in parallel — any clock or RNG read would make frame *t* depend on *when* it rendered, not
*where* in the timeline, producing flicker/non-reproducibility.

**(b) The HyperFrames key rules (AGENTS.md), as used here:**
1. Every timed element has `data-start`, `data-duration`, `data-track-index` (scenes, captions, audio).
2. Timed elements **must** carry `class="clip"` — the framework uses it for visibility control (e.g. line 1289). Forgetting `clip` = the element won't be shown/hidden on its window.
3. Timelines must be **paused** and registered on `window.__timelines["<composition-id>"]` (1668, 2374).
4. Videos use `muted` + a separate `<audio>` for sound (here audio is all separate `<audio>` clips).
5. Sub-compositions referenced via `data-composition-src` (not used here — single file — but part of the contract).
6. Deterministic logic only (see (a)).

**(c) Plus the local determinism guards the author added on top:**
- **"Hard kill" `set`s** after every transition/overlay fade (e.g. 2305, 2329, 2337) so a seek landing past a fade can't leave an overlay partially visible.
- **`stripIds()`** (1940–1944) removes `data-hf-id` from cloned nodes (feed rows, slot reels) so cloning never produces duplicate framework IDs.
- **`immediateRender:false`** on `fromTo`/`to` overlays that must not paint their start-state before their beat (2262, 2304, 2327…).
- Killing leftover filter bleed at cuts (e.g. zero the halftone portrait opacity at 32, lines 1814–1817) — a CSS filter can paint through the runtime's `visibility:hidden`, so the author explicitly `set` opacity 0 at the cut.

**(c→generalize.)** Bake these into the engine as invariants the emitter cannot violate: never emit
`Date.now`/`Math.random`/`fetch`; always emit `.clip` + the three timing attrs on timed nodes; always
pause + register the timeline; always follow a fade-to-0 overlay with a hard `set(0)`; always strip
framework IDs from clones; always seed every procedural filter. These are cheap to check
mechanically and they are exactly the things whose absence produces flicker, desync, or non-repro.

---

## ANTI-PATTERNS WE ARE FIXING

The reference is good for reasons that are the **direct inverse** of how a naive multi-agent pipeline
tends to produce video. Naming them so we don't drift back:

**1. The closed effect-enum.** A pipeline that offers each scene a fixed menu — `["fade", "slide",
"zoom", "xfade"]` — can only ever assemble; it can never author. Nothing in this reference comes from
such a menu. The S3 calendar fills-then-disintegrates, the S4 feed *is* a slot machine, the S6 word
RGB-splits, the S8 phone drains to grayscale — each is **bespoke GSAP written against that scene's
specific argument** (#6). The fix: scenes get *primitives* (count-up, self-draw, procedural placement,
stepped glitch, cloned-scroll) and a brief to embody their message, not a dropdown of canned effects.
The transition verbs (#3) *look* like an enum but aren't — they're a small set chosen per boundary to
match cut content, authored to overlap the seam, and freely extended.

**2. Specs passed between agents instead of one shared timeline.** When each stage hands the next a
serialized "spec" (JSON of scenes, durations, effect names), every stage is blind to the others'
realities — the writer's timing, the designer's layout, the editor's seams never co-exist in one
artifact, so nobody can see dead air, collisions, or desync. This reference is the opposite: **one
file, one `tlReal`, all motion/audio/captions/transitions/texture co-authored on a single seekable
timeline** (#1). The re-timer (#2) only works *because* there is one timeline to remap onto; the seam
overlaps (#3) and alternating-track VO (#8) only work *because* transitions and audio share the same
time axis as the scenes. The fix: agents collaborate by editing **one composition**, not by relaying
specs — the timeline is the shared source of truth, and "looks alive on every frame / no dead air /
captions dodge content" become inspectable properties of that one artifact.

**3. Never looking at frames.** Flat output is the signature of a pipeline that emits and ships without
ever sampling the rendered result. The author of this reference clearly *paused on frames* — that is
the only way you discover: a long VO hold leaves a frozen frame (→ add re-rings, drifts, breathes,
waves, #5/#6); a halftone filter bleeds through `visibility:hidden` into the next scene (→ zero opacity
at the cut, 1814–1817); decorative accents swallow the adjacent word-space (→ hard `margin` gap,
210–217); a strike overshoots its box (→ `data-layout-allow-overlap`, 1869); captions collide with the
S3 calendar (→ `.vo-cap-low`, 1104–1107). **None of these are findable from a spec — only from
looking.** The fix: the engine must render and *look at* frames (or whole scenes) as part of the loop,
and treat "is this frame alive / legible / un-collided?" as a gate, the same way our eval/coach loop
already inspects output.

**The throughline:** quality here comes from **authoring against real content on one deterministic,
seekable timeline, and looking at the result** — not from selecting effects, not from relaying specs,
not from trusting the emit. Every technique above is a concrete, transferable mechanism for doing that.
