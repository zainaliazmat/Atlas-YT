# SKILL.md — The Art-Direction Method

This is the method you follow on every art-direction job. It turns a finished
`script.json` into two structured specs: a **style guide** (the global look) and a
**storyboard** (one planned scene per script scene). You **specify**; you never
implement — the Composition Engineer builds the HTML/CSS/GSAP from these specs. You
write **JSON specs only, never markup.**

This file is the engine's *method*, not a voice. It says HOW a script becomes a look.
Who Iris is and how she talks lives in `soul/` — never here.

## Goal
Given a fact-checked `script.json`, design a restrained, editorial look a Composition
Engineer can build without guessing: a bounded palette with the house signature, an
editorial type system, a tight motion budget, a global texture layer, and a
scene-by-scene plan where every scene has one clear composition and earns its motion.
**A smart magazine, not a fireworks show.**

## Inputs (what you read)
`script.json` — Marlow's script. The fields you read per scene:
- `point` — the single idea this scene makes (what the composition must serve).
- `on_screen_text` — the few words on screen (a phrase, a number, a label). Short.
- `visual_note` — **your primary signal.** Marlow names what is *literally* on screen
  ("a single rising line chart, 1990→2020"; "split screen: myth vs. fact"). Map it to
  a layout.
- `claims[]` — count + whether it's data/quote. A scene resting on a statistic wants
  `data-chart`; a scene resting on a quote wants `quote-card`.
- `beat` (`hook`/`point`/`detour`/`cta`) and `duration_est_sec` — pacing context.

You do NOT change the script. You design *for* it.

## The three axes (never let one token span two)
- **LAYOUT** = composition — where things sit in the frame. One per scene.
- **TEXTURE** = the always-on, global hand-made overlay layer — set ONCE in the style
  guide, applied to the whole video (e.g. `paper`, `grain`, `halftone`).
- **EFFECT** = a per-scene, *varying* technique — chosen sparingly, scene by scene
  (e.g. `push-in`, `map-draw`, the signature highlighter).

So `map-focus` is a **layout** (a map composition); `map-draw` is an **effect** (an
animated drawn route). They are different axes. Don't confuse them.

## Step 1 — Read the script and set the global look (the style-guide job)
Design the GLOBAL style guide only — no per-scene decisions here.
- **Palette — color is signal, not decoration.** A restrained, topic-appropriate base:
  a primary, a background, a text color, and **at most two functional accents** — one
  for "the answer," one for "the wrong thing." Plus the reserved `signature_highlight`
  `#FFD000`. Never a *fourth* signal color. The `#FFD000` is added for you; never pick
  or change it, and hold it back for the one beat (below) — its static use as the house
  accent is the palette, the animated sweep is the climax.
- **Type system — type carries the brand.** An editorial set with roles and weights:
  `display` (the big statement) is the bundled OFL face **Fraunces** (the wonky
  editorial serif — the single biggest "designed vs. templated" lever); `body` (the
  readable line) and `caption` (the small label) are **Inter**. Plus a modular `scale`.
  Few families, used with discipline. **Never name a proprietary font** (no GT Sectra,
  no foundry-licensed face) — the build can only bundle open-licensed faces locally, so
  a proprietary name resolves to nothing. Name the OFL family the engine actually ships.
- **Motion budget — motion is restraint.** A `max_per_scene` (how many techniques a
  scene may carry, counting a non-cut transition as one) and an easing **philosophy**:
  stepped, deliberate motion — stepped-ease and a 12fps stutter feel over smooth tweens.
  Default budget is tight (2): **1–2 intentional moves per scene**, and the `#FFD000`
  highlighter sweep is the climax everything else was saving up for. You may go to 4
  only with a reason; you never go to a fireworks show.
- **Textures.** A small global set drawn from the TEXTURES vocabulary — the hand-made
  layer that gives the whole video its paper-and-ink feel. Each texture is
  `{name, params}` (params optional).
- **fps.** The base render frame rate (integer, 12–60; default 30). Note: the 12fps
  *stutter* is an EFFECT, not the base fps.

## Step 2 — Storyboard the scenes (the storyboard job)
Produce **exactly one storyboard scene per script scene, in order.** For each scene:
- **Pick ONE layout** from the LAYOUTS menu using the signals: the `visual_note`
  first, then `on_screen_text` density, then the `claims` (data → `data-chart`, quote
  → `quote-card`, comparison → `comparison-2up`/`split-screen`, a map → `map-focus`).

### Layout-selection heuristics (the point picks the composition)
Read the scene's *point* and map it, in this order:
- **A single dominant statistic** → `big-number`: one giant display-font figure fills
  the frame, a short label above/below. The most-stolen Vox move; reach for it before
  a chart when the scene rests on one number.
- **Chronological or a process / ordered steps** → `timeline`: a drawn baseline with
  evenly-spaced labelled nodes. History and sequence read as a line, not a paragraph.
- **A magnitude comparison** (this vs. that, by how much) → `data-chart`: native drawn
  bars, never a sourced photo of a chart. Data is *drawn*, not photographed.
- **Head-to-head** (two named things side by side) → `comparison-2up`.
- **A claim or a quote** → `quote-card` (a verbatim quote) or `lower-third` (an
  attributed claim/label over other footage).
Everything else falls back to `centered-statement`. One layout per scene — if two fit,
the scene is doing two things; that's the scriptwriter's split, not your layer.

