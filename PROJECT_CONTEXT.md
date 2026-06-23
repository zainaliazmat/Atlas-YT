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

**Status (high level):** It works end-to-end. There is a real, completed example project in
`atlas/projects/` that ran all 10 stages and produced a real `video.mp4`. **All 7 pipeline roles
now run real engines** — Scout and Sage predate the pipeline; the other five specialists were built
and dropped into their registered slots, and the former `research`-stage stub is now wired to
Sage's real engine (the stub survives only as an opt-in offline fallback; see §12). Beyond the
terminal, a **Chainlit web operator UI is fully built** as a second frontend (§12).

The registry now holds **10 agents**: the 7 pipeline specialists plus three **additive, off-pipeline**
agents that power a new **self-improvement / evaluation system** (§13): **Vera 🔬** the Reference
Analyst (builds a `reference_rubric` from reference videos — defines the standard), and two domain
coaches — **Quill 🖋️** (editorial/content) and **Flux 🎚️** (production/craft) — that author coaching
addenda when the eval loop diagnoses a quality shortfall. None of the three is a pipeline stage; the
10-stage line is unchanged.

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
| **Tests** | `pytest` (73 test files across the repo, 36 of them under `atlas/tests/` — plus the dashboard's own suite under `atlas/dashboard/tests/`; pure unit tests, no network) |
| **Version control** | **Git** (active branch is **`main`**, which is also the upstream default). The owner-run fixes landed via PR #1; the self-improvement system (§13) is the current uncommitted in-flight work. |

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
   research → script → factcheck ★GATE → style → storyboard
        → assets ∥ narration → compose ▲auto-gate → audiomix → render ★GATE → video.mp4
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
│   │   ├── security.py        #   read-mostly guardrails; the one sanctioned write is gate approval via the seam
│   │   ├── static/            #   the Control Room front-end assets
│   │   ├── tests/             #   API + security + real-gate-write tests + Playwright E2E (e2e/)
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
│   │   └── *.schema.json      #   project, research_brief, script, factcheck_report, …
│   ├── rubric/                # ★ FROZEN, CEO-OWNED QUALITY STANDARD (read-only; NO write path) — §13
│   │   ├── __init__.py        #   deep-frozen accessors (load_rubric, bands, global_weights…); no writers
│   │   └── rubric.json        #   v0.2.0-phase2-calibrated: 6 weighted dims + 1 floor + per-stage bands
│   ├── eval/                  # ★ THE EVALUATION / SELF-IMPROVEMENT SUBSYSTEM (read-only over rubric) — §13
│   │   ├── inspector.py       #   orchestrates analyzers → scorecard (python -m eval.inspector projects/<slug>)
│   │   ├── analyzers/         #   text.py (structural JSON) · audio.py (ffmpeg) · video.py (ffprobe/frame-diff)
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
│   ├── tests/                 # 36 test files (contracts, pipeline, registry, routing, + 15 test_eval_* + coaches)
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
│   └── script_engine.py run.py chat.py llm.py SKILL.md soul/ tests/
├── art-director/              # "Iris" — script → style_guide.json + storyboard.json
│   └── art_engine.py run.py chat.py llm.py SKILL.md soul/ tests/
├── asset-sourcer/             # "Magpie" — storyboard → license-cleared asset_manifest.json
│   └── source_engine.py sources.py run.py chat.py llm.py assets/ SKILL.md soul/ tests/
├── audio-designer/            # "Cadence" — narration (TTS) + documentary audio mix
│   └── audio_engine.py audio_sources.py sfx_kit.py hf_audio.py run.py llm.py SKILL.md soul/
├── composition-engineer/      # "Mason" — artifacts → HyperFrames HTML + render → video.mp4
│   └── composition_engine.py hf_tools.py run.py chat.py llm.py SKILL.md soul/ tests/
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
1. `chat.py` → `session.AtlasSession.send()` ([atlas/session.py:250](atlas/session.py#L250)) —
   builds bounded context (durable summary + fleet snapshot + recent window) and calls the orchestrator.
2. `orchestrator.Orchestrator.run_turn_async()` ([atlas/orchestrator.py:171](atlas/orchestrator.py#L171)) —
   runs the Claude Agent SDK `query()` loop with `permission_mode="bypassPermissions"` (tools
   auto-run), streaming Atlas's text and auto-executing tools.
3. Tools are generated by `tools.build_server()` ([atlas/tools.py:222](atlas/tools.py#L222)) —
   one `<agent>_<job>` + one `ask_<agent>` per registry entry, plus the single `produce_video` tool.

**Core code path for video production:**
- `tools._make_produce_tool` → `pipeline.produce()` ([atlas/pipeline.py:182](atlas/pipeline.py#L182)) —
  the deterministic runner. Its `STAGES` list ([atlas/pipeline.py:58](atlas/pipeline.py#L58)) is the
  one fixed order; gate checkpoints are `_factcheck_gate()` and `_final_render_gate()`.

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
  [atlas/pipeline.py:341](atlas/pipeline.py#L341)).
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
| `art-director` | **Iris** 🎨 | Art Director | `art_engine.design_style(script)`; `art_engine.build_storyboard(script, style_guide)` | `script.json` → `style_guide.json` + `storyboard.json` |
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

---

## 7. Data Models & Schemas

The pipeline's "language" is a set of JSON artifacts, each pinned by a schema in
`atlas/contracts/`. All set `additionalProperties: true` and require a `schema_version` string.

| Artifact (file) | Contract | Purpose / key fields |
|---|---|---|
| `project.json` | `project` | **Master state** for one video: `project_id`, `slug`, `status`, `config.gates`, per-stage `stages{status, artifact, validated}`, `gates{factcheck, final_render}`, `artifacts`, `history`. |
| `research_brief.json` | `research_brief` | Sage's pack shape (reused): `verified_facts[]`, `myths_and_corrections[]`, `contested_or_uncertain[]`, `key_statistics`, `sources[]`, `suggested_angles`. |
| `script.json` | `script` | `working_title`, `hook`, `cta`, `total_scenes`, `est_runtime_sec`, `scenes[]` where each scene has `scene_no`, `point`, `narration` (required), `on_screen_text`, `claims[{claim_id, text, source_ref}]`, `visual_note`, `duration_est_sec`. |
| `factcheck_report.json` | `factcheck_report` | `verdict` (`pass`/`block`), `summary{verified, flagged, unverifiable}`, `claims[]`. **The fact-check gate reads this.** |
| `style_guide.json` | `style_guide` (v1.1) | palette (incl. `signature_highlight: #FFD000`), typography, motion budget, layout, fps, textures, dos/donts. |
| `storyboard.json` | `storyboard` (v1.1) | `total_scenes`, per-scene `layout`, `shots[{kind, content, asset_ref}]`, `transition`, `effects[]`, `signature_beat`. |
| `asset_manifest.json` | `asset_manifest` | `assets[{asset_id, scene_no, type, source, uri, license, attribution, status}]` (`status` ∈ cleared/sourced/placeholder). |
| `narration.transcript.json` | `narration_transcript` | `total_duration_sec`, `segments[{scene_no, start_sec, end_sec, text}]` — the **downstream timing authority** for captions. |
| `audio_manifest.json` | `audio_manifest` (v1.1) | `total_duration_sec`, `master_uri`, `vo_uri`, `tracks[{role, uri, gain_db, ducking, license, status}]`. |
| `composition_manifest.json` | `composition_manifest` | Mason's per-scene build + auto-gate record (additive). |

**Vocabulary is closed-set** across the art/composition stages (the three orthogonal axes):
LAYOUTS (10: centered-statement, split-screen, full-bleed-image, lower-third, data-chart,
quote-card, map-focus, list-stack, comparison-2up, title-card), TEXTURES (5 global static:
paper, grain, halftone, vignette, scanlines), EFFECTS (7 per-scene: stutter-12fps, stepped-ease,
highlighter-FFD000, map-draw, chromatic-aberration, push-in, parallax), TRANSITIONS (5 at
boundaries: cut, match-cut, dip-to-black, push, wipe). Unknown tokens are an error, never
silently dropped.

**Agent memory model** (every agent): `chat_state.json` holds a single distilled `summary`
(+ a `pending` backlog field for crash-safety); `memory.json` (Scout/Sage) logs past `runs`/`wins`.
The full transcript lives only in RAM and is distilled into the summary on every session boundary.

---

## 8. APIs / Interfaces

The project's main interfaces are CLIs, the in-process orchestrator tools, each engine's public
functions, and two optional web frontends: a Chainlit "meeting room" and a FastAPI "Control Room"
dashboard. Only the dashboard exposes an HTTP API, and it is **read-mostly** (one sanctioned write).

**Control Room dashboard** (`./yt-atlas` → http://127.0.0.1:8848; or `cd atlas && python -m
dashboard.server`): an **additive, read-mostly FastAPI service** that reads live system state
(registry, `project.json`, artifacts, souls, **eval scorecards**) and serves six Control Room screens
(+ a gate screen) as typed JSON + static assets; the API is browsable at `/api/docs`. The **only**
mutation is gate approval, delegated unchanged to `session.AtlasSession.approve_gate` →
`pipeline.produce(slug, approve=<gate>)`. It never reorders stages, edits a contract, touches gate
logic, or writes `chat_state.json`. The whole deliverable lives in the new `atlas/dashboard/` package;
the engine, pipeline, contracts, gates, registry, session, and Chainlit UI are all untouched. It turns
the approved `yt-agents-dashboard.html` prototype into a real, tested service (its own API + security +
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
cd atlas && python -m pytest tests/ -q          # 36 atlas test files (incl. 15 test_eval_* + coaches)
cd atlas && python -m pytest dashboard/tests/ -q  # the Control Room's own API/security/E2E suite
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
- **An 8th agent — Vera 🔬 the Reference Analyst — is built and tested.** It is a standalone
  delegable job + persona (job `reference_analyst_build_rubric`) that builds a `reference_rubric`
  from reference videos via FFmpeg/OpenCV. It is a STANDARD/job, **not** a pipeline stage — the
  10-stage line is unchanged. Adds `atlas/adapters/reference_analyst.py`,
  `atlas/contracts/reference_rubric.schema.json`, and its tests; it surfaces through the registry
  with no orchestrator change.
- **An end-to-end run actually succeeded:** `atlas/projects/gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison--…/`
  has `status: "done"`, all 10 stages `done`, both gates `approved`, and a real `video.mp4`
  (11 scenes, ~72s audio).
- **The web operator UI is fully built** (Chainlit, `atlas/web/app.py`). All planned phases are
  complete: **A** streaming chat · **B** the two pipeline gates as Approve/Revise buttons with inline
  artifact previews · **C-v1** roster sidebar + per-agent persona chat (`AgentSession`/`SessionRegistry`,
  resume-on-profile-switch) · **C-v2** Marlow's script job-gate as an approval button (injectable
  approver seam, terminal behavior byte-identical) · **D** inline media (swatches/thumbnails/draft MP4).
  It's additive: orchestrator, pipeline, contracts, registry, and every sibling engine are untouched.
- **A FastAPI "Control Room" dashboard is built and tested** (`atlas/dashboard/`, launched by `./yt-atlas`
  → :8848; see §8). It turns the approved `yt-agents-dashboard.html` prototype into a real, read-mostly
  monitoring service over live state (registry, projects, artifacts, souls, eval scorecards), with the one
  sanctioned write (gate approval) routed through the existing pipeline seam. It ships its own API/security/
  real-gate-write/Playwright-E2E suites and touches nothing in the engine, pipeline, contracts, or registry.

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
  - **Direction B — relevant sourcing (Magpie, `asset-sourcer/source_engine.py` — the current working
    diff).** `rank_candidates` is now **relevance-first** (license-rank only breaks ties — inverts the
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

**In flight — the self-improvement / evaluation system (uncommitted working diff; full detail in §13):**
- **Phase 1 (foundation) built and proven:** the frozen, CEO-owned `atlas/rubric/` (read-only, no write path)
  plus the `atlas/eval/` analyzers + inspector + roll-up + diagnose + loop. A real scorecard runs against a
  finished project; the loop is write-boundary-safe (can only touch soft-tier persona/prompt markdown).
- **Phase 2 in progress:** step 1 = band **calibration** from the `ReferanceVideos/` set (`calibrate.py` →
  `rubric.proposal.json`, never the rubric itself); step 2 = **hardened loop** (holdout split + judged
  noise-floor gate + held-out verifier) with a real accept demonstrated; step 3 = **split coaching** — the loop
  now delegates a diagnosed fix to one of two sibling coaches (**Quill 🖋️** editorial / **Flux 🎚️** production)
  by stage (`coach_for_stage` / `delegate_to_coach` / `use_coaches`); step 4 = **bounded research/self-study
  has begun** (a `research` flag widens what the coach tries, but a hypothesis is adopted only if it beats
  the held-out gate). The rubric numbers are still partly placeholders pending the visual CEO interview.
- This work is **additive and uncommitted** (new `atlas/rubric/`, `atlas/eval/`, two coach adapters, three new
  sibling projects, `docs/`, and the two root design docs). The pipeline, contracts, and registry-of-7-specialists
  are untouched; the registry gained only the three off-pipeline agents.

**Known gaps / tech debt:**
- **The `research` stage now runs Sage's REAL engine** (was a stub when this doc was first written).
  `pipeline.py` wires `sage.produce_research` as the default `research` producer (mirroring how
  `sage.produce_factcheck` replaced the factcheck stub). The offline placeholder
  (`stubs.produce_research`) is retained only as an **opt-in fallback**: set `ATLAS_RESEARCH_STUB` truthy
  to force it (dev / no-network), and that path logs loudly so a stub run is never mistaken for real
  research. So a `produce_video` run now researches the topic for real before scripting.
- **Docs now reconciled to the code:** `atlas/README.md` and `atlas/PLAN.md` were updated to the full
  8-agent fleet + 10-stage pipeline + gates + web UI (PLAN.md keeps its pre-build review as a clearly
  marked historical record), and `CHANGELOG.md` gained a `0.3.0 — Full fleet, real engines` entry. The
  registry/adapters remain the ground truth, but the prose no longer contradicts them.
- **Per-scene TTS is sequential** (~11s/scene overhead); a 10+ scene script can run minutes
  (narration job timeout is raised to 900s). Parallelizing per-scene TTS is a documented follow-up.
- **Web UI is complete but shares state with the terminal** — `atlas/web/app.py` (Chainlit) is a
  full second frontend, but it shares one `chat_state.json` with the terminal Atlas (last-writer-wins);
  don't run both at once. It also pulls in a dormant `opentelemetry-instrumentation-ollama` shim via
  `literalai`/`traceloop` (not actual Ollama — can't be dropped cleanly).
- Model IDs are inconsistent across agents' `llm.py` (see §9 note).
- Provider-fallback chains (e.g. a transition `xfade` vs hard `cut`, `whisper.cpp` word timing) are
  best-effort and degrade silently — verify behavior when debugging render/caption issues.

---

## 13. The Self-Improvement & Evaluation System

> This is the newest layer (uncommitted working diff). It adds a "self-improvement department" that
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

### The path / current phase
- **Phase 1** (establish the standard + the basic measurement) — **done**. **Phase 2**: step 1 calibration ·
  step 2 hardened loop (a real accept was demonstrated end-to-end) · step 3 split coaching — **done/in
  progress**; **step 4 bounded research/self-study has begun** — a `research` flag threads through to the
  owning coach to widen what's tried, but a researched hypothesis is **adopted only when it beats the
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
