# Creative Roundtable — Replication Blueprint

This document describes how to replicate the Creative Roundtable pattern — built
for **Marlow** in [`scriptwriter/roundtable.py`](../scriptwriter/roundtable.py) — across the
other creative specialists: **Iris** (Art Director), **Cadence** (Audio Designer),
and **Mason** (Composition Engineer).

> **Grounding note.** This blueprint reflects the *actual* roundtable seam in this
> repo, not a generic pattern. The roundtable is a reusable class
> (`CreativeRoundtable` + `RoundtableConfig`) driven by the specialist's OWN
> `chat(system, user) -> str` LLM seam. It is **not** a separate agentic process —
> the three sub-agents (Critic → Researcher → Craftsman) are three independent
> one-shot calls through that one seam, which is how "fresh context" is guaranteed
> (each call is system + user only; `context_proof.history_tokens == 0`).

---

## The pattern, exactly as built

A roundtable run is `CreativeRoundtable(config).review_and_enhance(draft, upstream_intent, project_dir)`
returning `(enhanced_artifact, log)`. It is wired into a specialist by:

1. **Construct a `RoundtableConfig`** with the specialist's persona text + LLM seam:

   ```python
   from roundtable import CreativeRoundtable, RoundtableConfig   # copied into the specialist's dir

   cfg = RoundtableConfig(
       specialist_name="Iris",
       specialist_role="Art Director",
       skill_md=read("SKILL.md"),     # the Critic judges against this
       style_md=read("soul/STYLE.md"),# the Craftsman writes in this voice
       soul_md=read("soul/SOUL.md"),  # identity for both
       llm_chat=llm.chat,             # the specialist's own brain (one seam, shared)
       search_tool=search.web_search, # real DuckDuckGo seam (the Researcher mines it)
   )
   enhanced, log = CreativeRoundtable(cfg).review_and_enhance(
       draft_artifact, upstream_intent, project_dir=pdir)
   ```

2. **Customize the three roles.** The Critic/Researcher/Craftsman prompts in
   `roundtable.py` are written generically (they interpolate `specialist_role`,
   `skill_md`, `style_md`). Per specialist you tune *what the Critic looks for* via
   the **SKILL.md** content and *what the Researcher searches for* via the upstream
   intent's topical hints. The role-specific focus is documented below.

3. **Make it opt-in with a kill switch.** Mirror Marlow's
   `MARLOW_ROUNDTABLE` env flag (`adapters/scriptwriter.py`): default ON, set to
   `0/false/no/off` to skip. Use `<SPECIALIST>_ROUNDTABLE` (e.g. `IRIS_ROUNDTABLE`).

4. **Persist the log.** `review_and_enhance(..., project_dir=pdir)` already writes
   `roundtable_log.json` beside the artifact. The eval system reads it
   automatically (see Part 3) — no extra wiring needed.

5. **Graceful degradation is absolute.** If any sub-agent fails (bad JSON, an
   exception, or the Critic finds nothing), `review_and_enhance` returns the DRAFT
   unchanged and records the reason in `log["error"]`. It **never raises** — a
   review step must never crash the pipeline.

> **One log, one schema.** All four specialists write the same
> `roundtable_log.json` shape (`specialist`, `criticisms[]`, `research_findings[]`,
> `draft_artifact`, `enhanced_artifact`, `diff_summary`, `error`, `context_proof`).
> The eval analyzer keys off these names, so a new specialist plugs in for free.
> One caveat: the built-in `_generate_diff_summary` diffs `scenes[].narration` +
> `hook` (script-shaped). For non-script artifacts (a storyboard, an audio
> manifest, a composition manifest), override `_generate_diff_summary` so
> `diff_summary.scenes_modified` reflects *that* artifact's unit of change —
> otherwise the eval's `craftsman_impact` reads 0 even on a real rewrite.

---

## IRIS — Art Director Roundtable

**Specialist:** Iris (Art Director)
**Engine functions:** `art_engine.design_style()` / `art_engine.build_storyboard()`
**Upstream intent:** `motion_mood_board.json` + `narrative_intent.json` + `thematic_anchor`
**Kill switch:** `IRIS_ROUNDTABLE`
**Roundtable focus:** visual composition quality, layout appropriateness, effect selection

### Critic focus
Judges visual decisions against Iris's Bauhaus principles (STYLE.md):

- **Layout** — "Does this layout serve the information, or is it used because it's available?"
- **Effect selection** — "Does this effect advance the emotional intent, or is it decorative?"
- **Visual hierarchy** — "Does the most important element dominate the frame within 0.5s?"
- **Texture justification** — "Is there a conceptual reason for this texture tied to the thematic anchor?"
- **Signature restraint** — "Is the `#FFD000` highlighter used exactly once, on the single most important element?"

Example criticism (the JSON shape the Critic returns):

