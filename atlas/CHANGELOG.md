# Changelog — Atlas (the YT Manager / Showrunner)

## 0.4.0 — Atlas-only, chat-driven (2026-06-27)

A decisive restructure: **Atlas is the SOLE orchestrator, reached only through chat.**
The deterministic pipeline and the monitoring dashboard are gone; the end-to-end video
flow now lives as a **playbook in Atlas's system prompt** that he runs by calling the
team's tools in sequence against one project workspace.

- **Removed the pipeline.** Deleted `pipeline.py`, the `produce_video` tool, the
  `produce` CLI subcommand, and the assembly-line belt (`dispatcher.py`,
  `supervisor.py`, `atlas_decider.py`). Stage order, gates-as-state-machine, and
  per-stage mandatory validation are gone.
- **Atlas orchestrates via a PLAYBOOK.** `orchestrator.py`'s system prompt now carries
  the canonical sequence (start_project → research → script → factcheck → style →
  storyboard → assets → narration → compose → audiomix → render) and runs it by calling
  the agent job tools. Fact-check is a **conversational checkpoint** — a `block` verdict
  routes back to Marlow and re-checks; it is never approved away. Atlas pauses and asks
  before the final render, and deviates freely for partial/iterative requests.
- **Lightweight per-project manifest** (`projects.py`): `projects/<slug>/project.json`
  is a flat **checklist** of produced artifacts (done/pending + path, + the factcheck
  verdict) — not a state machine. `start_project`, `project_status`, and the optional
  `validate_artifact` are the only non-registry tools. Every job tool is now **slug-wired**:
  it reads upstream artifacts from `projects/<slug>/` and writes its output there, so a
  sequence of delegations accumulates ONE video. The old topic-heuristic
  `_resolve_project_dir` in each adapter is replaced by explicit-slug resolution.
- **Removed the dashboard.** Deleted `atlas/dashboard/` entirely (FastAPI monitoring UI
  + its 19 test files / Playwright e2e) and `project_view.py` (the gate-card read views).
- **One chat UI.** The Chainlit `web/app.py` is the single interface; its gate-button
  machinery is removed (approvals are conversational). `session.py` is decoupled from the
  pipeline (`approve_gate` / `latest_blocked_project` removed). The terminal REPL
  (`chat.py`) remains as a dev fallback over the same session core.
- **Kept:** `registry.py`, `adapters/`, the registry-driven tool generation, the 6
  specialist projects + engines and their determinism, `contracts/` (now surfaced via the
  optional `validate_artifact` tool), and `eval/` + `rubric/` + the off-playbook agents
  (Scout/Vera/Quill/Flux), untouched.

## 0.3.0 — Full fleet, real engines (2026-06-22)

The state-of-the-world entry. Everything the 0.2.0 notes called a "stub" is now a
real engine; the pipeline runs end-to-end on real specialists.

- **All 7 pipeline roles run REAL engines — no stubs.** The five former stub slots
  (Scriptwriter **Marlow**, Art Director **Iris**, Asset Sourcer **Magpie**, Audio
  **Cadence**, Composition Engineer **Mason**) are built and dropped into their
  registered slots; Scout and Sage predate the pipeline. The 10-stage line is
  research→script→factcheck (★gate)→style→storyboard→assets ∥ narration→compose
  (▲auto-gate)→audiomix→render (★gate)→`video.mp4`. The `research` stage now binds
  Sage's real engine; the offline placeholder survives only as an **opt-in fallback**
  behind `ATLAS_RESEARCH_STUB` (and logs loudly when used).
- **8th agent — Vera 🔬 the Reference Analyst** (`reference_analyst`). A standalone
  delegable job + persona (job `reference_analyst_build_rubric`) that builds a
  `reference_rubric` from reference videos via FFmpeg/OpenCV. It is a STANDARD/job,
  **not** a pipeline stage. Adds `contracts/reference_rubric.schema.json`,
  `adapters/reference_analyst.py`, and its tests — surfaced through the registry with
  no orchestrator change.
- **Issue #2 ("irrelevant footage") closed.** Brand chips (Mason renders real inline
  brand logos for un-sourceable trademarked marks; Iris auto-tags `kind:'brand'` shots;
  Magpie skips render-kind asset rows) + relevance-first sourcing (Magpie's
  `rank_candidates` ranks by relevance, license only breaks ties; a relevance floor
  ships a clean placeholder instead of zero-relevance footage) + named-model fallback.
- **Owner-run fixes:** model IDs normalized to full slugs (creative agents on
  `claude-opus-4-8`, others `claude-sonnet-4-6`); Mason render fixes (font, native
  data-chart rendering, a contrast-blocking gate, caption legibility).

## 0.2.0 — Showrunner (evolve Atlas into the executive producer)

Evolves Atlas's *role* from chief-of-staff to **Showrunner** of an explainer-video
agency. Additive — Atlas's character, registry, adapters, memory, and chat are
preserved and extended; Scout and Sage behavior is unchanged.

