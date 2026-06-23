# SKILL.md — The Editorial Coaching Method

This is the method Quill follows on every coaching job. The output of the whole
method is a single **coaching addendum**: a few imperative sentences appended to a
content specialist's persona/prompt that nudge a quality metric into its rubric band
on the next draft — without regressing the sibling properties you're told to keep.

This file is the engine's *method*, not a voice. It says HOW a diagnosed target
becomes an addendum. Who Quill is and how she talks lives in `soul/` — never here.

## The boundary Quill never crosses (read this first)
Quill **authors text only.** She does NOT read or write the rubric, does NOT decide
pass/fail, does NOT pick which metric to fix or which way to move it, and does NOT
write any file. The diagnosis — the band, the direction, the sibling properties to
preserve — is **given to her as data** by the CEO-owned rubric (via the Atlas
diagnose adapter). Quill respects that direction exactly and turns it into persuasive,
domain-aware coaching. She is the writer of the note, not the judge of the work.

## Domain — pre-production / content (the editorial side, dimension G2)
Quill owns the content specialists and the rubric stages they produce:

- **research** — the brief: coverage, source quality, claim support, framing.
- **script** — hook strength, one-idea-per-scene clarity, narrative arc, the turn,
  the detour, claim density, the CTA.
- **factcheck** — how claims are framed and supported against the brief.
- **assets** — whether the chosen visuals are RELEVANT to the narration.

`coach_engine.COACHED_STAGES = ("research", "script", "factcheck", "assets")` and
`coaches_stage(stage)` is the membership check. A stage outside this set (e.g.
`compose`, `audio`, `render`) belongs to another coach; Quill declines it.

## Inputs (what the engine gets)
`propose_addendum(*, band_id, direction, preserve="", measured_value=None,
owner="", chat_fn=None)`:

- `band_id` — the metric to move into range, e.g. `script:info_density`,
  `assets:relevance`. The first segment is usually the stage.
- `direction` — the move the rubric prescribes, in plain words: "LOWER it to about
  2.75", "RAISE hook strength into [0.7, 1.0]". Quill never overrides this.
- `preserve` — sibling properties that must NOT regress while fixing the target,
  appended verbatim (e.g. " Keep these in range: script:runtime_fit in [60,90].").
- `measured_value` — the current measured value, for context (optional).
- `owner` — the specialist being coached (e.g. "Marlow", "Sage"), for addressing.
- `chat_fn` — the INJECTED brain seam `(system, user) -> str`. Defaults to
  `llm.chat` (the Claude subscription). Passing a fake makes the engine fully
  offline + deterministic for tests.

## The method
1. **Anchor on the named metric.** Read `band_id` and `direction`. The note's only
   job is to move *that* metric *that* way. No new goals, no scope creep.
2. **Translate into the editorial craft.** Express the move in the language the
   specialist already thinks in — hook, one idea per scene, arc, claim support, CTA,
   visual relevance — so it reads as a craft note, not a metric readout.
3. **Make it imperative and concrete.** 2–4 crisp sentences a writer can act on in
   the next draft. Verbs, not adjectives. "Cut to one claim per scene," not "improve
   density."
4. **Protect the siblings.** Weave the `preserve` constraints in so the fix can't
   break what's already in band.
5. **Wrap and label.** The addendum is wrapped under a fixed header
   `## Coach note (Quill · editorial · target <band_id>)` so the downstream prompt
   assembler can find, attribute, and (when the metric is back in band) retire it.

## Output contract (what the engine returns)
A plain dict — never a file:

```json
{
  "band_id": "script:info_density",
  "direction": "LOWER it to about 2.75",
  "domain": "editorial",
  "owner": "Marlow",
  "addendum": "## Coach note (Quill · editorial · target script:info_density)\n…",
  "source": "llm"
}
```

- `addendum` — the soft-tier text to append to the specialist's persona/prompt,
  always wrapped with the `## Coach note (Quill · editorial · target <band_id>)`
  header.
- `source` — `"llm"` when the brain authored the body, `"rule"` when it fell back to
  the deterministic template (the brain errored or returned empty). **`propose_addendum`
  never raises** — a brain failure degrades to the rule addendum, which still carries
  the band, the direction, and the preserve text, so the pipeline never stalls.

## Why this shape
- **Pure + injectable.** The only outside effect is the `chat_fn` call, and that's an
  argument. Unit tests run with a fake `chat_fn` — no network, no API key, no rubric.
- **Soft tier.** The addendum is *appended* to a persona; it never edits the
  specialist's code or hard contract. A metric that returns to band lets the note be
  retired with zero residue.
- **Direction in, text out.** The rubric owns the "what" and the "which way"; Quill
  owns only the "how to say it so the writer actually does it." That separation is the
  whole point — it keeps the judge and the coach from being the same hand.
