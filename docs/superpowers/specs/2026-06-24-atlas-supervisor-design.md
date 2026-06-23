# Atlas as autonomous supervisor — design

**Date:** 2026-06-24
**Status:** approved design, ready for slice-by-slice implementation
**Branch:** `control-room`

## Vision

Make **Atlas the single brain** for all production work. No process is triggered
directly anymore: every action — from the dashboard *or* from chat — becomes a **request
to Atlas**, and Atlas decides what to do, delegates each task to the right specialist,
handles failures itself, and **only interrupts the CEO on genuinely critical decisions**.
The dashboard and chat become two views of **one Atlas engine over shared state**.

This does NOT discard the reliability the belt already has. The deterministic dispatcher
and spine remain the **reliable hands** (station single-occupancy, the in-flight
concurrency cap, on-disk persistence, crash-recovery, contract validation, and the
never-ship-unverified guarantee). Atlas is layered on top as the **decision-maker** at the
points that need judgment.

Chosen architecture: **Approach 1 — Atlas as supervisor over the reliable belt.**
(Rejected: per-video agent loops that re-implement scheduling — too much rework/risk; one
global planner — a bottleneck that hurts multi-video throughput.)

## Agreed policy decisions

1. **Render gate → autonomous under a budget rule (option B).** Atlas self-approves the
   final render when the render plan is under the CEO's threshold (e.g. est. cost < $X /
   runtime < N min / short-form format); above it, Atlas escalates with a **HyperFrames
   draft-preview card** so the CEO decides render-or-kill.
2. **Fact-check / quality block → bounded auto-fix (option A), default 2 attempts.** Atlas
   delegates a fix (Marlow revises the flagged claims) and re-runs the check up to 2×;
   the 3rd block escalates. It **never ships** a video that fails the fact-check.
3. **Unified path → same engine, two views (option A).** Dashboard buttons and chat both
   drive one Atlas engine over the same on-disk state + event ring. Not a single literal
   transcript; the dashboard stays a fast cockpit whose buttons are requests to Atlas.

## Multi-video concurrency

Preserved exactly as today: the dispatcher's global `max_in_flight` cap runs several
videos down the belt at once; per-station single-occupancy keeps one video per station.
Atlas decisions are **per-video, at that video's decision points** — independent bounded
calls, not one serialized brain — so the design scales to many concurrent videos
(the reason Approach 1 beats a global planner here).

## Architecture

### 1. The supervisor seam

Today `Dispatcher._on_result(slug, result)` encodes a fixed failure policy
(transient→retry, deterministic→park, blocked→wait). Replace it with a call to Atlas:

```
atlas_decide(slug, result, context) -> Decision
```

Invoked whenever the spine returns at a decision point (stage failed, fact-check blocked,
render pending, stage finished). Atlas returns **one Decision from a bounded, validated
set** — the LLM proposes, but may only pick a legal move:

| Decision | Meaning |
|---|---|
| `PROCEED` | nothing wrong; continue down the belt |
| `RETRY_STAGE(stage)` | re-run a failed station (e.g. transient hiccup) |
| `FIX_AND_RERUN(stage, instructions)` | delegate a fix to the responsible specialist/coach, then re-run from that station |
| `RERUN_FROM(stage)` | send the video back to an earlier station (e.g. back to research) |
| `APPROVE_GATE(gate)` | self-approve a gate, **within policy only** |
| `ESCALATE(reason, payload)` | call the CEO |
| `KILL(reason)` | abandon the video (e.g. genuinely unverifiable topic) |

The **dispatcher executes** the Decision with its existing reliable mechanics: the same
reset-and-re-run path as the Re-run button (`rerun`/`_reset_failed_stage`), `resume()` for
a gate approval, park-and-emit for an escalate. Atlas decides; the dispatcher executes.

### 2. Bounded autonomy (enforced in the executor, not trusted to the LLM)

Defense-in-depth — the policy is structural, so even a hallucinating Atlas can only ever
escalate, never ship junk / overspend / loop:

- **A fact-check block can never be approved away.** `APPROVE_GATE(factcheck)` is not a
  legal decision; the only moves on a block are `FIX_AND_RERUN` / `ESCALATE` / `KILL`.
- **Auto-fix is counted.** A per-video counter caps fact-check `FIX_AND_RERUN` at 2
  (configurable); the 3rd block forces `ESCALATE` regardless of the LLM's choice.
