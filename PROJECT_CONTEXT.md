# PROJECT_CONTEXT.md — YT-AGENTS

> Onboarding document for an AI assistant with **zero prior knowledge** of this codebase.
> Read this top-to-bottom and you can discuss the project intelligently and suggest changes.
> Where something is inferred rather than verified, it says so.

---

## 1. Project Overview

**YT-AGENTS is a multi-agent "video agency" that turns a topic brief into a finished,
narrated, fact-checked explainer video — autonomously.** Every pipeline stage now runs a real
specialist engine (the last placeholder, the `research` stage, was wired to Sage — see §12).
You (the "CEO") talk to a single
manager agent called **Atlas (the Showrunner)** in a chat "meeting room" — either the
terminal REPL or the **web operator UI** (a Chainlit app; now fully built, see §12). Atlas
delegates to a fleet of specialist agents — each a self-contained Python project with its own
personality, brain, and memory — and runs them through a deterministic production
pipeline, pausing at two human approval "gates," and produces a final `video.mp4`.

**The problem it solves:** producing an explainer video normally means coordinating a
researcher, a fact-checker, a scriptwriter, an art director, an asset/licensing person, an
audio engineer, and a motion-graphics builder. This project encodes each of those as an
**LLM-backed agent** and wires them into one supervised, contract-validated assembly line
that can run end-to-end with no human in the loop except at two sign-off points.

**Who it's for:** a solo creator / "CEO of a one-person agency" who wants a fleet of
agents to do the production grind while they stay in the director's chair.

