# CREATIVE_UPGRADES_SPEC.md — implementable craft spec

> The "what to build" for §5. Authored by the owner (main thread) from explainer-craft fundamentals + the verified system internals. Everything here is deterministic and renderable with the existing HTML/CSS/SVG/GSAP stack. Implementation digests are folded into `CREATIVE_UPGRADES.md`.

## Craft principles distilled (Vox / Johnny Harris / Kurzgesagt / editorial motion)

1. **One idea per scene, stated as a short on-screen label — not the whole narration.** The screen reinforces a *phrase* ("Wrong question.", "Coffee → 95mg"); the voice carries the sentence. (Mason already prefers `on_screen_text` for the heading after the §4 fix; the caption is now scrimmed.)
2. **Big numbers are the hero.** A single giant stat fills the frame. This is the most-stolen Vox device and we have no layout for it → add `big-number`.
3. **Show change over time / sequence as a drawn line.** History and process read as a timeline with nodes → add `timeline`.
4. **Color is signal, not decoration.** One accent for "the answer", one for "the wrong thing", `#FFD000` held for the single turn beat. The existing style guide already does this well — teach Iris to keep doing it and never add a 4th signal color.
5. **Motion is restraint.** 1–2 deliberate moves per scene; the highlighter beat is the climax. A count-up on the hero number is worth more than five fades → optional `count-up` effect.
6. **Type carries the brand.** A real editorial display face is the single biggest "looks designed vs templated" lever. The current guide names **GT Sectra (proprietary, never bundled)** → replace with a bundled OFL face.
7. **Data is drawn, not photographed.** Native SVG bars (added in §4 C5) beat stock charts. Keep pushing data scenes to `data-chart`/`big-number`, never to a sourced photo.

## Upgrade 1 — Typography: bundle OFL fonts (HIGHEST visual impact)

Replace proprietary GT Sectra with open-licensed faces, bundled locally (HyperFrames forbids render-time font fetch). All three are **SIL OFL 1.1**, available from Google Fonts (github.com/google/fonts, static TTFs):

| Role | Family | License | Why |
|---|---|---|---|
| Display / headings | **Fraunces** (72pt opt; or Fraunces variable static cut) | OFL 1.1 | Wonky old-style editorial serif — the Vox/long-form-magazine feel. |
| Body / captions / labels | **Inter** | OFL 1.1 | Already named in the guide; neutral, legible at caption size. |
| Mono / data callouts (optional) | **JetBrains Mono** | OFL 1.1 | Tabular figures for stats/code. |

**Mechanism for Mason:** bundle the `.ttf` files in the repo (e.g. `composition-engineer/fonts/`), and at scene-build time copy them into the scene project's `assets/fonts/` (reuse the existing `_copy_asset_local` localizing pattern), then emit a deterministic `@font-face { font-family:'Fraunces'; src:url('assets/fonts/Fraunces.ttf'); }` block in the scene CSS. No network, frame-seek deterministic. Iris's `typography.display.family` becomes `"Fraunces"` (or the chosen OFL display) so the C1-fixed font resolver feeds the real bundled name into CSS. Add a test: emitted HTML contains an `@font-face` whose `src` is a local `assets/fonts/...` path (no `http`).

## Upgrade 2 — Vocabulary extension (LOCKSTEP, small & high-value)

Within-scene tokens only (cross-scene transitions are metadata-only today — not worth a new transition token). Each new token requires the **four-way lockstep**: contract enum + Iris specifier + Mason renderer + a test.

### `big-number` (LAYOUT)
- **Renders:** one giant display-font number/stat centered, a short label above/below, optional unit. Pure CSS/HTML, no JS. The number may carry the `#FFD000` highlight when it's the signature beat.
- **Lockstep:** add `"big-number"` to LAYOUTS in `atlas/contracts/style_guide.schema.json` + `storyboard.schema.json` enums, `art-director/art_engine.py` LAYOUTS set, `composition-engineer/composition_engine.py` LAYOUTS + a `_layout_big_number` renderer; test: a big-number scene emits the stat at hero scale and self-scans clean.
- **Iris chooses it when:** a scene's point is a single dominant statistic.

### `timeline` (LAYOUT)
- **Renders:** a horizontal SVG baseline with evenly-spaced nodes + date/label text per node (parsed from `on_screen_text`/shot content, like `parse_chart_data`). Deterministic inline SVG, no animation required (optional staggered build-time reveal within motion budget).
- **Lockstep:** same four files; `_layout_timeline` renderer; test: emits `<svg>` with N nodes for N parsed entries, self-scans clean.
- **Iris chooses it when:** the scene is chronological / a process with ordered steps. Exercises the history/process code path.

### `count-up` (EFFECT, optional — ship only if frame-deterministic)
- **Renders:** the hero number tweens 0→target via a GSAP tween on the paused master timeline (`onUpdate` writes `textContent`). HyperFrames seeks to fixed times, so the value at each seeked frame is determined → deterministic. **Implementer must verify** it passes the self-scan (no `Math.random`/`Date.now`, no late `gsap.set`, motion lives on the master timeline) and the inspect motion-assertion; if it can't be made clean, DROP it and keep the two layouts.
- **Lockstep (if shipped):** EFFECTS enum in both contracts + Iris + Mason `count-up` partial + test (deterministic value at a sampled frame).

> Keep it to these. Better 2–3 perfect tokens than a broken transition.

## Upgrade 3 — Teach the fleet (permanent, in the right files)

Concise additions (the implementer writes the actual prose, in each agent's voice):

- **Iris** (`art-director/soul/STYLE.md` + `SKILL.md`): layout-selection heuristics (single dominant stat → `big-number`; ordered/chronological → `timeline`; magnitude comparison → `data-chart`; head-to-head → `comparison-2up`); "the screen says the phrase, the voice says the sentence" (short `on_screen_text`, never the full narration); signal-color discipline (max 2 functional colors + the reserved `#FFD000`, never a 4th); the `#FFD000` beat lands on the single turn/tension scene; motion budget = 1–2 intentional moves, the highlighter is the climax; specify the bundled OFL display font, never a proprietary one.
- **Marlow** (`scriptwriter/soul/STYLE.md` + `SKILL.md`): cold-open hook that reframes or contradicts; one idea per scene; a mid-video "turn"; surface concrete numbers so Iris can build `big-number`/`data-chart`; vary cadence (short punch after a long line); end on a viewer-directed button.
- **Cadence** (`audio-designer/soul/STYLE.md` or `SOUL.md` + `SKILL.md`): match bed mood to topic; VO authoritative at 0 dB, hard-duck the bed; one signature SFX on the cut into the `#FFD000` beat; let a beat of near-silence precede the turn.
- **Mason** (`composition-engineer/SKILL.md`): document the new `big-number`/`timeline`(/`count-up`) renderers, the local OFL `@font-face` bundling, the native `data-chart` SVG, and that contrast failures now block the auto-gate.

## Determinism guardrails (every change must hold)
No `Math.random`/`Date.now`/`new Date`/render-time fetch; all motion build-time on the one paused GSAP timeline; new fonts bundled locally (no fetch); closed-set stays an error-on-unknown set; Iris↔Mason token sets must remain byte-for-byte identical (the AST-parse lockstep test must stay green).