- **Render budget is enforced in code.** `APPROVE_GATE(render)` is honored only if the
  render plan is under the CEO's budget rule; over budget → the executor converts it to
  `ESCALATE`.
- **Per-video decision budget.** A cap on total Atlas actions per video; on reaching it,
  Atlas escalates rather than spinning.

### 3. The unified request path

- New internal entry point `atlas.handle_request(intent, ...)` for typed intents:
  `make_video`, `rerun`, `cancel`, `answer_escalation`, `ask`.
- New endpoint `POST /api/atlas/request`. Dashboard buttons (Generate, Re-run, gate
  replies) post here instead of calling `/api/trigger | /api/retry | /api/rerun`
  directly. Atlas decides, then drives the dispatcher. The old endpoints become
  **internal** (only Atlas calls them).
- Chat already routes to Atlas. Chat + dashboard converge on **one Atlas engine** over the
  same on-disk state and the same event ring (audit feed).
- Atlas's per-video decisions run in the dispatcher's background worker context (preserves
  multi-video concurrency); the chat path is the same Atlas for direct conversation. Both
  share the specialist registry.

### 4. The escalation surface

When Atlas `ESCALATE`s, the video parks and the CEO is pulled in via:

- **Needs-You tray** (already on the dashboard) — the escalation with Atlas's reason and a
  one-click path to the decision view.
- **Render decision = the HyperFrames card** — for an over-budget render, the card shows
  the per-scene HyperFrames draft frames (already produced at compose, served by
  `/api/media/{slug}/draft/{rel}`) + the render plan (scenes, runtime, est. cost). Actions:
  **Approve render** / **Kill**.
- **Fact-check escalation** — shows the flagged/unverifiable claims + what Atlas already
  tried, with **Kill** / **Guide** (free-text fed to the next fix attempt).
- **Audit plane** — every Atlas decision logged to `project.json` history + the event ring
  with `initiator="atlas"` (distinct from `ceo`/`chat`/`dispatcher`). Optional push
  notification for escalations.
- The CEO's reply (dashboard *or* chat) returns to Atlas as `answer_escalation`; Atlas
  resumes deciding.

## Error handling & safety

- Hard guarantees stay structural (Section 2).
- **Atlas-decision failure** (LLM error/timeout) falls back to a **safe default decider** =
  today's deterministic policy (transient→retry once, else park+escalate). An LLM outage
  degrades to *current behavior*, never to unsafe behavior.
- The Decision is **schema-validated**; a malformed/illegal decision is coerced to
  `ESCALATE`.
- **Crash-recovery** unchanged: `reconcile_interrupted` still parks zombies on restart;
  an interrupted Atlas decision re-derives from disk.

## Testing

- `atlas_decide` is **injectable like `produce_fn`** today → dispatcher tests use a fake
  decider, fully deterministic, no LLM. Cover: each Decision → correct dispatcher action;
  budget enforcement (3rd fact-check block escalates regardless of decider);
  render-over-budget converts approve→escalate; illegal decision → escalate; safe-default
  fallback on decider error.
- API tests: a Generate/Re-run button → Atlas request → video on the belt.
- One e2e: Generate → escalation card → approve → render.

## Build slices (each independently shippable, TDD)

1. **Supervisor seam** — `atlas_decide` + executor replacing `_on_result`, with the
   **safe default decider** (behaviour-identical to today). De-risks the plumbing with
   zero behavior change. Inject the decider so the dispatcher stays testable.
2. **Real Atlas decisions** — wire the LLM decider for failures + the bounded fact-check
   auto-fix (FIX_AND_RERUN delegating to Marlow/coaches, counted to 2).
3. **Render budget policy + HyperFrames escalation card** — budget rule in settings;
   `APPROVE_GATE(render)` honored under budget, else the draft-preview card.
4. **Unify the dashboard request path** through Atlas; old trigger/retry/rerun endpoints
   become internal; shared engine/state.
5. **Escalation surface polish** — Needs-You tray entries, `initiator="atlas"` audit
   plane, optional push notification.

Spec covers the full design; implementation proceeds **one slice at a time**, each as its
own plan, starting with Slice 1.

## Out of scope (for now)

- A single literal shared chat transcript across dashboard + chat (we chose shared
  engine/state, not one transcript).
- Atlas planning across the whole queue (global planner) — rejected for throughput.
- Changing the specialist engines themselves beyond what a `FIX_AND_RERUN` already calls.
