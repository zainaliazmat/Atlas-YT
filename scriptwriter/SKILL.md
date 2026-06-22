# SKILL.md — The Scriptwriting Method

This is the method you follow on every script job. Work the steps in order. The
output of the whole method is a single structured **script** (schema at the bottom):
a hook, a sequence of one-point scenes, and a CTA — every factual line tagged to a
fact the research brief already established.

This file is the engine's *method*, not a voice. It says HOW a brief becomes a
script. Who Marlow is and how he talks lives in `soul/` — never here.

## Goal
Given a `research_brief.json`, write a tight, watchable explainer script a narrator
can read aloud and an art director can shoot — one clear point per scene, a hook
that earns the first five seconds, and **not one factual claim that can't be traced
back to a source already in the brief.**

## Inputs (what you get)
- `research_brief.json` — Sage's pack. Your raw material AND your fence. You may
  shape, order, and dramatize what's in it; you may NOT introduce a fact it doesn't
  contain. The fields you read:
  - `verified_facts[]` — `{claim, sources:[url], confidence}`. The facts you may
    assert. Each is a ground for a claim; **prefer `confidence: high` for the hook and
    for anything on screen.**
  - `key_statistics[]` — `{stat, value, date, source}`. Specific numbers, each with
    its OWN source and (often) a date. Cite a stat-claim with its `[S#]` so it's
    grounded to *that* source. A `date` means the figure is a **snapshot** — keep the
    qualifier, never restate it as the current standing. A single-sourced stat is shaky:
    hedge or omit it — and if its *category* is flagged in `contested_or_uncertain` /
    `open_questions` (e.g. pricing comparability), **omit the figure, don't just attribute
    it** (attribution won't satisfy the fact-checker for an uncorroborated number).
  - `timeline[]`, `notable_quotes[]` — supporting material you may weave in, each
    carrying its own source.
  - `myths_and_corrections[]` — gold for hooks and tension; assert the *correction*,
    never the myth as fact.
  - `contested_or_uncertain[]` — usable only if attributed/softened ("the evidence
    is mixed…"), never stated as settled.
  - `open_questions[]` — what the research could NOT settle. Never assert anything here
    as a flat fact; if you raise it, frame it as open.
  - `overview`, `angle`, `target_audience`, `working_title` — shape the arc, the
    framing, and the reading level. The `overview` often flags which facts are shaky —
    heed it. No `target_length` exists; you set the runtime.
  - `sources[]` — `{url, title, credibility_note}`. The citation table every claim's
    `source_ref` must resolve into.

## Step 1 — Find the through-line (the planning / decision step)
Before writing a word, decide the ONE thing this video is about — the spine every
scene serves. Read the `angle` and `overview`, scan the verified facts, and name the
single argument or question the video answers. If you can't say it in a sentence, you
don't have a through-line yet. This is where you decide what to leave out: a fact
that doesn't serve the spine is cut, no matter how true.

## Step 2 — Build the arc
- **Hook (first ~5 seconds) — reframe a common assumption.** Open on the sharpest,
  most surprising true thing — a corrected myth, a startling verified stat, a concrete
  question — that *contradicts what the viewer assumes*: "most people think X; it's
  actually Y." Zero throat-clearing: no "In this video," "Today we're going to,"
  "Welcome back," "Have you ever wondered." The first sentence must make leaving feel
  like a loss.
- **Scenes (one point each).** Order the verified facts into a sequence where each
  scene makes exactly ONE point and sets up the next. One scene = one idea. If a
  scene needs the word "also," it's two scenes.
- **The turn.** Somewhere mid-video, place the beat where the argument *pivots* — the
  moment that recasts everything before it. It carries the most tension, and the art
  director hangs the signature `#FFD000` highlighter on this one scene. Make it
  unmistakable; don't bury it.
- **One scenic detour.** Somewhere in the middle, earn one vivid, concrete, human
  moment — the specific example, the person, the strange detail people actually
  remember. It must still be sourced from the brief and still serve the spine; it is
  a deliberate beat, not padding.
- **CTA (close) — a viewer-directed button.** One clean line aimed outward at the
  viewer — a question to the audience, a "follow for the next part," a turn outward.
  Never "in conclusion." Never a summary of what they just watched.

## Step 3 — Write each scene
Every scene carries:
- `beat` — `hook` | `point` | `detour` | `cta` (your label for the arc).
- `point` — the single idea this scene makes, in one plain line (for you and the
  art director; not necessarily spoken).
- `narration` — what the narrator says. Built for the ear: short, declarative
  sentences, read-aloud rhythm. One point, cleanly made. **Vary the cadence** — land a
  short punch after a long line; the rhythm is the voice.
- `on_screen_text` — the few words on screen this beat (a phrase, a number, a
  label). Short. Often the point distilled. May be empty for a pure-talk beat. **When
  the point IS a number, put the bare figure here** ("+340%", "95mg", the specific
  count) so the art director can build a `big-number` or `data-chart` from it — a drawn
  number beats a sentence about one. (Honor the reliability boundary in Step 4b: never
  put an unhedged shaky number on screen.)
- `visual_note` — names what is **literally on screen**: "a single rising line
  chart, 1990→2020," "close-up of the actual 1948 memo," "split screen: myth vs.
  fact." Not a mood ("something dynamic") — a thing a person could shoot or build.
- `duration_est_sec` — your honest estimate of how long the beat runs (narration
  pace ≈ 2.5 words/second is a fair guide).
- `claims[]` — see Step 4. The hook and CTA usually assert no fact and carry an
  empty `claims` list; that is legal and expected.

## Step 4 — Tag every factual line to a brief fact (THE RULE)
This is the part that cannot be skipped or fudged. **A claim that can't be tagged to
a brief source doesn't ship.**

For every line of narration that asserts a fact, emit a claim object. You do NOT
write the citation index yourself — you point at the brief item the line rests on, by
its tag. The engine then deterministically resolves that tag to a real `source_ref`
(a 0-based index into the brief's `sources[]`). **Cite each claim to what actually
supports it** — there are two tag families, and using the right one is the rule:

- **`F<index>`** — a `verified_facts` entry. The engine takes the first of that fact's
  source URLs that appears in `sources[]`; the `source_ref` is that source's index.
- **`S<index>`** — a `key_statistics` entry. The engine resolves the STAT'S OWN
  `source` to its index. **A claim that asserts a specific number must use the `[S#]`
  of the statistic carrying that figure** — so the number is cited to the evidence that
  actually backs it, not to a borrowed fact's source. Never paste one blanket tag onto
  every claim; the citation must point at the real support. **Use the entry's EXACT
  model + benchmark + figure together** — never the figure from one entry with the
  model/benchmark of another. (When you state a stat number, the engine confirms the
  cited entry agrees on the model/benchmark, not just the bare digits — a number-only
  match to the wrong entry is dropped, not smuggled.)

3. **If the tag is unknown, or its source URL doesn't resolve to a brief source, the
   claim cannot be grounded — and it does not ship.** The line is dropped. If dropping
   it empties a scene, the scene is dropped too.

So: you assert only what the brief established, and you point each assertion at the
exact item that backs it. A vivid line you can't ground gets cut, not smuggled — the
Fact-Checker re-derives the same resolution downstream, and a claim that points
nowhere (or at the wrong source) is flagged and blocks the whole production. (The
engine also runs a deterministic numeric-citation check: a number cited to a source
that doesn't carry it is repaired or caught here, before the gate.)

Rules for claims:
- Assert only what the brief carries (`verified_facts` and `key_statistics`). Don't
  assert a `contested_or_uncertain` or `open_questions` item as fact; if you use it,
  soften/attribute it and don't tag it as a hard claim.
- Never assert the myth side of a `myths_and_corrections` pair. Assert the
  correction (tag it to the fact that establishes the correction).
- One claim per asserted fact/number. If a sentence asserts two, it's two claims (or,
  better, two scenes).
- Don't invent a statistic, quote, date, or source. If the brief doesn't carry it,
  you don't have it.

### Step 4b — The reliability boundary: consensus-forward, keep solid specifics, drop fragile decimals
The line between what to assert flat and what to soften is **how many sources back it** —
which the brief tells you directly: `verified_facts` carry a *list* of sources + a
`confidence`; `key_statistics` carry a *single* `source`.
- **Lead with the consensus.** Load-bearing claims and ALL `on_screen_text` come from
  `verified_facts` (multi-source, confidence-rated — they pass fact-check every time). A
  specific number that lives *inside* a verified fact (e.g. a ~1M-token context window,
  a multi-source GPU-hours figure) is consensus — **state it confidently.** Keeping
  these solid specifics is the point; consensus-forward is not vague.
- **`key_statistics` are single-sourced by construction → never a bare fact.** For each
  one you're tempted to use: either **attribute-and-soften** to its one source
  explicitly ("one June-2026 pricing tracker lists DeepSeek R1 around $0.70/$2.50") and
  only when it genuinely adds value, or **omit** it. For **volatile benchmark
  percentages, prefer omit** — they're exactly what gets superseded and contradicted
  across sources.
- **Omit — don't attribute — a single-source figure whose category the brief flags as
  conflicting/uncertain.** If `open_questions` or `contested_or_uncertain` calls the
  figure's *category* unsettled (e.g. "are the pricing figures even comparable across
  providers/versions?", or benchmark numbers that mix harnesses/versions), then the
  number is uncorroborated and **attribution will not save it** — the fact-checker's own
  search won't corroborate a lone tracker's figure, so it blocks even when softened and
  cited. Drop the figure entirely and lead with the **multi-source qualitative** point
  from `verified_facts` instead — e.g. say "DeepSeek is *far cheaper per token*" (a
  multi-source verified fact, stated directionally) rather than "one tracker lists it at
  $0.27 in / $1.10 out" (a single, conflicting figure). The qualitative claim is the one
  that passes; the exact decimals are the part that blocks.
- **Magnitude/ratio comparatives are quantitative — they need the brief to carry the
  magnitude, not just the direction.** "An order of magnitude", "10x", "10× cheaper",
  "twice as fast", "half the price", "N-fold" all assert a *magnitude*, exactly like an
  explicit number does. Use one **only when a `verified_fact` (or corroborated stat) in
  *this run's* brief establishes that magnitude.** If the brief supports only the
  *direction* — e.g. its cheapness fact says "dramatically cheaper per token" with no
  multiple — then say it **directionally**: "far/dramatically cheaper", "much faster",
  "far more capable", with no implied multiple. Never introduce a magnitude the brief
  doesn't carry, and remember the brief is **re-researched every run** — a magnitude word
  that was safe last time ("order of magnitude") is unsupported this time if this brief's
  fact only carries the qualitative direction. On screen, "FAR CHEAPER" — not "≈10×" —
  unless the magnitude is brief-established.
- **Never cross-combine.** Don't attach a figure from one `key_statistics` entry to a
  model or benchmark named in another. If two entries share a number, they are *not*
  interchangeable — use the one entry's exact model + benchmark, or drop it.
- **A shaky figure is never a flat current fact** even beyond stats: anything that
  echoes a `contested_or_uncertain` / `open_questions` item gets softened/attributed or
  cut. Well-corroborated, stable figures may stay as confident claims.
- **Preserve temporal qualifiers.** If the brief dates a figure, keep the date in the
  line. Never present a snapshot as the present standing.
- **`on_screen_text` must never show an unhedged shaky number** (e.g. no bare
  "76.8%" card for a dated/single-sourced benchmark — that's an unqualified assertion).
- **Stay internally consistent.** If the script's argument is "rankings change every
  version / these models are a generation behind," do not also assert a specific
  "current leader" number that contradicts it. Pick one and mean it.

## Step 5 — Bound the runtime, then self-check
- Aim for a tight arc — typically 6–10 scenes — plus the one detour. Sum the
  per-scene `duration_est_sec` into `est_runtime_sec`. Cut to the spine; never pad
  to hit a length.
- Re-read the hook: if it opens with throat-clearing, rewrite it.
- Re-read each scene: if it makes more than one point, split it.
- Confirm every factual line has a claim tagged to a brief fact; cut the ones that
  don't.

## Your output contract (what you return)
Return ONLY a JSON object in exactly this shape. The engine assigns `scene_no` and
`claim_id`, resolves each claim's `support` tag into a `source_ref`, computes
`total_scenes`/`est_runtime_sec`, and stamps the schema envelope — so you focus on
the writing and the tagging, not the bookkeeping.

```json
{
  "working_title": "the title for this cut",
  "hook": "the spoken hook line (also scene 1's narration)",
  "cta": "the spoken closing line",
  "scenes": [
    {
      "beat": "hook | point | detour | cta",
      "point": "the single idea this scene makes",
      "narration": "what the narrator says",
      "on_screen_text": "the few words on screen (may be empty)",
      "visual_note": "what is literally on screen",
      "duration_est_sec": 7.5,
      "claims": [
        {"text": "the exact factual line asserted", "support": "F3"},
        {"text": "a line asserting a specific number", "support": "S1"}
      ]
    }
  ]
}
```

- `support` MUST be a tag: `F<index>` of a `verified_facts` entry, OR `S<index>` of a
  `key_statistics` entry (use `S#` whenever the claim asserts that stat's number).
  Never a URL, never an index you made up. The engine does the resolving.
- A scene that asserts no fact (a hook, a CTA, a pure-talk transition) has
  `"claims": []`. That is correct, not an omission.
- If a list is empty, return `[]`. Never pad.

## The final script (the interface the next agents receive)
The engine resolves your tags and wraps the envelope, producing the saved
`script.json` — `support:"F3"` becomes `source_ref: <index into brief.sources>`,
each scene gets a `scene_no`, each claim a scene-addressable `claim_id` (`s2c1`):

```json
{
  "schema_version": "1.0",
  "working_title": "...",
  "hook": "...",
  "cta": "...",
  "total_scenes": 8,
  "est_runtime_sec": 62.0,
  "scenes": [
    {
      "scene_no": 1, "beat": "hook", "point": "...", "narration": "...",
      "on_screen_text": "...", "visual_note": "...", "duration_est_sec": 6.0,
      "claims": [ {"claim_id": "s1c1", "text": "...", "source_ref": 4} ]
    }
  ]
}
```

The Fact-Checker (Sage, pass 2) then interrogates this script against the same
brief, claim by claim. Every `source_ref` you emit must resolve to a real
`brief.sources` entry — that is guaranteed by Step 4, by construction.
