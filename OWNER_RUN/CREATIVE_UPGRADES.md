# CREATIVE_UPGRADES.md — what the fleet learned and gained (§5)

> Phase §5 deliverable. Everything here is committed, tested, and **independently verified** (not just self-reported). Commits: `caa5c9a` (vocab+fonts), `507ab01` (teaching).

## The craft patterns, translated into THIS system

Top-tier explainer craft (Vox / Vox Atlas-Borders / Johnny Harris / Kurzgesagt / editorial motion) distilled to what this HTML/CSS/SVG/GSAP pipeline can actually render deterministically:

| Pattern | How it now lives in the system |
|---|---|
| One idea per scene; screen shows a *phrase*, voice carries the *sentence* | Mason heading uses the short `on_screen_text` (§4 C4); captions scrimmed for legibility; Iris/Marlow taught the rule. |
| **Big numbers as the hero** | New `big-number` layout — one giant stat at hero scale (380px), optional unit, `#FFD000` tint on the signature beat. |
| **Change/sequence as a drawn line** | New `timeline` layout — horizontal SVG baseline with evenly-spaced labelled nodes parsed from the scene text. |
| Numbers that animate up | New `count-up` effect — hero number tweens 0→target on the paused timeline (frame-deterministic via `data-target` + onUpdate). |
| Data is drawn, not photographed | Native inline-SVG bar chart for `data-chart` (§4 C5) — no more stock-photo charts or blank placeholders. |
| Color is signal | Iris taught: ≤2 functional accents + the reserved `#FFD000`, never a 4th; the highlighter lands on the single turn beat. |
| Type carries the brand | Proprietary "GT Sectra" replaced with bundled **OFL** faces (below) — the single biggest "designed vs templated" lever. |
| Restraint in motion | Motion budget = 1–2 deliberate moves; the highlighter is the climax (taught + enforced by the budget). |

## Vocabulary extended — IN LOCKSTEP (the non-negotiable)

Three new tokens, each added across all four required locations + a test, and verified to keep Iris↔Mason byte-identical (the AST-parse lockstep test stays green):

| Token | Axis | Contract enum | Iris specifier | Mason renderer | Test |
|---|---|---|---|---|---|
| `big-number` | LAYOUT | `storyboard.schema.json` (+ value-level, error-on-unknown) | `art_engine.py` LAYOUTS + `choose_layout` (single dominant stat → big-number) | `composition_engine.py` `_layout_big_number` + CSS | new big-number render + self-scan tests |
| `timeline` | LAYOUT | same | `art_engine.py` LAYOUTS + `choose_layout` (chronological/process cue) | `_layout_timeline` + `parse_timeline_data` + SVG/CSS | new timeline node-count + self-scan tests |
| `count-up` | EFFECT | same | `art_engine.py` EFFECTS | `_fx_count_up` (deterministic data-target tween) | new determinism test |

**Independent verification (main thread, not the implementer's own tests):** rendered a `big-number`+`count-up` scene and a `timeline` scene through the real engine →
- `scan_determinism` violations: **[]** (none) for both.
- `@font-face` is **local** (`assets/fonts/…`, no `http`); `font-family` resolves to `'Fraunces'` with **no dict-leak**.
- Fonts physically copied into the scene project (`Fraunces.ttf`, `Inter.ttf`).
- big-number emits the hero value + count-up `data-target`; timeline parses 4 real nodes.

> No new TRANSITION token was added on purpose: cross-scene transitions are currently metadata-only (scenes are independent comps concatenated), so a new transition token would not actually render. Flagged for a future "baked transitions" upgrade rather than shipped half-done.

## Fonts & licensing — all SIL OFL 1.1, bundled locally

HyperFrames forbids render-time font fetch, so fonts are bundled in `composition-engineer/fonts/` and copied per-scene into `assets/fonts/`, emitted as deterministic `@font-face` (see `fonts/LICENSES.md`):

| File | Family | Role | License |
|---|---|---|---|
| `Fraunces.ttf` | Fraunces (variable) | editorial display | SIL OFL 1.1 |
| `Inter.ttf` | Inter (variable) | body | SIL OFL 1.1 |
| `NotoSerifDisplay-*.ttf` | Noto Serif Display | display fallback | SIL OFL 1.1 |
| `NotoSans-*.ttf` | Noto Sans | body fallback | SIL OFL 1.1 |

Consistent with Magpie's open-license discipline — no proprietary fonts, no render-time fetch. Iris's default `typography.display.family` is now `Fraunces`, body `Inter`.

## How each agent was taught (permanent, in the files the engines/personas read)

- **Iris** (`art-director/soul/STYLE.md` + `SKILL.md`): layout-selection heuristics (stat→`big-number`, chronological→`timeline`, magnitude→`data-chart`, head-to-head→`comparison-2up`, claim→`quote-card`/`lower-third`); "the screen says the phrase, the voice says the sentence"; ≤2 functional colors + reserved `#FFD000`; the highlighter beat on the turn scene; motion = 1–2 moves; specify Fraunces/Inter, never proprietary. (Her JSON example's `GT Sectra` swapped to `Fraunces`.)
- **Marlow** (`scriptwriter/soul/STYLE.md` + `SKILL.md`): cold-open reframe/contradiction hook; one idea per scene; a mid-video "turn" that carries the `#FFD000`; surface concrete numbers so Iris can build `big-number`/`data-chart`; vary cadence; end on a viewer-directed button.
- **Cadence** (`audio-designer/soul/STYLE.md` + `SKILL.md`): match bed mood to topic; VO authoritative at 0 dB hard-ducking the bed; one signature SFX on the cut into the `#FFD000` beat; a beat of near-silence before the turn.
- **Mason** (`composition-engineer/SKILL.md`): documented the native `data-chart` SVG, the local OFL `@font-face` bundling, and that contrast failures now block the auto-gate. (Correctly did NOT document tokens until they existed — caught by the teacher's verify-first check.)

## Net effect for §6
The fleet can now render: real editorial type, hero big-numbers, drawn timelines, animated count-ups, native data charts, legible scrimmed captions — all deterministic and OFL-clean. This is the toolkit the 5-video ladder will exercise, escalating from the §4-fixed control to a showcase that uses the full new vocabulary.
