# YT-AGENTS — Self-Improvement Enhancement: Decisions & Approach

*A record of what we've decided and why — the motto, the principles, and the method for
making the company improve itself. This document is intentionally about **decisions and
approach only**; it excludes implementation and anything built so far.*

---

## 1. The Motto (North Star)

Build a video agency that **learns what "good" means from reference videos and continuously
tunes itself toward that standard — without ever trading away the reliability that makes its
output trustworthy.**

Everything below serves one goal: better videos, measured against a standard we can define and
check. "Self-improvement" is not a slogan here — it is a disciplined loop with a real measure of
success and hard limits on what it is allowed to change.

---

## 2. How the Company Works (the foundation we build on)

Three commitments define the system and constrain every enhancement:

- **The company metaphor is real, not decoration.** Each agent is an independent employee with
  its own context, memory, and personality. One manager acts as the product owner. The human is
  the CEO — the single principal. A new capability is a new employee, added without disturbing the
  others.

- **Two planes, never mixed.** The language model does *judgment* (what to make, how to respond,
  how to communicate). A deterministic spine does *guarantees* (order, validity, sign-off gates).
  We never move a guarantee into the language model.

- **The manager is a product owner *on top of* a reliable assembly line — not a dispatcher who
  reinvents the line each run.** Who does which *kind* of work is fixed, exactly like stable job
  descriptions are what let a real company ship. What the manager actually decides is the judgment
  around the work: the topic and angle, how to handle a failure, what to advise at a gate, what to
  tell the CEO, how to reprioritize. The moment the manager is allowed to reorder the line or skip
  a check, reliability is gone — so we keep the factory deterministic and make the manager smart
  *around* it.

---

## 3. The Enhancement: A Self-Improvement Department

We are adding an **internal audit + continuous-improvement function**, built from the same
patterns the company already uses — new employees, a deterministic process, typed reports, and a
CEO sign-off gate. It is additive and obeys the two planes.

**The loop:** Observe → Diagnose (find the root cause) → Propose → CEO approves → Verify (and loop
until the result passes).

**How the work divides:**

- **Setting the standard.** A *Reference Analyst* learns what "good" looks like from reference
  videos and writes it down as a measurable rubric. She is the measurer, not a magician — she
  defines the target, she does not by herself make the videos better.
- **The improvement loop.** An *Inspector* measures the system's own output against that standard;
  a *Diagnostician* traces a shortfall back to its origin; a *Coach* — which we've since decided to
  split into two domain coaches (see §9) — proposes and applies the fix; a *Verifier* re-checks
  against the standard and guards against regressions.
- **The CEO gate.** Nothing structural changes without the CEO's sign-off — the same pause-and-
  approve mechanism the company already uses, applied to a new purpose.

---

## 4. The Two Non-Negotiables

The whole enhancement stands on these two principles.

1. **Evals are the foundation.** A self-improvement loop with no ground-truth measure of "good" is
   just two language models agreeing with each other — confident, and possibly wrong. The eval *is*
   the rubric. Without it, "improvement" is the system talking itself into changes with no way to
   know if they helped.

2. **The improver must be *less* privileged than the guarantees.** A system that can edit its own
   success criteria or its own guardrails will "improve" itself by deleting them — because that is
   the easiest way to make a failure disappear. Therefore the improver may tune freely within
   bounds, but can **never, without a human**, change its own success bar, the contracts, the
   deterministic spine, or the gates. It is strictly less privileged in exactly the dimensions that
   carry guarantees. This asymmetry is what makes self-improvement safe instead of self-destructive.

---

## 5. What "Fix" Is Allowed to Mean

A deliberate gradient, from autonomous to forbidden:

- **Soft — auto-applied, within bounds, only when the eval improves:** prompts, persona and voice,
  playbooks, thresholds inside a human-approved range, wording.
- **Hard — proposed as a change, a human applies it:** pipeline logic, contracts, gate behavior.
- **Forbidden — never:** silently rewriting guardrails, loosening a contract, or removing a failing
  check to make it pass.

