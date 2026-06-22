# SKILL — Mason's method: the Vox technique library + deterministic assembly

This is the **engine's method**, not Mason's voice (that lives in `soul/`). It is the
authored mapping from the Art Director's finite vocabulary to deterministic, gate-clean
HyperFrames partials. GSAP idioms here are sourced from the installed HyperFrames doc
skills (`.agents/skills/hyperframes*`: `references/css-patterns.md`, `techniques.md`,
`captions.md`, `transcript-guide.md`, `motion-principles.md`) — but the Vox token→partial
mapping is ours and lives only here. (Namespace hygiene: those are HeyGen's doc skills;
this is our separate technique library.)

## The contract Mason builds to (verified, HyperFrames v0.6.115)

Each scene is a **standalone single-composition project** under `scenes/scene-NN/`
(its own `index.html` + `hyperframes.json`), gated and draft-rendered independently.
Whole-video assembly is the separate `render_video` step.

Generator invariants (lint-enforced — Phase 0):
- The composition root `<div data-composition-id data-width data-height data-start
  data-duration>` is the **first body element**. All layers/overlays nest **inside** it.
- Any element with `data-start`/`data-duration` also carries `class="clip"`.
- Initial visibility is **CSS `opacity:0`**, never `gsap.set`.
- All motion lives on **one paused master timeline**, `const tl = gsap.timeline({paused:true})`,
  registered as `window.__timelines["scene-NN"]`. Build-time `.from()/.to()/.fromTo()` only.
- GSAP is loaded from the same CDN the engine's own scaffolds use.

## fps — two decoupled numbers (do not conflate)
- **Render fps** = `style_guide.fps` (default 30), passed to `render --fps`.
- **Stutter cadence** = the `stutter-12fps` EFFECT only: `steps(n)`, `n = round(12 · duration_sec)`.
  12 is **constant**. `steps(round(30·dur))` would produce no visible stutter — never tie
  `steps()` to render fps.

## Fonts — bundled OFL, local `@font-face` (no render-time fetch)
The display/body faces ship **in the repo** (`composition-engineer/fonts/*.ttf`, all SIL
OFL 1.1): **Fraunces** (display) + **Inter** (body), with **Noto Serif Display** / **Noto
Sans** as guaranteed-present fallbacks. HyperFrames forbids fetching a font at render time,
so nothing is loaded from Google Fonts or any CDN. At scene-build time the needed `.ttf` is
**copied into the scene project's `assets/fonts/`** (the same deterministic localizing
pattern as `_copy_asset_local`) and the scene CSS emits a deterministic
`@font-face { font-family:'Fraunces'; src:url('assets/fonts/Fraunces.ttf'); }` block with a
**LOCAL relative `url()` — never `http(s)`.** Iris's `typography.display.family` resolves
through `_font_family`/`BUNDLED_FONTS`; an **unbundled family name falls back** to a bundled
face (so a stray proprietary name can never break the cascade or trigger a fetch). The
emitted HTML's `@font-face src` is always a local `assets/fonts/...` path — assert it, never
an `http` URL.

## The four axes (one partial per token; an unknown token is rejected, not dropped)

**LAYOUTS** (`storyboard.scene.layout`, composition / where things sit) — **12 tokens**:
`centered-statement` · `split-screen` · `full-bleed-image` · `lower-third` ·
`data-chart` · `quote-card` · `map-focus` · `list-stack` · `comparison-2up` · `title-card` ·
`big-number` · `timeline`.
Each positions the title block and any media; default is `centered-statement`.

`big-number` is the **Vox big-number device** — one dominant statistic at hero scale
(`font-size:380px`, tabular figures), a short label, an optional unit. The stat is parsed
from `on_screen_text`/shot content (`parse_hero_stat`, the largest magnitude wins). Pure
CSS/HTML, no JS; carries `#FFD000` on the signature beat; the `count-up` effect animates
this same `.big-number-value`.

`timeline` is a horizontal **inline-`<svg>` baseline** with N evenly-spaced labelled nodes,
one per chronological/process entry parsed from `on_screen_text`/shot content
(`parse_timeline_data` — years like `1969`/`2007`, else `Step N`/`Phase N`). Deterministic,
no animation required.

`data-chart` is **native, drawn data — never a sourced photo of a chart.** The renderer
parses chart entries from the shot content/`on_screen_text` (like `parse_chart_data`) and
emits a build-time **inline `<svg>` bar chart** (axis line + a `<rect>`/value-label/x-label
per bar, `viewBox` + `preserveAspectRatio`). No JS, no animation required, deterministic at
every seeked frame. A `data-chart` scene therefore always renders a real visual, never bare
centered text, even when no asset file resolved.

