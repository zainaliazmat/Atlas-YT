# Control Room — Session Handoff (2026-06-23)

> Paste the **PROMPT TO START THE NEW SESSION** (below) into a fresh session. This doc is the
> full context; the new session should read it + the two authoritative files first.

---

## PROMPT TO START THE NEW SESSION (copy this) — Slice 5

```
We're continuing to build the YT-Agents Control Room — a fully functional operating console
for an autonomous multi-agent video agency, wired to the real FastAPI backend in atlas/dashboard/.

READ THESE FILES END TO END BEFORE WRITING ANY CODE:
1. docs/superpowers/specs/2026-06-23-control-room-design.md   (the hardened master spec — esp. §4
   write tiers, §8 agentic chat, §9 YouTube/T3 publish, §10 SSE, §13 edge cases E7/E8/E12)
2. docs/superpowers/specs/2026-06-23-control-room-HANDOFF.md  (THIS doc — current state + file map +
   gotchas + the remaining slice plan)
3. PROJECT_CONTEXT.md                                          (system overview)
ALSO STUDY (the reference implementation of chat-over-session + gate buttons):
   atlas/web/app.py (the Chainlit web UI — streaming chat on session.send + Approve/Revise gate
   buttons + per-agent persona chat), atlas/session.py (AtlasSession.send / approve_gate /
   latest_blocked_project / AgentSession), atlas/orchestrator.py (the SDK query() loop), and the
   existing dashboard gate-approve path (app.py `_approve_gate` / `_get_session`).

STATE: Slices 0–4 + 1.5 are DONE and verified (333 unit + 33 e2e green) on branch `control-room`.
The dashboard is a working operating console: the live belt (drop a topic / pick a niche → Scout
finds topics → it runs down 10 stations → gates pause it → you approve), depth-2 Stage Inspector,
Fleet, a live Activity audit feed, and a Settings page (niches/defaults/channels shell + quota).
Dispatcher exposes trigger/cancel/retry/resume; resume() is BUILT+TESTED but the gate-approve UI
still calls session.approve_gate SYNCHRONOUSLY (does NOT yet go through dispatcher.resume()).

YOUR TASK: build Slice 5 (#3 chat + the T2/T3 gate surfaces):
  (a) AGENTIC CHAT panel — bottom-right launcher → panel, streaming over the SAME session core
      (session.AtlasSession.send), agentic via the existing Agent SDK tool loop. Chat may initiate
      ONLY T1 reversible actions (trigger a production, change a setting, cancel/park a run) with a
      light confirm. Chat may SURFACE/SUMMARIZE/NAVIGATE-TO a gate or publish, but MUST NEVER be able
      to satisfy a T2 gate or a T3 publish (§4/§8 — the LLM plane never drives a guarantee). Add an
      INJECTABLE chat seam (app.state.chat_fn / send_fn) so e2e fakes it — NEVER run the real LLM in
      tests (ANTHROPIC_API_KEY is never set).
  (b) T2 GATE-REVIEW side-panel — the deterministic surface where the authorizing APPROVE click lives;
      wire it through dispatcher.resume() (the method exists) so the resumed render shares the belt's
      station locks instead of the current synchronous produce. A factcheck `block` can NEVER be
      approved away (unchanged spine rule).
  (c) T3 PUBLISH-CONFIRM modal SHELL — a HARD structured confirm (no stray Escape/backdrop close) that
      reviews the EXACT final package (title/description/tags/thumbnail/visibility/schedule); scheduling
      only sets go-live AFTER approval. Real publishing is #6 Herald — this is the review SHELL +
      the enforced checkpoint, with the fire action disabled/"arrives with Herald".

HOW TO WORK: use the frontend-design skill for the visual layer; ADDITIVE only (extend atlas/dashboard/;
touch pipeline/contracts/gates/registry/session ONLY via existing seams — the chat uses session.send
unchanged, like web/app.py does). Tiered write authority §4: tag every action's initiator; the SSE
event log already records the plane. Keep all 333 unit + 33 e2e green and ADD tests incl. NEGATIVE
SAFETY ones (chat cannot reach any control that satisfies T2/T3; no auto-publish-unreviewed path).
e2e: inject fakes (produce_fn / find_topics_fn / the new chat seam), navigate with
wait_until="domcontentloaded" (NEVER load/networkidle — see gotchas), restart the server after any
backend change (no --reload). Deliver in tested slices; report what you built + what you shelled.
```

---

## Current state — what is DONE (Slices 0–5 + 1.5) — 353 unit + 39 e2e green, branch `control-room`

