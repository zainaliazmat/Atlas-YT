# Re-run control — design

**Date:** 2026-06-24
**Status:** approved, ready for implementation
**Branch:** `control-room`

## Problem

When a video parks `failed` (or finishes `done`/`cancelled`/`blocked`), the dashboard
offers no top-level way to re-run it. The only re-run paths today are:

- **Retry stage** — buried in the Inspector drawer; only re-runs the *single failed
  stage*. Useless when the failure is caused by a bad **upstream** artifact (e.g. an
  empty `research_brief.json` makes the `script` stage fail — retrying `script` re-reads
  the same empty brief and fails again).
- **Resume** — only appears at a gate panel, to continue after an approval.

Operators need an obvious "re-do this video" control, and the option to re-do from an
earlier stage (e.g. research) when that is where the real problem lives.

## Solution

A **Re-run split-button** on the project header:

- **Main button** → re-run the whole video from the start (reset all stages).
- **Caret dropdown** → "From &lt;stage&gt;" for each stage that has **already run**
  (status ≠ `pending`), in spine order. Picking one re-runs that stage + everything
  downstream, keeping upstream artifacts.

It re-runs the **same video** (same slug / topic / title) — it does not mint a new belt
card. It is hidden while the video is actively `running`/`queued`.

### Why "reset to pending" is the mechanism

`pipeline.produce()` iterates `STAGES` in order and runs any stage whose `status !=
"done"`, skipping done ones (`pipeline.py` spine loop). So:

- **From start** → set every stage `pending` → produce() re-runs all.
- **From stage X** → set X + all downstream `pending`, leave upstream `done` → produce()
  skips the done upstream and re-runs from X.

This is the existing `_reset_failed_stage` pattern, generalized.

## Components

### 1. `Dispatcher.rerun(slug, from_stage=None, *, initiator="ceo")`

`atlas/dispatcher.py`. Returns `{"slug", "rerunning": bool, "from_stage", ...}`.

- Load `project.json`; if missing → `{"rerunning": False}`.
- **Guard — active run:** if a live worker thread exists for `slug` → `{"rerunning":
  False, "reason": "still running"}` (no racing an active run).
- **Resolve the reset set** from `STAGES` order:
  - `from_stage is None` → all stage keys.
  - else → `from_stage` and every stage after it. Reject (`rerunning: False`) if
    `from_stage` is not a real stage key, or if its current status is `pending` (it
    never ran — cannot "re-run" it).
- Reset each stage in the set to `{"status": "pending", "artifact": None, "validated":
  False}`.
- **Re-earn gates** at/after the reset point: for each gate whose stage is in the reset
  set, clear its approval (`project["gates"][g]["status"] = "pending"`), so a fresh
  fact-check / final-render gate fires again.
- Clear the cancel flag (`self._cancel.discard`), reset the auto-retry budget
  (`self._retries.pop`), set `project["status"] = "queued"`, bump `updated`, atomic-write.
- Emit a `rerun` event and start the worker.

### 2. `POST /api/rerun/<slug>`

`atlas/dashboard/app.py`. Optional JSON body `{ "from_stage": "research" }`. Subject to
the same write-permission gate as `/api/trigger` and `/api/retry` (a reversible T1 write,
initiator `ceo`). No new read endpoint — the dropdown is built from the stage statuses
already on the project page.

### 3. Frontend split-button

`atlas/dashboard/static/{app.js,index.html,styles.css}`.

- Render a "Re-run" split-button in the project header (near "Open folder"), shown only
  when `belt_state ∈ {failed, cancelled, done, blocked}`.
- Main click → `POST /api/rerun/<slug>` (no body).
- Caret → menu of "From &lt;stage label&gt;" for each stage with status ≠ `pending`,
  spine order; selection → `POST` with `{from_stage}`.
- On success: toast + refresh the spine; on error: inline message.

## Error handling

- Re-run while running → guarded server-side (and button hidden client-side).
- Unknown / un-run `from_stage` → `{"rerunning": False}` (+ 4xx in the API), surfaced as
  an inline error.
- All reads tolerant of a missing/corrupt `project.json` (consistent with the rest of
  the dispatcher).

## Testing

- **Dispatcher unit** (`tests/test_dispatcher.py`):
  - rerun-all resets every stage and requeues + starts a worker;
  - rerun-from-stage resets that + downstream, keeps upstream `done`;
  - rejects an un-run (`pending`) stage and an unknown stage;
  - guards while a worker is live.
- **API unit** (`dashboard/tests/`): `POST /api/rerun` with and without `from_stage`
  returns ok and respects the write gate; bad `from_stage` → error.
- **e2e** (optional, if cheap): the split-button + dropdown appears for a failed video
  and triggers a re-run.

## Out of scope

- Per-stage re-run of a *future* (never-run) stage — disallowed by design.
- Minting a new video / duplicating a project.
- Changing gate semantics beyond re-earning on re-run.
