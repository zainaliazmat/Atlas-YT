# Dark Truth Behind the Social Media — DESIGN.md

**Format:** 1920×1080, 16:9, ~240-second (4:00) editorial motion-graphics video.
**Tech:** HyperFrames composition (HTML + GSAP, Tailwind v4 browser runtime, Lottie adapter for accent animations). **No React, no build step.**

This document is the single source of truth for all colors, type, texture, and motion.

---

## §2 — Visual Identity (the exact tokens)

Extracted from the reference poster + the two motion clips.

**Color tokens**
```
--paper        #F2EED6   /* warm cream background (dominant) */
--paper-shade  #E4E0C8   /* slightly darker cream for texture depth */
--ink          #1F1F1E   /* near-black, primary text */
--charcoal     #3A3A39   /* secondary text / halftone mids */
--spray        #2E5E1F   /* PRIMARY accent — deep forest spray-green */
--spray-deep   #1A5614   /* darker green for shadows/outlines */
--lime         #8FB573   /* highlight/glow accent (borrowed from clips) — use sparingly */
--paper-line   #C9C5AE   /* hairlines, dividers, mono labels */
```

**Type system** (all free on Google Fonts)
| Role | Font | Use |
|---|---|---|
| Hero grunge display | **Rubik Spray Paint** (fallback: Anton + grain mask) | "DARK TRUTH / BEHIND THE" |
| Secondary slab | **Roboto Slab** Black (or Zilla Slab) | "SOCIAL MEDIA", big numbers |
| Mono / metadata labels | **Space Mono** | vertical tags ("AR/17—20/.0610"), captions, UI |
| Tweet/quote-card body | **Inter** (system sans) | quote cards |
| Signature flourish | **Sacramento** (or render as SVG) | "Royale"-style sign-off |

**Texture & treatment rules**
- Background = `--paper` + a **grunge speckle/noise layer** (SVG `feTurbulence`, ~6–9% opacity, multiply blend). Claude Code can generate this procedurally — no asset hunt needed.
- All photographic imagery → **1-bit / halftone-dither, black & white**, then a hand-drawn `--spray` outline (the cutout look).
- Hero words → spray-paint texture with rough edges + faint over-spray bleed.
- Accent marks: hand-drawn green underlines, circles, arrows, "x" scribbles.
- Decorative metadata: tiny mono tags, registration ticks, a fake legal/credit block, a small line-art glyph (broken-doc / sad-face).

**Motion principles** (distilled from your two clips → reskinned to grunge)
1. **Kinetic typography** — reveal word-by-word; one **hero word** in `--spray`, the rest `--ink`.
2. **Orbiting clusters** — icons orbit a center point (clip 1's "juggling too many tools").
3. **Parallax cards** — quote/UI cards float in 3D with depth + drop shadow.
4. **Stacked repeat reveal** — a word repeats up a column, outline → solid (clip 2's "GROWTH ×6"). We use it on **"SCROLL"**.
5. **Counter ticks** — numbers count up fast then settle (stats).
6. **Shader/whip transitions** — `flash-through-white`, grain-dissolve between acts.
7. **Texture is alive** — speckle drifts/breathes subtly so static frames never feel dead.

---