- **Frozen artifact contracts** (`contracts/`): JSON Schema (Draft 2020-12) for
  `project`, `research_brief` (reuses Sage's pack shape + envelope), `script`,
  `factcheck_report`, `style_guide`, `storyboard`, `asset_manifest`,
  `narration_transcript`, `audio_manifest`, plus a `jsonschema`-backed validator.
  `style_guide`/`storyboard` are additively extensible via `schema_version`
  (`additionalProperties: true`).
- **Registry → 7 roles** (`registry.py`): Topic Scout + Researcher/Fact-Checker
  (real) and five registered **stub** slots — Scriptwriter, Art Director, Asset
  Sourcer, Audio, Composition Engineer. Sage keeps `research` and gains a temporary
  `factcheck` JobSpec (Option A: stub, does NOT call Sage's engine). `stub`/`role`
  fields; `roster()` shows per-agent status.
- **Stub specialists** (`adapters/stubs.py`): deterministic, offline producers — each
  reads its upstream artifact and writes a schema-valid placeholder, so the full
  data-flow and contract validation run end-to-end with no network.
- **Production spine** (`pipeline.py`): deterministic stage order, contract validation
  before every advance, the composition auto-gate (lint+validate+inspect per scene),
  and the two human gates as **pause-and-resume via `project.json`** — the runner
  returns `blocked_at_<gate>` + details and persists state; it does not block
  mid-tool. A fact-check `block` verdict cannot be approved away (routes back) and is
  not bypassed even in unattended mode. Resumable + idempotent.
- **CLI + tool**: `run.py produce "<brief>" [--unattended] [--resume <slug> --approve
  <gate>]`; a `produce_video` orchestrator tool so Atlas can run the line from the
  meeting room.
- **Persona evolved** (`soul/`): showrunner worldview (one point per scene; the
  fact-check gate is sacred; the kept `#FFD000` contradiction), line-producer voice,
  calibration samples, and a `validate_persona.py` weak-model harness.
- **Chat**: `/agents` now shows each role's status (ready vs stub) + the production
  capability. `/ask`, `/summary`, `/new`, `/help`, `/exit` unchanged.
- **Tests**: `test_contracts.py`, `test_showrunner_registry.py`, `test_pipeline.py`
  (contracts valid+invalid, 7-role registration, stub dispatch, gate pause/resume,
  block-cannot-be-approved, playbook ordering). 58 passing.

## 0.1.0 — Phase 1 + Phase 2

### Phase 1 — orchestration core (proven end-to-end)
- **Registry** (`registry.py`): one entry per managed agent (name, blurb, capabilities,
  adapter, project dir). Adding a future agent = one entry + one adapter, no
  orchestrator changes.
- **Adapters** (`adapters/`): `loader.py` (in-process sibling-engine import with
  module-name isolation, a load-once cache, and a thread lock), `base.py` (uniform
  `run_job` + persona `ask`), `scout.py`, `sage.py`. The siblings are imported, never
  modified.
- **Tool generation** (`tools.py`): SDK in-process tools generated FROM the registry
  (`scout_find_topics`, `sage_research`, `ask_scout`, `ask_sage`), each with error
  containment + a per-job timeout, dispatched via `asyncio.to_thread`.
- **Orchestrator** (`orchestrator.py`): Atlas on the Claude Agent SDK `query()` loop,
  autonomous tool use (no per-step gate), streaming reasoning. System prompt encodes
  the default playbook (Scout → decide & say why → Sage → report).
- **Deterministic progress** (`progress.py`): 🔎/📚/✅ status emitted from inside the
  tools; decisions/synthesis are Atlas's streamed text.
- Verified with one full autonomous run on a real niche (Scout → Atlas decision →
  Sage research with 26 sources → Atlas's CEO brief).

### Phase 2 — the meeting room
- **`chat.py`**: the primary interface (`python run.py chat`). Routes each message
  through the orchestrator; supports deterministic direct routing via `/ask`.
- **Summary-only memory**: distill on `/exit`, Ctrl+C, `/new`, `/summary` through the
  LLM seam; merge + bound; clear the transcript; persist only the summary;
  no-data-loss "pending" fallback on distill failure; accurate self-description.
  Reworked distill prompt for the multi-agent room (the CEO + the studio, not a single
  researcher).
- **Commands**: `/agents` (roster + each agent's effective provider), `/ask <agent>
  <question>`, `/summary`, `/new`, `/help`, `/exit` (`/quit` alias).
- **Cross-fleet memory snapshot**: Atlas surfaces recent work from each agent's own
  memory for grounded awareness.

### Provider policy
- Default brain: Claude via Claude Code subscription (no API key; warns if
  `ANTHROPIC_API_KEY` is set). `ATLAS_LLM` switches Atlas to Gemini/DeepSeek. No
  Ollama. `ATLAS_LLM` governs Atlas; delegated jobs inherit each sibling's `SAGE_LLM`.

### Review (pre-build)
- Plan reviewed via an adapted `/autoplan` (single-voice — Codex sandbox unavailable
  in this environment). 11 safety/DX findings folded into the build (loader lock +
  load-once, two-layer error containment, job timeout, Atlas-owned validation,
  provider precedence + surfacing, honest extensibility framing). See `PLAN.md`.

### Tests
- 32 pure-unit tests (no network/API): registry + tool generation (incl. a mock agent
  surfacing tools with zero orchestrator change), direct-address routing, mocked-
  adapter tool order + progress lines + error containment + timeout, loader identity/
  idempotency/restore, the event-loop-nesting mitigation, atomic write + corrupt
  recovery, validation, system-prompt split, and distill + pending fallback.
- Manual/integration (not unit-tested, by nature): the real multi-agent run and real
  cross-session recall — both exercised by hand during the build.