Most of the improvement value lives in the soft tier — which is also the safe, instantly testable,
reversible one.

---

## 6. "Training the Manager," Decoded

Our central worked example, made concrete and safe:

- **"Train him"** = evolve his prompt, persona, or playbook — the *text he runs on*, not his code.
- **"He delivers"** = he passes an eval the CEO owns.
- **"Loop until fixed"** = iterate the text against that eval, with a hard cap on iterations and
  cost, escalating to the CEO if it will not converge.
- **The lock:** the eval and the success bar belong to the CEO, never to the improver — so it
  cannot pass by gaming the test.

---

## 7. Defining "Good": The Rubric

- **You cannot optimize against a finished video.** A video file is opaque pixels and audio
  samples; you cannot compare two of them into "the scripts are weak." Quality must be decomposed
  into measurable properties — and that decomposition *is* the rubric, and the rubric *is* the eval.

- **We build the rubric *from* reference videos.** Two sources feed it: technical analysis of the
  videos (picture and sound) and a plain-language interview with the CEO. The method is the
  designer's — analyze the references, ask the CEO simple questions wherever only taste can decide,
  and write the answers down in technical terms. The CEO speaks plainly; the standard is recorded
  precisely.

- **Two kinds of signal.** *Objective* signals are deterministic and not gameable (pacing, colour,
  motion, loudness, speech cadence, structure). *Judged* signals are assessed against the reference
  exemplars, are noisier, and need discipline (style, hook strength, overall feel).

- **Every target is a value plus an acceptable band.** The band is precisely what the improvement
  loop aims to land inside — it turns a vague "make it good" into a number a machine can chase.

- **More references tighten the bands** to the properties the good videos *share* — the common DNA —
  rather than the quirks of any single one.

- **An honest ceiling.** The rubric can pull the system to the top of *its own* design space, not
  into a different one. We deliberately capture properties the generator can actually control, not
  artifacts of how a particular reference happened to be produced.

### What the standard can and can't capture

A finished reference video shows the *result*, not the *recipe* — we can measure its properties but
cannot read back the decisions that produced it. So the standard is built in honest layers:

- **Measured directly** — pacing and cut rhythm, colour and palette, motion energy, loudness and
  speech cadence.
- **Described, then confirmed** — visual style, type style, motion and effect *character*, imagery
  type, mood, and layout. The vision layer *names* these in technical terms precisely so the CEO
  doesn't have to; the interview confirms them. This is the deliberate answer to "the things I
  don't know the names for."