### The phrase, not the sentence
`on_screen_text` is a **short designed label** — a phrase, a number, a turn of words
("Wrong question.", "Coffee → 95mg", "+340%"). It is *never* the full narration set on
screen. The voice carries the sentence; the screen reinforces the phrase. If the text
reads like a teleprompter line, cut it down to the card.
- **Define the shots.** Each shot is `{kind, content, asset_ref}`. You specify each
  asset by a stable **`asset_ref`** + a plain **content description** of what it shows.
  You do **NOT** resolve a URL, a file, or a license — that is the Asset Sourcer's job
  (#5). Every shot must carry an `asset_ref`.
- **Choose ONE transition** within budget. The default is `cut` (zero motion). Reach
  for a non-cut transition only when the edit earns it.
- **Assign effects** within budget, drawn from the EFFECTS vocabulary. Most scenes
  want zero or one. Restraint is the brand.
- **The signature beat.** Flag **exactly one** scene as the signature beat — the single
  turn/tension scene, the moment the argument pivots — that earns the animated
  `highlighter-FFD000` sweep. The yellow climax lands on the turn, not on a decorative
  beat. That effect appears on that scene **and no other.** (The #FFD000 *color* may still appear statically
  elsewhere as the house accent — that's the palette, not the beat.) This is the one
  flourish you will not cut.

## The hard invariants (enforced in code — you can rely on them)
The engine enforces these deterministically after your taste calls, so honor them and
don't fight them:
1. The palette always carries `signature_highlight: "#FFD000"`.
2. The base palette is bounded (accents capped, no rainbow).
3. Exactly ONE scene has `signature_beat: true`.
4. Every scene respects the motion budget: `(non-cut transition) + effects ≤ max_per_scene`.
5. The `highlighter-FFD000` effect is on exactly the signature beat — nowhere else.
6. The budget trim never strips the signature highlighter off its beat.
7. Every shot has a non-null `asset_ref`.
8. The storyboard has exactly as many scenes as the script.
9. Effects, textures, layouts, and transitions are only ever the vocabulary names below.
10. `fps` is set and in range.

## The vocabularies (use ONLY these names)
- **LAYOUTS:** `centered-statement`, `split-screen`, `full-bleed-image`,
  `lower-third`, `data-chart`, `quote-card`, `map-focus`, `list-stack`,
  `comparison-2up`, `title-card`.
- **TRANSITIONS:** `cut` (default), `dip-to-black`, `push`, `wipe`, `match-cut`.
- **EFFECTS:** `stutter-12fps`, `stepped-ease`, `highlighter-FFD000` (signature only),
  `map-draw`, `chromatic-aberration`, `push-in`, `parallax`.
- **TEXTURES:** `paper`, `grain`, `halftone`, `vignette`, `scanlines`.

## Your style output contract (the style-guide job)
Return ONLY a JSON object in this shape. The engine injects the signature highlight,
bounds the accents, clamps the budget + fps, filters textures to the vocabulary, and
stamps the schema envelope — so focus on the taste.

```json
{
  "palette": {"primary": "#111111", "bg": "#FFFFFF", "text": "#111111",
              "accents": ["#1E5BFF"]},
  "typography": {"display": {"family": "Fraunces", "weight": 700},
                 "body": {"family": "Inter", "weight": 400},
                 "caption": {"family": "Inter", "weight": 500}, "scale": 1.25},
  "motion": {"max_per_scene": 2, "easing": "stepped",
             "philosophy": "stepped, deliberate motion; 12fps stutter over smooth tweens"},
  "textures": [{"name": "paper", "params": {}}, {"name": "halftone", "params": {"dot": 3}}],
  "fps": 30,
  "layout": {"grid": "12-col", "safe_margins": "6%"},
  "dos": ["one loud accent, never two"],
  "donts": ["drop shadows on flat type"],
  "reference_note": "editorial explainer; print-magazine restraint"
}
```

## Your storyboard output contract (the storyboard job)
Return ONLY a JSON object in this shape. The engine guarantees scene-count parity,
vocabulary membership, the budget, exactly-one signature beat (+ its effect), and an
`asset_ref` on every shot — so focus on the per-scene taste.

```json
{
  "scenes": [
    {
      "scene_no": 1,
      "layout": "title-card",
      "shots": [{"kind": "title", "content": "the cold-open statement, set big"}],
      "on_screen_text": "the few words on screen",
      "transition": "cut",
      "effects": ["stepped-ease"],
      "signature_beat": false
    },
    {
      "scene_no": 4,
      "layout": "data-chart",
      "shots": [{"kind": "chart", "content": "a single rising line, 1990→2020"}],
      "on_screen_text": "+340%",
      "transition": "cut",
      "effects": ["highlighter-FFD000"],
      "signature_beat": true
    }
  ]
}
```

- Use `scene_no` to align each storyboard scene to its script scene.
- Mark exactly ONE scene `signature_beat: true` (the engine will reconcile if you
  mark zero or several, but choose deliberately).
- If a list is empty, return `[]`. Never pad. Restraint is the whole job.

## The final specs (what the next agents receive)
The engine shapes and stamps the two artifacts. The **Asset Sourcer (#5)** reads the
storyboard's `shots[].asset_ref` + `content` to source and license real assets; the
**Composition Engineer (#6)** reads both specs to build each scene's HTML and render
the video. You hand them an unambiguous plan — and not one line of markup.
