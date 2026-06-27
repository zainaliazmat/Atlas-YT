# Reference-quality compose + a blocking quality gate

**Date:** 2026-06-28
**Status:** Approved design (pending spec review)
**Owner:** CEO (zain-ali)

## Problem

YouTube's 2026 "inauthentic content" policy demonetizes templated / AI-spam video. Our
studio engine currently ships exactly that: [studio/compose/__init__.py](../../../studio/compose/__init__.py)
authors an **identical template for every scene** — same `.label` + `.lead` hero line, plus
ONE beat chosen from a 4-way keyword enum (`count-up` / `orbit` / `bell` / `underline`) at
[studio/compose/__init__.py:291-315](../../../studio/compose/__init__.py#L291-L315). The
reference build, `reference/dark-truth-social/index.html` (~2377 lines, 9 hand-crafted
scenes), is the opposite: every scene has its own bespoke GSAP authored against its content
(calendar-fills-then-crumbles, slot-reel, shatter-bar, grayscale-drain, self-writing
signature). That difference is what separates hand-made from spam.

Worse, nothing **blocks** a flat render from publishing. The `dark-truth-v2` draft (our
engine's attempt at the *same* video as the reference) shipped with clipped text
("DARK TRUTH BEHIN TI", "4 MORE TIME[S]"), dropped the attributed quote cards
(Raskin/Brichter), 2 of 3 stats, the checklist, and the juries footnote — and still rendered.

## Goal

1. **A quality gate** that scores a draft 0–5 per dimension *with actionable per-dimension
   diagnostics*, runs a hard determinism + compliance self-check, and **BLOCKS publish**
   below bar. Calibrated to discriminate our flat draft (LOW/blocked) from the reference
   (HIGH/pass).
2. **A reference-quality compose stage**: a rich, parameterized, *growing* archetype library
   that reproduces how the reference was actually built — bespoke per-scene layout + motion,
   deterministic, no per-render AI.

Built in that order (gate first) so compose work is measurable against a live score, not
guesswork.

## Scope decisions (locked)

- **Compose = deterministic archetype "recipe book"** (not per-scene AI, not hybrid).
  Per-scene AI reintroduces nondeterminism + per-video cost/latency + a fragile strip step
  and doesn't compound. Hybrid LLM-polish is a FUTURE upgrade, only if cross-video sameness
  becomes the limiter.
- **Gate scoring/blocking lives in a new `studio/gate/` package**; evidence-gathering stays
  in `studio/review/`. No duplicated logic across the two — `gate/` imports `review/`.
- **Wiring target is `studio/pipeline.py`** (the live v2 production spine). Confirmed distinct
  from `atlas/` (the orchestrator, whose in-process pipeline is being removed —
  `atlas/dispatcher.py` / `atlas/atlas_decider.py` are deleted). `studio/` is the production
  ENGINE Atlas delegates into; a deterministic render pipeline there is correct. The gate is
  NOT wired into anything slated for removal.

---

## Part 1 — The Quality Gate (`studio/gate/`)

### Module layout

```
studio/gate/
  __init__.py        # score(slug|video|index) -> scorecard dict; the public seam
  dimensions.py      # the 0-5 deterministic scorers (no LLM)
  compliance.py      # hard pass/fail self-checks (BLOCKING, not scored)
  judge.py           # the LLM frame-based polish-vs-reference dimension (ensemble)
  scorecard.py       # combine dims + compliance -> verdict + reasons
  thresholds.json    # CEO-owned per-dimension floors + weights (read-only to the loop)
  calibrate.py       # run both anchors, assert discrimination
```

Reuses (no forking): `studio/review/evidence.py` (frame sampling, loudness, polish anchor),
`studio/review/motion_check.py` (dead-air), `studio/review/critics.technical_scan`
(determinism grep), `eval/analyzers/video.py` (motion energy, cut rhythm).

### Scored dimensions (0–5 each; every score carries a WHY)

| Dimension | Source (reuse) | Example diagnostic |
|---|---|---|
| `motion_energy` | `eval/analyzers/video.py` mean Δ-luma | "scenes 2,7 visually static (energy 0.8 < floor 2.5)" |
| **`motion_variety`** *(new — the anti-spam metric)* | static parse of `index.html`: count of *distinct* archetypes / layouts / beats across scenes | "8/9 scenes share one layout + the underline beat → templated" |
| **`content_fidelity`** *(new — deterministic, no LLM)* | static parse: does every scripted `on_screen_text` string + `claim` from `script.json` actually appear in its matching composition scene? | "scene 5 dropped the attributed quote cards (Raskin/Brichter); 2 of 3 stats missing; juries footnote absent" |
| `dead_air` | `motion_check` trailing-static / no-motion stretches | "dead air on scenes 3, 6, 8" |
| `pacing` | cut-rhythm (scene-duration distribution) | "scene 5 holds 14s with no beat change" |
| `audio` | loudness / peak / clipping from `evidence` | "−22 LUFS, 8 dB under the −14 target" |
| `polish_vs_reference` *(the layered LLM dim)* | **frame-based** ensemble judge vs the pack's golden reference frames | "loses to reference on 5/5 votes: flat type, no signature beat" |

**`content_fidelity` severity rule:** a MISSING ON-SCREEN ATTRIBUTED QUOTE is high-severity
(it's the factual centerpiece). Fact-check validates the *script*, never that the quote
*rendered* — this dimension closes that gap. A missing attributed quote alone can drop the
dimension below floor.

### Compliance self-check (hard pass/fail — BLOCKS, not scored 0–5)

- **Determinism** — `technical_scan`: no `Math.random` / `Date.now` / `new Date` / `fetch` /
  `XMLHttpRequest`; master timeline registered on `window.__timelines`.
- **Legibility / overflow** — reuse HyperFrames `inspect`'s overflow / layout-integrity
  result as a BLOCKING check. (The draft shipped clipped text, which means `inspect`'s
  overflow result was NOT gating; this wires it in so clipped text can never ship.)
- **License manifest complete** — every materialized asset carries a real license (no
  `Unknown`), via the Asset Library clearance data.
- **No real-person likeness** — compliance critic + a check that no real-person portrait was
  used (reference rule: anonymous halftone silhouette only; no real-executive likeness).
- **Fact-check passed** — `factcheck_report.json` verdict == `pass`.

### Blocking rule

```
verdict = BLOCKED if (
    any compliance check fails           # hard, un-approvable (same semantics as factcheck)
    OR any scored dimension < its floor   # default floor 3.0, per-dim in thresholds.json
) else PASS
```

The scorecard always lists the **specific reasons** (the failing checks + per-dimension
diagnostics), so a block tells compose exactly what to fix.

### Build order inside the gate

Deterministic dims + compliance FIRST (cheap, fast, no LLM, catch the biggest failures:
template-sameness, missing content, clipped text, dead air, nondeterminism, unlicensed
assets). THEN layer the LLM `polish_vs_reference` judge.

The deterministic dims are the **PRIMARY blockers**. The LLM dim is given an **ensemble
margin** (N seeded votes, fold in `eval/judged.py`'s seeded / order-randomized ensemble
method) so a borderline render does not flip-flop between runs. The text-digest judge from
`eval/judged.py` is dropped for this dimension — we judge actual frames, not a text proxy.

### Reference strategy for `polish_vs_reference` (works on FUTURE videos with no twin)

The `dark-truth-social` twin is ideal for *calibration*, but the gate runs on future videos
that have no twin. So the polish judge compares the draft's frames against **the chosen
design-pack's exemplar — a small golden-set of reference frames stored with the pack**, NOT a
per-video twin. Each design pack ships `golden/` reference frames representing its quality
bar; `polish_vs_reference` samples the draft and judges it pairwise against that set. This is
what makes the dimension generalize past the calibration pair.

### Calibration (the proof it discriminates)

`studio/gate/calibrate.py` runs the full gate on both anchors and asserts:

- `studio/projects/dark-truth-v2/renders/draft.mp4` (+ its `index.html`) → **BLOCKED / low**
- `reference/dark-truth-social/renders/dark-truth-social.mp4` (+ its `index.html`) → **PASS / high**

If it can't discriminate, the gate isn't trustworthy — thresholds get tuned until it does.
This is also the task's required end-to-end verification ("a flat draft is blocked with
specific reasons").

### Wiring into the pipeline

The scorecard verdict replaces the final gate's ad-hoc `motion_ok && review_ok` at
[studio/pipeline.py:492-524](../../../studio/pipeline.py#L492-L524). The `final` gate's
`details` carries the full per-dimension scorecard. Compliance failures are **un-approvable**
(a human cannot sign off a nondeterministic / unlicensed / clipped / unverified render —
same hard semantics as the factcheck gate). Scored-dimension failures are surfaced as the
block reason; `--unattended` auto-approval requires a PASS verdict.

---

## Part 2 — Compose recipe book (`studio/compose/archetypes/`)

### Archetype registry

`studio/compose/archetypes/` — a **registry** of ~12–15 genuinely distinct archetypes, each
owning its **bespoke layout + GSAP**. Target set (grows over time):

`hook-counter`, `title-with-portrait`, `stats-trio`, `device-mockup-loop`, `quote-cards`,
`metaphor-object-transform` (shatter / crumble / drain), `statement-with-strike`,
`checklist-reveal`, `signature-outro`, `map-or-data`, `comparison-vs`, `timeline`,
`glyph-trio`.

Each archetype module exposes a uniform interface:

```python
def build(scene, ctx) -> {"html": str, "beats_js": str, "needs": [...assets]}
```

so the Composer composes them on the existing re-timer / transition / ticker scaffold
without special-casing.

### Parameterization within each archetype (real variation, not renamed enums)

- `stats-trio` handles 1 / 3 / 5 values × bar / line / pie.
- `device-mockup-loop` handles any looping content (feed / refresh-reel / grayscale-drain).
- `quote-cards` handles 1–N attributed cards with highlighter-swipe on the key phrase.

### Growing library ↔ gate reinforcement

When `classify` finds no archetype that fits well, compose falls back to a generic layout
**and emits a "needs new archetype: <scene shape>" signal**. That is exactly what the gate's
`motion_variety` dimension flags as low. So a gap surfaces as a measurable score drop →
build the archetype once → it's reused and quality compounds. The two systems reinforce.

### Archetype SELECTION — tagged, not re-inferred

Compose must NOT re-infer the archetype from raw text (lossy, mis-fits). Instead:

- **An art-direction tagging step tags each scene with an `archetype` from a CLOSED vocab**,
  written into the scene contract. Compose READS the tag and executes it.
- A heuristic `classify(scene)` is the **FALLBACK** only when a scene has no tag.
- The closed archetype vocab is **parity-tested** against compose's archetype registry (a
  test asserts: every vocab value has a registry implementation, and vice versa) — the same
  pattern as the existing atlas Iris/Mason closed-vocabulary layout parity
  ([atlas/adapters/art_director.py:201](../../../atlas/adapters/art_director.py#L201)).

**RESOLVED:** the studio spine (`research → script → factcheck → vo → compose`) has no
art-direction stage today, so we add a thin **`storyboard` stage** (Iris / `art_director`
engine) between `factcheck` and `vo`:

```
research → script → factcheck★GATE → storyboard(Iris) → vo → compose → draft → review → final★GATE
```

The storyboard stage annotates each scene with an `archetype` tag from the closed vocab
(matching "Iris tags the archetype in the storyboard"), keeping the visual decision with the
art director rather than the scriptwriter. It is a normal resumable stage in `state.json`
(skipped when done, like every other), NOT a human gate. The closed archetype vocab lives in
ONE place and is parity-tested against compose's archetype registry. Compose reads the tag;
the heuristic `classify(scene)` runs only when a scene has no tag (e.g. a legacy project, or
a storyboard toolchain gap — it degrades, never crashes).

### Lift the real implementations from `dark-truth-social/index.html`

The VO-lock re-timer, transition library, ticker, and procedural halftone/grain filters are
already partially present in the studio pack partials (`makeRetimer`, `makeTransitions`,
`makeTicker`, the filters partial). Port the MISSING bespoke beats into the studio motion
library as reusable, parameterized factories: calendar-fill-then-crumble, slot-reel,
shatter-bar, grayscale-drain, highlighter-swipe (quote cards), sequential checkmarks,
strike-through, self-writing signature, parallax cards. `studio/GOLDEN_REFERENCE.md` already
documents the exact line refs and the technique teardown — it is the porting guide.

---

## Verification (end-to-end)

1. **Gate calibration passes**: `dark-truth-v2` draft → BLOCKED with specific reasons (clipped
   text, missing quote cards, low motion_variety, dead air); `dark-truth-social` → PASS. This
   alone satisfies the task's explicit verification.
2. **Compose climbs the score**: re-run the upgraded compose on `dark-truth-v2` → re-render →
   its gate score moves from BLOCKED toward the reference, with `motion_variety` and
   `content_fidelity` measurably improving.

## Non-goals (YAGNI)

- No per-scene AI authoring (nondeterminism + per-video cost).
- No hybrid LLM motion-polish (future, only if cross-video sameness becomes the limiter).
- No new render / TTS infrastructure — reuse the existing HyperFrames toolchain.
- No changes to `atlas/` orchestrator internals slated for removal.

## Risks

- **HyperFrames `inspect` availability** — the overflow check needs `npx hyperframes inspect`
  (Node ≥ 22). The compliance check must degrade gracefully (report "inspect unavailable" as
  a gap, and decide whether absence blocks or warns) rather than crash.
- **Golden-frame curation** — each pack needs a curated `golden/` frame set; for now only the
  reference pack has one. New packs without goldens skip the LLM dim (deterministic dims still
  gate).
- **Compose refactor surface** — the archetype refactor touches the Composer's core authoring
  loop; the determinism guarantee (`_enforce_determinism`) is the backstop and every archetype
  is determinism-tested.

## Post-build notes (Plan 1 shipped 2026-06-28)

- **Calibration is provisional (n=1 anchor pair).** Thresholds were tuned to a single
  LOW/HIGH pair (`dark-truth-v2` draft vs `dark-truth-social`). `motion_energy.floor=1.0`
  (lenient; structural dims carry the blocking weight) and `dead_air.floor=4.0` (strict) are
  n=1 fits — recorded in `studio/gate/thresholds.json` `_notes.calibration`. Recalibrate with
  a corpus before trusting the bands beyond this pair.
- **Compliance charter — likeness is inert until a `vision_fn` is wired.** `overflow_blocks`
  and `likeness_blocks` are warn-only today (false): an *unavailable* check does not block; a
  *real* finding always does. The no-real-person-likeness check is **not actually evaluated**
  until a `vision_fn` is wired into `gate.score()` / `collect_evidence`. Recorded in
  `thresholds.json` `_notes.compliance_charter`. `build_scorecard` honors the `*_blocks` flags,
  so flipping them on (with the toolchain wired) makes a required-but-unavailable check block —
  the fail-open hole the first reviews flagged is closed.
- **`motion_variety` token vocab must grow with the compose archetype library (Plan 2).** The
  real reference passes at a thin margin (0.78 ratio vs ~0.76 effective floor) because 2 of its
  bespoke beats aren't yet in the beat-token vocabulary. Plan 2 makes "a new archetype ships
  with its `motion_variety` token in the same commit" a parity-tested invariant so genuinely
  varied future videos are never false-blocked.