```json
{
  "rank": 1,
  "severity": "major",
  "principle_violated": "STYLE.md: 'If you use the highlighter twice, you've used it zero times.'",
  "target_text": "Scene 3 applies highlighter-FFD000 to the statistic. Scene 6 applies it to the CTA.",
  "location": "scenes 3 & 6",
  "diagnosis": "The highlighter appears twice. The signature is diluted — the second use has no power.",
  "impact": "The most important moment in the video has no visual climax."
}
```

### Researcher focus
Searches for visual references, color inspiration, compositional precedents:
film scenes that evoke the target emotion; photographic styles matching the texture
directive; era-accurate palettes from the thematic domain ("1970s NASA mission
control color palette"); cinematographic techniques matching the layout family.

### Craftsman focus
Re-designs the `storyboard.json` (or `style_guide.json`), applying the Critic's
structural feedback + the Researcher's references. Must produce an artifact that
passes its frozen contract (`storyboard.schema.json` / `style_guide.schema.json`).
**Override `_generate_diff_summary`** to diff scene layouts/effects, not narration.

---

## CADENCE — Audio Designer Roundtable

**Specialist:** Cadence (Audio Designer)
**Engine functions:** `audio_engine.record_narration()` / `audio_engine.mix_audio()`
**Upstream intent:** `narrative_intent.json` (TTS pacing, emotion-based EQ, music register)
**Kill switch:** `CADENCE_ROUNDTABLE`
**Roundtable focus:** narration delivery, music selection, SFX placement, mix balance

### Critic focus
Judges audio decisions against Cadence's principles (define them in her STYLE.md):

- **TTS pacing** — "Does the delivery speed match the beat's `pacing_profile`?"
- **Music selection** — "Does the bed's emotional register match the scene's `primary_emotion`?"
- **SFX placement** — "Does the signature SFX land on the exact frame of the narrative beat?"
- **Ducking** — "Does the VO sidechain preserve intelligibility without gutting the music?"
- **Loudness** — "Integrated loudness at −14 LUFS? Any peaks above −1 dBTP?"
- **Silence** — "Is there intentional silence before the critical lines?"

### Researcher focus
Documentary scores matching the emotional arc; sound-design techniques from
similar-tone films; genres historically tied to the topic; SFX libraries for the
thematic domain; TTS delivery references ("the pace and tone of a Werner Herzog
narration").

### Craftsman focus
Re-mixes the `audio_manifest.json` — TTS params, music selection, SFX timing, mix
levels — passing `audio_manifest.schema.json`. **Override `_generate_diff_summary`**
to diff manifest tracks/levels.

---

## MASON — Composition Engineer Roundtable

**Specialist:** Mason (Composition Engineer)
**Engine function:** `composition_engine.compose()`
**Upstream intent:** `motion_mood_board.json` (the direct source of truth for motion)
**Kill switch:** `MASON_ROUNDTABLE`
**Roundtable focus:** animation quality, motion-curve precision, caption timing, polish

### Critic focus
Judges animation against Mason's Motion Purist principles (STYLE.md):

- **Easing** — "Does this curve match the emotion it evokes?"
- **Audio-visual lock** — "Does peak velocity hit on the exact emphasized syllable?"
- **Caption timing** — "Do captions appear with the word and linger ≤0.2s after?"
- **Effect parameters** — "Are durations/overshoots/staggers concrete, or left at defaults?"
- **Motion hierarchy** — "Does the dominant effect command attention without competing?"

Example criticism:

```json
{
  "rank": 2,
  "severity": "major",
  "principle_violated": "STYLE.md: 'urgency → linear or sharp ease-in (mechanical, relentless)'",
  "target_text": "Scene 2 (hook, urgency 9): push-in with default exponential ease-out, 2.0s",
  "location": "scene 2",
  "diagnosis": "Ease-out 'settles into place peacefully' — it contradicts an urgency beat.",
  "impact": "Cognitive dissonance between the visual and verbal channels; urgency is understood, not felt."
}
```

### Researcher focus
Title sequences with a similar emotional tone; motion-graphics breakdowns for
specific effects; easing references per emotion; kinetic-typography examples
matching the pacing profile; timing references (frames per beat at a given tempo).

### Craftsman focus
Re-composes scene animations — easing, durations, caption timing, effect params —
passing `composition_manifest.schema.json`. **Override `_generate_diff_summary`**
to diff per-scene animation params.

---

## Integration checklist

For each specialist, verify:

- [ ] `roundtable.py` exists in the specialist's project directory (copy of the canonical one)
- [ ] `RoundtableConfig` is customized with the specialist's name, role, and persona files
- [ ] The Critic judges against the specialist's **SKILL.md** + **STYLE.md**
- [ ] The Researcher has `search_tool` wired and domain-appropriate query hints
- [ ] The Craftsman writes in the specialist's **STYLE.md** voice and passes the frozen contract
- [ ] `_generate_diff_summary` is overridden for non-script artifacts (so `scenes_modified` is meaningful)
- [ ] The engine calls `review_and_enhance(...)` before returning final output
- [ ] `review_and_enhance(project_dir=pdir)` writes `roundtable_log.json` to the project dir
- [ ] The roundtable is opt-in via a `<SPECIALIST>_ROUNDTABLE` env kill switch (default ON)
- [ ] Fresh context is proven via `context_proof` (`history_tokens == 0` per sub-agent)
- [ ] Any sub-agent failure degrades to the draft (never raises)

---

## Part 3 recap — how the eval system consumes the log

Once a specialist writes `roundtable_log.json`, the eval system reads it with **zero
per-specialist wiring**:

- [`atlas/eval/analyzers/roundtable.py`](../atlas/eval/analyzers/roundtable.py)
  - `analyze_roundtable(project_dir)` → process **diagnostics** (a side channel, not
    rubric-gated): Critic severity distribution + leniency flag, Researcher
    productivity + source-gap flag, Craftsman impact + no-op flag, overall process
    health.
  - `get_coach_context(project_dir)` → the same record reshaped for the coaches.
- [`atlas/eval/inspector.py`](../atlas/eval/inspector.py) attaches the diagnostics to
  the scorecard as `scorecard["roundtable"]` and sets `scorecard["roundtable_analyzed"]`.
- [`atlas/eval/loop.py`](../atlas/eval/loop.py) threads `roundtable_context` through
  `propose_fix → delegate_to_coach → adapter.run_job("propose_addendum", ...)`.
- The coach engines (Quill / Flux) fold the context into their prompt via
  `_roundtable_block(...)`, so a coach can target the exact link that broke.

**Why a side channel, not a rubric band:** the rubric measures OUTPUT quality and is
CEO-owned + frozen. The roundtable record is about HOW the work was made — there are
no `process:*` bands and there must not be. The diagnostics inform the coaches and
the CEO; they never gate a render.

---

## COMPLETE SYSTEM ARCHITECTURE

```
                         ┌────── REFERENCE VIDEOS ──────┐
                         │  Vera 🔬 builds the standard  │
                         │  → reference_rubric (frozen)  │
                         └───────────────┬───────────────┘
                                         ▼
                              ┌─────────────────────────┐
                              │   RUBRIC (read-only)     │
                              │   6 weighted dimensions  │
                              │   + 1 technical floor    │
                              └────────────┬────────────┘
                                           │ measures against
                                           ▼
┌────────────────── CREATIVE PIPELINE ──────────────────────────────────┐
│                                                                        │
│  RESEARCH ──→ TREATMENT ──→ NARRATIVE_INTENT ──→ MOTION_MOOD_BOARD      │
│     │ thematic_anchor   │ poetic vision  │ emotional score │ visual    │
│     │                   │                │                 │ architecture
│     ▼                   ▼                ▼                 ▼            │
│  SCRIPT (Marlow) ◄──────── governed by narrative_intent + mood_board   │
│     │                                                                  │
│     │  ┌─── CREATIVE ROUNDTABLE (internal, fresh context each) ───┐    │
│     │  │  CRITIC → RESEARCHER → CRAFTSMAN  →  roundtable_log.json  │    │
│     │  └──────────────────────────────────────────────────────────┘    │
│     ▼                                                                  │
│  FACTCHECK ──→ ★ GATE (block = un-approvable)                          │
│     │                                                                  │
│     ▼                                                                  │
│  STYLE ─→ STORYBOARD ─→ ASSETS ─→ NARRATION ─→ AUDIOMIX ─→ COMPOSE ─→ RENDER
│            │ Iris               │ Cadence              │ Mason  ★AUTO-GATE
│            │ Roundtable         │ Roundtable           │ Roundtable  ★GATE
└────────────┼────────────────────┼──────────────────────┼──────────────┘
             └── roundtable_log.json from any specialist ─┘
                                                         │
                                                  video.mp4
                                                         │
                                                         ▼
┌────────────────── EVALUATION LOOP ───────────────────────────────────┐
│                                                                       │
│  INSPECTOR ──→ SCORECARD ──→ DIAGNOSE ──→ PROPOSE FIX                  │
│     │              │             │              ├──→ Quill 🖋️ (editorial)
│     │              │             │              └──→ Flux  🎚️ (production)
│     ▼              ▼             ▼                      │              │
│  ANALYZERS:    gate each     credit            COACHING ADDENDA        │
│  audio.py      measurement   assignment:       (markdown only,        │
│  video.py      against the   the ONE failing   soft-tier write)       │
│  text.py       rubric band   property          ▲                      │
│  roundtable.py ◄── side channel: process diagnostics + coach context ─┘
│                  (Critic/Researcher/Craftsman read-out, NOT rubric-gated)
│                                                                       │
│  HOLD OUT:        NOISE FLOOR:        VALIDATION:                      │
│  reject changes   must beat           eval-of-the-eval                 │
│  that regress     natural variance    (pass-good / fail-bad)          │
└───────────────────────────────────────────────────────────────────────┘
```

The roundtable log closes the loop: the coaches no longer see only the final
artifact — they see the internal creative process that produced it, and can coach
the exact link (Critic, Researcher, or Craftsman) where quality leaked.
