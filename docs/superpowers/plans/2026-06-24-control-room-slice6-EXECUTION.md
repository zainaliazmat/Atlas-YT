# Slice 6 — Autonomous Execution Instructions (for Claude Code)

> **A future Claude Code session: read this first, then execute the plan end to end, autonomously.**
> You have standing approval to run the whole slice without pausing for confirmation, as long as you
> stay inside the guardrails below and keep every gate green. Make the best decision and proceed;
> record any judgment call in the commit body. Do **not** ask the user between tasks.

## What to build

**Slice 6 of the YT-Agents Control Room** — the Coaches view + the T4 proposal surface + the Echo
shell. Everything is specified.

- **Spec (the what + why):** `docs/superpowers/specs/2026-06-24-control-room-slice6-coaches-echo.md`
- **Plan (the how — 9 TDD tasks with full code):** `docs/superpowers/plans/2026-06-24-control-room-slice6-coaches-echo.md`

Read both end to end before writing code. The plan's task order **is** the build order.

## How to execute

1. **Use the `superpowers:subagent-driven-development` skill.** Dispatch one fresh subagent per task
   (Tasks 1→9, in order). Each subagent implements exactly one task from the plan (its failing test →
   minimal impl → passing test → commit), then returns.
2. **Two-stage review between tasks (you, the orchestrator):**
   - Run the task's own tests + the relevant suite; confirm green before moving on.
   - Skim the diff for guardrail violations (below). If a subagent drifted, send it back with the
     specific correction; do not accept a red or guardrail-breaking task.
3. **Commit per task** with the message already given in each task's Step 5. Stay on branch
   `control-room` (do not create a PR unless the user later asks).
4. After Task 9, run the **full regression** and report a single completion summary.

## Autonomy + decision rules

- **Run fully autonomously.** Do not stop to ask the user. When the plan is precise, follow it; when
  a detail is underspecified, **make the best decision consistent with the spec + the existing
  codebase patterns**, implement it, and note the choice in the commit body.
- **The plan's code is a strong default, not gospel.** If a literal snippet doesn't fit the real code
  (a helper is named differently, a fixture spawns differently, a CSS token is undefined), adapt it to
  match the actual repo — the *behavior and the tests* are the contract, not the exact lines.
- **Known soft spots to resolve yourself (the plan flags these):**
  - **Task 9 e2e fixture** — read `atlas/dashboard/tests/e2e/conftest.py` and mirror the existing
    `belt_server` spawn/injection mechanism exactly (inject the four `app.state` values the same way
    `produce_fn`/`find_topics_fn` are injected). Do not invent a new injection path.
  - **CSS tokens** — if a `var(--…)` in Task 7 is undefined, substitute the nearest token from the
    `:root` block (mirror the Slice-5 proposal/gate palette).
  - **F5 `supersedes`** — optional hardening: compute the same-band applied note at accept-time and
    return it so the UI can warn. Implement it if cheap; otherwise leave the envelope field unused
    (the write is reversible). Either is acceptable; note which you did.
- **If you hit a genuine blocker** (a test that cannot pass without changing a guardrailed file, a
  missing engine function, an architectural contradiction): stop, leave the work committed up to the
  last green task, and write a short `BLOCKED` note at the end of this file describing exactly what and
  why. Do not work around a guardrail.

## Guardrails (do NOT cross — these are the whole point of the slice)

- **Additive only.** Do **not** edit `eval/loop.py`, `eval/diagnose.py`, `eval/rollup.py`,
  `rubric/__init__.py`, the coach adapters, `registry.py`, or `pipeline.py`. Use them only through
  existing seams (`loop.apply_soft_change`, `loop.run_loop(write_soft=False, …)`, the read-only rubric
  accessors, `loop.EDITORIAL_STAGES`/`PRODUCTION_STAGES`).
- **One write path.** The ONLY new calls to `loop.apply_soft_change` are in the accept and revert
  endpoints. Nothing else writes a persona/rubric file.
- **`loop.can_write_rubric()` must stay `True`** — keep the negative-safety test that asserts it.
- **No real LLM/engine in tests.** Every engine touch is injected (`coach_propose_fn`, `echo_fn`,
  `produce_fn`, `find_topics_fn`). `ANTHROPIC_API_KEY` is never set. Never run `default_coach_propose`
  in a test.
- **Two planes preserved.** The chat stays T1-only; nothing here gives the LLM plane a T4 write.

## Verification gates (every task must keep these green)

```bash
# unit (atlas core + dashboard non-e2e)
cd atlas && ../venv/bin/python -m pytest tests/ -q
cd atlas && ../venv/bin/python -m pytest dashboard/tests/ --ignore=dashboard/tests/e2e -q
# e2e (Playwright; heavy — run the coaches file in isolation if the sandbox is contended)
cd atlas && ../venv/bin/python -m pytest dashboard/tests/e2e/ -q
```

- **Restart the server after any backend change** (`python -m dashboard.server` runs without
  `--reload`; a stale process serves new static files but old Python → phantom 404s).
- **e2e navigation uses `wait_until="domcontentloaded"`** (never `load`/`networkidle` — the SSE
  connection and the Google-Fonts CDN both stall those).
- Baseline before you start: **353 unit + 39 e2e green**. After Slice 6: that plus the new
  `test_proposals_store.py`, `test_proposals.py`, `test_coaches_api.py`, `test_coaches_e2e.py`, and the
  one new `test_settings_api.py` case. The known cooperative-cancel timing flake is not a regression.

## Definition of done

- All 9 tasks committed on `control-room`, each with its own green test cycle.
- Full suite green (the new Slice-6 tests included).
- `.gitignore` ignores `atlas/dashboard/control_room_proposals.json`.
- A one-paragraph completion summary to the user: what shipped, any decisions made, anything shelled
  (Echo real data + the post-render auto-propose wiring remain for #7, by design).
- After done, refresh the handoff: append a "Slice 6 — DONE" entry to
  `docs/superpowers/specs/2026-06-23-control-room-HANDOFF.md` mirroring the Slice-5 entry's style
  (files touched, totals, what's shelled).

## Execution log (append as you go)

- _(empty — the executing session appends per-task status / decisions / any BLOCKED note here)_