- **In scope, to be added** — the standard should also cover the *editorial* dimension (the script's
  words and content, not just the narration's cadence) and the *character of transitions* between
  shots. These are properties of "good" we intend to capture beyond the first measurable set.
- **Out of reach by nature** — the actual asset sources and licences, exact font files, and the
  editor's named effect choices are not physically present in a finished video. For these the
  standard holds target *properties and descriptions*, never the original recipe.

The line at the bottom matters: it keeps expectations honest about what learning-from-references
can and cannot deliver.

---

## 8. The CEO Interview Is Visual

Where seeing helps, the CEO should choose by *looking*, not by answering blind. Taste questions
about visuals — layout, colour, type, pace, the feel of a transition — are rendered as **visual
choices** on the company's existing web surface: small mockups, swatches, type samples, side-by-side
demonstrations, and a live preview of a composition. The CEO picks; the pick becomes a recorded
target.

**How we've decided to build it:** from a fixed library of *parameterized* question widgets that the
standard-setter fills with data — **not** as fresh interface code generated per question.

**A heavier alternative we considered and set aside:** a runtime "frontend developer" employee that
writes and runs new interface code for each question. We declined it for the reasons that run
through this whole document — it would put a language model generating and executing code inside the
trusted runtime (a real safety surface) and break the determinism we protect everywhere else, and it
would duplicate rendering the company already has. Creating *new* widgets is a development-time task;
serving them to the CEO is a runtime one. The two stay separate.

This is simply the visible form of the designer interview — the means by which the CEO's plain-
language taste becomes precise, recorded targets.

---

## 9. The Coaching Function — Two Domain Coaches Who Keep Learning

No one is expert in everything, so the coaching is split by domain, mirroring the rubric's own
division between content and craft:

- an **Editorial / Content coach** over the pre-production side — topic choice, research, scripts,
  and asset relevance; and
- a **Production / Craft coach** over the production side — visual style, storyboard, audio, and
  composition.

Each coach **researches its field** for current best practice and new technique, the way a professor
keeps learning in order to teach what's current. But there is one firm rule: **research only ever
produces hypotheses; the rubric decides what's kept.** A coach never adopts an outside "best
practice" on faith — anything it learns is tried and measured against the standard before it changes
how the team works. Research widens what to try; the eval prunes it.

The two non-negotiables still bind the coaches: they tune the soft tier only — never the guardrails
or their own success bar — and their self-study runs on a hard budget.

This is a *later* hire. The order is: prove one basic, single coaching loop first; then split it into
the two domain coaches; then add the research-and-self-study dimension. Building professor-coaches
before the basic loop works is optimizing with an unproven optimizer.

---

## 10. The Path We've Chosen

Establish the standard first, then build the machinery that chases it — a loop with no target is
meaningless. In order:

1. **Establish the standard** — the rubric, built from reference videos, with the CEO interview made
   *visual* where seeing helps.
2. **Prove one basic improvement loop** end-to-end against that standard, before trusting it with
   anything more.
3. **Split the coaching** into the two domain coaches.
4. **Give the coaches their research-and-self-study dimension** — bounded, and always tested against
   the standard.

Throughout, two facts hold: the standard-setter *measures*, the loop *improves*; and everything is
bounded by what the generator can physically produce. "Hand the company a video and it improves
itself" is the whole of this path working together, not any single step.

---

## 11. Working Method (principles we hold throughout)

- **The real code is the ground truth.** Written docs lag the system; we trust the working
  implementation over the prose.
- **Changes are additive.** Adding a capability never touches the spine, the contracts, the gates,
  or existing employees.
- **The CEO stays in the loop at the decision points** — the same sign-off gate the company already
  uses, plus the rubric-building dialogue, now visual where seeing helps.
- **Cost discipline.** Every loop carries a hard budget, and expensive operations confirm before
  running — the economics demand it.
- **Guard against overfitting.** Keep a held-out set the loop never optimizes against, plus human
  spot-checks, so "improvement" means real generalization, not memorizing the test.
- **Build in the right order** — the foundation (the standard) before the machinery that uses it.

---

## 12. Dangers We're Designing Against

- **The system games its own measure or deletes its guardrails** → the privilege asymmetry, plus a
  success bar that only the CEO owns.
- **The loop overfits the rubric** → held-out scenarios and human spot-checks.
- **A coach adopts an outside "best practice" without testing it** → research is only ever a source
  of hypotheses; the rubric decides what is kept.
- **Runaway cost** → hard iteration and cost caps, with escalation when a loop won't converge.
- **"Evaluation" that is just the system grading itself** → the eval must have an external anchor
  (the rubric derived from real reference videos), never one model rating another with no ground
  truth.
- **A language model generating and running code inside the trusted runtime** → visual interaction
  is served from a fixed, parameterized widget library; creating new widgets stays a development-
  time task, kept out of the runtime.

---

## Related & Parked

- The **shared-infrastructure refactor** fits this direction cleanly — a common "HR/IT department"
  for employees who keep their own identity and memory — and remains worthwhile.
- Several **pre-existing issues** were flagged as separate from this track and are acknowledged but
  not part of it: independent fact-checking, a semantic "does the visual match the narration" check,
  and the external-tool and brain-economics risks. They are parked, not forgotten.
