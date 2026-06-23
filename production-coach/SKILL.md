# SKILL.md — The Production Coaching Method

This is the method Flux follows on every coaching job. The output of the whole
method is a single **soft-tier coaching addendum**: a few imperative sentences
appended to a craft specialist's persona/prompt that nudge ONE diagnosed quality
metric into its rubric band on the next render — without regressing the sibling
properties you're told to preserve.

This file is the engine's *method*, not a voice. It says HOW a diagnosed metric
becomes an addendum. Who Flux is and how they talk lives in `soul/` — never here.

## The boundary you never cross (read this first)
Flux **authors text only.** Flux does NOT:
- read or write the rubric,
- decide pass/fail,
- measure anything,
- invent a goal beyond the metric named,
- regress a sibling property it's told to preserve.

The **direction to move a metric is GIVEN to Flux as data** — it is decided by the
CEO-owned rubric, upstream. Flux respects that direction exactly. Its only job is to
turn that diagnosis into persuasive, domain-aware coaching a craftsperson can act on.

## The domain Flux owns
Flux is the **PRODUCTION / CRAFT** coach. It mirrors the rubric's craft side —
dimensions **G3 (visual craft)**, **G5 (audio quality)**, and **G6 (AV coherence)** —
across these stages (`COACHED_STAGES`, which must match the atlas diagnose stages):

| stage | the craft specialist Flux is coaching |
|------------|----------------------------------------|
| `style` | the art director (visual style, palette, type) |
| `storyboard` | the storyboard (shot rhythm, layout variety) |
| `narration` | the narration/VO (delivery, modulation, intelligibility) |
| `compose` | the composition engineer (Mason — motion, pacing, restraint) |
| `audiomix` | the audio engineer (loudness, ducking, mix balance) |
| `render` | the final render (AV coherence, sync) |

`coaches_stage(stage) -> bool` is the membership check; the writing side checks the
stage prefix of a `band_id` (e.g. `compose:motion_energy` -> stage `compose`).

Flux thinks in: pacing and cut rhythm, motion energy and modulation, layout variety,
**effect discipline (the single signature beat)**, type, loudness and ducking,
intelligibility, and audio-visual coherence.

## Inputs (what propose_addendum gets)
`coach_engine.propose_addendum(*, band_id, direction, preserve="",
measured_value=None, owner="", chat_fn=None) -> dict`

- `band_id` — the metric to move, e.g. `compose:motion_energy`. The text before the
  `:` is the stage; it should be one Flux coaches.
- `direction` — the change the rubric decided, e.g. `"RAISE it to about 10"`. Flux
  honours this exactly; it never argues the rubric or flips the direction.
- `preserve` — a sentence naming sibling properties to keep in band while fixing this
  one (e.g. "Keep effect_discipline and layout_variety in band."). Appended verbatim
  so the fix can't break a neighbour.
- `measured_value` — the current value (context for the brain; never asserted as a
  verdict by Flux).
- `owner` — the craft specialist being coached (e.g. `Mason`), for addressing.
- `chat_fn` — the INJECTED LLM seam `(system, user) -> str`. Defaults to `llm.chat`
  (the subscription Claude brain). Tests pass a fake to run **offline, no network**.

## The method
1. **Read the diagnosis, not the rubric.** Take `band_id` + `direction` as given. Do
   not re-derive, re-measure, or second-guess them.
2. **Build the brain prompt.** The system prompt is the `COACHING_PHILOSOPHY` plus a
   strict instruction: write ONLY the addendum, 2–4 crisp imperative sentences in
   markdown; respect the band and direction exactly; invent no new goals; keep the
   named sibling properties in range.
3. **Author.** Call `chat_fn(system, user)`. If it returns non-empty text, wrap it in
   the standard header and mark `source="llm"`.
4. **Fail safe — always.** `propose_addendum` **never raises.** If the brain errors or
   returns empty, Flux falls back to a deterministic **rule** addendum (built from the
   same `band_id`, `direction`, and `preserve`) and marks `source="rule"`. Either way
   you get a usable addendum.

## Output contract (what propose_addendum returns)
A dict, exactly:

```python
{
  "band_id":   "compose:motion_energy",      # the metric targeted
  "direction": "RAISE it to about 10",       # echoed, honoured exactly
  "domain":    "production",                  # always — this is the production coach
  "owner":     "Mason",                       # who it's for ("" if unspecified)
  "addendum":  "## Coach note (Flux · production · target compose:motion_energy)\n…\n",
  "source":    "llm"                          # "llm" = brain-authored, "rule" = fallback
}
```

The `addendum` is always wrapped with the header
`## Coach note (Flux · production · target <band_id>)` so a downstream adapter can
recognise, attach, and later strip it. It is **soft-tier**: an addendum appended to a
specialist's prompt, never a hard edit to the engine.

## What the next agent receives
The atlas adapter takes the returned `addendum` and appends it to the named craft
specialist's persona/prompt for the next render. Because Flux only ever produces
text — and never touches the rubric, the measurement, or the pass/fail call — the
coaching loop stays auditable: the rubric diagnoses, Flux nudges, the specialist
renders, the rubric re-measures. Flux is one clean, replaceable link in that loop.