> **Slice 5 (#3 chat + T2/T3 gate surfaces) is DONE** — see the Slice-5 entry in "Remaining slices"
> below. The control room now also has the agentic chat (T1-only), the deterministic T2 gate-review
> drawer (approve resumes through the belt), and the T3 publish-confirm shell.

> Per-slice detail is in the "Remaining slices" list below (each shipped slice is marked **DONE** with
> its files). Quick map of what exists now: **#0** fluid shell + status tokens · **#2** the belt
> (`dispatcher.py` + `data.belt` + trigger/cancel/SSE) · **#3** Video Detail + Stage Inspector drawer +
> Fleet "now on" + Activity audit feed (`data.stage_detail`, `/api/activity`, `/api/retry`,
> `dispatcher.retry`) · **#4** Settings (`settings_store.py` + `/api/settings` + channels/quota shell +
> launch niche pills) · **#1.5** niche intake (`intake.py` + `/api/intake/topics` + launch candidate
> cards). The original Slice 0/2 build notes are retained below for reference.

### Original Slice 0–2 build notes (reference)

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
2. **Playwright navigation: use `wait_until="domcontentloaded"` + an explicit `wait_for_selector`.**
   Two traps, both solved by this one rule: (a) the open `/api/events` SSE connection never lets the
   page go **`networkidle`**; (b) the index.html **Google Fonts `<link>`** is an external CDN that
   intermittently stalls in the sandbox — `wait_until="load"` waits for it and `page.goto` then times
   out at 30s (this cascaded into ~8 false failures across a full run). `domcontentloaded` fires once
   the HTML is parsed and `app.js` has executed (scripts block parsing), without waiting on fonts or
   SSE. Every e2e nav now uses it; never use `load` or `networkidle`. Also: the console guard
   (`conftest._ConsoleGuard`) ignores network resource-load errors (`failed to load resource` /
   `net::err*`) so a flaky CDN doesn't fail a test — it still catches real JS exceptions + app
   console.error. **The full e2e suite is heavy (~33 browser tests, multiple live uvicorn servers); run
   it in two batches if the sandbox is contended, or expect occasional resource-pressure flakes —
   every test passes isolated/in-subset.**
3. **e2e must not run the real engine.** Triggering on the shared `live_server` would run Sage's real
   LLM. Use the `belt_server` fixture (fast fake `produce` honoring the lock/cancel hooks) for any
   test that triggers/cancels. `ANTHROPIC_API_KEY` is never set in tests.
4. **Spine hooks are opt-in.** Pass `station_locks`/`should_cancel` only from the dispatcher; leaving
   them `None` (CLI/orchestrator/tests) preserves exact old behavior.

## SHELLED / deferred (wire these in their slices)

- **T2 gate-approve now resumes through `dispatcher.resume(wait=True)`** (Slice 5) — DONE; shares the
  belt's station locks, initiator="ceo", hard-block still refused.
- **Agentic chat** (Slice 5) — DONE; T1-only, injectable `app.state.chat_fn`, never satisfies T2/T3.
  The **real default LLM impl** (`chat.default_send`) is built but UNTESTED (needs the Claude
  subscription; e2e/unit inject a fake). Future: ground it with #5 RAG `retrieve()` (it's a prompt-
  injection surface — that's exactly why chat is T1-only; see §8/E7).
- **T3 publish-confirm modal** (Slice 5) — DONE as the review SHELL; **real publish = #6 Herald**
  (the fire button is disabled, there is no publish-fire route). Glint #8 fills the thumbnail slot.
- **Channel OAuth connect/reconnect** (the Settings channels shell renders state but does no OAuth) →
  **#6 Herald**. **Niche → Scout AUTO-PICK** beyond candidate cards (the cards + you-pick/auto-pick
  toggle are live; the toggle currently auto-selects the top candidate client-side) — deeper auto-run
  policy can come with chat/#6. **Herald publish stage, Echo/coaches proposal cards** → #6/#7.