**TEXTURES** (`style_guide.textures`, always-on global overlays — **static CSS**,
`mix-blend-mode`, absolute children inside the root, **no infinite CSS animation**):
`paper` (multiply) · `grain` (overlay) · `halftone` (multiply) · `vignette` (multiply) ·
`scanlines` (overlay). Baseline when absent: `paper`, `grain`.

**EFFECTS** (`storyboard.scene.effects`, per-scene motion on the paused timeline):
- `stutter-12fps` — a stepped tick: `fromTo(scaleX 0→1, ease:"steps(round(12·dur))")`.
- `stepped-ease` — stepped easing on a small title nudge: `ease:"steps(6)"`.
- `highlighter-FFD000` — the **signature beat** (one scene only): a `#FFD000` sweep
  behind the title, `fromTo(scaleX 0→1, transform-origin:left)`. The one beat Mason
  hand-tunes; never trimmed off its scene.
- `map-draw` — SVG `stroke-dashoffset` 1→0 on a `pathLength="1"` path (`.to`).
- `chromatic-aberration` — restrained **static** RGB split via `text-shadow` (no animated
  SVG filter).
- `push-in` — slow `scale` on a still (`ease:"none"`).
- `parallax` — layered `yPercent` drift (`ease:"none"`).
- `count-up` — the hero number tweens 0→`data-target` on the paused master timeline; an
  `onUpdate` writes the rounded value into `.big-number-value` `textContent`. The target is
  read at **build time**; no `Math.random`/`Date.now`/late `gsap.set` — frame-deterministic
  (the engine seeks to fixed times, so each frame's value is determined). Pairs with
  `big-number`.

**TRANSITIONS** (`storyboard.scene.transition`) apply at the **`render_video` assembly
step**, between two scenes — never inside a scene body (bodies are transition-clean):
`cut`/`match-cut` → hard concat; `dip-to-black` → xfade `fadeblack`; `push` → `slideleft`;
`wipe` → `wipeleft`.

## Motion budget
`(non-cut transition + effects) ≤ style_guide.motion.max_per_scene` (default 2). Trim
extra effects from the tail; the mandatory `highlighter-FFD000` on the signature scene
is kept first and never trimmed.

## Captions (distinct from `script.on_screen_text`)
From `narration.transcript.json` segments. Segment `start_sec`/`end_sec` are **global**
(cumulative across the video); offset to each scene's **local** timeline by subtracting
the scene's first-segment start. Each caption is a native `<div class="caption clip"
data-start data-duration>` (local timing) — placed at **build time**, never late/async.

## Determinism strategy: author → self-scan → lint → validate → inspect → render
HyperFrames `lint` already catches the JS determinism trio (`Math.random`, `Date.now`,
`repeat:-1`) — rely on it. Mason's own pre-render **self-scan** owns exactly the three
rules `lint` misses:
1. **render-time fetch / network** (`fetch(`, `XMLHttpRequest`, `WebSocket`, …),
2. **animated SVG filters** (SMIL `<animate*>`),
3. **late/async `gsap.set`** (state mutated outside build time).
The self-scan runs first (pure Python); then the CLI gate `lint → validate → inspect`.
**No render until self-scan + lint + validate + inspect all pass.** Draft renders use
`--quality draft --format mp4 --strict`; no `--docker` (per-machine determinism is the
draft standard — byte-identical cross-machine is a final-render concern).

`inspect --strict` runs with a `*.motion.json` sidecar on the signature-beat scene and
any scene carrying `map-draw` or `highlighter-FFD000`, to machine-verify the sweep /
draw endpoints.

## Assets
Every `asset_manifest` URI must be local. An `http(s)://` URI is a **hard input-validation
block** (a remote 404 silently ships a broken, non-reproducible MP4). A missing local file
becomes a deterministic **styled placeholder panel** (no fetch) so the scene still composes;
the scene records an integrity flag (distinguishing a Magpie-declared `placeholder` status,
which is expected, from a `sourced`/`cleared` asset whose file is missing — surfaced to the
human gate). WCAG contrast failures from `validate` now **BLOCK the auto-gate** — a
contrast defect is a legibility failure, not a number to count and pass. A scene whose
text fails contrast does not clear the gate until the palette is fixed.

## The two jobs
- **`compose_scenes`** (pre-gate): 5 artifacts → per-scene HTML → self-scan + auto-gate →
  per-scene draft renders → `composition_manifest.json` + an `"auto-gate PASS"` summary.
- **`render_video`** (post-gate): final assembly — concat per-scene renders + storyboard
  transitions at boundaries + narration mux at final quality → the deliverable.
