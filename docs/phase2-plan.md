# Phase 2 — Plan: Calibrate → Harden → Split Coaching → Research

> **Read this with** `docs/phase1-report.md` (what's already built and proven),
> the two design docs (`self-improvement-enhancement-decisions.md`,
> `rubric-decomposition.md`), and `PROJECT_CONTEXT.md`. **The code is the ground
> truth** — verify every claim below against the actual files before acting.

This plan is **bounded and ordered**. The design docs mandate the sequence and
warn against shortcuts ("prove one basic loop before trusting it with more";
"building professor-coaches before the basic loop works is optimizing with an
unproven optimizer"). Do the steps in order. Do not start step *n+1* until step
*n*'s Definition of Done is met.

---

## 0. Where Phase 1 left off (the foundation you build on)

Built, proven on real data, 218 tests green, strictly additive:

- `atlas/rubric/` — frozen, CEO-owned standard. `rubric.json` = 6 weighted
  global dims + hard floor `F` + **42 per-property bands** + judged-reference
  pool + CEO-anchor stub. Loader is deeply immutable with **NO write path**.
- `atlas/eval/` — `types.py` (Measurement, EvalContext), `analyzers/{audio,video,text}.py`
  (deterministic, no LLM), `judged.py` (ensembled, variance-tracked),
  `tracking.py` (append-only + `noise_floor()`), `rollup.py`, `inspector.py`
  (CLI `python -m eval.inspector <dir>`), `validation.py` (eval-of-the-eval),
  `diagnose.py` (credit assignment), `loop.py` (bounded loop + write boundary).

**Key Phase-1 facts you will use:**
- Bands are **PLACEHOLDERS** — that is the #1 thing Phase 2 fixes (step 1).
- Judged **noise floor measured**: `overall_polish` σ≈0.23 → an improvement must
  clear ≈2σ≈0.47 to beat noise. `hook_strength` σ=0.0.
- The loop currently uses an **in-atlas placeholder coach** (`loop.propose_fix`)
  — step 3 replaces it with real sibling-agent coaches.
- `source_engine.relevance` is reused by `text.py`; `reference-analyst/` (Vera)
  is a working sibling that builds `reference_rubric`.

---

## 1. Non-negotiables (carry forward, unchanged)

1. **Additive only.** No edits to `pipeline.py` stage order, `contracts/*.schema.json`,
   the two human gates, `loader.py`, or any sibling engine's behavior. New
   capability = new code package and/or a new sibling agent project + one
   `registry.py` entry + one adapter. No orchestrator edits.
2. **Two planes.** Objective measurement = deterministic code (no LLM). LLM only
   for the explicitly judged props and for the coaches' *proposals* (never for a
   guarantee).
3. **Privilege asymmetry, structural.** The rubric/contracts/spine remain
   read-only to all improvement code. The improver writes ONLY soft-tier
   persona/prompt/playbook text, enforced by `loop.apply_soft_change`. A
   change to the rubric (e.g. calibrated bands) is a **CEO-owned human edit** —
   produce a *proposal*, a human applies it.
4. **Billing.** Do NOT set `ANTHROPIC_API_KEY` (diverts off the subscription).
   Use each agent's `llm.py` seam / `claude_agent_sdk`. Prefer current Claude
   models; read each agent's `llm.py` for IDs.
5. **Offline, deterministic tests.** Engines take an injectable seam; unit tests
   never hit the network/LLM. Live-QA does the real runs.
6. **Use the root `venv`.** Don't run the web UI and terminal Atlas at once
   (shared `chat_state.json`).

---

## 2. The architectural decision already made (don't relitigate)

- **Measurement infrastructure stays deterministic code in `atlas/`** (rubric,
  inspector, analyzers, tracking, rollup, validation, diagnose). These are the
  guarantees plane / the "HR-IT department" — NOT employees.
- **Improvers that exercise judgment are separate sibling agent projects** (their
  own engine/llm/soul/memory), wired via one registry entry + one adapter — like
  the existing fleet and like Vera. This is step 3.

---

## 3. The four steps

### STEP 1 — Calibrate the bands from references *(DO THIS FIRST)*

**Why first:** optimizing against placeholder bands is the exact failure the docs
warn about. Until the bands are real, "improvement" is meaningless.

**The subtlety — two kinds of band, two sources:**
- **Media-measurable bands** can be derived by running the EXISTING analyzers on
  the reference video(s): `audiomix:*`, `compose:motion_energy`, `compose:cut_rhythm`,
  `render:final_runtime/final_loudness/final_peak`. Run `eval/analyzers/audio.py`
  + `video.py` on the reference media so the band is in the SAME units as the
  scoring instrument (apples-to-apples). Cross-check against Vera's
  `reference_rubric` targets (pacing/motion/color/audio).
- **Structural / editorial bands CANNOT be read from a finished reference video**
  (you can't recover the script, storyboard, or asset manifest from an mp4):
  `script:*`, `storyboard:*`, `assets:*`, `narration:*`. These come from (a) the
  **CEO visual interview** (design doc §8 — parameterized widgets, see the
  cross-cutting workstream) + (b) Vera's `ceo_prefs`/`open_questions` + (c) the
  distribution across the 4 completed projects as a sanity prior. CEO confirms.

**Honest constraint:** only **1 reference video** exists today
(`ReferanceVideos/61383987.mp4`). Bands from one video are loose ("more
references tighten the bands"). Either gather more references or lean on the CEO
interview; **state the confidence per band**.

**Build:**
- `atlas/eval/calibrate.py` — a READ-ONLY *proposer*: runs the media analyzers on
  references + reads Vera's `reference_rubric`, and emits a **proposed band diff**
  (`rubric.proposal.json` or a human-readable diff) — it must NOT write
  `rubric.json` (privilege asymmetry). Each proposed band carries value, derived
  band, source ("media-measured" | "ceo-interview" | "prior"), and a confidence.
- CEO reviews → a human applies the diff to `atlas/rubric/rubric.json` (flip
  `placeholder:false` on calibrated bands).
- Re-run `validation.validate_instrument()` (must stay all-pass) and
  `validation.report_reference_fit()` on the references — **references should now
  pass** their calibrated bands; a reference that still fails is a real
  instrument problem to fix.

**DoD:** calibrated `rubric.json` (human-applied), placeholder flags cleared on
calibrated bands, instrument still sound, references pass, a calibration report
in `docs/`. Readiness: **HIGH** (rubric, validation harness, Vera all exist).

---

### STEP 2 — Harden the one loop (before trusting it more)

**Build:**
- **Held-out set.** Designate optimization vs held-out projects from the 4
  completed ones (e.g. optimize on `coffee-vs-tea` + `headphones`; hold out
  `gpt-4o-…comparison` + `the-first-job…jensen`). The loop NEVER measures against
  held-out during optimization; the Verifier checks generalization on it.
- **Beat-the-noise-floor gate.** Wire `tracking.noise_floor()` into `loop.decide`:
  a JUDGED target's accepted change must move the metric by **> 2σ** (use the
  measured floor); an OBJECTIVE target must cross its band with margin. This is
  what separates a real win from LLM jitter.
- **Verifier.** After an accept, re-run the scorecard (`inspector.run_inspection`)
  on the held-out set; reject if it regresses there (generalization, not
  memorization).
- **Human spot-check at accept** (gate-style, reuse the pause/approve idea): a
  proposed soft change surfaces to the CEO before it persists.
- **Demonstrate a REAL accept** on a now-calibrated band (Phase 1 only showed
  real rejects against the tight placeholder band).

**DoD:** loop requires noise-floor-beating + no held-out regression + (optional)
CEO sign-off; one real accepted improvement demonstrated end-to-end and recorded
in the tracking store; report it. Readiness: **HIGH** (loop/tracking/diagnose/
inspector exist; needs the held-out gate + noise gate + a real accept).

---

### STEP 3 — Split coaching into two sibling agents

**Build two NEW sibling agent projects** (mirror the `scriptwriter/` skeleton:
`*_engine.py`, `run.py`, `chat.py`, `llm.py`, `chat_state.py`, `compaction.py`,
`SKILL.md`, `soul/{SOUL,STYLE}.md` + examples, `tests/`, `requirements.txt`):
- `editorial-coach/` — pre-production domain: topic/research/scripts/asset
  relevance (owns G2 editorial + the script/asset side).
- `production-coach/` — production domain: visual style/storyboard/audio/
  composition (owns G3/G5/G6 craft side).

**Wire each** with one `registry.py` `AgentEntry` + one `atlas/adapters/<coach>.py`
(no orchestrator edits — tools generate from the registry). Each coach's *job* =
"propose a soft-tier persona/prompt addendum for stage X to move band Y into
range," respecting the rubric direction (the band decides; the coach proposes).

**Refactor `loop.py`** so `propose_fix` **delegates** to the right coach
(`diagnose` already attributes a shortfall to one owner → map owner to
content/craft coach) via its adapter, instead of the in-atlas placeholder. The
soft-tier write boundary and the coordination rule (`diagnose` flags multi-stage
→ never optimize a contested dimension with two coaches blind) stay enforced.

**DoD:** two coach agents in the registry + `/agents`, loop delegates to them, the
real accept from step 2 now runs through a real coach, tests for each coach +
the delegation path. Readiness: **MEDIUM** (diagnosis ready; coaches are new
employees, but the pattern (registry+adapter) and the write boundary exist).

---

### STEP 4 — Add bounded research / self-study

**Only after step 2 proves the optimizer.** Give each coach a research seam (web
search like Sage's) that produces **hypotheses only**; the rubric prunes. Hard
per-loop budget + iteration cap; expensive ops confirm first. A researched
"best practice" is adopted ONLY if it beats the eval **on the held-out set**.

**DoD:** bounded research dimension on at least one coach; a test/demo showing a
researched hypothesis is accepted only when it beats the held-out scorecard;
budget + escalation enforced. Readiness: **LOW by intent** (build last).

---

## 4. Cross-cutting workstream — the CEO visual interview (supports step 1)

Design doc §8: taste-based bands (layout, colour, type, pace, transition feel)
are set by the CEO **looking and choosing**, via a **fixed library of
parameterized question widgets** on the existing Chainlit web surface — swatches,
type samples, side-by-side demos, a live composition preview. **NOT** runtime-
generated UI code (that would put an LLM generating code in the trusted runtime —
explicitly declined in the docs). Creating widgets is a dev-time task; serving
them is runtime. Scope this as its own additive workstream feeding step 1's
structural bands; it can lag step 1's media bands.

---

## 5. Suggested decomposition / parallelism for Phase 2

- Step 1 (calibrate) and the step-3 coach **scaffolding** can be scaffolded in
  parallel, but the loop refactor (step 3) DEPENDS on calibrated bands (step 1)
  and the hardened loop (step 2) to be meaningful → keep 1 → 2 → 3 → 4 sequential
  at the *integration* points.
- Within a step, fan out independent pieces to subagents with isolated context
  (e.g. the two coach projects are independent → two parallel subagents, each
  owning one sibling dir). Cap concurrency ~3–5. Serialize anything touching
  `registry.py`, `loop.py`, or `rubric.json`.
- Keep the main thread lean: subagents return distilled module + test report +
  risks, not transcripts.

---

## 6. Live-QA expectations (every step)

- Run new code against REAL data (the references for step 1; the held-out
  projects for step 2; a real coach proposal → real re-measure for step 3).
- Re-measure the noise floor if the judged prompt/pool changes.
- Keep the full pytest suite green (218 + new) and additive (only new files +
  the human-applied `rubric.json` calibration + `.gitignore`).

---

## 7. First action for the new session

Read ground truth (this plan + `docs/phase1-report.md` + the two design docs +
`PROJECT_CONTEXT.md` + `atlas/eval/` + `atlas/rubric/` + `reference-analyst/`),
write a short PLAN + todo list confirming/adjusting the above, then **START WITH
STEP 1 (calibration)** — produce a band-calibration proposal from the reference
video(s) + Vera, surface it for CEO approval, and re-validate the instrument. Do
not build coaches (step 3) or research (step 4) until steps 1–2 are done.