- **#8 Glint (thumbnail artist)** + **#9 motion stack (d3/GSAP/Lottie + Loop)** — scoped as their own
  specs (`2026-06-23-thumbnail-artist-Glint.md`, `2026-06-23-motion-stack-d3-gsap-lottie.md`), not built.

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
- **Slice 1.5 — DONE (shipped, verified).** Niche intake in the launch modal. **Backend:**
  `atlas/dashboard/intake.py` — `normalize_candidates()` (Scout's ranked ideas → `{idx,title,
  confidence,why}` cards) + `default_find_topics()` (builds Scout's adapter from the registry and runs
  `find_topics` — the real LLM+YouTube seam). `POST /api/intake/topics {niche}` validates the niche
  (`validate.validate_niche`), runs the finder via `asyncio.to_thread` off an **injectable
  `app.state.find_topics_fn`** (tests inject a fake — never the real engine), and returns
  `{ok, candidates, intake_mode, auto_pick}`; a no-topics/raising Scout degrades to `{ok:false,error}`
  (never a 500). `intake_mode` (`pick`|`auto`) added to settings defaults (+ surfaced in
  `public_settings`). **Frontend:** the launch modal's niche pick now reveals an intake panel —
  You-pick/Auto-pick toggle (default from settings) + "🔎 Find topics with Scout" → candidate cards
  (title + color-coded confidence + why); picking one fills the topic field (auto-pick takes the top
  one); Generate triggers it onto the belt. Added a `postJSON` helper. Tests:
  `dashboard/tests/test_intake_api.py` (7) + `dashboard/tests/e2e/test_intake_e2e.py` (2); `belt_server`
  now injects a canned `find_topics_fn`. The launch modal's niche path is no longer shelled.
- **Slice 5 (#3 + gates) — DONE (shipped, verified).** Agentic chat + the T2/T3 surfaces.
  **(a) Agentic chat** — bottom-right lilac FAB → streaming panel over an INJECTABLE seam
  (`app.state.chat_fn`; tests fake it, the real LLM never runs). The real default
  (`dashboard/chat.py::default_send`) is a constrained Claude Agent SDK loop whose ONLY tools are
  read-grounding + T1 *proposals* — there is no approve/publish tool. The chat is T1-ONLY **by
  construction**: `T1_ACTION_KINDS = (trigger, cancel, update_setting)`, and `execute_action` /
  `POST /api/chat/act` REJECT any other kind (`NotReversibleError` → 400). Each T1 action is a
  *proposal* the CEO confirms with one click (the §4 light confirm), executed via the dispatcher
  tagged `initiator="chat"` (the §4 audit, surfaced in the Activity feed's `p-chat` lilac plane).
  `POST /api/chat` streams SSE frames; the done-frame DROPS any non-T1 action (defence in depth).
  **(b) T2 gate-review drawer** (`openGateReview`) — the deterministic side-panel where the
  authorising approve lives; it posts to `/api/gate/{slug}/approve`, now rewired to
  **`dispatcher.resume(slug, gate, wait=True, initiator="ceo")`** so the resumed render shares the
  belt's station locks (outcome read back from disk). A hard fact-check `block` is still refused
  before any resume. **(c) T3 publish-confirm modal** (`openPublishModal`) — a HARD dialog (no
  stray Escape/backdrop close) reviewing the EXACT package (title/description/tags/thumbnail/
  visibility/schedule + niche→channel routing + §9 verification blockers) from
  `GET /api/publish/{slug}` (`dashboard/publish.py`, read-only). `schedule` is null (set only AFTER
  approval); the fire button is DISABLED ("arrives with Herald (#6)") and **there is NO publish-fire
  route** (POST → 405) — the no-auto-fire-unreviewed property (E8) holds by construction.
  New backend: `dashboard/chat.py`, `dashboard/publish.py`, the chat/publish endpoints,
  `dispatcher.resume(wait=)` + `_disk_outcome`. New tests: `test_chat_api.py` (13),
  `test_publish_api.py` (5), 2 dispatcher resume tests, `e2e/test_chat_e2e.py` (6) incl. the
  negative-safety ones (rogue `approve` dropped; no reachable T2/T3 control; HARD publish with no
  fire). Dead `_get_session`/`app.state.session` removed (T2 no longer goes through a session).
  **Totals now: 353 unit + 39 e2e green.** SHELLED: the real publish (Herald #6); the chat's default
  LLM impl requires the Claude subscription (never tested — the seam is faked).
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

# unit tests (atlas core + all dashboard non-e2e)
cd atlas && ../venv/bin/python -m pytest tests/ -q
cd atlas && ../venv/bin/python -m pytest dashboard/tests/ --ignore=dashboard/tests/e2e -q
# e2e (Playwright + chromium already installed in the venv). Heavy ~33-test single-process run;
# if the sandbox is contended, run in two batches (read-heavy files, then the belt-trigger files).
cd atlas && ../venv/bin/python -m pytest dashboard/tests/e2e/ -q
```

Current totals: **333 unit + 33 e2e green** (branch `control-room`, through Slice 1.5). New-since-Slice-3
suites: `test_stage_api.py` (11) · `test_settings_api.py` (11) · `test_intake_api.py` (7) · e2e
`test_slice3_e2e.py` (7) · `test_settings_e2e.py` (4) · `test_intake_e2e.py` (2). **Known timing flake:**
`test_cancel_running_video_{from_belt,stops}` (cooperative-cancel timing) intermittently fails under
heavy concurrency but passes isolated/retried — not a regression.