**Status (high level):** It works end-to-end, and **multiple real `video.mp4` deliverables have
shipped** (see §12 — both pilot videos and the first "upgraded" run on the live Opus seam reached a
final cut). **All 7 pipeline roles run real engines** — Scout and Sage predate the pipeline; the
other five specialists were built and dropped into their registered slots, and the former
`research`-stage stub is now wired to Sage's real engine (the stub survives only as an opt-in offline
fallback; see §12). The deterministic spine has since **grown a three-stage creative-architecture
layer** (Iris's `treatment` → `narrative_intent` → `motion_mood_board`), so the line is now
**13 stages**, not 10. Beyond the terminal, a **Chainlit web
operator UI** and a **FastAPI "Control Room" dashboard** are both fully built as additional
frontends (§8, §12).

The registry holds **10 agents**: the 7 pipeline specialists plus three **additive, off-pipeline**
agents that power the **self-improvement / evaluation system** (§13): **Vera 🔬** the Reference
Analyst (builds a `reference_rubric` from reference videos — defines the standard), and two domain
coaches — **Quill 🖋️** (editorial/content) and **Flux 🎚️** (production/craft) — that author coaching
addenda when the eval loop diagnoses a quality shortfall. None of the three is a pipeline stage; the
13-stage line is unchanged by them.

> **Where this doc is "as of":** the live work is now on the **`control-room`** branch (68 commits
> ahead of `main`, which remains the upstream default). The newest things to land (a "creative quality"
> push on top of everything below): the **`narrative_intent` + `motion_mood_board` stages** (the
> creative layer grew from 1 to 3 stages — §6/§7), **signature WebGL shader transitions** that Mason
> actually renders and Iris chooses per-beat (§6/§7), the **opinionated persona overhaul**, **Marlow's
> live creative roundtable** (an internal Critic→Researcher→Craftsman self-review), and a **roundtable
> log wired into the eval/coach loop** as a process side-channel (§13). Earlier big landings still
> current: the `treatment` stage, the **diagram generator** (flagged), the **eval / self-improvement
> system committed and Phase-2-complete** (§13), and the dashboard's growth into a full **Control
> Room** (§8).

---

## 2. Tech Stack

| Layer | Choice |
|---|---|
| **Language** | Python 3 (uses `from __future__ import annotations`; targets 3.10+ syntax) |
| **Runtime** | A single shared `venv/` at the repo root (all agents share one environment) |
| **Default LLM brain** | **Claude via the Claude Code subscription**, through `claude-agent-sdk` (>=0.2.105) — **no API key**, draws from your Pro/Max plan, NOT the metered API |
| **Alternative brains** | Google Gemini (`google-generativeai`, `gemini-2.5-flash`) and DeepSeek (raw `requests`, `deepseek-v4-flash`), each behind a per-agent env switch |
| **Contract validation** | `jsonschema` (Draft 2020-12) |
| **Config / secrets** | `python-dotenv`, one shared root `.env` |
| **Video render engine** | **HyperFrames** — a Node.js CLI (`npx hyperframes`), NOT a Python dep. Needs **Node ≥ 22** + **FFmpeg/FFprobe** on PATH |
| **Text-to-speech** | **Kokoro TTS** (Kokoro-82M) via HyperFrames `tts` (`kokoro-onnx` + `soundfile`) |
| **Optional transcription** | `whisper.cpp` (word-level caption timing; optional, never required) |
| **External data APIs** | YouTube Data API v3 (Scout), Google Trends via `pytrends` (Scout), web search (DuckDuckGo `ddgs` default; Tavily/Brave optional), Wikipedia REST, GDELT news, plus PD/CC asset & audio archives (see §9) |
| **Package manager** | `pip` + per-project `requirements.txt` |
| **Web UI (optional)** | **Chainlit** 2.11.1 (in-process, additive) — the web "meeting room" at `atlas/web/app.py`; deps in `atlas/requirements-web.txt`, runtime config in `.chainlit/`. The terminal REPL needs none of it. |
| **Dashboard (optional)** | **FastAPI + uvicorn** (additive, read-mostly) — the "Control Room" monitoring service at `atlas/dashboard/`; deps in `atlas/dashboard/requirements.txt`; launched by `./yt-atlas` (port 8848). Playwright for its E2E tests. |
| **Tests** | `pytest` (43 test files under `atlas/tests/` incl. 16 `test_eval_*` and roundtable tests, plus 16 under `atlas/dashboard/tests/` incl. Playwright E2E, plus each specialist's own suite; pure unit tests, no network) |
| **Version control** | **Git** (active branch is **`control-room`**, 68 commits ahead of **`main`**, which is the upstream default). Earlier work landed on `main`/`owner-run-fixes`; the eval/self-improvement system (§13), the diagram generator, the creative-architecture stages (`treatment`/`narrative_intent`/`motion_mood_board`), the shader transitions, the creative roundtable, and the Control Room all live on `control-room` and are **committed**. |

A **web frontend now exists**: `atlas/session.py` is the UI-neutral core shared by both the
terminal REPL (`atlas/chat.py`) and the Chainlit web operator UI (`atlas/web/app.py`). Both
drive the same session core and share one `chat_state.json` (last-writer-wins — don't run both
at once). See §12 for the web UI's status (all phases A–D complete).

---

## 3. Architecture

The system has two distinct planes that are deliberately kept separate:

1. **The conversational plane (Atlas the Showrunner):** an LLM agent on the Claude Agent
   SDK `query()` loop. It picks topics, announces decisions in-character, relays gate
   details to the CEO, and decides which tools to call. It is *not trusted* to guarantee
   correctness.
2. **The deterministic plane (the production spine):** `atlas/pipeline.py`, a plain
   state-machine that runs stages in a fixed order, validates every artifact against a
   frozen JSON-Schema contract before advancing, enforces the two human gates as
   pause-and-resume, and is resumable/idempotent.

The glue is a **registry + adapters + generated tools** pattern: each specialist agent is
ONE entry in `registry.py`; tools are generated *from* the registry; adding an agent =
one registry entry + one adapter, with **no orchestrator changes**.

```
            ┌──────────────── You (the CEO) ────────────────┐
            │        chat.py / session.py  (meeting room)     │
            └───────────────────────┬────────────────────────┘
                              orchestrator.py
                       (Claude Agent SDK query() loop)
                                    │  tools generated FROM…
                                registry.py ──── adapters/ ──── loader.py
                                    │            (uniform wrap)  (in-process,
                                    │                             isolated import)
                    ┌───────────────┴────────────────┐
                    │  produce_video tool             │  ask_<agent> / <agent>_<job> tools
                    ▼                                  ▼
              pipeline.py  ◄── validates each stage ──►  contracts/*.schema.json
              (deterministic spine)                      (frozen artifact shapes)
                    │
   research → treatment → narrative_intent → motion_mood_board → script → factcheck ★GATE
        → style → storyboard → assets ∥ narration → compose ▲auto-gate → audiomix → render ★GATE
        → video.mp4   (13 stages; the 3-stage creative layer treatment→intent→mood_board is sequential)
                    │  each stage's producer calls a specialist engine in-process
                    ▼
        youtube-topic-agent/  topic-researcher/  scriptwriter/  art-director/
        asset-sourcer/  audio-designer/  composition-engineer/   (sibling projects)
                                    │
                              npx hyperframes  (tts / lint / validate / inspect / render)
                                    +  FFmpeg  (concat, mix, mux)
```

**Request/data flow for "make me a video":**
1. CEO asks Atlas for a video → Atlas calls the `produce_video` tool with a `brief`.
2. `pipeline.produce()` creates a `project.json` under `atlas/projects/<slug>/` and runs
   stages in order. Each stage's **producer** reads the upstream artifact(s) from the
   project dir, calls a specialist's **engine in-process**, and writes a new artifact
   (e.g. `script.json`).
3. After each stage, the pipeline **validates the artifact against its frozen contract**.
   A failure blocks the stage (it does not crash).
4. At the **fact-check gate** and the **final-render gate**, the pipeline persists
   `status: "blocked_at_<gate>"` to `project.json` and **returns** — it never blocks
   mid-tool. Atlas relays the details to the CEO and waits.
5. CEO signs off → Atlas re-invokes `produce_video` with `approve=<gate>`; the pipeline
   resumes from where it left off (already-done stages are skipped).
6. Final stage renders + muxes audio → `video.mp4`.

**Why in-process (not subprocess):** all sibling projects ship modules with the *same bare
names* (`llm`, `chat_state`, `search`, …). `adapters/loader.py` imports each engine with its
module graph isolated (snapshot `sys.path`/`sys.modules`, drop colliding names, load,
restore), caches load-once, and guards the mutation with a thread lock — so two engines run
in one process each bound to its own `llm`. Synchronous engines spin their own event loop, so
jobs are dispatched via `asyncio.to_thread` to avoid nesting with the SDK's loop.

---

## 4. Directory Structure

```
YT-AGENTS/
├── .env                       # SHARED secrets for the whole fleet (root-level; gitignored)
├── skills-lock.json           # pinned HyperFrames doc-skills (from heygen-com/hyperframes)
├── .chainlit/                 # Chainlit runtime config (config.toml + translations) for the web UI
├── venv/                      # the one shared virtualenv (noise — skip)
│
├── rubric-decomposition.md            # design doc: the per-artifact rubric + credit-assignment model (§13)
├── self-improvement-enhancement-decisions.md  # design doc: the self-improvement loop's principles + guardrails (§13)
├── yt-atlas                           # ★ one-command launcher (bash) for the Control Room dashboard (port 8848)
├── yt-agents-dashboard.html           # the approved static PROTOTYPE that the real atlas/dashboard/ app implements
├── docs/                              # phase reports for the self-improvement work (phase1-report, phase2-plan, …)
├── ReferanceVideos/                   # reference videos the rubric/calibration are derived from (sic: spelling)
│
├── atlas/                     # ★ THE SHOWRUNNER / ORCHESTRATOR (the brain of the system)
│   ├── run.py                 # entry point: `chat` (meeting room) | "<niche>" | `produce …`
│   ├── chat.py                # terminal REPL frontend (commands, memory, SIGINT handling)
│   ├── session.py             # ★ UI-neutral session core (AtlasSession + AgentSession + SessionRegistry)
│   ├── project_view.py        # read-only artifact previews + find_latest_blocked (web gate cards)
│   ├── web/                   # ★ the Chainlit web operator UI (optional, additive)
│   │   ├── app.py             #   Chainlit app: streaming chat, gate buttons, roster, media previews
│   │   └── README.md          #   how to run it (chainlit run web/app.py -w → :8000)
│   ├── requirements-web.txt   # web-only deps (chainlit 2.11.1) — terminal needs none of it
│   ├── web_sessions/          # per-agent web persona memory (created at runtime; separate from terminal state)
│   ├── dashboard/             # ★ the FastAPI "Control Room" monitoring service (optional, read-mostly) — §8
│   │   ├── app.py             #   create_app(): FastAPI factory; serves typed JSON + the static Control Room UI
│   │   ├── server.py          #   uvicorn launcher (python -m dashboard.server [--port 8848] [--projects DIR])
│   │   ├── data.py media.py   #   reads live state (registry, project.json, artifacts, souls, eval scorecards)
│   │   ├── atlas_request.py   #   the single typed front door: POST /api/atlas/request → handle_request(...)
│   │   ├── intake.py          #   niche intake (#1.5): niche → Scout find_topics → candidate cards
│   │   ├── settings_store.py  #   dashboard-owned settings JSON (#4): niches/defaults/channels → pipeline args
│   │   ├── publish.py chat.py #   Herald T3 publish package (read-only, fires nothing) + chat surface
│   │   ├── security.py        #   write guardrails; sanctioned writes routed through the existing seams
│   │   ├── static/            #   the Control Room front-end assets
│   │   ├── tests/             #   API + security + intake + publish + real-gate-write + Playwright E2E (e2e/)
│   │   ├── requirements.txt REPORT.md __init__.py
│   ├── orchestrator.py        # Atlas's brain: SDK query() loop, system prompt, playbooks
│   ├── registry.py            # ★ THE REGISTRY — one AgentEntry per agent; the source of truth
│   ├── tools.py               # generates SDK tools FROM the registry (+ timeout/containment)
│   ├── pipeline.py            # ★ THE PRODUCTION SPINE — deterministic stages + gates + resume
│   ├── progress.py            # deterministic 🔎/📝/✅ status-line emitter
│   ├── llm.py                 # Atlas's brain seam (ATLAS_LLM switch)
│   ├── validate.py            # niche/topic validation
│   ├── chat_state.py          # atomic JSON writes + tolerant loads (summary-only memory)
│   ├── contracts/             # ★ FROZEN ARTIFACT CONTRACTS (JSON Schema) + validator
│   │   ├── __init__.py        #   validate(name, obj), CONTRACT_VERSION, version_for()
│   │   └── *.schema.json      #   14 contracts: project, research_brief, creative_treatment,
│   │                          #     narrative_intent, motion_mood_board, script, factcheck_report, …
│   ├── rubric/                # ★ FROZEN, CEO-OWNED QUALITY STANDARD (read-only; NO write path) — §13
│   │   ├── __init__.py        #   deep-frozen accessors (load_rubric, bands, global_weights…); no writers
│   │   └── rubric.json        #   v0.2.0-phase2-calibrated: 6 weighted dims + 1 floor + per-stage bands
│   ├── eval/                  # ★ THE EVALUATION / SELF-IMPROVEMENT SUBSYSTEM (read-only over rubric) — §13
│   │   ├── inspector.py       #   orchestrates analyzers → scorecard (python -m eval.inspector projects/<slug>)
│   │   ├── analyzers/         #   text.py (structural JSON) · audio.py (ffmpeg) · video.py (ffprobe/frame-diff)
│   │   │                      #     · roundtable.py (PROCESS side-channel: reads roundtable_log.json — §13)
│   │   ├── judged.py          #   the only LLM analyzer: ensembled, seeded pairwise-vs-reference judging
│   │   ├── rollup.py          #   gate(measurement, band) + roll_up to global dimensions + floor
│   │   ├── diagnose.py        #   credit assignment → one primary failing property to fix
│   │   ├── loop.py            #   inspect→diagnose→propose→re-measure; WriteBoundaryError; coach routing
│   │   ├── calibrate.py       #   propose bands from reference videos → rubric.proposal.json (never rubric.json)
│   │   ├── holdout.py         #   train/test split; reject a change that regresses any held-out pass
│   │   ├── tracking.py        #   append-only JSONL store (runs/eval_runs.jsonl) + noise_floor()
│   │   ├── validation.py      #   eval-of-the-eval: every gated band must pass-good / fail-bad
│   │   ├── types.py           #   Measurement dataclass + Analyzer/EvalContext
│   │   └── runs/              #   append-only evaluation results (created at runtime)
│   ├── adapters/              # uniform wrappers around each specialist (no sibling edits)
│   │   ├── loader.py          #   in-process isolated import (the collision fix)
│   │   ├── base.py            #   Adapter ABC: run_job (JOB) + ask (PERSONA)
│   │   ├── stubs.py           #   offline placeholder producers + StubAdapter
│   │   ├── scout.py sage.py scriptwriter.py art_director.py
│   │   ├── asset_sourcer.py audio.py composition_engineer.py
│   │   ├── reference_analyst.py            # Vera 🔬 — builds the reference_rubric standard (off-pipeline)
│   │   ├── editorial_coach.py production_coach.py   # Quill 🖋️ + Flux 🎚️ — the two domain coaches (§13)
│   ├── soul/                  # Atlas's persona: SOUL.md + STYLE.md + examples/
│   ├── projects/              # ★ per-video working dirs (project.json + all artifacts + assets)
│   ├── tests/                 # 43 test files (contracts, pipeline, registry, routing, + 16 test_eval_* + coaches + roundtable)
│   ├── README.md PLAN.md CHANGELOG.md   # (README/PLAN describe the early Scout+Sage phase)
│   └── atlas.log              # produce_video arg-logging (INFO)
│
├── youtube-topic-agent/       # "Viral Scout" — finds ranked viral YouTube topic ideas
│   ├── agent.py youtube.py trends.py   # engine + YouTube Data API + Google Trends
│   ├── run.py chat.py llm.py chat_state.py compaction.py
│   ├── memory.json channel_cache.json trends_cache.json
│   ├── SKILL.md soul/ tests/ README.md
│
├── topic-researcher/          # "Sage" — research pack (Pass 1) + script fact-check (Pass 2)
│   ├── researcher.py factcheck.py search.py
│   ├── run.py chat.py llm.py compaction.py memory.json
│   ├── research_packs/        # saved JSON + Markdown research packs
│   ├── SKILL.md soul/ tests/ README.md
│
├── scriptwriter/              # "Marlow" — research brief → one-point-per-scene script
│   └── script_engine.py roundtable.py run.py chat.py llm.py SKILL.md soul/ tests/
│                              #   roundtable.py = the internal Critic→Researcher→Craftsman self-review (§6/§13)
├── art-director/              # "Iris" — treatment + narrative_intent + motion_mood_board + style + storyboard
│   └── art_engine.py run.py chat.py llm.py SKILL.md soul/ tests/
├── asset-sourcer/             # "Magpie" — storyboard → license-cleared asset_manifest.json (+ diagram PLANs)
│   └── source_engine.py sources.py diagram_engine.py run.py chat.py llm.py assets/ SKILL.md soul/ tests/
├── audio-designer/            # "Cadence" — narration (TTS) + documentary audio mix
│   └── audio_engine.py audio_sources.py sfx_kit.py hf_audio.py run.py llm.py SKILL.md soul/
├── composition-engineer/      # "Mason" — artifacts → HyperFrames HTML + render → video.mp4
│   └── composition_engine.py diagram_render.py shader_transition.py hf_tools.py run.py chat.py llm.py SKILL.md soul/ tests/
│                              #   shader_transition.py = signature WebGL transitions at the assembly seam (§6)
│
├── reference-analyst/         # "Vera" 🔬 — reference videos → reference_rubric (the STANDARD)
│   └── *_engine.py run.py chat.py llm.py standards/ SKILL.md soul/ tests/
├── editorial-coach/           # "Quill" 🖋️ — editorial/content coach (off-pipeline; §13)
│   └── coach_engine.py run.py chat.py llm.py SKILL.md soul/ tests/
├── production-coach/          # "Flux" 🎚️ — production/craft coach (off-pipeline; §13)
│   └── coach_engine.py run.py chat.py llm.py SKILL.md soul/ tests/
│
└── .agents/skills/            # HyperFrames documentation skills (hyperframes, -cli, -media,
                               #   -animation, faceless-explainer, embedded-captions, …)
```

Every specialist project follows the **same skeleton**: an `*_engine.py` (pure, deterministic,
LLM-injectable), `run.py` (CLI), `chat.py` (co-worker REPL), `llm.py` (provider seam),
`chat_state.py`/`compaction.py` (memory), `SKILL.md` (the engine's job contract / method),
`soul/` (SOUL.md identity + STYLE.md voice + examples/), and `tests/`.

---

## 5. Entry Points & Core Flow

**Primary entry point: `atlas/run.py`.** From inside `atlas/`:

| Command | What it does |
|---|---|
| `python run.py chat` | The **meeting room** REPL (the main interface). |
| `python run.py "<niche>"` | One-shot: Scout finds topics → Atlas decides → Sage researches → reports. |
| `python run.py produce "<brief>" [--unattended] [--resume <slug> --approve <gate>]` | Run/resume the full video pipeline from the CLI. |

**Secondary entry point: the web operator UI.** From inside `atlas/`:
`chainlit run web/app.py -w` → http://localhost:8000 — the same meeting room as a web app, with
the two gates rendered as **Approve / Revise buttons**, inline artifact/media previews, a roster
sidebar, and per-agent persona chat. It drives the same `session.py` core (no orchestrator/pipeline
changes). See §12 and `atlas/web/README.md`.

**Core code path for a meeting turn:**
1. `chat.py` → `session.AtlasSession.send()` ([atlas/session.py:318](atlas/session.py#L318)) —
   builds bounded context (durable summary + fleet snapshot + recent window) and calls the orchestrator.
2. `orchestrator.Orchestrator.run_turn_async()` ([atlas/orchestrator.py:171](atlas/orchestrator.py#L171)) —
   runs the Claude Agent SDK `query()` loop with `permission_mode="bypassPermissions"` (tools
   auto-run), streaming Atlas's text and auto-executing tools.
3. Tools are generated by `tools.build_server()` ([atlas/tools.py:222](atlas/tools.py#L222)) —
   one `<agent>_<job>` + one `ask_<agent>` per registry entry, plus the single `produce_video` tool.

**Core code path for video production:**
- `tools._make_produce_tool` → `pipeline.produce()` ([atlas/pipeline.py:296](atlas/pipeline.py#L296)) —
  the deterministic runner. Its `STAGES` list ([atlas/pipeline.py:98](atlas/pipeline.py#L98)) is the
  one fixed order (now 13 stages — the sequential creative layer `treatment → narrative_intent →
  motion_mood_board` sits between `research` and `script`); gate checkpoints are `_factcheck_gate()`
  and `_final_render_gate()`.

---

## 6. Key Modules / Components

### Atlas core
- **`registry.py`** — the single source of truth for "who Atlas can delegate to." Each
  `AgentEntry` has `name/display/emoji/blurb/project_dir/adapter_cls/jobs/role`. A `JobSpec`
  declares a delegable job (`name`, generated `tool` name, `description`, `params`, `timeout`).
  Helpers: `build_adapters()`, `get_entry()`, `roster()` (the `/agents` call sheet).
- **`adapters/`** — uniform wrap of each agent **without modifying it**. `base.Adapter` gives two
  capabilities: a **JOB** (`run_job` — calls the engine in-process, returns a compact digest) and a
  **PERSONA** (`ask` — loads the agent's `SOUL.md`+`STYLE.md` and replies in-character via Atlas's
  `llm` seam). `loader.load_engine()` is the isolated, cached, thread-safe importer.
- **`tools.py`** — turns the registry into SDK tools. Two hardening guarantees live here: **error
  containment** (every call wrapped so a sibling exception becomes a narratable tool result, never a
  crash) and **per-job timeout** (`asyncio.wait_for`). `produce_video` uses a full JSON Schema with
  `required: []` so both `{brief}` (new) and `{slug, approve}` (resume) shapes are valid; the handler
  enforces "exactly one of."
- **`pipeline.py`** — the deterministic spine (see §3/§5). Owns stage order, contract validation,
  the composition auto-gate, the two human gates (pause-and-resume via `project.json`), and resume
  logic. **Key rule:** a fact-check `block` verdict **can never be approved away** — it routes back
  upstream and re-blocks until the script is fixed and re-checked (`_factcheck_gate`,
  [atlas/pipeline.py:430](atlas/pipeline.py#L430)).
- **`contracts/`** — frozen JSON-Schema shapes for every artifact, `additionalProperties: true`
  (frozen-but-extensible). `validate(name, obj) -> (ok, errors)` never raises on bad data.
- **`session.py`** — UI-neutral session core shared by both frontends: `AtlasSession`
  (send/ask_agent/summarize/new_thread + memory, context assembly, status routing, plus
  `latest_blocked_project()` / `approve_gate(slug, gate)` for gate buttons), `AgentSession`
  (per-agent persona chat via `adapter.ask`, own summary-only memory under `atlas/web_sessions/`),
  and `SessionRegistry` (process-level cache that *resumes* a session on a web profile switch).
  Streaming reuses the orchestrator's already-callback-parameterized seams (`on_text=`, `Progress(sink=)`),
  so `orchestrator.py` is untouched by the web UI.
- **`project_view.py`** — read-only artifact previews + `find_latest_blocked()`; feeds the web gate cards.
- **`rubric/` + `eval/`** — the **self-improvement / evaluation system** (full detail in §13). `rubric/` is the
  frozen, CEO-owned quality standard (read-only, no write path); `eval/` measures a finished project against it
  (deterministic + ensembled-judge analyzers → scorecard), diagnoses the single biggest shortfall, and lets the
  loop propose a **soft-tier-only** coaching fix via the Quill/Flux coaches. Strictly read-only over the rubric,
  contracts, and spine — enforced structurally by `WriteBoundaryError`.

### The ten agents (each is an independent, runnable project)

The first seven are pipeline specialists; the last three (Vera, Quill, Flux) are **additive, off-pipeline**
agents for the self-improvement system (§13).

| Agent (project) | Persona | Role | Engine entry point(s) | Reads → Writes |
|---|---|---|---|---|
| `youtube-topic-agent` | **Viral Scout** 🔎 | Topic intake | `agent.run(niche, deep=False)` → ranked ideas | YouTube + Trends → topic ideas (in memory) |
| `topic-researcher` | **Sage** 📚 | Researcher & Fact-Checker | `researcher.run(topic, angle)` (Pass 1); `factcheck.factcheck(script, brief)` (Pass 2) | web → `research_brief`; script+brief → `factcheck_report` |
| `scriptwriter` | **Marlow** 📝 | Scriptwriter | `script_engine.write_script(brief)` | `research_brief.json` → `script.json` |
| `art-director` | **Iris** 🎨 | Art Director | `art_engine.design_treatment(brief)`; `art_engine.design_narrative_intent(treatment, brief)`; `art_engine.design_motion_mood_board(intent)`; `art_engine.design_style(script)`; `art_engine.build_storyboard(script, style_guide)` | `research_brief.json` → `creative_treatment.json` → `narrative_intent.json` → `motion_mood_board.json`; `script.json` → `style_guide.json` + `storyboard.json` |
| `asset-sourcer` | **Magpie** 🗂️ | Asset Sourcer & Licensing | `source_engine.source_assets(storyboard, style_guide, client, pdir)` | `storyboard.json` → `asset_manifest.json` + downloaded files |
| `audio-designer` | **Cadence** 🎙️ | Audio / Sound Designer | `audio_engine.record_narration(script, pdir)`; `audio_engine.mix_audio(...)` | `script.json` → `narration.wav` + `narration.transcript.json` + `master.wav` + `audio_manifest.json` |
| `composition-engineer` | **Mason** 🛠️ | Composition Engineer | `composition_engine.compose(pdir)`; `composition_engine.run_render(pdir)` | all artifacts → scene HTML + `composition_manifest.json` → `video.mp4` |
| `reference-analyst` | **Vera** 🔬 | Reference Analyst (off-pipeline job) | `reference_analyst` engine over reference videos (FFmpeg/OpenCV) | reference videos → `reference_rubric` (a STANDARD, not a pipeline artifact) |
| `editorial-coach` | **Quill** 🖋️ | Editorial/Content Coach (off-pipeline; §13) | `coach_engine.propose_addendum(band_id, direction, …)` | diagnosed editorial shortfall → markdown coaching addendum (text only) |
| `production-coach` | **Flux** 🎚️ | Production/Craft Coach (off-pipeline; §13) | `coach_engine.propose_addendum(band_id, direction, …)` | diagnosed production shortfall → markdown coaching addendum (text only) |

**A few critical domain mechanisms:**
- **Iris's "one #FFD000 beat":** exactly one scene per video carries the `highlighter-FFD000`
  signature effect (riso-yellow highlighter sweep). It's never trimmed under the motion budget.
  Iris *specifies*, never implements (no HTML); Mason builds it.
- **Magpie's license truth table:** an asset reaches `cleared` only with an accept-list license
  (CC0/PDM/PD/CC-BY/CC-BY-SA) **and** complete attribution **and** a local file. Pexels/Pixabay/NASA
  are force-`sourced` (flagged for human review). Anything else → flagged local placeholder. URIs are
  always local (HyperFrames forbids render-time fetches).
- **Cadence's documentary mix ("master-bridge"):** VO is authoritative (0 dB) and is the sidechain
  key that hard-ducks the licensed music bed; one signature SFX lands on the cut into Iris's signature
  beat. Everything is pre-mixed into `master.wav`, and the narration track's `uri` points at the
  master — so the renderer muxes the full mix with **zero Composition Engineer edits**.
- **Mason's brand chips (issue #2, Direction A):** when a scene names AI models whose logos are
  un-sourceable, Mason renders a deterministic **brand card** instead of placeholder footage — the
  real inline SVG logo (Lobe Icons, MIT) over the model name, framed in the brand color. Several
  models in a scene become a "matchup" row; a model framed as de-emphasized gets a `dim` chip.
  Inlined as data (no render-time fetch), so it stays frame-seek deterministic.
- **Mason's auto-gate:** before spending a render, each scene passes a self-scan (no network/fetch,
  no SMIL filters, no late `gsap.set`) + HyperFrames `lint` + `validate` (headless Chrome) + `inspect`
  (layout/overflow + motion assertions). A scene that fails the gate blocks the stage.
- **Iris's three-stage creative-architecture layer (`treatment → narrative_intent → motion_mood_board`):**
  before scripting, Iris runs a sequential creative-direction pipeline, each stage building on the last,
  all on the strong creative model and all **advisory + optional + backward-compatible** (a missing
  artifact leaves every downstream stage on its prior behavior):
  - **`treatment`** (`art_engine.design_treatment(brief)`) expands the research brief into a grounded
    creative direction — rhythm (e.g. `hook-BUILD-PEAK-breathe-CTA`), a visual world, mood refs, per-beat
    concept/mood/emphasis — distilled from the HyperFrames craft library.
  - **`narrative_intent`** (`art_engine.design_narrative_intent(treatment, brief)`) translates that poetry
    into a machine-actionable **emotional score**: a video-level thesis/journey/tone, a five-phase
    emotional arc (`hook/build/peak/breathe/cta`), and per-scene directives (emotion + intensity + pacing +
    texture + a human delivery note) over a **closed emotion vocabulary**. It exists because the treatment's
    emotional objective used to evaporate at each handoff — script/audio only read structural keywords.
    Marlow reads it for tone/pacing; Cadence reads it for TTS pacing/EQ/music.
  - **`motion_mood_board`** (`art_engine.design_motion_mood_board(intent)`) inverts the creative logic:
    it translates the emotional arc into a concrete **visual architecture** (a per-beat `beat_map` of
    effect/layout/transition/texture) whose closed vocabularies **mirror Mason's real HyperFrames
    vocabularies exactly** (a cross-engine parity test guards lock-step), so Mason executes it without
    interpretation. It governs BOTH Marlow's pacing AND Mason's motion design.
- **Iris/Mason's signature WebGL shader transitions:** the FIRST transitions the engine actually renders.
  `composition-engineer/shader_transition.py` ports four GLSL transitions from the gl-transitions catalogue
  — `whip-pan` / `sdf-iris` / `glitch` / `domain-warp` (`SHADER_TRANSITIONS`) — rendered headless via
  SwiftShader (byte-stable), spliced net-zero-duration at the assembly seam so narration stays in sync,
  budgeted to **≤2 per video** (`SHADER_BUDGET`), with a graceful fallback to a hard cut. **Iris chooses**
  the transition per-beat, mood-matched, reserved for the boundary into the signature beat; an
  unknown/missing value falls back to Mason's taste default. Kept frame-seek deterministic. Kill switch
  `MASON_SHADER_TRANSITIONS=0`.
- **Marlow's live creative roundtable (`scriptwriter/roundtable.py`):** an internal, opt-in
  **Critic → Researcher → Craftsman** self-review the scriptwriter runs on its own draft — fresh-context
  sub-agents that critique against `SKILL.md`, do real `ddgs` search for a "killer detail," and revise.
  It writes a `roundtable_log.json` beside the script (a process record for the CEO + eval). Kill switch
  `MARLOW_ROUNDTABLE=0`. The blueprint (`docs/creative-roundtable-blueprint.md`) is being ported to the
  other specialists.
- **The diagram generator (Magpie plans → Mason renders; behind `MAGPIE_DIAGRAM_GEN`):** a
  plan-then-render path for *generated* visuals instead of sourced footage. Magpie's
  `diagram_engine.plan_diagram(shot)` (the PLAN half) classifies each shot — data-viz/charts and
  data-driven maps always **generate**; a *conceptual* diagram generates only when the flag is on — and
  caches a `DiagramPlan` in the `plan` object of `asset_manifest` (v1.1). Mason's `diagram_render.py`
  (the RENDER half) composes that plan to deterministic in-HTML SVG at render time (no fetch, frame-seek
  safe). Charts (`bar`/`line`/`pie`, `CHART_KINDS`) are drawn natively by Mason from the scene's
  numbers. The flag defaults **off**, so today's sourcing behavior is unchanged unless opted in.

---

## 7. Data Models & Schemas

The pipeline's "language" is a set of JSON artifacts, each pinned by a schema in
`atlas/contracts/`. All set `additionalProperties: true` and require a `schema_version` string.

| Artifact (file) | Contract | Purpose / key fields |
|---|---|---|
| `project.json` | `project` | **Master state** for one video: `project_id`, `slug`, `status`, `config.gates`, per-stage `stages{status, artifact, validated}`, `gates{factcheck, final_render}`, `artifacts`, `history`. |
| `research_brief.json` | `research_brief` | Sage's pack shape (reused): `verified_facts[]`, `myths_and_corrections[]`, `contested_or_uncertain[]`, `key_statistics`, `sources[]`, `suggested_angles`. |
| `creative_treatment.json` | `creative_treatment` | Iris's pre-script creative direction (advisory/optional): `rhythm`, `visual_world`, `mood_refs[]`, and per-beat `concept`/`mood`/`emphasis`. Consumed by the script + style + storyboard stages when present. |
| `narrative_intent.json` | `narrative_intent` (v1.0) | Iris's **emotional score** (advisory/optional): video-level `thesis`/`journey`/`tone`, a five-phase emotional arc, and per-scene `emotion`/`intensity`/`pacing`/`texture` + delivery note, over a **closed emotion vocabulary**. Read by the script + narration stages. |
| `motion_mood_board.json` | `motion_mood_board` (v1.0) | Iris's **visual architecture** (advisory/optional): a per-beat `beat_map` of effect/layout/transition/texture whose closed vocabularies mirror Mason's HyperFrames vocab exactly (parity-tested). Governs both Marlow's pacing and Mason's motion. |
| `script.json` | `script` | `working_title`, `hook`, `cta`, `total_scenes`, `est_runtime_sec`, `scenes[]` where each scene has `scene_no`, `point`, `narration` (required), `on_screen_text`, `claims[{claim_id, text, source_ref}]`, `visual_note`, `duration_est_sec`. |
| `factcheck_report.json` | `factcheck_report` | `verdict` (`pass`/`block`), `summary{verified, flagged, unverifiable}`, `claims[]`. **The fact-check gate reads this.** |
| `style_guide.json` | `style_guide` (v1.1) | palette (incl. `signature_highlight: #FFD000`), typography, motion budget, layout, fps, textures, dos/donts. |
| `storyboard.json` | `storyboard` (v1.1) | `total_scenes`, per-scene `layout`, `shots[{kind, content, asset_ref}]`, `transition`, `effects[]`, `signature_beat`. |
| `asset_manifest.json` | `asset_manifest` (v1.1) | `assets[{asset_id, scene_no, type, source, uri, license, attribution, status}]` (`status` ∈ cleared/sourced/placeholder). v1.1 adds the `"diagram"` asset type + an optional cached `plan` (a `DiagramPlan` Mason composes to SVG at render). |
| `narration.transcript.json` | `narration_transcript` | `total_duration_sec`, `segments[{scene_no, start_sec, end_sec, text}]` — the **downstream timing authority** for captions. |
| `audio_manifest.json` | `audio_manifest` (v1.1) | `total_duration_sec`, `master_uri`, `vo_uri`, `tracks[{role, uri, gain_db, ducking, license, status}]`. |
| `composition_manifest.json` | `composition_manifest` | Mason's per-scene build + auto-gate record (additive). |

**Vocabulary is closed-set** across the art/composition stages:
LAYOUTS (10: centered-statement, split-screen, full-bleed-image, lower-third, data-chart,
quote-card, map-focus, list-stack, comparison-2up, title-card), TEXTURES (5 global static:
paper, grain, halftone, vignette, scanlines), EFFECTS (now **14** per-scene: stutter-12fps, stepped-ease,
highlighter-FFD000, map-draw, chromatic-aberration, push-in, parallax, count-up, breathe, bars-grow,
drift, word-reveal, pop-in, underline-grow — the later ones widened from the local
`hyperframes-animation` library + kinetic-typography work), CHART_KINDS (3 native data-chart sub-kinds
Mason draws: bar, line, pie), TRANSITIONS (5 at boundaries: cut, match-cut, dip-to-black, push, wipe),
and **SHADER_TRANSITIONS** (4 signature WebGL transitions Mason renders only into the signature beat:
whip-pan, sdf-iris, glitch, domain-warp — §6). Iris and Mason keep CHART_KINDS / SHADER_TRANSITIONS in
lock-step (cross-engine parity tests). Unknown tokens are an error, never silently dropped.

**Agent memory model** (every agent): `chat_state.json` holds a single distilled `summary`
(+ a `pending` backlog field for crash-safety); `memory.json` (Scout/Sage) logs past `runs`/`wins`.
The full transcript lives only in RAM and is distilled into the summary on every session boundary.

---

## 8. APIs / Interfaces

The project's main interfaces are CLIs, the in-process orchestrator tools, each engine's public
functions, and two optional web frontends: a Chainlit "meeting room" and a FastAPI "Control Room"
dashboard. Only the dashboard exposes an HTTP API; it began read-mostly and has grown into a true
operating console while keeping every guarantee in the deterministic spine (see below).

**Control Room dashboard** (`./yt-atlas` → http://127.0.0.1:8848; or `cd atlas && python -m
dashboard.server`): an **additive FastAPI service** that reads live system state (registry,
`project.json`, artifacts, souls, **eval scorecards**) and serves the Control Room screens (assembly
line, projects, the two gates, coaches, settings, …) as typed JSON + static assets; the API is
browsable at `/api/docs`. Across the `control-room` Slices it became the system's operating console:
- **One front door** — `POST /api/atlas/request` (`dashboard/atlas_request.py`) is a single typed
  request router; every dashboard button and chat intent becomes a `handle_request(...)` call that
  Atlas (the dispatcher) executes. It does **not** bypass guarantees — it routes to the same methods
  the per-action endpoints already call, so behavior is identical.
- **Niche intake (#1.5)** — `dashboard/intake.py` runs the pre-project discovery step (niche → Scout
  `find_topics` → candidate cards), and a picked candidate enters the belt via the normal T1 trigger.
- **Settings (#4)** — `dashboard/settings_store.py` is a single dashboard-owned JSON of
  niches/defaults/channels; the dashboard reads it and **passes values into the pipeline as args at
  trigger time** (a pure engine never reads it globally — preserves the §3/§11 decoupling rule).
- **Autonomous render under budget** — the final-render gate can auto-approve when an estimated render
  cost is within a configurable `render_budget_sec` ceiling (default 600s); over budget it escalates a
  draft-preview card for a human. The fact-check gate's `block` remains un-approvable (§6).
- **Escalation surface** — fix-attempt snapshot history, a live Atlas-activity line, and Guide / Kill
  actions on the gate card (`/api/gate/{slug}/guide` + `/kill`).
- **Herald T3 publish package (#6, not yet firing)** — `dashboard/publish.py` assembles the exact
  read-only package a human would review before anything could go live, but `fire_enabled` is
  **always False**: real publishing (Herald) isn't built, so nothing ever fires.

So the sanctioned writes are now gate approval (delegated unchanged to
`session.AtlasSession.approve_gate` → `pipeline.produce(slug, approve=<gate>)`), production triggers
via intake, and the settings JSON — all routed so they never reorder stages, edit a contract, touch
gate logic, or write `chat_state.json`. The whole deliverable lives in `atlas/dashboard/`; the engine,
pipeline, contracts, gates, registry, session, and Chainlit UI are untouched. It turns the approved
`yt-agents-dashboard.html` prototype into a real, tested service (its own API + security +
real-gate-write + Playwright E2E suites under `atlas/dashboard/tests/`).

**Web operator UI** (`chainlit run web/app.py -w` → :8000): a browser meeting room over the same
`session.py` core — streaming chat with Atlas, the two pipeline gates as **Approve / Revise**
buttons (Approve calls `pipeline.produce(slug, approve=<gate>)` directly; Revise is a conversational
turn back to Atlas), inline artifact/media previews, a roster sidebar, and per-agent persona chat
(including Marlow's script job-gate surfaced as an approval button). Additive only — the terminal
REPL, orchestrator, pipeline, contracts, and every sibling engine are untouched.

**Atlas meeting-room commands** (`python run.py chat`):
`<any message>` (delegates/answers), `/agents` (roster + each agent's effective provider),
`/ask <agent> <question>` (deterministic direct address), `/summary`, `/new`, `/help`,
`/exit` (`/quit`).

**Orchestrator tools** (generated; what Atlas's LLM can call):
- `<agent>_<job>` per JobSpec — e.g. `scout_find_topics(niche)`, `sage_research(topic, angle)`,
  `sage_factcheck(topic)`, `scriptwriter_write_script(topic)`, `art_director_design_style(topic)`,
  `art_director_build_storyboard(topic)`, `asset_sourcer_source_assets(topic)`,
  `audio_record_narration(topic)`, `audio_mix_audio(topic)`,
  `composition_engineer_compose_scenes(topic)`, `composition_engineer_render_video(topic)`.
- `ask_<agent>(question, context)` — single-turn in-character reply.
- `produce_video(brief?, slug?, approve?, unattended?)` — run/resume the full pipeline.

**Per-agent CLIs** (each `python run.py …` inside its project) — examples:
`youtube-topic-agent`: `run.py "<niche>"`, `--deep`, `chat`, `win "<topic>"`.
`topic-researcher`: `run.py research "<topic>" [--angle …] [--handoff f.json]`, `chat`.
`audio-designer`: `run.py narrate <script|dir>`, `run.py mix <dir>`, `run.py chat`.
`composition-engineer`: `run.py compose <dir> [--no-render]`, `run.py render <dir>`, `run.py chat`.

**HyperFrames CLI** (shelled out by the audio/composition engines via `hf_audio.py`/`hf_tools.py`):
`npx hyperframes tts|transcribe|lint|validate|inspect|render …`, plus `ffmpeg`/`ffprobe` for
concat/mix/mux.

---

## 9. Configuration & Environment

**One shared root `.env`** holds all keys (each agent loads its local `.env` first, then the root).
**The default setup needs NO keys for the LLM brain** (Claude subscription) — only Scout's YouTube
job needs a key. Secrets below are masked.

| Variable | Controls | Required? |
|---|---|---|
| `YOUTUBE_API_KEY` | Scout's YouTube Data API v3 calls | Needed for Scout's job |
| `GEMINI_API_KEY` | Gemini brain (any agent set to `gemini`) | Only if switching brains |
| `DEEPSEEK_API_KEY` | DeepSeek brain | Only if switching brains |
| `SMITHSONIAN_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY` | Extra Magpie asset sources | Optional/free; missing = source silently skipped |
| `FREESOUND_API_KEY` | Extra Cadence audio source | Optional/free |
| `TAVILY_API_KEY` / `BRAVE_API_KEY` | Higher-quality web search for Sage | Optional |
| **`ANTHROPIC_API_KEY`** | **DO NOT SET** — if set, the SDK bills the metered API instead of your subscription. `llm.py` warns. | Must be unset |

**The provider switches** — each agent has its own env switch read at import (precedence is local):
`ATLAS_LLM` (Atlas), `SAGE_LLM` (Sage; **note:** Scout's `llm.py` also reads `SAGE_LLM` per the
sibling summary — inferred), `IRIS_LLM`, `MAGPIE_LLM`, `AUDIO_LLM`, `MASON_LLM`. Each defaults to
`claude` and accepts `gemini`/`deepseek`. **A delegated job runs inside the sibling's engine and reads
that sibling's OWN switch** — so "ask Scout" (Atlas's brain) and "Scout does a job" (Scout's brain)
can run on different providers. `/agents` surfaces each effective provider.

> ⚠️ **Model-ID note for an assistant:** `atlas/llm.py` currently sets `CLAUDE_MODEL`/`ORCH_MODEL`
> to `"claude-sonnet-4-6"`, while the sibling summaries mention Opus and a `CHAT_MODEL` of
> `claude-sonnet-4-6`. If asked about "the model," **read the actual `llm.py` for the agent in
> question** rather than assuming — model IDs vary per agent and the most current Claude models
> (e.g. Opus 4.8) may be preferable. Don't set `ANTHROPIC_API_KEY` to "fix" anything.

---

## 10. Build, Run & Test

**Install** (shared root venv is the intended setup):
```bash
cd YT-AGENTS
python -m venv venv && source venv/bin/activate
pip install -r youtube-topic-agent/requirements.txt
pip install -r topic-researcher/requirements.txt
pip install -r scriptwriter/requirements.txt
pip install -r art-director/requirements.txt
pip install -r asset-sourcer/requirements.txt
pip install -r audio-designer/requirements.txt
pip install -r composition-engineer/requirements.txt
pip install -r atlas/requirements.txt
```
**System prerequisites for actual rendering:** Node ≥ 22 + the HyperFrames CLI (`npx hyperframes`),
FFmpeg/FFprobe on PATH, and the Kokoro TTS Python packages (`kokoro-onnx`, `soundfile`). Optional:
`whisper.cpp` for word-level caption timing.

**Run:**
```bash
cd atlas
python run.py chat                              # the meeting room (primary interface)
python run.py "AI tools & productivity"         # one-shot Scout→decide→Sage
python run.py produce "GPT-4o vs Claude … brief" # run the full video pipeline
python run.py produce "" --resume <slug> --approve factcheck   # resume after sign-off
```

**Run the web UI** (optional second frontend; install `requirements-web.txt` first):
```bash
cd atlas
pip install -r requirements-web.txt              # chainlit 2.11.1 (terminal needs none of this)
chainlit run web/app.py -w                        # -> http://localhost:8000
```

**Run the Control Room dashboard** (optional FastAPI monitor; read-mostly — §8):
```bash
./yt-atlas                                        # one-command launcher -> http://127.0.0.1:8848
# or, manually:
cd atlas && pip install -r dashboard/requirements.txt && python -m dashboard.server
```

**Evaluate a finished project against the rubric** (the self-improvement harness — §13):
```bash
cd atlas
python -m eval.inspector projects/<slug> [--judged] [--no-track]   # -> scorecard
```

**Test** (per project; pure unit tests, no network/API):
```bash
cd atlas && python -m pytest tests/ -q          # 43 atlas test files (incl. 16 test_eval_* + coaches + roundtable + creative-layer)
cd atlas && python -m pytest dashboard/tests/ -q  # 16 Control Room files: API/security/intake/publish/E2E
# Atlas core is well over 100 tests green (incl. session/web-session/gate/eval tests); each
# specialist has its own suite.
```

---

## 11. Conventions & Patterns

- **Registry-driven extensibility:** add an agent = one `AgentEntry` + one `Adapter`; tools and
  `/agents` listing appear automatically. The orchestrator never changes.
- **Two-plane separation:** the LLM does *judgment* (topic choice, synthesis, gate conversations);
  the deterministic spine does *guarantees* (order, contract validity, gates). Never move a guarantee
  into the LLM.
- **Frozen-but-extensible contracts:** `additionalProperties: true` + a documentary `schema_version`.
  New specialists ADD optional fields under a bumped version; old readers keep working.
- **Decoupling at the boundary:** specialist engines emit plain dicts and **never import Atlas**.
  Atlas stamps `schema_version` and validates at the seam (in the adapter / pipeline).
- **Engines are pure + injectable:** each `*_engine.py` takes the LLM/network seam as an argument
  (`chat_fn`, `client`, `tts_fn`, …) so tests run offline and deterministically.
- **Persona "soul" bundle:** `SOUL.md` (identity, used by the engine), `STYLE.md` (voice) and
  `examples/` (good/bad calibration) are chat-only; `SKILL.md` is the engine's job contract/method.
- **Provider seam:** every agent isolates LLM calls behind `llm.chat(system, user)` with one env
  switch; identical signatures across providers for true drop-in swaps. No Ollama (policy).
- **Crash-safe memory:** atomic JSON writes (`chat_state.atomic_write_json`); a failed distill parks
  raw turns under `pending` (no data loss), folded in on next launch; summary-only durable state.
- **Graceful degradation:** every external call is wrapped; failures return empty/placeholder and a
  note, never a crash. Status lines are deterministic; decisions are the LLM's words.
- **Determinism in the asset/audio/composition stages:** reproducible manifests, no `Math.random`/
  `Date.now` in rendered HTML, no render-time network.

---

## 12. Current State

**Complete and working:**
- The full orchestration core (registry/adapters/loader/tools/orchestrator/session/memory).
- The deterministic pipeline with contract validation, the composition auto-gate, and both human
  gates as pause-and-resume + resume-by-gate.
- **All seven pipeline roles have real engines** (Scout, Sage, Marlow, Iris, Magpie, Cadence, Mason). The
  registry's per-entry comments document each "stub slot was filled."
- **The pipeline is now 13 stages** — Iris's three-stage creative-architecture layer
  (`treatment → narrative_intent → motion_mood_board`) was added between `research` and `script`. All
  three are advisory/optional/backward-compatible (a missing artifact leaves every downstream stage on
  its prior behavior), so the spine's order/validation/gate guarantees are unchanged. See §6/§7.
- **The diagram generator landed** (behind `MAGPIE_DIAGRAM_GEN`, off by default): Magpie plans a
  `DiagramPlan` (cached in `asset_manifest` v1.1) and Mason renders it to deterministic in-HTML SVG;
  native data-charts (bar/line/pie) draw from the scene's numbers. See §6.
- **All three off-pipeline agents are built, tested, and committed** — Vera 🔬 the Reference Analyst
  (job `reference_analyst_build_rubric`, builds a `reference_rubric` from reference videos via
  FFmpeg/OpenCV — a STANDARD, not a stage) plus the two coaches Quill 🖋️ + Flux 🎚️ (§13). The
  13-stage line is unchanged by them; they surface through the registry with no orchestrator change.
- **Multiple real `video.mp4` deliverables have shipped:** the original end-to-end run
  (`atlas/projects/gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison--…/`, all stages `done`, both gates
  `approved`, ~72s), **both pilot videos** (render last-mile resolved — text-occlusion, motion-sidecar,
  transient-gate-retry, text-forward fixes; see the `pilot-videos-shipped` memory), and the **first
  "upgraded" run on the live Opus seam** with the treatment + diagram stages active
  (`how-ai-agents`, `MAGPIE_DIAGRAM_GEN=1`).
- **The web operator UI is fully built** (Chainlit, `atlas/web/app.py`). All planned phases are
  complete: **A** streaming chat · **B** the two pipeline gates as Approve/Revise buttons with inline
  artifact previews · **C-v1** roster sidebar + per-agent persona chat (`AgentSession`/`SessionRegistry`,
  resume-on-profile-switch) · **C-v2** Marlow's script job-gate as an approval button (injectable
  approver seam, terminal behavior byte-identical) · **D** inline media (swatches/thumbnails/draft MP4).
  It's additive: orchestrator, pipeline, contracts, registry, and every sibling engine are untouched.
- **A FastAPI "Control Room" dashboard is built and tested, and has grown into the operating console**
  (`atlas/dashboard/`, launched by `./yt-atlas` → :8848; see §8). It started as a read-mostly monitor
  over live state (registry, projects, artifacts, souls, eval scorecards) and, across the `control-room`
  Slices, gained: a single typed front door (`POST /api/atlas/request`), niche intake → Scout discovery
  (#1.5), a settings store passed into the pipeline as args (#4), **autonomous render under a
  `render_budget_sec` ceiling** with over-budget escalation, an escalation surface (fix-attempt history +
  live Atlas line + Guide/Kill), and a **read-only Herald T3 publish package that fires nothing** (real
  publishing isn't built). Every write is still routed so it never reorders stages, edits a contract, or
  touches gate logic. It ships its own API/security/intake/publish/Playwright-E2E suites.

**Recently resolved (owner run):**
- **Issue #2 — "irrelevant footage" — now RESOLVED; both fix directions landed.**
  Root cause: license-first ranking shipped zero-relevance museum art, the four AI brand logos are
  un-sourceable by design (trademarked, outside the CC0/PD allowlist), and Mason ignored storyboard
  shots so logo scenes rendered nothing. The two fixes:
  - **Direction A — brand chips (Mason + Iris + Magpie).** Mason (`composition_engine.py`) now reads
    `storyboard.shots` and renders **real inline brand logos** (Lobe Icons, MIT — OpenAI/Claude/Gemini/
    DeepSeek SVG marks inlined as data, no render-time fetch) via `detect_brands()` / `scene_brand_specs()`
    / `render_brand_chips()`; a `BRAND_CHIPS` registry is canonical; Iris auto-tags `kind:'brand'` shots;
    Magpie skips asset rows for render-kinds. A shot framing a model as de-emphasized ("dimmed into the
    background") gets a `dim` chip so named winners stand out. Known gap: generic "four logos" shots that
    name no specific model get no chips until the brain re-tags them.
  - **Direction B — relevant sourcing (Magpie, `asset-sourcer/source_engine.py`).**
    `rank_candidates` is now **relevance-first** (license-rank only breaks ties — inverts the
    Van Eyck bug); relevance is a normalized fraction of query *subject* tokens; museum sources are
    dropped for non-historical queries; a `RELEVANCE_FLOOR` (0.20) ships a clean placeholder instead of
    junk and a `RELEVANCE_WEAK` (0.50) flags weak-but-present assets for the human gate.
  - In parallel, the scriptwriter side gained citation/reliability hardening (label-aware citation fix,
    qualitative-citation auto-repair, magnitude-comparative reliability rule).
  See the `issue-2-irrelevant-footage` memory for the full root-cause + A/B detail.
- **Other owner-run fixes that landed alongside Issue #2:**
  - **Model IDs normalized to full slugs:** creative agents on `claude-opus-4-8`, the others on
    `claude-sonnet-4-6` (resolves the per-agent inconsistency the §9 note flags); plus a named-model
    fallback so a creative agent degrades to a named model rather than failing.
  - **Mason render fixes:** font handling, a native data-chart render, a contrast-blocking gate, and
    caption legibility.

**Newest landings — the "creative quality" push (committed on `control-room`):**
- **The `narrative_intent` + `motion_mood_board` stages** (the creative layer grew from 1 to 3 stages;
  two new contracts) — §6/§7.
- **Signature WebGL shader transitions** (`shader_transition.py`): the first transitions the engine
  actually renders — whip-pan/sdf-iris/glitch/domain-warp, SwiftShader byte-stable, net-zero-duration
  splice, ≤2/video, Iris chooses per-beat, graceful cut fallback, `MASON_SHADER_TRANSITIONS=0` kill
  switch — §6/§7.
- **EFFECTS widened to 14** (added `pop-in`, `underline-grow`) from mining the HyperFrames catalogue.
- **The opinionated persona overhaul** (Marlow/Iris/Mason SOULs rewritten for creative friction).
- **Marlow's live creative roundtable** (`scriptwriter/roundtable.py`): an opt-in internal
  Critic→Researcher→Craftsman self-review with real `ddgs` search, writing `roundtable_log.json`;
  `MARLOW_ROUNDTABLE=0` kill switch — §6.
- **The roundtable log wired into the eval/coach loop** (`eval/analyzers/roundtable.py`) as a process
  side-channel (never gated) that supercharges the Quill/Flux coaches — §13.

**Landed on `control-room` (committed) earlier:**
- **The `treatment` stage + the diagram generator** (above), and a **widened motion vocabulary**
  (kinetic-typography `word-reveal`, plus `breathe`/`bars-grow`/`drift` from the local
  `hyperframes-animation` library; `count-up`) — EFFECTS is now 13 (§7).
- **Audio normalized to −14 LUFS** (a final `loudnorm` pass; it was shipping ~−22, too quiet).
- **Render last-mile + diagram-label legibility fixes** (text-occlusion in data-chart/comparison,
  transient-Chrome-crash retry in the auto-gate, text-forward fallback for photo-less scenes,
  contrast failures surface rather than hard-block, and the `_on_fill_ink` node-label contrast fix).
- **The eval / self-improvement system is committed and Phase-2-complete** (full detail in §13): the
  frozen `atlas/rubric/` (read-only, no write path) + the `atlas/eval/` analyzers/inspector/roll-up/
  diagnose/hardened-loop, **all four Phase-2 steps done** — band calibration, the hardened loop (holdout
  split + judged noise-floor gate + held-out verifier, with a real accept demonstrated end-to-end),
  split coaching (Quill/Flux by stage), and the bounded research/self-study seam. It is additive: the
  pipeline, contracts, and registry-of-7-specialists are untouched; the registry gained only the three
  off-pipeline agents. The remaining open item is the **visual CEO interview** to replace the
  placeholder rubric bands with chosen targets.

**Known gaps / tech debt:**
- **The `research` stage now runs Sage's REAL engine** (was a stub when this doc was first written).
  `pipeline.py` wires `sage.produce_research` as the default `research` producer (mirroring how
  `sage.produce_factcheck` replaced the factcheck stub). The offline placeholder
  (`stubs.produce_research`) is retained only as an **opt-in fallback**: set `ATLAS_RESEARCH_STUB` truthy
  to force it (dev / no-network), and that path logs loudly so a stub run is never mistaken for real
  research. So a `produce_video` run now researches the topic for real before scripting.
- **Docs lag the code in places** — `atlas/README.md`/`atlas/PLAN.md`/`CHANGELOG.md` were reconciled
  during the owner run (full fleet + real engines), but predate the `control-room` work (the `treatment`
  stage, diagram generator, committed eval system, and Control Room growth). The registry/adapters and
  `pipeline.STAGES` remain the ground truth; treat the per-project prose as historical where it conflicts.
- **Per-scene TTS is sequential** (~11s/scene overhead); a 10+ scene script can run minutes
  (narration job timeout is raised to 900s). Parallelizing per-scene TTS is a documented follow-up.
- **Web UI is complete but shares state with the terminal** — `atlas/web/app.py` (Chainlit) is a
  full second frontend, but it shares one `chat_state.json` with the terminal Atlas (last-writer-wins);
  don't run both at once. It also pulls in a dormant `opentelemetry-instrumentation-ollama` shim via
  `literalai`/`traceloop` (not actual Ollama — can't be dropped cleanly).
- Model IDs were normalized during the owner run (creative agents on `claude-opus-4-8`, the rest on
  `claude-sonnet-4-6`), but values still vary per agent — read the relevant `llm.py` rather than
  assuming (see §9 note).
- Provider-fallback chains (e.g. a transition `xfade` vs hard `cut`, `whisper.cpp` word timing) are
  best-effort and degrade silently — verify behavior when debugging render/caption issues.

---

## 13. The Self-Improvement & Evaluation System

> This is the newest layer (built, tested, and **committed on `control-room`** — Phase 2 complete). It adds a "self-improvement department" that
> learns what *good* means from reference videos and continuously tunes the fleet toward that standard —
> **without ever being able to trade away reliability.** Design docs: `rubric-decomposition.md` and
> `self-improvement-enhancement-decisions.md` at the repo root; phase reports under `docs/`.

**Two non-negotiables it is built around:**
1. **Evals are the foundation.** Nothing is "better" unless it moves a measured number against a fixed bar.
   Without measurement, "improvement" is just two LLMs nodding at each other.
2. **The improver is LESS privileged than the guarantees.** It can never edit its own success bar (the
   rubric), the contracts, the pipeline spine, the gates, or the registry. This privilege asymmetry is
   enforced *structurally* (see the write boundary below), not by convention.

### The rubric — the frozen, CEO-owned standard (`atlas/rubric/`)
- `rubric/rubric.json` (currently **v0.2.0-phase2-calibrated**) defines **6 globally-weighted quality
  dimensions** (G1 pacing 0.20 · G2 editorial 0.25 · G3 visual craft 0.20 · G4 asset relevance 0.15 ·
  G5 audio 0.15 · G6 AV coherence 0.05) **plus one hard floor F** (technical integrity — a pass/fail gate,
  never averaged). Under those sit **per-stage bands** for each measurable property (e.g. `script:hook_strength`,
  `audiomix:integrated_loudness`, `compose:motion_energy`), each with an owner, a comparator
  (`range`/`gte`/`lte`/`eq`/`eq_true`/`info`), min/max/target, and a `kind` of **objective** or **judged**.
- `rubric/__init__.py` exposes **read-only** accessors that return deeply-immutable `MappingProxyType`
  (mutation raises) — **there is no write function anywhere.** The eval code reads it; nothing writes it.
  Many bands are still flagged `placeholder: true` — the *methods/ownership/structure* are stable; the
  *numbers* are tunable and await reference-derived calibration + a CEO interview.

### The eval subsystem (`atlas/eval/`) — measure → gate → diagnose
- **Analyzers** turn a finished project's artifacts into `Measurement`s. Three are **deterministic, no LLM**:
  `analyzers/text.py` (structural JSON over script/style/storyboard/assets/narration), `analyzers/audio.py`
  (ffmpeg/ffprobe loudness/peak/ducking/SNR), `analyzers/video.py` (ffprobe + frame-diff motion/cut-rhythm/
  AV-sync). One is LLM-backed: `judged.py` — **ensembled, seeded, pairwise-vs-reference** comparison
  (default N=5, a per-vote seeded coin flip defeats order bias, variance tracked) for the two holistic
  properties `script:hook_strength` and `render:overall_polish`. Every analyzer **degrades gracefully**:
  a missing artifact yields `value=None` + an error string, never a crash.
- **`inspector.py`** orchestrates the analyzers into a **scorecard**:
  `python -m eval.inspector projects/<slug> [--judged] [--no-track]`.
- **`rollup.py`** gates each measurement against its band and rolls local properties up into the global
  dimensions + floor. `overall_polish` is a holistic **anchor**, not a weighted term — if the locals all pass
  but the anchor fails, that's a flagged **"decomposition gap"** (the rubric is missing something).
- **`diagnose.py`** does **credit assignment**: it picks **one** primary failing property to fix — only a
  soft-tier, single-owner failure (multi-owner/coordination conflicts and hard-floor fails are escalated to
  the CEO, never auto-fixed), preferring the highest-weight dimension.
- **`tracking.py`** is an **append-only JSONL** results store (`eval/runs/eval_runs.jsonl`, crash-tolerant)
  and computes a **noise floor** (run a held-out set K≥5× and measure the natural variance) so a change must
  beat the noise to count as real. **`holdout.py`** keeps a train/test split and **rejects any change that
  regresses a property that passed on the held-out set** (overfitting guard). **`validation.py`** is the
  *eval-of-the-eval*: every gated band must pass a known-good sample and fail a known-bad one.
  **`calibrate.py`** proposes reference-derived bands into `eval/rubric.proposal.json` — **never** into the
  rubric (media-measurable bands come from references; structural/editorial bands surface as
  "needs CEO interview").

### The loop (`atlas/eval/loop.py`) — propose a soft fix, prove it, accept or reject
- Flow: **inspect → diagnose → propose → re-measure → accept/reject**, bounded by caps and the noise floor.
- **The write boundary is the safety core.** `apply_soft_change()` will only write **markdown** files that are
  soft-tier (stem contains `SOUL|STYLE|SKILL|PERSONA|PLAYBOOK|PROMPT|COACH`, or live under a `soul/` dir). It
  raises `WriteBoundaryError` on any attempt to touch `rubric/`, `contracts/`, `pipeline.py`, `registry.py`,
  or `adapters/loader.py`. A `can_write_rubric()` self-check asserts the rubric is genuinely unwritable.
  So a "fix" can only mean **evolving the text an agent runs on** (persona / playbook / prompt) — never its
  code, its success bar, or the spine. (The fix gradient: **soft** = auto-applied if eval improves · **hard**
  = proposed for a human to apply · **forbidden** = never.)

### The two domain coaches (Quill 🖋️ + Flux 🎚️)
- When the loop has a diagnosed target, it **delegates the authoring of the fix to a sibling coach** (Phase-2,
  step 3). **`coach_for_stage()`** routes by stage: editorial stages (`research`, `script`, `factcheck`,
  `assets`) → **Quill** (`editorial-coach/`); production stages (`style`, `storyboard`, `narration`, `compose`,
  `audiomix`, `render`) → **Flux** (`production-coach/`). `delegate_to_coach()` calls the coach adapter's
  `propose_addendum` job. **Direction is decided by the rubric; the coach only *authors* the persuasive,
  domain-aware addendum** (markdown text only — a coach never edits project files, the rubric, or pass/fail).
  Authoring priority in `propose_fix()`: injected `coach_fn` (tests) → delegate to the owning coach →
  legacy in-loop LLM → a deterministic rule addendum (offline-safe default).
- **Vera 🔬** (`reference-analyst/`) is the upstream of this whole system: she builds the `reference_rubric`
  *standard* from the `ReferanceVideos/` set — she defines "good", she does not improve videos.

### The roundtable process side-channel (`eval/analyzers/roundtable.py`)
- A specialist's internal Critic→Researcher→Craftsman review (Marlow today; see §6) writes a
  `roundtable_log.json`. Unlike every other analyzer, this one returns a plain **diagnostics dict**, not
  gated `Measurement`s — it's a **side channel** the Inspector attaches to the scorecard. It's deliberately
  un-gated: the CEO-owned rubric measures OUTPUT, never PROCESS (there are no `process:*` bands, by design).
- Its purpose is to **supercharge the coaches**: instead of seeing only the final script, Quill/Flux can see
  *where* in the chain a weakness originated (a lenient Critic, a source-less Researcher, a Craftsman who
  ignored real findings) — each implying a different coaching fix. Graceful: a missing/garbled log yields
  `None` and the eval system runs exactly as before.

### The path / current phase
- **Phase 1** (establish the standard + the basic measurement) — **done**. **Phase 2** — **all four steps
  done**: step 1 calibration · step 2 hardened loop (a real accept demonstrated end-to-end) · step 3 split
  coaching (Quill/Flux by stage) · step 4 bounded research/self-study — a `research` flag threads through to
  the owning coach to widen what's tried, but a researched hypothesis is **adopted only when it beats the
  held-out gate** (research widens; the rubric + held-out set prune). See `atlas/tests/test_eval_research.py`.
- Still ahead: the **visual CEO interview** to replace the placeholder rubric bands with chosen targets; until
  it lands the bands remain partly placeholders.

---

## 14. Glossary

- **Showrunner / Atlas** — the manager agent the CEO talks to; orchestrates the fleet and the pipeline.
- **CEO** — the human user; the system's single principal.
- **Registry** — `registry.py`; the one declaration of who Atlas can delegate to.
- **Adapter** — a uniform wrapper (`run_job` + `ask`) around a specialist, so Atlas can use it without
  modifying it.
- **Engine** — a specialist's pure logic module (`*_engine.py`), called in-process by its adapter.
- **JOB vs PERSONA** — a JOB runs the engine and returns structured output; a PERSONA (`ask`) replies
  in-character via the LLM seam (no structured output).
- **The spine / pipeline** — `pipeline.py`; the deterministic stage machine that guarantees order,
  validation, and gates.
- **Contract** — a frozen JSON Schema in `atlas/contracts/`; every artifact is validated against one
  before the pipeline advances.
- **Artifact** — a file produced by a stage (`script.json`, `storyboard.json`, `video.mp4`, …).
- **Gate** — a mandatory checkpoint. **Fact-check gate** (after fact-check; a `block` can't be approved
  away) and **final-render gate** (before spending the render). Both pause-and-resume via `project.json`.
- **Auto-gate** — Mason's automatic per-scene check (self-scan + lint + validate + inspect) before a render.
- **Creative treatment** — Iris's pre-script `treatment` stage output (`creative_treatment.json`): rhythm,
  visual world, mood, per-beat concept. Advisory/optional; consumed by script + style + storyboard when present.
- **Narrative intent** — Iris's `narrative_intent` stage output: the machine-actionable *emotional score*
  (thesis/journey/tone + five-phase arc + per-scene emotion/intensity/pacing/texture). Bridges the treatment's
  poetry to the script + narration stages so the emotional objective stops evaporating at each handoff.
- **Motion mood board** — Iris's `motion_mood_board` stage output: the *visual architecture* (per-beat
  effect/layout/transition/texture `beat_map`) whose closed vocab mirrors Mason's HyperFrames vocab exactly
  (parity-tested); governs both Marlow's pacing and Mason's motion.
- **Shader transitions** — four signature WebGL/GLSL transitions (whip-pan/sdf-iris/glitch/domain-warp)
  Mason renders headless (SwiftShader) at the assembly seam, ≤2/video, chosen per-beat by Iris into the
  signature beat; the first transitions the engine actually renders. Kill switch `MASON_SHADER_TRANSITIONS=0`.
- **Creative roundtable** — a specialist's internal, opt-in Critic→Researcher→Craftsman self-review
  (Marlow's `roundtable.py` today) that critiques + researches + revises its own draft, logging
  `roundtable_log.json`. `MARLOW_ROUNDTABLE=0` disables it; the log feeds the coaches as a process side-channel.
- **Diagram generator / DiagramPlan** — the flagged (`MAGPIE_DIAGRAM_GEN`) plan-then-render path: Magpie
  plans a `DiagramPlan` (cached in `asset_manifest` v1.1), Mason renders it to deterministic in-HTML SVG.
- **Control Room** — the FastAPI dashboard (`atlas/dashboard/`) as the operating console: monitoring +
  intake + settings + autonomous-render-under-budget + escalation, all routed through the spine's seams.
- **Slug** — a project directory name under `atlas/projects/`; identifies one video for resume.
- **Soul / SOUL.md / STYLE.md** — an agent's persona bundle (identity / voice + calibration examples).
- **SKILL.md** — an agent's engine job contract / method (the "how it works" for the engine).
- **HyperFrames** — the HTML-as-source-of-truth video framework; its Node CLI (`npx hyperframes`)
  does TTS, lint/validate/inspect, and render. HTML + a paused GSAP timeline + `data-*` timing attrs.
- **The master-bridge** — Cadence pre-mixes everything into `master.wav` and points the narration
  track's `uri` at it, so the renderer muxes the full mix without composition-side changes.
- **#FFD000 beat** — the signature riso-yellow highlighter moment Iris reserves once per video.
- **Distillation** — collapsing a meeting transcript into a single durable summary at each session boundary.
- **Stub** — an offline, deterministic placeholder producer (`adapters/stubs.py`) that writes a
  schema-valid artifact so the data-flow runs without a real specialist.
- **Rubric** — `atlas/rubric/`; the frozen, CEO-owned quality standard (weighted dimensions + bands).
  Read-only with **no write path** — the improver can never edit its own success bar.
- **Band** — a per-property target in the rubric (owner + comparator + min/max/target + objective|judged).
- **Objective vs judged** — an objective property is measured by deterministic code (ffmpeg/structural);
  a judged property is scored by an ensembled, seeded, pairwise-vs-reference LLM vote.
- **Scorecard** — the inspector's output: every measurement gated against its band + rolled up to dimensions.
- **Decomposition gap** — locals all pass but the holistic `overall_polish` anchor fails → the rubric is
  missing a term; escalated to the CEO rather than auto-fixed.
- **Noise floor** — the natural run-to-run variance of a metric; a change must beat it to count as real.
- **Holdout** — a held-out project set used to reject changes that overfit (any held-out pass that regresses).
- **Write boundary / soft-tier** — the structural rule that the loop may only write soft-tier markdown
  (persona/playbook/prompt/`soul/`), never the rubric, contracts, spine, or registry (`WriteBoundaryError`).
- **Coach** — Quill 🖋️ (editorial) or Flux 🎚️; authors a coaching addendum for a diagnosed shortfall.
  The rubric decides the **direction**; the coach only **authors** the text. Not a pipeline stage.
- **Reference rubric / Vera** — the standard Vera 🔬 derives from reference videos; defines "good".
