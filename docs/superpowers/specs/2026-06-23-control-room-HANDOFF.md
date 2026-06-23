# Control Room — Session Handoff (2026-06-23)

> Paste the **PROMPT TO START THE NEW SESSION** (below) into a fresh session. This doc is the
> full context; the new session should read it + the two authoritative files first.

---

## PROMPT TO START THE NEW SESSION (copy this)

```
We're continuing to build the YT-Agents Control Room — a fully functional operating console
for an autonomous multi-agent video agency, wired to the real FastAPI backend in atlas/dashboard/.

READ THESE THREE FILES END TO END BEFORE WRITING ANY CODE:
1. docs/superpowers/specs/2026-06-23-control-room-design.md   (the hardened master spec — §4 write
   tiers, §5 Echo, §6 the assembly line, §8 chat, §9 YouTube, §10 SSE, §13 edge cases E1–E15)
2. docs/superpowers/specs/2026-06-23-control-room-HANDOFF.md  (THIS doc — current state + file map +
   gotchas + the remaining slice plan)
3. PROJECT_CONTEXT.md                                          (system overview)

STATE: Slices 0–2 are DONE and verified (304 unit + 20 e2e green). The dashboard now has a working
live "belt" (assembly line): you can drop a topic → it runs down 10 stations → gates pause it →
you approve. Backend dispatcher + SSE + trigger/cancel all built and tested.

YOUR TASK: build Slice 3 — the Video Detail page (depth 1) + Stage/Agent Inspector (depth 2) +
Roster/Fleet view + Live Activity Feed. Most of the READ data already exists
(data.project_detail / fleet / agent_detail / provider_for) — this slice is mostly frontend over
existing endpoints. Follow the original brief's surfaces D, E, F, G and its UX principles
(progressive disclosure, modal-vs-panel-vs-page discipline, the ONE status language, no dead ends,
honest fix vocabulary = UNDERSTAND + RETRY + CANCEL).

HOW TO WORK: use the frontend-design skill for the visual layer; ADDITIVE only (extend
atlas/dashboard/, never touch pipeline/contracts/gates/registry/session except via existing seams);
wire everything to real endpoints (no mock data); keep all 324 tests green and add Playwright tests
for the new surfaces. After a BACKEND change, restart the server (no --reload); after a static
change just refresh. Deliver in tested slices and report what you built + what you shelled.
```

---

## Current state — what is DONE (Slices 0–2)

**Slice 0 — foundation (shipped).** Fixed the 4k right-side empty-space bug (the mock capped content
at `max-width:1320px` left-aligned); the shell is now fluid + centered (`.main`, cap 2200,
`margin-inline:auto`), with `.main.wide` reserved for the full-bleed belt. Added the **status-language
CSS tokens** (`--st-queued/running/blocked/failed/done/cancelled`). **Retired** the standalone mock
`yt-agents-dashboard.html` (deleted). Added a responsive e2e test.

**Slice 2 — the assembly line (shipped, backend + frontend).**
- **Spine hooks (minimal, opt-in, behavior-preserving):** `pipeline.produce()` gained
  `station_locks` (per-stage single-occupancy) + `should_cancel` (cooperative cancel). Both default
  `None` = byte-identical old behavior (54 pipeline tests still green). Refactor extracted
  `_run_stage()` and added public `create_project()` (mints a `queued` project, returns slug
  immediately). `_run_stage` tags failures `failure_kind` = `transient` (producer raised) vs
  `deterministic` (contract/auto-gate) for the retry policy.
- **`atlas/dispatcher.py` — the belt.** `Dispatcher(projects_dir, produce_fn, max_in_flight=2,
  max_retries=1)`. One `threading.Semaphore(1)` per stage = station=stage single-occupancy; a global
  in-flight semaphore caps concurrency (over-cap videos wait as `queued`); transient-retry with
  backoff / deterministic-no-retry; cooperative cancel (running stops at next station; queued marked
  on disk); `EventRing` (bounded, monotonic-id, `since()` for SSE Last-Event-ID, `initiator` per
  event for the §4 audit property); **belt state is rebuildable from disk** — the dispatcher holds
  only ephemeral control state. Public API: `trigger() / cancel() / resume() / live_state()`.
- **`data.belt(projects_dir)`** — the live belt view from disk: `{stations[10], videos[], occupancy,
  counts}`; each video has `belt_state` (normalized status), `station` (current), per-stage `stages`
  map, `gate`, `hard_block`.
