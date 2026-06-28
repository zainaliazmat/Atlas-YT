# Clean Tech Explainer — DESIGN.md

**Format:** 1920×1080, 16:9, ~60–90 s explainer. **Tech:** HyperFrames (HTML + GSAP),
deterministic, single-file composition.

A deliberately different look from `dark-truth-social` — used to prove the Design Pack
abstraction generalizes. Where that pack is warm-paper grunge, this one is cool, bright,
and minimal. It shares the SAME motion mechanism (`transitions.js` / `ticker.js` /
`retimer.js` from `design-packs/_shared/`) — only the surface (tokens + base.css + filters)
changes.

---

## Visual identity

**Color tokens**
```
--bg          #ffffff   /* clean white canvas (dominant) */
--surface     #f6f8fb   /* faint panel / card fill */
--ink         #14171a   /* near-black primary text */
--muted       #5b6470   /* secondary text / labels */
--line        #e3e7ec   /* hairlines, grid, dividers */
--accent      #2f6df6   /* the single brand accent (blue) */
--accent-deep #1e4fd0   /* accent shadow / pressed */
```

**Type system** (all free on Google Fonts)
| Role | Font | Use |
|---|---|---|
| Heading | **Inter** 800 | titles, big statements |
| Subhead | **Inter** 700 | section heads, numbers |
| Mono labels | **Space Mono** | metadata, captions, UI ticks |
| Body | **Inter** 400 | supporting copy, cards |

(No grunge display, no signature script — intentionally a smaller role set than
`dark-truth-social`, to show packs may define different `type` maps.)

**Texture & treatment**
- Background = `--bg` with a **faint static grid** (`--line`), no grain, no halftone.
- Imagery / panels → clean cards with **soft drop shadows** (`#soft-shadow`), rounded
  corners, hairline borders. No 1-bit dither, no spray edges.
- Accent used sparingly: one `--accent` highlight per scene.

**Motion**
- Reuses the shared transition verbs, ticker, and the VO-lock re-timer unchanged.
- Calmer budget: at most **1** signature transition, texture is NOT always-on (the clean
  grid is static) — see `tokens.json` `motion.budget`.
