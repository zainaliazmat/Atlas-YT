# studio CHANGELOG

## 2026-06-28 — Compose recipe book (Plan 2): bespoke per-scene archetype library

- compose is now a per-scene **archetype director**: 13 bespoke, deterministic archetype
  builders (big-number, list-stack, data-chart, comparison-2up, centered-statement,
  full-bleed-image, title-card, lower-third, split-screen, map-focus, timeline, diagram,
  quote-card) dispatched by the Iris storyboard tag (heuristic classify() fallback).
- Each archetype ships its `motion_variety` beat-token in `gate/parse.py::_BEAT_TOKENS` in the
  same commit (the CEO-mandated parity invariant), enforced by
  `test_archetype_token_parity.py`.
- Render-correct dispatch: builder `beats_js` is emitted into the executable `<script>`
  choreography and anchored at the scene's authored start; builder modules auto-register on
  package import.
- Measured on `dark-truth-v2` (compose-only): with a diverse storyboard the 9 scenes yield 9/9
  distinct beat signatures; `motion_variety` 0.37 → 5.0 (passed), `content_fidelity` 5.0
  (passed). Remaining BLOCKED dims (motion_energy/dead_air/audio) require an mp4 render.