- **Endpoints (atlas/dashboard/app.py):** `GET /api/belt`, `POST /api/trigger` (T1), `POST
  /api/cancel/{slug}` (T1), `GET /api/events` (SSE, Last-Event-ID backfill). Dispatcher built lazily
  via `_get_dispatcher(app)` using `app.state.produce_fn` (test-injectable) + `app.state.max_in_flight`.
- **Frontend (static/index.html, app.js, styles.css):** the live belt on the home surface
  (`#ov-belt`): 10-station occupancy strip (busy station pulses #FFD000) + per-video spine rows
  (10-segment track colored by the status language) + a **needs-you tray** (`#ov-needs`, aggregates
  blocked + failed). A **shared dialog system** (`openDialog`/`closeDialog`, focus-trap, Escape,
  return-focus, backdrop-close, `hard` flag for non-dismissable). The **launch modal** (topic +
  length + gates toggle → `POST /api/trigger`). **SSE client** (`connectEvents()`) refreshes the belt
  on every event. The old overview cards (KPIs / spine / gate / scorecard / fleet / activity) are kept
  below, untouched.

## File map (where things are)

| Area | File |
|---|---|
| Master spec (hardened) | `docs/superpowers/specs/2026-06-23-control-room-design.md` |
| This handoff | `docs/superpowers/specs/2026-06-23-control-room-HANDOFF.md` |
| Spine + hooks | `atlas/pipeline.py` (`produce`, `_run_stage`, `_station`, `create_project`, `STAGES`) |
| The belt | `atlas/dispatcher.py` (`Dispatcher`, `EventRing`) |
| Belt read view | `atlas/dashboard/data.py` (`belt`, `_belt_state`, `_current_station`; plus existing `project_detail/fleet/agent_detail/quality/gate_detail/provider_for`) |
| Endpoints + SSE | `atlas/dashboard/app.py` (`_get_dispatcher`, `_event_stream`, the `/api/*` routes) |
| Frontend | `atlas/dashboard/static/{index.html,app.js,styles.css}` |
| Dispatcher tests | `atlas/tests/test_dispatcher.py` (10 — incl. 2 retry) |
| Belt API tests | `atlas/dashboard/tests/test_belt_api.py` (5) |
| Stage/activity/retry tests | `atlas/dashboard/tests/test_stage_api.py` (11) |
| Belt e2e + fake-spine fixtures | `atlas/dashboard/tests/e2e/test_belt_e2e.py` (7), `conftest.py` (`belt_server`/`belt_fail_server`, `_fake_belt_produce`/`_fake_fail_then_done_produce`) |
| Slice-3 e2e (inspector/activity/fleet) | `atlas/dashboard/tests/e2e/test_slice3_e2e.py` (7) |
| Stage Inspector + Activity (frontend) | `atlas/dashboard/static/app.js` (`openStageInspector`/`renderStageInspector`/`openDrawer`, `renderActivity`/`drawActivityFeed`), `static/index.html` (`#v-activity`, `#drawer-root`), `static/styles.css` (Slice-3 block) |

## Architecture rules the new session MUST respect

- **Two planes (PROJECT_CONTEXT §3):** the LLM does judgment; the deterministic spine
  (`pipeline.py`) does guarantees. Never move a guarantee into the LLM.
- **Tiered write authority (spec §4):** T1 reversible (trigger/cancel/settings — light confirm,
  prefer inline undo) · T2 spine gates (deterministic UI ONLY; chat may surface but NEVER satisfy; a
  `block` can never be approved away) · T3 publish (hard structured confirm + enforced review;
  scheduled-AFTER-approval; no auto-fire-unreviewed) · T4 persona/rubric proposals (existing
  WriteBoundaryError + CEO approve). Tag every action's `initiator`.
- **ONE status language** everywhere: queued / running / blocked / failed / done / cancelled.
- **Decoupling (PROJECT_CONTEXT §11):** engines never import Atlas; Settings/seams are PASSED IN, not
  read globally by a pure engine.
- **Disk is the source of truth** for the belt (rebuildable from `projects/*/project.json`).
- **Additive only:** extend `atlas/dashboard/`; don't touch pipeline/contracts/gates/registry/session
  except via existing seams (the `station_locks`/`should_cancel` opt-in params are the sanctioned new
  seam).

## GOTCHAS (learned this session — don't rediscover them)

1. **Server restart after backend changes.** `./yt-atlas` / `python -m dashboard.server` runs WITHOUT
   `--reload`. A stale process serves NEW static files from disk but OLD Python in memory → new
   endpoints 404. After any .py change: kill the process and relaunch. (This caused a "/api/belt 404"
   that was purely staleness.)
2. **SSE breaks Playwright `networkidle`.** The open `/api/events` connection never lets the page go
   network-idle. e2e `_open()` now uses `wait_until="load"` + an explicit `wait_for_selector`. Do the
   same for any new navigation in tests; never use `networkidle`.
3. **e2e must not run the real engine.** Triggering on the shared `live_server` would run Sage's real
   LLM. Use the `belt_server` fixture (fast fake `produce` honoring the lock/cancel hooks) for any
   test that triggers/cancels. `ANTHROPIC_API_KEY` is never set in tests.
4. **Spine hooks are opt-in.** Pass `station_locks`/`should_cancel` only from the dispatcher; leaving
   them `None` (CLI/orchestrator/tests) preserves exact old behavior.

## SHELLED / deferred (wire these in their slices)

- Launch modal's **niche-pill → Scout auto-pick** path (needs intake #1.5); the type-a-topic path is
  live.
- **Gate-approve still resumes synchronously** via `session.approve_gate` (existing tested path), NOT
  `dispatcher.resume()` — so a resumed render doesn't yet share the belt's station locks. Wire
  `dispatcher.resume()` when building the T2 gate panel (Slice 5). The method already exists.
- **Chat FAB / agentic chat** not started (Slice 5).
- **Settings (niches/channels), Herald publish shell, Echo/coaches proposal cards** not started.

## Remaining slices (recommended order)

- **Slice 3 — DONE (shipped, verified).** Video Detail PAGE (depth 1) — the `v-pipeline` page gained an
  **event-history** card (from `project_detail.history`) + per-stage timing + a click/Enter affordance on
  every stage. Stage/Agent **Inspector** (depth 2) — a right slide-in **drawer** (`openDrawer`/
  `openStageInspector`) showing the owning agent + effective brain, the **Reads → agent runs → Writes**
  flow (upstream inputs existence-checked, output artifact + a field-level **contract stamp** VALID/INVALID
  + rejection slip), and a **failure surface** classified TRANSIENT vs DETERMINISTIC with the honest fix
  vocab — **UNDERSTAND** (what happened + what it means) + **RETRY** (transient only, spec §6.4) + **CANCEL**;
  a healthy stage is read-only. **Roster/Fleet** now shows the running agent's **current video + stage**
  (a clickable "now on" chip; same on the agent profile). **Live Activity Feed** — new `v-activity` rail
  surface: a filterable (by kind + **initiator plane**) audit ledger backfilled from `GET /api/activity`
  and live-tailed by the existing SSE; the initiator plane (ceo/dispatcher/chat) is the structural device
  (the §4 audit property). New backend (all additive, TDD'd): `data.stage_detail` + `STAGE_INPUTS` +
  `_classify_failure`, enriched `data.fleet`/`agent_detail` with `current`, `dispatcher.retry()`, and
  endpoints `GET /api/projects/{slug}/stage/{key}`, `GET /api/activity`, `POST /api/retry/{slug}` (T1);
  `app.state.max_retries` is now injectable. Tests: `dashboard/tests/test_stage_api.py` (11),
  2 dispatcher retry tests, `dashboard/tests/e2e/test_slice3_e2e.py` (7) + a `belt_fail_server` fixture.
  **Totals now: 315 unit + 27 e2e green.** SHELLED: nothing new — RETRY/CANCEL/activity all wired live.
- **Slice 4 (#4) — DONE (shipped, verified).** Settings surface (`v-settings` rail). **Backend** (additive,
  TDD'd): `atlas/dashboard/settings_store.py` — a dashboard-owned JSON (`control_room_settings.json`,
  gitignored) with `load/validate/save/public_settings`, tolerant defaults on missing/corrupt (E13),
  the `CONNECTION_STATES` machine + the `QUOTA` constant (§9: 1600 units/insert, 10000/day, ~6/day
  **shared across ALL channels**), and `length_for_niche()`. Niche names reuse `validate.validate_niche`.
  Endpoints `GET /api/settings` + `PUT /api/settings` (T1 reversible; validates+sanitizes, drops bad
  rows, never crashes); `app.state.settings_path` injectable. **Decoupling honored (§3/§11):** the
  trigger endpoint resolves a niche's default length from settings DASHBOARD-side and passes it INTO
  `dispatcher.trigger(length=...)` — no engine reads settings globally. **Frontend:** niches editor
  (rows → launch pills + per-niche default length + mapped channel), defaults (length/voice/style), and
  the Channels broadcast-bay SHELL (per-channel connection-state badge, the two YouTube verification
  flags, mapped niche, the shared ~6/day **quota banner**, and an honest disabled "Connect — arrives
  with Herald" affordance — OAuth/tokens are #6, none stored here). **Launch modal** now reads niches →
  renders **niche pills** that set `niche` + prefill the niche's default length (Scout auto-pick from a
  niche is still **shelled** for #1.5). Tests: `dashboard/tests/test_settings_api.py` (11),
  `dashboard/tests/e2e/test_settings_e2e.py` (4); the 3 server fixtures now set an isolated
  `settings_path`. **SHELLED:** channel OAuth connect/reconnect (lands with #6 Herald); niche→Scout
  auto-pick (lands with #1.5).
- **Slice 1.5:** Niche intake — select niche → Scout `find_topics` → configurable auto-pick / you-pick
  candidate cards → enters the belt. Completes the launch modal's second path.
- **Slice 5 (#3 + gates):** agentic chat panel (bottom-right launcher, T1-only tools, NEVER satisfies
  T2/T3) + T2 gate review side-panel (the approve CLICK lives here; wire `dispatcher.resume()`) + T3
  publish-confirm modal shell (structured exact-package review; hard, no stray-escape).
- **Slice 6:** Coaches (Quill/Flux) + Echo T4 proposal cards (accept/reject; Echo shows cohort/aggregate
  evidence, flags rubric contradictions as CEO-interview items) + the **negative safety Playwright
  tests** (chat cannot satisfy T2/T3 — no such control reachable; no auto-publish-unreviewed).

### New fleet/engine workstreams added to scope 2026-06-23 (planned, NOT yet built — own specs)
- **#8 — Glint 🎯 the Thumbnail Artist** (off-pipeline agent). Generates a SET of 3 high-CTR HTML+Chrome
  thumbnail stills (1280×720, LOCAL license-clean focal — Magpie cleared asset / Mason brand logo /
  ffmpeg video frame); reuses Mason's headless-Chrome path (NOT a HyperFrames timeline); **Herald's
  `package` delegates** to it → the candidates surface in the **T3 publish-confirm modal** where the
  CEO picks one. One registry entry + one adapter + `thumbnail_set.schema.json`; **no spine/stage
  change.** Lands with **#6 Herald**. Full brief: `docs/superpowers/specs/2026-06-23-thumbnail-artist-Glint.md`.
- **#9 — Motion stack upgrade: d3 + deeper GSAP + Lottie** (+ off-pipeline **Loop 🎞️** generator).
  Iris designs / Mason renders d3 data-charts (closed chart-type set, scoped to the `data-chart`
  layout), richer GSAP motion (new closed-set EFFECT tokens), and LOCAL Lottie assets; **Magpie**
  sources+license-clears Lotties via its existing truth table; new off-pipeline **Loop** agent
  generates a Lottie only on a Magpie miss (the explicit fallback chain). Closed-vocab (unknown =
  error), determinism wall, and Mason's auto-gate all intact; **10-stage spine UNCHANGED.** Builder
  owns the phasing (proposed M0 verify → M1 GSAP → M2 d3 → M3 Lottie-source → M4 Loop); **gated by a
  verification spike** (HyperFrames Lottie player? GSAP licensing? existing d3 path?). Independent of
  the UI slices — schedule whenever. Full brief: `docs/superpowers/specs/2026-06-23-motion-stack-d3-gsap-lottie.md`.

## Run + test

```bash
# run the control room (restart after any backend change — no --reload)
cd atlas && ../venv/bin/python -m dashboard.server --host 127.0.0.1 --port 8848   # -> :8848
# or ./yt-atlas from repo root

# unit tests
cd atlas && ../venv/bin/python -m pytest tests/ -q
cd atlas && ../venv/bin/python -m pytest dashboard/tests/test_api.py dashboard/tests/test_security.py \
  dashboard/tests/test_gate_write_real.py dashboard/tests/test_belt_api.py -q
# e2e (Playwright + chromium already installed in the venv)
cd atlas && ../venv/bin/python -m pytest dashboard/tests/e2e/ -q
```

Current totals: **315 unit + 27 e2e = 342 green** (Slice 3 added stage/activity/retry unit tests +
7 e2e). Run the new ones: `../venv/bin/python -m pytest dashboard/tests/test_stage_api.py
dashboard/tests/e2e/test_slice3_e2e.py -q`.
